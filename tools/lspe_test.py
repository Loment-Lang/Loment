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
    sites, unsourced = lspe.census(lomc.lex(src), "m")
    idx = [s for s in sites if s["kind"] == "index"]
    assert len(idx) == 1 and idx[0]["evidence"] == "Syntactic", sites
    assert unsourced == []

    src2 = src.replace("a[0]", "a[0] + a[1]")
    sites2, _ = lspe.census(lomc.lex(src2), "m")
    ids1 = {s["id"] for s in idx}
    ids2 = {s["id"] for s in sites2 if s["kind"] == "index"}
    assert ids1 < ids2, (ids1, ids2)


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
