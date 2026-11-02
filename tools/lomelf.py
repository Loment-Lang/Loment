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
br/switch/phi；直接 call（<= 6 实参）；内联汇编 syscall；atomicrmw add；ret/unreachable。
**没有**：浮点、bitcast、select、间接调用、闭包、聚合返回值、变参、结构体 GEP。
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import sys
from pathlib import Path

#: **文本段的容量**（`docs/253`，2026-10-16）。与自举侧 `lomelf.lomt` 的 `OUT_CAP`
#: **同一个数** —— 那边就是代码缓冲的大小。数据段钉在它后面（见 `ELF_DATA_VADDR`），
#: 所以这个数一旦被文本超过，数据段就会被压住（**不报错**，产物一启动就段错误）。
#: 越过它的情形由 `compile_ll` 末尾那条判据点名拒 —— 原先没有这条，地板是**没标注的**。
TEXT_CAP = 4194304                 # 4 MiB = 0x400000
#: 热更新档（`#1150`）：槽表排在文本段最前面，每槽一条 `jmp rel32` + NOP 填到定长。
#: 槽号按**函数名排序**分配，所以同一份 IR 每一次都落在同一个地址；函数集合一变，
#: 表形就变（准入侧拿 `slots` 与 `tramp` 逐条比，见 `#1152`）。
HOT_STRIDE = 16
ELF_TEXT_VADDR = 0x400000
#: 数据段在**代码缓冲之后**：文本段可用 `0x400000..0x800000`，正好是 `TEXT_CAP`。
#: 原先钉 `0x600000`（2 MiB 窗口），实测 2026-10-15 `main` 自己已占 99.4%。
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
        r"define\s+(?:(?:internal|private)\s+)?(.+?)\s+(@[-A-Za-z0-9_.]+)\s*\((.*?)\)"
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

    def finalize(self) -> bytes:
        for site, lbl, width, kind in self.fixups:
            if lbl not in self.labels:
                raise ElfError(f"未定义的标签: {lbl}")
            if kind == "abs":
                struct.pack_into("<Q", self.buf, site - self.base, self.labels[lbl])
            else:
                struct.pack_into("<i", self.buf, site - self.base,
                                 self.labels[lbl] - (site + width))
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
        for p in parts[1:]:
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
                    raise Unsupported("v0 只支持常量下标的结构体 GEP（动态下标请用 extractvalue）")
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
        if callee.startswith("%"):
            raise Unsupported("v0 不支持间接调用")
        args = [parse_operand(x) for x in split_top(argstr)] if argstr.strip() else []
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
        """入口闸（`#1150`）：读全局 epoch，非零就走候选那条路（真正的换在 `#1151`）。"""
        start = self.asm.here()
        self.hot_header["gate"] = start - TEXT_VADDR
        self.asm.label("__loment_hot_gate")
        self.asm.emit(b"\x55")                  # push rbp
        self.asm.emit(b"\x48\x89\xE5")          # mov rbp, rsp
        self.asm.mov_abs(RAX, "__loment_hot_epoch")
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
        for blk in f.blocks:
            self.cur_block = blk.label
            self.asm.label(self.blk(blk.label))
            for ins in blk.instrs:
                self.lower(ins)
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
PE_IMPORTS = ["ExitProcess", "GetStdHandle", "ReadFile", "WriteFile", "CloseHandle",
              "CreateFileA", "GetFileAttributesA", "VirtualAlloc", "GetCommandLineA",
              "FindFirstFileA", "FindNextFileA", "Sleep"]
# 第二个导入 DLL：联网。名字与上一组无一重名，所以 `slots` 仍是一张按函数名索引的平表。
PE_IMPORTS_WS2 = ["WSAStartup", "socket", "connect", "bind", "listen", "accept",
                  "send", "recv", "sendto", "recvfrom", "closesocket", "shutdown",
                  "setsockopt", "getsockopt", "getpeername", "getsockname",
                  "ioctlsocket", "select", "WSAGetLastError"]
