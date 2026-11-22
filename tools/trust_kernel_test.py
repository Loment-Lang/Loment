#!/usr/bin/env python3
# trust_kernel_test.py — R0–R5 判据 (docs/379 §6 C1–C7 / C11 的玩具形态)
#
# 它钉住四件事:
#   1. 有效推导被接受;
#   2. 改产物 / 改推导 / 改结论 / 提权 / 换语义 —— 必须拒;
#   3. 内核零工具链 import，删掉编译器也能验;
#   4. 两粒种子不因身份被区别对待。
#
# 运行: python tools/trust_kernel_test.py

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trust_demo_compiler as C  # noqa: E402
import trust_kernel as K  # noqa: E402
import trust_quorum as Q  # noqa: E402

TESTS: list[tuple[str, object]] = []
SRC_A = "PUSH 1\nPUSH 2\nADD\nPRINT\nHALT\n"
SRC_B = "PUSH 3\nPUSH 4\nADD\nPRINT\nHALT\n"


class Skip(Exception):
    """本机缺少某个环境（例如没有 C 编译器）；CI 上不许跳过。"""


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _build(sources=None, **kw):
    return C.build_program(sources or [SRC_A], **kw)


@test
def test_valid_program_is_accepted():
    art, d, c = _build()
    ok, why = K.verify(art, d, c)
    assert ok, why
    sem = [n["judgment"] for n in d["nodes"] if n["rule"] == "SEM"]
    assert sem and sem[0]["output"] == [3]
    print("      有效推导被接受，输出 [3]")


@test
def test_composition_runs_two_units():
    art, d, c = _build([SRC_A, SRC_B])
    ok, why = K.verify(art, d, c)
    assert ok, why
    sem = [n["judgment"] for n in d["nodes"] if n["rule"] == "SEM"][0]
    assert sem["output"] == [3, 7], sem["output"]
    print("      两单元组合被接受，输出 [3, 7]")


@test
def test_tampered_artifact_is_rejected():
    art, d, c = _build()
    bad = bytearray(art)
    bad[len(bad) // 2] ^= 1
    ok, why = K.verify(bytes(bad), d, c)
    assert not ok, "改了一个字节仍然被接受"
    print(f"      改产物 -> 拒绝 ({why[0][:60]})")


@test
def test_tampered_rule_is_rejected():
    art, d, c = _build()
    bad = json.loads(json.dumps(d))
    bad["nodes"][2]["rule"] = "MAGIC"
    ok, why = K.verify(art, bad, c)
    assert not ok, "未知规则仍然被接受"
    print("      改规则名 -> 拒绝")


@test
def test_tampered_premise_is_rejected():
    art, d, c = _build()
    bad = json.loads(json.dumps(d))
    bad["nodes"][2]["premises"] = ["nope"]
    ok, why = K.verify(art, bad, c)
    assert not ok, "缺失前提仍然被接受"
    print("      改前提到不存在的节点 -> 拒绝")


@test
def test_semantic_mismatch_is_rejected():
    art, d, c = _build()
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "SEM":
            n["judgment"]["output"] = [999]
    ok, why = K.verify(art, bad, c)
    assert not ok, "语义与解释器不符仍然被接受"
    print("      改 SEM 期望输出 -> 拒绝")


@test
def test_capability_escalation_is_rejected():
    art, d, c = _build(allowed=[])
    ok, why = K.verify(art, d, c)
    assert not ok, "required caps 不在 allowed 里仍然被接受"
    print(f"      PRINT 不在允许集 -> 拒绝 ({why[0][:60]})")


@test
def test_two_seeds_are_not_privileged():
    a1, d1, c1 = _build(seed="seed-A")
    a2, d2, c2 = _build(seed="seed-B")
    assert K.verify(a1, d1, c1)[0], "seed-A 被拒"
    assert K.verify(a2, d2, c2)[0], "seed-B 被拒"
    print("      两粒不同种子各有有效推导，都被接受")


@test
def test_missing_ax_s_is_rejected():
    art, d, c = _build()
    bad = json.loads(json.dumps(d))
    bad["nodes"] = [n for n in bad["nodes"] if n["rule"] != "AX-S"]
    ok, why = K.verify(art, bad, c)
    assert not ok, "没有 AX-S 的种子仍然成为根"
    print("      删掉 AX-S -> 拒绝")


@test
def test_derivation_is_deterministic():
    a1, d1, c1 = _build()
    a2, d2, c2 = _build()
    assert a1 == a2 and K._canon(d1) == K._canon(d2) and K._canon(c1) == K._canon(c2)
    print(f"      两次构建逐字节相同 ({len(a1)} B artifact, {len(K._canon(d1))} B proof)")


@test
def test_kernel_has_zero_toolchain_imports():
    tools = str(Path(__file__).resolve().parent)
    code = (
        "import sys; sys.path.insert(0, %r); import trust_kernel; "
        "bad=[m for m in ('trust_demo_compiler','lomentc','lspe','lnotary','potato') "
        "if m in sys.modules]; print('BAD' if bad else 'OK', bad)"
    ) % tools
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120, shell=False)
    assert r.returncode == 0 and r.stdout.strip().startswith("OK"), r.stdout + r.stderr
    src = Path(K.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"__future__", "argparse", "hashlib", "json", "sys", "pathlib"}
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports <= allowed, f"内核 import 了非标准库: {sorted(imports - allowed)}"
    print(f"      零工具链 import；只 import {sorted(imports)}")


