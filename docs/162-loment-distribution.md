# 162 · Loment 发行包：命令安装与安装包安装

> 版本 `0.1.4`（显示名 **Loment 0.1.4**）· 构建器 `tools/loment_dist.py`
> · 只要**源码 + 编辑器工具**的那种包见 `docs/164-loment-source-kit.md`
> · 判据 `tools/loment_dist_test.py`（进门禁；审计里是 C15）

## 0bis. 2026-09-14 重大变更：包变成本机原生，WSL 与 clang 都出局

**下面几节里"Windows 装进 WSL + `loment.cmd` 转发""`run`/`build` 需要 clang"的说法已经过期。**
现在：

- 同一份 IR **各链一遍**：Linux 包放 ELF、Windows 包放 **PE**（`.exe`），链接器是包内的
  **`loment-lomelf`**（`loment/tools/lomelf.lomt` 的自举产物）—— **不再需要 clang**；
- stage1 按**本机构建格式**出（Windows 上出 PE），所以构建过程在**本机直接跑**，**不再经 WSL**；
- Windows 安装：解包 → `install.ps1` 拷进 `$Prefix`（默认 `%LOCALAPPDATA%\Loment`）→
  包里自带的 `bin\loment.cmd` **直接调那些 `.exe`**；
- 判据新增两条：装完后 `loment run` 在本机编出 PE 并跑出 `PASS loment-user`，
  以及 `loment.cmd` 里**不再出现 `wsl`**。

也就是说不只是"包里没 Python"，现在是**包里没有任何外部依赖**：`loment build`/`run`
在一台干净的 Windows 上开箱即用。详见 `docs/167 §5`。

**包里还带 lompi**（2026-09-15 加）：`bin/lompi` + `share/lompi/skill/SKILL.md`
+ **`share/lompi/store/`（它的标准库，137 个文件 —— `std` 127 个模块 + `host`，
各带一份 `pkg.lomp`；安装时照 `lompi config` 说的路径拷进 lompi 的全局 store，
装完就能直接 `use std`，卸载收回本包发过的那几个版本）**。
它是 **Loment 库的包管理器**，用 Loment 自己写的，但**不是 Loment 官方工具** —— 是**另一个
命令**：`loment help` 里没有它，`loment <任何东西>` 也不转发给它。它的指南与 loment 那份
**同一套装法**（进 `~/.claude/skills/lompi/`、往 Codex 写**独立标记**的指针、卸载摘掉）。
正本在开发区、仓内 `lompi/` 是快照，两边由 `tools/lompi_sync.py` 校验 —— 全部细节见 `docs/170`。

**包里还带一份 agent skill**（2026-09-15 加）：`share/loment/skill/SKILL.md` ——
安装时同时写进 `~/.claude/skills/loment/`（用户级，任何工程都读得到；`--no-skill` /
`-NoSkill` 可关，卸载会摘掉；`~/.claude` 不存在就只留在包里并打印怎么手动放）。
它**自足**：内建函数表、语法、E1–E20 错误码、包内命令全在里面，不引用源码仓库路径 ——
目标是"装完 Loment，AI agent 读它就能写 Loment"。随包那份与仓库里的
`.claude/skills/loment/SKILL.md` **逐字节相同**（判据在 `loment_dist_test`）。

**指南里的代码由判据守着**（2026-09-15 补）：§1 的 `tour` **代码体**与仓库
`loment/examples/tour.lomt` 逐字节相同（**只有开头那段 `//` 注释不同** —— 指南里那份
去掉了指向仓库路径的注释，因为纯包用户没有本仓库），且**每个 `rust` 围栏样例都必须过前端**。
起因是实测出来的 —— 指南里那份 tour 漏了个 `;`、根本编不过，而它正文自称"和仓库里那份
是同一份"，此前没有任何东西守这句话；照抄它的用户只会得到一个莫名其妙的 E19。
要展示**故意写错**的片段时，在围栏前一行加 `<!-- no-compile -->` 豁免。

**别的 agent 怎么办（没有 Claude / 没有 Codex）**：不靠目录约定 —— **跑 CLI**。
`loment skill` 打印指南路径，`loment skill --print` 直接打全文，而 `loment help` 的用法里
就印着这一行。任何 agent 上手陌生语言的第一条命令都是 `loment --help`，入口因此与它是什么
工具无关（判据：`--print` 的输出与包里那份指南逐字节相同，在 `loment_dist_test` 里）。

