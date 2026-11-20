"""AI 检查器（`choose lspe_aicheck_true`，`docs/262` §10.7）。

**AI 是问题源，不是证据源。** 这条边界是它进 LSPE 的方式：

* 它只为**已经存在**的洞（反问器问过的站点）准备一份可复核的请求；
* 它给出的答案只能是 **Q014「AI 提出的怀疑」**，永远不进求解器、不改变
  `proved` / `floors` / `per_kind`；
* 模型调用不在编译期发生：编译只记 `lspe.aicheck: true`，请求与回答都走文件，
  这样 CI 是确定性的、可离线的，回答也能进版本库审查。

请求与回答都是 JSON。请求的 `site` 是内容寻址 id；回答只能引用已存在的 id，
引用未知 id 是一个错误，不是一条新发现。
"""

from __future__ import annotations

import json

from .questions import LspeError

FORMAT = "lspe-aicheck"
VERSION = 1
REPLY_FORMAT = "lspe-aicheck-replies"
VERDICTS = ("suspicious", "clean", "unsure")

#: 一段摘录最多几行（前后各 `radius` 行）。产物要小、要可读。
EXCERPT_RADIUS = 2


def _excerpt(lines: list[str], line: int, radius: int = EXCERPT_RADIUS) -> str:
    if not lines or line <= 0:
        return ""
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line + radius)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


def bundle(unit: str, questions: list[dict], sites_by_id: dict[str, dict],
           source_lines: list[str] | None = None,
           model: str | None = None) -> dict:
    """把反问器已经问过的站点变成一份**确定性的**审查请求。

    排序按站点 id，不按行号 —— 同一份单元两次生成的字节相同。
    """
    reqs = []
    for q in sorted(questions, key=lambda x: str(x.get("site"))):
        sid = str(q.get("site"))
        site = sites_by_id.get(sid, {})
        line = int(site.get("line", 0) or 0)
        reqs.append({
            "site": sid,
            "kind": site.get("kind"),
            "obligation": site.get("obligation"),
            "question": q.get("code"),
            "state": q.get("state"),
            "claim": q.get("claim"),
            "line": line,
            "span": site.get("span") or [line, 0, line, 0],
            "excerpt": _excerpt(source_lines or [], line),
        })
    return {"format": FORMAT, "version": VERSION, "unit": unit,
            "model": model, "requests": reqs}


def ingest(replies: dict, sites: list[dict], unit: str) -> dict:
    """吃掉一份模型回答。只产 **Q014**，不碰任何判决。

    返回 `{"findings": [...], "unknown": [...]}`；`findings` 里每条都是问题，
    不是证据。形状不对（格式、单元、verdict、未知站点）一律 **E036** ——
    回答也是要记账的输入，不能静默丢弃。
    """
    if replies.get("format") != REPLY_FORMAT:
        raise LspeError("E036", f"不是 AI 检查器的回答：format={replies.get('format')!r}")
    if replies.get("unit") != unit:
        raise LspeError("E036",
                        f"回答属于 {replies.get('unit')!r}，这一份是 {unit!r}")
    known = {s["id"] for s in sites}
    model = str(replies.get("model") or "unknown")
    findings: list[dict] = []
    unknown: list[str] = []
    for i, f in enumerate(replies.get("findings") or []):
        sid = str((f or {}).get("site") or "")
        verdict = str((f or {}).get("verdict") or "")
        if sid not in known:
            unknown.append(sid)
            continue
        if verdict not in VERDICTS:
            raise LspeError("E036", f"findings[{i}] 的 verdict 不认识：{verdict!r}")
        findings.append({
            "code": "Q014",
            "site": sid,
            "verdict": verdict,
            "note": str(f.get("note") or ""),
            "source": f"ai:{model}",
        })
    return {"findings": findings, "unknown": unknown}


def render_text(result: dict) -> list[str]:
    out = []
    for f in result.get("findings", []):
        out.append(f"[LSPE] Q014 {f['site']} ({f['verdict']}): {f['note']}")
    if result.get("unknown"):
        out.append("[LSPE] 回答引用了不存在的站点："
                   + ", ".join(result["unknown"]))
    return out


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
