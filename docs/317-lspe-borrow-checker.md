# 317 · LSPE 借用检查器（E10）：把 loan 变成一等对象

> 状态: **A0/A1 参考侧已落地（报告面）**（2026-11-09）· 上游: `docs/262`（LSPE）· `docs/299`（Tur 2026.3
> 生命周期）· `docs/305`（2026.2 封版）· `docs/306`–`308`（E9 域检查器，形状先例）
> 一句话: 在 LSPE 里加**第十个引擎**，把 v1 的"借用活到函数末尾"换成**以 loan 为
> 一等对象的流敏感分析**；它只产证据，不投票，证不出来的每一条都变成问题与地板。
>
> **本文不落任何代码。** 落地阶梯在 §9，每步的判据先红后绿；`lom/*.lom` 一个字节
> 都不动（这是编译器内部的分析，不是 L0 契约）。

---

## 0. 先说清楚"不做"

1. **不做 Rust 的第二个实现。** 目标不是"Rust 能编的这里都能编"，而是：
   `docs/299` §7 的**四类失败**（悬垂、别名、释放后使用、自引用）在 LSPE 里
   **各有一条可证伪的路**，证不出来的部分**有名字**。
2. **不引入 Datalog / 外部求解器。** 见 §2 的第一条教训：legacy Polonius 就是死在
   这一条上。E10 是 LSPE 工作列表里的一个 `solve()`。
3. **不做视图借用（字段级部分借用）**：Rust 自己也还在实验（§2.4）。今天的逃逸舱
   仍然是 `ptr`（`docs/299` §4：裸指针不受区域检查，责任在写的人）。
4. **不把运行期别名模型塞进编译期。** Tree Borrows 是 Miri 的模型，不是借用检查器
   的替代品（§2.3）；在 LSPE 里它最多是 `Differential`，而 `Differential` **永不**
   销义务（`docs/262` §5.3）。
5. **不新增错误码**，除非某条规则在既有码里确实没有家（§7 说明今天不需要）。

---

## 1. 起点：v1 有什么，缺什么

`docs/299` §8 的 v1 已经落地（两侧逐字节）：

* 规则一句话：**借用的区域不能比来源长**；实现是一个**逃逸分析**
  （`tools/lomentc.py` 的"生命周期"一节；自举侧 `checker.lomt` 的 `chk_life`）。
* 五条诊断：返回局部借用（E012）、无引用形参却返回借用（E041）、区域标记写错
  （E041）、`free` 时仍被借（E041）、存下来的 `&mut` 重叠（E007）。
* 函数摘要按 `docs/299` §3.2 的省略规则算出（一个引用入参 → 全是它；多个 → 取
  最短；`&a T` → 它）。

**v1 的边界是明写的**：不做 NLL —— "存下来的借用活到函数末尾"（`docs/299` §8）。
这条边界买来了两套实现的可镜像性（自举侧只有 token 流），代价是两种错误：

| 形状 | v1 的判决 | 应该的判决 | 这就是 Rust 的哪道题 |
|---|---|---|---|
| 借用在前一支里用、后一支里**已经死了**，后一支要 `&mut` | **拒**（假阳性） | 收 | NLL Problem Case #3 |
| `map` 里"取不到就插入、再取一次" | **拒**（假阳性） | 收 | Polonius Alpha 的样板 |
| `v.push(v.len())`（外层 `&mut` + 内层 `&`） | **拒** | 收 | 两阶段借用 |
| 借用在 `drop`/`free` 之后仍被用 | 部分拒 | 拒 | #164250 的同类 |

所以 E10 的目的**不是更严**，而是**更准**：把 v1 的"整个函数"细到"最后一次使用"，
同时保证每一条新收下的程序都有证据、每一条拒掉的都能指到 loan 与来源。

---

## 2. 外部对照：三代借用检查器与两次模型改革

### 2.1 NLL（2018 起，stable 至今）

* 区域 = **程序点的集合**，区域约束（outlives）求解一次，**流不敏感**：一个引用
  只要在函数的某条路径上活到返回，就被当成在所有路径上都活。
* 买到了"借用不再活到词法块末尾"，没买到 Problem Case #3（条件返回）：一个返回
  类型里的引用会把整条路径的借用都拉活。
* **两阶段借用**是 NLL 里的特例补丁（2017 设计，2018 重做）：`&mut` 分成
  reservation / activation 两段，**只对自动引用（autoref）生效**。它证明了一件事：
  同一个语义缺口，补丁会以"特例"的形式回来。

