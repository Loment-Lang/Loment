#!/usr/bin/env python3
# loment_p8_test.py — P8 自举自检 (M79–, docs/150)
#
# 运行: python tools/loment_p8_test.py   (退出码 0 = 全绿)
# M79: Loment 版 lexer 的 token 流必须与 Python 版 (lomc.lex) 一致。

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lomentc  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

# ---------------------------------------------------------------- 结果复用
#
# 这套判据里最贵的一件事是"同一个二进制编译同一个大单元": 驱动编译 `driver.lomt`
# 本身就要 ~55s（`-O0`）/~9s（`-O1`，见 `_build_linux_elf`）, 而**同一件事**在几个判据
# 里各跑一遍 —— 全套里最大的一项开销就落在这一项上。
#
# 缓存只用在"输入确定 ⇒ 输出确定"的计算上, 三条边界把它框住:
#
#   1. **只缓存纯计算**: 单元文本、参考实现的 IR、按 (IR 字节) 链出来的二进制、
#      以及"缓存二进制 + 仓库里的相对入口"的一次运行。运行之所以可以复用, 是因为
#      判据运行期间**树不变**, 而链接与驱动都是确定性的 —— 同一份字节跑两遍给同一份
#      产出。反过来, 任何**临时夹具**（探测用例、`deps/` 工程、自定义后缀工程 …）都
#      不进缓存: 它们每次写在不同目录, 内容也随测试而变, 键只能靠绝对路径区分,
#      所以规则取"**入口是仓库相对路径才缓存**"（夹具一律递 WSL 绝对路径）。
#   2. **缓存只活在本进程**, 进程结束即失效; 构建产物落在一个临时目录里, 由 atexit
#      删掉。判据各用自己的 `TemporaryDirectory`, 缓存目录独立于它们。
#   3. **不缓存断言**: 每个判据自己的比较、棘轮、正负例判定原样保留。
_CACHE_DIR = Path(tempfile.mkdtemp(prefix="loment-p8-cache-"))
atexit.register(shutil.rmtree, _CACHE_DIR, ignore_errors=True)

_MISS = object()
_UNIT_LL: dict[Path, str | Exception] = {}
#: 已构建的 Windows 侧可执行文件（词法器/parser/checker/codegen, 按 tag）。产物落在
#: `_CACHE_DIR`: 每个判据都有自己的 `TemporaryDirectory`, 放进那里的话它一退出就被删,
#: 后面的判据就无从复用了。
_BUILT: dict[str, Path] = {}
_DEP_PATHS: dict[Path, list[Path]] = {}
_UNIT_TEXT: dict[Path, str] = {}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _unit_ll(target: Path) -> str:
    """参考实现在该文件上发出来的 IR（`load` + `resolve_deps(entry=target)` + `emit_llvm`）。

    这条装载路径是本文件里**绝大多数**判据取 `want` 的方式（语料循环里每个文件还要
    取两遍: 一次在 `_unsupported` 里试能不能发, 一次在正文里当对照）, 而它是树上一个
    纯函数。异常同样缓存 —— 参考实现发不出来的文件每次都会抛同一个, 重算没有信息量。
    """
    key = target.resolve()
    got = _UNIT_LL.get(key, _MISS)
    if got is _MISS:
        try:
            mod = lomentc.load(target)
            deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
            got = lomentc.emit_llvm(mod, ROOT, deps)
        except Exception as e:  # noqa: BLE001
            got = e
        _UNIT_LL[key] = got
    if isinstance(got, Exception):
        raise got
    return got


ROOT = Path(__file__).resolve().parent.parent
LEXER = ROOT / "loment" / "selfhost" / "lexer.lomt"
TESTS: list[tuple[str, object]] = []
#: kind 编码 —— **与两个词法器同表**（`lomc.Tok.kind` / `lexer.lomt` 头那条注释）。
#: `raw` = 外部代码块的正文（`docs/185`），走它自己的分支（见 `_python_tokens`）。
KIND = {"ident": 0, "number": 1, "string": 2, "punct": 3, "eof": 4, "raw": 5}


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    unsigned char *out = malloc(20 * ((size_t)n + 8));
    unsigned int cnt = lex(buf, (unsigned int)n, out);
    printf("%u\\n", cnt);
    for (unsigned int i = 0; i < cnt; i++) {
        unsigned char *p = out + 20 * i;
        unsigned int kind = *(unsigned int *)(p + 0), st = *(unsigned int *)(p + 4),
                     ln = *(unsigned int *)(p + 8), line = *(unsigned int *)(p + 12),
                     col = *(unsigned int *)(p + 16);
        printf("%u %u %u %u %u\\n", kind, st, ln, line, col);
    }
    return 0;
}
"""


def _build(td: str) -> Path:
    hit = _BUILT.get("lexer")
    if hit is not None and hit.exists():
        return hit
    mod = lomentc.load(LEXER)
    deps = lomentc.resolve_deps(mod, ROOT, LEXER.parent, entry=LEXER)
    assert not lomentc.check(mod, deps=deps)
    ll = _CACHE_DIR / "lexer.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = _CACHE_DIR / "drv.c"
    c.write_text(DRIVER, encoding="utf-8")
    exe = _CACHE_DIR / "lexer.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["lexer"] = exe
    return exe


def _loment_tokens(exe: Path, src: Path) -> list[tuple[int, int, int, int, int]]:
    out = subprocess.run([shutil.which(str(exe)) or str(exe), str(src)],
                         capture_output=True, text=True, shell=False)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    n = int(lines[0])
    assert len(lines) - 1 == n, (len(lines) - 1, n)
    return [tuple(int(x) for x in ln.split()) for ln in lines[1:]]


def _python_tokens(src: Path) -> list[tuple[int, int, int, int, int]]:
    # 按原始字节解码 (不经换行归一化), 否则 CRLF 检出会让字节偏移整体偏移
    text = src.read_bytes().decode("utf-8")
    out = []
    for t in lomc.lex(text):
        # **跨度直接取 `t.off`/`t.len`，不要从 (line, col) 反推**：源里若有
        # **跨行的字符串字面量**，那个换行按源码算确实是"行首"，按词法却不是 ——
        # 两边会差出一个换行的字节数，而且是**越往后差得越多**。
        # 这个形状语料里一个都没有，只有自举 driver 里那句被 heredoc 腐蚀的
        # `fail("…\n")` 误打误撞撞出来过（见 `docs/185` §9.3）。
        # 词法器的 `off`/`len` 本来就是**字符**下标/长度，Loment 那侧报的是字节，这里换算。
        bstart = len(text[:t.off].encode("utf-8"))
        blen = len(text[t.off:t.off + t.len].encode("utf-8"))
        if t.kind == "eof":
            out.append((4, bstart, 0, t.line, t.col))
        elif t.kind == "raw":
            # 正文就是源里的原始字节（`docs/185` §4.1），`val` 与跨度是同一份
            out.append((5, bstart, blen, t.line, t.col))
        else:
            out.append((KIND[t.kind], bstart, blen, t.line, t.col))
    return out


@test
def test_m79_loment_lexer_matches_python():
    if not _clang():
        print("      SKIP: 无 clang")
        return
    files = [ROOT / "loment" / "examples" / "toolchain.lomt",
             ROOT / "loment" / "examples" / "all_loment.lomt",
             ROOT / "loment" / "examples" / "native_cap.lomt",
             ROOT / "loment" / "selfhost" / "lexer.lomt",
             # **外部代码块**（`docs/185` S1）：不加进来，raw 模式两边不一致也看不出来
             # —— 这正是本仓反复撞到的那个形状（判据只跑它跑的那些）。
             ROOT / "loment" / "extblock" / "evil.lomt",
             # **字符串里带裸换行**（`docs/185` §9.3）：上面那条"形状不存在就测不到"
             # 同一个毛病 —— 语料里一份这样的源都没有，是 driver 里一句被 heredoc
             # 腐蚀的 `fail("…\n")` 误打误撞撞出来的。这份源把那个形状钉成常驻。
             # 放在 `loment/lex/` 而不是 `examples/`：**它是夹具不是 API** ——
             # `examples/` 按定义全部进手册（`loment_manual.py` 直接 glob 它），
             # 一份词法语料进去会平白长出一页"公开 API"。
             ROOT / "loment" / "lex" / "multiline_str.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build(td)
        for f in files:
            got = _loment_tokens(exe, f)
            want = _python_tokens(f)
            assert len(got) == len(want), f"{f.name}: {len(got)} != {len(want)}"
            for i, (g, w) in enumerate(zip(got, want)):
                assert g == w, f"{f.name} token#{i}: Loment {g} != Python {w}"


PARSER = ROOT / "loment" / "selfhost" / "parser.lomt"
PARSER_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int parse(char *src, unsigned char *toks, char *out);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    unsigned char *toks = malloc(20 * ((size_t)n + 8));
    char *out = malloc((size_t)n * 4 + 1024);
    lex(buf, (unsigned int)n, toks);
    unsigned int m = parse(buf, toks, out);
    out[m] = 0;
    printf("%.*s\\n", (int)m, out);
    return 0;
}
"""


def _build_parser(td: str) -> Path:
    hit = _BUILT.get("parser")
    if hit is not None and hit.exists():
        return hit
    mod = lomentc.load(PARSER)
    deps = lomentc.resolve_deps(mod, ROOT, PARSER.parent, entry=PARSER)
    assert not lomentc.check(mod, deps=deps)
    ll = _CACHE_DIR / "parser.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = _CACHE_DIR / "pdrv.c"
    c.write_text(PARSER_DRIVER, encoding="utf-8")
    exe = _CACHE_DIR / "parser.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["parser"] = exe
    return exe


class Unsupported(Exception):
    pass


def _ex(e) -> str:
    """表达式 -> 规范 dump (与 Loment 版 parser 的约定一致)。"""
    n = type(e).__name__
    if n == "IntLit":
        # 数值, 不是原文: `0x1000` 出 `(int 4096)` (Loment 版的 put_num 同口径)
        return f"(int {e.value})"
    if n == "BoolLit":
        return f"(bool {'true' if e.value else 'false'})"
    if n == "StrLit":
        # 与 tools/lomfmt.py 的 _render 同一口径: 解码后的内容按 (反斜杠 -> 引号 ->
        # 换行) 顺序重转义。Loment 版词法给的是原文(含引号), parser 侧负责同样的解码。
        esc = e.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'(str "{esc}")'
    if n == "Ident":
        return f"(id {e.name})"
    if n == "ArrayLit":
        return "(arr" + "".join(" " + _ex(x) for x in e.items) + ")"
    if n == "StructLit":
        return (f"(struct {e.name}"
                + "".join(f" (f {fn} {_ex(fv)})" for fn, fv in e.inits) + ")")
    if n == "EnumCtor":
        return f"(enum {e.enum} {e.variant} {_ex(e.arg)})"
    if n == "EnumPath":
        return f"(enumpath {e.enum} {e.variant})"
    if n == "MethodCall":
        # 后缀风格 (与 (idx …)/(cast …)/(field …) 一致): 接收者先输出, 再跟 (mcall 名 实参…)
        return (f"{_ex(e.obj)}(mcall {e.name}"
                + "".join(" " + _ex(x) for x in e.args) + ")")
    if n == "Call":
        return "(call " + e.name + "".join(" " + _ex(a) for a in e.args) + ")"
    if n == "Bin":
        # 与 Loment 版一致: 左操作数已在前面输出, 这里只包住"运算符 + 右操作数"
        return f"{_ex(e.left)}(bin {e.op} {_ex(e.right)})"
    if n == "Un":
        return f"(un {e.op} {_ex(e.expr)})"
    if n == "Cast":
        return f"{_ex(e.expr)}(cast {e.type})"
    if n == "FieldAccess":
        return f"{_ex(e.obj)}(field {e.name})"
    if n == "Index":
        return f"{_ex(e.obj)}(idx {_ex(e.idx)})"
    if n == "Try":
        return f"{_ex(e.expr)}(try)"
    raise Unsupported(n)


def _arm_ex(pat, body) -> str:
    """match 臂: pat 为 None 表示通配 `_`; 否则是 EnumPath(可能带绑定名)。"""
    head = "_" if pat is None else f"(enumpath {pat.enum} {pat.variant}"
    if pat is not None and getattr(pat, "bind", None):
        head += f" bind {pat.bind}"
    if pat is not None:
        head += ")"
    return f" (arm {head} (" + "".join(" " + _st(x) for x in body) + "))"


def _st(s) -> str:
    """语句 -> 规范 dump。"""
    n = type(s).__name__
    if n == "Let":
        base = f"(let {s.name} {s.type}"
        if s.expr is not None:
            base += " " + _ex(s.expr)
        return base + ")"
    if n == "Return":
        return f"(ret {_ex(s.expr)})"
    if n == "ExprStmt":
        return _ex(s.expr)
    if n == "Assign":
        # 目标可以是 Ident 或 Index (`xs[0] = 1`); 两者 _ex 都能表达
        return f"{_ex(s.target)} (set {_ex(s.expr)})"
    if n == "If":
        then = "".join(" " + _st(x) for x in s.then)
        if s.otherwise and type(s.otherwise[0]).__name__ == "If" and len(s.otherwise) == 1:
            els = "(" + _st(s.otherwise[0]) + ")"
        else:
            els = "(" + "".join(" " + _st(x) for x in s.otherwise) + ")"
        return f"(if {_ex(s.cond)} ({then}) {els})"
    if n == "While":
        body = "".join(" " + _st(x) for x in s.body)
        return f"(while {_ex(s.cond)} ({body}))"
    if n == "For":
        body = "".join(" " + _st(x) for x in s.body)
        return f"(for {s.var} {_ex(s.lo)} {_ex(s.hi)} ({body}))"
    if n == "Guard":
        return f"(guard {s.cap} {_ex(s.expr)})"
    if n == "Match":
        # `if let` 也走这里 (参考实现把它反糖成两臂 Match: 命中臂 + 通配臂)
        return (f"(match {_ex(s.subject)}"
                + "".join(_arm_ex(p, b) for p, b in s.arms) + ")")
    raise Unsupported(n)


def _fn_mod(mod) -> str:
    fns = []
    for f in mod.funcs:
        s = f"(fn {f.name}"
        for p in f.params:
            s += f" (p {p.name} {p.type})"
        if f.ret != "()":
            s += f" -> {f.ret}"
        if f.body:
            s += " (" + "".join(" " + _st(x) for x in f.body) + ")"
        fns.append(s + ")")
    return f"(module {mod.name}" + "".join(" " + x for x in fns) + ")"


def _scan(obj, bad: tuple[str, ...]) -> None:
    import dataclasses
    if isinstance(obj, list):
        for x in obj:
            _scan(x, bad)
    elif dataclasses.is_dataclass(obj):
        if type(obj).__name__ in bad:
            raise Unsupported(type(obj).__name__)
        for f in dataclasses.fields(obj):
            _scan(getattr(obj, f.name), bad)


def _py_dump(src: Path) -> str:
    # 整数字面量按**数值**出 (`0x1000` -> `(int 4096)`): IntLit.value 本来就是数值,
    # Loment 版 parser 的 put_num 做同样的规范化 —— 两侧都不照抄原文写法。
    return _fn_mod(lomentc.load(src))


#: M80 已知缺口 (棘轮: **只许变短**, 现在是空的 —— 全语料逐字符一致)。
#: 机制保留: 将来出现新的缺口时先在这里登记 (带一句原因), 修好后删掉; 门禁会拒绝
#: "清单里已经一致"的条目, 所以这份清单不可能过期变松。
PARSE_KNOWN_GAPS: dict[str, str] = {}

#: dump 助手还没口径的结点 (第二道棘轮, 现在也是空的: 助手覆盖全部语料)。
#: 加新语料时如果助手缺结点, 这里会先红, 逼着先写 dump 口径 (再补 parser 分支)。
PARSE_HELPER_GAPS: dict[str, str] = {}

#: 可对照语料的**下限** (棘轮: 只许往上调)。
#: 42 -> 43: lomelf.lomt 补上十六进制口径 (2026-09-15)
#: 43 -> 44: native_match_full.lomt 补上"全覆盖 match 不写 `_`"的码形 (同日, 见该文件的注释)
#: 44 -> 45: tour.lomt —— 一个文件过完整门语言的示例 (同时是 agent 指南里那份)
CORPUS_FLOOR = 45

