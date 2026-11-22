# 376 · 可重演链：在 provenance 之上补闭包、种子绑定与预算

> 状态: **V0 已落地**（2026-11-22，基于 `origin/main` `b6bfb02f`）
> 上游: `#776`（`provenance.json`）· `docs/167` §5（genesis）· `docs/159`（种子自举）·
> `docs/160`（审计包）· `docs/203`（第三方复核）· `docs/358`（只信产物）·
> `docs/370`（LNotary，`origin/main`）
> 一句话: **#776 把 genesis 钉在种子、镜像源与镜像 IR 上；本文再把 `driver.lomt` /
> `lomelf.lomt` 的 `use` 闭包、发射环境与执行前的种子核对接上，并把根、配方、信任面
> 与预算收进一份 `trust.json`。**

---

## 0. 现状：链条已经有一条腿，缺的是闭包与执行前核对

`#776` 之后，`loment/build/genesis/provenance.json` 已经记录：

* genesis 的 sha256/size；
* 种子的 sha256/size；
* 镜像源 `loment/tools/lomelf.lomt` 的 sha256/size；
* `lomelf.lomt -> IR` 的哈希（由种子重算，与 clang 版本无关）；
* 生成它的 clang 版本与 target。

`loment_genesis.py --check` 做静态核对，`--verify` 完整重建到临时目录并比对。
这已经修掉了 `docs/167` §5.2 记的「genesis 只对得上自己的 SHA256SUMS」那条静默漂移。

仍然缺三件：

1. **闭包**：`provenance` 只记 `driver.lomt`？没有；它记的是**种子文件**和 `lomelf.lomt`
   文件。`driver.lomt` 的 `use` 依赖（`lexer` / `codegen` / `checker` / `comefor` /
   `potato` / `interp` / `switches` / `bytes`）与 `lomelf.lomt` 的闭包（`bytes` /
   `win_shim_data`）都没有各自的内容哈希。改了闭包里的一个文件、重生成种子，种子哈希会变；
   但如果种子没跟着重生成，`loment_seed --check` 会红。这一层是确定性的补充，不是重复。
2. **执行前核对种子**：`bootstrap.sh` 执行 genesis 前核对 `SHA256SUMS`（#437），但不核对
   种子。一个陈旧的 genesis 可以汇编一个已经漂移的种子，链条仍然收敛；漂移因此不响亮。
3. **一份总的 claim**：根、配方、信任面、预算散在 provenance、docs 与判据里；第三方要
   自己拼。`trust.json` 把它们收成一个可对账的对象。

本文把这三件补上。它不替换 #776 的 `--check/--verify`，只扩展 provenance 的格式（1 → 2）
并加一份上层清单。

---

## 1. 六条不可能性（先写死）

### A · 根不可消

任何重演链都要有一个起点。起点是一个程序加一次人类阅读；程序不能自己证明自己。
Loment 今天的根由两条入口的并集组成：已提交的 genesis（二进制入口），以及
`lomentc.py` + CPython + clang（参考入口）。`genesis` 是缓存，也是第二条入口的产物。
「无 clang 自举」的完整条件是「genesis 已在树里」。

### B · 同一配方不保证同一字节

genesis 由 clang 从种子构建。本机与 `provenance` 记录的都是 clang 22.1.8；CI 的工具链
可能不同。不同 LLVM 版本对同一份 IR 可以产出不同字节。`--verify` 在**同一 clang** 下要求
逐字节相同，跨版本改比行为（两个 genesis 对同一份探针 IR 给出相同产物）。跨环境的不同
字节是一个**新的根**，不是谎言。

### C · 定点不证明语义

`stage1` 编译 `driver.lomt` 得到种子，证明 `F(seed) = seed`。一个恶意 `F` 也可以有定点。
定点证明自洽，不证明正确。

### D · 双实现不独立

`lomentc.py` 与 `loment/selfhost/*.lomt` 由同一作者编写，自举侧从参考侧派生；
`loment_interp.py` 仍 `import lomentc`；`lomelf.py` 与 `lomelf.lomt` 是孪生链接器。
「两边一致」不是独立谱系的一致（`docs/160` §2.1、`docs/203` §7.3）。

