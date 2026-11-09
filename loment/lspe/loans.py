"""E10 借用检查器的分析核心（`docs/317`）：loan 是一等对象。

口径与 LSPE 其余引擎一致：吃**同一串 token**，规则逐条同形，输出确定。
这一版落 A0/A1 的模型，不改任何编译判决，也不进 Potato 台账（那一步要与自举侧
同步计数，单独落地）。

## 模型

* **place**：借用的来源（`&x` 的 `x`，`&mut s.f` 的 `s`）。
* **loan**：一次借用 —— `{id, place, kind ∈ {shared, mut}, create, kill, uses}`。
* **kill**：A1 起取**最后一次使用**（不是块末）；没有使用就是 create 自己。
  这正是 v1「借用活到函数末尾」与 E10 的差别：
  `if *cur > 0 { return *cur; } insert(&mut m)` 里的 `L1` 在 insert 之前已死。
* **冲突**：同一 place、区间 `[create, kill)` 相交、且至少一方是 `mut`。
* **悬垂**：`return` 用到一个借自**局部**（同一单元里 `let` 出来的）的 loan。

## 明确不做（与 `docs/317` §4.7 一致）

* 字段级（视图）借用：`&s.a` 与 `&mut s.b` 仍按同一 place 处理。
* 重借用 / 两阶段借用：`&mut *r` 记在 `r` 上，不与原 loan 合并。
* 跨函数摘要：`get(m, k)` 返回的借用不产生 loan（那是 A4）。
"""

from __future__ import annotations