#: 整数字面量口径探针: 语料的**另一处**字面量位置 (类型里的数组长度) 至今没有十六进制样本,
#: 不能指望语料自己盯住它 —— 两处都走 put_num, 这里钉一个最小样本。
HEX_PROBE = """module hexprobe

fn f(a: u32) -> u32 {
    return a + 0x10;
}

fn g(x: [u8; 0x10]) -> u32 {
    return 0xFF;
}
"""


def _parser_corpus() -> tuple[list[Path], list[str]]:
    """可对照的语料 + 因**dump 助手**缺结点而跳过的清单 (后者是 M80 的下一步工作单)。

    助手覆盖 module/fn/let/set/ret/if/while/for/guard/match + int/bool/str/id/call/
    mcall/bin/un/cast/field/idx/try/arr/struct/enum 这批结点。遇到没口径的结点
    (`_ex`/`_st` 抛 Unsupported) 就把整个文件跳过 —— 所以**新语料用到新结点时,
    两道棘轮先红**, 逼着先补 dump 口径 (再补 parser 分支), 而不是悄悄少对照一个文件。
    """
    files = sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                   + list((ROOT / "loment" / "selfhost").glob("*.lomt"))
                   + list((ROOT / "loment" / "tools").glob("*.lomt")))
    ok, skip = [], []
    for f in files:
        try:
            _py_dump(f)
            ok.append(f)
        except Unsupported as e:
            skip.append(f"{f.name}:{e}")
        except Exception as e:  # noqa: BLE001
            skip.append(f"{f.name}:{type(e).__name__}")
    return ok, skip


@test
def test_m80_loment_parser_ast_dump():
    """M80: 全部**可对照**语料的 AST dump 与 Python 版逐字符一致。

    判据从"5 个候选文件"扩到"助手足迹能覆盖的整个语料", 并要求覆盖面不许回退
    (`len(files) >= CORPUS_FLOOR`)。跳过的清单逐条打印 —— 它就是 M80 下一步的工作单:
    每补齐一个 dump 结点口径, 这个分母就变大 (先补助手, 再补 Loment 版 parser)。

    这条曾经抓到一个真 bug: `else if` 的 else 分支在 Python 侧是 `"(" + st + ")"`
    (无前导空格), 而 Loment 版统一写成 `" ("` —— 修好后 ir_stmt.lomt 才逐字符一致。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    files, skip = _parser_corpus()
    assert len(files) >= CORPUS_FLOOR, (
        f"可对照语料只剩 {len(files)} 个 (低于 {CORPUS_FLOOR} 是覆盖面回退)")
    helper_gap = {s.split(":", 1)[0] for s in skip}
    assert helper_gap == set(PARSE_HELPER_GAPS), (
        f"助手侧缺口变了: 现在 {sorted(helper_gap)} (登记 {sorted(PARSE_HELPER_GAPS)}) —— "
        f"少了的要删清单, 多了的先给 dump 助手写口径")
    with tempfile.TemporaryDirectory() as td:
        exe = _build_parser(td)
        probe = Path(td) / "hexprobe.lomt"
        probe.write_text(HEX_PROBE, encoding="utf-8")
        pg = subprocess.run([shutil.which(str(exe)) or str(exe), str(probe)],
                            capture_output=True, text=True, shell=False).stdout.strip()
        pw = _py_dump(probe)
        assert pg == pw, f"整数字面量口径漂移 (探针):\n  Loment {pg}\n  Python {pw}"
        bad, fixed = [], []
        for f in files:
            got = subprocess.run([shutil.which(str(exe)) or str(exe), str(f)],
                                 capture_output=True, text=True, shell=False).stdout.strip()
            want = _py_dump(f)
            if got == want:
                if f.name in PARSE_KNOWN_GAPS:
                    fixed.append(f.name)
                continue
            if f.name not in PARSE_KNOWN_GAPS:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                bad.append(f"{f.name} @{k}: Loment {got[max(0,k-40):k+30]!r} "
                           f"!= Python {want[max(0,k-40):k+30]!r}")
        assert not fixed, (f"这些文件已经一致, 请从 PARSE_KNOWN_GAPS 删掉: {fixed}")
        assert not bad, "\n".join(bad[:4])
        okn = len(files) - len(PARSE_KNOWN_GAPS)
        print(f"      {okn}/{len(files)} 语料逐字符一致; parser 侧缺口 {len(PARSE_KNOWN_GAPS)} 个, "
              f"助手侧缺口 {len(PARSE_HELPER_GAPS)} 个 (两道棘轮都只许变短)")


CHECKER = ROOT / "loment" / "selfhost" / "checker.lomt"
NEG = ROOT / "loment" / "selfhost" / "neg"
CHECKER_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int chk_arena_bytes(void);
extern unsigned int chk_arena_scr(void), chk_arena_ctab(void), chk_arena_nc(void);
extern unsigned int chk_arena_exs(void), chk_arena_env(void), chk_arena_tyt(void);
/* 开关 (docs/182 §1): 与驱动器走同一条流水线 —— lex -> apply_switches -> check_arena
   -> switch_rules。**少了中间两步, 这套对照就测不到开关**, 而开关恰恰是这一轮
   从"承诺"变"发明"的地方。 */
extern unsigned int apply_switches(char *src, unsigned char *toks, unsigned int nt,
                                   unsigned char *sw);
extern unsigned int switch_rules(char *src, unsigned char *toks, unsigned char *sw,
                                 unsigned char *errs, unsigned int o);
extern unsigned int check_arena(char *src, unsigned char *toks, unsigned char *errs,
                                unsigned char *syms, unsigned char *scr, unsigned char *ctab,
                                unsigned char *nc, unsigned char *exs, unsigned char *env,
                                unsigned char *tyt);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 8);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    buf[n] = 0;
    unsigned char *toks = malloc(20 * ((size_t)n + 16));
    unsigned char *errs = malloc(1024);
    /* arena 按单元规模定 (~100 KB), 由调用方自己开并按 chk_arena_*() 切片 ——
       与驱动器同一个口径 (不能在调用方进程里动 brk: 与 CRT 的 malloc 踩) */
    unsigned char *a = malloc(chk_arena_bytes());
    unsigned char *sw = malloc(8008);
    unsigned int nt = lex(buf, (unsigned int)n, toks);
    nt = apply_switches(buf, toks, nt, sw);
    unsigned int m = check_arena(buf, toks, errs, a,
                                 a + chk_arena_scr(), a + chk_arena_ctab(), a + chk_arena_nc(),
                                 a + chk_arena_exs(), a + chk_arena_env(), a + chk_arena_tyt());
    m += switch_rules(buf, toks, sw, errs, m * 8);
    printf("%u", m);
    for (unsigned int i = 0; i < m; i++) {
        unsigned int code = *(unsigned int *)(errs + 8 * i);
        unsigned int tk = *(unsigned int *)(errs + 8 * i + 4);
        unsigned int st = *(unsigned int *)(toks + 20 * tk + 4);
        unsigned int ln = *(unsigned int *)(toks + 20 * tk + 8);
        printf(" %u@%u:%.*s", code, tk, (int)(ln > 24 ? 24 : ln), buf + st);
    }
    printf("\\n");
    return 0;
}
"""

# 错误码口径: **单一真源**在 loment_diag.RULES (E001–E013); checker.lomt 的 E_* 常量
# 用的是同一张表的数字部分, 所以这里直接借 loment_diag.classify 分类参考实现的消息,
# 两边不会各自维护一份模式表而悄悄漂移。
import loment_diag  # noqa: E402

E_DUP, E_TYPE, E_FN, E_ARITY = 13, 2, 2, 3

# 还没搬到自举 checker 的规则: 文件名 -> 缺口说明。这些负例只要求 `⊆` (自举版可以少报),
# 其余负例要求码集**完全相等**。每在 checker.lomt 里补一条, 就删掉这里对应的一行 ——
# 这张表的价值就是"允许少报"的范围**有界、可数、只减不增**。
# 现在整张表的规模由 `tools/loment_rule_parity.py` 测出: 32/60 规则等价 (批次 1 = 声明级
# 规则, 批次 2 第一批 = let/return/赋值/if/while/for 的类型比对)。
# 曾经登记过 `unknown_let.lomt` (let 初始化的类型比对) —— 批次 2 的 return 比对落地后
# 两边码集相等, 于是这一行按表的约定删掉了。
RULE_GAPS: dict[str, str] = {}


def _classify_codes(errs: list[str]) -> list[int]:
    """把参考实现的错误消息按 loment_diag 的口径归类成数字码集。"""
    out: list[int] = []
    for e in errs:
        code, _title, _hint = loment_diag.classify(e)
        if code != "E999":
            out.append(int(code[1:]))
    return sorted(set(out))


def _py_codes(src: Path) -> list[int]:
    """Python 侧把错误消息归类成同一套错误码 (与 checker.lomt 对照)。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    return _classify_codes(lomentc.check(mod, deps=deps))


@test
def test_m81_cross_module_dup_is_rejected():
    """M81/单元级唯一性: **跨模块**同名顶层符号必须被两边一致地拒。

    单元的发射符号是**平的** —— 内核线按名字找入口 (`_start` / `timer_isr`, docs/155 §3),
    所以私有符号不能靠 mangling 变成模块限定名; 代价是"一个单元里顶层名字必须唯一":
    否则后端发出两条 `define @helper` (非法 IR), 调用点还会解析到同一个函数 (静默错编)。
    以前参考实现只查"入口 vs 依赖的 pub", 依赖之间的**私有**重名一路静默; 自举 checker
    因为不分模块反而早就报了 —— 这条钉住两边一致 (口径 E-DUP)。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    entry = ROOT / "loment" / "selfhost" / "neg_across" / "entry.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    py = _classify_codes(lomentc.check(mod, deps=deps))
    assert py == [E_DUP], f"参考实现没按 E-DUP (E013) 报跨模块重名: {py}"
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        unit = Path(td) / "u.lomt"
        unit.write_text(_unit_text(entry), encoding="utf-8", newline="\n")
        got, det = _loment_codes(exe, unit)
        assert sorted(set(got)) == [E_DUP], f"自举 checker 的码不对: {got} {det}"
        print(f"      跨模块重名: 两边都报 E-DUP (参考消息 + 自举码 {sorted(set(got))})")


@test
def test_m81_falling_off_the_end_is_refused():
    '''M81/控制流: **非 `()` 的函数不许从末尾掉出去**（`#22`, 码 E027）。

    "`check` 通过 = 这份程序不会 trap" 是这条命令给人的唯一保证, 而"掉出末尾"发射之后
    是一条**非法指令**（SIGILL）—— 只在**真走到那一支**时才发生, 所以多半是"上线很久、
    错误分支被新输入踩到"才炸。SKILL.md §6.14 原先把它写成一条**写作纪律**（"check 放行,
    跑到就是 Illegal instruction"）：一条静态检查的通过不意味着"不会 trap", 那正是这里的洞。

    规则是**结构**的(不做数据流): 末尾是 `return`、是带 `else` 的 `if` 且两支都返回、是
    **每条臂都返回**的 `match`。**四种合法收尾在下面当正例钉着** —— 只钉"该拒的拒了"
    很容易把规矩收严到误报合法程序。跑遍本仓语料时这条规则一条都不误报。

    自举 checker 还没这条(`loment_rule_parity` 里那两条探针登记成 MISSING), 所以这一条
    只钉参考实现 —— 与 `test_m81_two_units_with_the_same_module_name` 同一个口径。
    '''
    bad = {
        "if-no-else": "module m\n\nfn pick(x: u32) -> u32 {\n    if x > 0 {\n        return 1;\n    }\n}\n",
        "match-arm-falls": "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn pick(e: E) -> u32 {\n    match e {\n        E::A => {\n            return 1;\n        }\n        _ => {\n            let v: u32 = 2;\n        }\n    }\n}\n",
        "ends-with-expr": "module m\n\nfn pick(x: u32) -> u32 {\n    let v: u32 = x;\n    v + 1;\n}\n",
    }
    good = {
        "tail-return": "module m\n\nfn pick(x: u32) -> u32 {\n    if x > 0 {\n        return 1;\n    }\n    return 0;\n}\n",
        "if-else-both": "module m\n\nfn pick(x: u32) -> u32 {\n    if x > 0 {\n        return 1;\n    } else {\n        return 2;\n    }\n}\n",
        "match-all-arms": "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn pick(e: E) -> u32 {\n    match e {\n        E::A => {\n            return 1;\n        }\n        _ => {\n            return 2;\n        }\n    }\n}\n",
        "void-falls-fine": "module m\n\nfn log(x: u32) {\n    let v: u32 = x;\n}\n",
    }
    with tempfile.TemporaryDirectory() as tds:
        d = Path(tds)
        for tag, src in bad.items():
            f = d / (tag + ".lomt")
            f.write_text(src, encoding="utf-8", newline="\n")
            errs = lomentc.check(lomentc.load(f))
            codes = {loment_diag.classify(e)[0] for e in errs}
            assert "E027" in codes, f"{tag}: 掉出末尾没按 E027 拒: {errs}"
        for tag, src in good.items():
            f = d / (tag + ".lomt")
            f.write_text(src, encoding="utf-8", newline="\n")
            errs = lomentc.check(lomentc.load(f))
            assert not errs, f"{tag}: 合法收尾被误拒: {errs}"
        print(f"      掉出末尾: {len(bad)} 种非法形状都拒(E027), {len(good)} 种合法收尾都放行")


@test
def test_m81_two_units_with_the_same_module_name():
    """M81/单元级唯一性: **两份文件都写 `module same`** 必须先被拒 (`#139`)。

    这把闸原先按**模块名**去重 —— 两份都叫 `same` 的文件在它眼里是**同一个模块**,
    于是闸不响: `check` 退 0、`build` 报 "0 失败"、产物里两条 `define i32 @f`
    (**非法 IR**), 一路拖到链接器才炸成内部异常 (`ELF-ERR: ElfError: 标签重复: f`,
    而 `tools/loment.py` 只接 `lomc.LomError`, 所以那是一段栈)。

    现在**单元名**这一条立在符号那条**上面**, 而且消息点出**两个文件** —— 只报名字
    的话, 用户分不清"改哪一个"。

    自举 checker 这边对不上: 它**不解析 `use`**(见 `test_m81_loment_checker_matches_python`
    那条边界), 根本看不见第二个单元 —— 所以这一条只钉参考实现。移植到 checker.lomt
    时会落到 `RULE_GAPS` 那条纪律上。
    """
    with tempfile.TemporaryDirectory() as tds:
        d = Path(tds)
        (d / "a.lomt").write_text(
            "module same\npub fn f() -> i32 {\n    return 11;\n}\n",
            encoding="utf-8", newline="\n")
        (d / "b.lomt").write_text(
            "module same\npub fn f() -> i32 {\n    return 22;\n}\n",
            encoding="utf-8", newline="\n")
        ent = d / "host.lomt"
        ent.write_text(
            'module host\nuse "a.lomt"\nuse "b.lomt"\n'
            "fn _start() {\n    syscall4(60, f() as u64, 0, 0);\n}\n",
            encoding="utf-8", newline="\n")
        mod = lomentc.load(ent)
        deps = lomentc.resolve_deps(mod, ROOT, ent.parent, entry=ent)
        errs = lomentc.check(mod, deps=deps)
        assert errs, "两份同名单元没被拒 —— 产物会是两条 define @f (非法 IR)"
        assert any("单元名" in e and "a.lomt" in e and "b.lomt" in e for e in errs), \
            f"没报出'单元名被两份文件同时声明', 也没点出是哪两个文件: {errs}"
        assert len(errs) == 1, f"根因一条就够, 不该倒出一串症状: {errs}"
        print(f"      同名单元: 拒, 且消息点出两个文件（{errs[0][:48]}…）")


