# 318 · AI slop 的机械代理判据（`loment slop`）

> 状态: **已实现（2026-11-09）** · 上游: `docs/262` §10.7（LSPE 的 AI 检查器：问题源不是证据源）
> · `docs/302`（AI 产出的标注）· `CONTRIBUTING.md`（合并与 review 的纪律）
> 一句话: **slop 不是语气，是一组可观察的变更形状**；这些形状用机械判据就能抓，
> 模型只在"这些点值不值得人看一眼"上做提示，永远不改判决。

---

## 0. 为什么另起一格

LSPE 里也有一个叫 AI 检查器的组件（`docs/262` §10.7，随 PR #1505 落地；
`choose lspe_aicheck_true` + Q014）。它回答的是"这个**站点**有没有来源"——它是安全台账的怀疑源，不是代码质量。用它减 slop 等于拿听诊器测血糖：
它看不见 diff、看不见意图、不评重复与测试。

slop 的特征在**变更层**（PR / diff），而 LSPE 的站点在**单元层**。两层不是一件事，
所以这一格独立成立：`tools/loment_slop.py`，入口 `loment slop check`。

---

## 1. 什么算 slop（六条代理）

每条都是**可机械核对的形状**，不是主观判断；括号里是"为什么不选它"的失败模式。

| # | 代理 | 抓的是什么 | 误报风险与压法 |
|---|---|---|---|
| S1 | `scope.size` / `scope.outside_area` | 一次改动太大（>400 行 / >12 文件），或改到了声明范围之外 | 生成物单独提交、拆分 PR 就能消；阈值是政策，写在常量里 |
| S2 | `tests.trivial` | `assert True` / `assert 1 == 1` / `assert x == x` —— 恒真的"测试" | 只匹配**逐字**形状；`assert x == 2` 不碰 |
| S3 | `duplicates.block` | 新加的连续 5 行在 base 树里已有一份逐字相同的 | 只比**同语言代码**；`neg/`、语料、vendored、docs 全部跳过；纯 import 头不算 |
| S4 | `comments.restate` | 注释与下一行代码**逐字相同**（去掉 `#`/`//` 与标点） | 只抓完全相同；"解释为什么"的注释不会被撞上 |
| S5 | `api.unused` | 新加的 `pub fn` 在代码里一次都没被调用（只在定义处出现） | 只在 `.lomt` / `.lom` 上判；文档提到不算调用点 |
| S6 | `tests.missing` / `deps.new` | 新增源文件没有判据；或依赖/锁文件被改 | 新增文件是 medium，改 30 行以上没有测试是 low |

**刻意不做**：命名与风格、散文质量、架构品味、正确性（编译器与判据管）、安全审计
（LSPE 管）。一条判据越像审美，越会被忽略。

---

## 2. 两条边界：孪生与生成物

这个仓有**两处合法的重复**，判据必须放过它们，否则检查器会对着仓的纪律喊：

1. **孪生实现**（`tools/lomentc.py` 与 `loment/selfhost/*.lomt`）必须逐字节一致——
   跨语言的"重复"是要求，不是 slop。S3 只比同后缀的代码文件。
2. **机械产物**（种子、发布清单、`SHA256SUMS`）本来就该在 PR 里，但应**单独一条
   `[生成物]` 提交**。S1 的阈值对它们照常适用；拆分提交是消红的方式，不是豁免。

另外，`loment/selfhost/neg/` 是**故意写坏的夹具**，语料是 LLM 的输入 ——
S3 与 S4 跳过它们；从里面抄一段出来才是 slop。

---

## 3. 模型的位置：提示，不是判决

```
loment slop check --ask-cmd 'any-model --json'
```

- 输入：机械发现 + 截断的 diff 摘录（确定性排序、固定条数）；
- 输出：`{"format": "loment-slop-hints", "hints": [{"finding": id,
  "verdict": "agree|disagree|unsure", "note": ...}], "extra": [...]}`；
- **提示只打印成 `HINT`**：不改 findings、不改 `--strict` 的退出码；引用不存在的
  finding 只进 `unknown`，不新建；
- 提示层坏掉（模型没回、不是 JSON、进程失败）只打一行 `[slop] 提示层跳过`，
  报告照出。

通道层也**复用** `loment/lspe/aiask.py`（`run_raw` / `parse_json_text`）：argv 拆分、
Windows 反斜杠、围栏 JSON 只在一个地方踩坑。两份检查器共用一条通道，
但各自定义自己的提示形状 —— Q014 回答与 slop 提示不是一回事。

这条纪律与 LSPE 的 AI 检查器**同一条**（`docs/262` §10.7）：非确定性的外部工具
只能产问题，不能产证据。判据
`loment_slop_test::test_ai_hints_never_change_findings_or_exit_code` 钉住它 ——
带不带模型，findings 与 strict 退出码逐字相同。

---

## 4. 用法与退出码

```
python tools/loment_slop.py check                     # origin/main...HEAD
python tools/loment_slop.py check --worktree          # 连未提交的改动
python tools/loment_slop.py check --area tools --area loment
python tools/loment_slop.py check --strict --json
loment slop check ...                                 # 同一个入口
```

| 退出码 | 含义 |
|---|---|
| 0 | 没有 medium/high，或没开 `--strict` |
| 1 | `--strict` 且命中 medium/high |
| 2 | 用法/环境错误（不是 git 仓、base 不存在） |

输出按 `severity` 降序、再按 `id` 字典序 —— 同一棵树跑两遍，JSON 逐字节相同
（`test_report_is_deterministic`）。

---

## 5. CI 的姿势：先报告，后阻断

第一版**只报告**（report-only job，`continue-on-error`）：一个还没校准的判据直接
阻断，第一次误报就会被人关掉，之后等于不存在。校准的路径：

1. 先在 PR 上收集一季度的命中，逐条标"真 slop / 假阳性"；
2. 把假阳性压到零的规则留下，其余改阈值或删掉；
3. 再开 `--strict` 当必需检查。

阈值（400 行 / 12 文件 / 5 行重复）是**政策**，不是自然常数：改它们要同时改 §1 的
表格，并在 commit message 里写理由。

---

## 6. 判据（先红后绿的形状）

`tools/loment_slop_test.py` 8 条，每条代理都有正例与反例：

* 恒真断言命中、真断言不命中；
* 复制粘贴命中（对着 **base 树**比，不对着工作树 —— 否则自己匹配自己，规则永远沉默）；
* 未调用的 `pub fn` 命中，被调用的不命中；
* 范围超限与 `--area` 越界命中；
* 新增源文件无判据命中；
* 报告确定性；
* AI 提示不改 findings / 退出码，坏提示不吞报告；
* 干净的小改动没有 medium/high（**反例**：只会喊的检查器与不跑的检查器一样没用）。

---

## 7. 我不主张

1. **不主张它能识别全部 slop**。它抓的是六个可核对形状；命名、废话散文、错误的
   抽象仍然要人看。
2. **不主张模型能判断 slop**。模型只提示"哪条值得看"；它说 agree 也不加 severity。
3. **不主张阈值普适**。400/12/5 是按本仓的 PR 粒度定的，换了仓库要重新量。
4. **不主张"通过检查"等于"不是 slop"**：漏报是常态，判据只把最容易核的那部分
   变成可见的。
