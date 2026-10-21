#!/usr/bin/env python3
# loment_update.py — 热更新的准入判据（`#1147`）。
#
# 一个 Pre-Release 能不能被一个**正在跑的**工具链吃进去，不看 changelog，看五个等式：
#
#   1. L0 契约   —— `lom/*.lom` 的 sha256 逐条相同
#   2. 语言版本  —— LLS 的版本（`tools/loment_release.py:RELEASE`）相同
#   3. 能力清单  —— 工具链自己声明的 capability 集合相同
#   4. 基线      —— `<major>.<minor>.<patch>` 相同（今天与 2 同源，见 `standard/LLS.md` §6.4）
#   5. 固定核    —— 不可换的那部分（`loment/hotswap/fixed.txt` 列出的文件）的摘要相同
#
# 外加：签名（SHA256SUMS 的分离签名）、版本单调、不是 `-pre` 宣告版、未被重放。
#
# **为什么是等式而不是"只包含修复"**：标签是散文，摘要是字节。一个把新语法标成 `bug`
# 的提交照样过不了语言版本与契约那两条。
#
# 这一份是**参考实现**：跑在开发者机器/CI 上，可以调 `openssl` 验签。Loment 写的那一份
# （`loment/tools/update.lomt`）在没有非对称密码之前只能验摘要，签名那一步留给
# fetch/staging（`#1154`）——差异写在 `docs/278` §5。

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = 1

CONTRACT_GLOB = "lom/*.lom"
IDENTITY_GLOBS = (
    "loment/tools/*.lomt",
    "loment/selfhost/*.lomt",
    "tools/lomentc.py",
    "tools/lomelf.py",
    "tools/lomfmt.py",
    "tools/loment.py",
)
CAPABILITY_GLOBS = ("loment/tools/*.lomt", "loment/selfhost/*.lomt")
FIXED_CORE_LIST = "loment/hotswap/fixed.txt"

SIG_FILES = ("SHA256SUMS", "SHA256SUMS.asc", "loment-signing.pub.pem")

_TAG_RE = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)(?:-(?P<kind>[A-Za-z0-9.]+))?$")
_SUM_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(.+?)\s*$")
_CAP_RE = re.compile(r"^\s*capability\s+(.+?)\s*$")
_RELEASE_RE = re.compile(r'^RELEASE\s*=\s*"([^"]+)"', re.M)


