# 357 · 热更新能修链接器：工具链代际与原生更新器

> 上游：`#1828` / `#1830` / `#1695`。实现：`loment/tools/update.lomt`、
> `tools/loment_hotpack.py`、`tools/loment_dist.py`。判据：`tools/loment_hotpack_test.py`（6 条）。

## 1. 问题

`v0.1.5.1-hot.1` 只能换**跑着的 LSP 映像**。链接器（`bin/loment-lomelf`）、编译器
（`bin/loment-driver`）、CLI、fmt、doc 这些是 `bin/` 里的独立程序，载荷里没有它们 ——
所以构建期修复（GEP 首下标、PE `openat`）装完热更新也不会生效。存量 0.1.5.1 安装更糟：
包里没有更新器、没有固定公钥、也没有准入对照面，`loment hotupdate` 根本不存在。

## 2. 形状：两个代际 + 一个指针

装好的前缀里：

```
bin/loment-hotupdate                      原生更新器（信任根，永不参与热更新）
share/loment/hot/release-key.ed25519      钉住的裸 Ed25519 公钥（32 字节）
share/loment/hot/baseline.txt             准入对照面（language/contract/capability/fixed_core）
share/loment/hot/current                  当前代际：a / b / 0（0 = 基线 bin/）
share/loment/hot/gen/a/bin/<tool>         代际 A
share/loment/hot/gen/b/bin/<tool>         代际 B
```

- 两个代际目录由**安装器**建好；更新器只往**非活动**的那一格写。全部文件写完、逐条摘要核过，
  才写 `current`。旧代际原样保留。
- `--rollback` 把指针切回另一格；另一格没装过就回到 `0`（基线 `bin/`）。
- 启动器（bash 与 cmd）在解析工具时**先看 `current`**：`gen/<g>/bin/<tool>` 存在就用它，
  否则用 `bin/`。`loment-hotupdate` 例外 —— 它是信任根，永远从 `bin/` 起。

## 3. 载荷：`HOTG` v1

```
0   4B  "HOTG"
4   4B  version = 1
8   4B  count
12  4B  reserved
16  count × 128B 索引:
      name[48]（ASCII，NUL 补齐；manifest.txt / bin/<tool>）
      sha512[64]
      off u32（相对数据区）
      len u32
      mode u32
      reserved u32
data_start = align8(16 + count*128)
```

索引之后是 `manifest.txt` 与各工具的字节。**整份载荷**用裸 Ed25519 签名（`.sig`，64 字节）；
签名覆盖清单，清单里有：

| 行 | 含义 |
|---|---|
| `schema 1` | 格式版本 |
| `platform linux-x64` / `windows-x64` | 与载荷文件名一致 |
| `release v<RELEASE>-hot.<N>` | 发布 tag |
| `base_digest <sha512(baseline.txt)>` | 候选属于哪条基线 |
| `fixed_core <sha512(release-key.ed25519)>` | 信任锚没换 |
| `file <name> <sha512> <mode>` | 每个文件 |
| `fix <sha>` | 投影线上的修复提交（至少一条） |

哈希用 **SHA-512**：更新器与 `ed25519` 共用随包发的 `std` 模块，工具链自己的自举链能编；
不依赖 Python/openssl/gpg。

## 4. 只带修复

载荷从 `loment_projection` 的**投影树**编，不是从 R 编。投影第 3 关（契约 / 语言版本 /
能力 / surface / CLI 与基线逐条相同）先拦掉"标成 bug 的功能"；清单里的 `fix` 行给出这次
到底带了哪几条。没有 fix 的清单在打包与更新器两侧都会被拒绝。

## 5. 固定核

- 更新器**不进载荷**。它能验签、能写代际，载荷不能替换它。
- `fixed_core` 只钉发布公钥：运行树里的 `release-key.ed25519` 的 SHA-512 必须同时等于
  `baseline.txt` 与候选清单里的值。换了钥匙 → 失败关闭。
- 公钥与基线由**完整包**（或首支热更新的自举脚本）放进运行树；热更新自己不写这两份。

## 6. 首支的引导（存量 0.1.5.1）

存量安装没有更新器，也没有上面三份材料。首支可修链接器的热更新因此带一个**自举脚本**
（`bootstrap-hotfix.sh` / `.cmd`）：

1. 用户从发布页核对脚本、`loment-hotupdate-<platform>` 与两份启动器的摘要（与 `#419` 的
   发行锚同一条纪律：摘要发布在归档之外）；
2. 脚本建好两个代际目录，把更新器、公钥、基线放进前缀，**并换上新启动器**
   （`bin/loment` / `bin/loment.cmd`）—— 存量 0.1.5.1 的启动器只看 `bin/`，不换就等于
   载荷落了盘却没人用；
3. 运行更新器装载荷。

另一条路是直接装**新完整包**：安装器已经建好代际目录、装上新启动器。自举脚本服务的是
"不想重装、只想要修复"的存量安装。

之后这就是普通安装了：下一次热更新只需 `loment hotupdate --payload <file> --sig <file>`，
不再需要自举脚本。

**旧启动器**（0.1.5.1 那份）没有 `hotupdate` 内建，未知子命令只在 **PATH** 上找
`loment-hotupdate`。自举脚本已经把它装进 `<prefix>/bin/`，所以旧安装上下一次可以直接跑
`<prefix>/bin/loment-hotupdate --prefix <prefix> --payload <file> --sig <file>`；装过一次
新完整包之后，`loment hotupdate` 才由启动器转发。

## 7. 边界

- **载荷 32 MiB**：PE 垫片的 `brk` 堆是 64 MiB，更新器要同时放载荷、基线、路径。打包器在
  超过 32 MiB 时直接红，不发一份装不进去的载荷。
- **LSP 的内存换**仍保留（`v0.1.5.1-hot.1` 的那条路）：跑着的进程能在安全点换；落盘代际
  管的是**下一次启动**与**构建期工具**。两者不冲突。
- **Windows**：PE 工具进载荷；启动器用 `%GH%` 解析代际；更新器是 PE，走修过的 `openat`。
- **仍然不是安全边界**：本地的写权限可以替换更新器本身。威胁模型是候选/网络；更新器属于
  安装底座。

## 8. 判据

`tools/loment_hotpack_test.py`（7 条，登记在 `ci.py` 与 `.github/gate-floors.txt`）：

| 判据 | 问法 |
|---|---|
| `test_the_updater_is_built_by_the_selfhost_chain` | 自举链能出 ELF 与 PE 两份更新器？ |
| `test_check_apply_and_rollback_over_a_signed_payload` | 真载荷 check / apply / apply / rollback：a → b → a？ |
| `test_a_tampered_payload_is_refused` | 改一个字节 → 签名不过，什么也没写？ |
| `test_a_payload_without_fixes_is_refused` | 没有 `fix` 行 → 拒绝？ |
| `test_a_wrong_pinned_key_is_refused` | 运行树公钥被换 → `fixed_core` 对不上？ |
| `test_a_linker_from_the_payload_links_a_program` | 代际里的链接器编出的能力域程序跑得通（rc=0）？ |
| `test_the_launcher_prefers_the_active_generation` | `current` 一写，真启动器就改用代际里的工具？自举脚本也确实换上了新启动器？ |
