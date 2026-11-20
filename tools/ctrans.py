#!/usr/bin/env python3
"""ctrans.py — **C 写法 -> Loment**（`docs/186` Stage A）。

     C 写法 --ctrans--> Loment

## 这一份现在很薄 —— 机器在 `trans_core.py`

`docs/188` §7.1 定的架构：**一份解析器 + 方言表**。「花括号 + 分号」那一族
（**C / C++ / Java / C#**）形状相同，差异全进 `Dialect`；解析器、语法树、发射器
都在 `tools/trans_core.py` 里，一门一份。**本模块只提供 C 的那张表。**

## C 这张表里唯一"有想法"的一格：两个方向的强制转换

C 的 `&&` `||` `!` 与比较**出的是 `int`**（0/1），而任何整数都能当条件用（`if (x)`）；
Loment 的 `&&`/`||` **出 `bool`**、比较**出 `bool`**，条件**只收 `bool`**。
所以**两个方向都要补**：

| 场合 | C | Loment |
|---|---|---|
| `int x = a && b;` | 直接存 | `let x: i32 = ((a != 0) && (b != 0)) as i32;` |
| `if (x)`（x 是 int） | 合法 | `if x != 0 {` |
| `!x`（x 是 int） | 按 int 取反 | `x == 0` |

**同一处，Java / C# 的结论相反**（它们的 `&&` 出 `boolean`、条件只收 `boolean`，
一个方向都不用补）—— 而判据是同一条：**能表达的就转，表达不出来的就报错**。
C 的 `&&` 出 `int` 而 `(bool) as i32` 能表达 ⇒ 转。

## 三条**不是翻译、是决定**的东西（理由见 `trans_core.py` 文件头）

1. `int x;`（只声明不给值）补**零值** —— C 读未初始化变量是未定义行为。
2. `for (int i = …)` 的循环变量**改名外提** —— Loment 没有裸块。
3. `static` / `inline` **收下并丢掉** —— 这里翻的是整个单元的全部函数，
   内部链接在这个语境里没有可分辨的差别。其余说明符（`const` `extern`
   `volatile` `register` `typedef`）**拒绝**，它们都真的改语义。

用法:

    python tools/ctrans.py SRC.c [--out OUT.lomt]
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

#: **C 的方言表**。C++ / Java / C# 各在自己的模块里给出自己那张。
C = Dialect(
    name="c",
    types={
        "int": "i32",
        "unsigned": "u32", "unsigned int": "u32",
        "signed": "i32", "signed int": "i32",
        "long": "i64", "long int": "i64",
        "unsigned long": "u64", "unsigned long int": "u64",
        "void": "()",
    },
    type_words=frozenset({"int", "unsigned", "signed", "long", "void"}),
    spec_words=frozenset({"int", "unsigned", "signed", "long", "void",
                          "static", "const", "extern", "register", "volatile",
                          "typedef", "struct", "enum", "union", "char", "short",
                          "float", "double", "inline", "_Bool",
                          # **`bool` 收进来只为那句报错**（`<stdbool.h>` 的宏，C23 才是关键字）。
                          # 它不在 `types` 里，所以报的是"类型 `bool` 不在 Stage A 子集里" ——
                          # 不收它的话 `bool b = 1;` 会掉进"`bool` 后面期望 `=` 或 `(`"，
                          # 一句指不到点子的话。**映不映它是另一件事**（Loment 有 `bool`，
                          # 理论上能映；那是独立的一条决定，见 docs/186 §9）。
                          "bool"}),
    #: **`const` 从"拒"挪到"收下并丢掉"**（2026-09-18，做 C++ 那一门时一并改）：
    #: 在**只有标量、没有指针**的子集里，`const int x` / `const int a`（形参）
    #: 丢掉 `const` 是**保义**的 —— 源侧本来就保证了它不会被改。原先那句
    #: "它们都真的改语义"对 `const` 不成立，`docs/186` §6.3 已更正。
    #: （**顶层** `const unsigned int MAX = 65535;` 仍然收不了 —— 那要
    #: `potato_from` 把它收进 `consts`，是另一件事，`docs/186` §9 留着。）
    linkage=frozenset({"static", "inline", "const"}),
    bad_spec=frozenset({"extern", "register", "volatile", "typedef"}),
    agg=frozenset({"struct", "enum", "union"}),
    kw=frozenset(_KW),
    bin=dict(_BIN),
    precof=dict(_PRECOF),
    # C：`&&` 出 `int`、`if (x)` 收标量 ⇒ **两个方向都要补**
    coerce_int_to_bool=True,
    coerce_bool_to_int=True,
    int_default="i32",
    # **C 的寻常算术转换**（`#123` / `#125` / `#133`）：`int`/`unsigned`/`long` 混算
    # 或互相赋值时，C 把窄的那侧提升到宽/无符号那侧。不插转换的话本语言按**自己的**
    # 规则算，结果是**静默错**（`int a = -1; unsigned b = 0; a > b` C 答真、这边答假）。
    # 这一门**能表达**那个提升（`as`），所以转。见 `Dialect.usual_arith`。
    usual_arith=True,
    safe_suffix="_c",
    # C 里 `true` / `false` **不是字面量**：那是 `<stdbool.h>` 的宏，而这一门不收
    # `#include` —— `int true = 5;` 是合法的普通变量。见 `Dialect.bool_literals`。
    bool_literals=False,
    # 函数内的 `static` 是**静态存储期**，丢掉会改程序的数 —— 见 `Dialect.local_storage`。
    local_storage=frozenset({"static"}),
)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """C 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    * `keep` 给了就只翻这些函数 —— 一份 C 里可能既有"要翻译成 Loment"的函数，
      也有"留着外部链接（`pub extern fn`）"的函数。
    * `externs` 是**不在这份源码里、但调用点要认的**函数：`名字 -> Loment 返回类型`。
    """
    return _translate(src, C, keep=keep, externs=externs, consts=consts)


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    from trans_core import parse as _parse
    return _parse(src, C)


def main(argv: list[str] | None = None) -> int:
    return _main(C, "C", argv)


if __name__ == "__main__":
    sys.exit(main())
