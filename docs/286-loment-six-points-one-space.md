# 286 · 六点一空间：把六门语言的优点降成开关

> 状态: **全新计划（激进版）**（2026-11-03）
> 上游: `docs/204`（C/Rust/Java/Python 优点收集）· `docs/205`（0.1.5 的逐条裁决）·
> `docs/285`（`par` 与"最后一个一等公民"）· `docs/274`（新维度准入）·
> `docs/188`/`docs/179`（六门表层语法）· `docs/253`/`docs/262`（LMS/LSPE）·
> `docs/278`–`docs/283`（热更新与 Java 互操作）
> 一句话: **六门语言是五维空间里的六个点；Loment 不做第七个点，它做那个空间——
> 而且一个程序可以住在多个点上，只要边界被证明。**
>
> **本文先不管代价。** 代价那一格在 `docs/204`/`docs/205` 与 `docs/285` 里，
> 落地时按 `docs/158` §5 补。本文写的是"如果全都要，形状是什么"。

---

## 0. 先换问题

`docs/205` 问的是：**"Loment 该抄哪几条优点？"** 于是它必须逐条裁决"抄 / 换形式抄 /
明确不抄"，而"明确不抄"有五条（R7 卫生宏、J2 JIT、P3 动态类型、P6 生成器、C 的 UB）。

本计划换一个问题：

> **六门语言各自是五维空间里的哪个点？Loment 怎么成为那个空间？**

一旦这样问，"抄不抄"就变成了"这个点在哪、怎么到达"。六个点的坐标：

| 语言 | 权（能碰到什么） | 命（内存怎么活） | 机（什么在跑） | 记（事后能证明什么） | 时（事情按什么次序） |
|---|---|---|---|---|---|
| **C** | 封闭、无宿主 | 手动、无 GC | 原生、无运行期 | 编译器 + 符号表 | 顺序 |
| **C++** | 封闭 | 手动 + RAII | 原生 | 编译期（`constexpr` / 反射） | 顺序 / OpenMP |
| **Rust** | 封闭 | 所有权、无 GC | 原生 | 诊断 + cargo | `Send/Sync` + Rayon |
| **Go** | 宿主（网络） | 自动 GC | 运行期 + 快构建 | `gofmt/test/vet/pprof` | goroutine / channel |
| **Java** | 宿主（JVM） | 自动 GC（ZGC 等） | VM + JIT | JFR + IDE | 线程 / 虚拟线程 |
| **Python** | 宿主（PyPI） | 引用计数 + GC | 解释器 | REPL + 内省 | async / GIL |
| **Loment** | **整个空间** | **整个空间** | **整个空间** | **整个空间** | **整个空间** |

Loment 不做第七个点。它做**坐标系**：每个程序选一个点，或者一片区域；
语言负责证明"点与点之间的每一步都可走"。

---

## 1. 四条吸收通道

任何优点只能从四条通道进来。进不了这四条，它就是**第九轴**——按 `docs/285` §2.3
的举证责任，必须给出一对在五轴上完全相同、却可观察地不同的程序，否则不成立。

| 通道 | 它吸收什么 | 例子 | 判据形状 |
|---|---|---|---|
| **轴** | 整程序的语义选择 | `mode`/`gc`/`runtime`/`port`/`vm`/`lms`/`lspe`/`par` | 矩阵格 + 产物字段 + 链接器带牙检查 |
| **型** | 改变"能证什么"的语言特性 | 所有权、ADT、泛型/trait、效应、能力域、DGT | 检查器判据 + 孪生逐字节 |
| **面** | 只改写法与人体工学的拼写 | 六门表层、渐进类型、`?`、`defer`、`async` | 行为判据（同一语义，不同写法） |
| **端** | 生态与外部世界的连接 | C ABI、JVM/CPython/WASM、lompi、std/LibL | 端到端跑通 + 符号/ABI 判据 |

> 表里的"通道"只写四个；`+` 表示一条优点同时需要两条（例如 `型+面`）。版本与兼容
> （edition、向后兼容）归到**轴**：它是整门语言的承诺，不是某个类型或表面的特性。
> REPL 归到**轴**（执行模型）而不是面：它先是一个执行模式，才是一种手感。