# 描述符表 = 每个 DLL 一条 + 一条全零终止项（少了终止项加载器会把 ILT 当第二条描述符）。
PE_DLLS = [("kernel32.dll", PE_IMPORTS), ("ws2_32.dll", PE_IMPORTS_WS2)]
PE_IMPORT_DESC_BYTES = 20 * (len(PE_DLLS) + 1)
PE_STD_INPUT, PE_STD_OUTPUT, PE_STD_ERROR = -10, -11, -12
SYS_READ, SYS_WRITE, SYS_CLOSE, SYS_BRK, SYS_EXIT = 0, 1, 3, 12, 60
SYS_GETDENTS64, SYS_OPENAT, SYS_NEWFSTATAT = 217, 257, 262
SYS_POLL, SYS_SELECT = 7, 23
SYS_SOCKET, SYS_CONNECT, SYS_ACCEPT, SYS_ACCEPT4 = 41, 42, 43, 288
SYS_SENDTO, SYS_RECVFROM, SYS_SHUTDOWN = 44, 45, 48
SYS_BIND, SYS_LISTEN, SYS_GETSOCKNAME, SYS_GETPEERNAME = 49, 50, 51, 52
SYS_SETSOCKOPT, SYS_GETSOCKOPT = 54, 55
SYS_SENDMSG, SYS_RECVMSG, SYS_SOCKETPAIR = 46, 47, 53
SYS_EPOLL_WAIT, SYS_EPOLL_CTL, SYS_EPOLL_CREATE1 = 232, 233, 291
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
WS_SIZE = WS_NETTMP + WS_NETTMP_CAP
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

    out = bytearray(max(off - d, PE_LIT_OFF + len(PE_CMDLINE)))
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
    return bytes(out), slots


def _call_iat(asm: Asm, addr: int) -> None:
    """call qword ptr [addr]。地址是**编译期常量**（IAT 在固定 RVA），所以不需要回填。"""
    asm.emit(b"\x48\xA1" + struct.pack("<Q", addr))   # mov rax, [abs64]
    asm.emit(b"\xFF\xD0")                             # call rax


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
    sub_rsp(0x80)                               # 32 shadow + 第五~七参 + 各 case 的暂存
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
    movi32(RDX, 0x80000000)                     # GENERIC_READ
    ld(R11, RSP, 0x30)                          # flags
    a.emit(b"\x41\xF6\xC3\x01")                 # test r11b, O_WRONLY
    jcc_l("e", "__ws_o_acc")
    movi32(RDX, 0x40000000)                     # GENERIC_WRITE
    a.label("__ws_o_acc")
    movi32(R8, 3)                               # FILE_SHARE_READ|WRITE
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d
    movi32(RAX, 3)                              # OPEN_EXISTING
    a.emit(b"\x41\xF6\xC3\x40")                 # test r11b, O_CREAT
    jcc_l("e", "__ws_o_disp")
    movi32(RAX, 2)                              # CREATE_ALWAYS
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
    a.label("__ws_fstatat")
    st(RSP, 0x28, RDX)                          # stb
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
    api("GetFileAttributesA")
    a.emit(b"\x83\xF8\xFF")
    jcc_l("e", "__ws_fail")
    ld(RDX, RSP, 0x28)
    a.emit(b"\xA8\x10")                         # test al, FILE_ATTRIBUTE_DIRECTORY
    movi32(RCX, 0x81ED)                         # S_IFREG | 0755
    jcc_l("e", "__ws_fs_reg")
    movi32(RCX, 0x41ED)                         # S_IFDIR | 0755
    a.label("__ws_fs_reg")
    st32(RDX, 24, RCX)                          # st_mode
    a.emit(b"\x31\xC0")
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

    # （cmdline 字面量在 .idata 的固定偏移 PE_LIT_OFF 上，shim 用常量取址，不必内嵌）


class PeEmitter(Emitter):
    """把 syscall 改成走 shim、把入口桩改成 Windows 形态，其余降级与 ELF 目标共用。"""

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
    struct.pack_into("<Q", opt, 80, 0x100000)
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


