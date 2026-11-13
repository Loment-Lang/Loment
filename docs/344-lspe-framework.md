# 344 · LSPE 框架：宏观检查器、调度器与两档 SDK

> 状态: **设计稿**（2026-11-13）
> 基准: `origin/main`（`663b93ff`，2026-11-13）
> 上游: `docs/262-lspe-safety-police-espace.md`（LSPE）· `docs/317-lspe-borrow-checker.md`（E10）·
> `docs/320-capability-temporal-safety.md` · `docs/328-authority-temporal-safety.md` ·
> `docs/345-loment-classes-and-indirect.md`（类继承/间接引用，本框架的地基）·
> `docs/253`（LMS）· `docs/210`（LGC）· `docs/146`（能力域）· `docs/158`（冻结面）·
> `docs/182`（诊断与 `choose`）
> 一句话: **LSPE 已经是独立包；这次把它做成真正的框架：`MacroChecker` 抽象基类、
> 所有检查器作为子类、`LspeHost` 持 `checker` + `scheduler`、调度器按适用性决定
> 谁跑谁不跑，并把“为什么没跑”写进台账。类继承与间接引用由
> `choose class_on` / `indirect_on` 打开；默认关闭，`_off` 产物逐字节不变。**

---

## 0. 三句话

1. **现状不是零**：`loment/lspe/` 已经是独立包 —— 14 条义务、9 个引擎（E1–E9）+ E10、
   证据子集格上的工作列表求解器、站点普查、派生树、台账、LSPC、问题码。编译器只是
   客户端；引擎顺序今天**手写固定**。
2. **这次做的是“框架化”**：`MacroChecker` 是抽象基类；`LspeHost` 是宏观检查器实例，
   持有 `checker`（子类登记/派发）与 `scheduler`（适用性/顺序/预算/跳过）；所有
   引擎、主检查器、Advisory 都是 `MacroChecker` 的子类。派发用 `class_on` 的
   vtable + `indirect_on` 的封闭世界间接调用。
3. **不做的**：不做运行期插件加载（产物是静态 ELF）、不做开放世界的间接引用
   （目标集必须可枚举）、不现在发公开 SDK 1.0（义务/证据面还没定型）。
   **语义留在原处；这次搬的是派发。**

---

## 1. 现状：哪些已经在了

### 1.1 包与模块

| 模块 | 角色 |
|---|---|
| `loment/lspe/registry.py` | 14 条义务、8 种证据、级别门、站点种类、Q 码、三问、地板键 |
| `loment/lspe/solver.py` | 证据子集格上的工作列表；确定性步数预算与深度上限 |
| `loment/lspe/census.py` | 词法普查 + 引擎 E1–E4、E9 |
| `loment/lspe/loans.py` | E10 借用检查（报告面，A0/A1） |
| `loment/lspe/derive.py` | 派生树、规范序列化、证明哈希 |
| `loment/lspe/ledger.py` | Potato v16 `lspe` 字段与 `.lspe` 旁挂 |
| `loment/lspe/lspc.py` | 编译前风险单（警察） |
| `loment/lspe/questions.py` | 反问器（Q 码、回答通道、三问） |
| `loment/lspe/cache.py` | 增量缓存（键含 `site_id` / `obligation` / `unit_hash` / `dims` / `level`） |
| `loment/lspe/aicheck.py` / `aiask.py` | AI 检查器：只产 Q014，**永不进证据** |
| `loment/lspe/lspe.lomt` | 自举侧同一套普查与引擎（token 级、固定容量） |

编译器在 `tools/lomentc.py` 的 `load()` 里调一次普查、在 `emit_potato()` 里调一次台账，
再加一个名字根；它**不定义**义务、证据、求解器。这条边界由 `lspe_test` 钉住
（`docs/262` §5.14）。

### 1.2 九个引擎 + E10

