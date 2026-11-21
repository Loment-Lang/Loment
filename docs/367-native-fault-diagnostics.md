# 367 · 原生档的段错误详细化：从宿主报告器到产物自述

> 状态: **设计 + stage 0 已实装**（2026-11-21）；stage 1 起在 `tools/lomelf.py`
> 上游: `docs/366`（LVM 档的同一件事）· `docs/356`（DSG：安全子集不段错误）·
> `docs/182`（Lomenterr 的形状）· `docs/263`（上限）· `docs/167`（原生后端）
> 判据: `tools/loment_segv_test.py`（stage 0）· 后文 §4 的 stage 1 判据
> ⚠ 不要把它和 `tools/loment_crash_test.py` 搞混：那一条管的是**编译器**在容量越界时
> 不许出信号（`#805` / `#868`–`#874`），管的是构建期，不是运行期。
> 一句话: **原生档现在只有内核那一行；这一页把它拆成两层 ——
> 先在宿主把信号变成可读报告（stage 0），再把报告器嵌进产物自己说（stage 1）。**

---

## 0. 问题

原生档（不经过 LVM 的产物）里，一次坏访存是：

* shell 的 `Segmentation fault`；
* 或启动器下的退出码 **139**。

进程自己一句话都没说。LVM 档已经有了 `docs/366` 的详细诊断（对象表、越界字节、
已归还对象、站点），两台机器对同一份源的报错能力因此差了一大截。

**原生档没有对象表**，所以它给不出 LVM 那种"对象 #2、右越界 28 字节"。它能给的是：

1. 信号与 `si_code`（地址没映射 / 不允许这次访问 / 对齐错误 …）；
2. 故障地址 `si_addr`；
3. 故障 PC 与 RSP；
4. **PC 对应的站点**（函数 / 块 / 第几条指令）—— 这需要产物里带一张表；
5. 读还是写 —— 需要信号帧里的页错误码；
6. 若是栈溢出，故障地址贴着 RSP。

3–6 要求产物**自己**有这些信息。这是 stage 1 的全部内容。

---

## 1. stage 0：宿主报告器（已实装）

`tools/loment_segv.py` 用 `ptrace` 跑产物，在致命信号上读 `siginfo` 与寄存器，
按 `lomenterr` 的形状打一份报告：

```text
loment: 进程死于信号 11（SIGSEGV）
error[SEGV]: 段错误（segmentation fault）
  --> /tmp/crash_null:0x4006c8
   |
   | 信号：11（SIGSEGV）；SEGV_MAPERR：地址没有映射到任何对象
   | 访问：0x0
   | PC：0x4006c8；RSP：0x7fffc55c1e40
   |
   = 事实：进程执行到 0x4006c8 时访问 0x0，收到 SIGSEGV（SEGV_MAPERR）。原生档没有运行期站点表，这一句是宿主替它说的。
   = 建议：用 `--debug` 构建、再给本工具 `--map <产物>`，位置就能解析成函数与 `.lomt` 源行；要逐站点的诊断（对象表 / 对象代 / 对象现场），用 LVM 档运行（`--substrate sw_cap`，见 docs/366）。
```

* 退出码 = 128 + 信号（与 shell 一致），所以脚本不用改。
* `--json` 一行一条，给编辑器/CI。
* `si_code` **按信号分家**：第一版用一张扁平表，第二次写键覆盖了第一次，于是空指针
  写被判成"地址冲突"（`BUS_ADRALN`）。现在键是 `(信号, code)`。
* `--map OBJ`：`llvm-objdump -d -l` 的行表把 PC 解析成「函数+偏移（源行）」。这要求
  产物带 `-g`；**默认产物没有行表**，解析不出来就不编。
* 平台：Linux / WSL。Windows 上没有这条（stage 4）。

**stage 0 的边界**：报告发生在宿主。直接跑产物还是只有内核那一行。这正是 stage 1。

---

## 2. stage 1 的机制（已用最小原型验证）

### 2.1 内核投递需要 `SA_RESTORER`

