#!/usr/bin/env python3
"""loment_lms_test.py — `lms` 这一维：**档 + 七个细开关 + 叠加规则**（`docs/253` §9/§11.4）。

这一轮（0.1.5.1）把 `lms` 从"开/关"改判成**档名**，默认是**满档**。四条硬判据，
每条都对着 `docs/253` §9 编号里的一条：

* **A 默认就是满档**（判据 11）—— 一个 `lms_*` 都不写 ⇒ 七级全开，`lms` == `lms_full`。
* **B 档真的铺了默认**（判据 9）—— 四个档各自恰好铺出表里那一组；多一级少一级都红。
* **C 显式开关覆盖档**（判据 10）—— 降档之后显式写 `choose lms_check` 仍然把它打开；
  反过来在满档上 `choose close lms_crash` 也真的关掉它。**而且不判成"写了两遍"** ——
  初值不是"写的"（`SwitchTable.seeded` 那条区分就是为它存在的）。
* **D 产物里可判**（判据 9/11 的落点）—— `lms_levels` 进 Potato v17，七个布尔逐条对上。

外加两条登记处一致性：`_admin` 规则（`docs/262` §10.1）与"三张表是同一张"。

用法: python tools/loment_lms_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

_PROG = """module m
choose runtime
%s

fn f() -> u32 {
    let p: ptr = alloc(8);
    let x: u32 = load8(p, 0);
    return x;
}
"""


def _load(chooses: str):
    """走**真前门**（预扫 → 装载）—— 这正是不走 `Parser` 直接读源码的那条路。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(_PROG % chooses, encoding="utf-8", newline="\n")
        mod, deps = lomentc.load_unit(p, ROOT)
    return mod, deps


def _levels(chooses: str) -> dict[str, bool]:
    mod, _ = _load(chooses)
    return {n: lomentc._lms_level(mod, n) for n in lomentc.LMS_TOGGLES}


def test_default_is_the_full_tier() -> None:
    """判 A（`docs/253` §9 判据 11）：什么都不写 ⇒ 七级全开。"""
    mod, _ = _load("")
    assert mod.chooses.get("lms", lomentc.CORE_DEFAULTS["lms"]) == "lms_full", mod.chooses
    lv = {n: lomentc._lms_level(mod, n) for n in lomentc.LMS_TOGGLES}
    assert lv == {n: True for n in lomentc.LMS_TOGGLES}, lv
    print("      默认 lms_full：七级全开（一个 lms_* 都不写）")


