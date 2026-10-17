#!/usr/bin/env python3
"""loment_trans_test.py — **花括号族写法 -> Loment** 的 Loment 版（`docs/186` / `189`）。

`tools/trans_core.py` —— C / C++ / Java / C# 四门共用的那个翻译器：一份扫描器、
一个递归下降、一个发射器，四门的差异是**一张方言表加几个开关**。判据与第 16、17 格
**同一条**：同一份源喂两边，产出的 Loment 源码**逐字节相同**，退出码也相同。

    源  --trans_core.translate-->  Loment 源码
    源  --lomtrans.lomt-------->  Loment 源码     ← 逐字节比这一份

## 比什么

1. **stdout 逐字节相同**（翻译出来的 Loment 源码）；
2. **退出码相同**（0 = 翻出来了 / 1 = 子集外 / 2 = 解析不过）。

**不比起文案** —— Python 的报错消息里插了源文本与 `repr()`，Loment 侧复现不了。
与第 15 格 `lompotato`、第 17 格 `lomtfrom` 同一条纪律：**失败时比退出码**，
而 `trans_core` 那两类失败（`Unsupported` / `CError`）本来就是两种退出码，
所以这一条仍然分得出"没实现"和"你写错了"。

## 覆盖面

* 四门各自语料库里的"真程序"（`loment/{ctrans,cpptrans,jtrans,cstrans}/`）；
* 第 16 格那四张前端 `_BATTERY`（直接从 `loment_ctrans_test` 引 —— 同一批输入
  喂前端和喂翻译器，覆盖面对齐；两处各抄一份必然漂）；
* `_EXTRA` —— **翻译器独有**的那些路：两个方向的强转、`for` 改名外提、保留字后缀、
  `--keep` / `--extern` / `--const`、重名、常量名照抄。

## 这一格**不含**什么

`--tokens` / `--pre` / `--ast` 是孪生自己的**调试**开关（不是上游接口），判据不碰，
所以这里不设判据钉它们 —— 它们的作用是出错时把"读错了"和"发错了"分开。

用法: python tools/loment_trans_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import cpptrans  # noqa: E402
import cstrans  # noqa: E402
import ctrans  # noqa: E402
import jtrans  # noqa: E402
import lomelf  # noqa: E402
import pytrans  # noqa: E402  (Python 那一门的上游)
import lomentc  # noqa: E402
import loment_ctrans_test as T  # noqa: E402  (第 16 格那四张前端电池)
import trans_core  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TWIN = ROOT / "loment" / "tools" / "lomtrans.lomt"

#: 一门一张前端电池（第 16 格那四张，直接从 `loment_ctrans_test` 引）——
#: 同一批输入喂前端和喂翻译器，覆盖面对齐; 两处各抄一份必然漂。
_BATTERIES = {
    "c": T._BATTERY, "cpp": T._CPP_BATTERY,
    "java": T._JAVA_BATTERY, "csharp": T._CS_BATTERY,
}

#: **翻译器独有**的那几条路。语料与前端电池覆盖的是"前端怎么读源"，
#: 这里覆盖"翻译器怎么决定" —— 后者才是这一格新引入的东西。
#: 每项 `(标签, 源, 方言, 后缀, 选项)`；选项里的 `keep` / `externs` / `consts`
#: 就是 `trans_core.translate` 的那三个关键字，命令行侧对应 `--keep` / `--extern` / `--const`。
_EXTRA: list[tuple[str, str, object, str, dict]] = [
    # ---- 两个方向的强转: **方言表说了算**（C/C++ 都补，Java/C# 都不补）
    ("c_int_to_bool", "int f(int x) { if (x) { return 1; } return 0; }\n",
     ctrans.C, ".c", {}),
    ("c_bool_to_int", "int g(int a, int b) { int x = (a < b); return x; }\n",
     ctrans.C, ".c", {}),
    ("c_bool_ret", "bool pos(int x) { return x > 0; }\n", ctrans.C, ".c", {}),
    ("c_not", "int f(int x) { return !x; }\n", ctrans.C, ".c", {}),
    ("c_andand", "int f(int a, int b) { return a && b; }\n", ctrans.C, ".c", {}),
    ("cpp_bool_ret", "bool pos(int x) { return x > 0; }\n", cpptrans.CPP, ".cpp", {}),
    ("cpp_bool_param", "int f(bool b, int x) { if (b) { return x; } return 0; }\n",
     cpptrans.CPP, ".cpp", {}),
    ("java_no_coerce", "int f(boolean b) { if (b) { return 1; } return 0; }\n",
     jtrans.JAVA, ".java", {}),
    # Java/C# 这两条是**子集外**（退出码 1）—— 判据比退出码，所以照样钉得住
    ("java_int_as_cond", "int f(int x) { if (x) { return 1; } return 0; }\n",
     jtrans.JAVA, ".java", {}),
    ("java_bool_as_int", "int f(boolean b) { int x = b; return x; }\n",
     jtrans.JAVA, ".java", {}),
    ("cs_bool_ret", "class K { static bool pos(int x) { return x > 0; } }\n",
     cstrans.CSHARP, ".cs", {}),

    # ---- `for`: 改名外提 / 遮蔽 / 空条件 / 嵌套
    ("for_rename",
     "int f(int n) { int s = 0; for (int i = 0; i < n; i = i + 1) { s = s + i; }"
     " return s; }\n", ctrans.C, ".c", {}),
    ("for_shadow",
     "int f(int i) { int s = 0; for (int i = 0; i < 3; i = i + 1) { s = s + i; }"
     " return s + i; }\n", ctrans.C, ".c", {}),
    ("for_assign_init",
     "int f(int n) { int i = 0; int s = 0; for (i = 0; i < n; i = i + 1) { s = s + 1; }"
     " return s; }\n", ctrans.C, ".c", {}),
    ("for_empty_cond",
     "int f() { int s = 0; for (;;) { s = s + 1; } return s; }\n", ctrans.C, ".c", {}),
    ("for_nested",
     "int f(int n) { int s = 0; for (int i = 0; i < n; i = i + 1) {"
     " for (int j = 0; j < i; j = j + 1) { s = s + 1; } } return s; }\n",
     ctrans.C, ".c", {}),

    # ---- 三条"不是翻译、是决定"
    ("uninit_zero", "int f() { int x; return x; }\n", ctrans.C, ".c", {}),
    ("void_var", "int f() { void x; return 0; }\n", ctrans.C, ".c", {}),

    # ---- 保留字后缀（每门不同: `_c` / `_cpp` / `_j` / `_cs`）
    ("kw_suffix_c", "int f(int match) { int module = match; return module; }\n",
     ctrans.C, ".c", {}),
    ("kw_suffix_java",
     "class K { static int f(int match) { int module = match; return module; } }\n",
     jtrans.JAVA, ".java", {}),

    # ---- `--keep` / `--extern` / `--const`
    ("keep_only_one",
     "int a(int x) { return x; }\nint b(int y) { return y + 1; }\n",
     ctrans.C, ".c", {"keep": {"b"}}),
    ("keep_all_by_default",
     "int a(int x) { return x; }\nint b(int y) { return y + 1; }\n",
     ctrans.C, ".c", {}),
    ("extern_call_types",
     "int f(int n) { int s = 0; s = g(n); s = h(s); return s; }\n",
     ctrans.C, ".c", {"externs": {"g": "i32", "h": "()"}}),
    ("extern_unknown_is_loud",
     "int f(int n) { return unknown_fn(n); }\n", ctrans.C, ".c", {}),
    ("extern_wins_over_local",
     "int g(int x) { return x; }\nint f(int n) { return g(n); }\n",
     ctrans.C, ".c", {"externs": {"g": "()"}}),
    ("const_name_verbatim",
     "class K { static final int K0 = 5;\n static int f(int x) { return x + K0; } }\n",
     jtrans.JAVA, ".java", {"consts": {"K0": "i32"}}),
    ("const_needs_table",
     "class K { static final int K0 = 5;\n static int f(int x) { return x + K0; } }\n",
     jtrans.JAVA, ".java", {}),

    # ---- 重名（Loment 没有重载）
    ("dup_fn", "int f(int x) { return x; }\nint f(int y) { return y; }\n",
     ctrans.C, ".c", {}),

    # ---- 裸 `return`（不带值）：本语言的 `()` 函数没有提前退出，点名拒
    ("void_early_return", "void f(int x) { if (x) { return; } }\n", ctrans.C, ".c", {}),

    # ---- **寻常算术转换**（`#123` / `#133`）：C / C++ 转，Java / C# 不转（对照面）
    ("c_mixed_cmp",
     "int f(int a, unsigned int b) { return a > b; }\n", ctrans.C, ".c", {}),
    ("c_mixed_arith",
     "int f(int a, unsigned int b) { return a + b; }\n", ctrans.C, ".c", {}),
    ("c_long_promote",
     "int f(int x, long y) { return (x + y) > 0; }\n", ctrans.C, ".c", {}),
    ("c_sink_ret",
     "int f(unsigned int v) { return v; }\n", ctrans.C, ".c", {}),
    ("c_sink_arg",
     "unsigned int g(unsigned int a) { return a; }\n"
     "int f(int x) { return g(x); }\n", ctrans.C, ".c", {}),
    ("cs_no_promote",
     "class K { static int f(int a, uint b) { return a > b; } }\n",
     cstrans.CSHARP, ".cs", {}),

    # ---- **块作用域**（`#119` / `#120`）：遮蔽改名、出块撤掉、同层重复声明**拒**
    ("c_block_shadow",
     "int f(int c) { int x = 1; if (c) { int x = 2; } return x; }\n",
     ctrans.C, ".c", {}),
    ("c_for_body_shadow",
     "int f() { int s = 0; for (int i = 0; i < 3; i = i + 1) { int i = 9; s = s + i; }"
     " return s; }\n", ctrans.C, ".c", {}),
    ("c_block_redecl_ok",
     "int f(int c) { if (c) { int y = 1; } int y = 2; return y; }\n",
     ctrans.C, ".c", {}),
    ("c_same_scope_redecl",
     "int f() { int x = 1; int x = 2; return x; }\n", ctrans.C, ".c", {}),

    # ---- 语句位的调用也要过"本单元没有"那道闸（`#147`）
    ("c_call_stmt_position",
     "int f(int n) { g(n); return n; }\n", ctrans.C, ".c", {}),
]


def _twin_exe(td: Path) -> Path:
    """把 `lomtrans.lomt` 链成可执行文件（走仓库自己的原生后端，不经 clang）。

    它只吃源码、只用 open/read/write/brk/exit 五个系统调用，所以能在本机**原生**跑。
    """
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomtrans.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lomtrans.exe" if os.name == "nt" else "lomtrans")
    exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt" else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


def _argv(exe: Path, lang: str, fp: Path, opts: dict) -> list[str]:
    a = [str(exe), "--lang", lang, str(fp)]
    if opts.get("keep"):
        a += ["--keep", ",".join(sorted(opts["keep"]))]
    if opts.get("externs"):
        a += ["--extern", ",".join(f"{k}:{v}" for k, v in sorted(opts["externs"].items()))]
    if opts.get("consts"):
        a += ["--const", ",".join(f"{k}:{v}" for k, v in sorted(opts["consts"].items()))]
    return a


def _diff(lang: str, d, ext: str, extra: list) -> None:
    """一门一条判据的本体: 语料 + 前端电池 + `_EXTRA`，全部逐字节比。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str, dict]] = []
        for f in sorted((ROOT / "loment" / lang).glob("*" + ext)):
            cases.append((f.name, f.read_text(encoding="utf-8"), {}))
        cases += [(k + ext, v, {}) for k, v in sorted(_BATTERIES[lang].items())]
        cases += [(lab + ext, src, opts) for (lab, src, _d, _e, opts) in extra]
        bad = []
        for name, src, opts in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            try:
                want = trans_core.translate(src, d, keep=opts.get("keep"),
                                            externs=opts.get("externs"),
                                            consts=opts.get("consts"))
                wrc = 0
            except trans_core.Unsupported:
                want, wrc = "", 1
            except trans_core.CError:
                want, wrc = "", 2
            r = subprocess.run(_argv(exe, lang, fp, opts), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False, timeout=120)
            if r.returncode != wrc or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, wrc, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (
            f"{len(bad)}/{len(cases)} 份与 translate 不同（前 2）:\n"
            + "\n".join(f"  {n}: rc={rc}/{wrc}\n    py={w!r}\n    tw={g!r}"
                        for n, rc, wrc, w, g in bad[:2]))
        print(f"      {lang}: {len(cases)} 份 —— Loment 版翻译器与 "
              f"`trans_core.translate` 产出的源码逐字节相同，退出码也一致")


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_lomtrans_twin_matches_translate_c():
    """**C 那一门的翻译器有了 Loment 版**（S1 第十八格，§4.1 的第二根轴）。

    第十七格把 `potato_from` + `lomt_from` 两半都搬进了 Loment，但那两半之间那段
    ——**语法树 -> Loment 源码**—— 一直只有 Python 版。这一格补上它。

    覆盖面：`loment/ctrans/` 五份语料 + 第 16 格的 `_BATTERY` + `_EXTRA` 里 C 那几条。
    """
    _diff("c", ctrans.C, ".c", [x for x in _EXTRA if x[2] is ctrans.C])


