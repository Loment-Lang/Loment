# 367 · 原生档的段错误详细化：从宿主报告器到产物自述

> 状态: **stage 0 / 1 / 2 / 2.5 已实装**（2026-11-21）；**stage 4（PE）已实装**（2026-11-22，
> 参考发射器那一半 —— 自举镜像尚未跟进）；stage 3 / 5 是计划
> 上游: `docs/366`（LVM 档的同一件事）· `docs/356`（DSG：安全子集不段错误）·
> `docs/182`（Lomenterr 的形状）· `docs/263`（上限）· `docs/167`（原生后端）
> 判据: `tools/loment_segv_test.py`（10 条：stage 0 四条 + 产物自述六条）·
> `tools/loment_elf_test.py::test_mirror_fault_report_matches_reference`（stage 2 的孪生格）
> ⚠ 不要把它和 `tools/loment_crash_test.py` 搞混：那一条管的是**编译器**在容量越界时
> 不许出信号（`#805` / `#868`–`#874`），管的是构建期，不是运行期。
> 一句话: **原生档现在只有内核那一行；这一页把它拆成两层 ——
> 先在宿主把信号变成可读报告（stage 0），再把报告器嵌进产物自己说（stage 1 已落地）。**

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
   stage 2 做镜像（已逐字节相同，§4.2）；stage 3 才谈默认值。

---

## 4. stage 划分

| stage | 内容 | 判据 |
|---|---|---|
| **0** | 宿主报告器（`tools/loment_segv.py`） | `loment_segv_test`：正常程序不误报；空指针读/写、SIGILL 三格有报告；退出码 128+sig |
| **1** ✅ | 参考 lomelf：入口包装 + shim + handler + 站点表（`--fault-report`，默认关） | `loment_segv_test` E–H：同一份崩溃程序，**直接跑**就有报告；站点名（`store16 / entry / 第 11 条`）、读写方向、栈溢出判据、`ud2` 是 ILL；默认关时产物沉默 |
| **2** ✅ | 自举镜像 `loment/tools/lomelf.lomt` 同形 | `loment_elf_test` 的孪生格：开关打开时两边产物**逐字节相同**（`_start` / `main` 两个入口） |
| **2.5** ✅ | 可恢复故障：`--fault-heal N` 把未映射页置零后从同一条指令继续，预算用完才死 | `loment_segv_test` 判据 J：第一页修复后继续、第二页致命；孪生格带 `--fault-heal 2` |
| **3** | 默认打开 | 现有 ELF 判据全绿；体积与启动开销记账（`docs/263`） |
| **4** ✅ | Windows PE（参考发射器） | `loment_pe_test::test_pe_fault_report_is_default_off`（默认关、逐字节同，打开后有 VEH + 文案）· `test_pe_fault_report_prints_site_on_windows`（坏访存 / `ud2` 的码、地址、PC、RSP、站点；退出码是异常码）· `test_pe_fault_heal_is_refused_for_pe`（`--fault-heal` 点名拒） |
| **5** | 语言级 fault 走同一条通道（`docs/356` §5 的 `__loment_fault`） | 编译期已知的 panic / trap 站点带 kind + witness，不再只靠信号 |

**clang 路**（`loment_elf_test` 的 (a) 路）不在 stage 1–3 里：它不经 lomelf，注入点
在构建命令上，是另一笔。stage 0 的宿主报告器对它是可用的。

### 4.1 stage 1 的实际输出（本机实测）

`--fault-report` 打开后，空指针写直接跑出来是：

```text
loment: SIGSEGV（信号 11）
error[SEGV]: 段错误（segmentation fault）
  --> 原生产物:0x00000000004006c8
   | 信号：11
   | 地址：0x0000000000000000（写）
   | PC：0x00000000004006c8；RSP：0x00007ffe8c78b0c0
   | 站点：store16 / entry / 第 11 条（距该指令 0x11）
   |
   = 事实：原生档在执行上述 PC 时收到该信号。它没有对象表，所以不给对象现场；站点由产物里的表解析。
   = 建议：……
```

无限递归（栈溢出）那一格多一行判据：

```text
   | 判据：故障地址贴着 RSP（相差 < 64 KiB）—— 像是栈溢出
```

**代价**（同一份空指针夹具，实测）：默认 **8,568 B** → 打开后 **28,000 B**（+19,432 B，
约 **3.27 倍**；stage 2.5 的修复分支与它是同一段运行期，`--fault-heal N` 只改入口里的
一个立即数，**不再增加文件体积**）。来源：handler 的 IR（定宽十六进制打印、五路标题分派、站点查找）、
站点/函数/块/名字四张表（`.data`，每条访存 24 B）、以及 32 KiB 备栈（`.bss`，**不计入
文件**）。这个倍数对小产物很难看 —— stage 3 默认打开之前必须再谈一次（缩短打印、合并
字符串、或对极小产物退化成 stage 0 的宿主报告器），并把数记进 `docs/263`。