### 2.2 Polonius legacy（2018–2023）：Datalog 原型被放弃

* 核心思想换了一根轴：**origin = loan 的集合**（不是程序点区间），在每个 CFG 点
  上算 loan 的子集关系 —— 天生流敏感。
* 原型用 Datalog（Datafrog）写在 nightly 上，精度更高，但**规模不可救**：它把所有
  flow-sensitive 需要的事实都展开，编译时间不可接受。2023 年的 Inside Rust 更新
  与项目目标页直接写"**没有通向 stable 的路径**"；EuroRust 2024 的复盘（同年的
  IWACO 论文）标题就是"Polonius 的头六年"，其中一段专讲 Datalog 这条路怎么被
  放弃。
* **教训（本设计的第一条硬约束）**：精度没有成本模型就不算设计。E10 必须在既有
  步数预算（`docs/262` §5.12）里跑完整套语料，否则它连"候选"都不是。

### 2.3 Polonius Alpha（2026-08-04 起 nightly 默认开，stable 前夜）

公开发布的说法（Rust 官方博客，2026-08-04）：

* 在**既有 NLL 实现**上做增量改造（2023 起），只多算"按控制流路径的存活"，
  于是条件返回、`get_mut` 取不到再插入的 map 形状、"两个分支分别返回重借用或
  原借用"都能编过。
* 成本：严格等于或高于 NLL；top-10k crate 里显著回归很少（1% 阈值），**top-10k
  之外最坏 2–3×**；`-Zpolonius=off` 可关，官方请关的人写原因。
* **它既不是 legacy Polonius 的超集，也不是 NLL 的超集**：有的 legacy 收的程序
  Alpha 拒（例如循环里给字段反复赋一个新的 `&mut`），也有的 Alpha 收、legacy 拒。
* **没有迁移工具**；stable 仍走 NLL，Alpha 只在 nightly。
* 2026-10 还曝出一个**11 年的 soundness 洞**（`rust-lang/rust#164250`）：带
  `T: 'static` 约束的结构体，析构器信任这个约束，但字段可以被后赋值塞进短命引用，
  析构时把短命引用偷渡到全局 —— 全程没有 `unsafe`。修复是 MIR 上加一条检查
  （#164277）。**教训**：析构是借用语义的一部分，不是"另一条线"。

### 2.4 运行期别名模型：Stacked Borrows → Tree Borrows

* Tree Borrows（PLDI 2025 杰出论文）把"栈"换成"树"，在 30,000 个 crate 上比
  Stacked Borrows **少拒 54% 的测试用例**，还证明了它保留大多数既有优化。
* 它已并入 Miri，与 Stacked Borrows 并存、可配置；但它回答的是"这次运行有没有
  UB"，不是"这份源码该不该编过"。**过严的模型会被用户绕过**，这是校准问题，
  不是正确性问题。

### 2.5 视图借用（字段级部分借用）

Rust 的 view types 仍在实验（tracking issue #155938；社区汇总称目标完成时间
2027 年 4 月左右，尚未排期 stable），语法都还没定。跨函数的部分借用今天只能靠"拆结构体/传两个
引用"绕开。E10 **不追**这一格；等它定了再评估。

---

## 3. 七条教训 → 七条设计约束

每一条都写成"外部事实 → 约束 → 在本设计里落在哪"。

| # | 事实 | 约束 | 落点 |
|---|---|---|---|
| L1 | Datalog 原型精度高但规模不可救，六年没进 stable | **E10 不引入外部求解器**；精度必须落在既有的**步数预算**里，超了报 Q005 | §6.4 |
| L2 | NLL 流不敏感 ⇒ 假阳性，用户被迫改写或去 `unsafe` | **流敏感是最低要求**；但只在**能给出证据**的范围内流敏感，其余提问 | §4、§5.3 |
| L3 | 两阶段借用是特例补丁，补丁会回来 | 特例要么**建模成一等 loan 阶段**，要么不做；不许"这里特判一下" | §4.6 |
| L4 | Alpha 不是超集，双向都有 delta，且没有迁移工具 | **旧版次一个字节都不变**（`docs/305`）；新判决必须进 **delta 表**，按版次分别跑语料 | §8.3 |
| L5 | Rust 2024 的 RPIT 捕获规则改了语义，大量代码红；APIT 还无法自动修 | 语义变更**不许搭版次的便车**：要 lint、要能自动修、要有显式 opt-out | §8.4（`choose lifetime_*` 已是 opt-out） |
| L6 | #164250：析构器 + 协变字段赋值，11 年没被发现 | **析构是 loan 的 kill 点**；`DropHonest` 与 `FreeOwnership` 交叉核 | §4.3、§7 |
| L7 | Tree Borrows 比 Stacked Borrows 少拒 54% | **过严的模型会被绕过**；编译期只证可证的，运行期模型不冒充证据 | §0.4、§5.3 |