def _pe_emit(funcs, globals_, slots) -> tuple[bytes, int]:
    em = PeEmitter(globals_)
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    em.emit_entry_stub("_start")
    emit_win_shim(em, slots)
    for f in funcs:
        em.emit_func(f)
    return em.asm.finalize(), em.asm.labels["__entry"]


def compile_pe(text: str) -> tuple[bytes, dict]:
    global TEXT_VADDR
    globals_, funcs, _externs = parse_ll(text)
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（PE 产物需要一个用户态入口）")

    # 四个节的 RVA 全是常量，所以**不需要不动点**：发射一遍就完事。
    # （早先按"代码长度决定 .data 的 RVA"排过一次，那是为了迁就 .text 只能钉在最低 RVA；
    #   现在每节都钉死了，两遍发射连同它引发的节区重叠问题一起消失。）
    TEXT_VADDR = PE_IMAGE_BASE + PE_TEXT_RVA
    data, bss = _pe_layout_globals(globals_, PE_DATA_RVA)
    idata, slots = build_pe_idata()
    code, entry = _pe_emit(funcs, globals_, slots)

    blob = build_pe(code, bytes(data), entry - PE_IMAGE_BASE, idata)
    return blob, {"text": len(code), "data": len(data), "bss": bss,
                  "entry": entry - PE_IMAGE_BASE, "funcs": [f.name for f in funcs]}


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


def compile_ll(text: str, objects: list | None = None,
               allow_ports: bool = False, interp: str | None = None,
               collect_hot: bool = True,
               hotswap: bool = False) -> tuple[bytes, dict]:
    objects = objects or []
    global TEXT_VADDR, DATA_VADDR
    TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # PE 目标会改这两个全局
    globals_, funcs, externs = parse_ll(text)
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
    if hotswap:
        # 入口闸读的那个 epoch 字；候选在等时非零（`#1151` 写它）。
        em.asm.labels["__loment_hot_epoch"] = DATA_VADDR + len(data) + bss
        bss += 8
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
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（原生产物 v0 需要一个用户态入口）")
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
    em.emit_entry_stub("_start", tls=has_foreign, ctors=len(ctor_ranges),
                       finis=len(fini_ranges), call_exit=call_exit)
    for f in funcs:
        em.emit_func(f)
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
            [(site - em.asm.base, em.asm.labels[lbl])
             for site, lbl, _w, kind in em.asm.fixups if kind == "abs"],
        ) if collect_hot else "",
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--dump-win-shim" and len(argv) == 2:
        return dump_win_shim(argv[1])
    out, check, target = None, False, "elf"
    allow_ports = False
    interp = None
    hot_meta = None
    profile = "default"
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
        elif a == "--check":
            check = True
        elif a == "--allow-ports":
            # 开发通道专用（`docs/222` §4 的端口门）：默认**不收**带未定义符号的外源对象。
            allow_ports = True
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
    if (len(files) != 1 or target not in ("elf", "pe")
            or profile not in ("default", "hotswap")):
        print("用法: lomelf.py IN.ll [-o OUT] [--target elf|pe] [--check]"
              " [--link OBJ.o ...] [--allow-ports] [--interp PATH] [--hot-meta PATH]"
              " [--profile default|hotswap]",
              file=sys.stderr)
        return 2
    if link and target == "pe":
        print("[ERR] --link 目前只支持 ELF 目标 (docs/173 §3)", file=sys.stderr)
        return 2
    if profile == "hotswap" and target != "elf":
        print("[ERR] --profile hotswap 目前只支持 ELF 目标", file=sys.stderr)
        return 2
    src = Path(files[0])
    try:
        objs = [load_foreign(p) for p in link]
        if target == "pe":
            blob, info = compile_pe(src.read_text(encoding="utf-8"))
        else:
            blob, info = compile_ll(src.read_text(encoding="utf-8"), objs,
                                    allow_ports, interp,
                                    hotswap=(profile == "hotswap"))
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
