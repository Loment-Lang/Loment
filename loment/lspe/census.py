"""站点普查与引擎 E1-E4（`docs/262` §5.1 / §5.4）。

口径是**词法的**：参考实现与自举实现吃同一串 token，规则逐条同形。这个模块不拥有
L0/L1/L2 的安全谓词或 syscall 借用语义 —— 那些是编译器的事实（`docs/210` §7 /
`docs/246` §5.3），所以只在入口处按需取用。
"""

from __future__ import annotations

_L0_SAFE_BUILTINS: frozenset = frozenset()
_syscall_borrow_ok = None
_fnv1a32 = None


def _ensure_compiler_facts() -> None:
    global _L0_SAFE_BUILTINS, _syscall_borrow_ok, _fnv1a32
    if _fnv1a32 is not None:
        return
    from lomentc import (_L0_SAFE_BUILTINS as safe,
                         _fnv1a32 as fnv,
                         _syscall_borrow_ok as borrow)
    _L0_SAFE_BUILTINS, _fnv1a32, _syscall_borrow_ok = safe, fnv, borrow


#: `[` 前面出现这些关键字时，它是**类型方括号 / 数组字面量**，不是下标。
LSPE_BRACKET_KW = frozenset({
    "return", "if", "while", "match", "let", "const", "in", "else", "for",
    "fn", "struct", "enum", "impl", "trait", "pub", "use", "module", "choose",
    "capability", "excluded", "guard", "as", "mut",
})

#: 后接 `(` 也不算函数调用的关键字（`if (...)`、`guard d(...)` …）。
#: 与自举侧 `pt_lspe_call_kw` **逐字同表** —— 两边普查的调用点计数不能漂。
LSPE_CALL_KW = LSPE_BRACKET_KW | frozenset({"extern", "true", "false", "self"})



def census_bounds(toks: list, sid, sites: list[dict], cover=lambda _i: None,
                  lms=frozenset()) -> None:
    """E2b：LMS **g1 内联界检查**的站点（`docs/262` §5.1 / `#1021`）。

    第 3 级（`lms_guard`）对 `load8` / `store8` 发一条 `icmp ult i32 …, <const>` ——
    这是实打实花在热路径上的检查，但在此之前的普查里**没有条目**（它没有自己的语法）。

    规则与两侧**发射器**逐条同形（参考 `_Ir._lms_guard_site`，自举 `lms_alloc_size`）：

    * 只认 `load8` / `store8`（参考实现只给这两条内建发检查）；
    * 首参是标识符，且按发射顺序往回找到**最近一次** `let NAME : ptr = alloc(<常量>)`；
      中间出现一次 `NAME = …` 就作废（遮蔽/重新赋值改变来源）；
    * 常量只认字面量与模块 `const`（与 `_lms_fixed_size` 同一张表）；
    * 档位：`lms_guard` **且**（`lms_reject` 或 `lms_crash`）开着。三档回答都不开时
      发射器一条检查都不发（"没有回答的检查比没有检查更坏"）。

    ⚠ 这是**词法近似**，但它是两侧都完整持有的那一份表示：g1 站点没有自己的语法，
      id 只能从 token 文本拼（`bounds:<fn>:<指针>:<load8|store8>:<界>`）；重复的
      完全相同访问由 `sid` 的序数区分。
    """
    if "lms_guard" not in lms or not ({"lms_reject", "lms_crash"} & set(lms)):
        return
    fns = _lspe_functions(toks)
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "ident" or t.val not in ("load8", "store8"):
            continue
        if i + 1 >= n or toks[i + 1].kind != "punct" or toks[i + 1].val != "(":
            continue
        if i > 0 and toks[i - 1].kind == "punct" and toks[i - 1].val in (".", ":"):
            continue
        if i > 0 and toks[i - 1].kind == "ident" and toks[i - 1].val == "fn":
            continue
        a = i + 2
        if a >= n or toks[a].kind != "ident":
            continue
        fn = next(((nm, at) for (nm, _p, at, end) in fns if at <= i < end), None)
        if fn is None:
            continue
        size = _lspe_alloc_size(toks, a, fn[1])
        if size is None:
            continue
        site = {"id": sid("bounds",
                          f"bounds:{toks[fn[0]].val}:{toks[a].val}:{t.val}:{size}"),
                "kind": "bounds", "claim": "allow", "obligation": "InBounds",
                "evidence": "Syntactic", "source": "lms(g1)",
                "line": t.line, "proved": True}
        cid = cover(i)
        if cid:
            site["covered_by"] = cid
        sites.append(site)


