# 仓库工作约定（FujoOS）

> 工作区级指令。ZCode 读 `<repo>/AGENTS.md`，Claude Code / DSH 也认同名文件。
> 只写"从代码本身看不出来"的约定；判据与进度在 `docs/`。
>
> **动手之前先 `git fetch origin`** —— 你面前这棵树**会静默落后**，权威状态在 `origin`
> （第一节；懒得记就跑 `python tools/loment_state.py`）。

## 权威状态在 `origin`，不在你面前这棵本地树

**背景**：**本仓的检出静默落后是常态，而且落后得很多。** 2026-09-25 实测：本地 `main`
比 `origin/main` 落后 **181 个提交**，树里连 `.github/` 都不存在（它对 main 是 tracked）。
同形状的另一次代价更贵：0.1.4 **早已发布、公告都发了**，而有人读着本地树还在说"等
0.1.4"；一笔早已由别的会话合进主线的成品，被当成"还没人做"又收了一遍。

代价不在"少几行代码"，在于**基于旧树下结论**：版本、发布与否、tag、CI、某个文件在不在、
某条约定落没落地 —— 这些一旦读错，后面的判断全歪，而且**旧树不会报错**。还有一次是 PR：
基点老，一开出来就带着一片"看着像回归、其实是基点太老"的红。

**做法**：

1. **开工第一条命令是 `git fetch origin`**，并看一眼 `git log --oneline -1 origin/main` 的日期。
2. **判断任何"项目状态"都读 `origin/main`，不读工作树**。不必 checkout，直接读远端引用：
   `git show origin/main:<路径>`、`git log origin/main`。
3. **开分支 / rebase 一律以 `origin/main` 为基点**，不要以本地 `main` 为基点。
4. **对外文案**（README / 公告 / PR 描述）里的版本与"已发布"表态，必须核 `origin` 的 tag。
5. 懒人版：`python tools/loment_state.py` —— 上面这几件一次打出来（默认先 fetch）。

**边界**（别做过头）：

- 这条只约束**读状态**，不约束**写** —— 改东西照旧在工作树里，而 `fetch` **不会**更新检出。
- **落后是常态，不是故障**：发现自己落后时先 `git log` / `git diff` 看清是谁的，别惊慌回退。
- 反过来，"树老"**不是**本机红项的免罪牌：归因仍要落到具体那一笔（自己的 / 别人的在制品 /
  本机固有红项）。两条不能互相顶替。

## `kernel/` 与 `sdk/fuai-spec/` 在本仓是 **vendored 只读副本**，不是活的

**背景**：本仓是 Loment 的开发口，但 Loment 的 L0 接口层（`lom/*.lom`）是**跨线契约** ——
契约对面那一侧的源码在 FujoOS 仓里。若本仓不带那一侧，几条对账判据就**没有对照面**
（`lom_audit` 直接崩、`fuai_contract_check` 读不到）、`loment_p7_test` 断言不了
"syscall 层单一真源"。

**做法**：把这些对照物**按原路径**带进来（`tools/loment_publish.py` 顶部 `COUNTERPARTS`
是那份清单），判据因此一行都不用改。

**另有一类**：`loment/corpus.json` 点名的**语料**（Potato LLM 那一臂的输入，含
`sdk/linux/m1*.c` 与 `kernel/src/{smp,desk,graphics}.rs`）。它不是契约对照物，但同一个
道理 —— 判据要读、文件在 FujoOS 侧。那一份**从 `corpus.json` 推导**，不手抄：语料随
AI 线经常变，手抄必然静默漂。

**约定**：

1. **只读**。改它们要去 FujoOS 仓改，改完把副本重新拷一份过来 —— 与
   "本仓 `kernel/` 是陈旧副本"的既有分界一致。**在本仓直接改是错的**：没有东西会
   把你的改动带回去，而判据会拿着一份只在本地成立的对照面报绿。
2. 少了它们判据会**崩**，不是静默红；`loment_publish.py --check` 里有一条判据钉住
   "对照物在树里且在发布清单里"。
