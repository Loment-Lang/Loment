# 273 · 新维度准入清单 —— 一维不是"加个词"，是给整条链各加一格

> 状态: **规范**（2026-10-19，issue #943）· 上游: `docs/175` §3（核心模式）·
> `docs/940`（矩阵总判据）· `docs/273`（`mode` 行的样板）· `docs/272`（`no_gc` 的计划页）
> 一句话: 一维**不能只加在 checker 里** —— 它要在前端、两份链接器、VM、产物、诊断、
> 文档与门禁上**各有一格**；这张清单就是那一格一格的登记处。

---

## 1. 六个动作，缺一不可

| # | 动作 | 落点 | 门禁怎么看见它 |
|---|---|---|---|
| 1 | **声明**（取值 / 默认 / 冲突，两份实现同一张表） | 参考：`tools/lomentc.py` 的 `CORE_DIMS` / `CORE_DEFAULTS` / `CORE_CONFLICTS`；自举：`switches.lomt` 的 `sw_is_core`、`checker.lomt` 的 `chk_core_dim` / `chk_core_val`、`potato.lomt` 的 `pt_<dim>` | `loment_rule_parity`（码集相等）+ `loment_p8_test`（IR 逐字节） |
| 2 | **一行联动表**（这一维对每个子系统的含义） | 本文 §2 那张表；新维度的 PR 在这里加一行 | 评审时对着表逐格问"这一格是什么" |
| 3 | **每个取值一个探针**（它在别的取值下会不同） | `tools/loment_choose_matrix_test.py` 的 `MATRIX_DIMS` / 各行的 `test_...` | 矩阵判据（取值变了，探针必须变） |
| 4 | **冲突表**（哪两档矛盾、**为什么**，用用户的话） | `CORE_CONFLICTS`；自举侧同一条 `if` | 矩阵的冲突判据（两个取值都点名 + 理由非空） |
| 5 | **产物记录**（不读源码可判） | Potato 的字段（v 号随字段走）；需要时 IR 标记（`; loment-<dim>: <v>`） | `loment_potato*_test` + 各行的 artifact 判据 |
| 6 | **门禁覆盖整个矩阵**（不是抽样） | `MATRIX_DIMS` 从 `CORE_DIMS` 现算合法格；链接器/PE/镜像是 #940 那一条 | `loment_choose_matrix_test` + `loment_elf_test::test_both_linkers_and_pe_agree_on_every_legal_choose_cell` |

**"写得出、却什么也拿不到"是最坏的一格**（`docs/231` §1.3 对 `vm` 的判词）：
如果一维在产物上没有任何可分辨的效果，它**要么补上联动，要么别进核心表**。

## 2. 当前七维的联动表

| 维 | 前端 | 两份链接器 | VM | 运行期闭包 | 产物 | 诊断 |
|---|---|---|---|---|---|---|
| `mode` (`std`/`no_std`) | 拒 `no_std + hosted`（需求链） | `no_std` 标记在 IR 里；带宿主层 ⇒ 拒（两份链接器） | 不改 IR | `no_std` 不带宿主层 | Potato `mode` | E022（点名两个取值） |
| `gc` (`gc_manual`/`gc_auto`/`gc_auto_alpha`) | 收集器/梯子随档 | 链接 IR 里有的东西（档已经决定了它们） | 三档在 VM 上跑出同一结果 | auto/alpha 带收集器+根表；manual 不带 | Potato `gc` + `gc_ladder` | E022 |
| `runtime` (`runtime`/`no_runtime`) | 运行期构造在 `no_runtime` 下拒（E030） | 分配器/收集器随 IR | 不改 IR | 分配器/收集器/堆 | Potato `runtime` | E030 |
| `port` (`sealed`/`hosted`) | `hosted` 允许宿主层 | 动态 V2 / 静态 V1 两条腿；端口门 | 不改 IR | `hosted` 的堆从宿主来 | Potato `port` + `; loment-port` / `; loment-heap` | E022 |
| `vm` (`vm`/`no_vm`) | 不改 IR（今天只声明） | 不改产物 | 选择执行器 | 不改 | Potato `vm` | — |
| `lms` (`lms_close`/`lms_open`) | 检查器发 E024–E026；界检查按站点 | 不改产物（全是内联指令） | 不改 IR | 无新增组件 | Potato `lms` | E024–E026 |
| `lspe` (`lspe_open_admin`/`lspe_close_admin`) | 只改台账 | 不改产物 | 不改 IR | 无 | Potato `lspe` | — |
| `no_gc`（`gc` 的值，计划页随 #538 合入） | 收成核心取值 | 产物带收集器 ⇒ 拒（两份链接器） | 与 manual 同 | 无收集器 | Potato `gc = no_gc` + `; loment-gc` | E022（同维两值） |

## 3. 一个反例（这条清单是怎么长出来的）

`mode` 曾经是**唯一一格"记录但不驱动"**的维：`std` 与 `no_std` 的 IR 逐字节相同
（PR #947 的矩阵判据发现的）。那份判据当时**故意没断言**这一格 —— 因为断言会红，
而红着的东西不能当门禁。这一轮把它补上了：`no_std` 的产物现在**自己声明**
不带宿主层，两份链接器核它（`docs/273`）。

**下一次加维度的顺序**：先在 §2 的表里写一行（哪怕是"今天不改产物"，
也要**明写原因**），再照 §1 的六个动作落；矩阵判据会从 `CORE_DIMS` 自己长出新的格。
