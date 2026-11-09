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

# E8 的反证探针标记（`#766`）。**由 `tools/lspe_probes.py` 机械扫出来** ——
# 每个义务至少一条；加义务不补探针，`test_e8_probe_registry` 会红。
# lspe-probe: InBounds
# lspe-probe: Alive
# lspe-probe: FreeOwnership
# lspe-probe: SummaryContract
# lspe-probe: GuardHolds
# lspe-probe: Initialized
# lspe-probe: Exclusive
# lspe-probe: TypeWellFormed
# lspe-probe: DropHonest

import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402


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
    # 问题码（`docs/262` §5.10）：Q013 是 `lspe pass` 的未审计块（#786）。
    assert len(lspe.LSPE_QUESTIONS) == 13
    assert lspe.LSPE_QUESTIONS["Q013"]
    assert "未审计块" in lspe.LSPE_QUESTIONS["Q013"]


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
def test_nested_pass_blocks_count_only_the_outermost():
    """嵌套**只记最外层**（`#786` 硬规则 2 / `docs/262` §5.16 边界 2）。

    两个块套在一起时：`passes` 只有最外层那一条、`pass_sites` = 1、内层站点照样进
    `sites` 且 `covered_by` 指向**最外层**的 id（不是各自 `PROVED` —— 一次包装不许让
    收据翻倍）。
    """
    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
           "    lspe pass \"outer\" {\n"
           "        let p: u32 = a[1];\n"
           "        lspe pass \"inner\" {\n"
           "            let q: u32 = a[2];\n"
           "        }\n"
           "    }\n"
           "    return a[0];\n}\n")
    toks = lomc.lex(src)
    sites, _, passes = lspe.census(toks, "m")
    assert len(passes) == 2, passes
    outer = [p for p in passes if p["outer"]]
    assert len(outer) == 1 and outer[0]["reason"] == "outer", passes
    # 两个下标都记在**最外层**块名下（内层不单独计一次）
    covered = [s for s in sites if s.get("covered_by")]
    assert len(covered) == 2, sites
    assert {s["covered_by"] for s in covered} == {outer[0]["id"]}, covered
    doc = lspe.build_ledger(sites=sites, state="lspe_open_admin", set_by="default",
                            level=3, toks=toks, unit="m", passes=passes)
    assert doc["passes"] and len(doc["passes"]) == 1, doc["passes"]
    assert doc["passes"][0]["reason"] == "outer"
    assert doc["pass_sites"] == 1, doc
    assert doc["passed_sites"] == 2, doc
    assert doc["pass_ratio"] == "2/3", doc
    # 旁路的站点**不证明**：proved 只剩块外那一个。
    assert doc["proved"] == 1, doc


@test
def test_every_pass_block_is_a_q013_and_lspc_never_stays_silent():
    """`Q013` 进普查、进 LSPC 贴条（`#786` 语义表最后一行）。

    * 每个 pass 跨度自带 `question == "Q013"`（普查这一侧）；
    * `lspc_lines` **永远**先给一条族级通知（0 个块也照写），每个块再一条；
    * 没有块时只有那条族级通知 —— "0" 是可核的声明，不是沉默。
    """
    clean = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n")
    _, _, no_passes = lspe.census(lomc.lex(clean), "m")
    d0 = lspe.build_ledger(sites=[], state="lspe_open_admin", set_by="default",
                           level=3, toks=[], unit="m", passes=no_passes)
    lines0 = lspe.lspc_lines(d0)
    assert len(lines0) == 1 and "Q013" in lines0[0] and "0 block" in lines0[0], lines0

    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
           "    lspe pass \"caller checked\" {\n"
           "        let p: u32 = a[1];\n"
           "    }\n"
           "    return a[0];\n}\n")
    toks = lomc.lex(src)
    sites, _, passes = lspe.census(toks, "m")
    assert passes and all(p["question"] == "Q013" for p in passes), passes
    doc = lspe.build_ledger(sites=sites, state="lspe_open_admin", set_by="default",
                            level=3, toks=toks, unit="m", passes=passes)
    lines = lspe.lspc_lines(doc)
    assert len(lines) == 2, lines
    assert "Q013" in lines[0] and "1 block" in lines[0]
    assert "Q013" in lines[1] and "caller checked" in lines[1]
    assert all(p["question"] == "Q013" for p in passes)