| 引擎 | 证什么 | 默认级别 | 确定性要求 |
|---|---|---|---|
| **E1 常量** | 字面量下标、常量偏移、常量界 | L0 | 纯语法 |
| **E2 类型/能力** | 域内、名义类型、`excluded`、`revocable` | L0 | 类型检查之后 |
| **E3 局部流** | 初始化、存活、独占、悬垂 | L1 | CFG 顺序固定 |
| **E4 过程间** | 摘要：deref-only、guard 透传、free 所有权 | L2 | 摘要按固定序 |
| **E5 全局不变量** | 循环不变式、guard 谓词、归纳 | L3 | 不依赖墙钟 |
| **E6 替换/WF** | 替换后的良构性重证 | **L1（强制）** | 替换日志全序 |
| **E7 差分对质** | 两套实现的判决+来源一致 | L3 | 逐字节；**永不单独 PROVED** |
| **E8 反证探针** | 关掉检查有没有探针变红 | L0（离线） | 探针表机械生成 |
| **E9 域检查器** | 域声明良构、`guard` 静态包含性、撤销 | L0/L1/L3 | 词法 + 循环归纳 |
| **E10 借用** | loan 图、kill=最后使用、悬垂、`free` 时仍借 | A0/A1 | 报告面，不改判决 |

### 1.3 今天的“调度器”是隐式的

- 所有引擎**按手写固定顺序**跑（`docs/262` §5.4：不靠 dict/set 迭代序）；
- 求解器是一个按站点 id 排序的队列，全局预算 `LSPE_DEFAULT_BUDGET = 65536`、
  深度 `LSPE_DEFAULT_DEPTH = 6`；
- **没有**“这个单元不需要 E5”的记录，**没有**每引擎预算，**没有**计划哈希；
- 缓存能省重复计算，但“省了哪一段”不进台账。

这正是这次要补的三件事：**适用性判定、跳过原因、计划哈希**。

---

## 2. 形状：MacroChecker / 子类 / 调度器

### 2.1 一张图

```
LspeHost（宏观检查器实例）
├── checker   : CheckerRegistry        // 子类登记与派发
└── scheduler : Scheduler              // 适用性 / 顺序 / 预算 / 跳过

MacroChecker（抽象基类）
├── ProducerChecker   extends MacroChecker   // 主检查器的判决
├── E1Constant        extends MacroChecker
├── E2Typed           extends MacroChecker
├── E3LocalFlow       extends MacroChecker
├── E4Interproc       extends MacroChecker
├── E5GlobalInvariant extends MacroChecker
├── E6Substitution    extends MacroChecker   // 强制
├── E7Differential    extends MacroChecker   // Gate
├── E8Falsification   extends MacroChecker   // Gate
├── E9Domain          extends MacroChecker
├── E10Loans          extends MacroChecker
└── AdvisoryChecker   extends MacroChecker   // AI / 第三方，只产 Q
```

- **`MacroChecker` 是抽象基类**：定义 `desc()` / `role()` / `applies()` / `check()`。
- **`LspeHost` 是宏观检查器实例**：它持有 `checker`（登记/派发）与 `scheduler`
  （调度）；它本身不产证据、不改判决。
- **所有检查器都是子类**：主检查器是 `ProducerChecker`，引擎是 E1–E10，
  Advisory 是第三方/AI。子类只实现 `check()`；`applies()` 默认用描述符的触发器。
- **角色不是继承层级**：所有子类都 `extends MacroChecker`，但 `role()` 决定
  它能产什么（Producer/Evidence/Gate/Advisory）、能不能被跳过、要不要进 LSPC。
  继承给“统一接口”，角色给“权限”。

**为什么主检查器是 `ProducerChecker` 子类，而不是宿主**：`docs/262` §0.1 的价值是
**质疑器审问编译器自己的判决**。如果生产判决的 `check()` 同时是宿主，审问就变成
自己审自己；如果它只是子类，它仍与审计器平级，但**阶段**（P0）与**角色**
（Producer）把它和证据引擎分开。角色与阶段是审计独立性的落点，不是继承。