class Reject(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_files(tree: Path, pattern: str) -> list[Path]:
    return sorted(p for p in tree.glob(pattern) if p.is_file())


def read_release(tree: Path) -> str:
    text = (tree / "tools" / "loment_release.py").read_text(encoding="utf-8")
    m = _RELEASE_RE.search(text)
    if not m:
        raise Reject("tree-release", f"{tree}/tools/loment_release.py 里没有 RELEASE")
    return m.group(1)


def contract_digests(tree: Path) -> dict[str, str]:
    return {
        p.relative_to(tree).as_posix(): sha256_file(p)
        for p in _rel_files(tree, CONTRACT_GLOB)
    }


def capability_manifest(tree: Path) -> list[str]:
    caps: set[str] = set()
    for pattern in CAPABILITY_GLOBS:
        for p in _rel_files(tree, pattern):
            for line in p.read_text(encoding="utf-8").splitlines():
                m = _CAP_RE.match(line)
                if m:
                    caps.add(m.group(1))
    return sorted(caps)


def fixed_core_files(tree: Path) -> list[str]:
    listing = tree / FIXED_CORE_LIST
    if not listing.is_file():
        return []
    out: list[str] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _digest_of_paths(tree: Path, rels: list[str]) -> str:
    h = hashlib.sha256()
    for rel in rels:
        p = tree / rel
        if not p.is_file():
            raise Reject("tree-missing", rel)
        h.update(rel.encode("utf-8") + b"\0")
        h.update(sha256_file(p).encode("ascii"))
    return h.hexdigest()


def fixed_core_digest(tree: Path) -> str:
    return _digest_of_paths(tree, fixed_core_files(tree))


def identity(tree: Path) -> str:
    rels: list[str] = []
    for pattern in IDENTITY_GLOBS:
        rels.extend(p.relative_to(tree).as_posix() for p in _rel_files(tree, pattern))
    return _digest_of_paths(tree, sorted(rels))


def parse_tag(tag: str) -> tuple[str, str, int]:
    m = _TAG_RE.match(tag)
    if not m:
        raise Reject("tag-shape", tag)
    kind = m.group("kind") or ""
    return m.group("base"), kind, _suffix_number(kind)


def _suffix_number(kind: str) -> int:
    m = re.search(r"\.(\d+)$", kind)
    return int(m.group(1)) if m else 0


def manifest(tree: Path, release: str) -> dict:
    base, _kind, _n = parse_tag(release)
    return {
        "schema": SCHEMA,
        "release": release,
        "prerelease": "-" in release,
        "baseline": base,
        "language": read_release(tree),
        "contract": contract_digests(tree),
        "capabilities": capability_manifest(tree),
        "fixed_core": fixed_core_digest(tree),
        "identity": identity(tree),
        "functions": [],
        "artifact": {},
    }


def read_sums(candidate: Path) -> dict[str, str]:
    path = candidate / "SHA256SUMS"
    if not path.is_file():
        raise Reject("artifact", "SHA256SUMS 不在")
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _SUM_RE.match(line)
        if m:
            sums[m.group(2)] = m.group(1)
    return sums


def verify_signature(candidate: Path) -> None:
    missing = [n for n in SIG_FILES if not (candidate / n).is_file()]
    if missing:
        raise Reject("signature-missing", ", ".join(missing))
    try:
        r = subprocess.run(
            ["openssl", "dgst", "-sha256",
             "-verify", str(candidate / "loment-signing.pub.pem"),
             "-signature", str(candidate / "SHA256SUMS.asc"),
             str(candidate / "SHA256SUMS")],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise Reject("signature-unverifiable", "找不到 openssl —— 拒绝，不猜")
    if r.returncode != 0:
        raise Reject("signature", (r.stdout + r.stderr).strip()[:200])


def _read_state(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set()
    return set(data.get("accepted", []))


def _write_state(path: Path, accepted: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"accepted": sorted(accepted)}, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )


def check(candidate: Path, tree: Path, *, state: Path | None = None,
          current: str | None = None, allow_rollback: bool = False) -> dict:
    """返回候选的清单；任何一条不成立抛 `Reject`。"""
    man_path = candidate / "hotfix.json"
    if not man_path.is_file():
        raise Reject("manifest-missing", "hotfix.json")
    try:
        cand = json.loads(man_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise Reject("manifest-json", str(e)[:160])

    if cand.get("schema") != SCHEMA:
        raise Reject("schema", f"想要 {SCHEMA}，得到 {cand.get('schema')!r}")

    release = cand.get("release", "")
    base, kind, n = parse_tag(release)
    if kind == "pre":
        raise Reject("announcement-only", f"{release} 是宣告版，没有产物")
    if not cand.get("prerelease") or "-" not in release:
        raise Reject("not-prerelease", release)

    verify_signature(candidate)

    artifact = cand.get("artifact") or {}
    name = artifact.get("name", "")
    sums = read_sums(candidate)
    if not name or name not in sums:
        raise Reject("artifact", f"SHA256SUMS 里没有 {name!r}")
    if sums[name] != artifact.get("sha256"):
        raise Reject("artifact-digest", "清单与 SHA256SUMS 不一致")
    payload = candidate / name
    if not payload.is_file():
        raise Reject("artifact", f"{name} 不在")
    if sha256_file(payload) != sums[name]:
        raise Reject("artifact-digest", f"{name} 与 SHA256SUMS 不一致")

    running = {
        "baseline": read_release(tree),
        "language": read_release(tree),
        "contract": contract_digests(tree),
        "capabilities": capability_manifest(tree),
        "fixed_core": fixed_core_digest(tree),
    }

    if cand.get("language") != running["language"]:
        raise Reject("language", f"{cand.get('language')!r} != {running['language']!r}")
    if cand.get("baseline") != running["baseline"]:
        raise Reject("baseline", f"{cand.get('baseline')!r} != {running['baseline']!r}")
    if cand.get("contract") != running["contract"]:
        raise Reject("contract", _diff_keys(cand.get("contract"), running["contract"]))
    if sorted(cand.get("capabilities") or []) != running["capabilities"]:
        raise Reject("capabilities", "能力清单不同")
    if cand.get("fixed_core") != running["fixed_core"]:
        raise Reject("fixed-core", "不可换的那部分变了")

    accepted = _read_state(state)
    if release in accepted:
        raise Reject("replay", f"{release} 已经装过")

    if current:
        cur_base, _cur_kind, cur_n = parse_tag(current)
        if cur_base == base and n <= cur_n and not allow_rollback:
            raise Reject("version", f"{release} 不比 {current} 新")

    if state is not None:
        accepted.add(release)
        _write_state(state, accepted)
    return cand


def parse_hot_meta(text: str) -> dict[str, dict]:
    """`.lomhot`（`#1149`）-> {函数名: {frame, layout, calls}}。"""
    funcs: dict[str, dict] = {}
    cur: dict | None = None
    for line in text.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "func":
            if len(w) < 4:
                raise Reject("hot-meta", line)
            kv = dict(x.split("=", 1) for x in w[2:])
            cur = {"frame": int(kv["frame"]), "layout": kv["layout"], "calls": []}
            funcs[w[1]] = cur
        elif w[0] == "call" and cur is not None:
            cur["calls"].append((w[1], int(w[2].split("=", 1)[1])))
    return funcs


def remap_plan(old: dict[str, dict], new: dict[str, dict]) -> dict[str, dict]:
    """哪些函数能在安全点被改指到新镜像（`#1152` 用它）。

    两个条件缺一不可：帧布局哈希相同（槽位读得到）、调用点 id 序列相同
    （返回地址能一一对上）。任一不同 -> `remap: false`，只能等它没有活跃帧时再换。
    """
    plan: dict[str, dict] = {}
    for name, o in old.items():
        n = new.get(name)
        if n is None:
            plan[name] = {"remap": False, "why": "missing"}
            continue
        same_calls = [c for c, _ in o["calls"]] == [c for c, _ in n["calls"]]
        same_layout = o["layout"] == n["layout"]
        plan[name] = {
            "remap": same_calls and same_layout,
            "why": "" if same_calls and same_layout else ("calls" if not same_calls else "layout"),
        }
    return plan


def _diff_keys(a: dict | None, b: dict) -> str:
    a = a or {}
    added = sorted(set(a) - set(b))
    gone = sorted(set(b) - set(a))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    parts = []
    if added:
        parts.append("多 " + ", ".join(added))
    if gone:
        parts.append("少 " + ", ".join(gone))
    if changed:
        parts.append("改了 " + ", ".join(changed))
    return "; ".join(parts) or "摘要不同"


def cmd_manifest(args: argparse.Namespace) -> int:
    tree = Path(args.at).resolve()
    try:
        man = manifest(tree, args.release)
    except Reject as e:
        print(f"REJECT {e}", file=sys.stderr)
        return 1
    text = json.dumps(man, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    old = parse_hot_meta(Path(args.old).read_text(encoding="utf-8"))
    new = parse_hot_meta(Path(args.new).read_text(encoding="utf-8"))
    for name, row in remap_plan(old, new).items():
        print(f"remap {name} {'yes' if row['remap'] else 'no'}"
              + (f" ({row['why']})" if row["why"] else ""))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    tree = Path(args.at).resolve()
    candidate = Path(args.from_dir).resolve()
    try:
        man = check(
            candidate, tree,
            state=Path(args.state).resolve() if args.state else None,
            current=args.current,
            allow_rollback=args.allow_rollback,
        )
    except Reject as e:
        print(f"REJECT {e.code}: {e.detail}", file=sys.stderr)
        return 1
    print(f"ACCEPT {man['release']} {man.get('identity', '')[:12]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_update")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="compute the equality fields from a tree")
    m.add_argument("--at", default=".", help="tree to read (default: .)")
    m.add_argument("--release", required=True, help="the Pre-Release tag")
    m.add_argument("--out", help="write here instead of stdout")
    m.set_defaults(fn=cmd_manifest)

    c = sub.add_parser("check", help="decide whether a staged candidate may be hot-applied")
    c.add_argument("--from", dest="from_dir", required=True, help="the candidate directory")
    c.add_argument("--at", default=".", help="the running tree (default: .)")
    c.add_argument("--state", help="accepted-release ledger (enables replay protection)")
    c.add_argument("--current", help="the release the process is running now")
    c.add_argument("--allow-rollback", action="store_true",
                   help="explicit operator rollback: skip the monotonic check")
    c.set_defaults(fn=cmd_check)

    p = sub.add_parser("compare", help="which functions can be remapped between two builds")
    p.add_argument("--old", required=True, help="the running build's .lomhot")
    p.add_argument("--new", required=True, help="the candidate's .lomhot")
    p.set_defaults(fn=cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
