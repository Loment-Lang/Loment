"""LSPE 台账（`docs/262` §8）：从站点与级别算出 Potato v16 的 `lspe` 对象。

这是 LSPE 的第四件。编译器只负责把这个映射写进产物；计数、`per_kind`、不变量和
`lspe_close_admin` 的归零都在这里。
"""

from __future__ import annotations

from . import invariants as _inv
from .census import census_e4, census_wf
from .lspc import default_mode as _lspc_default
from .questions import ask as _ask
from .registry import LSPE_PROBED_OBLIGATIONS
from .solver import LspeSolver

#: 旧名字（`#766` 之前）：现在唯一真源在 `registry.LSPE_PROBED_OBLIGATIONS`。
LSPE_FALSIFICATION_OBLIGATIONS = LSPE_PROBED_OBLIGATIONS


def build(*, sites: list[dict], state: str, set_by: str, level: int,
          toks: list, unit: str, deps: list[list] = (),
          passes: list[dict] = (), instances: list[dict] = (),
          mode: str = "full", answers: dict[str, str] | None = None,
          dims: dict | None = None, facts: dict | None = None,
          budget: int | None = None, depth: int | None = None,
          lspc_mode: str | None = None, previous: dict | None = None,
          sidecar: bool = False, twin: dict | None = None,
          side_sets: dict[str, set[str]] | None = None,
          cache=None) -> dict:
    """返回 Potato v16 的 `lspe` 台账。

    * `sites` 是加载期算出的站点；`lspe_close_admin` 下**关审计** → 计数全零，
      但 `state` / `set_by` 仍然写明（"关的是警察，不是法律"）。
    * `deps` 是依赖单元的 token 流：E4 的安全表要看**根 + 依赖**（自举侧
      `compute_safe` 扫整个单元），所以发射这一层重算 `SummaryContract` 站点。
    * `passes` 是 `lspe pass "reason" { … }` 的块：里头的站点记 `covered_by`，
      **只记账、不证明**；`floors.pass` 只数最外层块。
    * `instances` 是单态化实例：每个实例一条 `TypeWellFormed`（`#745`）。
    * `mode` 是 `full` / `quiet` / `strict`；`answers` 是反问器的回答通道（`#755`）。
    * `sidecar=True` 时额外算 `proof_root`（`#748` / `#749`）；默认不算 —— 逐字节
      孪生比较的产物不带它，自举侧不需要复刻派生树。
    """
    answers = answers or {}
    dims = dict(dims or {})
    facts = dict(facts or {})
    if state == "lspe_close_admin":
        sites = []
        passes = []
    else:
        if deps:
            # ⚠ 判据是 `deps`，不是 `deps and sites`（`#1081`）：根里一条加载期站点都
            # 没有（比如只调了依赖里的 `ptr` 首参函数）时 `sites` 是空的 —— 旧条件
            # 会把整段重算跳过，于是**每一个调用点都从台账里消失**。
            sites = [s for s in sites if s["kind"] != "call"]
            # `_fnv1a32` 是内容寻址的实现细节，仍由编译器提供（它也用于能力域哈希）。
            from lomentc import _fnv1a32

            seen: dict[str, int] = {}

            def sid(kind: str, key: str) -> str:
                occ = seen.get(key, 0)
                seen[key] = occ + 1
                return f"{unit}:{kind}:{_fnv1a32(key + '#' + str(occ)):08x}"

            outer = [p for p in passes if p.get("outer", True)]

            def cover(i: int) -> str | None:
                for p in outer:
                    if p["open"] < i < p["close"]:
                        return p["id"]
                return None

            census_e4(toks, sid, sites, list(deps), cover)
        # 单态化实例 = 一次替换（`#745`）；没有实例就没有 WF 站点。
        if instances:
            def sid_new(kind: str, key: str) -> str:
                return f"{unit}:{kind}:{_sid_hash(key):08x}"
            census_wf(instances, sid_new, sites)

    # WF 义务的键**必须**带 substitution（`#745` 验收第 2 条）：没有键的 WF 站点是
    # "良构但不知道在哪个替换下"—— 那正是 #164250 的形状，一律拒。
    for s in sites:
        if s.get("kind") == "wf" and not s.get("subst"):
            raise ValueError(f"TypeWellFormed 站点 {s.get('id')!r} 没有 substitution 键")
    _apply_hand_proofs(sites, answers)
    solver = LspeSolver(sites, level, budget=budget, depth=depth).run()
    asked = {"questions": [], "assumed": [], "deferred": [], "refused": [],
             "errors": []}
    if state != "lspe_close_admin":
        asked = _ask(sites, solver, answers, mode)

    by_kind: dict[str, list[int]] = {}
    for site in sites:
        # `per_kind` 按**义务种类**计，不按站点种类计（`docs/262` §5.11）。
        pair = by_kind.setdefault(site["obligation"], [0, 0])
        pair[0] += 1
        if solver.proved(site):
            pair[1] += 1
    n = len(sites)
    proved = sum(1 for site in sites if solver.proved(site))
    passed = sum(1 for site in sites if site.get("covered_by"))
    outside = [p for p in passes if p.get("outer", True)]
    ratio = f"{passed}/{n}" if n else "0/0"
    drop_sites = [s for s in sites if s["kind"] == "drop"]
    drop_floors = sum(1 for s in drop_sites
                      if s.get("evidence") == "Assumed"
                      and str(s.get("source", "")).startswith("#floor-drop-"))
    # 地板按**原因**分桶，但一个站点只进一个桶：被回答/默认过的站点算 `assumed`，
    # 不再算 budget/depth/stale —— 否则同一条 UNDECIDED 会被数两次，I4 恒等式就假了。
    sf = {"budget": 0, "depth": 0, "stale_wf": 0}
    for site in sites:
        if solver.proved(site) or site.get("covered_by"):
            continue
        if site.get("assumed") or site.get("deferred") or site.get("refused"):
            continue
        reason = solver.reason(site)
        if reason in sf:
            sf[reason] += 1
    floor_detail = {
        "assumed": len(asked["assumed"]),
        "stale_wf": int(sf.get("stale_wf", 0)),
        "budget": int(sf.get("budget", 0)),
        "depth": int(sf.get("depth", 0)),
        "drop_honest": drop_floors,
        "pass": len(outside),
    }
    floors = sum(floor_detail.values())
    fals = sum(1 for s in sites if s["obligation"] in LSPE_FALSIFICATION_OBLIGATIONS)
    ledger = {
        "state": state,
        "set_by": set_by,
        "mode": mode,
        "level": level,
        "sites": n,
        "obligations": n,
        "proved": proved,
        "undecided": n - proved,
        "deferred": len(asked["deferred"]),
        "refused": len(asked["refused"]),
        "floors": floors,
        "floor_detail": floor_detail,
        "per_kind": {k: f"{v[1]}/{v[0]}" for k, v in sorted(by_kind.items()) if v[0]},
        "interrogations": {"provenance": proved, "falsification": fals,
                           "cross_exam": n},
        # 五条不变量在下面算；键位留在这里 —— 两侧逐字节比，次序也是判据。
        "invariants": None,
        # pass 块的收据：id + 跨度 + 理由 + 覆盖的站点数（`docs/262` §5.16）。
        "passes": [{"id": p["id"], "line": p["line"], "end_line": p["end_line"],
                    "reason": p["reason"], "sites": p["sites"]} for p in outside],
        "pass_sites": len(outside),
        "passed_sites": passed,
        "pass_ratio": ratio,
        "lspc": (lspc_mode if lspc_mode is not None
                 else _lspc_default(state, mode)),
        "acked": len([k for k, v in answers.items()
                      if k.startswith("lspc:") and v.lower() == "ack"]),
    }
    inv, problems = _inv.check(sites, solver, ledger, dims, facts, twin, side_sets)
    ledger["invariants"] = ({"i1": "not_run", "i2": "not_run", "i3": "not_run",
                             "i4": "not_run", "i5": "not_run"}
                            if state == "lspe_close_admin" else inv)
    # 反问器的错误（E034/E036）也要进自检 —— 否则一条没记上的问题会静默消失。
    for e in asked.get("errors", []):
        problems.append({"code": e.get("code", "E036"), "message": e.get("message", "")})
    ledger["_problems"] = problems
    ledger["_questions"] = asked["questions"]
    ledger["_deferred"] = asked["deferred"]
    ledger["_refused"] = asked["refused"]
    ledger["_assumed"] = asked["assumed"]
    if sidecar or cache is not None:
        from .derive import build_trees, proof_root
        trees = build_trees(sites, solver)
        if sidecar:
            ledger["proof_root"] = proof_root(trees)
        ledger["_trees"] = trees
        if cache is not None:
            # 命中的 `proof_hash` 必须与重算相同；不同就是硬错误（`#752`）。
            for site in sites:
                ent = trees.get(site["id"])
                if ent:
                    cache.check(site, ent["hash"])
                cache.put(site, {"verdict": ("PROVED" if solver.proved(site)
                                             else "UNDECIDED"),
                                 "reason": solver.reason(site),
                                 "hash": (ent or {}).get("hash")})
            cache.save()
    return ledger


