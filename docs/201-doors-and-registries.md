# 201 · 门与登记处：三条没有尺子的地方

> 2026-09-22。起因是一个问题——"Loment 仓库还需要什么 check？"查出来三条，
> 都是**同一个形状**：没有一个东西站在那里，于是坏了也不会红。

## 0. 一句话

判据（`ci.py` 的 `STATIC_CHECKS`，六十多条）本身很密；缺的全在**登记与闭门**这一层——
也就是"**没有一条尺子**"的那些接缝上。这份文档记的是三条接缝、它们的实测证据、
补了什么，以及**没补的是什么**。

（查的过程里还有**第四条**——"门禁的红集基线，新红即失败"。它**不在**这份文档里，
因为另一条线同时在落它（`.github/gate-baseline.txt` + workflow 里的对照步骤，并且
比这里最初设想的更细：候选新红先单跑一遍过滤并行假红）。同一天两处各写一份会撞车，
所以这里让路，只留一句指路。）

## 1. `lom/` 的闭门原本是一句空头承诺

`CONTRIBUTING.md` 的「Do not change anything under `lom/`」原本写着两条机制：

> A pull request that modifies anything under `lom/` is **detected automatically and closed
> without review**. … **Three of those, and you will no longer be able to open pull requests**
> against any Loment repository.

2026-09-22 逐条查过，**没有任何一条有实现**：

| 可能藏在哪 | 实测 |
|---|---|
| 仓内 workflow | `git log --diff-filter=A -- .github/workflows/*` 全历史只有 `gate.yml` 一个；它没有任何路径判断 |
| ruleset | `gh api repos/FujoJTOP/loment/rulesets` → `[]` |
| 分支保护 | `main` 的 protection 里**没有** `required_status_checks`，也没有路径规则 |
| 仓内脚本 | 没有任何东西做那句 `git diff --name-only origin/main -- lom/` |

**为什么这条最贵**：它是唯一一条**承诺过**的自动闭门，而且是对**外面**的人承诺的。
一条写在文档里、没有实现的规矩比没有规矩更坏——它让贡献者以为会被拦下，实际不会，
于是补丁被静默地放进一个谁也评不了的地方。而 `CONTRIBUTING.md` 自己写着
"A patch that breaks this document is not accepted"。

**做了的**：

* `gate.yml` 加一个 job `lom-door`（显示名 `lom/ 禁区`），**硬失败**，与 `static`
  （测量轮、故意不挂）性质不同。
* 它**只读 API 列文件名**（`/pulls/{n}/files`、`/compare/{before}...{after}`），
  **不 checkout 别人的代码**——所以一个字节的未审代码都不会进工作区，也因此
  **不需要** `pull_request_target`（那个事件为了拿写权限会带来真实的注入面）。
  只读 token 就够。
* `CONTRIBUTING.md` 改成说实话，并留一条带日期的更正说明"以前那两句不该那么写"。

**没做的（诚实边界）**：关闭 PR、三次禁言需要**仓库设置或一个 org 级 App**，
不是这个文件能单方面做到的。而且**失败 ≠ 挡住**——`main` 上目前**没有必需检查**，
所以这个 job 红了也只是红了。要它真的成为门，得把 `lom/ 禁区` 设成 required status
check。这一步在 `CONTRIBUTING.md` 里也写明了。

**2026-10-11：判定的内容改了一格 —— 拦住的是"改"，不是"碰"。** 文档一直写的是
"a pull request that **modifies** anything under `lom/`"，而实现是 `grep -E '^lom/'`
（碰到就拦）。两者在**新增**这一格上不一致，而差的那一格恰好是唯一能扩大接口面、
又不碰任何既有契约的动作（既有的三份那儿，对面在另一个仓库；新增一份没有对面可坏）。
现在按 `gh api` 的 `status` 分流：`added` 放行并在 summary 里列出来，
`modified`/`removed`/`renamed` 照旧拦，**列表形状不对**（缺 `status` 列）一律红
（fail closed —— 不给"API 换了形状就静默放行"留缝）。同一轮加了第四份契约
`lom/net.lom`，它是这条改动的第一个用例（`docs/229`）。

**这条改动本身没有让门变硬**：`lom/ 禁区` 仍然不是 required status，所以"新增放行"
在今天的实际效果是"不再对合法的扩展动作报一次红"，而不是"挡不住别的什么"。

## 2. 发布清单 `GLOBS`：没有"该列而没列"的判据

`loment_release.GLOBS` 是**显式清单**（不扫目录），而 `loment_rel_test` 比的是
"**已列**的条目与仓库是否一致"——比不出"仓库里有、清单上没有"。那两个方向里，
**后一半才是静默的那种**：文件在仓里、在门禁里跑，却根本不在发布清单上。

**实测（新判据第一次跑）**：13 个被跟踪的 `tools/*.py` 不在清单里，其中 4 个是判据 ——
`loment_capasserts_test` / `loment_eol_test` / `loment_ffi_test` / `loment_syscalls_test`。
它们都在 `ci.py` 的 `STATIC_CHECKS` 里跑，却不在清单上。

