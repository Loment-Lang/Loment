#!/usr/bin/env python3
"""lnotary_test.py — LNotary 独立验证器的判据（`docs/358` §4.2 / `docs/361`）。

LNotary（Loment Notary，产物公证人）是**产物侧**的审计器：只读 Potato 与旁挂，
不 import 编译器、不 import `loment.lspe`、不 import `potato`。
它不产出证据，只核对产物里已经写下的账。

`lnotary_true` 是一个**用户开关**（`set choose` 定义）：编译器把它记进
`switches`，所以 LNotary 不需要改自举编译器或种子。

每条判据都先红：在 `loment/lnotary/verify.py` 不存在时，本文件整体失败。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOMENTC = ROOT / "tools" / "lomentc.py"
sys.path.insert(0, str(ROOT / "loment"))


def _compile(src_text: str, name: str, tmp: Path) -> dict:
    src = tmp / f"{name}.lomt"
    src.write_text(src_text, encoding="utf-8")
    out = tmp / f"{name}.potato.json"
    r = subprocess.run(
        [sys.executable, str(LOMENTC), str(src), "--emit-potato", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    return json.loads(out.read_text(encoding="utf-8"))


class LNotaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lnotary-"))
        cls.plain = _compile(
            "module plain\n\nfn main() -> i32 {\n    return 0;\n}\n",
            "plain", cls.tmp,
        )
        cls.claimed = _compile(
            "module claimed\n"
            "set choose lnotary_true {\n}\n"
            "choose lnotary_true\n\n"
            "fn main() -> i32 {\n    return 0;\n}\n",
            "claimed", cls.tmp,
        )

    def _verify(self, doc: dict):
        from lnotary import verify

        return verify.verify_potato(doc)

    # A · 真实产物被接受
    def test_accepts_a_real_v23_potato(self) -> None:
        v = self._verify(self.plain)
        self.assertTrue(v.ok, v.errors)
        self.assertGreaterEqual(v.version, 20)

    # B · `lnotary_true` 是用户开关，且进产物
    def test_low_true_is_recorded_as_a_user_switch(self) -> None:
        names = {s.get("name") for s in self.claimed.get("switches", [])}
        self.assertIn("lnotary_true", names)
        v = self._verify(self.claimed)
        self.assertTrue(v.ok, v.errors)
        self.assertIn("lnotary_true", v.claimed_switches)

    # C · 索引与字段打架
    def test_rejects_systems_cost_disagreement(self) -> None:
        doc = json.loads(json.dumps(self.plain))
        doc["systems"]["lms"]["cost"]["levels"] = 3
        v = self._verify(doc)
        self.assertFalse(v.ok)
        self.assertTrue(any(e.code.startswith("LNT-SYS") for e in v.errors), v.errors)

    # D · 回收台账的自洽
    def test_rejects_gc_ladder_sum_mismatch(self) -> None:
        doc = json.loads(json.dumps(self.plain))
        doc["gc_ladder"]["total_sites"] = int(doc["gc_ladder"]["total_sites"]) + 1
        v = self._verify(doc)
        self.assertFalse(v.ok)
        self.assertTrue(any(e.code.startswith("LNT-GC") for e in v.errors), v.errors)

    # E · no_runtime 与有状态的级不能同时成立
    def test_rejects_stateful_level_under_no_runtime(self) -> None:
        doc = json.loads(json.dumps(self.plain))
        self.assertEqual(doc.get("runtime"), "no_runtime")
        doc["lms_budgets"]["lms_manage"] = "state"
        v = self._verify(doc)
        self.assertFalse(v.ok)
        self.assertTrue(any(e.code.startswith("LNT-RT") for e in v.errors), v.errors)

    # F · 少一个系统
    def test_rejects_missing_system(self) -> None:
        doc = json.loads(json.dumps(self.plain))
        del doc["systems"]["lspe"]
        v = self._verify(doc)
        self.assertFalse(v.ok)
        self.assertTrue(any(e.code.startswith("LNT-SYS") for e in v.errors), v.errors)

    # G · 零 import：公证人不能把被公证的一侧拉进来
    def test_verifier_imports_nothing_from_the_toolchain(self) -> None:
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'loment')!r})\n"
            "import lnotary.verify\n"
            "bad = [m for m in ('lomentc', 'potato', 'potato_from', 'lspe')\n"
            "       if m in sys.modules]\n"
            "print(','.join(bad))\n"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", f"公证人把工具链拉进来了: {r.stdout.strip()}")

    # H · 版本不认识就拒
    def test_rejects_unknown_potato_version(self) -> None:
        doc = json.loads(json.dumps(self.plain))
        doc["potato"] = "v999"
        v = self._verify(doc)
        self.assertFalse(v.ok)
        self.assertTrue(any(e.code == "LNT-VER" for e in v.errors), v.errors)

    # ---- V1 · 法域旁挂与封印（docs/361 §6） --------------------------------

    def _bytes(self, name: str) -> bytes:
        return (self.tmp / f"{name}.potato.json").read_bytes()

    def _derive(self, doc: dict, name: str) -> dict:
        from lnotary import lawspace

        return lawspace.derive_lawspace(doc, self._bytes(name))

    # I · 派生是确定的
    def test_lawspace_seal_is_deterministic(self) -> None:
        a = self._derive(self.plain, "plain")
        b = self._derive(self.plain, "plain")
        self.assertEqual(a, b)
        self.assertEqual(a["seal"], b["seal"])

    # J · 自产旁挂被接受
    def test_lawspace_accepts_its_own_artifact(self) -> None:
        from lnotary import lawspace

        side = self._derive(self.plain, "plain")
        self.assertEqual(lawspace.verify_lawspace(
            self.plain, side, self._bytes("plain")), [])

    # K · 改一条规律
    def test_tampered_law_is_rejected(self) -> None:
        from lnotary import lawspace

        side = self._derive(self.plain, "plain")
        side["laws"][0]["declared"] = "runtime"
        errs = lawspace.verify_lawspace(self.plain, side, self._bytes("plain"))
        self.assertTrue(any(e.code == "LNT-LAW" for e in errs), errs)

    # L · 改封印
    def test_tampered_seal_is_rejected(self) -> None:
        from lnotary import lawspace

        side = self._derive(self.plain, "plain")
        side["seal"] = "0" * 64
        errs = lawspace.verify_lawspace(self.plain, side, self._bytes("plain"))
        self.assertTrue(any(e.code == "LNT-SEAL" for e in errs), errs)

    # M · 旁挂绑的是产物字节
    def test_artifact_bytes_bind_the_seal(self) -> None:
        from lnotary import lawspace

        side = self._derive(self.plain, "plain")
        errs = lawspace.verify_lawspace(self.plain, side, b"not the artifact")
        self.assertTrue(any(e.code == "LNT-ART" for e in errs), errs)

    # N · 维度表与产物打架
    def test_dims_mismatch_is_rejected(self) -> None:
        from lnotary import lawspace

        side = self._derive(self.plain, "plain")
        side["dims"]["runtime"] = "runtime"
        errs = lawspace.verify_lawspace(self.plain, side, self._bytes("plain"))
        self.assertTrue(any(e.code == "LNT-DIMS" for e in errs), errs)

    # O · 声明了公证就必须给旁挂
    def test_claimed_artifact_requires_a_seal(self) -> None:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment.py"),
             "lnotary", "verify", str(self.tmp / "claimed.potato.json")],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("LNT-CLAIM", r.stderr)

    # P · CLI 的 seal -> verify 往返
    def test_cli_seal_and_verify_round_trip(self) -> None:
        out = self.tmp / "plain.lawspace.json"
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment.py"),
             "lnotary", "seal", str(self.tmp / "plain.potato.json"),
             "--out", str(out)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r2 = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment.py"),
             "lnotary", "verify", str(self.tmp / "plain.potato.json"),
             "--seal", str(out)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("lawspace", r2.stdout)


if __name__ == "__main__":
    unittest.main()
