# 155 · Loment → 内核 交接单（给内核线 DSH）

> 生成时间: 2026-09-10 · 作者: Loment 线（zcode）
> 读者: **开发 FujoOS 内核的 agent** —— 假设你没跟过 Loment 线，这份单子让你不用读完全部文档就能接活。
> 上游文档: `docs/145`（100 里程碑）· `docs/149`（内核集成设计）· `docs/146`（能力域语义）· `docs/147`（Potato v1）· `docs/154`（状态矩阵）
> 本文的一句话: **Loment 侧该做的都做完了，有 10 个里程碑卡在"内核侧未接"；这份单子列出内核侧具体要做什么。**

## 1. 你需要的 30 秒背景

**Loment** 是 FujoOS 自研的底层语言（100 里程碑计划见 `docs/145`），分四层：

| 层 | 是什么 | 权威文件 |
|---|---|---|
| L0 接口层 | `.lom` 单源 → 生成 Rust/C/Python/JSON | `lom/*.lom` + `tools/lomc.py` |
| L1 语言 | `tools/lomentc.py`，双后端（转译 Rust / 原生 LLVM IR） | `docs/143` |
| 表示层 Potato | 每个编译单元强制导出形式对象（含能力域） | `tools/potato.py` + `docs/147` |
| 自举 | 用 Loment 重写编译器四段（lexer/parser/checker/codegen） | `loment/selfhost/` + `docs/150` |

**关键事实：Loment 程序已经能在 FujoOS 用户态跑起来**（M67，串口 `M67 RESULT: PASS loment-user`），
链路是 `.lomt → LLVM IR → ELF → fujorun 容器 → QEMU -append fujo.run=<name> → 串口`，
一条命令复现：`python tools/loment_boot.py loment/examples/user_hello.lomt`。

**当前进度**（`docs/154`，从 `docs/145` 自动生成）：完成 **96** / 部分 **2** / 未达 **1** / 未开始 **1**（2026-09-15 复核，本单下方 §2 的 10 项里已有 9 项由内核线落地，见那里的更新块）。
自举四段（`loment/selfhost/*.lomt`）已能在 Loment 版 codegen 下生成与参考实现**逐字节相同**的 IR。

## 2. TL;DR：内核侧要做的 10 项

全部是"Loment 侧就绪、内核侧未接"。括号里是账本编号。

> **2026-09-15 更新（内核线回报 + 语言线复核）**：下表 10 项里 **9 项已落地**
> （A1/A3/A4/A5/B1/B2/C1/C2/D1 → M50/M37/M42/M40/M71/M74/M33/M70/M68），
> `docs/154` 已按此把这 9 条从「部分/待做」改成「✅」，并附了证据来源（串口
> `CAP_ASSERTS n=2 bad=0 guards=2 (A1-A4 ok)` / `INV_RUN … rev=2 nonrev=0 dom=5 probes=3`，
> 与矩阵用例 `m185-domcaps` / `m186-ahci-real` / `m187-loment-abi` / `m188-loment-isr`）。
> **只剩 A2（M39）没完成，但原因不是内核侧缺东西** —— 详见 `docs/145` M39 行：
> 双向 diff 的实质差全是 0，那 2 条 `WIRE` 红是 `tools/loment_caps_diff.py` 的**解析口径过期**
> （W64c 已把域表搬进 arena，由 `replay_init()` 启动时填充，工具还在找静态初值）。
> **修工具，不要排 C 段**；也别在修好前把它当红的门禁接进 `parallel_gate`。

| # | 要做的事 | 判据 |
|---|---|---|
| A1 | 把 `loment/build/cap_asserts.rs` 的 A1–A4 断言表接进内核（`include!` 或按字段读），并让它在启动时自检 | A1–A4 由形式对象驱动，内核侧断言失败要能报出 unit/name（M50） |
| A2 | 用同一套域模型做**双向 diff**：`kernel/src/capability.rs` 的域 ↔ Loment 形式对象的 `caps` | 双向 diff **0 差异**（M39） |
| A3 | 实现"撤销"：被撤销的域再调用要返回拒绝（域表已带 `revocable` 位） | 撤销后调用返回拒绝（M37） |
| A4 | 信任自适应域宽接口：域宽随质量台账变化**可观测** | 域宽变化在内核侧可观测（M42） |
| A5 | A1–A4 断言的 Loment 侧表达（`inv_run` 自检）——**与 A1 同一件事的内核侧闭环** | `inv_run` 自检通过（M40） |
| B1 | 内核模块加载器：装载 Loment 编译出的 ELF（入口 `_start` 零参、基址 0x400000） | Loment ↔ Rust **双向调用**通过（M71） |
| B2 | 调度器钩子：上下文切换时把域 id / 任务 id 以只读方式暴露给 Loment 侧 | 上下文切换计数正确（M74） |
| C1 | 安装 IDT，把 Loment 写的 `interrupt fn` 挂上去（ABI 见 §3） | QEMU 中触发中断并从中断处理返回（M33） |
| C2 | 键盘中断路径用 Loment 处理函数**实际生效**（不只是 IR 形状对） | 键盘中断路径生效（M70） |
| D1 | 给 AHCI 驱动提供**真机寄存器块**（现在是 mock：`6 31 1 1`） | 读扇区成功（M68） |