这一格 `docs/188` §7.1 那张"加一门表层语法要动哪里"的表里早就标着
**"没有任何判据会抓"**（`loment-dev-86` 那次是人工看出来的）。

**做了的**：

* `loment_tools_test::test_every_tool_is_in_the_release_manifest`，**两个方向都钉**：
  被跟踪的 `tools/*.py` 不在清单里 → 红；清单里某条 glob 一个文件都匹配不到
  （陈旧条目，`docs/152` 记过一次）→ 红。
* 14 条补进 `loment_release.GLOBS` **与** `loment/tools/lomrel.lomt` 的 `globs_text()`，
  **插入位置逐条对齐**（两份不同序 = `loment_rel_test` 报"同集合不同顺序"，
  症状是 `落盘不同: 47434B vs 47434B`）。

**第三处登记（自举镜像）当时没有判据兜着 —— 2026-10-10 补上**：上面那条钉的是
`tools/*.py` ↔ `GLOBS`，**比不到自举那一份** —— `loment/tools/lomrel.lomt` 的
`globs_text()`。实测：`f262a67`（逃生舱·粒度 B）把 `tools/loment_opt_obj.py` 加进 `GLOBS`
却漏了 `lomrel.lomt`，一路漂到 `main`（两侧工件数 539 / 538）。**CI 上看不见**：会抓它的
那几条判据（`loment_rel_test` 的 `--check` / `--emit` / `--checksums`）全要 WSL，ubuntu 的
runner 上 SKIP。补法是 `loment_rel_test::test_lomrel_globs_match_python_globs` ——
**纯 Python**（不编链、不跑 WSL），逐条同序比两份清单，所以在 CI 上真跑。

**故意的例外**：`docs/i18n/` 不管。`docs/i18n/glossary.md` §1 明写它是**译者向工件**、
"sits outside every release GLOB … and is not shipped"——那一格的"没进清单"是设计，
不是漏。判据的 docstring 里写着这句，免得以后有人照这条判据去"修"它。

## 3. `tools/loment_i18n_test.py`：写在文档里，而文件不存在

`docs/i18n/glossary.md` §2 的原文是：

> When the Chinese file changes the stamp goes stale, and **`tools/loment_i18n_test.py`
> goes red** until that file is re-translated and re-stamped.

**那个文件此前不存在。** 体例把译文戳（`<!-- translated-from: … -->` +
`<!-- source-sha256: … -->`）规定得很细，也点名了执行它的判据——但没有人写。

**做了的**：按 §2 实现 `tools/loment_i18n_test.py`。它除了"源的 sha 变了没"，
还钉三件体例里写了、此前没人查的事：戳必须**恰好是那两行**（在 H1 之前）、
`translated-from` 指的源必须**存在**、`.en.md` 与它的源必须**同目录同号同 slug**
（§1："Keep the number and the slug unchanged"——改了的话 `docs/NNN` 形式的引用与
`loment_src.py` 的按名 glob 会静默失配）。另加一条反向断言：`docs/i18n/` 下
**不该**有 `.en.md`（§1：glossary "gets no English sibling of its own"）。

**十份英文版的戳今天全部有效**（逐份核过），所以这条判据落地时是绿的。

**实测撞到的一个坑（值得记）**：`scan()` 的第一版拿**模块级的真实路径**判
"这个文件在不在 `docs/i18n/` 下"，而 `scan(root)` 是要能被夹具喂一棵**临时树**的。
后果不是红，是**绿得不对**——夹具里那份 `i18n/glossary.en.md` 被"缺戳"那一格顺手
报了出来，于是"该没有的有了"这条规矩**一次都没被验过**，而夹具还是 PASS。
修法两处：判定改成相对传入的 `root`；夹具的断言从"文件名出现过"收紧成
"**必须是被那条规矩的文案报出来的**"。这条与 `docs/190` §1.4 那条
"判据测过自己会红才算判据"是同一件事的第二次出现——**夹具绿也可能是假的**。

## 4. 诚实边界（别把这些当成已经解决了）

* **`lom-door` 失败了也挡不住合并**，因为 `main` 上没有必需检查（2026-09-22 查过：
  `/rulesets` 是空的，branch protection 里也没有 `required_status_checks`）。设成必需
  检查是仓库设置那一步，不是这个仓里的文件。
* **GLOBS 完整性只管 `tools/*.py`**。`lompi/store/**` 有自己那条
  （`loment_lompi_test::test_release_manifest_covers_every_store_file`），
  `.github/`、`editors/`、`kernel/`（vendored）各按各自的方式覆盖，没有并进这一条。
  哪一天它们也漏了，形状会和这一条一样：**绿着，却没发出去**。
* **i18n 那条判据只覆盖已有的 10 份 `.en.md`**。它不管"哪些文档应该有英文版"——
  翻译进度没有被任何东西钉住，这是有意的（译文是增量做的）。