@test
def test_lomtrans_twin_matches_translate_cpp():
    """C++ 那一门 —— **强转那一格的最尖处**：`bool` 是真类型，两个方向都补。

    C++ 的语料里有 `bool` 返回的函数，于是 `return x > 0;` 那一步必须按**声明的返回类型**
    决定场合（写死"整数"会发出 `(x > 0) as i32`，而函数声明的是 `-> bool` —— 本语言的
    类型错）。C 那一门没有 `bool`，所以这个分支只有 C++ 逼得出来。
    """
    _diff("cpp", cpptrans.CPP, ".cpp", [x for x in _EXTRA if x[2] is cpptrans.CPP])


@test
def test_lomtrans_twin_matches_translate_java():
    """Java 那一门 —— **两个方向一个都不补**，补不动的地方报**子集外**（退出码 1）。

    同一个 `if (x)`：C 那边补成 `if (x != 0)`，Java 这边是错 —— `&&` 出 boolean、
    条件只收 boolean，所以那里本来就该是个布尔表达式。两族结论相反，靠的是方言表
    那两个开关，不是代码里的分支。
    """
    _diff("java", jtrans.JAVA, ".java", [x for x in _EXTRA if x[2] is jtrans.JAVA])


@test
def test_lomtrans_twin_matches_translate_csharp():
    """C# 那一门 —— 与 Java 同一族（不补），但类型表与外壳那一层不一样。

    另外这一门与 Java 共享 `static` / `public` 那一套修饰词扫描，`const` 才是它的
    常量标志词（Java 是 `final`）—— 混了的话常量声明会被当成函数体里的一条语句。
    """
    _diff("csharp", cstrans.CSHARP, ".cs", [x for x in _EXTRA if x[2] is cstrans.CSHARP])