@test
def test_the_formatter_leaves_a_pass_block_alone():
    """格式化器**不认它是语法构造**（`#786` 验收最后一条 / `docs/262` §5.16）。

    `lspe` / `pass` 都不是保留字，格式化器按普通 token 排版；决策是"原样保留块头、
    只排缩进"。判据钉两条：块头逐字还在，且格式化**幂等**（跑第二遍不改字节）。
    """
    import lomfmt  # noqa: PLC0415

    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
           "    lspe pass \"caller checked\" {\n"
           "        let p: u32 = a[1];\n"
           "    }\n"
           "    return a[0] + p;\n}\n")
    once = lomfmt.format_source(src)
    assert 'lspe pass "caller checked" {' in once, once
    assert lomfmt.format_source(once) == once, "格式化器对含 pass 块的源不幂等"


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
def test_bounds_sites_come_from_lms_g1():
    """LMS g1 的内联界检查在台账里是 `bounds` 站点（`docs/262` §5.1 / `#1021`）。

    它没有自己的语法：来源是**词法形状**（`let p: ptr = alloc(<常量>)` 之后
    按发射顺序的 `load8` / `store8`），档位是 `lms_guard` 且（reject 或 crash）。
    档位关着时**一条都不发** —— 与两侧发射器同一条判据。
    """
    src = ("module m\n\nfn f() -> u32 {\n    let p: ptr = alloc(8);\n"
           "    store8(p, 0, 1);\n    let a: u32 = load8(p, 1);\n    return a;\n}\n")
    toks = lomc.lex(src)
    on = frozenset({"lms_guard", "lms_reject"})
    sites, _, _ = lspe.census(toks, "m", lms=on)
    bound = [s for s in sites if s["kind"] == "bounds"]
    assert len(bound) == 2, sites
    assert all(s["source"] == "lms(g1)" and s["proved"]
               and s["obligation"] == "InBounds" for s in bound), bound

    off, _, _ = lspe.census(toks, "m", lms=frozenset({"lms_check", "lms_manage"}))
    assert not [s for s in off if s["kind"] == "bounds"], off

    # `NAME = …` 让最近一次的来源作废（与发射器同一条"往回找"规则）。
    src2 = src.replace("    let a: u32 = load8(p, 1);",
                       "    p = p;\n    let a: u32 = load8(p, 1);")
    s2, _, _ = lspe.census(lomc.lex(src2), "m", lms=on)
    assert len([s for s in s2 if s["kind"] == "bounds"]) == 1, s2


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


def _unit(src: str, **kw):
    """装载一份小夹具的 LSPE 台账（不经过代码生成）。"""
    toks = lomc.lex(src)
    inst = kw.pop("instances", ())
    sites, _unsourced, passes = lspe.census(toks, "m", instances=inst)
    doc = lspe.build_ledger(
        sites=sites, state=kw.pop("state", "lspe_open_admin"),
        set_by=kw.pop("set_by", "default"), level=kw.pop("level", 3),
        toks=toks, unit="m", passes=passes, instances=inst, **kw)
    return doc, sites


@test
def test_question_registry_templates_are_fixed_and_safe():
    """每一问都有固定字段；文本里不出现威胁词（`docs/262` §6 硬规则 4 / `#753`）。"""
    assert set(lspe.LSPE_QUESTION_TEMPLATES) == set(lspe.LSPE_QUESTIONS)
    for code, tpl in lspe.LSPE_QUESTION_TEMPLATES.items():
        assert tpl["claim"] and tpl["chain"], code
        assert set(tpl["tried"]) == set(lspe.LSPE_INTERROGATIONS), code
        assert tpl["options"] and tpl["default"] in tpl["options"], code
        assert isinstance(tpl["reversible"], bool), code
        text = json.dumps(tpl, ensure_ascii=False).lower()
        assert "unsafe" not in text and "undefined behavior" not in text, code


