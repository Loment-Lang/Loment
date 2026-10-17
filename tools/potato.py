#!/usr/bin/env python3
# potato.py — Potato 形式对象校验器 (docs/142 v0, docs/147 v1)
#
# 判据 (docs/140 §9.4): 给定一个形式对象, 独立校验器仅凭它即可判定
# 能力域 / 契约 / 布局是否合法 —— 无需读源码, 无需读二进制。
# M47: 本文件不得 import 编译器 (lomentc) —— 独立性由 tools/potato_test.py 断言。
#
# 用法:
#   python tools/potato.py validate obj.json [obj2.json ...]
#   python tools/potato.py show obj.json
#
# 退出码: 0 = 全部合法 / 1 = 有非法项 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

INT_TYPES = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
TYPES = INT_TYPES + ("bool", "str", "ptr", "()")
# 编译器内建 trait (不要求在源中声明): Drop = RAII 析构 (M16)
BUILTIN_TRAITS = {"Drop": {"drop"}}
WIDTH = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i8": 1, "i16": 2, "i32": 4, "i64": 8}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: **边界内建** (`docs/205` R5): 调用它们, 就是**越过了语言的保证** —— 机器、裸地址。
#:
#: `unsafe` 在 Loment 里**不是一个关键字**（R5 的裁决因此是"抄意图, 形态要定"）,
#: 所以这条边界不是语法划的, 是**这张表**划的: "危险的那几个, 名字都在这里"。
#: 于是它可以被 **grep、被计数、被审计** —— 这正是 `docs/204` R5 那一格要的东西
#: （"一个工程可以规定 unsafe 只许出现在这三个文件"）。
#:
#: 两族在这里（**词法**上数调用点, 不看类型）:
#:   * 机调用 —— `syscall4` / `syscall6`
#:   * 裸指针变换 —— `ptr_add` / `ptr_sub` / `str_ptr`
#:
#: 第三族 `extern fn` 的**调用点**不在这张表里: 它不是内建, 是**本单元自己声明的**
#: 外部名字（`docs/173`）, 所以只能按名数, 数法见 `tools/loment_cli_test.py`。
#:
#: **它必须是编译器内建表的一个子集** —— 编译器那一侧有一条判据钉这个
#: （写在这里的下一段话解释了为什么不把它的文件名写出来）。
#: 单独放在这里而不放进编译器: 这个文件按 M47 **不许 import 编译器**
#: （独立性就是它存在的理由）, 而"哪些内建越界"恰恰是审计要用的那份清单。
#:
#: ⚠ **本文件里连"编译器那个模块的名字"都不能出现**：`potato_test` 的独立性判据
#: （M47）是对**这份源码做子串搜索**的, 而它要找的那个模块名恰好就是那两个字 ——
#: 2026-09-23 实测: 这段注释原来写了那个名字, 判据当场红。所以这里一律只写"编译器"。
BOUNDARY_BUILTINS = ("syscall4", "syscall6", "syscall7", "ptr_add", "ptr_sub", "str_ptr")
# v0 顶层字段; v1 = v0 + 泛型/实例/trait/impl (M45); v2 = v1 + 项目模式 (docs/143 §3.2);
# v3 = v2 + **开关取值** (docs/182 §1);
# v4 = v3 + **方言** (docs/184 §9 S4.3): 这份单元用了 `comefor` 定义的那些语法；
# v5 = v4 + **外部代码块** (docs/185 §7 ①): 这份单元里嵌了别语言的正文。
# v4 本来在 §8.3 里是留给 S1 的, 被 `comefor` 先用掉了, 所以 S1 顺延到 v5。
# 与 mode/switches 同一条纪律 —— **必填、可为空数组**, 不存在"缺这项"的形态。
TOP_KEYS_V0 = {"potato", "unit", "language", "imports", "capabilities", "functions", "layouts",
               "consts", "enums", "types", "excluded"}
TOP_KEYS_V1 = TOP_KEYS_V0 | {"traits", "impls", "generics", "instances", "guards"}
TOP_KEYS_V2 = TOP_KEYS_V1 | {"mode"}
TOP_KEYS_V3 = TOP_KEYS_V2 | {"switches"}
TOP_KEYS_V4 = TOP_KEYS_V3 | {"dialects"}
TOP_KEYS_V5 = TOP_KEYS_V4 | {"bodies"}
# v6 = v5 + **表层语法声明** (`docs/188`): 这份单元是用哪种写法写的。
# **与 `mode` 同级同形**（一个字符串取值）—— 用户 2026-09-18 定：它是**硬写法**，
# 与 `std`/`no_std` 同级，而且**出厂锁**（取值表官方给、不可删，将来可哈希钉死）。
# 与 `language` 的分工：`language` 说"这份东西**是**什么"（用别的写法写的，**它仍然是
# `loment`**），`grammar` 说"用什么**写法**写的"。
TOP_KEYS_V6 = TOP_KEYS_V5 | {"grammar"}
# v7 = v6 + **边界操作计数** `boundary` (`docs/205` R5): 一份单元里"越过语言保证的
# 那些调用点"有几个。**与 `guards` 同级同形** —— 一个自描述的对象, 理由也一样:
# 审计要能**不读源码**就回答"这个单元的信任边界有多大"。
# `unsafe` 在 Loment 里不是关键字, 所以这条边界由 `BOUNDARY_BUILTINS` 那张表划。
TOP_KEYS_V7 = TOP_KEYS_V6 | {"boundary"}
# v8 = v7 + **回收档** `gc` (`docs/175` §3.4): `gc_manual` / `gc_auto`。
# **与 `mode` 同级同形** —— 一个字符串取值、**必填**、只有根单元能定。于是
# "这个产物是在哪一档下编的"（以及它**有没有放弃确定性**）是**不读源码可判**的 ——
# 这正是 `docs/175` §3.4 要的那一条。
TOP_KEYS_V8 = TOP_KEYS_V7 | {"gc"}
# v9 = v8 + **运行期** `runtime` (`docs/175` §3.6): `runtime` / `no_runtime`。
# 取值**刻意只说"有没有"**，不说"里面装了什么" —— 装的东西会随年份长，钉进值名里
# 就等于两年后加一项能力要回头改这一维的定义。它今天装的就是 `gc_auto` 要的运行期。
TOP_KEYS_V9 = TOP_KEYS_V8 | {"runtime"}
# v10 = v9 + **GC 的组成** `gc_ladder` (`docs/210` §3/§5): 四层梯各领走几个 `alloc` 站点。
# **与 `boundary` 同级同形** —— 一个自描述的对象 + 一条自洽的和; 理由也一样: 审计/读者
# 要能**不读源码**就回答"这份程序的 GC 由哪几层组成"（`docs/210` §3 那句的头条主张）。
TOP_KEYS_V10 = TOP_KEYS_V9 | {"gc_ladder"}
# v11 = v10 + **对外端口** `port` (`docs/222` §4): `sealed` / `hosted`。
# 与 `runtime` 同形、同一条纪律: 取值**只说"通不通着世界"**, 不说"链的是哪个生态的库"
# （那会随年份长）。它是 `docs/219` 那张"世界端口表"在**产物里的那一格** ——
# 于是"这份产物允不允许有对外端口"是**不读源码可判**的。
TOP_KEYS_V11 = TOP_KEYS_V10 | {"port"}
# v12 = v11 + **面的组成** `surfaces` (`docs/224`): 机调用站点按"面"分一分
# (`net`/`file`/`mem`/`proc`/`other`)。**与 `gc_ladder` 同形**，但多一条**交叉**不变量：
# `total_sites` 必须等于 `boundary.syscalls` —— 每个机调用站点都落在**恰好一个**面里，
# 所以这两处数的是同一批东西，校验器判得了它们自相矛盾。
#
# **为什么是 v12 而不是 v11**：`surfaces` 与 `port` 两条线各自把"v11"用掉了（同一天、
# 两份不同的 `docs/222`）。`port` 先并进 main，所以它占住 v11，这一项顺延 ——
# 台阶是**只往上加**的，先到的那一版不会被后来的重编号（那会让已发出的对象全变非法）。
TOP_KEYS_V12 = TOP_KEYS_V11 | {"surfaces"}
# v13 = v12 + **执行模型** `vm` (`docs/231`): `vm` / `no_vm`。
# **与 `mode`/`gc`/`runtime`/`port` 同级同形** —— 一个字符串取值、**必填**、只有根单元能定。
# 于是"这份产物是给 VM 的还是原生的"是**不读源码可判**的（`docs/229` §4.1 那处空白的第一格）。
# ⚠ 这一档今天**只在开发通道**编得出来（渠道闸，`docs/231` §2）：发行版里 `choose vm` 是编译
# 错误，所以**发行版发的对象里 `vm` 恒为 `no_vm`** —— 校验器判不了"渠道"，但读得出这个值。
TOP_KEYS_V13 = TOP_KEYS_V12 | {"vm"}
# v14 = v13 + **联网站点的宽度组成** `net_ladder` (`docs/237`): 四档各领走几个联网站点。
# **与 `gc_ladder` 同形同一条纪律**（四个非负整数 + 一个总数），**多一条交叉**不变量：
# `total_net` 必须等于 `surfaces.net` —— 两处数的是同一批站点，只是分的粗细不同。
# 于是"这份程序要多少联网宽度"里的**组成**是**不读源码可判**的 ——
# `docs/218` §7 结尾那句"先让它可见且可判，再谈让它随证据变宽"的第一步。
TOP_KEYS_V14 = TOP_KEYS_V13 | {"net_ladder"}
# v15 = v14 + **内存安全** `lms` (`docs/253`，用户 2026-10-15 定第六维): `lms_close` / `lms_open`。
# **与 `mode`/`gc`/`runtime`/`port`/`vm` 同级同形** —— 一个字符串取值、**必填**、只有根单元
# 能定。于是"这份产物开没开内存安全"是**不读源码可判**的 —— 而这一维与 `gc` **正交**：
# `gc` 管"谁归还"（生命周期的内向可达），这一维管"谁能碰"（许可的外向）；
# `docs/210` §6.2 那句"箭头反了"说的就是这两条是同一张图上的相反方向。
# ⚠ **它不是一条"性能档"**：`close`（默认）与今天的形状逐字节相同；`open` 才动手。
# **`safety_ladder`（§7 的产物字段）不在这一版** —— 它是另一个字段，按 v10 的 `gc_ladder`
# 先例应当**单独升一版**，两处挤一版会让 v14 那些单元一起必填（`docs/147` 的台阶累积）。
TOP_KEYS_V15 = TOP_KEYS_V14 | {"lms"}
VERSIONS = ("v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11",
            "v12", "v13", "v14", "v15")
