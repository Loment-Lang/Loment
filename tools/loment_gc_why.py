#!/usr/bin/env python3
"""loment_gc_why.py — **逐站点说出"它为什么落到那一层"**（`docs/225` §4.③ 的第一片）。

## 它补的是哪一个洞

`gc_ladder`（`docs/210` §3）已经把"每一层领走几个 `alloc` 站点"写进了产物 —— 那是一个
**数**。但那个数**不告诉你为什么**：一个站点落在 `L3`，可能是五种完全不同的原因
（形状不合 / 尺寸不是字面量 / 名字外逃 / …），而**修法各不相同**。

`docs/214` §5 假设 2 的代价那一格写的就是这件事：「用户要写界与作用域；**曲线陡**」。
而 §7 证明了"自动改写"不可能 ⇒ **剩下的全部价值都在"把为什么说清楚"这一步上**。

## 它凭什么可信

它**不自己发明分类**：它复用 `lomentc` 的那几张表和同一条 rung 顺序（L0 → L2 → L1 → L3），
并且有一条**对账判据**（`loment_gc_why_test.py` 判据 A）钉着：

> 对语料里**每一份**模块，本工具数出来的四层直方图 **必须等于** `lomentc._count_gc_ladder()`
> 的四个数。

于是这个工具**不可能与编译器漂**：漂了就是红的。这与 `tools/loment_gc_surface.py`
（那把"证明面有多宽"的尺子）是同一种做法 —— **尺子自己也要有判据钉住**。

## 用法

    python tools/loment_gc_why.py FILE [--json]

**只读参考实现**（`tools/lomentc.py`）。自举侧要出这份报告得先有它自己的孪生 ——
那是下一格，见本文末尾的"没做的"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 四层梯的档名（顺序 = `_count_gc_ladder` 的分派顺序）。
RUNGS = ("L0", "L0.5", "L1", "L2", "L3")

#: `L3` 的**具名原因**。每一条都要能指到"用户该改哪儿"。
#: 键是稳定标识（进 `--json`），值是人话。
REASONS = {
    # `#1485`：这一档**根本没有分层**。只有 `choose gc_auto_alpha` 会填 L0/L1/L2；
    # 非 alpha 单元给一个 alpha 档的理由（"尺寸不是字面量"…）会把用户引到没用的改动上。
    "tier": "这一档没有分层（只有 `choose gc_auto_alpha` 才有 L0 / L1 / L2）",
    "shape": "形状不合 —— 只有语句级的 `let NAME: ptr = alloc(<字面量>);` 才分层",
    "param": "`NAME` 是形参（形参的缓冲不是这个函数分配的）",
    "size-not-literal": "尺寸不是字面量（L0 要求字面量，命名常量也不行）",
    "size-zero": "尺寸字面量为 0",
    "escape": "`NAME` 在别处出现过 —— 外逃 / 重新赋值 / 被遮蔽",
    "multi-decl": "`NAME` 被声明了不止一次",
    "no-epoch-no-l1": "既不在任何**开着纪元**的循环体里，也不在 L1 的定活名单里",
}


def _shape_sites(mod) -> list[dict]:
    """本模块里**形状合格**的 `alloc` 站点（语句级 `let NAME: ptr = alloc(…)`）。

    口径与 `_count_gc_ladder` 的 `walk` **逐条对齐**（那里只走语句列表，因为可分层站点
    只可能是语句级的 `let`）。
    """
    import dataclasses as _dc

    out: list[dict] = []

    def walk(ss: list, in_l2: bool, f) -> None:
        for s in ss:
            if (isinstance(s, lomentc.Let) and s.type == "ptr"
                    and isinstance(s.expr, lomentc.Call) and s.expr.name == "alloc"):
                out.append({"kind": "let", "line": s.line, "name": s.name,
                            "in_l2": in_l2, "let": s, "fn": f})
            if isinstance(s, lomentc.If):
                walk(s.then, in_l2, f)
                walk(s.otherwise, in_l2, f)
            elif isinstance(s, lomentc.While):
                walk(s.body, in_l2 or bool(s.l2), f)
            elif isinstance(s, lomentc.For):
                walk(s.body, in_l2 or bool(s.l2), f)
            elif isinstance(s, lomentc.Match):
                for _pat, body in s.arms:
                    walk(body, in_l2, f)

    for f in list(mod.funcs) + [g for im in mod.impls for g in im.funcs]:
        walk(f.body, False, f)
    return out


def _all_alloc_lines(mod) -> list[int]:
    """**全部** `alloc` 调用点（不看形状）—— 与 `_count_gc_ladder` 的 `reap` 同一个口径。

    形状不合的那些就是"数得出来、分不了层"的站点，它们正是最需要一句解释的。
    """
    import dataclasses as _dc

    lines: list[int] = []

    def reap(x) -> None:
        if isinstance(x, lomentc.Call) and x.name == "alloc":
            lines.append(getattr(x, "line", 0))
        if _dc.is_dataclass(x) and not isinstance(x, type):
            for fd in _dc.fields(x):
                if fd.name in ("line", "col", "off", "len"):
                    continue
                reap(getattr(x, fd.name))
        elif isinstance(x, (list, tuple)):
            for y in x:
                reap(y)

    reap(mod)
    return lines


def _n_lets(toks: list, f, name: str) -> int:
    """这个函数里 `let name` 出现了几次（`#1460`：遮蔽时两个站点都该说 multi-decl）。"""
    return sum(1 for i in range(f.tok_at, min(f.tok_end, len(toks)))
               if toks[i].kind == "ident" and toks[i].val == name
               and i > 0 and toks[i - 1].val == "let")


def _l0_refusal(f, toks: list, s: dict, params: set) -> "str | None":
    """`L0` **为什么没收它**（`None` = 收了）。按 `_l0_promotable` 那几道闸的**原顺序**问。

    **这一格比 `L3` 的理由更常用**：一块没进 `L0` 却进了 `L1`（定活）的程序，用户看到的
    是"它没被提升到栈" —— 而**为什么**只有这句话说得出来。
    （实测：`let p: ptr = alloc(n);` 尺寸不是字面量 ⇒ L0 拒、**L1 照样接住**。）
    """
    name = s["name"]
    if name in params:
        return "param"
    # `#1460`：声明次数要**自己数**。`_ptr_only_uses` 撞到第二个 `let` 就 `ok=False` 并
    # `break`，它的 `ndecl` 于是永远是 0/1 —— 把 `multi-decl` 那条理由变成了死码。
    if _n_lets(toks, f, name) != 1:
        return "multi-decl"
    lit = _size_literal_is_int(toks, f, name, s.get("line"))
    if lit is False:
        return "size-not-literal"
    if lit == 0:
        return "size-zero"
    ok, _ndecl, _last = lomentc._ptr_only_uses(toks, f.tok_at, f.tok_end, name,
                                               _decl_tok(toks, f, name, s.get("line")))
    if not ok:
        return "escape"
    return None


def _shape_reason(f, toks: list, s: dict, params: set, l0n: set, l1n: set) -> str:
    """形状合格的站点落在 `L3` 时，**是哪一条挡住的**。"""
    name = s["name"]
    if name in getattr(f, "l05_static", {}):
        return "l0_5"                     # L0 的证明结果，只是被函数级栈区接走了
    if name in l0n:
        return "l0"                       # 不该走到这里（调用方已排除）
    if s["in_l2"]:
        return "l2"
    if name in l1n:
        return "l1"
    # 到这儿 = L3：`L0` 与 `L1` 都没接住。按那几道闸的原顺序给出**第一条**挡住的。
    return _l0_refusal(f, toks, s, params) or "no-epoch-no-l1"


def _decl_tok(toks: list, f, name: str, line: int | None = None) -> int:
    """`name` 那条声明的 token 下标（`_ptr_only_uses` 要它来分辨"声明处"与"别处"）。

    `line` 给了就取**那一行的那条声明**（`#1460`）：同名遮蔽时每个站点各问各的，
    原先一律回**第一条** —— 第二个站点的"尺寸是不是字面量"会拿第一个 `let` 的尺寸去回答。
    """
    best, exact = -1, -1
    for i in range(f.tok_at, min(f.tok_end, len(toks))):
        if toks[i].kind == "ident" and toks[i].val == name \
                and i > 0 and toks[i - 1].val == "let":
            if line is None:
                return i
            if toks[i].line == line:
                exact = i
            if toks[i].line <= line:
                best = i
    return exact if exact >= 0 else best


def _size_literal_is_int(toks: list, f, name: str, line: int | None = None):
    """`let name: ptr = alloc(<num>);` 里那一格是不是整数字面量。

    回 `True`（是且 > 0）/ `False`（不是字面量）/ `0`（字面量但是 0）/ `None`（找不到那条声明）。
    与 `_l0_promotable` 的形状判定**同一条**：`let NAME : ptr = alloc ( <number> ) ;`。
    `line` 让同名遮蔽时问的是**这个站点自己的**那条声明（`#1460`）。
    """
    i = _decl_tok(toks, f, name, line)
    if i < 0:
        return None
    j = i + 1
    if not (j + 7 < len(toks) and toks[j].val == ":" and toks[j + 1].val == "ptr"
            and toks[j + 2].val == "=" and toks[j + 3].val == "alloc"
            and toks[j + 4].val == "("):
        return False
    if toks[j + 5].kind != "number":
        return False
    return lomentc._int_lit(toks[j + 5].val) & 0xFFFFFFFF or 0


def tier_of(mod) -> str:
    """这份单元自己声明的 GC 档（`choose gc_*` 的规范名，缺省 = `CORE_DEFAULTS`）。"""
    return getattr(mod, "chooses", {}).get("gc", lomentc.CORE_DEFAULTS["gc"])


ALPHA = "gc_auto_alpha"


def sites(mod) -> list[dict]:
    """逐站点：`{line, rung, reason, name}` —— **按源码行序**。

    `reason` 在 `L0`/`L1`/`L2` 上恒为 `None`（那些是**成功**的分层），在 `L3` 上是
    `REASONS` 里的一条键。

    **不跑分析**（`#1451`）：这一层只读 `mod` 上的分层表。原先在这里无条件调
    `_gc_alpha_pass(mod, [])` —— 既不尊重单元自己写的档，又会把调用方跑过的
    **带依赖**的分析表改写成**不带依赖**的一份（之后 `emit_*` 产出的就是另一个程序）。
    需要分层的调用方（`report` 与测试）自己先跑 `_gc_alpha_pass(mod, deps)`。
    """
    alpha = tier_of(mod) == ALPHA
    toks = mod.toks
    shape = _shape_sites(mod)
    out: list[dict] = []
    for s in shape:
        f = s["fn"]                     # **归属来自走语句时的那一层**，不按名字反查：
                                        # 不同函数可以有同名局部（实测：一对只差一行的
                                        # 程序里 `eat` 与 `f` 都有 `p`，反查会认错函数）。
        # `#1485`：非 alpha 档没有分层 —— 说"尺寸不是字面量"之类的话只会把人引去改
        # 一个不会改变结果的写法。这一档的答案只有一句：这一层不存在。
        if not alpha:
            out.append({"line": s["line"], "name": s["name"], "rung": "L3",
                        "reason": "tier", "l0_refused": None})
            continue
        params = {p.name for p in f.params}
        l0n = set(f.l0)
        l1n = _l1_names(f)
        r = _shape_reason(f, toks, s, params, l0n, l1n)
        rung = {"l0": "L0", "l0_5": "L0.5", "l2": "L2", "l1": "L1"}.get(r, "L3")
        out.append({"line": s["line"], "name": s["name"], "rung": rung,
                    "reason": None if rung != "L3" else r,
                    # 「L0 为什么没收」—— 每一行都给（不是 L3 才给）。一块落在 L1
                    # 而没上栈的程序，用户要看的正是这一句。
                    "l0_refused": None if rung in ("L0", "L0.5")
                    else _l0_refusal(f, toks, s, params)})
    # 形状不合的站点：全部 `alloc` 行号里减掉形状合格的那些（同一行可能出现两个，
    # 所以按**多重集**减，不按集合）。
    have = [s["line"] for s in shape]
    for ln in _all_alloc_lines(mod):
        if ln in have:
            have.remove(ln)
        else:
            out.append({"line": ln, "name": "", "rung": "L3", "reason": "shape",
                        "l0_refused": "shape"})
    out.sort(key=lambda x: (x["line"], x["rung"]))
    return out


def _l1_names(f) -> set:
    """本函数的 L1 名单 —— `f.l1` + 各 `Return.l1`（与 `_count_gc_ladder.ret_l1` 同一条）。"""
    out = set(f.l1)

    def rec(ss):
        for s in ss:
            if isinstance(s, lomentc.Return):
                out.update(s.l1)
            elif isinstance(s, lomentc.If):
                rec(s.then)
                rec(s.otherwise)
            elif isinstance(s, (lomentc.While, lomentc.For)):
                rec(s.body)
            elif isinstance(s, lomentc.Match):
                for _p, body in s.arms:
                    rec(body)

    rec(f.body)
    return out


def histogram(rows: list[dict]) -> dict:
    h = {"l0": 0, "l0_5": 0, "l1": 0, "l2": 0, "l3": 0}
    for r in rows:
        h[r["rung"].lower()] += 1
    h["total_sites"] = len(rows)
    return h


def report(path: Path) -> tuple[list[dict], dict, str]:
    """`(逐站点, 直方图, 档名)`。分析在这里跑 —— **带依赖**，且只在本单元真是 alpha 时跑。

    依赖要一起给（`#1451`）：`_gc_alpha_pass(mod, [])` 的 deref-only 安全表会少掉依赖里的
    函数，于是本可以上 L0 的站点被算成 L3 —— 那正是这把尺子要避免的那类错。
    """
    mod, deps = lomentc.load_unit(path, ROOT)
    tier = tier_of(mod)
    if tier == ALPHA:
        lomentc._gc_alpha_pass(mod, deps)
    rows = sites(mod)
    return rows, histogram(rows), tier


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐站点说出它为什么落在那一层（docs/225 §4.③）")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.file)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    # `#1463`：输入是**用户的参数** —— 路径写错、给了目录、文件不是 UTF-8，都要报一句
    # 并给一个具名退码。原先这些一路抛到 `pathlib` / `lomentc`，用户看到的是 Python 栈
    # （同类问题在别的 CLI 上已经修过一轮）；姊妹尺子 `loment_gc_surface.py` 也是这么做的。
    if not p.exists():
        print(f"[ERR] {a.file}: 文件不存在", file=sys.stderr)
        return 2
    if p.is_dir():
        print(f"[ERR] {a.file}: 这是一个目录，不是源文件", file=sys.stderr)
        return 2
    try:
        rows, h, tier = report(p)
    except (OSError, UnicodeDecodeError, ValueError, lomentc.LomError) as e:
        print(f"[ERR] {a.file}: 读不了这份源（{type(e).__name__}: {e}）", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"sites": rows, "ladder": h, "tier": tier},
                         ensure_ascii=False, indent=2))
        return 0
    if tier != ALPHA:
        # `#1485`：先把"这一档没有分层"说在前面 —— 否则下面那句"L3"看起来像是在
        # 建议用户改写法，而改写法在 `gc_manual` 下不会把任何一处挪出 L3。
        print(f"  这一档是 `{tier}` —— 分层（L0 / L1 / L2）只在 `choose gc_auto_alpha` "
              f"下存在；下面每一处都是 L3，换写法也不会改变这一点。")
    for r in rows:
        why = "" if r["reason"] is None else f"   ← {REASONS[r['reason']]}"
        nm = f" `{r['name']}`" if r["name"] else ""
        note = ""
        if r["rung"] not in ("L0", "L0.5") and r.get("l0_refused"):
            note = f"   （L0 没收：{REASONS[r['l0_refused']]}）"
        print(f"  {r['line']:>5}  {r['rung']}{nm}{why}{note}")
    print(f"\n  共 {h['total_sites']} 处："
          + " · ".join(f"{RUNGS[i]} {h[k]}"
                        for i, k in enumerate(("l0", "l0_5", "l1", "l2", "l3"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