> 还有一条是仓内的：`docs/262` §5.4 的引擎**只产证据、不投票**，引擎之间不互相
> 否决（E8 的反例除外）。E10 照这条写，所以它是 `solve()` 而不是第二个检查器。

---

## 4. 模型：loan 是一等对象

### 4.1 三个原子

```
place  一个可在两个相邻程序点之间"被记住"的位置：局部、形参、字段路径（只下探一层）
loan   一次借用：{ id, place, kind ∈ {shared, mut}, phase ∈ {reservation, activation},
                   parent_loan?, create 点 }
point  一个程序点：站点 id（内容寻址，与 LSPE 其余站点同一套）
```

**loan 不写进类型**：类型里仍然是 `&T` / `&mut T`（`docs/299` §3）。loan 是分析
对象，不是语法；用户不写、也看不见它的名字，只在诊断里被点名（§7）。

### 4.2 区域 = loan 的集合，不是程序点的区间

这是 Polonius 换的那根轴，也是 Problem Case #3 的解：

```
region(p) = { L | L 在点 p 仍然存活 }
```

* `&x` 在点 `p` 存活 ⟺ 存在一条从 `p` 出发的路径，在**没经过 L 的 kill** 之前
  用到了以 L 为来源的引用；
* "用到"包括：解引用、把引用传出去、把引用存进结构体/返回、比较引用；
* **不用**：赋值给一个从未再被读的变量。

因为集合是按点算的，**同一个 loan 可以在一条分支上活、在另一条上死** —— 这正是
v1 做不到的那一格。

### 4.3 kill 点（L6 的落点）

一个 loan 在下列任一事件上**死**：

1. 来源 place 被**移动** / 重新赋值；
2. 来源 place 被 `free`（`docs/299` §3.5 的编译期诊断在这里变成证据）；
3. 来源 place 的**析构器运行**（`drop` 站点；这就是 #164250 的同形）；
4. 引用变量自己的作用域结束（storage dead），且它没有被存进更长命的东西。

第 3 条把 `DropHonest` 拉进借用检查：析构器如果被信任（`Assumed`），它同时也是
loan 的 kill；若某条路径上析构先于使用，那就是 `Alive` / `NotDangling` 不成立，
**不是**"析构器的事，借用检查器不管"。

### 4.4 冲突关系

| 两条 loan | 同 place | 不同 place（可证不相交） |
|---|---|---|
| shared + shared | 允许 | 允许 |
| shared + mut | **冲突** | 允许（字段级只下探一层，见 §4.7） |
| mut + mut | **冲突**（重借用除外，§4.6） | 允许 |

"冲突"只在**两者在同一个点上同时存活**时成立（v1 是"整个函数"，这里换成点）。

### 4.5 存活是反向数据流

对每个函数做一次**反向**存活分析：

```
live_out(p) = ⋃ over successors s of live_in(s)
live_in(p)  = (live_out(p) − kills(p)) ∪ uses(p)
```

* `uses(p)`：点 p 上用到的 loan（含它派生的重借用）；
* `kills(p)`：点 p 上死掉的 loan；
* 迭代顺序：**逆后序（reverse postorder）**，手写固定；工作列表是有序队列
  （`docs/262` §5.5 的确定性纪律同一套）。

这是一个**标准数据流**，不需要 Datalog，也不会像 legacy Polonius 那样展开所有
事实：集合大小受每函数 loan 数上限约束（`docs/262` §5.12 的容量表加一项）。

### 4.6 重借用与两阶段借用（L3 的落点）

* **重借用**：`&mut *r` / `f(&mut r.field)` 产生一个 `parent_loan = L` 的新 loan
  `L'`；`L` 在 `L'` 活着期间进入"被冻结"状态（`L` 的读允许，写不允许）。
  作用域结束时 `L'` 死，`L` 解冻。