#: Python 那一门（`--lang python`）自己带的输入。**这一门的机器与前四门不一样** ——
#: 前四门吃花括号与分号，这台吃换行与缩进，所以要钉的是**块怎么划、缩进怎么算**。
_PY_BATTERY = {
    # ---- 基本形状
    "py_basic": "def f(a: int, b: int) -> int:\n    return a + b\n",
    "py_assign": "def f(a: int) -> int:\n    x = a * 2\n    return x\n",
    "py_call": "def g(a: int) -> int:\n    return a + 1\n\n"
               "def f(a: int) -> int:\n    return g(a) + g(a * 2)\n",
    "py_pass": "def f() -> int:\n    pass\n",

    # ---- 文档字符串：模块那一条发 `//`、函数那一条发 `///`，而且**在函数头之前**
    "py_fn_doc": 'def f(a: int) -> int:\n'
                 '    """Line one.\n\n    Line two.\n    """\n    return a\n',
    "py_mod_doc": '"""Module doc.\n\nMore.\n"""\n\nX = 3\n\n'
                  'def f(a: int) -> int:\n    return a + X\n',
    "py_doc_empty_line": 'def f() -> int:\n    """a\n\nb\n"""\n    return 1\n',

    # ---- 常量（模块级全大写）
    "py_const": "X = 3\n\ndef f(a: int) -> int:\n    return a + X\n",
    "py_const_neg": "NEG = -7\n\ndef f(a: int) -> int:\n    return a + NEG\n",
    "py_const_lower": "x = 3\n",
    "py_const_bad": "X = 's'\n",

    # ---- 块：if / elif / else / while / for（含嵌套）
    "py_if": "def f(x: int) -> int:\n    if x < 0:\n        return 0 - 1\n"
             "    return 0\n",
    "py_if_chain": "def f(x: int) -> int:\n    if x < 0:\n        return 0 - 1\n"
                   "    elif x == 0:\n        return 0\n    else:\n        return 1\n",
    "py_if_nested": "def f(a: int, b: int) -> int:\n    if a > 0:\n"
                    "        if b > 0:\n            return 1\n        return 2\n"
                    "    return 0\n",
    "py_while": "def f(n: int) -> int:\n    s = 0\n    while n > 0:\n"
                "        s = s + n\n        n = n - 1\n    return s\n",
    "py_for1": "def f(n: int) -> int:\n    s = 0\n    for i in range(4):\n"
               "        s = s + i\n    return s\n",
    "py_for2": "def f(a: int, b: int) -> int:\n    s = 0\n    for i in range(a, b):\n"
               "        s = s + i\n    return s\n",
    "py_for_reassigns_outer":
        "def f(n: int) -> int:\n    i = 9\n    for i in range(n):\n"
        "        pass\n    return i\n",

    # ---- 表达式：两个方向的 bool/int、n 元 and/or、一元、位运算、比较、十六进制
    "py_bool_cond": "def f(a: int, b: int) -> int:\n    if a and b:\n        return 1\n"
                    "    if a or b:\n        return 2\n    return 0\n",
    "py_and3": "def f(a: int, b: int, c: int) -> int:\n"
               "    if a and b and c:\n        return 1\n    return 0\n",
    "py_not": "def f(a: int) -> int:\n    if not a:\n        return 1\n    return 0\n",
    "py_cmps": "def f(a: int, b: int) -> int:\n    if a < b:\n        return 1\n"
               "    if a <= b:\n        return 2\n    if a != b:\n        return 3\n"
               "    return 0\n",
    "py_unary": "def f(a: int) -> int:\n    x = -a\n    z = +a\n"
                "    return x + z\n",
    # `~` 本语言没有对应的一元运算符 —— 上游点名拒，孪生跟着拒（`docs/198` §1）
    "py_invert": "def f(a: int) -> int:\n    return ~a\n",
    "py_bits": "def f(a: int, b: int) -> int:\n"
               "    return (a & b) | (a ^ b) | (a << 1) | (b >> 1)\n",
    "py_hex": "def f() -> int:\n    return 0x1f + 0b101 + 0o17 + 1_000\n",
    "py_aug": "def f(a: int, n: int) -> int:\n    acc = 0\n    for i in range(n):\n"
              "        acc += i * a\n    return acc\n",
    "py_bool_var": "def f(a: int, b: int) -> int:\n    x = a < b\n    return 0\n",
    "py_bool_ret": "def f(a: int, b: int) -> bool:\n    return a < b\n",
    "py_none_ret": "def f(a: int) -> None:\n    pass\n",
    "py_ann_assign": "def f(a: int) -> int:\n    x: int = a\n    y: bool = a > 0\n"
                     "    return x\n",

    # ---- 保留字加后缀
    "py_reserved": "def f(module: int) -> int:\n    match = module\n    return match\n",

    # ---- 缩进与换行的边角
    "py_crlf": "def f(a: int) -> int:\r\n    return a\r\n",
    "py_blank_lines": "def f(a: int) -> int:\n\n    x = a\n\n\n    return x\n",
    "py_comment_lines": "def f(a: int) -> int:\n    # lead\n    return a\n",
    "py_trailing_comment": "def f(a: int) -> int:\n    return a  # note\n",
    "py_empty": "",
    "py_comment_only": "# nothing\n",

    # ---- 拒收（退出码 1）与解析不过（退出码 2）
    "py_bool_as_int": "def f(a: int, b: int) -> int:\n    return a and b\n",
    "py_chain_cmp": "def f(a: int, b: int, c: int) -> int:\n"
                    "    if a < b < c:\n        return 1\n    return 0\n",
    "py_pow": "def f(a: int) -> int:\n    return a ** 2\n",
    "py_truediv": "def f(a: int) -> int:\n    return a / 2\n",
    "py_break": "def f(a: int) -> int:\n    while a > 0:\n        break\n    return 0\n",
    "py_no_annot": "def f(a) -> int:\n    return a\n",
    "py_no_ret": "def f(a: int):\n    return a\n",
    "py_default": "def f(a: int = 3) -> int:\n    return a\n",
    "py_vararg": "def f(a: int, *rest: int) -> int:\n    return a\n",
    "py_kwonly": "def f(a: int, *, b: int) -> int:\n    return a\n",
    "py_posonly": "def f(a: int, /, b: int) -> int:\n    return a\n",
    "py_decorator": "@deco\ndef f(a: int) -> int:\n    return a\n",
    "py_float": "def f(a: int) -> int:\n    return a + 1.5\n",
    "py_module_stmt": "x = 3\n",
    "py_import": "import os\n\ndef f(a: int) -> int:\n    return a\n",
    "py_stray_str": 'def f(a: int) -> int:\n    return a\n\n"stray"\n',
    "py_for_var_assign": "def f(n: int) -> int:\n    for i in range(n):\n"
                         "        i = 5\n    return 0\n",
    "py_while_for_var": "def f(n: int) -> int:\n    while n > 0:\n"
                        "        for i in range(2):\n            i = 9\n"
                        "        n = n - 1\n    return 0\n",
    "py_undeclared": "def f(a: int) -> int:\n    return zz\n",
    # 裸 `return`（不带值）—— 本语言的 `()` 函数没有提前退出
    "py_bare_return": "def f(a: int) -> int:\n    if a > 0:\n        return a\n    return\n",
    "py_async": "async def f(a: int) -> int:\n    return a\n",
    "py_class": "class C:\n    pass\n",
}