3. `LinuxFUAI/`（compat 线的 C 实现）**不在**这份清单里 —— 那是对方的独立仓库，不是
   文件。读它的两条判据（`lom_audit` 的 C 头、`lomc_test` 的发布副本一致性）在本机
   是**固有红项**，与搬家无关（旧树逐条同样红）。

## 展示 Loment 代码时用 `rust` 作围栏语言

**背景**：Loment 的 `.lomt`（L1）与 `.lom`（L0）**不是任何内置查看器认识的语言**：
ZCode 的内置查看器用 Shiki（语言集构建期固定）、Claude Code 的 TUI 用 highlight.js
（同样固定）、GitHub 的 markdown 用 Linguist（同样没有 loment）。它们都没有用户侧注册点
（调查与证据见 `docs/157`）。

**做法**：在**展示**场合 —— 聊天回复、`docs/*.md` 里的示例、issue/PR 描述 —— 用
` ```rust ` 作围栏。Loment 的原生语法与 Rust **重叠面很大**，关键词/类型/字符串/注释/
函数名这些着色基本一致；只有 `capability` / `guard` / `excluded` / `revocable` 会显示成
普通标识符。

（2026-09-18 起**不再声称"严格子集"** —— 表层语法已不止一种，见 `docs/188`。围栏语言的
理由从来只是**重叠面**上有相同的着色，与"是不是子集"无关，所以这一条照旧成立。）

**边界**（别做过头）：

- 这只是**显示层**的提示。**文件本身永远是 `.lomt` / `.lom`**，不要改扩展名、不要按
  Rust 语法去写；
- 引用语言的名字时写 Loment，不要说成 Rust；
- 引用的代码块若是 **Rust/C/Python** 文件，照旧用 `rust`/`c`/`python` —— 区分不出
  "真 Rust" 与"为了着色"时，看内容：源文件路径是 `.rs` 就是 Rust。

**真正的编辑器支持**在 `editors/vscode/`（TextMate 语法 + LSP 补全/跳转/诊断/格式化 +
6 个命令）。要**改** Loment 就用 VS Code；ZCode/Claude 的内置查看器只看不改。

## 对外文字用英文：**我们自己开的** PR 与 issue

**背景**：本仓是对外的，而**读 PR / issue 的人在仓外**。issue 一直是英文（`CONTRIBUTING.md`
的 bug 模板就是四条英文小标题），PR 正文却一度漂成中文 —— 2026-10-09 用户定：两者都要
英文。这与"聊天里用中文汇报"是**两个面**，不冲突：一个的读者在仓外，一个的读者是你。

**约定**：

1. `gh pr create` / `gh issue create` 的 `--title` 与 `--body` **都写英文**。AI 产出的照旧
   在正文首行点名工具（仓里还没有 `ai-generated` 标签）。
2. **commit message 不在此列** —— 仓里的提交消息一直是中文，照旧。
3. **在别人 PR / issue 上的评论跟对方的语言走**。这条只管**我们自己开的**那些 ——
   别推广成"所有 GitHub 文字都必须英文"。
4. 已有的不合规件**只改手上这一条**，不回头扫历史。

## L0 接口面有"冻结阈值"：`lom/*.lom` 是跨线契约，不是内部实现

**背景**：Loment 自身仍在开发，**实现层的高 churn 是预期的、也是允许的** ——
`tools/lomentc.py`（近 2 天 9 次改动）、checker 行为、IR 细节、自举链路，随你改。
但 `lom/*.lom`（L0 接口层）**不是实现**，它是**别的线要依赖的契约**：

- `FujoOS-compat` 的内核侧已经在依赖它：内容信任台账的原语
  `qual_feed = 33556`（`0x8314`）/ `qual_seq = 33557`（`0x8315`）与准入闸
  `dom_admit = 33555`（`0x8313`）就登记在 `lom/fuai.lom` 里，由
  `lom_spec_emit.py` 生成两份 `spec.json`，并被 `fuai_contract` 门禁逐条核对；
- 事实依据：**`lom/fuai.lom` 自 2026-09-09 建立后从未改动**，而同期编译器改了 9 次 ——
  这条"接口稳、实现动"的分界是当前跨线协作唯一的支点。

**约定（改动前先看这条）**：

1. **改 `lom/*.lom` 视为"对外契约变更"**：要单独一次提交，commit message 写清"谁依赖它、
   变了什么、下游要不要动"；不要在重构编译器时顺手改它。
   **新增一份 `.lom` 不是"改"**（2026-10-11 起，与 `lom/ 禁区` 那道门的判定一致）：
   既有的那几份的对面在另一个仓库，新增不动它们一个字节。代价是新增的那份**要自带判据**
   —— `lom/net.lom` 是样板（单源 + 四处对账 + 自证会红，见 `docs/229`）。
   它一旦被谁依赖，改它就回到上面这条下面。
2. **新增/删除原语 = 契约升版**：内核侧有针对枚举计数的断言
   （如 `lomc_test` 的 `Opcode` 变体数），改动必然触发下游假失败 ——
   预期如此，属于**该看见的信号**，不要靠"把断言放宽"消掉。
3. **不要为了内核方便而改 L0**：优先在内核侧做适配层（`docs/165` 的 L2）；
   反过来同样成立 —— 内核侧不直接吃语言线的生成产物。
4. 若确要变 L0：在 `docs/` 留一行"下游影响"，并知会 compat 线。

**不做**：不在语言冻结前把 Loment 产物提交成内核依赖、不把 Python 引入内核构建链（见 `docs/165`）。

## 改语言面要付双倍的工：手写一条提交、生成一条提交

**背景**：语言面（lexer / parser / checker / codegen + 诊断码 + 内建表）在仓里有**两个实现**
（`tools/lomentc.py` 与 `loment/selfhost/*.lomt`），必须逐字节一致（`docs/158` §5）。
每次改完，`loment/build/selfhost_driver.ll`（46 KB 的自举种子）会被 `--emit` 重生成，
而 SSA 编号会整体位移 —— 实测 `extern fn` 那两次提交里它以 **3858 行占了 diff 的 93%**，
把真正要看的 **153 行手写**淹掉（测量见 `docs/176` §1）。

**约定**：

1. **机械产物单独成一条提交**，消息里写明"只有生成物"。手写那条（源 + 文档 + 判据）与它
   分开，这样 review 只需要看前者。
2. **种子永远重新生成，不手工合并**。它在 `.gitattributes` 里标了 `-diff`，于是两边都改了
   它时是**整file 冲突** —— 那是对的，逼你跑 `python tools/loment_seed.py --emit`
   （`loment_seed_test` 会告诉你它是否过期）。
3. 判据 `loment_seed_test::test_seed_is_marked_generated` 钉住"种子被标成生成物"，
   防止这条纪律悄悄回退。**能藏的前提是它可复现** —— 上面那条"种子 == 参考实现的产物"
   就是那个前提，两条是一对。

## 写 Loment 程序之前，先读那份 agent 指南

**装好 Loment 工具链之后**（`loment version` 能跑），写或改 `.lomt` 之前先读指南：

- 有本仓库时：**`.claude/skills/loment/SKILL.md`**（套件入口；专题在同级
  `.claude/skills/loment-*/SKILL.md`）
- 只有安装包时：**`<前缀>/share/loment/skill/SKILL.md`**（入口）与
  `<前缀>/share/loment/skill/loment-*.md`（专题）

内建函数表、语法要点、E001–E038 错误码、以及包内命令 `ir/check/build/run/fmt/doc/lsp`
全在套件里，**自足**，不依赖本仓库其它文件。

**每个 skill 只有一份正本**（`.claude/skills/<name>/SKILL.md`）—— 别处都只是指针或同字节拷贝。
成员清单只登记一处：`tools/loment_dist.py` 的 `SKILL_SUITE`；加一个 skill 要同时过安装器、
发布清单与 `loment_dist_test`。

**它不是 Claude 专用的**：`.claude/skills/` 只是 Claude Code 的自动发现路径，
其它 agent（ZCode / Codex / Cursor …）按各自约定读不到它，所以安装器会把**指针**
写进它们认的用户级文件（Codex 是 `~/.codex/AGENTS.md`），并设 `LOMENT_SKILL` 环境变量。