**已知卡点（诚实写在这里）**：包里 `loment build`/`run` 用的链接器是**自举镜像**
`loment-lomelf`，而它**还不支持聚合按值**（struct / enum 当参数或返回值）——
那类程序要么报一条不像给用户看的错，要么直接崩。参考实现与 Python 后端都没这个问题。
详见 `docs/167 §5` 第 3 条。

## 0. 一句话

同一个包**两种装法**：命令安装（`sh install.sh` / `powershell -File install.ps1`）与
安装包安装（Windows 双击 `setup.exe`）。装出来的东西完全一样 —— **四个自举产物 + 启动器 + 种子，
包里没有 Python**（docs/159 那条"写并跑 Loment 不需要 Python"的落地形态）。

## 1. 产物（`loment/dist/`）

| 文件 | 装法 | 说明 |
|---|---|---|
| `loment-0.1.4-linux-x64.tar.gz` | 解包 → `sh install.sh` | Linux / WSL；含 `install.sh` |
| `loment-0.1.4-windows-x64.zip` | 解包 → `powershell -File install.ps1` | Windows；含 `install.ps1` / `install.cmd` |
| `loment-0.1.4-windows-x64-setup.exe` | **双击** | 自解压安装包（Windows 自带 `iexpress` 做的） |
| `SHA256SUMS` | — | 上面三件的 sha256 |

**两个归档是确定性字节**：zip 固定时间戳（`1980-01-01`）+ 目录项排序 + unix 权限位，
tar.gz 的 `mtime=0` + 稳定 uid/gid/uname、gzip 头不带时间。同输入两次构建 sha256 相同
（判据里有这条）。`setup.exe` **不是**确定性的（iexpress 自己塞时间戳），所以它只进
`loment/dist/SHA256SUMS`，不进 `loment/build/release-manifest.json` —— 与 VSIX 同一处理。

## 2. 包里是什么

| 路径 | 作用 |
|---|---|
| `bin/loment-driver` | 编译器（装载 → 检查 → 发射 LLVM IR）。**就是自举链的 stage1** |
| `bin/loment-lsp` | 语言服务（补全/跳转/诊断/`--check`），stdio 上的 LSP |
| `bin/loment-fmt` | 格式化器（与 Python 版逐字节相同，docs/159） |
| `bin/loment-doc` | API 文档生成器 |
| `bin/loment` | 启动器（`version`/`ir`/`check`/`build`/`run`/`fmt`/`doc`/`lsp`/`skill`；**其余命令先看 `PATH` 上有没有 `loment-<名字>`** —— 有就是用户注册的命令，原样转发；没有再交给 `loment-cli`。见 `docs/169` §3b） |
| `bin/loment-cli` | **命令前端**（38 条命令：`help`/`codes`/`explain`/`syntax`/`cheat`/`stat`/`grep`/`ls`/`tree`/`new`/…，见 `docs/169`）。**用 Loment 自己写的** —— 命令面只写一份，两个启动器各转发一行 |
| `bin/lompi` | **Loment 库的包管理器**（Loment 自己写的，源码 `lompi/`）。**独立命令，不是 `loment` 的子命令** —— `loment help` 里没有它，直接敲 `lompi`；详见 `docs/170` |
| `share/loment/seed.ll` | 自举种子 —— 只用 clang 就能从它重建整套工具链 |
| `share/loment/version` | `Loment 0.1.4 (0.1.4)` + 提交号与提交日期 |
| `share/loment/examples/user_hello.lomt` | 示例（用 syscall 打印） |
| `SHA256SUMS` | **随包**校验和，安装脚本第一步就校它 |

四个 ELF 都是自举产物，构建路径与冻结面判据同一条：

```
clang(种子 selfhost_driver.ll) -> stage1
stage1 <entry.lomt>            -> IR      (与参考实现逐字节相同, 见 docs/158)
clang -nostdlib -static        -> ELF     (无 libc, _start 即入口)
```

## 2b. 包不是封闭的：两个扩展点（0.1.4-pre2）

装好的 Loment 是**长东西的底座**，不是一组固定文件 + 一组固定命令。两处开口都在**文件系统**
上表达，没有注册表、没有新格式：