* **两阶段**：自动引用方法调用 `recv.m(args)`（`recv: &mut T`）产生
  `phase = reservation` 的 loan；`args` 里的借用先算，`activation` 在调用真正
  发生时。于是 `v.push(v.len())` 合法。**只对 autoref**：和 Rust 一样，不做
  `-Z two-phase-beyond-autoref` 那种全域版本（那是已知会被诊断淹没的特例）。
* 两个阶段都是**loan 的字段**，不是检查器里的 `if` 分支 —— L3 的约束。

### 4.7 不做字段级（视图）借用的后果，明写

今天的 place 只下探**一层**（`r.field`），但"不同字段互不冲突"**不证**：两个
`&mut` 指向同一结构体的不同字段仍然冲突。这与 Rust 今天的跨函数行为一致
（view types 未稳定）；逃逸方式是把字段分别借出去，或用 `ptr`。

---

## 5. 接进 LSPE：站点、义务、证据、问题、LSPC

### 5.1 站点（`LSPE_SITE_KINDS` 加四种）

| 站点 | 参数 | 说明 |
|---|---|---|
| `loan` | `place, kind, phase` | 借用的创建点（`&x`、`&mut x`、autoref） |
| `loan_use` | `loan_id` | 引用被使用（解引用 / 外传 / 存储） |
| `loan_kill` | `loan_id, cause ∈ {move, free, drop, scope}` | 借用的死亡点 |
| `loan_conflict` | `loan_a, loan_b, point` | 同一 place、同一点、互斥的两种存活 |

加站点要同时改三处：注册表、`census.py` 的普查、自举侧的站点扫描；I1（站点
覆盖）会盯住"台账里的站点都在注册表里"。

### 5.2 义务：**不新增**，复用已有的五条

`docs/262` §5.2 的纪律是"义务不合并、每条有语义所有者"。E10 需要的东西**恰好
已经都有**：

| 义务 | E10 给的证据 | 语义所有者 |
|---|---|---|
| `Exclusive` | 冲突点上的 loan 对；重借用冻结 | `docs/206 / E007` |
| `NotDangling` | 返回/存储的 loan 的 kill 是否早于使用 | `docs/206 / E012` |
| `Alive` | 使用点之后来源是否还活（含析构，§4.3） | `docs/210 / E029` |
| `Initialized` | 引用变量在首次使用前被赋值 | `docs/206 / E006` |
| `FreeOwnership` | `free` 与仍存活的 loan 的交 | `docs/210` |

> **为什么不再是第六条 `BorrowLive`**：`NotDangling` + `Alive` 已经覆盖"借用在
> 用点上还活着"的语义，新增一条会让同一个语义有两个所有者 —— 那是
> `docs/262` §5.2 明令禁止的"义务合并"的反面。

### 5.3 证据种类与地板

* `LocalFlow`：§4.5 的流敏感集合（主证据）；
* `Interproc`：§4.8 的函数摘要（调用点的 loan 需求）；
* `GlobalInvariant`：循环归纳（§4.9）；
* `Assumed(id)`：证不出来但用户选择放行 —— **进 `floor_detail.assumed`**，
  LSPC 点名，不是静默。

**E10 永不产生 `Differential`**（那是 E7 的事），也永不因为"两侧一致"就销义务。

### 5.4 反问器：新增三问

每一问都按 `docs/262` §6 的固定字段（`claim/chain/tried/options/default/reversible`）
写，`tried` 必须覆盖三问（来源 → 反证 → 对质），否则 E036。

| 码 | 问什么 | 什么时候问 | 默认 |
|---|---|---|---|
| **Q015** | 这个返回的借用**来自哪一个入参**？（条件返回，来源不唯一） | §4.5 算出的来源集合有两个以上，且没有 `&a T` 标记 | B：接受并标注（一条 `Assumed`） |
| **Q016** | 这次方法调用是**保留**还是**激活**？ | 同一 receiver 上 reservation 与 activation 之间出现冲突，无法用 autoref 证明 | B：按两阶段收，记一条地板 |
| **Q017** | 这个借用要**跨迭代**活着吗？ | 循环体的 live-in 里有循环外创建的 loan，且归纳形状不认 | C：拒（循环里存借用） |

Q009（别名不确定）保留给"place 不可判"的情形，不和新三问抢语义。

### 5.5 LSPC：四个新家族