在 x86-64 Linux 上，`rt_sigaction`(13) 加一个 `{handler, flags, restorer, mask}` 的
`struct sigaction`。**原型实测**：只装 handler（flags=0）时 `rt_sigaction` 返回 0，
但信号来了**没人接**，进程照旧被 SIGSEGV 杀死；把 `sa_flags` 加上
`SA_RESTORER`（`0x04000000`）并把 `sa_restorer` 指到一个 `rt_sigreturn`(15) 的小函数，
handler 才真的跑起来。这一条写在这里，免得下一个人再踩。

（glibc 的 `sigaction()` 总是替你做这件事；**裸 `rt_sigaction` 不会**。）

### 2.2 内核的调用约定 ≠ lomelf 的栈机约定

lomelf 的内部调用是**栈机**：调用方把实参逆序压栈，被调方从栈上读形参
（`loment_elf_test` 的 `thread_spawn` 那段字面机器码注释也是同一条）。

内核调 handler 是 SysV：`rdi=信号`、`rsi=siginfo`、`rdx=ucontext`，实参在**寄存器**里。
所以不能直接把一个带三个形参的 IR 函数当 handler —— 它读不到寄存器。

⇒ 一个**机器码 shim**（lomelf 发射，字面字节）：

```asm
; 进门前：rdi=signo, rsi=siginfo, rdx=ucontext
mov dword ptr [abs __lom_sig], edi     ; 89 3C 25 <disp32>
mov qword ptr [abs __lom_info], rsi    ; 48 89 34 25 <disp32>
mov qword ptr [abs __lom_uctx], rdx    ; 48 89 14 25 <disp32>
call __lom_handler                     ; E8 <rel32>
ud2                                    ; 0F 0B（handler 不该返回）
```

`__lom_handler` 是普通的**零形参 IR 函数**，从三个全局里读现场。`call` 只压返回地址，
与 lomelf 对一个零形参函数的调用序列一致。shim 放在 `.text`（数据段不可执行）；
三个 `disp32` 是数据段的绝对地址（静态 ELF 的 `DATA_VADDR` 是常量），handler 的
`rel32` 在发射 handler 之后回填。

### 2.3 栈溢出：备栈

栈溢出时正常栈已经不可用，handler 自己会跟着崩。装 handler 时同时
`sigaltstack`(131) 给一块 `.bss` 里的备栈（32 KiB），`sa_flags` 加
`SA_ONSTACK`（`0x08000000`）。故障地址贴着 RSP 时，报告里要直说"像是栈溢出"。

### 2.4 站点表

lomelf 在编码每个 `load` / `store` / `getelementptr` / `atomicrmw` 时知道
（函数、块、块内序号）与该指令的代码地址。产物里带两张表：

| 表 | 内容 | 用途 |
|---|---|---|
| `__lom_sites` | 每条访存：(code_addr u64, fn_idx u32, blk_idx u32, ins_idx u32) | PC → 站点（二分） |
| `__lom_names` | 函数名与块标签的字节串 + 一张 (name_off, name_len) 表 | 打印站点 |
| `__lom_fns` | 每个函数：(start u64, end u64, name_off u32, name_len u32) | PC 不在访存点上时也能点名函数 |

站点按地址升序（发射顺序天然升序）。报告取 **地址 ≤ PC 的最后一条**，并注明
`PC - site_addr` 的偏移。表进 `.data`（只读更贴切，但当前 ELF 只有 `.data`）。

**体积**：每条访存 20 字节；一份 200 处访存的程序 ≈ 4 KiB；名字表按去重后计。
stage 1 先按这个形状落，**效果与代价都量出来**再决定要不要压缩（`docs/263` 的纪律）。

### 2.5 报告与退出

handler 在备栈上跑，按 `lomenterr` 形状写 `__lom_msg`（`write(2, …)`）：

```text
loment: 段错误（SIGSEGV）
error[SEGV]: 访问越界（信号 11）
  --> /path/prog:0x4011a2
   |
   | 信号：11（SIGSEGV）；SEGV_MAPERR：地址没有映射到任何对象
   | 访问：0x0
   | PC：0x4011a2；RSP：0x7ffd…
   | 站点：_start / entry / 第 13 条（load，4 字节；PC 距该指令 +7）
   |
   = 事实：…
   = 建议：…
```

打印完先 `rt_sigaction(sig, SIG_DFL)`，再 `tgkill(pid, tid, sig)` —— 进程仍然**死于
信号**（shell 的 `Segmentation fault` 与 139 的语义不变）。`tgkill` 失败才
`exit_group(128+sig)`。