@test
def test_kernel_works_without_compiler():
    with tempfile.TemporaryDirectory(prefix="trust-kernel-") as td:
        root = Path(td)
        out = root / "out"
        C.write_program(out, [SRC_A])
        run = root / "run"
        run.mkdir()
        shutil.copyfile(K.__file__, run / "trust_kernel.py")
        for name in ("program.toy", "program.deriv", "program.phi"):
            shutil.copyfile(out / name, run / name)
        r = subprocess.run(
            [sys.executable, "trust_kernel.py", "verify",
             "--artifact", "program.toy", "--derivation", "program.deriv",
             "--conclusion", "program.phi"],
            cwd=str(run), capture_output=True, text=True, timeout=120, shell=False,
            env={"PYTHONPATH": "", "PATH": ""})
        assert r.returncode == 0, f"没有编译器时内核失败: {r.stdout}\n{r.stderr}"
        assert "ACCEPT" in r.stdout
    print("      删掉编译器（只留 kernel + 三个文件）仍接受")


@test
def test_malformed_derivation_is_rejected():
    cases = [
        {},
        {"format": 1, "kernel": {"rules_sha256": K.RULES_SHA256}, "nodes": [], "root": "x"},
        {"format": 1, "kernel": {"rules_sha256": "0" * 64}, "nodes": [], "root": "x"},
        {"format": 1, "kernel": {"rules_sha256": K.RULES_SHA256},
         "nodes": [{"id": "a", "rule": "AX-K", "premises": [], "judgment": None}],
         "root": "a"},
    ]
    for d in cases:
        ok, _ = K.verify(b"not an artifact", d, None)
        assert not ok, f"畸形推导被接受: {d!r}"
    print("      四类畸形推导 -> 拒绝")


@test
def test_budget_is_recorded():
    with tempfile.TemporaryDirectory(prefix="trust-budget-") as td:
        out = Path(td)
        C.write_program(out, [SRC_A])
        b = json.loads((out / "trust.json").read_text(encoding="utf-8"))
        assert b["rules"] == len(K.RULES) == 13
        assert b["proof_bytes"] > 0
        assert b["n"] == 1 and b["k"] == 1 and b["assumptions"] == 7
        art, d, c = _build()
        t0 = time.perf_counter()
        for _ in range(10):
            assert K.verify(art, d, c)[0]
        ms = (time.perf_counter() - t0) * 100
        assert ms > 0
        print(f"      rules {b['rules']}, proof {b['proof_bytes']} B, "
              f"check {ms:.2f} ms (10 次平均), rule_lines {b['rule_lines']}")


REPO = Path(__file__).resolve().parent.parent


