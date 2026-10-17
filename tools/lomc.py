#!/usr/bin/env python3
# lomc.py — L0 接口层编译器 (.lom → Rust / C / Python / JSON)
#
# 设计约束 (docs/140 §4/§5, docs/141):
#   1. 原生语法取已知语言的风味 (Rust 风味), 不发明新语法 → 规避 LLM 零语料;
#   2. 输出确定性: 无时间戳, 声明序稳定, LF 行尾 → 可逐字节 --check;
#   3. 语义检查前置: 重叠/越界/重复在生成期报错, 而不是运行期才发现;
#   4. 单一真源: 生成物禁止手改, 漂移由 --check 在 CI 中归零。
#
# 用法:
#   python tools/lomc.py lom/fuai.lom --emit-rust lom/build/fuai.rs --emit-c ...
#   python tools/lomc.py lom/fuai.lom --check        # 与磁盘产物对账, 有漂移退出 1
#   python tools/lomc.py lom/fuai.lom --print json   # 打到 stdout
#
# 退出码: 0 = 一致 / 1 = 差异或语义错误 / 2 = 用法或异常。

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 类型表

# .lom 类型 -> (python struct code, 宽度字节, rust 类型, c 类型)
TYPES: dict[str, tuple[str, int, str, str]] = {
    "u8": ("B", 1, "u8", "uint8_t"),
    "u16": ("H", 2, "u16", "uint16_t"),
    "u32": ("I", 4, "u32", "uint32_t"),
    "u64": ("Q", 8, "u64", "uint64_t"),
    "i8": ("b", 1, "i8", "int8_t"),
    "i16": ("h", 2, "i16", "int16_t"),
    "i32": ("i", 4, "i32", "int32_t"),
    "i64": ("q", 8, "i64", "int64_t"),
}

RUST_WIDE = {"u64": "u64", "i64": "i64"}  # 其余按声明宽度取


class LomError(Exception):
    """带**位置**的编译错误。

    `line <= 0` = **这一档指不出位置**（例如"这份源被前门拒了"，那句话里已经有
    "第 N 行"）：这时**不编一个位置**，消息原样输出 —— 编一个 `1:1:` 是撒谎，
    而"错要指在错的地方"这条纪律的反面就是"指到假的地方去"。
    """

    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"{line}:{col}: {msg}" if line > 0 else msg)
        self.line, self.col, self.msg = line, col, msg


# ---------------------------------------------------------------- 词法

@dataclass
class Tok:
    kind: str  # ident | number | string | punct | eof
    val: str
    line: int
    col: int
    #: 这个 token 在**源文本**里的起点与长度（`docs/184` §3.2 的 20 字节记录要用它们）。
    #:
    #: **`val` 与 `off`/`len` 不是一回事**：`val` 是**解码后**的文本（字符串字面量的
    #: 转义已经消掉、引号已经剥掉），而 `off`/`len` 圈的是**源里的原始片段**（含引号）。
    #: 宏体要的是后者 —— 照抄进输出流时，"文本 + 位置"才都是原来那一份（`docs/184` §4
    #: 那条"位置是构造出来的"）。自举侧的 token 记录本来就是 `(kind, off, len, line, col)`
    #: 这个形状，所以这里补上之后，两边的 token 记录**逐字段同序**。
    off: int = 0
    len: int = 0


_PUNCT = "{}()[]:;=@,.+-*/%<>!&|^?"
#: 字符串转义表。**2026-10-16 补上 `\r` 与 `\0`**（`#33` / `#36`）：
#: `\r` 原先不在表里，于是走"未知转义"那条兜底，**反斜杠被吃掉**、值变成字母 `r` —— 用户写
#: 回车得到一个 r，而且编译器什么都不说。`\0` 仓库自己在用（`loment/lib/proc.lomt` 的
#: `"/bin/sh\0"`），却一直靠同一条静默兜底在跑。
#:
#: 表外的转义**是错误**（见 `lex` 里那个 `raise`），不再"取原字符" —— 那条兜底正是这两个
#: issue 的共同成因：`"\x41"` 曾经静默变成 `x41` 三个字符。
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}