| id | 家族 | 保护什么 | 失败会怎样 | 风险 |
|---|---|---|---|---|
| `loan_conditional` | 条件返回的借用来源 | Q015 的接受 | 来源错认 ⇒ 悬垂 | medium |
| `loan_two_phase` | 方法调用的两阶段借用 | Q016 的接受 | 保守拒（不 UB） | low |
| `loan_loop` | 循环携带的借用 | Q017 的拒 | 悬垂 / 借用逃逸 | high |
| `loan_escape` | 跨单元摘要未证（L4 未开） | 库函数返回的 loan | 跨库假设错 | high |

`loan_escape` 的诚实点与 `cross_unit` 相同：L4 没开之前，库函数的摘要只能是
`Assumed`。**不假装验过。**

### 5.6 一次完整走查（Problem Case #3 的 Loment 形状）

```rust
fn bump(m: &mut Map, k: u32) -> u32 {
    let cur: &u32 = get(m, k);       // loan L1(shared, m)
    if *cur > 0 {
        return *cur;                 // L1 在这里活
    }
    insert(m, k, 1);                 // loan L2(mut, m)：L1 在这条路径上已死
    return 1;
}
```

| 站点 | 义务 | 证据 | 判决 |
|---|---|---|---|
| `loan L1` | —（创建不是义务） | — | — |
| `loan_use(*cur)` | `NotDangling` | `LocalFlow`：L1 的 kill 在 return 之后 | PROVED |
| `loan L2` | `Exclusive` | `LocalFlow`：冲突点集合为空（L1 不在 insert 点存活） | PROVED |
| `return *cur` | `NotDangling` | `LocalFlow`：解引用点上 L1 仍存活 | PROVED |

v1 在这一格会报 E007（L1 活到函数末尾）；E10 收，而且**每一步都有站点**。
如果 `get` 是**跨单元**函数、摘要未知，第三行仍然 PROVED（标量），但 `loan L1`
的来源会是 `Assumed` + `loan_escape` 一行。

---

### 5.7 温柔的形状：问与拒的边界

LSPE 的"温柔"不是"不拒"，而是**判决只出三种，每一种都带收据**：

| 判决 | 什么时候 | 用户看到什么 | 可逆吗 |
|---|---|---|---|
| `PROVED` | 有证据（`LocalFlow` / `Interproc` / `GlobalInvariant`） | 台账里的一行来源 | —— |
| `UNDECIDED` | 证据不足，且**没有反例** | Q015/Q016/Q017 + LSPC 一行 + `floor_detail.assumed`；默认 **B（接受并标注）** | 是（改答案 / 加标注 / strict） |
| `REFUTED` | **构造性反例**（悬垂、冲突点具体存在） | E007/E012/E041，点名 loan、来源、使用点、kill 点与改法 | 否（编译停止） |

三条硬规则：

1. **"证不出来"不许变成"拒"。** 拒只在有反例时（`docs/262` §5.4 硬规则 3 的同一条：
   构造性证据优先于一切非构造性证据）。
2. **默认是收，不是拒。** `UNDECIDED` 的默认 B 是"接受并标注"，问题本身**不阻断**
   （`--lspe-mode=strict` 才把未答升级成 E034；那是用户的显式选择）。
3. **拒也要温柔**：每条拒绝必须说清"哪一笔 loan、哪一处来源、哪一处用、哪一处死、
   怎么改"（§7）。只说"生命周期错误"的拒绝不算温柔，算失职。

**但有一条不温柔的边界必须写明白**：编译期没有运行期兜底。默认档是 `no_runtime`，
悬垂引用一旦被放过去就是 UB —— LMS 的梯子（跳过 / 崩溃 / 重启）救不了**编译期**
已经放行的借用错误。所以：

* 有反例的悬垂、别名**必须拒**，不提供"问一下再放行"；
* **Q017（循环携带借用）默认 C 拒**，不是 B 收 —— 它的失败形状是悬垂，且运行期
  没有可查的证据；
* Q015 / Q016 默认 B，因为它们的失败形状**看得见、回得去**（改写法 / 加标记），
  并且每次都出现在 LSPC 与台账里；
* `lspe pass`（Q013）不是"免检"：它跳过的是审问，收据照记；`lifetime_no` 是版次级
  的显式关闭，不是逐站点的沉默。

换句话说：**能问的都问，问到答案之前不替用户拒；但一旦有反例，编译器不再问 ——
反例是它唯一有资格拒的东西。**

## 6. 算法、预算与确定性

### 6.1 分析顺序（固定）

