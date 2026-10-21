# 280 · 热更新的构建档：稳定槽表 + 入口闸（`#1150`）

> 上游：`#1146`（伞）、`#1150`（切片 3）。参考实现：`tools/lomelf.py`；
> 判据：`tools/loment_hotswap_test.py`（7 条）；元数据格式见 `docs/279`。

## 1. 这一档改了什么

`lomelf --profile hotswap` 让产物多两样东西，**默认档一个字节都不动**：

1. **槽表**：排在文本段最前面，`TEXT_VADDR + slot * 16`，每槽一条 `jmp rel32` 到当前实现。
   内部调用与入口桩调 `_start` 都改走槽 —— 实现挪了，调用方的返回地址不动。
2. **入口闸**：每个函数在序言之后先 `call __loment_hot_gate`，闸读一个全局 epoch 字；
   为零就直接返回（今天的全部情形），非零走候选那条路（装载与翻转在 `#1151`）。

槽号按**函数名排序**分配，所以同一份 IR 每次都落在同一个地址。函数集合一变，后面的槽
整体位移 —— 这不是缺陷，是**可判的**：元数据里每个函数带 `tramp=<slot>`，准入侧逐条比，
表形变了就拒绝（`#1152` 的 `remap_plan`）。

## 2. 元数据多了什么

```
fixed gate=<文本相对偏移> stride=16 slots=<n> digest=<sha256>
func _start entry=<文本相对偏移> frame=… layout=… tramp=<slot>
call __gate@0 at=…
```

- `__gate@0` 是每个函数的**第一个**调用点；它与其它调用点一样进重映射表。
- `digest` = sha256（闸的字节 + `stride=16`）。这是固定核的真身：换版时它必须相同，
  否则装载器拒绝。**它比 `loment/hotswap/fixed.txt` 那份文件清单更硬** —— 后者是"哪些
  源文件属于固定核"，前者是"编出来的固定核到底是不是同一段机器码"。
- `entry` 与 `at` 都是**文本相对**的；装载器用 `TEXT_VADDR + entry` 算实际地址。

## 3. 判据

| 判据 | 问法 |
|---|---|
| `test_default_profile_matches_origin_main` | default 档与 `origin/main` 的 lomelf 现编一份逐字节相同？ |
| `test_slots_exist_only_in_the_hotswap_profile` | 默认档没有槽；热更新档每函数一个槽、有 64 hex 的固定核摘要？ |
| `test_internal_calls_target_the_slots` | 每条内部调用的 `E8` 都落到被调方的槽上？（读 ELF 字节，不反汇编） |
| `test_gate_is_the_first_site_and_targets_the_gate` | 每个函数第一个调用点是 `__gate@0` 且跳到闸本身？ |
| `test_each_slot_jumps_to_its_own_function` | 每条槽的 `E9` 都落回自己的入口？ |
| `test_cost_is_a_count_not_a_ratio` | 代价是"n 个函数 = n 条闸调用 + n 条槽"，不是墙钟比值？ |
| `test_behaviour_is_identical_under_wsl` | 两档在 WSL 里 stdout 与退出码逐字节相同？ |

第 1 条不是拿常量对拍：它把 `origin/main:tools/lomelf.py` 取出来、在子进程里**真编一份**，
再比 sha256。取不到 `origin/main` 就 SKIP 并说原因。

## 4. 代价

**每个函数 +1 条 `call`（5 字节），每个函数 +1 个槽（16 字节）**，外加一次闸的固定开销。
没有逐调用的间接跳转 —— 槽里的 `jmp` 只在**跨函数**时多一跳。这是 IR 层的计数，
判据里不出现墙钟比值。

## 5. 这一版**不**做到的

1. **只有参考实现。** `loment/tools/lomelf.lomt` 还没有 `--profile hotswap`（走 `.lomf`，
   两份输出须逐字节相同）。
2. **闸的非零路径是空的。** epoch 非零时什么也不做，真正的装载/翻转是 `#1151`。
3. **`loment build --profile hotswap` 还没接。** 今天只有 `lomelf --profile hotswap`；
   把档位从构建前端传下来是下一格的事。
4. **入口桩的返回地址在固定核里。** `__entry` 调 `_start` 的那条返回地址不参与重映射
   （它不换），`#1152` 走帧链时必须把固定核的地址段跳过去 —— 闸与入口桩的区间要一起判。