def _lspe_int(tok) -> int | None:
    if tok is None or tok.kind != "number":
        return None
    try:
        return int(tok.val.replace("_", ""), 0)
    except ValueError:
        return None


def _lspe_const_int(toks: list, name: str) -> int | None:
    """模块 `const NAME : T = <字面量>;` 的值（与 `_lms_fixed_size` 的 `self.consts` 同表）。"""
    n = len(toks)
    for k, t in enumerate(toks):
        if not (t.kind == "ident" and t.val == "const" and k + 1 < n
                and toks[k + 1].kind == "ident" and toks[k + 1].val == name):
            continue
        j = k + 2
        while j < n and not (toks[j].kind == "punct" and toks[j].val in ("=", ";")):
            j += 1
        if j + 1 < n and toks[j].val == "=":
            return _lspe_int(toks[j + 1])
    return None


def _lspe_alloc_size(toks: list, name_tok: int, lo: int) -> int | None:
    """往回找**最近一次** `let NAME : ptr = alloc(<常量>)`；被 `NAME = …` 挡掉就回 `None`。

    `lo` 是所在函数的体首 token —— 与自举侧 `lms_alloc_size` 同一条边界
    （参考实现的 `_Ir.lms_size` 是发射期的一张表，顺序等价于这条向后扫描）。
    """
    name = toks[name_tok].val
    n = len(toks)
    j = name_tok - 1
    while j >= lo:
        t = toks[j]
        if (t.kind == "ident" and t.val == name and j + 1 < n
                and toks[j + 1].kind == "punct" and toks[j + 1].val == "="):
            return None
        if (t.kind == "ident" and t.val == "let" and j > 0
                and not (toks[j - 1].kind == "ident" and toks[j - 1].val == "if")
                and j + 8 < n
                and toks[j + 1].kind == "ident" and toks[j + 1].val == name
                and toks[j + 2].kind == "punct" and toks[j + 2].val == ":"
                and toks[j + 3].kind == "ident" and toks[j + 3].val == "ptr"
                and toks[j + 4].kind == "punct" and toks[j + 4].val == "="
                and toks[j + 5].kind == "ident" and toks[j + 5].val == "alloc"
                and toks[j + 6].kind == "punct" and toks[j + 6].val == "("
                and toks[j + 8].kind == "punct" and toks[j + 8].val == ")"):
            arg = toks[j + 7]
            if arg.kind == "number":
                return _lspe_int(arg)
            if arg.kind == "ident":
                return _lspe_const_int(toks, arg.val)
            return None
        j -= 1
    return None


def pass_spans(toks: list, sid) -> list[dict]:
    """`lspe pass "reason" { … }` 的词法跨度（`docs/262` §5.16）。

    块是**审计旁路**，不是控制流：内层语句照常检查、照常发射；这里只记它的
    token 跨度、理由与 id，供普查给内层站点打 `covered_by`。
    """
    spans: list[dict] = []
    n = len(toks)
    for i, t in enumerate(toks):
        if not (t.kind == "ident" and t.val == "lspe" and i + 3 < n):
            continue
        if not (toks[i + 1].kind == "ident" and toks[i + 1].val == "pass"):
            continue
        if toks[i + 2].kind != "string" or not toks[i + 2].val.strip():
            continue                          # 解析器已用 E038 拒掉；这里防御性跳过
        if not (toks[i + 3].kind == "punct" and toks[i + 3].val == "{"):
            continue
        end = _lspe_match_brace(toks, i + 3)
        if end is None:
            continue
        spans.append({"id": sid("pass", f"pass:{toks[i + 2].val}"),
                      "reason": toks[i + 2].val,
                      "open": i + 3, "close": end,
                      "line": t.line, "end_line": toks[end].line, "sites": 0})
    for s in spans:
        s["outer"] = not any(o is not s and o["open"] < s["open"] and s["close"] < o["close"]
                             for o in spans)
    return spans


