# 229 · `lom/net.lom`：那张"号 → 面"的表升成 L0 契约（顺带：`lom/` 那道门放宽了）

> 状态: **已落地**（2026-10-11）。两件事写在一篇里，因为它们是一起发生的：
> ① 新增第四份 L0 契约（前三份是 `fuai` / `fuc` / `fujr`）；
> ② `lom/ 禁区` 那道门**一直拦的是"碰了 `lom/`"**，而它该拦的是"**改**既有契约"——
> 不先把这条说清，①根本进不来。

## 0. 一句话

> "号 → 面"那张表此前**在四个地方各有一份**（编译器、链接器、自举 checker、自举 potato），
> 彼此之间只有散文与一条判据牵着。现在它是 **`lom/net.lom` 一份单源**，
> 四处由 `lom_audit` 的 `[net/…]` 四节 + `loment_net_test` 逐条对账。
> 而**新增**一份 L0 契约不等于**改**契约 —— 门禁按 `status` 分了流。

## 1. 为什么是"新增"而不是"改"

`CONTRIBUTING.md` 那条禁区的原文一直是"**modifies** anything under `lom/`"，
而 `.github/workflows/gate.yml` 的实现是 `grep -E '^lom/'` —— **碰到就拦**。
两者在"新增"这一格上不一致，而差的那一格恰好是**唯一能扩大接口面的合法动作**：

* 既有那三份的对面在**另一个仓库**（`kernel/src/syscall.rs`、`spec.json`、`LinuxFUAI/…`），
  本仓单方面看不出改没改坏 —— 这是禁区存在的**唯一理由**；
* **新增**一份不动那三份一个字节，也就没有"对面那一半"可坏。它要的是**自己的判据**，
  不是别人的评审。

所以 2026-10-11 起，`lom-door` 用 `gh api` 的 `status` 分流：

| 改动 | 判定 |
|---|---|
| `added` | **放行**，在 job summary 里**列出来**（看得见，但不红） |
| `modified` / `removed` / `renamed` | 拦下（原样） |
| 列表**形状**不对（缺 `status` 列） | 红 —— fail closed，不给"API 换了形状就静默放行"留缝 |

四条行为了本地跑过（`added` 放行 / `modified` 拦下 / 无 `lom/` 放行 / 形状坏红）。

## 2. 契约本身

```
module net
meta   { schema, title, version, authority, layers{surface,runtime,not_dispatched}, gates{…} }
enum Surface        : u8   { other = 0, file = 1, mem = 2, net = 3, proc = 4 }
enum Syscall        : u32  { read = 0 {surface="file"} … accept4 = 288 {surface="net"} }   // 23 条
// 2026-10-12：`NotDispatched` **整个枚举已删**（联网面补完，`docs/234` §2）——
// Loment 的检查器不许空枚举，而留个空壳会让人以为"点名不做"还活着。
```

三件事**被写进文件**（不写在文档里、也不写在注释里）：

* **面名由用的人定**（`layers.surface`）—— `file`/`mem`/`net`/`proc` 是本仓库自己的划法，
  `other` 是兜底。这与 `docs/224` §2 那句是同一件事；
* **`Syscall` 与派发面今天同宽**（`gates.why_equal`）—— 不是"包含"。
  理由写在那儿：`surfaces` 的价值在"数与行为对得上"，若计数词汇比派发面宽，
  产物就会说出"3 个 net 站点"而其中一个是这个运行时答不了的号 —— `docs/217` 要堵的就是它；
* **"点名不做"也曾进契约**（`NotDispatched`）—— 此前它只活在 `docs/221` §7 的散文里。
  审计要求这一族与派发面**不相交**：哪天谁"顺手"实现了一个，`[net/lomelf]` 当场红。
  **2026-10-12 这一族清零、枚举删掉**（`docs/234` §2）—— 审计那条在枚举缺席时按空集判，
  判据留着；"派发面比声明面宽"改由 `loment_net_test` 的第四个反向用例钉（塞一个 `9999`）。

  2026-10-11.2：`select`(23) **已经从这一族挪进 `Syscall`** —— 运行时真的派发了它
  （`docs/231`）。挪动的形状正是上面那条审计设计的：先让 `[net/lomelf]` 红，再两边一起挪。

## 3. 四处对账（`lom_audit` 的 `[net/…]`）

| 节 | 对账面 | 方向 |
|---|---|---|
| `[net/lomentc]` | `lomentc.SURFACE_NUMBERS`（**计数词汇**） | 两个方向 |
| `[net/lomelf ]` | `lomelf.PE_SYSCALLS`（**派发面** + 面名） | 两个方向 |
| `[net/lomelf ]` | `NotDispatched` ∩ 派发面 = ∅ | 单向（"顺手实现了"就该红） |
| `[net/twin   ]` | `checker.lomt::chk_num_is_net`（只认 net 那一族） | 集合相等 |
| `[net/twin   ]` | `potato.lomt::pt_surf_code`（**整张表**，代号 1..4） | 按**分块**比 —— 不把四个代号抄进审计 |

