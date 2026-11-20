# 167 · Loment 原生 ELF 后端 —— 吃掉 clang 的活（第一格）

> 状态：**进行中**（2026-09-14 起）· 判据 `tools/loment_elf_test.py`（7/7）+ `tools/loment_genesis_test.py`（2/2）+ `tools/loment_pe_test.py`（9/9），审计主张 **C19 / C20 / C21 / C24**
> 上游：`docs/144` §3 写明"写机器码后端是自举之后的事"——自举已完成，这就是那件事。
> 前情：`docs/159` §4 曾把 clang 列为"地基语言，本次目标明确保留 / 不计划去掉"。
> **本文是对那一条方向的后置更新**：0.1.4 Alpha2 的目标就是把它去掉。

## 1. 为什么卡在 clang 上

clang 在本仓库只出现在两处，但两处都是必经之路：

| 位置 | 作用 |
|---|---|
| `loment/bootstrap.sh:71-74` | 把种子 `.ll` 变成 stage1 —— 链条的第一步 |
| `tools/loment_dist.py:582-589` | 把**每一个**发射出来的 `.ll` 变成 ELF |

运行期的启动器 `loment_dist.py:122-148` 更直白：`loment build` / `loment run` 找不到 clang 就
`exit 3`（"build/run need the foundation compiler"）。根因是仓库里**没有自己的汇编器/链接器**：
编译器只发 IR，IR → 可执行文件这一段一直外包给 LLVM。

WSL 是这条依赖的连带后果：产物是 Linux ELF，Windows 侧只能把整套工具链装进 WSL，
Windows 只留一个 `loment.cmd` 转发（`loment_dist.py:369-374`）。所以"去 clang"和"去 WSL"
是同一根绳子上的两段。

## 2. 这一步做了什么

新增 `tools/lomelf.py`（参考实现，含 PE 目标后约 2000 行）：**把 LLVM IR 子集直接编成 x86-64 静态 ELF**。

```bash
python tools/lomelf.py in.ll -o out
python tools/lomelf.py in.ll --check     # 只解析与降级，不落盘
```

三条设计取舍（都是为了让这件事**有界**）：

1. **吃文本 IR，不进编译器内部**。lomelf 读的是 `.ll` 文本，不是 AST。好处有三：不碰冻结面
   里的 `codegen.lomt`/`driver.lomt`；任何产出同一子集 IR 的东西都能喂进来；判据天然干净 ——
   *同一份 IR* 喂 clang 与 lomelf，比产物的行为。
2. **栈机，不做寄存器分配**。每个 SSA 值一个栈槽，表达式经 `rax`/`rcx` 求值。要的是正确，不是快。
3. **调用约定是我们自己的**。实参一律走栈（每标量 8 字节，聚合按 8 字节向上取整），标量返回值
   放 `rax`。**不是 System V** —— 见 §4。

支撑它的既有事实：Loment 发射的 IR 是**有界整数子集**（无浮点、无 `bitcast`/`select`/`cmpxchg`、
无间接调用/闭包、无 `llvm.memcpy` intrinsics——自带运行时 `__loment_memcpy/memset/memcmp/abort`
是 IR 里写的）。指令面就这些：`alloca/load/store/getelementptr/extractvalue/insertvalue`、
整数算术与位运算、`sdiv/udiv/srem/urem`（带除零陷阱块）、`icmp` 全谓词、`trunc/zext/sext`、
`ptrtoint/inttoptr`、`br/switch/phi`、直接 `call`、内联汇编 `syscall`、`atomicrmw add`、`ret/unreachable`。

## 3. 判据

```bash
python tools/loment_elf_test.py     # 7/7
```

