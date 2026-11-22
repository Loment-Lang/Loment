#!/usr/bin/env python3
# trust_kernel_test.py — R0 判据 (docs/378 §6 C1/C2/C3/C4/C5 的玩具形态)
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
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trust_demo_compiler as C  # noqa: E402
import trust_kernel as K  # noqa: E402

TESTS: list[tuple[str, object]] = []
SRC_A = "PUSH 1\nPUSH 2\nADD\nPRINT\nHALT\n"
SRC_B = "PUSH 3\nPUSH 4\nADD\nPRINT\nHALT\n"


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
        assert b["rules"] == len(K.RULES) == 12
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


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\ntrust_kernel_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