def _sid_hash(key: str) -> int:
    from .derive import fnv1a32
    return fnv1a32(key + "#0")


def _apply_hand_proofs(sites: list[dict], answers: dict[str, str]) -> None:
    """`drop:<类型>=prove` 是**手写证明**（`#747`）：证据换成 `Syntactic`，
    来源记成 `hand-proof:<类型>`，`floor_detail.drop_honest` 随之减一。"""
    for site in sites:
        if site["kind"] != "drop":
            continue
        ans = answers.get(f"drop:{site.get('destructor')}")
        if ans and ans.strip().lower() in ("prove", "proof", "a"):
            site["evidence"] = "Syntactic"
            site["source"] = f"hand-proof:{site.get('destructor')}"


def public(ledger: dict) -> dict:
    """产物里能看到的台账：去掉 `_` 开头的内部字段（问题、派生树、错误）。
    这些走 `.lspe` 旁挂与 CLI，不进 Potato —— 产物保持小，孪生逐字节比得上。"""
    return {k: v for k, v in ledger.items() if not k.startswith("_")}


def lspc_lines(ledger: dict) -> list[str]:
    """LSPC 的**贴条**（`docs/262` §5.10 / §5.16 / `#786`）。

    第一条是**族级**的 —— 即使这一份单元一个旁路块都没有也照写：LSPC 从不沉默，
    "0 个未审计块"本身就是一条可核的声明。之后每个块一条 **Q013**。
    """
    n = ledger.get("pass_sites", 0)
    passed = ledger.get("passed_sites", 0)
    total = ledger.get("sites", 0)
    out = [f"[LSPC] Q013 unchecked-block family: {n} block(s), "
           f"{passed}/{total} site(s) bypassed"]
    for p in ledger.get("passes", []):
        out.append(f"[LSPC] Q013 {p['id']} line {p['line']}-{p['end_line']}: "
                   f"{p['sites']} site(s) — {p['reason']}")
    if ledger.get("lspc") == "off":
        out.append("[LSPC] the pre-flight sheet is OFF (recorded) — "
                   "this is the largest unlabelled floor in the system")
    return out
