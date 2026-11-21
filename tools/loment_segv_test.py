#!/usr/bin/env python3
"""loment_segv_test.py — 原生档段错误报告器（`docs/367` stage 0）。

原生产物被信号杀死时自己不说话。`tools/loment_segv.py` 在宿主用 `ptrace` 接住现场，
按 `lomenterr` 的形状报出：信号、`si_code`、故障地址、PC、RSP（可选函数/源行）。

四条：

* **A 正常程序不误报** —— 退出码原样返回，stderr 没有 `error[`。
* **B 空指针** —— rc=139，报告里有 `error[SEGV]`、`SIGSEGV`、`访问：0x0`、`PC：0x`。
* **C `llvm.trap`（ud2）** —— rc=132，报告里有 `error[ILL]`、`SIGILL`。
* **D `--json`** —— 一行 JSON，字段齐（signal / addr / pc / si_code）。

夹具是**真前端 + 真链接器**编出来的 ELF（`lomentc` -> `lomelf`），不是手写的 IR。
Windows 上经 WSL 跑（与 `loment_elf_test` 同一条路）；没有 WSL 就 SKIP。

不要与 `tools/loment_crash_test.py` 搞混：那一条管的是**编译器**容量越界要出诊断
（`#805` / `#868`–`#874`），是构建期；这一条管的是**产物运行期**的段错误报告。

用法: python tools/loment_segv_test.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "loment_segv.py"

_WSL = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    try:
        return subprocess.run([*_WSL, "bash", "-lc", "true"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(text: str, name: str, td: Path) -> Path:
    src = td / f"{name}.lomt"
    src.write_text(text, encoding="utf-8", newline="\n")
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{name}: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _meta = lomelf.compile_ll(ir)
    exe = td / name
    exe.write_bytes(blob)
    return exe


def _run(exe: Path, extra: list[str] | None = None) -> tuple[int, str]:
    """在 WSL/Linux 里跑报告器 -> `(rc, stderr 文本)`。产物与工具都按 WSL 路径给。"""
    tag = f"/tmp/segv_{os.getpid()}"
    rel = " ".join(extra or [])
    script = (f"rm -f {tag} {tag}.err; cp {_wsl_path(exe)} {tag} && chmod +x {tag} && "
              f"cd /tmp && python3 {_wsl_path(TOOL)} {rel} {tag} 2> {tag}.err; "
              f"echo -n $?")
    r = subprocess.run([*_WSL, "bash", "-lc", script], capture_output=True, timeout=240)
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    if sys.platform.startswith("linux"):
        err = Path(f"{tag}.err")
        return rc, err.read_text(encoding="utf-8") if err.exists() else ""
    got = subprocess.run([*_WSL, "bash", "-lc", f"base64 -w0 {tag}.err 2>/dev/null || true"],
                         capture_output=True, timeout=120).stdout
    raw = base64.b64decode(got) if got.strip() else b""
    return rc, raw.decode("utf-8", "replace")


_OK = """module segv_ok
choose runtime

fn _start() {
    syscall4(60, 7, 0, 0);
}
"""

_NULL = """module segv_null
choose runtime
use bytes

fn _start() {
    let z: u64 = 0;
    let p: ptr = z as ptr;
    store32(p, 0, 1);
    syscall4(60, 0, 0, 0);
}
"""


def test_a_normal_program_is_not_flagged() -> None:
    """判 A：正常退出码原样返回，报告器不插一句。"""
    if not _wsl():
        print("      SKIP: 没有 WSL/Linux")
        return
    with tempfile.TemporaryDirectory() as tds:
        exe = _build(_OK, "segv_ok", Path(tds))
        rc, err = _run(exe)
    assert rc == 7, (rc, err[:300])
    assert "error[" not in err, err[:300]
    print("      正常程序：rc=7，没有 error[ 行")


def test_b_null_pointer_is_reported() -> None:
    """判 B：空指针写 -> 139，报告有信号、地址、PC。"""
    if not _wsl():
        print("      SKIP: 没有 WSL/Linux")
        return
    with tempfile.TemporaryDirectory() as tds:
        exe = _build(_NULL, "segv_null", Path(tds))
        rc, err = _run(exe)
    assert rc == 139, (rc, err[:300])
    for want in ("error[SEGV]:", "SIGSEGV", "访问：0x0", "PC：0x", "= 事实：", "= 建议："):
        assert want in err, (want, err[:500])
    print("      空指针：rc=139，信号/地址/PC/事实/建议都在")


def test_c_trap_is_an_ill() -> None:
    """判 C：`ud2` 是 SIGILL（原生档的 `llvm.trap`）-> rc=132。"""
    if not _wsl():
        print("      SKIP: 没有 WSL/Linux")
        return
    ir = """declare void @llvm.trap()

define void @_start() {
entry:
  call void @llvm.trap()
  ret void
}
"""
    blob, _meta = lomelf.compile_ll(ir)
    with tempfile.TemporaryDirectory() as tds:
        exe = Path(tds) / "segv_trap"
        exe.write_bytes(blob)
        rc, err = _run(exe)
    assert rc == 132, (rc, err[:300])
    for want in ("error[ILL]:", "SIGILL", "PC：0x"):
        assert want in err, (want, err[:400])
    print("      llvm.trap：rc=132，报告是 ILL")


def test_d_json_is_machine_readable() -> None:
    """判 D：`--json` 一行一条，字段齐。"""
    if not _wsl():
        print("      SKIP: 没有 WSL/Linux")
        return
    with tempfile.TemporaryDirectory() as tds:
        exe = _build(_NULL, "segv_json", Path(tds))
        rc, err = _run(exe, ["--json"])
    assert rc == 139, (rc, err[:300])
    row = json.loads(err.strip().splitlines()[-1])
    for key in ("exe", "signal", "signal_name", "si_code", "addr", "pc", "rsp"):
        assert key in row, (key, row)
    assert row["signal"] == 11 and row["addr"] == 0, row
    print(f"      --json：signal={row['signal']} addr=0x{row['addr']:x} pc=0x{row['pc']:x}")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_segv_test")


if __name__ == "__main__":
    sys.exit(main())
