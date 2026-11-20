#!/usr/bin/env python3
# loment_regex_test.py — 完整正则引擎的对拍判据（vs CPython `re`）
#
# 被测的是 `lompi/store/std/0.1.0/regex.lomt`（UTF-8、捕获、反向引用、`{n,m}`、惰性量词、
# 前后瞻）。驱动器是 `loment/tools/lomrxcheck.lomt`：它吃 JSONL、对每一条跑
# `re_search_mem`、打印捕获组的**字节跨度**。判据这一侧用 CPython `re.search` 算同一批，
# 把字符下标换成字节下标逐行比。
#
# **为什么这是"对"的判据而不只是"一致"的判据**：CPython 的 `re` 是这里的**外部神谕**
# （docs/189 §3 的第三型）。两个实现自己写的引擎互相比只能证明"两边一样错"；拿一个
# 独立实现当对照物，才量得到"这份引擎算得对不对"。
#
# **语料为什么这么长**：引擎的语法面是组合爆炸的 —— 每加一个构造（惰性、后顾、反向引用），
# 只测它自己那一格会漏掉"它跟别的构造放一起"的情形。所以除了特性电池，还拿
# `loment_diag.RULES` 的**全部 239 条真实模式** × 编译器能说的**真实诊断模板**过一遍。
#
# 运行: python tools/loment_regex_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DRIVER = "loment/tools/lomrxcheck.lomt"
ENGINE = "lompi/store/std/0.1.0/regex.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具

_exe: Path | None = None


def _driver() -> Path:
    """用**自举链**（种子 -> stage1 -> IR -> lomelf）编一次驱动器，全测复用。

    这条链本身就是判据的一半：引擎与驱动器都得能被**自举编译器**吃下去 —— 参考实现能编
    不等于装好的工具链能编（`docs/158` §5）。
    """
    global _exe
    if _exe is not None:
        return _exe
    import loment_dist  # noqa: E402
    name = "lomrxcheck.exe" if sys.platform == "win32" else "lomrxcheck.elf"
    exe = ROOT / "loment" / "build" / "dist" / name
    raw = loment_dist.build_host_tool(DRIVER)
    exe.parent.mkdir(parents=True, exist_ok=True)
    tmp = exe.with_name(exe.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, exe)
    if sys.platform != "win32":
        exe.chmod(0o755)
    _exe = exe
    return exe


def _msg_template(node):
    """把一处 `errs.append(...)` 的实参摊成一条模板；摊不出来就 None。

    与 `loment_err_test._msg_template` 同一份口径（`+` 拆到底、条件表达式取 body 那一支）。
    这里不 import 那个模块：两边是**独立的判据**，一个判据的夹具函数改了不该悄悄改另一个。
    """
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "1" for v in node.values)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _msg_template(node.left), _msg_template(node.right)
        return None if a is None or b is None else a + b
    if isinstance(node, ast.IfExp):
        return _msg_template(node.body)
    return None


def _messages() -> list[str]:
    """编译器**能说的**消息（模板上界）+ 一组手写的真实形状。"""
    src = (ROOT / "tools" / "lomentc.py").read_text(encoding="utf-8")

    def is_sink(call: ast.Call) -> bool:
        f = call.func
        if isinstance(f, ast.Attribute) and f.attr == "append" and isinstance(f.value, ast.Name):
            return "err" in f.value.id.lower()
        return isinstance(f, ast.Name) and "LomError" in f.id

    out: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and node.args and is_sink(node):
            t = _msg_template(node.args[0])
            if not t:
                continue
            t = re.sub(r"^\{[^}]*\}:\s*", "", t)
            if t not in seen:
                seen.add(t)
                out.append(t)
    out += [
        "使用未声明的变量 leftover",
        "未定义的函数 leftove_count（跨模块调用需要 pub）",
        "结构体 Job 缺字段 weight",
        "类型不匹配：需要 u32，得到 bool",
        "调用未定义的函数 foo",
        "变量名，未声明",
        "名字导入找不到模块 dep",
        "这个后端还没实现",
        "native 后端暂不支持这条语句",
        "重复定义：fn f",
        "12: 使用未声明的变量 z",
        "44:33: 期望 `;`，得到 `}`",
        "类型 u32 与 i64 不匹配",
        "实参个数不符：需要 2 个，得到 3 个",
        "循环导入 a -> b -> a",
        "这里没有 deps 目录",
    ]
    assert len(out) > 100, f"只抽到 {len(out)} 条消息 —— 模板提取跟编译器脱节了"
    return out


