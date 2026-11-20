#!/usr/bin/env python3
"""nltrans.py — **自然语言写法 -> Loment**（`docs/197`）。

     自然语言写法 --nltrans--> Loment

## 它是什么，不是什么

`docs/188` §0 把"表层语法"这件事框正了：**只让拼法与形状，不让语义**。
这一门是那条规矩的**极端样本** —— 前面六门（C / Python / Java / C# / C++ / Go）是
**别人已经存在的语言**，翻译器只能"能表达的就转，表达不出来的就报错"；而这一门
是**我们自己发明的**，所以那条子集线是**画出来的**，不是**碰上的**：

* 凡是画进子集的句子，翻出来的 Loment 与手写的一模一样（判据钉着"逐字节"）；
* 凡是没画的，**在词法/解析那一步就拒**，并说清这一版不收什么。

## 形状：**一个动词起头的句子 = 一条语句**

自然语言的骨架是"动词 + 宾语"，所以这一门的语法就是这句话的实现：
**语句以换行为界**（自然语言里没有分号），块用 `end` 收尾（缩进只是给人看的）。

### 顶层（声明）

| 句子 | 落成的 Loment |
|---|---|
| `program tour` | `module tour`（第一句，必须有） |
| `use json` / `use "pack.lomt"` | `use json` / `use "pack.lomt"` |
| `the standard library is not available` | `choose no_std` |
| `remember LIMIT as 3` | `pub const LIMIT: i64 = 3;` |
| `a Point has x as a whole number and y as a whole number` | `pub struct Point { x: i64, y: i64 }` |
| `a Kind is either Small or Big carrying a whole number` | `pub enum Kind { Small, Big(u32) }` |
| `a Sizer can size giving a count` | `pub trait Sizer { fn size(self) -> u32; }` |
| `a Point can be a Sizer` … `end` | `impl Sizer for Point { … }` |
| `a disk space called slots covers 0 to 4, and it can be taken back` | `capability slots : disk[0..4] revocable` |
| `leave out "the network"` | `excluded "the network"` |
| `someone else wrote read_at with fd as a 32-bit count giving a whole number` | `pub extern fn read_at(fd: u32) -> i64;` |
| `to add with a as a whole number and b as a whole number giving a whole number` … `end` | `pub fn add(a: i64, b: i64) -> i64 { … }` |

声明末尾写 `, only here` 就去掉 `pub`（`remember` 除外，见 §边界）。

### 语句

| 句子 | 落成的 Loment |
|---|---|
| `let n be 5` / `let b be 200 as a byte` | `let n: i64 = 5;` / `let b: u8 = 200;` |
| `let fb be a buffer of 1024 bytes` | `let fb: ptr = alloc(1024);` |
| `set n to 6` / `set item 0 of xs to 9` | `n = 6;` / `xs[0] = 9;` |
| `say "hi"` / `say the number n` | 写标准输出（文本 / 十进制数） |
| `talk to the machine 60 with n, 0, 0` | `syscall4(n, a, b, c)` |
| `paint 255 at 0 in fb` | `store8(fb, 0, 255 as u8)` |
| `do f of 3` | `f(3);` |
| `give back n times 2` | `return (n * 2);` |
| `when n is above 3` … `otherwise when …` … `otherwise` … `end` | `if … { } else if … { } else { }` |
| `while n is above 0` … `end` | `while … { }` |
| `for i from 0 to 10` … `end` | `for i in 0..10 { }`（上界**不含**） |
| `when k looks like a Kind that is Big carrying w` … `end` | `match k { Kind::Big(w) => { … } }`；**只写一条且没有 `when anything else` 就是 `if let`** |
| `when anything else` … `end` | `_ => { … }`（match 的通配臂） |
| `guard the slots space at 2` | `guard slots(2);` |

### 值

| 写法 | 落成的 Loment | | 写法 | 落成的 Loment |
|---|---|---|---|---|
| `a Point with x as 1 and y as 2` | `Point { x: 1, y: 2 }` | | `the x of p` | `p.x` |
| `a Kind that is Big carrying 5` | `Kind::Big(5)` | | `the list 1, 2, 3` | `[1, 2, 3]` |
| `item 0 of xs` | `xs[0]` | | `the length of xs` | `slice_len(xs)` |
| `the run of xs` | `&xs` | | `the changeable run of xs` | `&mut xs` |
| `ask p for size` | `p.size()` | | `<e> unless it failed` | `<e>?` |
| `something carrying x` | `Option::Some(x)` | | `nothing to carry` | `Option::None` |
| `a success carrying x` | `Result::Ok(x)` | | `a failure carrying x` | `Result::Err(x)` |
| `f of a, b`（调用） | `f(a, b)` | | 没有实参就直接写名字 | `f()` |

### 类型短语

| 自然语言 | Loment | | 自然语言 | Loment |
|---|---|---|---|---|
| `a whole number` / `a 32-bit whole number` | `i64` / `i32` | | `a byte` | `u8` |
| `a count` / `a 64-bit count` | `u32` / `u64` | | `a truth` | `bool` |
| `text` / `a buffer` | `str` / `ptr` | | `nothing` | `()` |
| `a list of 3 whole numbers` | `[i64; 3]` | | `a run of whole numbers` | `[i64]` |
| `a changeable run of whole numbers` | `mut [i64]` | | `a Point`（结构体/枚举名） | `Point` |
| `maybe a whole number` | `Option<i64>` | | `a whole number or a failure of text` | `Result<i64, str>` |

`a` / `an` 可省；`i64` / `u8` 这些**直接写也收**（要跟别人说同一件事时，缩写省事）。

## 三条**不是翻译、是决定**的东西

1. **`say` 分两句**（`say <文本>` / `say the number <数>`）。合并就要**猜表达式的类型**，
   而猜错的表现是"把一段文本按数去打"，那种错**两边都编得过**。
2. **类型是"查"出来的，不是"推"出来的**（`Parser.type_of`）。查得到的是：句子里写的
   `as <类型>`、字面量、**声明过的名字**（常量/形参/前面的 `let`）、**运算符的定则**
   （比较与 `and`/`or` 出 `truth`、同型则同型）、**被调函数的声明**、**结构体字段**、
   **方法声明**、**容器的元素类型**。够不着就**报错**，不默认按 i64 算 ——
   后者会把一个真的类型错推后到别处炸。
3. **续行只认"不可能当名字"的那些词**（`_CONTINUE`）。多收一个比少收一个坏：
   少收是"合法的一句读不通"（响亮），多收是"两句悄悄变一句"（见下面那条踩出来的）。

## 一处**踩出来的坑**（写进代码里的理由）

发出来的 `say the number` 那个辅助函数（`nl_write_num`）里，`0` **必须**先落成一个
带类型的局部量（`let zero: i64 = 0;`），不能直接在表达式里写 `0 - v`：

    实测（安装版 0.1.4 的自举驱动）：`let a: i64 = 0 - 12345;` 之后 `a as u64` 给的是
    4294954951（= 2^32 - 12345），而参考实现为同一句发的是 `sub i64 0, 12345`。
    也就是说**无后缀字面量在自举驱动里被当成了 u32**，`i64` 上下文里的取负于是 32 位回绕。

取负这件事在**运行期的 i64 变量**上是好的（实测 `let zero: i64 = 0; zero - v` 对
`v = -12345` 给 12345），所以辅助函数走"带类型的零"这条路，而不去碰字面量运算。
**这条不是绕过一个翻译器的限制，是绕过一个编译器的 bug** —— 两边都编得过、只有一边
算错，是本项目的红线；上面那个复现留给工具链那一侧（见 `docs/197` §7）。

## 这一版**不收**的（每条都有理由，不是没做）

* **`Option` / `Result` 的模式**（`when x looks like …` 用在它们身上）——
  **这是语言层面的边界，不是这一门的**：判据把写出来的名字与**单态化名**比
  （`Option::Some` 对 `Option_u32::Some`），而那是编译器的内部拼法。它们的**类型、
  构造、`?`** 都收。
* **`addin`**（开关设定单元）与 **`comefor` / `byuse`**（在源码里定义新语法）——
  前者是构建期的东西，后者**本身**就是在定义一门语法；两者都没有可判的语料。
* **`break` / `continue`**：Loment 没有这两个词（实测 `使用未声明的变量 break`）。
* **`for` 遍历切片**：Loment 的 `for` **只有**区间那一种（实测 `for x in xs` 过不了）。

用法:

    python tools/nltrans.py SRC.nl [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------- 异常

class NaturalError(Exception):
    """**写法本身就不对**（缺 `end`、句子读不通、类型短语拼错…）。

    与 `Unsupported` 分开：那个是"这一句读得通、但这一版不翻"（`docs/188` §5 第 2 条
    "子集外 —— 还是报错，不是声明能救的"）。分开的理由与 `docs/179` §6.5 那条一样：
    **错要指在错的地方**，而"你写错了"与"我还没做"是两件事。
    """


class Unsupported(Exception):
    """**读得通、翻不出来** —— 子集外（含语言层面的边界）。消息里必须点名是哪一处。"""


# ---------------------------------------------------------------- 词法

#: 运算符的词形与符号形**同一处**（"拼法可以有别名"那条纪律也适用于运算符）。
#: 值一律是 Loment 的运算符。多词的那些在解析器里按**最长匹配**处理。
_WORD_OPS = {
    "plus": "+", "minus": "-", "times": "*", "over": "/", "modulo": "%",
    "and": "&&", "or": "||",
    "is": "==", "is not": "!=", "is above": ">", "is below": "<",
    "is at least": ">=", "is at most": "<=",
    "shifted left by": "<<", "shifted right by": ">>",
}

#: 符号形的运算符 —— **只剩位运算那一族**。
#:
#: 它们**没有**自然语言说法（"按位与"是黑话，不是人话），所以符号是它们**唯一**的
#: 写法；其余每一个运算符都只留**词形**（`plus` / `is above` / `shifted left by` …）。
#:
#: ## 为什么必须"一个运算符一种写法"
#:
#: 2026-09-22 用户点出这一门**过度复杂**，量出来的头一条就是它：15 个运算符
#: **每个都有两种写法**（`plus` 与 `+`、`is above` 与 `>` …）—— 学一遍不够，得学两遍，
#: 而两遍说的是同一件事。**第二种写法一分钱不值**：它只是把"这一门比 Loment 简单"
#: 这件事抹掉。指认一个运算符，必须只有一个答案。
_SYMBOL_OPS = {"&", "|", "^"}

#: **6 个英文基名**（类型短语的全部）。其余类型一律照 Loment 写（见 `type_phrase`）。
#:
#: `a` / `an` 可省；复数也收（`whole numbers`）。
_TYPE_WORDS = {
    "whole number": "i64",
    "count": "u32",
    "byte": "u8",
    "truth": "bool",
    "text": "str",
    "buffer": "ptr",
}

#: 上面那六个的 Loment 拼法，以及其余全部直接写的类型名。
#: **这一张表的用处只有"合法吗"**（`type_atom` 拿它把"认得的类型名"与"随便一个标识符"
#: 分开）；真正的拼法是原样带过去，不翻译。
_TYPE_DIRECT = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
                "bool", "str", "ptr", "()"}

#: 内建函数（`.claude/skills/loment/SKILL.md` §3 那张表，**全部，没有别的**）。
#: 收进来的用处只有一条：**调用点报错时能分清"你名字写错了"与"这一版不收"**，
#: 外加让 `let n be load8 of buf, 0` 这种句子查得到类型。
_BUILTINS = {
    "str_len": "u32", "str_byte": "u32", "str_eq": "bool", "str_concat": "str",
    "str_ptr": "ptr", "alloc": "ptr", "free": "u32", "load8": "u32",
    "store8": "u32", "ptr_add": "ptr", "ptr_sub": "ptr", "slice_len": "u32",
    "panic": "u32", "atomic_add": "u32", "get_bits": "u8", "set_bits": "u8",
    "inb": "u32", "outb": "u32", "syscall4": "i64", "syscall6": "i64", "syscall7": "i64",
}

#: **保留词**。它们进了语法之后就不能再当名字用 —— 一份 `let has be 3` 会让解析器
#: 在很远的地方报一句看不懂的错。在这里**点名拒**，是 `docs/179` §6.5 那条
#: "错要指在错的地方"的直接兑现。
_RESERVED = {
    # **运算符**（当名字用会读错）
    "and", "or", "not", "is", "as", "plus", "minus", "times", "over", "modulo",
    "shifted",
    # **语句/声明的骨架词**（它们出现在"下一个记号决定这一句是什么"的位置）
    "program", "use", "remember", "to", "with", "giving", "let", "be", "set",
    "say", "the", "talk", "paint", "give", "do", "when", "otherwise", "while",
    "for", "from", "end",
    # **值的头一个词**：一个叫 `something` 的变量会让 `say something` 走进那条支。
    # `the … of …` 那一族的词（`item` / `length` / `run` / `list`）**不收** ——
    # 它们只在 `the` 后面有词义，别处就是普通名字。
    "something", "nothing", "success", "failure",
}
#: **故意不在表里的那些**（它们是词，但**可以**当名字）：
#: * `a` / `an` —— 类型短语里的冠词**可省**，而"省略"这件事正是它们能当名字的原因。
#:   第一版就是在这里踩的（`let x be a` 里那个 `a` 是形参名，见 `_CONTINUE` 的注解）；
#:   把 `a` 收进保留词表会把一份完全正常的 `to add with a as … and b as …` 顶掉。
#: * `list` / `run` / `length` / `changeable` —— 它们只在 `the` 后面才有词义，
#:   别处就是个普通名字（`to run giving …` 是本仓语料里的真函数）。
#: * `revocable` / `available` / `standard` / `library` / `made` —— 同上。

#: **行尾是这些词/符号时，换行不算句界**（一句话写到下一行是常态：函数头、
#: 参数表、算式都可能换行）。
#:
#: 少一个的症状是"明明是合法的一句，却在中间报'这句读不通'"；**多一个更坏** ——
#: 它把两句**悄悄粘成一句**。这一格是踩出来的：第一版把 **`a` / `an`** 也放了进来
#: （想着 `as a byte` 那种类型短语），于是
#:
#:     let x be a          <- `a` 是个**形参名**
#:     let y be b
#:
#: 两行被粘成一句 —— 而报错落在第二行上，说的是"这里该断了却还有 `let`"，
#: 与真正的原因（第一版多收了 `a`）隔着一层。**现在只收"不可能是名字"的那些**：
#: 运算符（含多词运算符的每一个词）、标点、以及结构关键字。
_CONTINUE = set(_SYMBOL_OPS) | {"(", ",", "the", "be", "as", "of", "with",
                                "in", "from", "to", "giving"}
_CONTINUE |= {w for op in _WORD_OPS for w in op.split()}
#: v2 新加的**声明**词（它们都不可能当名字，见 `_RESERVED`）—— 声明写不下要能换行。
#: 只收**声明头**上那几个（它们写在行尾时下一行多半还是这一句）。
#: `called` / `keeps` / `any` / `someone` / `else` / `wrote` **故意不收** ——
#: 它们是普通英文词，当名字用完全正常（`let other be called` 就是一份真语料），
#: 收了会把两句悄悄粘成一句。
_CONTINUE |= {"has", "either", "carrying", "can", "space", "covers"}

#: 声明末尾那个"只给自己看"的后缀。与 `pub` 相反 —— 见 `docs/197` §2。
_KEEP = (",", "only", "here")


class Tok:
    __slots__ = ("kind", "text", "line", "pos")

    def __init__(self, kind: str, text: str, line: int, pos: int):
        self.kind, self.text, self.line, self.pos = kind, text, line, pos

    def __repr__(self) -> str:  # pragma: no cover - 只在调试时看到
        return f"{self.kind}:{self.text}@{self.line}"


def _is_id_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_id(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(src: str) -> list[Tok]:
    """源文本 -> 记号流。**只有 `nl` 是句界**，缩进不进语法（缩进是给人看的）。"""
    out: list[Tok] = []
    i, line, n = 0, 1, len(src)
    while i < n:
        ch = src[i]
        if ch == "\n":
            out.append(Tok("nl", "\n", line, i))
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if _is_id_start(ch):
            j = i
            while j < n and _is_id(src[j]):
                j += 1
            word = src[i:j]
            if word == "note":          # `note ...` 是自然语言的注释
                while j < n and src[j] != "\n":
                    j += 1
                i = j
                continue
            out.append(Tok("id", word, line, i))
            i = j
            continue
        if ch.isdigit():
            j = i
            if src.startswith("0x", i) or src.startswith("0X", i):
                j = i + 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            out.append(Tok("num", src[i:j], line, i))
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    j += 1
                if j < n and src[j] == "\n":
                    raise NaturalError(f"第 {line} 行: 字符串没有收尾的 `\"`")
                j += 1
            if j >= n:
                raise NaturalError(f"第 {line} 行: 字符串没有收尾的 `\"`")
            out.append(Tok("str", src[i:j + 1], line, i))
            i = j + 1
            continue
        two = src[i:i + 2]
        if two in ("<<", ">>", "==", "!=", "<=", ">=", "&&", "||"):
            out.append(Tok("sym", two, line, i))
            i += 2
            continue
        if ch in "+-*/%&|^!<>(),.[];":
            out.append(Tok("sym", ch, line, i))
            i += 1
            continue
        raise Unsupported(f"第 {line} 行: 认不出的字符 `{ch}`"
                          f"（`docs/197` §边界：这一版只收动词起头的句子）")
    out.append(Tok("nl", "\n", line, n))

    # ---- 续行：上一个是运算符/逗号/连接词时，这个换行不算句界
    merged: list[Tok] = []
    for t in out:
        if t.kind == "nl":
            if not merged or merged[-1].kind == "nl":
                continue                      # 空行/连续换行 -> 合成一个
            if merged[-1].text in _CONTINUE:
                continue
        merged.append(t)
    return merged


# ---------------------------------------------------------------- 语法树

class Fn:
    """一个函数：名字、泛型形参、形参、返回类型、**原文**、原文里的起始行、语句。"""

    __slots__ = ("name", "params", "ret", "body", "line", "stmts", "generics", "pub")

    def __init__(self, name: str, params: list[tuple[str, str]], ret: str,
                 body: str, line: int, stmts: list,
                 generics: tuple[str, ...] = (), pub: bool = True):
        self.name, self.params, self.ret = name, params, ret
        self.body, self.line, self.stmts = body, line, stmts
        self.generics, self.pub = generics, pub


class Const:
    __slots__ = ("name", "ty", "value", "line", "pub")

    def __init__(self, name: str, ty: str, value: int, line: int, pub: bool = True):
        self.name, self.ty, self.value, self.line, self.pub = name, ty, value, line, pub


class Struct:
    __slots__ = ("name", "fields", "line", "pub", "generics", "text")

    def __init__(self, name, fields, line, pub, generics, text):
        self.name, self.fields = name, fields
        self.line, self.pub, self.generics, self.text = line, pub, generics, text


class Enum:
    __slots__ = ("name", "variants", "line", "pub", "generics", "text")

    def __init__(self, name, variants, line, pub, generics, text):
        self.name, self.variants = name, variants
        self.line, self.pub, self.generics, self.text = line, pub, generics, text


class Trait:
    __slots__ = ("name", "method", "ret", "line", "pub", "text")

    def __init__(self, name, method, ret, line, pub, text):
        self.name, self.method, self.ret = name, method, ret
        self.line, self.pub, self.text = line, pub, text


class Impl:
    __slots__ = ("target", "trait", "methods", "line", "text")

    def __init__(self, target, trait, methods, line, text):
        self.target, self.trait, self.methods = target, trait, methods
        self.line, self.text = line, text


class Decl:
    """**只带原文与位置**的那一类声明（`use` / `choose`）—— 给 `potato_from` 造载体用。"""

    __slots__ = ("name", "text", "line")

    def __init__(self, name: str, text: str, line: int):
        self.name, self.text, self.line = name, text, line


class Program:
    __slots__ = ("unit", "uses", "mode", "mode_decl", "consts", "structs", "enums",
                 "traits", "impls", "caps", "excluded", "fns", "externs")

    def __init__(self):
        self.unit = ""
        self.uses: list[Decl] = []
        self.mode = ""                      # "" | "std" | "no_std"
        self.mode_decl: Decl | None = None
        self.consts: list[Const] = []
        self.structs: list[Struct] = []
        self.enums: list[Enum] = []
        self.traits: list[Trait] = []
        self.impls: list[Impl] = []
        self.caps: list[tuple] = []         # (名字, 空间, lo, hi, revocable, 行)
        self.excluded: list[tuple[str, int]] = []
        self.fns: list[Fn] = []
        self.externs: list[Fn] = []         # 无正文的 `pub extern fn`


# ---------------------------------------------------------------- 解析器

class Parser:
    def __init__(self, toks: list[Tok], src: str, what: str = "这份源",
                 consts: dict[str, str] | None = None,
                 calls: dict[str, str] | None = None,
                 strict_types: bool = True):
        self.t, self.src, self.what = toks, src, what
        self.i = 0
        #: **函数名 -> 返回类型**（= 内建表 + 外部声明 + 本文件各 `to … giving`）。
        #: 它让 `let a be sum_to of LIMIT` 不必再补 `as` —— 那是**查声明**，不是推断。
        self.calls: dict[str, str] = dict(_BUILTINS)
        self.calls.update(calls or {})
        #: 第一遍（收声明表的那遍）为 `False`：那时表还没齐，说不出类型的 `let`
        #: 先放过，由第二遍来报。
        self.strict_types = strict_types
        #: **已知名字的类型**：模块常量（`consts` 那份表 / 本文件里的 `remember`）、
        #: 形参、已经读过的 `let`。它只服务一件事：`let v be n` 里那个 `n` 的
        #: 类型是**查出来的**，不是猜的。
        self.tenv: dict[str, str] = dict(consts or {})
        self.const_names: set[str] = set(consts or {})
        #: 结构体/枚举/泛型形参/方法 —— 三张表，`type_of` 靠它们把"查"做全。
        self.structs: dict[str, dict[str, str]] = {}      # 名字 -> {字段: 类型}
        self.variants: dict[str, dict[str, bool]] = {}    # 名字 -> {变体: 有没有载荷}
        self.gparams: set[str] = set()                    # 当前函数/类型的泛型形参
        #: **"这一个表达式里不许把 `of` 读成调用"**。`item <下标> of <东西>` 与
        #: `set item <下标> of <东西> to …` 里，下标后面**紧跟一个 `of`**，而
        #: 冠词那一层分不出"这是我的 `of`"还是"这是调用的 `of`" ——
        #: `item i of xs` 会被读成 `i(xs)`。所以这两处**把下标那一层的调用识别关掉**，
        #: 需要嵌套调用时用括号（括号会把它打开，因为括号是显式的）。
        self.no_call_of = False
        #: `choose std` / `choose no_std` 那一格（`the standard library is [not] available`）
        self.mode = ""
        self.methods: dict[tuple[str, str], str] = {}     # (类型, 方法) -> 返回类型

    # ---- 记号流的小工具

    def cur(self) -> Tok:
        # **夹住下标**：句尾那个哨兵换行被吃掉之后 `self.i` 会走到 `len(t)`，
        # 而这时"当前记号"的正确含义是**最后一个**（哨兵）—— 夹住的话，所有
        # `self.cur().kind == "nl"` 的判据在文件末尾都自然成立，不必每处都先查边界。
        return self.t[self.i if self.i < len(self.t) else len(self.t) - 1]

    def peek(self, k: int = 1) -> Tok:
        j = self.i + k
        return self.t[j] if j < len(self.t) else self.t[-1]

    def at(self, *words: str) -> bool:
        return self.cur().kind != "nl" and self.cur().text in words

    def word_at(self, k: int, word: str) -> bool:
        t = self.peek(k)
        return t.kind == "id" and t.text == word

    def eat(self, word: str) -> bool:
        if self.at(word):
            self.i += 1
            return True
        return False

    def need(self, word: str, why: str = "") -> Tok:
        """**要求并吃掉**这个词。吃是它的一半职责 —— 只查不吃的话，每个调用点都得
        自己再补一句 `self.i += 1`，漏一处就是"卡在原地"（写第一版时正是这么卡的）。"""
        if not self.at(word):
            self.err(f"这里要写 `{word}`{('（' + why + '）') if why else ''}")
        t = self.t[self.i]
        self.i += 1
        return t

    def eat_word(self, *words: str) -> str | None:
        """吃一个**词**（不是符号）—— 多词形式按最长匹配，`is not` 优先于 `is`。"""
        best, bestk = None, 0
        for k, w in enumerate(words):
            parts = w.split()
            if all(self.word_at(j, p) for j, p in enumerate(parts)):
                if len(parts) > bestk:
                    best, bestk = w, len(parts)
        if best is None:
            return None
        self.i += bestk
        return best

    def err(self, msg: str) -> None:
        raise NaturalError(f"第 {self.cur().line} 行: {msg}")

    def ident(self, what: str) -> str:
        """读一个**名字** —— 保留词在这里点名拒（见 `_RESERVED`）。"""
        t = self.cur()
        if t.kind != "id":
            self.err(f"{what}要写名字，这里得到 `{t.text}`")
        if t.text in _RESERVED:
            self.err(f"`{t.text}` 是这一门的词，不能当{what}（`docs/197` 的语法表）")
        self.i += 1
        return t.text

    def end_sentence(self) -> None:
        if self.cur().kind == "nl":
            self.i += 1
            return
        if self._at_eof() or self.at("end", "otherwise"):
            return
        self.err(f"这一句到这里该断了，却还有 `{self.cur().text}`"
                 f"（自然语言写法**一句一行**，没有分号）")

    def _at_eof(self) -> bool:
        return self.cur().kind == "nl" and self.i >= len(self.t) - 1

    def keep_suffix(self) -> bool:
        """读声明末尾可选的 `, only here`。**读到了就返回 `True`**（= 不导出）。

        它是"私有"那一档；不写就是 `pub`。写成一句话而不是一个符号，是因为
        自然语言里"这条只有这里看得见"本来就是一句话，而不是一个修饰符。
        """
        save = self.i
        if self.at(",") and self.word_at(1, "only") and self.word_at(2, "here"):
            for _ in range(3):
                self.i += 1
            return True
        self.i = save
        return False

    # ---- 程序

    def program(self, need_program: bool) -> Program:
        prog = Program()
        while not self._at_eof():
            if self.cur().kind == "nl":
                self.i += 1
                continue
            # 顶层的两类：`to …` 与各种声明。**都以"这句的第一个词"分派。**
            if self.at("to"):
                prog.fns.append(self.function())
                continue                      # `function` 自己吃掉了句尾
            if self.at("program"):
                self.need("program")
                prog.unit = self.ident("模块名")
            elif self.at("use"):
                prog.uses.append(self.use_clause())
            elif self.at("the") and self.word_at(1, "standard") and self.word_at(2, "library"):
                prog.mode_decl = self.mode_clause()
                prog.mode = self.mode
            elif self.at("remember"):
                prog.consts.append(self.constant())
            elif self.at("a"):
                self.declaration(prog)
                continue                      # 声明自己吃掉了句尾
            elif self.at("leave"):
                prog.excluded.append(self.excluded_clause())
            elif self.at("someone"):
                prog.externs.append(self.extern_decl())
            elif self.at("guard", "set", "let", "say", "talk", "paint", "when",
                         "while", "for", "give", "do", "ask", "item", "run"):
                self.err(f"`{self.cur().text}` 是**语句**，只能写在函数体里")
            else:
                self.err(f"顶层不认识的句子 `{self.cur().text}`"
                         f"（顶层只有 `program` / `use` / `remember` / `to` / "
                         f"`a …` 声明 / `leave out` / `someone else wrote`）")
            self.end_sentence()
        if need_program and not prog.unit:
            raise NaturalError("第一句必须是 `program <模块名>` —— 没有它这一份就不知"
                               "道自己叫什么（`docs/197` §2）")
        return prog

    def use_clause(self) -> Decl:
        line = self.cur().line
        self.need("use")
        t = self.cur()
        if t.kind == "str":
            self.i += 1
            text = f"use {t.text}"
        else:
            name = self.ident("要引入的东西")
            text = f"use {name}"
        return Decl("use", text, line)

    def mode_clause(self) -> Decl:
        """`the standard library is [not] available` -> `choose std` / `choose no_std`。"""
        line = self.cur().line
        line_start = self.cur().pos
        self.need("the")
        self.need("standard")
        self.need("library")
        self.need("is", "这一句是 `the standard library is [not] available`")
        neg = self.eat("not")
        self.need("available", "这一句是 `the standard library is [not] available`")
        # **`text` 存的是原文那一句，不是 `choose …`** —— 载体要用它
        # （`from_natural` 把声明的**原文**放进正文管道，`translate` 回头再读一遍）。
        # `choose no_std` 那一串是 `emit` 从 `prog.mode` 现算的，这里不留副本。
        self.mode = "no_std" if neg else "std"
        return Decl("choose", self.src[line_start:self.cur().pos].strip(), line)

    def excluded_clause(self) -> tuple[str, int]:
        line = self.cur().line
        self.need("leave")
        self.need("out", "这一句是 `leave out \"<什么>\"`")
        t = self.cur()
        if t.kind != "str":
            self.err("`leave out` 后面要写一个带引号的名字")
        self.i += 1
        # **去掉引号**：`excluded` 那一侧（`emit_lomt`）会自己加 —— 带引号地交过去
        # 会发出 `excluded ""the network""`（实测）。
        return t.text[1:-1], line

    def extern_decl(self) -> Fn:
        """`someone else wrote <名字> with … giving …` -> `pub extern fn …;`（`docs/173`）。"""
        start = self.cur()
        self.need("someone")
        self.need("else")
        self.need("wrote", "这一句是 `someone else wrote <名字> with … giving …`")
        name = self.ident("外部函数名")
        params, ret = self.signature()
        self.keep_suffix()
        return Fn(name, params, ret, self.src[start.pos:self.cur().pos].strip(),
                  start.line, [], (), True)

    def constant(self) -> Const:
        line = self.cur().line
        self.need("remember")
        name = self.ident("常量名")
        self.need("as", "自然语言写法里常量是 `remember 名字 as 值`")
        ty = "i64"
        if self.word_at(0, "a") or self.word_at(0, "an"):
            save = self.i
            self.i += 1
            ty = self.type_phrase()
            if ty is None:
                self.i = save
                ty = "i64"
        neg = self.eat("minus")
        val = self.cur()
        if val.kind != "num":
            self.err("常量只能是整数字面量（`docs/188` §7.1 那条边界："
                     "L1 常量只收整型）；要负数就写 `minus 7`")
        self.i += 1
        n = int(val.text, 0)
        if neg:
            n = -n
        if ty.startswith("u") and n < 0:      # pragma: no cover - 词法器不出负数
            raise Unsupported(f"第 {line} 行: 常量 {val.text} 是负的，装不进 {ty}")
        pub = not self.keep_suffix()
        # 常量进类型环境 —— 后面的函数体里 `let e be LIMIT times 10` 要查得到它
        self.tenv[name] = ty
        self.const_names.add(name)
        return Const(name, ty, n, line, pub)

    # ---- `a …` 那族声明：结构体 / 枚举 / trait / impl / 能力域

    def declaration(self, prog: Program) -> None:
        """读一条以 `a` 开头的顶层声明。**第一个词是 `a`，第二个词才分派。**"""
        start = self.cur()
        self.need("a")
        first = self.cur()
        if first.kind != "id":
            self.err("`a` 后面要写一个名字（结构体 / 枚举 / trait / impl / 能力域）")
        # 形态一：`a <空间> space called <名字> covers lo to hi`（能力域）
        if self.word_at(1, "space") and self.word_at(2, "called"):
            self.capability(prog, start)
            return
        self.i += 1
        name = first.text
        if name in _RESERVED:
            self.err(f"`{name}` 是这一门的词，不能当类型的名字")
        # `a Point for any T has …` / `a Point has …`
        generics = self.generics_clause()
        if self.at("has"):
            self.struct_decl(prog, name, generics, start)
            return
        if self.at("is"):
            self.enum_decl(prog, name, generics, start)
            return
        if self.at("can"):
            if self.word_at(1, "be"):         # `a Point can be a Sizer` -> impl
                self.impl_decl(prog, name, start)
                return
            self.trait_decl(prog, name, start)
            return
        self.err(f"`a {name}` 后面要写 `has`（结构体）/ `is either`（枚举）/ "
                 f"`can`（trait）/ `can be`（impl）—— `docs/197` §2 那张表")

    def generics_clause(self) -> tuple[str, ...]:
        """`for any T` / `for any T and U` -> 泛型形参表。读不到就是没有。"""
        out: list[str] = []
        if not (self.at("for") and self.word_at(1, "any")):
            return ()
        while self.at("for") and self.word_at(1, "any"):
            self.i += 2
            out.append(self.ident("泛型形参名"))
            if not self.eat("and"):
                break
        self.gparams |= set(out)
        return tuple(out)

    def struct_decl(self, prog: Program, name: str, generics: tuple[str, ...],
                    start: Tok) -> None:
        self.need("has")
        fields = [self.field_decl()]
        while self.eat("and"):
            fields.append(self.field_decl())
        pub = not self.keep_suffix()
        self.structs[name] = dict(fields)
        self.gparams -= set(generics)
        prog.structs.append(Struct(name, fields, start.line, pub, generics,
                                   self.src[start.pos:self.cur().pos].strip()))

    def field_decl(self) -> tuple[str, str]:
        fname = self.ident("字段名")
        self.need("as", "字段要写成 `名字 as <类型>`")
        ty = self.type_phrase()
        if ty is None:
            self.err("认不出这个类型短语（`docs/197` §2 那张表）")
        return fname, ty

    def enum_decl(self, prog: Program, name: str, generics: tuple[str, ...],
                  start: Tok) -> None:
        self.need("is")
        self.need("either", "枚举是 `a <名字> is either <甲> or <乙> carrying <类型>`")
        variants: list[tuple[str, str | None]] = [self.variant_decl()]
        while self.eat("or"):
            variants.append(self.variant_decl())
        pub = not self.keep_suffix()
        self.variants[name] = {v: (p is not None) for v, p in variants}
        self.gparams -= set(generics)
        prog.enums.append(Enum(name, variants, start.line, pub, generics,
                               self.src[start.pos:self.cur().pos].strip()))

    def variant_decl(self) -> tuple[str, str | None]:
        vname = self.ident("变体的名字")
        payload = None
        if self.at("carrying"):
            self.i += 1
            payload = self.type_phrase()
            if payload is None:
                self.err("`carrying` 后面要写载荷的类型")
        return vname, payload

    def trait_decl(self, prog: Program, name: str, start: Tok) -> None:
        self.need("can")
        method = self.ident("方法名")
        ret = "()"
        if self.at("giving"):
            self.i += 1
            ret = self.type_phrase()
            if ret is None:
                self.err("`giving` 后面要写返回类型")
        pub = not self.keep_suffix()
        self.methods[(name, method)] = ret
        prog.traits.append(Trait(name, method, ret, start.line, pub,
                                 self.src[start.pos:self.cur().pos].strip()))

    def impl_decl(self, prog: Program, target: str, start: Tok) -> None:
        self.need("can")
        self.need("be")
        self.need("a")
        trait = self.ident("trait 的名字")
        self.end_sentence()
        methods: list[Fn] = []
        while True:
            if self._at_eof():
                raise NaturalError("这份源到这里就断了 —— 有一个块没有 `end` 收尾")
            if self.cur().kind == "nl":
                self.i += 1
                continue
            if self.at("end"):
                break
            if not self.at("to"):
                self.err(f"impl 里只写方法（`to <名字> giving …`），"
                         f"这里是 `{self.cur().text}`")
            methods.append(self.function(in_impl=True, self_type=target))
        self.need("end", f"`a {target} can be a {trait}` 那一块要用 `end` 收尾")
        for m in methods:
            # 方法进"方法表"，`ask p for size` 才查得到返回类型
            self.methods[(target, m.name)] = m.ret
        prog.impls.append(Impl(target, trait, methods, start.line,
                               self.src[start.pos:self.cur().pos].strip()))

    def capability(self, prog: Program, start: Tok) -> None:
        space = self.ident("能力域的空间名")
        self.need("space")
        self.need("called", "能力域是 `a <空间> space called <名字> covers lo to hi`")
        name = self.ident("能力域的名字")
        self.need("covers")
        lo = self.int_literal("能力域的下界")
        self.need("to")
        hi = self.int_literal("能力域的上界")
        revocable = False
        if self.at(","):
            self.i += 1
            self.need("and")
            self.need("it")
            self.need("can", "能力域的可回收是 `, and it can be taken back`")
            self.need("be")
            self.need("taken")
            self.need("back")
            revocable = True
        prog.caps.append((name, space, lo, hi, revocable, start.line))

    def int_literal(self, what: str) -> int:
        t = self.cur()
        if t.kind != "num":
            self.err(f"{what}要写整数")
        self.i += 1
        return int(t.text, 0)

    # ---- 函数

    def function(self, in_impl: bool = False, self_type: str | None = None) -> Fn:
        start = self.cur()
        # 类型环境**按函数清**（常量那几条留着）—— 上一支的形参不许漏到这一支
        self.tenv = {k: v for k, v in self.tenv.items() if k in self.const_names}
        self.need("to")
        name = self.ident("函数名")
        generics = self.generics_clause()
        params, ret = self.signature(with_self=in_impl)
        if self_type is not None:
            # `self` 的类型就是 impl 的目标 —— `the x of self` 因此查得到字段
            self.tenv["self"] = self_type
        pub = not self.keep_suffix()
        if generics:
            self.gparams -= set(generics)
        self.end_sentence()
        body = self.block({"end"})
        self.need("end", f"函数 `{name}` 要用 `end` 收尾")
        text = self.src[start.pos:self.cur().pos].strip()
        return Fn(name, params, ret, text, start.line, body, generics, pub)

    def signature(self, with_self: bool = False) -> tuple[list[tuple[str, str]], str]:
        """读 `with <形参> and … giving <类型>`。形参表与返回类型都可省。"""
        params: list[tuple[str, str]] = []
        if with_self:
            params.append(("self", "self"))
        if self.at("with"):
            self.i += 1
            while True:
                pname = self.ident("形参名")
                self.need("as", "形参**必须**写类型 —— 这一门没有类型推断")
                ty = self.type_phrase()
                if ty is None:
                    self.err("认不出这个类型短语（`docs/197` §2 那张表）")
                params.append((pname, ty))
                self.tenv[pname] = ty
                if not self.eat("and"):
                    break
        ret = "()"
        if self.at("giving"):
            self.i += 1
            r = self.type_phrase()
            if r is None:
                self.err("`giving` 后面要写返回类型；不返回值就整句不写 `giving`")
            ret = r
        return params, ret

    # ---- 类型短语

    def type_phrase(self) -> str | None:
        """读一个类型短语。读不出来返回 `None`，**不动下标**。

        ## 每个类型只有一个拼法（用户 2026-09-22 那条"过度复杂"的第二处）

        原来有 **12 种**短语，而复合的那些比 Loment 自己的写法**更长**：
        `a list of 3 whole numbers`（26 字符）对 `[i64; 3]`（7 字符），
        `a whole number or a failure of text`（36）对 `Result<i64, str>`（16）——
        **纯亏**：多学一套话，写出来还更长。现在只剩两类：

        1. **6 个英文基名**（`whole number` / `count` / `byte` / `truth` / `text` /
           `buffer`）—— 短、常用、读起来是人话；
        2. **其余一律照 Loment 写**：`i32` / `u64` / `[i64; 3]` / `[i64]` /
           `mut [i64]` / `Option<i64>` / `Result<i64, str>` / 结构体名 / 泛型形参名。

        **唯一的例外**：第 1 类那六个的 Loment 拼法（`i64` / `u32` / `u8` / `bool` /
        `str` / `ptr`）**同时收**。理由不是"多一种写法方便"，而是**编译器报错时印的
        就是它们**（`return 类型 u8，函数声明 i32`）—— 得能把读到的那句话原样写回去。
        """
        save = self.i
        ty = self.type_atom()
        if ty is None:
            self.i = save
        return ty

    def type_atom(self) -> str | None:
        save = self.i
        if self.at("a") or self.at("an"):
            if self.peek().kind == "id":
                self.i += 1
        t = self.cur()
        if t.kind == "id" and t.text == "mut" and self.peek().kind == "sym"                 and self.peek().text == "[":
            self.i += 1
            inner = self.type_atom()
            if inner is None or not (inner.startswith("[") and inner.endswith("]")):
                self.err("`mut` 后面要写切片：`mut [i64]` 那样")
            return f"mut {inner}"
        if t.kind == "id":
            # **两个词的基名要先看**，而且两个记号都必须是词 —— 这一格是踩出来的：
            # 第一版写的生成式会**跳过换行**，于是单词的 `truth` 被当成"两个词"、
            # `self.i += 2` 顺手把**句尾的换行**一起吃了，下一句的 `give` 于是落在
            # "这一句还没断"的位置上，报一句指不到点子的错。
            nxt = self.peek(1)
            two = None
            if nxt.kind == "id":
                for cand in (f"{t.text} {nxt.text}",
                             f"{t.text} {nxt.text[:-1]}"
                             if nxt.text.endswith("s") else ""):
                    if cand in _TYPE_WORDS:
                        two = cand
                        break
            if two is not None:
                self.i += 2
                return _TYPE_WORDS[two]
            if t.text in _TYPE_WORDS:
                self.i += 1
                return _TYPE_WORDS[t.text]
            # ---- 名字这一类：`i32` / `T` / `Point` / `Option<i64>` / `Result<i64, str>`
            name = t.text
            self.i += 1
            if self.at("<"):                  # 泛型实参
                self.i += 1
                args = [self.type_phrase()]
                while self.eat(","):
                    args.append(self.type_phrase())
                if None in args:
                    self.i = save
                    return None
                if not self.at(">"):
                    self.err("泛型类型没关上 —— 写成 `Option<i64>` 那样")
                self.i += 1
                return f"{name}<{', '.join(args)}>"
            if (name in _TYPE_DIRECT or name in self.gparams
                    or name in self.structs or name in self.variants
                    or not self.strict_types):
                return name
            self.i = save
            return None
        if t.kind == "sym" and t.text == "[":
            self.i += 1
            inner = self.type_phrase()
            if inner is None:
                self.i = save
                return None
            if self.eat(";"):                 # 定长数组
                n = self.cur()
                if n.kind != "num":
                    self.err("定长数组要写长度：`[i64; 3]` 那样")
                self.i += 1
                if not self.at("]"):
                    self.err("定长数组没关上 —— 写成 `[i64; 3]` 那样")
                self.i += 1
                return f"[{inner}; {n.text}]"
            if not self.at("]"):
                self.err("切片没关上 —— 写成 `[i64]` 那样")
            self.i += 1
            return f"[{inner}]"
        self.i = save
        return None

    # ---- 语句块

    def block(self, stops: set[str]) -> list:
        out: list = []
        while True:
            if self._at_eof():
                raise NaturalError("这份源到这里就断了 —— 有一个块没有 `end` 收尾")
            if self.cur().kind == "nl":
                self.i += 1
                continue
            if self.at(*stops):
                return self._merge(out)
            out.append(self.sentence())
            self.end_sentence()

    def _merge(self, out: list) -> list:
        """**把相邻的"看形状"那几句合成一个 `match`**（`docs/197` §2 的 `when … looks like`）。

        合并的判据是**主语那串记号逐字相同** —— 存的是记号而不是渲染出来的字符串，
        所以不必假设两个表达式"长得一样就等价"。

        合成之后：**一条臂且没有 `when anything else`** 就是 `if let`；
        两条以上（或带了兜底）才是 `match`。这一条是这一门的**决定**，不是 Loment 的。
        """
        merged: list = []
        for s in out:
            if s[0] == "pat" and merged and merged[-1][0] == "patgroup" \
                    and merged[-1][1] == s[1]:
                merged[-1][3].append(s)
                continue
            if s[0] == "pat":
                # 臂表**先用 list**（要就地长），收尾再冻回 tuple —— 与这一门别处的
                # 语法树同一个形状，只有这一格需要一次原地追加。
                merged.append(["patgroup", s[1], s[2], [s], s[5]])
                continue
            if s[0] == "catchall" and merged and merged[-1][0] == "patgroup":
                merged[-1][3].append(s)
                continue
            merged.append(s)
        return [tuple(m) if m and m[0] == "patgroup" else m for m in merged]

    def sentence(self) -> tuple:
        t = self.cur()
        if t.kind != "id":
            self.err(f"句子要以动词起头，这里是 `{t.text}`")
        w = t.text
        if w == "let":
            return self.let()
        if w == "set":
            return self.set()
        if w == "say":
            return self.say()
        if w == "talk":
            return self.talk()
        if w == "paint":
            return self.paint()
        if w == "give":
            return self.give_back()
        if w == "do":
            self.i += 1
            if self.cur().kind == "id" and self.cur().text in (
                    "let", "set", "say", "talk", "paint", "give", "when", "while",
                    "for", "guard", "do"):
                self.err(f"`do` 后面要写一个**表达式**（它的用处是「调用一个返回值的"
                         f"函数、只为副作用」）；`{self.cur().text}` 本身就是一句，"
                         f"直接写就行")
            return ("do", self.expr(), t.line)
        if w == "guard":
            return self.guard()
        if w == "when":
            return self.when()
        if w == "while":
            self.i += 1
            cond = self.expr()
            self.end_sentence()
            body = self.block({"end"})
            self.need_end("while")
            return ("while", cond, body, t.line)
        if w == "for":
            return self.forloop()
        if w in ("to", "program", "remember", "use", "leave", "someone") or w == "a":
            self.err(f"`{w}` 是**顶层**的句子，不能写在函数体里")
        if w == "end":
            self.err("多了一个 `end`")
        self.err(f"不认识的句子 `{w}` —— 这一门是**动词起头**的"
                 f"（`docs/197` §2 那张表）")

    def need_end(self, what: str) -> None:
        self.need("end", f"`{what}` 那一块要用 `end` 收尾")

    def let(self) -> tuple:
        line = self.cur().line
        self.need("let")
        name = self.ident("变量名")
        self.need("be", "自然语言写法里变量是 `let 名字 be 值`")
        # `let fb be a buffer of 1024 bytes` —— 分配是**另一种**东西（不是"值"）
        if self.word_at(0, "a") and self.word_at(1, "buffer") and self.word_at(2, "of"):
            self.i += 3
            n = self.expr()
            self.need("bytes", "写成 `let 名字 be a buffer of <字节数> bytes`")
            self.tenv[name] = "ptr"
            return ("letbuf", name, n, line)
        e = self.expr()
        if e[0] == "cast":                    # `let b be 200 as a byte`
            self.tenv[name] = e[2]
            return ("let", name, e[1], e[2], line)
        # `?` 是**后缀**（`e_postfix` 已经把它收成一个 `try` 节点），所以这一格里
        # 不是"再看一眼 `unless`"，而是"看顶上是不是那个节点"。**必须在 `let` 这一层
        # 认出来**：Loment 的检查器明说 `?` 只能出现在 let 绑定的右半边
        # （`? 只能用于 let 绑定`），所以它发成自己的语句形状，不能混进别处。
        is_try = False
        if self.at("unless"):                 # `<表达式> unless it failed` -> `<表达式>?`
            # **放在这一层、不放 `e_postfix`**：`?` 只允许出现在 let 的右半边
            # （检查器那句话就是"只能用于 let 绑定"），而 `e_postfix` 同时也是
            # **实参**那一层用的 —— 放那儿的话 `find of x unless it failed` 会读成
            # `find((x?))`（实测），`?` 落到了实参上。
            self.i += 1
            self.need("it", "`?` 传播写成 `<表达式> unless it failed`")
            self.need("failed")
            is_try = True
        # 带 `?` 时 `let` 收到的类型是**拆开之后**的那一个（`type_of` 里那条法则）
        ty = self.type_of(("try", e) if is_try else e)
        if ty == "()" and self.strict_types:
            # 调用了一个**不返回值**的函数。不拦的话会发出 `let x: () = f();` ——
            # 而那要到编译那一刻才炸，报的是"类型 `()`"，与作者写下的那句话隔着一层。
            self.err(f"`{name}` 右边那个调用**不返回值**（`giving` 那一句没写）"
                     f"—— 它不能拿来当一个值")
        if ty is None and not self.strict_types:
            # 第一遍：声明表还没收齐，先放过（后面靠它的 `let` 也只会在第二遍里被报出来）
            self.tenv[name] = "i64"
            return ("let", name, e, "i64", line)
        if ty is None:
            self.err(
                f"说不出 `{name}` 是什么类型 —— 补一句 `as a whole number` 之类。\n"
                f"  （`docs/197` §3：类型是**查**出来的，不是推出来的 —— 字面量、"
                f"声明过的名字、运算符的定则、被调函数的声明、结构体字段、方法、"
                f"容器的元素都查得到；查不到就**报错**，不默认。" )
        self.tenv[name] = ty
        return ("lettry", name, e, ty, line) if is_try else ("let", name, e, ty, line)

    def type_of(self, e: tuple) -> str | None:
        """**查**出一个表达式的类型；查不到返回 `None`（由调用方报错）。

        **只查，不推**：每一条答案都写在别处（字面量自己、名字的声明、函数声明、
        结构体字段、方法声明、容器的元素），而**那些声明就是那些答案**。
        这里没有"猜一个再用"的分支 —— 猜错的表现是"两边都编得过"。
        """
        k = e[0]
        if k == "num":
            return "i64"
        if k == "str":
            return "str"
        if k == "bool":
            return "bool"
        if k == "cast":
            return e[2]
        if k == "name":
            return self.tenv.get(e[1])
        if k == "un":
            if e[1] == "!":
                return "bool"
            return self.type_of(e[2])
        if k == "bin":
            op = e[1]
            if op in ("&&", "||") or op in ("==", "!=", "<", "<=", ">", ">="):
                return "bool"
            if op in ("<<", ">>"):            # 左移右移：取左边那个类型
                return self.type_of(e[2])
            l, r = self.type_of(e[2]), self.type_of(e[3])
            return l if (l is not None and l == r) else None
        if k == "call":
            # **查被调函数的声明**（内建表 / 外部声明 / 本文件那几张 `to … giving`）
            return self.calls.get(e[1])
        if k == "try":
            # `e?` 的类型是**拆开之后**的那一个 —— `Result<i64, i64>` -> `i64`。
            # 它是 `?` 这个运算符自己的法则（对每个程序只有一个答案），不是"推"。
            inner = self.type_of(e[1])
            if inner and (inner.startswith("Result<") or inner.startswith("Option<")):
                return inner.split("<", 1)[1].rsplit(">", 1)[0].split(",")[0].strip()
            return None
        if k == "field":
            # `the x of p`：查 `p` 的类型那张结构体表
            base = self.type_of(e[1])
            if base is None:
                return None
            return self.structs.get(base, {}).get(e[2])
        if k == "index":
            # `item 0 of xs`：容器里那一个的类型
            base = self.type_of(e[1])
            if base and base.startswith("[") and base.endswith("]"):
                return base[1:-1].split(";")[0].strip().removeprefix("mut ")
            return None
        if k == "method":
            base = self.type_of(e[1])
            if base is None:
                return None
            return self.methods.get((base, e[2]))
        if k == "ctor":                       # 结构体字面量：类型就是它自己
            return e[1]
        if k == "evariant":                   # 枚举构造：类型是那个枚举
            return e[1]
        if k == "opt":                        # `something carrying x` / `nothing to carry`
            return None
        if k == "list":
            # 列表字面量：**长度与元素类型都写在字面量自己身上**（查，不是推）。
            # 元素对不上就查不出来（`[1, "a"]`）—— 那正是该报错的地方。
            if not e[1]:
                return None
            ts = {self.type_of(x) for x in e[1]}
            if len(ts) != 1 or None in ts:
                return None
            return f"[{ts.pop()}; {len(e[1])}]"
        if k == "slice":                      # `the run of xs` -> 切片
            base = self.type_of(e[1])
            if base is None:
                return None
            # `&xs` 要的是**元素那一层**的切片：`[i64; 3]` -> `[i64]`，
            # 而本来就是切片的 `[i64]` **还是** `[i64]`（不是再套一层 —— 第一版写的
            # `f"[{base}]"` 会给出 `[[i64]]`，那是一份编不过的源）。
            if base.startswith("mut "):
                base = base[4:]
            if base.startswith("[") and base.endswith("]"):
                inner = base[1:-1]
                return f"[{inner.split(';')[0].strip()}]"
            return f"[{base}]"
        return None

    def set(self) -> tuple:
        line = self.cur().line
        self.need("set")
        if self.at("the") and self.word_at(1, "item"):
            # `set the item 0 of xs to 9` —— **与取值同一形状**（`the <什么> of <东西>`），
            # 只是前面多了个 `set … to …`。左值不再另立一种写法。
            self.need("the")
            self.need("item")
            idx = self.without_calls(self.expr)
            self.need("of", "改一个格是 `set the item <下标> of <东西> to <值>`")
            base = self.expr()
            self.need("to")
            return ("setindex", base, idx, self.expr(), line)
        name = self.ident("变量名")
        self.need("to", "自然语言写法里赋值是 `set 名字 to 值`")
        return ("set", name, self.expr(), line)

    def say(self) -> tuple:
        line = self.cur().line
        self.need("say")
        if self.word_at(0, "the") and self.word_at(1, "number"):
            self.i += 2
            return ("saynum", self.expr(), line)
        return ("say", self.expr(), line)

    def talk(self) -> tuple:
        line = self.cur().line
        self.need("talk")
        self.need("to", "这一句是 `talk to the machine <号> with <a>, <b>, <c>`")
        self.need("the")
        self.need("machine", "这一版只有一台机器可以对话：`the machine`（= 裸 syscall）")
        nr = self.expr()
        self.need("with", "`talk to the machine <号>` 后面要写 `with <a>, <b>, <c>`")
        args = []
        while True:
            args.append(self.expr())
            if not self.eat(","):
                break
        if len(args) != 3:
            raise Unsupported(
                f"第 {line} 行: `talk` 要**三个**参数（`syscall4(号, a, b, c)`），"
                f"这里给出 {len(args)} 个。\n"
                f"  如果参数里还套了一个调用（比如 `f of 7`），它会把后面的逗号**吃掉**"
                f" —— 把嵌套的那个调用括起来：`with (f of 7), 0, 0`")
        return ("talk", nr, args[0], args[1], args[2], line)

    def paint(self) -> tuple:
        line = self.cur().line
        self.need("paint")
        v = self.expr()
        self.need("at", "这一句是 `paint <值> at <下标> in <缓冲区>`")
        idx = self.expr()
        self.need("in")
        buf = self.expr()
        return ("paint", v, idx, buf, line)

    def give_back(self) -> tuple:
        line = self.cur().line
        self.need("give")
        self.need("back", "返回是 `give back <表达式>`")
        return ("ret", self.expr(), line)

    def guard(self) -> tuple:
        line = self.cur().line
        self.need("guard")
        self.need("the")
        name = self.ident("能力域的名字")
        self.need("space")
        self.need("at", "守卫是 `guard the <能力域> space at <下标>`")
        return ("guard", name, self.expr(), line)

    def when(self) -> tuple:
        """三种 `when`：条件、**看形状**（一条 match 臂）、兜底。

        它们在**同一个词**上，因为自然语言里就是同一个词（"当……的时候"）。
        靠紧跟其后的词分开：`looks like`（看形状）、`anything else`（兜底）、其余是条件。

        看形状那条返回的是**一条臂**，不是一整个 match —— 相邻的同类臂由 `_merge`
        合成一个 `match`（合并的判据是**主语那串记号逐字相同**）。
        """
        line = self.cur().line
        self.need("when")
        if self.at("anything"):
            self.i += 1
            self.need("else", "兜底那一臂写 `when anything else`")
            self.end_sentence()
            body = self.block({"end"})
            self.need_end("when")
            return ("catchall", body, line)
        i0 = self.i
        subj = self.expr()
        if self.at("looks") and self.word_at(1, "like"):
            key = tuple(t.text for t in self.t[i0:self.i])
            self.i += 2
            pat = self.pattern()
            self.end_sentence()
            body = self.block({"end"})
            self.need_end("when")
            return ("pat", key, subj, pat, body, line)
        arms: list = [(subj, None)]
        self.end_sentence()
        arms[0] = (arms[0][0], self.block({"otherwise", "end"}))
        else_body = None
        while self.at("otherwise"):
            self.i += 1
            if self.at("when"):
                self.i += 1
                cond = self.expr()
                self.end_sentence()
                arms.append((cond, self.block({"otherwise", "end"})))
            else:
                self.end_sentence()
                else_body = self.block({"end"})
                break
        self.need_end("when")
        return ("when", arms, else_body, line)

    def forloop(self) -> tuple:
        line = self.cur().line
        self.need("for")
        name = self.ident("循环变量的名字")
        self.need("from", "这一句是 `for <名字> from <下界> to <上界>`（上界**不含**）")
        lo = self.expr()
        self.need("to")
        hi = self.expr()
        self.end_sentence()
        body = self.block({"end"})
        self.need_end("for")
        return ("for", name, lo, hi, body, line)

    # ---- 表达式（优先级同 Loment / Rust）

    def expr(self) -> tuple:
        return self.e_or()

    def e_or(self):
        return self._bin(self.e_and, (), ("or",))

    def e_and(self):
        return self._bin(self.e_cmp, (), ("and",))

    def e_cmp(self):
        l = self.e_bitor()
        while True:
            w = self.eat_word("is not", "is above", "is below", "is at least",
                              "is at most", "is")
            if w is None:
                return l
            l = ("bin", _WORD_OPS[w], l, self.e_bitor())

    def e_bitor(self):
        return self._bin(self.e_bitxor, ("|",))

    def e_bitxor(self):
        return self._bin(self.e_bitand, ("^",))

    def e_bitand(self):
        return self._bin(self.e_shift, ("&",))

    def e_shift(self):
        l = self.e_add()
        while True:
            w = self.eat_word("shifted left by", "shifted right by")
            if w is None:
                return l
            l = ("bin", _WORD_OPS[w], l, self.e_add())

    def e_add(self):
        return self._bin(self.e_mul, (), ("plus", "minus"))

    def e_mul(self):
        return self._bin(self.e_unary, (), ("times", "over", "modulo"))

    def _bin(self, sub, syms: tuple, words: tuple = ()):
        l = sub()
        while True:
            if self.cur().kind == "sym" and self.cur().text in syms:
                op = self.cur().text
                self.i += 1
                l = ("bin", op, l, sub())
                continue
            if words:
                w = self.eat_word(*words)
                if w is not None:
                    l = ("bin", _WORD_OPS[w], l, sub())
                    continue
            return l

    def e_unary(self):
        # 一元那两个也用**词**：`minus 5` / `not ok`。符号 `-` / `!` 不再收 ——
        # 与二元那十几个同一条纪律（一个运算符一个写法），而 `-` 作为**前缀**尤其
        # 容易与"减号"混着读。
        if self.at("minus"):
            self.i += 1
            return ("un", "-", self.e_unary())
        if self.at("not"):
            self.i += 1
            return ("un", "!", self.e_unary())
        return self.e_postfix()

    def e_postfix(self):
        e = self.primary()
        while True:
            if self.at("as"):
                self.i += 1
                ty = self.type_phrase()
                if ty is None:
                    self.err("`as` 后面要写类型（`docs/197` §2 那张表）")
                e = ("cast", e, ty)
                continue
            return e

    def pattern(self) -> tuple:
        """一个**形状**（match 的臂 / `if let` 的左半边）。

        只收两种：`a <枚举> that is <变体> [carrying <名字>]` 与 `_`（写 `anything else`）。
        **`Option` / `Result` 收不了** —— 见文件头 §边界：判据比的是**单态化名**，
        而那是编译器的内部拼法，源里写不出来。
        """
        if self.at("anything"):
            self.i += 1
            self.need("else", "兜底那一臂写 `when anything else`")
            return ("any",)
        self.need("a")
        t = self.cur()
        if t.kind != "id":
            self.err("形状要写 `a <枚举> that is <变体> [carrying <名字>]`")
        if t.text in ("Option", "Result"):
            raise Unsupported(
                f"第 {t.line} 行: **`Option` / `Result` 的形状这一版收不了** —— "
                f"不是这一门的限制：判据拿写出来的名字与**单态化名**比"
                f"（`Option::Some` 对 `Option_u32::Some`），而后者是编译器的内部拼法，"
                f"源里写不出来（实测；`docs/197` §边界）。它们的**类型、构造、`?`** 都收。")
        # 第一遍（收表那一遍）**只读不判**：表还没收齐，而且声明可以写在用它的
        # 地方**后面**（Loment 不在乎先后，实测）。真判据在第二遍那一支。
        lenient = not self.strict_types
        if not lenient and t.text not in self.variants:
            self.err(f"`{t.text}` 不是一个已知的枚举（形状只能看枚举，"
                     f"`docs/197` §2）")
        self.i += 1
        self.need("that")
        self.need("is", "形状是 `a <枚举> that is <变体> [carrying <名字>]`")
        vname = self.ident("变体的名字")
        known = self.variants.get(t.text, {})
        if not lenient and vname not in known:
            self.err(f"`{t.text}` 没有 `{vname}` 这个变体（有：{sorted(known)}）")
        bind = None
        if self.at("carrying"):
            self.i += 1
            bind = self.ident("绑定的名字")
        elif not lenient and known.get(vname):
            self.err(f"`{vname}` 带载荷 —— 要写成 `… carrying <名字>` 把值接住")
        return ("pat", t.text, vname, bind)

    def primary(self):
        t = self.cur()
        if t.kind == "num":
            self.i += 1
            return ("num", t.text)
        if t.kind == "str":
            self.i += 1
            return ("str", t.text)
        if t.kind == "sym" and t.text == "(":
            self.i += 1
            keep = self.no_call_of
            self.no_call_of = False           # 括号是**显式**的：里面照旧认调用
            e = self.expr()
            self.no_call_of = keep
            self.need(")", "括号没关上")
            return e
        if t.kind == "sym" and t.text == "[":
            # 方括号也收 —— 自然语言那一侧是 `the list …`，这一格是给"从 Lement 抄过来"
            # 的人留的近路（两者发出来一模一样）
            return self.list_literal("[", "]")
        if t.kind == "id" and t.text in ("true", "false"):
            self.i += 1
            return ("bool", t.text)
        if t.kind == "id" and t.text == "item":       # `item 0 of xs`
            self.i += 1
            idx = self.without_calls(self.expr)
            self.need("of", "取一格是 `item <下标> of <东西>`")
            return ("index", self.e_postfix(), idx)
        if t.kind == "id" and t.text == "the":
            return self.the_clause()
        if t.kind == "id" and t.text == "nothing":    # `nothing to carry` -> None
            self.i += 1
            self.need("to", "`Option` 的空写 `nothing to carry`")
            self.need("carry")
            return ("simple", "Option::None")
        if t.kind == "id" and t.text == "something":  # `something carrying x`
            self.i += 1
            self.need("carrying", "`Option` 有的写 `something carrying <值>`")
            return ("wrapped", "Option::Some", self.e_cmp())
        if t.kind == "id" and t.text in ("a", "an") and self._starts_a_literal():
            # `a` / `an` 是**冠词**，而冠词也可以省 —— 所以只有在"后面那个词是个已知的
            # **类型名**"时才当它是字面量的开头。不加这一道的话，一个**参数名叫 `a`**
            # 的函数会在 `when a is above b` 上被读成"一个结构体字面量"，而那个错
            # 会落在很远的地方（第一版第二次踩同一个坑：`a` 当名字是合法的）。
            return self.a_clause()
        if t.kind == "id":
            self.i += 1
            no_of, self.no_call_of = self.no_call_of, False
            if self.at("of") and not no_of:   # `f of a, b`
                self.i += 1
                # **实参只读到"后缀"那一层**（不是 `expr()`）：`total of p plus score of k`
                # 要读成 `total(p) + score(k)`，而不是 `total(p + score(k))`。
                # 中文/英文的自然读法就是前者 —— "p 的总和"是一个整体，"加上"才是运算。
                # 想传一个算式进去就加括号（`f of (a plus b)`），与 `the x of p plus 1`
                # 那条取字段的规矩**是同一个**（字段的底盘也是 `e_postfix`）。
                args = [self.e_postfix()]
                while self.eat(","):
                    args.append(self.e_postfix())
                return ("call", t.text, args, t.line)
            # **没有实参的调用就写名字本身**（`say the number run`）——
            # 自然语言里不会为了一个空参数表再加一层壳。分辨靠**声明表**：
            # 名字在这个单元里是个函数、又不是任何一个局部量 ⇒ 它是一次调用。
            # 局部量优先，所以"变量与函数同名"时取变量（写出来是什么就是什么）。
            if t.text in self.calls and t.text not in self.tenv:
                return ("call", t.text, [], t.line)
            return ("name", t.text, t.line)
        self.err(f"这里要写一个值，得到 `{t.text}`")

    def without_calls(self, sub):
        """在**这一层**关掉"把 `of` 读成调用"（见 `no_call_of` 的注解）。"""
        keep = self.no_call_of
        self.no_call_of = True
        try:
            return sub()
        finally:
            self.no_call_of = keep

    def list_literal(self, open_tok: str, close_tok: str) -> tuple:
        self.need(open_tok)
        items = [self.expr()]
        while self.eat(","):
            items.append(self.expr())
        self.need(close_tok, "列表没关上")
        return ("list", items)

    def the_clause(self) -> tuple:
        """`the <什么> of <东西>` —— **"取一个东西"只有这一种形状**。

        原来有五种形状各写各的（`the x of p` 取字段、`ask p for size` 取方法、
        `item 0 of xs` 取下标、`the length of xs`、`the run of xs`），用户
        2026-09-22 那条"过度复杂"的第三处就是它：**同一件事五种壳**。
        现在**一条规则**：取什么都是 `the <什么> of <东西>`，`<什么>` 是字段名、
        是方法名、还是 `item` / `length` / `run` 那三个固定的词，**由名字自己说了算**
        （方法那格靠声明表认；认不出就当字段，写错了 Loment 会报 E15「无此字段」）。

        列表字面量**不走这里** —— 它与切片、定长数组同族，一律照 Loment 写
        （`[1, 2, 3]`），于是这一条规则没有例外。
        """
        line = self.cur().line
        self.need("the")
        if self.at("item"):                   # `the item 0 of xs`
            self.i += 1
            idx = self.without_calls(self.expr)
            self.need("of", "取一格是 `the item <下标> of <东西>`")
            return ("index", self.e_postfix(), idx)
        if self.at("length") and self.word_at(1, "of"):
            self.i += 2
            return ("call", "slice_len", [self.e_postfix()], line)
        if self.at("changeable") and self.word_at(1, "run") and self.word_at(2, "of"):
            self.i += 3
            return ("slice", self.e_postfix(), True)
        if self.at("run") and self.word_at(1, "of"):
            self.i += 2
            return ("slice", self.e_postfix(), False)
        f = self.cur()
        if f.kind != "id":
            self.err("`the` 后面要写取什么（字段名 / 方法名 / `item` / `length` / `run`）")
        self.i += 1
        self.need("of", "取东西是 `the <什么> of <东西>` —— 字段、方法、下标都这一条")
        base = self.e_postfix()
        t = self.type_of(base)
        if t is not None and (t, f.text) in self.methods:
            return ("method", base, f.text)
        return ("field", base, f.text)

    def a_clause(self) -> tuple:
        """`a …` 开头的值：结构体字面量、枚举构造、`Result` 的两个构造。

        它与顶层那族声明**同一个冠词**，靠**第二个词**分开：值这边后面跟的是
        `with`（结构体）或 `that is`（枚举）。
        """
        t = self.cur()
        line = t.line
        self.i += 1
        nxt = self.cur()
        if nxt.kind != "id":
            self.err("`a` 后面要写类型的名字")
        if nxt.text == "success" and self.word_at(1, "carrying"):
            self.i += 2
            return ("wrapped", "Result::Ok", self.e_cmp())
        if nxt.text == "failure" and self.word_at(1, "carrying"):
            self.i += 2
            return ("wrapped", "Result::Err", self.e_cmp())
        name = nxt.text
        if name in ("Option", "Result"):
            raise Unsupported(
                f"第 {line} 行: `Option` / `Result` 用**那两句专门的写法**："
                f"`something carrying <值>` / `nothing to carry` / "
                f"`a success carrying <值>` / `a failure carrying <值>`")
        self.i += 1
        if self.at("with"):                   # 结构体字面量
            if name not in self.structs:
                self.err(f"`{name}` 不是一个已知的结构体（`docs/197` §2）")
            self.i += 1
            fields = [self.named_value()]
            while self.eat("and"):
                fields.append(self.named_value())
            known = self.structs[name]
            for fname, _ in fields:
                if fname not in known:
                    self.err(f"`{name}` 没有 `{fname}` 这个字段"
                             f"（有：{sorted(known)}）")
            return ("ctor", name, fields)
        if self.at("that") and self.word_at(1, "is"):
            if name not in self.variants:
                self.err(f"`{name}` 不是一个已知的枚举（`docs/197` §2）")
            self.i += 2
            vname = self.ident("变体的名字")
            if vname not in self.variants[name]:
                self.err(f"`{name}` 没有 `{vname}` 这个变体"
                         f"（有：{sorted(self.variants[name])}）")
            payload = None
            if self.at("carrying"):
                self.i += 1
                payload = self.e_cmp()          # 同上：载荷那一格也不是 `and` 的地盘
            elif self.variants[name][vname]:
                self.err(f"`{vname}` 带载荷 —— 要写成 `… carrying <值>`")
            return ("evariant", name, vname, payload)
        self.err(f"`a {name}` 后面要写 `with`（结构体字面量）或 `that is`（枚举构造）")

    def _starts_a_literal(self) -> bool:
        """`a` 后面那个词是不是一个**已知的类型名**（结构体 / 枚举 / `Result` 的两种）。"""
        nxt = self.peek(1)
        if nxt.kind != "id":
            return False
        return nxt.text in self.structs or nxt.text in self.variants             or nxt.text in ("success", "failure")

    def named_value(self) -> tuple[str, tuple]:
        """结构体字面量里的一个字段。

        **值只读到"比较"那一层为止** —— 因为 `and` 在这一格是**分隔符**
        （`with x as 1 and y as 2`），不再是"与"。读到 `expr()` 的话，
        `x as 4 and y as 5` 里那个 `4` 会把 `and y` 一起吃成 `(4 && y)`，
        再撞上 `as` 报一句指不到点子的错（实测）。
        **想在字段值里写 `and` / `or` 就加括号** —— 括号那一支走的是 `expr()`，
        运算符照旧。
        """
        fname = self.ident("字段名")
        self.need("as", "结构体字面量是 `a <类型> with <字段> as <值>`")
        return fname, self.e_cmp()


# ---------------------------------------------------------------- 发射

#: `say the number` 用的十进制输出。**只在用到时才发**（没用到就不进产物 ——
#: 产物要能与人手写的那份**逐字节**比，多一个函数就比不上了）。
#:
#: `let zero: i64 = 0;` 那一句**不是啰嗦**，是必须的：见文件头那条"踩出来的坑"。
_WRITE_NUM = '''fn nl_write_num(fd: u64, v: i64) {
    let buf: ptr = alloc(24);
    let start: u32 = 0;
    let x: u64 = 0;
    let zero: i64 = 0;
    if v < zero {
        store8(buf, 0, 45 as u8);
        start = 1;
        x = (zero - v) as u64;
    } else {
        x = v as u64;
    }
    let n: u32 = start;
    if x == 0 {
        store8(buf, n, 48 as u8);
        n = n + 1;
    }
    while x > 0 {
        store8(buf, n, (48 + ((x % 10) as u32)) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < (n - start) / 2 {
        let lo: u8 = load8(buf, start + i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, start + i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}
'''

_IND = "    "


class Emitter:
    def __init__(self, path: str = "translate"):
        self.path = path
        self.used_write_num = False

    # ---- 表达式

    def ex(self, e: tuple) -> str:
        k = e[0]
        if k == "num":
            return e[1]
        if k in ("str", "bool"):
            return e[1]
        if k == "name":
            return e[1]
        if k == "simple":                     # `nothing to carry` -> Option::None
            return e[1]
        if k == "wrapped":                    # `something carrying x` -> Option::Some(x)
            return f"{e[1]}({self.ex(e[2])})"
        if k == "bin":
            return f"({self.ex(e[2])} {e[1]} {self.ex(e[3])})"
        if k == "un":
            return f"({e[1]}{self.ex(e[2])})"
        if k == "cast":
            return f"({self.ex(e[1])} as {e[2]})"
        if k == "try":
            return f"({self.ex(e[1])}?)"
        if k == "field":
            return f"({self.ex(e[1])}.{e[2]})"
        if k == "index":
            return f"({self.ex(e[1])}[{self.ex(e[2])}])"
        if k == "method":
            return f"{self.ex(e[1])}.{e[2]}()"
        if k == "call":
            return f"{e[1]}({', '.join(self.ex(a) for a in e[2])})"
        if k == "list":
            return "[" + ", ".join(self.ex(a) for a in e[1]) + "]"
        if k == "slice":
            return f"(&{'mut ' if e[2] else ''}{self.ex(e[1])})"
        if k == "ctor":
            inner = ", ".join(f"{n}: {self.ex(v)}" for n, v in e[2])
            return f"{e[1]} {{ {inner} }}"
        if k == "evariant":
            if e[3] is None:
                return f"{e[1]}::{e[2]}"
            return f"{e[1]}::{e[2]}({self.ex(e[3])})"
        raise AssertionError(f"发射器不认识这个节点 {k!r}")  # pragma: no cover

    # ---- 语句

    def stmts(self, body: list, depth: int) -> list[str]:
        pad = _IND * depth
        out: list[str] = []
        for s in body:
            k = s[0]
            if k == "let":
                out.append(f"{pad}let {s[1]}: {s[3]} = {self.ex(s[2])};")
            elif k == "lettry":
                # `let x: T = e?;` —— **`?` 只能是 let 的右半边**（检查器那一句话
                # 就是"只能用于 let 绑定"），所以这一格单独一种语句，不能混进 `let`。
                out.append(f"{pad}let {s[1]}: {s[3]} = {self.ex(s[2])}?;")
            elif k == "letbuf":
                out.append(f"{pad}let {s[1]}: ptr = alloc({self.ex(s[2])});")
            elif k == "set":
                out.append(f"{pad}{s[1]} = {self.ex(s[2])};")
            elif k == "setindex":
                out.append(f"{pad}{self.ex(s[1])}[{self.ex(s[2])}] = {self.ex(s[3])};")
            elif k == "ret":
                out.append(f"{pad}return {self.ex(s[1])};")
            elif k == "do":
                out.append(f"{pad}{self.ex(s[1])};")
            elif k == "guard":
                out.append(f"{pad}guard {s[1]}({self.ex(s[2])});")
            elif k == "say":
                v = self.ex(s[1])
                out.append(f"{pad}syscall4(1, 1, str_ptr({v}) as u64, "
                           f"str_len({v}) as u64);")
            elif k == "saynum":
                # 补 `as i64` 也是**降级**（与 `talk` / `paint` 那两处同一条理由）：
                # "把这个数写出来"与它是 u8 还是 u32 无关 —— 而辅助函数收的是 i64。
                # **恒等转换是合法的**（实测 `let x: i64 = a as i64;` 过检查），
                # 所以不必先判断宽度。
                self.used_write_num = True
                out.append(f"{pad}nl_write_num(1, {self.ex(s[1])} as i64);")
            elif k == "talk":
                # **四个实参一律补 `as u64`**：`syscall4` 的形参就是 u64，而这一句的
                # 意思**就是** `syscall4` —— 补转换属于"降级"，不属于"翻译"
                # （与 `paint` 那个 `as u8` 同一条理由）。不补的话，传一个 i64
                # 变量进来会在检查时报 E1，而那与作者想说的那句话无关。
                out.append(f"{pad}syscall4({self.ex(s[1])} as u64, "
                           f"{self.ex(s[2])} as u64, {self.ex(s[3])} as u64, "
                           f"{self.ex(s[4])} as u64);")
            elif k == "paint":
                # `store8` 的下标是 u32、值是 u8 —— 两处都补，理由同上（降级，不是翻译）。
                out.append(f"{pad}store8({self.ex(s[3])}, {self.ex(s[2])} as u32, "
                           f"{self.ex(s[1])} as u8);")
            elif k == "while":
                out.append(f"{pad}while {self.ex(s[1])} {{")
                out += self.stmts(s[2], depth + 1)
                out.append(f"{pad}}}")
            elif k == "for":
                out.append(f"{pad}for {s[1]} in {self.ex(s[2])}..{self.ex(s[3])} {{")
                out += self.stmts(s[4], depth + 1)
                out.append(f"{pad}}}")
            elif k == "when":
                for j, (cond, arm) in enumerate(s[1]):
                    head = "if" if j == 0 else "} else if"
                    out.append(f"{pad}{head} {self.ex(cond)} {{")
                    out += self.stmts(arm, depth + 1)
                if s[2] is not None:
                    out.append(f"{pad}}} else {{")
                    out += self.stmts(s[2], depth + 1)
                out.append(f"{pad}}}")
            elif k == "patgroup":
                out += self.patgroup(s, depth)
            else:  # pragma: no cover - 解析器只会产出上面那些
                raise AssertionError(f"发射器不认识这个语句 {k!r}")
        return out

    def patgroup(self, s: tuple, depth: int) -> list[str]:
        """**看形状那几句** -> `match` 或 `if let`（`docs/197` §2 的那条决定）。"""
        pad = _IND * depth
        subj, arms = self.ex(s[2]), s[3]
        pats = [a for a in arms if a[0] == "pat"]
        catch = [a for a in arms if a[0] == "catchall"]
        lone = len(arms) == 1 and arms[0][0] == "pat"
        if lone and not catch:
            p = pats[0]
            out = [f"{pad}if let {self.pat(p[3])} = {subj} {{"]
            out += self.stmts(p[4], depth + 1)
            out.append(f"{pad}}}")
            return out
        out = [f"{pad}match {subj} {{"]
        for a in arms:
            if a[0] == "catchall":
                # `("catchall", body, line)` —— 正文在**第一格**（第二格才是行号；
                # 写成 `a[2]` 会把一个整数当语句表传下去，实测报"int 不可迭代"）
                out.append(f"{pad}{_IND}_ => {{")
                out += self.stmts(a[1], depth + 2)
            else:
                out.append(f"{pad}{_IND}{self.pat(a[3])} => {{")
                out += self.stmts(a[4], depth + 2)
            out.append(f"{pad}{_IND}}}")
        out.append(f"{pad}}}")
        return out

    def pat(self, p: tuple) -> str:
        if p[0] == "any":
            return "_"
        _, ty, var, bind = p
        return f"{ty}::{var}" if bind is None else f"{ty}::{var}({bind})"

    # ---- 声明

    def vis(self, pub: bool) -> str:
        return "pub " if pub else ""

    def gpar(self, generics: tuple[str, ...]) -> str:
        return f"<{', '.join(generics)}>" if generics else ""

    def struct(self, st: Struct) -> str:
        return (f"{self.vis(st.pub)}struct {st.name}{self.gpar(st.generics)} {{\n"
                + "".join(f"{_IND}{n}: {t},\n" for n, t in st.fields) + "}")

    def enum(self, en: Enum) -> str:
        arms = "".join(f"{_IND}{v}" + (f"({p})" if p else "") + ",\n"
                       for v, p in en.variants)
        return f"{self.vis(en.pub)}enum {en.name}{self.gpar(en.generics)} {{\n{arms}}}"

    def trait(self, tr: Trait) -> str:
        sig = f"fn {tr.method}(self)"
        if tr.ret != "()":
            sig += f" -> {tr.ret}"
        sig += ";"
        return f"{self.vis(tr.pub)}trait {tr.name} {{\n{_IND}{sig}\n}}"

    def impl(self, im: Impl) -> str:
        body = []
        for m in im.methods:
            body.append(self.fn(m, pub=False, inner=True))
        return (f"impl {im.trait} for {im.target} {{\n"
                + "\n\n".join(_indent(b, 1) for b in body) + "\n}")

    def fn(self, f: Fn, pub: bool = True, inner: bool = False) -> str:
        sig = []
        for n, t in f.params:
            sig.append(n if n == "self" else f"{n}: {t}")
        head = f"{self.vis(pub)}fn {f.name}{self.gpar(f.generics)}({', '.join(sig)})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        body = self.stmts(f.stmts, 1 if not inner else 1)
        if not body:
            return head + " {\n}"
        return head + " {\n" + "\n".join(body) + "\n}"


def _indent(text: str, n: int) -> str:
    pad = _IND * n
    return "\n".join(pad + ln if ln.strip() else ln for ln in text.split("\n"))


# ---------------------------------------------------------------- 入口

def parse_program(src: str, need_program: bool = True,
                  consts: dict[str, str] | None = None,
                  externs: dict[str, str] | None = None) -> Program:
    """一份**自然语言写法**的源 -> 程序。`potato_from.from_natural` 走这一条。

    `consts` 只在**手上没有 `remember` 那几句**时才要给（`translate` 那条路：
    它拿到的是一串函数正文，常量在 Potato 对象里）；`externs` 同理。

    ## 为什么**读两遍**

    第一遍只为收"函数名 -> 返回类型"这张表，第二遍拿它把类型查全。两遍用的**同一份
    记号流**（第一遍只读不动），差别只在 `strict_types`。

    不多读这一遍的话，每个"let 一个调用结果"都得写成
    `let a be (sum_to of LIMIT) as a whole number` —— 而少写就报错。那条路是
    **把成本转嫁给用户**，而这里读两遍就够了。
    """
    toks = tokenize(src)
    seed = dict(externs or {})
    p1 = Parser(toks, src, consts=consts, calls=seed, strict_types=False)
    first = p1.program(need_program)
    calls = dict(seed)
    for f in first.fns + first.externs:
        if f.ret in f.generics:
            # **泛型函数的返回类型查不出来** —— `largest<T>(a: T, b: T) -> T` 的 `T`
            # 不是一个类型，它是"调用点当场定的那个"。放进表里的话
            # `let m be largest of 1, 2` 会发成 `let m: T = …;`，那是一份编不过的源。
            # 所以**不放进表**：查不到就让作者写 `as`（与"查不到就报错"同一条纪律）。
            continue
        calls[f.name] = f.ret
    # 第一遍已经把三张"类型表"收全了（结构体字段 / 变体 / 方法），第二遍直接继承 ——
    # 不必再扫一遍：那三张表只由**声明**决定，而声明在两遍里一模一样。
    p2 = Parser(toks, src, consts=consts, calls=calls)
    p2.structs, p2.variants, p2.methods = p1.structs, p1.variants, p1.methods
    return p2.program(need_program)


def _check_names(prog: Program) -> None:
    """**点名**三种写错：`set` 一个没声明过的名字、调用一个不存在的函数、
    用了一个不存在的能力域。

    分成两步（先把全部名字收齐再查）是因为单遍扫会把"先用在先、声明在后"判成错的
    —— 那种假红比不查还坏。
    """
    fns = {f.name for f in prog.fns} | {f.name for f in prog.externs}
    builtin = set(_BUILTINS)
    for f in prog.fns:
        declared = {n for n, _ in f.params}
        for s in _walk(f.stmts):
            if s[0] in ("let", "letbuf", "lettry"):
                declared.add(s[1])
        for s in _walk(f.stmts):
            if s[0] == "set" and s[1] not in declared:
                # **行号取 `s[-1]`**：`("set", 名字, 表达式, 行号)` 的第三格是表达式 ——
                # 写成 `s[2]` 会把**一个语法树节点**印进"第 … 行"里（实测过）。
                raise Unsupported(
                    f"第 {s[-1]} 行: `set {s[1]} to …`，可 `{s[1]}` 在这个函数里"
                    f"**没有声明过**（`let` 一句都没有）—— 名字写错了，"
                    f"或者这一条该是 `let`")
            # **能力域的名字这里查不了**：那份声明不进这段正文（它归 Potato 对象的
            # `capabilities`，由 `emit_lomt` 发），所以 `translate` 手上的文本里
            # **根本没有** `a <空间> space called …` 那一句。名字写错的后果由编译器
            # 那一侧接（E4「未声明的能力域」），**报得一样准** —— 这里不必再造一份
            # 半截的判据（半截的判据会把"名字对但声明在别处"判成错）。
        # **"调用了不存在的函数"这一格故意不查**。查过一版，撤掉了：
        # `use "别的.lomt"` 引进来的函数**在这段正文里看不见**（`use` 那一句和那份
        # 库都不在这个单元里），于是每一个跨单元的调用都会被判成错的 ——
        # 那是**假红**，而假红比不查还坏（`docs/197` §5 那条纪律的反面教材）。
        # 真判据在编译器那一侧：E2「未定义的函数」，报得准，而且它看得见整棵依赖树。


def _walk(body: list):
    for s in body:
        yield s
        if s[0] == "when":
            for _c, arm in s[1]:
                yield from _walk(arm)
            if s[2] is not None:
                yield from _walk(s[2])
        elif s[0] == "patgroup":
            for a in s[3]:
                yield from _walk(a[4] if a[0] == "pat" else a[1])
        elif s[0] == "while":
            yield from _walk(s[2])
        elif s[0] == "for":
            yield from _walk(s[4])


def _calls(body: list):
    """语句里出现的**调用点** `(名字, 行号)`。"""
    out: list[tuple[str, int]] = []
    for s in _walk(body):
        for e, line in _exprs(s):
            out += _calls_in_expr(e, line)
    return out


def _exprs(s: tuple):
    k = s[0]
    if k in ("let", "lettry"):
        yield s[2], s[-1]
    elif k == "letbuf":
        yield s[2], s[3]
    elif k == "set":
        yield s[2], s[-1]
    elif k == "setindex":
        yield s[1], s[4]
        yield s[2], s[4]
        yield s[3], s[4]
    elif k in ("ret", "do", "say"):
        yield s[1], s[-1]
    elif k == "saynum":
        yield s[1], s[2]
    elif k == "guard":
        yield s[2], s[3]
    elif k == "talk":
        for e in s[1:5]:
            yield e, s[5]
    elif k == "paint":
        for e in s[1:4]:
            yield e, s[4]
    elif k == "while":
        yield s[1], s[3]
    elif k == "for":
        yield s[2], s[5]
        yield s[3], s[5]
    elif k == "when":
        for c, _arm in s[1]:
            yield c, s[3]
    elif k == "patgroup":
        yield s[2], s[4]


def _calls_in_expr(e: tuple, line: int):
    k = e[0]
    if k == "call":
        yield e[1], line
        for a in e[2]:
            yield from _calls_in_expr(a, line)
    elif k == "bin":
        yield from _calls_in_expr(e[2], line)
        yield from _calls_in_expr(e[3], line)
    elif k == "un":
        yield from _calls_in_expr(e[2], line)
    elif k == "cast":
        yield from _calls_in_expr(e[1], line)
    elif k == "try":
        yield from _calls_in_expr(e[1], line)
    elif k == "field":
        yield from _calls_in_expr(e[1], line)
    elif k == "index":
        yield from _calls_in_expr(e[1], line)
        yield from _calls_in_expr(e[2], line)
    elif k == "method":
        yield from _calls_in_expr(e[1], line)
    elif k == "wrapped":
        yield from _calls_in_expr(e[2], line)
    elif k == "list":
        for a in e[1]:
            yield from _calls_in_expr(a, line)
    elif k == "slice":
        yield from _calls_in_expr(e[1], line)
    elif k == "ctor":
        for _n, v in e[2]:
            yield from _calls_in_expr(v, line)
    elif k == "evariant" and e[3] is not None:
        yield from _calls_in_expr(e[3], line)


def emit(prog: Program, keep_out: bool = True) -> str:
    """程序 -> Loment 源码。

    **只发"这一层该发的"**：`module` / 能力域 / 常量 / `excluded` 由 `lomt_from` 发
    （走 Potato 对象），这里发的是**剩下的全部** —— `choose` / `use` / 结构体 / 枚举 /
    trait / impl / 函数。

    **顺序是定死的**（不是源码顺序）：`choose` -> `use` -> 结构体 -> 枚举 -> trait ->
    impl -> 函数。Loment 对声明的先后**没有要求**（实测：结构体写在用它的函数之后照样过），
    所以这里按"读起来顺"排，而不是按作者写的顺序 —— 产物要能与人手写的那份**逐字节**比，
    那个"手写的样子"就是按类聚在一起的。
    """
    em = Emitter()
    out: list[str] = []
    if prog.mode:
        out.append(f"choose {prog.mode}")
    for u in prog.uses:
        out.append(u.text)
    for st in prog.structs:
        out.append(em.struct(st))
    for en in prog.enums:
        out.append(em.enum(en))
    for tr in prog.traits:
        out.append(em.trait(tr))
    for im in prog.impls:
        out.append(em.impl(im))
    # **私有常量走这里，公开的走 Potato 对象**（`emit_lomt` 只发 `pub const`）。
    # 分成两条路不是拼凑：公开那条要进对象（下游读得到），私有那条不必 ——
    # 而"只有这里看得见"这件事**只有我这一层知道**，`emit_lomt` 没有这个信息。
    for c in prog.consts:
        if not c.pub:
            out.append(f"const {c.name}: {c.ty} = {c.value};")
    for f in prog.fns:
        out.append(em.fn(f, pub=f.pub))
    text = "\n\n".join(out)
    if text:
        text += "\n"
    if em.used_write_num:
        # **它排在前面**：产物是"一份完整的实现单元"，辅助函数放在被它服务的函数
        # 之前读起来顺；位置也是**确定的**（判据要与手写的那份逐字节比）。
        text = _WRITE_NUM + "\n" + text
    return text


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """自然语言源码 -> Loment 源码。

    **`keep` 在这一门没有用武之地**（与另外六门不同）：这里的 `src` 是**整份源**的
    正文，每一支都带实现，所以没有"要实现的"与"留作 `extern` 的"要分。
    它留在签名里是为了与 `ctrans` / `gotrans` **同一个形状**（`lomt_from` 按同一张
    表调它们）。
    """
    prog = parse_program(src, need_program=False, consts=consts, externs=externs)
    _check_names(prog)
    seen: set[str] = set()
    for f in prog.fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.line} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)
    return emit(prog)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nltrans",
        description="自然语言写法 -> Loment (docs/197)。出的是**声明与函数**，"
                    "module / 能力域 / 常量 / excluded 由 lomt_from 发。")
    ap.add_argument("path", help="自然语言写法的源 (.nl)")
    ap.add_argument("--out", metavar="PATH", help="写到文件（默认 stdout）")
    a = ap.parse_args(argv)
    p = Path(a.path)
    try:
        src = p.read_text(encoding="utf-8")
    except OSError as e:
        print(f"nltrans: 读不了 {p}: {e}", file=sys.stderr)
        return 2
    try:
        text = translate(src)
    except Unsupported as e:
        print(f"nltrans: 子集外: {e}", file=sys.stderr)
        return 1
    except NaturalError as e:
        print(f"nltrans: 写法不对: {e}", file=sys.stderr)
        return 1
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
