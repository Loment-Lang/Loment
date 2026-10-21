#!/usr/bin/env python3
# loment_caps_test.py — 固定容量盘点**不可漂**（`FujoJTOP/loment#370`、`#345`、`#770`）。
#
# 盘点的口径与理由都在 `tools/loment_caps.py` 的文件头里。这里的判据只做一件事：
# **那几个数与那几份名单是算出来的** —— 改一个容量、加一条检查，下一轮就对不上，于是
# "编译器有多少个固定容量、其中几个越界时说不出话"这件事**不会再悄悄变**。
#
# 名单**从工具输出生成**（`python tools/loment_caps.py --silent` / `--literals` /
# `--arrays` 重跑一遍即可重生成），不手抄：手抄的清单第一次改代码就漂，而这份的全部
# 价值就是它不漂。

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_caps  # noqa: E402
import loment_src  # noqa: E402  (扫的面从它的 GLOBS 推出来，见 `#770`)

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 2026-10-16 的数 —— `FujoJTOP/loment#345` 要的那个"数"就是这几个。
#:
#: 2026-10-16 第二次数（226 → **227**，沉默的那条**不动**）：`lomelf.lomt` 的 `OUT_CAP`
#: 第一次成为一条**上界**。原先它只当尺寸用，而**文本段越界是静默的** —— 数据段钉在
#: `TEXT_VADDR + OUT_CAP`，代码写超了就盖住数据段，产物一启动就段错误（`docs/200` §9）。
#: 那一笔给它补了守卫（`if tlen > OUT_CAP { fail("文本段满了 …") }`），于是它**会说
#: 话了** —— 所以条数 +1、沉默的那条不动。**这正是这条棘轮要的形状**：加一处容量就得
#: 来这里报一声，而且"说不出话的"那一份名单只在有人真的补上失败路径时才会变小。
#:
#: 2026-10-16 第三次数（227 → **228**，沉默的那条**不动**）：`lomelf.lomt` 的
#: `DIAGB_OFF` —— `--diag-out` 路径在 argv 暂存块最后 1 KiB 的落点（`#8`）。
#: 它出现在一条上界里（`if dst + 1024 > DIAGB_OFF`），而那一支**说得出来**
#: （`loment: too many --link files`）。与上一笔 `OUT_CAP` 同一个形状：加一处容量、
#: 它有失败路径，于是条数 +1、沉默不动。
#:
#: 2026-10-16 第四次数（`#345` 采纳的 (a) 落地）：① **筛子修了一处假阳性** —— `<<` / `>>`
#: 里的 `<` / `>` 被当成了上界比较，于是 `TY_K_NAMED << TY_KIND_SHIFT` 一直算一条「容量」；
#: 它是**类型标签**，修完整个退出本口径；② **编译器前端四处补上失败路径**（`SLOT` /
#: `DIR_CAP` / `SUBP_CAP` / `EXT_CAP`，都是静默截断）。另外新增 `EXPLAINED`：筛子判它
#: 沉默、而**来路已写明**的那几条（见 `tools/loment_caps.py` 那张表，逐条带理由）。
#: **两个数只许往「更好」的方向动**：条数变是信号，沉默数变小是好事。
#:
#: 2026-10-17（`#360` V2）：`lomelf.lomt` 加了动态链接那三处 —— `EXP_MAX` / `IMP_MAX`
#: 都有守卫（"有话"），`X_SIZE` 是**导出表那一格的步长**（12 字节），与 `SYM_STRIDE` /
#: `ARG_STEP` 同形：它不是上界，越过它的是 `EXP_MAX`。所以条数 225 → **228**、
#: 沉默 164 → **165**（多的是 `X_SIZE` 这一个名字）。
#:
#: 2026-10-17（`#45`）：`lomfmt.lomt` 的格式化器现在从源码里**单独扫注释**，注释表
#: `CMT_CAP = 4096`（落在 `MEM_CAP` 顶上那 64 KiB）是一条新容量 —— 条数 228 → **229**。
#: 它**有话**（满了 `fail` 退 1，不许悄悄少几条注释），所以沉默仍是 **165**、名单不动。
#:
#: 2026-10-17（0.1.4.7）：`lomadmit` 的用例表重排（#697/#698 把 `seq/hits/held` 换成
#: u64 并去掉那 9 个 `C_*` 常量）⇒ 条数 229 → **220**、沉默 165 → **156**，名单少那
#: 9 个名字。**只往"更好"的方向动**：没有新增的沉默名（判据会盯着）。
#:
#: 2026-10-18（`#719`）：`codegen.lomt` 加了常量表 `CG_MAXCONST = 1024`（`find_const` 不再
#: 扫全单元）。它**有话**（满了 `emit_module` 返回 0，驱动点名"functions/constants/enums"），
#: 所以沉默仍是 **156**、名单不动 —— 只加一条容量，条数 220 → **221**。
#:
#: 2026-10-18（`#485`）：`lomenterr.lomt` 也新用了一处 `MSG_CAP` —— `if mlen >= MSG_CAP`
#: 判的是"这条消息有没有被拷满"，判到了就印 `truncated` / 写 `"truncated": true`。
#: 它不是在写，是在**报**，所以来路写进 `loment_caps.EXPLAINED`（`gaps()` 仍是 0）；
#: 但按筛子的口径它仍算"沉默"一格（没有 `fail` 路径），于是沉默 156 → **157**、
#: 条数再 +1 → **222**。**只往"更好"的方向动**：这是一处检测点，不是新的写穿点。
#:
#: 2026-10-18（`#598` / `#600` / `#621` / `#622`）：`lomelf.lomt` 的导出表整张消失
#: （导出名现查 `.dynsym`，不再抄进表），于是 `EXP_MAX`（有话）与 `X_SIZE`（步长、
#: 沉默）两条退出盘点；同时新增 `OBJ_MAX` / `REL_MAX`（对象表与重定位表的容量，两处
#: 都有 `fail` 路径）。**条数不变（222）**，沉默 157 → **156**（少的是 `X_SIZE`）。
#:
#: 2026-10-19（`#600` × `#631`）：输入缓冲后加了 4 KiB **名字预留区**
#: （`NAME_RESERVE` / `NAME_CAP`）—— 撑满的 `.ll` 也要放得下收编面的名字。
#: `NAME_CAP` 是新的写穿点，有 `fail` 路径（不是沉默的一格）；条数 222 → **223**。
#:
#: 2026-10-19（`#609` / `#610` / `#617`）：`loment/tools/lomelf.lomt` 的 V1 三张新表
#: 与 TLS 块：GOT_MAX / WEAK_MAX / TLS_MAX / PORT_MAX / TLS_CAP，每一处越界都是
#: `fail` 点名退出（不是写穿）。条数 223 → **228**。
#:
#: 2026-10-19（`#770`）：**扫的面变成整棵 `loment/**`（递归）**，不再只是
#: `loment/selfhost/*.lomt` + `loment/tools/*.lomt`。新扫进来的是：
#:   * `loment/lib/json.lomt` 的 `J_NODE_STRIDE`（步长、沉默）与
#:     `loment/lib/proc.lomt` 的 `PROC_WS`（沉默）、`PROC_CMD_MAX`（有话）；
#:   * `loment/ct/offsets.lomt` 的 `STRIDE`（有话）；
#:   * `loment/examples/` 里四个示例常量：`NODE` / `W` / `H` / `SCALE`（都沉默）。
#: 所以条数 228 → **236**、沉默 156 → **162**（新增的 6 个沉默名都写进了 `EXPLAINED`）。
#: **这一笔只扩口径，不改行为**：新扫到的沉默项逐条在 `EXPLAINED` 里写清来路。
#:
#: 2026-10-19（`FujoJTOP/loment#774`，叠在 `#770` 的递归口径上）：**自举编译器零真缺口**。
#: ① 筛子的词汇表补了两种实际在用的"说话"形状 —— `set_err(` / `wr_err(`：
#:    `interp.lomt` 的守卫一直是"先记错误码、入口写成 `ERR <种类>`"，而 `SPEAKS`
#:    原先只有前缀 `err_`，认不出 `set_err`；7 处**本来就会说话**的守卫从沉默变有话。
#: ② `potato.lomt` 的三张表补了真守卫（`PT_EMAX` / `PT_FMAX` / `PT_GLRMX` —— 原先
#:    写穿/静默丢，现在点名拒），另外 7 处"越界由调用方报"的写进 `EXPLAINED`。
#:    顺带 `PT_EN` 被认出是**槽位不是容量**，退出本口径。
#: ⇒ 条数 236 → **235**、沉默 162 → **154**、沉默名 **129** 个。
CAPACITY_SITES = 236
SILENT_SITES = 155