def _starts_ext_block(toks: list, depth: int, declared: set) -> bool:
    """这个 `{` 是不是**外部代码块**的开头（`docs/185` §3）。

    两种写法：

    * `let IDENT {` —— **总认**。今天 `let` 只接 `IDENT :` / `IDENT =`，`let x { … }`
      根本解析不过，所以这个产生式是新加的、不撞任何既有语法；
    * `IDENT {` —— 只在 `IDENT` 是**本文件声明过的语言名**（`command` / `foruse`）**且**
      在顶层时认。`struct S {` / `fn f() -> u32 {` 的 `{` 前面也是标识符，靠"必须是语言名"
      把它们排除掉；顶层那一条挡的是 `if c { }`（`c` 既是语言名又是变量时）。
    """
    if len(toks) >= 2 and toks[-1].kind == "ident" and toks[-2].kind == "ident" \
            and toks[-2].val == "let":
        return True
    return (depth == 0 and bool(toks) and toks[-1].kind == "ident"
            and toks[-1].val in declared)


def lex(text: str) -> list[Tok]:
    toks: list[Tok] = []
    i, line, col, n = 0, 1, 1, len(text)
    #: 花括号深度 —— 只用来判"这个 `IDENT {` 在不在顶层"（`docs/185` §3）。
    depth = 0
    #: 本文件**声明过**的语言名（`command c` / `foruse py`）。不带 `let` 的外部代码块
    #: 靠它消歧：`struct S {` 的 `S` 与 `fn f() -> u32 {` 的 `u32` 也是"标识符后面跟 `{`"，
    #: 只有"声明过的语言名"能把它们排除掉（`docs/185` §3）。
    declared: set[str] = set()
    #: 刚发过 `command` / `foruse` —— 下一个标识符是语言名。
    want_lang = False
    while i < n:
        c = text[i]
        if c == "\n":
            line, col, i = line + 1, 1, i + 1
            continue
        if c in " \t\r":
            i, col = i + 1, col + 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise LomError(line, col, "未闭合的块注释")
            line += text[i:j].count("\n")
            i = j + 2
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    c2 = text[j + 1]
                    if c2 not in _ESCAPES:
                        raise LomError(line, col + (j - i),
                                       f"未知转义 \\{c2} —— 只认 \\n \\t \\r \\0 \\\\ \\\"")
                    buf.append(_ESCAPES[c2])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise LomError(line, col, "未闭合的字符串")
            toks.append(Tok("string", "".join(buf), line, col, i, j + 1 - i))
            i, col = j + 1, col + (j + 1 - i)
            continue
        m = re.match(r"0[xX][0-9A-Fa-f]+|\d+", text[i:])
        if m:
            toks.append(Tok("number", m.group(0), line, col, i, m.end()))
            i, col = i + m.end(), col + m.end()
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i:])
        if m:
            name = m.group(0)
            if want_lang:
                declared.add(name)      # `command c` / `foruse py`
                want_lang = False
            elif depth == 0 and name in ("command", "foruse"):
                want_lang = True
            toks.append(Tok("ident", name, line, col, i, m.end()))
            i, col = i + m.end(), col + m.end()
            continue
        if c in _PUNCT:
            if c == "{" and _starts_ext_block(toks, depth, declared):
                start = i + 1
                j, d = start, 1
                while j < n and d > 0:
                    if text[j] == "{":
                        d += 1
                    elif text[j] == "}":
                        d -= 1
                    j += 1
                if d != 0:
                    raise LomError(line, col, "外部代码块没闭合（少一个 `}`）")
                body = text[start:j - 1]
                toks.append(Tok("raw", body, line, col + 1, start, j - 1 - start))
                line += body.count("\n")
                col = (len(body) - body.rfind("\n")) if "\n" in body else col + 1 + len(body)
                i = j
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth = max(0, depth - 1)
            toks.append(Tok("punct", c, line, col, i, 1))
            i, col = i + 1, col + 1
            continue
        raise LomError(line, col, f"非法字符 {c!r}")
    # eof 的 off/len 都指"末尾" —— 0 长度会与"空 token"混淆，所以 off 落在源尾、len 为 0，
    # 而**任何真 token 的 off 都严格小于它**（判"到没到尾"靠 kind，不靠这个）。
    toks.append(Tok("eof", "", line, col, n, 0))
    return toks


# ---------------------------------------------------------------- 语法树

@dataclass
class Field:
    name: str
    type: str
    offset: int
    line: int