### 4.2 stage 2：自举镜像（本机实测，逐字节相同）

镜像这一侧不是"照着参考再写一遍运行期"，而是**同一段运行期文本**：与程序无关的那一段
（消息池、`__wrhex` / `__wrnum`、handler、shim 的函数头）在 `loment/tools/lomelf.lomt`
里是一串十六进制常量，由 `tools/lomelf.py` 的 `_fault_runtime_fixed()` 导出。镜像在
用户文本之后接上：四行表声明 + 这段常量 + 入口包装（按用户入口名生成），然后走它自己的
解析/发射/回填。四张表的内容（站点地址、函数区间、块名、名字池）由镜像**独立**扫描与
回填 —— 这条孪生判据因此真的在比"两边对同一份 IR 的判断"，而不是比同一段字节被抄了两遍。

为了让它成立，这一轮改了四处：

1. **条数从表头读**（`docs/367` 的这一笔）：handler 的站点/函数循环上界原先写死在 IR 里，
   是 `nsites` / `nfns` 的函数 —— 那段文本因此**随程序变**。改成
   `load i32, ptr @__lom_sites` / `@__lom_fns` 之后，它才与程序无关。
2. **四张表的位置**：声明写成 `zeroinitializer`，两边各自在**收集全局的那一刻**给一个
   标记字节（参考给 `g.data`，镜像在 `one_global` 里给 `gb[doff]`）。表的真实内容发射后
   整片回填，标记不出现在产物里；但布局因此与"表在文本里排在消息池之前"一致。
3. **abs32 回填**（镜像的 `resolve_fixups` 新增 kind 2）：shim 里 `mov [abs32], edi`
   三个槽指向 bss 里的信号全局，而 bss 的地址要等 `place_bss` —— 发射那一刻定不下来。
4. **没有标签的入口块**：参考给它合成名字 `entry`；镜像在扫到第一条指令时补一个
   `FB_NOFF = NO` 的块，回填时按字面量 `entry` 进名字池（长度 5，不是 0 —— 第一版漏了
   这一格，孪生格当场在 `.data` 的第 1409 字节上抓住）。

**顺带修掉的一个 stage 1 缺陷**：`fn main` 入口那条路发出的是 `call i32 @main`
（**没有括号**），参考解析器的调用正则要求 `(...)` —— 于是 `fn main` 的 `--fault-report`
产物**根本编不出来**，而 stage 1 的全部判据都用 `_start`，从没走过这条路。
`loment_segv_test` 判据 I 拿一份 `main` 夹具钉住它；孪生格也带一个 `main` 入口。

**镜像的上限**（越界是**点名退出**，不是静默丢）：站点 8,192 条、用户函数 4,096 个、
基本块 16,384 个、名字池 64 KiB；暂存区 640 KiB（`M_FAULT`，`ARENA` 同比 +640 KiB）。

**实测**：同一份空指针夹具（Loment 前端 → `.ll`），参考与镜像各自 `--fault-report`
编出的 ELF **逐字节相同**（28,000 B）；直接跑，两份报告除 RSP（ASLR）外逐字节相同 ——
`error[SEGV]`、地址 0x0（写）、PC、`站点：store16 / entry / 第 11 条`、事实/建议全在。
无限递归那一格两份报告也相同（`像是栈溢出` 那一行在）。


### 4.3 stage 2.5：可恢复故障（`--fault-heal N`）

**一句话**：信号是 SIGSEGV、`si_code` 是 `SEGV_MAPERR`、地址落在低 1 MiB、预算还有时，handler 用
`mmap(MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS, PROT_READ|WRITE)` 把**那一页**置零，
打一行修复记录，然后 `rt_sigreturn` —— 内核从**同一条指令**重新执行，读/写都落在新页上。
预算存在 `@__lom_heal`（8 字节）里；`0` 就是关闭，所以"报告"与"修复"共享同一段运行期，
`--fault-heal N` 只改入口里的一个立即数（文件体积不变）。

实测（`docs/367` 判据 J 的夹具）：

```text
loment: 故障已修复（未映射页置零）：0x0000000000010000；剩余 1 次
```

两条边界，都是实测出来的：

1. **页 0 修不了**：内核加固 `vm.mmap_min_addr` 默认 64 KiB，MAP_FIXED 到 0 被拒
   （EPERM）—— 空指针仍然走致命报告。这不是实现的缺陷；要看它，得改内核参数或提权。
