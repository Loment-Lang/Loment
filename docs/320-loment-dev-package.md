# 320 — `dev` 包：开发者工具的十块补齐

`docs/317` 把领域库拆成八个包，`dev` 只有四个模块（`semver` `semrange` `spdx` `args`）。
那是"跑一次 CLI"的最低限度；真正写工具时缺的都在**输入解析**与**文本改写**这一侧：
配置怎么读、命令行怎么拆、补丁怎么打、变更日志怎么出。这一份补十块。

## 落了什么

| 模块 | 公开面 | 解决什么 |
| --- | --- | --- |
| `duration` | `du_parse` `du_format` | Go 风格时长（`1h30m`、`250ms`、`1.5s`）的解析与格式化 |
| `target` | `tgt_parse` `tgt_part` | 目标三元组拆分；三段式里第二段按 **OS 名**判定（`x86_64-linux-gnu` 与 `x86_64-unknown-linux` 都成立） |
| `shellwords` | `sw_split` `sw_needs_quote` `sw_quote` `sw_quote_span` `sw_join` | POSIX shell 词法：引号、反斜杠、`'\''` 转义 |
| `dotenv` | `de_get` `de_count` `de_key_at` | `.env`：`export`、单双引号、行内注释 |
| `template` | `tpl_render` | `{{key}}` 替换；**未知键原样保留**；不递归 |
| `commit` | `cm_parse` `cm_parse_ptr` `cm_span_eq` | Conventional Commits → 48 字节定长结构 |
| `changelog` | `cl_generate` | 按 Features/Bug Fixes/Performance/Refactoring/Documentation/Other 分组出 Markdown |
| `jsonptr` | `jp_resolve` `jp_get_u64` `jp_get_str` | RFC 6901 指针；`~0`/`~1` 解码、数组下标严格规则 |
| `gitignore` | `gi_match` | 最后一条匹配赢、`!` 重新包含、`/` 锚定、`**` |
| `patch` | `pa_apply` | 统一 diff 的应用；**上下文逐字节核对**，不符返回 `PA_ERR` |

## 几条设计边界

- **不建树**：`jsonptr` 直接在原文上用 `jsn` 的游标 API 走，不复制 JSON。
- **不猜**：`patch` 上下文不匹配就报错（宁可不打，也不打到错位置）；`template`
  未知键保留原样；`gitignore` 严格按 git 的"最后一条赢"，不提供自己的优先级。
- **不重写整个文件**：所有输出接口都是 `(out, cap)`，返回**完整长度**；长度大于
  `cap` 就是"缓冲不够"，调用方自己决定重试。
- **数组入参是 `char**`**：`template` 的键值、`changelog` 的提交标题、`gitignore`
  的模式表都是 NUL 结尾的 C 字符串数组 —— 与 `args`、`pack` 一致，不和 `str` 混。

## 判据

- `loment_domains_test::test_dev_tooling_packages`：29 项行为，stdout 与 Python 的
  预期逐字节相同（`duration` 的 5400000000000、"1h30m0s"、`target` 的 os=5/env=3、
  `shellwords` 的词数与引用、`dotenv` 的值、`template` 的渲染、`commit` 四个字段长度、
  `changelog` 的完整 Markdown、`jsonptr` 的 20、`gitignore` 的 1/0、`patch` 的
  `a\nb\nc\n`）。
- 孪生：10 个新模块 + 门面 `dev.lomt` 共 11 份，参考实现与自举实现产物**逐字节相同**。
- 计数：`dev` 5 → 15 个 `.lomt`，整棵树 217 → **227** 个文件
  （`loment_lompi_test` 与 `loment_release` 的发布清单同步）。

## 命名

`target` 的前缀原本想用 `tg_`，但 `tg_` 在全局平命名空间里已被别的模块占了两处，
改用 `tgt_`。其余前缀 `du_` `sw_` `de_` `tpl_` `cm_` `cl_` `jp_` `gi_` `pa_` 均无冲突。
