#!/usr/bin/env python3
# loment_rule_parity.py — 规则覆盖率探针 (M85 后半, docs/145 / docs/150)
#
# 目的: 量化"自举 checker 与参考实现是否等价" —— 这是"脱离 Python"的第一道门
#       (谁在当规范的执行者)。做法: 每条参考规则配一个**最小负例**, 两边各跑一遍,
#       按 loment_diag 的统一错误码口径比对码集。
#
# 用法:
#   python tools/loment_rule_parity.py            # 覆盖率表 + 门禁 (退出码 0/1)
#   python tools/loment_rule_parity.py --gaps     # 只列自举版还没实现的规则
#   python tools/loment_rule_parity.py --case X   # 只跑某条 (调试用)
#
# 退出码: 0 = 达到预算且无假阳性/漂移 / 1 = 未达标 (便于 CI 里"只紧不松"地卡住)
#
# 门禁 (ratchet): 允许的缺口是**有界、可数、只减不增**的 —— 判据是
#   eq >= BUDGET             (等价规则数不得低于预算)
#   extras == 0 && drift == 0 && nopy == 0   (假阳性/口径漂移/探针失效必须是 0)
# 补完一批就把 BUDGET 往上调; 只调低 BUDGET 是**放松**门禁, 等于隐瞒缺口。
#
# 判据: status 列
#   EQUAL   两边码集相同           (等价)
#   MISSING 自举版少报 (缺口, 要补) (⊂)
#   EXTRA   自举版多报 (假阳性)     (⊃)  —— 这个更糟, 会误拒合法程序
#   DIFF    两边都不空但码不同     (码口径漂移)
#   NOPY    参考实现没报错 (探针本身写错了, 案例不算数)

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

import loment_p8_test as H  # noqa: E402  # 复用已验过的构建/运行夹具 (避免第二份漂移)

# 门禁预算: 批次 1 (声明级规则) 24/60; 批次 2 第一段 (语句级类型比对) 32/60;
# 批次 2 第二段 (复合类型表 + 表达式遍历器 + 字段/下标/数组/`as`/实参/方法/match/`?`/
# 移动借用) 后 **63/63** —— 自举 checker 与参考实现在这 63 条规则上完全等价
# (含 M13 移动 E006 与 M17 悬垂 E012)。
# 2026-09-17 项目模式 `choose` (E022) 进语言: 检查器那两条 (至多一次 / 模式名合法)
# 补完, **65/65**。第三条 ("库不许 choose") 是装载器规则, 不在这个数里。
#
# 2026-09-17 开关 (`docs/182` §1) 进语言: **69/69**。四条 —— 关着时体内不报 (裁减真生效)、
# 开着时照报、未定义的开关、同名两次。**前两条正是"`choose` 从承诺变发明"的证据**:
# 在此之前它什么都不驱动, 现在它真的决定一段代码编不编进去。
# 每补完一批就**往上调** —— 只调低是放松门禁, 等于隐瞒缺口。
# 2026-10-09 `runtime` 维**兑现**（`docs/227`）: **92/92**。两条 —— 默认档（`no_runtime`）
# 下，`/` 与 `alloc` 各一条。这一维从此不再是"只声明"：它成了一句**保证**，而保证要有
# 可证伪的判据，所以它必须进这张表（两个实现各判一次才叫等价）。
#
# 2026-10-10 `register`（`docs/223` 形态 A）进语言: **86/86**。五条 —— 撞官方名、
# 与 `_start` 并存、签名不对、有声明没体（新触发）、有体没声明（原样）。块那种写法与
# 函数那种写法**判成同一批码**，这正是"形态 A 编译到形态 B"要看见的东西。
# 2026-10-10 `choose hosted`（`docs/222` §4，第四维 `port`）落地: **90/90**。四条 ——
# 按维写两次、同维两个取值、`hosted` × `no_std`、`hosted` × `gc_auto_alpha`。
# （`hosted` × `gc_manual` / `gc_auto` 是**合法档**，不进这张负例表，由 `potato_test`
# 的 `v11-hosted-gc_manual` 合法样本守着。）
# **这一条当场抓到一个真分歧**：冲突说明里写了"悬垂"两个字，撞上 E012 的关键词分类器 ——
# 参考实现把那条冲突归成 E012、自举归成 E022。改写措辞之后两边一致。
# 2026-10-14 真线程 `thread_spawn`（M84, `docs/245`）落地: **102/104**。五条 ——
# 入口不是字面量、入口在本单元里查不到、入口形参不是一个、元数不对、实参类型不对。
# 这一族是"**直接调用**买到的东西"（入口是编译期的名字），所以三条语义都判得了。
# 2026-10-15 LMS 第 1 级检查器 `E028`（`docs/253` §5.1，字面量下标越过定长数组的界）：
# 两套实现都补齐 ⇒ **103/105**。这一条**只有这张表抓得住** —— 孪生判据
# (`loment_p8_test` / `loment_potato_emit_test`) 的语料里没有一个用 `lms_open`，
# 所以"参考侧拒、自举侧不拒"在那两条判据下照样绿。
# ⚠ 补这一条时**当场抓到一个真 bug**：加 `lms` 那一维时自举侧 `chk_core_dim` 的
# "不是核心维"哨兵没跟着从 5 挪到 6，于是 `choose lms_open` 被当成**未定义的开关**
# 报 E022 —— 码是错的、理由也是错的，而别的判据全绿。**这就是这张表存在的理由。**
# 2026-10-15 同日 `E029`（用完再放）也补齐 ⇒ **104/106**。两条各一个探针，两边码集相等。
BUDGET = 104