#: 特性电池：每一条都是一种**构造的边界**（正中、越界、空匹配、Unicode）。
BATTERY: list[tuple[str, str, int]] = [
    ("abc", "abc", 0), ("abc", "xabcx", 0), ("^abc$", "abc", 0), ("a.c", "abc", 0),
    ("a.c", "a\nc", 0), ("(?s)a.c", "a\nc", 0),
    ("a*c", "aaac", 0), ("a+c", "c", 0), ("ab?c", "ac", 0),
    ("a+?c", "aaac", 0), ("a{2,3}", "aaa", 0), ("a{2,}", "aaaa", 0),
    ("a{2,3}?$", "aaaa", 0), ("a{3}", "aa", 0), ("a{,3}", "a{,3}", 0),
    ("[a-c]+", "abcxyz", 0), ("[^a-c]+", "abcxyz", 0), ("[0-9]{2,4}", "a12345", 0),
    ("[]a]+", "]a]", 0), ("[a-]+", "a-b", 0), ("[-a]+", "-a-", 0),
    (r"\d+", "a123", 0), (r"\D+", "123abc", 0), (r"\s+", "a \t b", 0),
    (r"\S+", " a b ", 0), (r"\w+", "a_1-", 0), (r"\W+", "a_1-", 0),
    (r"a\tb", "a\tb", 0), (r"\x41+", "AAA", 0), (r"中+", "中中", 0),
    (r"\bcat\b", "a cat here", 0), (r"\bcat\b", "scatter", 0), (r"\Bcat", "scatter", 0),
    ("foo(?=bar)", "foobarbaz", 0), ("foo(?!bar)", "foobaz", 0),
    ("(?<=foo)bar", "xfoobar", 0), ("(?<!foo)bar", "xbazbar", 0),
    ("(?<!foo)bar", "xfoobar", 0), ("(?=a+$)", "aaa", 0),
    (r"(a+)(b+)\1", "aabbaa", 0), (r"(a+)(b+)\1", "aabbaa", 1),
    (r"(a+)(b+)\1", "aabbaa", 2), (r"(a)(b)?\1", "aa", 0),
    (r"(ab|cd)+", "abcdab", 0), (r"a(b|c)*d", "abcbcd", 0),
    (r"(?:ab)+", "ababx", 0), (r"(?i)abc", "xABCx", 0),
    (r"(?m)^b$", "a\nb\nc", 0), (r"\Aabc", "abcx", 0), (r"abc\Z", "abc", 0),
    ("^", "abc", 0), ("$", "abc", 0), ("", "abc", 0),
    ("类型 .+ 不匹配", "类型 u32 不匹配", 0),
    ("变量[^，]+未声明", "变量名，未声明", 0),
    ("变量[^，]+未声明", "变量名很长的东西未声明", 0),
    ("名字导入找不到模块|循环导入", "加载时：循环导入 a -> b", 0),
    ("", "", 0), ("a|", "", 0), ("(|a)", "b", 0),
]


def _expected(pat: str, text: str, cap: int) -> str:
    """CPython `re.search` 的答案 -> 与驱动器同一形状（字节下标）。"""
    try:
        m = re.search(pat, text)
    except re.error:
        return "-1"
    if m is None or cap > m.re.groups:
        return "-1"
    try:
        s, e = m.span(cap)
    except IndexError:
        return "-1"
    if s < 0 or e < 0:
        return "-1"
    bs = len(text[:s].encode("utf-8"))
    be = len(text[:e].encode("utf-8"))
    return f"{bs}:{be}"


def _witness(pat: str) -> str | None:
    """用 CPython 自己的正则解析树生成一条**应该命中**的例子。

    语料不能只有真实消息：它们大多数不命中任何规则，对拍会退化成"两边都说 no"。
    每条规则配一条见证串，命中的那一半才立得住 —— 而且它顺带证明模式本身不是死的
    （一条谁都匹配不上的规则，等于没有）。
    """
    try:
        from re import _parser as sre
    except ImportError:  # pragma: no cover
        import sre_parse as sre
    try:
        tree = sre.parse(pat)
    except Exception:
        return None

    def cat(av):
        if av == sre.CATEGORY_DIGIT:
            return "1"
        if av == sre.CATEGORY_SPACE:
            return " "
        if av == sre.CATEGORY_NOT_DIGIT:
            return "x"
        if av == sre.CATEGORY_NOT_SPACE:
            return "x"
        if av == sre.CATEGORY_NOT_WORD:
            return "-"
        return "x"

    def cls(items):
        neg = bool(items) and items[0][0] is sre.NEGATE

        def hit(ch):
            for op, av in items:
                if op is sre.LITERAL and av == ord(ch):
                    return True
                if op is sre.RANGE and av[0] <= ord(ch) <= av[1]:
                    return True
                if op is sre.CATEGORY:
                    return True
            return False

        if neg:
            for ch in "a0 _-中":
                if not hit(ch):
                    return ch
            return "a"
        for op, av in items:
            if op is sre.LITERAL:
                return chr(av)
            if op is sre.RANGE:
                return chr(av[0])
            if op is sre.CATEGORY:
                return cat(av)
        return "x"

    def emit(seq):
        out = []
        for op, av in seq:
            if op is sre.LITERAL:
                out.append(chr(av))
            elif op is sre.NOT_LITERAL:
                out.append("x" if av != ord("x") else "y")
            elif op is sre.ANY:
                out.append("x")
            elif op is sre.IN:
                out.append(cls(av))
            elif op is sre.CATEGORY:
                out.append(cat(av))
            elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT):
                lo, hi, sub = av
                if hi == 0:
                    n = 0
                else:
                    n = lo if lo > 0 else 1
                out.append(emit(sub) * n)
            elif op is sre.SUBPATTERN:
                out.append(emit(av[-1]))
            elif op is sre.BRANCH:
                out.append(emit(av[1][0]))
            elif op is sre.AT:
                pass
            elif op is sre.ASSERT:
                d, sub = av
                if d >= 0:
                    out.append(emit(sub))
            elif op is sre.GROUPREF:
                out.append("x")
        return "".join(out)

    w = emit(tree)
    return w if re.search(pat, w) else None


