#!/usr/bin/env python3
# loment_release.py — 发布清单与可复现包 (M95/M99/M100, docs/152)
#
# 判据: 发布清单覆盖全部 Loment 工件, 每条带 sha256; --check 可被第三方机器复现。
#   python tools/loment_release.py --emit
#   python tools/loment_release.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "build" / "release-manifest.json"
# 版本名的**唯一真源** (2026-09-12 由 `loment-1.0-pre` 改名): 机器可读的标识符用连字符形式,
# 人读的显示名是 `0.1.4 Alpha`; 对外 tag = `v0.1.4-alpha` (git ref 不许带空格)。
# 2026-09-13 由 0.1.3.4-alpha 升到 0.1.4-alpha: 去 Python 收口 (lompkg + L0 生成器 lomc)。
# 2026-09-15 升到 **0.1.4-alpha2**: 三条腿全部落地 —— 去 WSL + 去 clang(产品/构建路径)
# + 包内链接器换成自举镜像; 发行包首次自带 agent skill。tag 会另打 `v0.1.4-alpha2`。
# 2026-09-15 再升到 **0.1.4-alpha2.2**: 库系统 (docs/168) —— 依赖从源码的 use 推导、
# 实例身份=递归哈希、同名多实例真能共存、能力需求沿闭包推导、.lomp 清单 (Loment 自己);
# 并补上 agent 指南与 lomlib 的 Loment 孪生 (id)。tag 会另打 `v0.1.4-alpha2.2`。
# 2026-09-15 再升到 **0.1.4-alpha2.3**: CLI 命令面 (docs/169) —— `loment` 从 9 条命令扩到 38 条,
# 命令前端本身就是 Loment 写的 (loment/tools/lomcli.lomt), 两个启动器各加一行转发。
# tag 会另打 `v0.1.4-alpha2.3`。
# 2026-09-16 升到 **0.1.4-pre1**: 语言的两处开口交给使用者 —— ①**后缀不再是语言的一部分**
# (只有 `.lom` 是 L0, 别的后缀都是 L1 源; 项目用 `loment.conf` 的 `source_ext` 定自己的后缀),
# ②**自定义 `loment` 命令** (`loment foo` -> PATH 上的 `loment-foo`, 像 `git foo`)。
# 名字形式 `use <名字>` 也从"只认仓库四根"改成按层搜 (deps/ -> 自带 store -> 内置根)。
# alpha 系列到此为止: 这两条是给**别人**用这个语言的口子, pre 之后不再改语言面。
# 2026-09-16 升到 **0.1.4-pre2**: **FFI** (docs/173) —— `extern fn` 进语言, 两个编译器都发
# `declare` 且逐字节一致; `lomelf` 能读外部 ELF 目标文件并**按 C ABI 传参**链接, 于是 Loment
# 程序真的调到了 C / C++ / Rust 的库; 运行期那一族 (Python / JS / Java) 走新加的进程桥
# `loment/lib/proc.lomt`。见 docs/174。
# 2026-09-18 升到 **0.1.4**（正式版，用户定的）：pre 系列到此为止。
# **边界如实记一句**：这一版发的是**能用的工具链**（自举、运行期无 Python、无 libc），
# 而"仓库里只有 Loment"那个里程碑**还没到** —— 参考实现（`tools/lomentc.py` 等）与
# Python 判据仍在仓里，六门表层语法的翻译器也还没有 Loment 孪生（`docs/189`）。
# 那句话的对外版本写在 README 的 `## Status`（与 `tools/loment_publish.py` 里那份
# 逐字同源）—— **改版本号时一起改**，`loment_publish` 的判据会核。
# 2026-10-10 升到 **0.1.4.5-pre**（用户定的）：**预告版** —— 这一版的用处不是"装完的东西",
# 而是**提前把 0.1.4.5 要装的三件事宣告出去**（用户原话：pre-release 不要求做完，
# 只要告诉大家我们准备发这个版本了）。三件事：LomentGC 四层梯全量上线 / 网络在
# 两个平台都能用 / lompi 升到 0.2.0 Alpha。**正文里"已落地"与"预告"要分清**
# （`.github/release-notes/v0.1.4.5-pre.md` 里逐条标了），别把预告写成既成事实。
# tag = `v0.1.4.5-pre`。**`-pre` 后缀同时也是"宣告版"的记号**：`release.yml` 整个跳过
# 带 `-pre` 的 tag（它只发通知、不出包），所以那一版**没有产物**，由人直接建：
# `gh release create v0.1.4.5-pre --prerelease --notes-file .github/release-notes/v0.1.4.5-pre.md`。
# 2026-10-12 升到 **0.1.4.5**（用户定的）：**正式版** —— 宣告到此兑现，这一版**出五件产物**
# （Linux `tar.gz` / Windows `zip` / Windows 自解压 `setup.exe` / skill `zip` / `SHA256SUMS`）。
# tag = `v0.1.4.5`，由 `release.yml` 在 **`windows-latest`** 上出包 —— 第五件要走 Windows 自带的
# `iexpress`，ubuntu runner 上出不来（那正是文件头原先记着的未解问题，2026-10-10 挪 runner 解决，
# 并在真 runner 上 `dry_run` 打通一次）。**正文口径按 `-pre` 那份重新核一遍**
# （`.github/release-notes/v0.1.4.5.md`）：当时在 review 的已经合了，仍然是设计稿的别写成既成事实。
# 2026-10-12 升到 **0.1.4.5-quickfix**（用户定的名字：**Loment 0.1.4.5 QuickFix**）：**补丁** ——
# 0.1.4.5 发出去两小时后发现：**双击安装包全程没有一个可见窗口**。实测那条路是通的（包解开了、
# `install.cmd` 真跑了、东西也真装上了），只是"装好了"与"什么都没发生"长得一模一样，还留下
# 卡在 `pause` 的僵尸 `cmd`。这一版补上安装器的**脸**：`welcome.ps1` —— 渐入圆角标 +
# `Welcome to Loment` → 选装到哪 → 进度（流式显示 `install.ps1` 的输出）→ 完成回执，**等你手动关**。
# **工具链本身一个字没动**（那八个二进制与 0.1.4.5 逐字节相同），所以这是补丁不是新版。
# tag = `v0.1.4.5-quickfix`（`release.yml` 只对 `-pre` 结尾的 tag 整条跳过；这一版带产物，
# 与那种"只发通知"的宣告版不是一回事）。
RELEASE = "0.1.4.5-quickfix"
RELEASE_NAME = "0.1.4.5 QuickFix"  # 人读显示名 (发行包/文档用同一个真源)