1. 词法：place、loan、use、kill 的候选点（两侧同一套规则）；
2. 块骨架：`if/else`、`while`、`for`、`return`、`break`、`continue`；
3. 每函数反向存活（逆后序，有序工作列表）；
4. 摘要：返回借用的来源集合（`docs/299` §3.2 的省略规则，**先算摘要再算调用点**）；
5. 调用点实例化：把摘要里的"来自第 i 个入参"映射到实参的 loan；
6. 冲突检查：同 place、同点、互斥存活 ⇒ `loan_conflict` 站点；
7. 循环：一层归纳（`for i in A..B` / 无符号 `while i < B` 等，`docs/262` E9 的
   同一组形状）；认不出 ⇒ Q017。

### 6.2 自举侧怎么活

自举检查器是 **token 级、没有 AST**。E10 在自举侧只承诺**同一份块骨架**：

* 括号配平 + `if/else/while/for/return/break/continue` 的 token 形状；
* place 只认"局部名 / 形参名 / `名字.字段`"；
* 任何超出骨架的形状（`orelse`/`catch` 里藏借用、嵌套函数、宏式展开）**不进
  E10**，直接落 Q015 + `Assumed` —— **不静默跳过**。

这条与 `docs/299` §6 的"自举侧是迄今最大一笔改动"一致：E10 只在**两侧都能表达**
的子集上要求逐字节一致；子集之外由参考侧单独判据，且台账里标 `Assumed`。

### 6.3 确定性（沿用 `docs/262` §5.5）

* 引擎顺序手写固定；工作列表有序；同分证据按 site id 字典序；
* 同一棵树跑两遍，台账与 QIR 序列化**逐字节相同**（进 §9 的判据）；
* 站点 id 与既有站点同一套 `sid(kind, key)`（内容键，不掺时间与路径）。

### 6.4 预算（L1 的落点）

| 量 | 上限 | 越界 |
|---|---|---|
| 每函数 loan 数 | 4096 | Q005 + 台账 `budget` |
| 每单元求解步数 | 65536（既有） | Q005 |
| 过程间深度 | 6（既有） | Q006 |
| 每点存活集合大小 | 受 loan 数约束，不单独设 | — |

**按步数，不按墙钟**（`docs/262` §5.5 的同一条）。语料里最坏的一份单元要在预算
内跑完；跑不完是**红灯**，不是"降级跳过"。

---

## 7. 诊断：每一句都指到 loan 与来源

| 形状 | 码 | 消息必须点名 |
|---|---|---|
| 返回局部借用 | E012 | 局部名、loan id、返回点、kill 点 |
| 存进活得更久的容器/全局 | E041 | 容器的 place、loan id、两处行 |
| `free` 时仍有借用 | E041 | `free` 行、loan id、最后一次使用行 |
| `&mut` 与仍存活的借用冲突 | E007 | 两个 loan id、冲突点、最早的 kill 点 |
| 区域标记写错（不存在 / 非引用 / auto 档写） | E041 | 标记指向的名字、签名行 |
| 析构先于使用 | E041 | 析构点、loan id、使用点 |
| 条件返回来源不唯一 | Q015（LSPC 条目，不阻断） | 候选来源列表、每一处行号 |

**禁止**："生命周期错误"、"借用检查失败"这类不指来源的话（`docs/299` §5 已立规矩）。

---

## 8. 双实现、语料与迁移

### 8.1 两个实现的分工

| 侧 | 落点 | 承诺 |
|---|---|---|
| 参考 | `loment/lspe/census.py` 加 loan 普查；`solver.py` 只加证据来源 | 全语料；块骨架之外的形状走 Q015 |
| 自举 | `loment/selfhost/checker.lomt` 的 token 扫描 + `lspe.lomt` 的站点计数 | 只承诺 §6.2 的子集，逐字节一致 |
| 孪生 | `loment_rule_parity.py` 新探针 + `loment_p8_test` | 同一份源两个实现判决相同 |
| 差分 | E7（`Differential`） | **永不销义务**，只对质 |

### 8.2 语料（每条先红后绿）

正例（必须收）：

* `bump` / `get_mut` 取不到再插入（§5.6）；
* `v.push(v.len())` 的两阶段；
* 两个分支分别返回重借用或原借用；
* 借用存进结构体、结构体随函数返回（摘要链）；
* 循环里每次迭代新建、迭代结束即死的借用。

反例（必须拒，且报对码）：

* 返回局部借用（E012）；
* 返回后使用已 `free` 的借用（E041）；
* 同一 place 两个 `&mut` 重叠（E007）；
* 借用跨迭代逃逸（Q017 / E041）；
* 析构之后仍使用（E041，L6 的同形）。