### 2.2 角色（继承之上的第二轴）

| 角色 | 子类 | 能产什么 | 能不能跳过 |
|---|---|---|---|
| **Producer** | `ProducerChecker` | 判决/站点 | 否（P0 固定） |
| **Evidence** | E1–E6、E9、E10 | 证据节点 | 可按适用性跳过 |
| **Gate** | `E7Differential`、`E8Falsification` | `Differential` / `REFUTED` | E7 可按“孪生不可用”跳过；E8 按探针表 |
| **Advisory** | `AdvisoryChecker` | **只有 Q 码** | 可按开关跳过 |
| **Resolver** | `LspeSolver`（宿主固定） | 证据格定点 | 否 |
| **Ledger** | `ledger.py` / `lspc.py`（宿主固定） | 台账 / 预检单 | 否 |

**角色是数据，不是类。** 一个检查器可以声明多个义务，但只能有一个角色；
角色决定它能不能写证据、能不能被跳过、要不要进 LSPC。

### 2.3 阶段 DAG（固定）

```
P0 Producer  ProducerChecker：判决 / 站点
      │
P1 Census    站点普查：E1–E4 / E9 / E10 的站点
      │
P2 Evidence  E1–E6 / E9 / E10：产证据节点
      │
P3 Solver    证据子集格上的工作列表（宿主固定）
      │
P4 Gates     E7 差分 / E8 反证（永不单独 PROVED；E8 可 REFUTED）
      │
P5 Ledger    Potato v16 + .lspe 旁挂 + 证明根
      │
P6 LSPC      编译前风险单
```

**只有 P1/P2/P4 的检查器是可调度的**；P0/P3/P5/P6 是宿主固定阶段。
这条分界是“调度器不能改变语义”的硬保证：调度器只能决定**证据引擎跑不跑**，
不能决定**求解器怎么算**、**台账怎么写**。

---

## 3. 类继承与间接引用：地基

### 3.1 依赖 `docs/345`

框架依赖语言面的两条新开关：

| 开关 | 默认 | 框架用它做什么 |
|---|---|---|
| **`choose class_on`** | `class_off` | `MacroChecker` 抽象基类 + 子类 + vtable 派发 |
| **`choose indirect_on`** | `indirect_off` | `checker` 登记表里的函数指针/间接调用 |

两个 `_off` 下，LSPE 的产物与今天逐字节相同；框架代码本身用 `_on` 编译
（编译器/自举侧自己的构建），用户程序仍可选。

### 3.2 描述符（类的静态面）

```rust
abstract class MacroChecker {
    fn desc() -> EngineDesc;          // id / role / obligations / triggers / budget
    fn role() -> Role;
    fn applies(facts: &UnitFacts) -> bool;   // 默认用 desc.triggers
    fn check(ctx: &Ctx) -> [Claim];          // 子类实现；基类默认空
}

struct EngineDesc {
    id: str,
    role: u8,              // 0 Producer, 1 Evidence, 2 Gate, 3 Advisory
    obligations: u64,      // 位图
    triggers: u64,         // UnitFacts 位图
    mandatory: bool,
    budget: u32,
    version: u32,
}
```

- `Ctx` 只读：token 流、窄 AST/IR 视图、`choose` 维度、站点普查、缓存句柄、预算。
  **没有** IO、时钟、随机、台账写手、求解器内部。
- `Claim = { site_id, obligation, evidence: [EvidenceNode] }`。
- `EvidenceNode = { kind, source, span, plugin_id, plugin_version }`。
- **没有 `proved: bool`**。证成与否只由求解器按 `accept` 集合决定。

### 3.3 登记与派发

- **登记**：`CheckerRegistry` 持有一张 `[&MacroChecker]`（或类构造器表）；
  每个子类在编译期登记；顺序按 `(phase, id)` 固定。
