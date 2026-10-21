#!/usr/bin/env python3
"""loment_java_test.py — Java AOT P0 (docs/274, docs/275).

The first vertical slice:

    .class (real javac output) -> lomjava -> portable Loment IR + interface
    app.lomt + interface -> lomentc -> app IR
    app IR + library IR -> merge (declare -> internal call) -> lomelf -> PE/ELF

The executed criterion is Windows-only because the PE product is what runs
natively here; on Linux the same IR is still emitted, merged and compiled, and
the execution half prints a named SKIP.

Fixtures are committed, real `javac` output (see tools/fixtures/java/PROVENANCE.md).
No `java`, `javac` or `jar` is invoked by this test.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lomentc  # noqa: E402
import lomjava  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tools" / "fixtures" / "java" / "com" / "example"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _emit(classfile: Path, module: str, out: Path) -> tuple[Path, Path]:
    cf = lomjava.parse_class(classfile.read_bytes())
    ir = out / f"{module}.ll"
    iface = out / f"{module}.lomt"
    lomjava.emit(module, cf, ir, iface)
    return ir, iface


def _app_ir(tmp: Path, body: str) -> str:
    app = tmp / "app.lomt"
    app.write_text(body, encoding="utf-8")
    mod, deps = lomentc.load_unit(app, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise AssertionError(f"app check failed: {errs[:3]}")
    return lomentc.emit_llvm(mod, ROOT, deps)


def _build_and_run(tmp: Path, lib: Path, app_src: str) -> int:
    """Build one app + one library; return the product's exit code.

    On non-Windows this compiles the merged IR to ELF only (the build path is
    still exercised) and returns 0; the caller prints the SKIP.
    """
    app_ll = tmp / "app.ll"
    app_ll.write_text(_app_ir(tmp, app_src), encoding="utf-8")
    merged = tmp / "merged.ll"
    merged.write_text(lomelf.merge_ir_texts(
        [app_ll.read_text(encoding="utf-8"), lib.read_text(encoding="utf-8")],
        [str(app_ll), str(lib)]), encoding="utf-8")
    text = merged.read_text(encoding="utf-8")
    if sys.platform != "win32":
        lomelf.compile_ll(text)
        return 0
    blob, _info = lomelf.compile_pe(text)
    exe = tmp / "app.exe"
    exe.write_bytes(blob)
    r = subprocess.run([str(exe)], capture_output=True, timeout=30)
    return r.returncode


@test
def test_parse_real_class():
    """The parser reads real javac output and exposes the two static methods."""
    cf = lomjava.parse_class((FIX / "MathLib.class").read_bytes())
    names = {m.name for m in cf.methods}
    assert {"add", "mix"} <= names, names
    add = next(m for m in cf.methods if m.name == "add")
    assert add.desc == "(II)I" and add.code is not None
    print(f"      parsed {cf.name}: {sorted(names)}")


@test
def test_ir_and_interface_are_deterministic():
    """Same class -> byte-identical IR and interface on two runs."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a").mkdir(); (d / "b").mkdir()
        _emit(FIX / "MathLib.class", "mathlib", d / "a")
        _emit(FIX / "MathLib.class", "mathlib", d / "b")
        for name in ("mathlib.ll", "mathlib.lomt"):
            assert (d / "a" / name).read_bytes() == (d / "b" / name).read_bytes(), name
        iface = (d / "a" / "mathlib.lomt").read_text(encoding="utf-8")
        assert "module mathlib" in iface
        assert "pub fn mathlib_add" in iface
        assert "extern fn __jl_mathlib_add_" in iface
        print("      IR + interface byte-identical across runs")


@test
def test_static_int_end_to_end():
    """add(2,3) is called through the generated interface and returns 5."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "mathlib").mkdir(parents=True)
        ir, iface = _emit(FIX / "MathLib.class", "mathlib", d / "deps" / "mathlib")
        assert ir.exists() and iface.exists()
        rc = _build_and_run(d, ir, """module app
use mathlib