**Rust 派生形状**单独一列，附"已知 delta"：例如"循环里给字段反复赋新 `&mut`"
（legacy Polonius 收、Alpha 拒）在 Loment 的判决要写清楚，**不追平**任何一方。

### 8.3 迁移：按版次跑语料，旧版次一个字节不变

* `tur2026.1` / `tur2026.2`：`lifetime_no` 默认，E10 **不启用**，产物逐字节不变
  （`docs/305` 的封版承诺是硬的）；
* `tur2026.3`：`lifetime_auto` 默认，E10 从 A0 起逐级开；
* **delta 表**：同一份语料在 v1 与 E10 下的判决差，逐条登记（收 / 拒 / 码），
  公开在文档与台账里。L4 的教训是"没有迁移工具时，用户只能自己撞"；
* 不提供"旧算法模式"开关：`lifetime_no` 是那个开关，颗粒度到版次，足够。

### 8.4 不搭版次便车（L5 的落点）

任何**会改变旧版次判决**的修改都不许混进 E10：先加 lint（`loment diag` 里能看见），
再给自动修（能修的形状），最后才在下一版次默认。Rust 2024 的 RPIT 捕获规则是
反面教材：它改了默认语义，`cargo fix` 只能修一部分（APIT 无法自动修），用户看到
的是一串 E0499/E0502。

---

## 9. 落地阶梯

每一步都要求：两侧同判决、判据先红、台账字段进 Potato、**没有静默**。

| 步 | 内容 | 先红的判据 | 出口 |
|---|---|---|---|
| **A0** | loan/use/kill 站点进台账；**判决不变** | 台账里每个 `&x` / `&mut x` 都有一个 `loan` 站点；两侧计数相同 | Potato 字段齐、`lspe state` 列得出 |
| **A1** | 流敏感（if/else）+ kill=最后使用 | §5.6 正例在 v1 下红（E007）、在 A1 绿；反例仍红 | 条件返回、entry 形状通过 |
| **A2** | 循环归纳 + Q017 | 跨迭代借用反例红；迭代内借用正例绿 | `loan_loop` 进 LSPC |
| **A3** | 重借用 + 两阶段 + Q016 | `v.push(v.len())` 在 v1 红、A3 绿；假两阶段反例红 | `loan_two_phase` 进 LSPC |
| **A4** | 跨单元摘要（L4）+ Q015 的完整问答 | 库函数返回借用、调用点来源可追 | `loan_escape` 从 LSPC 里消失或明确保留 |

**A0 是纯台账步**：它不改任何判决，所以可以先落、先测、先让用户看见——这与 E9
的落地顺序同形（先把事实变成站点，再让事实改变判决）。

---

## 9.5 落地记录（2026-11-09）：A0/A1 的参考侧

这一轮落了**分析核心与查看入口**，没有接 Potato 台账，也没有动自举侧：

| 内容 | 位置 | 判据 |
|---|---|---|
| loan 图（place / create / kill / uses） | `loment/lspe/loans.py` | `test_loan_sites_are_first_class_and_deterministic` |
| kill = **最后一次使用**（A1） | 同上 | `test_flow_sensitive_kill_accepts_the_conditional_case` |
| 同 place、区间相交、一方 `mut` ⇒ 冲突 | 同上 | `test_conflict_when_a_shared_loan_outlives_a_mut_borrow`、`test_shared_loans_do_not_conflict` |
| 悬垂（返回到局部的借用） | 同上 | `test_dangling_return_names_the_local` |
| `free` 时仍有借用 | 同上 | `test_free_while_borrowed_is_reported` |
| 站点/三问/LSPC 四族登记 | `loment/lspe/registry.py` | `test_loan_registry_entries_are_complete` |
| 查看入口 `loment lspe loans FILE [--json] [--strict]` | `tools/loment.py` | `test_loans_cli_is_a_report_and_respects_lifetime_no` |

### A0b（同一页的下一步，2026-11-09）

`lspe.loans` 已进 Potato：`{total, shared, mut}`，只数**借用表达式**（创建），
不产生义务、不加地板。参考侧在 `ledger.build` 里调 `loans.count`，自举侧是
`lspe.loans` 的 token 扫描（`lspe.lomt`）+ `potato.lomt` 的 `PT_LOANCNT` 三格；
两侧逐字节相同（`loment_potato_emit_test` 9/9）。

