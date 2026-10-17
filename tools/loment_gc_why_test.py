#!/usr/bin/env python3
"""loment_gc_why_test.py — `tools/loment_gc_why.py` 的判据（`docs/225` §4.③）。

四条，第一条是**命根子**：

* **A 对账** —— 语料里每一份模块，本工具数出来的四层直方图**必须等于**
  `lomentc._count_gc_ladder()` 的四个数。**这一条把"尺子"钉在编译器上**：报告与编译器的
  分类只要漂一点点就红。没有它，这份报告第二天就会开始说谎。
* **B 理由完备** —— `L3` 的每一行都要有 `REASONS` 里的一条键；`L0`/`L1`/`L2` 的每一行
  `reason` 必须是 `None`（成功分层没有"原因"）。
* **C 一对程序** —— 只差一行的两份程序，落层要变、且理由要点名到**对的那一类**。
  （照 `docs/210` §5 的规矩：只钉"两个数相等"的不算，要一对程序。）
* **D 非空转** —— 语料上四层**都**得出现过非零。否则这套东西测的是一个不会发生的输入。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_gc_why as W  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _corpus() -> list[Path]:
    """语料 = `loment/examples/*.lomt` 里**装得进来**的那些。

    装不进来的跳过（multisyntax 的多数门要前门，这里只走 `.lomt` 原生那一条），
    但**至少**要有 12 份 —— 否则这条判据是在空转。
    """
    out = []
    cands = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    # ⚠ **唯一一份 alpha 语料在子目录里**：`examples/*.lomt` 是单层 glob，够不到它。
    # 少了它，语料里的 L0/L1/L2 恒为 0，判据 D 会空转（实测：一开始就是这么红的）。
    cands += [ROOT / "loment" / "examples" / "gc_ladder" / "main.lomt"]
    for p in cands:
        try:
            lomentc.load(p)
        except Exception:  # noqa: BLE001
            continue
        out.append(p)
    assert len(out) >= 12, f"语料只剩 {len(out)} 份 —— 这条判据在空转"
    return out


@test
def test_report_agrees_with_the_compiler():
    """判据 A：**对账**。报告的四层直方图 == `_count_gc_ladder` 的四个数。"""
    n = 0
    for p in _corpus():
        m = lomentc.load(p)
        got = W.histogram(W.sites(m))
        want = lomentc._count_gc_ladder(m)
        got = {k: got[k] for k in ("l0", "l1", "l2", "l3", "total_sites")}
        assert got == want, f"{p.name}: 报告 {got} != 编译器 {want}"
        n += 1
    print(f"      {n} 份模块：四层直方图与编译器逐份相同")


@test
def test_every_row_is_explained():
    """判据 B：`L3` 行必有一条具名理由；其余行的 `reason` 必为 `None`。"""
    for p in _corpus():
        for r in W.sites(lomentc.load(p)):
            if r["rung"] == "L3":
                assert r["reason"] in W.REASONS, f"{p.name}:{r['line']} 理由非法 {r['reason']!r}"
            else:
                assert r["reason"] is None, f"{p.name}:{r['line']} 成功分层不该有理由"


@test
def test_a_pair_of_programs_changes_the_reason():
    """判据 C：**一对（三份）程序**，只差一行 —— 落层要变，理由要点到对的那一类。

    ⚠ **写这一条时我自己的期望错了一次，记在这里**：我原以为"尺寸不是字面量"会落 `L3`，
    实测落 **`L1`** —— 因为 `L1`（定活）的资格与 `L0` **是同一条**（`_ptr_only_uses`），
    它**不要求尺寸是字面量**。所以"尺寸不是字面量"只说明 **`L0` 没收**，不说明没人收。
    ⇒ 报告因此把 **`l0_refused` 对每一行都给**（不是 `L3` 才给）：一块落在 `L1` 而没上栈的
    程序，用户要看的正是这一句。**这个区别就是这份报告存在的理由**。
    """
    NL = chr(10)
    head = "module m" + NL + "choose gc_auto_alpha" + NL + "choose runtime" + NL + NL

    def write(body: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="gcwhy-"))
        p = d / "x.lomt"
        p.write_text(head + body, encoding="utf-8", newline=NL)
        return p

    def row_of(p: Path) -> dict:
        rs = [r for r in W.sites(lomentc.load(p)) if r["name"] == "p"]
        assert len(rs) == 1, rs
        return rs[0]

    # ① 字面量尺寸 + 不外逃 ⇒ **L0**（提升到栈），没有"L0 没收"这一说
    a = row_of(write("fn f() -> u32 {" + NL + "    let p: ptr = alloc(8);" + NL
                     + "    store8(p, 0, 1);" + NL + "    return 0;" + NL + "}" + NL))
    assert a["rung"] == "L0" and a["l0_refused"] is None, a

    # ② **只把尺寸换成形参** ⇒ L0 拒、**L1 接住**，而报告必须说出"L0 没收：尺寸不是字面量"
    b = row_of(write("fn f(n: u32) -> u32 {" + NL + "    let p: ptr = alloc(n);" + NL
                     + "    store8(p, 0, 1);" + NL + "    return 0;" + NL + "}" + NL))
    assert b["rung"] == "L1", b
    assert b["l0_refused"] == "size-not-literal", b

    # ③ 尺寸仍是字面量，但**名字真外逃** ⇒ 两层都拒 ⇒ L3，理由是"外逃"
    #
    # ⚠ **这一条也写错过一次**：被调当时写成 `fn eat(p: ptr) -> u32 { return 0; }` ——
    # 而那个函数**压根不碰** `p`，于是按 deref-only 那条放宽（`docs/210` §7）它是**安全被调**，
    # L0 照样提升。**要让名字真外逃，被调得把它交出去**（这里：把它返回）。
    c = row_of(write("fn eat(p: ptr) -> ptr {" + NL + "    return p;" + NL + "}" + NL + NL
                     + "fn f() -> u32 {" + NL + "    let p: ptr = alloc(8);" + NL
                     + "    let q: ptr = eat(p);" + NL + "    store8(q, 0, 1);" + NL
                     + "    return 0;" + NL + "}" + NL))
    assert c["rung"] == "L3", c
    assert c["reason"] == "escape", c
    print("      三份只差一行的程序：L0 → L1(L0 因尺寸不是字面量没收) → L3(外逃)")


@test
def test_all_four_rungs_actually_occur():
    """判据 D：**非空转** —— 语料上四层都得出现过非零。"""
    seen = {"l0": 0, "l1": 0, "l2": 0, "l3": 0}
    for p in _corpus():
        h = W.histogram(W.sites(lomentc.load(p)))
        for k in seen:
            seen[k] += h[k]
    missing = [k for k, v in seen.items() if v == 0]
    assert not missing, f"这几层在语料上一次都没出现 —— 判据在空转: {missing}"
    print(f"      语料合计：{seen}")


def main() -> int:
    return loment_suite.run(TESTS, "loment_gc_why_test")


if __name__ == "__main__":
    sys.exit(main())
