# 361 · LNotary：产物公证人

> 状态: **V0–V2 + V5 已落地**（2026-11-21，Python 包）· 上游: `docs/358` §4.2（独立验证器）·
> `docs/357` §4.2 · `docs/262`（LSPE）· `docs/147`（Potato 台阶）· `docs/353`（Dissigsegv）
> 基准: `origin/main` `a460b90e` + 本分支
> 一句话: **第三方视角的产物审计器：只读 Potato，零工具链 import，
> 用 `choose lnotary_true` 声明"这份产物要求公证"。**

---

## 0. 为什么不是 LSPE 的一个模块

LSPE 是**产出账本**的那一侧：义务、证据、求解器、台账都在它里面。
把审计器放进同一个包，独立性会变成一句口头承诺 —— 它可以不 import 求解器，
但下一个人加一行 `import` 就没有东西挡得住。

LNotary 的位置因此是**公证人**：

| | LSPE | LNotary |
|---|---|---|
| 角色 | 警察（产出证据与台账） | 公证人（核对已经写下的账） |
| 输入 | token 流、站点、义务 | 产物（Potato）与旁挂 |
| 输出 | 义务的判决与证明根 | 接受 / 拒绝 + 可读理由 |
| 能不能改判决 | 能（它是求解器） | **不能**（它只读） |
| 与编译器的关系 | 编译器是它的客户端 | **零 import** |

一句话：**LSPE 说"我证了"；LNotary 说"我核过你写下的那句话"。**

---

## 1. V0 核什么

只核产物内部**自洽**，不重新计算证据（`docs/358` §4.2 的四类里的前两类）：

| 检查 | 内容 | 错误码 |
|---|---|---|
| 版本 | `potato` 是 `v0`–`v23`；超过就拒 | `LNT-VER` |
| 系统索引 | `systems` 六个系统齐全；`state/detail/cost` 与它们指向的字段一致 | `LNT-SYS` |
| 回收台账 | `gc_ladder` 的自洽：`l0 + l0_5 + l1 + l2 + l3 == total_sites`（v19+ 五格，v18− 四格） | `LNT-GC` |
| 七级 | `lms_levels` 恰好七个布尔 | `LNT-LMS` |
| 预算 | `lms_budgets` 七个取值合法；`runtime = no_runtime` 时不许有 `state` | `LNT-BUD` / `LNT-RT` |
| LSPE 台账 | `lspe.state` 是字符串、`lspe.sites` 是非负整数 | `LNT-LSPE` |
| 开关 | `switches` 每条 `{name, on}`；不许重名 | `LNT-SW` |
| 声明 | 声明了 `lnotary_true` 就必须有 v20+ 的 `systems` 台账 | `LNT-CLAIM` |

**它不核对**：`proven` 站点是否真的被证明、`lspe.sites` 数得对不对、IR 里有没有漏站点。
那需要看到 IR 或源码，属于下一档（§6）。V0 的价值是：**产物自己跟自己打架时，第三方不读源码就能红。**

V1 加一层**旁挂**：`lawspace`。它把"产物声称了什么、公证人核了什么"写成可哈希的对象，
不改变上面的检查。形状见 §5.1。

---

## 2. 声明开关：`lnotary_true`

```rust
// 源里的写法（用户开关，不是核心槽）
set choose lnotary_true {
}
choose lnotary_true
```

它是一个**用户开关**，编译器把它原样记进 Potato 的 `switches`：

```json
"switches": [{"name": "lnotary_true", "on": true}]
```

为什么要用用户开关：

1. **不改自举编译器，不改种子。** 核心槽（如 `lspe_aicheck_true`）要两个实现 + 种子重算；
   种子改动必须串行，而 LVM 那条线正在并行。用户开关绕开了这条冲突。
