#!/usr/bin/env python3
# loment_caps.py — 自举编译器那一侧**固定容量的盘点**（`FujoJTOP/loment#370`、`#345`、`#770`）。
#
# 口径（机械的，不靠记性）：
#
#   * **容量（具名常量）** = 一个 `const NAME: u32 = <数字>`，且它在某一处**被当作上界**
#     用过 —— 也就是出现在一条**序关系**比较里（`>` `>=` `<` `<=`），而不是只做相等比较
#     或只当一个码值传出去。`E_DEEP = 23` 那种是**诊断码**（它只会被 `==` 或当实参），
#     不算容量；`SLOT = 524288`、`TB_LBL_MAX = 32768` 那种才把"能装多少"写成了一个数。
#   * **容量（定长数组）** = 一个声明里的 `[T; N]`（`let`/`var`/`const`/`fn` 开头那几种）。
#     长度是类型的一部分，越界不写穿，但**丢**了多少没有话说。
#   * **容量（字面量上界）** = 一条 `<` / `<=` 比较右边的数字（`>= 8`），且左边那个变量
#     在随后 10 行里**被当作 store 的下标**用过（`store8/32/64` 的第二个实参）。
#     这条规则是从 `#770` 点名的三处反推出来的（`driver.lomt:865` 的 `j < 200`、
#     `lomfmt.lomt:497` 的 `n < 16`、`lomc.lomt:501` 的 `o + i < 4000`），
#     同时把"字符分类"（`c < 32`、`cp < 128`）与"算法常量"（SHA-256 的 64/32/56）
#     之外的大部分噪音挡在外面；剩下的由 `EXPLAINED` 逐条写清。
#   * **有话 / 沉默** = 那个上界被突破时，**同一处守卫分支**里有没有一句说得出话的东西。
#     会说话的形状逐个列在 `SPEAKS` 里（都是**测试得到**的，不是猜的）：
#     `fail(`、`sym_pub(`、`err_`、以及带 `E0`／`太大`／`非法` 的字面量。
#     一条都没有 ⇒ 走过去了 ⇒ 越界就是**下标写穿**（自举侧的症状是段错误，`docs/345`）。
#     **已知局限**（`#770` 的第 4 条）：这是**同文件 ±8 行**的筛子 —— 经 helper 报的守卫、
#     或写在 8 行之外的守卫会被判成沉默；反过来，邻近分支里的一句 `fail(` 也可能被算到
#     这张表头上。这类误判用 `EXPLAINED` 逐条写明来路，而不是把筛子放宽。
#
# **扫多大**（`#770`）：`loment/**` 整个语言树，**递归**，由 `loment_src.GLOBS` 里那条
# `loment/**` 推出来 —— 新加一个目录**自动**进口径，不靠手抄。原先只扫
# `loment/selfhost/*.lomt` 与 `loment/tools/*.lomt` 两层，于是：
#   * `loment/selfhost/grammar_decl/`、`neg/`、`neg_across/` 从来没被打开；
#   * `loment/lib/`（随包发的库代码，11 个文件）整个不在口径里；
#   * `loment/examples/`、`loment/ct/`、`loment/comefor/` 也不在。
# 现在只排除 `loment/build/`（生成物：`fuai_syscalls.lomt` 由判据写出、`channel_probe_*`
# 是临时件）与下面那两个生成的数据文件 —— 排除要写清理由，见 `EXCLUDE_TREES` / `EXCLUDE`。
#
# **已知未覆盖**（写在这里而不是假装没有）：`const OFF: u32 = N` 这种"保留区落点"
# （`loment/lib/json.lomt` 的 `J_NUMTMP`、`loment/tools/lomfmt.lomt` 的 `OFF_OUT` …）
# 被用作 memory 下标，但既不是上界比较、也没有 `*`/alloc —— 本口径**仍不**把它们算成
# 容量。它们回答的是"从这里往后预留多少"，与"一张表能装多少"相邻但不同；要收它们得
# 先给"保留区"一个独立的类，否则 arena 的布局偏移（`S_VL = 4592` 这种"某个字段在哪"
# 的常量）会一起混进容量数里，那个数就不再回答 `#345` 问的问题。`loment/lib` 本身
# 已经进了扫描面（`J_NODE_STRIDE` / `PROC_WS` 就在口径里），这一条只是那一类形状的边界。
#
# 这份清单**是算出来的**，所以它不会漂：改一个容量、加一个检查，下一轮的行数就变 ——
# `loment_caps_test.py` 把三类的数与沉默的名单钉住（具名常量逐名钉；字面量/数组按
# `(文件, 名字, 条数)` 钉）。
#
# 用法: python tools/loment_caps.py            # 三类的表格 + 汇总
#       python tools/loment_caps.py --silent   # 只列具名常量里沉默的那一份（给判据读）
#       python tools/loment_caps.py --literals # 只列字面量上界
#       python tools/loment_caps.py --arrays   # 只列定长数组

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_src  # noqa: E402  (语言源码树的唯一定义，见 `SOURCE_GLOB`)

