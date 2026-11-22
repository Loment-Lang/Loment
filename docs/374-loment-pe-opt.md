# 374 · PE 的 `--opt`：用户代码交给 clang，容器与运行期仍是 lomelf 的

> 2026-11-21 · 上游：`docs/212` §5（把 IR 交给 clang）、`docs/365`（两档的口径）。
> 一句话：Windows 的优化档落在**参考链接器**里（`tools/lomelf.py --target pe --opt`），
> 打包构建的 PE 那一半也走它；**安装包里的 `loment build --opt` 在 Windows 仍点名拒**，
> 原因与下一步见 §4。

---

## 1. 形态

默认档的 PE 是 lomelf 自己的栈机（逐字节可复现）。`--opt` 把**同一份 IR** 交给 clang，
但**容器与运行期不换人**：

1. 参考实现发 IR（与默认档同一份字节）；
2. **内联 `syscall` 改写成普通调用** `@__win_syscall_c`（号与实参原样；`thread_spawn`
   点名拒 —— Windows 没有 `clone`）；
3. clang `--target=x86_64-pc-windows-msvc -O2 -c` 出一个 COFF 目标文件；
4. lomelf 把对象摆进自己的 PE：`.text` / `.data` / `.rdata` / `.bss` 与 REL32 族
   重定位（`tools/lomelf.py` 的 `CoffObject` + `_pe_link_object`）。入口桩、
   `__win_syscall`、导入表全是**原来那套发射器** —— 两条档的 Win32 语义不会分叉。

## 2. 三处 ABI 垫片

* **入口栈**：Windows 的进程入口与 C 调用约定差一层，优化档的入口桩先
  `and rsp,-16` + `sub 32`（影子空间）再 `call _start` —— `docs/212` §5.2 那条
  （ELF 入口的 `stackrealign`）的 PE 版；
* **`__win_syscall_c`**：Win64 参数（rcx/rdx/r8/r9 + 栈上第五~七个）转进 shim 的
  Linux 约定，并且**保住 rbx / rdi / rsi** —— 后两个在 Win64 里是被调方保存的，
  而 shim 把它们当实参寄存器写穿。第一版漏了这一步：连打三个数，第二个之后的输出
  错位（`sum=31` 对，`fib=1107296304`）；判据 `test_pe_opt_matches_the_default_build`
  就是照着这个形状写的；
* **三个 libcall**：`memset` / `memcpy` / `memmove` 由 lomelf 发机器码（Win64 ABI，
  含重叠 memmove 的反向路径），代替 ELF 那条的 `_FREESTANDING_LIBCALLS`。判据
  `test_pe_opt_supplies_the_three_libcalls` 逐字节对结果。

## 3. 实测

同一台机、Windows 原生跑，编译器 PE 自己的 `--emit-llvm`：

| 单元 | 默认档 PE | `--opt` PE |
|---|---:|---:|
| `user_hello`（~40 行） | 46 ms | **33 ms** |
| 360 个函数的合成单元 | 797 ms | **55 ms**（14.5×） |

另在开发机把 **99 个单元**（examples + selfhost + tools）分别用"优化档编译器 PE"与
"默认档编译器 PE"编一遍，**产物与诊断逐字节相同**（自举驱动自己也在里面：优化档编译
自己时用的就是三个机器码 libcall）。

## 4. 今天到哪一步（别读成"安装包也拿到了"）

`--opt` 的 PE 路径落在**参考链接器**里。现在能拿到它的是两个地方：

* **打包构建**：`python tools/loment_dist.py --emit --opt` 的 PE 那一半也走
  `lomelf.compile_pe_opt` —— 发出去的 Windows 工具（driver / lsp / lomelf / cli …）
  本身更快；
* **仓库/开发侧**：`python tools/lomelf.py unit.ll --target pe --opt -o app.exe`。

**安装包里的 `loment build --opt` 在 Windows 仍点名拒**。包内链接器是自举镜像
（`loment/tools/lomelf.lomt`），它的 PE 侧还没有"吃一个 COFF 对象"的模式；而 Windows
上的 Loment 程序不能起 clang 子进程。补齐它是下一档的活，两步：

1. 自举镜像加 `--opt-obj` 模式（解析 COFF、摆节、回填 REL32 族）；
2. 启动器（batch）负责调 clang 出对象 —— 与 POSIX 那条给 ELF 调 clang 的位置相同。

在那之前，点名拒比静默退另一档诚实：优化档承诺的就是"交出去给优化器"。

## 5. 判据

- `loment_pe_test::test_pe_runs_natively_matches_linux_behavior`（既有那条 Windows 专用
  判据）里多两段：**同一份 IR 的两档 rc + stdout 逐字节一致**（递归 / 数组 / 多次打印），
  以及三个 libcall（含重叠 memmove）结果逐字节对。**没有新起判据** —— 跳过表只许变短
  （`ci.py --budget-vs`），而 Windows 之外的机器跑不了 PE，新判据生来就跳过正是要堵的形状。
- `tools/loment_dist.py` 的打包路径：`--opt` 时 PE 那一半也优化；clang 不在或拒收
  时**降级并点名**（与 ELF 那半同一口径）。
