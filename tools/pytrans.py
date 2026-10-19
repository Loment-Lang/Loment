#!/usr/bin/env python3
"""pytrans.py — **Python 写法 -> Loment** 的前端（`docs/187`；`docs/188` 之后的方向见下）。

    Python 写法的源码 --pytrans--> Loment 源码

## ⚠️ 这一份里还有一条**已上报未修**的缺陷（`docs/198`）

写 LumtUI 那条线（`docs/196`）时撞到的，带最小复现，**动这一份之前先看那一条**：

* **内建"当值用"与"当语句用"给两个答案**（`docs/198` §3）—— `ty_of()` 查 `self.fns`
  那道"Stage A 不跨单元"的闸，`raw()` 不查。于是一道闸按调用写在哪个位置给出两种判决。

（同一批上报里的 `~` 那条**已经修了**：现在点名拒，与 `**` / `/` 同一条纪律 ——
见下面「子集」那节与 `loment/pytrans/unary_invert.py`。）

## 它是**前端**，不是"翻译外源代码" —— 2026-09-18 改定的

第一版（`docs/187` 初稿）把这里当成**语义搬运**：Python 的 `//` 是向下取整、Loment 的
是向零截断，于是发一对辅助函数去"保住 Python 的语义"。**方向反了。**

用户 2026-09-18 定的模型（`docs/188` §0）：**Loment 可以被多种语法编写，最终行为无异**。
一份用 Python 写法写的单元**是一个 Loment 程序** —— 拼法是 Python 的，**语义是 Loment 的**。

> **表层语法只决定拼法与形状，不决定语义。**

所以这一份现在做的事，与 C 编译器把 C 读成汇编**同一个性质**：把一种拼法读成本语言。
下面每一条"改法"都按这条判。

## 一条判据管住全部：**能表达的就转，表达不出来的就报错**

"不迁就 Python"不是"一律拒绝"，因为有些差异**只是拼法**：

| 场合 | Python | Loment | 怎么办 |
|---|---|---|---|
| `if x:`（x 是 int） | 合法（真值） | 条件只收 `bool` | **转**成 `x != 0` —— 这是"整数当条件"的两种拼法 |
| `x = a and b` | `and` 返回**操作数**（`1 and 2 == 2`） | `&&` 出 `bool` | **报错** —— 这个意思 **Loment 表达不出来** |
| `1 + (a < b)` | `True` 就是 `1` | `i64 + bool` 是类型错 | **报错** —— 同上，Python 的 bool **就是 int** |
| `-7 // 2` | `-4`（向下取整） | `-3`（向零截断，实测） | **照 Loment 的来** —— `/` 是"整除"的拼法，不是"向下取整"的拼法 |

前两条合起来是同一件事：**Python 的 `bool` 是 `int` 的子类**，而 Loment 的 `bool`
是独立类型。所以 `bool -> int` 一律**报错**；`int -> bool`（条件位置）**转**。

对比 C 那一门（`docs/186`）：C 的 `&&` 出 `int`，而 `(bool) as i32` **能表达**那个意思，
所以 C 那门要补转换。**同一处，两门结论相反，判据是同一条。**

## 与第一版的另外两处不同

* **不再上提声明。** 第一版把"最外层赋值发生在嵌套块里"的名字提到函数头 ——
  理由是"Python 函数级、Loment 块级"。**那条理由是我写错的，而且探一下就露馅**
  （见下）。所以现在就地 `let`。
* **`for` 用一个明确的降级式**（见 §`for`），不再用计次变量去迁就 Python 的终值。

### 一处**我断言过、探过之后不成立**的差别（留着当教训）

`docs/187` 初稿与 `docs/188` §0.1 都把"**Python 是函数级作用域、Loment 的 `let`
是块级**"列成一处真差别。2026-09-18 一探，**它不存在**：

    fn f(c: bool) -> i32 { if c { let x: i32 = 10; } else { x = 20; } return x; }

* `lomentc.check` **不报错** —— 块里 `let` 的名，块外看得见；
* 编出来跑 `f(false)` 给 **20** —— 只有一条分支声明、另一条只赋值，值也对。

⇒ Loment 的 `let` 在这两个形状里就是能漏到块外的，与 Python 在这一处**本来就一致**。

所以第一版那个"上提"**不是方向反了，是多余**；现在就地 `let` 的结果两边本来就一样
（`loment/pytrans/blockscope.py` 是这一条的**同意集**语料，拿 CPython 当对照）。

**教训**：一处"语义差"是**探出来的**，不是**推出来的** —— 推出来的那个让我写了一版
多余的实现，还往文档里记了一条不存在的事实。

## `for` 的定义（写下来，因为它不是搬运而是定义）

    for i in range(a, b):        ->   let i: i64 = a;
        <body>                        while i < b {
                                          <body>
                                          i = i + 1;
                                      }

**这就是 `for i in range(…)` 在本语言里的意思。** 两个后果都写在这儿：

* 循环**结束后 `i` 是 `b`**，与 CPython 的 `b - 1` **不同**（CPython 的 for 目标停在
  最后一个取到的值）。这是那条降级式的直接结果，不是疏漏。
* **循环体里给 `i` 赋值 → 报错。** 那条降级式在那种写法下不成立（CPython 每轮都会
  把 `i` 重置成下一个值，而 `while` 不会）。与其悄悄给出不同的数，不如让人写 `while`。

只收 `range(n)` 与 `range(a, b)`：**带步进的 `range` 一律拒绝** —— 负步进与空区间的
边界都要单独想清楚。

## 子集

    模块级    NAME = <整数>            （常量, 全大写才算 —— 与 potato_from 同一条判据）
              def f(a: int, b: int) -> int: …
    语句      赋值/注解赋值/增量赋值   if/elif/else   while   for i in range(…)   return   f(…)
    表达式    整数  名字  调用  一元 - + not   二元 算术/位/比较/`//`/`%`   布尔 and/or

> **`~` 不在**（`docs/198` §1）：本语言的一元运算符只有 `-` 与 `!`，而 `~v` 是
> "与全 1 异或" —— **按宽度**分别写作 `v ^ -1`（i32）或 `v ^ 255`（u8）。
> 这台翻译器**不做整数宽度跟踪**，所以它替不了你选，只能点名拒。
> 与 `>>>`（逻辑右移）同一条：**先不收，除非有语料逼它**。

**不收的一律报错**（`docs/167`）：`break` / `continue` / `lambda` / 推导式 / `try` /
`with` / 导入 / 元组解包 / 链式比较 / `**` / `/`（真除，出浮点）/ `~`（按位取反，
按宽度写 `^ -1`）/ `str`·`list`·`dict` /
`for … else` / `while … else`。理由与 C 那门一样：**跳过 = 产出一份少算一步却照样能编的单元**。

## 两条**决定**（不是翻译）

1. **`int` 映 `i64`**，与 `potato_from.PY_TYPES` 一致。Python 的 `int` 是任意精度、
   `i64` 有界 —— 溢出行为的差别**没有处理**（溢出变成回绕）。
2. **局部变量的类型从它第一次赋值的右值推**（比较/布尔/`not` 出 `bool`，其余 `i64`）。
   Python 没有声明，所以必须有一条"谁来定"的规则；这条是可读且确定的。

用法:

    python tools/pytrans.py SRC.py [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Python 注解 -> Loment 类型。与 `potato_from.PY_TYPES` **同表**（`int` 映 `i64`）——
#: 两处对不上的话，`lomt_from` 发的签名与这里发出来的对不上，而那是**静默**的。
_TYPES = {"int": "i64", "bool": "bool", "None": "()"}

#: 二元运算符 -> Loment 运算符。
#: **`//` 与 `%` 都在**，且直接映本语言的 `/` 与 `%` —— 见文件头那条表：
#: `//` 是"整除"的**拼法**，而本语言的整除是向零截断的（实测 `-7 % 2 == -1`）。
#: `**` 不在（没有乘方）；`/` 不在（真除出浮点）。
_BIN = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
    ast.FloorDiv: "/", ast.Mod: "%",
    ast.LShift: "<<", ast.RShift: ">>", ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^",
}
_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">="}


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 静默跳过 = 产物少算一步却照样能编。"""