def _corpus() -> list[dict]:
    cases = [{"pat": p, "text": t, "cap": g} for p, t, g in BATTERY]
    import loment_diag  # noqa: E402
    pats = [p for _c, p, _t, _h in loment_diag.RULES]
    dead: list[str] = []
    for pat in pats:
        w = _witness(pat)
        if w is None:
            dead.append(pat)
        else:
            cases.append({"pat": pat, "text": w})
    # `(?!)` 是**兜底码 E0999 的规则**：它按设计什么都匹配不上（docs/182 §5.1）。
    assert not [p for p in dead if p != "(?!)"], (
        f"这些规则生成不出见证串（模式死了？）: {[p for p in dead if p != '(?!)'][:5]}")
    msgs = _messages()
    # 交叉积封顶：239 条模式 × 每条消息已经够把"规则真正会遇到的形状"铺开；
    # 再多只是让墙钟变长，判据的分辨率不涨。
    LIMIT = 14000
    i = 0
    while len(cases) < LIMIT and i < len(msgs):
        for pat in pats:
            if len(cases) >= LIMIT:
                break
            cases.append({"pat": pat, "text": msgs[i]})
        i += 1
    assert i == len(msgs) or len(cases) >= LIMIT
    return cases


def _run(cases: list[dict]) -> list[str]:
    td = Path(tempfile.mkdtemp(prefix="loment-rx-"))
    src = td / "cases.jsonl"
    with src.open("w", encoding="utf-8", newline="\n") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    r = subprocess.run([str(_driver()), str(src)], capture_output=True, shell=False,
                       timeout=600)
    out = r.stdout.decode("utf-8", "replace").splitlines()
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    assert len(out) == len(cases), (
        f"驱动器输出 {len(out)} 行，输入 {len(cases)} 条 —— 有行没被解析出来"
        f"（stderr: {r.stderr[-200:]!r}）")
    return out


# ---------------------------------------------------------------- 判据

@test
def test_the_loment_engine_agrees_with_cpython_re():
    """**每一条语料**上，Loment 引擎的捕获跨度与 CPython `re` 逐字节相同。

    不是"大致相同"：`-1` 与 `s:e` 是两种判决，跨度差一个字节就是差一个字节。
    """
    cases = _corpus()
    got = _run(cases)
    bad: list[str] = []
    for c, g in zip(cases, got):
        want = _expected(c["pat"], c["text"], c.get("cap", 0))
        if g != want:
            bad.append(f"pat={c['pat']!r} text={c['text']!r} cap={c.get('cap', 0)}: "
                       f"loment={g!r} python={want!r}")
            if len(bad) >= 8:
                break
    assert not bad, "引擎与 CPython re 有分歧:\n  " + "\n  ".join(bad)
    hits = sum(1 for g in got if g != "-1")
    print(f"      {len(cases)} 条语料逐字节一致（{hits} 条命中 / {len(cases) - hits} 条不命中）")


@test
def test_the_corpus_is_not_vacuous():
    """语料里**两种判决都要有** —— 全是 `-1` 的对拍证明不了任何事。

    这是"判据自己别空转"的那一条：模式表或消息表某天坏了（抽不到模板、模式全不匹配），
    上一条会以"全 -1 对全 -1"的方式**继续绿**。
    """
    cases = _corpus()
    got = _run(cases)
    hits = sum(1 for g in got if g != "-1")
    assert hits >= 200, f"只有 {hits} 条命中 —— 语料退化了（模式/消息抽不上来了？）"
    assert len(got) - hits >= 200, f"只有 {len(got) - hits} 条不命中 —— 另一半退化了"
    uniq = {g for g in got if g != "-1"}
    assert len(uniq) >= 20, f"命中的跨度只有 {len(uniq)} 种 —— 形状太单"
    print(f"      {hits} 命中 / {len(got) - hits} 不命中；命中的跨度 {len(uniq)} 种")


@test
def test_the_engine_self_checks_pass():
    """引擎自己的 `test_regex_all` 也得过 —— 双层：对拍 + 自带断言。"""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment.py"),
                        "test", ENGINE],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert "PASS test_regex_all" in r.stdout and "1/1 PASS" in r.stdout, (
        f"引擎自检没过: {r.stdout[-400:]} {r.stderr[-400:]}")
    print("      test_regex_all 过（字面量/类/量词/惰性/前后瞻/捕获/UTF-8/开关/边界）")


def main() -> int:
    print(f"loment_regex_test: {len(TESTS)} 条\n")
    return loment_suite.run(TESTS, "loment_regex_test")


if __name__ == "__main__":
    sys.exit(main())
