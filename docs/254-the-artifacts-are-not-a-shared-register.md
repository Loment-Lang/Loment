# 254 · 生成物不是共享寄存器：把全树台账从 PR 路径上撤下来

> 状态: **已落地（2026-10-16）** · 落点: `.github/workflows/manifest.yml`、`tools/ci.py`、
> `tools/lom_audit.py`、`tools/loment_tools_test.py`、`CONTRIBUTING.md` · 前史: `docs/201` §1、
> `docs/159` §（字节序那条坑）、`docs/176` §4A（种子为什么可以折叠出评审面）
>
> 一句话: **一份"每一行都属于某个别人文件"的生成物，放在 PR 路径上就是一个共享寄存器** ——
> 于是每一个 PR 都要写它，而任意两个 PR 写的是它里面相邻的行。撤下来，不是把它藏起来。

---

## 1. 症状：两个各改互不相干文件的 PR，冲突在同一片相邻行上

`loment/build/release-manifest.json` 与 `loment/build/SHA256SUMS` 记的是
`loment_release.GLOBS` 里**每一个文件**的 sha256。2026-10-16 实测：**667 条**，其中
`docs/` 占 **160 条**，且**连续**落在索引 481..640（`docs/*.md` 在 `GLOBS` 里是一条模式）。

而 `loment_release`（无参 = "入库台账 == 当前树"）是 `ci.py` 的门禁条目。于是：

* **每个 PR 都得重算它** —— 碰 `GLOBS` 里任何文件（`docs/*.md` 在内）都会让它过期；
* 两个 PR 各改**互不相干**的文件时，写的是台账里**相邻的行**。git 的合并上下文是 3 行，
  而 `docs/` 那 160 条是一个连续块 ⇒ **必冲突**。

**受控实验**（从 `origin/main` 起两个分支，各只加一份不同的 doc，别的一律不碰）：

```
A: docs/990-probe-a.md      B: docs/991-probe-b.md      试合 A ← B
冲突文件：loment/build/SHA256SUMS
         loment/build/release-manifest.json          ← 只有这两个，各 1 个冲突块
```

冲突块长这样（add/add 落在同一位置）：

```
 0187657…  docs/253-loment-memory-safety.md
<<<<<<< HEAD
 e60b2fc…  docs/991-probe-b.md
=======
 f997d75…  docs/990-probe-a.md
>>>>>>> probe/a
 6d3b459…  docs/manual/index.md
```

**这不是"运气差"。** 当时唯一开着的 `CONFLICTING` PR（#436）5 个文件里有 3 个是生成物，
冲突点同样是这两件 —— 而那一笔是**代码**改动，不是文档。

**判断的依据**：这份**入库**台账的唯一仓内消费者，就是"台账是否新鲜"那条判据本身 ——
**循环的**。真正有分量的东西在别处，而且都不读它：

| 判据 | 它读什么 |
|---|---|
| `loment_tools_test::test_every_tool_is_in_the_release_manifest` | `loment_release.GLOBS` |
| `loment_lompi_test::test_release_manifest_covers_every_store_file` | `loment_release.build()` 的**内存**结果 |
| `loment_rel_test::test_lomrel_emit_writes_same_bytes` | 两个实现各写一份**临时**文件再比 |
| `tools/loment_publish.py` | 完全不碰 |

---

## 2. 想过的别的办法（都写在这里，免得下一轮重想）

* **`merge=union`**：同一行两改时它把**两边**都留下 —— 同一路径两条哈希。那是**静默损坏**：
  `--check` 用 dict，后写的赢；`sha256sum -c` 有一半必失败。而 GitHub 侧**不跑**自定义
  merge driver，`merge=ours` 那一类根本够不着。
* **把台账缩到"非文档"**：只是把冲突从文档挪到代码，而且台账不再覆盖 `docs/` —— 一条
  只在出事时才被人发现的范围缩水。
* **留在 PR 里但不核新鲜度**：那入库的就是一份**没人核**的账。比不存更坏 —— 不存的账
  没人会信，存着的错账有人会信。
* **让 PR 分支"总是被机器人 rebase 到最新 main"**：这条能解冲突，但机器人往 PR 分支推
  那一笔会把必需检查挂成 `action_required`（`docs/159` 记过这个形状），PR 反而更卡。

---

## 3. 现在的安排

### 3.1 PR 路径只带"与这次改动一一对应"的东西

`manifest.yml` 的 `emit` job 现在只重算两件 + 改号：

* `loment/build/selfhost_driver.ll`（随 `loment/selfhost/*.lomt` 变）；
* `loment/tools/surface_data.lomt`（随内建表 / 诊断码变）；
* `loment_docs.py --fix`（撞号时给**新增的那一份**换号 —— 只动这个 PR 自己的文件）。

**它们留在 PR 路径上是有意的**：它们**正好在它所派生那份源改变时改变**，所以那时的冲突是
**两次改动真的重叠**，该让人看见。全树生成物不是这样 —— 它变了不代表你碰了它。

### 3.2 `main` 上有一个单一写者，**直推**，且与门禁并行

`manifest.yml` 新增 `emit-main`：由 `push: main` 触发，按**硬顺序**重算五件
（种子 → surface → 手册 → 发布清单 → 校验和 —— 清单记的是*那一刻的树*），**用属主 PAT
直接推到 `main`**。它只碰生成物，与任何人的 PR 都不重叠。

**为什么是直推，不是开一个 PR** —— 这一条是踩出来的，第一次写的就是"滚动 PR"那个形状：

