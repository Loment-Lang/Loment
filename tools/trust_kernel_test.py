#!/usr/bin/env python3
# trust_kernel_test.py — R0 判据 (docs/377 §6 C1/C2/C3/C4/C5 的玩具形态)
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