@test
def test_tv_output_node_mutation_is_rejected():
    """R1/C9: 逐节点翻译验证 —— 改一个输出节点或一条映射必须红。"""
    art, d, c = _build()
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "TV-L2":
            n["judgment"]["bytes_hex"] = "01030103020004000500"
    assert not K.verify(art, bad, c)[0], "改了输出字节仍然被接受"
    bad2 = json.loads(json.dumps(d))
    for n in bad2["nodes"]:
        if n["rule"] == "TV-L1":
            n["judgment"]["map"][0]["arg"] = 9
    assert not K.verify(art, bad2, c)[0], "改了翻译映射仍然被接受"
    print("      改 TV 输出字节 / 翻译映射 -> 拒绝")


@test
def test_real_l0_provenance_is_accepted():
    """R2: 真实 docs/376 provenance 作为 PROV 的可选前提，零工具链 import 也能核。"""
    art, d, c = _build(l0_root=str(REPO))
    ok, why = K.verify(art, d, c)
    assert ok, why
    prov = [n["judgment"] for n in d["nodes"] if n["rule"] == "PROV"][0]
    l0 = prov["l0"]
    assert l0["genesis_sha256"] == json.loads(
        (REPO / "loment/build/genesis/provenance.json").read_text(encoding="utf-8")
    )["genesis"]["sha256"]
    print(f"      L0 provenance 被接受 (genesis {l0['genesis_sha256'][:16]}…, "
          f"seed {l0['seed_sha256'][:16]}…)")


@test
def test_tampered_l0_record_is_rejected():
    art, d, c = _build(l0_root=str(REPO))
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "PROV":
            n["judgment"]["l0"]["seed_sha256"] = "0" * 64
    assert not K.verify(art, bad, c)[0], "改 L0 记录的种子哈希仍然被接受"
    print("      改 L0 记录 -> 拒绝")


@test
def test_l0_path_escape_is_rejected():
    art, d, c = _build(l0_root=str(REPO))
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "PROV":
            n["judgment"]["l0"]["provenance"] = "../outside.json"
    assert not K.verify(art, bad, c)[0], "路径逃逸仍然被接受"
    print("      L0 路径逃逸 -> 拒绝")


@test
def test_l0_file_mismatch_is_rejected():
    """把 provenance 与文件复制到临时根，改一个闭包文件；记录还在，文件对不上。"""
    prov_path = REPO / "loment/build/genesis/provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="trust-l0-") as td:
        tr = Path(td)
        rels = [prov["inputs"]["seed"]["path"], prov["inputs"]["mirror"]["path"],
                f"loment/build/genesis/{prov['genesis']['name']}",
                "loment/build/genesis/provenance.json"]
        for key in ("source_closure", "genesis_source_closure"):
            rels += [e["path"] for e in prov[key]["files"]]
        for rel in rels:
            src = REPO / rel
            dst = tr / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        victim = tr / prov["source_closure"]["files"][0]["path"]
        victim.write_bytes(victim.read_bytes() + b"\n")
        art, d, c = _build(l0_root=str(REPO))
        bad = json.loads(json.dumps(d))
        for n in bad["nodes"]:
            if n["rule"] == "PROV":
                n["judgment"]["l0"]["root"] = str(tr)
        assert not K.verify(art, bad, c)[0], "闭包文件被改仍然被接受"
    print("      L0 闭包文件对不上 -> 拒绝")


_BUNDLE: dict | None = None