2. **默认关。** 不写 `lnotary_true` 的产物一个字节都不变。
3. **可升格。** 等 LVM 线落地后，把它升成核心槽 `lnotary_true` 只差一次串行提交；
   `set choose` 的写法可以继续兼容。

代价明写：用户开关要**自己定义**（两行），不能只写 `choose lnotary_true`。
这是为了不碰种子付的价。

---

## 2.5 启用条件：`choose vm`

**LNotary 只在 `choose vm` 的产物上启用**（用户 2026-11-21 定）。

理由不是审美：公证人的强声明本来就用 LVM 的词说 —— 站点坐标 `(fn, blk, idx)`、
fault 码 `F01–F03`、`sw_cap` 基质。没有 `choose vm`，这些词在产物里没有对应的执行面，
公证会变成"用 LVM 的术语给另一件更弱的事盖章"。时间桥梁也需要两个基质可比；
产物不声明 `vm`，桥的一端是空的。

落点两处，所有入口共享同一条规则：

* `verify_potato` —— `vm != "vm"` ⇒ **`LNT-VM`**；
* `derive_lawspace` —— 同一个条件直接抛 `ValueError`（防直接调用绕过）。

两个边界要写明：

1. `docs/231` 的**发行版驱动器拒 `choose vm`**，所以这等于把 LNotary 定位成 **dev 工具链**的能力；
2. `choose vm` 是**声明**，不是"真的在 LVM 里跑过"的证明。运行证明要等 LVM 侧交出
   运行记录，那是下一档（不属于 V0–V5）。

---

## 3. 零 import 是一条判据

`tools/lnotary_test.py::test_verifier_imports_nothing_from_the_toolchain` 在子进程里
`import lnotary.verify`，然后断言 `sys.modules` 里没有：

```
lomentc · potato · potato_from · lspe
```

这条判据比"代码里没写 import"硬：**运行期也看不到它们**。
`lnotary/verify.py` 只 import 标准库（`dataclasses`）。

包的路径也照仓里的惯例：`loment/lnotary/` 以顶层名 `lnotary` 导入
（与 `loment/lspe` → `lspe` 同形），避免 `tools/loment.py` 遮蔽。

---

## 4. CLI

```
loment lnotary verify <potato.json> [--seal <lawspace.json>] [--sites <sites.json>]
                                [--universes <forest.json>] [--json]
loment lnotary seal   <potato.json> [--sites <sites.json>] [--out <lawspace.json>]
loment lnotary derive <lawspace.json> [--parent <universe.json>] --caps A,B
                                [--heap N] [--steps N] [--uid ID] [--out PATH]
```

- 接受 ⇒ 退出码 0，打印 `[LNOTARY] ok: … (potato v23[ + lawspace])`；
- 拒绝 ⇒ 退出码 1，逐条打印 `LNT-*` 与可读理由；
- 读不了 / 不是 JSON ⇒ 退出码 2。
- 产物声明了 `lnotary_true` 却没给 `--seal` ⇒ `LNT-CLAIM`。
- 产物没有 `choose vm` ⇒ `LNT-VM`（§2.5）；`seal` 与 `verify` 都拒。
- `--sites` 必须与 `--seal` 一起给；旁挂绑了站点表却没给、或给了没绑，都是 `LNT-LS`。
- `seal` 先跑 V0 与站点表自检；不通过就不出旁挂。旁挂是内容寻址的，
  同一份产物派生两次逐字节相同。

与 `loment lspe verify <artifact> <sidecar>` 并列。后者 import `lspe`；
前者**不 import**。两条命令放在一起，就是"被审计"与"审计"的分界。

### 4.1 旁挂的形状

```json
{
  "schema": 1,
  "potato": "v23",
  "artifact": { "sha256": "…", "bytes": 3157 },
  "dims": { "runtime": "no_runtime", "lms": "lms_full", "…": "…" },
  "dims_hash": "…",
  "laws": [
    { "id": "no_runtime", "declared": "no_runtime",
      "checked": true, "evidence": ["runtime", "lms_budgets"] }
  ],
  "sites": { "sha256": "<站点表的 seal>", "count": 4 },
  "genesis": { "mode": "same_artifact", "max_depth": 4,
               "max_children": 8, "max_total": 32 },
  "seal": "…"
}
```

