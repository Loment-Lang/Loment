"""LSPE 对**安全系统自己**下的五条判据（`docs/262` §8）。

| # | 不变量 | 违反时 |
|---|---|---|
| I1 | 站点对账：普查集合 == 各组件登记集合 | E035 |
| I2 | 来源覆盖：每条 allow 有来源或 `Assumed` 标注 | E034 |
| I3 | 维度不矛盾：no_runtime 零运行期站点 / gc_manual 无未记账 free / sealed 无 hosted | E035 |
| I4 | 地板可数：floors == assumed + stale_wf + budget + depth + drop_honest + pass | E036 |
| I5 | 孪生同源：同站点两实现的判决 + 来源相同 | E035 |

**I5 是闸门，不是证明** —— 孪生一致只说明两侧在做同一件事，不说明那件事对
（`docs/253` §5.1.1）。所以它单独不能产生一个 `PROVED`。
"""

from __future__ import annotations

from .registry import LSPE_FLOOR_KEYS, LSPE_SITE_KINDS


def _label(site: dict) -> bool:
    """站点有没有"谁批准了这次放行"的记录（I2）。"""
    if site.get("source"):
        return True
    if site.get("assumed") or site.get("deferred") or site.get("refused"):
        return True
    if site.get("covered_by"):          # pass 块本身就是收据
        return True
    return False


def check(sites: list[dict], solver, ledger: dict, dims: dict | None = None,
          facts: dict | None = None, twin: dict | None = None,
          side_sets: dict[str, set[str]] | None = None) -> tuple[dict, list[dict]]:
    """返回 `({"i1".."i5"}, problems)`。`problems` 里每条形如
    `{"code": "E035"|"E036"|"E034", "message": ...}`。"""
    dims = dims or {}
    facts = facts or {}
    problems: list[dict] = []

    # ---- I1：站点对账 -------------------------------------------------------
    unknown = sorted({str(s["kind"]) for s in sites} - LSPE_SITE_KINDS)
    i1 = "fail" if unknown else "ok"
    if unknown:
        problems.append({"code": "E035",
                         "message": f"未登记的站点种类：{unknown}"})
    if side_sets:
        mine = {s["id"] for s in sites}
        other = set().union(*side_sets.values()) if side_sets else set()
        ghost = sorted(other - mine)
        missing = sorted(mine - other)
        if ghost or missing:
            i1 = "fail"
            problems.append({"code": "E035",
                             "message": f"站点对账不一致：幽灵 {ghost[:3]}，"
                                        f"漏登 {missing[:3]}"})

    # ---- I2：来源覆盖 -------------------------------------------------------
    # 判决以**求解器**为准，不看站点自带的 `proved` 字段：级别截断之后，
    # "站点说证了"和"这个级别上真的证了"可以是两件事。
    unlabeled = [s["id"] for s in sites
                 if not solver.proved(s) and not _label(s)]
    i2 = "fail" if unlabeled else "ok"
    if unlabeled:
        problems.append({"code": "E034",
                         "message": f"{len(unlabeled)} 个放行没有来源也没有标注："
                                    f"{unlabeled[:3]}"})

    # ---- I3：维度不矛盾 -----------------------------------------------------
    i3 = "ok"
    if dims.get("runtime") == "no_runtime" and int(facts.get("runtime_sites", 0)):
        i3 = "fail"
        problems.append({"code": "E035",
                         "message": "no_runtime 产物里出现了运行期站点"})
    if dims.get("port") == "sealed" and int(facts.get("hosted_calls", 0)):
        i3 = "fail"
        problems.append({"code": "E035",
                         "message": "sealed 产物里出现了 hosted 调用"})
    if dims.get("gc") == "gc_manual":
        bad = [s["id"] for s in sites
               if s["kind"] == "free" and not solver.proved(s) and not _label(s)]
        if bad:
            i3 = "fail"
            problems.append({"code": "E035",
                             "message": f"gc_manual 有未记账的 free：{bad[:3]}"})

    # ---- I4：地板可数 -------------------------------------------------------
    fd = ledger.get("floor_detail") or {}
    total = sum(int(fd.get(k, 0) or 0) for k in LSPE_FLOOR_KEYS)
    i4 = "ok" if total == int(ledger.get("floors", -1)) else "fail"
    if i4 == "fail":
        problems.append({"code": "E036",
                         "message": f"地板不可数：floors={ledger.get('floors')}，"
                                    f"明细和={total}"})
    # 不存在"既没回答、也没默认"的 UNDECIDED。
    if i4 == "ok":
        floating = [s["id"] for s in sites
                    if not solver.proved(s) and not s.get("covered_by")
                    and not (s.get("assumed") or s.get("deferred")
                             or s.get("refused") or s.get("question"))]
        if floating:
            i4 = "fail"
            problems.append({"code": "E036",
                             "message": f"{len(floating)} 个 UNDECIDED 既没回答也没默认："
                                        f"{floating[:3]}"})

    # ---- I5：孪生同源 -------------------------------------------------------
    i5 = "ok"
    if twin is not None:
        mine = {s["id"]: (bool(solver.proved(s)), s.get("source")) for s in sites}
        if mine != twin:
            diff = sorted(k for k in set(mine) | set(twin)
                          if mine.get(k) != twin.get(k))
            i5 = "fail"
            problems.append({"code": "E035",
                             "message": f"孪生判决/来源不一致：{diff[:3]}"})

    return ({"i1": i1, "i2": i2, "i3": i3, "i4": i4, "i5": i5}, problems)
