# 289 · 热线与投影：一次 Pre-Release 只带走它自己的 fix（`#1156`）

> 上游：`#1146`（伞）、`#1147`（准入）、`#1153`（清单）。实现：
> `tools/loment_projection.py`；判据：`tools/loment_projection_test.py`（6 条）。

## 1. 问题

从 `main` 切出来的 Pre-Release 是"自上个发行合并进来的一切"：fix 与 feature 混在一起。
整包装进一个跑着的进程，等于把功能也带进去 —— 而"只吃修复"是这条线的前提。

功能开关**解决不了**：代码、代码路径、能力都在，开关是承诺不是证明。

## 2. 热线

每个基线一条，线性、累积：

```
B0  ->  B0 + F1  ->  B0 + F1 + F2  ->  ...
```

- `B0` 是基线的第一个发行（跑着的构建的 `<major>.<minor>.<patch>`）。
- `Fi` 是声明为 fix 的提交，按提交顺序重放。
- 跑着的构建本身必须是线上的一个点；一棵随便的工作树不是热更新的对象。

## 3. 投影

对 `R` 上的一个 Pre-Release，投影是 B0 以来**声明为 fix** 的提交里，能通过三关的最长前缀：

| 关 | 怎么判 | 过不去时 |
|---|---|---|
| 1. 干净应用 | 在 B0 的克隆上 `git cherry-pick`（真做） | 剔除，`depends-on-feature` |
| 2. 能编过 | 把这次投影**改到的** `.lomt` 各查一遍 | 剔除，`build` |
| 3. 接口面不动 | 契约 / LLS 版本 / 能力 / surface / CLI 五个摘要与 **B0** 比 | 剔除，`digest` |

第 3 关比的是 **B0**，不是 `R`：R 里的 feature 正是要被挡在外面的东西 —— 第一版比的是
`R`，于是每一个 fix 都被判成 `digest`（执行面判据当场抓到的）。

第 2 关只查改到的文件，不是全树重编：全树要几分钟，而这次投影引入的风险面就是那些文件。

**"声明为 fix"** 有两条等价的路：合并的 PR 带 `bug` 标签，或者提交正文带 `Hotfix: yes`。
标签是散文、摘要是字节 —— 一个被标成 `bug` 的功能由第 3 关拦，**不靠标签**。

被剔除的 fix **点名**（`#{1157}` 的清单与 `status` 读它）；它随完整发行走，需要重启。

## 4. 产物

投影出来的构建从**投影树**编，不从 `R` 编。树上给一个哈希（`git rev-parse HEAD^{tree}`），
于是"同一条线重放两次是不是同一棵树"是可判的（判据 5）。

一个没有 fix 提交的发布**不产生热更新件** —— 一个 Pre-Release 至多一次热更新。

## 5. 判据

| 判据 | 问法 |
|---|---|
| `test_only_declared_fixes_enter_the_line` | 一个 fix 一个 feature：线里只有 fix？ |
| `test_a_fix_that_depends_on_a_feature_is_evicted` | 应用不上的 fix 被剔除并点名？ |
| `test_a_mislabeled_feature_is_refused_by_the_digest` | 标成 fix 的新关键词由 `digest` 拦下？ |
| `test_two_fixes_apply_in_order` | 第二个依赖第一个时两条都进线、顺序不变？ |
| `test_replaying_the_line_is_byte_identical` | 重放两次树哈希相同？ |
| `test_no_fix_commits_means_no_hot_artifact` | 零 fix → 没有产物（线空、树空）？ |

夹具是**真的 git 仓库**（从本仓拷出来的树 + 几个提交），投影在克隆上真跑 `git cherry-pick`，
摘要用 `loment_hotfix` 现算。没有一步是手写的结论。

## 6. 这一版**不**做到的

1. **不查 GitHub 的标签。** `labels=` 是给调用方（release workflow）传的表；那一步还没接。
2. **第 2 关只查改到的 `.lomt`。** 链接器 / 自举链的回归要靠完整门禁，不在这里。
3. **二进制在另一个文件里。** 这一份产出**树**与清单；把树编成热更新件的是
   `tools/loment_hotart.py`（两份 hotswap 档 → 装载件 + 含 artifact 的清单，
   `loment_hotart_test` 4 条）。发行流程还没有调用它。
4. **`git` 是外部依赖。** 投影跑在 CI 上（那里有 git）；运行时不碰它。