`seal` 是对**除它自己以外**全部字段的规范序列化取 SHA-256；`dims_hash` 同理。
`laws` 只收录产物**真的声称**的三条之一（`no_runtime` / `lms_full` / `lifetime_auto`），
每条带 `checked` 与它用到的字段。`artifact.sha256` 把旁挂绑到产物字节，改一个字节就红。

---

## 5. 判据（二十九条，`tools/lnotary_test.py`）

| # | 判据 | 先红的方式 |
|---|---|---|
| A | 真实 v23 产物被接受（带 `choose vm`） | 验证器不存在 ⇒ 红 |
| A′ | `no_vm` 产物 ⇒ `LNT-VM`；`derive_lawspace` 抛 `ValueError` | 同上 |
| B | `lnotary_true` 是用户开关且进产物 | 同上 |
| C | 改 `systems.lms.cost.levels` ⇒ `LNT-SYS` | 同上 |
| D | 改 `gc_ladder.total_sites` ⇒ `LNT-GC` | 同上 |
| E | `no_runtime` + `state` 预算 ⇒ `LNT-RT` | 同上 |
| F | 少一个系统 ⇒ `LNT-SYS` | 同上 |
| G | 零 import（子进程查 `sys.modules`） | 同上 |
| H | 版本 `v999` ⇒ `LNT-VER` | 同上 |
| I | 旁挂派生确定（同产物两次 ⇒ 逐字节同） | 旁挂不存在 ⇒ 红 |
| J | 自产旁挂被接受 | 同上 |
| K | 改一条 law ⇒ `LNT-LAW` | 同上 |
| L | 改 `seal` ⇒ `LNT-SEAL` | 同上 |
| M | 换产物字节 ⇒ `LNT-ART` | 同上 |
| N | 改 `dims` ⇒ `LNT-DIMS` | 同上 |
| O | 声明 `lnotary_true` 而不给 `--seal` ⇒ `LNT-CLAIM` | 同上 |
| P | CLI `seal` → `verify --seal` 往返 | 同上 |
| Q | 真站点表被接受 | 站点表不存在 ⇒ 红 |
| R | 改一条站点等级 ⇒ `LNT-SITES` | 同上 |
| S | 旁挂绑站点表（`sites {sha256, count}`） | 同上 |
| T | 换一张站点表 ⇒ `LNT-LS` | 同上 |
| U | 站点表自己的封印被改 ⇒ `LNT-SEAL` | 同上 |
| V | 根的和绑（封印，初始授权） | 派生链不存在 ⇒ 红 |
| W | 子 caps ⊆ 父；提权 ⇒ `LNT-CAP` | 同上 |
| X | 子预算 ≤ 父；新增资源 ⇒ `LNT-BUD` | 同上 |
| Y | 深度 / 子数 / 总数 ⇒ `LNT-DEPTH` / `LNT-TREE` | 同上 |
| Z | 子的 sum 与环 ⇒ `LNT-SUM` / `LNT-CYCLE` | 同上 |
| AA | 旁挂 `genesis` 缺省与 mode 校验 | 同上 |
| AB | CLI `seal` → `derive` 根 → `derive` 子 → `verify --universes` 往返 | 同上 |

二十九条在本机全绿（`python tools/lnotary_test.py`，29/29，约 8.7s）。
登记处：`tools/ci.py` 的套件表与时间表、`.github/gate-floors.txt` 的 `lnotary_test 29`。

---

## 6. 下一档

