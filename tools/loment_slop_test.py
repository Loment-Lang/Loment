#!/usr/bin/env python3
"""`tools/loment_slop.py` 的判据（`docs/318`）。

每条代理判据都有**正例**（必须命中）与**反例**（不许误报），因为一个只会喊的
检查器与一个不跑的检查器一样没用。夹具是临时 git 仓：判据量的就是"变更"。
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_slop as slop  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       shell=False)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def _repo(td: str, files: dict[str, str]) -> Path:
    root = Path(td)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _write(root: Path, name: str, text: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _kinds(report: dict) -> list[str]:
    return [f["kind"] for f in report["findings"]]


@test
def test_trivial_assert_and_comment_restate():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"tools/base.py": "X = 1\n"})
        _write(root, "tools/x_test.py",
               "def test_x():\n"
               "    x = 1\n"
               "    # x = x + 1\n"
               "    x = x + 1\n"
               "    assert x == 2\n"
               "    assert True\n")
        rep = slop.check(root, "HEAD", worktree=True)
        kinds = _kinds(rep)
        assert "tests.trivial" in kinds, rep
        assert "comments.restate" in kinds, rep
        # `assert x == 2` 是**真断言**，不许被打成恒真。
        assert not [f for f in rep["findings"] if f["kind"] == "tests.trivial"
                    and f["line"] == 5], rep


@test
def test_duplicate_block_flags_copy_paste():
    block = ("def helper(a: int) -> int:\n"
             "    first = a + 1\n"
             "    second = first * 2\n"
             "    third = second - 3\n"
             "    return third + 4\n")
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/a.py": block})
        _write(root, "pkg/b.py", block)
        rep = slop.check(root, "HEAD", worktree=True)
        assert "duplicates.block" in _kinds(rep), rep


@test
def test_import_headers_are_not_copy_paste():
    """每个文件都一样的 import 头不是 slop —— 检查器自己的第一份报告教出来的。"""
    header = """import json
import os
import re
import subprocess
import sys
from pathlib import Path
"""
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/a.py": header + "X = 1\n"})
        _write(root, "pkg/b.py", header + "Y = 2\n")
        rep = slop.check(root, "HEAD", worktree=True)
        assert "duplicates.block" not in _kinds(rep), rep


@test
def test_unused_public_fn_is_flagged_but_a_used_one_is_not():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/keep.txt": "x\n"})
        _write(root, "loment/lsp/x.lomt",
               "pub fn orphan_helper_xyz() -> u32 {\n    return 1;\n}\n")
        _write(root, "loment/lsp/y.lomt",
               "pub fn used_helper_xyz() -> u32 {\n    return used_helper_xyz();\n}\n")
        rep = slop.check(root, "HEAD", worktree=True)
        hits = [f for f in rep["findings"] if f["kind"] == "api.unused"]
        assert any("orphan_helper_xyz" in f["message"] for f in hits), rep
        assert not any("used_helper_xyz" in f["message"] for f in hits), hits


@test
def test_scope_and_area():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/base.py": "x = 1\n"})
        _write(root, "pkg/big.py", "".join(f"line_{i} = {i}\n" for i in range(500)))
        rep = slop.check(root, "HEAD", worktree=True, areas=["tools"])
        kinds = _kinds(rep)
        assert "scope.size" in kinds, rep
        assert "scope.outside_area" in kinds, rep


@test
def test_tests_missing_for_a_new_source_file():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/base.py": "x = 1\n"})
        _write(root, "tools/loment_new.py", "VALUE = 1\n")
        rep = slop.check(root, "HEAD", worktree=True)
        assert "tests.missing" in _kinds(rep), rep


@test
def test_report_is_deterministic():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/base.py": "x = 1\n"})
        _write(root, "tools/x_test.py", "def test_x():\n    assert True\n")
        a = json.dumps(slop.check(root, "HEAD", worktree=True), sort_keys=True)
        b = json.dumps(slop.check(root, "HEAD", worktree=True), sort_keys=True)
        assert a == b


@test
def test_ai_hints_never_change_findings_or_exit_code():
    """提示层只加 `HINT`：findings、strict 退出码与不带模型时逐字相同。"""
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"pkg/base.py": "x = 1\n"})
        _write(root, "tools/x_test.py", "def test_x():\n    assert True\n")
        fake = Path(td) / "fake_hints.py"
        fake.write_text(
            "import json, sys\n"
            "p = json.load(sys.stdin)\n"
            "fid = p['bundle']['findings'][0]['id']\n"
            "print(json.dumps({'format': 'loment-slop-hints', 'version': 1,\n"
            "  'model': 'fake', 'hints': [{'finding': fid, 'verdict': 'agree',\n"
            "  'note': 'looks like slop'}], 'extra': ['check the tests']}))\n",
            encoding="utf-8")
        cmd = f"{sys.executable} {fake}"

        def run(*extra):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = slop.main(["check", "--root", str(root), "--base", "HEAD",
                                "--worktree", "--json", *extra])
            return rc, json.loads(buf.getvalue())

        rc0, plain = run()
        rc1, with_ai = run("--ask-cmd", cmd, "--strict")
        assert [f["id"] for f in plain["findings"]] == \
               [f["id"] for f in with_ai["findings"]], (plain, with_ai)
        assert rc0 == 0 and rc1 == 1, (rc0, rc1)   # strict 只看机械发现
        assert "hints" not in plain and with_ai["hints"]["hints"], with_ai
        assert with_ai["hints"]["extra"] == ["check the tests"], with_ai

        # 提示层坏了（不是 JSON）也不许吞掉报告。
        bad = Path(td) / "bad.py"
        bad.write_text("print('not json')\n", encoding="utf-8")
        rc2, broken = run("--ask-cmd", f"{sys.executable} {bad}")
        assert rc2 == 0 and broken["findings"] == plain["findings"], broken


@test
def test_clean_change_has_no_medium_findings():
    with tempfile.TemporaryDirectory() as td:
        root = _repo(td, {"tools/base.py": "def f(a: int) -> int:\n    return a\n"})
        _write(root, "tools/thing.py",
               "def double(a: int) -> int:\n    return a * 2\n")
        _write(root, "tools/thing_test.py",
               "from thing import double\n\n"
               "def test_double():\n    assert double(3) == 6\n")
        rep = slop.check(root, "HEAD", worktree=True)
        med = [f for f in rep["findings"] if f["severity"] in ("medium", "high")]
        assert med == [], rep


def main() -> int:
    return loment_suite.run([fn for _name, fn in TESTS], "loment_slop_test")


if __name__ == "__main__":
    sys.exit(main())