#: 扫哪棵树 —— **从 `loment_src.GLOBS` 的那条 `loment/**` 推出来**（`#770`）。
#: 新加一个 `.lomt` 目录自动被收进来，不需要改这里的名单。
SOURCE_GLOB = next(g for g in loment_src.GLOBS if g == "loment/**")

#: 不收的子树（写明为什么 —— 排除必须可核对）：
#:   * `loment/build/` —— 构建产物 / 生成物：`fuai_syscalls.lomt` 由 `loment_syscalls.emit()`
#:     写，`channel_probe_*.lomt` 是判据的临时件。它们的常量是**产物**，不是源码的容量。
EXCLUDE_TREES = ("loment/build/",)

#: **排除生成的数据文件**：它们的常量是表项（步长/条目数），不是"编译器自己有多少内存"。
#: 混进来会把数抬高一截，而这个数是要拿来说事的。
EXCLUDE = ("loment/tools/surface_data.lomt", "loment/tools/win_shim_data.lomt")

_CONST = re.compile(r"^const\s+([A-Z][A-Z0-9_]*)\s*:\s*u32\s*=\s*(\d+)\s*;", re.M)
#: 序关系比较：容量出现在任一侧。`==`/`!=` **不算** —— 那是码值，不是上界。
#: **移位不算**（2026-10-16 修）：`<<` / `>>` 里含 `<` / `>`，于是 `TY_K_NAMED << TY_KIND_SHIFT`
#: 被当成了上界比较 —— 一条**纯属正则不够严**的假阳性（`#345` 的盘点要拿去说事，不能混这个）。
_BOUND = r"(?<![<>=])(?:>=|<=|>|<)(?![<>=])"

#: 第二类：字面量上界。`<` / `<=` 右边的数字，且左边的变量在 `LITERAL_WINDOW` 行内
#: 被当作 `store*` 的**下标**（第二个实参）用过。
_LITERAL_BOUND = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:<|<=)\s*(\d+)\b")
_LITERAL_MIN = 8
LITERAL_WINDOW = 10

#: 第三类：定长数组声明 `[T; N]`（只认语句开头的 `let`/`var`/`const`/`fn` ——
#: 否则 `surface_data.lomt` 里那些**说明文字**里的 `[u32; 3]` 会被当成声明）。
_ARRAY_DECL = re.compile(r":\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*;\s*(\d+)\s*\]")
_ARRAY_STMT = re.compile(r"^(?:let|var|const|fn)\b")

#: 说得出话的几种形状（自举侧那几棵树里实际用到的，见文件头）。
#:
#: `loment: ` 是 2026-10-16 加的（`FujoJTOP/loment#8`）：那一轮把面向用户的失败消息
#: 从中文改成 **`loment: ` 开头的纯 ASCII 英文**。原先认得出话靠的是 `超`／`太大` 这类
#: **中文词**，改完就一条都认不出来了 —— 而守卫**照旧在报**（`wstr(2, "loment: …")`
#: 之后 `die(1)`）。加这一条不是放宽判据，是**把新形状补进词汇表**；实测它只让那三条
#: 恢复（`DIAGB_OFF` / `MAX_ADDIN` / `MAX_USE`），沉默总数不动。
#: `failp(` 是 2026-10-17 补的：`fail` 的变体（消息 + 一段字节），同样走
#: `loment: …` + `die(1)`。少了它，新写的"读满槽"那条守卫（用 `failp` 报路径）会被
#: 判成沉默 —— 那是词汇表不全，不是守卫不在。
#: `set_err(` / `wr_err(` 是 2026-10-19 补的（`#774`）：`interp.lomt` 的失败路径是
#: **先记一个错误码**（`set_err(st, E_OOM)`），再由入口把它写成 `ERR oom`（`wr_err`）。
#: 词汇表原先只有 `err_`（前缀），`set_err` 这种**把 err 放在词尾**的形状一条都认不出 ——
#: 于是 7 处**确实会说话**的守卫被算成了沉默。补的是**形状**，不是放宽判据。
SPEAKS = ("fail(", "failp(", "sym_pub(", "err_", "set_err(", "wr_err(",
          "E0", "loment: ", "太大", "非法", "超", "越界")