def _py_want(src: str, keep=None, consts=None) -> tuple[str, int]:
    """上游那两档错 -> (文本, 退出码)。与 `_EXTRA` 那条路同一个口径。"""
    try:
        return pytrans.translate(src, keep=keep, consts=consts), 0
    except pytrans.Unsupported:
        return "", 1
    except (pytrans.PyError, SyntaxError):
        return "", 2


@test
def test_lomtrans_twin_matches_pytrans():
    """**Python 那一门的翻译器也有了 Loment 版**（S1 第二十格）。

    前四门（C / C++ / Java / C#）是"花括号 + 分号"那一族，共用一台词法器与一台递归下降；
    Python 是**换行 + 缩进**，所以孪生那边是**另写的一套**：一台会造 INDENT / DEDENT 的
    词法器、一台吃缩进的递归下降、一个独立的发射器。

    覆盖面：`loment/pytrans/` 六份语料 + `_PY_BATTERY`。比 **stdout 逐字节 + 退出码**
    （1 = 子集外 / 2 = 解析不过）。

    **一处已知的子集外**：一行多条语句（`x = 1; y = 2`）。上游按 `;` 切开、两边都收，
    这里不做 —— 但**会出声**（退出码 1，不是不声不响地少发一条语句）。下一条判据钉着它。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted((ROOT / "loment" / "pytrans").glob("*.py")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".py", v) for k, v in sorted(_PY_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="")
            want, wrc = _py_want(src)
            r = subprocess.run([str(exe), "--lang", "python", str(fp)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               shell=False, timeout=120)
            if r.returncode != wrc or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, wrc, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (
            f"{len(bad)}/{len(cases)} 份与 pytrans 不同（前 2）:\n"
            + "\n".join(f"  {n}: rc={rc}/{wrc}\n    py={w!r}\n    tw={g!r}"
                         for n, rc, wrc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 Python：Loment 版与 `pytrans.translate` "
              f"产出的源码逐字节相同，退出码也一致")


@test
def test_lomtrans_python_keeps_and_consts():
    """`--keep` 与 `--const` 在 Python 那一门也要对得上。

    `--const` 在这门是**另一回事**：上游 `cenv` 是"模块常量 + 调用方给的"两张表合成
    （`dict.update` 的语义，**同名的以调用方为准**）—— 而模块常量本身照旧要发成
    `pub const`。这一条语料逼不出来，所以单独钉一条。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases = [
            ("keep", "def a(x: int) -> int:\n    return x\n\n"
                     "def b(y: int) -> int:\n    return y + 1\n", {"a"}, None),
            ("const_ext", "def f(a: int) -> int:\n    return a + K\n", None, {"K"}),
            ("const_override", "K = 1\n\ndef f(a: int) -> int:\n    return a\n",
             None, {"K"}),
        ]
        bad = []
        for name, src, keep, consts in cases:
            fp = td / (name + ".py")
            fp.write_text(src, encoding="utf-8", newline="")
            cargs = {k: "i64" for k in consts} if consts else None
            want, wrc = _py_want(src, keep=keep, consts=cargs)
            argv = [str(exe), "--lang", "python", str(fp)]
            if keep:
                argv += ["--keep", ",".join(sorted(keep))]
            if cargs:
                argv += ["--const", ",".join(f"{k}:{v}" for k, v in sorted(cargs.items()))]
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=120)
            if r.returncode != wrc or r.stdout != want:
                bad.append((name, r.returncode, wrc, want, r.stdout))
        assert not bad, ("\n".join(f"  {n}: rc={rc}/{wrc}\n    py={w!r}\n    tw={g!r}"
                                    for n, rc, wrc, w, g in bad))
        print(f"      {len(cases)} 份：`--keep` 与 `--const` 与上游一致")