| 想做的事 | 怎么做 | 规范 |
|---|---|---|
| 自己的源码后缀（`.foo` 而不是 `.lomt`） | 项目根放 `loment.conf`，`pub fn source_ext() -> str { return ".foo"; }` | `docs/143` §2.3 |
| 自己的 `loment` 子命令 | 把可执行文件命名成 `loment-<名字>` 放进 `PATH`；`loment <名字> ...` 原样转发 | `docs/169` §3b |

两条都不需要重装 Loment，也不需要工具链知道你的项目 —— 这也是为什么它们能由**第三方**用：
`loment git` 里的 `git` 不是 Loment 官方命令，而是"某个用 Loment 写的软件注册了它"。
官方命令（`version`/`build`/…）**优先**，所以注册自定义命令永远不会盖掉已有行为。

## 3. 安装语义

- **前缀**：Linux 默认 `$HOME/.local`（`--prefix` 可换）；Windows 默认 `%LOCALAPPDATA%\Loment`。
- **Windows 是两层**：工具链是 Linux ELF，所以装在 **WSL**（`$HOME/.local/share/loment`），
  Windows 侧只放一个 `loment.cmd` 转发（`wsl -e <wsl>/bin/loment %*`）。启动器自己把
  `D:\...` 路径转成 `/mnt/d/...`（`wslpath`）。
- **PATH**：Windows 走用户级 `Path`（`HKCU`，不需要管理员）；Linux 只打印该加哪一行，
  **不替用户改 shell 配置**（`--no-path` 关掉提示）。
- **PATH 上有更靠前的同名命令**：安装器只**追加**、不重排别人的 PATH，所以一个更早装下的
  `lompi` / `loment` 会**静默**盖过这一份 —— 敲 `lompi` 拿到的是旧那份，而**装完不会有一句话
  提到**（2026-10-12 的报障：`lompi` 一直是 0.1.0）。装完会**点名**它的路径；**不删**它 ——
  那不是这个安装器的东西。判据两半：静态形状（跨平台，门禁跑得到）+ Windows 上放个诱饵真跑。
- **文件类型**：Windows 上注册 `.lomt`/`.lom`（ProgID `Loment.Source` / `Loment.L0`，
  HKCU only），与 `tools/loment_filetype.py` 同一套键；`-NoFileType` 可关。
- **冒烟测试**：装完立刻 `loment version` + 编一个示例，失败就报错退出（不是"复制完就完事"）。
- **双击 = 窗口，带参数 = 命令行**（2026-10-12 用户报"双击 setup.exe 毫无反应"之后加的）：
  `install.cmd` 无参数时交给 `welcome.ps1` —— 渐入圆角标 + `Welcome to Loment` → 选装到哪 →
  进度（把 `install.ps1` 的输出流式显示）→ **完成回执（绿勾 + 版本 + 手动 Close）**。
  它**不重写安装逻辑**，只驱动 `install.ps1`：一份逻辑两张脸。带**任何**参数时走原来的命令行，
  所以 CI、`-DryRun`、`-Uninstall` 与所有判据的行为一个字节没变。
  * 实测那条报障：包解开了、脚本真跑了、东西真装上了 —— 只是**那条路上没有一个看得见的
    窗口**，于是"装好了"与"什么都没发生"长得一样（还留下卡在 `pause` 的僵尸 `cmd`）。
  * 判据两半：静态（跨平台：包里得有 `welcome.ps1`/`loment-logo.png`、两张脸的形状、
    窗口不重写校验）+ Windows 上真开一遍（`-AutoRun` 到沙箱前缀 + `-ShotDone` 自画完成页）。
- **卸载**：`sh install.sh --uninstall` / `powershell -File install.ps1 -Uninstall`
  （后者同时清 PATH 与文件类型键）。
- **幂等**：重复安装成功且不改语义（判据里有）。

**不做的事**（写在文档里免得当成缺陷）：不装 clang（地基语言，`run`/`build` 需要它，
缺了会明确报错）；不改系统级 PATH；不注册系统级文件关联（`UserChoice` 那条带哈希保护，
只能由用户在"打开方式 → 始终"里点）。

## 4. 自解压安装包怎么做的

用 Windows **自带**的 `iexpress`（`C:\Windows\System32\iexpress.exe`），不引第三方工具：

```
payload.zip + install.ps1 + install.cmd  --SED-->  loment-...-setup.exe
```

双击后：解压到临时目录 → `install.cmd` → `powershell -File install.ps1 -PayloadZip payload.zip`
→ 后续与命令安装**同一条路**（同一个 install.ps1，不是第二套逻辑）。

