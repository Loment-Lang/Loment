#!/usr/bin/env python3
# loment_hotload_exec.py — 在**真进程**里跑一遍双槽装载（`#1151`）。
#
# 用法: python3 loment_hotload_exec.py A.elf A.lomhot Q.elf Q.lomhot FUNC
#
# 它做的事就是装载器那四步，只是用 Python 的 ctypes 直接调 libc：
#   1. 把 A 的文本段与数据段映射到它链接时的地址（数据段是**共享状态**）；
#   2. 把 Q 的文本段映射到第二个槽、按规则重定位、mprotect 成 RX；
#   3. 把 A 槽表里 FUNC 的那条 jmp 改指 Q 的实现；
#   4. 通过一条按 Loment 调用约定写的小桩调两次，打印 BEFORE / AFTER。
#
# 输出（一行一条，供判据解析）:
#   BEFORE <n>
#   AFTER <n>
#   EPOCH <addr>
#   SLOT <addr>
#
# 只支持 Linux（这是我们产物的目标 ABI）。

from __future__ import annotations

import ctypes
import struct
import sys

PROT_READ, PROT_WRITE, PROT_EXEC = 1, 2, 4
MAP_PRIVATE, MAP_ANON, MAP_FIXED = 2, 0x20, 0x10
SLOT_B = 0x10000000
STUB_AT = 0x20000000
SLOT_STRIDE = 16

libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mprotect.restype = ctypes.c_int
libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


def die(msg: str) -> None:
    print(f"ERROR {msg}", file=sys.stderr)
    raise SystemExit(2)


