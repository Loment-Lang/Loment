#!/usr/bin/env python3
# loment_eol_test.py — Loment 版行尾门禁与 Python 版逐字节相同 (docs/189 §3 的 S1 第一格)
#
# 判据: **同一份仓库、同一个 cwd (= 仓库根)**, `loment/tools/lomeol.lomt` (链成 ELF 后跑) 与
# `tools/loment_eol.py`:
#   1. **无参数那一趟**的 stdout / stderr / 退出码逐字节相同;
#   2. **真有 CRLF 时**也逐字节相同 —— 造一个 CRLF 的新 `.lomt`, 走报告的第二条路
#      (只比干净那一趟是不够的: 两条路都绿才是"报告搬对了");
#   3. `lomeol.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# **`--fix` 那一趟没有搬** (要写盘), 判据也不比它 —— 见 `docs/189` §3 的 S1 表。
#
# 运行: python tools/loment_eol_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

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
import loment_eol  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 `/tmp` 是所有 `wsl -e` 调用共用的，
#: 固定文件名在并发跑门禁时会让两个进程互相跑对方的二进制 —— 那是**错结果**，不是慢）。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomeol.lomt"
#: 漂移用例用的探针：`.gitattributes` 里 `*.lomt text eol=lf`，所以一个新造的 CRLF `.lomt`
#: 会以 `i/ w/crlf attr/text eol=lf` 出现在 `git ls-files --eol` 里。
PROBE = ROOT / "loment" / "build" / "zz_crlf_probe.lomt"
PROBE_BYTES = b"module zzprobe\r\n\r\nfn f() -> u32 {\r\n    return 0;\r\n}\r\n"
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
    assert not errs, f"lomeol.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomeol.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomeol.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _norm(v: str) -> str:
    """归一——**这里刻意什么都不做**（照抄 `loment_status_test` 那一版会红）。

    那条归一的前提是"输出里的反斜杠只会来自路径"。**这份报告的文本里有一个字面的
    `\\n`**（`写仓库内工件要用 newline='\\n'` 那一句，来自 Python 版 `FIX` 里的
    `'\\n'`），归一成 `/n` 会让**判据自己**造出一个假差异 —— 实测就是这么红的：
    两边真输出逐字节相同，红的是比较器。这里的路径全来自 `git`（本来就斜杠），
    不需要归一。**留着函数名是为了让"为什么不做"写在它头上，而不是让它消失。**
    """
    return v


def _py(args: list[str]) -> tuple[int, str, str]:
    """进程内跑 Python 版并捕获两路输出（避免换行翻译 —— 与 loment_status_test 同一理由）。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_eol.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    """在 WSL 里跑孪生, **cwd = 仓库根**（`git` 要在这儿跑, 与 Python 版同）。"""
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomeol_{name}.bin"
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
def test_lomeol_matches_python():
    """无参数那一趟: stdout / stderr / 退出码逐字节相同。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        rc, out, _err = _pair(elf, td, "noargs", [])
        assert rc in (0, 1), f"退出码应当是 0 (干净) 或 1 (有 CRLF), 实得 {rc}"
        assert out.startswith("loment_eol: "), out[:120]
    print("      无参数那一趟: stdout + stderr + 退出码一致")


@test
def test_lomeol_reports_crlf_like_python():
    """真有 CRLF 时也一致 —— 造一个 CRLF 的新 `.lomt`, 走报告的第二条路。

    **两边一致还不够**: 要是两边**都没报**也会"一致"。所以额外独立断言一次
    "探针出现在输出里", 否则这条判据是摆样子。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    try:
        PROBE.write_bytes(PROBE_BYTES)
        # 先跟 Python 版要一次结论, 确认探针真的被算作 CRLF（判据自己的前提）
        prc, pout, _perr = _py([])
        rel = PROBE.relative_to(ROOT).as_posix()
        assert prc == 1, f"Python 版应当报 CRLF (退 1), 实得 {prc}: {pout[:200]!r}"
        assert rel in pout, f"Python 版没点到探针 {rel}: {pout[:200]!r}"
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            _pair(elf, td, "crlf", [])
    finally:
        PROBE.unlink(missing_ok=True)
    print("      有 CRLF 时: 报告与退出码一致（且独立断言过探针真被报出来）")


@test
def test_lomeol_selfhost_compiles():
    """`lomeol.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
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
        binn = f"{_T}lomeol_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomeol.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomeol.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomeol.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_eol_test")


if __name__ == "__main__":
    sys.exit(main())