#: `boundary` 的四个**分量**键。**口径是词法的**（`docs/204` R5: "可 grep、可计数、
#: 可审计"）: 只看"这个名字被调用了没有", 不判类型、不判危险 —— 那是编译器的事。
BOUNDARY_KEYS = ("extern_declared", "extern_calls", "syscalls", "ptr_transforms")
#: `gc_ladder` 的四个**分量**键 = 四层梯的名字（`docs/210` §2）。**自洽的那条**：
#: `l0 + l1 + l2 + l3` 必须等于 `total_sites`。校验器读不到源码, 所以判不了"数得对不对",
#: 但判得了"它们自相矛盾"。
GC_LADDER_KEYS = ("l0", "l1", "l2", "l3")
#: `net_ladder` 的四个**分量**键 = 四档宽度，**从窄到宽**（`docs/237`）。**自洽的那条**：
#: `w0 + w1 + w2 + w3` 必须等于 `total_net`；**交叉的那条**：`total_net` 必须等于
#: `surfaces.net`。校验器判不了"档分得对不对"（那是策略），但判得了"两处自相矛盾"。
NET_LADDER_KEYS = ("w0", "w1", "w2", "w3")
#: `mode` 的取值 = `choose` 的两个模式名 (docs/143 §3.2)。**只有这两个** ——
#: 拼错的模式名在编译器那边是 E022, 在对象里就是这里报错。
MODES = ("std", "no_std")
#: `gc` 的取值 = 两个**回收档**（`docs/175` §3.4）。**只有这两个** ——
#: 拼错的档名在编译器那边是 E022，在对象里就是这里报错。
#:
#: `gc_manual` = 回收由程序显式做；`gc_auto` = 运行期负责回收。
#: 后者**放弃了"确定性"这条差异点**（`docs/140`），而这件事**记在对象里** ——
#: 于是"这个产物放弃了确定性"是**不读源码可判**的。
GCS = ("gc_manual", "gc_auto", "gc_auto_alpha")
#: `runtime` 的取值 = **产物里有没有运行期**（`docs/175` §3.6）。只有这两个 ——
#: 它是一个**存在开关**，不是"运行期里装了什么清单"（那个会随年份长）。
RUNTIMES = ("runtime", "no_runtime")
#: `port` 的取值 = **产物通不通着世界**（`docs/222` §4）。只有这两个 ——
#: `sealed`（封闭，默认）= 今天的形状，到世界没有端口；`hosted`（对外）= 允许链真 libc /
#: 真共享库。与 `RUNTIMES` 同一条纪律：**不带生态名**。
PORTS = ("sealed", "hosted")
#: `vm` 的取值 = **要哪种执行模型**（`docs/231`）。只有这两个 ——
#: 与 `RUNTIMES` / `PORTS` 同一条纪律：**取值只说"有没有"**，不说"是哪一档"
#: （T0 解释 / T1 反馈向量 / … 那梯子会随年份长，钉进值名里就等于把今天的内容钉成身份）。
#: ⚠ 发行版发的对象里它**恒为** `no_vm`（渠道闸）；校验器判不了渠道，读得出值。
VMS = ("vm", "no_vm")
#: `lms` 的取值 = **要不要内存安全**（`docs/253`，用户 2026-10-15 定第六维）。
#: `lms_close`（关，**默认**）= 今天的形状；`lms_open`（开）= 检查器发 E024–E026 +
#: 保障器**按站点分配**发内联界检查。
#: 与 `RUNTIMES` / `PORTS` / `VMS` 同一条纪律：**取值只说"有没有"**，不说"里面装了什么"
#: （今天是边界检查，明天可能是派生指针追踪 —— 装的东西不进值名）；
#: **但带维名前缀**（照 `GC_WORDS` 那一族的 `gc_manual`/`gc_auto` 先例）。
#: **它不引入运行期组件**，所以与 `no_runtime` **不冲突**（`docs/253` §11.2）。
LMSS = ("lms_close", "lms_open")
#: **核心模式的全部取值**（`docs/182` §1.3 的"核心语法硬写法"）：各维的取值并起来。
#:
#: 放在这里是因为**两个模块都要它**：编译器用它判"这一条 `choose` 是核心模式还是开关"，
#: `potato_from.front_door` 用它把**核心 `choose` 行从表层语法里摘出来**（`docs/188` §0：
#: 一份 Python/C 写法的单元**仍然是 Loment 程序**，语义是 Loment 的 —— 所以那几行不能
#: 交给各家翻译器吃掉）。两处各写一份必然漂，判据钉的是"两份相等"。
#:
#: ⚠ **加一维就要动这一行**：`port`（`docs/222`）进来时这里少了它 —— 合并当场撞出来。
#: 少一个词的后果是**静默的**：那道维度在表层语法里会被当成"未定义的开关"。
#:
#: ⚠ **`vm`（`docs/231`）是第二次撞同一处** —— 而且自举侧那份手抄的
#: （`loment/selfhost/switches.lomt` 的 `sw_is_core`）当时**真的漏了**，症状正是上面那句：
#: `choose vm` 报 **E022「未定义的开关」**（码对、理由错），而 `loment_rule_parity` 只比
#: **码集**所以判绿。⇒ **加第三维的人：这里与 `sw_is_core` 两处都要动。**
#:
#: ⚠ **`lms`（`docs/253`）是第三次** —— 同上，这一行与 `sw_is_core` **两处都要动**，
#: 而漏掉是**静默**的（`choose lms` 会报成"未定义的开关"，码是 E022、码集不变）。
CORE_WORDS = (frozenset(MODES) | frozenset(GCS) | frozenset(RUNTIMES)
              | frozenset(PORTS) | frozenset(VMS) | frozenset(LMSS))
