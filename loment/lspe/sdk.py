"""LSPE SDK 1.0 —— 稳定面（`docs/361`）。

顶层只有一个检查器：`MainChecker`（主检查器）。域检查器、借用检查器、效应
检查器、GC 检查器等都是它的子检查器，通过 `main.sub(id)`、`main.domain_checker`
或 `main.borrow_checker` 访问。编译器只调 `main_checker().audit(...)`。

`SDK_SURFACE` 是这个版本公开的**全部名字**。加名字 = 升版；内置引擎类不在
这个面上（它们是主检查器的实现细节）。
"""

from __future__ import annotations

from .framework import (AdvisoryQuestion, AuditResult, CheckerRegistry, Claim,
                        Ctx, EngineDesc, EvidenceNode, HostResult, MainChecker,
                        MacroChecker, Plan, PluginChecker, SkipRecord,
                        UnitFacts, evidence_allowed, main_checker, plan_lines)

#: 公开 SDK 的版本。义务/证据/`MacroChecker` 形状从这里起冻结（`docs/361` §4）。
LSPE_SDK_VERSION = "lspe-sdk/1.0"

#: 冻结的公开名字。判据 `test_sdk_surface_is_frozen_and_narrow` 钉住它。
SDK_SURFACE = (
    "LSPE_SDK_VERSION",
    "SDK_SURFACE",
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
    "plan_lines",
)


def audit(toks: list, unit: str, **kw) -> AuditResult:
    """用一个新的主检查器审计一个单元（`docs/361` §3）。"""
    return main_checker().audit(toks, unit, **kw)