四条通道解决了"全部优点"的清单化问题：**不是四十个功能，是四条通道 × 五个轴。**
一条优点如果同时需要两条通道（例如"所有权"既改类型又要一个表面），它就在账本里
记两条，而不是偷偷变成一个新轴。

本文不写"代价"那一列。`docs/204` 的三段式（机制 → 得到什么 → 代价）仍然是账本的
输入格式；本文只写**机制与落点**，因为"先不管副作用"。

---

## 2. 六门语言：六个点，各自拿走什么

原则：**要那个目的，不要那个形式。** 形式撞墙就换形式；目的撞墙才是真的墙（§8）。

### 2.1 C —— 底座

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 可预测、无隐藏成本 | `no_std` + `no_runtime` + `gc_manual` + `no_gc` | 轴 |
| C ABI 是通用货币 | `extern fn` + `--link`；**反向**：C 能调 Loment | 端 |
| 编译器遍地、可移植 | 自研后端 + 交叉目标 + 裸机对象 | 轴 |
| 显式内存与布局 | `ptr`、`load8`/`store8`、`alloc`/`free`、结构体布局 | 型 |
| 小到能完整掌握 | 小核 + `loment cheat` + 手册 | 面 |
| 不作主张的谦虚 | 能力域与 `lom/*.lom` 契约层 | 轴 |

**不拿的形式**：UB。越界要 trap 或编译期拒，不是"编译器可以假设不会发生"。
这是唯一一条真正的"不抄"（§8）。

### 2.2 C++ —— 零成本抽象与编译期

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 零成本抽象 | 泛型 + 单态化 + 无动态派发默认 + 布局特化 | 型 |
| RAII / 确定性析构 | 作用域结束即释放（`Drop`）+ `free` + `defer` | 型+面 |
| `constexpr` / 编译期求值 | `const` + comptime + DGT | 型 |
| 静态反射（C++26） | DGT/comptime 反射，**无运行期反射成本** | 型 |
| 模板/概念 | 泛型 + 能力约束（capability 即是约束） | 型 |
| STL/算法 | `std` / LibL / lompi | 端 |
| 值/移动语义 | 所有权 + 移动子集 → 全移动 | 型 |
| 模块化构建 | 单元 + 增量缓存 + lompi | 端 |

**不拿的形式**：模板元编程的语法怪物。目的（编译期算、运行期零成本）由 comptime/DGT
承担；形式由 Loment 代码承担，诊断仍是 Loment 的诊断。

### 2.3 Rust —— 安全与工具

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 所有权/借用 | move + 别名 XOR 可变；**推断优先**，注解只在 Rust 表面 | 型 |
| `Send`/`Sync`、无数据竞争 | `par` 的足迹证明 + 线程语义 | 轴+型 |
| ADT + 穷尽 `match` | `enum`/`match`（已有） | 型 |
| trait + 单态化 | trait/泛型 + 静态派发 | 型 |
| `Result` + `?` | 错误是值 + `?` 早退 | 型+面 |
| Cargo 统一工具 | lompi + `loment build/test/fmt/doc` | 端 |
| 诊断质量 | 码 + 修法 + `--explain` + LSP | 轴 |
| 卫生宏 | **不做宏系统**；comptime 的 Loment 函数 + DGT | 型 |
| edition / 兼容 | LLS edition + 预算棘轮 | 轴 |
| async | 表面语法，语义仍是结构化任务 | 面 |

**不拿的形式**：把生命周期注解当默认。目的（安全）要；形式（人人写 `'a`）只在 Rust
表面出现，别的表面由推断与边界检查承担。

### 2.4 Go —— 简单、并发、工程

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 小语言、快上手 | 小核 + 六表面 + `loment cheat` | 面 |
| goroutine + channel | `par` 的结构化任务 + channel 库 | 轴+端 |
| 快速构建 | 简单 IR + 增量缓存 + LVM | 轴 |
| 单一静态二进制、交叉编译 | 原生后端 + 静态链接 | 轴 |
| 工具链 | `loment fmt/test/check` + `pprof` 式报告 | 端 |
| 标准库/网络 | `loment/lib` + net 线 + lompi | 端 |
| 结构化接口 | 结构化接口 + 能力约束 | 型 |
| 错误值 / `defer` / `panic` | `Result` + `defer` + trap | 型+面 |
| 向后兼容承诺 | LLS 兼容纪律 | 轴 |
| 泛型 | 泛型（已有单态化底座） | 型 |

