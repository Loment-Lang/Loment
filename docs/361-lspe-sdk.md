# 361 · LSPE SDK 1.0：一个主检查器，下面才是域/借用/……

> 状态: **已落地（2026-11-21，`lspe-sdk/1.0`）**
> 上游: `docs/344`（框架 V0–V4）· `docs/262`（LSPE）· `docs/317`（借用 E10）·
> `docs/341`（效应 E11）· `docs/353`（GC E12）· `docs/306`–`308`（域 E9）·
> `docs/345`（类与间接，语言面地基）
> 一句话: LSPE SDK 的顶层**只有一个检查器** —— `MainChecker`。它自己承担 Producer
> 角色，下辖域检查器、借用检查器、效应检查器、GC 检查器等；编译器只调
> `lspe.audit(...)`，不再自己拼“计划 + 普查”两步。

---

## 0. 用户裁定

2026-11-21：

> LSPE SDK 下只有一个大检查器，也就是主检查器；主检查器下辖域检查器、借用检查器
> 等检查器。

这条把 `docs/344` 里“宿主 + 平级引擎”的形状收成了**一棵树**：

* **一个入口**：`MainChecker`。它不是一个“恰好持有登记表的宿主”，而是 SDK 里
  唯一可以被称为“检查器”的顶层对象；
* **一棵子树**：E9 域、E10 借用、E11 效应、E12 GC、E1/E2/E5 常量与类型、
  E2b 界、E3 局部流、E4 过程间、E6 替换、E7 差分、E8 反证、A1 Advisory 都在
  它下面；
* **一条编译路径**：编译器只拿主检查器的 `audit()` 结果。计划、站点、效应追加
  都在主检查器内部完成。

---

## 1. 形状

```
LspeSdk（SDK 会话）
├── scheduler : Scheduler              // 适用性 / 顺序 / 预算 / 跳过（SDK 的）
└── checker   : MainChecker            // 唯一顶层检查器（Producer / P0）
    ├── checker   : CheckerRegistry    // 子检查器登记
    │
    ├── ConstantChecker      E1Constant          常量、guard、下标、循环归纳（E1/E2/E5 共用一遍）
    ├── TypedChecker         E2Typed             类型/能力（与上面同一遍词法）
    ├── BoundsChecker        E2bBounds           LMS g1 内联界（强制）
    ├── LocalFlowChecker     E3LocalFlow         初始化 / 存活 / 独占 / 悬垂
    ├── InterprocChecker     E4Interproc         过程间摘要
    ├── GlobalInvariantChecker E5GlobalInvariant 循环不变量（与 E1/E2 同一遍）
    ├── SubstitutionChecker  E6Substitution      替换后的 WF（强制）
    ├── DomainChecker        E9Domain            域声明 / guard 包含性 / 撤销
    ├── BorrowChecker        E10Loans            loan 图 / kill=最后使用 / 冲突 / 悬垂
    ├── EffectChecker        E11Effects          行 / 处理器 / resume / 续延捕获
    ├── GCChecker            E12GC               alloc 站点的回收判决
    ├── DifferentialGate     E7Differential      孪生差分（Gate）
    ├── FalsificationGate    E8Falsification     反证探针（Gate）
    └── AdvisoryChecker      A1Advisory          AI / 第三方，只产 Q 码
```

**调度器属于 SDK，不属于检查器。** `Scheduler` 是 `LspeSdk` 的成员，主检查器
**不持有**它，也没有 `plan()`；`MainChecker.audit(...)` / `run(...)` 要求调用方
传入 `Plan`，没有计划就点名失败。计划是 SDK 算好交给检查器的。

**主检查器不登记进自己的表。** 它是 P0，不参与“谁跑谁不跑”的调度；调度器只
决定**子检查器**跑不跑。`Plan.run` 里因此不再有 `P0Producer` —— 主检查器就是
生产器（判据 `test_sdk_audit_preserves_the_census_site_order_and_ids` 钉住这一点）。

---

## 2. 主检查器的三张脸

