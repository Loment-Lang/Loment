# 171 · Loment / lompi 的发布口

> 2026-09-15 用户定 · 工具 `tools/loment_publish.py` · 判据（门禁模式）`--check`
> · 上游：`docs/162`（发行包）、`docs/170`（lompi 随包）
>
> ⚠️ **归属已变（2026-10-17）**：三个库都搬进了 `Loment-Lang` 组织，
> `tools/loment_publish.py` 的 `ORG` 已改成它。**下面正文里的 `FujoJTOP` 是当时的记录，
> 不是现状** —— 现状与搬迁的三条坑（站点 URL、门禁镜像、Actions secret）见 `docs/255`。

## 0. 一句话

**开发在 `Loment-Lang/loment`（2026-10-17 起；此前是 `FujoJTOP/loment`），
发布口在各自的库。**

| 库 | 地址 | 可见性 | 装什么 |
|---|---|---|---|
| loment | `github.com/FujoJTOP/loment` | **public**（2026-09-16 用户定） | **开发口**：语言本体 + 工具链 + lompi + 文档 + 编辑器支持 |
| lompi | `github.com/FujoJTOP/lompi` | **public**（2026-09-17 用户定；09-16 只说开源 Loment 本体） | 包管理器（摊平到根）—— 保留为**单独特化的发布口** |

**原先的形态**（09-15 到 09-17）：开发在 FujoOS 单仓，两个库只是额外的发布口。09-17 用户把
开发口搬到了 `FujoJTOP/loment` —— 见 §0.2。

### 0.2 开发口搬到 `FujoJTOP/loment`（2026-09-17）

用户："未来这个库就是开发口了，你也需要从 FujoLang 文件中移除 FujoOS 源文件，FujoLang 分支
从 Github 下架，FujoLang 文件夹改名 Loment-DEV"。

- **FujoOS 源文件从 Loment 树里移走**：`kernel/` `sdk/` `ui/` `uisport/` `fujo-compat/`
  `fujopack/` `fujorun/` `release/` `assets/` `eval_results_v2/` `scripts/` `Cargo.*`
  与绝大多数 `docs/`。**这不是搬家** —— FujoOS 线有它自己的工作树
  （`D:\Dev\FujoOS` / `FujoOS-compat` / `FujoOS-netDEV` …）。
- **历史也滤掉**：新仓库只带 Loment 线的提交（`git filter-repo`）。只删文件是不够的 ——
  kernel/sdk 那些 diff 与提交信息会留在历史里。
- **lompi 跟着公开**：开发树离不开 `lompi/`（`loment_dist.py:67` 拿它构建**随包发行**的
  lompi 工具，`loment_lompi_test.py` 读它的源），而开发口是 public —— 两边可见性必须一致。
  用户选了"lompi 也开源"，于是 09-16 那条"只开源 Loment 本体"的划分作废。
- **本地目录**：`D:\Dev\Loment-DEV`（新克隆）。`D:\Dev\FujoOS-FujoLang` **暂时留着当后路**，
  等新开发口验证过再撤。
- **`Fujoos-FujoLang-DEV` 远端分支**：等新开发口验证好再下架。核过：它比 `FujoDEV-V2`
  领先 168 个提交，而那 168 个**只碰 Loment 线路径**，没有 kernel/sdk —— 所以下架不丢别的线。
- **`loment_publish.py --push loment` 从此是"一次性引导"**，不是日常机制 —— 开发口用普通
  `git push`。`lompi` 那个口照旧由它维护。

### 0.1 三个决定（2026-09-16）

- **loment 转公开。** 官网（`fujojtop.github.io/FujoOSwebsite`）本来就是公开的，
  它上面的 Loment 页面此前只能指向两个私有库 —— 现在指向得到东西了。
- **不打 tag。** 公开的是**仓库**，不是**发布**：安装包还没有对外分发，
  打 tag 会被读成"这一版发布了"。tag 留到真发布（M100 的外部审计之后）。
- **仓库元数据进 spec，不手敲。** `loment_publish.REPOS[*]` 里现在带
  `visibility` / `description` / `homepage` / `topics` 四项，`--push` 每次把它对齐到 GitHub。
  原先靠手敲 `gh repo edit`，只生效一次：换机器、重建库、或者谁手动改回私有，就悄悄漂了，
  而 `--check` 看不见（它只看路径清单）。**可见性是唯一有外向后果的那一项** ——
  私有变公开会在 GitHub 上留痕且基本不可逆，所以它变了会**打出来**，不安静地做。

下载的指向也定死了：**安装包发在本仓库的 Releases**，官网指向 GitHub，不是反过来
（用户 2026-09-16 明确："不要 Loment 去官网下载"）。README 里因此写的是
「发在本仓库的 Releases 里 —— 现在还没有发布」。

## 1. 为什么是"发布口"而不是"搬家"

