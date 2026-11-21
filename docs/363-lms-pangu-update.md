# 363 · LMS 的盘古档：站点账本、fault ABI 与时间桥梁

> 状态: **V1 已落地（两套实现 + 种子）**（2026-11-20）
> 上游: `docs/357`（三件增量）· `docs/358`（只信产物）· `docs/359`（本世代的墙）·
> `docs/360`（LVM-A 与 `sw_cap`）· `docs/356`（fault 类与 ABI）· `docs/253`（LMS 七级）·
> `docs/361`（LNotary）· `docs/245`（`thread_spawn`）
> 实测: 2026-11-20，本分支 + LVM 线的 `feat/lvm-substrate` 工作树
> 一句话: **LMS 从"七个布尔 + 一条内联比较"升级成"逐站点账本 + 与 LVM 同一个
> fault 契约"；同一语义在原生与 `sw_cap` 上得到同一个退出码。**

---

## 0. 这一轮加了三样，外加一个真缺陷

| 件 | 落点 | 判据 |
|---|---|---|
| **fault ABI**（原生侧） | `tools/lomentc.py` 的 `_IR_FAULT` 与 `_lms_fatal(1)` | 原生 F01 退出 **101** |
| **站点账本** | `tools/lms_sites.py` → 站点表；`loment/lnotary/sites.py` 复核 | 站点表先红后绿；改一条就红 |
| **旁挂绑定** | `lawspace.py` 的 `sites {sha256, count}` | 换一张站点表 ⇒ `LNT-LS` |
| **缓存键**（缺陷） | `tools/loment_cache.py` 的 `key_for` | 改一行用户开关 ⇒ 键变 |

**默认档一个字节都不变。** fault ABI 只在 `choose lms_pangu_true` 时发；
站点账本是旁挂，不进 Potato；缓存键的改动只影响**键**，不影响产物。

---

## 1. 契约：站点与 fault 码

站点坐标与 LVM 的 `sw_cap` **同一套**：

```
(fn, blk, idx)   →   "fn:blk#idx"
```

| kind | 名字 | 含义 | 退出码 |
|---|---|---|---|
| **1** | `F01` | 越界（对象 / 常量界） | **101** |
| **2** | `F02` | 悬垂（对象已归还） | **102** |
| **3** | `F03` | 空指针 | **103** |

退出码 `100 + kind` 与 `docs/356` §5.3 一致，也与 LVM 的 `fault_exit` 一致
（`tools/lvm.py` 的 `FAULT_CODES`）。`llvm.trap` 的 132 保留给"没有 kind 的旧出口"。

---

## 2. 两侧各检查什么（诚实边界）

| | 原生 g1 | LVM `sw_cap` |
|---|---|---|
| 范围 | `alloc(<常量>)` 直接发出的 `load8`/`store8` | **全部** guest 的 `load`/`store`/`atomicrmw` |
| 边界 | 编译期常量 | 对象表 `base/limit` |
| 对象代 | **无** | 对象表 `gen`（软件） |
| 站点文本 | **无**（V1 只报 kind） | `<fn>:<blk>#<idx>` + witness |
| 触发开关 | `lms_guard` | `--substrate sw_cap` |
| 默认 | 满档开着 | 关着 |

两侧**本来就不等价**：原生这一档是"常量界的标记子集"，LVM 那一档是"全量 guest"。
站点账本把这件事写下来：原生会拦的站点标 `checked`，其余标 `unsafe`；
LVM 的全量检查属于**基质**的账（`docs/360` §3.3），不塞进这张表冒充。

---

## 3. 时间桥梁：实测

同一语义（`alloc(4)` 后 `load8(p, 4)`）：

| 侧 | 命令 | 结果 |
|---|---|---|
| **原生** | `lms_pangu_true`（盘古档优先于拒绝器） | **rc = 101** |
| **LVM** | `--substrate sw_cap`，g1 关 | **rc = 101**，`F01 bounds violation at _start:entry#6 (offset=4 size=1 limit=4)` |

**盘古档的语义**：开着它时，g1 的越界**不再走 `lms_reject` 的"跳过"**，一律变成
fault（101）。这是刻意的：时间桥梁要的是"同一个语言事件"，跳过会给两侧不同的答案。
拒绝器本身不变，只是被更强的档覆盖。