#: 第二类：字面量上界（`#770`）。`[T; N]` 与 `literal:<N>` 各自钉住总数与沉默的那一份。
LITERAL_SITES = 63
LITERAL_SILENT = 62
ARRAY_SITES = 17
ARRAY_SILENT = 16

#: 越界时**说不出话**的那些具名常量（`--silent` 去重、按字节序）。136 个。
#: ⚠ 这里按**筛子**的口径（`not speaks`），所以那些"来路已写明"的也在内 ——
#: 判断"真缺口"用 `loment_caps.gaps()`（那条另有一条判据）。
SILENT_NAMES = frozenset((
    "ARENA_CAP",
    "CHK_ENV_MAX",
    "CASES_CAP",
    "CG_MAXENUM",
    "CG_MAXFN",
    "CMAX",
    "CNTSZ",
    "CR",
    "CTX_CAP",
    "CUR_CAP",
    "CW",
    "C_CAND",
    "DECL_KW",
    "DELIM_CAP",
    "DEPS_CAP",
    "DEPTHSZ",
    "DIGEST_LEN",
    "DIRQ",
    "DOC_CAP",
    "DOC_MAX",
    "EMAX",
    "ENTS_CAP",
    "ENT_CAP",
    "ENT_STRIDE",
    "FBUF",
    "FMAX",
    "FNMAX",
    "FRAME_CAP",
    "FUAI_CAP",
    "FW",
    "F_A1",
    "F_A3",
    "F_A4",
    "F_REV",
    "GIT_CAP",
    "H",
    "HASH_LEN",
    "IN_CAP",
    "J_NODE_STRIDE",
    "J_PL",
    "J_PS",
    "K_ADD",
    "K_ASHR",
    "K_FN",
    "K_INTTOPTR",
    "K_INT_MAX",
    "K_INT_MIN",
    "K_TRUNC",
    "LINE_CAP",
    "LMAX",
    "LTOK_CAP",
    "MAXBAD",
    "MAXD",
    "MAXF",
    "MAXOP",
    "MAXROW",
    "MAXTOK",
    "MAXW",
    "MAX_DIALECT",
    "MAX_LANG",
    "MEM_CAP",
    "MIN_RECLEN",
    "MMAX",
    "MSG_CAP",
    "MW",
    "M_AUX",
    "M_CAP",
    "NAMEE",
    "NAMES_CAP",
    "NAME_MAX",
    "NMAX",
    "NODE",
    "NPOOL",
    "NW",
    "N_DOC",
    "OB_CAP",
    "OUT_CAP",
    "PATHW",
    "PATH_CAP",
    "POOL_CAP",
    "PROC_WS",
    "PT_AMAX",
    "PT_EMAX",
    "PT_FMAX",
    "PT_GLRMX",
    "PT_GMAX",
    "PT_L0CAP",
    "PT_MAX",
    "PT_TMAX",
    "PT_TSTRIDE",
    "PY_TMAX",
    "QNAMES_CAP",
    "QUEUE_CAP",
    "Q_STRIDE",
    "ROWE",
    "SCALE",
    "SEG_TYPES",
    "SHOWN",
    "SHOW_MAX",
    "SMAX",
    "SRCCAP",
    "SRC_CAP",
    "STR_CAP",
    "SUG_CAP",
    "SUG_NAME",
    "SW",
    "SYM_MAX",
    "SYM_STRIDE",
    "SYS_CAP",
    "S_ENUM",
    "S_FIELD",
    "S_META",
    "S_NAME",
    "S_PARAM",
    "S_REC",
    "S_VALG",
    "TA",
    "TMAX",
    "TM_CAP",
    "TOKE",
    "TOKMAX",
    "TOOL_N",
    "TXT_CAP",
    "T_W",
    "URI_CAP",
    "VAL_REC",
    "VIM_CAP",
    "W",
    "W_EACH",
    "ZERO_CAP",
))