**不拿的形式**：把 goroutine 当语言原语。目的（廉价并发）要；形式是结构化任务，
自动的那一档由 `par`，手写的那一档由库与 `thread_spawn`。

### 2.5 Java —— 可移植、JIT、生态

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 字节码/VM 解耦 | `vm` + LVM（多执行模型） | 轴 |
| JIT + 运行期画像 | LVM T3 + PGO；与 `no_vm` 并存 | 轴 |
| 成熟 GC | `gc_auto` / `gc_auto_alpha`（已有四层梯） | 轴 |
| 生态 / Maven / Gradle | lompi + Java 互操作（`docs/282`/`docs/283`） | 端 |
| 工具 / IDE / JFR | LSP + 调试器 + 结构化 tracing | 轴+端 |
| 名义类型 + 接口 + 泛型 | trait/接口/泛型（名义契约） | 型 |
| 受检异常（意图） | `Result` + 穷尽 `match`（不要语法噪声） | 型 |
| 反射/注解 | comptime 反射；默认无运行期反射 | 型 |
| 热更新 | 0.1.5.1 热更新线 | 轴 |
| 虚拟线程/结构化并发 | `par` 任务 + 虚拟线程库 | 轴+端 |
| JMM / happens-before | 可观察次序 + 原子语义成文 | 轴+型 |
| 向后兼容 | LVM 字节码 + LLS edition | 轴 |
| records/sealed/pattern | `struct`/`enum`/`match`（已有） | 型 |

**不拿的形式**：强制运行期。目的（可移植、自适应）要；`vm`/`runtime` 是开关，
不是语言的义务。

### 2.6 Python —— 人体工学与生态

| 拿走的目的 | 形式 | 落点 |
|---|---|---|
| 可读性 | `loment fmt` + 一种显然的写法 | 面 |
| REPL / 交互 | LVM T0 + `loment repl` | 轴 |
| 动态 / duck typing | **Python 表面的渐进类型**（边界检查，内核仍静态） | 面 |
| 内建数据结构与切片 | 数组/切片 + `std` | 型+端 |
| 一切皆对象 + 内省 | Potato 形式对象（不是运行期反射） | 轴 |
| 生成器 / 迭代器 | 显式迭代器协议 + 库（不做语言级惰性） | 端+面 |
| `with` / 上下文管理器 | `Drop` / `defer` | 型 |
| 标准库 / 生态 | CPython 互操作 + lompi | 端 |
| 装饰器 / 元编程 | comptime 的高阶函数 | 型 |
| async/await | 表面语法，语义仍是结构化任务 | 面 |
| 渐进类型 | 类型注解 + 检查（有注解就查） | 面 |

**不拿的形式**：把动态执行当唯一模式。目的（写得快、生态大）要；Python 是一个表面，
内核仍是静态、可证明、可并行的。

---

## 3. 一个程序，多个点：制度边界

whole-program 开关有一个硬限制：**一个程序只能是一个点**。但真实系统不是：
FujoOS 的核与驱动、一个 C 库与一个 Python 脚本、一个安全模块与一段裸指针代码，
本来就要住在一个产物里。

本计划最激进的一条：**把"整程序一个点"改成"单元一个点，边界被证明"。**

### 3.1 制度（regime）

每个单元可以声明自己的**制度**——五轴上的一个点：

```rust
// kernel.lomt —— 核单元
regime no_std, no_runtime, gc_manual, par_manual

pub fn handle(irq: u32) -> u32 { ... }
```

```rust
// driver.lomt —— 宿主单元
regime std, hosted, gc_auto, par_auto
use "kernel.lomt"

fn main() -> u32 {
    let r: u32 = boundary kernel::handle(31);   // 边界调用，契约检查
    return r;
}
```

`regime` 不是 `choose` 的别名：`choose` 是**整程序**的，`regime` 是**单元**的。
根单元不再"携带所有开关"，它**组合**各个单元的制度。

### 3.2 边界契约

每一条跨制度的边要证五件事，缺一条就拒：

