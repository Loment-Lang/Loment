#!/usr/bin/env python3
# loment_capasserts_test.py — Loment 版 A1–A4 断言表生成器与 Python 版逐字节相同
# (docs/189 §3 的 S1 第五格)
#
# 判据: 同一份仓库、同一个 cwd (= 仓库根), `loment/tools/lomcapasserts.lomt` (链成 ELF 后跑)
# 与 `tools/potato_assert.py`:
#   1. `--print`: stdout / stderr / 退出码逐字节相同;
#   2. `--emit-rust PATH`: 输出行相同, **写出的文件字节也相同**, 且与 `--print` 的内容一致;
#   3. `--check` 的三条支路 (产物在且一致 / 在但不一致 / 不在) 逐字节相同;
#   4. `lomcapasserts.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# **两处归一化, 都只说了一件事: 平台**（不是在替工具遮掩差异）：
#   * `--check` 的 SKIP / ERR 两条消息里带一个路径。`Path.relative_to` 给的是**本机**分隔符
#     （Windows 上 `\`、WSL 里 `/`）—— 同一份 Python 换个平台就换一个分隔符, 所以那不是
#     工具行为。孪生只跑 Linux（ELF）, 于是判据在这一处把两边折成 `/`; 归一的**安全性**
#     另有一条断言钉住: `--print` 的内容里**一个反斜杠都没有**, 所以那次替换只可能碰到路径。
#   * 产物字节**不归一**: Python 那侧曾经随宿主换行翻译（Windows CRLF / Linux LF）——
#     那是 S3「生成是确定的」被破坏的实例, 已在 `tools/potato_assert.py` 里补上
#     `newline="\n"`（另两个生成器一直都有）。判据把这件事钉成"字节相同"。
#
# 没比的: argparse 的用法文本（英文用法块逐字复刻没有意义, 孪生也不搬）。
#
# 运行: python tools/loment_capasserts_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import potato_assert  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "loment" / "build"
SRC = ROOT / "loment" / "tools" / "lomcapasserts.lomt"
#: `--check` 看的那个路径（工具里也是写死的这一个）。
DEST = BUILD / "cap_asserts.rs"
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
    assert not errs, f"lomcapasserts.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomcapasserts.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomcapasserts.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}cap_{name}.bin"
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


def _py(args: list[str]) -> tuple[int, str, str]:
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = potato_assert.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _sep(v: str) -> str:
    """只把**路径分隔符**折成 `/`。安全性由 `test_..._print_matches_python` 里的
    "内容里没有反斜杠"那条断言保证 —— 归一碰不到工具自己的输出。"""
    return v.replace("\\", "/")