@test
def test_counter_asker_asks_once_and_defaults_to_assumed_not_proved():
    """同站点同原因只问一次；默认是"允许但标注"，**不是** PROVED（§6 规则 2/3）。"""
    site = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "none", "line": 1}
    solver = lspe.LspeSolver([site], 3).run()
    out = lspe.ask([site, dict(site)], solver)
    assert len(out["questions"]) == 1, out
    q = out["questions"][0]
    assert q["state"] == "assumed" and q["default"] == "B", q
    assert out["assumed"] == ["m:index:1"]
    assert not solver.proved(site), "标注不是证明"


@test
def test_e036_when_tried_does_not_cover_the_three_interrogations():
    """转交前必须三问都跑过；缺一问就是 E036（§6 硬规则 5 / `#755`）。"""
    tpl = dict(lspe.LSPE_QUESTION_TEMPLATES["Q001"])
    tpl["tried"] = {"provenance": "只剩这一问"}
    site = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "none", "line": 1}
    with mock.patch.dict(lspe.LSPE_QUESTION_TEMPLATES, {"Q001": tpl}):
        solver = lspe.LspeSolver([site], 3).run()
        out = lspe.ask([site], solver)
        assert out["errors"] and out["errors"][0]["code"] == "E036", out


@test
def test_answer_channels_defer_refuse_and_strict():
    """`answer` / `defer(reason)` / `refuse` 都进账；strict 下未答是 E034（`#755`/`#756`）。"""
    def fresh():
        return {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
                "evidence": "none", "line": 1}

    s1 = lspe.LspeSolver([fresh()], 3).run()
    out = lspe.ask([fresh()], s1, {"m:index:1": "defer(manual check)"})
    assert out["deferred"] == [{"site": "m:index:1", "reason": "manual check"}], out

    s2 = lspe.LspeSolver([fresh()], 3).run()
    out = lspe.ask([fresh()], s2, {"m:index:1": "refuse"})
    assert out["refused"] == ["m:index:1"], out

    s3 = lspe.LspeSolver([fresh()], 3).run()
    out = lspe.ask([fresh()], s3, None, "strict")
    assert out["errors"] and out["errors"][0]["code"] == "E034", out
    assert not out["assumed"]

    assert lspe.parse_answers(
        "m:index:1=B\n# 注释\ndrop:Guard=prove\n") == {
            "m:index:1": "B", "drop:Guard": "prove"}


@test
def test_drop_honest_is_one_floor_per_destructor_and_hand_proof_clears_it():
    """每个析构器恰一条 `DropHonest`；手写证明让标签消失（`#747`）。"""
    src = ("module m\n\nstruct G { id: u32 }\n\n"
           "impl Drop for G {\n    fn drop(self) {\n"
           "        let x: u32 = self.id;\n    }\n}\n\n"
           "fn f() -> u32 {\n    return 0;\n}\n")
    doc, sites = _unit(src)
    drop = [s for s in sites if s["kind"] == "drop"]
    assert len(drop) == 1 and drop[0]["obligation"] == "DropHonest", sites
    assert doc["floor_detail"]["drop_honest"] == 1, doc["floor_detail"]
    assert doc["per_kind"]["DropHonest"] == "1/1", doc["per_kind"]

    proved, _ = _unit(src, answers={"drop:G": "prove"})
    assert proved["floor_detail"]["drop_honest"] == 0, proved["floor_detail"]
    assert proved["per_kind"]["DropHonest"] == "1/1", proved["per_kind"]