> 账本里 A1–A5 的原始措辞见 `docs/145` 的 P4/P7 表；上面是"内核侧动作"的直译。
> 这 10 项做完，`docs/154` 里对应的 `部分/未开始` 会变成 `✅`。

## 3. 已经定死、**不要**单方面改的接口

这些是跨线契约，改动必须走 L0 单源（§5），否则两个实现会漂移：

1. **用户程序链接地址 = 0x400000**。默认地址会 `#PF err=0x5`（内核用户页从 0x400000 起）。
   编译命令见 `tools/loment_boot.py`：`-nostdlib -static -fuse-ld=lld -Wl,-e,_start -Wl,-Ttext=0x400000`。
2. **入口 `_start` 零参**，内核按 Linux ABI 交付栈（与 `sdk/linux/m30_linux.c` 同形）。
3. **syscall 编号的唯一真源是 `lom/fuai.lom`**（46 个原语）。不要在内核里另立第二张表 ——
   `tools/loment_syscalls.py --check` 会做逐字节对账，`loment_p7_test` 断言内核 dispatch **46/46** 覆盖。
   `syscall4` 用 rax/rdi/rsi/rdx，`syscall6` 追加 r10/r8。
4. **中断函数 ABI**：Loment 的 `interrupt fn f()` 编译成
   `define x86_intrcc void @f(ptr byval([8 x i8]) %__frame)` —— 帧是 **8 字节 byval 指针**，
   不是内核自定义结构体。内核侧 IDT 的 stub 要按这个形状交付。
5. **域表空间名 = FNV-1a 32 位哈希**（`_fnv1a32(space)`）。内核若要按名字查域，必须用同一算法。
6. **编译期 bump 堆是 65536 字节**（IR 全局 `@__loment_heap` / `@__loment_off`，溢出走 `__loment_abort`）。
   用户态要更大堆请用 M73 的固定块池分配器（`loment/examples/allocator.lomt`），不要指望内核扩堆。
7. **内核不需要提供 libc**：`__loment_memcmp` / `__loment_memset` / `__loment_abort` 由编译器在
   `.ll` 里内联（M31），已用 `llvm-nm` 验证无未定义符号。
   （2026-09-16 收窄：这条只说**纯 Loment 单元**。带 `extern fn` 的单元**必然**有未定义符号 ——
   那正是链接器要去解开的东西，见 `docs/173-loment-ffi.md`。内核线手上的产物全是纯 Loment，
   所以这条对内核线的承诺一字未变。）

## 4. 现成产物清单（可直接消费，不需要你重新生成）

| 路径 | 内容 | 用法 |
|---|---|---|
| `loment/build/cap_asserts.rs` | `CapAssert` 结构 + `CAP_ASSERTS` 静态表（unit/name/space/lo/hi/revocable/guards/a1..a4） | 内核 `include!` 或按字段读；由 `tools/potato_assert.py` 从形式对象生成，**请勿手改** |
| `loment/build/fuai_syscalls.lomt` | 46 个 `pub fn fuai_<name>(a0..a4) -> i64` 包装 | Loment 侧调用面；由 `lom/fuai.lom` 生成 |
| `loment/build/*.potato.json` | 形式对象 v1（`funcs`/`types`/`caps`/`guards`/`imports`…） | 内核校验域/契约的机器可读输入 |
| `loment/build/loment.ld` | 裸机链接脚本（1 MiB 起） | 裸机目标（M30/M32） |
| `lom/build/fuc.py`（由 `lom/fuc.lom` 生成） | `NODE_STRUCT` 等 FUI 布局 | 内核 FUI 解码；M69 已证明与 L1 程序**逐字节一致** |
| IR 全局 `@__loment_caps` | 域描述表：每行 `{ i64 fnv1a(space), i64 lo, i64 hi, i64 revocable }` | **运行时可读的域表**——内核可以直接读它做域检查 |
| IR 全局 `@__loment_audit` | `[16 x i64]` 审计计数数组，`guard` 通过时 `+1` | 运行时可读的审计计数（M38） |
| `tools/loment_boot.py` | `.lomt → ELF → fujorun → QEMU → 串口断言` 一键 | 回归用；内核侧改装载逻辑后请重跑 |
| `tools/loment.py dbg FILE --fn X / --addr 0x…` | Loment 版调试器（符号化） | 排查用户程序问题 |

