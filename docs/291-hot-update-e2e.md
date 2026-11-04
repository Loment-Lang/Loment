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

投影那半边（`docs/289`/`docs/290`）：一个 fix 进线、一个标错的关键词被摘要剔除、
一个依赖 feature 的 fix 被剔除并点名、零 fix 没有产物、回滚标成 rollback。

## 2. 真的 `loment lsp`：在事件循环顶部换掉自己

`tools/loment_lsp_hot_test.py` 驱动**发行版那个语言服务**（`loment/tools/lsp.lomt`）：

1. 基线树与"投影后"的树各编一次（hotswap 档），候选由 `loment_hotart` 打出来；
2. 起 LSP（`lsp --hot <候选路径>`），`initialize` → `didOpen` → `textDocument/definition`；
3. 把候选**改名就位**（模拟"GitHub 那边出现了"），发一条 hover（这条还由旧代码回答）；
4. 下一条请求之前，事件循环顶部那一眼把候选装上 —— 之后的 `definition` 由**新代码**回答。

旧代码给定义范围起点 `hc - 1`（第 3 列），新代码给 `hc`（第 4 列）。判据断言 3 → 4：
**只有返回地址真的被改写、新代码真的在跑**才会变。

> 为什么不用改版本字符串来证明？字符串在 `.data` 里，而**数据是共享状态**、不随候选换
> （见 §4 第 1 条）。改一个立即数才是"代码换了"的真凭据 —— 这一条是被执行面判据逼出来的。

`--hot` 只在**hotswap 档**上有意义；发行包里 `loment-lsp` 就是按那一档构建的
（`loment_dist.TOOLS` 的第四格）。默认档的产物里固定核还在，但槽表不存在，`hot_install`
会先认出这一点并返回 15，一个字节都不动。

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
| `test_a_running_lsp_replaces_itself_at_the_safe_point` | 真的 `loment lsp`：换完之后定义范围 3 → 4，索引还在？ |

## 4. 这一版**不**做到的

1. **候选的 `.data` 不换。** 换的是代码与数据里的绝对指针；**字符串字面量与已初始化的
   全局量保持旧值** —— 数据是共享状态（`docs/284` §2）。所以"修一句提示文案"这种 fix
   不属于热更新，得重启。`docs/285` 的投影只看摘要不看这一条：一个只改字符串的 fix 能
   过准入，但装上去看不到效果 —— 这是 v1 的**语义边界**，不是 bug。
2. ~~没有"树 → 热更新二进制"的构建步骤。~~ `tools/loment_hotart.py` 补上了：两份
   hotswap 档（旧树 + 投影树）→ 两份 `.lomhot` + 新文本 → 装载件 `candidate.hot` +
   含 artifact 的清单；`loment_hotart_test`（4 条）证明它过准入、文本就是投影树的产物。
   发行流程现在会调用它，但**只在 workflow_dispatch 给了 `hot_base` 时**：投影 → 从投影树
   打包 → 落 `loment/build/hot/` 并单独上传，**不进 dist**（发行资产一个字节不变）。
3. **传输仍是进程外**（`docs/288` §4），命令还没挂进启动器。
