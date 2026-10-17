#!/usr/bin/env python3
# loment_multisyntax_test.py — 多语法前端判据 (docs/179, docs/175 §5/§6 第 4 条)
#
# **五种源语法**: C / Rust / Go / Java / Python。每种**十条**判据, 表驱动 ——
# 加一个语言只加一行数据, 而不是再抄十段测试。
#
# 十种判据 (每种语法各一份):
#   1 类型映射      标量/指针/字符串 -> 我们声明的 Potato 类型
#   2 结构体/类     聚合声明 -> `types` (字段逐个对)
#   3 函数签名      形参名与类型、返回值
#   4 ABI 归类      哪些是 `c`(能发 extern fn), 哪些是那一族自己的约定
#   5 发不出来的**被报出来**   名字 + 原因 (不是静默丢)
#   6 顺序保留      声明的先后不被重排
#   7 产物可检查    发出的 L1 过 `lomentc.check`
#   8 按内容识别    `.lomt` 装着这门语法 -> 认出来
#   9 确定性        同一份输入 -> 同一串字节
#  10 那条"腿"      见 §腿
#
# ## "腿": 链接型 vs 运行型 —— 两组语言真正分岔的地方
#
# `docs/173` 把"支持一个语言"分成两条腿, 而这一次动手让它在**前端**这一侧也显出来:
#
#   * **链接型 (C / Rust / Go)**: 函数的调用约定是平台 C ABI, 于是能发 `extern fn`,
#     由 `lomelf` 把外面编好的目标文件链进来 —— 第 10 条是**真跑**:
#     编目标文件 -> 生成接口单元 -> L1 调用 -> 链 -> 跑出预期退出码。
#   * **运行型 (Java / Python)**: 方法是 JVM/解释器的调用约定, **不是 C ABI**
#     (Java 的 `native` 走 JNI, 符号名前两个参数还是 `JNIEnv*`/`jobject`)。第 10 条
#     因此不是"链起来跑", 而是**钉住它们一条都不会被发成 `extern fn`** ——
#     那正是"要调它们得走进程桥"这条结论的机器可验形式。
#     它们**跑得通**, 判据在 `loment_ffi_test` 的进程桥那几条 (Go/Python/Java/JS/Perl/Lua)。
#
# 所以第 10 条在两组里形状不同, **不是偷懒, 是那两组本来就不一样**。
#
# 用法: python tools/loment_multisyntax_test.py   (无 clang/WSL 时相关条目 SKIP)

from __future__ import annotations

import contextlib
import importlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf                                                        # noqa: E402
import lomentc                                                       # noqa: E402
import lomt_from                                                     # noqa: E402
import potato                                                        # noqa: E402
import potato_from                                                   # noqa: E402
import loment_diag                                                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

# ---------------------------------------------------------------- 夹具 (每种语法一份)

C_SRC = """\
struct Pair { int a; int b; };

enum Mode { IDLE, RUN, DONE };
enum Err { EOK = 0, EBAD = -1, EFULL = 5 };

int c_add(int a, int b) { return a + b; }
int c_sum(const unsigned char *p, unsigned n) { return 0; }
int c_utoa(unsigned value, char *buf) { return 0; }
double c_avg(double x, double y) { return x; }
"""
RUST_SRC = """\
#[no_mangle]
pub extern "C" fn r_add(a: i32, b: i32) -> i32 { a + b }

pub struct Pt { pub x: i32, pub y: i32 }

pub enum Mode { Idle, Run }

pub fn r_plain(x: i32) -> i32 { x * 2 }

pub fn r_big(x: i64) -> usize { 0 }
"""
GO_SRC = """\
package main

type Pt struct {
    X int
    Y int
}

//export g_add
func g_add(a int, b int) int { return a + b }

func g_plain(x int) int { return x * 2 }

// `a, b int` —— **同类型共享名字**，Go 里极常见。它是一条**正面**用例：
// 早先 `from_go` 不收这个写法，整个函数被跳过（而判据把那个 bug 记成了期望值）。
func g_many(a, b int) int { return a }

func g_slice(n int) []int { return nil }
"""
JAVA_SRC = """\
package demo;

enum Level { LOW, MID, HIGH }

public class Shape {
    public static final int SIDES = 4;
    private int w;
    private int h;
    private double area;

    public Shape(int a, int b) { this.w = a; this.h = b; }

    public static int j_add(int a, int b) { return a + b; }

    public native int j_native(int a);
}
"""
PY_SRC = """\
def py_add(a: int, b: int) -> int:
    return a + b

def py_plain(x: int) -> int:
    return x * 2

def py_big(x: bytes) -> int:
    return 0
"""

LINK_C = {"compiler": "clang", "ext": ".c", "name": "e2e_add",
          "src": "int e2e_add(int a, int b) { return a + b; }\n"}
# 用 clang 编 Rust 源不成立 (clang 不认 `pub extern "C" fn`), 所以那条腿由 rustc 走
LINK_RUST = {
    "compiler": "rustc", "ext": ".rs",
    "src": ('#![no_std]\n#[no_mangle]\npub extern "C" fn e2e_add(a: i32, b: i32) -> i32 '
            '{ a + b }\n#[panic_handler]\nfn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n'),
    "name": "e2e_add",
}

#: 每种语法的全部期望值。**加一个语言 = 加一项, 不改测试代码。**
SYNTAXES = [
    {
        # **`leg` 从 `"link"` 改成 `"grammar"`**（2026-09-18，`docs/188` §3）：
        # 一份 C 写法写的单元**就是 Loment**，它不该被发成 `extern fn` —— 那不是
        # "接口单元"，那是**旧模型**（外源代码）的形状。它走的是 `--impl` 翻译，
        # 而翻译的判据在 `loment_ctrans_test`（那边真编真跑比数）。
        # **这里仍留一条第 10 条**，但问的是另一件事：一条 `extern fn` 都不该有。
        "lang": "c", "ext": ".c", "src": C_SRC, "leg": "grammar",
        "types": [("Pair", [("a", "i32"), ("b", "i32")])],
        # 不带值的枚举走 `enums`; **带值的走 `consts`** (值进不了 enums 的 schema,
        # 而它常常是协议常量 —— 丢值比丢名严重)。整个枚举要么进一边、要么进另一边,
        # **不拆开** (拆开会造出一个看着少了一个变体的枚举)。
        "enums": [("Mode", ["IDLE", "RUN", "DONE"])],
        "consts": [("EOK", "i32", 0), ("EBAD", "i32", -1), ("EFULL", "i32", 5)],
        # **第 4 项 = 源码里显式宣称的外部 ABI**（`docs/188` §3）。C 源里没有"宣称"
        # 这回事 —— 一份 C 写法写的单元**就是 Loment**（`language: "loment"`），
        # 所以它一个都不宣称，`emitted` 因此是空的：接口那条路对**表层语法**没有意义，
        # 要走的是 `--impl`（翻译）。真 C（要链 `.o` 的那种）由 Loment 源里的
        # `extern fn` 声明，见 `loment_ffi_test`。
        "fns": [("c_add", [("a", "i32"), ("b", "i32")], "i32", None),
                ("c_sum", [("p", "ptr"), ("n", "u32")], "i32", None),
                ("c_utoa", [("value", "u32"), ("buf", "str")], "i32", None)],
        "emitted": [],
        "skips": [("c_utoa", "没带正文"), ("c_avg", "无映射")],
        "link": None,
    },
    {
        "lang": "rust", "ext": ".rs", "src": RUST_SRC, "leg": "link",
        "types": [("Pt", [("x", "i32"), ("y", "i32")])],
        "enums": [("Mode", ["Idle", "Run"])],
        "fns": [("r_add", [("a", "i32"), ("b", "i32")], "i32", "c"),
                # 没写 `extern "C"` 的那两个**不再记 abi** —— 见 `docs/188` §3
                ("r_plain", [("x", "i32")], "i32", None),
                ("r_big", [("x", "i64")], "u64", None)],
        "skips": [],
        "emitted": ["r_add"],
        "link": LINK_RUST,
    },
    {
        "lang": "go", "ext": ".go", "src": GO_SRC, "leg": "c-abi-subset",
        "types": [("Pt", [("X", "i64"), ("Y", "i64")])],
        "fns": [("g_add", [("a", "i64"), ("b", "i64")], "i64", "c"),
                ("g_plain", [("x", "i64")], "i64", None),    # 没 `//export` -> 不记 abi
                # `g_many(a, b int)` —— **同类型共享名字**。2026-09-18 做 Go 那一门时
                # 发现 `from_go` **不收**这个写法（Go 里极常见），于是整个函数被跳过，
                # 而这条判据把那个 bug 当成期望值记着。现在它是一条**正面**用例：
                # 分组形参解析得出来，两个名字都进 `params`。
                ("g_many", [("a", "i64"), ("b", "i64")], "i64", None)],
        "skips": [("g_slice", "无映射")],
        "emitted": ["g_add"],
        "link": None,
    },
    {
        "lang": "java", "ext": ".java", "src": JAVA_SRC, "leg": "runtime",
        "types": [("Shape", [("w", "i32"), ("h", "i32")])],
        "consts": [("SIDES", "i32", 4)],
        "enums": [("Level", ["LOW", "MID", "HIGH"])],
        "fns": [("j_add", [("a", "i32"), ("b", "i32")], "i32", None),
                ("j_native", [("a", "i32")], "i32", None)],
        "skips": [("Shape", "构造器")],
        "emitted": [],
        "link": None,
    },
    {
        "lang": "python", "ext": ".py", "src": PY_SRC, "leg": "runtime",
        "types": [],
        "fns": [("py_add", [("a", "i64"), ("b", "i64")], "i64", None),
                ("py_plain", [("x", "i64")], "i64", None),
                ("py_big", [("x", "ptr")], "i64", None)],
        "skips": [],
        "emitted": [],
        "link": None,
    },
]

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _rustc() -> str | None:
    return shutil.which("rustc")