| 契约 | 它回答什么 | 检查者 |
|---|---|---|
| **效应** | 被调方碰什么；调用方有没有那个能力 | 足迹 + 能力域 |
| **内存** | 实参/返回值是转移、借用还是复制；哪个 GC/分配器 | 所有权 + 区域 |
| **ABI** | C 约定还是 Loment 约定；布局是否一致 | 链接器 + 布局判据 |
| **失败** | trap / 错误值 / panic 怎么过边界 | 错误类型 + 最小次序 |
| **次序** | `par_auto` 的内部并行在边界处是一次 join | 任务语义 |

这条边界是**语义 FFI**，不是"C ABI 碰运气"。它也是把六门语言缝在一起的地方：
C 模块、Python 表面、Rust 安全模块、Java 托管服务，各自在自己的制度里，
只在边界上相遇，而边界是可证的。

### 3.3 制度图

把单元当节点、边界当边，程序就是一张**制度图**。它正好是 DGT 的用武之地：
编译期图类值 + 静态验证（`0.1.5.2`，伞 #1196）。编译器在这张图上检查：

1. 每条边的五条契约；
2. 图的每个环上的制度是否一致（不一致要显式的桥）；
3. 是否有单元声称的制度与它实际用的原语矛盾（例如 `no_runtime` 单元调了 `gc_auto`）。

产物里记 `regimes = {unit: {point}, boundaries: [...]}`，不读源码可判。

### 3.4 它不破坏"最后一个一等公民"

制度是五轴上的**点**，边界是点与点之间的**路**。轴仍然是八维，没有第九维；
制度图是**组合**，不是新轴。`docs/285` §2.3 的举证责任仍然成立：
一条新优点如果既不是轴值、也不是类型/表面/端口，它就不是这个空间里的东西。

---

## 4. 五轴的全激进目标

| 轴 | 全都要的目标 | 吸收自 | 机制 |
|---|---|---|---|
| **权** | 任何生态的库都只差一个 `port`；边界可证 | C 的 ABI、Java/Python 的生态、Go 的 stdlib | C ABI 双向 + hosted 端口 + 稳定产物 ABI + 能力域 + 沙箱 |
| **命** | 从 C 的手动到 Java 的 ZGC，一条连续谱；同一份源在每个点都被检查 | C 的手动、C++ 的 RAII、Rust 的所有权、Go/Java 的 GC | `no_gc` → `gc_manual` → `lms_*` → `gc_auto` → `gc_auto_alpha` + 全所有权 + `defer` + 每任务区域 |
| **机** | 一份源，所有执行模型：AOT / 字节码 / JIT / 热更新 / REPL / 交叉编译 | C 的原生、Java 的 JIT、Go 的快构建、Python 的 REPL | LVM T0–T3 + AOT + PGO + 热更新 + 单二进制 |
| **记** | 每一条主张都可机器核对；产物就是证据 | Rust 的诊断、Go 的工具、Java 的 JFR、Python 的内省 | Potato + LSPE + `--par-report` + **优点账本** + `--explain` + tracing |
| **时** | 一种调度语义，所有并发写法；默认确定性 | Go 的 goroutine、Java 的虚拟线程、Rust 的 `Send/Sync`、C/C++ 的 OpenMP、Python 的 async | `par`（保序自动）+ 结构化任务 + channel 库 + async 表面 + `thread_spawn`（手动）+ 工作窃取/投机 |

一句话：**权是"哪都能去"，命是"什么都能选"，机是"哪都能跑"，记是"什么都可证"，
时是"怎么并发都对"。**

---

## 5. 优点账本（六语言 × 四通道）

下表是账本的正文。状态：**已有** / **进行** / **目标**。代价不在此表。

### 5.1 C

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 薄抽象机、可预测 | 无隐藏分配/GC/隐式控制流 | 轴 | `no_std` + `no_runtime` + `gc_manual` + `no_gc` | 已有 |
| C ABI 通用货币 | 平台调用约定与布局 | 端 | `extern fn` + `--link` + C→Loment 反向 | 一半 |
| 编译器遍地 | 小语言、薄抽象机 | 轴 | 自研后端 + 交叉目标 + 裸机 | 已有 |
| 显式内存/指针 | 指针、手动分配、布局 | 型 | `ptr`/`load8`/`store8`/`alloc`/`free` + 边界计数 | 已有 |
| 小到能掌握 | 小规范、少关键字 | 面 | 小核 + 六表面 + `loment cheat` | 已有 |
| 工具链与调试 | GCC/Clang/调试器 | 端 | LLVM 后端 + `loment` CLI + lompi | 已有 |
| 不作主张的谦虚 | 不强制范式 | 轴 | 能力域 + `lom/*.lom` 契约 | 已有 |