@test
def test_lomtrans_python_out_of_subset_is_loud():
    """这一门**不做**的那一处（一行多条语句）必须**出声**。

    `x = 1; y = 2` 上游按 `;` 切开收成两条语句，这一门一条都不收。**不收可以，但得说**
    —— 静默少发一条语句，产物照样编得过，只是少算一步（`docs/167` 那条）。
    判的是**有没有声音**，不是有没有收。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        fp = td / "semi.py"
        fp.write_text("def f(a: int) -> int:\n    x = 1; y = 2\n    return x\n",
                      encoding="utf-8", newline="")
        r = subprocess.run([str(exe), "--lang", "python", str(fp)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           shell=False, timeout=120)
        assert r.returncode == 1, f"rc={r.returncode}（该报子集外）"
        assert r.stdout == "", f"一行多条语句不该悄悄发出去一半：{r.stdout!r}"
        print("      一行多条语句：不收，报子集外（退出码 1），不静默")


@test
def test_lomtrans_twin_selfhost_compiles():
    """`lomtrans.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。

    与第 16、17 格同一条：孪生不能只在自己这一套后端下面成立 —— 它得能被**已经
    自举过的那个编译器**吃下去，否则 S2 拆参考实现时这一步就断了。
    """
    import loment_dist  # noqa: E402

    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), TWIN.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lomtrans.lomt 失败: {r.stderr[-400:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lomtrans.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_trans_test")


if __name__ == "__main__":
    sys.exit(main())