#: 字面量上界里**沉默**的那一份，键是 `(文件, literal:<N>)`，值是**条数**
#: （同一个值在同一文件里出现几次都要钉住 —— 只钉名字会让"多拷了一处"漏过去）。
SILENT_LITERALS = {
    ("loment/examples/fuc_node.lomt", "literal:64"): 1,
    ("loment/examples/gc_heap/main.lomt", "literal:8"): 1,
    ("loment/lib/lumtui_doc.lomt", "literal:64"): 1,
    ("loment/lib/proc.lomt", "literal:8"): 1,
    ("loment/lib/sha256.lomt", "literal:16"): 1,
    ("loment/lib/sha256.lomt", "literal:32"): 1,
    ("loment/lib/sha256.lomt", "literal:56"): 1,
    ("loment/lib/sha256.lomt", "literal:64"): 2,
    ("loment/lib/sha256.lomt", "literal:8"): 1,
    ("loment/selfhost/checker.lomt", "literal:63"): 1,
    ("loment/selfhost/codegen.lomt", "literal:16"): 1,
    ("loment/selfhost/codegen.lomt", "literal:20"): 2,
    ("loment/selfhost/codegen.lomt", "literal:64"): 2,
    ("loment/selfhost/codegen.lomt", "literal:8"): 2,
    ("loment/selfhost/codegen.lomt", "literal:9"): 1,
    ("loment/selfhost/driver.lomt", "literal:200"): 2,
    ("loment/selfhost/driver.lomt", "literal:4000"): 3,
    ("loment/selfhost/interp.lomt", "literal:20"): 1,
    ("loment/selfhost/lexer.lomt", "literal:256"): 1,
    ("loment/selfhost/potato.lomt", "literal:20"): 1,
    ("loment/lspe/lspe.lomt", "literal:16"): 1,
    ("loment/tools/lomadmit.lomt", "literal:4000"): 1,
    ("loment/tools/lomc.lomt", "literal:4000"): 1,
    ("loment/tools/lomcapasserts.lomt", "literal:4000"): 2,
    ("loment/tools/lomclicheck.lomt", "literal:400"): 1,
    ("loment/tools/lomdoc.lomt", "literal:4000"): 1,
    ("loment/tools/lomelf.lomt", "literal:16"): 2,
    ("loment/tools/lomelf.lomt", "literal:8"): 2,
    ("loment/tools/lomenterr.lomt", "literal:1024"): 1,
    ("loment/tools/lomenterr.lomt", "literal:2000"): 1,
    ("loment/tools/lomenterr.lomt", "literal:4000"): 2,
    ("loment/tools/lomenterr.lomt", "literal:60"): 1,
    ("loment/tools/lomextblock.lomt", "literal:4000"): 1,
    ("loment/tools/lomfmt.lomt", "literal:16"): 1,
    ("loment/tools/lomfmt.lomt", "literal:4000"): 1,
    ("loment/tools/lomlib.lomt", "literal:64"): 1,
    ("loment/tools/lompkg.lomt", "literal:4000"): 2,
    ("loment/tools/lompotato.lomt", "literal:124"): 1,
    ("loment/tools/lompotc.lomt", "literal:400"): 1,
    ("loment/tools/lompotc.lomt", "literal:500"): 1,
    ("loment/tools/lomsyscalls.lomt", "literal:4000"): 2,
    ("loment/tools/lomvimgrammar.lomt", "literal:4000"): 1,
    ("loment/tools/lomzwframe.lomt", "literal:4000"): 1,
    ("loment/tools/lomzwframe.lomt", "literal:8"): 1,
    ("loment/tools/lsp.lomt", "literal:200"): 1,
    ("loment/tools/lsp.lomt", "literal:400"): 1,
    ("loment/tools/lsp.lomt", "literal:4000"): 1,
    ("loment/tools/lsp.lomt", "literal:60"): 2,
}

