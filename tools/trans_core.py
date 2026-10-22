"""trans_core.py — **花括号族（C / C++ / Java / C#）的共享前端核**（`docs/188` §7.1）。

    源语言写法 --<某门的方言表>--> 本模块 --> Loment 源码

## 为什么是"一份解析器 + 方言表"

这四门的**形状**是一样的：`<类型> <名>(<参数>) { … }`、分号结句、条件带括号、
同一张运算符优先级。**真正不同的只有几样** —— 它们全进 `Dialect`，不进 `if`：

    类型拼法表 · 声明起始词 · 收下丢掉的修饰词 · 出现就拒的修饰词 · 聚合类型词
    运算符表 · 优先级表 · **两个方向的强制转换** · 整型默认宽度 · 保留字避让后缀

四份独立的翻译器 = 四倍重复，而**同一件事写四遍必然漂**。

## 这一格最会咬人的地方：`bool` 与 `int` 的强制转换

| | `&&` 出什么 | `if (x)` 收什么 | 要不要补转换 |
|---|---|---|---|
| **C / C++** | `int` | 标量即可 | **两个方向都要** |
| **Java / C#** | `boolean` | 只要 `boolean` | **一个都不要** |

判据是那条一般的：**源语言的规则能表达出来的就转，表达不出来的就报错**
（`docs/187`）。C 的 `&&` 出 `int`，而 `(bool) as i32` **能**表达那个意思 ⇒ 转；
Java 的 `&&` 出 `boolean`，`int x = (a<b)` 在 Java 里本来就是**类型错** ⇒ 不转、报错。
**同一处的差，两族结论相反，而判据是同一条。**

## 子集（四门共用）

    类型      `Dialect.types` 里那些；其余报错，不猜
    函数      <类型> <名>(<类型> <名>, …) { … }
    语句      int x = e;   int x;   x = e;   return e;   return;
              if (e) S [else S]   while (e) S   for (init; cond; step) S   e;
    表达式    整数（十/十六进制）  标识符  调用
              一元 - ! ~      二元 || && | ^ & == != < > <= >= << >> + - * / %

**不支持的一律报错退出，不静默丢**（`docs/167`）。跳过一个函数在这里**不安全**：
`lomt_from --impl` 发出的是**真实现**，少一个就是"这个程序少算了一步"，而它照样能编过。

## 两条不是翻译、是**决定**的东西

1. **`int x;`（只声明不给值）补零值。** C 读未初始化变量是**未定义行为**；Loment 要的是
   确定的程序。零值是最接近"什么都没发生"的确定选择。**不是**想把 C 的 UB 变成 0。
2. **`for (int i = …)` 的变量改名外提。** Loment **没有裸块**
   （实测 `15:5 期望表达式，得到 '{'`），所以源语言的 for 作用域没法用一层 `{ }` 圈起来。
   改名是安全的：那个作用域本来就在循环结束处结束，外面看不到它。

用法：各门的模块给一张 `Dialect`，然后 `translate(src, D)` / `main(D, "C")`。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: 这一族的**共享**东西 —— 三张表与语言无关（C / C++ / Java / C# 的表达式优先级
#: 是同一张，多字符运算符也是同一批），所以放在这儿而不是方言表里。
#: 优先级表，**低到高**。`precof` 返回 `下标 + 1`。
_PREC = [
    ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
    ["<", ">", "<=", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
]
_PRECOF = {op: i + 1 for i, lvl in enumerate(_PREC) for op in lvl}

#: 运算符 -> (Loment 运算符, 操作数按什么算, 结果是不是 bool)。
#: 操作数 `"bool"` 说的是这一族把两侧当条件看（`&&` `||`），于是操作数要转成 bool。
#: 结果 `True` 说的是"它在源语言里其实出整数，进整数场合要补转换"。
#: **这一张四门共用** —— 差别不在运算符表，在下面那两个 `coerce_*` 开关上。
_BIN = {
    "*": ("*", "int", False), "/": ("/", "int", False), "%": ("%", "int", False),
    "+": ("+", "int", False), "-": ("-", "int", False),
    "<<": ("<<", "int", False), ">>": (">>", "int", False),
    "<": ("<", "int", True), ">": (">", "int", True),
    "<=": ("<=", "int", True), ">=": (">=", "int", True),
    "==": ("==", "int", True), "!=": ("!=", "int", True),
    "&": ("&", "int", False), "^": ("^", "int", False), "|": ("|", "int", False),
    "&&": ("&&", "bool", True), "||": ("||", "bool", True),
}

#: 语句关键字 —— 四门共用（`do` / `goto` / `sizeof` 在 Java/C# 里不是关键字，
#: 但它们出现在那些语言里也多半是写错了，早拒比晚拒好）。
_KW = {"if", "else", "while", "for", "return", "break", "continue", "do", "switch",
       "case", "default", "goto", "sizeof"}

# ---------------------------------------------------------------- 方言表
#
# **一份解析器 + 方言表**（`docs/188` §7.1）。这一族（C / C++ / Java / C#）的**形状**
# 是一样的：`<类型> <名>(<参数>) { … }`、分号结句、条件带括号。真正不同的只有下面
# 这几个字段 —— 所以它们进表，不进 if。


@dataclass(frozen=True)
class Dialect:
    """一种"花括号 + 分号"语言的差异面。**只放真正不同的东西。**"""

    name: str
    #: 类型拼法 -> Loment 类型
    types: dict
    #: 能拼进 `types` 的类型词
    type_words: frozenset
    #: 能出现在"类型位置"但本子集一律拒绝的。**必须认得它们才能给出对的报错** ——
    #: 不认的话 `static int f()` 会报"期望类型名"，而真正的原因是"不支持存储类"。
    spec_words: frozenset
    #: 收下并丢掉的修饰词（见 §语义选择 3 那条判据）
    linkage: frozenset
    #: 出现就拒的修饰词
    bad_spec: frozenset
    #: 聚合/枚举类型词（本子集不收）
    agg: frozenset
    #: 语句关键字（出现在表达式里就拒）
    kw: frozenset
    #: 二元运算符 -> (Loment 运算符, 操作数按什么算, 结果是不是 bool)。
    #: 操作数 `"bool"` = 这一族把两侧当条件看；结果 `True` = 它其实出整数，
    #: 进整数场合要补转换。
    bin: dict
    #: 优先级表，**低到高**。`precof` 返回 `下标 + 1`。
    precof: dict
    #: **两个方向的强制转换**（见 `Emitter.ex`）。这一格是这一族里**最会咬人**的：
    #:   C / C++  —— `&&` 出 `int`，`if (x)` 收标量      ⇒ **两个方向都要**
    #:   Java / C# —— `&&` 出 `boolean`，条件只收 `boolean` ⇒ **一个都不要**
    #: 同一处差，两条判据（"能表达的就转，表达不出来的就报错"）给出相反结论。
    coerce_int_to_bool: bool = True
    coerce_bool_to_int: bool = True
    #: **这一族的源码里 `true` / `false` 是不是字面量。**
    #: Java / C# / Go / C++ 是（它们是关键字，写 `int true = 5;` 本身就非法）；
    #: **C 不是** —— C99/C11 里它只是 `<stdbool.h>` 的一个**宏**，而这一门没有
    #: `#include`，所以 `int true = 5;` 是完全合法的普通变量。认成字面量的话，
    #: 声明处被改名（`let true_c: i32 = 5;`）、引用处却没改（`return (true) as i32;`）——
    #: 同一个名字在一个函数里**两个身份**，退 0 而数是错的。
    bool_literals: bool = True
    #: 布尔的整型投影用哪个宽度（`bool -> int` 的 `as <x>`）。**粗粒度的兜底** ——
    #: 真需求是"按上下文的具体整型"，现在传不进来，所以取这一族的默认宽度。
    int_default: str = "i32"
    #: **要不要按源语言的"寻常算术转换"插入整型转换。**
    #:
    #: `types` 里列了好几档整型（C 是 `int`/`unsigned`/`long`/`unsigned long` —— 见
    #: `docs/186` §4），可**混着用**的时候两侧怎么办，各语言规则不同：
    #:
    #:   * C / C++ —— **向更宽 / 无符号那侧提升**。这一门**能表达**那个意思（`as`），
    #:     所以**转**：`int a = -1; unsigned b = 0; a > b` 在 C 里是 `(a as u32) > b`
    #:     （`a` 变成 4294967295 ⇒ 真）。不转的话本语言按**有符号**算，答**假**。
    #:     同一件事还有一处：`int x = 1; long y = 2147483647;` 里 `(x + y > 0)` 原本
    #:     答 0（`x + y` 在 64 位算、`> 0` 却在 32 位比），而 C 答 1。
    #:   * Java / C# —— Java 的窄化转换**是编译错**（要显式写 `(int)`），C# 只有
    #:     常量折叠才隐式收窄。按 C 的规则插转换会写出这两门**本不接受**的程序，
    #:     所以**不转**（保持现状：让它在 check 那层响亮地失败）。
    #:   * Go —— 连**加宽**都要显式 `int64(x)`，规则又不一样。
    #:
    #: 所以这是**方言面**上的开关，不是 `if <语言>`（`docs/188` §7.1）。
    usual_arith: bool = False
    #: **窄整型的字面量初值要查范围。** Java/C# 的 `byte b = 200;` 本来就是编译错；
    #: 不查的话生成的是 `let b: i8 = 200;`，运行时静默回绕成 -56。
    narrow_literal_check: bool = False
    #: **常量零做除数/模数要拒。** Java 把 `1 / 0` 当编译期错误（常量表达式）；
    #: 不拒的话生成一份能编过的源，运行时 SIGFPE。
    reject_const_zero_div: bool = False
    #: **移位距离按 Java 语义掩码**（`int` 掩 31、`long` 掩 63）。本语言的移位不掩，
    #: 于是 `1 << 32` 在 Java 是 1、在这里是 0。
    mask_shift: bool = False
    #: **十六进制字面量按 32 位补码读**。Java 的 `0xFFFFFFFF` 是 `int -1`；
    #: 不这么做的话它会被当成 4294967295，然后被 32 位字面量闸拒掉。
    hex_wrap: bool = False
    #: 这些字面量写法在**这一门**里收不收。C 的 `010` 是八进制，但本子集历史上只收
    #: 十/十六进制（`docs/186` §4）；Java 的 `010`/`0b1010`/`1_000` 都是合法写法，
    #: 而 #811/#812/#813 要的就是它们。所以按方言分，不分的话 C 的旧判据会红。
    octal_literals: bool = False
    binary_literals: bool = False
    digit_separators: bool = False
    #: 撞上 Loment 保留字时给名字加的后缀。**每门不同** —— 两门用同一个后缀的话，
    #: 将来把两个单元合起来会莫名其妙撞名。
    safe_suffix: str = "_x"
    #: **哪些修饰词标志着"这是一条常量声明"**。Java 的 `static final`、C# 的 `const`、
    #: C++ 的 `constexpr` —— 它们在顶层出现时**不发成函数也不发成全局变量**，而是
    #: 由 `potato_from` 收进 `consts`（`_JAVA_CONST` 那一条）、由 `lomt_from` 发成
    #: `pub const`。翻译器只要**跳过**那条声明，并且认得那个名字。
    #: **空集 = 这一族不收顶层常量**（C 就是 —— `potato_from` 也不收它的全局量）。
    const_words: frozenset = frozenset()
    #: **在"函数内声明"这个位置上，哪些修饰词不能当成无所谓而丢掉。**
    #: C / C++ 的 `static` 在这里说的是**静态存储期**（值跨调用保留），与链接无关 ——
    #: 丢掉它会让每次调用都从初值重来，**产物照样编得过，只是数不对**。
    #: `const` / `inline` 在局部位置上没有可分辨的差别，所以不在这一集里。
    local_storage: frozenset = frozenset()
    #: **这一族特有的源码预处理**（`src -> src`），在分词**之前**跑。
    #:   Java / C# —— 抹掉 `class` 外壳（函数住在类里，而解析器看的是顶层）
    #:   C / C++   —— 没有
    #: **必须保持行号与偏移**（换成等长空白，别抽出来拼一拼）—— 报错里的行号
    #: 就是源里的行号，漂了就指不到点子。
    pre: object = None

    def safe(self, name: str) -> str:
        """撞上 Loment 保留字就加后缀。

        **不做成模块级函数** —— 后缀是这一门的，模块级就没有"门"这个概念了。
        """
        return name + self.safe_suffix if name in _LOMENT_KW else name


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 这里静默跳过 = 产物少算一步却照样能编。"""