### 5.2 C++

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 零成本抽象 | 单态化 + 内联 + 无动态派发 | 型 | 泛型 + 单态化 + DGT 布局特化 | 进行 |
| RAII / 确定性析构 | 作用域结束即释放 | 型+面 | `Drop` + `free` + `defer` | 一半 |
| `constexpr` | 编译期算常量 | 型 | `const` + comptime + DGT | 目标 |
| 静态反射（C++26） | 编译期检查/生成代码 | 型 | DGT/comptime 反射，无运行期反射 | 目标 |
| 模板/概念 | 约束泛型 | 型 | 泛型 + 能力约束 | 进行 |
| STL/算法 | 容器与算法库 | 端 | `std` / LibL / lompi | 进行 |
| 值/移动语义 | move/拷贝控制 | 型 | 所有权 + 移动子集 → 全移动 | 进行 |
| 模块/增量构建 | 模块化与缓存 | 端 | 单元 + 增量缓存 + lompi | 已有 |

### 5.3 Rust

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 所有权/借用 | move + 别名 XOR 可变 | 型 | E006/E007/E012 → 全所有权（推断优先） | 一半 |
| `Send`/`Sync` | 类型级并发安全 | 轴+型 | `par` 足迹 + 线程语义 | 设计 |
| ADT + 穷尽 match | 枚举/模式/穷尽 | 型 | `enum`/`match` | 已有 |
| trait + 单态化 | 静态派发、零成本 | 型 | trait/泛型 + 单态化 | 进行 |
| `Result` + `?` | 错误是值 + 早退 | 型+面 | `Result` + 穷尽 + `?` | 已有 |
| Cargo | 构建+包+测试+文档 | 端 | lompi + `loment` 子命令 | 已有 |
| 诊断质量 | 码 + 修法 + 解释 | 轴 | `loment_diag` + `--explain` + LSP | 已有 |
| 卫生宏 | 编译期代码生成 | 型 | comptime 的 Loment 函数 + DGT | 目标 |
| edition/兼容 | 多版本并存 | 轴 | LLS edition + 预算棘轮 | 进行 |
| async | 异步语法 | 面 | async over tasks（同一语义） | 目标 |

### 5.4 Go

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 简单/小语言 | 25 关键字、无继承/异常 | 面 | 小核 + 六表面 | 已有 |
| goroutine + channel | 轻量任务 + CSP | 轴+端 | `par` 结构化任务 + channel 库 | 设计 |
| 快速构建 | 简单 IR + 增量 | 轴 | LVM + 增量缓存 | 进行 |
| 单二进制/交叉编译 | 静态链接 | 轴 | 原生后端 + 静态链接 | 已有 |
| 工具链 | fmt/test/vet/pprof | 端 | `loment fmt/test/check` + 报告 | 已有 |
| 标准库/网络 | net/http 等 | 端 | `loment/lib` + net 线 + lompi | 进行 |
| 结构化接口 | 隐式满足 | 型 | 结构化接口 + 能力约束 | 目标 |
| 错误值/defer/panic | 显式错误、清理、崩溃 | 型+面 | `Result` + `defer` + trap | 一半 |
| 向后兼容 | Go 1 承诺 | 轴 | LLS 兼容纪律 | 已有 |
| 泛型 | 类型参数 | 型 | 泛型 | 进行 |