#: 定长数组里**沉默**的那一份，键是 `(文件, array:[T;N])`，值是条数。
SILENT_ARRAYS = {
    ("loment/ct/loops.lomt", "array:[u64;8]"): 1,
    ("loment/examples/demo.lomt", "array:[u32;4]"): 2,
    ("loment/examples/native_agg.lomt", "array:[u32;4]"): 1,
    ("loment/examples/native_mut.lomt", "array:[u32;2]"): 1,
    ("loment/examples/native_mut.lomt", "array:[u32;4]"): 1,
    ("loment/examples/native_slice.lomt", "array:[u32;3]"): 1,
    ("loment/examples/native_slice.lomt", "array:[u32;4]"): 1,
    ("loment/examples/tour.lomt", "array:[u32;3]"): 1,
    ("loment/nltrans/Surface.lomt", "array:[i64;3]"): 1,
    ("loment/selfhost/codegen.lomt", "array:[u32;16]"): 1,
    ("loment/selfhost/ir_index.lomt", "array:[u64;4]"): 2,
    ("loment/selfhost/lexer.lomt", "array:[u32;16]"): 1,
    ("loment/selfhost/pos/ok.lomt", "array:[u32;4]"): 1,
    ("loment/tools/lomeol.lomt", "array:[u32;32]"): 1,
}