@test
def test_m81_dep_body_is_checked():
    """M81: **依赖的正文也要查** (`#149`, `docs/198` §4)。

    `check` 原先只把 `deps` 的**导出符号**入表, 正文一个字不验 —— 于是
    `pub fn f(p: ptr) -> u32 { return p; }` 这样的库**当依赖时一路绿**, 只有当**入口**
    查才报。后果: 一个库可以带着正文类型错发布, 而每一个使用它的程序 check 都绿;
    抓得住它的只有"把库文件自己当入口"那种形状 —— 那是**给库写判据的人**的义务,
    没有一个消费者能强制。

    现在把每个依赖**当成入口**再查一遍(它的 `use` 图就是整份闭包), 消息前面点出
    **哪个文件** —— 不然用户不知道去改哪一个。自举 checker 不解析 `use`, 所以这一条
    同样只钉参考实现。
    """
    with tempfile.TemporaryDirectory() as tds:
        d = Path(tds)
        (d / "deps" / "badlib").mkdir(parents=True)
        (d / "deps" / "badlib" / "badlib.lomt").write_text(
            "module badlib\npub fn f(p: ptr) -> u32 {\n    return p;\n}\n",
            encoding="utf-8", newline="\n")
        ent = d / "main.lomt"
        ent.write_text(
            # `choose runtime`：这个入口用了 `alloc`（`docs/227` 之后默认档是一句保证）。
            # 少了它，E022 会**抢在**"库正文那一处"前面报 —— 而这条判据要看的正是后者。
            "module main\nchoose runtime\nuse badlib\nfn _start() {\n"
            "    let x: u32 = f(alloc(8));\n    syscall4(60, x as u64, 0, 0);\n}\n",
            encoding="utf-8", newline="\n")
        mod = lomentc.load(ent)
        deps = lomentc.resolve_deps(mod, ROOT, ent.parent, entry=ent)
        errs = lomentc.check(mod, deps=deps)
        assert errs, "库的正文类型错在**当依赖**时没被报出来"
        assert any("badlib" in e and "return 类型 ptr" in e for e in errs), \
            f"报了别的、没报库正文那一处: {errs}"
        # 库文件**自己当入口**时也报 —— 两边是同一句话 (否则又是两个答案)
        own = lomentc.load(d / "deps" / "badlib" / "badlib.lomt")
        own_deps = lomentc.resolve_deps(own, ROOT, d / "deps" / "badlib", entry=None)
        direct = lomentc.check(own, deps=own_deps)
        assert any("return 类型 ptr" in e for e in direct), direct
        print("      依赖正文: 当依赖 / 当入口 都报得出同一处")


def _build_checker(td: str) -> Path:
    hit = _BUILT.get("checker")
    if hit is not None and hit.exists():
        return hit
    mod = lomentc.load(CHECKER)
    deps = lomentc.resolve_deps(mod, ROOT, CHECKER.parent, entry=CHECKER)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    ll = _CACHE_DIR / "checker.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = _CACHE_DIR / "cdrv.c"
    c.write_text(CHECKER_DRIVER, encoding="utf-8")
    exe = _CACHE_DIR / "checker.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["checker"] = exe
    return exe


def _loment_codes(exe: Path, src: Path) -> list[int]:
    """返回 (错误码列表, 明细字符串) —— 明细用于失败时定位。"""
    out = subprocess.run([shutil.which(str(exe)) or str(exe), str(src)],
                         capture_output=True, text=True, shell=False)
    assert out.returncode == 0, out.stderr
    parts = out.stdout.split()
    n = int(parts[0])
    detail = parts[1:1 + n]
    codes = sorted({int(x.split("@")[0]) for x in detail})
    return codes, " ".join(detail)


@test
def test_m81_loment_checker_matches_python():
    """M81: Loment 版检查器与 Python 版的判定一致 (负例拒绝 + 正例接受, **码集相等**)。

    码值取自项目的统一口径 `loment_diag.RULES` (E001–E013)。以前只断言 `⊆` ——
    那允许自举版"少报"(更宽松就等于放过真正该拒的程序): 实测 `let x: u32 = true;`
    参考实现拒、自举版放行。现在要求**相等** —— "自举 checker 与参考等价"是
    "脱离 Python"的第一道门 (谁在当规范的执行者)。还没补到位的规则在 `RULE_GAPS`
    里如实登记, 每补一条删一行。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    neg = sorted(NEG.glob("*.lomt"))
    assert len(neg) >= 6, len(neg)
    # 正例只取"单编译单元"文件: Loment 版检查器不解析 use 导入 (见 docs/150 边界)
    pos = [ROOT / "loment" / "selfhost" / "pos" / "ok.lomt",
           ROOT / "loment" / "examples" / "mathutil.lomt",
           ROOT / "loment" / "examples" / "bytes.lomt",
           ROOT / "loment" / "examples" / "native.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        exact = 0
        for f in neg:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want, f"{f.name}: Python 未报错"
            assert got, f"{f.name}: Loment 未报错"
            if f.name in RULE_GAPS:
                assert set(got) <= set(want), f"{f.name}: Loment {got} ⊄ Python {want} [{det}]"
                continue
            assert sorted(set(got)) == want, \
                f"{f.name}: 码集不等 Loment {sorted(set(got))} vs Python {want} [{det}]"
            exact += 1
        for f in pos:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want == [] and got == [], f"{f.name}: 正例被拒 (py={want} loment={got}) [{det}]"
        print(f"      负例码集: {exact}/{len(neg)} 完全相等" + (
            f"; 登记缺口 {len(RULE_GAPS)} 个: {sorted(RULE_GAPS)}" if RULE_GAPS else ""))


CODEGEN = ROOT / "loment" / "selfhost" / "codegen.lomt"
IR_TARGET = ROOT / "loment" / "selfhost" / "ir_const.lomt"
CODEGEN_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int cg_arena_bytes(void);
extern unsigned int emit_module(char *src, unsigned char *toks, char *out, unsigned char *st);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 8);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    buf[n] = 0;
    unsigned char *toks = malloc(20 * ((size_t)n + 16));
    /* 余量 65536 而不是 8192（`driver.lomt` 的 `IR_CAP` 注释记的是"上界 8*单元 + 8192"）：
       `gc_auto` 给**每一份**产物恒加约 5.2 KB 的收集器文本，小单元下 8192 就不够了 ——
       实测症状是**夹具自己段错误**（0xC0000005），看起来像孪生崩了，其实是我把缓冲写穿。
       真驱动那边是 `IR_CAP = 16 MB`，本来就够；这里给足余量，免得夹具比真东西还紧。 */
    char *out = malloc((size_t)n * 8 + 65536);
    /* 状态块按单元规模定 (~500 KB), 不能压在语言堆上 —— 与驱动同一个口径 */
    unsigned char *st = malloc(cg_arena_bytes());
    lex(buf, (unsigned int)n, toks);
    unsigned int m = emit_module(buf, toks, out, st);
    printf("%.*s", (int)m, out);
    return 0;
}
"""


def _build_codegen(td: str) -> Path:
    hit = _BUILT.get("codegen")
    if hit is not None and hit.exists():
        return hit
    mod = lomentc.load(CODEGEN)
    deps = lomentc.resolve_deps(mod, ROOT, CODEGEN.parent, entry=CODEGEN)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    ll = _CACHE_DIR / "codegen.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = _CACHE_DIR / "gdrv.c"
    c.write_text(CODEGEN_DRIVER, encoding="utf-8")
    exe = _CACHE_DIR / "codegen.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["codegen"] = exe
    return exe


@test
def test_m85_checker_accepts_corpus_units():
    """M85 后半: 自举 checker 在**拼接单元**上的覆盖面 (driver 视角, docs/150)。

    M81 的判据是"负例集判定一致", 那是在单文件上跑; 要当"闸门"还得能**放行合法程序**。
    这一条把覆盖面钉住: `loment/{examples,selfhost,tools}` 里每个 `.lomt` 的**拼接单元**
    (依赖 + 本文件 + 预置枚举, 与驱动器装载的同一份) 必须**一条诊断都没有**(已登记缺口除外)。

    缺口清单和覆盖面一起钉: 缺口被修好时这条会提醒更新清单 (而不是让它悄悄过期)。

    **`loment/tools/` 是后加的**(实测赚回来一条真缺陷): 本判据原先只看 `examples` +
    `selfhost`, 于是 `loment/tools/*.lomt` 没有判据把"单独当单元看"钉住 —— 而
    `switches.lomt` 正好证明这类错误**只在单看时暴露**: 它初版漏了 `use bytes`
    (`load32`/`store32` 在 `loment/examples/bytes.lomt` 里, 不是语言内建), 在
    `checker`/`lomdoc` 的单元里被上游的 `use bytes` 盖住, 一路绿到被本判据抓住。
    加目录前先量过: 12 份 `loment/tools/*.lomt` 在参考实现下当单元看**都是零诊断**,
    所以这不是"放宽", 是把同一把尺子量到底。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    gaps = {
        # 非目标: 参考实现的 IR 后端自己也发不出来 (native: inb 未实现), 它本就不是单元的
        # 合法形状 —— 所以这条不是"checker 的缺口", 而是"这份文件不进单元语料"。
        "native_raii.lomt",
    }
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        ok = []
        for target in sorted([t for d in ("examples", "selfhost", "tools")
                              for t in (ROOT / "loment" / d).glob("*.lomt")]):
            unit = Path(td) / f"u_{target.stem}.lomt"
            unit.write_text(_unit_text(target), encoding="utf-8", newline="\n")
            codes, det = _loment_codes(exe, unit)
            if target.name in gaps:
                assert codes, f"{target.name}: 缺口已消失 —— 请从 gaps 里删掉它"
                continue
            assert not codes, f"{target.name}: 单元上有假报 {codes} {det[:100]}"
            ok.append(target.name)
        print(f"      checker 放行单元: {len(ok)} 无诊断"
              f" (另有已登记缺口 {len(gaps)} 个: {', '.join(sorted(gaps))})")


@test
def test_m82_loment_codegen_byte_identical():
    """M82(子集): Loment 版 codegen 的 .ll 与 Python 版逐字节一致 (常量/参数返回 + 表达式)。"""
    if not _clang():
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        for target in (IR_TARGET, ROOT / "loment" / "selfhost" / "ir_expr.lomt",
                       ROOT / "loment" / "selfhost" / "ir_stmt.lomt",
                       ROOT / "loment" / "selfhost" / "ir_logic.lomt",
                       ROOT / "loment" / "selfhost" / "ir_cast.lomt",
                       ROOT / "loment" / "selfhost" / "ir_mem.lomt",
                       ROOT / "loment" / "selfhost" / "ir_for.lomt",
                       ROOT / "loment" / "selfhost" / "ir_div.lomt",
                       ROOT / "loment" / "selfhost" / "ir_builtin.lomt",
                       ROOT / "loment" / "selfhost" / "ir_index.lomt",
                       ROOT / "loment" / "selfhost" / "ir_call5.lomt"):
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"{target.name} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")


@test
def test_m82_hosted_arena_is_byte_identical():
    """`docs/228` §5 的 **A4**：`choose hosted` 那一份产物，两个实现**逐字节**一致。

    **这条不判"跑得起来"**（那是 `loment_hosted_test` 的 A3）—— 它只钉"两个实现发的是
    同一段文本"。分开的理由与 `test_m88` 同：IR 等价与"那个 IR 真的做了事"是两件事，
    合成一条会让"两边一起错"看起来是绿的。

    **源只有一份** —— 就是 `lomentc_test.HEAP_HOSTED_SRC`（A3 那条运行时判据用的也是它）。

    它也是 **A1 的另一半**：这一份**带 `choose hosted`**，而 `ir_mem.lomt` 那 9 份
    **不带** —— 两边各自逐字节一致，才说得上"`sealed` 没动、`hosted` 才动"。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test                                            # noqa: E402,PLC0415
    # 两份源：**分配器**那一份（`docs/228`）与 **分配器+收集器**那一份（`docs/230`）。
    # 后者是这一步真正的新东西 —— 收集器本体也是**换**出来的（`_arena_indirect`），
    # 两个实现必须发同一段，所以它必须各过一遍。
    cases = (("ir_hosted", lomentc_test.HEAP_HOSTED_SRC),
             ("ir_hosted_gc", lomentc_test.HEAP_HOSTED_GC_AUTO_SRC))
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        for name, src in cases:
            target = Path(td) / f"{name}.lomt"
            target.write_text(src, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"{name} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")
            # 两边一致还不够 —— 一致地**没做**那件事也是"一致"。这一段把"做了什么"钉住，
            # 免得两个实现一起退回静态 arena 而这条判据照样绿（`docs/219` §7 那条口径）。
            assert f"@__loment_heap = {lomentc_test.HEAP_PTR_INIT}" in want, \
                "hosted 的 arena 不是宿主来的"
            assert "declare ptr @malloc(i64)" in want, "没向宿主声明分配器"
    # 收集器那一份还得**真的带着收集器**：表按 arena 换过（位图：1 MiB / 64）、且走基址指针
    assert "@__loment_mark = internal global [16384 x i8]" in want
    assert "%hb = call ptr @__loment_arena()" in want
    print("      hosted arena + hosted collector: 两个实现逐字节一致")


@test
def test_m88_l0_alpha_pair_is_byte_identical():
    """L0（`docs/210` §2）那一对程序，在**两个实现**上逐字节一致。

    **源只有一份** —— 就是 `lomentc_test.L0_PAIR_SRC`（那边同时钉参考侧的逐格提升表）。
    各抄一份必然漂，而这一条的全部意义就是"两个实现看的是同一份程序"。

    这一对里有**提升的与不该提升的**（`lomentc_test.L0_WANT` 逐格说明了理由），所以它盖住的
    不只是"提升真的发生了"，还有"不该提升的地方也没提升" —— 例如 `load16/32/64`、
    `store16/32/64` 是 `bytes.lomt` 的库函数（不是内建），拿它们碰指针的站点**不许**提升。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test  # noqa: E402  （源只有一份：L0 那一对是那边的常量）
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        outs: dict[str, str] = {}
        for tag, src in (("alpha", lomentc_test.L0_ALPHA_SRC),
                         ("manual", lomentc_test.L0_MANUAL_SRC)):
            target = Path(td) / f"gc_l0_{tag}.lomt"
            target.write_text(src, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            outs[tag] = got
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"gc_l0_{tag} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")
    # 不许空转：alpha 那一份必须**真的**有提升的缓冲，manual 那一份必须一个都没有
    assert ".buf" in outs["alpha"], "alpha 那一份里没有提升的缓冲 —— 这条判据在空转"
    assert ".buf" not in outs["manual"], "gc_manual 那一份不该有提升的缓冲"


@test
def test_m92_slot_table_is_byte_identical():
    """**块内槽表**（`docs/210` §2.9）那一份真值表源，两个实现**逐字节**一致。

    **源只有一份** —— `lomentc_test.SLOT_TABLE_SRC`（那边同时钉参考侧那四格：哪两个站点
    精确、掩码是哪几位、表那一行写没写进初值）。各抄一份必然漂，而这一条的全部意义就是
    "两个实现看的是同一份程序"。

    这一格比别的几格更依赖这条判据：`_slot_plan` 与 `slot_mask_at` 是两套**手写**的
    token 走法（别名闭包、用法校验、掩码置位），差一格就是"一个站点精确、另一个保守" ——
    产物里多一条 `store i32 -k` 少一条，逐字节当场看得见。**而真值表那边只钉参考实现**，
    钉不到镜像。所以这一条不许空转：产物里必须有那条槽表行与至少一条 `-k` 标记。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test  # noqa: E402  （源只有一份）
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        target = Path(td) / "gc_slots.lomt"
        target.write_text(lomentc_test.SLOT_TABLE_SRC, encoding="utf-8", newline="\n")
        want = _unit_ll(target)
        got = _run_codegen(exe, target, td)
        if got != want:
            i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
            a = max(0, (i or 0) - 80)
            raise AssertionError(
                f"gc_slots 首个差异 @{i}:\n"
                f" loment {got[a:(i or 0) + 100]!r}\n python {want[a:(i or 0) + 100]!r}")
        # 不许空转：两条**带掩码**的借出标记都得在（不然"两边都没发"也能通过）。
        # 掩码编在块头那个字里（`-(mask + 2)`），所以这里点的是 `-2`（掩码 0）与 `-4`（0b10）。
        assert got.count("store i32 -2, ptr") == 1 and got.count("store i32 -4, ptr") == 1