def _covered(spans: list[dict], i: int) -> str | None:
    """token `i` 落在哪个 pass 块里（取最外层；没有就回 None）。"""
    for s in spans:
        if s["open"] < i < s["close"] and s.get("outer", True):
            return s["id"]
    return None


def _mark(site: dict, cover, i: int) -> dict:
    cid = cover(i)
    if cid:
        site["covered_by"] = cid
    return site


def census(toks: list, unit: str,
           lms=frozenset()) -> tuple[list[dict], list[int], list[dict]]:
    """站点普查（`docs/262` §5.1）：E1/E2（guard + 下标）、E3、E4、pass 块。

    口径是**词法的**，且两套实现用**同一条规则** —— 参考侧吃 `lomc.lex` 的 token，
    自举侧吃驱动器的 token 数组。这样 twin 的计数不会因为 AST 形状不同而漂。

    线性 CFG：语句按 token 顺序执行，`return` 结束它所在块剩下一段。这是 E3 的
    "固定顺序"定义（`docs/262` §5.4）——没有分支敏感，只有块结构。
    """
    _ensure_compiler_facts()
    sites: list[dict] = []
    unsourced: list[int] = []
    seen: dict[str, int] = {}

    def sid(kind: str, key: str) -> str:
        # 内容寻址：键是站点的 token 文本，重复序数只区分**完全相同**的表达式。
        # 不用行号（会漂），也不用全局序号（在前面插一个站点会把后面全部重编号）。
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        return f"{unit}:{kind}:{_fnv1a32(key + '#' + str(occ)):08x}"

    spans = pass_spans(toks, sid)

    def cover(i: int) -> str | None:
        return _covered(spans, i)

    census_e1e2(toks, sid, sites, unsourced, cover)
    census_bounds(toks, sid, sites, cover, lms)
    census_e3(toks, sid, sites, cover)
    census_e4(toks, sid, sites, (), cover)
    for s in sites:
        cid = s.get("covered_by")
        if cid:
            for ps in spans:
                if ps["id"] == cid:
                    ps["sites"] += 1
    return sites, unsourced, spans