@test
def test_the_two_numbers_are_pinned():
    """具名容量的总数与"沉默"的条数钉住 —— 这两个数就是 `#345` 要的那个答案。"""
    rows = loment_caps.scan()
    assert len(rows) == CAPACITY_SITES, (
        f"容量条数变了：{len(rows)}（钉的是 {CAPACITY_SITES}）—— 新加/删掉一个容量，"
        "或者改了一处用法，都在这里现形")
    got = sum(1 for r in rows if not r["speaks"])
    assert got == SILENT_SITES, (
        f"「越界时说不出话」的条数变了：{got}（钉的是 {SILENT_SITES}）—— 变**小**是好事，"
        "把那两个数一起改掉；变**大**说明新添了一处没有失败路径的容量")


@test
def test_the_silent_list_cannot_drift():
    """沉默的那份名单**逐名**钉住 —— 这才是"清单不会漂"那句的兑现。"""
    rows = loment_caps.scan()
    got = frozenset(r["name"] for r in rows if not r["speaks"])
    extra = sorted(got - SILENT_NAMES)
    gone = sorted(SILENT_NAMES - got)
    assert not extra, (
        f"这几处**新**变得说不出话：{extra} —— 越界时它们不报错，只会写穿"
        "（自举侧的症状是段错误，`FujoJTOP/loment#345`）")
    assert not gone, (
        f"这几处已经有失败路径了（好事）：{gone} —— 把 `SILENT_NAMES` 跟着改掉，"
        "别让名单比代码旧")


@test
def test_literal_and_array_counts_are_pinned():
    """第二类（字面量上界 / 定长数组）也钉住数与沉默数（`#770`）。

    **Why**：`#770` 之前，容量写成字面量（`while j < 200`、`if n < 16`、`o + i < 4000`）
    或静态数组（`[u32; 16]`）就**完全不在盘点里** —— 加一处新的不会有任何东西动。
    现在它们各有一套数：加一处就得来这里报一声，沉默的那一份还得在 `EXPLAINED`
    里写明来路（见 `test_the_second_class_is_loud_or_explained`）。
    """
    lits = loment_caps.scan_literals()
    arrs = loment_caps.scan_arrays()
    assert len(lits) == LITERAL_SITES, (
        f"字面量上界条数变了：{len(lits)}（钉的是 {LITERAL_SITES}）")
    assert sum(1 for r in lits if not r["speaks"]) == LITERAL_SILENT, (
        "「字面量上界越界时说不出话」的条数变了")
    assert len(arrs) == ARRAY_SITES, (
        f"定长数组条数变了：{len(arrs)}（钉的是 {ARRAY_SITES}）")
    assert sum(1 for r in arrs if not r["speaks"]) == ARRAY_SILENT, (
        "「定长数组越界时说不出话」的条数变了")


