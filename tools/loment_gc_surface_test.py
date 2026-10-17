#!/usr/bin/env python3
"""判据：`docs/210` §7 那个"最该先量的数"是真量出来的，不是空转。

`tools/loment_gc_surface.py` 把仓里自己的 Loment 代码跑成 `gc_auto_alpha`，读 v10 的
`gc_ladder` 加总。这一格钉三件事：

1. **注入真的把 manual 翻成 alpha**（`_inject` 的单元行为）—— 不然量的是 `gc_manual`，
   那 `gc_ladder` 恒为 `{0,0,0,total}`，整支尺子量的是空气；
2. **语料里真有站点、真有分层**（非空转）：站点总数、L0、L3 都非零；
3. **确定性**（两次跑逐字节相同）。

**不钉逐格精确数**：语料是**活的**（示例会加、会改），钉死数字等于让**别人**的示例改动
把这一格顶红 —— 而红在这里**不是**回归。要的是"尺子还量得出东西、分层还在"，精确数由
`--json` 报出来给人看（CI 日志里也有）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import loment_gc_surface as surf  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_injection_flips_manual_to_alpha():
    """没有 `choose gc` 的源 → 在 `module` 行之后插两个 `choose`；已有 `choose gc` 的 → 不碰。"""
    got = surf._inject("module m\n\nfn f() -> u32 {\n    return 0;\n}\n")
    lines = got.split("\n")
    assert lines[1] == "choose gc_auto_alpha" and lines[2] == "choose runtime", lines[:3]
    assert surf._inject("module m\nchoose gc_manual\n") is None, "已表态的不该被注入"
    assert surf._inject("not a loment unit\n") is None, "没有 module 行不该被注入"


@test
def test_surface_is_non_vacuous_and_layers_fire():
    """语料里**真有** alloc 站点，而且 L0 与 L3 都**真的**接住了东西（尺子没空转）。"""
    rows = surf.measure()
    t = surf.total(rows)
    print(f"      证明面：L0={t['l0']} L1={t['l1']} L2={t['l2']} L3={t['l3']} "
          f"/ total={t['total_sites']}")
    assert t["total_sites"] >= 20, f"语料里 alloc 站点太少了，尺子量的是空气：{t}"
    assert t["l0"] >= 3, f"L0 一格都没接住 —— 提升那一层坏了？{t}"
    assert t["l3"] >= 1, f"没有残差？那 L3 那半的判据也没意义了：{t}"
    # `gc_ladder` 的自洽（promise 由 v10 保证，这里顺带钉一把）
    assert t["l0"] + t["l1"] + t["l2"] + t["l3"] == t["total_sites"], t
    # 已经在源里写了 `choose gc` 的（`gc_ladder` 那份）必须被跳过，不是被硬翻
    assert any(g.get("skipped") for _rel, g in rows), "该跳过的那份不见了"


@test
def test_measure_is_deterministic():
    """两次量出同一个东西 —— 尺子本身不许有随机/顺序依赖。"""
    assert surf.measure() == surf.measure()


def main() -> int:
    return loment_suite.run(TESTS, "loment_gc_surface_test")


if __name__ == "__main__":
    sys.exit(main())
