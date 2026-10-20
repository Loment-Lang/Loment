# 原生面的独立 oracle，与一个可证伪的自举根（#772 / #776）

**状态**：已落地（2026-10-19）。判据 `loment_spec_oracle_test`（3 条）与
`loment_genesis_test`（5 → 8 条）；根记录 `loment/build/genesis/provenance.json`。

---

## 1. #772：孪生判据证明不了"对"

### 缺口

仓里判"自举侧对不对"的判据，期望值都来自参考实现（`tools/lomentc.py`）：

* `loment_p8_test::test_m83_m84_self_compile_and_fixed_point`：
  `stage1(driver.lomt) == emit_llvm(driver.lomt)`，然后定点；
* `loment_store_twin_test`：`lompi/store/**` 每一份，两边 IR 逐字节相同。

两条都只证明**两个实现一致**。#694 是活证据：`253402300800 as i64` 在两个实现里
**一起**按 32 位截断，所有孪生判据全绿。发现它的是手写的期望值，不是仓里任何一条判据。

### 落地的形状：一张从规范长出来的可执行表

`tools/loment_spec_oracle_test.py` 里是一张 21 格的用例表。每格只有一份数据：

```python
Case("literal-widen-u64", "u64", C("u64", L(253402300800)))
```

同一棵表达式树长出两样东西（**同源，不会手改漂**）：

1. `_render()` 生成喂给两个编译器的 Loment 源码；
2. `_model()` 按规范算期望值。

`_model()` 是**第三条路径**：不调 `emit_llvm`、不调 `loment_interp`、不调任何编译器
代码。它实现的是 `docs/144` §3 的整数语义 —— 溢出按模回绕、按声明符号性选
`sdiv`/`udiv`、`ashr`/`lshr`、`icmp slt/ult` —— 以及 #694 定下的 `as` 规则
（字面量加宽保值、窄目标回绕）。每条用例的 `cite` 指向这份规范。

判据跑**真程序**：参考实现与自举侧各编一个本机可执行文件（Windows PE / Linux ELF，
**不需要 clang 或 WSL**），程序把每格结果按 8 字节写到 stdout。判定的问题是
`ref == spec and mirror == spec`，不是 `ref == mirror`。

### 它真的会红

`test_agreement_alone_is_not_accepted` 把 #694 的历史错值（`0xFFF44180`）同时当作
"参考的答案"和"自举侧的答案"喂给判定：两侧一致、规范不同意 —— 判定必须红，并且必须
点名 `literal-widen-u64`。这条判据就是 oracle 没有退化成孪生判据的证明。

`test_the_table_covers_the_shared_error_classes` 是一张**只许变长**的覆盖面账：
13 个类别（回绕、有/无符号除法与余数、算术/逻辑右移、`as` 加宽/变窄、有/无符号比较）
必须都在表里，出处不能空。

### 边界

* 它只覆盖**表达式语义**这一层；结构体布局、能力域、GC 仍只有孪生判据。
* 它比的是**行为**，不是 IR 字节 —— IR 逐字节仍是 `loment_store_twin_test` 的事。
* 默认字面量宽度不在规范里（`docs/143`：字面量由上下文定宽），所以比较类用例
  **先把两边落到声明类型上**，不拿未定宽的默认值考符号性。

---

## 2. #776：根只能被信任，不能被证伪

### 缺口

`loment/build/genesis/lomelf-linux-x64.elf` 是自举链的第一个可执行文件。
旧的 `--check` 比的是它和**同目录**的 `SHA256SUMS` —— 而那份 SUM 是 `--emit` 一起写的。
改了 `loment/tools/lomelf.lomt` 却忘了 `--emit` 时，二进制与哈希一起没动，判据照绿
（`docs/192` §3 记着这个没有兜底的缺口）。本次动笔时它就**正在发生**：genesis 上一次
重建是 `9ad7c6c8`（"按 6 MiB 布局重建"），之后镜像源在 `e675c00f`（LibC close-out）
又改了 —— 提交的 genesis 已经是一个**陈旧的汇编器**。

