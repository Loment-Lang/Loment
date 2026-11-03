# 285 · 每个 Pre-Release 都带一份 `hotfix.json`（`#1153`）

> 上游：`#1146`（伞）、`#1147`（准入判据）。实现：`tools/loment_hotfix.py`；
> 落盘点：`tools/loment_dist.py` 的 `emit()`（在算 `SHA256SUMS` **之前**）；
> 判据：`tools/loment_hotfix_test.py`（7 条）。

## 1. 清单描述什么

**投影出来的构建**（`#1156`），不是发行二进制。发行二进制是完整安装，永远不会被热装。
一个没有 fix 提交的发布不产生热更新件，`artifact` 就留空 —— 清单**照样存在**（稳定版
也带一份，准入按 `not-prerelease` 拒绝它，那条路因此每年都被走到一次）。

```
schema       1
release      v0.1.5-hot.1          ← workflow 传进来的真 tag
prerelease   true                  ← tag 里有没有 "-"
baseline     0.1.5                 ← tools/loment_release.py 的 RELEASE
language     0.1.5                 ← 同上（今天两者同源，见 docs/278 §6.4）
contract     { "lom/fuai.lom": "<sha256>", ... }
capabilities [ "make_syscall", ... ]
surface      { keywords, syntax, diagnostics }        ← 三个 sha256
cli          "<sha256>"
fixed_core   "<sha256>"
functions    []                    ← #1156 的投影构建提供
projection   { baseline, line: [], evicted: [], tree: null }
artifact     { }                   ← 没有热更新件时为空
```

## 2. 两处与 issue 正文不同（都是判据逼出来的）

1. **契约用 sha256，不是 blake2b。** `#1153` 的表格写的是 blake2b，但准入判据要由**工具链
   自己**在进程里算（`loment/tools/update.lomt`），而工具链里只有 `sha256.lomt`。选一个它
   算不出来的算法，等于把准入锁死在 Python 里 —— 那正是 `docs/278` §2 拒绝的形状。
2. **`baseline` 从 `RELEASE` 读，不从 tag 猜。** 表格本来就写的是 "from `RELEASE`"；tag 上
   还挂着 `-hot.N`。猜出来的那个数与 `loment_update` 那边的基线不是同一个量。tag 与
   `RELEASE` 对不上时**生产侧直接拒绝**（一份清单不能同时属于两条基线）。

## 3. 接口面的三个摘要

`surface` 与 `cli` 是"一个被标成 `bug` 的功能"唯一过不去的地方（标签是散文，摘要是字节）：

| 字段 | 来源 |
|---|---|
| `surface.keywords` | `loment/tools/lomcli.lomt` 里 `is_kw()` 的词表 |
| `surface.syntax` | 同一文件的命令行（`row(mem, fd, "名字", "用法")`） |
| `surface.diagnostics` | `tools/lomentc.py` 里 `E0xx` 诊断码的集合 |
| `cli` | 命令名 + 用法串，按名字排序后的摘要 |

加一个词、加一条语法、加一个诊断码、加一条命令，这里都会动。**它量的是工具链自己发出去
的那一份**（`loment keywords` / `loment syntax` / `loment help` 打的就是它们），不是编译器
内部某个私有表 —— 这条边界写在这里，免得以后把它当"完整的语法面审计"。

## 4. 落盘顺序：先清单，后校验和

`emit()` 里清单在 `write_sums(out)` **之前**写。反过来的话，签名锚（`SHA256SUMS` + `.asc`）
压根不覆盖 `hotfix.json` —— 一个没被签的清单比没有清单更糟。

workflow 把真 tag 传给构建（`LOMENT_RELEASE_TAG`），于是 `release` / `prerelease` 跟着
tag 走，而不是从 `RELEASE` 猜。

## 5. 判据

| 判据 | 问法 |
|---|---|
| `test_every_field_is_computed_from_the_tree` | 13 个字段齐全、形状对、值非空？ |
| `test_the_contract_digest_follows_lom_fuai` | 改一行 `lom/fuai.lom` → `contract` 里那一条变，固定核不动？ |
| `test_the_cli_digest_follows_the_command_table` | 加一条 CLI 命令 → `cli` 变？ |
| `test_the_surface_digest_follows_a_new_keyword` | 加一个关键词 → `surface.keywords` 变、语法形态不动？ |
| `test_a_signed_prerelease_manifest_is_accepted` | 清单 + `SHA256SUMS` + 分离签名一起过准入（openssl 真签真验）？ |
| `test_a_stable_release_manifest_is_refused` | 稳定版也带清单，但准入按 `not-prerelease` 拒绝？ |
| `test_the_release_workflow_builds_it_before_signing` | workflow 传真 tag；清单先落盘、后算校验和；签名覆盖整个 dist？ |

## 6. 这一版**不**做到的

1. **`functions[]` 与 `projection` 是空的。** 它们由 `#1156` 的投影构建填；这一版只保证
   字段在、编码定死、两个实现同名。
2. **`artifact` 为空。** 没有热更新件时清单仍然发 —— 消费者靠 `prerelease` 与 `artifact`
   两条分别拒绝"稳定版"和"这不是一份热更新件"。
3. **接口面摘要不覆盖语义。** 它拦的是"新词/新语法/新码/新命令"，不是"某个内置函数悄悄
   改了行为" —— 那是固定核与投影的职责。
4. **清单不进安装器。** 安装走的是 `SHA256SUMS`（`loment_dist.py` 的安装脚本），清单是
   加性的，忽略它的安装器照常工作。
