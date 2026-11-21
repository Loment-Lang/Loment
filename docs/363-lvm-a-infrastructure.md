# 363 · LVM-A 的底层基础设施：P5 快照、封印、独立验证器、创世衰减

> 状态: **已实装**（2026-11-20），参考实现 + 自举对齐；自举 checkpoint 与 `sw_tag` 在 `docs/364`
> 上游: `docs/360`（时间桥梁）· `docs/359`（本世代的墙）· `docs/358`（只信产物）·
> `docs/357`（盘古的三件增量）· `docs/362`（`sw_cap` 第一实现）· `docs/264`（跨档同答）
> 判据: `tools/lvm_cp_test.py`（7 条，含 `docs/364` 的 `sw_tag` 快照）·
> `tools/lvm_seal_test.py`（9 条）· `tools/lvm_twin_test.py` 的 `test_cap_*`（2 条，
> 加自举 checkpoint 一条在 `docs/364`）
> 一句话: **盘古要的底层不是七层架构，是四件能跑的东西：快照、封印、独立复查、派生衰减。**

---

## 0. 这一笔补的是哪四块

`docs/357` 把盘古对账成三件真增量，`docs/360` 又加了时间桥梁的 P5。四件都在这一笔里：

| # | 设施 | 落点 | 判据 |
|---|---|---|---|
| **1** | **P5 checkpoint / resume** | `tools/lvm.py::ExplicitExecutor` | `lvm_cp_test` |
| **2** | **封印**（产物 + 站点 + 基质 + 规律 + 创世 + 总哈希） | `tools/lvm_seal.py::build_seal` | `lvm_seal_test` A/B/G/H |
| **3** | **独立验证器**（零 import 工具链） | `tools/lvm_seal.py::verify` | `lvm_seal_test` F |
| **4** | **同产物创世衰减**（caps ⊆ / 预算 ≤ / 深度） | `tools/lvm_seal.py::derive` | `lvm_seal_test` E |
| 附 | **自举镜像的 `sw_cap`** | `loment/tools/lvm.lomt` | `lvm_twin_test` `test_cap_*` |

---

## 1. P5：guest 状态就是全部状态

### 1.1 为什么要第二台引擎

递归 T0 的 guest 状态有一半分住在 **Python 的调用栈**里：当前函数、当前块、块内序号、调用者的
待写回位置。那不是 guest 的，取不到也存不下来。`ExplicitExecutor` 把这一半搬进显式的
`stack: [_IterFrame]`：

```rust
// 展示用 rust 围栏，理由见 CLAUDE.md
struct IterFrame {
  fn, label, idx, prev,          // PC = (函数, 块, 块内序号)
  vals, caps, objs, pending,     // SSA 值 / 能力 / 本帧对象 / phi 暂存
  wait_dest, wait_retty, wait_hook,  // 调用者等待的返回值与生命周期钩子
}
```

默认 T0 **仍是递归引擎**：新的显式栈引擎只在 `--checkpoint-at` / `--resume` 下启用，
所以默认路径一个字节都没动。判据 A 拿整份语料钉「两个引擎同答」。

### 1.2 快照格式

`LVMC\x01` + zlib(canonical JSON)。里面是 **guest 的全部状态**：

| 字段 | 内容 |
|---|---|
| `ir` | 产物 IR 的 sha256（换一份字节码恢复被**点名拒**） |
| `stack` | 每帧的函数 / 块 / 序号 / 值 / 能力 / 帧对象 / phi / 待写回 |
| `mem` / `heap` / `sp` | 4 MiB guest 内存与两个游标 |
| `sub` | 对象表、归还窗口、指针影子表、全局能力（`sw_cap`） |
| `vfds` / `argv` | 合成文件与命令行 |

宿主状态里唯一进快照的是**序列化格式本身**。剖面与确定性计数不进：它们记的是解释器的账，
不是 guest 的状态 —— 传进来就点名拒，不静默丢。

### 1.3 判据

* **A** 10 份语料：递归与显式栈 `rc + stdout` 逐字节同。
* **B** 32 个指令边界：存 -> 取 -> 跑完，答案不变。
* **C** 至少一个采样点栈深 > 1（被调者也在快照里），恢复后仍对。
* **D** 链式：K1 -> K2 -> 跑完。
* **E** 同一 K 两次快照逐字节同；换 IR 恢复被点名拒。
* **F** t2 / 剖面 / 计数 / 缺 `--checkpoint-out` 都点名拒。

