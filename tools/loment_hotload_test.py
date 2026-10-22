#!/usr/bin/env python3
# loment_hotload_test.py — 双槽装载器参考模型（`#1151`）。
#
# 夹具不是手写的：两份 hotswap 镜像都由 `tools/lomentc.py` + `tools/lomelf.py` 从真的
# `.lomt` 源编出来，元数据是链接器自己写的；这里只问"搬到第二个槽以后每个字节该是什么"。

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import loment_hotload as hl  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


A = """module hotload

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let msg: str = "HOT\\n";
    write_str(1, msg);
    let x: u32 = add(1, 2);
    syscall4(60, x as u64, 0, 0);
}
"""

C = A.replace("add(1, 2)", "add(1, 5)")

SLOT_B = 0x10000000


def _meta(src: str):
    with __import__("tempfile").TemporaryDirectory() as t:
        p = Path(t) / "probe.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, info = lomelf.compile_ll(ir, hotswap=True)
    meta = hl.parse(info["hot"])
    return blob, meta, hl.text_of(blob, meta)


@test
def test_metadata_carries_a_relocation_table() -> None:
    _blob, meta, text = _meta(A)
    assert meta["base"] == lomelf.ELF_TEXT_VADDR
    assert meta["relocs"], "没有 reloc 行"
    for off, tgt in meta["relocs"]:
        assert struct.unpack_from("<Q", text, off)[0] == tgt, (off, tgt)
    print(f"      {len(meta['relocs'])} 条绝对地址重定位，逐条与文本里的值对得上")


@test
def test_relocation_shifts_every_absolute_value() -> None:
    _blob, meta, text = _meta(A)
    moved = hl.relocate(text, meta, SLOT_B)
    delta = SLOT_B - meta["base"]
    for off, tgt in meta["relocs"]:
        assert struct.unpack_from("<Q", moved, off)[0] == tgt + delta
    print(f"      搬到 {SLOT_B:#x}：{len(meta['relocs'])} 条绝对地址全部加上 delta")


@test
def test_new_slot_trampolines_stay_inside_the_slot() -> None:
    _blob, meta, text = _meta(A)
    moved = hl.relocate(text, meta, SLOT_B)
    targets = hl.slot_targets(moved, meta, SLOT_B)
    assert targets, "没有槽"
    for tgt in targets.values():
        assert SLOT_B <= tgt < SLOT_B + len(moved), hex(tgt)
    print(f"      {len(targets)} 条槽的 E9 都落回新槽内部")


@test
def test_flip_rewrites_the_old_slot_table() -> None:
    _ab, am, atext = _meta(A)
    _cb, cm, _ctext = _meta(C)
    old = bytearray(atext)
    new = hl.new_entries(cm, SLOT_B)
    hl.flip(old, am["base"], am["fixed"]["slots"], new)
    for slot, want in new.items():
        rel = struct.unpack_from("<i", old, slot * 16 + 1)[0]
        assert old[slot * 16] == 0xE9
        assert am["base"] + slot * 16 + 5 + rel == want, (slot, hex(want))
    print("      翻转后旧槽表的每条 jmp 都改指新镜像的实现")


@test
def test_fixed_core_mismatch_is_refused() -> None:
    _ab, am, atext = _meta(A)
    _cb, cm, ctext = _meta(C)
    bad = dict(cm)
    bad["fixed"] = dict(cm["fixed"], digest="0" * 64)
    try:
        hl.vet(am, bad, len(atext), len(ctext))
    except hl.Refused as e:
        assert e.code == "fixed-core", e.code
    else:
        raise AssertionError("固定核不同却装了上去")
    print("      固定核摘要不同 —— 拒绝")


@test
def test_slot_table_mismatch_is_refused() -> None:
    _ab, am, atext = _meta(A)
    _cb, cm, ctext = _meta(C)
    bad = dict(cm)
    bad["fixed"] = dict(cm["fixed"], slots=int(cm["fixed"]["slots"]) + 1)
    try:
        hl.vet(am, bad, len(atext), len(ctext))
    except hl.Refused as e:
        assert e.code == "table", e.code
    else:
        raise AssertionError("槽表形状不同却装了上去")
    print("      槽表形状不同 —— 拒绝")


@test
def test_function_set_mismatch_is_refused() -> None:
    _ab, am, atext = _meta(A)
    _cb, cm, ctext = _meta(C)
    bad = dict(cm)
    bad["funcs"] = {k: v for k, v in cm["funcs"].items() if k != "add"}
    try:
        hl.vet(am, bad, len(atext), len(ctext))
    except hl.Refused as e:
        assert e.code == "table", e.code
    else:
        raise AssertionError("函数集合不同却装了上去")
    print("      函数集合不同 —— 拒绝")


@test
def test_ping_pong_between_two_targets() -> None:
    _ab, am, atext = _meta(A)
    _cb, cm, _ctext = _meta(C)
    old = bytearray(atext)
    hl.flip(old, am["base"], am["fixed"]["slots"], hl.new_entries(cm, SLOT_B))
    first = dict(hl.new_entries(cm, SLOT_B))
    back = hl.new_entries(am, am["base"])
    hl.flip(old, am["base"], am["fixed"]["slots"], back)
    for slot, want in back.items():
        rel = struct.unpack_from("<i", old, slot * 16 + 1)[0]
        assert am["base"] + slot * 16 + 5 + rel == want
        assert want != first[slot]
    print("      两次翻转：槽表从 A 指到 B，再指回 A")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotload_test")


if __name__ == "__main__":
    sys.exit(main())
