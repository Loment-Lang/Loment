#!/usr/bin/env python3
"""jtrans.py — **Java 写法 -> Loment**（`docs/188` §7.1 六门里的第三门）。

     Java 写法 --jtrans--> Loment

机器在 `tools/trans_core.py`（花括号族的共享核），本模块给它一张 **Java 的方言表**
外加一处**这一族里只有 Java（和 C#）才有的预处理**：把 `class` 外壳抹掉。

## 这一门**比 C 那门简单**，因为语义本来就贴着

`docs/186` §5 说 C 那门最要紧的是"`int` 与 `bool` 不是一回事"。**Java 里那处缝不存在**：

| | C | Java |
|---|---|---|
| `&&` `\|\|` `!` 出什么 | `int`（0/1） | **`boolean`** |
| `if (x)` 收什么 | 标量即可 | **只收 `boolean`** |
| `int x = (a < b);` | 合法 | **类型错** |
| `int` 的宽度 | 平台相关 | **恒为 32 位** ✓ 与 `i32` 一致 |
| `/` 与 `%` | 向零截断 | **向零截断** ✓ 一致 |
| `>>` | 实现定义（x86 上是算术） | **算术** ✓ 与 Loment 一致（实测 `-8 >> 1 == -4`） |

⇒ **这门一个方向的强制转换都不用补**（`coerce_int_to_bool=False`、
`coerce_bool_to_int=False`）。方言表里那两个开关正好是判据"能表达的就转、
表达不出来的就报错"的另一半：Java 的 `int x = (a < b);` 在 Java 里**本来就编不过**，
所以遇到它报错是对的，不该悄悄补一个 `as i32`。

## Java 独有的两处

### ① `class` 外壳要抹掉（`_unwrap`）

Java 的函数住在 `class X { … }` 里，而共享 parser 看的是**顶层**的
`<类型> <名>(…) { … }`。所以先把外壳抹掉：把 `class X … {` 那段头**换成等长空白**
（保留换行），配对的 `}` 也换成空白。

**为什么是"等长空白"而不是"抽出来拼一拼"**：行号与偏移**不变**，于是报错里的
行号就是源里的行号。（同 `potato_from._blank_keep_off` 那个手法。）

**实现搬去了 `trans_core.strip_shells`** —— C# 那一门要抹 `namespace` + `class`
**两层**，同一个手法不该抄两遍。搬过去时顺手补了一处这里原来有的洞：原先只把
**注释**抹成空白，于是 `String s = "class X {";` 会被当成一个 `class` 头，
外壳从字符串**里面**开始切。现在字符串字面量也一起抹。

**`interface` / `enum` / 嵌套类一律拒** —— 它们不是"外壳"，是另一回事。

### ② `>>>` 是**逻辑**右移，本语言的 `>>` 是**算术**的

所以 `>>>` 不能直接映过去。`trans_core` 里把它**点名拒掉**，理由写在报错里
（要它得写 `(a as u32) >> n`）。**先不收** —— 那是个能表达出来的差，但还没有语料逼它。

## 子集

Java 的类型：`int`→`i32`、`long`→`i64`、`short`→`i16`、`byte`→`i8`、`char`→`u16`、
`boolean`→`bool`、`void`→`()`。**`float` / `double` / `String` 拒**（本语言没有浮点与字符串值）。

修饰词：`public` / `private` / `protected` / `static` / `final` / `synchronized` /
`abstract` **收下并丢掉**（这里翻的是整个单元的全部函数，可见性在这个语境里没有
可分辨的差别）；`native` / `transient` / `volatile` / `strictfp` / `package` / `import`
**拒** —— 它们都真的改语义或本子集表示不了。

用法:

    python tools/jtrans.py SRC.java [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trans_core import (  # noqa: E402
    CError, Dialect, Unsupported, _BIN, _KW, _PRECOF, main as _main,
    strip_shells, translate as _translate,
)

# ---------------------------------------------------------------- class 外壳

#: **Java 里要抹掉的那层壳**：`class X { … }`。
#: 尾巴那个 `{` 也吃进正则（`[^{;]*\{`），于是 `{` 的下标就是 `m.end() - 1` ——
#: 顺带把"只有头没有体"的写法排除掉。
_CSHELL_HEAD = re.compile(r"(?<![\w.])"
                          r"(?:public\s+|final\s+|abstract\s+|strictfp\s+)*"
                          r"class\s+[A-Za-z_]\w*[^{;]*\{")

#: `(名字, 正则, 透明, 有体)` —— 四格的意思见 `trans_core.strip_shells`。
#: **不透明**：`class` 里的嵌套类是另一回事，不进去抹，留给方言表的 `agg` 去拒。
_JAVA_SHELLS = [("class", _CSHELL_HEAD, False, True)]


def _mask(src: str) -> str:
    """Blank comments and string/char literals, preserving newlines and length."""
    out = list(src)
    i = 0
    while i < len(src):
        c = src[i]
        if c == "/" and i + 1 < len(src) and src[i + 1] == "/":
            j = src.find("\n", i)
            if j < 0:
                j = len(src)
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < len(src) and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = len(src) if j < 0 else j + 2
            for k in range(i, j):
                if src[k] != "\n":
                    out[k] = " "
            i = j
        elif c in ("'", '"'):
            quote = c
            j = i + 1
            while j < len(src):
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, len(src))):
                if src[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


#: Constructs that are **outside the subset**. Name them before the shared parser
#: sees them: otherwise `this.x` becomes "unrecognized '.'", `throws` becomes
#: "expected '{'", and so on (issues #831/#832/#833/#834/#838/#839/#840/
#: #842/#843/#845/#846/#847/#848/#851/#853/#855).
_PRE = [
    (r"\bsynchronized\b", "`synchronized`"),
    (r"\b(?:this|super)\s*\.", "`this` / `super` 的字段访问"),
    (r"\.class\b", "`X.class` 类字面量"),
    (r"\binstanceof\b", "`instanceof`"),
    (r"\bthrows\b", "`throws` 子句"),
    (r"->", "lambda（`->`）"),
    (r"@[A-Za-z_]", "注解"),
    (r"\bstatic\s*\{", "静态初始化块"),
    (r"\b(?:enum|interface|record|sealed|permits)\b", "`enum` / `interface` / `record` / `sealed`"),
    (r"(?<![\w.])[A-Z]\w*[ \t]*\([^;{]*\)[ \t]*\{", "构造器"),
    (r"\bnull\b", "`null` 字面量"),
    (r"\b(?:float|double)\b", "`float` / `double`"),
    (r"\d+\.\d+(?:[eE][+-]?\d+)?", "浮点字面量"),
]


def _precheck(src: str) -> None:
    if '"""' in src:
        line = src[:src.index('"""')].count("\n") + 1
        raise Unsupported(
            f"第 {line} 行: 不支持文本块（`\"\"\"`）。本子集没有多行字符串值；"
            f"整条构造原先会被拆成看不懂的 token 错误。")
    masked = _mask(src)
    for pat, what in _PRE:
        m = re.search(pat, masked)
        if m:
            line = masked[:m.start()].count("\n") + 1
            raise Unsupported(
                f"第 {line} 行: 不支持{what}（Stage A 的 Java 子集没有对应语义；"
                f"整条构造原先会被拆成看不懂的 token 错误）。")


