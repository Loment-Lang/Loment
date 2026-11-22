"""LSPE SDK 1.0 —— 稳定面（`docs/361`）。

SDK 持有两件东西：**调度器**（`Scheduler`）与**唯一的主检查器**
（`MainChecker`）。主检查器下辖域检查器、借用检查器、效应检查器、GC 检查器
等子检查器；它自己**不持有调度器** —— 计划由 SDK 算好再传进去。

编译器只调 `lspe.audit(...)`（或一个 `LspeSdk` 会话的 `audit()`）。

`SDK_SURFACE` 是这个版本公开的**全部名字**。加名字 = 升版；内置引擎类不在
这个面上（它们是主检查器的实现细节）。
"""

from __future__ import annotations

import hashlib
import json

from .framework import (AdvisoryQuestion, AuditResult, CheckerRegistry, Claim,
                        Ctx, EngineDesc, EvidenceNode, HostResult, MainChecker,
                        MacroChecker, Plan, PluginChecker, SkipRecord,
                        UnitFacts, evidence_allowed)
from .solver import LSPE_DEFAULT_BUDGET

#: 公开 SDK 的版本。义务/证据/`MacroChecker` 形状从这里起冻结（`docs/361` §4）。
LSPE_SDK_VERSION = "lspe-sdk/1.0"


class Scheduler:
    """SDK 的调度器：适用性 / 顺序 / 预算 / 跳过原因。

    它不属于任何检查器（`docs/361` §1）：主检查器只接收 `Plan`。它不改变
    求解器（`docs/344` §4）。
    """

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


class LspeSdk:
    """SDK 会话：一个调度器 + 唯一的主检查器（`docs/361` §1）。

    调度是 SDK 的职责：`plan()` 产出 `Plan`，`audit()` / `run()` 把它交给主
    检查器。主检查器不能自己算计划。
    """

    def __init__(self, plugins: tuple[MacroChecker, ...] = ()) -> None:
        self.checker = MainChecker(plugins)
        self.scheduler = Scheduler()

    def facts(self, toks: list, *, dims: dict | None = None, level: int = 3,
              lms=frozenset(), instances: list | tuple = (),
              slots: dict | None = None, twin_available: bool = True,
              probes: list | tuple = ()) -> UnitFacts:
        return UnitFacts.from_tokens(
            toks, dims=dims, level=level, lms=frozenset(lms or ()),
            instances=instances, slots=slots,
            twin_available=twin_available, probes=probes)

    def plan(self, facts: UnitFacts) -> Plan:
        return self.scheduler.plan(facts, self.checker.checker)

    def plan_for_tokens(self, toks: list, unit: str, **kw) -> Plan:
        return self.plan(self.facts(toks, **kw))

    def audit(self, toks: list, unit: str, *, dims: dict | None = None,
              level: int = 3, lms=frozenset(), instances=(),
              slots: dict | None = None, twin_available: bool = True,
              probes=(), scoped: bool = False,
              shadowed: dict[str, frozenset[str]] | None = None,
              effects: bool | None = None,
              borrows: bool = False) -> AuditResult:
        """SDK 的审计入口：算计划，再交给主检查器。"""
        slots_in = dict(slots or {})
        if effects is True and slots_in.get("effects") != "true":
            slots_in["effects"] = "true"
        facts = self.facts(toks, dims=dims, level=level, lms=lms,
                           instances=instances, slots=slots_in,
                           twin_available=twin_available, probes=probes)
        plan = self.plan(facts)
        use_effects = (bool(effects) if effects is not None
                       else slots_in.get("effects") == "true")
        return self.checker.audit(
            toks, unit, plan=plan, lms=lms, instances=instances,
            scoped=scoped, shadowed=shadowed, effects=use_effects,
            borrows=borrows)

    def run(self, ctx: Ctx, plan: Plan | None = None):
        """框架面：SDK 算计划，主检查器执行。"""
        return self.checker.run(ctx, plan or ctx.plan or self.plan(ctx.facts))

    def check(self, ctx: Ctx) -> list:
        """框架面：`MacroChecker` 统一接口。"""
        ctx.plan = ctx.plan or self.plan(ctx.facts)
        return self.checker.check(ctx)


#: 冻结的公开名字。判据 `test_sdk_surface_is_frozen_and_narrow` 钉住它。
SDK_SURFACE = (
    "LSPE_SDK_VERSION",
    "SDK_SURFACE",
    "LspeSdk",
    "Scheduler",
    "MainChecker",
    "main_checker",
    "audit",
    "AuditResult",
    "MacroChecker",
    "EngineDesc",
    "Claim",
    "EvidenceNode",
    "AdvisoryQuestion",
    "Ctx",
    "UnitFacts",
    "Plan",
    "SkipRecord",
    "HostResult",
    "PluginChecker",
    "CheckerRegistry",
    "evidence_allowed",
    "plan_for_tokens",
    "plan_lines",
)


def main_checker(plugins: tuple[MacroChecker, ...] = ()) -> MainChecker:
    """SDK 的唯一检查器；调度器在 `LspeSdk` 上，不在这里。"""
    return MainChecker(plugins)


def audit(toks: list, unit: str, **kw) -> AuditResult:
    """用一个新的 SDK 会话审计一个单元（`docs/361` §3）。"""
    return LspeSdk().audit(toks, unit, **kw)


def plan_for_tokens(toks: list, unit: str, **kw) -> Plan:
    """兼容入口：用一个新的 SDK 会话算计划。"""
    return LspeSdk().plan_for_tokens(toks, unit, **kw)


def plan_lines(plan: Plan) -> list[str]:
    """`loment lspe state` 的调度面：计划、跳过原因与预算。"""
    run = ",".join(plan.run) if plan.run else "-"
    out = [f"[LSPE] plan {plan.plan_hash:016x} level={plan.level} run={run}"]
    for s in plan.skip:
        out.append(f"[LSPE] skip {s.engine}: {s.reason}")
    out.append("[LSPE] budgets " + ", ".join(f"{e}={n}" for e, n in plan.budgets))
    return out
