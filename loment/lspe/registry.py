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
#: `bounds` 是**生成**的站点（LMS g1 内联界检查，没有自己的语法；`#1021`）。
LSPE_SITE_KINDS = frozenset({
    "guard", "index", "bounds", "free", "alive", "init", "exclusive", "call",
})

#: **问题码**（`docs/262` §5.10）。与 `E034–E038` 是两个命名空间：问题**永不阻断**
#: （`--lspe-mode=strict` 把未答升级成 E034），它们是 LSPC 贴出来的条。
#: `Q013` 是 `lspe pass "reason" { … }` 的未审计块（`docs/262` §5.16 / `#786`）——
#: 块跳过的是**审问**，不是收据：LSPC 每次都把它列出来。
LSPE_QUESTIONS: dict[str, str] = {
    "Q001": "找不到来源",
    "Q002": "检查是惰性的（关掉也不红）",
    "Q003": "两套实现分歧",
    "Q004": "替换后 WF 未重证（stale_wf）",
    "Q005": "步数预算耗尽",
    "Q006": "过程间深度到顶",
    "Q007": "析构诚实性无法证明",
    "Q008": "域未知（能力域覆盖不到）",
    "Q009": "别名不确定",
    "Q010": "free 所有权歧义",
    "Q011": "运行期泄漏",
    "Q012": "假设已过期（源码变了）",
    "Q013": "未审计块（`lspe pass`）—— 跳过的是审问，不是收据",
}
