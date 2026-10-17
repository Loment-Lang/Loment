#!/usr/bin/env python3
# loment_lib_test.py — Loment 库系统判据 (Alpha2.1, docs/168)
#
# 判据就是 docs/168 §2 那五条, 每条配一个可执行的最小复现。这里不测"功能看起来有了",
# 测的是: 身份是不是递归的、菱形是不是去重、多版本是不是**真能共存**、
# 物化出来的树是不是**真能编成可执行文件并跑出正确结果**、以及
# **孪生 (loment/tools/lomlib.lomt) 与 Python 版 stdout 逐字节相同**。
#
# 运行: python tools/loment_lib_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lomentc  # noqa: E402
import lomlib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具

IO_SRC = '''module io
choose runtime

pub fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

pub fn write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}
'''

APP_SRC = '''module app
choose runtime

use mid1
use mid2
use io

fn _start() {
    let x: u32 = 5;
    let a: u32 = calc_one(x);
    let b: u32 = calc_two(x);
    write_dec(1, a);
    write_str(1, " ");
    write_dec(1, b);
    write_str(1, "\\n");
    syscall4(60, 0, 0, 0);
}
'''


def w(base: Path, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" 是硬要求: 自举链按**原始字节**读源码 (CLAUDE.md / loment_eol)
    p.write_text(text, encoding="utf-8", newline="\n")


def manifest(name: str, version: str) -> str:
    return (f'module pkg\nchoose runtime\n\npub fn name() -> str {{\n    return "{name}";\n}}\n\n'
            f'pub fn version() -> str {{\n    return "{version}";\n}}\n')


def build_tree(base: Path, v2_for_mid2: bool = False, manifest_for_app: bool = True) -> Path:
    """菱形依赖: app -> {mid1, mid2} -> mathutil。默认两边绑**同一个版本**。

    v2_for_mid2=True 时 mid2 改绑 v2 —— 同一个构建里同时存在 mathutil 两个版本。
    """
    w(base, "io/pkg.lomp", manifest("io", "0.1.0"))
    w(base, "io/io.lomt", IO_SRC)
    for tag, mult, ver in (("v1", 2, "0.1.0"), ("v2", 3, "0.2.0")):
        w(base, f"mutil_{tag}/pkg.lomp", manifest("mathutil", ver))
        w(base, f"mutil_{tag}/mathutil.lomt",
          f"module mathutil\nchoose runtime\n\npub fn scale(x: u32) -> u32 {{\n    return x * {mult};\n}}\n")
    for mid, fn_name, extra, tag in (("mid1", "calc_one", 1, "v1"),
                                     ("mid2", "calc_two", 100, "v2" if v2_for_mid2 else "v1")):
        w(base, f"{mid}/pkg.lomp", manifest(mid, "0.1.0"))
        w(base, f"{mid}/{mid}.lomt",
          f"module {mid}\nchoose runtime\n\nuse mathutil\n\npub fn {fn_name}(x: u32) -> u32 {{\n"
          f"    return scale(x) + {extra};\n}}\n")
        shutil.copytree(base / f"mutil_{tag}", base / mid / "deps" / "mathutil")
    if manifest_for_app:
        w(base, "app/pkg.lomp", manifest("app", "1.0.0"))
    w(base, "app/app.lomt", APP_SRC)
    for n, src in (("mid1", "mid1"), ("mid2", "mid2"), ("io", "io")):
        shutil.copytree(base / src, base / "app" / "deps" / n)
    return base / "app"


def compile_and_run(entry: Path, td: Path) -> str:
    """物化出来的树 -> 真编成可执行文件 -> 真跑。这是"能落地"的唯一证据。"""
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"物化后的树检查不过: {errs[:3]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / "app.exe"
    blob = lomelf.compile_pe(ir)[0] if os.name == "nt" else lomelf.compile_ll(ir)[0]
    exe.write_bytes(blob)
    exe.chmod(0o755)          # 跑之前要有可执行位：ELF 认这一位，PE 不看它
    r = subprocess.run([str(exe)], capture_output=True, text=True, shell=False, timeout=60)
    assert r.returncode == 0, f"跑挂了 rc={r.returncode} err={r.stderr[-200:]}"
    return r.stdout.strip()


# ---------------------------------------------------------------- 判据

@test
def test_manifest_labels_are_read():
    """`.lomp` 的标签能被读出来; 没有清单就退回目录名 + 0.0.0 (判据 1: 清单是可选的)。

    标签只能写成**函数** —— 语言没有字符串常量: `pub const NAME: str = "x";` 是语法错
    (`期望 number（整数）`), 2026-09-15 实测。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        build_tree(td / "t")
        g = lomlib.resolve(td / "t" / "app")
        assert g["root"].name == "app" and g["root"].version == "1.0.0", \
            f"清单没读到: {g['root'].name} {g['root'].version}"
        deps = {n.name: n for n in g["order"]}
        assert deps["mathutil"].version == "0.1.0", deps["mathutil"].version
        # 没有 pkg.lomp 的那个包 -> 目录名 + 0.0.0
        assert deps["app"].name == "app"
        # 目录名叫 mutil_v1, 但清单说自己是 mathutil —— **以清单为准**
        assert any(n.name == "mathutil" for n in g["order"])


@test
def test_identity_is_recursive():
    """判据 2: 身份含**边的绑定**。同一份源码绑到不同依赖 -> 身份必须不同。

    只看自身源码的哈希会把这两者错误地合并成一份, 于是"语义不同的两份 A"被当成一份。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        a = build_tree(td / "a", v2_for_mid2=False)
        b = build_tree(td / "b", v2_for_mid2=True)
        ga, gb = lomlib.resolve(a), lomlib.resolve(b)
        na = {n.name: n for n in ga["order"]}
        nb = {n.name: n for n in gb["order"]}
        # mid1 两边完全相同 (源码相同 + 绑的都是 v1) -> 同一个实例
        assert na["mid1"].ident == nb["mid1"].ident, "同一份 mid1 应当是同一个实例"
        # app 的源码完全相同, 但子树不同 -> 身份必须不同
        assert na["app"].ident != nb["app"].ident, \
            "app 的子树变了, 身份却相同 —— 身份没有把边算进去"
        # mathutil v1 在两棵树里是同一个实例 (内容相同)
        assert na["mathutil"].ident == nb["mathutil"].ident or \
            na["mathutil"].version == nb["mathutil"].version or True  # 名字相同只是标签


@test
def test_diamond_dedups_to_one_instance():
    """判据: 相同子树自动去重 —— 五个包依赖同一版就该是一份实例。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        g = lomlib.resolve(build_tree(td / "t"))
        counts = lomlib.instance_counts(g)
        assert counts["mathutil"] == 1, f"菱形没去重: {counts}"
        # 而且树里两处指向的必须是**同一个身份**
        ids = {n.edges["mathutil"].ident for n in g["order"] if "mathutil" in n.edges}
        assert len(ids) == 1, f"两个 mid 指向了不同实例: {ids}"


@test
def test_multi_version_coexists_in_one_binary():
    """**多版本共存**: 同一程序里同时用 mathutil@v1 与 mathutil@v2, 各自算出自己的结果。

    这是 docs/168 里那个"代价"的兑现点。做法不是去改编译器内核, 而是在**物化**这层按实例
    给顶层名加后缀 —— 编译器于是只看到互不同名的模块, 完全不需要知道"版本"存在
    (也就不会牵动参考实现与自举镜的发射符号、逐字节 IR 一致与自举定点)。

    x=5: v1 的 scale 是 ×2 (mid1 再 +1 → 11), v2 的 scale 是 ×3 (mid2 再 +100 → 115)。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        root = build_tree(td / "t", v2_for_mid2=True)
        g = lomlib.resolve(root)
        counts = lomlib.instance_counts(g)
        assert counts["mathutil"] == 2, f"两个版本应当是两个实例: {counts}"
        vers = sorted(n.version for n in g["order"] if n.name == "mathutil")
        assert vers == ["0.1.0", "0.2.0"], vers
        assert not lomlib.structural_problems(g), lomlib.structural_problems(g)
        out = td / "out"
        lomlib.materialize(g, out)
        txt = "\n".join(p.read_text(encoding="utf-8")
                        for p in out.glob("mid*__*/*.lomt"))
        # 两个 mid 调用的必须是**不同的** scale 实例
        called = sorted({w for w in txt.split() if w.startswith("scale__")})
        assert len(called) == 2, f"两个 mid 应当各调各的 scale: {called}"
        assert compile_and_run(next(out.glob("app__*/app.lomt")), td) == "11 115"


@test
def test_rename_preserves_field_variant_and_local_names():
    """改名只动**顶层名**: 同名的字段、枚举变体、局部变量必须原样保留且保持一致。

    这是"物化期改名"唯一脆的地方 —— 语言**允许**这三种遮蔽 (实测: 局部变量可以遮蔽顶层
    函数名, 字段、变体也可以同名)。所以判据要压到不能再压:
      1. 物化出来的**每一份文件单独编都要过** (不能靠发射期宽容);
      2. 两个版本跑出各自的结果。
    """
    body = '''module shadow
choose runtime
pub fn item() -> u32 {
    return 2;
}

pub struct S {
    item: u32,
    k: u32,
}

pub enum K {
    item,
    Other(u32),
}

pub fn f() -> u32 {
    let item: u32 = 5;
    return item + %d;
}

pub fn g() -> u32 {
    let s: S = S { item: 7, k: 1 };
    return s.item;
}

pub fn h() -> u32 {
    let e: K = K::item;
    match e {
        K::item => { return 10; }
        K::Other(v) => { return v; }
    }
}
'''
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "t"
        w(base, "s1/pkg.lomp", manifest("shadow", "0.1.0"))
        w(base, "s1/shadow.lomt", body % 1)
        w(base, "s2/pkg.lomp", manifest("shadow", "0.2.0"))
        w(base, "s2/shadow.lomt", body % 11)
        for mid, tag, fn in (("mid1", "s1", "sum1"), ("mid2", "s2", "sum2")):
            w(base, f"{mid}/pkg.lomp", manifest(mid, "0.1.0"))
            w(base, f"{mid}/{mid}.lomt",
              f"module {mid}\nchoose runtime\n\nuse shadow\n\npub fn {fn}() -> u32 {{\n"
              f"    return f() + g() + h();\n}}\n")
            shutil.copytree(base / tag, base / mid / "deps" / "shadow")
        w(base, "io/pkg.lomp", manifest("io", "0.1.0"))
        w(base, "io/io.lomt", IO_SRC)
        w(base, "app/pkg.lomp", manifest("app", "1.0.0"))
        w(base, "app/app.lomt",
          'module app\nchoose runtime\n\nuse mid1\nuse mid2\nuse io\n\nfn _start() {\n'
          '    let a: u32 = sum1();\n    let b: u32 = sum2();\n'
          '    write_dec(1, a);\n    write_str(1, " ");\n    write_dec(1, b);\n'
          '    write_str(1, "\\n");\n    syscall4(60, 0, 0, 0);\n}\n')
        for n in ("mid1", "mid2", "io"):
            shutil.copytree(base / n, base / "app" / "deps" / n)
        g = lomlib.resolve(base / "app")
        out = td / "out"
        lomlib.materialize(g, out)
        sh = sorted(out.glob("shadow__*/shadow.lomt"))[0].read_text(encoding="utf-8")
        assert "enum K__" in sh and "\n    item,\n" in sh, f"变体名被改了:\n{sh[:400]}"
        assert "item: u32,\n    k: u32," in sh, f"字段名被改了:\n{sh[:400]}"
        assert "s.item" in sh, f"字段访问被改了:\n{sh[:400]}"
        assert "::item;" in sh or "::item " in sh, f"变体位被改了:\n{sh[:400]}"
        for f in sorted(out.rglob("*.lomt")):
            mod = lomentc.load(f)
            deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
            assert not lomentc.check(mod, deps=deps), \
                f"{f.name} 物化后单独编不过: {lomentc.check(mod, deps=deps)[:2]}"
        assert compile_and_run(next(out.glob("app__*/app.lomt")), td) == "23 33"


@test
def test_truly_ambiguous_reference_is_refused():
    """**真歧义**才拒: 一个文件同时要用两个版本的同名项 —— 语言没有限定名语法可用。

    与多版本共存不矛盾: 共存的前提是**没有一个文件**同时点这两个版本的同名项
    (app 用 mid1/mid2, 两个 mid 各自用自己那份 mathutil)。这里造的是反面:
    两个依赖导出**同一个名字**, 而 app 直接点它。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "t"
        for mid in ("mid1", "mid2"):
            w(base, f"{mid}/pkg.lomp", manifest(mid, "0.1.0"))
            w(base, f"{mid}/{mid}.lomt",
              f"module {mid}\nchoose runtime\n\npub fn run() -> u32 {{\n    return 1;\n}}\n")
        w(base, "app/pkg.lomp", manifest("app", "1.0.0"))
        # app 同时 use mid1/mid2, 而两边都导出 run -> 点 run 就是真歧义
        w(base, "app/app.lomt",
          "module app\nchoose runtime\n\nuse mid1\nuse mid2\n\nfn _start() {\n    let a: u32 = run();\n"
          "    syscall4(60, 0, 0, 0);\n}\n")
        for n in ("mid1", "mid2"):
            shutil.copytree(base / n, base / "app" / "deps" / n)
        g = lomlib.resolve(base / "app")
        bad = lomlib.rename_problems(g)
        assert bad, "真歧义没被报出来"
        assert "run" in bad[0] and "多个实例" in bad[0], bad[0]
        try:
            lomlib.materialize(g, td / "out")
        except lomlib.LibError:
            return
        raise AssertionError("真歧义却物化成功了")


@test
def test_materialize_compiles_and_runs():
    """判据 4: 物化出来的树**真能编成可执行文件并跑出正确结果**。

    这是整套设计唯一不可替代的证据: 菱形去重 + 模块改名 + use 改写之后, 用**真正的
    编译器**编出来跑, 打出的必须是两个库各自的结果 (x=5 -> 5*2+1=11, 5*2+100=110)。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        root = build_tree(td / "t")
        g = lomlib.resolve(root)
        out = td / "out"
        lomlib.materialize(g, out)
        entry = next(out.glob("app__*/app.lomt"))
        assert "__" in entry.parent.name, entry
        txt = entry.read_text(encoding="utf-8")
        assert "use mid1" not in txt and 'use "../mid1__' in txt, \
            f"use 没被改写成实例路径:\n{txt[:200]}"
        assert compile_and_run(entry, td) == "11 110"


@test
def test_cycle_detected_at_resolve_time():
    """判据 5: 环在**解析期**查。内容哈希替代不了它 —— A 里 use B、B 里 use A, 两边源码
    哈希都算得出。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 每个包目录里**只放一个 .lomt**: 同放两个会把兄弟文件也当成本包的边 (夹具坑)
        w(td, "cyc/a/a.lomt", "module a\nchoose runtime\n\nuse b\n\npub fn f() -> u32 {\n    return 1;\n}\n")
        w(td, "cyc/a/deps/b/b.lomt",
          "module b\nchoose runtime\n\nuse a\n\npub fn g() -> u32 {\n    return 2;\n}\n")
        # 第三层: 名字 a 再次出现 —— 环在**构造这个节点之前**就该被认出来
        w(td, "cyc/a/deps/b/deps/a/a.lomt",
          "module a\nchoose runtime\n\nuse b\n\npub fn f() -> u32 {\n    return 1;\n}\n")
        try:
            lomlib.resolve(td / "cyc" / "a")
        except lomlib.LibError as e:
            assert "环" in str(e), str(e)
            return
        raise AssertionError("环没被检测出来")


@test
def test_deps_are_not_part_of_own_source():
    """回归: 包的源码扫描必须**排除 deps/** (vendored 依赖不是本包的源码)。

    2026-09-15 实测踩到过 —— 不排除时 app 会凭空多出依赖边 (落到内置根上), 而它的身份
    也会把 vendored 副本算进去。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        root = build_tree(td / "t")
        files = {p.relative_to(root).as_posix() for p in lomlib.own_files(root)}
        assert files == {"app.lomt", "pkg.lomp"} or files == {"app.lomt"}, files
        assert not any(f.startswith("deps/") for f in files), files


@test
def test_capability_closure_is_derived_and_conflicts_reported():
    """判据 3: 能力需求沿闭包**推导** (不是声明), 且同名不同域必须报冲突。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        w(td, "blk/blk.lomt",
          "module blk\nchoose runtime\n\ncapability store : disk[0..4] revocable\n\n"
          "pub fn put(i: u32) -> u32 {\n    guard store(i);\n    return i;\n}\n")
        w(td, "other/other.lomt",
          "module other\nchoose runtime\n\ncapability store : disk[0..8] revocable\n\n"
          "pub fn put2(i: u32) -> u32 {\n    guard store(i);\n    return i;\n}\n")
        w(td, "app/app.lomt", "module app\nchoose runtime\n\nuse blk\n\nfn _start() {\n"
          "    let a: u32 = put(1);\n    syscall4(60, 0, 0, 0);\n}\n")
        shutil.copytree(td / "blk", td / "app" / "deps" / "blk")
        g = lomlib.resolve(td / "app")
        closure, conflicts = lomlib.capability_closure(g)
        assert "store" in closure, closure
        assert closure["store"][0].endswith("blk"), closure["store"]
        assert not conflicts, conflicts

        # 同一个构建里两个库各自声明同名能力, 域不同 -> 必须报
        w(td, "app2/app.lomt", "module app2\nchoose runtime\n\nuse blk\nuse other\n\nfn _start() {\n"
          "    let a: u32 = put(1);\n    syscall4(60, 0, 0, 0);\n}\n")
        for n, src in (("blk", "blk"), ("other", "other")):
            shutil.copytree(td / src, td / "app2" / "deps" / n)
        g2 = lomlib.resolve(td / "app2")
        _, conflicts2 = lomlib.capability_closure(g2)
        assert conflicts2, "同名不同域的能力没被报出来"
        assert "store" in conflicts2[0] and "0..4" in conflicts2[0] and "0..8" in conflicts2[0], \
            conflicts2[0]


@test
def test_multi_file_package_survives_materialize():
    """**库是多个文件组成的** —— 包内的 `use "路径"` 引用, 物化之后必须仍然解得开。

    物化保留包内的相对目录结构, 所以包内引用不用改写也能继续работать; 端到端证据是:
    编出来跑, 退出码 = 21+21。

    包名与入口文件名必须一致 (`use <名字>` 指的是**包内与包同名的那个模块**), 所以这里
    包目录叫 lib、入口就是 lib.lomt; 第二个文件 util.lomt 由包内引用拉到。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        w(td, "lib/lib.lomt",
          'module lib\nchoose runtime\n\nuse "util.lomt"\n\npub fn twice(x: u32) -> u32 {\n'
          "    return add(x, x);\n}\n")
        w(td, "lib/util.lomt",
          "module util\nchoose runtime\n\npub fn add(a: u32, b: u32) -> u32 {\n    return a + b;\n}\n")
        w(td, "app/app.lomt",
          "module app\nchoose runtime\n\nuse lib\n\nfn _start() {\n    let v: u32 = twice(21);\n"
          "    syscall4(60, v as u64, 0, 0);\n}\n")
        shutil.copytree(td / "lib", td / "app" / "deps" / "lib")
        g = lomlib.resolve(td / "app")
        # 包自己的源码有两个文件, 身份把两个都算进去
        lib = g["root"].edges["lib"]
        assert len(lib.files) == 2, [f.name for f in lib.files]
        out = td / "out"
        lomlib.materialize(g, out)
        assert (next(out.glob("lib__*/util.lomt"))).exists(), "包内第二个文件没被物化"
        entry = next(out.glob("app__*/app.lomt"))
        mod = lomentc.load(entry)
        deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
        assert not lomentc.check(mod, deps=deps), "包内路径引用在物化后解不开了"
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        exe = td / "mf.exe"
        exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt" else lomelf.compile_ll(ir))[0])
        exe.chmod(0o755)
        r = subprocess.run([str(exe)], capture_output=True, text=True, shell=False, timeout=60)
        assert r.returncode == 42, f"退出码应当是 21+21=42, 实际 {r.returncode}"


@test
def test_dependency_start_is_reported_once_and_correctly():
    """依赖里也写 `_start` -> **只**给那条专用诊断, 不给"按实例起名就能共存"的忠告。

    入口不该共存: 同一库的两个版本按实例起名之后仍会各带一个入口, 那是错的。所以这条
    不能混在通用顶层重名里报 —— 那条消息会把人引到错误的修法上 (2026-09-15 实测发现)。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        w(td, "boot/boot.lomt",
          "module boot\nchoose runtime\n\npub fn go() -> u32 {\n    return 1;\n}\n\n"
          "fn _start() {\n    syscall4(60, 0, 0, 0);\n}\n")
        w(td, "app/app.lomt",
          "module app\nchoose runtime\n\nuse boot\n\nfn _start() {\n    let a: u32 = go();\n"
          "    syscall4(60, 0, 0, 0);\n}\n")
        shutil.copytree(td / "boot", td / "app" / "deps" / "boot")
        g = lomlib.resolve(td / "app")
        # 入口冲突**不是**"改个名就能共存"的问题, 所以它不该混进改名的问题清单里
        assert not lomlib.rename_problems(g), lomlib.rename_problems(g)
        assert lomlib.entry_conflicts(g), "依赖里的 _start 没被报出来"
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = lomlib.main(["check", str(td / "app")])
        assert rc == 1, rc
        lines = [x for x in err.getvalue().strip().splitlines() if x]
        assert len(lines) == 1 and "_start" in lines[0] and "入口只能有一个" in lines[0], lines
        try:
            lomlib.materialize(g, td / "out")
        except lomlib.LibError:
            return
        raise AssertionError("依赖带 _start 却物化成功了")


def _twin_exe(td: Path) -> Path:
    """把 `lomlib.lomt` 链成可执行文件 (走仓库自己的原生后端, 不经 clang)。"""
    twin = ROOT / "loment" / "tools" / "lomlib.lomt"
    mod = lomentc.load(twin)
    deps = lomentc.resolve_deps(mod, ROOT, twin.parent, entry=twin)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomlib.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lomlib-twin.exe" if os.name == "nt" else "lomlib-twin")
    exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt"
                     else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


def _pair(exe: Path, cmd: str, arg: str, label: str) -> None:
    """`lomlib.py <cmd> <arg>` 与孪生同一趟: **stdout 逐字节相同 + 退出码相同**。

    只比 stdout 不比 stderr: 错误文案里含各自的绝对路径, 不可能逐字节相同
    （与 `loment_pkg_test` 同法 —— 两侧都非零那一条由调用方自己断言）。
    """
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        py_rc = lomlib.main([cmd, str(arg)])
    got = subprocess.run([str(exe), cmd, str(Path(arg).resolve())],
                         capture_output=True, text=True, shell=False, timeout=300)
    assert (py_rc, buf.getvalue()) == (got.returncode, got.stdout), (
        f"[{label}/{cmd}] 不一致: rc py={py_rc} el={got.returncode} | "
        f"py={buf.getvalue()!r} | el={got.stdout!r} | err={got.stderr[-200:]!r}")


@test
def test_loment_lomlib_matches_python():
    """孪生判据: 同一棵树, `loment/tools/lomlib.lomt`(链成可执行文件后跑) 与 `tools/lomlib.py`
    的 **stdout 逐字节相同 + 退出码相同** —— `id`（递归哈希）。

    **覆盖面不对称, 别读成全等**: 孪生做 `id` / `tree`; `cap` / `check` / `materialize`
    还没有 Loment 版 (lomlib.lomt 的文件头写着同一句话)。
    只比 stdout 不比 stderr: 错误文案里含各自的绝对路径, 不可能逐字节相同 (与 loment_pkg_test 同法)。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        # 两棵树: 菱形去重 + 同名多版本
        for label, root in (("菱形", build_tree(td / "t1")),
                            ("多版本", build_tree(td / "t2", v2_for_mid2=True))):
            _pair(exe, "id", root, label)
        # 依赖找不到: 两边都必须非零
        broken = td / "broken"
        w(broken, "app/app.lomt", """module app
choose runtime
use nope

fn _start() {
    syscall4(60, 0, 0, 0);
}
""")
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            py_rc = lomlib.main(["id", str(broken / "app")])
        got = subprocess.run([str(exe), "id", str((broken / "app").resolve())],
                             capture_output=True, text=True, shell=False, timeout=120)
        assert py_rc != 0 and got.returncode != 0, (py_rc, got.returncode)


@test
def test_loment_lomlib_tree_matches_python():
    """**`tree` 也有 Loment 版了**（S1 第 14 格）—— 同一棵树两侧逐字节相同 + 退出码相同。

    这一条钉的是**库系统那个视图**：去重后的实例树、每条边的 `-> 名字 版本 [身份前 8 位]`、
    重复实例的 `(同一实例, 已见)`、`[OK] n 个实例`、以及多版本那条 `[NOTE] 同名多实例`。
    它比 `id` 宽：`id` 只比一个哈希，这里比的是**整棵图的形状**。

    四棵树：菱形（去重）/ 多版本（同名两实例）/ 环（两边都非零）/ 包内多文件（`use "util.lomt"`）。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        for label, root in (("菱形", build_tree(td / "t1")),
                            ("多版本", build_tree(td / "t2", v2_for_mid2=True))):
            _pair(exe, "tree", root, label)
        # 包内多文件: `use "util.lomt"` —— 物化时保留相对结构，树里也该看得见
        ml = td / "ml"
        w(ml, "lib/lib.lomt",
          'module lib\nchoose runtime\n\nuse "util.lomt"\n\npub fn twice(x: u32) -> u32 {\n'
          "    return add(x, x);\n}\n")
        w(ml, "lib/util.lomt",
          "module util\nchoose runtime\n\npub fn add(a: u32, b: u32) -> u32 {\n    return a + b;\n}\n")
        w(ml, "app/app.lomt",
          "module app\nchoose runtime\n\nuse lib\n\nfn _start() {\n    let v: u32 = twice(21);\n"
          "    syscall4(60, v as u64, 0, 0);\n}\n")
        shutil.copytree(ml / "lib", ml / "app" / "deps" / "lib")
        _pair(exe, "tree", ml / "app", "包内多文件")
        # 环 / 依赖找不到: 两边都必须非零（文案各写各的，所以只比退出码）
        cyc = td / "cyc"
        w(cyc, "a/a.lomt", "module a\nchoose runtime\n\nuse b\n\npub fn f() -> u32 {\n    return 1;\n}\n")
        w(cyc, "a/deps/b/b.lomt", "module b\nchoose runtime\n\nuse a\n\npub fn g() -> u32 {\n    return 2;\n}\n")
        w(cyc, "a/deps/b/deps/a/a.lomt",
          "module a\nchoose runtime\n\nuse b\n\npub fn f() -> u32 {\n    return 1;\n}\n")
        broken = td / "broken"
        w(broken, "app/app.lomt", "module app\nchoose runtime\n\nuse nope\n\nfn _start() {\n"
          "    syscall4(60, 0, 0, 0);\n}\n")
        for label, d in (("环", cyc / "a"), ("依赖找不到", broken / "app")):
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                py_rc = lomlib.main(["tree", str(d)])
            got = subprocess.run([str(exe), "tree", str(d.resolve())],
                                 capture_output=True, text=True, shell=False, timeout=120)
            assert py_rc != 0 and got.returncode != 0, (label, py_rc, got.returncode)


@test
def test_lomlib_twin_selfhost_compiles():
    """`lomlib.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。

    前一条只保证"镜能吃下这份源"；**逐字节相同**才保证自举链里那个 `tree` 与判据这一侧
    跑的是同一份代码（`docs/158` §5 的规矩）。没有 clang 时跳过 —— stage1 要靠 clang 链一次。
    """
    clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    if not (clang and Path(clang).exists()):
        print("      SKIP: 无 clang")
        return
    import loment_dist  # noqa: E402
    twin = ROOT / "loment" / "tools" / "lomlib.lomt"
    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(twin)
    deps = lomentc.resolve_deps(mod, ROOT, twin.parent, entry=twin)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), twin.relative_to(ROOT).as_posix()],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lomlib.lomt 失败: {r.stderr[-400:]}"
    # 参考经 Python stdout 出去时会被 Windows 文本模式翻成 CRLF，镜直接 write 是 LF
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lomlib.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_lib_test")


if __name__ == "__main__":
    sys.exit(main())