@test
def test_m89_l2_epoch_is_byte_identical():
    """L2（**块纪元**，`docs/210` §2）：那一份真值表源，在**两个实现**上逐字节一致。

    这一条是 L2 的镜像那一半 —— 规则定义在 token 流上，两个实现各写一份实现，
    所以"逐字节"不是形式：`l2_ok` 与 `_l2_ok` 的判断差一格，产物就整段不同。

    **源只有一份**（`lomentc_test.L2_EPOCH_SRC`，那边同时钉逐格的真值表）。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        outs: dict[str, str] = {}
        for tag, src in (("alpha", lomentc_test.L2_ALPHA_SRC),
                         ("manual", lomentc_test.L2_MANUAL_SRC),
                         # `if` 的两支那一格（`docs/210` §2.3）：孪生那边的支跨度是
                         # `skip_block` **现算**的 —— 差一格这一条就红。
                         ("ifalpha", lomentc_test.L2_IF_ALPHA_SRC),
                         ("ifmanual", lomentc_test.L2_IF_MANUAL_SRC)):
            target = Path(td) / f"gc_l2_{tag}.lomt"
            target.write_text(src, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            outs[tag] = got
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"gc_l2_{tag} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")
    # 不许空转：alpha 那一份必须**真的**多出前沿的存取（每开一格多一条读、一条写）
    assert outs["alpha"].count("@__loment_off") > outs["manual"].count("@__loment_off"), \
        "alpha 那一份没有多出前沿的存取 —— 这条判据在空转"


@test
def test_m90_gc_auto_collector_is_byte_identical():
    """`gc_auto` 的收集器（`docs/210` §2.1）：同一份源，**两个实现**逐字节一致。

    这一条是收集器那一半的镜像判据，也是最难"蒙对"的一条：
      * 收集器本体是**五百多行 IR**（四个遍历 + 根表 + 触发），自举侧那份是脚本从
        `lomentc._IR_GC` 生成的（不手抄），文本那一半靠生成保证；
      * 但**挂点是手写的**：根表登记每格吃掉三条 temp，`alloc` 站点那句要插在
        表达式求值之后 —— temp 编号错一位，整段产物全错。所以这条判据真正钉的是
        "挂点在**哪**、按**什么顺序**"。

    **源只有一份**（`lomentc_test.GC_AUTO_SRC`，那边同时钉静态痕迹）。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        outs: dict[str, str] = {}
        for tag, src in (("auto", lomentc_test.GC_AUTO_AUTO_SRC),
                         ("manual", lomentc_test.GC_AUTO_MANUAL_SRC)):
            target = Path(td) / f"gc_auto_{tag}.lomt"
            target.write_text(src, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            outs[tag] = got
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"gc_auto_{tag} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")
    # 不许空转：auto 那一份必须真的带收集器与挂点，manual 一份都不许沾
    assert "@__loment_collect" in outs["auto"], "auto 那一份里没有收集器 —— 这条判据在空转"
    assert "call void @__loment_maybe_collect" in outs["auto"], "auto 那一份里没有挂点"
    assert "@__loment_collect" not in outs["manual"], "manual 那一份不该有收集器"


@test
def test_m92_lms_entry_a_rescue_is_byte_identical():
    """LMS 第 3 级入口 A（`docs/253` §5.3）：`alloc` 分配不出时**先收一次再试** ——
    **两个实现逐字节一致**。

    ⚠ **这一条是为一个"孪生看不见的分歧"专门立的。**
    `loment_p8_test` / `loment_potato_emit_test` 的语料里**没有一个用 `lms_open`**，
    所以"参考侧发了 rescue、自举侧没发"在那两条判据下**照样绿** ——
    孪生只证"一致"，而这是**它没量到的那一段**（`feedback_twin_proves_agreement_not_correctness`）。
    **实测过一次**：自举侧第一轮只改了 `emit_heap`（sealed）、**漏了 `emit_heap_hosted`**，
    两边 `hosted` 那一支当场差 462 字节 —— 这正是这条判据要挡的形状。

    **三格**覆盖所有能成立的情形（`gc_manual` 不在表里 —— 它没有收集器，
    入口 A 在那档下**一个字都不该发**；那半由 `lomentc_test` 的 3×2 网格管）。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    # ⚠ 尺寸走 `const` 名，**不能是字面量** —— 否则 `gc_auto_alpha` 会把 `alloc`
    # 静态提升到栈上（L0），产物里**根本不需要堆**，这三格会**空转着全绿**。
    probe = ("module m\nchoose runtime\nconst N: u32 = 64;\n%s\n\n"
             "fn f() -> u32 {\n    let p: ptr = alloc(N);\n"
             "    return load8(p, 0);\n}\n")
    cases = (
        ("gc_auto", "choose gc_auto\nchoose lms_open"),
        ("gc_auto_alpha", "choose gc_auto_alpha\nchoose lms_open"),
        ("gc_auto_hosted", "choose hosted\nchoose gc_auto\nchoose lms_open"),
    )
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        outs: dict[str, str] = {}
        for tag, chooses in cases:
            target = Path(td) / f"lms_{tag}.lomt"
            target.write_text(probe % chooses, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            outs[tag] = got
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"lms_{tag} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")
    # 不许空转：三格都必须**真的**带那条救援路径，且**两条成功返回各清一次**标记
    for tag, out in outs.items():
        assert "@__loment_lms_retry" in out, f"{tag}: 没有入口 A —— 这条判据在空转"
        assert out.count("store i1 false, ptr @__loment_lms_retry") == 2, tag


@test
def test_m91_l1_definite_lifetime_is_byte_identical():
    """L1（**定活**，`docs/210` §2.5）：那一份真值表源，在**两个实现**上逐字节一致。

    这一条钉的是 L1 的镜像那一半。规则定义在 token 流上（理由：`ptr` 是 Copy，
    `docs/206` 的 E006 对它一句话也说不了 —— 详见 `lomentc_test::test_l1_definite_lifetime_rule`），
    两个实现各写一份实现，所以"逐字节"不是形式：
      * **落点**（"最后一次用处之后最早那条 `return` 之前 / 函数末尾"）两边各有一份推演，
        差一格产物就整段不同；
      * 插进去的那两条指令（`load ptr` + `call @__loment_free`）要**吃 temp 号** ——
        编号错一位，后面全错。

    **源只有一份**（`lomentc_test.L1_SRC`，那边同时钉逐格的真值表）。`gc_manual` 那一份
    一起过：它在 L1 上是**空的**，所以这一条也顺带钉住"默认档一个字节都不动"。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    import lomentc_test  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        outs: dict[str, str] = {}
        for tag, src in (("alpha", lomentc_test.L1_ALPHA_SRC),
                         ("manual", lomentc_test.L1_MANUAL_SRC)):
            target = Path(td) / f"gc_l1_{tag}.lomt"
            target.write_text(src, encoding="utf-8", newline="\n")
            want = _unit_ll(target)
            got = _run_codegen(exe, target, td)
            outs[tag] = got
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"gc_l1_{tag} 首个差异 @{i}: "
                    f"loment {got[a:(i or 0) + 80]!r} / python {want[a:(i or 0) + 80]!r}")
    # 不许空转：alpha 那一份必须**真的**多出 L1 的 free，manual 那一份一个都不许多
    assert outs["alpha"].count("@__loment_free") > outs["manual"].count("@__loment_free"), \
        "alpha 那一份没有多出 L1 的 free —— 这条判据在空转"


def _dep_paths(target: Path) -> list[Path]:
    """按 lomentc.resolve_deps 的规则取依赖文件 (被依赖者在前), 并与参考实现的模块名序列核对。

    Loment 版 codegen 只吃**单个编译单元**(不解析 `use`) —— 依赖装载由驱动/夹具负责,
    与 M80/M81 自举阶段的边界一致。这里把"参考实现解析出的模块序"当判据钉死:
    路径规则一旦与 lomentc 漂移, 名字序列就对不上, 测试会直接失败。

    **按文件缓存**（连同下面那条名字序断言）—— 判据里同一份单元的单元文本被反复要
    （`_run_codegen` 一个文件一次, 语料循环里还要再来）, 而它是纯函数。
    """
    key = target.resolve()
    hit = _DEP_PATHS.get(key)
    if hit is not None:
        return hit
    mod = lomentc.load(target)
    deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
    paths: list[Path] = []
    seen: set[Path] = set()

    def visit(m, cur_base: Path) -> None:
        # 名字形式先落到绝对路径; 规则在 lomentc.resolve_name, 这里只调用不重抄
        for imp in list(m.imports) + [str(lomentc.resolve_name(n, ROOT))
                                      for n in m.name_imports]:
            p = Path(imp)
            cand = p if p.is_absolute() else None
            if cand is None or not cand.exists():
                for base_try in (ROOT, cur_base):
                    q = base_try / imp
                    if q.exists():
                        cand = q
                        break
            if cand is None or not cand.exists():
                raise FileNotFoundError(imp)
            rp = cand.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            sub = lomentc.load(rp)
            visit(sub, rp.parent)
            paths.append(rp)

    visit(mod, target.parent)
    names = [lomentc.load(p).name for p in paths]
    assert names == [m.name for m in deps], f"依赖序与 lomentc 不一致: {names} vs {[m.name for m in deps]}"
    _DEP_PATHS[key] = paths
    return paths


#: 核心模式的全部取值（与 `potato.CORE_WORDS` 同一条；只在这个夹具里用来认那一行）。
#:
#: ⚠ **这是第三份手抄**（另两份：`potato.CORE_WORDS` 与自举侧 `switches.lomt` 的
#: `sw_is_core`）。加 `vm` 维（`docs/231`）时把这一份也补上了 —— 但**它已经漏了
#: `port` 的 `sealed`/`hosted`**（`docs/222` 那次没跟上），这一处**不在本次范围**，
#: 留着可见；真要收口就把三份变成一份推导的。
_CORE_CHOOSE = ("std", "no_std", "gc_manual", "gc_auto", "gc_auto_alpha",
                "runtime", "no_runtime", "vm", "no_vm")


def _blank_core_choose(text: str) -> str:
    """把**核心 `choose` 行抹成等长空白** —— 镜像装载器对依赖做的那一下（`docs/227` §4）。

    **为什么夹具要自己镜像它**：`load_file` 在把依赖拼进合并流之前，会把依赖写下的核心
    `choose` 抹掉（`blank_tok`）—— 不然检查器会当成"这一维写了两次"。这个夹具是**绕过
    装载器**直接拼的，所以它得自己抹；不抹的话，一份 `choose runtime` 的库 + 一份
    `choose runtime` 的根拼起来就是两次声明 —— 实测 `ahci.lomt` 当场报
    `假报 [22] 22@166:choose`。

    只抹**依赖**那一侧：根那一行是它的容器，留着（与装载器同一条）。
    """
    out: list[str] = []
    for line in text.split("\n"):
        head = line.strip().rstrip(";").split()
        if len(head) == 2 and head[0] == "choose" and head[1] in _CORE_CHOOSE:
            out.append(" " * len(line))
        else:
            out.append(line)
    return "\n".join(out)


def _unit_text(target: Path) -> str:
    """依赖按序拼接 + 本单元 (与 lomentc.emit_llvm 的 `mods = deps + [mod]` 同序)。

    还要镜像 lomentc.load 的**预置枚举注入**: `Option`/`Result` 缺失时由加载器补进
    `mod.enums`。原生后端按声明发射聚合类型 (`Result<u32,u32>` -> `{ i32, i64 }`),
    所以这份声明对被编译单元必须是可见的 —— 否则枚举查不到, 只能退化成 i64。
    """
    key = target.resolve()
    hit = _UNIT_TEXT.get(key)
    if hit is not None:
        return hit
    text = ("".join(_blank_core_choose(p.read_text(encoding="utf-8")) + "\n"
                    for p in _dep_paths(target))
            + target.read_text(encoding="utf-8"))
    # 判据要用**注入前**的模块 (lomentc.load 返回值里已经有它们了, 拿它判断永远为真)。
    # **开关也要先落定**（`docs/182` §1）—— 这里绕过了 `lomentc.load` 直接调 `Parser`,
    # 不补这一步的话 `set choose X { … }` 会撞上解析器的"未知顶层关键字"。
    # 实测撞到: 加 `switch.lomt` 之后 `test_m85_checker_accepts_corpus_units` 报
    # `LomError: 25:1: 未知顶层关键字 'set'`。
    raw = target.read_text(encoding="utf-8")
    have = {e.name for e in lomentc.Parser(
        lomentc._apply_switches(lomc.lex(raw), lomentc.SwitchTable()), raw).parse().enums}
    if "Option" not in have or "Result" not in have:
        text += "\n" + lomentc._PRELUDE
    _UNIT_TEXT[key] = text
    return text


def _run_codegen(exe: Path, target: Path, td: str) -> str:
    """在"装载好的单元"上跑 Loment 版 codegen (无依赖时就是原文件)。"""
    try:
        text = _unit_text(target)
    except Exception:  # noqa: BLE001
        text = target.read_text(encoding="utf-8")
    unit = Path(td) / f"unit_{target.stem}.lomt"
    # 必须写 LF: 驱动按原始字节读文件, 而 lomentc.load 用 read_text (通用换行) ——
    # CRLF 会让字符串字面量里多出 \0D (自举 codegen 就是这么抓到的)
    unit.write_text(text, encoding="utf-8", newline="\n")
    try:
        return subprocess.run([shutil.which(str(exe)) or str(exe), str(unit)],
                              capture_output=True, text=True, timeout=30, shell=False).stdout
    except subprocess.TimeoutExpired:
        return ""


def _build_from_ll(ll: Path, td: str, name: str) -> Path:
    """用给定的 .ll + 同一个 C 驱动链出可执行文件 —— 自举多阶段复用 (M83/M84)。

    **按 .ll 的字节缓存**: 链接是确定性的, 同一份 IR 给同一个二进制 (M84 的定点正好是
    "第 2 阶段喂进去的 IR 与第 1 阶段相同")。
    """
    key = _digest(ll.read_bytes())
    hit = _BUILT.get(f"from_ll:{key}")
    if hit is not None and hit.exists():
        return hit
    c = _CACHE_DIR / f"{name}_drv.c"
    c.write_text(CODEGEN_DRIVER, encoding="utf-8")
    exe = _CACHE_DIR / f"{name}_{key}.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT[f"from_ll:{key}"] = exe
    return exe


DRIVER_LOMT = ROOT / "loment" / "selfhost" / "driver.lomt"


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
    """Windows 路径 -> WSL 里的 /mnt/<drive>/..."""
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


#: `-O1` 的 loop-idiom 把逐字节拷贝的循环认成 `memmove` 调用, 而 `-nostdlib` 的链接里
#: 没有它 (报 `undefined symbol: memmove`, 且**只缺这一个**)。补一个最小的实现即可 ——
#: 写错了不会悄悄换个产物: 判据比的是产物与参考**逐字节相同**, 抄错一步当场红。
_MEM_STUB = """\
void *memmove(void *d, const void *s, unsigned long n) {
    unsigned char *dd = d; const unsigned char *ss = s;
    if (dd < ss) { for (unsigned long i = 0; i < n; i++) dd[i] = ss[i]; }
    else { for (unsigned long i = n; i > 0; i--) dd[i - 1] = ss[i - 1]; }
    return d;
}
"""


def _mem_stub() -> Path:
    """`-O1` 的 ELF 要的 `memmove` (见上); 编一次, 复用。"""
    hit = _BUILT.get("memstub")
    if hit is not None and hit.exists():
        return hit
    c = _CACHE_DIR / "memstub.c"
    c.write_text(_MEM_STUB, encoding="utf-8")
    o = _CACHE_DIR / "memstub.o"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "--target=x86_64-unknown-linux-gnu", "-ffreestanding", "-fno-builtin", "-O0",
         "-c", "-o", str(o), str(c)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["memstub"] = o
    return o


def _build_linux_elf(ll_text: str, td: str, name: str) -> Path:
    """IR -> x86_64 Linux ELF (无 libc, `_start` 即入口) —— 从 Windows 交叉编译。

    **按 IR 的字节缓存**: 链接确定性, 同一份 IR 给同一个 ELF。几处判据要的是**同一个**
    驱动二进制 (m83 的 elf1 与 elf2、语料判据、perf 判据各建一份), 这里让它们指向同一
    个文件 —— 也就是让"用同一份 IR"这件事在**字节层面**成立, 而不是靠两个路径都叫
    "驱动"。

    **`-O1`**: 原先不给优化开关, clang 的默认 `-O0` 就是它。这个二进制是这一整套判据里
    反复跑的那个, 而同一个二进制在 `-O1` 下**快 6.3 倍** (驱动编译自身: 本机 5 次采样
    54.45–57.53s vs 8.65–9.28s), 产物与 `-O0` **逐字节相同** —— 判据比的正是那份产物,
    优化级别只决定跑得多快。
    """
    key = _digest(ll_text.encode("utf-8"))
    hit = _BUILT.get(f"elf:{key}")
    if hit is not None and hit.exists():
        return hit
    ll = _CACHE_DIR / f"{key}.ll"
    ll.write_text(ll_text, encoding="utf-8")
    elf = _CACHE_DIR / f"{key}.elf"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-O1", "-o", str(elf), str(ll), str(_mem_stub())],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    _BUILT[f"elf:{key}"] = elf
    return elf