- **派发**：`scheduler.plan(facts)` 对每个子类调 `applies()`；跑的子类按固定顺序
  调 `check()`。`class_on` 的 vtable 负责 `check()` 的多态；`indirect_on` 负责
  登记表里的函数指针。
- **封闭世界**：所有子类在编译期已知；没有运行期 `register()`，没有 `dlopen`。
  这与 `docs/345` §4 的目标集规则同一条。
- **孪生**：参考侧与自举侧的 `EngineDesc`、计划、跳过原因、计划哈希逐字节相同；
  自举侧的类/间接由 `docs/345` 的 C1–C4 提供；容量越界走 `LSPE_CAPACITIES`
  的点名失败，不静默截断。

### 3.4 成本

类/间接不是免费的：L0 新 opcode/ABI、两套实现、`lomelf` 的间接调用、vtable 发射、
自举侧每加一个函数 ≈ +4.7s、种子 ~3.88MB 悬崖（`docs/345` §6）。
框架层必须薄：只加基类、登记表、调度器；引擎的实现留在原处。

---

## 4. 调度器：谁跑、谁不跑、为什么

### 4.1 输入与输出

```rust
struct UnitFacts {
    has_domain: bool,
    has_borrow: bool,
    has_loops: bool,
    has_calls: bool,
    has_generics: bool,
    has_literals: bool,
    twin_available: bool,
    dims: DIMS,
    level: u8,
    unit_hash: u64,
}

struct Plan {
    run: [EngineId],
    skip: [SkipRecord],       // {engine, reason}
    budgets: [u32],
    plan_hash: u64,
}
```

### 4.2 触发器

| 引擎 | 触发 | 不触发时的跳过原因 |
|---|---|---|
| E1 常量 | 有字面量下标/界 | `not_applicable` |
| E2 类型/能力 | 有 `guard` / `domain` / `excluded` / `revocable` / 类型站点 | `not_applicable` |
| E3 局部流 | 有可变局部 / 借用 / 初始化站点 | `not_applicable` |
| E4 过程间 | 有带 `ptr`/切片实参的调用 | `not_applicable` |
| E5 全局不变量 | 有循环 + guard/下标 | `not_applicable` |
| E6 替换/WF | 有泛型/替换 | **强制**（不可跳） |
| E7 差分 | 两套实现都在 | `twin_missing` |
| E8 反证 | 探针表里有本单元义务的条目 | `no_probe` |
| E9 域 | 有 `domain` / `capability` / `guard` / `revoke` | `not_applicable` |
| E10 借用 | 有 `&`/`ref`，且 `lifetime != lifetime_no` | `profile`（`lifetime_no`） |
| AI | `choose lspe_aicheck_true` | `profile` |
| 插件 | 已登记且启用 | `not_registered` |

**过近似原则**：调度器可以多跑（成本），不能少跑（漏检）。只要不确定是否适用，
就**跑**，并让它自己报 `Q002(inert)`。这条与 `docs/262` §5.10 的
“没有探针的义务停在 `UNDECIDED(inert)`”同一条纪律。

### 4.3 跳过原因（必须进台账）

| 原因 | 含义 |
|---|---|
| `not_applicable` | 触发器不成立 |
| `profile` | 维度关掉了它（例如 `lifetime_no` 关 E10） |
| `cached` | 同一 `(unit_hash, dims, level, obligation)` 命中缓存，且重建的证明哈希相同 |
| `budget_zero` | 调度器给它分配 0 步；台账记 `Q005` |
| `missing_facts` | 需要的生产者事实缺失；**不能**当作通过 |
| `twin_missing` | E7 需要两套实现，当前只有一套 |
| `no_probe` | E8 的探针表里没有本单元义务的条目 |
| `not_registered` | 插件没登记/没启用 |

**台账里 `skipped[]` 与 `run[]` 分开记**；`skipped` 永远不计入 `proved`。
覆盖率表加一列 `skipped`，并有恒等式：

