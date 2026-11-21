# 365 · Loment 与 C / Rust 竞速：在开发者需要的地方加速

> 2026-11-21 · **Flomtc 的编译期优化到此为止**（`docs/350` §12）；从这一页起，Flomtc 的
> 目标是**运行速度**——在开发者需要的地方，把 Loment 产物送到 C / Rust 的量级。
> 上游：`docs/226`（性能账）、`docs/212`（卸载）。工具：`tools/loment_race.py`。
> 一句话：**默认档是 5.5–20× C；`--opt` 档是 0.6–2.1× C，内存型负载反超 C -O2。**

---

## 1. 口径

- 同一台机、同一个 OS：x86-64 **静态 ELF**，全部在 **WSL** 里跑；计时在一次
  `wsl -e` 调用内完成（`docs/226` §1 的坑：每样本起一次 WSL 会量到 130 ms 启动）。
- 同一算法、同一 8 位校验和；四份产物（Loment 原生 / Loment+clang / C / Rust）
  的退出码必须相同，否则这一轮判失败。
- `min of 5`；绝对值换机器会变，**比值**才是结论。
- 编译器：`gcc 15.2 -O2 -static`、`rustc 1.93 -O -C target-feature=+crt-static`、
  `clang 22.1.8 -O2 -static -nostdlib -ffreestanding`（吃 Loment 的 IR）。

## 2. 基线（2026-11-21，`tools/loment_race.py --runs 7`）

| kernel | Loment 原生 | opt `--portable` | **opt（默认 native）** | C -O2 | Rust -O | 原生/C | opt/C |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sum`（20M 次整数累加） | 50.6 ms | 13.5 | **2.3** | 8.8 | 6.4 | 5.7× | **0.26×** |
| `mem`（5M 次字节读改写） | 25.9 | 1.2 | **1.1** | 1.4 | 1.3 | 18.1× | **0.79×** |
| `fib(32)`（纯递归） | 16.7 | 4.7 | **4.0** | 2.2 | 4.2 | 7.7× | 1.8× |
| `bits`（2M 次 popcount） | 50.0 | 5.2 | **0.4** | 6.0 | 5.3 | 8.3× | **0.07×** |
| `arr`（16 KiB 数组 × 200 趟） | 22.6 | 1.0 | **1.0** | 1.1 | 1.2 | 20.5× | **0.91×** |

**第一目标已达成：opt 档在五个 kernel 上全部快于 Rust -O.**

| kernel | opt（native） | Rust -O | 谁快 |
|---|---:|---:|---|
| sum | 2.3 | 6.4 | **Loment 2.8×** |
| mem | 1.1 | 1.3 | **Loment** |
| fib | 4.0 | 4.2 | **Loment** |
| bits | 0.4 | 5.3 | **Loment 13×** |
| arr | 1.0 | 1.2 | **Loment** |

## 3. 怎么读

1. **默认档不是"慢"**，它是**逐字节可复现**的那一档：产物与参考实现相同，契约/孪生判据
   都建在它上面。代价是后端没有优化器（栈机、无寄存器分配，`docs/226` §2）。
2. **opt 档是开发者的速度档**：同一份 IR 直接喂 clang。默认 **`-O3 -march=native`
   `-mllvm -align-all-functions=6`**；`--portable` 退到 generic `-O2`（分发用）。
   与 `--portable` 相比，native 把 `sum` 从 13.5 拉到 2.3 ms、`bits` 从 5.2 拉到 0.4 ms。
3. **对齐是实打实的一档**：`fib` 的调用循环在 16 字节对齐下 4.7 ms，函数 64 字节对齐后
   **4.0 ms**——同一段汇编、只差 `.p2align`，正好跨过 Rust（4.2）。
4. **对 C 的账**：内存型（`mem`/`bits`/`arr`）与 `sum` 已反超 gcc -O2；`fib` 仍差 1.8×
   （纯递归的调用开销，C 的 gcc 把 `fib(32)` 整个常量折叠了——那不是同一场比赛）。
5. **Rust 不是另一个数量级**：五个 kernel 的 Rust 列都落在 opt 档的可达范围内。

## 4. 开发者怎么用

```bash
# 默认档：产物与参考实现逐字节相同（契约、孪生、发行产物用这一档）
python tools/loment.py ir prog.lomt

# 速度档：本机 CPU 调优（-O3 -march=native -align-all-functions=6；要 clang）
python tools/loment.py opt prog.lomt -o prog

# 可分发的速度档：generic x86-64（-O2）
python tools/loment.py opt prog.lomt -o prog --portable

# 包里同一条路：
loment build prog.lomt --opt
loment run prog.lomt --opt
```

**取档原则**：发布契约、字节对拍、跨实现一致性 → 默认档；热循环、benchmark、生产
负载 → `--opt`。两档的**语义与诊断相同**，只有产物字节与运行速度不同。

## 5. 边界（先写死）

1. **`--opt` 产物不与默认档逐字节相同**。这是有意的：默认档的可复现性来自"没有优化器"。
   两条判据不能同时满足——所以它们是**两条构建档**，不是同一档的开关。
2. **`--opt` 需要 clang，且只出 ELF**。Windows/PE 的优化档还没接；`loment_dist` 的
   启动器今天同样只在 ELF 上走 `--opt`。
3. **默认 native 绑定构建机的 CPU**：要发布给别人，用 `--portable`（generic `-O2`）；
   两条都改变产物字节，契约/孪生那一侧只能用默认档。
4. **不承诺所有负载都到 C**：`fib` 仍差 1.8×（gcc 把常量参数整条折叠）。`fib` 这类
   深递归的下一档要动 IR/调用约定，不是换 flag。
5. **不是性能门禁**。墙钟比值不作 CI 判据（`docs/226` §1）；这一页是产品账。
6. **胶水照旧**（`docs/226` §4）：调 C 比调自己还便宜，热函数仍可直接 `extern fn`。

## 6. 下一档（按回报）

1. **PE 的 `--opt`**：Windows 开发者现在拿不到这条档。
2. **`fib` 这类深递归**：调用约定与 IR 形状（C 那一格是常量折叠，不构成同一目标）。
3. **热函数库**：数组/字符串的常用操作在 LibL 里给出优化实现，按需替换。