### 5.5 Java

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 字节码/VM | 语言与 VM 解耦 | 轴 | `vm` + LVM | 设计 |
| JIT/PGO | 运行期画像 | 轴 | LVM T3 + PGO | 目标 |
| 成熟 GC | 分代/并发/可换 | 轴 | `gc_auto` / `gc_auto_alpha` | 已有 |
| 生态/Maven | 依赖与框架 | 端 | lompi + Java 互操作 | 进行 |
| 工具/IDE/JFR | 调试与剖析 | 轴+端 | LSP + 调试器 + tracing | 进行 |
| 名义类型/接口/泛型 | 名字即契约 | 型 | trait/接口/泛型 | 进行 |
| 受检异常（意图） | 错误不可忽略 | 型 | `Result` + 穷尽 | 已有 |
| 反射/注解 | 元数据与生成 | 型 | comptime 反射（无运行期） | 目标 |
| 热更新 | 运行期替换 | 轴 | 0.1.5.1 热更新 | 进行 |
| 虚拟线程/结构化并发 | 轻量线程 | 轴+端 | `par` 任务 + 虚拟线程库 | 设计 |
| JMM | happens-before | 轴+型 | 可观察次序 + 原子语义 | 进行 |
| 向后兼容 | 字节码/源码 | 轴 | LVM 字节码 + LLS | 进行 |
| records/sealed/pattern | 现代 Java | 型 | `struct`/`enum`/`match` | 已有 |

### 5.6 Python

| 优点 | 机制 | 通道 | Loment 落点 | 状态 |
|---|---|---|---|---|
| 可读性 | 缩进/PEP 8 | 面 | `loment fmt` + 一种显然的写法 | 已有 |
| REPL/交互 | 解释执行 | 轴 | LVM T0 + `loment repl` | 进行 |
| 动态/duck typing | 运行期名字解析 | 面 | Python 表面的渐进类型（边界检查） | 目标 |
| 内建数据结构/切片 | dict/list/slice | 型+端 | 数组/切片 + `std` | 进行 |
| 一切皆对象/内省 | `getattr`/`dir`/元类 | 轴 | Potato 形式对象 | 已有 |
| 生成器/迭代器 | 惰性序列 | 端+面 | 显式迭代器协议（不做语言级惰性） | 目标 |
| `with`/上下文管理器 | 作用域释放 | 型 | `Drop` / `defer` | 已有 |
| 标准库/生态 | PyPI/AI | 端 | CPython 互操作 + lompi | 目标 |
| 装饰器/元编程 | 包裹语法 | 型 | comptime 高阶函数 | 目标 |
| async/await | 异步语法 | 面 | async over tasks | 目标 |
| 渐进类型 | 注解 + 检查 | 面 | 有注解就查 | 目标 |

---

## 6. 三个乘法，两个新装置

"全部优点"不是把 50 条功能排进路线图，而是沿三条轴做**乘法**，再加两个让它们
可被核对的装置。

### 6.1 执行乘法

一份 IR → 所有执行模型：

| 模式 | 来源 | Loment |
|---|---|---|
| 原生 AOT | C/C++/Rust/Go | 自研后端 + 静态链接 |
| 字节码解释 | Java/Python | LVM T0 |
| 基线编译 | Java/Go | LVM T2 |
| 优化/去优化 | Java JIT | LVM T3 + PGO |
| 热更新 | Java hot swap | 0.1.5.1 |
| REPL | Python | LVM T0 + `loment repl` |

判据只有一条：**同一份源在 AOT / 字节码 / JIT 下的可观察轨迹相同**——现有的
`test_gc_vm_lspe_and_lvm_agree_on_every_tier` 已经是这条判据的雏形，把它扩到所有档。

### 6.2 并发乘法

一种调度语义，所有写法：

| 写法 | 来源 | Loment |
|---|---|---|
| 自动并行 | —（这是 Loment 自己的） | `par_auto`：保序、零注解、零猜测 |
| goroutine/channel | Go | 结构化任务 + channel 库 |
| 虚拟线程 | Java | `par` 任务 + 库 |
| async/await | Python/Rust | 表面语法，语义仍是结构化任务 |
| 手写线程 | C/POSIX | `thread_spawn`（手动逃生舱） |
| 工作窃取/投机 | C++/Java/Rust | 机制第 2/3 阶 |

判据：所有写法在边界处归一到**同一份任务语义**（结构化、可观察次序、最小失败次序）。
不允许"另一种并发语义"，只允许另一种语法。

### 6.3 编译期乘法

| 能力 | 来源 | Loment |
|---|---|---|
| `constexpr` | C++ | `const` + comptime |
| 静态反射 | C++26 | DGT/comptime 反射 |
| 宏 | Rust/C | comptime 的 Loment 函数（不做独立宏语言） |
| 注解 | Java | comptime 元数据 + 契约 |
| 泛型单态化 | Rust/C++/Java | 泛型 + 单态化 + 布局特化 |

