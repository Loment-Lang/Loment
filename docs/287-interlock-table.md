# 287 · 交锁表：一个 IR 构造上，五套系统谁先谁后

> 状态: **规范**（2026-11，issue `#1020`）· 上游: `docs/253` §5.3（LMS 的 g1/入口 A）、
> `docs/210` §2（GC 四层梯）、`docs/262` §5（LSPE 台账）、`docs/243`（LVM）
> 一句话: **同一个 `alloc` / `store8` 上，GC、LMS、LSPE、LVM、运行期都在动；它们的顺序
> 今天由发射器的书写顺序决定。这一页把那个顺序写成表，并用判据钉住。**

---

## 1. 为什么要这张表

`docs/253` §5.3 的 g1 检查、`docs/210` §2 的写屏障、`docs/262` 的台账、`docs/243` 的 VM
都盯着同一批构造。**没有一张表时**，插一套新系统只要在两行之间加一句，没人需要决定它
该在谁前面。两个已经踩过的例子（§3）说明顺序**是承重的**，不是排版。

这张表不是"新增一套系统"；它把**现有的组合**写下来，并给每一行一个判据。

## 2. 表

`order` 是**发射顺序**（小在前）。`emits` = 往 IR 里写；`observes` = 只读不写。

| construct | system | 动作 | order | 判据 |
|---|---|---|---|---|
| `alloc(n)` | GC L0/L1/L2 | 静态提升 / 定活 free / 纪元回卷 | 0（编译期） | `loment_gc_why_test` |
| `alloc(n)` | runtime 分配器 | `__loment_alloc` 自己（IR，不是宿主） | 1 | `lvm_test` · `loment_elf_test` |
| `alloc(n)` | GC L3 | `__loment_maybe_collect` 在分配点前 | 2 | `lomentc_test::test_lms_entry_a_rescue_needs_both_lms_and_a_collector` |
| `alloc(n)` | LMS 入口 A | 分配失败 → 收一次 → retry；`%lmsretry` 是本帧 `alloca` | 3（在 `boom` 之后、`dead` 之前） | `loment_lms_test::test_lms_entry_a_rescue_is_executed_by_every_product` |
| `alloc(n)` | LVM | 执行上面全部（分配器是 IR） | 4 | `loment_lms_test::test_memory_exhaustion_hits_the_same_wall_on_four_products` |
| `free(p)` | GC | 空闲链、块头 | 0 | `loment_elf_test::test_a_collection_keeps_the_free_list` |
| `free(p)` | LMS 检查器 | E029：之后再用 `p` 是错 | 编译期 | `lomentc_test` 的 E029 两条 |
| `free(p)` | LSPE | `Alive` 义务族 | 编译期 | `lspe_test` |
| `store8(p, off, v)` | GC alpha | 写屏障 `__loment_wb`，**在接受分支内**、GEP 之后 | 1（`lmsok` 内） | `loment_choose_matrix_test::test_interlock_store8_checks_before_barrier` |
| `store8(p, off, v)` | LMS g1 | `icmp` + `lmsok` / `lmsbad` 两路 | 0（屏障之外） | 同上 |
| `load8(p, off)` | GC | 根表 push（指针槽）；`gc_auto` 的根在函数入口 | 0 | `loment_elf_test` 根表判据 |
| `load8(p, off)` | LMS g1 | `icmp` + reject 路径的 `phi` | 1 | `lomentc_test::test_lms_g1_site_response_is_crash_or_reject` |
| `guard cap(i)` | LMS | g1/g2 的界检查 | 0 | `lomentc_test` 的 guard 判据 |
| `guard cap(i)` | LSPE | 站点 + 来源 | 1 | `lspe_test` · `loment_lms_test` |
| `guard cap(i)` | runtime | 审计计数器 | 2 | `loment_caps_test` |
| `_start` | LVM | 唯一入口（`docs/243` §3.5） | —— | `lvm_test::test_no_start_is_refused` |

⚠ **`order` 是同一 construct 内的相对顺序**，不是跨 construct 的全局序。

## 3. 两个承重的例子

### 3.1 `store8` 在 `gc_auto_alpha` + `lms_full` 下

屏障**坐在接受分支里**（`lmsok:` 之后、`getelementptr` 之后），检查**坐在分支外**。
反过来写，屏障会在一个被拒的地址上跑 —— 写没发生，但卡被标了。**没有判据会红**，
只是标记是错的。所以 `test_interlock_store8_checks_before_barrier` 断言：

* `lmsok:` 块里有 `call void @__loment_wb`；
* `lmsbad:` 块里**没有**；
* `br i1 … lmsok … lmsbad` 在 `lmsok:` 之前。

### 3.2 `alloc` 在 `gc_auto` + `lms_full` 下

入口 A 的 retry 块在分配器的 `boom` 之后、`dead` 之前；收集器在**分配器内部**跑，所以
retry 标记必须是**本帧的 `alloca`**（`%lmsretry`），不能是全局。`test_interlock_alloc_retry_is_inside_the_allocator`
断言：

* `%lmsretry = alloca i1` 在 `__loment_alloc` 的 `define` 之内、`boom:` 之前；
* `rescue:` 块里 `call void @__loment_collect()`；
* `floop` 的 phi 有一条来自 `rescue` 的入边；
* 产物里**没有** `@__loment_lms_retry` 全局（`docs/253` §13.2）。

## 4. 加一套系统的规则

1. 新系统如果碰现有 construct，**必须在 §2 表里加一列/一行**，写明 `emits`/`observes`
   与 `order`；
2. 它的位置若在两者之间，要写**为什么**在那里（§3 那种"反过来会静默错"的理由）；
3. 表里的 `判据` 一栏必须有一条**真实存在**的判据；移动位置让那条判据红，才算钉住。

## 5. 我不主张

- 不主张这张表覆盖了**所有**构造：它只列今天至少有两套系统在动的那些。`guard` 的
  runtime 一列与 `_start` 的 LSPE 一格是空位，不是"没有"。
- 不主张 `order` 永远不该改：改是一次决定，按 §4 写理由并让判据红。
