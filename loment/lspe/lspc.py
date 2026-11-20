"""LSPC：编译前贴条（`docs/262` §7）。

警察不是上帝。它不审判，只贴条、开单、告诉你后果。预检单有 **12 个风险族**，
一族一行、五列（你现在靠什么 / 如果不成立 / 修法 / 不修的后果 / 谁承担）。

最后一行是递归的：**把告知关掉这件事本身，也必须被告知**（§7.2）。

`lspc` 是**构建旋钮**，不是第八维：`full` / `quiet` / `strict` / `off`。
默认跟随 `lspe`；`--lspc=off` 必须写进产物（否则它就成了最大的一块无标签地板）。
"""

from __future__ import annotations

from .registry import LSPE_LSPC_FAMILIES

LSPC_MODES = ("full", "quiet", "strict", "off")


def default_mode(lspe_state: str, lspe_mode: str = "full") -> str:
    """`docs/262` §7.6 的默认表。"""
    if lspe_state == "lspe_close_admin":
        return "off"
    if lspe_mode == "quiet":
        return "quiet"
    return "full"


def _present(fid: str, sites: list[dict], facts: dict, ledger: dict,
             previous: dict | None) -> list[str]:
    """这一族在不在场。返回站点 id 列表（空 = 不在场，但行照常出现）。"""
    out: list[str] = []
    if fid == "nonliteral_index":
        out = [s["id"] for s in sites
               if s["kind"] == "index" and s.get("evidence") in (None, "none")]
    elif fid == "free_ownership":
        out = [s["id"] for s in sites
               if s["kind"] == "free" and not s.get("proved")]
    elif fid == "drop_honest":
        out = [s["id"] for s in sites if s["kind"] == "drop"]
    elif fid == "revocable":
        # E9 起以**站点**为准（`docs/262` §7.2）：词法计数只是旧产物的兜底。
        out = [s["id"] for s in sites if s["kind"] == "revoke"]
        if not out:
            out = [f"revocable:{i}" for i in range(int(facts.get("revocable", 0)))]
    elif fid == "extern":
        out = [f"extern:{i}" for i in range(int(facts.get("extern", 0)))]
    elif fid == "no_runtime":
        out = [f"runtime:{i}" for i in range(int(facts.get("runtime_sites", 0)))]
    elif fid == "gc_manual":
        out = [f"alloc:{i}" for i in range(int(facts.get("allocs", 0)))]
    elif fid == "nonliteral_guard":
        out = [s["id"] for s in sites if s["kind"] == "domain_access"
               and s.get("evidence") in (None, "none")]
        if not out:
            out = [f"guard:{i}" for i in range(int(facts.get("nonliteral_guard", 0)))]
    elif fid == "defer":
        out = [f"deferred:{d['site']}" for d in (previous or {}).get("deferred", [])]
    elif fid == "expired":
        out = [f"expired:{x}" for x in (previous or {}).get("expired", [])]
    elif fid == "cross_unit":
        out = [f"dep:{i}" for i in range(int(facts.get("cross_unit", 0)))]
    elif fid == "effect_unhandled":
        out = [s["id"] for s in sites
               if s["kind"] == "perform" and not s.get("proved")]
    elif fid == "effect_general_handler":
        out = [s["id"] for s in sites if s["kind"] == "handler_capture"]
    elif fid == "effect_foreign":
        out = [s["id"] for s in sites if s["kind"] == "effect_foreign"]
    elif fid == "audit_off":
        if ledger.get("state") == "lspe_close_admin" or ledger.get("lspc") == "off":
            out = [f"audit-off:{ledger.get('state', '?')}"]
        elif (previous or {}).get("close_admin"):
            out = ["audit-off:previous-build"]
    return out