2. **必须补 `add rsp, 8`**：内核进 handler 时 `rsp` 指着 `rt_sigframe` 的**开头**
   （那一格是 restorer 地址）；glibc 的 restorer 是被 handler 的 `ret` 弹进去的，运行时
   `rsp` 已经是 `&ucontext`（frame+8）。我们的 shim 走 `call`/`ret`（净 0），第一版少补
   这一条 —— 内核把 frame 开头当 ucontext 读，PC 跳到栈上，第二个信号才死。
   shim 的尾巴因此是：`test eax,eax / je ud2 / add rsp,8 / mov eax,15 / syscall / ud2`。

**不主张**：修复不等于正确 —— 读到的 0 可能让程序算出垃圾；它只保证"不因这一次故障
而死"。所以默认关、必须显式给预算，且每次修复都写一行。

### 4.3.1 站点归属的边界（stage 2.5 顺带修的诊断缺陷）

`--fault-report` 的站点查找原先只判 `addr <= rip`、取最大的那一条。**函数的序言**
（`push rbp` / `sub rsp`）不是访存站点，于是栈溢出那条 `push` 会被算到**前一个函数**
最后一个站点的头上 —— 实测报出 `站点：__loment_memcpy / body / 第 3 条`，而 PC 明明在
`down` 里。修复：每条站点记下它**展开出来的机器码长度**（`lower` 前后之差），查找条件是
`addr <= rip < addr+len`；不在任何站点区间里就回退到"所在函数"那条消息。

条目仍是 24 字节：原先的两个 `u16`（kind/width，报告从未用过）换成一个 `u32` 长度。
两侧（参考与镜像）都在发射后把长度填进表里；判据 F 现在断言归属到 `down`，而不是某个
相邻函数。

### 4.4 stage 4：Windows PE（参考发射器，本机实测）

**一句话：PE 上不是信号，是 SEH 异常。** 同一份坏访存，ELF 侧是 SIGSEGV（11），
PE 侧是异常码 `0xC0000005`；`ud2` 是 `0xC000001D`。stage 1 的那一套
（`rt_sigaction`、备栈、`rt_sigreturn`、`tgkill` 重发）在这里**一个都不能用**。

**机制**（`tools/lomelf.py` 的 `_fault_pe_runtime_ir` + `PeEmitter`）：

1. **安装**：入口桩先 `AddVectoredExceptionHandler(1, handler)`。它是**向量化**处理器，
   由加载器在 SEH 之前直接调用。**实测**：`SetUnhandledExceptionFilter` 在无 CRT 的
   产物上根本不会被调用（回调里塞 `ExitProcess(42)` 探针，拿到的仍是崩溃退出码）——
   这一格是量出来的，不是偏好。
2. **桥**：`__lom_pe_shim` 是机器码，把回调的 `rcx`（`PEXCEPTION_POINTERS`）落到全局，
   再 `call __lom_pe_body`。**必须保存 `rdi` / `rsi`**：Win64 上它们是非易失的，而栈机
   把它们当实参寄存器用 —— 不保存，控制交回 ntdll 之后异常会**原地重来**（实测：报告
   刷屏直到栈耗尽）。这一条与 ELF 的 shim 恰好相反：那边 handler 不返回。
3. **报告**：`__lom_pe_body` 是一段与程序无关的 IR（与 stage 1 同一个思路），读
   `ExceptionRecord`（码 / `ExceptionAddress` / `ExceptionInformation[1]`）与
   `ContextRecord`（RSP `+0x98`、RIP `+0xF8`），用与 ELF 同一套 `__wrhex` / `__wrnum`
   与站点表打出 lomenterr 形状。站点查找直接复用 `_ir_site_lookup` —— 两边的站点行
   是**同一段文本**。
4. **结局**：返回 `EXCEPTION_CONTINUE_SEARCH`（0）。打印完 nobody 接，进程按 Windows
   的方式终止 —— **退出码就是异常码本身**（`0xC0000005` / `0xC000001D`），与不装处理器
   同一个结局。ELF 那边是"重发信号、仍然死于信号"；两边语义不同，本文件不主张同形。

**本机实测**（空指针写夹具，`--target pe --fault-report`）：

```text
loment: 原生档故障（PE 异常）
error[FAULT]: 原生档在执行时收到异常（PE；docs/367 stage 4）
  --> 原生产物:0x0000000140005154
   | 异常：0x00000000c0000005；地址：0x0000000000000000
   | PC：0x0000000140005154；RSP：0x00000000013fff18
   | 站点：_start / entry / 第 1 条（距该指令 0x0000000000000013）
   |
   = 事实：…
   = 建议：…
```

**与 ELF 的差别（都写在这里，不留白）**：

* **先于 SEH**。VEH 在程序自己的 SEH 之前跑：一份**自己接了**这个异常的程序也会先
  看到一行报告，然后照常处理（返回值是 `CONTINUE_SEARCH`，不改变结局）。这一档的产物
  今天不用 SEH，所以实际不会出现；写在这里免得以后把它当成"报告器错了"。