### E · 随机差分不覆盖触发条件

后门可以在一个很窄的输入形状上触发。语法感知的生成与变异提高命中率，仍然不构成
「不存在」的证明。随机差分只给「在生成器分布下未发现」。

### F · 内容寻址不阻止构建期投毒

恶意编译器可以产出恶意二进制，并给它一个正确的哈希。哈希解决替换，不解决投毒。
签名把信任移到密钥，透明日志把信任移到见证者，重演把信任移到重演者。

---

## 2. 能合法拿掉的假设

| 假设 | 换成 | 代价 |
|---|---|---|
| 二进制是信任载体 | 配方 + 根是载体，二进制是缓存 | 需要 provenance + 闭包 + trust |
| 同一配方同一字节 | 同一配方 + 同一具名环境同一字节 | 跨环境是新根；`--verify` 分同版本/跨版本两档 |
| 一个实现正确 | N 条独立谱系在可观察行为上一致 | 需要一条不共享代码的谱系；今天没有 |
| 定点即正确 | 定点 + 独立判卷人 | 需要独立 oracle 与语料 |
| 哈希清单是真相 | 哈希清单 + 透明日志 + N 次重演 | 社会层，不是密码学 |
| 根可以很小 | 根被标价，逐步缩小 | 需要预算与棘轮 |

---

## 2.5 可读性不是必要条件，但解释链必须落地

> 2026-11-22 追问（ChatGPT 提案）：如果根可以被独立验证，为什么它必须可读？

可读性确实不是正确性的必要条件：一份人人读得懂的源码也可能藏后门；一粒没人读得完的
二进制也可能被独立解析、执行与比对。但「独立验证」不会让根消失，它把根搬到了
**规范 + 验证器**上。三种锚必选其一：

| 锚 | 人必须读什么 | 它能证明什么 | 它不能证明什么 |
|---|---|---|---|
| **可读根** | 根的实现 | 根的行为 | 根是否符合意图 |
| **规范 + 小验证器** | 规范 + 验证器 | 产物符合规范 | 规范本身是否对 |
| **状态 + 转换规则 + 不变量** | 规则 + 不变量 + 检查器 | 不变量在规则下保持 | 不变量是否足够 |

三行的共同点：总要有一个东西被有限地写下来，并且它的语义可以被独立复查。独立实现之间
的一致只证明它们同意，不证明它们对；验证器之间也会一起错。

`G_0 = (S_0, R, I)` 是个好形状，但它不消除根：`R` 可执行就回到程序；`R` 是形式关系就需要
证明检查器，检查器的语义是新的根；`I` 只覆盖写下来的性质，「没有后门」不是任意程序可
判定的不变量。它把「读一个程序」换成「读规则 + 不变量 + 检查器」，仍是一次人类阅读。

所以本节的结论是：**根必须有限可述、可独立检查、并被标价**。可读是「有限可述」的
最强形式，不是唯一形式。V0 的锚是「provenance + 闭包」；机器可验证的锚（独立 oracle）
仍是 C7，未落地。

---

## 3. 对象

### 3.1 `provenance.json`（format 2）

在 #776 的 format 1 上增加：

```json
{
  "format": 2,
  "source_closure": {
    "sha256": "...",
    "files": [{"path": "loment/selfhost/driver.lomt", "sha256": "...", "size": 0}]
  },
  "genesis_source_closure": {
    "sha256": "...",
    "files": [{"path": "loment/tools/lomelf.lomt", "sha256": "...", "size": 0}]
  },
  "seed_env": {"host": "windows", "python": "3.11.15", "implementation": "CPython"}
}
```

`source_closure` 是 `driver.lomt` 加它的 `use` 闭包；`genesis_source_closure` 是
`lomelf.lomt` 加它的 `use` 闭包。两份都按通用换行哈希，按 posix 路径排序。