#: (二进制字节, 仓库内相对入口) -> 那次运行的结果。
_DRIVER_RUNS: dict[tuple[str, str], tuple[int, str, str]] = {}


def _run_driver_raw(elf: Path, relpath: str, td: str, name: str) -> tuple[int, str, str]:
    """跑自举驱动, 返回 (退出码, stdout 文本, stderr 文本)。

    **入口是仓库相对路径时按 (二进制字节, 入口) 缓存** —— 这条路上同一个二进制被喂给
    同一个大单元好几次 (m83 的定点、语料判据、perf 的两次计时), 一次 ~58s。驱动与链接
    都是确定性的, 判据运行期间**树不变**, 所以同一份字节跑两遍给同一份产出。
    **临时夹具一并排除**: 它们递的是 WSL 绝对路径 (每次运行都不同), 不进这条分支。
    """
    key = None
    if not relpath.startswith("/"):
        key = (_digest(elf.read_bytes()), relpath)
        hit = _DRIVER_RUNS.get(key)
        if hit is not None:
            return hit
    got = Path(td) / f"{name}.out.ll"
    # rm -f 先删: 目标名固定, 上一次刚退出的进程可能还占着 inode
    # (cp 会报 "Text file busy"); unlink 总能成功, cp 于是写新 inode
    script = (f"rm -f {_T}{name} && cp {_wsl_path(elf)} {_T}{name} && chmod +x {_T}{name} && "
              f"cd {_wsl_path(ROOT)} && {_T}{name} {relpath} > {_wsl_path(got)}")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    text = got.read_text(encoding="utf-8") if got.exists() else ""
    out = (r.returncode, text, r.stderr)
    if key is not None:
        _DRIVER_RUNS[key] = out
    return out


def _run_driver(elf: Path, relpath: str, td: str, name: str) -> str:
    """跑自举驱动并要求成功 (cd 到仓库根, 把入口路径交给它 —— 它自己解析 use)。"""
    rc, text, err = _run_driver_raw(elf, relpath, td, name)
    assert rc == 0, f"驱动退出 {rc}: {err[-400:]}"
    return text


#: M85 的**已知缺口**（棘轮: 只许变短）。`_unsupported` 管的是"**参考实现自己**就发不出来"
#: 那一类（那不算缺口, 是目标之外）; 这里管的是另一类 —— **参考发得出来、自举侧的 IR
#: 路径发得不一样**。它是**真缺口**, 所以登记而不是跳过: 条目在, 判据会断言"它**仍然**
#: 不一致"（哪天修好了, 那条断言会红, 逼着把条目删掉）；同时"新添一份语料"也必须先表态,
#: 否则 `assert got == want` 当场红。
M85_KNOWN_GAPS: dict[str, str] = {
    "native_chain.lomt": (
        "链式泛型（泛型函数体里再调泛型函数）: 自举侧的 `prepare`/单态化只做**一趟** —— "
        "它走单元本体, 看到的实参类型还是 `T`, 于是实例名拼成 `pick_T` 而不是参考实现的 "
        "`pick_u32`（参考实现是 8 轮迭代, 走的是*实例*的体）。两处同源: potato 发射那一侧"
        "已经**点名拒**了（`loment_potato_emit_test` 的 REFUSED）、IR 路径这一侧还没有, "
        "所以在这里留一个可见的缺口 —— 见 docs/189 S1 第 21 格第 53 条。"),
}


#: 语料里**不在这条判据目标内**的文件, 按原因跳过。
def _unsupported(target: Path) -> str | None:
    # **声明的读法不是 Loment 的**: 自举侧**按定义**收不了 —— 只认 `grammar loment`
    # 这一种拼法（六门翻译器还没有 Loment 孪生，`docs/188` §4.1）。驱动自己报的话就是
    # "这份源的 `choose write grammar` 自举侧收不了 … 请用参考实现编译"。
    #
    # 这一条**不是把判据放宽**: 判据的目标是"自举编译器能造出语料里那些 `.lomt` 的
    # 产物"，而一份**声明了别的读法**的源是它能力之外的东西 —— 与下面那条
    # "参考实现的 IR 后端发不出来"是同一形状 (目标之外, 不是因为编错了)。
    # 等翻译器有了 Loment 孪生，这个分支自然不再命中: 那时驱动会接受它。
    import potato_from  # noqa: PLC0415
    g, _err, declared = potato_from.read_grammar_decl(
        target.read_text(encoding="utf-8", errors="replace"))
    if declared and g != "loment":
        return f"声明的读法是 {g}（自举侧只收 loment，docs/188 §4.1）"
    try:
        _unit_ll(target)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return None


@test
def test_m85_missing_module_declaration_says_so():
    """issue #24: **缺 `module` 那一行要说得出话，不能让编译器自己段错误。**

    自举驱动原先没有这一条：少了那一行的单元一路走到 codegen —— 而 codegen 假设
    "第一个记号就是 `module`、第二个就是它的名字"，于是**编译器自己**段错误（用户拿到
    shell 的 "Segmentation fault"：没有诊断、没有行号、没有退出码）。参考实现那一步是
    `期望 module（文件必须以 module 开头），得到 'fn'` 退 1。

    **`check` 是会被人拿去喂未知输入的命令**（格式化器、LSP 的半成品缓冲、注册表里被
    截断的第一行），所以这条的代价不是"报错不好看"，而是"工具死了还不说为什么"。

    两面都钉：缺 `module` / 名字写丢了 / 名字换了行 —— 三种各退 1 且说得出是哪一种；
    **合法的源（含写了 `choose write grammar loment` 的那种）照旧要编得出来**。
    """
    clang = _clang()
    native = sys.platform.startswith("linux")
    if not clang or not (native or _wsl()):
        print("      SKIP: 需要 clang, 以及本机 Linux 或 WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs_nomod")
        work = Path(td)

        def run(p: Path, name: str) -> tuple[int, str]:
            if native:
                elf.chmod(0o755)
                r = subprocess.run([str(elf), str(p)], cwd=str(ROOT),
                                   capture_output=True, shell=False)
                return r.returncode, r.stderr.decode("utf-8", "replace")
            rc, _out, err = _run_driver_raw(elf, _wsl_path(p), td, name)
            return rc, err

        bad = {
            "nomod.lomt": ("fn _start() {" + chr(10) + "    syscall4(60, 0, 0, 0);" + chr(10) + "}" + chr(10),
                           "module"),
            "noname.lomt": ("module" + chr(10) + chr(10) + "fn _start() {" + chr(10)
                            + "    syscall4(60, 0, 0, 0);" + chr(10) + "}" + chr(10), "same line"),
        }
        for name, (src, want) in bad.items():
            p = work / name
            p.write_text(src, encoding="utf-8", newline=chr(10))
            rc, err = run(p, name.replace(".", "_"))
            assert rc == 1, f"{name}: 应当退 1, 得到 {rc}（段错误的话就是没拦住）: {err[:200]}"
            assert want in err, f"{name}: 没说清是哪一种: {err[:200]!r}"
        for name, src in {
            "good.lomt": ("module good" + chr(10) + chr(10) + "fn _start() {" + chr(10)
                          + "    syscall4(60, 0, 0, 0);" + chr(10) + "}" + chr(10)),
            "decl.lomt": ("choose write grammar loment" + chr(10) + chr(10) + "module good" + chr(10) + chr(10)
                          + "fn _start() {" + chr(10) + "    syscall4(60, 0, 0, 0);" + chr(10) + "}" + chr(10)),
        }.items():
            p = work / name
            p.write_text(src, encoding="utf-8", newline=chr(10))
            rc, err = run(p, name.replace(".", "_"))
            assert rc == 0, f"{name}: 合法源被拒了: rc={rc} {err[:200]}"
        print("      缺 module / 名字丢了 / 名字换行: 三种都退 1 报得出；合法源照旧编译")


@test
def test_m85_driver_checks_before_emitting():
    """M85: 同一个自举二进制**先检查再发射** —— 负例被拒、正例放行。

    checker 的覆盖面到 40/40 单元之后才敢打开这道闸门 (在这之前它会把合法程序判错)。
    判据:
      * `selfhost/neg/*.lomt` 必须非零退出、带诊断、且**不产出 IR**;
      * `selfhost/pos/*.lomt` 必须零退出且产物与参考逐字节相同。

    这条把 M81 的"错误码集合一致"从"夹具驱动 checker"升级成"**编译器自己**判"。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    neg = sorted((ROOT / "loment" / "selfhost" / "neg").glob("*.lomt"))
    pos = sorted((ROOT / "loment" / "selfhost" / "pos").glob("*.lomt"))
    assert neg and pos, "缺负例/正例语料"
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs_gate")
        for f in neg:
            rel = f.relative_to(ROOT).as_posix()
            rc, out, err = _run_driver_raw(elf, rel, td, f"neg_{f.stem}")
            assert rc != 0, f"{f.name}: 负例没被拒 (exit {rc})"
            assert "static checks failed" in err, f"{f.name}: 没报诊断: {err[:200]}"
            assert "@" in err and "line" in err, f"{f.name}: 诊断格式不对: {err[:200]}"
            assert out.strip() == "", f"{f.name}: 被拒时不该产出 IR"
        # 单元级负例: 跨模块同名 —— 驱动要自己装载完这两个文件才发现, 也必须拒
        rel = "loment/selfhost/neg_across/entry.lomt"
        rc, out, err = _run_driver_raw(elf, rel, td, "neg_across")
        assert rc != 0, f"跨模块重名没被拒 (exit {rc})"
        assert "static checks failed" in err, f"没报诊断: {err[:200]}"
        assert out.strip() == "", "被拒时不该产出 IR"
        # 跨单元负例（`docs/227` §4 第 2 条）：依赖用了**会拖运行期的构造**（除法）却
        # 什么都没声明，而根写着 `no_runtime`（默认档）。
        #
        # **这一条 2026-10-10 换过形状**：从前测的是"库不许写 `choose`"，而那条规则已经
        # 不存在了（依赖现在可以声明需求、根必须满足它）。换成新规则下真正违法的形状之后，
        # **两个实现都判得住** —— 自举侧是把所有单元拼成一整块 token 再查，依赖里那个 `/`
        # 就在眼前；参考实现走的是"依赖义务"那条。
        # （装载器**不再**管依赖写的 `choose`；它只把那些行抹成空白，原因见 `load_file`。）
        rel = "loment/selfhost/neg_dep_choose/entry.lomt"
        rc, out, err = _run_driver_raw(elf, rel, td, "neg_dep_choose")
        assert rc != 0, f"依赖用了运行期构造却没声明, 没被拒 (exit {rc})"
        assert "static checks failed" in err, f"没报诊断: {err[:200]}"
        assert out.strip() == "", "被拒时不该产出 IR"
        for f in pos:
            rel = f.relative_to(ROOT).as_posix()
            unsupported = _unsupported(f)     # 参考实现的 IR 后端能不能发这个文件
            rc, out, err = _run_driver_raw(elf, rel, td, f"pos_{f.stem}")
            # 正例的判据是"**检查阶段**放行"; 能不能发 IR 取决于它是不是 IR 后端的合法目标
            # (pos/ok.lomt 是给 checker 写的正例, 含 IR 后端不支持的类型, 这不是闸门的事)
            assert "static checks failed" not in err, f"{f.name}: 正例被 check 拒了: {err[:200]}"
            if unsupported is None:
                assert rc == 0, f"{f.name}: 正例退出码 {rc}: {err[:200]}"
                assert out == _unit_ll(f), f"{f.name}: 正例产物与参考不一致"
        print(f"      驱动闸门: 负例 {len(neg)}/{len(neg)} 被拒, 正例 {len(pos)}/{len(pos)} 过检")


#: `choose write grammar` 自举侧守卫的语料 (三个单元同一个模块名, 产物才好比)。
GDECL = ROOT / "loment" / "selfhost" / "grammar_decl"


@test
def test_m87_driver_strips_grammar_decl():
    """M87: 自举侧那条 `choose write grammar` 的守卫 —— 该收的收、该拒的拒, 抹掉之后不留痕。

    声明说的是"**怎么读**", 不是那份源的一部分 (`docs/188` §1)。自举侧是在 `lex`
    **之前按字节抹**的, 所以 lexer/parser/codegen 一行都没动 —— 收不收得下, 就是这一条在钉。

    三件:

      * **抹掉之后不留痕**: 带声明的那份与不带的那份 **产物逐字节相同**。对照面是
        **另一个单元**, 不是参考实现 —— 两个实现**一起**错(比如都多抹了一行)时,
        "自举 == 参考"照样绿 (`docs/182` §1.9 那条形状)。**原生拼法有两个, 两个都验**:
        `decl.lomt`(拼 `loment`) 与 `rust.lomt`(拼 `rust`)。后者进这一组, 根据是
        用户 2026-09-22 的裁定「**rust 语法是 Loment 基础语法, 不需要翻译**」——
        它抹掉之后走的是**同一条原生路**, 所以该有**同一份产物**(三个单元同一个模块名);
      * **别的拼法拒**: 参考实现是**真收**那份 `grammar python` 的(它按 Python 读),
        自举侧拒 —— 拒得说清"这门写法还没接上"(`docs/189` §4.1), **不是**"未定义的开关
        `write`"(那是把"还没接上"错报成"你写错了");
      * **拒的时候不产 IR**。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs_grammar")
        outs = {}
        for name in ("plain", "decl", "rust"):
            f = GDECL / f"{name}.lomt"
            rc, out, err = _run_driver_raw(elf, f.relative_to(ROOT).as_posix(), td, name)
            assert rc == 0, f"{name}.lomt: 自举侧退出 {rc}: {err[-300:]}"
            assert out == _unit_ll(f), f"{name}.lomt: 产物与参考不一致"
            outs[name] = out
        for name, spelling in (("decl", "loment"), ("rust", "rust")):
            assert outs[name] == outs["plain"], (
                f"带着 `choose write grammar {spelling}` 编译的产物与不带的不一样 —— "
                f"抹掉之后留痕了")
        f = GDECL / "foreign.lomt"
        rc, out, err = _run_driver_raw(elf, f.relative_to(ROOT).as_posix(), td, "foreign")
        # 参考实现这份是**收**的 (拿它自己的前门按 Python 读) —— 分歧正是 docs/189 §4.1 那句话
        assert _unit_ll(f).strip(), "参考实现本该读得通这份 (夹具的前提)"
        assert rc != 0, f"foreign.lomt: 别的拼法没被拒 (exit {rc})"
        assert "does not take" in err, f"foreign.lomt: 没说到点子上: {err[:200]}"
        assert "未定义的开关" not in err, f"foreign.lomt: 报成了词法/语法错: {err[:200]}"
        assert out.strip() == "", "被拒时不该产出 IR"
        print("      声明: 两个原生拼法(loment/rust)抹掉后都与不带那份逐字节一致; "
              "别的拼法拒且指对原因")