* **没有备栈**。PE 的 SEH 在同一个栈上回调，栈溢出时报告器自己也会崩 —— 所以
  "像是栈溢出"那条判据在 PE 上是**最佳努力**，不是保证（建议那一行已写明）。
* **没有恢复分支**。`--fault-heal` 在 PE 上点名拒：要改 `ContextRecord` 再返回
  `EXCEPTION_CONTINUE_EXECUTION`，那是另一段运行期。
* **单位置**：参考发射器这一半；自举镜像（`loment/tools/lomelf.lomt`）尚未跟进 ——
  与 stage 2 的 ELF 不同，PE 这一侧没有"逐字节孪生"判据，直到镜像也发这一段。
* **体积**：同一份 `user_hello`，默认 23,040 B → 打开后 33,792 B（+10,752 B，约 1.47 倍；
  比 ELF 的 3.27 倍小 —— 没有备栈、没有信号分派链）。默认关，产物逐字节不变。

**判据**（`loment_pe_test`）：默认关时逐字节不变；打开后产物里有 VEH 与报告文案；
Windows 上坏访存 / `ud2` 两格有报告且退出码等于异常码；`--fault-heal` 与 `--link`
在 PE 上是点名拒绝。


### 4.5 接下来要模拟的三台"下一代硬件"（设计，未实装）

站点表 + handler 已经把"故障现场"变成**可查询的结构**；下面三件事只差在这张表上再加
一点状态。按代价从低到高：

1. **最近站点环（LBR / BRBE 的访存版）**。每个站点展开段前插一条
   `mov dword [trace + head & mask], site_idx; inc head`（约 10 字节/站点），环在 `.data`
   （例如 64 项 × 4 字节）。handler 在报告尾部按环序打出最近 16 个站点名与 PC ——
   一次故障因此带着**来路**，不只是落点。代价：文本段约 +10 B/站点、运行期每次访存
   多两次内存操作。开关必须独立（`--fault-trace`），因为它是唯一会拖慢正常执行的机制。
2. **站点热计数（PMU 的软件版）**。同一条插入换成 `inc dword [counter + site_idx*4]`，
   计数表与站点表同序。报告打出"该站点在崩溃前执行了 N 次"；配合环可以回答"是第一次
   就走错，还是第 10⁶ 次才踩线"（GC/分配器那类"跑久了才炸"的故障最需要这个数）。
   代价同上，且计数会溢出（u32 足够）。
3. **确定性重放**。把站点环、`argc/argv` 与环境块的哈希、以及每个**外部输入读**
   （`read`/`recv` 的返回值与字节数）记进一份只追加的日志；故障时打印日志的哈希。
   有日志的产物可以在宿主重放到同一个 PC —— 这是把 LVM 侧的 `LVMC` 快照思路搬到
   原生档（那里有显式状态，这里只有**输入序列**）。代价最大：要拦截所有输入系统调用，
   而且"重放到同一 PC"只对单线程、无熵源的程序成立。

**共同前提**：站点表已经是"地址 → 名字"的唯一索引，三件事都只是往里加一列；但第 1、2
条会改**每条访存指令的展开字节**，因此它们的孪生判据要按"打开开关的产物逐字节相同"
来写（与 `--fault-report` 同一条路），不能只在参考侧实现。

---

## 5. 我不主张

1. **stage 0 与 stage 1 是两条路**：0 在宿主（不经 lomelf 的产物、clang 路也能用），
   stage 1 只有 `--fault-report` 打开的 lomelf 产物才有；默认关（§3）。
2. **不主张原生能给 LVM 那种对象细节**。没有对象表就没有"右越界 28 字节"；
   原生能给的是信号、地址、PC、站点、读写方向，以及栈溢出的判据。
3. **不主张源行**。默认产物没有行表；`--debug` + `--map` 是 stage 0 的一条可选路。
4. **不主张 Windows 与 Linux 同形**（stage 4）：PE 上进程按 Windows 的方式终止
   （退出码是异常码），也没有备栈（§4.4）。
5. **不主张体积免费**。站点表与备栈的代价要量（stage 3 的判据）。

---

## 6. 与其它文档的关系

* `docs/366` —— LVM 档的同一条需求；两档共用"首行稳定 + Lomenterr 形状"的口径。
* `docs/356` —— DSG 的 `__loment_fault(kind, site, witness)` 是 stage 5 的规范；
  本文的 stage 1 是它的信号兜底那一半（§356 §6.2 的 L2）。
* `docs/167` —— 原生后端与两只链接器的逐字节孪生。
* `docs/182` §5.3 —— 两侧的**码**与**形状**一致、文本各写各的；本文沿用。
