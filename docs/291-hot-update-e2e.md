# 291 · 端到端：一次 fix 进到跑着的进程里，一次契约变更被拒（`#1155`）

> 上游：`#1146`（伞）。判据：`tools/loment_hotcore_test.py`（7 条，真进程）、
> `tools/loment_hotline_test.py`（6 条）、`tools/loment_projection_test.py`（6 条）。

## 1. 这条判据要证明的三件事

1. **换的是代码，不是状态。** 换之前由旧代码写满的一块"索引"（256 字节），换之后由**新
   代码**原样读回来，校验和不变（`test_the_index_survives_the_swap`）。
2. **版本号来自代码。** 每一帧读到的版本常量是 `nest` 函数体里的**字面量** —— 不是环境
   变量、不是 `--version` 文件。只有返回地址真的被改写，它才会从 1 变成 2
   （`test_every_live_frame_continues_in_the_new_image`）。
3. **拒绝是点名的。** 固定核摘要（RC 7）、槽表形状（RC 4）、帧布局（`(1<<12)|slot`）、
   调用点 id 序列（`(2<<12)|slot`）—— 旧代码继续跑，`SUM` 仍是旧实现的值。

投影那半边（`docs/287`/`docs/288`）：一个 fix 进线、一个标错的关键词被摘要剔除、
一个依赖 feature 的 fix 被剔除并点名、零 fix 没有产物、回滚标成 rollback。

## 2. 这条判据**不**证明什么

**它跑的不是发行版里的 `loment lsp`。** 发行版的那个二进制还没有在事件循环顶部调用
安全点（`hot_tick`），也没有用 `--profile hotswap` 构建。要让它成立，还差两步：

1. `loment lsp` 的 `while go == 1` 顶部加一次调用：看一眼暂存目录，有候选就交给固定核；
2. 发行包的 `lsp` 用 `--profile hotswap` 构建（槽表与入口闸就位）。

这两步都是**小改动**，但它们改变的是"发行版的行为"，不是这条线的判据 —— 所以没有塞进
这一版：判据在这里用的是同一个固定核、同一套准入、同一份投影，只是宿主程序是一个
最小的 LSP 形状的探针。

## 3. 判据

| 判据 | 问法 |
|---|---|
| `test_every_live_frame_continues_in_the_new_image` | N = 1/2/16 层，每层都读到新版本常量？ |
| `test_the_index_survives_the_swap` | 旧代码写满的索引，新代码读回来的校验和不变？ |
| `test_the_swap_reports_one_epoch` | 装完 epoch 是 READY？ |
| `test_tampered_digest_is_refused` | 闸改一字节 → RC 7，什么都没动？ |
| `test_slot_shape_mismatch_is_refused` | 槽表条数不同 → RC 4？ |
| `test_frame_layout_change_is_refused_by_name` | 布局变了 → 拒绝码点名槽号？ |
| `test_call_id_change_is_refused_by_name` | id 序列变了 → 拒绝码点名槽号？ |

## 4. 这一版**不**做到的

1. **发行版 `loment lsp` 不参与热更新**（见 §2）：事件循环顶部还没有安全点调用。
2. ~~没有"树 → 热更新二进制"的构建步骤。~~ `tools/loment_hotart.py` 补上了：两份
   hotswap 档（旧树 + 投影树）→ 两份 `.lomhot` + 新文本 → 装载件 `candidate.hot` +
   含 artifact 的清单；`loment_hotart_test`（4 条）证明它过准入、文本就是投影树的产物。
   **发行流程还没有调用它** —— 投影跑在哪、什么时候跑，是 workflow 的事（现由
   `loment_projection` 手工/CI 驱动）。
3. **传输仍是进程外**（`docs/286` §4），命令还没挂进启动器。
