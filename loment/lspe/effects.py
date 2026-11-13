"""E11 代数效应与效应处理器（`docs/346`）：行、处理器、`resume` 的检查核心。

口径与 LSPE 其余引擎一致：吃**同一串 token**，规则逐条同形，输出确定。A0/A1 是
**报告面** —— 它不改变任何编译判决，也不进 Potato 台账（那一步要与自举侧同步计数，
单独落地，理由与 `docs/317` §9.5 同）。

## 模型

* **effect**：一组操作（`effect Log { fn write(v: u32) -> (); }`）。
* **row**：函数签名上的有限效应集合（`fn f() -> T effects Log, Net`）。
  `pub fn` 必须写（整程序的属性由程序声明）。私有函数的**传递**行按调用图推断，
  但直接 `perform` 仍必须被当场处理器销掉，或写进声明行 —— "开口要"不许靠推断蒙混。
* **perform**：`perform Log.write(x)` —— 向最近的处理器求助。
* **handle**：`handle Log { BODY } with { op(p) => { … } }` —— 深度处理器；
  `BODY` 里的 `Log` 被销掉，臂里的 perform 归**外层**处理器。
* **resume**：只出现在臂里；每个臂的每条路径调用 0 次（abort）或 1 次。
* **尾续**：臂里每个 `resume` 都是所在块的最后一条语句。尾续处理器不需要续延对象，
  这是 `no_runtime` 默认档能跑效应的原因。

## 明确不做（与 `docs/346` §0 一致）

* 不做多射续延的默认语义：`resume` 两次是反例（Q020）。
* 不做跨 `extern` 的效应：C 侧没有证据记录（R7）。
* 不做效应多态的函数指针、不做处理器作为一等值、不做跨单元摘要（A4）。
* 不做字段级/控制流精确的线性证明：`resume` 的路径形状只认"块尾"这一种。
"""

from __future__ import annotations