def _derive_rows_independently(want: str) -> int:
    """**独立推一遍**：不复用 `potato_assert.rows()`, 照 docs/147 §4 的 A1–A4 重写一遍,
    断言每一行的字面量都在输出里。只比"两边相同"的话, 两边一起错也会一致。"""
    ai_spaces = {"ai", "model", "llm", "net_llm", "infer"}
    n = 0
    for p in sorted(BUILD.glob("*.potato.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        guards = int(doc.get("guards", 0))
        for c in doc.get("capabilities", []):
            d = c["domain"]
            a1 = bool(d["space"]) and d["hi"] >= d["lo"]
            a2 = guards >= 0
            a3 = d["space"] not in ai_spaces
            a4 = bool(c["revocable"])
            tf = lambda v: "true" if v else "false"  # noqa: E731
            line = (f'    CapAssert {{ unit: "{doc["unit"]}", name: "{c["name"]}", '
                    f'space: "{d["space"]}", lo: {d["lo"]}, hi: {d["hi"]}, '
                    f'revocable: {tf(a4)}, guards: {guards}, a1: {tf(a1)}, '
                    f'a2: {tf(a2)}, a3: {tf(a3)}, a4: {tf(a4)} }},')
            assert line in want, f"这一行没出现在生成结果里:\n{line}"
            n += 1
    assert n > 0, "语料里一条能力都没有? (那这条判据什么也没比)"
    assert f"// {n} 条" in want, f"`// N 条` 与我独立数出来的对不上（{n}）"
    assert want.count("    CapAssert {") == n, "表里的行数与独立清点不一致"
    return n


@test
def test_capasserts_print_matches_python():
    """`--print`: stdout / stderr / 退出码逐字节相同；内容另经独立推导。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        wr, wo, we = _py(["--print"])
        gr, go, ge = _run(elf, td, "print", ["--print"])
    assert (wr, wo, we) == (gr, go, ge), (
        f"[print] 不一致: rc py={wr} loment={gr}\n  py {wo[-160:]!r}/{we[-80:]!r}\n"
        f"  sh {go[-160:]!r}/{ge[-80:]!r}")
    assert wr == 0 and wo.endswith("}\n"), (wr, wo[-40:])
    # 归一化的前提：内容里一个反斜杠都没有（`--check` 那两条消息的折叠只可能碰路径）
    assert "\\" not in wo, "生成内容里出现了反斜杠 —— SKIP/ERR 那处的归一化不再安全"
    n = _derive_rows_independently(wo)
    print(f"      `--print`: 输出一致；{n} 行另经独立推导 + 清点")


@test
def test_capasserts_emit_writes_same_bytes():
    """`--emit-rust PATH`: 输出行相同, **写出的字节相同**, 且与 `--print` 的内容一致。

    "字节相同"这一条是有来历的: Python 那侧原先没给 `write_text` 传 `newline="\\n"`,
    于是 Windows 上写出 CRLF、Linux 上 LF —— 同一个形式对象集的产物**随宿主变**,
    与 S3「生成是确定的」冲突。补上之后两边才真的是同一串字节（`--check` 看不见这件事:
    它用 `read_text()` 读回来, 通用换行会把 CRLF 折回 LF, 同机自洽）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        # 两边都往**同一个仓库相对路径**写（输出行里会带这个路径, 所以必须同一个）
        rel = "loment/build/capasserts_emit_tmp.rs"
        dest = ROOT / rel
        try:
            rc, po, pe = _py(["--emit-rust", rel])
            assert rc == 0, f"Python 版 --emit-rust 失败 rc={rc}: {pe[:160]!r}"
            py_bytes = dest.read_bytes()
            gr, go, ge = _run(elf, td, "emit", ["--emit-rust", rel])
            sh_bytes = dest.read_bytes()
            assert (rc, po, pe) == (gr, go, ge), (
                f"输出行不一致:\n  py {po!r}/{pe!r}\n  sh {go!r}/{ge!r}")
            crlf_py = py_bytes.count(b"\r\n")
            crlf_sh = sh_bytes.count(b"\r\n")
            assert py_bytes == sh_bytes, (
                f"落盘不同: py {len(py_bytes)}B vs loment {len(sh_bytes)}B"
                f"（CRLF 行数 {crlf_py} vs {crlf_sh}）")
        finally:
            dest.unlink(missing_ok=True)
        # 与 `--print` 的内容一致（不是"两边一起错"）
        cr, cw, _ = _py(["--print"])
        assert cr == 0 and py_bytes == cw.encode("utf-8"), "落盘内容与 --print 对不上"
    print(f"      `--emit-rust`: 输出行 + 落盘字节一致（{len(py_bytes)}B, 与 --print 同）")


@test
def test_capasserts_check_matches_python_in_all_three_branches():
    """`--check` 的三条支路都要比: 产物在且一致 / 在但不一致 / 不在。

    后两条的消息里带**本机**路径分隔符（`Path.relative_to` 的产物, 不是工具行为）——
    只在这一处折成 `/`, 且上面那条断言已经保证折叠碰不到别的东西。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    was_there = DEST.exists()
    backup = DEST.read_bytes() if was_there else None
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            # (a) 不在 -> SKIP
            DEST.unlink(missing_ok=True)
            wr, wo, we = _py(["--check"])
            gr, go, ge = _run(elf, td, "skip", ["--check"])
            assert (_sep(wo), we, wr) == (go, ge, gr), (
                f"[SKIP] 不一致: rc py={wr} loment={gr}\n  py {wo!r}\n  sh {go!r}")
            assert wo.startswith("[SKIP] ") and wr == 0, (wo, wr)
            # (b) 在且一致 -> OK（产物由参考实现生成, 两边读同一份）
            DEST.parent.mkdir(parents=True, exist_ok=True)
            rc_e, _, pe_e = _py(["--emit-rust", "loment/build/cap_asserts.rs"])
            assert rc_e == 0, pe_e[:160]
            wr, wo, we = _py(["--check"])
            gr, go, ge = _run(elf, td, "ok", ["--check"])
            assert (wr, wo, we) == (gr, go, ge), (
                f"[OK] 不一致: rc py={wr} loment={gr}\n  py {wo!r}\n  sh {go!r}")
            assert wo.startswith("[OK] cap_asserts.rs 一致 ("), wo
            # (c) 在但不一致 -> ERR（stderr + 退 1）
            with DEST.open("w", encoding="utf-8", newline="\n") as f:
                f.write("// 篡改\n")
            wr, wo, we = _py(["--check"])
            gr, go, ge = _run(elf, td, "err", ["--check"])
            assert (wr, _sep(wo), _sep(we)) == (gr, go, ge), (
                f"[ERR] 不一致: rc py={wr} loment={gr}\n  py {wo!r}/{we!r}\n  sh {go!r}/{ge!r}")
            assert wr == 1 and we.startswith("[ERR] ") and "不一致" in we, (wr, we)
    finally:
        if backup is None:
            DEST.unlink(missing_ok=True)
        else:
            DEST.write_bytes(backup)
    print("      `--check`: 三条支路（SKIP / OK / ERR）逐字节相同")


@test
def test_capasserts_selfhost_compiles():
    """`lomcapasserts.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
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
        binn = f"{_T}cap_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomcapasserts.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomcapasserts.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomcapasserts.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_capasserts_test")


if __name__ == "__main__":
    sys.exit(main())
