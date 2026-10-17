#!/usr/bin/env python3
# loment_genesis_test.py — 链条的起点: genesis 起头能不能在**没有 clang** 的环境里跑通
# (docs/167 §5 ②, 审计主张 C21)
#
# 判据两条:
#   1. genesis 二进制与 `loment/build/genesis/SHA256SUMS` 的哈希一致, 且 `bootstrap.sh` 真的引用它;
#   2. 在**把 clang 从 PATH 里拿掉**的环境下跑 `sh loment/bootstrap.sh`, 四条证明全过
#      (种子自复现 + 三阶段定点 + 非自身入口一致)。
#
# 第二条是这条主张的全部意思: 重建这套工具链**不需要 C 编译器, 也不需要解释器** ——
# 链条上每一条 `.ll` 都由 genesis (提交进仓库的那个二进制) 自己汇编。
#
# 运行: python tools/loment_genesis_test.py   (无 WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_genesis  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []

#: genesis 的源（自举镜像 `loment/tools/lomelf.lomt`）。判据从这里**读**它的常量，
#: 不另抄一份 —— 与 `loment_p8_test::test_m85_codegen_table_capacity` 同一条纪律：
#: 表放大了而守卫还卡在旧数，与反过来一样坏。
LOMELF = ROOT / "loment" / "tools" / "lomelf.lomt"


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


@test
def test_genesis_hash_and_reference():
    """genesis 的哈希与它自己的 SHA256SUMS 一致, 且 bootstrap.sh 引用了它。"""
    rc = loment_genesis.check()
    assert rc == 0, "loment_genesis --check 未通过 (缺文件 / 哈希不符 / 启动脚本没引用)"
    assert loment_genesis.GEN.exists(), "缺 genesis 二进制"
    print(f"      genesis {loment_genesis.GEN.stat().st_size} B, 哈希一致, bootstrap.sh 引用它")


@test
def test_seed_fits_lomelf_input_buffer():
    """种子的 `.ll` 必须装得进 lomelf 的输入缓冲 —— 越界是**静默**的，所以要有判据。

    2026-09-18 实测撞上的（`docs/192`）：`read_all(fd, inb, IN_CAP)` 读满 `IN_CAP` 就
    返回，**不检查文件还有没有剩**，于是超限的 `.ll` 被**悄悄截断**，汇编出来的是一个
    **启动就段错误**的可执行文件（不是报错）。同一份 `.ll`：

        genesis 汇编 -> rc=139 (段错误)        clang 汇编 -> rc=0, 产物正常

    而当时驱动单元的 `.ll` 已经涨到**离 2 MiB 只剩 1377 B** —— 它不是"很远的容量"，
    是**下一次改动就撞**的东西。这条判据把它变成一条看得见的预算。

    **它只报数、不抬上限**：越界仍然是静默的，修法（拒绝 / 抬布局）见 `docs/192` §5。
    """
    src = LOMELF.read_text(encoding="utf-8")
    m = re.search(r"const IN_CAP: u32 = (\d+);", src)
    assert m, f"在 {LOMELF.name} 里找不到 `const IN_CAP: u32 = <数>;`"
    cap = int(m.group(1))
    got = loment_genesis.SEED.stat().st_size
    assert got < cap, (
        f"种子 {got} B 超过 lomelf 输入上限 {cap} B (超 {got - cap} B)。"
        f"**越界不报错, 而是静默截断**成一个启动就崩的二进制 (docs/192) —— "
        f"要么把驱动单元改小, 要么抬 lomelf 的输入缓冲 (那一动要连 M_TXT/M_OUT/M_GB/"
        f"M_TAB/M_OBJ 与 ARENA 整条布局一起改)")
    print(f"      种子 {got} B / lomelf 输入上限 {cap} B, 余量 {cap - got} B")


