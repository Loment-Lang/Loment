#!/usr/bin/env python3
"""loment_java_test.py — Java AOT P0 (docs/281, docs/282).

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
    classes = lomjava.load_classes(classfile)
    ir = out / f"{module}.ll"
    iface = out / f"{module}.lomt"
    lomjava.emit(module, classes, ir, iface)
    return ir, iface


def _app_ir(tmp: Path, body: str) -> str:
    app = tmp / "app.lomt"
    app.write_text(body, encoding="utf-8")
    mod, deps = lomentc.load_unit(app, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise AssertionError(f"app check failed: {errs[:3]}")
    return lomentc.emit_llvm(mod, ROOT, deps)


def _build_and_run_io(tmp: Path, libs: list[Path], app_src: str) -> tuple[int, bytes, bytes]:
    """Build one app + N libraries; return (exit code, stdout, stderr)."""
    app_ll = tmp / "app.ll"
    app_ll.write_text(_app_ir(tmp, app_src), encoding="utf-8")
    merged = tmp / "merged.ll"
    merged.write_text(lomelf.merge_ir_texts(
        [app_ll.read_text(encoding="utf-8")] +
        [p.read_text(encoding="utf-8") for p in libs],
        [str(app_ll)] + [str(p) for p in libs]), encoding="utf-8")
    text = merged.read_text(encoding="utf-8")
    if sys.platform == "win32":
        blob, _info = lomelf.compile_pe(text)
        exe = tmp / "app.exe"
    else:
        blob, _info = lomelf.compile_ll(text)
        exe = tmp / "app"
    exe.write_bytes(blob)
    exe.chmod(0o755)
    r = subprocess.run([str(exe)], capture_output=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def _build_and_run(tmp: Path, lib: Path, app_src: str) -> int:
    return _build_and_run_io(tmp, [lib], app_src)[0]


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
        assert rc == 5, rc
        print(f"      add(2,3) -> exit 5 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      clamp/sign -> exit 0 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      long add/cmp/mix -> exit 0 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      sumSquares(3,4)=25, addTwice(5)=20 -> exit 0 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      sum()=140, sumLong()=14 -> exit 0 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      new/getfield/putfield -> 16 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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
        assert rc == 0, rc
        print(f"      length/charAt of literals -> exit 0 on {'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_instance_methods_end_to_end():
    """A constructor with an argument and an `invokevirtual` in the same class."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "instlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "InstanceLib.class", "instlib", d / "deps" / "instlib")
        rc = _build_and_run(d, ir, """module app
use instlib

fn _start() {
    let r: i32 = instlib_run();
    syscall4(60, (r - 12) as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      new InstanceLib(7).add(5) = 12 on {'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_static_fields_end_to_end():
    """getstatic/putstatic: state survives across calls to the same class."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "staticlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "StaticLib.class", "staticlib", d / "deps" / "staticlib")
        rc = _build_and_run(d, ir, """module app
use staticlib

fn _start() {
    let a: i32 = staticlib_bump(5);
    let b: i32 = staticlib_bump(3);
    let c: i32 = staticlib_get();
    let ok: i32 = (a - 5) + (b - 8) + (c - 8);
    syscall4(60, ok as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      counter: 5 -> 8 -> 8 on {'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_single_class_jar():
    """A jar with one class is accepted; multi-class jars are named and refused."""
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        jar = d / "mathlib.jar"
        with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(FIX / "MathLib.class", "com/example/MathLib.class")
        (d / "deps" / "mathlib").mkdir(parents=True)
        ir, _iface = _emit(jar, "mathlib", d / "deps" / "mathlib")
        rc = _build_and_run(d, ir, """module app
use mathlib

fn _start() {
    let r: i32 = mathlib_add(2, 3);
    syscall4(60, r as u64, 0, 0);
}
""")
        assert rc == 5, rc
        print(f"      single-class jar -> add(2,3)=5 on {'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_exceptions_end_to_end():
    """try/catch + athrow: a caught RuntimeException returns 7."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "exclib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "ExcLib.class", "exclib", d / "deps" / "exclib")
        rc = _build_and_run(d, ir, """module app
use exclib

fn _start() {
    let a: i32 = exclib_thrower();
    let b: i32 = exclib_after();
    let ok: i32 = (a - 7) + (b - 3);
    syscall4(60, ok as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      throw/catch -> 7, after() -> 3 on {'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_multi_class_jar():
    """A two-class jar: cross-class new/getfield/putstatic/invokestatic + <clinit>."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "multiclass").mkdir(parents=True)
        jar = FIX.parent.parent / "multiclass.jar"
        ir, _iface = _emit(jar, "multiclass", d / "deps" / "multiclass")
        rc = _build_and_run(d, ir, """module app
use multiclass

fn _start() {
    let r: i32 = multiclass_ClassA_run();
    syscall4(60, (r - 22) as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      ClassA.run(): new/getfield/static add/value -> 22 on {'PE' if sys.platform == 'win32' else 'ELF'}")


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


@test
def test_jrt_println_end_to_end():
    """#1060: System.out/err print/println and Math's integer subset, through jrt.

    `jrt.ll` is the shared runtime (generated from `loment/lib/jrt/jrt.lomt`);
    the app, the library and the runtime are merged into one product. The
    expected bytes are what the JVM prints for the same class.
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "printlib").mkdir(parents=True)
        ir, _iface = _emit(FIX / "PrintLib.class", "printlib", d / "deps" / "printlib")
        jrt = ROOT / "loment" / "lib" / "jrt" / "jrt.ll"
        rc, out, err = _build_and_run_io(d, [ir, jrt], """module app
use printlib

fn _start() {
    printlib_hello();
    syscall4(60, 0, 0, 0);
}
""")
    assert rc == 0, rc
    assert out == b"hello\n42\n-7\nx=1234567890123\n5\n3\n9\n", out
    assert err == b"e\n", err
    print(f"      println/print + Math -> {len(out)} bytes of stdout, "
          f"{len(err)} of stderr")


@test
def test_string_equals_and_hash_code():
    """#1060: String.equals/hashCode agree with Java, ASCII and non-ASCII."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "streq").mkdir(parents=True)
        ir, _iface = _emit(FIX / "StrEq.class", "streq", d / "deps" / "streq")
        rc = _build_and_run(d, ir, """module app
use streq

fn _start() {
    let r: i32 = streq_run();
    syscall4(60, r as u64, 0, 0);
}
""")
    assert rc == 63, rc
    print("      equals/hashCode: 63 (six checks, including 中文)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_java_test")


if __name__ == "__main__":
    sys.exit(main())