@test
def test_the_silent_literal_and_array_lists_cannot_drift():
    """第二类的沉默名单**按 (文件, 名字, 条数)** 钉住 —— 加一处同名同值的也要现形。"""
    got = Counter((r["file"], r["name"]) for r in loment_caps.scan_literals()
                  if not r["speaks"])
    extra = sorted(str(k) for k in set(got) - set(SILENT_LITERALS))
    gone = sorted(str(k) for k in set(SILENT_LITERALS) - set(got))
    changed = sorted(f"{k}: {SILENT_LITERALS[k]} -> {got[k]}" for k in set(got) & set(SILENT_LITERALS)
                     if got[k] != SILENT_LITERALS[k])
    assert not extra, f"这几处**新**字面量上界说不出来话：{extra}"
    assert not gone, f"这几处字面量上界已经不在口径里了（去掉这一行）：{gone}"
    assert not changed, f"这几处字面量上界的条数变了：{changed}"

    got = Counter((r["file"], r["name"]) for r in loment_caps.scan_arrays()
                  if not r["speaks"])
    extra = sorted(str(k) for k in set(got) - set(SILENT_ARRAYS))
    gone = sorted(str(k) for k in set(SILENT_ARRAYS) - set(got))
    changed = sorted(f"{k}: {SILENT_ARRAYS[k]} -> {got[k]}" for k in set(got) & set(SILENT_ARRAYS)
                     if got[k] != SILENT_ARRAYS[k])
    assert not extra, f"这几处**新**定长数组说不出来话：{extra}"
    assert not gone, f"这几处定长数组已经不在口径里了（去掉这一行）：{gone}"
    assert not changed, f"这几处定长数组的条数变了：{changed}"


@test
def test_the_scan_covers_the_whole_source_tree():
    """**扫面 = 整棵 `loment/**`（递归），从 `loment_src.GLOBS` 推出来**（`#770`）。

    **Why**：原先 `TREES = ("loment/selfhost", "loment/tools")` + `glob("*.lomt")`
    是**两层、非递归**的：
      * `loment/selfhost/grammar_decl/`、`neg/`、`neg_across/` 从来没被打开过；
      * `loment/lib/`（11 个随包发的库文件）整个不在口径里 —— 那正是 `#770` 点名的洞。
    清单写成手抄的树名，新加一个目录时**没有任何东西会动**。现在树名从
    `loment_src.GLOBS` 的 `loment/**` 推出来，覆盖由构造保证；这条判据只是在**核对**
    它：`_sources()` 必须等于"那个 glob 展开、减掉明确排除的子树"。
    """
    # (1) 树名确实来自 GLOBS，而不是这里又手写了一遍。
    assert loment_caps.SOURCE_GLOB in loment_src.GLOBS, (
        f"SOURCE_GLOB={loment_caps.SOURCE_GLOB!r} 不在 loment_src.GLOBS 里 —— "
        "扫的面必须与源码包的树定义是同一条")
    # (2) 覆盖 = glob 展开 - EXCLUDE_TREES - EXCLUDE（不是"我们恰好扫了这几层"）。
    want = {p.relative_to(ROOT).as_posix()
            for p in ROOT.glob(f"{loment_caps.SOURCE_GLOB}/*.lomt") if p.is_file()}
    want = {f for f in want
            if not any(f.startswith(t) for t in loment_caps.EXCLUDE_TREES)
            and f not in loment_caps.EXCLUDE}
    got = {p.relative_to(ROOT).as_posix() for p in loment_caps._sources()}
    missing = sorted(want - got)
    assert not missing, f"这些 `.lomt` 在源码树里、却不在口径里：{missing[:10]}"
    # (3) `#770` 点名的两个洞**具体**在里面（不是"将来某天会"）。
    for must in ("loment/lib/json.lomt",
                 "loment/selfhost/grammar_decl/decl.lomt"):
        assert must in got, f"{must} 不在口径里 —— `#770` 的洞又回来了"
    assert any(f.startswith("loment/lib/") for f in got), "loment/lib/ 一个文件都没扫到"