class PyError(Exception):
    """这份 Python 本身有问题（语法/名字）。与 `Unsupported` 分开。"""


# ---------------------------------------------------------------- 发射

#: Loment 保留字 —— 生成的标识符不能撞上。
_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


def _safe(name: str) -> str:
    return name + "_py" if name in _LOMENT_KW else name


def _ty_of_annotation(node, where: str) -> str:
    """参数/返回注解 -> Loment 类型。**没有注解就报错** —— Python 不写注解就没有
    类型可用，而 Loment 必须有；猜一个宽度是把作者的意思改掉。"""
    if node is None:
        raise Unsupported(f"{where}: 没有类型注解（Loment 必须有类型；"
                          f"写 `: int` 或 `-> int`）")
    txt = ast.unparse(node)
    if txt not in _TYPES:
        raise Unsupported(f"{where}: 注解 `{txt}` 不在子集里（只收 {sorted(_TYPES)}）")
    return _TYPES[txt]


class Emitter:
    """一个 `def` -> 一段 Loment 源码。"""

    def __init__(self, fns: dict[str, str], consts: dict[str, str] | None = None,
                 params: dict[str, list[str]] | None = None) -> None:
        #: 本单元所有函数的**返回类型**。调用点的类型靠它。
        self.fns = fns
        #: 本单元所有函数的**形参类型**（按声明顺序）。调用点（`raw()` 的 Call 分支）
        #: 靠它决定每个实参要什么类型 —— **只查返回类型是不够的**（`#218`）：
        #: 原先每个实参都硬写 `want="i64"`，于是一个 `bool` 形参永远没有调用点能对上，
        #: 而 `pick(True)` 报的还是"这里要的是整数，给的是布尔"（把话反过来说了）。
        self.params: dict[str, list[str]] = dict(params or {})
        #: 模块常量的名字 -> 类型。生成的 `pub const` 是模块级的，函数体里用得到。
        self.consts: dict[str, str] = dict(consts or {})
        #: 名字 -> 类型（`bool` / `i64`）。**推导出来的**，见文件头 §决定 2。
        self.vars: dict[str, str] = {}
        self.lines: list[str] = []
        self.depth = 0
        self.ret = "()"

    # ---- 行
    def out(self, s: str) -> None:
        self.lines.append("    " * (self.depth + 1) + s)

    def gap(self) -> None:
        """控制流前面留一个空行 —— 但别在开头留，也别连着留两个。"""
        if self.lines and self.lines[-1] != "" and not self.lines[-1].endswith("{"):
            self.lines.append("")

    # ---- 类型：只有 `i64` / `bool` / `()` 三档
    def ty_of(self, e) -> str:
        if isinstance(e, ast.Constant):
            if isinstance(e.value, bool):
                return "bool"
            if isinstance(e.value, int):
                return "i64"
            raise Unsupported(f"第 {e.lineno} 行: 不支持常量 {e.value!r}"
                              f"（只收整数与 True/False）")
        if isinstance(e, ast.Name):
            if e.id in self.consts:
                return self.consts[e.id]
            if e.id not in self.vars:
                raise PyError(f"第 {e.lineno} 行: 用了没赋过值的 `{e.id}`")
            return self.vars[e.id]
        if isinstance(e, ast.Call):
            f = e.func
            if not isinstance(f, ast.Name):
                raise Unsupported(f"第 {e.lineno} 行: 只收直接函数调用")
            r = self.fns.get(f.id)
            if r is None:
                raise Unsupported(
                    f"第 {e.lineno} 行: 调用了本单元没有的函数 `{f.id}`"
                    f"（Stage A 不跨单元 —— 要调外部函数请留着 `pub extern fn` 那条路）")
            return r
        if isinstance(e, ast.UnaryOp):
            # Python 的 `not` **永远**出 bool（与 `and`/`or` 不同），所以它可翻。
            return "bool" if isinstance(e.op, ast.Not) else "i64"
        if isinstance(e, ast.BinOp):
            return "i64"
        if isinstance(e, (ast.Compare, ast.BoolOp)):
            return "bool"
        raise Unsupported(f"第 {e.lineno} 行: 不支持表达式 `{type(e).__name__}`"
                          f"（{_expr_name(e)}）")

    # ---- 表达式：`want` 是**这个位置要的类型**，一路显式往下传
    #
    # **不要挂在实例上** —— 嵌套时外层会串到内层。
    def ex(self, e, want: str) -> str:
        got = self.ty_of(e)
        if got == "()":
            raise Unsupported(f"第 {e.lineno} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e)
        if got == want:
            return raw
        if got == "i64" and want == "bool":
            # `if x:` / `not x` 里 x 是整数 —— "整数当条件"的两种拼法，**转**
            return f"{raw} != 0"
        if got == "bool" and want == "i64":
            # **不转，报错**。Python 的 `bool` 是 `int` 的子类（`True + 1 == 2`），
            # 而 `and`/`or` 返回的是**操作数** —— 这个意思 Loment 表达不出来。
            # 悄悄补一个 `as i64` 会让 `a and b` 变成 0/1，而 Python 给的是操作数本身。
            raise Unsupported(
                f"第 {e.lineno} 行: 这里要的是整数，给的是**布尔**。"
                f"Python 的 `bool` 是 `int` 的子类（`True + 1 == 2`），而 `and`/`or` "
                f"返回的是**操作数**（`1 and 2` 是 `2`）—— 这个意思 Loment 表达不出来。"
                f"把意思写清楚：要 0/1 就写 `1 if c else 0`，要操作数就写成 `if`/`else`")
        raise AssertionError((got, want))

    def raw(self, e) -> str:
        """表达式在**它自己的类型**下的写法。括号一律加上 —— 别让读者去猜优先级。"""
        if isinstance(e, ast.Constant):
            if isinstance(e.value, bool):
                return "true" if e.value else "false"
            return str(e.value)
        if isinstance(e, ast.Name):
            if e.id in self.consts:
                return e.id          # 模块常量名照抄 —— 生成的 `pub const` 在同层
            if e.id not in self.vars:
                raise PyError(f"第 {e.lineno} 行: 用了没赋过值的 `{e.id}`")
            return _safe(e.id)
        if isinstance(e, ast.Call):
            f = e.func
            if not isinstance(f, ast.Name):
                raise Unsupported(f"第 {e.lineno} 行: 只收直接函数调用")
            if e.keywords:
                raise Unsupported(f"第 {e.lineno} 行: 不支持关键字实参")
            # **按被调函数的形参类型要实参**（`#218`）。原先一律 `want="i64"`：
            # 一个 `bool` 形参在**任何**调用点都配不上 —— `pick(True)` 被拒，而理由
            # 还反着说（"这里要的是整数"；其实是形参要 bool、调用点硬塞 i64）。
            # 形参表是 `translate()` 从同一份 AST 上读的，不另推一遍。
            want_ps = self.params.get(f.id)
            if want_ps is None:
                # `ty_of()` 已经保证被调函数在本单元里（Stage A 不跨单元），所以
                # 走到这里只可能是调用方自己构造的 Emitter（没有传形参表）——
                # 退回旧行为，别把"少一张表"变成一条假诊断。
                return f"{_safe(f.id)}({', '.join(self.ex(a, 'i64') for a in e.args)})"
            if len(want_ps) != len(e.args):
                raise Unsupported(
                    f"第 {e.lineno} 行: 调用 `{f.id}` 要 {len(want_ps)} 个实参，"
                    f"这里给了 {len(e.args)} 个")
            return f"{_safe(f.id)}({', '.join(self.ex(a, w) for a, w in zip(e.args, want_ps))})"
        if isinstance(e, ast.UnaryOp):
            if isinstance(e.op, ast.Not):
                # Python 的 `not` 收任何真值、永远出 bool；Loment 的 `!` 只收 bool。
                return f"!{self.ex(e.operand, 'bool')}"
            if isinstance(e.op, ast.Invert):
                # **点名拒**（`docs/198` §1）。它本可以转 —— `~v` 就是"与全 1 异或" ——
                # 但那是**按宽度**的（i32 上是 `v ^ -1`、u8 上是 `v ^ 255`），而这台
                # 翻译器不做宽度跟踪，替不了作者选。原先直接映成 `"~"`，而本语言的词法器
                # 没有那个字符 —— **发出来的源编不过**，而且报的错指向生成出来的那一份。
                raise Unsupported(
                    f"第 {e.lineno} 行: 不支持按位取反 `~`（本语言的一元运算符只有 `-` 与 "
                    f"`!`；`~v` 是“与全 1 异或”，要它得**按宽度**写 `v ^ -1`（i32）或 "
                    f"`v ^ 255`（u8）—— 翻译器不做宽度跟踪，所以不替你选）")
            if isinstance(e.op, (ast.USub, ast.UAdd)):
                op = {ast.USub: "-", ast.UAdd: ""}[type(e.op)]
                return f"({op}{self.ex(e.operand, 'i64')})"
            raise Unsupported(f"第 {e.lineno} 行: 不支持一元 `{type(e.op).__name__}`")
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Pow):
                raise Unsupported(f"第 {e.lineno} 行: 不支持 `**`"
                                  f"（Loment 没有乘方；写成一个循环或调用）")
            if isinstance(e.op, ast.Div):
                raise Unsupported(f"第 {e.lineno} 行: 不支持 `/`"
                                  f"（Python 的真除出浮点，Loment 没有浮点；用 `//`）")
            op = _BIN.get(type(e.op))
            if op is None:
                raise Unsupported(f"第 {e.lineno} 行: 不支持二元 `{type(e.op).__name__}`")
            return f"({self.ex(e.left, 'i64')} {op} {self.ex(e.right, 'i64')})"
        if isinstance(e, ast.Compare):
            if len(e.ops) != 1:
                # 链式比较在 Python 里等价于 `a<b and b<c`，中间的 `b` **只求值一次**。
                # 照抄成 `(a<b) && (b<c)` 会求两次 —— `b` 是调用时结果就不一样。
                raise Unsupported(f"第 {e.lineno} 行: 不支持链式比较"
                                  f"（`a < b < c` 里中间那个只求值一次，"
                                  f"拆成 `a < b and b < c` 显式写）")
            op = _CMP.get(type(e.ops[0]))
            if op is None:
                raise Unsupported(f"第 {e.lineno} 行: 不支持比较 `{type(e.ops[0]).__name__}`")
            return f"({self.ex(e.left, 'i64')} {op} {self.ex(e.comparators[0], 'i64')})"
        if isinstance(e, ast.BoolOp):
            # `and` / `or` -> `&&` / `||`。**不再有"只能用在条件位置"那条特判** ——
            # 它由 `ex` 的类型规则管：条件位置（要 bool）照收，整数位置（要 i64）报错
            # 并说清为什么。**判据在类型上，不在语法位置上**，这才是"语义听 Loment 的"。
            op = "&&" if isinstance(e.op, ast.And) else "||"
            parts = [self.ex(v, "bool") for v in e.values]
            return "(" + f" {op} ".join(parts) + ")"
        raise Unsupported(f"第 {e.lineno} 行: 不支持表达式 `{type(e).__name__}`"
                          f"（{_expr_name(e)}）")

    # ---- 语句
    def stmts(self, body: list) -> None:
        for st in body:
            self.stmt(st)

    def stmt(self, st) -> None:
        if isinstance(st, ast.Assign):
            if len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
                raise Unsupported(f"第 {st.lineno} 行: 赋值的目标只能是单个名字")
            self._bind(st.targets[0].id, st.value, st)
            return
        if isinstance(st, ast.AnnAssign):
            if st.value is None:
                raise Unsupported(f"第 {st.lineno} 行: 只注解不给值的赋值"
                                  f"（Loment 的 `let` 要初值）")
            self._bind(st.target.id, st.value, st)
            return
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.target, ast.Name):
                raise Unsupported(f"第 {st.lineno} 行: 增量赋值的目标不是名字")
            n = st.target.id
            if n not in self.vars:
                raise PyError(f"第 {st.lineno} 行: `{n}` 没赋过值就用"
                              f"`{type(st.op).__name__}=`")
            if n in self._for_targets:
                raise Unsupported(f"第 {st.lineno} 行: 循环体里给循环变量 `{n}` 赋值"
                                  f"—— 见文件头 §`for` 的定义：那条降级式在这种写法下"
                                  f"不成立（CPython 每轮都会重置它）。改用 `while`")
            # `x += e` -> `x = x + e`（Loment 没有复合赋值）
            fake = ast.BinOp(left=ast.Name(id=n, ctx=ast.Load()), op=st.op, right=st.value)
            fake.lineno, fake.col_offset = st.lineno, st.col_offset
            self.out(f"{_safe(n)} = {self.ex(fake, self.vars[n])};")
            return
        if isinstance(st, ast.Return):
            if st.value is None:
                # 与 `~`、`**`、`/` 同一条：本语言的 `()` 函数**没有提前退出**
                # （`return;` 不是合法构造，参考实现报 `E19 期望表达式，得到 ';'`）。
                raise Unsupported(
                    f"第 {st.lineno} 行: 不支持不带值的 `return`。本语言的 `()` 函数"
                    f"**没有提前退出**——它跑到末尾就结束。把它改写成 `if`/`else` 让末尾成为"
                    f"唯一出口，或者把函数改成有返回值并用 `return 0`")
            self.out(f"return {self.ex(st.value, self.ret)};")
            return
        if isinstance(st, ast.If):
            self.gap()
            self._if_chain(st)
            return
        if isinstance(st, ast.While):
            self.gap()
            self.out(f"while {self.ex(st.test, 'bool')} {{")
            self.depth += 1
            self._for_targets |= self._loopvars(st.body)
            self.stmts(st.body)
            self._for_targets -= self._loopvars(st.body)
            self.depth -= 1
            self.out("}")
            return
        if isinstance(st, ast.For):
            self._for(st)
            return
        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                # 函数的文档字符串已在 `emit_fn` 摘走了；散落的字符串多半是写错了。
                raise Unsupported(f"第 {st.lineno} 行: 这里有一句散落的字符串"
                                  f"（只有函数第一句是文档字符串；注释用 `#`）")
            if not isinstance(st.value, ast.Call):
                raise Unsupported(f"第 {st.lineno} 行: 表达式语句只收函数调用")
            self.out(f"{self.raw(st.value)};")
            return
        if isinstance(st, ast.Pass):
            return
        raise Unsupported(f"第 {st.lineno} 行: 不支持语句 `{type(st).__name__}`"
                          f"（{_stmt_name(st)}）")

    def _if_chain(self, st: ast.If) -> None:
        """`if` / `elif` / `else` —— Loment 有 `else if`，保持那个形状（比嵌一层好读）。"""
        self.out(f"if {self.ex(st.test, 'bool')} {{")
        self.depth += 1
        self.stmts(st.body)
        self.depth -= 1
        rest = st.orelse
        while len(rest) == 1 and isinstance(rest[0], ast.If):
            self.out(f"}} else if {self.ex(rest[0].test, 'bool')} {{")
            self.depth += 1
            self.stmts(rest[0].body)
            self.depth -= 1
            rest = rest[0].orelse
        if rest:
            self.out("} else {")
            self.depth += 1
            self.stmts(rest)
            self.depth -= 1
        self.out("}")

    def _bind(self, name: str, value, st) -> None:
        """赋值一条。名字**已经在 `self.vars` 里**（形参或之前赋过）就是纯赋值；
        否则这是它的第一次出现，**就地 `let`**（类型按右值推 —— 见文件头 §决定 2）。

        **不往函数头上提** —— Python 是函数级作用域而 Loment 是块级，那是迁移就
        Python 的语义（文件头那条）。就地声明之后，"`if` 里赋的名外面要用"会由
        Loment 的检查器报出来，那是对的。
        """
        sname = _safe(name)
        # **先看它是不是循环变量** —— 再看类型。反过来的话，已经在 `vars` 里的
        # 循环变量会走"纯赋值"那条路，悄悄绕开这条闸门。
        if name in self._for_targets:
            raise Unsupported(f"第 {st.lineno} 行: 循环体里给循环变量 `{name}` 赋值"
                              f"—— 见文件头 §`for` 的定义：那条降级式在这种写法下"
                              f"不成立（CPython 每轮都会重置它）。改用 `while`")
        if name in self.vars:
            self.out(f"{sname} = {self.ex(value, self.vars[name])};")
            return
        ty = self._decl_type(st)
        self.vars[name] = ty
        self.out(f"let {sname}: {ty} = {self.ex(value, ty)};")

    def _decl_type(self, st) -> str:
        """局部变量的类型：注解优先，否则看右值（比较/布尔/`not` 出 `bool`）。"""
        if isinstance(st, ast.AnnAssign):
            return _ty_of_annotation(st.annotation, f"第 {st.lineno} 行")
        return self.ty_of(st.value)

    def _loopvars(self, body: list) -> set[str]:
        """这一层里 `for` 声明的循环变量名 —— 循环体不许给它们赋值（见 §`for`）。"""
        out: set[str] = set()
        for st in body:
            if isinstance(st, ast.For) and isinstance(st.target, ast.Name):
                out.add(st.target.id)
        return out

    #: 当前**正在发射的**循环体里那些循环变量。**在 `emit_fn` 里按函数新建** ——
    #: 别做成类属性：那是可变的共享状态，会跨函数漏。
    _for_targets: set[str]

    def _for(self, st: ast.For) -> None:
        """`for i in range(…)` -> `let i = a; while i < b { …; i = i + 1; }`。

        **这是定义，不是搬运** —— 两个后果写在文件头 §`for` 里（终值是 `b`、
        循环体不许给 `i` 赋值）。
        """
        if not isinstance(st.target, ast.Name):
            raise Unsupported(f"第 {st.lineno} 行: `for` 的目标不是单个名字")
        it = st.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range"):
            raise Unsupported(f"第 {st.lineno} 行: `for` 只收 `range(…)`"
                              f"（Python 的迭代协议在这里没有对应）")
        if it.keywords or not (1 <= len(it.args) <= 2):
            raise Unsupported(f"第 {st.lineno} 行: 只收 `range(n)` 与 `range(a, b)`"
                              f"（带步进的 `range` 先不做）")
        n = st.target.id
        sname = _safe(n)
        if len(it.args) == 1:
            start, stop = "0", self.ex(it.args[0], "i64")
        else:
            start, stop = self.ex(it.args[0], "i64"), self.ex(it.args[1], "i64")
        self.gap()
        if n in self.vars:
            self.out(f"{sname} = {start};")
        else:
            self.vars[n] = "i64"
            self.out(f"let {sname}: i64 = {start};")
        self.out(f"while {sname} < {stop} {{")
        self.depth += 1
        self._for_targets.add(n)
        self.stmts(st.body)
        self._for_targets.discard(n)
        self.out(f"{sname} = {sname} + 1;")
        self.depth -= 1
        self.out("}")

    # ---- 整个函数
    def emit_fn(self, node: ast.FunctionDef) -> str:
        self.vars = {}
        self._for_targets = set()
        self.lines = []
        self.depth = 0
        self.ret = "()"
        if node.decorator_list:
            raise Unsupported(f"第 {node.lineno} 行: 不支持装饰器")
        if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
            raise Unsupported(f"第 {node.lineno} 行: 不支持变参/位置参数")
        if node.args.kwonlyargs:
            raise Unsupported(f"第 {node.lineno} 行: 不支持 keyword-only 参数")
        if node.args.defaults or node.args.kw_defaults:
            raise Unsupported(f"第 {node.lineno} 行: 不支持默认参数"
                              f"（Loment 没有重载，写两个函数名）")
        ps = []
        for a in node.args.args:
            t = _ty_of_annotation(a.annotation, f"第 {node.lineno} 行 参数 {a.arg}")
            self.vars[a.arg] = t
            ps.append(f"{_safe(a.arg)}: {t}")
        self.ret = _ty_of_annotation(node.returns, f"第 {node.lineno} 行 返回类型")

        # **文档字符串 -> Loment 的 `///`**（`main.lomt` 那种写法）：不是丢掉它 ——
        # Python 的 docstring 是 `__doc__`，本语言有它自己的文档注释位置。
        body = list(node.body)
        doc_lines: list[str] = []
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc_lines = ["/// " + ln for ln in
                         body[0].value.value.strip().splitlines()]
            body = body[1:]

        self.lines += [ln.rstrip() for ln in doc_lines]
        head = f"pub fn {node.name}({', '.join(ps)})"
        if self.ret != "()":
            head += f" -> {self.ret}"
        self.lines.append(head + " {")
        self.stmts(body)
        self.lines.append("}")
        return "\n".join(self.lines)