def _fnv1a32(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


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


#: 括号对：`_match` 只数同一种括号，不同类型不会互相干扰。
_CLOSE = {"{": "}", "(": ")", "[": "]"}


def _match(t: list, i: int) -> int | None:
    """`t[i]` 是开括号时返回配对的闭括号下标；否则 None。"""
    if i < 0 or i >= len(t) or t[i].kind != "punct":
        return None
    openv = t[i].val
    closev = _CLOSE.get(openv)
    if closev is None:
        return None
    depth = 0
    for j in range(i, len(t)):
        if t[j].kind != "punct":
            continue
        if t[j].val == openv:
            depth += 1
        elif t[j].val == closev:
            depth -= 1
            if depth == 0:
                return j
    return None


def _brace_pairs(t: list) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, tok in enumerate(t):
        if tok.kind != "punct":
            continue
        if tok.val == "{":
            stack.append(i)
        elif tok.val == "}" and stack:
            out.append((stack.pop(), i))
    return out


def _innermost(pairs: list[tuple[int, int]], i: int) -> int | None:
    best: int | None = None
    for o, c in pairs:
        if o < i < c and (best is None or o > best):
            best = o
    return best


def _enclosing(pairs: list[tuple[int, int]], i: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for o, c in pairs:
        if o <= i <= c and (best is None or o > best[0]):
            best = (o, c)
    return best


def _arity(t: list, open_i: int) -> tuple[int, int | None]:
    """形参个数（顶层逗号 + 1；空表 0）与右括号下标。"""
    close = _match(t, open_i)
    if close is None:
        return 0, None
    depth = 0
    commas = 0
    any_tok = False
    for j in range(open_i + 1, close):
        tok = t[j]
        if tok.kind == "punct" and tok.val in "([{<":
            depth += 1
        elif tok.kind == "punct" and tok.val in ")]}>":
            depth -= 1
        elif depth == 0 and tok.kind == "punct" and tok.val == ",":
            commas += 1
        if not (tok.kind == "punct" and tok.val == ","):
            any_tok = True
    return (0 if not any_tok else commas + 1), close


def _row(t: list, lo: int, hi: int) -> list[str] | None:
    """`[lo, hi)` 里 `effects A, B` 的 A/B；没有 `effects` 返回 None。"""
    depth = 0
    eff = None
    for j in range(lo, hi):
        tok = t[j]
        if tok.kind == "punct" and tok.val in "([<":
            depth += 1
        elif tok.kind == "punct" and tok.val in ")]>":
            depth = max(0, depth - 1)
        elif depth == 0 and tok.kind == "ident" and tok.val == "effects":
            eff = j
    if eff is None:
        return None
    names: list[str] = []
    for j in range(eff + 1, hi):
        if _ident(t, j) and t[j].val not in names:
            names.append(t[j].val)
    return names


def _effects(t: list) -> dict[str, dict]:
    """效应声明：`{名字: {ops: {op: {arity, requires, line}}, line, span}}`。"""
    out: dict[str, dict] = {}
    n = len(t)
    i = 0
    while i < n:
        if not (_ident(t, i) and t[i].val == "effect"
                and _ident(t, i + 1) and _p(t, i + 2, "{")):
            i += 1
            continue
        close = _match(t, i + 2)
        if close is None:
            i += 1
            continue
        name = t[i + 1].val
        ops: dict[str, dict] = {}
        j = i + 3
        while j < close:
            if _ident(t, j) and t[j].val == "fn" and _ident(t, j + 1):
                op = t[j + 1].val
                k = j + 2
                while k < close and not _p(t, k, "("):
                    k += 1
                arity, pclose = _arity(t, k) if k < close else (0, None)
                end = (pclose + 1) if pclose is not None else k
                requires = None
                while end < close and not _p(t, end, ";"):
                    if (_ident(t, end) and t[end].val == "requires"
                            and _ident(t, end + 1)):
                        requires = t[end + 1].val
                    end += 1
                ops[op] = {"arity": arity, "requires": requires,
                           "line": int(t[j].line)}
                j = end + 1
                continue
            j += 1
        out[name] = {"ops": ops, "line": int(t[i].line),
                     "span": [int(t[i].line), int(t[i].col),
                              int(t[close].line), int(t[close].col)]}
        i = close + 1
    return out


def _in_spans(spans: list[tuple[int, int]], i: int) -> bool:
    return any(o <= i <= c for o, c in spans)


def _functions(t: list, effect_spans: list[tuple[int, int]]) -> list[dict]:
    """函数声明：名字、形参数、声明行、体、`extern` / `pub`。"""
    out: list[dict] = []
    n = len(t)
    for i, tok in enumerate(t):
        if not (tok.kind == "ident" and tok.val == "fn"):
            continue
        if not _ident(t, i + 1) or _in_spans(effect_spans, i):
            continue
        name = t[i + 1].val
        k = i + 2
        while k < n and not _p(t, k, "("):
            k += 1
        if k >= n:
            continue
        arity, pclose = _arity(t, k)
        if pclose is None:
            continue
        depth = 0
        end = None
        for j in range(pclose + 1, n):
            v = t[j].val if t[j].kind == "punct" else ""
            if v and v in "([<":
                depth += 1
            elif v and v in ")]>":
                depth = max(0, depth - 1)
            elif depth == 0 and v in ("{", ";"):
                end = j
                break
        if end is None:
            continue
        body = None
        if _p(t, end, "{"):
            c = _match(t, end)
            if c is None:
                continue
            body = (end, c)
        prev = t[i - 1].val if i > 0 and _ident(t, i - 1) else ""
        pub = prev == "pub" or (
            i > 1 and _ident(t, i - 2) and t[i - 2].val == "pub")
        out.append({
            "name": name, "arity": arity, "line": int(tok.line),
            "decl": _row(t, pclose + 1, end),
            "body": list(body) if body else None,
            "extern": prev == "extern",
            "pub": pub,
            "_sig": [k, end],
        })
    return out


def _arms(t: list, open_i: int, close: int) -> list[dict]:
    """处理器臂：`op(p) => { … }`。"""
    out: list[dict] = []
    j = open_i + 1
    while j < close:
        if _ident(t, j) and _p(t, j + 1, "("):
            arity, pclose = _arity(t, j + 1)
            k = pclose + 1 if pclose is not None else j + 2
            if _p(t, k, "=") and _p(t, k + 1, ">") and _p(t, k + 2, "{"):
                arm_close = _match(t, k + 2)
                if arm_close is not None:
                    resumes = [r for r in range(k + 3, arm_close)
                               if _ident(t, r) and t[r].val == "resume"
                               and _p(t, r + 1, "(")]
                    tail = all(_tail(t, r, arm_close) for r in resumes)
                    out.append({
                        "op": t[j].val, "arity": arity,
                        "line": int(t[j].line), "params": arity,
                        "resume": len(resumes), "resume_idx": resumes,
                        "tail_resumptive": tail,
                        "arm": [k + 2, arm_close],
                    })
                    j = arm_close + 1
                    continue
        j += 1
    return out


def _tail(t: list, resume_i: int, arm_close: int) -> bool:
    """`resume` 是所在块的最后一条语句（只认块尾与尾随的 `}`）。"""
    close = _match(t, resume_i + 1)
    if close is None:
        return False
    j = close + 1
    if _p(t, j, ";"):
        j += 1
    while j < arm_close:
        if not _p(t, j, "}"):
            return False
        j += 1
    return True


def _handlers(t: list) -> list[dict]:
    """`handle E { BODY } with { ARMS }`（深度处理器）。"""
    out: list[dict] = []
    n = len(t)
    for i, tok in enumerate(t):
        if not (tok.kind == "ident" and tok.val == "handle"
                and _ident(t, i + 1) and _p(t, i + 2, "{")):
            continue
        body_close = _match(t, i + 2)
        if body_close is None:
            continue
        w = body_close + 1
        if not (_ident(t, w) and t[w].val == "with" and _p(t, w + 1, "{")):
            continue
        arms_close = _match(t, w + 1)
        if arms_close is None:
            continue
        out.append({
            "effect": t[i + 1].val, "line": int(tok.line),
            "body": [i + 2, body_close], "arms_span": [w + 1, arms_close],
            "arms": _arms(t, w + 1, arms_close),
        })
    return out


def _effect_of_op(t: list) -> dict[str, list[str]]:
    """op 名 → 声明它的效应名（用于无前缀的 `perform op(…)`）。"""
    out: dict[str, list[str]] = {}
    for name, eff in _effects(t).items():
        for op in eff["ops"]:
            out.setdefault(op, []).append(name)
    return out


def _performs(t: list, effect_spans: list[tuple[int, int]]) -> list[dict]:
    out: list[dict] = []
    n = len(t)
    i = 0
    while i < n:
        if not (_ident(t, i) and t[i].val == "perform" and not _in_spans(effect_spans, i)):
            i += 1
            continue
        if _ident(t, i + 1) and _p(t, i + 2, ".") and _ident(t, i + 3):
            out.append({"effect": t[i + 1].val, "op": t[i + 3].val,
                        "idx": i, "line": int(t[i].line)})
            i += 4
            continue
        if _ident(t, i + 1) and _p(t, i + 2, "("):
            out.append({"effect": None, "op": t[i + 1].val,
                        "idx": i, "line": int(t[i].line)})
            i += 2
            continue
        i += 1
    return out


def _enclosing_fn(functions: list[dict], i: int) -> dict | None:
    best = None
    for f in functions:
        b = f["body"]
        if b and b[0] <= i <= b[1] and (best is None or b[0] > best["body"][0]):
            best = f
    return best


def _handled_at(handlers: list[dict], i: int) -> set[str]:
    """`i` 处仍被生效的处理器销掉的效应集合（深度语义：臂内不算）。"""
    out: set[str] = set()
    for h in handlers:
        b = h["body"]
        if b[0] <= i <= b[1]:
            out.add(h["effect"])
    return out


def _calls(t: list, functions: list[dict]) -> list[tuple[str, str, int]]:
    """(调用方, 被调方, 下标)。只认本单元里声明过的函数。"""
    names = {f["name"] for f in functions}
    out: list[tuple[str, str, int]] = []
    for f in functions:
        b = f["body"]
        if not b:
            continue
        for i in range(b[0] + 1, b[1]):
            if not (_ident(t, i) and t[i].val in names and _p(t, i + 1, "(")):
                continue
            if _p(t, i - 1, "."):
                continue
            out.append((f["name"], t[i].val, i))
    return out


def _choose_words(t: list, dim: str) -> set[str]:
    """`choose <dim> <value>` 里出现过的值（同一行）。"""
    out: set[str] = set()
    for i, tok in enumerate(t):
        if not (_ident(t, i) and tok.val == "choose"):
            continue
        for j in range(i + 1, len(t)):
            if t[j].line != tok.line:
                break
            if _ident(t, j) and t[j].val == dim and _ident(t, j + 1):
                out.add(t[j + 1].val)
    return out


def analyze(toks: list, unit: str = "m") -> dict:
    """返回 `{unit, effects, functions, handlers, sites, findings, questions, summary}`。"""
    fnv = _fnv1a32
    effects = _effects(toks)
    effect_spans = []
    for i, tok in enumerate(toks):
        if _ident(toks, i) and tok.val == "effect" and _ident(toks, i + 1) \
                and _p(toks, i + 2, "{"):
            c = _match(toks, i + 2)
            if c is not None:
                effect_spans.append((i, c))
    functions = _functions(toks, effect_spans)
    handlers = _handlers(toks)
    performs = _performs(toks, effect_spans)
    calls = _calls(toks, functions)
    op_owner = _effect_of_op(toks)
    no_gc = "no_gc" in _choose_words(toks, "gc")

    seen: dict[str, int] = {}

    def sid(kind: str, key: str) -> str:
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        return f"{unit}:{kind}:{fnv(key + '#' + str(occ)):08x}"

    findings: list[dict] = []
    questions: list[dict] = []
    sites: list[dict] = []

    def finding(kind: str, line: int, message: str, **kw) -> None:
        f = {"kind": kind, "line": int(line), "message": message}
        f.update(kw)
        findings.append(f)

    def question(code: str, site: str, state: str, claim: str) -> None:
        questions.append({"code": code, "site": site, "state": state,
                          "claim": claim})

    # ---- 处理器：完整性、臂、resume ----
    arm_spans: list[tuple[int, int]] = []
    general: list[dict] = []
    for h in handlers:
        name = h["effect"]
        for a in h["arms"]:
            arm_spans.append((a["arm"][0], a["arm"][1]))
        spec = effects.get(name)
        if spec is None:
            finding("unknown_effect", h["line"],
                    f"处理器处理的效应 `{name}` 没有声明")
            question("Q018", f"handle:{name}", "refuted",
                     f"处理器处理的效应 `{name}` 没有声明")
            h["exhaustive"] = False
            h["missing"] = []
            continue
        seen_ops: set[str] = set()
        bad = False
        missing = [op for op in spec["ops"] if op not in {a["op"] for a in h["arms"]}]
        for a in h["arms"]:
            if a["op"] in seen_ops:
                finding("duplicate_arm", a["line"],
                        f"效应 `{name}` 的 `{a['op']}` 有重复的臂")
                question("Q019", f"handle:{name}:{a['op']}", "refuted",
                         f"臂 `{a['op']}` 重复")
                bad = True
            seen_ops.add(a["op"])
            if a["op"] not in spec["ops"]:
                finding("arm_op_not_in_effect", a["line"],
                        f"臂 `{a['op']}` 不属于效应 `{name}`")
                question("Q019", f"handle:{name}:{a['op']}", "refuted",
                         f"臂 `{a['op']}` 不属于 `{name}`")
                bad = True
            elif a["arity"] != spec["ops"][a["op"]]["arity"]:
                finding("arm_arity", a["line"],
                        f"臂 `{a['op']}` 有 {a['arity']} 个参数，声明是 "
                        f"{spec['ops'][a['op']]['arity']} 个")
                question("Q019", f"handle:{name}:{a['op']}", "refuted",
                         f"臂 `{a['op']}` 的参数个数不符")
                bad = True
            if a["resume"] >= 2:
                blocks = [(_innermost(_brace_pairs(toks), r), r)
                          for r in a["resume_idx"]]
                blocks = [b for b in blocks if b[0] is not None]
                same = len({b[0] for b in blocks}) < len(blocks)
                if same:
                    finding("resume_twice", a["line"],
                            f"臂 `{a['op']}` 在同一块里调用了两次 `resume`")
                elif not a["tail_resumptive"]:
                    finding("resume_ambiguous", a["line"],
                            f"臂 `{a['op']}` 的 `resume` 次数证不出 0/1")
                question("Q020", f"{h['effect']}:{a['op']}", "refuted",
                         f"臂 `{a['op']}` 的 `resume` 线性无法证明")
        if missing:
            finding("handler_incomplete", h["line"],
                    f"效应 `{name}` 少臂：{', '.join(missing)}",
                    effect=name, missing=missing)
            question("Q019", f"handle:{name}", "refuted",
                     f"处理器没有覆盖 {', '.join(missing)}")
        h["missing"] = missing
        h["exhaustive"] = not missing and not bad
        h["_general"] = any(a["resume"] > 0 and not a["tail_resumptive"]
                            for a in h["arms"])
        if h["_general"]:
            general.append(h)
            question("Q022", f"handle:{name}", "assumed",
                     f"效应 `{name}` 有非尾续臂，续延捕获的回收未证明")

    # ---- resume 只能在臂里 ----
    for i, tok in enumerate(toks):
        if not (_ident(toks, i) and tok.val == "resume" and _p(toks, i + 1, "(")):
            continue
        if _in_spans(effect_spans, i) or _in_spans(arm_spans, i):
            continue
        finding("resume_outside_handler", tok.line,
                "`resume` 只能出现在处理器的臂里")
        question("Q020", f"resume@{tok.line}", "refuted",
                 "`resume` 在处理器之外")

    # ---- 效应行的局部推断与调用传播 ----
    local = {f["name"]: set() for f in functions}
    for p in performs:
        eff = p["effect"]
        if eff is None:
            owners = op_owner.get(p["op"], [])
            if len(owners) == 1:
                eff = owners[0]
                p["effect"] = eff
            else:
                finding("unknown_op", p["line"],
                        f"`perform {p['op']}` 无法唯一归到某个效应")
                question("Q018", f"perform:{p['line']}", "refuted",
                         f"操作 `{p['op']}` 不属于任何已声明的效应")
                continue
        if eff not in effects:
            finding("unknown_effect", p["line"],
                    f"`perform {eff}.{p['op']}` 的效应没有声明")
            question("Q018", f"perform:{p['line']}", "refuted",
                     f"效应 `{eff}` 没有声明")
            continue
        owner = _enclosing_fn(functions, p["idx"])
        if owner is None:
            continue
        handled = eff in _handled_at(handlers, p["idx"])
        if not handled:
            local[owner["name"]].add(eff)
        # `perform` 是"开口要"的动作：当场没有处理器，就必须写进声明行。
        # 私有的**传递**行可以靠调用图推断，但直接 perform 不许靠推断蒙混。
        if not handled and owner["decl"] is None:
            finding("missing_effect", p["line"],
                    f"`perform {eff}.{p['op']}` 没有处理器，"
                    f"`{owner['name']}` 也没有写 `effects {eff}`",
                    function=owner["name"], effect=eff, op=p["op"])
            question("Q018", f"perform:{p['line']}", "refuted",
                     f"`{owner['name']}` 的 perform 没有处理器也没有声明")

    effective = {f["name"]: set(local[f["name"]]) for f in functions}

    def contract(name: str) -> set[str]:
        f = next((x for x in functions if x["name"] == name), None)
        if f is None:
            return set()
        return set(f["decl"]) if f["decl"] is not None else set(effective[name])

    for _ in range(len(functions) + 2):
        changed = False
        for caller, callee, idx in calls:
            if callee not in effective or caller not in effective:
                continue
            need = contract(callee) - _handled_at(handlers, idx)
            before = len(effective[caller])
            effective[caller] |= need
            changed = changed or len(effective[caller]) != before
        if not changed:
            break

    for f in functions:
        f["inferred"] = sorted(effective[f["name"]])
        f["effective"] = f["inferred"]
        if f["decl"] is not None:
            extra = effective[f["name"]] - set(f["decl"])
            f["missing"] = sorted(extra)
            if extra:
                finding("missing_effect", f["line"],
                        f"`{f['name']}` 的 `effects` 行少了 {', '.join(sorted(extra))}",
                        function=f["name"], effects=sorted(extra))
                question("Q018", f"row:{f['name']}", "refuted",
                         f"`{f['name']}` 实际会触发 {', '.join(sorted(extra))}")
        else:
            f["missing"] = []
            if f["pub"] and effective[f["name"]]:
                finding("undeclared_row", f["line"],
                        f"`pub fn {f['name']}` 省略了 `effects` 行；导出函数的行必须声明",
                        function=f["name"], effects=sorted(effective[f["name"]]))
                question("Q018", f"row:{f['name']}", "assumed",
                         f"`{f['name']}` 的行是推断出来的，没有写进契约")

    # ---- 调用点：声明了行的调用方必须盖住被调方的行 ----
    call_rows: list[dict] = []
    for caller, callee, idx in calls:
        cf = next((x for x in functions if x["name"] == caller), None)
        if cf is None:
            continue
        need = contract(callee) - _handled_at(handlers, idx)
        declared = set(cf["decl"]) if cf["decl"] is not None else None
        ok = declared is None or need <= declared
        call_rows.append({"caller": caller, "callee": callee, "idx": idx,
                          "need": sorted(need), "proved": ok,
                          "line": int(toks[idx].line)})
        if not ok:
            missing = sorted(need - declared)
            finding("unhandled_call", toks[idx].line,
                    f"`{caller}` 调用 `{callee}` 需要 {', '.join(missing)}，"
                    f"但它的 `effects` 行没有",
                    caller=caller, callee=callee, effects=missing)
            question("Q018", f"call:{caller}->{callee}:{toks[idx].line}", "refuted",
                     f"`{caller}` 的行盖不住 `{callee}` 的 {', '.join(missing)}")

    # ---- extern 边界：C 侧没有证据记录 ----
    foreign: list[dict] = []
    for f in functions:
        if f["extern"] and f["decl"]:
            foreign.append(f)
            finding("extern_effects", f["line"],
                    f"`extern fn {f['name']}` 声明了效应 {', '.join(f['decl'])}；"
                    "效应不能穿越 C 边界")
            question("Q021", f"extern:{f['name']}", "refuted",
                     f"`{f['name']}` 把效应带到了 extern 边界")

    # ---- no_gc：一般处理器的续延回收没有证明 ----
    if no_gc and general:
        for h in general:
            finding("capture_unproved", h["line"],
                    f"`no_gc` 下 `{h['effect']}` 的续延捕获没有回收证明",
                    effect=h["effect"])

    # ---- 固定容量：越界要有一条会说话的路（`docs/262` §5.12） ----
    from .registry import LSPE_CAPACITIES
    for name, count, what in (
            ("effect_declarations", len(effects), "效应声明"),
            ("ops_per_effect", max((len(e["ops"]) for e in effects.values()),
                                   default=0), "单个效应的操作")):
        cap = LSPE_CAPACITIES[name][0]
        if count > cap:
            finding("capacity", 1,
                    f"{what}有 {count} 个，超过 LSPE 上限 {cap}")
            question("Q005", f"capacity:{name}", "assumed",
                     f"{what}超过容量 {cap}")
    depth = 0
    for h in handlers:
        n = sum(1 for other in handlers
                if other is not h and h["body"][0] < other["body"][0] < h["body"][1])
        depth = max(depth, n + 1)
    if depth > LSPE_CAPACITIES["handler_depth"][0]:
        finding("capacity", 1,
                f"词法处理器嵌套 {depth} 层，超过 LSPE 上限 "
                f"{LSPE_CAPACITIES['handler_depth'][0]}")
        question("Q019", "capacity:handler_depth", "assumed",
                 "处理器嵌套超过容量")

    # ---- 效应声明 / 函数行 / perform / handle / resume / 调用 的 LSPE 站点 ----
    declare = set(effects)
    for name, spec in effects.items():
        sites.append({
            "id": sid("effect_decl", f"{name}:{spec['line']}"),
            "kind": "effect_decl", "claim": "allow",
            "obligation": "EffectAllowed", "evidence": "Syntactic",
            "source": f"effect:{name}", "line": spec["line"],
            "span": spec["span"], "proved": True, "effect": name,
            "ops": sorted(spec["ops"])})
    for f in functions:
        declared = f["decl"]
        proved = not f["missing"]
        sites.append({
            "id": sid("row", f"{f['name']}:{f['line']}"),
            "kind": "row", "claim": "allow" if proved else "deny",
            "obligation": "EffectAllowed",
            "evidence": "Typed" if declared is not None else "Syntactic",
            "source": f"row:{f['name']}", "line": f["line"],
            "span": [f["line"], 0, f["line"], 0], "proved": proved,
            "function": f["name"], "declared": declared,
            "inferred": f["inferred"]})
    for p in performs:
        eff = p["effect"]
        handled = eff in _handled_at(handlers, p["idx"]) if eff else False
        owner = _enclosing_fn(functions, p["idx"])
        declared = set(owner["decl"]) if owner and owner["decl"] is not None else set()
        # 直接 perform 只认两条路：当场处理器，或声明行。推断只解释**传递**行。
        ok = handled or (eff in declared)
        ev = "Syntactic" if handled else ("Typed" if eff in declared else "none")
        sites.append({
            "id": sid("perform", f"{eff}:{p['op']}:{p['line']}"),
            "kind": "perform", "claim": "allow" if ok else "deny",
            "obligation": "EffectAllowed", "evidence": ev,
            "source": f"effect:{eff}" if eff else None,
            "line": p["line"], "span": [p["line"], 0, p["line"], 0],
            "proved": ok, "effect": eff, "op": p["op"]})
    for h in handlers:
        ok = h.get("exhaustive", False)
        sites.append({
            "id": sid("handle", f"{h['effect']}:{h['line']}"),
            "kind": "handle", "claim": "allow" if ok else "deny",
            "obligation": "HandlerExhaustive", "evidence": "Syntactic",
            "source": f"handle:{h['effect']}", "line": h["line"],
            "span": [h["line"], 0, h["line"], 0], "proved": ok,
            "effect": h["effect"]})
        if h.get("_general"):
            sites.append({
                "id": sid("handler_capture", f"{h['effect']}:{h['line']}"),
                "kind": "handler_capture", "claim": "allow",
                "obligation": "CaptureReclaim", "evidence": "Assumed",
                "source": "docs/346 §7", "line": h["line"],
                "span": [h["line"], 0, h["line"], 0], "proved": False,
                "effect": h["effect"]})
    for h in handlers:
        for a in h["arms"]:
            linear = a["resume"] < 2 or a["tail_resumptive"]
            sites.append({
                "id": sid("resume", f"{h['effect']}:{a['op']}:{a['line']}"),
                "kind": "resume", "claim": "allow" if linear else "deny",
                "obligation": "ResumeLinear", "evidence": "LocalFlow",
                "source": f"arm:{a['op']}", "line": a["line"],
                "span": [a["line"], 0, a["line"], 0], "proved": linear,
                "effect": h["effect"], "op": a["op"], "count": a["resume"]})
    for c in call_rows:
        sites.append({
            "id": sid("row_call", f"{c['caller']}->{c['callee']}:{c['line']}"),
            "kind": "row_call", "claim": "allow" if c["proved"] else "deny",
            "obligation": "EffectAllowed", "evidence": "Interproc",
            "source": f"row:{c['callee']}", "line": c["line"],
            "span": [c["line"], 0, c["line"], 0], "proved": c["proved"],
            "caller": c["caller"], "callee": c["callee"],
            "need": c["need"]})
    for f in foreign:
        sites.append({
            "id": sid("effect_foreign", f"{f['name']}:{f['line']}"),
            "kind": "effect_foreign", "claim": "deny",
            "obligation": "EffectBounded", "evidence": "Axiom",
            "source": f"extern:{f['name']}", "line": f["line"],
            "span": [f["line"], 0, f["line"], 0], "proved": False,
            "function": f["name"]})

    findings.sort(key=lambda f: (f["line"], f["kind"], f["message"]))
    questions.sort(key=lambda q: (q["site"], q["code"], q["claim"]))
    sites.sort(key=lambda s: s["id"])

    return {
        "unit": unit,
        "effects": effects,
        "functions": [{"name": f["name"], "arity": f["arity"],
                       "declared": f["decl"], "inferred": f["inferred"],
                       "missing": f["missing"], "line": f["line"],
                       "extern": f["extern"], "pub": f["pub"]}
                      for f in sorted(functions, key=lambda f: (f["line"], f["name"]))],
        "handlers": [{k: v for k, v in h.items() if not k.startswith("_")}
                     for h in sorted(handlers, key=lambda h: h["line"])],
        "sites": sites,
        "findings": findings,
        "questions": questions,
        "summary": {
            "effects": len(effects),
            "ops": sum(len(e["ops"]) for e in effects.values()),
            "functions": len(functions),
            "handlers": len(handlers),
            "performs": len(performs),
            "general_handlers": len(general),
            "findings": len(findings),
            "sites": len(sites),
        },
    }


def text_lines(report: dict) -> list[str]:
    s = report["summary"]
    out = [f"[effects] {report['unit']}: {s['effects']} effect(s) / {s['ops']} op(s), "
           f"{s['functions']} function(s), {s['handlers']} handler(s), "
           f"{s['performs']} perform(s)"]
    for f in report["findings"]:
        out.append(f"EFFECT[{f['kind']}] line {f['line']}: {f['message']}")
    if report["summary"]["general_handlers"]:
        out.append(f"EFFECT[general] {s['general_handlers']} 个非尾续处理器需要续延捕获")
    if not report["findings"]:
        out.append("EFFECT[none] 所有 perform 都有处理器或已声明的行")
    return out