fn _start() {
    let r: i32 = mathlib_add(2, 3);
    syscall4(60, r as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 5, rc
            print("      add(2,3) -> exit 5 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_branches_end_to_end():
    """Branches and early returns: clamp/sign through the same path."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "branchlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "BranchLib.class", "branchlib", d / "deps" / "branchlib")
        rc = _build_and_run(d, ir, """module app
use branchlib

fn _start() {
    let a: i32 = branchlib_clamp(0 - 5);
    let b: i32 = branchlib_sign(0 - 5);
    let c: i32 = branchlib_sign(0);
    let d: i32 = branchlib_sign(5);
    let ok: i32 = a + (b + 1) + c + (d - 1);
    syscall4(60, ok as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      clamp/sign -> exit 0 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_long_end_to_end():
    """long params/returns, `lcmp`, `ldc2_w`, `i2l` and the 64-bit stack slots."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "longlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "LongLib.class", "longlib", d / "deps" / "longlib")
        rc = _build_and_run(d, ir, """module app
use longlib

fn _start() {
    let a: i64 = longlib_add(3, 4);
    let c1: i32 = longlib_cmp(3, 4) + 1;
    let c2: i32 = longlib_cmp(4, 3) + 1;
    let c3: i32 = longlib_cmp(5, 5) + 1;
    let m: i64 = longlib_mix(5, 2);
    let ok: i64 = (a - 7) + (c1 as i64) + ((c2 - 2) as i64) + ((c3 - 1) as i64) + (m - 15);
    syscall4(60, ok as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      long add/cmp/mix -> exit 0 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_static_calls_end_to_end():
    """`invokestatic` to another method in the same class, including long args."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "multilb").mkdir(parents=True)
        ir, _iface = _emit(FIX / "MultiLib.class", "multilb", d / "deps" / "multilb")
        rc = _build_and_run(d, ir, """module app
use multilb

fn _start() {
    let a: i32 = multilb_sumSquares(3, 4);
    let b: i64 = multilb_addTwice(5);
    let ok: i64 = ((a - 25) as i64) + (b - 20);
    syscall4(60, ok as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      sumSquares(3,4)=25, addTwice(5)=20 -> exit 0 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_arrays_end_to_end():
    """int[] and long[]: newarray, iastore/iaload, lastore/laload, loop code."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "arraylib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "ArrayLib.class", "arraylib", d / "deps" / "arraylib")
        rc = _build_and_run(d, ir, """module app
use arraylib

fn _start() {
    let a: i32 = arraylib_sum();
    let b: i64 = arraylib_sumLong();
    let ok: i64 = ((a - 140) as i64) + (b - 14);
    syscall4(60, ok as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      sum()=140, sumLong()=14 -> exit 0 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_objects_end_to_end():
    """new/dup/<init>/getfield/putfield with a default constructor."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "objlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "ObjLib.class", "objlib", d / "deps" / "objlib")
        rc = _build_and_run(d, ir, """module app
use objlib

fn _start() {
    let r: i32 = objlib_makeAndRead();
    syscall4(60, (r - 16) as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      new/getfield/putfield -> 16 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_strings_end_to_end():
    """String literals (modified UTF-8 -> UTF-16LE), length() and charAt()."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "stringlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "StringLib.class", "stringlib", d / "deps" / "stringlib")
        rc = _build_and_run(d, ir, """module app
use stringlib

fn _start() {
    let a: i32 = stringlib_len();
    let b: i32 = stringlib_at();
    let c: i32 = stringlib_code();
    let ok: i32 = (a - 5) + (b - 101) + (c - 65);
    syscall4(60, ok as u64, 0, 0);
}
""")
        if sys.platform == "win32":
            assert rc == 0, rc
            print("      length/charAt of literals -> exit 0 on PE")
        else:
            print("SKIP: PE execution is Windows-only; IR emitted and linked to ELF")


@test
def test_merge_removes_satisfied_declaration():
    """The merged IR has no extern declaration for a symbol defined in a second input."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "mathlib").mkdir(parents=True)
        ir, _ = _emit(FIX / "MathLib.class", "mathlib", d / "deps" / "mathlib")
        app = d / "app.ll"
        app.write_text(_app_ir(d, """module app
use mathlib

fn _start() {
    let r: i32 = mathlib_add(2, 3);
    syscall4(60, r as u64, 0, 0);
}
"""), encoding="utf-8")
        merged = d / "merged.ll"
        merged.write_text(lomelf.merge_ir_texts(
            [app.read_text(encoding="utf-8"), ir.read_text(encoding="utf-8")],
            [str(app), str(ir)]), encoding="utf-8")
        text = merged.read_text(encoding="utf-8")
        assert "declare i32 @__jl_mathlib_add_" not in text
        assert "define internal i32 @__jl_mathlib_add_" in text
        print("      declare -> internal definition")


@test
def test_merge_rejects_duplicate_definition():
    """Two inputs defining the same symbol are an error, not a silent overwrite."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        a = d / "a.ll"; b = d / "b.ll"
        for p in (a, b):
            p.write_text("define internal i32 @f() {\nentry:\n  ret i32 1\n}\n",
                         encoding="utf-8")
        try:
            lomelf.merge_ir_texts([a.read_text(encoding="utf-8"),
                                   b.read_text(encoding="utf-8")], [str(a), str(b)])
        except lomelf.Unsupported as e:
            assert "重复定义" in str(e), e
            print(f"      duplicate rejected: {e}")
        else:
            raise AssertionError("duplicate definition was accepted")


@test
def test_bad_class_is_rejected():
    """A corrupt class file is a named error, not a traceback."""
    for data in (b"\x00" * 8, b"not a class file"):
        try:
            lomjava.parse_class(data)
        except lomjava.JavaError as e:
            assert "magic" in str(e) or "truncated" in str(e), e
        else:
            raise AssertionError("corrupt class accepted")


def main() -> int:
    return loment_suite.run(TESTS, "loment_java_test")


if __name__ == "__main__":
    sys.exit(main())
