#!/usr/bin/env python3
# loment_hotmeta_test.py — 热更新的元数据：帧布局哈希 + 调用点表（`#1149`）。
#
# 判据的问法与 `loment_update_test` 同形：**换一处，元数据该怎么动？**
#   * 只改一个常量 -> 布局与 id 都不动（可重映射）；
#   * 加一个临时量 -> 布局哈希变（不可重映射）；
#   * 加一个调用   -> id 序列变（不可重映射）；
#   * 采不采集     -> 产物**一个字节都不许变**（加性）。
#
# IR 不是手写的：每份都由 `tools/lomentc.py` 从真的 `.lomt` 源编出来。

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import loment_update as lu  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


A = """module hotmeta

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let x: u32 = add(1, 2);
    syscall4(60, x as u64, 0, 0);
}
"""

B = """module hotmeta

fn add(a: u32, b: u32) -> u32 {
    let t: u32 = a + b;
    let u: u32 = t + 0;
    return u;
}

fn _start() {
    let x: u32 = add(1, 2);
    syscall4(60, x as u64, 0, 0);
}
"""

C = """module hotmeta

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let x: u32 = add(1, 5);
    syscall4(60, x as u64, 0, 0);
}
"""

D = """module hotmeta

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let x: u32 = add(1, 2);
    let y: u32 = add(3, 4);
    syscall4(60, (x + y) as u64, 0, 0);
}
"""


def _ir(src_text: str) -> str:
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "probe.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        return lomentc.emit_llvm(mod, ROOT, deps)


def _meta(src_text: str) -> tuple[str, dict]:
    _blob, info = lomelf.compile_ll(_ir(src_text))
    return info["hot"], lu.parse_hot_meta(info["hot"])


@test
def test_metadata_is_deterministic() -> None:
    a, _ = _meta(A)
    b, _ = _meta(A)
    assert a == b
    assert a.startswith("lomhot 1\n")
    print("      同一份源编两次，元数据逐字节相同")


@test
def test_every_function_has_a_frame_and_calls_are_listed() -> None:
    _text, funcs = _meta(A)
    assert set(funcs) >= {"add", "_start"}, funcs.keys()
    ids = [c for c, _ in funcs["_start"]["calls"]]
    assert "add@0" in ids, ids
    assert all(off > 0 for _c, off in funcs["_start"]["calls"])
    print("      每个函数都有帧与布局；`_start` 里那条调用 id 是 add@0")


@test
def test_collection_does_not_change_the_artifact() -> None:
    ir = _ir(A)
    on, info = lomelf.compile_ll(ir)
    off, _ = lomelf.compile_ll(ir, collect_hot=False)
    assert on == off, "采集元数据改变了产物字节"
    assert info["hot"].startswith("lomhot 1")
    print("      采不采集，ELF 逐字节相同（元数据是加性的）")


@test
def test_layout_changes_when_a_temporary_is_added() -> None:
    _ta, fa = _meta(A)
    _tb, fb = _meta(B)
    assert fa["add"]["layout"] != fb["add"]["layout"]
    plan = lu.remap_plan(fa, fb)
    assert plan["add"] == {"remap": False, "why": "layout"}, plan["add"]
    print("      加一个临时量 -> 布局哈希变 -> add 不可重映射")


@test
def test_one_line_change_keeps_layout_and_ids() -> None:
    _ta, fa = _meta(A)
    _tc, fc = _meta(C)
    assert fa["_start"]["layout"] == fc["_start"]["layout"]
    assert [c for c, _ in fa["_start"]["calls"]] == [c for c, _ in fc["_start"]["calls"]]
    plan = lu.remap_plan(fa, fc)
    assert plan["_start"]["remap"] and plan["add"]["remap"], plan
    print("      只改一个常量 -> 布局与 id 都不动 -> 两个函数都可重映射")


@test
def test_added_call_marks_the_function_not_remappable() -> None:
    _ta, fa = _meta(A)
    _td, fd = _meta(D)
    assert [c for c, _ in fd["_start"]["calls"]] == ["add@0", "add@1"]
    plan = lu.remap_plan(fa, fd)
    assert plan["_start"] == {"remap": False, "why": "calls"}, plan["_start"]
    print("      多一个调用 -> id 序列变 -> _start 不可重映射")


@test
def test_cli_writes_the_sidecar() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        ll = t / "probe.ll"
        ll.write_text(_ir(A), encoding="utf-8", newline="\n")
        meta = t / "probe.lomhot"
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lomelf.py"), str(ll),
             "-o", str(t / "probe.elf"), "--hot-meta", str(meta)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        text = meta.read_text(encoding="utf-8")
        assert text.startswith("lomhot 1\n") and "func _start " in text
        print("      命令行 --hot-meta 落出同一份元数据")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotmeta_test")


if __name__ == "__main__":
    sys.exit(main())