def _rust_has_target() -> bool:
    """**这份工具链装没装**这个目标的 `core` —— 不是"rustc 认不认识这个名字"。

    原先是 `rustc --print target-list`, 而那个名单对**所有**内置目标都为真: 它回答的是
    "名字在不在内置表里", 与有没有这个目标的 `core` 无关。本机没装 rustc 时这一族判据
    一律 SKIP, 所以错在哪看不出来; `#371` 让它们在 runner 上**原生跑**之后现形 ——
    容器里 apt 装的 rustc **认识** `x86_64-unknown-none`, 却没装它的 core, 于是判据红在
    "工具链不全"上 (`error[E0463]: can't find crate for core … may not be installed`),
    而那不是被测行为。
    """
    rustc = _rustc()
    if not rustc:
        return False
    r = subprocess.run([rustc, "--print", "sysroot"], capture_output=True,
                       text=True, shell=False)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    d = Path(r.stdout.strip()) / "lib" / "rustlib" / "x86_64-unknown-none" / "lib"
    return d.is_dir() and any(d.glob("libcore*.rlib"))


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _transcribe(spec: dict) -> tuple[dict, object]:
    doc, rep = potato_from.LANGS[spec["lang"]](spec["src"], f"m{spec['ext']}", "strict")
    assert not rep.validate_errors, f"{spec['lang']}: {rep.validate_errors[:2]}"
    assert potato.validate(doc) == [], f"{spec['lang']}: {potato.validate(doc)[:2]}"
    return doc, rep


def _emit(spec: dict) -> tuple[str, list]:
    doc, _rep = _transcribe(spec)
    return lomt_from.emit_lomt(doc)


# ---------------------------------------------------------------- 十种判据, 每种语法一份

def _mk(spec: dict):
    lang = spec["lang"]

    def t_types_map():
        """1 类型映射: 这门语言的标量/指针/字符串映到我们声明的 Potato 类型。"""
        doc, _ = _transcribe(spec)
        got = {f["name"]: [p["type"] for p in f["params"]] for f in doc["functions"]}
        for name, params, _ret, _abi in spec["fns"]:
            assert name in got, f"{lang}: 没抽出函数 {name}（抽到 {sorted(got)}）"
            assert len(got[name]) == len(params), f"{lang}/{name}: 形参数 {got[name]}"

    def t_aggregate_becomes_type():
        """2 聚合与常量：结构体/类 -> `types`，枚举 -> `enums` 或 `consts`，常量 -> `consts`。

        **带值的枚举走 `consts`**（值进不了 `enums` 的 schema，而它常常是协议常量 ——
        丢值比丢名严重）；不带值的走 `enums`。整个枚举要么进一边、要么进另一边，
        **不拆开** —— 拆开会造出一个"看着少了一个变体"的枚举。
        """
        doc, _ = _transcribe(spec)
        got_t = {t["name"]: [(f["name"], f["type"]) for f in t["fields"]]
                 for t in doc["types"]}
        for name, fields in spec.get("types", []):
            assert name in got_t, f"{lang}: 没有类型 {name}（有 {sorted(got_t)}）"
            assert got_t[name] == fields, f"{lang}/{name}: {got_t[name]} != {fields}"
        got_e = {e["name"]: list(e["variants"]) for e in doc["enums"]}
        for name, vs in spec.get("enums", []):
            assert name in got_e, f"{lang}: 没有枚举 {name}（有 {sorted(got_e)}）"
            assert got_e[name] == vs, f"{lang}/{name}: {got_e[name]} != {vs}"
        got_c = {c["name"]: (c["type"], c["value"]) for c in doc["consts"]}
        for name, ty, val in spec.get("consts", []):
            assert got_c.get(name) == (ty, val), \
                f"{lang}: 常量 {name} = {got_c.get(name)} 期望 {(ty, val)}"

    def t_signatures():
        """3 函数签名: 形参名、形参类型、返回类型都对得上。"""
        doc, _ = _transcribe(spec)
        got = {f["name"]: ([(p["name"], p["type"]) for p in f["params"]], f["ret"])
               for f in doc["functions"]}
        for name, params, ret, _abi in spec["fns"]:
            assert got.get(name) == (params, ret), \
                f"{lang}/{name}: {got.get(name)} != {(params, ret)}"

    def t_abi_classification():
        """4 ABI 归类: **只有源码显式宣称**的那个才记 `abi`（`docs/188` §3）。

        这一条是**这整套东西的安全阀**: 把普通 Rust `pub fn` 当 C ABI 调、
        把 Go 的 `//export` 与普通 `func` 当成一回事 —— 都是**错编**。

        2026-09-18 改：**"因为这是个 X 文件"那条推断删了**。它把"用 X 写法写的
        Loment"当成外国货，于是整批函数被 ABI 闸门挡掉、转出一个空 module。
        现在 `None` 有确定的意思：**它是 Loment 函数**。
        """
        doc, _ = _transcribe(spec)
        got = {f["name"]: f.get("abi") for f in doc["functions"]}
        for name, _params, _ret, abi in spec["fns"]:
            assert got.get(name) == abi, f"{lang}/{name}: abi={got.get(name)!r} 期望 {abi!r}"
            assert abi is None or abi in potato.ABIS, \
                f"{lang}: abi {abi!r} 不在 {potato.ABIS}"
        assert doc.get("grammar") == lang, f"{lang}: grammar={doc.get('grammar')!r}"

    def t_unsupported_are_reported():
        """5 发不出来的**必须被报出来** (名字 + 原因) —— 静默丢比拒绝坏。"""
        _doc, rep = _transcribe(spec)
        # **整条流水线**的跳过项: 转写那一步 + 发射那一步。只看后者会让"前一步丢的"
        # 看起来像没丢 (2026-09-17 实测撞过一次, docs/179 §6.3)。
        _text, emit_skips = _emit(spec)
        reasons = {s["name"]: s["why"] for s in rep.skipped}
        for n, why in emit_skips:
            reasons.setdefault(n, why)
        for name, sub in spec["skips"]:
            assert name in reasons, f"{lang}: {name} 没被报为跳过（报了 {sorted(reasons)}）"
            assert sub in reasons[name], f"{lang}/{name}: 原因 {reasons[name]!r} 不含 {sub!r}"

    def t_order_preserved():
        """6 顺序保留: 抽出来的函数顺序 = 源里的顺序。"""
        doc, _ = _transcribe(spec)
        want = [n for n, _p, _r, _a in spec["fns"]]
        assert [f["name"] for f in doc["functions"]] == want, \
            f"{lang}: {[f['name'] for f in doc['functions']]} != {want}"

    def t_unit_is_checkable():
        """7 产物可检查: 发出的 L1 单元过 `lomentc.check` (它要进冻结核心)。"""
        text, _sk = _emit(spec)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / f"m{spec['ext']}.lomt"
            p.write_text(text, encoding="utf-8", newline="\n")
            mod = lomentc.load(p)
            errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, p.parent, entry=p))
            assert not errs, f"{lang}: 发出的 L1 过不了检查: {errs[:2]}\n{text[:400]}"

    def t_detected_from_content():
        """8 按内容识别: 一份 `.lomt` 装这门语法, `resolve_lang` 要认出来。"""
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "m.lomt"
            p.write_text(spec["src"], encoding="utf-8", newline="\n")
            got, why = potato_from.resolve_lang(p, "auto")
            assert got == lang, f"认成 {got!r}（{why}）, 期望 {lang!r}"

    def t_emission_deterministic():
        """9 确定性: 同一份输入 -> 同一串字节 (产物要能进判据)。"""
        a, _ = _emit(spec)
        b, _ = _emit(spec)
        assert a == b, f"{lang}: 两次发射不一致"

    def t_the_leg():
        """10 那条腿。三档, 见文件头 §腿 —— **分档本身就是这一条的结论**。

        * `link`           C / Rust: C ABI 是语言级默认 -> 真编目标文件、真链、真跑。
        * `c-abi-subset`   Go: **只有 `//export` 过的**是 C ABI。这一条钉住
                           "哪些发得出来、哪些发不出来" —— 它同时也是"不能整门语言
                           一概而论"的机器可验形式。
                           (不做真链: Go 的 C ABI 目标文件要 `-buildmode=c-archive`,
                           还得有目标平台的 C 交叉工具链; 它跑得通那条在
                           `loment_ffi_test` 的进程桥里。)
        * `runtime`        Java / Python: 一条都不是 C ABI -> 钉住"一条都不许发
                           `extern fn`"。要调它们走进程桥 (docs/173 §4)。
        """
        text, _sk = _emit(spec)
        decls = [ln.split("pub extern fn ", 1)[1].split("(", 1)[0]
                 for ln in text.splitlines() if ln.startswith("pub extern fn")]
        want = spec["emitted"]                # 应该出现在产物里的 extern fn
        if spec["leg"] in ("runtime", "grammar"):
            # `runtime` = Java / Python：调用约定不是 C ABI，**不能**发 `extern fn`。
            # `grammar` = C：它是**表层语法**，写出来的是 Loment 函数，`abi` 缺席，
            # 所以同样一条都不该发（`docs/188` §3）。两条理由不同，结论一样。
            assert not decls, f"{lang}: 不该有 extern fn 声明: {decls}"
            return
        if spec["leg"] == "c-abi-subset":
            assert decls == want, f"{lang}: 发出来的 {decls} != 该发的 {want}"
            return
        assert decls == want, f"{lang}: 发出来的 {decls} != 该发的 {want}"
        if not link_ready(spec):
            print(f"      SKIP {lang} 端到端: 缺工具链")
            return
        _link_roundtrip(spec)

    return [(f"{lang}_1_types", t_types_map),
            (f"{lang}_2_aggregate", t_aggregate_becomes_type),
            (f"{lang}_3_signatures", t_signatures),
            (f"{lang}_4_abi", t_abi_classification),
            (f"{lang}_5_skips_reported", t_unsupported_are_reported),
            (f"{lang}_6_order", t_order_preserved),
            (f"{lang}_7_checkable", t_unit_is_checkable),
            (f"{lang}_8_detected", t_detected_from_content),
            (f"{lang}_9_deterministic", t_emission_deterministic),
            (f"{lang}_10_leg", t_the_leg)]


