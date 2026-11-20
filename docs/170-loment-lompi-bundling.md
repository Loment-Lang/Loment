# 170 · lompi 随 Loment 一起发行

> 版本 `0.1.4` · lompi `0.2.0` · 实现 `loment/tools/lomcli.lomt` 之外
> · 判据 `tools/loment_lompi_test.py` · 同步 `tools/lompi_sync.py`
> · 边界见 `docs/169 §2`（`loment` 的命令面里**不出现** lompi）

## 0. 一句话

**lompi 从 0.1.4 Alpha2.3 起随 Loment 一起安装**，但它**不是 Loment 官方工具** ——
它不编 Loment、不读 Loment 源码树，是**另一个命令**。装完 `loment`，`lompi` 就在 PATH 上；
`loment help` / `loment commands` 里没有它，`loment <任何东西>` 也不转发给它。

它是**用 Loment 写的**，由同一条自举链编出来，所以"随包发行"这件事本身是可复现的。

## 0.1 命令面照 pip, `<store>` 可以省

命令面按 pip 摆（对照表在 lompi 指南 §11），但**每条取 store 的命令都不必写 store**：

```
lompi index fixture/store     ==  lompi index            # 省掉就用全局 store
lompi show  fixture/store x   ==  lompi show  x
lompi obj   add  fixture/store k.o  ==  lompi obj add k.o
```