#: 守卫分支往后看几行算"同一处"。
LOOKAHEAD = 8

#: **这一条判据看不见、但确实不是缺口**的那些 —— 逐条写清为什么，因为"排除"必须可核对
#: （`docs/258` §2：盘点是**筛子**，不是真相；筛掉的东西要留痕）。
#: 键是 `(文件, 常量名)`；第二类的名字是 `literal:<数字>` / `array:[T;N]`。
#: 两类，键是 `(文件, 常量名)`：
#:   * **码值/标签**：它被 `* 256` 这种**拼进一个码**，或被拿来判一个码的**取值范围**。
#:     文件头那句"码值不算容量"就是它，只是正则看不出来
#:     （`TY_K_NAMED` 原先也在这里 —— 2026-10-16 修掉移位的 `<` 之后它**整个**退出了
#:     口径，连"沉默"都算不上：那正是修这条正则的意义）；
#:   * **越界由调用方报**：守卫在 A 文件里 `return 0`，说那句话的人在 B 文件里 ——
#:     单文件 ±8 行扫不到，但用户是**听得到**的。
#:   * **第二类的局部常量**：字面量上界 / 定长数组是**局部**的（拷贝到定长目的地、
#:     定宽编码、算法常量），到界即停、不写穿；哪些是"有意截断"逐条写在下面。
EXPLAINED: dict[tuple[str, str], str] = {
    ("loment/selfhost/checker.lomt", "K_FN"):
        "kind 标签：`K_FN * 256 + argc` 是把它**拼进一个码**，不是「能装多少」",
    ("loment/selfhost/checker.lomt", "K_INT_MIN"):
        "整型码的**取值范围**：`c >= K_INT_MIN && c <= K_INT_MAX` 判的是码，不是计数",
    ("loment/selfhost/checker.lomt", "K_INT_MAX"):
        "整型码的**取值范围**：同 `K_INT_MIN`（判的是码，不是计数）",
    ("loment/selfhost/checker.lomt", "CHK_ENV_MAX"):
        "写满即停（不写穿）；>1536 由发射阶段的 `PT_EMAX` 点名拒 —— 检查器没有输出通道",
    ("loment/selfhost/codegen.lomt", "CG_MAXFN"):
        "越界**由调用方报**：守卫这里 `return 0`，驱动器 `fail(\"…codegen 表容量…\")` 说得出来",
    ("loment/selfhost/codegen.lomt", "CG_MAXENUM"):
        "越界**由调用方报**：同 `CG_MAXFN`（一句话点名两张表）",
    ("loment/tools/lomenterr.lomt", "MSG_CAP"):
        "**检测点，不是写入点**（`#485`）：`if mlen >= MSG_CAP` 判的是「这条消息有没有"
        "被拷满」，判到了就印 `truncated` / 写 `\"truncated\": true`。越界在这里不是"
        "写穿，而是这条判据要回答的问题",
    # ---- `#774`（2026-10-19）：自举编译器剩下的那些"越界由调用方报"或"步长不是容量"。
    # 每一条都指着**真实存在的失败消息**（行号会漂，所以引的是消息原文）——
    # 用来核对的是"那句话还在不在"，不是"这一行还是不是那个数"。
    ("loment/selfhost/comefor.lomt", "MAX_DIALECT"):
        "越界**由调用方报**：`expand_comefor` 返回 4，驱动器 `comefor_fail` 打印 "
        "`too many comefor dialects (limit is 64)` 并 `die(1)`",
    ("loment/selfhost/potato.lomt", "PT_L0CAP"):
        "越界**由调用方报**：`pt_lay_check` 返回 1，`potato_emit` 打印 "
        "`one of the L0 layouts ... cannot be parsed`（点名拒，不静默少几条 `layouts`）",
    ("loment/selfhost/potato.lomt", "PT_FMAX"):
        "越界**由调用方报**：本笔给它补了守卫（第 17 个字段起 `pt_l0_rec` 返回低 32 位 0），"
        "`pt_lay_check` 于是返回 1，同 `PT_L0CAP` 那句点名消息（消息里写了 16 字段）",
    ("loment/selfhost/potato.lomt", "PT_GLRMX"):
        "越界**由调用方报**：本笔给它补了守卫（`pt_gl_l2ranges` 返回 `PT_GLRMX + 1`，"
        "经 `pt_gl_fn` / `pt_gl_all` 把高位置 1），`potato_emit` 打印 "
        "`more than 32 loop bodies that carry an allocation`。原先静默停在 32，"
        "把剩下的分配算成 L3（与参考实现分叉且两边都不报错）",
    ("loment/selfhost/potato.lomt", "PT_TMAX"):
        "越界**由调用方报**：`pt_tref_add` 返回 0 → `pt_collect_at` 返回 0 → "
        "`potato_emit` 打印 `the type string pool is full (128 entries of 64 bytes)`",
    ("loment/selfhost/potato.lomt", "PT_TSTRIDE"):
        "**步长/单条长度**，不是条数：`len + 8 >= PT_TSTRIDE` 挡的是**一条类型串太长**，"
        "走的是和 `PT_TMAX` 同一条拒绝（`the type string pool is full (128 entries of "
        "64 bytes) -- raise PT_TMAX / PT_TSTRIDE`）",
    ("loment/selfhost/potato.lomt", "PT_EMAX"):
        "越界**由调用方报**：本笔把 `pt_env_put` 的静默 `if` 改成返回 1，"
        "`pt_mono_body` / `pt_mono_one` 返回 3，`potato_emit` 打印 "
        "`more than 1536 locals or parameters in one function (PT_EMAX)`",
    ("loment/selfhost/potato.lomt", "PT_MAX"):
        "越界**由调用方报**：`pt_inst_add` 返回 `PT_MAX + 1` → `pt_mono_call` 返回 1 → "
        "`potato_emit` 打印 `the shape of a generic call site is outside this section`"
        "（实例表满也落在这一句；拒发，不静默丢实例）",
    ("loment/selfhost/potato.lomt", "PT_AMAX"):
        "越界**由调用方报**：`narg > PT_AMAX || ntp > PT_AMAX` → `pt_mono_call` 返回 1 → "
        "同 `PT_MAX` 那句 `the shape of a generic call site is outside this section`",
    ("loment/selfhost/potato.lomt", "PT_GMAX"):
        "越界**由调用方报**：`pt_gfn_scan` 返回 1 → `potato_emit` 打印 "
        "`more generic function declarations than this section takes`",
    ("loment/selfhost/lexer.lomt", "MAX_LANG"):
        "**有守卫**（`dn < MAX_LANG`，不写穿）：它静默**丢**的是第 17 条语言声明 —— "
        "那是 (a) 里「丢」那一半，**本笔没做**。留着的理由写在这里而不是抹掉：参考实现"
        "那份 `ext_langs` 是**无界 list**，所以两边在「>16 门语言声明」这一个输入上会分叉，"
        "而它们**各自都说得通** —— 修它要先给语言定一条「一个单元最多声明几门语言」的规则，"
        "那是 `docs/258` §4 记着的那一件",
    # ---- 2026-10-19（`#770`）扩口径后**新扫进来**的具名常量 ----
    ("loment/lib/json.lomt", "J_NODE_STRIDE"):
        "DOM 每个节点的**步长**（6 格 u32 = 24 B）—— 它是布局常量，不是上界；"
        "越界由 arena 的大小（`j_arena_bytes()`）决定",
    ("loment/lib/proc.lomt", "PROC_WS"):
        "工作区 4096 B 的**内层兜底**：`pc_len` / `pc_cstr` 到界即停、不写穿；"
        "`proc_sh` 在 `PROC_CMD_MAX` 处先把过长的命令行拒掉（那里说得出来）",
    ("loment/examples/gc_heap/main.lomt", "NODE"):
        "示例程序自身的节点布局（16 B）；不是编译器或工具的固定表",
    ("loment/examples/lumtui_demo.lomt", "W"):
        "示例程序的设计画布宽度（96），由示例自己决定；不是编译器或工具的固定表",
    ("loment/examples/lumtui_demo.lomt", "H"):
        "示例程序的设计画布高度（36），同 `W`",
    ("loment/examples/native.lomt", "SCALE"):
        "示例程序的缩放系数（3），只参与尺寸计算；不是编译器或工具的固定表",
}