def link_ready(spec: dict) -> bool:
    if spec["leg"] != "link":
        return False
    if not _wsl():
        return False
    return bool(_clang()) and (spec["link"]["compiler"] != "rustc"
                               or (_rustc() and _rust_has_target()))


def _link_roundtrip(spec: dict) -> None:
    """真跑: 编目标文件 -> 接口单元 -> L1 调用 -> 链 -> 跑出 42。"""
    lg = spec["link"]
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / f"{lg['name']}{lg['ext']}"
        src.write_text(lg["src"], encoding="utf-8", newline="\n")
        iface, skipped = _iface_from(td, lg["name"], lg["src"], spec["lang"])
        assert f"pub extern fn {lg['name']}" in iface.read_text(encoding="utf-8"), \
            (spec["lang"], skipped)
        obj = td / "a.o"
        if lg["compiler"] == "clang":
            cmd = [_clang(), "--target=x86_64-unknown-linux-gnu", "-x", "c", "-c", "-O1",
                   "-ffreestanding", "-fno-stack-protector", "-o", str(obj), str(src)]
        else:
            cmd = [_rustc(), "--target", "x86_64-unknown-none", "--crate-type",
                   "staticlib", "--emit=obj", "-O", "-o", str(obj), str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"{spec['lang']} 编目标文件失败: {r.stderr[-300:]}"
        # 产物非空 —— clang 把不认识的输入当 linker input 时会 **rc=0 且不出东西**
        assert obj.exists() and obj.stat().st_size > 0, "没产出目标文件 (漏了 -x c?)"
        _run_with(td, td / f"{lg['name']}_iface.lomt", [obj],
                  40 + 2, lg["name"])       # 40 + 2 = 42


def _iface_from(td: Path, name: str, src: str, lang: str) -> tuple[Path, list]:
    doc, rep = potato_from.LANGS[lang](src, f"{name}.x", "strict")
    text, skipped = lomt_from.emit_lomt(doc)
    out = td / f"{name}_iface.lomt"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out, [(s["name"], s["why"]) for s in rep.skipped] + skipped


def _run_with(td: Path, iface: Path, objs: list[Path], exit_want: int, fname: str) -> None:
    main = td / "main.lomt"
    main.write_text(f'module ms\n\nuse "{iface.as_posix()}"\n\n'
                    "fn _start() {\n"
                    f"    syscall4(60, {fname}(40 as i32, 2 as i32) as u64, 0, 0);\n"
                    "}\n", encoding="utf-8", newline="\n")
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, errs
    # `allow_ports=True`：外源语法编出来的那些 `.o` 是**真的外源对象**，链接器默认会因为
    # "可能带端口"而拒（`docs/222` §4 的门）；这条要测的是"七门语法跑同一条管线"。
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps),
                               [lomelf.load_foreign(p) for p in objs], allow_ports=True)
    exe = td / "ms.elf"
    exe.write_bytes(blob)
    r = subprocess.run([*_WSLCMD, "bash", "-lc",
                        f"cp {_wsl_path(exe)} /tmp/ms_{exe.stat().st_size}.elf && "
                        f"chmod +x /tmp/ms_{exe.stat().st_size}.elf && "
                        f"/tmp/ms_{exe.stat().st_size}.elf"],
                       capture_output=True, text=True, timeout=120, shell=False)
    assert r.returncode == exit_want, \
        f"退出码 {r.returncode} != {exit_want} (stderr {r.stderr[-200:]!r})"


# ---------------------------------------------------------------- 跨语法的判据

@test
def test_five_syntaxes_and_ten_tests_each():
    """这一条钉住**规模本身**: 五种语法, 每种十条 —— 少一条就红。

    它看着像废话判据, 但它防的是一件真事: 加语言/加判据时漏掉一条 (比如给新语言
    只写了 8 条), 而**没有任何东西会告诉你**。规模是有人明确要过的, 就该有判据钉着。
    """
    langs = [s["lang"] for s in SYNTAXES]
    assert len(set(langs)) >= 4, f"至少四种语法, 现在是 {langs}"
    for lang in langs:
        n = len([1 for name, _f in TESTS if name.startswith(f"{lang}_")])
        assert n >= 10, f"{lang} 只有 {n} 条判据 (至少 10)"
    per = {g["lang"]: len([1 for n, _f in TESTS if n.startswith(g["lang"] + "_")])
           for g in SYNTAXES}
    print(f"      {len(langs)} 种语法 × 每种至少 10 条 = "
          f"{sum(per.values())} 条: {per}")


@test
def test_sniffing_separates_languages_that_share_keywords():
    """`class` 在 Python/Java 里都有, `package` 在 Go/Java 里都有 —— 必须分得开。

    这两处是**实测撞出来的**: 加 Java 之后, 一份 Java 源码被判成 `go`(都有 `package`),
    而没切开的 `class` 会让 Java 被判成 `python`。判据钉住切开了。
    """
    cases = [
        ("python", "class A:\n    pass\n"),
        ("java", "class A {\n    int x;\n}\n"),
        ("java", "package com.example;\npublic class A { }\n"),
        ("go", "package main\n\nfunc f() { }\n"),
        ("go", "package main\n\ntype T struct {\n    X int\n}\n"),
        ("rust", "struct T {\n    x: i32,\n}\n"),
        ("c", "struct T { int x; };\n"),
        # **`enum X { … }` 在 C / Java / Rust 里长得一样** -> 它不是任何一门的判据,
        # 只能靠文件里**别的**特征定位。这一组钉住"带枚举的 C 不许被判成 java"。
        ("c", "enum Color { RED, GREEN };\n\nint f(int a) { return a; }\n"),
        ("java", "enum Color { RED, GREEN }\n\nclass T { int x; }\n"),
        # **`import` 在 Python 与 Java 里都有** -> 按**行尾分号**分开。一份带 import 的
        # 真 Java 文件原先被内容兜底判成 `python`, 然后 `ast.parse` 在 `package a.b;`
        # 上抛一坨 traceback (2026-09-17, docs/179 §8.1 第 6 条)。
        ("java", "package a.b;\nimport java.util.List;\npublic class T { int x; }\n"),
        ("python", "import os\n\nclass A:\n    x: int\n"),
    ]
    for want, src in cases:
        got, why = potato_from.detect_lang(src)
        assert got == want, f"认成 {got!r}（{why}）, 期望 {want!r}: {src!r}"
    # 反过来: Loment 不许被认成任何一种外源语法
    for snippet in ("module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
                    "fn f() { return 1; }\n",
                    "struct S { a: u32 }\n"):
        got, why = potato_from.detect_lang(snippet)
        assert got != "c", f"Loment 被认成 C（{why}）: {snippet!r}"
    print("      共 7 组易混的形状都分得开 (class/package/struct 各自归属)")


@test
def test_grammar_and_abi_are_declared_in_the_schema():
    """`grammar` 与 `abi` 都必须在 `potato` 的表里 —— 校验器与前端不许各说各话。

    2026-09-18 改（`docs/188` §3）：**查的对象从 `abi` 换成了 `grammar`**。
    `abi` 现在是"源码显式宣称的外部 ABI"，大多数函数没有它（`None`）；
    而每种写法都必须落进 `potato.GRAMMARS` —— 那张表是**出厂锁**的官方表。
    """
    for s in SYNTAXES:
        doc, _ = _transcribe(s)
        assert doc["grammar"] in potato.GRAMMARS, (s["lang"], doc.get("grammar"))
        assert doc["language"] == "loment", (s["lang"], doc["language"])
        for f in doc["functions"]:
            a = f.get("abi")
            assert a is None or a in potato.ABIS, (s["lang"], f["name"], a)
    for g in ("loment",) + tuple(s["lang"] for s in SYNTAXES):
        assert g in potato.GRAMMARS, f"前端支持 {g} 而 schema 的 GRAMMARS 里没有"
    print(f"      GRAMMARS = {potato.GRAMMARS}；五种写法都记 grammar，language 一律 loment")