| 面 | 方法 | 谁用 |
|---|---|---|
| **调度面** | `plan(facts)` / `plan_for_tokens(...)` | 编译器、`loment lspe state` |
| **审计面** | `audit(toks, unit, ...)` | **编译器唯一入口** |
| **框架面** | `run(ctx)` / `check(ctx)` / `sub(id)` | SDK 使用者、插件判据 |

### 2.1 审计面

```python
sdk = lspe.LspeSdk()
res = sdk.audit(toks, "m", dims={"lifetime": "lifetime_auto"},
                lms={"lms_guard"}, slots={"effects": "true"})
res.sites       # 台账要的站点，顺序与内容寻址 id 与旧普查逐字节相同
res.unsourced   # 反问器的输入
res.passes      # `lspe pass` 块
res.plan        # SDK 的调度器给出；`_plan` 内部字段，`public()` 会剥掉
res.effects     # E11 报告（只在打开时非 None，不写台账）
res.loans       # E10 报告（只在 `borrows=True` 时非 None，不写台账）

sdk.checker.domain_checker   # E9Domain
sdk.checker.borrow_checker   # E10Loans
sdk.scheduler                # 调度器在 SDK 上，不在检查器里
```

`MainChecker.audit(...)` 也接受同一组扫描参数，但 `plan` 是**必填**：主检查器
不自己算计划。`plan` 只能来自 `LspeSdk.scheduler`。

审计面内部的**阶段顺序是固定的**，与旧 `census()` 完全一致：

```
E1/E2/E5 → E9 → E2b → E3 → E4 → E6 → E12 → E11（追加）→ E10（报告面）
```

计划只决定“这一段跑不跑”，不改变顺序；因此站点列表、pass 块计数与内容寻址
id 都不漂。`docs/344` §4.5 的确定性要求照旧成立。

E7 差分与 E8 反证是 P4 的**固定闸门**：它们登记在主检查器下面，但不属于
`audit()` 的普查阶段；执行它们的是台账/不变量那条固定路径（`docs/344` §2.3）。
主检查器不会因为“它们是子检查器”就把它们塞进普查顺序。

### 2.2 框架面

`MainChecker` 本身也是 `MacroChecker` 子类：

```python
class MainChecker(MacroChecker):
    desc = EngineDesc("MainChecker", 0, "Producer", budget=0)
    def check(self, ctx):        # 统一接口：所有子检查器的组合
        ...
```

子检查器有两种执行方式：

* `check(ctx) -> [Claim | AdvisoryQuestion]`：走 `MacroChecker` 统一接口；
* `scan(stage)`：走普查阶段，往共享的 `CensusStage` 里追加站点。

同一份子检查器实例两用，不需要第二套登记表。求解器仍然是唯一裁决者；主检查器
的 `check()` 只是把子检查器的输出汇总，再交给 `_accept_claim` 校验角色与
`accept` 集合。

主检查器**不持有** `scheduler`，也没有 `plan()`。框架面的计划由
`LspeSdk.run(ctx)` / `LspeSdk.check(ctx)` 先算好，再交给主检查器。

### 2.3 子检查器访问

```python
sdk = lspe.LspeSdk()
main = sdk.checker
main.sub("E9Domain")        # 域检查器
main.domain_checker         # 同上
main.borrow_checker         # 借用检查器（E10Loans）
```

**内置引擎类不在顶层导出。** 顶层只有 `MainChecker`、`MacroChecker`（给插件
继承）与数据形状；`ConstantChecker` 等是实现细节。判据
`test_sdk_has_exactly_one_main_checker_and_no_top_level_engines` 钉住这一点。

### 2.4 自举侧同一形状

`loment/lspe/lspe.lomt` 也按同一条边界切：

| 名字 | 角色 |
|---|---|
| `lspe_sched` | SDK 的调度器；返回计划位 `LS_PLAN_ENGINE` / `LS_PLAN_DOMAIN` |
| `lspe_main` | 唯一主检查器；按计划派发 |
| `lspe_engine` / `lspe_domain` | 私有的子检查器，只有 `lspe_main` 调 |

`loment/selfhost/potato.lomt` 先调 `lspe_sched`，再把 `plan` 交给 `lspe_main`；
它不再直接调子检查器。`lspe_main` 多出的失败码 3 = 域声明容量，与引擎的 1/2
分开（写手要能点名）。E2b 与 E1–E6 共用 `LS_PLAN_ENGINE` 一段，所以 `lms`
不改变阶段粒度，调度器不收它。

