# 222 · `loment lompicheck`：发布口的 lompi 跟上开发口了没

> 2026-10-09 · 2026-10-10 重写成**注册出来的命令**（`docs/223`）· 源码 `loment/tools/lompicheck.lomt`
> · 上游：`docs/171`（两个发布口）、`docs/169`（CLI 与启动器）、`docs/189` §47（指针比较那条限制）

## 0. 一句话

**`FujoJTOP/lompi` 是 `loment` 开发口里 `lompi/` 的一份发布副本**（`docs/171`：开发在主仓、
发布口只读、由 `tools/loment_publish.py --push lompi` 整体覆盖推上去）。于是"发布口是不是
最新"是个**可以算**的问题 —— 两个仓库里每个文件都有内容，逐文件比一遍就行。

```bash
# 编出来，产物就叫 `loment-lompicheck`（名字来自源码里的 `register` 块，`docs/218`）
loment build loment/tools/lompicheck.lomt
# 自己把它放进 PATH —— `loment build --install DIR` 还没做（`docs/218` §6 步骤 2）
mv loment-lompicheck ~/.local/bin/
loment lompicheck                                  # 之后随时
```

```
lompicheck: the lompi publish outlet against the loment source
  source  <你的 loment 检出>
  outlet  ~/.lompi/cache/lompi
[OK] the outlet is in step with the source (170 file(s) identical)
```

**始终以开发口为准**，而且**每次都查发布口** —— 不查就不知道它掉了队。掉了队**不是使用者
能修的**（发布口只由那条 `--push` 写），所以这条命令会明确说"该去提 issue 提醒开发者"，
并给出链接：`https://github.com/FujoJTOP/lompi/issues/new`。

退出码：`0` 一致 / `1` 不一致 / `2` 用法错或读不了。

## 1. 它是两半拼的，为什么必须两半

| 这一半 | 在哪 | 干什么 | 为什么不能是另一半 |
|---|---|---|---|
| **引擎** | `loment/tools/lompicheck.lomt`（编成 PE/ELF；一条注册出来的 `loment` 命令） | 只读**本地两棵树**，逐文件比内容 | 它**不联网**：不是因为没有 socket —— PE 派发面现在是 29 个号、socket 那一族两个平台都在（`docs/234`）—— 而是因为到 `api.github.com` 要走 TLS，**工具链里没有 TLS** |
| **拉取那一半** | **你**（跑引擎打出来的那条 `git` 命令） | 把 `FujoJTOP/lompi` 的一份缓存 clone 更新好，再跑一次引擎 | 引擎是 Loment，**不会 fork 也不会联网**；`git` 是 shell 的事 |

这条边界与 lompi 自己那条**现在不是同一条了**：`lompi fetch` 从 0.2.0 起自己做 HTTP
（`docs/236`），卡的是 **TLS 与解析器**，而这里卡的是**同一个** —— `api.github.com` 是 HTTPS。
两次都是**工具链缺，不是设计上不想要**。

（2026-10-10 之前这儿有个 `--install` 生成的 shell 薄壳替你跑 git。`docs/218` 的注册机制把
"注册"那一半吃掉之后它就没有必要了 —— 剩下的"打印一条 `git` 命令"不值得再养一个安装器，
那个 `tools/lompicheck.py` 已经删掉。）

## 2. 比的到底是什么

发布口那份是**摊平**过的（`loment_publish.REPOS["lompi"]` 的 `paths` + `renames`），于是：

| 开发口 | 发布口 |
|---|---|
| `lompi/<x>` | `<x>`（摊平到根） |
| `.claude/skills/lompi/<x>` | `.claude/skills/lompi/<x>`（原地） |

发布口**根上**还有三样东西开发口里没有对应物，一律当例外：

- `README.md` —— `loment_publish` 自己写的（`spec["readme"]`）；
- `.git` —— 那份缓存 clone 自己的元数据；
- `.claude` —— 属于**另一对**（上表第二行整棵落在那儿），不是"多出来的"。

上面这几条引擎里是**写死的**（引擎是 Loment，引不了 Python 的发布清单），所以有一条判据
从 `loment_publish.REPOS["lompi"]` **推出应有的样子再核引擎源码** —— 清单加了第三棵子树而
引擎没跟上，那条红。