#: 站点 id 的内容寻址与普查同一套（`census.sid`）。**不 import census 模块**：
#: 包里 `census` 这个名字被函数覆盖了（`lspe/__init__.py`），从包导入会拿到函数。
def _fnv1a32(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


#: 判定"这是类型位置的 `&`"时，前面的 token。词法器把 `->` 拆成 `-` `>`，
#: 所以返回箭头要单独认（见 `_type_amps`）。
_TYPE_LEAD = (":",)


def _is(t, i: int, kind: str, val: str | None = None) -> bool:
    if i < 0 or i >= len(t):
        return False
    tok = t[i]
    if tok.kind != kind:
        return False
    return val is None or tok.val == val


def _p(t, i: int, val: str) -> bool:
    return _is(t, i, "punct", val)


def _ident(t, i: int) -> bool:
    return _is(t, i, "ident")


def _brace_pairs(t: list) -> list[tuple[int, int]]:
    st: list[int] = []
    out: list[tuple[int, int]] = []
    for i, tok in enumerate(t):
        if tok.kind == "punct" and tok.val == "{":
            st.append(i)
        elif tok.kind == "punct" and tok.val == "}" and st:
            out.append((st.pop(), i))
    return out


def _enclosing(pairs: list[tuple[int, int]], i: int) -> tuple[int, int] | None:
    best = None
    for o, c in pairs:
        if o < i < c and (best is None or o > best[0]):
            best = (o, c)
    return best


def _signature_spans(t: list) -> list[tuple[int, int]]:
    """`fn` 的形参括号（`&` 在里面是类型，不是借用表达式）。"""
    out = []
    n = len(t)
    for i, tok in enumerate(t):
        if not (tok.kind == "ident" and tok.val == "fn"):
            continue
        j = i + 1
        while j < n and not _p(t, j, "("):
            j += 1
        if j >= n:
            continue
        depth = 0
        k = j
        while k < n:
            if _p(t, k, "("):
                depth += 1
            elif _p(t, k, ")"):
                depth -= 1
                if depth == 0:
                    out.append((j, k))
                    break
            k += 1
    return out


def _type_amps(t: list) -> set[int]:
    """类型位置里的 `&`：`: &T` / `-> &T`（以及它们的 `&mut T`）。"""
    out: set[int] = set()
    n = len(t)
    for i, tok in enumerate(t):
        if tok.kind != "punct":
            continue
        if tok.val in _TYPE_LEAD:
            j = i + 1
        elif tok.val == "-" and _p(t, i + 1, ">"):
            j = i + 2            # `-> T`：返回类型
        else:
            continue
        depth = 0
        while j < n:
            if _p(t, j, "(") or _p(t, j, "[") or _p(t, j, "<"):
                depth += 1
            elif _p(t, j, ")") or _p(t, j, "]") or _p(t, j, ">"):
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and (t[j].kind == "punct"
                                 and t[j].val in (",", "=", ";", "{", "}")):
                break
            if depth == 0 and _p(t, j, "&"):
                out.add(j)
            j += 1
    return out


def _statement_end(t: list, i: int) -> int:
    n = len(t)
    j = i
    depth = 0
    while j < n:
        if _p(t, j, "(") or _p(t, j, "["):
            depth += 1
        elif _p(t, j, ")") or _p(t, j, "]"):
            if depth > 0:
                depth -= 1
        elif depth == 0 and (_p(t, j, ";") or t[j].kind == "eof"):
            return j
        elif depth == 0 and _p(t, j, "}"):
            return j
        j += 1
    return n


def _binding(t: list, amp: int, pairs: list[tuple[int, int]]) -> tuple[str, int] | None:
    """`let NAME [: T] = &…` → (NAME, `let` 的下标)。

    从借用往回走到最近的 `let`（同一语句内，`;`/`{` 是边界）。类型标注里还有
    `&T`，所以不能只看 `=` 前两个 token —— `let cur: &u32 = &m…` 的 `=` 前面是 `u32`。
    """
    j = amp - 1
    depth = 0
    while j >= 0:
        if _p(t, j, ")") or _p(t, j, "]"):
            depth += 1
        elif _p(t, j, "(") or _p(t, j, "["):
            if depth > 0:
                depth -= 1
        elif depth == 0 and (_p(t, j, ";") or _p(t, j, "{")):
            return None
        elif depth == 0 and _is(t, j, "ident", "let") and _ident(t, j + 1):
            return t[j + 1].val, j
        j -= 1
    return None


def _locals(t: list, pairs: list[tuple[int, int]]) -> set[str]:
    """`let` 出来的局部名 —— 悬垂判决只关心"来源是局部"。"""
    out: set[str] = set()
    for i, tok in enumerate(t):
        if tok.kind == "ident" and tok.val == "let" and _ident(t, i + 1):
            out.add(t[i + 1].val)
    return out


def _uses(t: list, name: str, lo: int, hi: int) -> list[int]:
    out = []
    for i in range(lo, min(hi, len(t))):
        if not (_ident(t, i) and t[i].val == name):
            continue
        # 字段名（`r.field` 的 field）不是对 `r` 的使用。
        if i > 0 and _p(t, i - 1, "."):
            continue
        out.append(i)
    return out


def count(toks: list) -> dict:
    """台账里的 loan 计数（A0b）：只数**借用表达式**，不建图。

    规则 R，两套实现逐条同形（自举侧 `lspe_loans`）：

    * 只看 `&`；前一个 token 是 `:` 或 `->` 的是**类型位置**，不算；
    * `&mut` 看下一个 token 是不是 `mut`；
    * 再下一个必须是标识符，且不是 `self`（`&self` 是方法签名，不是借用表达式）。

    只数**创建**：uses / kills / 冲突仍在 `analyze()` 里，接台账是 A0b 之后的事。
    """
    total = shared = mut = 0
    for i, tok in enumerate(toks):
        if not (tok.kind == "punct" and tok.val == "&"):
            continue
        type_pos = False
        if i > 0:
            prev = toks[i - 1]
            if prev.kind == "punct" and prev.val == ":":
                type_pos = True
            if (prev.kind == "punct" and prev.val == ">"
                    and i > 1 and toks[i - 2].kind == "punct"
                    and toks[i - 2].val == "-"):
                type_pos = True
        if type_pos:
            continue
        j = i + 1
        is_mut = False
        if _is(toks, j, "ident", "mut"):
            is_mut = True
            j += 1
        if _ident(toks, j) and toks[j].val != "self":
            total += 1
            if is_mut:
                mut += 1
            else:
                shared += 1
    return {"total": total, "shared": shared, "mut": mut}


def analyze(toks: list, unit: str = "m") -> dict:
    """返回 `{unit, loans, sites, conflicts, findings, questions, summary}`。"""
    fnv = _fnv1a32
    pairs = _brace_pairs(toks)
    sig = _signature_spans(toks)
    type_amps = _type_amps(toks)

    def in_sig(i: int) -> bool:
        return any(o <= i <= c for o, c in sig)

    def sid(kind: str, key: str, seen: dict[str, int]) -> str:
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        return f"{unit}:{kind}:{fnv(key + '#' + str(occ)):08x}"

    seen: dict[str, int] = {}
    loans: list[dict] = []
    for i, tok in enumerate(toks):
        if not (tok.kind == "punct" and tok.val == "&"):
            continue
        if i in type_amps or in_sig(i):
            continue
        mut = _is(toks, i + 1, "ident", "mut")
        base = i + 2 if mut else i + 1
        if not _ident(toks, base):
            continue        # `&&` / `&[T]`（切片字面量）等等：A1 不造 loan
        place = toks[base].val
        scope = _enclosing(pairs, i)
        bind = _binding(toks, i, pairs)
        var = bind[0] if bind else None
        if var is not None:
            lo = i + 1
            hi = scope[1] if scope else _statement_end(toks, i)
            uses = _uses(toks, var, lo, hi)
            kill = uses[-1] if uses else i
            span = (bind[1], scope[1] if scope else kill)
        else:
            end = _statement_end(toks, i)
            uses = []
            kill = end
            span = (i, end)
        loans.append({
            "id": sid("loan", f"{place}:{'mut' if mut else 'shared'}:{i}", seen),
            "place": place, "kind": "mut" if mut else "shared",
            "var": var, "create": i, "kill": kill, "uses": uses,
            "line": tok.line, "span": [tok.line, 0, toks[base].line, 0],
            "scope": list(scope) if scope else None,
            "span_idx": list(span),
        })

    locals_ = _locals(toks, pairs)
    findings: list[dict] = []

    # ---- 冲突：同 place、区间相交、至少一方 mut ----
    conflicts = []
    for a in loans:
        for b in loans:
            if a["id"] >= b["id"] or a["place"] != b["place"]:
                continue
            if "mut" not in (a["kind"], b["kind"]):
                continue
            # 区间是 [create, kill+1)：使用点本身仍算活着。
            lo = max(a["create"], b["create"])
            hi = min(a["kill"] + 1, b["kill"] + 1)
            if lo < hi:
                conflicts.append({"a": a["id"], "b": b["id"],
                                  "place": a["place"], "at": lo,
                                  "line": toks[lo].line})
    conflicts.sort(key=lambda c: (c["at"], c["a"], c["b"]))

    # ---- 悬垂：return 用到借自局部的 loan ----
    for ln in loans:
        if ln["var"] is None or ln["place"] not in locals_:
            continue
        for u in ln["uses"]:
            j = u - 1
            while j >= 0 and not (_p(toks, j, ";") or _p(toks, j, "{")
                                  or _p(toks, j, "}")):
                j -= 1
            if _is(toks, j + 1, "ident", "return"):
                findings.append({
                    "kind": "dangling_return", "loan": ln["id"],
                    "place": ln["place"], "line": toks[u].line,
                    "message": f"`return` 借用了局部 `{ln['place']}` 的引用"})
                break

    # ---- free 时仍被借 ----
    for i, tok in enumerate(toks):
        if not (tok.kind == "ident" and tok.val == "free" and _p(toks, i + 1, "(")
                and _ident(toks, i + 2) and _p(toks, i + 3, ")")):
            continue
        name = toks[i + 2].val
        live = [ln for ln in loans
                if ln["place"] == name and ln["create"] < i <= ln["kill"]]
        if live:
            findings.append({
                "kind": "free_while_borrowed", "line": tok.line,
                "place": name, "loans": [ln["id"] for ln in live],
                "message": f"`free({name})` 时仍有 {len(live)} 笔借用活着"})

    findings.sort(key=lambda f: (f["line"], f["kind"]))

    # ---- LSPE 形状的站点（报告面；接台账是下一步） ----
    sites: list[dict] = []
    for ln in loans:
        sites.append({
            "id": ln["id"], "kind": "loan", "claim": "allow",
            "obligation": "Exclusive" if ln["kind"] == "mut" else "NotDangling",
            "evidence": "Syntactic", "source": f"loan:{ln['place']}",
            "line": ln["line"], "span": ln["span"], "proved": True,
            "place": ln["place"], "loan_kind": ln["kind"]})
        for u in ln["uses"]:
            sites.append({
                "id": f"{ln['id']}:use:{u}", "kind": "loan_use",
                "claim": "allow",
                "obligation": "Exclusive" if ln["kind"] == "mut" else "NotDangling",
                "evidence": "LocalFlow", "source": ln["id"],
                "line": toks[u].line, "span": [toks[u].line, 0, toks[u].line, 0],
                "proved": True, "loan": ln["id"]})
        sites.append({
            "id": f"{ln['id']}:kill", "kind": "loan_kill", "claim": "allow",
            "obligation": "Alive", "evidence": "LocalFlow", "source": "last-use",
            "line": toks[ln["kill"]].line,
            "span": [toks[ln["kill"]].line, 0, toks[ln["kill"]].line, 0],
            "proved": True, "loan": ln["id"]})
    for c in conflicts:
        sites.append({
            "id": sid("loan_conflict", f"{c['a']}|{c['b']}", seen),
            "kind": "loan_conflict", "claim": "deny",
            "obligation": "Exclusive", "evidence": "none", "source": None,
            "line": c["line"], "span": [c["line"], 0, c["line"], 0],
            "proved": False, "a": c["a"], "b": c["b"], "place": c["place"]})

    # ---- 反问器：证不出来的形状（Q015–Q017 的入口） ----
    questions = []
    for c in conflicts:
        questions.append({"code": "Q001", "site": c["a"],
                          "state": "refuted",
                          "claim": f"`{c['place']}` 上两笔借用在第 {c['line']} 行重叠"})
    for f in findings:
        if f["kind"] == "dangling_return":
            questions.append({"code": "Q001", "site": f["loan"],
                              "state": "refuted",
                              "claim": f"返回局部 `{f['place']}` 的借用"})

    loans.sort(key=lambda x: (x["create"], x["id"]))
    sites.sort(key=lambda s: s["id"])
    return {
        "unit": unit,
        "loans": loans,
        "sites": sites,
        "conflicts": conflicts,
        "findings": findings,
        "questions": questions,
        "summary": {
            "loans": len(loans),
            "shared": sum(1 for x in loans if x["kind"] == "shared"),
            "mut": sum(1 for x in loans if x["kind"] == "mut"),
            "conflicts": len(conflicts),
            "dangling": sum(1 for f in findings if f["kind"] == "dangling_return"),
            "free_while_borrowed": sum(1 for f in findings
                                       if f["kind"] == "free_while_borrowed"),
        },
    }


def text_lines(report: dict) -> list[str]:
    s = report["summary"]
    out = [f"[loans] {report['unit']}: {s['loans']} loan(s) "
           f"({s['shared']} shared / {s['mut']} mut), "
           f"{s['conflicts']} conflict(s), {s['dangling']} dangling"]
    for f in report["findings"]:
        out.append(f"LOAN[{f['kind']}] line {f['line']}: {f['message']}")
    for c in report["conflicts"]:
        out.append(f"LOAN[conflict] line {c['line']}: `{c['place']}` "
                   f"({c['a']} × {c['b']})")
    if not report["findings"] and not report["conflicts"]:
        out.append("LOAN[none] 没有冲突，也没有悬垂")
    return out
