# 279 · 热更新的元数据：帧布局哈希 + 调用点表（`#1149`）

> 上游：`#1146`（伞）、`#1149`（切片 2）。参考实现：`tools/lomelf.py`；
> 判据：`tools/loment_hotmeta_test.py`（7 条）；准入侧消费：`tools/loment_update.py`。

## 1. 它解决什么

强制重映射（`#1152`）要在安全点把**每一帧的返回地址**从旧镜像改指到新镜像。这件事只有在一张
表存在时才做得到：**旧偏移 → 稳定 id → 新偏移**。这张表只能由发射器产生 —— 事后从反汇编里
重建，等于把发射器写第二遍，而且两边一定会分歧。

## 2. 格式

`lomelf --hot-meta PATH` 落一份**旁挂文本**（sidecar），默认不写。

```
lomhot 1
func _start frame=32 layout=<64 hex>
call add@0 at=37
call extern:syscall4@0 at=64
func add frame=16 layout=<64 hex>
```

- **顺序是确定的**：函数按 IR 顺序，调用点按发射顺序。
- `at` 是**函数相对偏移**（从函数入口算起），不是虚拟地址 —— 换一次链接、换一个槽地址都不改它。
- id 是 `callee@序号`，序号是"这个函数里第几次调用同一个 callee"。外部调用带 `extern:` 前缀，
  因为它们的 ABI 与内部调用不同，不能混为一谈。
- 记的位置是**返回地址**：`call` 指令之后的第一条指令。重映射把旧返回地址换成新二进制的
  同一个 id 的偏移，于是调用者在新镜像里接着跑。
- `layout` 是帧布局的 sha256：`frame=<大小>`、`self.slots` 的全部偏移（排序去重）、
  `self.allocas` 的 `偏移/大小`（排序）。槽位是 `[rbp-x]`，所以偏移相同 = 新代码读得到旧帧里的值。

## 3. 为什么是旁挂而不是 ELF 节

issue 里写的是"`.lomhot` 节"。这一版落成**旁挂文件**，理由是：

1. **默认产物的字节一个都不许变。** 往每个 ELF 里加一个节会改掉所有已有判据的字节对拍基线，
   而这一格要的只是"能算出来"。
2. 元数据要跟产物一起走 —— 热更新包里本来就有 `hotfix.json` 与 `SHA256SUMS`，多一个旁挂件
   不比多一个节弱。**什么时候并进 ELF 是装载器（`#1151`）的决定，不是发射器的。**

判据 `test_collection_does_not_change_the_artifact` 钉住第 1 条：采集开/关，ELF 逐字节相同。

## 4. `remap` 怎么判

`loment_update.remap_plan(旧, 新)` 逐函数给两个条件：

| 条件 | 不变时 | 变了时 |
|---|---|---|
| `layout` 相同 | 新代码读得到旧帧里的槽 | `remap: false (layout)` —— 只能等它没有活跃帧 |
| 调用点 id 序列相同 | 返回地址一一对上 | `remap: false (calls)` —— 同一个 id 不再指同一个调用 |

两个都成立才是 `remap: true`。这是 `#1152` 的输入，也是切片 9"剔除并点名"要用的那张表。

## 5. 判据

| 判据 | 问法 |
|---|---|
| `test_metadata_is_deterministic` | 同一份源编两次，元数据逐字节相同？ |
| `test_every_function_has_a_frame_and_calls_are_listed` | 每个函数都有帧与布局，调用有 id 与偏移？ |
| `test_collection_does_not_change_the_artifact` | 采集开/关，ELF 逐字节相同？ |
| `test_layout_changes_when_a_temporary_is_added` | 多一个临时量 → 布局哈希变、`remap: false (layout)`？ |
| `test_one_line_change_keeps_layout_and_ids` | 只改一个常量 → 布局与 id 都不动、两个函数都可重映射？ |
| `test_added_call_marks_the_function_not_remappable` | 多一个调用 → id 序列变、`remap: false (calls)`？ |
| `test_cli_writes_the_sidecar` | `--hot-meta` 落出的就是同一份元数据？ |

IR 全部由 `tools/lomentc.py` 从真的 `.lomt` 源编出来，不手写。

## 6. 这一版**不**做到的

1. **只有参考实现。** `loment/tools/lomelf.lomt` 还没有 `--hot-meta`。它要按 `docs/257`
   的 `.lomf` 补丁流程改，且**两份输出必须逐字节相同**（孪生纪律）。这是 `#1149` 剩下的那一半。
2. **旁挂，不是 ELF 节**（理由见 §3）。
3. **`frame` 里只记偏移与大小，不记每个槽的 SSA 名字。** 两个值互换槽位而偏移集合不变时，
   哈希看不出来 —— 那是"同一布局的不同赋值"，新代码按旧帧读仍然读的是同一个值的位置，
   影响落在 `#1152` 的映射规则里，不在这一格的格式里。
4. **入口闸（`#1150`）还没有调用点。** 它落地后每个函数会多一条 `__gate@0`，这张表自动跟着变。