def _bundle() -> dict:
    """用真编译器 + 真 lms_sites + 真 LNotary 造一份 R3 夹具。只造一次。"""
    global _BUNDLE
    if _BUNDLE is not None:
        return _BUNDLE
    sys.path.insert(0, str(REPO / "loment"))
    from lnotary import lawspace
    td = Path(tempfile.mkdtemp(prefix="trust-r3-"))
    src = td / "r3.lomt"
    src.write_text(
        "module r3\nchoose lvm\nchoose runtime\nchoose lifetime_auto\n\n"
        "fn _start() {\n"
        "    let p: ptr = alloc(4);\n"
        "    let x: u32 = load8(p, 4);\n"
        "    syscall4(60, 0, 0, 0);\n"
        "}\n", encoding="utf-8")
    potato = td / "potato.json"
    r = subprocess.run([sys.executable, str(REPO / "tools" / "lomentc.py"), str(src),
                        "--emit-potato", str(potato)], cwd=str(REPO),
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    ir = td / "r3.ll"
    r = subprocess.run([sys.executable, str(REPO / "tools" / "lomentc.py"), str(src),
                        "--print", "llvm"], cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    ir.write_text(r.stdout, encoding="utf-8")
    sites = td / "sites.json"
    r = subprocess.run([sys.executable, str(REPO / "tools" / "lms_sites.py"), str(ir),
                        "--out", str(sites)], cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(potato.read_text(encoding="utf-8"))
    site_doc = json.loads(sites.read_text(encoding="utf-8"))
    side = lawspace.derive_lawspace(doc, potato.read_bytes(), sites=site_doc)
    law = td / "lawspace.json"
    law.write_text(json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    _BUNDLE = {"root": str(td), "potato": "potato.json",
               "sites": "sites.json", "lawspace": "lawspace.json"}
    return _BUNDLE


def _reseal(obj: dict) -> dict:
    body = {k: v for k, v in obj.items() if k != "seal"}
    obj["seal"] = K._sha(K._canon(body))
    return obj


@test
def test_real_lnotary_bundle_is_accepted():
    """R3: 真 Potato + 真站点表 + LNotary lawspace 作为 SAFE 前提。"""
    b = _bundle()
    art, d, c = _build(l0_safe=b)
    ok, why = K.verify(art, d, c)
    assert ok, why
    safe = [n["judgment"] for n in d["nodes"] if n["rule"] == "SAFE"][0]
    assert safe["l0_safe"]["potato_sha256"] == K._sha(
        (Path(b["root"]) / b["potato"]).read_bytes())
    print(f"      LNotary bundle 被接受 (potato {safe['l0_safe']['potato_sha256'][:16]}…)")


@test
def test_tampered_lawspace_claim_is_rejected():
    b = _bundle()
    root = Path(b["root"])
    side = json.loads((root / b["lawspace"]).read_text(encoding="utf-8"))
    assert side["laws"], "R3 夹具没有可篡改的 law"
    side["laws"][0]["checked"] = False
    _reseal(side)
    bad = root / "lawspace_bad.json"
    bad.write_text(json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    l0 = dict(b)
    l0["lawspace"] = "lawspace_bad.json"
    art, d, c = _build(l0_safe=l0)
    assert not K.verify(art, d, c)[0], "lawspace 的 checked=false 仍然被接受"
    print("      改 lawspace 的 checked -> 拒绝")


@test
def test_tampered_site_table_is_rejected():
    b = _bundle()
    root = Path(b["root"])
    sites = json.loads((root / b["sites"]).read_text(encoding="utf-8"))
    sites["sites"][0]["grade"] = "bogus"
    _reseal(sites)
    bad_sites = root / "sites_bad.json"
    bad_sites.write_text(json.dumps(sites, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    side = json.loads((root / b["lawspace"]).read_text(encoding="utf-8"))
    side["sites"] = {"sha256": sites["seal"],
                     "count": (sites.get("counts") or {}).get("sites")}
    _reseal(side)
    bad_law = root / "lawspace_sites_bad.json"
    bad_law.write_text(json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
    l0 = dict(b)
    l0["sites"] = "sites_bad.json"
    l0["lawspace"] = "lawspace_sites_bad.json"
    art, d, c = _build(l0_safe=l0)
    assert not K.verify(art, d, c)[0], "站点等级被改成 bogus 仍然被接受"
    print("      改站点等级 -> 拒绝")


@test
def test_tampered_fault_edge_is_rejected():
    """V3: checked 站点的 edge 必须与 Potato 的 pangu/reject 开关一致。"""
    b = _bundle()
    root = Path(b["root"])
    sites = json.loads((root / b["sites"]).read_text(encoding="utf-8"))
    checked = [s for s in sites.get("sites", []) if s.get("grade") == "checked"]
    assert checked, "R3 夹具没有 checked 站点"
    doc = json.loads((root / b["potato"]).read_text(encoding="utf-8"))
    levels = doc.get("lms_levels") or {}
    pangu = any(isinstance(x, dict) and x.get("name") == "lms_pangu_true"
                and x.get("on") is True for x in (doc.get("switches") or []))
    want = "fault" if pangu else ("skip" if levels.get("lms_reject") is True else "abort")
    s = checked[0]
    if want == "fault":
        s["edge"] = "skip"
        s.pop("fault", None)
    else:
        s["edge"] = "fault"
        s["fault"] = "F01"
    _reseal(sites)
    bad_sites = root / "sites_edge_bad.json"
    bad_sites.write_text(json.dumps(sites, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    side = json.loads((root / b["lawspace"]).read_text(encoding="utf-8"))
    side["sites"] = {"sha256": sites["seal"],
                     "count": (sites.get("counts") or {}).get("sites")}
    _reseal(side)
    bad_law = root / "lawspace_edge_bad.json"
    bad_law.write_text(json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
    l0 = dict(b)
    l0["sites"] = "sites_edge_bad.json"
    l0["lawspace"] = "lawspace_edge_bad.json"
    art, d, c = _build(l0_safe=l0)
    assert not K.verify(art, d, c)[0], "checked 站点的 edge 与开关不一致仍然被接受"
    print(f"      改 checked 站点的 edge（应为 {want}）-> 拒绝")


@test
def test_tampered_potato_invariant_is_rejected():
    b = _bundle()
    root = Path(b["root"])
    sys.path.insert(0, str(REPO / "loment"))
    from lnotary import lawspace
    doc = json.loads((root / b["potato"]).read_text(encoding="utf-8"))
    doc["gc_ladder"]["total_sites"] = int(doc["gc_ladder"]["total_sites"]) + 1
    bad_potato = root / "potato_bad.json"
    bad_potato.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    site_doc = json.loads((root / b["sites"]).read_text(encoding="utf-8"))
    side = lawspace.derive_lawspace(doc, bad_potato.read_bytes(), sites=site_doc)
    bad_law = root / "lawspace_potato_bad.json"
    bad_law.write_text(json.dumps(side, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
    l0 = dict(b)
    l0["potato"] = "potato_bad.json"
    l0["lawspace"] = "lawspace_potato_bad.json"
    art, d, c = _build(l0_safe=l0)
    assert not K.verify(art, d, c)[0], "gc_ladder 恒等式被破坏仍然被接受"
    print("      改 Potato 的 gc_ladder -> 拒绝")


_CLOADER: Q.CLoader | None = None


def _c_loader() -> Q.CLoader:
    global _CLOADER
    if _CLOADER is None:
        _CLOADER = Q.CLoader()
    if not _CLOADER.available():
        raise Skip("no C compiler (cc) here; on Windows the C kernel runs through WSL")
    return _CLOADER


def _fake_kernel(kid: str, verdict: str, crash: bool = False) -> Q.Kernel:
    def run(a, d, c):
        if crash:
            raise RuntimeError("silent kernel")
        return verdict, "test kernel"

    return Q.Kernel(id=kid, lang="test", covers=frozenset({Q.CORE, Q.L0}),
                    impl_sha256=hashlib.sha256(kid.encode()).hexdigest(),
                    rules_sha256=K.RULES_SHA256, runner=run)


_CORE_CASES: list[tuple[str, bytes, dict, dict]] = []


def _core_batch() -> list[tuple[str, bytes, dict, dict]]:
    global _CORE_CASES
    if _CORE_CASES:
        return _CORE_CASES
    art, d, c = _build()
    out = [("valid", art, d, c)]
    art2, d2, c2 = _build([SRC_A, SRC_B])
    out.append(("two units", art2, d2, c2))
    bad = bytearray(art)
    bad[len(bad) // 2] ^= 1
    out.append(("tampered artifact", bytes(bad), d, c))
    cases = [
        ("unknown rule", lambda x: x["nodes"][2].__setitem__("rule", "MAGIC")),
        ("missing premise", lambda x: x["nodes"][2].__setitem__("premises", ["nope"])),
        ("bad translation map",
         lambda x: [n["judgment"]["map"][0].__setitem__("arg", 9)
                    for n in x["nodes"] if n["rule"] == "TV-L1"]),
    ]
    for name, mutate in cases:
        dd = json.loads(json.dumps(d))
        mutate(dd)
        out.append((name, art, dd, c))
    dd = json.loads(json.dumps(d))
    for n in dd["nodes"]:
        if n["rule"] == "SEM":
            n["judgment"]["output"] = [999]
    out.append(("bad SEM expectation", art, dd, c))
    dd = json.loads(json.dumps(d))
    dd["nodes"] = [n for n in dd["nodes"] if n["rule"] != "AX-S"]
    out.append(("no AX-S", art, dd, c))
    _CORE_CASES = out
    return out


@test
def test_second_kernel_agrees_on_core_batch():
    """C6: 第二个内核（C，另一份代码）对同一批推导给同一判决。"""
    ck = _c_loader().kernel()
    for name, art, d, c in _core_batch():
        mine, why = K.verify(art, d, c)
        theirs, twhy = ck.runner(art, d, c)
        assert (theirs == Q.ACCEPT) == mine, (
            f"{name}: python={'ACCEPT' if mine else 'REJECT'} c={theirs} ({twhy})")
    print(f"      C 内核与 Python 内核在 {len(_core_batch())} 份推导上判决一致")


@test
def test_second_kernel_is_a_separate_implementation():
    """C6: 它声明同一张规则表，且不是 Python 内核的包装。"""
    ck = _c_loader().kernel()
    assert ck.lang == "c" and ck.rules_sha256 == K.RULES_SHA256, ck
    src = (Path(__file__).resolve().parent / "trust_kernel_c.c").read_text(encoding="utf-8")
    includes = set(re.findall(r"#include\s*<([^>]+)>", src))
    assert includes <= {"stdint.h", "stdio.h", "stdlib.h", "string.h"}, includes
    assert "system(" not in src and "popen(" not in src, "C 内核不该调用外部程序"
    assert ck.impl_sha256 != Q.python_kernel().impl_sha256
    print(f"      C 内核独立：includes {sorted(includes)}，规则表 {ck.rules_sha256[:16]}…")


@test
def test_quorum_accepts_with_two_independent_kernels():
    """C7: 两个零共享代码的内核都接受，k=2 接受。"""
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    art, d, c = _build()
    ok, det = Q.quorum_verify(art, d, c, [pk, ck], 2)
    assert ok, det
    assert det["profile"] == Q.CORE and det["covering"] == ["python-kernel", "c-kernel"]
    print(f"      商团接受：{det['reason']}")


@test
def test_quorum_rejects_one_tampered_kernel():
    """C7/T4: n=3,k=2，一个永远接受的内核不能放行坏产物。"""
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    sab = _fake_kernel("saboteur", Q.ACCEPT)
    art, d, c = _build()
    bad = bytearray(art)
    bad[len(bad) // 2] ^= 1
    ok, det = Q.quorum_verify(bytes(bad), d, c, [pk, ck, sab], 2)
    assert not ok and "disagree" in det["reason"], det
    print(f"      一个坏内核被两个诚实内核挡住（{det['reason']}）")


@test
def test_quorum_rejects_disagreement_on_valid_proof():
    """C7: 分歧 ⇒ 拒绝，不猜。"""
    pk = Q.python_kernel()
    liar = _fake_kernel("always-reject", Q.REJECT)
    art, d, c = _build()
    ok, det = Q.quorum_verify(art, d, c, [pk, liar], 2)
    assert not ok and "disagree" in det["reason"], det
    print(f"      接受/拒绝分歧 -> 拒绝（{det['reason']}）")


@test
def test_quorum_threshold_keeps_a_lone_saboteur_out():
    """C7: 沉默按拒绝处理；一个接受票少于 k 不能放行。"""
    sab = _fake_kernel("saboteur", Q.ACCEPT)
    silent = _fake_kernel("silent", Q.REJECT, crash=True)
    art, d, c = _build()
    bad = bytearray(art)
    bad[-3] ^= 1
    ok, det = Q.quorum_verify(bytes(bad), d, c, [sab, silent], 2)
    assert not ok, det
    assert "1 accepting" in det["reason"] or "disagree" in det["reason"], det
    print(f"      单独一个接受票不够（{det['reason']}）")


@test
def test_quorum_coverage_keeps_l0_single_kernel():
    """R4 边界: C 内核只覆盖 core；L0 档的商团按覆盖取票，不把回避当接受。"""
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    art, d, c = _build(l0_root=str(REPO))
    assert Q.profile(d) == Q.L0
    ok, det = Q.quorum_verify(art, d, c, [pk, ck], 2)
    assert not ok and "coverage" in det["reason"], det
    ok1, det1 = Q.quorum_verify(art, d, c, [pk, ck], 1)
    assert ok1 and det1["covering"] == ["python-kernel"], det1
    print(f"      L0 档只有 1 个覆盖内核；k=2 拒绝（{det['reason']}）")


@test
def test_quorum_rejects_bad_configuration():
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    art, d, c = _build()
    same = Q.Kernel(id="twin", lang="c", covers=ck.covers, impl_sha256=ck.impl_sha256,
                    rules_sha256=ck.rules_sha256, runner=ck.runner)
    foreign = Q.Kernel(id="foreign", lang="c", covers=frozenset({Q.CORE}),
                       impl_sha256=hashlib.sha256(b"foreign").hexdigest(),
                       rules_sha256="0" * 64, runner=ck.runner)
    cases = [
        ([], 1, "no kernels"),
        ([pk], 0, "1..1"),
        ([pk, ck], 3, "1..2"),
        ([pk, pk], 2, "not unique"),
        ([ck, same], 2, "share one implementation"),
        ([pk, foreign], 1, "rule table"),
    ]
    for ks, k, why in cases:
        ok, det = Q.quorum_verify(art, d, c, ks, k)
        assert not ok and why in det["reason"], (why, det)
    print(f"      六类坏配置 -> 拒绝")


@test
def test_quorum_budget_is_recorded():
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    b = Q.quorum_budget([pk, ck], 2)
    assert b["n"] == 2 and b["k"] == 2 and b["diversity_axes"] == 1
    assert len({x["impl_sha256"] for x in b["kernels"]}) == 2
    assert {x["lang"] for x in b["kernels"]} == {"python", "c"}
    with tempfile.TemporaryDirectory(prefix="trust-quorum-budget-") as td:
        out = Path(td)
        C.write_program(out, [SRC_A], quorum=b)
        trust = json.loads((out / "trust.json").read_text(encoding="utf-8"))
        assert trust["n"] == 2 and trust["k"] == 2 and trust["diversity_axes"] == 1
        assert trust["quorum"]["kernels"][1]["lang"] == "c"
    print(f"      n={b['n']} k={b['k']} axes={b['diversity_axes']} (language)")


_REFINE_OLD = "PUSH 1\nPRINT\nPUSH 2\nPRINT\nHALT\n"
_REFINE_NEW = "PUSH 1\nPRINT\nHALT\n"
_REFINE_ADDED = "PUSH 1\nPRINT\nPUSH 2\nPRINT\nPUSH 3\nPRINT\nHALT\n"


def _refine(name: str) -> tuple[bytes, dict, dict]:
    old = _REFINE_OLD
    new = _REFINE_NEW
    if name == "added":
        old, new = _REFINE_NEW, _REFINE_ADDED
    _, art, d, c = C.build_refinement([old], [new])
    return art, d, c


@test
def test_refinement_removing_a_deviation_is_accepted():
    """C11/T6: 新代只剩一个 PRINT，旧代有两个 -> 接受。"""
    art, d, c = _refine("valid")
    assert c["delta"] == {"removed": [{"unit": 0, "index": 3, "op": "PRINT"}],
                          "added": []}, c["delta"]
    ok, why = K.verify(art, d, c)
    assert ok, why
    print(f"      T' 删除 1 个偏差站点 -> 接受（{c['delta']['removed']}）")


@test
def test_refinement_adding_a_deviation_is_rejected():
    """C11: 反向或新增偏差 -> 拒绝。"""
    art, d, c = _refine("added")
    ok, why = K.verify(art, d, c)
    assert not ok and "adds deviations" in why[0], why
    print(f"      T' 新增偏差站点 -> 拒绝（{why[0]}）")


@test
def test_refinement_delta_lie_is_rejected():
    """C11: delta 是推导的一部分；少报/多报都拒。"""
    art, d, c = _refine("valid")
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "REFINE":
            n["judgment"]["delta"]["removed"] = []
    assert not K.verify(art, bad, c)[0], "少报 removed 仍然被接受"
    bad2 = json.loads(json.dumps(d))
    for n in bad2["nodes"]:
        if n["rule"] == "REFINE":
            n["judgment"]["from_deviations"] = []
    assert not K.verify(art, bad2, c)[0], "改旧代偏差集仍然被接受"
    print("      改 delta / 旧代偏差集 -> 拒绝")


@test
def test_refinement_predecessor_tamper_is_rejected():
    """C11: 旧代按内容哈希绑定；改一个字节而哈希不动 -> 拒绝。"""
    art, d, c = _refine("valid")
    bad = json.loads(json.dumps(d))
    for n in bad["nodes"]:
        if n["rule"] == "REFINE":
            h = n["judgment"]["from_container_hex"]
            n["judgment"]["from_container_hex"] = ("00" if h[:2] != "00" else "01") + h[2:]
    assert not K.verify(art, bad, c)[0], "旧代容器被改而哈希不变仍然被接受"
    bad2 = json.loads(json.dumps(d))
    for n in bad2["nodes"]:
        if n["rule"] == "REFINE":
            n["judgment"]["from_artifact_sha256"] = "0" * 64
    assert not K.verify(art, bad2, c)[0], "旧代哈希被改仍然被接受"
    print("      旧代容器 / 哈希篡改 -> 拒绝")


@test
def test_refinement_new_artifact_tamper_is_rejected():
    art, d, c = _refine("valid")
    bad = bytearray(art)
    bad[len(bad) // 2] ^= 1
    assert not K.verify(bytes(bad), d, c)[0], "新代产物被改仍然被接受"
    print("      新代产物改一个字节 -> 拒绝")


@test
def test_second_kernel_agrees_on_refinement_batch():
    """C6 × C11: C 内核也实现 REFINE，判决与 Python 一致。"""
    ck = _c_loader().kernel()
    art, d, c = _refine("valid")
    added_art, added_d, added_c = _refine("added")
    lie = json.loads(json.dumps(d))
    for n in lie["nodes"]:
        if n["rule"] == "REFINE":
            n["judgment"]["delta"]["added"] = [{"unit": 0, "index": 99, "op": "PRINT"}]
    old_tamper = json.loads(json.dumps(d))
    for n in old_tamper["nodes"]:
        if n["rule"] == "REFINE":
            n["judgment"]["from_artifact_sha256"] = "1" * 64
    batch = [("valid", art, d, c), ("added", added_art, added_d, added_c),
             ("delta lie", art, lie, c), ("old tamper", art, old_tamper, c)]
    for name, a, dd, cc in batch:
        mine, why = K.verify(a, dd, cc)
        theirs, twhy = ck.runner(a, dd, cc)
        assert (theirs == Q.ACCEPT) == mine, (
            f"{name}: python={'ACCEPT' if mine else 'REJECT'} c={theirs} ({twhy})")
    print(f"      C 内核与 Python 在 {len(batch)} 份 refinement 推导上判决一致")


@test
def test_quorum_accepts_a_refinement():
    """C7 × C11: 两内核商团接受只减不增的更新。"""
    pk, ck = Q.python_kernel(), _c_loader().kernel()
    art, d, c = _refine("valid")
    ok, det = Q.quorum_verify(art, d, c, [pk, ck], 2)
    assert ok, det
    added_art, added_d, added_c = _refine("added")
    ok2, det2 = Q.quorum_verify(added_art, added_d, added_c, [pk, ck], 2)
    assert not ok2, det2
    print(f"      商团接受只减不增的更新；新增偏差被拒（{det['reason']}）")


def main() -> int:
    failed = []
    skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Skip as e:
            skipped += 1
            print(f"  SKIP  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    passed = len(TESTS) - len(failed) - skipped
    extra = f", {skipped} 跳过" if skipped else ""
    print(f"\ntrust_kernel_test: {passed}/{len(TESTS)} 通过{extra}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
