#!/usr/bin/env python3
"""lomelf.py — Loment 原生 ELF 后端（参考实现）

**吃掉 clang 的活**：把 `tools/lomentc.py` 发射的 LLVM IR 子集直接编成 x86-64 静态 ELF，
中间不再经过任何 C 编译器。这是 docs/167"彻底脱离 clang"的第一格。

    python tools/lomelf.py in.ll -o out
    python tools/lomelf.py in.ll --check        # 只解析与降级，不落盘

判据（`tools/loment_elf_test.py`）：同一份 `.ll`，本工具产出的 ELF 与
`clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fno-pie -fuse-ld=lld`
产出的 ELF **行为逐值一致**（stdout 与退出码相同）。

v0 的取舍（与 clang 路径不同的地方，逐条记账，别当成"等价"）：

* **调用约定是我们自己的**：实参一律走**栈**（每标量 8 字节，聚合按 8 字节向上取整），
  标量返回值放 `rax`。**不保证 System V / C ABI** —— 原生产物 v0 只保证自身自洽，不给 C 调。
* **聚合返回值 v0 不支持**（明确报错，不静默错编）。
* **栈机**：每个 SSA 值一个栈槽，表达式经 `rax`/`rcx` 求值，不做寄存器分配。正确优先。
* **只吃文本 IR**：不依赖编译器内部结构，任何产出同一子集 `.ll` 的东西都能喂进来。

子集（v0）：标量 i1/i8/i16/i32/i64 与 ptr、`{...}` 结构体、`[N x T]` 数组；
alloca/load/store/getelementptr/extractvalue/insertvalue；算术/位运算/比较/转型；
br/switch/phi；直接 call（<= 6 实参）与间接 call（`call %reg`，`docs/348`）；
内联汇编 syscall；atomicrmw add；ret/unreachable。
**没有**：浮点、bitcast、select、闭包记录本身、聚合返回值、变参、结构体 GEP。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

#: **文本段的容量**（`docs/253`，2026-10-16）。与自举侧 `lomelf.lomt` 的 `OUT_CAP`
#: **同一个数** —— 那边就是代码缓冲的大小。数据段钉在它后面（见 `ELF_DATA_VADDR`），
#: 所以这个数一旦被文本超过，数据段就会被压住（**不报错**，产物一启动就段错误）。
#: 越过它的情形由 `compile_ll` 末尾那条判据点名拒 —— 原先没有这条，地板是**没标注的**。
TEXT_CAP = 8388608                 # 8 MiB = 0x800000
#: 热更新档（`#1150`）：槽表排在文本段最前面，每槽一条 `jmp rel32` + NOP 填到定长。
#: 槽号按**函数名排序**分配，所以同一份 IR 每一次都落在同一个地址；函数集合一变，
#: 表形就变（准入侧拿 `slots` 与 `tramp` 逐条比，见 `#1152`）。
HOT_STRIDE = 16
ELF_TEXT_VADDR = 0x400000
#: 数据段在**代码缓冲之后**：文本段可用 `0x400000..0xC00000`，正好是 `TEXT_CAP`。
#: 原先钉 `0x600000`（2 MiB 窗口），2026-10-16 抬到 4 MiB，2026-11-21 抬到 8 MiB ——
#: 自举侧编出来的 `lomenterr` / 正则驱动器那份代码已到 4,228,966 B（CI 实测）。
ELF_DATA_VADDR = ELF_TEXT_VADDR + TEXT_CAP
TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # 当前目标的活动值, PE 目标会改
SCALAR_SIZE = {"i1": 1, "i8": 1, "i16": 2, "i32": 4, "i64": 8, "ptr": 8}

RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = 0, 1, 2, 3, 4, 5, 6, 7
R8, R9, R10, R11, R12, R13, R14, R15 = 8, 9, 10, 11, 12, 13, 14, 15
CC = {"o": 0, "no": 1, "b": 2, "ae": 3, "e": 4, "ne": 5, "be": 6, "a": 7,
      "s": 8, "ns": 9, "p": 10, "np": 11, "l": 12, "ge": 13, "le": 14, "g": 15}
ICMP_CC = {"eq": ("e", 0), "ne": ("ne", 0), "ugt": ("a", 0), "uge": ("ae", 0),
           "ult": ("b", 0), "ule": ("be", 0), "sgt": ("g", 1), "sge": ("ge", 1),
           "slt": ("l", 1), "sle": ("le", 1)}


class ElfError(Exception):
    pass


class Unsupported(ElfError):
    pass


# ------------------------------------------------------------------ 类型


def parse_type(s: str, i: int = 0) -> tuple[str, int]:
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        raise ElfError("类型缺失")
    if s[i] in "{[":
        close = "}" if s[i] == "{" else "]"
        depth, j = 0, i
        while j < len(s):
            if s[j] == s[i]:
                depth += 1
            elif s[j] == close:
                depth -= 1
                if depth == 0:
                    return s[i : j + 1], j + 1
            j += 1
        raise ElfError(f"类型括号不闭合: {s[i:]!r}")
    j = i
    while j < len(s) and (s[j].isalnum() or s[j] == "_"):
        j += 1
    return s[i:j], j


def is_agg(ty: str) -> bool:
    return ty.startswith("{") or ty.startswith("[")


def _fields(ty: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in ty[1:-1]:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def array_parts(ty: str) -> tuple[int, str]:
    m = re.match(r"\[\s*(\d+)\s*x\s*(.+)\]\s*$", ty.strip(), re.S)
    if not m:
        raise ElfError(f"不是数组类型: {ty!r}")
    return int(m.group(1)), m.group(2).strip()


def size_of(ty: str) -> int:
    if ty in SCALAR_SIZE:
        return SCALAR_SIZE[ty]
    if ty.startswith("{"):
        off = 0
        for f in _fields(ty):
            sz = size_of(f)
            off = (off + sz - 1) // sz * sz + sz
        return off
    if ty.startswith("["):
        n, et = array_parts(ty)
        return n * size_of(et)
    raise ElfError(f"v0 不支持的类型: {ty!r}")


def field_offset(ty: str, idx: int) -> tuple[int, str]:
    off = 0
    for k, f in enumerate(_fields(ty)):
        sz = size_of(f)
        off = (off + sz - 1) // sz * sz
        if k == idx:
            return off, f
        off += sz
    raise ElfError(f"字段下标越界: {ty} #{idx}")


def elem_of(ty: str) -> str:
    return array_parts(ty)[1]


# ------------------------------------------------------------------ 词法小工具


_VALUE_RE = re.compile(r"^(%[-A-Za-z0-9_.]+|@[-A-Za-z0-9_.]+|-?[0-9]+|true|false|null|undef)$")


def split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_operand(s: str) -> tuple[str, str]:
    ty, i = parse_type(s)
    rest = s[i:].strip()
    if "," in rest:
        rest = rest.split(",", 1)[0].strip()
    m = _VALUE_RE.match(rest)
    if not m:
        raise ElfError(f"解析不了的值: {s!r}")
    return ty, m.group(1)


#: 行尾的**元数据附着**: `, !dbg !12`。M59 的 `--debug` 才发 (`docs/167` §7)。
_META_TAIL_RE = re.compile(r",\s*![-A-Za-z0-9_.]*(?:\s+![-A-Za-z0-9_.]+)?\s*$")


def strip_meta(s: str) -> str:
    """去掉行尾的元数据附着 (`, !dbg !12`), 反复去直到不动。

    附着**只长在指令末尾** —— `!` 在操作数位置上不出现 (`!` 开头的定义行在 `parse_ll`
    里整行跳过)。所以这条正则不会误伤; 引号数**为奇**时整行不动, 那是字符串还没合上,
    行尾的 `, !x` 是字符串的内容而不是附着 (本工具链不产生这种形状, 但便宜的护栏)。

    去掉之后 `--debug` 的 IR 与不带 `--debug` 的那一份在**指令这一层逐字节同** ——
    原生产物因此两者**字节相同** (`docs/167` §7: 元数据被忽略, 不留行表)。
    """
    if s.count('"') % 2:
        return s
    while True:
        n = _META_TAIL_RE.sub("", s)
        if n == s:
            return s
        s = n


def unbalanced(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
    return depth > 0


# ------------------------------------------------------------------ IR 解析


class Instr:
    __slots__ = ("dest", "text")

    def __init__(self, dest, text):
        self.dest, self.text = dest, text


class Block:
    def __init__(self, label):
        self.label = label
        self.instrs: list[Instr] = []


class Func:
    def __init__(self, name, ret, params, internal):
        self.name, self.ret, self.params, self.internal = name, ret, params, internal
        self.blocks: list[Block] = []


class Global:
    def __init__(self, name, ty, data):
        self.name, self.ty, self.data = name, ty, data
        self.addr = 0


def parse_ll(text: str) -> tuple[list[Global], list[Func], list[str]]:
    """文本 IR -> (全局, 函数, **外部函数名**)。

    第三个返回值是 `declare` 出来的外部符号 (docs/173 的 `extern fn`)。v0 原先**整行跳过**
    `declare` —— 外部符号连表示都没有, 于是 `call @c_add` 会在 `finalize()` 里报"未定义的
    标签"。现在记下来: 它们由 `--link` 进来的外部目标文件提供, 调用点还要按 **C ABI**
    传参 (见 `Emitter._call`)。

    行首的 `#dbg_*` (`#dbg_declare` / `#dbg_value` / `#dbg_assign` / `#dbg_label`) 是
    **M59 的调试记录**, 只有 `--debug` 才发。它们**不是指令**: 原生产物里不留行表, 元数据
    被忽略 (`docs/167` §7 那一行) —— 所以这里跟 `;` 注释和 `!` 元数据定义一样**整行跳过**。
    不跳的话它会落到 `lower()` 的兜底 `Unsupported` 上: 于是 `--debug` 的 IR **链不出来**,
    而文档说的是"被忽略"。两边都得跳 (`parse_ll` 顶层与 `parse_func` 内各一处)。
    """
    globals_, funcs, externs, lines, i = [], [], [], text.split("\n"), 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line[0] in ";!" or line.startswith("#dbg_"):
            continue
        if line.startswith("declare "):
            m = re.match(r"declare\s+(?:[\w.]+\s+)*?(@[-A-Za-z0-9_.]+)\s*\(", line)
            if m:
                externs.append(m.group(1)[1:])
            continue
        if line.startswith(("target ", "attributes ", "source_filename", "module ")):
            continue
        if line.startswith("@"):
            globals_.append(_parse_global(line))
            continue
        if line.startswith("define "):
            fn, i = _parse_func(lines, i - 1)
            funcs.append(fn)
    return globals_, funcs, externs


def _parse_global(line: str) -> Global:
    m = re.match(r"(@[-A-Za-z0-9_.]+)\s*=\s*(.*)$", line)
    if not m:
        raise ElfError(f"全局声明解析失败: {line!r}")
    name, rest = m.group(1), m.group(2)
    while True:
        m2 = re.match(
            r"^(private|internal|external|unnamed_addr|dso_local|constant|global|common)\s+(.*)$", rest)
        if not m2:
            break
        rest = m2.group(2)
    ty, j = parse_type(rest)
    return Global(name, ty, _parse_init(ty, rest[j:].strip()))


def _parse_init(ty: str, init: str) -> bytes:
    init = init.strip()
    if init.startswith('c"'):
        m = re.match(r'c"(.*)"$', init, re.S)
        if not m:
            raise ElfError(f"字节串解析失败: {init!r}")
        body, out, k = m.group(1), bytearray(), 0
        while k < len(body):
            if body[k] == "\\":
                out.append(int(body[k + 1 : k + 3], 16))
                k += 3
            else:
                out.append(ord(body[k]))
                k += 1
        return bytes(out)
    if init.startswith("zeroinitializer"):
        return b"\0" * size_of(ty)
    if ty.startswith("["):
        inner = init[len(ty):].strip() if init.startswith(ty) else init
        if not inner.startswith("["):
            raise ElfError(f"数组初值解析失败: {init!r}")
        out = bytearray()
        for it in split_top(inner[1:-1]):
            t2, j = parse_type(it)
            out += _parse_init(t2, it[j:].strip())
        if len(out) != size_of(ty):
            raise ElfError(f"数组初值长度不符: {init!r}")
        return bytes(out)
    if ty in SCALAR_SIZE:
        if init in ("null", "undef"):
            return b"\0" * size_of(ty)
        if not re.match(r"^-?[0-9]+$", init):
            raise ElfError(f"标量初值解析失败: {init!r}")
        n = int(init)
        return (n & ((1 << (8 * size_of(ty))) - 1)).to_bytes(size_of(ty), "little")
    if ty.startswith("{"):
        # 结构体初值。LLVM 两种写法都要吃:
        #   `{ i64, i64 } { i64 1, i64 2 }`  (带类型前缀, 数组元素里常见)
        #   `{ i64 1, i64 2 }`               (无前缀 —— 能力域表 `@__loment_caps` 就是这种)
        # 字段类型**以 ty 为准**, 不从初值文本里猜: 嵌套聚合的值是 `{ i64 1, i64 2 }`,
        # 拆出来的"类型"其实是值本身。
        inner = init[len(ty):].strip() if init.startswith(ty) else init
        if not inner.startswith("{"):
            raise ElfError(f"结构体初值解析失败: {init!r}")
        flds = _fields(ty)
        vals = split_top(inner[1:-1])
        if len(vals) != len(flds):
            raise ElfError(f"结构体初值字段数不符: {init!r}")
        out = bytearray()
        for k, v in enumerate(vals):
            off, fty = field_offset(ty, k)
            if off > len(out):                    # 字段间的对齐空洞 LLVM 不写, 自己补
                out += b"\0" * (off - len(out))
            t2, j = parse_type(v)
            rest = v[j:].strip()
            out += _parse_init(t2, rest) if rest else _parse_init(fty, v)
        out += b"\0" * (size_of(ty) - len(out))    # 尾随填充
        return bytes(out)
    raise ElfError(f"v0 不支持的全局初值: {ty} {init!r}")


def _parse_func(lines: list[str], start: int) -> tuple[Func, int]:
    head = lines[start].strip()
    m = re.match(
        r"define\s+(?:(?:internal|private)\s+)?(.+?)\s+(@[-A-Za-z0-9_.$]+)\s*\((.*?)\)"
        r"(?:\s+\S+)*\s*\{?\s*$", head)
    if not m:
        raise ElfError(f"函数头解析失败: {head!r}")
    internal = "internal" in head or "private" in head
    ret, _ = parse_type(m.group(1))
    params = []
    for p in split_top(m.group(3)):
        if p:
            ty, k = parse_type(p)
            params.append((ty, p[k:].strip()))
    fn = Func(m.group(2)[1:], ret, params, internal)
    i, cur = start + 1, None
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line == "}":
            break
        if not line or line[0] in ";!" or line.startswith("#dbg_"):
            continue
        if line.endswith(":") and "=" not in line and not line.startswith("%"):
            cur = Block(line[:-1])
            fn.blocks.append(cur)
            continue
        while unbalanced(line) and i < len(lines):
            line += " " + lines[i].strip()
            i += 1
        if cur is None:  # LLVM 允许省略入口块标签
            cur = Block("entry")
            fn.blocks.append(cur)
        m2 = re.match(r"^(%[-A-Za-z0-9_.]+)\s*=\s*(.*)$", line)
        if m2:
            cur.instrs.append(Instr(m2.group(1), strip_meta(m2.group(2).strip())))
        else:
            cur.instrs.append(Instr(None, strip_meta(line)))
    return fn, i


# ------------------------------------------------------------------ x86-64 编码


def rex(w=0, r=0, x=0, b=0) -> bytes:
    v = 0x40 | (w << 3) | ((r >> 3) << 2) | ((x >> 3) << 1) | (b >> 3)
    return b"" if v == 0x40 else bytes([v])


def modrm(mod, reg, rm) -> bytes:
    return bytes([(mod << 6) | ((reg & 7) << 3) | (rm & 7)])


def _mem(reg_field, base, disp) -> bytes:
    out = bytes([(0b10 << 6) | ((reg_field & 7) << 3) | (base & 7)])
    if (base & 7) == 4:
        out += bytes([0x24])
    return out + struct.pack("<i", disp)


def mov_rr(dst, src) -> bytes:
    return rex(1, src, 0, dst) + b"\x89" + modrm(3, src, dst)


def mov_rr32(dst, src) -> bytes:
    return rex(0, src, 0, dst) + b"\x89" + modrm(3, src, dst)


def mov_ri(dst, imm) -> bytes:
    return rex(1, 0, 0, dst) + bytes([0xB8 + (dst & 7)]) + struct.pack("<Q", imm & 0xFFFFFFFFFFFFFFFF)


def mov_ri32(dst, imm) -> bytes:
    return rex(0, 0, 0, dst) + bytes([0xB8 + (dst & 7)]) + struct.pack("<I", imm & 0xFFFFFFFF)


def mov_ri_se(dst, imm) -> bytes:
    """`mov r/m64, imm32`（**符号扩展**）—— 与 `mov_ri32` 只差这一点。

    `mov_ri32` 发的是 `mov eax, imm32`：高位**清零**，所以常数只能是非负的
    （`-38` 会变成 `4294967258`）。要往寄存器里放一个**负**常数（错误码）就得用这一条。
    """
    return rex(1, 0, 0, dst) + b"\xC7" + modrm(3, 0, dst) + struct.pack("<i", imm)


def mov_rm(dst, base, disp) -> bytes:
    return rex(1, dst, 0, base) + b"\x8B" + _mem(dst, base, disp)


def mov_mr(base, disp, src) -> bytes:
    return rex(1, src, 0, base) + b"\x89" + _mem(src, base, disp)


def load_sized(reg, base, disp, size) -> bytes:
    if size == 8:
        return mov_rm(reg, base, disp)
    if size == 4:
        return rex(0, reg, 0, base) + b"\x8B" + _mem(reg, base, disp)
    op = 0xB6 if size == 1 else 0xB7
    return rex(0, reg, 0, base) + b"\x0F" + bytes([op]) + _mem(reg, base, disp)


def store_sized(base, disp, src, size) -> bytes:
    if size == 8:
        return mov_mr(base, disp, src)
    if size == 4:
        return rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp)
    pre = b"\x66" if size == 2 else b""
    op = 0x88 if size == 1 else 0x89
    return pre + rex(0, src, 0, base) + bytes([op]) + _mem(src, base, disp)


def lea(dst, base, disp) -> bytes:
    return rex(1, dst, 0, base) + b"\x8D" + _mem(dst, base, disp)


def alu_rr(op, dst, src) -> bytes:
    return rex(1, src, 0, dst) + bytes([op]) + modrm(3, src, dst)


def alu_ri(digit, dst, imm) -> bytes:
    """0x81 /digit dst, imm32；digit 是 Group1 的 /digit（0=add 5=sub 7=cmp）。"""
    return rex(1, 0, 0, dst) + b"\x81" + modrm(3, digit, dst) + struct.pack("<i", imm)


ADD, OR_, AND_, SUB, XOR_, CMP = 0, 1, 4, 5, 6, 7


def imul_rr(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xAF" + modrm(3, dst, src)


def shift_cl(op, reg) -> bytes:
    return rex(1, 0, 0, reg) + b"\xD3" + modrm(3, op, reg)


def group3(op, reg) -> bytes:
    return rex(1, 0, 0, reg) + b"\xF7" + modrm(3, op, reg)


def setcc(cc, reg) -> bytes:
    return rex(0, 0, 0, reg) + b"\x0F" + bytes([0x90 + CC[cc]]) + modrm(3, 0, reg)


def movzx8(dst, src) -> bytes:
    return rex(0, dst, 0, src) + b"\x0F\xB6" + modrm(3, dst, src)


def movsx8(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xBE" + modrm(3, dst, src)


def movsx16(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xBF" + modrm(3, dst, src)


def movsxd(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x63" + modrm(3, dst, src)


def zext_to64(reg, size) -> bytes:
    """reg 低 size 字节零扩展成 64 位（reg 已是原生宽度零扩展的形态时只需清高位）。"""
    if size >= 8:
        return b""
    if size == 4:
        return mov_rr32(reg, reg)
    if size == 2:
        return rex(1, reg, 0, reg) + b"\x0F\xB7" + modrm(3, reg, reg)
    return movzx8(reg, reg)


def sext_to64(reg, size) -> bytes:
    if size >= 8:
        return b""
    if size == 4:
        return movsxd(reg, reg)
    if size == 2:
        return movsx16(reg, reg)
    return movsx8(reg, reg)


def push_r(reg) -> bytes:
    return rex(0, 0, 0, reg) + bytes([0x50 + (reg & 7)])


def pop_r(reg) -> bytes:
    return rex(0, 0, 0, reg) + bytes([0x58 + (reg & 7)])


# ------------------------------------------------------------------ 汇编缓冲


class Asm:
    def __init__(self, base):
        self.base, self.buf = base, bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int, str]] = []

    def here(self) -> int:
        return self.base + len(self.buf)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ElfError(f"标签重复: {name}")
        self.labels[name] = self.here()

    def emit(self, b: bytes) -> None:
        self.buf += b

    def jmp(self, lbl: str) -> None:
        at = self.here()
        self.emit(b"\xE9\x00\x00\x00\x00")
        self.fixups.append((at + 1, lbl, 4, "rel"))

    def jcc(self, cc: str, lbl: str) -> None:
        at = self.here()
        self.emit(b"\x0F" + bytes([0x80 + CC[cc]]) + b"\x00\x00\x00\x00")
        self.fixups.append((at + 2, lbl, 4, "rel"))

    def call(self, lbl: str, extern: bool = False) -> None:
        """`extern=True` 标记"这条 call 走的是 C ABI 那条路"（`#630`）。

        动态链接只许把这类 fixup 变成导入；没写 `declare` 的调用留在 `rel`，
        到 `finalize()` 仍按"未定义的标签"报错 —— 否则它会被绑到 PLT 桩上，
        而调用点发的是 Loment 的栈约定，C 那头读到的实参全是垃圾。
        """
        at = self.here()
        self.emit(b"\xE8\x00\x00\x00\x00")
        self.fixups.append((at + 1, lbl, 4, "ext" if extern else "rel"))

    def mov_abs(self, reg: int, lbl: str) -> None:
        self.emit(mov_ri(reg, 0))
        self.fixups.append((self.here() - 8, lbl, 8, "abs"))

    def label_addr(self, lbl: str) -> int:
        """`@名字` 的地址。**函数**登记时不带 `@`（`f.name` 已剥过），全局量带 ——
        两种写法都认，否则 `ptrtoint ptr @f to i64` 在最终回填时找不到函数标签。"""
        key = lbl
        if key not in self.labels and key.startswith("@") and key[1:] in self.labels:
            key = key[1:]
        return self.labels[key]

    def finalize(self) -> bytes:
        for site, lbl, width, kind in self.fixups:
            # 全局量登记时带 `@`（`@.str…`），**函数**登记时不带（`f.name` 已剥过 `@`）。
            # `ptrtoint ptr @f to i64` 走的是后一条：试一下剥掉 `@` 的键。
            key = lbl
            if key not in self.labels and key.startswith("@") and key[1:] in self.labels:
                key = key[1:]
            if key not in self.labels:
                raise ElfError(f"未定义的标签: {lbl}")
            if kind == "abs":
                struct.pack_into("<Q", self.buf, site - self.base, self.labels[key])
            else:
                struct.pack_into("<i", self.buf, site - self.base,
                                 self.labels[key] - (site + width))
        return bytes(self.buf)


# ------------------------------------------------------------------ 结果类型


def result_type(text: str) -> str | None:
    t = text
    if t.startswith("alloca"):
        return "ptr"
    if t.startswith("load "):
        return parse_type(t[5:])[0]
    if t.startswith("getelementptr"):
        return "ptr"
    if t.startswith("extractvalue"):
        ty, i = parse_type(t[len("extractvalue "):])
        return field_offset(ty, int(t[len("extractvalue ") + i:].rsplit(",", 1)[-1]))[1]
    if t.startswith("insertvalue"):
        return parse_type(t[len("insertvalue "):])[0]
    if t.startswith("icmp"):
        return "i1"
    if t.startswith("atomicrmw"):
        return re.search(r",\s*(\w+)\s+\S+", t).group(1)
    for op in ("add", "sub", "mul", "and", "or", "xor",
               "sdiv", "udiv", "srem", "urem", "shl", "lshr", "ashr"):
        if t.startswith(op + " "):
            return parse_type(t[len(op) + 1:])[0]
    if t.startswith(("trunc ", "zext ", "sext ", "ptrtoint ", "inttoptr ")):
        return parse_type(re.search(r"\bto\s+(.+)$", t).group(1))[0]
    if t.startswith("phi "):
        return parse_type(t[4:])[0]
    if t.startswith("call "):
        if t[5:].lstrip().startswith("void"):
            return None
        return parse_type(t[5:])[0]
    return None


#: `call <ret> asm sideeffect "<名字>", "<约束>", (<操作数>)`。
#: 名字是**一整串自由文本**：`syscall` 就是那条指令本身，`thread_spawn @入口`（M84）是
#: 链接器自己发的**定制序列**（`docs/245`）—— 所以名字不写死在模式里，由 `_call` 分派。
#: 链接器**不组装汇编**：它认名字、自己发机器码（这就是 `docs/167` 那格"吃掉 clang 的活"）。
CALL_ASM_RE = re.compile(
    r'call\s+(.+?)\s+asm\s+sideeffect\s+"([^"]*)"\s*,\s*"([^"]*)"\s*\((.*)\)\s*$', re.S)
BR_COND_RE = re.compile(r"i1\s+(\S+?),\s*label\s+%([\w.]+?),\s*label\s+%([\w.]+)\s*$")
SWITCH_RE = re.compile(r"switch\s+(\w+)\s+(\S+?),\s*label\s+%([\w.]+)\s*\[(.*)\]\s*$", re.S)
CASE_RE = re.compile(r"(\w+)\s+(-?[0-9]+),\s*label\s+%([\w.]+)")
PHI_RE = re.compile(r"phi\s+(.+?)\s*\[", re.S)
PHI_BODY_RE = re.compile(r"\[([^\[\]]*)\]")
PHI_ENTRY_RE = re.compile(r"(.*?),\s*%([\w.]+)\s*$")


def parse_phi(text: str) -> tuple[str, list[tuple[str, str]]]:
    """`phi i1 [ v, %a ], [ w, %b ]` -> (类型, [(值, 前驱块标签), ...])。"""
    m = PHI_RE.match(text)
    if not m:
        raise ElfError(f"phi 解析失败: {text!r}")
    ty, _ = parse_type(m.group(1))
    entries = []
    for body in PHI_BODY_RE.findall(text):
        em = PHI_ENTRY_RE.match(body.strip())
        if not em:
            raise ElfError(f"phi 入边解析失败: {body!r}")
        entries.append((em.group(1).strip(), em.group(2)))
    return ty, entries


class Emitter:
    def __init__(self, globals_: list[Global], externs: set[str] | None = None,
                 hotswap: bool = False, swappable: list[str] | None = None):
        self.asm = Asm(TEXT_VADDR)
        self.externs = externs or set()   # docs/173: 这些名字走 C ABI 传参
        self.hotswap = hotswap
        #: 槽号按 **IR 顺序** 分配（不是按名字排序）—— 自举镜像按同一路走，两边
        #: 才可能逐字节相同；函数集合一变槽表整体位移，`tramp=<slot>` 会把它暴露出来。
        self.hot_slots = ({n: i for i, n in enumerate(swappable or [])}
                          if hotswap else {})
        self.hot_header: dict = {}
        self.globals = {g.name: g for g in globals_}
        self.cur: Func | None = None
        self.cur_block = ""
        self.slots: dict[str, int] = {}
        self.allocas: dict[str, tuple[int, int]] = {}
        self.frame = 0
        self.phis: dict[str, list[Instr]] = {}
        #: 热更新的元数据（`#1149`）：每个函数的帧布局哈希与调用点表。
        #: 采集**不改一个字节**（见 `loment_hotmeta_test` 的加性判据），只有
        #: `--hot-meta PATH` 才落盘。
        self.hot_funcs: list[dict] = []
        self._hot_entry = 0
        self._hot_calls: list[tuple[str, int]] = []
        self._hot_seq: dict[str, int] = {}
        #: `docs/367` stage 1：置成字典时，`__lom_shim` 的体由链接器直接写机器码。
        self.fault: dict | None = None

    # ---- 帧

    def plan_frame(self, f: Func) -> None:
        self.slots, self.allocas, self.frame, self.phis = {}, {}, 0, {}
        self.slots.update({n: -0 for n in ()})
        for blk in f.blocks:
            for ins in blk.instrs:
                if ins.text.startswith("phi ") and ins.dest:
                    self.phis.setdefault(blk.label, []).append(ins)
        arg = 16
        if is_agg(f.ret):
            arg = 24                      # 隐藏结果指针占 [rbp+16]
        for ty, name in f.params:
            self.slots[name] = arg
            arg += (max(8, size_of(ty)) + 7) // 8 * 8
        for blk in f.blocks:
            for ins in blk.instrs:
                if ins.text.startswith("alloca"):
                    ty, _ = parse_type(ins.text[len("alloca "):])
                    sz = size_of(ty)
                    self.frame += (sz + 7) // 8 * 8
                    self.allocas[ins.dest] = (-self.frame, sz)
                elif ins.dest and ins.dest not in self.slots:
                    rt = result_type(ins.text)
                    if rt:
                        sz = max(8, size_of(rt))
                        self.frame += (sz + 7) // 8 * 8
                        self.slots[ins.dest] = -self.frame

    # ---- 取值 / 存值

    def put(self, dest: str, reg: int) -> None:
        self.asm.emit(mov_mr(RBP, self.slots[dest], reg))

    def get(self, ty: str, val: str, reg: int) -> None:
        """标量 -> reg，**零扩展**到 64 位（符号语义在用到的地方显式 sext）。"""
        sz = size_of(ty)
        if val.startswith("%"):
            if val in self.allocas:
                # **alloca 的"值"就是它的地址** —— `%p.buf = alloca …` 之后把 `%p.buf`
                # 当**普通 SSA 值**用（`store ptr %p.buf, ptr %p.addr`，L0 提升出来的产物
                # 就是这一条）。`get_ptr` / `agg_addr` 一直在做这件事，`get` 漏了这一格 ——
                # 于是那份产物在链接期报"未定义的 SSA 值"（FujoJTOP/loment#239）。
                # 走这条不落 `zext`：`lea` 出来的已经是 64 位地址（自举侧同形，否则 ELF 不逐字节同）。
                self.asm.emit(lea(reg, RBP, self.allocas[val][0]))
                return
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的 SSA 值: {val}")
            self.asm.emit(load_sized(reg, RBP, off, sz))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
            if sz != 8:
                self.asm.emit(load_sized(reg, reg, 0, sz))
        elif val == "true":
            self.asm.emit(mov_ri32(reg, 1) if sz <= 4 else mov_ri(reg, 1))
        elif val in ("false", "null", "undef"):
            self.asm.emit(mov_ri32(reg, 0))
        else:
            self.asm.emit(mov_ri(reg, int(val)))
        if sz < 8:
            self.asm.emit(zext_to64(reg, sz))

    def get_signed(self, ty: str, val: str, reg: int) -> None:
        self.get(ty, val, reg)
        self.asm.emit(sext_to64(reg, size_of(ty)))

    def get_ptr(self, val: str, reg: int) -> None:
        if val.startswith("%"):
            if val in self.allocas:
                self.asm.emit(lea(reg, RBP, self.allocas[val][0]))
                return
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的指针: {val}")
            self.asm.emit(mov_rm(reg, RBP, off))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
        else:
            raise ElfError(f"v0 不支持指针常量: {val!r}")

    def agg_addr(self, val: str, reg: int) -> None:
        if val.startswith("%"):
            if val in self.allocas:
                self.asm.emit(lea(reg, RBP, self.allocas[val][0]))
                return
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的聚合值: {val}")
            self.asm.emit(lea(reg, RBP, off))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
        else:
            raise ElfError(f"v0 不支持聚合常量: {val!r}")

    def copy(self, dst: int, src: int, n: int) -> None:
        self.asm.emit(mov_rr(RDI, dst))
        self.asm.emit(mov_rr(RSI, src))
        self.asm.emit(mov_ri(RCX, n))
        self.asm.emit(b"\xF3\xA4")

    # ---- 指令

    def lower(self, ins: Instr) -> None:
        t, d = ins.text, ins.dest
        if t.startswith("alloca"):
            return
        if t.startswith("load "):
            return self._load(t, d)
        if t.startswith("store "):
            return self._store(t)
        if t.startswith("getelementptr"):
            return self._gep(t, d)
        if t.startswith("extractvalue"):
            return self._extract(t, d)
        if t.startswith("insertvalue"):
            return self._insert(t, d)
        if t.startswith("icmp "):
            return self._icmp(t, d)
        for op in ("add", "sub", "mul", "and", "or", "xor"):
            if t.startswith(op + " "):
                return self._alu(op, t, d)
        for op in ("sdiv", "udiv", "srem", "urem"):
            if t.startswith(op + " "):
                return self._div(op, t, d)
        for op in ("shl", "lshr", "ashr"):
            if t.startswith(op + " "):
                return self._shift(op, t, d)
        for op in ("trunc", "zext", "sext"):
            if t.startswith(op + " "):
                return self._cast(op, t, d)
        if t.startswith(("ptrtoint ", "inttoptr ")):
            return self._ptrcast(t, d)
        if t.startswith("phi "):
            return
        if t.startswith("br"):
            return self._br(t)
        if t.startswith("switch "):
            return self._switch(t)
        if t.startswith("call "):
            return self._call(t, d)
        if t.startswith("ret"):
            return self._ret(t)
        if t.startswith("unreachable"):
            self.asm.emit(b"\x0F\x0B")
            return
        if t.startswith("atomicrmw"):
            return self._atomic(t, d)
        raise Unsupported(f"v0 不支持的指令: {t!r}")

    def _binoperands(self, op, t):
        ty, i = parse_type(t[len(op) + 1:])
        a, b = split_top(t[len(op) + 1 + i:])
        return ty, a.strip(), b.strip()

    def _load(self, t, d):
        ty, i = parse_type(t[5:])
        _pty, pval = parse_operand(split_top(t[5 + i:].lstrip(" ,"))[0])
        if is_agg(ty):
            self.agg_addr(d, RDI)
            self.get_ptr(pval, RSI)
            self.copy(RDI, RSI, size_of(ty))
        else:
            self.get_ptr(pval, RAX)
            self.asm.emit(load_sized(RAX, RAX, 0, size_of(ty)))
            if size_of(ty) < 8:
                self.asm.emit(zext_to64(RAX, size_of(ty)))
            self.put(d, RAX)

    def _store(self, t):
        ty, i = parse_type(t[6:])
        parts = split_top(t[6 + i:].lstrip(" ,"))
        val = parts[0]
        _pty, pval = parse_operand(parts[1])
        if is_agg(ty):
            self.get_ptr(pval, RDI)
            self.agg_addr(val, RSI)
            self.copy(RDI, RSI, size_of(ty))
        else:
            self.get(ty, val, RAX)
            self.get_ptr(pval, RCX)
            self.asm.emit(store_sized(RCX, 0, RAX, size_of(ty)))

    def _gep(self, t, d):
        rest = re.sub(r"^getelementptr\s+(inbounds\s+)?", "", t)
        ty, i = parse_type(rest)
        parts = split_top(rest[i:].lstrip(" ,"))
        _pty, pval = parse_operand(parts[0])
        self.get_ptr(pval, RAX)
        cur = ty
        idxs = parts[1:]
        # LLVM 的**第一个**下标按**指针指向的类型**整块步进，类型停在 pointee；之后的下标
        # 才进入数组/结构体。把数组规则也套在首下标上，`[64 x i64], i64 cap` 就只走 8 字节
        # 而不是一整行 512 —— 每个有状态能力的行因此互相重叠（`#1830`）。
        if idxs:
            ity, ival = parse_operand(idxs[0])
            self.get_signed(ity, ival, RCX)
            step = size_of(ty)
            if step != 1:
                self.asm.emit(mov_ri(RDX, step))
                self.asm.emit(imul_rr(RCX, RDX))
            self.asm.emit(alu_rr(0x01, RAX, RCX))
            idxs = idxs[1:]
        for p in idxs:
            ity, ival = parse_operand(p)
            # **GEP 的下标是**符号扩展的（LLVM LangRef）—— `get` 按设计零扩展，所以这里
            # 必须走 `get_signed`：`getelementptr i8, ptr %p, i32 -4` 要的是 `%p - 4`，
            # 零扩展把它变成 `%p + 4294967292`（4 GiB 之外，撞了就是 SIGSEGV）。
            # 结构体那条路不受影响：它走 `field_offset` + `alu_ri`，那条本来就符号扩展。
            self.get_signed(ity, ival, RCX)
            if cur.startswith("["):
                et = elem_of(cur)
                step = size_of(et)
                cur = et
            elif cur.startswith("{"):
                if not re.match(r"^-?[0-9]+$", ival):
                    raise Unsupported("v0 只支持常量**字段**下标的结构体 GEP（动态下标请用 extractvalue）")
                off, cur = field_offset(cur, int(ival))
                if off:
                    self.asm.emit(alu_ri(ADD, RAX, off))
                continue
            else:
                step = size_of(cur)
            if step != 1:
                self.asm.emit(mov_ri(RDX, step))
                self.asm.emit(imul_rr(RCX, RDX))
            self.asm.emit(alu_rr(0x01, RAX, RCX))
        self.put(d, RAX)

    def _extract(self, t, d):
        ty, i = parse_type(t[len("extractvalue "):])
        val, idx_s = t[len("extractvalue ") + i:].lstrip(" ,").rsplit(",", 1)
        off, fty = field_offset(ty, int(idx_s.strip()))
        if is_agg(fty):
            self.agg_addr(d, RDI)
            self.agg_addr(val.strip(), RAX)
            self.asm.emit(lea(RSI, RAX, off))
            self.copy(RDI, RSI, size_of(fty))
        else:
            self.agg_addr(val.strip(), RAX)
            self.asm.emit(load_sized(RAX, RAX, off, size_of(fty)))
            if size_of(fty) < 8:
                self.asm.emit(zext_to64(RAX, size_of(fty)))
            self.put(d, RAX)

    def _insert(self, t, d):
        ty, i = parse_type(t[len("insertvalue "):])
        parts = split_top(t[len("insertvalue ") + i:].lstrip(" ,"))
        agg_val = parts[0]
        ety, eval_ = parse_operand(parts[1])
        idx = int(parts[2])
        off, fty = field_offset(ty, idx)
        dst = self.slots[d]
        if agg_val == "undef":
            self.asm.emit(lea(RDI, RBP, dst))
            self.asm.emit(mov_ri(RCX, size_of(ty)))
            self.asm.emit(mov_ri32(RAX, 0))
            self.asm.emit(b"\xF3\xAA")  # rep stosb
        else:
            self.asm.emit(lea(RDI, RBP, dst))
            self.agg_addr(agg_val, RSI)
            self.copy(RDI, RSI, size_of(ty))
        if is_agg(ety):
            self.asm.emit(lea(RDI, RBP, dst + off) if False else lea(RDI, RBP, dst))
            self.asm.emit(alu_ri(ADD, RDI, off) if off else b"")
            self.agg_addr(eval_, RSI)
            self.copy(RDI, RSI, size_of(ety))
        else:
            self.get(ety, eval_, RAX)
            self.asm.emit(store_sized(RBP, dst + off, RAX, size_of(ety)))

    def _icmp(self, t, d):
        # `#376`：原先直接 `ICMP_CC[m.group(1)]` —— 一条认不出的 `icmp` 会让整个 PE
        # 后端甩 Python 栈（`'NoneType' object has no attribute 'group'`），没指令、
        # 没谓词、没来源。两条失败都**点名**：看不懂的整条指令 / 不认识的谓词。
        m = re.match(r"icmp\s+(?:samesign\s+)?(\w+)\s+(.+)$", t, re.S)
        if not m:
            raise Unsupported(f"PE 后端看不懂这条 icmp：{t.strip()!r}")
        if m.group(1) not in ICMP_CC:
            raise Unsupported(
                f"PE 后端不认识比较谓词 icmp {m.group(1)}：{t.strip()!r}")
        cc, signed = ICMP_CC[m.group(1)]
        ty, i = parse_type(m.group(2))
        a, b = split_top(m.group(2)[i:])
        if signed:
            self.get_signed(ty, a.strip(), RAX)
            self.get_signed(ty, b.strip(), RCX)
        else:
            self.get(ty, a.strip(), RAX)
            self.get(ty, b.strip(), RCX)
        self.asm.emit(alu_rr(0x39, RAX, RCX))
        self.asm.emit(setcc(cc, RAX))
        self.asm.emit(movzx8(RAX, RAX))
        self.put(d, RAX)

    def _alu(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        self.get(ty, a, RAX)
        self.get(ty, b, RCX)
        if op == "mul":
            self.asm.emit(imul_rr(RAX, RCX))
        else:
            self.asm.emit(alu_rr({"add": 0x01, "sub": 0x29, "and": 0x21,
                                  "or": 0x09, "xor": 0x31}[op], RAX, RCX))
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(ty)))
        self.put(d, RAX)

    def _div(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        signed = op[0] == "s"
        if signed:
            self.get_signed(ty, a, RAX)
            self.get_signed(ty, b, RCX)
            self.asm.emit(b"\x48\x99")  # cqo
        else:
            self.get(ty, a, RAX)
            self.get(ty, b, RCX)
            self.asm.emit(mov_ri32(RDX, 0))
        self.asm.emit(group3(7 if signed else 6, RCX))
        reg = RDX if "rem" in op else RAX
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(reg, size_of(ty)))
        self.put(d, reg)

    def _shift(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        if op == "ashr":
            self.get_signed(ty, a, RAX)
        else:
            self.get(ty, a, RAX)
        self.get(ty, b, RCX)
        self.asm.emit(shift_cl({"shl": 4, "lshr": 5, "ashr": 7}[op], RAX))
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(ty)))
        self.put(d, RAX)

    def _cast(self, op, t, d):
        m = re.match(r"(.+?)\s+(\S+)\s+to\s+(.+)$", t[len(op) + 1:], re.S)
        fty, fval, tty = parse_type(m.group(1))[0], m.group(2), parse_type(m.group(3))[0]
        self.get(fty, fval, RAX)
        fsz, tsz = size_of(fty), size_of(tty)
        if op == "zext":
            self.asm.emit(zext_to64(RAX, fsz))
        elif op == "sext":
            self.asm.emit(sext_to64(RAX, fsz))
        if tsz < 8:
            self.asm.emit(zext_to64(RAX, tsz))
        self.put(d, RAX)

    def _ptrcast(self, t, d):
        op = t.split(" ", 1)[0]
        m = re.match(r"(.+?)\s+(\S+)\s+to\s+(.+)$", t[len(op) + 1:], re.S)
        fty, fval, tty = parse_type(m.group(1))[0], m.group(2), parse_type(m.group(3))[0]
        if op == "ptrtoint":
            self.get_ptr(fval, RAX)
        else:
            self.get(fty, fval, RAX)
        if size_of(tty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(tty)))
        self.put(d, RAX)

    def _br(self, t):
        body = t[2:].strip()
        if body.startswith("label"):
            return self._jump_to(body[len("label "):].strip().lstrip("%"))
        m = BR_COND_RE.search(body)
        self.get("i1", m.group(1), RAX)
        self.asm.emit(b"\x85\xC0")  # test eax, eax
        a, b = m.group(2), m.group(3)
        a, nxt = self._emit_phis_then(a)
        self.asm.jcc("ne", self.blk(a))
        self.asm.label(nxt)
        self._jump_to(b)

    def _jump_to(self, tgt: str) -> None:
        tgt, nxt = self._emit_phis_then(tgt)
        self.asm.jmp(self.blk(tgt))
        self.asm.label(nxt)

    def _emit_phis_then(self, tgt: str) -> tuple[str, str]:
        """给 target 的每个 phi 写入来自**当前块**的入边值，返回 (真实标签, 跳过标签)。

        **两趟，不是一趟。** LLVM 的 phi 是**同时**赋值：同一前驱上的若干 phi
        按"读旧值、写新值"一次完成。一趟顺序写会当场自我覆盖 —— 最典型的是
        `%cur = phi [.., %nxt]` 与 `%prev = phi [.., %cur]` 这一对（链表遍历里
        的标准写法）：先写 `%cur` 再读 `%cur` 给 `%prev`，`%prev` 拿到的是 `%nxt`。

        实测（2026-09-25）：这一条让 `__loment_free` 的插入**每步都以为走到了链尾**，
        于是每次都插在头部 —— 空闲链表恒为 1 个节点，`gc_auto` 因此回收不掉、
        arena 一路涨到 OOM。最小复现是一个三行的循环：`%j = phi [.., %i]` 该得 2，
        一趟写会得 3。所以这一趟暂存不是优化，是语义。

        暂存用 `push`/`pop`（各自 1 字节），不占帧 —— 栈本来就是现成的暂存区，
        而 pop 反序正好把顺序还原。自举侧 `loment/tools/lomelf.lomt` 同一形态，
        两边的产物才逐字节相同。
        """
        nxt = f"__ph{self.asm.here():x}"
        phis = self.phis.get(tgt)
        if not phis:
            return tgt, nxt
        pending: list[tuple[Instr, str, bool]] = []
        for phi in phis:
            pty, entries = parse_phi(phi.text)
            for ival, pred in entries:
                if pred != self.cur_block:
                    continue
                agg = is_agg(pty)
                if agg:
                    ity, i2 = parse_type(ival)
                    self.agg_addr(ival[i2:].strip(), RSI)
                    self.asm.emit(push_r(RSI))
                else:
                    self.get(pty, ival, RAX)
                    self.asm.emit(push_r(RAX))
                pending.append((phi, pty, agg))
        for phi, pty, agg in reversed(pending):
            if agg:
                self.asm.emit(pop_r(RSI))
                self.asm.emit(lea(RDI, RBP, self.slots[phi.dest]))
                self.copy(RDI, RSI, size_of(pty))
            else:
                self.asm.emit(pop_r(RAX))
                self.put(phi.dest, RAX)
        return tgt, nxt

    def _switch(self, t):
        m = SWITCH_RE.match(t)
        ty, val, default, body = m.group(1), m.group(2), m.group(3), m.group(4)
        self.get(ty, val, RAX)
        for _ity, num, lbl in CASE_RE.findall(body):
            self.asm.emit(alu_ri(CMP, RAX, int(num)))
            nxt = f"__sw{self.asm.here():x}"
            self.asm.jcc("ne", nxt)
            self._jump_to(lbl)
            self.asm.label(nxt)
        self._jump_to(default)

    def _call(self, t, d):
        m = CALL_ASM_RE.match(t)
        if m:
            asm_name = m.group(2)
            if asm_name == "syscall":
                return self._call_asm(m, d)
            if asm_name.startswith("thread_spawn"):
                return self._call_spawn(m, d)
            raise Unsupported(f"不认识的内联汇编名: {asm_name!r} (docs/245)")
        rest = t[5:].lstrip()
        if rest.startswith("void"):
            ret_ty, rest = None, rest[4:].lstrip()
        else:
            ret_ty, i = parse_type(rest)
            rest = rest[i:].lstrip()
        cm = re.match(r"(@[-A-Za-z0-9_.]+|%[-A-Za-z0-9_.]+)\s*\((.*)\)\s*$", rest, re.S)
        callee, argstr = cm.group(1), cm.group(2)
        args = [parse_operand(x) for x in split_top(argstr)] if argstr.strip() else []
        if callee.startswith("%"):
            if getattr(self, "pe_target", False):
                # Windows 侧的函数值要另一套约定（`_Ir` 发的记录是 ELF 的那一套），
                # 而现在两边都还没有 —— **响亮地拒绝**，不静默编一个假的出来。
                raise Unsupported(
                    "间接调用只在 ELF 目标可用 —— Windows 侧的闭包记录还没有约定 (docs/348)")
            # 间接调用（`docs/348`）：callee 是一个装着代码地址的 SSA 值。约定与直接调用
            # **同一套**栈传参（`_Ir` 已经把环境指针放在实参 0）。**先压完实参再读寄存器**：
            # `get` 用 RAX 求值，先把代码地址读进 RCX 会被后面的求值踩掉。
            ret_agg = ret_ty is not None and is_agg(ret_ty)
            if len(args) > 16:
                raise Unsupported("v0 最多 16 个实参")
            total = 0
            for ty, _ in reversed(args):
                total += (max(8, size_of(ty)) + 7) // 8 * 8
            if ret_agg:
                total += 8
            for ty, val in reversed(args):
                if is_agg(ty):
                    sz = (size_of(ty) + 7) // 8 * 8
                    self.asm.emit(alu_ri(SUB, RSP, sz))
                    self.asm.emit(mov_rr(RDI, RSP))
                    self.agg_addr(val, RSI)
                    self.copy(RDI, RSI, size_of(ty))
                else:
                    self.get(ty, val, RAX)
                    self.asm.emit(push_r(RAX))
            if ret_agg:
                self.asm.emit(lea(RAX, RBP, self.slots[d]))
                self.asm.emit(push_r(RAX))
            self.get("ptr", callee, RCX)
            self.asm.emit(b"\xFF\xD1")          # call rcx
            if total:
                self.asm.emit(alu_ri(ADD, RSP, total))
            if d is not None and not ret_agg:
                self.put(d, RAX)
            return
        # ---- 外部函数 (docs/173): 实参走**寄存器**, 不走我们自己的栈约定。
        # 求值顺序: **从右往左**逐个算进 RAX 再 push, 然后从左往右 pop 进目标寄存器 ——
        # 这样 push 完栈顶正好是第 0 个实参, pop 的先后与寄存器顺序自然对齐。不能直接
        # `self.get(ty, val, RDI)` 一个个算: `get` 内部要用 RAX/RCX 求值, 会把前面已经放好的
        # 寄存器踩掉。
        if callee[1:] in self.externs:
            if len(args) > len(C_ARG_REGS):
                raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段最多 6 个实参 (docs/173 §3)")
            if ret_ty is not None and is_agg(ret_ty):
                raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段不接受聚合返回值")
            for ty, val in reversed(args):
                if is_agg(ty):
                    raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段不接受聚合实参")
                self.get(ty, val, RAX)
                self.asm.emit(push_r(RAX))
            for reg, _a in zip(C_ARG_REGS, args):
                self.asm.emit(pop_r(reg))
            # ⚠ **C ABI 要求 `call` 那一刻 `rsp ≡ 0 (mod 16)`**（System V）。我们自己的调用
            # 约定把实参压栈再弹出，压弹是**平衡**的、但**对齐不是** —— 我们的帧不对齐，于是
            # 到了这里 rsp 常常是 8（mod 16）。不修的话**任何用到 16 字节 SSE 存的 C 函数都会
            # 当场 `#GP`**：实测（`FujoJTOP/loment#359`）zlib 的 `compress` 就死在
            # `movaps %xmm0,-0x70(%rbp)` 上（`si_code=SI_KERNEL`、`si_addr=NULL` —— 那不是
            # 段越界，是**对不齐**）。
            #
            # 用 **RBX** 存原 rsp：它是 SysV 的**被调方保存**寄存器，C 返回时一定还是原值。
            # ⚠ 正因为它被调方保存，**我们自己必须先保存调用方的 RBX**（`#626`）：C 的
            # 帧里 RBX 可能是它的局部数据，而我们把它改成了自己的栈指针、还不还原 ——
            # C → Loment 回调（zlib 的 zalloc/zfree、qsort 的比较函数）再走一次 extern
            # 调用时，上层 C 帧读到的就是 rsp。push/pop 这一对让序列"来去一样"。
            self.asm.emit(push_r(RBX))
            self.asm.emit(mov_rr(RBX, RSP))
            self.asm.emit(alu_ri(AND_, RSP, -16))
            self.asm.call(callee[1:], extern=True)
            self._hot_record("extern:" + callee[1:])
            self.asm.emit(mov_rr(RSP, RBX))
            self.asm.emit(pop_r(RBX))
            # **返回值照旧落回目标槽** —— 与内部调用同一步。少了这一句, 调用结果**丢掉**,
            # 后面读到的是那个槽的旧值: 症状是"程序能跑、结果是假 0" (实测第一版就是这样:
            # `c_add(3,4) + c_mul(5,6)` 退出码 0 而不是 37)。
            if d is not None:
                self.put(d, RAX)
            return
        ret_agg = ret_ty is not None and is_agg(ret_ty)
        if len(args) > 16:
            raise Unsupported("v0 最多 16 个实参")
        total = 0
        for ty, _ in reversed(args):
            total += (max(8, size_of(ty)) + 7) // 8 * 8
        if ret_agg:
            total += 8
        for ty, val in reversed(args):
            if is_agg(ty):
                sz = (size_of(ty) + 7) // 8 * 8
                self.asm.emit(alu_ri(SUB, RSP, sz))  # sub rsp, sz
                self.asm.emit(mov_rr(RDI, RSP))
                self.agg_addr(val, RSI)
                self.copy(RDI, RSI, size_of(ty))
            else:
                self.get(ty, val, RAX)
                self.asm.emit(push_r(RAX))
        if ret_agg:
            self.asm.emit(lea(RAX, RBP, self.slots[d]))
            self.asm.emit(push_r(RAX))
        if callee == "@llvm.trap":
            self.asm.emit(b"\x0F\x0B")
        else:
            self.asm.call(self._hot_target(callee[1:]))
            self._hot_record(callee[1:])
        if total:
            self.asm.emit(alu_ri(ADD, RSP, total))
        if d is not None and not ret_agg:
            self.put(d, RAX)

    def _call_asm(self, m, d):
        cons, argstr = m.group(3), m.group(4)
        regs = [x.strip().strip("{}") for x in cons.split(",") if x.strip().startswith("{")]
        regs = [r for r in regs if not r.startswith(("=", "~"))]
        args = [parse_operand(x) for x in split_top(argstr)] if argstr.strip() else []
        regmap = {"ax": RAX, "di": RDI, "si": RSI, "dx": RDX, "r10": R10, "r8": R8,
                  "r9": R9}
        if len(regs) != len(args):
            raise Unsupported(f"内联汇编约束与实参不匹配: {cons!r}")
        # 依赖寄存器顺序: rax 最后写 (syscall 号), 但其它寄存器不能互相踩
        # （`r9` 是 Linux 第 6 个实参寄存器，`syscall7` 用满它 —— 孪生侧那张表也要同步）
        for regname in ("di", "si", "dx", "r10", "r8", "r9"):
            for (ty, val), r in zip(args, regs):
                if r == regname:
                    self.get(ty, val, regmap[regname])
        for (ty, val), r in zip(args, regs):
            if r == "ax":
                self.get(ty, val, RAX)
        self._emit_syscall()
        if d is not None:
            self.put(d, RAX)

    def _emit_syscall(self) -> None:
        """内联汇编 syscall 落在指令上的那一步。**整个 syscall 面只有这一处** ——
        PE 目标覆盖它去走 `__win_syscall`，其余取参/存值逻辑两边共用。"""
        self.asm.emit(b"\x0F\x05")

    #: `thread_spawn` 的定制序列（M84，`docs/245`）。**两边发的是同一串字节** ——
    #: 自举侧（`loment/tools/lomelf.lomt` 的 `do_call_spawn`）照抄这一段，`loment_elf_test`
    #: 的孪生那格在逐字节钉它。所以这里是**字面机器码**而不是一串 `mov_*` 助手：
    #: 助手会随两侧各自的寄存器编码习惯漂，字面字节不会。
    #:
    #: 布局（`|<-- `_SPAWN_HEAD` -->|<-- call 5 字节 -->|<-- `_SPAWN_TAIL` -->`）：
    #: ```asm
    #:                            ; 进门前: `get` 已把栈顶取进 rsi、子线程实参取进 r9、
    #:                            ;         完成通知地址取进 r12 (0 = 不要通知)
    #:   BF 00 0F 05 00           mov  edi, 331520   ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM
    #:   31 D2                    xor  edx, edx      ; ptid = NULL
    #:   45 31 D2                 xor  r10d, r10d    ; ctid = NULL
    #:   45 31 C0                 xor  r8d, r8d      ; newtls = 0
    #:   B8 38 00 00 00           mov  eax, 56       ; clone
    #:   0F 05                    syscall
    #:   48 85 C0                 test rax, rax
    #:   75 35                    jne  .parent       ; 父线程: rax = tid, 跳过下面 53 字节
    #:   48 89 E5                 mov  rbp, rsp      ; **子线程**: 换掉继承来的 rbp
    #:   41 51                    push r9            ; 实参压在栈顶 = 被调方的第 0 个形参
    #:   E8 xx xx xx xx           call <入口>        ; **直接调用** —— 静态调用图不变
    #:   41 89 C5                 mov  r13d, eax     ; 存住入口的返回值
    #:   4D 85 E4                 test r12, r12      ; 有通知地址吗
    #:   74 19                    jz   .no_done      ; 没有就直落 exit
    #:   F0 41 FF 04 24           lock incl [r12]    ; 原子加一 (也是释放屏障)
    #:   44 89 E7                 mov  edi, r12d
    #:   B8 CA 00 00 00           mov  eax, 202      ; futex
    #:   BE 81 00 00 00           mov  esi, 129      ; FUTEX_WAKE|FUTEX_PRIVATE_FLAG
    #:   BA 01 00 00 00           mov  edx, 1        ; 唤醒一个等待者
    #:   0F 05                    syscall
    #:   44 89 EF                 .no_done: mov edi, r13d
    #:   B8 3C 00 00 00           mov  eax, 60       ; exit —— **不是 exit_group**
    #:   0F 05                    syscall            ; (后者会把整组线程一起杀掉)
    #: ```
    #: 两条要点，都不是可有可无的：
    #: 1. **子线程必须换 `rbp`**。`clone` 只换 `rsp`，而这个后端**所有局部与临时值都是
    #:    `rbp` 相对寻址** —— 不换就是子线程每一次读写都落在父线程的帧里，`clone` 的
    #:    返回值落点成了共享槽，`if r == 0` 这句判断本身在读共享内存（实测症状：
    #:    第一个子线程从不走那条路，却出现第二个 —— 子线程被当成父线程接着往下跑）。
    #: 2. **实参走 r9 而不是栈**。系统调用**不碰 r9**（`syscall` 只踩 rcx/r11），而子线程
    #:    在 `clone` 那一刻**继承整个寄存器组**，所以 r9 是唯一能把一个值安全带过
    #:    `clone` 的寄存器；栈顶那个字节属于父线程的帧，不能读。r12（通知地址）同理。
    #: 3. **通知那一段必须在入口返回之后、`exit` 之前**，而**只有编译器站得在这个位置**：
    #:    入口是 Loment 写的，它一 `return`，控制权就回到了这段尾声。所以 `join` 的握手
    #:    是**产物保证的**，不靠工作线程自觉。
    _SPAWN_HEAD = (b"\xBF\x00\x0F\x05\x00"    # mov edi, 331520
                   b"\x31\xD2"                # xor edx, edx
                   b"\x45\x31\xD2"            # xor r10d, r10d
                   b"\x45\x31\xC0"            # xor r8d, r8d
                   b"\xB8\x38\x00\x00\x00"    # mov eax, 56 (clone)
                   b"\x0F\x05"                # syscall
                   b"\x48\x85\xC0"            # test rax, rax
                   b"\x75\x35"                # jne .parent —— 跳过下面 53 字节
                   b"\x48\x89\xE5"            # mov rbp, rsp
                   b"\x41\x51")               # push r9
    #: 子线程在**入口返回之后**跑的那一段：先存下返回值，再（要是有通知地址的话）原子加一
    #: 并 `futex_wake`，最后 `exit`。**这一段是"入口一 return 就没机会再跑任何代码"的补丁**
    #: —— 没有它，`join` 只能靠工作线程自己记得喊一声，忘了就是挂死。
    _SPAWN_TAIL = (b"\x41\x89\xC5"            # mov r13d, eax   —— 存住入口的返回值
                   b"\x4D\x85\xE4"            # test r12, r12   —— r12 = 完成通知地址 (0 = 无)
                   b"\x74\x19"                # jz .no_done —— 跳过下面 25 字节
                   b"\xF0\x41\xFF\x04\x24"    # lock incl [r12] —— 原子加一 (也是释放屏障)
                   b"\x44\x89\xE7"            # mov edi, r12d
                   b"\xB8\xCA\x00\x00\x00"    # mov eax, 202 (futex)
                   b"\xBE\x81\x00\x00\x00"    # mov esi, 129 (FUTEX_WAKE|FUTEX_PRIVATE_FLAG)
                   b"\xBA\x01\x00\x00\x00"    # mov edx, 1 (唤醒一个)
                   b"\x0F\x05"                # syscall
                   b"\x44\x89\xEF"            # .no_done: mov edi, r13d
                   b"\xB8\x3C\x00\x00\x00"    # mov eax, 60 (exit)
                   b"\x0F\x05")               # syscall

    def _call_spawn(self, m, d):
        """`thread_spawn @入口`：发上面那段定制序列，中间那条 `E8` 指向**直接调用**。"""
        ent = m.group(2)[len("thread_spawn"):].strip()
        if not ent.startswith("@"):
            raise Unsupported(f"thread_spawn 的 asm 名字缺入口: {m.group(2)!r} (docs/245)")
        ent = ent[1:]
        args = [parse_operand(x) for x in split_top(m.group(4))] if m.group(4).strip() else []
        if len(args) != 3:
            raise Unsupported("thread_spawn 需要 3 个操作数（栈顶、子线程实参、完成通知地址）")
        # 按 IR 里的顺序取（栈顶 → rsi、实参 → r9、通知地址 → r12）。`get` 对简单操作数
        # 只用目标寄存器，所以这三条互不踩。
        self.get(args[0][0], args[0][1], RSI)
        self.get(args[1][0], args[1][1], R9)
        self.get(args[2][0], args[2][1], R12)
        self.asm.emit(self._SPAWN_HEAD)
        self.asm.call(ent)
        self.asm.emit(self._SPAWN_TAIL)
        if d is not None:
            self.put(d, RAX)

    def _ret(self, t):
        if t.strip() != "ret void":
            m = re.match(r"ret\s+(.+?)\s+(\S+)\s*$", t, re.S)
            ty, i = parse_type(m.group(1))
            val = t[len("ret ") + i:].strip()
            if is_agg(ty):
                # 隐藏结果指针在 [rbp+16]: **取它指向的缓冲**, 拷过去, 再把指针回传
                self.asm.emit(mov_rm(RDI, RBP, 16))
                self.agg_addr(val, RSI)
                self.copy(RDI, RSI, size_of(ty))
                self.asm.emit(mov_rm(RAX, RBP, 16))
            else:
                self.get(ty, val, RAX)
        self.asm.emit(b"\xC9\xC3")  # leave; ret

    def _atomic(self, t, d):
        m = re.match(r"atomicrmw\s+add\s+(\w+)\s+(\S+?),\s*(\w+)\s+(\S+?)(?:\s+\w+)?$", t)
        vty, pval = m.group(1), m.group(2)
        self.get_ptr(pval, RCX)
        self.get(m.group(3), m.group(4), RAX)
        self.asm.emit(b"\xF0" + rex(1, RAX, 0, RCX) + b"\x0F\xC1" + _mem(RAX, RCX, 0))
        if d:
            self.put(d, RAX)

    # ---- 函数

    def blk(self, label: str) -> str:
        assert self.cur is not None
        return f"{self.cur.name}${label}"

    def _hot_target(self, name: str) -> str:
        """内部调用在热更新档下走**槽**，不是直接跳到实现。"""
        if self.hotswap and name in self.hot_slots:
            return f"__hot_tramp_{name}"
        return name

    def emit_trampolines(self) -> None:
        """槽表：`TEXT_VADDR + slot*HOT_STRIDE`，每条 `jmp rel32` 到当前实现。"""
        for name, slot in sorted(self.hot_slots.items(), key=lambda kv: kv[1]):
            at = self.asm.here()
            want = TEXT_VADDR + slot * HOT_STRIDE
            if at != want:
                raise ElfError(f"热更新槽 {slot} 落在 {at:#x}，应为 {want:#x}")
            self.asm.label(f"__hot_tramp_{name}")
            self.asm.jmp(name)
            self.asm.emit(b"\x90" * (HOT_STRIDE - 5))

    def emit_gate(self) -> None:
        """入口闸（`#1150`）：读全局 epoch；顺手把调用者的 `rbp` 记下来（`#1152`）。

        帧链只能从闸里拿到：语言没有"取当前帧指针"的内建，而闸在**每一个**函数入口
        被调用，`[rbp]` 正好是那个函数的帧指针。固定核的 `hot_install` 从 `epoch + 8`
        读出它，再沿 `[rbp]` 一路走到根，把每一帧的返回地址改指到新镜像。

        字节布局（`self_gate` 是 text 相对偏移）：

        ```
        0   55                 push rbp
        1   48 89 E5           mov rbp, rsp
        4   48 8B 4D 00        mov rcx, [rbp]      ; 调用者的帧指针
        8   48 B8 <imm64>      movabs rax, &epoch  ; imm64 在 +10
        18  48 89 48 08        mov [rax+8], rcx    ; 帧指针存进 epoch+8
        22  83 38 00           cmp dword [rax], 0
        25  74 00              je +0
        27  5D C3              pop rbp; ret
        ```
        """
        start = self.asm.here()
        self.hot_header["gate"] = start - TEXT_VADDR
        self.asm.label("__loment_hot_gate")
        self.asm.emit(b"\x55")                  # push rbp
        self.asm.emit(b"\x48\x89\xE5")          # mov rbp, rsp
        self.asm.emit(b"\x48\x8B\x4D\x00")      # mov rcx, [rbp]
        self.asm.mov_abs(RAX, "__loment_hot_epoch")
        self.asm.emit(b"\x48\x89\x48\x08")      # mov [rax+8], rcx
        self.asm.emit(b"\x83\x38\x00")          # cmp dword [rax], 0
        self.asm.jcc("e", "__loment_hot_gate_ret")
        self.asm.label("__loment_hot_gate_pending")
        self.asm.label("__loment_hot_gate_ret")
        self.asm.emit(b"\x5D\xC3")              # pop rbp; ret
        # **固定核的摘要**：闸的字节 + 槽表的定长。换版时这一段必须逐字节相同，
        # 否则装载器拒绝（准入侧把它当作 fixed_core 的真身，见 docs/280 §4）。
        body = bytes(self.asm.buf[start - TEXT_VADDR:])
        self.hot_header["gate_digest"] = hashlib.sha256(
            body + f"stride={HOT_STRIDE}".encode("ascii")).hexdigest()

    def emit_func(self, f: Func) -> None:
        if self.fault is not None and f.name == "__lom_shim":
            # 内核按 SysV 传参、本链接器的内部调用是栈机 —— 这个 shim 只做寄存器落槽。
            self.asm.label(f.name)
            shim = self.asm.here()
            handler = self.asm.labels["__lom_handler"]
            g = self.globals
            self.asm.emit(_fault_shim_bytes(g["@__lom_sig"].addr,
                                            g["@__lom_info"].addr,
                                            g["@__lom_uctx"].addr, handler, shim))
            return
        self.cur = f
        self.plan_frame(f)
        self.asm.label(f.name)
        self._hot_entry = self.asm.here()
        self._hot_calls = []
        self._hot_seq = {}
        self.asm.emit(b"\x55")          # push rbp
        self.asm.emit(b"\x48\x89\xE5")  # mov rbp, rsp
        if self.frame:
            self.asm.emit(alu_ri(SUB, RSP, self.frame))
        if self.hotswap:
            self.asm.call("__loment_hot_gate")
            self._hot_record("__gate")
        plan = self.fault["plan"] if self.fault is not None else None
        for bi, blk in enumerate(f.blocks):
            self.cur_block = blk.label
            self.asm.label(self.blk(blk.label))
            bgi = plan["blk_idx"].get((f.name, bi)) if plan is not None else None
            for ii, ins in enumerate(blk.instrs):
                site_ix = None
                here0 = self.asm.here()
                if bgi is not None:
                    kw = _site_kind_width(ins.text)
                    if kw is not None:
                        # 长度在 **lower 之后** 才定得下来：一条 IR 指令展开成一段机器码，
                        # 真正的那条 `mov` 往往不在段首（先算操作数）。报告只在
                        # `[起点, 起点+长度)` 里认站点 —— 否则函数序言里的 `push`
                        # 会被算到**上一个函数**最后一个站点头上（`docs/367` §4.3.1）。
                        plan["sites"].append([here0, plan["fn_idx"][f.name],
                                              bgi, ii, kw[0], kw[1], 0])
                        site_ix = len(plan["sites"]) - 1
                self.lower(ins)
                if site_ix is not None:
                    plan["sites"][site_ix][6] = self.asm.here() - here0
        row = {
            "name": f.name,
            "entry": self._hot_entry - TEXT_VADDR,
            "frame": self.frame,
            "layout": self._frame_layout(),
            "calls": self._hot_calls,
        }
        if self.hotswap:
            row["tramp"] = self.hot_slots[f.name]
        self.hot_funcs.append(row)
        self.cur = None

    def _frame_layout(self) -> str:
        parts = [f"frame={self.frame}"]
        parts.extend(f"s{o}" for o in sorted(set(self.slots.values())))
        parts.extend(f"a{o}/{z}" for o, z in sorted(self.allocas.values()))
        return hashlib.sha256("\n".join(parts).encode("ascii")).hexdigest()

    def _hot_record(self, target: str) -> None:
        n = self._hot_seq.get(target, 0)
        self._hot_seq[target] = n + 1
        self._hot_calls.append((f"{target}@{n}", self.asm.here() - self._hot_entry))

    def _call_stub_slot(self, skip_label: str) -> None:
        """调 `[rbx]` 指向的函数；空槽与 `-1` 哨兵跳过（`#611`）。

        `.ctors`/`.dtors` 在 GNU 老 ABI 里以 `-1`（表头）或 `0`（表尾）收尾 —— 那些不是
        函数指针。不跳过就会 `call -1`，产物一启动就崩，而根因离现场很远。
        """
        self.asm.emit(b"\x48\x8B\x03")          # mov rax, [rbx]
        self.asm.emit(b"\x48\x85\xC0")          # test rax, rax
        self.asm.jcc("e", skip_label)           # jz skip
        self.asm.emit(b"\x48\x83\xF8\xFF")      # cmp rax, -1
        self.asm.jcc("e", skip_label)           # je skip
        self.asm.emit(b"\xFF\xD0")              # call rax

    def emit_entry_stub(self, start_name: str, tls: bool = False,
                        ctors: int = 0, finis: int = 0, call_exit: bool = False) -> None:
        self.asm.label("__entry")
        if tls:
            # `arch_prctl(ARCH_SET_FS = 0x1002, &__loment_tls)` —— 让 `fs:0x28`
            # （`-fstack-protector` 的金丝雀）落在我们自己的 TLS 块里，而不是地址 `0x28`。
            self.asm.emit(b"\xB8" + struct.pack("<I", 158))       # mov eax, 158 (arch_prctl)
            self.asm.emit(b"\xBF" + struct.pack("<I", 0x1002))    # mov edi, ARCH_SET_FS
            self.asm.mov_abs(RSI, "__loment_tls")                 # movabs rsi, &tls
            self.asm.emit(b"\x0F\x05")                            # syscall
        # **构造函数先跑**（`#611`）：每个表是一串 8 字节函数指针，先后顺序就是表里的顺序。
        # 用 `rbx` 走游标、`r12` 存表尾；两个都是 SysV 的被调方保存寄存器，构造函数不会
        # 偷改它们（改了我们也不依赖 —— 每次循环都从内存里重新取）。
        for j in range(ctors):
            self.asm.mov_abs(RBX, f"__loment_ctor_{j}")
            self.asm.mov_abs(R12, f"__loment_ctor_end_{j}")
            self.asm.label(f"__ctor_loop_{j}")
            self.asm.emit(b"\x4C\x39\xE3")                        # cmp rbx, r12
            self.asm.jcc("ae", f"__ctor_done_{j}")
            self._call_stub_slot(f"__ctor_next_{j}")
            self.asm.label(f"__ctor_next_{j}")
            self.asm.emit(b"\x48\x83\xC3\x08")                    # add rbx, 8
            self.asm.jmp(f"__ctor_loop_{j}")
            self.asm.label(f"__ctor_done_{j}")
        self.asm.call(self._hot_target(start_name))
        # **析构表逆序跑**（`#611`）：C++ 的 `.fini_array` 语义就是"构造的逆序"。
        for j in range(finis):
            self.asm.mov_abs(RBX, f"__loment_fini_end_{j}")
            self.asm.mov_abs(R12, f"__loment_fini_{j}")
            self.asm.label(f"__fini_loop_{j}")
            self.asm.emit(b"\x4C\x39\xE3")                        # cmp rbx, r12
            self.asm.jcc("be", f"__fini_done_{j}")
            self.asm.emit(b"\x48\x83\xEB\x08")                    # sub rbx, 8
            self._call_stub_slot(f"__fini_next_{j}")
            self.asm.label(f"__fini_next_{j}")
            self.asm.jmp(f"__fini_loop_{j}")
            self.asm.label(f"__fini_done_{j}")
        if call_exit:
            # **链了 libc 就走 `exit(3)`**（`#605`）：`syscall(60)` 不刷 stdio、不跑
            # `atexit`，用户 `printf` 的输出就那样丢了 —— 程序还安静地退出 0。
            self.asm.emit(mov_ri32(RDI, 0))
            self.asm.call("exit")
        else:
            # **密封产物保持裸系统调用**：没有 libc 可调，这是唯一正确的出口，
            # 行为与这条规则出现之前完全一样。
            self.asm.emit(mov_ri32(RAX, 60))  # exit(0)
            self.asm.emit(mov_ri32(RDI, 0))
            self.asm.emit(b"\x0F\x05")

    def emit_conscript(self, defined: set[str], externs: set[str] | None = None) -> None:
        """**收编面**（`docs/219` §6 第 1 条）：C 世界里那几个"**可闭合**"的名字，由**我们**提供。

        名字与实现在这里，**表的另一半在 `tools/loment_ports.py`** —— 那边把它判成 `close`，
        这边把它做出来；两处指的是同一批符号（那是 `docs/219` §5.2 条件二："名字的另一端
        在我们手里"）。⚠ 改动这一批时**两边一起改**，否则尺子会报"可闭合"而链接器提供不出。

        **为什么是裸机器码而不是 IR**：调用方是**外部 C 对象**，按 System V 传参
        （`rdi`/`rsi`/`rdx`），而我们自己 IR 定义的函数**实参走栈**。这三个都是叶子函数
        —— 只用 caller-saved 寄存器、不碰栈、不需要序言 —— 所以既不用给发射器加
        "C ABI 定义"那一档（那是 `docs/205` C2 的活），也不需要栈帧。

        三条都**不主张**超出 C 标准的语义：`memcpy` 重叠是 UB（所以前向拷贝就够），
        `memcmp` 只保证符号。

        **优先级**：这几个标签在我们自己的代码**之后、外部对象之前**立起来，而摆对象时的
        规则是"以先来的为准"（`compile_ll` 里那句 `if nm in em.asm.labels: continue`）——
        所以**一个自带 `memcpy` 的库，它的那份会被我们盖掉**。这不是疏忽，是 §5.2 条件二：
        名字的另一端**在我们手里**，"`memcpy` 是什么意思"由我们说了算。

        **内部跳转写成字面字节**（不是 `jcc` + 标签）：这样两侧发的是**同一串字节**，
        自举侧 (`loment/tools/lomelf.lomt` 的 `emit_conscript`) 不必用它的回填机制 ——
        而逐字节一致是 `loment_elf_test` 的自举镜像那格在钉的。位移按下面的布局手算，
        改任何一条指令都要把三处 `rel32`（`0F 84`/`0F 85` 后面那 4 字节）重算。
        """
        a = self.asm
        # memcpy(rdi=d, rsi=s, rdx=n) -> rax=d。**C 的 `memcpy` 返回 `void*`（就是 `d`）** ——
        # 调用方可能拿它（`p = memcpy(...)`），所以原起点必须先存起来：
        # 循环里 `rdi` 会被推到末尾，rax 又被字节搬运占用，于是借 `r8`（SysV 调用者保存、
        # 本叶子里没人碰）。
        # 布局: mov(3) test(3) jz(6)=12 | loop@12(14 字节) … jnz@25(6) | done@31 movret ret
        a.label("memcpy")
        a.emit(b"\x49\x89\xF8"                      # mov r8, rdi
               b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x13\x00\x00\x00"          # jz done   (31 - 12 = +19)
               b"\x8A\x06"                          # mov al, [rsi]
               b"\x88\x07"                          # mov [rdi], al
               b"\x48\xFF\xC6"                      # inc rsi
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xED\xFF\xFF\xFF"          # jnz loop  (12 - 31 = -19)
               b"\x4C\x89\xC0"                      # mov rax, r8
               b"\xC3")                             # ret
        # memset(rdi=p, esi=v, rdx=n) -> rax=p（同一条理由）。
        # 布局: mov(3) test(3) jz(6)=12 | loop@12(9 字节) … jnz@21(6) | done@27 movret ret
        a.label("memset")
        a.emit(b"\x49\x89\xF8"                      # mov r8, rdi
               b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x0F\x00\x00\x00"          # jz done   (27 - 12 = +15)
               b"\x40\x88\x37"                      # mov [rdi], sil
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xF1\xFF\xFF\xFF"          # jnz loop  (12 - 27 = -15)
               b"\x4C\x89\xC0"                      # mov rax, r8
               b"\xC3")                             # ret
        # memcmp(rdi=a, rsi=b, rdx=n) -> eax (符号)。
        # 布局: test(3) jz eq(6)=9 | loop@9 jnz diff@15(6) | jnz loop@30(6) | eq@36 ret | diff@39 ret
        a.label("memcmp")
        a.emit(b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x1B\x00\x00\x00"          # jz eq     (36 - 9  = +27)
               b"\x8A\x07"                          # mov al, [rdi]
               b"\x8A\x0E"                          # mov cl, [rsi]
               b"\x38\xC8"                          # cmp al, cl
               b"\x0F\x85\x12\x00\x00\x00"          # jnz diff  (39 - 21 = +18)
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xC6"                      # inc rsi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xE5\xFF\xFF\xFF"          # jnz loop  (9 - 36  = -27)
               b"\x31\xC0"                          # xor eax, eax
               b"\xC3"                              # ret
               b"\x0F\xB6\xC0"                      # movzx eax, al
               b"\x0F\xB6\xC9"                      # movzx ecx, cl
               b"\x29\xC8"                          # sub eax, ecx
               b"\xC3")                             # ret
        # **栈金丝雀失败了**（`-fstack-protector` 编出来的库会引它）：`ud2` = 立刻 SIGILL。
        # 它**不该被调到** —— 我们的 TLS 块（见 `compile_ll` 的 `__loment_tls`）里那个金丝雀
        # 没人写，入口读它、出口比它，两边相等。真被调到说明栈被踩了，那时**响亮地死**才对。
        a.label("__stack_chk_fail")
        a.emit(b"\x0F\x0B")                         # ud2
        # ---- malloc / free：**内存那一条不收编，是"划掉"**（`docs/219` §2.1）----
        #
        # 它们是一层**换 ABI 的薄壳**，不是一份新分配器：C 按 System V 把实参放在
        # `rdi`，而我们自己的函数**从栈上取实参**（调用方 `push`、`[rbp+16]` 起）。
        # 所以壳做三件事：`push rbp` / `push rdi` / `call`，回来清栈再返回。
        #
        # **只在堆在的时候发**：底下那两条 `@__loment_alloc` / `@__loment_free` 是我们这份
        # IR 里的定义（编译器在程序用到 `alloc`/`free` 时发出）。没有它们就没有"我们的堆"——
        # 那时**不发**，让外部对象的 `malloc` 在链接期响亮地报未定义，而不是发一个悬空调用。
        #
        # **宿主版堆不包壳**（`#631`）：`choose hosted` 的分配器自己 `declare` 了
        # `@malloc` 并向宿主要 arena。若这里再定义 `malloc` 转调 `__loment_alloc`，
        # 那条导入就指回壳自身 —— 无限递归（实测栈溢出 SIGSEGV）。所以 IR 自己
        # `declare` 过的名字不包壳，留给 `.so` 的导出表（自举侧用 `ext_has`，同一条）。
        externs = set(externs or ())
        if "__loment_alloc" in defined and "malloc" not in externs:
            a.label("malloc")
            a.emit(b"\x55\x57")                  # push rbp; push rdi (size)
            a.call("__loment_alloc")
            a.emit(b"\x48\x83\xC4\x08\x5D\xC3")  # add rsp, 8; pop rbp; ret
        if "__loment_free" in defined and "free" not in externs:
            a.label("free")
            a.emit(b"\x55\x57")                  # push rbp; push rdi (ptr)
            a.call("__loment_free")
            a.emit(b"\x48\x83\xC4\x08\x5D\xC3")  # add rsp, 8; pop rbp; ret


# ------------------------------------------------------------------ ELF 写出


def align_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a


#: 节类型 / 段类型（只用得到的几个）。
SHT_NULL, SHT_PROGBITS, SHT_STRTAB, SHT_RELA = 0, 1, 3, 4
SHT_DYNAMIC, SHT_NOBITS, SHT_DYNSYM = 6, 8, 11
PT_GNU_RELRO = 0x6474E552


def _shdr(nameoff, typ, flags, addr, off, size, link=0, info=0, align=1, entsize=0) -> bytes:
    return struct.pack("<IIQQQQIIQQ", nameoff, typ, flags, addr, off, size,
                       link, info, align, entsize)


def _shstrtab(*names: str) -> tuple[bytes, dict[str, int]]:
    """按给定顺序拼 `.shstrtab`，返回 (字节, 名字 -> 偏移)。0 号永远是空串。"""
    out = bytearray(b"\x00")
    at: dict[str, int] = {}
    for nm in names:
        at[nm] = len(out)
        out += nm.encode() + b"\x00"
    return bytes(out), at


def build_elf(text: bytes, data: bytes, bss_size: int, entry: int) -> bytes:
    """静态产物的 ELF：两个 `PT_LOAD` + 一张**节头表**（`#606`）。

    节头表放在**不加载的文件尾部**：执行不需要它，但 `objdump -d` / `readelf -r` /
    `strip` 全都按节头找 `.text`。没有它时 `objdump -d` 只打一行文件格式，用户手上
    没有自己程序的反汇编。几百字节的代价换回整套 binutils。
    """
    text_off = 0x1000
    data_off = align_up(text_off + len(text), 0x1000)
    file_end = data_off + len(data)

    shstr, sname = _shstrtab(".text", ".data", ".bss", ".shstrtab")
    shstr_off = file_end
    shoff = align_up(shstr_off + len(shstr), 8)
    shnum, shstrndx = 5, 4

    ehdr = bytearray()
    ehdr += b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    ehdr += struct.pack("<HHIQQQIHHHHHH", 2, 0x3E, 1, entry, 64, shoff, 0,
                        64, 56, 2, 64, shnum, shstrndx)

    def phdr(flags, off, vaddr, filesz, memsz):
        return struct.pack("<IIQQQQQQ", 1, flags, off, vaddr, vaddr, filesz, memsz, 0x1000)

    ph_text = phdr(5, text_off, TEXT_VADDR, len(text), len(text))
    ph_data = phdr(6, data_off, DATA_VADDR, len(data), len(data) + bss_size)

    out = bytearray(ehdr + ph_text + ph_data)
    out += b"\x00" * (text_off - len(out))
    out += text
    out += b"\x00" * (data_off - len(out))
    out += data
    assert len(out) == file_end, (len(out), file_end)
    out += shstr
    out += b"\x00" * (shoff - len(out))
    out += _shdr(0, SHT_NULL, 0, 0, 0, 0, align=0)
    out += _shdr(sname[".text"], SHT_PROGBITS, 0x6, TEXT_VADDR, text_off,
                 len(text), align=16)
    out += _shdr(sname[".data"], SHT_PROGBITS, 0x3, DATA_VADDR, data_off,
                 len(data), align=16)
    out += _shdr(sname[".bss"], SHT_NOBITS, 0x3, DATA_VADDR + len(data),
                 data_off + len(data), bss_size, align=8)
    out += _shdr(sname[".shstrtab"], SHT_STRTAB, 0, 0, shstr_off, len(shstr))
    return bytes(out)


#: Linux x86-64 的系统 loader。`PT_INTERP` 指的就是它 —— **由它**把 `.so` 映射进来、
#: 按名字解析、写 GOT。我们只负责把"要什么"写清楚（`docs/173` 阶段 3 / `docs/183` V2）。
ELF_INTERP = b"/lib64/ld-linux-x86-64.so.2\x00"

#: **loader 的候选落点**（`#604`）。原先那条路径是写死的 glibc 路径：在 musl 发行版
#: （Alpine 等）上链接照样成功，`execve` 却 `No such file or directory` —— 一句话都没有。
#: 顺序是"经典 glibc 优先，再补救", 第一个在**链接机上**存在的胜出。
#: Windows 上交叉链接时这些路径没有意义（目标不是本机），退回经典路径；要指定就
#: `--interp PATH` 或环境变量 `LOMENT_INTERP`。
_INTERP_CANDIDATES = (
    "/lib64/ld-linux-x86-64.so.2",
    "/lib/ld-linux-x86-64.so.2",
    "/usr/lib/ld-linux-x86-64.so.2",
    "/lib/ld-musl-x86_64.so.1",
)


def resolve_interp(override: str | None = None) -> bytes:
    """选 `PT_INTERP` 的那条路径（`#604`）。返回带结尾 NUL 的字节串。

    优先级：显式参数 `--interp` -> `LOMENT_INTERP` -> 链接机上的候选 -> 经典路径。
    **在 Linux 上找不到任何候选就点名报错**，不把一个必然启动不了的产物交给用户；
    非 Linux 主机的交叉链接没有"本机 loader"可言，保留经典路径（用 `--interp` 覆盖）。
    """
    if override:
        return override.encode() + b"\x00"
    env = os.environ.get("LOMENT_INTERP")
    if env:
        return env.encode() + b"\x00"
    if sys.platform.startswith("linux"):
        for cand in _INTERP_CANDIDATES:
            if Path(cand).exists():
                return cand.encode() + b"\x00"
        raise Unsupported(
            "找不到程序解释器（PT_INTERP）：试过 " + ", ".join(_INTERP_CANDIDATES)
            + " —— 用 `--interp PATH` 指定目标系统的 loader（`docs/183` §4）")
    # 非 Linux 主机（Windows / macOS）是在**交叉链接**：本机路径与目标无关，
    # 保留经典 glibc 路径，要换就 `--interp`。
    return ELF_INTERP

#: `DT_*` 号（`docs/183` V2）。只取这一版用得到的。
DT_NEEDED, DT_PLTGOT, DT_HASH, DT_STRTAB, DT_SYMTAB = 1, 3, 4, 5, 6
DT_STRSZ, DT_SYMENT, DT_PLTREL, DT_JMPREL, DT_PLTRELSZ = 10, 11, 20, 23, 2
#: `DT_RELA`/`DT_RELASZ`/`DT_RELAENT` —— 这一版没有 `.rela.dyn`（非 PIE，代码里的地址全是
#: 链接期定死的），但 **`DT_RELAENT` 必须发**：缺了它 glibc 会跳过**整个** PLT 重定位表
#: （`readelf` 的原话是 `missing or corrupt dynamic tag: DT_RELAENT`），GOT 全是 0，
#: 第一次走 PLT 就跳 NULL —— 实测就是这么崩的（`si_addr = NULL`）。
DT_RELA, DT_RELASZ, DT_RELAENT = 7, 8, 9
DT_DEBUG = 21
DT_GNU_HASH = 0x6FFFFEF5
DT_FLAGS = 30              # DF_BIND_NOW = 8：GOT 在加载时一次填满，不走惰性绑定
DF_BIND_NOW = 8
PT_LOAD, PT_DYNAMIC, PT_INTERP, PT_GNU_STACK = 1, 2, 3, 0x6474E551


def build_elf_dynamic(text: bytes, data: bytes, bss_size: int, entry: int, *,
                      tail: bytes, interp_vaddr: int, interp_len: int,
                      dyn_vaddr: int, dyn_len: int, tail_vaddr: int,
                      dynstr_vaddr: int, dynstr_len: int, dynsym_vaddr: int,
                      nsyms: int, rela_vaddr: int, rela_len: int,
                      got_vaddr: int, got_len: int) -> bytes:
    """产出一个**动态链接**的 ELF 可执行文件（`docs/183` V2）。

    与 `build_elf` 的差别是"多了几条程序头、多了一段、文件头那一页也在 LOAD 里"：

    * `PT_INTERP` 指向 `.interp` 字符串（**文件偏移** —— 内核按偏移读它，不看 vaddr）；
      vaddr 仍要在某个 `PT_LOAD` 里，因为段是整块映射的；
    * `PT_DYNAMIC` 指向 `.dynamic`（在尾部那一小段里，可写 —— loader 会往里写 `DT_DEBUG`）；
    * 尾部段（`.got.plt` + `.dynamic`）**单独一条 RW `PT_LOAD`**，落在 BSS 之后
      （理由见 `build_dynamic_tables`）。

    **文件头那一页单开一条只读 LOAD**（`#608`）：原先它和代码同一条 R|X 段，
    于是 ELF 头与程序头表落在一个**可执行**页里。gcc 的产物也是把这一页单独放成 R。
    第一个 `PT_LOAD` 仍从文件偏移 0 起 —— `AT_PHDR` 靠的就是
    `首个 PT_LOAD 的 p_vaddr - p_offset`。

    **`PT_GNU_RELRO`**（`#608`）：尾部段的 `.got.plt` / `.dynamic` 在加载器做完重定位后
    必须变成只读。`DF_BIND_NOW` 已经发了，所以这些槽之后不会再被写；把整段罩进 RELRO
    是安全的。GOT 可写是漏洞链的第一个原语，而这个产物本来就完全有资格被利用。

    再补一条 `PT_GNU_STACK`（RW、不可执行）：没有它，旧内核会给一个可执行栈并告警。
    """
    text_off = 0x1000
    data_off = align_up(text_off + len(text), 0x1000)
    tail_off = align_up(data_off + len(data), 0x1000)
    tail_len = (dyn_vaddr - tail_vaddr) + dyn_len

    def phdr(typ, flags, off, vaddr, filesz, memsz, align=0x1000):
        return struct.pack("<IIQQQQQQ", typ, flags, off, vaddr, vaddr, filesz, memsz, align)

    # **第一个 `PT_LOAD` 必须从文件偏移 0 起**：内核用
    # `AT_PHDR = (首个 PT_LOAD 的 p_vaddr - p_offset) + e_phoff` 告诉 loader
    # 程序头在哪。若这一页不进任何 LOAD，`AT_PHDR` 就落在未映射的地址上，
    # loader 连一行 `LD_DEBUG` 都来不及打就没了。
    # 这一页只放 ELF 头 + 程序头，所以**只要 R**；代码从下一页起单独 R|X。
    phs = (
        phdr(PT_INTERP, 4, text_off + (interp_vaddr - TEXT_VADDR), interp_vaddr,
             interp_len, interp_len, 1)
        + phdr(PT_LOAD, 4, 0, TEXT_VADDR - text_off, text_off, text_off)
        + phdr(PT_LOAD, 5, text_off, TEXT_VADDR, len(text), len(text))
        + phdr(PT_LOAD, 6, data_off, DATA_VADDR, len(data), len(data) + bss_size)
        + phdr(PT_LOAD, 6, tail_off, tail_vaddr, tail_len, tail_len)
        + phdr(PT_DYNAMIC, 6, tail_off + (dyn_vaddr - tail_vaddr), dyn_vaddr, dyn_len, dyn_len)
        + phdr(PT_GNU_RELRO, 4, tail_off, tail_vaddr, tail_len, tail_len, 1)
        + phdr(PT_GNU_STACK, 6, 0, 0, 0, 0, 0x10)
    )
    shstr, sname = _shstrtab(".text", ".data", ".bss", ".interp", ".dynstr",
                             ".dynsym", ".rela.plt", ".got.plt", ".dynamic", ".shstrtab")
    shstr_off = tail_off + tail_len
    shoff = align_up(shstr_off + len(shstr), 8)
    shnum, shstrndx = 11, 10

    ehdr = bytearray()
    ehdr += b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    ehdr += struct.pack("<HHIQQQIHHHHHH", 2, 0x3E, 1, entry, 64, shoff, 0,
                        64, 56, 8, 64, shnum, shstrndx)

    out = bytearray(ehdr + phs)
    out += b"\x00" * (text_off - len(out))
    out += text
    out += b"\x00" * (data_off - len(out))
    out += data
    out += b"\x00" * (tail_off - len(out))
    out += tail
    assert len(out) == tail_off + tail_len, (len(out), tail_off + tail_len)
    out += shstr
    out += b"\x00" * (shoff - len(out))
    out += _shdr(0, SHT_NULL, 0, 0, 0, 0, align=0)
    out += _shdr(sname[".text"], SHT_PROGBITS, 0x6, TEXT_VADDR, text_off, len(text), align=16)
    out += _shdr(sname[".data"], SHT_PROGBITS, 0x3, DATA_VADDR, data_off, len(data), align=16)
    out += _shdr(sname[".bss"], SHT_NOBITS, 0x3, DATA_VADDR + len(data),
                 data_off + len(data), bss_size, align=8)
    out += _shdr(sname[".interp"], SHT_PROGBITS, 0x2, interp_vaddr,
                 text_off + (interp_vaddr - TEXT_VADDR), interp_len)
    out += _shdr(sname[".dynstr"], SHT_STRTAB, 0x2, dynstr_vaddr,
                 text_off + (dynstr_vaddr - TEXT_VADDR), dynstr_len)
    out += _shdr(sname[".dynsym"], SHT_DYNSYM, 0x2, dynsym_vaddr,
                 text_off + (dynsym_vaddr - TEXT_VADDR), 24 * nsyms,
                 link=5, info=1, align=8, entsize=24)
    out += _shdr(sname[".rela.plt"], SHT_RELA, 0x2, rela_vaddr,
                 text_off + (rela_vaddr - TEXT_VADDR), rela_len,
                 link=6, info=8, align=8, entsize=24)
    out += _shdr(sname[".got.plt"], SHT_PROGBITS, 0x3, got_vaddr,
                 tail_off + (got_vaddr - tail_vaddr), got_len, align=8)
    out += _shdr(sname[".dynamic"], SHT_DYNAMIC, 0x3, dyn_vaddr,
                 tail_off + (dyn_vaddr - tail_vaddr), dyn_len,
                 link=5, align=8, entsize=16)
    out += _shdr(sname[".shstrtab"], SHT_STRTAB, 0, 0, shstr_off, len(shstr))
    return bytes(out)


def build_dynamic_tables(base_text_vaddr: int, text_len: int, tail_vaddr: int,
                         imports: list[str], sonames: list[str],
                         interp: bytes = ELF_INTERP):
    """把动态链接要的那几张表算出来：**PLT / .dynstr / .dynsym / .hash / .rela.plt**
    （接在代码后面）与 **.got.plt / .dynamic**（单独一段，接在 BSS 之后）。

    返回 `(text_tail, data_tail, labels, info)`：

    * `text_tail` **必须恰好接在** `text_len` 后面，`data_tail` 从 `tail_vaddr` 起；
    * `labels`：导入名 -> **PLT 桩的 vaddr**（调用点要指到桩上，不是库函数）；
    * `info`：`.interp` / `.dynamic` / 尾部段 的 vaddr 与长度，喂给 `build_elf_dynamic`。

    **`.got.plt` 不能接在 `data` 后面**：紧接着 `data` 的是 **BSS**，而 BSS 里放着运行时
    自己的全局（FFI 的 arena 就是 1 MiB）。GOT 摆在那里，程序第一次往全局里写就把
    GOT 覆盖了 —— 症状是 `jmp *GOT[i]` 跳进垃圾地址（实测 GOT 里是被覆盖成了输入数据）。
    所以这一段单开一条 `PT_LOAD`，落在 `DATA_VADDR + len(data) + bss` 之后。

    **`DF_BIND_NOW`**：GOT 在加载时一次填满。惰性绑定那条路（`push idx; jmp PLT0`）照样发，
    但永远不会走到 —— 少一条"运行到一半才解析"的路径。
    """
    text_base = base_text_vaddr + text_len
    plt_at = align_up(text_base, 16)
    n = len(imports)
    text_tail = bytearray(b"\x00" * (plt_at - text_base))

    got_at = tail_vaddr
    got_n = 3 + n
    got = bytearray(8 * got_n)

    interp_at = plt_at + 16 * (n + 1)
    dynstr_at = interp_at + len(interp)
    ds = bytearray(b"\x00")                     # 0 号必须是空串（st_name == 0 是"没有名字"）
    name_off: dict[str, int] = {}
    # 导入名在前、SONAME 在后；多个共享对象按 `--link` 顺序各占一个 `DT_NEEDED`（`#596`）。
    for nm in list(imports) + list(sonames):
        name_off[nm] = len(ds)
        ds += nm.encode() + b"\x00"
    ds += b"\x00"
    dynsym_at = align_up(dynstr_at + len(ds), 8)
    dynsym = bytearray(24)                       # 0 号是保留的空符号
    for nm in imports:
        # STB_GLOBAL(1)<<4 | STT_FUNC(2)；st_shndx = 0（未定义 —— 由 loader 解析）
        dynsym += struct.pack("<IBBHQQ", name_off[nm], (1 << 4) | 2, 0, 0, 0, 0)
    hash_at = align_up(dynsym_at + len(dynsym), 8)
    nbucket, nchain = 1, 1 + n
    h = bytearray(struct.pack("<II", nbucket, nchain))
    h += struct.pack("<I", 0)                    # buckets：我们没有要导出的符号
    h += struct.pack("<" + "I" * nchain, *([0] * nchain))   # chains：全是 0（链尾）
    hashb = bytes(h)
    # **GNU hash**（`DT_GNU_HASH`）：这一版的产物**不导出任何符号**（只有一个空符号 +
    # 若干未定义的导入），所以表是退化的：`symoffset = nsyms`（没有一个符号落在桶链里）、
    # 一张 1 格的 bloom、一个全 0 的桶、空链。发它的理由不是"好看"：现代 glibc 编出来的
    # 每个对象都有它，而只有 SysV `.hash` 的对象在 `do_lookup_x` 那里走的是另一条老路 ——
    # 与一个**能跑**的对照件（gcc 出的）逐项比过之后，这是剩下唯一的结构差别。
    gnu_at = align_up(hash_at + len(hashb), 8)
    nsyms = 1 + n
    gnu = bytearray(struct.pack("<IIII", 1, nsyms, 1, 6))   # nbuckets, symoffset, bloom_size, shift
    gnu += struct.pack("<Q", 0)                              # bloom
    gnu += struct.pack("<I", 0)                              # buckets[0]
    gnub = bytes(gnu)
    rela_at = align_up(gnu_at + len(gnub), 8)
    rela = bytearray()
    for i, nm in enumerate(imports):
        # `r_info = (符号号 << 32) | 类型` —— 类型是 **R_X86_64_JUMP_SLOT = 7**（把解析到的
        # 函数地址写进 GOT[i+3]，由 loader 做）。第一版把符号号塞进了低 32 位，于是 readelf
        # 读成 `R_X86_64_64`/`R_X86_64_PC32`，glibc 照那个错类型去处理，GOT 仍是 0。
        rela += struct.pack("<QQq", got_at + 8 * (i + 3), ((1 + i) << 32) | 7, 0)

    # ---- PLT：16 字节一格。PLT0 + 每个导入一格。
    #
    # ⚠ x86 的 `rip` 相对位移是**相对指令末尾**算的，不是相对指令开头 —— 按开头算会差 4 字节，
    # 读到的 GOT 槽是错的那个，桩一跳就撞 NULL（V2 第一版实测：`si_addr = NULL`）。
    # 所以这里每个 `_rel32(指令起点, 指令长度, 目标)` 都把长度也传进去。
    def _rel32(at: int, size: int, target: int) -> bytes:
        return struct.pack("<i", target - (at + size))

    plt = bytearray()
    p0 = plt_at
    plt += b"\xff\x35" + _rel32(p0, 6, got_at + 8)           # push [GOT+8]  (link_map)
    plt += b"\xff\x25" + _rel32(p0 + 6, 6, got_at + 16)      # jmp  [GOT+16] (resolver)
    plt += b"\x0f\x1f\x40\x00"                               # nop
    labels = {}
    for i, nm in enumerate(imports):
        at = plt_at + 16 * (i + 1)
        labels[nm] = at
        plt += b"\xff\x25" + _rel32(at, 6, got_at + 8 * (i + 3))     # jmp *GOT[i+3]
        plt += b"\x68" + struct.pack("<I", i)                        # push i
        plt += b"\xe9" + _rel32(at + 11, 5, p0)                      # jmp PLT0
        got[8 * (i + 3):8 * (i + 4)] = struct.pack("<Q", at + 6)     # 惰性那条路的落点

    # ---- `.dynamic`：放在 `.got.plt` 之后（同在数据段，可写）。
    dyn_at = got_at + len(got)
    entries = [
        *((DT_NEEDED, name_off[so]) for so in sonames),
        (DT_HASH, hash_at),
        (DT_GNU_HASH, gnu_at),
        (DT_STRTAB, dynstr_at),
        (DT_STRSZ, len(ds)),
        (DT_SYMTAB, dynsym_at),
        (DT_SYMENT, 24),
        (DT_PLTGOT, got_at),
        (DT_JMPREL, rela_at),
        (DT_PLTRELSZ, len(rela)),
        (DT_PLTREL, 7),                           # 7 = DT_RELA
        # **不发 `DT_RELA` / `DT_RELASZ`**：我们的代码是位置相关的，一条非 PLT 重定位
        # 都没有。发了它（哪怕 `RELASZ=0`）等于向 loader 声明"这里有一张静态重定位表"，
        # 而那张表的位置只是 `.rela.plt` 的别名 —— "空表"与"没有这张表"不是一回事。
        (DT_FLAGS, DF_BIND_NOW),
        # `DT_DEBUG`（`#607`）：loader 会往这里写 `r_debug`，调试器靠它走已加载对象链。
        # 非 PIE 产物没有别的通道，缺了它 `gdb` 的 `info sharedlibrary` 就是空的。
        (DT_DEBUG, 0),
        (0, 0),
    ]
    dynb = b"".join(struct.pack("<QQ", t, v) for t, v in entries)
    got[0:8] = struct.pack("<Q", dyn_at)          # GOT[0] = &_DYNAMIC（惯例）

    tail = bytearray()
    tail += b"\x00" * (interp_at - (plt_at + len(plt)))   # 对齐填充（PLT 之后到 .interp）
    tail += interp
    tail += ds
    tail += b"\x00" * (dynsym_at - (dynstr_at + len(ds)))
    tail += dynsym
    tail += b"\x00" * (hash_at - (dynsym_at + len(dynsym)))
    tail += hashb
    tail += b"\x00" * (gnu_at - (hash_at + len(hashb)))
    tail += gnub
    tail += b"\x00" * (rela_at - (gnu_at + len(gnub)))
    tail += rela
    text_tail += plt
    text_tail += tail

    data_tail = bytearray(got)
    data_tail += dynb
    info = {"interp_vaddr": interp_at, "interp_len": len(interp),
            "dyn_vaddr": dyn_at, "dyn_len": len(dynb), "tail_vaddr": tail_vaddr,
            "dynstr_vaddr": dynstr_at, "dynstr_len": len(ds),
            "dynsym_vaddr": dynsym_at, "nsyms": nsyms,
            "rela_vaddr": rela_at, "rela_len": len(rela),
            "got_vaddr": got_at, "got_len": len(got)}
    return bytes(text_tail), bytes(data_tail), labels, info


# ------------------------------------------------------------------ PE 目标 (Windows, x86-64)
#
# 同一份 IR 的第二个目标：PE64 控制台程序。为了**去掉 WSL** —— 原来的 Windows 路径要
# 把 ELF 丢进 WSL 跑，PE 产物在 Windows 上原生就能跑。
#
# 与 ELF 目标的差别（逐条记账，别当成"等价"）：
#   * x64 Windows 没有 `syscall` 指令，所以每个内联汇编 syscall 都改发 `call __win_syscall`，
#     由 shim 按 syscall 号派发到 kernel32。**syscall 面只收敛在这一处**。
#   * shim 自己做栈对齐（`and rsp,-16`）—— Loment 自己的帧不保证 16 字节对齐，
#     而被调方（Windows API）里的 `movaps` 会直接 #GP。
#   * 实现了 29 个号，**其余号返回 `-ENOSYS`（-38）**。第一批 8 个是文件与进程：
#     `exit`(60) `write`(1) `read`(0) `close`(3) `openat`(257) `brk`(12)
#     `getdents64`(217) `newfstatat`(262)。第二批 13 个是**联网**（2026-10-10，docs/217）：
#     `socket`(41) `connect`(42) `accept`(43) `accept4`(288) `sendto`(44) `recvfrom`(45)
#     `shutdown`(48) `bind`(49) `listen`(50) `getsockname`(51) `getpeername`(52)
#     `setsockopt`(54) `getsockopt`(55) —— 由 `ws2_32.dll` 顶上，socket 型 fd 在
#     read/write/close 上分流到 recv/send/closesocket。第三批 1 个是 `poll`(7)。
#     第四批 7 个（2026-10-12，docs/231 与 docs/234）：`select`(23) `sendmsg`(46)
#     `recvmsg`(47) `socketpair`(53) `epoll_wait`(232) `epoll_ctl`(233)
#     `epoll_create1`(291) —— 到这儿联网面上**再不剩"点名不做"的号**。
#     写要跨平台跑的程序时按这 29 个来 —— 别处用了别的号, 在 PE 上是 `-ENOSYS`
#     （2026-10-11 从 `-1` 改过来的：`-1` 是 `EPERM`，拿"权限不够"说"没实现"是假话，
#     而 Linux 对不认得的号就是返回 `-ENOSYS` —— 见 `PE_ENOSYS`）。
#   * 联网那批是**语义仿真**而不是转发：负 errno、`AF_INET6`=10（Windows 是 23）、
#     `SOL_SOCKET` 与 `SO_*` 的号、`struct timeval` 的超时（Windows 只认毫秒）、
#     `MSG_NOSIGNAL`（Windows 没有这一位）都得翻译。派发面的真话是 `PE_DISPATCH`
#     那张表，判据把它与冻出来的 blob 逐条对齐。
#   * Windows 没有 procfs，argv 靠 shim 从 `GetCommandLineA` 合成 `/proc/self/cmdline`
#     （空格分隔转 NUL 分隔, 并合并连续分隔符）。

PE_IMAGE_BASE = 0x140000000
PE_SEC_ALIGN, PE_FILE_ALIGN = 0x1000, 0x200
# **四个节都钉在固定 RVA**（不是算出来的）。这样 shim 里对 IAT 与静态状态的取址全是编译期
# 常量 —— 整段 shim 与布局**完全无关**，可以原样冻结成一段 blob 交给自举镜像，那边一个回填
# 都不用做。顺带也干掉了"按代码长度重排 .data"的不动点循环。
PE_TEXT_RVA = 0x1000             # 代码（可增长，别超 16 MiB）
PE_IDATA_RVA = 0x1000000         # 导入表 + "/proc/self/cmdline" 字面量（有文件内容）
PE_DATA_RVA = 0x2000000          # 全局变量（可增长，别超 16 MiB）
PE_STATE_RVA = 0x3000000         # shim 的静态状态（纯 bss，不占文件）
PE_STATE_VA = PE_IMAGE_BASE + PE_STATE_RVA
PE_LIT_OFF = 0x1000              # 字面量在 .idata 内的偏移
PE_LIT_VA = PE_IMAGE_BASE + PE_IDATA_RVA + PE_LIT_OFF
PE_CMDLINE = b"/proc/self/cmdline\x00"
#: `uname(63)` 的六个字段，各 65 字节（`struct utsname` 的形状）。**合成 `Linux`**：
#: guest 的身份由 guest ABI 决定，与宿主无关（`docs/243` §3.4、`docs/311` §2 的 S 类）。
#: 放在 `.idata` 的固定偏移上，所以 shim 里的取址是编译期常量。
PE_UNAME = b"".join(f[:64].ljust(65, b"\x00")
                    for f in (b"Linux", b"loment", b"0.1.5", b"#1 LVM T0",
                              b"x86_64", b"(none)"))
PE_UNAME_OFF = PE_LIT_OFF + len(PE_CMDLINE)
PE_UNAME_VA = PE_IMAGE_BASE + PE_IDATA_RVA + PE_UNAME_OFF
PE_IMPORTS = ["ExitProcess", "GetStdHandle", "ReadFile", "WriteFile", "CloseHandle",
              "CreateFileA", "GetFileAttributesA", "VirtualAlloc", "GetCommandLineA",
              "FindFirstFileA", "FindNextFileA", "Sleep",
              # 系统与进程信息（`docs/303` §2.3/§2.6）：全部平 C API、无回调。
              "GlobalMemoryStatusEx", "OpenProcess", "CreateToolhelp32Snapshot",
              "Process32First", "Process32Next", "Thread32First", "Thread32Next",
              "OpenThread", "GetProcessIoCounters", "GetProcessHandleCount",
              "GetSystemTimes", "GetTickCount64", "QueryPerformanceCounter",
              "GetSystemTimeAsFileTime",
              # P0 时间/进程身份（`docs/311` §4.1）：`getpid` 与 `clock_gettime` 的单调钟。
              "GetCurrentProcessId", "QueryPerformanceFrequency",
              # 文件/内存/管道一批（`docs/311` P0 之后的同一条线）。
              "SetFilePointerEx", "DeleteFileA", "CreatePipe", "DuplicateHandle",
              "VirtualProtect", "VirtualFree", "CreateFileMappingA", "MapViewOfFile",
              "UnmapViewOfFile", "GetLastError",
              "GetCurrentThreadId", "GetCurrentDirectoryA", "SetCurrentDirectoryA",
              "MoveFileExA", "CreateDirectoryA", "RemoveDirectoryA", "FlushFileBuffers",
              "SetEndOfFile", "FlushViewOfFile",
              "GetFileAttributesExA", "GetFileInformationByHandle",
              # 链接 / 锁 / 属性 / 预分配一批。
              "CreateHardLinkA", "CreateSymbolicLinkA", "GetFinalPathNameByHandleA",
              "LockFileEx", "UnlockFileEx", "SetFileTime", "GetFileSizeEx",
              # 等待唤醒 / 亲和 / 控制台（futex、sched_getaffinity、ioctl）。
              "GetProcessAffinityMask", "GetConsoleScreenBufferInfo"]
# 第二个导入 DLL：联网。名字与上一组无一重名，所以 `slots` 仍是一张按函数名索引的平表。
PE_IMPORTS_WS2 = ["WSAStartup", "socket", "connect", "bind", "listen", "accept",
                  "send", "recv", "sendto", "recvfrom", "closesocket", "shutdown",
                  "setsockopt", "getsockopt", "getpeername", "getsockname",
                  "ioctlsocket", "select", "WSAGetLastError"]
# 第三个导入 DLL：性能计数器（PDH）。它是平 C API —— 无 COM、无回调 —— 所以一张
# 导入表就能解开 CPU/GPU/磁盘/网络/温度九类计数器（`docs/303` §2）。
# 只用 A 版：本语言的字符串是字节，没有 UTF-16。
PE_IMPORTS_PDH = ["PdhOpenQueryA", "PdhAddEnglishCounterA", "PdhCollectQueryData",
                  "PdhGetFormattedCounterValue", "PdhCloseQuery"]
# 第四个导入 DLL：进程与系统信息（psapi）。
PE_IMPORTS_PSAPI = ["EnumProcesses", "GetProcessMemoryInfo", "GetPerformanceInfo"]
# 第五个导入 DLL：随机数（`getrandom` 的等价物 RtlGenRandom）。
PE_IMPORTS_ADVAPI = ["SystemFunction036"]
# 第六个导入 DLL：等待/唤醒原语。**kernel32 不导出它们**（实测 Win11 的导出表里没有），
# 它们只由 api-ms-win-core-synch-l1-2-0 这个 apiset 提供；把 DLL 名写成 apiset 是唯一
# 能按名字取到的写法（写成 kernel32 会让加载器直接拒收，症状是进程起不来、退出码 127）。
PE_IMPORTS_SYNCH = ["WaitOnAddress", "WakeByAddressAll", "WakeByAddressSingle"]
# 描述符表 = 每个 DLL 一条 + 一条全零终止项（少了终止项加载器会把 ILT 当第二条描述符）。
PE_DLLS = [("kernel32.dll", PE_IMPORTS), ("ws2_32.dll", PE_IMPORTS_WS2),
           ("pdh.dll", PE_IMPORTS_PDH), ("psapi.dll", PE_IMPORTS_PSAPI),
           ("advapi32.dll", PE_IMPORTS_ADVAPI),
           ("api-ms-win-core-synch-l1-2-0.dll", PE_IMPORTS_SYNCH)]

#: 通用 Win32 调用器（号 4097）的函数表。号 → 导入名；顺序就是 `__ws_win32` 里那条
#: 比较链的顺序。一条 (号, 名字) 只花 ~15 字节机器码：共享的四参数装载在前，各分支只
#: 剩 `mov rax,[iat]; call rax`。**加一个函数 = 一行导入 + 这里一行**。
WIN32_CALLS = (
    (1, "GlobalMemoryStatusEx"),
    (2, "GetPerformanceInfo"),
    (3, "EnumProcesses"),
    (4, "OpenProcess"),
    (5, "GetProcessMemoryInfo"),
    (6, "CloseHandle"),
    (7, "CreateToolhelp32Snapshot"),
    (8, "Process32First"),
    (9, "Process32Next"),
    (10, "GetProcessIoCounters"),
    (11, "GetProcessHandleCount"),
    (12, "GetSystemTimes"),
    (13, "OpenThread"),
    (14, "Thread32First"),
    (15, "Thread32Next"),
    (16, "GetTickCount64"),
    (17, "QueryPerformanceCounter"),
    (18, "GetSystemTimeAsFileTime"),
)
PE_IMPORT_DESC_BYTES = 20 * (len(PE_DLLS) + 1)
PE_STD_INPUT, PE_STD_OUTPUT, PE_STD_ERROR = -10, -11, -12
SYS_READ, SYS_WRITE, SYS_CLOSE, SYS_BRK, SYS_EXIT = 0, 1, 3, 12, 60
SYS_GETPID, SYS_UNAME, SYS_TIME, SYS_CLOCK_GETTIME = 39, 63, 201, 228
SYS_GETDENTS64, SYS_OPENAT, SYS_NEWFSTATAT = 217, 257, 262
SYS_POLL, SYS_SELECT = 7, 23
SYS_SOCKET, SYS_CONNECT, SYS_ACCEPT, SYS_ACCEPT4 = 41, 42, 43, 288
SYS_SENDTO, SYS_RECVFROM, SYS_SHUTDOWN = 44, 45, 48
SYS_BIND, SYS_LISTEN, SYS_GETSOCKNAME, SYS_GETPEERNAME = 49, 50, 51, 52
SYS_SETSOCKOPT, SYS_GETSOCKOPT = 54, 55
SYS_SENDMSG, SYS_RECVMSG, SYS_SOCKETPAIR = 46, 47, 53
SYS_EPOLL_WAIT, SYS_EPOLL_CTL, SYS_EPOLL_CREATE1 = 232, 233, 291
#: P0（`docs/311` §4.1）：时间与进程身份。ELF 上由内核给，PE 上由 shim 合成/转发。
SYS_GETPID, SYS_UNAME, SYS_TIME, SYS_CLOCK_GETTIME = 39, 63, 201, 228
#: 文件/内存/管道一批：hotswap、`lib/proc`、`lomelf.lomt` 用到的号。
SYS_OPEN, SYS_LSEEK, SYS_UNLINK, SYS_DUP2, SYS_PIPE2 = 2, 8, 87, 33, 293
SYS_MMAP, SYS_MPROTECT, SYS_MUNMAP = 9, 10, 11
#: 目录/属性/时间/身份一批。
SYS_RENAME, SYS_MKDIR, SYS_RMDIR, SYS_GETCWD, SYS_CHDIR, SYS_ACCESS = 82, 83, 84, 79, 80, 21
SYS_FSYNC, SYS_FDATASYNC, SYS_FTRUNCATE = 74, 75, 77
SYS_GETTIMEOFDAY, SYS_CLOCK_GETRES, SYS_NANOSLEEP = 96, 229, 35
SYS_GETUID, SYS_GETGID, SYS_GETEUID, SYS_GETEGID = 102, 104, 107, 108
SYS_GETPPID, SYS_GETTID, SYS_SCHED_YIELD = 110, 186, 24
#: 别名与 at 变体一批。
SYS_PIPE, SYS_DUP, SYS_DUP3, SYS_EXIT_GROUP = 22, 32, 292, 231
SYS_CLOCK_NANOSLEEP, SYS_UNLINKAT, SYS_MKDIRAT, SYS_RENAMEAT, SYS_FACCESSAT = 230, 263, 258, 264, 269
SYS_TRUNCATE, SYS_CHMOD, SYS_UMASK, SYS_MSYNC, SYS_MADVISE = 76, 90, 95, 26, 28
SYS_STAT, SYS_FSTAT, SYS_LSTAT, SYS_STATX = 4, 5, 6, 332
#: 位置读写与散列/聚集一批。
SYS_PREAD64, SYS_PWRITE64, SYS_READV, SYS_WRITEV = 17, 18, 19, 20
#: 链接 / 锁 / 属性 / 预分配一批。所有权类（chown 族）在 Windows 上没有对应物，
#: 按"存在即成功"处理 —— 与 `chmod` 同一条口径；`fchmod` 也一样。
SYS_FLOCK, SYS_LINK, SYS_SYMLINK, SYS_READLINK = 73, 86, 88, 89
SYS_FCHMOD, SYS_FCHMODAT, SYS_CHOWN, SYS_FCHOWN, SYS_LCHOWN = 91, 268, 92, 93, 94
SYS_UTIME, SYS_UTIMES, SYS_UTIMENSAT = 132, 235, 280
SYS_FALLOCATE, SYS_FADVISE64, SYS_SENDFILE = 285, 221, 40
SYS_SYNC, SYS_SYNCFS, SYS_SYNC_FILE_RANGE = 162, 306, 277
#: 内存重映射 / CPU 亲和 / 等待唤醒 / 随机数 / 终端与 fd 控制一批。
SYS_MREMAP = 25
SYS_SCHED_GETAFFINITY, SYS_SCHED_SETAFFINITY = 204, 203
SYS_FUTEX, SYS_GETRANDOM = 202, 318
SYS_IOCTL, SYS_FCNTL = 16, 72
#: 同义号：带 sigmask 的等待/选择、带 flags 的 fchmodat。
SYS_PSELECT6, SYS_PPOLL, SYS_EPOLL_PWAIT, SYS_EPOLL_PWAIT2 = 270, 271, 281, 441
SYS_FCHMODAT2 = 452
SYS_RENAMEAT2 = 316
#: 还能等价落到已有处理器上的同义号（at 变体 / 旧拼法 / 无状态查询）。
SYS_CREAT, SYS_EPOLL_CREATE, SYS_SET_TID_ADDRESS = 85, 213, 218
SYS_IOPRIO_SET, SYS_IOPRIO_GET, SYS_GETCPU = 251, 252, 309
SYS_MKNOD, SYS_MKNODAT, SYS_FCHOWNAT, SYS_FUTIMESAT = 133, 259, 260, 261
SYS_LINKAT, SYS_SYMLINKAT, SYS_READLINKAT = 265, 266, 267
SYS_CLOSE_RANGE, SYS_OPENAT2, SYS_FACCESSAT2 = 436, 437, 439
#: 进程身份/组类：Windows 没有 POSIX 凭据模型，按既有"绝对身份"口径落地
#: （`getuid` 族已经这样做了）。设置类一律成功；查询类返回固定值。
SYS_SETUID, SYS_SETGID = 105, 106
SYS_SETPGID, SYS_GETPGRP, SYS_SETSID = 109, 111, 112
SYS_SETREUID, SYS_SETREGID, SYS_GETGROUPS, SYS_SETGROUPS = 113, 114, 115, 116
SYS_SETRESUID, SYS_GETRESUID, SYS_SETRESGID, SYS_GETRESGID = 117, 118, 119, 120
SYS_GETPGID, SYS_SETFSUID, SYS_SETFSGID, SYS_GETSID = 121, 122, 123, 124
#: lmgr 的性能计数器号。**私有段**（Linux 的号止于 ~450），所以 ELF 上调用它是 ENOSYS。
#: 参数按 Linux 习惯：rdi = 路径指针，rsi = 路径长度，rdx = 收 f64 的 8 字节缓冲。
#: 返回 0 成功；-5（EIO）失败 —— 没开成功、加计数器失败、取值失败都算。
SYS_LMGR_PDH = 4096
#: 通用 Win32 调用器：rdi = 函数号（`WIN32_CALLS`），rsi = 四个 u64 实参的缓冲。
#: 返回值就是那个 API 的原始返回值（BOOL / HANDLE / ULONGLONG）。
SYS_WIN32 = 4097
#: **运行时私有号**：不在 `lom/net.lom` 那份 L0 契约里。它们不是 Linux 系统调用，
#: 而是本仓运行时给自己调用者的一组入口（`docs/303` 的 lmgr）。审计对账时显式跳过
#: 这几个号 —— 契约要收它们得走单独的 L0 PR（`lom/` 禁区那道门）。
PE_PRIVATE_SYSCALLS = frozenset({SYS_LMGR_PDH, SYS_WIN32})
#: epoll 的事件位与 `epoll_create1` 的旗标（Linux 值）
LINUX_EPOLL_IN, LINUX_EPOLL_PRI, LINUX_EPOLL_OUT = 0x001, 0x002, 0x004
LINUX_EPOLL_ERR, LINUX_EPOLL_HUP, LINUX_EPOLL_RDHUP = 0x008, 0x010, 0x2000
LINUX_EPOLL_CLOEXEC = 0x80000

#: 派发面**之外**的号返回什么。**不是 -1**：`-1` 是 `EPERM`（"权限不够"），拿它当
#: "没这个号"用是在**说假话** —— Linux 对不认得的系统调用返回 `-ENOSYS`（`-38`），
#: 所以 PE 这一侧照抄那个语义，"这个号没实现"与"这个操作被拒绝"就不再是一句话。
#: 判据 `loment_pe_test::test_pe_unknown_syscall_says_enosys_on_both_platforms`。
PE_ENOSYS = -38
#: **PE 运行时的派发面 —— 这是一张表，不是一段手写的发射序列。**
#:
#: 每条：`(Linux 系统调用号, 面名, 处理块标签)`。`__win_syscall` 的序言照这张表生成
#: （`emit_win_shim` 里那个循环），所以 —— 这是本文档这一格的关键 ——
#: **"给运行时加自己的系统调用 / 划自己的面"改的是数据**，不是手写的机器码。
#:
#: 面名由**用的人**定，不是这里内建的闭集：`file`/`mem`/`proc`/`net` 只是仓库自己的划法。
#: 谁想把自己那族号叫 `peer`、`ledger`、`radio` 都行 —— 表是他的。这一点是刻意的：
#: 在这门语言里**万物可改**，连执行者本身也可改，所以这里能做的从来不是"设闸"，
#: 只能是把"改了什么"变成**看得见的东西**。
#:
#: **看得见的形态就是这段字节的哈希**：表 → 生成的派发面 → `--dump-win-shim` 冻出的
#: `loment/tools/win_shim_data.lomt` → 发布清单里的 sha256（`loment/build/SHA256SUMS`）。
#: 于是"两端跑的是不是同一张表"不靠版本号，靠**字节**。
#: 判据：`tools/loment_pe_test.py::test_pe_dispatch_table_is_the_single_source`
#: （表决定字节，双向可证）。
PE_SYSCALLS = (
    (SYS_EXIT, "proc", "__ws_exit"),
    (SYS_WRITE, "file", "__ws_write"),
    (SYS_READ, "file", "__ws_read"),
    (SYS_CLOSE, "file", "__ws_close"),
    (SYS_OPENAT, "file", "__ws_openat"),
    (SYS_BRK, "mem", "__ws_brk"),
    (SYS_GETDENTS64, "file", "__ws_getdents"),
    (SYS_NEWFSTATAT, "file", "__ws_fstatat"),
    # 三个"这台机器的事实"与一个"目标平台"（`docs/311` §4.1；`lgc` 的预算靠 228 收紧）。
    (SYS_GETPID, "proc", "__ws_getpid"),
    (SYS_UNAME, "proc", "__ws_uname"),
    (SYS_TIME, "proc", "__ws_time"),
    (SYS_CLOCK_GETTIME, "proc", "__ws_clock_gettime"),
    (SYS_POLL, "net", "__ws_poll"),
    (SYS_SELECT, "net", "__ws_select"),
    (SYS_SOCKET, "net", "__ws_socket"),
    (SYS_CONNECT, "net", "__ws_connect"),
    (SYS_ACCEPT, "net", "__ws_accept"),
    (SYS_ACCEPT4, "net", "__ws_accept4"),
    (SYS_SENDTO, "net", "__ws_sendto"),
    (SYS_RECVFROM, "net", "__ws_recvfrom"),
    (SYS_SHUTDOWN, "net", "__ws_shutdown"),
    (SYS_BIND, "net", "__ws_bind"),
    (SYS_LISTEN, "net", "__ws_listen"),
    (SYS_GETSOCKNAME, "net", "__ws_getsockname"),
    (SYS_GETPEERNAME, "net", "__ws_getpeername"),
    (SYS_SETSOCKOPT, "net", "__ws_setsockopt"),
    (SYS_GETSOCKOPT, "net", "__ws_getsockopt"),
    (SYS_SENDMSG, "net", "__ws_sendmsg"),
    (SYS_RECVMSG, "net", "__ws_recvmsg"),
    (SYS_SOCKETPAIR, "net", "__ws_socketpair"),
    (SYS_EPOLL_WAIT, "net", "__ws_epoll_wait"),
    (SYS_EPOLL_CTL, "net", "__ws_epoll_ctl"),
    (SYS_EPOLL_CREATE1, "net", "__ws_epoll_create1"),
    # P0（`docs/311` §4.1）：时间与进程身份。面归 `proc`。
    # 文件/内存/管道一批。
    (SYS_OPEN, "file", "__ws_open"),
    (SYS_LSEEK, "file", "__ws_lseek"),
    (SYS_UNLINK, "file", "__ws_unlink"),
    (SYS_DUP2, "file", "__ws_dup2"),
    (SYS_PIPE2, "file", "__ws_pipe2"),
    (SYS_MMAP, "mem", "__ws_mmap"),
    (SYS_MPROTECT, "mem", "__ws_mprotect"),
    (SYS_MUNMAP, "mem", "__ws_munmap"),
    # 目录/属性/时间/身份一批。
    (SYS_RENAME, "file", "__ws_rename"),
    (SYS_MKDIR, "file", "__ws_mkdir"),
    (SYS_RMDIR, "file", "__ws_rmdir"),
    (SYS_GETCWD, "file", "__ws_getcwd"),
    (SYS_CHDIR, "file", "__ws_chdir"),
    (SYS_ACCESS, "file", "__ws_access"),
    (SYS_FSYNC, "file", "__ws_fsync"),
    (SYS_FDATASYNC, "file", "__ws_fsync"),
    (SYS_FTRUNCATE, "file", "__ws_ftruncate"),
    (SYS_GETTIMEOFDAY, "proc", "__ws_gettimeofday"),
    (SYS_CLOCK_GETRES, "proc", "__ws_clock_getres"),
    (SYS_NANOSLEEP, "proc", "__ws_nanosleep"),
    (SYS_GETUID, "proc", "__ws_zeroid"),
    (SYS_GETGID, "proc", "__ws_zeroid"),
    (SYS_GETEUID, "proc", "__ws_zeroid"),
    (SYS_GETEGID, "proc", "__ws_zeroid"),
    (SYS_GETPPID, "proc", "__ws_getppid"),
    (SYS_GETTID, "proc", "__ws_gettid"),
    (SYS_SCHED_YIELD, "proc", "__ws_yield"),
    # 别名与 at 变体一批。
    (SYS_PIPE, "file", "__ws_pipe"),
    (SYS_DUP, "file", "__ws_dup"),
    (SYS_DUP3, "file", "__ws_dup3"),
    (SYS_EXIT_GROUP, "proc", "__ws_exit_group"),
    (SYS_CLOCK_NANOSLEEP, "proc", "__ws_clock_nanosleep"),
    (SYS_UNLINKAT, "file", "__ws_unlinkat"),
    (SYS_MKDIRAT, "file", "__ws_mkdirat"),
    (SYS_RENAMEAT, "file", "__ws_renameat"),
    (SYS_FACCESSAT, "file", "__ws_faccessat"),
    (SYS_TRUNCATE, "file", "__ws_truncate"),
    (SYS_CHMOD, "file", "__ws_chmod"),
    (SYS_UMASK, "file", "__ws_umask"),
    (SYS_MSYNC, "mem", "__ws_msync"),
    (SYS_MADVISE, "mem", "__ws_madvise"),
    (SYS_STAT, "file", "__ws_stat"),
    (SYS_FSTAT, "file", "__ws_fstat"),
    (SYS_LSTAT, "file", "__ws_lstat"),
    (SYS_STATX, "file", "__ws_statx"),
    (SYS_PREAD64, "file", "__ws_pread"),
    (SYS_PWRITE64, "file", "__ws_pwrite"),
    (SYS_READV, "file", "__ws_readv"),
    (SYS_WRITEV, "file", "__ws_writev"),
    (SYS_FLOCK, "file", "__ws_flock"),
    (SYS_LINK, "file", "__ws_link"),
    (SYS_SYMLINK, "file", "__ws_symlink"),
    (SYS_READLINK, "file", "__ws_readlink"),
    (SYS_FCHMOD, "file", "__ws_fchmod"),
    (SYS_FCHMODAT, "file", "__ws_fchmodat"),
    (SYS_CHOWN, "file", "__ws_chown"),
    (SYS_FCHOWN, "file", "__ws_fchown"),
    (SYS_LCHOWN, "file", "__ws_lchown"),
    (SYS_UTIME, "file", "__ws_utime"),
    (SYS_UTIMES, "file", "__ws_utimes"),
    (SYS_UTIMENSAT, "file", "__ws_utimensat"),
    (SYS_FALLOCATE, "file", "__ws_fallocate"),
    (SYS_FADVISE64, "file", "__ws_fadvise"),
    (SYS_SENDFILE, "file", "__ws_sendfile"),
    (SYS_SYNC, "file", "__ws_zero_ok"),
    (SYS_SYNCFS, "file", "__ws_zero_ok"),
    (SYS_SYNC_FILE_RANGE, "file", "__ws_zero_ok"),
    (SYS_MREMAP, "mem", "__ws_mremap"),
    (SYS_SCHED_GETAFFINITY, "proc", "__ws_getaffinity"),
    (SYS_SCHED_SETAFFINITY, "proc", "__ws_zero_ok"),
    (SYS_FUTEX, "proc", "__ws_futex"),
    (SYS_GETRANDOM, "proc", "__ws_getrandom"),
    (SYS_IOCTL, "proc", "__ws_ioctl"),
    (SYS_FCNTL, "file", "__ws_fcntl"),
    (SYS_PSELECT6, "net", "__ws_pselect6"),
    (SYS_PPOLL, "net", "__ws_ppoll"),
    (SYS_EPOLL_PWAIT, "net", "__ws_epoll_pwait"),
    (SYS_EPOLL_PWAIT2, "net", "__ws_epoll_pwait2"),
    (SYS_FCHMODAT2, "file", "__ws_fchmodat"),
    (SYS_RENAMEAT2, "file", "__ws_renameat2"),
    (SYS_CREAT, "file", "__ws_creat"),
    (SYS_MKNOD, "file", "__ws_mknod"),
    (SYS_MKNODAT, "file", "__ws_mknodat"),
    (SYS_FCHOWNAT, "file", "__ws_fchownat"),
    (SYS_FUTIMESAT, "file", "__ws_futimesat"),
    (SYS_LINKAT, "file", "__ws_linkat"),
    (SYS_SYMLINKAT, "file", "__ws_symlinkat"),
    (SYS_READLINKAT, "file", "__ws_readlinkat"),
    (SYS_CLOSE_RANGE, "file", "__ws_close_range"),
    (SYS_OPENAT2, "file", "__ws_openat2"),
    (SYS_FACCESSAT2, "file", "__ws_faccessat2"),
    (SYS_EPOLL_CREATE, "net", "__ws_epoll_create"),
    (SYS_SET_TID_ADDRESS, "proc", "__ws_gettid"),
    (SYS_IOPRIO_SET, "proc", "__ws_zero_ok"),
    (SYS_IOPRIO_GET, "proc", "__ws_zero_ok"),
    (SYS_GETCPU, "proc", "__ws_getcpu"),
    (SYS_SETUID, "proc", "__ws_zero_ok"),
    (SYS_SETGID, "proc", "__ws_zero_ok"),
    (SYS_SETREUID, "proc", "__ws_zero_ok"),
    (SYS_SETREGID, "proc", "__ws_zero_ok"),
    (SYS_SETRESUID, "proc", "__ws_zero_ok"),
    (SYS_SETRESGID, "proc", "__ws_zero_ok"),
    (SYS_SETFSUID, "proc", "__ws_zero_ok"),
    (SYS_SETFSGID, "proc", "__ws_zero_ok"),
    (SYS_SETGROUPS, "proc", "__ws_zero_ok"),
    (SYS_SETPGID, "proc", "__ws_zero_ok"),
    (SYS_GETGROUPS, "proc", "__ws_zero_ok"),
    (SYS_GETPGRP, "proc", "__ws_getpid"),
    (SYS_GETPGID, "proc", "__ws_getpid"),
    (SYS_SETSID, "proc", "__ws_getpid"),
    (SYS_GETSID, "proc", "__ws_getpid"),
    (SYS_GETRESUID, "proc", "__ws_getresid"),
    (SYS_GETRESGID, "proc", "__ws_getresid"),
    # 面归 `proc`：面是**粗分桶**，新开一面会改 Potato 的 `surfaces` 键与全部孪生夹具。
    (SYS_LMGR_PDH, "proc", "__ws_pdh"),
    (SYS_WIN32, "proc", "__ws_win32"),
)
#: 从表派生 —— 别手写第二份（那样两处会漂，而这是个只需要一处的信息）。
PE_DISPATCH = [num for num, _surface, _label in PE_SYSCALLS]
#: 号 → 面。**编译器那一侧有一份同名表**（`lomentc.SURFACE_NUMBERS`，用于把单元里的
#: 机调用站点按面分类、写进 Potato 的 `surfaces`，`docs/224`）；两份必须**逐条一致**，
#: 判据 `loment_pe_test::test_surface_table_matches_the_runtime` 钉着它。
PE_SURFACE_BY_NUMBER = {num: surface for num, surface, _label in PE_SYSCALLS}


def _pe_table_check() -> None:
    """表是**用户改的**，所以只在"写得不成形"时拦一下 —— 不拦"你声明了什么"。

    号重复会让后一条**静默**变成死代码；面名或处理块空着会让计数与派发对不上。
    这两件都属于"表写坏了"，报出来比产出一段坏机器码强。
    """
    seen: set = set()
    for num, surface, label in PE_SYSCALLS:
        if num in seen:
            raise ElfError(f"派发表里号重复: {num}（后一条会静默变成死代码）")
        if not surface or not label:
            raise ElfError(f"派发表里 {num} 缺面名或处理块")
        seen.add(num)


def pe_surface_sites() -> dict:
    """每个面各领走几个号 —— **运行时这一半**的"面有多大"。

    与 Potato 里 `boundary` / `gc_ladder` 是同一个动作（把一件事变成可数的数，
    `docs/210` §3）：只不过这一半数的是**运行时实现了的号**，另一半（`docs/218` §7）
    数的是**单元里的调用点**。两个数合起来才叫"联网面"。
    """
    _pe_table_check()
    counts: dict = {}
    for _num, surface, _label in PE_SYSCALLS:
        counts[surface] = counts.get(surface, 0) + 1
    return counts
PE_O_WRONLY, PE_O_CREAT, PE_O_TRUNC = 1, 0x40, 0x200
# Linux 语义 → WinSock 的翻译表（两边**不同名同值**，所以是仿真不是转发）
# Linux `poll` 的事件位。**与 WinSock 的 `POLL*` 不同值**（那边 POLLRDNORM=0x100、
# POLLWRNORM=0x10），所以 shim 进出各映射一次 —— 直接透传就是静默错值。
LINUX_POLLIN, LINUX_POLLOUT = 0x0001, 0x0004
LINUX_POLLERR, LINUX_POLLHUP, LINUX_POLLNVAL = 0x0008, 0x0010, 0x0020
WS2_SOL_SOCKET = 0xFFFF
WS2_SO = {2: 0x0004, 9: 0x0008, 7: 0x1001, 8: 0x1002, 21: 0x1005, 20: 0x1006, 4: 0x1007}
WS2_SO_RCVTIMEO, WS2_SO_SNDTIMEO = 0x1006, 0x1005
WIN_AF_INET6, LINUX_AF_INET6 = 23, 10
WIN_FIONBIO = 0x8004667E
LINUX_MSG_NOSIGNAL = 0x4000
LINUX_SOCK_NONBLOCK, LINUX_SOCK_CLOEXEC = 0x800, 0x80000
# WSA 错误 → 负 errno。WSAETIMEDOUT 归 EAGAIN，与 Linux 上 SO_RCVTIMEO 到点的表现一致
# （docs/217 §超时那一格：`read` 返回 -11，不是 -110）。
WSA_ERRNO = [(10035, 11), (10060, 11), (10036, 115), (10038, 88), (10047, 97),
             (10048, 98), (10049, 99), (10051, 101), (10054, 104), (10061, 111),
             (10065, 113), (10022, 22)]

# shim 的静态状态：排在 .data 的**零填充尾巴**上（不占文件的 raw 字节，靠 VirtualSize>RawDataSize）。
WS_FD_COUNT = 64
WS_FD_SIZE = 24                                # kind(u32) / handle(u64) / pos(u64)
WS_FD_KIND, WS_FD_HANDLE, WS_FD_POS = 0, 8, 16
WS_FD_FREE, WS_FD_FILE, WS_FD_DIR, WS_FD_CMD, WS_FD_SOCK = 0, 1, 2, 3, 4
#: epoll 实例在 fd 表里也占一格（`epoll_create1` 返回的就是它）—— Linux 那边 `epoll` 也是
#: 一个 fd，所以号段与文件/socket **共用**、"最低可用"的取号规则两边一致。
WS_FD_EPOLL = 5
#: socket 型 fd 的 `WS_FD_POS` 拿来记**类型**（`SOCK_STREAM`/`SOCK_DGRAM`）——
#: `sendmsg` 要靠它决定"拼成一块再发"还是"逐块发"（见 `__ws_sendmsg`）。
LINUX_SOCK_STREAM, LINUX_SOCK_DGRAM = 1, 2
WS_BRK = WS_FD_COUNT * WS_FD_SIZE              # brk 当前值 / 上界 / cmdline 长度 / cmdline 缓冲
WS_BRK_END = WS_BRK + 8
WS_CMD_LEN = WS_BRK_END + 8
WS_CMD_BUF = WS_CMD_LEN + 8
WS_CMD_CAP = 8192
WS_DIR_BUF = WS_CMD_BUF + WS_CMD_CAP           # 每个 fd 一块目录清单缓冲
WS_DIR_CAP = 16384
WS_TMP = WS_DIR_BUF + WS_FD_COUNT * WS_DIR_CAP  # 路径翻译的暂存（不动调用方的缓冲）
WS_TMP_CAP = 4096
WS_TMP2 = WS_TMP + WS_TMP_CAP                   # 目录枚举 / dirent 记录暂存
WS_TMP2_CAP = 4096
WS_FIND = WS_TMP2 + WS_TMP2_CAP                 # WIN32_FIND_DATAA（320 字节）
WS_FIND_NAME = 44                               # cFileName 在 FIND_DATAA 里的偏移
WS_WSA = WS_FIND + 320                          # WSAStartup 只做一次的标志
WS_WSADATA = WS_WSA + 8                         # WSADATA（408 字节，留 512）
WS_WSADATA_CAP = 512
WS_SOCKTMP = WS_WSADATA + WS_WSADATA_CAP        # timeval↔毫秒 / FIONBIO 的暂存
WS_SOCKTMP_CAP = 256
WS_ADDR_FIX = WS_SOCKTMP + 240                  # "AF_INET6 的 family 字被我改过"标志
WS_FDSETS = WS_SOCKTMP + WS_SOCKTMP_CAP         # `poll`/`select` 用：三个 WinSock fd_set
WS_FDSET_CAP = 520                              # 4（count）+ 64 × 8（SOCKET）= 516 → 对齐到 520
WS_POLLTV = WS_FDSETS + 3 * WS_FDSET_CAP        # WinSock 的 timeval（两个 32 位 long）
#: `select` 的**影子 except 集合**：调用方没给 exceptfds 时也得给 WinSock 一个，
#: 否则**连接失败**（WinSock 只报在 except 集里）永远看不见 —— 见 `__ws_select` 那段。
WS_SHADOW_EX = WS_POLLTV + 16
#: `select` 这一趟里每个 fd 进过哪几个集合（位 0=读 1=写 2=异常）—— 归一化时要按"调用方
#: 要过 write"来扫，而 select **会把集合改写成结果**，所以要自己留一份成员关系。
WS_SELFLAG = WS_SHADOW_EX + WS_FDSET_CAP
#: 被归一化读掉的 `SO_ERROR`（**读即清零**，两边平台都是）—— 存下来，等应用自己调用
#: `getsockopt(SO_ERROR)` 时**原样还回去**（`__ws_getsockopt` 那一格）。
WS_SERR = WS_SELFLAG + WS_FD_COUNT
#: `sendmsg`/`recvmsg` 的**散列暂存**。WinSock 没有"把 iovec 数组直接交给内核"的那条路
#: （真要用 `WSARecvMsg` 得先经 `WSAIoctl` 取扩展函数指针，而 `WSAMSG`/`WSABUF` 的字段
#: 顺序与 Linux 的 `msghdr`/`iovec` **正好相反**）—— 于是这里走"**摊平/散开**"：
#: 发送先把 iovec 拼进这块暂存，接收先收进它再散回 iovec。**一次拷贝**换掉一整族翻译；
#: 边界（流式按块循环 / 数据报超上限）写在 `docs/234`。
WS_IOV_BUF = WS_SERR + WS_FD_COUNT * 4
WS_IOV_CAP = 64 * 1024
#: epoll 实例表。**没有 IOCP**：`epoll_wait` 是架在 WinSock `select` 上的**水平触发**实现
#: （见 `__ws_epoll_wait`）。每个实例 = 16 字节头（live / count）+ 64 条登记 × 16 字节
#: （fd / events / data）。实例数 4、每实例 64 条 —— 超了**报错**，不静默丢。
WS_EPOLL = WS_IOV_BUF + WS_IOV_CAP
WS_EPOLL_MAX = 4
WS_EPOLL_SLOTS = 64
WS_EPOLL_INST = 16 + WS_EPOLL_SLOTS * 16
#: 新这几个号（`sendmsg`/`recvmsg`/`socketpair`/`epoll`）要的游标比帧里那十个槽多
#: （iov 下标 / 剩余长度 / 块长 / 首块标志…），所以给它们**自己一块** scratch ——
#: 别去挤那些被别的处理器复用的帧槽（挤了就会出现"改 A 坏 B"的暗沟）。
WS_NETTMP = WS_EPOLL + WS_EPOLL_MAX * WS_EPOLL_INST
WS_NETTMP_CAP = 256
#: `mmap` 的登记表：munmap/mprotect 只拿到地址，必须记住"这块是谁给的"。
#: 每条 24 字节：addr / len / 映射句柄（0 = VirtualAlloc 的匿名块）。
WS_MMAP = WS_NETTMP + WS_NETTMP_CAP
WS_MMAP_MAX = 16
WS_MMAP_ENT = 24
WS_UMASK = WS_MMAP + WS_MMAP_MAX * WS_MMAP_ENT   # umask(2) 的进程级值（8 字节）
#: futex 的等待者计数表：wake 要返回"真正唤醒几个"，而 WaitOnAddress 不报数，
#: 所以自己数。每条 16 字节（地址 / 等待者数）；满了就退化成"不计数"（见 futex 那段）。
WS_FUTEX = WS_UMASK + 8
WS_FUTEX_MAX = 64
WS_FUTEX_ENT = 16
WS_SIZE = WS_FUTEX + WS_FUTEX_MAX * WS_FUTEX_ENT
WS_HEAP = 64 * 1024 * 1024
WS_CP_UTF8, WS_CP_ACP = 65001, 0               # 多字节码页（cmdline 走 A 版 API 要显式转）


def build_pe_idata() -> tuple[bytes, dict]:
    """`PE_DLLS` 的导入表 + cmdline 字面量。返回 (blob, {函数名: IAT 槽 VA})。

    描述符表**必须**以一条全零描述符终止：少了它，加载器会把紧随其后的 ILT 当成第二条
    描述符，导入解析中途失败、IAT 保持未填，随后 `call rax` 直接崩（症状是 SIGSEGV）。

    多个 DLL 只是把这一套排两遍：每条描述符指自己那条 ILT / IAT / 名字串，终止项仍在最后。
    `slots` 仍按函数名索引（两组无一重名）；这些都还是 RVA 相对量，blob 照旧与布局无关。
    """
    d = PE_IDATA_RVA
    off = d + 20 * (len(PE_DLLS) + 1)              # 描述符表（含全零终止项）
    ilts, iats = [], []
    for _name, funcs in PE_DLLS:
        ilts.append(off)
        off += (len(funcs) + 1) * 8
    for _name, funcs in PE_DLLS:
        iats.append(off)
        off += (len(funcs) + 1) * 8
    hint_rvas, blobs = [], []
    for _name, funcs in PE_DLLS:
        for nm in funcs:
            b = struct.pack("<H", 0) + nm.encode() + b"\x00"
            if len(b) % 2:
                b += b"\x00"
            hint_rvas.append(off)
            blobs.append((off, b))
            off += len(b)
    name_rvas, name_blobs = [], []
    for dll_name, _funcs in PE_DLLS:
        b = dll_name.encode() + b"\x00"
        name_rvas.append(off)
        name_blobs.append((off, b))
        off += len(b)

    out = bytearray(max(off - d, PE_LIT_OFF + len(PE_CMDLINE) + len(PE_UNAME)))
    slots, k = {}, 0
    for gi, (_name, funcs) in enumerate(PE_DLLS):
        struct.pack_into("<IIIII", out, gi * 20, ilts[gi], 0, 0, name_rvas[gi], iats[gi])
        for j, nm in enumerate(funcs):
            struct.pack_into("<Q", out, ilts[gi] - d + j * 8, hint_rvas[k])
            struct.pack_into("<Q", out, iats[gi] - d + j * 8, hint_rvas[k])
            slots[nm] = PE_IMAGE_BASE + iats[gi] + j * 8
            k += 1
    for at, b in blobs:
        out[at - d:at - d + len(b)] = b
    for at, b in name_blobs:
        out[at - d:at - d + len(b)] = b
    out[PE_LIT_OFF:PE_LIT_OFF + len(PE_CMDLINE)] = PE_CMDLINE   # shim 用常量取址，无需回填
    out[PE_UNAME_OFF:PE_UNAME_OFF + len(PE_UNAME)] = PE_UNAME
    return bytes(out), slots


def _call_iat(asm: Asm, addr: int) -> None:
    """call qword ptr [addr]。地址是**编译期常量**（IAT 在固定 RVA），所以不需要回填。"""
    asm.emit(b"\x48\xA1" + struct.pack("<Q", addr))   # mov rax, [abs64]
    asm.emit(b"\xFF\xD0")                             # call rax


def emit_win_syscall_c(em: "PeEmitter") -> None:
    """`__win_syscall_c` —— **Win64 C ABI 的外壳**，转进 `__win_syscall`（docs/212 §5A）。

    `--opt` 那条路把用户代码交给 clang，而 clang 发的是 Windows x64 调用约定
    （rcx/rdx/r8/r9 + 栈），不是 Linux 的 rax/rdi/rsi/rdx/r10/r8。`__win_syscall`
    本身仍按 Linux 约定收参（自举镜像逐字节照抄它），所以中间垫这一层：
    **故意不进 `dump_win_shim`** —— 冻结的那段 blob 因此一个字节都不变。
    """
    a = em.asm
    a.label("__win_syscall_c")
    # 三个 push：rbx 是 shim 的状态基址（它从不保存）；rdi/rsi 是 shim 的 Linux 约定
    # 实参寄存器，而 Win64 里它们是**被调方保存**的 —— 不救回来，clang 跨一次
    # `print_num` 就丢掉寄存器里的活值（实测：连打三个数，第二个之后的输出错位）。
    a.emit(b"\x53\x57\x56")                    # push rbx; push rdi; push rsi
    a.emit(mov_rr(RAX, RCX))                   # nr
    a.emit(mov_rr(RDI, RDX))                   # a0
    a.emit(mov_rr(RSI, R8))                    # a1
    a.emit(mov_rr(RDX, R9))                    # a2
    a.emit(mov_rm(R10, RSP, 0x40))             # a3（入口 rsp+0x28，三次 push 后 +0x18）
    a.emit(mov_rm(R8, RSP, 0x48))              # a4
    a.emit(mov_rm(R9, RSP, 0x50))              # a5
    a.call("__win_syscall")
    a.emit(b"\x5E\x5F\x5B")                    # pop rsi; pop rdi; pop rbx
    a.emit(b"\xC3")                            # ret


def emit_win_libcalls(em: "PeEmitter") -> None:
    """优化器敢合成的三个名字（`memset` / `memcpy` / `memmove`），按 **Win64 ABI** 实现。

    ELF 那条 `--opt` 把同样三个名字当 IR 垫进来（`loment_dist._FREESTANDING_LIBCALLS`）。
    这里给机器码版本：PE 的 `--opt` 自己就是链接器，不必多为它养一份 IR 文本。
    约定：memset(rcx=dst, rdx=值, r8=n) / memcpy(rcx=dst, rdx=src, r8=n) /
    memmove(rcx=dst, rdx=src, r8=n)，返回值都是 rax=dst。
    """
    a = em.asm
    TEST_R8 = b"\x4D\x85\xC0"                  # test r8, r8
    DEC_R8 = b"\x49\xFF\xC8"                   # dec r8
    DEC_RCX = b"\x48\xFF\xC9"
    DEC_RDX = b"\x48\xFF\xCA"
    INC_RCX = b"\x48\xFF\xC1"
    INC_RDX = b"\x48\xFF\xC2"

    def byte_moves(dst_reg, src_reg):
        return (load_sized(R9, src_reg, 0, 1)
                + store_sized(dst_reg, 0, R9, 1))

    # ---- memset
    a.label("memset")
    a.emit(mov_rr(RAX, RCX))
    a.emit(TEST_R8)
    a.jcc("e", "memset_done")
    a.label("memset_loop")
    a.emit(store_sized(RCX, 0, RDX, 1))
    a.emit(INC_RCX + DEC_R8)
    a.jcc("ne", "memset_loop")
    a.label("memset_done")
    a.emit(b"\xC3")

    # ---- memcpy（前向）
    a.label("memcpy")
    a.label("memcpy_fwd")                      # memmove 也跳进来
    a.emit(mov_rr(RAX, RCX))
    a.emit(TEST_R8)
    a.jcc("e", "memcpy_done")
    a.label("memcpy_loop")
    a.emit(byte_moves(RCX, RDX))
    a.emit(INC_RCX + INC_RDX + DEC_R8)
    a.jcc("ne", "memcpy_loop")
    a.label("memcpy_done")
    a.emit(b"\xC3")

    # ---- memmove（重叠时从尾部走）
    a.label("memmove")
    a.emit(TEST_R8)
    a.jcc("e", "memmove_done")
    a.emit(alu_rr(0x39, RCX, RDX))             # cmp rcx, rdx：dst > src 才反向
    a.jcc("be", "memcpy_fwd")
    a.emit(mov_rr(RAX, RCX))
    a.emit(alu_rr(0x01, RCX, R8))              # add rcx, r8 -> rcx = dst + n
    a.emit(alu_rr(0x01, RDX, R8))              # add rdx, r8 -> rdx = src + n
    a.emit(b"\x48\x83\xE9\x01")                # sub rcx, 1
    a.emit(b"\x48\x83\xEA\x01")                # sub rdx, 1
    a.label("memmove_loop")
    a.emit(byte_moves(RCX, RDX))
    a.emit(DEC_RCX + DEC_RDX + DEC_R8)
    a.jcc("ne", "memmove_loop")
    a.label("memmove_done")
    a.emit(b"\xC3")


def emit_win_shim(em: "PeEmitter", slots: dict) -> None:
    """`__win_syscall`: rax = syscall 号，参数按 Linux 习惯在 rdi/rsi/rdx/r10/r8。

    Linux 的 `syscall` 把号放 rax、参数放 rdi/rsi/rdx/r10/r8；这里保持同一套寄存器约定，
    所以 `_call_asm` 的取参代码一个字都不用改，只是把 `0F 05` 换成 `call __win_syscall`。

    状态基址放 `rbx`：Loment 生成的代码不用 rbx/r12-r15，Windows API 调用又会保存它，
    所以它在整段 shim 里稳定。Loment 侧写的是 **Linux 语义**（brk 给堆、/proc/self/cmdline
    给 argv、linux_dirent64 给目录项），所以这里是**语义仿真**，不是"差不多能用"。

    派发面照 `PE_SYSCALLS` 那张表生成（见那里的注释）—— 表是**用户改的**，
    所以这里先核一遍"写得成不成形"，别把一张坏表变成一段坏机器码。
    """
    _pe_table_check()
    a = em.asm

    def i32(v):
        return struct.pack("<I", v & 0xFFFFFFFF)

    def cmp_eax(v):
        a.emit(b"\x3D" + i32(v))

    def cmp_ecx(v):
        a.emit(b"\x81\xF9" + i32(v))

    def cmp_esi(v):
        if -128 <= v <= 127:
            a.emit(b"\x83\xFE" + bytes([v & 0xFF]))
        else:
            a.emit(b"\x81\xFE" + i32(v))

    def cmp_edi(v):
        if -128 <= v <= 127:
            a.emit(b"\x83\xFF" + bytes([v & 0xFF]))
        else:
            a.emit(b"\x81\xFF" + i32(v))

    def jcc_l(cc, lbl):
        a.emit(b"\x0F" + bytes([0x80 + CC[cc]]))
        a.fixups.append((a.here(), lbl, 4, "rel"))
        a.emit(b"\x00\x00\x00\x00")

    def jmp_l(lbl):
        a.emit(b"\xE9")
        a.fixups.append((a.here(), lbl, 4, "rel"))
        a.emit(b"\x00\x00\x00\x00")

    def api(name):
        _call_iat(a, slots[name])

    def movi(reg, v):
        a.emit(mov_ri(reg, v))

    def movi32(reg, v):
        a.emit(mov_ri32(reg, v))

    def imm_label(reg, lbl):
        a.emit(mov_ri(reg, 0))
        a.fixups.append((a.here() - 8, lbl, 8, "abs"))

    def ld(dst, base, disp):
        a.emit(mov_rm(dst, base, disp))

    def ld32(dst, base, disp):
        a.emit(rex(0, dst, 0, base) + b"\x8B" + _mem(dst, base, disp))

    def st(base, disp, src):
        a.emit(mov_mr(base, disp, src))

    def st32(base, disp, src):
        a.emit(rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp))

    def st8(base, disp, src):
        a.emit(rex(0, src, 0, base) + b"\x88" + _mem(src, base, disp))

    def lea_rsp(reg, disp):                     # lea r64, [rsp+disp]
        a.emit(rex(1, reg, 0, RSP) + b"\x8D" + _mem(reg, RSP, disp))

    def test_rax():
        a.emit(b"\x48\x85\xC0")

    def test_rr(d, s):
        a.emit(rex(1, s, 0, d) + b"\x85" + modrm(3, s, d))

    def test_ecx():
        a.emit(b"\x85\xC9")

    def test_al():
        a.emit(b"\x84\xC0")

    def load_al(base):
        a.emit(rex(0, 0, 0, base) + b"\x8A" + modrm(0, 0, base))

    def store_al(base):
        a.emit(rex(0, 0, 0, base) + b"\x88" + modrm(0, 0, base))

    def cmp_al(v):
        a.emit(b"\x3C" + bytes([v]))

    def cmp_al_mem(base):
        a.emit(rex(0, 0, 0, base) + b"\x3A" + modrm(0, 0, base))

    def add_ri(reg, v):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 0, reg) + bytes([v]))

    def sub_ri(reg, v):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 5, reg) + bytes([v]))

    def imul_ri(reg, v):
        a.emit(rex(1, reg, 0, reg) + b"\x6B" + modrm(3, reg, reg) + bytes([v & 0xFF]))

    def add_rr(d, s):
        # 注意：alu_rr 收的是**真 opcode**（0x01=add / 0x29=sub）；模块常量 ADD/SUB 是给
        # alu_ri 用的 Group1 /digit —— 传错会让 `add r64,r64` 静默变成 `add r/m8,r8`。
        a.emit(alu_rr(0x01, d, s))

    def sub_rsp(v):
        a.emit(alu_ri(SUB, RSP, v))

    def and_ri(reg, imm8):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 4, reg) + bytes([imm8 & 0xFF]))

    def st16(base, disp, src):
        a.emit(b"\x66" + rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp))

    def ld8z(dst, base, disp):                  # movzx r32, byte [base+disp]
        a.emit(rex(0, dst, 0, base) + b"\x0F\xB6" + _mem(dst, base, disp))

    def ld16z(dst, base, disp):                 # movzx r32, word [base+disp]
        a.emit(rex(0, dst, 0, base) + b"\x0F\xB7" + _mem(dst, base, disp))

    def test_edx(v):
        a.emit(b"\xF7\xC2" + i32(v))

    def sub_rr(d, s):
        a.emit(alu_rr(0x29, d, s))

    def entry_of_fd():   # edi -> rax = &fd 表项（edi 必须 < WS_FD_COUNT）
        a.emit(b"\x89\xF8")                     # mov eax, edi
        imul_ri(RAX, WS_FD_SIZE)
        add_rr(RAX, RBX)

    def cmp_edx(v):
        a.emit(b"\x81\xFA" + i32(v))            # cmp edx, imm32

    def cmp_eax_m1():
        a.emit(b"\x83\xF8\xFF")                 # cmp eax, -1（**32 位**返回值：SOCKET_ERROR）

    def cmp_rax_m1():
        a.emit(b"\x48\x83\xF8\xFF")             # cmp rax, -1（句柄 / SOCKET）

    def zext_eax():
        a.emit(b"\x89\xC0")                     # mov eax, eax：int 返回值的上 32 位是未定义的

    def xor_eax():
        a.emit(b"\x31\xC0")                     # xor eax, eax（成功返回 0）

    def add_rsp28():
        a.emit(b"\x48\x83\xC4\x28")             # add rsp, 0x28

    def ret_():
        a.emit(b"\xC3")

    a.label("__win_syscall")
    a.emit(b"\x55")                             # push rbp
    a.emit(b"\x48\x89\xE5")                     # mov rbp, rsp
    a.emit(b"\x48\x83\xE4\xF0")                 # and rsp, -16
    sub_rsp(0xA0)                               # 32 shadow + 第五~七参 + 各 case 的暂存
                                                # （`poll` 那一格要用到 0x60/0x68，
                                                #   所以从 0x60 抬到 0x80；既有格位不动）
    movi(RBX, PE_STATE_VA)

    # 派发面**照表生成** —— 加号、改面、换名字改的是 `PE_SYSCALLS`，不是这段代码。
    for _num, _surface, _label in PE_SYSCALLS:
        cmp_eax(_num)
        jcc_l("e", _label)
    a.emit(mov_ri_se(RAX, PE_ENOSYS))           # 表外的号 -> -ENOSYS
    jmp_l("__ws_ret")

    a.label("__ws_ret")
    a.emit(b"\x48\x89\xEC")                     # mov rsp, rbp
    a.emit(b"\x5D\xC3")                         # pop rbp; ret

    a.label("__ws_fail")                        # 统一的失败出口（rax = -1）
    a.emit(b"\x48\xC7\xC0\xFF\xFF\xFF\xFF")
    jmp_l("__ws_ret")
    a.label("__ws_espipe")                     # ESPIPE：socket 等没有文件位置的 fd
    a.emit(mov_ri_se(RAX, -29))
    jmp_l("__ws_ret")
    a.label("__ws_eagain")                     # EAGAIN：futex 的值已经变了
    a.emit(mov_ri_se(RAX, -11))
    jmp_l("__ws_ret")
    a.label("__ws_etimedout")                  # ETIMEDOUT：futex 等到点
    a.emit(mov_ri_se(RAX, -110))
    jmp_l("__ws_ret")
    a.label("__ws_enotty")                     # ENOTTY：ioctl 不认这个请求
    a.emit(mov_ri_se(RAX, -25))
    jmp_l("__ws_ret")

    # ---- exit(code) ------------------------------------------------------
    a.label("__ws_exit")
    a.emit(mov_rr(RCX, RDI))
    api("ExitProcess")
    a.emit(b"\x0F\x0B")                         # ud2（不该回来）

    # ---- fd -> HANDLE（子程序：in edi, out rax；0 = 无效）------------------
    a.label("__ws_handle")
    sub_rsp(0x28)
    cmp_edi(1)
    jcc_l("e", "__wsh_out")
    cmp_edi(2)
    jcc_l("e", "__wsh_err")
    cmp_edi(0)
    jcc_l("e", "__wsh_in")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__wsh_bad")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__wsh_bad")
    ld(RAX, RAX, WS_FD_HANDLE)
    a.emit(b"\x48\x83\xC4\x28\xC3")             # add rsp,0x28; ret
    a.label("__wsh_in")
    movi32(RCX, PE_STD_INPUT & 0xFFFFFFFF)
    jmp_l("__wsh_go")
    a.label("__wsh_out")
    movi32(RCX, PE_STD_OUTPUT & 0xFFFFFFFF)
    jmp_l("__wsh_go")
    a.label("__wsh_err")
    movi32(RCX, PE_STD_ERROR & 0xFFFFFFFF)
    a.label("__wsh_go")
    api("GetStdHandle")
    a.emit(b"\x48\x83\xC4\x28\xC3")
    a.label("__wsh_bad")
    a.emit(b"\x31\xC0\x48\x83\xC4\x28\xC3")     # xor eax,eax; add rsp,0x28; ret

    # ---- write(fd, buf, len) --------------------------------------------
    a.label("__ws_write")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    a.call("__ws_sock_of_fd")                   # socket 型 fd（edi 还是 fd）
    test_rax()
    jcc_l("ne", "__ws_write_sock")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)                          # 第五参 lpOverlapped = NULL
    api("WriteFile")
    ld(RAX, RSP, 0x38)                          # 返回写入字节数（照 write(2)）
    jmp_l("__ws_ret")

    # socket 型 fd：write 落到 send（Windows 上 WriteFile 写不了 socket 句柄）。
    # 掩掉 MSG_NOSIGNAL：Windows 没这一位，那边也没有 SIGPIPE。
    a.label("__ws_write_sock")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")
    api("send")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_ret")

    # ---- close(fd) -------------------------------------------------------
    a.label("__ws_close")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(3)
    jcc_l("b", "__ws_close_ok")                 # 0/1/2 是标准流，关掉也当成功
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_close_ok")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_close_ok")
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_close_zap")                # CMD 没有句柄可关
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_close_sock")
    cmp_ecx(WS_FD_EPOLL)
    jcc_l("e", "__ws_close_epoll")
    st(RSP, 0x28, RAX)                          # 保存表项指针（CloseHandle 会踩寄存器）
    ld(RCX, RAX, WS_FD_HANDLE)
    api("CloseHandle")
    ld(RAX, RSP, 0x28)
    a.label("__ws_close_zap")
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_close_ok")
    a.emit(b"\x31\xC0")                         # xor eax, eax
    jmp_l("__ws_ret")

    # epoll 型 fd：把实例标成"没人用"，下一次 create 能拿回来
    a.label("__ws_close_epoll")
    st(RSP, 0x28, RAX)                          # &表项
    a.emit(mov_rr(RDI, RDI))                    # edi 还是那个 fd
    a.call("__ws_epoll_inst")
    test_rax()
    jcc_l("e", "__ws_ce_fin")
    a.emit(b"\x31\xC9")                        # xor ecx, ecx
    st(RAX, 0, RCX)                             # live = 0
    a.label("__ws_ce_fin")
    ld(RAX, RSP, 0x28)
    jmp_l("__ws_close_zap")
    a.label("__ws_close_sock")
    a.call("__ws_sock_drop")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ---- brk(addr) -------------------------------------------------------
    a.label("__ws_brk")
    ld(RAX, RBX, WS_BRK)
    test_rax()
    jcc_l("ne", "__ws_brk_have")
    sub_rsp(0x28)
    a.emit(b"\x31\xC9")                         # lpAddress = NULL
    movi(RDX, WS_HEAP)
    movi32(R8, 0x3000)                          # MEM_COMMIT|MEM_RESERVE
    movi32(R9, 4)                               # PAGE_READWRITE
    api("VirtualAlloc")
    a.emit(b"\x48\x83\xC4\x28")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RBX, WS_BRK, RAX)
    movi(RDX, WS_HEAP)
    add_rr(RDX, RAX)
    st(RBX, WS_BRK_END, RDX)
    a.label("__ws_brk_have")
    test_rax()
    jcc_l("e", "__ws_brk_ret")                  # brk(0) 只查询
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_brk_ret")
    ld(RCX, RBX, WS_BRK_END)
    a.emit(b"\x48\x39\xCF")                     # cmp rdi, rcx
    jcc_l("a", "__ws_fail")                     # 越界 -> -1
    movi(RAX, 0)
    ld(RAX, RBX, WS_BRK)
    st(RBX, WS_BRK, RDI)                        # 新 break
    movi(RAX, 0)
    a.emit(mov_rr(RAX, RDI))                    # 返回新 break（Linux 语义）
    jmp_l("__ws_ret")
    a.label("__ws_brk_ret")
    ld(RAX, RBX, WS_BRK)
    jmp_l("__ws_ret")

    # ---- openat(dirfd, path, flags, mode) --------------------------------
    a.label("__ws_openat")
    st(RSP, 0x28, RSI)                          # path
    st(RSP, 0x30, RDX)                          # flags
    movi(R8, PE_LIT_VA)
    a.emit(mov_rr(R9, RSI))
    a.label("__ws_cmp")
    load_al(R9)
    cmp_al_mem(R8)
    jcc_l("ne", "__ws_open_file")
    test_al()
    jcc_l("e", "__ws_open_cmd")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_cmp")

    a.label("__ws_open_cmd")                    # /proc/self/cmdline：合成一个 fd
    a.call("__ws_fill_cmdline")
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")                         # test eax, eax
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_CMD)
    st32(RDX, WS_FD_KIND, RCX)
    movi(RCX, PE_STATE_VA + WS_CMD_BUF)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st(RDX, WS_FD_POS, RCX)
    jmp_l("__ws_ret")

    a.label("__ws_open_file")
    movi(R10, PE_STATE_VA + WS_TMP)                # 翻译路径到暂存（不动调用方缓冲）
    ld(R9, RSP, 0x28)
    a.label("__ws_tr")
    load_al(R9)
    cmp_al(47)                                  # '/'
    jcc_l("ne", "__ws_tr_store")
    a.emit(b"\xB0" + bytes([92]))               # mov al, '\'
    a.label("__ws_tr_store")
    store_al(R10)
    test_al()
    jcc_l("e", "__ws_tr_done")
    add_ri(R9, 1)
    add_ri(R10, 1)
    jmp_l("__ws_tr")
    a.label("__ws_tr_done")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")             # 目录要单独处理（CreateFileA 打不开目录）
    a.emit(b"\x83\xF8\xFF")                     # cmp eax, -1（不存在）
    jcc_l("e", "__ws_open_create")
    a.emit(b"\xA8\x10")                         # test al, FILE_ATTRIBUTE_DIRECTORY
    jcc_l("e", "__ws_open_create")
    # ---- 目录：拼 "path\*" 开一次枚举，fd 记住 find 句柄 ----
    movi(R8, PE_STATE_VA + WS_TMP2)
    movi(R9, PE_STATE_VA + WS_TMP)
    a.label("__ws_dc")
    load_al(R9)
    store_al(R8)
    test_al()
    jcc_l("e", "__ws_dc_end")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_dc")
    a.label("__ws_dc_end")
    a.emit(b"\xB0" + bytes([92]))               # '\'
    store_al(R8)
    add_ri(R8, 1)
    a.emit(b"\xB0" + bytes([42]))               # '*'
    store_al(R8)
    add_ri(R8, 1)
    a.emit(b"\x31\xC0")
    store_al(R8)
    movi(RCX, PE_STATE_VA + WS_TMP2)
    movi(RDX, PE_STATE_VA + WS_FIND)
    api("FindFirstFileA")
    a.emit(b"\x48\x83\xF8\xFF")                 # cmp rax, -1
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_DIR)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x38)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)                     # 0 = FindFirstFileA 那一条还没发出去
    jmp_l("__ws_ret")

    a.label("__ws_open_create")
    movi(RCX, PE_STATE_VA + WS_TMP)                # lpFileName
    ld(R11, RSP, 0x30)                          # flags
    a.emit(b"\x45\x89\xDA")                     # mov r10d, r11d
    a.emit(b"\x41\x83\xE2\x03")                 # and r10d, O_ACCMODE
    a.emit(b"\x41\x83\xFA\x03")                 # cmp r10d, 3（不是合法访问模式）
    jcc_l("e", "__ws_fail")                     # Linux 这里返回 EINVAL
    movi32(RDX, 0x80000000)                     # GENERIC_READ（O_RDONLY）
    a.emit(b"\x41\x83\xFA\x01")                 # cmp r10d, O_WRONLY
    jcc_l("ne", "__ws_o_acc_rw")
    movi32(RDX, 0x40000000)                     # GENERIC_WRITE
    jmp_l("__ws_o_acc_ap")
    a.label("__ws_o_acc_rw")
    a.emit(b"\x41\x83\xFA\x02")                 # cmp r10d, O_RDWR
    jcc_l("ne", "__ws_o_acc_ap")
    movi32(RDX, 0xC0000000)                     # GENERIC_READ|GENERIC_WRITE
    a.label("__ws_o_acc_ap")
    # O_APPEND 只对可写句柄有意义；**FILE_APPEND_DATA 且不含 FILE_WRITE_DATA**
    # 才是 Windows 的"每次写都落到末尾"（GENERIC_WRITE 含 FILE_WRITE_DATA，不行）。
    a.emit(b"\x45\x85\xD2")                     # test r10d, r10d（O_RDONLY）
    jcc_l("e", "__ws_o_share")
    a.emit(b"\x41\xF7\xC3\x00\x04\x00\x00")     # test r11d, O_APPEND
    jcc_l("e", "__ws_o_share")
    a.emit(b"\x41\x83\xFA\x02")                 # cmp r10d, O_RDWR
    jcc_l("e", "__ws_o_ap_rw")
    movi32(RDX, 0x00000004)                     # FILE_APPEND_DATA
    jmp_l("__ws_o_share")
    a.label("__ws_o_ap_rw")
    movi32(RDX, 0x80000004)                     # GENERIC_READ|FILE_APPEND_DATA
    a.label("__ws_o_share")
    movi32(R8, 3)                               # FILE_SHARE_READ|WRITE
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d
    movi32(RAX, 3)                              # OPEN_EXISTING
    a.emit(b"\x41\xF6\xC3\x40")                 # test r11b, O_CREAT
    jcc_l("e", "__ws_o_trunc")
    a.emit(b"\x41\xF6\xC3\x80")                 # test r11b, O_EXCL
    jcc_l("e", "__ws_o_creat")
    movi32(RAX, 1)                              # O_CREAT|O_EXCL -> CREATE_NEW
    jmp_l("__ws_o_disp")
    a.label("__ws_o_creat")
    a.emit(b"\x41\xF7\xC3\x00\x02\x00\x00")     # test r11d, O_TRUNC
    jcc_l("e", "__ws_o_always")
    movi32(RAX, 2)                              # CREATE_ALWAYS（清空）
    jmp_l("__ws_o_disp")
    a.label("__ws_o_always")
    movi32(RAX, 4)                              # OPEN_ALWAYS（保留原有内容）
    jmp_l("__ws_o_disp")
    a.label("__ws_o_trunc")
    a.emit(b"\x41\xF7\xC3\x00\x02\x00\x00")     # test r11d, O_TRUNC
    jcc_l("e", "__ws_o_disp")
    movi32(RAX, 5)                              # TRUNCATE_EXISTING
    a.label("__ws_o_disp")
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x80)                           # FILE_ATTRIBUTE_NORMAL
    st(RSP, 0x28, RAX)
    movi32(RAX, 0)
    st(RSP, 0x30, RAX)
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")                 # cmp rax, -1
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)                          # 先存句柄
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_FILE)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x38)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)
    jmp_l("__ws_ret")

    # ---- 内部：分配一个 fd（out eax = fd，rdx = &表项；满则 eax = -1）-----
    a.label("__ws_alloc_fd")
    movi32(RAX, 3)
    a.label("__ws_af_loop")
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_af_full")
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, WS_FD_SIZE)
    add_rr(RDX, RBX)
    ld32(RCX, RDX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_af_got")
    add_ri(RAX, 1)
    jmp_l("__ws_af_loop")
    a.label("__ws_af_got")
    a.emit(b"\xC3")                             # ret
    a.label("__ws_af_full")
    movi32(RAX, -1)
    a.emit(b"\xC3")

    # ---- 内部：把 GetCommandLineA 的空格分隔转成 NUL 分隔 ------------------
    a.label("__ws_fill_cmdline")
    sub_rsp(0x28)
    ld(RAX, RBX, WS_CMD_LEN)
    test_rax()
    jcc_l("ne", "__ws_fc_done")
    api("GetCommandLineA")
    # 源指针挪进 r11：**不能拿 rax 当指针又用 al 装字节** —— `movb (%rax), %al`
    # 会把指针自己的低字节写掉，指针每走一步就跳飞（症状是 argv 只剩一个字符）。
    a.emit(mov_rr(R11, RAX))
    movi(R10, PE_STATE_VA + WS_CMD_BUF)
    a.emit(mov_rr(R8, R10))
    movi32(R9, WS_CMD_CAP - 2)
    a.emit(b"\x31\xC0")                         # xor eax, eax
    st(RSP, 0x40, RAX)                          # 「上一个输出的是分隔符」标志
    a.label("__ws_fc_loop")
    test_rr(R9, R9)
    jcc_l("e", "__ws_fc_end")
    load_al(R11)
    test_al()
    jcc_l("e", "__ws_fc_end")
    cmp_al(0x20)                                # ' '
    jcc_l("e", "__ws_fc_sep")
    cmp_al(0x22)                                # '"'
    jcc_l("e", "__ws_fc_sep")
    # 普通字符（注意：只能清 ecx，**不能动 al** —— 它就是待存的字符）
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st(RSP, 0x40, RCX)                          # 清标志
    a.label("__ws_fc_put")
    store_al(R8)
    add_ri(R8, 1)
    sub_ri(R9, 1)
    add_ri(R11, 1)
    jmp_l("__ws_fc_loop")
    # 分隔符（空格或引号）：收拢**一整段**，至多产出一个 NUL。
    # cmd 给子进程的命令行与 PowerShell/bash 的形态不同（实测会多一个空参数），
    # 所以这里按"分隔符串"处理而不是"一个字符一个分隔符"。
    a.label("__ws_fc_sep")
    ld(RAX, RSP, 0x40)
    test_rax()
    jcc_l("ne", "__ws_fc_skip1")                # 已经出过分隔符：只吞掉这个字符
    a.emit(b"\x4D\x39\xD0")                     # cmp r8, r10：还没写过任何字符就别写 NUL
    jcc_l("e", "__ws_fc_skip1")
    movi(RCX, 1)
    st(RSP, 0x40, RCX)
    a.emit(b"\x30\xC0")                         # xor al, al（**只能清 al**）
    jmp_l("__ws_fc_put")                        # put 负责存 + 推进源指针
    a.label("__ws_fc_skip1")
    add_ri(R11, 1)
    jmp_l("__ws_fc_loop")
    a.label("__ws_fc_end")
    ld(RAX, RSP, 0x40)
    test_rax()
    jcc_l("ne", "__ws_fc_term")                 # 已经以分隔符收尾了，别再补一个
    a.emit(b"\x30\xC0")                         # xor al, al
    store_al(R8)
    add_ri(R8, 1)
    a.label("__ws_fc_term")
    a.emit(mov_rr(RAX, R8))
    a.emit(b"\x4C\x29\xD0")                     # sub rax, r10
    st(RBX, WS_CMD_LEN, RAX)
    a.label("__ws_fc_done")
    a.emit(b"\x48\x83\xC4\x28\xC3")

    # ---- read(fd, buf, len) ---------------------------------------------
    a.label("__ws_read")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_read_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_read_sock")
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_read_cmd")
    a.label("__ws_read_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x4C\x8D\x4C\x24\x28")             # lea r9, [rsp+0x28]
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("ReadFile")
    ld32(RAX, RSP, 0x28)
    jmp_l("__ws_ret")

    # socket 型 fd：read 落到 recv（Windows 上 ReadFile 读不了 socket 句柄）
    a.label("__ws_read_sock")
    a.call("__ws_sock_of_fd")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d（flags = 0）
    api("recv")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_ret")

    a.label("__ws_read_cmd")
    ld(RDX, RAX, WS_FD_POS)                     # pos
    ld(RCX, RAX, WS_FD_HANDLE)                  # cmd 缓冲地址
    add_rr(RCX, RDX)
    ld(R8, RBX, WS_CMD_LEN)
    a.emit(alu_rr(0x29, R8, RDX))               # 剩余 = 总长 - pos（0x29 = sub r/m64, r64）
    test_rr(R8, R8)
    jcc_l("le", "__ws_rc_zero")
    ld(R9, RSP, 0x38)                           # 调用方要的字节数
    a.emit(b"\x4D\x39\xC8")                     # cmp r8, r9
    jcc_l("be", "__ws_rc_n")
    a.emit(mov_rr(R8, R9))
    a.label("__ws_rc_n")
    st(RSP, 0x40, R8)                           # 本次拷贝字节数
    ld(R10, RSP, 0x30)                          # dst
    a.label("__ws_rc_cp")
    test_rr(R8, R8)
    jcc_l("e", "__ws_rc_done")
    load_al(RCX)
    store_al(R10)
    add_ri(RCX, 1)
    add_ri(R10, 1)
    a.emit(b"\x49\x83\xE8\x01")                 # sub r8, 1
    jmp_l("__ws_rc_cp")
    a.label("__ws_rc_done")
    ld(R8, RSP, 0x40)
    entry_of_fd()                               # rdi 还是 fd
    ld(RCX, RAX, WS_FD_POS)
    add_rr(RCX, R8)
    st(RAX, WS_FD_POS, RCX)
    a.emit(mov_rr(RAX, R8))
    jmp_l("__ws_ret")
    a.label("__ws_rc_zero")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ---- newfstatat(dirfd, path, stb, flags) -----------------------------
    # 消费方只读 st_mode（`load32(stb, 24) & S_IFMT`），所以只填这一个字段。
    # ---- 内部：FILETIME(100ns since 1601) -> Unix 秒（rax 进/出）----------
    a.label("__ws_ft2unix")
    movi(RCX, 116444736000000000)
    sub_rr(RAX, RCX)
    movi(RCX, 10000000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")                  # div rcx
    ret_()

    # ---- 内部：填 struct stat。in: rdi = buf, rsi = WIN32_FILE_ATTRIBUTE_DATA --
    # 布局（x86-64 Linux）：st_nlink@16, st_mode@24, st_uid@28, st_gid@32,
    # st_size@48, st_blksize@56, st_blocks@64, atime@72, mtime@88, ctime@104。
    a.label("__ws_stat_fill")
    a.emit(mov_rr(R11, RDI))
    movi32(RCX, 0)
    a.emit(b"\x31\xC0")
    a.label("__ws_sf_zero")
    store_al(R11)
    add_ri(R11, 1)
    add_ri(RCX, 1)
    a.emit(b"\x48\x81\xF9" + i32(144))       # cmp rcx, 144
    jcc_l("b", "__ws_sf_zero")
    ld32(RAX, RSI, 0)                        # dwFileAttributes
    movi32(RCX, 0x81A4)                      # S_IFREG | 0644
    a.emit(b"\xA9\x10\x00\x00\x00")          # test eax, FILE_ATTRIBUTE_DIRECTORY
    jcc_l("e", "__ws_sf_mode")
    movi32(RCX, 0x41ED)                      # S_IFDIR | 0755
    a.label("__ws_sf_mode")
    st32(RDI, 24, RCX)
    movi32(RAX, 1)
    st(RDI, 16, RAX)                         # st_nlink = 1
    ld32(RAX, RSI, 28)                       # sizeHigh
    a.emit(b"\x48\xC1\xE0\x20")              # shl rax, 32
    ld32(RCX, RSI, 32)                       # sizeLow
    a.emit(b"\x48\x09\xC8")                  # or rax, rcx
    st(RDI, 48, RAX)
    movi32(RAX, 4096)
    st(RDI, 56, RAX)                         # st_blksize
    ld(RAX, RDI, 48)
    movi(RCX, 511)
    add_rr(RAX, RCX)
    movi(RCX, 512)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")                  # div rcx
    st(RDI, 64, RAX)                         # st_blocks
    ld(RAX, RSI, 12)                         # ftLastAccessTime
    a.call("__ws_ft2unix")
    st(RDI, 72, RAX)
    ld(RAX, RSI, 20)                         # ftLastWriteTime
    a.call("__ws_ft2unix")
    st(RDI, 88, RAX)
    ld(RAX, RSI, 4)                          # ftCreationTime
    a.call("__ws_ft2unix")
    st(RDI, 104, RAX)
    ret_()

    # ---- newfstatat(dirfd, path, stb, flags) ----------------------------
    a.label("__ws_fstatat")
    st(RSP, 0x28, RDX)                       # stb
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_fstat_fd")
    load_al(RSI)
    test_al()
    jcc_l("e", "__ws_fstat_fd")
    movi(R8, PE_STATE_VA + WS_TMP)
    a.emit(mov_rr(R9, RSI))
    a.label("__ws_fs_tr")
    load_al(R9)
    cmp_al(47)
    jcc_l("ne", "__ws_fs_st")
    a.emit(b"\xB0" + bytes([92]))
    a.label("__ws_fs_st")
    store_al(R8)
    test_al()
    jcc_l("e", "__ws_fs_done")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_fs_tr")
    a.label("__ws_fs_done")
    movi(RCX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x31\xD2")                      # GetFileExInfoStandard = 0
    lea_rsp(R8, 0x30)
    api("GetFileAttributesExA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RDI, RSP, 0x28)
    lea_rsp(RSI, 0x30)
    a.call("__ws_stat_fill")
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_fstat_fd")
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    lea_rsp(RDX, 0x30)                       # BY_HANDLE_FILE_INFORMATION
    api("GetFileInformationByHandle")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    # 拼一个 WIN32_FILE_ATTRIBUTE_DATA 形状（36 字节）给 __ws_stat_fill。
    ld32(RAX, RSP, 0x30)
    st32(RSP, 0x68, RAX)                     # attrs
    ld(RAX, RSP, 0x34)
    st(RSP, 0x6C, RAX)                       # creation
    ld(RAX, RSP, 0x3C)
    st(RSP, 0x74, RAX)                       # access
    ld(RAX, RSP, 0x44)
    st(RSP, 0x7C, RAX)                       # write
    ld32(RAX, RSP, 0x50)
    st32(RSP, 0x84, RAX)                     # sizeHigh
    ld32(RAX, RSP, 0x54)
    st32(RSP, 0x88, RAX)                     # sizeLow
    ld(RDI, RSP, 0x28)
    lea_rsp(RSI, 0x68)
    a.call("__ws_stat_fill")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- stat / lstat / fstat -------------------------------------------
    a.label("__ws_stat")
    a.emit(mov_rr(RDX, RSI))                 # stb
    a.emit(mov_rr(RSI, RDI))                 # path
    movi(RDI, -100)                          # AT_FDCWD
    a.emit(b"\x45\x31\xD2")                  # xor r10d, r10d
    jmp_l("__ws_fstatat")
    a.label("__ws_lstat")
    jmp_l("__ws_stat")                       # Windows 不区分符号链接
    a.label("__ws_fstat")
    a.emit(mov_rr(RDX, RSI))                 # stb
    a.emit(b"\x31\xF6")                      # xor esi, esi（path = NULL -> 按 fd）
    jmp_l("__ws_fstatat")

    # ---- statx(dirfd, path, flags, mask, buf) ---------------------------
    a.label("__ws_statx")
    st(RSP, 0x28, R8)                        # buf
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_einval")
    movi(R8, PE_STATE_VA + WS_TMP)
    a.emit(mov_rr(R9, RSI))
    a.label("__ws_sx_tr")
    load_al(R9)
    cmp_al(47)
    jcc_l("ne", "__ws_sx_st")
    a.emit(b"\xB0" + bytes([92]))
    a.label("__ws_sx_st")
    store_al(R8)
    test_al()
    jcc_l("e", "__ws_sx_done")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_sx_tr")
    a.label("__ws_sx_done")
    movi(RCX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x31\xD2")
    lea_rsp(R8, 0x30)
    api("GetFileAttributesExA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RDI, RSP, 0x28)
    a.emit(mov_rr(R11, RDI))
    movi32(RCX, 0)
    a.emit(b"\x31\xC0")
    a.label("__ws_sx_zero")
    store_al(R11)
    add_ri(R11, 1)
    add_ri(RCX, 1)
    a.emit(b"\x48\x81\xF9" + i32(256))       # cmp rcx, 256
    jcc_l("b", "__ws_sx_zero")
    movi32(RAX, 0x7FF)                       # FS_STATX_BASIC_STATS
    st32(RDI, 0, RAX)                        # stx_mask
    movi32(RAX, 4096)
    st32(RDI, 4, RAX)                        # stx_blksize
    movi32(RAX, 1)
    st32(RDI, 16, RAX)                       # stx_nlink
    ld32(RAX, RSP, 0x30)
    movi32(RCX, 0x81A4)
    a.emit(b"\xA9\x10\x00\x00\x00")
    jcc_l("e", "__ws_sx_mode")
    movi32(RCX, 0x41ED)
    a.label("__ws_sx_mode")
    st16(RDI, 28, RCX)                       # stx_mode (u16)
    movi32(RAX, 1)
    st(RDI, 32, RAX)                         # stx_ino
    ld32(RAX, RSP, 0x4C)                     # sizeHigh（attr0/creation4/access12/write20/sizeHigh28）
    a.emit(b"\x48\xC1\xE0\x20")
    ld32(RCX, RSP, 0x50)                     # sizeLow
    a.emit(b"\x48\x09\xC8")
    st(RDI, 40, RAX)                         # stx_size
    ld(RAX, RDI, 40)
    movi(RCX, 511)
    add_rr(RAX, RCX)
    movi(RCX, 512)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")
    st(RDI, 48, RAX)                         # stx_blocks
    ld(RAX, RSP, 0x3C)                       # access time at data+12 = 0x3C
    a.call("__ws_ft2unix")
    st(RDI, 64, RAX)                         # stx_atime.sec
    ld(RAX, RSP, 0x44)                       # write at 0x44
    a.call("__ws_ft2unix")
    st(RDI, 112, RAX)                        # stx_mtime.sec（atime64/btime80/ctime96/mtime112）
    ld(RAX, RSP, 0x34)                       # creation at 0x34
    a.call("__ws_ft2unix")
    st(RDI, 96, RAX)                         # stx_ctime.sec
    xor_eax()
    jmp_l("__ws_ret")

    # ---- pread64 / pwrite64 ---------------------------------------------
    # Linux 语义：**不动文件位置**。Windows 的句柄位置是有状态的，所以这一格
    # "取旧位置 → 定位 → 读/写 → 放回旧位置"；CMD 合成 fd 直接从它的缓冲里拷。
    a.label("__ws_pread")
    st(RSP, 0x30, RSI)                       # buf
    st(RSP, 0x38, RDX)                       # count
    st(RSP, 0x40, R10)                       # offset
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("ne", "__ws_espipe")               # socket 没有"位置"这个概念
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_pre_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_pre_cmd")
    a.label("__ws_pre_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x48, RAX)                       # handle
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, 0)
    lea_rsp(R8, 0x50)
    movi32(R9, 1)                            # FILE_CURRENT
    api("SetFilePointerEx")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x40)
    a.emit(b"\x45\x31\xC0")
    a.emit(b"\x45\x31\xC9")                  # FILE_BEGIN
    api("SetFilePointerEx")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    lea_rsp(R9, 0x58)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("ReadFile")
    st32(RSP, 0x60, RAX)
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x50)
    a.emit(b"\x45\x31\xC0")
    movi32(R9, 0)                            # FILE_BEGIN
    api("SetFilePointerEx")
    ld32(RAX, RSP, 0x60)
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld32(RAX, RSP, 0x58)
    jmp_l("__ws_ret")

    a.label("__ws_pre_cmd")
    ld(RCX, RBX, WS_CMD_LEN)
    ld(RDX, RSP, 0x40)
    a.emit(alu_rr(0x29, RCX, RDX))           # remaining = len - off
    test_rr(RCX, RCX)
    jcc_l("le", "__ws_rc_zero")
    ld(R8, RSP, 0x38)
    a.emit(b"\x4C\x39\xC8")                  # cmp r8, rcx
    jcc_l("be", "__ws_pre_cn")
    a.emit(mov_rr(R8, RCX))
    a.label("__ws_pre_cn")
    st(RSP, 0x68, R8)
    ld(R9, RAX, WS_FD_HANDLE)                # cmd 缓冲基址
    add_rr(R9, RDX)
    ld(R10, RSP, 0x30)
    a.label("__ws_pre_cc")
    test_rr(R8, R8)
    jcc_l("e", "__ws_pre_cd")
    load_al(R9)
    store_al(R10)
    add_ri(R9, 1)
    add_ri(R10, 1)
    sub_ri(R8, 1)
    jmp_l("__ws_pre_cc")
    a.label("__ws_pre_cd")
    ld(RAX, RSP, 0x68)
    jmp_l("__ws_ret")

    a.label("__ws_pwrite")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    st(RSP, 0x40, R10)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("ne", "__ws_espipe")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_pwr_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_fail")                  # 合成 fd 只读
    a.label("__ws_pwr_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x48, RAX)
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, 0)
    lea_rsp(R8, 0x50)
    movi32(R9, 1)
    api("SetFilePointerEx")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x40)
    a.emit(b"\x45\x31\xC0")
    a.emit(b"\x45\x31\xC9")
    api("SetFilePointerEx")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    lea_rsp(R9, 0x58)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("WriteFile")
    st32(RSP, 0x60, RAX)
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x50)
    a.emit(b"\x45\x31\xC0")
    movi32(R9, 0)
    api("SetFilePointerEx")
    ld32(RAX, RSP, 0x60)
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld32(RAX, RSP, 0x58)
    jmp_l("__ws_ret")

    # ---- readv / writev ---------------------------------------------------
    # 逐个 iovec 直接落到 ReadFile/WriteFile/send/recv：**不摊平**。没有 64 KiB 上限，
    # 短读/短写就地停（与 Linux 一样）。下标放进帧里的槽（0x80）：Windows API 是
    # caller-saved，r9 跨调用靠不住。
    a.label("__ws_writev")
    st(RSP, 0x30, RSI)                       # iov
    st(RSP, 0x38, RDX)                       # iovcnt
    a.emit(b"\x31\xC0")
    st(RSP, 0x50, RAX)                       # total = 0
    st(RSP, 0x80, RAX)                       # i = 0
    a.call("__ws_sock_of_fd")
    st(RSP, 0x40, RAX)                       # socket handle（0 = 不是 socket）
    test_rax()
    jcc_l("ne", "__ws_wv_loop")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_wv_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_fail")                  # 合成 fd 只读
    a.label("__ws_wv_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x48, RAX)                       # handle
    a.label("__ws_wv_loop")
    ld(R9, RSP, 0x80)
    ld(RCX, RSP, 0x38)
    a.emit(b"\x49\x39\xC9")                  # cmp r9, rcx
    jcc_l("ae", "__ws_wv_done")
    ld(RSI, RSP, 0x30)
    a.emit(mov_rr(R11, R9))
    imul_ri(R11, 16)
    add_rr(R11, RSI)
    ld(RCX, R11, 8)                          # len
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_wv_next")
    ld(R10, R11, 0)                          # base
    st(RSP, 0x58, RCX)
    st(RSP, 0x60, R10)
    ld(RAX, RSP, 0x40)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_wv_fw")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x60)
    ld(R8, RSP, 0x58)
    a.emit(b"\x45\x31\xC9")
    api("send")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_wv_got")
    a.label("__ws_wv_fw")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x60)
    ld(R8, RSP, 0x58)
    lea_rsp(R9, 0x68)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("WriteFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_wv_werr")
    ld32(RAX, RSP, 0x68)
    a.label("__ws_wv_got")
    st(RSP, 0x70, RAX)                       # n
    ld(RCX, RSP, 0x50)
    add_rr(RCX, RAX)
    st(RSP, 0x50, RCX)
    ld(RCX, RSP, 0x58)
    a.emit(b"\x48\x39\xC8")                  # cmp rax, rcx（短写就停）
    jcc_l("b", "__ws_wv_done")
    a.label("__ws_wv_next")
    ld(R9, RSP, 0x80)
    add_ri(R9, 1)
    st(RSP, 0x80, R9)
    jmp_l("__ws_wv_loop")
    a.label("__ws_wv_werr")
    ld(RAX, RSP, 0x50)
    test_rr(RAX, RAX)
    jcc_l("ne", "__ws_wv_done")              # 已经写了一部分：照 write 的规矩返回它
    jmp_l("__ws_fail")
    a.label("__ws_wv_done")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")

    a.label("__ws_readv")
    st(RSP, 0x30, RSI)                       # iov
    st(RSP, 0x38, RDX)                       # iovcnt
    a.emit(b"\x31\xC0")
    st(RSP, 0x50, RAX)                       # total
    st(RSP, 0x78, RAX)                       # cmd 标志
    st(RSP, 0x80, RAX)                       # i
    a.call("__ws_sock_of_fd")
    st(RSP, 0x40, RAX)
    test_rax()
    jcc_l("ne", "__ws_rv_loop")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_rv_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("ne", "__ws_rv_file")
    movi32(RCX, 1)
    st(RSP, 0x78, RCX)
    jmp_l("__ws_rv_loop")
    a.label("__ws_rv_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x48, RAX)
    a.label("__ws_rv_loop")
    ld(R9, RSP, 0x80)
    ld(RCX, RSP, 0x38)
    a.emit(b"\x49\x39\xC9")
    jcc_l("ae", "__ws_rv_done")
    ld(RSI, RSP, 0x30)
    a.emit(mov_rr(R11, R9))
    imul_ri(R11, 16)
    add_rr(R11, RSI)
    ld(RCX, R11, 8)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_rv_next")
    ld(R10, R11, 0)
    st(RSP, 0x58, RCX)                       # len
    st(RSP, 0x60, R10)                       # base
    ld(RAX, RSP, 0x40)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_rv_nosock")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x60)
    ld(R8, RSP, 0x58)
    a.emit(b"\x45\x31\xC9")
    api("recv")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_rv_got")
    a.label("__ws_rv_nosock")
    ld(RAX, RSP, 0x78)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_rv_fr")
    a.emit(mov_rr(RAX, RDI))
    entry_of_fd()
    ld(RCX, RAX, WS_FD_POS)
    ld(R8, RAX, WS_FD_HANDLE)
    add_rr(R8, RCX)
    ld(R9, RBX, WS_CMD_LEN)
    sub_rr(R9, RCX)
    test_rr(R9, R9)
    jcc_l("le", "__ws_rv_zero")
    ld(RCX, RSP, 0x58)
    a.emit(b"\x4C\x39\xC9")                  # cmp rcx, r9
    jcc_l("be", "__ws_rv_cn")
    a.emit(mov_rr(RCX, R9))
    a.label("__ws_rv_cn")
    st(RSP, 0x58, RCX)                       # n
    ld(R10, RSP, 0x60)
    a.label("__ws_rv_cp")
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_rv_cpd")
    load_al(R8)
    store_al(R10)
    add_ri(R8, 1)
    add_ri(R10, 1)
    sub_ri(RCX, 1)
    jmp_l("__ws_rv_cp")
    a.label("__ws_rv_cpd")
    ld(R9, RSP, 0x58)
    entry_of_fd()
    ld(RCX, RAX, WS_FD_POS)
    add_rr(RCX, R9)
    st(RAX, WS_FD_POS, RCX)
    a.emit(mov_rr(RAX, R9))
    jmp_l("__ws_rv_got")
    a.label("__ws_rv_zero")
    xor_eax()
    jmp_l("__ws_rv_got")
    a.label("__ws_rv_fr")
    ld(RCX, RSP, 0x48)
    ld(RDX, RSP, 0x60)
    ld(R8, RSP, 0x58)
    lea_rsp(R9, 0x68)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("ReadFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld32(RAX, RSP, 0x68)
    a.label("__ws_rv_got")
    st(RSP, 0x70, RAX)                       # n
    ld(RCX, RSP, 0x50)
    add_rr(RCX, RAX)
    st(RSP, 0x50, RCX)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_rv_done")               # EOF
    ld(RCX, RSP, 0x58)
    a.emit(b"\x48\x39\xC8")                  # cmp rax, rcx（短读就停）
    jcc_l("b", "__ws_rv_done")
    a.label("__ws_rv_next")
    ld(R9, RSP, 0x80)
    add_ri(R9, 1)
    st(RSP, 0x80, R9)
    jmp_l("__ws_rv_loop")
    a.label("__ws_rv_done")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")


    # ---- flock -----------------------------------------------------------
    # LOCK_SH / LOCK_EX / LOCK_NB / LOCK_UN → LockFileEx / UnlockFileEx。
    # 锁整段 4 GiB（Windows 的 low/high 各 0xFFFFFFFF），与 "锁整个文件" 同义。
    a.label("__ws_flock")
    st(RSP, 0x30, RSI)                       # op
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)                       # h
    a.emit(b"\x31\xC0")
    st(RSP, 0x40, RAX)
    st(RSP, 0x48, RAX)
    st(RSP, 0x50, RAX)
    st(RSP, 0x58, RAX)                       # OVERLAPPED = 0
    ld(RDX, RSP, 0x30)
    a.emit(b"\xF7\xC2\x08\x00\x00\x00")      # test edx, LOCK_UN
    jcc_l("ne", "__ws_flock_un")
    a.emit(b"\x45\x31\xC0")                  # flags = 0
    a.emit(b"\xF7\xC2\x02\x00\x00\x00")      # LOCK_EX?
    jcc_l("e", "__ws_flock_nb")
    movi32(R8, 2)                            # LOCKFILE_EXCLUSIVE_LOCK
    a.label("__ws_flock_nb")
    a.emit(b"\xF7\xC2\x04\x00\x00\x00")      # LOCK_NB?
    jcc_l("e", "__ws_flock_go")
    a.emit(b"\x41\x83\xC8\x01")              # or r8d, LOCKFILE_FAIL_IMMEDIATELY
    a.label("__ws_flock_go")
    ld(RCX, RSP, 0x38)
    a.emit(mov_rr(RDX, R8))
    a.emit(b"\x45\x31\xC0")
    movi32(R9, -1)
    movi32(RAX, -1)
    st(RSP, 0x20, RAX)
    lea_rsp(RAX, 0x40)
    st(RSP, 0x28, RAX)
    api("LockFileEx")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_flock_un")
    ld(RCX, RSP, 0x38)
    a.emit(b"\x31\xD2")
    movi32(R8, -1)
    movi32(R9, -1)
    lea_rsp(RAX, 0x40)
    st(RSP, 0x20, RAX)
    api("UnlockFileEx")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- link / symlink / readlink --------------------------------------
    a.label("__ws_link")
    st(RSP, 0x30, RSI)                       # 新路径
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")                # 旧路径 → TMP
    ld(RSI, RSP, 0x30)
    movi(R10, PE_STATE_VA + WS_TMP2)
    a.call("__ws_translate2")                # 新路径 → TMP2
    movi(RCX, PE_STATE_VA + WS_TMP2)
    movi(RDX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x45\x31\xC0")
    api("CreateHardLinkA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_symlink")
    st(RSP, 0x30, RSI)
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")                # 目标 → TMP
    ld(RSI, RSP, 0x30)
    movi(R10, PE_STATE_VA + WS_TMP2)
    a.call("__ws_translate2")                # 链接路径 → TMP2
    movi(RCX, PE_STATE_VA + WS_TMP2)
    movi(RDX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x45\x31\xC0")
    api("CreateSymbolicLinkA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # readlink 用 GetFinalPathNameByHandleA：拿到的是**最终**目标（会解穿整条链），
    # 不是中间一跳；再剥掉 Win32 的 `\\?\` 前缀。这是 Windows 能给出的最接近的了。
    a.label("__ws_readlink")
    st(RSP, 0x30, RSI)                       # buf
    st(RSP, 0x38, RDX)                       # size
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x31\xD2")
    a.emit(b"\x41\xB8\x03\x00\x00\x00")
    a.emit(b"\x45\x31\xC9")
    movi32(RAX, 3)                           # OPEN_EXISTING
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x02000000)                  # FILE_FLAG_BACKUP_SEMANTICS
    st(RSP, 0x28, RAX)
    movi32(RAX, 0)
    st(RSP, 0x30, RAX)
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")
    jcc_l("e", "__ws_fail")
    st(RSP, 0x40, RAX)
    a.emit(mov_rr(RCX, RAX))
    movi(RDX, PE_STATE_VA + WS_TMP2)
    movi32(R8, 4096)
    a.emit(b"\x45\x31\xC9")
    api("GetFinalPathNameByHandleA")
    st(RSP, 0x48, RAX)                       # 长度
    ld(RCX, RSP, 0x40)
    api("CloseHandle")
    ld(RCX, RSP, 0x48)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_fail")
    movi(R8, PE_STATE_VA + WS_TMP2)
    a.emit(mov_rr(RAX, R8))
    a.emit(b"\x81\x38" + i32(0x5C3F5C5C))    # cmp dword [rax], "\\?\"
    jcc_l("ne", "__ws_rl_go")
    add_ri(R8, 4)
    sub_ri(RCX, 4)
    a.label("__ws_rl_go")
    st(RSP, 0x50, RCX)                       # 返回长度（Linux 可以是 target 的全长）
    ld(RDX, RSP, 0x38)
    a.emit(alu_rr(0x39, RCX, RDX))           # cmp rcx, rdx
    jcc_l("be", "__ws_rl_cap")
    a.emit(mov_rr(RCX, RDX))
    a.label("__ws_rl_cap")
    ld(R9, RSP, 0x30)
    a.label("__ws_rl_cp")
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_rl_cpd")
    load_al(R8)
    store_al(R9)
    add_ri(R8, 1)
    add_ri(R9, 1)
    sub_ri(RCX, 1)
    jmp_l("__ws_rl_cp")
    a.label("__ws_rl_cpd")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")

    # ---- 所有权 / 权限位 -------------------------------------------------
    # Windows 没有 POSIX 的 uid/gid 与 mode 位：**存在即成功**（与 `chmod` 同一条口径）。
    a.label("__ws_chown")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")
    a.emit(b"\x83\xF8\xFF")
    jcc_l("e", "__ws_enoent")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_lchown")
    jmp_l("__ws_chown")
    a.label("__ws_fchmod")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_fchown")
    jmp_l("__ws_fchmod")
    a.label("__ws_fchmodat")
    a.emit(mov_rr(RSI, RSI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")
    a.emit(b"\x83\xF8\xFF")
    jcc_l("e", "__ws_enoent")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- utime / utimes / utimensat -------------------------------------
    # 内部：unix 秒（rax）→ FILETIME（rax）。
    a.label("__ws_unix2ft")
    movi(RCX, 10000000)
    a.emit(imul_rr(RAX, RCX))
    movi(RCX, 116444736000000000)
    add_rr(RAX, RCX)
    ret_()

    # utimensat(dirfd, path, times, flags)：times 是两条 16 字节 timespec。
    # nsec == UTIME_NOW(0x3FFFFFFF) 取当前时间；UTIME_OMIT(0x3FFFFFFE) 就不动那一项。
    a.label("__ws_utimensat")
    st(RSP, 0x68, RDX)                       # times
    a.emit(mov_rr(RSI, RSI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    movi32(RDX, 0x100)                       # FILE_WRITE_ATTRIBUTES
    a.emit(b"\x41\xB8\x03\x00\x00\x00")
    a.emit(b"\x45\x31\xC9")
    movi32(RAX, 3)
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x02000000)
    st(RSP, 0x28, RAX)
    movi32(RAX, 0)
    st(RSP, 0x30, RAX)
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)                       # h
    a.emit(b"\x45\x31\xC0")                  # atime 指针 = NULL
    a.emit(b"\x45\x31\xC9")                  # mtime 指针 = NULL
    ld(RDX, RSP, 0x68)
    test_rr(RDX, RDX)
    jcc_l("ne", "__ws_ut_at")
    lea_rsp(RCX, 0x50)
    api("GetSystemTimeAsFileTime")
    lea_rsp(R8, 0x50)
    lea_rsp(R9, 0x50)
    jmp_l("__ws_ut_call")
    a.label("__ws_ut_at")
    # ---- atime ----
    ld(RAX, RDX, 0)                          # sec
    ld(RCX, RDX, 8)                          # nsec
    movi(R11, 0x3FFFFFFE)
    a.emit(alu_rr(0x39, RCX, R11))           # cmp rcx, r11
    jcc_l("e", "__ws_ut_mt")                 # OMIT → 指针留 NULL
    movi(R11, 0x3FFFFFFF)
    a.emit(alu_rr(0x39, RCX, R11))
    jcc_l("e", "__ws_ut_atnow")
    a.call("__ws_unix2ft")
    st(RSP, 0x50, RAX)
    lea_rsp(R8, 0x50)
    jmp_l("__ws_ut_mt")
    a.label("__ws_ut_atnow")
    lea_rsp(RCX, 0x50)
    api("GetSystemTimeAsFileTime")
    lea_rsp(R8, 0x50)
    a.label("__ws_ut_mt")
    ld(RDX, RSP, 0x68)
    ld(RAX, RDX, 16)                         # mtime.sec
    ld(RCX, RDX, 24)
    movi(R11, 0x3FFFFFFE)
    a.emit(alu_rr(0x39, RCX, R11))
    jcc_l("e", "__ws_ut_call")
    movi(R11, 0x3FFFFFFF)
    a.emit(alu_rr(0x39, RCX, R11))
    jcc_l("e", "__ws_ut_mtnow")
    a.call("__ws_unix2ft")
    st(RSP, 0x58, RAX)
    lea_rsp(R9, 0x58)
    jmp_l("__ws_ut_call")
    a.label("__ws_ut_mtnow")
    lea_rsp(RCX, 0x58)
    api("GetSystemTimeAsFileTime")
    lea_rsp(R9, 0x58)
    a.label("__ws_ut_call")
    ld(RCX, RSP, 0x38)
    a.emit(b"\x31\xD2")                      # creation = NULL
    api("SetFileTime")
    st32(RSP, 0x70, RAX)
    ld(RCX, RSP, 0x38)
    api("CloseHandle")
    ld32(RAX, RSP, 0x70)
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # utime(path, struct utimbuf*)：两条 8 字节秒。
    a.label("__ws_utime")
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_utime0")
    ld(RAX, RSI, 0)
    st(RSP, 0x78, RAX)
    a.emit(b"\x31\xC0")
    st(RSP, 0x80, RAX)
    ld(RAX, RSI, 8)
    st(RSP, 0x88, RAX)
    a.emit(b"\x31\xC0")
    st(RSP, 0x90, RAX)
    a.emit(mov_rr(RSI, RDI))                 # utime 的路径在 rdi
    lea_rsp(RDX, 0x78)
    movi32(R10, 0)
    jmp_l("__ws_utimensat")
    a.label("__ws_utime0")
    a.emit(mov_rr(RSI, RDI))
    a.emit(b"\x31\xD2")
    movi32(R10, 0)
    jmp_l("__ws_utimensat")

    # utimes(path, struct timeval[2])：每条 16 字节（sec + usec）。
    a.label("__ws_utimes")
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_utime0")
    ld(RAX, RSI, 0)
    st(RSP, 0x78, RAX)
    a.emit(b"\x31\xC0")
    st(RSP, 0x80, RAX)
    ld(RAX, RSI, 16)
    st(RSP, 0x88, RAX)
    a.emit(b"\x31\xC0")
    st(RSP, 0x90, RAX)
    a.emit(mov_rr(RSI, RDI))
    lea_rsp(RDX, 0x78)
    movi32(R10, 0)
    jmp_l("__ws_utimensat")

    # ---- fallocate / fadvise / sync -------------------------------------
    a.label("__ws_zero_ok")
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_fadvise")
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_fallocate")
    test_rr(RSI, RSI)
    jcc_l("ne", "__ws_fail")                 # mode != 0（打洞/折叠）没有对应物
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_fal_f")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_fail")
    a.label("__ws_fal_f")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x30, RAX)                       # h
    st(RSP, 0x38, RDX)                       # offset
    st(RSP, 0x40, R10)                       # len
    a.emit(mov_rr(RCX, RAX))
    lea_rsp(RDX, 0x48)
    api("GetFileSizeEx")
    ld(RAX, RSP, 0x38)
    ld(RCX, RSP, 0x40)
    add_rr(RAX, RCX)
    ld(RCX, RSP, 0x48)
    a.emit(alu_rr(0x39, RAX, RCX))           # cmp rax, rcx
    jcc_l("be", "__ws_fal_ok")               # 已经在目标长度内
    ld(RCX, RSP, 0x30)
    a.emit(mov_rr(RDX, RAX))
    a.emit(b"\x45\x31\xC0")
    a.emit(b"\x45\x31\xC9")
    api("SetFilePointerEx")
    ld(RCX, RSP, 0x30)
    api("SetEndOfFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    a.label("__ws_fal_ok")
    xor_eax()
    jmp_l("__ws_ret")


    # ---- sendfile(out_fd, in_fd, offset*, count) -------------------------
    # 入必须是文件；出可以是文件或 socket。按 4096 一块搬；`offset*` 给了就先定位、
    # 搬完把新位置写回去（与 Linux 一致：调用后 in fd 的位置就是 offset+返回值）。
    a.label("__ws_sendfile")
    st(RSP, 0x30, RSI)                       # in fd
    st(RSP, 0x38, RDX)                       # offset*
    st(RSP, 0x40, R10)                       # count
    a.call("__ws_sock_of_fd")                # 出是 socket？
    st(RSP, 0x48, RAX)
    test_rax()
    jcc_l("ne", "__ws_sf_outok")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x50, RAX)                       # 出句柄
    a.label("__ws_sf_outok")
    ld(RDI, RSP, 0x30)
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x58, RAX)                       # 入句柄
    ld(RDX, RSP, 0x38)
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_sf_loop0")
    ld(RDX, RDX, 0)                          # *offset
    a.emit(mov_rr(RCX, RAX))
    a.emit(b"\x45\x31\xC0")
    a.emit(b"\x45\x31\xC9")
    api("SetFilePointerEx")
    a.label("__ws_sf_loop0")
    a.emit(b"\x31\xC0")
    st(RSP, 0x60, RAX)                       # total
    a.label("__ws_sf_loop")
    ld(RAX, RSP, 0x40)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_sf_done")
    movi(RCX, 4096)
    a.emit(alu_rr(0x39, RAX, RCX))           # cmp rax, rcx
    jcc_l("be", "__ws_sf_n")
    a.emit(mov_rr(RAX, RCX))
    a.label("__ws_sf_n")
    st(RSP, 0x68, RAX)                       # want
    ld(RCX, RSP, 0x58)
    movi(RDX, PE_STATE_VA + WS_TMP2)
    a.emit(mov_rr(R8, RAX))
    lea_rsp(R9, 0x70)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("ReadFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_sf_err")
    ld32(RAX, RSP, 0x70)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_sf_done")               # EOF
    st(RSP, 0x78, RAX)                       # got
    ld(RAX, RSP, 0x48)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_sf_fw")
    a.emit(mov_rr(RCX, RAX))
    movi(RDX, PE_STATE_VA + WS_TMP2)
    ld(R8, RSP, 0x78)
    a.emit(b"\x45\x31\xC9")
    api("send")
    cmp_eax_m1()
    jcc_l("e", "__ws_sf_err")
    zext_eax()
    jmp_l("__ws_sf_wr")
    a.label("__ws_sf_fw")
    ld(RCX, RSP, 0x50)
    movi(RDX, PE_STATE_VA + WS_TMP2)
    ld(R8, RSP, 0x78)
    lea_rsp(R9, 0x70)
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("WriteFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_sf_err")
    ld32(RAX, RSP, 0x70)
    a.label("__ws_sf_wr")
    ld(RCX, RSP, 0x60)
    add_rr(RCX, RAX)
    st(RSP, 0x60, RCX)
    ld(RCX, RSP, 0x40)
    sub_rr(RCX, RAX)
    st(RSP, 0x40, RCX)
    ld(RCX, RSP, 0x78)
    a.emit(alu_rr(0x39, RAX, RCX))           # cmp rax, rcx（写少了就停）
    jcc_l("b", "__ws_sf_done")
    jmp_l("__ws_sf_loop")
    a.label("__ws_sf_done")
    ld(RDX, RSP, 0x38)
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_sf_ret")
    ld(RCX, RSP, 0x58)
    a.emit(b"\x31\xD2")
    lea_rsp(R8, 0x80)
    movi32(R9, 1)                            # FILE_CURRENT
    api("SetFilePointerEx")
    ld(RDX, RSP, 0x38)
    ld(RAX, RSP, 0x80)
    st(RDX, 0, RAX)
    a.label("__ws_sf_ret")
    ld(RAX, RSP, 0x60)
    jmp_l("__ws_ret")
    a.label("__ws_sf_err")
    ld(RAX, RSP, 0x60)
    test_rr(RAX, RAX)
    jcc_l("ne", "__ws_ret")
    jmp_l("__ws_fail")


    # ---- mremap(old, oldsize, newsize, flags, newaddr) -------------------
    # 只在**匿名**映射上做：分配新块、搬 min(旧,新) 字节、放旧块、改登记表。
    # 文件映射的重映射会丢掉"跟文件同一份存储"这个语义，所以那一种明确拒绝。
    a.label("__ws_mremap")
    st(RSP, 0x30, RDI)                       # old
    st(RSP, 0x38, RSI)                       # oldsize
    st(RSP, 0x40, RDX)                       # newsize
    movi(R8, PE_STATE_VA + WS_MMAP)
    a.emit(b"\x45\x31\xC9")
    a.label("__ws_mr_find")
    a.emit(b"\x41\x81\xF9" + i32(WS_MMAP_MAX))
    jcc_l("ae", "__ws_fail")
    ld(RAX, R8, 0)
    a.emit(alu_rr(0x39, RAX, RDI))
    jcc_l("e", "__ws_mr_found")
    add_ri(R8, WS_MMAP_ENT)
    a.emit(b"\x41\x83\xC1\x01")
    jmp_l("__ws_mr_find")
    a.label("__ws_mr_found")
    st(RSP, 0x50, R8)                        # 表项
    ld(RAX, R8, 16)
    test_rr(RAX, RAX)
    jcc_l("ne", "__ws_fail")                 # 文件映射：拒绝
    a.emit(b"\x31\xC9")                      # lpAddress = NULL
    ld(RDX, RSP, 0x40)
    movi32(R8, 0x3000)
    movi32(R9, 4)
    api("VirtualAlloc")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x58, RAX)
    ld(RCX, RSP, 0x38)
    ld(RDX, RSP, 0x40)
    a.emit(alu_rr(0x39, RCX, RDX))           # cmp rcx, rdx
    jcc_l("be", "__ws_mr_cap")
    a.emit(mov_rr(RCX, RDX))
    a.label("__ws_mr_cap")
    st(RSP, 0x60, RCX)
    ld(R8, RSP, 0x30)                        # src
    ld(R9, RSP, 0x58)                        # dst
    a.label("__ws_mr_cp")
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_mr_cpd")
    load_al(R8)
    store_al(R9)
    add_ri(R8, 1)
    add_ri(R9, 1)
    sub_ri(RCX, 1)
    jmp_l("__ws_mr_cp")
    a.label("__ws_mr_cpd")
    ld(RCX, RSP, 0x30)
    a.emit(b"\x31\xD2")
    movi32(R8, 0x8000)                       # MEM_RELEASE
    api("VirtualFree")
    ld(R8, RSP, 0x50)
    ld(RAX, RSP, 0x58)
    st(R8, 0, RAX)
    ld(RAX, RSP, 0x40)
    st(R8, 8, RAX)
    a.emit(b"\x31\xC9")
    st(R8, 16, RCX)
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")

    # ---- sched_getaffinity / setaffinity --------------------------------
    a.label("__ws_getaffinity")
    test_rr(RDI, RDI)
    jcc_l("ne", "__ws_fail")                 # 只模拟当前进程
    st(RSP, 0x30, RSI)                       # size
    st(RSP, 0x38, RDX)                       # mask
    ld(RCX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x31\xC0")
    a.label("__ws_ga_zero")
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ga_go")
    store_al(R8)
    add_ri(R8, 1)
    sub_ri(RCX, 1)
    jmp_l("__ws_ga_zero")
    a.label("__ws_ga_go")
    movi(RCX, -1)                            # GetCurrentProcess
    lea_rsp(RDX, 0x40)
    lea_rsp(R8, 0x48)
    api("GetProcessAffinityMask")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RAX, RSP, 0x40)
    ld(RCX, RSP, 0x48)
    a.emit(alu_rr(0x21, RAX, RCX))           # and rax, rcx
    a.emit(mov_rr(R9, RAX))
    ld(R8, RSP, 0x38)
    ld(RCX, RSP, 0x30)
    movi(RDX, 8)
    a.emit(alu_rr(0x39, RCX, RDX))           # cmp rcx, rdx
    jcc_l("be", "__ws_ga_cap")
    a.emit(mov_rr(RCX, RDX))
    a.label("__ws_ga_cap")
    a.label("__ws_ga_cp")
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ga_done")
    store_al(R8)                             # al 就是 r9 的低字节
    a.emit(b"\x49\xC1\xE9\x08")              # shr r9, 8
    add_ri(R8, 1)
    sub_ri(RCX, 1)
    jmp_l("__ws_ga_cp")
    a.label("__ws_ga_done")
    movi32(RAX, 8)                           # 本机 Linux 实测返回 8（内核掩码字节数）
    jmp_l("__ws_ret")

    # ---- futex(uaddr, op, val, timeout, uaddr2, val3) -------------------
    a.label("__ws_futex")
    st(RSP, 0x30, RDI)                       # uaddr
    st(RSP, 0x40, RDX)                       # val
    st(RSP, 0x48, R10)                       # timeout*
    st(RSP, 0x50, RSI)                       # op
    a.emit(mov_rr(RAX, RSI))
    and_ri(RAX, 0x7F)                        # 去掉 PRIVATE/CLOCK 标志
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fx_wait")
    cmp_eax(1)
    jcc_l("e", "__ws_fx_wake")
    a.emit(mov_ri_se(RAX, -38))
    jmp_l("__ws_ret")
    a.label("__ws_fx_wait")
    ld32(RAX, RDI, 0)
    a.emit(b"\x39\xD0")                      # cmp eax, edx
    jcc_l("ne", "__ws_eagain")
    movi32(R9, -1)                           # INFINITE
    ld(RCX, RSP, 0x48)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_fx_waitgo")
    ld(RAX, RCX, 0)                          # sec
    movi(R11, 1000)
    a.emit(imul_rr(RAX, R11))
    a.emit(mov_rr(R10, RAX))                 # sec*1000
    ld(RAX, RCX, 8)                          # nsec
    movi(RCX, 1000000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")                  # rax = nsec / 1e6
    add_rr(RAX, R10)
    a.emit(mov_rr(R9, RAX))
    movi(R11, 0xFFFFFFFE)
    a.emit(alu_rr(0x39, R9, R11))
    jcc_l("be", "__ws_fx_waitgo")
    a.emit(mov_rr(R9, R11))
    a.label("__ws_fx_waitgo")
    # 登记一个等待者（表满就不记，唤醒数会少报，但不会挡道）
    movi(R8, PE_STATE_VA + WS_FUTEX)
    a.emit(b"\x45\x31\xC9")
    a.emit(b"\x31\xC0")
    st(RSP, 0x60, RAX)
    a.label("__ws_fx_fe")
    a.emit(b"\x41\x81\xF9" + i32(WS_FUTEX_MAX))
    jcc_l("ae", "__ws_fx_fe_done")
    ld(RAX, R8, 0)
    a.emit(alu_rr(0x39, RAX, RDI))
    jcc_l("e", "__ws_fx_fe_got")
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_fx_fe_take")
    add_ri(R8, WS_FUTEX_ENT)
    a.emit(b"\x41\x83\xC1\x01")
    jmp_l("__ws_fx_fe")
    a.label("__ws_fx_fe_take")
    st(R8, 0, RDI)
    a.label("__ws_fx_fe_got")
    st(RSP, 0x60, R8)
    a.emit(b"\xF0\x49\xFF\x40\x08")          # lock inc qword [r8+8]
    a.label("__ws_fx_fe_done")
    ld(RCX, RSP, 0x30)
    lea_rsp(RDX, 0x40)
    movi32(R8, 4)
    api("WaitOnAddress")
    st32(RSP, 0x64, RAX)
    ld(R8, RSP, 0x60)
    test_rr(R8, R8)
    jcc_l("e", "__ws_fx_nocnt")
    a.emit(b"\xF0\x49\xFF\x48\x08")          # lock dec qword [r8+8]
    a.label("__ws_fx_nocnt")
    ld32(RAX, RSP, 0x64)
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fx_wfail")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_fx_wfail")
    api("GetLastError")
    cmp_eax(1460)                            # ERROR_TIMEOUT
    jcc_l("e", "__ws_etimedout")
    jmp_l("__ws_fail")
    a.label("__ws_fx_wake")
    movi(R8, PE_STATE_VA + WS_FUTEX)
    a.emit(b"\x45\x31\xC9")
    a.label("__ws_fx_wf")
    a.emit(b"\x41\x81\xF9" + i32(WS_FUTEX_MAX))
    jcc_l("ae", "__ws_fx_wzero")
    ld(RAX, R8, 0)
    a.emit(alu_rr(0x39, RAX, RDI))
    jcc_l("e", "__ws_fx_wgot")
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_fx_wzero")              # 空槽之后不会再有条目
    add_ri(R8, WS_FUTEX_ENT)
    a.emit(b"\x41\x83\xC1\x01")
    jmp_l("__ws_fx_wf")
    a.label("__ws_fx_wzero")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_fx_wgot")
    st(RSP, 0x60, R8)                        # 表项
    ld(RAX, R8, 8)                           # 等待者数
    ld(RDX, RSP, 0x40)                       # 想唤醒的个数
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_fx_wzero")
    a.emit(alu_rr(0x39, RAX, RDX))           # cmp rax, rdx
    jcc_l("be", "__ws_fx_wc")
    a.emit(mov_rr(RAX, RDX))
    a.label("__ws_fx_wc")
    st(RSP, 0x58, RAX)                       # woken
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_fx_wzero")
    a.label("__ws_fx_wl")
    ld(R9, RSP, 0x58)
    test_rr(R9, R9)
    jcc_l("e", "__ws_fx_wd")
    ld(RCX, RSP, 0x30)
    api("WakeByAddressSingle")
    ld(R9, RSP, 0x58)
    sub_ri(R9, 1)
    st(RSP, 0x58, R9)
    jmp_l("__ws_fx_wl")
    a.label("__ws_fx_wd")
    ld(RAX, RSP, 0x58)
    ld(R8, RSP, 0x60)
    a.emit(b"\xF0\x49\x29\x40\x08")          # lock sub qword [r8+8], rax
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")

    # ---- getrandom(buf, len, flags) -------------------------------------
    a.label("__ws_getrandom")
    st(RSP, 0x30, RSI)
    a.emit(mov_rr(RCX, RDI))
    a.emit(mov_rr(RDX, RSI))
    api("SystemFunction036")
    a.emit(b"\x84\xC0")                      # test al, al
    jcc_l("e", "__ws_fail")
    ld(RAX, RSP, 0x30)
    jmp_l("__ws_ret")

    # ---- ioctl(fd, req, arg) --------------------------------------------
    a.label("__ws_ioctl")
    cmp_esi(0x5413)                          # TIOCGWINSZ
    jcc_l("e", "__ws_ioctl_winsz")
    cmp_esi(0x541B)                          # FIONREAD
    jcc_l("e", "__ws_ioctl_fion")
    jmp_l("__ws_enotty")
    a.label("__ws_ioctl_winsz")
    st(RSP, 0x30, RDX)                       # arg
    movi32(RCX, -11)                         # STD_OUTPUT_HANDLE
    api("GetStdHandle")
    test_rax()
    jcc_l("e", "__ws_ioctl_def")
    a.emit(mov_rr(RCX, RAX))
    lea_rsp(RDX, 0x40)
    api("GetConsoleScreenBufferInfo")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_ioctl_def")
    # rows = srWindow.Bottom - srWindow.Top + 1（偏移 16 / 12）
    ld16z(RAX, RSP, 0x40 + 16)
    ld16z(RCX, RSP, 0x40 + 12)
    a.emit(alu_rr(0x29, RAX, RCX))
    add_ri(RAX, 1)
    # cols = Right - Left + 1（偏移 14 / 10）
    ld16z(R9, RSP, 0x40 + 14)
    ld16z(R11, RSP, 0x40 + 10)
    a.emit(alu_rr(0x29, R9, R11))
    add_ri(R9, 1)
    jmp_l("__ws_ioctl_fill")
    a.label("__ws_ioctl_def")
    jmp_l("__ws_enotty")                     # 不是控制台：与 Linux 的 ENOTTY 对齐
    a.label("__ws_ioctl_fill")
    ld(RDX, RSP, 0x30)
    st16(RDX, 0, RAX)                        # ws_row
    st16(RDX, 2, R9)                         # ws_col
    a.emit(b"\x31\xC0")
    st16(RDX, 4, RAX)
    st16(RDX, 6, RAX)
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_ioctl_fion")
    st(RSP, 0x30, RDX)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_enotty")
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, 0x4004667F)                  # FIONREAD
    ld(R8, RSP, 0x30)
    api("ioctlsocket")
    a.emit(b"\x85\xC0")
    jcc_l("ne", "__ws_sock_err")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- fcntl(fd, cmd, arg) --------------------------------------------
    a.label("__ws_fcntl")
    a.emit(b"\x85\xF6")                      # test esi, esi（F_DUPFD）
    jcc_l("e", "__ws_fcntl_dup")
    cmp_esi(1030)                            # F_DUPFD_CLOEXEC
    jcc_l("e", "__ws_fcntl_dup")
    cmp_esi(1)                               # F_GETFD
    jcc_l("e", "__ws_fcntl_zero")
    cmp_esi(2)                               # F_SETFD
    jcc_l("e", "__ws_fcntl_zero")
    cmp_esi(4)                               # F_SETFL
    jcc_l("e", "__ws_fcntl_zero")
    cmp_esi(3)                               # F_GETFL
    jcc_l("e", "__ws_fcntl_fl")
    a.emit(mov_ri_se(RAX, -22))              # EINVAL
    jmp_l("__ws_ret")
    a.label("__ws_fcntl_zero")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_fcntl_fl")
    movi32(RAX, 2)                           # O_RDWR：读/写都不拦
    jmp_l("__ws_ret")
    a.label("__ws_fcntl_dup")
    a.emit(mov_rr(R8, RDX))                  # 从 arg 开始找空槽
    a.label("__ws_fcntl_loop")
    a.emit(b"\x41\x81\xF8" + i32(WS_FD_COUNT))
    jcc_l("ae", "__ws_fail")
    a.emit(mov_rr(RAX, R8))
    imul_ri(RAX, WS_FD_SIZE)
    add_rr(RAX, RBX)
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_fcntl_dup2")
    add_ri(R8, 1)
    jmp_l("__ws_fcntl_loop")
    a.label("__ws_fcntl_dup2")
    a.emit(mov_rr(RSI, R8))
    jmp_l("__ws_dup2")


    # ---- 同义号：sigmask 参数在 Windows 上没有对应物，直接丢掉 --------------
    # pselect6(nfds, r, w, e, timespec*, sigmask) → select()；只把 timespec 换 timeval。
    a.label("__ws_pselect6")
    test_rr(R8, R8)
    jcc_l("e", "__ws_select")                # timeout 为 NULL：直接走 select
    ld(RAX, R8, 0)                           # sec
    st(RSP, 0x30, RAX)
    ld(RAX, R8, 8)                           # nsec
    movi(RCX, 1000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")                  # usec = nsec / 1000
    st(RSP, 0x38, RAX)
    lea_rsp(R8, 0x30)
    jmp_l("__ws_select")

    # ppoll(fds, nfds, timespec*, sigmask, size) → poll()（第三参是毫秒，NULL = -1）
    a.label("__ws_ppoll")
    test_rr(RDX, RDX)
    jcc_l("ne", "__ws_pp_tv")
    movi32(RDX, -1)
    jmp_l("__ws_poll")
    a.label("__ws_pp_tv")
    ld(RAX, RDX, 0)                          # sec
    movi(RCX, 1000)
    a.emit(imul_rr(RAX, RCX))
    a.emit(mov_rr(R10, RAX))
    ld(RAX, RDX, 8)                          # nsec
    movi(RCX, 1000000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")
    add_rr(RAX, R10)
    a.emit(mov_rr(RDX, RAX))
    jmp_l("__ws_poll")

    a.label("__ws_epoll_pwait")              # timeout 已经是毫秒
    jmp_l("__ws_epoll_wait")

    a.label("__ws_epoll_pwait2")             # timespec* → 毫秒
    test_rr(R10, R10)
    jcc_l("ne", "__ws_ep2_tv")
    movi32(R10, -1)
    jmp_l("__ws_epoll_wait")
    a.label("__ws_ep2_tv")
    ld(RAX, R10, 0)
    movi(RCX, 1000)
    a.emit(imul_rr(RAX, RCX))
    a.emit(mov_rr(R11, RAX))
    ld(RAX, R10, 8)
    movi(RCX, 1000000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")
    add_rr(RAX, R11)
    a.emit(mov_rr(R10, RAX))
    jmp_l("__ws_epoll_wait")

    a.label("__ws_renameat2")                # flags != 0（NOREPLACE/EXCHANGE）明确拒绝
    test_rr(R8, R8)
    jcc_l("ne", "__ws_fail")
    jmp_l("__ws_renameat")


    # ---- 还能落到已有处理器上的同义号 --------------------------------
    a.label("__ws_creat")                    # creat(path, mode) = open(O_CREAT|W|TRUNC)
    a.emit(mov_rr(RAX, RSI))                 # mode
    movi32(RSI, 0x241)
    a.emit(mov_rr(RDX, RAX))
    jmp_l("__ws_open")

    # mknod / mknodat：只支持普通文件（建出来就是一个空文件），返回 0 而不是 fd。
    a.label("__ws_mknod")
    a.emit(mov_rr(RAX, RSI))                 # mode
    jmp_l("__ws_mknod_common")
    a.label("__ws_mknodat")
    a.emit(mov_rr(RAX, RDX))                 # mode
    a.emit(mov_rr(RDI, RSI))                 # path
    a.label("__ws_mknod_common")
    a.emit(mov_rr(RDX, RAX))
    movi32(RCX, 0xF000)
    a.emit(alu_rr(0x21, RDX, RCX))           # mode & S_IFMT
    movi32(RCX, 0x8000)
    a.emit(alu_rr(0x39, RDX, RCX))
    jcc_l("ne", "__ws_fail")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    movi32(RDX, 0xC0000000)
    a.emit(b"\x41\xB8\x03\x00\x00\x00")
    a.emit(b"\x45\x31\xC9")
    movi32(RAX, 1)                           # CREATE_NEW
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x80)
    st(RSP, 0x28, RAX)
    movi32(RAX, 0)
    st(RSP, 0x30, RAX)
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    api("CloseHandle")
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_fchownat")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_chown")

    a.label("__ws_futimesat")
    a.emit(mov_rr(RDI, RSI))
    a.emit(mov_rr(RSI, RDX))
    jmp_l("__ws_utimes")

    a.label("__ws_linkat")
    a.emit(mov_rr(RDI, RSI))
    a.emit(mov_rr(RSI, R10))
    jmp_l("__ws_link")

    a.label("__ws_symlinkat")
    a.emit(mov_rr(RSI, RDX))
    jmp_l("__ws_symlink")

    a.label("__ws_readlinkat")
    a.emit(mov_rr(RDI, RSI))
    a.emit(mov_rr(RSI, RDX))
    a.emit(mov_rr(RDX, R10))
    jmp_l("__ws_readlink")

    a.label("__ws_faccessat2")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_access")

    a.label("__ws_epoll_create")             # size 参数是历史遗留，忽略
    a.emit(b"\x31\xFF")
    jmp_l("__ws_epoll_create1")

    a.label("__ws_getcpu")
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_gc_node")
    a.emit(b"\x31\xC0")
    st(RDI, 0, RAX)                          # cpu = 0
    a.label("__ws_gc_node")
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_gc_ok")
    a.emit(b"\x31\xC0")
    st(RSI, 0, RAX)                          # node = 0
    a.label("__ws_gc_ok")
    xor_eax()
    jmp_l("__ws_ret")

    # openat2(dirfd, path, struct open_how*, size)：只认 resolve = 0
    a.label("__ws_openat2")
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_fail")
    ld(RAX, RDX, 16)                         # how.resolve
    test_rr(RAX, RAX)
    jcc_l("ne", "__ws_fail")                 # RESOLVE_* 没有对应物
    ld(RAX, RDX, 0)                          # how.flags
    ld(RCX, RDX, 8)                          # how.mode
    a.emit(mov_rr(RDI, RSI))                 # path
    a.emit(mov_rr(RSI, RAX))
    a.emit(mov_rr(RDX, RCX))
    jmp_l("__ws_open")

    # close_range(first, last, flags)：逐个关掉；flock/句柄都按 fd 表口径收
    a.label("__ws_close_range")
    st(RSP, 0x38, RDI)                       # first
    a.emit(mov_rr(R9, RSI))                  # last
    movi(R10, WS_FD_COUNT)
    a.emit(alu_rr(0x39, R9, R10))            # cmp r9, r10
    jcc_l("b", "__ws_cr_cap")
    a.emit(mov_rr(R9, R10))
    sub_ri(R9, 1)
    a.label("__ws_cr_cap")
    st(RSP, 0x40, R9)
    a.label("__ws_cr_loop")
    ld(R8, RSP, 0x38)
    ld(R9, RSP, 0x40)
    a.emit(alu_rr(0x39, R8, R9))             # cmp r8, r9
    jcc_l("a", "__ws_cr_done")
    a.emit(mov_rr(RAX, R8))
    imul_ri(RAX, WS_FD_SIZE)
    add_rr(RAX, RBX)
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(3)
    jcc_l("b", "__ws_cr_zero")               # 0/1/2 是标准流
    test_ecx()
    jcc_l("e", "__ws_cr_next")               # 空槽
    cmp_ecx(WS_FD_SOCK)
    jcc_l("ne", "__ws_cr_file")
    a.emit(mov_rr(RDI, R8))
    a.call("__ws_sock_drop")                 # socket 要走 closesocket
    jmp_l("__ws_cr_next")
    a.label("__ws_cr_file")
    st(RSP, 0x30, RAX)
    ld(RCX, RAX, WS_FD_HANDLE)
    api("CloseHandle")
    ld(RAX, RSP, 0x30)
    a.label("__ws_cr_zero")
    a.emit(b"\x31\xC9")
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_cr_next")
    ld(R8, RSP, 0x38)
    add_ri(R8, 1)
    st(RSP, 0x38, R8)
    jmp_l("__ws_cr_loop")
    a.label("__ws_cr_done")
    xor_eax()
    jmp_l("__ws_ret")


    a.label("__ws_getresid")                 # getresuid / getresgid：三个 0
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_gr_e")
    a.emit(b"\x31\xC0")
    st(RDI, 0, RAX)
    a.label("__ws_gr_e")
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_gr_s")
    a.emit(b"\x31\xC0")
    st(RSI, 0, RAX)
    a.label("__ws_gr_s")
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_gr_ok")
    a.emit(b"\x31\xC0")
    st(RDX, 0, RAX)
    a.label("__ws_gr_ok")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- lmgr pdh(path_ptr, path_len, out_f64) --------------------------
    # 一次采样开一个查询：open -> add -> collect -> read -> close。PDH 的
    # PDH_FMT_COUNTERVALUE 是 CStatus(DWORD) + 4 字节填充 + union，double 在偏移 8，
    # 所以 value 缓冲给 16 字节、结果从 +8 取。
    a.label("__ws_pdh")
    st(RSP, 0x28, RDX)                          # out
    movi(R8, PE_STATE_VA + WS_TMP)              # dst
    a.emit(mov_rr(R9, RSI))                     # len
    a.emit(mov_rr(R10, RDI))                    # src
    a.label("__ws_pdh_cp")
    test_rr(R9, R9)
    jcc_l("e", "__ws_pdh_cp_done")
    load_al(R10)
    store_al(R8)
    add_ri(R10, 1)
    add_ri(R8, 1)
    sub_ri(R9, 1)
    jmp_l("__ws_pdh_cp")
    a.label("__ws_pdh_cp_done")
    a.emit(b"\x41\xC6\x00\x00")                 # mov byte [r8], 0
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    a.emit(b"\x31\xD2")                         # xor edx, edx
    lea_rsp(R8, 0x30)                           # &query
    api("PdhOpenQueryA")
    a.emit(b"\x85\xC0")                         # test eax, eax
    jcc_l("ne", "__ws_pdh_fail")
    ld(RCX, RSP, 0x30)
    movi(RDX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x45\x31\xC0")                     # xor r8d, r8d
    lea_rsp(R9, 0x38)                           # &counter
    api("PdhAddEnglishCounterA")
    a.emit(b"\x85\xC0")
    jcc_l("ne", "__ws_pdh_e6")
    ld(RCX, RSP, 0x30)
    api("PdhCollectQueryData")
    a.emit(b"\x85\xC0")
    jcc_l("ne", "__ws_pdh_e7")
    ld(RCX, RSP, 0x38)
    movi32(RDX, 0x400)                          # PDH_FMT_LARGE（整数）
    lea_rsp(R8, 0x40)                           # &type
    lea_rsp(R9, 0x48)                           # &value（16 字节）
    api("PdhGetFormattedCounterValue")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_pdh_ok")
    # 速率型计数器首采没有增量：等一个采样间隔再采一次，再取。
    movi32(RCX, 100)
    api("Sleep")
    ld(RCX, RSP, 0x30)
    api("PdhCollectQueryData")
    a.emit(b"\x85\xC0")
    jcc_l("ne", "__ws_pdh_e7")
    ld(RCX, RSP, 0x38)
    movi32(RDX, 0x400)
    lea_rsp(R8, 0x40)
    lea_rsp(R9, 0x48)
    api("PdhGetFormattedCounterValue")
    a.emit(b"\x85\xC0")
    jcc_l("ne", "__ws_pdh_e8")
    a.label("__ws_pdh_ok")
    ld(RCX, RSP, 0x30)
    api("PdhCloseQuery")
    ld(RDX, RSP, 0x28)
    ld(RAX, RSP, 0x50)                          # double 在 value + 8
    a.emit(b"\x48\x89\x02")                     # mov [rdx], rax
    a.emit(b"\x31\xC0")                         # xor eax, eax
    jmp_l("__ws_ret")
    a.label("__ws_pdh_e8")
    a.emit(mov_ri_se(RAX, -8))
    jmp_l("__ws_pdh_close")
    a.label("__ws_pdh_e7")
    a.emit(mov_ri_se(RAX, -7))
    jmp_l("__ws_pdh_close")
    a.label("__ws_pdh_e6")
    a.emit(mov_ri_se(RAX, -6))
    a.label("__ws_pdh_close")
    st(RSP, 0x60, RAX)                          # 保住错误码
    ld(RCX, RSP, 0x30)
    api("PdhCloseQuery")
    ld(RAX, RSP, 0x60)
    jmp_l("__ws_ret")
    a.label("__ws_pdh_fail")
    a.emit(mov_ri_se(RAX, -5))                  # -EIO（连查询都开不了）
    jmp_l("__ws_ret")

    # ---- generic Win32 call(fn_id, args) --------------------------------
    # 四个实参照 Linux 习惯装在 rdi/rsi/rdx/r10，但这里只用 rdi（号）与 rsi（数组）。
    # 装载四个参数到 Windows 的 rcx/rdx/r8/r9，然后按号跳到对应导入槽。
    # 参数少于四个的函数忽略多装的那几个；**五参数函数这个调用器不接**（第五个要上栈）。
    a.label("__ws_win32")
    ld(RCX, RSI, 0)
    ld(RDX, RSI, 8)
    ld(R8, RSI, 16)
    ld(R9, RSI, 24)
    for _id, _name in WIN32_CALLS:
        cmp_edi(_id)
        jcc_l("e", f"__ws_w{_id}")
    a.emit(mov_ri_se(RAX, -38))                 # 表外的号 -> -ENOSYS
    jmp_l("__ws_ret")
    for _id, _name in WIN32_CALLS:
        a.label(f"__ws_w{_id}")
        api(_name)
        jmp_l("__ws_ret")

    # ---- getdents64(fd, buf, n) -----------------------------------------
    # 一次发一条 linux_dirent64（消费方本来就是 while 循环读到 0 为止）。
    a.label("__ws_getdents")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_DIR)
    jcc_l("ne", "__ws_fail")
    st(RSP, 0x40, RAX)                          # &表项
    st(RSP, 0x48, RSI)                          # 调用方缓冲
    ld(RCX, RAX, WS_FD_POS)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_gd_emit")                  # pos=0：FindFirstFileA 那条还没发
    ld(RCX, RAX, WS_FD_HANDLE)
    movi(RDX, PE_STATE_VA + WS_FIND)
    api("FindNextFileA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_gd_end")
    a.label("__ws_gd_emit")
    ld(RAX, RSP, 0x40)
    movi(RCX, 1)
    st(RAX, WS_FD_POS, RCX)
    movi(R8, PE_STATE_VA + WS_TMP2)                # 记录缓冲
    movi(R9, 1)
    st(R8, 0, R9)                               # d_ino
    a.emit(b"\x31\xC9")
    st(R8, 8, RCX)                              # d_off
    movi(R10, PE_STATE_VA + WS_FIND + WS_FIND_NAME)
    a.emit(mov_rr(R11, R10))
    a.label("__ws_gd_nl")
    load_al(R11)
    test_al()
    jcc_l("e", "__ws_gd_len")
    add_ri(R11, 1)
    jmp_l("__ws_gd_nl")
    a.label("__ws_gd_len")
    sub_rr(R11, R10)                            # namelen
    movi(RAX, 19)
    add_rr(RAX, R11)
    add_ri(RAX, 1)
    add_ri(RAX, 7)
    and_ri(RAX, -8)                             # d_reclen = align(19+namelen+1, 8)
    st16(R8, 16, RAX)
    st(RSP, 0x50, RAX)
    movi(RDX, PE_STATE_VA + WS_FIND)
    ld32(RDX, RDX, 0)                           # dwFileAttributes
    test_edx(0x10)
    movi32(RCX, 8)                              # DT_REG
    jcc_l("e", "__ws_gd_dt")
    movi32(RCX, 4)                              # DT_DIR
    a.label("__ws_gd_dt")
    a.emit(b"\x41\x88\x48\x12")                 # mov [r8+18], cl
    movi(R9, PE_STATE_VA + WS_TMP2 + 19)
    a.label("__ws_gd_cp")
    load_al(R10)
    store_al(R9)
    test_al()
    jcc_l("e", "__ws_gd_cpd")
    add_ri(R10, 1)
    add_ri(R9, 1)
    jmp_l("__ws_gd_cp")
    a.label("__ws_gd_cpd")
    ld(R8, RSP, 0x50)                           # reclen
    movi(R9, PE_STATE_VA + WS_TMP2)
    ld(R10, RSP, 0x48)
    st(RSP, 0x58, R8)
    a.label("__ws_gd_out")
    test_rr(R8, R8)
    jcc_l("e", "__ws_gd_outd")
    load_al(R9)
    store_al(R10)
    add_ri(R9, 1)
    add_ri(R10, 1)
    a.emit(b"\x49\x83\xE8\x01")                 # sub r8, 1
    jmp_l("__ws_gd_out")
    a.label("__ws_gd_outd")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_gd_end")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ================================================================ 联网
    # （docs/217）。这一批是**语义仿真**，不是转发 —— 凡两边不同值的都在这里翻译：
    #   * `AF_INET`=2 两边同值、`sockaddr_in`/`sockaddr_in6` 布局逐字节相同 ⇒ 直通；
    #     **`AF_INET6` Linux=10 / Windows=23** ⇒ 进出各补一次 family 字；
    #   * `SOL_SOCKET` 1→0xffff，`SO_*` 各有各的号（见 `WS2_SO`）；`IPPROTO_TCP`=6 与
    #     `TCP_NODELAY`=1 两边同值 ⇒ 直通；
    #   * `SO_RCVTIMEO`/`SO_SNDTIMEO`：Linux 收 `struct timeval{i64 sec; i64 usec}`，
    #     Windows 只认 32 位毫秒 ⇒ 两种量纲都要算（**除法**，`imul_ri` 只吃 imm8）；
    #   * 失败返回**负 errno**（`__ws_wsa_err` 那张表），不是 SOCKET_ERROR；
    #   * `MSG_NOSIGNAL`(0x4000) 掩掉：Windows 没有这一位，那边也没有 SIGPIPE；
    #   * `SOCK_NONBLOCK`(0x800) → ioctlsocket(FIONBIO)，`SOCK_CLOEXEC`(0x80000) 丢掉。
    #   * **点名不做**（用得着就是 -1）：`poll`/`select`/`epoll`、`sendmsg`/`recvmsg`、
    #     `socketpair`、`AF_UNIX`。
    #
    # 两条纪律，错了是静默的错值：
    #   ① Win64 只保 rbx/rbp/rdi/rsi/r12-r15 ⇒ Linux 的实参（rsi/rdx/r10/r8/r9）在**第一次
    #      API 调用前**全部落到栈槽；
    #   ② 这些 case 直接坐在 `__win_syscall` 的序言栈帧上（rsp 16 对齐、0x60 里已含 32 字节
    #      影子空间）⇒ **不再 `sub rsp,0x28`**，第五参就是 [rsp+0x20]。子程序是 `call`
    #      进来的，只有它们才要自己 sub。（子程序用的格子从 0x40 起，落在调用方 0x20 以下。）

    # ---- 内部：WSAStartup 只做一次（不做则第一次 socket() 报 WSANOTINITIALISED）---
    a.label("__ws_wsa_start")
    sub_rsp(0x28)
    ld(RAX, RBX, WS_WSA)
    test_rax()
    jcc_l("ne", "__ws_wsa_done")
    movi32(RCX, 0x0202)                         # MAKEWORD(2, 2)
    movi(RDX, PE_STATE_VA + WS_WSADATA)
    api("WSAStartup")
    movi(RCX, 1)
    st(RBX, WS_WSA, RCX)                        # 失败也立标志：真正的错由后面那次调用报
    a.label("__ws_wsa_done")
    add_rsp28()
    ret_()

    # ---- 内部：WSA 错误码 → 负 errno -------------------------------------
    a.label("__ws_wsa_err")
    sub_rsp(0x28)
    api("WSAGetLastError")
    add_rsp28()
    for _code, _errno in WSA_ERRNO:
        cmp_eax(_code)
        jcc_l("e", f"__ws_wsa_e{_code}")
    movi(RAX, -1)                               # 表外的一律 -1（明写的上限，见 docs/217）
    ret_()
    for _code, _errno in WSA_ERRNO:
        a.label(f"__ws_wsa_e{_code}")
        movi(RAX, -_errno)
        ret_()

    # ---- 内部：fd → SOCKET（不是 socket 型就 0）---------------------------
    a.label("__ws_sock_of_fd")
    cmp_edi(3)
    jcc_l("b", "__ws_sof_no")                   # 0/1/2 是标准流
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_sof_no")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("ne", "__ws_sof_no")
    ld(RAX, RAX, WS_FD_HANDLE)
    ret_()
    a.label("__ws_sof_no")
    xor_eax()
    ret_()

    # ---- 内部：SOCKET（rax）→ 一个 socket 型 fd（eax = fd，rdx = &表项）----
    a.label("__ws_sock_alloc")
    sub_rsp(0x28)
    st(RSP, 0x40, RAX)                          # SOCKET：__ws_alloc_fd 会踩 rax/rdx/rcx
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")                         # test eax, eax
    jcc_l("s", "__ws_sa_full")
    movi(RCX, WS_FD_SOCK)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x40)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)
    add_rsp28()
    ret_()
    a.label("__ws_sa_full")
    movi32(RAX, -1)
    add_rsp28()
    ret_()

    # ---- 内部：关掉一个 socket 型 fd（edi = fd；表项清零）-----------------
    a.label("__ws_sock_drop")
    cmp_edi(3)
    jcc_l("b", "__ws_sd_done")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_sd_done")
    entry_of_fd()
    st(RSP, 0x40, RAX)                          # &表项
    ld(RCX, RAX, WS_FD_HANDLE)
    sub_rsp(0x28)
    api("closesocket")
    add_rsp28()
    ld(RAX, RSP, 0x40)                          # 同一个格子：sub/add 前后 RSP 相同
    a.emit(b"\x31\xC9")
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_sd_done")
    ret_()

    # ---- 内部：sockaddr 的 family 字（AF_INET6：Linux 10 ↔ Windows 23）-----
    # 布局逐字节相同，差别只在这两个字，所以**就地改**、进 API 前后各补一次（单线程，
    # 程序在系统调用期间看不到）。空指针合法（connect/accept 的 addr 可以是 0）⇒ 跳过。
    a.label("__ws_addr_in")                     # rdi = &sockaddr
    xor_eax()
    st(RBX, WS_ADDR_FIX, RAX)                   # 默认"没改过"
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ai_done")
    a.emit(b"\x0F\xB7\x07")                     # movzx eax, word [rdi]
    cmp_eax(LINUX_AF_INET6)
    jcc_l("ne", "__ws_ai_done")
    movi(RCX, WIN_AF_INET6)
    a.emit(b"\x66\x89\x0F")                     # mov word [rdi], cx
    movi(RAX, 1)
    st(RBX, WS_ADDR_FIX, RAX)
    a.label("__ws_ai_done")
    ret_()

    a.label("__ws_addr_restore")                # rdi = &sockaddr
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ar_done")
    ld(RAX, RBX, WS_ADDR_FIX)
    test_rax()
    jcc_l("e", "__ws_ar_done")
    movi(RCX, LINUX_AF_INET6)
    a.emit(b"\x66\x89\x0F")
    a.label("__ws_ar_done")
    ret_()

    a.label("__ws_addr_out")                    # 出口方向：23 → 10
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ao_done")
    a.emit(b"\x0F\xB7\x07")
    cmp_eax(WIN_AF_INET6)
    jcc_l("ne", "__ws_ao_done")
    movi(RCX, LINUX_AF_INET6)
    a.emit(b"\x66\x89\x0F")
    a.label("__ws_ao_done")
    ret_()

    # ---- 公共出口 --------------------------------------------------------
    a.label("__ws_not_sock")
    movi(RAX, -88)                              # ENOTSOCK：给的 fd 不是 socket
    jmp_l("__ws_ret")

    a.label("__ws_sock_err")                    # API 失败 → 负 errno
    a.call("__ws_wsa_err")
    jmp_l("__ws_ret")

    def case_fd1(api_name):
        """listen(50) / shutdown(48)：参数两边同值，直通。"""
        st(RSP, 0x30, RSI)
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        api(api_name)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        xor_eax()
        jmp_l("__ws_ret")

    def case_addr2(api_name):
        """connect(42) / bind(49)：fd, addr, addrlen —— 差别只在 API 名字。"""
        st(RSP, 0x30, RSI)                      # addr
        st(RSP, 0x38, RDX)                      # addrlen
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        st(RSP, 0x40, RAX)                      # SOCKET
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_in")
        ld(RCX, RSP, 0x40)
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        api(api_name)
        st(RSP, 0x48, RAX)                      # 返回值
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_restore")
        ld(RAX, RSP, 0x48)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        xor_eax()
        jmp_l("__ws_ret")

    def case_peername(api_name):
        """getsockname(51) / getpeername(52)：出口方向要补 family 字。"""
        st(RSP, 0x30, RSI)                      # addr（可为 0）
        st(RSP, 0x38, RDX)                      # addrlen 指针
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        api(api_name)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_out")
        xor_eax()
        jmp_l("__ws_ret")

    def case_sendrecv(api6, api4, is_send, tag):
        """sendto(44) / recvfrom(45)：6 参，第 5/6 参走栈（[rsp+0x20]/[rsp+0x28]）。

        **addr 为 NULL 时走 `send`/`recv`**：Linux 上 `sendto(已连接, …, NULL, …)` 就是
        `send`，而 WinSock 的 `sendto` 拿 NULL 加非零 tolen 会报 WSAEFAULT —— 不分流就是
        一个"Linux 能跑、Windows 报错"的暗沟。
        """
        st(RSP, 0x30, RSI)                      # buf
        st(RSP, 0x38, RDX)                      # len
        st(RSP, 0x40, R10)                      # flags
        st(RSP, 0x48, R8)                       # addr
        st(RSP, 0x50, R9)                       # addrlen
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        st(RSP, 0x58, RAX)                      # SOCKET
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        ld(R9, RSP, 0x40)
        a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))                # 掩 MSG_NOSIGNAL
        ld(RAX, RSP, 0x48)
        test_rax()
        jcc_l("e", f"__ws_sr_{tag}_plain")
        st(RSP, 0x20, RAX)                      # 第 5 参 = addr
        ld(RAX, RSP, 0x50)
        st(RSP, 0x28, RAX)                      # 第 6 参 = addrlen
        ld(RDI, RSP, 0x48)
        if is_send:
            a.call("__ws_addr_in")
        api(api6)
        st(RSP, 0x50, RAX)                      # 返回值（0x50 那个 addrlen 已经用完了）
        ld(RDI, RSP, 0x48)
        if is_send:
            a.call("__ws_addr_restore")
        else:
            a.call("__ws_addr_out")
        ld(RAX, RSP, 0x50)
        jmp_l(f"__ws_sr_{tag}_fin")
        a.label(f"__ws_sr_{tag}_plain")         # addr == NULL ⇒ send/recv
        api(api4)
        a.label(f"__ws_sr_{tag}_fin")
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        zext_eax()
        jmp_l("__ws_ret")

    # ---- sendmsg(46) / recvmsg(47) --------------------------------------
    # Linux 的 `msghdr` 与 WinSock 的 `WSAMSG`/`WSABUF` **不是同一份东西**：字段顺序相反
    # （`iovec` 是 {base,len}、`WSABUF` 是 {len,buf}），真要走 `WSASendMsg`/`WSARecvMsg`
    # 还得先 `WSAIoctl(SIO_GET_EXTENSION_FUNCTION_POINTER)` 取扩展函数指针。
    # 这里换一条路：**摊平/散开**（发送逐 iov 直发、接收先收进暂存再散回 iov）。
    # 代价、边界与不做的部分（`msg_control`）写在 `docs/234`。
    a.label("__ws_sendmsg")                     # rdi=fd rsi=msghdr rdx=flags
    st(RSP, 0x30, RSI)                          # msghdr
    st(RSP, 0x38, RDX)                          # flags
    a.call("__ws_sock_of_fd")                   # rdi 不变（助手只写 rax/rcx/rdx）
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)                          # SOCKET
    ld(RCX, RSP, 0x30)
    ld(RDX, RCX, 0)                             # msg_name
    st(RSP, 0x40, RDX)
    ld(RDX, RCX, 8)                             # msg_namelen
    st(RSP, 0x48, RDX)
    ld(RDX, RCX, 16)                            # msg_iov
    st(RSP, 0x50, RDX)
    ld(RDX, RCX, 24)                            # msg_iovlen
    st(RSP, 0x60, RDX)
    xor_eax()
    st(RBX, WS_NETTMP, RAX)                     # j（第几条 iov）
    st(RBX, WS_NETTMP + 8, RAX)                 # iovoff（本条里发到哪了）
    st(RBX, WS_NETTMP + 16, RAX)                # remaining（还没发出去的字节）
    st(RBX, WS_NETTMP + 40, RAX)                # sent（已发总数）
    movi(RCX, 1)
    st(RBX, WS_NETTMP + 48, RCX)                # 首块标志（地址只用在第一块上）
    # remaining = Σ iov.len
    xor_eax()
    st(RSP, 0x68, RAX)
    a.label("__ws_sm_sum")
    ld(RAX, RSP, 0x68)
    ld(RCX, RSP, 0x60)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_sm_fill")
    ld(RCX, RSP, 0x50)
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, 16)
    add_rr(RCX, RDX)
    ld(RDX, RCX, 8)
    ld(RCX, RBX, WS_NETTMP + 16)
    add_rr(RCX, RDX)
    st(RBX, WS_NETTMP + 16, RCX)
    ld(RAX, RSP, 0x68)
    add_ri(RAX, 1)
    st(RSP, 0x68, RAX)
    jmp_l("__ws_sm_sum")
    # ---- 拼一块（最多 WS_IOV_CAP）再发 ---------------------------------
    # **为什么按块而不是按 iov 逐条发**：一次 `sendmsg` 在数据报 socket 上必须是**一个**
    # 数据报 —— 逐 iov 发会把它拆成好几个（实测：第二个 iov 走 `send`，未 connect 的
    # UDP socket 上直接 WSAENOTCONN）。而数据报的上限（65507）小于这里的块大小，
    # 所以"拼够一块就发"对数据报永远是"整个数据报一次发出"，分块只可能发生在流上。
    a.label("__ws_sm_fill")
    ld(RAX, RBX, WS_NETTMP + 16)
    test_rax()
    jcc_l("e", "__ws_sm_done")                  # 一个字节都没有 ⇒ 0（Linux 同）
    xor_eax()
    st(RBX, WS_NETTMP + 24, RAX)                # 本块的字节数 n = 0
    a.label("__ws_sm_floop")
    ld(RAX, RBX, WS_NETTMP + 24)
    movi(RCX, WS_IOV_CAP)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_sm_fldone")               # 块满了
    ld(RAX, RBX, WS_NETTMP)                     # j
    ld(RCX, RSP, 0x60)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_sm_fldone")               # iov 用完了
    ld(RCX, RSP, 0x50)
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, 16)
    add_rr(RCX, RDX)                            # &iov[j]
    st(RBX, WS_NETTMP + 56, RCX)
    ld(RDX, RCX, 8)                             # len_k
    ld(R9, RBX, WS_NETTMP + 8)
    sub_rr(RDX, R9)                             # 本条还剩多少
    test_rr(RDX, RDX)
    jcc_l("ne", "__ws_sm_have")
    xor_eax()
    st(RBX, WS_NETTMP + 8, RAX)                 # 本条空了：iovoff = 0、换下一条
    ld(RAX, RBX, WS_NETTMP)
    add_ri(RAX, 1)
    st(RBX, WS_NETTMP, RAX)
    jmp_l("__ws_sm_floop")
    a.label("__ws_sm_have")                     # rdx = 本条剩余
    ld(RCX, RBX, WS_NETTMP + 24)                # rcx = n
    movi(R9, WS_IOV_CAP)
    sub_rr(R9, RCX)                             # 缓冲还能装多少
    a.emit(alu_rr(0x39, RDX, R9))
    jcc_l("be", "__ws_sm_m")
    a.emit(mov_rr(RDX, R9))
    a.label("__ws_sm_m")                        # rdx = 这一段
    movi(RDI, PE_STATE_VA + WS_IOV_BUF)
    add_rr(RDI, RCX)                            # 目的 = 暂存 + n
    ld(RCX, RBX, WS_NETTMP + 56)                # （n 已经用过了）
    ld(RSI, RCX, 0)                             # iov[j].base
    ld(R9, RBX, WS_NETTMP + 8)
    add_rr(RSI, R9)                             # 源 = base + iovoff
    st(RBX, WS_NETTMP + 64, RDX)
    a.emit(mov_rr(RCX, RDX))
    a.emit(b"\xF3\xA4")                         # rep movsb
    ld(RDX, RBX, WS_NETTMP + 64)
    ld(RCX, RBX, WS_NETTMP + 24)
    add_rr(RCX, RDX)
    st(RBX, WS_NETTMP + 24, RCX)                # n += 这一段
    ld(RCX, RBX, WS_NETTMP + 8)
    add_rr(RCX, RDX)
    st(RBX, WS_NETTMP + 8, RCX)                 # iovoff += 这一段
    ld(RCX, RBX, WS_NETTMP + 16)
    sub_rr(RCX, RDX)
    st(RBX, WS_NETTMP + 16, RCX)                # remaining -= 这一段
    jmp_l("__ws_sm_floop")
    a.label("__ws_sm_fldone")
    xor_eax()
    st(RBX, WS_NETTMP + 32, RAX)                # 这一块已经发出去多少
    # ---- 发：给了地址且是首块就 `sendto`，否则 `send` ---------------------
    a.label("__ws_sm_again")
    movi(RDX, PE_STATE_VA + WS_IOV_BUF)
    ld(RAX, RBX, WS_NETTMP + 32)
    add_rr(RDX, RAX)                            # 缓冲 + 本块已发
    ld(R8, RBX, WS_NETTMP + 24)
    sub_rr(R8, RAX)                             # 本块还剩多少
    ld(RCX, RSP, 0x40)                          # name
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_sm_plain")
    ld(R9, RBX, WS_NETTMP + 48)
    test_rr(R9, R9)
    jcc_l("e", "__ws_sm_plain")
    xor_eax()
    st(RBX, WS_NETTMP + 48, RAX)                # 首块用掉了
    ld(RDI, RSP, 0x40)
    a.call("__ws_addr_in")
    ld(RCX, RSP, 0x58)
    ld(R8, RBX, WS_NETTMP + 24)
    ld(RAX, RBX, WS_NETTMP + 32)
    sub_rr(R8, RAX)
    movi(RDX, PE_STATE_VA + WS_IOV_BUF)
    add_rr(RDX, RAX)
    ld(R9, RSP, 0x38)
    a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))
    ld(RAX, RSP, 0x40)
    st(RSP, 0x20, RAX)                          # 第 5 参 = addr
    ld(RAX, RSP, 0x48)
    st(RSP, 0x28, RAX)                          # 第 6 参 = addrlen
    api("sendto")
    st(RBX, WS_NETTMP + 56, RAX)
    ld(RDI, RSP, 0x40)
    a.call("__ws_addr_restore")
    ld(RAX, RBX, WS_NETTMP + 56)
    jmp_l("__ws_sm_got")
    a.label("__ws_sm_plain")
    ld(RCX, RSP, 0x58)
    ld(R9, RSP, 0x38)
    a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))
    api("send")
    a.label("__ws_sm_got")
    cmp_eax_m1()
    jcc_l("e", "__ws_sm_err")
    zext_eax()
    ld(RCX, RBX, WS_NETTMP + 32)
    add_rr(RCX, RAX)
    st(RBX, WS_NETTMP + 32, RCX)                # 本块已发 += 这次发出去的
    # 本块发完了吗
    ld(RCX, RBX, WS_NETTMP + 24)
    a.emit(alu_rr(0x39, RCX, RAX))              # cmp n, 本块已发
    jcc_l("be", "__ws_sm_nextblk")              # n <= 已发 ⇒ 发完了
    jmp_l("__ws_sm_again")                      # 短写：接着发剩下的
    a.label("__ws_sm_nextblk")
    ld(RCX, RBX, WS_NETTMP + 40)
    ld(RAX, RBX, WS_NETTMP + 32)
    add_rr(RCX, RAX)
    st(RBX, WS_NETTMP + 40, RCX)                # sent += 本块
    jmp_l("__ws_sm_fill")
    a.label("__ws_sm_err")
    # **一个字节都没出去** ⇒ 报错；已经出去过 ⇒ 返回已发字节数（Linux 的短写语义）
    ld(RCX, RBX, WS_NETTMP + 32)
    test_rr(RCX, RCX)
    jcc_l("ne", "__ws_sm_part")
    ld(RCX, RBX, WS_NETTMP + 40)
    test_rr(RCX, RCX)
    jcc_l("ne", "__ws_sm_part")
    a.call("__ws_sock_err")
    jmp_l("__ws_ret")
    a.label("__ws_sm_part")
    ld(RAX, RBX, WS_NETTMP + 40)
    ld(RCX, RBX, WS_NETTMP + 32)
    add_rr(RAX, RCX)
    jmp_l("__ws_ret")
    a.label("__ws_sm_done")
    ld(RAX, RBX, WS_NETTMP + 40)
    jmp_l("__ws_ret")

    a.label("__ws_recvmsg")                     # rdi=fd rsi=msghdr rdx=flags
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)
    ld(RCX, RSP, 0x30)
    ld(RDX, RCX, 0)
    st(RSP, 0x40, RDX)                          # msg_name
    ld(RDX, RCX, 8)
    st(RSP, 0x48, RDX)                          # msg_namelen（调用方给的容量）
    ld(RDX, RCX, 16)
    st(RSP, 0x50, RDX)                          # msg_iov
    ld(RDX, RCX, 24)
    st(RSP, 0x60, RDX)                          # msg_iovlen
    # 收多长：iov 总长，且不超过暂存
    xor_eax()
    st(RSP, 0x68, RAX)                          # k
    st(RSP, 0x70, RAX)                          # total
    a.label("__ws_rm_sum")
    ld(RAX, RSP, 0x68)
    ld(RCX, RSP, 0x60)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_rm_go")
    ld(RCX, RSP, 0x50)
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, 16)
    add_rr(RCX, RDX)
    ld(RDX, RCX, 8)
    ld(RCX, RSP, 0x70)
    add_rr(RCX, RDX)
    st(RSP, 0x70, RCX)
    ld(RAX, RSP, 0x68)
    add_ri(RAX, 1)
    st(RSP, 0x68, RAX)
    jmp_l("__ws_rm_sum")
    a.label("__ws_rm_go")
    ld(RAX, RSP, 0x70)
    movi(RCX, WS_IOV_CAP)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("be", "__ws_rm_cap")
    a.emit(mov_rr(RAX, RCX))
    a.label("__ws_rm_cap")
    st(RBX, WS_NETTMP + 32, RAX)                # 这次最多收这么多
    ld(RCX, RSP, 0x40)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_rm_plain")
    movi32(RCX, 128)                            # sockaddr_storage 够大
    st(RBX, WS_NETTMP + 24, RCX)
    movi(RCX, PE_STATE_VA + WS_NETTMP + 24)
    st(RSP, 0x28, RCX)                          # 第 6 参 = &addrlen
    movi(RCX, PE_STATE_VA + WS_SOCKTMP)
    st(RSP, 0x20, RCX)                          # 第 5 参 = addr
    ld(RCX, RSP, 0x58)
    movi(RDX, PE_STATE_VA + WS_IOV_BUF)
    ld(R8, RBX, WS_NETTMP + 32)
    ld(R9, RSP, 0x38)
    a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))
    api("recvfrom")
    st(RBX, WS_NETTMP + 40, RAX)
    movi(RDI, PE_STATE_VA + WS_SOCKTMP)
    a.call("__ws_addr_out")                     # family 字回 Linux 的值
    jmp_l("__ws_rm_have")
    a.label("__ws_rm_plain")
    ld(RCX, RSP, 0x58)
    movi(RDX, PE_STATE_VA + WS_IOV_BUF)
    ld(R8, RBX, WS_NETTMP + 32)
    ld(R9, RSP, 0x38)
    a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))
    api("recv")
    st(RBX, WS_NETTMP + 40, RAX)
    a.label("__ws_rm_have")
    ld(RAX, RBX, WS_NETTMP + 40)
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    st(RBX, WS_NETTMP + 40, RAX)                # n
    # 散开：n 字节按 iov[] 依次放
    xor_eax()
    st(RSP, 0x68, RAX)                          # k
    st(RSP, 0x70, RAX)                          # off
    a.label("__ws_rm_sc")
    ld(RAX, RSP, 0x70)
    ld(RCX, RBX, WS_NETTMP + 40)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_rm_scd")
    ld(RAX, RSP, 0x68)
    ld(RCX, RSP, 0x60)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_rm_scd")
    ld(RCX, RSP, 0x50)
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, 16)
    add_rr(RCX, RDX)                            # &iov[k]
    ld(RDX, RCX, 8)                             # len_k
    ld(R8, RBX, WS_NETTMP + 40)                 # n
    ld(R9, RSP, 0x70)
    sub_rr(R8, R9)                              # n - off
    a.emit(alu_rr(0x39, RDX, R8))
    jcc_l("be", "__ws_rm_scm")
    a.emit(mov_rr(RDX, R8))
    a.label("__ws_rm_scm")                      # rdx = 这一段拷多少
    st(RBX, WS_NETTMP + 48, RDX)
    st(RBX, WS_NETTMP + 56, RCX)                # &iov[k]（rep 会踩 rcx）
    ld(RDI, RCX, 0)                             # 目的 = iov[k].base
    movi(RSI, PE_STATE_VA + WS_IOV_BUF)
    ld(R9, RSP, 0x70)
    add_rr(RSI, R9)                             # 源 = 暂存 + off
    a.emit(mov_rr(RCX, RDX))
    a.emit(b"\xF3\xA4")                         # rep movsb
    ld(RCX, RBX, WS_NETTMP + 48)
    ld(RAX, RSP, 0x70)
    add_rr(RAX, RCX)
    st(RSP, 0x70, RAX)                          # off += 这一段
    ld(RAX, RSP, 0x68)
    add_ri(RAX, 1)
    st(RSP, 0x68, RAX)                          # k += 1
    jmp_l("__ws_rm_sc")
    a.label("__ws_rm_scd")
    ld(RCX, RSP, 0x30)
    xor_eax()
    st32(RCX, 48, RAX)                          # msg_flags = 0（没有 OOB/TRUNC 可报）
    # 对端地址：WinSock 写进的是**我们的** scratch（`WS_SOCKTMP`），得拷回调用方的
    # `msg_name`，并按调用方给的容量截断（Linux 的 `msg_namelen` 收尾就是"库容不够就截"）。
    ld(RDX, RSP, 0x40)                          # name
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_rm_fin")
    ld(RAX, RBX, WS_NETTMP + 24)                # WinSock 回来的地址长度
    test_rax()
    jcc_l("e", "__ws_rm_nl")
    ld(RCX, RSP, 0x48)                          # 调用方的容量
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("be", "__ws_rm_cp")
    a.emit(mov_rr(RAX, RCX))                    # 太短 ⇒ 拷这么多
    a.label("__ws_rm_cp")
    test_rax()
    jcc_l("e", "__ws_rm_nl")
    st(RBX, WS_NETTMP + 48, RAX)
    a.emit(mov_rr(RDI, RDX))
    movi(RSI, PE_STATE_VA + WS_SOCKTMP)
    a.emit(mov_rr(RCX, RAX))
    a.emit(b"\xF3\xA4")                        # rep movsb
    a.label("__ws_rm_nl")
    ld(RCX, RSP, 0x30)
    ld(RDX, RBX, WS_NETTMP + 24)
    st32(RCX, 8, RDX)                           # msg_namelen = 收下来的地址长度
    a.label("__ws_rm_fin")
    ld(RAX, RBX, WS_NETTMP + 40)
    jmp_l("__ws_ret")

    # ---- socketpair(53) --------------------------------------------------
    # Windows **没有** `socketpair`，而它的 `AF_UNIX` 也不认 Linux 那种路径 —— 所以这里
    # 造的是**回环上的一对真 socket**（STREAM→TCP、DGRAM→两条互 connect 的 UDP）。
    # 怎么做：**调内部调度器**（把 Linux 号与参数摆好 `call __win_syscall`），于是
    # `socket`/`bind`/`getsockname`/`listen`/`connect`/`accept` 全走那几条**已经有两平台
    # 语料钉着的**路径 —— 比在这里手搓一串 WinSock 调用少一大截、也都验过。
    # 与 Linux 的差别写进 `docs/234`（不是 AF_UNIX、没有 SCM_RIGHTS、getsockname 报 AF_INET，
    # 且这一对 fd 不是"最低的两个"）。
    a.label("__ws_socketpair")                  # rdi=domain rsi=type rdx=proto r10=sv
    st(RSP, 0x30, RSI)                          # type
    st(RSP, 0x38, RDX)                          # proto
    st(RSP, 0x40, R10)                          # sv
    xor_eax()
    st(RBX, WS_NETTMP, RAX)                     # listener / a1（0 = 还没建）
    st(RBX, WS_NETTMP + 8, RAX)                 # client / a2
    st(RBX, WS_NETTMP + 16, RAX)                # server（只有 STREAM 有）
    cmp_edi(1)                                  # AF_UNIX
    jcc_l("e", "__ws_sp_dom")
    cmp_edi(2)                                  # AF_INET
    jcc_l("e", "__ws_sp_eopnotsupp")
    cmp_edi(LINUX_AF_INET6)
    jcc_l("e", "__ws_sp_eopnotsupp")
    movi(RAX, -97)                              # EAFNOSUPPORT
    jmp_l("__ws_ret")
    a.label("__ws_sp_eopnotsupp")
    movi(RAX, -95)                              # EOPNOTSUPP：AF_INET 上 Linux 也这么拒
    jmp_l("__ws_ret")
    a.label("__ws_sp_dom")
    ld(RDX, RSP, 0x38)
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_sp_proto0")
    movi(RAX, -93)                              # EPROTONOSUPPORT
    jmp_l("__ws_ret")
    a.label("__ws_sp_proto0")
    ld(RAX, RSP, 0x30)
    a.emit(mov_rr(RCX, RAX))
    a.emit(alu_ri(AND_, RCX, 0xF))              # 低 4 位 = 类型
    st(RBX, WS_NETTMP + 48, RCX)
    a.emit(alu_ri(AND_, RAX, LINUX_SOCK_NONBLOCK))
    st(RBX, WS_NETTMP + 56, RAX)                # 要不要非阻塞（建完再设）
    cmp_ecx(1)
    jcc_l("e", "__ws_sp_tok")
    cmp_ecx(2)
    jcc_l("e", "__ws_sp_tok")
    movi(RAX, -22)                              # EINVAL（**照 Linux 实测**：类型不认识时
                                                # `socketpair` 给的是 EINVAL，不是
                                                # EPROTOTYPE —— 差一个码就是"两边不一致"）
    jmp_l("__ws_ret")
    a.label("__ws_sp_tok")
    cmp_ecx(2)
    jcc_l("e", "__ws_sp_dgram")
    # ================= STREAM：回环 TCP 一对 =================
    movi32(RDI, 2)                              # AF_INET
    movi32(RSI, 1)                              # SOCK_STREAM
    movi32(RDX, 0)
    movi(RAX, SYS_SOCKET)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    st(RBX, WS_NETTMP, RAX)                     # listener
    # sa = 127.0.0.1:0
    movi(RCX, 2)
    st32(RBX, WS_NETTMP + 64, RCX)              # family = AF_INET、port = 0
    movi32(RCX, 0x0100007F)                     # 127.0.0.1（网络序）
    st32(RBX, WS_NETTMP + 68, RCX)
    xor_eax()
    st32(RBX, WS_NETTMP + 72, RAX)
    st32(RBX, WS_NETTMP + 76, RAX)
    # bind(listener, sa, 16)
    ld(RDI, RBX, WS_NETTMP)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi32(RDX, 16)
    movi(RAX, SYS_BIND)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # listen(listener, 1)
    ld(RDI, RBX, WS_NETTMP)
    movi32(RSI, 1)
    movi(RAX, SYS_LISTEN)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # getsockname(listener, sa, &len=16) —— 端口落回 sa，于是 sa 就是"对端地址"
    movi32(RCX, 16)
    st32(RBX, WS_NETTMP + 80, RCX)
    ld(RDI, RBX, WS_NETTMP)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi(RDX, PE_STATE_VA + WS_NETTMP + 80)
    movi(RAX, SYS_GETSOCKNAME)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # client = socket(AF_INET, SOCK_STREAM, 0)
    movi32(RDI, 2)
    movi32(RSI, 1)
    movi32(RDX, 0)
    movi(RAX, SYS_SOCKET)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    st(RBX, WS_NETTMP + 8, RAX)                 # client
    # connect(client, sa, 16)
    ld(RDI, RBX, WS_NETTMP + 8)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi32(RDX, 16)
    movi(RAX, SYS_CONNECT)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # server = accept(listener, NULL, NULL)
    ld(RDI, RBX, WS_NETTMP)
    xor_eax()
    a.emit(mov_rr(RSI, RAX))
    a.emit(mov_rr(RDX, RAX))
    movi(RAX, SYS_ACCEPT)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    st(RBX, WS_NETTMP + 16, RAX)                # server
    # 监听者用完就关（Linux 的 socketpair 也不会留这个 fd）
    ld(RDI, RBX, WS_NETTMP)
    movi(RAX, SYS_CLOSE)
    a.call("__win_syscall")
    xor_eax()
    st(RBX, WS_NETTMP, RAX)                     # 标记"已关"，别在清理时重复关
    jmp_l("__ws_sp_finish")
    # ================= DGRAM：两条互 connect 的 UDP =================
    a.label("__ws_sp_dgram")
    movi32(RDI, 2)
    movi32(RSI, 2)                              # SOCK_DGRAM
    movi32(RDX, 0)
    movi(RAX, SYS_SOCKET)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    st(RBX, WS_NETTMP, RAX)                     # a1
    movi(RCX, 2)
    st32(RBX, WS_NETTMP + 64, RCX)              # a1 的 sa：127.0.0.1:0
    movi32(RCX, 0x0100007F)
    st32(RBX, WS_NETTMP + 68, RCX)
    xor_eax()
    st32(RBX, WS_NETTMP + 72, RAX)
    st32(RBX, WS_NETTMP + 76, RAX)
    ld(RDI, RBX, WS_NETTMP)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi32(RDX, 16)
    movi(RAX, SYS_BIND)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    movi32(RCX, 16)
    st32(RBX, WS_NETTMP + 80, RCX)
    ld(RDI, RBX, WS_NETTMP)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi(RDX, PE_STATE_VA + WS_NETTMP + 80)
    movi(RAX, SYS_GETSOCKNAME)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # a2
    movi32(RDI, 2)
    movi32(RSI, 2)
    movi32(RDX, 0)
    movi(RAX, SYS_SOCKET)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    st(RBX, WS_NETTMP + 8, RAX)                 # a2
    movi(RCX, 2)
    st32(RBX, WS_NETTMP + 96, RCX)              # a2 的 sa
    movi32(RCX, 0x0100007F)
    st32(RBX, WS_NETTMP + 100, RCX)
    xor_eax()
    st32(RBX, WS_NETTMP + 104, RAX)
    st32(RBX, WS_NETTMP + 108, RAX)
    ld(RDI, RBX, WS_NETTMP + 8)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 96)
    movi32(RDX, 16)
    movi(RAX, SYS_BIND)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    movi32(RCX, 16)
    st32(RBX, WS_NETTMP + 80, RCX)
    ld(RDI, RBX, WS_NETTMP + 8)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 96)
    movi(RDX, PE_STATE_VA + WS_NETTMP + 80)
    movi(RAX, SYS_GETSOCKNAME)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    # 互 connect（Linux 的 DGRAM socketpair 也是连着的）
    ld(RDI, RBX, WS_NETTMP)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 96)
    movi32(RDX, 16)
    movi(RAX, SYS_CONNECT)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    ld(RDI, RBX, WS_NETTMP + 8)
    movi(RSI, PE_STATE_VA + WS_NETTMP + 64)
    movi32(RDX, 16)
    movi(RAX, SYS_CONNECT)
    a.call("__win_syscall")
    test_rax()
    jcc_l("s", "__ws_sp_err")
    a.label("__ws_sp_finish")
    # 要非阻塞就把两边都设上（建的时候故意用阻塞的：非阻塞的 connect 立刻返回）
    ld(RAX, RBX, WS_NETTMP + 56)
    test_rax()
    jcc_l("e", "__ws_sp_sv")
    movi(RCX, 1)
    st(RBX, WS_SOCKTMP, RCX)                    # FIONBIO 的 u_long = 1
    ld(RDI, RBX, WS_NETTMP)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_sp_nb2")
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_sp_nb2")                   # 那个 fd 已经关了（listener）
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    a.label("__ws_sp_nb2")
    ld(RDI, RBX, WS_NETTMP + 8)
    a.call("__ws_sock_of_fd")
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    # STREAM 那一格还有 server 这个 fd 也要设
    ld(RDI, RBX, WS_NETTMP + 16)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_sp_sv")
    a.call("__ws_sock_of_fd")
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    a.label("__ws_sp_sv")
    # sv[0] / sv[1]
    ld(RCX, RSP, 0x40)                          # sv
    ld(R9, RBX, WS_NETTMP + 16)
    test_rr(R9, R9)
    jcc_l("e", "__ws_sp_svd")
    ld(RDX, RBX, WS_NETTMP + 8)                 # STREAM：sv[0] = client
    st32(RCX, 0, RDX)
    ld(RDX, RBX, WS_NETTMP + 16)                #        sv[1] = server
    st32(RCX, 4, RDX)
    jmp_l("__ws_sp_svdone")
    a.label("__ws_sp_svd")
    ld(RDX, RBX, WS_NETTMP)                     # DGRAM：sv[0] = a1
    st32(RCX, 0, RDX)
    ld(RDX, RBX, WS_NETTMP + 8)                 #        sv[1] = a2
    st32(RCX, 4, RDX)
    a.label("__ws_sp_svdone")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_sp_err")
    st(RBX, WS_NETTMP + 88, RAX)                # 先记下 errno
    ld(RDI, RBX, WS_NETTMP + 16)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_sp_e1")
    a.call("__ws_sock_drop")
    a.label("__ws_sp_e1")
    ld(RDI, RBX, WS_NETTMP + 8)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_sp_e2")
    a.call("__ws_sock_drop")
    a.label("__ws_sp_e2")
    ld(RDI, RBX, WS_NETTMP)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_sp_e3")
    a.call("__ws_sock_drop")
    a.label("__ws_sp_e3")
    ld(RAX, RBX, WS_NETTMP + 88)
    jmp_l("__ws_ret")

    # ---- epoll(291 / 233 / 232) ------------------------------------------
    # Windows 上**没有 epoll**，也没有"接一下就能用"的东西：IOCP 是完成端口，语义与 epoll
    # 不是一回事（那是"投递异步 IO、等完成"），接进去等于重写整个 IO 模型。所以这里做的是
    # **水平触发**的实现：`epoll_wait` 架在 WinSock `select` 上，登记表在 shim 自己状态区里。
    # 由此三条**写明的差别**（`docs/234`）：
    #   * `EPOLLET`（边沿）**不兑现** —— 当水平触发处理。安全（不会丢事件），
    #     代价是"靠边沿省唤醒"的程序在 Windows 上会多醒几次；
    #   * `EPOLLERR` 只在 WinSock 的 except 集报出来时给出（连接失败那个形状），
    #     `EPOLLHUP` **不合成**（Linux 在对面关闭时会给 HUP，这里探测不到）；
    #   * 返回顺序是**登记顺序**（Linux 也是"没规定顺序"，但同一个程序两边可能看到不同次序）。
    a.label("__ws_epoll_inst")                  # in: edi = fd → rax = 实例指针（0 = 不是 epoll fd）
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(3)
    jcc_l("b", "__ws_ei_no")
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_ei_no")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_EPOLL)
    jcc_l("ne", "__ws_ei_no")
    ld(RAX, RAX, WS_FD_HANDLE)                  # 实例号
    movi(RCX, WS_EPOLL_INST)                    # 1040：imul_ri 只吃 imm8，得用 imul_rr
    a.emit(imul_rr(RAX, RCX))
    movi(RCX, PE_STATE_VA + WS_EPOLL)
    add_rr(RAX, RCX)
    ret_()
    a.label("__ws_ei_no")
    xor_eax()
    ret_()

    a.label("__ws_epoll_create1")               # rdi = flags
    cmp_edi(0)
    jcc_l("e", "__ws_ec_ok")
    cmp_edi(LINUX_EPOLL_CLOEXEC)
    jcc_l("e", "__ws_ec_ok")
    movi(RAX, -22)                              # EINVAL
    jmp_l("__ws_ret")
    a.label("__ws_ec_ok")
    xor_eax()
    st(RSP, 0x38, RAX)                          # 实例号
    a.label("__ws_ec_scan")
    ld(RAX, RSP, 0x38)
    cmp_eax(WS_EPOLL_MAX)
    jcc_l("ae", "__ws_ec_full")
    movi(RCX, WS_EPOLL_INST)
    a.emit(imul_rr(RAX, RCX))
    movi(RCX, PE_STATE_VA + WS_EPOLL)
    add_rr(RAX, RCX)
    ld(RCX, RAX, 0)                             # live
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ec_got")
    ld(RAX, RSP, 0x38)
    add_ri(RAX, 1)
    st(RSP, 0x38, RAX)
    jmp_l("__ws_ec_scan")
    a.label("__ws_ec_full")
    movi(RAX, -24)                              # EMFILE：实例用完（4 个）
    jmp_l("__ws_ret")
    a.label("__ws_ec_got")
    st(RSP, 0x40, RAX)                          # 实例指针
    movi(RCX, 1)
    st(RAX, 0, RCX)                             # live = 1
    ld(RCX, RSP, 0x40)
    xor_eax()
    st(RCX, 8, RAX)                             # count = 0
    a.call("__ws_alloc_fd")                     # rax = fd、rdx = &表项
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_ec_nofd")
    movi(RCX, WS_FD_EPOLL)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x38)
    st(RDX, WS_FD_HANDLE, RCX)                  # handle = 实例号
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)
    jmp_l("__ws_ret")
    a.label("__ws_ec_nofd")
    ld(RCX, RSP, 0x40)
    xor_eax()
    st(RCX, 0, RAX)                             # fd 表满了：把实例还回去
    movi(RAX, -24)                              # EMFILE
    jmp_l("__ws_ret")

    a.label("__ws_epoll_ctl")                   # rdi=epfd rsi=op rdx=fd r10=event
    st(RSP, 0x30, RSI)                          # op
    st(RSP, 0x38, RDX)                          # fd
    st(RSP, 0x40, R10)                          # event
    a.call("__ws_epoll_inst")
    test_rax()
    jcc_l("e", "__ws_ek_badfd")
    st(RSP, 0x48, RAX)                          # 实例
    # **先验 op**（Linux 的顺序：op 不认识就 EINVAL，与那个 fd 登记没登记无关）——
    # 放到后面去会让"op 不认识 + fd 已登记"落进 MOD 分支、返回 0（实测撞出来的）
    ld(RAX, RSP, 0x30)
    cmp_eax(1)
    jcc_l("b", "__ws_ek_opbad")
    cmp_eax(3)
    jcc_l("a", "__ws_ek_opbad")
    jmp_l("__ws_ek_opok")
    a.label("__ws_ek_opbad")
    movi(RAX, -22)                              # EINVAL
    jmp_l("__ws_ret")
    a.label("__ws_ek_opok")
    ld(RDI, RSP, 0x38)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_ek_perm")                  # 不是 socket：EPERM（Linux 同口径）
    # 扫登记表：找到 ⇒ rax = 表项指针
    xor_eax()
    st(RSP, 0x50, RAX)                          # k
    a.label("__ws_ek_loop")
    ld(RAX, RSP, 0x50)
    ld(RCX, RSP, 0x48)
    ld(RCX, RCX, 8)                             # count
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_ek_none")
    imul_ri(RAX, 16)
    ld(RCX, RSP, 0x48)
    add_rr(RAX, RCX)
    add_ri(RAX, 16)                             # &entry[k]
    ld32(RCX, RAX, 0)                           # entry.fd
    ld(RDX, RSP, 0x38)
    a.emit(alu_rr(0x39, RCX, RDX))
    jcc_l("e", "__ws_ek_found")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_ek_loop")
    a.label("__ws_ek_none")                     # 没找到
    ld(RAX, RSP, 0x30)
    cmp_eax(1)
    jcc_l("e", "__ws_ek_addnew")
    movi(RAX, -2)                               # ENOENT（MOD/DEL 一个没登记的 fd）
    jmp_l("__ws_ret")
    a.label("__ws_ek_found")                    # rax = 表项指针
    ld(RCX, RSP, 0x30)
    cmp_ecx(1)
    jcc_l("e", "__ws_ek_exist")                  # ADD 已存在 ⇒ EEXIST
    cmp_ecx(2)
    jcc_l("e", "__ws_ek_remove")
    # MOD：改 events / data
    ld(RCX, RSP, 0x40)                          # event
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ek_fault")
    ld32(RDX, RCX, 0)
    st32(RAX, 4, RDX)
    ld(RDX, RCX, 4)                             # data 在 +4（Linux 的 epoll_event 是 packed）
    st(RAX, 8, RDX)
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_ek_exist")
    movi(RAX, -17)                              # EEXIST
    jmp_l("__ws_ret")
    a.label("__ws_ek_fault")
    movi(RAX, -14)                              # EFAULT（ADD/MOD 没给 event）
    jmp_l("__ws_ret")
    a.label("__ws_ek_perm")
    movi(RAX, -1)                               # EPERM：那个 fd 不是 socket
    jmp_l("__ws_ret")
    a.label("__ws_ek_badfd")
    movi(RAX, -9)                               # EBADF：epfd 不是 epoll 实例
    jmp_l("__ws_ret")
    a.label("__ws_ek_remove")                   # 把最后一条搬到这一格，count--
    ld(RCX, RSP, 0x48)
    ld(RDX, RCX, 8)                             # count
    sub_ri(RDX, 1)
    st(RCX, 8, RDX)
    a.emit(mov_rr(RCX, RDX))
    imul_ri(RCX, 16)
    ld(RDX, RSP, 0x48)
    add_rr(RCX, RDX)
    add_ri(RCX, 16)                             # &entry[count-1]
    ld(RDX, RCX, 0)
    st(RAX, 0, RDX)                             # fd + events 两个 u32 一起搬
    ld(RDX, RCX, 8)
    st(RAX, 8, RDX)                             # data
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_ek_addnew")
    ld(RCX, RSP, 0x48)
    ld(RDX, RCX, 8)
    cmp_edx(WS_EPOLL_SLOTS)
    jcc_l("ae", "__ws_ek_nospc")
    ld(RCX, RSP, 0x40)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ek_fault")
    ld(RDX, RSP, 0x48)
    ld(RDX, RDX, 8)                             # count
    a.emit(mov_rr(RAX, RDX))
    imul_ri(RAX, 16)
    ld(RDX, RSP, 0x48)
    add_rr(RAX, RDX)
    add_ri(RAX, 16)                             # &entry[count]
    ld(RDX, RSP, 0x38)
    st32(RAX, 0, RDX)                           # fd
    ld(RDX, RSP, 0x40)
    ld(R9, RDX, 0)
    st32(RAX, 4, R9)                            # events
    ld(R9, RDX, 4)
    st(RAX, 8, R9)                              # data
    ld(RCX, RSP, 0x48)
    ld(RDX, RCX, 8)
    add_ri(RDX, 1)
    st(RCX, 8, RDX)                             # count++
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_ek_nospc")
    movi(RAX, -28)                              # ENOSPC：这个实例的 64 格用完了
    jmp_l("__ws_ret")

    a.label("__ws_epoll_wait")                  # rdi=epfd rsi=events rdx=maxevents r10=timeout(ms)
    st(RSP, 0x30, RSI)                          # 输出数组
    st(RSP, 0x38, RDX)                          # maxevents
    st(RSP, 0x40, R10)                          # timeout
    a.call("__ws_epoll_inst")
    test_rax()
    jcc_l("e", "__ws_ew_badfd")
    st(RSP, 0x48, RAX)                          # 实例
    ld(RCX, RAX, 8)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ew_empty")                 # 一条登记都没有：Linux 也是"等到超时"
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    a.call("__ws_set_init")                     # 读集
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    a.call("__ws_set_init")                     # 写集
    movi(RDI, PE_STATE_VA + WS_SHADOW_EX)
    a.call("__ws_set_init")                     # 异常集（错误/挂断从这里冒出来）
    xor_eax()
    st(RSP, 0x50, RAX)                          # k
    a.label("__ws_ew_fill")
    ld(RAX, RSP, 0x50)
    ld(RCX, RSP, 0x48)
    ld(RCX, RCX, 8)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_ew_call")
    imul_ri(RAX, 16)
    ld(RCX, RSP, 0x48)
    add_rr(RAX, RCX)
    add_ri(RAX, 16)
    st(RSP, 0x58, RAX)                          # &entry[k]
    ld(RDI, RAX, 0)                             # entry.fd（低 32 位）
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_ew_next")                  # 不是 socket（关掉了）：跳过
    st(RSP, 0x60, RAX)                          # SOCKET
    ld(RCX, RSP, 0x58)
    ld32(RCX, RCX, 4)                           # events 掩码
    st(RSP, 0x68, RCX)
    a.emit(alu_ri(AND_, RCX, LINUX_EPOLL_IN | LINUX_EPOLL_PRI | LINUX_EPOLL_RDHUP))
    test_ecx()
    jcc_l("e", "__ws_ew_wr")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_add")
    a.label("__ws_ew_wr")
    ld(RCX, RSP, 0x68)
    a.emit(alu_ri(AND_, RCX, LINUX_EPOLL_OUT))
    test_ecx()
    jcc_l("e", "__ws_ew_ex")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_add")
    a.label("__ws_ew_ex")
    movi(RDI, PE_STATE_VA + WS_SHADOW_EX)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_add")
    a.label("__ws_ew_next")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_ew_fill")
    a.label("__ws_ew_call")
    ld(RAX, RSP, 0x40)                          # timeout（毫秒；负数 = 无限等）
    test_rax()
    jcc_l("s", "__ws_ew_inf")
    movi(RCX, 1000)
    a.emit(b"\x31\xD2")                         # xor edx, edx
    a.emit(group3(6, RCX))                      # div rcx → rax = 秒、rdx = 毫秒余数
    movi(RCX, PE_STATE_VA + WS_POLLTV)
    st32(RCX, 0, RAX)                           # tv_sec
    movi(R8, 1000)
    a.emit(imul_rr(RDX, R8))
    st32(RCX, 4, RDX)                           # tv_usec
    st(RSP, 0x20, RCX)                          # 第 5 参 = timeval
    jmp_l("__ws_ew_sel")
    a.label("__ws_ew_inf")
    xor_eax()
    st(RSP, 0x20, RAX)                          # NULL = 无限等
    a.label("__ws_ew_sel")
    movi32(RCX, 0)                              # WinSock 不认 nfds
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    movi(R8, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    movi(R9, PE_STATE_VA + WS_SHADOW_EX)
    api("select")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    # 逐条登记看结果：WinSock 会把集合改写成"就绪的那几个"
    xor_eax()
    st(RSP, 0x50, RAX)                          # k
    st(RSP, 0x70, RAX)                          # 已写出的条数
    a.label("__ws_ew_out")
    ld(RAX, RSP, 0x50)
    ld(RCX, RSP, 0x48)
    ld(RCX, RCX, 8)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_ew_done")
    imul_ri(RAX, 16)
    ld(RCX, RSP, 0x48)
    add_rr(RAX, RCX)
    add_ri(RAX, 16)
    st(RSP, 0x58, RAX)                          # &entry[k]
    ld(RDI, RAX, 0)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_ew_onext")
    st(RSP, 0x60, RAX)                          # SOCKET
    xor_eax()
    st(RSP, 0x78, RAX)                          # ev = 0
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_ew_hasr")
    ld(RAX, RSP, 0x78)
    add_ri(RAX, LINUX_EPOLL_IN)
    st(RSP, 0x78, RAX)
    a.label("__ws_ew_hasr")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_ew_hasw")
    ld(RAX, RSP, 0x78)
    add_ri(RAX, LINUX_EPOLL_OUT)
    st(RSP, 0x78, RAX)
    a.label("__ws_ew_hasw")
    movi(RDI, PE_STATE_VA + WS_SHADOW_EX)
    ld(RSI, RSP, 0x60)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_ew_hase")
    ld(RAX, RSP, 0x78)
    add_ri(RAX, LINUX_EPOLL_ERR)
    st(RSP, 0x78, RAX)
    a.label("__ws_ew_hase")
    ld(RAX, RSP, 0x78)
    test_rax()
    jcc_l("e", "__ws_ew_onext")                 # 没就绪
    # 写出：{u32 events; u64 data} —— Linux 的 epoll_event 是 **packed**（12 字节一条）
    ld(RCX, RSP, 0x70)
    ld(RDX, RSP, 0x38)
    a.emit(alu_rr(0x39, RCX, RDX))
    jcc_l("ae", "__ws_ew_done")                 # 输出数组满了：剩下的下次再说
    a.emit(mov_rr(RAX, RCX))
    movi(RDX, 12)
    a.emit(imul_rr(RAX, RDX))                   # 12 字节一条（packed）
    ld(RDX, RSP, 0x30)
    add_rr(RAX, RDX)                            # &events[n]
    ld(RDX, RSP, 0x78)
    st32(RAX, 0, RDX)                           # events
    ld(RCX, RSP, 0x58)
    ld(RDX, RCX, 8)                             # entry.data
    st(RAX, 4, RDX)                             # data 紧跟在 +4
    ld(RAX, RSP, 0x70)
    add_ri(RAX, 1)
    st(RSP, 0x70, RAX)                          # n++
    a.label("__ws_ew_onext")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_ew_out")
    a.label("__ws_ew_done")
    ld(RAX, RSP, 0x70)
    jmp_l("__ws_ret")
    a.label("__ws_ew_badfd")
    movi(RAX, -9)                               # EBADF
    jmp_l("__ws_ret")
    a.label("__ws_ew_empty")                    # 空登记表：就睡 timeout，然后回 0
    ld(RAX, RSP, 0x40)
    test_rax()
    jcc_l("s", "__ws_ew_einf")                  # 负数 = 无限等
    a.emit(mov_rr(RCX, RAX))                    # 毫秒直接喂给 Sleep
    api("Sleep")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_ew_einf")
    movi32(RCX, 0xFFFFFFFF)
    api("Sleep")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- socket(af, type, proto) ----------------------------------------
    a.label("__ws_socket")
    st(RSP, 0x28, RDI)                          # af
    st(RSP, 0x30, RSI)                          # type
    st(RSP, 0x38, RDX)                          # proto
    a.call("__ws_wsa_start")
    ld(RCX, RSP, 0x30)
    a.emit(mov_rr(RAX, RCX))
    movi32(R11, LINUX_SOCK_NONBLOCK)
    a.emit(alu_rr(0x21, RAX, R11))              # and rax, r11（非零 = 要设非阻塞）
    st(RSP, 0x40, RAX)
    movi32(RAX, 0xFFF7F7FF)                     # 清 SOCK_NONBLOCK|SOCK_CLOEXEC 两位
    a.emit(alu_rr(0x21, RCX, RAX))
    st(RSP, 0x30, RCX)
    ld(RDI, RSP, 0x28)
    cmp_edi(LINUX_AF_INET6)
    jcc_l("ne", "__ws_sk_af")
    movi32(RDI, WIN_AF_INET6)
    a.label("__ws_sk_af")
    ld(RDX, RSP, 0x38)
    a.emit(mov_rr(R8, RDX))                     # proto → 第 3 参
    ld(RDX, RSP, 0x30)
    a.emit(mov_rr(RCX, RDI))                    # af → 第 1 参
    api("socket")
    cmp_rax_m1()                                # INVALID_SOCKET 是 64 位的全 1
    jcc_l("e", "__ws_sock_err")
    a.call("__ws_sock_alloc")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    st(RSP, 0x48, RAX)                          # fd
    ld(R11, RSP, 0x40)
    test_rr(R11, R11)
    jcc_l("e", "__ws_sk_done")
    movi(R11, 1)
    st(RBX, WS_SOCKTMP, R11)                    # FIONBIO 的 u_long = 1
    ld(RCX, RDX, WS_FD_HANDLE)                  # rdx 还指着刚分配的表项
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    cmp_eax_m1()
    jcc_l("ne", "__ws_sk_done")
    a.call("__ws_wsa_err")                      # 设不上非阻塞就别留个"假非阻塞"的 socket
    st(RSP, 0x50, RAX)
    ld(RDI, RSP, 0x48)
    a.call("__ws_sock_drop")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")
    a.label("__ws_sk_done")
    ld(RAX, RSP, 0x48)
    jmp_l("__ws_ret")

    # ---- accept(43) / accept4(288) --------------------------------------
    a.label("__ws_accept")
    a.emit(b"\x45\x31\xD2")                     # xor r10d, r10d：accept 没有 flags
    jmp_l("__ws_ac_go")
    a.label("__ws_accept4")
    a.label("__ws_ac_go")
    st(RSP, 0x40, R10)                          # flags（accept4）
    st(RSP, 0x30, RSI)                          # addr（可为 0）
    st(RSP, 0x38, RDX)                          # addrlen 指针
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    api("accept")
    cmp_rax_m1()
    jcc_l("e", "__ws_sock_err")
    st(RSP, 0x48, RAX)                          # 新 SOCKET
    ld(RDI, RSP, 0x30)
    a.call("__ws_addr_out")
    ld(RAX, RSP, 0x48)
    a.call("__ws_sock_alloc")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    st(RSP, 0x50, RAX)                          # fd
    ld(R11, RSP, 0x40)
    movi32(RCX, LINUX_SOCK_NONBLOCK)
    a.emit(alu_rr(0x85, R11, RCX))              # test r11, rcx（只用来跳）
    jcc_l("e", "__ws_ac_done")
    movi(R11, 1)
    st(RBX, WS_SOCKTMP, R11)
    ld(RCX, RDX, WS_FD_HANDLE)
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    cmp_eax_m1()
    jcc_l("ne", "__ws_ac_done")
    a.call("__ws_wsa_err")
    st(RSP, 0x58, RAX)
    ld(RDI, RSP, 0x50)
    a.call("__ws_sock_drop")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_ac_done")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")

    # ---- connect / bind / listen / shutdown / getsockname / getpeername --
    a.label("__ws_connect")
    case_addr2("connect")
    a.label("__ws_bind")
    case_addr2("bind")
    a.label("__ws_listen")
    case_fd1("listen")
    a.label("__ws_shutdown")
    case_fd1("shutdown")
    a.label("__ws_getsockname")
    case_peername("getsockname")
    a.label("__ws_getpeername")
    case_peername("getpeername")
    a.label("__ws_sendto")
    case_sendrecv("sendto", "send", True, "sd")
    a.label("__ws_recvfrom")
    case_sendrecv("recvfrom", "recv", False, "rf")

    # ---- setsockopt(fd, level, optname, optval, optlen) ------------------
    # Linux 的 level/optname 与 `SO_*` 号都要翻；两个 TIMEO 还要把 timeval 折成毫秒。
    a.label("__ws_setsockopt")
    st(RSP, 0x30, RSI)                          # level
    st(RSP, 0x38, RDX)                          # optname
    st(RSP, 0x40, R10)                          # optval
    st(RSP, 0x48, R8)                           # optlen
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)                          # SOCKET
    ld(RCX, RSP, 0x30)
    cmp_ecx(1)                                  # SOL_SOCKET
    jcc_l("ne", "__ws_sso_go")                  # 别的 level 两边同值 ⇒ 直通
    movi(RCX, WS2_SOL_SOCKET)
    st(RSP, 0x30, RCX)
    ld(RDX, RSP, 0x38)
    for _opt in sorted(WS2_SO):
        cmp_edx(_opt)
        jcc_l("e", f"__ws_sso_{_opt}")
    jmp_l("__ws_sso_bad")
    for _opt in sorted(WS2_SO):
        a.label(f"__ws_sso_{_opt}")
        movi(RCX, WS2_SO[_opt])
        st(RSP, 0x38, RCX)
        if _opt in (21, 20):                    # SO_SNDTIMEO / SO_RCVTIMEO
            # ms = sec*1000 + usec/1000（`imul_ri` 只吃 imm8，乘 1000 得用 imul_rr/div）
            ld(RAX, RSP, 0x40)
            ld(RAX, RAX, 0)
            movi(RCX, 1000)
            a.emit(imul_rr(RAX, RCX))           # sec * 1000
            st(RSP, 0x50, RAX)
            ld(RAX, RSP, 0x40)
            ld(RAX, RAX, 8)                     # usec
            a.emit(b"\x31\xD2")                 # xor edx, edx
            movi(RCX, 1000)
            a.emit(group3(6, RCX))              # div rcx → rax = usec/1000
            ld(R11, RSP, 0x50)
            add_rr(RAX, R11)
            st(RBX, WS_SOCKTMP, RAX)            # 低 32 位就是毫秒
            movi(RCX, PE_STATE_VA + WS_SOCKTMP)
            st(RSP, 0x40, RCX)
            movi(RCX, 4)
            st(RSP, 0x48, RCX)
        jmp_l("__ws_sso_go")
    a.label("__ws_sso_bad")
    movi(RAX, -95)                              # EOPNOTSUPP：没映射的 SO_*
    jmp_l("__ws_ret")
    a.label("__ws_sso_go")
    ld(RCX, RSP, 0x58)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    ld(R9, RSP, 0x40)
    ld(RAX, RSP, 0x48)
    st(RSP, 0x20, RAX)                          # 第 5 参 = optlen
    api("setsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- getsockopt(fd, level, optname, optval, optlen 指针) -------------
    a.label("__ws_getsockopt")
    st(RSP, 0x60, RDI)                          # fd（`SO_ERROR` 那张小表要用）
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    st(RSP, 0x40, R10)
    st(RSP, 0x48, R8)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)
    ld(RCX, RSP, 0x30)
    cmp_ecx(1)
    jcc_l("ne", "__ws_gso_go")
    movi(RCX, WS2_SOL_SOCKET)
    st(RSP, 0x30, RCX)
    ld(RDX, RSP, 0x38)
    for _opt in sorted(WS2_SO):
        cmp_edx(_opt)
        jcc_l("e", f"__ws_gso_{_opt}")
    jmp_l("__ws_gso_bad")
    for _opt in sorted(WS2_SO):
        a.label(f"__ws_gso_{_opt}")
        movi(RCX, WS2_SO[_opt])
        st(RSP, 0x38, RCX)
        jmp_l("__ws_gso_go")
    a.label("__ws_gso_bad")
    movi(RAX, -95)
    jmp_l("__ws_ret")
    a.label("__ws_gso_go")
    # `select` 的归一化为了判"连接失败"读过 `SO_ERROR`，而**读即清零** —— 先把我们存的那份
    # 还回去（否则应用自己读 `SO_ERROR` 会看到 0，与 Linux 不同）。
    ld(RCX, RSP, 0x38)
    cmp_ecx(WS2_SO[4])                          # SO_ERROR
    jcc_l("ne", "__ws_gso_wsa")
    ld(RAX, RSP, 0x60)                          # fd
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_gso_wsa")
    movi(RDX, PE_STATE_VA + WS_SERR)
    imul_ri(RAX, 4)
    add_rr(RDX, RAX)
    ld32(RCX, RDX, 0)
    test_ecx()
    jcc_l("e", "__ws_gso_wsa")                  # 没存过：照常问 WinSock
    st32(RDX, 0, RCX)                           # 还回去，自己那份清掉（读即清零）
    ld(RAX, RSP, 0x40)                          # 调用方的 optval
    st32(RAX, 0, RCX)
    ld(RAX, RSP, 0x48)                          # 调用方的 optlen
    movi(RCX, 4)
    st32(RAX, 0, RCX)
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_gso_wsa")
    ld(RCX, RSP, 0x58)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    ld(R9, RSP, 0x40)
    ld(RAX, RSP, 0x48)                          # optlen 指针（调用方的，Windows 会回写）
    st(RSP, 0x20, RAX)
    api("getsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    ld(RCX, RSP, 0x38)
    cmp_ecx(WS2_SO_RCVTIMEO)
    jcc_l("e", "__ws_gso_time")
    cmp_ecx(WS2_SO_SNDTIMEO)
    jcc_l("e", "__ws_gso_time")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_gso_time")                    # 毫秒 → timeval（Windows 还把 *optlen 改成 4）
    ld(RAX, RSP, 0x40)
    ld32(RAX, RAX, 0)                           # ms（DWORD）
    a.emit(b"\x31\xD2")
    movi(RCX, 1000)
    a.emit(group3(6, RCX))                      # div rcx → rax = sec，rdx = ms % 1000
    movi(RCX, 1000)
    a.emit(imul_rr(RDX, RCX))                   # usec = (ms%1000) * 1000
    ld(RCX, RSP, 0x40)
    st(RCX, 0, RAX)                             # timeval.tv_sec
    st(RCX, 8, RDX)                             # timeval.tv_usec
    ld(RCX, RSP, 0x48)
    movi(RDX, 16)
    st32(RCX, 0, RDX)                           # *optlen = sizeof(struct timeval)
    xor_eax()
    jmp_l("__ws_ret")

    # ---- poll(7)：Linux 的 pollfd 数组 → WinSock 的 select --------------------
    #
    # 为什么架在 `select` 上而不是 `WSAPoll`：WSAPoll 在 Windows 上有名的问题
    # （连接失败时 POLLOUT 照样报、POLLHUP 不可靠），而 `select` 是 WinSock 里最老最稳的一个。
    #
    # **两处点名偏差**（也写进 `docs/221`）：
    #   * 只认 **socket 型 fd**：文件 fd（0/1/2 等）在 PE 上报 `POLLNVAL`，
    #     而 Linux 会给它们 `POLLIN`/`POLLOUT`；
    #   * 三个集合全空时**不睡**（WinSock 的 select 拿空集合直接 `WSAEINVAL`）。
    #
    # 事件位两边**不一样**（Linux `POLLIN`=1 / `POLLOUT`=4；Windows `POLLRDNORM`=0x100 /
    # `POLLWRNORM`=0x10），所以进出各映射一次；`POLLNVAL` 这一档 Windows 没有，由这里补。

    a.label("__ws_set_init")                    # rdi = setptr
    xor_eax()
    st32(RDI, 0, RAX)                           # fd_count = 0
    ret_()

    a.label("__ws_set_add")                     # rdi = setptr, rsi = handle → rax = 1 / 0(满)
    ld32(RCX, RDI, 0)
    cmp_ecx(WS_FD_COUNT)                        # WinSock 的 FD_SETSIZE = 64
    jcc_l("ae", "__ws_sadd_full")
    a.emit(mov_rr(RAX, RCX))
    imul_ri(RAX, 8)
    add_rr(RAX, RDI)
    st(RAX, 8, RSI)                             # fd_array[count] = handle
                                                # ⚠ 偏移是 **8** 不是 4：Win64 上
                                                # `SOCKET` 是 8 字节，`u_int fd_count` 后面
                                                # 要按 8 对齐 —— 写进 4 就落进填充区，
                                                # select 拿到的是垃圾句柄（症状：它报错）
    add_ri(RCX, 1)
    st32(RDI, 0, RCX)
    movi(RAX, 1)
    ret_()
    a.label("__ws_sadd_full")
    xor_eax()
    ret_()

    a.label("__ws_set_has")                     # rdi = setptr, rsi = handle → rax = 1 / 0
    ld32(RCX, RDI, 0)
    a.emit(b"\x31\xD2")                         # xor edx, edx
    a.label("__ws_shas_loop")
    a.emit(alu_rr(0x39, RDX, RCX))              # cmp rdx, rcx
    jcc_l("ae", "__ws_shas_no")
    a.emit(mov_rr(RAX, RDX))
    imul_ri(RAX, 8)
    add_rr(RAX, RDI)
    ld(RAX, RAX, 8)                             # fd_array 在偏移 8（见 __ws_set_add）
    a.emit(alu_rr(0x39, RAX, RSI))              # cmp rax, rsi
    jcc_l("e", "__ws_shas_yes")
    add_ri(RDX, 1)
    jmp_l("__ws_shas_loop")
    a.label("__ws_shas_no")
    xor_eax()
    ret_()
    a.label("__ws_shas_yes")
    movi(RAX, 1)
    ret_()

    a.label("__ws_poll")
    st(RSP, 0x28, RDI)                          # fds
    st(RSP, 0x30, RSI)                          # nfds
    st(RSP, 0x38, RDX)                          # timeout（毫秒；< 0 = 无限等）
    a.emit(mov_rr(RAX, RSI))
    cmp_eax(WS_FD_COUNT)                        # 我们只认自己那张 64 格的 fd 表
    jcc_l("a", "__ws_poll_einval")
    a.call("__ws_wsa_start")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_FDSETS + 2 * WS_FDSET_CAP)
    a.call("__ws_set_init")
    # ---- 第一趟：清 revents、把要等的 handle 塞进集合 ----
    xor_eax()
    st(RSP, 0x40, RAX)                          # i = 0
    a.label("__ws_poll_1l")
    ld(RAX, RSP, 0x40)
    ld(RCX, RSP, 0x30)
    a.emit(alu_rr(0x39, RAX, RCX))              # cmp rax, rcx
    jcc_l("ae", "__ws_poll_1d")
    imul_ri(RAX, 8)
    ld(RCX, RSP, 0x28)
    add_rr(RAX, RCX)                            # rax = &pollfd[i]
    st(RSP, 0x48, RAX)
    ld32(RCX, RAX, 0)                           # fd
    movi32(RDX, 0)
    st16(RAX, 6, RDX)                           # revents = 0
    cmp_ecx(0)
    jcc_l("l", "__ws_poll_1n")                  # 负 fd：Linux 跳过它
    a.emit(mov_rr(RDI, RCX))                    # edi = fd
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("ne", "__ws_poll_have")
    ld(RAX, RSP, 0x48)
    movi32(RCX, LINUX_POLLNVAL)
    st16(RAX, 6, RCX)                           # 不是 socket ⇒ POLLNVAL
    jmp_l("__ws_poll_1n")
    a.label("__ws_poll_have")
    st(RSP, 0x50, RAX)                          # SOCKET
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 4)                          # events
    movi32(RAX, LINUX_POLLIN)
    a.emit(alu_rr(0x85, RCX, RAX))              # test rcx, rax
    jcc_l("e", "__ws_poll_ev_w")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_poll_einval")              # 集合满了：报错，不静默丢
    a.label("__ws_poll_ev_w")
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 4)
    movi32(RAX, LINUX_POLLOUT)
    a.emit(alu_rr(0x85, RCX, RAX))              # test rcx, rax
    jcc_l("e", "__ws_poll_1n")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_poll_einval")
    a.label("__ws_poll_1n")
    ld(RAX, RSP, 0x40)
    add_ri(RAX, 1)
    st(RSP, 0x40, RAX)
    jmp_l("__ws_poll_1l")
    # ---- 等 ----
    a.label("__ws_poll_1d")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld32(RCX, RDI, 0)
    movi(RDX, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld32(RAX, RDX, 0)
    add_rr(RCX, RAX)
    test_ecx()
    jcc_l("e", "__ws_poll_2")                   # 全空 ⇒ 不睡（见上面的偏差说明）
    ld32(RAX, RSP, 0x38)
    cmp_eax(0)
    jcc_l("l", "__ws_poll_inf")
    a.emit(b"\x31\xD2")                         # xor edx, edx
    movi(RCX, 1000)
    a.emit(group3(6, RCX))                      # div rcx → rax = 秒，rdx = 毫秒余数
    movi(RCX, 1000)
    a.emit(imul_rr(RDX, RCX))                   # 微秒 = (ms % 1000) * 1000
    movi(RDI, PE_STATE_VA + WS_POLLTV)
    st32(RDI, 0, RAX)
    st32(RDI, 4, RDX)
    st(RSP, 0x20, RDI)
    jmp_l("__ws_poll_go")
    a.label("__ws_poll_inf")
    movi(RDX, 0)
    st(RSP, 0x20, RDX)                          # 第五参 NULL ⇒ 无限等
    a.label("__ws_poll_go")
    movi32(RCX, 0)                              # WinSock 忽略 nfds
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    movi(R8, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    movi(R9, 0)                                 # exceptfds = NULL
    api("select")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    # ---- 第二趟：把就绪映射回 revents，数几个非零 ----
    a.label("__ws_poll_2")
    xor_eax()
    st(RSP, 0x40, RAX)                          # i
    st(RSP, 0x58, RAX)                          # n = 0
    a.label("__ws_poll_2l")
    ld(RAX, RSP, 0x40)
    ld(RCX, RSP, 0x30)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_poll_2d")
    imul_ri(RAX, 8)
    ld(RCX, RSP, 0x28)
    add_rr(RAX, RCX)
    st(RSP, 0x48, RAX)
    ld32(RCX, RAX, 0)
    cmp_ecx(0)
    jcc_l("l", "__ws_poll_2n")
    a.emit(mov_rr(RDI, RCX))
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_poll_2n")                  # 非 socket（POLLNVAL 已经写好）
    st(RSP, 0x50, RAX)
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 6)
    st(RSP, 0x60, RCX)                          # rev = 上一趟留下的 revents
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_poll_2w")
    ld(RAX, RSP, 0x60)
    movi32(RCX, LINUX_POLLIN)
    add_rr(RAX, RCX)                            # 位不相交 ⇒ 加法就是或
    st(RSP, 0x60, RAX)
    a.label("__ws_poll_2w")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_poll_2s")
    ld(RAX, RSP, 0x60)
    movi32(RCX, LINUX_POLLOUT)
    add_rr(RAX, RCX)
    st(RSP, 0x60, RAX)
    a.label("__ws_poll_2s")
    ld(RDX, RSP, 0x48)
    ld(RAX, RSP, 0x60)
    st16(RDX, 6, RAX)
    test_rax()
    jcc_l("e", "__ws_poll_2n")
    ld(RAX, RSP, 0x58)
    add_ri(RAX, 1)
    st(RSP, 0x58, RAX)
    a.label("__ws_poll_2n")
    ld(RAX, RSP, 0x40)
    add_ri(RAX, 1)
    st(RSP, 0x40, RAX)
    jmp_l("__ws_poll_2l")
    a.label("__ws_poll_2d")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_poll_einval")
    movi(RAX, -22)                              # EINVAL
    jmp_l("__ws_ret")

    # ---- select(23)：Linux 的三张位图 → WinSock 的三个 fd_set ----------------
    #
    # 与 `poll` 复用**同一批集合助手**（`__ws_set_init/add/has`）与同一片状态
    # （`WS_FDSETS` 三个 520 字节的 fd_set —— `poll` 那一轮就是按三个留的）。
    # 不同的地方在**入参的形状**：`poll` 收 `pollfd` 数组，`select` 收 Linux 的
    # **位图**（`__fds_bits`，x86-64 上一个 `u64`），所以这里多一层位 ↔ 集合的搬运。
    #
    # **这一格真正的活是 `exceptfds` 的语义差**：
    #   * Linux：非阻塞 connect 失败 ⇒ 那个 fd 出现在 **writefds**（再由 `SO_ERROR` 报错）；
    #   * WinSock：非阻塞 connect 失败 ⇒ 出现在 **exceptfds**（MSDN：except 集"用于检测
    #     连接失败与 OOB 数据"），而 writefds **不报**。
    # 直接转发的话，一份"等 writefds 判连接成没成"的程序在 Linux 上跑得动、在 Windows 上
    # **永远等不到** —— 正是 `docs/217` 要堵的那种静默分歧。所以出参这里做一次**归一化**：
    # 对 except 集里每个 socket 问一次 `SO_ERROR`，非零（= 连接失败，不是 OOB）就把它
    # 从 except **挪进** write。
    #
    # **两处点名的偏差**（与 `poll` 同族，写进 `docs/231`）：
    #   * 只认 **socket 型 fd**：三个位图里若有一位是我们等不了的 fd（文件 fd、越界的
    #     编号），返回 `-EBADF`。`poll` 那格能**逐 fd** 报 `POLLNVAL`，`select` 没有
    #     逐 fd 的出口，所以只能响亮地拒 —— 不静默丢掉那一位；
    #   * `nfds > 64` 报 `-EINVAL`（我们那张 fd 表就 64 格）。
    #
    # 三个集合**全空**时：Linux 的语义是"就睡 `timeout`"（`select(0,NULL,NULL,NULL,&tv)`
    # 是常见的毫秒睡眠），而 WinSock 对全空集合直接 `WSAEINVAL`，所以这一格自己 `Sleep`。
    # **这是 `poll` 那格没有的好处**：那边三个集合全空时不睡（`docs/221` §3）。

    a.label("__ws_bit_get")                     # rdi = 位图, rsi = fd → rax = 0/1
    ld(RAX, RDI, 0)
    a.emit(mov_rr(RCX, RSI))
    a.emit(shift_cl(5, RAX))                    # shr rax, cl（Group2 /5）
    a.emit(alu_ri(AND_, RAX, 1))
    ret_()

    a.label("__ws_bit_set")                     # rdi = 位图, rsi = fd
    movi(RDX, 1)
    a.emit(mov_rr(RCX, RSI))
    a.emit(shift_cl(4, RDX))                    # shl rdx, cl
    ld(RAX, RDI, 0)
    a.emit(alu_rr(0x09, RAX, RDX))              # or rax, rdx
    st(RDI, 0, RAX)
    ret_()

    a.label("__ws_bit_clr")                     # rdi = 位图, rsi = fd
    movi(RDX, 1)
    a.emit(mov_rr(RCX, RSI))
    a.emit(shift_cl(4, RDX))                    # shl rdx, cl
    a.emit(b"\x48\xF7\xD2")                     # not rdx
    ld(RAX, RDI, 0)
    a.emit(alu_rr(0x21, RAX, RDX))              # and rax, rdx
    st(RDI, 0, RAX)
    ret_()

    a.label("__ws_fd_of_sock")                  # rdi = SOCKET → rax = fd（找不到 = -1）
    xor_eax()
    a.label("__ws_fos_loop")
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_fos_no")
    a.emit(mov_rr(RCX, RAX))
    imul_ri(RCX, WS_FD_SIZE)
    a.emit(mov_rr(RDX, RBX))
    add_rr(RCX, RDX)                            # rcx = &fd 表项
    ld32(RDX, RCX, WS_FD_KIND)
    a.emit(alu_ri(CMP, RDX, WS_FD_SOCK))
    jcc_l("ne", "__ws_fos_next")
    ld(RDX, RCX, WS_FD_HANDLE)
    a.emit(alu_rr(0x39, RDX, RDI))              # cmp rdx, rdi
    jcc_l("e", "__ws_fos_yes")
    a.label("__ws_fos_next")
    add_ri(RAX, 1)
    jmp_l("__ws_fos_loop")
    a.label("__ws_fos_yes")
    ret_()
    a.label("__ws_fos_no")
    movi(RAX, -1)
    ret_()

    a.label("__ws_select")                      # rdi=nfds rsi=rd rdx=wr r10=ex r8=tv
    st(RSP, 0x28, RDI)                          # nfds
    st(RSP, 0x30, RSI)                          # readfds（位图指针，可 NULL）
    st(RSP, 0x38, RDX)                          # writefds
    st(RSP, 0x40, R10)                          # exceptfds
    st(RSP, 0x48, R8)                           # timeout（Linux timeval：两个 i64）
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("a", "__ws_sel_einval")
    a.call("__ws_wsa_start")
    # `WS_SELFLAG`：这一趟里每个 fd 进过哪几个集合（归一化要按"调用方要过 write"扫，
    # 而 `select` 会把集合改写成结果 ⇒ 自己留一份成员关系）
    movi(RDI, PE_STATE_VA + WS_SELFLAG)
    movi(RCX, WS_FD_COUNT)
    xor_eax()
    a.label("__ws_sel_zf")
    st8(RDI, 0, RAX)
    add_ri(RDI, 1)
    a.emit(alu_ri(SUB, RCX, 1))
    test_rr(RCX, RCX)
    jcc_l("ne", "__ws_sel_zf")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_SHADOW_EX)
    a.call("__ws_set_init")             # 影子 except 集（见"归一化"那段）
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_FDSETS + 2 * WS_FDSET_CAP)
    a.call("__ws_set_init")
    # ---- 第一趟：位图 → 三个集合；顺手把调用方位图里那一位**清掉**
    #      （Linux 语义：返回时集合里只剩就绪的 —— 结果那一趟再按真实就绪置位）----
    xor_eax()
    st(RSP, 0x50, RAX)                          # i（fd）
    st(RSP, 0x60, RAX)                          # nhandles（三个集合里的 handle 总数）
    a.label("__ws_sel_1l")
    ld(RAX, RSP, 0x50)
    ld(RCX, RSP, 0x28)
    a.emit(alu_rr(0x39, RAX, RCX))              # cmp rax, rcx
    jcc_l("ae", "__ws_sel_1d")
    xor_eax()
    st(RSP, 0x58, RAX)                          # k（第几个集合：0=读 1=写 2=异常）
    a.label("__ws_sel_1k")
    ld(RAX, RSP, 0x58)
    cmp_eax(3)
    jcc_l("ae", "__ws_sel_1n")
    # 位图指针 = *(rsp + 0x30 + k*8)（位移是变量，所以先算地址再解引用）
    a.emit(mov_rr(RCX, RAX))
    imul_ri(RCX, 8)
    add_ri(RCX, 0x30)
    add_rr(RCX, RSP)
    ld(RCX, RCX, 0)
    st(RSP, 0x70, RCX)                          # 位图指针（0 = 调用方没给这个集合）
    test_ecx()
    jcc_l("e", "__ws_sel_1kn")                  # NULL：跳过
    a.emit(mov_rr(RDI, RCX))
    ld(RSI, RSP, 0x50)                          # fd
    a.call("__ws_bit_get")
    test_rax()
    jcc_l("e", "__ws_sel_1kn")                  # 这一位没置
    # 有位：这个 fd 必须是 socket 型的，否则我们等不了它
    ld(RDI, RSP, 0x50)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_sel_ebadf")
    st(RSP, 0x78, RAX)                          # SOCKET
    # 集合指针 = WS_FDSETS + k*WS_FDSET_CAP
    ld(RCX, RSP, 0x58)
    movi(RDX, WS_FDSET_CAP)                     # ⚠ `imul_ri` 只吃 imm8（520 & 0xFF = 8），
    a.emit(imul_rr(RCX, RDX))                   #    所以乘数得放进寄存器用 `imul_rr`
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    add_rr(RCX, RDX)
    a.emit(mov_rr(RDI, RCX))
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_sel_einval")               # 集合满（>64 个 handle）：报错，不静默丢
    ld(RAX, RSP, 0x60)
    add_ri(RAX, 1)
    st(RSP, 0x60, RAX)
    # 影子 except 集：**write / except 两栏的成员都得进去**。WinSock 把"非阻塞 connect
    # 失败"报在 **except 集**里（MSDN），光放进 writefds 是等不到的 —— 实测症状是
    # `select` 干等超时、返回 0。归一化那一段再把它搬回 write 集（见那段注释）。
    ld(RCX, RSP, 0x58)                          # k
    test_ecx()
    jcc_l("e", "__ws_sel_nosh")                 # k == 0（readfds）不进影子集
    movi(RDI, PE_STATE_VA + WS_SHADOW_EX)
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_sel_einval")
    a.label("__ws_sel_nosh")
    # WS_SELFLAG[fd] |= 1 << k（成员关系自己留一份：`select` 会把集合改写成结果）
    movi(RDX, PE_STATE_VA + WS_SELFLAG)
    ld(RAX, RSP, 0x50)                          # fd
    add_rr(RDX, RAX)
    movi(RAX, 1)
    ld(RCX, RSP, 0x58)                          # k（进 cl）
    a.emit(shift_cl(4, RAX))                    # rax = 1 << k
    ld8z(RCX, RDX, 0)
    a.emit(alu_rr(0x09, RCX, RAX))              # or rcx, rax
    st8(RDX, 0, RCX)
    # 清掉调用方位图里这一位（结果那一趟会按就绪重设）
    ld(RDI, RSP, 0x70)
    ld(RSI, RSP, 0x50)
    a.call("__ws_bit_clr")
    a.label("__ws_sel_1kn")
    ld(RAX, RSP, 0x58)
    add_ri(RAX, 1)
    st(RSP, 0x58, RAX)
    jmp_l("__ws_sel_1k")
    a.label("__ws_sel_1n")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_sel_1l")
    a.label("__ws_sel_1d")
    # ---- 三个集合全空：Linux 是"就睡 timeout" ----
    ld(RAX, RSP, 0x60)
    test_rax()
    jcc_l("ne", "__ws_sel_go")
    ld(RAX, RSP, 0x48)
    test_rax()
    jcc_l("e", "__ws_sel_sleep_inf")
    # 毫秒 = sec*1000 + usec/1000
    ld(RCX, RAX, 0)                             # sec
    movi(RDX, 1000)
    a.emit(imul_rr(RCX, RDX))
    ld(RDX, RAX, 8)                             # usec
    movi(R8, 1000)
    a.emit(mov_rr(RAX, RDX))
    a.emit(b"\x31\xD2")                         # xor edx, edx
    a.emit(group3(6, R8))                       # div r8 → rax = usec/1000
    add_rr(RCX, RAX)
    api("Sleep")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_sel_sleep_inf")
    movi32(RCX, 0xFFFFFFFF)                     # INFINITE：与 Linux 的"永远等"同义
    api("Sleep")
    xor_eax()
    jmp_l("__ws_ret")
    # ---- 等 ----
    a.label("__ws_sel_go")
    ld(RAX, RSP, 0x48)
    test_rax()
    jcc_l("e", "__ws_sel_notv")
    ld(RCX, RAX, 0)                             # sec（i64）
    a.emit(alu_ri(CMP, RCX, 0x7FFFFFFF))        # ⚠ 比的是**秒**不是指针：`alloc` 给的是
                                                #   高地址（PE 基址 0x140000000），拿指针去
                                                #   `jle` 会**从不跳**，于是每格都被夹成
                                                #   0x7FFFFFFF ≈ 36 分钟（实测：整支挂住）
    jcc_l("le", "__ws_sel_secok")
    movi(RCX, 0x7FFFFFFF)                       # 夹到 32 位（Windows timeval 是两个 long）
    a.label("__ws_sel_secok")
    movi(RDX, PE_STATE_VA + WS_POLLTV)
    st32(RDX, 0, RCX)                           # sec  （Linux 的 i64 → Windows 的 long）
    ld(RCX, RSP, 0x48)
    ld(RCX, RCX, 8)                             # usec（i64）
    st32(RDX, 4, RCX)
    st(RSP, 0x20, RDX)                          # 第 5 参：与 `poll` 同一处（存 RDX）
    jmp_l("__ws_sel_call")
    a.label("__ws_sel_notv")
    movi(R8, 0)
    st(RSP, 0x20, R8)                           # NULL ⇒ 无限等
    a.label("__ws_sel_call")
    movi32(RCX, 0)                              # WinSock 忽略 nfds
    ld(RDX, RSP, 0x30)                          # readfds 位图指针（NULL 就传 NULL）
    test_rr(RDX, RDX)
    jcc_l("e", "__ws_sel_rd0")
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    a.label("__ws_sel_rd0")
    ld(R8, RSP, 0x38)                           # writefds 位图指针
    test_rr(R8, R8)
    jcc_l("e", "__ws_sel_wr0")
    movi(R8, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    a.label("__ws_sel_wr0")
    # exceptfds **永远**用影子集：里面装着调用方 write / except 两栏的全部成员。
    # 为什么不能直接把调用方的 except 集递过去 —— 两个原因，都是实测撞出来的：
    #   (a) 调用方常常只给 writefds（等 connect 成没成的标准写法），NULL 递过去就没人报
    #       连接失败；给它一个空集也一样，因为 WinSock 只报**集合里**的句柄；
    #   (b) 结果不能直接落回调用方的 except 集，得先按 SO_ERROR 分类（见归一化（一））。
    movi(R9, PE_STATE_VA + WS_SHADOW_EX)
    api("select")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    # **返回 0 也照样往下走**：两趟归一化要按名单再问一遍 `SO_ERROR` —— "同步就失败"
    # 的 socket 恰好让 WinSock 报 0（`docs/231`）
    # ---- 归一化（一）：把影子集里"连接失败"的挪进 write 集 ----
    # 逐个问 SO_ERROR：非零 = **连接失败**（不是 OOB）⇒ 从影子里摘掉 + 加进 write。
    # 这一格是 Linux 与 WinSock **语义真的不同**的地方（`docs/231`）：不归一化的话，
    # "等 writefds 判连接成没成"的程序在 Windows 上永远等不到。
    xor_eax()
    st(RSP, 0x50, RAX)                          # i（在影子集里的下标）
    movi(RCX, PE_STATE_VA + WS_SHADOW_EX)       # WinSock 的结果**永远**落在影子集里
    st(RSP, 0x68, RCX)
    a.label("__ws_sel_nl")
    ld(RCX, RSP, 0x68)
    ld32(RAX, RCX, 0)                           # ex.fd_count
    ld(RDX, RSP, 0x50)
    a.emit(alu_rr(0x39, RDX, RAX))              # cmp rdx, rax
    jcc_l("ae", "__ws_sel_wscan")               # i >= count ⇒ 完（接着跑写入扫描）
    a.emit(mov_rr(RAX, RDX))
    imul_ri(RAX, 8)
    add_rr(RAX, RCX)
    ld(RAX, RAX, 8)                             # SOCKET
    st(RSP, 0x78, RAX)
    # getsockopt(sock, SOL_SOCKET, SO_ERROR, &tmp, &len)
    movi32(RCX, 4)
    movi(RDX, PE_STATE_VA + WS_SOCKTMP + 8)
    st32(RDX, 0, RCX)                           # optlen = sizeof(int)
    st(RSP, 0x20, RDX)                          # 第五参
    ld(RCX, RSP, 0x78)
    movi(RDX, WS2_SOL_SOCKET)
    movi(R8, WS2_SO[4])                         # SO_ERROR
    movi(R9, PE_STATE_VA + WS_SOCKTMP)
    api("getsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    movi(RCX, PE_STATE_VA + WS_SOCKTMP)
    ld32(RAX, RCX, 0)                           # 错误码
    test_rax()
    jcc_l("e", "__ws_sel_nnext")                # 0 ⇒ 真的 OOB，留在 except
    ld(RDI, RSP, 0x78)
    a.emit(mov_rr(RSI, RAX))
    a.call("__ws_sel_save_err")                 # 读即清零 ⇒ 存一份给应用（见那个助手）
    # 连接失败：**调用方要了 writefds 才搬**（没要就只是把它丢掉）
    ld(RAX, RSP, 0x38)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_sel_nswap")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("ne", "__ws_sel_nswap")               # 已在 write 集里：别加第二遍（计数会翻倍）
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_sel_einval")
    a.label("__ws_sel_nswap")
    # swap-remove：count -= 1; ex[i] = ex[count-1]（**不动 i**，下一轮看新进来的那个）
    ld(RCX, RSP, 0x68)
    ld32(RAX, RCX, 0)
    a.emit(alu_ri(SUB, RAX, 1))
    st32(RCX, 0, RAX)
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, 8)
    add_rr(RDX, RCX)
    ld(RDX, RDX, 8)                             # ex[count-1]
    ld(RSI, RSP, 0x50)
    a.emit(mov_rr(RAX, RSI))
    imul_ri(RAX, 8)
    add_rr(RAX, RCX)
    st(RAX, 8, RDX)                             # ex[i] = ex[count-1]
    jmp_l("__ws_sel_nl")

    a.label("__ws_sel_nnext")
    # SO_ERROR == 0 ⇒ 不是连接失败，是**真 except 事件**（带外数据）。影子集是内部的：只有
    # 调用方**点名要过** exceptfds（SELFLAG 位 2）时，才留着让它落到调用方的位图上；
    # 否则从影子里**摘掉** —— WinSock 报了、调用方没问的东西不该出现在结果里。
    ld(RAX, RSP, 0x40)
    test_rr(RAX, RAX)
    jcc_l("e", "__ws_sel_nswap")                # 调用方没要 exceptfds ⇒ 摘掉
    ld(RDI, RSP, 0x78)
    a.call("__ws_fd_of_sock")
    test_rax()
    jcc_l("e", "__ws_sel_nswap")
    movi(RDX, PE_STATE_VA + WS_SELFLAG)
    add_rr(RDX, RAX)
    ld8z(RCX, RDX, 0)
    a.emit(alu_ri(AND_, RCX, 4))                # 位 2 = 这个 fd 进过调用方的 exceptfds
    test_ecx()
    jcc_l("e", "__ws_sel_nswap")                # 不在那一栏里 ⇒ 摘掉
    ld(RAX, RSP, 0x50)                          # 留着：只往前走一格（不做 swap-remove）
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_sel_nl")
    # ---- 归一化（二）：**调用方要过 write 的 socket** 身上若已有错，补报可写 ----
    # 排在归一化（一）**之后**：那一趟要看**真实的** `SO_ERROR` 才分得清"连接失败"与
    # "真 OOB"；这个扫描读一次就把它清零（读即清零），抢在前面会把失败误判成 OOB、
    # 塞进调用方的 except 集。
    a.label("__ws_sel_wscan")
    # 为什么还要这一步：Linux 把"connect 已经失败"的 socket 报成**可写**（实测），而 WinSock
    # 对这种**同步就失败**的 socket 在 select 里**什么都不报**（实测：把同一个 socket 同时
    # 放进 writefds 与 exceptfds，两边都拿不到）。所以按调用方的 write 名单再问一遍
    # `SO_ERROR` —— 有错就正是 Linux 说的那个"可写"。
    xor_eax()
    st(RSP, 0x50, RAX)                          # fd
    a.label("__ws_sel_wl")
    ld(RAX, RSP, 0x50)
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_sel_wscand")               # 扫描完 → 第三趟
    movi(RDX, PE_STATE_VA + WS_SELFLAG)
    add_rr(RDX, RAX)
    ld8z(RCX, RDX, 0)
    a.emit(alu_ri(AND_, RCX, 2))                # 位 1 = 这个 fd 进过 writefds
    test_ecx()
    jcc_l("e", "__ws_sel_wn")
    ld(RDI, RSP, 0x50)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_sel_wn")                   # 不是 socket：不管
    st(RSP, 0x78, RAX)                          # SOCKET
    movi32(RCX, 4)
    movi(RDX, PE_STATE_VA + WS_SOCKTMP + 8)
    st32(RDX, 0, RCX)
    st(RSP, 0x20, RDX)
    ld(RCX, RSP, 0x78)
    movi(RDX, WS2_SOL_SOCKET)
    movi(R8, WS2_SO[4])                         # SO_ERROR
    movi(R9, PE_STATE_VA + WS_SOCKTMP)
    api("getsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    movi(RCX, PE_STATE_VA + WS_SOCKTMP)
    ld32(RAX, RCX, 0)
    test_rax()
    jcc_l("e", "__ws_sel_wn")                   # 没挂账：本来就好好的
    ld(RDI, RSP, 0x78)
    a.emit(mov_rr(RSI, RAX))
    a.call("__ws_sel_save_err")                 # 读即清零 ⇒ 存一份给应用
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("ne", "__ws_sel_wn")                  # WinSock 本来就报了它 ⇒ 别加第二遍
                                                #   （加两遍 = 返回的**计数**翻倍；
                                                #    Linux 数的是三个集合里置位的**位数**）
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x78)
    a.call("__ws_set_add")
    a.label("__ws_sel_wn")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_sel_wl")
    a.label("__ws_sel_wscand")
    # ---- 第三趟：三个集合 → 调用方位图，数就绪的 ----
    a.label("__ws_sel_nd")
    xor_eax()
    st(RSP, 0x68, RAX)                          # total = 0
    st(RSP, 0x58, RAX)                          # k
    a.label("__ws_sel_3k")
    ld(RAX, RSP, 0x58)
    cmp_eax(3)
    jcc_l("ae", "__ws_sel_3d")
    # 位图指针 = *(rsp + 0x30 + k*8)
    a.emit(mov_rr(RCX, RAX))
    imul_ri(RCX, 8)
    add_ri(RCX, 0x30)
    add_rr(RCX, RSP)
    ld(RCX, RCX, 0)
    st(RSP, 0x70, RCX)
    test_ecx()
    jcc_l("e", "__ws_sel_3kn")                  # 调用方没给这个集合
    xor_eax()
    st(RSP, 0x50, RAX)                          # j
    a.label("__ws_sel_3l")
    ld(RAX, RSP, 0x50)
    ld(RCX, RSP, 0x58)
    movi(RDX, WS_FDSET_CAP)                     # ⚠ `imul_ri` 只吃 imm8（520 & 0xFF = 8），
    a.emit(imul_rr(RCX, RDX))                   #    所以乘数得放进寄存器用 `imul_rr`
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    add_rr(RCX, RDX)                            # 集合指针
    # k == 2（except）不一样：WinSock 的结果**落在影子集里**（三个 `WS_FDSETS` 集合只当
    # 入参用），所以那一栏得改去读影子集 —— 否则读到的会是"调用方输入的陈旧拷贝"，
    # 凡是问过的 fd 都会被当成就绪（实测：只问 except 的用例里全报就绪）。
    ld(RDX, RSP, 0x58)                          # k
    a.emit(alu_ri(CMP, RDX, 2))
    jcc_l("ne", "__ws_sel_3set")
    movi(RCX, PE_STATE_VA + WS_SHADOW_EX)
    a.label("__ws_sel_3set")
    ld32(RDX, RCX, 0)                           # count
    a.emit(alu_rr(0x39, RAX, RDX))              # cmp rax, rdx
    jcc_l("ae", "__ws_sel_3kn")
    imul_ri(RAX, 8)
    add_rr(RAX, RCX)
    ld(RAX, RAX, 8)                             # SOCKET
    a.emit(mov_rr(RDI, RAX))
    a.call("__ws_fd_of_sock")
    cmp_eax(0)
    jcc_l("l", "__ws_sel_3j")                   # 找不到对应的 fd：跳过（不该发生）
    ld(RDI, RSP, 0x70)
    a.emit(mov_rr(RSI, RAX))
    a.call("__ws_bit_set")
    ld(RAX, RSP, 0x68)
    add_ri(RAX, 1)
    st(RSP, 0x68, RAX)                          # total += 1
    a.label("__ws_sel_3j")
    ld(RAX, RSP, 0x50)
    add_ri(RAX, 1)
    st(RSP, 0x50, RAX)
    jmp_l("__ws_sel_3l")
    a.label("__ws_sel_3kn")
    ld(RAX, RSP, 0x58)
    add_ri(RAX, 1)
    st(RSP, 0x58, RAX)
    jmp_l("__ws_sel_3k")
    a.label("__ws_sel_3d")
    ld(RAX, RSP, 0x68)
    jmp_l("__ws_ret")
    a.label("__ws_sel_zero")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_sel_ebadf")
    movi(RAX, -9)                               # EBADF
    jmp_l("__ws_ret")
    a.label("__ws_sel_einval")
    movi(RAX, -22)                              # EINVAL
    jmp_l("__ws_ret")

    a.label("__ws_sel_save_err")                # in: rdi = SOCKET, rsi = 错误码
    a.emit(mov_rr(R8, RSI))                     # 先藏起来：`__ws_fd_of_sock` 会用 rcx/rdx
    a.call("__ws_fd_of_sock")                   # rdi → rax = fd（-1 = 找不到）
    cmp_eax(0)
    jcc_l("l", "__ws_sse_no")
    movi(RDX, PE_STATE_VA + WS_SERR)
    imul_ri(RAX, 4)
    add_rr(RDX, RAX)
    st32(RDX, 0, R8)
    a.label("__ws_sse_no")
    ret_()

    # ---- getpid() --------------------------------------------------------
    a.label("__ws_getpid")
    api("GetCurrentProcessId")
    zext_eax()
    jmp_l("__ws_ret")

    # ---- uname(buf) ------------------------------------------------------
    # S 类（`docs/311` §2）：答案由 guest 身份决定，不由宿主决定 —— PE 上也报 Linux。
    a.label("__ws_uname")
    movi(RSI, PE_UNAME_VA)
    movi(RCX, len(PE_UNAME))
    a.label("__ws_uname_cp")
    load_al(RSI)
    store_al(RDI)
    add_ri(RSI, 1)
    add_ri(RDI, 1)
    a.emit(b"\x48\xFF\xC9")                  # dec rcx
    jcc_l("ne", "__ws_uname_cp")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- time(tloc) ------------------------------------------------------
    # FILETIME 是自 1601-01-01 起的 100ns；减 Unix 纪元（11644473600 秒）再除以 10^7。
    a.label("__ws_time")
    st(RSP, 0x48, RDI)
    lea_rsp(RCX, 0x30)
    api("GetSystemTimeAsFileTime")
    ld(RAX, RSP, 0x30)
    movi(RCX, 116444736000000000)
    sub_rr(RAX, RCX)
    movi(RCX, 10000000)
    a.emit(b"\x31\xD2")                      # xor edx, edx
    a.emit(b"\x48\xF7\xF1")                  # div rcx
    ld(RDI, RSP, 0x48)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_time_out")
    st(RDI, 0, RAX)
    a.label("__ws_time_out")
    jmp_l("__ws_ret")

    # ---- clock_gettime(clk, ts) -----------------------------------------
    # 0 = REALTIME（FILETIME）；1/4/7 = MONOTONIC（QPC）；其余 -EINVAL。
    # ts 按 Linux 的 timespec 写两个 i64（秒、纳秒），不是 Windows 的 LARGE_INTEGER。
    a.label("__ws_clock_gettime")
    st(RSP, 0x48, RDI)
    st(RSP, 0x50, RSI)
    cmp_edi(0)
    jcc_l("e", "__ws_cgt_rt")
    cmp_edi(1)
    jcc_l("e", "__ws_cgt_mono")
    cmp_edi(4)
    jcc_l("e", "__ws_cgt_mono")
    cmp_edi(7)
    jcc_l("e", "__ws_cgt_mono")
    movi(RAX, -22)                           # EINVAL
    jmp_l("__ws_ret")

    a.label("__ws_cgt_rt")
    lea_rsp(RCX, 0x30)
    api("GetSystemTimeAsFileTime")
    ld(RAX, RSP, 0x30)
    movi(RCX, 116444736000000000)
    sub_rr(RAX, RCX)
    movi(RCX, 10000000)
    a.emit(b"\x31\xD2")                      # xor edx, edx
    a.emit(b"\x48\xF7\xF1")                  # div rcx -> rax=sec, rdx=rem
    st(RSP, 0x40, RAX)
    imul_ri(RDX, 100)                        # nsec = rem * 100
    ld(RSI, RSP, 0x50)
    ld(RCX, RSP, 0x40)
    st(RSI, 0, RCX)
    st(RSI, 8, RDX)
    xor_eax()
    jmp_l("__ws_ret")

    a.label("__ws_cgt_mono")
    lea_rsp(RCX, 0x30)
    api("QueryPerformanceCounter")
    lea_rsp(RCX, 0x38)
    api("QueryPerformanceFrequency")
    ld(RAX, RSP, 0x30)                       # qpc
    ld(RCX, RSP, 0x38)                       # freq
    a.emit(b"\x31\xD2")                      # xor edx, edx
    a.emit(b"\x48\xF7\xF1")                  # div rcx -> rax=sec, rdx=rem
    st(RSP, 0x40, RAX)
    a.emit(mov_rr(RAX, RDX))                 # rax = rem
    movi(R8, 1000000000)
    a.emit(rex(1, RAX, 0, R8) + b"\x0F\xAF" + modrm(3, RAX, R8))   # imul rax, r8
    a.emit(b"\x31\xD2")                      # xor edx, edx
    ld(RCX, RSP, 0x38)                       # freq
    a.emit(b"\x48\xF7\xF1")                  # div rcx -> nsec
    ld(RSI, RSP, 0x50)
    ld(RCX, RSP, 0x40)
    st(RSI, 0, RCX)
    st(RSI, 8, RAX)
    xor_eax()
    jmp_l("__ws_ret")

    # ---- 内部：Linux 路径 -> Windows 路径（'/' -> '\'）-------------------
    # in: rsi = 源（NUL 结尾），r10 = 目的。out: rax = 长度（不含 NUL）。
    a.label("__ws_translate2")
    a.emit(b"\x31\xC9")                      # xor ecx, ecx
    a.label("__ws_tr3_loop")
    load_al(RSI)
    cmp_al(0)
    jcc_l("e", "__ws_tr3_done")
    cmp_al(47)                               # '/'
    jcc_l("ne", "__ws_tr3_keep")
    a.emit(b"\xB0\x5C")                      # mov al, '\'
    a.label("__ws_tr3_keep")
    store_al(R10)
    add_ri(RSI, 1)
    add_ri(R10, 1)
    add_ri(RCX, 1)
    jmp_l("__ws_tr3_loop")
    a.label("__ws_tr3_done")
    store_al(R10)                            # 写 NUL：暂存会被同一进程的下一跳复用
    a.emit(mov_rr(RAX, RCX))
    ret_()

    # ---- 内部：Linux prot -> Win32 页保护。in: edi; out: eax。 -----------
    a.label("__ws_winprot")
    movi32(RAX, 2)                           # PAGE_READONLY
    a.emit(b"\xF7\xC7\x02\x00\x00\x00")      # test edi, 2
    jcc_l("e", "__ws_wp_x")
    movi32(RAX, 4)                           # PAGE_READWRITE
    a.label("__ws_wp_x")
    a.emit(b"\xF7\xC7\x04\x00\x00\x00")      # test edi, 4
    jcc_l("e", "__ws_wp_done")
    cmp_eax(4)
    jcc_l("ne", "__ws_wp_rx")
    movi32(RAX, 0x40)                        # PAGE_EXECUTE_READWRITE
    jmp_l("__ws_wp_done")
    a.label("__ws_wp_rx")
    movi32(RAX, 0x20)                        # PAGE_EXECUTE_READ
    a.label("__ws_wp_done")
    ret_()

    # ---- 内部：登记一块映射。in: rax=addr, rdx=len, rcx=映射句柄(0=匿名)。--
    a.label("__ws_map_rec")
    movi(R8, PE_STATE_VA + WS_MMAP)
    a.emit(b"\x45\x31\xC9")                  # xor r9d, r9d
    a.label("__ws_mr_loop")
    a.emit(b"\x41\x81\xF9" + i32(WS_MMAP_MAX))   # cmp r9d, WS_MMAP_MAX
    jcc_l("ae", "__ws_mr_done")              # 表满：不写穿（调用方拿到地址，但 munmap 找不到）
    ld(R10, R8, 0)
    test_rr(R10, R10)
    jcc_l("e", "__ws_mr_free")
    add_ri(R8, WS_MMAP_ENT)
    a.emit(b"\x41\x83\xC1\x01")              # add r9d, 1
    jmp_l("__ws_mr_loop")
    a.label("__ws_mr_free")
    st(R8, 0, RAX)
    st(R8, 8, RDX)
    st(R8, 16, RCX)
    a.label("__ws_mr_done")
    ret_()

    # ---- open(path, flags, mode) -> openat(AT_FDCWD, …) ------------------
    a.label("__ws_open")
    a.emit(mov_rr(R10, RDX))                 # mode -> r10
    a.emit(mov_rr(RDX, RSI))                 # flags -> rdx
    a.emit(mov_rr(RSI, RDI))                 # path -> rsi
    a.emit(b"\x31\xFF")                      # xor edi, edi（dirfd 不用）
    jmp_l("__ws_openat")

    # ---- unlink(path) ----------------------------------------------------
    a.label("__ws_unlink")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("DeleteFileA")
    a.emit(b"\x85\xC0")                      # test eax, eax
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- lseek(fd, off, whence) -----------------------------------------
    a.label("__ws_lseek")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    st(RSP, 0x50, RDX)
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("ne", "__ws_lseek_spipe")
    ld(RDI, RSP, 0x40)
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_lseek_cmd")
    cmp_ecx(WS_FD_FILE)
    jcc_l("ne", "__ws_fail")
    ld(RCX, RAX, WS_FD_HANDLE)
    ld(RDX, RSP, 0x48)
    lea_rsp(R8, 0x58)
    ld(R9, RSP, 0x50)
    a.emit(b"\x41\x83\xF9\x02")              # cmp r9d, 2
    jcc_l("a", "__ws_einval")
    api("SetFilePointerEx")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_lseek_spipe")
    movi(RAX, -29)                           # ESPIPE
    jmp_l("__ws_ret")
    a.label("__ws_lseek_cmd")
    ld(RCX, RAX, WS_FD_POS)
    ld(R8, RSP, 0x50)
    a.emit(b"\x45\x85\xC0")                  # test r8d, r8d
    jcc_l("e", "__ws_lseek_cmd_set")
    a.emit(b"\x41\x83\xF8\x01")              # cmp r8d, 1
    jcc_l("e", "__ws_lseek_cmd_cur")
    a.emit(b"\x41\x83\xF8\x02")              # cmp r8d, 2
    jcc_l("e", "__ws_lseek_cmd_end")
    jmp_l("__ws_einval")
    a.label("__ws_lseek_cmd_cur")
    ld(R8, RSP, 0x48)
    add_rr(RCX, R8)
    jmp_l("__ws_lseek_cmd_clamp")
    a.label("__ws_lseek_cmd_end")
    ld(RCX, RBX, WS_CMD_LEN)
    jmp_l("__ws_lseek_cmd_clamp")
    a.label("__ws_lseek_cmd_set")
    ld(RCX, RSP, 0x48)
    a.label("__ws_lseek_cmd_clamp")
    test_rr(RCX, RCX)
    jcc_l("s", "__ws_lseek_cmd_zero")
    ld(R8, RBX, WS_CMD_LEN)
    a.emit(b"\x4C\x39\xC1")                  # cmp rcx, r8
    jcc_l("be", "__ws_lseek_cmd_store")
    a.emit(mov_rr(RCX, R8))
    jmp_l("__ws_lseek_cmd_store")
    a.label("__ws_lseek_cmd_zero")
    a.emit(b"\x31\xC9")
    a.label("__ws_lseek_cmd_store")
    st(RAX, WS_FD_POS, RCX)
    a.emit(mov_rr(RAX, RCX))
    jmp_l("__ws_ret")
    a.label("__ws_einval")
    movi(RAX, -22)
    jmp_l("__ws_ret")

    # ---- dup2(old, new) --------------------------------------------------
    a.label("__ws_dup2")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    a.emit(mov_rr(RAX, RSI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    ld(RDI, RSP, 0x40)
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x50, RAX)                       # old handle
    # old 的种类：0..2 视为 FILE；否则读表，socket/epoll 不支持。
    ld(RDI, RSP, 0x40)
    cmp_edi(3)
    jcc_l("b", "__ws_dup2_kf")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_dup2_nope")
    cmp_ecx(WS_FD_EPOLL)
    jcc_l("e", "__ws_dup2_nope")
    st(RSP, 0x58, RCX)
    jmp_l("__ws_dup2_hk")
    a.label("__ws_dup2_kf")
    movi32(RCX, WS_FD_FILE)
    st(RSP, 0x58, RCX)
    a.label("__ws_dup2_hk")
    # 目标槽位若已占用：先关掉（socket/epoll 不碰）。
    ld(RDI, RSP, 0x48)
    entry_of_fd()
    st(RSP, 0x60, RAX)
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_dup2_go")
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_dup2_nope")
    cmp_ecx(WS_FD_EPOLL)
    jcc_l("e", "__ws_dup2_nope")
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_dup2_zap")
    ld(RCX, RAX, WS_FD_HANDLE)
    api("CloseHandle")
    ld(RAX, RSP, 0x60)
    a.label("__ws_dup2_zap")
    a.emit(b"\x31\xC9")
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_dup2_go")
    # DuplicateHandle(-1, old, -1, &new, 0, FALSE, DUPLICATE_SAME_ACCESS)
    movi(RCX, -1)
    ld(RDX, RSP, 0x50)
    movi(R8, -1)
    lea_rsp(R9, 0x68)
    a.emit(b"\x31\xC0")
    st(RSP, 0x20, RAX)                       # dwDesiredAccess = 0
    st(RSP, 0x28, RAX)                       # bInheritHandle = FALSE
    movi32(RAX, 2)                           # DUPLICATE_SAME_ACCESS
    st(RSP, 0x30, RAX)
    api("DuplicateHandle")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RAX, RSP, 0x60)
    ld(RCX, RSP, 0x58)
    st32(RAX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x68)
    st(RAX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RAX, WS_FD_POS, RCX)
    ld(RAX, RSP, 0x48)
    jmp_l("__ws_ret")
    a.label("__ws_dup2_nope")
    movi(RAX, -9)                            # EBADF（socket/epoll 不做 dup2）
    jmp_l("__ws_ret")

    # ---- pipe2(fds, flags) ----------------------------------------------
    a.label("__ws_pipe2")
    st(RSP, 0x40, RDI)
    lea_rsp(RCX, 0x48)
    lea_rsp(RDX, 0x50)
    a.emit(b"\x45\x31\xC0")                  # r8 = NULL
    a.emit(b"\x45\x31\xC9")                  # r9 = 0
    api("CreatePipe")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    a.call("__ws_alloc_fd")
    cmp_eax_m1()
    jcc_l("e", "__ws_pipe_full")
    st(RSP, 0x58, RAX)
    st(RSP, 0x60, RDX)
    # **先占住这一格再分配第二个**：alloc_fd 靠 kind != 0 判"已占用"，
    # 这里若等两个都分配完再填，两次都会拿到同一个最低空号。
    ld(RAX, RSP, 0x60)
    movi32(RCX, WS_FD_FILE)
    st32(RAX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x48)
    st(RAX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RAX, WS_FD_POS, RCX)
    a.call("__ws_alloc_fd")
    cmp_eax_m1()
    jcc_l("e", "__ws_pipe_full")
    st(RSP, 0x68, RAX)
    st(RSP, 0x70, RDX)
    ld(RAX, RSP, 0x60)
    movi32(RCX, WS_FD_FILE)
    st32(RAX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x48)
    st(RAX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RAX, WS_FD_POS, RCX)
    ld(RAX, RSP, 0x70)
    movi32(RCX, WS_FD_FILE)
    st32(RAX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x50)
    st(RAX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RAX, WS_FD_POS, RCX)
    ld(RDI, RSP, 0x40)
    ld(RAX, RSP, 0x58)
    st32(RDI, 0, RAX)
    ld(RAX, RSP, 0x68)
    st32(RDI, 4, RAX)
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_pipe_full")
    ld(RCX, RSP, 0x48)
    api("CloseHandle")
    ld(RCX, RSP, 0x50)
    api("CloseHandle")
    movi(RAX, -24)                           # EMFILE
    jmp_l("__ws_ret")

    # ---- mmap(addr,len,prot,flags,fd,off) --------------------------------
    a.label("__ws_mmap")
    st(RSP, 0x38, RSI)                       # len
    st(RSP, 0x40, RDX)                       # prot
    st(RSP, 0x48, R10)                       # flags
    st(RSP, 0x50, R8)                        # fd
    st(RSP, 0x58, R9)                        # off
    ld(RAX, RSP, 0x38)
    test_rax()
    jcc_l("e", "__ws_einval")
    test_rr(RDI, RDI)
    jcc_l("ne", "__ws_einval")               # 固定地址映射不支持
    ld(RAX, RSP, 0x48)
    a.emit(b"\xA9\x20\x00\x00\x00")          # test eax, MAP_ANONYMOUS
    jcc_l("ne", "__ws_mmap_anon")
    jmp_l("__ws_mmap_file")
    a.label("__ws_mmap_anon")
    ld(RDI, RSP, 0x40)
    a.call("__ws_winprot")
    a.emit(b"\x31\xC9")                      # lpAddress = NULL
    ld(RDX, RSP, 0x38)
    movi32(R8, 0x3000)                       # MEM_COMMIT|MEM_RESERVE
    a.emit(mov_rr(R9, RAX))
    api("VirtualAlloc")
    test_rax()
    jcc_l("e", "__ws_mmap_nomem")
    st(RSP, 0x60, RAX)
    ld(RDX, RSP, 0x38)
    a.emit(b"\x31\xC9")                      # map handle = 0
    a.call("__ws_map_rec")
    ld(RAX, RSP, 0x60)
    jmp_l("__ws_ret")
    a.label("__ws_mmap_file")
    ld(RAX, RSP, 0x58)
    test_rax()
    jcc_l("ne", "__ws_einval")               # 只支持 off = 0
    ld(RDI, RSP, 0x40)
    a.call("__ws_winprot")
    st(RSP, 0x48, RAX)                       # win prot
    ld(RDI, RSP, 0x50)
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    a.emit(b"\x31\xD2")                      # attrs = NULL
    ld(R8, RSP, 0x48)
    a.emit(b"\x45\x31\xC9")                  # max high = 0
    ld(RAX, RSP, 0x38)
    st(RSP, 0x20, RAX)                       # max low = len
    a.emit(b"\x31\xC0")
    st(RSP, 0x28, RAX)                       # name = NULL
    api("CreateFileMappingA")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RSP, 0x58, RAX)                       # mapping handle
    a.emit(mov_rr(RCX, RAX))
    movi32(RDX, 4)                           # FILE_MAP_READ
    ld(RAX, RSP, 0x40)
    a.emit(b"\xF7\xC0\x02\x00\x00\x00")      # test eax, PROT_WRITE
    jcc_l("e", "__ws_mmap_acc")
    movi32(RDX, 2)                           # FILE_MAP_WRITE
    a.label("__ws_mmap_acc")
    a.emit(b"\x45\x31\xC0")                  # off high = 0
    a.emit(b"\x45\x31\xC9")                  # off low = 0
    ld(RAX, RSP, 0x38)
    st(RSP, 0x20, RAX)                       # bytes = len
    api("MapViewOfFile")
    test_rax()
    jcc_l("e", "__ws_mmap_mapfail")
    st(RSP, 0x60, RAX)
    ld(RDX, RSP, 0x38)
    ld(RCX, RSP, 0x58)
    ld(RAX, RSP, 0x60)
    a.call("__ws_map_rec")
    ld(RAX, RSP, 0x60)
    jmp_l("__ws_ret")
    a.label("__ws_mmap_mapfail")
    ld(RCX, RSP, 0x58)
    api("CloseHandle")
    jmp_l("__ws_fail")
    a.label("__ws_mmap_nomem")
    movi(RAX, -12)                           # ENOMEM
    jmp_l("__ws_ret")

    # ---- mprotect(addr, len, prot) --------------------------------------
    a.label("__ws_mprotect")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    a.emit(mov_rr(RDI, RDX))
    a.call("__ws_winprot")
    ld(RCX, RSP, 0x40)
    ld(RDX, RSP, 0x48)
    a.emit(mov_rr(R8, RAX))
    lea_rsp(R9, 0x50)
    api("VirtualProtect")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- munmap(addr, len) ----------------------------------------------
    a.label("__ws_munmap")
    st(RSP, 0x40, RDI)
    movi(R8, PE_STATE_VA + WS_MMAP)
    a.emit(b"\x45\x31\xC9")                  # xor r9d, r9d
    a.label("__ws_mu_loop")
    a.emit(b"\x41\x81\xF9" + i32(WS_MMAP_MAX))
    jcc_l("ae", "__ws_einval")
    ld(RAX, R8, 0)
    a.emit(b"\x48\x39\xF8")                  # cmp rax, rdi
    jcc_l("e", "__ws_mu_found")
    add_ri(R8, WS_MMAP_ENT)
    a.emit(b"\x41\x83\xC1\x01")              # add r9d, 1
    jmp_l("__ws_mu_loop")
    a.label("__ws_mu_found")
    st(RSP, 0x48, R8)
    ld(RCX, R8, 16)                          # 映射句柄
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_mu_anon")
    st(RSP, 0x50, RCX)
    ld(RCX, RSP, 0x40)
    api("UnmapViewOfFile")
    ld(RCX, RSP, 0x50)
    api("CloseHandle")
    jmp_l("__ws_mu_clear")
    a.label("__ws_mu_anon")
    ld(RCX, RSP, 0x40)
    a.emit(b"\x31\xD2")                      # size = 0
    movi32(R8, 0x8000)                       # MEM_RELEASE
    api("VirtualFree")
    a.label("__ws_mu_clear")
    ld(R8, RSP, 0x48)
    a.emit(b"\x31\xC0")
    st(R8, 0, RAX)
    st(R8, 8, RAX)
    st(R8, 16, RAX)
    xor_eax()
    jmp_l("__ws_ret")

    # ---- 身份 / 调度 -----------------------------------------------------
    a.label("__ws_zeroid")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")
    a.label("__ws_getppid")
    movi32(RAX, 1)
    jmp_l("__ws_ret")
    a.label("__ws_gettid")
    api("GetCurrentThreadId")
    zext_eax()
    jmp_l("__ws_ret")
    a.label("__ws_yield")
    sub_rsp(0x28)
    a.emit(b"\x31\xC9")                      # Sleep(0)
    api("Sleep")
    add_rsp28()
    xor_eax()
    jmp_l("__ws_ret")

    # ---- nanosleep(req, rem) --------------------------------------------
    a.label("__ws_nanosleep")
    ld(RCX, RDI, 8)                          # nsec
    movi(R10, 1000000)
    a.emit(mov_rr(RAX, RCX))
    a.emit(b"\x31\xD2")
    a.emit(rex(1, 0, 0, R10) + bytes([0xF7]) + modrm(3, 6, R10))   # div r10
    ld(RCX, RDI, 0)                          # sec
    movi(R8, 1000)
    a.emit(rex(1, RCX, 0, R8) + bytes([0x0F, 0xAF]) + modrm(3, RCX, R8))   # imul rcx, r8
    add_rr(RAX, RCX)
    a.emit(mov_rr(RCX, RAX))
    api("Sleep")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- gettimeofday(tv, tz) -------------------------------------------
    a.label("__ws_gettimeofday")
    st(RSP, 0x40, RDI)
    lea_rsp(RCX, 0x30)
    api("GetSystemTimeAsFileTime")
    ld(RAX, RSP, 0x30)
    movi(RCX, 116444736000000000)
    sub_rr(RAX, RCX)
    movi(RCX, 10000000)
    a.emit(b"\x31\xD2")
    a.emit(b"\x48\xF7\xF1")                  # div rcx -> sec, rem(100ns)
    st(RSP, 0x48, RAX)
    a.emit(mov_rr(RAX, RDX))
    movi(R8, 10)
    a.emit(b"\x31\xD2")
    a.emit(rex(1, 0, 0, R8) + bytes([0xF7]) + modrm(3, 6, R8))    # div r8
    ld(RDI, RSP, 0x40)
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_gtd_done")
    ld(RCX, RSP, 0x48)
    st(RDI, 0, RCX)
    st(RDI, 8, RAX)
    a.label("__ws_gtd_done")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- clock_getres(clk, ts) ------------------------------------------
    a.label("__ws_clock_getres")
    test_rr(RSI, RSI)
    jcc_l("e", "__ws_cgr_done")
    a.emit(b"\x31\xC0")
    st(RSI, 0, RAX)
    movi32(RAX, 1)
    st(RSI, 8, RAX)
    a.label("__ws_cgr_done")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- getcwd(buf, size) ----------------------------------------------
    a.label("__ws_getcwd")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    a.emit(mov_rr(RCX, RSI))                 # nBufferLength
    a.emit(mov_rr(RDX, RDI))                 # lpBuffer
    api("GetCurrentDirectoryA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RCX, RSP, 0x48)
    a.emit(b"\x48\x39\xC8")                  # cmp rax, rcx
    jcc_l("ae", "__ws_erange")
    a.emit(b"\x48\xFF\xC0")                  # inc rax
    jmp_l("__ws_ret")
    a.label("__ws_erange")
    movi(RAX, -34)
    jmp_l("__ws_ret")

    # ---- chdir(path) -----------------------------------------------------
    a.label("__ws_chdir")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("SetCurrentDirectoryA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- rename(old, new) ------------------------------------------------
    a.label("__ws_rename")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    ld(RSI, RSP, 0x48)
    movi(R10, PE_STATE_VA + WS_TMP2)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    movi(RDX, PE_STATE_VA + WS_TMP2)
    movi32(R8, 1)                            # MOVEFILE_REPLACE_EXISTING
    api("MoveFileExA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- mkdir(path, mode) ----------------------------------------------
    a.label("__ws_mkdir")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    a.emit(b"\x31\xD2")
    api("CreateDirectoryA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- rmdir(path) -----------------------------------------------------
    a.label("__ws_rmdir")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("RemoveDirectoryA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- access(path, mode) ---------------------------------------------
    a.label("__ws_access")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")
    a.emit(b"\x83\xF8\xFF")                  # cmp eax, -1
    jcc_l("e", "__ws_enoent")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_enoent")
    movi(RAX, -2)
    jmp_l("__ws_ret")

    # ---- fsync / fdatasync(fd) ------------------------------------------
    a.label("__ws_fsync")
    st(RSP, 0x40, RDI)
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_fsync_ok")
    cmp_ecx(WS_FD_FILE)
    jcc_l("ne", "__ws_fail")
    ld(RCX, RAX, WS_FD_HANDLE)
    api("FlushFileBuffers")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    a.label("__ws_fsync_ok")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- ftruncate(fd, len) ---------------------------------------------
    a.label("__ws_ftruncate")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    cmp_edi(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_FILE)
    jcc_l("ne", "__ws_einval")
    ld(RCX, RAX, WS_FD_HANDLE)
    ld(RDX, RSP, 0x48)
    a.emit(b"\x45\x31\xC0")                  # newpos = NULL
    a.emit(b"\x45\x31\xC9")                  # FILE_BEGIN
    api("SetFilePointerEx")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    ld(RDI, RSP, 0x40)
    entry_of_fd()
    ld(RCX, RAX, WS_FD_HANDLE)
    api("SetEndOfFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- 别名 / at 变体 -------------------------------------------------
    a.label("__ws_pipe")
    a.emit(b"\x31\xF6")                      # xor esi, esi
    jmp_l("__ws_pipe2")
    a.label("__ws_dup3")
    jmp_l("__ws_dup2")
    a.label("__ws_dup")
    st(RSP, 0x40, RDI)
    a.call("__ws_alloc_fd")
    cmp_eax_m1()
    jcc_l("e", "__ws_emfile")
    ld(RDI, RSP, 0x40)
    a.emit(mov_rr(RSI, RAX))
    jmp_l("__ws_dup2")
    a.label("__ws_emfile")
    movi(RAX, -24)
    jmp_l("__ws_ret")
    a.label("__ws_exit_group")
    jmp_l("__ws_exit")
    a.label("__ws_clock_nanosleep")
    a.emit(mov_rr(RDI, RDX))
    a.emit(mov_rr(RSI, R10))
    jmp_l("__ws_nanosleep")
    a.label("__ws_unlinkat")
    a.emit(b"\xF7\xC2" + i32(0x200))         # test edx, AT_REMOVEDIR
    jcc_l("e", "__ws_unlinkat_file")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_rmdir")
    a.label("__ws_unlinkat_file")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_unlink")
    a.label("__ws_mkdirat")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_mkdir")
    a.label("__ws_renameat")
    a.emit(mov_rr(RDI, RSI))
    a.emit(mov_rr(RSI, R10))
    jmp_l("__ws_rename")
    a.label("__ws_faccessat")
    a.emit(mov_rr(RDI, RSI))
    jmp_l("__ws_access")

    # ---- chmod / umask ---------------------------------------------------
    a.label("__ws_chmod")
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")                # Windows 无 POSIX 权限位：存在即成功
    a.emit(b"\x83\xF8\xFF")                  # cmp eax, -1
    jcc_l("e", "__ws_enoent")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_umask")
    ld(RAX, RBX, WS_UMASK)
    st(RBX, WS_UMASK, RDI)
    jmp_l("__ws_ret")

    # ---- madvise / msync -------------------------------------------------
    a.label("__ws_madvise")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")
    a.label("__ws_msync")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    movi(R8, PE_STATE_VA + WS_MMAP)
    a.emit(b"\x45\x31\xC9")                  # xor r9d, r9d
    a.label("__ws_ms_loop")
    a.emit(b"\x41\x81\xF9" + i32(WS_MMAP_MAX))
    jcc_l("ae", "__ws_einval")
    ld(RAX, R8, 0)
    a.emit(b"\x48\x39\xF8")                  # cmp rax, rdi
    jcc_l("e", "__ws_ms_found")
    add_ri(R8, WS_MMAP_ENT)
    a.emit(b"\x41\x83\xC1\x01")              # add r9d, 1
    jmp_l("__ws_ms_loop")
    a.label("__ws_ms_found")
    ld(RCX, R8, 16)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_ms_anon")               # 匿名块没有"落盘"这回事
    ld(RCX, RSP, 0x40)
    ld(RDX, RSP, 0x48)
    api("FlushViewOfFile")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    a.label("__ws_ms_anon")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- truncate(path, len) --------------------------------------------
    a.label("__ws_truncate")
    st(RSP, 0x40, RDI)
    st(RSP, 0x48, RSI)
    a.emit(mov_rr(RSI, RDI))
    movi(R10, PE_STATE_VA + WS_TMP)
    a.call("__ws_translate2")
    movi(RCX, PE_STATE_VA + WS_TMP)
    movi32(RDX, 0x40000000)                  # GENERIC_WRITE
    a.emit(b"\x41\xB8\x03\x00\x00\x00")      # mov r8d, 3
    a.emit(b"\x45\x31\xC9")                  # r9d = NULL
    movi32(RAX, 3)                           # OPEN_EXISTING
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x80)                        # FILE_ATTRIBUTE_NORMAL
    st(RSP, 0x28, RAX)
    a.emit(b"\x31\xC0")
    st(RSP, 0x30, RAX)                       # hTemplate = NULL
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")              # cmp rax, -1
    jcc_l("e", "__ws_fail")
    st(RSP, 0x50, RAX)
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x48)
    a.emit(b"\x45\x31\xC0")                  # newpos = NULL
    a.emit(b"\x45\x31\xC9")                  # FILE_BEGIN
    api("SetFilePointerEx")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_trunc_fail")
    ld(RCX, RSP, 0x50)
    api("SetEndOfFile")
    st(RSP, 0x58, RAX)
    ld(RCX, RSP, 0x50)
    api("CloseHandle")
    ld(RAX, RSP, 0x58)
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_fail")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_trunc_fail")
    ld(RCX, RSP, 0x50)
    api("CloseHandle")
    jmp_l("__ws_fail")

    # （cmdline / uname 字面量在 .idata 的固定偏移上，shim 用常量取址，不必内嵌）


class PeEmitter(Emitter):
    """把 syscall 改成走 shim、把入口桩改成 Windows 形态，其余降级与 ELF 目标共用。"""

    #: PE 目标标记 —— `_call` 用它把"只有 ELF 兑现得了的降级"点名拒（`docs/348`）。
    pe_target = True

    def _emit_syscall(self) -> None:
        self.asm.call("__win_syscall")

    def _call_spawn(self, m, d):
        """`thread_spawn` 是 Linux 的 `clone` 直译 —— Windows 侧没有对应物（`CreateThread`
        走的是另一套入口约定：它**要一个地址**，而那正是本语言取不到的东西）。
        所以这里**响亮地拒绝**，不静默错编一个假的线程出来（`docs/245`）。"""
        raise Unsupported("thread_spawn 只在 ELF 目标可用 —— Windows 没有 clone (docs/245)")

    def emit_entry_stub(self, start_name: str) -> None:
        a = self.asm
        a.label("__entry")
        a.emit(b"\x48\x83\xEC\x28")                # sub rsp, 0x28
        a.call(start_name)
        a.emit(mov_ri32(RAX, SYS_EXIT))            # exit(0) 也走 shim，保持单一路径
        a.emit(mov_ri32(RDI, 0))
        a.call("__win_syscall")
        a.emit(b"\x0F\x0B")


def build_pe(text: bytes, data: bytes, entry_rva: int, idata: bytes) -> bytes:
    """静态 PE32+（console, x86-64）。四个节都钉在固定 RVA：.text / .idata / .data / .state。"""
    nsec = 3
    hdr = 0x40 + 4 + 20 + 240 + 40 * nsec
    toff = align_up(hdr, PE_FILE_ALIGN)                      # .text 的 raw
    ioff = toff + align_up(len(text), PE_FILE_ALIGN)         # .idata 的 raw
    doff = ioff + align_up(len(idata), PE_FILE_ALIGN)        # .data 的 raw
    image_size = align_up(PE_STATE_RVA + WS_SIZE, PE_SEC_ALIGN)

    o = bytearray()
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    o += dos
    o += b"PE\x00\x00"
    o += struct.pack("<HHIIIHH", 0x8664, nsec, 0, 0, 0, 240, 0x22)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    opt[2] = 14
    struct.pack_into("<I", opt, 16, entry_rva)
    struct.pack_into("<I", opt, 20, PE_TEXT_RVA)             # BaseOfCode
    struct.pack_into("<Q", opt, 24, PE_IMAGE_BASE)
    struct.pack_into("<I", opt, 32, PE_SEC_ALIGN)
    struct.pack_into("<I", opt, 36, PE_FILE_ALIGN)
    struct.pack_into("<HHHHHH", opt, 40, 6, 0, 0, 0, 6, 0)
    # PE32+ 的 SizeOfImage / SizeOfHeaders 在 56 / 60；写成 PE32 的 54 / 58 会把值落进
    # Win32VersionValue 槽，加载器读到 SizeOfHeaders=0 直接拒收（"不是有效的 Win32 应用程序"）。
    struct.pack_into("<I", opt, 56, image_size)
    struct.pack_into("<I", opt, 60, toff)
    struct.pack_into("<H", opt, 68, 3)                       # Subsystem: console
    # 栈留 16 MB：编译器自己的递归下降比小工具深得多（Linux 那侧默认 8 MB，本来就够宽）。
    struct.pack_into("<Q", opt, 72, 16 * 1024 * 1024)
    # 提交 8 MB：**保留够但提交不够时加载器不替我们长**（实测：`std/ansi.lomt` 这类
    # 大单元在 1 MB 提交上直接访问违例；自举编译器自己的递归下降要得更深）。
    struct.pack_into("<Q", opt, 80, 8 * 1024 * 1024)
    struct.pack_into("<Q", opt, 88, 0x100000)
    struct.pack_into("<Q", opt, 96, 0x1000)
    struct.pack_into("<I", opt, 108, 16)
    struct.pack_into("<II", opt, 112 + 8, PE_IDATA_RVA, PE_IMPORT_DESC_BYTES)  # Import Directory
    o += opt

    def sec(name, vs, rva, rs, rp, ch):
        h = bytearray(40)
        h[0:len(name)] = name
        struct.pack_into("<I", h, 8, vs)
        struct.pack_into("<I", h, 12, rva)
        struct.pack_into("<I", h, 16, rs)
        struct.pack_into("<I", h, 20, rp)
        struct.pack_into("<I", h, 36, ch)
        return bytes(h)

    # 每节的 VirtualSize 一直铺到下一节的起点：加载器**不接受节间有空洞**（实测：只要
    # .text 与 .idata 之间留 0x1000 的空隙就直接 "不是有效的 Win32 应用程序"）。
    # 铺满既消掉空洞，又保住了各节的固定 RVA。
    o += sec(b".text", PE_IDATA_RVA - PE_TEXT_RVA, PE_TEXT_RVA,
             align_up(len(text), PE_FILE_ALIGN), toff, 0x60000020)
    o += sec(b".idata", PE_DATA_RVA - PE_IDATA_RVA, PE_IDATA_RVA,
             align_up(len(idata), PE_FILE_ALIGN), ioff, 0xC0000040)
    o += sec(b".data", PE_STATE_RVA - PE_DATA_RVA + WS_SIZE, PE_DATA_RVA,
             align_up(len(data), PE_FILE_ALIGN), doff, 0xC0000040)
    o += b"\x00" * (toff - len(o)) + text
    o += b"\x00" * (ioff - len(o)) + idata
    # 最后一节也必须补齐到声明的 SizeOfRawData，否则加载器读到 EOF 之外直接拒收
    o += b"\x00" * (doff - len(o)) + bytes(data)
    o += b"\x00" * (align_up(len(data), PE_FILE_ALIGN) - len(data))
    return bytes(o)


def _pe_layout_globals(globals_: list[Global], data_rva: int) -> tuple[bytearray, int]:
    """把全局布到 data/bss。内容与 data_rva 无关，只有地址跟着变。"""
    DATA_VADDR = PE_IMAGE_BASE + data_rva          # noqa: N806 (与 ELF 目标同名同义)
    data, bss = bytearray(), 0
    for g in globals_:
        if g.data != b"\0" * size_of(g.ty):
            while len(data) % 8:
                data.append(0)
            g.addr = DATA_VADDR + len(data)
            data += g.data
    while len(data) % 8:
        data.append(0)
    for g in globals_:
        sz = size_of(g.ty)
        if g.data == b"\0" * sz:
            bss = align_up(bss, 8)
            g.addr = DATA_VADDR + len(data) + bss
            bss += align_up(sz, 8)
    return data, bss


def dump_win_shim(path) -> int:
    """把 `__win_syscall` 的机器码**冻结**成一段 blob（自举镜像照抄这一份）。

    四个节的 RVA 与 IAT 槽都是编译期常量，所以这段 blob 与布局**完全无关** ——
    里面一个待回填的地址都没有，镜像把它原样摆在 .text 里即可。
    判据：镜像产出的 PE 与参考实现产出的 PE 行为一致（`tools/loment_pe_test.py`）。
    """
    a = Asm(0)
    em = PeEmitter([])
    em.asm = a
    _idata, slots = build_pe_idata()
    emit_win_shim(em, slots)
    blob = a.finalize()
    Path(path).write_bytes(blob)
    # 再生成自举镜像用的**自包含**数据模块（hex 内嵌）：镜像是独立二进制，
    # 不该依赖运行时去读仓库里的文件。
    idata, _slots = build_pe_idata()

    def hex_parts(data):
        h = data.hex()
        return [h[i:i + 400] for i in range(0, len(h), 400)]

    def part_fn(name, parts):
        out = [f"pub fn {name}(i: u32) -> str {{"]
        out += [f'    if i == {k} {{ return "{p}"; }}' for k, p in enumerate(parts)]
        out += ['    return "";', "}", ""]
        return out

    sparts, iparts = hex_parts(blob), hex_parts(idata)
    src = [
        "// win_shim_data.lomt — 由 `tools/lomelf.py --dump-win-shim` 生成，别手改。",
        "//",
        f"// 两段**与布局无关**的数据：`__win_syscall` 的机器码（{len(blob)} 字节）与",
        f"// 导入表（{len(idata)} 字节，kernel32 + ws2_32，含 /proc/self/cmdline 字面量）。四个节钉在固定",
        "// RVA、IAT 槽是常量，所以它们里面一个待回填的地址都没有 —— 自举镜像原样搬即可。",
        "//",
        "// **故意按片给、不做 str_concat**：运行时的 bump 堆只有 64 KiB（lomentc.py 的",
        "// alloc_ir，超了直接 abort = ud2 = SIGILL），拼一个 8 KB 的 hex 串就会撞顶。",
        "// 调用方逐片解、逐片写进输出即可。",
        "",
        "module win_shim_data",
        "",
        f"pub fn n_shim() -> u32 {{ return {len(sparts)}; }}",
        "",
    ]
    src += part_fn("shim_part", sparts)
    src += [f"pub fn n_idata() -> u32 {{ return {len(iparts)}; }}", ""]
    src += part_fn("idata_part", iparts)
    shim_src = Path(__file__).resolve().parent.parent / "loment" / "tools" / "win_shim_data.lomt"
    shim_src.write_text("\n".join(src), encoding="utf-8", newline="\n")
    print(f"[OK] {path} ({len(blob)} B) + {shim_src.name}")
    return 0


def _entry_name(funcs) -> str:
    """用户态入口名：`_start` 优先；没有它时 `main` 接手。

    `main` 不许有形参 —— 入口桩按无参 ABI 调它，带形参的 `main` 读到的是寄存器里
    的残留值（那是静默错，不是报错）。`_start` 仍然优先，所以裸机/内核那一路不受影响。
    """
    if any(f.name == "_start" for f in funcs):
        return "_start"
    mains = [f for f in funcs if f.name == "main"]
    if not mains:
        raise Unsupported("没有 _start 或 main 入口（原生产物 v0 需要一个用户态入口）")
    if mains[0].params:
        raise Unsupported("main 不能有形参（没有 _start 时它就是用户态入口）")
    return "main"


def _pe_emit(funcs, globals_, slots, entry: str) -> tuple[bytes, int]:
    em = PeEmitter(globals_)
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    em.emit_entry_stub(entry)
    emit_win_shim(em, slots)
    for f in funcs:
        em.emit_func(f)
    return em.asm.finalize(), em.asm.labels["__entry"]


def compile_pe(text: str) -> tuple[bytes, dict]:
    global TEXT_VADDR
    globals_, funcs, _externs = parse_ll(text)
    entry = _entry_name(funcs)

    # 四个节的 RVA 全是常量，所以**不需要不动点**：发射一遍就完事。
    # （早先按"代码长度决定 .data 的 RVA"排过一次，那是为了迁就 .text 只能钉在最低 RVA；
    #   现在每节都钉死了，两遍发射连同它引发的节区重叠问题一起消失。）
    TEXT_VADDR = PE_IMAGE_BASE + PE_TEXT_RVA
    data, bss = _pe_layout_globals(globals_, PE_DATA_RVA)
    idata, slots = build_pe_idata()
    code, entry = _pe_emit(funcs, globals_, slots, entry)

    blob = build_pe(code, bytes(data), entry - PE_IMAGE_BASE, idata)
    return blob, {"text": len(code), "data": len(data), "bss": bss,
                  "entry": entry - PE_IMAGE_BASE, "funcs": [f.name for f in funcs]}


# ------------------------------------------------- PE 的 --opt：接 clang 编出来的 COFF（docs/212 §5A）
#
# 默认档是 lomelf 自己的栈机（逐字节可复现）；`--opt` 把**同一份 IR** 交给 clang，
# 只把编译结果（一个 x86-64 COFF 目标文件）接回 PE 容器。运行期（`__win_syscall`、
# 导入表、入口桩）仍是 lomelf 发的那些字节 —— 不许第二份 Win32 仿真层。

#: clang 在 x64 COFF 上会发的重定位（实测覆盖：整份自举驱动只有 REL32）。
COFF_ADDR64, COFF_ADDR32, COFF_ADDR32NB = 0x0001, 0x0002, 0x0003
COFF_REL32 = 0x0004
#: REL32 的五个变体只差"从字段后第几个字节算相对量"（0..5）。
COFF_REL32_NAMES = {COFF_REL32 + n: n for n in range(6)}
#: 没数据、纯链接器元数据的节：丢掉是对的（relocation 指向它们时一并丢）。
COFF_SKIP_CHARS = 0x00000800          # IMAGE_SCN_LNK_REMOVE
COFF_CNT_CODE = 0x00000020
COFF_CNT_IDATA = 0x00000040
COFF_CNT_UDATA = 0x00000080
COFF_ALIGN_MASK = 0x00F00000


class CoffObject:
    """一个 x86-64 COFF 目标文件（clang --target=x86_64-pc-windows-msvc -c）。

    只取本档要用的三样：节字节、符号表、重定位表。**不认识的重定位点名拒** ——
    猜一个偏移的后果是运行期跳到错地址，那比编译期报错坏得多。
    """

    def __init__(self, name: str, raw: bytes):
        self.name = name
        if len(raw) < 20:
            raise Unsupported(f"{name}: 不是 COFF 目标文件（太短）")
        machine, nsect, _stamp, symptr, nsyms, _optsz, chars = struct.unpack_from(
            "<HHIIIHH", raw, 0)
        if machine != 0x8664:
            raise Unsupported(f"{name}: 目标机器 {machine:#x}，只收 x86-64")
        if chars & 0x0002:
            raise Unsupported(f"{name}: 这是 PE 映像，不是可重定位目标文件")
        if len(raw) < 20 + 40 * nsect:
            raise Unsupported(f"{name}: 节表越界")
        strtab = b""
        strtab_at = symptr + 18 * nsyms
        if nsyms and strtab_at + 4 <= len(raw):
            size = struct.unpack_from("<I", raw, strtab_at)[0]
            if size >= 4:
                strtab = raw[strtab_at:min(len(raw), strtab_at + size)]

        def sym_name(field: bytes) -> str:
            if field[:4] == b"\x00\x00\x00\x00":
                off = struct.unpack_from("<I", field, 4)[0]
                if 4 <= off < len(strtab):
                    end = strtab.find(b"\x00", off)
                    return strtab[off:end if end >= 0 else len(strtab)].decode(
                        "utf-8", "replace")
                return ""
            return field.rstrip(b"\x00").decode("utf-8", "replace")

        self.sections = []
        for k in range(nsect):
            off = 20 + 40 * k
            rawname = raw[off:off + 8]
            if rawname[:1] == b"/":
                try:
                    no = int(rawname[1:].rstrip(b"\x00"))
                    end = strtab.find(b"\x00", no)
                    sname = strtab[no:end if end >= 0 else len(strtab)].decode(
                        "utf-8", "replace")
                except ValueError:
                    sname = rawname.rstrip(b"\x00").decode("utf-8", "replace")
            else:
                sname = rawname.rstrip(b"\x00").decode("utf-8", "replace")
            (vsize, _va, rsize, rptr, rlcptr, _lnptr, nrlc, _nln,
             sch) = struct.unpack_from("<IIIIIIHHI", raw, off + 8)
            data = raw[rptr:rptr + rsize] if rsize and rptr else b""
            # `.bss` 的字节不在文件里（PointerToRawData = 0），大小在 VirtualSize/原始大小里。
            size = max(vsize, rsize)
            relocs = []
            for j in range(nrlc):
                at = rlcptr + 10 * j
                ra, sym, ty = struct.unpack_from("<IIH", raw, at)
                relocs.append((ra, sym, ty))
            self.sections.append({"name": sname, "data": data, "size": size,
                                  "chars": sch, "relocs": relocs})

        # 符号表：aux 记录占名额但不可被重定位引用 —— 索引保持原样，缺口留 None。
        self.syms: list[dict | None] = [None] * nsyms
        i = 0
        while i < nsyms:
            off = symptr + 18 * i
            nm8, value, secnum, _typ, cls, naux = struct.unpack_from(
                "<8sIhHBB", raw, off)
            self.syms[i] = {"name": sym_name(nm8), "value": value,
                            "section": secnum, "class": cls}
            i += 1 + naux


def pe_opt_ir(text: str) -> str:
    """把 IR 里的内联 `syscall` 换成 `@__win_syscall_c`（同一个号、同一组实参）。

    这一步在 **clang 之前**：换完之后 clang 看到的是普通调用，能照常内联/优化。
    `thread_spawn` 在这里点名拒 —— Windows 没有 `clone`，与其静默编一段假的，不如说清。
    """
    declare = ("declare i64 @__win_syscall_c(i64, i64, i64, i64, i64, i64, i64)")
    out, used = [], False
    pat = re.compile(r'^(\s*)(%[\w.$-]+) = call i64 asm sideeffect "([^"]*)", '
                     r'"[^"]*"\((.*)\)$')
    for line in text.splitlines():
        m = pat.match(line)
        if not m:
            out.append(line)
            continue
        indent, dest, name, args = m.groups()
        if name != "syscall":
            raise Unsupported(f"PE --opt 不认识的内联汇编: {name!r}（docs/245）")
        vals = []
        for part in args.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.startswith("i64 "):
                raise Unsupported(f"PE --opt: 系统调用实参不是 i64: {part!r}")
            vals.append(part[4:].strip())
        while len(vals) < 7:
            vals.append("0")
        call = ", ".join(f"i64 {v}" for v in vals[:7])
        out.append(f"{indent}{dest} = call i64 @__win_syscall_c({call})")
        used = True
    if used:
        out.insert(0, declare)
        out.insert(1, "")
    return "\n".join(out) + "\n"


def _pe_link_object(obj: CoffObject, asm: Asm, data: bytearray,
                    externs: dict[str, int]) -> dict[str, int]:
    """把对象摆进 PE 的 .text/.data：节数组装 + 重定位回填。

    `externs` 是 lomelf 自己发的那几个符号（入口桩、`__win_syscall_c`、三个 libcall），
    名字 -> VA。返回对象的**已定义外部符号**（名字 -> VA），入口桩靠它找 `_start`。
    """
    base_va = PE_IMAGE_BASE
    text_start = align_up(len(asm.buf), 16)
    asm.buf += b"\x00" * (text_start - len(asm.buf))
    sec_place: list[dict | None] = []
    bss: list[tuple[int, dict]] = []                 # (序号, 节) —— 等初始数据摆完再定位
    for si, s in enumerate(obj.sections):
        chars = s["chars"]
        if chars & COFF_SKIP_CHARS or not (chars & (COFF_CNT_CODE | COFF_CNT_IDATA
                                                    | COFF_CNT_UDATA)):
            sec_place.append(None)                   # 链接器元数据：丢掉
            continue
        align = 1
        if chars & COFF_ALIGN_MASK:
            align = 1 << (((chars & COFF_ALIGN_MASK) >> 20) - 1)
        if chars & COFF_CNT_CODE:
            off = align_up(len(asm.buf), max(align, 16))
            asm.buf += b"\x00" * (off - len(asm.buf))
            at = off
            asm.buf += s["data"]
            sec_place.append({"buf": asm.buf, "at": at, "va": TEXT_VADDR + off})
        elif chars & COFF_CNT_IDATA:
            off = align_up(len(data), align)
            data += b"\x00" * (off - len(data))
            at = off
            data += s["data"]
            sec_place.append({"buf": data, "at": at,
                              "va": base_va + PE_DATA_RVA + off})
        else:                                        # .bss：只占地址，不占文件
            sec_place.append(None)
            bss.append((si, s))
    bss_off = 0
    bss_base = len(data)
    for si, s in bss:
        chars = s["chars"]
        align = 1
        if chars & COFF_ALIGN_MASK:
            align = 1 << (((chars & COFF_ALIGN_MASK) >> 20) - 1)
        off = align_up(bss_off, align)
        bss_off = off + max(1, s["size"])
        sec_place[si] = {"buf": None, "at": None,
                         "va": base_va + PE_DATA_RVA + bss_base + off}

    # 符号 -> VA（重定位按**符号表索引**解析，同名 COMDAT 常量不会串）
    sym_va: dict[int, int] = {}
    defined: dict[str, int] = {}
    for i, sym in enumerate(obj.syms):
        if sym is None:
            continue
        sec = sym["section"]
        if sec > 0 and sec <= len(sec_place) and sec_place[sec - 1] is not None:
            va = sec_place[sec - 1]["va"] + sym["value"]
            sym_va[i] = va
            if sym["class"] == 2 and sym["name"]:    # IMAGE_SYM_CLASS_EXTERNAL
                defined[sym["name"]] = va

    undef: list[str] = []
    for si, s in enumerate(obj.sections):
        place = sec_place[si]
        if place is None:
            continue
        for ra, symi, ty in s["relocs"]:
            sym = obj.syms[symi] if symi < len(obj.syms) else None
            if sym is None:
                raise Unsupported(f"{obj.name}: 重定位指向辅助记录（节 {s['name']}）")
            sec = sym["section"]
            if sec > 0:
                if sec > len(sec_place) or sec_place[sec - 1] is None:
                    continue                          # 指向被丢掉的节：空转
                target = sym_va[symi]
            elif sec == 0:
                name = sym["name"]
                if name not in externs:
                    undef.append(name)
                    continue
                target = externs[name]
            else:
                raise Unsupported(f"{obj.name}: 绝对符号重定位 {sym['name']!r}")
            if place["buf"] is None:
                raise Unsupported(f"{obj.name}: .bss 上不该有重定位（{s['name']}+{ra}）")
            field = place["at"] + ra
            buf = place["buf"]
            p_va = place["va"] + ra
            if ty in COFF_REL32_NAMES:
                add = struct.unpack_from("<i", buf, field)[0]
                rel = target + add - (p_va + 4 + COFF_REL32_NAMES[ty])
                struct.pack_into("<i", buf, field, rel)
            elif ty == COFF_ADDR64:
                add = struct.unpack_from("<q", buf, field)[0]
                struct.pack_into("<Q", buf, field, target + add)
            elif ty == COFF_ADDR32:
                add = struct.unpack_from("<I", buf, field)[0]
                struct.pack_into("<I", buf, field, (target + add) & 0xFFFFFFFF)
            elif ty == COFF_ADDR32NB:
                add = struct.unpack_from("<I", buf, field)[0]
                struct.pack_into("<I", buf, field,
                                 (target + add - PE_IMAGE_BASE) & 0xFFFFFFFF)
            else:
                raise Unsupported(
                    f"{obj.name}: 不认识的 COFF 重定位类型 {ty:#x}（{s['name']}+{ra}）")
    if undef:
        names = ", ".join(sorted(set(undef)))
        raise Unsupported(f"{obj.name}: 未定义的符号: {names}")
    # 对象里的 `_start`/`main` 交给入口桩——桩里的 call 是名字键，finalize 时回填
    for nm, va in defined.items():
        if nm not in asm.labels:
            asm.labels[nm] = va
    return defined


def _pe_find_clang() -> str | None:
    cc = os.environ.get("LOMENT_CC")
    if cc and Path(cc).exists():
        return cc
    hit = shutil.which("clang")
    if hit:
        return hit
    fallback = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if fallback.exists():
        return str(fallback)
    return None


def _pe_opt_obj(text: str, cc: str) -> bytes:
    """`pe_opt_ir` 之后把 IR 交给 clang，收回一个 COFF 目标文件的字节。"""
    with tempfile.TemporaryDirectory(prefix="loment-peopt-") as td:
        ll = Path(td) / "unit.ll"
        obj = Path(td) / "unit.obj"
        ll.write_text(pe_opt_ir(text), encoding="utf-8", newline="\n")
        flags = os.environ.get("LOMENT_PE_OPT_FLAGS", "-O2").split()
        r = subprocess.run(
            [cc, "--target=x86_64-pc-windows-msvc", *flags, "-c",
             "-ffreestanding", "-fno-stack-protector", "-mno-stack-arg-probe",
             "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
             "-fno-exceptions", "-fno-rtti", "-w",
             "-o", str(obj), str(ll)],
            capture_output=True, text=True, shell=False)
        if r.returncode != 0:
            raise Unsupported(f"clang 没收下这份 IR: {r.stderr.strip()[-400:]}")
        return obj.read_bytes()


def compile_pe_opt(text: str, cc: str | None = None) -> tuple[bytes, dict]:
    """PE 的 `--opt` 档：同一份 IR，clang -O2 的用户代码 + lomelf 的运行期。

    与默认档的差别只有**用户代码怎么编**；入口、`__win_syscall`、导入表、节布局
    全部走同一套发射器 —— 两条路因此不会在 Win32 语义上分叉。
    """
    global TEXT_VADDR
    cc = cc or _pe_find_clang()
    if cc is None:
        raise Unsupported("PE --opt 需要 clang（设置 LOMENT_CC 或把它放进 PATH）")
    _globals, funcs, _externs = parse_ll(text)
    entry = _entry_name(funcs)
    TEXT_VADDR = PE_IMAGE_BASE + PE_TEXT_RVA
    idata, slots = build_pe_idata()

    em = PeEmitter([])
    a = em.asm
    # 入口桩：Windows 的进程入口栈对齐与 C 约定差一层，先摆正再 call。
    a.label("__entry")
    a.emit(b"\x48\x83\xE4\xF0")                # and rsp, -16
    a.emit(b"\x48\x83\xEC\x20")                # sub rsp, 32（影子空间）
    a.call(entry)
    a.emit(mov_ri32(RAX, SYS_EXIT))
    a.emit(mov_ri32(RDI, 0))
    a.call("__win_syscall")
    a.emit(b"\x0F\x0B")
    emit_win_shim(em, slots)
    emit_win_syscall_c(em)
    emit_win_libcalls(em)
    externs = {nm: a.labels[nm] for nm in ("__win_syscall_c", "memset", "memcpy",
                                           "memmove")}

    obj = CoffObject("clang 目标文件", _pe_opt_obj(text, cc))
    data = bytearray()
    _pe_link_object(obj, a, data, externs)

    code = a.finalize()
    entry_rva = a.labels["__entry"] - PE_IMAGE_BASE
    blob = build_pe(code, bytes(data), entry_rva, idata)
    return blob, {"text": len(code), "data": len(data), "bss": 0,
                  "entry": entry_rva, "opt": True, "funcs": [f.name for f in funcs]}


# ------------------------------------------------------------------ 驱动


#: 我们实现的重定位类型 (docs/173 阶段 2)。值就是 ELF 的 `R_X86_64_*` 编号。
REL_64 = 1        # S + A
REL_PC32 = 2      # S + A - P
REL_PLT32 = 4     # 同 PC32 —— 静态链接里没有 PLT, 两者等价 (GNU ld 也这么处理)
REL_GOTPCREL = 9  # G + A - P：`-fPIC` 的对象按 GOT 槽取地址（`#609`）
REL_32 = 10       # (S + A) 的低 32 位, 无符号
REL_32S = 11      # 同上, 有符号 (x86-64 上 addrmode 的绝对值走这条)
REL_TPOFF64 = 18  # S + A - TP：TLS 变量相对线程指针的 64 位偏移（`#610`）
REL_TPOFF32 = 23  # 同上，32 位
REL_PC64 = 24     # S + A - P, 64 位
REL_GOTPCRELX = 41      # 同 GOTPCREL，允许把 `mov` 松弛成 `lea`；不松弛也正确
REL_REX_GOTPCRELX = 42  # 带 REX 前缀的 GOTPCRELX
SUPPORTED_RELOCS = (REL_64, REL_PC32, REL_PLT32, REL_GOTPCREL, REL_32, REL_32S,
                    REL_TPOFF64, REL_TPOFF32, REL_PC64, REL_GOTPCRELX,
                    REL_REX_GOTPCRELX)

#: 重定位类型名（只为了**把话说清楚**）。`#610` / `#609` 之前，不认识的类型只报一个
#: 数字 —— 用户拿着 "不认识的重定位类型 23" 什么也做不了。名字让这条消息可行动。
RELOC_NAMES = {
    3: "R_X86_64_GOT32", 5: "R_X86_64_COPY", 6: "R_X86_64_GLOB_DAT",
    7: "R_X86_64_JUMP_SLOT", 8: "R_X86_64_RELATIVE", 9: "R_X86_64_GOTPCREL",
    12: "R_X86_64_16", 13: "R_X86_64_PC16", 14: "R_X86_64_8",
    15: "R_X86_64_PC8", 16: "R_X86_64_DTPMOD64", 17: "R_X86_64_DTPOFF64",
    18: "R_X86_64_TPOFF64", 19: "R_X86_64_TLSGD", 20: "R_X86_64_TLSLD",
    21: "R_X86_64_DTPOFF32", 22: "R_X86_64_GOTTPOFF", 23: "R_X86_64_TPOFF32",
    25: "R_X86_64_GOTOFF64", 26: "R_X86_64_GOTPC32", 41: "R_X86_64_GOTPCRELX",
    42: "R_X86_64_REX_GOTPCRELX",
}


def reloc_name(ty: int) -> str:
    return RELOC_NAMES.get(ty, f"#{ty}")

#: 我们**不摆**、丢掉也无害的节: 展开信息与调试信息。代码引用不到它们, 所以针对它们的重定位
#: 是空转 —— **跳过而不是报错**。
#:
#: **为什么只放这几个**: `.data`/`.rodata` 也"我们不摆", 但丢掉它们**不是无害的** —— 代码
#: 里的字符串常量就住在 `.rodata`, 丢了会让程序读到垃圾 (静默错)。所以只有"代码引用不到"的
#: 元数据节能进这张名单, 其余一律仍按"指向非代码节"硬拒。
#: 实测来源: Zig 的 `-OReleaseSmall` 对象只剩一条 `.rela.eh_frame`, 不放行它整个语言就用不了。
_META_SEC = (".eh_frame", ".debug", ".comment", ".note")


class ForeignObject:
    """一个外部 ELF64 可重定位目标文件里我们真正要用的那点东西 (docs/173 阶段 1/2)。

    **取什么**: `.text` 的字节 + 节在其中的偏移 + 它导出的全局符号 (名字 -> 段内偏移)
    + **重定位表**。三条边界仍然**硬失败**而不是猜:

    * 重定位类型不认识 → 报错 (猜的后果是"跳到错地址", 那是运行期崩溃而不是编译期报错);
    * 重定位指向**非代码节** (`.data`/`.rodata`) → 报错 —— 本档只摆代码节;
    * 一个符号**有重定位引用它、但到链接结束都没人定义** → 报错 (点名怎么补)。

    **未定义符号本身不再是错误**: 它可能由**另一个对象**提供 —— 这正是多目标文件 C 库的
    形状 (`a.o` 调 `b.o`)。判定推迟到"所有对象都摆好、符号表建好之后"。
    """

    def __init__(self, path: Path):
        self._load(path, path.name, path.read_bytes())

    @classmethod
    def from_bytes(cls, path: Path, name: str, raw: bytes) -> "ForeignObject":
        """从**内存里的字节**构造 —— 归档成员没有自己的文件 (`docs/173` 阶段 2)。"""
        self = cls.__new__(cls)
        try:
            self._load(path, name, raw)
        except Unsupported as e:
            # 解析失败时**符号表可能已经读出来了**（重定位那一趟才炸）。把这份半成品挂到
            # 异常上：归档那一侧靠它说清"这个成员定义了 f，但用不了：原因"（`#628`）。
            e.partial = self
            raise
        return self

    def _load(self, path: Path, name: str, raw: bytes) -> None:
        self.path = path
        self.name = name
        if raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
            raise Unsupported(f"{name}: 不是 ELF64 小端目标文件")
        if struct.unpack_from("<H", raw, 16)[0] != 1:      # e_type != ET_REL
            raise Unsupported(f"{name}: 不是可重定位目标文件 (ET_REL)")
        e_shoff, = struct.unpack_from("<Q", raw, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 58)
        secs = []
        for k in range(e_shnum):
            o = e_shoff + k * e_shentsize
            nameoff, typ, flags, addr, off, size, link, info, align, entsize = \
                struct.unpack_from("<IIQQQQIIQQ", raw, o)
            secs.append({"nameoff": nameoff, "type": typ, "flags": flags, "off": off,
                         "size": size, "link": link, "info": info, "entsize": entsize,
                         "align": align, "name": ""})
        shstr = secs[e_shstrndx]
        for s in secs:
            end = raw.index(b"\x00", shstr["off"] + s["nameoff"])
            s["name"] = raw[shstr["off"] + s["nameoff"]:end].decode("utf-8", "replace")
        # 代码节: `.text` **与 `.text.*`**。
        # **为什么必须认 `.text.*`**: clang/gcc 默认把所有函数塞进一个 `.text`, 但
        # rustc/LLVM 默认**按函数分节** (每个函数一个 `.text.<名字>`), 此时 `.text` 本身
        # 是**空的** (size 0) —— 只认 `.text` 会直接报"没有 .text" (实测 Rust 那条判据就是
        # 这么红的)。所以把所有代码节**按节表顺序拼成一块**, 并记下每节在拼接结果里的偏移,
        # 符号地址 = 该节偏移 + 符号的段内值 (16 对齐, 与函数对齐要求一致)。
        code: list[dict] = []
        buf = bytearray()
        text_align = 16
        for s in secs:
            if s["type"] == 1 and (s["name"] == ".text" or s["name"].startswith(".text.")):
                # `sh_addralign` 是节的一部分（`#612`）：`.text` 要求 32/64 时也得摆对，
                # 否则 AVX 指令取指/取常数会 #GP。原先这里硬写 16。
                a = max(16, s["align"] or 1)
                text_align = max(text_align, a)
                while len(buf) % a:
                    buf.append(0)
                s["at"] = len(buf)
                buf += raw[s["off"]:s["off"] + s["size"]]
                code.append(s)
        if not code:
            raise Unsupported(f"{name}: 没有代码节 (.text / .text.*)")
        self.text = bytes(buf)
        self.text_align = text_align
        self.sec_at = {k: s["at"] for k, s in enumerate(secs) if "at" in s}
        # **数据节**（`docs/219` S2 的前置，§8b 第 1 条）：`SHF_ALLOC` 的 `.rodata*` /
        # `.data*` / `.bss`。真实 C 库的**字符串常量、常量表、结构体**都在这里。
        #
        # **它卡住的不是"摆不摆得下"，是"读不读得进来"**：代码里那条
        # `lea rdi, [rip + .L.str]` 是一条**指向数据节的符号**的重定位，而我们原先只登记
        # 代码节里的符号 ⇒ 它会以"引用了未定义的符号 .L.str"的面目报出来（实测），
        # 一句"不支持的节"都看不到。所以这一步是**真实库的门槛**。
        #
        # 收 `SHF_ALLOC`(0x2) 且类型是 `SHT_PROGBITS`(1) / `SHT_NOBITS`(8)（以及后面
        # 那三种函数指针数组）的；`.text*` 已经在上面那一趟收走；元数据节
        # （`.eh_frame`/`.debug`/`.comment`/`.note`）**不收** —— 代码引用不到它们
        # （`_META_SEC` 同一条理由）。
        #
        # `.bss`(NOBITS) 这里**按零字节摆进 DATA**（不是单独一段）：小、且省掉一条"段内
        # 符号落在 bss"的路径。**代价**：带大 `.bss` 的库会把零字节写进文件 —— 这是本档
        # 明写的边界，不是疏忽。
        #
        # **TLS**（`#610`）单独摆：`SHF_TLS`(0x400) 的 `.tdata*` / `.tbss*` 不进普通数据段，
        # 它们要按 TLS 块布局（`TPOFF` 重定位算的是"相对线程指针"）。`.tbss` 同样按零字节摆。
        #
        # **构造函数表**（`#611`）：`.init_array`(SHT_INIT_ARRAY=14) / `.preinit_array`(16) /
        # `.ctors` 是**函数指针数组**，不是普通数据。它们要被摆进数据段（重定位才落得下），
        # 并由入口桩在 `_start` 之前逐个调用。只认 `.ctors` 是不够的：那是老工具的写法，
        # 现在 gcc/clang 默认发 `.init_array`，而它原先被当成"非代码节"硬拒。
        dbuf = bytearray()
        tbuf = bytearray()
        data_align = 16
        tls_align = 16
        ctors: list[tuple[int, int]] = []
        finis: list[tuple[int, int]] = []
        for s in secs:
            if s["name"] == ".text" or s["name"].startswith(".text."):
                continue
            if s["name"].startswith(_META_SEC) or s["size"] == 0:
                continue
            if not (s["flags"] & 0x2):                      # SHF_ALLOC
                continue
            if s["flags"] & 0x400:                          # SHF_TLS
                if s["type"] not in (1, 8):
                    continue
                a = max(1, s["align"] or 1)
                tls_align = max(tls_align, a)
                while len(tbuf) % a:
                    tbuf.append(0)
                s["tlsat"] = len(tbuf)
                tbuf += raw[s["off"]:s["off"] + s["size"]] if s["type"] == 1 else bytes(s["size"])
                continue
            is_ctor = (s["type"] in (14, 16) or s["name"] == ".ctors"
                       or s["name"].startswith(".ctors."))
            # `.fini_array`(SHT_FINI_ARRAY=15) / `.dtors` 是同一件事的另一头（`#611`）：
            # 也要摆进数据段、由入口桩在 `_start` 之后**逆序**调用。
            is_fini = (s["type"] == 15 or s["name"] == ".dtors"
                       or s["name"].startswith(".dtors."))
            if s["type"] not in (1, 8) and not is_ctor and not is_fini:
                continue
            # 与代码节同一条口径：至少 16（历史布局），要求更大就按更大的来。
            a = max(16, s["align"] or 1)
            data_align = max(data_align, a)
            while len(dbuf) % a:
                dbuf.append(0)
            s["dat"] = len(dbuf)
            # 14/15/16 是 `Elf64_Addr` 数组，与 PROGBITS 一样按原字节摆；8(NOBITS) 补零。
            dbuf += raw[s["off"]:s["off"] + s["size"]] if s["type"] in (1, 14, 15, 16) \
                else bytes(s["size"])
            if is_ctor:
                ctors.append((s["dat"], s["size"]))
            elif is_fini:
                finis.append((s["dat"], s["size"]))
        self.data = bytes(dbuf)
        self.data_align = data_align
        self.tls = bytes(tbuf)
        self.tls_align = tls_align
        self.ctors = ctors
        self.finis = finis
        self.dsec_at = {k: s["dat"] for k, s in enumerate(secs) if "dat" in s}
        # 符号: 定义在代码节里的符号进 `syms`（**含局部**，见 `#613`）; 数据节里的进
        # `dsyms`; 绝对符号进 `absyms`。未定义的只记名 —— 强引用进 `undefined`，
        # **弱引用进 `weak_undefined`**（`#617`：它不该是硬需求，解析不到就写 0）。
        syms: dict[str, int] = {}
        dsyms: dict[str, int] = {}      #: 数据节里的符号（**含局部**）
        tsyms: dict[str, int] = {}      #: TLS 块里的符号（**含局部**，`#610`）
        absyms: dict[str, int] = {}     #: `SHN_ABS` 定义的符号（地址就是 `st_value`）
        binds: dict[str, int] = {}      #: 定义符号的绑定（0 LOCL / 1 GLOB / 2 WEAK）
        undefined: list[str] = []
        weak_undefined: list[str] = []
        #: `SHT_SYMTAB_SHNDX`（18）: symtab 节号 -> (数据偏移, 条数)。`SHN_XINDEX` 的
        #: 真实节号在这张伴随表里（`#619`）。
        xindex: dict[int, tuple[int, int]] = {}
        for si, s in enumerate(secs):
            if s["type"] == 18:
                if s["link"] >= len(secs) or s["off"] + s["size"] > len(raw):
                    raise Unsupported(f"{name}: .symtab_shndx 损坏或越界")
                xindex[s["link"]] = (s["off"], s["size"] // 4)
        #: (symtab 节号, 符号下标) -> 名字 —— 重定位按**下标**引用符号, 靠它回查名字
        name_of: dict[tuple[int, int], str] = {}
        for si, s in enumerate(secs):
            if s["type"] != 2:                             # SHT_SYMTAB
                continue
            strt = secs[s["link"]]
            for k in range(s["size"] // 24):
                o = s["off"] + k * 24
                nameoff, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", raw, o)
                if shndx == 0xFFFF:                        # SHN_XINDEX
                    xi = xindex.get(si)
                    if xi is None or k >= xi[1]:
                        raise Unsupported(
                            f"{name}: 第 {k} 个符号用 SHN_XINDEX 却没有 .symtab_shndx")
                    shndx, = struct.unpack_from("<I", raw, xi[0] + k * 4)
                if nameoff == 0:
                    # **节符号**（`STT_SECTION`）：`st_name` 就是 0，名字靠 `shndx` 认。
                    # clang 对"指向 `.rodata` 里某个常量"的重定位就是引用它 —— 实测
                    # `static const char *const msgs[] = {"ab","cd"};` 那条报的是
                    # "重定位引用了下标记号外的符号 3"。给它一个**合成名**（只在本函数内用、
                    # 不进产物），地址 = 该节基址 + `st_value`。
                    # ⚠ 名字必须**按对象**唯一：两个对象各有 `.rodata` 时，只用节号的话
                    # 第二个会被"先来的为准"挡掉、指到**第一个对象的节**上（静默算错地址）。
                    synth = f"${name}$sec{shndx}"
                    if shndx < len(secs):
                        s0 = secs[shndx]
                        if "at" in s0:
                            name_of[(si, k)] = synth
                            syms[synth] = s0["at"] + value
                            binds[synth] = 0
                        elif "dat" in s0:
                            name_of[(si, k)] = synth
                            dsyms[synth] = s0["dat"] + value
                            binds[synth] = 0
                        elif "tlsat" in s0:
                            name_of[(si, k)] = synth
                            tsyms[synth] = s0["tlsat"] + value
                            binds[synth] = 0
                    continue
                end = raw.index(b"\x00", strt["off"] + nameoff)
                nm = raw[strt["off"] + nameoff:end].decode("utf-8", "replace")
                # **`st_info` 的高 4 位是绑定, 低 4 位是类型** —— `ELF64_ST_INFO(bind, type)`
                # 就是 `(bind << 4) | type`。原文这里是 `info & 0x0F`, 取到的是**类型**:
                # 收定义时靠"类型是 FUNC/OBJECT"误打误撞能对, 但一旦要判绑定 (比如"未定义
                # 的符号是不是全局的") 就整个失效 —— 实测它让归档的固定点选不出成员
                # (`c_sub` 的 info=0x10, `&0x0F` 得 0 被当成 LOCAL)。
                bind = info >> 4
                # ⚠ **局部符号（`bind == 0`）必须按对象改名**（2026-10-15）。理由与上面那条
                # 节符号**一模一样**，只是 S1f 当时只修了节符号：
                #   `.LC0` / `.L.str` / `my_version` 这种名字**每个成员里都有**，而下面那张
                #   标签表是**按名字**查的、而且"先来的为准" —— 不改名时，第二个成员的
                #   `.LC0` 会解析到**第一个成员的** `.LC0` 上：**静默指错地址**，不报任何错。
                #   实测（`FujoJTOP/loment#359`）：zlib 的 `deflate.o` 排在 `zutil.o` 之前时，
                #   `zlibVersion()` 返回的指针就落到 deflate.o 的常量上（`docs/219` §8b 那条
                #   记了很久的"某个成员的数据/重定位还有残留缺陷"）。改完两边都读回 `1.3.1`。
                # **只改局部**：跨对象的解析正是靠全局/弱符号，改了它们反而把 C 库拼不起来。
                # 名字是**内部的**（不进产物 —— 我们的产物没有符号表），所以两侧各取各的形
                # 也行，只要各自自洽；这里取 `$<对象名>$<原名>`，与节符号那条同一个形状。
                if bind == 0:
                    nm = f"${name}${nm}"
                name_of[(si, k)] = nm
                if shndx == 0:
                    # 强引用是硬需求；**弱引用不是**（`#617`）—— C 的语义是解析不到就绑 0，
                    # `if (opt) opt();` 正是为它写的。它也不能进端口门或归档需求集。
                    if bind == 1:                          # GLOBAL
                        undefined.append(nm)
                    elif bind == 2:                        # WEAK
                        weak_undefined.append(nm)
                # **shndx 可以是保留值** (`SHN_ABS`=0xfff1 / `SHN_COMMON`=0xfff2；
                # `SHN_XINDEX`=0xffff 已在上面换成真实节号)。以前这里只把它们排除在
                # `IndexError` 之外、没写语义分支，于是"自己的对象定义了它"被报成未定义
                # （`#619`）。
                elif shndx == 0xFFF1:                      # SHN_ABS: 值就是绝对地址
                    absyms[nm] = value
                    binds[nm] = bind
                elif shndx == 0xFFF2:                      # SHN_COMMON: 在这块 .bss 里划出来
                    al = max(value, 1)
                    while len(dbuf) % al:
                        dbuf.append(0)
                    dsyms[nm] = len(dbuf)
                    dbuf += bytes(size)
                    binds[nm] = bind
                elif shndx < len(secs):
                    tgt = secs[shndx]
                    if "at" in tgt and bind in (0, 1, 2):
                        # **局部也算定义**（`#613`）：`-ffunction-sections` 下 `helper` 是
                        # `STB_LOCAL`，名字上面已经按对象改过，登记它才有东西可解析。
                        # 自举镜像一直是这么做的；只收 GLOBAL/WEAK 会拒绝同一份对象。
                        syms[nm] = tgt["at"] + value
                        binds[nm] = bind
                    elif "dat" in tgt:
                        # **数据节里的符号一律收（含局部）**：`.L.str` 就是 `bind=0` 的
                        # `STT_OBJECT`（实测），只收 GLOBAL/WEAK 会把它漏掉，于是代码里那条
                        # `lea rdi, [rip + .L.str]` 找不到标签、以"未定义的符号"报出来。
                        dsyms[nm] = tgt["dat"] + value
                        binds[nm] = bind
                    elif "tlsat" in tgt:
                        # TLS 的 `st_value` 是**节内偏移**，不是地址（`#610`）。真正的
                        # 地址要等 TLS 块落到数据段里才知道（`compile_ll` 里加上块基址）。
                        tsyms[nm] = tgt["tlsat"] + value
                        binds[nm] = bind
        self.syms = syms
        self.dsyms = dsyms
        self.tsyms = tsyms
        self.absyms = absyms
        self.binds = binds
        self.undefined = sorted(set(undefined))
        self.weak_undefined = sorted(set(weak_undefined))
        #: SHN_COMMON 可能往 `dbuf` 追加过存储。
        self.data = bytes(dbuf)
        # 重定位: (目标节号, 节内偏移, 类型, 目标符号名, 加数)。**两条路** ——
        # 落在**代码节**上的进 `relocs`(写进代码缓冲), 落在**数据节**上的进 `drelocs`
        # (写进数据段)。两者都在"所有对象都摆好"之后才应用。
        self.relocs: list[tuple[int, int, int, str, int]] = []
        self.drelocs: list[tuple[int, int, int, str, int]] = []
        for s in secs:
            if s["type"] != 4 or s["size"] == 0:        # SHT_RELA
                continue
            tgt = s["info"]
            into_data = tgt < len(secs) and "dat" in secs[tgt]
            if tgt >= len(secs) or ("at" not in secs[tgt] and not into_data):
                where = secs[tgt]["name"] if tgt < len(secs) else f"#{tgt}"
                if tgt < len(secs) and secs[tgt]["name"].startswith(_META_SEC):
                    continue          # 展开/调试信息: 我们不摆, 丢掉无损 (见 _META_SEC)
                raise Unsupported(f"{name}: 重定位指向非代码节 ({where}) —— docs/173 §3")
            for k in range(s["size"] // 24):
                o = s["off"] + k * 24
                r_off, r_info, r_add = struct.unpack_from("<QQq", raw, o)
                ty, sym_i = r_info & 0xFFFFFFFF, r_info >> 32
                if ty not in SUPPORTED_RELOCS:
                    raise Unsupported(
                        f"{name}: 不认识的重定位类型 {ty} ({reloc_name(ty)}, docs/173 §3)")
                nm = name_of.get((s["link"], sym_i))
                if nm is None:
                    raise Unsupported(f"{name}: 重定位引用了下标记号外的符号 {sym_i}")
                (self.drelocs if into_data else self.relocs).append((tgt, r_off, ty, nm, r_add))

    @property
    def defined(self) -> set[str]:
        """这个对象**定义了**的名字 —— 归档挑选与端口门都看这一张（`#614`）。

        数据节里的定义与代码节里的同等重要：真实库的版本串/查找表就住在 `.rodata`。
        """
        return set(self.syms) | set(self.dsyms) | set(self.absyms)


class Archive:
    """一个 `ar` 归档 (`.a`): 静态库的**标准形状** (docs/173 阶段 2)。

    **只取需要的东西**: 经典固定点选择 —— 某成员**定义**了当前需要的符号就收它, 收完把它
    引用的未定义符号也加进需求, 重复到不再变化。**不整包收**: 整包收会把"某个成员用了
    printf"这种根本用不到的成员也拖进来, 于是**整个库被拒** —— 而真实库里几乎总有那么
    一两个成员带着无关的 libc 依赖。这个差别是"多目标文件 C 库"这条判据能过的原因。
    """

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        if raw[:8] != b"!<arch>\n":
            raise Unsupported(f"{path.name}: 不是 ar 归档 (缺 !<arch> 魔数)")
        members: list[tuple[str, bytes]] = []
        pos = 8
        while pos + 60 <= len(raw):
            hdr = raw[pos:pos + 60]
            if hdr[58:60] != b"`\n":                # 成员头以 "`\n" 收尾 (0x60 0x0A)
                raise Unsupported(f"{path.name}: 归档成员头损坏 (偏移 {pos})")
            name = hdr[0:16].decode("ascii", "replace").strip()
            size_s = hdr[48:58].decode("ascii", "replace").strip()
            try:
                size = int(size_s)
            except ValueError:
                raise Unsupported(f"{path.name}: 归档成员长度不是数字 ({size_s!r})")
            data = raw[pos + 60:pos + 60 + size]
            if len(data) != size:
                raise Unsupported(f"{path.name}: 归档成员被截断 ({name})")
            members.append((name, data))
            pos += 60 + size + (size & 1)          # 数据按偶数对齐, 奇数补一个填充字节
        if not members:
            raise Unsupported(f"{path.name}: 归档里没有成员")
        self.members = members

    def select(self, needed: set) -> "list[ForeignObject]":
        # 元数据成员 (`/` 符号索引、`//` 长名表、`/SYM64/`) 不是 ELF —— 按魔数滤掉最稳,
        # 因为它们的名字格式在各家 ar 实现里并不一致。
        pool = [(n, d) for n, d in self.members if d[:4] == b"\x7fELF"]
        got: list[ForeignObject] = []
        #: (成员名, 失败原因, 它定义的名字)。**旧的注释说"后面会报到"，但它从来没被报过**
        #: （`#628`）：成员被静默丢掉，用户只看到"未定义的符号"，指向**调用方**而不是
        #: 那个本可以提供它的成员。这里留住它，固定点走完再对一次账。
        skipped: list[tuple[str, str, set[str]]] = []
        changed = True
        while changed:
            changed = False
            rest = []
            for name, data in pool:
                try:
                    fo = ForeignObject.from_bytes(self.path, name, data)
                except Unsupported as e:
                    part = getattr(e, "partial", None)
                    defs: set[str] = set()
                    if part is not None:
                        # `$...` 是按对象改名的局部/节符号（见 `ForeignObject._load`），
                        # 永远不会是别人的需求名。
                        defs = {n for n in (*getattr(part, "syms", {}),
                                            *getattr(part, "dsyms", {}))
                                if not n.startswith("$")}
                    skipped.append((name, str(e), defs))
                    continue
                # **数据节里的定义也算数**（`#614`）：`set(fo.syms)` 只看得见代码节，
                # 于是 `.rodata` 里的版本串/查找表永远选不中成员。
                hit = fo.defined & needed
                if hit:
                    got.append(fo)
                    # 只有**强定义**才把名字闭合。弱定义留在需求集里，后面的强定义
                    # 仍然会被收进来 —— 收完两边在标签表里再由"强盖弱"定胜负（`#618`）。
                    needed -= {nm for nm in hit if fo.binds.get(nm, 1) != 2}
                    needed |= set(fo.undefined)
                    changed = True
                else:
                    rest.append((name, data))
            pool = rest
        if skipped:
            # 固定点结束后仍然"需要"（`needed` 只增不减，所以再减掉已收成员提供的）
            # 且由某个被丢掉的成员定义的名字，才是这条诊断的靶子。
            provided = {n for fo in got for n in (*fo.syms, *fo.dsyms)}
            unresolved = set(needed) - provided
            for name, why, defs in skipped:
                hit = sorted(defs & unresolved)
                if hit:
                    raise Unsupported(
                        f"{self.path.name}: archive member {name} defines "
                        f"{', '.join(hit)} but could not be used: {why} —— the "
                        f"symbol is in the archive; it was not absent")
        return got


class SharedObject:
    """一个**共享对象**（`.so`）—— `docs/183` §4 的 **V2**，`docs/173` 阶段 3。

    与 `ForeignObject` 是两件事，别混：

    * `.o` 是**可重定位目标文件**（`ET_REL`）：我们把它**读进来**、摆进产物、应用重定位；
    * `.so` 是**共享对象**（`ET_DYN`）：我们**只读它的导出表**，产物里**一个字节也不摆**
      —— 跑起来的时候由系统的 loader 把 `.so` 映射进来，符号在**它**那边解析。

    所以"能跑通"这件事在这里证明不了什么：静态链也能跑通。判据另外量**产物里有没有
    `DT_NEEDED`、有没有把库的代码搬进来**（`loment_ffi_test` 那条 V2 判据）。

    **只按导出表找名字**：`.dynsym` 里 `SHN_UNDEF` 的是它自己的依赖（如 `libc.so.6`），
    不是它能给人的东西 —— 拿它去满足我们的引用就是**错绑**。
    """

    #: `DT_*` 的号（只取这里用得上的）。
    DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_STRSZ, DT_SYMENT, DT_SONAME = 1, 4, 5, 6, 10, 11, 14
    #: `EM_X86_64`。`EI_CLASS`/`EI_DATA` 说"64 位小端"，每一个交叉目标都满足它 ——
    #: 只有 `e_machine` 分得开 x86-64 与别的架构（`#629`）。
    EM_X86_64 = 62

    def __init__(self, path: Path):
        raw = path.read_bytes()
        self.path, self.name, self.raw = path, path.name, raw
        n = len(raw)

        def need(off: int, size: int, what: str) -> None:
            """**每一次读之前都问一句：这一段在文件里吗**（`#592`）。

            这是 `.so` 那一侧的 `#167`（`.o` / `.a` 的同一课）。一个截断的、或者被改坏
            一个字段的共享对象，不该让用户拿到 `struct.error` / `ValueError` / `KeyError`
            —— 那是**我们内部炸了**，不是"这个库我读不了"。所以统一成 `Unsupported`，
            消息里说清要的区间与文件长度。
            """
            if off < 0 or size < 0 or off + size > n:
                raise Unsupported(
                    f"{path.name}: 共享对象是截断或损坏的 —— {what} 要文件里的 "
                    f"[{off}, {off + size})，而文件只有 {n} 字节")

        if n < 64:
            raise Unsupported(f"{path.name}: 不是一个 ELF64 共享对象（只有 {n} 字节）")
        if raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
            raise Unsupported(f"{path.name}: 不是 ELF64 小端共享对象")
        if struct.unpack_from("<H", raw, 16)[0] != 3:            # e_type != ET_DYN
            raise Unsupported(f"{path.name}: 不是共享对象 (ET_DYN)")
        e_machine, = struct.unpack_from("<H", raw, 18)
        if e_machine != self.EM_X86_64:
            # **`e_machine` 是唯一分得开架构的字段**（`#629`）。放过去只有两种结局：
            # loader 拒掉它，或者机器上恰好装了一份**同名**的原生库 —— 于是产物悄悄
            # 绑到了另一份库上，而 V2 唯一的保证（"导出表证明名字存在"）就没了。
            raise Unsupported(
                f"{path.name}: e_machine={e_machine} is not EM_X86_64 "
                f"({self.EM_X86_64}) —— this shared object is built for another "
                f"architecture; the loader would reject it or bind a different library")
        e_phoff, = struct.unpack_from("<Q", raw, 32)
        e_phentsize, e_phnum = struct.unpack_from("<HH", raw, 54)
        e_shoff, = struct.unpack_from("<Q", raw, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 58)
        # (vaddr, 文件内偏移, p_filesz, p_memsz, p_flags)。`voff` 用前三个把**文件**
        # 读出来；导出扫描用 vaddr + **p_memsz** 判"这个 st_value 在映像里吗" ——
        # `.bss` 里的数据符号只在 memsz 里（实测 libc 的 `rpc_createerr` 就在那儿）。
        loads: list[tuple[int, int, int, int, int]] = []
        dyn: tuple[int, int] | None = None
        if e_phnum and e_phentsize < 56:
            raise Unsupported(f"{path.name}: 程序头条目只有 {e_phentsize} 字节（要 ≥ 56）")
        need(e_phoff, e_phnum * e_phentsize, "程序头表")
        for k in range(e_phnum):
            o = e_phoff + k * e_phentsize
            p_type, p_flags, p_off, p_vaddr, _pa, p_filesz, p_memsz, _al = \
                struct.unpack_from("<IIQQQQQQ", raw, o)
            if p_type == 1:
                loads.append((p_vaddr, p_off, p_filesz, p_memsz, p_flags))
            elif p_type == 2:
                dyn = (p_off, p_filesz)
        if dyn is None:
            raise Unsupported(f"{path.name}: 共享对象没有 PT_DYNAMIC")

        def voff(vaddr: int) -> int:
            for v, off, sz, _mem, _fl in loads:
                if v <= vaddr < v + sz:                       # 文件里的部分：p_filesz
                    return off + (vaddr - v)
            raise Unsupported(
                f"{path.name}: 动态表里的地址 0x{vaddr:x} 落在任何 PT_LOAD 之外")

        dt: dict[int, int] = {}
        need(dyn[0], dyn[1], "PT_DYNAMIC")
        o = dyn[0]
        while o + 16 <= dyn[0] + dyn[1]:
            tag, val = struct.unpack_from("<QQ", raw, o)
            o += 16
            if tag == 0:
                break
            dt.setdefault(tag, val)
        if self.DT_STRTAB not in dt or self.DT_STRSZ not in dt:
            raise Unsupported(f"{path.name}: 动态表里没有 DT_STRTAB / DT_STRSZ")
        strtab_off = voff(dt[self.DT_STRTAB])
        strsz = dt[self.DT_STRSZ]
        need(strtab_off, strsz, "字符串表 (DT_STRTAB)")
        strtab = raw[strtab_off:strtab_off + strsz]

        def dstr(off: int) -> str:
            # 偏移越界 / 没有结尾 NUL 都是一个改坏的字段，`strtab.index` 会抛 ValueError。
            if off < 0 or off >= len(strtab):
                raise Unsupported(
                    f"{path.name}: 字符串表里的偏移 {off} 越界（表长 {len(strtab)}）")
            end = strtab.find(b"\x00", off)
            if end < 0:
                raise Unsupported(f"{path.name}: 字符串表里偏移 {off} 之后没有结尾的 NUL")
            return strtab[off:end].decode("utf-8", "replace")

        # 没有 SONAME 的共享对象，`DT_NEEDED` 得写**它本来的文件名**（loader 按那个找）。
        # **`DT_SONAME` 存在但为空是一个独立的坏形态**（`#641`）：原先只分"有没有这个
        # tag"，于是 `DT_NEEDED=""` 被写进产物 —— loader 匹配不上任何库，构建却报成功。
        if self.DT_SONAME in dt:
            soname = dstr(dt[self.DT_SONAME])
            if not soname:
                raise Unsupported(
                    f"{path.name}: DT_SONAME is present but empty —— DT_NEEDED would be "
                    f"an empty string, which the loader cannot match to any library; "
                    f"rebuild it with -Wl,-soname,libfoo.so.1")
            self.soname = soname
        else:
            # 没有 SONAME：`DT_NEEDED` 记**命令行给的那个路径**（GNU ld 同一条，`#595`）。
            # 只记 basename 的话，`--link /tmp/mylibs/libadd.so` 的产物会去找
            # `libadd.so`，而那个名字不在 loader 的任何搜索路径里 —— 链接成功、运行期
            # `cannot open shared object file`，链路里没有任何一步说出这件事。
            self.soname = str(path)
        if e_shnum == 0:
            # 条数能从 `DT_HASH` 拿，但**版本表**在节头里；被 strip 掉的 `.so` 这一版还不读。
            raise Unsupported(
                f"{path.name}: 共享对象被 strip 掉了节头 —— 这一版只读带节头的 `.so`")

        secs = []
        if e_shentsize < 64:
            raise Unsupported(f"{path.name}: 节头条目只有 {e_shentsize} 字节（要 ≥ 64）")
        need(e_shoff, e_shnum * e_shentsize, "节头表")
        for k in range(e_shnum):
            so = e_shoff + k * e_shentsize
            nameoff, typ, flags, _addr, off, size, link, info, _a2, entsize = \
                struct.unpack_from("<IIQQQQIIQQ", raw, so)
            secs.append({"nameoff": nameoff, "type": typ, "flags": flags, "off": off,
                         "size": size, "link": link, "info": info, "entsize": entsize,
                         "name": ""})
        if e_shstrndx >= len(secs):
            raise Unsupported(
                f"{path.name}: e_shstrndx={e_shstrndx} 越界（只有 {len(secs)} 个节头）")
        shstr = secs[e_shstrndx]
        need(shstr["off"], shstr["size"], "节名字符串表 (.shstrtab)")
        sbase, send = shstr["off"], shstr["off"] + shstr["size"]
        for s in secs:
            at = sbase + s["nameoff"]
            if at >= send:
                raise Unsupported(
                    f"{path.name}: 节名字偏移 {s['nameoff']} 越出 .shstrtab")
            end = raw.find(b"\x00", at, send)
            if end < 0:
                raise Unsupported(f"{path.name}: .shstrtab 里这个节名没有结尾的 NUL")
            s["name"] = raw[at:end].decode("utf-8", "replace")
        byname = {s["name"]: s for s in secs}

        dynsym = byname.get(".dynsym")
        if dynsym is None or ".dynstr" not in byname:
            raise Unsupported(f"{path.name}: 共享对象没有 .dynsym/.dynstr")
        need(dynsym["off"], dynsym["size"], ".dynsym")
        need(byname[".dynstr"]["off"], byname[".dynstr"]["size"], ".dynstr")
        dstrtab = raw[byname[".dynstr"]["off"]:
                      byname[".dynstr"]["off"] + byname[".dynstr"]["size"]]

        #: 版本号 -> 名字（`.gnu.version_d` 的 `vd_aux` 链）—— 只为了**把话说清楚**。
        vnames: dict[int, str] = {}
        verd = byname.get(".gnu.version_d")
        if verd is not None:
            need(verd["off"], verd["size"], ".gnu.version_d")
            vo = verd["off"]
            # `vd_next` 是文件里的一个 u32：**成环只要两处 4 字节编辑**（`#603`）。
            # 所以这条链有两个界 —— 走不出这一节，以及走不出一节能容纳的条数。
            # 没有它们，链接器在 100% CPU 上转圈，用户连一句话都拿不到。
            vend = verd["off"] + verd["size"]
            seen = 0
            # `Elf64_Verdef` 的字段序是 version(0) flags(2) ndx(4) **cnt(6)** hash(8)
            # aux(12) next(16)。`cnt` 在 6，不在 2 —— 第一版读到的是 `flags`，于是
            # 每个版本的 `vd_next` 链只走了第一步，`vnames` 里永远是空的，
            # 报错信息里那个版本名就一直是 `?`。
            while True:
                need(vo, 20, "版本定义 (.gnu.version_d)")
                _ver, _flags, vidx, _cnt, _hash, aux, nxt = \
                    struct.unpack_from("<HHHHIII", raw, vo)
                need(vo + aux, 4, "版本定义的辅助链 (.gnu.version_d)")
                adx, = struct.unpack_from("<I", raw, vo + aux)
                vnames[vidx & 0x7FFF] = dstr(adx)
                if nxt == 0:
                    break
                vo += nxt
                seen += 1
                if vo < verd["off"] or vo + 20 > vend or seen > verd["size"] // 20:
                    raise Unsupported(
                        f"{path.name}: .gnu.version_d 的链成环或越界"
                        f"（第 {seen} 步走到 {vo}，这一节是 "
                        f"[{verd['off']}, {vend})）")
        #: `.gnu.version` 与 `.dynsym` 一一对应：bit 15 是**隐藏位**（`@@` 默认版本没有它，
        #: `@` 非默认版本有）。未加版本的引用只能绑到**没有隐藏位**的那一个。
        versym: list[int] = []
        vs = byname.get(".gnu.version")
        if vs is not None:
            need(vs["off"], vs["size"] // 2 * 2, ".gnu.version")
            versym = list(struct.unpack_from("<" + "H" * (vs["size"] // 2), raw, vs["off"]))

        #: 导出表：名字 -> (kind, 是不是**默认**版本)。
        self.exports: dict[str, tuple[str, bool]] = {}
        self.versions: dict[str, str] = {}
        #: 名字 -> `hidden` / `internal` —— 在 `.dynsym` 里，但 loader **不会**给外人绑定。
        #: 单独记一份，只为了 `resolve()` 被问到时能说出原因（`#642`）。
        self.hidden: dict[str, str] = {}
        # ⚠ 别叫 `n`：上面那个 `n` 是**文件长度**，`need()` 闭包读的正是它。
        # 这里覆盖掉的话，越界检查会拿"符号条数"去比文件长度 —— 检查还在，但比错了东西。
        nsym = dynsym["size"] // 24
        for k in range(1, nsym):
            so2 = dynsym["off"] + k * 24
            st_name, st_info, st_other, st_shndx, st_value, _size = \
                struct.unpack_from("<IBBHQQ", raw, so2)
            if st_shndx == 0 or st_name == 0:                   # 它自己的依赖，不是它的导出
                continue
            bind, typ = st_info >> 4, st_info & 0x0F
            # GLOBAL/WEAK 的 NOTYPE(0) / OBJECT(1) / FUNC(2) / IFUNC(10)。
            # **IFUNC 必须算函数导出**：glibc 的 `strlen` / `memcpy` 就是 IFUNC
            # （`STT_GNU_IFUNC`），loader 的 `JUMP_SLOT` 解析会调它的 resolver 拿到实现
            # —— 对它来说与普通函数没差别。
            # **NOTYPE 也算函数导出**（`#591`）：没有 `.type` 指令的汇编函数
            # （`gcc -shared` 出来的 `STT_NOTYPE`）就是一个正常的可调用导出，`dlsym`
            # 和 loader 都不看这个类型。`STT_SECTION`(3) / `STT_FILE`(4) / `STT_TLS`(6)
            # 仍然拒 —— 它们不是可调用的名字。
            if bind not in (1, 2) or typ not in (0, 1, 2, 10):
                continue
            end = dstrtab.find(b"\x00", st_name)
            if st_name >= len(dstrtab) or end < 0:
                raise Unsupported(
                    f"{path.name}: .dynsym 第 {k} 项的 st_name={st_name} 越出 .dynstr "
                    f"（表长 {len(dstrtab)}）")
            nm = dstrtab[st_name:end].decode("utf-8", "replace")
            # **可见性**（`#642`）：`STV_HIDDEN` / `STV_INTERNAL` 的符号也在 `.dynsym` 里，
            # 但 loader 只允许本对象内部绑定。把它们当成导出，构建会成功、加载时却
            # `undefined symbol`。`STV_PROTECTED`(3) 仍然可以跨对象绑定，照收。
            vis = st_other & 0x3
            if vis in (1, 2):
                self.hidden[nm] = "hidden" if vis == 2 else "internal"
                continue
            # **st_value 必须落在映像里**（`#643`）：导出表证明"这个名字存在"，但没证明
            # "它指向一段被映射的内存" —— 越界的值绑上去，第一条 call 就跳到未映射地址。
            # SHN_ABS 的 **OBJECT** 是版本标记（`ZLIB_1.2.2` / `GLIBC_2.10`，value=0），
            # 合法且不在任何段里；ABS 的 FUNC 仍然要过下面的地址检查。
            if not (st_shndx == 0xFFF1 and typ == 1):
                if st_shndx >= len(secs):
                    raise Unsupported(
                        f"{path.name}: export {nm} has st_shndx={st_shndx}, which is "
                        f"not a section of this object")
                sec = secs[st_shndx]
                if not (sec["flags"] & 0x2):                  # SHF_ALLOC
                    raise Unsupported(
                        f"{path.name}: export {nm} is defined in section "
                        f"{sec['name'] or st_shndx}, which is not allocated at run time")
                if not any(v <= st_value < v + mem
                           for v, _off, _fs, mem, _fl in loads):
                    raise Unsupported(
                        f"{path.name}: export {nm} has st_value=0x{st_value:x}, which is "
                        f"outside every PT_LOAD —— calling it would jump to an unmapped "
                        f"address")
                if typ == 2 and not any(v <= st_value < v + mem and (fl & 0x1)
                                        for v, _off, _fs, mem, fl in loads):
                    raise Unsupported(
                        f"{path.name}: function export {nm} has st_value="
                        f"0x{st_value:x} in a non-executable segment —— calling it "
                        f"would fault")
            hidden = bool(versym[k] & 0x8000) if k < len(versym) else False
            # 同名可能有多条（不同版本）：**默认版本优先**，非默认的不覆盖它。
            if nm not in self.exports or (self.exports[nm][1] is False and not hidden):
                self.exports[nm] = ("func" if typ in (0, 2, 10) else "object", not hidden)
            if k < len(versym) and (versym[k] & 0x7FFF) > 1:
                self.versions.setdefault(nm, vnames.get(versym[k] & 0x7FFF, "?"))
        if not self.exports:
            raise Unsupported(f"{self.soname}: 没有可用的导出（GLOBAL/WEAK 的 FUNC/OBJECT）")

    def resolve(self, name: str) -> str:
        """这个名字能不能绑到一份**可用的**导出上；绑不上就**点名拒**（不留给运行时崩）。

        三种"绑不上"与 `docs/183` §4 V2 要的三条失败模式一一对应：库里没有这个名字、
        只有**非默认版本**（未加版本的引用绑不上它，loader 会把它当未定义）、以及**数据**
        符号（数据导入要 GOT 间接，而 Loment 的代码直接取址 —— 这一版只做函数导入）。
        """
        e = self.exports.get(name)
        if e is None:
            if name in self.hidden:
                raise Unsupported(
                    f"{self.soname}: {name} is in .dynsym but has "
                    f"{self.hidden[name]} visibility —— the loader does not bind it "
                    f"from outside the object, so this import would fail at load time")
            raise Unsupported(
                f"{self.soname}: 没有导出 {name} —— 动态链接只在导出表里找名字，"
                f"库里没有这个名字，产物一定加载不起来")
        kind, is_default = e
        if not is_default:
            raise Unsupported(
                f"{self.soname}: {name} 只以**非默认版本**导出（{self.versions.get(name, '?')}）"
                f" —— 未加版本的引用绑不上它；本链接器还不发版本化引用（`docs/183` §4 V2）")
        if kind == "object":
            raise Unsupported(
                f"{self.soname}: {name} 是**数据**符号 —— 数据导入要 GOT 间接，而 Loment 的"
                f"代码直接取址（`getelementptr`/`load`），这一版只做函数导入")
        return kind


def load_foreign(path: Path):
    """按**内容**分流 (不看扩展名): `!<arch>\\n` 是归档, `\\x7fELF` 是目标文件。

    看内容而不是后缀: `.a` / `.o` / `.lib` / 无后缀在各家工具链里并不统一, 而两种格式的
    魔数都极短且不可能互串。
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        # **命名的错误，不是崩**（`#360` 的失败模式之一）：共享对象不在就是"打不开"，
        # 与自举侧 `read_link_file` 的那条同形。
        raise Unsupported(f"打不开外部目标文件：{path}（{e.strerror or e}）") from None
    if raw[:8] == b"!<arch>\n":
        return Archive(path)
    if raw[:4] == b"\x7fELF" and len(raw) >= 20 and struct.unpack_from("<H", raw, 16)[0] == 3:
        return SharedObject(path)                  # `.so`：V2 的那条腿（docs/173 阶段 3）
    # `#637`：Debian/Ubuntu 的 `libz.so` 是 **GNU ld 链接脚本**（ASCII 的
    # `GROUP ( /lib/.../libz.so.1 ... )`），不是 ELF。原先它掉进 `ForeignObject`，
    # 报"不是 ELF64 小端目标文件"—— 把共享对象叫成"目标文件"，也没说该链哪个。
    head = raw[:4096].lstrip()
    if head.startswith((b"GROUP", b"INPUT", b"/* GNU ld script")):
        txt = raw[:4096].decode("utf-8", "replace")
        m = re.search(r"^\s*(?:GROUP|INPUT)\s*\(\s*([^\s)]+)", txt, re.M)
        target = m.group(1) if m else ""
        hint = f"；它指向 {target}，链那个文件" if target else "；链它指向的 .so 本体"
        raise Unsupported(f"{path.name}: 这是 GNU ld 链接脚本{hint}（不是 ELF 共享对象）")
    return ForeignObject(path)


#: System V AMD64 整数实参寄存器 (我们**自己的**约定是实参走栈, 这一套只给 extern 调用点)。
C_ARG_REGS = (RDI, RSI, RDX, RCX, R8, R9)

#: **链接器今天真正发出的收编面**（`docs/219` §6 第 1 条）。`Emitter.emit_conscript`
#: 发的那几个名字 —— 与 `loment/tools/lomelf.lomt` 的 `emit_conscript` 同序同字节。
#:
#: ⚠ 它是 `tools/loment_ports.py` 的 `PROVIDE` 的**子集**，而这件事有一条判据钉着
#: （`loment_ports_test::test_the_linkers_conscript_surface_is_inside_the_rulers_provide_set`）：
#: 尺子说"可闭合"而链接器发不出来，那条产物就会在链接期报"未定义的符号"，
#: **与表上的话正好相反**。反过来不要求 —— 表里还有一批**尚未实现**的
#: （`calloc` / `realloc` / `strlen` / `__udivdi3` …），那是 S1d/S1e 的活。
#:
#: ⚠ `malloc` / `free` 多两条条件：**堆在才发** —— 它们是一层"换成 C ABI"的薄壳，
#: 底下调的是 `@__loment_alloc` / `@__loment_free`（`docs/219` §2.1）。没有堆就没有
#: "我们的堆"可指，发了会悬着一个未定义的调用。判据是**我们这份 IR 里有没有那两条定义**
#: （`funcs` 的名字），不是文本里出现过那个名字。**第二条**（`#631`）：IR 自己
#: `declare` 了 `@malloc`/`@free` 时不发 —— 宿主版分配器就是向 libc 的 `malloc`
#: 要 arena 的，包壳会让那条导入指回壳自身（无限递归）。
CONSCRIPT = ("memcpy", "memset", "memcmp", "__stack_chk_fail", "malloc", "free")

#: 产物带外部对象时给它的一小块 **TLS**（`docs/219` S2）。要 ≥ 0x30 字节 ——
#: `-fstack-protector` 的库读 **`fs:0x28`** 取金丝雀；留大一点是给以后 `errno` 之类的槽位。
_TLS_SIZE = 256


def hot_metadata(funcs: list[dict], header: dict | None = None,
                 base: int | None = None,
                 relocs: list[tuple[int, int]] | None = None) -> str:
    """`.lomhot` 的文本形（`#1149` / `#1150`）。

    顺序是**确定的**：函数按 IR 顺序，调用点按发射顺序。调用点偏移是**函数相对**的
    （从函数入口算起），所以换一个槽地址、换一次链接都不改这些数。`entry` 是函数入口
    相对文本段起点的偏移（装载器用它算实际地址）；`tramp` 只有热更新档才有。
    """
    out = ["lomhot 1"]
    if header:
        out.append(f"fixed gate={header['gate']} stride={header['stride']}"
                   f" slots={header['slots']} digest={header['gate_digest']}")
    for fn in funcs:
        row = (f"func {fn['name']} entry={fn['entry']}"
               f" frame={fn['frame']} layout={fn['layout']}")
        if "tramp" in fn:
            row += f" tramp={fn['tramp']}"
        out.append(row)
        for cid, off in fn["calls"]:
            out.append(f"call {cid} at={off}")
    # 绝对地址的重定位表（`#1151`）：装载器把第二份镜像摆到别的槽时，按 `新基址 - base`
    # 逐条改这些 8 字节。PC 相对的那些（call/jmp/jcc）**不在表里** —— 整体平移时不改。
    if base is not None:
        out.append(f"base {base}")
        for off, tgt in relocs or ():
            out.append(f"reloc {off} {tgt}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 原生段错误报告（`docs/367` stage 1）
#
# 开关打开时，链接器往产物里注入一台很小的运行期：
#   * 入口换成 `__lom_entry`：装备栈、给 5 个致命信号装 handler（SA_SIGINFO|
#     SA_ONSTACK|SA_RESTORER），再调用用户的 `_start` / `main`；
#   * handler 是普通 IR 函数（零形参），它从一个**机器码 shim** 填好的三个全局里读现场
#     —— 内核按 SysV 传 rdi/rsi/rdx，而本链接器的内部调用是**栈机**，两者不能直接接；
#   * 报告按 `lomenterr` 的形状写 stderr，然后恢复默认处置并用 tgkill 重发信号 ——
#     进程仍然**死于信号**（shell 的 `Segmentation fault` 与退出码 139 不变）。
#
# **必须带 SA_RESTORER**：原型实测，只装 handler（flags=0）时 `rt_sigaction` 返回 0，
# 但信号来了没人接。glibc 的 `sigaction()` 会替你补上，裸 `rt_sigaction` 不会。

_FAULT_SIGNALS = (11, 7, 4, 8, 6)
_FAULT_FLAGS = 0x04000000 | 0x08000000 | 0x00000004   # SA_RESTORER|SA_ONSTACK|SA_SIGINFO
_FAULT_ALT = 32768

_SYS3 = ('call i64 asm sideeffect "syscall", '
         '"={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"')
_SYS6 = ('call i64 asm sideeffect "syscall", '
         '"={ax},{ax},{di},{si},{dx},{r10},{r8},{r9},~{cx},~{r11},~{memory}"')


def _cstr(text: str) -> str:
    """IR 的 `c"..."`：每个字节一个 `\\XX`（盘上的换行/引号也就都不需要转义）。"""
    return 'c"' + "".join("\\%02X" % b for b in text.encode("utf-8")) + '"'


def _site_kind_width(text: str) -> tuple[int, int] | None:
    """一条指令是不是访存站点；是就给出 (kind, width)。

    kind：0 load / 1 store / 2 atomicrmw。`getelementptr` **不算** —— 它不碰内存。
    """
    try:
        if text.startswith("load "):
            ty, _ = parse_type(text[len("load "):])
            return (0, size_of(ty))
        if text.startswith("store "):
            ty, _ = parse_type(text[len("store "):])
            return (1, size_of(ty))
        if text.startswith("atomicrmw "):
            m = re.match(r"atomicrmw \w+ ptr \S+,\s*(\w+)", text)
            if m:
                return (2, size_of(m.group(1)))
    except (ElfError, Unsupported):
        return None
    return None


def _fault_plan(funcs) -> dict:
    """在注入运行期**之前**把名字表/编号定下来（用户函数是 0..n-1，运行期排在后面）。"""
    fn_idx = {f.name: i for i, f in enumerate(funcs)}
    blk_names: list[str] = []
    blk_idx: dict[tuple[str, int], int] = {}
    for fi, f in enumerate(funcs):
        for bi, b in enumerate(f.blocks):
            blk_idx[(f.name, bi)] = len(blk_names)
            blk_names.append(b.label)
    strs = bytearray()
    off: dict[str, int] = {}

    def intern(s: str) -> int:
        if s not in off:
            off[s] = len(strs)
            strs.extend(s.encode("utf-8") + b"\0")
        return off[s]

    fn_off = [intern(f.name) for f in funcs]
    blk_off = [intern(b) for b in blk_names]
    nsites = sum(1 for f in funcs for b in f.blocks for ins in b.instrs
                 if _site_kind_width(ins.text) is not None)
    return {
        "fn_idx": fn_idx, "blk_idx": blk_idx, "blk_names": blk_names,
        "fn_off": fn_off, "blk_off": blk_off,
        "strs": bytes(strs) or b"\0",
        "nsites": nsites, "nfns": len(funcs), "nblks": len(blk_names),
        "sites": [],                      # 发射期填：(addr, fn_idx, blk_idx, ins_idx, kind, width)
    }


def _fault_table_globals_ir(plan: dict) -> str:
    """四张表的声明。**IR 里写成 `zeroinitializer`**，放进 `.data` 是链接器的事。

    原先这里写一个长度等于表大小的 `c"\\EE\\00…"` 标记，只为让布局判它"有内容"。
    那串文本是表大小的三倍（每字节三个字符），site 一多就把输入缓冲吃掉 ——
    而它唯一的作用是"别进 bss"。现在两边各做一次等价的动作：
    参考实现给 `g.data` 一个标记字节（见 `compile_ll`）；自举镜像在它的
    `one_global` 里给 `gb[doff]` 一个标记字节。产物字节不变（表内容发射后整片回填）。
    """
    return "\n".join([
        f'@__lom_sites = global [{4 + 24 * plan["nsites"]} x i8] zeroinitializer',
        f'@__lom_fns = global [{4 + 24 * plan["nfns"]} x i8] zeroinitializer',
        f'@__lom_blks = global [{4 + 8 * plan["nblks"]} x i8] zeroinitializer',
        f'@__lom_strs = global [{len(plan["strs"])} x i8] zeroinitializer',
    ])


def _fault_patch_tables(em, plan: dict, data: bytearray, text_end: int) -> None:
    """把站点/函数/块/名字四张表写进 `.data`（`em` 已经发完所有函数）。"""
    def put(name: str, blob: bytes) -> None:
        g = em.globals[name]
        off = g.addr - DATA_VADDR
        assert len(blob) == size_of(g.ty), (name, len(blob), size_of(g.ty))
        data[off:off + len(blob)] = blob

    sites = sorted(plan["sites"])
    blob = bytearray(struct.pack("<I", len(sites)))
    for addr, fi, bi, ii, _kind, _width, ln in sites:
        # 第 5 个 u32 是**展开后的机器码长度**：报告只在 `[addr, addr+len)` 里认站点。
        # （kind/width 原先是另外两个 u16，报告从没用过；长度换掉了它们，条目仍是 24 B。）
        blob += struct.pack("<QIIII", addr, fi, bi, ii, ln)
    put("@__lom_sites", bytes(blob))

    # 函数区间：只收**用户函数**（表的下标就是它们的编号）。终点 = 下一个用户函数的
    # 起点；最后一个用户函数的终点是**第一个运行期函数**的起点（别把运行期算进用户函数）。
    funcs_order = [f for f in em.fault["funcs"] if f.name in plan["fn_idx"]]
    starts = [em.asm.labels[f.name] for f in funcs_order]
    tail = next((em.asm.labels[f.name] for f in em.fault["funcs"]
                 if f.name not in plan["fn_idx"]), text_end)
    blob = bytearray(struct.pack("<I", len(funcs_order)))
    for i, f in enumerate(funcs_order):
        end = starts[i + 1] if i + 1 < len(starts) else tail
        fi = plan["fn_idx"].get(f.name)
        if fi is None:
            # 运行期函数不进名字表 —— 给一个空名字，PC 落进去时打印空。
            blob += struct.pack("<QQII", starts[i], end, 0, 0)
        else:
            blob += struct.pack("<QQII", starts[i], end, plan["fn_off"][fi],
                                len(f.name))
    put("@__lom_fns", bytes(blob))

    blob = bytearray(struct.pack("<I", plan["nblks"]))
    # 块名长度从名字池里取：`blk_off` 指向的串以 NUL 结尾。
    for off, label in zip(plan["blk_off"], plan["blk_names"]):
        blob += struct.pack("<II", off, len(label.encode("utf-8")))
    put("@__lom_blks", bytes(blob))
    put("@__lom_strs", plan["strs"].ljust(len(plan["strs"]), b"\0"))





#: 五个信号的人类名字/码/标题（顺序就是 `__lom_handler` 里的分派顺序）。
_FAULT_TITLES = (
    (11, "SIGSEGV", "SEGV", "段错误（segmentation fault）"),
    (7, "SIGBUS", "BUS", "总线错误（bus error）"),
    (4, "SIGILL", "ILL", "非法指令（illegal instruction）"),
    (8, "SIGFPE", "FPE", "算术异常（floating-point exception）"),
    (6, "SIGABRT", "ABRT", "进程自己 abort 了（abort）"),
)


def _fault_messages() -> list[tuple[str, str]]:
    """（全局名，文本）。`__lom_handler` 按名字引用。"""
    out = [
        ("__lom_m_sig", "   | 信号："),
        ("__lom_m_nl", "\n"),
        ("__lom_m_addr", "   | 地址："),
        ("__lom_m_wr", "（写）\n"),
        ("__lom_m_rd", "（读）\n"),
        ("__lom_m_pc", "   | PC："),
        ("__lom_m_rsp", "；RSP："),
        ("__lom_m_arrow", "  --> 原生产物:"),
        ("__lom_m_so", "   | 判据：故障地址贴着 RSP（相差 < 64 KiB）—— 像是栈溢出\n"),
        ("__lom_m_fact", "   |\n   = 事实：原生档在执行上述 PC 时收到该信号。它没有对象表，"
                         "所以不给对象现场；站点由产物里的表解析。\n"),
        ("__lom_m_tip", "   = 建议：要逐站点的对象诊断（对象表 / 对象代 / 现场），"
                        "用 LVM 档运行（--substrate sw_cap，见 docs/366）；"
                        "本报告由产物自己发出（docs/367 stage 1）。\n"),
        ("__lom_ms1", "   | 站点："),
        ("__lom_ms2", " / "),
        ("__lom_ms3", " / 第 "),
        ("__lom_ms4", " 条（距该指令 "),
        ("__lom_ms5", "   | 站点：（PC 不在任何访存站点上；也不在任何用户函数里）\n"),
        ("__lom_ms5a", "   | 站点：（PC 不在任何访存站点上；所在函数："),
        ("__lom_ms5b", "）\n"),
        ("__lom_ms6", "）\n"),
    ]
    for sig, name, tag, title in _FAULT_TITLES:
        out += [
            (f"__lom_hn{sig}", f"loment: {name}（信号 "),
            (f"__lom_hd{sig}", "）\n"),
            (f"__lom_he{sig}", f"error[{tag}]: {title}\n"),
        ]
    out += [("__lom_hgen", "loment: 信号 "),
            ("__lom_hgen2", "）\n"),
            ("__lom_hgen3", "error[FAULT]: 进程收到致命信号\n")]
    # `--fault-heal`（stage 3 的"可恢复故障"）：修好一行，然后**继续跑**。
    out += [("__lom_hh1", "loment: 故障已修复（未映射页置零）："),
            ("__lom_hh2", "；剩余 "),
            ("__lom_hh3", " 次\n")]
    return out


def _fault_globals_ir(plan: dict) -> str:
    out = [
        _fault_table_globals_ir(plan),
        "@__lom_sig = global [8 x i8] zeroinitializer",
        "@__lom_info = global [8 x i8] zeroinitializer",
        "@__lom_uctx = global [8 x i8] zeroinitializer",
        "@__lom_act = global [32 x i8] zeroinitializer",
        f"@__lom_alt = global [{_FAULT_ALT} x i8] zeroinitializer",
        "@__lom_ss = global [24 x i8] zeroinitializer",
        "@__lom_heal = global [8 x i8] zeroinitializer",
        "@__lom_hexbuf = global [18 x i8] zeroinitializer",
        "@__lom_decbuf = global [3 x i8] zeroinitializer",
    ]
    for name, text in _fault_messages():
        out.append(f"@{name} = global [{len(text.encode('utf-8'))} x i8] {_cstr(text)}")
    return "\n".join(out)


def _ir_wrhex(fn: str = "__wrhex") -> str:
    """定宽十六进制（`0x` + 16 位）。分支-free：`digit = nib + 48 + (nib>9)*39`。"""
    body = [
        f"define void @{fn}(i64 %v) {{",
        "entry:",
        "  %b = ptrtoint ptr @__lom_hexbuf to i64",
        "  %pz = inttoptr i64 %b to ptr",
        "  store i8 48, ptr %pz",
        "  %b1 = add i64 %b, 1",
        "  %p1 = inttoptr i64 %b1 to ptr",
        "  store i8 120, ptr %p1",
    ]
    for i in range(16):
        sh = 60 - 4 * i
        off = 2 + i
        body += [
            f"  %sh{i} = lshr i64 %v, {sh}",
            f"  %n{i} = and i64 %sh{i}, 15",
            f"  %g{i} = icmp ugt i64 %n{i}, 9",
            f"  %z{i} = zext i1 %g{i} to i64",
            f"  %m{i} = mul i64 %z{i}, 39",
            f"  %d{i} = add i64 %n{i}, 48",
            f"  %e{i} = add i64 %d{i}, %m{i}",
            f"  %p{i} = add i64 %b, {off}",
            f"  %q{i} = inttoptr i64 %p{i} to ptr",
            f"  store i8 %e{i}, ptr %q{i}",
        ]
    body += [
        f"  %w = {_SYS3}(i64 1, i64 2, i64 %b, i64 18)",
        "  ret void",
        "}",
    ]
    return "\n".join(body)


def _ir_wrdec(fn: str = "__wrnum") -> str:
    """十进制，**不带前导零**（3 位够信号号与站点序号）。

    三条出口：≥100 写三位；10..99 写两位；个位写一位。缓冲区是同一个，按起止切片。
    """
    lines = [
        f"define void @{fn}(i64 %v) {{",
        "entry:",
        "  %b = ptrtoint ptr @__lom_decbuf to i64",
        "  %h = udiv i64 %v, 100",
        "  %h2 = urem i64 %h, 10",
        "  %h3 = add i64 %h2, 48",
        "  %t = udiv i64 %v, 10",
        "  %t2 = urem i64 %t, 10",
        "  %t3 = add i64 %t2, 48",
        "  %o = urem i64 %v, 10",
        "  %o2 = add i64 %o, 48",
        "  %p0 = inttoptr i64 %b to ptr",
        "  store i8 %h3, ptr %p0",
        "  %b1 = add i64 %b, 1",
        "  %p1 = inttoptr i64 %b1 to ptr",
        "  store i8 %t3, ptr %p1",
        "  %b2 = add i64 %b, 2",
        "  %p2 = inttoptr i64 %b2 to ptr",
        "  store i8 %o2, ptr %p2",
        "  %h0 = icmp eq i64 %h, 0",
        "  br i1 %h0, label %lt100, label %all3",
        "all3:",
        f"  %w3 = {_SYS3}(i64 1, i64 2, i64 %b, i64 3)",
        "  ret void",
        "lt100:",
        "  %t0 = icmp eq i64 %t2, 0",
        "  br i1 %t0, label %one, label %two",
        "two:",
        f"  %w2 = {_SYS3}(i64 1, i64 2, i64 %b1, i64 2)",
        "  ret void",
        "one:",
        f"  %w1 = {_SYS3}(i64 1, i64 2, i64 %b2, i64 1)",
        "  ret void",
        "}",
    ]
    return "\n".join(lines)


def _emit_msg_write(name: str, parts: list[str]) -> None:
    # 同一条消息会在不同块里写多次，而 SSA 名字是**函数级唯一**的 —— 用位置做后缀，
    # 免得 `%b_...` 被重定义（本链接器按名字分配槽，重定义会静默指到同一个槽）。
    u = len(parts)
    text = dict(_fault_messages())[name]
    parts += [f"  %b{u} = ptrtoint ptr @{name} to i64",
              f"  %w{u} = {_SYS3}(i64 1, i64 2, i64 %b{u}, "
              f"i64 {len(text.encode('utf-8'))})"]


def _ir_fn_lookup(parts: list[str]) -> None:
    """`sitenone` 里的退路：PC 不在访存点上时，指出它落在哪个**用户函数**里。

    条数**从表的头部读**（`docs/367` stage 2）：这样这一段运行期文本与程序无关，
    自举镜像可以把字节原样嵌进去，两边的产物才能逐字节相同。
    """
    parts += [
        "  %fn0 = ptrtoint ptr @__lom_fns to i64",
        "  %fnpp = inttoptr i64 %fn0 to ptr",
        "  %fnco = load i32, ptr %fnpp",
        "  %fncv = zext i32 %fnco to i64",
        "  br label %floop",
        "floop:",
        "  %fi = phi i64 [0, %sitenone], [%fi2, %fcont]",
        "  %fv = phi i64 [-1, %sitenone], [%fv2, %fcont]",
        "  %fdone = icmp sge i64 %fi, %fncv",
        "  br i1 %fdone, label %fafter, label %fbody",
        "fbody:",
        "  %fe = mul i64 %fi, 24",
        "  %fp = add i64 %fb, %fe",
        "  %fpp = inttoptr i64 %fp to ptr",
        "  %fs = load i64, ptr %fpp",
        "  %fpe = add i64 %fp, 8",
        "  %fpep = inttoptr i64 %fpe to ptr",
        "  %fen = load i64, ptr %fpep",
        "  %fge = icmp uge i64 %rip, %fs",
        "  %flt = icmp ult i64 %rip, %fen",
        "  %fin = and i1 %fge, %flt",
        "  br i1 %fin, label %ftake, label %fskip",
        "ftake:",
        "  br label %fcont",
        "fskip:",
        "  br label %fcont",
        "fcont:",
        "  %fv2 = phi i64 [%fi, %ftake], [%fv, %fskip]",
        "  %fi2 = add i64 %fi, 1",
        "  br label %floop",
        "fafter:",
        "  %fhas = icmp sge i64 %fv, 0",
        "  br i1 %fhas, label %fshow, label %fnone",
        "fnone:",
    ]
    _emit_msg_write("__lom_ms5", parts)
    parts += ["  br label %sitedone", "fshow:"]
    # 名字：fns[fv] 的 off@+16 / len@+20
    parts += [
        "  %fne = mul i64 %fv, 24",
        "  %fnp = add i64 %fb, %fne",
        "  %fn16 = add i64 %fnp, 16",
        "  %fn16p = inttoptr i64 %fn16 to ptr",
        "  %fno = load i32, ptr %fn16p",
        "  %fnov = zext i32 %fno to i64",
        "  %fn20 = add i64 %fnp, 20",
        "  %fn20p = inttoptr i64 %fn20 to ptr",
        "  %fnl = load i32, ptr %fn20p",
        "  %fnlv = zext i32 %fnl to i64",
        "  %fnm2 = add i64 %xb, %fnov",
    ]
    _emit_msg_write("__lom_ms5a", parts)
    parts += [f"  %w_fnm = {_SYS3}(i64 1, i64 2, i64 %fnm2, i64 %fnlv)"]
    _emit_msg_write("__lom_ms5b", parts)
    parts += ["  br label %sitedone"]


def _ir_site_lookup(parts: list[str], from_block: str = "entry") -> None:
    """站点查找 + 打印。表按地址升序；取 `addr <= rip` 的最大那一条。

    站点的条数也从表头读（同 `_ir_fn_lookup` 的理由）。
    """
    parts += [
        "  %sb0 = ptrtoint ptr @__lom_sites to i64",
        "  %sb = add i64 %sb0, 4",
        "  %sn0 = ptrtoint ptr @__lom_sites to i64",
        "  %snpp = inttoptr i64 %sn0 to ptr",
        "  %snco = load i32, ptr %snpp",
        "  %sncv = zext i32 %snco to i64",
        "  %fb0 = ptrtoint ptr @__lom_fns to i64",
        "  %fb = add i64 %fb0, 4",
        "  %kb0 = ptrtoint ptr @__lom_blks to i64",
        "  %kb = add i64 %kb0, 4",
        "  %xb0 = ptrtoint ptr @__lom_strs to i64",
        "  %xb = add i64 %xb0, 0",
        "  br label %sloop",
        "sloop:",
        f"  %si = phi i64 [0, %{from_block}], [%si2, %scont]",
        f"  %sbi = phi i64 [-1, %{from_block}], [%sbi2, %scont]",
        f"  %sba = phi i64 [0, %{from_block}], [%sba2, %scont]",
        "  %sdone = icmp sge i64 %si, %sncv",
        "  br i1 %sdone, label %safter, label %sbody",
        "sbody:",
        "  %so = mul i64 %si, 24",
        "  %sa = add i64 %sb, %so",
        "  %sap = inttoptr i64 %sa to ptr",
        "  %sv = load i64, ptr %sap",
        "  %sp20 = add i64 %sa, 20",
        "  %slp = inttoptr i64 %sp20 to ptr",
        "  %sln = load i32, ptr %slp",
        "  %slv = zext i32 %sln to i64",
        "  %sle = icmp ule i64 %sv, %rip",
        "  %sle2 = add i64 %sv, %slv",
        "  %slt = icmp ult i64 %rip, %sle2",
        "  %sin = and i1 %sle, %slt",
        "  %sgt = icmp ugt i64 %sv, %sba",
        "  %sboth = and i1 %sin, %sgt",
        "  br i1 %sboth, label %stake, label %sskip",
        "stake:",
        "  br label %scont",
        "sskip:",
        "  br label %scont",
        "scont:",
        "  %sbi2 = phi i64 [%si, %stake], [%sbi, %sskip]",
        "  %sba2 = phi i64 [%sv, %stake], [%sba, %sskip]",
        "  %si2 = add i64 %si, 1",
        "  br label %sloop",
        "safter:",
        "  %shas = icmp sge i64 %sbi, 0",
        "  br i1 %shas, label %showsite, label %sitenone",
        "sitenone:",
    ]
    _ir_fn_lookup(parts)
    parts += ["showsite:"]
    # site[bi]：fn@+8(u32) blk@+12(u32) ins@+16(u32) len@+20(u32)
    parts += [
        "  %se = mul i64 %sbi, 24",
        "  %sp = add i64 %sb, %se",
        "  %sp8 = add i64 %sp, 8",
        "  %spp = inttoptr i64 %sp8 to ptr",
        "  %siv = load i32, ptr %spp",
        "  %sfi = zext i32 %siv to i64",
        "  %sp12 = add i64 %sp, 12",
        "  %sbp = inttoptr i64 %sp12 to ptr",
        "  %sbv = load i32, ptr %sbp",
        "  %sblk = zext i32 %sbv to i64",
        "  %sp16 = add i64 %sp, 16",
        "  %sxp = inttoptr i64 %sp16 to ptr",
        "  %sxv = load i64, ptr %sxp",
        "  %sins = and i64 %sxv, 4294967295",
        # fn 名：fns[fi] = {start,end u64, off u32, len u32}
        "  %fe = mul i64 %sfi, 24",
        "  %fp = add i64 %fb, %fe",
        "  %fp16 = add i64 %fp, 16",
        "  %fpp = inttoptr i64 %fp16 to ptr",
        "  %fno = load i32, ptr %fpp",
        "  %fnov = zext i32 %fno to i64",
        "  %fp20 = add i64 %fp, 20",
        "  %fnlp = inttoptr i64 %fp20 to ptr",
        "  %fnl = load i32, ptr %fnlp",
        "  %fnlv = zext i32 %fnl to i64",
        # blk 名：blks[bi] = {off u32, len u32}
        "  %ke = mul i64 %sblk, 8",
        "  %kp = add i64 %kb, %ke",
        "  %kpp = inttoptr i64 %kp to ptr",
        "  %kno = load i32, ptr %kpp",
        "  %knov = zext i32 %kno to i64",
        "  %kp4 = add i64 %kp, 4",
        "  %knlp = inttoptr i64 %kp4 to ptr",
        "  %knl = load i32, ptr %knlp",
        "  %knlv = zext i32 %knl to i64",
        "  %fnm = add i64 %xb, %fnov",
        "  %knm = add i64 %xb, %knov",
    ]
    _emit_msg_write("__lom_ms1", parts)
    parts += [f"  %w_fn = {_SYS3}(i64 1, i64 2, i64 %fnm, i64 %fnlv)"]
    _emit_msg_write("__lom_ms2", parts)
    parts += [f"  %w_kn = {_SYS3}(i64 1, i64 2, i64 %knm, i64 %knlv)"]
    _emit_msg_write("__lom_ms3", parts)
    parts += ["  call void @__wrnum(i64 %sins)"]
    _emit_msg_write("__lom_ms4", parts)
    parts += ["  %sdist = sub i64 %rip, %sba",
              "  call void @__wrhex(i64 %sdist)"]
    _emit_msg_write("__lom_ms6", parts)
    parts += ["  br label %sitedone", "sitedone:"]


def _ir_heading(parts: list[str]) -> None:
    """`loment: <名字>（信号 N）` / `error[码]: 标题` / `--> 原生产物:0x…`。

    五个信号各自一块（IR 里没有查表之外更便宜的分派）；都不是就走通用块。
    """
    n = len(_FAULT_TITLES)
    parts += [f"  %h0 = icmp eq i64 %sig, {_FAULT_TITLES[0][0]}",
              "  br i1 %h0, label %hb0, label %hc1"]
    for i, (sig, name, tag, title) in enumerate(_FAULT_TITLES):
        parts.append(f"hb{i}:")
        _emit_msg_write(f"__lom_hn{sig}", parts)
        parts += ["  call void @__wrnum(i64 %sig)"]
        _emit_msg_write(f"__lom_hd{sig}", parts)
        _emit_msg_write(f"__lom_he{sig}", parts)
        _emit_msg_write("__lom_m_arrow", parts)
        parts += ["  call void @__wrhex(i64 %rip)"]
        _emit_msg_write("__lom_m_nl", parts)
        parts += ["  br label %hdone"]
        if i + 1 < n:
            nxt = _FAULT_TITLES[i + 1][0]
            parts += [f"hc{i+1}:", f"  %h{i+1} = icmp eq i64 %sig, {nxt}",
                      f"  br i1 %h{i+1}, label %hb{i+1}, label %hc{i+2}"]
    parts.append(f"hc{n}:")
    _emit_msg_write("__lom_hgen", parts)
    parts += ["  call void @__wrnum(i64 %sig)"]
    _emit_msg_write("__lom_hgen2", parts)
    _emit_msg_write("__lom_hgen3", parts)
    _emit_msg_write("__lom_m_arrow", parts)
    parts += ["  call void @__wrhex(i64 %rip)"]
    _emit_msg_write("__lom_m_nl", parts)
    parts += ["  br label %hdone"]


def _fault_runtime_fixed() -> str:
    """与**程序无关**的那一段（`__wrhex` 到 shim）。

    自举镜像把这一段的字节原样嵌进去（`docs/367` stage 2）—— 两边的产物要逐字节
    相同，所以这一段在这边是唯一的真源，那边是它的十六进制副本（判据
    `loment_elf_test::test_mirror_fault_report_matches_reference` 逐字节比两份产物）。
    """
    parts = [_ir_wrhex(), "", _ir_wrdec(), ""]
    parts += [
        # rt_sigreturn：handler 不返回，但内核要求有 restorer。
        "define void @__lom_restore() {",
        "entry:",
        f'  %r = call i64 asm sideeffect "syscall", '
        f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},~{{cx}},~{{r11}},~{{memory}}"(i64 15, i64 0, i64 0, i64 0)',
        "  ret void",
        "}",
        "",
        "define i64 @__lom_handler() {",
        "entry:",
        "  %sig = load i64, ptr @__lom_sig",
        "  %inf = load i64, ptr @__lom_info",
        "  %uct = load i64, ptr @__lom_uctx",
        "  %ia = add i64 %inf, 16",
        "  %iap = inttoptr i64 %ia to ptr",
        "  %addr = load i64, ptr %iap",
        "  %ra = add i64 %uct, 168",
        "  %rap = inttoptr i64 %ra to ptr",
        "  %rip = load i64, ptr %rap",
        "  %sa = add i64 %uct, 160",
        "  %sap = inttoptr i64 %sa to ptr",
        "  %rsp = load i64, ptr %sap",
        "  %ea = add i64 %uct, 192",
        "  %eap = inttoptr i64 %ea to ptr",
        "  %err = load i64, ptr %eap",
        # ---- 可恢复故障（`--fault-heal N`）----
        # 只在"没映射"（si_code=SEGV_MAPERR=1）、地址落在低 1 MiB、且预算还有时尝试。
        # 做法是 MAP_FIXED 一页零内存，然后**返回 1** —— shim 看到返回值就 rt_sigreturn，
        # 内核从同一 PC 重新执行那条指令（读/写都落到新页上）。预算存在全局里，
        # 0 就是关闭：这条分支于是与"默认的致命报告"共享同一段文本。
        "  %scp = add i64 %inf, 8",
        "  %scpp = inttoptr i64 %scp to ptr",
        "  %sci = load i32, ptr %scpp",
        "  %scv = zext i32 %sci to i64",
        "  %hb = load i64, ptr @__lom_heal",
        "  %hpos = icmp ugt i64 %hb, 0",
        "  %ismap = icmp eq i64 %scv, 1",
        "  %issig = icmp eq i64 %sig, 11",
        "  %h1 = and i1 %hpos, %ismap",
        "  %h2 = and i1 %h1, %issig",
        "  %low = icmp ult i64 %addr, 1048576",
        "  %hok = and i1 %h2, %low",
        "  br i1 %hok, label %heal, label %hfatal",
        "heal:",
        "  %pg = and i64 %addr, -4096",
        f"  %mp = {_SYS6}(i64 9, i64 %pg, i64 4096, i64 3, i64 50, i64 -1, i64 0)",
        "  %pgot = icmp eq i64 %mp, %pg",
        "  br i1 %pgot, label %hgot, label %hfatal",
        "hgot:",
        "  %hnew = sub i64 %hb, 1",
        "  store i64 %hnew, ptr @__lom_heal",
    ]
    _emit_msg_write("__lom_hh1", parts)
    parts += ["  call void @__wrhex(i64 %addr)"]
    _emit_msg_write("__lom_hh2", parts)
    parts += ["  call void @__wrnum(i64 %hnew)"]
    _emit_msg_write("__lom_hh3", parts)
    parts += ["  ret i64 1", "hfatal:"]
    _ir_heading(parts)
    parts.append("hdone:")
    _emit_msg_write("__lom_m_sig", parts)
    parts += ["  call void @__wrnum(i64 %sig)"]
    _emit_msg_write("__lom_m_nl", parts)
    _emit_msg_write("__lom_m_addr", parts)
    # 读写方向只对**页错误**有意义：SIGILL/FPE/ABRT 的 `REG_ERR` 不是页错误码，
    # 拿它判方向会凭空说一个"读"。`err == 0` 就不说方向。
    parts += ["  call void @__wrhex(i64 %addr)",
              "  %haserr = icmp ne i64 %err, 0",
              "  br i1 %haserr, label %dirchk, label %dirnone",
              "dirchk:",
              "  %isw = and i64 %err, 2",
              "  %iswr = icmp ne i64 %isw, 0",
              "  br i1 %iswr, label %dwr, label %drd",
              "dwr:"]
    _emit_msg_write("__lom_m_wr", parts)
    parts += ["  br label %ddone", "drd:"]
    _emit_msg_write("__lom_m_rd", parts)
    parts += ["  br label %ddone",
              "dirnone:"]                     # 没有页错误码：只说地址，不编方向
    _emit_msg_write("__lom_m_nl", parts)
    parts += ["  br label %ddone", "ddone:"]
    _emit_msg_write("__lom_m_pc", parts)
    parts += ["  call void @__wrhex(i64 %rip)"]
    _emit_msg_write("__lom_m_rsp", parts)
    parts += ["  call void @__wrhex(i64 %rsp)"]
    _emit_msg_write("__lom_m_nl", parts)
    _ir_site_lookup(parts, from_block="ddone")
    # 栈溢出时故障地址可能**高于** RSP（写进下一页的守卫页），所以两个方向都算；
    # 无符号减法在反方向会回绕成一个巨大的数，不会被判成"近"。
    parts += ["  %d1 = sub i64 %rsp, %addr",
              "  %d2 = sub i64 %addr, %rsp",
              "  %s1 = icmp ult i64 %d1, 65536",
              "  %s2 = icmp ult i64 %d2, 65536",
              "  %gstack = or i1 %s1, %s2",
              "  br i1 %gstack, label %gso, label %gnoso",
              "gso:"]
    _emit_msg_write("__lom_m_so", parts)
    parts += ["  br label %gsodone", "gnoso:", "  br label %gsodone", "gsodone:"]
    _emit_msg_write("__lom_m_fact", parts)
    _emit_msg_write("__lom_m_tip", parts)
    parts += [
        "  ; 恢复默认处置，再重发信号 —— 进程仍然死于信号。",
        f"  %r0 = {_SYS6}(i64 13, i64 %sig, i64 0, i64 0, i64 8, i64 0, i64 0)",
        f"  %pid = {_SYS6}(i64 39, i64 0, i64 0, i64 0, i64 0, i64 0, i64 0)",
        f"  %tid = {_SYS6}(i64 186, i64 0, i64 0, i64 0, i64 0, i64 0, i64 0)",
        f"  %r3 = {_SYS6}(i64 234, i64 %pid, i64 %tid, i64 %sig, i64 0, i64 0, i64 0)",
        "  %add = add i64 %sig, 128",
        f"  %r4 = {_SYS3}(i64 231, i64 %add, i64 0, i64 0)",
        "  ret i64 0",
        "}",
        "",
        # 机器码 shim 的占位函数：名字是真的（`ptrtoint ptr @__lom_shim` 可取），
        # 字节由 `Emitter.emit_func` 直接写出。它排在 handler **之后**：写 rel32 时
        # 要拿 `__lom_handler` 的地址，标签那时才立起来。
        "define void @__lom_shim() {",
        "entry:",
        "  ret void",
        "}",
    ]
    return "\n".join(parts)


def _fault_entry_ir(entry: str, entry_is_main: bool, heal: int = 0) -> str:
    parts = [
        "define i64 @__lom_entry() {",
        "entry:",
        "  %sb = ptrtoint ptr @__lom_ss to i64",
        "  %sp = inttoptr i64 %sb to ptr",
        "  %alt = ptrtoint ptr @__lom_alt to i64",
        "  store i64 %alt, ptr %sp",
        "  %f8 = add i64 %sb, 8",
        "  %fp = inttoptr i64 %f8 to ptr",
        "  store i32 0, ptr %fp",
        "  %f16 = add i64 %sb, 16",
        "  %sq = inttoptr i64 %f16 to ptr",
        f"  store i64 {_FAULT_ALT}, ptr %sq",
        f"  %rss = {_SYS6}(i64 131, i64 %sb, i64 0, i64 0, i64 0, i64 0, i64 0)",
        "  %ab = ptrtoint ptr @__lom_act to i64",
        "  %hs = ptrtoint ptr @__lom_shim to i64",
        "  store i64 %hs, ptr @__lom_act",
        "  %ff = add i64 %ab, 8",
        "  %ffp = inttoptr i64 %ff to ptr",
        f"  store i64 {_FAULT_FLAGS}, ptr %ffp",
        "  %rs = ptrtoint ptr @__lom_restore to i64",
        "  %rf = add i64 %ab, 16",
        "  %rfp = inttoptr i64 %rf to ptr",
        "  store i64 %rs, ptr %rfp",
        f"  store i64 {heal}, ptr @__lom_heal",
    ]
    for k, sig in enumerate(_FAULT_SIGNALS):
        parts.append(f"  %s{k} = {_SYS6}(i64 13, i64 {sig}, i64 %ab, i64 0, "
                     f"i64 8, i64 0, i64 0)")
    if entry_is_main:
        parts += [f"  %rv = call i32 @{entry}()",
                  "  %rv2 = zext i32 %rv to i64",
                  "  ret i64 %rv2"]
    else:
        parts += [f"  call void @{entry}()", "  ret i64 0"]
    parts.append("}")
    return "\n".join(parts)


def _fault_runtime_ir(entry: str, entry_is_main: bool, plan: dict,
                      heal: int = 0) -> str:
    """安装 + 报告（信号/地址/PC/RSP/站点）。站点表由链接器发射后回填。"""
    return "\n".join([_fault_globals_ir(plan), "", _fault_runtime_fixed(), "",
                      _fault_entry_ir(entry, entry_is_main, heal)])


def _fault_shim_bytes(sig_addr: int, info_addr: int, uctx_addr: int,
                      handler_addr: int, shim_addr: int) -> bytes:
    """内核 → 栈机 的桥：把 rdi/rsi/rdx 落到三个全局，再 `call` handler。"""
    b = bytearray()
    b += bytes([0x89, 0x3C, 0x25]) + struct.pack("<I", sig_addr)      # mov [abs], edi
    b += bytes([0x48, 0x89, 0x34, 0x25]) + struct.pack("<I", info_addr)   # mov [abs], rsi
    b += bytes([0x48, 0x89, 0x14, 0x25]) + struct.pack("<I", uctx_addr)   # mov [abs], rdx
    # call rel32：rel = target - (addr_of_next_instruction)
    call_at = shim_addr + len(b)
    b += bytes([0xE8]) + struct.pack("<i", handler_addr - (call_at + 5))
    # 处理器返回 1 = **已修复**：`rt_sigreturn` 从同一条指令继续跑。
    # ⚠ 内核给的 `rsp` 指着 `rt_sigframe` 的**开头**（那一格是 restorer 地址）；
    # glibc 的 restorer 是被 handler 的 `ret` **弹进去**的，所以它运行时 `rsp` 已经
    # 是 `&ucontext`（frame+8）。这里走的是 call/ret（净 0），必须自己补 `add rsp, 8`，
    # 否则内核把 frame 开头当成 ucontext 读 —— 实测 PC 跳到栈上、第二个信号才死。
    # 返回 0（致命那条路其实不返回）就 ud2。
    b += bytes([0x85, 0xC0])                                          # test eax, eax
    b += bytes([0x74, 0x0B])                                          # je  ud2 (skip 11)
    b += bytes([0x48, 0x83, 0xC4, 0x08])                              # add rsp, 8
    b += bytes([0xB8, 0x0F, 0x00, 0x00, 0x00])                        # mov eax, 15
    b += bytes([0x0F, 0x05])                                          # syscall
    b += bytes([0x0F, 0x0B])                                          # ud2
    return bytes(b)


def compile_ll(text: str, objects: list | None = None,
               allow_ports: bool = False, interp: str | None = None,
               collect_hot: bool = True,
               hotswap: bool = False,
               fault_report: bool = False,
               fault_heal: int = 0) -> tuple[bytes, dict]:
    objects = objects or []
    global TEXT_VADDR, DATA_VADDR
    TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # PE 目标会改这两个全局
    globals_, funcs, externs = parse_ll(text)
    fault_entry: str | None = None
    fault_plan: dict | None = None
    if fault_heal > 0:
        fault_report = True
    if fault_report:
        if hotswap:
            raise Unsupported("`--fault-report` 与热更新入口闸暂不叠加（docs/367 stage 1）")
        user_entry = _entry_name(funcs)
        user_is_main = user_entry == "main"
        fault_plan = _fault_plan(funcs)
        text = text + "\n" + _fault_runtime_ir(user_entry, user_is_main,
                                               fault_plan, fault_heal)
        globals_, funcs, externs = parse_ll(text)
        # 四张表在 IR 里是 `zeroinitializer`（见 `_fault_table_globals_ir`）；这里给
        # 一个标记字节把它们钉进 `.data` —— 布局判据是"内容 != 全零"。内容在
        # `_fault_patch_tables` 里整片回填，所以标记本身不出现在产物里。
        for g in globals_:
            if g.name in ("@__lom_sites", "@__lom_fns",
                          "@__lom_blks", "@__lom_strs"):
                g.data = b"\xEE" + b"\0" * (size_of(g.ty) - 1)
        fault_entry = "__lom_entry"
    # A name that is both `declare`d and `define`d in this text is an internal call.
    # Multi-input linking (docs/283 sec 6.2) concatenates the app IR with library IRs;
    # the app's `declare __jl_x` then meets the library's `define __jl_x`. Keeping the
    # name in `externs` would send the call through the C ABI register path, which is
    # wrong on PE. Same rule as `merge_ir_texts` (declare satisfied by define).
    _defined = {f.name for f in funcs}
    externs = [x for x in externs if x not in _defined]
    # **`no_std` 的带牙检查**（`#941` / #539）：声明只用核那一层，就不能带宿主层。
    # 与 `docs/227` 判据 B 同形 —— 编译器是那句话的作者，能拿一份不是自己生成的 IR
    # 去核它的只有链接器这一侧。
    if "; loment-mode: no_std" in text and "; loment-port: hosted" in text:
        raise Unsupported(
            "这份 IR 声明了 `no_std`（只用核那一层），却带着宿主层"
            "（`; loment-port: hosted`）—— `no_std` 的定义里没有宿主；"
            "去掉 `choose no_std`，或去掉 `choose hosted`")
    # **`no_gc` 的带牙检查**（`#538`）：IR 自己声明"不带收集器"，那就核它真的没带。
    # 这是 `docs/227` 判据 B 的镜像 —— 那边判"声明了 `no_runtime` 却拖进运行期"，
    # 这边判"声明了 `no_gc` 却带着收集器"。检查放在**链接器**而不是编译器，因为
    # 编译器正是那句话的作者；能拿一份不是自己生成的 IR 来核它的，只有这一侧。
    # **dismemory（docs/373）**：这份 IR 要 LVM 在没有地址的内存模型上跑。原生档有地址，
    # 照常链出来只会静默错编 —— 与 no_std / no_gc 同一条纪律：点名拒。
    if "; loment-memory: dismemory" in text:
        raise Unsupported(
            "这份 IR 要求 LVM 的 dismemory 档（内存里没有地址，docs/373）；"
            "原生档有地址，链不出来 —— 用 LVM 跑它")
    if "; loment-gc: no_gc" in text and (
            "@__loment_collect" in text or "@__loment_roots" in text):
        raise Unsupported(
            "这份 IR 声明了 `no_gc`（产物不带收集器），却带着收集器"
            "（`@__loment_collect` / `@__loment_roots`）—— 两句话不能同时成立；"
            "去掉 `choose no_gc`，或去掉产生收集器的那一档")
    # **先把三种输入分开**（`#616`）：归档要展开、共享对象只提供导出表、其余才是要摆的
    # `.o`。以前这一步先做归档展开、后滤共享对象 —— 而展开时要读 `o.undefined`，
    # `SharedObject` 没有那个字段，于是 `--link libz.so --link libfoo.a` 直接抛
    # `AttributeError`（用户看到 Python 栈，不是诊断）。
    shared = [o for o in objects if isinstance(o, SharedObject)]
    archives = [o for o in objects if isinstance(o, Archive)]
    direct = [o for o in objects if not isinstance(o, (Archive, SharedObject))]
    # 归档展开 (docs/173 阶段 2): 先算出"当前需要的符号", 再让每个归档按固定点挑成员。
    # 需求有两个来源: ① 我们自己 `declare` 的外部符号; ② 直接给的 `.o` 里未定义的符号。
    # 两者都要, 因为真实库里常常是"我的 a.o 引用 ar 成员里的 b"。
    objects = list(direct)
    if archives:
        need = {x for x in externs if not x.startswith("llvm.")}
        for o in direct:
            need |= set(o.undefined)
        for a in archives:
            objects += a.select(need)
    # **端口门**在**所有标签立完之后**判（`#615`）：那时"还剩什么解析不到"才是真正的
    # 端口。判在展开这一步会把"a.o 调 g、b.o 定义 g"这种自闭合组合也拒掉。
    # **多个共享对象**（`#596`）：每个一个 `DT_NEEDED`，导入按 `--link` 顺序解析
    # （第一个导出它的库胜出）。`docs/219` 让用户同时链 `libz.so.1` 与 `libc.so.6`
    # 拿 `malloc`/`printf`，原先那条命令必然失败。
    # 全局布局: bytes 在 data, 全零在 bss
    data = bytearray()
    bss = 0
    for g in globals_:  # 第一遍: 有内容的进 data
        if g.data != b"\0" * size_of(g.ty):
            while len(data) % 8:
                data.append(0)
            g.addr = DATA_VADDR + len(data)
            data += g.data
    while len(data) % 8:
        data.append(0)
    # **外部对象的数据节**（`docs/219` §8b 第 1 条）：接在我们自己的全局**之后**、
    # bss **之前**。顺序不能换 —— bss 的地址是在这一趟**之后**才算的
    # （`len(data) + bss`），先算 bss 再追加数据会让两者撞在一起。
    # 每个对象的数据块按它**自己的对齐要求**（`sh_addralign` 的上界，至少 16）落位。
    # 只对齐到 16 是不够的：对象里那个 `.rodata.cst32` 相对块首是 32 对齐，而块首
    # 落在 `16 (mod 32)` 时它就成了 `16 (mod 32)` —— AVX 的 `vmovdqa` 当场 #GP（`#612`）。
    dbases: list[int] = []
    for obj in objects:
        a = max(16, getattr(obj, "data_align", 16))
        while len(data) % a:
            data.append(0)
        dbases.append(len(data))
        data += obj.data
    # **静态 GOT**（`#609`）：`-fPIC` 对象用 `GOTPCREL` / `*_GOTPCRELX` 说"地址在这个
    # GOT 槽里，相对 PC"。我们在数据段里造这些槽，内容就是符号地址；不做 `mov`->`lea`
    # 的松弛也**仍然正确** —— 多一次取存，少一条路径。
    got_names: list[str] = []
    for obj in objects:
        for _sec, _off, ty, nm, _add in obj.relocs:
            if ty in (REL_GOTPCREL, REL_GOTPCRELX, REL_REX_GOTPCRELX) \
                    and nm not in got_names:
                got_names.append(nm)
    got_names.sort()
    got_off = align_up(len(data), 8)
    got_addr = {nm: DATA_VADDR + got_off + 8 * k for k, nm in enumerate(got_names)}
    if got_names:
        data += bytes(8 * len(got_names))
    # **TLS 块**（`docs/219` S2）：`-fstack-protector` 编出来的库（发行版的 zlib 就是）
    # 会读 **`fs:0x28`** 取栈金丝雀。我们**没有 TLS**，`fs` 是 0 —— 那就是去读地址 `0x28`，
    # 未映射 ⇒ **一进去就段错误**。所以带外部对象时给一小块 TLS，入口用
    # `arch_prctl(ARCH_SET_FS)` 把 `fs` 指过来。
    #
    # 金丝雀**不需要是随机数**：函数入口把它存到栈上、出口比一次，中间没人改 ⇒ 永远相等。
    # 这块只要**存在且可读写**就够了。**谁都不该调到 `__stack_chk_fail`** —— 调到了说明
    # 栈真被踩了，那时响亮地死（`ud2`）才对。
    #
    # **有 `SHF_TLS` 节时**（`#610`）块要按真正的 TLS 布局来：`.tdata` 在前、`.tbss` 在后，
    # 线程指针（`fs`）指**块尾**（x86-64 的 variant II —— `TPOFF` 因此是负偏移），块尾之后
    # 留 0x40 字节给 TCB / `fs:0x28` 的栈金丝雀。`TPOFF32/64` 的重定位就落在这个布局上。
    tls_addr = 0
    tls_base = 0
    tls_bases: list[int] = []
    has_tls = any(obj.tls for obj in objects)
    if has_tls and shared:
        # 动态链接时 `fs` 归 loader 管，我们不再装自己的 TLS 块；此时给 `.o` 里的
        # `TPOFF` 算偏移就没有"我们的线程指针"可指 —— 宁可点名拒，不静默算错。
        raise Unsupported(
            "带 TLS 的外部 `.o` 与共享对象一起链 —— 这一版的 TLS 块只在自立产物里安装，"
            "动态链接时 `fs` 归 loader 管（`#610`）")
    if objects and not shared:
        if has_tls:
            a = max(16, max(obj.tls_align for obj in objects))
            while len(data) % a:
                data.append(0)
            tls_base = DATA_VADDR + len(data)
            cur = 0
            parts = bytearray()
            for obj in objects:
                a2 = max(1, obj.tls_align)
                while cur % a2:
                    parts.append(0)
                    cur += 1
                tls_bases.append(cur)
                parts += obj.tls
                cur += len(obj.tls)
            # TP 要 16 对齐（TCB 的 SSE 状态按这个假设放）；补零只影响内存布局。
            while cur % 16:
                parts.append(0)
                cur += 1
            data += parts
            tls_addr = tls_base + cur          # TP = 块尾（variant II）
            data += bytes(0x40)                # TCB + fs:0x28 的金丝雀槽
        else:
            while len(data) % 16:
                data.append(0)
            tls_addr = DATA_VADDR + len(data)
            data += bytes(_TLS_SIZE)
    # **数据长度要 8 对齐再往下算**：自举侧是 `align_to(cell(tab, C_GB), 8)`，
    # 它按那个长度放 bss、也按那个长度写段大小 —— 这里不对齐就会在"bss 的地址"与
    # "数据段字节数"两处各差一截（实测差 2 字节：`filesz=22` vs `24`）。
    while len(data) % 8:
        data.append(0)
    for g in globals_:  # 第二遍: 全零的进 bss（排在 data 之后, 否则会和 rodata 撞）
        sz = size_of(g.ty)
        if g.data == b"\0" * sz:
            bss = align_up(bss, 8)
            g.addr = DATA_VADDR + len(data) + bss
            bss += align_up(sz, 8)
    # **`llvm.*` 不是外部函数, 是内建**: 运行期块里有 `declare void @llvm.trap()`, 而 `_call`
    # 对它是**特判** (发 `ud2` 而不是真去 call)。放进 externs 会把它抢进 C ABI 那条路,
    # 于是 `call llvm.trap` 在 finalize 里报"未定义的标签" —— 实测把 loment_elf_test 从
    # 7/7 打到 4/7。所以这里滤掉 (除 llvm.* 之外没有别的内建 declare)。
    em = Emitter(globals_, {x for x in externs if not x.startswith("llvm.")},
                 hotswap=hotswap, swappable=[f.name for f in funcs])
    if fault_entry is not None:
        em.fault = {"entry": fault_entry, "plan": fault_plan, "funcs": funcs}
    if hotswap:
        # 入口闸写/读的两个字：epoch（`#1151`）与调用者的帧指针（`#1152`）。
        # 它们必须相邻且相隔 8 —— 闸里用 `[rax+8]` 存，固定核按 `epoch+8` 读。
        em.asm.labels["__loment_hot_epoch"] = DATA_VADDR + len(data) + bss
        bss += 16
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    # **TLS 只在"没有 loader 替我们管"的时候才自己装**。
    #
    # 静态链外部 `.o` 时确实得自己装（`docs/219` S2 那条教训）：系统里的库多半是
    # `-fstack-protector` 编的，第一件事就是读 `fs:0x28`；不把 `fs` 指过去，读的就是
    # 地址 0x28，一进去就段错误。
    #
    # **但动态链接时正好相反**：`fs` 这时归 `ld.so` 管，指向 glibc 的整块 TLS
    # （`errno`、`tcache`、栈金丝雀都在里面）。我们再 `arch_prctl(ARCH_SET_FS)` 一次，
    # 就把 glibc 的 TLS 顶掉了 —— 症状不是"金丝雀不对"，是 zlib 一路调到 libc 的
    # `malloc` 时读到垃圾的线程指针（实测崩在 libc 里，`fs_base` 指着我们那块）。
    has_foreign = bool(objects) and not shared
    if has_foreign:
        em.asm.labels["__loment_tls"] = tls_addr
    entry = fault_entry or _entry_name(funcs)
    # 构造函数表的位置（`#611`）：表本身已经摆在 `data` 里，这里只把首尾地址立成标签，
    # 入口桩按它们走表。一个对象都没有时 `ctor_ranges` 为空，桩里一条指令都不多。
    ctor_ranges: list[tuple[int, int]] = []
    fini_ranges: list[tuple[int, int]] = []
    for i, obj in enumerate(objects):
        for doff, size in obj.ctors:
            ctor_ranges.append((DATA_VADDR + dbases[i] + doff, size))
        for doff, size in obj.finis:
            fini_ranges.append((DATA_VADDR + dbases[i] + doff, size))
    for j, (cstart, csize) in enumerate(ctor_ranges):
        em.asm.labels[f"__loment_ctor_{j}"] = cstart
        em.asm.labels[f"__loment_ctor_end_{j}"] = cstart + csize
    for j, (fstart, fsize) in enumerate(fini_ranges):
        em.asm.labels[f"__loment_fini_{j}"] = fstart
        em.asm.labels[f"__loment_fini_end_{j}"] = fstart + fsize
    # **出口**：链了共享对象、而它导出了 `exit` 时走 libc（`#605`）；否则保持 `syscall(60)`。
    # 用"导出表里有没有 `exit`"而不是"有没有共享对象"：只有 libz 的产物没有 stdio 要刷，
    # 也就不该凭空多出一条导入。
    call_exit = any("exit" in so.exports for so in shared)
    if hotswap:
        em.emit_trampolines()
        em.emit_gate()
        em.hot_header["stride"] = HOT_STRIDE
        em.hot_header["slots"] = len(funcs)
    em.emit_entry_stub(entry, tls=has_foreign, ctors=len(ctor_ranges),
                       finis=len(fini_ranges), call_exit=call_exit)
    for f in funcs:
        em.emit_func(f)
    if fault_plan is not None:
        # 所有函数都发完了：站点地址、函数区间、名字池这一刻才是最终的。
        _fault_patch_tables(em, fault_plan, data, em.asm.here())
    # **收编面**（`docs/219` §6）：链里带了**任何外部输入**（`.o` 或 `.so`），就把
    # "可闭合"那几个名字提供出来，于是它们**不再是未定义符号** —— 从表上划掉。
    # 判据是"有没有外部输入"，不是"有没有人要它"：精确到后者要链接器先算出需求集，
    # 自举侧 `lomelf.lomt` 一次只驻留一个对象，那一格留到下一轮（`docs/219` §8b）。
    # **`.so` 也要发**（`#631`）：只链 `.so` 时原先一格收编面都没有，`memcpy` 一族
    # 落到 `.so` 的导出表上 —— 而 glibc 把它们全发成 IFUNC/非默认版本，等于一个都落不下。
    # 名字的另一端在我们手里（§5.2 条件二），发出来才算兑现文档里的那句话。
    if objects or shared:
        em.emit_conscript({f.name for f in funcs}, externs)
    # 外部目标文件 (docs/173 阶段 1/2): 三步, **顺序不能换** ——
    #   ① **摆位置**: 每个对象的 `.text` 接在我们自己的代码之后 (16 对齐), 记下基址;
    #   ② **建符号表**: 把我们自己的标签与所有对象的导出符号合成一张表。**必须先全摆完**:
    #      `a.o` 调 `b.o` 时, `a.o` 的重定位指向一个只有 `b.o` 知道的地址;
    #   ③ **应用重定位**: 到这一步才能算 `S + A - P`。
    # 调用点照旧走现有的 `call`/fixup 机制 (唯一多出来的是"参数进寄存器", 见 `Emitter._call`)。
    bases: list[int] = []
    appended: list[str] = []
    #: 对象提供的定义来自哪个绑定（0 LOCL / 1 GLOB / 2 WEAK）。`em.asm.labels` 里没有它
    #: 就说明那个名字是我们自己的（IR/收编面）—— 那是**不可覆盖**的。
    obj_binds: dict[str, int] = {}

    def place(nm: str, addr: int, bind: int, owner: str) -> None:
        """登记一个对象符号，同时执行**强盖弱 + 双强报错**（`#618`）。"""
        if nm in em.asm.labels:
            prev = obj_binds.get(nm)
            if prev is None:
                return              # 我们自己的标签/收编面优先（docs/219 §6）
            if prev == 2 and bind != 2:
                em.asm.labels[nm] = addr       # 强定义盖过先来的弱定义
                obj_binds[nm] = bind
                appended.append(nm)
            elif prev != 2 and bind != 2:
                raise Unsupported(
                    f"{owner}: 符号 {nm} 重复定义 —— 已经有一个强定义来自别的对象")
            # 弱 + 弱 / 强 + 弱: 先来的为准
            return
        em.asm.labels[nm] = addr
        obj_binds[nm] = bind
        appended.append(nm)

    for i, obj in enumerate(objects):
        a = max(16, getattr(obj, "text_align", 16))
        while len(em.asm.buf) % a:
            em.asm.buf.append(0)
        bases.append(len(em.asm.buf))
        seen: set[str] = set()
        for nm, val in obj.syms.items():
            if nm in seen:
                continue
            seen.add(nm)
            place(nm, TEXT_VADDR + bases[-1] + val, obj.binds.get(nm, 1), obj.name)
        # `SHN_ABS` 的定义：地址就是 `st_value`，与段基址无关（`#619`）。
        for nm, val in obj.absyms.items():
            if nm in seen:
                continue
            seen.add(nm)
            place(nm, val, obj.binds.get(nm, 1), obj.name)
        # 数据节里的符号（含**局部**，见 `ForeignObject._load`）：地址落在 DATA 段。
        for nm, val in obj.dsyms.items():
            if nm in seen:
                continue
            seen.add(nm)
            place(nm, DATA_VADDR + dbases[i] + val, obj.binds.get(nm, 1), obj.name)
        # TLS 符号：地址 = TLS 块基址 + 该对象在块里的起点 + 节内偏移（`#610`）。
        if has_tls:
            for nm, val in obj.tsyms.items():
                if nm in em.asm.labels:
                    continue
                em.asm.labels[nm] = tls_base + tls_bases[i] + val
                appended.append(nm)
        em.asm.buf += obj.text
    # **`_GLOBAL_OFFSET_TABLE_` 是我们的符号**（`#617`）：`-fPIC` / 弱引用的对象会把它报成
    # `GLOBAL UND`，但它由**链接器**给（就是静态 GOT 的基址）。不定义它，端口门会把它
    # 当成"通向世界的端口"，一个自闭合的 PIC 对象因此被拒。
    em.asm.labels.setdefault("_GLOBAL_OFFSET_TABLE_", DATA_VADDR + got_off)
    # **端口门**（2026-10-12 用户定：0.1.4.5 里 libc 不可调用）。
    #
    # ⚠ 判的是**解析之后还剩什么**（`#615`）：以前拿每个对象自己的 `undefined` 清单来判，
    # 于是 `a.o` 调 `g`、`b.o` 定义 `g` 的普通组合也被当成"通向世界"；对象调
    # `memcpy`/`malloc`（收编面会提供）同样被冤枉。现在只有**标签表里仍然没有**的名字
    # 才算端口 —— 与"最后会不会变成导入/未定义"是同一件事。
    if not allow_ports:
        ported = sorted({nm for o in objects for nm in o.undefined
                         if nm not in em.asm.labels})
        if ported or shared:
            # 一条共享对象本身就是"这份产物要在运行期往外要东西"—— 比带未定义符号的 `.o` 更明确。
            shown = ", ".join(ported[:6]) + ("…" if len(ported) > 6 else "") \
                if ported else "（共享对象：" + ", ".join(s.soname for s in shared) + "）"
            raise Unsupported(
                f"这份产物带了通向世界的端口：{shown} —— 链接器默认不收。"
                f"**链接器不知道渠道**（它是一只被启动器调起来的独立工具），所以默认永远是拒；"
                f"启动器在两个渠道都传 `--allow-ports`（`#406` 开闸，2026-10-17）。"
                f"自己调链接器时要它就得显式写 `--allow-ports`；"
                f"自闭合的对象（没有未定义符号、不链共享对象）不受这条影响")
    # ---- V2（`docs/183` §4）：链了共享对象 ⇒ 产物是**动态链接**的。
    #
    # 此时"未定义的标签"不是错：它们要是共享对象的导出，就变成**导入** —— 我们发一个 PLT 桩，
    # 桩的地址就是那个标签的地址，运行期由 loader 把真实的函数地址填进 GOT。
    # 名字**不在导出表里**才报错，而且**报到链接期**（不是留给运行时崩）。
    #
    # **导入集合必须在应用对象重定位之前立好**（`#627`）：`helper.o` 的重定位指向
    # `libz.so` 的导出时，那个标签只有 PLT 桩能提供；先应用重定位就会以"未定义的符号"失败。
    dyn_info = None
    imported: list[str] = []
    if shared:
        # ① 我们自己 IR 里**走 extern 路径**发出的 call（`#630`）：只有这些该变成导入。
        #    没有 `declare` 的调用留在 `rel`，finalize 仍按"未定义的标签"报错 —— 它发的是
        #    Loment 的栈约定，绑到 PLT 上等于让 C 读垃圾实参。
        imports = {lbl for _site, lbl, _w, k in em.asm.fixups
                   if k == "ext" and lbl not in em.asm.labels}
        # ② 外部对象自己的未定义引用（`#627`）。对象是 C 代码，按定义就是 C ABI。
        imports.update(nm for o in objects for nm in o.undefined
                       if nm not in em.asm.labels)
        # 弱未定义引用只有在 `.so` **确实导出**它时才绑过去；否则按 `#617` 写 0。
        exports_any = {n for so in shared for n in so.exports}
        imports.update(nm for o in objects for nm in o.weak_undefined
                       if nm not in em.asm.labels and nm in exports_any)
        # 入口桩在 V2 走 `exit(3)`（`#605`）时那条调用的 kind 不是 `ext` —— 显式补进来，
        # 否则它会以"未定义的标签"落空（`tests` 抓到的 `${exit}`）。
        if call_exit:
            imports.add("exit")
        imports = sorted(imports)
        for nm in imports:
            # **hidden 也是"这个库里有这个名字"**（`#642`）：选 owner 时把它算进来，
            # 好让 `resolve()` 给出"它藏着，loader 不会跨对象绑定"那句 —— 否则用户拿到
            # 的是"没有导出"，方向完全指错（名字明明在 `.dynsym` 里）。
            owner = next((so for so in shared
                          if nm in so.exports or nm in so.hidden), None)
            if owner is None:
                raise Unsupported(
                    ", ".join(so.soname for so in shared)
                    + f": 没有导出 {nm} —— 运行期没人能提供它")
            owner.resolve(nm)
        # PLT 接在**代码之后**：`finalize()` 只回填、不改长度，所以现在就能算出桩的地址并
        # 登记成标签（重定位与回填因此能指到桩上），finalize 之后再按同一长度把它接上。
        tail_vaddr = align_up(DATA_VADDR + len(data) + bss, 0x1000)
        text_tail, tail, plt_labels, dyn_info = build_dynamic_tables(
            TEXT_VADDR, len(em.asm.buf), tail_vaddr, imports,
            [so.soname for so in shared], resolve_interp(interp))
        for nm, at in plt_labels.items():
            em.asm.labels[nm] = at
        imported = imports
    # **GOT 槽的内容 = 符号地址**（`#609`）。槽的**地址**在布局时就定了，内容要等所有
    # 标签立完才知道 —— 所以填在这里、重定位应用之前。（合并 #618–#620 那一轮时这条
    # 被漏掉过：PIC 产物照样链接、跑起来在第一条 `mov [rip+GOT]` 上段错误。）
    for nm, at in got_addr.items():
        if nm in em.asm.labels:
            struct.pack_into("<Q", data, at - DATA_VADDR, em.asm.labels[nm])
    applied = 0

    def reloc_value(ty: int, val: int, here: int, where: str) -> bytes:
        """把一个重定位的**结果**编成要写进去的字节（`S + A` / `S + A - P`）。"""
        if ty in (REL_PC32, REL_PLT32):
            rel = val - here
            if not -2 ** 31 <= rel < 2 ** 31:
                raise Unsupported(f"{where}: PC32 重定位溢出")
            return struct.pack("<i", rel)
        if ty == REL_PC64:
            return struct.pack("<q", val - here)
        if ty == REL_64:
            return struct.pack("<Q", val & 0xFFFFFFFFFFFFFFFF)
        return struct.pack("<I", val & 0xFFFFFFFF)     # REL_32 / REL_32S

    def weak_value(ty: int, nm: str, add: int, here: int) -> bytes:
        """弱未定义引用解析不到时的值（`#617`）：**S = 0**。

        * `PC32` / `PLT32` / `PC64` 存的是 `S + A - P`，S=0 ⇒ `A - P`（不是 0！）。
          写 0 会让 `call` 落到下一条指令上 —— 分支一旦被走到就是死循环。
        * **GOTPCREL 族**存的是"GOT 槽相对 PC"，而**槽的内容**才是符号地址；槽由
          `got_addr` 建好并保持 0，所以这里照常写 `GOT + A - P`。第一版写成 0 位移，
          于是 `mov rax, [rip+0]` 读到了指令后面的字节 —— 非 0 ⇒ `if (weak_fn)` 为真，
          真的去 call 一个空地址（实测 SIGSEGV）。
        """
        if ty in (REL_GOTPCREL, REL_GOTPCRELX, REL_REX_GOTPCRELX):
            return struct.pack("<i", got_addr[nm] + add - here)
        if ty in (REL_PC32, REL_PLT32):
            return struct.pack("<i", add - here)
        if ty == REL_PC64:
            return struct.pack("<q", add - here)
        if ty == REL_64:
            return struct.pack("<Q", add)
        return struct.pack("<I", add & 0xFFFFFFFF)

    for i, obj in enumerate(objects):
        base = TEXT_VADDR + bases[i]
        for sec, r_off, ty, nm, add in obj.relocs:
            target = em.asm.labels.get(nm)
            site = bases[i] + obj.sec_at[sec] + r_off     # 写进缓冲的位置 (缓冲内偏移)
            here = base + obj.sec_at[sec] + r_off         # 公式里的 P (绝对地址)
            if target is None:
                # **弱引用不是错误**：C 的语义是绑 0（`#617`）。强引用才是"没人定义"。
                if nm in obj.weak_undefined:
                    raw = weak_value(ty, nm, add, here)
                    em.asm.buf[site:site + len(raw)] = raw
                    applied += 1
                    continue
                # **到这一步才判"没人定义"**: 这是多目标文件 C 库与"引用 libc"的分界线 ——
                # 前者由另一个对象提供, 后者到链接结束都空着。
                raise Unsupported(
                    f"{obj.name}: 引用了未定义的符号 {nm} —— 到链接结束都没人提供它；"
                    f"链一个导出它的 .so（--link libfoo.so），或把定义它的对象一起链进来")
            val = target + add                            # 公式里的 S + A
            # **GOTPCREL**（`#609`）：值是 GOT 槽相对 PC 的偏移，不是 S+A-P。
            if ty in (REL_GOTPCREL, REL_GOTPCRELX, REL_REX_GOTPCRELX):
                rel = got_addr[nm] + add - here
                if not -2 ** 31 <= rel < 2 ** 31:
                    raise Unsupported(f"{obj.name}: GOTPCREL 重定位溢出 ({nm})")
                em.asm.buf[site:site + 4] = struct.pack("<i", rel)
                applied += 1
                continue
            # **TLS**（`#610`）：`S + A - TP`，TP 是 TLS 块尾（variant II），所以是负值。
            if ty == REL_TPOFF32:
                em.asm.buf[site:site + 4] = struct.pack("<i", val - tls_addr)
                applied += 1
                continue
            if ty == REL_TPOFF64:
                em.asm.buf[site:site + 8] = struct.pack("<q", val - tls_addr)
                applied += 1
                continue
            raw = reloc_value(ty, val, here, f"{obj.name}: {nm}")
            em.asm.buf[site:site + len(raw)] = raw
            applied += 1
    # **落在数据节上的重定位**：算的是同一套 `S + A` / `S + A - P`，只是**写进 DATA**
    # 而不是代码缓冲（`.data.rel.ro` 里的函数指针表、指向字符串的 `const char *` 都是这一类）。
    for i, obj in enumerate(objects):
        for sec, r_off, ty, nm, add in obj.drelocs:
            target = em.asm.labels.get(nm)
            site = dbases[i] + obj.dsec_at[sec] + r_off
            here = DATA_VADDR + site
            if target is None:
                if nm in obj.weak_undefined:
                    raw = weak_value(ty, nm, add, here)
                    data[site:site + len(raw)] = raw
                    applied += 1
                    continue
                raise Unsupported(
                    f"{obj.name}: 引用了未定义的符号 {nm} —— 到链接结束都没人提供它；"
                    f"链一个导出它的 .so（--link libfoo.so），或把定义它的对象一起链进来")
            val = target + add
            raw = reloc_value(ty, val, here, f"{obj.name}: 数据节的 {nm}")
            data[site:site + len(raw)] = raw
            applied += 1
    text = em.asm.finalize() + (text_tail if dyn_info is not None else b"")

    # **文本段的容量是明写的**（`docs/253`）：数据段钉在 `TEXT_VADDR + TEXT_CAP`，
    # 所以文本一旦超过 `TEXT_CAP` 就会盖到数据段上 —— 而**盖上去不报错**，产物一启动就
    # 段错误（0 字节输出），看起来像编译器坏了。实测 2026-10-15：`main` 自己已占
    # 2 084 579 / 2 097 152（99.4%），任何一笔中等改动都会顶破。所以这里**点名拒**。
    # 自举侧 `lomelf.lomt` 同一处、同一个数（那边是 `OUT_CAP`）。
    if len(text) > TEXT_CAP:
        raise Unsupported(
            f"文本段 {len(text)} 字节超过 {TEXT_CAP} —— 链接器把数据段钉在"
            f" `TEXT_VADDR + {TEXT_CAP}`，再长就会盖住数据段，产物一跑就段错误。"
            f" 抬 `loment/tools/lomelf.lomt` 的 `OUT_CAP`、和 `tools/lomelf.py` 的"
            f" `TEXT_CAP` / `ELF_DATA_VADDR`（两个实现同一个数）")
    entry = em.asm.labels["__entry"]
    if dyn_info is not None:
        blob = build_elf_dynamic(text, bytes(data), bss, entry, tail=tail, **dyn_info)
    else:
        blob = build_elf(text, bytes(data), bss, entry)
    return blob, {
        "text": len(text), "data": len(data), "bss": bss, "entry": entry,
        "funcs": [f.name for f in funcs], "objects": [o.name for o in objects],
        "linked": appended, "relocs": applied,
        "shared": [s.soname for s in shared], "imports": imported,
        "hot": hot_metadata(
            em.hot_funcs,
            em.hot_header if hotswap else None,
            em.asm.base,
            [(site - em.asm.base, em.asm.label_addr(lbl))
             for site, lbl, _w, kind in em.asm.fixups if kind == "abs"],
        ) if collect_hot else "",
    }


#: IR 级链接：同名 `define` 出现两次是硬错；一个 `declare` 只要在同一批输入里有
#: `define`，就从文本里去掉 —— 于是调用点不再走 C ABI，而是走 Loment 自己的栈约定。
#: 这对 PE 尤其重要：PE 的 extern 调用点仍是 System V 寄存器（见 `C_ARG_REGS` 的注释）。
_IR_DEF_RE = re.compile(r"^define\b[^@\n]*@([A-Za-z0-9_.$-]+)\s*\(", re.M)
_IR_DECL_RE = re.compile(r"^declare\b[^@\n]*@([A-Za-z0-9_.$-]+)\s*\(", re.M)


def merge_ir_texts(texts: list[str], names: list[str] | None = None) -> str:
    """Merge several IR modules and resolve declarations against definitions.

    The inputs are independent modules (application, one or more Java libraries,
    `jrt`).  This is deliberately a text-level merge: the rest of the linker
    still sees one module, so both targets and both implementations keep one
    code path.  It is also what makes a `declare`-ed Java symbol a direct call
    instead of an extern C-ABI call.
    """
    names = names or [f"input#{i}" for i in range(len(texts))]
    defined: dict[str, str] = {}
    starts = 0
    for idx, (name, text) in enumerate(zip(names, texts)):
        for sym in _IR_DEF_RE.findall(text):
            if sym in defined:
                raise Unsupported(
                    f"重复定义 @{sym}: {defined[sym]} 与 {name}")
            if idx == 0 and (sym.startswith("__jl_") or sym.startswith("__jrt_")):
                # 前两个前缀属于 AOT 降级器与运行期：应用 IR 定义它们就等于
                # 冒充一个库的实现（docs/282 §6.2）。
                raise Unsupported(
                    f"应用 IR 不能定义保留前缀的符号 @{sym}（__jl_ / __jrt_ 是保留的）")
            defined[sym] = name
            if sym == "_start":
                starts += 1
    if "_start" not in defined:
        if "main" not in defined:
            raise Unsupported("没有 _start 或 main 入口（合并后的 IR 需要恰好一个）")
    elif starts != 1:
        raise Unsupported("合并后的 IR 必须恰好有一个 _start")

    out: list[str] = []
    for idx, text in enumerate(texts):
        kept: list[str] = []
        for line in text.splitlines():
            # 版次元数据属于**程序**（第一份就是应用）：库/运行期那一份带着它时丢掉，
            # 否则同名外部定义会撞（`docs/296` §1.2）。第一份照留。
            if idx > 0 and line.startswith("@loment.tur.edition = "):
                continue
            m = _IR_DECL_RE.match(line)
            if m and m.group(1) in defined:
                continue
            kept.append(line)
        out.append("\n".join(kept))
    return "\n\n".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--dump-win-shim" and len(argv) == 2:
        return dump_win_shim(argv[1])
    out, check, target = None, False, "elf"
    opt = False
    allow_ports = False
    interp = None
    hot_meta = None
    profile = "default"
    fault_report = False
    fault_heal = 0
    files: list[str] = []
    link: list[Path] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "--out"):
            i += 1
            out = argv[i]
        elif a == "--interp":
            # 目标系统的 loader 路径（`#604`）：musl / Nix / 非标准布局靠它。
            i += 1
            interp = argv[i]
        elif a == "--target":
            i += 1
            target = argv[i]
        elif a == "--opt":
            # 只有 PE 目标是这条路的入口（ELF 的 --opt 由启动器直接调 clang，docs/212 §5A）。
            opt = True
        elif a == "--check":
            check = True
        elif a == "--allow-ports":
            # 开发通道专用（`docs/222` §4 的端口门）：默认**不收**带未定义符号的外源对象。
            allow_ports = True
        elif a == "--fault-report":
            # 原生档段错误报告（`docs/367` stage 1）：产物自己说。默认关 —— 开了会改
            # 入口、加备栈与站点表，产物字节随之改变（见 docs/367 §3）。
            fault_report = True
        elif a == "--fault-heal":
            # 可恢复故障（`docs/367` §4.3）：预算 N 次；未映射页置零后从同一条指令继续。
            # N > 0 隐含 `--fault-report`（修复分支住在同一段运行期里）。
            i += 1
            try:
                fault_heal = int(argv[i], 10)
            except (IndexError, ValueError):
                print("[ERR] --fault-heal 需要一个非负整数（次数）", file=sys.stderr)
                return 2
            if fault_heal < 0:
                print("[ERR] --fault-heal 的次数不能是负数", file=sys.stderr)
                return 2
            fault_report = True
        elif a == "--link":
            i += 1
            link.append(Path(argv[i]))
        elif a == "--hot-meta":
            # 热更新的元数据（`#1149`）：帧布局哈希 + 调用点表。**不影响产物一个字节**。
            i += 1
            hot_meta = argv[i]
        elif a == "--profile":
            # 构建档（`#1150`）。default 与 hotswap 的产物**不**逐字节相同 —— 后者多槽表、
            # 多入口闸，这是它存在的理由；default 那份不受影响（判据拿 origin/main 对拍）。
            i += 1
            profile = argv[i]
        else:
            files.append(a)
        i += 1
    if (not files or target not in ("elf", "pe")
            or profile not in ("default", "hotswap")):
        print("用法: lomelf.py IN.ll [MORE.ll ...] [-o OUT] [--target elf|pe] [--check]"
              " [--link OBJ.o ...] [--allow-ports] [--interp PATH] [--hot-meta PATH]"
              " [--opt]"
              " [--profile default|hotswap] [--fault-report] [--fault-heal N]",
              file=sys.stderr)
        return 2
    if link and target == "pe":
        print("[ERR] --link 目前只支持 ELF 目标 (docs/173 §3)", file=sys.stderr)
        return 2
    if profile == "hotswap" and target != "elf":
        print("[ERR] --profile hotswap 目前只支持 ELF 目标", file=sys.stderr)
        return 2
    if opt and target != "pe":
        print("[ERR] --opt 在 lomelf 里只接 PE 目标（ELF 那条在启动器里，docs/212 §5A）",
              file=sys.stderr)
        return 2
    if fault_report and target == "pe":
        print("[ERR] --fault-report 目前只支持 ELF 目标（docs/367 stage 4 才做 PE）",
              file=sys.stderr)
        return 2
    src = Path(files[0])
    try:
        texts = [Path(f).read_text(encoding="utf-8") for f in files]
        text = texts[0] if len(texts) == 1 else merge_ir_texts(texts, files)
        objs = [load_foreign(p) for p in link]
        if target == "pe":
            blob, info = (compile_pe_opt(text) if opt else compile_pe(text))
        else:
            blob, info = compile_ll(text, objs, allow_ports, interp,
                                    hotswap=(profile == "hotswap"),
                                    fault_report=fault_report,
                                    fault_heal=fault_heal)
    except Unsupported as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    except ElfError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    if check:
        print(f"[OK] {src} 可编 (text {info['text']} B, data {info['data']} B, bss {info['bss']} B)")
        return 0
    if hot_meta:
        if target != "elf":
            print("[ERR] --hot-meta 目前只支持 ELF 目标", file=sys.stderr)
            return 2
        try:
            Path(hot_meta).write_text(info["hot"], encoding="utf-8", newline="\n")
        except OSError as e:
            print(f"[ERR] 写不了 {hot_meta}: {e.strerror or e}", file=sys.stderr)
            return 1
    if out is None:
        out = str(src.with_suffix(".exe" if target == "pe" else ""))
    # **写不出去要说话，而且要说清是哪个路径**（`issue #8`）。这里原先是一句**裸写**：
    # 目录不存在就是没接住的 `OSError`，用户拿到 Python 栈 —— 而"父目录没建"这条
    # 看一眼路径就明白。自举侧那一半（`loment/tools/lomelf.lomt`）同一条口径，码是 E029。
    try:
        Path(out).write_bytes(blob)
        os.chmod(out, 0o755)
    except OSError as e:
        print(f"[ERR] 写不了输出文件 {out}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"[OK] {src} -> {out} ({len(blob)} B, text {info['text']} B, data {info['data']} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