@test
def test_m85_driver_emits_structured_diagnostics():
    """自举驱动的 `--diag-out`: 结构与参考实现同形, **码集相等** (`docs/182` §5)。

    报错器 `lomenterr` 的输入前提。这条钉四件:

      * **一行一条 JSON**, 字段集与参考实现一致 (`lomentc.DIAG_FIELDS`) —— 两个实现
        必须是**同一份形状**, 否则下游得按来源分叉;
      * **码集与参考实现相等** —— 文本本来就不同 (参考吐整句中文, 驱动只有源码片段,
        `docs/158` §4), 诚实的靶子是**码**不是文本;
      * **给路径就建文件**, 无错时是**空的**而不是"不存在" —— 两边同一口径, 这样上一次
        跑剩的旧文件不会被这一次误读成诊断;
      * 有错时退出码**非零**。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    import loment_diag
    src = ROOT / "loment" / "selfhost" / "neg" / "choose_twice.lomt"
    rel = src.relative_to(ROOT).as_posix()
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocsd")

        def drive(entry_rel: str, diag: Path) -> int:
            script = (f"rm -f {_T}fd && cp {_wsl_path(elf)} {_T}fd && chmod +x {_T}fd && "
                      f"cd {_wsl_path(ROOT)} && {_T}fd {entry_rel} --diag-out "
                      f"{_wsl_path(diag)} > /dev/null")
            return subprocess.run([*_WSLCMD, "bash", "-lc", script],
                                  capture_output=True, text=True, timeout=300,
                                  shell=False).returncode

        bad = Path(td) / "bad.jsonl"
        assert drive(rel, bad) != 0, "有错却退了 0"
        recs = [json.loads(x) for x in bad.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert recs, "没写出结构化诊断"
        for d in recs:
            assert set(d) == set(lomentc.DIAG_FIELDS), (sorted(d), list(lomentc.DIAG_FIELDS))
            assert d["line"] > 0, d
        got = sorted({d["code"] for d in recs})
        m = lomentc.load(src)
        dd = lomentc.resolve_deps(m, ROOT, src.parent, entry=src)
        want = sorted({loment_diag.classify(e)[0] for e in lomentc.check(m, deps=dd)})
        assert got == want, f"两侧码集不同: 自举 {got} vs 参考 {want}"
        ok = Path(td) / "ok.jsonl"
        assert drive("loment/selfhost/pos/ok.lomt", ok) == 0, "合法入口却退非零"
        assert ok.exists() and ok.read_bytes() == b"", \
            f"无错时应当是**存在且为空**的, 得到 {ok.read_bytes()[:40]!r}"
        print(f"      驱动 --diag-out: {len(recs)} 条 {got} 与参考码集相等; 无错时空文件")


@test
def test_m83_selfhosted_driver_compiles_itself():
    """M83: 自举驱动是**一个能独立跑的可执行文件**, 且它能编译自己。

    在这之前, 自举链的每一环都是"被 C 驱动调用的函数" —— 没有能独立跑的编译器。
    `driver.lomt` 用 brk 向内核要内存、从 `/proc/self/cmdline` 拿入口路径、
    自己递归解析 `use`、往 stdout 吐 IR。

    判据 (全部在 WSL 里执行):
      1. 参考实现发射 driver.lomt 的单元 -> 链成 ELF -> 跑它 -> 产物与参考逐字节相同;
      2. 用它自己的产物再链一个 ELF (ELF2) -> ELF2 跑同一入口, 产物与 ELF1 相同 (定点);
      3. 同一驱动对别的入口也与参考逐字节相同 (驱动不是"只会编译自己")。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    if not _wsl():
        print("      SKIP: 无 WSL, 交叉产物未执行 (M83 部分)")
        return
    self_rel = DRIVER_LOMT.relative_to(ROOT).as_posix()
    with tempfile.TemporaryDirectory() as td:
        # 1. 参考发射 -> ELF1
        want = _unit_ll(DRIVER_LOMT)
        elf1 = _build_linux_elf(want, td, "fujocs1")
        got1 = _run_driver(elf1, self_rel, td, "fujocs1")
        bad = next((k for k in range(min(len(got1), len(want))) if got1[k] != want[k]), None)
        assert got1 == want, (
            f"驱动产物与参考不一致: want {len(want)}B got {len(got1)}B @{bad}")
        # 2. 用驱动自己的产物再链一个 -> 定点。
        # 上一行已经钉住 `got1 == want`, 即第 2 阶段喂给链接器的是**同一份 IR**; 链接是
        # 确定性的, 所以 elf2 就是 elf1 —— 这里把这件事**显式断言**出来 (而不是让它只是
        # 缓存命中的副作用), 定点才落到"同一个二进制跑同一入口给同一份产出"上。
        elf2 = _build_linux_elf(got1, td, "fujocs2")
        assert elf2.read_bytes() == elf1.read_bytes(), \
            "M84: 产物与参考逐字节相同, 但由它链出的二进制与第 1 阶段不同 —— 链接不定点"
        got2 = _run_driver(elf2, self_rel, td, "fujocs2")
        assert got2 == got1, "M84: 自举驱动的第 2 阶段产物与第 1 阶段不一致"
        # 3. 同一个二进制对别的入口也与参考一致
        for rel in ("loment/examples/native_res.lomt", "loment/examples/demo.lomt"):
            target = ROOT / rel
            assert _run_driver(elf1, rel, td, f"u_{target.stem}") == _unit_ll(target), \
                f"驱动在 {rel} 上与参考不一致"
        print(f"      自举驱动: {len(want)}B 自身单元 -> ELF -> 逐字节相同; 二阶段定点成立; 另 2 例一致")


@test
def test_m86_driver_handles_addin():
    """`addin` 是**跨单元**的（`docs/182` §1.4/§1.7）—— 自举驱动必须认得它。

    这条钉三件事，缺一条 `addin` 就只是"写在纸上"：

    1. 驱动**真的把 `addin` 目标拉进单元** —— 最坏的失败模式是**静默忽略**它，那样
       开关取值就成了"未定义的开关"，而报出来的错与真正的原因（装载器不认它）对不上；
    2. **鸡生蛋那条**（`docs/182` §1.6 ②）：`choose` 的取值写在入口、`set choose` 的定义
       写在被拉进来的那一份里，`banner` 照样编得出来；
    3. 产物与参考实现**逐字节相同** —— 装载顺序是身份的一部分（use 依赖在前、
       `addin` 目标在后、根最后）。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    rel = "loment/examples/addin/main.lomt"
    target = ROOT / rel
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs86")
        # 参考侧走**唯一入口**（预扫 → 装载 → 解析依赖）—— 手拼 `load` + `resolve_deps`
        # 会漏掉预扫那一趟，`addin` 就白写了（实测 `tools/loment.py` 原先就是这么拼的）。
        m2, d2 = lomentc.load_unit(target, ROOT)
        want = lomentc.emit_llvm(m2, ROOT, d2)
        got = _run_driver(elf, rel, td, "addin_main")
        bad = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
        assert got == want, f"addin 用例上驱动与参考不一致 @{bad}"
        assert "define i32 @banner()" in got, got[:300]
        print(f"      addin: 驱动跨单元装载 + 开关定状态, 产物与参考逐字节相同 ({len(got)}B)")


@test
def test_m85_selfhosted_driver_compiles_corpus():
    """M85(核心): 自举驱动**自己做全部装载**, 按路径把整个语料编译一遍。

    入口路径来自 `/proc/self/cmdline`, `use` 递归解析 (依赖先写), 缺 Option/Result
    时注入预置枚举 —— 这些原本都在夹具里 (`_unit_text`)。判据 = 每个可发射的
    `.lomt` 都产出与参考**逐字节相同**的 IR, 一个二进制、一个入口路径。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs85")
        ok, skip, gap = [], [], []
        # 语料 = 示例 + 自举前端 + **工具与库** (loment/tools, loment/lib)。
        # 后者是"用**自举编译器**就能造出这些工具"的判据 —— 装 LSP/格式化器不需要 Python。
        #
        # `loment/comefor` 是 **S4.1 那条判据的落点**（`docs/184` §9）：`def_dialect.lomt`
        # 用自定义语法写、`def_hand.lomt` 是它手写展开后的样子 —— 两份都进语料，
        # "自举驱动的产物与参考逐字节相同"这一条就把它们一起兜住了。
        for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                             + list((ROOT / "loment" / "selfhost").glob("*.lomt"))
                             + list((ROOT / "loment" / "tools").glob("*.lomt"))
                             + list((ROOT / "loment" / "lib").glob("*.lomt"))
                             + list((ROOT / "loment" / "comefor").glob("*.lomt"))):
            why = _unsupported(target)
            if why:
                skip.append((target.name, why))
                continue
            want = _unit_ll(target)
            rel = target.relative_to(ROOT).as_posix()
            got = _run_driver(elf, rel, td, f"m85_{target.stem}")
            bad = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
            if target.name in M85_KNOWN_GAPS:
                # 登记过的缺口: **仍然不一致**才算数 —— 哪天一致了这条会红, 逼着删条目
                # (与 `PARSE_KNOWN_GAPS` 那两道棘轮同一个形状: 只许变短, 不会过期变松)。
                assert got != want, (
                    f"{target.name}: 登记在 M85_KNOWN_GAPS 里, 但产物**已经一致** —— "
                    f"请删掉那条登记（{M85_KNOWN_GAPS[target.name][:60]}…）")
                gap.append((target.name, M85_KNOWN_GAPS[target.name]))
                continue
            assert got == want, (
                f"{rel}: 驱动产物与参考不一致 (want {len(want)}B got {len(got)}B @{bad})")
            ok.append(target.name)
        for name, why in skip:
            print(f"      非目标: {name} ({why})")
        for name, why in gap:
            print(f"      已知缺口: {name} ({why[:60]}…)")
        print(f"      自举驱动按路径编译语料: {len(ok)}/{len(ok)} 逐字节一致"
              + (f"; 已知缺口 {len(gap)} 个" if gap else ""))


@test
def test_m83_m84_self_compile_and_fixed_point():
    """M83/M84: 自举编译器编译自身 -> 可运行二进制; 三阶段产物逐字节相同 (定点)。

    stage1 = 由 **Python 版** lomentc 编译 Loment 版 codegen 得到的可执行文件;
    stage2 = 由 **stage1 自己产出的 IR** 链出的可执行文件 (M83: 编译器编译自己的产出可运行);
    stage3 = 由 stage2 的产出链出。M84 判据 = 第 2/3 阶段产物逐字节相同。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        exe1 = _build_codegen(td)                      # stage1
        s1 = _run_codegen(exe1, CODEGEN, td)           # stage1 产出的 codegen.lomt 的 IR
        assert s1 == _unit_ll(CODEGEN), "stage1 产物与参考不一致 (M82 回归)"
        ll1 = Path(td) / "s1.ll"
        ll1.write_text(s1, encoding="utf-8")
        exe2 = _build_from_ll(ll1, td, "stage2")       # M83
        s2 = _run_codegen(exe2, CODEGEN, td)
        assert s2 == s1, "M84: 第 2 阶段产物与第 1 阶段不一致 (未定点)"
        ll2 = Path(td) / "s2.ll"
        ll2.write_text(s2, encoding="utf-8")
        exe3 = _build_from_ll(ll2, td, "stage3")       # 三阶段
        s3 = _run_codegen(exe3, CODEGEN, td)
        assert s3 == s2, "M84: 第 3 阶段产物与第 2 阶段不一致 (未定点)"
        # 定点不能是巧合: 第 2 阶段对别的单元也要与参考一致
        for target in (ROOT / "loment" / "selfhost" / "checker.lomt",
                       ROOT / "loment" / "selfhost" / "ir_div.lomt"):
            assert _run_codegen(exe2, target, td) == _unit_ll(target), \
                f"stage2 在 {target.name} 上与参考不一致"
        print(f"      定点: stage1 == stage2 == stage3 ({len(s1)}B); stage2 对 checker/ir_div 亦一致")


@test
def test_m82_coverage_report():
    """M82 进度表: 对全部示例跑 Loment 版 codegen 并与 Python 版逐字节比对。

    已知可通过的目标文件必须继续通过(防回归); 其余示例的差异数作为"M82 彻底完成"
    的进度分母打印出来 —— 覆盖到全部示例 = M82 完成 (见 docs/150 剩余清单)。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    known = ["ir_const.lomt", "ir_expr.lomt", "ir_stmt.lomt", "ir_logic.lomt",
             "ir_cast.lomt", "ir_mem.lomt", "ir_for.lomt", "ir_div.lomt", "ir_builtin.lomt",
             "ir_index.lomt",
             "ir_call5.lomt",
             # 预置枚举 + `?` 早退 + `if let` 三条路径的回归闸 (不放进列表就会静默退化)
             "native_res.lomt",
             # 整数->指针 (M83 给托管驱动补的那一步) 与自举驱动自身
             "native_brk.lomt", "driver.lomt",
             # M2 的 str_concat (堆拼接 + 新运行时常量 + 标签表)
             "native_concat.lomt",
             # 用**自举编译器**造工具: 格式化器/文档生成器/LSP 与 JSON 库都必须在列表里,
             # 否则"装工具不需要 Python"这条会静默退化 (装法见 scripts/lomc.ps1)
             "lomfmt.lomt", "lomdoc.lomt", "lsp.lomt", "json.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        ok, diff, unsupported = [], [], []
        for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                             + list((ROOT / "loment" / "selfhost").glob("*.lomt"))
                             + list((ROOT / "loment" / "tools").glob("*.lomt"))
                             + list((ROOT / "loment" / "lib").glob("*.lomt"))):
            try:
                want = _unit_ll(target)
            except Exception as e:  # noqa: BLE001  参考实现的 IR 后端本身就不发这个示例
                unsupported.append((target.name, f"{type(e).__name__}: {e}"))
                continue
            try:
                got = _run_codegen(exe, target, td)
            except Exception:  # noqa: BLE001
                got = ""
            (ok if got == want else diff).append(target.name)
    missing = [k for k in known if k not in ok]
    assert not missing, f"已知可通过的目标文件回归失败: {missing}"
    total = len(ok) + len(diff)
    print(f"      目标覆盖 {len(ok)}/{total} 字节一致" + (f"; 待补: {', '.join(diff)}" if diff else ""))
    for name, why in unsupported:
        print(f"      非目标: {name} (参考实现自己就发不出来 -> {why})")
    if diff:
        print("      缺口分类 (按文件计):")
        for feature, hits in _gap_breakdown(diff).items():
            print(f"        {feature}: {len(hits)}")
    return


@test
def test_m85_codegen_table_capacity():
    """自举 codegen 的**每函数表容量**必须装得下最大的编译单元。

    这是批次 2 抓到的一次静默错编: 单元长到 246 个函数后, 越过了布局里
    `fk 表`的 192 格, 于是表尾被后面的参数替换表写穿 —— `fkind` 读出来是 2, 少数函数被
    改名成 `<接收者>_<方法>`, 逐字节判据只报"两个编译器不一致"。

    2026-09-16: `use std` 那种 128 个模块的门面单元有 **4048 个顶层函数**, 旧容量 (307)
    下自举镜直接段错误。布局改成按 `CG_*` 常量定 (函数表 `CG_FN+i*12`, 容量 `CG_MAXFN`),
    整块也从语言自带的 64 KiB 堆改由驱动器从 `brk` 开 (`cg_arena_bytes()`)。这里把
    "容量 >= 最大单元的函数数"钉成静态判据, 并且**要求守卫与容量常量同源** ——
    表放大了而守卫还卡在旧数, 同样是写穿。
    """
    import re as _re
    src = (ROOT / "loment" / "selfhost" / "codegen.lomt").read_text(encoding="utf-8")

    def cst(name: str) -> int:
        m = _re.search(rf"const {name}: u32 = (\d+);", src)
        assert m, f"{name} 没找到"
        return int(m.group(1))
    fn_base, fn_stride = cst("CG_FN"), 12
    fk_base, fk_stride = cst("CG_FK"), 8
    cur_base = cst("CG_CUR")                          # 当前函数参数替换表
    enum_base, enum_stride = cst("CG_ENUM"), 80
    local_base = cst("CG_LOCAL")                      # 枚举表的上界 = 局部表基址
    param_base, param_stride = cst("CG_PARAM"), 80
    bytes_total = cst("CG_BYTES")
    # 守卫必须用**同一个**常量, 否则表放大了守卫还卡在旧数 (或反过来写穿)
    assert f"n >= CG_MAXFN" in src, "函数表的守卫没和容量常量同源"
    assert f"en >= CG_MAXENUM" in src, "枚举表的守卫没和容量常量同源"
    # 容量 = 每张表在"下一张表开始时"之前能放多少个。
    cap_fn = (fk_base - fn_base) // fn_stride          # fk 表紧跟函数表
    cap_fk = (cur_base - fk_base) // fk_stride         # 参数替换表紧跟 fk 表
    cap_param = (bytes_total - param_base) // param_stride
    cap_enum = (local_base - enum_base) // enum_stride  # 枚举表上界 = 局部表基址
    cap = min(cap_fn, cap_fk, cap_param)
    # 最大单元 = driver.lomt 的整单元 (lexer+codegen+checker+driver)。
    # 用**真实词法器**数 `fn` 标识符 token —— 这正是 codegen 看到的数量 (字符串里的
    # "fn" 是 string token, 不算; 这也是 codegen.lomt 的 tok_is 刚补上的守卫)。
    entry = ROOT / "loment" / "selfhost" / "driver.lomt"
    unit_toks = lomc.lex(_unit_text(entry))
    nfns = sum(1 for tk in unit_toks if tk.kind == "ident" and tk.val == "fn")
    assert nfns <= cap, (f"最大单元有 {nfns} 个函数, 超过 codegen 表容量 {cap} "
                         f"(fn {cap_fn} / fk {cap_fk} / 形参 {cap_param}) —— 请重排布局")
    nenum = sum(1 for tk in unit_toks if tk.kind == "ident" and tk.val == "enum")
    assert nenum <= cap_enum, f"最大单元有 {nenum} 个枚举, 超过枚举表容量 {cap_enum}"
    print(f"      codegen 表容量: {cap} 个函数 (fn {cap_fn}/fk {cap_fk}/形参 {cap_param}), "
          f"最大单元 {nfns} 个; 枚举表 {cap_enum} 格, 单元里 {nenum} 个")