def loads(blob: bytes) -> list[tuple[int, int, int, int]]:
    e_phoff = struct.unpack_from("<Q", blob, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", blob, 0x36)[0]
    e_phnum = struct.unpack_from("<H", blob, 0x38)[0]
    out = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", blob, o)[0] != 1:
            continue
        p_offset, p_vaddr, _pa, p_filesz, p_memsz = struct.unpack_from("<QQQQQ", blob, o + 8)
        out.append((p_offset, p_vaddr, p_filesz, p_memsz))
    return out


def map_at(addr: int, size: int, prot: int) -> int:
    p = libc.mmap(ctypes.c_void_p(addr), size, prot,
                  MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0)
    if p in (None, 0xFFFFFFFFFFFFFFFF):
        die(f"mmap {addr:#x} failed (errno {ctypes.get_errno()})")
    return int(p)


def mprotect(addr: int, size: int, prot: int) -> None:
    if libc.mprotect(ctypes.c_void_p(addr), size, prot) != 0:
        die(f"mprotect {addr:#x} failed (errno {ctypes.get_errno()})")


def page_down(v: int) -> int:
    return v & ~0xFFF


def page_up(v: int) -> int:
    return (v + 0xFFF) & ~0xFFF


def parse_meta(path: str) -> dict:
    funcs = {}
    fixed = {}
    relocs = []
    base = None
    for line in open(path, encoding="utf-8"):
        w = line.split()
        if not w:
            continue
        if w[0] == "func":
            kv = dict(x.split("=", 1) for x in w[2:])
            funcs[w[1]] = {"entry": int(kv["entry"]),
                           "tramp": int(kv.get("tramp", "-1"))}
        elif w[0] == "fixed":
            for x in w[1:]:
                k, v = x.split("=", 1)
                fixed[k] = int(v) if k != "digest" else v
        elif w[0] == "base":
            base = int(w[1])
        elif w[0] == "reloc":
            relocs.append((int(w[1]), int(w[2])))
    if base is None:
        die("metadata has no base")
    return {"funcs": funcs, "fixed": fixed, "base": base, "relocs": relocs}


def main() -> int:
    if not sys.platform.startswith("linux"):
        die("this probe only runs on Linux (the artifact ABI)")
    a_elf, a_meta_p, q_elf, q_meta_p, fn = sys.argv[1:6]
    a = open(a_elf, "rb").read()
    q = open(q_elf, "rb").read()
    am = parse_meta(a_meta_p)
    qm = parse_meta(q_meta_p)
    if sets_differ(am, qm):
        die("function sets differ")
    if am["fixed"]["digest"] != qm["fixed"]["digest"]:
        die("fixed-core digest differs")

    aseg = loads(a)
    qseg = loads(q)
    text_seg = next(s for s in aseg if s[1] == am["base"])
    data_seg = next(s for s in aseg if s[1] != am["base"])
    # A：文本 RWX（要改槽表），数据 RW（共享状态）
    map_at(page_down(text_seg[1]), page_up(text_seg[3]), PROT_READ | PROT_WRITE | PROT_EXEC)
    ctypes.memmove(text_seg[1], a[text_seg[0]:text_seg[0] + text_seg[2]], text_seg[2])
    map_at(page_down(data_seg[1]), page_up(data_seg[3]), PROT_READ | PROT_WRITE)
    ctypes.memmove(data_seg[1], a[data_seg[0]:data_seg[0] + data_seg[2]], data_seg[2])

    # Q：文本搬到第二个槽，只重定位**指向文本自身**的绝对量
    qtext = next(s for s in qseg if s[1] == qm["base"])
    map_at(page_down(SLOT_B), page_up(qtext[3]), PROT_READ | PROT_WRITE)
    qbuf = bytearray(q[qtext[0]:qtext[0] + qtext[2]])
    lo, hi = qm["base"], qm["base"] + len(qbuf)
    for off, tgt in qm["relocs"]:
        if lo <= tgt < hi:
            struct.pack_into("<Q", qbuf, off, tgt + (SLOT_B - qm["base"]))
    ctypes.memmove(SLOT_B, bytes(qbuf), len(qbuf))
    mprotect(page_down(SLOT_B), page_up(qtext[3]), PROT_READ | PROT_EXEC)

    if fn not in am["funcs"]:
        die(f"no function {fn}")
    slot = am["funcs"][fn]["tramp"]
    slot_va = am["base"] + slot * SLOT_STRIDE

    # 入口闸里那条 movabs 给出 epoch 的地址（共享数据段里）
    gate = am["base"] + am["fixed"]["gate"]
    gate_bytes = ctypes.string_at(gate, 16)
    if gate_bytes[4:6] != b"\x48\xb8":
        die(f"gate does not start with movabs: {gate_bytes.hex()}")
    epoch = struct.unpack_from("<Q", gate_bytes, 6)[0]

    stub = bytearray()
    stub += bytes([0x6A, 0x02])                       # push 2
    stub += bytes([0x6A, 0x01])                       # push 1
    stub += bytes([0xE8]) + struct.pack("<i", slot_va - (STUB_AT + 9))
    stub += bytes([0x48, 0x83, 0xC4, 0x10])           # add rsp, 16
    stub += bytes([0xC3])                             # ret
    map_at(STUB_AT, 4096, PROT_READ | PROT_WRITE | PROT_EXEC)
    ctypes.memmove(STUB_AT, bytes(stub), len(stub))
    call = ctypes.CFUNCTYPE(ctypes.c_uint64)(STUB_AT)

    before = call() & 0xFFFFFFFF
    # 翻转：A 的槽直接指到 Q 的实现
    q_entry = SLOT_B + qm["funcs"][fn]["entry"]
    rel = q_entry - (slot_va + 5)
    if not -(2 ** 31) <= rel < 2 ** 31:
        die("rel32 overflow")
    mprotect(page_down(slot_va), 4096, PROT_READ | PROT_WRITE)
    ctypes.memmove(slot_va, b"\xE9" + struct.pack("<i", rel), 5)
    mprotect(page_down(slot_va), 4096, PROT_READ | PROT_EXEC)
    after = call() & 0xFFFFFFFF

    print(f"BEFORE {before}")
    print(f"AFTER {after}")
    print(f"EPOCH {epoch:#x}")
    print(f"SLOT {slot_va:#x}")
    return 0


def sets_differ(a: dict, q: dict) -> bool:
    return set(a["funcs"]) != set(q["funcs"])


if __name__ == "__main__":
    sys.exit(main())