class CError(Exception):
    """这份 C 解析不过。与 `Unsupported` 分开：一个是"没实现"，一个是"你自己写错了"。"""


# ---------------------------------------------------------------- 外壳剥离
#
# Java 与 C# 的函数**不住在顶层**：它们在 `class X { … }` 里，C# 还多一层
# `namespace N { … }`。而共享 parser 只认顶层的 `<类型> <名>(…) { … }` —— 所以分词
# **之前**先把那两层外壳抹掉。
#
# **抹法是"换成等长空白"**（与 `potato_from._blank_keep_off` 同一个手法），不是把成员
# 抽出来拼一拼：长度、行号、偏移**一字不变**，于是报错里的行号**就是源里的行号**。
# 抽出来重排的话行号会漂，而"报错行号指到别处"正是 `docs/179` §6.5 记过的那种
# 把人引向错方向的东西。


def blank_keep_off(m: "re.Match[str]") -> str:
    """换成**等长**空白，且**保留换行** —— 行号与偏移都不动。"""
    return "".join("\n" if ch == "\n" else " " for ch in m.group())


def match_brace(text: str, open_idx: int) -> int:
    """`{` 的下标 -> 配对 `}` 的下标；数不配平返回 -1。

    调用方**必须先把注释与字符串字面量抹掉** —— 否则 `/* } */` 或 `"}"` 会把配对算错。
    """
    d = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            d += 1
        elif text[i] == "}":
            d -= 1
            if d == 0:
                return i
    return -1


#: 扫外壳之前要抹成等长空白的东西：注释**与字符串字面量**。
#: 只抹注释是不够的 —— `String s = "class X {";` 会让扫描把那行当成一个 `class` 头，
#: 于是外壳从字符串里开始切，切出一份**错得看不出来**的源。（`jtrans` 原先只抹了注释。）
_SHELL_NOISE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:[^\"\\\n]|\\.)*\"", re.S)

def strip_shells(src: str, heads: "list", line0: int = 1) -> str:
    """抹掉 `heads` 点名的外壳，壳里的成员就成了顶层。**不动行号。**

    `heads` 每项是 `(名字, 正则, 透明, 有体)`：

    * **透明**（`namespace`）—— 壳里还会有壳，抹完**继续进去**找（命名空间能套）
    * **不透明**（`class`）—— 壳里就是成员，**不再进去**：嵌套类是另一回事，留给方言表
      的 `agg` 去拒（`docs/188` §7.1 的 Java 就是这么定的）
    * **有体 = False** —— 这一条没有身体（C# 10 的文件级 `namespace Foo;`），
      正则负责一路匹配到 `;`，抹成空白就行

    正则应把**尾部那个 `{` 也吃进去**（`[^{;]*\\{` 这种），于是 `{` 的下标就是
    `m.end() - 1` —— 顺带把"头与 `{` 之间夹了个 `;`"的怪写法排除掉了。
    """
    sniff = _SHELL_NOISE.sub(blank_keep_off, src)
    out = list(src)

    def walk(lo: int, hi: int) -> None:
        i = lo
        while i < hi:
            for what, rx, transparent, braced in heads:
                m = rx.match(sniff, i)
                if not m:
                    continue
                if braced:
                    j = m.end() - 1                      # 尾巴那个 `{`
                    k = match_brace(sniff, j)
                    if k < 0 or k >= hi:
                        raise CError(f"第 {line0 + src[:m.start()].count(chr(10))} 行: "
                                     f"`{what}` 的花括号不配平")
                    if transparent:
                        walk(j + 1, k)
                else:
                    k = m.end() - 1                      # 尾巴那个 `;`
                for p in range(m.start(), (m.end() if not braced else j + 1)):
                    if out[p] != "\n":
                        out[p] = " "
                if braced and out[k] != "\n":
                    out[k] = " "
                i = k + 1
                break
            else:
                i += 1

    walk(0, len(sniff))
    return "".join(out)


# ---------------------------------------------------------------- 分词

