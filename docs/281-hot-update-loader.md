# 281 · 双槽装载器（`#1151`）

> 上游：`#1146`（伞）、`#1151`（切片 4）。参考模型：`tools/loment_hotload.py`；
> 判据：`tools/loment_hotload_test.py`（8 条）。元数据格式见 `docs/279`、`docs/280`。

## 1. 装载器要做的四件事

1. **搬**：把候选镜像的文本段从它链接时的 `base` 复制到**另一个槽** `slot`。
2. **改**：按 `.lomhot` 里的 `reloc` 表，把每条 8 字节绝对地址加上 `slot - base`。
   `call` / `jmp` / `jcc` 是 PC 相对的，**不在表里** —— 整体平移不改。
3. **验**：固定核摘要、槽表形状（`stride` / `slots`）、函数集合、每条槽的目标都落在新槽里。
   任何一条不成立就**拒**，旧镜像继续跑。
4. **翻**：把**旧镜像槽表**里的每条 `jmp rel32` 改指到新镜像的实现，然后发布新的 epoch。
   （旧帧还在旧代码里跑，所以这一步之后是"旧代码 + 新实现"；把帧也搬过去是 `#1152`。）

## 2. 元数据新增的两行

```
base 4194304
reloc 38 8388608
```

- `base` 是链接时的文本基址（今天 `TEXT_VADDR = 0x400000`）。
- `reloc <文本相对偏移> <目标地址>`：这一格是 8 字节绝对地址。两个实现写出来的表**逐字节相同**
  （镜像对拍 `loment_elf_test` 的三条判据覆盖）。
- 表的内容**只含我们自己的 `movabs`**（全局量、字符串、TLS 块、hotswap 的 epoch 字）。
  外部对象的重定位不在这张表里 —— v1 的 hotswap 档不与 `--link` 混用。

## 3. 参考模型与它**不**做的事

`tools/loment_hotload.py` 在**字节数组**上做上面四件事，不碰进程：

- `relocate(text, meta, slot)`：整体复制 + 逐条加 delta；
- `slot_targets(text, meta, base)`：读每条槽的 `E9 rel32`，给回目标地址；
- `vet(old, new, …)`：表形 / 固定核 / 函数集合 / 入口范围，逐条点名拒；
- `flip(old_text, old_base, slots, new_entries)`：把旧槽表改指新实现。

它**不**负责：`mmap` / `mprotect`、原子发布 epoch、释放旧槽、把活跃帧改指过去（`#1152`）。
那几件属于进程里的装载器（Loment 侧的固定核），参考模型只钉"搬过去以后每个字节该是什么"。

## 4. 判据

| 判据 | 问法 |
|---|---|
| `test_metadata_carries_a_relocation_table` | 每条 `reloc` 的偏移上，文本里的 8 字节值**就是**记录的目标？ |
| `test_relocation_shifts_every_absolute_value` | 搬到 `0x10000000` 后，每条都正好加了 delta？ |
| `test_new_slot_trampolines_stay_inside_the_slot` | 新镜像每条槽的 `E9` 都落在新槽内部？ |
| `test_flip_rewrites_the_old_slot_table` | 翻转后旧槽表每条 `jmp` 都指到新实现？ |
| `test_fixed_core_mismatch_is_refused` | 固定核摘要不同 → `fixed-core`？ |
| `test_slot_table_mismatch_is_refused` | `slots` 不同 → `table`？ |
| `test_function_set_mismatch_is_refused` | 函数集合不同 → `table`？ |
| `test_ping_pong_between_two_targets` | 连翻两次，槽表从 A 指到 B 再指回 A？ |

夹具两份镜像都由 `tools/lomentc.py` + `tools/lomelf.py` 从真的 `.lomt` 源编出来，
元数据是链接器写的，不手写。

## 5. 这一版**不**做到的

1. **进程里的装载器还没写**（Loment 侧的固定核）。它要做 mmap 两个槽、mprotect、发布 epoch、
   在安全点调用 `#1152` 的重映射。
2. **没有执行面判据**：参考模型不跑代码；"翻转后行为变了"要等进程里的装载器 + 一个真探针。
3. **数据段里的绝对指针不在重定位表里**。今天的产物把字符串等放进 `.data`，引用它们的是
   代码里的 `movabs`（已在表里）；但一份在**数据里**存指针的产物（例如函数指针表）不能这样搬。
   这是 v1 的边界，写在 `docs/280` §5 同一条口径里。
4. **外源对象（`--link`）不与 hotswap 档混用**：它们的重定位在链接期已应用，不在 `.lomhot` 里。