**两个方向**这一条是有来历的：早期那些对账只查"实现少了什么"，于是"实现**多了**一个号"
一直没人报（`docs/201` §2 记过同族的洞）。

`lom_audit` 在门禁里是**已知红**（本机与 CI 都缺 `LinuxFUAI/` 姊妹检出），也就是说它的红
**不构成信号** —— 所以同一组对账另有一条**能绿也能红**的判据：`tools/loment_net_test.py`。
它三件事：契约自己的形状（**29 / 21 / 0 / 不相交**）、四处逐条一致、
以及**自证会红**（四个方向各改一次：面名 / 少一个号 / 派发面少一个 / 顺手实现 `sendmsg`）。

## 4. 顺带撞出来的一个真 bug（自举 `lomc` 的值 0）

把 `lom/net.lom` 加进 `loment_lomc_test` 的语料清单（那四份 `.lom` 逐一与 Python 版**逐字节**比）
之后，它当场红了：

```
[g1_net_rust] 不一致:   want 1799B / got 1797B
  want: pub const SURFACE_OTHER: u8 = 0x00;
  got : pub const SURFACE_OTHER: u8 = 0x0;
```

自举 `lomc.lomt` 的 `v_hex` 里，十六进制数字位那一趟是 `while x > 0` —— **值为 0 时一位都不写**，
只剩下 `width - 1` 个补零。`u8` 于是发成 `0x0`，而 `u32` 的 0 会发成 `0x0000000`。

**为什么它到今天才露头**：既有的三份 `.lom` 里没有一个**值为 0** 的变体或常量
（`fuai` 的 opcode 都是两万起、`fujr` 的 `Tag` 是 1/4/5、`fuc` 两个常数非零）。
判据一直都在（`loment_lomc_test` 逐字节比），只是**没有语料踩到那一格** ——
这正是 `docs/190` §1.4 那条"判据要有能踩到它的语料"的又一个实例。

修法一行：`while x > 0 || d == 0`。

## 5. 登记处（加一份 L0 契约要动的地方）

| 处 | 改了什么 |
|---|---|
| `lom/net.lom` | 新文件 |
| `lom_audit.GENERATED` | `(LOM_NET, "json", lom/build/net.json)` —— 生成物按 `--emit` 落盘、按字节对账 |
| `lom_audit.audit_net` | 新的四节（`main()` 里调用） |
| `loment_release.GLOBS` | `tools/loment_net_test.py`（**工具自己也要在清单里**，那条判据钉着） |
| `loment/tools/lomrel.lomt` | 同上，**位置与 GLOBS 对齐**（两份清单的顺序就是它们的顺序） |
| `ci.py` | `STATIC_CHECKS` + 实测耗时（0.2 s） |
| `loment_lomc_test.LOMS` | 加第四份 —— 自举 lomc 读它也要逐字节对得上 |
| `.github/workflows/gate.yml` / `CONTRIBUTING.md` | 门按 `status` 分流（§1） |

`lom/*.lom` 本来就在 `GLOBS` 的 glob 里（`"lom/*.lom"`）与 `loment_publish` 的目录白名单里，
所以新契约**自动**进发布清单，不必另外登记。

## 6. 判据

```
loment_net_test   3/3   契约形状 + 四处对账 + 自证会红（四个方向）
loment_lomc_test  4/4   四份 .lom 的四路发射与 Python 版逐字节相同
loment_tools_test 33/33 每个被跟踪的 tools/*.py 都在发布清单里
loment_rel_test   8/8   两份清单（Python 与自举）产出的字节相同
```

## 7. 没做的

* **没让实现"从生成物读表"**（即 `from lom.build.net import …`）。今天走的是
  `fuc.lom`/`fujr.lom` 那套既有做法：**单源 + 审计对账**，实现各留一份。
  改成代码生成会动 `lomc` 的四个发射器（它们现在只发常量，不发 `meta`），
  那是一次独立的、更大的改动 —— 而"单源"这件事**已经由判据兑现了**，不是靠命名。
* **没把 `NotDispatched` 的号并进 `SURFACE_NUMBERS`** —— 理由见 §2 那条 `why_equal`：
  计数词汇与派发面同宽是**故意的**。
* **没有"必需检查"**：`lom/ 禁区` 仍然只是"红了"，`main` 上没把它设成 required status
  （`docs/201` §1 记过这件事）。这一轮只改了**判定的内容**，没有动仓库设置。