def census_e1e2(toks: list, sid, sites: list[dict], unsourced: list[int],
                cover=lambda _i: None) -> None:
    """E1 常量 + E2 类型（`docs/262` §5.4）。

    * `guard` 关键字 = 一个 guard 站点，来源 `capability`；
    * `[` 且前一个 token 是**非关键字标识符 / `)` / `]`** = 一个 index 站点；
      下标是**裸字面量**（`[ N ]` 三个 token）时来源 `lms(g0)`，否则无来源。
    """
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind == "ident" and t.val == "guard":
            cap = toks[i + 1].val if i + 1 < n else ""
            site = {"id": sid("guard", f"guard:{cap}"), "kind": "guard",
                    "claim": "allow", "obligation": "GuardHolds",
                    "evidence": "Typed",
                    "source": "capability", "line": t.line, "proved": True}
            cid = cover(i)
            if cid:
                site["covered_by"] = cid
            sites.append(site)
            continue
        if t.kind != "punct" or t.val != "[" or i == 0:
            continue
        prev = toks[i - 1]
        indexish = ((prev.kind == "ident" and prev.val not in LSPE_BRACKET_KW)
                    or (prev.kind == "punct" and prev.val in (")", "]")))
        if not indexish:
            continue
        # 能力域声明 `capability d : disk[0..8]` 的 `[` 不是下标 —— 里面有 `..`
        # （词法器把它拆成两个 `.` token）。
        if (i + 3 < n and toks[i + 1].kind == "number"
                and toks[i + 2].kind == "punct" and toks[i + 2].val == "."
                and toks[i + 3].kind == "punct" and toks[i + 3].val == "."):
            continue
        literal = (i + 2 < n and toks[i + 1].kind == "number"
                   and toks[i + 2].kind == "punct" and toks[i + 2].val == "]")
        # 站点内容 = LHS 的最近几个 token + 方括号里的 token 文本；空格/注释/换行不进键。
        depth = 0
        j = i + 1
        inner: list[str] = []
        while j < n:
            if toks[j].kind == "punct" and toks[j].val == "[":
                depth += 1
            elif toks[j].kind == "punct" and toks[j].val == "]":
                if depth == 0:
                    break
                depth -= 1
            inner.append(toks[j].val)
            j += 1
        # 只用**紧邻的前一个 token** 作 LHS。往前多抓几个 token 会把上一条语句
        # （`;`、`return` …）也卷进键里 —— 插入一个无关站点就会让 id 变。
        lhs = prev.val
        site = {"id": sid("index", f"index:{lhs}[{''.join(inner)}]"),
                "kind": "index",
                "claim": "allow", "obligation": "InBounds",
                "evidence": ("Syntactic" if literal else "none"),
                "source": ("lms(g0)" if literal else None),
                "line": t.line, "proved": literal}
        cid = cover(i)
        if cid:
            site["covered_by"] = cid
        sites.append(site)
        if not literal:
            unsourced.append(t.line)


def _lspe_match_brace(toks: list, i: int) -> int | None:
    d = 0
    for j in range(i, len(toks)):
        t = toks[j]
        if t.kind != "punct":
            continue
        if t.val == "{":
            d += 1
        elif t.val == "}":
            d -= 1
            if d == 0:
                return j
    return None


def _lspe_functions(toks: list) -> list[tuple[int, int, int, int]]:
    """本单元每个**有体**函数的 `(名字 token, 首形参 token, 体首 token, 体尾 token)`。"""
    n = len(toks)
    out: list[tuple[int, int, int, int]] = []
    for i, t in enumerate(toks):
        if not (t.kind == "ident" and t.val == "fn"):
            continue
        if i > 0 and toks[i - 1].kind == "ident" and toks[i - 1].val == "extern":
            continue
        if i + 1 >= n or toks[i + 1].kind != "ident":
            continue
        # 找形参表的 `(`（跳过 `<...>`）
        k = i + 2
        while k < n and not (toks[k].kind == "punct" and toks[k].val == "("):
            if toks[k].kind == "punct" and toks[k].val in (";", "{"):
                break
            k += 1
        if k >= n or not (toks[k].kind == "punct" and toks[k].val == "("):
            continue
        d = 0
        j = k
        while j < n:
            if toks[j].kind == "punct" and toks[j].val == "(":
                d += 1
            elif toks[j].kind == "punct" and toks[j].val == ")":
                d -= 1
                if d == 0:
                    break
            j += 1
        if d != 0:
            continue
        b = j + 1
        while b < n and not (toks[b].kind == "punct" and toks[b].val in ("{", ";")):
            b += 1
        if b >= n or toks[b].val != "{":
            continue
        e = _lspe_match_brace(toks, b)
        if e is None:
            continue
        out.append((i + 1, k + 1, b + 1, e))
    return out


def _lspe_is_write(toks: list, k: int) -> bool:
    """`x = ...` 是写；`x == ...` 是读（词法器把 `==` 拆成两个 `=` token）。"""
    n = len(toks)
    return (k + 1 < n and toks[k + 1].kind == "punct" and toks[k + 1].val == "="
            and not (k + 2 < n and toks[k + 2].kind == "punct" and toks[k + 2].val == "="))