单仓里 961 个已跟踪文件里，Loment 只占 314、lompi 33；其余是内核 / sdk / ui / 资产。
而这些路径**互相引用得很密**：`CLAUDE.md` 指向 `lom/*.lom`，`docs/*` 里到处是
`loment/tools/lomcli.lomt`、`tools/loment*.py`。真搬走就要一次改干净所有引用，
而其中一部分归**别的线**（跨线协议：不碰对方的路径）。

所以分工是：

- **单仓 = 正本与开发地** —— 所有线在这里干活，判据在这里跑；
- **两个库 = 发布口** —— 外面的人 `git clone` 它们就能拿到 Loment / lompi，
  不用把整个 FujoOS 拖下来。

## 2. 怎么发

```
python tools/loment_publish.py --status        # 切出来会是什么样（文件数 + 树哈希）
python tools/loment_publish.py --check         # 门禁: 路径清单自洽
python tools/loment_publish.py --push loment   # 或 --push lompi / --push all
```

- **切分用 `git filter-repo`，在克隆副本里跑** —— 它默认**就地重写仓库**，绝不能对工作仓
  下手。工具每次都 `git clone --no-local` 到临时目录再过滤，跑完删掉。
- **历史保留**：切出来的库带自己那段历史（loment 侧 143 个提交，lompi 侧 1 个 ——
  `lompi/` 本来就是一次性收进单仓的）。
- **推成 `main`**：空库的**第一个 ref** 会成为默认分支。**别**用 `gh repo create --source
  --push` —— 它会把你**当前分支名**（`Fujoos-FujoLang-DEV`）当默认分支，一个新库顶着
  单仓的开发分支名很怪。第一次跑就撞上了，现在改成显式 `push HEAD:main` 再 `PATCH
  default_branch`。
- **幂等**：库已存在就只推，不重建。

## 3. 路径清单为什么这样切

**Loment 那侧保留原结构**（`loment/`、`lom/`、`tools/`、`docs/`、`editors/`）：
那边**到处是按路径引用的**，路径一改，全部文档与注释失效。

| 切什么 | 为什么 |
|---|---|
| `loment/` | 语言本体：前端、自举镜、示例、库、种子 |
| `lom/` | L0 接口契约（跨线契约，`docs/141`） |
| `editors/` | vim / VS Code 的语法与 LSP 支持 |
| `docs/manual/`、`docs/{14?,15*,16*,17*}-loment-*.md`、`docs/141-l0-lom-spec.md` | 语言文档 |
| `tools/lom*` | 工具链（Python 参考实现 + 全部判据 + 发布/打包工具） |
| `.claude/skills/loment/` | 给 AI agent 的指南 |
| `LICENSE` | 没有它不算一个能发出去的仓库 |

**lompi 那侧摊平到根**（`--path-rename lompi/:`）：它是个自足的小项目，`lompi.lomt` 直接
在根上才像正经仓库；而且它的模块互相用 `use "lpi_cli.lomt"` 这种**相对 CWD** 的导入，
摊平后"在仓库根下 `loment build lompi.lomt -o lompi`"正好成立。

**有意不在里面**的：`tools/lompi_sync.py` 与 `tools/loment_lompi_test.py` 留在单仓 ——
它们管的是"**单仓里那份 lompi 快照**"（同步与随包判据），属于 Loment 侧的机制，
不是 lompi 自己的东西。

## 4. 这一层的边界（诚实清单）

- **不在 CI 里跑 `--push`**：推送是对外动作，得人按。`--check` 进 CI，它只读。
- **没有自动同步**：单仓提交之后，两个库**不会自动跟上** —— 要显式跑 `--push`。
  判据 `--check` 只保证**清单自洽**（路径都存在、条目数没崩），
  **不保证两个库是最新的**（那要联网比对，不适合进门禁）。
- **发布口是只读的**：**别在 `FujoJTOP/loment` / `lompi` 里直接提交** —— 下次
  `--push` 会 `--force` 把它盖掉。两个库的 README 第一段就写着这句话。
- **`--force` 是刻意的**：发布口的内容应当**完全**由单仓决定；留着分叉只会让人困惑。
  代价是"在那边提交过的东西会没"。
- **切的是 `HEAD`**：只发已提交的内容，工作区里没提交的改动不会进去。

## 5. 第三个发布口：VS Code Marketplace（扩展）