# --------------------------------------------------------------------------- 案例表
#
# 每条 = (规则名, 源码)。规则名里的 `E0xx` 是 loment_diag.RULES 的期望口径,
# 只在注释里做提示 —— 但**判据不比注释, 只比两边实测码集**, 所以注释写错不会误判。
# 源码一律是**单编译单元**(无 use): 自举 checker 不解析模块导入 (docs/150 边界)。
#
# **`addin` 那几条进不来, 预算因此没动**(C2, `docs/182` §1.4/§1.10): `addin` 是**装载层**
# 的规则(要两个单元: 入口 + 它 `addin` 到的那个), 而这张表喂的是自包含单文件。
# 这与 `docs/182` §4 落点表里 `addin` 那一行的判断一致 —— **装载器规则走棘轮**,
# 先例 `loment_p8_test` 的驱动闸门(`test_m86_driver_handles_addin` 等)。
# **不是漏了, 是落点不同**: 只调低 `BUDGET` 才是放松, 不调不是。

_CASES: list[tuple[str, str]] = [
    # ---- 顶层重名 (E013) --------------------------------------------------
    ("dup-fn", "module m\n\nfn f() -> u32 {\n    return 1;\n}\n\nfn f() -> u32 {\n    return 2;\n}\n"),
    ("dup-struct", "module m\n\nstruct S {\n    a: u32,\n}\n\nstruct S {\n    b: u32,\n}\n"),
    ("dup-enum", "module m\n\nenum E {\n    A,\n}\n\nenum E {\n    B,\n}\n"),
    ("dup-const", "module m\n\nconst C: u32 = 1;\nconst C: u32 = 2;\n"),
    ("dup-fn-vs-const", "module m\n\nfn C() -> u32 {\n    return 1;\n}\n\nconst C: u32 = 2;\n"),
    ("dup-param", "module m\n\nfn f(a: u32, a: u32) -> u32 {\n    return a;\n}\n"),
    # ---- 结构体/枚举定义 (E009 / E013) -------------------------------------
    ("struct-field-dup", "module m\n\nstruct S {\n    a: u32,\n    a: u32,\n}\n"),
    ("struct-empty", "module m\n\nstruct S {\n}\n"),
    ("struct-base-name", "module m\n\nstruct u32 {\n    a: u32,\n}\n"),
    ("enum-variant-dup", "module m\n\nenum E {\n    A,\n    A,\n}\n"),
    ("enum-empty", "module m\n\nenum E {\n}\n"),
    ("enum-base-name", "module m\n\nenum u32 {\n    A,\n}\n"),
    # ---- 声明里的未声明类型 (E002) -----------------------------------------
    ("struct-field-type-unknown", "module m\n\nstruct S {\n    a: Foo,\n}\n"),
    ("enum-payload-type-unknown", "module m\n\nenum E {\n    A(Foo),\n}\n"),
    ("fn-ret-type-unknown", "module m\n\nfn f() -> Foo {\n    return 1;\n}\n"),
    ("fn-param-type-unknown", "module m\n\nfn f(x: Foo) -> u32 {\n    return 1;\n}\n"),
    ("let-type-unknown", "module m\n\nfn f() -> u32 {\n    let x: Foo = 1;\n    return x;\n}\n"),
    # ---- 常量 (E001 / E013) -----------------------------------------------
    # 注意: `const C: bool = true;` 在**解析期**就被拒 (常量值走 int_lit), 到不了 check;
    # 所以能触发类型规则的只有 "类型不是整型但值是整数字面量" 这个形状。
    ("const-non-int-type", "module m\n\nconst C: bool = 0;\n"),
    # ---- 能力域 (E004 / E005) ---------------------------------------------
    ("cap-dup", "module m\n\ncapability c : space[1..2]\ncapability c : space[1..2]\n"),
    ("cap-lo-gt-hi", "module m\n\ncapability c : space[5..2]\n"),
    ("cap-unknown-guard",
     "module m\n\nfn f() -> u32 {\n    guard nope(0);\n    return 1;\n}\n"),
    # ---- 调用 (E002 / E003 / E001) ----------------------------------------
    ("call-unknown-fn", "module m\n\nfn f() -> u32 {\n    return g(1);\n}\n"),
    ("call-arity", "module m\n\nfn g(a: u32) -> u32 {\n    return a;\n}\n\nfn f() -> u32 {\n    return g(1, 2);\n}\n"),
    ("call-arg-type", "module m\n\nfn g(a: u32) -> u32 {\n    return a;\n}\n\nfn f() -> u32 {\n    return g(true);\n}\n"),
    ("builtin-arity", "module m\n\nfn f() -> u32 {\n    return str_len();\n}\n"),
    ("builtin-arg-type", "module m\n\nfn f() -> u32 {\n    return str_len(1);\n}\n"),
    # ---- 表达式 (E001 / E008) ---------------------------------------------
    ("let-type-mismatch", "module m\n\nfn f() -> u32 {\n    let x: u32 = true;\n    return x;\n}\n"),
    ("return-type-mismatch", "module m\n\nfn f() -> u32 {\n    return true;\n}\n"),
    ("assign-undeclared", "module m\n\nfn f() -> u32 {\n    x = 1;\n    return 0;\n}\n"),
    ("assign-type-mismatch",
     "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    x = true;\n    return x;\n}\n"),
    ("assign-not-lvalue", "module m\n\nfn f() -> u32 {\n    1 = 2;\n    return 0;\n}\n"),
    # 条件类型用 `str` 变量触发 (整型字面量的类型推断是 None, 参考实现据此放行)
    ("if-cond-not-bool",
     "module m\n\nfn f(s: str) -> u32 {\n    if s {\n        return 1;\n    }\n    return 0;\n}\n"),
    ("while-cond-not-bool",
     "module m\n\nfn f(s: str) -> u32 {\n    while s {\n        return 1;\n    }\n    return 0;\n}\n"),
    # `for i in A..B` 的 A/B 必须是整型 (语法上 for 只吃 range, 没有 `for i in x` 形式)
    ("for-non-int",
     "module m\n\nfn f(s: str) -> u32 {\n    for i in s..2 {\n        return i;\n    }\n    return 0;\n}\n"),
    ("as-bad-target", "module m\n\nfn f() -> u32 {\n    return (1 as Foo);\n}\n"),
    ("as-bad-operand", "module m\n\nfn f(s: str) -> u32 {\n    return (s as u32);\n}\n"),
    # ---- 字段 / 下标 / 数组 (E002 / E009) ---------------------------------
    ("field-on-non-struct", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    return x.a;\n}\n"),
    ("field-unknown-name",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f(p: S) -> u32 {\n    return p.z;\n}\n"),
    ("structlit-unknown-field",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { z: 1 };\n    return p.a;\n}\n"),
    ("structlit-dup-field",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { a: 1, a: 2 };\n    return p.a;\n}\n"),
    ("structlit-missing-field",
     "module m\n\nstruct S {\n    a: u32,\n    b: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { a: 1 };\n    return p.a;\n}\n"),
    ("array-empty", "module m\n\nfn f() -> u32 {\n    let a: [u32; 4] = [];\n    return a[0];\n}\n"),
    ("array-elem-type", "module m\n\nfn f() -> u32 {\n    let a: [u32; 2] = [1, true];\n    return a[0];\n}\n"),
    ("index-non-array", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    return x[0];\n}\n"),
    ("index-non-int",
     "module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[true];\n}\n"),
    ("slice-len-arity", "module m\n\nfn f() -> u32 {\n    return slice_len();\n}\n"),
    # 整型字面量的类型推断是 None, 参考实现据此放行 -> 用 str 变量才能触发
    ("slice-len-arg-type", "module m\n\nfn f(s: str) -> u32 {\n    return slice_len(s);\n}\n"),
    # ---- match (E008 / E001) ----------------------------------------------
    # 模式语法只吃 `Enum::Variant` 或 `_` (没有整型/字面量模式), 臂体必须是块。
    ("match-not-enum",
     "module m\n\nfn f() -> u32 {\n    match 1 {\n        _ => { return 1; }\n    }\n    return 0;\n}\n"),
    ("match-unknown-variant",
     "module m\n\nenum E {\n    A,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::Z => { return 1; }\n    }\n}\n"),
    ("match-dup-pattern",
     "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A => { return 1; }\n        E::A => { return 2; }\n    }\n}\n"),
    ("match-nonexhaustive",
     "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A => { return 1; }\n    }\n}\n"),
    ("match-payload-arity",
     "module m\n\nenum E {\n    A,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A(x) => { return x; }\n    }\n}\n"),
    ("match-payload-type",
     "module m\n\nenum E {\n    A(u32),\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A(b) => { if b { return 1; } return 2; }\n    }\n}\n"),
    ("match-enum-lit-no-payload",
     "module m\n\nenum E {\n    A,\n}\n\nfn f() -> u32 {\n    let e: E = E::A(1);\n    return 0;\n}\n"),
    ("match-enum-lit-bad-payload",
     "module m\n\nenum E {\n    A(u32),\n}\n\nfn f() -> u32 {\n    let e: E = E::A(true);\n    return 0;\n}\n"),
    # ---- `?` / 借用 / 方法 (E010 / E007 / E011 / E002) ---------------------
    ("qmark-misuse", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1?;\n    return x;\n}\n"),
    ("method-unknown",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f(p: S) -> u32 {\n    return p.nope();\n}\n"),
    ("slice-write-readonly",
     "module m\n\nfn f(xs: [u32]) -> u32 {\n    xs[0] = 1;\n    return 0;\n}\n"),
    ("borrow-mut-twice",
     "module m\n\nfn g(a: mut [u32], b: mut [u32]) -> u32 {\n    return 0;\n}\n\nfn f(xs: mut [u32]) -> u32 {\n    return g(&mut xs, &mut xs);\n}\n"),
    ("borrow-mut-and-share",
     "module m\n\nfn g(a: mut [u32], b: [u32]) -> u32 {\n    return 0;\n}\n\nfn f(xs: mut [u32]) -> u32 {\n    return g(&mut xs, &xs);\n}\n"),
    # ---- 移动 / 悬垂 (E006 / E012) ----------------------------------------
    # 非 Copy 类型 (struct / 数组) 的变量"传出去"就算移动: `let t: S = s;` 之后 s 不能再用。
    ("move-use-after",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let s: S = S { a: 1 };\n    let t: S = s;\n    return s.a;\n}\n"),
    ("move-by-arg",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn take(p: S) -> u32 {\n    return p.a;\n}\n\nfn f() -> u32 {\n    let s: S = S { a: 1 };\n    let n: u32 = take(s);\n    return s.a;\n}\n"),
    # 返回局部变量的借用 = 悬垂 (`&arr` 借用局部数组)
    ("dangling-return",
     "module m\n\nfn f() -> [u32] {\n    let a: [u32; 2] = [1, 2];\n    return &a;\n}\n"),
    # ---- 项目模式 `choose` (E022, docs/143 §3.2) --------------------------
    # 这两条是**检查器**规则。第三条 ("库不许 choose") 是装载器规则, 装不进这里 ——
    # 套件喂的是自包含单文件源码, 没有"被 use 进来的那一层"。与 E018 同构,
    # 棘轮由驱动闸门 (`loment_p8_test` 的 neg_dep_choose) 承担, 这两条才进预算。
    ("choose-twice", "module m\n\nchoose std\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-bad-mode", "module m\n\nchoose fast\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # ---- 核心模式的**维**（`docs/175` §3.0 / §3.4, 2026-09-23 加 `gc`）--------
    # "只能声明一次"从"总共一次"改成**按维**一次 —— 所以这三条各钉一边：
    # 同一维写两次（同值 / 两值）都得报，而**两维各一份是合法的**（不在套件里，
    # 它在 `lomentc_test` 的正例那边）。第四条是两档冲突。
    ("choose-gc-twice", "module m\n\nchoose gc_auto\nchoose gc_auto\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-gc-two-values", "module m\n\nchoose gc_manual\nchoose gc_auto\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-no-std-gc-auto", "module m\n\nchoose no_std\nchoose gc_auto\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # 混合档（`gc_auto_alpha`）同一条冲突，**只强不弱** —— 它比 `gc_auto`
    # 更依赖运行期（要自适应、要策略池）。
    ("choose-no-std-gc-alpha", "module m\n\nchoose no_std\nchoose gc_auto_alpha\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # ---- 第三维 `runtime`（`docs/175` §3.6）---------------------------------
    # 与 `gc` 完全同一套：按维一次、取值只有两个、与自动回收**定义上矛盾**。
    ("choose-runtime-twice", "module m\n\nchoose runtime\nchoose runtime\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-runtime-two-values", "module m\n\nchoose runtime\nchoose no_runtime\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # **有一条不冲突的配对必须记着**：`runtime` + `gc_manual` = "要运行期、但内存
    # 我自己管" —— 它是合法档，**不该**报错。套件只收负例，所以这里放的是该报的
    # 那一对；合法的那一对由 `potato_test` 的 `v9-runtime-on` 合法样本守着。
    ("choose-no-runtime-gc-auto", "module m\n\nchoose no_runtime\nchoose gc_auto\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # ---- `runtime` 维**兑现**（`docs/227`，2026-10-09）--------------------------------
    # 这一维从"只声明"变成"有保证"：`no_runtime`（默认）说"产物里没有运行期"，于是凡是
    # 会把那段运行期拖进产物的构造，在**这一档下**编译期点名拒。两条负例各钉一边 ——
    # 一条走**算术**（`/` 拖进 `__loment_abort`）、一条走**分配**（`alloc` 拖进分配器 +
    # 堆全局），因为参考实现的触发是**分开判**的（`@__loment_` 与 `@__loment_alloc`）。
    ("choose-no-runtime-div", "module m\n\nfn f() -> u32 {\n    return 10 / 3;\n}\n"),
    ("choose-no-runtime-alloc", "module m\n\nchoose no_runtime\n\nfn f() -> u32 {\n    let p: ptr = alloc(8);\n    return 0;\n}\n"),
    # ---- 第四维 `port`（`docs/222` §4）--------------------------------------
    # 与前面三维同一套：按维一次、取值只有两个、与两个档**定义上矛盾**。
    ("choose-port-twice", "module m\n\nchoose sealed\nchoose sealed\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-port-two-values", "module m\n\nchoose sealed\nchoose hosted\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # `no_std` 说"底下没有东西"、`hosted` 说"往下链东西" —— 定义上矛盾。
    ("choose-hosted-no-std", "module m\n\nchoose no_std\nchoose hosted\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # 混合档的 L2 是前沿回卷，而外部库把指针放进它自己的结构里（`docs/219` §6.1）。
    ("choose-hosted-gc-alpha", "module m\n\nchoose hosted\nchoose gc_auto_alpha\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # **有一条不冲突的配对必须记着**：`hosted` + `gc_manual` / `gc_auto` = "要对外、
    # 但内存我自己管" —— 合法档，**不该**报错。这套件只收负例，所以合法的那一对由
    # `potato_test` 的 `v11-hosted-gc_manual` 合法样本守着，这里不重复。
    # ---- 第五维 `vm`（`docs/231`）------------------------------------------
    # 与前四维同一套：按维一次、取值只有两个。**但这一维没有冲突用例** ——
    # `vm` 与 `runtime` 今天确实合不来（VM 要用运行期），那条由 `docs/227` 那条**既有的**
    # 检查接住（它读源码里用没用到运行期构造），不是一条写进 `CORE_CONFLICTS` 的冲突。
    ("choose-vm-twice", "module m\n\nchoose vm\nchoose vm\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-vm-two-values", "module m\n\nchoose vm\nchoose no_vm\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # 取值只有两个 —— 拼错的词与 `choose fast` 同一条路（未定义的开关）。
    ("choose-vm-bad-value", "module m\n\nchoose vm_x\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    # **渠道闸不在这里测**：这套件跑在仓库里，两边都是 dev 通道，`choose vm` 是**合法**的。
    # "发行版拒"只能靠**打包侧端到端**（照 `docs/202`"真装真跑"）—— 见 `docs/231` §5 判据 D。
    # 合法的那一对（dev + `choose vm`）由 `potato_test` 的 v13 正例守着，这里不重复。
    # ---- 开关 (docs/182 §1) -------------------------------------------------
    # **关着**: 体连 token 都不进 parser（docs/182 §2）。所以体内那条类型错**不该报**，
    # 只报体外面那条 —— 两边都得这样。**不要**把体写成一个"关着就什么都不报"的源：
    # 那样参考实现不报错，就不是一条合法的**负例**（这套件测的是"该报的报了没有"）。
    ("switch-off", "module m\n\nset choose feat {\n    fn h() -> u32 {\n"
                   "        let x: u32 = true;\n        return x;\n    }\n}\n\n"
                   "choose close feat\n\nfn f() -> u32 {\n    return missing();\n}\n"),
    ("switch-on", "module m\n\nset choose feat {\n    fn h() -> u32 {\n"
                  "        return undefined_thing;\n    }\n}\n\nchoose feat\n\n"
                  "fn f() -> u32 {\n    return 1;\n}\n"),
    ("switch-undef", "module m\n\nchoose nope\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("switch-dup", "module m\n\nset choose feat {\n}\n\nchoose feat\nchoose close feat\n\n"
                   "fn f() -> u32 {\n    return 1;\n}\n"),
    # ---- 控制流: 非 () 的函数可能从末尾掉出去 (E024, `#22`) --------------
    # 这张表只收负例(参考实现必须报错, 否则记 NOPY) —— 所以合法的收尾不在这里,
    # 它们由 `loment_p8_test` 的正例钉子管。
    # 自举 checker 还没这条规则 ⇒ 它是 MISSING(允许; 补完再把 BUDGET 调上去)。
    ("falloff-if", "module m\n\nfn pick(x: u32) -> u32 {\n    if x > 0 {\n        return 1;\n    }\n}\n"),
    ("falloff-match-arm", "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn pick(e: E) -> u32 {\n    match e {\n        E::A => {\n            return 1;\n        }\n        _ => {\n            let v: u32 = 2;\n        }\n    }\n}\n"),
    # ---- 命令声明 (E024/E025/E026, docs/223) --------------------------------
    # 这五条是**检查器**规则（只看这一个单元）。第六条"库不许声明命令"是**装载器**规则
    # —— 它要两个单元（入口 + 被 use 的那个），装不进这张表，与 `choose` 那第三条
    # 同一个落点：棘轮由 `loment_p8_test` 的驱动闸门承担，预算里不算它。
    ("cmd-bad-name",
     "module m\n\npub fn loment_command() -> str { return \"a/b\"; }\n\n"
     "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n"),
    ("cmd-reserved-name",
     "module m\n\npub fn loment_command() -> str { return \"version\"; }\n\n"
     "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n"),
    ("cmd-bad-shape",
     "module m\n\npub fn loment_command() -> str {\n"
     "    let s: str = \"x\";\n    return s;\n}\n\n"
     "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n"),
    ("cmd-two-entries",
     "module m\n\npub fn loment_command() -> str { return \"mcmd\"; }\n\n"
     "fn _start() {\n    syscall4(60, 0, 0, 0);\n}\n\n"
     "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n"),
    ("cmd-body-no-decl",
     "module m\n\npub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n"),
    # ---- `register`（形态 A，`docs/223` §2 第 3 步）--------------------------------
    # 与上面那五条**同一批码**，只是换了一种写法：名字由**语法**给出（不再从函数体里扫
    # 字符串），块里是这条命令的条目。两种写法在两边都必须判成同一个码。
    ("reg-reserved-name",
     "module m\n\nregister version {\n"
     "    pub fn command_main(argv: ptr, argc: u32) -> u32 {\n        return 0;\n    }\n}\n"),
    ("reg-two-entries",
     "module m\n\nfn _start() {\n    syscall4(60, 0, 0, 0);\n}\n\nregister mcmd {\n"
     "    pub fn command_main(argv: ptr, argc: u32) -> u32 {\n        return 0;\n    }\n}\n"),
    ("reg-bad-signature",
     "module m\n\nregister mcmd {\n"
     "    pub fn command_main(a: u32, b: u32) -> u32 {\n        return 0;\n    }\n}\n"),
    ("reg-no-main",
     "module m\n\nregister mcmd {\n"
     "    pub fn helper() -> u32 {\n        return 0;\n    }\n}\n"),
    # 反向那一半（声明在、体不在）也要成对 —— 它原先要等到**链接**才炸。
    ("cmd-decl-no-body",
     "module m\n\npub fn loment_command() -> str { return \"mcmd\"; }\n\n"
     "fn helper() -> u32 {\n    return 0;\n}\n"),
    # ---- excluded 的**行为轴** (E005, `docs/218` §7 第 2 件 / `docs/227` §3) --------
    # 这一族钉的是"**名字之外**的那一半"：M41 比的是空间名（`excluded "usb"` 对
    # `capability … : usb[…]`），它**从不看行为**，所以"声明不碰网络、代码里在调 socket"
    # 是它看不见的。两种写法（`network` / `net`）各一条 —— 参考实现认两个，自举版也得认。
    ("excl-net-socket",
     "module m\n\nexcluded \"network: 本单元不申请任何 net 能力\"\n\n"
     "fn f() -> i64 {\n    return syscall6(41, 2, 1, 0, 0, 0);\n}\n"),
    ("excl-net-bind",
     "module m\n\nexcluded \"net: 不碰网络\"\n\n"
     "fn f() -> i64 {\n    return syscall6(49, 3, 0, 16, 0, 0);\n}\n"),
    # ---- 真线程 `thread_spawn` (M84, `docs/245`) -----------------------------------
    # 这一族是"**直接调用**买到的东西"：入口是编译期的名字，于是**三条**都能在编译期判 ——
    # 是字面量、本单元里真有这个函数、形参恰好一个。两侧各写一遍（参考 `_walk_expr` 的
    # 内建分支 / 自举 `chk_tex_atom_call` + `chk_lookup_lit_name`），所以在这里成对钉。
    # **除了 arity 那条，全用满四个实参** —— 元数不对时两侧走的是不同的分支（参考只在
    # `else` 里做那三条检查，自举是"元数先报、检查照做"），要钉的是那三条本身，不是叠加。
    ("thread-entry-not-literal",
     "module m\n\nfn worker(a: u64) -> u32 {\n    return 0;\n}\n\n"
     "fn f(s: str) -> i64 {\n    return thread_spawn(s, 0, 1, 2);\n}\n"),
    ("thread-entry-unknown",
     "module m\n\nfn f() -> i64 {\n    return thread_spawn(\"nope\", 0, 1, 2);\n}\n"),
    ("thread-entry-two-params",
     "module m\n\nfn worker(a: u64, b: u64) -> u32 {\n    return 0;\n}\n\n"
     "fn f() -> i64 {\n    return thread_spawn(\"worker\", 0, 1, 2);\n}\n"),
    ("thread-arity",
     "module m\n\nfn worker(a: u64) -> u32 {\n    return 0;\n}\n\n"
     "fn f() -> i64 {\n    return thread_spawn(\"worker\", 0, 1);\n}\n"),
    ("thread-arg-type",
     "module m\n\nfn worker(a: u64) -> u32 {\n    return 0;\n}\n\n"
     "fn f() -> i64 {\n    return thread_spawn(\"worker\", true, 1, 2);\n}\n"),
    # ---- LMS 第 1 级检查器 (E028) ------------------------------------------
    # `docs/253` §5.1：`lms` 开着时，**字面量下标越过定长数组的界**是编译期的一个诊断。
    # ⚠ **这一条为什么必须进这张表**：孪生判据**看不见**这个分歧 ——
    # `loment_p8_test` / `loment_potato_emit_test` 的语料里没有一个用 `lms_open`，
    # 所以"参考侧拒、自举侧不拒"在那两条判据下**照样绿**（孪生只证一致，不证对）。
    # 只有这张表会让同一个 `choose lms_open` 源**两边各跑一遍、比码集**。
    ("lms-e028-literal-index-over-a-fixed-array",
     "module m\n\nchoose lms_open\n\nfn f() -> u32 {\n"
     "    let a: [u32; 4] = [1, 2, 3, 4];\n    let x: u32 = a[10];\n    return x;\n}\n"),
    ("lms-e029-use-after-free",
     "module m\n\nchoose runtime\nchoose lms_open\n\nfn f() -> u32 {\n"
     "    let p: ptr = alloc(8);\n    free(p);\n"
     "    let x: u32 = load8(p, 0);\n    return x;\n}\n"),
    # ---- 语句起点的"这不是表达式" (issue #49) ---------------------------------
    # `@` 在**词法上是合法 punct**（L0 的布局语法用它），L1 里却不是任何表达式的开头。
    # 参考实现自 2026-09-09 起就报 E019；自举侧原先**一条都不报** —— 编译通过、退出码 0、
    # 什么都不说（实测 0.1.4.5 发布版同）。两侧在这条上一直分叉，补一条探针钉住它。
    ("stmt-not-expression",
     "module m\n\nfn f() -> i64 {\n    @;\n    return 0;\n}\n"),
    ("expr-not-expression",
     "module m\n\nfn f() -> u32 {\n    let v: u32 = @ 1;\n    return v;\n}\n"),
    # ---- 0.1.4.6 的一批"自举侧说得不对或不说"的探针 -------------------------------
    # 分诊（2026-10-15，已发布 0.1.4.5 对照参考实现）：这几条在已发布编译器上要么给错码、
    # 要么一声不吭，而参考侧都报得出来。本判据的分工就是把这类分叉**量成 MISSING**，
    # 而不是靠人一条条去比对。
    # 注意：`let x = 1;`（#7）与 `let a: f64 = 1.0;`（#323）也量过，是 **DIFF** ——
    # 自举报 E002、参考报 E019。DIFF 是"假阳性/口径漂移"那一档，必须为 0，所以这两条
    # **不能**进这份表；它们记在各自的 issue 上，修好（变成 EQUAL）再收进来。
    ("break-not-a-name",
     "module m\n\nfn f() -> u32 {\n    let i: u32 = 0;\n    while i < 3 {\n        break;\n    }\n    return i;\n}\n"),
    ("undeclared-in-return",
     "module m\n\nfn f() -> u32 {\n    return undefined_thing;\n}\n"),
    # `let x = 1;`（#7）与 `let a: f64 = 1.0;`（#323）—— 这两条原先量出来是 DIFF / EXTRA：
    # 参考实现**解析错就中止**，检查器不中止，于是它把下游的伤也报出来（`x` 的类型槽去解
    # 下一个 token、`f64` 不是类型），码集永远多一个。补的**不是**一条条检查，是
    # `chk_syntax_prepass` 那道**中止语义**（进了语法错就不再跑任何检查趟）。
    ("let-without-type",
     "module m\n\nfn f() -> u32 {\n    let x = 1;\n    return x;\n}\n"),
    ("f64-in-annotation",
     "module m\n\nfn f() -> u32 {\n    let a: f64 = 1.0;\n    return 0;\n}\n"),
    # `#323` 的另一半：**类型位上的未知名**是 E030，两侧都要是。上面那条压根到不了类型表
    # （`1.0` 里的 `.` 在解析期就被拒成 E019）；这两条才量到 E030 本身。
    # 第二条尤其要紧：在 `check_type` 补上 `[` 的递归之前，自举侧**一条都不报** ——
    # 数组/切片的元素类型从来没被检查过，也就是**静默放行一个不存在的类型**。
    ("unknown-type-let",
     "module m\n\nfn f() -> u32 {\n    let a: f64 = 0;\n    return 0;\n}\n"),
    ("unknown-type-array-element",
     "module m\n\nfn f() -> u32 {\n    let a: [f64; 4] = [0, 0, 0, 0];\n    return 0;\n}\n"),
    # `#33` / `#36`：字符串里的**未知转义**。参考实现在词法期就拒（E019）；自举词法器没有
    # 失败通道，所以同一件事落在 `chk_syntax_prepass` —— 两边的**码**必须一样。
    # `#289`: two integer operands with no common type. The reference names the pair and says
    # which `as` to write; the self-host reports E001 at the operator. Same code, and the
    # widening half is pinned by `lomentc_test::test_mixed_width_arithmetic_has_a_common_type`.
    ("mixed-width-no-common",
     "module m\n\nfn bad(a: u32, b: i32) -> u32 { return a + b; }\n"),
    ("unknown-string-escape",
     "module m\n\nfn f() -> u32 {\n    let s: str = \"a\\qb\";\n    return 0;\n}\n"),
    # `#48`：`loment check` 对**编不过**的源退出 0、零输出。两条都是"自举侧没有解析器，
    # 于是这种语法错在它眼里根本不存在"：块没闭合、语句之间少一个 `;`。参考实现在解析期
    # 各报一条 E019，自举侧原先**一条都不报** —— 于是 check 说"没事"。
    ("unclosed-brace",
     "module m\n\nfn f() -> u32 {\n    let v: u32 = 1;\n"),
    ("missing-semicolon",
     "module m\n\nfn f() -> u32 {\n    let v: u32 = 1\n    let w: u32 = 2;\n"
     "    return v;\n}\n"),
    ("bin-literal",
     "module m\n\nfn f() -> u32 {\n    let v: u32 = 0b1010;\n    return v;\n}\n"),
    # ---- 自举侧"死给你看"的两条 (issue #335 / #44) --------------------------------
    # 裸 `return;` 参考实现报 E019；自举侧原先**段错误、零输出**。
    ("bare-return",
     "module m\n\nfn f() {\n    return;\n}\n"),
    # 深层嵌套参考实现报 E023（源码太深，上限 64 层）；自举侧原先走到 codegen 才把栈用穿
    # —— 也是段错误。**表达式要包一层括号**，否则后面的用例会被并进这个元组。
    ("deep-nesting",
     ("module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n"
      + "".join("    if x > 0 {\n" for _ in range(100))
      + "".join("    }\n" for _ in range(100))
      + "    return x;\n}\n")),
]


def _report(rid: str, src: str, exe: Path, td: str) -> tuple[str, str, list[int], list[int]]:
    """跑一条案例, 返回 (status, 明细, py 码, loment 码)。"""
    f = Path(td) / f"case_{rid}.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    try:
        py_msgs = lomentc.check(lomentc.load(f))
    except lomentc.LomError as e:
        # **解析期**的错误是**抛**出来的，不是回一张消息表 —— 原先这里只接了后者，于是任何
        # 在 `load()` 就失败的探针会把整套判据崩掉，而不是记一条结果。E019 这一族恰恰全在
        # 解析期，所以此前一条探针都没有（`stmt-not-expression` 是第一条）。
        # `str(LomError)` 就是分类器吃的 `行:列: 文本`（`lomc.LomError.__init__`）。
        py_msgs = [str(e)]
    py = H._classify_codes(py_msgs)
    lo, detail = H._loment_codes(exe, f)
    if not py:
        return "NOPY", "参考实现未报错", py, lo
    sp, sl = set(py), set(lo)
    if sp == sl:
        st = "EQUAL"
    elif sl < sp:
        st = "MISSING"
    elif sl > sp:
        st = "EXTRA"
    else:
        st = "DIFF"
    unclass = [m for m in py_msgs if H.loment_diag.classify(m)[0] == "E999"]
    det = f"py={sorted(sp)} loment={sorted(lo)}"
    if unclass:
        det += " | 未分类: " + unclass[0][:60]
    return st, det, py, lo


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    only: str | None = None
    gaps_only = False
    i = 0
    while i < len(args):
        if args[i] == "--case" and i + 1 < len(args):
            only = args[i + 1]
            i += 2
        elif args[i] == "--gaps":
            gaps_only = True
            i += 1
        else:
            print(__doc__)
            return 2
    clang = H._clang()
    if not clang:
        print("[SKIP] 无 clang, 无法构建自举 checker")
        return 0
    cases = [(r, s) for r, s in _CASES if only is None or r == only]
    if not cases:
        print(f"[ERR] 没有案例匹配 {only!r}")
        return 2
    counts: dict[str, int] = {}
    gaps: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as td:
        exe = H._build_checker(td)
        for rid, src in cases:
            st, det, _py, lo = _report(rid, src, exe, td)
            counts[st] = counts.get(st, 0) + 1
            if st == "NOPY":
                gaps.append((rid, "探针失效: 参考实现未报错"))
            elif st != "EQUAL":
                gaps.append((rid, det))
            if not gaps_only or st != "EQUAL":
                print(f"  {st:8s} {rid:28s} {det}")
    total = sum(counts.values())
    eq = counts.get("EQUAL", 0)
    extra = counts.get("EXTRA", 0)
    drift = counts.get("DIFF", 0)
    nopy = counts.get("NOPY", 0)
    print(f"\n规则覆盖率: {eq}/{total} 等价 (预算 {BUDGET})"
          f"  (缺口 {counts.get('MISSING', 0)}"
          f" / 假阳性 {extra} / 口径漂移 {drift} / 探针失效 {nopy})")
    if gaps and not gaps_only:
        print("  待补:")
        for rid, det in gaps:
            print(f"    - {rid}: {det}")
    # 单条调试模式: 这一条不是 EQUAL 就算失败 (与预算无关)
    if only is not None:
        return 0 if eq == 1 else 1
    bad: list[str] = []
    if extra or drift or nopy:
        bad.append(f"假阳性 {extra} / 口径漂移 {drift} / 探针失效 {nopy} (必须为 0)")
    if eq < BUDGET:
        bad.append(f"等价规则 {eq} < 预算 {BUDGET} (回退; 补完一批才把 BUDGET 往上调)")
    if bad:
        for b in bad:
            print(f"[FAIL] {b}")
        return 1
    print(f"[OK] 达到预算 {eq}/{BUDGET} 且无假阳性/漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