#: 字面量上界 / 定长数组的逐条来路。键是 `(文件, literal:<N>)` / `(文件, array:[T;N])`。
#: 口径与具名常量一致：筛子判沉默，而这里写清"为什么它不是（新的）写穿点"。
_LIT_SCRATCH_4000 = (
    "定长 4000 B scratch：路径/文本拷进暂存，到界即停、**不写穿**；被截断的路径在"
    "随后的 open/exec/stat 上失败，用户听得到")
_LIT_DIREnt_200 = (
    "目录项名字拷进 200 B 暂存：到界即停、不写穿；更长的名字在后续 open 时找不到文件"
    "（随包产物的名字都短于此，见 `loment_release.GLOBS`）")
_LIT_FIXED_WIDTH_8 = "定宽编码/字段数（8 字节小端由 8 次 `store8` 写出）；不随输入增长"
_LIT_DEMO = "示例/语料里的定长块（语言面演示）；不是编译器或工具的固定表"

_LITERAL_EXPLAINED: dict[tuple[str, str], str] = {}
_ARRAY_EXPLAINED: dict[tuple[str, str], str] = {}


def _add(kind: str, entries: dict[tuple[str, str], str], *pairs: tuple[str, str, str]) -> None:
    for f, name, why in pairs:
        entries[(f, f"{kind}:{name}")] = why