判据：编译期产物可复现；生成的代码与手写代码走同一条检查链；诊断仍是 Loment 的码与修法。

### 6.4 装置一：优点账本

`merits.lock`：每一条声称的优点一条记录，机器可读。

```
merit:        rust.ownership
from:         rust
mechanism:    move + alias-xor-mut
channel:      type
axes:         []
surface:      [rust]
criterion:    loment_borrow_test::test_alias_xor_mut
artifact:     potato.boundary
status:       landed | planned | refused
refused_why:  <只有"墙"才能填这一格>
```

`loment merits --check` 的规则：

1. `status: landed` ⇒ 必须有 `criterion` 与 `artifact`，且判据在门禁里；
2. `status: refused` ⇒ 必须点名 §8 的五面墙之一；
3. 一条优点若同时属于两条通道，记两条，不许合并成一条含糊的；
4. 账本里没有的"优点"不许出现在 README、官网、发布说明里。

这是"零沉默"从编译器扩展到**对外的每一句话**：**没有判据的优点不是优点，是宣传。**

### 6.5 装置二：制度图

§3 的 regime graph。它把"一个程序多个点"从口号变成一张可验证的图；
DGT 是它的实现底座。产物记 `regimes` 与 `boundaries`，链接器核。

---

## 7. 唯一判据：账本清零

一个版本可以发布，当且仅当：

1. `loment merits --check` 全绿（每条 `landed` 都有判据与产物字段）；
2. `loment_rule_parity` / `loment_p8_test` / 矩阵判据全绿（两份实现逐字节）；
3. 每一条 `refused` 都指向 §8 的一面墙，且墙的理由成文；
4. 新增的每一条判据都先在未修的实现上跑红（`docs/285` §6.3 的纪律）。

这条判据的作用不是"证明语言好"，是**证明我们没有在宣传里撒谎**。六门语言的优点
可以被吸收，但吸收的证据必须和代码在同一个门禁里。

---

## 8. 五面墙与唯一的不抄

墙是**结构**，不是代价。它们决定空间的形状，不决定我们做不做。

| 墙 | 两边 | 开关/边界的解法 |
|---|---|---|
| **可预测 vs 自适应** | C 的全静态 vs Java 的 JIT | `vm`/`runtime`/`gc`；一个单元一个点，制度边界组合 |
| **无运行期 vs 托管生态** | C 的 freestanding vs Java/Python 的生态 | `runtime`/`port`；边界就是端口 |
| **全静态 vs 全动态** | Rust 的所有权 vs Python 的 duck typing | 表面 + 渐进类型；内核静态，动态表面在边界检查 |
| **极小核 vs 极大表达** | Go 的 25 关键字 vs C++ 的模板 | 小核 + comptime/DGT + 库；表面把长尾藏起来 |
| **确定性 vs 自动 GC/并行** | Rust/C 的可预测 vs Go/Java 的自动 | `gc` 档 + `par` 的保序等价；程序自己选 |

**唯一真正的"不抄"：C 的 UB。** 因为"越界是未定义"不是一种风格，是把正确性
交给运气；Loment 的选择是 trap 或编译期拒（`docs/285` §3.4 的失败语义）。
这一条撞的不是墙，是底线。

`docs/205` 里另外四条"明确不抄"在本计划里全部升级：

| `docs/205` | 新计划 |
|---|---|
| R7 卫生宏 | comptime 的 Loment 函数 + DGT；不做独立宏语言，诊断不降级 |
| J2 JIT | `vm` 的一个值（LVM T3）；与 `no_vm` 并存 |
| P3 动态类型 | Python 表面的渐进类型；内核仍静态、可证 |
| P6 生成器 | 显式迭代器协议 + 库；不做语言级惰性 |
| C 的 UB | 仍然不抄（§8 的底线） |

这就是"全新"的地方：**旧账本的"不抄"是五条；新账本只剩一条底线，其余都变成了开关或表面。**

---

## 9. 波次（依赖顺序，不是预算顺序）

