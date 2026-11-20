#!/usr/bin/env python3
"""`lmgr/` 的判据（`docs/303`）。

量三件事：注册表是 42 槽、能力表是 19 项、二进制能把两者如实说出去。
编译走参考实现（lomentc → lomelf），不经安装包 —— 装的包可能落后几十个提交。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lmgr.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _blob(fn_name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    m = re.search(rf'fn {fn_name}\(\) -> str {{\s*return "([^"]*)";', text)
    assert m, f"{fn_name} not found in {SRC}"
    return m.group(1)


def _records(blob: str) -> list[str]:
    return [r for r in blob.split(";") if r]


@test
def test_registry_has_42_slots():
    recs = _records(_blob("registry"))
    assert len(recs) == 42, len(recs)
    keys = [r.split(",")[0] for r in recs]
    assert len(set(keys)) == 42, "duplicate slot keys"
    implemented = []
    for r in recs:
        name, mask, done = r.split(",")
        assert "." in name, r
        assert int(mask) > 0, r
        assert done in ("0", "1"), r
        if done == "1":
            implemented.append(name)
    # 只有真能出数的槽位才许写 1；能力齐了但没实现也是 0。
    assert implemented == ["cpu.load", "mem.phys", "mem.proc", "proc.tree"], implemented


@test
def test_effect_domain_is_registered_but_unimplemented():
    """效应管理器（`docs/341`）隶属 LMGR：四个槽位在册，但今天一个都不出数。

    “在册”与“实现”是两件事：登记让 `lmgr list` / 选择页看见它，`done=0` 让状态
    如实报 unavailable（`docs/303` §3.5）。台账（A2c）与运行期（A3）之前不许写 1。
    """
    recs = _records(_blob("registry"))
    eff = [r.split(",") for r in recs if r.split(",")[0].startswith("effect.")]
    assert [e[0] for e in eff] == ["effect.list", "effect.row",
                                  "effect.handler", "effect.audit"], eff
    assert all(e[1] == "1" for e in eff), eff      # 只要求 fs.read
    assert all(e[2] == "0" for e in eff), eff      # 一个都不许冒充已实现


@test
def test_capability_table_has_19_entries():
    recs = _records(_blob("caps_blob"))
    assert len(recs) == 19, len(recs)
    bits = [int(r.split(",")[1]) for r in recs]
    assert len(set(bits)) == 19, "duplicate capability bits"
    assert sum(bits) == 2 ** 19 - 1, "capability bits are not one bit each"


def _build(tmp: Path) -> Path:
    ll = tmp / "lmgr.ll"
    potato = tmp / "lmgr.potato.json"
    pe = os.name == "nt"
    exe = tmp / ("lmgr.exe" if pe else "lmgr")
    env = dict(os.environ, PYTHONUTF8="1")
    subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"), str(SRC),
                    "--emit-llvm", str(ll), "--emit-potato", str(potato)],
                   check=True, cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", env=env)
    subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"), str(ll),
                    "-o", str(exe), "--target", "pe" if pe else "elf"],
                   check=True, cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", env=env)
    return exe


def _run_json(args: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="lmgr-test-") as d:
        exe = _build(Path(d))
        out = subprocess.run([str(exe), *args], check=True,
                             capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout)


@test
def test_list_json_names_every_slot_once():
    doc = _run_json(["list", "--json"])
    assert doc["schema"] == "lmgr/v1"
    assert doc["manager"] == "list"
    keys = [r["key"] for r in doc["rows"]]
    assert len(keys) == 42, len(keys)
    assert len(set(keys)) == 42, "duplicate keys in the envelope"
    assert doc["meta"]["slots"] == 42
    for r in doc["rows"]:
        assert r["state"] in ("available", "degraded", "unavailable"), r


@test
def test_cpu_load_reports_a_percentage_or_refuses():
    with tempfile.TemporaryDirectory(prefix="lmgr-test-") as d:
        exe = _build(Path(d))
        out = subprocess.run([str(exe), "cpu", "load", "--json"],
                             capture_output=True, text=True, encoding="utf-8")
    if out.returncode == 2:
        # 没有 PDH 的平台必须**点名拒绝**，不是输出一个假设的数。
        assert "win.pdh" in out.stdout or "failed" in out.stdout, out.stdout
        return
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["manager"] == "cpu.load"
    v = doc["rows"][0]["fields"]["busy_pct"]
    assert 0 <= v <= 100, v


@test
def test_mem_slots_report_or_refuse():
    with tempfile.TemporaryDirectory(prefix="lmgr-test-") as d:
        exe = _build(Path(d))
        for slot, cap in (("phys", "win.mem"), ("proc", "win.proc")):
            out = subprocess.run([str(exe), "mem", slot, "--json"],
                                 capture_output=True, text=True, encoding="utf-8")
            if out.returncode == 2:
                assert cap in out.stdout, out.stdout
                continue
            assert out.returncode == 0, out.stderr
            doc = json.loads(out.stdout)
            assert doc["manager"] == f"mem.{slot}"
            assert doc["rows"], doc
            if slot == "phys":
                fields = doc["rows"][0]["fields"]
                assert fields["total_mib"] > 0
                assert 0 <= fields["load_pct"] <= 100


@test
def test_proc_tree_reports_or_refuses():
    with tempfile.TemporaryDirectory(prefix="lmgr-test-") as d:
        exe = _build(Path(d))
        out = subprocess.run([str(exe), "proc", "tree", "--json"],
                             capture_output=True, text=True, encoding="utf-8")
    if out.returncode == 2:
        assert "win.proc" in out.stdout, out.stdout
        return
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["manager"] == "proc.tree"
    assert doc["rows"], doc
    for r in doc["rows"]:
        assert r["key"].startswith("pid:")
        assert r["fields"]["name"], r
        assert r["fields"]["threads"] >= 0, r


@test
def test_doctor_json_reports_every_capability():
    doc = _run_json(["doctor", "--json"])
    caps = doc["capabilities"]
    assert len(caps) == 19, len(caps)
    assert all(isinstance(c["ok"], bool) for c in caps)
    assert doc["meta"]["total"] == 19


def main() -> int:
    return loment_suite.run([fn for _name, fn in TESTS], "lmgr_test")


if __name__ == "__main__":
    sys.exit(main())
