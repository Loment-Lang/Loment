"""证据子集格上的工作列表求解器（`docs/262` §5.3 / §5.5 / §5.12）。"""

from __future__ import annotations

from .derive import evidence_node
from .registry import LSPE_EVIDENCE_KINDS, LSPE_EVIDENCE_LEVEL, LSPE_OBLIGATIONS

#: 步数预算的默认值（`docs/262` §5.12）。**按步数，不按墙钟** —— 同一个量在墙钟上
#: 有 3.2×/4.13×/5.4× 的散布，预算若按秒就会"同一棵树在慢机器上结论不同"。
LSPE_DEFAULT_BUDGET = 65536

#: 过程间摘要的默认深度（`#750` 的 Q006）。
LSPE_DEFAULT_DEPTH = 6


class LspeSolver:
    """证据子集格上的工作列表（`docs/262` §5.3 / §5.5）。

    * 证据是 `LSPE_EVIDENCE_KINDS` 的**子集**，按包含关系构成有限格；
    * `Differential` 永远不在 accept 集合里 —— 它最多是闸门，不能销义务；
    * 队列按站点 id 排序，不是哈希序；
    * 步数预算与深度上限**确定性**地截断（`#750`）：耗尽记 `UNDECIDED(budget)` /
      `UNDECIDED(depth)`，绝不静默当作成功；
    * 传播边（`depends_on`）：被依赖的站点全部证成时，本站点获得 `Interproc` ——
      这是 E5（全局不变量）在求解器里的落点，站点自己带 `GlobalInvariant` 时不走这条。
    """

    def __init__(self, sites: list[dict], level: int = 3,
                 budget: int | None = None, depth: int | None = None) -> None:
        self.level = level
        self.order = sorted(sites, key=lambda s: s["id"])
        self.accept = {k: set(v["accept"]) for k, v in LSPE_OBLIGATIONS.items()}
        self.evidence = {s["id"]: self._initial(s) for s in self.order}
        self.reasons: dict[str, str | None] = {}
        self.budget = LSPE_DEFAULT_BUDGET if budget is None else max(0, int(budget))
        self.depth = LSPE_DEFAULT_DEPTH if depth is None else max(0, int(depth))
        self.steps = 0
        self.budget_hits: set[str] = set()
        self.depth_hits: set[str] = set()
        for s in self.order:
            if int(s.get("depth", 0) or 0) > self.depth:
                self.depth_hits.add(s["id"])
                self.reasons[s["id"]] = "depth"

    def _initial(self, site: dict) -> frozenset:
        # pass 块里的站点只记账，不参与证明（`docs/262` §5.16）。
        if site.get("covered_by"):
            return frozenset()
        # 替换之后没有重证的 WF（`#746`）：旧证据按 key 失效，绝不回退成 PROVED。
        if site.get("stale_wf"):
            return frozenset()
        ev = site.get("evidence")
        # 级别只缩不放（`docs/262` §5.6）：E2 的 Typed 与 E3 的 LocalFlow 从 L1 起，
        # E4 的 Interproc 从 L2 起，E5 的 GlobalInvariant 从 L3 起。
        if self.level < LSPE_EVIDENCE_LEVEL.get(ev, 0):
            return frozenset()
        if ev in (None, "none"):
            return frozenset()
        if ev in LSPE_EVIDENCE_KINDS:
            return frozenset({ev})
        return frozenset()

    def run(self) -> "LspeSolver":
        queue = list(self.order)
        by_id = {s["id"]: s for s in self.order}
        while queue:
            if self.steps >= self.budget:
                # 剩下的站点**逐条记成 budget**，不是"没处理就等于没事"。
                for s in queue:
                    self.budget_hits.add(s["id"])
                    self.reasons[s["id"]] = "budget"
                break
            site = queue.pop(0)
            self.steps += 1
            sid = site["id"]
            if sid in self.depth_hits:
                continue
            if site.get("stale_wf") and not site.get("redischarged"):
                self.reasons[sid] = "stale_wf"
                continue
            ev = set(self.evidence[sid])
            deps = [d for d in site.get("depends_on", []) if d in by_id]
            if deps and all(self._proved_id(d) for d in deps):
                ev.add("Interproc")
            self.evidence[sid] = frozenset(ev)
        return self

    def _proved_id(self, sid: str) -> bool:
        site = next((s for s in self.order if s["id"] == sid), None)
        if site is None:
            return False
        return bool(self.evidence.get(sid, frozenset())
                    & self.accept.get(site["obligation"], set()))

    def reason(self, site: dict) -> str | None:
        """这个站点为什么停在 UNDECIDED（`None` = 证成了）。"""
        sid = site["id"]
        if sid in self.depth_hits:
            return "depth"
        if sid in self.budget_hits:
            return "budget"
        if site.get("stale_wf") and not site.get("redischarged"):
            return "stale_wf"
        if self.proved(site):
            return None
        if site.get("covered_by"):
            return "pass"
        return site.get("reason") or "none"

    def proved(self, site: dict) -> bool:
        return bool(self.evidence[site["id"]]
                    & self.accept.get(site["obligation"], set()))

    def evidence_kinds(self, site: dict) -> frozenset:
        return self.evidence.get(site["id"], frozenset())

    def evidence_nodes(self, site: dict) -> list[dict]:
        """派生树里的证据节点（`docs/262` §5.9）。只有证成的站点才有节点。"""
        if not self.proved(site):
            return []
        source = site.get("source")
        if not source:
            return []
        kind = site.get("evidence")
        if kind in (None, "none"):
            kind = next(iter(sorted(self.evidence_kinds(site))))
        return [evidence_node(str(kind), str(source), site)]

    def floors(self) -> dict[str, int]:
        """`UNDECIDED` 按原因分桶（`docs/262` §8 / I4）。"""
        out = {"budget": 0, "depth": 0, "stale_wf": 0}
        for site in self.order:
            if self.proved(site):
                continue
            r = self.reason(site)
            if r in out:
                out[r] += 1
        return out