@test
def test_type_well_formed_is_keyed_by_substitution():
    """同一类型在两个替换下是**两个**站点，证据不共享（`#745`）。"""
    src = "module m\n\nfn f() -> u32 {\n    return 0;\n}\n"
    inst = [{"kind": "fn", "name": "id_u32", "of": "id", "args": ["u32"]},
            {"kind": "fn", "name": "id_u8", "of": "id", "args": ["u8"]}]
    doc, sites = _unit(src, instances=inst)
    wf = [s for s in sites if s["kind"] == "wf"]
    assert len(wf) == 2 and len({s["id"] for s in wf}) == 2, wf
    assert {s["subst"] for s in wf} == {"u32", "u8"}, wf
    assert doc["per_kind"]["TypeWellFormed"] == "2/2", doc["per_kind"]

    bad = [{"id": "m:wf:dead", "kind": "wf", "obligation": "TypeWellFormed",
            "evidence": "Typed", "source": "x", "line": 1}]
    try:
        lspe.build_ledger(sites=bad, state="lspe_open_admin", set_by="default",
                          level=3, toks=[], unit="m")
        assert False, "没有 substitution 键的 WF 义务必须被拒"
    except ValueError:
        pass


@test
def test_stale_wf_is_q004_and_never_proved():  # noqa: D401
    """替换前证过、替换后没重证 ⇒ UNDECIDED(stale_wf) / Q004（`#746`）。"""
    site = {"id": "m:wf:1", "kind": "wf", "obligation": "TypeWellFormed",
            "evidence": "Typed", "source": "monomorphise", "subst": "u32",
            "stale_wf": True, "line": 1}
    solver = lspe.LspeSolver([site], 3).run()
    assert not solver.proved(site) and solver.reason(site) == "stale_wf"
    q = lspe.ask([dict(site)], solver)["questions"][0]
    assert q["code"] == "Q004", q

    twin = dict(site, evidence="Differential")
    assert not lspe.LspeSolver([twin], 3).run().proved(twin), \
        "Differential 不能销 stale_wf"


@test
def test_derivation_hash_is_canonical_and_evidence_bound():
    """同一棵树同一哈希；证据一变哈希就变；零证据的 PROVED 被拒（`#748`）。"""
    site = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "Syntactic", "source": "lms(g0)",
            "span": [3, 4, 3, 4], "line": 3, "proved": True}
    t1 = lspe.derivation(site)
    t2 = lspe.derivation(dict(site))
    assert lspe.proof_hash(t1) == lspe.proof_hash(t2)
    assert t1["evidence"][0]["span"] == [3, 4, 3, 4]
    t3 = lspe.derivation(site, [{"kind": "Syntactic", "source": "other",
                                 "span": [3, 4, 3, 4]}])
    assert lspe.proof_hash(t1) != lspe.proof_hash(t3)
    try:
        lspe.derivation(dict(site), [])
        assert False, "PROVED 且零证据必须被拒"
    except ValueError:
        pass


@test
def test_sidecar_verify_detects_mutation_missing_and_foreign_unit():
    """`verify` 不重编译：改过的旁挂、少站点、别的单元都要拒（`#749`）。"""
    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n")
    doc, _ = _unit(src, sidecar=True)
    artifact = {"unit": "m", "lspe": lspe.public(doc)}
    side = lspe.sidecar("m", 3, doc["_trees"])
    assert doc["proof_root"] == side["root"]
    assert lspe.verify(artifact, side) == []

    s2 = json.loads(json.dumps(side))
    k = sorted(s2["sites"])[0]
    s2["sites"][k]["hash"] = "deadbeef"
    assert lspe.verify(artifact, s2)

    s3 = json.loads(json.dumps(side))
    s3["sites"].pop(sorted(s3["sites"])[0])
    assert lspe.verify(artifact, s3)

    s4 = json.loads(json.dumps(side))
    s4["unit"] = "other"
    assert lspe.verify(artifact, s4)


