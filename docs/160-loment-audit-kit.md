# 160 · Loment 0.1.4 Alpha 审计包（M100）

> 这份文件的读者是**第三方复核者**（以及几个月后忘掉细节的我自己）。
> 一条命令跑完全部判据：`python tools/loment_audit.py --json`
> —— 它会打印/落盘 `loment/build/audit-report.json`（含提交、tag、clang 版本、
> **24 条**主张的逐条结果，以及**不主张清单**）。

## 0. 审计包的设计原则

审计工具**只调用已有门禁**，不新写判据。原因：审计工具自己长成第二套判据，是这类
项目最典型的坑 —— 两边一起漂，红绿都失去意义。`tools/loment_tools_test.py` 里有一条
一致性门禁（`test_audit_claims_match_ci`）专门盯这件事：审计工具列的每个工具都必须
出现在 `tools/ci.py` 的静态门禁表里。

## 1. 主张清单（**24 条**，各自有可执行判据）

> ⚠ **别在这一列复述分母**（2026-09-22 第三方复核的发现）：以前这里写着每条主张的
> "通过标准 = N/N"，而**20 行里有 16 行那个 N 早就是过期的**（工具加过用例，文档没跟）。
> 复述一个会变的数就是制造**第二份真相**。所以现在的口径是：**分母以工具自己打印的
> 尾行为准**，本节只写**不变量**（哪几条断言必须成立）。一条判据钉住条数本身：
> `loment_tools_test::test_audit_claims_match_ci` 现在要求 `len(CLAIMS)` **等于**
> `docs/160` §1 表格的行数（原先只钉 `>= 10` 的下限 —— 于是 C21–C24 加进工具时
> 表格没跟，四个数（18/20/23/24）各说各话）。

