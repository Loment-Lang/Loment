#!/usr/bin/env python3
"""`lmgr/` 的判据（`docs/303`）。

量三件事：注册表是 38 槽、能力表是 19 项、二进制能把两者如实说出去。
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
SRC = ROOT / "lmgr" / "main.lomt"
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
def test_registry_has_38_slots():
    recs = _records(_blob("registry"))
    assert len(recs) == 38, len(recs)
    keys = [r.split(",")[0] for r in recs]
    assert len(set(keys)) == 38, "duplicate slot keys"
    for r in recs:
        name, mask = r.split(",")
        assert "." in name, r
        assert int(mask) > 0, r


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
    assert len(keys) == 38, len(keys)
    assert len(set(keys)) == 38, "duplicate keys in the envelope"
    assert doc["meta"]["slots"] == 38
    for r in doc["rows"]:
        assert r["state"] in ("available", "degraded", "unavailable"), r


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