#: 函数级的**可选** `abi` (docs/179 §2)。取值 = 源语言那一侧的调用约定:
#:   `c`      = 平台 C ABI (System V / Win64) —— 可以发成 L1 的 `extern fn` (docs/173 §2)
#:   其余     = 不是平台 C ABI, **不能**发 `extern fn`; 要调它得走别的路 (进程桥等)
#:
#: 加一个源语言就要在这里加一个名字 (`go` / `java`) —— **它不只是"记个名字"**:
#: 每一种都是在说"这一族的函数不能用 C ABI 调", 而 `lomt_from` 据此把它们挡在产物外。
ABIS = ("c", "rust", "python", "go", "java")
#: `grammar` 的取值 = **表层语法**的规范名（`docs/188`）。用户 2026-09-18 定：
#: 这是**出厂锁**的官方表 —— **不可删、用户不能加，将来可以哈希把它钉死**。
#:
#: **与 `ABIS` 不是一回事，别混**：`ABIS` 说的是"这个函数用哪种调用约定"
#: （只在**真的是外国**的时候才有意义）；`grammar` 说的是"这份单元用哪种**写法**写的"。
#: 用别的写法写的单元**仍然是 Loment**（`language: "loment"`）。
#:
#: 源里可以写别名（`py` / `python`），但**进对象的只有这里这几个规范名** ——
#: 否则同一份源出两串字节，判据当场红。
#: `natural` = **Loment 自己的自然语言写法**（`docs/197`）。它与前六门**不同类** ——
#: 那六门是别人已有的语言，这一门是**我们自己发明的**，所以子集线是**画出来的**，
#: 不是**碰上的**。之所以仍记在这里，是因为它守的规矩一模一样：
#: **只让拼法与形状，不让语义**（`docs/188` §0）。
GRAMMARS = ("loment", "c", "python", "java", "go", "rust", "csharp", "cpp",
            "natural")


def _is_array_type(t: object) -> bool:
    return isinstance(t, str) and t.startswith("[") and t.endswith("]") and ";" in t


def _is_slice_type(t: object) -> bool:
    """切片 [T] / 可变切片 mut [T] (M3/M4)。"""
    if not isinstance(t, str):
        return False
    body = t[4:] if t.startswith("mut ") else t
    return body.startswith("[") and body.endswith("]") and ";" not in body


def _slice_elem(t: str) -> str:
    body = t[4:] if t.startswith("mut ") else t
    return body[1:-1].strip()


def _type_ok(t: object, known: set[str]) -> bool:
    """基类型 / 已声明 struct / 数组 [T; N] / 切片 [T] / 可变切片 mut [T]。"""
    if not isinstance(t, str):
        return False
    if t in known:
        return True
    if _is_slice_type(t):
        return _type_ok(_slice_elem(t), known)
    if _is_array_type(t):
        try:
            elem = t[1:t.rindex(";")].strip()
            n = int(t[t.rindex(";") + 1:-1].strip())
        except ValueError:
            return False
        return n > 0 and _type_ok(elem, known)
    return False


def _req(obj: dict, key: str, where: str, errs: list[str]):
    if key not in obj:
        errs.append(f"{where}: 缺字段 {key}")
        return None
    return obj[key]


