# 309 · `lgc`：手动 GC 时间控制（Tur 2026.3）

> 状态：**已落地**（2026-11-05）· 版次：`tur2026.3`（`docs/305` 封了 2026.2）
> 上游：`docs/210`（四层梯与收集器）、`docs/226`（性能账）、`docs/175` §3.4（三档语义）
> Potato：**v21** 的 `gc_control`（`docs/147`）

---

## 1. 要解决的问题

今天"收集器什么时候跑"完全由**编译期**决定：分配量过 32 KiB（`gc_auto`）或过自适应阈值
（`gc_auto_alpha`）就收一次，`gc_manual` 则一次都不收。这三档都没有给程序一个**明确的
时间维度**：既不能在一段关键路径上要求"别收"，也不能说"这一帧最多花 2ms 收"。

`lgc` 补的就是这一维，并且在**每一档**都成立：

```rust
lgc end;                  // 暂停：此后分配不再触发收集（自动两档也一样）
lgc start;                // 恢复：回到本档的默认状态
lgc start { … }           // 词法窗口：块内开启收集，出块恢复进入前的状态
lgc start(10ms) { … }     // 同上，外加一个**协作式**时间预算
```

**它不是第四档**：`choose gc` 仍是唯一的档位选择；`lgc` 只控制那一档的收集器
**何时允许开始**。产物里仍然写着 `gc: gc_manual` / `gc_auto` / …，另加一个
`gc_control` 说明这份程序用了多少控制语句。

## 2. 语义

### 2.1 三档下的默认值

| `choose gc` | `@__loment_gc_on` 初值 | `lgc end` 之后 | 窗口内 |
|---|---|---|---|
| `gc_manual`（默认档） | **0** | 0 | 1（窗口才收集） |
| `gc_auto` | 1 | 0 | 1 |
| `gc_auto_alpha` | 1 | 0 | 1 |
| `no_gc` | —— | **编译错误 E039** | 同左 |

`gc_manual` 的旧承诺"产物里没有收集器"**对没写 `lgc` 的程序一个字节都不变**；
写了 `lgc` 的单元才带上收集器，且默认关。这是这一维唯一被允许的例外，产物里以
`gc_control.manual_collector = true` 写出来。

### 2.2 预算是**协作式**的，不是抢占式

预算只在"要不要**开始**一次收集"处检查：

```text
on == 0                -> 不收
deadline == 0          -> 没有预算，按原触发条件
now >= deadline        -> 不收
otherwise              -> 按原触发条件
```

一旦开始，这一次收集会跑完。**为什么不中途停**：标记到一半停下会把堆留在不一致状态
（标记集、空闲链表、前沿三者都要一起回滚），那需要一整套可中断收集器；这一版不做，
并在文档里明写边界。预算买的是"这一帧不会再起新的收集"，不是"收集绝不超过 2ms"。

### 2.3 窗口与 `return`

窗口是**词法**的：进入时保存 `on` / `deadline`，退出时恢复。`return` 提前离开时，
发射器在 `ret` 之前按**逆序**恢复所有打开的窗口；嵌套窗口各存各的。`break` /
`continue` 不离开函数，窗口保持打开（它们本来就在窗口的块里）。

`lgc end` / `lgc start` 是**显式状态语句**，不做词法恢复：它们改的就是当前状态，
直到下一次语句或窗口恢复覆盖它。

### 2.4 时长

`lgc start(<时长>)` 只收**编译期**整数 + 单位：

| 单位 | 纳秒 |
|---|---|
| `ns` | 1 |
| `us` | 1 000 |
| `ms` | 1 000 000 |
| `s` | 1 000 000 000 |
| `m` | 60 000 000 000 |

裸数字按纳秒收。`0` = 不限时（窗口照开，不设截止）。变量预算要两个实现一致的类型推断，
**v1 不做**：写 `lgc start(frame_budget)` 是解析期错误。

### 2.5 时钟

`clock_gettime(CLOCK_MONOTONIC)`（Linux syscall 228）：

* **ELF / native**：真时钟，预算生效；
* **LVM T0/T2/T3**：解释器把 228 如实转给宿主 `time.monotonic_ns()`，三档共用同一个
  `Executor.syscall`；
* **PE**：`syscall 228` 目前不在 Windows 垫片的表里（返回 `-ENOSYS`）。`__loment_now_ns`
  在 syscall 返回负时回 **0** —— 预算因此**不会收紧**（窗口仍开，收集按原条件跑）。
  这是**明写的边界**，不是静默：需要预算的代码应跑在 ELF 或 VM 上；
* **FujoOS 内核**：`clock_gettime` 今天是返回 0 的占位 —— 同一后果（预算不收紧）。

## 3. 运行时形状（不读源码可判的部分）

源里出现任何 `lgc` 时，产物额外带：

```llvm
@__loment_gc_on       = internal global i32 <0|1>   ; manual=0, auto/alpha=1
@__loment_gc_deadline = internal global i64 0       ; 0 = 不限时
```

以及四个函数：

| 函数 | 做什么 |
|---|---|
| `__loment_now_ns()` | 读单调钟；读不到回 0 |
| `__loment_gc_allow()` | `on != 0` 且（无预算 或 `now < deadline`） |
| `__loment_collect_now()` | 允许才调 `__loment_collect`（OOM 入口 A 用） |
| `__loment_maybe_collect_ctl(i32)` | 允许才调原来的 `__loment_maybe_collect` |

**原来的收集器一行不改**：分配点改调 `_ctl` 包装，OOM 救援改调 `collect_now`。
没写 `lgc` 的程序连这些符号都不出现（`lomentc_test` 与 IR 逐字节比对钉住）。

## 4. 判据

| 判据 | 钉什么 |
|---|---|
| `lomentc_test::test_lgc_is_a_tur2026_3_feature` | 2026.3 收下；2026.2 拒；运行期预算拒；带预算必须带块 |
| `lomentc_test::test_lgc_is_refused_under_no_gc` | E039，且分类表认识它 |
| `lomentc_test::test_lgc_control_runtime_appears_only_when_used` | 没写 `lgc` 的单元没有控制符号；写了的有；manual 初值 0 / auto 初值 1 |
| `lomentc_test::test_lgc_window_saves_and_restores_even_on_return` | 入口块两个槽；`2ms` 折成 2 000 000；`ret` 前恢复 |
| `loment_elf_test::test_lgc_pause_and_budget_really_gate_collection` | 五格真跑：窗口收、暂停不收、`1ns` 预算不收 |
| `loment_p8_test`（孪生） | 同一份源，两个实现逐字节一致 |
| `potato_test::test_v21_requires_the_gc_control_counts` | v21 字段的七条反例；v20 对象仍合法 |

## 5. 没做的（写明）

* **抢占式中断收集**：预算是"不开始新收集"，不是"收集绝不超过 N 微秒"（§2.2）。
* **变量预算**：只收编译期时长（§2.4）。
* **PE / FujoOS 的真时钟**：228 在那两处还没实现，预算退化为"不收紧"（§2.5）。
* **`no_gc` 下的 `lgc`**：定义上矛盾，编译期拒（E039），不给运行期退化。
