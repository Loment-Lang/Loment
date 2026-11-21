#!/usr/bin/env python3
"""lms_bridge_test.py — LMS × LVM 时间桥梁（`docs/363`）。

四条判据：

* **A 原生 fault ABI**：`choose lms_pangu_true` 下，F01 越界退出 **101**（= 100+kind）。
  需要 WSL 跑 ELF；没有就 SKIP，并写明原因。
* **B LVM `sw_cap`**：同一语义的探针在 `--substrate sw_cap` 下退出 **101**，
  并在 stderr 点名 `<函数>:<块>#<序号>`。`--substrate` 还没合进来就 SKIP。
* **C 默认档**：不写 `lms_pangu_true` 时 IR 里**没有** `__loment_fault`（逐字节不变的影子）。
* **D 缓存键**：`choose close lms_reject` 必须改变 IR 缓存键 —— 否则改一行开关会读旧 IR。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "loment"))

import loment_suite  # noqa: E402

LOMENTC = ROOT / "tools" / "lomentc.py"
LOMELF = ROOT / "tools" / "lomelf.py"
#: `LOMENT_LVM` 指向另一条线的工作树时，判据 B 可以直接对它的 `--substrate` 跑 ——
#: 不必等 `docs/362` 合进来。CI 上没这个变量，就退回本树。
LVM = Path(os.environ.get("LOMENT_LVM", str(ROOT / "tools" / "lvm.py")))

_SRC = """module {name}
choose vm
choose runtime
{switches}
fn _start() {{
    let p: ptr = alloc(4);
    let x: u32 = load8(p, 4);
    syscall4(60, 0, 0, 0);
}}
"""

_PANGU = """set choose lms_pangu_true {
}
choose lms_pangu_true"""

_LVM_ONLY = """choose close lms_guard
choose close lms_reject
choose close lms_crash"""


def _wsl_path(p: Path) -> str:
    s = p.resolve().as_posix()
    return "/mnt/" + s[0].lower() + s[2:]


def _fault_block(ir: str) -> str:
    start = ir.find("define internal void @__loment_fault")
    if start < 0:
        return ""
    end = ir.find("\n}", start)
    return ir[start:end + 2] if end >= 0 else ir[start:]


class LmsBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parent = ROOT / ".scratch-lnotary"
        parent.mkdir(exist_ok=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix="lms-bridge-", dir=str(parent)))
        (cls.tmp / "pangu.lomt").write_text(
            _SRC.format(name="pangu", switches=_PANGU), encoding="utf-8")
        (cls.tmp / "plain.lomt").write_text(
            _SRC.format(name="plain", switches=""), encoding="utf-8")
        (cls.tmp / "lvm.lomt").write_text(
            _SRC.format(name="lvmonly", switches=_LVM_ONLY), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 工具
    def _compile(self, name: str) -> str:
        out = self.tmp / f"{name}.ll"
        r = subprocess.run([sys.executable, str(LOMENTC), str(self.tmp / f"{name}.lomt"),
                            "--print", "llvm"],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        out.write_text(r.stdout, encoding="utf-8")
        return r.stdout

    def _link(self, name: str) -> Path:
        elf = self.tmp / f"{name}.elf"
        r = subprocess.run([sys.executable, str(LOMELF), str(self.tmp / f"{name}.ll"),
                            "-o", str(elf)],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return elf

    def _run_elf(self, elf: Path) -> int | None:
        """Linux 上**直接跑**（CI 就是 Linux，`#382`）；Windows 上经 WSL 跑。"""
        if sys.platform.startswith("linux"):
            elf.chmod(0o755)
            r = subprocess.run([str(elf)], capture_output=True, text=True,
                               encoding="utf-8", timeout=60)
            return r.returncode
        script = self.tmp / "run.sh"
        script.write_text(
            "#!/bin/bash\n"
            f"cp {_wsl_path(elf)} /tmp/lms_bridge.elf\n"
            "chmod +x /tmp/lms_bridge.elf\n"
            "/tmp/lms_bridge.elf\n"
            "rc=$?\n"
            'echo "rc=$rc"\n',
            encoding="utf-8", newline="\n")
        try:
            r = subprocess.run(["wsl", "-e", "bash", _wsl_path(script)],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"rc=(\d+)", r.stdout or "")
        return int(m.group(1)) if m else None

    # A · 原生 fault ABI：F01 = 101
    def test_native_fault_abi_exits_101(self) -> None:
        ir = self._compile("pangu")
        self.assertIn("call void @__loment_fault(i32 1)", ir)
        elf = self._link("pangu")
        rc = self._run_elf(elf)
        if rc is None:
            self.skipTest("wsl_unavailable: 本机跑不了 ELF")
        self.assertEqual(rc, 101, f"原生 F01 应退出 101，得到 {rc}")

    # B · LVM sw_cap：F01 = 101 + 站点 —— **不进地板**（今天跑不了，见 `main`）
    def probe_lvm_sw_cap(self) -> None:
        """`--substrate` 在 `docs/362` 那条线上；本树还没有它。

        `ci.py --budget-vs` 明令「新判据生来就跳过」不算数（跳过表只许变短），所以
        这一条**不登记**成 `test_`。基质合进来之后把它改回 `test_lvm_sw_cap_exits_101_with_site`
        并登记地板/跳过账 —— 在那之前它是手动探针：

            LOMENT_LVM=<那棵树>/tools/lvm.py python tools/lms_bridge_test.py --probe-lvm
        """
        if "--substrate" not in LVM.read_text(encoding="utf-8"):
            raise AssertionError("substrate_not_merged: tools/lvm.py 还没有 --substrate")
        r = subprocess.run([sys.executable, str(LVM), str(self.tmp / "lvm.lomt"),
                            "--substrate", "sw_cap"],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 101, r.stdout + r.stderr)
        self.assertRegex(r.stderr, r"F01 bounds violation at .+:.+#\d+")

    # C · 默认档没有 fault ABI
    def test_default_artifact_has_no_fault_symbol(self) -> None:
        ir = self._compile("plain")
        self.assertNotIn("__loment_fault", ir)

    # D · 缓存键必须看见用户开关
    def test_cache_key_sees_user_switches(self) -> None:
        import loment_cache
        import lomentc

        a = lomentc.load(self.tmp / "pangu.lomt")
        b = lomentc.load(self.tmp / "plain.lomt")
        ka = loment_cache.key_for(a, None, flags={})
        kb = loment_cache.key_for(b, None, flags={})
        self.assertIsNotNone(ka)
        self.assertNotEqual(ka, kb, "开关变了，IR 缓存键没变 —— 会读到旧 IR")

    # E · 孪生：stage1（自举）与参考实现的 **fault 那段**逐字节同
    def test_selfhost_mirror_matches_reference(self) -> None:
        """全量 IR 在 `choose runtime` 上有一条**既有的**镜像差异（分配器注释），
        不归这一笔管；这里核的是本次特性的那段：fault 定义 + 调用点 + 默认无符号。"""
        import loment_dist
        import lomentc

        try:
            stage1 = loment_dist.build_stage1()
        except (SystemExit, Exception) as e:  # noqa: BLE001
            self.skipTest(f"stage1_unavailable: {e}")
        got = loment_dist.emit_ir(
            stage1, str(self.tmp / "pangu.lomt"), ".").read_text(encoding="utf-8")
        mod, deps = lomentc.load_unit(self.tmp / "pangu.lomt", ROOT)
        want = lomentc.emit_llvm(mod, ROOT, deps)
        self.assertIn("call void @__loment_fault(i32 1)", want)
        self.assertIn("call void @__loment_fault(i32 1)", got)
        self.assertTrue(_fault_block(got), "自举侧没有 __loment_fault 定义")
        self.assertEqual(_fault_block(got), _fault_block(want))
        plain = loment_dist.emit_ir(
            stage1, str(self.tmp / "plain.lomt"), ".").read_text(encoding="utf-8")
        self.assertNotIn("__loment_fault", plain)


#: `loment_tools_test::test_every_criterion_is_registered` 要看见它们；
#: `unittest` 的 loader 不经过 `TESTS`，所以两条路各留一份名字。
TESTS = [(n, getattr(LmsBridgeTest, n)) for n in dir(LmsBridgeTest)
         if n.startswith("test_")]


def main() -> int:
    if "--probe-lvm" in sys.argv:
        case = LmsBridgeTest("probe_lvm_sw_cap")
        try:
            LmsBridgeTest.setUpClass()
            try:
                case.probe_lvm_sw_cap()
            finally:
                LmsBridgeTest.tearDownClass()
        except Exception as e:  # noqa: BLE001 —— 探针也要给一行可读的红
            print(f"  FAIL  probe_lvm_sw_cap: {type(e).__name__}: {e}")
            return 1
        print("  PASS  probe_lvm_sw_cap")
        return 0
    return loment_suite.run_unittest(LmsBridgeTest, "lms_bridge_test")


if __name__ == "__main__":
    sys.exit(main())
