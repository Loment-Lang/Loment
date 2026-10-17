#!/usr/bin/env python3
"""gotrans.py — **Go 写法 -> Loment**（`docs/188` §7.1 六门里的第六门，也是最后一门）。

     Go 写法 --gotrans--> Loment

## 为什么这一门**不**共用 `trans_core`

`docs/188` §7.1 定的架构是"一份解析器 + 方言表"，而那一份是给**花括号 + 分号**
那一族（C / C++ / Java / C#）的：`<类型> <名>(…) { … }`、条件带括号、`;` 结句。
**Go 的形状不一样**，三条都不同：

| | 花括号族 | Go |
|---|---|---|
| 类型写在哪 | 名字**前面**（`int f(int a)`） | 名字**后面**（`func f(a int) int`） |
| 条件的括号 | 要（`if (x)`） | **不要**（`if x`）—— 写了反而编不过 |
| 循环 | `for` / `while` / `do` | **只有 `for`**，一个词管三种形状 |

硬塞进那张方言表的话，得给解析器加三个"哪一门"的开关 —— 那就不是方言表了。
所以这一门**自足一份**（分词器 + 递归下降 + 发射器都在这儿）。

## 与 Loment 的语义差：**两处都不用补转换**

Go 的 `&&` `||` `!` 与比较**出 `bool`**、条件**只收 `bool`** —— 与 Loment 一致；
而且 Go **没有** `bool ↔ int` 的隐式转换（`if x`（x 是 int）在 Go 里编不过）。
所以与 Java / C# 同一档：**两个方向都不用补**。

其余都对得上：`/` 与 `%` 向零截断（实测探过）、`>>` 对**有符号**是算术的、
整数运算按模 2^n 回绕（Loment 的 `i32` 加法也是回绕）。

## 这一门**收**两样别人拒的东西 —— 理由不一样，值得写下来

`i++` / `i--` / `x += e`：在 C / Java / C# 里它们是**表达式**（有值），
所以 `y = i++` 那种写法映射不过去，那几门一律拒。**Go 里它们是语句、没有值**
（`y = i++` 在 Go 里编不过），所以 `i++` 就是 `i = i + 1`，**一字不差**。
⇒ 同一处，按"能表达的就转"给出相反的结论 —— 而差别在**源语言那边**，不在 Loment 这边。

## 子集

`int`→`i64`（**Go 的 `int` 有平台宽度**，在 x86-64 上是 64 位 —— 与
`potato_from.GO_TYPES` 同一个口径）、`int8/16/32/64`、
`uint`/`uint8/16/32/64`、`byte`→`u8`、`rune`→`i32`、`bool`→`bool`、无名返回→`()`。
**`string` / `float32/64` / `complex` / 指针 / 切片 / map / chan / interface / struct
全拒**。

用法:

    python tools/gotrans.py SRC.go [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 类型表

#: Go 的类型拼法 -> Loment 类型。
#:
#: **`int` 映 `i64`，与 `potato_from.GO_TYPES` 一个口径** —— 那一层写的是"Go 的
#: `int`/`uint` 是平台相关的，本仓只支持 x86-64，所以按 64 位读"。
#:
#: **两层必须说同一件事**：`potato_from` 的 `params` 会在**不带正文**时发成
#: `pub extern fn`（接口单元），而带正文时发的是**翻译器**写出来的签名。两处对
#: `int` 的宽度说法不一致的话，同一份 Go 会产出**两个互相矛盾**的单元 ——
#: 而那种错两边都编得过。（第一版这里写的是 `i32`，是照着 Loment 的默认整数顺手写的，
#: 与对象层当场打架。）
GO_TYPES = {
    "int": "i64", "int8": "i8", "int16": "i16", "int32": "i32", "int64": "i64",
    "uint": "u64", "uint8": "u8", "uint16": "u16", "uint32": "u32", "uint64": "u64",
    # `byte` 是 `uint8` 的别名、`rune` 是 `int32` 的别名（Go 规范写死的）
    "byte": "u8", "rune": "i32",
    "bool": "bool",
    "": "()",                     # 没有返回类型 = 不返回 = `()`
}

#: 认得出来但**本子集不收**的类型 —— 认得它们才报得出"哪一处不支持"。
GO_BAD_TYPES = ("string", "float32", "float64", "complex64", "complex128",
                "uintptr", "error", "any", "struct", "interface", "map", "chan",
                "func")

#: Go 关键字里**出现就拒**的那一批（`for` / `if` / `else` / `return` / `var` /
#: `const` / `func` / `package` / `import` / `type` 由解析器自己处理，不在这里）。
GO_KW = frozenset({
    "break", "continue", "goto", "fallthrough", "defer", "go", "select", "switch",
    "case", "default", "range", "map", "chan", "struct", "interface", "type",
    "package", "import", "func", "var", "const", "nil", "true", "false", "iota",
    "panic", "recover", "make", "new", "len", "cap", "append", "copy", "delete",
    "complex", "real", "imag", "close", "print", "println", "any", "min", "max",
})

#: `true` / `false` 是 Go 的真字面量（与 Loment 同名同义）
GO_BOOL_LIT = {"true": 1, "false": 0}

#: Go 的默认整数 —— `:=` 在**推不出更精确的类型**时给这个（`int` 在 x86-64 上是 64 位，
#: 与 `potato_from.GO_TYPES` 同一个口径，见那张表的注解）。
GO_INT = "i64"


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 静默跳过 = 产物少算一步却照样能编。"""