# 报错器 (`lomenterr`) **自己的版本号** —— 与语言版本是**两条线** (2026-10-13 用户定)。
#
# **为什么它该有自己的版本**: `lomenterr` 是一件**独立交付的程序**, 不是编译器的一部分 ——
# 它有自己的命令行面 (`--short`/`--json`/`--max`/`errconfig`), 自己的判据, 自己的孪生
# (`loment/tools/lomerrcheck.lomt`), 而且它的改动**与语言面无关**: 2026-09-18 到 10-13 之间
# 它改了七八轮 (四段说明卡 -> 六语言方言 -> 双语默认英文 -> 三个出口 -> 逐条建议),
# 而语言版本在这段时间里只动过一次。把两件事捆在一个号上, 就只能记成"语言又升了一版",
# 那既不真, 也让人没法说"我用的是哪个报错器"。
#
# **两条线各自独立, 但只有一处真源**: 这个常量是报错器版本的**唯一**来源 ——
# `loment_diag.py --dump-surface` 把它导进 `surface_data` (报错器用它打 `lomenterr version`),
# 文档里那份 (README / wiki) 由判据核。**不许在别处再写一遍字面量** (本仓那条老账:
# 同一份清单抄第二遍, 两处迟早不同)。
#
# 这条线对应的是报错器的三次交付 (逐条记在 `docs/182`):
#   0.1.0  编译期落地 —— 渲染器本身, 接进工具链与两个启动器
#   0.1.1  说明卡 + 六语言方言卡 + 上色 + 语法声明优先
#   0.1.2  双语 (默认英文) + 三个出口 (`--short`/`--json`) + 逐条建议
LOMENTERR = "0.1.2"
GLOBS = [
    "tools/lomc.py", "tools/lom_audit.py", "tools/lomc_test.py", "tools/lomentc.py",
    "tools/lomentc_test.py", "tools/potato.py", "tools/potato_test.py",
    "tools/potato_cross.py", "tools/potato_from.py", "tools/potato_measure.py",
    "tools/potato_llm_arm.py",
    "tools/potato_assert.py", "tools/loment.py", "tools/lomfmt.py",
    # 修改件 (`.lomf`, docs/257): 套用器 + 树里那些补丁。**补丁本身也进清单** ——
    # 它是"这一笔改了什么"的记录, 丢了就等于那一笔无从复核 (docs/179 §7.3 那条)。
    "tools/lomf.py", "loment/patches/*.lomf", "tools/lomdoc.py",
    "tools/lompkg.py", "tools/lomlib.py", "tools/loment_lib_test.py",
    # std 核的行为判据 (docs/180): lib/ 里那些函数算得对不对 —— 与
    # `loment_lib_test`(库系统) 是两件事, 原先只测了后者
    "tools/loment_std_test.py",
    # LibL 的 libc 面 (docs/232): 与真 glibc 逐字节对跑
    "tools/loment_libl_test.py",
    # LibL 家族第三个包 internet (docs/241): 对 CPython 标准库 + 真 http.server 对跑
    "tools/loment_internet_test.py",
    # 第四个包 os (docs/242): 与 CPython 的 os/time/signal **在同一个 Linux 环境里**对跑
    "tools/loment_os_test.py",
    # 真线程 thread_spawn (docs/245): 探针编成 ELF **真的起 N 个线程**在共享内存上累加,
    # 另外拿 gettid 证伪"顺序跑下来也能凑出同样的和"那条。负例在编译期 (入口名/形参个数)。
    "tools/loment_thread_test.py",
    # LumtUI (loment/lib/lumtui*.lomt, docs/191): GUI 库的判据。
    # 对照物三把不同的尺子 —— L0 字节 / 独立推出的布局 / FreeType。
    "tools/loment_lumtui_test.py",
    "tools/loment_cli_test.py",
    # 固定容量盘点 (docs/345 那件事的"数"): 自举侧有多少个固定容量、几个越界时说不出话
    "tools/loment_caps.py", "tools/loment_caps_test.py",
    # 报错器 (docs/182 §6/§8)。**位置与自举那份 `lomrel.lomt` 对齐** —— 两处的条目顺序
    # 就是清单的顺序, 插在不同位置会给出同集合不同顺序的两份清单 (见下面那段注解)。
    "tools/loment_err_test.py",
    # lompi (随包发行的独立命令, docs/170): 源码快照 + 它外面的正本与仓内副本的同步/校验
    "lompi/*.lomt",
    # lompi 的标准库: 随 Loment 一起装 (std/host/internet/os 四个包, 共 160 个文件 ——
    # 数见 `loment_lompi_test.STORE_FILES`; 用户 2026-09-16 定的随包形状)。
    # **版本号是故意写死的**: 自举那边 (loment/tools/lomrel.lomt) 的 glob 只认"一段目录 +
    # 一个名字模式", `**` 在那边展开不出递归 (实测只给一层, 于是把 std/ host/ 两个**目录**
    # 当文件收了进来)。升版本要同时改两份清单 —— `loment_lompi_test` 里那条
    # `test_release_manifest_covers_every_store_file` 就是钉这个的: 库改版而清单没跟上,
    # `loment_release --check` 会**看不见文件而照样绿**, 那条会红。
    "lompi/store/std/0.1.0/*", "lompi/store/host/0.1.0/*", "lompi/store/internet/0.1.0/*",
    "lompi/store/os/0.1.0/*",
    "tools/lompi_sync.py", "tools/loment_lompi_test.py",
    # 发布口 (docs/171): 把单仓里的 Loment / lompi 切出来推到各自的私有库
    "tools/loment_publish.py",
    # 发布口跟上了没 (docs/222): `loment lompicheck` —— 一条**注册出来的**用户命令,
    # 联网把 `FujoJTOP/lompi` 与 `FujoJTOP/loment` 的文件树逐条比。
    # **位置必须与自举那份 `lomrel.lomt` 逐行对齐**（那份只认名字, 没有注释可依）。
    "tools/lompicheck_test.py",
    "tools/loment_register_test.py",
    "tools/loment_diag.py", "tools/loment_build.py",
    "tools/loment_lsp.py", "tools/loment_tools_test.py", "tools/loment_boot.py",
    # 编译期子集解释器 (S4.0, docs/184 §9): 参考侧解释器 + 它的判据 + 语料。
    # **位置必须与自举那份 `lomrel.lomt` 逐行对齐** —— `lomrel` 的条目顺序就是这份
    # GLOBS 的顺序, 两处插在不同位置会给出**同集合不同顺序**的两份清单, 判据报
    # "落盘不同"而字节数一样 (见 `tools/lomt_from.py` 上面那条注释)。
    "tools/loment_interp.py", "tools/loment_ct_test.py",
    # `comefor` 的 token 层展开 (S4.1, docs/184 §9): 展开器 + 判据 + 最小方言演示。
    "tools/loment_comefor.py", "tools/loment_comefor_test.py",
    # 外部代码块的字节保真 (S1, docs/185)
    "tools/loment_extblock_test.py",
    "tools/loment_p7_test.py", "tools/loment_p8_test.py", "tools/loment_p9_test.py",
    # GC 证明面的量尺（`docs/210` §7）：把语料跑成 `gc_auto_alpha`，加总 v10 的 `gc_ladder`。
    # **两处清单插入同一位置** —— 次序就是发布清单的次序。
    "tools/loment_gc_surface.py", "tools/loment_gc_surface_test.py",
    "tools/loment_gc_why.py", "tools/loment_gc_why_test.py",
    "tools/loment_syscalls.py", "tools/loment_manual.py", "tools/ci.py",
    # `#343` 那一类的计量器（`#369`）：两条不变量（非零退出要带诊断、不许被信号打死）
    # 过一层语料，缺口的**账**在判据里，只许变短。
    "tools/loment_invariant_test.py",
    # 各判据套件共用的运行器 (`#371`): 门禁要看"哪条判据跳过了"，它跟 `ci.py` 是一对，
    # 所以排在它旁边（**位置必须与自举那份 `lomrel.lomt` 逐行对齐**）。
    # （合 main 时两条各插在同一处：两条都留，只要两份清单次序一致。）
    "tools/loment_suite.py",
    "tools/vscode_ext.py", "tools/vscode_ext_test.py",
    # 调试器 (docs/190): DAP 适配器 + ptrace 后端, 及其无头判据。
    # **位置与自举那份 `lomrel.lomt` 对齐** —— 见下面那条注释。
    "tools/loment_dap.py", "tools/loment_dap_test.py",
    "tools/mono_trace.py",
    "tools/loment_ir_diff.py", "tools/loment_rule_parity.py",
    # 渠道常量（`docs/231` §2.2）：参考实现侧那份恒为 dev；**自举侧那一份在
    # `loment/selfhost/checker.lomt` 里**（一个常量一行），打包期由 `loment_dist.py` 换掉。
    # **两处清单插入同一位置** —— 次序就是发布清单的次序。
    "tools/loment_channel.py", "tools/loment_channel_test.py",
    "tools/loment_seed.py", "tools/loment_seed_test.py",
    "tools/loment_fmt_test.py", "tools/loment_audit.py",
    "tools/loment_doc_test.py", "tools/loment_json_test.py",
    "tools/lomelf.py", "tools/loment_opt_obj.py", "tools/loment_elf_test.py", "tools/loment_pe_test.py",
    # LVM 的 T0（`docs/243`）：那份 IR 的**第四个读者**（解释器）+ 它那条判据
    # （同一批程序，原生产物与 VM 比 stdout 字节 + 退出码）。**两处清单插入同一位置**。
    "tools/lvm.py", "tools/lvm_test.py", "tools/lvm_twin_test.py", "tools/lvm_profile_test.py",
    # `lom/net.lom` 那条 L0 契约的判据 (docs/229): 四处对账 + 自证会红。
    # **位置与自举那份 `lomrel.lomt` 对齐**（两份清单的顺序就是它们的顺序）。
    "tools/loment_net_test.py",
    # 多语法前端 (docs/179): 形式对象 -> L1 接口单元, 及其端到端判据。
    # **位置与自举那份 `lomrel.lomt` 对齐** —— 清单的条目顺序就是这份 GLOBS 的顺序,
    # 两处插在不同位置会给出同集合不同顺序的两份清单, 判据报"落盘不同"而字节数一样。
    "tools/lomt_from.py", "tools/loment_multisyntax_test.py",
    # 六门表层语法各一个**大型项目** (`examples/multisyntax-projects/`): 前门翻出来的
    # Loment 跑出的数 == 对照组 == 独立期望值。**那批语料本身不进清单** —— 它要靠
    # `tools/` 才能跑，而 `tools/` 不随包发（与 `docs/179` §8 同一个理由）。
    "tools/loment_multisyntax_projects_test.py",
    # **超大型**项目 (100 个 Python 语法模块、1000 个函数): 语料是**生成物**,
    # 判据逐字节钉住它不漂。与上面那批同理, **语料本身不进清单** (tools/ 不随包发)。
    "tools/loment_megaproject_test.py",
    # 多语言程序 (docs/183 §8.2 的 S2 判据): 三段各用一门语法写, 真编真链真跑。
    "tools/loment_multilang_test.py",
    # 花括号族 (C/C++/Java/C#) 的**共享前端核** (docs/188 §7.1): 一份解析器 + 方言表。
    # 各门只给自己那张 `Dialect`。
    "tools/trans_core.py",
    # C 翻成 Loment 的翻译器与它的判据 (docs/186 Stage A)。
    "tools/ctrans.py",
    "tools/loment_ctrans_test.py",
    # Python 翻成 Loment (docs/187 Stage A 的第二门)。
    "tools/pytrans.py",
    "tools/loment_pytrans_test.py",
    # Java 翻成 Loment (docs/188 §7.1 六门里的第三门; 花括号族第二张方言表)。
    "tools/jtrans.py",
    "tools/loment_jtrans_test.py",
    # C# 翻成 Loment (六门里的第四门; 花括号族**多两层壳**的那一门)。
    "tools/cstrans.py",
    "tools/loment_cstrans_test.py",
    # C++ 翻成 Loment (六门里的第五门; 与 C 同一张形状、**不同的理由**要补转换)。
    "tools/cpptrans.py",
    "tools/loment_cpptrans_test.py",
    # Go 翻成 Loment (六门里的第六门; **不共用共享核** —— 三条形状都不一样)。
    "tools/gotrans.py",
    "tools/loment_gotrans_test.py",
    # Loment 的**自然语言写法** (docs/197)。与上面六门**不同类**: 那六门是别人已有的
    # 语言, 这一门是我们自己发明的 —— 所以它没有共享核, 也不共用那份"方言表"。
    "tools/nltrans.py",
    "tools/loment_nltrans_test.py",
    # Potato 形式对象 -> L1 接口单元 (docs/179): 发射那一半的 Loment 版
    # (与 `lompotc` 那条**前端**是一对 —— 合起来才是 `from_c -> emit_lomt` 整条路)。
    "tools/loment_lomtfrom_test.py",
    # 花括号族语法树 -> Loment 源码 (docs/186/189 第十八格): `trans_core` 那一半
    # (语料/电池从 `loment_ctrans_test` 引, 所以没有自己的数据文件)。
    "tools/loment_trans_test.py",
    # 自举侧的 Potato v5 发射 (docs/189 第十九格): 语料是现成的 `loment/examples/*.lomt`
    # 与 `lompi/store/**` / `loment/lib/**`, 没有自己的数据文件。
    "tools/loment_potato_emit_test.py",
    # `choose write grammar` (docs/188 §1): 读法由**声明**决定、出厂锁的取值表。
    "tools/loment_grammar_test.py",
    # PE 目标的 shim 机器码（tools/lomelf.py --dump-win-shim 重建；自举镜像照抄这一份）
    "loment/build/win_shim.bin",
    "tools/loment_genesis.py", "tools/loment_genesis_test.py",
    "tools/loment_status_test.py", "tools/loment_rel_test.py",
    "tools/loment_pkg_test.py", "tools/loment_lomc_test.py", "loment/lib/*.lomt",
    "tools/loment_lsp_test.py", "tools/loment_editors_test.py",
    "editors/vim/*.md", "editors/vim/syntax/*.vim", "editors/vim/ftdetect/*.vim",
    "editors/vim/ftplugin/*.vim",
    "tools/loment_filetype.py", "tools/loment_filetype_test.py",
    # 行尾门禁 (docs/161): 本清单的 sha 对 CRLF 免疫, 但自举判据按原始字节读源码 —— 两者配对
    "tools/loment_eol.py",
    # 发行包 (docs/162): 命令安装 + 自解压安装包
    "tools/loment_dist.py", "tools/loment_dist_test.py",
    # 签名 (docs/163): Authenticode + SHA256SUMS 分离签名
    "tools/loment_sign.py", "tools/loment_sign_test.py",
    # 源码包 (docs/164): 语言源码 + 编辑器工具, 只从 git 索引取
    "tools/loment_src.py",
    # 无 Python 自举 (docs/159): 启动脚本 + 种子 (参考实现发射的驱动 IR) + Loment 版格式化器
    "loment/bootstrap.sh", "scripts/lomc.ps1", "scripts/install-lsp.ps1",
    "loment/build/selfhost_driver.ll", "loment/build/genesis/*", "loment/tools/*.lomt",
    "editors/loment.ico",
    "editors/vscode/package.json", "editors/vscode/language-configuration.json",
    "editors/vscode/language-configuration-python.json",
    "editors/vscode/LICENSE", "editors/vscode/loment-icon.png",
    "editors/vscode/README.md", "editors/vscode/src/*.js",
    "editors/vscode/syntaxes/*.json",
    # 编译期子集语料 (S4.0): `loment_ct_test` 读它们, 判据随包发就得连语料一起发。
    "loment/ct/*.lomt",
    # `comefor` 演示对：方言源 + 手写展开源（判据要两份都在才跑得起来）。
    "loment/comefor/*.lomt",
    # 多语言程序那四份源 (三段外源语法 + 一份 Loment) 与它的说明。
    # **只写尾部 `*`**（不写 `*/*`）: 自举侧那个 glob 匹配器不认中间的通配
    # （实测 `loment/examples/multilang/*/*.lomt` 在它那边一条都展开不出来，
    # `loment_rel_test` 当场红）。一条一层目录写清楚，两边就一致。
    "loment/examples/multilang/*.lomt",
    "loment/examples/multilang/01-c/*.lomt",
    "loment/examples/multilang/02-python/*.lomt",
    "loment/examples/multilang/03-java/*.lomt",
    "loment/examples/multilang/README.md",
    # 一份**纯 Loment 项目**, 叶子单元用 C 拼法写 (docs/211)。与 multilang 同类 —— 也是
    # **一个目录**, 所以同样要逐条列 (单层的 `loment/examples/*.lomt` 收不到子目录)。
    "loment/examples/cproject/*.lomt",
    "loment/examples/cproject/README.md",
    # 外部代码块的**词法**语料 (S1): 只喂两个词法器, 不是可编单元。
    "loment/extblock/*.lomt",
    # **词法**语料: 只有词法器才看得见的形状。**故意不放进 `loment/examples/`** ——
    # 那个目录按定义全是公开 API（`loment_manual.py` 直接 glob 它出手册页），
    # 一份夹具进去会平白长出一页 "api/<名字>.md"。这里的文件只被 `loment_p8_test`
    # 的 token 流对照点名，**不进任何编译语料**。
    "loment/lex/*.lomt",
    # C 翻译器的语料 (docs/186): **源码语言那一侧的普通 `.c`**，不是装着什么的 `.lomt`。
    # 只被 `loment_ctrans_test` 读；不进任何 Loment 编译语料。
    "loment/ctrans/*.c",
    # Python 翻译器的语料 (docs/187): 同理，是**普通 `.py`**。
    "loment/pytrans/*.py",
    # Java 翻译器的语料 (docs/188 §7.1): 同理，是**普通 `.java`**。
    "loment/jtrans/*.java",
    # C# / C++ / Go 翻译器的语料: 同理，是**普通 `.cs` / `.cpp` / `.go`**。
    "loment/cstrans/*.cs",
    "loment/cpptrans/*.cpp",
    "loment/gotrans/*.go",
    # 自然语言写法的语料与它的**同源 Loment 孪生** (docs/197): 两份**必须是同一个
    # 程序**的两种拼法 —— 判据比的是"翻出来的 Loment 逐字节相同", 所以两份都得在。
    "loment/nltrans/*.nl", "loment/nltrans/*.lomt",
    # `#343` 那一类（"编译器不作声地死掉"）的**最小见证**（`#369`）：`#343` 表里每一格
    # 各一只，是这一类**仅有的**目击者 —— 边界走查在判据里现生成，见证只能是文件。
    "loment/invariants/*.lomt",
    "loment/examples/*.lomt", "loment/selfhost/*.lomt", "loment/corpus.json",
    "lom/*.lom",
    # 设计文档: 这份清单是 **Loment 线**的, 而本仓就是 Loment 的开发口 —— `docs/` 里
    # 全是这条线的文档, 所以一条 `docs/*.md` 就够, 不必再按名字分段收。
    #
    # 原文是 `docs/14*.md` + `docs/15*-loment-*.md` + `docs/16*-loment-*.md` —— 那是
    # **旧树**（`docs/` 里 FujoOS 与 Loment 两线混放）留下的写法。它在那边就已经两头不讨好:
    # `14*` 捞进了内核文档 (`docs/14-tss-irq.md`、`144/145/146/149-内核-*`), 而 `15*`/`16*`
    # 的 `-loment-` 收紧又**漏掉**了 110/142/147/17x 那几篇名字里没有 loment 的。
    # 搬到开发口之后前一半变成 5 条**指向不存在文件**的陈旧项 —— 2026-09-17 归因
    # `loment_rel_test` 那两条红时查出来的。
    "docs/*.md",
    "docs/manual/*.md",
    "docs/manual/api/*.md",
    # 工具与判据**自己**也要在清单里 —— `loment_tools_test::test_every_tool_is_in_the_release_manifest`
    # 钉住"被跟踪的 `tools/*.py` 一个都不能少"。这一组就是那条判据第一次跑出来的缺项（14 个，
    # 其中 5 个是判据）：它们有的在门禁里跑、有的被别的工具的判据点名，却都不在清单上。
    # **`docs/i18n/` 不在此列** —— 那是译者向工件，`docs/i18n/glossary.md` §1 明写不随包发。
    "tools/_safepath.py", "tools/fuai_contract_check.py", "tools/fuic.py",
    "tools/fujopack.py", "tools/lom_spec_emit.py", "tools/loment_bootstrap.py",
    "tools/loment_capasserts_test.py", "tools/loment_eol_test.py",
    "tools/loment_ffi_test.py", "tools/loment_hosted_test.py", "tools/loment_i18n_test.py",
    # `docs/NNN-slug.md` 的**编号**：查重号 / 取空号 / 撞了改号（`docs/228` 那次撞号之后加的）。
    # 判据是"基线只许变短"那一条，见 `loment_docs_test` 的 docstring。
    "tools/loment_docs.py", "tools/loment_docs_test.py",
    # `lom/` 那道禁区（`docs/239` §5）：把 `gate.yml` 里那段脚本抠出来真跑 ——
    # 门是 L0 契约唯一的守卫，改它等于改守卫，所以它自己也得有判据。
    "tools/loment_door_test.py",
    # 准入判定（`docs/238`）：判定是纯函数，两个实现逐字节相同。
    "tools/loment_admit.py", "tools/loment_admit_test.py",
    # 零宽那一帧（`docs/240`）：定长、没有载荷字段。
    "tools/loment_zwframe.py", "tools/loment_zwframe_test.py",
    # 世界端口的量尺 (`docs/219` S0) 与它的判据。**插入位置与
    # `loment/tools/lomrel.lomt` 的 `globs_text()` 逐行对齐** (`loment_rel_test` 钉)。
    "tools/loment_ports.py", "tools/loment_ports_test.py",
    "tools/loment_probe.py", "tools/loment_release.py", "tools/loment_status.py",
    "tools/loment_syscalls_test.py",
    # 权威状态在 `origin` 那一条 (CLAUDE.md 第一节): 读 origin 的版本/tag/落后数, 让
    # "先 fetch 再读状态"可执行。**这两行的位置与 `loment/tools/lomrel.lomt` 的
    # `globs_text()` 逐行对齐** —— 两处插在不同位置会给出同集合不同顺序的两份清单,
    # `loment_rel_test` 报"落盘不同"而字节数一样。
    "tools/loment_state.py", "tools/loment_state_test.py",
]