计数规则 R（两侧逐条同形）：只看 `&`；前一个是 `:` 或 `->` 的是类型位置；
`&mut` 看下一个是不是 `mut`；再下一个必须是标识符且不是 `self`。

**为什么先不接台账**：`docs/262` 的孪生要求参考侧与自举侧的台账逐字节一致。
loan 站点一旦进 Potato，自举侧必须同计数，而自举检查器是 token 级、没有 AST ——
那是一次独立的、要重算种子与 genesis 的改动。所以这一轮把**判决**做成报告面，
默认构建的产物与台账一个字节都不变；下一步才是 A0b（两侧同计数）与 A2/A3。

**已知边界**（与 §4.7、§6.2 一致）：字段级借用不区分；重借用/两阶段未建模；
跨函数摘要是 A4，`get(m, k)` 这类返回借用目前**不产生 loan**。

## 10. 我不主张

1. **不主张与 Rust 等价**。等价的是**失败模式的覆盖**（§0.1），不是"同一份程序
   两边同判"。
2. **不主张 Alpha 级精度**。L1 的预算约束意味着精确度有上限；超出的形状进 Q015。
3. **不主张"更严就是更安全"**。L7：过严会被绕过；每一条新拒都要有一条对应的
   "用户怎么改"（§7 的最后一列）。
4. **不主张跨单元已经验过**。A4 之前，库摘要只有 `Assumed`。
5. **不主张字段级借用**。§4.7 是不做，不是"以后顺手"。

---

## 11. 开放问题

1. **Q015 的默认**该是 B（接受并标注）还是 C（拒）？Rust 在 Problem Case #3 上
   选了"拒 + 让用户改写"，Alpha 选了"收"；Loment 的默认档是 `lifetime_auto`，
   收的代价是可能的误收。**建议先 B，并在 delta 表里标出来**，A4 再复核。
2. **两阶段是否只对方法**？Rust 的教训是通用两阶段会淹没诊断；Loment 的方法
   调用形状固定（`recv.m(args)`），先只做这一格。
3. **`ptr` 与借用的交界**：`ptr` 逃逸舱不受 E10 管（`docs/299` §4），但把
   `&T` 转成 `ptr` 再转回来是**用户显式承担**还是**编译期拒绝**？建议后者在
   `lifetime_auto` 下做一条 E041，`manual` 下放行并要求标注。
4. **与 LMS 的重叠**：LMS 的运行期检查（g1 界检查）与 E10 的编译期证明是两层；
   同一个访存点两条证据并存时，台账怎么记？建议 `LocalFlow` 与 `lms(g0)` 各记
   一条，不合并（`docs/262` §5.2 同一条）。

---

## 12. 引用

外部资料（2026-11-08 查）：

* Rust 官方博客：Enabling the next iteration of the borrow checker on nightly
  （2026-08-04，Polonius Alpha）——
  https://blog.rust-lang.org/2026/08/04/enabling-polonius-alpha-on-nightly/
* Inside Rust：Polonius update（2023-10-06，Datalog 原型的规模问题）——
  https://blog.rust-lang.org/inside-rust/2023/10/06/polonius-update/
* Rust project goals：Stabilizable Polonius support on nightly ——
  https://rust-lang.github.io/rust-project-goals/2025h2/polonius.html
* EuroRust 2024：The first six years in the development of Polonius ——
  https://eurorust.eu/2024/talks/the-first-six-years-in-the-development-of-polonius/
* Tree Borrows（PLDI 2025，Villani/Hostert/Dreyer/Jung）——
  https://dl.acm.org/doi/full/10.1145/3735592
* rust-lang/rust#155938（view types tracking issue）——
  https://github.com/rust-lang/rust/issues/155938
* Rust edition guide：RPIT lifetime capture rules（Rust 2024）——
  https://github.com/rust-lang/edition-guide/blob/master/src/rust-2024/rpit-lifetime-capture.md
* rust-lang/rust#164250（11 年 soundness 洞，2026-10-13）——
  https://github.com/rust-lang/rust/issues/164250

仓内（读过的设计，不是引用）：

* `docs/262`（LSPE：义务、证据、引擎、LSPC、地板）
* `docs/299`（Tur 2026.3 生命周期：v1 的五条规则与明写的边界）
* `docs/305`（2026.2 封版：旧版次一个字节都不变）
* `docs/306`–`308`（E9 域检查器：站点化 + 台账 + LSPC 的先例）
* `docs/253` §1.2（地板与假设的记账）
