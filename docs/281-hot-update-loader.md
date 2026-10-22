# 281 · 双槽装载器（`#1151`）

> 上游：`#1146`（伞）、`#1151`（切片 4）。参考模型：`tools/loment_hotload.py`；
> 判据：`tools/loment_hotload_test.py`（8 条）。元数据格式见 `docs/279`、`docs/280`。

## 1. 装载器要做的四件事

1. **搬**：把候选镜像的文本段从它链接时的 `base` 复制到**另一个槽** `slot`。
2. **改**：按 `.lomhot` 里的 `reloc` 表，把**指向本镜像代码**的 8 字节绝对量加上
   `slot - base`；**指向数据段的绝对量保持原值** —— 热更新换的是代码，不是状态，
   新镜像看见的必须是同一份全局量 / 字符串 / epoch 字。
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
- **装载器按目标地址分类**：落在 `[base, base+文本长度)` 里的才加 delta，其余（数据段）
  一律不动。今天没有函数指针，所以实际上一条代码目标都没有；这条规则是给以后留的，
  也是执行面判据当场纠正过的一处 —— 第一版把 epoch 的地址也平移了，新镜像的闸会去读
  一块没映射的地址。

## 3. 参考模型与它**不**做的事

`tools/loment_hotload.py` 在**字节数组**上做上面四件事，不碰进程：

- `relocate(text, meta, slot)`：整体复制 + 逐条加 delta；
- `slot_targets(text, meta, base)`：读每条槽的 `E9 rel32`，给回目标地址；
- `vet(old, new, …)`：表形 / 固定核 / 函数集合 / 入口范围，逐条点名拒；
- `flip(old_text, old_base, slots, new_entries)`：把旧槽表改指新实现。

`tools/loment_hotload_exec.py` 负责**进程里的那一半的参考实现**：用 `ctypes` 直接调
`mmap` / `mprotect`，把 A 的文本段与数据段映射回它链接时的地址（文本 RWX、数据 RW），
把 Q 搬到槽 B 并 `mprotect` 成 RX，改 A 槽表里那一条 `jmp`，再通过一条按 Loment 调用约定
写的小桩**真调**两次：翻转前 3、翻转后 4。epoch 的地址不是猜的 —— 从入口闸那条
`movabs` 的立即数里读出来，断言它落在共享数据段里。

它**不**负责：原子发布 epoch（今天只是读写同一个字）、释放旧槽、把活跃帧改指过去
（`#1152`）。

### 3.1 固定核：`loment/lib/hotswap.lomt`

同一套步骤的 **Loment 实现**（这才是会随工具链一起发出去的那一份）：

- `hot_install(blob, n)` 收一份扁平装载件（头 + 重定位表 + 函数表 + 候选文本），
  自己 `mmap` 第二个槽（`syscall7(9, …)`）、复制、按目标分类改绝对量、`mprotect` 成 RX；
- 固定核摘要**在进程内现算**：`sha256(本镜像的闸字节 + "stride=16")` 与候选的逐字节比；
- 改自己的槽表时**不能撤 EXEC** —— 装载器自己正从那一页取指。这一版为了自包含，
  补丁期间用 RWX（真实的固定核应该住在一块单独的、不参与换页的区域，那样可以一直 RW 或
  用双映射）；打完立刻收回 RX。
- 失败**不终止进程**：返回一个数字（1 头太短 / 2 魔数 / 3 长度 / 4 槽表 / 5 闸长 /
  6 内存 / 7 摘要 / 8 mmap / 9 地址 / 10 mprotect / 11 槽号 / 12 入口 / 13-14 自改），
  由调用方点名。探针把返回码与结果一起打出来。

`tools/loment_hotcore_test.py` 让一个真的 Loment 探针**换掉自己**：先调 `add(1,2)` 得 3，
把装载件交给固定核，再调一次得 4；epoch 由装载器写成 1。另两条判据把候选的闸改一个字节
（返回 7）、把槽表条数改掉（返回 4），两次都保持 `AFTER 3`、`EPOCH 0`。

## 4. 判据

| 判据 | 问法 |
|---|---|
| `test_metadata_carries_a_relocation_table` | 每条 `reloc` 的偏移上，文本里的 8 字节值**就是**记录的目标？ |
| `test_data_references_are_shared_not_shifted` | 搬到 `0x10000000` 后，数据引用**保持原值**？ |
| `test_new_slot_trampolines_stay_inside_the_slot` | 新镜像每条槽的 `E9` 都落在新槽内部？ |
| `test_flip_rewrites_the_old_slot_table` | 翻转后旧槽表每条 `jmp` 都指到新实现？ |
| `test_fixed_core_mismatch_is_refused` | 固定核摘要不同 → `fixed-core`？ |
| `test_slot_table_mismatch_is_refused` | `slots` 不同 → `table`？ |
| `test_function_set_mismatch_is_refused` | 函数集合不同 → `table`？ |
| `test_ping_pong_between_two_targets` | 连翻两次，槽表从 A 指到 B 再指回 A？ |
| `test_executes_in_a_real_process` | 真进程里翻转前后调同一个函数：3 -> 4？epoch 在共享数据段？ |
| `test_fixed_core_flips_its_own_table` | 固定核（Loment）在自己进程里装槽表：3 -> 4、返回 0、epoch=1？ |
| `test_tampered_digest_is_refused` | 候选的闸改一个字节 → 返回 7，`AFTER 3`、`EPOCH 0`？ |
| `test_slot_shape_mismatch_is_refused` | 槽表条数不同 → 返回 4，没装？ |

夹具两份镜像都由 `tools/lomentc.py` + `tools/lomelf.py` 从真的 `.lomt` 源编出来，
元数据是链接器写的，不手写。执行那条在 Windows 上走 WSL，Linux 上原生。

## 5. 这一版**不**做到的

1. **固定核还是"自包含探针"形态**：它改了正运行的那一页（RWX 窗口）、只按**条数**核对
   函数集合（不逐名比）、并且还没有接进工具链的构建（`loment build --profile hotswap`
   的管线还缺）。真实形态要把它放在单独的不换页区域，并逐名核对槽表。
2. **epoch 只是读写同一个字**，没有 release/acquire 语义，也没有"翻转进行中"的保护；
   旧槽也不释放（`#1152` 之前旧帧还在里面跑）。
3. **数据段里的绝对指针**：今天的产物把字符串等放进 `.data`，引用它们的是代码里的
   `movabs`（在表里，且**不平移**——数据是共享的）。一份在**数据里**存**代码**指针的产物
   （函数指针表）才需要按目标分类做真正的重定位；语言今天没有函数指针。
4. **外源对象（`--link`）不与 hotswap 档混用**：它们的重定位在链接期已应用，不在 `.lomhot` 里。