@test
def test_literal_caps_and_static_arrays_are_visible():
    """`#770` 点名的三处字面量上界与静态数组**必须被看见**。

    **Why**：这三处是 issue 里逐条给的复现：
      * `loment/selfhost/driver.lomt` 的 `while j < 200 …`（目录项名字在 200 字节截断）；
      * `loment/tools/lomfmt.lomt` 的 `if n < 16`（块类型栈 16 格，更深的**静默丢弃**）；
      * `loment/tools/lomc.lomt` 的 `o + i < 4000`（错误文本在 4000 字节截断）。
    再加一条静态数组（`[u32; 16]` 这种长度写在类型里的容量）。它们此前一条都不在
    盘点里 —— 这条判据就是"现在在了"的兑现，而且钉的是**行级**事实。
    """
    lits = {(r["file"], r["line"], r["name"]) for r in loment_caps.scan_literals()}
    for f, line, name in (("loment/selfhost/driver.lomt", 868, "literal:200"),
                          ("loment/tools/lomfmt.lomt", 497, "literal:16"),
                          ("loment/tools/lomc.lomt", 501, "literal:4000")):
        assert (f, line, name) in lits, (
            f"{f}:{line} 的 {name} 没被看见 —— `#770` 的那一处又隐形了")
    arrs = {(r["file"], r["name"]) for r in loment_caps.scan_arrays()}
    assert ("loment/selfhost/lexer.lomt", "array:[u32;16]") in arrs, (
        "静态数组（`[u32; 16]`）一条都没扫到 —— 第二类没接上")


@test
def test_the_second_class_is_loud_or_explained():
    """第二类的每一处沉默要么**有话**、要么在 `EXPLAINED` 里写明来路（`#770`）。

    **Why**：把字面量/数组算进"容量"之后，默认它们全是沉默的（局部截断不说一句话）。
    这一条要求**每一处都能核对**：为什么它不是（新的）写穿点 —— 否则"扩大口径"
    只是把沉默数抬高一截，没有回答 `#345` 问的那个问题。
    """
    for label, rows in (("字面量上界", loment_caps.scan_literals()),
                        ("定长数组", loment_caps.scan_arrays())):
        gap = loment_caps.gaps(rows)
        assert rows, f"{label}一条都没扫到 —— 这条判据自己先失效了"
        assert not gap, (
            f"{label}还有**说不出话**、也没写来路的："
            + "; ".join(f"{r['file']}:{r['line']} {r['name']}" for r in gap)
            + " —— 补一条守卫，或在 `EXPLAINED` 里写清为什么它不写穿")


@test
def test_the_scan_is_confined_and_deterministic():
    """口径：**整棵 `loment/`、但只有 `loment/`**（排除生成物），且**跑两遍一样**。"""
    a = loment_caps.scan()
    b = loment_caps.scan()
    assert a == b, "同一棵树上扫两遍结果不同 —— 那不是机械的"
    assert loment_caps.scan_literals() == loment_caps.scan_literals()
    assert loment_caps.scan_arrays() == loment_caps.scan_arrays()
    for r in a + loment_caps.scan_literals() + loment_caps.scan_arrays():
        assert r["file"].startswith("loment/"), r["file"]
        assert not any(r["file"].startswith(t) for t in loment_caps.EXCLUDE_TREES), (
            f"{r['file']} 是生成物（{loment_caps.EXCLUDE_TREES}）—— 不该在口径里")
        assert r["file"] not in loment_caps.EXCLUDE, (
            f"{r['file']} 是生成的数据文件，常量是表项不是容量 —— 它不该在口径里")


@test
def test_the_front_end_has_no_silent_capacity():
    """**编译器前端一处沉默的容量都不许有** —— 这是 `#345` 采纳的 (a) 的第一档。

    **Why**：`#343` 那一类"崩了却一句话都不说"的实例（36 层嵌套块、21 层括号、11 个形参、
    结构体字面量少个逗号）**全部**落在前端：驱动、词法、检查器、发射器。所以 (a) 不是
    一口气给所有容量都补话，而是先划出一条**能验、能守住**的线：**用户一份源文件推得动的
    那些容量，越界必须有话**。

    **How to apply**：前端那五个文件（`loment_caps.FRONTEND`）里，三类里筛子判沉默的那些
    **必须都在 `EXPLAINED` 里有一条写清来路的记录**，否则这条红。2026-10-16 补完四处
    （`SLOT` / `DIR_CAP` / `SUBP_CAP` / `EXT_CAP`）之后，剩下的具名那条是 `MAX_LANG`；
    `#770` 扩口径之后新增的字面量/数组（`4000` / `200` / `16` 等）也逐条写了来路。
    """
    fe = [r for r in loment_caps.scan() if Path(r["file"]).name in loment_caps.FRONTEND]
    gap = loment_caps.gaps(fe)
    assert fe, "前端一处容量都没扫到 —— 这条判据自己先失效了（那时它证明不了任何事）"
    assert not gap, (
        "编译器前端还有**说不出话**的容量："
        + "; ".join(f"{r['file']}:{r['line']} {r['name']}={r['value']}" for r in gap)
        + " —— 越界时它只会写穿（自举侧的症状是段错误），而用户一份源就能推得动它"
          "（`FujoJTOP/loment#345` 采纳的 (a)）")
    for label, rows in (("字面量上界", loment_caps.scan_literals()),
                        ("定长数组", loment_caps.scan_arrays())):
        fe2 = [r for r in rows if Path(r["file"]).name in loment_caps.FRONTEND]
        gap2 = loment_caps.gaps(fe2)
        assert not gap2, (
            f"编译器前端的{label}还有**说不出话**的：" + "; ".join(
                f"{r['file']}:{r['line']} {r['name']}" for r in gap2))
    print(f"      前端 {len(fe)} 处具名容量，缺口 0（其余要么有话，要么来路写在 EXPLAINED 里）")


