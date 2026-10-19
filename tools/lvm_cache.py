#!/usr/bin/env python3
"""lvm_cache.py — **编译产物缓存**（`docs/265`，issue #646）。

## 它是什么

T2（`tools/lvm_t2.py`）每跑一次就把整份 IR 重新解析、重新编译；紧凑形（`tools/lvm_compact.py`，
`docs/250`）已经是那批编译好的 op 的序列化。这一层做的只是**把它放到输入旁边**，
下次跑同一份输入时直接读。

## 一条不许含糊的纪律

**旧缓存必须点名拒绝，不能"多半还是对的"就用。** 缓存头里记三样关于输入的东西
（大小 / mtime / 内容哈希），外加**它缓的到底是哪份 IR**（IR 文本的哈希 ——
源文件没变、但它 `use` 的模块变了时，这一条是唯一能发现的地方）。
任何一样对不上就 `CacheStale`，消息里带着缓存文件的路径与**是哪一样**变了。

## 形态

文件头自己说明这是哪一种形（与 `docs/250` §2.1 同一条纪律）：
`MAGIC | VERSION | FORM | 输入大小 | 输入 mtime | 输入哈希 | IR 哈希 | 载荷长度 | 紧凑形`。
载荷**就是** `lvm_compact.encode_ir()` 的产物 —— 再套一层壳，不重新发明格式。
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
from pathlib import Path

import lvm
import lvm_compact

#: 缓存文件的魔数。与紧凑形的 `LVMC` 分开：**读到缓存**与**读到紧凑形**是两件事，
#: 前者多一层"这份缓存配不配这份输入"的头。
MAGIC = b"LVMCACHE"
VERSION = 1
#: 文件头里写的形 —— 载荷是紧凑形。`lvm_compact` 自己也带形，两边必须一致。
FORM_COMPACT = lvm_compact.FORM_COMPACT

#: `MAGIC | VERSION | FORM` 之后是四个定长字段 + 两段 32 字节摘要。
_HDR = struct.Struct("<8sBBQQ32s32sQ")
HASH = hashlib.blake2b


class CacheStale(lvm.LvmError):
    """缓存**不能被用**（旧的 / 坏的 / 不是缓存的）。消息里必须点名文件与原因。"""


def default_path(entry: Path) -> Path:
    """输入旁边的那一格：`tour.lomt` -> `tour.lomt.lvmc`。

    加后缀而不是换后缀：`tour.lvmc` 会与"用户自己放的紧凑形"撞名，而这两件事
    的契约不同（紧凑形是产物，缓存是**可以被删掉的**）。
    """
    return entry.with_name(entry.name + ".lvmc")


def _digest(data: bytes) -> bytes:
    return HASH(data, digest_size=32).digest()


def key_for(entry: Path, ir_text: str) -> tuple[int, int, bytes, bytes]:
    """输入的三样 + IR 的哈希。**读一次文件**，调用方不必自己再读。"""
    st = entry.stat()
    raw = entry.read_bytes()
    return (st.st_size, st.st_mtime_ns, _digest(raw), _digest(ir_text.encode("utf-8")))


def encode(payload: bytes, key: tuple[int, int, bytes, bytes]) -> bytes:
    size, mtime, digest, ir_digest = key
    head = _HDR.pack(MAGIC, VERSION, FORM_COMPACT, size, mtime, digest, ir_digest,
                     len(payload))
    return head + payload


def decode(blob: bytes, key: tuple[int, int, bytes, bytes], path: Path) -> bytes:
    """字节 -> 紧凑形载荷；**任何一处对不上都点名拒绝**。"""
    if len(blob) < _HDR.size:
        raise CacheStale(f"缓存 {path} 被截断（{len(blob)} 字节，连文件头都放不下）")
    magic, ver, form, size, mtime, digest, ir_digest, plen = _HDR.unpack_from(blob)
    if magic != MAGIC:
        raise CacheStale(f"缓存 {path} 的头不是 {MAGIC!r}（读到 {magic!r}）")
    if ver != VERSION:
        raise CacheStale(f"缓存 {path} 的版本 {ver} 不认识（这一版只认 {VERSION}）")
    if form != FORM_COMPACT:
        raise CacheStale(f"缓存 {path} 说载荷是形 {form}，不是紧凑形（{FORM_COMPACT}）")
    if size != key[0]:
        raise CacheStale(f"缓存 {path} 过期：输入大小变了（缓存 {size}，现在 {key[0]}）")
    if mtime != key[1]:
        raise CacheStale(f"缓存 {path} 过期：输入 mtime 变了")
    if digest != key[2]:
        raise CacheStale(f"缓存 {path} 过期：输入内容变了（哈希对不上）")
    if ir_digest != key[3]:
        raise CacheStale(f"缓存 {path} 过期：IR 变了（依赖或被编译的源变了）")
    if plen != len(blob) - _HDR.size:
        raise CacheStale(
            f"缓存 {path} 的载荷长度对不上（头里 {plen}，实际 {len(blob) - _HDR.size}）")
    return blob[_HDR.size:]


def write(path: Path, payload: bytes, key: tuple[int, int, bytes, bytes]) -> None:
    """原子写：先写同目录的临时文件，再 `os.replace` —— 半截缓存不如没有。"""
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        tmp.write_bytes(encode(payload, key))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def build(ir_text: str) -> bytes:
    """IR 文本 -> 紧凑形。**这是"冷"的那一趟**（`encode_ir` 会编译整份程序）。"""
    return lvm_compact.encode_ir(ir_text)


def load_or_build(path: Path, entry: Path, ir_text: str, out=None) -> bytes:
    """缓存能用就用，不能用就**点名拒绝**并重新编译（并把新的写回去）。

    返回紧凑形载荷。写失败（只读目录等）只警告，不影响这一趟运行 ——
    缓存是加速，不是运行的前提。
    """
    err = out if out is not None else sys.stderr
    key = key_for(entry, ir_text)
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        payload = build(ir_text)
    else:
        try:
            return decode(blob, key, path)
        except CacheStale as e:
            print(f"lvm: {e}；重新编译", file=err)
            payload = build(ir_text)
    try:
        write(path, payload, key)
    except OSError as e:
        print(f"lvm: 缓存写不进 {path}（{e}）；这一趟照常跑", file=err)
    return payload
