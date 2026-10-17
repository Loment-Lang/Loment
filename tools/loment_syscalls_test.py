#!/usr/bin/env python3
# loment_syscalls_test.py — Loment 版 FUAI 系统调用层生成器与 Python 版逐字节相同
# (docs/189 §3 的 S1 第二格)
#
# 判据: 同一份仓库、同一个 cwd (= 仓库根), `loment/tools/lomsyscalls.lomt` (链成 ELF 后跑)
# 与 `tools/loment_syscalls.py`:
#   1. `--check`: stdout / stderr / 退出码逐字节相同;
#   2. `--emit PATH`: 输出行相同, **写出的文件字节也相同**, 且与仓库里那份产物相同
#      (两边都往同一个临时路径写, 再比它和 `loment/build/fuai_syscalls.lomt` 的字节);
#   3. `lomsyscalls.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# **argparse 的用法文本没搬** (那条英文用法块逐字复刻没有意义) —— 判据也不比它。
#
# 运行: python tools/loment_syscalls_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

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
import loment_syscalls  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomsyscalls.lomt"
ARTIFACT = ROOT / "loment" / "build" / "fuai_syscalls.lomt"
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
    assert not errs, f"lomsyscalls.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomsyscalls.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomsyscalls.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _norm(v: str) -> str:
    """**刻意什么都不做** —— 理由同 `loment_eol_test`：归一化会改掉报告里字面的字符。"""
    return v


def _py(args: list[str]) -> tuple[int, str, str]:
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_syscalls.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomsys_{name}.bin"
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


def _pair(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, args)
    if (wr, _norm(wo), _norm(we)) == (gr, go, ge):
        return gr, go, ge
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n"
        f"  py     out={wo[-200:]!r} err={we[-200:]!r}\n"
        f"  loment out={go[-200:]!r} err={ge[-200:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_lomsyscalls_check_matches_python():
    """`--check`: stdout / stderr / 退出码逐字节相同；结论也要与产物对得上。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        rc, out, _err = _pair(elf, td, "check", ["--check"])
        assert rc == 0, f"`--check` 该退 0（产物一致），实得 {rc}: {out[:160]!r}"
        # **独立断言**：报出的原语数必须等于产物里 `pub fn fuai_` 的个数 ——
        # 只比两边相同的话，两边一起数错也会"一致"。
        n = ARTIFACT.read_text(encoding="utf-8").count("pub fn fuai_")
        assert f"({n} 个原语)" in out, f"报的个数与产物对不上（产物里 {n} 个）: {out[:160]!r}"
    print("      `--check`: 输出一致；且报出的原语数与产物独立对过")


@test
def test_lomsyscalls_emit_writes_same_bytes():
    """`--emit PATH`: 输出行相同，写出的字节相同，且与仓库里那份产物相同。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    orig = ARTIFACT.read_bytes()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        # 两边都往**同一个仓库相对路径**写（输出行里会带这个路径，所以必须同一个）
        rel = "loment/build/lomsyscalls_emit_tmp.lomt"
        dest = ROOT / rel
        try:
            rc, po, pe = _py(["--emit", rel])
            assert rc == 0, f"Python 版 --emit 失败 rc={rc}: {pe[:160]!r}"
            py_bytes = dest.read_bytes()
            gr, go, ge = _run(elf, td, "emit", ["--emit", rel])
            sh_bytes = dest.read_bytes()
            assert (rc, _norm(po), _norm(pe)) == (gr, go, ge), (
                f"输出行不一致:\n  py {po!r} {pe!r}\n  sh {go!r} {ge!r}")
            assert py_bytes == sh_bytes, f"落盘不同: {len(py_bytes)}B vs {len(sh_bytes)}B"
            assert py_bytes == orig, "两边写出的内容与仓库里的产物不同（不该）"
        finally:
            dest.unlink(missing_ok=True)
    print("      `--emit`: 输出行 + 落盘字节一致（与仓库里那份产物相同）")


@test
def test_lomsyscalls_selfhost_compiles():
    """`lomsyscalls.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
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
        binn = f"{_T}lomsys_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomsyscalls.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomsyscalls.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomsyscalls.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_syscalls_test")


if __name__ == "__main__":
    sys.exit(main())