@test
def test_budget_and_depth_floors_are_counted_separately():
    """步数预算与深度上限各自成桶；降低预算只许把 PROVED 变成 UNDECIDED（`#750`）。"""
    sites = [{"id": f"m:index:{i:02d}", "kind": "index",
              "obligation": "InBounds", "evidence": "none", "line": i}
             for i in range(6)]
    s = lspe.LspeSolver(sites, 3, budget=2).run()
    assert len(s.budget_hits) == 4, s.budget_hits

    deep = [dict(x, depth=9) for x in sites]
    s2 = lspe.LspeSolver(deep, 3, depth=6).run()
    assert len(s2.depth_hits) == 6, s2.depth_hits

    doc = lspe.build_ledger(sites=[dict(x) for x in sites],
                            state="lspe_open_admin", set_by="default", level=3,
                            toks=[], unit="m", mode="strict", budget=2)
    assert doc["floor_detail"]["budget"] == 4, doc["floor_detail"]
    assert doc["floor_detail"]["assumed"] == 0, doc["floor_detail"]

    # 直接证据不受预算影响；需要传播的才受影响 —— 预算只许**去掉**证明。
    dep = [{"id": "m:index:00", "kind": "index", "obligation": "InBounds",
            "evidence": "Syntactic", "source": "s", "line": 1},
           {"id": "m:index:01", "kind": "index", "obligation": "InBounds",
            "evidence": "none", "line": 1, "depends_on": ["m:index:00"]}]
    hi = lspe.LspeSolver(dep, 3, budget=100).run()
    lo = lspe.LspeSolver([dict(x) for x in dep], 3, budget=1).run()
    assert hi.proved(dep[1]) and not lo.proved(dep[1]), (hi.evidence, lo.evidence)


@test
def test_lspc_sheet_has_twelve_families_and_five_columns():
    """12 族 × 五列；最后一行是递归的"审计自己被关掉"（`#757`）。"""
    sh = lspe.lspc_sheet([], {"state": "lspe_open_admin", "lspc": "full"})
    assert sh["families"] == 12 and len(sh["rows"]) == 12, sh
    for r in sh["rows"]:
        for k in ("protects", "if_fails", "fix", "cost", "bearer"):
            assert r[k], (r["id"], k)
    assert sh["rows"][-1]["id"] == "audit_off"
    assert lspe.lspc_text(sh)

    off = lspe.lspc_sheet([], {"state": "lspe_close_admin", "lspc": "off"},
                          mode="off")
    assert off["rows"][-1]["present"], off["rows"][-1]

    sites = [{"id": "m:index:1", "kind": "index", "obligation": "InBounds",
              "evidence": "none", "line": 1}]
    strict = lspe.lspc_sheet(sites, {"state": "lspe_open_admin", "lspc": "full"},
                             mode="strict")
    assert "nonliteral_index" in strict["blocking"], strict["blocking"]


@test
def test_lspc_modes_acknowledgements_and_quiet():
    """不修 = 默认 ack；`--lspc=off` 要进产物；quiet 只给总数（`#759`）。"""
    sites = [{"id": "m:index:1", "kind": "index", "obligation": "InBounds",
              "evidence": "none", "line": 1}]
    sh = lspe.lspc_sheet(sites, {"state": "lspe_open_admin", "lspc": "full"},
                         mode="strict", acks={"nonliteral_index": "ack"})
    assert sh["blocking"] == [] and sh["acked"] == 1, sh
    quiet = lspe.lspc_sheet(sites, {"state": "lspe_open_admin", "lspc": "full"},
                            mode="quiet")
    assert len(lspe.lspc_text(quiet)) == 1
    assert lspe.lspc_default("lspe_close_admin") == "off"
    assert lspe.parse_acks({"lspc:drop_honest": "ack"}) == {"drop_honest": "ack"}


@test
def test_lspc_reads_the_previous_build():
    """上一轮的 defer / 过期假设 / close_admin 都要出现在这一轮（`#760`）。"""
    prev_art = {"lspe": {"state": "lspe_close_admin", "deferred": 2}}
    prev_side = {"sites": {"m:index:1": {"hash": "aaaa"}}}
    items = lspe.previous_items(prev_art, prev_side, {"m:index:2": {"hash": "bbbb"}})
    assert items["close_admin"] and len(items["deferred"]) == 2, items
    assert items["expired"] == ["m:index:1"], items

    sh = lspe.lspc_sheet([], {"state": "lspe_open_admin", "lspc": "full"},
                         previous=items)
    rows = {r["id"]: r for r in sh["rows"]}
    assert rows["defer"]["present"] and rows["expired"]["present"], rows
    assert rows["audit_off"]["present"], rows["audit_off"]


