#!/usr/bin/env python3
# loment_caps_test.py — 固定容量盘点**不可漂**（`FujoJTOP/loment#370`、`#345`）。
#
# 盘点的口径与理由都在 `tools/loment_caps.py` 的文件头里。这里的判据只做一件事：
# **那两个数与那份名单是算出来的** —— 改一个容量、加一条检查，下一轮就对不上，于是
# "编译器有多少个固定容量、其中几个越界时说不出话"这件事**不会再悄悄变**。
#
# 名单**从工具输出生成**（`python tools/loment_caps.py --silent` 重跑一遍即可重生成），
# 不手抄：手抄的清单第一次改代码就漂，而这份的全部价值就是它不漂。

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_caps  # noqa: E402

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
CAPACITY_SITES = 225
SILENT_SITES = 164

#: 越界时**说不出话**的那些名字（`--silent` 去重、按字节序）。138 个。
#: ⚠ 这里按**筛子**的口径（`not speaks`），所以那 6 条"来路已写明"的也在内 ——
#: 判断"真缺口"用 `loment_caps.gaps()`（那条另有一条判据）。
SILENT_NAMES = frozenset((
    "ARENA_CAP",
    "ARGS_CAP",
    "ARG_STEP",
    "CASES_CAP",
    "CG_MAXENUM",
    "CG_MAXFN",
    "CMAX",
    "CNTSZ",
    "CR",
    "CTX_CAP",
    "CUR_CAP",
    "CW",
    "C_ASKED",
    "C_CAND",
    "C_HELD",
    "C_HITS",
    "C_PLEN",
    "C_POFF",
    "C_PREV",
    "C_SEQ",
    "C_SLEN",
    "C_SOFF",
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
    "ENV_CAP",
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
    "HASH_LEN",
    "IN_CAP",
    "J_PL",
    "J_PS",
    "K_ADD",
    "K_ASHR",
    "K_FN",
    "K_INTTOPTR",
    "K_INT_MAX",
    "K_INT_MIN",
    "K_TRUNC",
    "LIMIT",
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
    "MAX_DEPTH",
    "MAX_DIALECT",
    "MAX_LANG",
    "MEM_CAP",
    "MIN_RECLEN",
    "MMAX",
    "MW",
    "M_AUX",
    "M_CAP",
    "NAMEE",
    "NAMES_CAP",
    "NAME_MAX",
    "NMAX",
    "NPOOL",
    "NW",
    "N_DOC",
    "OB_CAP",
    "OUT_CAP",
    "PATHW",
    "PATH_CAP",
    "POOL_CAP",
    "PT_AMAX",
    "PT_EMAX",
    "PT_EN",
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
    "TOK_CAP",
    "TOOL_N",
    "TXT_CAP",
    "T_W",
    "URI_CAP",
    "VAL_REC",
    "VIM_CAP",
    "W_EACH",
    "ZERO_CAP",
))


@test
def test_the_two_numbers_are_pinned():
    """容量总数与"沉默"的条数钉住 —— 这两个数就是 `#345` 要的那个答案。"""
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
def test_the_scan_is_confined_and_deterministic():
    """口径两条：**只扫那两棵树**（生成的数据文件排除在外），且**跑两遍一样**。"""
    a = loment_caps.scan()
    b = loment_caps.scan()
    assert a == b, "同一棵树上扫两遍结果不同 —— 那不是机械的"
    for r in a:
        assert r["file"].startswith(("loment/selfhost/", "loment/tools/")), r["file"]
        assert r["file"] not in loment_caps.EXCLUDE, (
            f"{r['file']} 是生成的数据文件，常量是表项不是容量 —— 它不该在口径里")


@test
def test_the_front_end_has_no_silent_capacity():
    """**编译器前端一处沉默的容量都不许有** —— 这是 `#345` 采纳的 (a) 的第一档。

    **Why**：`#343` 那一类"崩了却一句话都不说"的实例（36 层嵌套块、21 层括号、11 个形参、
    结构体字面量少个逗号）**全部**落在前端：驱动、词法、检查器、发射器。所以 (a) 不是
    一口气给 224 处都补话（那要动两棵树的每一处，而且大半是别的工具的私有缓冲），
    而是先划出一条**能验、能守住**的线：**用户一份源文件推得动的那些容量，越界必须有话**。

    **How to apply**：前端那五个文件（`loment_caps.FRONTEND`）里，筛子判沉默的那些
    **必须都在 `EXPLAINED` 里有一条写清来路的记录**，否则这条红。2026-10-16 补完四处
    （`SLOT` / `DIR_CAP` / `SUBP_CAP` / `EXT_CAP`）之后，剩下的那一条是 `MAX_LANG` ——
    它的来路写在 `EXPLAINED` 里（有守卫、不写穿；丢的是第 17 条语言声明，而那要先把
    "一个单元最多声明几门语言"定成语言规则，见 `docs/258` §4）。
    """
    fe = [r for r in loment_caps.scan() if Path(r["file"]).name in loment_caps.FRONTEND]
    gap = loment_caps.gaps(fe)
    assert fe, "前端一处容量都没扫到 —— 这条判据自己先失效了（那时它证明不了任何事）"
    assert not gap, (
        "编译器前端还有**说不出话**的容量："
        + "; ".join(f"{r['file']}:{r['line']} {r['name']}={r['value']}" for r in gap)
        + " —— 越界时它只会写穿（自举侧的症状是段错误），而用户一份源就能推得动它"
          "（`FujoJTOP/loment#345` 采纳的 (a)）")
    print(f"      前端 {len(fe)} 处容量，缺口 0（其余 {len(fe) - len(gap)} 处要么有话，"
          "要么来路写在 EXPLAINED 里）")


@test
def test_every_exclusion_states_a_reason():
    """`EXPLAINED` 里的每一条**都还在口径里，而且都写清了理由** —— 排除必须可核对。

    **Why**：`EXPLAINED` 是一张"筛子看走眼了"的表，而它唯一的价值是**每一条都能核对**：
    键指着的那处容量还在不在？理由是不是一句话说得清的事实？少一条核对，它就从"说明"
    变成"把数改小"的手法。

    **How to apply**：键是 `(文件, 常量名)` 且必须真的出现在 `scan()` 的输出里 ——
    容量被删掉、被改名、或者那处用法改了，这条就红（2026-10-16 写这张表时当场抓到一条：
    `TY_K_NAMED` 修掉移位的假阳性之后**整个退出**了口径，那条记录于是必须删掉，不是留着）。
    """
    rows = loment_caps.scan()
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