两份探针都带 `choose vm`：LNotary 的启用条件是"产物真的声明了 LVM"（`docs/361` §2.5），
时间桥梁的两侧必须在同一个基质剖面下比较。

第二条是在 LVM 线的 `feat/lvm-substrate` 工作树上跑的（本分支的 `tools/lvm.py`
还没有 `--substrate`；判据 B 因此 SKIP，等它合进来自动激活）。

**一个先后问题要写下来**：如果 g1 和 `sw_cap` 同时开，而 LVM 那条线的编译器还是旧的，
g1 会先 `abort`（132），`sw_cap` 轮不到。两份探针因此各关一侧：

* 验原生 fault ABI ⇒ 开 `lms_pangu_true`（它覆盖拒绝器，让 g1 走致命分支）；
* 验 `sw_cap` ⇒ 关 `lms_guard`（不让 g1 先死），让基质检查。

两条线合到一起、LVM 的编译器拿到同一个 fault ABI 之后，同一份源可以直接两侧同跑。
这条是合并时要核的第一件事，不是设计缺陷。

---

## 4. 站点账本

`tools/lms_sites.py` 读一份 IR，逐条列出访存站点：

```json
{
  "schema": 1,
  "source": { "sha256": "…", "bytes": 12345 },
  "sites": [
    { "fn": "_start", "blk": "entry", "idx": 4,
      "op": "store", "ty": "i32", "grade": "proven", "rule": "R1" },
    { "fn": "_start", "blk": "L1_lmsok", "idx": 1,
      "op": "load", "ty": "i8", "grade": "checked", "fault": "F01" }
  ],
  "counts": { "sites": 5, "proven": 1, "checked": 1, "unsafe": 3 },
  "seal": "…"
}
```

等级三种：

* **`proven`**：证明落在站点上（`rule`）：**R1** 常量下标 + 常量长度；**R2** 规范循环；
  **R3** 支配分支；**R4** 未落（要 `gc_ladder`）。`rule` 必填，LNotary 只核
  **引用是否可复查**（V4，`docs/361` §6.6）；
* **`checked`**：同一个 `*lmsok` 块里**第一条**访存 —— g1 检查保护的就是它；
* **`unsafe`**：其余。块里第二条访存（比如把结果写进局部变量的 `store i32 %t6`）
  不在检查范围内，**不许跟着升级**。

`seal` 是除自己外全部字段的规范哈希；`source.sha256` 把表绑到 IR 字节。
LNotary 的 `sites.verify_sites` 只核自洽、绑定与规则引用，不重扫 IR（零工具链 import 是硬约束）。

旁挂因此多一格：

```json
"sites": { "sha256": "<站点表的 seal>", "count": 4 }
```

给了 `--sites` 却没绑、绑了却没给、或者换了一张表 —— 三种都是 `LNT-LS`。

---

## 5. 缓存缺陷：用户开关看不见

`loment_cache.key_for` 的源字节摘要用的是**抹掉 `choose` 行之后**的源 ——
所以 `choose close lms_reject` 或 `lms_pangu_true` 改了发射，键却不变。
实测：同一份源改一行开关，读到了旧 IR（`L1_lmsok` 仍然走 `lmsjoin`，没有 fault）。

修法：键里加一格用户开关摘要（`sw=<名字>:<0|1>`，按名字排序）。
判据：`lms_bridge_test::test_cache_key_sees_user_switches` —— 同源不同开关，键必须不同。

这是这一轮**唯一改到既有行为的缺陷修复**；它不改变任何产物字节，只改变缓存命中。

---

## 6. 判据（`tools/lms_bridge_test.py`，4 条）

| # | 判据 | 现状 |
|---|---|---|
| **A** | 原生 `lms_pangu_true` 的 F01 退出 **101**（WSL 跑 ELF） | 绿 |
| **B** | LVM `--substrate sw_cap` 的 F01 退出 **101** + 站点 | **SKIP**（本分支还没合 `--substrate`）；对 `feat/lvm-substrate` 手动实测绿 |
| **C** | 默认档 IR 里**没有** `__loment_fault` | 绿 |
| **D** | 改一行用户开关 ⇒ IR 缓存键变 | 绿 |

外加 `lnotary_test` 的 21 条（含站点表 5 条）：

