# 311 · LVM 在 PE 上模拟 Linux：一份 guest ABI，三种落地

> 状态：**设计（未落地）** · 2026-11-06
> 上游：`docs/243` §3.4（VM 该如实回答还是自己合成）、`docs/217` / `docs/221`（PE shim 的翻译哲学）、
> `docs/309` §2.5（PE 上没有 228 的实测后果）、`docs/231`（select）、`docs/234`（socketpair/epoll）
> 判据：新增的三层，见 §5（今天都还不存在）

---

## 0. 一句话

> **guest 看不到宿主。** VM 的 guest ABI 固定是 Linux，与 LVM 自己被编成什么、跑在哪台机器上
> 无关；PE 上的 LVM 与 ELF 上的 LVM 对**同一份 IR** 必须给出同一份 stdout、退出码与陷阱站点。
> 今天做不到，原因不是"PE 少了什么库"，而是**"宿主能给什么"和"guest 该看到什么"被写成了同一张表**。

---

## 1. 问题：三份系统调用实现，一张不一致的名单

Loment 的产物有四种形态，系统调用面有三种独立实现：

| 实现 | 在哪 | 号的数量 | 谁写 |
|---|---|---|---|
| Linux 内核 | ELF 原生产物 | 全部 | 不是我们 |
| `__win_syscall`（shim） | PE 原生产物 | **29**（`lomelf.PE_SYSCALLS`） | `tools/lomelf.py` 生成的机器码 |
| `vm_syscall` | LVM（T0 解释） | **10** | `tools/lvm.py` 与 `loment/tools/lvm.lomt` 各一份 |

三者只在**有判据比较它们的地方**一致。实测的、今天是真不一致的三处：

| 号 | 名 | ELF | PE shim | LVM（Python） | LVM（自举） |
|---|---|---|---|---|---|
| 39 | `getpid` | ✓ | **−38** | 转宿主 `os.getpid()` | 转宿主 `syscall4(39)` |
| 63 | `uname` | ✓ `Linux` | **−38** | 合成 `Linux` | 合成 `Linux` |
| 201 | `time` | ✓ | **−38** | 转宿主 | 转宿主 |
| 228 | `clock_gettime` | ✓ | **−38** | 转宿主 `monotonic_ns` | 转宿主 `syscall4(228)` |
| 未知号 | —— | **−38** | **−38** | **`die(2)`** | **`die(2)`** |

两条后果，都是"静默"的：

1. **`lgc` 的预算在 PE 上不收紧**（`docs/309` §2.5 已写明）：窗口照开，只是没有截止。
   同一份源在 Linux 上"最多花 2ms 才开始下一次收集"、在 PE 上"随便收"，而**没有任何东西报错**。
2. **LVM 自己编成 PE 时，guest 会看到 −38**。`loment/tools/lvm.lomt` 的 `vm_syscall` 把
   39 / 201 / 228 **转发给宿主**；在 PE 上宿主是那张 29 个号的表，于是这三个号对 guest 变成
   −38 —— 而同一份 `lvm.lomt` 在 ELF 上是好的。**这正是本文件要消灭的形状**：
   同一份解释器，换一个宿主，guest 的可观察行为变了。

> LVM 今天**从来没有被编成 PE 过**：`loment_pe_test.TOOLS` 只编 `lomstatus` 与 `lomrel`。
> 所以第 2 条现在还没人踩到 —— 但它不是假设，是 `vm_syscall` 源码直接可读出来的。

---

## 2. 规则：guest 看不到宿主

`docs/243` §3.4 已经有一条规矩，但它只管**合成**这一类：

> "这台机器的事实"如实转出去；"被解释程序的身份 / 目标平台"由 VM 合成。

它不够用，因为它没说"宿主**给不了**的时候怎么办"。补全成四条：

| 类 | 判据 | 例子 | 宿主变了会怎样 |
|---|---|---|---|
| **S 合成** | 答案由 guest 的身份/目标平台决定 | `uname` → `Linux`；`/proc/self/cmdline` → 被解释程序自己的 argv | **不许变** |
| **E 在 VM 里模拟** | 答案只由 guest 状态决定 | `brk`（VM 自己的堆）、合成 fd 的 `read`/`close` | 不变 |
| **F 转发并归一化** | 答案里有一部分是"这台机器"的事实 | `getpid`、`time`、`clock_gettime` 的**增量** | 绝对值可变，**形状与符号不许变** |
| **R 拒绝** | 这条 ABI 在 VM 里没有实现 | 网络号（41…）、`poll`/`select`、文件系统 | **永远是同一个答案** |

三条硬约束（这是本文件的核心，不是注释）：