| # | 主张 | 判据（`python tools/…`） | 不变量（**全绿以工具尾行为准**） |
|---|---|---|---|
| C1 | 自举 checker 与参考实现**规则等价** | `loment_rule_parity.py` | 假阳性 / 口径漂移 / 探针失效 **必须为 0**；棘轮预算只升不降 |
| C2 | 两个后端**逐字节等价** + 三阶段自举定点 + 语料零诊断 + 负例被拒 | `loment_p8_test.py` | stage1=stage2=stage3；负例全部非零退出且无信号；语料 IR 逐字节相同 |
| C3 | 参考实现自身形状 | `lomentc_test.py` | 全绿 |
| C4 | 工具侧（诊断分类完整、内建表一致、增量缓存、**审计清单一致**、DWARF 变量信息与 `--debug` IR 可编译） | `loment_tools_test.py` | 全绿 |
| C5 | **无 Python 自举**：种子自复现、启动脚本无解释器、定点 | `loment_seed_test.py` | 全绿（含 `sh loment/bootstrap.sh` 只用 clang 跑通） |
| C6 | Loment 版格式化器与 Python 版**逐字节相同** | `loment_fmt_test.py` | 全绿（语料 + 边界 + 幂等） |
| C7 | 示例集与交叉编译目标 | `loment_p9_test.py` | 全绿（示例编译；aarch64 目标发射并可反汇编） |
| C8 | 编辑器宿主与 LSP 往返（无头） | `vscode_ext_test.py` | 全绿 |
| C9 | 发布清单**可复现**（两个独立实现给出同一份） | `loment_rel_test.py` | stdout / 落盘字节 / 退出码逐字节相同。**量尺 2026-10-16 换过**（原为 `loment_release.py --check` = "入库台账 == 当前树"）；台账与**树**的一致性改由 `lom_audit` 的 M95 按**滞后容差**判 —— `docs/254` |
| C10 | 账本不是手改的 | `loment_status.py --check` | 状态矩阵与 `docs/145` 一致 |
| C11 | **LSP 去 Python**（Loment 版语言服务：补全/跳转/诊断/`--check`） | `loment_lsp_test.py` | 全绿（真二进制往返 + 码/行号 + `--check`） |
| C12 | 工具链等价（格式化器/文档生成器与 Python 版逐字节相同） | `loment_doc_test.py` | 全绿 |
| C13 | JSON 库与 Python `json` 逐字节一致 | `loment_json_test.py` | 全绿（2026-09-22 修掉一条**恒真式**断言，见 §2 第 12 条） |
| C14 | Windows 文件类型注册 + 启动脚本无解释器 | `loment_filetype_test.py` | 全绿（本机那条 VS Code 路径**曾抛 OSError**，2026-09-22 已修，见 §2 第 12 条） |
| C15 | 发行包：命令安装 (sh/ps1) 与自解压安装包，装出来的编译器产物与参考逐字节相同 | `loment_dist_test.py` | 全绿（干净检出上部分用例**按设计跳过** —— 跳过数见工具输出，见 §2 第 13 条） |
| C16 | 发行包签名：Authenticode (发布者可读/篡改可验) + SHA256SUMS 分离签名 | `loment_sign_test.py` | 同上 |
| C17 | **包管理器去 Python**：Loment 版 `lompkg` 与 Python 版 stdout **逐字节相同** | `loment_pkg_test.py` | 全绿（链/嵌套/空包 + 错误场景 + 双向锁往返） |
| C18 | **L0 生成器去 Python**：Loment 版 `lomc` 的四个后端与 Python 版**逐字节相同** | `loment_lomc_test.py` | 全绿（发射 + 落盘字节 + `--check` 对账/漂移 + 错误码） |
| C19 | **原生 ELF 后端**：`lomelf` 把 IR 直接编成 x86-64 ELF（**不经 clang**），与 clang 链产物**行为逐值一致** | `loment_elf_test.py` | 全绿（stdout 字节 + 退出码；不支持项**报错**而非静默；种子→stage1→产物全程无 clang） |
| C20 | **自举侧镜像**：`loment/tools/lomelf.lomt`（走种子自举链编成二进制）产出的 ELF 与参考**逐字节相同** | `loment_elf_test.py` | 语料逐字节相同 |
| C21 | **链条起点**（`genesis`）：提交进仓库的 lomelf 二进制起头，在**没有 clang** 的环境里跑通 `bootstrap.sh` 的四条证明 | `loment_genesis_test.py` | 全绿（重建工具链既不需要 C 编译器，也不需要解释器） |
| C22 | **构建路径去 Python 第一格**：Loment 版里程碑状态矩阵生成器 | `loment_status_test.py` | stdout / stderr / 落盘字节 / 退出码与 Python 版**逐字节相同**（含 `--check` 漂移检出） |
| C23 | **构建路径去 Python 第二格**：Loment 版发布清单生成器（glob 展开 + sha256 + 定形 JSON） | `loment_rel_test.py` | stdout / 落盘字节 / 退出码与 Python 版**逐字节相同** |
| C24 | **原生 PE 后端**：同一份 IR 产出的 Windows PE 控制台程序**在本机原生跑**，与 clang/Linux 路一致 | `loment_pe_test.py` | stdout 字节 + 退出码一致；编译与运行**都不碰 clang/wsl** |

> **C21–C24 是 2026-09-14 之后加进工具的**（`608728b` / `c27c2c8` / `f440c78` / `db90a16`），
> 而 `docs/160` 的表格一直停在 C20 —— 恰好漏掉"去 clang / 构建去 Python / PE"这半条叙事。
> **第三方照这份文档复核会整段跳过它们**，所以 2026-09-22 补上，并加了那条钉住条数的判据。

一键跑（本机实测 **20+ 分钟** —— C19/C20/C21/C24 要现编 clang 与自举链；文档原先写"约 4 分钟"，与实测差一个量级）：

```bash
python tools/loment_audit.py --json     # 24/24 通过（本机 C14 是环境红, 见 §2） + loment/build/audit-report.json
python tools/loment_audit.py --list     # 只列主张与命令
```

## 2. 明确**不**主张（复核者请按"未验证"处理）

1. **冻结面内的判据都是自证的**：两个实现由同一作者编写，"两边一致"不等于"语义正确"。
   本审计包**不构成**外部确认 —— 外部审计要补的正是这一层。