@test
def test_invariants_detect_corrupted_ledgers():
    """I1–I5 都是**对安全系统自己**的判据；坏台账必须红（`#761`–`#764`）。"""
    good = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "Syntactic", "source": "lms(g0)", "line": 1}
    solver = lspe.LspeSolver([good], 3).run()
    ledger = {"sites": 1, "proved": 1, "floors": 0,
              "floor_detail": {"assumed": 0, "stale_wf": 0, "budget": 0,
                               "depth": 0, "drop_honest": 0, "pass": 0}}
    inv, probs = lspe.check_invariants([good], solver, ledger)
    assert inv == {"i1": "ok", "i2": "ok", "i3": "ok", "i4": "ok", "i5": "ok"}, inv
    assert not probs

    bad_i4 = dict(ledger, floors=1)
    inv, probs = lspe.check_invariants([good], solver, bad_i4)
    assert inv["i4"] == "fail" and probs[0]["code"] == "E036", (inv, probs)

    free = {"id": "m:free:1", "kind": "free", "obligation": "FreeOwnership",
            "evidence": "none", "line": 1}
    fs = lspe.LspeSolver([free], 3).run()
    inv, probs = lspe.check_invariants([free], fs, ledger, dims={"gc": "gc_manual"})
    assert inv["i2"] == "fail" and any(p["code"] == "E034" for p in probs), inv

    inv, _ = lspe.check_invariants([good], solver, ledger,
                                   twin={"m:index:1": (False, None)})
    assert inv["i5"] == "fail", inv


@test
def test_e5_proves_loop_indices_only_with_a_matching_unsigned_guard():
    """`for i in 0..N` / 无符号 `while i < N` 与数组长度相等才证（`#765`）。"""
    def evidence(src):
        sites, _u, _p = lspe.census(lomc.lex(src), "m")
        idx = [s for s in sites if s["kind"] == "index"]
        assert len(idx) == 1, sites
        return idx[0]

    for_ = evidence("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
                    "    let s: u32 = 0;\n    for i in 0..4 {\n"
                    "        s = s + a[i];\n    }\n    return s;\n}\n")
    assert for_["evidence"] == "GlobalInvariant" and for_["proved"], for_

    while_ = evidence("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
                      "    let s: u32 = 0;\n    let i: u32 = 0;\n"
                      "    while i < 4 {\n        s = s + a[i];\n"
                      "        i = i + 1;\n    }\n    return s;\n}\n")
    assert while_["evidence"] == "GlobalInvariant", while_

    signed = evidence("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
                      "    let s: u32 = 0;\n    let i: i32 = 0;\n"
                      "    while i < 4 {\n        s = s + a[i];\n"
                      "        i = i + 1;\n    }\n    return s;\n}\n")
    assert signed["evidence"] == "none", signed

    start2 = evidence("module m\n\nfn f(a: [u32; 4]) -> u32 {\n"
                      "    let s: u32 = 0;\n    for i in 2..4 {\n"
                      "        s = s + a[i];\n    }\n    return s;\n}\n")
    assert start2["evidence"] == "none", start2

    # E5 不在 L0/L1 跑（GlobalInvariant 从 L3 起被承认）。
    assert not lspe.LspeSolver([for_], 2).run().proved(for_)
    assert lspe.LspeSolver([for_], 3).run().proved(for_)


@test
def test_lspe_cache_cold_warm_identical_and_mismatch_is_fatal():
    """缓存不许改变产物；哈希不一致是硬错误；目录不许落在 GLOB 里（`#752`）。"""
    src = ("module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n")
    with tempfile.TemporaryDirectory() as td:
        dims = {"gc": "gc_manual"}
        d1, _ = _unit(src, cache=lspe.Cache(td, "m", "h1", dims, 3))
        d2, _ = _unit(src, cache=lspe.Cache(td, "m", "h1", dims, 3))
        assert lspe.public(d1) == lspe.public(d2), "冷/热构建的台账必须逐字段相同"

        bad = lspe.Cache(td, "m", "h1", dims, 3)
        key = next(iter(bad.data))
        bad.data[key]["hash"] = "deadbeef"
        try:
            _unit(src, cache=bad)
            assert False, "缓存哈希不一致必须是硬错误"
        except ValueError as e:
            assert "缓存哈希不一致" in str(e), e

        try:
            lspe.Cache(ROOT, "m", "h1", dims, 3, directory=ROOT / "loment")
            assert False, "缓存目录不许落在 GLOB 目录里"
        except ValueError:
            pass