@test
def test_m85_heap_budget():
    """静态预算: 驱动从 `brk` 拿的**固定**块之和必须留在 PE 垫片的堆上限以内。

    这是**批次 2 期间被抓到的一次真实停机**: checker 加的 `alloc(2048)` 让它和 codegen
    的 `alloc(49152)` 一起越过语言自带的 64 KiB 堆, 边界检查走 `@__loment_abort`,
    在自举驱动里表现为一条**非法指令 (SIGILL)** —— 从输出上看像"编译器崩了"。

    2026-09-16 起**两条路径都不再用语言堆**: checker 的 arena 与 codegen 的状态块都由
    驱动器从 `brk` 开 (两块加起来 ~600 KB, 放不进 64 KiB)。于是预算的边界换成了 PE 垫片
    的 `WS_HEAP` —— 这正是 `use std` 撞到的那道墙: 1.86 MB 的单元按旧的
    `(len+64)*20` 要 37 MB 词法缓冲, 加上原有的 ~30 MB 就越过了 64 MB (`fujoc-s: 单元太大`)。
    所以这里钉两件事:
      ① 语言堆里不再有固定分配 (否则一旦放大又回去撞 SIGILL);
      ② 驱动的**固定** brk 块之和 <= `WS_HEAP` (Linux 侧 brk 走内核, 无此上限)。
    """
    import re as _re
    heap = 65536          # 语言自带的 bump 堆 (镜像 lomentc 的 @__loment_heap)
    # ① 语言堆: 两个模块里都不该再有固定 alloc
    for rel in ("loment/selfhost/checker.lomt", "loment/selfhost/codegen.lomt"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        fixed = [int(m) for m in _re.findall(r"\balloc\((\d+)\)", src)]
        assert not fixed, (
            f"{rel} 里还有语言堆的固定分配 {fixed} —— 状态块现在按单元规模定 "
            f"(放大就撞 64 KiB 的 SIGILL), 请改由驱动器从 brk 开")
    drv = (ROOT / "loment" / "selfhost" / "driver.lomt").read_text(encoding="utf-8")
    # ② 固定 brk 块 (常量实参) 之和 <= WS_HEAP
    lom = (ROOT / "tools" / "lomelf.py").read_text(encoding="utf-8")
    m = _re.search(r"WS_HEAP = (\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)", lom)
    assert m, "找不到 WS_HEAP"
    ws_heap = int(m.group(1)) * int(m.group(2)) * int(m.group(3))
    fixed = _re.findall(r"sys_alloc\(([A-Z_]+) as u64\)", drv)
    consts = {}
    for rel in ("loment/selfhost/driver.lomt", "loment/selfhost/codegen.lomt"):
        consts.update({n: int(c) for n, c in
                       _re.findall(r"const ([A-Z_]+): u32 = (\d+);",
                                   (ROOT / rel).read_text(encoding="utf-8"))})
    total = sum(consts.get(n, 0) for n in fixed) + 65536 + 16        # argv_buf + snc
    for fn in ("chk_arena_bytes", "cg_arena_bytes"):
        rel = "checker.lomt" if fn.startswith("chk") else "codegen.lomt"
        src = (ROOT / "loment" / "selfhost" / rel).read_text(encoding="utf-8")
        mm = _re.search(rf"{fn}\(\) -> u32 \{{\s*return\s+(\w+);", src)
        assert mm, f"找不到 {fn}()"
        v = mm.group(1)
        total += int(v) if v.isdigit() else consts[v]
    assert total < ws_heap, (
        f"驱动的固定 brk 块合计 {total}B 超过 PE 垫片的堆上限 {ws_heap}B —— "
        f"调小 UNIT_CAP/IR_CAP/TOKS_CAP 或抬高 lomelf.py 的 WS_HEAP")
    print(f"      预算: 语言堆不用 (checker/codegen 都走 brk); 固定 brk 块 {total}B "
          f"< PE 垫片堆 {ws_heap}B (余 {ws_heap - total}B, 还要放单元的词法缓冲)")


@test
def test_m85_codegen_arg_arity_is_loud():
    """实参上限 (10) 必须**响亮地失败**, 不能静默截断。

    自举 codegen 的形参类型表步长 80 = 10 槽 x 8 字节 (加宽会撞 40960 的枚举表), 所以
    实参/形参上限是 10。批次 2 里第一次出现 11 个实参的调用时它**静默丢了最后一个**
    (IR 少一个实参), 参考实现照发 -> 逐字节判据报"两个编译器不一致", 但定位成本很高。
    现在超限会 `panic(10)`; 这条测试同时钉住两边: ① 驱动源码里仍有那道闸门;
    ② 语料里没有任何函数超过 10 个形参 (否则把闸门"修好"就等于让它再次静默)。
    """
    src = (ROOT / "loment" / "selfhost" / "codegen.lomt").read_text(encoding="utf-8")
    assert "panic(10);" in src, "自举 codegen 的实参超限闸门不见了"
    assert "a < 10" in src, "实参上限常量变了: 请同步形参表步长与这条测试"
    worst = 0
    worst_fn = ""
    for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                         + list((ROOT / "loment" / "selfhost").glob("*.lomt"))):
        try:
            mod = lomentc.load(target)
        except Exception:  # noqa: BLE001
            continue
        for f in mod.funcs:
            if len(f.params) > worst:
                worst, worst_fn = len(f.params), f"{target.name}:{f.name}"
    assert worst <= 10, f"语料里有 {worst} 个形参的函数 ({worst_fn}), 超过 self-hosted codegen 的 10 槽上限"
    print(f"      实参上限: 闸门存在; 语料最大形参数 {worst} ({worst_fn}) <= 10")


@test
def test_m86_selfhost_perf_budget():
    """M86: 自举驱动编译自身的时间进护栏 (回归判据, 不是紧预算)。

    实测基线 (本机; WSL 内跑 ELF, 减掉 ~0.12s 的 WSL 启动开销):

    | 单元 | 函数数 | 自举驱动 | 参考实现 (Python) |
    |---|---|---|---|
    | mathutil | 2 | <0.1s | ~0.1s |
    | lexer | 10 | <0.1s | ~0.1s |
    | codegen | 130 | 4.6s | ~0.7s |
    | driver (自编译) | **253** | **12.8s** | 1.2s |
    | driver (自编译) | **435** | **34.1s** | 1.7s |
    | driver (自编译) | **446** | **42.3s** | 1.7s |

    自举版比参考实现慢约 10 倍, 而且**超线性** (130 -> 253 -> 435 个函数,
    4.6s -> 12.8s -> 34.1s): 符号查找是**线性扫** (`chk_lookup_slot` / codegen 的
    `find_fn`), 单元越大越贵 —— `12.8 × (435/253)^2 ≈ 37.9s`, 实测 34.1s, 就在这条线上。

    ⚠ **量这条的时候先看机器闲不闲**：2026-09-22 本轮实测，空闲时 42.3s（WSL 基线 0.15s），
    而我自己的一个后台编译在跑时同一份源报了 **81s** —— 差 1.9 倍全在争抢上。
    判"这条红是不是回归"之前，先单独跑一次并且确认没有别的构建在跑。

    ⚠ **2026-10-10 复核：余量只剩 ~1%**。并进 `runtime` 维兑现（`docs/227`）与自适应收集器
    （`docs/212`/`#200`）之后，**单独跑**（确认没有别的构建在跑）实测 **59.33s / 60s**。
    同一棵树上后台还有活时那次报 **61.4s** —— 正是上面那条"先看机器闲不闲"的又一次实测。
    **护栏这次没有抬**：它是绊线不是预算，抬了就得有个"谁变慢了"的说法，而这里没有 ——
    变慢的是**单元规模**（那条超线性曲线本来就写着这个结局）。真正的修法还是给
    `chk_lookup_slot` / `find_fn` 加索引，那件事仍然没做。

    **2026-09-21 护栏从 30s 抬到 60s**: 第二行那个 435 是 `docs/189` S1 第十九格
    (自举侧 potato 发射, +59 个函数) 之后的驱动链 —— 它把 30s 那道线顶破了,
    而**破的不是"谁变慢了"**: 参考实现在同一份单元上只从 0.40s 涨到 0.52s(1.3x),
    自举侧涨 2.7x, 差距正是上面那条超线性曲线。真正的修法是给那几处查找加索引
    (**独立的一件事**, 没有做), 在那之前护栏只能跟着单元规模走。它仍然报数,
    所以"忽然又慢一倍"这种真回归照样看得见。

    **2026-10-12 护栏从 90s 降到 20s —— 这条与上面几次方向相反, 理由也不同**:
    上面几次是"线没动, 单元长大了"; 这一次是**被测的那个二进制换了优化级别** ——
    `_build_linux_elf` 从 clang 默认的 `-O0` 改成 `-O1` (同一份 IR 从 54.5s 降到
    8.65s, 本机 5 次采样 8.65–9.28s, 产物与 `-O0` 逐字节相同)。线不动才是错的:
    90s 之下, 这条判据要等到自举侧**慢 10 倍**才响, 那已经不是绊线了。
    20s ≈ 实测的 2.3 倍, 仍罩得住这条判据自己记录过的最大争抢膨胀 (1.9 倍,
    见上面 42.3s -> 81s 那次), "忽然又慢一倍"照样看得见。
    上面表格里那些 `-O0` 的数不再与本判据直接可比, 但作为**超线性曲线**的记账保留。
    """
    if not (_wsl() and _clang()):
        print("      SKIP: 需要 WSL + clang")
        return
    with tempfile.TemporaryDirectory() as td:
        ll = _CACHE_DIR / "perf_driver.ll"
        ll.write_text(_unit_ll(DRIVER_LOMT), encoding="utf-8")
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "perf_drv")
        subprocess.run([*_WSLCMD, "bash", "-lc",
                        f"rm -f {_T}perf_drv && "
                        f"cp {_wsl_path(elf)} {_T}perf_drv && chmod +x {_T}perf_drv"],
                       capture_output=True, text=True, timeout=120, shell=False)

        def timed(cmd: str) -> float:
            t0 = time.time()
            subprocess.run([*_WSLCMD, "bash", "-lc", cmd],
                           capture_output=True, text=True, timeout=300, shell=False)
            return time.time() - t0

        base = min(timed("true") for _ in range(3))
        runs = [timed(f"cd {_wsl_path(ROOT)} && {_T}perf_drv loment/selfhost/driver.lomt "
                      f"> /dev/null") for _ in range(2)]
        best = min(runs) - base
        #: **函数数打进输出**（2026-10-10 起）：这条护栏量的是一条 **O(n²)** 的曲线
        #: （见本函数头注），所以"时间涨了"要先问"单元是不是也涨了"。不印这个数，
        #: 下一个人只看到时间变了，分不清是回归还是规模。
        n_fn = len(re.findall(r"^define ", ll.read_text(encoding="utf-8"), re.M))
        #: **90 → 20（2026-10-12）**：与上面几次**方向相反** —— 不是单元长大了，是
        #: ELF 换了优化级别（`-O0` → `-O1`，见 `_build_linux_elf`：同一份 IR 54.5s → 8.65s，
        #: 产物逐字节相同）。线不动才是错的：90s 之下要等自举侧慢 10 倍才响。
        #: 20s ≈ 实测的 2.3 倍，仍罩得住本判据记录过的最大争抢膨胀（1.9 倍）。
        #: **它仍然是绊线，不是预算**：20s 之下，"忽然又慢一倍"照样看得见。
        #: 真正的修法没变 —— 给 `chk_lookup_slot` / `find_fn` 加索引，那件事仍然没做。
        budget = 20.0
        assert best <= budget, (
            f"自举自编译 {best:.1f}s 超过护栏 {budget:.0f}s ({n_fn} 个函数; "
            f"-O1 下 618 个函数时基线 8.65s)")
        print(f"      自举自编译: {best:.2f}s / {n_fn} 个函数 (护栏 {budget:.0f}s; "
              f"参考实现 Python 1.7s, WSL 基线 {base:.2f}s)")


#: 参考实现拒了、而**驱动器看不见**的用例。驱动器只有 lex -> check -> emit、**没有 parser**,
#: 所以两类都落在这里, 各自的理由写在条目旁:
#:   * **解析期错误** —— 驱动器没有那一趟, 看不见;
#:   * **自举 checker 还没实现的规则**（`loment_rule_parity` 里登记成 MISSING 的那几条）——
#:     驱动器跑得到, 但它的 checker 还没那条规则。
#: 两类都只要求"驱动器不崩", 不要求它拒。
PARSE_LEVEL: set[str] = {
    # `#22` 的控制流规则（非 `()` 函数不许从末尾掉出去, 码 E027）：自举 checker 还没实现,
    # `loment_rule_parity` 里同样是 MISSING。那一边补完, 这两行一起删。
    "falloff-if",
    "falloff-match-arm",
    # `#12` 的二进制字面量（`0b1010`）：参考实现报 **E019**，也就是**解析期** —— 驱动器没有
    # parser 那一趟, 看不见它, 所以这一格属于这里而**不是**缺口。探针（`bin-literal`）是
    # `loment_rule_parity` 在 #396 里补的, 补的时候漏了这条登记, 于是"驱动器闸门"那一版变红。
    "bin-literal",
}