2. 三处**刻意保守偏离**：类型算不出来时（下标/`&`、`match` 主体、`?`），自举 checker
   可能**少报**诊断（方向保守，不假阳性）。见 `docs/158 §4`。
3. **同名 `let` 双 alloca**：同一函数里重复声明同名变量时两个后端语义不同
   （参考复用槽位，自举发两条）。这是唯一一处**已知的语义不一致**。
4. **自举驱动整条链没有 parser**：解析期错误（如 `const C: bool = true;`）只有参考实现
   能报出来。parser 本身已与参考逐字符一致（43/43 语料），但没有接进 driver 的链路。
5. **aarch64 只验证到发射**：没有 qemu-user、没有真机执行（`docs/158 §4`）。
6. 自举性能 12.8s（参考 1.2s）；DWARF 有**行表 + 变量名/声明行**，但**位置求值**要完整调试器：
   `llvm-objdump --debug-vars` 在 freestanding 目标上只显示 `<unknown op DW_OP_fbreg>`，
   clang 自身产物同样如此（对照见 `docs/145` P6 的 M59 后置修订）。
7. **工具链与 L0 生成器都已无 Python 成分**：`lomfmt`/`lomdoc`/`loment_lsp`/`lompkg` 与
   **L0 生成器 `lomc`** 都有 Loment 实现（进度见 `docs/159 §4b`），判据都是"与 Python 版
   逐字节相同"。L0 这一格的特别之处：它是**跨线契约面**（12 个生成物被内核线消费），所以
   做法是不改 L0、不改生成物，只把"输出逐字节相同"钉成门禁 —— 下游一行不用动。
   **不主张**：本版不替 `--emit-*` 建父目录（Python 会 `mkdir -p`），也不复刻词法层的
   非法字符/未闭合注释报错（沿用自举 lexer 的跳过行为）；语料里没有这类输入。
8. **自举 checker 在大单元里的一条走岔（开放，形状待定位）**：`lomc.lomt` 里把一个局部
   命名为 `fn` 会让 checker 报一串 E002 并退出 1（改名即通过）；**但最小单元同样写法逐字节
   一致、零诊断** —— 所以这不是"关键字当标识符被误拒"（我最初这么记过，已更正，见
   `docs/150` M81 后置修订第 2 条的更正框）。触发它的更可能是某个**规模相关**的表/游标。
   复现与账见 `docs/150`。
9. **自举 codegen 的 `as` 取型缺口（开放）**：`(48 + (x % 10) as u32) as u8` 这种
   "运算符右侧是带 `as` 的括号表达式" 会让自举 codegen 发射**非法 IR**
   （`trunc i64 -> i8`，clang 拒）。参考实现正确。移植 `lomc` 时撞到，本次在源码侧
   绕开（显式中间变量），**codegen 侧的缺口仍开放**；最小重现与根因见 `docs/150` 的 M82 后置修订。
10. Mimosa 扫描器多次未能给出完整结论（`scanner_enobufs`）—— 因此**不宣称项目安全**。
11. **本清单比别处的开放项清单短**（2026-09-22 第三方复核发现）：`docs/158` §4 列 **12 条**
    （本页成文时是 10 条，且 §5 那行还写着"7 条"，两个数都过期了），`docs/167` §5.3 另记一条
    —— 至少有 **5 条**已知开放项当时没进这一页，于是第三方会**以为它们不在复核范围里**。
    补记（编号照 `docs/158` §4）：**括号里 `ptr` 表达式再 `as` 整数**时自举 codegen 发
    `zext i32`（截成 32 位，运行期用错地址）· FFI 外部目标文件 **4 MiB 上限** ·
    `R_X86_64_PC64` 符号扩展两实现不一致 · **混宽整数比较**（`u32 == u8`）参考实现发非法 IR、
    自举侧截窄再比（语料从没有这种写法，所以是"**两边一致地错**"）· **关键字可当标识符**
    两个实现处理不同。第五条出自 `docs/167` §5.3：**自举镜像 `lomelf.lomt` 不支持按值传聚合**
    —— 发行包里 `loment build/run` 编不出任何按值传 struct/enum 的程序（enum-by-value 直接
    SIGSEGV），而 C24 的两个语料**恰好不按值传聚合**，所以 9/9 全绿照不到它。
