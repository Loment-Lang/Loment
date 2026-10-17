#!/usr/bin/env python3
"""fujoci — QEMU 无头启动 + 日志断言自动化 (M78)

CI 流水线:
  1) cargo build --release (kernel)
  2) flatten (--pad 0x1A0000)
  3) 用例矩阵: 兼容矩阵 (fujoregress 9) + 里程碑 demo (m61..m77 抽样,
     每个 initrd → QEMU 无头 → 注入 'os run hermes' → 日志断言
     'MXX RESULT: PASS' 或专用关键字)
  4) 报告: 控制台 + JSON (--json out.json); 退出码 = 全 PASS 0 / 有 FAIL 1

用法: python tools/ci.py [--only IX] [--json out.json] [--fast]
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

from _safepath import safe_open

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


KERNEL = os.path.join(ROOT, "kernel", "fujo-kernel.bin")
# 端口可覆盖: 同机并行跑多个 agent 时避免互抢 (FUJO_MON_PORT / FUJO_SER_PORT)
MON_PORT = int(os.environ.get("FUJO_MON_PORT", "4568"))
SER_PORT = int(os.environ.get("FUJO_SER_PORT", "4001"))
KEYS = ["o", "s", "spc", "r", "u", "n", "spc", "h", "e", "r", "m", "e", "s", "ret"]

# 兼容矩阵 (9) —— 复用 fujoregress 的断言面
COMPAT = [
    ("elf-linux", "sdk/linux/m30_linux.elf", "M30 RESULT: PASS", 14.0),
    ("elf-linux2", "sdk/linux/m33_trace.elf", "M33 RESULT: PASS", 14.0),
    ("run-fujopack", "sdk/build/m31_res.run", "M31 RESULT: PASS", 14.0),
    ("multi-fujorun", "sdk/build/m32_multi.initrd", "M32 RESULT: PASS", 14.0),
    ("macho-darwin", "sdk/mac/m29_darwin.macho", "M29 RESULT: PASS", 14.0),
    ("pe-m3", "sdk/win/hello_win.exe", "M3 verified", 14.0),
    ("pe-m26", "sdk/win/m26_win.exe", "M26 RESULT: PASS", 14.0),
    ("pe-m27", "sdk/win/m27_mingw.exe", "M27 RESULT: PASS", 14.0),
    ("pe-m30", "sdk/win/m30_win.exe", "M30 RESULT: PASS", 14.0),
]

# 里程碑 log 断言抽样 (Wave2-5 关注面)
MILESTONES = [
    ("m61-blit", "sdk/linux/m61_blit.elf", "M61 RESULT: PASS"),
    ("m62-shader", "sdk/linux/m62_shader.elf", "M62 RESULT: PASS"),
    ("m63-mix", "sdk/linux/m63_mix.elf", "M63 RESULT: PASS"),
    ("m64-smp", "sdk/linux/m64_smp.elf", "M64 RESULT: PASS"),
    ("m65-tss", "sdk/linux/m65_tss.elf", "M65 RESULT: PASS"),
    ("m66-pcache", "sdk/linux/m66_pcache.elf", "M66 RESULT: PASS"),
    ("m67-irq", "sdk/linux/m67_irq.elf", "M67 RESULT: PASS"),
    ("m68-perf", "sdk/linux/m68_perf.elf", "M68 RESULT: PASS"),
    ("m69-game2", "sdk/linux/m69_game2.elf", "M69 RESULT: PASS"),
    ("m71-asm", "sdk/linux/m71_asm.elf", "M71 RESULT: PASS"),
    ("m72-ld", "sdk/linux/m72_ld.elf", "M72 RESULT: PASS"),
    ("m73-edit", "sdk/linux/m73_edit.elf", "M73 RESULT: PASS"),
    ("m74-cc", "sdk/linux/m74_cc.elf", "M74 RESULT: PASS"),
    ("m75-dbg", "sdk/linux/m75_dbg.elf", "M75 RESULT: PASS"),
    ("m76-trace", "sdk/linux/m76_trace.elf", "M76 RESULT: PASS"),
    ("m77-win", "sdk/linux/m77_win.elf", "M77 RESULT: PASS"),
    ("m82-ut", "sdk/linux/m82_ut.elf", "M82 RESULT: PASS"),
    ("m83-leak", "sdk/linux/m83_leak.elf", "M83 RESULT: PASS"),
    ("m84-dump", "sdk/linux/m84_dump.elf", "M84 RESULT: PASS"),
    ("m86-wmap", "sdk/linux/m86_wmap.elf", "M86 RESULT: PASS"),
    ("m87-mcard", "sdk/linux/m87_mcard.elf", "M87 RESULT: PASS"),
    ("m88-sess", "sdk/linux/m88_sess.elf", "M88 RESULT: PASS"),
    ("m89-ctx", "sdk/linux/m89_ctx.elf", "M89 RESULT: PASS"),
    ("m90-cctx", "sdk/linux/m90_ctx.elf", "M90 RESULT: PASS"),
    ("m91-cap", "sdk/linux/m91_cap.elf", "M91 RESULT: PASS"),
    ("m92-route", "sdk/linux/m92_route.elf", "M92 RESULT: PASS"),
    ("m93-infer", "sdk/linux/m93_infer.elf", "M93 RESULT: PASS"),
    ("m94-fupm", "sdk/linux/m94_fupm.elf", "M94 RESULT: PASS"),
    ("m95-life", "sdk/linux/m95_life.elf", "M95 RESULT: PASS"),
]

# 非竞速用例 (键盘 sleep 快速, boot 慢): 全部用例 boot 7.5s + 输入 1.5s
BOOT_S = 7.5
KEY_HZ = 0.10

# L0/L1 静态门禁 (docs/141, docs/143): 先于 QEMU 矩阵, 失败即中止 — 接口漂移不该等到运行期。
STATIC_CHECKS = ("lomc_test", "lom_audit", "lomentc_test", "potato_test", "potato_cross",
                 # 编译期子集解释器 (S4.0, docs/184 §9)
                 "loment_ct_test",
                 # `comefor` 的 token 层展开 (S4.1, docs/184 §9)
                 "loment_comefor_test",
                 # 外部代码块的字节保真 (S1, docs/185)
                 "loment_extblock_test",
                 # 多语言程序 (docs/183 §8.2 的 S2 判据)
                 "loment_multilang_test",
                 # C 翻成 Loment: 翻译出来的跑出的数 == clang 编那份 C 跑出的数 (docs/186)
                 "loment_ctrans_test",
                 # Python 翻成 Loment: 跑出的数 == CPython 跑那份 Python 的数 (docs/187)
                 "loment_pytrans_test",
                 # Java 翻成 Loment: 跑出的数 == javac 编那份 Java 跑出的数 (docs/188 §7.1)
                 "loment_jtrans_test",
                 # C# 翻成 Loment: 跑出的数 == dotnet 编那份 C# 跑出的数 (docs/188 §7.1)
                 "loment_cstrans_test",
                 # C++ 翻成 Loment: 跑出的数 == g++ 编那份 C++ 跑出的数 (docs/188 §7.1)
                 "loment_cpptrans_test",
                 # Go 翻成 Loment: 跑出的数 == go build 那份 Go 跑出的数 (docs/188 §7.1)
                 "loment_gotrans_test",
                 # 自然语言写法 (docs/197): 同一个程序的两种拼法 -> **逐字节同一份** Loment,
                 # 而且翻出来的那一份真编真跑, 跑出的数 == 独立推出来的期望值
                 "loment_nltrans_test",
                 # Potato 形式对象 -> L1 接口单元 (docs/179) 的 Loment 版 (发射那一半)
                 "loment_lomtfrom_test",
                 # 花括号族语法树 -> Loment 源码 (docs/186/189 第十八格) 的 Loment 版
                 "loment_trans_test",
                 # 自举侧的 Potato v5 发射 (docs/189 第十九格): `stage1 --emit-potato`
                 # 与 `lomentc.emit_potato` **逐字节**比, 子集外点名拒绝
                 "loment_potato_emit_test",
                 # `choose write grammar`: 读法由声明决定、出厂锁的取值表、声明先抹掉
                 "loment_grammar_test",
                 "loment_tools_test", "loment_p7_test", "loment_p8_test", "loment_p9_test",
                 # GC 证明面的量尺（`docs/210` §7）—— 语料跑成 alpha、加总 gc_ladder。
                 "loment_gc_surface_test",
                 # 逐站点说出"为什么落到那一层"（`docs/229`）—— 判据 A 是与编译器的**对账**：
                 # 这份报告的四层直方图必须逐份等于 `_count_gc_ladder()`，漂了就红。
                 "loment_gc_why_test",
                 "loment_rule_parity", "loment_seed_test", "loment_fmt_test", "loment_doc_test",
                 # 渠道（`docs/231` §5 判据 D/F）：发行版种子可重算、且与 dev 只差一个字节；
                 # 发行版驱动器拒 `choose vm`、dev 收（后者要 clang + WSL，没有就 SKIP）。
                 "loment_channel_test",
                 "loment_json_test", "loment_pkg_test", "loment_lomc_test", "loment_lsp_test",
                 "loment_elf_test", "loment_pe_test", "loment_genesis_test", "loment_status_test",
                 # LVM 的 T0（`docs/243`）：同一批程序，**原生产物与 VM** 比 stdout 字节 + 退出码。
                 "lvm_test",
                 # LVM 自举镜像的**孪生判据**（`docs/243` §5）：同一份 `.ll` 喂参考实现与
                 # `loment/tools/lvm.lomt`（走种子自举链编成 ELF），比 stdout 字节 + 退出码。
                 # 它同时也在测**自举链把镜像编得对不对**。没有 clang 就 SKIP（不假装成立）。
                 "lvm_twin_test",
                 # LVM 的 T1（`docs/247`）：反馈剖面 —— 确定性 / 关上什么都不写 /
                 # 块次数 == 出边和（对语料每一份）/ 循环次数已知的夹具逐条对上。
                 "lvm_profile_test",
                 # `lom/net.lom` 那条 L0 契约 (docs/228): 号 → 面的单源, 四面对账
                 "loment_net_test",
                 # FFI 第 1 阶段 (docs/173): 外部目标文件 + C ABI + lomelf 单独链接, 跑出结果
                 "loment_ffi_test",
                 # 世界端口的量尺 (docs/219 S0): 目标文件/归档的端口表 + 四个类别,
                 # 含 `dlsym`(按名解析) 与内联 `syscall`(符号表看不见) 两个证伪对。
                 "loment_ports_test",
                 # 对外端口的第一条腿 (docs/222): `--hosted` 真的链上系统 libz,
                 # 一份 Loment 源 compress/uncompress 逐字节回原文; 附一条证伪。
                 "loment_hosted_test",
                 "loment_rel_test",
                 "loment_editors_test",
                 # **`loment_release` 与 `loment_manual` 从 2026-10-16 起不在这里。**
                 # 它们的无参模式是"入库生成物 == 当前树"的**新鲜度**判据，而那条判据
                 # 只在**唯一权威的那棵树**（`main`）上为真。以前靠"每个 PR 都重算一遍"
                 # 把它变成处处为真，代价是那两个文件是**全树台账**（每一行都是别人的
                 # 文件）⇒ 两个并发 PR 写相邻行 ⇒ **必冲突**（实测见
                 # `.github/workflows/manifest.yml` 文件头那节）。
                 #
                 # 替代它的不是"少了条判据"，是**换了一处执行**：`manifest.yml` 的
                 # `emit-main` 在每一次合并后重算（与门禁同时起跑），而
                 # `loment_tools_test` 里那两条新判据钉住这套安排本身 ——
                 # PR 侧不许再提交整树生成物、main 侧的写者必须存在且不排在门禁后面。
                 #
                 # **工具本身没删**：`--check` 仍可自查一份 checkout，孪生对拍
                 # （`loment_rel_test`）也照跑 —— 那是审计主张 C9/C23 的量尺。
                 "vscode_ext_test", "loment_filetype_test", "loment_status",
                 # 调试器 (docs/190): 无头 DAP 往返 —— 断点命中源行、单步按行走、落不上的要说
                 "loment_dap_test",
                 "fuai_contract_check", "loment_eol", "loment_dist_test",
                 # S1 第一格 (docs/189 §3): `lomeol.lomt` 与 `loment_eol.py` 逐字节相同
                 "loment_eol_test",
                 # S1 第二格: `lomsyscalls.lomt` 与 `loment_syscalls.py` 逐字节相同
                 "loment_syscalls_test",
                 # S1 第五格 (docs/189 §3): `lomcapasserts.lomt` 与 `potato_assert.py` 逐字节
                 # 相同（含 `--emit-rust` 的**落盘字节** —— 那一条抓到参考实现随宿主换行的问题）
                 "loment_capasserts_test",
                 # 英文版的翻译戳 (docs/i18n/glossary.md §2): 中文源一改, 戳过期就红。
                 # **体例里点名了这条判据的名字, 而文件此前不存在** —— 写在文档里、没人执行。
                 "loment_i18n_test",
                 # `docs/NNN` 的编号：**不许出现新的撞号**，且基线只许变短
                 # (2026-10-11 实测 main 上有 7 个号被两份以上文档共用) —— `tools/loment_docs.py`
                 "loment_docs_test",
                 # `lom/` 那道禁区自己的判据（把 gate.yml 里那段脚本抠出来跑）
                 "loment_door_test",
                 # 准入判定（`docs/238`）：两个实现逐字节相同 + 那条不对称
                 "loment_admit_test",
                 # 零宽那一帧的静态规则 + 两条证伪（`docs/240`）
                 "loment_zwframe_test",
                 "loment_sign_test", "loment_src", "loment_lib_test", "loment_cli_test",
                 # 固定容量盘点 (docs/345): 那两个数与那份名单不可漂
                 "loment_caps_test",
                 # 报错器 (docs/182 §6/§8): 渲染的字段、两条报错通道、缺席时的兜底
                 "loment_err_test",
                 # `#343` 那一类的**计数**（`#369`）：编译器不作声地死掉 —— 两条不变量
                 # （非零退出要带一条诊断、不许被信号打死）过一层语料，**两份实现都问**
                 # （参考实现是 Python，发不出信号；用户真正跑的是自举驱动）。缺口记成账，
                 # 只许变短。实测约 17s（含一次 种子 -> clang -> stage1）。
                 "loment_invariant_test",
                 # std 核的行为判据 (docs/180): lib/ 里的函数算得对不对
                 "loment_std_test",
                 # LibL 的 libc 面 (docs/232): 同一组调用与**真 glibc** 逐字节对跑。
                 # 与 `loment_std_test` 的分界是**对照物**: 那边是 Python, 这边是 libc 自己。
                 "loment_libl_test",
                 # LibL 家族第三个包 internet (docs/241): 对照物是 CPython 标准库
                 # (ipaddress / urllib.parse / http.client) + **真起一个 http.server**。
                 "loment_internet_test",
                 # 第四个包 os (docs/242): 探针编成 ELF 与 CPython 的 os/time **在同一个
                 # Linux 环境里**对跑 (Windows 走 WSL, Linux 直接跑)。
                 "loment_os_test",
                 # M84 真线程 (docs/245): 探针编 ELF, **真的起 N 个线程**在共享内存上
                 # 累加 (WSL/原生 Linux), 负例在编译期。与 os 那条同一个环境借口。
                 "loment_thread_test",
                 # LumtUI (docs/196): GUI 库的三把尺子 —— .fuc 与 lom/fuc.lom 逐字节、
                 # 布局/命中/焦点与独立推出的期望一致、字体与 FreeType 对
                 "loment_lumtui_test",
                 "loment_lompi_test", "lompi_sync", "loment_publish",
                 # 发布口跟上了没 (docs/222): `loment lompicheck` 的映射与比对。
                 # **联网那条可跳**，且不断言"两边一致"（掉队是开发侧该跑 `--push`，
                 # 不是这份 PR 的错）—— 见那份判据的文件头。
                 "lompicheck_test",
                 # 在源码里注册一条 `loment` 命令 (docs/223): 声明扫描 /
                 # `--print-command` / 两个启动器的命名分流 / 生成的进程入口真跑一遍。
                 "loment_register_test",
                 # 多语法前端 (docs/179, docs/175 §6 第 4 条): 外源源码 -> 接口单元
                 # -> L1 调用 -> 链外部目标文件 -> 跑出预期退出码
                 "loment_multisyntax_test",
                 # 六门表层语法**各一个大型项目**（带正文、能真跑）: 前门翻出来的 Loment
                 # 跑出的数 == 对照组（clang/g++/javac/go/CPython/rustc）== 独立期望值
                 "loment_multisyntax_projects_test",
                 # **超大型**: 100 个 Python 语法模块、1000 个函数, 一次编一次跑;
                 # 语料是生成物, 判据逐字节钉住它不漂
                 "loment_megaproject_test",
                 # 权威状态在 `origin`、不在本地树那一条 (CLAUDE.md 第一节): 判据在临时
                 # 夹具仓里验"工具报的是权威那一份还是工作树那一份", 并钉住"无法判定"
                 # 必须是**可见的** rc=2, 不能伪装成同步
                 "loment_state_test")


#: **不能与别的检查同时跑**的那几条 —— 它们**写仓库里的共享位置**。
#: `lompi_sync` 会把正本同步进 `lompi/`(只在不等时写, 但"只在不等时"靠不住:
#: 一次脏树就够), 而好几条判据要读 `lompi/`。并行调度时这一组单独串行跑。
#: (`loment_dist_test` 用的 `loment/build/dist` 只有它自己碰, 不必独占。)
EXCLUSIVE_STATIC = ("lompi_sync",)

#: 各条判据在 **CI runner**（ubuntu-latest, 4 vCPU）上的实测耗时, 秒。
#: 量于 2026-09-26 那一轮 `gate.out`（run 36209626967）, **`loment_dist_test` 除外** ——
#: 它那 954s 里有 3/4 是"用 stage1 重编自举驱动", 2026-10-09 已经拿掉（docs/213）,
#: 这里填的是拿掉之后的估计值。
#:
#: **只用来切分片**, 且过期只会让分片不均 —— 不会漏判据: 分片是对 `STATIC_CHECKS`
#: **全表**切的（见 `shards`）, 没登记的名字按 `SHARD_DEFAULT_S` 算。
SHARD_COST = {
    "lomentc_test": 15.8,
    "potato_test": 16.8,
    "loment_ct_test": 1.9,
    "loment_ctrans_test": 168.9,
    "loment_lomtfrom_test": 9.3,
    "loment_trans_test": 136.6,
    "loment_potato_emit_test": 50.0,
    "loment_grammar_test": 1.1,
    "loment_tools_test": 6.3,
    "loment_p8_test": 229.2,
    "loment_rule_parity": 2.5,
    # 渠道（`docs/231`）：三次自举驱动 IR + 两个 ELF 构建 —— 与本机实测同量级时再收窄。
    "loment_channel_test": 300.0,
    "loment_seed_test": 243.8,
    "loment_docs_test": 0.5,
    "loment_editors_test": 1.3,
    "loment_dist_test": 110.0,
    "loment_src": 1.2,
    "loment_lib_test": 10.0,
    "loment_cli_test": 50.8,
    "loment_err_test": 6.6,
    # `#369` 的计量器：28 次运行 + 一次 `种子 -> clang -> stage1`。这个数是**估的**
    # （于 Windows+WSL 实测 17s；runner 上原生起 stage1，应该更小）—— 按这张表自己的
    # 说明，估偏只会让分片不均，不会漏判据。
    "loment_invariant_test": 20.0,
    "loment_std_test": 1.0,
    "loment_libl_test": 3.0,
    # 实测: 8 条; 每条各编一个原生探针 (参考编译器 IR + lomelf), 端到端那条还起一个
    # 真 http.server。本机 6.9s。
    "loment_internet_test": 10.0,
    # 实测: 5 条; 一条编 ELF 探针并在 WSL 里跟 CPython 对跑, 另一条起 4 个真线程
    # 跑 20 万次临界区 (docs/245 阶段 2)。本机 5.6s。
    "loment_os_test": 20.0,
    # 实测: 6 条; 两条各编一次 ELF 探针并在 WSL 里起 4 个线程跑 (另四条是编译期负例,
    # 不落产物)。本机 0.8s —— WSL 热的时候。冷启动给宽一点。
    "loment_thread_test": 30.0,
    "loment_lompi_test": 119.8,
    "loment_multisyntax_projects_test": 2.1,
    "loment_net_test": 0.2,          # 实测: 读四份表 + 四条自证, 不编译
    # 含 300 个 Java 模块那一档的 javac + JVM（没有 javac 时那一支可见地 SKIP，快得多）
    "loment_megaproject_test": 15.0,
}
#: 没登记耗时的判据按这个算。实测大部分判据在秒级, 取 8s 偏保守。
SHARD_DEFAULT_S = 8.0

#: **整条跑、且各占一片**的那几条 —— 它们是长尾（一条就 200s+）, 与任何别的判据同片
#: 都不会更快（墙钟是 max 不是 sum）。
#:
#: **为什么独占还更划算**: 它们是**单线程长跑**, 跟三条重判据挤同一台 4 vCPU 的 runner
#: 时会互相拖 —— 实测独占一个安静的 runner 之后 `loment_seed_test` 从 **244s 掉到 172s**、
#: `loment_p8_test` 从 229s 掉到 156s（docs/213 §5）。**这才是这条名单现在的理由**;
#: 以前还有一条"独占之后那一片的工具链可以压到最小", 那个随工具链做成镜像而作废了
#: （各片现在是同一份镜像, 没有"档位"可言）。
#: 往这里加一条之前: 先确认它真的是**长尾单线程**（否则切成两片更划算）, 并把它从
#: `SHARD_COST` 的负担里拿走（它自己一片, 不再参与均分）。
ISOLATED_STATIC = ("loment_seed_test", "loment_p8_test", "loment_channel_test")


def shards(n: int) -> list[list[str]]:
    """把 `STATIC_CHECKS` 切成 `n` 片: 独占的、长尾的各自成片, 其余按实测耗时贪心均分。

    **为什么是计算出来的而不是手写一张表**: 门禁的分片表一旦手写, 新增一条判据时
    它就会**静默落在所有分片之外** —— 那条判据从此不跑, 而门禁照样绿。这正是
    "加语法构造要清点所有读 L1 源的入口"那类洞的分片版。计算出来的表是
    `STATIC_CHECKS` 的一个**划分**, 覆盖是构造性的; 下面那条断言是防止将来改写时
    把它悄悄破坏（`--shard-plan` 也会跑一遍）。

    片序（`n` 足够大时）: **片 0** = `EXCLUSIVE_STATIC` —— 它们写仓库共享位置, 不能与
    别的判据同时跑, 而摊到并行 job 上之后"同时"还包括**别的 job**, 所以那一片自己
    一个 job（`gate.yml` 里其余分片 `needs:` 它）; 接着每一条 `ISOLATED_STATIC`
    各占一片; 最后才是均分出来的片。`n` 不够分时**直接断言失败**, 不静默合并 ——
    静默合并会把"那几条独占一片"这件事悄悄变成"它们与什么都有的一片挤在一起"。

    `n <= 1` 时退化成"整道门禁一片"（就是老行为）。
    """
    if n <= 1:
        return [list(STATIC_CHECKS)]
    excl = [x for x in STATIC_CHECKS if any(e in x for e in EXCLUSIVE_STATIC)]
    iso = [x for x in STATIC_CHECKS if x in ISOLATED_STATIC]
    rest = [x for x in STATIC_CHECKS if x not in excl and x not in iso]
    n_rest = n - 1 - len(iso)
    # `n` 太小时**直接失败并说清为什么** —— 静默退回"分不开就合一片"会把上面那两件
    # 事（独占、长尾各占一片）悄悄取消掉, 而调用方（`gate.yml`）是按片号取名字的:
    # 合掉之后 `--shard K/N` 取到的就不是它以为的那一片。
    assert n_rest >= 1, (f"要 {n} 片分不开: 独占的 {len(excl)} 条 + 长尾的 {len(iso)} 条"
                         f"各占一片（共 {1 + len(iso)} 片）, 剩下 {len(rest)} 条还得有地方放"
                         f" —— 至少 {2 + len(iso)} 片")
    buckets: list[list[str]] = [[] for _ in range(n_rest)]
    load = [0.0] * len(buckets)
    # **按耗时从大到小**装进当前最轻的那片: 先放大件, 大小件之间的空隙才填得满。
    for name in sorted(rest, key=lambda x: -SHARD_COST.get(x, SHARD_DEFAULT_S)):
        i = load.index(min(load))
        buckets[i].append(name)
        load[i] += SHARD_COST.get(name, SHARD_DEFAULT_S)
    out = [excl] + [[x] for x in iso] + buckets
    # 守卫: 这必须是一个**划分** —— 一条不漏、一条不重
    flat = [x for b in out for x in b]
    assert len(out) == n, f"切出 {len(out)} 片, 要 {n} 片"
    assert len(flat) == len(set(flat)), f"分片里有重复: {len(flat)} vs {len(set(flat))}"
    assert sorted(flat) == sorted(STATIC_CHECKS), "分片不是 STATIC_CHECKS 的划分"
    assert all(b for b in out), "有分片是空的"
    return out


def _sweep_wsl_tmp() -> None:
    """清掉 WSL `/tmp` 里**旧的**门禁残留 (`/tmp/loment-*`) —— 尽力而为, 出错不报。

    **为什么会有残留**: 各判据的 WSL 临时文件名带**进程号** (并行时同名文件会互相踩,
    见那些模块里的 `_T`), 于是每次跑都留一批新名字, 不再被下次覆写。几轮下来能吃掉
    几十上百 MB 的 tmpfs。

    **为什么只清 60 分钟前的**: 同一个 `/tmp` 是**共享**的, 直接 `rm -f /tmp/loment-*`
    会把**另一台并发在跑的门禁**正在用的文件删掉 (本仓明确考虑过"同机并行多个 agent",
    见 `FUJO_MON_PORT`)。按时间清就没有这个口子。
    """
    try:
        subprocess.run([*_WSLCMD, "bash", "-lc",
                        "find /tmp -maxdepth 1 -name 'loment-*' -mmin +60 -delete "
                        "2>/dev/null || true"],
                       capture_output=True, timeout=120, shell=False)
    except Exception:                                                # noqa: BLE001
        pass


def count_skips(text: str) -> int:
    """数一条检查的输出里"跳过的判据"有几条（`#371`）。

    各套件的写法是 `print("      SKIP: …"); return` —— 在运行器的 `try/except` 里那是
    **正常返回**，于是被记成 PASS、那一行报满。判据只认**行首**是 `SKIP` 的行：
    说明文字里提到 SKIP 的不算。
    """
    return sum(1 for ln in text.splitlines() if ln.lstrip().startswith("SKIP"))


#: 判据条数地板。一行 `套件 条数`；数值 = `len(该套件的 TESTS)`。与环境无关，所以**精确相等**。
ROOT_FLOORS = ".github/gate-floors.txt"
#: 允许跳过的判据。一行 `套件 判据名 # 环境/仓库: 理由`；`*` = 整条套件被跳过。
ROOT_SKIPS = ".github/gate-skips.txt"

_SKIP_LINE_RE = re.compile(r"^\s*SKIP\s+([^:]+):\s*(.*)$")
_PASS_LINE_RE = re.compile(r"^\s*PASS\s+(\S+)\s*$")


def read_budget() -> tuple[dict[str, int], set[tuple[str, str]]]:
    """读两份预算：`(地板, 允许的跳过)`。

    地板：`套件 条数` —— 条数是 `len(TESTS)`，**与环境无关**，所以必须精确相等。
    跳过：`套件 判据名` —— 实际跳过必须是它的**子集**。

    `#` 起头是注释，行尾 `#` 之后也是注释（两条纪律与 `.github/gate-baseline.txt` 同一套）。
    文件不见了**不跳过**：门禁不猜，直接判失败。
    """
    floors: dict[str, int] = {}
    skips: set[tuple[str, str]] = set()
    base = pathlib.Path(__file__).resolve().parent.parent
    for rel, keep in ((ROOT_FLOORS, "floor"), (ROOT_SKIPS, "skip")):
        p = base / rel
        if not p.exists():
            raise FileNotFoundError(f"{rel} 不见了 —— 门禁不猜，直接判失败")
        for ln in p.read_text(encoding="utf-8").splitlines():
            body = ln.split("#", 1)[0].strip()
            if not body:
                continue
            parts = body.split()
            if len(parts) != 2:
                raise ValueError(f"{rel}: 这一行要写成 `套件 值`: {ln!r}")
            suite, val = parts
            if keep == "floor":
                floors[suite] = int(val)
            else:
                skips.add((suite, val))
    return floors, skips


def read_criterion_lines(out: str) -> tuple[list[str], list[str], bool]:
    """从一条套件的输出里读 `(通过的判据, 跳过的判据, 是否整条套件级跳过)`。

    判据名出自 `loment_suite.run` 打的那两行。**整条套件级跳过**（`loment_hosted_test`
    那种循环之前就 `return 0` 的）一行判据都不打，只有一句 `套件: SKIP —— …`，
    所以那一路靠"输出里有 SKIP 而没有任何判据行"来认。
    """
    lines = out.splitlines()
    passed = [m.group(1) for ln in lines if (m := _PASS_LINE_RE.match(ln))]
    skipped = [m.group(1) for ln in lines if (m := _SKIP_LINE_RE.match(ln))]
    level = (not passed and not skipped
             and any(ln.lstrip().startswith("SKIP") for ln in lines))
    return passed, skipped, level


def check_budget(results, floors, skips, *, in_ci: bool) -> list[str]:
    """两道棘轮。返回**问题清单**（非空 = 门禁判失败）。

    1. **地板**：`len(TESTS)` 与签名文件精确相等。少了 = 有人删了判据；多了 = 有人加了
       判据没更新地板。两个方向都红 —— 否则那份文件会说谎。
    2. **跳过**：实际跳过 ⊆ 预算，多出来一条就红。这条挡的正是 `CONTRIBUTING.md` 禁止的
       那种做法：**给一条既有判据加个环境门让它不跑**，或者新写一条永远跳过的判据。
       反过来（预算里有、这次没跳）只在 CI 里 `::notice` —— 本机有没有 clang/WSL 与 runner
       不同，本机看到子集是正常的。

    问题清单里**带上可以直接粘进预算文件的行**：门禁得能教会你怎样让它满意，否则第一次
    落地的代价就是猜。
    """
    bad: list[str] = []
    seen: set[tuple[str, str]] = set()
    for r in results:
        if r.declared:
            want = floors.get(r.name)
            if want is None:
                bad.append(f"{r.name}: 不在 {ROOT_FLOORS} 里（新套件？给它记一行："
                           f"`{r.name} {r.declared}`）")
            elif r.declared != want:
                how = "少了" if r.declared < want else "多了"
                bad.append(f"{r.name}: 判据条数 {how}（{want} -> {r.declared}）—— "
                           f"少了是有人删判据；多了是没更新 {ROOT_FLOORS}")
        if r.level_skip:
            key = (r.name, "*")
            seen.add(key)
            if key not in skips:
                bad.append(f"{r.name}: 整条套件被跳过，而它不在 {ROOT_SKIPS} 里 —— "
                           f"补一行 `{r.name} *  # 环境: …`")
            continue
        # **跳过必须有名字**，否则它对这个预算**完全不可见** —— 那正是本版要堵的形状
        # （一层安全检查看不见的洞）。没搬上 `loment_suite.run` 的套件打的还是
        # `SKIP <原因>` 那种没名字的写法：`count_skips` 数得到，而这里认不出来。
        # 把它们改成 `  SKIP  <判据名>: <原因>`（搬上共享运行器，或者照这个形状改名）。
        unnamed = r.skips - len(r.skipped)
        if unnamed > 0:
            bad.append(f"{r.name}: 打了 {r.skips} 条 SKIP，其中 {unnamed} 条**没有名字** —— "
                       f"它们对预算不可见。跳过要写成 `  SKIP  <判据名>: <原因>`"
                       f"（把那些判据搬上 `loment_suite.run`，或者照这个形状改名）")
        for crit in r.skipped:
            key = (r.name, crit)
            seen.add(key)
            if key not in skips:
                bad.append(f"{r.name} 的 {crit}: 跳过了而没登记 —— 要么让它跑起来，要么补一行 "
                           f"`{r.name} {crit}  # 环境/仓库: 为什么它在这台机器上跑不了`")
    if in_ci:
        # **只对"这一趟真的跑了那个套件、而它没跳"的条目打 notice。** 原先这里是
        # `skips - seen` 直接滤，而 `seen` 只装**这一趟的结果**里的跳过 —— 分片跑的时候，
        # 表里所有**不在这一片**的条目都会被点成"这次没跳"。后果不是噪音而是**反的**：
        # 那句"照 notice 把表削短"会变成"把整张表删光"（实测：合并 `#382` 之后那一轮，
        # 六个分片的 notice 合起来正好覆盖整张 140 条的表）。一条会教人做错事的提示，
        # 与判据说谎是同一类问题。
        ran = {r.name for r in results}
        for suite, crit in sorted(skips - seen):
            if suite not in ran:
                continue          # 这一片根本没跑那个套件 —— 不是"它没跳"
            print(f"::notice::{ROOT_SKIPS} 里的 {suite} {crit} 这次**没跳** —— 修好了就删掉它")
    return bad


def format_line(name: str, ok: bool, tail: str, dt: float, skips: int) -> str:
    """`:: [static] …` 那一行的**纯函数**版本 —— 判据能直接钉它的形状。

    跳过的条数**只在非零时出现**：大多数检查一条都不跳，给它加个 `0 SKIP` 只是噪音。
    """
    mark = f"  {skips} SKIP" if skips else ""
    return f":: [static] {name:14s} {'PASS' if ok else 'FAIL'}  {dt:6.1f}s{mark}  {tail}"


class Result(NamedTuple):
    """一条检查的结果。**前五项的位置与老的 5 元组一致** —— 按索引取的那几处不用改。"""

    name: str
    ok: bool
    tail: str
    dt: float
    skips: int
    passed: tuple[str, ...]      # 真跑过且通过的判据名
    skipped: tuple[str, ...]     # 打了 SKIP 的判据名（运行器会连名字一起打）
    declared: int                # `len(TESTS)`；没有 TESTS 的 driver 脚本是 0
    level_skip: bool             # 整条套件级跳过（循环之前就 return 0 那种）


def _report(r: "Result") -> None:
    """一行结果。**只在一处打** —— 两个路径各打一次会变成打两遍 (上一版就是这么错的)。"""
    print(format_line(r.name, r.ok, r.tail, r.dt, r.skips), flush=True)


def _run_one_check(name: str) -> Result:
    """在**当前进程**里跑一条检查。`-j` 时它是进程池的工作单元 (所以必须在模块级)。"""
    import contextlib
    import io

    buf = io.StringIO()
    t0 = time.perf_counter()
    mod = None
    try:
        mod = __import__(name)
        old_argv, sys.argv = sys.argv, [name]  # 防 argparse 读 ci.py 的 argv
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = mod.main()
        finally:
            sys.argv = old_argv
    except BaseException as e:  # noqa: BLE001  (SystemExit/argparse 也算失败)
        rc = 1
        buf = io.StringIO(f"{type(e).__name__}: {e}")
    dt = time.perf_counter() - t0
    out = buf.getvalue()
    tail = out.strip().splitlines()
    passed, skipped, level = read_criterion_lines(out)
    try:
        declared = len(getattr(mod, "TESTS", None) or ())
    except Exception:                                                # noqa: BLE001
        declared = 0
    # `#371`: 跳过被记成**通过**, 所以那个数得单独数出来报 —— 否则这一行报的是
    # "没抛异常的总数"。CI 是 ubuntu 容器, 里面没有 `wsl`, 于是 104 条判据在 runner 上
    # 一条都不跑而门禁全绿 (最重的是 `loment_elf_test`: 运行时护栏整条在里面)。
    return Result(name, rc == 0, (tail[-1] if tail else ""), dt, count_skips(out),
                  tuple(passed), tuple(skipped), declared, level)


def run_static(only: tuple[str, ...] = (), jobs: int = 1, names: list[str] | None = None):
    """跑各检查脚本的 main()。返回 [(name, ok, tail, dt, skips)], **顺序 = STATIC_CHECKS 顺序**。

    **每条都计时**: 不看这个数就没法谈"门禁太慢"是慢在哪 —— 41 条里往往几条吃掉大半。
    计时结果按耗时排序打出来 (见 `main`), 于是"该优化哪条"是个实测结论而不是猜。

    `only` 非空时只跑名字里含这些子串的那几条 —— 优化门禁时得能单独把一条跑起来看。

    `names` 直接给**这一轮要跑的名字表**（`--shard K/N` 走这条）, 与 `only` 二选一;
    它来自 `shards()` 的某一格, 所以**不需要**在这里再校验覆盖 —— 那里已经断言过
    "分片是 `STATIC_CHECKS` 的一个划分"。

    **`jobs > 1` 时并行跑**。41 条彼此独立, 串行跑纯是浪费; 墙钟时间从"和"变成
    "最慢的那条"。两条前提:
      * 每条自己在**独立进程**里跑, 所以某条的全局状态 (`sys.argv`、模块级缓存) 不会
        串到别条上 —— 这是并行**更安全**的一面, 不只是更快;
      * 但 WSL 的 `/tmp` 与仓库是**共享**的, 所以 (a) 各模块的 WSL 临时文件名都带了
        进程号 (见那些模块里的 `_T`), (b) `EXCLUSIVE_STATIC` 里那几条独占着跑。
    """
    if names is None:
        names = [n for n in STATIC_CHECKS if not only or any(o in n for o in only)]
    if only:
        print(f"fujoci: --only-static 只跑 {len(names)}/{len(STATIC_CHECKS)} 条", flush=True)

    if jobs <= 1 or len(names) <= 1:
        out = []
        for n in names:
            r = _run_one_check(n)
            _report(r)
            out.append(r)
        return out

    import concurrent.futures as cf

    res: dict[str, tuple] = {}
    excl = [n for n in names if any(e in n for e in EXCLUSIVE_STATIC)]
    par = [n for n in names if n not in excl]
    if excl:
        print(f"fujoci: 独占跑的 {len(excl)} 条: {' '.join(excl)}", flush=True)
        for n in excl:
            res[n] = _run_one_check(n)
            _report(res[n])
    print(f"fujoci: 并行 {len(par)} 条, {jobs} 路", flush=True)
    with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_run_one_check, n): n for n in par}
        for f in cf.as_completed(futs):
            r = f.result()
            res[r[0]] = r
            _report(r)              # **谁先完先打** —— 长跑时看得见进度
    return [res[n] for n in names]  # 返回值仍按 STATIC_CHECKS 顺序, 便于比对


def kill_qemu():
    subprocess.run(["taskkill", "/F", "/IM", "qemu-system-x86_64.exe"],
                   capture_output=True)


def run_one(kernel, rel, needle, timeout_s):
    initrd = os.path.join(ROOT, rel)
    if not os.path.exists(initrd):
        return ("MISS", f"initrd not found: {initrd}", "")
    kill_qemu()
    time.sleep(0.8)
    tmpd = tempfile.mkdtemp(prefix="fujoci-")
    log = os.path.join(tmpd, "qemu.log")
    p = subprocess.Popen([
        shutil.which("qemu-system-x86_64"), "-m", "256M",
        "-kernel", kernel, "-initrd", initrd,
        "-serial", f"file:{log}",
        "-serial", f"tcp:127.0.0.1:{SER_PORT},server=on,wait=off",
        "-monitor", f"telnet:127.0.0.1:{MON_PORT},server,nowait",
        "-display", "none", "-no-reboot",
    ])
    time.sleep(BOOT_S)
    try:
        s = socket.create_connection(("127.0.0.1", MON_PORT), timeout=3)
        f = s.makefile("w")
        for k in KEYS:
            f.write(f"sendkey {k}\n")
            f.flush()
            time.sleep(KEY_HZ)
        s.close()
    except OSError:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if p.poll() is not None:
                break
        except Exception:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    try:
        p.kill()
    except Exception:
        pass
    log_txt = ""
    if os.path.exists(log):
        log_txt = open(log, errors="replace").read()
    if needle in log_txt:
        return ("PASS", "", log_txt)
    tail = "\n".join(log_txt.splitlines()[-6:])
    return ("FAIL", f"needle '{needle}' not found", tail)


def main():
    ap = argparse.ArgumentParser(prog="fujoci")
    ap.add_argument("-k", "--kernel", default=KERNEL)
    ap.add_argument("--only", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--static-only", action="store_true",
                    help="只跑 L0 静态门禁 (lomc_test / lom_audit / fuai_contract), 不启 QEMU")
    ap.add_argument("--only-static", action="append", default=[], metavar="SUBSTR",
                    help="只跑静态门禁里名字含 SUBSTR 的那几条 (可重复) —— 优化门禁用")
    ap.add_argument("--shard", metavar="K/N", default=None,
                    help="只跑第 K 片（共 N 片, K 从 1 数）—— 把整道门禁摊到 N 个并行 job 上, "
                         "见 shards()。`K/N` 里 N 必须等于 `--shard-plan` 给的那个 N")
    ap.add_argument("--shard-plan", type=int, metavar="N", default=None,
                    help="只打印 N 片的分法（每片一行: 片号 + 条数 + 名字）, 不跑任何判据")
    default_jobs = max(1, min(8, (os.cpu_count() or 4)))
    ap.add_argument("-j", "--jobs", type=int, default=default_jobs,
                    help=f"静态门禁并行度 (默认 {default_jobs}; 1 = 串行同进程)")
    a = ap.parse_args()

    if a.shard_plan is not None:
        for i, b in enumerate(shards(a.shard_plan)):
            print(f"shard {i + 1}/{a.shard_plan} ({len(b)} 条): {' '.join(b)}")
        return 0

    # `--shard K/N` -> 这一片的名字表。**必须经过 `shards(N)`** —— 分片的划分与它的
    # 覆盖守卫都只在那一个地方（手写名字表会静默漏掉新判据, 见 `shards` 的说明）。
    picked: list[str] | None = None
    if a.shard:
        k_s, _, n_s = a.shard.partition("/")
        try:
            k, n = int(k_s), int(n_s)
        except ValueError:
            ap.error(f"--shard 要写成 K/N（如 2/6）, 得到 {a.shard!r}")
        allp = shards(n)
        if not 1 <= k <= len(allp):
            ap.error(f"--shard {a.shard}: K 要在 1..{len(allp)} 之间")
        picked = allp[k - 1]
        print(f"fujoci: 分片 {k}/{n} —— {len(picked)} 条: {' '.join(picked)}", flush=True)

    if a.static_only:
        _sweep_wsl_tmp()
    wall0 = time.perf_counter()
    static = run_static(tuple(a.only_static), a.jobs, picked)   # 每条结果它自己打 (见 _report)
    wall = time.perf_counter() - wall0
    # **耗时榜**: 优化门禁只能优化"实测最慢的那几条"。41 条里往往 5 条吃掉八成 ——
    # 没有这张榜，"哪条慢"就永远是个印象 (而印象通常错)。
    # **"总和"与"墙钟"分开报**: 并行之后这两个数不是一回事, 而"还能不能再快"看的是墙钟
    # 与**最慢那条**的差 —— 差越小, 说明并行已经榨干, 该去优化那条本身了。
    slow_total = sum(r[3] for r in static)
    slowest = max((r[3] for r in static), default=0.0)
    print("-" * 60)
    print(f"fujoci: 静态门禁 墙钟 {wall:.1f}s / 各条合计 {slow_total:.1f}s "
          f"/ 最慢一条 {slowest:.1f}s（-j {a.jobs}）")
    print("  最慢的 8 条：")
    for r in sorted(static, key=lambda x: -x.dt)[:8]:
        pct = (r.dt / slow_total * 100) if slow_total else 0
        print(f"    {r.name:24s} {r.dt:7.1f}s  {pct:4.1f}%")
    # **预算要在"有红就中止"那一步之前查。** 原先它在下面那个 `return 1` 之后，于是
    # **最需要它的时候它不跑**：任何一条套件红（哪怕那条红早就登记在基线里、根本不算新红）
    # 都会从这里中止，把预算那整段跳过 —— 跳过表与地板在那一次跑里**完全没有约束力**。
    # 2026-10-15 实测到的形状：第 5 片有 109 条判据跳过，一条都没量到，就因为该片有一条
    # 基线内的红。这与本版要堵的是同一个洞（"没跑"却在某条路径上不算账）。
    budget_bad: list[str] = []
    if a.static_only:
        print("-" * 60)
        print(f"fujoci: 静态门禁 {sum(1 for r in static if r.ok)}/{len(static)} PASS")
        # `#371`: **"PASS" 不等于"跑了"**。跳过被运行器记成通过, 所以这里单独报一次总数 ——
        # 不报的话, 那一行读起来就是覆盖面, 而 runner 上有一整族判据根本不执行。
        skipped = sum(r.skips for r in static)
        if skipped:
            print(f"fujoci: 其中 {skipped} 条判据**没跑**（环境不满足, 见各行的 `N SKIP`）"
                  f"—— 这一行才是覆盖面")
        # **"没跑"本身也要有代价**：签入的预算说清哪些跳过是接受的（`.github/gate-skips.txt`）
        # 与每个套件该有多少条判据（`.github/gate-floors.txt`）。多出来的跳过、被删掉的判据，
        # 在这一步判失败 —— 否则"把判据改得永远不跑"与"把判据删掉"都是能过的。
        floors, budget_skips = read_budget()
        budget_bad = check_budget(static, floors, budget_skips,
                                  in_ci=bool(os.environ.get("GITHUB_ACTIONS")))
        if budget_bad:
            print("-" * 60)
            print(f"fujoci: 判据预算对不上 {len(budget_bad)} 条：")
            for b in budget_bad:
                print(f"  - {b}")
    if any(not r.ok for r in static):
        print("-" * 60)
        print("fujoci: 静态门禁失败 — 中止 (接口漂移优先于运行时回归)")
        return 1
    if budget_bad:
        return 1
    if a.static_only:
        return 0

    cases = []
    for name, rel, needle, to in COMPAT:
        cases.append((name, rel, needle, to))
    for name, rel, needle in MILESTONES:
        cases.append((name, rel, needle, a.timeout))

    results = []
    for i, (name, rel, needle, to) in enumerate(cases):
        if a.only is not None and i != a.only:
            continue
        print(f":: [{i:02d}] {name:16s} ...", flush=True)
        st, err, logtxt = run_one(a.kernel, rel, needle, to)
        print(f":: [{i:02d}] {name:16s} {st} {err}", flush=True)
        if st != "PASS" and logtxt:
            print(logtxt)
        results.append({"case": name, "rel": rel, "status": st, "needle": needle})
    ok = sum(1 for r in results if r["status"] == "PASS")
    print("-" * 60)
    print(f"fujoci: {ok}/{len(results)} PASS")
    if a.json:
        json.dump(results, safe_open(a.json, "w"), indent=1)
    return 0 if ok == len(results) and len(results) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
