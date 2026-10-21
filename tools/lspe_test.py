#!/usr/bin/env python3
"""`loment/lspe/` 的包级判据（`docs/262`）。

端到端判据仍留在 `lomentc_test` / `loment_potato_emit_test`：它们量的是编译器**把**
LSPE 接进去以后的行为。这一套量的是子系统自己的表面 —— 登记表、证据格、求解器、
普查规则与台账形状 —— 不经过编译器。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loment"))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lspe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_registry_has_14_obligations_and_8_evidence_kinds():
    assert len(lspe.LSPE_OBLIGATIONS) == 14
    assert len(lspe.LSPE_EVIDENCE_KINDS) == 8
    for name, spec in lspe.LSPE_OBLIGATIONS.items():
        assert spec["owner"], name
        assert spec["accept"], name
        assert "Differential" not in spec["accept"], name


@test
def test_level_only_lowers_what_is_proved():
    """L0 不认 Typed；L1 认；Interproc 要到 L2。级别只缩不放。"""
    sites = [
        {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
         "evidence": "Typed", "line": 1},
        {"id": "m:call:2", "kind": "call", "obligation": "SummaryContract",
         "evidence": "Interproc", "line": 2},
    ]
    at0 = lspe.LspeSolver(sites, 0).run()
    at1 = lspe.LspeSolver(sites, 1).run()
    at2 = lspe.LspeSolver(sites, 2).run()
    assert not any(at0.proved(s) for s in sites)
    assert at1.proved(sites[0]) and not at1.proved(sites[1])
    assert at2.proved(sites[1])


@test
def test_differential_never_discharges():
    site = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "Differential", "line": 1}
    solver = lspe.LspeSolver([site], 3).run()
    assert not solver.proved(site)


@test
def test_census_is_content_addressed_and_lexical():
    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n")
    sites, unsourced, passes = lspe.census(lomc.lex(src), "m")
    idx = [s for s in sites if s["kind"] == "index"]
    assert len(idx) == 1 and idx[0]["evidence"] == "Syntactic", sites
    assert unsourced == []
    assert passes == []

    src2 = src.replace("a[0]", "a[0] + a[1]")
    sites2, _, _ = lspe.census(lomc.lex(src2), "m")
    ids1 = {s["id"] for s in idx}
    ids2 = {s["id"] for s in sites2 if s["kind"] == "index"}
    assert ids1 < ids2, (ids1, ids2)


@test
def test_pass_block_covers_its_sites_and_shows_up_in_the_ledger():
    """`lspe pass "reason" { … }`（`docs/262` §5.16 / `#786`）。

    块里的站点**仍进 `sites`**，但不证明；块自己的站点数、`pass_ratio` 与
    `floors.pass` 要一起动 —— 旁路只让"被旁路掉多少"这个公开的数变难看，
    不让覆盖率变好看。
    """
    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
           "    lspe pass \"manual bound check\" {\n"
           "        let p: u32 = a[1];\n"
           "    }\n"
           "    return a[0] + p;\n}\n")
    toks = lomc.lex(src)
    sites, _, passes = lspe.census(toks, "m")
    assert len(passes) == 1 and passes[0]["reason"] == "manual bound check", passes
    covered = [s for s in sites if s.get("covered_by")]
    assert len(covered) == 1, sites
    doc = lspe.build_ledger(sites=sites, state="lspe_open_admin", set_by="default",
                            level=3, toks=toks, unit="m", passes=passes)
    assert doc["sites"] == 2 and doc["proved"] == 1, doc
    assert doc["passes"][0]["sites"] == 1, doc["passes"]
    assert doc["pass_sites"] == 1 and doc["passed_sites"] == 1
    assert doc["pass_ratio"] == "1/2"
    assert doc["floors"] == 1 and doc["floor_detail"]["pass"] == 1
    assert doc["per_kind"]["InBounds"] == "1/2"


@test
def test_callee_with_a_pass_block_is_an_unchecked_summary():
    """旁路**不跨调用传播**（`#786` 第 3 条 / `docs/262` §5.16）。

    同一条 deref-only 被调：不带旁路 ⇒ 调用点 `Interproc`；带旁路 ⇒ 摘要 `Unchecked`，
    调用点保持 UNDECIDED。两条一起钉，防止"把带旁路的函数从安全表里摘掉"变成
    "谁都不再安全"。
    """
    body = ("module m\n\nfn {name}(p: ptr) -> u32 {{\n"
            "{inner}"
            "}}\n\nfn f(q: ptr) -> u32 {{\n    return {name}(q);\n}}\n")
    plain = body.format(name="deref", inner="    return load8(p, 0);\n")

    def summary(doc):
        return doc["per_kind"].get("SummaryContract")

    def ledger(src):
        toks = lomc.lex(src)
        sites, _, passes = lspe.census(toks, "m")
        return lspe.build_ledger(sites=sites, state="lspe_open_admin", set_by="default",
                                 level=3, toks=toks, unit="m", passes=passes)

    assert summary(ledger(plain)) == "1/1", ledger(plain)["per_kind"]
    bypassed = body.format(
        name="deref",
        inner='    lspe pass "caller checked" {\n        return load8(p, 0);\n    }\n')
    got = ledger(bypassed)
    assert summary(got) == "0/1", got["per_kind"]


@test
def test_pass_ratio_does_not_increase_on_the_corpus():
    """`pass_ratio` 的棘轮（`docs/262` §5.16 / `#1065`）。

    基线 `.github/lspe-pass-baseline.txt` 逐份钉住 `passed_sites / sites`：
    **比值变大就红**（把站点包进 `lspe pass "…"` 必须显式改基线，那一步就是承认
    "我多卖了一格审计"）；变小是好事，只打印。
    """
    from fractions import Fraction  # noqa: PLC0415

    import lspe_ratchet as R  # noqa: PLC0415

    base = R.load_baseline()
    cur = R.measure()
    stale = sorted(set(base) ^ set(cur))
    assert not stale, (
        "基线里的单元与语料表对不上：" + " ".join(stale) +
        "\n  重算：`python tools/lspe_ratchet.py --emit`")

    def ratio(pair):
        p, s = pair
        return Fraction(p, s) if s else Fraction(0)

    worse, better = [], []
    for rel in sorted(cur):
        if ratio(cur[rel]) > ratio(base[rel]):
            worse.append(f"{rel}: {base[rel][0]}/{base[rel][1]} -> "
                         f"{cur[rel][0]}/{cur[rel][1]}")
        elif ratio(cur[rel]) < ratio(base[rel]):
            better.append(f"{rel}: {base[rel][0]}/{base[rel][1]} -> "
                          f"{cur[rel][0]}/{cur[rel][1]}")
    assert not worse, (
        "这些单元的 `pass_ratio` 变大了（旁路块只能显式记账，不能顺手涨）：\n  " +
        "\n  ".join(worse) +
        "\n  确认是有意的就 `python tools/lspe_ratchet.py --emit` 并提交基线")
    if better:
        print(f"      {len(better)} 份单元的旁路率下降：" + "；".join(better[:4]))
    total_p = sum(p for p, _ in cur.values())
    total_s = sum(s for _, s in cur.values())
    print(f"      {len(cur)} 份单元：{total_p}/{total_s} 个站点被旁路"
          f"（基线 {len(base)} 份）")


@test
def test_ledger_shape_and_close_admin_zeroing():
    site = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "Syntactic", "line": 1}
    open_doc = lspe.build_ledger(
        sites=[site], state="lspe_open_admin", set_by="default",
        level=3, toks=[], unit="m")
    assert open_doc["sites"] == 1 and open_doc["proved"] == 1
    assert open_doc["per_kind"] == {"InBounds": "1/1"}
    assert open_doc["invariants"]["i1"] == "ok"

    closed = lspe.build_ledger(
        sites=[site], state="lspe_close_admin", set_by="source",
        level=3, toks=[], unit="m")
    assert closed["state"] == "lspe_close_admin" and closed["set_by"] == "source"
    assert closed["sites"] == 0 and closed["per_kind"] == {}


@test
def test_ledger_i1_fails_on_an_unregistered_site_kind():
    bad = {"id": "m:bogus:1", "kind": "bogus", "obligation": "InBounds",
           "evidence": "none", "line": 1}
    doc = lspe.build_ledger(
        sites=[bad], state="lspe_open_admin", set_by="default",
        level=3, toks=[], unit="m")
    assert doc["invariants"]["i1"] == "fail"


@test
def test_selfhost_half_lives_under_loment_lspe():
    mod = (ROOT / "loment" / "lspe" / "lspe.lomt").read_text(encoding="utf-8")
    assert "module lspe" in mod
    assert "pub fn lspe_engine" in mod
    potato = (ROOT / "loment" / "selfhost" / "potato.lomt").read_text(encoding="utf-8")
    assert "use lspe" in potato
    assert "pub fn lspe_engine" not in potato, "普查引擎不该还留在发射器里"


def main() -> int:
    return loment_suite.run([fn for _name, fn in TESTS], "lspe_test")


if __name__ == "__main__":
    sys.exit(main())