**为什么不用 MSI / Inno / NSIS**：本机没有 WiX/Inno/NSIS，装它们要联网 + 管理员；而
`iexpress` 是系统自带的，能做"双击安装"这件事。代价要如实说：

- **未签名/自签名** → SmartScreen 会提示"未知发布者"（README 里写了怎么继续；
  签名怎么做、为什么自签名消不掉警告，见 `docs/163-loment-signing.md`）；
- `setup.exe` 的字节**不确定**（不能当"可复现工件"）；
- 没有 MSI 那套企业分发/静默安装（`install.ps1 -DryRun` 可以当"先看计划"）。

### 4a. `install.cmd` 为什么在两种布局里都能用

`install.cmd` 是**自解压包**和 **zip 包**共用的入口：

- 自解压包：解出来的目录里有 `payload.zip` → 它先解包再装（`-PayloadZip`）；
- zip 包：目录本身就是 payload → 它直接调 `install.ps1`，不需要任何额外文件。

**这是踩出来的**：第一版 `install.cmd` 只认自解压布局（写死去找 `payload.zip`），而 zip 包里
根本没有这个文件 —— 用户解压 zip 后看到 `install.cmd`（最像安装器、也能双击）一跑就是
"install failed"。现在判据里有一条专门跑这条用户路径（`zip 布局下 install.cmd -DryRun 通过`），
并且双击时窗口会停住把结果打给你看。

### 4b. 签名与顺序

`loment/dist/` 里的 `setup.exe` 可以用 `tools/loment_sign.py --dist --sign --sign-sums` 签名
（Authenticode + SHA256SUMS 分离签名）。**顺序是硬约束**：签名会改 PE 的字节，所以清单必须在
**签名之后**重算 —— `--dist --sign` 已经内置（签完自动重算 `SHA256SUMS`），别手动先签后改，
否则 `loment_dist --check` 会对不上。

**当前 `loment/dist/` 里的发行件是未签名的**（2026-09-12 用户指示暂停签名这条线；重新打包会
重算字节、旧签名必然失效，所以已把失效的 `.sig/.asc/.pem/FINGERPRINT` 从该目录清掉，免得出一个
自相矛盾的下载件）。签名能力没删：一条命令 `python tools/loment_sign.py --dist --sign --sign-sums
--sign-gpg` 就能签回来；自签名消不掉 SmartScreen 警告，换 CA 证书只需设
`LOMENT_SIGN_PFX`/`LOMENT_SIGN_PFX_PASS`，流程不变（`--print-cmd` 打印等价命令）。

## 5. 判据（65 条，`tools/loment_dist_test.py`，进门禁；审计里是 C15）

| 组 | 判的是 |
|---|---|
| 布局 | 该有的文件都在；`.sh`/`.ps1`/`.cmd` 纯 ASCII；`.ps1`/`.cmd` 是 CRLF；`bin/loment` 是 LF；ELF 权限 755 |
| 归档 | 归档内容与 payload **逐文件 sha256 相同**；zip/tar.gz 两次写出**字节相同** |
| 产物 | `--check` 与 `SHA256SUMS` 一致；**且归档里「源码直出」的条目（skill / seed / 示例 / README / 启动器 / `version`）等于当前仓库里的那份**（2026-09-15 补：原先只拿归档跟它自己的清单比，两边一起过期就永远报「一致」）；归档里的 driver == 构建产物 |

### 新鲜度这一列踩过四次，每次都是同一个形状

「源码直出的条目」= 打进归档的字节**现在**就能从仓库算出来的那些。它们不在编译产物那一类，
所以 `--emit` 不会因为改了就重算 —— 忘了重打包时，归档里是旧的，而 `--check` 只拿归档跟
**它自己的** `SHA256SUMS` 比，两边一起过期就永远报「一致」。四次都是这么发现的（都不是假想）：

| 时间 | 漏掉的条目 | 怎么发现的 |
|---|---|---|
| 2026-09-15 | `skill` / `seed` | 用户问「安装包更新了吗」 |
| 2026-09-16 | `store`（新增 137 个文件） | 加 store 时补 `_fresh_sources` |
| 2026-09-16 | `install.sh` / `install.ps1`（模板直出） | 改完模板重打包才发现归档里还是旧的 |
| 2026-09-16 | **`share/loment/version`** | 打 0.1.4-pre2 时发现包里印的是**上一个**提交号的 commit 行 |