E10 仍是参考侧报告面；E11/E12 的台账计数住在 `potato.lomt` 的台账装配器里，
不在 `lspe_main` —— 这条不假装已经对齐。判据
`test_selfhost_has_the_same_scheduler_outside_the_main_checker` 钉住自举侧的
调度器/主检查器/私有子检查器三层。

这次自举侧多出的两个函数让种子从 7,393,632 B 长到 7,417,951 B；`lomelf` 的
全局表从 6144 抬到 8192（+65,536 B，后面每张表同幅后移），genesis 按新种子/
镜像重出。

---

## 3. 编译器接入

`tools/lomentc.py` 的 `load()` 里只剩一次调用：

```python
_lspe_audit = lspe.audit(
    tt, mod.name, dims=effective_dims(mod), level=lspe_level,
    lms=_lms_on, slots=getattr(mod, "slots", {}),
    scoped=_scoped,
    shadowed=(_shadowed_names(mod) if _scoped else {}))
mod.lspe_plan = _lspe_audit.plan
mod.lspe_sites = _lspe_audit.sites
mod.lspe_unsourced_lines = _lspe_audit.unsourced
mod.lspe_passes = _lspe_audit.passes
```

旧的两步 `plan_for_tokens(...)` + `census(...)`，以及 A2c 里单独追加
`effects_analyze(...)` 的那一段都不在编译器里了。判据
`test_compiler_reaches_lspe_only_through_the_sdk` 直接扫 `tools/lomentc.py`，
防止它被改回两条入口。

`census()` 仍是旧普查入口；`plan_for_tokens()` 是 SDK 的兼容包装（内部是
`LspeSdk`）。**它们都不是第二个检查器**。

---

## 4. 冻结面

`loment/lspe/sdk.py` 是唯一声明版本的地方：

```python
LSPE_SDK_VERSION = "lspe-sdk/1.0"
SDK_SURFACE = ( ... 23 个名字 ... )
```

### 4.1 冻结了什么

| 面 | 内容 |
|---|---|
| SDK 会话 / 调度 | `LspeSdk`、`Scheduler` |
| 顶层检查器 | `MainChecker` |
| 基类 | `MacroChecker`（`desc` / `role` / `applies` / `check` / `scan`） |
| 描述符 | `EngineDesc`（id / phase / role / obligations / triggers / mandatory / budget / version） |
| 数据 | `Claim`、`EvidenceNode`、`AdvisoryQuestion`、`Ctx`、`UnitFacts`、`Plan`、`SkipRecord`、`HostResult`、`AuditResult` |
| 插件 | `PluginChecker`、`evidence_allowed` |
| 入口 | `main_checker()`、`audit()`、`plan_for_tokens()`、`plan_lines()` |
| 版本 | `LSPE_SDK_VERSION`、`SDK_SURFACE` |

### 4.2 兼容规则

1. **加名字 = 升版。** `SDK_SURFACE` 只许在版本号变化时改动；判据钉住它与
   顶层属性一一对应。
2. **义务表与证据种类从此冻结。** `LSPE_OBLIGATIONS` 增删、`accept` 集合变化
   都算契约变更，要单独提交并写清下游影响（与 `lom/*.lom` 同一条纪律）。
3. **不导出编译器内部表示。** AST/IR/求解器内部/台账写手都不在 SDK 面上。
4. **调度器属于 SDK，不属于任何检查器。** `MainChecker` 不提供 `plan()`、
   不持有 `scheduler`；计划只能由 `LspeSdk.scheduler` 产生。
5. **没有运行期插件。** 产物是静态 ELF；SDK 是编译期的。
6. **强制集不能跳过。** `lspe_close_admin` 只关审计，不关检查。

### 4.3 两档

| 档 | 能返回 | 能进证据？ |
|---|---|---|
| **Advisory** | `AdvisoryQuestion` | **永不** |
| **Evidence** | `Claim` + `EvidenceNode` | 仅当义务的 `accept` 含 `External`/`Plugin` |

