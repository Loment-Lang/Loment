#!/usr/bin/env python3
# loment_hotload.py — 双槽装载器的参考模型（`#1151`）。
#
# 它**不执行**代码，只做装载器在进程里要做的那几件数据变换，好让判据能逐字节问：
#
#   1. 把一份 hotswap 镜像的文本段从 `base` 搬到 `slot`（`reloc` 表逐条加 delta）；
#   2. 校验固定核摘要、槽表形状、每条槽都落在槽里；
#   3. 把**旧**槽表里的每条 `jmp rel32` 改指到新镜像的实现。
#
# 进程里的那一半（mmap / mprotect / 原子发布 epoch / 释放旧槽）在 Loment 侧，
# 规范在 `docs/281`；这里钉的是"搬过去以后每个字节该是什么"。

from __future__ import annotations

import struct
from pathlib import Path

SLOT_STRIDE = 16


class Refused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def parse(text: str) -> dict:
    funcs: dict[str, dict] = {}
    fixed: dict = {}
    base = None
    relocs: list[tuple[int, int]] = []
    cur = None
    for line in text.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "func":
            kv = dict(x.split("=", 1) for x in w[2:])
            cur = {"frame": int(kv["frame"]), "layout": kv["layout"],
                   "entry": int(kv["entry"]), "calls": []}
            if "tramp" in kv:
                cur["tramp"] = int(kv["tramp"])
            funcs[w[1]] = cur
        elif w[0] == "call" and cur is not None:
            cur["calls"].append((w[1], int(w[2].split("=", 1)[1])))
        elif w[0] == "fixed":
            for x in w[1:]:
                k, v = x.split("=", 1)
                fixed[k] = int(v) if k != "digest" else v
        elif w[0] == "base":
            base = int(w[1])
        elif w[0] == "reloc":
            relocs.append((int(w[1]), int(w[2])))
    if base is None:
        raise Refused("base", "没有 base 行")
    return {"funcs": funcs, "fixed": fixed, "base": base, "relocs": relocs}


def _load_segment(blob: bytes, va: int) -> tuple[int, int, int]:
    e_phoff = struct.unpack_from("<Q", blob, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", blob, 0x36)[0]
    e_phnum = struct.unpack_from("<H", blob, 0x38)[0]
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", blob, o)[0] != 1:
            continue
        p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<QQQQ", blob, o + 8)
        if p_vaddr <= va < p_vaddr + p_filesz:
            return p_offset, p_vaddr, p_filesz
    raise Refused("segment", f"{va:#x} 不在任何 LOAD 段里")


def text_of(blob: bytes, meta: dict) -> bytes:
    """文本段（从 `meta['base']` 起）的字节。"""
    off, _vaddr, filesz = _load_segment(blob, meta["base"])
    return blob[off:off + filesz]


def relocate(text: bytes, meta: dict, slot: int) -> bytearray:
    """把文本段搬到 `slot`。

    **只有指向本镜像代码的绝对量要加 `delta`**。指向数据段（全局、字符串、epoch 字）的
    绝对量**必须保持原值** —— 热更新换的是代码，不是状态；新镜像看见的必须是同一份数据。
    今天没有函数指针，所以表里通常一条代码目标都没有；这条规则是给以后留的。
    """
    out = bytearray(text)
    base = meta["base"]
    lo, hi = base, base + len(out)
    for off, tgt in meta["relocs"]:
        if off + 8 > len(out):
            raise Refused("reloc", f"偏移 {off} 越界")
        if lo <= tgt < hi:
            struct.pack_into("<Q", out, off, tgt + (slot - base))
    return out


def slot_targets(text: bytes, meta: dict, base: int) -> dict[int, int]:
    """镜像自己的槽表指向哪：`{slot: 目标地址}`。"""
    out: dict[int, int] = {}
    for name, fn in meta["funcs"].items():
        if "tramp" not in fn:
            raise Refused("table", f"{name} 没有 tramp 槽")
        if fn["tramp"] * SLOT_STRIDE + 5 > len(text):
            raise Refused("table", f"{name} 的槽越界")
        if text[fn["tramp"] * SLOT_STRIDE] != 0xE9:
            raise Refused("table", f"{name} 的槽不是 E9")
        rel = struct.unpack_from("<i", text, fn["tramp"] * SLOT_STRIDE + 1)[0]
        out[fn["tramp"]] = base + fn["tramp"] * SLOT_STRIDE + 5 + rel
    return out


def vet(old: dict, new: dict, old_text_len: int, new_text_len: int) -> None:
    """装之前先拒：表形、固定核、槽的目标范围。"""
    if (old["fixed"].get("slots") != new["fixed"].get("slots")
            or old["fixed"].get("stride") != new["fixed"].get("stride")):
        raise Refused("table", "槽表形状不同")
    if old["fixed"].get("digest") != new["fixed"].get("digest"):
        raise Refused("fixed-core", "固定核摘要不同")
    if set(old["funcs"]) != set(new["funcs"]):
        raise Refused("table", "函数集合不同")
    for name, fn in new["funcs"].items():
        if "tramp" not in fn or fn["tramp"] >= new["fixed"]["slots"]:
            raise Refused("table", f"{name} 的槽号越界")
        if fn["entry"] >= new_text_len:
            raise Refused("table", f"{name} 的入口越界")
    if old["fixed"]["gate"] >= old_text_len:
        raise Refused("fixed-core", "闸的偏移越界")


def flip(old_text: bytearray, old_base: int, old_slots: int,
         new_entries: dict[int, int]) -> None:
    """把旧槽表里每条 `jmp rel32` 改指到新镜像的实现（不经过新镜像的槽）。"""
    for slot, target in new_entries.items():
        at = slot * SLOT_STRIDE
        if at + 5 > len(old_text):
            raise Refused("flip", f"槽 {slot} 越界")
        rel = target - (old_base + at + 5)
        if not -(2 ** 31) <= rel < 2 ** 31:
            raise Refused("flip", f"槽 {slot} 的 rel32 溢出")
        old_text[at] = 0xE9
        struct.pack_into("<i", old_text, at + 1, rel)


def new_entries(new_meta: dict, slot_base: int) -> dict[int, int]:
    return {fn["tramp"]: slot_base + fn["entry"]
            for fn in new_meta["funcs"].values()}