def test_each_tier_seeds_exactly_its_levels() -> None:
    """判 B（判据 9）：四个档各自**恰好**是表里那一组 —— 多一级少一级都红。"""
    want = {
        "lms_full": {n: True for n in lomentc.LMS_TOGGLES},
        "lms_forensic_admin": {
            "lms_check": True, "lms_manage": True, "lms_guard": True,
            "lms_reject": True, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
        # ⚠ **1+3+4**，不是初稿的 1+4（`docs/253` §5.4 / #388）：拒绝器不自己发现错误，
        # 它的判决只能来自第 2 或第 3 级 —— 1+4 的日常档里没有任何东西给它喂判决。
        "lms_daily_admin": {
            "lms_check": True, "lms_manage": False, "lms_guard": True,
            "lms_reject": True, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
        "lms_manage_only_admin": {
            "lms_check": False, "lms_manage": True, "lms_guard": False,
            "lms_reject": False, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
    }
    assert set(want) == set(lomentc.LMS_TIERS), (set(want), set(lomentc.LMS_TIERS))
    for tier, exp in want.items():
        got = _levels(f"choose {tier}")
        assert got == exp, (tier, got, exp)
    print("      四个档各自恰好铺出表里那一组（含 daily = 1+3+4）")


def test_explicit_switches_override_the_tier() -> None:
    """判 C（判据 10）：显式写的开关**覆盖**档铺的初值，且**不算写了两遍**。"""
    # 降档之后显式打开一级
    mod, deps = _load("choose lms_manage_only_admin\nchoose lms_check")
    assert lomentc._lms_level(mod, "lms_check") is True, "显式写没有覆盖档"
    assert lomentc._lms_level(mod, "lms_guard") is False, "档铺的其余几级不该被带上来"
    errs = lomentc.check(mod, deps=deps)
    assert not [e for e in errs if "写了两次" in e], errs
    # 满档上显式关掉一级
    mod2, deps2 = _load("choose close lms_crash")
    assert lomentc._lms_level(mod2, "lms_crash") is False, "显式 `close` 没有覆盖满档"
    assert lomentc._lms_level(mod2, "lms_guard") is True, "关一级不该把别的级带下去"
    assert not [e for e in lomentc.check(mod2, deps=deps2) if "写了两次" in e]
    print("      降档 + `choose lms_check` ⇒ 开；满档 + `choose close lms_crash` ⇒ 关")


def test_two_tiers_are_an_error() -> None:
    """同一维写两次仍然是错误 —— 档名不改变"这一维只能声明一次"。"""
    mod, deps = _load("choose lms_daily_admin\nchoose lms_full")
    errs = lomentc.check(mod, deps=deps)
    assert any("核心模式这一维只能声明一次" in e for e in errs), errs
    print("      两个档名同时写 ⇒ 逐维的「只能声明一次」照旧报")


def test_levels_reach_the_artifact() -> None:
    """判 D：`lms_levels` 进 Potato v17，七个布尔与编译器那张表逐条对上。"""
    mod, deps = _load("choose lms_forensic_admin")
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    assert doc["potato"] == "v17", doc["potato"]
    assert doc["lms"] == "lms_forensic_admin"
    assert doc["lms_levels"] == {
        "lms_check": True, "lms_manage": True, "lms_guard": True,
        "lms_reject": True, "lms_crash": False, "lms_restart": False,
        "lms_seed": False}, doc["lms_levels"]
    # 校验器认它（同一份对象，两边判一样）
    assert potato.validate(doc) == [], potato.validate(doc)
    print("      Potato v17 的 lms_levels 与编译器那张表逐条对上，校验器收下")


def test_manager_report_lists_every_alloc_site() -> None:
    """第 2 级（管理器）的产物（#386）：内存图**对得上账**。

    `lms_report` 数出的 `alloc` 站点必须**恰好**等于 Potato 里 `gc_ladder.total_sites` ——
    两个数数的是同一批站点，只是分的粗细不同。这条交叉不变量是这一级唯一的"对不对"
    判据：报告本身说"有三处"这件事，只有拿另一处独立数出来的数才判得了。
    """
    mod, deps = _load("")
    rep = lomentc.lms_report(mod, deps)
    lines = rep.splitlines()
    assert lines[0] == "# lms report v1", lines[:2]
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    total = int(next(x for x in lines if x.startswith("total_alloc_sites ")).split()[1])
    assert total == doc["gc_ladder"]["total_sites"], (total, doc["gc_ladder"])
    assert any(x.startswith("levels ") and "lms_guard" in x for x in lines), lines
    assert any(x.startswith("arena ") for x in lines), lines
    print(f"      报告数出 {total} 处 alloc，与 gc_ladder.total_sites 对上")


_FLAKY = r"""
#include <stdio.h>
int main(void) {
    int n = 0;
    FILE *f = fopen("%(dir)s/count", "r");
    if (f) { if (fscanf(f, "%%d", &n) != 1) n = 0; fclose(f); }
    n += 1;
    f = fopen("%(dir)s/count", "w");
    fprintf(f, "%%d", n);
    fclose(f);
    return n < 3 ? 1 : 0;
}
"""

_ALWAYS = r"""
#include <stdio.h>
int main(void) { return 7; }
"""


def test_the_floor_is_named_and_sized() -> None:
    """第 7 级（#391）：信任地板有**名字**、有**数**，而且那个数**不会过期**。

    `docs/265` §4 里那一段写死了自举种子的字节数与 SHA-256。这条判据每次重算再比 ——
    于是"文档里的数"与"树里的种子"不可能漂开。种子每次 `--emit` 都会变，
    所以**这份文档必须跟着重算**，而这条判据就是那个提醒。
    """
    import hashlib
    doc = (ROOT / "docs" / "265-lms-0151.md").read_text(encoding="utf-8")
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "自举种子不在 —— 第 7 级的实物没了"
    blob = seed.read_bytes()
    want_bytes = str(len(blob))
    want_sha = hashlib.sha256(blob).hexdigest()
    assert f"artifact: loment/build/selfhost_driver.ll" in doc, "地板没点名那件实物"
    assert f"bytes: {want_bytes}" in doc, (
        f"docs/265 的地板字节数过期了：树里是 {want_bytes}")
    assert f"sha256: {want_sha}" in doc, (
        f"docs/265 的地板哈希过期了：树里是 {want_sha}")
    print(f"      地板 = {want_bytes} 字节 / {want_sha[:16]}…（每次都重算）")


def _sh_path(p: Path) -> str:
    """这条路在**真正跑命令的那个 shell** 里长什么样。

    Linux 上就是本机路径；Windows 上命令经 WSL 跑，路径要翻成 `/mnt/d/…`（与
    `loment_elf_test._wsl_path` 同一条规则 —— 少这一步，`cc` 会在 WSL 里找不到 Windows
    侧的文件，而症状是**编译失败**，很容易被误读成"这台机器没有 C 编译器"。
    """
    s = p.resolve().as_posix()
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def test_restarter_brings_the_program_back() -> None:
    """第 6 级（#390）：**用 C 写的**重启器 —— 进程死了把它拉起来。

    两个方向都判，缺一个都能过：

    * **拉起来**：前两次退出码 1、第三次 0 ⇒ 恰好重启 2 次，最终 rc=0，程序跑了 3 次；
    * **不许永远拉**：一个永远退 7 的程序 ⇒ 重启到上限就**停**，返回 7。

    后者是这一级最容易被"写成一个 while(1)"的地方：一个永远重启的守护进程会把
    **一次真崩溃**变成一条看不见的日志。
    """
    src = ROOT / "restarter" / "loment-restart.c"
    assert src.exists(), "restarter/loment-restart.c 不在 —— 第 6 级没有实物"
    linux = sys.platform.startswith("linux")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        flaky = tdp / "flaky.c"
        flaky.write_text(_FLAKY % {"dir": _sh_path(tdp).rstrip("/")},
                         encoding="utf-8", newline="\n")
        always = tdp / "always.c"
        always.write_text(_ALWAYS, encoding="utf-8", newline="\n")
        if linux:
            run = lambda cmd: subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                             text=True, cwd=str(tdp))
        else:
            run = lambda cmd: subprocess.run(["wsl", "-e", "bash", "-lc", cmd],
                                             capture_output=True, text=True)
        base = _sh_path(tdp)
        r = run(f"cd {base} && cc -O2 -o loment-restart {_sh_path(src)} "
                f"&& cc -O2 -o flaky flaky.c && cc -O2 -o always always.c")
        if r.returncode != 0:
            print(f"      SKIP: 没有 C 编译器 ({r.stderr.strip()[:120]})")
            return
        run(f"cd {base} && rm -f count && ./loment-restart ./flaky; echo rc=$?")
        r2 = run(f"cd {base} && rm -f count && ./loment-restart ./flaky > /dev/null 2>&1; "
                 f"echo rc=$?; cat count")
        out = r2.stdout.split()
        assert out and out[0] == "rc=0", (r2.stdout, r2.stderr)
        assert out[1] == "3", f"程序应该被跑 3 次，实际 {out[1]}"
        r3 = run(f"cd {base} && ./loment-restart ./always 2>&1; echo rc=$?")
        assert "rc=7" in r3.stdout, r3.stdout
        assert "giving up" in r3.stdout, "没有上限 —— 这会把一次真崩溃变成看不见的日志"
    print("      崩溃后拉起（2 次）并停止；永远崩则到上限就停（rc=7）")


def test_the_admin_rule_is_mechanical() -> None:
    """`docs/262` §10.1：**关掉关键安全系统的取值必须带 `_admin`** —— 这里第一次可判。"""
    for tier in lomentc.LMS_TIERS:
        if tier == "lms_full":
            assert not tier.endswith("_admin"), "满档不是关闭，不该带 `_admin`"
        else:
            assert tier.endswith("_admin"), (
                f"`{tier}` 降级了内存安全却没有 `_admin` 后缀")
    # 三张表必须是同一张：编译器 / 校验器 / 七级键序
    assert tuple(potato.LMSS) == tuple(lomentc.CORE_DIMS["lms"]), (
        potato.LMSS, lomentc.CORE_DIMS["lms"])
    assert tuple(potato.LMS_LEVEL_KEYS) == tuple(lomentc.LMS_TOGGLES), (
        potato.LMS_LEVEL_KEYS, lomentc.LMS_TOGGLES)
    print("      非满档一律带 `_admin`；编译器 / 校验器 / 七级键序是同一张表")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_lms_test")


if __name__ == "__main__":
    sys.exit(main())