# ---- 字面量上界（`#770`）----
for _f in ("loment/selfhost/driver.lomt", "loment/tools/lomadmit.lomt",
           "loment/tools/lomc.lomt", "loment/tools/lomcapasserts.lomt",
           "loment/tools/lomdoc.lomt", "loment/tools/lomenterr.lomt",
           "loment/tools/lomextblock.lomt", "loment/tools/lomfmt.lomt",
           "loment/tools/lompkg.lomt", "loment/tools/lomsyscalls.lomt",
           "loment/tools/lomvimgrammar.lomt", "loment/tools/lomzwframe.lomt",
           "loment/tools/lsp.lomt"):
    _LITERAL_EXPLAINED[(_f, "literal:4000")] = _LIT_SCRATCH_4000

_add("literal", _LITERAL_EXPLAINED,
     ("loment/selfhost/driver.lomt", "200", _LIT_DIREnt_200),
     ("loment/examples/fuc_node.lomt", "64", _LIT_DEMO),
     ("loment/examples/gc_heap/main.lomt", "8", _LIT_FIXED_WIDTH_8),
     ("loment/lib/proc.lomt", "8", _LIT_FIXED_WIDTH_8),
     ("loment/lib/lumtui_doc.lomt", "64", "文档头初始化 64 B 固定块；不是输入驱动的容量"),
     ("loment/lib/sha256.lomt", "8", "SHA-256 算法常量（块 64 B / 字 16 个）；定长算法"),
     ("loment/lib/sha256.lomt", "16", "SHA-256 算法常量（块 64 B / 字 16 个）；定长算法"),
     ("loment/lib/sha256.lomt", "32", "SHA-256 算法常量（摘要 32 B）；定长算法"),
     ("loment/lib/sha256.lomt", "56", "SHA-256 算法常量（长度字段 56 B）；定长算法"),
     ("loment/lib/sha256.lomt", "64", "SHA-256 算法常量（块 64 B）；定长算法"),
     ("loment/selfhost/checker.lomt", "63",
      "moved / freed 表的容量（各 63 条）：到界即停、不写穿；第 64 条静默不记"
      "（与参考实现同口径 —— 这是 `#770` 让它可见、而不是本笔改动的那一类）"),
     ("loment/selfhost/codegen.lomt", "8",
      "自举发射器的定长槽（结构体字段/枚举变体 8 格）：到界即停、不写穿；"
      "超出部分静默截断（与参考实现同口径）"),
     ("loment/selfhost/codegen.lomt", "9",
      "自举发射器的定长槽（方法调用实参 9 格）：到界即停、不写穿"),
     ("loment/selfhost/codegen.lomt", "16",
      "自举发射器的定长槽（形参 16 格）：到界即停、不写穿"),
     ("loment/selfhost/codegen.lomt", "20",
      "自举发射器的定长槽（形参/实参 20 格；`#265` 量过）：到界即停、不写穿"),
     ("loment/selfhost/codegen.lomt", "64",
      "自举发射器的定长槽（结构体/能力域表 64 格）：到界即停、不写穿"),
     ("loment/selfhost/interp.lomt", "20", "解释器 token 文本槽 20 B（词法单元定长）"),
     ("loment/selfhost/lexer.lomt", "256",
      "扩展前缀的暂存上界 256 B；超过的输入被截断（到界即停、不写穿）"),
     ("loment/lspe/lspe.lomt", "14",
      "LSPE 引擎的 14 个 u32 计数槽（固定形状，`PT_L3CNT`）；引擎先清零这十四个槽再逐项累加"),
     ("loment/selfhost/potato.lomt", "20", "函数表项的 20 B 定长（排序时逐字节交换）"),
     ("loment/tools/lomclicheck.lomt", "400",
      "字面量文件名的暂存 400 B；到界即停，随后的 open 会报错"),
     ("loment/tools/lomelf.lomt", "8", "ELF/PE 的定宽字段（8 字节）"),
     ("loment/tools/lomelf.lomt", "16", "ELF64 头部的定宽字段（16 字节）"),
     ("loment/tools/lomenterr.lomt", "60", "列宽/片段上限 60 B（截断用于显示）"),
     ("loment/tools/lomenterr.lomt", "1024", "errconfig 路径的暂存 1024 B；到界即停"),
     ("loment/tools/lomenterr.lomt", "2000", "errconfig 追加段的暂存 2000 B；到界即停"),
     ("loment/tools/lomfmt.lomt", "16",
      "块类型栈 16 格；第 17 层块的类型被**静默丢弃**（不写穿，但格式化结果少一层）"
      "—— 这是 `#770` 记着的已知缺口，本盘点先让它可见"),
     ("loment/tools/lomlib.lomt", "64", "缩进缓冲 64 空格；超过的缩进在显示层被截断"),
     ("loment/tools/lompotato.lomt", "124", "名字槽 124 B（表项定长）"),
     ("loment/tools/lompotc.lomt", "400", "类型文本缓冲 400 B；到界即停、不写穿"),
     ("loment/tools/lompotc.lomt", "500", "标识符缓冲 500 B；到界即停、不写穿"),
     ("loment/tools/lomzwframe.lomt", "8", "两个 u32 的定宽字段（4 + 4 字节）"),
     ("loment/tools/lsp.lomt", "60", "显示层列宽 60 B（截断是有意的）"),
     ("loment/tools/lsp.lomt", "200", "显示层消息片段 200 B（截断是有意的）"),
     ("loment/tools/lsp.lomt", "400", "显示层消息片段 400 B（截断是有意的）"),
     )