**只有 `@__loment_caps` / `@__loment_audit` 这两个全局是"运行时接口"**，其余是编译期/工具链产物。

## 5. 分工与纪律（这条最容易出事，请先读）

**Loment 线的开发不在 `D:\Dev\FujoOS`，而在独立工作树 `D:\Dev\FujoOS-FujoLang`**
（clone 出来的，分支 `Fujoos-FujoLang-DEV`，origin = GitHub）。

原因：共享工作树的**当前分支会被别人切走**。实测踩坑：提交时分支已被切到 `FujoDEV-V2`，
于是 `git push origin Fujoos-FujoLang-DEV` 报 "Everything up-to-date"，提交其实没推上去
（要用 `git push origin HEAD:Fujoos-FujoLang-DEV` 才落到正确分支）。所以两条线各自一个工作树。

给内核线的四条纪律：

1. **不要动这些路径**（Loment 线产物）：`loment/`、`lom/`、`tools/loment*.py`、`tools/potato*.py`、
   `tools/lom*.py`、`docs/14?-loment-*.md`、`docs/15?-loment-*.md`。要改接口请走下面的第 2 条。
2. **接口改动一律先落 L0 单源** `lom/*.lom`，然后跑 `python tools/lomc.py … ` 或
   `python tools/loment_syscalls.py --emit loment/build/fuai_syscalls.lomt`；生成物由门禁对账，
   别手改 `kernel/src/fui/fuc_gen.rs`、`loment/build/fuai_syscalls.lomt` 这类文件。
3. **提交只含自己的路径 + 提交后立刻推**（并发线出现过 `git reset origin/<branch>` 丢掉别人提交）。
   只提交自己的 hunk 要**整 hunk 过滤**，并在干净 worktree 里验一遍提交态。
4. **门禁口径**：`python tools/ci.py --static-only`。Loment 侧 7 项应当全绿
   （`lomentc_test` / `potato_test` / `loment_tools_test` / `loment_p7_test` / `loment_p8_test` /
   `loment_p9_test` / `loment_status`）。另外 4 项（`lom_audit` / `potato_cross` /
   `fuai_contract_check` / `lomc_test` 的两条）依赖**另一个私有库 `LinuxFUAI/`**——
   它不在仓库里，缺它时这 4 项红是**预期行为，不是回归**。

## 6. 判据对照表（做完怎么证明）

| 账本 | 内核侧做完的标志 | 复现命令 |
|---|---|---|
| M50 / M40 | `CAP_ASSERTS` 接进内核且启动自检通过；`inv_run` 自检通过 | `python tools/potato_assert.py --check` + 内核串口输出 |
| M39 | 内核域模型 ↔ 形式对象 `caps` 双向 diff 0 差异 | 需要一个 diff 脚本/测试；Loment 侧已有 `caps` 字段与 A1–A4 |
| M37 | 撤销后调用返回拒绝 | 用户态程序 + 串口断言 |
| M42 | 域宽随质量台账变化可观测 | 同上 |
| M71 | Loment ↔ Rust 双向调用通过 | `python tools/loment_boot.py loment/examples/native_entry.lomt` |
| M74 | 上下文切换计数正确 | 内核计数器 + 串口 |
| M33 / M70 | QEMU 中中断触发并返回；键盘路径生效 | `loment_p7_test` 现有 8 项 + 新增中断用例 |
| M68 | 真机寄存器块下读扇区成功 | 替换 mock 块后跑 AHCI 用例 |

## 7. 需要 Loment 线配合时怎么办

不要在内核里绕过去。把需求写成三句话发到 Loment 线（或直接落 `lom/*.lom`）：

1. **要什么原语/接口**（例如"要一个 `fuai_map_page(addr, len, prot)`"）；
2. **为什么内核侧绕不过去**（一句话）；
3. **判据**（怎么验证它生效）。

Loment 线收到后会：改 `lom/*.lom` 单源 → 重新生成包装与 spec → 跑 `lom_audit` 逐字节对账 →
更新 `docs/149`（内核集成）与 `docs/154`（状态矩阵）。

## 8. 编辑器集成（VS Code 扩展，M56）