| 用例 | 判据 | 结果 |
|---|---|---|
| `test_lomelf_matches_clang_behavior` | 4 个可终止的 `_start` 程序，原生产物与 clang 产物的 **stdout 字节 + 退出码**一致 | 4/4 一致 |
| `test_lomelf_reports_unsupported_instead_of_miscompiling` | 聚合返回值 / 间接调用 / 没有 `_start` 三类输入**必须报错**，不许静默编出错的 ELF | 3/3 报错 |
| `test_lomelf_cli_check_and_usage` | `--check` 不落盘且 rc=0；无参数 rc=2 | ✅ |
| `test_lomelf_compiles_selfhost_ir_without_clang` | **主线**：种子 →(clang 一次)→ stage1 → 发 IR → lomelf 编成 ELF → 跑出 `M67 RESULT: PASS loment-user` | ✅ |
| `test_lomelf_selfhost_matches_reference` | **自举镜像**：`loment/tools/lomelf.lomt`（走种子自举链）对四个语料产出的 ELF 与参考**逐字节相同** | 4/4 |
| `test_lomelf_rebuilds_the_compiler_without_clang` | **重建不需要 clang（参考侧）**：`lomelf(种子)` → 一个能用的编译器 → 它自编译 `driver.lomt` 的产物**== 种子**（1 630 436 B） | ✅ |
| `test_lomelf_selfhost_rebuilds_the_compiler` | **重建不需要 clang（自举侧）**：种子 → stage1 → 编出**镜像** → 镜像把种子编成编译器（与参考逐字节相同）→ 它自编译产物 **== 种子** | ✅ |

语料（只放**会终止**的 `_start` 程序）：

```
user_hello   M67 RESULT: PASS loment-user                        rc=0
bootprobe    M76 sysname=Linux / M76 RESULT: PASS bootprobe      rc=0
selfcheck    M77 RESULT: FAIL getrandom                          rc=1   ← 两边一致 (沙箱无 getrandom)
all_loment   M78 sum=42 double=84 max=84 fib=55                  rc=0   ← 含泛型/多模块/字符串
```

`loment/examples/native_entry.lomt` **不进**这个判据：它是 `while true` 的裸机入口（M32 验证集），
按设计不终止。它属于"能不能编出来"，不属于"跑出来一样"。

主线那条用例是这一步真正的意思：**编译一个 Loment 程序不再需要 clang**。clang 只在"从种子重建
stage1"那一步出现（见 §5）。

## 4. 与 clang 路径的差异（逐条记账，别当成"等价"）

| # | 差异 | 影响 |
|---|---|---|
| 1 | **调用约定是自己定的**（实参走栈，非 System V） | 原生产物 v0 **不给 C 调**；仓库里的 C 夹具（`native_driver.c` 那类）仍走 clang 路径 |
| 2 | ~~聚合返回值不支持~~ **已支持**（隐藏结果指针：调用方在实参之外多压一个缓冲地址，被调方写进去、再用 `rax` 回传） | 2026-09-14 补；这是「能编译编译器自己」的前置 |
| 3 | 间接调用 / 函数指针 / 闭包 不支持 | 同上（语言本身目前也没有） |
| 4 | 浮点不支持 | 语言本身没有（发射面 0 处 `fadd`/`fcmp`） |
| 5 | 变参、结构体动态下标 GEP 不支持 | 同上 |
| 6 | 不做寄存器分配 —— 产物比 `clang -O1` 大且慢 | v0 要的是正确性；性能是后面的事 |
| 7 | 不写 DWARF | `--debug` 的元数据（`!DILocalVariable`、`#dbg_declare` 记录、行尾的 `, !dbg !N` 附着）**被忽略**；原生产物没有行表。**这句话 2026-10-13 之前只是声明，不是事实**：`lomelf` 把 `#dbg_declare` 当成不支持的指令**拒**了，于是 `--debug` 的 IR 链不出来 —— 引号里的"被忽略"是与实现相反的那一半。修法见 `docs/210` §7：两边各跳过行首的 `#dbg_*`、去掉行尾的附着 |

**不覆盖**的东西统一是"报错退出"，不是"编出个错的"——第二条用例专门钉这个。

## 5. 还没做的（这一步只吃了一格）

1. ~~自举侧的镜像尚未落地~~ **已落地**。`loment/tools/lomelf.lomt` 走种子自举链编成二进制后，
   对四个语料程序（user_hello / bootprobe / selfcheck / all_loment）产出的 ELF 与参考实现
   **逐字节相同**（8240 / 8296 / 12392 B…），且产物跑出来的输出与 clang 路一致
   （判据 `loment_elf_test` 的 `test_lomelf_selfhost_matches_reference`，审计主张 **C20**）。
   也就是说：**编译一个 Loment 程序从发 IR 到出 ELF，全程不需要 clang** ——
   clang 只剩"种子 → stage1"这一步（见第 4 条）。