class GoError(Exception):
    """这份 Go 语法不过。与 `Unsupported` 分开：一个是"没实现"，一个是"写错了"。"""


# ---------------------------------------------------------------- 分词

_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<lc>//[^\n]*)
    | (?P<bc>/\*.*?\*/)
    | (?P<str>"(?:[^"\\\n]|\\.)*")
    | (?P<rune>'(?:[^'\\\n]|\\.)*')
    | (?P<num>0[xX][0-9a-fA-F_]+|\d[\d_]*)
    | (?P<id>[A-Za-z_]\w*)
    | (?P<op>:=|\.\.\.|<<=|>>=|&=|\|=|\^=|\+=|-=|\*=|/=|%=|\+\+|--|==|!=|<=|>=|&&|\|\||<<|>>|[-+*/%&|^!<>=();,{}\[\].:])
""", re.X | re.S)

#: 这一门**不收**的运算符。`++` / `--` / `+=` 那批**不在这里** —— Go 里它们是**语句**，
#: 由解析器展开成 `x = x + 1`（见文件头那条）。`&` `|` `^` 是**位运算**，Loment 有，
#: 所以也不拒；拒的是 `&^`（Go 独有的位清除）与 `<-`（channel）。
_REJECT_OP = {"&^": "位清除 `&^`", "&^=": "位清除 `&^=`", "<-": "channel 收发 `<-`"}


#: **哪些 token 出现在行尾时会自动补分号**（Go 规范 §Semicolons 那张表，逐条对齐）。
#: 少了这一步的话，`return a`（没写分号）会报"期望 `;`，得到 '}'" —— 而 Go 里
#: 那样写是**对的**，分号由词法器补。这不是"顺手宽容一下"，是 Go 语法的一部分。
_ASI_WORDS = {"break", "continue", "fallthrough", "return"}
_ASI_OPS = {")", "]", "}", "++", "--"}


def _asi_after(kind: str, text: str) -> bool:
    if kind in ("id", "num"):
        # 关键字里只有那四个在表上，其余（`if` / `for` / `}` 前的 `else` …）不补
        return text not in ("if", "else", "for", "func", "package", "import",
                            "var", "const", "type", "switch", "case", "default",
                            "go", "defer", "select", "range", "map", "chan",
                            "struct", "interface")
    return kind == "op" and text in _ASI_OPS


def tokens(src: str) -> list[tuple[str, str, int]]:
    """Go 源码 -> `[(种类, 文本, 行)]`。注释与空白丢掉，**行尾自动补分号**。"""
    out: list[tuple[str, str, int]] = []
    line = 1
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise GoError(f"第 {line} 行: 认不出的字符 {src[i]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind in ("ws", "lc", "bc"):
            line += text.count("\n")
            i = m.end()
            continue
        # **字符串不在分词这一层拒** —— 等到解析器真正用到它的那一处再拒。
        # 在分词层拒的话，`import "fmt"` 会报"不支持字符串字面量"，而**真正的原因**
        # 是那一行 `import`（用户写的是 import，不是字符串）—— 一句话指错方向。
        # `primary()` 那边有一条专门的点名（那一处的报错才是对的）。
        # ---- **自动分号插入**：上一个 token 在表上、且中间跨了行 ⇒ 补一个 `;`
        if out and line > out[-1][2] and _asi_after(out[-1][0], out[-1][1]):
            out.append(("op", ";", out[-1][2]))
        if kind == "rune":
            # `'a'` 是**一个整数**（Go 的 rune 就是 int32）—— 本子集收它，取码点。
            body = text[1:-1]
            if len(body) == 1 and body.isascii() and body.isprintable():
                out.append(("num", str(ord(body)), line))
            else:
                raise Unsupported(f"第 {line} 行: 不支持转义/多字节的 rune 字面量 {text!r}")
            i = m.end()
            continue
        if kind == "op" and text in _REJECT_OP:
            raise Unsupported(f"第 {line} 行: 不支持{_REJECT_OP[text]}")
        out.append(("str" if kind == "str" else
                    "num" if kind == "num" else
                    "id" if kind == "id" else "op", text, line))
        i = m.end()
    # 收尾那一条（文件末尾那一行也会补一个）
    if out and _asi_after(out[-1][0], out[-1][1]):
        out.append(("op", ";", out[-1][2]))
    return out


# ---------------------------------------------------------------- 语法树


class Lit:
    """整数字面量。`b=True` = 源里是 `true`/`false`（Go 有真 bool，同 Loment）。"""
    __slots__ = ("v", "line", "b")

    def __init__(self, v: int, line: int, b: bool = False) -> None:
        self.v, self.line, self.b = v, line, b


class Var:
    __slots__ = ("name", "line")

    def __init__(self, name: str, line: int) -> None:
        self.name, self.line = name, line


class Call:
    __slots__ = ("name", "args", "line")

    def __init__(self, name: str, args: list, line: int) -> None:
        self.name, self.args, self.line = name, args, line


class Un:
    __slots__ = ("op", "e", "line")

    def __init__(self, op: str, e, line: int) -> None:
        self.op, self.e, self.line = op, e, line


class Cast:
    """`int(b)` / `uint8(x)` —— Go 的**类型转换**。

    Go **没有隐式数值转换**（`int8` 不会自动变成 `int`），所以转换在 Go 里到处都是 ——
    而它**正好**对上 Loment 的 `as`。这是这一门里少见的"两边严法一致"的一格：
    C / C++ 要靠翻译器**猜**哪里补转换（两边都收），Go 是源码里**已经写好了**。
    """

    __slots__ = ("ty", "e", "line")

    def __init__(self, ty: str, e, line: int) -> None:
        self.ty, self.e, self.line = ty, e, line


class Bin:
    __slots__ = ("op", "l", "r", "line")

    def __init__(self, op: str, l, r, line: int) -> None:
        self.op, self.l, self.r, self.line = op, l, r, line


class Decl:
    """`var x T = e` / `x := e` / `var x T`。`ty` 是**已经定下来的** Loment 类型串。"""
    __slots__ = ("ty", "name", "init", "line")

    def __init__(self, ty: str, name: str, init, line: int) -> None:
        self.ty, self.name, self.init, self.line = ty, name, init, line


class Assign:
    __slots__ = ("name", "e", "line")

    def __init__(self, name: str, e, line: int) -> None:
        self.name, self.e, self.line = name, e, line


class Return:
    __slots__ = ("e", "line")

    def __init__(self, e, line: int) -> None:
        self.e, self.line = e, line


class If:
    __slots__ = ("cond", "then", "els", "line")

    def __init__(self, cond, then: list, els, line: int) -> None:
        self.cond, self.then, self.els, self.line = cond, then, els, line


class While:
    __slots__ = ("cond", "body", "line")

    def __init__(self, cond, body: list, line: int) -> None:
        self.cond, self.body, self.line = cond, body, line


class For3:
    """`for init; cond; post { }` —— 三截那一种。"""
    __slots__ = ("init", "cond", "post", "body", "line")

    def __init__(self, init, cond, post, body: list, line: int) -> None:
        self.init, self.cond, self.post, self.body, self.line = init, cond, post, body, line


class ExprStmt:
    __slots__ = ("e", "line")

    def __init__(self, e, line: int) -> None:
        self.e, self.line = e, line


class Fn:
    __slots__ = ("ret", "name", "params", "body", "line")

    def __init__(self, ret: str, name: str, params: list, body: list, line: int) -> None:
        self.ret, self.name, self.params, self.body, self.line = ret, name, params, body, line


# ---------------------------------------------------------------- 解析

#: 优先级，低到高 —— **与 Loment 同一张**（Go 的运算符优先级与 C 系那一张一致，
#: 只有 `&&`/`||` 的相对层级在 Go 里也相同）。
_PREC = [["||"], ["&&"], ["==", "!=", "<", "<=", ">", ">="],
         ["+", "-", "|", "^"], ["*", "/", "%", "<<", ">>", "&"]]
_PRECOF = {op: i + 1 for i, lvl in enumerate(_PREC) for op in lvl}

#: 结果是不是 `bool`（Go 里比较与 `&&`/`||` 都出 `bool`）
_BOOL_RESULT = {"<", ">", "<=", ">=", "==", "!=", "&&", "||"}

_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


class Parser:
    def __init__(self, toks: list[tuple[str, str, int]]) -> None:
        self.t = toks
        self.i = 0
        #: 变量名 -> 声明的 Loment 类型（发射器只要分 `bool` 与"其它"两档）
        self.varty: dict[str, str] = {}
        self.ret = "()"

    # ---- 小工具
    def peek(self, k: int = 0) -> tuple[str, str, int]:
        j = self.i + k
        if j < len(self.t):
            return self.t[j]
        return ("eof", "", self.t[-1][2] if self.t else 1)

    def at(self, text: str, k: int = 0) -> bool:
        return self.peek(k)[1] == text

    def want(self, text: str) -> tuple[str, str, int]:
        t = self.peek()
        if t[1] != text:
            raise GoError(f"第 {t[2]} 行: 期望 `{text}`，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t

    def ident(self) -> tuple[str, int]:
        t = self.peek()
        if t[0] != "id":
            raise GoError(f"第 {t[2]} 行: 期望标识符，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t[1], t[2]

    def safe(self, name: str) -> str:
        return name + "_go" if name in _LOMENT_KW else name

    # ---- 类型
    def go_type(self, where: str) -> str:
        """吃一个类型（**写在名字后面**）-> Loment 类型串。"""
        t = self.peek()
        if t[0] != "id":
            raise GoError(f"第 {t[2]} 行: 期望类型，得到 {t[1] or '<结束>'!r}")
        if t[1] in GO_BAD_TYPES:
            raise Unsupported(f"第 {t[2]} 行: 类型 `{t[1]}` 不在本子集里"
                              f"（{where}）—— 本语言只有整数标量与 `bool`")
        if t[1] == "[]" or self.at("[", 1):
            raise Unsupported(f"第 {t[2]} 行: 不支持切片/数组类型（{where}）")
        if t[1] not in GO_TYPES:
            raise Unsupported(f"第 {t[2]} 行: 类型 `{t[1]}` 不在本子集里（{where}）"
                              f"—— 本子集只收 {sorted(k for k in GO_TYPES if k)}")
        self.i += 1
        return GO_TYPES[t[1]]

    # ---- 表达式
    def expr(self) -> object:
        return self.binary(1)

    def binary(self, minp: int) -> object:
        left = self.unary()
        while True:
            t = self.peek()
            if t[0] != "op" or t[1] not in _PRECOF:
                return left
            p = _PRECOF[t[1]]
            if p < minp:
                return left
            self.i += 1
            right = self.binary(p + 1)
            left = Bin(t[1], left, right, t[2])

    def unary(self) -> object:
        t = self.peek()
        if t[0] == "op" and t[1] == "-":
            self.i += 1
            return Un("-", self.unary(), t[2])
        if t[0] == "op" and t[1] == "!":
            # **Go 的 `!` 收 `bool`**（与 Loment 一致）—— 不像 C 那样"非零即真"
            self.i += 1
            return Un("!", self.unary(), t[2])
        if t[0] == "op" and t[1] == "^":
            raise Unsupported(f"第 {t[2]} 行: 不支持一元 `^`（按位取反）")
        if t[0] == "op" and t[1] == "&":
            raise Unsupported(f"第 {t[2]} 行: 不支持取地址 `&`（本子集没有指针）")
        return self.primary()

    def primary(self) -> object:
        t = self.peek()
        if t[1] == "(":
            self.i += 1
            e = self.expr()
            self.want(")")
            return e
        if t[0] == "str":
            # **点名**：本语言没有字符串值，而 `string` 也不是本子集的类型
            raise Unsupported(f"第 {t[2]} 行: 不支持字符串字面量 {t[1][:12]}… "
                              f"（本语言没有字符串值；`string` 也不是本子集的类型）")
        if t[0] == "num":
            self.i += 1
            return Lit(int(t[1].replace("_", ""), 0), t[2])
        if t[0] == "id":
            if t[1] in GO_BOOL_LIT:
                self.i += 1
                return Lit(GO_BOOL_LIT[t[1]], t[2], True)
            if t[1] in GO_KW:
                raise Unsupported(f"第 {t[2]} 行: 表达式里不支持 `{t[1]}`")
            # **类型转换**：`int(b)` / `uint8(x)` —— 那个名字是个类型名、后面跟着 `(`
            if t[1] in GO_TYPES and t[1] and self.at("(", 1):
                self.i += 1
                self.want("(")
                e = self.expr()
                self.want(")")
                return Cast(GO_TYPES[t[1]], e, t[2])
            self.i += 1
            if self.at("."):
                # 成员访问（`os.Exit`、`fmt.Println`）—— 本子集只有标量与自由函数
                raise Unsupported(f"第 {t[2]} 行: 不支持成员访问 `{t[1]}.…`"
                                  f"（本子集只有标量与自由函数）")
            if self.at("("):
                self.i += 1
                args = []
                if not self.at(")"):
                    args.append(self.expr())
                    while self.at(","):
                        self.i += 1
                        args.append(self.expr())
                self.want(")")
                return Call(t[1], args, t[2])
            return Var(t[1], t[2])
        raise GoError(f"第 {t[2]} 行: 期望表达式，得到 {t[1] or '<结束>'!r}")

    # ---- 语句
    def block(self) -> list:
        self.want("{")
        out = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise GoError(f"第 {self.peek()[2]} 行: 块没闭合")
            if self.at(";"):
                self.i += 1          # 自动分号：`}` 后面也会补一个，跳掉
                continue
            out.append(self.stmt())
        self.want("}")
        return out

    def stmt(self) -> object:
        t = self.peek()
        if t[1] == "var":
            return self.var_decl()
        if t[1] == "if":
            return self.if_()
        if t[1] == "for":
            return self.for_()
        if t[1] == "return":
            self.i += 1
            if self.at(";"):
                self.i += 1
                return Return(None, t[2])
            e = self.expr()
            self.want(";")
            return Return(e, t[2])
        if t[1] in ("break", "continue", "goto", "fallthrough", "defer", "go",
                    "select", "switch", "case", "default", "type", "const",
                    "panic"):
            raise Unsupported(f"第 {t[2]} 行: 不支持 `{t[1]}`")
        return self.simple_stmt(semi=True)

    def simple_stmt(self, semi: bool) -> object:
        """`x := e` / `x = e` / `x op= e` / `x++` / `x--` / `f(...)`。"""
        t = self.peek()
        if t[0] != "id":
            raise GoError(f"第 {t[2]} 行: 期望语句，得到 {t[1] or '<结束>'!r}")
        if t[1] in GO_KW:
            raise Unsupported(f"第 {t[2]} 行: 这里不支持 `{t[1]}`")
        # `x, y := ...` —— 多重赋值**不收**（Go 独有的形状，Loment 没有）
        if self.at(",", 1):
            raise Unsupported(f"第 {t[2]} 行: 不支持多重赋值/多重声明 `{t[1]}, …`")
        name, line = self.ident()
        if self.at(":="):
            self.i += 1
            e = self.expr()
            if semi:
                self.want(";")
            # **`:=` 的类型是推出来的**：Go 推 `int` / `bool`，这里跟 Loment 的默认走
            return Decl(self.infer_ty(e), name, e, line)
        if self.at("++") or self.at("--"):
            op = "+" if self.at("++") else "-"
            self.i += 1
            if semi:
                self.want(";")
            return Assign(name, Bin(op, Var(name, line), Lit(1, line), line), line)
        for aop, op in (("+=", "+"), ("-=", "-"), ("*=", "*"), ("/=", "/"),
                        ("%=", "%"), ("&=", "&"), ("|=", "|"), ("^=", "^"),
                        ("<<=", "<<"), (">>=", ">>")):
            if self.at(aop):
                self.i += 1
                e = self.expr()
                if semi:
                    self.want(";")
                return Assign(name, Bin(op, Var(name, line), e, line), line)
        if self.at("="):
            self.i += 1
            e = self.expr()
            if semi:
                self.want(";")
            return Assign(name, e, line)
        if self.at("("):
            self.i += 1
            args = []
            if not self.at(")"):
                args.append(self.expr())
                while self.at(","):
                    self.i += 1
                    args.append(self.expr())
            self.want(")")
            if semi:
                self.want(";")
            return ExprStmt(Call(name, args, line), line)
        raise GoError(f"第 {self.peek()[2]} 行: `{name}` 后面期望 `:=` / `=` / `(`，"
                      f"得到 {self.peek()[1]!r}")

    def var_decl(self) -> object:
        t = self.want("var")
        if self.at("("):
            raise Unsupported(f"第 {t[2]} 行: 不支持 `var ( … )` 成组声明")
        name, line = self.ident()
        if self.at("="):
            self.i += 1
            e = self.expr()
            self.want(";")
            return Decl(self.infer_ty(e), name, e, line)
        ty = self.go_type("var 声明")
        if self.at("="):
            self.i += 1
            e = self.expr()
            self.want(";")
            return Decl(ty, name, e, line)
        self.want(";")
        # Go 的零值初始化 —— 与 Loment 那边"不写初值也要有确定值"是同一条
        return Decl(ty, name, None, line)

    def if_(self) -> object:
        t = self.want("if")
        # Go 允许 `if init; cond { }` —— 有分号就把它当前一截语句
        init = None
        if self._has_semi_before_brace():
            init = self.simple_stmt(semi=True)
        cond = self.expr()
        if init is not None:
            # 前一截是语句、条件在后面：Loment 没有"带初始化的 if"，
            # 但可以有裸块吗？没有 —— 所以只能拒绝（少见写法）。
            raise Unsupported(f"第 {t[2]} 行: 不支持 `if init; cond {{ }}`"
                              f"（把 init 提到 if 前面，写成两条语句）")
        then = self.block()
        els = None
        if self.at("else"):
            self.i += 1
            if self.at("if"):
                els = [self.if_()]
            else:
                els = self.block()
        return If(cond, then, els, t[2])

    def for_(self) -> object:
        """Go 的三种 `for`：`for { }` / `for cond { }` / `for init; cond; post { }`。"""
        t = self.want("for")
        if self.at("{"):
            return While(None, self.block(), t[2])
        if not self._has_semi_before_brace():
            # 只有条件 —— `for cond { }`
            cond = self.expr()
            return While(cond, self.block(), t[2])
        init = None if self.at(";") else self.simple_stmt(semi=True)
        if self.at(";"):
            self.i += 1
        cond = None if self.at(";") else self.expr()
        self.want(";")
        # 步进后面跟的是 `{` 不是 `;`（Go 的三截 `for` 里最后一截没有分号）
        post = None if self.at("{") else self.simple_stmt(semi=False)
        return For3(init, cond, post, self.block(), t[2])

    def _has_semi_before_brace(self) -> bool:
        """往后看：在**括号深度 0** 上先遇到 `;` 还是 `{`。"""
        d = 0
        k = 0
        while True:
            kind, text, _ln = self.peek(k)
            if kind == "eof":
                return False
            if text in ("(", "["):
                d += 1
            elif text in (")", "]"):
                d -= 1
            elif d == 0 and text == ";":
                return True
            elif d == 0 and text == "{":
                return False
            k += 1

    def infer_ty(self, e: object) -> str:
        """`:=` / `var x = e` 推出来的 Loment 类型。

        **Go 这一门能推得比 C / C# 准，因为 Go 自己就那么严**：
        它的运算符**要求两侧类型完全相同**（没有隐式数值转换），所以
        `a % b` 的类型就是 `a` 的类型 —— 取左边就对。而 `int(x)` 这种转换
        **把类型写在源码里了**，直接读它。

        推不出更精确的时候给 `GO_INT`（`int` 在 x86-64 上是 64 位）。
        第一版这里一律回 `i32`，于是 `t := a % b`（`a`/`b` 是 `int`）发成
        `let t: i32 = (a % b);` —— 两边类型对不上，是个**错的**产物。
        """
        if isinstance(e, Lit):
            return "bool" if e.b else GO_INT
        if isinstance(e, Var):
            return self.varty.get(e.name, GO_INT)
        if isinstance(e, Cast):
            return e.ty                      # 源码里写死的，最准
        if isinstance(e, Call):
            return self.fn_rets.get(e.name, GO_INT)
        if isinstance(e, Un):
            return "bool" if e.op == "!" else self.infer_ty(e.e)
        if isinstance(e, Bin):
            # Go 的比较与 `&&`/`||` 出 `bool`；其余两侧同类，取左边
            return "bool" if e.op in _BOOL_RESULT else self.infer_ty(e.l)
        return GO_INT

    #: 本单元所有函数的返回类型（调用点推断用），`unit()` 里填
    fn_rets: dict = {}

    # ---- 顶层
    def unit(self) -> list:
        out: list[Fn] = []
        # 先把函数签名扫一遍 —— **Go 的函数可以先用后声明**，而调用点的类型
        # （`x := f()` 推 int 还是 bool）要提前知道。
        self.fn_rets = self._scan_sigs()
        while self.peek()[0] != "eof":
            t = self.peek()
            if t[1] == ";":
                self.i += 1          # 自动分号：函数体那个 `}` 后面会补一个
                continue
            if t[1] == "package":
                self.i += 1
                self.ident()
                continue
            if t[1] == "import":
                raise Unsupported(f"第 {t[2]} 行: 不支持 `import`"
                                  f"（外部函数要用什么，在这一层表达不了）")
            if t[1] == "type":
                raise Unsupported(f"第 {t[2]} 行: 不支持 `type` 声明")
            if t[1] == "const":
                raise Unsupported(f"第 {t[2]} 行: 不支持顶层 `const`")
            if t[1] == "var":
                raise Unsupported(f"第 {t[2]} 行: 不支持顶层 `var`（包级变量）")
            if t[1] != "func":
                raise Unsupported(f"第 {t[2]} 行: 顶层只收 `func`，得到 {t[1]!r}")
            out.append(self.fn_decl())
        return out

    def _scan_sigs(self) -> dict:
        """只扫签名，不动位置：`名字 -> 返回类型`。"""
        save = self.i
        rets: dict = {}
        try:
            while self.peek()[0] != "eof":
                if self.at("func"):
                    self.i += 1
                    if self.at("("):
                        break               # 方法是另一回事
                    name, _ = self.ident()
                    self._skip_params()
                    r = self._peek_ret()
                    rets[name] = r
                    self._skip_brace()
                else:
                    self.i += 1
        except (GoError, Unsupported):
            pass
        finally:
            self.i = save
        return rets

    def _skip_params(self) -> None:
        self.want("(")
        d = 1
        while d:
            t = self.peek()
            if t[0] == "eof":
                raise GoError("形参表没闭合")
            if t[1] == "(":
                d += 1
            elif t[1] == ")":
                d -= 1
            self.i += 1

    def _peek_ret(self) -> str:
        if self.at("{"):
            return "()"
        if self.at("("):
            raise Unsupported("第 "
                              f"{self.peek()[2]} 行: 不支持多返回值")
        return self.go_type("返回类型")

    def _skip_brace(self) -> None:
        while not self.at("{"):
            if self.peek()[0] == "eof":
                raise GoError("函数体没找到")
            self.i += 1
        self.want("{")
        d = 1
        while d:
            t = self.peek()
            if t[0] == "eof":
                raise GoError("函数体没闭合")
            if t[1] == "{":
                d += 1
            elif t[1] == "}":
                d -= 1
            self.i += 1

    def fn_decl(self) -> Fn:
        t = self.want("func")
        if self.at("("):
            raise Unsupported(f"第 {t[2]} 行: 不支持方法（Go 的接收者）")
        name, line = self.ident()
        params = self.params()
        ret = self._peek_ret()
        self.ret = ret
        self.varty = {n: ty for (ty, n, _l) in params}
        body = self.block()
        return Fn(ret, name, params, body, line)

    def params(self) -> list:
        """`(a int, b, c int)` —— **同类型可以共享**（Go 的写法）。"""
        self.want("(")
        out: list = []
        if not self.at(")"):
            while True:
                # Go 的同类型共享写法：`a, b int` 与 `a int, b int` **都要收**。
                # 分辨办法与 Go 自己一样：先尽量吃一个**标识符列表**（逗号分），
                # 后面**必然是类型** ——
                #   `a int, b int` → 吃到 `a` 就停（下一个是 `int` 不是 `,`），类型 `int`
                #   `v, lo, hi int` → 吃到 `hi`，类型 `int`，三个共享
                names = [self.ident()[0]]
                while self.at(",") and self.peek(1)[0] == "id":
                    self.i += 1
                    names.append(self.ident()[0])
                ty = self.go_type("形参")
                for n in names:
                    out.append((ty, n, self.peek()[2]))
                if not self.at(","):
                    break
                self.i += 1
        self.want(")")
        return out


# ---------------------------------------------------------------- 发射


class Emitter:
    """一个函数一份。**Go 这一份是自足的** —— 与 `trans_core.Emitter` 同形，
    但按 Go 的语义写（条件无括号、`!` 收 bool、`:=` 的类型是推出来的）。"""

    def __init__(self, fns: dict[str, str], int_default: str = "i32") -> None:
        self.fns = fns
        self.int_default = int_default
        self.vars: dict[str, str] = {}
        self.varty: dict[str, str] = {}
        #: **块作用域栈**（`#235` / `#237`）。与 `trans_core` 里 C 那门同一个手法：
        #: 块里的遮蔽声明改名，出块时把外层原值还回去。Go 有块作用域而本语言没有，
        #: 不补这一层，同一个程序在 `grammar go` 与 `grammar c` 下算出不同的数
        #: （`#237` 实例 1：`int y` 遮蔽后 Go 读 50、C 读 10）。
        self.scopes: list[dict[str, tuple[str | None, str | None]]] = []
        self.ret = "()"
        self.n = 0
        self.lines: list[str] = []

    def decl_bind(self, name: str, line: int) -> str:
        """一条声明要发出去的名字 —— 保住源语言的块作用域（同 `trans_core.decl_bind`）。"""
        if self.scopes:
            cur = self.scopes[-1]
            if name in cur:
                raise Unsupported(
                    f"第 {line} 行: `{name}` 在同一个块里声明了两次（Go 里这是编译错）")
            old = self.vars.get(name)
            cur[name] = (old, self.varty.get(name))
            if old is not None:
                nm = self.fresh(name)
                self.vars[name] = nm
                return nm
        elif name in self.vars:
            raise Unsupported(
                f"第 {line} 行: `{name}` 在同一个函数里声明了两次（Go 里这是编译错）")
        self.vars[name] = name
        return name

    def block(self, body: list, depth: int) -> None:
        """发一个块并**保住源语言的块作用域**（`if` / `while` / `for` 的体）。"""
        saved: dict[str, tuple[str | None, str | None]] = {}
        self.scopes.append(saved)
        try:
            self.stmts(body, depth)
        finally:
            self.scopes.pop()
            for name, (old, oldty) in saved.items():
                if old is None:
                    self.vars.pop(name, None)
                    self.varty.pop(name, None)
                else:
                    self.vars[name] = old
                    self.varty[name] = oldty      # type: ignore[assignment]

    def fresh(self, base: str) -> str:
        while True:
            self.n += 1
            out = f"{base}_go__{self.n}"
            if out not in self.vars.values():
                return out

    def out(self, s: str, depth: int) -> None:
        self.lines.append("    " * (depth + 1) + s)

    def gap(self) -> None:
        if not self.lines or self.lines[-1] == "" or self.lines[-1].endswith("{"):
            return
        self.lines.append("")

    def ty_of(self, e: object) -> str:
        if isinstance(e, Lit):
            return "bool" if e.b else "int"
        if isinstance(e, Var):
            return "bool" if self.varty.get(e.name) == "bool" else "int"
        if isinstance(e, Call):
            r = self.fns.get(e.name)
            if r is None:
                raise Unsupported(f"第 {e.line} 行: 调用了本单元没有的函数 `{e.name}`")
            return "void" if r == "()" else ("bool" if r == "bool" else "int")
        if isinstance(e, Un):
            return "bool" if e.op == "!" else "int"
        if isinstance(e, Cast):
            return "bool" if e.ty == "bool" else "int"
        if isinstance(e, Bin):
            return "bool" if e.op in _BOOL_RESULT else "int"
        raise AssertionError(type(e))

    def want_of(self, ty: str) -> str:
        return "bool" if ty == "bool" else "int"

    def ex(self, e: object, want: str) -> str:
        got = self.ty_of(e)
        if got == "void":
            raise Unsupported(f"第 {e.line} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e)
        if got == want:
            return raw
        # **Go 两个方向都不补** —— 它的 `&&` 出 bool、条件只收 bool，
        # 而且**没有** `bool ↔ int` 的隐式转换（`if x`（x 是 int）在 Go 里编不过）。
        if got == "bool" and want == "int":
            raise Unsupported(
                f"第 {e.line} 行: 这里要的是**整数**，给的是布尔。"
                f"Go 的布尔与整数**不是一回事**（不像 C 那样能互相顶），所以这里本来就该"
                f"是个整数表达式；真要 0/1 得显式写（本子集暂不收那个转换）")
        raise Unsupported(
            f"第 {e.line} 行: 这里要的是**条件**，给的是整数。"
            f"Go 的条件**只收 bool**（`if x`（x 是 int）在 Go 里本来就编不过），"
            f"所以这里本来就该是个布尔表达式（多半写错了）")

    def raw(self, e: object) -> str:
        if isinstance(e, Lit):
            return ("true" if e.v else "false") if e.b else str(e.v)
        if isinstance(e, Var):
            if e.name not in self.vars:
                raise Unsupported(f"第 {e.line} 行: 用了没声明过的 `{e.name}`")
            return self.vars[e.name]
        if isinstance(e, Call):
            return f"{e.name}({', '.join(self.ex(a, 'int') for a in e.args)})"
        if isinstance(e, Un):
            if e.op == "!":
                # **Go 的 `!` 收 bool** —— 与 Loment 一致，原样发
                return f"(!{self.ex(e.e, 'bool')})"
            return f"(-{self.ex(e.e, 'int')})"
        if isinstance(e, Cast):
            # **Go 的显式转换就是 Loment 的 `as`** —— 一字不差地过去
            want = "bool" if e.ty == "bool" else "int"
            return f"({self.ex(e.e, want)} as {e.ty})"
        if isinstance(e, Bin):
            if e.op in _BOOL_RESULT:
                if e.op in ("&&", "||"):
                    return f"({self.ex(e.l, 'bool')} {e.op} {self.ex(e.r, 'bool')})"
                return f"({self.ex(e.l, 'int')} {e.op} {self.ex(e.r, 'int')})"
            return f"({self.ex(e.l, 'int')} {e.op} {self.ex(e.r, 'int')})"
        raise AssertionError(type(e))

    # ---- 语句
    def stmts(self, body: list, depth: int) -> None:
        for s in body:
            self.stmt(s, depth)

    def stmt(self, s: object, depth: int) -> None:
        if isinstance(s, Decl):
            nm = self.decl_bind(s.name, s.line)
            self.varty[s.name] = s.ty
            if s.init is None:
                self.out(f"let {nm}: {s.ty} = 0;", depth)
            else:
                self.out(f"let {nm}: {s.ty} = "
                         f"{self.ex(s.init, self.want_of(s.ty))};", depth)
            return
        if isinstance(s, Assign):
            if s.name not in self.vars:
                raise Unsupported(f"第 {s.line} 行: 赋值给没声明过的 `{s.name}`")
            self.out(f"{self.vars[s.name]} = "
                     f"{self.ex(s.e, self.want_of(self.varty.get(s.name, 'int')))};", depth)
            return
        if isinstance(s, Return):
            want = self.want_of(self.ret)
            self.out("return;" if s.e is None else
                     f"return {self.ex(s.e, want)};", depth)
            return
        if isinstance(s, If):
            self.gap()
            self.out(f"if {self.ex(s.cond, 'bool')} {{", depth)
            self.block(s.then, depth + 1)
            if s.els is None:
                self.out("}", depth)
            else:
                self.out("} else {", depth)
                self.block(s.els, depth + 1)
                self.out("}", depth)
            return
        if isinstance(s, While):
            self.gap()
            cond = "true" if s.cond is None else self.ex(s.cond, "bool")
            self.out(f"while {cond} {{", depth)
            self.block(s.body, depth + 1)
            self.out("}", depth)
            return
        if isinstance(s, For3):
            self.for_(s, depth)
            return
        if isinstance(s, ExprStmt):
            if not isinstance(s.e, Call):
                raise Unsupported(f"第 {s.line} 行: 表达式语句只收函数调用")
            self.out(f"{self.raw(s.e)};", depth)
            return
        raise AssertionError(type(s))

    def for_(self, s: For3, depth: int) -> None:
        """`for init; cond; post { }` -> `init; while cond { body; post; }`。

        **Loment 没有裸块**，所以 `for i := 0; …` 里那个 `i` 只能改名外提 ——
        与 `trans_core` 里 C 那门同一个手法（那边探过：`15:5 期望表达式，得到 '{'`）。
        """
        self.gap()
        saved = None
        if isinstance(s.init, Decl):
            old = self.vars.get(s.init.name)
            oldty = self.varty.get(s.init.name)
            new = self.fresh(s.init.name)
            self.vars[s.init.name] = new
            self.varty[s.init.name] = s.init.ty
            if s.init.init is None:
                self.out(f"let {new}: {s.init.ty} = 0;", depth)
            else:
                self.out(f"let {new}: {s.init.ty} = "
                         f"{self.ex(s.init.init, self.want_of(s.init.ty))};", depth)
            saved = (s.init.name, old, oldty)
        elif s.init is not None:
            self.stmt(s.init, depth)

        cond = self.ex(s.cond, "bool") if s.cond is not None else "true"
        self.out(f"while {cond} {{", depth)
        self.block(s.body, depth + 1)
        if s.post is not None:
            self.stmt(s.post, depth + 1)
        self.out("}", depth)

        if isinstance(s.init, Decl) and saved is not None:
            name, old, oldty = saved
            if old is None:
                del self.vars[name]
                self.varty.pop(name, None)
            else:
                self.vars[name] = old
                self.varty[name] = oldty    # type: ignore[assignment]

    def emit_fn(self, f: Fn) -> str:
        self.vars = {n: n for (_t, n, _l) in f.params}
        self.varty = {n: t for (t, n, _l) in f.params}
        self.ret = f.ret
        self.n = 0
        self.lines = []
        ps = ", ".join(f"{n}: {t}" for (t, n, _l) in f.params)
        head = f"pub fn {f.name}({ps})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        self.lines.append(head + " {")
        self.stmts(f.body, 0)
        self.lines.append("}")
        return "\n".join(self.lines)


# ---------------------------------------------------------------- 入口


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """Go 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    `consts` 收下不用（这一门不收顶层 `const`）；留着是为了与另外几门同形，
    好让 `lomt_from` 的分发表一行一条。
    """
    p = Parser(tokens(src))
    fns = p.unit()
    rets = {f.name: f.ret for f in fns}
    if externs:
        rets.update(externs)
    seen: set[str] = set()
    for f in fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.line} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)
    out = []
    for f in fns:
        if keep is not None and f.name not in keep:
            continue
        out.append(Emitter(rets).emit_fn(f))
    return "\n\n".join(out) + "\n"


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    return Parser(tokens(src)).unit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gotrans", description="Go 写法 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"))
    except Unsupported as e:
        print(f"[ERR] 子集外: {e}", file=sys.stderr)
        return 1
    except GoError as e:
        print(f"[ERR] 解析失败: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"[ERR] 读取失败: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"[OK] {a.path} -> {a.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
