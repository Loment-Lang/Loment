#!/usr/bin/env python3
"""cpptrans.py — **C++ 写法 -> Loment**（`docs/188` §7.1 六门里的第五门）。

     C++ 写法 --cpptrans--> Loment

机器在 `tools/trans_core.py`（花括号族的共享核），本模块给它一张 **C++ 的方言表**。

## 这一门的结论与 C 一样，**理由却不同** —— 那正是它值得单独说的地方

`docs/186` §5 说 C 那门最要紧的是"`int` 与 `bool` 不是一回事"，所以两个方向的
强制转换都要补。C++ **不是**那个理由：

| | C | C++ |
|---|---|---|
| `a < b` 出什么 | `int`（0/1） | **`bool`**（C++ 有独立的布尔类型） |
| `int x = (a < b);` | 合法（本来就是 int） | **合法**（bool **隐式转** int） |
| `if (x)`（x 是 int） | 合法（标量即真） | **合法**（int **隐式转** bool） |
| `if (a < b)` | 合法（int 即标量） | 合法（本来就是 bool） |

⇒ **两个方向仍然都要补**，但原因是 C++ 把 C 那两处隐式转换**都留着**
（`bool ↔ int` 双向隐式）—— 而不是"它根本没有 bool"。

**这一格是"同一族里两门看起来一样、解释完全不同"的样本**，所以它在这里写清楚：
将来谁看到"C 与 C++ 都开了两个开关"就去把其中一个关掉，会当场弄坏一门。

对照：**Java / C# 是关的**，因为那两门 `int x = (a < b);` **本来就编不过**。

## `true` / `false` 在共享核里

它们**不是** `1` / `0`：`bool t = true;` 发成 `let t: bool = 1;` 是本语言的类型错。
所以 `trans_core.Lit` 带一个 `b` 位（源里是布尔字面量），`Emitter.ty_of` / `raw`
照它走。这一改动**四门都受益**（Java 的 `boolean t = true;` 原先报的是
"用了没声明过的 `true`" —— 一句指不到点子的话）。

## 与 C 相同的那几处

* **预处理指令（`#include` / `#define`）不收** —— `#define` 真的改语义，而这一层
  没有可分辨的作用。所以语料不带 include。
* **顶层 `const` 全局量**收不了（要 `potato_from` 把它收进 `consts`，
  `docs/186` §9 那一条）。**但 `const` 局部量与 `const` 形参收** —— 在只有标量、
  没有指针的子集里丢掉 `const` 是保义的（2026-09-18 与 C 一起改过来）。
* **属性/成员访问（`std::cout` / `x.f`）不收**。`::` 连分词都过不去，而 `.` 是
  `primary()` 点名拒的（消息里就点了 C++ 的 `ns::f`）。

## 子集

`int`→`i32`、`unsigned [int]`→`u32`、`short [int]`→`i16`、`unsigned short`→`u16`、
`long`/`long long`→`i64`、对应的 `unsigned`→`u64`、`bool`→`bool`、`void`→`()`。
**`char`（有符号性由实现决定）、`float` / `double`、`std::` 那一套全拒。**

用法:

    python tools/cpptrans.py SRC.cpp [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trans_core import (  # noqa: E402
    CError, Dialect, Unsupported, _BIN, _KW, _PRECOF, main as _main,
    translate as _translate,
)

#: **C++ 的方言表**。
CPP = Dialect(
    name="cpp",
    types={
        "int": "i32",
        "unsigned": "u32", "unsigned int": "u32",
        "signed": "i32", "signed int": "i32",
        "short": "i16", "short int": "i16",
        "unsigned short": "u16", "unsigned short int": "u16",
        "long": "i64", "long int": "i64",
        "unsigned long": "u64", "unsigned long int": "u64",
        "long long": "i64", "long long int": "i64",
        "unsigned long long": "u64", "unsigned long long int": "u64",
        # **带明确符号的 `char` 收** —— 只有**光秃秃的 `char`** 是实现定义的（见 `spec_words`）。
        # 这两个是标准写明的：`signed char` 至少 -127..127、`unsigned char` 0..255。
        "signed char": "i8", "unsigned char": "u8",
        # C++ 有**独立的布尔类型**（不像 C 那样得靠 `<stdbool.h>`）——
        # 这是这一门与 C 最要紧的一处差别，见文件头那张表。
        "bool": "bool",
        "void": "()",
    },
    type_words=frozenset({
        "int", "unsigned", "signed", "short", "long", "bool", "void",
    }),
    spec_words=frozenset({
        "int", "unsigned", "signed", "short", "long", "bool", "void",
        # 收下并丢掉的修饰词
        "static", "inline", "const",
        # 出现就拒的
        "extern", "register", "volatile", "typedef", "constexpr", "mutable",
        "thread_local", "explicit", "friend", "virtual", "override", "final",
        # 本子集表示不了的类型 —— **认得它们才报得出"这一族的哪个类型不支持"**。
        # `char` 也在这一列：它的有符号性**由实现决定**（x86-64 上 g++ 是 signed，
        # ARM 上常常不是），映成 i8 或 u8 都会在某台机器上悄悄算错。
        # **只列单词** —— `spec()` 是逐词收的，`signed char` / `long double`
        # 这种多词组合由 `signed`+`char` / `long`+`double` 各自命中后自然拼出来，
        # 拼出来的整串不在 `types` 里，于是报的是"类型 `signed char` 不在子集里"。
        "char", "wchar_t", "char8_t", "char16_t", "char32_t",
        "float", "double", "size_t", "ssize_t",
        "int8_t", "int16_t", "int32_t", "int64_t", "uint8_t", "uint16_t",
        "uint32_t", "uint64_t", "ptrdiff_t", "auto",
        # 聚合/枚举/模板 —— 也放进来，这样报的是**具体**那句（见 `agg`）
        "class", "struct", "union", "enum", "namespace", "template", "typename",
    }),
    #: 见 `ctrans.py` 里同一条注解：标量子集里丢掉 `const` 是**保义**的。
    linkage=frozenset({"static", "inline", "const"}),
    bad_spec=frozenset({"extern", "register", "volatile", "typedef", "constexpr",
                        "mutable", "thread_local", "explicit", "friend", "virtual",
                        "override", "final"}),
    agg=frozenset({"class", "struct", "union", "enum", "namespace", "template",
                   "typename"}),
    #: `new` / `delete` / `this` / `nullptr` 这些在表达式位置出现必然是子集外，
    #: 早点名比"期望 `;`"强。**`true` / `false` 不在这里** —— 它们是字面量，
    #: 由共享解析器直接映过去（见文件头）。
    kw=frozenset(_KW | {"new", "delete", "this", "nullptr", "using", "namespace",
                        "template", "typename", "public", "private", "protected",
                        "virtual", "override", "final", "friend", "operator",
                        "try", "catch", "throw", "auto", "explicit", "mutable",
                        "constexpr", "thread_local", "noexcept", "typeid",
                        "static_cast", "dynamic_cast", "reinterpret_cast",
                        "const_cast", "class", "struct", "union", "enum"}),
    bin=dict(_BIN),
    precof=dict(_PRECOF),
    # **两个方向都补** —— 与 C 同一个结论、**不同的理由**（C++ 有真 bool，
    # 但 bool 与 int 双向隐式转换都留着）。见文件头那张表，别照 C 的注解改。
    coerce_int_to_bool=True,
    coerce_bool_to_int=True,
    int_default="i32",
    # C++ 的整型转换规则与 C 同源（**寻常算术转换**）—— 见 `Dialect.usual_arith`
    # 与 `tools/ctrans.py` 那一处（`#123` / `#125` / `#133`）。
    usual_arith=True,
    safe_suffix="_cpp",
    #: 顶层常量**不收**（与 C 一致）：`potato_from` 那边也没有 C++ 的常量收集。
    const_words=frozenset(),
    #: 函数内的 `static` 是**静态存储期**，丢掉会改程序的数 —— 见 `Dialect.local_storage`。
    #: 与 C 同一条。
    local_storage=frozenset({"static"}),
)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """C++ 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。"""
    return _translate(src, CPP, keep=keep, externs=externs, consts=consts)


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    from trans_core import parse as _parse
    return _parse(src, CPP)


def main(argv: list[str] | None = None) -> int:
    return _main(CPP, "C++", argv)


if __name__ == "__main__":
    sys.exit(main())