Loment 现在有**编辑器宿主**了：`editors/vscode/`（VS Code 扩展，已在本机装为 `hooyan2022.loment@0.2.0`）。

| 能力 | 谁提供 |
|---|---|
| 语法高亮（`.lomt` / `.lom`） | `syntaxes/loment.tmLanguage.json` + `syntaxes/lom.tmLanguage.json` |
| 语法高亮（七门外门表层语法） | `syntaxes/loment-<规范名>.tmLanguage.json`，借宿主的 `source.c` / `source.python` / …（`docs/235`） |
| 补全 / 跳转定义 / 诊断 / 格式化 | `tools/loment_lsp.py`（LSP over stdio）；**格式化在服务端做**，扩展不自己起进程 |
| 生成 LLVM IR / 静态检查 / 跑 `test_*` / 在 FujoOS 里跑 | VS Code 任务 → `lomentc.py` / `loment.py` / `loment_boot.py`（argv 数组，不经 shell） |

重建与验收（在仓库根）：

```
cd editors/vscode && npm install --omit=dev     # 装 vscode-languageclient（离线打包会打进 VSIX）
python tools/vscode_ext.py --check              # 清单/语法结构校验
python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
python tools/vscode_ext.py --install            # 重打包 + 侧载（本机 `code --install-extension` 起不来）
code --install-extension loment/build/loment-vscode.vsix --force
python tools/vscode_ext_test.py                 # 无头验收 16/16（已进门禁 vscode_ext_test）
```

要点（对内核线也有用）：

- 扩展**不需要预装 npm 包**（`vscode-languageclient` 打进 VSIX）；`.vsix` 是**确定性字节**
  （固定时间戳），一条命令可重建，因此它不进校验和清单，清单只收源码。
- 语言服务是**纯 Python**、只吃文档文本：只要工作区里有 `tools/loment_lsp.py` 就能跑；
  扩展从工作区逐级向上查找，也可用设置 `loment.serverPath` 指定。
- **安全纪律**：扩展源码里没有 `child_process`（spawn 在 `vscode-languageclient` 内部），
  可执行位置只写字面量 `python`，一切路径走 argv 数组 —— 配置字符串不参与命令构造。
  **"工作区能覆盖"这件事本身也声明了**（#166）：`capabilities.untrustedWorkspaces` =
  `{supported:"limited", restrictedConfigurations:[…]}`，把"命令 / 参数 / 会被执行的脚本路径"
  那八个设置挡住 —— 未受信工作区里 `.vscode/settings.json` 不能再借着它们指向别的程序。
  落点两处：`vscode_ext_test::test_vscode_hardens_untrusted_workspaces` 与
  `vscode_ext.check()`（后者保证**每次打包都会看**）。
- 无头验收覆盖：清单/语法结构 + **每个语法正则都能编译**（防止语法文件静默失效）+
  `server-path.js` 真能找到服务 + 对真实服务做完整 LSP 往返（initialize → didOpen 诊断 →
  补全 → 跳转 → 格式化 → shutdown）+ 错误路径（未声明变量必须成为诊断）+ VSIX 结构 +
  **表层语法声明检测与参考实现逐条对拍**（全仓 769 份源，枚举而非手挑）+
  **真的开文档看语言 id 换没换**（那一路是事件驱动的，纯模块判据覆盖不到）。
- 「在编辑器里人工点验三个能力」**还没做**（需要打开一个 `.lomt` 看一眼），所以 `docs/154`
  里 M56 仍记为「部分」。

## 9. 诚实边界（别把话说满）

- 上面 10 项是**内核侧的具体动作**，但内核怎么实现（IDT 怎么建、调度器钩子挂哪、加载器放哪个目录）
  是**内核线的设计自由**，这份单子只钉"跨线 ABI 形状"与"判据"。
- `docs/154` 的 100 项口径里，还有 3 项需要外部条件（M48 的 LLM 臂要模型凭据、M92 aarch64 要模拟器、
  M100 要外部审计），（2026-09-15 修正：原写的「3 项天生只能部分」已不适用 —— M30 链接流程与 M59 DWARF 现记为完成，只剩 M56 LSP 无编辑器宿主还在「部分」，另有 M92 aarch64 无模拟器。）
  这些**不在这份交接单范围内**。
- 自举线（M83–M86）与内核接入正交，不阻塞上面 10 项：**M83/M84 已达成**（自编译出可运行二进制；
  三阶段产物逐字节全等 637115B）。M85/M86 还缺“后端覆盖全语言”（泛型单态化 / trait / `match`+枚举 / 能力域）。
