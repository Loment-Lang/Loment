# 220 · `syscall7`：把 Linux 的六个实参寄存器用满（带地址的 UDP 从此写得出来）

> 状态: **已落地**（2026-10-10）。起点是一件**写不出来**的事：`docs/217` §5 点名过的那个缺口 ——
> `sendto`(44) / `recvfrom`(45) 的第 6 个实参没地方放，于是"带地址的 UDP"在 Loment 里
> **根本表达不了**。本文记：为什么非加不可、动了几处登记、两个平台的实测，以及它顺带证明了什么。

## 0. 一句话

> 名字里的数就是**值**的个数（与 `syscall4` / `syscall6` 同一约定）：`syscall7` = `nr` + **6 个实参**，
> 正好把 `rdi/rsi/rdx/r10/r8/r9` 用满。加完这一条，Loment 才第一次**写得出**一个完整的 UDP 回显：
> 发出去 → 收到 → **知道是谁发的** → 回给他。

## 1. 为什么非得加一个内建，而不是"绕一下"

今天能做的只有一半：

```rust
// 能发、能收 —— 但收的那一方**不知道是谁发的**
connect(udp, peer, 16);        // 先把 UDP socket 连到对端
write(udp, buf, len);          // 出去
read(udp, buf, len);           // 回来
```

`recvfrom` 的 `from` 与 `fromlen` 是**第 5、6 个实参**，而 `syscall6` 只能递 5 个
（`nr + a0..a4`）—— 第 6 格交给寄存器里的**残留值**。Linux 会拿那个值当 `socklen_t` 校验：
要么 `EINVAL`，要么**读越界**（它是调用方给的缓冲区长度，不是一个可以猜的数）。

所以这不是"少一个便利"，是**语言面缺一格**：UDP 服务端（回给发送者）在 Loment 里没有写法。
`docs/217` §5 当时把它记成"点名不做"，并写明"谁先给语言侧补上第 6 个实参，谁就该把这一格补上" ——
本文就是那一格。

## 2. 动了哪几处（"清点所有读 L1 源的入口"）

一个内建名要在这 12 处对齐，少一处就是**静默的半死**（编译过、行为错）：

| 在哪 | 改什么 |
|---|---|
| `tools/lomentc.py` | 内建表 + **两处发射**：LLVM IR 的 inline-asm 约束、Rust 面那份 `asm!` |
| `tools/potato.py` | `BOUNDARY_BUILTINS`（另有一条判据钉它是内建表的**子集**） |
| `loment/selfhost/checker.lomt` | **5 处**：返回类型 ×3、内建名清单、实参个数 |
| `loment/selfhost/codegen.lomt` | 发射（约束串多一个 `{r9}`） |
| `loment/selfhost/potato.lomt` | `boundary.syscalls` 的计数 |
| `loment/tools/lomcli.lomt` | `loment stat` 的「边界操作」 |
| **`tools/lomelf.py` + `loment/tools/lomelf.lomt`** | **两个链接器**的寄存器装载表（`r9`） |
| `loment/lex/boundary.lomt` + `tools/loment_cli_test.py` | 词法夹具 + 它**写死**的五个数 |
| `tools/nltrans.py` / `.claude/skills/loment/SKILL.md` | 类型表 / 指南的内建清单 |
| 生成物 | 自举种子（自举侧改了）、发布清单、`SHA256SUMS` |

**孪生那一侧差点埋一个雷**：`lomelf.lomt` 的装载循环上界是**写死**的 `while code <= 6` ——
加了 `r9` 却忘了改它，`r9` 就**不会被装载**（编译过、跑起来传的是一格垃圾）。
这不是猜的：它是照着 `tools/lomelf.py` 里那张硬编码的寄存器顺序表对照出来的。