---

## 2. 封印：一份产物 + 一张站点台账 + 一个总哈希

`build_seal(ir_text, profile)` 产出：

```rust
struct Seal {
  schema, artifact { format, bytes, sha256, site_count, sites_sha256 },
  sites: [Site],                       // fn / blk / idx / offset / kind / grade
  laws: [Law],                         // id / verdict / needs / exclusions
  substrate { profile, guarantees, assumptions },
  verifier { id, version, bytes, sha256, lines, max_bytes },
  genesis { mode, max_depth, max_children, max_total },
  floor { seed_sha256 },               // 本笔留空；可填
  sum,                                 // 对除 sum 外全部字段的规范序列化取 sha256
}
```

三条设计决定：

1. **站点由验证器独立扫**。`scan_sites` 只懂 IR 文本：`define` 起函数、`label:` 起块、
   `load`/`store`/`getelementptr`/`atomicrmw` 是访存，`asm "syscall"` 与 `declare` 出来的调用
   是 unsafe 面。它**不 import 编译器、链接器、LSPE**，所以 A 链的「完备性」换成了
   「**产物里的每条访存**」这一句可核的话（`docs/358` §1A）。
2. **规律不是自由标签**。`no_segv` 要求 `P2.bounds + P4.fault`，`no_dangling` 要求 `P3.gen`，
   `site_stable` 要求 `P1.site`；缺一条就只能是 `floored`，说成 `checked` 验证器就红。
3. **验证器被标价**。文件字节 / 行数 / sha256 / `max_bytes` 都进封印；超预算就红（B8）。
   今天的数：**464 行 / 17,832 字节 / 预算 128 KiB**。

---

## 3. 独立验证器：只读产物，零 import

`verify(ir_text, seal, host)` 重算八件事：总哈希、产物哈希、基质保证、基质假设、站点台账、
站点计数/哈希、规律、创世预算、验证器字节。任一不符就红，并给出**可读的拒绝理由**。

判据 F 是机械的：在子进程里用 `sys.meta_path` **挡住** `lomentc` / `lomelf` / `lvm` / `lspe`，
验证器照样 PASS。它证明的是「删掉编译器还能验」，不是「编译器没漏站点」——
后者读不到源码，永远证不了（`docs/225` 的可验证的缺席）。

---

## 4. 基质剖面与时间桥梁

`SUBSTRATES` 四档：

| profile | guarantees | assumptions | 本机可跑 |
|---|---|---|---|
| `sw_cap` | `P1 site` / `P2 bounds` / `P3 gen` / `P4 fault` | `P5.checkpoint:opt_in` / `W6.host_cpu` | **是** |
| `sw_tag` | `P1` / `P2` / `P4` | `P3.probabilistic` … | 是（声明降级） |
| `hw_cap` | 四原语 + `P5.hardware` | `W6.cheri_silicon` | **否** |
| `hw_tag` | `P1/P2/P4/P5.hardware` | `P3.probabilistic` / `W6.mte_silicon` | 否 |

规则只有一条：**`guarantees` 里不许出现这一档没有的东西。**

* 在本机生成 `hw_cap` 封印：它**降级** —— guarantees 空、四条保证全进 assumptions、
  规律全 `floored`；这样的封印自洽、可验证。
* 把 guarantees 伪造成硬件那一套：验证器红（`substrate guarantees are not what this host
  can honour`）。这就是 `docs/360` B6 的两个分支。
* **B5（时间桥梁的硬判据）**：同一份产物，`sw_cap` 与 `hw_cap` 封印的 `artifact.sha256`
  相同，站点**身份**相同；只有 grade / 保证 / 封印哈希变。**换代不重编。**

---

## 5. 创世衰减：同产物派生

`derive(parent, delta)` 五条，照 `docs/358` §3.2：

| # | 规则 | 失败 |
|---|---|---|
| G1 | `delta.caps ⊆ parent.caps` | `cap_escalation` |
| G2 | `delta.budget ≤ parent.budget`（heap / steps） | `budget_escalation:*` |
| G3 | `depth + 1 ≤ max_depth` | `depth_exceeded` |
| G4 | 子数 < `max_children`；总数 ≤ `max_total` | `children_exceeded` / `tree_exceeded` |
| G5 | `sum = H(parent.sum ‖ canonical(delta))` | — |

