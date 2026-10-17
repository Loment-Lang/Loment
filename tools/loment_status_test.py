#!/usr/bin/env python3
# loment_status_test.py — Loment 版里程碑状态矩阵与 Python 版逐字节相同 (docs/167 Stage 3)
#
# 判据: 同一份 `docs/145-loment-100-milestones.md`, `loment/tools/lomstatus.lomt` (链成 ELF 后跑)
# 与 `tools/loment_status.py`:
#   1. 无参数 (门禁模式) 的 **stdout / stderr / 退出码**逐字节相同;
#   2. `--emit` **写出的文件**逐字节相同, 且 stdout / stderr 也相同;
#   3. `--check` 在**产物被动过**时两边给出同样的结论 (退出码 1 + 同一句 [DIFF]);
#   4. `lomstatus.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# 运行: python tools/loment_status_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_status  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomstatus.lomt"
OUTP = ROOT / "docs" / "154-loment-status.md"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomstatus.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomstatus.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomstatus.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _norm(v: str) -> str:
    """只归一路径分隔符 (Windows 的 Python 打印 docs\\154-..., Linux 侧是 docs/154-...)。"""
    return v.replace(chr(92), "/")


def _py(args: list[str]) -> tuple[int, str, str]:
    """进程内跑 Python 版并捕获两路输出 (见 loment_pkg_test 的同一理由: 避免换行翻译)。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_status.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomstatus_{name}.bin"
    quoted = " ".join(args)
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {quoted} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, err


def _pair(elf: Path, td: Path, name: str, args: list[str]) -> None:
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, args)
    if (wr, _norm(wo), _norm(we)) == (gr, go, ge):
        return
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n"
        f"  py     out={wo[-160:]!r} err={we[-160:]!r}\n"
        f"  loment out={go[-160:]!r} err={ge[-160:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_lomstatus_matches_python():
    """无参数 (门禁模式) 与 `--check`: stdout / stderr / 退出码逐字节相同。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        _pair(elf, td, "noargs", [])
        _pair(elf, td, "check", ["--check"])
    print("      无参数 / --check: stdout + stderr + 退出码一致")


@test
def test_lomstatus_emit_writes_same_bytes():
    """`--emit`: 写出的文件逐字节相同, stdout / stderr 也相同 (并把原文件还原)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    orig = OUTP.read_bytes()
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            # 先让 Python 版写一遍, 存下字节
            rc, po, pe = _py(["--emit"])
            assert rc == 0, f"python --emit 失败 rc={rc}"
            py_bytes = OUTP.read_bytes()
            # 再让 Loment 版写一遍
            gr, go, ge = _run(elf, td, "emit", ["--emit"])
            sh_bytes = OUTP.read_bytes()
            assert (rc, _norm(po), _norm(pe)) == (gr, go, ge), (
                f"stdout/stderr 不一致:\n  py {po!r} {pe!r}\n  sh {go!r} {ge!r}")
            assert py_bytes == sh_bytes, f"落盘不同: {len(py_bytes)}B vs {len(sh_bytes)}B"
            assert py_bytes == orig, "两边写出的内容与仓库里的 docs/154 不同 (不该)"
    finally:
        OUTP.write_bytes(orig)
    print("      --emit: 落盘字节 + 输出行一致 (与仓库现有 docs/154 相同)")


@test
def test_lomstatus_detects_drift():
    """产物被动过时, 两边给同样的 [DIFF] 与退出码 1。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    orig = OUTP.read_bytes()
    try:
        OUTP.write_bytes(orig + b"// drift\n")
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            _pair(elf, td, "drift", [])
    finally:
        OUTP.write_bytes(orig)
    print("      漂移检出: 退出码与 [DIFF] 一致")


@test
def test_lomstatus_selfhost_compiles():
    """lomstatus.lomt 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}lomstatus_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomstatus.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomstatus.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 50000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomstatus.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_status_test")


if __name__ == "__main__":
    sys.exit(main())