2. **PE64 / Windows 原生（去 WSL）** —— **第一步已落**（2026-09-14）。`tools/lomelf.py` 新增
   `--target pe`：同一份 IR 产出静态 PE32+ 控制台程序，**在 Windows 上原生跑，不经 WSL**
   （主张 **C24**，判据 `tools/loment_pe_test.py` 16/16）。

   与 ELF 目标只差 **syscall 面**：x64 Windows 没有 `syscall` 指令，所以 `_call_asm` 把 `0F 05`
   换成 `call __win_syscall`，由 shim 按 syscall 号（仍在 `rax`、参数仍在 `rdi/rsi/rdx`）派发到
   kernel32。**整个 syscall 面收敛在这一处**，取参代码一个字没改。

   判据分两层：平台中性的两个语料（`user_hello` / `all_loment`）的 PE 产物与 clang/Linux 路
   **stdout 字节 + 退出码一致**；以及把 `PATH` 收成**只剩 python**（没有 clang、没有 wsl）也照样
   编得出、跑得起来 —— 后者才是"去 WSL"这条判据本身。

   `bootprobe`(M76) 与 `selfcheck`(M77) **不进** PE 判据：一个是 uname 的 sysname，一个是
   seccomp 沙箱里 getrandom 会失败的断言，在 Windows 上**本就该不同** —— 拿它们当判据只会
   把平台差异误判成回归。

   **syscall 面（2026-09-14 补齐）**：`read`(0) / `write`(1) / `close`(3) /
   `brk`(12) / `exit`(60) / `getdents64`(217) / `openat`(257) / `newfstatat`(262) —— 那是
   仓库里 Loment 工具用到的全部，但**只是文件与进程那一半**：**联网一个号都没有**。
   **2026-10-10 补上联网那 13 个**（`socket`/`connect`/`accept`/`accept4`/`sendto`/`recvfrom`/
   `shutdown`/`bind`/`listen`/`getsockname`/`getpeername`/`setsockopt`/`getsockopt`，
   由 `ws2_32.dll` 顶着）⇒ 派发面 **21 个号**、导入表成 **两条描述符**。
   **2026-10-10 加 `poll`(7)**（`docs/221`）、**2026-10-11 加 `select`(23)**（`docs/231`）、
   **2026-10-12 加 `sendmsg`/`recvmsg`/`socketpair`/`epoll` 那一族**（`docs/234`）
   ⇒ 派发面 **29 个号**、联网面**再无「点名不做」**。号表、五条翻译决定
   （family / `SO_*` / timeval↔毫秒 / errno / `MSG_NOSIGNAL`）与**点名不做**的那几件
   见 **`docs/217`**（那一族**已经清零**；2026-10-12 起契约里连 `NotDispatched` 这个枚举都删了 —— `docs/234` §2）。Windows 没有 procfs，所以 **`/proc/self/cmdline` 由 shim
   合成**（`GetCommandLineA` 的空格分隔转成 NUL 分隔），argv 因此照常可用；`brk` 用
   `VirtualAlloc` 一次划一块堆来仿真；`getdents64` 一次发一条 `linux_dirent64`（消费方本来
   就是读到 0 为止的循环）；`newfstatat` 只填消费方会读的 `st_mode`（`load32(stb,24)&S_IFMT`）。
   于是判据 `tools/loment_pe_test.py` **16/16**：

   * `test_pe_runs_the_loment_toolchain_natively` —— **`lomstatus` 与 `lomrel` 本身**编成 PE 在
     本机原生跑（argv + 目录遍历 + 文件 I/O），stdout 字节 + 退出码与 Python 版逐字节相同；
   * `test_pe_runs_the_selfhost_compiler_natively` —— **自举种子的 PE 版原生当编译器用**：
     对语料产出的 IR 与参考实现逐字节相同，那份 IR 再经 `lomelf --target pe` 出的产物行为也对；
   * `test_pe_selfhost_mirror_matches_reference` —— **自举镜像产出的 PE 与参考逐字节相同**；
   * `test_pe_links_capability_programs` —— **能力域程序能过原生后端**（2026-09-15 补）。
     `capability` 会发一张 `[N x { i64, i64, i64, i64 }]` 的域描述表，而 `_parse_init`
     原先只认标量 / 字节串 / 数组，**结构体全局初值直接 `v0 不支持的全局初值`** ——
     于是**任何带 `capability` 的程序在链接期就死**，连仓库自己的
     `loment/examples/native_cap.lomt` 都编不出来，而这个洞一直没被语料扫到
     （PE 判据的语料是 `user_hello` / `all_loment`，两个都不带能力域）。
     这一条同时钉三件事：域内 guard 放行、**非字面量越界运行期 trap**、
     字面量越界在检查期就拒（与 docs/146 §3 的静态规则对齐）。

   **自举侧的镜像也能出 PE 了**（2026-09-14）：`loment/tools/lomelf.lomt` 加 `--target pe`
   对应的那条路（按输出名 `.exe` 选目标），产出的 PE 与参考实现**逐字节相同**
   （判据 `test_pe_selfhost_mirror_matches_reference`）。这是去 WSL 的最后一道坎 ——
   Windows 包里那个"把 `.ll` 变成 `.exe`"的链接器可以是自举产物，不必是 Python，也不必是 clang。

   镜像这边比参考实现多两条约束，都是它的地址模型带来的：
   * 表里存的是 **RVA 而不是绝对地址** —— `0x140000000` 装不进 u32，所以只在真要落 8 字节
     绝对地址的地方（`em_mov_abs` 的绝对回填）才把基址加回去；相对跳转与 RVA 差值等价，不受影响。
   * shim 机器码与导入表以 **hex 分片**形式内嵌在 `loment/tools/win_shim_data.lomt` 里
     （`tools/lomelf.py --dump-win-shim` 生成），**故意不做 `str_concat`**：运行时的 bump 堆
     只有 64 KiB（`lomentc.py` 的 `alloc_ir`，超了直接 `abort` = `ud2` = SIGILL），拼一个
     8 KB 的 hex 串就会撞顶 —— 这个坑正是先在镜像里炸出来的。

   也就是说这台机器上 **`.lomt → 编译器 → .ll → 可执行文件 → 跑` 整条链已经不需要 clang，
   也不需要 WSL，连"出 PE 的那把链接器"都可以是自举产物**。写 shim 时又踩到两个"看起来完全无关"的坑，都记在这里：
   （d）shim 最初把 argv 源指针放在 `rax` 上又用 `al` 装字节 —— `movb (%rax),%al` 会**把指针
   自己的低字节写掉**，指针每走一步就跳飞，症状是 argv 只剩一个字符；
   （e）PE 的默认栈（1 MB）对**编译器自己**太小 —— 递归下降直接把栈打爆成 SIGSEGV，
   换成 16 MB 保留才过。小工具照不出来，只有喂编译器本体才暴露。

   **发行包也切过来了**（2026-09-14，docs/162 §0bis）：`loment_dist.py` 不再调 clang，
   改用本机原生后端链 IR（同一份 IR 各链一遍 → Linux 包放 ELF、Windows 包放 PE）；
   stage1 按本机构建格式出，构建过程**在本机直接跑**，不再经 WSL；包里多了
   **`loment-lomelf`**（`loment build/run` 的链接器），Windows 侧由包内的 `bin\loment.cmd`
   直接调那些 `.exe`。判据 `loment_dist_test` **35/35**，其中两条是这次新增的：
   * 装完后 `loment run` 在本机编出 PE 并跑出 `PASS loment-user`（无 WSL、无 clang）；
   * `loment.cmd` 里**不再出现 `wsl`**。

   **还没做**：`tools/loment.py` 的 `ir`/`bench`/`cov`/`dbg` 四处还在调 clang。

   **PE 的四个节钉在固定 RVA**（`.text` 0x1000 / `.idata` 0x1000000 / `.data` 0x2000000 /
   状态挂在 `.data` 的零填充尾巴上 0x3000000）。这样 shim 里对 IAT 与静态状态的取址全是
   编译期常量，`--dump-win-shim` 冻出来的 blob（今天 **15208 字节**；2026-10-10 联网那批是
   6296，之前 2312，见 `docs/217` §4 与 `docs/234`）**一个待回填的地址都没有** ——
   自举镜像照抄即可。又撞到两条加载器脾气，只有实测才看得见：
   （f）**节间不能留空洞** —— `.text` 与 `.idata` 之间只要空 0x1000，加载器就报"不是有效的
   Win32 应用程序"。解法是每节的 VirtualSize 一直铺到下一节起点；
   （g）`out[dll_rva - d:] = dll` 是**开放切片赋值**，会把缓冲区从那里截断 —— 以前缓冲区正好
   那么长所以无害，缓冲区一撑大它就把后面的数据整段吃掉（症状：shim 里的路径字面量没了）。

   写 PE 写出时撞到三个 bug，都只在"加载器认不认"这一层暴露，记在这里：
   （a）**PE32+ 的 `SizeOfImage`/`SizeOfHeaders` 在偏移 56/60**；写成 PE32 的 54/58 会把值落进
   `Win32VersionValue` 槽，加载器读到 `SizeOfHeaders=0` 直接拒收（"不是有效的 Win32 应用程序"）；
   （b）**导入描述符表必须以一条全零描述符终止** —— 少了它，加载器把紧随其后的 ILT 当成第二条
   描述符，导入解析中途失败、IAT 保持未填，随后 `call rax` 直接崩；
   （c）**调用点必须 `sub rsp,0x28`**（32 字节 shadow space + 对齐）—— x64 ABI 要求；少了它崩在
   callee 里，症状是 SIGSEGV 而不是"格式错"。
   还有一条不是格式而是布局：`.text` 的 RVA 不能钉死（早先 `.text` 钉 0x1000、`.data` 钉 0x2000，
   文本一过一页两节 RVA 就重叠），所以改成**两遍发射** —— 先量代码长度，再把 `.data` 排到 `.text` 之后。