@dataclass
class Record:
    name: str
    size: int
    packed: bool
    endian: str
    fields: list[Field]
    line: int


@dataclass
class Variant:
    name: str
    value: int
    meta: dict[str, str]
    line: int


@dataclass
class Enum:
    name: str
    base: str
    variants: list[Variant]
    line: int


@dataclass
class Const:
    name: str
    type: str
    value: int
    line: int


@dataclass
class Param:
    name: str
    values: dict[str, int]
    line: int
    note: str = ""


@dataclass
class Meta:
    entries: dict[str, object]  # str | int | 嵌套 dict[str, object]
    line: int


@dataclass
class Module:
    name: str
    enums: list[Enum] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    consts: list[Const] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    meta: Meta | None = None


# ---------------------------------------------------------------- 语法分析

class Parser:
    def __init__(self, toks: list[Tok], src: str):
        self.toks, self.src, self.i = toks, src, 0

    # -- 基础
    def peek(self, k: int = 0) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind: str, val: str | None = None) -> bool:
        t = self.peek()
        return t.kind == kind and (val is None or t.val == val)

    def accept(self, kind: str, val: str | None = None) -> Tok | None:
        return self.next() if self.at(kind, val) else None

    def expect(self, kind: str, val: str | None = None, what: str = "") -> Tok:
        if not self.at(kind, val):
            t = self.peek()
            want = val if val is not None else kind
            got = t.val or "<eof>"
            raise LomError(t.line, t.col, f"期望 {want}{what}，得到 {got!r}")
        return self.next()

    def ident(self, what: str = "标识符") -> str:
        return self.expect("ident", None, f"（{what}）").val

    def int_lit(self, what: str = "整数") -> int:
        neg = bool(self.accept("punct", "-"))
        t = self.expect("number", None, f"（{what}）")
        v = int(t.val, 16) if t.val[:2].lower() == "0x" else int(t.val, 10)
        return -v if neg else v

    def string_lit(self, what: str = "字符串") -> str:
        return self.expect("string", None, f"（{what}）").val

    # -- 顶层
    def parse(self) -> Module:
        self.expect("ident", "module", "（文件必须以 module 开头）")
        mod = Module(self.ident("模块名"))
        seen = False
        while not self.at("eof"):
            t = self.peek()
            if t.kind != "ident":
                raise LomError(t.line, t.col, f"顶层只允许 enum/record/const/param，得到 {t.val!r}")
            if t.val == "enum":
                mod.enums.append(self.parse_enum())
            elif t.val == "record":
                mod.records.append(self.parse_record())
            elif t.val == "const":
                mod.consts.append(self.parse_const())
            elif t.val == "param":
                mod.params.append(self.parse_param())
            elif t.val == "meta":
                if mod.meta is not None:
                    raise LomError(t.line, t.col, "meta 块重复")
                mod.meta = self.parse_meta()
            else:
                raise LomError(t.line, t.col, f"未知顶层关键字 {t.val!r}")
            seen = True
            self.accept("punct", ";")  # 可选分隔
        if not seen:
            raise LomError(1, 1, "模块为空")
        return mod

    def parse_enum(self) -> Enum:
        kw = self.expect("ident", "enum")
        name = self.ident("枚举名")
        self.expect("punct", ":")
        base = self.ident("整型基类型")
        if base not in TYPES:
            raise LomError(kw.line, kw.col, f"未知类型 {base!r}（可用: {', '.join(TYPES)}）")
        self.expect("punct", "{")
        variants: list[Variant] = []
        while not self.at("punct", "}"):
            vname = self.ident("枚举成员名")
            self.expect("punct", "=")
            vline = self.peek().line
            val = self.int_lit("枚举值")
            meta: dict[str, str] = {}
            if self.accept("punct", "{"):
                while not self.at("punct", "}"):
                    k = self.ident("元数据键")
                    self.expect("punct", "=")
                    if self.at("string"):
                        meta[k] = self.string_lit(f"{k} 的值")
                    else:
                        meta[k] = str(self.int_lit(f"{k} 的值"))
                    self.accept("punct", ",")
                    self.accept("punct", ";")
                self.expect("punct", "}")
            variants.append(Variant(vname, val, meta, vline))
            self.accept("punct", ",")
            self.accept("punct", ";")
        self.expect("punct", "}")
        return Enum(name, base, variants, kw.line)

    def parse_record(self) -> Record:
        kw = self.expect("ident", "record")
        name = self.ident("记录名")
        self.expect("ident", "layout", "（record 必须带 layout(...)）")
        self.expect("punct", "(")
        packed, size, endian = False, -1, "little"
        while not self.at("punct", ")"):
            k = self.ident("layout 选项")
            if k == "packed":
                packed = True
            else:
                self.expect("punct", "=")
                if k == "size":
                    size = self.int_lit("记录大小")
                elif k == "endian":
                    endian = self.ident("字节序")
                    if endian not in ("little", "big"):
                        raise LomError(kw.line, kw.col, f"未知字节序 {endian!r}")
                else:
                    raise LomError(kw.line, kw.col, f"未知 layout 选项 {k!r}")
            self.accept("punct", ",")
        self.expect("punct", ")")
        if size < 0:
            raise LomError(kw.line, kw.col, f"record {name} 缺少 size=")
        self.expect("punct", "{")
        fields: list[Field] = []
        while not self.at("punct", "}"):
            fname = self.ident("字段名")
            self.expect("punct", ":")
            ftype = self.ident("字段类型")
            if ftype not in TYPES:
                raise LomError(kw.line, kw.col, f"未知字段类型 {ftype!r}")
            self.expect("punct", "@")
            foff = self.int_lit("字段偏移")
            fields.append(Field(fname, ftype, foff, kw.line))
            self.accept("punct", ",")
            self.accept("punct", ";")
        self.expect("punct", "}")
        return Record(name, size, packed, endian, fields, kw.line)

    def parse_const(self) -> Const:
        kw = self.expect("ident", "const")
        name = self.ident("常量名")
        self.expect("punct", ":")
        ctype = self.ident("常量类型")
        if ctype not in TYPES:
            raise LomError(kw.line, kw.col, f"未知类型 {ctype!r}")
        self.expect("punct", "=")
        return Const(name, ctype, self.int_lit("常量值"), kw.line)

    def parse_param(self) -> Param:
        kw = self.expect("ident", "param")
        name = self.ident("参数名")
        self.expect("punct", "{")
        vals: dict[str, int] = {}
        note = ""
        while not self.at("punct", "}"):
            impl = self.ident("实现名")
            self.expect("punct", "=")
            if impl == "note":
                note = self.string_lit("note")
            else:
                vals[impl] = self.int_lit(f"{impl} 的取值")
            self.accept("punct", ",")
            self.accept("punct", ";")
        self.expect("punct", "}")
        if not vals:
            raise LomError(kw.line, kw.col, f"param {name} 为空")
        return Param(name, vals, kw.line, note)

    def parse_meta(self) -> Meta:
        kw = self.expect("ident", "meta")
        self.expect("punct", "{")
        return Meta(self.parse_meta_body(), kw.line)

    def parse_meta_body(self) -> dict[str, object]:
        """meta 体: key = "str" | key = 1 | key { ... } — 文档级元数据的嵌套映射。"""
        out: dict[str, object] = {}
        while not self.at("punct", "}"):
            t = self.peek()
            k = self.string_lit("meta 键") if t.kind == "string" else self.ident("meta 键")
            if k in out:
                raise LomError(t.line, t.col, f"meta 键 {k} 重复")
            if self.at("punct", "{"):
                self.next()
                out[k] = self.parse_meta_body()
            else:
                self.expect("punct", "=")
                if self.at("string"):
                    out[k] = self.string_lit(f"{k} 的值")
                else:
                    out[k] = self.int_lit(f"{k} 的值")
            self.accept("punct", ",")
            self.accept("punct", ";")
        self.expect("punct", "}")
        return out