def _lspe_unreachable(toks: list, free_i: int, use_i: int) -> bool:
    """从 use 反向走到 free：同一块或**外层**块里出现过 `return` ⇒ 这句不可达。

    反向走法自然区分兄弟块：兄弟块里的 `return` 会遇到它的 `}` 被记进深度，不算。
    """
    d = 0
    k = use_i - 1
    while k > free_i:
        t = toks[k]
        if t.kind == "punct":
            if t.val == "}":
                d += 1
            elif t.val == "{" and d > 0:
                d -= 1
        elif t.kind == "ident" and d == 0 and t.val == "return":
            return True
        k -= 1
    return False


def _lspe_owned(toks: list, at: int, free_i: int, name_i: int) -> bool:
    """`free(x)` 之前最近一处 `let x` 是不是 `let x : ptr = alloc (`。"""
    k = free_i - 1
    while k > at:
        if (toks[k].kind == "ident" and toks[k].val == toks[name_i].val
                and toks[k - 1].kind == "ident" and toks[k - 1].val == "let"):
            n = len(toks)
            return (k + 5 < n and toks[k + 1].kind == "punct" and toks[k + 1].val == ":"
                    and toks[k + 2].kind == "ident" and toks[k + 2].val == "ptr"
                    and toks[k + 3].kind == "punct" and toks[k + 3].val == "="
                    and toks[k + 4].kind == "ident" and toks[k + 4].val == "alloc"
                    and toks[k + 5].kind == "punct" and toks[k + 5].val == "(")
        k -= 1
    return False


def census_e3(toks: list, sid, sites: list[dict],
              cover=lambda _i: None) -> None:
    """E3 局部流（`docs/262` §5.4）：`FreeOwnership` / `Alive` / `Initialized` / `Exclusive`。

    规则与检查器的局部表**同源**（E029 的 `freed` 表、E006 的 `moved` 表）：这里是
    把那些判决变成证据，不是另写一套语义。线性 CFG 见 `census` 的说明。
    """
    for _name_i, _p_i, at, end in _lspe_functions(toks):
        for i in range(at, end):
            t = toks[i]
            # ---- FreeOwnership + 之后的 Alive ----
            if (t.kind == "ident" and t.val == "free" and i + 3 < end
                    and toks[i + 1].kind == "punct" and toks[i + 1].val == "("
                    and toks[i + 2].kind == "ident"
                    and toks[i + 3].kind == "punct" and toks[i + 3].val == ")"):
                name_i = i + 2
                name = toks[name_i].val
                uses: list[int] = []
                for j in range(i + 4, end):
                    if toks[j].kind != "ident" or toks[j].val != name:
                        continue
                    if j > at and toks[j - 1].kind == "ident" and toks[j - 1].val == "let":
                        continue                       # 重新声明 = 新名字主人
                    if _lspe_is_write(toks, j):
                        continue                       # 写是重新指向，不是用旧块
                    uses.append(j)
                reachable = [j for j in uses if not _lspe_unreachable(toks, i + 3, j)]
                owned = _lspe_owned(toks, at, i, name_i)
                sites.append(_mark({"id": sid("free", f"free:{name}"),
                                    "kind": "free", "claim": "allow",
                                    "obligation": "FreeOwnership",
                                    "evidence": ("LocalFlow" if owned and not reachable else "none"),
                                    "source": ("local-flow" if owned and not reachable else None),
                                    "line": t.line, "proved": owned and not reachable},
                                   cover, i))
                for j in uses:
                    un = _lspe_unreachable(toks, i + 3, j)
                    sites.append(_mark({"id": sid("alive", f"alive:{name}:{j - i}"),
                                        "kind": "alive", "claim": "allow",
                                        "obligation": "Alive",
                                        "evidence": ("LocalFlow" if un else "none"),
                                        "source": ("local-flow" if un else None),
                                        "line": toks[j].line, "proved": un},
                                       cover, j))
            # ---- Initialized（`let x: T;` 之后的读） ----
            if (t.kind == "ident" and t.val == "let" and i + 1 < end
                    and toks[i + 1].kind == "ident"):
                name = toks[i + 1].val
                depth = 0
                initialized = False
                j = i + 2
                while j < end:
                    v = toks[j].val if toks[j].kind == "punct" else None
                    if v in ("(", "[", "{"):
                        depth += 1
                    elif v in (")", "]", "}") and depth > 0:
                        depth -= 1
                    elif v == "=" and depth == 0:
                        # 声明里的 `=`（`==` 是读，不算；它的第一个 `=` 后面紧跟 `=`）。
                        if not (j + 1 < end and toks[j + 1].kind == "punct"
                                and toks[j + 1].val == "="):
                            initialized = True
                    elif v == ";" and depth == 0:
                        break
                    j += 1
                if initialized or j >= end:
                    continue
                assigned = False
                for k in range(j + 1, end):
                    if (toks[k].kind == "ident" and toks[k].val == "let"
                            and k + 1 < end and toks[k + 1].kind == "ident"
                            and toks[k + 1].val == name):
                        break
                    if toks[k].kind != "ident" or toks[k].val != name:
                        continue
                    if k > at and toks[k - 1].kind == "ident" and toks[k - 1].val == "let":
                        continue
                    if _lspe_is_write(toks, k):
                        assigned = True
                        continue
                    sites.append(_mark({"id": sid("init", f"init:{name}:{k - j}"),
                                        "kind": "init", "claim": "allow",
                                        "obligation": "Initialized",
                                        "evidence": ("LocalFlow" if assigned else "none"),
                                        "source": ("local-flow" if assigned else None),
                                        "line": toks[k].line, "proved": assigned},
                                       cover, k))
            # ---- Exclusive（`&mut x` 与同一语句里的另一次使用重叠） ----
            if (t.kind == "punct" and t.val == "&" and i + 2 < end
                    and toks[i + 1].kind == "ident" and toks[i + 1].val == "mut"
                    and toks[i + 2].kind == "ident"):
                name = toks[i + 2].val
                other = False
                k = i + 3
                while k < end:
                    if toks[k].kind == "punct" and toks[k].val == ";":
                        break
                    if toks[k].kind == "ident" and toks[k].val == name:
                        other = True
                    k += 1
                sites.append(_mark({"id": sid("exclusive", f"exclusive:{name}:{i}"),
                                    "kind": "exclusive", "claim": "allow",
                                    "obligation": "Exclusive",
                                    "evidence": ("none" if other else "LocalFlow"),
                                    "source": (None if other else "local-flow"),
                                    "line": t.line, "proved": not other},
                                   cover, i))