def _seed_table_counts() -> dict[str, int]:
    """按 `lomelf.lomt` 的**表模型**数种子 `.ll` 要用掉多少格。

    三张表都是"单调 bump + 越界踩下一张"，所以要数的正是各自的**入表次数**：

    * **全局表** —— 每条 `@x = ` 一条（`gadd` 一次）。参考实现给**每个字符串字面量站点**
      发一个 `.str.<函数>.<序号>` 全局、不按内容去重，所以"多几十个字面量"就是"多几十条"。
    * **标签表** —— 全局 + `declare` + `define` + 基本块名（`lbl_add` 的四类调用点）。
    * **回填表** —— 每次 `call` / `br` 一条，**加上"每个函数里引用到的全局"各一条**
      （`em_mov_abs`；槽表按函数去重，所以同一个全局在一个函数里多次引用只算一次）。
      这个模型用旧上限验过：旧上限 16384 下 HEAD 的种子算出来 13634（**过**），
      而 2026-09-20 那版种子算出来 15686（**当场顶破**）—— 与实际行为一致。
    """
    s = loment_genesis.SEED.read_text(encoding="utf-8")
    globals_ = len(re.findall(r"(?m)^@[-A-Za-z0-9_.]+\s*=", s))
    decls = len(re.findall(r"(?m)^declare", s))
    defines = len(re.findall(r"(?m)^define", s))
    blocks = len(re.findall(r"(?m)^[-A-Za-z0-9_.]+:", s))
    calls = len(re.findall(r"(?m)^\s*(?:%\S+ = )?call ", s))
    brs = len(re.findall(r"(?m)^\s*br ", s))
    fix = 0
    for m in re.finditer(r"(?ms)^define[^\n]*@([-A-Za-z0-9_.]+)\(.*?\n\}", s):
        body = m.group(0)
        refs = set(re.findall(r"@[-A-Za-z0-9_.]+", body))
        refs.discard("@" + m.group(1))
        targets = set(re.findall(r"call [^\n]*?@([-A-Za-z0-9_.]+)\(", body))
        fix += len({r for r in refs if r[1:] not in targets})
    return {"globals": globals_, "labels": globals_ + decls + defines + blocks,
            "fixup": calls + brs + fix}


@test
def test_seed_fits_lomelf_table_caps():
    """种子的**三张表**都要装得进 `lomelf` 的容量 —— 越界是**静默**的，所以要有判据。

    2026-09-20 实测（`docs/200`）：`lomelf.lomt` 的零碎表都是**单调 bump、没有边界检查**，
    越界就踩进下一张表。那次是**全局表**先满（1801 → 2086，上限 2048）—— 报出来的却是
    `lomelf: 未定义的标签: `（空白标签名），因为全局表写进了**标签表**。抬了全局表之后
    又当场撞上**回填表**（上限 16384）。两处都抬了，并且各 `*_add` 都补了守卫。

    这条判据的作用是**把余量变成看得见的预算**：与 `test_seed_fits_lomelf_input_buffer`
    同一个形状（从 `lomelf.lomt` 里**读**上限，不另抄一份）。
    """
    src = LOMELF.read_text(encoding="utf-8")
    caps = {}
    for name in ("TB_G_MAX", "TB_LBL_MAX", "TB_FIX_MAX"):
        m = re.search(rf"const {name}: u32 = (\d+);", src)
        assert m, f"在 {LOMELF.name} 里找不到 `const {name}: u32 = <数>;`"
        caps[name] = int(m.group(1))
    got = _seed_table_counts()
    pairs = [("globals", "TB_G_MAX", "全局表"), ("labels", "TB_LBL_MAX", "标签表"),
             ("fixup", "TB_FIX_MAX", "回填表")]
    for key, cap, label in pairs:
        assert got[key] < caps[cap], (
            f"种子的{label}要 {got[key]} 条，超过 {cap}={caps[cap]}（超 {got[key] - caps[cap]}）。"
            f"**越界不报错**（单调 bump，会写进下一张表）—— 抬 `{cap}`，见 docs/200")
        print(f"      {label} {got[key]} / {caps[cap]}, 余量 {caps[cap] - got[key]}")


