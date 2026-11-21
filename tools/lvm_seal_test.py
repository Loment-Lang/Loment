#!/usr/bin/env python3
"""lvm_seal_test.py — 盘古的三件底层设施（`docs/358` / `docs/363`）。

* **封印**：产物哈希 + 逐站点台账 + 基质剖面 + 规律/排除 + 创世预算 + 总哈希。
* **独立验证器**：只读产物与封印，**零 import 工具链**，自己扫 IR 重算。
* **同产物创世衰减**：capset ⊆ 父、预算 ≤ 父、深度/子数/总数不超封印、派生确定。

九条：

* **A** 真产物的封印能验证通过；站点数 > 0。
* **B** 篡改产物 / 台账 / 总哈希 —— 三种都红。
* **C** `hw_cap` 在本机**降级**（guarantees 空、规律 floored）仍然自洽；伪造硬件保证则红。
* **C2** `sw_tag` 的 `no_dangling` 只能是 `floored`（声明跟着基质降级）。
* **D** 时间桥梁：同一份产物在 `sw_cap` 与 `hw_cap` 封印下 `artifact.sha256` 相同（B5）。
* **E** 创世衰减：越权/超预算/超深度被拒；同父同差量逐字节同。
* **F** 验证器零 import：子进程里挡住编译器/链接器/LSPE，它照样给出判决。
* **G** 验证器的字节/行数进封印，超预算就红；验证器文件本身也对得上。

用法: python tools/lvm_seal_test.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm_seal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "lvm_seal.py"

_PROG = """module seal_prog
choose runtime
use bytes

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        s = add(s, a[i]);
        i = i + 1;
    }
    let p: ptr = alloc(16);
    store32(p, 0, s);
    let v: u32 = load32(p, 0);
    free(p);
    syscall4(60, v as u64, 0, 0);
}
"""


def _ir() -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "seal_prog.lomt"
        src.write_text(_PROG, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        return lomentc.emit_llvm(mod, ROOT, deps)


def test_emit_verify_roundtrip() -> None:
    """判 A：真产物 -> 封印 -> 验证通过；台账非空。"""
    ir = _ir()
    seal = lvm_seal.build_seal(ir, "sw_cap")
    assert seal["artifact"]["site_count"] > 0, "站点台账是空的"
    got = lvm_seal.verify(ir, seal)
    assert got["ok"], got["reasons"]
    laws = {l["id"]: l["verdict"] for l in seal["laws"]}
    assert laws["no_segv"] == "checked", laws
    assert laws["no_dangling"] == "checked", laws
    assert "site ledger" in got["checked"]
    print(f"      封印 = {seal['artifact']['site_count']} 个站点；"
          f"sw_cap 下三条规律都 checked")


def test_tampering_is_red() -> None:
    """判 B：改产物 / 改台账 / 改总哈希 —— 三种都红。"""
    ir = _ir()
    seal = lvm_seal.build_seal(ir, "sw_cap")

    other = ir.replace("add i32", "sub i32", 1)
    assert other != ir
    got = lvm_seal.verify(other, seal)
    assert not got["ok"] and any("artifact" in r for r in got["reasons"]), got

    bad = json.loads(json.dumps(seal))
    bad["sites"][0]["kind"] = "store" if bad["sites"][0]["kind"] != "store" else "load"
    got = lvm_seal.verify(ir, bad)
    assert not got["ok"] and any("site ledger" in r for r in got["reasons"]), got

    bad2 = json.loads(json.dumps(seal))
    bad2["artifact"]["site_count"] += 1
    got = lvm_seal.verify(ir, bad2)
    assert not got["ok"] and any("sum" in r for r in got["reasons"]), got
    print("      产物 / 台账 / 总哈希：三种篡改都红")


def test_hw_cap_degrades_or_refuses() -> None:
    """判 C：本机没有 CHERI，`hw_cap` 必须降级；伪造硬件保证必须红（B6）。"""
    ir = _ir()
    degraded = lvm_seal.build_seal(ir, "hw_cap")
    assert degraded["substrate"]["guarantees"] == [], degraded["substrate"]
    assert all(l["verdict"] == "floored" for l in degraded["laws"]), degraded["laws"]
    got = lvm_seal.verify(ir, degraded)
    assert got["ok"], got["reasons"]

    forged = json.loads(json.dumps(degraded))
    forged["substrate"]["guarantees"] = lvm_seal.SUBSTRATES["hw_cap"]["guarantees"]
    forged["sum"] = lvm_seal.sum_of(forged)
    got = lvm_seal.verify(ir, forged)
    assert not got["ok"], "伪造的 hw_cap 保证被接受了"
    assert any("honour" in r for r in got["reasons"]), got
    print("      hw_cap 在本机：降级自洽；伪造硬件保证 -> 红")


def test_sw_tag_declares_no_dangling_floored() -> None:
    """判 C2：`sw_tag` 的 `no_dangling` 只能是 `floored` —— 声明跟着基质降级。"""
    ir = _ir()
    seal = lvm_seal.build_seal(ir, "sw_tag")
    laws = {l["id"]: l["verdict"] for l in seal["laws"]}
    assert laws["no_segv"] == "checked", laws
    assert laws["no_dangling"] == "floored", laws
    assert "P3.gen" not in seal["substrate"]["guarantees"], seal["substrate"]
    assert "P3.probabilistic" in seal["substrate"]["assumptions"], seal["substrate"]
    got = lvm_seal.verify(ir, seal)
    assert got["ok"], got["reasons"]
    print("      sw_tag：no_segv checked / no_dangling floored / P3 进 assumptions")


def test_time_bridge_keeps_artifact_hash() -> None:
    """判 D（B5）：换基质不换字节码 —— 两份封印的 artifact.sha256 相同。"""
    ir = _ir()
    a = lvm_seal.build_seal(ir, "sw_cap")
    b = lvm_seal.build_seal(ir, "hw_cap")
    assert a["artifact"]["sha256"] == b["artifact"]["sha256"]
    # 站点的**身份**（哪条指令是访存）不随基质变；变的只是 grade。
    ident = lambda s: [(x["id"], x["fn"], x["blk"], x["idx"], x["offset"], x["kind"])
                       for x in s["sites"]]
    assert ident(a) == ident(b), "换基质居然挪动了站点"
    assert a["sum"] != b["sum"], "换基质居然没改封印 —— 剖面没进哈希"
    print("      同一份产物：sw_cap 与 hw_cap 的字节码哈希相同、站点身份相同，封印不同")


def test_derive_attenuates() -> None:
    """判 E：越权 / 超预算 / 超深度都拒；同父同差量逐字节同。"""
    seal = lvm_seal.build_seal(_ir(), "sw_cap")
    root = lvm_seal.root_universe(seal, ["file_read", "net_connect", "domain:blk"],
                                  4096, 1_000_000)
    child, why = lvm_seal.derive(root, {"caps": ["file_read"],
                                        "budget": {"heap": 2048, "steps": 100}})
    assert child is not None, why
    assert child["caps"] == ["file_read"]
    assert child["depth"] == 1 and child["total"] == 2

    same, _ = lvm_seal.derive(root, {"caps": ["file_read"],
                                     "budget": {"heap": 2048, "steps": 100}})
    assert same == child, "同父同差量派生出了不同结果"

    _, why = lvm_seal.derive(root, {"caps": ["file_read", "root"],
                                    "budget": {"heap": 1, "steps": 1}})
    assert "cap_escalation" in why, why
    _, why = lvm_seal.derive(root, {"caps": [],
                                    "budget": {"heap": 4097, "steps": 1}})
    assert "budget_escalation:heap" in why, why

    u = root
    for _ in range(4):
        u, why = lvm_seal.derive(u, {"caps": [], "budget": {"heap": 1, "steps": 1}})
        assert u is not None, why
    u2, why = lvm_seal.derive(u, {"caps": [], "budget": {"heap": 1, "steps": 1}})
    assert u2 is None and "depth_exceeded" in why, why

    bad = json.loads(json.dumps(root))
    bad["caps"].append("root")
    _, why = lvm_seal.derive(bad, {"caps": [], "budget": {"heap": 0, "steps": 0}})
    assert "parent_sum_mismatch" in why, why
    print("      子集 / 预算 / 深度 / 父哈希：四条拒绝面都动；同输入同结果")


def test_verifier_is_zero_import() -> None:
    """判 F（V1）：子进程挡住编译器/链接器/LSPE，验证器照样判。"""
    ir = _ir()
    seal = lvm_seal.build_seal(ir, "sw_cap")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "a.ll").write_text(ir, encoding="utf-8", newline="\n")
        (tdp / "a.seal").write_text(json.dumps(seal), encoding="utf-8", newline="\n")
        script = f"""