`genesis` 仍是固定路径 `lomelf-linux-x64.elf`；没有改名。内容寻址由 `genesis.sha256` 与
`provenance` 记录承担，文件名保持稳定，`bootstrap.sh` 与 #776 的判据都不用动。

### 3.2 `seed.sha256`

`loment/build/genesis/seed.sha256` 是 `provenance.inputs.seed` 的 sh-friendly 投影：

```
7d75a74f...  loment/build/selfhost_driver.ll
```

`bootstrap.sh` 用 `tr -d '\r' | sha256sum` 核它，执行 genesis 前就能让种子漂移变响亮。
记录缺失时只告警；权威判据是 `loment_genesis --check` 的 provenance/闭包核对。

### 3.3 `trust.json`

```json
{
  "schema": 1,
  "root": {
    "seed": {"path": "loment/build/selfhost_driver.ll", "sha256": "...", "size": 7468507},
    "genesis": {"name": "lomelf-linux-x64.elf", "sha256": "...", "size": 312328},
    "mirror_ir": {"sha256": "...", "size": 1679955},
    "toolchain": {"clang": "clang version 22.1.8 ...", "target": "x86_64-unknown-linux-gnu"},
    "provenance_format": 2
  },
  "recipe": {
    "source_closure": {"sha256": "...", "files": []},
    "genesis_source_closure": {"sha256": "...", "files": []},
    "reference_closure": {"sha256": "...", "files": []},
    "toolchain": {},
    "seed_env": {},
    "commands": [
      "python tools/loment_seed.py --emit",
      "python tools/loment_genesis.py --emit",
      "python tools/loment_genesis.py --verify",
      "sh loment/bootstrap.sh"
    ]
  },
  "trust_surface": {
    "anchor": "provenance+closures",
    "spec": "docs/376-replay-trust.md",
    "spec_sha256": "...",
    "verifier": ["tools/loment_genesis.py", "tools/loment_trust.py"],
    "assumptions": ["..."]
  },
  "diversity": {"lineages": [], "independent_lineages": 0},
  "differential": {"corpus_sha256": null, "cases": 0, "divergences": 0},
  "budget": {
    "seed_bytes": 7468507,
    "seed_input_cap": 8388608,
    "reference_lines": 18929,
    "spec_lines": 339,
    "verifier_lines": 883,
    "assumption_count": 5,
    "oracle_lines": null,
    "replay_minutes": null,
    "independent_lineages": 0
  }
}
```

`reference_closure` 是 `lomentc.py` 加它的局部 import 闭包加 `loment_seed.py`。
`trust_surface.spec_sha256` 让「规范改了而清单没跟」变成一条红。

---

## 4. 现状对账（`origin/main` `b6bfb02f`，2026-11-22）

| 项 | 今天 | 信任含义 |
|---|---|---|
| 种子 | 7,468,507 B（sha256 `7d75a74f…`） | 不可人读；`IN_CAP` 8,388,608 B，余量 920,101 B |
| genesis | 312,328 B（sha256 `059b342b…`） | 由 provenance 钉在种子/镜像/IR/clang 上 |
| 源闭包 | 12 文件 | `driver.lomt` 的 `use` 闭包已内容寻址 |
| genesis 源闭包 | 5 文件 | `lomelf.lomt` 的 `use` 闭包已内容寻址 |
| 参考闭包 | 14 文件 | `lomentc.py` 18,929 行 + 局部 import |
| 发布 | `release-manifest.json` 1,109 条，`0.1.5.1` | 工件内容寻址；缺构建输入 |
| 孪生 | 参考 vs 自举，语料 IR 逐字节相同 | 一致，不独立 |
| CI | 工具链可能不同 | `--verify` 同版本逐字节、跨版本比行为 |

一句对账：**Loment 已经有「内容寻址的工件」与 #776 的 genesis 记录，缺的是「闭包 + 执行前
种子核对 + 一份总 claim」；V0 补的就是这三件。**

---

## 5. 三个操作

`root()` —— 只读，算根、闭包与预算，不构建。

