#!/usr/bin/env python3
"""loment_java_test.py — Java AOT P0 (docs/282, docs/283).

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
def test_inheritance_and_virtual_dispatch():
    """#1059: a base-typed receiver dispatches to the subclass's override.

    `ShapeLib.total(Shape, Shape)` calls `area()` on the base type; the two
    arguments are a `Square` and a `Rect`. Closed-world dispatch compares the
    object's class id and calls the matching implementation directly.
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "shapelib").mkdir(parents=True)
        jar = FIX.parent.parent / "shapelib.jar"
        ir, _iface = _emit(jar, "shapelib", d / "deps" / "shapelib")
        rc = _build_and_run(d, ir, """module app
use shapelib

fn _start() {
    let a: i32 = shapelib_ShapeLib_run();
    let b: i32 = shapelib_ShapeLib_plain();
    syscall4(60, ((a - 17) + (b - 11)) as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      Square/Rect/Shape dispatch -> 17 and 11 on "
              f"{'PE' if sys.platform == 'win32' else 'ELF'}")


@test
def test_switch_tables_and_sparse():
    """#1059: `tableswitch` and `lookupswitch` lower to compare chains."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "deps" / "switchlib").mkdir(parents=True)
        jar = FIX.parent.parent / "switchlib.jar"
        ir, _iface = _emit(jar, "switchlib", d / "deps" / "switchlib")
        rc = _build_and_run(d, ir, """module app
use switchlib

fn _start() {
    let a: i32 = switchlib_pick(3);
    let b: i32 = switchlib_sparse(1000);
    let c: i32 = switchlib_pick(9);
    syscall4(60, ((a - 30) + (b - 7) + (c + 1)) as u64, 0, 0);
}
""")
        assert rc == 0, rc
        print(f"      tableswitch/lookupswitch -> 30, 7, -1 on "
              f"{'PE' if sys.platform == 'win32' else 'ELF'}")


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


def _mk_class(name: str, code: bytes, *, max_stack: int = 8, max_locals: int = 8,
              exc: list | None = None, method: str = "m", desc: str = "()V",
              access: int = 0x0008, smt: bytes | None = None,
              super_name: str = "java/lang/Object") -> bytes:
    """手工造一个最小 class 文件。

    javac 不会产出验证器要拒绝的那几种形状（类型混淆、越界分支、坏异常表），
    所以这些反例只能自己拼字节 —— 它们是"恶意输入"的夹具，不是"真实现的产物"。
    """
    import struct
    cp = [
        b"\x01" + struct.pack(">H", len(name)) + name.encode(),
        b"\x07\x00\x01",
        b"\x01" + struct.pack(">H", len(super_name)) + super_name.encode(),
        b"\x07\x00\x03",
        b"\x01" + struct.pack(">H", len(method)) + method.encode(),
        b"\x01" + struct.pack(">H", len(desc)) + desc.encode(),
        b"\x01\x00\x04Code",
        b"\x01" + struct.pack(">H", 13) + b"StackMapTable",
    ]
    body = struct.pack(">HHI", max_stack, max_locals, len(code)) + code
    body += struct.pack(">H", len(exc or []))
    for start, end, target, ctype in (exc or []):
        body += struct.pack(">HHHH", start, end, target, ctype)
    # `StackMapTable` 是 **Code 属性内部**的属性，不是方法属性。
    body += struct.pack(">H", 0 if smt is None else 1)
    if smt is not None:
        body += struct.pack(">HI", 8, len(smt)) + smt
    meth = (struct.pack(">HHHH", access, 5, 6, 1) +
            struct.pack(">HI", 7, len(body)) + body)
    return (struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(cp) + 1) + b"".join(cp) +
            struct.pack(">HHHHHH", 0x0021, 2, 4, 0, 0, 1) + meth + b"\x00\x00")