@test
def test_diag_does_not_tell_you_to_fix_valid_foreign_source():
    """诊断工具**不许**对一份语法正确的 C/Java 说"改那一行的写法"。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for s in SYNTAXES:
            p = td / f"m{s['ext']}.lomt"           # 后缀是 .lomt, 内容是外源语法
            p.write_text(s["src"], encoding="utf-8", newline="\n")
            note = loment_diag.foreign_note(p, ['63:25: 非法字符 "\'"'])
            assert note and "不是 Loment" in note, (s["lang"], note)
            # **不锁大小写**: 语言名现在取自 `LANG_CARDS` 的显示名（`Rust` / `Python` …）——
            # 一处真源，也是给人看的那个写法。这条要钉的是"说得出是哪门语言"，
            # 不是"它必须写成大写的"。
            assert s["lang"].lower() in note.lower(), (s["lang"], note)
        lp = td / "t.lomt"
        lp.write_text("module t\n\nfn f() -> u32 {\n    return 1\n}\n",
                      encoding="utf-8", newline="\n")
        assert loment_diag.foreign_note(lp, ["5:1: 期望 ;，得到 '}'"]) is None
        assert loment_diag.foreign_note(td / "m.c", []) is None
    print("      五种语法都给出对得上的提示; Loment 的 typo 不给")


@test
def test_diag_does_not_call_a_native_declaration_foreign():
    """**声明里写的"原生"语法不是"外源"** —— `rust` 是 `NATIVE_GRAMMARS` 里那个。

    `choose write grammar rust` 的文件**就是** Loment（只是拼法，`docs/188` §2、
    2026-09-22 的裁定），前门当原生读。而这一处原先对 `lang in LANGS` 一律给外源卡 ——
    `rust` 也在 `LANGS` 里，于是它说"**这个文件不是 Loment 语法**、别照上面那条改"，
    而上面那条（**补 `module`**）**恰恰是对的**：一条该听的话被一句"别听"撤销。

    **但只排"声明"那一路**：一份**真 Rust 源码**叫 `.rs`、没写声明 —— 那时按**后缀**
    认，而那句提示**是对的**（那份内容确实不是 Loment）。靠 `resolve_lang` 给的
    `why`（`文件头声明 …` / `后缀 …`）分。一刀切把 `rust` 整个排掉，
    会把那条真该说的话一起消掉（`SYNTAXES` 里那条 `.rs` 用的就是真 Rust 源码）。
    """
    import loment_diag                                                # noqa: PLC0415
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        d = td / "r.lomt"                      # 声明是 rust、正文是 Loment
        d.write_text("choose write grammar rust\n\nfn f() -> i32 {\n    return 1;\n}\n",
                     encoding="utf-8", newline="\n")
        assert loment_diag.foreign_note(d, ["2:1: 期望 module，得到 'fn'"]) is None, \
            "对一份 `choose write grammar rust` 的 Loment 说了'不是 Loment'"
        # 反面一：真 Rust 源码（`.rs`、没声明）**照旧**要点名
        r = td / "real.rs"
        r.write_text("#[no_mangle]\npub fn f() {}\n", encoding="utf-8", newline="\n")
        note = loment_diag.foreign_note(r, ["1:1: 期望 module，得到 '#'"])
        assert note and "不是 Loment" in note and "rust" in note.lower(), note
        # 反面二：声明为**外源**的照旧要点名
        c = td / "x.lomt"
        c.write_text("choose write grammar c\nint f() { return ~1; }\n",
                     encoding="utf-8", newline="\n")
        note = loment_diag.foreign_note(c, ["2:22: 非法字符 '~'"])
        assert note and "不是 Loment" in note, note
    print("      声明为原生的不给'外源'提示；真 Rust 源码与声明为外源的照旧点名")


@test
def test_dbg_refuses_foreign_grammar():
    """`loment dbg` 对别的写法要**拒** —— 它给的 `文件:行` 两头都不对。

    `dbg` 过前门，拿到的是**译文**的模块：DWARF 行表里写的是**译文**的行号，
    而 `mod.src` 是 `None`（`lomentc.load`：翻译出来的单元**不声称**源文件是那一份）。
    于是输出指向一个**不存在的地方**——实测一份 **9 行**的 C 写法文件报 `…\\t.lomt:10`，
    而且路径丢了一截、分隔符混用。**退 0**，看着像成功。

    只钉"拒"，不钉输出格式：`dbg` 那条路要 clang 才能走完，而这一条在**用不着 clang**
    的那一步就该拦住（判据因此在本机与 CI 上都能跑）。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "d.lomt"
        p.write_text("choose write grammar c\n\nunsigned int f(unsigned int x) {\n"
                     "    return x + 1;\n}\n", encoding="utf-8", newline="\n")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment.py"),
                            "dbg", str(p), "--fn", "f"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        out = r.stdout + r.stderr
        assert r.returncode != 0, f"没拒（rc=0）:\n{out[:200]}"
        assert "只处理**原生**写法" in out, f"没给出说得通的拒绝理由:\n{out[:200]}"
        # 那份"文件:行"走的是 **stdout** —— 拒绝之后它必须是空的
        # （不能拿整段出来查 `.lomt:`：拒绝消息自己就带上路径了）。
        assert r.stdout.strip() == "", f"还吐了个 文件:行:\n{r.stdout[:200]}"
    print("      `loment dbg` 认出非原生写法就拒，不给不存在之处的 文件:行")


@test
def test_the_cross_unit_gate_does_not_depend_on_call_position():
    """"本单元没有的函数"这条闸，**不许**因为调用恰好写在语句位就绕过去。

    `trans_core.raw()` 对 `ast.Call` 原先**不查** `self.fns`（`ty_of` 才查）——
    于是同一个 `acquire(16);`：写在**值位**被拒、写在**语句位**照发。
    判决取决于"恰好写在哪个位置"不是设计，是**漏**：那道闸的用意是"跨单元调用要
    显式声明"，与写法位置无关。（`docs/198` §3 报过这一条，说根在共用核、
    不只 Python 那一门。）
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        msgs = []
        for i, body in enumerate(("int f() { acquire(16); return 7; }\n",
                                  "int f() { return acquire(16); }\n")):
            p = td / f"t{i}.lomt"
            p.write_text("choose write grammar c\n" + body, encoding="utf-8", newline="\n")
            try:
                potato_from.front_door(p)
            except lomt_from.NotRepresentable as e:
                msgs.append(str(e))
            else:
                raise AssertionError(f"第 {i} 种（{'语句位' if i == 0 else '值位'}）没拒")
        for m in msgs:
            assert "acquire" in m and "本单元没有的函数" in m, m
        # 反面：调**已在本单元**的函数照旧（语句位也不许被误拒）
        p = td / "ok.lomt"
        p.write_text("choose write grammar c\nint g(int x) { return x; }\n"
                     "int f() { g(1); return 7; }\n", encoding="utf-8", newline="\n")
        assert "g(1);" in potato_from.front_door(p).source
    print("      语句位与值位给同一个判决；调本单元的函数照旧")


@test
def test_forward_declaration_is_merged_and_a_real_duplicate_is_not():
    """**前向声明并进它的定义**；**真重复照旧报**。

    C 里 `int g(int);` 后面跟 `int g(int x) { … }` 是**最常规**的写法 —— 互相递归的
    两个函数**只能**这么写。对象里于是一个名字有两条记录（一条有正文、一条没有），
    而下游报"**函数 g 重复**"：一个**假的重复**，用户只写了一份定义。
    （`docs/186` §4 承诺"报得出是哪个函数"，而这里报的是一个不存在的冲突。）

    四种形状各钉一面：声明+定义 → 并成一条；两条声明 → 并成一条（下游说"只有声明
    没有定义"，那是对的）；**两条都有正文 → 不并**（真重复，一并就把一份写重复了的
    源悄悄收下了 —— 那比报错坏）；互相递归 → 现在**翻得过**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        def fns_of(i: int, body: str) -> list[str]:
            p = td / f"t{i}.lomt"          # **路径要不同** —— front_door 按路径备忘
            p.write_text("choose write grammar c\n" + body, encoding="utf-8", newline="\n")
            return [x.split("(")[0].replace("pub fn ", "")
                    for x in potato_from.front_door(p).source.splitlines()
                    if x.startswith("pub fn")]
        assert fns_of(0, "int g(int x);\nint g(int x) { return x; }\n"
                         "int main() { return g(7); }\n") == ["g", "main"]
        assert fns_of(1, "int odd(int n);\n"
                         "int even(int n) { if (n == 0) { return 1; } return odd(n - 1); }\n"
                         "int odd(int n) { if (n == 0) { return 0; } return even(n - 1); }\n"
                         "int main() { return even(4); }\n") == ["odd", "even", "main"]
        for i, body in ((2, "int g(int x) { return x; }\nint g(int x) { return x; }\n"
                            "int main() { return g(1); }\n"),
                        (3, "int g(int x);\nint g(int x);\nint main() { return 7; }\n")):
            p = td / f"t{i}.lomt"
            p.write_text("choose write grammar c\n" + body, encoding="utf-8", newline="\n")
            try:
                potato_from.front_door(p)
            except lomt_from.NotRepresentable:
                pass
            else:
                raise AssertionError(f"第 {i} 种该拒（真重复 / 只有声明）却过了")
    print("      前向声明并进定义、互相递归翻得过；真重复与只有声明照旧拒")


