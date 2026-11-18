#!/usr/bin/env python3
"""loment_oracle_test.py — 用**独立推导的语义 oracle** 查"两个实现一起错"（`docs/349`）。

## 它在查什么

仓里其它判据比的都是"两个实现是否一致"。它们**一起错**时全绿（`#513` §12：`i1` 宽度
#432、结构体字段偏移 #431）。这里放第三方：`semantics/loment_sem.lls` —— 用 Loment 写的
源级解释器，只照 `standard/LLS.md` 的条款实现，不 import 编译器任何代码。

**期望值不是任何实现算出来的**：`CASES` 那张表是照规范手推的（每条注释写清条款）。
于是这条判据同时钉两件事：编译器/VM 与规范一致，oracle 自己也与规范一致。

## 三方 + 第二读

* 正本 oracle：`semantics/loment_sem.lls`（Loment 写、`lomentc`+`lomelf` 编、WSL 跑）；
* 被验：参考编译器 → `lomelf` → 原生（WSL 跑），以及 LVM T0（有入口的那几份）；
* 第二读：`tools/loment_oracle.py`（Python 写，只作交叉 —— 两份读法一致比一份可靠）。

## 已知分歧

`KNOWN_DIVERGENCES` 里每一条都是"规范说 X、实现做 Y"。判据**钉住分歧仍然存在**：
哪天修好了，这条会红，逼你把表和 `docs/349` 一起改。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import loment_suite  # noqa: E402
import lomentc       # noqa: E402
import lomelf        # noqa: E402
import lvm           # noqa: E402
import loment_oracle as second_reading  # noqa: E402
import loment_elf_test as ELF           # noqa: E402  (复用 `_sh` / `_wsl_path`)

ROOT = Path(__file__).resolve().parent.parent
ORACLE_SRC = ROOT / "semantics" / "loment_sem.lls"
CORPUS = ROOT / "semantics" / "corpus"

#: `semantics/loment_sem.lls` 必须引到的 LLS 小节 —— 每一格都被语料真的走到。
CITED_SECTIONS = (
    "§10.2", "§10.3", "§10.6", "§10.7", "§11.3", "§11.4", "§11.5",
    "§12.1", "§12.3", "§12.5", "§12.6", "§12.10", "§14.1", "§14.2",
    "§18.1", "§18.3",
)

#: **手推的期望值**。注释里写清是哪条条款推出来的 —— 这张表是判据的地基，
#: 它不能由任何实现生成（否则就在测"两份实现是否互相抄"）。
CASES: dict[str, tuple[bytes, int]] = {
    # §10.2：u8 的 255 + 1 回绕成 0
    "01_wrap": (b"0\n", 0),
    # §10.2：向零截断；余数与被除数同号（-7/2 = -3，-7%2 = -1）
    "02_div": (b"-3\n-1\n", 0),
    # §12.5：`&&` 左边 false ⇒ 右边不求值；`||` 左边 true ⇒ 右边不求值 ⇒ 只打出 y
    "03_short_circuit": (b"y\n", 0),
    # §12.10 / §11.4：a[1] 改成 9 后 1 + 9 + 3 + 4
    "04_array_index": (b"17\n", 0),
    # §10.7 / §11.3：数组按值传是移动 —— 被调方改自己那份，调用方的 1 不动
    "05_array_move": (b"99 1\n", 0),
    # §10.7 / §18.1：`&a` 进 `[u32]` 形参，slice_len 是 3
    "06_slice_len": (b"3\n", 0),
    # §10.9 / §12.3：`&mut` + 解引用 ⇒ 调用方的 x 从 41 变 42
    "07_ref_mut": (b"42\n", 0),
    # §11.5 / §18.3：0+1+2+3+4 = 10；退出码 10 % 7 = 3
    "08_loop_exit": (b"10\n", 3),
    # §14.2 / §18.3：`_start` 自己发 exit(5)
    "09_start": (b"42\n", 5),
    # §10.2 / §12.4：i8 的 127+1 = -128；u16 的 0-1 = 65535；
    # u64 的最大值按**无符号**比 1 大（按有符号比会错）
    "10_wrap_signed": (b"-128\n65535\nbig\n", 0),
}

#: 规范与实现对不上的地方。**值为 (规范说什么, 实现做什么)**。
#:
#: `main-with-return-type`：LLS §14.1 "A `main` with a return type is refused"，
#: 而参考实现接受 `fn main() -> u32`，入口桩还把它**静默丢掉**（退出码恒为 0）。
#: 这条由 oracle 第一天跑出来的：oracle 按规范拒，实现收。
KNOWN_DIVERGENCES: dict[str, str] = {
    "main-with-return-type": "§14.1 说拒，参考实现接受且静默丢弃返回值",
}

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 编 oracle

_ORACLE: Path | None = None
_ORACLE_TD: tempfile.TemporaryDirectory | None = None


def _oracle() -> Path:
    """`semantics/loment_sem.lls` → ELF。整进程只编一次（每次跑判据都要用）。"""
    global _ORACLE, _ORACLE_TD
    if _ORACLE is not None:
        return _ORACLE
    _ORACLE_TD = tempfile.TemporaryDirectory()
    td = Path(_ORACLE_TD.name)
    mod = lomentc.load(ORACLE_SRC)
    deps = lomentc.resolve_deps(mod, ROOT, ORACLE_SRC.parent, entry=ORACLE_SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"oracle 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _info = lomelf.compile_ll(ir)
    out = td / "oracle.elf"
    out.write_bytes(blob)
    _ORACLE = out
    return out


def _run_elf(elf: Path, arg: Path, td: Path, timeout: int = 60) -> tuple[int, bytes]:
    """在 WSL 里跑 ELF（带一个文件参数），返回 (退出码, stdout)。

    与 `loment_elf_test._run_bin` 同一条纪律：stdout 落文件、退出码单独 `echo -n $?`。
    """
    binn = f"/tmp/loment-oracle-{os.getpid()}.bin"
    outp = td / "run.out"
    outp.unlink(missing_ok=True)
    script = (f"cp {ELF._wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"{binn} {ELF._wsl_path(arg)} > {ELF._wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = ELF._sh(script, timeout + 60)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


def _native(src: Path, td: Path) -> tuple[int, bytes]:
    """参考实现 → IR → lomelf → 原生，WSL 里跑。"""
    ll = ELF._ref_ir(src, td)
    blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
    nat = td / f"{src.stem}.native"
    nat.write_bytes(blob)
    return _run_elf(nat, src, td)


# ---------------------------------------------------------------- 判据

@test
def test_oracle_is_independent():
    """正本 oracle 只读规范：不 import 编译器、不出现它们的模块名。"""
    text = ORACLE_SRC.read_text(encoding="utf-8")
    for bad in ("import lomentc", "import lomelf", "import lomfmt",
                "potato_from", "import lvm"):
        assert bad not in text, f"oracle 里出现了 {bad!r} —— 独立性破了"
    for sec in CITED_SECTIONS:
        assert sec in text, f"oracle 没引 {sec} —— 覆盖的那一格说不清出处"
    print(f"      {len(CITED_SECTIONS)} 个小节都有引用；无编译器依赖")


@test
def test_loment_oracle_matches_the_spec_table():
    """**正本**（Loment 写的 oracle）跑语料，与手推表逐字节比。"""
    if not ELF._wsl():
        print("      SKIP: 无 WSL")
        return
    oracle = _oracle()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        bad = []
        for stem, (want_out, want_rc) in sorted(CASES.items()):
            src = CORPUS / f"{stem}.lomt"
            rc, out = _run_elf(oracle, src, td)
            if (rc, out) != (want_rc, want_out):
                bad.append((stem, want_rc, want_out, rc, out))
        assert not bad, ("oracle 与规范表不一致（前 3）：\n" + "\n".join(
            f"  {n}: 表=({a},{b!r}) oracle=({c},{d!r})" for n, a, b, c, d in bad[:3]))
    print(f"      {len(CASES)} 份语料：Loment oracle 与规范表逐字节一致")


@test
def test_native_agrees_with_the_spec_table():
    """**被验**：参考编译器出的原生产物，与同一张规范表逐字节比。

    这一条才是"编译器对不对"的问法；上面那条问的是 oracle 自己。
    """
    if not ELF._wsl():
        print("      SKIP: 无 WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        bad = []
        for stem, (want_out, want_rc) in sorted(CASES.items()):
            src = CORPUS / f"{stem}.lomt"
            rc, out = _native(src, td)
            if (rc, out) != (want_rc, want_out):
                bad.append((stem, want_rc, want_out, rc, out))
        assert not bad, ("原生与规范表不一致（前 3）：\n" + "\n".join(
            f"  {n}: 表=({a},{b!r}) 原生=({c},{d!r})" for n, a, b, c, d in bad[:3]))
    print(f"      {len(CASES)} 份语料：原生产物与规范表逐字节一致")


@test
def test_vm_agrees_where_it_has_an_entry():
    """LVM 与规范表一致 —— 只对有 VM 入口（`_start`）的那几份。

    `main` 形状的入口 LVM 不认（`docs/243` §3.5 的四形状），所以那几份 skip，不是漏。
    """
    ran = 0
    for stem, (want_out, want_rc) in sorted(CASES.items()):
        src = CORPUS / f"{stem}.lomt"
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        buf = io.BytesIO()
        try:
            rc = lvm.run_ir(ir, buf)
        except lvm.LvmError as e:
            assert "没有 `_start`" in str(e), f"[{stem}] VM 报了个不是入口的错：{e}"
            continue
        assert (rc, buf.getvalue()) == (want_rc, want_out), (
            f"[{stem}] VM=({rc},{buf.getvalue()!r}) 表=({want_rc},{want_out!r})")
        ran += 1
    print(f"      {ran} 份有 VM 入口的语料：LVM 与规范表一致")


@test
def test_python_reading_agrees():
    """第二读（`tools/loment_oracle.py`）与规范表一致 —— 两份独立读法互证。"""
    bad = []
    for stem, (want_out, want_rc) in sorted(CASES.items()):
        src = CORPUS / f"{stem}.lomt"
        got = second_reading.run_source(src.read_text(encoding="utf-8"))
        assert not got.error, f"[{stem}] 第二读拒了：{got.error}"
        if (got.rc, got.stdout) != (want_rc, want_out):
            bad.append((stem, want_rc, want_out, got.rc, got.stdout))
    assert not bad, ("第二读与规范表不一致：\n" + "\n".join(
        f"  {n}: 表=({a},{b!r}) py=({c},{d!r})" for n, a, b, c, d in bad))
    print(f"      {len(CASES)} 份语料：第二读与规范表逐字节一致")


@test
def test_the_oracle_is_not_vacuous():
    """**证伪面**：换一份源，oracle 的输出必须跟着变（否则它可能在读常量表）。"""
    if not ELF._wsl():
        print("      SKIP: 无 WSL")
        return
    oracle = _oracle()
    src = ("module probe\nchoose runtime\n\nfn main() {\n"
           "    let a: u8 = 254;\n    let b: u8 = a + 3;\n"
           "    print_num(b as i64);\n    print(\"\\n\");\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        f = td / "probe.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        rc, out = _run_elf(oracle, f, td)
    # 254 + 3 = 257；u8 回绕 ⇒ 1（§10.2）。若 oracle 只是查表，它给不出 1。
    assert (rc, out) == (0, b"1\n"), f"证伪探针不对：({rc}, {out!r})"
    print("      换一份源（254+3 ⇒ 1）：oracle 真的在算")


@test
def test_known_divergences_are_still_there():
    """`KNOWN_DIVERGENCES` 里的分歧**仍然存在** —— 修好了就红，逼你更新表与文档。"""
    src = "module m\n\nfn main() -> u32 {\n    return 7;\n}\n"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        f = td / "main_ret.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(f)
        deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
        errs = lomentc.check(mod, deps=deps)
    assert not errs, (
        "`main` 带返回类型现在被拒了 —— 那条分歧没了。"
        "把它从 KNOWN_DIVERGENCES 与 docs/349 里删掉（这是好消息）。")
    got = second_reading.run_source(src)
    assert got.error, "第二读居然收了 `main() -> u32` —— 它没有照 §14.1 拒"
    print("      main-with-return-type：规范拒、实现收 —— 分歧仍在（已记档）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_oracle_test")


if __name__ == "__main__":
    sys.exit(main())