# ---------------------------------------------------------------- 语义检查

def type_range(t: str) -> tuple[int, int]:
    bits = int(t[1:])
    if t[0] == "u":
        return 0, (1 << bits) - 1
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


def check(mod: Module) -> list[str]:
    """返回错误列表; 空列表 = 通过。错误在生成期拦截, 不留给运行期。"""
    errs: list[str] = []
    names: dict[str, str] = {}

    def declare(name: str, kind: str, line: int) -> None:
        if name in names:
            errs.append(f"{line}: 名字 {name} 重复（已作为 {names[name]} 声明）")
        else:
            names[name] = kind

    for e in mod.enums:
        declare(e.name, "enum", e.line)
        lo, hi = type_range(e.base)
        seen_val: dict[int, str] = {}
        seen_name: set[str] = set()
        for v in e.variants:
            if v.name in seen_name:
                errs.append(f"{v.line}: 枚举 {e.name} 成员 {v.name} 重复")
            seen_name.add(v.name)
            if not (lo <= v.value <= hi):
                errs.append(f"{v.line}: {e.name}.{v.name} = {v.value} 超出 {e.base} 范围 [{lo}, {hi}]")
            if v.value in seen_val:
                errs.append(
                    f"{v.line}: {e.name}.{v.name} 与 .{seen_val[v.value]} 同值 {v.value}（枚举值必须唯一）"
                )
            else:
                seen_val[v.value] = v.name
        if not e.variants:
            errs.append(f"{e.line}: 枚举 {e.name} 为空")

    for r in mod.records:
        declare(r.name, "record", r.line)
        if r.size <= 0:
            errs.append(f"{r.line}: record {r.name} size 必须为正")
        seen_f: set[str] = set()
        occupied: list[tuple[int, int, str]] = []
        for f in r.fields:
            if f.name in seen_f:
                errs.append(f"{f.line}: record {r.name} 字段 {f.name} 重复")
            seen_f.add(f.name)
            w = TYPES[f.type][1]
            if f.offset < 0:
                errs.append(f"{f.line}: {r.name}.{f.name} 偏移为负")
            if f.offset + w > r.size:
                errs.append(
                    f"{f.line}: {r.name}.{f.name} @{f.offset}+{w} 越过 size={r.size}"
                )
            occupied.append((f.offset, f.offset + w, f.name))
        occupied.sort()
        for (o1, e1, n1), (o2, e2, n2) in zip(occupied, occupied[1:]):
            if o2 < e1:
                errs.append(
                    f"{r.line}: record {r.name} 字段 {n1}@{o1}..{e1} 与 {n2}@{o2}..{e2} 重叠"
                )
        if occupied and occupied[-1][1] < r.size and not r.packed:
            # 尾部留白合法 (对齐填充), 仅提示不报错
            pass

    for c in mod.consts:
        declare(c.name, "const", c.line)
        lo, hi = type_range(c.type)
        if not (lo <= c.value <= hi):
            errs.append(f"{c.line}: const {c.name} = {c.value} 超出 {c.type} 范围 [{lo}, {hi}]")

    for p in mod.params:
        declare(p.name, "param", p.line)
        for impl, val in p.values.items():
            lo, hi = type_range("i64")
            if not (lo <= val <= hi):
                errs.append(f"{p.line}: param {p.name}.{impl} 超出 i64 范围")
    return errs