```
sites = run_sites + skipped_sites(0) + undecided_sites
```

跳过的引擎没有站点，所以它不改变 `sites`；但它改变“**哪些格子没有被看过**”。
这一列是新的诚实项：**没跑 ≠ 过了**。

### 4.4 强制集

以下子类**不能被跳过**；事实缺失时它仍然跑，并报 `Q001` / `Q002`：

- `TypeWellFormed`（有类型站点时）；
- `NoRuntime`（`choose no_runtime` 时）；
- `InBounds`（有下标站点时）；
- `InDomain`（有域站点时）；
- `E6`（有泛型/替换时）；
- `E8Falsification` 的反证闸（有探针条目时）。

`lspe_close_admin` **只关审计，不关检查**（`docs/262` §10.2）：
调度器照旧跑强制集；关掉的只是台账与 LSPC。

### 4.5 确定性

- 计划 = `(phase, id)` 排序，不靠哈希序；
- 每引擎预算固定，全局预算仍是 `LSPE_DEFAULT_BUDGET`；
- 计划哈希进台账；同源同维度 ⇒ 同一计划哈希；
- 缓存命中要求重建的 `proof_hash` 相同；跨级别不复用（`docs/262` §5.13）；
- 冷/热构建的 `lspe` 字段逐字节相同；
- 类/间接的派发不引入哈希序：vtable 槽位按声明+override 固定，登记表按 id 排序。

### 4.6 调度器的成本

调度器本身是 O(子类数) 的表扫描 + 一次排序；子类数上限固定（`LSPE_CAPACITIES`
加一条 `checkers <= 64`）。它不改变求解器的步数预算，只决定**谁先拿到预算**。
预算是**分配**，不是**减少**：总预算仍是 65536，分配只影响谁先耗尽、谁记 `Q005`。

---

## 5. SDK：两档，不是一把钥匙

### 5.1 两个面，先做编译器侧

| 面 | 是什么 | 什么时候 |
|---|---|---|
| **编译器侧 SDK** | 写一个 `MacroChecker` 子类，登记进 `LspeHost`；构建期插件 | **先做**（in-tree v0） |
| **程序侧 API** | 程序读自己的 Potato/台账 | 以后；当前 LSPE 是编译期 |

“发布 LSPE SDK”默认读作**编译器侧**。程序侧是另一个产品（运行期读台账），
不在本文。

### 5.2 两档

| 档 | 能返回 | 能进证据？ | 例子 |
|---|---|---|---|
| **Advisory** | `Question`（Q0xx） | **永不** | AI 检查器 Q014；第三方 lint |
| **Evidence** | `Claim` + `EvidenceNode` | 仅当义务的 `accept` 集合含 `External`/`Plugin` | 经过评审的第三方证明器 |

**默认是 Advisory。** 要升 Evidence，必须：

1. 该义务在 `LSPE_OBLIGATIONS` 里显式接受 `External`/`Plugin`（登记表变更，
   单独提交、单独评审）；
2. 插件有 `plugin_id` + `plugin_version` + `plugin_hash`，进台账；
3. 插件确定性（无 IO/时钟/随机/哈希序），或被限制在 Advisory 档；
4. 求解器仍然按 `accept` 集合裁决 —— 插件**不能**直接写 `proved`。

### 5.3 API 面（窄）

```rust
abstract class MacroChecker {
    fn desc() -> EngineDesc;
    fn role() -> Role;
    fn applies(facts: &UnitFacts) -> bool;
    fn check(ctx: &Ctx) -> [Claim];
}

struct Claim {
    site_id: str,
    obligation: str,
    evidence: [EvidenceNode],
}

struct EvidenceNode {
    kind: str,          // Axiom / Syntactic / ... / External
    source: str,        // 必填
    span: [u32],
    plugin_id: str,
    plugin_version: u32,
}
```

**不暴露**：