@test
def test_the_text_window_is_the_same_number_on_both_sides():
    """文本段窗口：两个实现必须用**同一个数**（`docs/200` §9）。

    数据段钉在 `TEXT_VADDR + <代码缓冲容量>`（2026-10-16 起，原先钉在 `0x600000` 那 2 MiB）。
    两边要是**各抄一个数**，产物就不再逐字节相同 —— 而症状是 `loment_elf_test` 里那一堆
    "镜像与参考不同（8216B vs 8216B）"：**字节数一样**，内容不同，最难读的一类红。
    所以在这里把两个数**对起来**，红了就直接说清是哪一边飘了。
    """
    src = LOMELF.read_text(encoding="utf-8")
    m = re.search(r"const OUT_CAP: u32 = (\d+);", src)
    assert m, "在 lomelf.lomt 里找不到 `const OUT_CAP: u32 = <数>;`"
    out_cap = int(m.group(1))
    m = re.search(r"const DATA_VADDR: u32 = (\d+);", src)
    assert m, "在 lomelf.lomt 里找不到 `const DATA_VADDR: u32 = <数>;`"
    data_vaddr = int(m.group(1))
    m = re.search(r"const TEXT_VADDR: u32 = (\d+);", src)
    assert m, "在 lomelf.lomt 里找不到 `const TEXT_VADDR: u32 = <数>;`"
    text_vaddr = int(m.group(1))
    ref = (ROOT / "tools" / "lomelf.py").read_text(encoding="utf-8")
    m = re.search(r"^TEXT_CAP = (\d+)", ref, re.M)
    assert m, "在 tools/lomelf.py 里找不到 `TEXT_CAP = <数>`"
    py_cap = int(m.group(1))
    m = re.search(r"^ELF_DATA_VADDR = ELF_TEXT_VADDR \+ TEXT_CAP", ref, re.M)
    assert m, "tools/lomelf.py 的 `ELF_DATA_VADDR` 不是 `ELF_TEXT_VADDR + TEXT_CAP` 了"
    assert out_cap == py_cap, (
        f"代码缓冲容量两边不是一个数：lomelf.lomt 的 OUT_CAP={out_cap}，"
        f"tools/lomelf.py 的 TEXT_CAP={py_cap}")
    assert data_vaddr == text_vaddr + out_cap, (
        f"数据段不在代码缓冲之后：DATA_VADDR={data_vaddr}，"
        f"TEXT_VADDR+OUT_CAP={text_vaddr + out_cap}（docs/200 §9）")
    print(f"      两边同一个数：代码缓冲 {out_cap}，"
          f"数据段钉在 0x{data_vaddr:x}（文本段可用 {out_cap}）")


@test
def test_bootstrap_needs_no_clang():
    """把 clang 从 PATH 拿掉, `sh loment/bootstrap.sh` 仍要跑通四条证明。"""
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    boot = ROOT / "loment" / "bootstrap.sh"
    assert boot.exists(), "缺 bootstrap.sh"
    # PATH 只留 /usr/bin:/bin (WSL 的 clang 不在里面); 显式把 LOMENT_USE_CLANG 清掉
    script = (f"cd {_wsl_path(ROOT)} && unset LOMENT_USE_CLANG CC && "
              f"env PATH=/usr/bin:/bin sh loment/bootstrap.sh")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=1800, shell=False)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"bootstrap 失败 rc={r.returncode}: {out[-400:]!r}"
    assert "genesis" in out, f"起点不是 genesis: {out[:400]!r}"
    assert "SEED BOOTSTRAP OK" in out, f"没有跑到结论行: {out[-300:]!r}"
    # 与**仓库里那份种子**比, 不写死字节数 —— 原先钉的是 1630436, 种子一改就变成一条
    # 要人去猜的假红 (2026-09-15 改了 codegen, 种子涨到 1631085 就撞上了)。
    # 这样仍然是棘轮: bootstrap 复现出来的种子必须**和提交的那份一样大**;
    # 而"提交的那份对不对"由 loment_seed_test 对着参考实现管。
    want = loment_genesis.SEED.stat().st_size
    assert str(want) in out.replace(" ", "").replace(",", ""), (
        f"bootstrap 复现的种子字节数 != 仓库里那份 ({want}): {out[-200:]!r}")
    print("      PATH 里没有 clang, bootstrap 仍四条全过 (genesis 起头)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_genesis_test")


if __name__ == "__main__":
    sys.exit(main())
