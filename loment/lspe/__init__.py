"""LSPE — Loment Safety Police Espace（`docs/262`）。

编译器是这里的**客户端**：它把 token 流与站点交给 `lspe`，再把返回的台账写进
Potato v16。义务、证据、接收关系与求解器都定义在这个包里，不定义在编译器里。
"""

from __future__ import annotations

from .census import census, census_e4
from .ledger import build as build_ledger
from .registry import (
    LSPE_EVIDENCE_KINDS,
    LSPE_EVIDENCE_LEVEL,
    LSPE_OBLIGATIONS,
    LSPE_SITE_KINDS,
)
from .solver import LspeSolver

__all__ = [
    "LSPE_EVIDENCE_KINDS",
    "LSPE_EVIDENCE_LEVEL",
    "LSPE_OBLIGATIONS",
    "LSPE_SITE_KINDS",
    "LspeSolver",
    "build_ledger",
    "census",
    "census_e4",
]