3. **构建/发布路径去 Python**（**第一格已落**：`loment/tools/lomstatus.lomt` 取代
   `loment_status.py`，判据 `loment_status_test` 4/4 + 主张 **C22** —— 三路输出与落盘字节
   都与 Python 版逐字节相同。**下一格**：`lom_spec_emit` / `loment_release` /
   `loment_src` / `loment_dist` / `loment_seed` / `loment_manual` / `lom_audit` / `loment` CLI）。
4. **重建不再需要 clang —— 参考侧已证**：`lomelf(种子) → 编译器 → 自编译产物 == 种子`
   （判据 `test_lomelf_rebuilds_the_compiler_without_clang`，1 630 436 B 逐字节相同）。
   也就是说今天就能**不用 clang** 造出一个能用的 Loment 编译器。
   **还差一步才算真的落地**：
   ① ~~做这件事的工具得是自举侧的~~ **已成立**。镜像 `lomelf.lomt` 这一轮补齐到能**编出编译器**
   并成立定点：种子 →(clang 一次)→ stage1 → 编出镜像 → **镜像把种子编成一个编译器**（980 016 B，
   与参考实现逐字节相同）→ 那个编译器自编译 `driver.lomt` 的产物 **== 种子**（1 630 436 B）。
   判据 `test_lomelf_selfhost_rebuilds_the_compiler`。也就是说：**重建这套工具链不需要 C 编译器，
   也不需要解释器**，clang 只剩第一步（种子 → stage1）。
   路上修掉的三个真 bug 都记在这里，因为它们都只在**大单元**上才暴露、语料照不出来：
   （a）槽表按 2048 定，而种子里 `expr_atom` 一个函数就要 3823 个槽 —— 溢出**写进了 alloca 表**，
   表现是"某个 alloca 查不到"（现在 32768）；
   （b）字面量按 u32 解析，`mul i64 %x, 4294967296` 被截成 0（现在走 64 位解析）；
   （c）表按语料规模定太小（全局 256 → 2048；标签/回填 4096 → 16384）。
   另外把"指针值查不到"从**静默发错代码**改成**报错退出** —— 这类静默错编正是最难查的。
   ② ~~链条要有第一个可执行文件~~ **已落地**。`loment/build/genesis/lomelf-linux-x64.elf`（72 136 B，
   由种子构建出来、**可复现**、提交进仓库、有自己的 `SHA256SUMS`）就是那个起点；`bootstrap.sh`
   改成**优先用它**（没有才退回 clang，`LOMENT_USE_CLANG=1` 可强制对照）。
   于是 `sh loment/bootstrap.sh` 在**PATH 里没有 clang** 的环境下四条证明全过 —— 这就是
   "重建工具链不需要 C 编译器，也不需要解释器"的落地形态（判据 `loment_genesis_test`，主张 **C21**）。
   它由 `tools/loment_genesis.py --emit/--check` 维护；`loment/build/genesis/*` 进发布清单。
   **残留的诚实点**：genesis 消不掉 —— 它可复现、有来源、有哈希，但它是个二进制，不装作没有。

   **2026-09-15 实测：它会静默漂移。** 这天改了 `codegen.lomt`（match 默认目标标签名，
   见下第 3 条前面的来历），种子随之更新；重新 `--emit` 出来的 genesis 是 **90 504 B**
   （哈希 `926c4ebb…`），而仓库里提交的那个是 **72 136 B**（`cdb70351…`）——
   也就是说它**早就落后于种子了**，只是没人发现：`loment_genesis.py --check`
   只核对"文件与它自己的 SHA256SUMS 一致"，**没有任何东西把 genesis 和种子绑在一起**。
   后果是温和的（bootstrap 每一级都会重新编译，起点是个旧汇编器也能收敛），
   但"可复现"这句话当时只对**同一份种子**成立，对"种子变了"是不设防的。
   要根治得让 `--check` 也核对**种子哈希**（把它写进 genesis 目录里的元数据）。
   在这次修正之前，`--check` 全绿**不代表 genesis 是最新的**。

