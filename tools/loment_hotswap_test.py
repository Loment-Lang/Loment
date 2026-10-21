#!/usr/bin/env python3
# loment_hotswap_test.py — 热更新构建档：稳定槽表 + 入口闸（`#1150`）。
#
# 三件事分开证：
#   * **默认档一个字节都不许动** —— 拿 origin/main 的 lomelf 现编一份对拍（不是比常量）。
#   * **热更新档的接线是真的** —— 直接读 ELF 的字节：每条内部调用是 `E8` 且落到槽表里，
#     每条槽是 `E9` 且落回它自己的函数入口，每个函数的第一个调用点是 `__gate@0`。
#   * **行为没变** —— 两档编出来的 ELF 在 WSL 里跑，stdout 与退出码逐字节相同。
#
# IR 由 `tools/lomentc.py` 从真 `.lomt` 源编出来；字节从 ELF 的 LOAD 段里读，不靠反汇编器。

from __future__ import annotations

import hashlib
import struct
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


PROBE = """module hotswap_probe

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

BASE = lomelf.ELF_TEXT_VADDR


def _ir(src_text: str) -> str:
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "probe.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        return lomentc.emit_llvm(mod, ROOT, deps)


def _build(hotswap: bool):
    ir = _ir(PROBE)
    blob, info = lomelf.compile_ll(ir, hotswap=hotswap)
    return blob, info, lu.parse_hot_meta(info["hot"]), _header(info["hot"])


def _header(text: str) -> dict[str, str]:
    for line in text.splitlines():
        if line.startswith("fixed "):
            return {k: v for k, v in (x.split("=", 1) for x in line.split()[1:])}
    return {}


def _file_off(blob: bytes, va: int) -> int:
    e_phoff = struct.unpack_from("<Q", blob, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", blob, 0x36)[0]
    e_phnum = struct.unpack_from("<H", blob, 0x38)[0]
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", blob, o)[0]
        if p_type != 1:
            continue
        p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<QQQQ", blob, o + 8)
        if p_vaddr <= va < p_vaddr + p_filesz:
            return p_offset + (va - p_vaddr)
    raise AssertionError(f"VA {va:#x} 不在任何 LOAD 段里")


def _call_target(blob: bytes, va: int) -> int:
    off = _file_off(blob, va)
    assert blob[off] == 0xE8, f"{va:#x} 不是 call（字节 {blob[off]:#x}）"
    return va + 5 + struct.unpack_from("<i", blob, off + 1)[0]


def _jmp_target(blob: bytes, va: int) -> int:
    off = _file_off(blob, va)
    assert blob[off] == 0xE9, f"{va:#x} 不是 jmp（字节 {blob[off]:#x}）"
    return va + 5 + struct.unpack_from("<i", blob, off + 1)[0]


def _call_va(fn: dict, cont_off: int) -> int:
    return BASE + fn["entry"] + cont_off - 5


@test
def test_default_profile_matches_origin_main() -> None:
    r = subprocess.run(
        ["git", "-C", str(ROOT), "show", "origin/main:tools/lomelf.py"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("SKIP: 取不到 origin/main:tools/lomelf.py")
        return
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "old_lomelf.py").write_text(r.stdout, encoding="utf-8", newline="\n")
        ll = t / "probe.ll"
        ll.write_text(_ir(PROBE), encoding="utf-8", newline="\n")
        script = (
            "import sys, hashlib, pathlib\n"
            f"sys.path.insert(0, {str(t)!r})\n"
            "import old_lomelf\n"
            f"src = pathlib.Path({str(ll)!r}).read_text(encoding='utf-8')\n"
            "blob, _ = old_lomelf.compile_ll(src)\n"
            "print(hashlib.sha256(blob).hexdigest())\n"
        )
        p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    ours = hashlib.sha256(lomelf.compile_ll(_ir(PROBE))[0]).hexdigest()
    assert p.stdout.strip() == ours, (p.stdout.strip(), ours)
    print("      default 档与 origin/main 逐字节相同")


@test
def test_slots_exist_only_in_the_hotswap_profile() -> None:
    _b, _i, fd, hd = _build(False)
    assert hd == {}
    assert all("tramp" not in fn for fn in fd.values())
    _b2, _i2, fh, hh = _build(True)
    assert int(hh["stride"]) == 16 and int(hh["slots"]) == len(fh)
    assert len(hh["digest"]) == 64
    assert all("tramp" in fn for fn in fh.values())
    print("      默认档没有槽表；热更新档每函数一个槽、stride=16")


@test
def test_internal_calls_target_the_slots() -> None:
    blob, _info, funcs, _hd = _build(True)
    checked = 0
    for name, fn in funcs.items():
        for cid, cont in fn["calls"]:
            if cid.startswith("extern:") or cid.startswith("__gate@"):
                continue
            callee = cid.rsplit("@", 1)[0]
            want = BASE + funcs[callee]["tramp"] * 16
            got = _call_target(blob, _call_va(fn, cont))
            assert got == want, (name, cid, hex(got), hex(want))
            checked += 1
    assert checked >= 2, checked
    print(f"      {checked} 条内部调用都落在被调方的槽上")


@test
def test_gate_is_the_first_site_and_targets_the_gate() -> None:
    blob, _info, funcs, hd = _build(True)
    for name, fn in funcs.items():
        assert fn["calls"], name
        cid, cont = fn["calls"][0]
        assert cid == "__gate@0", (name, cid)
        assert _call_target(blob, _call_va(fn, cont)) == BASE + int(hd["gate"])
    print("      每个函数的第一个调用点都是 __gate@0，且跳到闸本身")


@test
def test_each_slot_jumps_to_its_own_function() -> None:
    blob, _info, funcs, _hd = _build(True)
    for name, fn in funcs.items():
        assert _jmp_target(blob, BASE + fn["tramp"] * 16) == BASE + fn["entry"], name
    print("      每条槽的 E9 都落回它自己的函数入口")


@test
def test_cost_is_a_count_not_a_ratio() -> None:
    _b, info_off, foff, _h = _build(False)
    _b2, info_on, fon, hd = _build(True)
    n = len(fon)
    assert int(hd["slots"]) == n
    assert sum(1 for fn in fon.values() if fn["calls"][0][0] == "__gate@0") == n
    assert info_on["text"] > info_off["text"]
    print(f"      {n} 个函数 = {n} 条入口闸调用 + {n} 条槽（{n * 16} 字节）")


@test
def test_behaviour_is_identical_under_wsl() -> None:
    import loment_elf_test as elf  # noqa: E402  (共用那套 WSL 助手)

    if not elf._wsl():
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        results = []
        for tag, hs in (("default", False), ("hotswap", True)):
            blob, _info = lomelf.compile_ll(_ir(PROBE), hotswap=hs)
            p = td / f"{tag}.elf"
            p.write_bytes(blob)
            results.append(elf._run_bin(p, tag, td))
        assert results[0] == results[1] == (3, b"HOT\n"), results
    print("      两档在 WSL 里 stdout 与退出码逐字节相同（rc=3）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotswap_test")


if __name__ == "__main__":
    sys.exit(main())