# ---- 定长数组（`#770`）----
_add("array", _ARRAY_EXPLAINED,
     ("loment/selfhost/codegen.lomt", "[u32;16]",
      "IR 发射的常量缓冲 16 格；长度是类型的一部分，字面量下标由编译器检查"),
     ("loment/selfhost/ir_index.lomt", "[u64;4]", "自举链自检程序的定长数组（4 格）"),
     ("loment/selfhost/lexer.lomt", "[u32;16]",
      "扩展前缀表 16 格；有 `dn < MAX_LANG` 守卫（不写穿）"),
     ("loment/selfhost/pos/ok.lomt", "[u32;4]", "正例语料里的定长数组（语言面演示）"),
     ("loment/tools/lomeol.lomt", "[u32;32]", "tokens 缓冲 32 格"),
     ("loment/ct/loops.lomt", "[u64;8]", "解释器语料里的定长数组（语言面演示）"),
     ("loment/nltrans/Surface.lomt", "[i64;3]", "自然语言语法语料里的定长数组（语言面演示）"),
     ("loment/examples/demo.lomt", "[u32;4]", _LIT_DEMO),
     ("loment/examples/native_agg.lomt", "[u32;4]", _LIT_DEMO),
     ("loment/examples/native_mut.lomt", "[u32;4]", _LIT_DEMO),
     ("loment/examples/native_mut.lomt", "[u32;2]", _LIT_DEMO),
     ("loment/examples/native_slice.lomt", "[u32;4]", _LIT_DEMO),
     ("loment/examples/native_slice.lomt", "[u32;3]", _LIT_DEMO),
     ("loment/examples/tour.lomt", "[u32;3]", _LIT_DEMO),
     )

EXPLAINED.update(_LITERAL_EXPLAINED)
EXPLAINED.update(_ARRAY_EXPLAINED)

#: **编译器前端** —— "用户一份源文件能推动的那些容量"。`#345` 采纳的 (a) 就是先把这一档
#: 做成 0 静默：它正好是 `#343` 那一类崩溃（嵌套块 / 括号 / 形参表 / 缺逗号）住的地方。
FRONTEND = ("driver.lomt", "lexer.lomt", "checker.lomt", "codegen.lomt", "parser.lomt")