def sheet(sites: list[dict], ledger: dict, dims: dict | None = None,
          facts: dict | None = None, previous: dict | None = None,
          mode: str = "full", acks: dict[str, str] | None = None) -> dict:
    """生成预检单。`mode=off` 也返回一张单（那一行 `audit_off` 会自己指出来）。"""
    dims = dims or {}
    facts = dict(facts or {})
    acks = acks or {}
    rows = []
    for fam in LSPE_LSPC_FAMILIES:
        fid = str(fam["id"])
        ids = _present(fid, sites, facts, ledger, previous)
        rows.append({
            "id": fid,
            "family": fam["family"],
            "protects": fam["protects"],
            "if_fails": fam["if_fails"],
            "fix": fam["fix"],
            "cost": fam["cost"],
            "bearer": fam["bearer"],
            "risk": fam["risk"],
            "present": bool(ids),
            "sites": ids,
            "acked": fid in acks and acks[fid].lower() == "ack",
        })
    n_ack = sum(1 for r in rows if r["acked"])
    blocking = [r["id"] for r in rows
                if mode == "strict" and r["present"] and r["risk"] == "high"
                and not r["acked"]]
    return {
        "mode": mode,
        "families": len(rows),
        "present": sum(1 for r in rows if r["present"]),
        "acked": n_ack,
        "blocking": blocking,
        "rows": rows,
    }


def render_text(sh: dict) -> list[str]:
    """五列的文本形态。`quiet` 只给总数（§7.3 规则 3）。"""
    if sh["mode"] == "quiet":
        return [f"[LSPC] {sh['present']} risk famil{'y' if sh['present'] == 1 else 'ies'} "
                f"present, {sh['acked']} acknowledged"]
    out = [f"[LSPC] pre-flight sheet ({sh['mode']}): {sh['families']} families, "
           f"{sh['present']} present, {sh['acked']} acknowledged"]
    for r in sh["rows"]:
        if not r["present"] and r["id"] != "audit_off":
            continue
        if r["id"] == "audit_off" and not r["present"]:
            mark = "ok"            # 审计开着：这一行照常出现，但说的是"没关"
        else:
            mark = "ack" if r["acked"] else ("!" if r["risk"] == "high" else "-")
        out.append(f"  [{mark}] {r['family']}")
        out.append(f"      现在靠: {r['protects']}")
        out.append(f"      不成立: {r['if_fails']}")
        out.append(f"      修法:   {r['fix']}")
        out.append(f"      不修的后果: {r['cost']}")
        out.append(f"      谁承担: {r['bearer']}"
                   + (f" — {len(r['sites'])} site(s)" if r["sites"] else ""))
    if sh["blocking"]:
        out.append("  [LSPC] strict 阻断: " + ", ".join(sh["blocking"]))
    return out


def previous_items(artifact: dict | None, side: dict | None,
                   current_trees: dict | None = None) -> dict:
    """跨构建记忆（`docs/262` §7.7 / `#760`）。

    * 上一轮的 `deferred` 计数 → 本轮的预检项；
    * 上一轮 `Assumed` 的站点，若内容寻址 id 在新构建里消失或哈希变了 → "过期"；
    * 上一轮 `lspe_close_admin` → "你上次没有审计"。

    比较用**内容寻址 id**，不用行号 —— 行号一动，整个机制就会把"没变"报成"变了"。
    """
    out: dict = {"deferred": [], "expired": [], "close_admin": False}
    if artifact:
        lp = artifact.get("lspe") or {}
        out["close_admin"] = lp.get("state") == "lspe_close_admin"
        out["deferred"] = [{"site": f"count:{i}",
                            "reason": "previous build deferred it"}
                           for i in range(int(lp.get("deferred", 0) or 0))]
    if side:
        prev = side.get("sites") or {}
        if current_trees is None:
            out["expired"] = sorted(prev)
        else:
            out["expired"] = sorted(
                sid for sid, ent in prev.items()
                if sid not in current_trees
                or current_trees[sid]["hash"] != ent.get("hash"))
    return out


def parse_acks(answers: dict[str, str]) -> dict[str, str]:
    """answers 文件里的 `lspc:<族>=ack|fix`（`#759`）。"""
    return {k.split(":", 1)[1]: v for k, v in answers.items()
            if k.startswith("lspc:") and ":" in k}