# ---------------------------------------------------------------- 生成器

GEN_BANNER = "由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。"


def _hex(v: int, base: str) -> str:
    bits = int(base[1:])
    width = bits // 4
    return f"0x{v:0{width}X}" if v >= 0 else str(v)


def emit_rust(mod: Module) -> str:
    out = [f"// {GEN_BANNER}", f"// module {mod.name}", ""]
    for c in mod.consts:
        out.append(f"pub const {c.name}: {c.type} = {_hex(c.value, c.type)};")
    if mod.consts:
        out.append("")
    for p in mod.params:
        for impl, val in p.values.items():
            out.append(f"pub const {p.name.upper()}_{impl.upper()}: i64 = {val};")
        out.append("")
    for e in mod.enums:
        out.append(f"// enum {e.name}: {e.base} ({len(e.variants)} 项)")
        for v in e.variants:
            out.append(f"pub const {e.name.upper()}_{v.name.upper()}: {e.base} = {_hex(v.value, e.base)};")
        out.append(f"pub const {e.name.upper()}_COUNT: usize = {len(e.variants)};")
        out.append("")
    for r in mod.records:
        out.append(f"// record {r.name}: size={r.size} endian={r.endian} packed={str(r.packed).lower()}")
        out.append(f"pub const {r.name.upper()}_SIZE: usize = {r.size};")
        for f in sorted(r.fields, key=lambda x: x.offset):
            out.append(f"pub const {r.name.upper()}_{f.name.upper()}_OFF: usize = {f.offset};")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_c(mod: Module) -> str:
    out = [f"/* {GEN_BANNER} */", f"/* module {mod.name} */", "#ifndef LOM_GEN_H", "#define LOM_GEN_H", ""]
    out.append("#include <stdint.h>")
    out.append("")
    for c in mod.consts:
        out.append(f"#define {c.name} (({TYPES[c.type][3]}){_hex(c.value, c.type)})")
    if mod.consts:
        out.append("")
    for p in mod.params:
        for impl, val in p.values.items():
            out.append(f"#define {p.name.upper()}_{impl.upper()} ({val})")
        out.append("")
    for e in mod.enums:
        out.append(f"/* enum {e.name}: {e.base} ({len(e.variants)} 项) */")
        for v in e.variants:
            out.append(f"#define {e.name.upper()}_{v.name.upper()} (({TYPES[e.base][3]}){_hex(v.value, e.base)})")
        out.append(f"#define {e.name.upper()}_COUNT ({len(e.variants)}u)")
        out.append("")
    for r in mod.records:
        out.append(f"/* record {r.name}: size={r.size} endian={r.endian} packed={str(r.packed).lower()} */")
        out.append(f"#define {r.name.upper()}_SIZE ({r.size}u)")
        for f in sorted(r.fields, key=lambda x: x.offset):
            out.append(f"#define {r.name.upper()}_{f.name.upper()}_OFF ({f.offset}u)")
        out.append("")
    out += ["#endif /* LOM_GEN_H */"]
    return "\n".join(out).rstrip() + "\n"