@test
def test_an_unmappable_return_type_names_the_declaration_not_the_call_site():
    """返回类型映不上时，报的必须是**那条声明**，不是几行之外的**调用点**。

    `float f() { … }` 在转写那一步就没进对象（"返回类型 'float' 无映射"）。原先用户
    看到的是下游那句"**调用了本单元没有的函数 `f`**"—— 指到 `main` 里那个调用点，
    而真正该改的是**三行之上**那条声明。

    **这一条是第二批（`#74` 那一笔）修好的**：前门原先把 `potato_from` 的
    `Report.skipped` 整个丢掉（`doc, _rep = LANGS[lang](…)`），于是转写期丢的东西
    在编译器这条路上一个字都不说、只剩下游那句假消息。这里把它**钉住** ——
    没有判据的话，那句话改回去也没人知道。
    """
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "f.lomt"
        p.write_text("choose write grammar c\nfloat f() { return 7; }\n"
                     "int main() { return f(); }\n", encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(p)
        except lomt_from.NotRepresentable as e:
            msg = str(e)
            assert "float" in msg and "无映射" in msg, f"没说清是返回类型的问题:\n{msg}"
            assert "'f'" in msg or "`f`" in msg, f"没点名那条声明:\n{msg}"
            assert "调用了本单元没有的函数" not in msg, \
                f"又只报调用点了（真因在那条声明上）:\n{msg}"
        else:
            raise AssertionError("`float` 返回类型映不上，却没拒")
    print("      返回类型映不上时报的是那条声明（点名 + 真因），不是调用点")










#: 照 2026-09-17 一个子 agent **真写出来的那份 C** 蒸馏的 (无 `#include`、无 libc),
#: 刻意保留了三处当时把工具链绊倒的形状: Allman `{`、`unsigned` 单独写、
#: 单引号字符字面量 `'0'`。它由下面那条端到端每次真跑 —— 见 `docs/179` §6。
C_IN_LOMT = """\
// 一份 C 源码 后缀是 lomt

int c_fact(int n)
{
    int acc = 1;
    int i;
    if (n < 0) {
        return 0;
    }
    for (i = 2; i <= n; i = i + 1) {
        acc = acc * i;
    }
    return acc;
}

unsigned c_gcd(unsigned a, unsigned b)
{
    unsigned t;
    while (b != 0) {
        t = a % b;
        a = b;
        b = t;
    }
    return a;
}

// 2026-09-18: 原来写的是 `int n = 0; … n = n + (int)(v & 1u);`。
// 那两样都过不了翻译器的 Stage A 子集，而且**第二样是真的错** ——
// `n`（i32）加 `v & 1`（u32）在 C 里靠隐式转换成立，**Loment 里是类型错**。
// 写成类型干净的版本（仍然是合法 C）：全用 `unsigned`。
unsigned c_popcount(unsigned v)
{
    unsigned n = 0;
    while (v != 0) {
        n = n + (v & 1);
        v = v >> 1;
    }
    return n;
}

"""

#: **子集外**的那一份（2026-09-18 从 `C_IN_LOMT` 里拆出来的）。
#:
#: 拆的理由是 `--impl` **全有或全无**：一份源里有一个子集外的函数，整份就翻不了。
#: 而 `char *` 与 `(char)` 强转在翻译器的 Stage A 子集之外（`docs/186` §4），
#: 所以它没法跟上面那三个混在一起。它现在钉的是"**响亮拒绝**"那条。
C_OUT_OF_SUBSET = """\
// 把数字写进调用方缓冲 返回写了几位
int c_utoa(unsigned value, char *buf)
{
    unsigned scale = 1;
    unsigned v = value;
    int n = 0;
    while (v >= 10) {
        v = v / 10;
        scale = scale * 10;
    }
    while (scale > 0) {
        buf[n] = (char)('0' + (value / scale) % 10);
        n = n + 1;
        scale = scale / 10;
    }
    return n;
}
"""


@test
def test_c_source_named_lomt_end_to_end():
    """**一份 C 源码后缀写成 `.lomt`** —— `docs/175` §5 那条原话的完整形态。

    ## 2026-09-18：这条路从"接口单元 + 链 clang"改成了"**翻译**"

    原来是：认出 C → 发 `pub extern fn` 接口单元 → clang 编 `.o` → 链进来 → 跑。
    那是**旧模型**（把 C 当外源代码）。按 `docs/188` §3，一份 C 写法写的单元
    **就是 Loment**，所以它该走 `--impl` 翻成 `pub fn` —— **不用 clang 编目标文件，
    也不用链**。

    判据本身没变，还是那句"**翻译出来的 Loment 跑出的数 == 直接编那份 C 跑出的数**"
    （同一个形状在 `loment_ctrans_test` 里更全 —— 那边有三份语料）。这里钉的是
    **后缀那一层**：后缀写 `.lomt`、内容是 C，照样认得出、照样跑得起来。

    **`char *` 那个函数挪走了**（`C_OUT_OF_SUBSET`）：`--impl` 是**全有或全无**的
    （一份源里有一个子集外的函数，整份就翻不了），所以带 `char *buf` 的那种没法
    和这三个混在一份里。它现在钉的是"**响亮拒绝**"，见下面那条断言。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / "mymod.lomt"                 # ← 后缀是 .lomt, 内容是 C
        src.write_text(C_IN_LOMT, encoding="utf-8", newline="\n")
        got_lang, why = potato_from.resolve_lang(src, "auto")
        assert got_lang == "c", f"认成 {got_lang!r}（{why}）"

        # 翻译那一步（`--impl`）：接的是 `grammar`，不再接 `language`
        doc, _rep = potato_from.from_c(C_IN_LOMT, "mymod.lomt", "strict")
        assert doc["language"] == "loment" and doc["grammar"] == "c", doc["language"]
        impl, skipped = lomt_from.emit_lomt(doc, impl=True)
        assert not skipped, skipped
        # **一条 `extern fn` 都不该有** —— 它不是外国货，是 Loment
        assert "pub extern fn " not in impl, impl[:300]
        assert "pub fn c_fact(" in impl and "pub fn c_gcd(" in impl, impl[:300]
        unit = td / "mymod.impl.lomt"
        unit.write_text(impl, encoding="utf-8", newline="\n")

        # 子集外的那个（`char *buf`）**必须响亮报错**，而且指得到点上
        doc2, _r2 = potato_from.from_c(C_OUT_OF_SUBSET, "bad.c", "strict")
        try:
            lomt_from.emit_lomt(doc2, impl=True)
        except lomt_from.NotRepresentable as e:
            assert "char" in str(e) or "子集外" in str(e), str(e)
        else:
            raise AssertionError("`char *` 在子集外，却一个字都没报")

        want = (720 + 21 + 13) % 256            # 6! + gcd(1071,462) + popcount(0xbeef)
        main = td / "main.lomt"
        main.write_text(f'module msc\nchoose runtime\n\nuse "{unit.as_posix()}"\n\n'
                        "fn _start() {\n"
                        "    let a: u32 = c_fact(6 as i32) as u32;\n"
                        "    let b: u32 = c_gcd(1071 as u32, 462 as u32);\n"
                        "    let c: u32 = c_popcount(48879 as u32) as u32;\n"
                        "    syscall4(60, ((a + b + c) % 256) as u64, 0, 0);\n"
                        "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(main)
        deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs
        # **没有 `load_foreign`** —— 没有目标文件要链了
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
        exe = td / "ms.elf"
        exe.write_bytes(blob)
        rr = subprocess.run([*_WSLCMD, "bash", "-lc",
                             f"cp {_wsl_path(exe)} /tmp/ms_cinlomt.elf && "
                             f"chmod +x /tmp/ms_cinlomt.elf && /tmp/ms_cinlomt.elf"],
                            capture_output=True, text=True, timeout=120, shell=False)
        assert rr.returncode == want, \
            f"退出码 {rr.returncode} != {want} (stderr {rr.stderr[-200:]!r})"

        # ---- 对照：把同一份 C 用 clang 直接编出来跑，数要一样（**比数不比文本**）
        cside = td / "c_side.c"
        cside.write_text(C_IN_LOMT + "\nstatic void _x(long c){\n"
                         '    __asm__ volatile("syscall" : : "a"(60L), "D"(c)'
                         ' : "rcx", "r11", "memory");\n}\n'
                         "void _start(void){ _x((c_fact(6) + c_gcd(1071,462)"
                         " + c_popcount(48879)) % 256); }\n",
                         encoding="utf-8", newline="\n")
        binp = td / "c_side.bin"
        r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-O1",
                            "-ffreestanding", "-nostdlib", "-fno-stack-protector",
                            "-o", str(binp), str(cside)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        rc2 = subprocess.run([*_WSLCMD, "bash", "-lc",
                              f"chmod +x {_wsl_path(binp)} && {_wsl_path(binp)}; echo -n $?"],
                             capture_output=True, text=True, timeout=120, shell=False)
        assert int(rc2.stdout.strip()) == want, f"clang 那边 {rc2.stdout} != {want}"
    print(f"      C 装 `.lomt`: 认出 -> 翻译成 Loment -> 跑出 {want}（clang 编那份 C 也是 {want}）")


@test
def test_unrepresentable_object_is_rejected():
    """对象里有 L1 那侧接不上的东西 (traits / layouts / imports) -> **报错退出**。"""
    base = potato_from._blank("m", "c")
    for key, val in (("traits", [{"name": "T", "methods": ["m"]}]),
                     ("layouts", [{"name": "H", "size": 8, "endian": "little",
                                   "packed": True,
                                   "fields": [{"name": "a", "type": "u32", "offset": 0}]}]),
                     ("imports", ["other"])):
        doc = json.loads(json.dumps(base))
        doc[key] = val
        try:
            lomt_from.emit_lomt(doc)
        except lomt_from.NotRepresentable as e:
            assert key in str(e), (key, str(e))
            continue
        raise AssertionError(f"{key} 非空却没有报错")
    print("      traits / layouts / imports 非空 -> 一律报错退出")


@test
def test_field_level_drops_are_reported_too():
    """**字段级跳过也要出声** —— 语料库那条 (§8) 撞出来的第 1 个口子。

    五个前端都在调 `Report.skip_field`, 而 `lomt_from` 原先**只打印**实体级的
    `skipped` —— 一份 struct 少两个字段 (`scale: float` / `names: list`),
    `[OK]` 那行干干净净, 一个数都不变。字段不进"实体分母"是对的 (见 `skip_field`
    的注释), 但**不计数不等于不报告**。

    这条测的是**工具的输出**, 不是某份对象 —— 所以它必须走 `lomt_from.main`,
    而不是直接调 `transcribe`。
    """
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "cfg.py"
        src.write_text("class Cfg:\n    base: int\n    scale: float\n    names: list\n",
                       encoding="utf-8", newline="\n")
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = lomt_from.main([str(src), "--lang", "auto"])
        assert rc == 0, err.getvalue()
    text = err.getvalue()
    for f in ("Cfg.scale", "Cfg.names"):
        assert f"[skip] {f}:" in text, f"{f} 没出声:\n{text}"
    # 出声之后**仍然不许进产物** —— 报出来是为了让人知道, 不是为了硬塞一个 ptr
    assert "scale" not in out.getvalue(), out.getvalue()
    print("      `float`/`list` 字段: 报出来 (且仍不进产物)")


@test
def test_declaration_forms_that_used_to_vanish():
    """三种**最平常**的声明写法曾经静默消失, 一处一种语言 (docs/179 §8.1)。

    共同点: 丢了以后从产物上**看不出来** —— 少一个常量, 而汇总行不变、`[skip]` 没有。
    语料里也各放了一份 (§8.2), 这条夹具把**形态本身**钉死。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # Python: 带注解的常量 / 负号 / 移位表达式 —— 原先只认"裸整数字面量 + 赋值"
        py = td / "c.py"
        py.write_text("MAX: int = 8\nLOW = -5\nSHIFT = 1 << 4\n",
                      encoding="utf-8", newline="\n")
        doc, _ = potato_from.transcribe(py, "python")
        got = {c["name"]: c["value"] for c in doc["consts"]}
        assert got == {"MAX": 8, "LOW": -5, "SHIFT": 16}, got
        # Rust: `pub const` 原先**一条都不抽** (那 5 个文件丢了 14 个)
        rs = td / "c.rs"
        rs.write_text("pub const MASK: u32 = 0xFF;\npub const NEG: i32 = -5;\n",
                      encoding="utf-8", newline="\n")
        doc, _ = potato_from.transcribe(rs, "rust")
        assert {c["name"]: c["value"] for c in doc["consts"]} == {"MASK": 255, "NEG": -5}
        # Java: `void` 原先报"返回类型 'void' 无映射" —— **那句提示本身是错的**,
        # `void` 完全表示得了 (C 那边一直映 `"()"`)。注意它仍然是 `abi: "java"`,
        # 照样不会被发成 `extern fn` —— 修的是**表示**, 不是**调用约定**。
        jv = td / "D.java"
        jv.write_text("public class D {\n    private int b;\n"
                      "    public void set_b(int v) { }\n}\n",
                      encoding="utf-8", newline="\n")
        doc, rep = potato_from.transcribe(jv, "java")
        fn = [x for x in doc["functions"] if x["name"] == "set_b"]
        assert fn and fn[0]["ret"] == "()", doc["functions"]
        # 2026-09-18：**不再记 `abi`** —— "因为这是个 Java 文件"是推断，
        # 按 `docs/188` §3 删了。`void` 那条修的是**表示**，仍然成立：
        # 它照样不会被发成 `extern fn`（`abi` 缺席 = 它是 Loment 函数）。
        assert "abi" not in fn[0], fn[0]
        assert doc["grammar"] == "java" and doc["language"] == "loment", doc["grammar"]
        assert not [x for x in rep.skipped if "void" in x["why"]], rep.skipped
    print("      Python 注解/负号/移位、Rust `pub const`、Java `void` 都进得来")


@test
def test_functions_are_found_wherever_they_start():
    """**同一行上的第二个函数**也是一条声明，不能因为"不在行首"就看不见（C / C++）。

    `_C_FN` 原先锚 `^[ \\t]*`（配 `re.M`），于是 `int f(){ … } int g(){ … }` 里 `g`
    **整个消失** —— 而且不是"被跳过"：对象里没有它、`skipped` 里也没有它，下游只会说
    "调用了本单元没有的函数 `g`"（一个**指向别处**的错）。这与 `_C_FIELD` 是同一处毛病
    （`struct P { int a; int b; };` 一行写完只抽得到第一个字段），那一处已经改过。

    **为什么不能只把锚去掉**：那个锚本来挡的是"函数**体**里与定义同形的写法" ——
    `Foo bar(1);` 在 C++ 里就是**变量声明**，与 `Foo bar(...) {...}` 只差一个 `{`。
    所以 `_from_c` 里按"已认下的函数体范围"再挡一道（`claimed`）。这一条两面都钉。
    """
    # ① 两个函数同一行：都要在
    doc, rep = potato_from.from_c("int f(int a){return a+1;} int g(int a){return a+2;}\n",
                                  "t.c", "strict")
    assert [f["name"] for f in doc["functions"]] == ["f", "g"], doc["functions"]
    assert not rep.skipped, rep.skipped
    # ② 顶层语句 + 函数同一行（`int g = 7; int main(void){…}`）
    doc, _ = potato_from.from_c("int g = 7; int main(void){ return 5; }\n", "t.c", "strict")
    assert [f["name"] for f in doc["functions"]] == ["main"], doc["functions"]
    # ③ 三个同一行
    doc, _ = potato_from.from_c(
        "int a1(){return 1;} int a2(){return 2;} int a3(){return 3;}\n", "t.c", "strict")
    assert [f["name"] for f in doc["functions"]] == ["a1", "a2", "a3"], doc["functions"]
    # ④ **反面**：体里那些与定义同形的写法不是函数。
    #    - 裸的调用（`g(1)`）本来就不该匹配；
    #    - C++ 的 `Foo bar(1);` **会**匹配那条正则，靠 `claimed` 挡下来。
    doc, _ = potato_from.from_c("int f(){ return g(1); }\nint g(int x){ return x; }\n",
                                "t.c", "strict")
    assert [f["name"] for f in doc["functions"]] == ["f", "g"], doc["functions"]
    doc, _ = potato_from.from_cpp("int f(){ Foo bar(1); return 0; }\n", "t.cpp", "strict")
    assert [f["name"] for f in doc["functions"]] == ["f"], doc["functions"]
    # ⑤ 被跳过的定义，**体内**同样要被认下来（不然它的体会被当成一串函数）
    doc, rep = potato_from.from_c("float f(){ int x = 1; return 0; }\nint g(){ return 1; }\n",
                                  "t.c", "strict")
    assert [f["name"] for f in doc["functions"]] == ["g"], doc["functions"]
    assert [x["name"] for x in rep.skipped] == ["f"], rep.skipped
    print("      同行多函数都找得到；体内与定义同形的写法（含 C++ 的 `Foo bar(1);`）不被误认")


@test
def test_unrecognised_toplevel_content_is_reported():
    """顶层"谁也不认识"的东西要**报出来**，不能让它悄悄蒸发。

    `_from_c` 只有三条正则（`_C_STRUCT` / `_C_ENUM` / `_C_FN`）扫顶层，**扫不到的既不进
    产物、也不进 `skipped`** —— 单元于是带着**半份内容**被发出去，而 `check` 判 `[OK]`。
    `docs/186` §4 把"全局变量 / 聚合类型 / 预处理指令"逐条列进**点名拒绝**那一栏。

    两面都钉：七种认不出的形状**必须报**；而认识的形状（含 `struct` / `enum` 后面那个
    分号、以及注释里的 `#`）**不许**被误报 —— 残渣检测最容易错的就是"顺手报多了"。
    """
    for label, head in {
        "typedef": "typedef int myint;\n",
        "global":  "int g = 7;\n",
        "union":   "union U { int a; long b; };\n",
        "include": "#include <stdio.h>\n",
        "define":  "#define N 5\n",
        "extern":  "extern int g;\n",
        "knr":     "int f(a, b) int a; int b; { return a + b; }\n",
    }.items():
        doc, rep = potato_from.from_c(head + "int main() { return 5; }\n", "t.c", "strict")
        got = [x for x in rep.skipped if "没有对应的规则" in x["why"]]
        assert got, f"{label}: 没报出来（skipped={rep.skipped}）"
        assert [f["name"] for f in doc["functions"]] == ["main"], (label, doc["functions"])
    # 反面：认识的形状不许被当成残渣（`struct` / `enum` 尾随的分号也算它们的）
    doc, rep = potato_from.from_c(
        "struct P { int a; };\nenum E { A, B };\nint main() { return 5; }\n", "t.c", "strict")
    assert not [x for x in rep.skipped if "没有对应的规则" in x["why"]], rep.skipped
    assert [t["name"] for t in doc["types"]] == ["P"], doc["types"]
    # 注释里的 `#` 不算（注释先被等长抹掉）
    _, rep = potato_from.from_c("/* #define X 5 */\nint main() { return 5; }\n", "t.c", "strict")
    assert not rep.skipped, rep.skipped
    print("      顶层认不出的七种形状都报得出来；struct/enum 与注释里的 # 不误报")


@test
def test_class_languages_report_unrecognised_toplevel_content():
    """**Java / C# 那一族也要报"谁也不认识"的顶层内容**（`#121` / `#75`）。

    同一个口子在 C 那边（批次 1）就补上了（上面那条判据），而 `_from_class_lang`
    当时**漏了** —— 于是：

      * `use "x.lomt"` / `use bytes`（**Loment 语法**，这一族没有对应构造）静默蒸发；
      * 类里一条谁也不认识的成员（方法头写坏成 `static int bad( { … }`、嵌套类、
        静态初始化块、带注解的方法）**静默消失**。

    两种都是"整份单元少一块内容，而 `check` 判 OK"。**两面都钉**：认不出的必须报；
    认识的（一个正常的类 + 枚举、以及类里的常量/字段/方法）**不许**被误报 ——
    残渣检测最容易错的就是"顺手报多了"。
    """
    NL = chr(10)
    java_good = ("class K {" + NL + "    static final int N0 = 5;" + NL
                 + "    int a;" + NL + "    static int f() { return 1; }" + NL + "}" + NL
                 + "enum E { A, B }" + NL)
    cs_good = ("class K {" + NL + "    const int N0 = 5;" + NL
               + "    int a;" + NL + "    static int f() { return 1; }" + NL + "}" + NL
               + "enum E { A, B }" + NL)
    for lang, fn, good in (("java", potato_from.from_java, java_good),
                           ("csharp", potato_from.from_csharp, cs_good)):
        # ① `use` 行：既不进产物、也不该无声无息
        doc, rep = fn('use "d_packdef.lomt"' + NL + "use bytes" + NL + NL + good, "t.x", "strict")
        left = [x for x in rep.skipped if "没有对应的规则" in x["why"]]
        assert len(left) == 2, f"{lang}: 两条 use 没报出来: {rep.skipped}"
        assert [f["name"] for f in doc["functions"]] == ["f"], (lang, doc["functions"])
        # ② 写坏的成员：原来整条消失（连 `[skip]` 都没有）
        broken = ("class K {" + NL + "    static int good() { return 7; }" + NL
                  + "    static int bad( { return 1; }" + NL + "}" + NL)
        doc2, rep2 = fn(broken, "t.x", "strict")
        lost = [x for x in rep2.skipped if "不是常量 / 字段 / 方法" in x["why"]]
        assert lost, f"{lang}: 认不出的成员没报出来: {rep2.skipped}"
        assert [f["name"] for f in doc2["functions"]] == ["good"], (lang, doc2["functions"])
        # ③ 反面：正常的类 + 枚举、类里的常量/字段/方法都不许被当成残渣
        # **壳行也不算**（`namespace` / `using` / 两层壳的花括号）—— 它们是**结构**，
        # `class` / `enum` 那两条正则已经把类体认下来了。不排掉的话，一份平常的 C# 文件
        # （`loment/cstrans/*.cs` 三份语料都是这个形状）会因为这些行被**整份拒掉**。
        _, rep3 = fn(good, "t.x", "strict")
        assert not [x for x in rep3.skipped if "没有对应的规则" in x["why"]], (lang, rep3.skipped)
        assert not [x for x in rep3.skipped if "不是常量 / 字段 / 方法" in x["why"]], (lang, rep3.skipped)
        shelled = ("using System;" + NL + NL + "namespace D" + NL + "{" + NL
                   + "    public class T" + NL + "    {" + NL
                   + "        public static int f(int a) { return a; }" + NL
                   + "    }" + NL + "}" + NL)
        _, rep4 = fn(shelled, "t.x", "strict")
        left4 = [x for x in rep4.skipped if "没有对应的规则" in x["why"]]
        assert not left4, f"{lang}: shell rows were reported as leftovers: {left4}"
    print("      Java / C#：use 与写坏的成员都报得出来；正常的类与枚举不误报")


@test
def test_front_door_refuses_when_the_transcribe_step_drops_something():
    """**转写那一步丢的东西也要到用户面前**（既有 #74 的实质）。

    `potato_from` 的 `Report.skipped` 与 `lomt_from.emit_lomt` 的 `skipped` 是两处：
    前者是"**根本没进对象**"（顶层 `typedef` / 全局量 / `union` / 预处理指令 / K&R 函数），
    后者是"进了对象但发不出来"。`front_door` 一直只看后者，把前者整个丢掉
    （`doc, _rep = LANGS[lang](…)`）—— 于是这些东西**在编译器这条路上一个字都不说**，
    而 `lomt_from.py` 那条 CLI 会打 `[skip]`。**两副面孔，而编译器那副是瞎的。**

    这条同时钉住那个"假 skip"：Java / C# 的**类壳**（没有字段的类）原先无条件报一条
    `无可用字段` —— 那不是丢东西，它只是方法的命名空间。不清掉它，"有 skip 就拒"
    会把**每一份** Java / C# 语料都拒掉（实测：7 门语料里 6 份带这条假 skip）。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for label, body in [("typedef", "typedef int myint;\nint main() { return 5; }\n"),
                            ("include", "#include <stdio.h>\nint main() { return 5; }\n"),
                            ("knr", "int f(a, b) int a; int b; { return a + b; }\n"),
                            ("use", "use bytes\nint f() { return 1; }\n")]:
            p = td / f"{label}.lomt"
            p.write_text("choose write grammar c\n" + body, encoding="utf-8", newline="\n")
            try:
                potato_from.front_door(p)
            except lomt_from.NotRepresentable:
                pass
            else:
                raise AssertionError(f"{label}: 转写期丢了东西，前门却放过了")
        # 反面一：干净的单元照过
        good = td / "ok.lomt"
        good.write_text("choose write grammar c\nint main() { return 5; }\n",
                        encoding="utf-8", newline="\n")
        assert potato_from.front_door(good).source
        # 反面二：Java / C# 的**类壳**（无字段）不算丢
        for name, src in (
            ("java", "public class Sample {\n    public static int f() {\n        return 1;\n    }\n}\n"),
            ("csharp", "class Sample {\n    static int F() {\n        return 1;\n    }\n}\n"),
        ):
            doc, rep = potato_from.LANGS[name](src, f"Sample.{name}", "strict")
            assert not rep.skipped, f"{name}: 类壳被当成丢东西了 {rep.skipped}"
    print("      转写期丢的东西前门会拒；类壳（无字段）不受影响")


@test
def test_bad_literals_and_identifiers_are_refused_at_the_users_line():
    """词法上"读不了"的东西要**当着用户那一行**说，而不是甩 Python 内部异常。

    两处都在共用词法表里，六门一起受影响：

    * **前导零的整数字面量**（C 的八进制，`010` == 8）。`primary` 用的是
      `int(t[1], 0)` —— 那是**按 Python 的读法**认进制，它**拒绝前导零**，与 C **正好相反**。
      原先漏出裸 `ValueError`：整屏回溯、没有行号、退出码 1（与"子集外"同码不同因），
      而三条防线（`emit_lomt` / `front_door` / `lomentc.load_unit`）**一条都拦不住**。
    * **标识符里的 `\\w` 是 Unicode 的**。`_变量` 这种名字前端收下、产物却过不了本语言的
      词法（那边是 ASCII），报出来还带着**生成单元**的行号。现在收紧成 `[A-Za-z0-9_]`。

    两边都钉：**拒得到**（且行号是用户的），而 `0x1F` / 十进制 / ASCII 标识符**照旧**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for label, body in [("octal", "int main() { return 010; }\n"),
                            ("cjk", "int main() { int _变量 = 46; return _变量; }\n")]:
            p = td / f"{label}.lomt"
            p.write_text("choose write grammar c\n" + body, encoding="utf-8", newline="\n")
            try:
                potato_from.front_door(p)
            except lomt_from.NotRepresentable as e:
                assert "第 2 行" in str(e), f"{label}: 行号不是用户那一行 —— {str(e)[:120]}"
            except ValueError as e:
                raise AssertionError(f"{label}: 漏出裸 Python 异常 {e}")
            else:
                raise AssertionError(f"{label}: 读不了，却没拒")
        for okk in ("int main() { return 0x1F; }\n", "int main() { return 8; }\n",
                    "int main() { int _v1 = 46; return _v1; }\n"):
            p = td / "ok.lomt"
            p.write_text("choose write grammar c\n" + okk, encoding="utf-8", newline="\n")
            assert potato_from.front_door(p).source
    print("      八进制与 Unicode 标识符在**用户那一行**被拒；hex/十进制/ASCII 照旧")


@test
def test_entry_layer_refuses_foreign_grammar_instead_of_mangling_it():
    """**"读 L1 源的入口"认出别的写法就拒**（`docs/182` §1.9 那条消费者轴）。

    `lomfmt` / `lomdoc` 是**独立入口**：它们不走前门，直接拿词法器读原文。而一份
    `.lomt` 里可以装六种写法的任何一种。对 C 那门后果很重：

        choose write grammar c unsigned int f(unsigned int x) {     ← 声明与函数头揉成一行

    **声明那一行整个是"怎么读"**：前门按它定读法、再把它抹成等长空白 ⇒ 那一行上的
    函数跟着一起消失，单元变成空的。`lomfmt --write` 是**原地写回**，用户那份源就这么
    被改坏了（实测：改之前跑一次，前门译文里那个函数**没了**）。

    `lomdoc` 那边更绕：它**过前门**拿到的是**译文**的模块（函数带的是**译文行号**），
    却拿**原文**去按那些行号索引 —— 短文件 `IndexError` 甩栈；长文件不越界，于是
    **不崩、给出张冠李戴的文档**（`fn alpha` 拿到 `beta` 的注释），退 0。

    两面都钉：非原生写法**必须拒**（`fmt` 还**不许动文件**），原生与 `rust` **照旧**。
    """
    import lomdoc                                                     # noqa: PLC0415
    import lomfmt                                                     # noqa: PLC0415
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "w.lomt"
        p.write_bytes(("choose write grammar c\n\nunsigned int f(unsigned int x) {\n"
                       "    return x + 1;\n}\n").encode("utf-8"))
        before = p.read_bytes()
        assert lomfmt.main(["--write", str(p)]) == 2, "fmt 对 C 写法没拒"
        assert p.read_bytes() == before, "fmt --write 把用户那份源改了"
        assert lomfmt.main([str(p)]) == 2, "fmt（stdout 模式）没拒"
        assert lomdoc.main([str(p)]) == 1, "doc 对 C 写法没拒"
        # 原生与 `rust`（`NATIVE_GRAMMARS` 里那个）照旧
        for tag, src in (("native.lomt", "module m\n\nfn f() -> i32 {\n    return 1;\n}\n"),
                         ("rust.lomt", "choose write grammar rust\n\nmodule m\n\n"
                                       "fn f() -> i32 {\n    return 1;\n}\n")):
            q = td / tag
            q.write_text(src, encoding="utf-8", newline="\n")
            assert "fn f" in lomfmt.format_source(q.read_text(encoding="utf-8")), tag
    print("      fmt/doc 认出非原生写法就拒（fmt 不动文件）；原生与 rust 照旧")



@test
def test_array_typed_struct_field_does_not_invalidate_the_object():
    """`[T; N]` 当**结构体字段**: 校验器曾经把它判成"未声明", 于是**整份对象非法**、
    `lomt_from` 直接 `[ERR]` 退出 —— 一个字段的问题毁掉整个模块, 比 skip 更坏。

    根因是**同一批字段被查了两遍**, 后一遍用裸字符串相等, 而且它的名单里
    **不含 enums** (docs/179 §8.1 第 4 条)。两个 agent **各自独立**撞到。
    """
    doc = potato_from._blank("m", "rust")
    doc["types"] = [{"name": "W", "fields": [{"name": "n", "type": "u32"},
                                             {"name": "w", "type": "[u64; 8]"}]}]
    assert not potato.validate(doc), potato.validate(doc)
    # 枚举类型的字段也一起钉: 后一遍的 `declared` 曾经漏掉 enums
    doc["enums"] = [{"name": "S", "variants": ["A", "B"]}]
    doc["types"].append({"name": "U", "fields": [{"name": "s", "type": "S"}]})
    assert not potato.validate(doc), potato.validate(doc)
    print("      `[u64; 8]` 与枚举类型的字段都过得了校验")


#: 多语法**语料**: 目录名就是它声称的语法。
#: 布局 = `loment/examples/multisyntax/<语法>/<NN-名字>/<名字>.lomt`。
#: 文件后缀是 `.lomt` —— 也就是走**内容兜底**那条路, 后缀一点忙都帮不上。
CORPUS = ROOT / "loment" / "examples" / "multisyntax"
#: 每个语法至少要有的项目数 (三个 agent × 5 个项目 = 15)。
CORPUS_MIN = {"python": 5, "rust": 5, "java": 5}


@test
def test_multisyntax_corpus_projects_all_check():
    """语料库: **每个项目的目录名就是它声称的语法**, 逐条验到底。

    这一条比上面那批**更接近真事**: 那批是**我**照语法写的小夹具, 这里是**别人照自己
    习惯写的整项目**。2026-09-17 实测, 它一次撞出六个"静默丢东西"的口子 ——
    `float` 字段、`AnnAssign` 常量、Rust `pub const`、Java `void`、枚举类型的字段,
    以及 `[T; N]` 字段让**整份对象**被判非法。夹具照着我写的规则写, 撞不出这些;
    **只有不受控的输入能** —— 这就是语料库存在的理由。

    钉住四件事, 缺一不可:
      1. **认对** —— `detect_lang` 的结果要等于目录名 (目录名是**声称**, 不是输入);
      2. **转成合法对象** —— v1 schema 校验干净 (`validate_errors` 为空);
      3. **产物非空** —— 前三件事在一份"什么都没抽出来"的对象上**也全都成立**,
         所以必须数一遍声明。这条是这组里最容易漏、也最要命的一条;
      4. **过 `lomentc.check`** —— 产出的 L1 单元得真能编。
    """
    if not CORPUS.is_dir():
        raise AssertionError(f"语料目录不在: {CORPUS}")
    seen: dict[str, int] = {}
    for langdir in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        claim = langdir.name
        assert claim in potato_from.LANGS, f"目录名不是已知语法: {claim}"
        for proj in sorted(p for p in langdir.iterdir() if p.is_dir()):
            src = next(iter(sorted(proj.glob("*.lomt"))), None)
            assert src is not None, f"{proj} 里没有 .lomt"
            text = src.read_text(encoding="utf-8")
            got, why = potato_from.detect_lang(text)
            assert got == claim, f"{src}: 认成 {got!r}（{why}）, 目录声称 {claim}"
            doc, rep = potato_from.transcribe(src, claim)
            assert not rep.validate_errors, (src, rep.validate_errors)
            out, _skipped = lomt_from.emit_lomt(doc)
            n_decl = sum(out.count("pub " + k)
                         for k in ("const ", "struct ", "enum ", "extern fn "))
            assert n_decl > 0, f"{src}: 产物是空的 (一个声明都没有)"
            with tempfile.TemporaryDirectory() as t:
                unit = Path(t) / (src.stem + ".iface.lomt")
                unit.write_text(out, encoding="utf-8", newline="\n")
                mod = lomentc.load(unit)
                deps = lomentc.resolve_deps(mod, ROOT, unit.parent, entry=unit)
                errs = lomentc.check(mod, deps=deps)
                assert not errs, (src, errs[:3])
            seen[claim] = seen.get(claim, 0) + 1
    for lang, least in CORPUS_MIN.items():
        n = seen.get(lang, 0)
        assert n >= least, f"{lang} 只有 {n} 个项目 (至少 {least})"
    print(f"      {sum(seen.values())} 个语料项目全部认对/合法/非空/过检: {seen}")


for _spec in SYNTAXES:
    for _name, _fn in _mk(_spec):
        TESTS.append((_name, _fn))


#: 报行号那条用的夹具：六门各一份，**前面还有一段正文**，失败的那一处落在后面 ——
#: 拼接的偏移只有这样才看得出来（单函数、第一段正文从第 1 行起时，"函数体内的行号"
#: 常常正好等于"文件里的行号"，那种夹具即使病灶还在也照样绿）。
#: 后两份是缺陷报告里那个**最小复现**（`~` 在第 3 行）与它的多行注释变体，
#: 照抄下来让文档里那两句话机器可验。
_LINE_MARK = "nope"
_LINE_CASES = [
    ("c", "x.c",
     "int a(int x) { return x + 1; }\n\nint b(int v) {\n    return nope(v);\n}\n"),
    ("cpp", "x.cpp",
     "int a(int x) { return x + 1; }\n\nint b(int v) {\n    return nope(v);\n}\n"),
    ("java", "T.java",
     "public class T {\n    public static int a(int x) { return x + 1; }\n\n"
     "    public static int b(int v) {\n        return nope(v);\n    }\n}\n"),
    ("csharp", "T.cs",
     "public class T {\n    public static int a(int x) { return x + 1; }\n\n"
     "    public static int b(int v) {\n        return nope(v);\n    }\n}\n"),
    ("python", "x.py",
     "def a(x: int) -> int:\n    return x + 1\n\ndef b(v: int) -> int:\n    return nope(v)\n"),
    ("go", "x.go",
     "package main\n\nfunc a(x int) int {\n    return x + 1\n}\n\n"
     "func b(v int) int {\n    return nope(v)\n}\n"),
    ("java", "Tilde.java",
     "public class T {\n    public static int f(int v) {\n        return ~v;\n    }\n}\n"),
    # 类壳那一族（Java/C#）的正文**要按剥离后的下标回原文里数行号**，所以剥注释
    # 必须**保长度**：`sub(" ", …)` 会把一段多行注释压成一个空格，于是后面所有行
    # **整体上移** —— 报出来的行号指到别处（这一门原先正是这么写的）。
    # 共用引擎同一条路，所以只留 Java 这一份。
    ("java", "Comment.java",
     "public class C {\n    /* 一段\n       跨行注释 */\n\n"
     "    public static int f(int v) {\n        return ~v;\n    }\n}\n"),
]


@test
def test_subset_error_line_is_the_file_line():
    """子集外的写法报的行号必须是**文件里的行号**，不是"函数体内"的行号。

    走前门编译一份别的写法时，`lomt_from.emit_lomt` 把各函数正文拼成**一份源**再交给
    翻译器，而 `trans_core` 那几十处 `第 {n} 行` 数的是**它拿到的那串文本**的行号。
    原先裸拼（`"\\n".join`）—— 同一个文件里十几个函数，拼起来与文件完全对不上，
    用户在源码里按那个行号**找不到东西**（`docs/179` §6.5：错要指在错的地方）。
    实测：`~` 在文件第 3 行，报出来是"第 2 行"。

    **两个入口各验一次**（`docs/182` §1.9：一条判据盖不住多个入口）：

      * **前门**（`potato_from.front_door`）—— 偏移就是在拼接那一步丢的，这里是**主**症状；
      * **`translate()`** —— 直接拿**整份源**翻的那条路。它本来就对，这一条钉住它
        **保持**对：修前门时最省事的错法（改成"一个函数翻一次再拼"）正是从这一处漏出去。

    期望的行号**从源里推**（那一处标记落在第几行），不写死一个数 —— 写死的话
    夹具一改就成了"两边都错成一样"，那正是这条缺陷原先能躺着的原因。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for lang, fname, src in _LINE_CASES:
            mark = "~" if "~" in src else _LINE_MARK
            want = next(i + 1 for i, ln in enumerate(src.splitlines()) if mark in ln)
            # 夹具自证**能分辨两种读法**：函数体内的行号与文件里的行号必须不同，
            # 否则这条判据测的是一个"两种读法都对"的输入（等于没测）。
            doc, _ = potato_from.LANGS[lang](src, fname, "strict")
            fn = next(f for f in doc["functions"] if mark in (f.get("body") or ""))
            rel = want - fn["body_line"] + 1
            assert rel != want, (f"[{lang}] 夹具分辨不出两种读法（rel==want=={want}）")
            # 入口 2：`translate()` 拿整份源
            mod = importlib.import_module(lomt_from._TOOLS[lang][0])
            errs = tuple(getattr(mod, e) for e in lomt_from._TOOLS[lang][1].split())
            try:
                mod.translate(src)
            except errs as e:            # type: ignore[misc]
                assert f"第 {want} 行" in str(e), (
                    f"[{lang}/translate] 期望第 {want} 行（体内的说法是第 {rel} 行）: {e}")
            else:
                raise AssertionError(f"[{lang}] 子集外的写法一个字都没报")
            # 入口 1：**前门**（potato_from -> lomt_from --impl）
            p = td / fname
            p.write_text(src, encoding="utf-8", newline="\n")
            try:
                potato_from.front_door(p)
            except lomt_from.NotRepresentable as e:
                assert f"第 {want} 行" in str(e), (
                    f"[{lang}/前门] 期望第 {want} 行（体内的说法是第 {rel} 行）: {e}")
            else:
                raise AssertionError(f"[{lang}] 前门把子集外的写法放过去了")
    print(f"      {len(_LINE_CASES)} 份源：前门与 translate() 报的都是文件里的行号")


@test
def test_body_line_does_not_leak_into_the_emitted_unit():
    """`body_line` 是**给诊断用的**定位信息，**不进产物**。

    `--impl` 发出来的 `.lomt` 是给别人看、也给别人编的源码。垫空行如果漏进产物，
    它就不只是"多几个空行"：`docs/176` 那类逐字节比对会当场红，而且同一份源
    在不同的函数顺序下会出不同的字节。这一条钉住"产物只由**语义**决定"。
    """
    src = (ROOT / "loment" / "jtrans" / "Bits.java").read_text(encoding="utf-8")
    doc, _ = potato_from.from_java(src, "Bits.java", "strict")
    assert all(f.get("body_line") for f in doc["functions"]), "夹具没带上 body_line"
    with_line, _ = lomt_from.emit_lomt(doc, impl=True)
    # 把定位信息摘掉：产物**一个字节都不该变**（拼接那一步只影响报错里的行号）
    stripped = {**doc, "functions": [{k: v for k, v in f.items() if k != "body_line"}
                                     for f in doc["functions"]]}
    without, _ = lomt_from.emit_lomt(stripped, impl=True)
    assert with_line == without, "body_line 漏进产物了"
    print("      --impl 的单元与 body_line 无关（垫空行只影响报错里的行号）")


def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_multisyntax_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
