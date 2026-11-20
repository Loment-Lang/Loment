# 278 · 热更新的准入判据（`#1147`）

> 上游：issue `#1146`（伞）、`#1147`（切片 1）。参考实现：`tools/loment_update.py`；
> 判据：`tools/loment_update_test.py`（13 条）。

## 1. 一句话

**一个 Pre-Release 能不能被正在跑的工具链换进去，不看 changelog，看五个等式。**

- 契约：`lom/*.lom` 逐条 sha256 相同；
- 语言版本：`tools/loment_release.py:RELEASE` 相同（`standard/LLS.md` §6.4：语言版本只有这一个数）；
- 能力清单：工具链自己声明的 `capability` 集合相同；
- 基线：`<major>.<minor>.<patch>` 相同；
- 固定核：`loment/hotswap/fixed.txt` 列出的文件摘要相同（今天这份清单还不存在，摘要为空串的 sha256）。

外加：分离签名验得过、版本单调、不是 `-pre` 宣告版、没有被重放过。

**为什么是等式**：标签是散文，摘要是字节。一个把新语法标成 `bug` 的提交，照样过不了语言版本与契约。

## 2. `hotfix.json`

```json
{
  "schema": 1,
  "release": "v0.1.5-hot.1",
  "prerelease": true,
  "baseline": "0.1.5",
  "language": "0.1.5",
  "contract": { "lom/fuai.lom": "<sha256>", "...": "..." },
  "capabilities": [],
  "fixed_core": "<sha256>",
  "identity": "<sha256>",
  "functions": [],
  "artifact": { "name": "loment-hotfix-v0.1.5-hot.1.tar.gz", "sha256": "<sha256>" }
}
```

- **sha256，不是 blake2b**：工具链里已经有 `lompi/store/std/.../sha256.lomt`，没有 blake2b。
  准入判据由工具链自己执行，摘要算法必须是它自己能算的那个。
- `identity` 是工具链实现的摘要（`loment/tools/*.lomt`、`loment/selfhost/*.lomt`、
  `tools/lomentc.py`、`tools/lomelf.py`、`tools/lomfmt.py`、`tools/loment.py`）。
  它是 `#900`（升级后 build 缓存必须失效）要的那个"身份变了"的落点。
- `functions` 属于切片 2（`#1149`），这一版恒为空数组。

## 3. 判据

`tools/loment_update_test.py` 的每一支都问同一个问题：**换掉一个字节，还过不过？**
夹具不是手写清单：每份 manifest 都由 `manifest()` 从**真的树**算出来，树是从本仓拷出来再改一处的；
签名用 openssl 真签真验。

| 判据 | 改什么 | 期望 |
|---|---|---|
| `test_manifest_is_computed_from_the_tree` | 无 | 每个字段都从树里算出来 |
| `test_implementation_only_candidate_is_accepted` | 只改 `lomcli.lomt` | 接受，且 `identity` 变了 |
| `test_contract_change_is_rejected` | `lom/fuai.lom` + 一个字节 | `contract` |
| `test_language_change_is_rejected` | 树里 `RELEASE` | `language` |
| `test_baseline_change_is_rejected` | tag 说别的基线 | `baseline` |
| `test_capability_change_is_rejected` | 多一条 `capability` | `capabilities` |
| `test_fixed_core_change_is_rejected` | 固定核里的文件变了 | `fixed-core` |
| `test_signature_tamper_is_rejected` | `SHA256SUMS` + 一个字节 | `signature` |
| `test_stable_release_is_rejected` | 稳定版带清单 | `not-prerelease` |
| `test_announcement_only_tag_is_rejected` | `-pre` 宣告版 | `announcement-only` |
| `test_replay_is_rejected` | 同一个 tag 装两次 | `replay` |
| `test_version_must_move_forward` | 版本不前进 | `version`（显式回滚放行） |
| `test_cli_accepts_the_same_candidate` | 命令行入口 | `ACCEPT` |

## 4. 这一版**不**做到的

1. **只有参考实现。** `loment/tools/update.lomt`（Loment 写的那一份）还没写。原因在 §5：
   签名那一步 Loment 今天做不了。
2. **签名只认 openssl 的 PEM 公钥**（`loment-signing.pub.pem`）。发布流水线今天用的是 gpg，
   导出的是 `loment-signing.asc`；切片 6（`#1153`）要连带发布 PEM，或者准入侧改用 `proc_sh`
   调 gpg。**在补上之前，真实 Pre-Release 过不了这一关是预期的。**
3. **`functions` 恒为空**，函数级元数据在切片 2。

## 5. 顺带查出来的两件硬事实（会改设计）

### 5.1 `-pre` tag 今天**不出包**

`.github/workflows/release.yml` 在文件头就写了：`-pre` 是"宣告版"，整个作业跳过，
**一个 asset 都不挂**。所以"Pre-Release 发布 = 一次热更新"不能落在 `-pre` 上。

可用的形状是 **`v<RELEASE>-hot.<N>`**：它带 `-`，于是 GitHub 上是 pre-release、走带产物那条路；
又不是 `-pre`，不被跳过。这一条要写进切片 6/9。

### 5.2 tag 必须严格等于 `v<RELEASE>`

同一个 workflow 的第一步硬要求 `got == want`（`want` 就是 `loment_release.RELEASE`）。
于是**每发一次热更新就改一次 `RELEASE`** —— 而改 `RELEASE` 就是改语言版本，正好违反等式 2。
这两件事不能同时在一条流水线里成立。

⇒ 热更新需要**自己的发布通道**（独立 workflow 或 release.yml 里一条显式的分支），
它不动 `RELEASE`，只把投影出来的树打包、签名、发成 `v<RELEASE>-hot.<N>`。
这条是切片 6（`#1153`）与切片 9（`#1156`）的前置。

## 6. 登记

- `tools/ci.py`：`STATIC_CHECKS` + `SHARD_COST`（本机 Windows 实测 9.4s）。
- `.github/gate-floors.txt`：`loment_update_test 13`（双向棘轮）。
- `tools/loment_release.py:GLOBS` 与 `loment/tools/lomrel.lomt:globs_text()`：**同一位置**。