`replay` / `verify` —— `loment_genesis.py --verify` 在具名环境重建 genesis；同 clang 逐字节比，
跨 clang 比行为。`--mirror-ir` 单独重算 `lomelf.lomt -> IR` 的哈希。

`judge(corpus)` —— 参考实现与独立 oracle 各跑一遍，比可观察行为。V0 未接。

---

## 6. 判据（每条先红）

| # | 判据 | 证伪方式 |
|---|---|---|
| **C1** | `provenance` 的 `source_closure` / `genesis_source_closure` 与盘上闭包一致 | 改一个依赖仍绿 ⇒ 闭包是假的 |
| **C2** | `seed.sha256` 等于当前种子的规范化哈希 | 改种子仍绿 ⇒ 绑定是假的 |
| **C3** | 改种子一个字节，`loment_genesis --check` 红 | 只比 genesis 自己的 SUM ⇒ 漂移不响 |
| **C4** | 改 `driver.lomt` / `lomelf.lomt` / 闭包文件，`--check` 红 | 只比入口文件 ⇒ 闭包漏了 |
| **C5** | `--verify` 同 clang 逐字节、跨 clang 行为一致 | 复现不出仍报绿 ⇒ 重演是装饰 |
| **C6** | 种子漂移时 `bootstrap.sh` 在执行 genesis 前失败 | 用旧 genesis 静默收敛 ⇒ 漂移不响 |
| **C7** | 独立 oracle 的静态 import 检查：不许 `import lomentc` | 共享前端仍算独立 ⇒ 判卷人被污染 |
| **C8** | 差分语料哈希进 `trust.json`；零新分歧是棘轮 | 语料变了而哈希没变 ⇒ 结果不可复现 |
| **C9** | 语料包含自指输入：种子、`driver.lomt`、`lomelf.lomt` 及变异 | 只测小程序 ⇒ Thompson 后门照不到 |
| **C10** | 预算只许朝安全方向变：种子/参考/验证器/假设只降；独立谱系只升 | 数字涨了没有同提交更新 `docs/376` ⇒ 棘轮失效 |
| **C11** | 第三方在干净机器上只用 `trust.json` + root + recipe 复现哈希 | 仍要读 `tools/` ⇒ 重演没有离开开发者 |
| **C12** | `trust.json` 进发布清单并被签名；缺一条 ⇒ 红 | 断言与签名脱节 ⇒ 透明层是装饰 |

C1–C6、C8、C10 的 V0 形态落在 `tools/loment_trust_test.py`（9 条）；C7/C9/C11/C12 未接。

---

## 7. 我不主张

1. **不主张把根缩到 hex0。** 今天做不到：种子 7.47 MB、参考实现 18,929 行。
2. **不主张跨 clang 版本逐字节可复现。** `--verify` 同版本比字节、跨版本比行为。
3. **不主张重演能证明语义正确。** 它证明「这份产物来自这粒种子与这份配方」。
4. **不主张现有双实现是独立谱系。** 它们是同一作者、同一谱系。
5. **不主张随机差分能证明后门不存在。** 它只能给「在生成器分布下未发现」。
6. **不主张签名/透明日志解决维护者被收买（xz）。** 那是社会层。
7. **不主张内容寻址的文件名。** genesis 保持固定路径；内容寻址由 provenance 的 sha256 承担。
8. **不主张性能。** 重演成本、oracle 成本、语料体积都未测。

---

## 8. 与 358 / LNotary 的关系

| 轴 | `docs/358` + LNotary | 本文 |
|---|---|---|
| 时间 | 构建之后，静态产物 | 构建当时，从根重演 |
| 对象 | 产物 + 封印 + 小验证器 | 根 + 配方 + 闭包 + 重演 |
| 攻击者 | 恶意产物 | 恶意编译器 |
| 信任落点 | 小验证器 | N 条独立谱系与 N 次重演 |
| 输出 | 接受/拒绝 + 理由 | `trust.json` + `--verify` 判决 |

`docs/358` 把编译器移出运行期信任边界；本文把编译器移出构建期信任边界。LNotary 是只读
公证人，零 import；它可以把 `trust.json` 的哈希写进旁挂。