def validate(doc: object) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["根节点必须是对象"]
    ver = doc.get("potato")
    if ver not in VERSIONS:
        errs.append(f"potato 版本必须是 {VERSIONS} 之一，得到 {ver!r}")
        ver = "v0"
    top = {"v0": TOP_KEYS_V0, "v1": TOP_KEYS_V1, "v2": TOP_KEYS_V2, "v3": TOP_KEYS_V3,
           "v4": TOP_KEYS_V4, "v5": TOP_KEYS_V5, "v6": TOP_KEYS_V6, "v7": TOP_KEYS_V7,
           "v8": TOP_KEYS_V8, "v9": TOP_KEYS_V9, "v10": TOP_KEYS_V10,
           "v11": TOP_KEYS_V11, "v12": TOP_KEYS_V12, "v13": TOP_KEYS_V13,
           "v14": TOP_KEYS_V14, "v15": TOP_KEYS_V15}[ver]
    for k in doc:
        if k not in top:
            errs.append(f"未知顶层字段 {k!r}（{ver} 不允许扩展字段）")
    if ver in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        for k in ("traits", "impls", "generics", "instances"):
            if not isinstance(doc.get(k), list):
                errs.append(f"{ver}: 缺字段 {k}（必须是数组，可为空）")
        g = doc.get("guards")
        if not isinstance(g, int) or isinstance(g, bool) or g < 0:
            errs.append(f"{ver}: guards 必须是非负整数，得到 {g!r}")
    if ver in ("v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填** (docs/175 §8 的判据: 从对象里删掉该字段, 校验器必须红)。这就是
        # 必须升版本而不是往旧版里加字段的原因 —— 要求必填会让既有的旧版对象全变非法,
        # 而旧版是**承诺过能回放**的 (docs/147 §5, 冻结样本 demo.v0.json 一直在跑)。
        m = doc.get("mode")
        if m not in MODES:
            errs.append(f"{ver}: mode 必须是 {MODES} 之一，得到 {m!r}")
    if ver in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填, 可为空数组** (docs/182 §1)。与 `mode` 同一条纪律: 不存在"缺这项"的形态,
        # 所以"这份单元是在什么开关状态下编的"是**可回放**的。
        # 用户 2026-09-17: **"开关的取值是要进 Potato 的"**。
        sw = doc.get("switches")
        if not isinstance(sw, list):
            errs.append(f"{ver}: switches 必须是数组（可为空），得到 {sw!r}")
        else:
            seen_s: set[str] = set()
            for j, s in enumerate(sw):
                w = f"switches[{j}]"
                if not isinstance(s, dict):
                    errs.append(f"{w}: 必须是对象")
                    continue
                nm = s.get("name")
                if not isinstance(nm, str) or not IDENT_RE.match(nm):
                    errs.append(f"{w}.name 非法: {nm!r}")
                elif nm in seen_s:
                    errs.append(f"{w}.name 重复: {nm}")
                else:
                    seen_s.add(nm)
                if not isinstance(s.get("on"), bool):
                    errs.append(f"{w}.on 必须是布尔（开关**只能**是开或关，没有第三态）")
    if ver in ("v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填, 可为空数组**（`docs/184` §9 S4.3）。同 `mode`/`switches` 那条纪律：
        # 不存在"缺这项"的形态 —— 于是"这份产物用了哪些自定义语法"是**可回放**的。
        #
        # **为什么带上 `body`**：只记一个名字的话，读产物的人知道"用了方言 `def`"却
        # 不知道 `def` 是什么（`docs/184` §7 ②）—— 那正是"产物不自解释"。`body` 就是
        # 定义处那段程序的**源文本**，于是这一条是可重建的，不是一条死记的名字。
        dl = doc.get("dialects")
        if not isinstance(dl, list):
            errs.append(f"v4: dialects 必须是数组（可为空），得到 {dl!r}")
        else:
            seen_d: set[str] = set()
            for j, d in enumerate(dl):
                w = f"dialects[{j}]"
                if not isinstance(d, dict):
                    errs.append(f"{w}: 必须是对象")
                    continue
                nm = d.get("name")
                if not isinstance(nm, str) or not IDENT_RE.match(nm):
                    errs.append(f"{w}.name 非法: {nm!r}")
                elif nm in seen_d:
                    errs.append(f"{w}.name 重复: {nm}")
                else:
                    seen_d.add(nm)
                if not isinstance(d.get("body"), str):
                    errs.append(f"{w}.body 必须是字符串（定义处那段程序的源文本）")
    if ver in ("v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填, 可为空数组** —— 同 `mode`/`switches`/`dialects` 那条纪律。
        #
        # **按源里的顺序, 不按名字排**: 与 `dialects` 不同 —— 方言是个**集合**（名字唯一），
        # 而外部代码块是个**序列**（先后的正文可能要拼起来，顺序是有意义的）。
        dl = doc.get("bodies")
        if not isinstance(dl, list):
            errs.append(f"v5: bodies 必须是数组（可为空），得到 {dl!r}")
        else:
            for j, d in enumerate(dl):
                w = f"bodies[{j}]"
                if not isinstance(d, dict):
                    errs.append(f"{w}: 必须是对象")
                    continue
                nm = d.get("lang")
                if not isinstance(nm, str) or not IDENT_RE.match(nm):
                    errs.append(f"{w}.lang 非法: {nm!r}")
                if not isinstance(d.get("body"), str):
                    errs.append(f"{w}.body 必须是字符串（块里的原始正文）")
    if ver in ("v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/188` §2）—— 与 `mode` 同一条纪律：不存在"缺这项"的形态。
        #
        # **为什么它必须必填**：`docs/179` §3.1 说**可选**的前提是"不认识它的消费者
        # **忽略它就是对的**"。这里**不成立** —— 忽略它的人会把"用 Python 写法写的
        # Loment"当成 Loment 直接读，那是**误判**。所以它升了版本（v6）。
        #
        # **取值是出厂锁的官方表**（用户 2026-09-18）：不可删、将来可哈希钉死。
        # 用户**不能**往这里加值。加一门语法 = 改 `GRAMMARS` = 两个实现一起改 + 升版本。
        g = doc.get("grammar")
        if g not in GRAMMARS:
            errs.append(f"v6: grammar 必须是 {GRAMMARS} 之一，得到 {g!r}"
                        f"（**写法名**，不是语言名；见 docs/188）")
    if ver in ("v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/205` R5）—— 与 `mode` / `guards` 同一条纪律：不存在"缺这项"的形态。
        #
        # **为什么不是可选的**：`boundary` 的全部价值在"可 grep、可计数、可审计"。
        # 一个**缺席**的计数与一个"零"在读者眼里长得一样, 而那是两件事 ——
        # 缺席会被读成"这个单元很干净"。必填让那件事不可能发生。
        b = doc.get("boundary")
        if not isinstance(b, dict):
            errs.append(f"v7: boundary 必须是对象（五个计数），得到 {b!r}")
        else:
            for k in BOUNDARY_KEYS:
                v = b.get(k)
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errs.append(f"boundary.{k} 必须是非负整数，得到 {v!r}")
            tot = b.get("total_sites")
            if not isinstance(tot, int) or isinstance(tot, bool) or tot < 0:
                errs.append(f"boundary.total_sites 必须是非负整数，得到 {tot!r}")
            # **只在四个分量都是合法值时**才去对和 —— 分量本身不合法时上面已经报过一次,
            # 再报一次"和也对不上"是**级联**, 不是信息。孪生那份按同样的口径
            # （`bnd_part` 不合法就返回 `NOTFOUND`, 和数那一步直接跳过）——
            # 两份的**错误条数**必须一致, `potato_test` 的孪生判据比的正是条数。
            elif all(isinstance(b.get(k), int) and not isinstance(b.get(k), bool)
                     and b.get(k) >= 0 for k in BOUNDARY_KEYS):
                # **自洽**: 总数就是那三项之和。这是**校验器独立判得了**的那部分 ——
                # 它读不到源码, 所以判不了"这几个数数得对不对", 但判得了"它们自相矛盾"。
                want = b["extern_calls"] + b["syscalls"] + b["ptr_transforms"]
                if tot != want:
                    errs.append(f"boundary.total_sites 是 {tot}, 但三项之和是 {want}"
                                "（总数必须等于 extern_calls + syscalls + ptr_transforms）")
            extra = sorted(set(b) - set(BOUNDARY_KEYS) - {"total_sites"})
            if extra:
                errs.append(f"boundary 里有不认识的键: {extra}")

    if ver in ("v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/175` §3.4）—— 与 `mode` 同一条纪律：不存在"缺这项"的形态。
        # 于是"这个产物是哪一档"（以及它有没有放弃确定性）是**可回放**的。
        # **台阶是累积的**：v9/v10 也要验 v8 的 `gc`（原先这里写的是 `== "v8"`，
        # 于是 v9 起就不再验 `gc` —— 与自举侧校验器 `ver >= 8` 的口径**不一致**，
        # 2026-10-01 顺手对齐）。
        g = doc.get("gc")
        if g not in GCS:
            errs.append(f"{ver}: gc 必须是 {GCS} 之一，得到 {g!r}")
        # **两档不能同时选**（`docs/175` §3.4 ⚠）：这一条校验器**独立判得了** ——
        # 两个取值都在对象里, 不需要读源码。于是"自称既 no_std 又 gc_auto"是一件
        # **能被外部判非法**的事, 而不是"只有编译器知道"。
        if doc.get("mode") == "no_std" and g in ("gc_auto", "gc_auto_alpha"):
            errs.append("mode=no_std 与 gc=gc_auto 不能同时选 —— 自动回收要一个运行期，"
                        "而 no_std 的定义是「只能用核那一层」（docs/175 §3.4）")

    if ver in ("v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/175` §3.6）—— 与 `mode` / `gc` 同一条纪律：不存在"缺这项"的形态。
        # 于是"这个产物里有没有运行期"是**不读源码可判**的。
        # 台阶累积：v10 也要验 v9 的 `runtime`（同 `gc` 那一条）。
        rt = doc.get("runtime")
        if rt not in RUNTIMES:
            errs.append(f"{ver}: runtime 必须是 {RUNTIMES} 之一，得到 {rt!r}")
        # **两条冲突，校验器独立判得了**（两个取值都在对象里，不需要读源码）：
        # 自动回收要的就是那个运行期，与 `no_runtime` 定义上矛盾。
        # 注意 `gc_manual` **不冲突** —— "要运行期、但内存我自己管"是合法档。
        if rt == "no_runtime" and doc.get("gc") in ("gc_auto", "gc_auto_alpha"):
            errs.append("runtime=no_runtime 与 gc=gc_auto 不能同时选 —— 自动回收要的就是"
                        "那个运行期，两者定义上矛盾（docs/175 §3.6）")

    # 累积：v11/v12 也要验 v10 的 `gc_ladder`（与孪生侧 `ver >= 10` 对齐 —— 同 v8→v9 那次）
    if ver in ("v10", "v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/210` §5 第一条）—— 与 `boundary` / `guards` 同一条纪律：
        # 不存在"缺这项"的形态。`gc_ladder` 的全部价值在"**GC 的组成**不读源码可判"，
        # 而一个**缺席**的组成会被读成"这份程序没有 GC"，那是误判。
        #
        # 与 `boundary` 同形：四个分量 + 一个 `total_sites`，而**四数之和必须等于
        # `total_sites`**（`docs/210` §5 第一行那条**自洽**判据）。校验器读不到源码，
        # 所以判不了"这几个数数得对不对"，但判得了"它们自相矛盾"。
        gl = doc.get("gc_ladder")
        if not isinstance(gl, dict):
            errs.append(f"v10: gc_ladder 必须是对象（四个分层计数 + total_sites），得到 {gl!r}")
        else:
            for k in GC_LADDER_KEYS:
                v = gl.get(k)
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errs.append(f"gc_ladder.{k} 必须是非负整数，得到 {v!r}")
            tot = gl.get("total_sites")
            if not isinstance(tot, int) or isinstance(tot, bool) or tot < 0:
                errs.append(f"gc_ladder.total_sites 必须是非负整数，得到 {tot!r}")
            # **只在四个分量都合法时**才对和 —— 与 `boundary` 同口径（分量本身不合法时
            # 上面已经报过，再报"和也对不上"是**级联**；两份校验器比的正是**错误条数**）。
            elif all(isinstance(gl.get(k), int) and not isinstance(gl.get(k), bool)
                     and gl.get(k) >= 0 for k in GC_LADDER_KEYS):
                want = sum(gl[k] for k in GC_LADDER_KEYS)
                if tot != want:
                    errs.append(f"gc_ladder.total_sites 是 {tot}, 但四项之和是 {want}"
                                "（总数必须等于 l0 + l1 + l2 + l3）")
            extra = sorted(set(gl) - set(GC_LADDER_KEYS) - {"total_sites"})
            if extra:
                errs.append(f"gc_ladder 里有不认识的键: {extra}")

    # 累积：v12 也要验 v11 的 `port`（`docs/222` §4）—— 与孪生侧 `ver >= 11` 对齐。
    if ver in ("v11", "v12", "v13", "v14", "v15"):
        # **必填**（`docs/222` §4）—— 与 `mode` / `gc` / `runtime` 同一条纪律：
        # 不存在"缺这项"的形态。于是"这份产物通不通着世界"是**不读源码可判**的，
        # 而不是"看源码里写没写 `choose hosted`"（`docs/219` §7：测量强于声明）。
        # 台阶累积：v11 也要验 v10 以下每一版的字段（上面那些 `if ver in (...)` 已经覆盖）。
        pt = doc.get("port")
        if pt not in PORTS:
            errs.append(f"{ver}: port 必须是 {PORTS} 之一，得到 {pt!r}")
        # **两条冲突，校验器独立判得了**（两个取值都在对象里，不需要读源码）——
        # 与 `runtime` 那一条同口径。`hosted` 与 `gc_manual` / `gc_auto` **不冲突**：
        # "要对外、但内存我自己管"是一条必须能表达的档。
        if pt == "hosted" and doc.get("mode") == "no_std":
            errs.append("port=hosted 与 mode=no_std 不能同时选 —— `no_std` 说「底下没有"
                        "东西」，`hosted` 说「往下链东西」（docs/222 §4.2）")
        if pt == "hosted" and doc.get("gc") == "gc_auto_alpha":
            errs.append("port=hosted 与 gc=gc_auto_alpha 不能同时选 —— 混合档的 L2 是"
                        "分配器前沿回卷，而外部库把指针放进它自己的结构里，那些指针在"
                        "Loment 的栈之外（docs/219 §6.1）")

    if ver in ("v12", "v13", "v14", "v15"):
        # **必填**（`docs/224`）—— 与 `boundary` / `gc_ladder` / `runtime` 同一条纪律。
        # `surfaces` 是**面名 → 站点数**（面名由用的人定：`net`/`file`/… 只是仓库自己的划法），
        # 外加一个保留键 `total_sites`。
        sf = doc.get("surfaces")
        if not isinstance(sf, dict):
            errs.append(f"v12: surfaces 必须是对象（面名 → 站点数 + total_sites），得到 {sf!r}")
        else:
            parts = {k: v for k, v in sf.items() if k != "total_sites"}
            for k in sorted(parts):
                v = parts[k]
                if not isinstance(k, str) or not IDENT_RE.match(k):
                    errs.append(f"surfaces 的键 {k!r} 不是标识符（面名你定，但得是个名字）")
                elif not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errs.append(f"surfaces.{k} 必须是非负整数，得到 {v!r}")
            tot = sf.get("total_sites")
            if not isinstance(tot, int) or isinstance(tot, bool) or tot < 0:
                errs.append(f"surfaces.total_sites 必须是非负整数，得到 {tot!r}")
            else:
                # 只在**分量都合法**时对和（与 `boundary` / `gc_ladder` 同口径：否则是级联，
                # 而两份校验器比的正是**错误条数**）。
                if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                       for v in parts.values()):
                    want = sum(parts.values())
                    if tot != want:
                        errs.append(f"surfaces.total_sites 是 {tot}, 但各面之和是 {want}"
                                    "（总数必须等于各面之和）")
                # **交叉不变量**：每个机调用站点落在恰好一个面里 ⇒ 这两处数的是同一批站点。
                # 校验器读不到源码，但判得了两处自相矛盾。
                bnd = doc.get("boundary")
                if isinstance(bnd, dict) and isinstance(bnd.get("syscalls"), int)                         and not isinstance(bnd.get("syscalls"), bool):
                    if tot != bnd["syscalls"]:
                        errs.append(f"surfaces.total_sites 是 {tot}, 但 boundary.syscalls 是"
                                    f" {bnd['syscalls']}（两者数的是同一批机调用站点）")

    if ver in ("v14",):
        # **必填**（`docs/237`）—— 与 `boundary` / `gc_ladder` / `surfaces` / `runtime` 同一条
        # 纪律。`net_ladder` 是**四档宽度 → 站点数** + 一个保留键 `total_net`；四档**恒在**
        # （哪怕为 0），键的集合固定，读者与校验器都不用猜。
        nl = doc.get("net_ladder")
        if not isinstance(nl, dict):
            errs.append(f"v14: net_ladder 必须是对象（四档宽度 → 站点数 + total_net），得到 {nl!r}")
        else:
            for k in NET_LADDER_KEYS:
                v = nl.get(k)
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errs.append(f"net_ladder.{k} 必须是非负整数，得到 {v!r}")
            tot = nl.get("total_net")
            if not isinstance(tot, int) or isinstance(tot, bool) or tot < 0:
                errs.append(f"net_ladder.total_net 必须是非负整数，得到 {tot!r}")
            else:
                # 只在**四档都合法**时对和（同 `gc_ladder`：否则是级联，而两份校验器比的
                # 正是**错误条数**）。
                if all(isinstance(nl.get(k), int) and not isinstance(nl.get(k), bool)
                       and nl.get(k) >= 0 for k in NET_LADDER_KEYS):
                    want = sum(nl[k] for k in NET_LADDER_KEYS)
                    if tot != want:
                        errs.append(f"net_ladder.total_net 是 {tot}, 但四档之和是 {want}"
                                    "（总数必须等于各档之和）")
                    # **交叉不变量**：每个联网站点落在一档里，而同一个站点在 `surfaces` 里
                    # 落在 `net` 面里 ⇒ 这两处数的是**同一批站点**。校验器读不到源码，
                    # 但判得了两处自相矛盾（少一档就是"有些联网站点没归类"，多一档就是
                    # "给不是联网的站点配了宽度"）。
                    sf = doc.get("surfaces")
                    if isinstance(sf, dict) and isinstance(sf.get("net", 0), int) \
                            and not isinstance(sf.get("net", 0), bool):
                        if tot != sf.get("net", 0):
                            errs.append(f"net_ladder.total_net 是 {tot}, 但 surfaces.net 是"
                                        f" {sf.get('net', 0)}（两者数的是同一批联网站点）")
            extra = sorted(set(nl) - set(NET_LADDER_KEYS) - {"total_net"})
            if extra:
                errs.append(f"net_ladder 里有不认识的键: {extra}")

    if ver in ("v13",):
        # **必填**（`docs/231`）—— 与 `mode` / `gc` / `runtime` / `port` 同一条纪律：
        # 不存在"缺这项"的形态。于是"这份产物是给 VM 的还是原生的"是**不读源码可判**的。
        # **这里没有冲突可判**：`vm` 与 `runtime` 今天确实合不来（VM 要用运行期），但那条
        # 由编译器那边 `docs/227` 的既有检查接住（"声明了 no_runtime 但它会让产物带上
        # 运行期"）—— 对象里判不了它，因为**"用不用得到运行期"要读源码**。
        # 少写一条判不了的冲突，比写一条判不准的强（`docs/188` 的"宁拒勿猜"）。
        vv = doc.get("vm")
        if vv not in VMS:
            errs.append(f"{ver}: vm 必须是 {VMS} 之一，得到 {vv!r}")
        # ⚠ **只能同向**：`vm` 那一档要一个引擎，而引擎今天还不存在（`docs/231` §1.3）。
        # 校验器判不了渠道，但判得了"这份对象自称是 VM 产物、而它的 vm 值不在表里"——
        # 上面那条已经覆盖。**这里刻意不多写**：把"引擎存不存在"写进对象是下一版的事。

    if "lms" in top:
        lm = doc.get("lms")
        if lm not in LMSS:
            errs.append(f"{ver}: lms 必须是 {LMSS} 之一，得到 {lm!r}")
        # ⚠ **刻意不写与 `no_runtime` 的冲突**（`docs/253` §11.2）：LMS 的四格落的都是
        # **指令**（一条 `cmp` / 一个 `llvm.trap`），**不发运行期组件** —— 所以
        # `lms open` + `no_runtime` 不矛盾。这里多写一条反而会把那条结论说反。

    unit = _req(doc, "unit", "根", errs)
    if unit is not None and (not isinstance(unit, str) or not IDENT_RE.match(unit)):
        errs.append(f"unit 必须是标识符，得到 {unit!r}")
    _req(doc, "language", "根", errs)

    # ---- 导入的编译单元名
    imports = _req(doc, "imports", "根", errs)
    if isinstance(imports, list):
        for i, x in enumerate(imports):
            if not isinstance(x, str) or not IDENT_RE.match(x):
                errs.append(f"imports[{i}]: 必须是标识符，得到 {x!r}")
    elif imports is not None:
        errs.append("imports 必须是字符串数组")

    # 先收集 L1 声明的类型名: 函数签名可以引用它们
    declared_types: set[str] = set()
    for key in ("types", "enums"):
        if isinstance(doc.get(key), list):
            for t in doc[key]:
                if isinstance(t, dict) and isinstance(t.get("name"), str):
                    declared_types.add(t["name"])
    allowed_types = set(TYPES) | declared_types

    # ---- 能力域
    caps = _req(doc, "capabilities", "根", errs)
    seen_cap: set[str] = set()
    if isinstance(caps, list):
        for i, c in enumerate(caps):
            w = f"capabilities[{i}]"
            if not isinstance(c, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = c.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_cap:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_cap.add(name)
            dom = c.get("domain")
            if not isinstance(dom, dict):
                errs.append(f"{w}.domain 必须是对象")
            else:
                space = dom.get("space")
                lo, hi = dom.get("lo"), dom.get("hi")
                if not isinstance(space, str) or not IDENT_RE.match(space):
                    errs.append(f"{w}.domain.space 非法: {space!r}")
                if not isinstance(lo, int) or not isinstance(hi, int):
                    errs.append(f"{w}.domain.lo/hi 必须是整数")
                elif lo < 0 or lo > hi:
                    errs.append(f"{w}.domain 区间非法: [{lo}..{hi}]")
            if not isinstance(c.get("revocable"), bool):
                errs.append(f"{w}.revocable 必须是布尔")
    elif caps is not None:
        errs.append("capabilities 必须是数组")

    # ---- 函数签名
    funcs = _req(doc, "functions", "根", errs)
    seen_fn: set[str] = set()
    if isinstance(funcs, list):
        for i, f in enumerate(funcs):
            w = f"functions[{i}]"
            if not isinstance(f, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = f.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_fn:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_fn.add(name)
            if not _type_ok(f.get("ret"), allowed_types):
                errs.append(f"{w}.ret 非法类型: {f.get('ret')!r}")
            params = f.get("params")
            if not isinstance(params, list):
                errs.append(f"{w}.params 必须是数组")
                continue
            seen_p: set[str] = set()
            for j, p in enumerate(params):
                pw = f"{w}.params[{j}]"
                if not isinstance(p, dict) or not _type_ok(p.get("type"), allowed_types):
                    errs.append(f"{pw}: 参数类型非法")
                    continue
                pn = p.get("name")
                if not isinstance(pn, str) or not IDENT_RE.match(pn):
                    errs.append(f"{pw}.name 非法: {pn!r}")
                elif pn in seen_p:
                    errs.append(f"{pw}.name 重复: {pn}")
                else:
                    seen_p.add(pn)
            # `abi` 是**可选**字段 (docs/179 §2): 外源模块的调用约定。省略 = 未声明。
            # **可选是刻意的** —— 必填会逼着升 v3 (docs/178 §1 那条理由), 而这一个字段
            # 不值得动契约版本: 不认识它的消费者忽略它就是对的 (它们本来也不判 ABI)。
            # 只有"声明了"才校验取值, 免得拼错一个 ABI 名一路静默到链接期。
            abi = f.get("abi")
            if abi is not None and abi not in ABIS:
                errs.append(f"{w}.abi 非法: {abi!r} (可选, 给了就必须是 {ABIS} 之一)")
            # `body` 是**可选**字段 (`docs/186`): 这个函数的**原文**（签名 + 体），
            # 语言就是本单元的 `language`。与 `abi` 同一条先例 —— 省略合法（声明单元
            # 里一个函数都没有正文，那是**常态**），给了就必须是个非空字符串。
            #
            # **为什么是整段函数原文、不是只存 `{…}`**：签名那边 Potato 记的是**映射后的**
            # 类型名（`i32`），从 `i32` 回推 C 的拼法是另一张表，而原文本来就在手边；
            # 更紧的是**保真** —— `unsigned` 与 `unsigned int` 在 Potato 里都是 `u32`。
            bd = f.get("body")
            if bd is not None and (not isinstance(bd, str) or not bd.strip()):
                errs.append(f"{w}.body 必须是非空字符串 (可选, 给了就是这段函数的原文)")
            # `body_line` 是**可选**字段 (`docs/186` §3)：`body` 那段原文在**源文件**里的
            # 起始行。与 `abi`/`body` 同一条先例 —— 省略合法（不认识它的消费者忽略它就是
            # 对的，它们本来也不报行号）。给了就必须是个 >=1 的整数，**且必须与 `body`
            # 配对**：一个"指不到任何正文的位置"不表示任何东西，留着只会让人以为有正文。
            ln = f.get("body_line")
            if ln is not None:
                if not isinstance(ln, int) or isinstance(ln, bool) or ln < 1:
                    errs.append(f"{w}.body_line 必须是 >=1 的整数 (可选, 与 body 配对): {ln!r}")
                elif bd is None:
                    errs.append(f"{w}.body_line 给了却没有 body —— 它指的是那段正文的位置")
    elif funcs is not None:
        errs.append("functions 必须是数组")

    # ---- 布局
    layouts = _req(doc, "layouts", "根", errs)
    seen_lay: set[str] = set()
    if isinstance(layouts, list):
        for i, r in enumerate(layouts):
            w = f"layouts[{i}]"
            if not isinstance(r, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = r.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_lay:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_lay.add(name)
            size = r.get("size")
            if not isinstance(size, int) or size <= 0:
                errs.append(f"{w}.size 必须是正整数，得到 {size!r}")
                size = 0
            if r.get("endian") not in ("little", "big"):
                errs.append(f"{w}.endian 必须是 little/big")
            if not isinstance(r.get("packed"), bool):
                errs.append(f"{w}.packed 必须是布尔")
            fields = r.get("fields")
            if not isinstance(fields, list):
                errs.append(f"{w}.fields 必须是数组")
                continue
            occ: list[tuple[int, int, str]] = []
            seen_f: set[str] = set()
            for j, fd in enumerate(fields):
                fw = f"{w}.fields[{j}]"
                if not isinstance(fd, dict):
                    errs.append(f"{fw}: 必须是对象")
                    continue
                fn = fd.get("name")
                ft = fd.get("type")
                off = fd.get("offset")
                if not isinstance(fn, str) or not IDENT_RE.match(fn):
                    errs.append(f"{fw}.name 非法: {fn!r}")
                elif fn in seen_f:
                    errs.append(f"{fw}.name 重复: {fn}")
                else:
                    seen_f.add(fn)
                if ft not in WIDTH:
                    errs.append(f"{fw}.type 非法或非定宽: {ft!r}")
                    continue
                if not isinstance(off, int) or off < 0:
                    errs.append(f"{fw}.offset 必须是非负整数")
                    continue
                if off + WIDTH[ft] > size:
                    errs.append(f"{fw} 越界: @{off}+{WIDTH[ft]} > size={size}")
                occ.append((off, off + WIDTH[ft], str(fn)))
            occ.sort()
            for (o1, e1, n1), (o2, e2, n2) in zip(occ, occ[1:]):
                if o2 < e1:
                    errs.append(f"{w} 字段 {n1}@{o1}..{e1} 与 {n2}@{o2}..{e2} 重叠")
    elif layouts is not None:
        errs.append("layouts 必须是数组")

    # ---- 枚举 (无载荷变体)
    enums = _req(doc, "enums", "根", errs)
    seen_en: set[str] = set()
    if isinstance(enums, list):
        for i, e in enumerate(enums):
            w = f"enums[{i}]"
            if not isinstance(e, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = e.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_en:
                errs.append(f"{w}.name 重复: {name}")
            elif name in TYPES:
                errs.append(f"{w}.name 与基类型同名: {name}")
            else:
                seen_en.add(name)
            vs = e.get("variants")
            if not isinstance(vs, list) or not vs:
                errs.append(f"{w}.variants 必须是非空数组")
                continue
            seen_v: set[str] = set()
            for j, v in enumerate(vs):
                if not isinstance(v, str) or not IDENT_RE.match(v):
                    errs.append(f"{w}.variants[{j}] 非法: {v!r}")
                elif v in seen_v:
                    errs.append(f"{w}.variants[{j}] 重复: {v}")
                else:
                    seen_v.add(v)
            pl = e.get("payloads")
            if pl is not None:
                if not isinstance(pl, dict):
                    errs.append(f"{w}.payloads 必须是对象")
                else:
                    for vn, pt in pl.items():
                        if vn not in seen_v:
                            errs.append(f"{w}.payloads 键 {vn!r} 不是已声明变体")
                        if not _type_ok(pt, allowed_types):
                            errs.append(f"{w}.payloads[{vn}] 类型未声明: {pt!r}")
    elif enums is not None:
        errs.append("enums 必须是数组")

    # ---- 常量 (仅整型)
    consts = _req(doc, "consts", "根", errs)
    seen_c: set[str] = set()
    if isinstance(consts, list):
        for i, c in enumerate(consts):
            w = f"consts[{i}]"
            if not isinstance(c, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = c.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_c:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_c.add(name)
            if c.get("type") not in INT_TYPES:
                errs.append(f"{w}.type 必须是整型: {c.get('type')!r}")
            if not isinstance(c.get("value"), int):
                errs.append(f"{w}.value 必须是整数")
    elif consts is not None:
        errs.append("consts 必须是数组")

    # ---- L1 原生类型 (struct)
    types = _req(doc, "types", "根", errs)
    seen_ty: set[str] = set()
    if isinstance(types, list):
        for i, t in enumerate(types):
            w = f"types[{i}]"
            if not isinstance(t, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = t.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_ty:
                errs.append(f"{w}.name 重复: {name}")
            elif name in TYPES:
                errs.append(f"{w}.name 与基类型同名: {name}")
            else:
                seen_ty.add(name)
            fields = t.get("fields")
            if not isinstance(fields, list) or not fields:
                errs.append(f"{w}.fields 必须是非空数组")
                continue
            seen_f: set[str] = set()
            for j, fd in enumerate(fields):
                fw = f"{w}.fields[{j}]"
                if not isinstance(fd, dict):
                    errs.append(f"{fw}: 必须是对象")
                    continue
                fn = fd.get("name")
                if not isinstance(fn, str) or not IDENT_RE.match(fn):
                    errs.append(f"{fw}.name 非法: {fn!r}")
                elif fn in seen_f:
                    errs.append(f"{fw}.name 重复: {fn}")
                else:
                    seen_f.add(fn)
                ft = fd.get("type")
                if not _type_ok(ft, allowed_types):
                    errs.append(f"{fw}.type 非法或未声明: {ft!r}")
    elif types is not None:
        errs.append("types 必须是数组")
    # 类型引用完整性: **上面那条 `_type_ok` 已经查过了** (行 377, 用的是 `allowed_types`)。
    #
    # 这里原先还有一遍"再查一次"的循环, 用的是 `fd["type"] not in declared` —— **裸字符串
    # 相等**。它与上一条查的是同一批字段, 只有两处不同, 而两处都是错的:
    #   * 它不认复合类型 —— `[u64; 8]` / `[i32]` 过了 `_type_ok` 却过不了它, 于是
    #     结构体里放一个数组字段, 整份对象被判**非法**, `lomt_from` 直接 `[ERR]` 退出
    #     (比 skip 更坏: 一个字段的问题毁掉整个模块)。2026-09-17 由 Java/Rust 两个 agent
    #     **各自独立**撞到 (`int[]` 字段与 `[u64; 8]` 字段)。
    #   * `declared` 是 `TYPES | seen_ty`, **不含 enums** —— 而 `allowed_types` 含。
    #     于是"字段类型是一个已声明的枚举"被误报未声明。
    # 结论: 这一遍**只可能误报, 不可能多抓** (它查的字段集合是上一条的子集)。删掉它,
    # 顺带消掉"两份判据各自漂移"的可能 —— 那种漂移就是这个 bug 的来源。

    # 类型名跨表唯一 (types / enums 共用一个命名空间)
    for n in sorted(seen_ty & seen_en):
        errs.append(f"类型名 {n} 同时出现在 types 与 enums")

    # ---- 出界声明
    excl = _req(doc, "excluded", "根", errs)
    if isinstance(excl, list):
        for i, x in enumerate(excl):
            if not isinstance(x, str) or not x.strip():
                errs.append(f"excluded[{i}]: 必须是非空字符串")
    elif excl is not None:
        errs.append("excluded 必须是字符串数组")

    # ---- M45: 泛型声明 / 单态化实例 / trait / impl (仅 v1)
    gen_by: dict[tuple[str, str], list[str]] = {}
    for i, g in enumerate(doc.get("generics") or []):
        w = f"generics[{i}]"
        if not isinstance(g, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        kind, name = g.get("kind"), g.get("name")
        if kind not in ("fn", "type"):
            errs.append(f"{w}.kind 必须是 fn/type: {kind!r}")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
            continue
        ps = g.get("params")
        if not isinstance(ps, list) or not ps:
            errs.append(f"{w}.params 必须是非空数组")
            continue
        seen_t: set[str] = set()
        for j, p in enumerate(ps):
            if not isinstance(p, str) or not IDENT_RE.match(p):
                errs.append(f"{w}.params[{j}] 非法: {p!r}")
            elif p in seen_t:
                errs.append(f"{w}.params[{j}] 重复: {p}")
            else:
                seen_t.add(p)
        if (kind, name) in gen_by:
            errs.append(f"{w}: 泛型声明重复 {kind} {name}")
        else:
            gen_by[(kind, name)] = list(ps)

    seen_i: set[str] = set()
    for i, inst in enumerate(doc.get("instances") or []):
        w = f"instances[{i}]"
        if not isinstance(inst, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        kind, name, of = inst.get("kind"), inst.get("name"), inst.get("of")
        if kind not in ("fn", "type"):
            errs.append(f"{w}.kind 必须是 fn/type: {kind!r}")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
        elif name in seen_i:
            errs.append(f"{w}.name 重复: {name}")
        else:
            seen_i.add(name)
        if not isinstance(of, str) or (kind, of) not in gen_by:
            errs.append(f"{w}.of 不是已声明的泛型: {of!r}")
            continue
        args = inst.get("args")
        if not isinstance(args, list):
            errs.append(f"{w}.args 必须是数组")
            continue
        if len(args) != len(gen_by[(kind, of)]):
            errs.append(f"{w}.args 数量 {len(args)} 与泛型参数 {len(gen_by[(kind, of)])} 不符")
        for j, a in enumerate(args):
            if not _type_ok(a, allowed_types):
                errs.append(f"{w}.args[{j}] 非法类型: {a!r}")

    tr_methods: dict[str, set[str]] = dict(BUILTIN_TRAITS)
    for i, t in enumerate(doc.get("traits") or []):
        w = f"traits[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        name = t.get("name")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
            continue
        if name in tr_methods:
            errs.append(f"{w}.name 重复: {name}")
            continue
        ms = t.get("methods")
        if not isinstance(ms, list) or not ms:
            errs.append(f"{w}.methods 必须是非空数组")
            continue
        seen_m: set[str] = set()
        for j, m in enumerate(ms):
            if not isinstance(m, str) or not IDENT_RE.match(m):
                errs.append(f"{w}.methods[{j}] 非法: {m!r}")
            elif m in seen_m:
                errs.append(f"{w}.methods[{j}] 重复: {m}")
            else:
                seen_m.add(m)
        tr_methods[name] = seen_m

    for i, im in enumerate(doc.get("impls") or []):
        w = f"impls[{i}]"
        if not isinstance(im, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        tr, ty = im.get("trait"), im.get("for")
        if not isinstance(tr, str) or tr not in tr_methods:
            errs.append(f"{w}.trait 不是已声明的 trait: {tr!r}")
        if not _type_ok(ty, allowed_types):
            errs.append(f"{w}.for 非法类型: {ty!r}")
        ms = im.get("methods")
        if not isinstance(ms, list) or not ms:
            errs.append(f"{w}.methods 必须是非空数组")
            continue
        seen_m: set[str] = set()
        for j, m in enumerate(ms):
            if not isinstance(m, str) or not IDENT_RE.match(m):
                errs.append(f"{w}.methods[{j}] 非法: {m!r}")
            elif m in seen_m:
                errs.append(f"{w}.methods[{j}] 重复: {m}")
            else:
                seen_m.add(m)
                if tr in tr_methods and m not in tr_methods[tr]:
                    errs.append(f"{w}.methods[{j}] {m!r} 未在 trait {tr} 中声明")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato", description="Potato 形式对象校验器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="+")
    s = sub.add_parser("show")
    s.add_argument("file")
    r = sub.add_parser("replay", help="M51: 按对象自带版本回放校验")
    r.add_argument("files", nargs="+")
    r.add_argument("--expect-version", choices=VERSIONS, default=None)
    a = ap.parse_args(argv)

    if a.cmd == "replay":
        bad = 0
        for f in a.files:
            try:
                doc = json.loads(Path(f).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"[ERR] {f}: {e}", file=sys.stderr)
                return 2
            ver = doc.get("potato")
            errs = validate(doc)
            want = a.expect_version
            mismatch = want is not None and ver != want
            if errs or mismatch:
                bad += 1
                why = f"版本 {ver!r} != 期望 {want!r}" if mismatch else errs[0]
                print(f"[INVALID] {f}: 版本={ver!r} {why}")
            else:
                print(f"[OK] {f}: 版本={ver!r} 回放通过"
                      f"{' (legacy)' if ver != VERSIONS[-1] else ''}")
        return 1 if bad else 0

    if a.cmd == "show":
        doc = json.loads(Path(a.file).read_text(encoding="utf-8"))
        print(f"unit={doc.get('unit')} lang={doc.get('language')} "
              f"caps={len(doc.get('capabilities', []))} fns={len(doc.get('functions', []))} "
              f"layouts={len(doc.get('layouts', []))}")
        for c in doc.get("capabilities", []):
            d = c["domain"]
            print(f"  cap  {c['name']}: {d['space']}[{d['lo']}..{d['hi']}]"
                  f"{' revocable' if c['revocable'] else ''}")
        for f in doc.get("functions", []):
            args = ", ".join(f"{p['name']}: {p['type']}" for p in f["params"])
            print(f"  fn   {f['name']}({args}) -> {f['ret']}")
        for r in doc.get("layouts", []):
            print(f"  rec  {r['name']} size={r['size']} fields={len(r['fields'])}")
        for c in doc.get("consts", []):
            print(f"  const {c['name']}: {c['type']} = {c['value']}")
        for t in doc.get("types", []):
            print(f"  type {t['name']} fields={len(t['fields'])}")
        for g in doc.get("generics", []):
            print(f"  gen  {g['kind']} {g['name']}<{', '.join(g['params'])}>")
        for i in doc.get("instances", []):
            print(f"  inst {i['kind']} {i['name']} = {i['of']}<{', '.join(i['args'])}>")
        for t in doc.get("traits", []):
            print(f"  trait {t['name']} methods={','.join(t['methods'])}")
        for i in doc.get("impls", []):
            print(f"  impl {i['trait']} for {i['for']} methods={','.join(i['methods'])}")
        for x in doc.get("excluded", []):
            print(f"  excl {x}")
        return 0

    bad = 0
    for f in a.files:
        try:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERR] {f}: {e}", file=sys.stderr)
            return 2
        errs = validate(doc)
        if errs:
            bad += 1
            print(f"[INVALID] {f}: {len(errs)} 项")
            for e in errs:
                print("  " + e)
        else:
            print(f"[OK] {f}: 形式对象合法")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
