"""LSPE 的框架面（`docs/344` V0-V3）。

这一层只搬**派发**：义务、证据、求解器和台账仍由原模块拥有。`MacroChecker`
定义统一接口，`CheckerRegistry` 是编译期登记表，`Scheduler` 按适用性给出
`Plan`，`LspeHost` 按计划调用子类。Advisory 子类只能产问题，Evidence 插件
还要过义务的 `accept` 集合；求解器仍是唯一裁决者。

默认路径不依赖本模块：`census()` 不传 `plan` 时按旧顺序跑全部阶段。编译器
在 `load()` 里算一次计划并传进来；计划本身进台账的 `_plan` 内部字段（`public()`
会剥掉），所以 Potato 产物不变。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

from .registry import LSPE_OBLIGATIONS
from .solver import LSPE_DEFAULT_BUDGET

#: in-tree SDK 的版本。公开 1.0 要等义务/证据冻结（`docs/344` §5.4）。
LSPE_SDK_VERSION = "lspe-sdk/0"

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


class MacroChecker:
    """所有检查器的统一接口；子类只实现 `check()`（`docs/344` §2.1）。"""

    desc: EngineDesc = EngineDesc("?", 2, "Evidence")

    def role(self) -> str:
        return self.desc.role

    def applies(self, facts: UnitFacts) -> bool:
        """默认看触发器；拿不准的检查器把 trigger 写宽，**过近似**。"""
        return (not self.desc.triggers) or facts.has(*self.desc.triggers)

    def check(self, ctx: Ctx) -> list:
        fn = ctx.engines.get(self.desc.id)
        return list(fn(ctx)) if fn is not None else []

    #: 插件身份（内置检查器为空）。Evidence 插件必须带齐三项（`docs/344` §5.2）。
    plugin_id = ""
    plugin_version = 0
    plugin_hash = ""


class ProducerChecker(MacroChecker):
    desc = EngineDesc("P0Producer", 0, "Producer", budget=0)


class E1Constant(MacroChecker):
    desc = EngineDesc("E1Constant", 2, "Evidence",
                      obligations=("InBounds", "InDomain"),
                      triggers=("indices", "literals"))


class E2Typed(MacroChecker):
    desc = EngineDesc("E2Typed", 2, "Evidence",
                      obligations=("InDomain", "CapabilityLive", "GuardHolds",
                                   "NoRuntime"),
                      triggers=("guards", "domains", "excluded", "revocable", "types"))


class E2bBounds(MacroChecker):
    desc = EngineDesc("E2bBounds", 2, "Evidence",
                      obligations=("InBounds",), mandatory=True,
                      triggers=("bounds",))


class E3LocalFlow(MacroChecker):
    desc = EngineDesc("E3LocalFlow", 2, "Evidence",
                      obligations=("Alive", "Exclusive", "Initialized", "NotDangling",
                                   "FreeOwnership"),
                      triggers=("locals", "borrows", "guards", "calls"))


class E4Interproc(MacroChecker):
    desc = EngineDesc("E4Interproc", 2, "Evidence",
                      obligations=("SummaryContract", "FreeOwnership"),
                      triggers=("calls",))


class E5GlobalInvariant(MacroChecker):
    desc = EngineDesc("E5GlobalInvariant", 2, "Evidence",
                      obligations=("InDomain", "GuardHolds"),
                      triggers=("loops", "indices"))


class E6Substitution(MacroChecker):
    desc = EngineDesc("E6Substitution", 2, "Evidence",
                      obligations=("TypeWellFormed",), mandatory=True,
                      triggers=("generics", "drop"), budget=8192)


class E9Domain(MacroChecker):
    desc = EngineDesc("E9Domain", 2, "Evidence",
                      obligations=("InDomain", "CapabilityLive", "GuardHolds"),
                      triggers=("domains", "guards", "revocable"))


class E10Loans(MacroChecker):
    desc = EngineDesc("E10Loans", 2, "Evidence",
                      obligations=("Alive", "Exclusive", "NotDangling"),
                      triggers=("borrows",))


class E11Effects(MacroChecker):
    desc = EngineDesc("E11Effects", 2, "Evidence",
                      obligations=("EffectAllowed", "HandlerExhaustive",
                                   "ResumeLinear", "EffectBounded",
                                   "CaptureReclaim"),
                      triggers=("effects",))


class E7Differential(MacroChecker):
    desc = EngineDesc("E7Differential", 4, "Gate", budget=0)


class E8Falsification(MacroChecker):
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


BUILTIN_CHECKERS = (
    ProducerChecker(),
    E1Constant(), E2Typed(), E2bBounds(), E3LocalFlow(), E4Interproc(),
    E5GlobalInvariant(), E6Substitution(), E9Domain(), E10Loans(), E11Effects(),
    E7Differential(), E8Falsification(), AdvisoryChecker(),
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


class Scheduler:
    """适用性 / 顺序 / 预算 / 跳过原因。它不改变求解器（`docs/344` §4）。"""

    def plan(self, facts: UnitFacts, registry: CheckerRegistry) -> Plan:
        run: list[str] = []
        skip: list[SkipRecord] = []
        for checker in registry.checkers():
            reason = self._skip_reason(checker, facts)
            if reason is None:
                run.append(checker.desc.id)
            else:
                skip.append(SkipRecord(checker.desc.id, reason))
        budgets: list[tuple[str, int]] = []
        remaining = LSPE_DEFAULT_BUDGET
        for cid in run:
            desc = registry.get(cid).desc
            take = max(0, min(int(desc.budget), remaining))
            budgets.append((cid, take))
            remaining -= take
        payload = {
            "run": run,
            "skip": [[s.engine, s.reason] for s in skip],
            "budgets": budgets,
            "facts": facts.digest(),
        }
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return Plan(tuple(run), tuple(skip), tuple(budgets),
                    int(digest[:16], 16), int(hashlib.sha256(
                        json.dumps(facts.digest(), sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
                    ).hexdigest()[:16], 16),
                    level=facts.level, dims=facts.dims)

    def _skip_reason(self, checker: MacroChecker, facts: UnitFacts) -> str | None:
        desc = checker.desc
        if desc.mandatory:
            # 强制集永不 skipped；事实缺失让引擎自己报 Q001/Q002。
            # **只有显式 mandatory 的检查器**（E2b/E6）走这条；按义务表把整个引擎
            # 拉进强制集会违背"触发器不成立就没有站点"的过近似。
            return None
        if desc.id == "E7Differential":
            return None if facts.twin_available else "twin_missing"
        if desc.id == "E8Falsification":
            return None if facts.probes else "no_probe"
        if desc.id == "E10Loans" and facts.dim("lifetime") == "lifetime_no":
            return "profile"
        if desc.id == "A1Advisory" and not facts.has("advisory"):
            return "profile"
        if desc.triggers and not checker.applies(facts):
            return "not_applicable"
        return None


def evidence_allowed(obligation: str) -> bool:
    """第三方证据是否被这条义务接受（默认只有 Advisory）。"""
    spec = LSPE_OBLIGATIONS.get(obligation)
    if not spec:
        return False
    accept = set(spec["accept"])
    return bool(accept & {"External", "Plugin"})


class LspeHost:
    """宏观检查器实例：持有登记表与调度器（`docs/344` §2.1）。"""

    def __init__(self, plugins: tuple[MacroChecker, ...] = ()) -> None:
        self.checker = CheckerRegistry()
        for c in BUILTIN_CHECKERS:
            self.checker.register(c)
        for p in plugins:
            self.checker.register(p)
        self.scheduler = Scheduler()

    def plan(self, facts: UnitFacts) -> Plan:
        return self.scheduler.plan(facts, self.checker)

    def run(self, ctx: Ctx, plan: Plan | None = None) -> HostResult:
        plan = plan or self.plan(ctx.facts)
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


def default_host() -> LspeHost:
    return LspeHost()


def plan_for_tokens(toks: list, unit: str, *, dims: dict | None = None,
                    level: int = 3, lms=frozenset(), instances=(),
                    slots: dict | None = None, twin_available: bool = True,
                    probes=()) -> Plan:
    facts = UnitFacts.from_tokens(toks, dims=dims, level=level, lms=lms,
                                  instances=instances, slots=slots,
                                  twin_available=twin_available, probes=probes)
    return default_host().plan(facts)


def plan_lines(plan: Plan) -> list[str]:
    """`loment lspe state` 的调度面：计划、跳过原因与预算。"""
    run = ",".join(plan.run) if plan.run else "-"
    out = [f"[LSPE] plan {plan.plan_hash:016x} level={plan.level} run={run}"]
    for s in plan.skip:
        out.append(f"[LSPE] skip {s.engine}: {s.reason}")
    out.append("[LSPE] budgets " + ", ".join(f"{e}={n}" for e, n in plan.budgets))
    return out