def _sources() -> list[Path]:
    """语言树里所有该扫的 `.lomt` —— **递归**，从 `SOURCE_GLOB` 展开（`#770`）。"""
    out: list[Path] = []
    for p in sorted(ROOT.glob(f"{SOURCE_GLOB}/*.lomt")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(rel.startswith(t) for t in EXCLUDE_TREES):
            continue
        if rel in EXCLUDE:
            continue
        out.append(p)
    return out


def _store_index_args(line: str) -> list[str]:
    """一行里每个 `store8/32/64(…)` 的**第二个实参**（下标），按括号深度切。

    只认下标，不认值 —— `store8(out, o, cp as u8)` 里的 `cp` 是**写的值**，不是容量；
    把它算进去会让 `cp < 128` 这种字符分类变成"容量"（`#770` 的规则就是从三个实例反推的，
    先被这条排除掉了一大半噪音）。
    """
    out: list[str] = []
    for m in re.finditer(r"store(?:8|32|64)\s*\(", line):
        i, depth, cur, args = m.end(), 1, "", []
        while i < len(line) and depth > 0:
            ch = line[i]
            if ch == "(":
                depth += 1
                cur += ch
            elif ch == ")":
                depth -= 1
                if depth > 0:
                    cur += ch
            elif ch == "," and depth == 1:
                args.append(cur)
                cur = ""
            else:
                cur += ch
            i += 1
        args.append(cur)
        if len(args) >= 2:
            out.append(args[1])
    return out


def scan() -> list[dict]:
    """**具名常量**那一类：回每一处 `{file, line, name, value, kind, speaks, explained}`。

    同一常量在几处用到就是几条。`explained` 非空 = 筛子判它沉默、而 `EXPLAINED` 已写明
    它不是缺口 —— 判据看的是**两者都为假**的那一份（见文件头与 `docs/258` §2）。
    """
    rows: list[dict] = []
    for p in _sources():
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        rel = p.relative_to(ROOT).as_posix()
        caps = {m.group(1): int(m.group(2)) for m in _CONST.finditer(text)}
        # 先**一次**扫出"每个大写标识符出现在哪几行" —— 逐常量扫全文件是 O(常量数 × 行数)
        # 的正则（`lomelf.lomt` 一个文件就 187 × 6000），`#770` 把 `loment/lib` 与示例
        # 收进口径之后那会慢到不可接受。这里只把**候选行**交给下面的正则，输出逐字节同义。
        cand: dict[str, list[int]] = {}
        for i, ln in enumerate(lines):
            for m in re.finditer(r"\b[A-Z][A-Z0-9_]*\b", ln):
                cand.setdefault(m.group(0), []).append(i)
        for name, value in caps.items():
            esc = re.escape(name)
            mentions = cand.get(name, ())
            # **上界**：它出现在一条**序关系**比较里（`==`/`!=` 不算 —— 那是码值）。
            bound_lines = [
                i for i in mentions
                if re.search(rf"(?:{_BOUND}\s*{esc}\b)|(?:\b{esc}\s*{_BOUND})", lines[i])
            ]
            # **尺寸/步长**：它乘在别的东西上（`* NN`），或本身就是一次分配/声明的长度。
            # `SLOT` 那种只做步长的就是靠这一条进来的 —— 它当然是容量，只是从没被比较过。
            size_lines = [
                i for i in mentions
                if re.search(rf"(?:\*\s*{esc}\b)|(?:\b{esc}\s*\*)|"
                             rf"(?:\b(?:alloc|sys_alloc|reserve)\s*\(\s*{esc}\b)", lines[i])
            ]
            sites = sorted(set(bound_lines) | set(size_lines))
            if not sites:
                continue                     # 既不是上界也不是尺寸 ⇒ 不是容量（码值/掩码）
            speaks = any(
                any(s in ln for s in SPEAKS)
                for i in sites
                for ln in lines[i:i + LOOKAHEAD]
            )
            rows.append({"file": rel, "line": sites[0] + 1, "name": name, "value": value,
                         "kind": ("上界" if bound_lines else "尺寸"), "speaks": speaks,
                         "explained": EXPLAINED.get((rel, name), "")})
    return rows


def scan_literals() -> list[dict]:
    """**字面量上界**那一类（`#770`）：`<` / `<=` 右边的数字，左边变量被当作 store 下标。"""
    rows: list[dict] = []
    for p in _sources():
        lines = p.read_text(encoding="utf-8").split("\n")
        rel = p.relative_to(ROOT).as_posix()
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("//"):
                continue
            code = ln.split("//", 1)[0]
            for m in _LITERAL_BOUND.finditer(code):
                var, value = m.group(1), int(m.group(2))
                if value < _LITERAL_MIN:
                    continue
                window = lines[i:i + LITERAL_WINDOW]
                if not any(re.search(rf"\b{re.escape(var)}\b", arg)
                           for w in window for arg in _store_index_args(w.split("//", 1)[0])):
                    continue                 # 这个数字没在给一次写入当上界 ⇒ 不是容量
                name = f"literal:{value}"
                speaks = any(s in w for w in lines[i:i + LOOKAHEAD] for s in SPEAKS)
                rows.append({"file": rel, "line": i + 1, "name": name, "value": value,
                             "kind": "字面量", "speaks": speaks,
                             "explained": EXPLAINED.get((rel, name), "")})
    return rows


def scan_arrays() -> list[dict]:
    """**定长数组**那一类（`#770`）：`let`/`var`/`const`/`fn` 声明里的 `[T; N]`。"""
    rows: list[dict] = []
    for p in _sources():
        lines = p.read_text(encoding="utf-8").split("\n")
        rel = p.relative_to(ROOT).as_posix()
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("//") or not _ARRAY_STMT.match(s):
                continue
            code = ln.split("//", 1)[0]
            m = _ARRAY_DECL.search(code)
            if not m:
                continue
            typ, value = m.group(1), int(m.group(2))
            name = f"array:[{typ};{value}]"
            speaks = any(s2 in w for w in lines[i:i + LOOKAHEAD] for s2 in SPEAKS)
            rows.append({"file": rel, "line": i + 1, "name": name, "value": value,
                         "kind": "定长数组", "speaks": speaks,
                         "explained": EXPLAINED.get((rel, name), "")})
    return rows


def gaps(rows: list[dict]) -> list[dict]:
    """**真的缺口**：筛子说它沉默，而 `EXPLAINED` 也拿不出理由的那一份。"""
    return [r for r in rows if not r["speaks"] and not r["explained"]]


def _summary(label: str, rows: list[dict]) -> str:
    gap = gaps(rows)
    ne = sum(1 for r in rows if r["explained"] and not r["speaks"])
    silent = len(gap) + ne
    return (f"{label} {len(rows)} 处；**有话 {len(rows) - silent}，沉默 {silent}**"
            f"（其中 {ne} 条已写明来路，见 `EXPLAINED`）")


def _print_rows(rows: list[dict]) -> None:
    print(f"{'文件:行':<44} {'容量':<20} {'值':>10}  越界时")
    for r in rows:
        where = f"{r['file']}:{r['line']}"
        mark = "有话" if r["speaks"] else ("沉默（已写明来路）" if r["explained"] else "**沉默**")
        print(f"{where:<44} {r['name']:<20} {r['value']:>10}  {mark}")


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    rows = scan()
    lits = scan_literals()
    arrs = scan_arrays()
    if "--silent" in a:
        for r in gaps(rows):
            print(f"{r['file']}:{r['line']} {r['name']}={r['value']}")
        return 0
    if "--literals" in a:
        for r in lits:
            print(f"{r['file']}:{r['line']} {r['name']}")
        return 0
    if "--arrays" in a:
        for r in arrs:
            print(f"{r['file']}:{r['line']} {r['name']}")
        return 0
    if "--frontend" in a:
        # `#345` 采纳的 (a) 的第一档：**编译器前端不许有沉默的具名容量**（见 `FRONTEND`）。
        fe = [r for r in rows if Path(r["file"]).name in FRONTEND]
        fegap = gaps(fe)
        print(f"编译器前端：具名容量 {len(fe)} 处，**缺口 {len(fegap)}**")
        for r in fegap:
            print(f"  {r['file']}:{r['line']} {r['name']}={r['value']}")
        return 0
    _print_rows(rows)
    print("")
    _print_rows(arrs)
    print("")
    _print_rows(lits)
    print()
    print(_summary("具名容量", rows))
    print(_summary("定长数组", arrs))
    print(_summary("字面量上界", lits))
    for label, rs in (("具名", rows), ("数组", arrs), ("字面量", lits)):
        for r in gaps(rs):
            print(f"  沉默（{label}）: {r['file']}:{r['line']} {r['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