@test
def test_the_self_host_compiler_has_no_silent_capacity():
    """**自举编译器（`loment/selfhost/`）一处真缺口都不许有** —— `#774` 的那条线。

    **Why**：`#774` 说的是"固定表没有失败路径，而失败是**沉默**的"。前端那五个文件
    （`docs/258` 的 (a)）只是这条线的一段；`potato` / `interp` / `comefor` 同样在
    编译一份用户源时被推着走。这一条把 (a) 从"前端"扩到**整个自举编译器**：
    `loment/selfhost/` 下每一处容量，要么它自己的守卫说得出来，要么 `EXPLAINED`
    里有一条**指着真实失败消息**的理由（越界由调用方的 `pt_wfail` / `fail` 报）。

    **How to apply**：加一张固定表就加一条 `EXPLAINED` 或一条守卫；两样都没有 =
    下一份输入会**静默写穿**（自举侧的症状是段错误，`#343` 那一族）。这条判据
    与 `loment_caps.gaps()` 同源，不另写一套扫描。
    """
    rows = [r for r in loment_caps.scan()
            if r["file"].startswith("loment/selfhost/")]
    assert rows, "自举编译器一处容量都没扫到 —— 这条判据自己先失效了"
    gap = loment_caps.gaps(rows)
    assert not gap, (
        "自举编译器还有**说不出话**的容量："
        + "; ".join(f"{r['file']}:{r['line']} {r['name']}={r['value']}" for r in gap)
        + " —— 越界时它只会写穿或静默截断（`#774`）"
    )
    print(f"      自举编译器 {len(rows)} 处容量，真缺口 0"
          f"（{sum(1 for r in rows if r['explained'])} 处来路写在 EXPLAINED 里）")


@test
def test_every_exclusion_states_a_reason():
    """`EXPLAINED` 里的每一条**都还在口径里，而且都写清了理由** —— 排除必须可核对。

    **Why**：`EXPLAINED` 是一张"筛子看走眼了"的表，而它唯一的价值是**每一条都能核对**：
    键指着的那处容量还在不在？理由是不是一句话说得清的事实？少一条核对，它就从"说明"
    变成"把数改小"的手法。

    **How to apply**：键是 `(文件, 名字)`（三类共用一个表；第二类的名字是
    `literal:<N>` / `array:[T;N]`）且必须真的出现在扫描输出里 —— 容量被删掉、被改名、
    或者那处用法改了，这条就红（2026-10-16 写这张表时当场抓到一条：`TY_K_NAMED` 修掉
    移位的假阳性之后**整个退出**了口径，那条记录于是必须删掉，不是留着）。
    """
    rows = loment_caps.scan() + loment_caps.scan_literals() + loment_caps.scan_arrays()
    live = {(r["file"], r["name"]) for r in rows}
    for key, why in loment_caps.EXPLAINED.items():
        assert key in live, (
            f"{key} 在 `EXPLAINED` 里，但它**不在**口径里了 —— 删掉这一条："
            "留着一张指不到东西的说明，比没有更坏")
        assert why.strip(), f"{key} 的理由是空的 —— 排除必须写清为什么"
    print(f"      {len(loment_caps.EXPLAINED)} 条排除，逐条指着口径里真实存在的容量")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print("")
    print(f"loment_caps_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