import sys
BLOCK = {{"lomentc", "lomelf", "lvm", "lspe", "loment"}}
class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCK:
            raise RuntimeError("blocked import: " + name)
        return None
sys.meta_path.insert(0, Blocker())
import runpy
sys.argv = ["lvm_seal.py", "verify", "--ir", {str(tdp / 'a.ll')!r},
            "--seal", {str(tdp / 'a.seal')!r}]
runpy.run_path({str(TOOL)!r}, run_name="__main__")
"""
        r = subprocess.run([sys.executable, "-I", "-c", script],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"验证器被挡住了：{r.stdout[-300:]} {r.stderr[-300:]}"
    src = TOOL.read_text(encoding="utf-8")
    for bad in ("import lomentc", "import lomelf", "import lvm", "import lspe"):
        assert not re.search(rf"^\s*{re.escape(bad)}\b", src, re.M), f"验证器 import 了 {bad}"
    print("      子进程：挡住 lomentc/lomelf/lvm/lspe，验证器仍 PASS")


def test_verifier_price_is_in_the_seal() -> None:
    """判 G：验证器字节/行数进封印；超预算或字节对不上都红。"""
    ir = _ir()
    meta = lvm_seal._verifier_meta()
    assert meta["bytes"] > 0 and meta["lines"] > 0
    seal = lvm_seal.build_seal(ir, "sw_cap")
    got = lvm_seal.verify(ir, seal, verifier_bytes=TOOL.read_bytes())
    assert got["ok"], got["reasons"]

    got = lvm_seal.verify(ir, seal, verifier_bytes=b"x")
    assert not got["ok"] and any("verifier" in r for r in got["reasons"]), got

    with tempfile.TemporaryDirectory() as td:
        big = Path(td) / "big.py"
        big.write_bytes(b"#" * (lvm_seal.MAX_VERIFIER_BYTES + 1))
        seal2 = lvm_seal.build_seal(ir, "sw_cap", verifier_path=big)
        got = lvm_seal.verify(ir, seal2)
        assert not got["ok"] and any("budget" in r for r in got["reasons"]), got
    print(f"      验证器 {meta['lines']} 行 / {meta['bytes']} 字节；超预算与错字节都红")


def test_scan_is_complete_over_the_artifact() -> None:
    """判 H：独立数一遍访存 —— 扫描器不许漏（`docs/358` A 链的相对完备）。"""
    ir = _ir()
    sites = lvm_seal.scan_sites(ir)
    counted = 0
    for raw in ir.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith(("define", "declare", "}", "@")) or line.endswith(":"):
            continue
        body = line.split("=", 1)[1].strip() if "=" in line else line
        word = body.split()[0] if body else ""
        if word in ("load", "store", "getelementptr", "atomicrmw"):
            counted += 1
        elif word == "call" and 'asm sideeffect "syscall"' in body:
            counted += 1
    assert len(sites) == counted, f"台账 {len(sites)} 条，独立数出 {counted} 条"
    ids = [s["id"] for s in sites]
    assert ids == list(range(len(sites))), "站点编号不连续"
    print(f"      独立数出 {counted} 条访存 == 台账 {len(sites)} 条")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_seal_test")


if __name__ == "__main__":
    sys.exit(main())