* 真站点表接受；
* 改等级 ⇒ `LNT-SITES`；
* 旁挂绑站点表；
* 换一张表 ⇒ `LNT-LS`；
* 站点表自己的封印被改 ⇒ `LNT-SEAL`。

登记：`tools/ci.py` 的套件表与时间表、`.github/gate-floors.txt`
（`lnotary_test 21`、`lms_bridge_test 4`）。

---

## 6.5 自举侧：同一契约的第二个实现

`loment/selfhost/codegen.lomt` 加了三处，文本与参考实现逐字节同：

* `lms_mask` 的第 **128** 位（**非级位**，七级只占 0..6）：`choose lms_pangu_true` 置位，
  `choose close lms_pangu_true` 清位；
* 两个 g1 坏块：位开着发 `call void @__loment_fault(i32 1)`，否则保持 `__loment_abort`；
* `emit_runtime` 末尾：位开着追加 `_IR_FAULT` 的同一段文本。

种子重算：`loment/build/selfhost_driver.ll` 7,283,635 → **7,289,202 B**，
sha256 `26b69c1afae8b96c7da5d789f88153fea2b5100b22182c4ca7c75cc50a44e628`；
`docs/270` §4 的地板数同步更新（`loment_lms_test` 21/21）。
种子是生成物，单独一笔提交（`CLAUDE.md`）。

**驱动器要传一个参数**：`lms_pangu_true` 是用户开关，`apply_switches` 会先把它
从 token 流里抹掉，所以 `lms_mask` 扫不到它。`emit_module` 因此多收第 7 个参数
`pangu`，由驱动器从开关表取值；置位后 `lms_mask` 的**第 128 位**（非级位）。

孪生对拍：同一份 pangu 探针，`stage1` 与 `lomentc --print llvm` 的
`__loment_fault` 定义与 `call void @__loment_fault(i32 1)` 调用点**逐字节相同**
（`lms_bridge_test` 的 E）。全量 IR 在 `choose runtime` 上有一条**既有**的镜像差异
（分配器注释），不归这一笔管；判据只核本次特性的那一段，并额外核"默认档两侧都没有
fault 符号"。

---

## 7. 没做的（每条都写清为什么）

1. **原生侧报站点文本。** V1 只报 kind（退出码）。文本要一段 syscall-only 的写路径，
   与 `no_runtime` 的 fixed stub 一起做（`docs/356` §5.3）。
2. **原生的 F02/F03。** 对象代与空指针检查在原生侧还没有；LVM `sw_cap` 有。
   站点账本把它们标 `unsafe`，不冒充。
3. **`proven` 等级。** 站点表 V1 只有 `checked` / `unsafe`；R1–R4 的规则引用
   （`docs/356` §6.2）是下一档。
4. **`race_free`。** `docs/357` §5.1 的候选第四规律，要动线程内存模型。

---

## 8. 我不主张

1. **不主张两侧等价。** 原生是标记子集，LVM 是全量 guest；站点账本把这个差别写下来。
2. **不主张 fault ABI 已进 L0 契约。** 退出码 `100+kind` 仍是提案；
   `docs/356` §11 第 7 条同一条。
3. **不主张站点表能证明"没有漏站点"。** 它只枚举 IR 里**已经存在**的访存；
   源码级完备不在这一档（`docs/358` §1A）。
4. **不主张自举侧已经跟上。** 见 §7 第 1 条。
5. **不主张性能。** fault ABI 的代价是一条 `call` + 一次系统调用；站点表是离线成本；
   都没测。

---

## 9. 与其它文档的关系

* `docs/360` —— LVM-A 的 `sw_cap` 基质。本文的站点坐标与 fault 码直接沿用它的实现。
* `docs/356` —— fault 类、`100+kind`、`fault_ladder`。本文落的是 F01 的原生出口与站点表。
* `docs/358` / `docs/361` —— 只信产物与 LNotary。本文给 LNotary 加了站点表与旁挂绑定（V2）。
* `docs/253` / `docs/270` —— LMS 七级。本文不改七级的语义，只把第 3/4/5 级的出口
  换成带 kind 的 fault，并把"哪些站点真的被检查"写成账。
* `docs/245` §10.1 —— 线程的 UB 仍在；`race_free` 是它唯一的落点（§7 第 5 条）。