1. **R 类必须回 `-ENOSYS`（−38），不许 `die`。** Linux 对不认得的号就是 −38；guest 里
   `if syscall(...) == -38 { … }` 这种探测写法是合法程序。今天两份 LVM 都是"打一行字然后
   `die(2)`" —— 那是**工具**的 Loudness，不是 **guest** 的语义。两者要分开（§4.2）。
2. **F 类只允许绝对值漂移。** `getpid` 两次跑可以不同，但**符号**、返回类型、写回内存的
   **布局**（Linux x86-64 `timespec` = 两个 i64）必须一致。判据比的是"固定输入换一个变量"，
   不是"把差异抹掉"（`docs/243` §3.4.1 末尾那条已经这么写过一次）。
3. **S/E 类不许碰宿主。** 一旦某个 S 类答案是从宿主读来的，PE 与 ELF 就开始分叉 ——
   `uname` 是唯一一个已经被判据钉住的例子，它恰好是本文件要推广的形状。

---

## 3. 架构：两张表 + 一个宿主剖面

### 3.1 拆表

今天 `vm_syscall` 的一个 `if nr == N` 分支里，**三件事**写在一起：guest 语义、宿主能力、
归一化。拆成两张：

```rust
// guest 看到什么 —— 固定、与宿主无关。
// kind: S 合成 / E 模拟 / F 转发 / R 拒绝。
const GUEST_ABI: [Entry; 12] = [
    Entry { nr: 0,   name: "read",           kind: E },
    Entry { nr: 1,   name: "write",          kind: E },
    Entry { nr: 3,   name: "close",          kind: E },
    Entry { nr: 12,  name: "brk",            kind: E },
    Entry { nr: 39,  name: "getpid",         kind: F },
    Entry { nr: 60,  name: "exit",           kind: E },
    Entry { nr: 63,  name: "uname",          kind: S },
    Entry { nr: 201, name: "time",           kind: F },
    Entry { nr: 228, name: "clock_gettime",  kind: F },
    Entry { nr: 257, name: "openat",         kind: S },   // 只认 cmdline
    Entry { nr: 41,  name: "socket",         kind: R },   // 下面的 VM 不做网络
    Entry { nr: 7,   name: "poll",           kind: R },
];
```

```rust
// 宿主能给什么 —— 每台宿主一份，**这是唯一允许随平台变的东西**。
// elf: 内核那一张；pe: lomelf.PE_SYSCALLS。
fn host_cap(host: HostProfile, nr: u64) -> bool;

// F 类的一条：宿主给不了 ⇒ 对 guest 仍然是 -ENOSYS（不是 die，不是 0）。
fn forward(host: HostProfile, nr: u64, a: ptr, ...) -> i64;
```

**`GUEST_ABI` 是唯一的真源**：PE 的派发表、LVM 的解释器分支、判据里的期望集合，都从它派生。
这是 `lomelf.PE_SYSCALLS` 已经在用的手法（"表决定字节，双向可证"），只是当时只覆盖了 PE 一侧。

### 3.2 三种落地的形状

```text
        ┌───────────────┐
guest → │  GUEST_ABI    │  ← 固定：Linux
        └───────┬───────┘
                │ S / E          │ F                    │ R
                ▼                ▼                      ▼
         VM 自己算         host_cap(profile, nr)      −ENOSYS
                          ├ elf → 内核 syscall
                          └ pe  → __win_syscall
                                   └ 也不认识 → −ENOSYS（同一个答案）
```

* **ELF 原生产物**：没有 VM，内核就是 guest ABI 的实现。`GUEST_ABI` 对它只是**期望值**。
* **PE 原生产物**：`__win_syscall` 是 host_cap 的实现。它今天缺 39 / 201 / 228（§4.1）。
* **LVM**：`vm_syscall` 是 guest ABI 的实现，它自己的宿主调用走 host_cap。
  **编成 PE 时，LVM 就是一个 Linux ABI 的实现跑在另一个 Linux ABI 的实现上** —— 这是允许的，
  条件是**内层用到的每一个号都在外层的能力集里**（§4.3 的叠层判据）。

### 3.3 为什么 LVM 是这条线的正确落点

LVM 已经是"一台 Linux 机器"的模拟器：自带内存、`brk`、合成 fd、`uname`。它缺的从来不是
**能力**，是**把"宿主事实"与"guest 身份"分开**。做完这一步，LVM 同时变成：

* **PE 的参照实现**：PE 上任何一个号的行为，都有一份跑在 Linux 上的、可逐字节比的答案；
* **CI 上唯一能跑的那一半**：见 §5 L2。

---

## 4. 要补的缺口

### 4.1 PE shim：补三个号