> 2026-10-13 · 首次发布 `hooyan2022.loment` **v0.2.0** · 判据 `vscode_ext_test::test_vscode_publish_ready`
>
> **v0.2.1（同日）**：listing 整个换成**英文**、并且**不再拿 FujoOS 介绍自己**（商店页显示的就是
> `editors/vscode/README.md` 加 `package.json` 的 `description`；第一版是中文、开篇写的是
> "FUAI-OS 自研底层语言"，而 Loment 自 0.1.4.5 起已经是独立的东西，且 FujoOS 外界拿不到）。
> 同一次把 `package.json` 里**用户看得见的**文案全换成英文（命令标题、设置说明、调试器标签、
> 不受信工作区那段）—— 此前 32 条中文串，现在 0 条。`src/*.js` 里的**注释**保持中文（仓内文风），
> 只有弹给用户看的那几句改了（编译器/工具链找不到时的提示）。
>
> **补记（同日的事实，不追因）**：第一次上传用的是**另一个账号**下的 `FujoJTOP` publisher ——
> `vsce` 同样回了 `DONE`，但那次在浏览器里打开 `manage/publishers/FujoJTOP` 是 **404**，
> 外面的三个探针（发布者页 / item 页 / 下载端点，外加一个已知存在的扩展做对照）也全查不到。
> 原因没查明（最可能是**浏览器登的账号与那个发布者不是同一个**），改用 `hooyan2022` 重发，
> 不再追。旧账号下若真躺着一条，删不删都不影响这里。
>
> **建发布者只能人来点**：Marketplace 的 API 明确拒 ——
> `Creating a publisher from command line is not supported`（错误类型是 `InvalidReCaptchaTokenException`，
> 它挂了 reCAPTCHA 防脚本建号）。所以这一格写进流程，别指望自动化。

上面两个口是**代码仓**（`loment_publish.py --push`，内容完全由单仓决定）。Marketplace 是
**另一个形状**：它是个商店 —— 发布要一个发布者账号，而且**版本号发出去就烧掉了**。所以单独成节。

**地址**：<https://marketplace.visualstudio.com/items?itemName=hooyan2022.loment> ·
管理台 <https://marketplace.visualstudio.com/manage/publishers/hooyan2022> ·
装法 `code --install-extension hooyan2022.loment`。

### 5.1 账号那一半（做一次，之后不用碰）

微软账号 → 建一个 Azure DevOps 组织 → 建 **PAT**（[短链](https://go.microsoft.com/fwlink/?LinkId=307137)）→
在 [manage](https://marketplace.visualstudio.com/manage) 建发布者。

两处**填错就 401、而报错看不出来**：

- PAT 的 **Organization** 要选 **All accessible organizations**（选单个组织不行 —— 商店发布跨组织）；
- Scopes 用 **Custom defined** → **Show all scopes** → **Marketplace → Manage**（只要这一个）。

发布者 **ID 是永久的**（`FujoJTOP`，与 `package.json` 的 `publisher` 必须一致），显示名可以改。

### 5.2 发布

```
cd editors/vscode
npx --yes @vscode/vsce@latest publish      # 版本取 package.json；token 走 VSCE_PAT 环境变量
```

**升版用明确给号的写法**（`vsce publish 0.2.1`），别用 `patch`/`minor` —— 那两个会自己改
`package.json` **并提交**，与"版本写在源里、由人提交"的既有习惯打架。

### 5.3 发布前那四件（都有判据或工具兜着）

| 件 | 为什么 |
|---|---|
| `repository` | 没有它 `vsce` 每次都要求 `--allow-missing-repository` |
| 扩展目录里的 `LICENSE` | **`vsce` 只找扩展目录**这一份（仓根那份它看不见）；`Loment-Open` 不是 SPDX 标识，找不到文件它会提示。判据钉住它与仓根**逐字节相同** |
| `icon`（128×128 PNG） | 没有它商店卡片是灰的。现在这份是从 `editors/loment.ico` 的 128 帧转出来的 |
| `package.json` 的 `files` | vsce 的允许清单。**`node_modules` 必须在里面** —— 商店装下来的那份**不会**跑 `npm install` |

判据 `test_vscode_publish_ready` 把它们连同"两份打包清单不许漏同一类文件"一次钉住；
`vscode_ext.check()` 里另有 `icon` / `LICENSE` 两条（每次打包都会看）。

### 5.4 边界（诚实清单）

- **两个打包器**：商店走 `vsce`，本机侧载走 `python tools/vscode_ext.py --install`（确定性字节、进判据）。
  **别拿侧载那份去发商店** —— 那份的 `vsixmanifest` 是手写的，商店的校验比侧载严，没验过。
- **不在 CI 里发**：与 §4 同一条道理 —— 发布是对外动作，得人按；PAT 也不进 secrets。
- **PAT 是明文的**：`vsce` 从 `VSCE_PAT` 读。**别把它写进任何文件、任何提交**；
  贴出去过（截图、聊天、日志）就立刻去那个页面撤销重发。
- **listing 的第一句必须说清"需要一套 Loment 检出"**：扩展不带编译器（`README.md` 顶上那句就是为它写的）。
  不说的话，第一条评价会是"装了没反应"。
- **版本号烧掉就没了**：撤下来也没法重发同一个号；发错了就发下一个号。