- 编译器的 AST/IR 类（会变；Clang-Tidy/ESLint 的教训是“插件 API 冻住了内部表示”）；
- 求解器内部（只能给 `Claim`，不能问“现在是不是 PROVED”）；
- 台账写手（只能返回，不能写）；
- 跳过强制检查的能力；
- 运行期加载（产物是静态 ELF，没有 `dlopen`）。

### 5.4 版本与发布

- **`lspe-sdk/0`（in-tree，不稳定）**：现在就能写；API 可改；只在仓内测试用。
- **`lspe-sdk/1.0`（公开）**：等义务表、证据种类、`MacroChecker` 形状**冻结**之后。
  它是**跨线契约**，按 `lom/*.lom` 的纪律：单独提交、写清下游、变更要升版。
- **发布时机**：`docs/262` 的对外故事本来就排在开发完成之后；SDK 是这个故事里
  最硬的一份契约，**更不应该现在发**。先发 in-tree v0，公开 1.0 等 0.1.5.x
  的 LSPE 面稳定。

---

## 6. 前史（先点名）

| 先例 | 机制 | 对本文的教训 |
|---|---|---|
| **LLVM PassManager / AnalysisManager** | pass 注册、`getAnalysisUsage`、失效、按需分析 | 调度器要声明“我依赖什么、我保留什么”；失效是调度的一部分 |
| **rustc `LintPass` + `declare_lint_pass!` + `register_lints`** | trait + 静态注册 + lint 级别 | 最接近“检查器基类 + 登记表 + 级别”；级别是数据 |
| **Clang Static Analyzer `CheckerBase` / `CheckerRegistry`** | checker 注册、`check::PreStmt`、`BugReporter` | 检查器只产 bug/路径，不直接改结论 |
| **Clang-Tidy / ESLint / SonarQube** | 插件规则、quality profile | profile 决定哪些规则跑；API 太宽会冻住内部 |
| **Infer / Frama-C** | 分析器选择、插件 | “跑哪些分析”本身是一等配置 |
| **Demand-driven analysis**（Duesterwald/Gupta/Soffa 1995） | 只算被查询性质需要的事实 | “哪些检查需要跑”有理论前身 |
| **Incremental analysis**（Ryder/Paull、IncA） | 只重算变化的单元 | 缓存/跳过要有原因，不能静默 |
| **Simula 67 / C++ / Java / C#** | 类、单继承、vtable、接口 | 单继承 + vtable 是成熟形状；见 `docs/345` |

**自报**：登记表、求解器、证据格、LSPC 都已经在 `loment/lspe/` 里；本文没有
新的安全理论。新的只有**`MacroChecker` 子类化的派发**、**跳过原因进台账**、
**两档 SDK**。

---

## 7. 不可能性与边界（先写死）

1. **类继承/间接引用是 `choose` 打开的。** `class_off`/`indirect_off` 下，
   LSPE 产物与今天逐字节相同；`class_on`/`indirect_on` 见 `docs/345`。
2. **间接引用必须封闭世界。** 目标集可静态枚举；`ptr` 强转/FFI 函数指针拒绝；
   否则缺席主张降级或拒绝。
3. **没有运行期插件。** 产物是静态 ELF；SDK 是**编译期**的。
4. **第三方证据默认不可信。** 默认 Advisory；Evidence 要义务显式 opt-in +
   身份/版本 + 评审；求解器仍然裁决。
5. **强制检查不能跳过。** `lspe_close_admin` 只关审计；调度器不能把强制集
   变成 `skipped`。
6. **跳过不能长得像通过。** 每条 skip 必须有原因、进台账；`skipped` 不计入
   `proved`。
7. **调度器只能过近似。** 不确定就多跑，不能少跑；少跑是漏检，多跑只是成本。
8. **孪生成本是真的。** 自举侧每加一个函数 ≈ +4.7s；60s 护栏只剩 ~1%；
   种子 ~3.88MB 悬崖；框架层必须薄，容量要先进 `LSPE_CAPACITIES`。
