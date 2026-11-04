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


def _l0_refusal(f, toks: list, s: dict, params: set) -> "str | None":
    """`L0` **为什么没收它**（`None` = 收了）。按 `_l0_promotable` 那几道闸的**原顺序**问。

    **这一格比 `L3` 的理由更常用**：一块没进 `L0` 却进了 `L1`（定活）的程序，用户看到的
    是"它没被提升到栈" —— 而**为什么**只有这句话说得出来。
    （实测：`let p: ptr = alloc(n);` 尺寸不是字面量 ⇒ L0 拒、**L1 照样接住**。）
    """
    name = s["name"]
    if name in params:
        return "param"
    lit = _size_literal_is_int(toks, f, name)
    if lit is False:
        return "size-not-literal"
    if lit == 0:
        return "size-zero"
    ok, ndecl, _last = lomentc._ptr_only_uses(toks, f.tok_at, f.tok_end, name,
                                              _decl_tok(toks, f, name))
    if not ok:
        return "escape"
    if ndecl != 1:
        return "multi-decl"
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


def _decl_tok(toks: list, f, name: str) -> int:
    """`name` 那条声明的 token 下标（`_ptr_only_uses` 要它来分辨"声明处"与"别处"）。"""
    for i in range(f.tok_at, min(f.tok_end, len(toks))):
        if toks[i].kind == "ident" and toks[i].val == name \
                and i > 0 and toks[i - 1].val == "let":
            return i
    return -1


def _size_literal_is_int(toks: list, f, name: str):
    """`let name: ptr = alloc(<num>);` 里那一格是不是整数字面量。

    回 `True`（是且 > 0）/ `False`（不是字面量）/ `0`（字面量但是 0）/ `None`（找不到那条声明）。
    与 `_l0_promotable` 的形状判定**同一条**：`let NAME : ptr = alloc ( <number> ) ;`。
    """
    i = _decl_tok(toks, f, name)
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


def sites(mod) -> list[dict]:
    """逐站点：`{line, rung, reason, name}` —— **按源码行序**。

    `reason` 在 `L0`/`L1`/`L2` 上恒为 `None`（那些是**成功**的分层），在 `L3` 上是
    `REASONS` 里的一条键。
    """
    lomentc._gc_alpha_pass(mod, [])
    toks = mod.toks
    shape = _shape_sites(mod)
    out: list[dict] = []
    for s in shape:
        f = s["fn"]                     # **归属来自走语句时的那一层**，不按名字反查：
                                        # 不同函数可以有同名局部（实测：一对只差一行的
                                        # 程序里 `eat` 与 `f` 都有 `p`，反查会认错函数）。
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


def report(path: Path) -> tuple[list[dict], dict]:
    mod = lomentc.load(path)
    rows = sites(mod)
    return rows, histogram(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐站点说出它为什么落在那一层（docs/225 §4.③）")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.file)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    rows, h = report(p)
    if a.json:
        print(json.dumps({"sites": rows, "ladder": h}, ensure_ascii=False, indent=2))
        return 0
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