| 号 | 名字 | 实现 | 归一化 |
|---|---|---|---|
| 39 | `getpid` | `GetCurrentProcessId()` | 截成 Linux 的 `pid_t`（i32） |
| 201 | `time(NULL)` | `GetSystemTimeAsFileTime` → Unix 秒 | 忽略 `NULL`；`tloc != 0` 时也写回 |
| 228 | `clock_gettime` | `QueryPerformanceCounter` + `QueryPerformanceFrequency`，或 `GetTickCount64` → 纳秒 | **按 Linux 的 `timespec` 写**：两个 i64（秒、纳秒），不是 Windows 的 `LARGE_INTEGER` |

228 是**必须**的：`lgc` 的预算今天在 PE 上是"不收紧"（`docs/309` §2.5）。补上之后，
同一份 `lgc` 源在 Windows 原生与 Linux 上行为一致 —— 这是这条线第一个能拿出去说的结果。

> 这三个号的实现都在 `emit_win_shim` 里，且都必须**从 `PE_SYSCALLS` 那张表生成**，
> 不新增手写机器码路径（`loment_pe_test::test_pe_dispatch_table_is_the_single_source` 已经在钉这条）。

### 4.2 LVM：未知号回 −ENOSYS，并给一份覆盖报告

改 `vm_syscall` 的兜底：

```rust
// 今天：打一行字，die(2) —— 这是**工具**的响亮，不是 guest 的语义。
// 之后：guest 拿到 Linux 会给的那个答案；"这一条没实现"记进覆盖报告。
syscall_unimplemented(hit, nr);
return ENOSYS as u64;          // -38
```

**为什么要变**：guest 里"探测一个号在不在"是合法程序；`die(2)` 把它变成一次崩溃，
而 Linux 上它只是拿到 −38。这不是"放宽"，是**把两个不同的东西分开**：
`die` 留给"调用本身是坏的"（越界指针、非合成 fd），−ENOSYS 留给"这个号没有"。

**代价（要承认）**：`docs/243` §3.4 原文写的是"表里没有的号一律点名拒"。这条要改，
而且 `lvm_test` 里可能有用例断言那行字（落地时要逐条核，不能默认它还绿）。
折中：**回 −38 的同时写进覆盖报告**（`--syscall-report`，列出"哪个号、哪个程序、第几条")，
于是"响亮度"从 stderr 挪到一份可数的产物上，判据可以对着它断言"语料没有打到表外的号"。

### 4.3 叠层：LVM-on-PE 的能力集包含关系

LVM 编成 PE 之后，`vm_syscall` 里所有 **F 类**的宿主调用都要经过 `__win_syscall`。
所以有一条静态可判的不变式：

> **∀ nr ∈ {vm_syscall 用到的宿主号}：nr ∈ PE_DISPATCH ∪ {228 等本轮补的}**

这条不需要 Windows：它是**两张表的包含关系**，读源码就能判（§5 L1）。
它也是唯一能在 CI 上跑住的那一半 —— 真正的行为比较见 §5 L3 的坦白的缺口。

### 4.4 不做（并且写在这里，不是留白）

* **VM 里的文件系统**：`read`/`openat` 只认合成 fd 与 `/proc/self/cmdline`（`docs/243` §3.4）。
  这不是"PE 的缺口"，是 VM 的**范围**；用到真实文件的程序不在可比集合里。
* **VM 里的网络**：41…54 这一族在 LVM 里是 R 类。理由同上：T0/T2/T3 没有 fd 表可以等。
* **PE 上的 `epoll`**：IOCP 不是一次翻译能补的（`docs/221` §7 的理由仍成立）。
* **文件 fd 的 `poll`**：PE 上只认 socket 型 fd，文件 fd 报 `POLLNVAL`（`docs/221` §3）——
  这是**已点名的不一致**，本文件不消灭它，只要求它留在名单上。

---

## 5. 判据：三层，前两层在 CI 上真跑

这条线最容易变成"在 Windows 上跑过一次，然后永远红不了"。所以判据按**能不能在 linux CI 上跑**
分层，第三层明写它跑了什么、没跑什么。

### L1 · 表的包含与一致（结构，任何平台）

* `GUEST_ABI` ↔ `PE_DISPATCH` ↔ `vm_syscall` 的分支：三方对同一组号给同一类答案
  （S/E/F/R 逐条相同）。**这是 `PE_SYSCALLS` 那条"表决定字节"规则向 VM 一侧的延长**。
* **叠层包含**：`vm_syscall` 用到的宿主号 ⊆ `PE_DISPATCH`（§4.3），缺一个就红，
  并在消息里点名 `(GuestNr, HostNr)`。

### L2 · 宿主剖面：**在 Linux 上模拟 PE 的宿主能力**（能进 CI 的关键）