@test
def test_i5_divergence_probe_is_caught():
    """I5 的分歧探针：同一份源、把一侧的规则改掉，台账必须**看得出**不同（`#764`）。

    这不是"孪生证明了对"，而是一条反向判据：如果比较对象对规则改动**不敏感**，
    那"两边逐字节相同"就只是噪声。
    """
    import importlib  # noqa: PLC0415

    cmod = importlib.import_module("lspe.census")
    src = "module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n"
    toks = lomc.lex(src)
    base, _u, _p = lspe.census(toks, "m")
    patched = set(cmod.LSPE_BRACKET_KW) | {"a"}
    with mock.patch.object(cmod, "LSPE_BRACKET_KW", frozenset(patched)):
        twin, _u, _p = lspe.census(toks, "m")
    assert {s["id"] for s in base} != {s["id"] for s in twin}, \
        "改了一条普查规则，站点集合却一点没变 —— 比较对象对规则不敏感"


@test
def test_safe_set_converges_past_256_rounds():
    """E4 的安全表不动点没有轮数上限（`#1093`）。

    300 层 deref-only 链：旧上限 256 轮会把后 44 个调用点记成"未决定"——
    那不是预算耗尽，是**假的洞**。
    """
    lines = ["module m", "", "fn f0(p: ptr) -> u32 {",
             "    return load8(p, 0);", "}", ""]
    for i in range(1, 300):
        lines += [f"fn f{i}(p: ptr) -> u32 {{", f"    return f{i-1}(p);", "}", ""]
    toks = lomc.lex("\n".join(lines))
    sites, _u, _p = lspe.census(toks, "m")
    calls = [s for s in sites if s["kind"] == "call"]
    assert len(calls) == 299, len(calls)
    assert all(s["proved"] for s in calls), \
        f"{sum(1 for s in calls if not s['proved'])} 个调用点停在 256 轮"


@test
def test_e7_e8_differential_and_probe_registry():
    """E7 只证一致不证对；E8 的登记表机械生成，缺探针 ⇒ Q002（`#766`）。"""
    twin = {"id": "m:index:1", "kind": "index", "obligation": "InBounds",
            "evidence": "Differential", "source": "twin", "line": 1}
    assert not lspe.LspeSolver([twin], 3).run().proved(twin)

    import lspe_probes  # noqa: PLC0415
    assert lspe_probes.missing() == [], lspe_probes.missing()
    assert lspe_probes.unknown() == [], lspe_probes.unknown()

    inert = {"id": "m:index:2", "kind": "index", "obligation": "CapabilityLive",
             "evidence": "none", "line": 2}
    solver = lspe.LspeSolver([inert], 3).run()
    q = lspe.ask([inert], solver)["questions"][0]
    assert q["code"] == "Q002", q


@test
def test_capacity_declarations_have_a_loud_path():
    """每个新容量都有一条会说话的路，没有一个"写穿就完"（`#751`）。"""
    for name, (cap, how) in lspe.LSPE_CAPACITIES.items():
        assert cap > 0 and how.strip(), name
    mod = (ROOT / "loment" / "lspe" / "lspe.lomt").read_text(encoding="utf-8")
    assert "return 1;" in mod and "return 2;" in mod, "自举侧的两个容量失败要还在"


