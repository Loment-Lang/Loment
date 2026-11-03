# 286 · 暂存目录：GitHub 与运行进程之间的那一格（`#1154`）

> 上游：`#1146`（伞）、`#1147`（准入）、`#1153`（清单）。参考实现：
> `tools/loment_staging.py`；判据：`tools/loment_staging_test.py`（6 条）。

## 1. 为什么要有这一格

换的那条路**一次网络 I/O 都不能做**。工具链今天没有 TLS，而把 TLS 塞进一个正在换自己代码
的进程里，是把这个切片最难验证的部分（网络）和最危险的部分（改栈）绑在一起。

所以中间放一个目录：

```
$LOMENT_HOT_HOME/                    (默认 ~/.loment/hot)
  <baseline>/
    incoming/<release>/              hotfix.json + 载荷 + SHA256SUMS + .asc + 公钥
    rejected/<release>.json          {"release":…, "code":…, "detail":…}
    applied/<release>/               已经交给换的那一份（`status` 读它）
    state.json                       接受过的 release（重放保护）
```

**按基线分开**是一半的隔离：`0.1.5` 的进程只在 `0.1.5/` 下看，`0.1.6` 的候选连出现在
它视野里的机会都没有。另一半是 `poll()` 只读 `incoming/`：`rejected/` 里的东西永远不被捡。

## 2. 两个动作

- **`fetch`（进程外）**：用主机上的 `gh release download` 拿资产，或者从用户自己下好的目录
  复制（`--from`），然后走 `#1147` 的准入：签名、五个等式、预发布、版本单调、重放。
  任何一条不过 → `rejected/<release>.json`，带着点名的 `code` 与 `detail`。
  通过 → `incoming/<release>/`（先写进临时目录再 `rename`，所以轮询永远看不到半份候选）。
- **`poll`（进程内，事件循环顶部）**：扫一眼 `incoming/`，按版本**数字**排序取最大的那个。
  一次轮询就捡到；没有就返回 `None`（不是错误）。

`fetch` 是**幂等**的：同一个 tag 再来一次就是同一份候选 —— 还在 `incoming/` 的就直接返回，
已经换过的（在 `applied/`）也直接返回，不会多出一份、也不会被当成重放拒掉。

## 3. 判据

| 判据 | 问法 |
|---|---|
| `test_a_staged_candidate_is_picked_up_in_one_poll` | 放进去，一次轮询就捡到？ |
| `test_a_bad_signature_lands_in_rejected` | 签名被换 → `rejected`（`signature`），轮询看不见？ |
| `test_a_stable_release_lands_in_rejected` | 稳定版 → `rejected`（`not-prerelease`）？ |
| `test_candidates_for_two_baselines_stay_apart` | 两条基线各看各的目录，谁也捡不到对方的？ |
| `test_fetching_the_same_tag_twice_leaves_one_candidate` | 同一个 tag 取两次还是一条？ |
| `test_the_swap_path_opens_no_socket` | 把 `socket.socket` 换成抛异常之后，`stage`/`poll` 照常工作？ |

最后一条是"换的那条路不做网络"的**执行面**判据：不是读源码找 `socket`，而是让它开不出来。

## 4. 这一版**不**做到的

1. **`loment update fetch` 这个命令还没挂进启动器。** 启动器的命令表是手写的两份（bash /
   batch）加一份 `lomcli.lomt`；挂上去的同时要写一份 Loment 侧的暂存实现（发行包里没有
   Python）。协议与语义在这一份里定死，挂命令是下一步。
2. **不做自动调度。** 0.1.5.1 的 `fetch` 是显式命令或一条 cron/CI 步骤；事件仍然是
   Pre-Release 本身。
3. **不做并发保护。** 同一份 `incoming/` 被两个 `fetch` 同时写不在这一版的范围里
   （`rename` 是原子的，最坏是多花一次下载）。
4. **不清理 `applied/`。** 它只增不减 —— `status`（`#1157`）要读历史。
