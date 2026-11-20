"""反问器与回答通道（`docs/262` §6）。

反问器不是质疑器在跟用户说话：它转交一个**已经命名、定界、三问都跑过**的洞。
它只在 `UNDECIDED` 上启用，不碰 `PROVED`，也不碰普通错误。

三条硬规则落在代码里：

* **同站点、同原因只问一次**（`seen` 集合）；
* **默认不是允许，是允许但标注** —— 默认答案 `B` 记一条 `Assumed` 地板，
  不把站点变成 `PROVED`；
* **转交前必须先自审**：`tried` 不覆盖 `provenance` / `falsification` /
  `cross_exam` 的问题被拒（**E036**）。
"""

from __future__ import annotations

import re

from .registry import (LSPE_INTERROGATIONS, LSPE_PROBED_OBLIGATIONS,
                       LSPE_QUESTION_TEMPLATES, LSPE_REASON_QUESTION)

#: answers 文件里 `defer(...)` 的形态。
_DEFER = re.compile(r"^defer\s*\(\s*(.*?)\s*\)$", re.I)


class LspeError(Exception):
    """LSPE 自己的错误（E034–E038）。**不是用户代码的错误**。"""

    def __init__(self, code: str, message: str, site: str | None = None) -> None:
        super().__init__(f"{code}: {message}" + (f" [{site}]" if site else ""))
        self.code = code
        self.message = message
        self.site = site


def parse_answers(text: str) -> dict[str, str]:
    """`lspe.answers` 的解析：一行 `<site>=<answer>`，`#` 开头是注释。

    answer 是 `A` / `B` / `C` / `defer(<理由>)` / `refuse`。键也可以写
    `drop:<类型>`（析构器的手写证明 = `prove`）。
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        site, _, ans = line.partition("=")
        site, ans = site.strip(), ans.strip()
        if not site or not ans:
            raise LspeError("E036", f"answers 行不完整：{raw!r}")
        out[site] = ans
    return out


def load_answers(path) -> dict[str, str]:
    from pathlib import Path  # noqa: PLC0415

    p = Path(path)
    if not p.is_file():
        raise LspeError("E036", f"answers 文件不存在：{p}")
    return parse_answers(p.read_text(encoding="utf-8"))


def _fill(text: str, site: dict, solver=None) -> str:
    vals = {
        "site": site.get("id", "?"),
        "obligation": str(site.get("obligation", "?")),
        "kind": str(site.get("kind", "?")),
        "type": str(site.get("type") or site.get("id", "?")),
        "subst": str(site.get("subst") or "∅"),
        "destructor": str(site.get("destructor") or site.get("id", "?")),
        "name": str(site.get("destructor") or site.get("name") or site.get("id", "?")),
        "domain": str(site.get("domain") or "?"),
        "construct": str(site.get("construct") or site.get("kind", "?")),
        "callee": str(site.get("callee") or site.get("source") or "?"),
        "id": str(site.get("id", "?")),
    }
    try:
        return text.format(**vals)
    except (KeyError, IndexError):
        return text


def question_for(site: dict, solver, answers: dict[str, str] | None = None,
                 mode: str = "full") -> dict:
    """把一个 `UNDECIDED` 站点转成一条问题记录（不写台账，纯函数）。"""
    reason = solver.reason(site) if solver is not None else site.get("reason")
    code = LSPE_REASON_QUESTION.get(str(reason or "none"), "Q001")
    # 没有配对反证探针的检查是**惰性**的（E8 / `#766`）：Q002，不是 Q001。
    if code == "Q001" and str(site.get("obligation")) not in LSPE_PROBED_OBLIGATIONS:
        code = "Q002"
    tpl = LSPE_QUESTION_TEMPLATES[code]
    tried = {k: _fill(str(v), site, solver) for k, v in tpl["tried"].items()}
    missing = [k for k in LSPE_INTERROGATIONS if k not in tried or not tried[k]]
    if missing:
        raise LspeError("E036", f"问题 {code} 的 tried 缺 {missing}", site.get("id"))
    ans = None
    if answers:
        ans = answers.get(str(site.get("id"))) or answers.get(code)
    state = "asked"
    if ans:
        if _DEFER.match(ans):
            state = "deferred"
        elif ans.strip().lower() == "refuse":
            state = "refused"
        elif ans.strip().upper() in ("A", "B", "C"):
            state = {"A": "deferred", "B": "assumed", "C": "refused"}[ans.strip().upper()]
        else:
            raise LspeError("E036", f"不认识的回答 {ans!r}", site.get("id"))
    elif mode == "strict":
        state = "unanswered"
    else:
        state = "assumed"                    # 默认 = 允许但标注（§6 硬规则 2）
    return {
        "code": code,
        "site": site.get("id"),
        "claim": _fill(str(tpl["claim"]), site, solver),
        "chain": [_fill(str(x), site, solver) for x in tpl["chain"]],
        "tried": [{"engine": k, "result": tried[k]} for k in LSPE_INTERROGATIONS],
        "options": dict(tpl["options"]),
        "default": tpl["default"],
        "reversible": bool(tpl["reversible"]),
        "answer": ans,
        "state": state,
    }


def ask(sites: list[dict], solver, answers: dict[str, str] | None = None,
        mode: str = "full") -> dict:
    """跑反问器：每条 `UNDECIDED` 站点至多问一次，返回问题与三种回答的账。"""
    questions: list[dict] = []
    assumed: list[str] = []
    deferred: list[dict] = []
    refused: list[str] = []
    errors: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for site in sorted(sites, key=lambda s: s["id"]):
        if site.get("covered_by") or solver.proved(site):
            continue
        reason = str(solver.reason(site) or "none")
        key = (site["id"], reason)
        if key in seen:
            continue
        seen.add(key)
        try:
            q = question_for(site, solver, answers, mode)
        except LspeError as e:
            errors.append({"code": e.code, "site": e.site, "message": e.message})
            continue
        site["question"] = q["code"]
        site["answer"] = q["answer"]
        q["state"] = q["state"]
        if q["state"] == "assumed":
            assumed.append(site["id"])
            site["assumed"] = True
        elif q["state"] == "deferred":
            m = _DEFER.match(q["answer"] or "")
            deferred.append({"site": site["id"],
                             "reason": (m.group(1) if m else "fix")})
            site["deferred"] = True
        elif q["state"] == "refused":
            refused.append(site["id"])
            site["refused"] = True
        elif q["state"] == "unanswered":
            errors.append({"code": "E034", "site": site["id"],
                           "message": "strict 模式下有未回答的问题"})
        questions.append(q)
    return {"questions": questions, "assumed": assumed, "deferred": deferred,
            "refused": refused, "errors": errors}


def answer_lines(doc: dict) -> list[str]:
    """把台账里的问题打印成 LSPC 的条（`loment lspe state` 用）。"""
    out: list[str] = []
    for q in doc.get("questions", []):
        state = q.get("state", "asked")
        out.append(f"[LSPE] {q['code']} {q['site']} ({state}): {q['claim']}")
    return out
