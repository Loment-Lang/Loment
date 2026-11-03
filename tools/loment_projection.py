#!/usr/bin/env python3
# loment_projection.py — 热线上的一份 Pre-Release 只带走它自己的 fix（`#1156`）。
#
# 一次 Pre-Release 里混着 fix 与 feature；把它整包装进一个正在跑的进程，等于把功能也带
# 进去，而"只吃修复"正是这条线的前提。功能开关解决不了：代码、代码路径、能力都在，
# 开关是承诺不是证明。
#
# **热线**（每个基线一条，线性、累积）：
#
#     B0 -> B0+F1 -> B0+F1+F2 -> …
#
# **投影**：从 B0 起，按提交顺序重放"声明为 fix"的提交，取能通过三关的最长前缀：
#
#   1. 干净地应用（cherry-pick 语义）—— 依赖某个 feature 的 fix 因此应用不上；
#   2. 能编过；
#   3. 接口面摘要不动（契约、LLS 版本、能力、surface、CLI）。
#
# 过不了的 fix 被**剔除并点名**：它随完整发行走，需要重启。
#
# "声明为 fix" 有两条等价的路：合并的 PR 带 `bug` 标签，或者提交正文带 `Hotfix: yes`。
# 标签是散文、摘要是字节 —— 一个被标成 `bug` 的功能由第 3 关拦，不靠标签。

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_hotfix as hf  # noqa: E402

#: 接口面摘要的五个量（`docs/283` §3）。任何一个动了就不是热更新，是功能。
DIGEST_KEYS = ("language", "contract", "capabilities", "surface", "cli")


@dataclass
class Step:
    sha: str
    subject: str
    reason: str = ""

    def as_dict(self) -> dict:
        out = {"sha": self.sha, "subject": self.subject}
        if self.reason:
            out["reason"] = self.reason
        else:
            # 进线的每一条都要能自证"这是 fix" —— 消费者按这个字段拒绝"声称装了个功能"。
            out["kind"] = "fix"
        return out


@dataclass
class Projection:
    baseline: str
    release: str
    base_ref: str
    line: list[Step] = field(default_factory=list)
    evicted: list[Step] = field(default_factory=list)
    tree: str | None = None
    detail: str = ""

    @property
    def empty(self) -> bool:
        return not self.line

    def as_dict(self) -> dict:
        return {
            "baseline": self.baseline,
            "release": self.release,
            "line": [s.as_dict() for s in self.line],
            "evicted": [s.as_dict() for s in self.evicted],
            "tree": self.tree,
        }


def _git(tree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, check=check)


def commits(tree: Path, base_ref: str, tip_ref: str) -> list[tuple[str, str]]:
    """B0..R 的提交，**按提交顺序**（老的在前）。"""
    r = _git(tree, "log", "--reverse", "--format=%H%x09%s", f"{base_ref}..{tip_ref}")
    return [tuple(line.split("\t", 1)) for line in r.stdout.splitlines() if "\t" in line]


def is_declared_fix(tree: Path, sha: str, *, labels: dict[str, list[str]] | None = None) -> bool:
    """`Hotfix: yes` 尾注，或者合并的 PR 带 `bug` 标签（调用方给的表）。"""
    if labels and "bug" in labels.get(sha, []):
        return True
    body = _git(tree, "log", "-1", "--format=%B", sha).stdout
    for line in body.splitlines():
        key, _, val = line.partition(":")
        if key.strip().lower() == "hotfix" and val.strip().lower() in ("yes", "true", "1"):
            return True
    return False


def interface_digests(tree: Path) -> dict[str, str]:
    return {
        "language": hf.read_release(tree),
        "contract": json.dumps(hf.contract_digests(tree), sort_keys=True),
        "capabilities": json.dumps(hf.capability_manifest(tree)),
        "surface": json.dumps(hf.surface(tree), sort_keys=True),
        "cli": hf.cli_digest(tree),
    }


def _changed(tree: Path, base_ref: str) -> list[str]:
    r = _git(tree, "diff", "--name-only", base_ref, "HEAD")
    return [x for x in r.stdout.splitlines() if x]


def default_build(tree: Path, base_ref: str):
    """能编过：把这次投影实际改到的 `.lomt` 各查一遍。

    不是全树重编 —— 那要几分钟；改到的文件才是这次投影引入的风险面。
    """
    def run() -> tuple[bool, str]:
        import lomentc
        root = tree.resolve()
        for rel in _changed(tree, base_ref):
            if not rel.endswith(".lomt"):
                continue
            p = tree / rel
            if not p.is_file():
                continue
            try:
                mod = lomentc.load(p)
                deps = lomentc.resolve_deps(mod, root, p.parent, entry=p)
                errs = lomentc.check(mod, deps=deps)
            except Exception as e:                     # 解析器自己的失败也算编不过
                return False, f"{rel}: {e}"
            if errs:
                return False, f"{rel}: {errs[0]}"
        return True, ""
    return run


def project(tree: Path, base_ref: str, tip_ref: str, work: Path, *,
            release: str, labels: dict[str, list[str]] | None = None,
            build=None) -> Projection:
    """在 `work`（一棵可以随便折腾的克隆）上把热线重放一遍。"""
    import shutil
    if work.exists():
        shutil.rmtree(work)
    _git(tree, "worktree", "prune", check=False)
    # **关掉换行转换**：契约摘要算的是字节，检出时把 LF 转成 CRLF 会让"没改过的文件"
    #  тоже 变一个摘要（本机 `core.autocrlf=true`，实测过）。
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                    "clone", "--quiet", "--no-hardlinks", str(tree), str(work)],
                   check=True, capture_output=True)

    # 比的是**基线**的接口面，不是 R 的 —— R 里那些 feature 正是要被挡在外面的东西。
    _git(work, "checkout", "--quiet", "--detach", base_ref)
    base_digests = interface_digests(work)
    proj = Projection(baseline=hf.read_release(tree), release=release, base_ref=base_ref)

    for sha, subject in commits(tree, base_ref, tip_ref):
        if not is_declared_fix(tree, sha, labels=labels):
            continue                                # 没声明 = feature，不进热线
        cp = _git(work, "cherry-pick", "--no-gpg-sign", sha, check=False)
        if cp.returncode != 0:
            _git(work, "cherry-pick", "--abort", check=False)
            proj.evicted.append(Step(sha, subject, "depends-on-feature"))
            continue
        if interface_digests(work) != base_digests:
            _git(work, "reset", "--hard", "HEAD~1", check=False)
            proj.evicted.append(Step(sha, subject, "digest"))
            continue
        check = build if build is not None else default_build(work, base_ref)
        ok, detail = check()
        if not ok:
            _git(work, "reset", "--hard", "HEAD~1", check=False)
            proj.evicted.append(Step(sha, subject, "build"))
            proj.detail = detail
            continue
        proj.line.append(Step(sha, subject))

    if proj.line:
        proj.tree = _git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
    return proj


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(prog="loment_projection")
    ap.add_argument("--at", default=".")
    ap.add_argument("--base", required=True, help="B0（基线那个 tag/提交）")
    ap.add_argument("--tip", required=True, help="R（Pre-Release 的提交）")
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    tree = Path(args.at).resolve()
    with tempfile.TemporaryDirectory() as t:
        proj = project(tree, args.base, args.tip, Path(t) / "work", release=args.release)
    text = json.dumps(proj.as_dict(), indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