3. **镜像 lomelf 不支持聚合按值 —— 现在这是发行包的主卡点**（2026-09-15 实测）。
   镜像自己的注释写着"聚合返回值 / 间接调用 / 浮点不支持（报错退出，不静默错编）"
   （`loment/tools/lomelf.lomt:15`），而 `tools/lomelf.py` 那边聚合返回值**已经支持**了
   （本节第 2 条的对照表：隐藏结果指针）。**两者已经不同步。**

   为什么这从"已知边界"升级成"卡点"：`f193c61` 把发行包的链接步骤从 clang 换成了
   **包内的 `loment-lomelf`**（就是镜像）。于是包里 `loment build` / `loment run`
   **编不出任何按值传 struct / enum 的程序** —— 而 struct/enum 是语言核心特性，
   `lomelf skill` 还专门教它们。参考实现（`tools/lomentc.py`）与 Python 后端都没问题，
   只有**包这条路**断。

   两个最小复现（都用参考实现发 IR，再喂包里的 `loment-lomelf.exe`）：

   | 复现 | 现象 |
   |---|---|
   | `struct E { a: u32, b: u32 }` + `fn tot(e: E) -> u32` + 调用 | 干净报错，但消息是 `loment: 指针值查不到: % @fn=_start nalloc=1 nslot=23` —— 不像给用户看的 |
   | `enum K { A, B(u32) }` + `match` | **SIGSEGV（139）** —— 直接崩，不是报错 |

   第二条**违反了镜像自己那条"不静默错编、要报错退出"的约定**（§5 上面记过：`指针值查不到`
   正是当初为此从静默改成报错的）。所以这里有两件事：**(a) 给镜像补聚合支持**（对齐 Python 侧），
   **(b) 至少让不支持的分支干净报错、不崩**。

   判据缺口也说清楚：`loment_pe_test` 的 `test_pe_runs_the_loment_toolchain_natively` 用的是
   `lomstatus`/`lomrel`，那两个程序**恰好不按值传聚合**，所以 9/9 全绿也照不到这个洞 ——
   与上面"大单元才暴露"是同一类盲区（语料挑得不够刁）。

