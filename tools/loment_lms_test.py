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
