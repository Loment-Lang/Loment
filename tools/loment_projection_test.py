#!/usr/bin/env python3
# loment_projection_test.py — 热线与投影（`#1156`）。
#
# 夹具是一棵**真的 git 仓库**：从本仓拷出来的树 + 几个提交。投影在它的克隆上真跑
# `git cherry-pick`，摘要用 `loment_hotfix` 现算 —— 没有任何一步是手写的结论。

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_projection as pr  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_COPY = (
    "lom/*.lom",
    "tools/loment_release.py",
    "loment/tools/*.lomt",
    "loment/selfhost/*.lomt",
    "tools/lomentc.py",
    "tools/lomelf.py",
    "tools/lomfmt.py",
    "tools/loment.py",
    "loment/hotswap/fixed.txt",
)

_GIT = ["git", "-c", "user.email=proj@example.invalid", "-c", "user.name=proj"]


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run([*_GIT, "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-400:])
    return r.stdout


def _repo(t: Path) -> Path:
    repo = t / "repo"
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = repo / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    _git(repo, "init", "--quiet")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "baseline")
    _git(repo, "tag", "B0")
    return repo


def _commit(repo: Path, msg: str, edit) -> str:
    edit(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


def _append(rel: str, text: str):
    def edit(repo: Path) -> None:
        p = repo / rel
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return edit


def _patch_tool_impl(repo: Path) -> None:
    _append("tools/loment.py", "\n# hotfix: one implementation-side change\n")(repo)


def _add_keyword(word: str):
    def edit(repo: Path) -> None:
        _append("loment/tools/lomcli.lomt",
                f'\nfn _probe_{word}(s: ptr, n: u32) -> bool {{\n'
                f'    return eq_cstr(s, n, "{word}");\n}}\n')(repo)
    return edit


def _project(repo: Path, work_parent: Path, release="v0.1.5-hot.1", **kw):
    return pr.project(repo, "B0", "HEAD", work_parent / "work", release=release, **kw)


@test
def test_only_declared_fixes_enter_the_line() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        fix = _commit(repo, "fix: one implementation change\n\nHotfix: yes",
                      _patch_tool_impl)
        _commit(repo, "feature: a new keyword", _add_keyword("hotfeature"))
        proj = _project(repo, t)
        assert [s.sha for s in proj.line] == [fix], proj.as_dict()
        assert proj.evicted == []
    print("      一个 fix 一个 feature：线里只有 fix，feature 压根不进投影")


@test
def test_a_fix_that_depends_on_a_feature_is_evicted() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        _commit(repo, "feature: a new keyword", _add_keyword("hotfeature"))
        dep = _commit(repo, "fix: tweak that line\n\nHotfix: yes",
                      _add_keyword("hotfeature2"))
        # 同一个文件、紧挨着 feature 加的那一段 —— 不带上 feature 就应用不上
        def edit(repo_path: Path) -> None:
            p = repo_path / "loment" / "tools" / "lomcli.lomt"
            text = p.read_text(encoding="utf-8")
            p.write_text(text.replace("hotfeature2", "hotfeature3"), encoding="utf-8",
                         newline="\n")
        dep2 = _commit(repo, "fix: depends on the feature\n\nHotfix: yes", edit)
        proj = _project(repo, t)
        reasons = {s.sha: s.reason for s in proj.evicted}
        assert reasons.get(dep2) in ("depends-on-feature", "digest"), reasons
        assert dep not in [s.sha for s in proj.line] or reasons.get(dep2)
        assert proj.line == [] or all(s.sha != dep2 for s in proj.line)
    print("      依赖 feature 的 fix 被剔除并点名")


@test
def test_a_mislabeled_feature_is_refused_by_the_digest() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        bad = _commit(repo, "fix: adds a keyword (mislabeled)\n\nHotfix: yes",
                      _add_keyword("hotnotafix"))
        proj = _project(repo, t)
        assert [s.sha for s in proj.evicted] == [bad], proj.as_dict()
        assert proj.evicted[0].reason == "digest"
        assert proj.line == []
    print("      标成 fix 的新关键词由摘要拦下（digest），不靠标签")


@test
def test_two_fixes_apply_in_order() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)

        def edit1(repo_path: Path) -> None:
            _append("tools/loment.py", "\n# first fix\n")(repo_path)

        def edit2(repo_path: Path) -> None:
            p = repo_path / "tools" / "loment.py"
            text = p.read_text(encoding="utf-8")
            p.write_text(text.replace("# first fix", "# first fix, then the second"),
                         encoding="utf-8", newline="\n")

        a = _commit(repo, "fix: first\n\nHotfix: yes", edit1)
        b = _commit(repo, "fix: second, on top of the first\n\nHotfix: yes", edit2)
        proj = _project(repo, t)
        assert [s.sha for s in proj.line] == [a, b], proj.as_dict()
        assert proj.evicted == []
    print("      两个有依赖关系的 fix 按顺序都进了线")


@test
def test_replaying_the_line_is_byte_identical() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        _commit(repo, "fix: one\n\nHotfix: yes", _patch_tool_impl)
        one = _project(repo, t / "a")
        two = _project(repo, t / "b")
        assert one.tree and one.tree == two.tree, (one.tree, two.tree)
    print("      同一条线重放两次：树哈希相同（产物可复现）")


@test
def test_no_fix_commits_means_no_hot_artifact() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        _commit(repo, "feature: only features here", _add_keyword("hotonlyfeature"))
        proj = _project(repo, t)
        assert proj.line == [] and proj.tree is None and proj.empty
    print("      没有 fix 提交 → 没有热更新件（一个 Pre-Release 至多一次）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_projection_test")


if __name__ == "__main__":
    sys.exit(main())