| 档 | 加什么 | 依赖 | 状态 |
|---|---|---|---|
| **V1** | `lawspace` 旁挂与**封印哈希**（`docs/358` §4.1） | `docs/357` W1 | **已落地** |
| **V2** | 站点表与旁挂绑定（`docs/363`） | `docs/356` 站点坐标 | **已落地** |
| **V3** | `fault_ladder` 与 R1–R4 的复查（`docs/356` §6） | Dissigsegv 实装 | 下一档 |
| **V4** | `proven` 站点的规则引用必须可复查 | 规则表进产物 | 未开始 |
| **V5** | 同产物派生链（`docs/358` §3.2 的 `derive`） | `docs/357` W3 | **已落地** |

V2 刻意停在这里：**站点表只枚举 IR 里已经存在的访存，不冒充源码级完备。**
封印只绑产物、旁挂与站点表的字节，不假装证明了规律本身。
`docs/253` §8.1 的"拒，而不是退化"在公证人这里的读法是：
**核不了的东西不进这一档，不拿"设计上考虑了"冒充已经核过。**

---

## 6.5 同产物派生链（V5）

`loment/lnotary/derive.py` 把 PANGU 的"子宇宙不继承父宇宙的全部权限"落成四元组：

- **宇宙** = `(id, parent, seal, caps, budget, depth, sum)`；
- **根** `sum = H("lnotary-root/1" ‖ seal ‖ {caps, budget})` —— 初始授权也绑进来，
  `docs/358` 的"根 = seal"是它的简化读法；
- **子** `sum = H(父 sum ‖ {caps, budget})`；派生只允许**收缩**：
  `caps ⊆ 父`、`budget ≤ 父`、`depth = 父 + 1`；
- **树**：恰好一个根；每层子数 ≤ `max_children`、总数 ≤ `max_total`；环直接拒；
- 三个预算数住在**封印**里（`genesis`），子宇宙改不了它 —— 改封印就 `LNT-SEAL`。

这是**静态**定理：子能碰的东西不多于父。哪些 `unsafe` 站点在子权能下真的到得了，
是可达性分析，不在这一档（§7 第 6 条）。

---

## 7. 我不主张

1. **不主张 V0–V2 能证明产物安全。** 它只证明产物内部没有假账；
   封印证明"这份旁挂绑的是这份产物"，不证明规律本身成立。
2. **不主张零 import 等于零缺陷。** 它证明没有共享代码路径；共享的**规格理解**
   仍然可能两边一起错（`docs/349` 的三方判据是那只补丁）。
3. **不主张 `lnotary_true` 已经升格为核心槽。** 它今天是用户开关，要两行定义。
4. **不主张它替代 `loment lspe verify`。** 两条命令看的是不同层面：一条查证明根，
   一条查索引与台账的自洽。
5. **不主张现在扩到源码级完备。** 那要站点表与 IR 解码（`docs/358` §1A）。
6. **不主张派生链证明可达性。** V5 证明"子能碰的东西不多于父"（静态）；哪些 `unsafe`
   站点在子权能下真的到得了，是另一条分析，不在定理里（`docs/363` §7）。
7. **不主张 LNotary 今天能用于发行版产物。** 发行版驱动器拒 `choose vm`（`docs/231`），
   所以它是 **dev 工具链**的能力；另外 `choose vm` 是声明，不是运行证明（§2.5）。

---

## 8. 与其它文档的关系

* `docs/358` §4.2 —— 独立验证器的设计。本文是它的第一档落地。
* `docs/357` §4.2 —— 三件增量之一；本文只落"验证器"这一件。
* `docs/262` / `docs/344` —— LSPE 的产出侧。本文是它的第三方对照面。
* `docs/147` —— Potato 台阶。V0 的版本门（`v0`–`v23`）照它写。
* `docs/353` / `docs/356` —— Dissigsegv 与 `fault_ladder`；V2 的输入。
* `docs/349` —— 语义 oracle。零 import 挡不住"两边一起错"，那一条要靠三方判据。
