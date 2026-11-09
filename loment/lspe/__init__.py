"""LSPE — Loment Safety Police Espace（`docs/262`）。

编译器是这里的**客户端**：它把 token 流与站点交给 `lspe`，再把返回的台账写进
Potato v16。义务、证据、接收关系与求解器都定义在这个包里，不定义在编译器里。

v2（0.1.5.2）把四件都补齐：质疑器（E1–E8）的判决、反问器（Q 码与回答通道）、
警察 LSPC（预检单与模式）、台账（派生树与证明哈希、五条不变量）。
"""

from __future__ import annotations

from . import aicheck as _aicheck
from . import aiask as _aiask
from . import loans as _loans
from .cache import Cache
from .census import census, census_e4
from .derive import (build_trees, derivation, proof_hash, proof_root, sidecar,
                     verify, why_lines)
from .invariants import check as check_invariants
from .ledger import build as build_ledger, lspc_lines, public
from .lspc import (LSPC_MODES, default_mode as lspc_default,
                   parse_acks, previous_items, render_text as lspc_text,
                   sheet as lspc_sheet)
from .questions import LspeError, ask, load_answers, parse_answers
from .registry import (
    LSPE_CAPACITIES,
    LSPE_EVIDENCE_KINDS,
    LSPE_EVIDENCE_LEVEL,
    LSPE_FLOOR_KEYS,
    LSPE_INTERROGATIONS,
    LSPE_LSPC_FAMILIES,
    LSPE_OBLIGATIONS,
    LSPE_PROBED_OBLIGATIONS,
    LSPE_QUESTION_TEMPLATES,
    LSPE_QUESTIONS,
    LSPE_SITE_KINDS,
)
from .solver import LSPE_DEFAULT_BUDGET, LSPE_DEFAULT_DEPTH, LspeSolver

#: AI 检查器（`docs/262` §10.7）：请求/回答都是文件，AI 只产 Q014。
aicheck_bundle = _aicheck.bundle
aicheck_dumps = _aicheck.dumps
aicheck_ingest = _aicheck.ingest
aicheck_render = _aicheck.render_text
#: 通道层（`docs/262` §10.7）：`--ask-cmd` / `--ask` 把请求送出去。
aicheck_ask = _aiask.ask
aicheck_prompt = _aiask.prompt
#: E10 借用检查器（`docs/317`）：loan 图分析（报告面）。
loans_analyze = _loans.analyze
loans_text = _loans.text_lines

__all__ = [
    "Cache",
    "LSPC_MODES",
    "LSPE_DEFAULT_BUDGET",
    "LSPE_DEFAULT_DEPTH",
    "LSPE_CAPACITIES",
    "LSPE_EVIDENCE_KINDS",
    "LSPE_EVIDENCE_LEVEL",
    "LSPE_FLOOR_KEYS",
    "LSPE_INTERROGATIONS",
    "LSPE_LSPC_FAMILIES",
    "LSPE_OBLIGATIONS",
    "LSPE_PROBED_OBLIGATIONS",
    "LSPE_QUESTION_TEMPLATES",
    "LSPE_QUESTIONS",
    "LSPE_SITE_KINDS",
    "LspeError",
    "LspeSolver",
    "aicheck_ask",
    "aicheck_bundle",
    "aicheck_dumps",
    "aicheck_ingest",
    "aicheck_prompt",
    "aicheck_render",
    "ask",
    "build_ledger",
    "build_trees",
    "census",
    "census_e4",
    "check_invariants",
    "derivation",
    "load_answers",
    "lspc_default",
    "lspc_lines",
    "lspc_sheet",
    "lspc_text",
    "loans_analyze",
    "loans_text",
    "parse_acks",
    "parse_answers",
    "previous_items",
    "proof_hash",
    "proof_root",
    "public",
    "sidecar",
    "verify",
    "why_lines",
]