Default 是 Advisory。Evidence 插件还要 `plugin_id` / `plugin_version` /
`plugin_hash` 三件套；今天义务表里没有任何一条 opt-in，所以第三方证据一律
被拒（这是有意的缺口，不是遗漏）。

---

## 5. 子检查器清单

| 引擎 | 类 | 它拥有什么 | 阶段 | 报告面？ |
|---|---|---|---|---|
| E1/E2/E5 | `ConstantChecker` / `TypedChecker` / `GlobalInvariantChecker` | guard、下标、循环归纳（共用一遍词法） | P2 | 否 |
| E2b | `BoundsChecker` | LMS g1 内联界 | P2 | 否 |
| E3 | `LocalFlowChecker` | 初始化、存活、独占、悬垂 | P2 | 否 |
| E4 | `InterprocChecker` | 过程间摘要 | P2 | 否 |
| E6 | `SubstitutionChecker` | 替换后的 WF | P2 | 否 |
| E9 | `DomainChecker` | 域声明、guard 包含性、撤销 | P2 | 否 |
| E10 | `BorrowChecker` | loan 图、kill、冲突、悬垂 | P2 | **是**（不写台账） |
| E11 | `EffectChecker` | 行、处理器、resume、续延捕获 | P2 | **是**（站点追加） |
| E12 | `GCChecker` | alloc 回收判决 | P2 | 否 |
| E7 | `DifferentialGate` | 孪生差分 | P4 | Gate |
| E8 | `FalsificationGate` | 反证探针 | P4 | Gate |
| A1 | `AdvisoryChecker` | 第三方/AI 怀疑 | P2 | Advisory |

E10/E11 是**报告面**：主检查器会跑它们（在请求时），但今天不把 loan/effect 站点
写进 Potato 台账。那一步（E10 A0c / E11 台账计数）要与自举侧同步，单独落地；
本页不把它们说成已完成。

---

## 6. 判据

| # | 判据 | 证伪方式 |
|---|---|---|
| A | SDK 持有调度器；顶层只有一个主检查器，且主检查器没有 `scheduler` / `plan()` | 调度器进了检查器 ⇒ 边界破 |
| B | 域/借用是主检查器的子检查器，可按 id 取到 | 取不到 ⇒ 下辖只是说法 |
| C | `sdk.audit()` 的站点顺序与 id 与旧普查逐字节相同 | 漂一条 ⇒ 台账不可比 |
| D | 借用检查器在 `lifetime_no` 时被 SDK 的调度器跳过 | 没跳过 ⇒ profile 失效 |
| E | 效应站点只在打开时追加，且追加在最后 | 提前/多出 ⇒ 产物漂 |
| F | `check()` 真的派发到子检查器 | 不派发 ⇒ 主检查器是空壳 |
| G | 公开面 = `SDK_SURFACE`，版本 1.0 | 多导出 ⇒ 契约冻错 |
| H | 编译器只有 `lspe.audit(...)` 一条入口 | 第二条 ⇒ SDK/主检查器被绕过 |
| I | 主检查器没有 `Plan` 时点名失败 | 能自调度 ⇒ 调度器边界破 |
| J | 自举侧同样有 `lspe_sched` 在 `lspe_main` 之外，子检查器私有 | 绕过主检查器 ⇒ 两侧形状不一致 |

对应 `tools/lspe_test.py` 末尾的 9 条（2026-11-21 加入）。

---

## 7. 我不主张

1. **不主张这是新的安全理论。** 义务、证据格、求解器、台账都已在；这次收的是
   派发与入口。
2. **不主张 E10/E11 已经写进台账。** 它们是报告面；台账计数仍待同步。
3. **不主张开放世界插件。** 目标集必须可枚举；没有 `dlopen`。
4. **不主张“主检查器 = 编译器判决”。** 它审问判决、记录来源；L0/L1/L2 的
   判决仍由编译器与运行时做。
5. **不主张检查器自己调度。** 调度器是 SDK 的；主检查器只执行 SDK 给的 `Plan`。
6. **不主张跳过是免费的。** 每条 skip 有原因、进台账；`skipped` 不计入 `proved`。