给解释器一个 `HostProfile`：`elf` / `pe`。`pe` 这一份**只放行 `PE_DISPATCH` 里的号**，
其余照 `__win_syscall` 的兜底回 −38。于是：

> 同一份 IR，`host=elf` 与 `host=pe` 两次跑，stdout 字节 + 退出码必须相同；
> 允许的差异只有 §2 的 F 类绝对值（且判据里**不放行任何一条** —— 语料不读 `getpid`/`time`）。

这条**不需要 Windows**，因此能进 CI。它抓的是本轮的真问题（LVM 转发了一个 PE 没有的号），
而且抓得住"以后有人再转发一个新的宿主号、却没同时补 shim"这一类。

⚠ 它**不能**证明 `__win_syscall` 真的实现了那 29 个号 —— 那是 L3 的事。

### L3 · 真产物：Windows 上 PE × ELF 逐字节（**只在 Windows 跑，要明写**）

`loment_pe_test` 里 PE 那一侧的"真跑"用例今天都是 `SKIP: 非 Windows`（CI 是 ubuntu；
Linux 侧只有 `test_elf_net_programs_run_on_linux` 那一条会跑）。这条线不改变这个事实，只做两件事：

1. 把**新的三个号**（39/201/228）加进 `NET_CASES` 同一张表 —— PE 与 WSL 的 ELF 逐字节；
2. 在文档与判据输出里**明写"S 个用例在这个 runner 上是 SKIP"**，别让"全绿"读成"两边都跑过"。

### L4 · 新产物：LVM 自己编成 PE（本轮之后）

`loment_pe_test.TOOLS` 加一项 `("lvm", "loment/tools/lvm.lomt", […], "lvm")`：
用 PE 版 LVM 去解释一份语料，stdout + rc 与 `tools/lvm.py` 相同。这是本文件的**验收线**。

---

## 6. 分阶段

| 阶段 | 内容 | 完成时能说什么 | 依赖 |
|---|---|---|---|
| **P0** | PE shim 补 39 / 201 / 228（表驱动生成） | `lgc` 的预算在 PE 上真的收紧；`uname`/`time` 不再 −38 | 无 |
| **P1** | LVM 未知号改 −ENOSYS + `--syscall-report`；核 `lvm_test` 的既有断言 | guest 探测号拿到 Linux 的答案 | P0 |
| **P2** | `GUEST_ABI` 表 + `HostProfile` + L1/L2 判据 | CI 上能红："VM 转发了 PE 没有的号" | P1 |
| **P3** | `lvm.lomt` 编 PE 跑语料（L4） | LVM-on-PE 与 LVM-on-ELF 逐字节 | P2 |

P0 是唯一一件**今天就有一个用户可见症状**的事（`lgc` 预算），建议先做。

---

## 7. 风险与取舍

1. **`die(2)` → `−ENOSYS` 会改掉一条**写进 `docs/243` §3.4 的规矩。这是**故意的**：
   那条规矩把"工具要响"和"guest 要像 Linux"写成了一件事。改完要在 §3.4 留一行指向本文，
   并把 `lvm_test` 里断言那行字的用例逐条处理（**不是放宽断言**，是承认语义变了）。
2. **F 类的绝对值**：`getpid` / `time` / `clock_gettime` 的绝对值天然不可比。判据只能比
   符号与形状；任何"两次跑出来不一样"的用例都不该存在（`docs/243` §3.4 已经这么说过）。
3. **叠层下的两套 ABI**：LVM-on-PE 的内外两层都叫"Linux"，但**外层的能力集更小**。
   一旦内层转发了一个外层没有的号，症状是 guest 拿到 −38 —— 与"Linux 上这个号本来就不存在"
   **无法从 guest 侧区分**。所以 L1 的包含判据必须是**静态**的，不能等运行时才发现。
4. **Windows 用例仍然不在门禁里**。门禁只有 ubuntu runner（#382）—— 这是环境事实，
   不是本文件能消掉的。本文件的处理是：把能搬到 CI 的部分搬到 CI，把搬不动的**数出来**。

---

## 8. 与既有文档的关系

* 本文**不**重写 `docs/217` / `docs/221` 的翻译表（family / `SO_*` / 超时量纲）—— 那些是
  PE shim 的**内部**，本文只要求它们对 guest 不可见。
* 本文**不**改 `docs/309` 的 `lgc` 语义：预算在 PE 上从"不收紧"变成"真的收紧"，是
  **补齐**，不是改定义。
* 本文把 `docs/243` §3.4 的规矩从"合成 vs 转发"扩成"S / E / F / R 四类"，那条规矩的原文
  应改为指向本节。