派生节点自带 `parent` 与 `delta`，所以整条链可以**离线逐节复核**：根对全部字段取哈希，
每一节只对「父哈希 + 差量」取哈希。同父同差量逐字节同。

**边界**：这是**同产物**的一条链，不是任意树；`max_children` / `max_total` 由调用方记账
（本笔按链模型），跨产物创世明确关掉（`docs/358` §9）。

---

## 6. 自举对齐：`sw_cap` 进了 `lvm.lomt`

`docs/360` §6：两套实现不能共享代码路径；B7：同一故障给同一 code + site。自举侧落的是
**按地址**的那一半：

* 固定对象表（4096 条，按 base 升序 + 二分），归还窗口 1024 条，帧对象栈 4096 条；
* 五个来源同参考：全局（跳过外层 `@__loment_heap`）、`brk`、`alloca`、`__loment_alloc`、argv；
* `__loment_*` 帧是特权帧；`K_LOAD` / `K_STORE` 前做 `cap_check`；
* 故障行与参考同形：`lvm: F01 bounds violation at _start:entry#13`，退出码 `100+kind`。

**判据**（`lvm_twin_test`）：

* `test_cap_faults_match_reference`：越界 / 悬垂 / 堆越界三条，两边的**退出码**与
  **`F0k + 站点`**逐字节同；
* `test_cap_legal_programs_match`：语料在 `sw_cap` 下两边 stdout + rc 同答。

### 为什么自举侧只能按地址

`VAL_REC` 只有 16 字节（`vlo`/`vhi`），没有给每个 SSA 值再挂 `(obj_id, gen)` 的位置。
所以它抓的是「地址没有归属 / 归属已归还 / 越出对象」；**抓不到**「洗过整数之后同一地址被
复用」。这条边界与参考实现 §5.1 的那条**同一句话**，不是自举侧少做了一半能力 —— 两边都
没有指针携带的代。`sw_cap` 只在 T0；T2 + `sw_cap` 在两边都点名拒。

---

## 7. 判据汇总（每条先红）

| 套件 | 条数 | 钉什么 |
|---|---|---|
| `lvm_cp_test` | 7 | 两引擎同答 / 任意边界存取 / 调用栈进快照 / 链式 / 确定与绑定 / 只走 T0 |
| `lvm_seal_test` | 9 | 往返 / 三种篡改 / 硬件降级与伪造 / B5 同字节码 / 创世四条拒绝面 / 零 import / 验证器标价 / 站点完备 |
| `lvm_twin_test::test_cap_*` | 2 | 三条故障同 code+site / 合法语料同答 |

---

## 8. 我不主张

1. **不主张快照格式跨实现。** 今天是参考实现的格式（JSON + zlib）；自举侧的
   `lvm.lomt` 还没有 checkpoint。跨实现的快照兼容是另一件事。
2. **不主张封印进了 Potato。** 它是**旁挂**（`lvm_seal.py emit --out`），默认产物一个
   字节没动（`docs/357` P8）。`substrate` 字段进产物要另开一次契约变更。
3. **不主张验证器能替代编译器检查。** 它读不到源码，也读不到 IR 的漏站点。
4. **不主张创世是通用树。** 同产物、链模型、有界。
5. **不主张自举侧与参考逐位同。** 行为被三条故障与一份语料钉住；实现结构不同
   （`docs/243` §5.1）。
6. **不主张性能。** checkpoint 的序列化成本、封印体积、验证器成本都未测。

---

## 9. 与其它文档的关系

* `docs/360` —— 本文落 P5 与基质剖面；B5 由此有判据，B7 由孪生判据兑现。
* `docs/358` —— 本文是它的对象（Seal / Universe）与两个操作（`verify` / `derive`）的第一实现。
* `docs/357` —— 三件增量各自落在 §2 / §3 / §5；独立验证器是「避免 LSPE 自我证明」那一页。
* `docs/362` —— `sw_cap` 的 T0 实现；本文补它的 P5、封印与自举镜像。
* `docs/364` —— 自举 checkpoint 与 `sw_tag`；本文的 P5 与基质剖面在自举侧的延伸。
* `docs/264` —— 跨档同答；本文把「档」扩到「基质」，并加了 checkpoint 这条轴。