12. **两条判据的"证据强度"曾弱于表面**（2026-09-22 第三方复核发现，**同日已修**）：
    * **C13**：`loment_json_test.py` 那条主用例原先写着 `json.loads(p) and 1` —— 对任何非空
      JSON **恒等于 1**，所以它只断言"根非空"，不校验根节点类型（docstring 却写着"根节点
      类型 + 关键字段取值"）。**已修**：改用**同文件 380 行早就有**的 `_kind_of`（与
      `json.lomt` 的 kind 同表）。修完 `loment_json_test` 仍 **4/4 绿** ⇒ 库本身没问题，
      坏的是那条断言 —— 但结论要说清：**修之前，这条通过不构成"JSON 库与 Python 一致"的
      证据**（那时真正的证据是同文件的 `test_json_decoding_and_lookup` 与
      `test_json_check_matches_loment_twin`）。
    * **C14**：`loment_filetype.py` 在本机某条 VS Code 路径上 `is_file()` 会**抛 OSError
      （WinError 649, 装入点）而不是返回 `False`**，判据因此是**崩**，不是 SKIP ——
      与本仓别的判据（缺 clang/WSL 时 `print SKIP`）做法不一致。**已修**：新增
      `_plain_file()`（`OSError` → "不是文件"）。本机现在正确地把那条路径判为**不可用**并
      输出一条 SKIP；剩下的 3/4 里那一条红是**本机已注册过文件类型**（环境，不是回归）。
13. **`N/N 通过` 这句话同时容纳"真跑了"和"跳过了"**（同上）：所有 `*_test.py` 的 `main()`
    把"没抛异常"记成通过，而决定跳过的那条只 `print("SKIP…")` 然后 `return` ——
    **跳过照样进分子**。审计工具抓的就是那句尾行，所以**报告里看不出跳了几条**。
    本节 §4 那句"SKIP 不算通过"只对**读报告的人**成立，工具没有兑现它。
    ⇒ 复核者若关心 C15/C16 这类"干净检出上大部分用例按设计跳过"的主张，**要自己看工具完整
    输出**，不能只看尾行。
14. **C2 里有一条受负载影响的墙钟预算**（同上，实测）：`loment_p8_test` 的
    `test_m86_selfhost_perf_budget`（自编译护栏 60s）在**三路并行**跑时是 **67.7s → 假红**
    （整条报 `19/20`），**单独跑 45.6s → 20/20**。所以它是**并发噪声**，不是回归 ——
    **门禁的"新红一律单跑复验再归因"正是为这一类准备的**（`.github/workflows/gate.yml`
    与 `CONTRIBUTING.md`）。复核者按 §3 的口径把这一条记成"保留意见"而不是驳回，是对的。

## 3. 第三方复核步骤（30–60 分钟）

```bash
git clone -b Fujoos-FujoLang-DEV <repo> && cd FujoOS
git checkout <审计报告里的 commit>   # 报告 provenance.commit —— 判据要对的**就是它**
python tools/loment_eol.py --fix    # 第 0 步: 把检出行尾拉回 LF (见 docs/161)
python tools/loment_audit.py --json # 期望 24/24
```

> tag `v0.1.3.4-alpha`（annotated）是 **M96 冻结面快照**，早于当前审计状态：
> 在那个提交上 `tools/loment_eol.py` 还不存在、主张也只有 10 条。要复核**冻结面**就 checkout
> 这个 tag（那里看 docs/158 与 `--emit-llvm` 的逐字节判据）；要复核**当前主张**就用报告里的
> commit。两者别混。

