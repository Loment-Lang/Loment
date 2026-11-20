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
    # 字面量落在 `[lo..hi]` 里是**语法事实**（E9 的 Syntactic）；循环归纳给
    # GlobalInvariant；其余交给反问器 Q008。
    "InDomain": {"owner": "docs/146",
                 "accept": ("Syntactic", "Typed", "GlobalInvariant", "Assumed")},
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
    # `Assumed` 是默认（docs/253 §1.2 的地板），`Syntactic` 是**手写证明**：
    # 开发者给一条 `drop:<类型>=prove` 就把它从地板里移走（`#747`）。
    "DropHonest": {"owner": "docs/253 §1.2",
                   "accept": ("Assumed", "Syntactic")},
    "GuardHolds": {"owner": "docs/146",
                   "accept": ("Syntactic", "Typed", "GlobalInvariant", "Assumed")},
    "SummaryContract": {"owner": "docs/210 §7",
                        "accept": ("Axiom", "Interproc", "Assumed")},
    # E11 效应检查器（`docs/341`）。行是**声明出来的契约**：`Typed` 来自签名，
    # `Syntactic` 来自词法处理器栈，`Interproc` 来自被调函数的行。
    "EffectAllowed": {"owner": "docs/341",
                      "accept": ("Syntactic", "Typed", "Interproc", "Assumed")},
    "HandlerExhaustive": {"owner": "docs/341",
                          "accept": ("Syntactic", "Typed", "Assumed")},
    # `resume` 的 0/1 是**局部流**事实；尾续判定是语法事实。
    "ResumeLinear": {"owner": "docs/341",
                     "accept": ("LocalFlow", "Syntactic", "Assumed")},
    # 效应不穿越 C 边界：这是规范给的 `Axiom`，不是分析出来的。
    "EffectBounded": {"owner": "docs/341",
                      "accept": ("Axiom", "Typed", "Assumed")},
    # 一般处理器的续延捕获要被回收（`no_gc` 下没有自动档，只能是假设）。
    "CaptureReclaim": {"owner": "docs/341",
                       "accept": ("LocalFlow", "Interproc", "Assumed")},
}

#: 有**配对反证探针**的义务（E8 / `#766`）。探针登记表由 `tools/lspe_probes.py`
#: 从 `tools/*_test.py` 里的 `# lspe-probe: <Obligation>` 标记**机械生成**；
#: 这里只是它的语义面。没有探针的义务，它的站点停在 `UNDECIDED(inert)` / **Q002**。
LSPE_PROBED_OBLIGATIONS: frozenset[str] = frozenset({
    "InBounds", "Alive", "FreeOwnership", "SummaryContract", "GuardHolds",
    "Initialized", "Exclusive", "TypeWellFormed", "DropHonest",
    # E9 域检查器（`docs/306`–`308`）：包含性有配对反证（越界字面量必须红），
    # 撤销有"撤销后仍 guard"的探针。
    "InDomain", "CapabilityLive",
})

#: LSPE 自己的固定容量（`docs/262` §5.12 / `#751`）。**每一个都要有一条会说话的路**：
#: 值写明"谁是那条路"。自举侧的两张表是既有的点名失败（`pt_wfail`），
#: 参考侧靠预算与确定性上限，越界一律抛错，不静默截断。
LSPE_CAPACITIES: dict[str, tuple[int, str]] = {
    "pass_blocks": (64, "lspe_engine 回 2 -> pt_wfail（点名）"),
    "ptr_first_functions": (1024, "lspe_engine 回 1 -> pt_wfail（点名）"),
    "obligations_per_unit": (65536, "求解器步数预算耗尽 -> Q005，不静默"),
    "derivation_nodes": (65536, "证明哈希只对站点数（预算内）算，越界先报预算"),
    "substitution_log": (65536, "WF 站点数受预算约束；越界 -> Q005"),
    # E9 域检查器（`docs/306`–`308`）。
    "domain_declarations": (64, "lspe_domain 回 1 -> pt_wfail（点名）"),
    # E11 效应检查器（`docs/341`）。行是有限集合，容量越界必须点名，不许静默截断。
    "effect_declarations": (64, "lspe_effects 回 1 -> pt_wfail（点名）"),
    "ops_per_effect": (256, "lspe_effects 回 1 -> pt_wfail（点名）"),
    "handler_depth": (64, "词法处理器栈深度上限；越界 -> Q019，不静默"),
}