最后一条比前三条更该被盯：它带 **HEAD 短号**，所以**每一次提交都会让它过期**。配套的两条纪律：

1. **发射顺序**：先提交源码 → 重算发布清单 → 提交清单 → **最后**出包（`loment_dist --emit`）。
   出包放最后，包里那个 commit 行才等于最终 HEAD；反过来先出包再提交，包里永远是上一代。
2. **源码包要在出包之前**：`SHA256SUMS` 是出包时扫目录算的，里面也含 `loment-<ver>-src.zip`。
   先出包再出源码包，清单里的 src.zip 哈希就对不上了（`--check` 会报 DIFF）。
| **端到端 · Linux** | tar → 装进临时前缀 → `loment version` 出版本行 → **`loment ir` 的产物与参考实现逐字节相同** → `loment check` 正例 0 且不吐 IR → `loment run` 真跑出输出 → 缺组件时报错**指名**（`this package does not include loment-fmt`）→ `--uninstall` 摘干净 → 再装一次仍成功 |
| **端到端 · Windows** | 解包 → `install.ps1` **真装**（`-Prefix <临时>` + `-NoPath -NoFileType`，不动用户 PATH 与注册表）→ 写出 `loment.cmd` 且指向包内原生 exe → `loment version` 能跑 → **`loment run` 在本机编出 PE 并真跑出输出**（不再经 WSL）→ 装出的 agent skill 与仓库里那份逐字节相同 → `--uninstall` 摘干净（含 agent skill，且别家 agent 的文件按标记精确还原） |
| Windows 解析 | `install.ps1 -DryRun`、`-DryRun -PayloadZip`（自解压那条路）、**`install.cmd -DryRun`（zip 布局，用户双击那条路）** 在**真 PowerShell 5.1 / cmd** 下都能跑；`setup.exe` 是 PE 且非空 |

最强的一条是 Linux 那行的**逐字节相同**：它同时证明了"包里的编译器是自举产物"和"装出来的东西能用"。

### 为什么 Windows 那条要"真装"而不是只跑 `-DryRun`

只校验"脚本能解析 + 计划打印得出来"会漏掉真实的拷贝/路径 bug。本文档第一版就是这么漏的，
**真装一遍立刻抓到两个**：

1. **`Split-Path -Parent` 会把 `/` 改写成 `\`**（Windows 的 provider 语义），于是
   `wsl -e mkdir -p \tmp\a\b` 在 WSL 里建的是一个**名字带反斜杠的目录**（rc=0！），
   随后 `cp` 报 `No such file or directory` 指向错的地方。修法：WSL 路径的父目录用
   **纯字符串**算（`Wsl-Parent`），不碰 `Split-Path`。
2. **`WslDir` 传成 Windows 风格路径**（`C:/...`）时静默建出垃圾目录树。修法：显式校验
   必须是绝对 WSL 路径，否则直接报错 —— 这条现在也是判据之一。

## 6. 复现与边界

```bash
python tools/loment_dist.py --emit                       # 四个产物, 本机链接 (不需要 WSL, 也不需要 clang)
python tools/loment_dist.py --list                       # 只列会打进去的文件
python tools/loment_dist.py --check                      # 产物与 SHA256SUMS 一致, 且源码直出的条目最新?
                                                         # (第 4 件 loment-skill.zip = agent 指南, 由 --emit 一并产出)
python tools/loment_dist_test.py                         # 全量判据 (布局/归档/两条安装路径/skill)
```

也支持只打子集（门禁用它省时间）：`--emit --only driver --no-exe`。

**`--emit` 要在 commit 之后跑。** 包里 `share/loment/version` 的 `commit <sha>` 是**打包那一刻
的 `HEAD`**，所以在工作区改完就打包、之后才提交，产物上的那个 sha 会**永远落后一个提交**
（`loment_dist --check` 看不出来 —— 它不是"源码直出"的条目）。要发的那一版：先提交，
再 `--emit`。

**边界（诚实清单）**：只在 x86_64 上打过；Linux 侧只给 tar.gz（没有 `.deb`/`.rpm`）；
没有代码签名/公证；没有 aarch64 包（M92 的 aarch64 还没执行过，见 docs/145）；
安装包不含 clang，也不含 Python —— 两者都不需要才能装，但 `run`/`build` 需要 clang。
