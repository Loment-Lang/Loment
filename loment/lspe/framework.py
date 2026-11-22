"""LSPE 的框架面（`docs/344` V0-V4，`docs/361` SDK 1.0）。

这一层只搬**派发**：义务、证据、求解器和台账仍由原模块拥有。

顶层只有一个检查器 —— `MainChecker`（主检查器）。它自己承担 Producer 角色，
下辖域检查器（`DomainChecker` / E9）、借用检查器（`BorrowChecker` / E10）、
效应检查器（`EffectChecker` / E11）、GC 检查器（`GCChecker` / E12）等子检查器。
`CheckerRegistry` 是编译期登记表。**调度器属于 SDK**（`sdk.Scheduler`），
不在主检查器里：主检查器只接收 SDK 给的 `Plan`，按它调用子检查器。
Advisory 子类只能产问题，Evidence 插件还要过义务的 `accept` 集合；求解器仍是
唯一裁决者。

编译器只有一条入口：`lspe.audit(...)`（`LspeSdk` 会话）。SDK 持有调度器，算完
计划再把 `Plan` 交给主检查器；主检查器自己不会调度。`census()` 保留给包内与
旧判据。默认路径的站点集合、顺序与内容寻址 id 不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import effects as _effects
from . import loans as _loans
from .census import (census_bounds, census_constant, census_domain, census_drop,
                     census_e3, census_e4, census_frame, census_reclaim,
                     census_wf, site_id_factory)
from .registry import LSPE_OBLIGATIONS

PHASES = ("P0", "P1", "P2", "P3", "P4", "P5", "P6")
ROLES = ("Producer", "Evidence", "Gate", "Advisory", "Resolver", "Ledger")

#: 强制检查：事实缺失也跑，调度器不能把它们变成 `skipped`。
MANDATORY_OBLIGATIONS = frozenset({
    "TypeWellFormed", "NoRuntime", "InBounds", "InDomain",
})

#: 调用点的关键字补集。与 `census.LSPE_CALL_KW` 同一口径；这里复制是为了
#: `framework` 不在导入期反向依赖 `census`（后者会在函数里导入本模块）。
_CALL_KW = frozenset({
    "return", "if", "while", "match", "let", "const", "in", "else", "for",
    "fn", "struct", "enum", "impl", "trait", "pub", "use", "module", "choose",
    "capability", "excluded", "guard", "as", "mut", "extern", "true", "false",
    "self", "effect", "handle", "perform", "resume", "class", "extends",
})


@dataclass(frozen=True)
class EngineDesc:
    id: str
    phase: int
    role: str
    obligations: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    mandatory: bool = False
    budget: int = 4096
    version: int = 1

    def __post_init__(self) -> None:
        if self.phase < 0 or self.phase >= len(PHASES):
            raise ValueError(f"非法阶段 {self.phase}")
        if self.role not in ROLES:
            raise ValueError(f"非法角色 {self.role!r}")
        for ob in self.obligations:
            if ob not in LSPE_OBLIGATIONS:
                raise ValueError(f"未登记义务 {ob!r}（{self.id}）")


@dataclass(frozen=True)
class UnitFacts:
    """调度器的只读输入（`docs/344` §4.1）。"""

    flags: frozenset[str] = frozenset()
    dims: tuple[tuple[str, str], ...] = ()
    level: int = 3
    unit_hash: int = 0
    twin_available: bool = True
    probes: frozenset[str] = frozenset()

    def has(self, *names: str) -> bool:
        return any(n in self.flags for n in names)

    def dim(self, name: str) -> str:
        return dict(self.dims).get(name, "")

    def digest(self) -> dict:
        return {
            "flags": sorted(self.flags),
            "dims": list(self.dims),
            "level": int(self.level),
            "unit_hash": int(self.unit_hash),
            "twin": bool(self.twin_available),
            "probes": sorted(self.probes),
        }

    @classmethod
    def from_tokens(cls, toks: list, *, dims: dict | None = None,
                    level: int = 3, unit_hash: int = 0,
                    lms=frozenset(), instances: list | tuple = (),
                    slots: dict | None = None,
                    twin_available: bool = True,
                    probes: list | tuple = ()) -> "UnitFacts":
        """从最终 token 流推触发事实。**过近似**：拿不准就给 True。"""
        flags: set[str] = set()
        idents = [t.val for t in toks if getattr(t, "kind", "") == "ident"]
        puncts = [t.val for t in toks if getattr(t, "kind", "") == "punct"]

        if any(getattr(t, "kind", "") == "number" for t in toks):
            flags.add("literals")
        if "[" in puncts:
            flags.add("indices")
        if "guard" in idents:
            flags.add("guards")
        if any(x in idents for x in ("domain", "capability", "revoke")):
            flags.add("domains")
        if "excluded" in idents:
            flags.add("excluded")
        if "revocable" in idents:
            flags.add("revocable")
        if ":" in puncts:
            flags.add("types")
        if "&" in puncts:
            flags.add("borrows")
        if any(x in idents for x in ("let", "mut")) or "=" in puncts:
            flags.add("locals")
        if any(x in idents for x in ("for", "while")):
            flags.add("loops")
        if "<" in puncts:
            flags.add("generics")
        if any(x in idents for x in ("drop", "Drop")):
            flags.add("drop")
        if any(x in idents for x in ("effect", "perform", "handle", "resume")):
            flags.add("effects")
        if any(x in idents for x in ("class", "extends", "virtual", "override", "abstract")):
            flags.add("classes")
        if "free" in idents:
            flags.add("free")

        n = len(toks)
        for i, t in enumerate(toks):
            if (getattr(t, "kind", "") == "ident" and t.val not in _CALL_KW
                    and i + 1 < n and getattr(toks[i + 1], "kind", "") == "punct"
                    and toks[i + 1].val == "("):
                flags.add("calls")
                break
        if (("load8" in idents or "store8" in idents)
                and "lms_guard" in set(lms)):
            flags.add("bounds")
        if isinstance(slots, dict) and slots:
            if slots.get("effects") == "true":
                flags.add("effects")
            if slots.get("aicheck") == "true":
                flags.add("advisory")
        if twin_available:
            flags.add("twin")
        if probes:
            flags.add("probes")
        if instances:
            flags.add("generics")
        return cls(flags=frozenset(flags),
                   dims=tuple(sorted((dims or {}).items())),
                   level=int(level), unit_hash=int(unit_hash),
                   twin_available=bool(twin_available),
                   probes=frozenset(probes))


@dataclass(frozen=True)
class SkipRecord:
    engine: str
    reason: str


@dataclass(frozen=True)
class Plan:
    run: tuple[str, ...]
    skip: tuple[SkipRecord, ...]
    budgets: tuple[tuple[str, int], ...]
    plan_hash: int
    facts_hash: int
    level: int = 3
    dims: tuple[tuple[str, str], ...] = ()

    def runs(self, engine: str) -> bool:
        return engine in self.run

    def skip_reason(self, engine: str) -> str | None:
        return next((s.reason for s in self.skip if s.engine == engine), None)

    def to_dict(self) -> dict:
        return {
            "run": list(self.run),
            "skip": [{"engine": s.engine, "reason": s.reason} for s in self.skip],
            "budgets": [{"engine": e, "steps": b} for e, b in self.budgets],
            "plan_hash": self.plan_hash,
            "facts_hash": self.facts_hash,
            "level": self.level,
            "dims": [{"name": k, "value": v} for k, v in self.dims],
        }


@dataclass(frozen=True)
class EvidenceNode:
    kind: str
    source: str
    span: tuple[int, int, int, int] = (0, 0, 0, 0)
    plugin_id: str = ""
    plugin_version: int = 0
    plugin_hash: str = ""


@dataclass(frozen=True)
class Claim:
    site_id: str
    obligation: str
    evidence: tuple[EvidenceNode, ...]


@dataclass(frozen=True)
class AdvisoryQuestion:
    code: str
    message: str


@dataclass
class Ctx:
    toks: list
    unit: str
    facts: UnitFacts
    dims: dict = field(default_factory=dict)
    level: int = 3
    engines: dict[str, Callable[["Ctx"], list]] = field(default_factory=dict)
    cache: object | None = None
    budget: int = 0
    spent: int = 0
    budget_hit: bool = False
    plan: "Plan | None" = None

    def spend(self, steps: int = 1) -> bool:
        """检查器从本引擎预算里扣步数；耗尽返回 False 并记 Q005。"""
        steps = max(0, int(steps))
        if self.spent + steps > self.budget:
            self.budget_hit = True
            return False
        self.spent += steps
        return True


@dataclass
class HostResult:
    plan: Plan
    claims: list[Claim] = field(default_factory=list)
    questions: list[AdvisoryQuestion] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


@dataclass
class CensusStage:
    """一次普查的共享状态。子检查器只往这里追加自己那段。"""

    toks: list
    unit: str
    sid: Callable[[str, str], str]
    sites: list
    unsourced: list
    passes: list
    cover: Callable[[int], str | None]
    lms: frozenset = frozenset()
    instances: tuple = ()
    shadowed: dict | None = None
    scoped: bool = False
    effects: bool = False
    borrows: bool = False
    effects_out: dict | None = None
    loans_out: dict | None = None


@dataclass
class AuditResult:
    """主检查器的一次审计：计划 + 站点 + 两条报告面。"""

    plan: Plan
    sites: list[dict]
    unsourced: list[int]
    passes: list[dict]
    effects: dict | None = None
    loans: dict | None = None


class MacroChecker:
    """所有检查器的统一接口；子类实现 `check()` 和/或 `scan()`（`docs/344` §2.1）。"""

    desc: EngineDesc = EngineDesc("?", 2, "Evidence")

    def role(self) -> str:
        return self.desc.role

    def applies(self, facts: UnitFacts) -> bool:
        """默认看触发器；拿不准的检查器把 trigger 写宽，**过近似**。"""
        return (not self.desc.triggers) or facts.has(*self.desc.triggers)

    def check(self, ctx: Ctx) -> list:
        fn = ctx.engines.get(self.desc.id)
        return list(fn(ctx)) if fn is not None else []

    def scan(self, stage: CensusStage) -> None:
        """普查阶段；默认不是站点生产者（`docs/361` §2）。"""
        return None

    #: 插件身份（内置检查器为空）。Evidence 插件必须带齐三项（`docs/344` §5.2）。
    plugin_id = ""
    plugin_version = 0
    plugin_hash = ""


class ConstantChecker(MacroChecker):
    """E1/E2/E5 共用的词法遍：guard、下标、循环归纳。"""

    desc = EngineDesc("E1Constant", 2, "Evidence",
                      obligations=("InBounds", "InDomain"),
                      triggers=("indices", "literals"))

    def scan(self, stage: CensusStage) -> None:
        census_constant(stage.toks, stage.sid, stage.sites, stage.unsourced,
                        stage.cover)


class TypedChecker(MacroChecker):
    desc = EngineDesc("E2Typed", 2, "Evidence",
                      obligations=("InDomain", "CapabilityLive", "GuardHolds",
                                   "NoRuntime"),
                      triggers=("guards", "domains", "excluded", "revocable", "types"))


class BoundsChecker(MacroChecker):
    desc = EngineDesc("E2bBounds", 2, "Evidence",
                      obligations=("InBounds",), mandatory=True,
                      triggers=("bounds",))

    def scan(self, stage: CensusStage) -> None:
        census_bounds(stage.toks, stage.sid, stage.sites, stage.cover, stage.lms)


class LocalFlowChecker(MacroChecker):
    desc = EngineDesc("E3LocalFlow", 2, "Evidence",
                      obligations=("Alive", "Exclusive", "Initialized", "NotDangling",
                                   "FreeOwnership"),
                      triggers=("locals", "borrows", "guards", "calls"))

    def scan(self, stage: CensusStage) -> None:
        census_e3(stage.toks, stage.sid, stage.sites, stage.cover)


class InterprocChecker(MacroChecker):
    desc = EngineDesc("E4Interproc", 2, "Evidence",
                      obligations=("SummaryContract", "FreeOwnership"),
                      triggers=("calls",))

    def scan(self, stage: CensusStage) -> None:
        census_e4(stage.toks, stage.sid, stage.sites, (), stage.cover)


class GlobalInvariantChecker(MacroChecker):
    desc = EngineDesc("E5GlobalInvariant", 2, "Evidence",
                      obligations=("InDomain", "GuardHolds"),
                      triggers=("loops", "indices"))


class SubstitutionChecker(MacroChecker):
    desc = EngineDesc("E6Substitution", 2, "Evidence",
                      obligations=("TypeWellFormed",), mandatory=True,
                      triggers=("generics", "drop"), budget=8192)

    def scan(self, stage: CensusStage) -> None:
        census_drop(stage.toks, stage.sid, stage.sites)
        census_wf(stage.instances, stage.sid, stage.sites)


class DomainChecker(MacroChecker):
    """域检查器（E9）：域声明、`guard` 包含性、撤销。"""

    desc = EngineDesc("E9Domain", 2, "Evidence",
                      obligations=("InDomain", "CapabilityLive", "GuardHolds"),
                      triggers=("domains", "guards", "revocable"))

    def scan(self, stage: CensusStage) -> None:
        census_domain(stage.toks, stage.sid, stage.sites, stage.cover)


class BorrowChecker(MacroChecker):
    """借用检查器（E10，报告面）：loan 图、kill = 最后使用、冲突与悬垂。"""

    desc = EngineDesc("E10Loans", 2, "Evidence",
                      obligations=("Alive", "Exclusive", "NotDangling"),
                      triggers=("borrows",))

    def scan(self, stage: CensusStage) -> None:
        if stage.borrows:
            stage.loans_out = _loans.analyze(stage.toks, stage.unit)

    def analyze(self, toks: list, unit: str = "m") -> dict:
        return _loans.analyze(toks, unit)


class EffectChecker(MacroChecker):
    """效应检查器（E11，报告面）：行、处理器、`resume` 与续延捕获。"""

    desc = EngineDesc("E11Effects", 2, "Evidence",
                      obligations=("EffectAllowed", "HandlerExhaustive",
                                   "ResumeLinear", "EffectBounded",
                                   "CaptureReclaim"),
                      triggers=("effects",))

    def scan(self, stage: CensusStage) -> None:
        if not stage.effects:
            return
        rep = _effects.analyze(stage.toks, stage.unit)
        stage.effects_out = rep
        stage.sites.extend(rep.get("sites", []))


class GCChecker(MacroChecker):
    """GC 检查器（E12，`docs/353` §5）：每个 `alloc` 的回收判决指到唯一声明。"""

    desc = EngineDesc("E12GC", 2, "Evidence",
                      obligations=("ReclaimScoped",),
                      triggers=("locals", "loops", "free"))

    def scan(self, stage: CensusStage) -> None:
        if stage.scoped:
            census_reclaim(stage.toks, stage.sid, stage.sites, stage.cover,
                           stage.shadowed)


class DifferentialGate(MacroChecker):
    desc = EngineDesc("E7Differential", 4, "Gate", budget=0)


class FalsificationGate(MacroChecker):
    desc = EngineDesc("E8Falsification", 4, "Gate", budget=0)


class AdvisoryChecker(MacroChecker):
    desc = EngineDesc("A1Advisory", 2, "Advisory",
                      triggers=("advisory",), budget=1024)


class PluginChecker(MacroChecker):
    """第三方检查器：默认 Advisory；声明 Evidence 要过义务的 `accept` 集合。"""

    def __init__(self, desc: EngineDesc, fn: Callable[[Ctx], list], *,
                 plugin_id: str, plugin_version: int, plugin_hash: str = "") -> None:
        if not plugin_id or int(plugin_version) <= 0:
            raise ValueError("插件必须有 plugin_id 与正版本号")
        if desc.role == "Evidence" and not plugin_hash:
            raise ValueError("Evidence 插件必须带 plugin_hash")
        self.desc = desc
        self._fn = fn
        self.plugin_id = plugin_id
        self.plugin_version = int(plugin_version)
        self.plugin_hash = plugin_hash

    def check(self, ctx: Ctx) -> list:
        return list(self._fn(ctx))


#: 主检查器下面的内置子检查器。主检查器自己不在表里 —— 它是 P0 的生产者，
#: 不是自己下辖的对象（`docs/361` §1）。
BUILTIN_CHECKERS = (
    ConstantChecker(), TypedChecker(), BoundsChecker(), LocalFlowChecker(),
    InterprocChecker(), GlobalInvariantChecker(), SubstitutionChecker(),
    DomainChecker(), BorrowChecker(), EffectChecker(), GCChecker(),
    DifferentialGate(), FalsificationGate(), AdvisoryChecker(),
)


class CheckerRegistry:
    """编译期登记表。登记后按 `(phase, id)` 固定排序（`docs/344` §4.5）。"""

    #: `docs/344` §4.6：检查器数上限。超过就点名失败，不静默截断。
    MAX_CHECKERS = 64

    def __init__(self) -> None:
        self._by_id: dict[str, MacroChecker] = {}

    def register(self, checker: MacroChecker) -> None:
        if not isinstance(checker, MacroChecker):
            raise TypeError("只登记 MacroChecker 子类")
        if len(self._by_id) >= self.MAX_CHECKERS:
            raise ValueError(f"LSPE 检查器超过 {self.MAX_CHECKERS} 个")
        cid = checker.desc.id
        if cid in self._by_id:
            raise ValueError(f"检查器 {cid} 重复登记")
        self._by_id[cid] = checker

    def get(self, engine: str) -> MacroChecker:
        return self._by_id[engine]

    def checkers(self) -> tuple[MacroChecker, ...]:
        return tuple(sorted(self._by_id.values(),
                            key=lambda c: (c.desc.phase, c.desc.id)))

    def __len__(self) -> int:
        return len(self._by_id)


def evidence_allowed(obligation: str) -> bool:
    """第三方证据是否被这条义务接受（默认只有 Advisory）。"""
    spec = LSPE_OBLIGATIONS.get(obligation)
    if not spec:
        return False
    accept = set(spec["accept"])
    return bool(accept & {"External", "Plugin"})


class MainChecker(MacroChecker):
    """LSPE SDK 的唯一主检查器（`docs/361`）。

    它自己承担 Producer 角色（P0），下辖所有子检查器。**它不持有调度器**：
    `Scheduler` 属于 SDK，`audit()` / `run()` 要求调用方传入 `Plan`；没有计划
    就点名失败。子检查器分两用：`check()` 走 `MacroChecker` 统一接口产
    `Claim`/问题，`scan()` 走普查阶段产站点。计划（由 SDK 生成）仍是唯一的
    “谁跑谁不跑”的依据。
    """

    desc = EngineDesc("MainChecker", 0, "Producer", budget=0)

    #: 普查阶段的固定顺序（`docs/361` §2）。第一列是**任何**一个在计划里就触发
    #: 该阶段的引擎 id；第二列是该阶段的唯一所有者（共享词法遍只跑一次）。
    STAGES: tuple[tuple[tuple[str, ...], str], ...] = (
        (("E1Constant", "E2Typed", "E5GlobalInvariant"), "E1Constant"),
        (("E9Domain",), "E9Domain"),
        (("E2bBounds",), "E2bBounds"),
        (("E3LocalFlow",), "E3LocalFlow"),
        (("E4Interproc",), "E4Interproc"),
        (("E6Substitution",), "E6Substitution"),
        (("E12GC",), "E12GC"),
        # E11/E10 是报告面，追加在普查之后：与旧编译器“普查 + 追加效应”的
        # 站点顺序逐字节相同。
        (("E11Effects",), "E11Effects"),
        (("E10Loans",), "E10Loans"),
    )

    def __init__(self, plugins: tuple[MacroChecker, ...] = ()) -> None:
        self.checker = CheckerRegistry()
        for c in BUILTIN_CHECKERS:
            self.checker.register(c)
        for p in plugins:
            self.checker.register(p)

    # ---- 子检查器 -------------------------------------------------------
    def sub(self, engine: str) -> MacroChecker:
        return self.checker.get(engine)

    @property
    def subcheckers(self) -> tuple[MacroChecker, ...]:
        """按 `(phase, id)` 固定排序的全部子检查器。"""
        return self.checker.checkers()

    @property
    def domain_checker(self) -> MacroChecker:
        return self.sub("E9Domain")

    @property
    def borrow_checker(self) -> MacroChecker:
        return self.sub("E10Loans")

    # ---- 执行（计划由 SDK 传入；主检查器不持有调度器） -------------------
    def run(self, ctx: Ctx, plan: Plan | None = None) -> HostResult:
        plan = plan or ctx.plan
        if plan is None:
            raise ValueError("MainChecker 不持有调度器：请由 SDK 传入 Plan")
        result = HostResult(plan)
        budgets = dict(plan.budgets)
        for cid in plan.run:
            checker = self.checker.get(cid)
            ctx.budget = budgets.get(cid, 0)
            ctx.spent = 0
            ctx.budget_hit = False
            produced = checker.check(ctx) or []
            if ctx.budget_hit:
                # 预算是**分配**，不静默成功（`docs/344` §4.6 / 判据 I）。
                result.questions.append(AdvisoryQuestion(
                    "Q005", f"{cid} 的预算 {ctx.budget} 步耗尽"))
            for item in produced:
                if isinstance(item, Claim):
                    self._accept_claim(checker, item, result)
                elif isinstance(item, AdvisoryQuestion):
                    result.questions.append(item)
                else:
                    result.errors.append({
                        "code": "E_FRAMEWORK_RESULT",
                        "message": f"{cid} 返回了 {type(item).__name__}",
                    })
        return result

    def check(self, ctx: Ctx) -> list:
        """`MacroChecker` 统一接口：主检查器是所有子检查器的组合。"""
        result = self.run(ctx)
        if result.errors:
            raise ValueError(result.errors[0]["message"])
        return list(result.claims) + list(result.questions)

    def _accept_claim(self, checker: MacroChecker, claim: Claim, result: HostResult) -> None:
        if checker.role() == "Advisory":
            result.errors.append({
                "code": "E_FRAMEWORK_ADVISORY_CLAIM",
                "message": f"Advisory {checker.desc.id} 不能写证据（{claim.site_id}）",
            })
            return
        if claim.obligation not in LSPE_OBLIGATIONS:
            result.errors.append({
                "code": "E_FRAMEWORK_UNKNOWN_OBLIGATION",
                "message": f"未登记义务 {claim.obligation}（{checker.desc.id}）",
            })
            return
        if checker.plugin_id and not evidence_allowed(claim.obligation):
            result.errors.append({
                "code": "E_FRAMEWORK_PLUGIN_NOT_OPT_IN",
                "message": f"义务 {claim.obligation} 不接受外部证据"
                           f"（{checker.plugin_id}）",
            })
            return
        result.claims.append(claim)

    # ---- 普查 / 审计（计划由 SDK 传入） --------------------------------
    def _scan(self, toks: list, unit: str, *, plan: Plan | None,
              lms, instances, shadowed, scoped: bool, effects: bool,
              borrows: bool) -> CensusStage:
        sid = site_id_factory(unit)
        spans, cover = census_frame(toks, sid)
        stage = CensusStage(
            toks=toks, unit=unit, sid=sid, sites=[], unsourced=[],
            passes=spans, cover=cover, lms=frozenset(lms or ()),
            instances=tuple(instances or ()), shadowed=shadowed,
            scoped=bool(scoped), effects=bool(effects), borrows=bool(borrows))
        run = None if plan is None else set(plan.run)
        for ids, owner in self.STAGES:
            if run is not None and not (run & set(ids)):
                continue
            if owner == "E12GC" and not stage.scoped:
                continue
            self.checker.get(owner).scan(stage)
        for s in stage.sites:
            cid = s.get("covered_by")
            if cid:
                for ps in stage.passes:
                    if ps["id"] == cid:
                        ps["sites"] += 1
        return stage

    def census(self, toks: list, unit: str, *, lms=frozenset(), instances=(),
               plan: Plan | None = None,
               shadowed: dict[str, frozenset[str]] | None = None,
               scoped: bool = False) -> tuple[list[dict], list[int], list[dict]]:
        """旧普查入口；内部仍由主检查器跑同一组子检查器。"""
        st = self._scan(toks, unit, plan=plan, lms=lms, instances=instances,
                        shadowed=shadowed, scoped=scoped, effects=False,
                        borrows=False)
        return st.sites, st.unsourced, st.passes

    def audit(self, toks: list, unit: str, *, plan: Plan,
              lms=frozenset(), instances=(), scoped: bool = False,
              shadowed: dict[str, frozenset[str]] | None = None,
              effects: bool = False,
              borrows: bool = False) -> AuditResult:
        """按 SDK 给的 `plan` 跑子检查器；主检查器不自己调度。

        `effects=True` 才追加 E11 站点；`borrows=True` 才跑 E10 的报告面
        （它今天不写台账，默认不付这份成本）。
        """
        st = self._scan(toks, unit, plan=plan, lms=lms, instances=instances,
                        shadowed=shadowed, scoped=scoped,
                        effects=bool(effects), borrows=bool(borrows))
        return AuditResult(plan=plan, sites=st.sites, unsourced=st.unsourced,
                           passes=st.passes, effects=st.effects_out,
                           loans=st.loans_out)
