"""LSPE 台账（`docs/262` §8）：从站点与级别算出 Potato v16 的 `lspe` 对象。

这是 LSPE 的第四件。编译器只负责把这个映射写进产物；计数、`per_kind`、不变量和
`lspe_close_admin` 的归零都在这里。
"""

from __future__ import annotations

from .census import census_e4
from .registry import LSPE_SITE_KINDS
from .solver import LspeSolver


def build(*, sites: list[dict], state: str, set_by: str, level: int,
          toks: list, unit: str, deps: list[list] = ()) -> dict:
    """返回 Potato v16 的 `lspe` 台账。

    * `sites` 是加载期算出的站点；`lspe_close_admin` 下**关审计** → 计数全零，
      但 `state` / `set_by` 仍然写明（"关的是警察，不是法律"）。
    * `deps` 是依赖单元的 token 流：E4 的安全表要看**根 + 依赖**（自举侧
      `compute_safe` 扫整个单元），所以发射这一层重算 `SummaryContract` 站点。
    """
    if state == "lspe_close_admin":
        sites = []
    elif deps and sites:
        sites = [s for s in sites if s["kind"] != "call"]
        # `_fnv1a32` 是内容寻址的实现细节，仍由编译器提供（它也用于能力域哈希）。
        from lomentc import _fnv1a32

        seen: dict[str, int] = {}

        def sid(kind: str, key: str) -> str:
            occ = seen.get(key, 0)
            seen[key] = occ + 1
            return f"{unit}:{kind}:{_fnv1a32(key + '#' + str(occ)):08x}"

        census_e4(toks, sid, sites, list(deps))

    solver = LspeSolver(sites, level).run()
    by_kind: dict[str, list[int]] = {}
    for site in sites:
        # `per_kind` 按**义务种类**计，不按站点种类计（`docs/262` §5.11）。
        pair = by_kind.setdefault(site["obligation"], [0, 0])
        pair[0] += 1
        if solver.proved(site):
            pair[1] += 1
    n = len(sites)
    proved = sum(1 for site in sites if solver.proved(site))
    return {
        "state": state,
        "set_by": set_by,
        "mode": "full",
        "level": level,
        "sites": n,
        "obligations": n,
        "proved": proved,
        "undecided": n - proved,
        "deferred": 0,
        "refused": 0,
        "floors": 0,
        "floor_detail": {"assumed": 0, "stale_wf": 0, "budget": 0, "drop_honest": 0},
        "per_kind": {k: f"{v[1]}/{v[0]}" for k, v in sorted(by_kind.items()) if v[0]},
        # I1 = 站点对账（今天只对"种类都认识"这一半；跨组件那份在 #761）。
        "invariants": {
            "i1": ("ok" if all(s["kind"] in LSPE_SITE_KINDS for s in sites) else "fail"),
            "i2": "not_run", "i3": "not_run", "i4": "not_run", "i5": "not_run",
        },
        "acked": 0,
    }