比对用的是**内容**（分块比字节，多大文件都只吃 2 × 8 KiB）。**不**比版本号：lompi 自己报的
`0.1.0` 是个标签，两边永远是它，盯不出任何东西。

## 3. 实测的两条坑（都会静默地把好结果说成坏结果）

### 3.1 `core.autocrlf` —— 一个完全正常的发布口会被报成 170 处差异

**发布口仓库里没有 `.gitattributes`**（开发口那份是给开发口用的，`--push` 只往 lompi 那个库
发 `lompi/` 与 `.claude/skills/lompi/`）。于是 Windows 上按默认设置 clone 出来的工作树**全是
CRLF**，而开发口那边因为 `.gitattributes` 的 `*.lomt text eol=lf` 是 **LF** —— 逐文件比
字节，170 个全不同。实测过：污染后 46/46 行是 CRLF。

所以引擎打出来的那条 `git` 命令**显式**带着 `-c core.autocrlf=false -c core.eol=lf`（更新
已有的缓存那条走 `fetch --depth 1 origin main` + `reset --hard FETCH_HEAD`）—— 老的、按默认
设置拉下来的缓存因此会被修回 LF。这一步**没有自动化的部分**：程序只把命令打给你。

### 3.2 参考实现的 codegen 不能比两个指针（`docs/189` §47）

引擎里判空写成 `(p as u64) == 0` 而不是 `p == (0 as ptr)`：`tools/lomentc.py` 会为两个
`ptr` 比相等发出 `icmp == ptr`（**非法 IR**，自举那份发的是正确的 `icmp eq`）。这条限制仓库里
早就记着，`loment/selfhost/potato.lomt` 也是照它绕的 —— 本文件只是跟着绕。

**为什么这条对本文特别要紧**：`tools/lompicheck_test.py` 是用**仓库里的参考编译器**把引擎
编出来测的，所以引擎每碰一次指针比较，那条判据就整条挂。它第一次跑就是这么发现的。

## 4. 边界（诚实清单）

- **只认 `main`**：两边都比默认分支。feature 分支上的 `lompi/` 不在比对范围里。
- **缓存 clone 是浅的**（`--depth 1`）：只用来读文件，不给你历史。
- **取不到网就退化成"读旧缓存"**，并且**会在输出第二行说明**这份可能是旧的；一份缓存都
  没有、又取不到网才退出 2。
- **要能编 Loment**：`loment build loment/tools/lompicheck.lomt` 一次，之后直接用产物。
  没有工具链就没有这条命令 —— 它是给"有 loment 检出的人"用的。
- **包里没有它**：发行包（`loment_dist`）装的是工具链本身，这条命令不在里面；
  `loment/tools/lompicheck.lomt` 随源码发，谁有检出谁能编。
- **它不修任何东西**：发布口只由 `--push` 写，这条命令只**说**（连那份缓存 clone 也只**打印**
  该跑的 `git` 命令）。

## 5. 判据

`tools/lompicheck_test.py`（登记在 `ci.py` 的 `STATIC_CHECKS`，也在发布清单 `GLOBS` 里）**7/7**：

1. 单元里那条命令声明在（`register` 块，`docs/218` 形态 A）、而且**没有** `_start`；
2. 发布口那个 URL 必须**正好**是发布清单里那一份（`loment_publish.REPOS["lompi"]["repo"]`）；
3. `--print-command` 答出 `lompicheck` —— 启动器就是拿它命名产物的；
4. `--help` 与 `--refresh` 有回应；
5. 未知选项**退出 2**（不是静默忽略）；
6. 用**仓库里的参考编译器**把引擎编出来，拿本仓**真的** `lompi/` 当源侧造一个发布口目录，
   两个方向各跑一遍：一致（并核文件数）/ 改一个 / 删一个 / 多一个（文件与整棵目录各一次）；
7. 默认发布口**从 `argv[0]` 推**（`<家目录>/.lompi/cache/lompi`），期望值按"第一个
   `/Users/` 标记之后那个分隔符"独立算出来。

夹具是**真源码树**拷贝出来的，不是手写的小玩意 —— 手写的不会告诉你"170 个文件里从哪一个
开始不对"。
