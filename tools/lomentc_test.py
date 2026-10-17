#!/usr/bin/env python3
# lomentc_test.py — L1 Loment 编译器 + Potato v0 校验器自检 (docs/143 §验收)
#
# 运行: python tools/lomentc_test.py   (退出码 0 = 全绿)
# 说明: 编译/运行验证 (rustc) 由 docs/143 §5 记录的命令执行, 本套件只做静态判定。

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import loment_diag  # noqa: E402
import lomentc  # noqa: E402
import potato  # noqa: E402

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
        codes = {loment_diag.classify(x)[0] for x in e}
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
        ("module m\nfn f() -> u32 {\n    let a: u32 = 10;\n    return a / 2;\n}\n", "除法 `/`"),
        ("module m\nfn f() -> u32 {\n    let a: u32 = 10;\n    return a % 3;\n}\n", "取模 `%`"),
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
        # 问会一律落 E999 —— `native` 那批也一样（既有行为，不是这条引入的）。
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
def test_unit_wide_unique_names():
    """跨模块同名顶层符号 -> 编译错误 (发射符号是平的, 见 docs/150 缺口①)。

    单元里两个模块各声明一个私有 `helper` 时, 后端会发出两条 `define @helper` (非法 IR),
    调用点还会解析到同一个函数 (静默错编) —— 必须编译期拒。同一模块**内部**的重名照旧
    由原规则报 (别被这条取代)。
    """
    entry = ROOT / "loment" / "selfhost" / "neg_across" / "entry.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    unit_errs = lomentc.check(mod, deps=deps)
    assert any("重名" in e for e in unit_errs), unit_errs
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
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(&x); }\n")
    assert any("& 只能作用于数组" in x for x in e), e
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(x); }\n")
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
    assert "__LOMENT_AUDIT" in rs and "fn __loment_guard(" in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }]" in ir
    assert "@__loment_audit = internal global [16 x i64]" in ir


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
def test_e028_literal_index_over_a_fixed_array():
    """**字面量下标越过定长数组的界**是**编译期**的一个诊断，不是运行期的一种可能。

    **2026-10-16 改判（issue #18）**：这一条**不再看 `lms`**。原先它只在
    `choose lms_open` 时发，而 `lms` 是**开发通道的中间态、不进发行版**（`docs/253` §14.2）
    —— 于是发行版的用户根本拿不到它。可它本来就不是一条 LMS 检查：`docs/205` §1.1 C5
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
    assert any("E028" in x for x in errs(off)), errs(off)
    # `lms` 开着也一样拒，而且**只报一条** —— 两条路都在的时候不许数出两条诊断
    on = "module m\nchoose lms_open\n" + body
    assert len([x for x in errs(on) if "E028" in x]) == 1, errs(on)
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
    assert any("E028" in x for x in errs(w)), errs(w)


@test
def test_e030_the_runtime_refusal_names_its_own_subsystem():
    """默认档下用了会拖运行期的构造 ⇒ **自有码 E031**，四段正文齐，正文是那句话不是那个 token。

    **Why（issue #321）**：这条诊断原先**并进 E022**「`choose` / `addin` 用法不对」，
    理由是"码按修法分，修法同族（去写一行 `choose runtime`）"。那条理由**数错了读者**：
    报的是用户写的**那个构造**（`/`、`alloc`…），而他那一行 `choose` **没有问题** ——
    实测原样的输出是 `error[E022]: bad `choose` / `addin`` 配 `message: /`，用户去看
    `choose` 翻来覆去都是对的，还得读完 `why:` 里三条无关的才轮到适用的那一条。
    **码是用户最先读到的那个词，指错子系统就是误导，不是简短。**

    **How to apply**：四件事一起钉 —— ① 码是 E031（不是 E022）；② 正文**是那句话**
    （「除法 `/`」），不是那一格 token 原文；③ 说明卡四段齐、`fixes` 至少三条；④ 库那一侧
    （"依赖用了却没声明"）走**同一个码**，因为对用户是同一件事。
    """
    import loment_diag
    src = "module m\n\nfn f() -> u32 {\n    let a: u32 = 10;\n    return a / 2;\n}\n"
    got = errs(src)
    assert got, "默认档下 `a / 2` 应当被拒"
    codes = {loment_diag.classify(m)[0] for m in got}
    assert codes == {"E031"}, f"码不是 E031：{codes} —— {got}"
    # ② 正文是那句话：里面有"除法 `/`"，而不是只有那一格 token
    assert "除法 `/`" in got[0], got
    # ③ 说明卡四段齐（两张表都写），fixes 至少三条
    for tbl in (loment_diag.CARDS[30], loment_diag.CARDS_EN[30]):
        assert tbl.what and tbl.why and tbl.yes and tbl.no, tbl
        assert len(tbl.fixes) >= 3, tbl
    assert loment_diag.ASCII_ONE_LINER[30], "`loment codes` 那一行要有"
    assert loment_diag.TITLE_EN[30], "英文标题要有"
    # ④ 六个构造走同一个码（名单是 `_RUNTIME_SITES`），这里抽三个代表：两个内建 + 取模
    for one, want in (("fn f() -> u32 {\n    let p: ptr = alloc(8);\n    return 0;\n}\n",
                       "`alloc`"),
                      ("fn f() -> u32 {\n    panic(1);\n    return 0;\n}\n", "`panic`"),
                      ("fn f(a: u32, b: u32) -> u32 {\n    return a % b;\n}\n", "取模 `%`")):
        e = errs("module m\n\nchoose no_runtime\n\n" + one)
        assert e and {loment_diag.classify(m)[0] for m in e} == {"E031"}, (want, e)
        assert want in e[0], (want, e)
    # 库那一半：依赖用了却没声明 —— 同一件事，同一个码
    dep = ("module dep\n\npub fn dep_div(x: u32) -> u32 {\n    return x / 2;\n}\n")
    root = ("module m\nuse \"dep.lomt\"\n\nfn f() -> u32 {\n    return dep_div(4);\n}\n")
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "dep.lomt").write_text(dep, encoding="utf-8")
        rp = d / "m.lomt"
        rp.write_text(root, encoding="utf-8")
        mod = lomentc.load(rp)
        deps = lomentc.resolve_deps(mod, ROOT, rp.parent, entry=rp)
        e = lomentc.check(mod, None, deps)
    assert e and {loment_diag.classify(m)[0] for m in e} == {"E031"}, e
    print("      E031 自有码：构造自己是它的位置，库那一半同码")


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

    这一对同样是"同一份源，加不加 `choose lms_open` 判决相反"。
    """
    head = "module m\nchoose runtime\n%s\n"

    def body(use_after: bool) -> str:
        if use_after:
            return ("fn f() -> u32 {\n    let p: ptr = alloc(8);\n    free(p);\n"
                    "    let x: u32 = load8(p, 0);\n    return x;\n}\n")
        return ("fn f() -> u32 {\n    let p: ptr = alloc(8);\n"
                "    let x: u32 = load8(p, 0);\n    free(p);\n    return x;\n}\n")

    # 关着（默认）：use-after-free 一个字的检查都没有
    assert errs(head % "" + body(True)) == [], errs(head % "" + body(True))
    # 开着：编译期拒
    on = head % "choose lms_open" + body(True)
    assert any("E029" in x for x in errs(on)), errs(on)
    # **先读后 free** 合法 —— 这一半挡住"见过 free 就报"那种过头实现
    ok = head % "choose lms_open" + body(False)
    assert errs(ok) == [], errs(ok)
    # **free 之后重新赋值** = 这个名字不再指那块死掉的块 ⇒ 不报（与自举侧 `chk_fr_del` 同口径）
    re = head % "choose lms_open" + (
        "fn f() -> u32 {\n    let p: ptr = alloc(8);\n    free(p);\n"
        "    p = alloc(8);\n    return load8(p, 0);\n}\n")
    assert errs(re) == [], errs(re)


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

    唯一性只管第③层 (内置四根): 它抓的是"作者把名字写重了"; 使用者选哪一份是**优先级**的
    事, 像 PYTHONPATH。两件事混进一个错误, 使用者不知道该改什么。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _pkg(proj / "deps" / "bytes", "bytes")
        got = lomentc.resolve_name("bytes", ROOT, proj, None)
        assert got == proj / "deps" / "bytes" / "bytes.lomt", got
        assert hasattr(lomentc, "NAME_ROOTS") and len(lomentc.NAME_ROOTS) == 4


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
    want_buf = sum(2 + (sz + 7) // 8
                   for per in L0_WANT.values() for sz in per.values())
    assert alpha_ll.count(".buf") == want_buf, alpha_ll.count(".buf")
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
    assert a == {"l0": 1, "l1": 1, "l2": 1, "l3": 1, "total_sites": 4}, f"alpha: {a}"
    assert m == {"l0": 0, "l1": 0, "l2": 0, "l3": 4, "total_sites": 4}, f"manual: {m}"
    for d in (a, m):
        assert d["l0"] + d["l1"] + d["l2"] + d["l3"] == d["total_sites"], d
    # 两份源**只差那一行**
    assert GC_LADDER_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        GC_LADDER_MANUAL_SRC.replace("choose gc_manual", "X")


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
#: 取消）/ `f_call` 把它交给了用户函数 / `f_rettp` 从体内 `return p` / `f_two` 的第一格被
#: **后面那个同名循环**挡掉（诚实的保守，见 §7）。
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
def test_runtime_dep_must_declare_and_agree():
    """`docs/227` 判据 D：依赖的**声明是需求** —— 库没写要报，根不满足要报。

    改之前这里是一律「库不许 `choose`」（`lomentc.py` 的 `check()`）。那条按字面做不下去：
    `loment/lib/proc.lomt` 要除法，当根编要 `choose runtime`、当依赖又不许写 —— 两面堵死。
    新规矩把方向反过来，而它**本来就是那句老报错自己写的**（"库该声明**能力需求**"）。
    """
    import tempfile
    NL = chr(10)
    lib_div = "module lib" + NL + NL + "pub fn half(x: u32) -> u32 {" + NL + "    return x / 2;" + NL + "}" + NL
    lib_rt = "module lib" + NL + "choose runtime" + NL + NL + "pub fn half(x: u32) -> u32 {" + NL + "    return x / 2;" + NL + "}" + NL
    tail = NL + 'use "lib.lomt"' + NL + NL + "fn f() -> u32 {" + NL + "    return half(4);" + NL + "}" + NL

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
    assert hosted.index("declare ptr @malloc(i64)") < hosted.index("call ptr @malloc(i64 1048576)")

    # A1：同一份源去掉那一行 ⇒ 逐字节还是原来那一段（闸门真的在 `port` 上）
    assert "@__loment_heap = internal global [65536 x i8] zeroinitializer" in sealed
    assert "malloc" not in sealed, "sealed 的产物里出现了宿主符号"
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
    assert "malloc" not in sealed


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
    assert "[1048576 x i8] zeroinitializer" in ffi, "arena 还是那块 64 KiB 的数组"
    assert "getelementptr [65536 x i8], ptr @__loment_heap" not in ffi, "还在直接寻址"
    assert "%hb = call ptr @__loment_arena()" in ffi, "分配器没用基址指针"
    assert "ret ptr @__loment_arena_block" in ffi, "arena 不是产物里那块静态的"
    assert "malloc" not in ffi, "sealed 的产物里出现了宿主符号"
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
    ("gc_auto/lms", "choose gc_auto\nchoose runtime\nchoose lms_open"),
    ("gc_auto_alpha/lms", "choose gc_auto_alpha\nchoose runtime\nchoose lms_open"),
)