def _verify_rejects(data: bytes, prefix: str, *needles: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        try:
            lomjava.emit("bad", [lomjava.parse_class(data)], d / "bad.ll", d / "bad.lomt")
        except lomjava.JavaError as e:
            msg = str(e)
            assert msg.startswith(prefix), msg
            for n in needles:
                assert n in msg, msg
            return msg
        else:
            raise AssertionError("the verifier accepted a class it must reject")


@test
def test_verifier_rejects_int_used_as_a_reference():
    """#1058：降级之后一个槽就是 i64 —— 不判类型，手写字节码就能让它把整数当指针。

    `iconst_1; athrow` 里抛的"对象"是个整数。降级器会 `inttoptr` 它然后写内存。
    """
    msg = _verify_rejects(_mk_class("A", b"\x04\xbf"), "J002", "reference")
    print(f"      int-as-ref rejected: {msg}")


@test
def test_verifier_rejects_astore_of_an_int():
    msg = _verify_rejects(_mk_class("A", b"\x04\x4b\x00"), "J002", "astore")
    print(f"      astore-of-int rejected: {msg}")


@test
def test_verifier_rejects_a_stack_height_mismatch():
    # 0: iconst_1 / 1: ifeq +4 -> 5 / 4: iconst_2 / 5: goto +3 -> 8 / 8: pop / 9: return
    # 两条边到达 5 时栈深不同（0 与 1）—— 合并点必须拒。
    msg = _verify_rejects(_mk_class("A", b"\x04\x99\x00\x04\x05\xa7\x00\x03\x57\xb1"),
                          "J002", "differ")
    print(f"      height mismatch rejected: {msg}")


@test
def test_verifier_rejects_a_bad_exception_range():
    msg = _verify_rejects(_mk_class("A", b"\xb1", exc=[(0, 99, 0, 0)]), "J002", "exception range")
    print(f"      bad exception range rejected: {msg}")


@test
def test_verifier_rejects_oversized_code():
    msg = _verify_rejects(_mk_class("A", b"\x00" * 65536), "J002", "cap")
    print(f"      oversized code rejected: {msg}")


@test
def test_verifier_names_the_unsupported_constructs():
    msg = _verify_rejects(_mk_class("A", b"\xa8\x00\x03"), "J003", "jsr")
    print(f"      jsr rejected: {msg}")
    msg = _verify_rejects(_mk_class("A", b"\x0b\xb1"), "J003", "float")
    print(f"      float rejected: {msg}")


def _fieldref_pool(owner: str, field: str, desc: str) -> list:
    return [None,
            (lomjava.CP_UTF8, owner), (lomjava.CP_CLASS, 1),
            (lomjava.CP_UTF8, field), (lomjava.CP_UTF8, desc),
            (lomjava.CP_NAME_AND_TYPE, 3, 4),
            (lomjava.CP_FIELDREF, 2, 5)]


@test
def test_verifier_rejects_private_and_final_violations():
    """访问控制：另一个类碰私有字段、或在本类构造器之外写 final 字段。"""
    pool = _fieldref_pool("A", "x", "I")
    a = lomjava.ClassFile(
        "A", [], [lomjava.Field(lomjava.ACC_PRIVATE | lomjava.ACC_FINAL, "x", "I")], pool)
    # aconst_null; getfield #6; pop; return
    read = lomjava.Method(lomjava.ACC_STATIC, "m", "()V",
                          b"\x01\xb4\x00\x06\x57\xb1", 4, 4, [])
    b = lomjava.ClassFile("B", [read], [], pool)
    try:
        lomjava.verify_classes([a, b])
    except lomjava.JavaError as e:
        assert str(e).startswith("J002") and "private" in str(e), e
        print(f"      private field rejected: {e}")
    else:
        raise AssertionError("private field access was accepted")

    # aconst_null; iconst_1; putfield #6; return —— 在 B 里写 A 的 final 字段
    write = lomjava.Method(lomjava.ACC_STATIC, "m", "()V",
                           b"\x01\x04\xb5\x00\x06\xb1", 4, 4, [])
    a2 = lomjava.ClassFile("A", [], [lomjava.Field(lomjava.ACC_FINAL, "x", "I")], pool)
    b2 = lomjava.ClassFile("B", [write], [], pool)
    try:
        lomjava.verify_classes([a2, b2])
    except lomjava.JavaError as e:
        assert str(e).startswith("J002") and "final" in str(e), e
        print(f"      final write rejected: {e}")
    else:
        raise AssertionError("a final field write outside <init> was accepted")


@test
def test_verifier_rejects_a_stack_map_that_disagrees():
    """`StackMapTable` 说的栈深/槽型必须与推导结果一致（docs/282 §8.1）。"""
    import struct
    # same_locals_1_stack_item, offset_delta 0, 一个 Integer —— 但推导在 0 处栈是空的
    smt = struct.pack(">H", 1) + b"\x40\x01"
    msg = _verify_rejects(_mk_class("A", b"\xb1", smt=smt), "J002", "stack depth")
    print(f"      stack map rejected: {msg}")


@test
def test_verifier_honours_protected_and_the_super_chain():
    """`protected` 在子类里可用、在包外非子类里不可用 —— 要沿超类链判。"""
    pool = _fieldref_pool("p1/A", "x", "I")
    a = lomjava.ClassFile("p1/A", [],
                          [lomjava.Field(lomjava.ACC_PROTECTED, "x", "I")], pool)
    # aconst_null; getfield #6; pop; return
    read = lomjava.Method(lomjava.ACC_STATIC, "m", "()V",
                          b"\x01\xb4\x00\x06\x57\xb1", 4, 4, [])
    outside = lomjava.ClassFile("p2/B", [read], [], pool)
    try:
        lomjava.verify_classes([a, outside])
    except lomjava.JavaError as e:
        assert str(e).startswith("J002") and "protected" in str(e), e
        print(f"      protected outside a subclass rejected: {e}")
    else:
        raise AssertionError("protected access outside the package/subclass was accepted")
    sub = lomjava.ClassFile("p2/C", [read], [], pool, super_name="p1/A")
    lomjava.verify_classes([a, sub])
    print("      protected inside a subclass accepted")


@test
def test_verifier_rejects_uninitialized_this_escaping():
    """构造器里 `this` 在 `invokespecial <init>` 之前不能当引用用（JVMS §4.10.1.9）。"""
    # <init>: aload_0; athrow
    msg = _verify_rejects(_mk_class("A", b"\x2a\xbf", method="<init>", access=0x0001),
                          "J002", "uninitialized")
    print(f"      uninitialized this rejected: {msg}")


@test
def test_verifier_caps_the_class_count():
    many = [lomjava.ClassFile(f"C{i}", [], [], []) for i in range(lomjava.MAX_CLASSES + 1)]
    try:
        lomjava.verify_classes(many)
    except lomjava.JavaError as e:
        assert str(e).startswith("J002") and "classes" in str(e), e
        print(f"      class cap: {e}")
    else:
        raise AssertionError("the class-count cap was not enforced")


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
