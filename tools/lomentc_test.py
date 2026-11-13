#!/usr/bin/env python3
# lomentc_test.py — L1 Loment 编译器 + Potato v0 校验器自检 (docs/143 §验收)
#
# 运行: python tools/lomentc_test.py   (退出码 0 = 全绿)
# 说明: 编译/运行验证 (rustc) 由 docs/143 §5 记录的命令执行, 本套件只做静态判定。

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import loment_diag  # noqa: E402
import loment_geometry  # noqa: E402
import lomentc  # noqa: E402
import lspe  # noqa: E402
import potato  # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "loment" / "examples" / "demo.lomt"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def parse(src: str) -> lomentc.Module:
    m = lomentc.Parser(lomc.lex(src), src).parse()
    # L0/L1/L2 的分析现在**不在 `parse()` 里**（它要跨模块的安全表，见 `_gc_alpha_pass`）——
    # 判据若只 `parse` 就要读 `f.l0`/`s.l2`/`Return.l1`，得自己把那一趟跑上。无依赖 ⇒ `[]`。
    lomentc._gc_alpha_pass(m, [])
    return m


def errs(src: str) -> list[str]:
    return lomentc.check(parse(src))

def _compile_dep_unit(files: dict[str, str], entry: str = "app.lomt"):
    """Compile a multi-file unit with the reference; return (errs, IR)."""
    with tempfile.TemporaryDirectory(prefix="lomentc-1175-") as td:
        d = Path(td)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8", newline="\n")
        src = (d / entry).resolve()
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps) if not errs else None
        return errs, ir


# ---------------------------------------------------------------- 正例

@test
def test_int_literal_as_ptr_is_legal_and_lowers_to_inttoptr():
    """`0 as ptr`（空指针的惯用写法）要过检查，且**发射成 inttoptr**。

    2026-09-15 由 lompi（另一条线用 Loment 写的包管理器）实测抓到：参考实现把整型
    **字面量**转 ptr 判成非法目标（`as 目标类型非法 ptr`），而自举镜一直放行 ——
    于是同一份源码**参考报错、打包版能编**。两条一起修，缺一条都不行：
    只放行不修发射，参考会发出 `zext i32 0 to ptr`，而 zext 产不出指针，那是**非法 IR**。
    """
    NL = chr(10)
    src = NL.join([
        "module m", "",
        "fn z() -> ptr {", "    return 0 as ptr;", "}", "",
        "fn w(v: u64) -> ptr {", "    return v as ptr;", "}", "",
        "fn _start() {", "    syscall4(60, 0, 0, 0);", "}", "",
    ])
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "inttoptr i32 0 to ptr" in ir, ir
    assert "zext i32 0 to ptr" not in ir, ir
    # 变量那种窄写法本来就没坏，别在修字面量时把它碰坏
    assert "inttoptr i64" in ir, ir


@test
def test_out_of_range_literal_wraps_instead_of_emitting_an_invalid_constant():
    """超范围的字面量**按目标类型环绕**, 不能把写出来的数原样塞进 IR (#529)。

    `let v: u8 = 300;` 在运行时取到的就是 44 —— Loment 的整数是环绕的, 所以发射器要印
    44。印 300 得到的 `store i8 300` **不是合法 LLVM** (i8 常量装不下 300), 只是包内
    链接器自己截断才让它"碰巧"跑对; 换成 clang 会拒。**这是发射侧的缺陷, 不是检查侧**:
    照 Rust 那样在 `check` 直接拒收不符合 Loment 的语义, 所以这里钉的是 IR 的形状。
    """
    NL = chr(10)
    src = NL.join([
        "module m", "choose runtime", "",
        "fn _start() {",
        "    let a: u8 = 300;",
        "    let b: i8 = 200;",
        "    let c: u16 = 65536;",
        "    syscall4(60, 0, 0, 0);",
        "}", "",
    ])
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "store i8 44," in ir, ir
    assert "store i8 -56," in ir, ir
    assert "store i16 0," in ir, ir
    # 反例: 任何一个"写出来的原值"出现在 iN 常量位置, 都是非法 IR
    for bad in ("store i8 300", "store i8 200", "store i16 65536"):
        assert bad not in ir, f"{bad!r} 不是合法 LLVM ({ir})"


@test
def test_int_literal_cast_keeps_the_literal_value():
    """**整数字面量 `as iN` 的值就是字面量本身**（#694）—— 不许先按 i32 截断再放宽。

    `253402300800 as i64` 曾经发成 `zext i32 -769664 to i64` = 4294197632：
    值的低 32 位看着"像个数"，所以两个实现一致时没有任何孪生判据看得见。直接赋值那条
    路一直是 64 位的 —— 坏的正好只是转型那一支。

    **窄目标照旧环绕**（Loment 的整数语义）：`300 as u8` 是 44，不是 300。所以这条判据
    同时钉住两半：宽目标不被截、窄目标仍环绕。
    """
    NL = chr(10)
    src = NL.join([
        "module m", "choose runtime", "",
        "fn _start() {",
        "    let a: i64 = 253402300800 as i64;",
        "    let b: u64 = 253402300800 as u64;",
        "    let d: i64 = 4294967296 as i64;",
        "    let e: i64 = 9223372036854775807 as i64;",
        "    let g: u64 = 18446744073709551615 as u64;",
        "    let h: u8 = 300 as u8;",
        "    let i: i32 = 4294967296 as i32;",
        "    syscall4(60, 0, 0, 0);",
        "}", "",
    ])
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    for want in ("store i64 253402300800", "store i64 4294967296",
                 "store i64 9223372036854775807",
                 "store i64 18446744073709551615"):
        assert want in ir, f"缺少 {want!r}: {ir}"
    assert "zext i32 -769664" not in ir, ir
    # 窄目标仍然按位宽环绕（这一半别在修 #694 时弄丢）
    assert "store i8 44," in ir, ir
    assert "store i32 0," in ir, ir


@test
def test_int_constant_expression_cast_is_accepted():
    """issue #703: **全是字面量的算式**也是整型 —— `(1 << 20) as u64` 不许被拒。

    `expr_type` 对整数字面量返回 `None`（"由上下文定宽"），于是整条算式也是 `None`；
    `as` 那一格把 `None` 当成"不知道类型"，就把 `(1 + 1) as u64` 说成"不是整型" ——
    而同一式子带一个变量（`(x + 1) as u64`）照常放行。报文给的理由还是**假的**。
    自举侧（`checker.lomt` 同处按 `chk_ty_is_int`）一直放行它，所以这同时是一处
    **孪生分歧**：同一份源码，参考拒、打包版能编。

    钉三面：
      * 全是字面量的算式被接受（`+ - * / % << >> & | ^` 与一元 `-`）；
      * 算式**仍在 i32 上算、再按目标放宽**（`zext i32`）—— 与自举侧逐字节一致，
        不是"在 i64 上算"那种看似更聪明的做法（那会换掉孪生那一侧的产物）；
      * 非整型操作数照旧被拒（别把 `as` 放宽成什么都不查）。
    """
    NL = chr(10)
    src = NL.join([
        "module m", "choose runtime", "",
        "fn _start() {",
        "    let a: u64 = (1 + 1) as u64;",
        "    let b: u64 = (1 << 20) as u64;",
        "    let c: u64 = (3 * 1024 - 1) as u64;",
        "    let d: i64 = (0 - 7) as i64;",
        "    let e: u8 = (255 + 1) as u8;",
        "    let f: u64 = ((1 | 2) & 3 ^ 1) as u64;",
        "    let g: u64 = (7 / 2 % 3) as u64;",
        "    syscall4(60, 0, 0, 0);",
        "}", "",
    ])
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "zext i32" in ir, f"常量式转型没有先按 i32 算再放宽: {ir}"
    # 窄目标照旧环绕：`(255 + 1) as u8` 是 256 的一次 `trunc ... to i8`（= 0），
    # 不是原样存进去的 256（那会发成非法 IR）。
    assert re.search(r"trunc i32 \S+ to i8", ir), f"窄目标没有环绕: {ir}"

    bad = NL.join([
        "module m", "choose runtime", "",
        "struct P { x: u32 }", "",
        "fn _start() {",
        "    let p: P = P { x: 1 };",
        "    let a: u64 = p as u64;",
        "    syscall4(60, 0, 0, 0);",
        "}", "",
    ])
    got = errs(bad)
    assert any("as 只能作用于整型" in e for e in got), (
        f"非整型操作数不再被拒（`as` 被放宽过头了）: {got}")


@test
def test_demo_parses_and_checks():
    mod = lomentc.load(DEMO)
    deps = lomentc.resolve_deps(mod, ROOT, DEMO.parent, entry=DEMO)
    assert lomentc.check(mod, deps=deps) == [], lomentc.check(mod, deps=deps)
    assert [f.name for f in mod.funcs] == [
        "fib", "gcd", "popcount", "in_domain", "blk_end", "mask_low", "has_flag",
        "sum_array", "fill_incr", "color_code", "sum_range", "quadruple", "max_blocks",
        "shape_area",
    ]
    # demo.lomt 用的是**名字形式** (语料 2026-09-15 迁过去了); L0 的 lom/fujr.lom 仍是路径形式
    assert mod.name_imports == ["mathutil"], mod.name_imports
    assert mod.uses == ["lom/fujr.lom"]
    assert [c.name for c in mod.caps] == ["blk_write"]
    assert [s.name for s in mod.structs] == ["Blk"]
    names = [e.name for e in mod.enums]
    assert "Color" in names and "Shape" in names, names  # 另有预置 Option/Result
    assert [c.name for c in mod.consts] == ["MAX_BLKS"]
    assert mod.excluded == ["network: 本单元不申请任何 net 能力", "usb: 不触碰 USB 子系统"]


@test
def test_transpile_is_deterministic():
    mod = lomentc.load(DEMO)
    assert lomentc.emit_rust(mod, ROOT) == lomentc.emit_rust(mod, ROOT)
    assert lomentc.emit_potato(mod, ROOT) == lomentc.emit_potato(mod, ROOT)


@test
def test_rust_output_shape():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    for needle in (
        "pub fn fib(n: u32) -> u32 {",
        "pub const CAP_BLK_WRITE_SPACE: &str = \"disk\";",
        "pub const CAP_BLK_WRITE_REVOCABLE: bool = true;",
        "pub const HEADER_SIZE: usize = 64;",   # 来自 use 的 L0 布局
        "while (i < n) {",
    ):
        assert needle in rs, f"缺少 {needle!r}"


@test
def test_potato_emits_used_layouts():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["potato"] == potato.VERSIONS[-1] and doc["unit"] == "demo" and doc["language"] == "loment"
    # v2 的新字段 (docs/143 §3.2): 没写 `choose` 就是默认 `std` —— 对象里永远显式。
    assert doc["mode"] == "std", doc.get("mode")
    assert [r["name"] for r in doc["layouts"]] == ["Header", "Section"]
    cap = doc["capabilities"][0]
    assert cap == {"name": "blk_write", "domain": {"space": "disk", "lo": 0, "hi": 4}, "revocable": True}
    assert potato.validate(doc) == []


# ---------------------------------------------------------------- 负例: 语义检查

@test
def test_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = true; return x; }\n')
    assert any("表达式类型" in x for x in e), e


@test
def test_unknown_function_rejected():
    e = errs('module m\nfn f() -> u32 { return g(1); }\n')
    assert any("未定义的函数" in x for x in e), e


@test
def test_arity_mismatch_rejected():
    e = errs('module m\nfn g(a: u32) -> u32 { return a; }\nfn f() -> u32 { return g(1, 2); }\n')
    assert any("实参" in x for x in e), e


@test
def test_bad_condition_type_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; if x { return 1; } return 0; }\n')
    assert any("条件类型" in x for x in e), e


@test
def test_inverted_capability_domain_rejected():
    e = errs('module m\ncapability c : disk[4..2]\nfn f() -> u32 { return 0; }\n')
    assert any("下界" in x for x in e), e


@test
def test_duplicate_function_rejected():
    e = errs('module m\nfn f() -> u32 { return 0; }\nfn f() -> u32 { return 1; }\n')
    assert any("重复定义" in x for x in e), e


@test
def test_undeclared_variable_rejected():
    e = errs('module m\nfn f() -> u32 { x = 1; return 0; }\n')
    assert any("未声明" in x for x in e), e


@test
def test_return_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { return true; }\n')
    assert any("return 类型" in x for x in e), e


@test
def test_deep_nesting_is_refused_not_crashed():
    """**嵌套太深要报出来**，不是把栈用穿（`#44`）。

    400 层嵌套块（4 KB 的一份源）原先让参考实现抛 `RecursionError`、让**自举侧直接
    段错误**（C 的栈更小）—— 两种都到不了用户面前，而 `check` 是个会被人拿去喂
    **未知输入**的命令（格式化器、LSP 的半成品缓冲、注册表里被截断的第一行）。

    两面都钉：超过上限**必须报**（带行号与原因）；**没到上限的合法程序不许被误拒** ——
    上限设小了就会把正常程序挡在门外，而本仓最深的语料是 12 层。
    """
    deep = ("module m" + chr(10) + "fn f() -> u32 {" + chr(10)
            + "".join("    " * (i + 1) + "if 1 > 0 {" + chr(10) for i in range(200))
            + "    " * 201 + "return 1;" + chr(10)
            + "".join("    " * (200 - i) + "}" + chr(10) for i in range(200)) + "}" + chr(10))
    try:
        lomentc.check(parse(deep))
    except lomentc.LomError as ex:
        msg = str(ex)
        assert "嵌套超过" in msg, msg
        assert str(lomentc.MAX_NEST) in msg, msg
    else:
        raise AssertionError("200 层嵌套没被拒 —— 栈会先爆")
    # 表达式那一侧同一个闸
    paren = "module m" + chr(10) + "fn f() -> u32 {" + chr(10) + "    return " + "(" * 200 + "1"         + ")" * 200 + ";" + chr(10) + "}" + chr(10)
    try:
        lomentc.check(parse(paren))
    except lomentc.LomError as ex:
        assert "嵌套超过" in str(ex), ex
    else:
        raise AssertionError("200 层括号没被拒")
    # 反面：上限以内照旧（注意收尾 —— 嵌套的那几层 `if` 摆在 `while` 体里、
    # 末尾 `return 0;` 收口，不然会被 E024「从末尾掉出去」抓住，那是另一条规则的事）
    ok = ("module m" + chr(10) + "fn f() -> u32 {" + chr(10)
          + "    while 1 > 0 {" + chr(10)
          + "".join("    " * (i + 2) + "if 1 > 0 {" + chr(10) for i in range(8))
          + "    " * 10 + "return 1;" + chr(10)
          + "".join("    " * (9 - i) + "}" + chr(10) for i in range(8))
          + "        return 0;" + chr(10) + "    }" + chr(10) + "    return 0;" + chr(10)
          + "}" + chr(10))
    assert lomentc.check(parse(ok)) == [], lomentc.check(parse(ok))
    print(f"      嵌套上限：超过 {lomentc.MAX_NEST} 层报得出（块与表达式各一条），以内照旧")


# ---------------------------------------------------------------- 负例: Potato 校验器

@test
def test_potato_rejects_unknown_key():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["extra"] = 1
    assert any("未知顶层字段" in x for x in potato.validate(doc))


@test
def test_potato_rejects_bad_domain():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["capabilities"][0]["domain"] = {"space": "disk", "lo": 9, "hi": 1}
    assert any("区间非法" in x for x in potato.validate(doc))


@test
def test_potato_rejects_overlapping_layout():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["layouts"][0]["fields"] = [
        {"name": "a", "type": "u32", "offset": 0},
        {"name": "b", "type": "u32", "offset": 2},
    ]
    assert any("重叠" in x for x in potato.validate(doc))


@test
def test_potato_rejects_out_of_bounds():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["layouts"][0]["fields"] = [{"name": "a", "type": "u64", "offset": 60}]
    assert any("越界" in x for x in potato.validate(doc))


@test
def test_potato_rejects_bad_signature():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["functions"][0]["ret"] = "f32"
    assert any("ret 非法类型" in x for x in potato.validate(doc))


# ---------------------------------------------------------------- --check 漂移

@test
def test_check_mode_detects_drift():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "demo.rs"
        obj = Path(td) / "demo.json"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj)]) == 0
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 0
            out.write_text("// drifted\n", encoding="utf-8")
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 1


@test
def test_struct_literal_missing_field_rejected():
    e = errs('module m\nstruct P { x: u32, y: u32 }\nfn f() -> u32 { let p: P = P { x: 1 }; return p.x; }\n')
    assert any("缺字段" in x for x in e), e


@test
def test_struct_unknown_field_rejected():
    e = errs('module m\nstruct P { x: u32 }\nfn f() -> u32 { let p: P = P { x: 1, z: 2 }; return p.x; }\n')
    assert any("无字段" in x for x in e), e


@test
def test_field_access_on_non_struct_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; return x.y; }\n')
    assert any("非结构体" in x for x in e), e


#: `#323`：类型位置上的未知名 —— 六个位置各一份（第三项是消息里该出现的**定位**）。
#: 它们原先**全走 E002「符号未声明」**，用户拿到的建议是"改拼写 / 补 `use` / 补类型标注"，
#: 三条一条都不适用（`f64` 不是拼错，是这门语言没有浮点）。
#: `array-elem` 那一格还额外量到**自举侧一条都不报**（`check_type` 见到 `[` 直接返回）。
_UNTYPED_SITES: tuple[tuple[str, str, str], ...] = (
    ("let", "module m\nfn f() -> u32 {\n    let a: f64 = 0;\n    return 0;\n}\n",
     "let a 的类型"),
    ("param", "module m\nfn f(p: f64) -> u32 {\n    return 0;\n}\n", "参数 p 的类型"),
    ("return", "module m\nfn f() -> f64 {\n    return 0;\n}\n", "返回类型"),
    ("field", "module m\nstruct S {\n    x: f64,\n}\n\nfn f() -> u32 {\n    return 0;\n}\n",
     "结构体 S.x 的字段类型"),
    ("payload", "module m\nenum E {\n    A(f64),\n}\n\nfn f() -> u32 {\n    return 0;\n}\n",
     "枚举 E::A 的载荷类型"),
    ("array-elem", "module m\nfn f() -> u32 {\n    let a: [f64; 4] = [0, 0, 0, 0];\n"
                   "    return 0;\n}\n", "[f64; 4]"),
)


@test
def test_an_unknown_type_is_not_reported_as_an_undeclared_name():
    """`#323`：`let a: f64 = 0;` 要说「**未知类型**」，不是「符号未声明」。

    **为什么值得一个独立的码**（E030，理由写在 `loment_diag` 那张表的那一条上）：按
    "码按修法分"，E002 的修法是"声明那个名字 / 改拼写 / 补 `use`"，而这一条的修法是
    "**这门语言没有这个类型**"。0.1.4.5 把两者并成一个码，于是最常撞的那一格（浮点）
    拿到的是三条一条都不适用的建议 —— 用户会去找一个不存在的拼写错误。

    判据量的是**参考实现的形状**（码 + 那句定位）。"两边码集相等"由
    `loment_rule_parity` 的同名探针管 —— 那一条只证**一致**，这一条证**对**。
    """
    for name, src, role in _UNTYPED_SITES:
        e = errs(src)
        codes = {loment_diag.family(loment_diag.classify(x)[0]) for x in e}
        assert codes == {"E030"}, (name, e)
        assert any("未知类型" in x and role in x for x in e), (name, e)


@test
def test_the_runtime_refusal_names_the_construct_and_the_reason():
    """`#321`：默认档（`no_runtime`）拒用运行期构造时，正文要**说清是哪一格、为什么**。

    0.1.4.5 给的是 `message: /` —— 正文是一个 token。用户读到的码是 ``E022 bad `choose` ``，
    而他的 `choose` 一行都没写错；真正的原因（`runtime` 那一维是一句**保证**）排在六条
    `why` 的第三条，要读完才知道。修好之后正文是一句话，本判据钉住那句话里必须有三样：

    * 那份保证的来历（`no_runtime`）；
    * 被拒的**构造本身**（``除法 `/` `` / ``取模 `%` `` / `` `alloc` ``）；
    * 以及"为什么"（`保证` —— 产物里不许出现那段运行期）。

    **这一条此前根本不存在**：措辞的修复（PR #398）只被"两边码集相等"盖着，而码集相等
    既看不出正文是 token 还是句子，也看不出它有没有说错话。
    """
    for src, site in (
        ("module m\nfn f() -> u32 {\n    panic(1);\n    return 0;\n}\n", "`panic`"),
        ("module m\nfn f(a: str, b: str) -> bool {\n    return str_eq(a, b);\n}\n",
         "`str_eq`"),
        ("module m\nfn f() -> ptr {\n    return alloc(8);\n}\n", "`alloc`"),
    ):
        e = errs(src)
        hit = [x for x in e if "no_runtime" in x and site in x]
        assert hit, (site, e)
        assert all("保证" in x and len(x) > 60 for x in hit), (site, hit)


@test
def test_bitwise_precedence():
    """& 比 | 结合更紧 (与 Rust 一致)。"""
    rs = lomentc.emit_rust(parse('module m\nfn f() -> u32 { return 1 | 2 & 3; }\n'), ROOT)
    assert "(1 | (2 & 3))" in rs, rs


@test
def test_free_is_refused_on_the_rust_backend():
    """`free` 在 Rust 后端**点名拒**，不再静默降级成空操作（issue #165）。

    **两件事一起钉，缺一条就测不到这件事：**
      * Rust 侧**抛**，而且码是 E023（"这一版还没实现"）—— 不是"你源码写错了"那一类；
      * IR 侧**照旧发真的 `@__loment_free`** —— 只钉前者的话，"两边都不发"也会通过，
        而那恰恰是这条 issue 要消灭的**静默分歧**。这是本仓"孪生判据只证明一致、不证明对"
        的同一条教训：验收要的是"两边不静默地不一致"，不是"Rust 侧闭嘴了"。
    """
    src = ("module m\n"
           "fn f() -> u32 {\n"
           "    let p: ptr = alloc(8);\n"
           "    free(p);\n"
           "    return 0;\n"
           "}\n")
    mod = parse(src)
    try:
        lomentc.emit_rust(mod, ROOT)
        raise AssertionError("Rust 后端给 `free` 发了码 —— 应当点名拒（issue #165）")
    except lomc.LomError as e:
        # 判的是 **`e.msg`（模板本身）**：`classify` 不剥 `行:列: ` 前缀，拿 `str(e)` 去
        # 问会一律落 E0999 —— `native` 那批也一样（既有行为，不是这条引入的）。
        # "每句话都有码"那条判据扫的也正是**模板**。
        code, _title, _hint = loment_diag.classify(e.msg)
        assert code == "E023", f"拒了，但码不是 E023: {code} / {e.msg}"
    assert "__loment_free" in lomentc.emit_llvm(mod, ROOT), \
        "原生侧那条真回收不见了 —— 修 Rust 侧不该动它"


@test
def test_excluded_and_types_in_potato():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["excluded"] == ["network: 本单元不申请任何 net 能力", "usb: 不触碰 USB 子系统"]
    assert doc["types"] == [
        {"name": "Blk", "fields": [{"name": "off", "type": "u32"}, {"name": "len", "type": "u32"}]}
    ]
    assert potato.validate(doc) == []


@test
def test_const_and_array_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub const MAX_BLKS: u32 = 8;" in rs
    assert "pub fn sum_array(xs: [u32; 4]) -> u32 {" in rs
    assert "xs[(i) as usize]" in rs, "下标应降级为 as usize"


@test
def test_const_in_potato():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["consts"] == [{"name": "MAX_BLKS", "type": "u32", "value": 8}]
    assert potato.validate(doc) == []


@test
def test_array_literal_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let a: [u32; 2] = [1, true]; return a[0]; }\n')
    assert any("类型不一致" in x for x in e), e


@test
def test_array_length_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let a: [u32; 3] = [1, 2]; return a[0]; }\n')
    assert any("数组长度不符" in x for x in e), e


@test
def test_index_on_non_array_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; return x[0]; }\n')
    assert any("非数组" in x for x in e), e


@test
def test_const_non_integer_rejected():
    e = errs('module m\nconst C: bool = 1;\nfn f() -> u32 { return 0; }\n')
    assert any("必须是整型" in x for x in e), e


@test
def test_potato_rejects_bad_const():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["consts"][0]["type"] = "bool"
    assert any("必须是整型" in x for x in potato.validate(doc))


@test
def test_potato_accepts_array_signature():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    fn = next(f for f in doc["functions"] if f["name"] == "sum_array")
    assert fn["params"][0]["type"] == "[u32; 4]"
    assert potato.validate(doc) == []


@test
def test_enum_match_and_for_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub enum Color {" in rs
    assert "match c {" in rs and "Color::Red => {" in rs and "_ => {" in rs
    assert "for i in 0..n {" in rs


@test
def test_potato_has_enums():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert {"name": "Color", "variants": ["Red", "Green", "Blue"]} in doc["enums"]
    assert potato.validate(doc) == []


@test
def test_payload_enum_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub enum Shape {" in rs and "Circle(u32)," in rs and "Empty," in rs
    assert "Shape::Circle(r) => {" in rs and "Shape::Square(a) => {" in rs


@test
def test_payload_binding_is_typed():
    e = errs(
        "module m\nenum S { C(u32) }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::C(v) => { let x: bool = v; return 0; }\n    }\n}\n"
    )
    assert any("表达式类型" in x for x in e), e


@test
def test_payload_variant_requires_binding():
    e = errs(
        "module m\nenum S { C(u32) }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::C => { return 0; }\n    }\n}\n"
    )
    assert any("需绑定" in x for x in e), e


@test
def test_no_payload_variant_cannot_bind():
    e = errs(
        "module m\nenum S { E }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::E(x) => { return 0; }\n    }\n}\n"
    )
    assert any("不能绑定" in x for x in e), e


@test
def test_ctor_on_payloadless_rejected():
    e = errs("module m\nenum S { E }\nfn f() -> S { return S::E(1); }\n")
    assert any("不能带参数" in x for x in e), e


@test
def test_payload_type_mismatch_rejected():
    e = errs("module m\nenum S { C(u32) }\nfn f() -> S { return S::C(true); }\n")
    assert any("载荷类型" in x for x in e), e


@test
def test_unknown_payload_type_rejected():
    e = errs("module m\nenum S { C(Foo) }\nfn f() -> u32 { return 0; }\n")
    assert any("未知类型 Foo（枚举 S::C 的载荷类型）" in x for x in e), e


@test
def test_potato_payloads_and_bad_payload():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    shape = next(e for e in doc["enums"] if e["name"] == "Shape")
    assert shape["payloads"] == {"Circle": "u32", "Square": "u32"}
    assert potato.validate(doc) == []
    shape["payloads"]["Nope"] = "u32"  # 不是已声明的变体
    assert any("不是已声明变体" in x for x in potato.validate(doc))


@test
def test_match_non_exhaustive_rejected():
    e = errs(
        "module m\nenum C { A, B }\nfn f(c: C) -> u32 {\n"
        "    match c {\n        C::A => { return 1; }\n    }\n    return 0;\n}\n"
    )
    assert any("不穷尽" in x for x in e), e


@test
def test_match_unknown_variant_rejected():
    e = errs(
        "module m\nenum C { A }\nfn f(c: C) -> u32 {\n"
        "    match c {\n        C::Z => { return 1; }\n        _ => { return 0; }\n    }\n}\n"
    )
    assert any("无变体" in x for x in e), e


@test
def test_match_on_non_enum_rejected():
    e = errs(
        "module m\nfn f(x: u32) -> u32 {\n"
        "    match x {\n        _ => { return 0; }\n    }\n}\n"
    )
    assert any("不是枚举" in x for x in e), e


@test
def test_duplicate_enum_variant_rejected():
    e = errs("module m\nenum C { A, A }\nfn f(c: C) -> u32 { return 0; }\n")
    assert any("变体 A 重复" in x for x in e), e


@test
def test_for_bounds_type_rejected():
    e = errs(
        "module m\nfn f(b: bool) -> u32 {\n    let s: u32 = 0;\n"
        "    for i in 0..b { s = s + i; }\n    return s;\n}\n"
    )
    assert any("应为整型" in x for x in e), e


@test
def test_potato_rejects_bad_enum():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["enums"][0]["variants"] = ["Red", "Red"]
    assert any("重复" in x for x in potato.validate(doc))


@test
def test_import_codegen_and_potato():
    deps = lomentc.resolve_deps(lomentc.load(DEMO), ROOT, DEMO.parent, entry=DEMO)
    assert [d.name for d in deps] == ["mathutil"]
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT, deps)
    assert "// ==== 导入模块 mathutil ====" in rs
    assert "pub fn double(x: u32) -> u32 {" in rs and "pub struct Pair {" in rs
    assert rs.count("pub fn double") == 1, "导入模块只应输出一次"
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT, deps))
    assert doc["imports"] == ["mathutil"]
    assert potato.validate(doc) == []


@test
def test_import_dedup():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "base.lomt").write_text('module base\nfn fb() -> u32 { return 1; }\n', encoding="utf-8")
        (d / "a.lomt").write_text(
            'module a\nuse "base.lomt"\nuse "base.lomt"\nfn fa() -> u32 { return fb(); }\n',
            encoding="utf-8",
        )
        mod = lomentc.load(d / "a.lomt")
        deps = lomentc.resolve_deps(mod, d, d, entry=d / "a.lomt")
        assert [x.name for x in deps] == ["base"]


@test
def test_cycle_import_rejected():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.lomt").write_text('module a\nuse "b.lomt"\nfn fa() -> u32 { return 1; }\n', encoding="utf-8")
        (d / "b.lomt").write_text('module b\nuse "a.lomt"\nfn fb() -> u32 { return 2; }\n', encoding="utf-8")
        mod = lomentc.load(d / "a.lomt")
        try:
            lomentc.resolve_deps(mod, d, d, entry=d / "a.lomt")
        except lomc.LomError as ex:
            assert "循环导入" in ex.msg, ex.msg
        else:
            raise AssertionError("循环导入应被拒绝")


@test
def test_name_import_resolves():
    """名字形式 `use mathutil` 落到 loment/examples/mathutil.lomt (搜索根第一条命中)。"""
    assert lomentc.resolve_name("mathutil", ROOT) == ROOT / "loment" / "examples" / "mathutil.lomt"
    mod = parse("module m\nuse mathutil\nfn f() -> u32 { return double(2); }\n")
    deps = lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    assert [x.name for x in deps] == ["mathutil"]


@test
def test_name_import_not_found():
    mod = parse("module m\nuse nosuchmod\nfn f() -> u32 { return 0; }\n")
    try:
        lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    except lomc.LomError as ex:
        assert "名字导入找不到模块" in ex.msg, ex.msg
    else:
        raise AssertionError("找不到的名字导入应报错")


@test
def test_name_import_ambiguous_is_rejected():
    """命中多处必须报错, **不许静默取第一个** —— 否则搜索顺序会变成隐藏语义。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for rel in ("loment/lib", "loment/examples"):
            (d / rel).mkdir(parents=True)
            (d / rel / "dup.lomt").write_text("module dup\n", encoding="utf-8")
        try:
            lomentc.resolve_name("dup", d)
        except lomc.LomError as ex:
            assert "名字导入有歧义" in ex.msg and "lib" in ex.msg and "examples" in ex.msg, ex.msg
        else:
            raise AssertionError("歧义的名字导入应报错")


@test
def test_name_import_reaches_a_module_inside_a_package():
    """名字形式要能命中**包内模块**, 不只是与包同名那个入口 (2026-09-20)。

    包是一个目录, 里面除了入口还有别的模块 (`std/vec.lomt`、`host/fs.lomt`)。只认入口
    的话, 整包只能被"拖进同一个单元"那一种方式消费 —— 而单元的发射符号是**平的**, 模块
    一多就撞名, 且代价按模块数超线性涨。这两件事一起把 std 挡在门外。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        pkg = proj / "deps" / "geom"
        pkg.mkdir(parents=True)
        (pkg / "geom.lomt").write_text("module geom\nuse \"area.lomt\"\n"
                                       "pub fn g_area(w: u32, h: u32) -> u32 { return ar(w, h); }\n",
                                       encoding="utf-8")
        (pkg / "area.lomt").write_text("module area\npub fn ar(w: u32, h: u32) -> u32 "
                                       "{ return w * h; }\n", encoding="utf-8")
        assert lomentc.resolve_name("area", ROOT, proj) == pkg / "area.lomt"
        # 入口那条路没被新层挤掉: `use geom` 仍然命中 `<包>/<包>.lomt`
        assert lomentc.resolve_name("geom", ROOT, proj) == pkg / "geom.lomt"
        mod = parse("module m\nuse area\nfn f() -> u32 { return ar(3, 4); }\n")
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=proj / "x.lomt")
        assert [x.name for x in deps] == ["area"]


@test
def test_name_import_package_module_ambiguous_is_rejected():
    """两个包里都有同名模块 -> **报错, 不许取先搜到的那个**。与第 3 层同一条理由:
    搜索顺序一旦变成隐藏语义, 换台机器就换了个模块。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        for name in ("geom", "extra"):
            d = proj / "deps" / name
            d.mkdir(parents=True)
            (d / "area.lomt").write_text(f"module area\npub fn ar() -> u32 {{ return {len(name)}; }}\n",
                                        encoding="utf-8")
        try:
            lomentc.resolve_name("area", ROOT, proj)
        except lomc.LomError as ex:
            assert "名字导入有歧义" in ex.msg and "geom" in ex.msg and "extra" in ex.msg, ex.msg
        else:
            raise AssertionError("包内模块重名应报歧义")


@test
def test_project_deps_cannot_shadow_the_toolchain_standard_library():
    """#980: `deps/std` 不能顶掉工具链自带的 `std`。

    依赖混淆的落点就是这一层 —— 项目本地的 `deps/<名字>` 排在 store 前面。保留名
    (`std` / `libl` / `host` / `internet` / `os`) 跳过这一层；非保留名照旧，项目本地的
    `geom` 仍然先命中。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        std = proj / "deps" / "std"
        std.mkdir(parents=True)
        (std / "std.lomt").write_text("module std\npub fn magic() -> u32 { return 42; }\n",
                                      encoding="utf-8")
        got = lomentc.resolve_name("std", ROOT, proj)
        assert got == ROOT / "lompi" / "store" / "std" / "0.1.0" / "std.lomt", got
        geom = proj / "deps" / "geom"
        geom.mkdir()
        (geom / "geom.lomt").write_text("module geom\n", encoding="utf-8")
        assert lomentc.resolve_name("geom", ROOT, proj) == geom / "geom.lomt"


@test
def test_store_modules_resolve_by_name_in_this_checkout():
    """**本仓也是一个 store 消费者**: `lompi/store` 就在仓里, 装出来的前缀才是
    `share/lompi/store`。没有 `<仓根>/lompi/store` 这一条, 在本仓写 `use vec` 一律 E018 ——
    而"本仓能不能用 std"正是它要回答的问题。"""
    store = ROOT / "lompi" / "store"
    assert store.is_dir(), f"仓里没有 {store.relative_to(ROOT)}"
    assert lomentc.resolve_name("vec", ROOT) == store / "std" / "0.1.0" / "vec.lomt"
    assert lomentc.resolve_name("std", ROOT) == store / "std" / "0.1.0" / "std.lomt"
    assert lomentc.resolve_name("fs", ROOT) == store / "host" / "0.1.0" / "fs.lomt"
    assert lomentc.store_roots(ROOT, None) == [store]


@test
def test_store_packages_do_not_shadow_the_built_in_roots():
    """商店里的**包内模块**不许盖住编译器自己的源码。

    这不是假想的: 加包内模块那一层时真撞上了 —— `store/std/0.1.0/interp.lomt` 把
    `loment/selfhost/interp.lomt` 顶掉, 驱动当场编不过 (`CT_HEAP_BYTES` 未解析)。
    四根是编译器自己的源码, 商店里的包是随包发行的库; 后者是**私有名字空间**, 排序上
    必须让位。项目本地 `deps/` 不在此列 —— 那是使用者显式装的, 先于工具链的一切。
    """
    assert lomentc.resolve_name("interp", ROOT) == ROOT / "loment" / "selfhost" / "interp.lomt"
    assert lomentc.resolve_name("mem", ROOT) == ROOT / "loment" / "lib" / "mem.lomt"
    # 商店里确实**存在**同名的包内模块 —— 否则这条判据是空的 (它在测空气)
    assert (ROOT / "lompi" / "store" / "std" / "0.1.0" / "interp.lomt").exists(), \
        "商店里没有 interp.lomt, 这条判据就测不到遮蔽"
    assert (ROOT / "lompi" / "store" / "std" / "0.1.0" / "mem.lomt").exists()


@test
def test_std_package_is_one_unit_without_name_collisions():
    """v. std 的**整包门面必须是一份能装进一个单元**的源码。

    单元的发射符号是平的 (`docs/158` §2), 所以包内任意两个模块的同名顶层声明都是硬错。
    这条把"std 现在装得下"钉住 —— 它同时是给后来人看的棘轮: 往 std 里加模块时撞名,
    门禁当场红, 而不是等到某个用户 `use std` 才发现。

    **只在参考实现这一侧跑**: 自举侧装载同一份语料要按模块数超线性涨 (实测 n=64 已 187 秒,
    128 个模块是几十分钟量级), 拿它当判据会把门禁变成等待。两个实现对"重名要拒"这条**规则**
    的等价性由 `loment_p8_test` 的 `neg_across` 与 `loment_rule_parity` 管; 这里管的是**语料**。
    """
    mod, deps = lomentc.load_unit(ROOT / "lompi" / "store" / "std" / "0.1.0" / "std.lomt", ROOT)
    seen: dict[str, str] = {}
    dups: list[str] = []
    for m in [*deps, mod]:
        decls = ([(f.name, "函数") for f in m.funcs] + [(s.name, "结构体") for s in m.structs]
                 + [(e.name, "枚举") for e in m.enums if not e.from_prelude]
                 + [(c.name, "常量") for c in m.consts])
        for nm, kind in decls:
            if nm in seen and seen[nm] != m.name:
                dups.append(f"{kind} {nm}: {seen[nm]} <-> {m.name}")
            else:
                seen.setdefault(nm, m.name)
    assert not dups, "std 门面装不进一个单元 —— 平名字撞了:\n  " + "\n  ".join(dups)
    assert len(deps) + 1 > 100, f"std 只装进来 {len(deps)+1} 个模块, 门面是不是被改小了"


@test
def test_name_and_path_import_are_equivalent():
    """两种写法装载出**同一串依赖**(名字形式只是路径形式的一层解析)。"""
    src = 'module m\nuse mathutil\nfn f() -> u32 { return double(2); }\n'
    got = [x.name for x in lomentc.resolve_deps(
        parse(src), ROOT, ROOT, entry=ROOT / "x.lomt")]
    want = [x.name for x in lomentc.resolve_deps(
        parse('module m\nuse "loment/examples/mathutil.lomt"\nfn f() -> u32 { return double(2); }\n'),
        ROOT, ROOT, entry=ROOT / "x.lomt")]
    assert got == want == ["mathutil"]


@test
def test_missing_import_rejected():
    mod = parse('module m\nuse "nope/void.lomt"\nfn f() -> u32 { return 0; }\n')
    try:
        lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    except lomc.LomError as ex:
        assert "不存在" in ex.msg, ex.msg
    else:
        raise AssertionError("缺失导入应报错")


@test
def test_collision_with_imported_symbol_rejected():
    deps = lomentc.resolve_deps(lomentc.load(DEMO), ROOT, DEMO.parent, entry=DEMO)
    clash = parse('module m\nfn double(x: u32) -> u32 { return x; }\n')
    e = lomentc.check(clash, deps=deps)
    assert any("重复定义" in x for x in e), e


@test
def test_const_visible_inside_function():
    e = errs("module m\nconst C: u32 = 1;\nfn f() -> u32 { return C; }\n")
    assert e == [], e


NATIVE = ROOT / "loment" / "examples" / "native.lomt"


@test
def test_llvm_backend_m0():
    """M0 原生后端: 标量子集 -> LLVM IR (docs/144)。"""
    mod = lomentc.load(NATIVE)
    ir = lomentc.emit_llvm(mod, ROOT)
    assert lomentc.emit_llvm(mod, ROOT) == ir, "IR 生成不确定"
    for needle in ("define i32 @fib(i32 %n) {", "alloca i32", "icmp ult i32",
                   "L1_wcond:", "br i1", "ret i32"):
        assert needle in ir, f"IR 缺少 {needle!r}"
    assert "mul i32" in ir and ", 3" in ir, "常量应内联到指令"


@test
def test_llvm_signedness_picks_instruction():
    assert "sdiv i32" in lomentc.emit_llvm(parse("module m\nfn f(a: i32, b: i32) -> i32 { return a / b; }\n"), ROOT)
    assert "udiv i32" in lomentc.emit_llvm(parse("module m\nfn f(a: u32, b: u32) -> u32 { return a / b; }\n"), ROOT)


@test
def test_rust_enum_derive_matches_what_we_emit():
    """枚举的 `PartialEq` 只在**载荷也真的可比较**时才发。

    否则 Rust 为枚举生成的 `impl PartialEq` 会要求载荷类型也可比较, 于是把源码里根本
    没写过的 `Span == Span` 编出来, 报 E0369(2026-09-15 用户实测: enum 载荷是 struct 时)。

    钉的是**判据与实际发出的 derive 一致**: 第一版拿"struct 的字段递归算能不能派生"
    当判据, 结论是"Span 可以", 可 struct 那一段根本没发 PartialEq —— 两边对不上, 照旧
    E0369。所以这里比的是生成出来的文本本身。
    """
    src = ("module m\n\nstruct Span {\n    lo: u32,\n    hi: u32,\n}\n\n"
           "enum WithStruct {\n    Empty,\n    Line(Span),\n}\n\n"
           "enum WithScalar {\n    A,\n    B(u32),\n}\n")
    rs = lomentc.emit_rust(parse(src), ROOT)
    assert "#[derive(Clone, Copy, PartialEq)]\npub enum WithScalar" in rs, rs[:400]
    assert "#[derive(Clone, Copy)]\npub enum WithStruct" in rs, rs[:400]
    assert "#[derive(Clone, Copy, PartialEq)]\npub enum WithStruct" not in rs, rs[:400]


@test
def test_native_match_on_call_subject_does_not_crash():
    """被匹配值是**函数调用**时, 原生发射器不许崩。

    2026-09-15 由用户实测报出来(`KeyError: 'v'` @ lomentc.py 的绑定存取), 7 行复现。
    根因不在 match 的发射代码, 而在 `_collect_locals`: 它推被匹配值的类型时给 `expr_type`
    传了**空的 funcs/structs**, 于是"主体是调用"推不出返回类型 -> 枚举查不到 -> 匹配的
    绑定变量根本不会被收集 -> 发射期 `self.vars[bind]` 直接 KeyError(Python 栈回溯,
    不是诊断, 所以 `loment diag` 还说没错误)。同一处对 `for` 的上下界也一样 —— 上下界
    是调用(或字段访问)时, 推断同样落空。
    """
    src = ("module m\n"
           "fn g() -> Result<i64, u32> {\n    return Result::Ok(7);\n}\n"
           "fn m1() -> bool {\n    match g() {\n"
           "        Result_i64_u32::Ok(v) => { return v == 7; }\n"
           "        Result_i64_u32::Err(e) => { return e == 0; }\n    }\n}\n"
           "fn m2() -> bool {\n    if let Result_i64_u32::Ok(v) = g() {\n"
           "        return v == 7;\n    }\n    return false;\n}\n"
           "fn lo() -> u32 {\n    return 1;\n}\n"
           "fn hi() -> u32 {\n    return 3;\n}\n"
           "fn s() -> u32 {\n    let acc: u32 = 0;\n    for i in lo()..hi() {\n"
           "        acc = acc + i;\n    }\n    return acc;\n}\n")
    # 走**和用户完全相同**的那条路: load -> resolve_deps -> check -> emit_llvm
    # (单独 parse 少了装载/准备那几步, 结果不能代表真实行为)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(f)
        deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
        assert lomentc.check(mod, deps=deps) == []
        text = lomentc.emit_llvm(mod, ROOT, deps)
    # 绑定与循环变量**真的落到了 IR**(收集到了 -> 有 alloca)
    assert "%v.addr = alloca i64" in text, text[:400]
    assert "%i.addr = alloca i32" in text, text[:400]
    assert "icmp slt i32" in lomentc.emit_llvm(parse("module m\nfn f(a: i32, b: i32) -> bool { return a < b; }\n"), ROOT)
    assert "lshr i32" in lomentc.emit_llvm(parse("module m\nfn f(a: u32, b: u32) -> u32 { return a >> b; }\n"), ROOT)


@test
def test_llvm_aggregates_m23_m26():
    """M23–M26: struct / 数组 / 枚举 / match 的 IR 形态。"""
    ir = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_agg.lomt"), ROOT)
    assert "alloca { i32, i32 }" in ir, "struct alloca"
    assert "getelementptr inbounds { i32, i32 }" in ir, "struct GEP"
    assert "alloca [4 x i32]" in ir, "数组 alloca"
    assert "alloca { i32, i64 }" in ir, "枚举 tagged union"
    assert "insertvalue { i32, i64 }" in ir and "extractvalue { i32, i64 }" in ir
    assert "switch i32" in ir, "match 降级"
    assert "phi i1" in ir, "M27 短路"


@test
def test_llvm_accepts_aggregate_signature():
    """聚合参数在原生路径可用 (LLVM 结构体按值); C ABI 不保证, 故勿从 C 直接调用。"""
    ir = lomentc.emit_llvm(
        parse("module m\nstruct S { a: u32 }\nfn f(s: S) -> u32 { return s.a; }\n"), ROOT
    )
    assert "define i32 @f({ i32 } %s)" in ir, ir


@test
def test_llvm_check_mode():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.ll"
        obj = Path(td) / "n.json"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj)]) == 0
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 0
            out.write_text("; drift\n", encoding="utf-8")
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 1


@test
def test_str_builtins_m1_m2():
    """M1/M2: str 字面量 + str_len/str_eq/str_byte 的双后端降级。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_str.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert '.len() as u32' in rs and 'as_bytes()[' in rs
    assert '"abc"' in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "constant [3 x i8]" in ir, "字符串常量"
    assert "insertvalue { ptr, i64 }" in ir, "str 值构造"
    assert "call i32 @__loment_memcmp" in ir, "str_eq 降级 (自带运行时)"
    assert "zext i8" in ir, "str_byte 降级"


@test
def test_print_builtin_and_main_entry():
    """`print` 是一个内建（写 fd 1）；`fn main` 是正常入口（没有 `_start` 时由链接器接手）。

    **Why**：hello world 原先要写 `let` + `str_ptr as u64` + 两条 `syscall4` —— 那是
    系统调用的细节，不该由第一个程序承担。`print` 把"写 stdout"收成一个内建；`main`
    让用户程序不必知道 `_start`。两者都必须**两套实现**一致，所以这条判据同时钉：
    参考实现的类型/边界、IR 里 `print` 真的落成 `write(1, …)`、以及 `main` 不被编译器
    改写成 `_start`（合成入口是链接器的事，IR 里凭空多一个 `_start` 会和自举侧分叉）。
    """
    src = "module hello\n\nfn main() {\n    print(\"hi\\n\");\n}\n"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        p = td / "hello.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        assert lomentc.check(mod, deps=deps) == []
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        assert "define void @main()" in ir, ir[:400]
        assert "@_start" not in ir, "合成入口是链接器的事，IR 里不该凭空多一个 `_start`"
        assert 'asm sideeffect "syscall"' in ir and "(i64 1, i64 1," in ir, (
            "`print` 必须落成 write(1, ptr, len)")
        assert "ptrtoint ptr" in ir, "str 的数据指针要转成整数交给 syscall"

        # 类型：print 只吃 str
        bad = td / "bad.lomt"
        bad.write_text("module bad\n\nfn main() {\n    print(1);\n}\n",
                       encoding="utf-8", newline="\n")
        bm = lomentc.load(bad)
        bd = lomentc.resolve_deps(bm, ROOT, bad.parent, entry=bad)
        errs = lomentc.check(bm, deps=bd)
        assert any("print" in e for e in errs), errs

        # 边界计数：print 是**机调用**，不是裸指针变换
        b = lomentc._count_boundary(mod)
        assert (b["syscalls"], b["ptr_transforms"]) == (1, 0), b
    print("      `print`：类型/IR/边界都对；`fn main` 不被改写成 `_start`")


@test
def test_unit_wide_unique_names():
    """跨模块同名顶层符号 -> 编译错误 (发射符号是平的, 见 docs/150 缺口①)。

    单元里两个模块各声明一个私有 `helper` 时, 后端会发出两条 `define @helper` (非法 IR),
    调用点还会解析到同一个函数 (静默错编) —— 必须编译期拒。同一模块**内部**的重名照旧
    由原规则报 (别被这条取代)。

    **消息也要说实话**（`#141`）：它必须点名**两个**模块，而不是说"函数 f 与模块 ma
    重名"（`f` 与 `ma` 并不撞车）。原先只断言"有 `重名`"，于是消息说错话也全绿。
    """
    entry = ROOT / "loment" / "selfhost" / "neg_across" / "entry.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    unit_errs = lomentc.check(mod, deps=deps)
    assert any("重名" in e for e in unit_errs), unit_errs
    dup = [e for e in unit_errs if "`helper`" in e and "重名" in e]
    assert dup, f"没有点名 `helper` 的撞车消息: {unit_errs}"
    # 两边都点名：撞车的两个单元是 `entry` 与 `has_helper`（不是 `helper` 与某个模块）
    for side in ("entry", "has_helper"):
        assert f"模块 `{side}`" in dup[0], f"消息没点名模块 `{side}`: {dup[0]}"
    assert "与模块" not in dup[0], (
        f"还在说「与模块 X 重名」—— 那让用户去调解符号与模块名之间不存在的冲突: {dup[0]}")
    assert "都会发射" in dup[0], f"没说清真正撞的是什么: {dup[0]}"
    e2 = errs('module m\nfn f() -> u32 { return 1; }\nfn f() -> u32 { return 2; }\n')
    assert any("重复定义" in x for x in e2), e2


@test
def test_m2_concat_dual_path_runs_equal():
    """M2/M28: `str_concat` 的两条路径**真的都跑一遍**, 输出逐行相同。

    在这之前"双路径逐值一致"只有文档里的手工命令 (docs/143 §5), 没有自动化判据。
    这条把它钉住: Rust 侧用 rustc 编 `test_*() -> bool` 探针, IR 侧用 clang 编 C 驱动
    调同名函数, 两边打印的 `名字 0/1` 清单必须一字不差, 且全是 1。
    """
    import shutil
    import subprocess
    rustc = shutil.which("rustc")
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not rustc or not clang:
        print("      SKIP: 无 rustc/clang")
        return
    p = ROOT / "loment" / "examples" / "native_concat.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    probes = [f for f in mod.funcs
              if f.name.startswith("test_") and not f.params and f.ret == "bool"]
    assert len(probes) >= 5, f"native_concat 的探针太少: {[f.name for f in probes]}"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(lomentc.emit_rust(mod, ROOT, deps), encoding="utf-8")
        h = ['include!("m.rs");', "fn main() {"]
        for f in probes:
            h.append(f'    println!("{f.name} {{}}", if {f.name}() {{ 1 }} else {{ 0 }});')
        h.append("}")
        (Path(td) / "main.rs").write_text("\n".join(h) + "\n", encoding="utf-8")
        exe = Path(td) / "r.exe"
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(Path(td) / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-600:]
        rust_out = subprocess.run([str(exe)], capture_output=True, text=True,
                                  shell=False).stdout
        (Path(td) / "m.ll").write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
        c = ["#include <stdio.h>"]
        for f in probes:
            c.append(f"extern _Bool {f.name}(void);")
        c.append("int main(void) {")
        for f in probes:
            c.append(f'    printf("{f.name} %d\\n", (int){f.name}());')
        c += ["    return 0;", "}"]
        (Path(td) / "drv.c").write_text("\n".join(c) + "\n", encoding="utf-8")
        exe2 = Path(td) / "i.exe"
        r = subprocess.run([clang, "-O1", "-o", str(exe2), str(Path(td) / "drv.c"),
                            str(Path(td) / "m.ll")], capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-600:]
        ir_out = subprocess.run([str(exe2)], capture_output=True, text=True,
                                shell=False).stdout
    assert rust_out == ir_out, f"双路径输出不同:\nRUST\n{rust_out}\nIR\n{ir_out}"
    assert rust_out.count(" 1") == len(probes), f"有探针不通过:\n{rust_out}"
    print(f"      双路径一致: {len(probes)} 个探针全 1 (rustc 与 clang 输出逐行相同)")


@test
def test_str_type_errors():
    e = errs('module m\nfn f() -> u32 { return str_len(1); }\n')
    assert any("内建 str_len" in x for x in e), e
    e = errs('module m\nfn f() -> bool { return str_eq("a"); }\n')
    assert any("实参" in x for x in e), e
    e = errs('module m\nfn f() -> u32 { return str_byte("a", true); }\n')
    assert any("实参类型" in x for x in e), e


@test
def test_slice_m3():
    """M3: 只读切片 [T] + &array + slice_len + 下标。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_slice.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "xs: &[u32]" in rs, "切片参数"
    assert "(&a)" in rs, "& 取切片"
    assert ".len() as u32" in rs, "slice_len 降级"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "{ ptr, i64 }" in ir and "insertvalue { ptr, i64 }" in ir
    assert "extractvalue { ptr, i64 }" in ir


@test
def test_slice_type_errors():
    # `lifetime_no` 是"绝对关闭"：`&x` 仍只作用于数组（`docs/299` §8）。
    e = errs("module m\nchoose lifetime_no\n"
             "fn f() -> u32 { let x: u32 = 1; return slice_len(&x); }\n")
    assert any("& 只能作用于数组" in x for x in e), e
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(x); }\n")
    assert any("不是数组或切片" in x for x in e), e
    # 默认档（`lifetime_auto`）里 `&x` 是一个借用，`slice_len` 仍然不收它。
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(&x); }\n")
    assert any("不是数组或切片" in x for x in e), e


@test
def test_mut_slice_m4():
    """M4: 可变切片 mut [T] + &mut array + 经切片写回。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_mut.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "xs: &mut [u32]" in rs, "可变切片参数"
    assert "(&mut a)" in rs, "&mut 取切片"
    assert "xs[(i) as usize] = " in rs, "经切片写回"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "store i32" in ir and "getelementptr inbounds i32" in ir


@test
def test_mut_slice_errors():
    e = errs(
        "module m\nfn w(xs: mut [u32]) -> u32 { xs[0] = 1; return xs[0]; }\n"
        "fn f() -> u32 { let a: [u32; 2] = [0, 0]; return w(&a); }\n"
    )
    assert any("要求可变切片" in x for x in e), e
    e = errs(
        "module m\nfn r(xs: [u32]) -> u32 { xs[0] = 1; return xs[0]; }\n"
    )
    assert any("只读切片不能写" in x for x in e), e


@test
def test_m11_three_directory_project():
    """M11: 三目录工程 —— c 导入 b 导入 a, 跨目录路径解析。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a").mkdir()
        (d / "b").mkdir()
        (d / "c").mkdir()
        (d / "a" / "base.lomt").write_text(
            "module base\npub const K: u32 = 3;\npub fn triple(x: u32) -> u32 { return x * K; }\n",
            encoding="utf-8")
        (d / "b" / "mid.lomt").write_text(
            'module mid\nuse "a/base.lomt"\npub fn six(x: u32) -> u32 { return triple(x) * 2; }\n',
            encoding="utf-8")
        (d / "c" / "top.lomt").write_text(
            'module top\nuse "b/mid.lomt"\nfn run(x: u32) -> u32 { return six(x); }\n',
            encoding="utf-8")
        top = lomentc.load(d / "c" / "top.lomt")
        deps = lomentc.resolve_deps(top, d, top_dir := d / "c", entry=d / "c" / "top.lomt")
        assert [x.name for x in deps] == ["base", "mid"], [x.name for x in deps]
        assert lomentc.check(top, deps=deps) == [], lomentc.check(top, deps=deps)
        rs = lomentc.emit_rust(top, d, deps)
        assert "pub fn triple(x: u32) -> u32" in rs and "pub fn six(x: u32) -> u32" in rs


@test
def test_m5_borrow_check():
    """M5: 同一次调用里不得既借又可变借 / 可变借两次。"""
    ok = 'module m\nfn f(a: [u32], b: [u32]) -> u32 { return 0; }\n' \
         'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&a, &a); }\n'
    assert errs(ok) == [], errs(ok)
    bad = 'module m\nfn f(a: mut [u32], b: [u32]) -> u32 { return 0; }\n' \
          'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&mut a, &a); }\n'
    assert any("既被可变借用又被借用" in x for x in errs(bad)), errs(bad)
    bad2 = 'module m\nfn f(a: mut [u32], b: mut [u32]) -> u32 { return 0; }\n' \
           'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&mut a, &mut a); }\n'
    assert any("可变借用两次" in x for x in errs(bad2)), errs(bad2)


@test
def test_m12_private_symbol_invisible():
    """M12: 导入模块未 pub 的符号不可见。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "lib.lomt").write_text(
            "module lib\nfn hidden() -> u32 { return 1; }\npub fn shown() -> u32 { return 2; }\n",
            encoding="utf-8")
        (d / "app.lomt").write_text(
            'module app\nuse "lib.lomt"\nfn f() -> u32 { return hidden(); }\n', encoding="utf-8")
        app = lomentc.load(d / "app.lomt")
        deps = lomentc.resolve_deps(app, d, d, entry=d / "app.lomt")
        e = lomentc.check(app, deps=deps)
        assert any("未定义的函数 hidden" in x for x in e), e
        (d / "app2.lomt").write_text(
            'module app2\nuse "lib.lomt"\nfn f() -> u32 { return shown(); }\n', encoding="utf-8")
        app2 = lomentc.load(d / "app2.lomt")
        deps2 = lomentc.resolve_deps(app2, d, d, entry=d / "app2.lomt")
        assert lomentc.check(app2, deps=deps2) == [], lomentc.check(app2, deps=deps2)


@test
def test_m13_move_semantics():
    """M13: 非 Copy 类型赋值/传参 = 移动; 移动后再用报错; Copy 类型放行。"""
    e = errs('module m\nfn f() -> u32 { let a: [u32; 2] = [1, 2]; let b: [u32; 2] = a; return a[0]; }\n')
    assert any("已被移动" in x for x in e), e
    e = errs("module m\nfn f() -> u32 { let a: [u32; 2] = [1, 2]; let b: [u32; 2] = a; return b[0]; }\n")
    assert e == [], e
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; let y: u32 = x; return x + y; }\n")
    assert e == [], e


@test
def test_m14_no_alloc_audit():
    """M14: 当前语言按构造即 no-alloc, 分配点恒为空。"""
    assert lomentc.alloc_audit(lomentc.load(ROOT / "loment" / "examples" / "native.lomt")) == []
    assert lomentc.alloc_audit(lomentc.load(DEMO)) == []


MEM = ROOT / "loment" / "examples" / "native_mem.lomt"
RAII = ROOT / "loment" / "examples" / "native_raii.lomt"


@test
def test_m15_alloc_both_backends():
    mod = lomentc.load(MEM)
    rs = lomentc.emit_rust(mod, ROOT)
    assert "__loment_alloc" in rs and "__loment_store8" in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_heap = internal global [65536 x i8]" in ir
    assert "getelementptr [65536 x i8]" in ir


@test
def test_m18_wrap_and_divzero():
    """M18: 算术回绕 (Rust 路径需 -O / -C overflow-checks=off); 除零 trap。"""
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "add i32" in ir, "IR 原生回绕"
    assert "icmp eq i32" in ir and "call void @__loment_abort()" in ir, "除零 trap"
    rs = lomentc.emit_rust(lomentc.load(MEM), ROOT)
    assert "a + b" in rs


@test
def test_m19_panic():
    rs = lomentc.emit_rust(parse("module m\nfn f(x: u32) -> u32 { return panic(x); }\n"), ROOT)
    assert "panic!(" in rs
    ir = lomentc.emit_llvm(parse("module m\nfn f(x: u32) -> u32 { return panic(x); }\n"), ROOT)
    assert "call void @__loment_abort()" in ir


@test
def test_m20_ports_rust_only():
    rs = lomentc.emit_rust(lomentc.load(RAII), ROOT)
    assert 'asm!("in al, dx"' in rs and 'asm!("out dx, al"' in rs
    try:
        lomentc.emit_llvm(lomentc.load(RAII), ROOT)
    except lomc.LomError as ex:
        assert "inb" in ex.msg or "outb" in ex.msg, ex.msg
    else:
        raise AssertionError("IR 后端应明确拒绝 inb/outb")


@test
def test_m21_atomics():
    rs = lomentc.emit_rust(lomentc.load(MEM), ROOT)
    assert "fetch_add(" in rs and "Ordering::SeqCst" in rs
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "atomicrmw add ptr" in ir and "seq_cst" in ir


@test
def test_m16_drop_impl():
    rs = lomentc.emit_rust(lomentc.load(RAII), ROOT)
    assert "impl Drop for Guard {" in rs and "fn drop(&mut self) {" in rs
    assert "#[derive(Clone, Copy)]\npub struct Guard" not in rs, "有析构的类型不得 derive Copy"


@test
def test_m17_dangling_borrow_rejected():
    e = errs("module m\nfn f() -> [u32] { let a: [u32; 2] = [1, 2]; return &a; }\n")
    assert any("悬垂" in x for x in e), e


@test
def test_cast_operator():
    rs = lomentc.emit_rust(parse("module m\nfn f(x: u32) -> u8 { return x as u8; }\n"), ROOT)
    assert "(x) as u8" in rs
    ir = lomentc.emit_llvm(parse("module m\nfn f(x: u32) -> u8 { return x as u8; }\n"), ROOT)
    assert "trunc i32" in ir
    e = errs("module m\nfn f(x: str) -> u8 { return x as u8; }\n")
    assert any("as 只能作用于整型" in x for x in e), e


@test
def test_m22_bitfields():
    """M22: 位域操作内建 (get_bits/set_bits) 双后端。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_bits.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "1u16 << (" in rs, "掩码构造"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "lshr i8" in ir and "shl i8" in ir and "xor i8" in ir
    e = errs('module m\nfn f() -> u8 { return get_bits(1, 0); }\n')
    assert any("实参" in x for x in e), e


@test
def test_m31_freestanding_runtime():
    """M31: 自带运行时, 不依赖 libc。"""
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "define internal void @__loment_abort()" in ir
    assert "declare void @abort()" not in ir, "不得再依赖 libc abort"
    ir2 = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_str.lomt"), ROOT)
    assert "define internal i32 @__loment_memcmp" in ir2
    assert "declare i32 @memcmp" not in ir2, "不得再依赖 libc memcmp"


@test
def test_m33_interrupt_calling_convention():
    """M33: interrupt fn -> x86_intrcc。"""
    ir = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_entry.lomt"), ROOT)
    assert "define x86_intrcc void @timer_isr" in ir
    assert "byval" in ir, "x86_intrcc 帧指针需 byval"


CAP = ROOT / "loment" / "examples" / "native_cap.lomt"


@test
def test_p4_domain_table_and_audit():
    """M35/M38: 域描述表 + 审计钩子。"""
    mod = lomentc.load(CAP)
    rs = lomentc.emit_rust(mod, ROOT)
    assert "pub static CAP_DOMAINS: &[CapDomain]" in rs
    assert 'space: "disk", lo: 0, hi: 4, revocable: true' in rs
    assert "__LOMENT_AUDIT" in rs
    # 审计与 trap **各是一个函数**（`#162`）：审计只在放行那一支调用。
    assert "fn __loment_audit(" in rs and "fn __loment_trap(" in rs
    assert "__LOMENT_AUDIT[cap] += 1" in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }]" in ir
    assert "@__loment_audit = internal global [16 x i64]" in ir


@test
def test_p4_violating_guard_has_no_audit_side_effect():
    """`#162`：越界那次**不许**先加审计再 trap（SECURITY.md 属性 1 与 2）。

    两个后端都要问，而且用的不是"看看字符串在不在"：Rust 侧编出来**真跑**一次，
    用一个越界的运行期下标触发 panic，再读 `__LOMENT_AUDIT` —— 修之前它是 1，
    修之后是 0（放行那次才变 1）。IR 侧看**结构**：审计 `store` 必须排在
    `__loment_abort` 之后（也就是只在 `gok` 块里）。
    """
    import shutil
    import subprocess
    src = ("module m\n"
           "choose runtime\n"
           "capability c : disk[0..4]\n"
           "pub fn f(n: u32) -> u32 {\n"
           "    guard c(n);\n"
           "    return n;\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        assert lomentc.check(mod) == [], lomentc.check(mod)
        rs = lomentc.emit_rust(mod, ROOT)
        ir = lomentc.emit_llvm(mod, ROOT)

    # ---- IR：判决搬进 `@__loment_guard2`（docs/306-308 的十种域共用一个判决点），
    # 但**属性不变**：审计的 store 仍只在 `pass:` 块里，`fail:` 块只有 abort + unreachable。
    # 断言比原来更强：它覆盖所有 kind，而不只是调用点那一条。
    assert "call void @__loment_guard2(" in ir, "调用点没走统一判决"
    rt = ir[ir.find("define internal void @__loment_guard2("):]
    fail = rt.find("fail:")
    abort = rt.find("call void @__loment_abort()", fail)
    unreach = rt.find("unreachable", abort)
    pas = rt.find("pass:", unreach)
    # `docs/313`：`pass` 之后先分一次"要不要记账"（链上的祖先只判成员），
    # 审计在 `aaudit` 块里 —— 仍然只有 `pass` 这一条路能到它。
    aaudit = rt.find("aaudit:", pas)
    audit = rt.find("getelementptr i64, ptr @__loment_audit", aaudit)
    store = rt.find("store i64", audit) if audit >= 0 else -1
    assert 0 <= fail < abort < unreach < pas < aaudit < audit < store, (
        "IR 里审计不在 pass 块里（fail={} abort={} pass={} aaudit={} gep={} store={}）—— "
        "越界那次会先写审计（属性 1 破了）".format(fail, abort, pas, aaudit, audit, store))
    assert "br i1 %sn, label %done, label %aaudit" in rt[pas:aaudit], (
        "`aaudit` 不是从 `pass` 的唯一那一支进来的")

    # ---- Rust：真跑。没有 rustc 就跳过这一半（环境限制，不改判据的结论面）
    rustc = shutil.which("rustc")
    if not rustc:
        print("      SKIP: 无 rustc（Rust 运行那一半）")
        return
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "m.rs").write_text(rs, encoding="utf-8", newline="\n")
        main = (
            'include!("m.rs");\n'
            "fn main() {\n"
            "    std::panic::set_hook(Box::new(|_| {}));\n"
            "    let bad = std::panic::catch_unwind(|| f(9));\n"
            '    println!("bad={} audit={}", bad.is_err(), unsafe { __LOMENT_AUDIT[0] });\n'
            "    let ok = std::panic::catch_unwind(|| f(2));\n"
            '    println!("ok={} audit={}", ok.is_ok(), unsafe { __LOMENT_AUDIT[0] });\n'
            "}\n")
        (d / "main.rs").write_text(main, encoding="utf-8", newline="\n")
        exe = d / ("g.exe" if sys.platform == "win32" else "g")
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(d / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-600:]
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             shell=False).stdout
        assert "bad=true audit=0" in out, (
            f"越界那次写了审计（应当是 0）: {out!r}")
        assert "ok=true audit=1" in out, f"放行那次没计审计: {out!r}"
    print("      越界 trap 后审计为 0，放行一次才变 1（Rust）；IR 的 store 在 abort 之后")


@test
def test_p4_guard_bounds():
    """M36: 字面量越界编译期拒绝; 域内放行。"""
    ok = "module m\nchoose runtime\ncapability c : disk[0..4]\nfn f() -> u32 { guard c(2); return 0; }\n"
    assert errs(ok) == [], errs(ok)
    bad = "module m\nchoose runtime\ncapability c : disk[0..4]\nfn f() -> u32 { guard c(9); return 0; }\n"
    assert any("越界" in x for x in errs(bad)), errs(bad)
    e = errs("module m\nchoose runtime\nfn f() -> u32 { guard nope(0); return 0; }\n")
    assert any("未声明的能力" in x for x in e), e


@test
def test_s1_revision_generation_catches_stale_reference():
    """S1（`docs/320`）：`revision` 域 + `generation` / `retire`。

    这是"对象代"的最小面：引用是显式的 `(idx, gen)`，`retire` 把槽的代翻一页，
    旧代在下一次 `guard blk(idx, gen)` 上必须 trap。Rust 侧真跑一遍；
    IR 侧看结构和那三个运行时符号在不在。
    """
    src = (
        "module m\n"
        "choose runtime\n"
        "capability blk : disk[0..7] revision\n"
        "pub fn take(i: u32) -> u64 {\n"
        "    let g: u64 = generation blk(i);\n"
        "    return g;\n"
        "}\n"
        "pub fn use_it(i: u32, g: u64) -> u32 {\n"
        "    guard blk(i, g);\n"
        "    return 1;\n"
        "}\n"
        "pub fn drop_it(i: u32) -> u32 {\n"
        "    retire blk(i);\n"
        "    return 0;\n"
        "}\n"
    )
    assert errs(src) == [], errs(src)
    mod = parse(src)
    ir = lomentc.emit_llvm(mod, ROOT)
    rs = lomentc.emit_rust(mod, ROOT)
    assert "@__loment_gen = internal global [16 x [64 x i64]]" in ir, "缺对象代表"
    assert "call i64 @__loment_gen_read(" in ir, "generation 没走读数"
    assert "call void @__loment_gen_bump(" in ir, "retire 没走翻代"
    assert "i64 15" in ir, "guard 没把 revision 的 kind 15 发出去"
    assert "__LOMENT_GEN" in rs and "__loment_gen_read" in rs and "__loment_gen_bump" in rs

    import shutil
    import subprocess
    rustc = shutil.which("rustc")
    if not rustc:
        print("      SKIP: 无 rustc（Rust 运行那一半）")
        return
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "m.rs").write_text(rs, encoding="utf-8", newline="\n")
        main = (
            'include!("m.rs");\n'
            "fn main() {\n"
            "    std::panic::set_hook(Box::new(|_| {}));\n"
            "    let g = take(3);\n"
            "    let p1 = std::panic::catch_unwind(|| use_it(3, g)).is_ok();\n"
            "    drop_it(3);\n"
            "    let p2 = std::panic::catch_unwind(|| use_it(3, g)).is_err();\n"
            "    let g2 = take(3);\n"
            "    let p3 = std::panic::catch_unwind(|| use_it(3, g2)).is_ok();\n"
            '    println!("g={} pass={} stale={} g2={} pass2={}", g, p1, p2, g2, p3);\n'
            "}\n")
        (d / "main.rs").write_text(main, encoding="utf-8", newline="\n")
        exe = d / ("g.exe" if sys.platform == "win32" else "g")
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(d / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-800:]
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             shell=False).stdout
        assert "g=0 pass=true stale=true g2=1 pass2=true" in out, (
            f"旧的代没有在 retire 后失效: {out!r}")
    print("      旧代 retire 后 trap，新代放行（Rust）；IR 走 gen 表")


@test
def test_s1_revision_requires_the_right_domain_kind():
    """`generation` / `retire` 只对 `revision` 域有意义；`guard` 必须带代。"""
    e = errs("module m\nchoose runtime\ncapability c : disk[0..4]\n"
             "fn f(i: u32) -> u64 { let g: u64 = generation c(i); return g; }\n")
    assert any("revision" in x for x in e), e
    e = errs("module m\nchoose runtime\ncapability c : disk[0..4]\n"
             "fn f(i: u32) -> u32 { retire c(i); return 0; }\n")
    assert any("revision" in x for x in e), e
    e = errs("module m\nchoose runtime\ncapability c : disk[0..4] revision\n"
             "fn f(i: u32) -> u32 { guard c(i); return 0; }\n")
    assert any("第二个实参" in x for x in e), e
    e = errs("module m\nchoose runtime\ncapability c : disk[0..99] revision\n"
             "fn f(i: u32) -> u32 { return 0; }\n")
    assert any("64" in x for x in e), e



@test
def test_1175_dependency_guard_uses_the_dependency_bound():
    """#1175: a guard in a dependency uses **that file's** `lo/hi`.

    Before the fix the reference resolved by bare name against the entry's
    table only, left `Guard` at its default `[0..0]`, and the emitted binary
    trapped at the first in-range index.
    """
    lib = """module lib

choose runtime

capability blk : disk[0..4] revocable

pub fn touch(i: u32) -> u32 {
    guard blk(i);
    return i;
}
"""
    app = """module app

choose runtime

use "./lib.lomt"

fn _start() {
    let x: u32 = touch(3);
    syscall4(60, x as u64, 0, 0);
}
"""
    e, ir = _compile_dep_unit({"lib.lomt": lib, "app.lomt": app})
    assert e == [], e
    call = next(l for l in ir.splitlines() if "call void @__loment_guard2" in l)
    assert "i64 0, i64 4, i64 0, i64 0, i64 0, ptr " in call, call
    table = next(l for l in ir.splitlines() if "@__loment_caps =" in l)
    assert "[1 x" in table and "i64 0, i64 4, i64 1" in table, table


@test
def test_1175_literal_guard_in_dependency_uses_the_declared_bound():
    """The literal check and the emitted bound must be the same declaration."""
    lib = """module lib

choose runtime

capability blk : disk[0..4] revocable

pub fn touch() -> u32 {
    guard blk(3);
    return 3;
}
"""
    app = """module app

choose runtime

use "./lib.lomt"

fn _start() {
    syscall4(60, touch() as u64, 0, 0);
}
"""
    e, ir = _compile_dep_unit({"lib.lomt": lib, "app.lomt": app})
    assert e == [], e
    call = next(l for l in ir.splitlines() if "call void @__loment_guard2" in l)
    assert "i64 0, i64 4, i64 0, i64 0, i64 0, ptr " in call, call


@test
def test_1175_duplicate_capability_across_files_is_e004():
    """The entry may not redeclare a dependency's capability name (#1175)."""
    lib = """module lib

choose runtime

capability blk : disk[0..4] revocable

pub fn touch(i: u32) -> u32 {
    guard blk(i);
    return i;
}
"""
    app = """module app

choose runtime

use "./lib.lomt"
capability blk : disk[0..100] revocable

fn _start() {
    let x: u32 = touch(3);
    syscall4(60, x as u64, 0, 0);
}
"""
    e, _ = _compile_dep_unit({"lib.lomt": lib, "app.lomt": app})
    assert any("同名声明重复" in x for x in e), e
    msg = next(x for x in e if "同名声明重复" in x)
    assert "app.lomt" in msg and "lib.lomt" in msg and ": 6:" in msg, msg


@test
def test_1175_guard_without_any_declaration_is_an_error():
    """A guard whose name is nowhere in the unit must not become `[0..0]`."""
    lib = """module lib

choose runtime

pub fn touch(i: u32) -> u32 {
    guard blk(i);
    return i;
}
"""
    app = """module app

choose runtime

use "./lib.lomt"

fn _start() {
    let x: u32 = touch(3);
    syscall4(60, x as u64, 0, 0);
}
"""
    e, _ = _compile_dep_unit({"lib.lomt": lib, "app.lomt": app})
    assert any("未声明的能力" in x for x in e), e

@test
def test_e028_literal_index_over_a_fixed_array():
    """**字面量下标越过定长数组的界**是**编译期**的一个诊断，不是运行期的一种可能。

    **2026-10-16 改判（issue #18）**：这一条**不再看 `lms`**。原先它只在
    `choose lms_open` 时发，而那个取值是**当时的开发通道中间态**（`docs/253` §14.2；
    2026-10-18 删除、改成四档，`lms` 随 0.1.5 进发行版）—— 于是发行版的用户根本拿不到它。
    可它本来就不是一条 LMS 检查：`docs/205` §1.1 C5
    早把「越界访问在**字面量**时编译期拒」写成语言规则，只是那一格**只覆盖 `guard`**
    （`docs/146` §3：`guard cap(9)` 对域 `[0..4]` 是编译错误）。同一个"字面量越界"、
    两个构造两个答案 —— 那正是 issue #18 点名的不一致。

    **为什么这一格值得拒**（`docs/253` §4.4 实测）：不拒的时候发出来的是
    `getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 10`，而 `inbounds`
    在 LLVM IR 里**恰是"越界即 UB"的标记**（走仓内 `lomelf` 则静默踩相邻字节）。
    """
    body = "fn f() -> u32 { let a: [u32; 4] = [1, 2, 3, 4]; let x: u32 = a[10]; return x; }\n"
    # **默认档也拒**（这正是这一笔改的东西：从前这一行是 `assert errs(off) == []`）
    off = "module m\n" + body
    # **码从分类器拿，不在消息正文里找**（`#486`）：正文不再自带 `E028:` 前缀 ——
    # 渲染器的头一行已经印了码，正文再写一次就是同一件事印两遍。
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E028" for x in errs(off)), errs(off)
    # `lms` 开着也一样拒，而且**只报一条** —— 两条路都在的时候不许数出两条诊断
    on = "module m\nchoose lms_full\n" + body
    assert len([x for x in errs(on) if loment_diag.family(loment_diag.classify(x)[0]) == "E028"]) == 1, errs(on)
    # **边界**：正好在界内的那个下标**不报** —— 否则这条会把"越界"读成"下标 >= 0 就报"
    edge = "module m\n" + body.replace("a[10]", "a[3]")
    assert errs(edge) == [], errs(edge)
    # **只对定长数组**：切片 `[T]` 的长度是运行期的，界不是编译期常量 ⇒ 这一格不报
    sl = "module m\nfn g(xs: [u32]) -> u32 { return xs[10]; }\n"
    assert errs(sl) == [], errs(sl)
    # **只对字面量**：算出来的下标同样不报（这一条只能在编译期比两个已知的数）
    var = "module m\nfn h(i: u32) -> u32 { let a: [u32; 4] = [1, 2, 3, 4]; return a[i]; }\n"
    assert errs(var) == [], errs(var)
    # **写一侧也是同一个界**：`a[10] = 7;` 同判
    w = "module m\nfn k() -> u32 { let a: [u32; 4] = [1, 2, 3, 4]; a[10] = 7; return a[0]; }\n"
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E028" for x in errs(w)), errs(w)


@test
def test_e030_the_runtime_refusal_names_its_own_subsystem():
    """默认档下用了会拖运行期的构造 ⇒ **自有码 E0208**（族 E031），四段正文齐，正文是那句话不是那个 token。

    **Why（issue #321）**：这条诊断原先**并进 E022**「`choose` / `addin` 用法不对」，
    理由是"码按修法分，修法同族（去写一行 `choose runtime`）"。那条理由**数错了读者**：
    报的是用户写的**那个构造**（`/`、`alloc`…），而他那一行 `choose` **没有问题** ——
    实测原样的输出是 `error[E022]: bad `choose` / `addin`` 配 `message: /`，用户去看
    `choose` 翻来覆去都是对的，还得读完 `why:` 里三条无关的才轮到适用的那一条。
    **码是用户最先读到的那个词，指错子系统就是误导，不是简短。**

    **How to apply**：四件事一起钉 —— ① 码是 E0208（不是 E022）；② 正文**是那句话**
    （「除法 `/`」），不是那一格 token 原文；③ 说明卡四段齐、`fixes` 至少三条；④ 库那一侧
    （"依赖用了却没声明"）是**同一族（E031）的另一个细分码 E0188** —— 修法在库那一侧
    （`choose runtime` 写在依赖里），位置不同就是不同的码。
    """
    import loment_diag
    src = "module m\n\nfn f() -> u32 {\n    panic(1);\n    return 0;\n}\n"
    got = errs(src)
    assert got, "默认档下 `panic` 应当被拒"
    codes = {loment_diag.classify(m)[0] for m in got}
    assert codes == {"E0208"}, f"码不是 E0208：{codes} —— {got}"
    # ② 正文是那句话：里面有"`panic`"，而不是只有那一格 token
    assert "`panic`" in got[0], got
    # ③ 说明卡四段齐（两张表都写），fixes 至少三条
    for tbl in (loment_diag.CARDS[208], loment_diag.CARDS_EN[208]):
        assert tbl.what and tbl.why and tbl.yes and tbl.no, tbl
        assert len(tbl.fixes) >= 3, tbl
    assert loment_diag.ASCII_ONE_LINER[208], "`loment codes` 那一行要有"
    assert loment_diag.TITLE_EN[208], "英文标题要有"
    # ④ 六个构造走同一个码（名单是 `_RUNTIME_SITES`），这里抽三个代表：两个内建 + 取模
    for one, want in (("fn f() -> u32 {\n    let p: ptr = alloc(8);\n    return 0;\n}\n",
                       "`alloc`"),
                      ("fn f() -> u32 {\n    panic(1);\n    return 0;\n}\n", "`panic`"),
                      ("fn f(a: str, b: str) -> bool {\n    return str_eq(a, b);\n}\n",
                       "`str_eq`")):
        e = errs("module m\n\nchoose no_runtime\n\n" + one)
        assert e and {loment_diag.classify(m)[0] for m in e} == {"E0208"}, (want, e)
        assert want in e[0], (want, e)
    # 库那一半：依赖用了却没声明 —— 同一件事，同一个码
    dep = ("module dep\n\npub fn dep_alloc() -> ptr {\n    return alloc(8);\n}\n")
    root = ("module m\nuse \"dep.lomt\"\n\nfn f() -> ptr {\n    return dep_alloc();\n}\n")
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "dep.lomt").write_text(dep, encoding="utf-8")
        rp = d / "m.lomt"
        rp.write_text(root, encoding="utf-8")
        mod = lomentc.load(rp)
        deps = lomentc.resolve_deps(mod, ROOT, rp.parent, entry=rp)
        e = lomentc.check(mod, None, deps)
    assert e and {loment_diag.classify(m)[0] for m in e} == {"E0188"}, e
    print("      E031 这一族拆成两格：单元自己用 E0208 / 依赖用了没声明 E0188")


@test
def test_a_literal_led_expression_keeps_its_width():
    """以**整数字面量开头**的表达式**不掉位宽**（issue #19 第 2 条）。

    **Why**：这一条曾经是**静默算错**：`let alllit: u64 = 4294967296 * 2;` 发出的是
    `mul i32`，结果是 `0`；而同一个式子让变量在左（`n * 2`）就是对的。同一件算术，
    两个答案，`check` 全绿 —— 那是"下一个 `alloc` 的尺寸"的直接输入。
    `lompi/lpi_sha.lomt` 因此留下过一条**写法纪律**（"所有算术都让变量在左"），
    它是这个缺口的存在证明（那一份在另一个仓里，跟着一起改）。

    **How to apply**：整型字面量在 `expr_type` 里是 `None`（"由上下文定宽"），于是
    **上下文**（绑定的类型 / 形参类型）说了算 —— 这条把四种形状一起钉住：字面量在左、
    字面量在右、两个都是字面量、以及通过实参传进去。**任何一种退化成 32 位都要红。**
    """
    src = "module m\n\n" + (
        "fn a() -> u64 {\n    let v: u64 = 4294967296 * 2;\n    return v;\n}\n\n"
        "fn b() -> u64 {\n    let v: u64 = 2 * 4294967296;\n    return v;\n}\n\n"
        "fn c() -> u64 {\n    let v: u64 = 4294967296 * 4294967296;\n    return v;\n}\n\n"
        "fn callee(u: u64) -> u64 {\n    return u;\n}\n\n"
        "fn d() -> u64 {\n    return callee(4294967296 * 2);\n}\n\n"
        "fn e() -> u64 {\n    let v: u64 = 4294967296 + 1;\n    return v;\n}\n")
    out = lomentc.emit_llvm(parse(src), ROOT, [])
    # a / b / c / d 各一条 `mul i64 4294967296 …`（d 那条是实参里的同一个式子）
    assert out.count("mul i64 4294967296") + out.count("mul i64 2, 4294967296") == 4, out
    assert "mul i32" not in out, out                # 任何一种退化成 32 位都要红
    assert "add i64 4294967296, 1" in out, out
    print("      字面量开头的宽算术留在 64 位（四种形状）")


@test
def test_arithmetic_is_defined_to_wrap_never_undefined():
    """`+` / `-` / `*` 是**定义好的回绕**，不是 UB（issue #19 第 1 条的落地）。

    **Why**：`docs/144` §35 写着"除零/溢出语义与 Rust 路径不同（LLVM 是 UB，Rust 是
    panic）：M0 不处理，**规则必须在 M2 前定**"。规则在这里定下来，而且是**能机械核**的
    那一种：**发射出来的 IR 里不许出现 `nsw` / `nuw`**。LLVM 的 `add`/`sub`/`mul` 不带
    这两个标记时**默认就是按模回绕**（定义好的），带上它们才是"溢出即 UB"。所以这一条
    同时钉住两件事：语义是"回绕"（用户读得到的那一半写进 `SKILL.md` §6.2 的陷阱表），
    以及**实现没有偷偷把定义好的行为换成 UB**。

    **How to apply**：加新的算术指令（或换后端）时跑这条 —— 谁顺手加上 `nsw`，
    就是把一个已定义的行为换成未定义的行为，而 IR 看着仍然"很正常"。
    除零那一半（`b == 0` 走 trap）由 `test_m18_wrap_and_divzero` 钉着，两条一起才是规则。
    """
    src = ("module m\nchoose runtime\n\nfn f(a: u32, b: u32) -> u32 {\n"
           "    let x: u32 = a + b;\n    let y: u32 = x - b;\n"
           "    let z: u32 = y * a;\n    return z / b % b;\n}\n")
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert " add i32 " in ir and " sub i32 " in ir and " mul i32 " in ir, ir
    assert "nsw" not in ir and "nuw" not in ir, (
        "IR 里出现了 `nsw`/`nuw` —— 那是「溢出即 UB」，而规则定的是**回绕**：" + ir)
    print("      算术不带 nsw/nuw：回绕是定义好的语义")


@test
def test_e029_use_after_free():
    """LMS 第 1 级（`docs/253` §5.1）：**用完再放** —— `free(p)` 之后再用 `p`。

    与 E006（已被移动）**分开**：移走是**所有权**的事，释放是**合法性**的事。
    ⚠ `docs/210` §2.8 给 `free` 加的那五条检查管的是 **`free` 自己那一端**
    （这块是不是真块、有没有重复释放），管不了"**之后再用**" —— 那正是这一格。

    **2026-10-18 改判（0.1.5.1）**：这一对从"加不加 `choose lms_open`"翻成
    "默认满档 ⇒ 开着；降档到 `lms_manage_only_admin` ⇒ 关着" —— 因为 `lms`
    的默认值现在是 `lms_full`，而 `lms_open` / `lms_close` 不再是取值。
    """
    head = "module m\nchoose runtime\n%s\n"

    def body(use_after: bool) -> str:
        if use_after:
            return ("fn f() -> u32 {\n    let p: ptr = alloc(8);\n    free(p);\n"
                    "    let x: u32 = load8(p, 0);\n    return x;\n}\n")
        return ("fn f() -> u32 {\n    let p: ptr = alloc(8);\n"
                "    let x: u32 = load8(p, 0);\n    free(p);\n    return x;\n}\n")

    # **默认 = 满档 ⇒ 第 1 级开着**：use-after-free 编译期拒
    on = head % "" + body(True)
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E029" for x in errs(on)), errs(on)
    # 降到"只要管理器"那一档：第 1 级关着，use-after-free 一个字的检查都没有
    off = head % "choose lms_manage_only_admin" + body(True)
    assert errs(off) == [], errs(off)
    # 显式清单个开关**覆盖档**（`docs/253` §11.4 叠加规则）：降档了也照样开着第 1 级
    ov = head % "choose lms_manage_only_admin\nchoose lms_check" + body(True)
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E029" for x in errs(ov)), errs(ov)
    # **先读后 free** 合法 —— 这一半挡住"见过 free 就报"那种过头实现
    ok = head % "" + body(False)
    assert errs(ok) == [], errs(ok)
    # **free 之后重新赋值** = 这个名字不再指那块死掉的块 ⇒ 不报（与自举侧 `chk_fr_del` 同口径）
    re = head % "" + (
        "fn f() -> u32 {\n    let p: ptr = alloc(8);\n    free(p);\n"
        "    p = alloc(8);\n    return load8(p, 0);\n}\n")
    assert errs(re) == [], errs(re)


@test
def test_pass_block_never_skips_a_hard_check():
    """`lspe pass` 只跳**审计**，不跳**检查**（`#786` 语义表 / 验收第 3 条）。

    E028（字面量越界）与 E029（用完再放）在块里照发 —— 否则旁路就成了语法级的
    "关掉检查器"，而那正是这条设计明确不做的事。两条都用**块外**的对照样本一起钉：
    不是"见过 pass 就报"，是检查器根本没被绕开。
    """
    e028 = ("module m\nchoose runtime\nfn f() -> u32 {\n"
            "    let a: [u32; 4] = [1, 2, 3, 4];\n"
            "    lspe pass \"caller checked\" {\n"
            "        let x: u32 = a[10];\n"
            "    }\n"
            "    return x;\n}\n")
    got = errs(e028)
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E028" for x in got), got
    # 对照：同样的形状、下标在界内 ⇒ 不报（证明上面那条不是"见到 pass 就红"）。
    ok028 = e028.replace("a[10]", "a[3]")
    assert errs(ok028) == [], errs(ok028)

    e029 = ("module m\nchoose runtime\nfn f() -> u32 {\n"
            "    let p: ptr = alloc(8);\n"
            "    lspe pass \"caller checked\" {\n"
            "        free(p);\n"
            "        let x: u32 = load8(p, 0);\n"
            "    }\n"
            "    return x;\n}\n")
    got2 = errs(e029)
    assert any(loment_diag.family(loment_diag.classify(x)[0]) == "E029" for x in got2), got2
    # 对照：先读后 free ⇒ 块里也不报。
    ok029 = e029.replace("        free(p);\n        let x: u32 = load8(p, 0);\n",
                         "        let x: u32 = load8(p, 0);\n        free(p);\n")
    assert errs(ok029) == [], errs(ok029)


@test
def test_m41_excluded_space_enforced():
    src = ('module m\nexcluded "network: 不申请网络"\n'
           'capability net : network[0..1]\nfn f() -> u32 { return 0; }\n')
    assert any("excluded 的空间" in x for x in errs(src)), errs(src)
    ok = ('module m\nexcluded "network: 不申请网络"\n'
          'capability disk : disk[0..1]\nfn f() -> u32 { return 0; }\n')
    assert errs(ok) == [], errs(ok)


@test
def test_m43_capability_fuzz():
    """M43: 随机 (lo, hi, idx) —— 编译期判定必须与域语义一致。"""
    import random

    rnd = random.Random(20260908)  # 固定种子: 可复现的模糊测试 (非加密用途, 故意确定)
    for _ in range(120):
        lo = rnd.randint(0, 8)
        hi = lo + rnd.randint(0, 8)
        idx = rnd.randint(0, 20)
        src = (f"module m\nchoose runtime\ncapability c : disk[{lo}..{hi}]\n"
               f"fn f() -> u32 {{ guard c({idx}); return 0; }}\n")
        got = errs(src)
        should_reject = not (lo <= idx <= hi)
        assert (len(got) > 0) == should_reject, (lo, hi, idx, got)


def _potato(name: str) -> dict:
    p = ROOT / "loment" / "examples" / f"{name}.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    return json.loads(lomentc.emit_potato(mod, ROOT, deps))


@test
def test_m45_form_object_v1_covers_slices_strings_generics():
    """M45: v1 必须导出切片/字符串/泛型/实例/trait/impl, 且全部通过独立校验。"""
    sl = _potato("native_slice")
    assert any(p["type"] == "[u32]" for f in sl["functions"] for p in f["params"]), sl["functions"]
    assert potato.validate(sl) == []
    st = json.loads(lomentc.emit_potato(
        parse('module s\nfn len_of(s: str) -> u32 { return str_len(s); }\n'), ROOT))
    assert any(p["type"] == "str" for f in st["functions"] for p in f["params"])
    assert potato.validate(st) == []
    gen = _potato("native_gen")
    assert {"kind": "fn", "name": "max", "params": ["T"]} in gen["generics"], gen["generics"]
    assert any(i["name"] == "max_u32" and i["of"] == "max" and i["args"] == ["u32"]
               for i in gen["instances"]), gen["instances"]
    assert potato.validate(gen) == []
    tr = _potato("native_trait")
    assert tr["traits"] == [{"name": "Measurable", "methods": ["measure"]}], tr["traits"]
    assert {i["for"] for i in tr["impls"]} == {"Small", "Big"}
    assert all(i["methods"] == ["measure"] for i in tr["impls"]), tr["impls"]
    assert potato.validate(tr) == []
    raii = _potato("native_raii")
    assert raii["impls"] and raii["impls"][0]["trait"] == "Drop"
    assert potato.validate(raii) == []
    entry = _potato("native_entry")
    assert any(f["ret"] == "()" for f in entry["functions"]), entry["functions"]
    assert potato.validate(entry) == []


@test
def test_m45_every_example_exports_a_valid_object():
    ex = ROOT / "loment" / "examples"
    names = sorted(p.stem for p in ex.glob("*.lomt"))
    assert len(names) >= 15, names
    for n in names:
        doc = _potato(n)
        assert doc["potato"] == potato.VERSIONS[-1], n
        assert doc["mode"] in potato.MODES, (n, doc.get("mode"))
        assert potato.validate(doc) == [], (n, potato.validate(doc))


@test
def test_switches_elide_code_and_land_in_potato():
    """开关（`docs/182` §1）: **真的驱动代码裁减**, 且取值进 Potato。

    这是把 `choose` 从"承诺"变成"发明"的那一步。在此之前 `mode` 被校验、进对象、被回放,
    **没有任何一行按它分支**（`loment_build.py` 零命中、`lomelf.py` 里的 `mode` 是 POSIX 的）。
    开关的第一个真消费方就是**代码裁减**, 它不依赖任何跨线契约。

    钉七条（`docs/182` §1.8）:
      * 开着: 体摊到顶层, 里面的东西**存在**;
      * 关着: 体**连 token 都不进 parser** —— 体内的语义错**不报**（这才是"关掉 = 不依赖"）;
        反过来, 引用体内才有的东西就成了未定义;
      * 括号不配对**照报**（体可以不解析, 但结构不能塌）;
      * 未定义的开关 / 同名两次 / 超上限 各有错;
      * 取值进 Potato 且**按名字排序**（确定性是判据）。
    """
    def chk(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            try:
                mod = lomentc.load(p)
            except lomc.LomError as e:
                # **开关在词法流上落定**（docs/182 §2），所以"块没闭合"是**装载期**的错,
                # 抛出来而不是进 check() 的 errs —— 与解析器其它结构错一致。
                return [e.msg], None
            return lomentc.check(mod, deps=[]), json.loads(lomentc.emit_potato(mod, ROOT))

    BODY = ("set choose feat {\n"
            "    fn helper() -> u32 {\n        return 7;\n    }\n}\n")
    CALL = "fn f() -> u32 {\n    return helper();\n}\n"
    # 开着: helper 存在
    errs, doc = chk("module m\n\n" + BODY + "\nchoose feat\n\n" + CALL)
    assert not errs, errs
    assert doc["potato"] == potato.VERSIONS[-1] and doc["switches"] == [{"name": "feat", "on": True}], doc
    # 关着: 体整个不在了 —— 引用 helper 反而成了未定义
    errs, doc = chk("module m\n\n" + BODY + "\nchoose close feat\n\n" + CALL)
    assert errs and "helper" in errs[0], errs
    # 关着: 体内的**语义错不报**（体没进 parser）
    errs, doc = chk("module m\n\nset choose feat {\n    fn helper() -> u32 {\n"
                    "        return undefined_thing;\n    }\n}\n\nchoose close feat\n\n"
                    "fn f() -> u32 {\n    return 1;\n}\n")
    assert not errs, errs
    assert doc["switches"] == [{"name": "feat", "on": False}], doc["switches"]
    # 括号不配对: 照报
    errs, _ = chk("module m\n\nset choose feat {\n    fn h() -> u32 {\n        return 1;\n"
                  "}\n\nfn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "没闭合" in errs[0], errs
    # 未定义的开关
    errs, _ = chk("module m\n\nchoose nope\n\nfn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "未定义的开关" in errs[0], errs
    # 同名两次
    errs, _ = chk("module m\n\n" + BODY + "\nchoose feat\nchoose close feat\n\n"
                  "fn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "写了两次" in errs[0], errs
    # 上限: 500 条能过, 501 条报错（**绝不静默丢**）
    def many(n):
        return ("module m\n\n" + "".join("set choose s%d {}\n" % i for i in range(n))
                + "\nfn f() -> u32 {\n    return 1;\n}\n")
    errs, doc = chk(many(lomentc.MAX_CHOOSE))
    assert not errs, errs[:2]
    assert len(doc["switches"]) == lomentc.MAX_CHOOSE
    errs, _ = chk(many(lomentc.MAX_CHOOSE + 1))
    assert errs and "超过上限" in errs[0], errs
    print("      开关: 裁减/不裁减/potato 排序/上限 %d 都对" % lomentc.MAX_CHOOSE)


@test
def test_addin_carries_switches_across_units():
    """`addin <名字>` + `chooseset.lomt` + **装载器预扫**（`docs/182` §1.4/§1.7，C2）。

    **这条判据就是预扫存在的全部理由**（`docs/182` §1.6 ② 那个鸡生蛋）：根单元的
    `choose verbose` 要按名字找到 `set choose verbose` 的定义，而那份定义在 `addin`
    进来的、**还没读到**的单元里 —— 状态必须在装载**之前**定，可定义又在装载**之后**才
    出现。预扫失效时（表定不下来），**这条是唯一会红的**。

    "跨单元"是字面意思（§1.7）：`addin` 拉进来的单元**本身参与编译**，所以体里的函数
    要给根用就得 `pub` —— 它仍然是一个模块。
    """
    CS = ("module chooseset\n\naddin chooseset\n\n"
          "set choose verbose {\n"
          "    pub fn banner() -> u32 {\n        return 0x5EED;\n    }\n}\n")
    ROOT_SRC = ("module main\n\naddin chooseset\n\n%s\n"
                "fn _start() {\n    syscall4(60, banner() as u64, 0, 0);\n}\n")

    def build(value: str):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "chooseset.lomt").write_text(CS, encoding="utf-8", newline="\n")
            p = d / "main.lomt"
            p.write_text(ROOT_SRC % value, encoding="utf-8", newline="\n")
            try:
                mod, deps = lomentc.load_unit(p, ROOT)
            except lomc.LomError as e:
                return [e.msg], "", None
            errs = lomentc.check(mod, deps=deps)
            ir = "" if errs else lomentc.emit_llvm(mod, ROOT, deps)
            return errs, ir, json.loads(lomentc.emit_potato(mod, ROOT, deps))

    # 开着: `banner` 真的进了单元（预扫读到了 addin 单元里的定义）
    errs, ir, doc = build("choose verbose")
    assert not errs, errs
    assert "define i32 @banner()" in ir, ir[:300]
    assert doc["switches"] == [{"name": "verbose", "on": True}], doc["switches"]
    # 关着: 体**整段不在** —— 引用它反而成了未定义。"关掉 = 不依赖"字面成立。
    errs, ir, doc = build("choose close verbose")
    assert doc["switches"] == [{"name": "verbose", "on": False}], doc["switches"]
    assert "define i32 @banner" not in ir, ir[:300]
    assert any("未定义的函数 banner" in e for e in errs), errs
    print("      addin: 根 choose 读得到 addin 单元里的定义；关着时体整段不在")


@test
def test_addin_and_chooseset_rules():
    """C2 的四条拒绝规则（`docs/182` §1.4/§1.10）—— 每条都要能证伪。"""
    OK_CS = "module chooseset\n\naddin chooseset\n\nset choose feat {\n}\n"

    def two(root_src: str, cs_src: str, name: str = "chooseset"):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / f"{name}.lomt").write_text(cs_src, encoding="utf-8", newline="\n")
            p = d / "main.lomt"
            p.write_text(root_src, encoding="utf-8", newline="\n")
            try:
                mod, deps = lomentc.load_unit(p, ROOT)
            except lomc.LomError as e:
                return [e.msg]
            return lomentc.check(mod, deps=deps)

    # ① addin 目标里写了非 choose 代码 -> 拒。**两个方向都要堵**: `use` 那条禁 choose,
    #    这条路就得禁其余的一切, 否则"装代码"与"装开关"就又混成一件事了。
    errs = two("module main\n\naddin chooseset\n",
               OK_CS.replace("set choose feat {\n}",
                             "fn helper() -> u32 {\n    return 1;\n}"))
    assert errs and "只能写 choose 相关代码" in errs[0], errs

    # ② **库里的 `addin`** -> 拒。它写了也**不会生效**（预扫只走"根 + 根 addin 到的单元"
    #    那张图）—— 静默失效正是本仓反复要消灭的那种东西, 所以报出来。
    errs = two('module main\n\nuse "lib.lomt"\n\nfn _start() {\n}\n',
               "module lib\n\naddin chooseset\n\nfn helper() -> u32 {\n    return 1;\n}\n",
               name="lib")
    assert errs and "库不许 `addin`" in errs[0], errs

    # ③ 开关声明**嵌在另一个开关体里** -> 拒。放开它就把"状态"变成求不动点
    #    （`B` 算不算数取决于 `A` 开没开, 而 `A` 的状态正是预扫要算的东西）。
    errs = two("module main\n\nset choose a {\n    set choose b {\n    }\n}\n\n"
               "fn _start() {\n}\n", OK_CS)
    assert errs and "不许写在另一个开关体里" in errs[0], errs

    # ④ `addin` 递归超深度 -> **报错, 不静默丢**（与 `MAX_USE` 同一条纪律）
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        n = lomentc.MAXDEPTH + 1
        for i in range(n):
            nxt = f"addin a{i + 1}\n" if i + 1 < n else ""
            (d / f"a{i}.lomt").write_text(f"module a{i}\n\n{nxt}", encoding="utf-8",
                                          newline="\n")
        p = d / "main.lomt"
        p.write_text("module main\n\naddin a0\n\nfn _start() {\n}\n",
                     encoding="utf-8", newline="\n")
        try:
            lomentc.load_unit(p, ROOT)
            raise AssertionError(f"{n} 层 addin 应当报错, 却过了")
        except lomc.LomError as e:
            assert "嵌套超过" in e.msg, e.msg
    print("      addin/chooseset: 白名单/库不许 addin/嵌套拒绝/深度上限 四条都对")


@test
def test_mode_follows_choose_and_defaults_to_std():
    """`mode` 就是根单元 `choose` 的那一个值, 不写则 `std` (docs/143 §3.2 / docs/175 §8)。

    这是 docs/175 §8 那条判据的**正向**一半: 形式对象里的 mode 必须跟着源码走。
    反向那一半 (删掉字段 -> 校验器必须红) 在 `potato_test` 的 v2/v3 负例里。
    """
    src = "module m\n\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        doc = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
        assert doc["potato"] == potato.VERSIONS[-1] and doc["mode"] == "no_std", doc.get("mode")
    # 不写 choose -> std
    src2 = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src2, encoding="utf-8", newline="\n")
        doc2 = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
        assert doc2["mode"] == "std", doc2.get("mode")


@test
def test_m46_compiler_self_check_is_mandatory():
    """M46: 形式对象校验失败 => 编译失败 (不可绕过)。"""
    orig = potato.validate
    potato.validate = lambda doc: ["注入错误"]  # noqa: ARG005
    try:
        try:
            lomentc.emit_potato(lomentc.load(DEMO), ROOT)
            assert False, "应当抛 LomError"
        except lomc.LomError as e:
            assert "形式对象自检失败" in str(e), e
    finally:
        potato.validate = orig


@test
def test_m46_cli_requires_potato_with_codegen():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.rs"
        assert lomentc.main([str(DEMO), "--emit-rust", str(out)]) == 2
        assert not out.exists()
        obj = Path(d) / "x.json"
        assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                             "--emit-potato", str(obj)]) == 0
        assert out.exists() and obj.exists()
        assert potato.validate(json.loads(obj.read_text(encoding="utf-8"))) == []


# ---------------------------------------------------------------- 名字形式的搜索层 (2026-09-16)
#
# 触发原因: 装在用户机器上 (没有仓库) 时 `use std` 找不到任何东西 —— 名字形式原先只认四个
# **仓库相对**的根。现在按层搜: 项目本地 deps/ -> 工具链自带的库 -> 内置四根 (只它要求唯一)。
# 规范在 docs/143, 冻结面记录在 docs/158 §2/§5。

def _pkg(d: Path, name: str) -> None:
    """写一个最小可编译的包目录 `<d>/{<name>.lomt, area.lomt}` (入口与它的伴生模块)。"""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.lomt").write_bytes(
        (f"module {name}\n\nuse \"area.lomt\"\n\npub fn g_area(w: u32, h: u32) -> u32 {{\n"
         f"    return ar(w, h);\n}}\n").encode())
    (d / "area.lomt").write_bytes(
        b"module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n")
    # 夹具模拟**工具链自带的**包：清单里必须带 `LomentItem`（docs/331）。
    (d / "pkg.lomp").write_bytes(
        (f'module pkg\n\npub fn name() -> str {{\n    return "{name}";\n}}\n\n'
         f'pub fn origin() -> str {{\n    return "LomentItem";\n}}\n').encode())


def _entry(p: Path, name: str) -> Path:
    p.write_bytes((f"module hi\n\nuse {name}\n\nfn main() -> u32 {{\n"
                   f"    return g_area(3 as u32, 4 as u32);\n}}\n").encode())
    return p


@test
def test_name_form_searches_project_deps_first():
    """`<项目根>/deps/<名字>/<名字>.lomt` 命中即用, 包内的路径形式按**被导入文件所在目录**解析。

    后半句才是关键: `deps/geom/geom.lomt` 里的 `use "area.lomt"` 只相对 `deps/geom/` 成立
    —— 只按 CWD 找会去够 `<CWD>/area.lomt` (一个不存在的路径)。CWD 故意是别的目录。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _pkg(proj / "deps" / "geom", "geom")
        hi = _entry(proj / "hi.lomt", "geom")
        mod = lomentc.load(hi)
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        names = [d.name for d in deps]
        assert names == ["area", "geom"], f"依赖序不对: {names}"
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        assert "define i32 @ar(" in ir and "define i32 @g_area(" in ir, ir[:300]


@test
def test_name_form_falls_through_to_the_toolchain_store():
    """项目里没有 deps/ 时落到**工具链自带的库**: `<工具目录>/../share/lompi/store/`。

    这就是"装完 Loment 就能 `use std`"那一层 —— 用户 2026-09-16 定: 像 python/java 那样。
    """
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "tool"
        (tool / "bin").mkdir(parents=True)
        _pkg(tool / "share" / "lompi" / "store" / "geom" / "0.1.0", "geom")
        proj = Path(td) / "proj"
        proj.mkdir()
        got = lomentc.resolve_name("geom", ROOT, proj, tool / "bin")
        assert got == tool / "share" / "lompi" / "store" / "geom" / "0.1.0" / "geom.lomt", got


@test
def test_store_package_without_the_lomentitem_mark_is_not_a_builtin():
    """没有 `LomentItem` 标记的包**不进内置库的解析面**（docs/331）。

    这道闸挡的是"把一份社区包塞进 store、当成标准库"那条路：名字一样、内容不同。
    """
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "tool"
        (tool / "bin").mkdir(parents=True)
        d = tool / "share" / "lompi" / "store" / "geom" / "0.1.0"
        _pkg(d, "geom")
        (d / "pkg.lomp").unlink()
        proj = Path(td) / "proj"
        proj.mkdir()
        try:
            lomentc.resolve_name("geom", ROOT, proj, tool / "bin")
        except lomentc.LomError as e:
            assert "LomentItem" in str(e), e
        else:
            raise AssertionError("没有标记的包不该被当成内置库")


@test
def test_the_store_can_be_bypassed_entirely():
    """`LOMENT_NO_STORE=1` 让解析器**完全不看** store（docs/331）。

    **Why**：Loment 要能编裸金属 —— 那条路上不该有任何"顺手拉一份标准库"的可能。
    绕开之后 `use std` 找不到就是找不到，`deps/` 与内置四根照常工作。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        dep = proj / "deps" / "mine"
        dep.mkdir(parents=True)
        (dep / "mine.lomt").write_text("module mine\n", encoding="utf-8")
        old = os.environ.get("LOMENT_NO_STORE")
        os.environ["LOMENT_NO_STORE"] = "1"
        try:
            assert lomentc.store_roots(ROOT, None) == []
            assert lomentc.resolve_name("mine", ROOT, proj) == dep / "mine.lomt"
            try:
                lomentc.resolve_name("vec", ROOT, proj)
            except lomentc.LomError as e:
                assert "找不到" in str(e), e
            else:
                raise AssertionError("绕开 store 之后 `use vec` 应当找不到")
        finally:
            if old is None:
                os.environ.pop("LOMENT_NO_STORE", None)
            else:
                os.environ["LOMENT_NO_STORE"] = old


@test
def test_community_deps_cannot_take_a_builtin_package_name():
    """`deps/math` 不能顶替 store 里的 `math` 包 —— 保留名是**store 里全部包名**。

    #980 的保留名原先只有写死的那五个；`docs/331` 起，store 里每个包名都自动成为保留名。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        evil = proj / "deps" / "math"
        evil.mkdir(parents=True)
        (evil / "math.lomt").write_text("module math\n", encoding="utf-8")
        got = lomentc.resolve_name("math", ROOT, proj)
        assert got == ROOT / "lompi" / "store" / "math" / "0.1.0" / "math.lomt", got


@test
def test_store_with_two_versions_is_an_error():
    """自带的库里同名多版本 = 报错, 不猜 —— 编译器不做版本选择 (docs/168 §4.2)。"""
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "tool"
        (tool / "bin").mkdir(parents=True)
        for v in ("0.1.0", "0.2.0"):
            _pkg(tool / "share" / "lompi" / "store" / "geom" / v, "geom")
        proj = Path(td) / "proj"
        proj.mkdir()
        try:
            lomentc.resolve_name("geom", ROOT, proj, tool / "bin")
        except lomentc.LomError as e:
            assert "版本" in str(e), e
        else:
            raise AssertionError("多版本应当报错")


@test
def test_project_local_deps_wins_over_a_builtin_root():
    """`deps/` 里有 `bytes`、仓库内置根里也有 —— 前两层先命中先用, **不报歧义**。

    唯一性只管第③层 (内置五根，含 `loment/lspe`): 它抓的是"作者把名字写重了"; 使用者选
    哪一份是**优先级**的事, 像 PYTHONPATH。两件事混进一个错误, 使用者不知道该改什么。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _pkg(proj / "deps" / "bytes", "bytes")
        got = lomentc.resolve_name("bytes", ROOT, proj, None)
        assert got == proj / "deps" / "bytes" / "bytes.lomt", got
        assert hasattr(lomentc, "NAME_ROOTS") and len(lomentc.NAME_ROOTS) == 5


@test
def test_use_count_over_the_limit_is_an_error():
    """单文件 use 超过 MAX_USE **报错**, 不静默丢 —— 自举镜原先第 9 条起直接不要, 而参考
    实现照收, 两个实现于是对同一份源码给出不同的单元。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        d = proj / "deps" / "many"
        d.mkdir(parents=True)
        for i in range(lomentc.MAX_USE + 1):
            (d / f"m{i}.lomt").write_bytes(
                f"module m{i}\n\npub fn f{i}() -> u32 {{\n    return {i} as u32;\n}}\n".encode())
        uses = "\n".join(f'use "m{i}.lomt"' for i in range(lomentc.MAX_USE + 1))
        (d / "many.lomt").write_bytes(f"module many\n\n{uses}\n".encode())
        hi = Path(td) / "hi.lomt"
        hi.write_bytes(b"module hi\n\nuse many\n\nfn main() -> u32 {\n    return 0;\n}\n")
        mod = lomentc.load(hi)
        try:
            lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        except lomentc.LomError as e:
            assert str(lomentc.MAX_USE) in str(e), e
        else:
            raise AssertionError(f"{lomentc.MAX_USE + 1} 条 use 应当报错")


# ---------------------------------------------------------------- 自定义后缀 (2026-09-16)
# 后缀**不属于语言** (docs/143 §2.3): 名字形式找的是"哪个名字", 后缀是实现细节, 由项目自己
# 那份 `loment.conf` 定。`use "x.foo"` 这种路径形式本来就自带后缀, 不受配置影响。

@test
def test_project_conf_sets_the_name_form_suffix():
    """项目根那份 `loment.conf` 把名字形式的后缀从 `.lomt` 换成 `.foo`。

    两半都要对: ① 名字形式 `use geom` 落到 `<项目根>/deps/geom/geom.foo`; ② 进了包以后,
    包内的**路径形式** `use "area.foo"` 仍按被导入文件所在目录解析 —— 配置只管名字形式,
    别把路径形式也一起改了 (那是两套规则, 混一起就没人能预期)。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        d = proj / "deps" / "geom"
        d.mkdir(parents=True)
        (proj / "loment.conf").write_bytes(
            b'// \xe9\xa1\xb9\xe7\x9b\xae\xe9\x85\x8d\xe7\xbd\xae\n'
            b'module conf\n\n'
            b'pub fn source_ext() -> str {\n    return ".foo";\n}\n')
        (d / "area.foo").write_bytes(
            b"module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n")
        (d / "geom.foo").write_bytes(
            b'module geom\n\nuse "area.foo"\n\npub fn g_area(w: u32, h: u32) -> u32 {\n'
            b"    return ar(w, h);\n}\n")
        hi = proj / "hi.foo"
        _entry(hi, "geom")
        assert lomentc.source_ext_of(proj, None) == ".foo"
        assert lomentc.resolve_name("geom", ROOT, proj, None, ".foo") == d / "geom.foo"
        mod = lomentc.load(hi)
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        assert [m.name for m in deps] == ["area", "geom"], [m.name for m in deps]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        assert ir and "@g_area" in ir, "自定义后缀的单元发不出 IR"


@test
def test_conf_is_optional_and_a_broken_one_falls_back():
    """没配 / 配坏了 / 配了个不以 `.` 开头的 —— 一律**当没配**, 退回 `.lomt`。

    配置文件也是源码, 它会被人改坏。改坏一个字母就把名字形式指到一堆奇怪的文件上, 比
    "退回默认"糟得多: 前者是编译期一连串看不懂的错, 后者至少还编得过。

    注意**不要求 `module` 头**: 与 `lompi.conf` 同一个形状 —— 那边也是一个词法器扫标签,
    根本没有 parser 去要求文件头。另外注释里的同名字符串不该误命中 (词法器天然不会)。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        assert lomentc.source_ext_of(proj, None) == ".lomt", "没 loment.conf 就是默认"
        assert lomentc.source_ext_of(None, None) == ".lomt"
        for bad in (b'pub fn other() -> str { return ".foo"; }\n',
                    b'pub fn source_ext() -> str { return ext; }\n',
                    b'pub fn source_ext() -> str { return "foo"; }\n',
                    b'pub fn source_ext() -> str { return ""; }\n',
                    b'pub fn source_ext() -> str { return ".foo\n',
                    b"fn broken( {\n"):
            (proj / "loment.conf").write_bytes(bad)
            assert lomentc.source_ext_of(proj, None) == ".lomt", bad
        # 注释里出现的 `source_ext` + 字符串不算 —— 词法器扫, 注释进不了词法流
        (proj / "loment.conf").write_bytes(
            b'// source_ext ".bar"\nmodule conf\n\n'
            b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc.source_ext_of(proj, None) == ".foo"
        # 没有 `module` 头也认 —— 与 lompi.conf 同形 (它那边也没要求)
        (proj / "loment.conf").write_bytes(b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc.source_ext_of(proj, None) == ".foo"


@test
def test_custom_suffix_still_reaches_the_toolchain_lomt():
    """项目换成 `.foo` 之后, 工具链自带/内置根里的 `.lomt` 模块**照样找得到**。

    兜底那条不是可选的: 使用者只该改**自己**的源码后缀 —— 仓库内置四根、以及装在工具链
    旁边那份 store 全是 `.lomt`, 少了兜底, 换后缀等于把标准库整个弄丢。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        (proj / "loment.conf").write_bytes(
            b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc._ext_chain(".foo") == (".foo", ".lomt")
        assert lomentc._ext_chain(".lomt") == (".lomt",)
        got = lomentc.resolve_name("mathutil", ROOT, proj, None, ".foo")
        assert got == ROOT / "loment" / "examples" / "mathutil.lomt", got


# ---------------------------------------------------------------- 外部函数 (2026-09-16)
# `extern fn` 是 docs/173（FFI）第 1 阶段的语言面。形状：只有签名、末尾分号、由链接进来的
# 目标文件提供实现。第 1 阶段**只收标量与 ptr** —— 聚合按值与 `str` 都会改变调用点形状，
# 报错退出而不是静默错编。

@test
def test_extern_declares_and_calls():
    """`extern fn` 发 `declare`，调用点是普通 `call` —— C ABI 由 LLVM 自己按平台给。"""
    src = ("module m\n\nextern fn c_add(a: i32, b: i32) -> i32;\n\n"
           "fn f() -> i32 { return c_add(3 as i32, 4 as i32); }\n")
    mod = parse(src)
    assert [x.name for x in mod.externs] == ["c_add"], [x.name for x in mod.externs]
    assert mod.funcs[0].name == "f" and not mod.funcs[0].extern
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "declare i32 @c_add(i32, i32)" in ir, ir[:400]
    # 调用点必须是**普通 call**（没有额外包装）—— 包装一层就等于我们自己在做 ABI 转换，
    # 而原生 LLVM 路径上那件事是白拿的。
    assert "call i32 @c_add(i32 3, i32 4)" in ir, ir[:400]


@test
def test_extern_without_a_return_type_is_void():
    """不写 `-> T` 就是 void —— `extern fn c_free(p: ptr);` 必须收。

    这一条是**实测踩出来的**：parse_fn 原先靠"后面是 `{`"判断无返回类型，而外部函数没有
    函数体（后面是 `;`），于是最普通的释放函数写法直接被判成"缺了 `->`"。
    """
    src = ("module m\nchoose runtime\n\nextern fn c_free(p: ptr);\n\n"
           "fn f() -> u32 { let q: ptr = alloc(8); c_free(q); return 0; }\n")
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT)
    assert "declare void @c_free(ptr)" in ir, ir[:400]
    assert "call void @c_free(ptr " in ir, ir[:400]


@test
def test_extern_rejects_unsupported_signature():
    """`str` / 结构体 / 数组**按值**都不进签名 —— 出现就报错，不静默错编。"""
    for src, why in (
        ("module m\n\nextern fn f(s: str) -> i32;\nfn g() -> i32 { return 0; }\n", "str"),
        ("module m\n\nstruct P { x: u32 }\n\nextern fn f(p: P) -> i32;\nfn g() -> i32 { return 0; }\n", "结构体按值"),
        ("module m\n\nextern fn f(a: [u32; 4]) -> i32;\nfn g() -> i32 { return 0; }\n", "数组按值"),
    ):
        got = errs(src)
        assert any("不支持" in e for e in got), f"{why}: {got}"


@test
def test_extern_conflicts_with_a_real_function():
    """外部函数与普通函数同名 = 两个不同的实现绑到同一个名字上，报重名。"""
    src = ("module m\n\nfn f() -> i32 { return 1; }\n\nextern fn f() -> i32;\n\n"
           "fn g() -> i32 { return 0; }\n")
    got = errs(src)
    assert any("重名" in e for e in got), got


@test
def test_two_modules_may_declare_the_same_extern():
    """两个模块各自 `extern fn c_add` 指的是**同一个外部符号**（等于 C 里重复包头文件）：
    不报重名，且 `declare` **只出一条**。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.lomt").write_bytes(
            b"module a\n\nextern fn c_add(x: i32, y: i32) -> i32;\n\n"
            b"pub fn use_it() -> i32 { return c_add(1 as i32, 2 as i32); }\n")
        (d / "b.lomt").write_bytes(
            b'module b\n\nuse "a.lomt"\n\nextern fn c_add(x: i32, y: i32) -> i32;\n\n'
            b"fn g() -> i32 { return 0; }\n")
        m = lomentc.load(d / "b.lomt")
        deps = lomentc.resolve_deps(m, ROOT, d, entry=d / "b.lomt")
        assert lomentc.check(m, deps=deps) == [], lomentc.check(m, deps=deps)
        ir = lomentc.emit_llvm(m, ROOT, deps)
        assert sum(1 for l in ir.splitlines() if l.startswith("declare i32 @c_add")) == 1, ir[:400]


@test
def test_extern_takes_no_body_and_no_type_parameters():
    """两种写错的形态要在**解析期**挡住：给了函数体、带了类型参数。"""
    body = "module m\n\nextern fn f() -> i32 { return 1; }\n"
    try:
        parse(body)
    except lomc.LomError as e:
        assert "分号" in e.msg, e.msg
    else:
        raise AssertionError("外部函数带函数体应当被拒")
    gen = "module m\n\nextern fn f<T>(a: T) -> i32;\n"
    try:
        parse(gen)
    except lomc.LomError as e:
        assert "类型参数" in e.msg, e.msg
    else:
        raise AssertionError("外部函数带类型参数应当被拒（单态化要看得到源码）")


@test
def test_boundary_builtins_are_real_builtins():
    """`potato.BOUNDARY_BUILTINS` 必须是编译器内建表的一个**子集**。

    **Why** (`docs/205` R5): 那份清单划的是"越过语言保证的那几个内建"这条边界 ——
    它的价值全在"可 grep、可计数、可审计"上。而它必须是 `potato.py` 里**自己的一份**,
    因为那个文件按 M47 **不许 import 编译器**（独立性就是它存在的理由）。两份因此
    可能悄悄漂: 内建改了名、被删掉了 —— 数出来的数会变成 0, 而**没有任何东西会报**。
    这条判据就是那个"会报"。
    **How to apply**: 它钉的是"表里没有编译器不认识的名字"。反过来的那一半 ——
    **新加了一个同样危险的内建而没进表** —— 这条**测不到**, 那一步要人来判。
    """
    missing = [b for b in potato.BOUNDARY_BUILTINS if b not in lomentc.BUILTINS]
    assert not missing, f"BOUNDARY_BUILTINS 里有编译器不认识的名字: {missing}"
    assert potato.BOUNDARY_BUILTINS, "边界内建表空了 —— 那这条判据就没在干活"


# ---------------------------------------------------------------- L0（docs/210 §2）

#: L0 判据用的那一对程序：**逐字同源，只差 `choose` 那一行**（`docs/210` §5 第二行）。
#: 一个函数一格 —— 提升的与**不该提升的**放在同一份里，所以"少提升一格"和"多提升一格"
#: 都会被同一条判据抓住。
#:
#: **`loment_p8_test` 的孪生判据复用这一对源**（`import lomentc_test`）—— 两个实现比的是
#: 同一份程序，各抄一份必然漂。
L0_PAIR_SRC = """module gc_l0_pair
@@CHOOSE@@
choose runtime

const SZ: u32 = 64;

enum E { A(u32), B }

fn consume(y: ptr) -> u32 {
    return load8(y, 0);
}

fn f_pos() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    store8(p, 1, 9);
    return load8(p, 0);
}

fn f_alias(x: ptr) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    return consume(p) + load8(x, 0);
}

fn f_assign(q: ptr) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    p = q;
    return load8(p, 0);
}

fn f_ret() -> ptr {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    return p;
}

fn f_dyn(n: u32) -> u32 {
    let p: ptr = alloc(n);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_shadow() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    if load8(p, 0) == 7 {
        let p: ptr = alloc(16);
        store8(p, 0, 9);
    }
    return load8(p, 0);
}

fn f_const_size() -> u32 {
    let p: ptr = alloc(SZ);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_derived() -> u32 {
    let p: ptr = alloc(64);
    store8(ptr_add(p, 4), 0, 7);
    return load8(p, 0);
}

fn f_hex() -> u32 {
    let p: ptr = alloc(0x40);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_round() -> u32 {
    let p: ptr = alloc(20);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_freed() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    let v: u32 = load8(p, 0);
    free(p);
    return v;
}

fn f_atomic() -> u32 {
    let p: ptr = alloc(8);
    store8(p, 0, 1);
    atomic_add(p, 1);
    return load8(p, 0);
}

fn f_iflet(o: E) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    let r: u32 = load8(p, 0);
    if let E::A(p) = o {
        r = r + p;
    }
    return r;
}

fn f_partial(x: ptr) -> u32 {
    let p: ptr = alloc(64);
    let q: ptr = alloc(32);
    store8(p, 0, 7);
    store8(q, 0, 9);
    return load8(p, 0) + consume(q) + load8(x, 0);
}
"""

L0_ALPHA_SRC = L0_PAIR_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
L0_MANUAL_SRC = L0_PAIR_SRC.replace("@@CHOOSE@@", "choose gc_manual")

#: 每个函数的**提升表**（不进表 = 一个都不提升）。每一格的"为什么"对着源码里的那一行：
#: 出块的、重新赋值的、返回的、尺寸不是字面量的、被遮蔽的、派生指针的、`free` 过的、
#: `if let` 绑定同名的，全在"一个都不提升"那一栏 —— 而 `f_hex`（十六进制仍是字面量）、
#: `f_round`（20 -> 3×i64）、`f_atomic`（第三个只解引用的内建）在提升那一栏。
L0_WANT = {
    "f_pos": {"p": 64},
    "f_alias": {"p": 64},
    "f_assign": {},
    "f_ret": {},
    "f_dyn": {},
    "f_shadow": {},
    "f_const_size": {},
    "f_derived": {},
    "f_hex": {"p": 64},
    "f_round": {"p": 20},
    "f_freed": {},
    "f_atomic": {"p": 8},
    "f_iflet": {},
    "f_partial": {"p": 64, "q": 32},
}

#: 那一对程序里的 `alloc` 站点总数、以及 alpha 档**留下不动的**那几个 —— 两个数都写死，
#: 于是"少提升一格"（alpha 数变大）与"多提升一格"（alpha 数变小）都会红。
#: **跨函数 deref-only 放宽之后**（`docs/210` §7）：`consume`（只把首参透给 `load8`）成了安全
#: 的被调 —— 于是 `f_alias` 的 `p`、`f_partial` 的 `q` 也提走了（站点 5 → 7，留下 11 → 9）。
L0_ALLOC_SITES = 16
L0_ALPHA_ALLOC_LEFT = 9


def _l0_emit(src: str) -> tuple[dict[str, dict[str, int]], str]:
    """编译一份源，回 (每个函数的提升表, IR)。"""
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    table = {f.name: f.l0 for f in mod.funcs if f.l0}
    return table, lomentc.emit_llvm(mod, ROOT)


@test
def test_array_index_ir_is_valid_llvm():
    """数组/切片**下标**的 GEP 必须用下标**自己的类型** —— 写死 `i32` 会发出**不合法的 LLVM IR**
    （`i32 %<i64 值>`，见 `docs/212` §4）。而 `lomelf` 是按 GEP 里**写的**类型去读下标的
    （`tools/lomelf.py:799`），于是**悄悄截成 32 位**照跑 —— 所以这个 bug 一直没露；clang 不宽容，当场拒。

    两条判据互补（**光有文本不够**：文本判据抓不到语义）：

      * **文本**：`a[i]`（`i: u64`）的 GEP 里是 `i64 %`；`a[2] * 5` 那个算式是 `mul i64`
        （**不是** `mul [4 x i64]` —— 那是孪生曾经发的形状，见 `loment_p8_test::test_m82` 里
        `ir_index.lomt` 那条逐字节判据）；
      * **语义**：真 `clang` 收得下（有 clang 才算，没有就 SKIP —— 与仓里其它 clang 判据同形）。
    """
    import shutil
    import subprocess

    src = ("module m\nfn _start() {\n"
           "    let a: [u64; 4] = [1, 2, 3, 4];\n"
           "    let i: u64 = 1;\n"
           "    let x: u64 = a[i] * 3 + a[2];\n"
           "    a[i] = x;\n"
           "    syscall4(60, a[i] % 256, 0, 0);\n"
           "}\n")
    ir = lomentc.emit_llvm(parse(src), ROOT)
    assert "i32 0, i64 %" in ir, f"动态下标没按 i64 发:\n{ir[:500]}"
    assert "mul i64 %" in ir, f"算式里 `a[i]` 的类型不是元素 i64:\n{ir[:500]}"
    assert "mul [4 x i64]" not in ir, "算式里 `a[i]` 用成了**数组**类型（同 `docs/212` §4）"
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not clang:
        print("      SKIP 语义那一半：无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        ll = Path(td) / "m.ll"
        ll.write_text(ir, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                            "-ffreestanding", "-O2", "-c", str(ll), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"clang 拒了 IR（类型不合法）:\n{r.stderr[:400]}"


@test
def test_l0_rule_pair():
    """`gc_auto_alpha` 的 **L0**：一对只差 `choose` 一行的程序，逐格钉住提升表（`docs/210` §2）。

    **这一条同时是"L0 真的发生"和"L0 没被夸大"**（`docs/210` §5 的第二、三行）：
    逐格的表钉住"哪一格该提、哪一格不该提"，而 `alloc` 调用点的两个数钉住"提了就要少一次
    调用" —— **把提升关掉而保留计数**（表还在、IR 里却还是 `call @__loment_alloc`）会让
    alpha 那一格涨到 16，红；**把计数改成 0** 而实际照提会让 `L0_ALPHA_ALLOC_LEFT` 对不上，
    也红。两个方向都有钩子，所以这不是一条"顺手报个绿"的判据。
    """
    alpha_tbl, alpha_ll = _l0_emit(L0_ALPHA_SRC)
    manual_tbl, manual_ll = _l0_emit(L0_MANUAL_SRC)

    assert alpha_tbl == {k: v for k, v in L0_WANT.items() if v}, f"alpha 提升表: {alpha_tbl}"
    assert manual_tbl == {}, f"`gc_manual` 下不许有任何提升，得到 {manual_tbl}"

    a_calls = alpha_ll.count("call ptr @__loment_alloc")
    m_calls = manual_ll.count("call ptr @__loment_alloc")
    assert m_calls == L0_ALLOC_SITES, f"`gc_manual` 的分配点应恒为 {L0_ALLOC_SITES}，得到 {m_calls}"
    assert a_calls == L0_ALPHA_ALLOC_LEFT, f"alpha 的分配点应为 {L0_ALPHA_ALLOC_LEFT}，得到 {a_calls}"
    # 两个数都非平凡：不然"全都提升"与"一个都不提升"都能让上面两条同时成立
    assert 0 < L0_ALPHA_ALLOC_LEFT < L0_ALLOC_SITES, "这一对里两种走向都要有"
    # 缓冲的 `[... x i64]` 计数钉住"尺寸取整"没被改坏。每格现在有 **2 + 字数** 处：
    # `alloca`、那条 `store ptr %N.buf`，以及**根登记的每一个字**
    # （`FujoJTOP/loment#362`：提升缓冲的每个 8 字节字各占一格根表）。
    #
    # **`f_partial` 是 L0.5 那一格**（`docs/210` §2.10）：它有两处静态站点，于是不各发
    # 一条缓冲，而是共用 `%l05.buf`（64 + 32 = 96 B = 12 字）。参照数 = `alloca` 一条 +
    # 静态站点两条（`getelementptr ... %l05.buf`）+ 根登记 12 字 —— 15 处；对应地，
    # 那两格的 L0 参照（`2 + 8` 与 `2 + 4`）不再出现。
    want_buf = sum(2 + (sz + 7) // 8
                   for per in L0_WANT.values() for sz in per.values())
    want_buf += 15 - (2 + 8) - (2 + 4)
    assert alpha_ll.count(".buf") == want_buf, alpha_ll.count(".buf")
    assert alpha_ll.count("%l05.buf = alloca [12 x i64]") == 1, alpha_ll.count("%l05.buf")
    assert ".buf" not in manual_ll
    # 两份源**只差那一行** —— 不然"差别来自档位"这个说法就不成立
    assert L0_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        L0_MANUAL_SRC.replace("choose gc_manual", "X")



#: `docs/246` §5.3 那一刀（"系统调用的实参是**借**"）的三份源 —— **只差一行**。
_SYSCALL_BORROW_SRC = """module sysborrow
choose gc_auto_alpha
choose runtime

fn put(fd: u64, v: u32, nr: u64) {
    let buf: ptr = alloc(16);
    store8(buf, 0, v as u8);
    store8(buf, 1, 0);
    syscall4(@@NR@@, fd, buf as u64, 2);
}

fn _start() {
    put(1, 65, 1);
    syscall4(60, 0, 0, 0);
}
"""


@test
def test_syscall_borrow_rule():
    """**交给白名单里的系统调用是"借"**（`docs/246` §5.3）：那一格进 L0，**表外的照旧不进**。

    **Why**：这一刀是"让 L0–L2 真的有事可干"里唯一能动语料的一刀（`tools/loment_gc_surface.py`：
    证明面 4/23 → **8/23**），而它的实质是**一条语言对内核的断言**（"这批系统调用在调用期间就把
    缓冲区消费掉、不留存用户指针"）—— 断言错了就是**悬垂指针**。所以判据必须**三个方向都钉**：

      1. 表内（`write` = 1）⇒ 提升（否则这一刀空转）；
      2. **表外**（`sendto` = 44，故意排除：带 `MSG_ZEROCOPY` 时内核持有那些页）⇒ **不**提升 ——
         这一格就是"表本身"的守卫：谁把 44 加进白名单，判据立刻红；
      3. 号**不是字面量**（`nr` 是参数）⇒ **不**提升 —— 不知道是哪个系统调用，就不许当借。

    三份源**只差那一行**（下面最后一条断言钉着），所以"差别来自这一行"这个说法成立。
    """
    tbl_in, _ = _l0_emit(_SYSCALL_BORROW_SRC.replace("@@NR@@", "1"))
    tbl_out, _ = _l0_emit(_SYSCALL_BORROW_SRC.replace("@@NR@@", "44"))
    tbl_var, _ = _l0_emit(_SYSCALL_BORROW_SRC.replace("@@NR@@", "nr"))

    assert tbl_in == {"put": {"buf": 16}}, f"表内的号应当让这一格进 L0，得到 {tbl_in}"
    assert tbl_out == {}, f"表外的号不许进 L0，得到 {tbl_out}"
    assert tbl_var == {}, f"号不是字面量时不许进 L0，得到 {tbl_var}"
    # 三份源只差那一行
    a = _SYSCALL_BORROW_SRC.replace("@@NR@@", "X")
    assert a.count("X") == 1 and a.replace("X", "1") == _SYSCALL_BORROW_SRC.replace("@@NR@@", "1")

#: `gc_ladder` 那一对（`docs/210` §3/§5）：一份 `gc_auto_alpha` 的源里四层**各来一站** ——
#: `a` 是字面量尺寸（L0）、`b` 尺寸是变量且顶层（L1）、`c` 在循环体里（L2）、`d` 外逃（L3）。
GC_LADDER_ALPHA_SRC = """module gc_ladder
choose gc_auto_alpha
choose runtime

fn main() -> i32 {
    let a: ptr = alloc(64);
    store8(a, 0, 9);
    let n: u32 = 8;
    let b: ptr = alloc(n);
    store8(b, 0, 3);
    let i: i32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 1);
        i = i + 1;
    }
    let d: ptr = alloc(n);
    let e: ptr = d;
    store8(e, 0, 1);
    return (load8(a, 0) as i32) + (load8(b, 0) as i32) + (load8(e, 0) as i32);
}
"""
GC_LADDER_MANUAL_SRC = GC_LADDER_ALPHA_SRC.replace("choose gc_auto_alpha", "choose gc_manual")


@test
def test_gc_ladder_rule():
    """`gc_ladder`（`docs/210` §3/§5, Potato v10）：一对只差 `choose` 一行的程序，逐格钉住四层。

    **这一格的全部价值是那条自洽**（`docs/210` §5 第一行）：`l0 + l1 + l2 + l3 == total_sites`。
    校验器**读不到源码**，所以它判不了"这几个数数得对不对"，但判得了"它们自相矛盾" ——
    于是这四个数必须**真的**把 `alloc` 站点分完，而不是编译器顺手写几个好看的数字。

    四格**各自**钉住，也是为了挡住"随手报个绿"：只钉和的话，`{0,0,0,4}` 与 `{1,1,1,1}`
    都能让和成立。**非 alpha 档三个数恒为 0** —— 与"默认档一行都不动"（`docs/175` §3.4）
    是同一条事实（参考侧那三张表只在 alpha 那一趟填）。
    """
    a = json.loads(lomentc.emit_potato(parse(GC_LADDER_ALPHA_SRC), ROOT))["gc_ladder"]
    m = json.loads(lomentc.emit_potato(parse(GC_LADDER_MANUAL_SRC), ROOT))["gc_ladder"]
    assert a == {"l0": 1, "l0_5": 0, "l1": 1, "l2": 1, "l3": 1,
                 "total_sites": 4}, f"alpha: {a}"
    assert m == {"l0": 0, "l0_5": 0, "l1": 0, "l2": 0, "l3": 4,
                 "total_sites": 4}, f"manual: {m}"
    for d in (a, m):
        assert (d["l0"] + d["l0_5"] + d["l1"] + d["l2"] + d["l3"]
                == d["total_sites"]), d
    # 两份源**只差那一行**
    assert GC_LADDER_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        GC_LADDER_MANUAL_SRC.replace("choose gc_manual", "X")


#: L0.5（`docs/210` §2.10）那一对：一个函数两处 L0 站点（成组 ⇒ 开区），另一个只有一处
#: （不成组 ⇒ 留在 L0）。**只差函数形状**，所以差别只可能来自分组规则。
L05_SRC = """module l05

choose gc_auto_alpha
choose runtime

fn two() -> u32 {
    let a: ptr = alloc(16);
    let b: ptr = alloc(24);
    store8(a, 0, 1);
    store8(b, 0, 2);
    return load8(a, 0) + load8(b, 0);
}

fn one() -> u32 {
    let p: ptr = alloc(16);
    store8(p, 0, 3);
    return load8(p, 0);
}
"""


@test
def test_l0_5_region_rule():
    """L0.5：**两处**站点共用一条缓冲，**一处**留在 L0（`docs/210` §2.10）。

    这条钉三件事，缺一条都能让"分组"悄悄退化成"全都分组"或"全都不分组"：

      * `gc_ladder` 里 `two` 的两处算 `l0_5`、`one` 的一处算 `l0`；
      * IR 里 `%l05.buf` 只有 **一条** `alloca`（40 B = 16 + 24 对齐后），且两处站点的
        偏移是 **0 与 2**（不是各自一条 `%a.buf` / `%b.buf`）；
      * `one` 里仍然是 `%p.buf` —— **少于两处不开区**。
    """
    doc = json.loads(lomentc.emit_potato(parse(L05_SRC), ROOT))
    gl = doc["gc_ladder"]
    assert doc["potato"] == potato.VERSIONS[-1], doc["potato"]
    assert gl == {"l0": 1, "l0_5": 2, "l1": 0, "l2": 0, "l3": 0,
                  "total_sites": 3}, gl
    ir = lomentc.emit_llvm(parse(L05_SRC), ROOT)
    assert ir.count("%l05.buf = alloca [5 x i64]") == 1, ir.count("%l05.buf")
    assert "%a.buf" not in ir and "%b.buf" not in ir, "区内站点不该再有各自的缓冲"
    assert "%p.buf = alloca [2 x i64]" in ir, "单站点函数必须留在 L0"
    assert "ptr %l05.buf, i32 0, i32 0" in ir and "ptr %l05.buf, i32 0, i32 2" in ir, ir


#: 块内精确（`docs/210` §2.9）那张真值表 —— 逐格摆开**什么形状精确、掩码是哪几位**。
#: 站点一律是 `let NAME: ptr = alloc(...)`；`a`/`ae` 那种靠**别名**（`let ae: ptr = a;`）
#: 走到 L3（`_ptr_only_uses` 过得去的是 L0/L1，不要掩码）。
SLOT_TABLE_SRC = """module slottbl
choose gc_auto_alpha
choose runtime

fn call_it(p: ptr, v: u64) {
    store8(p, 8, (v & 255) as u8);
}

fn _start() {
    let n: u32 = 8;
    let a: ptr = alloc(n);
    let ae: ptr = a;
    store8(ae, 0, 1);
    let ra: u32 = load8(ae, 0);
    let b: ptr = alloc(n);
    let be: ptr = b;
    let v: u64 = 3;
    store8(be, 8, (v & 255) as u8);
    let c: ptr = alloc(n);
    call_it(c, 5);
    let d: ptr = alloc(n);
    let de: ptr = d;
    let off: u32 = 4;
    store8(de, off, 1);
    syscall4(60, (ra + (v as u32)) as u64, 0, 0);
}
"""


@test
def test_slot_table_rule():
    """**块内槽表**（`docs/210` §2.9）：什么形状算精确、掩码是哪几位，逐格钉住。

    **Why**：这一格的"对"与"错"几乎**看不见** —— 精确与保守扫出来的字在绝大多数程序里
    是同一批（保守那条按**值**判"长得像不像地址"，而能被证成非指针的值本来就不会长得像
    地址，见 `docs/210` §4.2 那段"行为上只在陈旧字节那个角落看得见差别"）。所以它只能靠
    **逐格的真值表**证伪，判据本身必须把每一格点名。

    **How to apply**：四个站点四格，各对着规则的一条：

      * ① 直接 `store8`、值是字面量 -> **精确且掩码 0**（一个指针位都没有）；
      * ② 同一个字收的是**非字面量** -> **精确但置位**（"可能是指针"就扫它）；
      * ③ 块交给用户函数写 -> **不精确**（编译器看不进去，退回保守）；
      * ④ `store8` 的偏移是变量 -> **不精确**（认不出写的是哪个字）。

    再加两条钉子：只有**精确站点**改写块头（保守站点一个字节都不动），而掩码是
    **编在块头那个字里**的（`-(mask + 2)`）—— 它**不进任何表**：掩码本来就是每个站点一个
    常数，头上那个 32 位字装得下。少了这条钉子的后果是"掩码算了但没写下去"，
    收集器于是每一块都按保守扫，这一格静默地什么都不做。
    """
    m = parse(SLOT_TABLE_SRC)
    slots = getattr(m.funcs[1], "slots", {})
    assert slots == {"a": 0, "b": 2}, f"精确站点掩码: {slots}"
    ll = lomentc.emit_llvm(m, ROOT)
    # 借出标记：`-(mask + 2)` —— a（掩码 0）是 `-2`、b（掩码 0b10）是 `-4`；保守站点还是 `-1`
    assert ll.count("store i32 -2, ptr") == 1 and ll.count("store i32 -4, ptr") == 1, \
        "精确站点的借出标记没带上掩码"
    assert ll.count("store i32 -1, ptr") >= 2, "保守站点的借出标记不该动"
    assert "@__loment_slots" not in ll, "掩码不该再进任何表"


#: `surfaces`（`docs/222`, v11）：**每一个面至少两站**，外加两处"归 `other`"的形态
#: —— 号不在表里的（`9999`）与**首参不是字面量**的（`n1`）。数够少，所以每个格子都能
#: 逐个点名比对，而不是"和对了就行"。
SURFACES_SRC = """module surf
choose runtime

fn n() -> i64 {
    let n1: u64 = 41;
    let a: i64 = syscall4(0, 1, 0, 0);
    let b: i64 = syscall4(1, 1, 0, 0);
    let c: i64 = syscall6(41, 0, 0, 0, 0, 0);
    let d: i64 = syscall7(288, 0, 0, 0, 0, 0, 0);
    let e: i64 = syscall4(60, 0, 0, 0);
    let f: i64 = syscall4(12, 4096, 0, 0);
    let g: i64 = syscall4(9999, 0, 0, 0);
    let h: i64 = syscall4(n1, 0, 0, 0);
    return a + b + c + d + e + f + g + h;
}
"""


@test
def test_surfaces_rule():
    """`surfaces`（`docs/222`, Potato v11）：机调用站点按"面"分一分，**逐格**钉住。

    与 `gc_ladder` 那条同一个形状，但多一条**跨字段**的自洽：`total_sites` 必须同时等于
    各面之和**和** `boundary.syscalls`（每个机调用站点落在恰好一个面里 ⇒ 两处数的是同一批
    站点）。所以这里两处都断言 —— 只钉一边的话，`surfaces` 自己内部自洽、却整批漏数站点
    （或整批多数）照样能过。

    **两处"归 `other`"的形态各来一站**（号不在表里 / 首参不是字面量）：`other` 是这张表的
    兜底，兜底塌了就等于"凡是没登记的号静默消失"，而那正是这份计数要防的事。
    """
    doc = json.loads(lomentc.emit_potato(parse(SURFACES_SRC), ROOT))
    sf = doc["surfaces"]
    assert sf == {"file": 2, "mem": 1, "net": 2, "other": 2, "proc": 1, "total_sites": 8}, sf
    assert doc["boundary"]["syscalls"] == sf["total_sites"], (doc["boundary"], sf)
    # 两处都非平凡：不然"全归 `other`"或"什么都数不到"也能让上面那条成立
    assert sf["other"] < sf["total_sites"], sf
    # 空程序也得有这一项（**必填**，与 `boundary`/`gc_ladder` 同一条纪律）
    empty = json.loads(lomentc.emit_potato(parse("module e\n"), ROOT))
    assert empty["surfaces"] == {"total_sites": 0}, empty["surfaces"]
    assert empty["boundary"]["syscalls"] == 0, empty["boundary"]


#: `net_ladder` 那一条用的源：**四档各来一站**，再加一处"号是变量"。
NET_LADDER_SRC = """module nl
choose runtime

fn n() -> i64 {
    let v: u64 = 41;
    let a: i64 = syscall4(53, 0, 0, 0);
    let b: i64 = syscall4(41, 0, 0, 0);
    let c: i64 = syscall4(42, 0, 0, 0);
    let d: i64 = syscall4(7, 0, 0, 0);
    let e: i64 = syscall4(291, 0, 0, 0);
    let f: i64 = syscall4(49, 0, 0, 0);
    let g: i64 = syscall4(v, 0, 0, 0);
    return a + b + c + d + e + f + g;
}
"""


@test
def test_net_ladder_rule():
    """`net_ladder`（`docs/237`, Potato v14）：联网站点按**宽度档**分一分，**逐档**钉住。

    与 `gc_ladder` 同形（四档 + 总数、各档之和 == 总数），**多一条跨字段**：`total_net`
    必须等于 `surfaces.net` —— 两处数的是**同一批站点**，只是分的粗细不同。所以这里两处
    都断言：只钉一边的话，"梯子上少了一档"或"面上多算了几个"照样能过。

    **`total_net` 与 `total_sites` 刻意不相等**（6 vs 7）：那个"号是变量"的站点落在
    `surfaces.other` 里，**不在**梯子上 —— 梯子只回答"联网的那部分按宽度怎么分"。
    把两者混起来（比如拿 `total_net` 去对 `boundary.syscalls`）就是错的，这条判据钉住它。
    """
    doc = json.loads(lomentc.emit_potato(parse(NET_LADDER_SRC), ROOT))
    nl = doc["net_ladder"]
    assert nl == {"w0": 1, "w1": 2, "w2": 2, "w3": 1, "total_net": 6}, nl
    assert doc["surfaces"] == {"net": 6, "other": 1, "total_sites": 7}, doc["surfaces"]
    assert nl["total_net"] == doc["surfaces"]["net"], (nl, doc["surfaces"])
    assert nl["total_net"] != doc["surfaces"]["total_sites"], (nl, doc["surfaces"])
    assert doc["boundary"]["syscalls"] == 7, doc["boundary"]
    # 空程序也得有这一项、而且**四档恒在**（必填，与 `gc_ladder` 同一条纪律：
    # 键的集合固定，读者与校验器都不用猜）。
    empty = json.loads(lomentc.emit_potato(parse("module e\n"), ROOT))
    assert empty["net_ladder"] == {"w0": 0, "w1": 0, "w2": 0, "w3": 0, "total_net": 0}, \
        empty["net_ladder"]


@test
def test_net_width_rung_table_covers_the_net_numbers():
    """**分档表与面表同集**（`docs/237`）：联网号**恰好**有一档，非联网号**一档也没有**。

    这是这张表唯一的自洽判据 —— 它没有 `SURFACE_NUMBERS` 那条"与运行时逐条判等"
    （那张表抄的是 `lomelf.PE_SYSCALLS`，是一份**事实**；分档是**策略**，运行时不认宽度）。
    能判的只有两条：少一个号 = 有联网站点没归类（`total_net` 会小于 `surfaces.net`，
    校验器红）；多一个号 = 给不联网的号配了宽度（那些站点在 `surfaces` 里不算 net，
    校验器同样红）。**两条都在对象那一侧独立判得了**，这一条是把它钉在**表**这一层。
    """
    net_nums = {n for n, face in lomentc.SURFACE_NUMBERS.items() if face == "net"}
    assert set(lomentc.NET_WIDTH_RUNGS) == net_nums, \
        (sorted(net_nums - set(lomentc.NET_WIDTH_RUNGS)),
         sorted(set(lomentc.NET_WIDTH_RUNGS) - net_nums))
    assert set(lomentc.NET_WIDTH_RUNGS.values()) == set(lomentc.NET_WIDTH_RUNG_KEYS), \
        sorted(set(lomentc.NET_WIDTH_RUNGS.values()))
    # 四档**都要有号**：空着的那一档在对象里恒为 0，读者分不出"这档没有号"与"这份程序
    # 不用它" —— 那是设计上要不要这四档的问题（`docs/237` §5 记着），不是实现能在这一层
    # 消掉的。这里只钉"每一档至少有一个号"，免得表写成三档、代码里留着第四档。
    used = set(lomentc.NET_WIDTH_RUNGS.values())
    assert used == set(lomentc.NET_WIDTH_RUNG_KEYS), sorted(used)


#: `excluded` 的**行为轴**那一条用的源（`docs/218` §7 第 2 件 / `docs/227` §3）。
EXCLUDED_NET_SRC = """module x

excluded "network: 本单元不申请任何 net 能力"

fn f() -> i64 {
    return syscall6(41, 2, 1, 0, 0, 0);
}
"""


@test
def test_excluded_network_is_a_behaviour_claim():
    """`excluded "network"` × 联网号：**声明说不开的门，代码里开了**（`docs/218` §7 第 2 件）。

    M41 那条（`能力 X 使用了 excluded 的空间`）比的是**名字** —— `excluded "usb"` 对
    `capability … : usb[…]`，它**从不看行为**，所以"声明了不碰网络、代码里却在调 `socket`"
    是它看不见的。这一条钉的就是补上的那半。

    **它只回答"开了没有"**（**权威**读法），不回答"外发的字节里有没有本域的数据"
    （那是 `docs/208` 的**保密**读法，由 M41 那一族管）—— `docs/218` §8 最后两行点名的
    "两轴不能混、不能拿一条冒充两条"就落在这里：两条判据各判各的。
    """
    assert errs(EXCLUDED_NET_SRC) == ["6: 机调用号 41 使用了 excluded 的空间 network"], \
        errs(EXCLUDED_NET_SRC)
    # **别的空间不管这件事** —— 那正是 M41 那一半（名字），不是这一条（行为）
    assert errs(EXCLUDED_NET_SRC.replace('excluded "network:', 'excluded "usb:')) == [], \
        "`usb` 的空间不该被联网号触发"
    # **别的面也不管** —— `exit`(60) 是 `proc`，不是 `net`
    assert errs(EXCLUDED_NET_SRC.replace("syscall6(41, 2, 1, 0, 0, 0)",
                                         "syscall4(60, 0, 0, 0)")) == [], \
        "非联网面不该被这条判据碰到"
    # 面名两个写法都认（`net` 是**面**的叫法、`network` 是**空间**的叫法）
    spelled = EXCLUDED_NET_SRC.replace('"network:', '"net:')
    assert errs(spelled) == ["6: 机调用号 41 使用了 excluded 的空间 net"], errs(spelled)
    # **号写成变量就什么都不说** —— 词法口径的诚实边界（与 `surfaces` 落 `other` 同一条）
    var = ('module x\n\nexcluded "network: 本单元不申请任何 net 能力"\n\n'
           "fn f(n1: u64) -> i64 {\n    return syscall6(n1, 2, 1, 0, 0, 0);\n}\n")
    assert errs(var) == [], errs(var)


#: 逐形参安全位的真值源（`docs/210` §2.2.1）：第一实参（回归）、两跳后的第二实参（放宽）、
#: 以及一个外逃的被调（阴性对照）。两份只差 `choose` 那一行。
GC_ARGPOS_ALPHA_SRC = """module gc_argpos
choose gc_auto_alpha
choose runtime

fn first(p: ptr) -> u32 {
    return load8(p, 0) as u32;
}

fn peek(off: u32, p: ptr) -> u32 {
    return load8(p, off) as u32;
}

fn peek2(off: u32, p: ptr) -> u32 {
    return peek(off, p);
}

fn escape(p: ptr) -> ptr {
    return ptr_add(p, 1);
}

fn main() -> i32 {
    let n: u32 = 8;
    let a: ptr = alloc(n);
    let b: ptr = alloc(n);
    store8(a, 0, 5);
    store8(b, 0, 6);
    let x: u32 = first(a);
    let y: u32 = peek2(0, b);
    let c: ptr = alloc(n);
    let d: ptr = escape(c);
    store8(d, 0, 1);
    return (x as i32) + (y as i32) + (load8(d, 0) as i32);
}
"""
GC_ARGPOS_MANUAL_SRC = GC_ARGPOS_ALPHA_SRC.replace("choose gc_auto_alpha", "choose gc_manual")


@test
def test_gc_safe_argument_position():
    """**安全位置是逐形参的，不是"只有第一个实参"**（`docs/210` §2.2.1，2026-10-21）。

    这一格钉的是那次放宽的**两个方向**：

      * `a` 传在 `first` 的**第一个**实参 —— 旧规则也接得住（回归格）；
      * `b` 传在 `peek2` 的**第二个**实参，而 `peek2` 又把它传在 `peek` 的第二个实参 ——
        **两跳**的逐形参不动点。旧规则下这两个站点都在 L3；std 库里 82.9% 的残差正是这一格；
      * `c` 交给 `escape`（返回 `ptr_add(p, 1)`）—— 形参外逃，**必须仍留在 L3**。
        这一格是**阴性对照**：规则一放松就把它也提走，那是 use-after-free。

    实测（`loment_gc_surface` 的口径）std 库 2,948 个站点：放开前 33.7% 进 L0–L2，
    放开后 89.1%。所以这不是一格边角。
    """
    a = json.loads(lomentc.emit_potato(parse(GC_ARGPOS_ALPHA_SRC), ROOT))["gc_ladder"]
    m = json.loads(lomentc.emit_potato(parse(GC_ARGPOS_MANUAL_SRC), ROOT))["gc_ladder"]
    assert a == {"l0": 0, "l0_5": 0, "l1": 2, "l2": 0, "l3": 1,
                 "total_sites": 3}, f"alpha: {a}"
    assert m == {"l0": 0, "l0_5": 0, "l1": 0, "l2": 0, "l3": 3,
                 "total_sites": 3}, f"manual: {m}"
    ir = lomentc.emit_llvm(parse(GC_ARGPOS_ALPHA_SRC), ROOT)
    ir_m = lomentc.emit_llvm(parse(GC_ARGPOS_MANUAL_SRC), ROOT)
    # alpha 的产物里还有收集器自己的清扫路径也会调 `__loment_free`，所以不钉绝对数；
    # 钉的是"默认档一个都没有、alpha 至少多出 a 与 b 两条"。
    assert "call void @__loment_free(" not in ir_m, "默认档一行都不许动"
    assert ir.count("call void @__loment_free(") >= 2,         "两个安全的块都该在最后一次用处之后被还掉（第一个实参 + 第二跳的第二实参）"


@test
def test_l0_param_shadow_is_not_promoted():
    """形参同名的那条 `let` **不提升** —— 它买的是"不会发出没有定义的 `%NAME.buf`"。

    `_collect_locals` 把与形参同名局部**排除**在 alloca 之外（`seen = params`），所以那条
    `let p` 写的是**形参的槽**；若把它提升，`%p.buf` 这条 alloca 根本不会被发射，
    IR 里就出现一个未定义的值。这条判据只钉参考侧（自举侧在这个形状上本来就与参考不一致 ——
    `collect_locals` 不排形参，是**另一条既有的**缺口，不归 L0 管）。
    """
    src = ("module m\nchoose gc_auto_alpha\nchoose runtime\n\n"
           "fn f(p: ptr) -> u32 {\n    let p: ptr = alloc(64);\n"
           "    store8(p, 0, 7);\n    return load8(p, 0);\n}\n")
    tbl, ll = _l0_emit(src)
    assert tbl == {}, f"形参同名不该提升，得到 {tbl}"
    assert ".buf" not in ll


@test
def test_l0_safe_set_is_exactly_the_deref_only_ptr_arg0_builtins():
    """`_L0_SAFE_BUILTINS` 必须**恰好**是"首参是 `ptr` 且只解引用它"的那几个内建。

    这条钉的是 `test_boundary_builtins_are_real_builtins` 自己承认**测不到的那一半**：
    那份判据只管"表里的名字编译器认识"，**新加一个同样危险的内建而没人过问**它抓不住。
    这里反过来：内建表里**每一个**首参为 `ptr` 的内建，都必须落在"算"或"不算"这两张表之一，
    而"不算"那一张要逐条给出理由（见 `tools/lomentc.py` 里那张表）—— **加了新内建就红**。

    **Why**: `load16/32/64`、`store16/32/64` 曾经被误当成安全名单（它们**不是**内建，是
    `loment/examples/bytes.lomt` 里用 `load8`/`store8` 拼的 pub 函数）—— 那会把一个指针
    交给别人的栈帧，而"那个函数不会把 p 存起来"编译器并不知道。
    """
    ptr0 = {b for b, (ps, _) in lomentc.BUILTINS.items() if ps and ps[0] == "ptr"}
    known = lomentc._L0_SAFE_BUILTINS | lomentc._L0_PTR_ARG0_UNSAFE
    assert ptr0 == known, f"首参是 ptr 的内建少了或多了: 表里 {ptr0 ^ known}"
    # 想真被"审"过：安全那一张必须非空且真的只是解引用的那几个
    assert lomentc._L0_SAFE_BUILTINS == {"load8", "store8", "atomic_add"}
    # `load16/32/64`/`store16/32/64` **不许**出现在任何一张里 —— 它们根本不是内建
    for b in ("load16", "load32", "load64", "store16", "store32", "store64"):
        assert b not in lomentc.BUILTINS, f"{b} 怎么成了内建？名单要重判"
        assert b not in known, f"{b} 不是内建，不该在 L0 名单里"


#: L2 判据用的那一份源：**逐字同源，只差 `choose` 那一行**（与 `L0_PAIR_SRC` 同一条纪律）。
#: 每个函数一格 —— 该开纪元的与**不该开**的放在同一份里，"少开一格"与"多开一格"都会被同一条
#: 判据抓住。`loment_p8_test` 的孪生判据**复用这一份**（`import lomentc_test`）。
L2_EPOCH_SRC = """module gc_l2_epoch
@@CHOOSE@@
choose runtime

fn ld(a: ptr, b: u32) -> u32 {
    return load8(a, b);
}

fn f_pos(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        s = s + load8(p, 0);
        i = i + 1;
    }
    return s;
}

fn f_escape(n: u32, keep: ptr) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        keep = p;
        s = s + load8(p, 0);
        i = i + 1;
    }
    return s + load8(keep, 0);
}

fn f_strcat(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let a: str = str_concat("x", "y");
        s = s + str_len(a) + n;
        i = i + 1;
    }
    return s;
}

fn f_for(n: u32) -> u32 {
    let s: u32 = 0;
    for i in 0..100 {
        let p: ptr = alloc(n);
        store8(p, 0, 2);
        s = s + load8(p, 0);
    }
    return s;
}

fn f_free(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        s = s + load8(p, 0);
        free(p);
        i = i + 1;
    }
    return s;
}

fn f_l0() -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(64);
        store8(p, 0, 3);
        s = s + load8(p, 0);
        i = i + 1;
    }
    return s;
}

fn f_inline(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        s = s + load8(alloc(n), 0);
        i = i + 1;
    }
    return s;
}

fn f_call(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        s = s + ld(p, 0);
        i = i + 1;
    }
    return s;
}

fn f_bodyret(n: u32) -> u32 {
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        return load8(p, 0);
    }
    return 0;
}

fn f_rettp(n: u32, c: u32) -> ptr {
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        if i == c {
            return p;
        }
        i = i + 1;
    }
    return 0;
}

fn f_nested(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let q: ptr = alloc(n);
        store8(q, 0, 1);
        let j: u32 = 0;
        while j < 4 {
            let p: ptr = alloc(n);
            store8(p, 0, 2);
            s = s + load8(p, 0);
            j = j + 1;
        }
        s = s + load8(q, 0);
        i = i + 1;
    }
    return s;
}

fn f_two(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 3);
        s = s + load8(p, 0);
        i = i + 1;
    }
    let j: u32 = 0;
    while j < 100 {
        let p: ptr = alloc(n);
        store8(p, 0, 4);
        s = s + load8(p, 0);
        j = j + 1;
    }
    return s;
}

fn f_after(n: u32) -> u32 {
    let q: ptr = alloc(n);
    let s: u32 = load8(q, 0);
    let i: u32 = 0;
    while i < 4 {
        let q: ptr = alloc(n);
        store8(q, 0, 3);
        s = s + load8(q, 0);
        i = i + 1;
    }
    return s;
}

fn f_multi(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let p: ptr = alloc(n);
        let r: ptr = alloc(n);
        store8(p, 0, 3);
        store8(r, 0, 5);
        s = s + load8(p, 0) + load8(r, 0);
        i = i + 1;
    }
    return s;
}

fn f_both(n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 100 {
        let a: ptr = alloc(64);
        let b: ptr = alloc(n);
        store8(a, 0, 1);
        store8(b, 0, 2);
        s = s + load8(a, 0) + load8(b, 0);
        i = i + 1;
    }
    return s;
}
"""

L2_ALPHA_SRC = L2_EPOCH_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
L2_MANUAL_SRC = L2_EPOCH_SRC.replace("@@CHOOSE@@", "choose gc_manual")

#: 每个函数里**按出现顺序**数到的循环，各自的"能不能在末尾批量归还"。每一格的"为什么"：
#: `f_escape` 把指针赋给体外 / `f_strcat` 有 `str_concat` / `f_free` 体内有 `free` /
#: `f_l0` 尺寸是字面量（L0 已经提到栈上，没什么可归还）/ `f_inline` 是认不出来的分配（整格
#: 取消）/ `f_call` 把它交给了用户函数 / `f_rettp` 从体内 `return p`。
#: `f_two` 是**同名循环互相挡**那一格 —— `#364` 之后体之后的扫描**扫到同一层下一次同名
#: `let` 就停**（那之后名字换了主人；语言没有块作用域，`let` 是唯一的重声明形状）。
#: **`#714` 之后第一格变回 `False`**：第二个循环的 `let p` 在**另一个块**里，静态上认不
#: 出那个块跑没跑（`if` 的分支就可能没跑），所以第一个循环**整格取消**；第二个循环体之后
#: 没有同名出现，照旧开。`s_shadow` 那条判据钉的就是这个保守方向。
#: `f_bodyret` 是**终止**那一格：体在 `return` 处结束 —— 仍然开纪元，只是那条路不回退。
#: `f_after` 是**只查体之后**那一格：体之前的同名使用不算逃逸。
L2_WANT = {
    "f_pos": [True],
    "f_escape": [False],
    "f_strcat": [False],
    "f_for": [True],
    "f_free": [False],
    "f_l0": [False],
    "f_inline": [False],
    "f_call": [True],
    "f_bodyret": [True],
    "f_rettp": [False],
    "f_nested": [True, True],
    "f_two": [False, True],
    "f_after": [True],
    "f_multi": [True],
    "f_both": [True],
}


def _l2_flags(src: str) -> dict[str, list[bool]]:
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    out: dict[str, list[bool]] = {}
    for f in mod.funcs:

        def walk(stmts: list, acc: list) -> None:
            for s in stmts:
                if isinstance(s, (lomentc.While, lomentc.For)):
                    acc.append(s.l2)
                    walk(s.body, acc)
                elif isinstance(s, lomentc.If):
                    walk(s.then, acc)
                    walk(s.otherwise, acc)

        acc: list = []
        walk(f.body, acc)
        if acc:
            out[f.name] = acc
    return out


def _l2_if_flags(src: str) -> dict[str, list[tuple[bool, bool]]]:
    """每个函数里每个 `if` 的**两支**各开不开纪元（`docs/210` §2.3 的 `if` 那一格）。

    **与 `_l2_flags` 分开成两把尺子**：那一张是"每个循环一格"，把 `if` 混进去会把已有的
    那些格子全打乱。这一张各管各的，顺序就是源里 `if` 的出现序（含嵌套、含 `else if` 链
    展开出来的那些）。
    """
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    out: dict[str, list[tuple[bool, bool]]] = {}
    for f in mod.funcs:

        def walk(stmts: list, acc: list) -> None:
            for s in stmts:
                if isinstance(s, (lomentc.While, lomentc.For)):
                    walk(s.body, acc)
                elif isinstance(s, lomentc.If):
                    acc.append((s.then_l2, s.else_l2))
                    walk(s.then, acc)
                    walk(s.otherwise, acc)

        acc: list = []
        walk(f.body, acc)
        if acc:
            out[f.name] = acc
    return out


@test
def test_l2_block_epoch_rule():
    """`gc_auto_alpha` 的 **L2**：**编译器自己推断的块纪元** —— 循环体末尾把分配器前沿退回
    去，整段一次性不存在（"回收时间而不是对象"）。

    **判据在这里是"逐格的真值表"**，两个方向都是钩子：哪一格该开而没开（规则太窄）会红，
    哪一格不该开却开了（规则太松 —— 那是**记错账**，会悬空指针）也会红。

    **形式与 `docs/210` 原稿不同，而且更激进**：原稿要求手写 `epoch { … }`（那要动冻结面、
    加关键字）。这里把那条假设**拿掉** —— 纪元边界由编译器从**块结构 + 逃逸**推出来：
    函数体与循环体本来就是块，只要"体内分配的指针一处都不出体"，体末就能整段退回。
    **零新语法**，而且是宣言里那句"编译器和运行时共同预测对象生命周期"的字面形态。

    **规则定义在 token 流上**（与 L0 同一个理由，见 `_l0_promotable`）—— 两个实现共用同一份
    判断，`loment_p8_test::test_m89_l2_epoch_is_byte_identical` 逐字节核。

    **静态这一条只钉"决定"**；"前沿真的退回去了"由 `loment_elf_test` 那条**动态**判据钉
    （同一份程序：`gc_manual` 耗尽、`gc_auto_alpha` 跑通）。
    """
    assert _l2_flags(L2_ALPHA_SRC) == L2_WANT, f"alpha: {_l2_flags(L2_ALPHA_SRC)}"
    manual = _l2_flags(L2_MANUAL_SRC)
    assert set(manual) == set(L2_WANT), f"manual 少了函数的循环: {sorted(set(L2_WANT) - set(manual))}"
    assert all(not any(v) for v in manual.values()), f"`gc_manual` 下不许开纪元: {manual}"
    # 两份源只差那一行
    assert L2_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        L2_MANUAL_SRC.replace("choose gc_manual", "X")


#: `#714`：体**之后**另一个块里的同名 `let` 不能当作"名字换了主人"。
#:
#: 形状就是 issue 里那三只（`p15_shadow0` / `p16_noshadow` / `p17_shadow1`）的静态版：
#: 循环体分配 `c`，体之后有一个**可能没跑**的 `if` 重新声明 `c`，最后那次 `load8(c, 0)`
#: 仍然指循环里那块 ⇒ 前沿**不能**退。旧规则在 `if` 里那个 `let` 处 `break`，把后面
#: 那次出体使用一起跳过去了，于是发出 `store i32 %tN, ptr @__loment_off`，活块被回收。
L2_SHADOW_SRC = """module gc_l2_shadow
choose gc_auto_alpha
choose runtime

fn s_shadow(cond: u32, n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 3);
        s = s + load8(c, 0);
        i = i + 1;
    }
    if cond != 0 {
        let c: ptr = alloc(8);
        store8(c, 0, 9);
        s = s + load8(c, 0);
    }
    return s + load8(c, 0);
}

fn s_noshadow(cond: u32, n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 3);
        s = s + load8(c, 0);
        i = i + 1;
    }
    return s;
}

fn s_shadow_only(cond: u32, n: u32) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 3);
        s = s + load8(c, 0);
        i = i + 1;
    }
    if cond != 0 {
        let c: ptr = alloc(8);
        store8(c, 0, 9);
        s = s + load8(c, 0);
    }
    return s;
}
"""


@test
def test_l2_shadow_in_another_block_cancels_the_epoch():
    """`#714`：体之后**另一个块**里的同名 `let` 结束时末的整格退回（保守），不能用它当
    "名字换主人"的证据 —— 那条分支可能根本没跑。

    三格：
      * `s_shadow` —— issue 的复现形状：`if` 里同名 `let` + 之后仍用循环那块 ⇒ **False**
        （旧规则给出 True，于是活块被回收；这是这条判据的"红"）；
      * `s_shadow_only` —— 同一个 `if`、但体之后没有再用 ⇒ **False**。这是**保守**的代价：
        静态上分不出 `if` 跑没跑，宁可少开一格；
      * `s_noshadow` —— 对照组：去掉那个 `if` ⇒ **True**（规则没有被一刀切死）。
    """
    assert _l2_flags(L2_SHADOW_SRC) == {
        "s_shadow": [False],
        "s_shadow_only": [False],
        "s_noshadow": [True],
    }, _l2_flags(L2_SHADOW_SRC)


#: `if` 的两支开纪元那一格（`docs/210` §2.3 / §7）。**源只有一份** —— 这条真值表判据与
#: 字节一致判据（`loment_p8_test::test_m89_l2_epoch_is_byte_identical`）都读它。
L2_IF_SRC = """module l2if_t
@@CHOOSE@@
choose runtime

fn f_ok(x: u32) -> u32 {
    let n: u32 = 64;
    let s: u32 = 0;
    if x > 0 {
        let p: ptr = alloc(n);
        store8(p, 0, 1);
        s = s + load8(p, 0);
    }
    return s;
}

fn f_escape(x: u32) -> u32 {
    let n: u32 = 64;
    let s: u32 = 0;
    if x > 0 {
        let p: ptr = alloc(n);
        s = s + (p as u64) as u32;
    }
    return s;
}

fn f_both(x: u32) -> u32 {
    let n: u32 = 64;
    let s: u32 = 0;
    if x > 0 {
        let a: ptr = alloc(n);
        store8(a, 0, 1);
        s = s + load8(a, 0);
    } else {
        let b: ptr = alloc(n);
        store8(b, 0, 2);
        s = s + load8(b, 0);
    }
    return s;
}

fn f_l0(x: u32) -> u32 {
    let s: u32 = 0;
    if x > 0 {
        let c: ptr = alloc(64);
        store8(c, 0, 3);
        s = s + load8(c, 0);
    }
    return s;
}

fn f_after(x: u32) -> u32 {
    let n: u32 = 64;
    let q: ptr = alloc(n);
    let s: u32 = 0;
    if x > 0 {
        let d: ptr = alloc(n);
        q = d;
        store8(q, 0, 4);
    }
    return s;
}

fn f_none(x: u32) -> u32 {
    let s: u32 = 0;
    if x > 0 {
        s = s + 1;
    }
    return s;
}
"""
L2_IF_ALPHA_SRC = L2_IF_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
L2_IF_MANUAL_SRC = L2_IF_SRC.replace("@@CHOOSE@@", "choose gc_manual")

#: 每一格 = 一个 `if` 的 `(then_l2, else_l2)`；顺序就是源里的出现序（**没有嵌套**，所以
#: 每个函数正好一格）。逐条说清为什么：
L2_IF_WANT = {
    # 支里分配、名字只当 `store8`/`load8` 的第一个实参、出支之后一处也不出现 ⇒ 开
    "f_ok": [(True, False)],
    # `p as u64` 把指针变出了一个**整数**（它会流到哪儿这里看不见）⇒ 取消
    "f_escape": [(False, False)],
    # **两支各自**判：两支都合格 ⇒ 都开（两支的纪元各退各的，互不影响）
    "f_both": [(True, True)],
    # 尺寸是**字面量** ⇒ 这一格已经被 **L0** 提到栈上了。它在 arena 上不占地方，
    # 既不欠这次归还、也不该挡别人 —— 于是这一支"没有可归还的"（`nlive == 0`）⇒ 不开
    "f_l0": [(False, False)],
    # `d` 被赋给**支外**的 `q`（`q = d`）⇒ 出支了 ⇒ 取消
    "f_after": [(False, False)],
    # 支里一个 `alloc` 都没有 ⇒ 没什么可归还的（规则 1）⇒ 不开；`else` 缺席也记 False
    "f_none": [(False, False)],
}


@test
def test_l2_if_branch_rule():
    """`gc_auto_alpha` 的 **L2 也认 `if` 的两支**（`docs/210` §2.3；§7 原先把它列在"没做"里）。

    **Why**：`if` 的每一支与循环体是**同一个形状的块**，规则也还是那一条（`_l2_ok`），
    只是"体"换成那一支的 token 跨度。少了这一格，**写在分支里的分配**（不在函数体顶层，
    所以 L1 也接不到）就只能等收集器。

    **判据在这里是"逐格的真值表"**，两个方向都是钩子：该开而没开会红（规则太窄），
    不该开却开了也会红（那会**悬空指针**，不是记错账而已）。

    **动态那一半**（"前沿真的退回去了"）在
    `loment_elf_test::test_l2_if_branch_epoch_bounds_the_heap`。
    """
    assert _l2_if_flags(L2_IF_ALPHA_SRC) == L2_IF_WANT, f"alpha: {_l2_if_flags(L2_IF_ALPHA_SRC)}"
    manual = _l2_if_flags(L2_IF_MANUAL_SRC)
    assert set(manual) == set(L2_IF_WANT), "`gc_manual` 下的函数集合该与 alpha 一样"
    assert all(not a and not b for v in manual.values() for a, b in v), \
        f"`gc_manual` 下不许开纪元: {manual}"
    # 两份源只差那一行
    assert L2_IF_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        L2_IF_MANUAL_SRC.replace("choose gc_manual", "X")


#: `gc_auto`（`docs/210` §2.1 的"自动"那一档）那一段。**源只有一份** —— 这条静态判据与
#: 字节一致判据（`loment_p8_test::test_m90_gc_auto_collector_is_byte_identical`）都读它。
#:
#: 挑这几行的用意：`hold` 有**形参** `ptr`、`_start` 有**局部** `ptr`（`a` 与循环里的 `t`），
#: 于是根表那一半在两个函数里都真的发出来 —— 只测一个函数的话，"形参算不算根"这格是空的。
GC_AUTO_SRC = """module gc_auto_t
@@CHOOSE@@
choose runtime

fn hold(p: ptr) -> u32 {
    return load8(p, 0);
}

fn _start() {
    let a: ptr = alloc(8);
    store8(a, 0, 5);
    let i: u32 = 0;
    let s: u32 = 0;
    while i < 64 {
        let t: ptr = alloc(16);
        store8(t, 0, 1);
        s = s + load8(t, 0);
        i = i + 1;
    }
    if hold(a) == 5 {
        syscall4(60, 7, 0, 0);
    }
    syscall4(60, 3, 0, 0);
}
"""
GC_AUTO_AUTO_SRC = GC_AUTO_SRC.replace("@@CHOOSE@@", "choose gc_auto")
GC_AUTO_MANUAL_SRC = GC_AUTO_SRC.replace("@@CHOOSE@@", "choose gc_manual")
GC_AUTO_ALPHA_SRC = GC_AUTO_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")


def _gc_emit(src: str) -> str:
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    return lomentc.emit_llvm(mod, ROOT)


#: 触发挂点该挂在**哪儿**（issue #367）：三份源只差"分配走哪条路"，外加默认档那一格。
_GC_TRIGGER_ALLOC_SRC = """module trigalloc
@@CHOOSE@@
choose runtime

fn _start() {
    let p: ptr = alloc(32);
    store8(p, 0, 1);
    syscall4(60, load8(p, 0) as u64, 0, 0);
}
"""
_GC_TRIGGER_CONCAT_SRC = """module trigconcat
@@CHOOSE@@
choose runtime

fn _start() {
    let s: str = "abcd";
    let t: str = str_concat(s, "efgh");
    syscall4(60, str_len(t) as u64, 0, 0);
}
"""
_GC_TRIGGER_BOTH_SRC = """module trigboth
@@CHOOSE@@
choose runtime

fn _start() {
    let p: ptr = alloc(8);
    store8(p, 0, 1);
    let s: str = "abcd";
    let t: str = str_concat(s, "efgh");
    syscall4(60, (str_len(t) + load8(p, 0)) as u64, 0, 0);
}
"""


@test
def test_gc_trigger_is_attached_where_the_bytes_are_asked_for():
    """触发挂点挂在**真向堆要字节的那一处**（issue #367）—— 而不是挂在它某个调用点上。

    **Why**：触发量的是"这次向堆要了多少字节"，所以报账的地方只能是**真向堆要字节的那一处**
    （参考侧 `alloc_ir` / 自举侧 `emit_alloc_seq`）。原先它挂在 `alloc` 那一支上，而
    `str_concat` 是同一个 `alloc_ir` 的**另一个**调用点 ⇒ 拼接分配的字节从不进计数 ⇒
    **纯拼接的程序一次都不收**，64 KiB 填满就 `abort`。拼接是语言的主字符串操作，所以这
    不是边角：文本处理那类程序在自动档下**从第一次分配起就在超支**。

    **这一半只钉"挂点有几条"**（便宜、不跑程序）。"真的收得回来"是动态那一半的活
    （`loment_elf_test::test_gc_alpha_collects_a_concat_only_program`），两者缺一不可 ——
    挂点齐了而收集器是空函数，这条照样绿。

    **反证**：把触发挪回 `alloc` 那一支，第二、三格立刻变 0（实测）。
    """
    n = "call void @__loment_maybe_collect(i32 "
    alloc_only = _gc_emit(_GC_TRIGGER_ALLOC_SRC.replace("@@CHOOSE@@", "choose gc_auto"))
    concat_only = _gc_emit(_GC_TRIGGER_CONCAT_SRC.replace("@@CHOOSE@@", "choose gc_auto"))
    both = _gc_emit(_GC_TRIGGER_BOTH_SRC.replace("@@CHOOSE@@", "choose gc_auto"))
    manual = _gc_emit(_GC_TRIGGER_BOTH_SRC.replace("@@CHOOSE@@", "choose gc_manual"))
    assert alloc_only.count(n) == 1, (
        f"只走 `alloc` 的源该有 1 条挂点，得到 {alloc_only.count(n)}")
    assert concat_only.count(n) == 1, (
        f"只走 `str_concat` 的源也该有 1 条挂点，得到 {concat_only.count(n)} —— "
        "挂点漏在拼接那条路上，程序会一次都不收，直到 arena 耗尽")
    assert both.count(n) == 2, (
        f"两条分配路各一处，该有 2 条挂点，得到 {both.count(n)}")
    assert manual.count(n) == 0, (
        f"`gc_manual` 不该有挂点（默认档一行都不动），得到 {manual.count(n)} —— "
        "挂点的门跟收集器是同一条 `gc_any`")


@test
def test_gc_auto_collector_text():
    """`choose gc_auto` 的收集器**只在这一档**追加（`docs/210` §2.1）—— 三档的文本。

    **Why**：`gc_auto` 到落地之前是**空承诺** —— 选它编得过，产物里却没有收集器；
    而"有收集器"这件事在静态上就是几条**可数**的痕迹，钉住它们比跑起来便宜得多，
    也能挡住最阴的那种错：收集器发了、**挂点没挂**（产物里有 `__loment_collect`，
    却从来没被调用过 —— 跑起来像没事，直到 arena 耗尽）。

    **How to apply**：这一条只核"痕迹在不在、在几处"。**"真的回收了吗"是动态判据的活**
    （`loment_elf_test::test_gc_auto_collects_while_manual_exhausts`）——
    静态判据对"收集器是空函数"照样绿，两者缺一不可。
    """
    auto = _gc_emit(GC_AUTO_AUTO_SRC)
    manual = _gc_emit(GC_AUTO_MANUAL_SRC)
    alpha = _gc_emit(GC_AUTO_ALPHA_SRC)
    roots = (("根表", "@__loment_roots"), ("根表游标", "@__loment_rootn"))
    coll = (("分配量计数", "@__loment_gcbytes"),
            ("标记位表", "@__loment_mark"),
            ("标记函数", "@__loment_markp"),
            ("收集器", "@__loment_collect"),
            ("触发入口", "@__loment_maybe_collect"))
    # **根表**：`gc_auto` 与 `gc_auto_alpha` **都发** —— 两档只要函数里有指针槽就会往它写
    # （`roots_push`），定义必须在（见 `_IR_ROOTS`）。
    for what, sym in roots:
        assert sym in auto and sym in alpha, f"两档都该有{what}（{sym}）"
        assert sym not in manual, f"`gc_manual` 的产物里不该有{what}（{sym}）—— 默认档不许变"
    # **收集器**：这份源里 alpha 的 `alloc` 全被 L0 提走了（`a`/`t` 都是字面量尺寸）+ `hold`
    # 是 deref-only ⇒ **没有堆**，所以 alpha **不该带收集器**（它引用的是堆那几个全局）。
    # 这一栏就是"档位串了没有"—— alpha 那份只该差根表，不该差收集器。
    for what, sym in coll:
        assert sym in auto, f"`gc_auto` 的产物里没有{what}（{sym}）"
        assert sym not in manual, f"`gc_manual` 的产物里不该有{what}（{sym}）—— 默认档不许变"
        assert sym not in alpha, (f"`gc_auto_alpha` 这份源没堆（alloc 全被 L0 提走），"
                                  f"不该有{what}（{sym}）")
    # **挂点**：每个 `alloc` 站点前面正好一条（本程序两个函数各一处）
    assert auto.count("call void @__loment_maybe_collect(i32 ") == 2, \
        ("触发点该正好两处（每个 `alloc` 站点之前一条），得到 "
         f"{auto.count('call void @__loment_maybe_collect(i32 ')} —— "
         "发了收集器却没挂点，正是这条判据要挡的那种错")
    # **根表登记**：三个指针槽 —— `hold` 的形参 p，`_start` 的 a 与 t。
    # 用 `.addr to i64` 数（`__loment_free` 里也有一条 `ptrtoint ptr %p`，那是参数转型，
    # 按 `ptrtoint ptr %` 数会把它算进来 —— 计数要挑只属于这一件事的形状）。
    # 少了形参那一个，说明"形参也算根"这格是空的。
    assert auto.count(".addr to i64") == 3, \
        f"根表该登记三格（形参 p + 局部 a、t），得到 {auto.count('.addr to i64')}"
    for slot in ("%p.addr to i64", "%a.addr to i64", "%t.addr to i64"):
        assert slot in auto, f"根表漏了 {slot}（形参/局部/循环内局部各一格）"
    # L0/L2 只在 alpha 档 —— 这两条同时挡住"档位串了"的另一半
    assert ".buf" not in auto, "`gc_auto` 不该有 L0 缓冲（L0 只在 alpha 档）"
    assert ".buf" in alpha, "alpha 那一份该有提升的缓冲 —— 那说明档位判据在这儿失效了"


#: `#1465` 的那份源：两个 `alloc` 挤在**同一个表达式**里，第一个的结果只活在 SSA
#: 临时值中。第二个 `alloc` 的钩子若正好在这一刻收，第一个块就被当成垃圾。
_SPILL_SRC = """module gc_spill
@@CHOOSE@@
choose runtime

fn tag(a: ptr) -> ptr {
    store8(a, 0, 7);
    return a;
}

fn check(a: ptr, b: ptr) -> u32 {
    store8(b, 0, 9);
    return load8(a, 0) as u32;
}

fn _start() {
    let sz: u32 = 16;
    let r: u32 = check(tag(alloc(sz)), alloc(sz));
    syscall4(60, r as u64, 0, 0);
}
"""


@test
def test_alloc_results_are_spilled_into_the_root_table():
    """`#1465`：分配结果写进本帧的 spill 槽，槽按具名槽登记进根表。

    根表只认具名槽 —— 表达式里的 SSA 临时值不是根，`f(tag(alloc(sz)), alloc(sz))`
    的第一块活不过第二次分配里的那次收集。每个分配站点一格、**发射序**编号；入口清零，
    分配完立刻落槽。默认档（`gc_manual`）不发槽，产物一个字节都不多。

    动态那一半在 `loment_elf_test::test_a_temporary_allocation_survives_a_collection`：
    这条只核"槽在不在、挂没挂"，对"收集器真的看得见它"照样绿。
    """
    auto = _gc_emit(_SPILL_SRC.replace("@@CHOOSE@@", "choose gc_auto"))
    manual = _gc_emit(_SPILL_SRC.replace("@@CHOOSE@@", "choose gc_manual"))
    assert "%gc.sp.0 = alloca ptr" in auto and "%gc.sp.1 = alloca ptr" in auto, (
        "两个分配站点该各开一个 spill 槽")
    assert "%gc.sp.2" not in auto, "槽按站点数开，不多开"
    for i in (0, 1):
        assert f"store ptr null, ptr %gc.sp.{i}" in auto, f"槽 {i} 没有入口清零"
        assert f"ptrtoint ptr %gc.sp.{i} to i64" in auto, f"槽 {i} 没登记进根表"
        # 入口一次 + 语句边界一次（`#1465`：槽只活一条语句，不活到帧尾）
        assert auto.count(f"store ptr null, ptr %gc.sp.{i}") == 2, (
            f"槽 {i} 的清零次数是 {auto.count(f'store ptr null, ptr %gc.sp.{i}')}，"
            "该是两次（入口 + 语句边界）—— 少的那次就是泄漏")
    # 每个 `alloc` 的**下一条**就是落槽（值在寄存器里活过下一次分配之前必须进槽）。
    lines = auto.splitlines()
    hit = []
    for k, ln in enumerate(lines):
        if ln.strip().startswith("%") and "= call ptr @__loment_alloc(i32 " in ln:
            reg = ln.split("=")[0].strip()
            hit.append((reg, lines[k + 1].strip()))
    assert len(hit) == 2, hit
    for k, (reg, nxt) in enumerate(hit):
        assert nxt == f"store ptr {reg}, ptr %gc.sp.{k}", (
            f"第 {k} 个分配站点没有立刻落槽，下一行是 {nxt!r}")
    assert "gc.sp" not in manual, "`gc_manual` 的产物里不该有 spill 槽 —— 默认档不许变"


#: `gc_auto_alpha` 的**自适应收集器**那一份源（`docs/210` §2.4 的"策略池"那一半）。
#: `chew` 里那块 `p` 是 **L3**（尺寸是变量 ⇒ 不是 L0；`let q: ptr = p` 是外逃 ⇒ 不是 L1），
#: 所以 alpha 这一档**非得有收集器**不可 —— 此前它根本没有（L3 残差没人收，只有分配器）。
GC_ADAPT_SRC = """module gc_adapt
@@CHOOSE@@
choose runtime

fn chew(n: u32) -> u32 {
    let p: ptr = alloc(n);
    let q: ptr = p;
    store8(q, 0, 1);
    return load8(q, 0);
}

fn _start() {
    let sz: u32 = 64;
    let a: ptr = alloc(sz);
    store8(a, 0, 5);
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 64 {
        s = s + chew(16);
        i = i + 1;
    }
    if load8(a, 0) == 5 {
        syscall4(60, 7, 0, 0);
    }
    syscall4(60, 3, 0, 0);
}
"""
GC_ADAPT_AUTO_SRC = GC_ADAPT_SRC.replace("@@CHOOSE@@", "choose gc_auto")
GC_ADAPT_ALPHA_SRC = GC_ADAPT_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
GC_ADAPT_MANUAL_SRC = GC_ADAPT_SRC.replace("@@CHOOSE@@", "choose gc_manual")


@test
def test_gc_alpha_adaptive_collector_text():
    """`gc_auto_alpha` 的收集器是**自适应**那一支（`docs/210` §2.4 的"策略池"）。

    **Why**：alpha 此前**根本没有收集器**（L3 残差没人收），与 `gc_auto` 的区别只在阶梯 ——
    这一步给它配上收集器、且触发是**自适应**的（阈值跟活集走 + 空间触发）。静态上钉三件事：

    1. alpha 现在**有**收集器（`__loment_collect` / `maybe_collect` / 根表 / 标记函数）；
    2. 它的触发**是自适应那一支**（`@__loment_thresh` + `@__loment_livebytes`）；
    3. `gc_auto` 那支**碰都不碰**那些符号（档位没串）—— 它仍是固定 32 KiB。

    **动态那一半**（"真的收得掉、而且比固定档收得下更大的活集"）在
    `loment_elf_test::test_gc_alpha_adaptive_bounds_a_large_live_set`。
    """
    auto = _gc_emit(GC_ADAPT_AUTO_SRC)
    alpha = _gc_emit(GC_ADAPT_ALPHA_SRC)
    manual = _gc_emit(GC_ADAPT_MANUAL_SRC)
    shared = ("@__loment_collect", "@__loment_maybe_collect", "@__loment_roots",
              "@__loment_markp", "@__loment_gcbytes")
    adapt = ("@__loment_thresh", "@__loment_livebytes")
    for sym in shared:
        assert sym in auto and sym in alpha, f"两档都该有收集器痕迹：{sym}"
        assert sym not in manual, f"`gc_manual` 不该有收集器痕迹：{sym}"
    for sym in adapt:
        assert sym in alpha, f"alpha 的自适应触发缺 {sym}"
        assert sym not in auto, f"`gc_auto` 不该有自适应那一支（{sym}）—— 档位串了"
    # **分代那一档**（`docs/210` §2.7）：alpha 多一张卡表、一条写屏障、minor/major 两个入口；
    # `gc_auto` **一个都不沾** —— 它老实做全量，也就不该替每一次 `store8` 付那条间接调用。
    # 这一栏钉的是这一格最要紧的那件事：**写屏障是分代成立的前提**（小回收不扫老年代），
    # 少了它，`loment_elf_test::test_gc_alpha_minor_keeps_a_young_block_reachable_from_an_old_one`
    # 立刻红。
    gen = ("@__loment_cards", "@__loment_oldtop", "@__loment_wb",
           "@__loment_minor", "@__loment_major")
    for sym in gen:
        assert sym in alpha, f"alpha 的分代那一档缺 {sym}"
        assert sym not in auto, f"`gc_auto` 不该有分代的痕迹（{sym}）—— 档位串了"
    # **挂点比符号更紧**：本程序两个 `store8`（`chew` 里的 `q`、`_start` 里的 `a`）⇒ alpha
    # 正好两条屏障；符号在而挂点漏了，是**静默地少收一块**（那一块正是判据要抓的）。
    assert alpha.count("call void @__loment_wb(ptr ") == 2, \
        f"alpha 的写屏障该两条（两个 `store8` 站点），得到 {alpha.count('call void @__loment_wb(ptr ')}"
    assert auto.count("call void @__loment_wb(ptr ") == 0, \
        "`gc_auto` 不该挂写屏障 —— 它做全量，那条税买不到东西"
    # **挂点**：两档各挂两条（本程序两个 `alloc` 站点 —— `chew` 的 `p`、`_start` 的 `a`）
    assert auto.count("call void @__loment_maybe_collect(i32 ") == 2, \
        f"`gc_auto` 挂点该两条，得到 {auto.count('call void @__loment_maybe_collect(i32 ')}"
    assert alpha.count("call void @__loment_maybe_collect(i32 ") == 2, \
        f"alpha 挂点该两条，得到 {alpha.count('call void @__loment_maybe_collect(i32 ')}"


#: `gc_auto_alpha` 的 **L1（定活）** 那一份源。**源只有一份** —— 真值表判据与字节一致判据
#: （`loment_p8_test::test_m91_l1_definite_lifetime_is_byte_identical`）都读它。
#:
#: 每一格对着 `_l1_place` 的一条边界（那一格为什么这样，见 `L1_WANT` 里逐条的注释）。
L1_SRC = """module gc_l1
@@CHOOSE@@
choose runtime

fn f_ret() -> u32 {
    let n: u32 = 64;
    let p: ptr = alloc(n);
    store8(p, 0, 1);
    return load8(p, 0);
}

fn f_end() {
    let n: u32 = 64;
    let p: ptr = alloc(n);
    store8(p, 0, 1);
    syscall4(60, load8(p, 0) as u64, 0, 0);
}

fn f_loop() {
    let n: u32 = 64;
    let p: ptr = alloc(n);
    let i: u32 = 0;
    let s: u32 = 0;
    while i < 4 {
        store8(p, 0, 1);
        s = s + load8(p, 0);
        i = i + 1;
    }
    syscall4(60, s as u64, 0, 0);
}

fn f_nest() -> u32 {
    let n: u32 = 64;
    let p: ptr = alloc(n);
    let i: u32 = 0;
    let j: u32 = 0;
    let s: u32 = 0;
    while i < 4 {
        j = 0;
        while j < 4 {
            s = s + load8(p, 0);
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}

fn f_two() -> u32 {
    let n: u32 = 32;
    let a: ptr = alloc(n);
    let b: ptr = alloc(n);
    store8(a, 0, 1);
    store8(b, 0, 2);
    return load8(a, 0) + load8(b, 0);
}

fn f_early() -> u32 {
    let n: u32 = 8;
    let p: ptr = alloc(n);
    store8(p, 0, 1);
    if load8(p, 0) == 0 {
        return 9;
    }
    syscall4(60, 1, 0, 0);
    return 0;
}

fn f_l0() -> u32 {
    let p: ptr = alloc(16);
    store8(p, 0, 1);
    return load8(p, 0);
}

fn f_cond(c: u32) -> u32 {
    if c == 1 {
        let n: u32 = 8;
        let p: ptr = alloc(n);
        store8(p, 0, 1);
        return load8(p, 0);
    }
    return 0;
}

fn eat(q: ptr) -> u32 {
    return load8(q, 0);
}

fn f_escape() -> u32 {
    let n: u32 = 8;
    let p: ptr = alloc(n);
    store8(p, 0, 5);
    return eat(p);
}

fn f_userfree() -> u32 {
    let n: u32 = 8;
    let p: ptr = alloc(n);
    store8(p, 0, 1);
    let v: u32 = load8(p, 0);
    free(p);
    return v;
}

fn f_reassign() -> u32 {
    let n: u32 = 8;
    let p: ptr = alloc(n);
    store8(p, 0, 1);
    p = alloc(n);
    store8(p, 0, 2);
    return load8(p, 0);
}
"""
L1_ALPHA_SRC = L1_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
L1_MANUAL_SRC = L1_SRC.replace("@@CHOOSE@@", "choose gc_manual")
L1_AUTO_SRC = L1_SRC.replace("@@CHOOSE@@", "choose gc_auto")

#: 每个函数里 L1 **决定要还掉**的名字（按 AST 序：各循环末尾的、各 `return` 前的、函数末尾的）。
#: 每一格的"为什么"，对着 `_l1_place` 的规则读：
#:   * `f_ret` 用处在返回表达式里 -> 落点一（那条 `return` 之前）；
#:   * `f_end` void 函数没有 `return` -> 落点二（函数末尾）；
#:   * `f_loop` / `f_nest` 用处落在循环里 -> 也是落点二（"最外层循环之后"那个落点撤了，
#:     理由写在 §2.5：自举侧拿不到外层循环的跨度，而函数末尾同样安全）；
#:   * `f_two` 两个候选一次扫描按声明序出来；
#:   * `f_early` 最后一次用处之后还有两条 `return` -> 挑**最早**那条（`return 9`）之前 ——
#:     落空的那条路（`return 0`）会漏一次，**安全但不是最优**，这是明写的；
#:   * `f_l0` 尺寸是字面量 -> L0 已经提到栈上 -> 它不碰 arena，这里不接；
#:   * `f_cond` 声明不在函数体顶层 -> 那一支可能根本没走、槽里是未初始化的值 -> **不接**（安全）；
#:   * `f_escape` 交给用户函数 -> 外逃；`f_userfree` 用户自己 `free` 过（那一处**不是**
#:     候选的"安全用法"）；`f_reassign` 重新赋值 —— 三条都取消资格；
#:   * `eat` 没有候选。
L1_WANT = {
    "f_ret": ["p"],
    "f_end": ["p"],
    "f_loop": ["p"],
    "f_nest": ["p"],
    "f_two": ["a", "b"],
    "f_early": ["p"],
    "f_l0": [],
    "f_cond": [],
    "eat": [],
    "f_escape": ["p"],
    "f_userfree": [],
    "f_reassign": [],
}


def _l1_names(src: str) -> dict[str, list[str]]:
    """每个函数里 L1 要还掉的名字 —— 直接读**编译器的决定**（AST 上那三个 `l1` 槽），
    不去 grep 产物（产物里分不出"用户自己写的 `free`"与"L1 插的"）。"""
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    out: dict[str, list[str]] = {}

    def walk(stmts: list, acc: list) -> None:
        for s in stmts:
            if isinstance(s, (lomentc.While, lomentc.For)):
                walk(s.body, acc)
                acc.extend(s.l1)
            elif isinstance(s, lomentc.If):
                walk(s.then, acc)
                walk(s.otherwise, acc)
            elif isinstance(s, lomentc.Match):
                for _, b in s.arms:
                    walk(b, acc)
            elif isinstance(s, lomentc.Return):
                acc.extend(s.l1)

    for f in mod.funcs:
        acc: list[str] = []
        walk(f.body, acc)
        acc.extend(f.l1)
        out[f.name] = acc
    return out


@test
def test_l1_definite_lifetime_rule():
    """`gc_auto_alpha` 的 **L1（定活）**：**逐格的真值表**（`docs/210` §2.5）。

    **为什么这一层必须长在 token 流上**（而不是 `docs/210` §2 原先写的"靠 `docs/206` 的
    移动/借用子集"）：那条路的证据是 E006（移出之后不许再用），而 `_is_copy_type` 把 `ptr`
    算作 **Copy** ⇒ 传一个 `ptr` 根本不算"移出" ⇒ E006 对 `ptr` 一句话也说不了。所以这一层
    与 L0/L2 同一条纪律：**规则定义在 token 流上**（两个实现唯一都完整持有的表示）。

    **两个方向都是钩子**：哪一格该插而没插（规则太窄）会红；哪一格不该插却插了
    （规则太松 —— 那是 **use-after-free**）也会红。第二条尤其重要：`f_cond` 那一格
    （声明在 `if` 里）就是"松一格就出事"的例子 —— 声明那一支没走时槽里是**未初始化**的
    值，在函数末尾 `free` 它就是在 free 一个垃圾指针。

    **档位门**：`gc_manual`（默认档）与 `gc_auto` 下这一层**一个都不许插** —— 默认档
    不许改变任何现有程序（`docs/175` §3.4 五条之一）。
    """
    alpha = _l1_names(L1_ALPHA_SRC)
    manual = _l1_names(L1_MANUAL_SRC)
    auto = _l1_names(L1_AUTO_SRC)
    assert alpha == L1_WANT, f"alpha 的决定表 {alpha} / 期望 {L1_WANT}"
    assert all(v == [] for v in manual.values()), f"`gc_manual` 下不许插：{manual}"
    assert all(v == [] for v in auto.values()), f"`gc_auto` 下不许插：{auto}"
    # 不许空转：这一份源里必须**真的**有插进去的，也有被取消资格的
    assert sum(len(v) for v in alpha.values()) == 8, "这条判据在空转（没有插进去的）"
    assert alpha["f_l0"] == [] and alpha["f_cond"] == []
    # `f_escape` 把 `p` 交给用户函数 `eat` —— `eat` 只把首参透给 `load8`（deref-only 放宽，
    # `docs/210` §7），所以 `p` **不外逃**了，L1 接得住它。这一格正是那次放宽在 L1 上的钉子。
    assert alpha["f_escape"] == ["p"], alpha["f_escape"]
    # 两份源只差那一行
    assert L1_ALPHA_SRC.replace("choose gc_auto_alpha", "X") ==         L1_MANUAL_SRC.replace("choose gc_manual", "X")


@test
def test_runtime_is_forced_in_and_absent_by_default():
    """`docs/227` 判据 C：`runtime` = **强制带**，默认档 = 一个 `@__loment_` 都没有。

    这一对是这一维**买到的全部**：同一份**用不到**运行期的程序，加不加那一行，产物差
    **恰好**是那段运行期。差得比它多（比如把分配器也带进来）就说明"强制带"越了界 ——
    §2.3 那条：`_IR_HEAP` **不**跟着 `rt_on` 走，因为那是 64 KiB 的零初全局，
    给一个只会除法的裸机程序凭空装上它是"给承诺加税"。
    """
    NL = chr(10)
    body = NL.join(["fn f() -> u32 {", "    return 7;", "}", ""])
    off = "module m" + NL + NL + body
    on = "module m" + NL + "choose runtime" + NL + NL + body
    assert errs(off) == [], errs(off)
    assert errs(on) == [], errs(on)

    ir_off = lomentc.emit_llvm(parse(off), ROOT, [])
    ir_on = lomentc.emit_llvm(parse(on), ROOT, [])
    # 默认档：这段运行期**一点痕迹都没有**
    assert "@__loment_" not in ir_off, ir_off
    # `choose runtime`：那段在，而且**只有**那段（四个符号 = memcmp/memset/memcpy/abort）
    assert ir_on.count("@__loment_") == 4, ir_on
    for sym in ("memcmp", "memset", "memcpy", "abort"):
        assert f"@__loment_{sym}" in ir_on, sym
    # **分配器不在里面**（§2.3）：强制带的是那段 freestanding 文本块，不是那 64 KiB 的堆
    assert "@__loment_alloc" not in ir_on, "强制带把分配器也拖进来了"
    assert "@__loment_heap" not in ir_on, "强制带把堆全局也拖进来了"
    # 产物差**恰好**是那一段：摘掉它，剩下的与默认档逐字符相同
    assert ir_on.replace(lomentc._IR_RUNTIME + NL, "", 1) == ir_off, (len(ir_off), len(ir_on))


@test
def test_pkg_manifest_carries_the_runtime_requirement():
    """包清单（`pkg.lomp` 的 `runtime()`）是**包级**的运行期需求（`docs/324`）。

    四件事一起钉，少一件就测不到这项机制：

      1. 清单说 `runtime`、模块自己没写 `choose` ⇒ 根是默认 `no_runtime` 时**报**，
         而且正文要点名**包**（不是只有模块）；
      2. 根写 `choose runtime` ⇒ 放行（需求被满足）；
      3. 清单说 `no_runtime`、模块是纯的 ⇒ 默认档**放行** —— 这就是"库不必都要运行期"；
      4. 清单说 `no_runtime` 而模块里有 `alloc` ⇒ 报（清单与代码矛盾，不许静默）。

    另加一条：清单说 `runtime` 时，**模块自己当根编**也用清单的默认档 —— 否则店里每个
    模块都必须再写一行 `choose runtime`，包级声明就白写了。
    """
    import tempfile
    NL = chr(10)

    def pkg(rt: str) -> str:
        return ("module pkg" + NL + NL + "pub fn name() -> str {" + NL
                + '    return "demo";' + NL + "}" + NL + NL
                + "pub fn version() -> str {" + NL + "    return \"0.1.0\";" + NL + "}"
                + NL + NL + "pub fn runtime() -> str {" + NL
                + '    return "' + rt + '";' + NL + "}" + NL)

    pure = "module mod" + NL + NL + "pub fn twice(x: u32) -> u32 {" + NL + "    return x + x;" + NL + "}" + NL
    heavy = "module mod" + NL + NL + "pub fn mk() -> ptr {" + NL + "    return alloc(8);" + NL + "}" + NL

    def run(manifest: str, mod: str, root_extra: str, body: str) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "demo").mkdir()
            (d / "demo" / "pkg.lomp").write_text(manifest, encoding="utf-8")
            (d / "demo" / "mod.lomt").write_text(mod, encoding="utf-8")
            rp = d / "root.lomt"
            rp.write_text("module root" + NL + root_extra + NL
                          + 'use "demo/mod.lomt"' + NL + NL
                          + "fn f() -> u32 {" + NL + "    " + body + NL + "    return 0;" + NL + "}" + NL,
                          encoding="utf-8")
            m = lomentc.load(rp)
            deps = lomentc.resolve_deps(m, ROOT, rp.parent, entry=rp)
            root_errs = lomentc.check(m, None, deps)
            # 模块自己当根编：清单的默认档要生效（否则店里每份模块都得再写一行）
            mm = lomentc.load(d / "demo" / "mod.lomt")
            return root_errs, lomentc.check(mm)

    # ① 清单要运行期、模块用它、根没写 ⇒ 报，点名包
    e, _ = run(pkg("runtime"), heavy, "", "let p: ptr = mk();")
    assert any("demo" in x and "runtime" in x for x in e), e
    # ② 根写了 `choose runtime` ⇒ 不报
    e, _ = run(pkg("runtime"), heavy, "choose runtime", "let p: ptr = mk();")
    assert e == [], e
    # ③ 清单说 no_runtime、模块是纯的 ⇒ 默认档放行（库不必都要运行期）
    e, _ = run(pkg("no_runtime"), pure, "", "let v: u32 = twice(2);")
    assert e == [], e
    # ④ 清单说 no_runtime 而模块里有 alloc ⇒ 报（清单与代码矛盾）
    e, _ = run(pkg("no_runtime"), heavy, "", "let p: ptr = mk();")
    assert any("alloc" in x for x in e), e
    # ⑤ 模块自己当根：清单说 runtime ⇒ 不报 E031；说 no_runtime 且纯 ⇒ 也不报
    _, e2 = run(pkg("runtime"), heavy, "choose runtime", "let p: ptr = mk();")
    assert e2 == [], e2
    _, e3 = run(pkg("no_runtime"), pure, "", "let v: u32 = twice(2);")
    assert e3 == [], e3


@test
def test_runtime_dep_must_declare_and_agree():
    """`docs/227` 判据 D：依赖的**声明是需求** —— 库没写要报，根不满足要报。

    改之前这里是一律「库不许 `choose`」（`lomentc.py` 的 `check()`）。那条按字面做不下去：
    `loment/lib/proc.lomt` 要除法，当根编要 `choose runtime`、当依赖又不许写 —— 两面堵死。
    新规矩把方向反过来，而它**本来就是那句老报错自己写的**（"库该声明**能力需求**"）。
    """
    import tempfile
    NL = chr(10)
    lib_div = "module lib" + NL + NL + "pub fn mk() -> ptr {" + NL + "    return alloc(8);" + NL + "}" + NL
    lib_rt = "module lib" + NL + "choose runtime" + NL + NL + "pub fn mk() -> ptr {" + NL + "    return alloc(8);" + NL + "}" + NL
    tail = NL + 'use "lib.lomt"' + NL + NL + "fn f() -> ptr {" + NL + "    return mk();" + NL + "}" + NL

    def run(lib: str, root: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "lib.lomt").write_text(lib, encoding="utf-8")
            rp = d / "root.lomt"
            rp.write_text(root, encoding="utf-8")
            m = lomentc.load(rp)
            deps = lomentc.resolve_deps(m, ROOT, rp.parent, entry=rp)
            return lomentc.check(m, None, deps)

    # ① 库用了会拖运行期的构造却**什么都没写** ⇒ 报，并且点名那个库
    e = run(lib_div, "module root" + tail)
    assert any("lib" in x and "需求" in x for x in e), e
    # ② 库要求 `runtime`、根不满足（默认 `no_runtime`）⇒ 报，两边都点名
    e = run(lib_rt, "module root" + tail)
    assert any("runtime" in x and "根" in x for x in e), e
    # ③ 根满足它 ⇒ **不许**报（"合法的那一对不许拒"）
    e = run(lib_rt, "module root" + NL + "choose runtime" + tail)
    assert e == [], e


@test
def test_a_slot_is_root_only_wherever_the_dimension_is():
    """槽（`choose lspe_aicheck_true`）与维度同一条边界：**整个程序**的格，不是单元的。

    这一条钉三件事：`addin` 单元里写了要报（①，否则那行静默无效）、
    依赖写了要按**需求**核（②，根不满足就报、两边点名）、根满足时不许拒（③）。
    """
    import tempfile
    NL = chr(10)
    body = NL + "pub fn f() -> u32 {" + NL + "    return 1;" + NL + "}" + NL

    def run(files: dict[str, str], root: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name, text in files.items():
                (d / name).write_text(text, encoding="utf-8")
            rp = d / "root.lomt"
            rp.write_text(root, encoding="utf-8")
            # 走**唯一入口**（`load_unit`）：`addin` 是预扫期落定的，
            # 自己拼 `load()` + `resolve_deps()` 会让它白写 —— 那条库注释就在讲这件事。
            m, deps = lomentc.load_unit(rp, ROOT)
            return lomentc.check(m, None, deps)

    # ① `addin` 单元里写槽 ⇒ 报（前缀与核心维度那条相同，诊断码照旧 E022）
    e = run({"sw.lomt": "module sw" + NL + "choose lspe_aicheck_true" + NL},
            "module root" + NL + "addin sw" + NL + body)
    assert any("`addin` 单元不许声明核心模式" in x and "aicheck" in x for x in e), e

    # ② 依赖写 `lspe_aicheck_true`、根没有 ⇒ 报，两边点名，并给出要补的那一行
    lib = "module lib" + NL + "choose lspe_aicheck_true" + NL + body
    tail = NL + 'use "lib.lomt"' + NL + body
    e = run({"lib.lomt": lib}, "module root" + tail)
    assert any("aicheck" in x and "根" in x and "lspe_aicheck_true" in x for x in e), e

    # ③ 根满足它 ⇒ 这一格**不许**报（"合法的那一对不许拒"）
    e = run({"lib.lomt": lib}, "module root" + NL + "choose lspe_aicheck_true" + tail)
    assert [x for x in e if "aicheck" in x] == [], e


@test
def test_a_sibling_dependency_is_not_every_units_requirement():
    """#726：体检一个依赖时，**它的兄弟**不是它的依赖。

    闭包 = 根的全部传递依赖。从前 `_check` 的"依赖重查"那一趟给每个单元递的是
    **整份闭包减自己** —— 于是闭包里有一个写 `choose runtime` 的兄弟，任何没写这一行
    的单元都会被判"根是 `no_runtime`、依赖要求 `runtime`"。实测：根 `use` 一个
    `std/f64conv`（它 → `f64` → `f64bits`/`wide`/`mem`）时，一条 `use` 都没有的
    `f64bits` 会收到四条这种话，而它自己干干净净。

    这一条钉三件事：兄弟不算（①）、**真依赖仍然算**（②）、两边的可接受集与自举一致
    （`loment_store_twin_test` 那 148/156 是另一处证据，不在这里重复）。
    """
    import tempfile
    NL = chr(10)
    tail = NL

    def run(files: dict[str, str], root: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name, text in files.items():
                (d / name).write_text(text, encoding="utf-8")
            rp = d / "root.lomt"
            rp.write_text(root, encoding="utf-8")
            m = lomentc.load(rp)
            deps = lomentc.resolve_deps(m, ROOT, rp.parent, entry=rp)
            return lomentc.check(m, None, deps)

    plain = "module a" + NL + NL + "pub fn f() -> u32 { return 1; }" + NL
    needs = ("module b" + NL + "choose runtime" + NL + NL
             + "pub fn g(x: u32) -> u32 { return x / 2; }" + NL)
    root = ("module root" + NL + "choose runtime" + NL
            + 'use "a.lomt"' + NL + 'use "b.lomt"' + NL + tail
            + "fn _start() { syscall4(60, 0, 0, 0); }" + NL)
    # ① 兄弟组合法：a 没有 use、b 要求 runtime，根满足它 ⇒ 一个字都不许报
    e = run({"a.lomt": plain, "b.lomt": needs}, root)
    assert e == [], ("兄弟单元被当成了 `a` 的依赖（#726）：" + repr(e))

    # ② 真依赖的体检**不许**被这条放宽掉：mid 用了 rt（要求 runtime）却没自己声明
    mid = ("module mid" + NL + 'use "rt.lomt"' + NL + NL
           + "pub fn h(x: u32) -> u32 { return rt_half(x); }" + NL)
    rt = ("module rt" + NL + "choose runtime" + NL + NL
          + "pub fn rt_half(x: u32) -> u32 { return x / 2; }" + NL)
    root2 = ("module root" + NL + "choose runtime" + NL
             + 'use "mid.lomt"' + NL + tail
             + "fn _start() { syscall4(60, 0, 0, 0); }" + NL)
    e = run({"mid.lomt": mid, "rt.lomt": rt}, root2)
    assert any("mid" in x and "runtime" in x for x in e), (
        "真依赖的要求没有生效 —— 修 #726 修过头了：" + repr(e))
#: `@__loment_heap`（间接寻址那一份）的**初值写法** —— 语义上是全零指针，但必须写成
#: `zeroinitializer` 而不是 `null`：**我们自己的两个链接器**（`tools/lomelf.py` 与
#: `loment/tools/lomelf.lomt`）的 IR 子集只有零初始化这一种写法，`ptr null` 会让它们
#: 在解析期就拒掉（实测 `标量初值解析失败: 'null'`，`FujoJTOP/loment#359` 那一轮）。
#: 这条不是偏好，是**约束**。
HEAP_PTR_INIT = "internal global ptr zeroinitializer"

#: `docs/228` 的 A 那一份程序：分配 **200000** 字节（> 64 KiB）逐字节写 7，读回最后一字节。
#: **源只有一份** —— `loment_hosted_test` 那条运行时判据（A3）用的就是**同一个常量**。
#: 分开抄一份必然漂，而 A3 的全部意义就是"两个方向上看的是同一条程序"。
HEAP_SRC = """module host_heap

@@CHOOSE@@

fn main() -> i32 {
    let n: u32 = 200000;
    let p: ptr = alloc(n);
    let i: u32 = 0;
    while i < n {
        store8(p, i, 7 as u8);
        i = i + 1;
    }
    return load8(p, n - 1) as i32;
}
"""

# `choose runtime` 是 `alloc` 自己要的（`docs/227`：默认档 `no_runtime` 是一句**保证**，
# 不是提示）—— 这一份用了 `alloc`，所以两档都必须写上，否则**检查器先拒**，
# 后面那些"产物长什么样"的判据根本走不到。
HEAP_HOSTED_SRC = HEAP_SRC.replace("@@CHOOSE@@", "choose hosted\nchoose runtime")
# 同一份源，**只去掉 `choose hosted` 那一行**：于是它就是没有 `choose hosted` 的那一档
# （默认 `sealed`）。
HEAP_SEALED_SRC = HEAP_SRC.replace("@@CHOOSE@@", "choose runtime")
#: `docs/230` 那一份：**hosted + `gc_auto`**，而且**逼收集器真的跑起来**。
#:
#: 先要一块 **200000 字节**（> 64 KiB —— 静态 arena 装不下它）写满 7；再churn 200 次小分配，
#: 把触发计数据推过 `32768`（`_IR_GC_TRIGGER_FIXED` 的阈值），也就是**收集器一定会收**；
#: 最后把那 200000 字节**逐字节读回来** —— 只被栈上 `big` 指着的那一块**必须原封不动**。
#: 读回全对返回 7。静态档在第一次 `alloc(200000)` 就落 `boom`（SIGILL）。
#:
#: **源只有一份**：孪生那条（`loment_p8_test`）与运行时那条（`loment_hosted_test`）都用它。
HEAP_GC_SRC = """module host_gc

@@CHOOSE@@

fn main() -> i32 {
    let big: ptr = alloc(200000);
    let i: u32 = 0;
    while i < 200000 {
        store8(big, i, 7 as u8);
        i = i + 1;
    }
    let j: u32 = 0;
    while j < 200 {
        let junk: ptr = alloc(1024);
        store8(junk, 0, 1 as u8);
        j = j + 1;
    }
    let k: u32 = 0;
    let bad: u32 = 0;
    while k < 200000 {
        if load8(big, k) != 7 { bad = bad + 1; }
        k = k + 1;
    }
    if bad == 0 { return 7; }
    return 99;
}
"""

# `choose runtime` 是 `gc_auto` 自己要的（它与默认的 `no_runtime` 定义上冲突）。
# 两份只差 `choose hosted` 那一行，而**收集器换不换**就看它。
HEAP_HOSTED_GC_AUTO_SRC = HEAP_GC_SRC.replace(
    "@@CHOOSE@@", "choose hosted\nchoose gc_auto\nchoose runtime")
HEAP_SEALED_GC_AUTO_SRC = HEAP_GC_SRC.replace("@@CHOOSE@@", "choose gc_auto\nchoose runtime")

#: `docs/231`（B3）用的那一份：**在静态 64 KiB 上真的收一次**。
#:
#: 上面那两份判据里 `sealed` 那一侧在第一次 `alloc(200000)` 就落 `boom` —— **根本没走到
#: 收集器**。而 B3 改的是 `_IR_GC_SHARED` 本体，**每一档**都变了，所以得单独有一条把
#: 静态档的收集器跑起来：累计要 **200 KB**（收不回来必然 OOM），同时守着一块只被栈上
#: `keep` 指着的 1000 字节 —— **收集之后必须原样**。成功返回 **9**。
GC_SEALED_CHURN_SRC = """module gc_sealed_churn

choose gc_auto
choose runtime

fn main() -> i32 {
    let keep: ptr = alloc(1000);
    let i: u32 = 0;
    while i < 1000 {
        store8(keep, i, 9 as u8);
        i = i + 1;
    }
    let j: u32 = 0;
    while j < 200 {
        let junk: ptr = alloc(1024);
        store8(junk, 0, 1 as u8);
        j = j + 1;
    }
    let k: u32 = 0;
    let bad: u32 = 0;
    while k < 1000 {
        if load8(keep, k) != 9 { bad = bad + 1; }
        k = k + 1;
    }
    if bad == 0 { return 9; }
    return 99;
}
"""


@test
def test_mark_table_is_a_bitmap_sized_by_the_arena():
    """`docs/231`（B3）：标记表是**位图**，每 8 字节一位 ⇒ **arena / 64 字节**。

    判的是**关系**而不是某个数：两档各自的 arena 与表的大小必须是那个除法。
    以前是"每 8 字节一个 `i32`"（arena / 2 字节 —— 1 MiB 的 arena 带 512 KiB 的 `.bss`）。
    """
    for ir, arena in ((lomentc._IR_GC_SHARED, 65536),
                      (lomentc._IR_GC_SHARED_HOSTED, 1048576)):
        assert f"@__loment_mark = internal global [{arena // 64} x i8] zeroinitializer" in ir
        assert f"[{arena // 8} x i32]" not in ir, "旧的那个形状不该再出现"
    # 位运算的三件：字节下标 = 位号 >> 3、掩码 = 低三位、按位与判"标过没有"
    g = lomentc._IR_GC_SHARED
    assert "%mby = lshr i32 %midx, 3" in g and "%mbit = and i32 %midx, 7" in g
    assert "%mmsk = trunc i32 %msh to i8" in g and "%was = icmp ne i8 %mold, 0" in g
    # **口径没变**：位号仍是「块头偏移 >> 3」—— 换 arena 只换表的行数
    assert "%midx = lshr i32 %i, 3" in g


def _heap_ir(src: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "heap.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(f)
        deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
        assert lomentc.check(mod, deps=deps) == []
        return lomentc.emit_llvm(mod, ROOT, deps)


@test
def test_hosted_heap_comes_from_the_platform_and_sealed_is_untouched():
    """`docs/228` §5 的 **A2 + A1**：`hosted` 向宿主声明分配器；`sealed` 一个字节没动。

    **两条一起判**才说明问题：只判"`hosted` 里有 `malloc`"会被"给**所有**程序都加上"
    骗过 —— 而那正是 `sealed` 那条保证被改掉的样子（`docs/228` §2）。
    """
    hosted = _heap_ir(HEAP_HOSTED_SRC)
    sealed = _heap_ir(HEAP_SEALED_SRC)

    # A2：arena 不是 `.bss` 里的定长数组，而是宿主给的一块
    assert f"@__loment_heap = {HEAP_PTR_INIT}" in hosted
    assert "[65536 x i8]" not in hosted, "hosted 的 arena 还是那个静态数组"
    assert "declare ptr @malloc(i64)" in hosted, "没向宿主声明分配器"
    assert "; loment-heap: platform 1048576" in hosted, "没把这件事写进产物"
    # 声明在**用之前**（LLVM 对未声明的调用是 `use of undefined value`，实测）
    assert hosted.index("declare ptr @malloc(i64)") < hosted.index("call ptr @malloc(i64 1048592)")

    # A1：同一份源去掉那一行 ⇒ 逐字节还是原来那一段（闸门真的在 `port` 上）
    assert "@__loment_heap = internal global [65536 x i8] zeroinitializer" in sealed
    assert "@malloc" not in sealed, "sealed 的产物里出现了宿主符号"
    assert "loment-heap" not in sealed, "sealed 的产物里出现了 A 的标记"
    # 两份唯一的差别就是 arena 那一段 —— 偏移模型、空闲链表、前沿回退**共用**
    assert "icmp ule i32 %nxtoff, 1048576" in hosted
    assert "icmp ule i32 %nxtoff, 65536" in sealed


@test
def test_hosted_collector_is_the_same_collector():
    """`docs/230`：`_IR_GC_HOSTED` 与 `_IR_GC_SHARED` **只差 arena 那三件事**。

    这是"**换**出来"而不是"另抄一份"的判据：两份文本把 arena 相关的行去掉之后，
    **其余每一行必须逐字相同**。不同就说明两份收集器已经开始各长各的了 ——
    而那正是 `docs/158` §5 那条孪生纪律要挡的东西。

    它同时钉住"换的是哪三件"：基址、基址反算、标记表行数、以及两处根扫边界。
    """
    def rest(text: str, markers: tuple[str, ...]) -> list[str]:
        return [l for l in text.split("\n")
                if l != "entry:" and not any(m in l for m in markers)]
    a = lomentc._IR_GC_SHARED
    b = lomentc._IR_GC_SHARED_HOSTED
    assert rest(a, ("@__loment_heap", "65536", "[1024 x i8]")) == \
           rest(b, ("%hb", "1048576", "[16384 x i8]")), "两份收集器已经不一样了"
    # 换掉的**每一处**都真的换掉了 —— 这条防"上面那条因为两边都空而恒真"
    assert "@__loment_heap" not in b and "65536" not in b and "[1024 x i8]" not in b
    assert b.count("%hb = call ptr @__loment_arena()") == a.count("entry:")
    # 触发那一段**共用**：它按字节数触发，不看 arena 大小
    assert lomentc._IR_GC_HOSTED == b + lomentc._IR_GC_TRIGGER_FIXED


@test
def test_hosted_gc_auto_grows_too_and_gets_the_hosted_collector():
    """`docs/230`（= `docs/228` §4 的 **B1**）：`hosted` + `gc_auto` 也吃平台那一块，
    而且**收集器跟着换**。

    在这之前 `gc_auto` 那一档留在静态 64 KiB，靠 `; loment-heap: static 65536` 说出来
    （`docs/228` §5 的 A5）。B1 落地后那条记号**没有再出现的场合** —— 判据也从
    "它说了"改成"它涨了"：这一档现在必须**在平台上**，表也跟着换成 `arena/8`。
    """
    ir = _heap_ir(HEAP_HOSTED_GC_AUTO_SRC)
    assert "; loment-heap: platform 1048576" in ir
    assert f"@__loment_heap = {HEAP_PTR_INIT}" in ir, "arena 不是宿主来的"
    assert "@__loment_mark = internal global [16384 x i8]" in ir, "标记表没跟着 arena 换"
    assert "[1024 x i8]" not in ir
    assert "%hb = call ptr @__loment_arena()" in ir, "收集器没用基址指针"
    assert "; loment-heap: static" not in ir, "B1 落地后这条记号不该再出现"
    # 对照：同一份源**去掉 `choose hosted`** ⇒ 还是静态那一套 —— 闸门真的在 `port` 上
    sealed = _heap_ir(HEAP_SEALED_GC_AUTO_SRC)
    assert "@__loment_mark = internal global [1024 x i8]" in sealed
    assert "@__loment_heap = internal global [65536 x i8] zeroinitializer" in sealed
    assert "@malloc" not in sealed


#: `docs/219` §8b 的 **S2**（V1 剩下的最后一件）那一份：根开了运行期、**声明了外部函数**，
#: 于是堆按需带上（`_heap_needed` 的 `_declares_extern` 那一支）。与 `HEAP_SRC` 的差别
#: 只有"有没有 `extern fn`"这一件事 —— 判据要**两个方向一起判**（见那条 test 的说明）。
FFI_HEAP_SRC = """module ffi_heap

@@CHOOSE@@

extern fn c_len(p: ptr) -> i32;

fn ask(p: ptr) -> i32 {
    return c_len(p);
}

fn main() -> i32 {
    return 7;
}
"""
FFI_HEAP_SEALED_SRC = FFI_HEAP_SRC.replace("@@CHOOSE@@", "choose runtime")
FFI_HEAP_ALPHA_SRC = FFI_HEAP_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha\nchoose runtime")


@test
def test_ffi_artifacts_get_an_arena_big_enough_for_a_real_library():
    """`docs/219` §8b 的 **S2**：声明了 `extern fn` 的 `sealed` 产物，arena **给够**。

    **Why**：这是 V1（`docs/183` §4）实测剩下的最后一件 —— 真 `libz.a` 的 `deflateInit`
    在**静态** arena 上要 **256–512 KiB**（`docs/219` §8b 的实测矩阵），而 `_IR_HEAP` 的
    64 KiB 一装就 `__loment_abort`。而 `sealed` 的定义是"底下没有东西"，**没有宿主的
    `malloc` 可问**（`docs/228` §2）⇒ 只有一条路：arena **封在产物里，但给够**（1 MiB）。
    没有这一条，"链真 libc"到 `compress` 就断在这儿。

    **How to apply**：判据必须**两个方向一起判**。只判"FFI 那一份很大"会被"给**所有**程序
    都加大"骗过 —— 而那正是 `sealed` 那条保证被改掉的样子（同一个坑 `docs/228` §5 的 A1
    已经踩过：只判 hosted 有 `malloc`）。所以对照那一半是**同一份源去掉 `extern fn`**：
    它必须还是老样子（64 KiB、直接寻址、产物里一个字不提 arena）。

    第三条钉住 `gc_auto_alpha` 那一格。它的收集器（`_IR_GC_TRIGGER_ADAPT`）是**直接寻址**
    arena 的，换 arena 要连它一起换、也就是再抄一份那段文本（`hosted` 与 alpha **定义上
    冲突**，仓库里没有"间接寻址的 alpha"这份先例）。所以那一格**明写地**留在 64 KiB，
    而且**说出来**（"不静默"）—— 这条判据钉的就是"它说了"。
    """
    ffi = _heap_ir(FFI_HEAP_SEALED_SRC)
    sealed = _heap_ir(HEAP_SEALED_SRC)

    # ① `extern fn` 那一档：arena 封在产物里、给够 1 MiB、寻址走基址指针
    assert f"; loment-heap: ffi {lomentc.FFI_ARENA_BYTES}" in ffi, \
        "没把 arena 这件事写进产物（构建那一侧不该去读源码来知道它）"
    # 块比 arena 多 16 字节：`#620` 的取整余量（基址对齐到 16 之后那一小截仍在块内）。
    assert f"[{lomentc.FFI_ARENA_BYTES + 16} x i8] zeroinitializer" in ffi, (
        "arena 还是那块 64 KiB 的数组")
    assert "getelementptr [65536 x i8], ptr @__loment_heap" not in ffi, "还在直接寻址"
    assert "%hb = call ptr @__loment_arena()" in ffi, "分配器没用基址指针"
    assert "ptrtoint ptr @__loment_arena_block to i64" in ffi, "arena 不是产物里那块静态的"
    assert "@malloc" not in ffi, "sealed 的产物里出现了宿主符号"
    # ② 对照：**没有** `extern fn` 的那一份一个字节没动
    assert "[65536 x i8] zeroinitializer" in sealed
    assert "loment-heap" not in sealed, "没链外源库的产物也被改大了 —— 那是 sealed 那条保证"
    # ③ `gc_auto_alpha` 那一格：留 64 KiB，**而且说出来**
    alpha = _heap_ir(FFI_HEAP_ALPHA_SRC)
    assert "; loment-heap: static 65536" in alpha, \
        "这一格没换 arena，却也没说出来 —— 那是静默"
    assert "[65536 x i8] zeroinitializer" in alpha


#: `docs/232` 那条判据的输入：**一档一份**最小程序。每档都得有自己的产物 ——
#: 只测一档看不出"某一档的文本少了终结指令"这类错。
_TIER_PROBE = """module tier_probe

@@CHOOSE@@

fn main() -> i32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7 as u8);
    return load8(p, 0) as i32;
}
"""

#: （名字，`choose` 那几行）。`hosted` 与 `gc_auto_alpha` 是**定义上冲突**的
#: （`CORE_CONFLICTS`），所以没有那一格。
#:
#: **已知缺口**（棘轮：只许变短，条目在就断言"它仍然在"）。`gc_auto_alpha` 那一档现在还
#: 发不出合法 LLVM，而且**与本 PR 修的不是同一个缺陷**：它把 `alloc` 提到栈上之后
#: **整个单元没有堆** ⇒ 收集器整段不发，而**写屏障的调用照样发**（`__loment_wb` 只定义在
#: 收集器那一段里）⇒ `use of undefined value '@__loment_wb'`。修它要先回答"没有堆的单元
#: 怎么知道堆不在"（发射前扫一遍，还是干脆让 alpha 档永远带堆），那是**另一笔**。
#: **现在是空的**：`gc_auto_alpha/sealed` 那一格原先在这里（写屏障挂在了没有 arena 的
#: `sealed` 单元上 ⇒ 引用不到堆 ⇒ clang 拒）。2026-10-12 把它改成"跟着 `heap_needed`
#: 走"之后，那一格过了 —— 这条判据自己会红着要求删掉它（`FujoJTOP/loment#239`）。
_KNOWN_ILLEGAL_TIERS: set[str] = set()

_TIER_CHOOSES = (
    ("gc_manual/sealed", "choose runtime"),
    ("gc_auto/sealed", "choose gc_auto\nchoose runtime"),
    ("gc_auto_alpha/sealed", "choose gc_auto_alpha\nchoose runtime"),
    ("gc_manual/hosted", "choose hosted\nchoose runtime"),
    ("gc_auto/hosted", "choose hosted\nchoose gc_auto\nchoose runtime"),
    # **LMS 第 3 级入口 A**（`docs/253` §5.3）：`alloc` 分配不出时先收一次再试。
    # ⚠ 这两格是**故意**加进来的 —— 那一支 IR（`boom` 改成 `rescue`/`dead`、`floop`
    # 的 phi 多一条入边）是本项目里**手写 IR 最容易写错**的一类，而孪生判据看不出来
    # （两处实现一起错时它照样绿）。**只有过一遍 clang 才知道它是不是合法 LLVM。**
    ("gc_auto/lms", "choose gc_auto\nchoose runtime\nchoose lms_guard"),
    ("gc_auto_alpha/lms", "choose gc_auto_alpha\nchoose runtime\nchoose lms_guard"),
)


@test
def test_lms_entry_a_rescue_needs_both_lms_and_a_collector():
    """LMS 第 3 级入口 A（`docs/253` §5.3）：`alloc` 分配不出时**先收一次再试**。

    **两个条件缺一不可** —— `lms` 开着（它属于 LMS 第 3 级）**而且**这份产物里真有
    收集器（`gc_auto` / `gc_auto_alpha` / **`lgc`**）。`gc_manual` 单独**没有东西可收**，
    "拉回"在那里是一句空话 —— 所以那一格**必须一个字都不加**。这是这一级明写的边界。

    **`gc_manual` + `lgc` 有收集器**（`#1461`）：窗口一开，`__loment_gc_allow` 就放行，
    OOM 那一刻先收一次再试是窗口该有的行为；漏掉这一支，窗口里撞 OOM 直接 abort。
    所以那一格必须有 `lmsretry`。

    网格：{gc_manual, gc_auto, gc_auto_alpha} × {关着, 开着}，外加 `gc_manual` + `lgc` 两格。
    """
    # ⚠ **尺寸必须走 `const` 名，不能是字面量**：`gc_auto_alpha` 会把
    # `let p: ptr = alloc(<字面量>)` **静态提升到栈上**（L0），于是这份产物**根本不需要堆**
    # —— `__loment_alloc` 一个字节都不发，"分配不出"也就无从谈起。
    # 第一版探针就是栽在这儿的：alpha 那一格**静默为空**，看起来像"入口 A 没生效"，
    # 实际是**测的那件事不在场**（`docs/210` §2.2 那条诊断教训的又一实例）。
    # 走 `const` 名（或加一次 `as u64`）就取消提升资格。
    probe = ("module m\nchoose runtime\nconst N: u32 = 64;\n%s\n\n"
             "fn main() -> i32 {\n    let p: ptr = alloc(N);\n"
             "    return load8(p, 0) as i32;\n}\n")

    def ir(chooses: str) -> str:
        return lomentc.emit_llvm(parse(probe % chooses), ROOT, [])

    for gc in ("gc_manual", "gc_auto", "gc_auto_alpha"):
        # 2026-10-18（0.1.5.1）：第 3 级由 `lms_guard` 这个**细开关**管，
        # 它默认开着（满档）；这里显式关掉那一格，判据因此不依赖默认档。
        off = ir(f"choose {gc}\nchoose close lms_guard")
        assert "lmsretry" not in off, (gc, "关着时不许有")
        on = ir(f"choose {gc}\nchoose lms_guard")
        if gc == "gc_manual":
            assert "lmsretry" not in on, (gc, "没有收集器 ⇒ 没有东西可拉回")
        else:
            assert "%lmsretry = alloca i1" in on, (gc, "开着且要收 ⇒ 该有")
            # **默认产物不带 `@__loment_` 符号**（`docs/253` §13.2）：标记是本帧的 alloca，
            # 不是全局 —— 满档是默认，这条不能反。
            assert "@__loment_lms_retry" not in on, (gc, "不许再长出全局符号")
            # **只许重来一次**：重试标记在**每一条成功返回前**都要清掉（1 次初始化 + 2 次清）。
            assert on.count("store i1 false, ptr %lmsretry") == 3, on

    # **`gc_manual` + `lgc`**（`#1461`）：manual 档也有收集器了 —— 窗口里那句"开"就是
    # 它被允许收的时刻，OOM 救援因此必须在场。
    manual_lgc = lomentc.emit_llvm(_lgc_load(
        "choose write grammar tur2026.3\nmodule m\nchoose runtime\n"
        "choose gc_manual\nchoose lms_guard\nconst N: u32 = 64;\n\n"
        "fn _s() {\n    lgc start {\n        let q: ptr = alloc(N);\n"
        "        store8(q, 0, 1);\n    }\n}\n"), ROOT, [])
    assert "%lmsretry = alloca i1" in manual_lgc, (
        "`gc_manual` + `lgc` 的产物里该有 LMS 第 3 级入口 —— 窗口里撞 OOM 要先收一次")
    assert "call void @__loment_collect_now()" in manual_lgc, (
        "救援必须走 `collect_now` —— 它才尊重窗口的开关")


#: g1 探针：`alloc` **直接发出**的指针上，一个越界写 + 一个界内读。
_LMS_SITE = ("module m\nchoose runtime\nconst N: u32 = 8;\n%s\n\n"
             "fn f() -> u32 {\n    let p: ptr = alloc(N);\n"
             "    store8(p, 9, 1);\n    let x: u32 = load8(p, 0);\n    return x;\n}\n")


@test
def test_lms_g1_site_response_is_crash_or_reject():
    """LMS 第 3/4/5 级在**访存点**上的回答（`docs/253` §5.3–§5.5）。

    * **默认满档**：两个访存点各发一条 `icmp ult`，越界那一支**跳过**（第 4 级）、
      控制流汇到 `lmsjoin` —— 赔进去的是一条指令，不是这个进程；
    * **`choose close lms_reject`**：同一处改成 `call @__loment_abort`（第 5 级）；
    * **`choose close lms_guard`**：一条检查都不发。

    ⚠ 第三条不是"省点代码"：`docs/253` §5.3 明写 `g3 == 0` 是常态，而**没有回答的
    检查比没有检查更坏** —— 所以第 3 级关掉时连比较一起不发。
    """
    on = _lms_ir(_LMS_SITE % "")
    assert on.count("lmsok") >= 2, "两个访存点都该有检查"
    assert on.count("lmsjoin") >= 1, on
    # 拒那一支是 `br label %L*_lmsjoin`，不是 abort
    assert "lmsbad:\n  br label %" in on, on

    crash = _lms_ir(_LMS_SITE % "choose close lms_reject")
    assert "lmsok" in crash, "第 5 级仍然要检查"
    assert "lmsbad:\n  call void @__loment_abort()" in crash, crash
    assert "lmsjoin" not in crash, crash

    off = _lms_ir(_LMS_SITE % "choose close lms_guard")
    assert "lmsok" not in off, "关掉第 3 级之后一条检查都不许有"


def _lms_ir(src: str) -> str:
    return lomentc.emit_llvm(parse(src), ROOT, [])


@test
def test_lms_g1_site_is_valid_llvm():
    """上面三种回答的产物都要过 clang 前端 —— 手写 IR 只有过 clang 才算合法。"""
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not clang:
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        for tag, chooses in (("full", ""), ("crash", "choose close lms_reject"),
                             ("off", "choose close lms_guard")):
            p = Path(td) / f"lms_{tag}.ll"
            p.write_text(_lms_ir(_LMS_SITE % chooses), encoding="utf-8", newline="\n")
            r = subprocess.run([clang, "-c", "-o", os.devnull, "-x", "ir", str(p)],
                               capture_output=True, text=True)
            assert r.returncode == 0, (tag, r.stderr[:400])
    print("      三种回答（拒 / 崩 / 关）的产物都过 clang 前端")


#: 运行时探针：界内写 1；界外写 7 与界外读**都越过 `alloc(8)` 的界**。
#: 第 4 级跳过那两条 ⇒ 答案是 1；第 5 级接手 ⇒ 进程自己死。
_LMS_RUN = ("module m\nchoose runtime\nconst N: u32 = 8;\n%s\n\n"
            "fn _start() -> u32 {\n    let p: ptr = alloc(N);\n"
            "    store8(p, 0, 1);\n    store8(p, 9, 7);\n"
            "    let x: u32 = load8(p, 0);\n    let y: u32 = load8(p, 9);\n"
            "    let v: u32 = x + y;\n    syscall4(60, v as u64, 0, 0);\n    return 0;\n}\n")


@test
def test_lms_rejector_keeps_running_and_crash_stops_it():
    """判「跳过之后没更坏」（`docs/253` §5.4 那条最难的判据）。

    同一份源，只差 `choose close lms_reject`：

    * **默认满档**：越界的那条 `store` / `load` 被拒，**程序继续**，退出码是**界内那一半**
      算出来的 **1**（界外读回 0，界外写没发生）。
    * **关掉第 4 级**：同样的越界变成 `llvm.trap` —— 进程被信号打死（rc 132），
      **不是**一个算错的答案。

    判据的形状与 `lvm_t3_test` 判 B 同一条：**不许空转** —— 只断言"没崩"是不够的，
    还要断言答案**恰好**是 1。少了它，一个"把越界访问静默跳过又顺手把界内的也跳过"的
    实现照样能过。
    """
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not clang:
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        rc: dict[str, int] = {}
        for tag, chooses in (("reject", ""), ("crash", "choose close lms_reject")):
            ll = td / f"{tag}.ll"
            ll.write_text(_lms_ir(_LMS_RUN % chooses), encoding="utf-8", newline="\n")
            exe = td / f"{tag}.bin"
            r = subprocess.run(
                [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                 "-ffreestanding", "-static", "-fno-pie", "-fuse-ld=lld",
                 "-Wl,-e,_start", str(ll), "-o", str(exe)],
                capture_output=True, text=True, shell=False)
            assert r.returncode == 0, (tag, r.stderr[-300:])
            rc[tag] = _run_elf(exe, td, tag)
    assert rc["reject"] == 1, f"拒绝器那一格答案不对: rc={rc['reject']}（期望 1）"
    assert rc["crash"] == 132, f"崩溃器那一格没死: rc={rc['crash']}（期望 132 = SIGILL）"
    print("      越界访存：默认跳过（rc=1，界内那一半照算）/ 关掉第 4 级则 SIGILL（rc=132）")


def _run_elf(exe: Path, td: Path, name: str) -> int:
    """在 Linux 上直接跑，在 Windows 上经 WSL 跑 —— 与 `loment_elf_test` 同一条路数。"""
    if sys.platform.startswith("linux"):
        r = subprocess.run([str(exe)], capture_output=True, shell=False)
        # ⚠ **被信号打死时 Python 给的是 `-signo`，不是 shell 的 `128+signo`** ——
        # 两条路（本机 Linux / Windows 经 WSL）必须报同一个数，否则判据只在一边绿。
        # CI 上第一版就是这么红的：`rc=-4`（SIGILL）对不上期望的 132。
        return r.returncode if r.returncode >= 0 else 128 - r.returncode
    wslp = "/mnt/" + str(exe.resolve()).replace("\\", "/")[0].lower() + \
        str(exe.resolve()).replace("\\", "/")[2:]
    dst = f"/tmp/lms_{name}_{os.getpid()}.bin"
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"cp {wslp} {dst} && chmod +x {dst} && {dst}; echo -n $?"],
                       capture_output=True, text=False, shell=False)
    tail = r.stdout.decode(errors="replace").strip()
    try:
        return int(tail.splitlines()[-1])
    except (ValueError, IndexError):
        return -1


@test
def test_every_tier_emits_ir_clang_accepts():
    """**每一档**的产物都要过 clang 的**前端**（`-c`：不链接、也不跑）。

    这条补的是一个**结构性盲点**：孪生判据比的是"两个实现**一不一致**"，而两个实现
    **一起错**的时候它照样绿。实测（2026-10-11）：`_IR_GC_TRIGGER_FIXED` 的 `go:` 块
    少了一条终结指令（`call void @__loment_collect()` 之后直接就是 `out:` 标签），
    自举那一份**同样**少 —— 于是**所有**孪生判据都看不见它，而任何走 clang 的路径
    （`hosted` 构建）拿到的是一份**非法 IR**：

        error: expected instruction opcode

    用 `-c` 而不是链接：这一条量的是"**产物是不是合法 LLVM**"，与"能不能链上 libc"无关
    —— 后者是各条运行时判据的活。
    """
    import shutil
    import subprocess
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not clang:
        print("      SKIP: 无 clang")
        return
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        for tag, chooses in _TIER_CHOOSES:
            f = Path(td) / "tier_probe.lomt"
            f.write_text(_TIER_PROBE.replace("@@CHOOSE@@", chooses),
                         encoding="utf-8", newline="\n")
            mod = lomentc.load(f)
            deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
            errs = lomentc.check(mod, deps=deps)
            assert not errs, f"{tag} 自己检查不过: {errs[:1]}"
            ll = Path(td) / "tier_probe.ll"
            ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8", newline="\n")
            r = subprocess.run([clang, "-c", str(ll), "-o", str(Path(td) / "tier_probe.o")],
                               capture_output=True, text=True, shell=False)
            if tag in _KNOWN_ILLEGAL_TIERS:
                assert r.returncode != 0, (
                    f"{tag} 已经能过了 —— 请把它从 `_KNOWN_ILLEGAL_TIERS` 里删掉")
                continue
            if r.returncode != 0:
                first = next((l for l in r.stderr.split("\n") if "error" in l), r.stderr[:80])
                bad.append(f"{tag}: {first.strip()}")
    assert not bad, "有档位发出来的不是合法 LLVM:\n  " + "\n  ".join(bad)
    ok = len(_TIER_CHOOSES) - len(_KNOWN_ILLEGAL_TIERS)
    print(f"      {ok} 档的产物都过 clang 前端"
          f"（另有已登记缺口 {len(_KNOWN_ILLEGAL_TIERS)} 个: {', '.join(sorted(_KNOWN_ILLEGAL_TIERS))}）")


@test
def test_mixed_width_arithmetic_has_a_common_type():
    """`#289`: two integer operands must have a common type; widen the narrow one, refuse if none.

    Both sides were wrong before, and in **opposite** directions: the reference widened only the
    left operand and left the right at its own width, so `a: u32 == b: u8` emitted
    `icmp eq i32 %a, %b` with `%b` an `i8` - **invalid IR**, which clang rejects on sight; the
    self-host truncated the left one instead, which disagrees with the reference on
    `load8(...) == x: u8` and is a **wrong answer** in general (`docs/158` section 4 item 11).

    **Both sides were wrong together**, so the twin criteria stayed green - agreement is not
    correctness.

    Measures three things: the direction of the widening (`zext` to the wider one, never a
    truncation), whether the product is legal LLVM, and that the pair which cannot hold the other
    (`u32` vs `i32`) is refused.
    """
    import shutil
    import subprocess

    def ir_of(lines: list[str]) -> str:
        return lomentc.emit_llvm(parse(chr(10).join(lines) + chr(10)), ROOT, [])

    ir = ir_of(["module m", "", "fn eq(a: u32, b: u8) -> bool { return a == b; }"])
    assert "zext i8" in ir and "icmp eq i32" in ir, ir
    ir = ir_of(["module m", "", "fn add(a: i64, b: u32) -> i64 { return a + b; }"])
    assert "zext i32" in ir and "add i64" in ir, ir
    e = errs("module m" + chr(10) + chr(10)
             + "fn bad(a: u32, b: i32) -> u32 { return a + b; }" + chr(10))
    assert any("没有公共类型" in x and "as" in x for x in e), e
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not clang:
        print("      SKIP: no clang (widening direction and the refusal were still measured)")
        return
    with tempfile.TemporaryDirectory() as td:
        ll = Path(td) / "mw.ll"
        ll.write_text(ir_of(["module m", "",
                             "fn eq(a: u32, b: u8) -> bool { return a == b; }"]),
                      encoding="utf-8", newline=chr(10))
        r = subprocess.run([clang, "-c", str(ll), "-o", str(Path(td) / "mw.o")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
    print("      narrow side widened (zext), irreconcilable pair refused, product passes clang")


@test
def test_lspe_dimension_defaults_open():
    """第七维：不写 `choose` 就是 `lspe_open_admin`，台账随产物一起发出（v16）。"""
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["potato"] == potato.VERSIONS[-1]
    lp = doc["lspe"]
    assert lp["state"] == "lspe_open_admin"
    assert lp["set_by"] == "default"
    assert lp["mode"] == "full" and lp["level"] == 3
    # demo 里有下标表达式；普查至少给出一条，且四个计数自洽。
    assert lp["sites"] >= 1
    assert lp["obligations"] == lp["sites"]
    assert lp["sites"] == lp["proved"] + lp["undecided"]
    assert "InBounds" in lp["per_kind"]
    # demo 有一条 `capability blk_write … revocable`：E9 给它一条 `InDomain`（Typed）
    # 和一条 `CapabilityLive`（Assumed），所以地板是 1（撤销域），不是 0。
    assert lp["per_kind"]["InDomain"] == "1/1", lp["per_kind"]
    assert lp["per_kind"]["CapabilityLive"] == "1/1", lp["per_kind"]
    assert lp["floors"] == lp["floor_detail"]["assumed"] == 1
    # `#786` 起 `floor_detail` 多一格 `pass`；`#750` 再加 `depth`。
    assert lp["floor_detail"] == {"assumed": 1, "stale_wf": 0,
                                  "budget": 0, "depth": 0, "drop_honest": 0,
                                  "pass": 0}, lp["floor_detail"]
    assert lp["passes"] == [] and lp["pass_ratio"] == f"0/{lp['sites']}"
    assert lp["invariants"] == {"i1": "ok", "i2": "ok", "i3": "ok",
                                "i4": "ok", "i5": "ok"}, lp["invariants"]
    assert lp["interrogations"] == {"provenance": lp["proved"],
                                    "falsification": lp["sites"],
                                    "cross_exam": lp["sites"]}
    assert lp["lspc"] == "full" and lp["acked"] == 0
    assert potato.validate(doc) == []
    # QIR 骨架：每个站点有 Site / Claim / Obligation / Evidence 四格。
    s0 = lomentc.load(DEMO).lspe_sites[0]
    assert s0["claim"] == "allow"
    assert s0["obligation"] in ("GuardHolds", "InBounds")
    # 证据可以是 E5 的 `GlobalInvariant`（demo 的两个循环下标），所以不比名字：
    # 证成的站点必须**有来源** —— 这才是"每个放行都有出处"那一格。
    assert s0["proved"] and s0["source"], s0
    assert s0["evidence"] not in (None, "none"), s0


@test
def test_lspe_bounds_sites_match_the_ir_checks():
    """台账里的 `bounds` 站点 == IR 里那些**常量界**的 `icmp ult i32`（`#1021`）。

    这条是那个问题的量尺：LMS g1 的检查没有自己的语法，只有**词法形状**；
    如果普查规则与发射器的规则分叉，两边的数就对不上 —— 而注释不会让它们对上。
    """
    src = ("module lspe_bounds\n\nchoose runtime\n\nfn f() -> u32 {\n"
           "    let p: ptr = alloc(16);\n    store8(p, 0, 1);\n"
           "    let a: u32 = load8(p, 15);\n    return a;\n}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod, deps = lomentc.load_unit(p, ROOT)
        bounds = [s for s in mod.lspe_sites if s["kind"] == "bounds"]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
    consts = [ln for ln in ir.splitlines()
              if re.search(r"icmp ult i32 [0-9]+, [0-9]+$", ln)]
    assert len(bounds) == 2 and len(consts) == 2, (len(bounds), consts)

    # 档位关掉（只要管理器）：两边都归零。
    off_src = src.replace("choose runtime", "choose runtime\nchoose lms_manage_only_admin")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "off.lomt"
        p.write_text(off_src, encoding="utf-8", newline="\n")
        mod2, deps2 = lomentc.load_unit(p, ROOT)
        bounds2 = [s for s in mod2.lspe_sites if s["kind"] == "bounds"]
        ir2 = lomentc.emit_llvm(mod2, ROOT, deps2)
    assert not bounds2, bounds2
    assert not re.search(r"icmp ult i32 [0-9]+, [0-9]+$", ir2, re.M)


@test
def test_lspe_close_admin_is_recorded():
    """`lspe_close_admin` 关的是审计，不是检查；`set_by` 必须说清是源码定的。"""
    src = "module lspe_close\n\nchoose lspe_close_admin\n\nfn f() -> u32 { return 0; }\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "close.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        doc = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
    assert doc["potato"] == potato.VERSIONS[-1]
    assert doc["lspe"]["state"] == "lspe_close_admin"
    assert doc["lspe"]["set_by"] == "source"
    # 关审计：普查不算，计数全零。
    assert doc["lspe"]["sites"] == 0 and doc["lspe"]["per_kind"] == {}
    assert potato.validate(doc) == []


@test
def test_lspe_q1_provenance_and_strict_e034():
    """Q1 来源问：guard 与裸字面量下标有来源；非字面量下标没有 —— strict 下是 E034。"""
    src = ("module lspe_q1\n\n"
           "choose runtime\n\n"
           "capability d : disk[0..8]\n\n"
           "fn f(a: [u32; 4], i: u32) -> u32 {\n"
           "    guard d(i);\n"
           "    let x: u32 = a[0];\n"
           "    return a[i];\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "q1.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        assert lomentc.check(lomentc.load(p), deps=[]) == []
        errs = lomentc.check(lomentc.load(p), deps=[], lspe_mode="strict")
        assert any("未标注的放行" in e for e in errs), errs
        doc = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
    lp = doc["lspe"]
    # 3 条 E1/E2 站点 + E9 的两条（域声明 + `guard d(i)` 的包含性）。
    assert lp["sites"] == 5, lp
    assert lp["proved"] == 3 and lp["undecided"] == 2, lp
    assert lp["per_kind"] == {"GuardHolds": "1/1", "InBounds": "1/2",
                             "InDomain": "1/2"}, lp
    assert potato.validate(doc) == []


@test
def test_lspe_e035_rejects_an_unregistered_site_kind():
    """站点对账（I1 的第一半）：普查里出现一个没登记的种类 ⇒ E035。"""
    mod = lomentc.load(DEMO)
    mod.lspe_sites = list(getattr(mod, "lspe_sites", [])) + [
        {"id": "demo:bogus:0", "kind": "bogus", "source": None,
         "line": 1, "proved": False},
    ]
    errs = lomentc.check(mod, deps=[])
    assert any("未登记的站点" in e for e in errs), errs


@test
def test_lspe_census_agrees_with_the_ast():
    """独立对照：token 普查的 guard/index 数必须等于 AST 的 Guard/Index 节点数。

    twin 只证一致 —— 两边用同一条 token 规则，同错时它照样绿（`docs/253` §5.1.1）。
    这一条是那条教训的独立那一腿。
    """
    from dataclasses import fields, is_dataclass

    def walk(x, out):
        if isinstance(x, lomentc.Guard):
            out["guard"] = out.get("guard", 0) + 1
        if isinstance(x, lomentc.Index):
            out["index"] = out.get("index", 0) + 1
        if is_dataclass(x):
            for f in fields(x):
                v = getattr(x, f.name)
                if isinstance(v, list):
                    for y in v:
                        walk(y, out)
                elif is_dataclass(v):
                    walk(v, out)

    checked = 0
    bad = []
    for p in sorted((ROOT / "loment" / "examples").glob("*.lomt")):
        try:
            mod = lomentc.load(p)
        except lomc.LomError:
            continue
        ast: dict[str, int] = {}
        walk(mod, ast)
        tok: dict[str, int] = {}
        # 独立对照只覆盖 E1/E2 两族 —— E3/E4 的站点不是"节点数"同形的东西
        # （`free`/`let` 的**流**不是节点本身），它们的判据另写。
        for s in getattr(mod, "lspe_sites", []):
            if s["kind"] in ("guard", "index"):
                tok[s["kind"]] = tok.get(s["kind"], 0) + 1
        checked += 1
        if ast != tok:
            bad.append((p.name, ast, tok))
    assert checked >= 30, checked
    assert not bad, bad


@test
def test_lspe_site_ids_are_content_addressed():
    """前面插一个无关站点，已有站点的 id 不变（#794）。"""
    src1 = "module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[0];\n}\n"
    src2 = ("module m\n\nfn f(a: [u32; 4], b: [u32; 4]) -> u32 {\n"
            "    let x: u32 = b[1];\n    return a[0];\n}\n")
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "a.lomt"
        p1.write_text(src1, encoding="utf-8", newline=chr(10))
        p2 = Path(td) / "b.lomt"
        p2.write_text(src2, encoding="utf-8", newline=chr(10))
        m1 = lomentc.load(p1)
        m2 = lomentc.load(p2)
    ids1 = [s["id"] for s in m1.lspe_sites if s["kind"] == "index"]
    ids2 = [s["id"] for s in m2.lspe_sites if s["kind"] == "index"]
    assert len(ids1) == 1 and len(ids2) == 2, (ids1, ids2)
    assert ids1[0] in ids2, (ids1, ids2)


@test
def test_line_numbers_do_not_drift_after_a_multiline_string():
    """`#481`：字符串字面量里的换行也要走行号。

    原先词法器只把**列**往前推，`line` 停在开引号那一行 —— 之后每条诊断都比真实位置
    少掉字符串里的换行数。实测：`"a<换行>b"` 之后的那行类型错报在 4，而它在 5。
    """
    src = ('module m' + chr(10) + 'fn f() -> i32 {' + chr(10)
           + '  let s: str = "a' + chr(10) + 'b";' + chr(10)
           + '  let x: u32 = true;' + chr(10) + '  return 0;' + chr(10) + '}' + chr(10))
    toks = lomc.lex(src)
    lets = [t for t in toks if t.val == "let"]
    assert [t.line for t in lets] == [3, 5], [(t.val, t.line) for t in lets]
    assert any(t.val == "b" for t in toks) is False  # 换行是字面量正文，不是 token
    e = errs(src)
    assert e and e[0].startswith("5:"), e
    print("      多行字符串之后：第二个 let 在第 5 行、诊断也在第 5 行（不再漂）")


@test
def test_load_errors_point_at_the_use_line_not_at_module():
    """`#480`：装载期的报错指到**那条 `use` 自己的行**，不是 `1:1`。

    原先 `resolve_name` / `resolve_deps` 一律 `LomError(1, 1, …)` —— `loment.py diag`
    的 caret 落在 `module m` 上，而 `use` 在几十行之外，用户照着 caret 改不到任何东西。
    三种都钉：名字形式找不到、路径形式不存在、`use` 条数越界（指到**越界的第一条**）。
    """
    import os as _os
    NL = chr(10)

    def load_err(src: str) -> tuple[int, int, str]:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "root.lomt"
            p.write_text(src, encoding="utf-8")
            m = lomentc.load(p)
            try:
                lomentc.resolve_deps(m, ROOT, p.parent, entry=p)
            except lomc.LomError as e:
                return e.line, e.col, e.msg
            raise AssertionError("装载期没有报错")

    ln, col, msg = load_err("module m" + NL + NL + "use no_such_module_xyz" + NL
                            + "fn f() -> u32 { return 0; }" + NL)
    assert (ln, col) == (3, 1), (ln, col, msg)
    assert "no_such_module_xyz" in msg, msg

    ln, col, msg = load_err("module m" + NL + NL + 'use "nope.lomt"' + NL
                            + "fn f() -> u32 { return 0; }" + NL)
    assert (ln, col) == (3, 1), (ln, col, msg)

    uses = "".join(f'use "f{i}.lomt"' + NL for i in range(301))
    ln, col, msg = load_err("module m" + NL + uses + "fn f() -> u32 { return 0; }" + NL)
    assert (ln, col) == (302, 1), (ln, col, msg)      # 第 1 行 module，301 条从第 2 行起
    assert "301" in msg, msg
    print("      装载期三种报错都指到那条 use 行（3:1 / 3:1 / 302:1）")


@test
def test_refusals_do_not_leak_milestone_ids_or_mangled_names():
    """`#484`：给用户看的拒绝话里没有内部里程碑号，也没有 mangled 实例名。

    原先 `Box<[u32]>` 这一格报的是 `native M23: struct Box_[u32] 字段类型 …` —— `M23`
    是仓库内部的里程碑号，`Box_[u32]` 是拼给形式对象看的实例名；用户两样都不该看到。
    现在：`Box<u32>`（人写的类型）与"字段类型目前只支持标量"（真正的卡点）。
    """
    p = ROOT / "loment" / "examples" / "arg_shape" / "main.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    assert not lomentc.check(mod, deps=deps), "夹具本身要能过检查"
    # ① 形式对象那一层：写出来的泛型形状，不是拼出来的实例名
    try:
        lomentc.emit_potato(mod, ROOT, deps)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
    else:
        raise AssertionError("参考实现居然发出来了？这一格要重判")
    assert "Box<[u32]>" in msg, msg[:200]
    for leak in ("native M", "M23", "Box_["):
        assert leak not in msg, f"用户可见的拒绝话里漏了内部记号 {leak!r}: {msg[:200]}"
    # ② 原生 IR 那一层：**同一条**名字规则（`docs/299` 之后切片字段已经能落，
    #    所以第一个卡点从"字段只支持标量"前移到了"实例名没有规则"）。
    try:
        lomentc.emit_llvm(mod, ROOT, deps)
    except lomc.LomError as e:
        msg2 = e.msg
    else:
        raise AssertionError("IR 后端居然发出来了？这一格要重判")
    assert "Box<[u32]>" in msg2, msg2[:200]
    for leak in ("native M", "M23", "Box_["):
        assert leak not in msg2, f"IR 侧拒绝话里漏了内部记号 {leak!r}: {msg2[:200]}"
    print("      两条拒绝话：写出来的类型 + 真正的卡点，无里程碑号 / mangled 名")


@test
def test_null_pointer_literal_is_accepted_in_argument_position():
    """`#373`：`0 as ptr` 在**实参位置**也要判成 ptr。

    原先 `expr_type(Cast)` 对"字面量 -> ptr"给 None，于是实参检查把它当整型字面量拒；
    而 `let pin: ptr = 0 as ptr;` 与 `if pin != (0 as ptr)` 都过 —— 同一个写法两个裁决。
    自举侧一直是接受的那一边（同一份 `lpi_cli.lomt` 由它编出来）。
    """
    head = "module m" + chr(10) + "fn s(p: ptr) -> u32 { return 0; }" + chr(10)
    ok = head + "fn f() -> u32 { return s(0 as ptr); }" + chr(10)
    assert errs(ok) == [], errs(ok)
    # 非零字面量不是"空指针"：没有类型、也不是 ptr，照旧拒（别把它一起松开）
    bad = head.replace("module m", "module m") + "fn f() -> u32 { return s(5 as ptr); }" + chr(10)
    e = errs(bad)
    assert e and "ptr" in e[0], e
    print("      `0 as ptr` 在实参位置接受；`5 as ptr` 仍拒")


@test
def test_machine_diagnostics_carry_a_column_when_unambiguous():
    """`#5`：`--json` / `--short` 的列号不再是**永远 0**。

    `check()` 只给行；能从消息里**无歧义**定出名字时补列（名字在那一行恰好出现一次）。
    定不出的（`函数 f 参数 K 重复`）仍是 0 —— 少给一列比给一列错的强。
    """
    import json as _json

    src = ("module t2" + chr(10) + "fn _start() {" + chr(10)
           + "    let zz: u32 = 1;" + chr(10) + "    let y: u32 = nosuchv;" + chr(10)
           + "    syscall4(60, zz, y, 0);" + chr(10) + "}" + chr(10))
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "col.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        d = Path(t) / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(p), "--check", "--diag-out", str(d)],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 1, r.stderr[-200:]
        recs = [json.loads(x) for x in d.read_text(encoding="utf-8").splitlines() if x]
    hit = [x for x in recs if "nosuchv" in x["message"]]
    assert hit and hit[0]["col"] == 18, recs
    print("      `nosuchv` 的诊断带列号 18（不是 0）")


@test
def test_a_check_that_raises_becomes_a_diagnostic_not_a_traceback():
    """`#655`：`check()` **自己会抛** —— 一条两万项的扁平表达式撞上 `RecursionError`，
    `check` 把它转成一条 `LomError`（`_RECURSION_FLOOR` 上面那段写的就是这件事）。

    `main` 原先只护住了 `load_unit` 与 `emit_*`：那句为这种情况写好话变成了 Python 栈，
    而 `--diag-out` 还是**空的** —— 编辑器与 CI 看到的是"编译失败、零条诊断"。
    """
    import json as _json

    src = ("module m" + chr(10) + "fn f() -> u32 {" + chr(10)
           + "    return " + "1 + " * 20000 + "1;" + chr(10) + "}" + chr(10))
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "deep.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        d = Path(t) / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(p), "--check", "--diag-out", str(d)],
                           capture_output=True, text=True, encoding="utf-8")
        recs = [_json.loads(x) for x in d.read_text(encoding="utf-8").splitlines() if x]
    assert r.returncode == 1, r.returncode
    assert "Traceback" not in r.stderr, r.stderr[-300:]
    assert "嵌套超过" in r.stderr, r.stderr[-300:]
    assert len(recs) == 1 and recs[0]["line"] == 0, recs
    assert recs[0]["code"] == "E023", recs
    print("      check() 抛出：一条 E023（line 0），不是 Python 栈")


@test
def test_a_use_cycle_is_one_diagnostic_at_the_use_line():
    """`#31`：循环 `use` 报在**那一行**、码是 E018（装载期），不是 E013 指到文件末尾之外。"""
    NL = chr(10)
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "a.lomt").write_text("module a" + NL + 'use "b.lomt"' + NL
                                  + "fn fa() -> u32 { return 1; }" + NL, encoding="utf-8")
        (d / "b.lomt").write_text("module b" + NL + 'use "a.lomt"' + NL
                                  + "fn fb() -> u32 { return 2; }" + NL, encoding="utf-8")
        out = d / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(d / "a.lomt"), "--check", "--diag-out", str(out)],
                           capture_output=True, text=True, encoding="utf-8")
        recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x]
    assert r.returncode == 1 and len(recs) == 1, (r.returncode, recs)
    assert recs[0]["line"] == 2 and recs[0]["col"] == 1, recs
    assert recs[0]["code"] == "E018", recs
    print("      循环 use：E018 @ 2:1（那一行，不是 EOF 之外）")


@test
def test_one_undeclared_call_target_is_one_diagnostic():
    """`#6`：一个未声明的被调只报一次（原先 `--json` 里同一个对象出现两遍）。"""
    src = ("module cat1" + chr(10) + "fn _start() {" + chr(10)
           + '    let a: str = "ab";' + chr(10) + '    let b: str = "cd";' + chr(10)
           + "    let c: str = str_cat(a, b);" + chr(10) + "}" + chr(10))
    e = errs(src)
    hits = [x for x in e if "str_cat" in x]
    assert len(hits) == 1, e
    print("      未声明的被调：一条诊断")


@test
def test_a_parameter_may_shadow_a_module_const():
    """`#217`：形参与模块级常量同名**不是** E013（形参遮蔽常量，合法）。"""
    src = ("module v3" + chr(10) + "pub const K: i64 = 3;" + chr(10)
           + "pub fn f(K: i64) -> i64 { return K * 2; }" + chr(10))
    assert errs(src) == [], errs(src)
    dup = ("module v3" + chr(10) + "fn f(K: i64, K: i64) -> i64 { return K; }" + chr(10))
    assert any("重复" in x for x in errs(dup)), errs(dup)
    print("      形参遮蔽常量：接受；真重名：仍然报")


def _lspe_registry_errors() -> list[str]:
    """登记表自身的判据（测试侧）：缺所有者、缺 accept 集合、未知证据、Differential 销义务。"""
    errs: list[str] = []
    known = set(lspe.LSPE_EVIDENCE_KINDS)
    for name, spec in lspe.LSPE_OBLIGATIONS.items():
        owner = spec.get("owner")
        accept = spec.get("accept")
        if not isinstance(owner, str) or not owner:
            errs.append(f"{name}: 缺语义所有者")
        if not isinstance(accept, tuple) or not accept:
            errs.append(f"{name}: 缺 accept 集合")
            continue
        bad = set(accept) - known
        if bad:
            errs.append(f"{name}: 未知证据种类 {sorted(bad)}")
        if "Differential" in accept:
            errs.append(f"{name}: Differential 不能单独销掉义务")
    return errs


@test
def test_lspe_obligation_registry():
    """19 条义务、8 种证据；每条都有 owner 与 accept；Differential 不在任何 accept 里。"""
    assert len(lspe.LSPE_OBLIGATIONS) == 19
    assert len(lspe.LSPE_EVIDENCE_KINDS) == 8
    assert _lspe_registry_errors() == []
    assert all("Differential" not in v["accept"]
               for v in lspe.LSPE_OBLIGATIONS.values())
    assert set(lspe.LSPE_EVIDENCE_KINDS) == {
        "Axiom", "Syntactic", "Typed", "LocalFlow", "Interproc",
        "GlobalInvariant", "Assumed", "Differential"}


@test
def test_lspe_level_only_lowers_proved():
    """L0 只认语法证据；L3 才认类型证据。级别只缩不放（`docs/262` §5.6）。"""
    src = ("module lspe_lv\n\nchoose runtime\n\n"
           "capability d : disk[0..8]\n\n"
           "fn f(a: [u32; 4], i: u32) -> u32 {\n"
           "    guard d(i);\n"
           "    let x: u32 = a[0];\n"
           "    return a[i];\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "lv.lomt"
        p.write_text(src, encoding="utf-8", newline=chr(10))
        d0 = json.loads(lomentc.emit_potato(lomentc.load(p, lspe_level=0), ROOT))
        d3 = json.loads(lomentc.emit_potato(lomentc.load(p, lspe_level=3), ROOT))
    assert d0["lspe"]["level"] == 0 and d3["lspe"]["level"] == 3
    # L0：guard 的 Typed 与域声明的 Typed 都被级别挡掉，只剩裸字面量下标那条
    # Syntactic；L3 把两条 Typed 都放回来（guard + 域声明）。
    assert d0["lspe"]["proved"] == 1, d0["lspe"]
    assert d3["lspe"]["proved"] == 3, d3["lspe"]
    assert d0["lspe"]["proved"] <= d3["lspe"]["proved"]


def _lspe_load(tmp: str, name: str, src: str, level: int = 3):
    p = Path(tmp) / f"{name}.lomt"
    p.write_text(src, encoding="utf-8", newline=chr(10))
    mod = lomentc.load(p, lspe_level=level)
    return p, mod


@test
def test_lspe_e3_a_reachable_use_after_free_is_never_proved():
    """#740：`free` 之后可达的用，只能是 UNDECIDED —— 不能是 PROVED。"""
    src = ("module e3_alive\n\nchoose runtime\n\n"
           "fn f(p: ptr) -> u32 {\n"
           "    free(p);\n"
           "    if p == p {\n"
           "        return 0;\n"
           "    }\n"
           "    return 1;\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p, mod = _lspe_load(td, "alive", src)
        doc = json.loads(lomentc.emit_potato(mod, ROOT))
    alive = [s for s in mod.lspe_sites if s["kind"] == "alive"]
    assert alive, [s["kind"] for s in mod.lspe_sites]
    assert all(s["evidence"] == "none" for s in alive), alive
    assert doc["lspe"]["per_kind"]["Alive"] == f"0/{len(alive)}", doc["lspe"]


@test
def test_lspe_e3_an_unreachable_use_after_free_is_proved():
    """#740：`return` 之后的用不可达，`Alive` 这一条能证；`free` 本身仍无来源。"""
    src = ("module e3_dead\n\nchoose runtime\n\n"
           "fn g(p: ptr) -> u32 {\n"
           "    free(p);\n"
           "    return 0;\n"
           "    let x: u32 = load8(p, 0);\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p, mod = _lspe_load(td, "dead", src)
        d0 = json.loads(lomentc.emit_potato(mod, ROOT))
        d0_low = json.loads(lomentc.emit_potato(
            lomentc.load(p, lspe_level=0), ROOT))
    alive = [s for s in mod.lspe_sites if s["kind"] == "alive"]
    assert [s["evidence"] for s in alive] == ["LocalFlow"], alive
    assert d0["lspe"]["per_kind"]["Alive"] == "1/1", d0["lspe"]
    assert d0["lspe"]["per_kind"]["FreeOwnership"] == "0/1", d0["lspe"]
    # L0 不认 LocalFlow：同一条站点降回 UNDECIDED（级别只缩不放）。
    assert d0_low["lspe"]["per_kind"]["Alive"] == "0/1", d0_low["lspe"]


@test
def test_lspe_e3_initialized_before_assignment_is_undecided():
    """#740：`let x: T;` 之后、赋值之前的读是 UNDECIDED；赋值之后才是 LocalFlow。"""
    src = ("module e3_init\n\nchoose runtime\n\n"
           "fn h() -> u32 {\n"
           "    let x: u32;\n"
           "    let a: u32 = x;\n"
           "    x = 3;\n"
           "    return a + x;\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        _p, mod = _lspe_load(td, "init", src)
        doc = json.loads(lomentc.emit_potato(mod, ROOT))
    init = [s for s in mod.lspe_sites if s["kind"] == "init"]
    assert [s["evidence"] for s in init] == ["none", "LocalFlow"], init
    assert doc["lspe"]["per_kind"]["Initialized"] == "1/2", doc["lspe"]


@test
def test_lspe_e3_exclusive_overlap_is_undecided():
    """#740：同一条语句里 `&mut x` 与 `x` 重叠 ⇒ `Exclusive` 不证。"""
    src = ("module e3_excl\n\nchoose runtime\n\n"
           "fn take2(p: ptr, v: u32) -> u32 {\n"
           "    return load8(p, 0) + v;\n"
           "}\n\n"
           "fn h() -> u32 {\n"
           "    let x: u32 = 1;\n"
           "    return take2(&mut x as ptr, x);\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        _p, mod = _lspe_load(td, "excl", src)
        doc = json.loads(lomentc.emit_potato(mod, ROOT))
    excl = [s for s in mod.lspe_sites if s["kind"] == "exclusive"]
    assert [s["evidence"] for s in excl] == ["none"], excl
    assert doc["lspe"]["per_kind"]["Exclusive"] == "0/1", doc["lspe"]


@test
def test_lspe_e4_deref_only_callee_yields_interproc():
    """#741：只把首参透给 `load8` 的被调有摘要；把首参返回的被调没有。

    摘要键含实参文本 —— 同一个 `deref` 的两次调用是两个键（推断式泛型的替换由实参决定）。
    """
    src = ("module e4_sum\n\nchoose runtime\n\n"
           "fn deref(p: ptr) -> u32 {\n"
           "    return load8(p, 0);\n"
           "}\n\n"
           "fn leak(p: ptr) -> ptr {\n"
           "    return p;\n"
           "}\n\n"
           "fn h(q: ptr, r: ptr) -> u32 {\n"
           "    let a: u32 = deref(q);\n"
           "    let b: u32 = deref(r);\n"
           "    let c: ptr = leak(q);\n"
           "    return a + b;\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p, mod = _lspe_load(td, "sum", src)
        d1 = json.loads(lomentc.emit_potato(lomentc.load(p, lspe_level=1), ROOT))
        d3 = json.loads(lomentc.emit_potato(mod, ROOT))
    calls = [s for s in mod.lspe_sites if s["kind"] == "call"]
    assert [s["evidence"] for s in calls] == ["Interproc", "Interproc", "none"], calls
    assert len({s["id"] for s in calls}) == 3, calls
    assert d3["lspe"]["per_kind"]["SummaryContract"] == "2/3", d3["lspe"]
    # L1 不认 Interproc（E4 从 L2 起）：站点还在，判决降回 UNDECIDED。
    assert d1["lspe"]["per_kind"]["SummaryContract"] == "0/3", d1["lspe"]


@test
def test_lspe_e4_recursive_summary_terminates_undecided():
    """#741：纯递归没有基例 ⇒ 摘要不进安全表，调用点是洞（不是静默的 PROVED）。"""
    src = ("module e4_rec\n\nchoose runtime\n\n"
           "fn rec(p: ptr) -> u32 {\n"
           "    return rec(p);\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        _p, mod = _lspe_load(td, "rec", src)
    calls = [s for s in mod.lspe_sites if s["kind"] == "call"]
    assert [s["evidence"] for s in calls] == ["none"], calls

def _lgc_load(src: str):
    """按**声明**装载（`front_door` 读 `choose write grammar tur2026.3`）并返回模块。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        return lomentc.load(p)


#: `lgc`（`docs/309`）的基准源：Tur 2026.3，手动档（默认不收集）。
LGC_SRC = """choose write grammar tur2026.3
module lgc

choose runtime

fn churn(n: u32) -> u32 {
    let i: u32 = 0;
    lgc end;
    while i < n {
        let p: ptr = alloc(64);
        store8(p, 0, i as u8);
        i = i + 1;
    }
    lgc start(2ms) {
        let q: ptr = alloc(16);
        store8(q, 0, 7);
    }
    return 0;
}

fn _start() { syscall4(60, churn(3) as u64, 0, 0); }
"""


@test
def test_lgc_is_a_tur2026_3_feature():
    """`lgc` 只在 Tur 2026.3 里是关键字（`docs/309`；2026.2 已封版）。

    三格：2026.3 收下；2026.2 里 `lgc` 仍是普通标识符（报"未声明"）；预算只收
    **编译期时长**（变量预算要两边一致的类型推断，v1 不做）。
    """
    ok = lomentc.check(_lgc_load(LGC_SRC), deps=[])
    assert ok == [], ok
    old = LGC_SRC.replace("tur2026.3", "tur2026.2")
    try:
        errs = lomentc.check(_lgc_load(old), deps=[])
    except lomc.LomError as e:      # 2026.2 里 `lgc` 是普通标识符：解析期就拒
        errs = [str(e)]
    assert errs, "2026.2 居然收下了 `lgc`"
    bad = LGC_SRC.replace("lgc start(2ms)", "lgc start(2 + 3)")
    try:
        errs = lomentc.check(_lgc_load(bad), deps=[])
    except lomc.LomError as e:
        errs = [str(e)]
    assert errs, "运行期算出来的预算居然被收下了"
    # `no_runtime` 是**保证**：产物里没有那段运行期，而 `lgc` 要发控制全局与四个函数。
    nr = ("choose write grammar tur2026.3\nmodule m\nchoose no_runtime\n"
          "fn f() { lgc end; }\n")
    assert any("no_runtime" in e for e in lomentc.check(_lgc_load(nr), deps=[]))
    hard = LGC_SRC.replace("lgc start(2ms) {", "lgc start(2ms);\n    if true {")
    try:
        _lgc_load(hard)
    except Exception as e:
        assert "必须带块" in str(e), e
    else:
        raise AssertionError("带预算的 lgc start 没有带块，居然被收下了")


@test
def test_lgc_is_refused_under_no_gc():
    """`no_gc` 的产物**没有收集器**，`lgc` 定义上没有东西可控 —— 编译期点名拒（E039）。"""
    src = LGC_SRC.replace("choose runtime", "choose runtime\nchoose no_gc")
    errs = lomentc.check(_lgc_load(src), deps=[])
    assert any("no_gc" in e and "lgc" in e for e in errs), errs
    import loment_diag  # noqa: PLC0415
    code, _t, _h = loment_diag.classify(errs[0])
    assert loment_diag.family(code) == "E039", (code, errs[0])


@test
def test_lgc_control_runtime_appears_only_when_used():
    """控制运行期**按需发**（`docs/309`）：没写 `lgc` 的程序一个字节都不多。

    `gc_manual` 尤其重要：它的既有承诺是"产物里没有收集器/根表"。用了 `lgc` 的单元
    才带上收集器（默认关闭），没用的一格照旧 —— 两边都在这里钉住。
    """
    plain = LGC_SRC.replace("    lgc end;\n", "").replace(
        "    lgc start(2ms) {\n        let q: ptr = alloc(16);\n"
        "        store8(q, 0, 7);\n    }\n", "")
    ir_plain = lomentc.emit_llvm(_lgc_load(plain), ROOT)
    for sym in ("@__loment_gc_on", "@__loment_gc_deadline", "__loment_gc_allow",
                "__loment_maybe_collect_ctl", "__loment_now_ns",
                "@__loment_roots", "@__loment_collect"):
        assert sym not in ir_plain, f"没写 lgc 的单元里出现了 {sym}"
    ir = lomentc.emit_llvm(_lgc_load(LGC_SRC), ROOT)
    for sym in ("@__loment_gc_on = internal global i32 0",
                "@__loment_gc_deadline = internal global i64 0",
                "__loment_gc_allow", "__loment_maybe_collect_ctl",
                "__loment_now_ns", "@__loment_roots", "@__loment_collect",
                "call void @__loment_maybe_collect_ctl(i32 64)"):
        assert sym in ir, f"用了 lgc 的单元缺 {sym}"
    # 自动档初值是 1（`lgc end` 才是暂停）；manual 初值 0（窗口才收）。
    auto = LGC_SRC.replace("choose runtime", "choose runtime\nchoose gc_auto")
    ir_auto = lomentc.emit_llvm(_lgc_load(auto), ROOT)
    assert "@__loment_gc_on = internal global i32 1" in ir_auto, "gc_auto 的初值不是 1"


@test
def test_lgc_window_saves_and_restores_even_on_return():
    """窗口在**每条退出路径**上恢复 `on`/`deadline`（`docs/309`）。

    这一格钉 IR 形状：每个 `begin` 在入口块有 `%lgc.on.N` / `%lgc.dl.N` 两个槽；
    `return` 之前先把它们读回来再 `ret`。嵌套窗口各用各的编号。
    """
    ir = lomentc.emit_llvm(_lgc_load(LGC_SRC), ROOT)
    assert "%lgc.on.0 = alloca i32" in ir and "%lgc.dl.0 = alloca i64" in ir, ir
    # begin：保存进入前，再置 1 与 deadline（2ms = 2_000_000）
    assert "store i32 %" in ir and "ptr %lgc.on.0" in ir
    assert "add i64 %" in ir and "2000000" in ir, "2ms 没有折成 2000000 纳秒"
    # return 前恢复：`churn` 的 `ret i32 0` 之前能看到 `load ... %lgc.on.0`
    ret_at = ir.rindex("ret i32 0", 0, ir.index("\n; _start"))
    head = ir[:ret_at]
    assert "load i32, ptr %lgc.on.0" in head, "return 前没有恢复 on"
    assert head.rindex("load i32, ptr %lgc.on.0") > head.index("lgc.on.0 = alloca")


@test
def test_domains_ten_kinds_static():
    """十种域的静态规则（docs/306 §2–§5）：形状、极性、参数、容量。"""
    ok = ("module m\nchoose runtime\n"
          "capability front : disk[()..3]\n"
          "capability back  : disk[5..()]\n"
          "capability allc  : disk[()..()]\n"
          "capability dng   : mem[2..4] danger\n"
          "capability ep    : quota[0..4] epoch(3)\n"
          "capability farc  : slot[0..()] far(2)\n"
          "capability sl    : trace[0..100] sliding(2)\n"
          "capability dec   : lease[0..4] decaying(3)\n"
          "capability gr    : cred[0..()] growing(2)\n"
          "capability cons  : auth[0..1] consumed(1)\n"
          "fn f() -> u32 { tick; return 0; }\n")
    assert errs(ok) == [], errs(ok)

    # 形状：前无限只判上界、后无限只判下界
    assert errs("module m\nchoose runtime\ncapability c : disk[()..3]\n"
                "fn f() -> u32 { guard c(100); return 0; }\n"), "前无限应当只判上界"
    assert errs("module m\nchoose runtime\ncapability c : disk[5..()]\n"
                "fn f() -> u32 { guard c(100); return 0; }\n") == []
    # 极性的对偶：危险域的字面量落在禁止区内是**编译错误**
    assert any("危险域" in x for x in errs(
        "module m\nchoose runtime\ncapability c : mem[2..4] danger\n"
        "fn f() -> u32 { guard c(3); return 0; }\n"))
    assert errs("module m\nchoose runtime\ncapability c : mem[2..4] danger\n"
                "fn f() -> u32 { guard c(9); return 0; }\n") == []
    # 时律参数必须为正
    assert any("参数必须为正" in x for x in errs(
        "module m\nchoose runtime\ncapability c : q[0..4] epoch(0)\n"
        "fn f() -> u32 { return 0; }\n"))
    # consumed 必须有界且域宽 <= 64
    assert any("必须有上下界" in x for x in errs(
        "module m\nchoose runtime\ncapability c : q[0..()] consumed(1)\n"
        "fn f() -> u32 { return 0; }\n"))
    assert any("超过 64" in x for x in errs(
        "module m\nchoose runtime\ncapability c : q[0..99] consumed(1)\n"
        "fn f() -> u32 { return 0; }\n"))


@test
def test_domains_remaining_kinds_static():
    """余下七种域（`docs/306` §5、`docs/307` §1–§5）：形状前提与实参个数。

    每条都必须在**未修**的代码上是红的：这些构造在 0.1.5.1 里根本不存在，
    参考实现会把 `cyclic(4)` 当成未知尾巴词 —— 所以这条判据的第一版先确认
    "它现在能报"，不是"它一直能报"。
    """
    # cyclic：周期必须大于上界，否则回绕会放进域外的位置
    assert any("必须大于上界" in x for x in errs(
        "module m\nchoose runtime\ncapability c : ring[0..3] cyclic(2)\n"
        "fn f() -> u32 { return 0; }\n"))
    # adaptive / flow / generational / reachable：形状必须有上下界
    assert any("必须有上下界" in x for x in errs(
        "module m\nchoose runtime\ncapability a : t[0..()] adaptive\n"
        "fn f() -> u32 { return 0; }\n"))
    # reachable：义务槽按位置取模 64，域宽必须放得下
    assert any("超过 64" in x for x in errs(
        "module m\nchoose runtime\ncapability r : lease[0..99] reachable(2)\n"
        "fn f() -> u32 { return 0; }\n"))
    # 语境类三支必须带第二个实参
    assert any("需要第二个实参" in x for x in errs(
        "module m\nchoose runtime\ncapability s : auth[0..15] subject(3)\n"
        "fn f(n: u32) -> u32 { guard s(n); return n; }\n"))
    # 其余律不接受
    assert any("不接受第二个实参" in x for x in errs(
        "module m\nchoose runtime\ncapability c : disk[0..4]\n"
        "fn f(n: u32) -> u32 { guard c(n, 1); return n; }\n"))
    # quality 只喂自适应域
    assert any("quality 只用于自适应域" in x for x in errs(
        "module m\nchoose runtime\ncapability c : disk[0..4]\n"
        "fn f() -> u32 { quality c = 10; return 0; }\n"))
    # discharge 只结可达域的义务
    assert any("discharge 只用于可达域" in x for x in errs(
        "module m\nchoose runtime\ncapability c : disk[0..4]\n"
        "fn f() -> u32 { discharge c(1); return 0; }\n"))
    print("      余下七种域：七条形状/实参规则逐条报错")


@test
def test_domains_ten_kinds_runtime():
    """十种域真跑一遍：形状 3 + 极性 1 + 时律 6。

    **不是"字符串在不在"**：生成的 Rust 编出来真跑，虚拟钟直接拨到每个用例要的值。
    """
    import shutil
    import subprocess
    src = ("module m\nchoose runtime\n"
           "capability reg   : disk[2..4]\n"
           "capability front : disk[()..3]\n"
           "capability back  : disk[5..()]\n"
           "capability allc  : disk[()..()]\n"
           "capability dng   : mem[2..4] danger\n"
           "capability ep    : quota[0..4] epoch(3)\n"
           "capability farc  : slot[0..()] far(2)\n"
           "capability sl    : trace[0..100] sliding(2)\n"
           "capability dec   : lease[0..4] decaying(3)\n"
           "capability gr    : cred[0..()] growing(2)\n"
           "capability cons  : auth[0..1] consumed(1)\n"
           "fn f_reg(n: u32) -> u32 { guard reg(n); return n; }\n"
           "fn f_front(n: u32) -> u32 { guard front(n); return n; }\n"
           "fn f_back(n: u32) -> u32 { guard back(n); return n; }\n"
           "fn f_allc(n: u32) -> u32 { guard allc(n); return n; }\n"
           "fn f_dng(n: u32) -> u32 { guard dng(n); return n; }\n"
           "fn f_ep(n: u32) -> u32 { guard ep(n); return n; }\n"
           "fn f_farc(n: u32) -> u32 { guard farc(n); return n; }\n"
           "fn f_sl(n: u32) -> u32 { guard sl(n); return n; }\n"
           "fn f_dec(n: u32) -> u32 { guard dec(n); return n; }\n"
           "fn f_gr(n: u32) -> u32 { guard gr(n); return n; }\n"
           "fn f_cons(n: u32) -> u32 { guard cons(n); return n; }\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "d.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        assert lomentc.check(mod) == [], lomentc.check(mod)
        rs = lomentc.emit_rust(mod, ROOT)
        ir = lomentc.emit_llvm(mod, ROOT)
    # 十种域共用一个判决点；两种后端都得有它
    assert "@__loment_guard2" in ir and "@__loment_tick" in ir
    assert "__loment_guard2(" in rs and "__loment_tick()" in rs

    rustc = shutil.which("rustc")
    if not rustc:
        print("      SKIP: 无 rustc（运行那一半）")
        return
    main = (
        'include!("m.rs");\n'
        "fn main() {\n"
        "    std::panic::set_hook(Box::new(|_| {}));\n"
        "    let cases: &[(fn(u32) -> u32, u32, u64, bool)] = &[\n"
        "        (f_reg, 2, 0, true), (f_reg, 9, 0, false),\n"
        "        (f_front, 1, 0, true), (f_front, 100, 0, false),\n"
        "        (f_back, 100, 0, true), (f_back, 3, 0, false),\n"
        "        (f_allc, 999, 0, true),\n"
        "        (f_dng, 9, 0, true), (f_dng, 3, 0, false),\n"
        "        (f_ep, 2, 0, true), (f_ep, 2, 3, false),\n"
        "        (f_farc, 1, 0, true), (f_farc, 9, 0, false),\n"
        "        (f_farc, 3, 2, true), (f_farc, 1, 2, false),\n"
        "        (f_sl, 0, 0, true), (f_sl, 5, 0, false),\n"
        "        (f_sl, 5, 5, true), (f_sl, 0, 5, false),\n"
        "        (f_dec, 2, 0, true), (f_dec, 2, 4, false),\n"
        "        (f_gr, 0, 0, true), (f_gr, 1, 0, false),\n"
        "        (f_gr, 6, 3, true), (f_gr, 7, 3, false),\n"
        "    ];\n"
        "    let mut bad = 0;\n"
        "    for (f, arg, now, want) in cases {\n"
        "        unsafe { __LOMENT_NOW = *now; }\n"
        "        let ok = std::panic::catch_unwind(|| f(*arg)).is_ok();\n"
        "        if ok != *want { bad += 1;\n"
        '            println!("MISMATCH now={} arg={} ok={} want={}", now, arg, ok, want); }\n'
        "    }\n"
        "    unsafe { __LOMENT_NOW = 0; __LOMENT_USED[10] = [0; 64]; }\n"
        "    let c1 = std::panic::catch_unwind(|| f_cons(0)).is_ok();\n"
        "    let c2 = std::panic::catch_unwind(|| f_cons(0)).is_ok();\n"
        "    if !(c1 && !c2) { bad += 1; println!(\"CONSUMED c1={} c2={}\", c1, c2); }\n"
        '    println!("bad={}", bad);\n'
        "}\n")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "m.rs").write_text(rs, encoding="utf-8", newline="\n")
        (d / "main.rs").write_text(main, encoding="utf-8", newline="\n")
        exe = d / ("d.exe" if sys.platform == "win32" else "d")
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(d / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-800:]
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             shell=False).stdout
    assert "bad=0" in out and "MISMATCH" not in out, out
    print("      十种域：形状 3 + 极性 1 + 时律 6，逐例真跑一致")


@test
def test_domains_remaining_kinds_runtime():
    """余下七种域逐例真跑：cyclic / generational / adaptive / subject / witnessed /
    flow / reachable（`docs/306` §5、`docs/307` §1–§5）。

    虚拟钟、质量、票表、义务表都由 main 直接拨到用例要的值 —— 不是"字符串在不在"。
    """
    import shutil
    import subprocess
    src = ("module m\nchoose runtime\n"
           "capability cyc : ring[0..3] cyclic(8)\n"
           "capability gen : buf[0..7] generational(2)\n"
           "capability ad  : trust[0..99] adaptive\n"
           "capability sub : auth[0..15] subject(3)\n"
           "capability wit : nonce[0..15] witnessed(1)\n"
           "capability flw : flow[65538..65540] flow\n"
           "capability rch : lease[0..15] reachable(2)\n"
           "fn f_cyc(n: u32) -> u32 { guard cyc(n); return n; }\n"
           "fn f_gen(n: u32) -> u32 { guard gen(n); return n; }\n"
           "fn f_ad(n: u32) -> u32 { quality ad = 128; guard ad(n); return n; }\n"
           "fn f_adfull(n: u32) -> u32 { quality ad = 255; guard ad(n); return n; }\n"
           "fn f_sub(x: u32, s: u32) -> u32 { guard sub(x, s); return x; }\n"
           "fn f_wit(x: u32, t: u32) -> u32 { guard wit(x, t); return x; }\n"
           "fn f_flw(a: u32, b: u32) -> u32 { guard flw(a, b); return a; }\n"
           "fn f_rch(n: u32) -> u32 { guard rch(n); return n; }\n"
           "fn d_rch(n: u32) -> u32 { discharge rch(n); return n; }\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        assert lomentc.check(mod) == [], lomentc.check(mod)
        rs = lomentc.emit_rust(mod, ROOT)
        ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_qual_store" in ir and "@__loment_discharge" in ir
    assert "quality ad = 128" in src and "__loment_guard2" in rs

    rustc = shutil.which("rustc")
    if not rustc:
        print("      SKIP: 无 rustc（运行那一半）")
        return
    main = (
        'include!("m.rs");\n'
        "fn main() {\n"
        "    std::panic::set_hook(Box::new(|_| {}));\n"
        "    let mut bad = 0;\n"
        "    macro_rules! want { ($e:expr, $w:expr) => { if ($e).is_ok() != $w { bad += 1; } } }\n"
        "    unsafe { __LOMENT_NOW = 0; }\n"
        # cyclic：判的是回绕后的位置
        "    want!(std::panic::catch_unwind(|| f_cyc(9)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_cyc(5)), false);\n"
        "    want!(std::panic::catch_unwind(|| f_cyc(8)), true);\n"
        # generational：每 N tick 极性翻转
        "    want!(std::panic::catch_unwind(|| f_gen(3)), true);\n"
        "    unsafe { __LOMENT_NOW = 2; }\n"
        "    want!(std::panic::catch_unwind(|| f_gen(3)), false);\n"
        "    unsafe { __LOMENT_NOW = 4; }\n"
        "    want!(std::panic::catch_unwind(|| f_gen(3)), true);\n"
        # adaptive：质量 128 把 [0..99] 缩到 [0..49]
        "    want!(std::panic::catch_unwind(|| f_ad(49)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_ad(50)), false);\n"
        "    want!(std::panic::catch_unwind(|| f_adfull(99)), true);\n"
        # subject：主体 1 在白名单，主体 2 不在
        "    want!(std::panic::catch_unwind(|| f_sub(5, 1)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_sub(5, 2)), false);\n"
        # witnessed：同一张票第二次用不了
        "    unsafe { __LOMENT_WIT[4] = [0; 64]; }\n"
        "    want!(std::panic::catch_unwind(|| f_wit(3, 7)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_wit(3, 7)), false);\n"
        "    want!(std::panic::catch_unwind(|| f_wit(3, 8)), true);\n"
        # flow：只放行声明过的边（(1,2)/(1,3) 在 [65538..65540] 里）
        "    want!(std::panic::catch_unwind(|| f_flw(1, 2)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_flw(1, 3)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_flw(2, 2)), false);\n"
        # reachable：占用后不能重复登记；过期结清也拒
        "    unsafe { __LOMENT_NOW = 0; __LOMENT_OBL[6] = [0; 64]; }\n"
        "    want!(std::panic::catch_unwind(|| f_rch(2)), true);\n"
        "    want!(std::panic::catch_unwind(|| f_rch(2)), false);\n"
        "    unsafe { __LOMENT_OBL[6] = [0; 64]; __LOMENT_NOW = 3; }\n"
        "    want!(std::panic::catch_unwind(|| d_rch(2)), false);\n"
        "    unsafe { __LOMENT_NOW = 0; }\n"
        "    want!(std::panic::catch_unwind(|| f_rch(2)), true);\n"
        "    want!(std::panic::catch_unwind(|| d_rch(2)), true);\n"
        '    println!("bad={}", bad);\n'
        "}\n")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "m.rs").write_text(rs, encoding="utf-8", newline="\n")
        (d / "main.rs").write_text(main, encoding="utf-8", newline="\n")
        exe = d / ("d.exe" if sys.platform == "win32" else "d")
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(d / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-800:]
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             shell=False).stdout
    assert "bad=0" in out, out
    print("      余下七种域：cyclic/generational/adaptive/subject/witnessed/flow/reachable 逐例真跑一致")


@test
def test_domain_blocks_scope_and_auto_guard():
    """`docs/313`：`domain … { }` 的作用域、自动守卫、空间标签、嵌套链。

    - 块外 `guard` -> 编译错误；
    - 同空间的内层必须落在（或等于）外层形状里；
    - `in SPACE` 只许给数组 / 切片 / ptr；
    - 块内只有**同空间**的下标访问自动受检；
    - 嵌套链只有最内层记账（外层带 silent 位）。
    """
    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            m = lomentc.load(p)
            return m, lomentc.check(m)

    _, e1 = load("module m\nchoose runtime\n"
                 "fn f(i: u32) -> u32 { domain d : disk[0..4] { } guard d(i); return i; }\n")
    assert any("guard 引用了未声明的能力" in x for x in e1), e1
    _, e2 = load("module m\nchoose runtime\n"
                 "fn f(i: u32) -> u32 { domain o : disk[0..3] { domain n : disk[0..5] { } }"
                 " return i; }\n")
    assert any("不在外层" in x for x in e2), e2
    _, e3 = load("module m\nchoose runtime\n"
                 "fn f() -> u32 { let x: u32 in disk = 1; return x; }\n")
    assert any("不能带空间" in x for x in e3), e3

    m, e = load("module m\nchoose runtime\n"
                "fn f(a: [u32; 8] in disk, b: [u32; 8] in mem, i: u32) -> u32 {\n"
                "    domain d : disk[0..7] { a[i] = 1; b[i] = 2; }\n"
                "    return a[0];\n}\n")
    assert e == [], e
    body = lomentc.emit_llvm(m, ROOT)
    body = body[body.index("define i32 @f"):]
    assert body.count("call void @__loment_guard2") == 1, (
        "自动守卫只该落在同空间的那个访问上")

    m2, e2b = load("module m\nchoose runtime\n"
                   "fn f(a: [u32; 8] in disk, i: u32) -> u32 {\n"
                   "    domain whole : disk[0..7] {\n"
                   "        domain half : disk[0..3] { a[i] = 1; }\n"
                   "    }\n    return a[0];\n}\n")
    assert e2b == [], e2b
    ir2 = lomentc.emit_llvm(m2, ROOT)
    assert ", i64 128, i64 0, i64 0, ptr " in ir2, "外层没有 silent 位"
    assert ", i64 0, i64 0, i64 0, ptr " in ir2, "内层不是正常记账那一支"
    print("      块级域：作用域 / 嵌套包含 / 空间标签 / 自动守卫链，逐条成立")


@test
def test_borrow_domains_phase1_rules():
    """`docs/318` 期 1：`borrow(x)` / `borrow_mut(x)` 两种区域形状 + 三条规则。

    R1 寿命：源必须是可见的名字；R2 权威：共享域不许写、独占域不许再借；
    R3 逃逸：域里借出去的不能 `return`，仍被借用时不能 `free`。

    判据末尾把 `_borrow_domain_errs` **临时关掉**，六个负例必须全部放行 —— 红的
    位置因此确定在这条规则上，不是别的地方。形状本身是新增的，所以"先红后绿"
    这一格只能这样证明（关掉规则 ⇒ 绿的对照），不能靠旧主干。
    """
    def errs(body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b.lomt"
            p.write_text("choose write grammar tur2026.3\nmodule b\nchoose runtime\n\n" + body,
                         encoding="utf-8", newline="\n")
            m = lomentc.load(p)
            return lomentc.check(m, deps=[])

    # 正例：共享域只读；独占域可写（源是 mut 切片）
    assert errs("fn f(xs: [u32]) -> u32 {\n"
                "    domain v : borrow(xs) {\n        return xs[0];\n    }\n}\n") == []
    assert errs("fn f(xs: mut [u32]) -> u32 {\n"
                "    domain e : borrow_mut(xs) {\n        xs[0] = 1;\n        return xs[0];\n"
                "    }\n}\n") == []
    # `freeze` 就是共享域套在 mut 源上：读可以
    assert errs("fn f(xs: mut [u32]) -> u32 {\n"
                "    domain z : borrow(xs) {\n        return xs[0];\n    }\n}\n") == []

    # R2a 共享域不许写（下标 / 内建两条路）
    assert any("只能读 xs" in x for x in errs(
        "fn f(xs: mut [u32]) -> u32 {\n    domain v : borrow(xs) {\n        xs[0] = 1;\n"
        "        return 0;\n    }\n}\n"))
    assert any("只能读 p" in x for x in errs(
        "fn f(p: ptr) -> u32 {\n    domain v : borrow(p) {\n        store8(p, 0, 1);\n"
        "    }\n    return 0;\n}\n"))
    # R2b 独占域不许再借（别名）
    assert any("不能再借 xs" in x for x in errs(
        "fn f(xs: mut [u32]) -> u32 {\n    domain e : borrow_mut(xs) {\n"
        "        let w: [u32] = &xs;\n        return w[0];\n    }\n}\n"))
    # R2a 共享域里连 `&mut` 也不行
    assert any("不能再借 &mut xs" in x for x in errs(
        "fn f(xs: [u32]) -> u32 {\n    domain v : borrow(xs) {\n"
        "        let w: mut [u32] = &mut xs;\n        return w[0];\n    }\n}\n"))
    # R3 借用不能带出、仍被借用时不能 free
    assert any("不能带出" in x for x in errs(
        "fn f(xs: [u32]) -> [u32] {\n    domain v : borrow(xs) {\n        return &xs;\n"
        "    }\n}\n"))
    assert any("不能 free" in x for x in errs(
        "const N: u32 = 8;\nfn f() -> u32 {\n    let p: ptr = alloc(N);\n"
        "    domain v : borrow_mut(p) {\n        free(p);\n    }\n    return 0;\n}\n"))
    # R1 源必须可见
    assert any("不在作用域里" in x for x in errs(
        "fn f(xs: [u32]) -> u32 {\n    domain v : borrow(nope) {\n        return 0;\n}\n}\n"))
    # `domain(` 仍然可以是普通调用 —— 上下文关键字不被抢
    assert not any("域" in x for x in errs(
        "fn f() -> u32 { return 0; }\n"))

    # **红必须红在这条规则上**：把 `_borrow_domain_errs` 临时关掉，所有负例都得能编过
    # —— 否则判据测的是别的东西（`docs/243` 那条"空判据"教训）。
    negatives = (
        "fn f(xs: mut [u32]) -> u32 {\n    domain v : borrow(xs) {\n        xs[0] = 1;\n"
        "        return 0;\n    }\n}\n",
        "fn f(p: ptr) -> u32 {\n    domain v : borrow(p) {\n        store8(p, 0, 1);\n"
        "    }\n    return 0;\n}\n",
        "fn f(xs: mut [u32]) -> u32 {\n    domain e : borrow_mut(xs) {\n"
        "        let w: [u32] = &xs;\n        return w[0];\n    }\n}\n",
        "fn f(xs: [u32]) -> u32 {\n    domain v : borrow(xs) {\n"
        "        let w: mut [u32] = &mut xs;\n        return w[0];\n    }\n}\n",
        "fn f(xs: [u32]) -> [u32] {\n    domain v : borrow(xs) {\n        return &xs;\n"
        "    }\n}\n",
        "const N: u32 = 8;\nfn f() -> u32 {\n    let p: ptr = alloc(N);\n"
        "    domain v : borrow_mut(p) {\n        free(p);\n    }\n    return 0;\n}\n",
    )
    orig = lomentc._borrow_domain_errs
    lomentc._borrow_domain_errs = lambda f, funcs=None: []
    try:
        for body in negatives:
            assert errs(body) == [], (
                "关掉这条规则之后仍然红 —— 那这一格测的不是它:\n" + body + str(errs(body)))
    finally:
        lomentc._borrow_domain_errs = orig
    print("      借用域期 1：view / freeze / exclusive 三条规则逐格成立（关掉即放行）")


@test
def test_borrow_domains_phase2_shapes():
    """`docs/318` 期 2：`window(xs, LO, N)` / `split(xs, LO, N)` / `cursor(xs)`。

    - window：**字面量**下标必须在 `[LO, LO+N)` 内；域里借出去的值仍然不能带出。
    - split：同来源上两段 split **重叠** → E041（`docs/315` 的 `mutex` 边）。
    - cursor：不能把元素借用**存进局部**（没有 NLL，存下来就可能跨轮）。
    - 嵌套域的下标不受外层窗口管（各自的形状各管各的）。

    仍然把规则临时关掉，确认每一格的红都红在这条规则上。
    """
    def errs(body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b2.lomt"
            p.write_text("choose write grammar tur2026.3\nmodule b2\nchoose runtime\n\n" + body,
                         encoding="utf-8", newline="\n")
            m = lomentc.load(p)
            return lomentc.check(m, deps=[])

    # 正例
    assert errs("fn f(xs: mut [u32]) -> u32 {\n    domain w : window(xs, 4, 4) {\n"
                "        xs[5] = 1;\n        return xs[6];\n    }\n}\n") == []
    assert errs("fn f(xs: mut [u32]) -> u32 {\n    domain a : split(xs, 0, 4) {\n"
                "        xs[1] = 1;\n    }\n    domain b : split(xs, 4, 4) {\n"
                "        return xs[5];\n    }\n}\n") == []
    assert errs("fn f(xs: [u32]) -> u32 {\n    domain c : cursor(xs) {\n"
                "        let i: u32 = 0;\n        let s: u32 = 0;\n"
                "        while i < 4 { s = s + xs[i]; i = i + 1; }\n        return s;\n"
                "    }\n}\n") == []
    # 动态下标期 2 不判（运行期检查是期 3）—— 这一格钉住"不误报"
    assert errs("fn f(xs: mut [u32], i: u32) -> u32 {\n    domain w : window(xs, 4, 4) {\n"
                "        return xs[i];\n    }\n}\n") == []

    # 负例
    over = ("fn f(xs: mut [u32]) -> u32 {\n    domain w : window(xs, 4, 4) {\n"
            "        return xs[9];\n    }\n}\n")
    under = ("fn f(xs: mut [u32]) -> u32 {\n    domain w : window(xs, 4, 4) {\n"
             "        return xs[1];\n    }\n}\n")
    overlap = ("fn f(xs: mut [u32]) -> u32 {\n    domain a : split(xs, 0, 5) {\n"
               "        return xs[1];\n    }\n    domain b : split(xs, 4, 4) {\n"
               "        return xs[5];\n    }\n}\n")
    stored = ("fn f(xs: [u32]) -> u32 {\n    domain c : cursor(xs) {\n"
              "        let p: [u32] = &xs;\n        return p[0];\n    }\n}\n")
    assert any("越窗" in x for x in errs(over))
    assert any("越窗" in x for x in errs(under))
    assert any("重叠" in x for x in errs(overlap))
    assert any("游标域" in x for x in errs(stored))

    # 嵌套：内层窗口的下标不受外层窗口管
    nested = ("fn f(xs: mut [u32]) -> u32 {\n    domain a : split(xs, 0, 4) {\n"
              "        domain b : split(xs, 4, 4) {\n            return xs[5];\n"
              "        }\n    }\n}\n")
    assert errs(nested) == [], errs(nested)

    orig = lomentc._borrow_domain_errs
    lomentc._borrow_domain_errs = lambda f, funcs=None: []
    try:
        for body in (over, under, overlap, stored):
            assert errs(body) == [], ("关掉这条规则之后仍然红 —— 那这一格测的不是它:\n"
                                      + body + str(errs(body)))
    finally:
        lomentc._borrow_domain_errs = orig
    print("      借用域期 2：window / split / cursor 三种形状逐格成立（关掉即放行）")


@test
def test_borrow_domains_phase3_shapes():
    """`docs/318` 期 3：`loan` / `epoch` / `session` / `seal`。

    - loan：块内**不能用**被借出的名字（块外恢复）；
    - epoch：块内借出去的值不能 `return` 带出；
    - session：块内恰好用一次；块**之后**不许再用（所有权已经走了）；
    - seal：块内可以把 buf 交给外部，块**之后**不行。
    """
    def errs(body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b3.lomt"
            p.write_text("choose write grammar tur2026.3\nmodule b3\nchoose runtime\n\n" + body,
                         encoding="utf-8", newline="\n")
            m = lomentc.load(p)
            return lomentc.check(m, deps=[])

    # 正例
    assert errs("fn f(xs: [u32], p: [u32]) -> u32 {\n    domain l : loan(xs) {\n"
                "        return p[0];\n    }\n}\n") == []
    assert errs("fn f(xs: [u32]) -> u32 {\n    domain ep : epoch {\n"
                "        return xs[0];\n    }\n}\n") == []
    assert errs("fn f(c: u32) -> u32 {\n    domain s : session(c) {\n"
                "        return c;\n    }\n}\n") == []
    assert errs("fn f(buf: ptr) -> u32 {\n    domain sl : seal(buf) {\n"
                "        syscall4(0, 1, buf as u64, 8);\n    }\n    return 0;\n}\n") == []

    loan = ("fn f(xs: [u32]) -> u32 {\n    domain l : loan(xs) {\n"
            "        return xs[0];\n    }\n}\n")
    epoch = ("fn f(xs: [u32]) -> [u32] {\n    domain ep : epoch {\n"
             "        return &xs;\n    }\n}\n")
    twice = ("fn f(c: u32) -> u32 {\n    domain s : session(c) {\n"
             "        let a: u32 = c;\n        let b: u32 = c;\n        return a + b;\n    }\n}\n")
    after = ("fn f(c: u32) -> u32 {\n    domain s : session(c) {\n"
             "        let a: u32 = c;\n        return a;\n    }\n    return c;\n}\n")
    unsealed = ("fn f(buf: ptr) -> u32 {\n    domain sl : seal(buf) {\n"
                "        let x: u32 = 1;\n    }\n    syscall4(0, 1, buf as u64, 8);\n"
                "    return 0;\n}\n")
    assert any("块内不能再用" in x for x in errs(loan))
    assert any("不能带出" in x for x in errs(epoch))
    assert any("已经移交过" in x for x in errs(twice))
    assert any("所有权已经随" in x for x in errs(after))
    assert any("不能把 buf 交给外部" in x for x in errs(unsealed))

    orig = lomentc._borrow_domain_errs
    lomentc._borrow_domain_errs = lambda f, funcs=None: []
    try:
        for body in (loan, epoch, twice, after, unsealed):
            assert errs(body) == [], ("关掉这条规则之后仍然红 —— 那这一格测的不是它:\n"
                                      + body + str(errs(body)))
    finally:
        lomentc._borrow_domain_errs = orig
    print("      借用域期 3：loan / epoch / session / seal 逐格成立（关掉即放行）")


@test
def test_domain_blocks_per_instance_state():
    """`docs/313` §4：块 = 实例。循环每轮、递归每层各自一份状态（Rust 后端真跑）。"""
    import shutil
    import subprocess
    src = ("module m\nchoose runtime\n"
           "fn f(n: u32) -> u32 {\n"
           "    let a: [u32; 4] in disk = [0, 0, 0, 0];\n"
           "    let i: u32 = 0;\n"
           "    while i < n {\n"
           "        domain tok : disk[0..3] consumed(1) { a[0] = i; }\n"
           "        i = i + 1;\n"
           "    }\n    return n;\n}\n"
           "fn g(n: u32) -> u32 {\n"
           "    if n == 0 { return 0; }\n"
           "    let a: [u32; 4] in disk = [0, 0, 0, 0];\n"
           "    domain one : disk[0..3] consumed(1) { a[0] = 1; }\n"
           "    return g(n - 1);\n}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        assert lomentc.check(mod) == [], lomentc.check(mod)
        rs = lomentc.emit_rust(mod, ROOT)
    rustc = shutil.which("rustc")
    if not rustc:
        print("      SKIP: 无 rustc（运行那一半）")
        return
    main = ('include!("m.rs");\n'
            "fn main() {\n"
            "    std::panic::set_hook(Box::new(|_| {}));\n"
            "    let mut bad = 0;\n"
            "    if std::panic::catch_unwind(|| f(3)).is_err() { bad += 1; }\n"
            "    if std::panic::catch_unwind(|| g(3)).is_err() { bad += 1; }\n"
            '    println!("bad={}", bad);\n'
            "}\n")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "m.rs").write_text(rs, encoding="utf-8", newline="\n")
        (d / "main.rs").write_text(main, encoding="utf-8", newline="\n")
        exe = d / ("d.exe" if sys.platform == "win32" else "d")
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(d / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-800:]
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             shell=False).stdout
    assert "bad=0" in out, out
    print("      块级域：循环每轮 / 递归每层都是新实例（consumed 每轮都通过）")


@test
def test_domain_for_is_the_loop_head():
    """`docs/315` §2：`domain:for` 是**域上的循环**，不是体里另一次守卫。

    - 守卫只落在循环头**一次** —— 体内对同一位置的 `a[i]` 不再挂钩（有状态的域
      否则会被扣两次）；
    - 回边推虚拟钟（`@__loment_tick`）；`choose domain_clock_explicit` 关掉它；
    - 无界形状 / `danger` / 要第二实参的语境类没有"下一个位置"，编译期拒。
    """
    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            m = lomentc.load(p)
            return m, lomentc.check(m)

    def fbody(ir: str) -> str:
        return ir[ir.index("define i32 @f"):]

    m, e = load("module m\nchoose runtime\n"
                "fn f(a: [u32; 8] in disk) -> u32 {\n"
                "    domain d : disk[0..7] consumed(1) {\n"
                "        domain:for i { a[i] = a[i] + 1; }\n"
                "    }\n    return a[0];\n}\n")
    assert e == [], e
    body = fbody(lomentc.emit_llvm(m, ROOT))
    assert body.count("call void @__loment_guard2") == 1, (
        "循环头该只有一次守卫（体内那个下标已被证过）\n" + body)
    assert "icmp ule i32" in body, "循环上界不是域的形状"
    assert "call void @__loment_tick()" in body, "回边没有推钟"

    m2, e2 = load("module m\nchoose runtime\nchoose domain_clock_explicit\n"
                  "fn f(a: [u32; 8] in disk) -> u32 {\n"
                  "    domain d : disk[0..7] {\n"
                  "        domain:for i { a[i] = 1; }\n"
                  "    }\n    return a[0];\n}\n")
    assert e2 == [], e2
    assert "call void @__loment_tick()" not in fbody(lomentc.emit_llvm(m2, ROOT)), (
        "`domain_clock_explicit` 没关掉回边推钟")

    m3, e3 = load("module m\nchoose runtime\n"
                  "fn f(a: [u32; 8] in disk) -> u32 {\n"
                  "    domain d : disk[0..7] {\n"
                  "        domain:for i step 2 { a[i] = 1; }\n"
                  "    }\n    return a[0];\n}\n")
    assert e3 == [], e3
    b3 = fbody(lomentc.emit_llvm(m3, ROOT))
    assert "add i32" in b3 and ", 2" in b3, "步长没进更新步"

    # 嵌套：`domain:for` 是透明的一层 —— 体内照旧能看见外面的域
    m4, e4 = load("module m\nchoose runtime\n"
                  "fn f(a: [u32; 8] in disk) -> u32 {\n"
                  "    domain whole : disk[0..7] {\n"
                  "        domain half : disk[0..3] consumed(1) {\n"
                  "            domain:for i { guard half(i); a[i] = 1; }\n"
                  "        }\n"
                  "    }\n    return a[0];\n}\n")
    assert e4 == [], e4

    # `in 外层`：循环走外层的形状，但**最内层仍更窄** —— 体内访问不能被免掉。
    m5, e5 = load("module m\nchoose runtime\n"
                  "fn f(a: [u32; 8] in disk) -> u32 {\n"
                  "    domain whole : disk[0..7] {\n"
                  "        domain half : disk[0..3] {\n"
                  "            domain:for i in whole { a[i] = 1; }\n"
                  "        }\n"
                  "    }\n    return a[0];\n}\n")
    assert e5 == [], e5
    b5 = fbody(lomentc.emit_llvm(m5, ROOT))
    assert b5.count("call void @__loment_guard2") == 3, (
        "外层循环头 1 条 + 体内整条链 2 条（外层 silent + 内层记账）\n" + b5)

    for bad, frag in [
        ("domain d : disk[()..7] { domain:for i { } }", "有限形状"),
        ("domain d : disk[0..7] danger { domain:for i { } }", "危险域"),
        ("domain d : auth[0..7] subject(3) { domain:for i { } }", "第二个实参"),
    ]:
        try:
            parse("module m\nchoose runtime\nfn f() -> u32 {\n    "
                  + bad + "\n    return 0;\n}\n")
        except lomentc.LomError as ex:
            assert frag in str(ex), (frag, str(ex))
        else:
            raise AssertionError(f"没有拒：{bad}")
    print("      域循环：头一次守卫 / 回边 tick / 显式档 / 步长 / 透明嵌套 / 三类拒绝")


@test
def test_domain_tree_and_graph_edges():
    """`docs/315` §4–§5：树边（显式链 / `child_of`）与图边（`couple` / `mutex`）。

    - 显式 `guard half(x)` 也判祖先（silent），只有叶子记账；
    - `child_of` 必须连到包围它的同空间父域、且子 ⊆ 父；
    - `couple` 的两个域必须共享位置；守卫多出一条 silent 的伙伴判决；自动守卫同理；
    - `mutex` 要求两个形状可证不相交。
    """
    NL = chr(10)

    def S(*lines):
        return NL.join(lines)

    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.lomt"
            p.write_text(src, encoding="utf-8", newline=NL)
            m = lomentc.load(p)
            return m, lomentc.check(m)

    def fbody(ir):
        return ir[ir.index("define i32 @f"):]

    # 1) 显式守卫走整条链
    m, e = load(S("module m", "choose runtime",
                  "fn f(a: [u32; 8] in disk) -> u32 {",
                  "    domain whole : disk[0..7] {",
                  "        domain half : disk[0..3] { guard half(2); }",
                  "    }",
                  "    return a[0];", "}", ""))
    assert e == [], e
    b = fbody(lomentc.emit_llvm(m, ROOT))
    assert b.count("call void @__loment_guard2") == 2, b
    assert ", i64 128, i64" in b, "祖先没有 silent 位"

    # 2) child_of：好形状 / 三种拒绝
    m2, e2 = load(S("module m", "choose runtime",
                    "fn f(a: [u32; 8] in disk) -> u32 {",
                    "    domain whole : disk[0..7] {",
                    "        domain half : disk[0..3] child_of whole { a[0] = 1; }",
                    "    }",
                    "    return a[0];", "}", ""))
    assert e2 == [], e2
    for bad, frag in [
        ("domain whole : disk[0..7] { domain half : disk[0..9] child_of whole { } }",
         "C1"),
        ("domain whole : disk[0..7] { domain half : mem[0..3] child_of whole { } }",
         "同一空间"),
        ("domain half : disk[0..3] child_of ghost { }", "不在外层作用域"),
    ]:
        try:
            parse(S("module m", "choose runtime", "fn f() -> u32 {", "    " + bad,
                    "    return 0;", "}", ""))
        except lomentc.LomError as ex:
            assert frag in str(ex), (frag, str(ex))
        else:
            raise AssertionError("没有拒：" + bad)

    # 3) couple：共享位置 + 伙伴 silent 判决（显式与自动两条路）+ 质量整组
    m3, e3 = load(S("module m", "choose runtime",
                    "capability top : disk[0..15] adaptive",
                    "fn f(a: [u32; 16] in disk) -> u32 {",
                    "    domain d : disk[0..7] adaptive {",
                    "        couple d, top;",
                    "        guard d(2);",
                    "        a[2] = 1;",
                    "        quality d = 128;",
                    "    }",
                    "    return a[2];", "}", ""))
    assert e3 == [], e3
    b3 = fbody(lomentc.emit_llvm(m3, ROOT))
    assert b3.count("i64 138, i64") >= 2, "伙伴没有 silent 判决（kind|128）"
    assert b3.count("call void @__loment_qual_store") >= 2, "质量没有写到整组"
    def rejects(src_bad, frag):
        try:
            m_, _e = load(src_bad)
        except lomentc.LomError as ex:
            assert frag in str(ex), (frag, str(ex))
            return
        raise AssertionError("没有拒：" + frag)

    rejects(S("module m", "choose runtime", "capability top : disk[8..15]",
              "fn f() -> u32 { domain d : disk[0..7] { couple d, top; } return 0; }", ""),
            "共同位置")

    # 4) mutex：可证不相交才行
    parse(S("module m", "choose runtime", "capability x : disk[0..3]",
            "capability y : disk[4..7]",
            "fn f() -> u32 { mutex x, y; return 0; }", ""))
    rejects(S("module m", "choose runtime", "capability x : disk[0..5]",
              "capability y : disk[4..7]",
              "fn f() -> u32 { mutex x, y; return 0; }", ""), "重叠")

    # 5) 横向并行 / 反向
    m5, e5 = load(S("module m", "choose runtime",
                    "fn f(a: [u32; 8] in disk, b: [u32; 8] in mem) -> u32 {",
                    "    domain da : disk[0..3] {",
                    "        domain db : mem[0..3] {",
                    "            domain:for i, j in da, db { a[i] = 1; b[j] = 2; }",
                    "        }",
                    "    }",
                    "    domain rv : disk[0..3] { domain:for i step -2 { a[i] = 3; } }",
                    "    return a[0];", "}", ""))
    assert e5 == [], e5
    b5 = fbody(lomentc.emit_llvm(m5, ROOT))
    assert "icmp uge i32" in b5, "反向步长没有翻条件"
    assert b5.count("call void @__loment_guard2") >= 3, "并行循环每轮该判两个域"
    for bad, frag in [
        ("domain p : disk[0..3] { domain q : mem[0..7] { domain:for i, j in p, q { } } }",
         "跨度不一致"),
        ("domain p : disk[0..3] { domain:for i, k in p { } }", "一一对应"),
    ]:
        try:
            parse(S("module m", "choose runtime",
                    "fn f(a: [u32; 8] in disk, b: [u32; 8] in mem) -> u32 {",
                    "    " + bad, "    return 0;", "}", ""))
        except lomentc.LomError as ex:
            assert frag in str(ex), (frag, str(ex))
        else:
            raise AssertionError("没有拒：" + bad)
    print("      树/图：显式链 / child_of / couple / mutex / 并行 / 反向，逐条成立")


@test
def test_solid_body_is_a_first_class_unit():
    """`docs/315` §5.2：`solid 名 : A, B;` —— 立体图的体。

    体 = couple + 名字：`guard 名(x)` 判根成员、其余静默；`quality 名 = q` 写到整组；
    几何报告里成一条 `solids` 条目。体的图**按函数**求分量 —— 另一个函数里的块域
    不能被算进伙伴（那会引用一个悬空的槽）。
    """
    NL = chr(10)

    def S(*lines):
        return NL.join(lines)

    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.lomt"
            p.write_text(src, encoding="utf-8", newline=NL)
            m = lomentc.load(p)
            return m, lomentc.check(m)

    src = S("module m", "choose runtime",
            "capability ring    : disk[0..15] adaptive",
            "capability partner : disk[0..15] adaptive",
            "fn f(a: [u32; 16] in disk) -> u32 {",
            "    solid body : ring, partner;",
            "    guard body(3);",
            "    quality body = 128;",
            "    return a[0];", "}", "")
    m, e = load(src)
    assert e == [], e
    ir = lomentc.emit_llvm(m, ROOT)
    body = ir[ir.index("define i32 @f"):]
    assert body.count("call void @__loment_guard2") == 2, body     # 根 + 静默伙伴
    assert "i64 138, i64" in body, "伙伴不是 silent（kind|128）"
    assert body.count("call void @__loment_qual_store") == 2, body  # 质量整组
    g = json.loads(loment_geometry.geometry(m).to_json())
    assert [x["name"] for x in g["solids"]] == ["body"], g["solids"]
    assert g["solids"][0]["root"] == g["solids"][0]["members"][0], g["solids"]

    # 按函数隔离：g 里把 ring 和块域 d 结成一个体，f 的守卫不能把 d 算进去
    src2 = S("module m", "choose runtime",
             "capability ring    : disk[0..15] adaptive",
             "capability partner : disk[0..15]", "",
             "fn f(a: [u32; 16] in disk) -> u32 {",
             "    couple ring, partner;",
             "    guard ring(3);",
             "    return a[0];", "}",
             "fn g(a: [u32; 16] in disk) -> u32 {",
             "    domain d : disk[0..7] {",
             "        solid inner_body : d, ring;",
             "        guard inner_body(2);",
             "    }",
             "    return a[0];", "}", "")
    m2, e2 = load(src2)
    assert e2 == [], e2
    ir2 = lomentc.emit_llvm(m2, ROOT)
    f_ir = ir2[ir2.index("define i32 @f"):ir2.index("define i32 @g")]
    # d 的审计槽号 = 能力数 2 + 第一个块 = 2；f 的守卫不该出现它
    assert "i64 2, i64 %" not in f_ir, "f 的守卫把另一个函数的块算进了伙伴"
    g_ir = ir2[ir2.index("define i32 @g"):]
    assert "i64 0, i64 %" in g_ir, g_ir
    print("      体的图：根 + 静默成员 / 质量整组 / 几何 solids / 按函数隔离，逐条成立")


@test
def test_revoke_cascades_down_the_domain_tree():
    """`docs/306` §6.1 C2：撤销级联（撤父 ⇒ 整棵子树拒绝）。

    C1（封顶）由形状包含在编译期证；C2 是**运行期**的那一条 —— 它要的是
    "撤销之后整棵子树再也不放行"，而且撤销位本身不能被复用成"没撤过"。

    四条判据：

    1. `revoke NAME;` 发 `@__loment_revoke(ptr …, i64 0, i64 1)`；带位置那份是
       `(ptr, %v64, 0)` —— 位置按 `idx & 63` 记在位图里；
    2. **级联不需要传播指令**：守卫本来就走整条竖向链，链上每一层各自带自己的
       撤销位对 —— 撤销父之后，子域那条链上的父判决当场就拒；
    3. 顶层能力的撤销位在全局表（`@__loment_rev`，墓碑）；块域实例的在栈上
       （`alloca [2 x i64]`，块入口清零 = 新实例）；不可撤的域指向共享零格
       （`@__loment_revfree`），于是普通程序一个字节不多发；
    4. 体名撤**全组成员**（块在前、能力在后 —— 与自举侧的枚举同序）。
    """
    NL = chr(10)

    def S(*lines):
        return NL.join(lines)

    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.lomt"
            p.write_text(src, encoding="utf-8", newline=NL)
            m = lomentc.load(p)
            return m, lomentc.check(m)

    # 1) 顶层能力：整实例撤销 + 撤销后的守卫仍带撤销位
    m1, e1 = load(S("module m", "choose runtime",
                    "capability ring  : disk[0..15] revocable",
                    "capability plain : disk[0..15]",
                    "fn f(a: [u32; 16] in disk) -> u32 {",
                    "    guard ring(3);",
                    "    revoke ring;",
                    "    guard ring(4);",
                    "    guard plain(5);",
                    "    return a[0];", "}", ""))
    assert e1 == [], e1
    b1 = lomentc.emit_llvm(m1, ROOT)
    b1 = b1[b1.index("define i32 @f"):]
    assert b1.count("call void @__loment_revoke(ptr") == 1, b1
    assert "call void @__loment_revoke(ptr %t" in b1, "撤销没走可撤域那一格"
    assert ", i64 0, i64 1)" in b1, "整实例撤销的两个实参不对"
    # 可撤的能力 → 全局表里的那一格；不可撤的 → 共享零格
    assert "getelementptr [64 x [2 x i64]], ptr @__loment_rev, i64 0, i64 0" in b1, b1
    assert "@__loment_revfree" in b1, "不可撤的域应该指向共享零格"

    # 2) 块域 + 位置撤销：位图那一份；块实例的撤销位在栈上
    m2, e2 = load(S("module m", "choose runtime",
                    "fn f(a: [u32; 8] in disk) -> u32 {",
                    "    domain d : disk[0..7] revocable {",
                    "        revoke d(5);",
                    "        guard d(6);",
                    "    }",
                    "    return a[0];", "}", ""))
    assert e2 == [], e2
    b2 = lomentc.emit_llvm(m2, ROOT)
    b2 = b2[b2.index("define i32 @f"):]
    assert "alloca [2 x i64]" in b2, "可撤的块没有分撤销位"
    assert b2.count("call void @__loment_revoke(ptr") == 1, b2
    assert ", i64 0)" in b2, "位置撤销的第二个实参应该是 0（all）"

    # 3) 级联：父可撤、子不可撤 —— 守卫子的那条链上，父那一层带父的撤销位
    m3, e3 = load(S("module m", "choose runtime",
                    "fn f(a: [u32; 8] in disk) -> u32 {",
                    "    domain outer : disk[0..7] revocable {",
                    "        domain inner : disk[0..3] child_of outer {",
                    "            guard inner(2);",
                    "            revoke outer;",
                    "            guard inner(3);",
                    "        }",
                    "    }",
                    "    return a[0];", "}", ""))
    assert e3 == [], e3
    b3 = lomentc.emit_llvm(m3, ROOT)
    b3 = b3[b3.index("define i32 @f"):]
    assert b3.count("call void @__loment_guard2") == 4, b3   # 两次守卫 × 链上两层
    # 链上父那一层（silent）与子那一层：一个带可撤域、一个指向共享零格
    for line in b3.splitlines():
        if "call void @__loment_guard2" in line and "i64 128" in line:
            assert ", ptr %t" in line, "父那一层没带自己的撤销位：" + line

    # 4) 体名 = 全组成员（块在前、能力在后）
    m4, e4 = load(S("module m", "choose runtime",
                    "capability ring : disk[0..15] revocable",
                    "fn f(a: [u32; 16] in disk) -> u32 {",
                    "    domain leaf : disk[0..7] revocable {",
                    "        solid body : leaf, ring;",
                    "        revoke body;",
                    "    }",
                    "    return a[0];", "}", ""))
    assert e4 == [], e4
    b4 = lomentc.emit_llvm(m4, ROOT)
    b4 = b4[b4.index("define i32 @f"):]
    assert b4.count("call void @__loment_revoke(ptr") == 2, b4
    i_block = b4.index("call void @__loment_revoke(ptr %t")
    i_cap = b4.index("call void @__loment_revoke(ptr %t", i_block + 1)
    assert i_block < i_cap, "体的撤销顺序应是块在前、能力在后"

    # 5) 拒绝：没声明 revocable / 名字不存在 / 体里有不可撤的成员
    def rejects(src_bad, frag):
        try:
            m_, _e = load(src_bad)
        except lomentc.LomError as ex:
            assert frag in str(ex), (frag, str(ex))
            return
        raise AssertionError("没有拒：" + frag)

    rejects(S("module m", "choose runtime", "capability c : disk[0..15]",
              "fn f() -> u32 { revoke c; return 0; }", ""), "revocable")
    rejects(S("module m", "choose runtime",
              "fn f() -> u32 { revoke nope; return 0; }", ""), "未声明的域")
    rejects(S("module m", "choose runtime", "capability c : disk[0..15]",
              "fn f() -> u32 { domain d : disk[0..7] revocable {",
              "    solid body : d, c;", "    revoke body;", "}",
              "return 0; }", ""), "revocable")

    # 6) 几何报告里能读出来：可撤标记 + 每个撤销点的目标
    g = json.loads(loment_geometry.geometry(m4).to_json())
    nodes = {x["name"]: x for x in g["nodes"]}
    assert nodes["leaf"]["revocable"] is True, nodes["leaf"]
    assert nodes["ring"]["revocable"] is True, nodes["ring"]
    (rv,) = g["revokes"]
    assert rv["position"] is False, rv
    assert sorted(rv["targets"]) == sorted([nodes["leaf"]["id"], nodes["ring"]["id"]]), rv
    print("      C2：撤销 / 级联 / 位置位图 / 体整组 / 解析期拒绝 / 几何 revokes，逐条成立")


@test
def test_domain_geometry_reads_trees_and_graphs():
    """`docs/315` §4–§5：横/竖树、平/立体图 —— 从代码里读出来的**确定性**形状。

    一棵三层竖链（`contains`）+ 一条兄弟边（`sibling`）+ 一个分支 + 一条
    `domain:for` 的位置轴；立体图多出父 → 孙的 `cross` 边。图那一半再加上顶层
    capability 顶点、`child_of` / `couple` / `mutex` 三种边。
    """
    NL = chr(10)

    def S(*lines):
        return NL.join(lines)

    def load(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.lomt"
            p.write_text(src, encoding="utf-8", newline=NL)
            m = lomentc.load(p)
            return m, lomentc.check(m)

    src = S("module geo", "choose runtime",
            "fn f(a: [u32; 8] in disk) -> u32 {",
            "    domain whole : disk[0..7] {",
            "        domain half : disk[0..3] {",
            "            domain leaf : disk[0..1] consumed(1) {",
            "                domain:for i step 2 { a[i] = 1; }",
            "            }",
            "        }",
            "        domain tail : disk[4..7] { a[4] = 2; }",
            "    }",
            "    if a[0] > 0 { domain side : mem[0..1] { a[1] = 3; } }",
            "    return a[0];", "}", "")
    mod, e = load(src)
    assert e == [], e
    g = loment_geometry.geometry(mod)
    out1 = g.to_json()
    out2 = loment_geometry.geometry(mod).to_json()
    assert out1 == out2, "几何不是确定性的"
    d = json.loads(out1)
    names = {n["name"]: n for n in d["nodes"]}
    assert set(names) == {"whole", "half", "leaf", "tail", "side"}, names
    kinds = sorted(e["kind"] for e in d["edges"])
    assert kinds.count("contains") == 3, kinds          # whole→half→leaf + whole→tail
    assert kinds.count("sibling") == 1, kinds           # half 与 tail 同层
    assert kinds.count("cross") == 1, kinds             # 立体图：whole → leaf
    assert d["branches"] == ["f:12"], d["branches"]
    (loop,) = d["loops"]
    assert loop["domain"] == names["leaf"]["id"] and loop["step"] == 2, loop
    assert loop["positions"] == 1, loop                 # 0..1 间隔 2：只有一个位置

    # 图边：顶层 capability 也是顶点；child_of / couple / mutex 各自成边
    src2 = S("module g", "choose runtime",
             "capability top : disk[0..15] adaptive",
             "capability other : mem[16..19]",
             "fn f(a: [u32; 16] in disk) -> u32 {",
             "    domain d : disk[0..7] {",
             "        domain inner : disk[0..3] child_of d {",
             "            domain twin : mem[0..3] {",
             "                couple d, top;",
             "                mutex top, other;",
             "                domain:for i, j in inner, twin { a[i] = 1; }",
             "            }",
             "        }",
             "    }",
             "    return a[0];", "}", "")
    m2, e2 = load(src2)
    assert e2 == [], e2
    g2 = json.loads(loment_geometry.geometry(m2).to_json())
    kinds2 = {(x["src"], x["dst"], x["kind"]) for x in g2["edges"]}
    ids2 = {n["name"]: n["id"] for n in g2["nodes"]}
    assert (ids2["top"], ids2["other"], "mutex") in kinds2, kinds2
    assert (ids2["top"], ids2["d"], "couple") in kinds2, kinds2   # 边按 (src, dst) 排序
    assert (ids2["d"], ids2["inner"], "child_of") in kinds2, kinds2
    assert len(g2["loops"]) == 2, g2["loops"]            # 并行的两个位置轴
    print("      域几何：竖链 / 兄弟 / 分支 / 位置轴 / 跨层 / couple / mutex，确定性成立")


@test
def test_domains_selfhost_twin():
    """十种域 + `tick;`：自举镜与参考实现的 IR **逐字节相同**。

    这条真跑自举链（种子 -> stage1），不是字符串比对。没有 clang 就跳过。
    """
    import shutil
    import subprocess
    import loment_dist
    clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    if not (clang and Path(clang).exists()):
        print("      SKIP: 无 clang")
        return
    src = ("module dom_twin\nchoose runtime\n"
           "capability reg   : disk[2..4]\n"
           "capability front : disk[()..3]\n"
           "capability back  : disk[5..()]\n"
           "capability allc  : disk[()..()]\n"
           "capability dng   : mem[2..4] danger\n"
           "capability ep    : quota[0..4] epoch(3)\n"
           "capability farc  : slot[0..()] far(2)\n"
           "capability sl    : trace[0..100] sliding(2)\n"
           "capability dec   : lease[0..4] decaying(3)\n"
           "capability gr    : cred[0..()] growing(2)\n"
           "capability cons  : auth[0..1] consumed(1)\n"
           "fn f_reg(n: u32) -> u32 { guard reg(n); return n; }\n"
           "fn f_front(n: u32) -> u32 { guard front(n); return n; }\n"
           "fn f_back(n: u32) -> u32 { guard back(n); return n; }\n"
           "fn f_allc(n: u32) -> u32 { guard allc(n); return n; }\n"
           "fn f_dng(n: u32) -> u32 { guard dng(n); return n; }\n"
           "fn f_ep(n: u32) -> u32 { guard ep(n); return n; }\n"
           "fn f_farc(n: u32) -> u32 { guard farc(n); return n; }\n"
           "fn f_sl(n: u32) -> u32 { guard sl(n); return n; }\n"
           "fn f_dec(n: u32) -> u32 { guard dec(n); return n; }\n"
           "fn f_gr(n: u32) -> u32 { guard gr(n); return n; }\n"
           "fn f_cons(n: u32) -> u32 { guard cons(n); return n; }\n"
           "fn f_tick(n: u32) -> u32 { tick; return n; }\n"
           # 余下七种（docs/306 §5、docs/307 §1–§5）：形状、极性、第二实参、两条语句
           "capability cyc   : ring[0..3] cyclic(8)\n"
           "capability gen   : buf[0..7] generational(2)\n"
           "capability ad    : trust[0..99] adaptive\n"
           "capability sub   : auth[0..15] subject(3)\n"
           "capability wit   : nonce[0..15] witnessed(1)\n"
           "capability flw   : flow[65538..65540] flow\n"
           "capability rch   : lease[0..15] reachable(2)\n"
           "fn g_cyc(n: u32) -> u32 { guard cyc(n); return n; }\n"
           "fn g_gen(n: u32) -> u32 { guard gen(n); return n; }\n"
           "fn g_ad(n: u32) -> u32 { quality ad = 128; guard ad(n); return n; }\n"
           "fn g_sub(x: u32, s: u32) -> u32 { guard sub(x, s); return x; }\n"
           "fn g_wit(x: u32, t: u32) -> u32 { guard wit(x, t); return x; }\n"
           "fn g_flw(a: u32, b: u32) -> u32 { guard flw(a, b); return a; }\n"
           "fn g_rch(n: u32) -> u32 { guard rch(n); return n; }\n"
           "fn g_dis(n: u32) -> u32 { discharge rch(n); return n; }\n"
           # docs/313：块级域 + 空间标签 + 自动守卫（嵌套链、有状态实例）
           "fn h_dom(a: [u32; 8] in disk, i: u32) -> u32 {\n"
           "    domain whole : disk[0..7] {\n"
           "        domain half : disk[0..3] consumed(1) { a[i] = 1; }\n"
           "    }\n"
           "    return a[0];\n"
           "}\n"
           # docs/315：域循环（循环头一次守卫 + 回边 tick；体内不重复守卫）
           "fn h_loop(a: [u32; 8] in disk) -> u32 {\n"
           "    domain loopd : disk[0..7] consumed(1) {\n"
           "        domain:for i step 2 { a[i] = a[i] + 1; }\n"
           "    }\n"
           "    return a[0];\n"
           "}\n"
           # `in 外层`：最内层更窄，体内访问仍挂整条链（docs/315 §1.4）
           "fn h_loop2(a: [u32; 8] in disk) -> u32 {\n"
           "    domain outer2 : disk[0..7] {\n"
           "        domain inner2 : disk[0..3] {\n"
           "            domain:for i in outer2 { a[i] = 1; }\n"
           "        }\n"
           "    }\n"
           "    return a[0];\n"
           "}\n"
           # docs/315 §4–§5：树边与图边（child_of / couple / mutex / 并行 / 反向）
           "capability cpa : disk[0..15] adaptive\n"
           "capability cpb : mem[16..19]\n"
           "fn h_graph(a: [u32; 16] in disk, b: [u32; 4] in mem) -> u32 {\n"
           "    mutex cpa, cpb;\n"
           "    domain p : disk[0..3] {\n"
           "        couple p, cpa;\n"
           "        domain q : mem[0..3] {\n"
           "            domain:for i, j in p, q { a[i] = 1; b[j] = 2; }\n"
           "        }\n"
           "    }\n"
           "    return a[0];\n"
           "}\n"
           "fn h_child(a: [u32; 8] in disk) -> u32 {\n"
           "    domain outer3 : disk[0..7] {\n"
           "        domain leaf3 : disk[0..3] child_of outer3 { a[0] = 1; }\n"
           "    }\n"
           "    domain rv3 : disk[0..3] { domain:for i step -2 { a[i] = 3; } }\n"
           "    return a[0];\n"
           "}\n"
           # docs/315 §5.2：立体图的体（根 + 静默成员 / 质量整组 / 按函数隔离）
           "capability cpc : disk[0..15] adaptive\n"
           "fn h_solid(a: [u32; 16] in disk) -> u32 {\n"
           "    solid body : cpa, cpc;\n"
           "    guard body(4);\n"
           "    quality body = 64;\n"
           "    return a[0];\n"
           "}\n"
           "fn h_solid2(a: [u32; 16] in disk) -> u32 {\n"
           "    domain dd : disk[0..7] {\n"
           "        solid b2 : dd, cpa;\n"
           "        guard b2(2);\n"
           "    }\n"
           "    return a[0];\n"
           "}\n"
           # docs/306 §6.1 C2：撤销（整实例 / 按位置 / 体整组）与链上的级联
           "capability cpr : disk[0..15] revocable\n"
           "fn h_rev(a: [u32; 16] in disk) -> u32 {\n"
           "    domain rd : disk[0..7] revocable {\n"
           "        solid rb : rd, cpr;\n"
           "        guard rb(3);\n"
           "        revoke rb;\n"
           "        guard rd(4);\n"
           "    }\n"
           "    revoke cpr(5);\n"
           "    guard cpr(6);\n"
           "    return a[0];\n"
           "}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "d.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        assert lomentc.check(mod, deps=deps) == [], lomentc.check(mod, deps=deps)[:2]
        want = lomentc.emit_llvm(mod, ROOT, deps)
        want_p = lomentc.emit_potato(mod, ROOT, deps)
    stage1 = loment_dist.build_stage1()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "d.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        r = subprocess.run([str(stage1), str(p)], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           shell=False, timeout=600)
        rp = subprocess.run([str(stage1), str(p), "--emit-potato"], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译十种域失败: {r.stderr[-600:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, (
        f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)")
    assert rp.returncode == 0, f"stage1 Potato 出口失败: {rp.stderr[-600:]}"
    got_p = rp.stdout.replace("\r\n", "\n")
    assert got_p == want_p, (
        f"自举镜与参考的 Potato 不一致 (want {len(want_p)}B got {len(got_p)}B)")
    print(f"      种子自举链编译十种域成功，IR 与 Potato 都与参考逐字节相同 ({len(want)}B)")

@test
def test_lgc_end_window_is_a_scoped_pause():
    """`lgc end { … }`：短暂暂停窗口（`docs/309`），与 `lgc start { … }` 对称。

    三件事：形状收下（`end(1ms)` 不收）；IR 里块内置 0、出块恢复进入前的值；
    Potato 把它记成"一次暂停 + 一个窗口"。
    """
    src = ("choose write grammar tur2026.3\nmodule m\nchoose gc_auto\nchoose runtime\n"
           "fn f() {\n    lgc end {\n        let p: ptr = alloc(16);\n"
           "        store8(p, 0, 1);\n    }\n}\n")
    mod = _lgc_load(src)
    assert lomentc.check(mod, deps=[]) == [], lomentc.check(mod, deps=[])
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "%lgc.on.0 = alloca i32" in ir and "%lgc.dl.0 = alloca i64" in ir, ir
    assert "store i32 0, ptr @__loment_gc_on" in ir, "暂停窗口没有把 on 置 0"
    # 出块恢复：close 之后有一条把保存值写回 on 的 load/store
    zero = ir.index("store i32 0, ptr @__loment_gc_on")
    tail = ir[zero:]
    assert "load i32, ptr %lgc.on.0" in tail, "出块没有恢复 on"
    gl = lomentc._count_gc_control(mod)
    assert (gl["pauses"], gl["windows"]) == (1, 1), gl
    try:
        _lgc_load(src.replace("lgc end {", "lgc end(1ms) {"))
    except Exception as e:
        assert "不接受时长" in str(e), e
    else:
        raise AssertionError("`lgc end(1ms)` 居然被收下了")


# ---------------------------------------------------------------- 生命周期（docs/299）

def _with_profile(src: str, profile: str) -> lomentc.Module:
    """按指定版次解析（`load()` 才设 `mod.profile`，判据里直接给）。"""
    m = lomentc.Parser(lomc.lex(src), src, profile=profile).parse()
    m.profile = profile
    return m


@test
def test_lifetime_dimension_defaults_follow_the_edition():
    """`lifetime` 是第八维：2026.3 默认 `auto`，旧版次默认 `no`（`docs/305`）。"""
    src = "module m\nfn main() -> i32 { return 7; }\n"
    m = parse(src)
    assert m.profile == potato_from.DEFAULT_EDITION, m.profile
    doc = json.loads(lomentc.emit_potato(m, ROOT))
    assert doc["potato"] == potato.VERSIONS[-1], doc["potato"]
    assert doc["lifetime"] == "lifetime_auto", doc.get("lifetime")

    old = _with_profile(src, "tur2026.2")
    doc2 = json.loads(lomentc.emit_potato(old, ROOT))
    assert doc2["lifetime"] == "lifetime_no", doc2.get("lifetime")


@test
def test_lifetime_words_are_refused_before_2026_3():
    """旧版次写 `lifetime_*` → 点名拒（不是"未知取值"那种泛泛的报错）。"""
    src = "module m\nchoose lifetime_auto\nfn main() -> i32 { return 7; }\n"
    e = lomentc.check(_with_profile(src, "tur2026.2"))
    assert any("生命周期控制从" in x for x in e), e
    # 2026.3 的同一份源是合法的
    assert lomentc.check(parse(src)) == []


@test
def test_lifetime_dimension_is_declared_once():
    src = ("module m\nchoose lifetime_auto\nchoose lifetime_no\n"
           "fn main() -> i32 { return 7; }\n")
    e = errs(src)
    assert any("核心模式这一维只能声明一次" in x for x in e), e


@test
def test_lifetime_borrow_types_lower_to_pointers():
    """`&T` 进签名/字段，`r.field` 自动解一层，产物是 `ptr`。"""
    src = ("module m\n"
           "struct V { x: u32, y: u32 }\n"
           "fn get(v: &V) -> u32 { return v.x; }\n"
           "fn main() -> i32 { let a: V = V { x: 3, y: 4 }; return get(&a) as i32; }\n")
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "define i32 @get(ptr %v)" in ir, ir
    assert "call i32 @get(ptr %a.addr)" in ir, ir


@test
def test_lifetime_rejects_escaping_local_borrow():
    """悬垂：直接返回、经别名、经调用传回来、经结构体字段 —— 四条路都要拦。"""
    for src in (
        "module m\nfn f() -> &u32 { let a: u32 = 1; return &a; }\n",
        "module m\nfn f() -> &u32 { let a: u32 = 1; let r: &u32 = &a; return r; }\n",
        "module m\nfn id(p: &u32) -> &u32 { return p; }\n"
        "fn f() -> &u32 { let a: u32 = 1; return id(&a); }\n",
        "module m\nstruct V { p: &u32 }\n"
        "fn f() -> V { let x: u32 = 1; return V { p: &x }; }\n",
    ):
        e = errs(src)
        assert any("悬垂" in x for x in e), (src, e)


@test
def test_lifetime_return_borrow_needs_a_source_parameter():
    """没有引用形参却返回借用 ⇒ 区域无从推断（E041）。

    局部来源那一路由 E012 说清根因，不再叠一条 E041（同一处报两遍会把根因埋掉）；
    外部来源（例如返回借用的 extern 调用）没有别的诊断，由 E041 点名。
    """
    local = errs("module m\nfn f() -> &u32 { let a: u32 = 1; return &a; }\n")
    assert any("悬垂" in x for x in local), local
    assert not any("签名里没有引用形参" in x for x in local), local
    ext = errs("module m\nextern fn ext() -> &u32;\n"
               "fn f() -> &u32 { return ext(); }\n")
    assert any("签名里没有引用形参" in x for x in ext), ext


@test
def test_lifetime_manual_markers():
    """`&a T` 只在 manual 档可用，且必须指向一个真实存在的引用形参。"""
    manual = ("module m\nchoose lifetime_manual\n"
              "fn pick(a: &u32, b: &u32) -> &a u32 { return a; }\n")
    assert errs(manual) == [], errs(manual)
    auto = manual.replace("choose lifetime_manual\n", "")
    e = errs(auto)
    assert any("不写区域标记" in x for x in e), e
    bad = manual.replace("&a u32 {", "&zz u32 {")
    e2 = errs(bad)
    assert any("不存在的形参" in x for x in e2), e2
    bad2 = manual.replace("fn pick(a: &u32, b: &u32)", "fn pick(a: u32, b: &u32)")
    e3 = errs(bad2)
    assert any("不是引用形参" in x for x in e3), e3


@test
def test_lifetime_free_while_borrowed_is_rejected():
    src = ("module m\nchoose runtime\n"
           "fn f() -> u32 { let p: ptr = alloc(8); let r: &u8 = &*p; free(p); return 1; }\n")
    e = errs(src)
    assert any("仍被借用时释放" in x for x in e), e


@test
def test_lifetime_stored_mut_borrow_is_exclusive():
    src = ("module m\nfn f() -> u32 { let a: u32 = 1; let r: &mut u32 = &mut a;\n"
           "    let s: &u32 = &a; return 1; }\n")
    e = errs(src)
    assert any("既被可变借用又被借用" in x for x in e), e


@test
def test_lifetime_no_rejects_borrow_types():
    """`lifetime_no` 是**绝对关闭**：`&T` 还是今天的解析错误。"""
    src = "module m\nchoose lifetime_no\nfn f(p: &u32) -> u32 { return 1; }\n"
    try:
        parse(src)
    except Exception as e:
        assert "&" in str(e), e
    else:
        raise AssertionError("`lifetime_no` 下 `&u32` 居然被收下了")


@test
def test_lifetime_stored_borrow_excludes_later_inline_borrows():
    """存下来的借用活到函数末尾：之后的**行内**借用也算重叠（E007）。

    之前只判“两次存下来的借用”，`let r = &mut a; g(&a)` 一条都不报 —— 那是漏判。
    字段路径让不相交的字段免于误报。
    """
    head = ("module m\nfn g(p: &u32) -> u32 { return *p; }\n"
            "fn gm(p: &mut u32) -> u32 { return *p; }\n")
    cases = {
        "stored mut + inline shared":
            head + "fn f() -> u32 { let a: u32 = 1; let r: &mut u32 = &mut a;"
                   " return g(&a); }\n",
        "stored mut + inline mut":
            head + "fn f() -> u32 { let a: u32 = 1; let r: &mut u32 = &mut a;"
                   " return gm(&mut a); }\n",
        "stored shared + inline mut":
            head + "fn f() -> u32 { let a: u32 = 1; let r: &u32 = &a;"
                   " return gm(&mut a); }\n",
    }
    for name, src in cases.items():
        e = errs(src)
        assert any("借用" in x and "两次" in x or "既被可变借用又被借用" in x for x in e), (name, e)
    # 两把共享借用可以共存
    ok = head + "fn f() -> u32 { let a: u32 = 1; let r: &u32 = &a; return g(&a); }\n"
    assert errs(ok) == [], errs(ok)
    # 不相交的字段各借各的（Rust 允许）
    disjoint = ("module m\nstruct V { x: u32, y: u32 }\n"
                "fn f() -> u32 { let v: V = V { x: 1, y: 2 };"
                " let a: &mut u32 = &mut v.x; let b: &mut u32 = &mut v.y; return 1; }\n")
    assert errs(disjoint) == [], errs(disjoint)
    # 但根与字段是重叠的
    overlap = ("module m\nstruct V { x: u32, y: u32 }\n"
               "fn f() -> u32 { let v: V = V { x: 1, y: 2 };"
               " let a: &mut V = &mut v; let b: &mut u32 = &mut v.x; return 1; }\n")
    assert any("两次" in x or "既被可变借用又被借用" in x for x in errs(overlap)), errs(overlap)
    # 借用**存进字段**也算存下来（`R { p: &mut a }` 活到绑定结束）
    stored_field = ("module m\nstruct R { p: &mut u32 }\n"
                    "fn f() -> u32 { let a: u32 = 1; let r: R = R { p: &mut a };"
                    " return g(&a); }\n")
    assert any("既被可变借用又被借用" in x for x in errs(stored_field)), errs(stored_field)


@test
def test_lifetime_slice_field_is_a_zero_copy_view():
    """`struct C { data: &[u32] }` 要能落成 `{ { ptr, i64 }, i32 }`，字段能取长度/取下标。"""
    src = ("module m\n"
           "struct C { data: &[u32], n: u32 }\n"
           "fn mk(a: &[u32]) -> C { let c: C = C { data: a, n: slice_len(a) }; return c; }\n"
           "fn use(c: &C) -> u32 { return slice_len(c.data) + c.data[0]; }\n"
           "fn f() -> u32 { let a: [u32; 2] = [1, 2]; let c: C = mk(&a); return use(&c); }\n")
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "define { { ptr, i64 }, i32 } @mk" in ir, ir[:500]
    assert "extractvalue { ptr, i64 }" in ir, "字段里的切片没有按 {ptr,i64} 落"


@test
def test_lifetime_generic_borrow_instantiates():
    """`Box<&u32>` 要单态化成一个**标识符**实例名，并落成 `{ ptr }`。"""
    src = ("module m\nstruct Box<T> { v: T }\n"
           "fn mk(p: &u32) -> Box<&u32> { let b: Box<&u32> = Box { v: p }; return b; }\n"
           "fn get(b: &Box<&u32>) -> u32 { return *b.v; }\n"
           "fn f() -> u32 { let a: u32 = 7; let b: Box<&u32> = mk(&a); return get(&b); }\n")
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "define { ptr } @mk(ptr %p)" in ir, ir[:400]
    assert "define i32 @get(ptr %b)" in ir, ir[:400]


@test
def test_lifetime_index_through_reference():
    """`p: &[u32; 2]` 的 `p[0]` 读写都要解一层借用。"""
    read = "module m\nfn f(p: &[u32; 2]) -> u32 { return p[0]; }\n"
    assert errs(read) == [], errs(read)
    assert "getelementptr inbounds [2 x i32]" in lomentc.emit_llvm(parse(read), ROOT, [])
    write = ("module m\nfn f(p: &mut [u32; 2]) -> u32 { p[1] = 7; return p[1]; }\n")
    assert errs(write) == [], errs(write)
    assert "store i32 7" in lomentc.emit_llvm(parse(write), ROOT, [])


@test
def test_lifetime_verdicts_are_the_same_on_every_gc_tier():
    """GC 三档不改变借用的判决（`docs/299` §3.5 / #1247）。"""
    bad = ("module m\nchoose runtime\nchoose {gc}\n"
           "fn g(p: &u32) -> u32 {{ return *p; }}\n"
           "fn f() -> u32 {{ let a: u32 = 1; let r: &mut u32 = &mut a; return g(&a); }}\n")
    good = ("module m\nchoose runtime\nchoose {gc}\n"
            "fn id(p: &u32) -> &u32 {{ return p; }}\n")
    for gc in ("gc_manual", "gc_auto", "gc_auto_alpha"):
        e = errs(bad.format(gc=gc))
        assert any("借用" in x for x in e), (gc, e)
        assert errs(good.format(gc=gc)) == [], (gc, errs(good.format(gc=gc)))


# ---------------------------------------------------------------- 效应语法面（A2）
# `docs/341` §11 的 A2：**声明与行接受并擦除，perform/handle/resume 给 E023**。
# 默认关（`lspe_effects_false`），所以旧源一个字节都不变。

_EFF_BASE = "module m\nchoose lspe_effects_true\n\n"


@test
def test_effects_slot_is_registered_and_defaults_off():
    """`lspe_effects_true|false` 是一格构建旋钮，默认关（`docs/341` §11 A2）。"""
    assert "effects" in lomentc.CORE_SLOTS, lomentc.CORE_SLOTS
    assert lomentc.CORE_SLOT_DEFAULTS["effects"] == "false"
    assert lomentc.CORE_SLOT_OF["lspe_effects_true"] == ("effects", "true")
    assert lomentc.CORE_SLOT_OF["lspe_effects_false"] == ("effects", "false")


@test
def test_effect_declarations_and_rows_are_accepted_and_erased():
    """开关打开：`effect` 与 `effects` 行解析成 AST；发射器不看它们 ⇒ IR 逐字节相同。"""
    decl = _EFF_BASE + (
        "effect Log {\n"
        "    fn write(v: u32) -> ();\n"
        "    fn flush() -> ();\n"
        "}\n\n"
        "fn f(x: u32) -> u32 effects Log { return x; }\n")
    plain = _EFF_BASE + "fn f(x: u32) -> u32 { return x; }\n"
    m = parse(decl)
    assert [e.name for e in m.effects] == ["Log"], m.effects
    assert [op.name for op in m.effects[0].ops] == ["write", "flush"]
    assert m.effects[0].ops[0].params == [("v", "u32")]
    assert m.effects[0].ops[0].ret == "()"
    assert m.funcs[0].effects == ["Log"], m.funcs[0].effects
    assert errs(decl) == [], errs(decl)
    assert lomentc.emit_llvm(m, ROOT, []) == lomentc.emit_llvm(parse(plain), ROOT, [])


@test
def test_effects_are_off_by_default():
    """开关关着：`effect` 仍是未知顶层关键字，`effects` 行也不被吃（旧行为）。"""
    off = "module m\n\neffect Log { fn write(v: u32) -> (); }\n"
    try:
        parse(off)
        assert False, "默认档不该认 `effect`"
    except lomc.LomError as e:
        assert "未知顶层关键字" in str(e), e
    off_row = "module m\n\nfn f(x: u32) -> u32 effects Log { return x; }\n"
    try:
        parse(off_row)
        assert False, "默认档不该认 `effects` 行"
    except lomc.LomError as e:
        assert "期望" in str(e), e


@test
def test_effect_row_names_must_be_declared_and_unique():
    """行里的未声明效应 ⇒ E002；重复声明 ⇒ E013（`docs/341` §6 R1）。"""
    bad = _EFF_BASE + "fn f(x: u32) -> u32 effects Nope { return x; }\n"
    e = errs(bad)
    assert e and loment_diag.family(loment_diag.classify(e[0])[0]) == "E002", \
        (e, loment_diag.classify(e[0]))
    dup = _EFF_BASE + (
        "effect Log { fn write(v: u32) -> (); }\n"
        "effect Log { fn flush() -> (); }\n")
    e = errs(dup)
    assert e and loment_diag.family(loment_diag.classify(e[0])[0]) == "E013", \
        (e, loment_diag.classify(e[0]))


@test
def test_perform_parses_then_refuses_as_e023():
    """`perform` 解析得到（AST 里有节点），检查器以 E023 点名拒 —— 不是 Python 栈。"""
    src = _EFF_BASE + (
        "effect Log { fn write(v: u32) -> (); }\n\n"
        "fn run(x: u32) -> u32 effects Log {\n"
        "    let y: u32 = perform Log.write(x);\n"
        "    return y;\n}\n")
    m = lomentc.Parser(lomc.lex(src), src).parse()
    assert m.effect_runtime and m.effect_runtime[0][0] == "perform", m.effect_runtime
    e = errs(src)
    assert e and all(loment_diag.family(loment_diag.classify(x)[0]) == "E023" for x in e), e
    assert any("perform" in x for x in e), e


@test
def test_handle_and_resume_parse_then_refuse_as_e023():
    """`handle` / `resume` 同样：先有 AST，再由检查器点名拒（`docs/341` §11 A3）。"""
    src = _EFF_BASE + (
        "effect Log { fn write(v: u32) -> (); }\n\n"
        "fn run(x: u32) -> u32 {\n"
        "    let n: u32 = handle Log {\n"
        "        let a: u32 = perform Log.write(x);\n"
        "        a + 1\n"
        "    } with {\n"
        "        write(v) => { resume(v + 1); }\n"
        "    };\n"
        "    return n;\n}\n")
    m = lomentc.Parser(lomc.lex(src), src).parse()
    kinds = {k for k, _l, _c in m.effect_runtime}
    assert kinds == {"perform", "handle", "resume"}, m.effect_runtime
    let = m.funcs[0].body[0]
    assert isinstance(let.expr, lomentc.Handle), let
    assert let.expr.arms[0].op == "write" and let.expr.arms[0].params == ["v"]
    e = errs(src)
    assert len(e) == 3 and all(
        loment_diag.family(loment_diag.classify(x)[0]) == "E023" for x in e), e


@test
def test_extern_cannot_declare_effects():
    """`extern fn … effects E` 是边界错（`docs/341` §6 R7），A2 以 E023 点名。"""
    src = _EFF_BASE + (
        "effect Log { fn write(v: u32) -> (); }\n\n"
        "extern fn puts(p: ptr) -> i32 effects Log;\n")
    e = errs(src)
    assert e and all(loment_diag.family(loment_diag.classify(x)[0]) == "E023" for x in e), e
    assert any("extern" in x for x in e), e


def main() -> int:
    return loment_suite.run(TESTS, "lomentc_test")


if __name__ == "__main__":
    sys.exit(main())
