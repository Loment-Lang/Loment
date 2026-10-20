"""LSPE 的义务与证据登记表（`docs/262` §5.2 / §5.3）。

这是 LSPE 的**语义面**：哪些义务存在、每种义务接受哪些证据、证据从哪个上下文级别起
被承认。编译器不定义这些 —— 它只是调用 `lspe` 的客户端。
"""

from __future__ import annotations

#: LSPE 的证据种类（`docs/262` §5.3）。**`Differential` 永远不在 accept 集合里** ——
#: 孪生只证一致，不证对（`docs/253` §5.1.1）。
LSPE_EVIDENCE_KINDS = ("Axiom", "Syntactic", "Typed", "LocalFlow",
                       "Interproc", "GlobalInvariant", "Assumed", "Differential")

#: 每种证据**最早**在哪个上下文级别被承认（`docs/262` §5.4 / §5.6）。
#: 没列出的（Axiom / Syntactic / Assumed / Differential）从 L0 起就承认。
LSPE_EVIDENCE_LEVEL = {"Typed": 1, "LocalFlow": 1, "Interproc": 2, "GlobalInvariant": 3}

#: LSPE 的 14 条义务（`docs/262` §5.2）。每条都点名**语义所有者**与**接收的证据种类**。
#: LSPE 不发明语义：`owner` 一项是指到那份语义文档/码的。
LSPE_OBLIGATIONS: dict[str, dict[str, object]] = {
    "InBounds": {"owner": "docs/146 / E028",
                 "accept": ("Syntactic", "Typed", "LocalFlow", "Interproc",
                            "GlobalInvariant", "Assumed")},
    "Alive": {"owner": "docs/210 / E029",
              "accept": ("LocalFlow", "Interproc", "Assumed")},
    "InDomain": {"owner": "docs/146",
                 "accept": ("Typed", "GlobalInvariant", "Assumed")},
    "Exclusive": {"owner": "docs/206 / E007",
                  "accept": ("LocalFlow", "Interproc", "Assumed")},
    "Initialized": {"owner": "docs/206 / E006",
                    "accept": ("Syntactic", "LocalFlow", "Interproc", "Assumed")},
    "NotDangling": {"owner": "docs/206 / E012",
                    "accept": ("LocalFlow", "Interproc", "Assumed")},
    "NoRuntime": {"owner": "docs/182 / E031",
                  "accept": ("Axiom", "Syntactic", "Typed")},
    "FreeOwnership": {"owner": "docs/210",
                      "accept": ("LocalFlow", "Interproc", "Assumed")},
    "LadderLiability": {"owner": "docs/210",
                        "accept": ("Typed", "Interproc", "GlobalInvariant", "Assumed")},
    "CapabilityLive": {"owner": "docs/146",
                       "accept": ("Typed", "GlobalInvariant", "Assumed")},
    "TypeWellFormed": {"owner": "docs/158 / docs/147",
                       "accept": ("Axiom", "Typed", "Interproc", "Assumed")},
    "DropHonest": {"owner": "docs/253 §1.2",
                   "accept": ("Assumed",)},
    "GuardHolds": {"owner": "docs/146",
                   "accept": ("Syntactic", "Typed", "GlobalInvariant", "Assumed")},
    "SummaryContract": {"owner": "docs/210 §7",
                        "accept": ("Axiom", "Interproc", "Assumed")},
}

#: 普查里允许出现的站点种类（I1 的第一半）。加一种站点要同时登记在这里。
LSPE_SITE_KINDS = frozenset({
    "guard", "index", "free", "alive", "init", "exclusive", "call",
})