---

## 9. V0 落地（2026-11-22）

已落地：

* `tools/loment_genesis.py`：`provenance.json` 升到 format 2，增加 `source_closure` /
  `genesis_source_closure` / `seed_env`；`--emit` 同时写 `seed.sha256`；`--check` 核闭包
  与种子；`--verify` 保留 #776 的同版本逐字节 / 跨版本行为比对。
* `loment/bootstrap.sh`：执行 genesis 前用 `tr -d '\r' | sha256sum` 核 `seed.sha256`；
  漂移时 fail closed。`LOMENT_ALLOW_DRIFT=1` 只留给实验；记录缺失时只告警。
* `tools/loment_trust.py` + `loment/build/trust.json`：root / 三份闭包 / toolchain /
  seed_env / trust_surface / budget；`--emit` / `--check`。
* `tools/loment_trust_test.py`：9 条判据（C1–C6、C8、C10 的 V0 形态 + 确定性 + 发布口径）。
* 登记：`tools/ci.py` 的 `STATIC_CHECKS`、`.github/gate-floors.txt`（`loment_trust_test 9`）、
  `tools/loment_release.py` 与 `loment/tools/lomrel.lomt`。

未落地：

* **C5 跨 clang 版本**：本机 `--verify` 同 clang 逐字节通过；CI 的 LLVM 19 未重演。
* **C7 独立 oracle**：`tools/loment_interp.py` 仍 `import lomentc`。
* **C8/C9 随机差分与自指语料**：`differential.cases=0`。
* **C11 第三方无 `tools/` 重演**、**C12 透明日志**：未接。

验证（本机，2026-11-22）：

* `loment_genesis_test` **9/9**：format 2 闭包、镜像 IR 复现、同 clang 逐字节重建（`059b342b…`）、
  PATH 无 clang 的完整自举、执行前 genesis 校验。
* `loment_trust_test` **9/9**；`loment_trust --check`、`loment_genesis --check` 均为 0。
* `loment_tools_test` 的 shard-cost 两条已补齐（`loment_trust_test` 实测 41.0s）。
* `loment_seed_test` 未在干净基线重跑：它的 bootstrap 与 genesis 套件走同一条链。
* `loment_elf_test` / `loment_pe_test` 不必重跑：genesis 字节与上游逐字节相同。

本 PR 新增了 `trust.json` / `seed.sha256` / `docs/376`；`release-manifest.json` 归 main 侧
emit-main（`docs/254`，`origin/main`），PR 不带整树台账。

---

## 10. 落地顺序

1. **Commit 1（先红）**：`loment_trust_test.py` 的 C1–C6/C8/C10 判据；登记 ci/floors。
2. **Commit 2（修）**：`loment_genesis.py` 的 provenance format 2 + `seed.sha256`；
   `bootstrap.sh` 的种子核对。
3. **Commit 3（清单）**：`loment_trust.py` + `trust.json`；发布口径。
4. 后置：独立 oracle、随机差分、透明日志、CI 跨 clang 重演。

---

## 11. 前史

| 本文用到的 | 谁先做过 | 本文差在哪 |
|---|---|---|
| 信任链的 quine 不可能性 | Thompson 1984 | 落到 Loment 的 genesis/种子/参考实现三件套上 |
| 多样双编译（DDC） | Wheeler 2005 | 要求「不共享代码」的谱系；Loment 今天没有 |
| 从极小种子重建工具链 | Guix full-source bootstrap、live-bootstrap | 它们到 hex0 级；Loment 的种子仍是 MB 级 |
| 同源同环境同字节 | Reproducible Builds | #776 的 `--verify` 已分同版本/跨版本两档 |
| 构建来源证明 | SLSA provenance、in-toto | 它们证明「谁构建的」，本文证明「从哪粒种子、按哪份配方构建的」 |
| 产物携带可核声明 | PCC、`docs/358` | 它们管运行期；本文管构建期 |
| 产物公证 | LNotary（`docs/370`） | 它只读产物；本文给它一个可核的根 |