> **用 `GITHUB_TOKEN` 开的 PR、以及它推的分支，都不触发 `pull_request` / `synchronize`。**
> 官方规则：`GITHUB_TOKEN` 触发的事件**不新建 workflow run**，只有 `workflow_dispatch` /
> `repository_dispatch` 例外。于是那个 PR 的门禁**永远不起**、必需检查**永远不报** ⇒
> auto-merge 永远不落。本仓 `docs/159` 已经撞过这个形状的亲戚（"机器人推送会把必需检查
> 挂成 `action_required`"，那一节记的是 2026-09-25 那次）。

**它凭什么推得动 `main`**：`enforce_admins` 是 `false`（2026-10-16 用 API 核实过），所以
**属主 PAT** 能绕过必需检查；`GITHUB_TOKEN` 不是 admin，会被拒 —— 那正是上面那条替代路
走不通的第二个理由。用的是 `secrets.MANIFEST_TOKEN`，且**只有这个 job 引用它**：`emit`
那一路跑在 `pull_request_target` 上（树是别人的），既拿不到也不需要它。

**"不排在门禁后面"是明确的**：不加 `needs:`、不用 `workflow_run`。两种写法都会把重算
串在门禁**之后**，于是每一次合并变成"先等门禁、再等重算"。重算只读树、只写生成物，没有
任何理由等门禁。`main` 的 `push` 门禁随后照样会在这一笔上跑一遍 —— 只是**不阻塞**它。

**代价，一并说清楚**：`main` 上每次合并多一笔机器人提交，而**那一笔没有被门禁拦过**
（它是重算出来的，不是人写的；门禁随后会在它上面跑，但拦不住）。以及这一套依赖一个
**仓 secret**：没有它 `emit-main` 直接红（不静默跳过），而**建它是一次人工动作**。

### 3.3 判据：撤掉的那条换了个执行处，并且加了三条

| 原来 | 现在 |
|---|---|
| `ci.py` 的 `loment_release` / `loment_manual` 门禁条目（无参 = 入库 == 当前树） | 撤出 `STATIC_CHECKS`。它们的 `--check` 语义**没动**，仍可自查一份 checkout |
| `lom_audit` M93/M95 = 逐字节等于当前树 | 改成**滞后容差**判（见下） |
| `loment_rel_test::test_lomrel_emit_writes_same_bytes` 的 `assert py_bytes == orig` | 去掉；**保留**孪生逐字节对拍（那才是这条判据的分量） |
| 审计主张 **C9** 的量尺 `loment_release` | 换成 `loment_rel_test`（"两个独立实现给出同一份清单"）—— 一条在交付物上恒假的判据不能挂在门禁上 |
| — | 新增 `loment_tools_test` 两条：**PR 侧不许提交整树生成物**、**`main` 侧写者必须存在且不排在门禁后面**（先在这两条落地的树上跑红过） |

**M93/M95 为什么反而变强了**：它们只对"**自这些文件上次被写出以来没动过**"的路径要求相等。
动过的 = 还没轮到写者刷新，容忍；没动过的 = 必须对得上，抓的是**真正错的**台账（手工改过的、
生成器写错的）。旧写法在"仓库里的与生成的一起过期"时照样绿 —— `docs/162` 记过那个形状，
四次发布都是这么漏掉的。

> **实现细节，别踩**：`git diff --name-only <清单上次的提交>` **不带第二个参数** ——
> 那是与**工作树**比。写成 `<sha> HEAD` 看不见未提交的改动，本机带着改动跑一次就会把
> 刚改的那几个文件报成"台账对不上"（实测 6 处）。另外门禁的分片 checkout 必须
> `fetch-depth: 0`：浅克隆里 `git log` 只看得到 HEAD，问出来永远是"什么都没动"。
> 判不了时 M93/M95 **不静默放行**，会报一条指名要那个参数的账。

---

## 4. 结算：换到了什么，丢掉了什么

**换到**：并发 PR 在**结构上**不可能因为生成物冲突 —— 不再靠快慢和运气。这不是"少收几条路径"，
是"把那个共享寄存器整个移出这条路"。

**丢掉**：一份 checkout 的台账不再自带"与树一致"的保证。那份保证现在由 `emit-main` 在
**每一次合并**后去做 —— 比原来"每个 PR 各做一次"**更常做**，而且只在**唯一权威的那棵树**上做；
`lom_audit` M93/M95 的滞后容差接着判它的**对错**。代价是合并到刷新之间有一个窗口期，
台账是落后的 —— 而那段窗口里**没有任何东西读它**（§1 那张表）。

**顺带修掉的**：`docs/manual/index.md` 第 3 行是 `tools/lomentc.py` 的哈希戳
（`loment_manual.compiler_version`），于是**两个都改了编译器的 PR 也会在同一行上对撞**。
它跟着台账一起挪到了 `emit-main`。

---

## 5. 残留（都不是这一轮的问题，但别当成已解）

1. **种子与 `surface_data.lomt` 仍在 PR 路径上**，两条 PR 同时改 `loment/selfhost/*.lomt`
   仍会冲突。那是**真**重叠（两次改动落在同一棵源码树上），该让人看见 —— 但它是"冲突"
   这件事的剩余来源，不是零。
2. **文档撞号是另一个问题**：两条线各自看 `origin/main` 都取到同一个空号，两边都合进来。
   `emit-main` 现在以 `HEAD~1` 为 base 跑一次 `--fix`，把**这一次合并新带进来**的那一份挪号
   —— 不这么做的话 `main` 上就有重号、`loment_docs_test` 红，而**红的正是刚合进去那一笔**。
   PR 侧那一次 `--fix` 管的是"分支基于旧 main"那一半。
3. **写者的失败不会有人喊**：`emit-main` 挂了或被删了，生成物就是落后的，而除了
   §3.3 那条"写者必须存在"的结构判据之外，没有东西会发现。那条判据钉的是**存在与形状**，
   不是**它跑成功了**。