| 波 | 内容 | 为什么在这个位置 |
|---|---|---|
| **W1 内核** | 八维 + `par` S1/S2 + 所有权/效应/ADT/泛型 + 账本 + DGT 种子 | 没有类型与效应，后面的边界与并行都无从谈起 |
| **W2 制度** | `regime` 声明 + 边界契约 + 制度图 + LLS 大版本 | 边界要先有可证的效应与所有权 |
| **W3 执行** | LVM T0–T3 + AOT + JIT/PGO + 热更新 + REPL + 等价判据 | 没有 IR 就没有 JIT，也没有 REPL |
| **W4 表面与端** | 六表面 + 渐进类型 + `?`/`defer`/async/channel + C ABI 双向 + Java/Python/WASM 端口 | 表面要落在已稳定的语义与执行模型上 |
| **W5 收口** | 账本清零 + 矩阵 + LLS 1.0/2.0 + 轴闭合 | 最后才有人有资格说"全都要了" |

顺序是**依赖图**，不是"先做便宜的"。每一波都必须有可跑的行为差或产物差；
没有"只声明"的波（`docs/274` §1 的"最坏一格"）。

---

## 10. 与 `docs/205` 的差别（一句话版）

- 旧问题："抄哪几条？" 新问题："六个点在空间的哪里？"
- 旧裁决：5 条"明确不抄"。新裁决：1 条底线（UB）+ 5 面墙；其余全部进四条通道。
- 旧范围：C/Rust/Java/Python。新范围：+ C++、+ Go。
- 旧执行：一个程序一个点。新执行：一个程序可以多个点，边界被证明。
- 旧承诺：每条优点有裁决。新承诺：每条优点有**判据 + 产物字段**，否则不许对外说。

---

## 11. 一句

> 六门语言各自答对了一道题。Loment 不抢它们的答案，它把六张答卷做成一张
> **可切换的卷子**——并且让每一道题都有判据。

---

## Sources

- C：[C (programming language) — Wikipedia](https://en.wikipedia.org/wiki/C_(programming_language)) ·
  [Epitech, 2026](https://www.epitech.eu/2026/04/13/maitriser-langage-c-architecture-logicielle/) ·
  [Sololearn](https://www.sololearn.com/nl/Discuss/1783777/what-are-the-advantages-of-c-programming-language)
- C++：[Static Reflection in C++ — isocpp.org](https://isocpp.org/blog/2025/01/static-reflection-in-cpp-wu-yongwei) ·
  [C++26: Reflection, Memory Safety, Contracts, and a New Async Model — InfoQ](https://www.infoq.com/news/2026/04/cpp-26-reflection-safety-async/) ·
  [CppDepend, top 10 reasons](https://www.cppdepend.com/blog/top-10-reasons-modern-embedded-systems-engineering-is-shifting-to-cpp17/)
- Rust：[Rust vs C++ Comparison for 2026 — JetBrains](https://blog.jetbrains.com/rust/2025/12/16/rust-vs-cpp-comparison-for-2026/) ·
  [Why Rust is the go-to language for agent-assisted software development — Red Badger](https://content.red-badger.com/resources/why-rust-is-the-go-to-language-for-agent-assisted-software-development)
- Go：[What is Golang — Netguru](https://www.netguru.com/blog/what-is-golang) ·
  [Go 语言真正强的领域是什么 — Tencent Cloud](https://cloud.tencent.cn/developer/article/2640383) ·
  [Should I learn Go or Rust for backend? — Educative](https://www.educative.io/blog/should-i-learn-go-or-rust-for-backend)
- Java：[Java 25 LTS — Tencent Cloud](https://cloud.tencent.cn/developer/article/2658860) ·
  [Java pros and cons in 2026 — Netguru](https://www.netguru.com/blog/java-pros-and-cons) ·
  [Why the JVM Is Still Thriving in the Age of Cloud Native](https://podscan.fm/podcasts/the-programming-languages-podcast-with-fexingo-python-rust-javascript-and-modern-coding-1/episodes/why-the-jvm-is-still-thriving-in-the-age-of-cloud-native-1)
- Python：[Rust vs. Python — JetBrains](https://blog.jetbrains.com/rust/2025/11/10/rust-vs-python-finding-the-right-balance-between-speed-and-simplicity/) ·
  [Python vs Go — JetBrains](https://blog.jetbrains.com/go/2026/10/13/python-vs-go/) ·
  [The Python Tutorial 3.14](https://docs.python.org/zh-cn/3.14/tutorial/appetite.html) ·
  [The Pros and Cons of Python — Redwerk](https://redwerk.com/blog/pros-and-cons-of-python/)