9. **公开 SDK 会冻结合同。** 义务/证据面还在动（E10 A0/A1、E9、S1.5）；
   现在发 1.0 会把未定型的东西钉死。
10. **类/间接不是免费的。** L0 契约、两套实现、`lomelf`、vtable、种子/容量
    都要付；框架只是第一个消费者，不是豁免。

---

## 8. 判据（可证伪）

| # | 判据 | 证伪方式 |
|---|---|---|
| **A** | 描述符覆盖现有全部引擎；同一语料上计划跑出的引擎集合与今天一致 | 少一个引擎 ⇒ 判决漂 |
| **B** | 无字面量下标的单元跳过 E1，原因 `not_applicable`；有则跑 | 原因缺失 ⇒ 跳过不可审计 |
| **C** | 强制集永不 `skipped`；`lspe_close_admin` 下仍跑 | 强制被跳 ⇒ 安全故事破 |
| **D** | 同一单元/维度/登记表 ⇒ 计划哈希逐字节相同；两侧同哈希 | 不同 ⇒ 孪生破 |
| **E** | Advisory 子类返回 `Claim` ⇒ 被拒，只能返回 `Question` | 能写证据 ⇒ 档位失效 |
| **F** | Evidence 子类对未 opt-in 的义务返回证据 ⇒ 被拒；求解器仍裁决 | 能绕过 accept ⇒ 证据面失效 |
| **G** | 每条 skip 有原因；`skipped` 不计入 `proved`；覆盖率恒等式成立 | 静默跳过 ⇒ 新的“沉默写穿” |
| **H** | 不注册任何插件 ⇒ 产物与今天逐字节相同 | 变了 ⇒ 默认档被破坏 |
| **I** | 每引擎预算耗尽记 `Q005`，不静默成功 | 静默 ⇒ 预算失效 |
| **J** | `lspe-sdk/0` 只在树内；公开 1.0 必须等义务/证据冻结 | 提前发 ⇒ 合同冻错 |

---

## 9. 落地阶梯

| 步 | 内容 | 出口判据 |
|---|---|---|
| **V0** | `MacroChecker` 基类 + 子类描述符 + 静态登记表 + 固定顺序；**不改判决** | A / D / H |
| **V1** | 调度器 + 跳过原因 + 计划哈希 + 台账 `skipped[]` | B / C / G / I |
| **V2** | Advisory SDK（只能问，不能证） | E |
| **V3** | Evidence SDK（义务 opt-in + 身份/版本） | F |
| **V4** | 公开 `lspe-sdk/1.0`（冻结后） | J |

V0 依赖 `docs/345` 的 **C0**（`choose` 两维 + 语法骨架）；V1 依赖 **C2**（vtable +
间接调用）；V2/V3 依赖 **C3**（封闭世界目标集）。每一步都走 `docs/158` §5：
两侧同判决、判据先红、生成物单独提交、种子 `--emit` 不手合。

---

## 10. 我不主张

1. **不主张这是新的安全理论。** 登记表/求解器/证据格已经在；这次搬的是派发。
2. **不主张开放世界的继承/间接。** `class_on`/`indirect_on` 必须封闭；
   `class_off`/`indirect_off` 是默认且字节不变。
3. **不主张运行期插件。** 产物是静态 ELF；SDK 是编译期的。
4. **不主张现在发公开 SDK 1.0。** 义务/证据面还在动；先 in-tree v0。
5. **不主张“所有检查器平级”。** 生产者/审计器/求解器/台账角色不同，阶段不同；
   它们都继承 `MacroChecker`，但角色与阶段把它们分开。
6. **不主张“跳过是免费的”。** 跳过要原因、要进台账、要进 LSPC 的可见面。
7. **不主张第三方证据默认可信。** 默认 Advisory；Evidence 要 opt-in + 评审。