### 落地的机制：记录 + 两条可证伪的检查

`--emit` 现在同时写 `loment/build/genesis/provenance.json`，钉住：

* genesis 的 sha256 / size / 名字；
* 种子的 sha256 / size；
* 镜像源 `loment/tools/lomelf.lomt` 的 sha256 / size；
* **`lomelf.lomt -> IR` 那一步的 IR 哈希** —— 这一步走的是**种子**编出来的 stage1，
  与 clang 版本无关，所以"源改了、产物没跟上"是一条**确定**的红（`docs/192` §3 点 3
  建议的形状）；
* 生成它的 clang 版本行与 target。

三条检查，按代价分层：

| 命令 | 需要 | 抓什么 |
|---|---|---|
| `--check` | 无 | 记录里的每个输入都要等于盘上的文件（改了源忘了重建 ⇒ 红） |
| `--mirror-ir` | clang + 能跑 Linux ELF | 用种子重算镜像 IR，哈希必须等于记录 |
| `--verify` | 同上 | **完整重建**到临时目录，与提交件比 |

`--verify` 的比对规则写明了信任边界：

* 本机 clang 与记录**相同** ⇒ 要求**逐字节相同**。这是 #776 要的
  "独立实现的汇编器、一个答案"的形状 —— 重建走的是 clang（第三方工具链），不是
  `lomelf` 的孪生。
* clang 版本**不同** ⇒ 逐字节比是假红机（clang 一升级全红，`docs/192` §3），改比**行为**：
  两个 genesis 对同一份固定的小 `.ll` 探针给出**逐字节相同**的产物。汇编是确定性的，
  所以这问的仍是"是不是同一个汇编器"；但它**不**证明
  "这个 clang 生成的字节就是提交的字节" —— 那一层只在同版本工具链上由逐字节比给出。
  打印这条边界，不假装做到了字节级证明。

### 还剩下的信任

仓库内的哈希不能证明"这个 clang 没骗你"。记录让**替换可检测**（改了输入没重生成 ⇒ 红；
在钉住的工具链上重建 ⇒ 逐字节比），但根仍然是**一个二进制**。这是 #776 列的第三条路
（接受并写清），不是被当成证明的东西。

### 同批修掉的两处实测缺陷

* genesis 在 git 里是 **mode 100644**，Linux 检出上 `bootstrap.sh` 的 `[ -x ]` 为假，
  静默退回 clang —— 根在那里根本不是根。改为 **100755**；`test_bootstrap_needs_no_clang`
  的断言从"输出里有 genesis 这个词"收紧成"**起点: genesis**"（退回 clang 时那句
  "没有 genesis, 退回老路子"里也有这个词，旧断言是假绿）。
* `loment_genesis._wsl()` 先查 `shutil.which("wsl")`，于是在 Linux 上 `--emit` 拒绝运行，
  尽管 ELF 本来就能原生跑。改为按平台判断（Linux 原生 / Windows 经 WSL），
  输出路径也按平台转换。

---

## 3. 判据账

| 套件 | 之前 | 之后 |
|---|---|---|
| `loment_spec_oracle_test` | —（不存在） | 3 |
| `loment_genesis_test` | 5 | 8 |

`loment_spec_oracle_test` 进了三处登记：`ci.py:STATIC_CHECKS`、发布清单
`loment_release.GLOBS`、以及它的自举孪生 `loment/tools/lomrel.lomt`（同一位置）。
跳过表没有新增条目。

## 4. 复现

```sh
python tools/loment_spec_oracle_test.py          # #772
python tools/loment_genesis.py --check           # #776 静态
python tools/loment_genesis.py --mirror-ir       # #776 漂移探测
python tools/loment_genesis.py --verify          # #776 完整重建
python tools/loment_genesis_test.py
```