def _stmt_name(st) -> str:
    return {"Break": "Loment 没有 `break`；改写条件或提前 return",
            "Continue": "Loment 没有 `continue`；用一个 did 标志改写",
            "Try": "没有异常", "With": "没有上下文管理器",
            "Import": "没有模块", "ImportFrom": "没有模块",
            "Global": "没有全局变量", "Nonlocal": "没有嵌套函数",
            "Delete": "没有 `del`", "Raise": "没有异常",
            "Assert": "没有 `assert`（写成 `if not c { return …; }`）",
            "FunctionDef": "不支持嵌套函数", "ClassDef": "不支持类",
            "Match": "没有 `match`（写成 if/elif 链）",
            }.get(type(st).__name__, "")


def _expr_name(e) -> str:
    return {"List": "没有列表", "Dict": "没有字典", "Set": "没有集合",
            "Tuple": "没有元组", "Subscript": "没有下标（先做标量）",
            "Attribute": "没有属性访问", "JoinedStr": "没有 f-string",
            "IfExp": "没有三元（写成 if/else）",
            "Await": "没有异步", "Yield": "没有生成器",
            }.get(type(e).__name__, "")


# ---------------------------------------------------------------- 入口


def parse(src: str) -> tuple[list, list, str]:
    """`(函数, 常量, 模块文档字符串)`。

    常量是 `NAME = <整数>` 并且名字全大写（与 `potato_from` 同一条判据）。
    模块文档字符串**收下**并原样带走 —— 转成 `//` 注释发出去，不是丢掉。
    """
    tree = ast.parse(src)
    fns, consts = [], []
    doc = ""
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        doc = body[0].value.value
        body = body[1:]
    for node in body:
        if isinstance(node, ast.FunctionDef):
            fns.append(node)
        elif isinstance(node, ast.AsyncFunctionDef):
            raise Unsupported(f"第 {node.lineno} 行: 不支持 async 函数")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            c = node.value
            if isinstance(c, ast.Constant) and isinstance(c.value, int) \
                    and not isinstance(c.value, bool):
                consts.append((node.targets[0].id, c.value))
            elif isinstance(c, ast.UnaryOp) and isinstance(c.op, ast.USub) \
                    and isinstance(c.operand, ast.Constant) \
                    and isinstance(c.operand.value, int):
                consts.append((node.targets[0].id, -c.operand.value))
            else:
                raise Unsupported(f"第 {node.lineno} 行: 常量 `{node.targets[0].id}` "
                                  f"的值不是整数字面量")
        else:
            raise Unsupported(f"第 {node.lineno} 行: 模块级只收 `def` 与全大写常量，"
                              f"得到 `{type(node).__name__}`")
    return fns, consts, doc


