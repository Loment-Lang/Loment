#!/usr/bin/env python3
"""loment_oracle.py — 从标准独立推导的 Loment **源语义**解释器（研究原型，`docs/349`）。

## 它是什么

仓里其它一切判据比的都是"两个实现是否一致"：参考编译器与自举编译器发同一份 IR、
两只链接器发同一份 ELF、三个引擎跑同一份字节。它们**一起错**的时候，没有一个判据看得见
（`#513` §12：`i1` 宽度 #432、结构体字段偏移 #431 都是这么过去的）。

这个文件是那个缺失的第三方：它**只读 `standard/LLS.md`**（以及 `docs/` 里记下来的
行为），不 import 编译器、链接器、格式器的任何代码，也不复制它们的数据结构。它直接
解释 Loment 源，产出"这份程序在规范语义下应该往 stdout 写什么、退出码是多少"。

## 独立性纪律（判据 `loment_oracle_test::test_oracle_is_independent` 扫本文件）

* 只 import 标准库；
* 不出现 `import lomentc` / `lomelf` / `lomfmt` / `potato_*` / `lvm`；
* 语义规则不在代码注释里各写一份，而是与 `semantics/*.lls` 的 `rule` 一一对应
  （`RULE_CODES` 与规则文件两个方向都查）。

## 第一版的边界（都是点名拒，不猜）

* 只有整数、`bool`、`str`、定长数组、`&` / `&mut`、`*`、调用、`if` / `while` / `return`；
* 内建只有 `print` / `print_num` / `slice_len` / `syscall4`（**只认 exit=60**）；
* 没有 `struct` / `enum` / `match` / `for` / `defer` / `alloc` / `ptr` / `use`；
* 越界下标、除以零是 trap；`main` 带返回类型按 §14.1 拒绝（实现接受，分歧进
  `docs/349` 的已知分歧表）。

## 用法

    python tools/loment_oracle.py FILE.lomt     # 跑一遍，stdout + 退出码照搬
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

#: `semantics/*.lls` 里每条 `rule` 的 `code` 行必须命中这里，反过来也查。
RULE_CODES = (
    "int_wrap", "int_div", "int_rem", "int_cmp", "cast", "bool_ops", "literal_type",
    "bind_let", "assign", "eval_if", "eval_while", "call",
    "array_lit", "index_read", "index_write", "slice_arg",
    "ref_make", "ref_deref", "ref_store",
    "builtin_print", "builtin_print_num", "builtin_slice_len", "syscall_exit",
    "entry_start", "entry_main",
)

INT_TYPES = {
    "u8": (8, False), "u16": (16, False), "u32": (32, False), "u64": (64, False),
    "i8": (8, True), "i16": (16, True), "i32": (32, True), "i64": (64, True),
}


class OracleError(Exception):
    """解释器自己报的错（拒绝 / trap），带规则名便于对账。"""

    def __init__(self, msg: str, rule: str = ""):
        super().__init__(msg)
        self.rule = rule


class ParseError(OracleError):
    pass


class SpecRefusal(OracleError):
    """超出第一版子集，或规范要求拒绝的写法。"""


class Trap(OracleError):
    pass


# ---------------------------------------------------------------- 词法

@dataclass
class Tok:
    kind: str          # ident / int / str / punct / eof
    text: str
    line: int
    value: object = None


_PUNCT3 = ("...",)
_PUNCT2 = ("->", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "..", ":=", "=>", "::")
_PUNCT1 = "(){}[];,:.+-*/%<>=!&|^"


def lex(src: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == '"':
            j = i + 1
            buf = bytearray()
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    j += 1
                    if j >= n:
                        raise ParseError(f"第 {line} 行: 字符串没闭合")
                    e = src[j]
                    buf += { "n": b"\n", "t": b"\t", "r": b"\r", "0": b"\0",
                             "\\": b"\\", '"': b'"' }.get(e, e.encode("utf-8"))
                    j += 1
                    continue
                if src[j] == "\n":
                    raise ParseError(f"第 {line} 行: 字符串跨行")
                buf += src[j].encode("utf-8")
                j += 1
            if j >= n:
                raise ParseError(f"第 {line} 行: 字符串没闭合")
            toks.append(Tok("str", src[i:j + 1], line, bytes(buf)))
            i = j + 1
            continue
        if c.isdigit():
            j = i
            if src.startswith("0x", i) or src.startswith("0X", i):
                j = i + 2
                while j < n and (src[j] in "0123456789abcdefABCDEF_"):
                    j += 1
                v = int(src[i + 2:j].replace("_", ""), 16)
            else:
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
                v = int(src[i:j].replace("_", ""), 10)
            toks.append(Tok("int", src[i:j], line, v))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Tok("ident", src[i:j], line))
            i = j
            continue
        for p in _PUNCT3 + _PUNCT2:
            if src.startswith(p, i):
                toks.append(Tok("punct", p, line))
                i += len(p)
                break
        else:
            if c in _PUNCT1:
                toks.append(Tok("punct", c, line))
                i += 1
            else:
                raise ParseError(f"第 {line} 行: 不认识的字符 {c!r}")
    toks.append(Tok("eof", "", line))
    return toks


# ---------------------------------------------------------------- 语法

@dataclass(frozen=True)
class Ty:
    kind: str                    # int / bool / str / unit / array / slice / ref
    bits: int = 0
    signed: bool = False
    elem: "Ty | None" = None
    length: int = 0
    mut: bool = False


T_BOOL = Ty("bool")
T_STR = Ty("str")
T_UNIT = Ty("unit")


def ty_name(t: Ty) -> str:
    if t.kind == "int":
        return ("i" if t.signed else "u") + str(t.bits)
    if t.kind == "array":
        return f"[{ty_name(t.elem)}; {t.length}]"
    if t.kind == "slice":
        return ("&mut [" if t.mut else "&[") + ty_name(t.elem) + "]"
    if t.kind == "ref":
        return ("&mut " if t.mut else "&") + ty_name(t.elem)
    return t.kind


@dataclass
class Node:
    kind: str
    line: int
    a: object = None
    b: object = None
    c: object = None
    items: list = field(default_factory=list)


@dataclass
class Func:
    name: str
    params: list          # list[(str, Ty)]
    ret: Ty | None
    body: list
    line: int


@dataclass
class Program:
    module: str
    consts: dict
    funcs: dict
    choices: list


class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks
        self.i = 0

    def peek(self) -> Tok:
        return self.t[self.i]

    def next(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, text: str) -> bool:
        k = self.peek()
        return k.kind in ("punct", "ident") and k.text == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> Tok:
        if not self.at(text):
            k = self.peek()
            raise ParseError(f"第 {k.line} 行: 期望 {text!r}，得到 {k.text!r}")
        return self.next()

    def ident(self) -> str:
        k = self.peek()
        if k.kind != "ident":
            raise ParseError(f"第 {k.line} 行: 期望标识符，得到 {k.text!r}")
        self.i += 1
        return k.text

    # ---- 类型

    def ty(self) -> Ty:
        if self.accept("&"):
            mut = self.accept("mut")
            inner = self.ty()
            if inner.kind == "array":            # `&[T]` 的参数类型写法
                return Ty("slice", elem=inner.elem, length=inner.length, mut=mut)
            return Ty("ref", elem=inner, mut=mut)
        if self.accept("["):
            elem = self.ty()
            if self.accept(";"):
                k = self.peek()
                if k.kind != "int":
                    raise ParseError(f"第 {k.line} 行: 数组长度要是整数字面量")
                self.next()
                self.expect("]")
                return Ty("array", elem=elem, length=int(k.value))
            self.expect("]")
            return Ty("slice", elem=elem)
        if self.accept("("):
            self.expect(")")
            return T_UNIT
        name = self.ident()
        if name in INT_TYPES:
            b, s = INT_TYPES[name]
            return Ty("int", bits=b, signed=s)
        if name == "bool":
            return T_BOOL
        if name == "str":
            return T_STR
        raise SpecRefusal(f"{name}：第一版只认整数 / bool / str / 数组 / 引用（docs/349）")

    # ---- 顶层

    def program(self) -> Program:
        module, consts, funcs, choices = "", {}, {}, []
        while self.peek().kind != "eof":
            if self.at("choose"):
                line = self.next().line
                parts = []
                while self.peek().line == line and not self.at(";"):
                    parts.append(self.next().text)
                self.accept(";")
                choices.append(" ".join(parts))
                continue
            if self.accept("module"):
                module = self.ident()
                self.accept(";")
                continue
            if self.accept("use"):
                raise SpecRefusal("`use`：第一版按单单元解释（docs/349 §边界）")
            if self.accept("const"):
                name = self.ident()
                self.expect(":")
                t = self.ty()
                self.expect("=")
                k = self.peek()
                if k.kind != "int":
                    raise SpecRefusal("`const`：第一版只吃整数字面量")
                self.next()
                self.expect(";")
                consts[name] = (t, int(k.value))
                continue
            self.accept("pub")
            if self.accept("fn"):
                funcs_here = self.func(module)
                funcs[funcs_here.name] = funcs_here
                continue
            k = self.peek()
            raise SpecRefusal(f"第 {k.line} 行: 顶层只认 choose / module / const / fn，"
                              f"得到 {k.text!r}（第一版子集见 docs/349）")
        return Program(module, consts, funcs, choices)

    def func(self, module: str) -> Func:
        line = self.peek().line
        name = self.ident()
        self.expect("(")
        params: list[tuple[str, Ty]] = []
        while not self.at(")"):
            pname = self.ident()
            self.expect(":")
            params.append((pname, self.ty()))
            if not self.accept(","):
                break
        self.expect(")")
        ret = None
        if self.accept("->"):
            ret = self.ty()
        body = self.block()
        return Func(name, params, ret, body, line)

    # ---- 语句

    def block(self) -> list:
        self.expect("{")
        out = []
        while not self.at("}"):
            if self.peek().kind == "eof":
                raise ParseError("块没闭合")
            out.append(self.stmt())
        self.expect("}")
        return out

    def stmt(self) -> Node:
        k = self.peek()
        if self.accept("let"):
            self.accept("mut")
            name = self.ident()
            self.expect(":")
            t = self.ty()
            self.expect("=")
            e = self.expr()
            self.expect(";")
            return Node("let", k.line, name, t, e)
        if self.accept("if"):
            cond = self.expr()
            body = self.block()
            els = None
            if self.accept("else"):
                els = self.stmt() if self.at("if") else self.block()
            return Node("if", k.line, cond, body, els)
        if self.accept("while"):
            cond = self.expr()
            body = self.block()
            return Node("while", k.line, cond, body)
        if self.accept("return"):
            if self.accept(";"):
                return Node("return", k.line, None)
            e = self.expr()
            self.expect(";")
            return Node("return", k.line, e)
        if self.accept("break"):
            self.expect(";")
            return Node("break", k.line)
        if self.accept("continue"):
            self.expect(";")
            return Node("continue", k.line)
        if self.accept("for"):
            name = self.ident()
            self.expect("in")
            src = self.expr()
            if self.accept(".."):
                hi = self.expr()
                body = self.block()
                return Node("for_range", k.line, name, src, hi, items=[body])
            body = self.block()
            return Node("for_in", k.line, name, src, items=[body])
        e = self.expr()
        if self.accept("="):
            v = self.expr()
            self.expect(";")
            return Node("assign", k.line, e, v)
        self.expect(";")
        return Node("expr", k.line, e)

    # ---- 表达式

    _PREC = {"||": 1, "&&": 2, "==": 3, "!=": 3,
             "<": 4, "<=": 4, ">": 4, ">=": 4,
             "|": 5, "^": 6, "&": 7, "<<": 8, ">>": 8, "+": 9, "-": 9,
             "*": 10, "/": 10, "%": 10}

    def expr(self, min_prec: int = 1) -> Node:
        left = self.unary()
        while True:
            k = self.peek()
            if k.kind != "punct" or k.text not in self._PREC:
                return left
            prec = self._PREC[k.text]
            if prec < min_prec:
                return left
            self.next()
            right = self.expr(prec + 1)
            left = Node("bin", left.line, k.text, left, right)

    def unary(self) -> Node:
        k = self.peek()
        if self.accept("-"):
            return Node("neg", k.line, self.unary())
        if self.accept("!"):
            return Node("not", k.line, self.unary())
        if self.accept("*"):
            return Node("deref", k.line, self.unary())
        if self.accept("&"):
            mut = self.accept("mut")
            return Node("ref", k.line, self.unary(), mut)
        return self.postfix(self.primary())

    def postfix(self, e: Node) -> Node:
        while True:
            if self.accept("as"):
                e = Node("cast", e.line, e, self.ty())
                continue
            if self.accept("("):
                args = []
                while not self.at(")"):
                    args.append(self.expr())
                    if not self.accept(","):
                        break
                self.expect(")")
                e = Node("call", e.line, e, None, args)
                continue
            if self.accept("["):
                idx = self.expr()
                self.expect("]")
                e = Node("index", e.line, e, idx)
                continue
            return e

    def primary(self) -> Node:
        k = self.peek()
        if k.kind == "int":
            self.next()
            return Node("int", k.line, int(k.value))
        if k.kind == "str":
            self.next()
            return Node("str", k.line, bytes(k.value))
        if k.kind == "ident":
            if k.text == "true":
                self.next()
                return Node("bool", k.line, True)
            if k.text == "false":
                self.next()
                return Node("bool", k.line, False)
            self.next()
            return Node("name", k.line, k.text)
        if self.accept("("):
            e = self.expr()
            self.expect(")")
            return e
        if self.accept("["):
            items = []
            while not self.at("]"):
                items.append(self.expr())
                if not self.accept(","):
                    break
            self.expect("]")
            return Node("array", k.line, items)
        raise ParseError(f"第 {k.line} 行: 解析不了表达式 {k.text!r}")


# ---------------------------------------------------------------- 值

@dataclass
class Slot:
    ty: Ty
    value: object


@dataclass
class Val:
    ty: Ty
    data: object


@dataclass
class SliceView:
    slots: list
    off: int
    length: int


class Return(Exception):
    def __init__(self, val):
        self.val = val


class Break(Exception):
    pass


class Continue(Exception):
    pass


class Exit(Exception):
    def __init__(self, code: int):
        self.code = code & 0xFF


def _wrap(v: int, t: Ty) -> int:
    mask = (1 << t.bits) - 1
    v &= mask
    if t.signed and v >= 1 << (t.bits - 1):
        v -= 1 << t.bits
    return v


def _fit(v: int, t: Ty) -> bool:
    if not t.signed:
        return 0 <= v < (1 << t.bits)
    return -(1 << (t.bits - 1)) <= v < (1 << (t.bits - 1))


# ---------------------------------------------------------------- 求值

class Interp:
    def __init__(self, prog: Program):
        self.p = prog
        self.out = bytearray()
        self.consts = {n: Val(t, _wrap(v, t)) for n, (t, v) in prog.consts.items()}

    # ---- 表达式

    def eval(self, e: Node, env: dict, want: Ty | None = None) -> Val:
        k = e.kind
        if k == "int":
            t = want if want is not None and want.kind == "int" else Ty("int", 32, False)
            if t.kind != "int":
                raise SpecRefusal(f"第 {e.line} 行: 整数字面量放进 {ty_name(t)}（E001）",
                                  "literal_type")
            if not _fit(e.a, t):
                raise SpecRefusal(f"第 {e.line} 行: 字面量 {e.a} 装不进 {ty_name(t)}（§10.6）",
                                  "literal_type")
            return Val(t, e.a)
        if k == "str":
            return Val(T_STR, e.a)
        if k == "bool":
            return Val(T_BOOL, e.a)
        if k == "name":
            if e.a in env:
                return Val(env[e.a].ty, env[e.a].value)
            if e.a in self.consts:
                return self.consts[e.a]
            raise SpecRefusal(f"第 {e.line} 行: 没有这个名字 {e.a!r}")
        if k == "neg":
            v = self.eval(e.a, env, want)
            if v.ty.kind != "int":
                raise SpecRefusal(f"第 {e.line} 行: 一元 `-` 只对整数", "int_wrap")
            return Val(v.ty, _wrap(-v.data, v.ty))
        if k == "not":
            v = self.eval(e.a, env, T_BOOL)
            if v.ty.kind != "bool":
                raise SpecRefusal(f"第 {e.line} 行: `!` 只对 bool（§10.3）", "bool_ops")
            return Val(T_BOOL, not v.data)
        if k == "deref":
            v = self.eval(e.a, env, want)
            if v.ty.kind != "ref":
                raise SpecRefusal(f"第 {e.line} 行: `*` 只对引用（§12.3）", "ref_deref")
            return Val(v.ty.elem, v.data.value)
        if k == "ref":
            mut = bool(e.b)
            slot = self.lvalue(e.a, env)
            t = Ty("ref", elem=slot.ty, mut=mut)
            if want is not None and want.kind == "slice" and slot.ty.kind == "array":
                return Val(Ty("slice", elem=slot.ty.elem, mut=mut),
                           SliceView(slot.value, 0, slot.ty.length))
            if want is not None and want.kind in ("ref", "slice"):
                t = want
            return Val(t, slot)
        if k == "cast":
            v = self.eval(e.a, env)
            t: Ty = e.b
            if v.ty.kind != "int" or t.kind != "int":
                raise SpecRefusal(f"第 {e.line} 行: 第一版只做整数之间的 `as`（§12.6）", "cast")
            return Val(t, _wrap(v.data, t))
        if k == "array":
            if want is None or want.kind not in ("array",):
                raise SpecRefusal(f"第 {e.line} 行: 数组字面量需要期望类型（§10.6）", "array_lit")
            if len(e.a) != want.length:
                raise SpecRefusal(f"第 {e.line} 行: 数组长度 {len(e.a)} ≠ {want.length}（E016）",
                                  "array_lit")
            slots = [Slot(want.elem, self.eval(item, env, want.elem).data) for item in e.a]
            return Val(want, slots)
        if k == "index":
            arr = self.eval(e.a, env)
            if arr.ty.kind not in ("array", "slice"):
                raise SpecRefusal(f"第 {e.line} 行: 下标只能用在数组/切片上（§12.10）",
                                  "index_read")
            i = self.eval(e.b, env, Ty("int", 32, False))
            if i.ty.kind != "int":
                raise SpecRefusal(f"第 {e.line} 行: 下标要是整数", "index_read")
            slots, off = self._elems(arr)
            n = len(slots)
            if not (0 <= i.data < n):
                raise Trap(f"第 {e.line} 行: 下标 {i.data} 越界（长度 {n}）；"
                           "语言的界外行为不在可观察语义内（§12.10）", "index_read")
            s = slots[i.data]
            return Val(s.ty, s.value)
        if k == "call":
            return self.call(e, env)
        if k == "bin":
            return self.binop(e, env, want)
        raise SpecRefusal(f"第 {e.line} 行: 求值器还不认识 {k}")

    def _elems(self, v: Val):
        if v.ty.kind == "array":
            return v.data, 0
        sv: SliceView = v.data
        return sv.slots[sv.off: sv.off + sv.length], 0

    def binop(self, e: Node, env: dict, want: Ty | None) -> Val:
        op = e.a
        left = self.eval(e.b, env, want)
        if op == "&&":
            if left.ty.kind != "bool":
                raise SpecRefusal(f"第 {e.line} 行: `&&` 要 bool（§10.3）", "bool_ops")
            if not left.data:
                return Val(T_BOOL, False)
            return Val(T_BOOL, bool(self.eval(e.c, env, T_BOOL).data))
        if op == "||":
            if left.ty.kind != "bool":
                raise SpecRefusal(f"第 {e.line} 行: `||` 要 bool（§10.3）", "bool_ops")
            if left.data:
                return Val(T_BOOL, True)
            return Val(T_BOOL, bool(self.eval(e.c, env, T_BOOL).data))
        right = self.eval(e.c, env, left.ty)
        if left.ty != right.ty:
            raise SpecRefusal(f"第 {e.line} 行: `{op}` 两边类型不同："
                              f"{ty_name(left.ty)} vs {ty_name(right.ty)}（E001）")
        if left.ty.kind == "int":
            return self.int_binop(op, left, right, e.line)
        if left.ty.kind == "bool" and op in ("==", "!="):
            return Val(T_BOOL, (left.data == right.data) == (op == "=="))
        raise SpecRefusal(f"第 {e.line} 行: `{op}` 不支持 {ty_name(left.ty)}（第一版）")

    def int_binop(self, op: str, a: Val, b: Val, line: int) -> Val:
        t, x, y = a.ty, a.data, b.data
        if op in ("+", "-", "*"):
            v = {"+": x + y, "-": x - y, "*": x * y}[op]
            return Val(t, _wrap(v, t))
        if op in ("/", "%"):
            if y == 0:
                raise Trap(f"第 {line} 行: 除零/取余零是 trap（§10.2）",
                           "int_div" if op == "/" else "int_rem")
            q = abs(x) // abs(y)
            if (x < 0) != (y < 0):
                q = -q
            return Val(t, _wrap(q if op == "/" else x - q * y, t))
        if op in ("==", "!=", "<", "<=", ">", ">="):
            r = {"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y,
                 ">": x > y, ">=": x >= y}[op]
            return Val(T_BOOL, r)
        if op in ("&", "|", "^", "<<", ">>"):
            if op in ("<<", ">>"):
                v = (x << (y % t.bits)) if op == "<<" else (x >> (y % t.bits))
                if t.signed and op == ">>":
                    v = x >> (y % t.bits)          # Python 的算术右移与二补数一致
            else:
                v = {"&": x & y, "|": x | y, "^": x ^ y}[op]
            return Val(t, _wrap(v, t))
        raise SpecRefusal(f"运算符 {op} 不在第一版里")

    # ---- 调用

    def call(self, e: Node, env: dict) -> Val:
        callee = e.a
        if callee.kind != "name":
            raise SpecRefusal(f"第 {e.line} 行: 第一版只支持按名字调用", "call")
        name = callee.a
        fn = self.p.funcs.get(name)
        if fn is None:
            return self.builtin(name, e, env)
        if len(e.c) != len(fn.params):
            raise SpecRefusal(f"第 {e.line} 行: {name} 要 {len(fn.params)} 个实参，"
                              f"给了 {len(e.c)} 个")
        frame: dict = {}
        for (pname, pty), arg in zip(fn.params, e.c):
            frame[pname] = self.bind_arg(arg, pty, env)
        try:
            self.run_block(fn.body, frame)
            rv = Val(T_UNIT, None)
        except Return as r:
            rv = r.val if r.val is not None else Val(T_UNIT, None)
        if fn.ret is None:
            return Val(T_UNIT, None)
        if rv.ty != fn.ret:
            raise SpecRefusal(f"第 {e.line} 行: {name} 返回 {ty_name(rv.ty)}，"
                              f"声明 {ty_name(fn.ret)}")
        return rv

    def bind_arg(self, arg: Node, pty: Ty, env: dict) -> Slot:
        if arg.kind == "ref":
            slot = self.lvalue(arg.a, env)
            want_slice = pty.kind == "slice"
            if want_slice and slot.ty.kind == "array":
                return Slot(pty, SliceView(slot.value, 0, slot.ty.length))
            if pty.kind == "ref":
                return Slot(pty, slot)
            raise SpecRefusal(f"第 {arg.line} 行: `&` 实参对不上形参类型 {ty_name(pty)}",
                              "ref_make")
        v = self.eval(arg, env, pty)
        if pty.kind == "slice":
            raise SpecRefusal(f"第 {arg.line} 行: `[T]` 形参要 `&a` 实参（§10.7）", "slice_arg")
        if v.ty != pty:
            raise SpecRefusal(f"第 {arg.line} 行: 实参 {ty_name(v.ty)} 对不上形参 {ty_name(pty)}（E001）")
        return Slot(pty, v.data)

    def builtin(self, name: str, e: Node, env: dict) -> Val:
        args = e.c
        if name == "print":
            v = self.eval(args[0], env, T_STR)
            self.out += v.data
            return Val(Ty("int", 32, False), len(v.data))
        if name == "print_num":
            v = self.eval(args[0], env)
            if v.ty.kind != "int":
                raise SpecRefusal(f"第 {e.line} 行: print_num 要整数（§18.1）",
                                  "builtin_print_num")
            data = str(v.data).encode()
            self.out += data
            return Val(Ty("int", 32, False), len(data))
        if name == "str_len":
            v = self.eval(args[0], env, T_STR)
            return Val(Ty("int", 32, False), len(v.data))
        if name == "str_byte":
            v = self.eval(args[0], env, T_STR)
            i = self.eval(args[1], env, Ty("int", 32, False))
            if not (0 <= i.data < len(v.data)):
                raise Trap(f"第 {e.line} 行: str_byte 下标越界（§18.1）", "str_byte")
            return Val(Ty("int", 32, False), v.data[i.data])
        if name == "str_eq":
            a = self.eval(args[0], env, T_STR)
            b = self.eval(args[1], env, T_STR)
            return Val(T_BOOL, a.data == b.data)
        if name == "str_concat":
            a = self.eval(args[0], env, T_STR)
            b = self.eval(args[1], env, T_STR)
            return Val(T_STR, a.data + b.data)
        if name == "slice_len":
            v = self.eval(args[0], env)
            if v.ty.kind != "slice":
                raise SpecRefusal(f"第 {e.line} 行: slice_len 要切片（§18.1）",
                                  "builtin_slice_len")
            return Val(Ty("int", 32, False), v.data.length)
        if name == "syscall4":
            nr = self.eval(args[0], env)
            if nr.data == 60:
                code = self.eval(args[1], env)
                raise Exit(int(code.data))
            raise SpecRefusal(f"第 {e.line} 行: 第一版只认 syscall 60（exit）；"
                              f"得到 {nr.data}（docs/349 §边界）", "syscall_exit")
        raise SpecRefusal(f"第 {e.line} 行: 第一版没有这个内建/函数 {name!r}")

    # ---- 左值与语句

    def lvalue(self, e: Node, env: dict) -> Slot:
        if e.kind == "name":
            if e.a in env:
                return env[e.a]
            raise SpecRefusal(f"第 {e.line} 行: 没有这个名字 {e.a!r}")
        if e.kind == "index":
            base = self.lvalue(e.a, env) if e.a.kind == "name" else None
            arrv = Val(base.ty, base.value) if base is not None else self.eval(e.a, env)
            if arrv.ty.kind not in ("array", "slice"):
                raise SpecRefusal(f"第 {e.line} 行: 下标只能用在数组/切片上", "index_write")
            slots, _ = self._elems(arrv)
            i = self.eval(e.b, env, Ty("int", 32, False))
            if not (0 <= i.data < len(slots)):
                raise Trap(f"第 {e.line} 行: 下标 {i.data} 越界（长度 {len(slots)}）",
                           "index_write")
            return slots[i.data]
        if e.kind == "deref":
            r = self.eval(e.a, env)
            if r.ty.kind != "ref" or not r.ty.mut:
                raise SpecRefusal(f"第 {e.line} 行: 只有 `&mut` 引用能写（§10.9）", "ref_store")
            return r.data
        raise SpecRefusal(f"第 {e.line} 行: 赋值目标不是左值（E014）", "assign")

    def run_block(self, body: list, env: dict):
        for st in body:
            self.exec_stmt(st, env)

    def exec_stmt(self, st: Node, env: dict):
        if st.kind == "let":
            v = self.eval(st.c, env, st.b)
            env[st.a] = Slot(st.b, v.data)
            return
        if st.kind == "assign":
            v = self.eval(st.b, env, None)
            slot = self.lvalue(st.a, env)
            if slot.ty != v.ty:
                raise SpecRefusal(f"第 {st.line} 行: 赋值 {ty_name(v.ty)} → {ty_name(slot.ty)}（E001）",
                                  "assign")
            slot.value = v.data
            return
        if st.kind == "if":
            c = self.eval(st.a, env, T_BOOL)
            if c.ty.kind != "bool":
                raise SpecRefusal(f"第 {st.line} 行: if 的条件要 bool（§11.5）", "eval_if")
            if c.data:
                self.run_block(st.b, env)
            elif isinstance(st.c, Node):
                self.exec_stmt(st.c, env)
            elif st.c is not None:
                self.run_block(st.items[0], env)
            return
        if st.kind == "while":
            while True:
                c = self.eval(st.a, env, T_BOOL)
                if c.ty.kind != "bool":
                    raise SpecRefusal(f"第 {st.line} 行: while 的条件要 bool（§11.5）", "eval_while")
                if not c.data:
                    return
                try:
                    self.run_block(st.b, env)
                except Break:
                    return
                except Continue:
                    continue
        if st.kind == "for_range":
            lo = self.eval(st.b, env)
            if lo.ty.kind != "int":
                raise SpecRefusal(f"第 {st.line} 行: for 的范围要整数（§11.6）", "eval_for")
            hi = self.eval(st.c, env, lo.ty)
            v = lo.data
            while v < hi.data:
                env[st.a] = Slot(lo.ty, v)
                try:
                    self.run_block(st.items[0], env)
                except Break:
                    break
                except Continue:
                    pass
                v = _wrap(v + 1, lo.ty)
            return
        if st.kind == "for_in":
            src = self.eval(st.b, env)
            if src.ty.kind not in ("array", "slice"):
                raise SpecRefusal(f"第 {st.line} 行: for-in 要数组/切片（§11.6）", "eval_for")
            slots, _ = self._elems(src)
            for cell in slots:
                env[st.a] = Slot(cell.ty, cell.value)
                try:
                    self.run_block(st.items[0], env)
                except Break:
                    break
                except Continue:
                    pass
            return
        if st.kind == "break":
            raise Break()
        if st.kind == "continue":
            raise Continue()
        if st.kind == "return":
            raise Return(self.eval(st.a, env) if st.a is not None else None)
        if st.kind == "expr":
            self.eval(st.a, env)
            return
        raise SpecRefusal(f"第 {st.line} 行: 求值器还不认识语句 {st.kind}")

    # ---- 入口

    def run(self) -> tuple[bytes, int]:
        p = self.p
        if "_start" in p.funcs:
            fn = p.funcs["_start"]
            if fn.params or fn.ret is not None:
                raise SpecRefusal("`_start` 必须无参、无返回类型（§14.2）", "entry_start")
            try:
                self.run_block(fn.body, {})
            except Exit as x:
                return bytes(self.out), x.code
            raise SpecRefusal("`_start` 正常返回到调用者 —— 规范要求以 syscall 结束（§14.2）",
                              "entry_start")
        if "main" in p.funcs:
            fn = p.funcs["main"]
            if fn.params or fn.ret is not None:
                raise SpecRefusal("`main` 必须无参、无返回类型（§14.1）", "entry_main")
            try:
                self.run_block(fn.body, {})
            except Exit as x:
                return bytes(self.out), x.code
            return bytes(self.out), 0
        raise SpecRefusal("没有入口：`_start` 或 `main`（§14.6）", "entry_main")


# ---------------------------------------------------------------- 门面

@dataclass
class Outcome:
    stdout: bytes
    rc: int
    rule: str = ""
    error: str = ""


def run_source(src: str) -> Outcome:
    """解释一份 Loment 源；正常跑完给 (stdout, rc)，拒绝/trap 给 error。"""
    try:
        prog = Parser(lex(src)).program()
        out, rc = Interp(prog).run()
        return Outcome(out, rc)
    except (Exit,) as x:                       # pragma: no cover - 防御
        return Outcome(b"", x.code)
    except OracleError as e:
        return Outcome(b"", 255, e.rule, str(e))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法: python tools/loment_oracle.py FILE.lomt", file=sys.stderr)
        return 2
    with open(argv[1], "rb") as f:
        src = f.read().decode("utf-8", errors="replace")
    r = run_source(src)
    if r.error:
        print(f"oracle: {r.error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(r.stdout)
    return r.rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