@test
def test_m85_driver_gate_on_probe_cases():
    """M85 字面判据 (可映射部分): 把 `loment_rule_parity` 的规则负例**直接喂给自举驱动**。

    `lomentc_test` 那 91 条里可映射到驱动器的是"规则判定 / 确定性 / 抗崩"三类, 这里把
    规则判定与抗崩合起来做: 每条规则负例都让驱动器跑一遍, 要求
      ① 退出码只能是 0 或 1 —— **绝不能因信号而死** (检查器开发期崩过两次: 游标越过 eof、
         递归死循环, 都是"进程直接没了"而不是"报了个错");
      ② 参考实现判错的用例, 驱动器也要拒 (除 `PARSE_LEVEL` 登记的解析期用例 —— 驱动器
         没有 parser, 解析期错误它看不见, 这类只能靠参考实现兜)。

    不可映射的三类 (运行时/双后端、Python API 形状断言、夹具侧 3 目录加载) 列在 docs/150;
    3 目录加载其实已被"驱动自编译"覆盖 (driver -> codegen -> lexer/bytes 四级 use)。
    """
    if not (_wsl() and _clang()):
        print("      SKIP: 需要 WSL + clang")
        return
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_rule_parity as P  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "gate_drv")
        cases = list(P._CASES)
        bad_signal: list[str] = []
        leaked: list[str] = []
        for rid, src in cases:
            f = Path(td) / f"g_{rid}.lomt"
            f.write_text(src, encoding="utf-8", newline="\n")
            rc, _out, _err = _run_driver_raw(elf, _wsl_path(f), td, f"g_{rid}")
            if rc not in (0, 1):
                bad_signal.append(f"{rid}(rc={rc})")
            elif rc == 0 and rid not in PARSE_LEVEL:
                leaked.append(rid)
        assert not bad_signal, f"驱动器被信号打死: {bad_signal}"
        assert not leaked, (f"这些规则负例驱动器放行了: {leaked} —— 要么检查器漏了这条规则, "
                            f"要么它其实是解析期错误 (请登记进 PARSE_LEVEL)")
        print(f"      规则负例经驱动器: {len(cases)} 条全部非零退出且无信号 "
              f"(解析期豁免 {len(PARSE_LEVEL)} 条)")
        # 名字形式 `use <名字>` 装载失败必须**报出来** (镜像侧), 不许静默少装一个依赖 ——
        # 静默少装的后果是后面报一条 E002 "未定义的函数", 用户根本看不出是导入出的事。
        miss = Path(td) / "g_namemiss.lomt"
        miss.write_text("module g_namemiss\n\nuse nosuchmod\n\nfn f() -> u32 { return 0; }\n",
                        encoding="utf-8", newline="\n")
        rc2, _o2, err2 = _run_driver_raw(elf, _wsl_path(miss), td, "g_namemiss")
        assert rc2 != 0 and "name import" in err2, \
            f"名字导入失败没报出来: rc={rc2} err={err2[-200:]!r}"
        print("      名字导入失败: 驱动器报错并退出非零")
        # ---- 名字形式的两层落点与 use 条数上限 (2026-09-16, 见 docs/143 + docs/158 §2)
        # ① 项目本地 `deps/`: 名字形式命中它, 且包内的**路径形式**相对**被导入文件所在
        #    目录**解析 (`deps/geom/geom.lomt` 里的 `use "area.lomt"`)。驱动器 CWD 是仓库根,
        #    所以 CWD 那条路必定落空 —— 走的正是这一层。
        proj = Path(td) / "proj"
        (proj / "deps" / "geom").mkdir(parents=True)
        (proj / "deps" / "geom" / "area.lomt").write_text(
            "module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n",
            encoding="utf-8", newline="\n")
        (proj / "deps" / "geom" / "geom.lomt").write_text(
            "module geom\n\nuse \"area.lomt\"\n\npub fn g_area(w: u32, h: u32) -> u32 {\n"
            "    return ar(w, h);\n}\n", encoding="utf-8", newline="\n")
        dep_hi = proj / "hi.lomt"
        dep_hi.write_text("module hi\n\nuse geom\n\nfn main() -> u32 {\n"
                          "    return g_area(3 as u32, 4 as u32);\n}\n",
                          encoding="utf-8", newline="\n")
        rc3, out3, err3 = _run_driver_raw(elf, _wsl_path(dep_hi), td, "g_deps")
        assert rc3 == 0, f"deps/ 那层没解析出来: rc={rc3} err={err3[-300:]!r}"
        assert "@g_area" in out3, out3[:200]
        print("      deps/ 层 + 包内路径形式: 驱动器解析成功")
        # ② 单文件 use 超过上限**报错**, 不静默丢 (丢掉的话单元少几块, 而参考实现照收 ——
        #    两个实现于是对同一份源码给出不同的产物)。
        many = proj / "deps" / "many"      # 名字形式第 1 层: <项目根>/deps/<名字>/
        many.mkdir(parents=True)
        nlim = lomentc.MAX_USE + 1
        for i in range(nlim):
            (many / f"m{i}.lomt").write_text(
                f"module m{i}\n\npub fn f{i}() -> u32 {{\n    return {i} as u32;\n}}\n",
                encoding="utf-8", newline="\n")
        (many / "many.lomt").write_text(
            "module many\n\n" + "\n".join(f'use "m{i}.lomt"' for i in range(nlim)) + "\n",
            encoding="utf-8", newline="\n")
        lim_hi = proj / "lim.lomt"
        lim_hi.write_text("module lim\n\nuse many\n\nfn main() -> u32 {\n    return 0;\n}\n",
                          encoding="utf-8", newline="\n")
        rc4, _o4, err4 = _run_driver_raw(elf, _wsl_path(lim_hi), td, "g_uselim")
        assert rc4 != 0 and "`use` lines in one file" in err4, \
            f"{nlim} 条 use 没被拒: rc={rc4} err={err4[-300:]!r}"
        print(f"      use 超过 {lomentc.MAX_USE} 条: 驱动器报错并退出非零")
        # ---- 自定义后缀 (2026-09-16, 见 docs/143 §2.3): `loment.conf` 把名字形式的后缀
        # 从 `.lomt` 换成 `.foo`。**判据是两边逐字节一致** —— 这条路上有三处各自独立实现
        # 的东西 (读配置的扫描器、两种后缀的候选顺序、后缀到扩展名的拼接), 只对一边测等于
        # 只测了一半: 自举镜多试一个后缀、参考实现少试一个, 都是"装得少一点"的静默错误。
        cust = Path(td) / "cust"
        (cust / "deps" / "geom").mkdir(parents=True)
        (cust / "loment.conf").write_text(
            '// 项目配置: 源码后缀换成 .foo\nmodule conf\n\n'
            'pub fn source_ext() -> str {\n    return ".foo";\n}\n',
            encoding="utf-8", newline="\n")
        (cust / "deps" / "geom" / "area.foo").write_text(
            "module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n",
            encoding="utf-8", newline="\n")
        (cust / "deps" / "geom" / "geom.foo").write_text(
            "module geom\n\nuse \"area.foo\"\n\npub fn g_area(w: u32, h: u32) -> u32 {\n"
            "    return ar(w, h);\n}\n", encoding="utf-8", newline="\n")
        c_hi = cust / "hi.foo"
        c_hi.write_text("module hi\n\nuse geom\n\nfn main() -> u32 {\n"
                        "    return g_area(3 as u32, 4 as u32);\n}\n",
                        encoding="utf-8", newline="\n")
        rc5, got5, err5 = _run_driver_raw(elf, _wsl_path(c_hi), td, "g_cust")
        assert rc5 == 0, f"自定义后缀的工程没编过: rc={rc5} err={err5[-400:]!r}"
        cmod = lomentc.load(c_hi)
        assert lomentc.source_ext_of(cust, None) == ".foo", "参考实现没读出 loment.conf"
        cdeps = lomentc.resolve_deps(cmod, ROOT, cust, entry=c_hi)
        want5 = lomentc.emit_llvm(cmod, ROOT, cdeps)
        assert "@g_area" in got5, got5[:200]
        if got5 != want5:
            k = next((i for i in range(min(len(got5), len(want5))) if got5[i] != want5[i]),
                     min(len(got5), len(want5)))
            raise AssertionError(f"自定义后缀的单元两个实现不一致 @{k}: "
                                 f"驱动 {got5[max(0,k-60):k+40]!r} != 参考 {want5[max(0,k-60):k+40]!r}")
        print("      自定义后缀 (.foo): 名字形式 + 包内路径形式, 驱动与参考逐字节一致")
        # 配坏的后缀 (不以 `.` 开头) 两个实现都要**当没配** —— 否则"改了配置但发现没生效"
        # 这件事只有一边看得见。这里只有 `geom.foo`, 后缀被忽略 => 找不到 => 必须报错。
        (cust / "loment.conf").write_text(
            'pub fn source_ext() -> str { return "foo"; }\n',
            encoding="utf-8", newline="\n")
        rc6, _o6, err6 = _run_driver_raw(elf, _wsl_path(c_hi), td, "g_badcfg")
        assert rc6 != 0 and "name import" in err6, \
            f"不以 `.` 开头的后缀没被忽略: rc={rc6} err={err6[-300:]!r}"
        assert lomentc.source_ext_of(cust, None) == ".lomt", "参考实现没忽略配坏的后缀"
        print("      配坏的后缀 (缺前导 `.`): 驱动与参考都当没配")
        # ---- 外部函数 (docs/173)。这条路上自举镜踩过两个**静默错编**, 两条都要钉住:
        # ① 收集阶段把 `extern fn f(a,b) -> T;` 当普通函数 → 发出一条没有函数体的 define,
        #    游标从签名滑进下一个函数的体 (main 整个消失, 退出码 0);
        # ② `emit_str_globals`/`strings_needed` 靠"找 `{`"界定函数体 → 外部函数没有体,
        #    于是把**下一个函数**的字符串常量挂到它名下 (`@.str.c_puts.0` 而不是 `@.str.main.0`)。
        # 判据同时覆盖两种形态, 且**比的是两个实现逐字节相同** —— 这正是这两条错误的症状
        # (前者让整段 IR 错位, 后者只错一个全局名, 只测"能不能编过"抓不到)。
        for tag, src in (("g_ext", "module e\n\nextern fn c_add(a: i32, b: i32) -> i32;\n\n"
                                  "fn main() -> i32 {\n    return c_add(3 as i32, 4 as i32);\n}\n"),
                         ("g_extstr", "module e\n\nextern fn c_puts(p: ptr) -> i32;\n\n"
                                      "fn main() -> i32 {\n    let s: str = \"hello\";\n"
                                      "    c_puts(str_ptr(s));\n    return 0;\n}\n")):
            d = Path(td) / tag
            d.mkdir()
            f = d / f"{tag}.lomt"
            f.write_text(src, encoding="utf-8", newline="\n")
            rc7, got7, err7 = _run_driver_raw(elf, _wsl_path(f), td, tag)
            assert rc7 == 0, f"自举镜编不过 extern ({tag}): rc={rc7} err={err7[-300:]!r}"
            want7 = lomentc.emit_llvm(lomentc.load(f), ROOT)
            assert got7 == want7, f"extern 的 IR 两个实现不一致 ({tag})"
        print("      extern fn: 声明 + 调用 + 字符串常量, 驱动与参考逐字节一致")
        # `tok_is` 比的是 token 文本, 源码里的**字符串字面量** `"extern"` 也会被它匹配上 ——
        # 判"前一个 token 是不是 extern"时必须同时判 kind == 0, 否则
        # `let s: str = "extern";` 这种完全正常的程序会被误判成外部函数的声明。
        lit_dir = Path(td) / "ffi_lit"
        lit_dir.mkdir()
        lit_f = lit_dir / "l.lomt"
        lit_f.write_text(
            "module l\n\nfn main() -> i32 {\n"
            '    let s: str = "extern";\n    return str_len(s) as i32;\n}\n',
            encoding="utf-8", newline="\n")
        rc8, got8, err8 = _run_driver_raw(elf, _wsl_path(lit_f), td, "g_extlit")
        assert rc8 == 0, f'字符串字面量 "extern" 被误拒了: rc={rc8} err={err8[-300:]!r}'
        assert got8 == lomentc.emit_llvm(lomentc.load(lit_f), ROOT), "字面量 extern 的 IR 不一致"
        print('      字符串字面量 "extern": 不误判 (判的是 token kind, 不是文本)')


@test
def test_m85_entry_load_distinguishes_empty_file_from_directory():
    """issue #52: 空文件、目录、路径不对, 三句话必须分得开。

    `load_file` 读到 0 字节时原先一律说「路径对吗?」。空文件与目录都走那一条,
    读者被指去查一条没问题的路径。缺文件本来就是另一句（「打不开」）, 这条不许被换掉。
    只有注释的文件与空文件一样「没有可编译的源」, 但字节数不是 0, 仍然应当被接受
    (issue 里点名的邻居; 不是 #45 的格式化行为)。
    """
    clang = _clang()
    native = sys.platform.startswith("linux")
    if not clang or not (native or _wsl()):
        print("      SKIP: 需要 clang, 以及本机 Linux 或 WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        elf = _build_linux_elf(_unit_ll(DRIVER_LOMT), td, "fujocs_empty")
        work = Path(td)

        def run(p: Path, name: str, *extra: str) -> tuple[int, str]:
            if native:
                elf.chmod(0o755)
                r = subprocess.run([str(elf), str(p), *extra], cwd=str(ROOT),
                                   capture_output=True, shell=False)
                err = r.stderr.decode("utf-8", "replace")
                return r.returncode, err
            if extra:
                # 带额外实参的那一次走这条路：`_run_driver_raw` 只会递一个入口。
                script = (f"rm -f {_T}{name} && cp {_wsl_path(elf)} {_T}{name} && "
                          f"chmod +x {_T}{name} && cd {_wsl_path(ROOT)} && "
                          f"{_T}{name} {_wsl_path(p)} {' '.join(extra)} > /dev/null")
                r2 = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                                    capture_output=True, text=True, timeout=300, shell=False)
                return r2.returncode, r2.stderr
            # 绝对路径交给 WSL 里跑的驱动前要转成 /mnt/<drive>/…, 否则反斜杠会被
            # bash 吃掉 (与本文件其它传绝对路径的判据同一条约定)
            rc, _out, err = _run_driver_raw(elf, _wsl_path(p), td, name)
            return rc, err

        empty = work / "zero.lomt"
        empty.write_bytes(b"")
        rc, err = run(empty, "empty")
        assert rc == 1, f"空文件应当退 1, 得到 {rc}: {err[:300]}"
        assert "is empty" in err, f"空文件没说自己是空的: {err[:300]}"
        assert "is the path right" not in err, f"空文件仍被说成路径问题: {err[:300]}"
        assert "is a directory" not in err, f"空文件被说成目录: {err[:300]}"

        rc, err = run(work, "dir")
        assert rc == 1, f"目录应当退 1, 得到 {rc}: {err[:300]}"
        assert "is a directory" in err, f"目录没说自己是目录: {err[:300]}"
        assert "is the path right" not in err, f"目录仍被说成路径问题: {err[:300]}"
        assert "is empty" not in err, f"目录被说成空文件: {err[:300]}"

        missing = work / "no-such-entry.lomt"
        rc, err = run(missing, "missing")
        assert rc == 1, f"缺文件应当退 1, 得到 {rc}: {err[:300]}"
        assert "cannot open" in err, f"缺文件不再报「打不开」: {err[:300]}"
        assert "is empty" not in err and "is a directory" not in err, (
            f"缺文件被说成空文件或目录: {err[:300]}")
        # `issue #8`：这句话里**不许有杂散字节**。原先那句手写了长度 20，而文案只有
        # 19 字节 —— 多出来的一字节是字符串常量的 NUL，用户拿到的是 `打不开 \0路径`
        # （任何把它当 C 串读的消费者都只看得到前半截）。行尾那个 `\n` 不算杂散。
        assert "\x00" not in err, f"缺文件那句话里带了 NUL: {err[:300]!r}"
        assert "\x00" not in run(work, "dir_nul")[1], "目录那句话里带了 NUL"
        # `issue #8`：**没有源码位置**的失败也要进结构化诊断（`E031` 这一档）。
        # 没写进去的话启动器只能回吐裸行 —— 那正是 issue 报的那个症状。
        # `line` 必须是 **0**，不是编出来的行号：渲染器见 0 就不印 `:行号`。
        dj = work / "d.jsonl"
        rc, err = run(missing, "missing_diag", "--diag-out", _wsl_path(dj) if not native else str(dj))
        assert rc == 1, f"缺文件应当退 1, 得到 {rc}: {err[:300]}"
        recs = [json.loads(x) for x in dj.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert recs, "缺文件没写结构化诊断 (启动器只能回吐裸行)"
        assert recs[0]["code"] == "E031", f"缺文件的码不是 E031: {recs[0]}"
        assert recs[0]["line"] == 0, f"没有位置的那一档 line 必须是 0: {recs[0]}"
        assert recs[0]["file"].endswith("no-such-entry.lomt"), recs[0]
        assert "cannot open" in err, "写结构化诊断的同时, 给人看的那一行也要照打"

        comment = work / "comment.lomt"
        comment.write_text("// only a comment\n", encoding="utf-8", newline="\n")
        rc, err = run(comment, "comment")
        assert rc == 0, f"只有注释的文件应当被接受, 得到 {rc}: {err[:300]}"
        assert "is empty" not in err and "is the path right" not in err, err[:300]

        real = work / "one.lomt"
        real.write_text("module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
                        encoding="utf-8", newline="\n")
        rc, err = run(real, "real")
        assert rc == 0, f"非空源文件不该被入口装载拒绝: {rc}: {err[:300]}"
        print("      入口装载: 空文件 / 目录 / 缺路径 三句话分开, 注释文件仍接受")


def _gap_breakdown(diff: list[str]) -> dict[str, list[str]]:
    """按"该文件需要哪些尚未实现的后端特性"给待补文件分类 (M82 工作list)。"""
    import re
    feats = {
        "除法/取模 (trap+运行时)": r"[^\w\s]/[^\w\s=]|%",
        "内建 alloc/free/str_*/atomic/位域": r"\b(alloc|free|str_len|str_eq|str_byte|slice_len|str_ptr|ptr_add|ptr_sub|atomic_add|get_bits|set_bits)\b",
        "syscall 内联汇编": r"\bsyscall[46]\b",
        "for 循环": r"\bfor\b",
        "match/枚举": r"\bmatch\b|\benum\b",
        "聚合/切片/字符串类型": r"\bstruct\b|\[[^\]]*\]|\bstr\b",
        "能力域/guard": r"\bguard\b|\bcapability\b|\bexcluded\b",
    }
    dirs = [ROOT / "loment" / "examples", ROOT / "loment" / "selfhost"]
    src_of = {}
    for d in dirs:
        for p in d.glob("*.lomt"):
            src_of[p.name] = p
    out: dict[str, list[str]] = {}
    for name in diff:
        p = src_of.get(name)
        if p is None:
            continue
        text = re.sub(r"//[^\n]*", "", p.read_text(encoding="utf-8"))
        for feat, pat in feats.items():
            if re.search(pat, text):
                out.setdefault(feat, []).append(name)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


def main() -> int:
    return loment_suite.run(TESTS, "loment_p8_test")


if __name__ == "__main__":
    sys.exit(main())