_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<lc>//[^\n]*)
    | (?P<bc>/\*.*?\*/)
    | (?P<chr>'(?:\\.|[^'\\])')
    | (?P<str>"(?:\\.|[^"\\])*")
    | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<num>0[xX][0-9a-fA-F_]+[lL]?|0[bB][01_]+[lL]?|0[oO][0-7_]+[lL]?|[0-9][0-9_]*[lL]?)
    | (?P<op>>>=|>>>|<<=|>>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\.\.\.|<<|>>|<=|>=|==|!=|&&|\|\||\+\+|--|[-+*/%&|^~!<>=();,{}\[\]?:#])
""", re.X | re.S)

#: 本子集不收的运算符：**必须**在分词那一步**整个**认出来再点名拒掉。
#: 漏一个的后果是它被**切成两半**：`+=` 变成 `+` `=`，于是 `a += 1` 报的是
#: "`a` 后面期望 `=` 或 `(`，得到 '+'" —— 指的**不是**真正的原因（用了复合赋值）。
_REJECT_OP = {"<<=", ">>=", ">>>=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}

#: **点名拒掉**、理由要单独说的运算符。`>>>` 是 Java 的逻辑右移 —— 本语言的 `>>`
#: 是**算术**的（实测 `-8 >> 1 == -4`），所以 `>>>` 不能直接映过去。要么 `(a as u32) >> n`，
#: 要么不收；现在**不收**（那是个能表达出来的差，但先别急着补 —— 没语料逼它）。
#:
#: `~`（按位取反）同理，而且**必须点名拒** —— 原先它在 `unary()` 的"收下"那一支里，
#: 于是前端照发 `(~v)`，而 **Loment 没有这个运算符**：产物死在编译器的**词法**那一步
#: （`非法字符 '~'`），报出来还带着**生成出来的那个单元**的行号。一个能过前端、
#: 却在后面炸的洞 —— 2026-09-18 写 `loment/jtrans/Bits.java` 时撞出来的。
#: 它**本可以转**（`~v` 就是"与全 1 异或"），但翻译器**不做整数宽度跟踪**（`docs/188` §7.1.1）：
#: i32 上是 `v ^ -1`、u8 上是 `v ^ 255`，**替不了你选**。所以不转，点名拒并给出两条出路。
#: 与 `>>>` 同一条纪律：**先不收，除非有语料逼它**。
_REJECT_NAMED = {"...": "变参 `...`", "++": "自增/自减", "--": "自增/自减",
                  ">>>": "逻辑右移 `>>>`（本语言的 `>>` 是算术的；要它得写 `(a as u32) >> n`）",
                  "~": "按位取反 `~`（本语言的一元运算符只有 `-` 与 `!`；`~v` 是"
                       "“与全 1 异或”，要它得**按宽度**写 `v ^ -1`（i32）或 `v ^ 255`（u8）"
                       "—— 翻译器不做宽度跟踪，所以不替你选）"}


def tokens(src: str) -> list[tuple[str, str, int]]:
    """C 源码 -> `[(种类, 文本, 行)]`。种类 ∈ {id, num, op}。注释与空白丢掉。"""
    out: list[tuple[str, str, int]] = []
    line = 1
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise CError(f"第 {line} 行: 认不出的字符 {src[i]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind in ("ws", "lc", "bc"):
            line += text.count("\n")
            i = m.end()
            continue
        if kind == "op":
            if text in _REJECT_NAMED:
                raise Unsupported(f"第 {line} 行: 不支持{_REJECT_NAMED[text]}`{text}`")
            if text in _REJECT_OP:
                raise Unsupported(f"第 {line} 行: 不支持 `{text}`"
                                  f"（写成 `x = x + 1;` 那种单独一条语句）")
        out.append((kind if kind in ("id", "num", "chr", "str") else "op",
                   text, line))
        i = m.end()
    return out


# ---------------------------------------------------------------- 语法树


class Lit:
    """整数字面量。`b=True` 表示它是**布尔字面量**（源里的 `true` / `false`）。

    **这一格不能省**：`bool t = true;` 若发成 `let t: bool = 1;` 就是本语言的类型错。
    `bool` 与 `int` 是不是一回事，正是 Java/C#/C++ 这一族与 C 的分界线，而在这条线上
    `true` 必须**带着它的类型**过去（见 `Emitter.ty_of` / `Emitter.raw`）。
    """

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


class Bin:
    __slots__ = ("op", "l", "r", "line")

    def __init__(self, op: str, l, r, line: int) -> None:
        self.op, self.l, self.r, self.line = op, l, r, line


class Decl:
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


class For:
    __slots__ = ("init", "cond", "step", "body", "line")

    def __init__(self, init, cond, step, body: list, line: int) -> None:
        self.init, self.cond, self.step, self.body, self.line = init, cond, step, body, line


class ExprStmt:
    __slots__ = ("e", "line")

    def __init__(self, e, line: int) -> None:
        self.e, self.line = e, line


class Fn:
    __slots__ = ("ret", "name", "params", "body", "line")

    def __init__(self, ret: str, name: str, params: list, body: list, line: int) -> None:
        self.ret, self.name, self.params, self.body, self.line = ret, name, params, body, line


# ---------------------------------------------------------------- 解析


class Parser:
    """花括号族的递归下降。**只有这一层** —— 不做预处理、不做类型检查。

    语言差异全部走 `self.d`（方言表），这里一行 `if <语言>` 都不该有。
    """

    def __init__(self, toks: list[tuple[str, str, int]], d: "Dialect") -> None:
        self.t = toks
        self.i = 0
        self.d = d

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
            raise CError(f"第 {t[2]} 行: 期望 `{text}`，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t

    def ident(self) -> tuple[str, int]:
        t = self.peek()
        if t[0] != "id":
            raise CError(f"第 {t[2]} 行: 期望标识符，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t[1], t[2]

    # ---- 声明说明符
    def spec(self, local: bool = False) -> tuple[str, str, int]:
        """吃 `[unsigned] int` / `void` / … -> `(Loment 类型, 变量名, 行)`。

        **顺手把"这一条声明里出现过哪些词"记在 `self.last_spec` 上** ——
        `unit()` 要靠它判"这是不是一条常量声明"（见 `Dialect.const_words`）。
        """
        words: list[str] = []
        seen: set[str] = set()
        self.last_spec = seen
        line = self.peek()[2]
        while self.peek()[0] == "id" and self.peek()[1] in self.d.spec_words:
            w = self.peek()[1]
            seen.add(w)
            if w in self.d.bad_spec:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持存储类/限定符 `{w}`")
            if w in self.d.linkage:
                # **函数那一层**收下并丢掉：`static` 是内部链接、`inline` 是内联建议，
                # 而这里翻的是**整个单元的全部函数** —— 没有第二个翻译单元能再定义同名函数，
                # 于是"内部链接"在这个语境里没有可分辨的差别。`const` 在只有标量、没有指针的
                # 子集里也一样（源侧本就保证它不会被改）。见文件头 §语义选择 3。
                #
                # **局部位置上不是这么回事**：那里 `static` 说的是**静态存储期**（值跨调用
                # 保留），与链接无关，丢掉会改程序的数。`local=True` 时拒掉 —— 见
                # `Dialect.local_storage`。
                if local and w in self.d.local_storage:
                    raise Unsupported(
                        f"第 {self.peek()[2]} 行: 函数内的 `{w}` 改的是**存储期**"
                        f"（值跨调用保留），Stage A 没有对应的表示 —— 丢掉它会让每次调用"
                        f"都从初值重来（**产物照样编得过，只是数不对**）。这一处不收。")
                self.i += 1
                continue
            if w in self.d.agg:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持聚合/枚举类型 `{w}`"
                                  f"（Stage A 只做整数标量）")
            words.append(w)
            self.i += 1
        if not words:
            t = self.peek()
            raise CError(f"第 {t[2]} 行: 期望类型名，得到 {t[1] or '<结束>'!r}")
        raw = re.sub(r"\s+", " ", " ".join(words)).strip()
        if raw not in self.d.types:
            raise Unsupported(f"第 {line} 行: 类型 `{raw}` 不在 Stage A 子集里")
        name, _ = self.ident()
        # **不在这里判 `(`** —— 函数**定义**的 `(` 就紧跟名字，那是正常形状。
        # 函数指针（`int (*fp)(int)`）会因为 `(` 出现在要标识符的位置被 `ident()` 拒掉。
        if self.at("*") or self.at("["):
            raise Unsupported(f"第 {line} 行: 不支持指针/数组声明 `{name}`")
        return self.d.types[raw], name, line

    # ---- 表达式
    def expr(self) -> object:
        return self.binary(1)

    def binary(self, minp: int) -> object:
        left = self.unary()
        while True:
            t = self.peek()
            # **只有这些**。别把 `<<` 也算进来 —— 它是正经的二元运算符；
            # `<<=` 那种复合赋值在 `tokens()` 那一步就已经被点名拒掉了。
            if t[0] == "op" and t[1] in ("?", "=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="):
                # **点名**这些，别让它们掉进"期望 `;`"那种指不到点子的报错里。
                # 赋值表达式（`a = (b = c)`）与三元都是 C 的表达式，而 Loment 的 `=` 是语句。
                raise Unsupported(f"第 {t[2]} 行: 不支持表达式里的 `{t[1]}`"
                                  + ("（三元 `?:` 改写成 if/else；赋值改写成单独一条语句）"
                                     if t[1] == "?" else
                                     "（Loment 的赋值是语句，不是表达式）"))
            if t[0] != "op" or t[1] not in self.d.bin:
                return left
            p = self.d.precof[t[1]]
            if p < minp:
                return left
            self.i += 1
            right = self.binary(p + 1)  # 左结合
            left = Bin(t[1], left, right, t[2])

    def unary(self) -> object:
        t = self.peek()
        # `~` **不在这里**：它在 `_REJECT_NAMED` 里被分词那一步点名拒掉（理由与两条出路
        # 见那张表）。留在这里收下的话，前端照发 `(~v)` 而 Loment 没有它 ——
        # 产物死在后面（词法）而不是这里。
        if t[0] == "op" and t[1] in ("-", "!"):
            self.i += 1
            return Un(t[1], self.unary(), t[2])
        if t[0] == "op" and t[1] in ("+", "*", "&"):
            if self.d.name == "java" and t[1] == "+":
                raise Unsupported(
                    f"第 {t[2]} 行: 不支持一元 `+`（Java 允许它，但这个子集没有"
                    f"把它翻过来的语义；去掉它即可）。")
            raise Unsupported(f"第 {t[2]} 行: 不支持一元 `{t[1]}`"
                              f"（取地址/解引用不在 Stage A 子集里）")
        return self.primary()

    def primary(self) -> object:
        t = self.peek()
        if t[1] == "(":
            self.i += 1
            e = self.expr()
            self.want(")")
            return e
        if t[0] == "chr":
            self.i += 1
            return Lit(_char_value(t[1], t[2]), t[2])
        if t[0] == "str":
            raise Unsupported(
                f"第 {t[2]} 行: 不支持字符串字面量（这一族把字符串值当引用类型，"
                f"而 Stage A 的子集没有字符串值；`String` 本身也不在类型表里）。")
        if t[0] == "num":
            self.i += 1
            return Lit(_parse_int_lit(t[1], t[2], self.d), t[2])
        if t[0] == "id" and t[1] in ("true", "false") and self.d.bool_literals:
            # 这一族的源码里这两个就是**字面量**（见 `Dialect.bool_literals`），
            # 而 Loment 也有 `true` / `false` —— 直接映过去。
            # 不走 `Var`：那样会报"用了没声明过的 `true`"，一句指不到点子的话
            # （真原因是"这是个布尔字面量，不是变量"）。
            self.i += 1
            return Lit(1 if t[1] == "true" else 0, t[2], True)
        if t[0] == "id":
            if t[1] in self.d.kw:
                raise Unsupported(f"第 {t[2]} 行: 表达式里不支持关键字 `{t[1]}`")
            self.i += 1
            if self.at("."):
                # 属性/字段访问（Java 的 `System.exit`、`this.w`、C++ 的 `ns::f`）。
                # **点名**它 —— 不点的话会掉进"`System` 后面期望 `=` 或 `(`"，指不到点子。
                raise Unsupported(f"第 {t[2]} 行: 不支持属性/字段访问 `{t[1]}.…`"
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
        raise CError(f"第 {t[2]} 行: 期望表达式，得到 {t[1] or '<结束>'!r}")

    # ---- 语句
    def stmt(self) -> object:
        t = self.peek()
        if t[1] == "{":
            raise Unsupported(f"第 {t[2]} 行: 不支持裸块 `{{ }}`"
                              f"（Loment 没有块语句；把里面的声明写到外层）")
        if t[1] == "if":
            self.i += 1
            self.want("(")
            cond = self.expr()
            self.want(")")
            then = self.block_or_stmt()
            els = None
            if self.at("else"):
                self.i += 1
                els = self.block_or_stmt()
            return If(cond, then, els, t[2])
        if t[1] == "while":
            self.i += 1
            self.want("(")
            cond = self.expr()
            self.want(")")
            return While(cond, self.block_or_stmt(), t[2])
        if t[1] == "for":
            return self.for_()
        if t[1] in ("do", "switch", "goto", "case", "default"):
            raise Unsupported(f"第 {t[2]} 行: 不支持 `{t[1]}`")
        if t[1] == "break":
            raise Unsupported(f"第 {t[2]} 行: 不支持 `break`"
                              f"（Loment 没有；改写条件或提前 return）")
        if t[1] == "continue":
            raise Unsupported(f"第 {t[2]} 行: 不支持 `continue`"
                              f"（Loment 没有；用一个 did 标志改写）")
        if t[1] == "return":
            self.i += 1
            if self.at(";"):
                self.i += 1
                return Return(None, t[2])
            e = self.expr()
            self.want(";")
            return Return(e, t[2])
        return self.simple()

    def block_or_stmt(self) -> list:
        """`{ … }` 或单条语句 —— 两种都返回**语句列表**。"""
        if self.at("{"):
            return self.block()
        return [self.stmt()]

    def block(self) -> list:
        self.want("{")
        out = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CError(f"第 {self.peek()[2]} 行: 块没闭合")
            out.append(self.stmt())
        self.want("}")
        return out

    def simple(self, semi: bool = True) -> object:
        """`int x [= e];` / `x = e;` / `f(...);`

        `semi=False` 给 `for` 的**步进**用 —— 它后面是 `)` 不是 `;`。
        """
        t = self.peek()
        if t[0] == "id" and t[1] in self.d.spec_words:
            ty, name, line = self.spec(local=True)
            if self.at(";"):
                self.i += 1
                return Decl(ty, name, None, line)
            self.want("=")
            e = self.expr()
            if semi:
                self.want(";")
            return Decl(ty, name, e, line)
        if t[0] != "id":
            raise CError(f"第 {t[2]} 行: 期望语句，得到 {t[1] or '<结束>'!r}")
        if t[1] in self.d.kw:
            raise Unsupported(f"第 {t[2]} 行: 这里不支持 `{t[1]}`")
        name, line = self.ident()
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
        raise CError(f"第 {self.peek()[2]} 行: `{name}` 后面期望 `=` 或 `(`，"
                     f"得到 {self.peek()[1]!r}")

    def for_(self) -> object:
        t = self.peek()
        self.i += 1
        self.want("(")
        init = None
        if self.at(";"):
            self.i += 1
        else:
            init = self.simple()
        cond = None
        if not self.at(";"):
            cond = self.expr()
        self.want(";")
        step = None
        if not self.at(")"):
            step = self.simple(semi=False)
        self.want(")")
        return For(init, cond, step, self.block_or_stmt(), t[2])

    # ---- 顶层
    def unit(self) -> list:
        out = []
        while self.peek()[0] != "eof":
            if self.at("#"):
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持预处理指令")
            ret, name, line = self.spec()
            if not self.at("("):
                # **顶层常量声明**（Java 的 `static final`、C# 的 `const`、C++ 的
                # `constexpr`）：它**不发成函数也不发成全局变量** —— `potato_from`
                # 已经把它收进 `consts`（`_JAVA_CONST` 那一条），`lomt_from` 会发
                # `pub const`。所以翻译器**跳过**它，只要认得那个名字。
                #
                # **`const_words` 为空就是这一族不收**（C 是 —— `potato_from` 也不收
                # 它的全局量，跳过会造出一个下游认不出的名字）。所以这一格是**方言
                # 说了算**，不是"看见 `=` 就跳"。
                if (self.d.const_words & self.last_spec) and self.at("="):
                    self.i += 1
                    v = self.peek()
                    neg = False
                    if self.at("-") and self.peek(1)[0] == "num":
                        neg = True
                        self.i += 1
                        v = self.peek()
                    if v[0] != "num":
                        raise Unsupported(
                            f"第 {v[2]} 行: 顶层常量 `{name}` 的值不是整数字面量"
                            f"（`potato_from` 只收得来整数常量）")
                    self.i += 1
                    self.want(";")
                    continue
                raise Unsupported(f"第 {line} 行: 不支持全局变量 `{name}`")
            self.i += 1
            params = []
            if not self.at(")"):
                if self.at("void") and self.at(")", 1):
                    self.i += 1
                else:
                    params.append(self.spec())
                    while self.at(","):
                        self.i += 1
                        params.append(self.spec())
            self.want(")")
            if self.at(";"):
                raise Unsupported(f"第 {line} 行: 函数 `{name}` 只有声明没有体"
                                  f"（Stage A 翻的是有体的函数）")
            out.append(Fn(ret, name, params, self.block(), line))
        return out


# ---------------------------------------------------------------- 发射

#: Loment 的保留字 —— 生成的标识符不能撞上。**这一张四门共用**（保留字是本语言的）。
#: 目标整型的取值范围。`narrow_literal_check` 用它把 Java 的编译期窄化错误
#: 在翻译期报出来，而不是让 `let b: i8 = 200;` 静默回绕。
_INT_RANGE = {
    "i8": (-128, 127), "i16": (-32768, 32767),
    "i32": (-(2 ** 31), 2 ** 31 - 1), "i64": (-(2 ** 63), 2 ** 63 - 1),
    "u8": (0, 255), "u16": (0, 65535),
    "u32": (0, 2 ** 32 - 1), "u64": (0, 2 ** 64 - 1),
}


def _int_const(e: object) -> int | None:
    """Fold a literal or a negated literal. Anything else is unknown (None)."""
    if isinstance(e, Lit) and not e.b:
        return e.v
    if isinstance(e, Un) and e.op == "-" and isinstance(e.e, Lit) and not e.e.b:
        return -e.e.v
    return None


def _parse_int_lit(text: str, line: int, d: "Dialect") -> int:
    """Parse decimal / hex / binary / octal, with `_` separators and an `L` suffix.

    Java and C both treat a leading zero as octal; Python's `int(s, 0)` does not,
    and raises on `010`.  The old path leaked that `ValueError` as a raw traceback
    or produced a C-specific message.  This helper names the literal instead.
    """
    if "_" in text and not d.digit_separators:
        raise Unsupported(
            f"第 {line} 行: 这一门不收数字分隔符 `_`（Java 的 `1_000` 要写成 `1000`）。")
    t = text.replace("_", "")
    wide = t[-1:] in ("l", "L")
    if wide:
        t = t[:-1]
    low = t.lower()
    is_octal = low.startswith("0o") or (len(t) > 1 and t[0] == "0"
                                        and not low.startswith(("0x", "0b")))
    if low.startswith("0b") and not d.binary_literals:
        raise Unsupported(
            f"第 {line} 行: 这一门不收二进制字面量 `{text}`（写成十六进制或十进制）。")
    if is_octal and not d.octal_literals:
        raise Unsupported(
            f"第 {line} 行: 读不了这个整数字面量 `{text}` —— 这一门只收**十/十六进制**"
            f"（`docs/186` §4）。`010` 是八进制（等于 8）；写成 `8` 或 `0x8`。")
    try:
        if low.startswith("0x"):
            v = int(t, 16)
        elif low.startswith("0b"):
            v = int(t, 2)
        elif low.startswith("0o"):
            v = int(t, 8)
        elif len(t) > 1 and t[0] == "0":
            v = int(t, 8)
        else:
            v = int(t, 10)
    except ValueError:
        raise Unsupported(
            f"第 {line} 行: 读不了这个整数字面量 `{text}`。支持十进制、`0x` 十六进制、"
            f"`0b` 二进制与 `0` 开头的八进制；`_` 分隔符与 `L` 后缀可以写。") from None
    if (getattr(d, "hex_wrap", False) and low.startswith("0x") and not wide
            and 2 ** 31 <= v < 2 ** 32):
        v -= 2 ** 32
    return v


def _char_value(text: str, line: int) -> int:
    """Java/C character literal -> its integer value."""
    body = text[1:-1]
    if body.startswith("\\u") and len(body) == 6:
        try:
            return int(body[2:], 16)
        except ValueError:
            pass
    esc = {"n": 10, "t": 9, "r": 13, "\\": 92, "'": 39, '"': 34, "0": 0,
           "b": 8, "f": 12}
    if body.startswith("\\") and len(body) == 2 and body[1] in esc:
        return esc[body[1]]
    if len(body) == 1:
        return ord(body)
    raise Unsupported(
        f"第 {line} 行: 读不了这个字符字面量 `{text}`（只收单字符与常见转义）。")


def _lit32(e: object) -> None:
    """**整数字面量必须在 32 位以内**，否则**拒**（不再静默截断）。

    C 里超 32 位的字面量是 `long`（或 `unsigned`），而这一门的 `int` 是 32 位。
    更麻烦的是：本语言里**没标注宽度的整数字面量是不确定的**，实测 `8589934591`
    读出来就是 `4294967295` —— 在**解析那一层**截断，事后 `as i64` 也救不回来
    （实测 `(8589934591 as i64) == (4294967295 as i64)` 是**真**）。

    子集没有更宽的字面量写法（没有后缀、没有专门的字面量类型），所以这里**拒** ——
    静默截断会让 `0x1FFFFFFFF == 0xFFFFFFFF` 从假变真。
    """
    if not (-(2 ** 31) <= e.v < 2 ** 31):
        raise Unsupported(
            f"第 {e.line} 行: 整数字面量 {e.v} 超出 32 位。C 里它是一个 `long`"
            f"（或 `unsigned`），而这一门的 `int` 是 32 位、**没有更宽的字面量写法**；"
            f"放着不管它会在解析那一层被静默截断。请把它拆小，或者改成运行时算。")


#: **比较**运算符（Loment 侧的拼法）—— 这几个的结论会被**宽窄 / 有符号无符号**翻掉，
#: 所以 `Emitter.pinned` 只对它们的操作数生效（见那一处）。
#: `==` / `!=` 也在内：宽窄不同时它们同样给出相反的答案
#: （`0x1FFFFFFFF == 0xFFFFFFFF` 在 64 位上为假、在 32 位上为真）。
_CMP_OPS = frozenset({"<", ">", "<=", ">=", "==", "!="})

#: **算术**运算符（转换要按"寻常算术转换"插在两边的那些）。移位不在内 ——
#: C 里移位的结果类型**只看左操作数**，右操作数不参与提升（`Dialect.usual_arith`）。
_ARITH_OPS = frozenset({"+", "-", "*", "/", "%", "&", "|", "^"})

#: 整型的**秩**（C 的寻常算术转换按它走）。秩 0 那几档**比 `int` 还窄** ——
#: C/C++ 里它们先被**整型提升**到 `int`（下面 `_promote`），再参与寻常算术转换。
#: `int` 与 `unsigned`（同 `signed`/`unsigned char` 那类）同秩，`long` 更高。
_INT_RANK = {"i8": 0, "u8": 0, "i16": 0, "u16": 0, "i32": 1, "u32": 1, "i64": 2, "u64": 2}
_INT_UNSIGNED = frozenset({"u8", "u16", "u32", "u64"})


def _promote(t: str) -> str:
    """**整型提升**：比 `int` 窄的一律先升到 `i32`（`u16` 的最大值也装得下）。"""
    return "i32" if _INT_RANK[t] == 0 else t


def _usual_arith(a: str | None, b: str | None) -> str | None:
    """C 的**寻常算术转换**：给了两边的整型，返回运算该在哪个宽度做。

    `None` 表示"这一侧不定型"（字面量，或全是字面量的式子）—— 由**上下文**决定，
    所以另一侧说了算；两侧都是 `None` 才返回 `None`。

    C 的规则（就这几档，`docs/186` §4 那张表）：
      0. 比 `int` 窄的先**整型提升**到 `i32`。
      1. 同型 ⇒ 同型。
      2. 同秩不同符号 ⇒ **无符号**那侧（`int` + `unsigned` = `unsigned`）。
      3. 秩不同 ⇒ 秩高的那侧**装得下**秩低那侧的全部值就取它
         （`long` 装得下 `int` / `unsigned`）。
      4. 否则 ⇒ 秩高那种的**无符号版**（`int` + `unsigned long` = `unsigned long`）。
    """
    if a is None:
        return b
    if b is None:
        return a
    a, b = _promote(a), _promote(b)
    if a == b:
        return a
    ra, rb = _INT_RANK[a], _INT_RANK[b]
    if ra == rb:
        return a if a in _INT_UNSIGNED else b
    hi, lo = (a, b) if ra > rb else (b, a)
    # **能装下**：秩高的那侧是无符号时不一定装得下同秩的有符号 —— 而这里秩不同，
    # 所以只要高的那侧是有符号就够（`i64` 装得下 `i32` / `u32`）。
    if hi not in _INT_UNSIGNED:
        return hi
    return "u64" if _INT_RANK[hi] == 2 else "u32"

_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


class Emitter:
    """语法树 -> Loment 源码（一个函数一份 Emitter）。

    与 `Parser` 一样：语言差异全走 `self.d`，这里不该有 `if <语言>`。
    """

    def __init__(self, fns: dict[str, str], d: "Dialect",
                 consts: dict[str, str] | None = None,
                 fname: dict[str, str] | None = None,
                 fparams: dict[str, list[str]] | None = None) -> None:
        #: 本单元**所有**函数的返回类型。调用点的类型靠它 —— 子集里没有跨单元调用。
        self.fns = fns
        #: 函数名 -> 形参的 Loment 类型表。**实参转换要按它**（`#125`：`int x = 7;
        #: f(x)` 调 `unsigned int f(unsigned int)`，C 里 `x` 隐式转 `u32`）。
        #: 不知道的函数（`extern`、跨单元）没有条目 ⇒ **不转** —— 宁可少转也别乱转。
        self.fparams: dict[str, list[str]] = dict(fparams or {})
        #: C 函数名 -> 发出去的名字（`translate` 算好的）。**函数名也要过 `safe()`** ——
        #: 撞上本语言保留字的函数名会让**产物本身**过不了词法（`pub fn command(…)` 里
        #: `command` 是语句关键字），而调用点还得跟着改，所以整份单元算一次、两处共用。
        #: 不在表里的名字（比如 `extern fn` 那种、名字由 `lomt_from` 发的）**照原样**。
        self.fname = dict(fname or {})
        self.d = d
        #: **模块常量的名字**。`pub const` 由 `lomt_from` 从 Potato 的 `consts` 发，
        #: 翻译器只要**认得那些名字**（顶层声明本身在解析时被跳过）。不给这张表的话
        #: 函数体里一引用常量就报"用了没声明过的"。
        self.consts: dict[str, str] = dict(consts or {})
        #: C 名字 -> 发出去的名字。`for` 的改名外提就靠这张表（见文件头 §语义选择 2）。
        self.vars: dict[str, str] = {}
        #: **块作用域栈**（`#120`）。每进一个 `if` / `while` / `for` 的体压一层，
        #: 记着那一层里重新绑定过的名及其外层原值 —— 出块时逆序还回去（`self.block`）。
        self.scopes: list[dict[str, tuple[str | None, str | None]]] = []
        #: **变量声明时的类型**（Loment 类型串）。发射器只需要分**两档**：
        #: `bool` 与"其它"（都当整数）。不分的话 `bool ok = …; if (ok) …` 会走错：
        #: `ty_of` 一律报 "int"，于是 Java 那条 `coerce_int_to_bool=False` 的路
        #: 会对一个**布尔**变量报"这里要的是条件，给的是整数" —— 一句**错的**诊断，
        #: 而那段 Java 完全合法。C++ 那一门的语料一上来就撞到了。
        self.varty: dict[str, str] = {}
        self.n = 0
        self.lines: list[str] = []
        #: 当前这个函数**声明的**返回类型（`emit_fn` 里设）。`return` 要按它决定
        #: 那个表达式是在"整数场合"还是"布尔场合"—— 见 `stmt` 的 `Return` 那支。
        self.ret: str = "()"

    def bind(self, raw: str) -> str:
        """C 局部名 -> 发出去的名字。**同一个 C 名字永远给同一个**（重复声明照旧），
        但**不同的** C 名字落到同一个名字上时，后到的让开。

        为什么不能只靠 `safe()`：它是**无状态的纯函数**，只知道"这个词是不是保留字"。
        撞了保留字加后缀之后完全可能撞上**用户自己写的**名字 ——
        `int let = 1; int let_c = 2;` 里 `let` 被改成 `let_c`，与用户那个真叫 `let_c` 的
        撞上，发出去是两条并排的 `let let_c`（本语言照收，第一条成死代码，**退 0**）。
        """
        nm = self.d.safe(raw)
        while raw not in self.vars and nm in self.vars.values():
            nm += "_"
        return nm

    def decl_bind(self, name: str, line: int) -> str:
        """一条**声明**要发出去的名字 —— 并管住"同名"这件事（`#119` / `#120`）。

        * **外层已经有同名** ⇒ 源语言里这是**遮蔽**（块作用域），而本语言**没有块作用域**
          （`docs/188` §0.1.1 实测：块里 `let` 的名块外看得见），不能靠 `{ }` 圈住。
          所以**改名**，出块时把外层那个还回去（`self.block` 管还原）。
        * **同一层里再来一条同名** ⇒ 源语言里是编译错（C 报 "redefinition"）——**拒**。
          放过去的话它静默变成一次赋值，程序算出不同的数（`#120` 的 Also 那张表：
          `int x = 1; int x = 2; return x;` 在 C 里编不过，在这里却答 2、退 0）。
        """
        if self.scopes:
            cur = self.scopes[-1]
            if name in cur:
                raise Unsupported(
                    f"第 {line} 行: `{name}` 在同一个块里声明了两次"
                    f"（源语言里这是编译错）—— 放过去它只会变成一次赋值，数就不同了")
            old = self.vars.get(name)
            cur[name] = (old, self.varty.get(name))
            if old is not None:
                nm = self.fresh(name)
                self.vars[name] = nm
                return nm
        elif name in self.vars:
            raise Unsupported(
                f"第 {line} 行: `{name}` 在同一个函数里声明了两次"
                f"（源语言里这是编译错）—— 放过去它只会变成一次赋值，数就不同了")
        nm = self.bind(name)
        self.vars[name] = nm
        return nm

    def block(self, body: list, depth: int) -> None:
        """发一个**块**（`if` / `while` / `for` 的体），并**保住源语言的块作用域**。

        体里声明的名出块之后就不该再看得见（源语言的规则）。本语言没有块作用域，
        所以：体里**遮蔽**外层同名的声明走改名（`decl_bind`），体里**新**声明的名
        出块时**撤掉** —— 不撤的话，函数后面再声明一个同名会被当成"重复声明"而**误拒**
        （`int f(int c) { if (c) { int y = 1; } int y = 2; return y; }` 在 C 里是合法的）。
        """
        saved: dict[str, tuple[str | None, str | None]] = {}
        self.scopes.append(saved)
        try:
            for s in body:
                self.stmt(s, depth)
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
            out = f"{self.d.safe(base)}__{self.n}"
            if out not in self.vars.values():
                return out

    def out(self, s: str, depth: int) -> None:
        self.lines.append("    " * (depth + 1) + s)

    def gap(self) -> None:
        """控制流前面留一个空行 —— **但别在开头留**（`{` 后面直接一个空行很难看），
        也别连着留两个。"""
        if not self.lines or self.lines[-1] == "" or self.lines[-1].endswith("{"):
            return
        self.lines.append("")

    # ---- 类型：只有 `int` / `bool` / `void` 三档
    def ty_of(self, e: object) -> str:
        if isinstance(e, Lit):
            # `true` / `false` 是**布尔字面量**，不是 `1` / `0`（见 `Lit`）
            return "bool" if e.b else "int"
        if isinstance(e, Var):
            # **看它声明时是什么**（见 `self.varty`），不是一律当整数
            return "bool" if self.varty.get(e.name) == "bool" else "int"
        if isinstance(e, Call):
            r = self.fns.get(e.name)
            if r is None:
                raise Unsupported(
                    f"第 {e.line} 行: 调用了本单元没有的函数 `{e.name}`"
                    f"（Stage A 不跨单元 —— 要调外部函数请留着 `pub extern fn` 那条路）")
            return "void" if r == "()" else "int"
        if isinstance(e, Un):
            return "bool" if e.op == "!" else "int"
        if isinstance(e, Bin):
            return "bool" if self.d.bin[e.op][2] else "int"
        raise AssertionError(type(e))

    def const_name(self, n: str) -> bool:
        """这是不是一个**模块常量**（而不是局部名）。"""
        return n in self.consts

    # ---- 整型宽度：只有**已知类型**的式子参与提升（字面量不定型，由上下文定）
    def num_ty(self, e: object) -> str | None:
        """表达式在源语言宽度规则下的**整型**；`None` = "不定型或不参与提升"。

        `None` 有两种来源，处理它们的办法一样（**不主动转**，交给接收方），
        所以合成一个返回值：
          * **字面量**（或全是字面量的式子）—— 不定型，接收方说了算（`docs/188` §7.1.1；
            一律钉成 `i32` 会弄坏 `u8 probe() { return 255; }` 那种，C# 的 `Byte.cs` 就是这么红的）；
          * 结果**不是整数**（比较 / `&&` / `||` 出 bool）—— 提升不适用。

        **调用点跟着改名走**（`self.fname`）不在这里管 —— 这里只问类型。
        """
        if isinstance(e, Var):
            t = self.varty.get(e.name)
            return t if t in _INT_RANK else None
        if isinstance(e, Call):
            t = self.fns.get(e.name)
            return t if t in _INT_RANK else None
        if isinstance(e, Un):
            # 一元 `-` 出的是**提升后**的操作数类型（`-x` 里 `x` 已经是那一档了）。
            return self.num_ty(e.e) if e.op == "-" else None
        if isinstance(e, Bin):
            if e.op in ("&&", "||") or self.d.bin[e.op][2]:
                return None                      # 比较 / 逻辑 ⇒ bool
            if e.op in ("<<", ">>"):
                return self.num_ty(e.l)          # C：移位的类型**只看左操作数**
            return _usual_arith(self.num_ty(e.l), self.num_ty(e.r))
        return None

    def conv(self, e: object, ty: str | None) -> str:
        """把表达式转成 `ty`（`ty` 为 `None` = 不转，按它自己的类型发）。

        两条纪律（都写在 `docs/188` §7.1.1 与 #125 / #123 里）：
          * **只转已知类型的式子** —— 字面量不定型，接收方说了算（见 `num_ty`）；
          * **同型不加** —— 别写 `(v as i32)` 这种废话。
        """
        if ty is None:
            return self.ex(e, "int")
        t = self.num_ty(e)
        if t is None or t == ty:
            return self.ex(e, "int")
        return f"({self.ex(e, 'int')} as {ty})"

    def want_of(self, ty: str) -> str:
        """一个**声明出来的** Loment 类型，在这个发射器眼里是"整数场合"还是
        "布尔场合"。

        **所有"右边赋给左边"的地方都要走它**（`Decl` / `Assign` / `Return` /
        `for` 的初值）—— 写死 `'int'` 的话，`bool ok = x > 0;` 会发成
        `let ok: bool = (x > 0) as i32;`：左边声明是 `bool`、右边是 `i32`，
        本语言的类型错。这一族里只有 `bool` 与整数两档要分（见 `self.varty`）。
        """
        return "bool" if ty == "bool" else "int"

    def sink(self, e: object, ty: str) -> str:
        """表达式放进一个**声明类型为 `ty` 的位置**（赋值 / 返回 / 实参 / `for` 初值）。

        源语言在这里做**隐式整型转换**（C：`unsigned int f(unsigned int v){return v;}`
        被 `int` 调用时，`v` 按 `u32` 收、按 `i32` 还 —— `#125`）。规则能表达就转
        （这一门的 `as` 做得到），所以**转**；不开 `usual_arith` 的方言
        （Java / C# / Go）**不转** —— 它们的规则各不同，硬转会写出那些语言本不接受
        的程序。`bool` 位置照旧走 `ex` 那条投影（`want_of`）。
        """
        if self.d.narrow_literal_check and ty in _INT_RANGE:
            v = _int_const(e)
            if v is not None:
                lo, hi = _INT_RANGE[ty]
                if not lo <= v <= hi:
                    raise Unsupported(
                        f"第 {e.line} 行: 字面量 {v} 超出 {ty} 的范围 "
                        f"[{lo}, {hi}]。Java 里这是一条编译期错误（窄化要显式写 "
                        f"`({ty})`）；放着不管会静默回绕。")
        w = self.want_of(ty)
        if w != "int" or not self.d.usual_arith:
            return self.ex(e, w)
        return self.conv(e, ty if ty in _INT_RANK else None)

    def ex(self, e: object, want: str) -> str:
        """表达式，**在 `want` 这个上下文里**的写法（必要时补转换）。

        **两条转换要不要补，是方言表说了算** —— 这是这一族里最会咬人的一格：
        C / C++ 的 `&&` 出 `int`、`if (x)` 收标量，两个方向都要补；Java / C# 的
        `&&` 出 `boolean`、条件只收 `boolean`，**一个都不用**。同一处的差别，
        按"能表达的就转、表达不出来的就报错"那条判据，两族结论相反。
        """
        got = self.ty_of(e)
        if got == "void":
            raise Unsupported(f"第 {e.line} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e)
        if got == want:
            return raw
        if got == "int" and want == "bool":
            if not self.d.coerce_int_to_bool:
                raise Unsupported(
                    f"第 {e.line} 行: 这里要的是**条件**，给的是整数。"
                    f"{self.d.name} 的 `&&`/`||` 出 boolean、条件也只收 boolean，"
                    f"所以这里本来就该是个布尔表达式（多半写错了）")
            return f"{raw} != 0"      # `if (x)`：非零即真
        if got == "bool" and want == "int":
            if not self.d.coerce_bool_to_int:
                raise Unsupported(
                    f"第 {e.line} 行: 这里要的是**整数**，给的是布尔。"
                    f"{self.d.name} 的布尔与整数**不是一回事**（不像 C 那样能互相顶），"
                    f"所以这里本来就该是个整数表达式（多半写错了）")
            # `int x = (a < b);`：bool -> 0/1。
            #
            # **整条投影要自己套一层括号**：`ex()` 的返回值是拿去当**操作数**用的
            # （`raw(Bin)` 把两侧直接拼进 `({左} {op} {右})`），所以它必须自足。
            # 原先只括了 `raw`、没括整条，于是 `as i32` 后面**紧接着** `<` / `<=` / `<<`
            # 时被本语言的 parser 当成泛型实参的开头：
            #     (2 < 3) < c   ->   ((2 < 3)) as i32 < c   ->   `11:40 期望 >，得到 ')'`
            # 而这是子集内的普通 C（比较套比较），换个不成功的写法（`+ 1`）就没事 ——
            # 所以 `loment/ctrans/coerce.c` 那份专挑这条缝的语料一直没照到它。
            return f"(({raw}) as {self.d.int_default})"
        raise AssertionError((got, want))

    def pinned(self, e: object, ty: str | None = None) -> str:
        """把表达式的操作数**按 `ty` 钉住宽度**，字面量**叶子式**地钉。

        只用在**比较**（`< > <= >= == !=`，见 `_CMP_OPS`）的两侧，理由是
        **只有它们会被宽窄 / 有符号无符号翻转结论**：

            本语言里没标注宽度的整数字面量是**不确定的**。实测 `(0 - 1) < 0` 编出来是
            `icmp ult i32`（**无符号**），而 `((0 as i32) - (1 as i32)) < (0 as i32)`
            才是 `icmp slt`。C 在这里是**有符号**的 —— 于是 `-1 < 0` 在 C 是真、在这边
            是假。给操作数带上 `as`，整条表达式就有了确定的类型。

        为什么要**递归到叶子**而不是给整条套一个 `as`：`as` 套在**没定型**的式子上
        改不了它内部怎么算（`(0 - 1)` 里那个减法还是无符号的）。所以必须一路钉到
        每个字面量。`ty` 默认取本族的 `int_default`（没有宽度信息时的兜底）；
        C 那门会传**提升后**的类型（见 `raw(Bin)`）—— 于是 `int a; unsigned b; a > b`
        钉成 `u32`，与 C 的规则一致。

        **别处（赋值 / 返回 / 实参）不走这里** —— 那些位置的宽度由**接收方**决定
        （`u8` / `u64` …），走 `conv`，而且**只转已知类型的式子**。

        超 i32 的字面量**拒**（`_lit32`）—— 这一门没有更宽的字面量写法，而解析层
        就会截断，`as` 救不回来。所以到不了这里。
        """
        if ty is None:
            ty = self.d.int_default
        if isinstance(e, Lit) and not e.b:
            _lit32(e)
            # **括号自己带上**（与 `ex` 里那处 `as` 同一个理由）：`(0 as i32) < (1 as i32)`
            # 里的 `i32 <` 否则会被 parser 当成**泛型实参**的开头。
            return f"({e.v} as {ty})"
        if (isinstance(e, Un) and e.op == "-" and isinstance(e.e, Lit)
                and not e.e.b):
            # `-2147483648`（`#131` 那张表的最后一行）：**正的** 2147483648 不在 i32 里，
            # 可整个式子（取负之后）在。`_lit32` 看的是那个正字面量，于是会误拒 ——
            # 先把负号并进常量再发，既过得了那道闸、又保住了有符号比较（C 给 1）。
            #
            # 只在**有符号**那一侧这么做：C 里十进制常量 `2147483648` 是 `long`，
            # 取负仍是 `long`；把它塞进无符号场合是另一条（未建）的规则，照旧拒。
            v = -e.e.v
            if ty in ("i8", "i16", "i32", "i64") and -(2 ** 31) <= v < 2 ** 31:
                return f"({v} as {ty})"
        if isinstance(e, Bin) and e.op not in ("&&", "||"):
            op, _want, _b = self.d.bin[e.op]
            return f"({self.pinned(e.l, ty)} {op} {self.pinned(e.r, ty)})"
        t = self.num_ty(e)
        if t is not None and t != ty:
            return f"({self.ex(e, 'int')} as {ty})"
        return self.ex(e, "int")

    def raw(self, e: object) -> str:
        """表达式在**它自己的类型**下的写法。括号一律加上 —— 别让读者去猜优先级。"""
        if isinstance(e, Lit):
            if e.b:
                return "true" if e.v else "false"
            _lit32(e)
            return str(e.v)
        if isinstance(e, Var):
            if self.const_name(e.name):
                return e.name          # 模块常量名照抄 —— `pub const` 在同层
            if e.name not in self.vars:
                raise Unsupported(
                    f"第 {e.line} 行: 用了没声明过的 `{e.name}`"
                    f"（顶层常量声明在解析时被跳过，`pub const` 由 `lomt_from` 从 Potato "
                    f"的 `consts` 发 —— 名字对不上的话就是那一步没收它）")
            return self.vars[e.name]
        if isinstance(e, Call):
            # **那道闸在这里也要过一遍**（`docs/198` §3）。`ty_of` 对 `ast.Call` 会查
            # `self.fns`（"Stage A 不跨单元"那条），而 `raw` 原先**不查**、直接发名字 ——
            # 于是同一个"本单元没有的函数"，写在**值位**被拒、写在**语句位**照发。
            # 判决取决于"恰好写在哪个位置"不是设计，是**漏**：那道闸的用意是"跨单元调用
            # 要显式声明"，与写法位置无关。
            #
            # **复用 `ty_of`**，不在这里另抄一份 —— 两处各写一遍必然漂。
            self.ty_of(e)
            # 调用点跟着函数名的改名走（见 `fname`）。
            nm = self.fname.get(e.name, e.name)
            # **实参按形参类型转**（`#125`）。形参表不知道（`extern` / 跨单元）或
            # 个数对不上（变参）就不转 —— 宁可少转也别乱转。
            ps = self.fparams.get(e.name)
            if ps is not None and len(ps) == len(e.args) and self.d.usual_arith:
                args = [self.ex(a, "bool") if self.want_of(t) == "bool"
                        else self.conv(a, t if t in _INT_RANK else None)
                        for a, t in zip(e.args, ps)]
            else:
                args = [self.ex(a, "int") for a in e.args]
            return f"{nm}({', '.join(args)})"
        if isinstance(e, Un):
            if e.op == "!":
                # Loment 的 `!` 只收 bool，而 C 的 `!x` 收 int —— 直接写成 `x == 0`
                return f"({self.ex(e.e, 'int')} == 0)"
            return f"({e.op}{self.ex(e.e, 'int')})"
        if isinstance(e, Bin):
            op, want, _b = self.d.bin[e.op]
            if self.d.reject_const_zero_div and e.op in ("/", "%"):
                v = _int_const(e.r)
                if v == 0:
                    raise Unsupported(
                        f"第 {e.line} 行: 常量零做除数/模数。Java 的常量表达式在这里"
                        f"就是编译期错误；生成出来的源能编过，但运行时会 SIGFPE。")
            if op in _CMP_OPS:
                # **比较的操作数要钉住宽度**（见 `pinned`）：先按源语言的寻常算术转换
                # 算该在哪个宽度比，再**叶子式**地钉那张表。不定型才退回 `int_default`。
                pt = None
                if self.d.usual_arith:
                    pt = _usual_arith(self.num_ty(e.l), self.num_ty(e.r))
                else:
                    # Java/C#: the comparison happens at the wider of the two
                    # operand types (either side is long => long). Without this,
                    # two same-width longs were pinned to int_default (i32), and
                    # the comparison silently narrowed to 32 bits (#491).
                    lt, rt = self.num_ty(e.l), self.num_ty(e.r)
                    if lt and rt and lt != rt:
                        pt = "i64" if "i64" in (lt, rt) else self.d.int_default
                    else:
                        pt = lt or rt
                return f"({self.pinned(e.l, pt)} {op} {self.pinned(e.r, pt)})"
            if self.d.mask_shift and e.op in ("<<", ">>"):
                # Java masks the shift distance: int by 0x1f, long by 0x3f.
                # The mask is emitted in the left operand's width because this
                # language requires both operands of a binary operator to have
                # the same width (issue #316).
                lt = self.num_ty(e.l) or self.d.int_default
                right = self.ex(e.r, "int")
                if lt == "i64":
                    right = f"(({right} as i64) & 63)"
                else:
                    right = f"(({right}) & 31)"
                return f"({self.ex(e.l, 'int')} {op} {right})"
            if self.d.usual_arith and op in _ARITH_OPS:
                # **算术也要按提升后的宽度做**（`int` + `long` 在 C 里是 64 位加法）。
                pt = _usual_arith(self.num_ty(e.l), self.num_ty(e.r))
                return f"({self.conv(e.l, pt)} {op} {self.conv(e.r, pt)})"
            return f"({self.ex(e.l, want)} {op} {self.ex(e.r, want)})"
        raise AssertionError(type(e))

    # ---- 语句
    def stmts(self, body: list, depth: int) -> None:
        for s in body:
            self.stmt(s, depth)

    def stmt(self, s: object, depth: int) -> None:
        if isinstance(s, Decl):
            if s.ty == "()":
                raise Unsupported(f"第 {s.line} 行: 不能声明 `void` 变量")
            # **初值要在"这条声明生效之前"发射**（`#234`）。块里一条遮蔽外层的
            # `int x = x + 100;`：本语言没有块作用域，那条声明会被改名成 `x__1`；
            # 若先 `decl_bind` 再发初值，`x` 在右边也会被读成 `x__1` ——
            # `let x__1: i32 = (x__1 + 100);` 是一条**自引用**的声明，check 直接报
            # "使用未声明的变量 x__1"（用户从没写过这个名字）。
            #
            # C 里这里其实是**未定义行为**（新 `x` 的作用域从声明符之后就开始了，
            # 初值读到的是它自己的不确定值）。本语言表达不了"读一个正在声明的名字"，
            # 而外层恰好有同名时读外层是**确定且保义**的选择（`#234` 的期望），
            # 所以初值在旧绑定下发射；外层没有那个名字就照旧响亮报"用了没声明过的"。
            init = None if s.init is None else self.sink(s.init, s.ty)
            nm = self.decl_bind(s.name, s.line)
            self.varty[s.name] = s.ty
            if init is None:
                # 见文件头 §语义选择 1：C 的未初始化在这里变成确定的零值
                self.out(f"let {nm}: {s.ty} = 0;", depth)
            else:
                self.out(f"let {nm}: {s.ty} = {init};", depth)
            return
        if isinstance(s, Assign):
            if s.name not in self.vars:
                raise Unsupported(f"第 {s.line} 行: 赋值给没声明过的 `{s.name}`")
            self.out(f"{self.vars[s.name]} = "
                     f"{self.sink(s.e, self.varty.get(s.name, 'int'))};", depth)
            return
        if isinstance(s, Return):
            if s.e is None:
                # **点名拒**：本语言的 `()` 函数**没有提前退出**这回事（`return;` 不是
                # 合法构造 —— 参考实现报 `E19 期望表达式，得到 ';'`）。原先照发 `return;`，
                # 于是**产出一份编不过的源**，而报出来的错指向生成出来的那一份。
                # 与 `~` / `>>>` 同一条纪律：**能表达的就转，表达不出来的就报错**。
                raise Unsupported(
                    f"第 {s.line} 行: 不支持 `return;`（不带值那种）。本语言的 `()` 函数"
                    f"**没有提前退出**——它跑到末尾就结束。把它改写成 `if`/`else` 让末尾成为"
                    f"唯一出口，或者把函数改成有返回值并用 `return 0;`")
            else:
                # **要的是这个函数声明的返回类型，不是写死的 `int`。**
                # `bool positive(int x) { return x > 0; }` 里那个 `x > 0` 本来就是
                # bool，写死 `int` 会把它补成 `(x > 0) as i32` —— 而函数声明的是
                # `-> bool`，那是本语言的类型错（实测报"return 类型 i32，函数声明 bool"）。
                #
                # 只有**翻 `bool` 返回的函数**才撞得到这一处，而 C 那门根本没有 bool
                # （Java/C# 的语料里也没有返回 bool 的函数），所以它一直没被逼出来 ——
                # C++ 一上来就撞到了。
                self.out(f"return {self.sink(s.e, self.ret)};", depth)
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
            self.out(f"while {self.ex(s.cond, 'bool')} {{", depth)
            self.block(s.body, depth + 1)
            self.out("}", depth)
            return
        if isinstance(s, For):
            self.for_(s, depth)
            return
        if isinstance(s, ExprStmt):
            if not isinstance(s.e, Call):
                raise Unsupported(f"第 {s.line} 行: 表达式语句只收函数调用")
            self.out(f"{self.raw(s.e)};", depth)
            return
        raise AssertionError(type(s))

    def for_(self, s: For, depth: int) -> None:
        """`for (init; cond; step) body` -> `init; while cond { body; step; }`。

        **Loment 没有裸块**，所以 `for (int i = …)` 里那个 `i` 不能靠一层 `{ }` 圈住 ——
        只能改名外提（见文件头 §语义选择 2）。改名是安全的：C 的 for 作用域本来就在
        循环结束处结束，外面看不到它。

        初值是**声明**时改名；初值是**赋值**（`for (i = 3; …)`）时原地发出，名字不动 ——
        那种写法用的是外面的变量，本来就该影响外面。
        """
        self.gap()
        saved: tuple[str, str | None, str | None] | None = None
        if isinstance(s.init, Decl):
            if s.init.ty == "()":
                raise Unsupported(f"第 {s.init.line} 行: 不能声明 `void` 变量")
            old = self.vars.get(s.init.name)
            oldty = self.varty.get(s.init.name)
            # 与 `stmt(Decl)` 同一处（`#234`）：初值在循环变量**生效之前**发射。
            # `for (int x = x + 1; …)` 里右边那个 `x` 是外层的那个（本语言表达不了
            # 自引用，而外层有同名时读外层是确定选择），改名之后才不会读成 `x__1`。
            init = None if s.init.init is None else self.sink(s.init.init, s.init.ty)
            new = self.fresh(s.init.name)
            self.vars[s.init.name] = new
            # 循环变量的**类型**也要跟着这个作用域走（见 `self.varty`）——
            # 收在 `saved` 里，循环结束处一起还回去。
            self.varty[s.init.name] = s.init.ty
            if init is None:
                self.out(f"let {new}: {s.init.ty} = 0;", depth)
            else:
                self.out(f"let {new}: {s.init.ty} = {init};", depth)
            saved = (s.init.name, old, oldty)
        elif s.init is not None:
            self.stmt(s.init, depth)

        cond = self.ex(s.cond, "bool") if s.cond is not None else "true"
        self.out(f"while {cond} {{", depth)
        # 体是**独立的一层作用域**（`#119`：体里 `int i = 9;` 遮蔽循环变量时，
        # 步进必须还写到循环变量那个名上，而不是被遮蔽的那个）。
        self.block(s.body, depth + 1)
        if s.step is not None:
            self.stmt(s.step, depth + 1)  # 步进在体**之后**
        self.out("}", depth)

        # 循环结束：那个名字的作用域也结束（C 的 for 语义）。有遮蔽就把外层的还回去。
        if isinstance(s.init, Decl) and saved is not None:
            name, old, oldty = saved
            if old is None:
                del self.vars[name]
                self.varty.pop(name, None)
            else:
                self.vars[name] = old
                self.varty[name] = oldty      # type: ignore[assignment]

    def emit_fn(self, f: Fn) -> str:
        self.vars = {}
        self.scopes = []
        for (_t, n, _l) in f.params:
            self.vars[n] = self.bind(n)
        self.varty = {n: t for (t, n, _l) in f.params}
        self.ret = f.ret
        self.n = 0
        self.lines = []
        ps = ", ".join(f"{self.vars[n]}: {t}" for (t, n, _l) in f.params)
        head = f"pub fn {self.fname.get(f.name, f.name)}({ps})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        self.lines.append(head + " {")
        self.stmts(f.body, 0)
        self.lines.append("}")
        return "\n".join(self.lines)

    #: `for` 的初值**与**循环里用了同一个名字时的遮蔽，靠 `vars` 的存/取还原 —
    #: 见 `for_` 结尾（不需要额外的栈）。


def parse(src: str, d: "Dialect") -> list:
    return Parser(tokens(_pre(src, d)), d).unit()


def _pre(src: str, d: "Dialect") -> str:
    """这一族的源码预处理（见 `Dialect.pre`）。没给就是原样。"""
    return d.pre(src) if d.pre else src


def translate(src: str, d: "Dialect", keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """C 源码 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    * `keep` 给了就只翻这些函数 —— 一份 C 里可能既有"要翻译成 Loment"的函数，
      也有"留着外部链接（`pub extern fn`）"的函数。
    * `externs` 是**不在这份源码里、但调用点要认的**函数：`名字 -> Loment 返回类型`。
      没有它的话，被翻译的代码一调到 `pub extern fn` 那种函数就会报"本单元没有"。
    """
    fns = Parser(tokens(_pre(src, d)), d).unit()
    rets = {f.name: f.ret for f in fns}
    if externs:
        rets.update(externs)
    seen: set[str] = set()
    for f in fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.line} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)
    # 函数名 -> 发出去的名字。**整份单元算一次**：函数名与调用点必须一起改，
    # 而且改完要**两两不同**（`safe()` 只管保留字，不管两个名字会不会撞到一起）。
    fname: dict[str, str] = {}
    taken: set[str] = set()
    for f in fns:
        nm = d.safe(f.name)
        while nm in taken:
            nm += "_"
        taken.add(nm)
        fname[f.name] = nm
    out = []
    # 函数名 -> 形参的 Loment 类型表。**实参转换要用它**（`#125`）—— 与 `rets` 一样
    # 整份单元算一次。`extern` 那种不在表里的照旧"不转"。
    pars = {f.name: [t for (t, _n, _l) in f.params] for f in fns}
    for f in fns:
        if keep is not None and f.name not in keep:
            continue
        out.append(Emitter(rets, d, consts, fname, pars).emit_fn(f))
    return "\n\n".join(out) + "\n"


def main(d: "Dialect", src_lang: str, argv: list[str] | None = None) -> int:
    """`path -> Loment` 的命令行。**各门复用这一个** —— 别各自抄一遍。

    `src_lang` 只用来写帮助与报错里的"这门语言叫什么"。
    """
    ap = argparse.ArgumentParser(prog=f"{d.name}trans",
                                 description=f"{src_lang} 写法 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"), d)
    except Unsupported as e:
        print(f"[ERR] 子集外: {e}", file=sys.stderr)
        return 1
    except CError as e:
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