@test
def test_lms_entry_a_rescue_needs_both_lms_and_a_collector():
    """LMS 第 3 级入口 A（`docs/253` §5.3）：`alloc` 分配不出时**先收一次再试**。

    **两个条件缺一不可** —— `lms` 开着（它属于 LMS 第 3 级）**而且**这份产物里真有
    收集器（`gc_auto` / `gc_auto_alpha`）。`gc_manual` 下**没有东西可收**，
    "拉回"在那里是一句空话 —— 所以那一格**必须一个字都不加**。这是这一级明写的边界。

    网格：{gc_manual, gc_auto, gc_auto_alpha} × {关着, 开着}。
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
        off = ir(f"choose {gc}")
        assert "__loment_lms_retry" not in off, (gc, "关着时不许有")
        on = ir(f"choose {gc}\nchoose lms_open")
        if gc == "gc_manual":
            assert "__loment_lms_retry" not in on, (gc, "没有收集器 ⇒ 没有东西可拉回")
        else:
            assert "__loment_lms_retry" in on, (gc, "开着且要收 ⇒ 该有")
            # **只许重来一次**：重试标记在**每一条成功返回前**都要清掉。
            # 不清的话"这一次"用掉的重试会留给下一次分配 —— 那是**假的能力**。
            assert on.count("store i1 false, ptr @__loment_lms_retry") == 2, on


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


def main() -> int:
    return loment_suite.run(TESTS, "lomentc_test")


if __name__ == "__main__":
    sys.exit(main())