省掉时用的是 `lompi config` 打的那一行 `store:`（全局根下的 `store\`，全局根本身也是从
`argv[0]` 反推的，见 §5）。**两条路走的是同一段代码**：命令行给了就照抄，没给就推一个，
再往下就没有分支了。

为什么要这样：pip 的使用者从来不需要知道 site-packages 在哪。要求每条命令都自带 store
路径，等于把"你的库装在哪"变成每次调用的必填项 —— 而那是机器该记住的事，不是人该背的。
`list` 是 `index` 的同义词（pip 的习惯动词）。

**两个版本号是两件事**，别看成同一个：`lompi version` 打的 `0.2.0` 是**这个工具自己**的
版本；`lompi/store/<name>/0.1.0/` 里那个 `0.1.0` 是**随包标准库**（`std`/`host`）自己的
包版本。升 lompi 不该动后者 —— 动了就要同时改 `loment_release.GLOBS` 与
`loment/tools/lomrel.lomt` 里那两个写死的 glob（判据见 `loment_lompi_test` 的
`LPI_VERSION` / `STORE_VERSION` 一对常量）。

打包侧的 `--opt`（`python tools/loment_dist.py --emit --opt`）让 **ELF 那一半**改走
`clang -O2`（docs/212 §5A）；没有 clang 就**降级并说明**，包照样出得来。PE 那一半不走
这条路 —— 这个包的 PE 形状是自举链接器出的，不是 clang 出的。

## 1. 正本在外面，仓里只放快照（三组配对）

lompi **在外面开发**，本仓 `lompi/` 是**随包发布的快照**。有三组配对，
`tools/lompi_sync.py` 管它们：

| 组 | 正本 | 仓内副本 |
|---|---|---|
| 源码 + 夹具 | `D:\Dev\Lolment-ku\lompi`（`--from-dir` / `LOMENT_LOMPI_DEV` 可换） | `lompi/` |
| agent 指南 | `~/.claude/skills/lompi/SKILL.md`（lompi 线在那里写它） | `.claude/skills/lompi/SKILL.md` |
| 标准库 store | `D:\Dev\Lolment-ku\store`（`LOMENT_LOMPI_STORE` 可换） | `lompi/store/` |

第三组是**整棵树照收**（`<name>/<version>/*`，没有"顶层文件白名单"可言），所以它的清单
是 `None` 而不是一串模块名 —— 逐层列出来只会漏。

```
python tools/lompi_sync.py                # 门禁模式: 逐字节比 (正本不在本机则**明说跳过**)
python tools/lompi_sync.py --from-dev     # 正本 -> 仓 (把外面的改动收进来)
python tools/lompi_sync.py --to-dev       # 仓 -> 正本 (在仓里改了要同步回去)
python tools/lompi_sync.py --status       # 只打印两边的摘要与各自哈希
```

**为什么要有这个工具**：CLAUDE.md 里那句「正本只有一份 —— 别处都只是指针或同字节拷贝，
复制出第二份必然漂」。这里**必须**有两份（正本在开发区），所以就得有个东西把"漂了"变成
**看得见**的红。它不是摆设 —— 第一次跑就当场抓到 `lpi_pkg.lomt` 两边不同；写这条的时候
又抓到指南那份漂了。

**正本不在本机时门禁模式跳过并打一行明说**（CI / 别人的机器上没有那条路径），但**仓内那份
自身的完整性是无条件查的**（每个模块文件在不在、夹具目录在不在）。

## 2. 打包与安装

- `loment_dist.py` 的 `TOOLS` 从二元组变成**三元组**：`(工具名, 入口, 编译时的 CWD)`。
  CWD 那一格是给**路径形式的 `use "..."`** 用的 —— 自举镜按 **CWD** 解析相对路径
  （参考实现按入口文件所在目录），仓库其它工具全用名字形式（`use bytes`），所以它们一律
  写 `"."`。**lompi 是唯一一个用路径形式 import 的**，它那格是 `"lompi"`。
- 包里落 `bin/lompi`（Windows 是 `bin/lompi.exe`）、`share/lompi/skill/SKILL.md`、
  `share/lompi/store/`。
- **标准库随包一起装**（用户 2026-09-16 定）：`std`（127 个模块 + `std.lomt` 门面）与
  `host`，各带一份 `pkg.lomp`，**共 137 个文件**。包里那份是**纯净副本**，安装器再照
  lompi 自己认的全局 store 拷一份过去。
  - **目的地一律问 `lompi config`**，不在这儿重推 `cfg_root()` 的三条规则 ——
    重推一份的下场是**漂**：lompi 改了规则，装在别处的库它自己看不见，而这边一切正常。
    判据 `test_installers_ask_lompi_where_the_store_is` 盯这条（脚本里出现 `.lompi`
    就红，那是 fallback 规则的尾巴、自己推才写得出）。
  - **卸载只收回本包发过的那几个 `<name>/<version>`**（从装出来的纯净副本里列出来），
    用户自己装的别的版本不碰。卸载时**先问再删** —— `bin/lompi` 一删就问不着了。
- **agent 指南与 loment 那份同一套装法**（用户 2026-09-15 要求）：
  装进 `~/.claude/skills/lompi/`、往 Codex 的 `~/.codex/AGENTS.md` 写一段**独立标记**的
  指针、卸载时摘掉。**标记用 `lompi:` 前缀而不复用 `loment:`** —— 两份指南是两件事，
  卸载一份不该动另一份。`--no-skill` / `-NoSkill` 同时管两份。
- 装完的冒烟：`loment version` 之外再加一条 `lompi`（无参打用法、退 2）—— 它是独立命令，
  所以单独冒烟。
- **安装脚本一律纯 ASCII**（PowerShell 5.1 按 ANSI 读无 BOM 脚本，非 ASCII 会连带把后面
  的行解析坏）。写这一段时踩过一次：注释里写了中文，`install.sh` 立刻不再是纯 ASCII ——
  判据里有一条专门守它。

## 3. 边界（用户定的，别越）

| 是 | 不是 |
|---|---|
| 随 Loment 一起装，在 PATH 上 | Loment 官方工具 |
| 用 Loment 写的、同一条自举链编的 | `loment` 的子命令 |
| 有自己的指南、自己的版本号、自己的自检 | `loment help` 里的一行 |

**不要**因为"它随包一起装"就把它挂进 `loment` 的命令表：**装在一起 ≠ 是同一件工具的部件。**
判据 `test_lompi_is_not_a_loment_subcommand` 钉着这条（往启动器或 `lomcli.lomt` 的目录里
塞 `lompi` 会立刻红）。

## 4. 判据（17 条，`tools/loment_lompi_test.py`）

判的是"它作为**发行件的一部分**是好的"，不判 lompi 自己的功能对不对（那是它的自检驱动的事）。

| 组 | 判的 |
|---|---|
| 编得出来 | 参考实现能**检查**它（这条曾经是红的，见 §5）；发行包同一条路（stage1 + lomelf）能编出来 |
| 跑得对 | **`lpi_test.lomt` 逐模块自检全绿**（7 个模块，退出码即结论）；`index` 列出夹具里的 5 个包，且**同名不同版本给不同内容哈希**；`check` 有**分辨力**（合法库退 0 说 OK，现造的坏库退 1）；`show` 报完整 64 位内容哈希；`version` 与源码真源一致且钉在 `LPI_VERSION` |
| 边界 | `lompi` 不出现在 `lomcli.lomt` 的命令目录 / help 总览，也不出现在两个启动器里 |
| 装法 | 包里有指南；两个安装器都按 loment 那套处理它，**且带自己的标记**；卸载摘得掉；五个脚本纯 ASCII |
| 标准库 | 137 个文件一件不少地进包、进 `_fresh_sources`（**源码直出**，漏重打包就红）；发布清单逐条覆盖 store；两个安装器都**问 `lompi config`** 而不是自己推规则；端到端**真装一遍**再让 lompi 自己去 `index` 那个 store |
| 一致性 | 仓内快照与外面正本逐字节一致（正本不在则明说跳过） |

**证伪过**：往启动器塞 `lompi` → 红；安装脚本混一个中文字符 → 红；把 lompi 的标记并成
loment 的 → 红；版本号漂到 0.2.0 → 红。加标准库这一轮又当场抓了三条自己的错：
判据把 store 路径少写了一层版本目录、装前/装后的前缀本就该是两个（写成了相等）、
以及发布清单里的 `**` 在自举的 glob 上**不递归**。

## 5. 诚实清单

- **参考实现与自举镜为 lompi 产出的 IR 仍不一致**（2026-09-15 实测 **114 行差异**，全是
  **宽度**类：参考把 `(off as u64) * 4294967296` 折成 i64，镜折成 i32）。属于任务 #18 记着
  的镜的混宽毛病，**既有、非本轮引入**；在这一处取值无害（操作数是字面量 0），所以不影响
  产物。**后果要说清**：同一份源码两侧产不出同一份 IR，所以 lompi 进不了 p8 的逐字节语料
  —— 本文件判的是"发行件是好的"，**不是**"双实现一致"。
- **`lpi_test.lomt` 不进发行包**（它不是 `bin/lompi` 的组成部分），但**跟着一起收进仓**，
  留在这里当判据用 —— 它比冒烟一条命令强得多。
- **lompi 自己的功能判据在它那边**（正本里的自检驱动）。本文件只守"随包"这一层。
- **POSIX 上全局根是个带反斜杠的怪名字**。`cfg_root()` 是**为 Windows 写的**：它认
  `\AppData\Local\` 与 `\Users\`，都不是就退到 `<exe 目录>\.lompi`。而 `cfg_join()` 拼
  子路径时**一律用 `\`** —— 于是在 Linux 上 `lompi config` 打出来的是
  `/opt/loment/bin\.lompi\store`，`mkdir -p` 会造出一个名字里**带反斜杠**的目录。
  它**能用**（lompi 自己算的是同一个串，找得到），只是难看。**这是 lompi 的设计，不是
  装脚本的锅** —— 装脚本照它说的那个路径放，不自作主张改成 `/`（改了它就真找不着了）。
  要治得治 lompi 那边（它的正本在开发区）。安装时那个路径会**原样打出来**，不藏。
- **发布清单里 store 的版本号是写死的**（`lompi/store/std/0.1.0/*`），因为自举的 glob
  只认"一段目录 + 一个名字模式"：`lompi/store/**` 在那边只展开一层，把 `std`/`host`
  两个**目录**当文件收了进来（实测 215 vs 213）。两条清单必须同时改，所以钉了一条判据
  `test_release_manifest_covers_every_store_file` —— 否则"库升版、清单没跟上"时
  `loment_release --check` 会**看不见文件却照样绿**。
- **`loment_dist --check` 原来有个洞，这轮补了**：它只在"归档里有这个条目"时才发现陈旧，
  于是新加一个 store 模块却忘了重打包，永远抓不到。现在**归档里缺条目也算陈旧**。
- 打包用的是 **stage1**（自举镜），不是参考实现 —— 因为发行包就是这么编的，判据要判
  "发出去的那个东西"。