## 6. 落地形态

- 门禁登记：`tools/ci.py` 的 `STATIC_CHECKS` 含 `loment_elf_test` 与 `loment_pe_test`；审计主张
  **C19**（ELF）与 **C24**（PE）。
- 工件清单：`tools/lomelf.py`、`tools/loment_elf_test.py`、`tools/loment_pe_test.py` 已进
  `loment_release.py` 的 `GLOBS`（清单 184 个工件，`--check` 184/184 —— genesis 也在里面）。
- **不改** L0（`lom/*.lom`）、不改 `codegen.lomt`/`driver.lomt`、不改两后端既有的逐字节等价
  （docs/158 §2）—— 新增的是**第三个后端**，不是在既有后端上动刀。

## 7. 证据

```
python tools/loment_elf_test.py
  PASS  test_lomelf_matches_clang_behavior            # 4 个程序: stdout 字节 + 退出码一致
  PASS  test_lomelf_reports_unsupported_instead_of_miscompiling   # 3 类输入报错
  PASS  test_lomelf_cli_check_and_usage               # --check 不落盘 rc=0 · 无参数 rc=2
  PASS  test_lomelf_compiles_selfhost_ir_without_clang # 种子->stage1->IR->原生产物->M67 PASS
  PASS  test_lomelf_selfhost_matches_reference        # 4 个程序: 自举镜像与参考逐字节相同

python tools/loment_audit.py --json
  [PASS] C19 原生 ELF 后端: ... loment_elf_test: 7/7 通过
  [PASS] C20 自举侧镜像: ... 逐字节相同 + 自举侧重建定点
```