#: 普查里允许出现的站点种类（I1 的第一半）。加一种站点要同时登记在这里。
#: `bounds` 是**生成**的站点（LMS g1 内联界检查，没有自己的语法；`#1021`）。
LSPE_SITE_KINDS = frozenset({
    "guard", "index", "bounds", "free", "alive", "init", "exclusive", "call",
    # v2：析构器（`#747`）与替换后的类型良构性（`#745`）。
    "drop", "wf",
    # E9 域检查器：域声明 / 守卫的包含性 / 撤销。
    "domain", "domain_access", "revoke",
    # E10 借用检查器（`docs/317`）：loan 是一等对象。站点先只进报告面。
    "loan", "loan_use", "loan_kill", "loan_conflict",
    # E11 效应检查器（`docs/341`）：声明 / 行 / perform / 处理器 / resume /
    # 调用行 / C 边界 / 续延捕获。
    "effect_decl", "row", "perform", "handle", "resume", "row_call",
    "effect_foreign", "handler_capture",
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
    # AI 检查器（`choose lspe_aicheck_true`，`docs/262` §10.7）：AI 是问题源，
    # 不是证据源 —— 它的输出只有这一种，永远不进求解器。
    "Q014": "AI 提出的怀疑（不是证据）",
    # E10 借用检查器（`docs/317` §5.4）：三种"证不出来"的形状。
    "Q015": "条件返回的借用来源不唯一",
    "Q016": "两阶段借用（保留 / 激活）无法证明",
    "Q017": "借用要跨迭代活着",
    # E11 效应检查器（`docs/341` §8）：五种"证不出来"。
    "Q018": "效应行未声明或盖不住",
    "Q019": "处理器的穷尽性无法证明",
    "Q020": "`resume` 的线性（0/1）无法证明",
    "Q021": "效应要穿越 C 边界",
    "Q022": "续延捕获的回收未证明",
}

#: 反问器转交前**必须**跑过的三问（`docs/262` §6 硬规则 5）。
#: 缺任何一问 ⇒ **E036**。顺序固定：来源 → 反证 → 对质。
LSPE_INTERROGATIONS: tuple[str, ...] = ("provenance", "falsification", "cross_exam")

#: 台账 `floor_detail` 的键（`docs/262` §8 / §8.1）。
#: `I4` 的恒等式是**这一整张表**的和 == `floors`：
#: `assumed + stale_wf + budget + depth + drop_honest + pass`。
#: `depth` 与 `budget` 分开计（`#750`）：一个是步数耗尽，一个是过程间深度到顶。
LSPE_FLOOR_KEYS: tuple[str, ...] = ("assumed", "stale_wf", "budget", "depth",
                                    "drop_honest", "pass")

#: 每一种 UNDECIDED 的**原因 → 问题码**（`docs/262` §5.10）。`None` = 普通无来源。
LSPE_REASON_QUESTION: dict[str, str] = {
    "none": "Q001",
    "Q001": "Q001",
    "inert": "Q002",
    "Q002": "Q002",
    "divergence": "Q003",
    "Q003": "Q003",
    "stale_wf": "Q004",
    "Q004": "Q004",
    "budget": "Q005",
    "Q005": "Q005",
    "depth": "Q006",
    "Q006": "Q006",
    "drop_honest": "Q007",
    "Q007": "Q007",
    "unknown_domain": "Q008",
    "Q008": "Q008",
    "alias": "Q009",
    "Q009": "Q009",
    "free_ambiguity": "Q010",
    "Q010": "Q010",
    "runtime_leak": "Q011",
    "Q011": "Q011",
    "expired": "Q012",
    "Q012": "Q012",
    "pass": "Q013",
    "Q013": "Q013",
}

#: 问题模板（`docs/262` §5.10 / §6）。每一问都有**固定字段**：
#: `claim` / `chain` / `tried` / `options` / `default` / `reversible`。
#: `tried` 的键**必须**覆盖 `LSPE_INTERROGATIONS` 三项（E036 的判据）。
#: 模板里的 `{…}` 由反问器填；填不出来的模板不是模板，是散文。
LSPE_QUESTION_TEMPLATES: dict[str, dict[str, object]] = {
    "Q001": {
        "claim": "「{site}」这次放行没有来源",
        "chain": ["{obligation}({kind})"],
        "tried": {"provenance": "E1-E4 都没有给出 source",
                  "falsification": "没有可用的反证探针",
                  "cross_exam": "孪生两侧都没有来源"},
        "options": {"A": "补一个常量界 / guard（成本：一处源码改动）",
                    "B": "接受这次放行并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q002": {
        "claim": "「{site}」的检查是惰性的：关掉它也不会红",
        "chain": ["{obligation}({kind})"],
        "tried": {"provenance": "检查存在，但没有配对的反证探针",
                  "falsification": "关掉检查后探针仍然绿",
                  "cross_exam": "两侧都没有探针"},
        "options": {"A": "补一条反证探针（成本：一条判据）",
                    "B": "标注为惰性检查（成本：Q002 收据）",
                    "C": "删掉这条检查"},
        "default": "B",
        "reversible": True,
    },
    "Q003": {
        "claim": "「{site}」在两套实现里判决不同",
        "chain": ["{obligation}({kind})"],
        "tried": {"provenance": "两侧都给出了来源",
                  "falsification": "分歧探针没有消掉差异",
                  "cross_exam": "逐字节比较失败"},
        "options": {"A": "把两侧改成同一规则（成本：一次语言面改动）",
                    "B": "标注分歧并选一侧（成本：一条 Assumed 地板）",
                    "C": "拒绝这个站点"},
        "default": "B",
        "reversible": False,
    },
    "Q004": {
        "claim": "「{site}」的类型良构性在替换后没有重证",
        "chain": ["TypeWellFormed({type}, {subst})"],
        "tried": {"provenance": "替换前的证据在替换后不再适用",
                  "falsification": "σ 之后的检查没有跑过",
                  "cross_exam": "两侧都停在 stale"},
        "options": {"A": "在替换处加一次重证（成本：一处类型标注）",
                    "B": "假设这条界在新替换下成立（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q005": {
        "claim": "「{site}」的求解步数预算耗尽",
        "chain": ["{obligation}({kind})"],
        "tried": {"provenance": "工作列表在预算内没有收敛",
                  "falsification": "降低预算只会更差",
                  "cross_exam": "两侧用同一份步数预算"},
        "options": {"A": "提高 --lspe-budget（成本：更长的编译）",
                    "B": "接受未决定并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q006": {
        "claim": "「{site}」的过程间深度到顶",
        "chain": ["SummaryContract({callee})"],
        "tried": {"provenance": "摘要在深度上限内没有展开",
                  "falsification": "递归环没有基例",
                  "cross_exam": "两侧用同一份深度上限"},
        "options": {"A": "提高 --lspe-depth（成本：更长的编译）",
                    "B": "接受未决定并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q007": {
        "claim": "「{site}」的析构器诚实性无法静态证明",
        "chain": ["DropHonest({destructor})"],
        "tried": {"provenance": "析构体不透明",
                  "falsification": "没有可用的反证",
                  "cross_exam": "两侧都假设它诚实"},
        "options": {"A": "手写一条证明（成本：一条 answers 记录）",
                    "B": "接受假设并标注（成本：一条 DropHonest 地板）",
                    "C": "删掉析构器"},
        "default": "B",
        "reversible": True,
    },
    "Q008": {
        "claim": "「{site}」落在能力域覆盖不到的地方",
        "chain": ["InDomain({domain})"],
        "tried": {"provenance": "没有 guard 或域声明",
                  "falsification": "越域探针没有触发",
                  "cross_exam": "两侧都不认识这个域"},
        "options": {"A": "补一个 guard / 域声明（成本：一处源码改动）",
                    "B": "接受并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q009": {
        "claim": "「{site}」的别名关系不确定",
        "chain": ["Exclusive({name})"],
        "tried": {"provenance": "没有独占借用证据",
                  "falsification": "别名探针没有结论",
                  "cross_exam": "两侧都不确定"},
        "options": {"A": "加 &mut / 缩短借用（成本：一处源码改动）",
                    "B": "接受并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q010": {
        "claim": "「{site}」的 free 所有权有歧义",
        "chain": ["FreeOwnership({name})"],
        "tried": {"provenance": "最近的 let 不是 alloc",
                  "falsification": "所有权探针不确定",
                  "cross_exam": "两侧都不确定"},
        "options": {"A": "改成显式 alloc/free 对（成本：一处源码改动）",
                    "B": "接受并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q011": {
        "claim": "「{site}」把运行期拖进了 no_runtime 产物",
        "chain": ["NoRuntime({construct})"],
        "tried": {"provenance": "构造需要运行期支持",
                  "falsification": "no_runtime 探针红了",
                  "cross_exam": "两侧都看见这个构造"},
        "options": {"A": "去掉运行期构造（成本：一处源码改动）",
                    "B": "改 choose runtime（成本：一个维度声明）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "A",
        "reversible": True,
    },
    "Q012": {
        "claim": "「{site}」的假设已经过期（源码变了）",
        "chain": ["Assumed({id})"],
        "tried": {"provenance": "内容寻址 id 与上一轮不同",
                  "falsification": "旧假设不再覆盖新站点",
                  "cross_exam": "上一轮的台账与这一轮不一致"},
        "options": {"A": "重新回答（成本：一条 answers 记录）",
                    "B": "接受过期假设并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "A",
        "reversible": True,
    },
    "Q013": {
        "claim": "「{site}」在 lspe pass 块里，跳过的是审问不是收据",
        "chain": ["{obligation}({kind})"],
        "tried": {"provenance": "块外的引擎没有看到站点",
                  "falsification": "旁路块不产生反证",
                  "cross_exam": "两侧都记了同一条收据"},
        "options": {"A": "删掉旁路块并补证明（成本：一处源码改动）",
                    "B": "保留旁路并记账（成本：pass_ratio 上涨）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },

    "Q015": {
        "claim": "「{site}」返回的借用来自多个入参，无法唯一归因",
        "chain": ["NotDangling({kind})", "loan:{place}"],
        "tried": {"provenance": "流敏感存活算出两个以上来源",
                  "falsification": "没有 `&a T` 标记可唯一归因",
                  "cross_exam": "两侧都算不出唯一来源"},
        "options": {"A": "在签名里写 `&a T` 指定来源（成本：一个参数名）",
                    "B": "接受这次放行并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "B",
        "reversible": True,
    },
    "Q016": {
        "claim": "「{site}」的方法调用需要两阶段借用，但保留/激活无法证明",
        "chain": ["Exclusive({kind})", "loan:{place}"],
        "tried": {"provenance": "autoref 形状不完整",
                  "falsification": "没有冲突点的构造性证据",
                  "cross_exam": "两侧都证不出保留期"},
        "options": {"A": "拆成两条语句，让保留期显式",
                    "B": "按两阶段收下并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "B",
        "reversible": True,
    },
    "Q017": {
        "claim": "「{site}」的借用要跨迭代活着，归纳形状不认",
        "chain": ["Alive({kind})", "loan:{place}"],
        "tried": {"provenance": "循环归纳只认裸循环变量的无符号形状",
                  "falsification": "跨迭代使用是构造性反例",
                  "cross_exam": "两侧都认不出归纳"},
        "options": {"A": "把借用改成每次迭代新建",
                    "B": "接受并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "C",
        "reversible": False,
    },
    "Q018": {
        "claim": "「{site}」的效应行没有覆盖这里要触发的效应",
        "chain": ["EffectAllowed({kind})", "row:{effect}"],
        "tried": {"provenance": "处理器栈与声明行都不能放行",
                  "falsification": "去掉处理器后探针仍然红",
                  "cross_exam": "两侧都算出同一条行"},
        "options": {"A": "在签名里补 `effects {effect}` 或加处理器",
                    "B": "接受这次放行并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝（strict 下构建停止）"},
        "default": "A",
        "reversible": True,
    },
    "Q019": {
        "claim": "「{site}」的处理器没有覆盖效应 `{effect}` 的每个操作",
        "chain": ["HandlerExhaustive({effect})"],
        "tried": {"provenance": "臂集合与声明集合逐条比过",
                  "falsification": "少臂的 perform 是构造性反例",
                  "cross_exam": "两侧看到同一组臂"},
        "options": {"A": "补齐缺失的臂",
                    "B": "把洞交给外层处理器（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "A",
        "reversible": True,
    },
    "Q020": {
        "claim": "「{site}」的 `resume` 次数证不出 0 或 1",
        "chain": ["ResumeLinear({kind})"],
        "tried": {"provenance": "只认块尾这一种尾续形状",
                  "falsification": "同一块里的两次 resume 是构造性反例",
                  "cross_exam": "两侧都按块骨架判定"},
        "options": {"A": "改成每条路径恰好一次（或明确 abort）",
                    "B": "按假设收下并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "A",
        "reversible": True,
    },
    "Q021": {
        "claim": "「{site}」把效应带到了 `extern` 边界",
        "chain": ["EffectBounded({function})"],
        "tried": {"provenance": "C 侧没有证据记录，无法回答 perform",
                  "falsification": "效应穿越是规范禁止的构造",
                  "cross_exam": "两侧都看见 extern 声明"},
        "options": {"A": "把效应留在 Loment 侧，C 调用保持无效应",
                    "B": "接受并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "A",
        "reversible": False,
    },
    "Q022": {
        "claim": "「{site}」的续延捕获没有回收证明",
        "chain": ["CaptureReclaim({effect})"],
        "tried": {"provenance": "处理器有非尾续臂，需要捕获续延",
                  "falsification": "no_gc 下没有自动回收档",
                  "cross_exam": "两侧都识出非尾续"},
        "options": {"A": "改写成尾续处理器（成本：一次源码改动）",
                    "B": "接受捕获并标注（成本：一条 Assumed 地板）",
                    "C": "让 LSPE 拒绝"},
        "default": "B",
        "reversible": True,
    },
}

#: LSPC 的 **12 个风险族**（`docs/262` §7.2）。每族五列：
#: `protects`（你现在靠什么）/ `if_fails`（如果不成立）/ `fix`（修法）/
#: `cost`（不修的后果）/ `bearer`（谁承担）。
#: `match` 是**从站点/维度里判它在不在场的谓词名**（`lspc._present`）。
#: 最后一行 `audit_off` 是递归的：把告知关掉本身也必须被告知（§7.2）。
LSPE_LSPC_FAMILIES: tuple[dict[str, object], ...] = (
    {"id": "nonliteral_index", "family": "非字面量下标 / 裸指针偏移",
     "protects": "guard 的运行期 trap",
     "if_fails": "越界写穿", "fix": "加 guard 或改常量界",
     "cost": "trap 或踩内存（看档）", "bearer": "归你", "risk": "high",
     "match": "nonliteral_index"},
    {"id": "free_ownership", "family": "free 无所有权路径",
     "protects": "L1 定活插的 free",
     "if_fails": "双重释放 / 悬垂", "fix": "显式 free 或换 gc_auto",
     "cost": "原生下 UB", "bearer": "归你", "risk": "high",
     "match": "free_ownership"},
    {"id": "drop_honest", "family": "Drop 析构器",
     "protects": "假设它诚实（DropHonest）",
     "if_fails": "二次释放（#164243 同形）", "fix": "审计析构体 / 手写证明",
     "cost": "归你", "bearer": "归你", "risk": "high",
     "match": "drop_honest"},
    {"id": "revocable", "family": "revocable 能力",
     "protects": "撤销后不再访问",
     "if_fails": "使用已撤销能力", "fix": "去掉 revocable 或加检查",
     "cost": "归你", "bearer": "归你", "risk": "medium",
     "match": "revocable"},
    {"id": "extern", "family": "extern fn / --link 边界",
     "protects": "C 侧遵守约定",
     "if_fails": "ABI / 生命周期错配", "fix": "收窄边界 / 加封装",
     "cost": "归你", "bearer": "归你", "risk": "high",
     "match": "extern"},
    {"id": "no_runtime", "family": "no_runtime 里的运行期构造",
     "protects": "E031 拒",
     "if_fails": "产物拖进运行期", "fix": "去掉该构造",
     "cost": "编译期就拒", "bearer": "编译器", "risk": "low",
     "match": "no_runtime"},
    {"id": "gc_manual", "family": "gc_manual 的 alloc",
     "protects": "你负责归还",
     "if_fails": "耗尽 / 泄漏", "fix": "加 free / 换档",
     "cost": "归你", "bearer": "归你", "risk": "medium",
     "match": "gc_manual"},
    {"id": "nonliteral_guard", "family": "非字面量 guard",
     "protects": "运行期 trap",
     "if_fails": "trap 或性能损失", "fix": "改常量界",
     "cost": "运行期 trap", "bearer": "归你", "risk": "medium",
     "match": "nonliteral_guard"},
    {"id": "defer", "family": "上一轮遗留的 defer",
     "protects": "台账里的 deferred",
     "if_fails": "未决问题被遗忘", "fix": "回答或删除",
     "cost": "归你", "bearer": "归你", "risk": "high",
     "match": "defer"},
    {"id": "expired", "family": "过期假设（源码已变）",
     "protects": "lspe.answers 里的假设",
     "if_fails": "假设不再成立", "fix": "重答",
     "cost": "归你", "bearer": "归你", "risk": "high",
     "match": "expired"},
    {"id": "cross_unit", "family": "跨单元库契约（L4 未开）",
     "protects": "库摘要未验证",
     "if_fails": "跨库假设错", "fix": "开 L4 / 读库源",
     "cost": "归你", "bearer": "归你", "risk": "medium",
     "match": "cross_unit"},
    {"id": "loan_conditional", "family": "条件返回的借用来源",
     "protects": "Q015 的接受", "if_fails": "来源错认 ⇒ 悬垂",
     "fix": "写 `&a T` 或标注", "cost": "归你", "bearer": "归你",
     "risk": "medium", "match": "loan_conditional"},
    {"id": "loan_two_phase", "family": "方法调用的两阶段借用",
     "protects": "Q016 的接受", "if_fails": "保守拒（不 UB）",
     "fix": "拆语句", "cost": "归你", "bearer": "归你",
     "risk": "low", "match": "loan_two_phase"},
    {"id": "loan_loop", "family": "循环携带的借用",
     "protects": "Q017 的拒", "if_fails": "悬垂 / 借用逃逸",
     "fix": "每次迭代新建", "cost": "归你", "bearer": "归你",
     "risk": "high", "match": "loan_loop"},
    {"id": "loan_escape", "family": "跨单元借用摘要（L4 未开）",
     "protects": "库函数返回的 loan", "if_fails": "跨库假设错",
     "fix": "开 L4 / 读库源", "cost": "归你", "bearer": "归你",
     "risk": "high", "match": "loan_escape"},
    {"id": "effect_unhandled", "family": "未处理的效应",
     "protects": "处理器栈或 `effects` 行", "if_fails": "运行期找不到处理器",
     "fix": "加 arm 或补 effects 行", "cost": "归你", "bearer": "归你",
     "risk": "high", "match": "effect_unhandled"},
    {"id": "effect_general_handler", "family": "非尾续处理器（续延捕获）",
     "protects": "尾续判定与回收证明", "if_fails": "续延逃逸 / 泄漏",
     "fix": "改写成尾续或标注回收", "cost": "归你", "bearer": "归你",
     "risk": "medium", "match": "effect_general_handler"},
    {"id": "effect_foreign", "family": "效应穿越 extern",
     "protects": "C 边界无效应", "if_fails": "C 侧无法回答 perform",
     "fix": "把效应留在 Loment 侧", "cost": "归你", "bearer": "归你",
     "risk": "high", "match": "effect_foreign"},
    {"id": "audit_off", "family": "lspe_close_admin / --lspc=off 本身",
     "protects": "你选择了不审计",
     "if_fails": "上面全部看不见", "fix": "打开",
     "cost": "归你（但这块地板有标签）", "bearer": "归你", "risk": "high",
     "match": "audit_off"},
)