def _unwrap(src: str) -> str:
    """抹掉 `class X … { … }` 的**外壳**，成员就成了顶层。**不动行号。**

    只抹**最外层**那一个 —— 嵌套类是另一回事，留给方言表拒。`interface` / `enum`
    也不在这里处理：它们由 `agg` 拒。

    实现搬去了 `trans_core.strip_shells`：C# 那一门要抹 `namespace` + `class` **两层**，
    同一个手法不该抄两遍 —— 而搬过去的时候顺手补了一处 `jtrans` 原来有的洞：原来只把
    **注释**抹成空白，于是 `String s = "class X {";` 会被当成一个 `class` 头，
    外壳从字符串**里面**开始切。现在字符串字面量也一起抹。
    """
    _precheck(src)
    # A `class` header that carries `extends` / `implements` was silently
    # discarded by the shell stripper (#822). The subset has no inheritance
    # model, so say that instead of translating a different program.
    for m in _CSHELL_HEAD.finditer(src):
        head = m.group(0)
        if re.search(r"\b(?:extends|implements)\b", head):
            line = src[:m.start()].count("\n") + 1
            raise Unsupported(
                f"第 {line} 行: 不支持 `extends` / `implements`（子集没有继承与接口模型；"
                f"整条子句原先会被静默丢掉，翻译出来的是**另一个程序**）。")
    # `package` / `import` change the meaning of a name; silently skipping them
    # would translate a different program (#859 / #860).
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if s.startswith("import ") or s.startswith("package "):
            raise Unsupported(
                f"第 {i} 行: 不支持 `import` / `package`（平名字空间里没有包与导入；"
                f"跳过它们会把名字解析成别的东西）。")
    return strip_shells(src, _JAVA_SHELLS)


