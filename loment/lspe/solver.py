"""证据子集格上的工作列表求解器（`docs/262` §5.3 / §5.5）。"""

from __future__ import annotations

from .registry import LSPE_EVIDENCE_KINDS, LSPE_EVIDENCE_LEVEL, LSPE_OBLIGATIONS


class LspeSolver:
    """证据子集格上的工作列表（`docs/262` §5.3 / §5.5）。

    P0 每个站点只有一条义务、没有传播边；但队列顺序与"只做并集"的单调性已经按最终
    形状写死。接 E5 的传播边时只加 successors，不改求解器，也不改确定性的那几条规矩。

    * 证据是 `LSPE_EVIDENCE_KINDS` 的**子集**，按包含关系构成有限格；
    * `Differential` 永远不在 accept 集合里 —— 它最多是闸门，不能销义务；
    * 队列按站点 id 排序，不是哈希序。
    """

    def __init__(self, sites: list[dict], level: int = 3) -> None:
        self.level = level
        self.order = sorted(sites, key=lambda s: s["id"])
        self.accept = {k: set(v["accept"]) for k, v in LSPE_OBLIGATIONS.items()}
        self.evidence = {s["id"]: self._initial(s) for s in self.order}

    def _initial(self, site: dict) -> frozenset:
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
        while queue:
            site = queue.pop(0)
            # 目前没有传播边；保留 worklist 形状，E5 在这里加 successors。
            self.evidence[site["id"]] = frozenset(self.evidence[site["id"]])
        return self

    def proved(self, site: dict) -> bool:
        return bool(self.evidence[site["id"]] & self.accept.get(site["obligation"], set()))