PE 目标（`--target pe`，Windows 原生）：

```
python tools/loment_pe_test.py
  PASS  test_pe_image_is_structurally_sane                 # 对齐/不重叠/入口可执行/文件盖得住
  PASS  test_pe_runs_natively_matches_linux_behavior        # 2 个程序: 与 clang/Linux 逐字节一致
  PASS  test_pe_builds_and_runs_without_clang_or_wsl        # PATH 里只剩 python 也照跑
  PASS  test_pe_runs_the_loment_toolchain_natively          # lomstatus/lomrel 原生跑, 与 Python 逐字节同
  PASS  test_pe_runs_the_selfhost_compiler_natively          # 种子的 PE 版原生当编译器, IR 逐字节同 + 全链可跑
  PASS  test_pe_selfhost_mirror_matches_reference             # 镜像产的 PE 与参考逐字节相同
  PASS  test_pe_reports_unsupported_instead_of_miscompiling # 3 类输入报错
  PASS  test_pe_cli_check_and_usage                         # --check 不落盘 rc=0 · 非法 --target rc=2

user_hello 的 IR 1581 B -> PE 2560 B（text 896 B / data 32 B）；产物原生跑出
M67 RESULT: PASS loment-user，rc=0，与 clang/Linux 路逐字节一致。
all_loment 的 PE 产物同样一致（M78 sum=42 double=84 max=84 fib=55）。
```

规模：`tools/lomelf.py` **约 2000 行**（参考，含 PE 目标）；`loment/tools/lomelf.lomt` **约 2700 行**
（自举镜像）；`tools/loment_elf_test.py` 约 300 行；`tools/loment_pe_test.py` 约 340 行。

**自举链的容量闸门**（写镜像时撞到的，逐条记）：自举 codegen 的**形参上限是 10**
（`loment/selfhost/codegen.lomt` 的表按 10 槽定）—— 超过会让 stage1 直接 SIGILL，
现象是"编译这个文件时编译器自己崩了"，而参考实现编同一份文件完全正常。镜像里
`do_div`/`do_shift` 因此把 `oplen/signed/is_rem` 打包成一个参数。
产物示例：`user_hello` 的 IR 1581 B → 原生 ELF 8224 B（text 714 B / data 32 B / bss 0）。
自举镜像产物与参考逐字节相同：user_hello 8224 B · bootprobe 8240 B · selfcheck 8296 B · all_loment 12392 B。
