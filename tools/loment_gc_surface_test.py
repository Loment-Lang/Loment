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
import tempfile
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
    assert surf._inject("module m\nchoose mode no_std\n") is None, (
        "`no_std` 与 `gc_auto` 冲突，注入只会让这份源自检失败 —— 该跳过，不该硬翻")
    assert surf._inject("module m\nchoose no_std\n") is None, "简写 `choose no_std` 也是表态"
    assert surf._inject("not a loment unit\n") is None, "没有 module 行不该被注入"


@test
def test_surface_is_non_vacuous_and_layers_fire():
    """语料里**真有** alloc 站点，而且 L0 与 L3 都**真的**接住了东西（尺子没空转）。"""
    rows = surf.measure()
    t = surf.total(rows)
    print(f"      证明面：L0={t['l0']} L0.5={t['l0_5']} L1={t['l1']} L2={t['l2']} "
          f"L3={t['l3']} / total={t['total_sites']}")
    assert t["total_sites"] >= 20, f"语料里 alloc 站点太少了，尺子量的是空气：{t}"
    assert t["l0"] >= 3, f"L0 一格都没接住 —— 提升那一层坏了？{t}"
    assert t["l3"] >= 1, f"没有残差？那 L3 那半的判据也没意义了：{t}"
    # `#1479`：L2 要有语料接住。原先 alpha 语料被整份跳过，L2 恒为 0 ——
    # 把 L2 那一层删了这条判据也不会红。（L0.5 语料里没有产出者，另有一条夹具判据。）
    assert t["l2"] >= 1, f"L2 一次都没出现 —— 纪元那一层坏了，或者 alpha 语料又被跳过了：{t}"
    # `gc_ladder` 的自洽（promise 由 v10 保证，这里顺带钉一把）。**五格都要算** ——
    # 原先漏了 L0.5：语料里第一份有 L0.5 的单元会让这条在**正确的代码**上变红（`#1479`）。
    assert (t["l0"] + t["l0_5"] + t["l1"] + t["l2"] + t["l3"]
            == t["total_sites"]), t
    # 声明了别的档（`gc_manual` 等）的源必须被跳过，不是被硬翻
    assert any(g.get("skipped") and not g.get("why") for _rel, g in rows), (
        "声明了别的档的那几份没有被跳过")


@test
def test_the_l05_layer_is_reachable():
    """`#1479`：L0.5（函数级栈区）要有**能产出它的**判据 —— 语料里没有两处 L0 的函数。

    夹具是最小形状：一个函数、两处字面量尺寸且不外逃的 `alloc` ⇒ 两处都进同一条栈区。
    """
    src = ("module m" + chr(10) + "choose gc_auto_alpha" + chr(10) + "choose runtime"
           + chr(10) + chr(10)
           + "fn f() -> u32 {" + chr(10)
           + "    let a: ptr = alloc(8);" + chr(10) + "    store8(a, 0, 1);" + chr(10)
           + "    let b: ptr = alloc(8);" + chr(10) + "    store8(b, 0, 2);" + chr(10)
           + "    return 0;" + chr(10) + "}" + chr(10))
    with tempfile.TemporaryDirectory() as tds:
        p = Path(tds) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        lad = surf._ladder_of(p)
    assert not lad.get("skipped"), lad
    assert lad["l0_5"] == 2 and lad["l0"] == 0, lad
    print("      两处 L0 站点共用一个函数级栈区（L0.5 = 2）")


@test
def test_measure_is_deterministic():
    """两次量出同一个东西 —— 尺子本身不许有随机/顺序依赖。"""
    assert surf.measure() == surf.measure()


def main() -> int:
    return loment_suite.run(TESTS, "loment_gc_surface_test")


if __name__ == "__main__":
    sys.exit(main())