#: Java 的方言表。
JAVA = Dialect(
    name="java",
    types={
        "int": "i32",
        "long": "i64",
        "short": "i16",
        "byte": "i8",
        # Java 的 `char` 是 **16 位无符号**整数（不是一个字符串类型）—— 所以它映 `u16`
        "char": "u16",
        "boolean": "bool",
        "void": "()",
    },
    type_words=frozenset({"int", "long", "short", "byte", "char", "boolean", "void"}),
    spec_words=frozenset({
        "int", "long", "short", "byte", "char", "boolean", "void",
        # 收下并丢掉的修饰词
        "public", "private", "protected", "static", "final", "synchronized", "abstract",
        # 出现就拒的
        "native", "transient", "volatile", "strictfp", "package", "import",
        # 本子集表示不了的类型 —— **认得它们才报得出"这一族的哪个类型不支持"**
        "float", "double", "String", "Object", "var",
    }),
    linkage=frozenset({"public", "private", "protected", "static", "final",
                       "synchronized", "abstract"}),
    bad_spec=frozenset({"native", "transient", "volatile", "strictfp",
                        "package", "import"}),
    #: `enum` / `interface` / 残留的 `class` —— 它们不是"外壳"，是另一回事。
    agg=frozenset({"enum", "interface", "record", "class", "extends", "implements"}),
    #: `_KW` 那批 + Java 自己的语句关键字。不点出来的话 `new Foo()` / `try { }`
    #: 会被当成标识符一路走到"期望 `;`"那种指不到点子的错。
    kw=frozenset(_KW | {"new", "this", "super", "throw", "throws", "try", "catch",
                        "finally", "instanceof", "assert", "yield", "var",
                        "package", "import", "extends", "implements", "synchronized"}),
    bin=dict(_BIN),
    precof=dict(_PRECOF),
    # **这一门两个方向都不补** —— Java 的 `&&` 出 `boolean`、条件只收 `boolean`，
    # 而 `int x = (a < b);` 在 Java 里本来就是类型错。（见文件头那张表。）
    coerce_int_to_bool=False,
    coerce_bool_to_int=False,
    int_default="i32",
    narrow_literal_check=True,
    reject_const_zero_div=True,
    mask_shift=True,
    hex_wrap=True,
    octal_literals=True,
    binary_literals=True,
    digit_separators=True,
    safe_suffix="_j",
    #: Java 的函数住在 `class` 里，而解析器看的是顶层 —— 见文件头 §①。
    pre=_unwrap,
    #: **`static final <类型> NAME = <整数>;` 是顶层常量** —— `potato_from._JAVA_CONST`
    #: 已经把它收进 `consts`，`lomt_from` 会发成 `pub const`。所以翻译器跳过那条
    #: 声明，只要认得那个名字。**标志词是 `final`**（Java 的常量必须写它）。
    const_words=frozenset({"final"}),
)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """Java 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。"""
    return _translate(src, JAVA, keep=keep, externs=externs, consts=consts)


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    from trans_core import parse as _parse
    return _parse(src, JAVA)


def main(argv: list[str] | None = None) -> int:
    return _main(JAVA, "Java", argv)


if __name__ == "__main__":
    sys.exit(main())