@test
def test_coverage_ratchet_never_decreases():
    """每种义务的覆盖率只许升不许降（`docs/262` §5.15 / `#767`）。"""
    from fractions import Fraction  # noqa: PLC0415

    import lspe_ratchet as R  # noqa: PLC0415
    base = R.load_coverage()
    cur = R.measure_coverage()
    assert base, "缺覆盖率基线：`python tools/lspe_ratchet.py --emit`"
    stale = sorted(set(base) ^ set(cur))
    assert not stale, ("覆盖率基线与语料对不上：" + " ".join(
        f"{a}:{b}" for a, b in stale[:8]) + "\n  重算：python tools/lspe_ratchet.py --emit")
    worse = []
    for key in sorted(cur):
        if Fraction(*cur[key]) < Fraction(*base[key]):
            worse.append(f"{key[0]} {key[1]}: {base[key][0]}/{base[key][1]} -> "
                         f"{cur[key][0]}/{cur[key][1]}")
    assert not worse, "覆盖率降了：\n  " + "\n  ".join(worse)


@test
def test_reverse_size_criterion_removing_wf_makes_the_case_invisible():
    """反向判据：把 `TypeWellFormed` 塌掉，#164250 形状重新变回不可见（`#767`）。

    这不是"更严的检查器"的广告 —— 它证明**尺寸是挣来的**：少一类义务，
    那一类缺陷就没有任何地方能表示它。
    """
    src = "module m\n\nfn f() -> u32 {\n    return 0;\n}\n"
    inst = [{"kind": "fn", "name": "id_u32", "of": "id", "args": ["u32"]}]
    toks = lomc.lex(src)
    full, _u, _p = lspe.census(toks, "m", instances=inst)
    assert [s for s in full if s["kind"] == "wf"], full

    collapsed = {k: v for k, v in lspe.LSPE_OBLIGATIONS.items()
                 if k != "TypeWellFormed"}
    with mock.patch.dict(lspe.registry.LSPE_OBLIGATIONS, collapsed, clear=True):
        blind, _u, _p = lspe.census(toks, "m", instances=inst)
        assert not [s for s in blind if s["kind"] == "wf"], blind


@test
def test_lspe_cli_refuses_cleanly_never_a_traceback():
    """缺文件 / 目录 / 前门拒都不甩 Python 栈（`#1087` / `#1181`）。"""
    def run(*args):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment.py"), *args],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False, timeout=120)

    for target in (str(ROOT / "no-such-file.lomt"), str(ROOT / "loment")):
        r = run("lspe", "state", target)
        assert r.returncode != 0, r
        assert "Traceback" not in r.stdout + r.stderr, r.stdout + r.stderr

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.lomt"
        p.write_text("module m\nchoose write grammar klingon\n\n"
                     "fn f() -> u32 {\n    return 0;\n}\n", encoding="utf-8")
        r = run("lspe", "state", str(p))
        assert r.returncode != 0, r
        assert "Traceback" not in r.stdout + r.stderr, r.stdout + r.stderr
        assert "[ERR]" in r.stderr, r.stderr


@test
def test_lspc_check_runs_before_codegen_and_never_writes():
    """`lspc check` 在有后续代码生成错误的单元上也出单；不写文件（`#758`）。"""
    src = ("module m\n\nstruct S { a: u32 }\n\n"
           "fn f(s: S) -> u32 {\n    return s.a;\n}\n\n"
           "fn g() -> u32 {\n    return f(S { a: 1 });\n}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        before = p.read_bytes()
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment.py"),
             "lspc", "check", str(p)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=False, timeout=120)
        assert r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-500:])
        assert "pre-flight sheet" in r.stdout, r.stdout[-500:]
        assert p.read_bytes() == before, "预检单不能改源码"


@test
def test_selfhost_ledger_emits_the_v2_fields():
    mod = (ROOT / "loment" / "selfhost" / "potato.lomt").read_text(encoding="utf-8")
    for field in ("interrogations", "lspc", "drop_honest", "depth",
                  "TypeWellFormed", "DropHonest", "assumed"):
        assert f'"{field}"' in mod, field
    mod2 = (ROOT / "loment" / "lspe" / "lspe.lomt").read_text(encoding="utf-8")
    assert "pub fn lspe_e5_hit" in mod2 and "pub fn lspe_num" in mod2


def main() -> int:
    return loment_suite.run([fn for _name, fn in TESTS], "lspe_test")


if __name__ == "__main__":
    sys.exit(main())