def translate(src: str, keep: set[str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """Python 写法的源码 -> Loment 源码（**只有常量与函数**，`module` 头由调用方加）。

    `keep` 给了就只翻这些函数；`consts` 是**调用方那边已知的模块常量**（名字 -> 类型）
    —— `lomt_from --impl` 走这条路时正文里只有函数，常量在 Potato 的 `consts` 里、
    由它自己发 `pub const`；不给这张表的话函数体里一引用常量就报"没赋过值"。
    """
    fns, mod_consts, doc = parse(src)
    sig: dict[str, str] = {}
    psig: dict[str, list[str]] = {}
    for f in fns:
        sig[f.name] = _ty_of_annotation(f.returns, f"第 {f.lineno} 行 返回类型")
        # 形参类型**留着**（`#218`）：调用点要按它决定每个实参的类型。
        psig[f.name] = [
            _ty_of_annotation(a.annotation, f"第 {f.lineno} 行 参数 {a.arg}")
            for a in f.args.args]
    seen: set[str] = set()
    for f in fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.lineno} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)

    cenv = {n: "i64" for n, _v in mod_consts}
    cenv.update(consts or {})       # 调用方那侧已知的常量（见 docstring）

    out: list[str] = []
    if doc:
        # 模块文档字符串 -> `//` 注释（**带走，不是丢掉**）
        out.append("\n".join("// " + ln for ln in doc.strip().splitlines()))
    if mod_consts:
        out.append("// ---- 模块常量")
        out += [f"pub const {n}: i64 = {v};" for n, v in mod_consts]
    for f in fns:
        if keep is not None and f.name not in keep:
            continue
        if out:
            out.append("")
        out.append(Emitter(sig, cenv, psig).emit_fn(f))
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pytrans", description="Python 写法 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"))
    except Unsupported as e:
        print(f"[ERR] 子集外: {e}", file=sys.stderr)
        return 1
    except (PyError, SyntaxError) as e:
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