def _lspe_safe_set(toks: list, fns: list[tuple[int, int, int, int]],
                   extra: list[list] = ()) -> set[str]:
    """deref-only 摘要的全局不动点（`docs/210` §7）。

    与自举侧 `compute_safe` **同一条 token 规则**（含 `syscall_borrow_ok` 那一支），
    所以两边算出的安全名集合相同 —— E4 的 `Interproc` 计数不能漂。
    `extra` 是依赖单元：自举侧 `compute_safe` 扫的是**整个单元**（依赖在根前面），
    所以发射时的安全表必须把依赖一起算进来（`load32 -> load16 -> load8` 常常跨模块）。
    递归环只有在有基例时才进入集合；纯递归的函数停在集合外，调用点记 UNDECIDED。
    """
    units = [(toks, fns)] + list(extra)
    safe: set[str] = set(_L0_SAFE_BUILTINS)
    changed = True
    rounds = 0
    while changed and rounds < 256:
        changed = False
        rounds += 1
        for utoks, ufns in units:
            for name_i, p_i, at, end in ufns:
                name = utoks[name_i].val
                if name in safe:
                    continue
                if (p_i + 2 >= end or utoks[p_i].kind != "ident"
                        or utoks[p_i].val == "self"):
                    continue
                if not (utoks[p_i + 1].kind == "punct" and utoks[p_i + 1].val == ":"
                        and utoks[p_i + 2].kind == "ident" and utoks[p_i + 2].val == "ptr"):
                    continue
                # 带 `lspe pass` 的摘要是 `Unchecked`（`#786` 第 3 条）：它不再证明
                # 任何事，所以调用点保持 UNDECIDED —— 旁路不跨调用传播。
                if any(utoks[k].kind == "ident" and utoks[k].val == "lspe"
                       and k + 1 < end and utoks[k + 1].kind == "ident"
                       and utoks[k + 1].val == "pass" for k in range(at, end)):
                    continue
                p = utoks[p_i].val
                ok = True
                for k in range(at, end):
                    if utoks[k].kind != "ident" or utoks[k].val != p:
                        continue
                    if (k > 0 and utoks[k - 1].kind == "ident"
                            and utoks[k - 1].val == "let") or _lspe_is_write(utoks, k):
                        ok = False
                        break
                    if _syscall_borrow_ok(utoks, k):
                        continue
                    unqualified = (k < 3
                                   or not (utoks[k - 3].kind == "punct"
                                           and utoks[k - 3].val in (".", ":")))
                    call_ok = (k >= 2 and k + 1 < len(utoks)
                               and utoks[k - 1].kind == "punct" and utoks[k - 1].val == "("
                               and utoks[k - 2].kind == "ident" and unqualified
                               and utoks[k + 1].kind == "punct"
                               and utoks[k + 1].val in (",", ")")
                               and utoks[k - 2].val in safe)
                    if not call_ok:
                        ok = False
                        break
                if ok:
                    safe.add(name)
                    changed = True
    return safe