def sha(p: Path) -> str:
    """内容哈希。文本按**通用换行**归一后哈希 (同一文件在 LF/CRLF 检出下哈希相同);
    二进制 (图标/压缩包) 按原始字节哈希 —— 清单里两类可以混, 消费者只比 sha256。"""
    raw = p.read_bytes()
    if b"\x00" in raw:                 # 二进制: 按原始字节哈希
        return hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build() -> dict:
    """清单的次序**按 posix 路径的字节序**，与平台无关。

    **为什么不能就用 `sorted(glob(...))`**：那排的是 `Path` 对象，而 `Path` 的比较在
    Windows 上按**大小写折叠**、在 POSIX 上按字节 —— 同一个目录能排出两种次序（实测：
    `loment/build/genesis/` 下 `SHA256SUMS` 与 `lomelf-linux-x64.elf` 谁在前，
    两种平台正好相反）。后果不是"好不好看"：**本机重算的清单与 CI 重算的永远不一致**
    ⇒ CI 每次都推一笔 `[生成物]` ⇒ 那笔机器人推送会把必需检查挂成 `action_required`
    ⇒ PR **永远**卡在 BLOCKED。**每个 PR 白付十几分钟 + 一次人工放行。**
    """
    files = []
    for g in GLOBS:
        for p in sorted(ROOT.glob(g), key=lambda q: q.as_posix()):
            if p.is_file():
                files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)})
    return {"release": RELEASE, "files": files}