def _py_struct_format(r: Record) -> str:
    """按偏移拼 struct 格式串, 空档用 x 填充 — 布局被完整捕获才算通过。"""
    fmt = "<" if r.endian == "little" else ">"
    cur = 0
    for f in sorted(r.fields, key=lambda x: x.offset):
        if f.offset > cur:
            fmt += f"{f.offset - cur}x"
        fmt += TYPES[f.type][0]
        cur = f.offset + TYPES[f.type][1]
    if cur < r.size:
        fmt += f"{r.size - cur}x"
    return fmt


def emit_python(mod: Module) -> str:
    out = [
        "#!/usr/bin/env python3",
        f"# {GEN_BANNER}",
        f"# module {mod.name}",
        "",
        "import struct as _struct",
        "",
    ]
    for c in mod.consts:
        out.append(f"{c.name} = {_hex(c.value, c.type)}")
    if mod.consts:
        out.append("")
    for p in mod.params:
        for impl, val in p.values.items():
            out.append(f"{p.name.upper()}_{impl.upper()} = {val}")
        out.append("")
    for e in mod.enums:
        out.append(f"# enum {e.name}: {e.base} ({len(e.variants)} 项)")
        for v in e.variants:
            out.append(f"{e.name.upper()}_{v.name.upper()} = {_hex(v.value, e.base)}")
        out.append(f"{e.name.upper()}_COUNT = {len(e.variants)}")
        out.append("")
    for r in mod.records:
        fmt = _py_struct_format(r)
        size = struct.calcsize(fmt)
        out.append(f"# record {r.name}: size={r.size} endian={r.endian}")
        out.append(f"{r.name.upper()}_SIZE = {r.size}")
        out.append(f"{r.name.upper()}_FMT = {fmt!r}")
        out.append(f"{r.name.upper()}_STRUCT = _struct.Struct({r.name.upper()}_FMT)")
        assert size == r.size, f"{r.name}: 生成格式串 {fmt!r} 算得 {size}B, 声明 {r.size}B"
        for f in sorted(r.fields, key=lambda x: x.offset):
            out.append(f"{r.name.upper()}_{f.name.upper()}_OFF = {f.offset}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def emit_json(mod: Module) -> str:
    doc = {
        "lom": "v0",
        "module": mod.name,
        "meta": mod.meta.entries if mod.meta else {},
        "consts": [{"name": c.name, "type": c.type, "value": c.value} for c in mod.consts],
        "params": [
            {"name": p.name, "values": p.values, **({"note": p.note} if p.note else {})}
            for p in mod.params
        ],
        "enums": [
            {
                "name": e.name,
                "base": e.base,
                "variants": [
                    {"name": v.name, "value": v.value, "meta": v.meta} for v in e.variants
                ],
            }
            for e in mod.enums
        ],
        "records": [
            {
                "name": r.name,
                "size": r.size,
                "endian": r.endian,
                "packed": r.packed,
                "fields": [
                    {"name": f.name, "type": f.type, "offset": f.offset}
                    for f in sorted(r.fields, key=lambda x: x.offset)
                ],
            }
            for r in mod.records
        ],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


EMITTERS = {
    "rust": emit_rust,
    "c": emit_c,
    "python": emit_python,
    "json": emit_json,
}


# ---------------------------------------------------------------- CLI

def load(path: Path) -> Module:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 读不到 {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    return Parser(lex(text), text).parse()


def ensure(lom_path, out_path, kind: str = "python") -> Path:
    """**按需生成**一份交付物（`lom/<x>.lom` -> `lom/build/<x>.<ext>`）。

    `docs/189` §3.0 的 S3 决定：交付物**不再提交进索引**，改成"谁要谁生成"。
    用它的是两个**运行时加载器**（`tools/fuic.py` / `tools/fujopack.py`）和判据 ——
    它们原先直接读那个**提交在仓里**的生成物，文件不在时（新克隆就是）当场崩。

    **能省的只做一次**：文件在就原样返回（决定 (a) 那条代价）。生成器就是本模块；
    S2 之后换成 `loment/tools/lomc.lomt`（同一件事的 Loment 实现）。
    """
    lom_path, out_path = Path(lom_path), Path(out_path)   # 调用方多半手上是 str
    if out_path.exists():
        return out_path
    mod = load(lom_path)
    errs = check(mod)
    if errs:
        raise LomError(0, 0, f"{lom_path}: {len(errs)} 项语义错误: {errs[0]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(EMITTERS[kind](mod), encoding="utf-8", newline="\n")
    return out_path


def ensure_python(lom_path, out_path) -> Path:
    """`ensure` 的 Python 那一档（两个运行时加载器用的）。"""
    return ensure(lom_path, out_path, "python")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomc", description="L0 接口层编译器 (.lom)")
    ap.add_argument("file", help="输入的 .lom 文件")
    ap.add_argument("--emit-rust", metavar="PATH")
    ap.add_argument("--emit-c", metavar="PATH")
    ap.add_argument("--emit-python", metavar="PATH")
    ap.add_argument("--emit-json", metavar="PATH")
    ap.add_argument("--print", dest="print_target", choices=sorted(EMITTERS))
    ap.add_argument("--check", action="store_true", help="不写盘，仅核对磁盘产物是否与生成结果一致")
    args = ap.parse_args(argv)

    path = Path(args.file)
    try:
        mod = load(path)
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        return 1

    errs = check(mod)
    if errs:
        print(f"[ERR] {path}: {len(errs)} 项语义错误:", file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        return 1

    targets = {
        "rust": args.emit_rust,
        "c": args.emit_c,
        "python": args.emit_python,
        "json": args.emit_json,
    }

    if args.print_target:
        sys.stdout.write(EMITTERS[args.print_target](mod))
        return 0

    if not any(targets.values()) and not args.check:
        print("[ERR] 未指定输出（--emit-* / --print / --check）", file=sys.stderr)
        return 2

    diffs: list[str] = []
    for kind, dest in targets.items():
        if not dest:
            continue
        want = EMITTERS[kind](mod)
        p = Path(dest)
        if args.check:
            if not p.exists():
                # S3 之后**产物不在索引里**（`docs/189` §3.0）：「文件不在」是仓库的
                # **默认状态**（新克隆就是这样），不是漂移 —— 所以只提示，不算差异。
                print(f"[SKIP] {dest}: 未生成（按需生成；--emit-* 可补）")
            elif p.read_text(encoding="utf-8") != want:
                got = p.read_text(encoding="utf-8")
                diffs.append(f"{dest}: 与生成结果不一致（磁盘 {len(got)}B / 生成 {len(want)}B）")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(want, encoding="utf-8", newline="\n")
            print(f"[OK] {path} -> {dest} ({len(want)}B)")

    if args.check:
        if diffs:
            print(f"[DIFF] {path}: {len(diffs)} 项漂移:", file=sys.stderr)
            for d in diffs:
                print("  " + d, file=sys.stderr)
            return 1
        print(f"[OK] {path}: 生成产物与磁盘一致（{sum(1 for v in targets.values() if v)} 个目标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