def census_e4(toks: list, sid, sites: list[dict],
              extra: list[list] = (), cover=lambda _i: None) -> None:
    """E4 过程间摘要（`docs/262` §5.4）：首参是 `ptr` 的函数的每个调用点一条
    `SummaryContract` 站点。摘要在安全表里 ⇒ `Interproc`；否则是洞（`none`）。

    摘要键是**调用点的实际实参 token 文本**，不是只有函数名 —— 推断式泛型的替换
    由实参决定，所以同一个 `f` 的两次不同调用是两个键。

    `extra` 是依赖单元：安全表要看**根 + 依赖**（自举侧 `compute_safe` 扫整个单元），
    但首参表只看根 —— 依赖里的函数不是这份产物要审计的站点。
    """
    _ensure_compiler_facts()
    fns = _lspe_functions(toks)
    ptr_first = {toks[n].val for (n, p, _at, end) in fns
                 if p + 2 < end and toks[p].kind == "ident"
                 and toks[p + 1].kind == "punct" and toks[p + 1].val == ":"
                 and toks[p + 2].kind == "ident" and toks[p + 2].val == "ptr"}
    if not ptr_first:
        return
    safe = _lspe_safe_set(toks, fns, [(u, _lspe_functions(u)) for u in extra])
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "ident" or t.val in LSPE_CALL_KW or t.val not in ptr_first:
            continue
        if not (i + 1 < n and toks[i + 1].kind == "punct" and toks[i + 1].val == "("):
            continue
        if i > 0 and toks[i - 1].kind == "punct" and toks[i - 1].val in (".", ":"):
            continue
        if i > 0 and toks[i - 1].kind == "ident" and toks[i - 1].val == "fn":
            continue
        depth = 0
        j = i + 1
        inner: list[str] = []
        while j < n and len(inner) < 32:
            if toks[j].kind == "punct" and toks[j].val == "(":
                depth += 1
            elif toks[j].kind == "punct" and toks[j].val == ")":
                depth -= 1
                if depth == 0:
                    break
            inner.append(toks[j].val)
            j += 1
        proved = t.val in safe
        sites.append(_mark({"id": sid("call", f"call:{t.val}{''.join(inner)}"),
                            "kind": "call", "claim": "allow",
                            "obligation": "SummaryContract",
                            "evidence": ("Interproc" if proved else "none"),
                            "source": (t.val if proved else None),
                            "line": t.line, "proved": proved},
                           cover, i))