**判据自己抓了一条**：改完夹具后 `loment_cli_test` 当场红 ——
*"夹具自己的期望值漂了: 词法器数出 {… 'syscalls': 4 … 'total sites': 11}, 写的是 {… 3 … 10}"*。
也就是说这条纪律是**活的**：夹具的期望值写死在判据里，谁动夹具谁被拦一次。

## 3. 实测（同一份源，两个平台）

`tools/loment_pe_test.py` 的 `UDP_DEMO`：两个 UDP socket 各绑一个**内核挑的临时端口** →
客户端 `sendto` → 服务端 `recvfrom`（**拿到发送方地址**）→ 用那个地址 `sendto` 回一发 →
客户端 `recvfrom` 收回来。

| 平台 | 怎么跑 | 退出码 | stdout |
|---|---|---|---|
| PE（Windows 原生） | 本机直接跑 `.exe` | 0 | `UDP RECV OK` / `UDP OK` |
| ELF（Linux） | WSL 里直接跑 | 0 | 同上，**逐字节相同** |

两个方向的**六个实参**都走过了：出站 `sendto` 与入站 `recvfrom` 各一次，回程再来一次。
两侧的 `{r9}` 落点不同，也正是这次要一起证明的东西：ELF 侧靠内联 asm 约束，
PE 侧靠 shim **一进来就把它落栈**（`case_sendrecv` 的第一件事）——
这一格在本次之前**从来没被任何判据执行过**。

## 4. 顺带兑现的两件

* **`boundary` 又能数全了**：`syscall7` 进了两个实现的边界计数，夹具的头注与那五个数同步改了
  （3 → 4，10 → 11）。
* **`docs/217` §5 的"点名不做"关掉了一条**：原文说"`sendto`/`recvfrom` 的非 NULL 那一支
  今天没有动态判据（语言侧递不满第 6 个实参）" —— 现在有了，而且是**跨平台**的。

## 5. 点名不做

* **不加 `syscall8`**：Linux x86-64 的系统调用最多 6 个实参，第 7 格没有语义。
* **不改 `syscall6` 的实参个数**：那是**已在用**的接口（`loment/lib/proc.lomt` 就有），
  扩它要动所有调用点；加一个新名字比改旧名字便宜，也更诚实。
* **不做 `sendmsg` / `recvmsg`**：那是 `iovec` 那一套，仍挂在 `docs/217` §5 的名单上。
* **不把权限/准入塞进这一格**：`docs/218` 那套是**记录**不是**闸**，与本文正交。

## 6. 判据

| 主张 | 判据 |
|---|---|
| 内建表与两份实现一致 | `lomentc_test` **129/129**（含 `BOUNDARY_BUILTINS ⊆ BUILTINS` 那条） |
| 两个实现发出的产物逐字节相同 | `loment_potato_emit_test` **6/6**（自举侧 Potato 与参考逐字节） |
| `loment stat` 与真词法器数得一样 | `loment_cli_test` **42/42**（夹具那五个数写死） |
| 两个平台真的收发过带地址的 UDP | `test_pe_and_elf_agree_on_net_programs`：PE 原生 vs WSL/ELF **逐字节相同** |
| Linux 侧单独也能跑 | `test_elf_net_programs_run_on_linux`（CI 的 ubuntu runner 跑得到；PE 那半在 CI 里没有 runner） |

## 7. 与其它文档的关系

* **`docs/217`（两个平台的联网面）** —— 本文的前置，也是本文关掉那个缺口的出处（§5、§6）。
* **`docs/218`（准入：不设守卫设档案）** —— 正交。`boundary` 的"按面计数"仍然没做：
  本文只让它**数得到**新名字，没让它**按面细分**。
* **`docs/219`（世界端口）** —— "有名字的边"归它；`syscall7` 是**没名字的边**（内联 syscall）
  多了一格，正是它 §5.1 说"符号表看不见"的那一族。
* **`docs/205` R5（边界可见 ≠ 边界正确）** —— 本文没改那句：加的是一格**可见**，不是一格**正确**。