数字格式：地址用十六进制（小写、`0x` 前缀），站点序号用十进制。IR 里各写一个
6 行的转换循环（`hex_digit` / `dec_digit`）。

### 2.6 入口包装

产物入口改成 lomelf 注入的 `__lom_entry`：

```text
sigaltstack(&__lom_alt, 0)
rt_sigaction(11/7/4/8/6, &__lom_act, 0, 8)   // SA_SIGINFO|SA_ONSTACK|SA_RESTORER
call <原入口>                                 // _start（或 main）
exit_group(0)
```

`SA_SIGINFO` 打开才拿得到 `siginfo`/`ucontext` 指针。五个信号：SIGSEGV(11)、
SIGBUS(7)、SIGILL(4)（含 `ud2`）、SIGFPE(8)、SIGABRT(6)。

---

## 3. 默认关，先证再开

stage 1 挂在 `compile_ll(..., fault_report=True)`（以及 CLI 的 `--fault-report`）上，
**默认关**。理由有两条，都是本仓的既有纪律：

1. **默认产物逐字节不变**（`docs/175` §3.4 那条）。加了入口包装、备栈与两张表，
   每一份原生产物的字节都会变；先把"开了以后对"证出来，再决定默认值。
2. **两个链接器要逐字节一致**（`docs/167` / `loment_elf_test` 的孪生那格）。
   lomelf 的参考实现与自举镜像必须发同一串字节 —— stage 1 先做参考侧，
   stage 2 做镜像，stage 2 的判据才允许把开关打开。

---

## 4. stage 划分

| stage | 内容 | 判据 |
|---|---|---|
| **0** | 宿主报告器（`tools/loment_segv.py`） | `loment_segv_test`：正常程序不误报；空指针读/写、SIGILL 三格有报告；退出码 128+sig |
| **1** | 参考 lomelf：入口包装 + shim + handler + 站点表（`--fault-report`，默认关） | `loment_segv_test` 的"产物自述"格：同一份崩溃程序，**直接跑**就有报告；PC 指回站点；PC 不在站点上时点名函数；栈溢出用备栈 |
| **2** | 自举镜像 `loment/tools/lomelf.lomt` 同形 | `loment_elf_test` 的孪生格：开关打开时两边产物**逐字节相同** |
| **3** | 默认打开 | 现有 ELF 判据全绿；体积与启动开销记账（`docs/263`） |
| **4** | Windows PE | PE 上不是信号，是异常处理器；形状相同、机制另写（lomelf 的 PE 发射器 + `win_shim`） |
| **5** | 语言级 fault 走同一条通道（`docs/356` §5 的 `__loment_fault`） | 编译期已知的 panic / trap 站点带 kind + witness，不再只靠信号 |

**clang 路**（`loment_elf_test` 的 (a) 路）不在 stage 1–3 里：它不经 lomelf，注入点
在构建命令上，是另一笔。stage 0 的宿主报告器对它是可用的。

---

## 5. 我不主张

1. **不主张 stage 0 让产物自己说话**。它在宿主；直接跑产物还是内核那一行。
2. **不主张原生能给 LVM 那种对象细节**。没有对象表就没有"右越界 28 字节"；
   原生能给的是信号、地址、PC、站点、访问种类与宽度，以及栈溢出的判据。
3. **不主张源行**。默认产物没有行表；`--debug` + `--map` 是 stage 0 的一条可选路。
4. **不主张 Windows 与 Linux 同形**（stage 4）。
5. **不主张体积免费**。站点表与备栈的代价要量（stage 3 的判据）。

---

## 6. 与其它文档的关系

* `docs/366` —— LVM 档的同一条需求；两档共用"首行稳定 + Lomenterr 形状"的口径。
* `docs/356` —— DSG 的 `__loment_fault(kind, site, witness)` 是 stage 5 的规范；
  本文的 stage 1 是它的信号兜底那一半（§356 §6.2 的 L2）。
* `docs/167` —— 原生后端与两只链接器的逐字节孪生。
* `docs/182` §5.3 —— 两侧的**码**与**形状**一致、文本各写各的；本文沿用。