> 第 0 步不是可选的礼貌：Windows 上（`core.autocrlf=true`）检出的换行取决于**怎么检出的**
> —— 先 checkout 默认分支再切分支，会留下十几个 CRLF 文件，而按原始字节读源码的判据会把
> 它们报成"逻辑红"。一条命令就能归零，`--fix` 不会吞内容改动。

1. **工件哈希**：把审计报告里的 `provenance.commit` 与本地 `git rev-parse HEAD` 对齐；
   `python tools/loment_release.py --check` 应报 N/N 一致。
2. **抽样手工核**（不依赖我的脚本）：
   - 挑一个负例 `loment/selfhost/neg/*.lomt`，用参考实现（`tools/lomentc.py`）与自举
     驱动各判一次，看**都拒**且错误码集合一致；
   - 挑一个示例，比较 `tools/lomentc.py --emit-llvm` 与自举驱动的输出是否**逐字节相同**
     （`tools/loment_ir_diff.py` 有现成的差分工具）；
   - 看 `docs/158` 的冻结面，挑一条规则，自己写一个最小反例，看两边是否同判。
3. **对抗性尝试**（这些是"门禁有没有牙"的检查，不是破坏）：
   - 把 `tools/loment_rule_parity.py` 的 `BUDGET` **调高**过头 → 门禁**必须**红。
     ⚠ **方向别写反**（2026-09-22 第三方复核实测更正）：门禁那一行是 `eq >= BUDGET`，
     所以**调低只会更容易过、根本不会红**；而且全仓**没有判据**拦"有人把 BUDGET 调低"
     —— 这条棘轮是**只写在文档里的约定**，不是判据。要它咬人得把 BUDGET 调高。
   - 改种子 `loment/build/selfhost_driver.ll` 一个字节 → `loment_seed --check` **必须**红；
   - 删/改一个发布工件 → `loment_release --check` **必须**红；
   - 在 `loment/bootstrap.sh` 里加一句 Python 便利检查 → `loment_seed --script-ok` **必须**红；
   - 把任一 pinned 文件改成 CRLF（内容不动）→ `loment_eol` **必须**红。
     ⚠ **更正**（同上，2026-09-22 实测）：后半个断言"而 `git status` **仍然报干净**"
     **在本机不成立** —— `git status --porcelain` 会报 ` M`，`git update-index --refresh`
     回 `needs update`。成立的只是"`git diff` 的内容为空"（那本来就是"内容对 git 不可见"
     的意思）。`docs/161` 的现场证据要按这个口径读。
4. **报告**：审计结论请连同 `audit-report.json`（含日期、环境、24 条结果）一起存证；
   有红项时报告里会直接列出主张编号与工具的输出尾行。

## 4. 复核环境

- Windows + WSL（有 clang）或 Linux/macOS 原生；Python ≥ 3.10；`clang` 需要在 PATH 上
  或装在 `C:\Program Files\LLVM\bin`（工具里有回退路径）。
- 需要执行 Linux ELF 的判据（自举定点、驱动跑语料、格式化器）在 Windows 上走 WSL；
  没有 WSL 时这些条目会打印 `SKIP` —— **SKIP 不算通过**，报告里会显示该条不红但也没有
  证据，复核者应把它记为"未验证"。

## 5. 关联材料

- `docs/158-loment-freeze.md` —— 冻结面（改什么要付什么代价）+ 12 条开放项（本节 §2 第 11 条）；
- `docs/162-loment-distribution.md` —— 发行包（命令安装 + 自解压安装包）；
- `docs/163-loment-signing.md` —— 签名（Authenticode + 分离签名；自签名不消警告）；
- `docs/161-loment-checkout-eol.md` —— 检出行尾契约：CRLF 为什么能伪装成逻辑红；
- `docs/150-loment-selfhost.md` —— 自举链（lexer/parser/checker/codegen/定点）与 M80 收口；
- `docs/159-loment-seed-bootstrap.md` —— 无 Python 自举与工具链去 Python 的进度排序；
- `docs/145` / `docs/154` —— 100 里程碑账本（当前：完成 87 · 部分 8 · 未达 1 · 未开始 4）。