def checksums_text(doc: dict) -> str:
    """SHA256SUMS 风格的校验和清单 (M88)。"""
    return "".join(f"{x['sha256']}  {x['path']}\n" for x in doc["files"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_release")
    # 路径**可选** (与 `--checksums PATH` 同一形状): 给了就写那里, 不给还是仓库那条。
    # 与自举那份 `lomrel.lomt` 的 `--emit` 逐字对齐 —— 两边都认这条可选路径,
    # `loment_rel_test` 才能拿"各写一份进程私有路径, 再比字节"来验它。
    ap.add_argument("--emit", nargs="?", const="", metavar="PATH",
                    help="写发布清单 (默认 loment/build/release-manifest.json)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--checksums", metavar="PATH", help="写 SHA256SUMS 风格清单 (M88)")
    a = ap.parse_args(argv)
    want = build()
    if a.checksums:
        # 显式 LF: 校验清单会被 sha256sum -c 之类逐行解析, CRLF 会让文件名带上 \r
        Path(a.checksums).write_text(checksums_text(want), encoding="utf-8", newline="\n")
        print(f"[OK] {a.checksums} ({len(want['files'])} 行)")
        return 0
    if a.emit is not None:
        # 给没给路径决定写哪儿; 打出来的那一行**回显调用方给的那个串** (自举侧也是这么做的),
        # 不给才回显仓库那条默认路径。
        dest = Path(a.emit) if a.emit else OUT
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 显式 LF: 清单是机器读的工件 (行尾不该随宿主变), 见 loment_manual 同处注释
        dest.write_text(json.dumps(want, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"[OK] {a.emit or OUT.relative_to(ROOT)} ({len(want['files'])} 个工件)")
        return 0
    if a.check or (a.emit is None and not a.checksums):
        # 无参数 = 门禁模式 (与仓库其它工具同一约定: ci.py 的静态门禁按 main() 调用)
        if not OUT.exists():
            print(f"[ERR] {OUT.relative_to(ROOT)} 缺失 (运行 --emit)")
            return 1
        got = json.loads(OUT.read_text(encoding="utf-8"))
        gmap = {x["path"]: x["sha256"] for x in got.get("files", [])}
        wmap = {x["path"]: x["sha256"] for x in want["files"]}
        bad = [p for p in wmap if gmap.get(p) != wmap[p]]
        extra = [p for p in gmap if p not in wmap]
        for p in bad[:8]:
            print(f"[DIFF] {p}")
        for p in extra[:8]:
            print(f"[STALE] {p}")
        print(f"loment_release: {len(wmap) - len(bad)}/{len(wmap)} 一致"
              f"{f' (+{len(extra)} 陈旧)' if extra else ''}")
        return 1 if bad or extra else 0
    print("[ERR] 需要 --emit 或 --checksums", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
