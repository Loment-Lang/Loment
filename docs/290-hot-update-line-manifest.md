# 290 · 投影清单与 `loment update status`（`#1157`）

> 上游：`#1153`（清单）、`#1156`（投影）。实现：`tools/loment_hotfix.py` 的
> `projection` 字段、`tools/loment_update.py` 的准入检查、`tools/loment_staging.py` 的
> `status()`；判据：`tools/loment_hotline_test.py`（6 条）。

## 1. 清单里的 `projection`

```
projection {
  baseline  0.1.5
  release   v0.1.5-hot.3
  line      [ { sha, subject, kind: "fix" } … ]     ← 按应用顺序
  evicted   [ { sha, subject, reason } … ]          ← depends-on-feature / build / digest
  tree      <git tree hash>                         ← 重放得出来
}
```

- `line` 里每一条都带 `kind: "fix"`。不是装饰：**准入按这个字段拒绝"声称装了个功能"**。
  一份把 feature 写进 `line` 的清单会被 `projection` 拒（判据 2）。
- 同一个 sha 不能同时在 `line` 与 `evicted` 里 —— 那是自相矛盾的清单，同样拒。
- `projection.baseline` 必须等于跑着的构建的基线（`projection-baseline`），
  `projection.release` 必须等于清单的 `release`（`projection-release`）。
- `tree` 是投影树的哈希：重放同一条线得到同一个值（`docs/287` 判据 5）。

## 2. `loment update status`

```
line    0.1.5
  v0.1.5-hot.1
    + 3f2a91c0d4e1  fix: overflow in the parser
  v0.1.5-hot.2  (rollback)
    + 3f2a91c0d4e1  fix: overflow in the parser
restart required
  ! 77b0c5a2e9f3  fix: adds a keyword  (digest)
```

- **按应用顺序**，不是按版本号。回滚在线上是"往左走"：后一次接受的 release 比前一次小，
  于是标成 `rollback`，不是 `upgrade`（自动接受保持单调，往回走只能是显式操作）。
  第一版按版本号排序，回滚永远看不见 —— 判据 5 抓到的。
- `restart required` 读**最后一份**清单的 `evicted`：那些 fix 没进线，随完整发行走。
- 拒绝永远是点名的（`projection-baseline` / `digest` / `build`），不是笼统的 `invalid`。

## 3. 判据

| 判据 | 问法 |
|---|---|
| `test_the_manifest_names_applied_and_evicted` | 一条好 fix 一条被剔除：清单里两边都点名？ |
| `test_a_manifest_claiming_a_feature_is_refused` | `line` 里写 `kind: "feature"` → `projection` 拒？ |
| `test_status_lists_the_line_and_the_restart_required` | `status` 列出线上那条与要重启那条（带理由）？ |
| `test_the_tree_hash_reproduces_the_artifact` | 两次投影同一棵树，清单里的 `tree` 对得上？ |
| `test_an_earlier_point_is_a_rollback` | 回到前一个点 → `status` 标 `(rollback)`？ |
| `test_a_manifest_from_another_baseline_is_refused` | 说自己是 0.1.6 的清单 → `projection-baseline`？ |

## 4. 这一版**不**做到的

1. **`status` 只读本机的 `applied/`。** 它不知道别的机器上装过什么（没有中心台账）。
2. **回滚是"再装一次前一个点"。** `hot_restore` 在强制重映射之后不适用（`docs/284` §6）；
   所以回滚走的是同一条 fetch → stage → 换 的路，只是候选更旧，且要显式
   `allow_rollback`。
3. **`restart required` 只报最后一份清单的。** 历史上被剔除的 fix 不重复报 —— 它们要么
   已经被后来的完整安装带走，要么还在等一次重启；这一版不做那个账。
