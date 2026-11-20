# 213 · 门禁：20 分钟砍到 N 片

> 状态: **已实现**（2026-10-09）· 入口: `python tools/ci.py --static-only`（本机一条路、
> CI 上 N 片并行）
> 一句话: **整道门禁的墙钟被一条判据里的一步钉死（954s / 20 分钟里的 16 分钟），而那一步
> 是同一件事的第二遍 —— 拿掉它，再按实测耗时切片。**

`docs/181` 是上一集：串行 25+ 分钟 → `-j` 并行 3 分钟。它结尾留了三条"下一步"，其中
第二条是**去掉重复的自举链**，并且写着"**在没量之前，别在这三条里选**"。这篇就是量完之后
的结论 —— 它选的是那一条。

## 1. 先量

那一轮 CI（run 36209626967，2026-09-26，`main@8ebef4e`）的 job 级与判据级实测：

```
装工具链      95s
跑静态门禁  1107s        （下面这张表之和 = 2036s，`-j 4`）
job 合计   20m10s
```

64 条里**八条吃掉 96%**：

| 判据 | 秒 | 占比 |
|---|---|---|
| `loment_dist_test` | **954.5** | 46.9% |
| `loment_seed_test` | 243.8 | 12.0% |
| `loment_p8_test` | 229.2 | 11.3% |
| `loment_ctrans_test` | 168.9 | 8.3% |
| `loment_trans_test` | 136.6 | 6.7% |
| `loment_lompi_test` | 119.8 | 5.9% |
| `loment_cli_test` | 50.8 | 2.5% |
| `loment_potato_emit_test` | 50.0 | 2.5% |
| 其余 56 条 | ~82 | 4% |

两个数一起看才说明问题：**墙钟 1107s ≈ 最慢一条 954s**。并行早就榨干了 ——
其余 63 条全都跑在它的阴影里。所以"门禁太慢"这件事当时**不是调度问题**，
是那一条本身的问题。

## 2. 瓶颈在"一条判据里的**一步**"

把 `loment_dist_test` 拆开量（本机 Windows，`--emit` 那段逐工具计时）：

| 段 | 本机 | 说明 |
|---|---|---|
| 前置五条（布局/归档/新鲜度/skill/文档样例） | 3s | 与速度无关 |
| `build_stage1`（种子 → stage1） | 0.9s | 纯 Python `lomelf` 链一次 |
| `build_tools` 里 **`emit_ir(loment-driver)`** | **273.1s** | **`build_tools` 的 76%** |
| `build_tools` 其余 8 个工具 | 88s | lsp 27 / lompi 17 / lomenterr 15 / … |
| 两个平台各链一遍（9 工具 × 2） | 3.8s | 3.2MB 的 IR 也只要 0.8s |
| `test_install_sh`（WSL/Linux 上装+跑） | 5.7s | |

那 273s 是**自举编译器编译它自己**（`loment/selfhost/driver.lomt`）。对比：

```
Python 参考实现 lomentc 发射 driver.lomt   2.5s   → 3.21MB IR
自举 stage1        编译 driver.lomt     273.1s   → 3.21MB IR   （慢 110 倍）
```

**为什么差 110 倍**：`loment_dist.build_stage1` 用**纯 Python 的 `lomelf`** 把种子链成
stage1，而 `lomelf` 是"栈机、不做寄存器分配"的后端（见 `tools/lomelf.py` 开头那串 v0
取舍）。那个 stage1 跑起来比参考实现慢两个数量级。

CI 上按 **2.55×** 的实测倍率折算（本机 `loment_dist_test` 374s ↔ CI 954s）：
`emit_ir(loment-driver)` ≈ **700s**，正好是那 954s 的四分之三。

## 3. 那一步是**重复劳动** —— 这才是关键

`loment/selfhost/driver.lomt` 的 IR 有两条独立判据在守，而它**已经作为种子提交在仓里**
（`loment/build/selfhost_driver.ll`，docs/159）：

* `loment_seed_test`：**种子 == 参考实现为 driver.lomt 发射的 IR**（`loment_seed.check()`，
  本机 3.1s）；
* `loment/bootstrap.sh` 第 2 步：**stage1 编译 driver.lomt == 种子**（定点本身）。

而 `bootstrap.sh` 在 CI 上**走的是 clang 那支**：`loment/build/genesis/lomelf-linux-x64.elf`
的 git mode 是 **100644（没有可执行位）**，`[ -x "$genesis" ]` 过不了。clang 编出来的
stage1 是**优化过的**，所以那边同样的活便宜得多 —— `loment_seed_test` 整条才 244s（含
两遍定点 + 一遍非自身入口），而这恰恰说明`loment_dist_test` 那 700s 买到的**不是覆盖，
是同一件事的第二遍**。

## 4. 改了什么

1. **`loment_dist.py`: `SEED_TOOL`** —— `build_tools` 里 `loment-driver` 的 IR 直接取
   `SEED`，不再用 stage1 重编。`--only driver` 因此连 stage1 都不必造（秒级）。
   包里那个 `bin/loment-driver` 于是**就是种子链出来的** —— 语义与原来一致（定点保证
   两者逐字节相同），只是不再为它等 700s。
2. **`loment_dist.py`: `build_tools` 并发** —— 其余 8 个入口互相独立（各写各的
   `STAGE/<name>.ll`），用 `ThreadPoolExecutor` 并行跑。收益全在 `emit_ir` 那些**子进程**
   上；链接是纯 Python、GIL 下不并行，但它总共才几秒。落盘仍按 `TOOLS` 顺序 ——
   **发布清单的顺序是判据**（`loment_publish` 那条钉的就是"两处清单同集合不同顺序"）。
3. **`loment_dist_test.py`: 补一条便宜的自我佐证** —— 见第 6 节。
4. **`ci.py`: `--shard K/N`** —— 把 `STATIC_CHECKS` 切成 N 片，**第 0 片是独占的**
   （`EXCLUSIVE_STATIC`），**`ISOLATED_STATIC` 那两条长尾各占一片**，其余按实测耗时贪心
   均分。划分是**算出来的**、并且当场断言"并集恰好等于 `STATIC_CHECKS`、互不相交" ——
   手写一张名单的话，新增一条判据就会静默落在所有片之外，那条判据从此不跑而门禁照样绿。
5. **`gate.yml`: 矩阵 + 聚合** —— 各片一个 runner（`fail-fast: false`，一片红不许砍掉
   别的片）；`static` 那个 job **名字不变**（保护规则挂的就是它），负责把 N 份 `gate.out`
   合成一份再对基线判定，并显式把"分片红了"转成自己红（`needs` 失败默认是**跳过**，
   而跳过不算失败 —— 保护规则会永远等下去）。

   **独占那一片是单独一个 job，其余五片 `needs:` 它**。这不是排版：`EXCLUSIVE_STATIC`
   的成员写**仓库里的共享位置**，"不能与别的判据同时跑"这句话在摊成并行 job 之后
   **"同时"还包括别的 job** —— 分片本身不给这个保证（`ci.py:shards` 把这一片排在片 1，
   `needs:` 才把它与其余分片串起来，两件事缺一不可）。代价是那一片的墙钟（CI 上约 8s）
   加在其余分片前面。

   （第一轮还有一档"每片按档位只装自己需要的工具链"的机制 —— 2026-10-10 随工具链做成
   镜像**一并删掉**了：各片现在是同一份镜像，没有"这一片只装什么"可言。见 §8。）

## 5. 实测

**本机**（Windows，`--only-static loment_dist_test`）：

| | 改前 | 改后 |
|---|---|---|
| `loment_dist.py --emit --no-exe` | 361.3s | **34.1s** |
| `loment_dist_test` | ~374s | **63.9s** |

改后 `loment_dist_test` 里最大的一块变成其余 8 个工具的并发编译。

**CI**：`20m10s → 4m24s`（PR #175，run 38009511071，**6 片**）。中间那一轮 5 片是 5m15s。

```
                        6 片这一轮         （5 片那一轮）
workflow 墙钟             4m24s              5m15s
-------------------------------------------------------
片 1/6 [none]               8s                 9s    lompi_sync（CI 上空转）
片 2/6 [llvm]             164s               180s    loment_p8_test 156s
片 3/6 [llvm]             200s               202s    loment_seed_test 172s
片 4/6 [full]             **251s**           301s    最重
片 5/6 [full]             240s               201s
片 6/6 [full]             175s                —
聚合                        8s                 9s
```

三条值得记的：

* 两条长尾**比单独跑时还快**（seed 244→172s、p8 229→156s）—— 独占一个安静的 4 vCPU
  runner 之后，没有别的判据跟它抢了。65 条的和从 2036s 掉到 932s。
* 5 片那一轮里最重的是"均分"出来的那一片（301s = 装工具链 95s + 30 条判据的和 294s），
  而长尾那两片只要 180s / 202s。所以切成 6 片：**最重片从 301s 落到 251s**。
* 但 6 片那一轮最重的**还是**均分出来的那一片 —— 它的 251s 里 95s 是装工具链（`full`
  档），剩下 156s 才是判据。也就是说这一片的墙钟已经被"装工具链"占掉三分之一强，
  再对半切判据只会更快地撞到那个 95s 的地板上（见 §7）。

**第三轮（2026-10-10，容器）**：各片改用 `container: ghcr.io/fujojtop/loment-ci:3`
（§8）。**第一版把六片全都放了进去，结果总墙钟 5m38s —— 比换之前还慢**：

```
独占片（含拉镜像）        62s     ← 换之前是 8s
分片 4/6                 139s
分片 5/6                 169s
分片 6/6                 228s
分片 3/6                 236s
分片 2/6                 259s
聚合                      10s
```

两笔账：**拉镜像要 ~55s**（不是我事前估的二十来秒），而把 apt 那 95s 换成它，各片只省下
四十来秒；更要命的是**独占那一片串在其余五片前面**（`needs:`，那是独占保证要的），
它那 62s 于是变成**整道门禁的前置等待** —— 一笔就把各片省下的全吃掉还倒亏。

**把独占那一片挪回 runner**（它一条外部命令都不碰）之后（第四轮，同一个 PR）：

```
                 第四轮             （第三轮）
lom/ 禁区           3s                 5s
独占片              8s                62s     ← 回 runner
分片 4/6          138s               139s
分片 5/6          157s               169s
分片 6/6          157s               228s
分片 3/6          238s               236s
分片 2/6          200s               259s
聚合               10s                10s
------------------------------------------------
**整道门禁**      **4m21s**          5m38s
```

**4m21s**：从最初的 20m10s 到这里是 **4.6×**。剩下最重的是 seed 那一片（238s = 拉镜像
~55s + 判据 ~180s），也就是 §7 说的那块地板。

## 6. 覆盖没有少，只是换了个地方付账

`loment_dist_test` 现在**不重编 driver**，所以"包里那个 driver 确实等于从 driver.lomt
编出来的东西"这句话**在该判据内部**就少了一个前提。补法不是"信任兄弟判据"，
而是把那个前提**就地钉住**：

```python
def test_driver_seed_matches_reference() -> None:
    """`SEED` 必须**就是**参考实现为 driver.lomt 发射的 IR。"""
```

它跑 `loment_seed.check()`（本机 3.1s，换掉的是原来的 273s）。于是判据自己就能推出
"归档里的 driver == 参考实现的产物"，而"stage1 自编 == 种子"那条定点仍由
`loment_seed_test` 的 `bootstrap` 守着（它一直在门禁里）。

**别把这两条判据拆到不同地方去**：它们是一对，一条没了另一条就变成"把可能过期的东西
藏起来"（与 `loment_seed_test` 里 `test_seed_is_marked_generated` 和
`test_seed_matches_reference` 的关系同一个形状）。

## 7. 地板在哪（下一步别再往这儿使劲）

分片之后墙钟 ≈ **最重那一片**，不是"总和 ÷ N"。两轮之后剩下两块地板：

* `loment_seed_test`（实测 172s）**整条没法再切**：它的两遍定点是**先后依赖**的
  （stage2 来自 stage1 的输出）。它自己一片 = 172s + 起步那一段，实测 200s ——
  这一片**已经贴着地板**（§8 之后"装 clang"那 35s 也换成了拉镜像）。
* `loment_p8_test`（156s）同理，稍矮（164s）。
* 均分出来的那几片，地板本来是**装工具链那 95s**（apt 55s + LLVM 19 35s）：
  6 片那一轮最重的片 251s = 95s 装 + 156s 判据 —— 判据那一半再切只会更快撞上 95s。
  **那 95s 已经不在门禁里了**（§8），剩下的只是拉镜像那一段。

所以"再快"只剩两条路，都**还没做**：

1. **把这两条长尾本身弄快**（它们的对照组是 clang，慢在自举那两遍定点）—— 直接抬
   172s 那块天花板；
2. 这两条判据能不能共用一次自举（同 §1，得先量）。

**加片号已经没用了**：再切只会让更多片各自付"起步"那一段（现在主要是拉镜像），
墙钟不动。

还有一条**已修**的观察（分片搬上来时先记在这里，按"别在搬家时顺手改判据"那条边界留到
单独一个 PR）：分片把"哪些判据会同时跑"这件事换了，于是**并发窗口变密**。6 片那一轮实测到
一次 `loment_rel_test` 在片 6 里 **5/6**、单跑 **6/6** —— 按既有机制判为并行假红
（`flaked.txt`），门禁结论不受影响，但每出现一次就多花一次单跑，且是噪音。

**根因不在 `loment_rel_test` 自己**（原先猜的两个固定路径确实也不合规，一起改了，见下），
而在**同片的 `loment_gc_surface_test`**：`loment_gc_surface._ladder_of` 把注入后的源写在
**源同目录**（`loment/selfhost|lib|examples/`，三个都是 `*.lomt` 的 GLOB 目录），名字取
`.tmp-gcsurface-<名字>.lomt`。它赌的是"**点开头的名字 glob 匹配不到**" —— 那是 **shell 与
`glob` 模块**的规矩，**`pathlib.Path.glob` 不遵守**（按 `fnmatch` 比，`*.lomt` 照样命中），
而 `loment_release.build()` 走的正是 `Path.glob`。于是窗口里那一份被
`loment_rel_test::test_manifest_order_is_platform_independent` 数进了
`loment/selfhost/*.lomt` 那一组，报"这一组在清单里的次序不是字节序"。

**同一根因有第二个出口**，只堵一处门禁照旧偶发红：把修好的树放回复现里，第 1 次迭代就换成
`loment_tools_test::test_m55_fmt_idempotent_and_semantics` 报同一条红 —— 它把格式化后的副本
写成源同目录的 `._fmt_<名字>.lomt`（`loment/examples/` 也是 `*.lomt` 的 GLOB 目录），
赌的是同一句"点开头的名字 glob 匹配不到"。**教训**：这类"临时文件藏在 GLOB 目录里"要按
**写法**扫（`with_name(` / `path.parent /`），不能按现象扫 —— 扫出来的两个文件前缀
（`.tmp-` 与 `._`）毫无共同点。

**改法**：① `_ladder_of` 与 `test_m55` 的副本都改写到**本进程私有的临时目录**（那段源根本
不进仓库；它不必是同级文件 —— 名字形式的 `use` 按 `NAME_ROOTS` 解析，没有一层是"入口文件
旁边"，路径形式先试 `root`）；② `loment_rel_test` 那两条写盘的判据改喂进程私有路径：
`--checksums` 本来就收路径，`--emit` 现在也收一条**可选**路径（两侧逐字对齐，不给还是仓库
那条），于是"真落盘 + 比字节"一条没少，而共享清单一个字节都不碰。默认路径那件事由
`test_emit_default_path_is_the_same_on_both_sides` 就地钉住（它不需要 WSL，在 ubuntu 上真跑）。

**怎么验**：`loment_rel_test` 在**无 WSL** 的档位上循环 500 次，同时压着同片的
`loment_gc_surface_test` / `loment_tools_test` / `loment_dist_test` / `loment_lompi_test`
（写 GLOB 目录的两处都在里面）——改前第 1 次就红，改后 0 次。（无 WSL 这一档与 CI 一致：
那三条要 WSL 的判据在 ubuntu 上全 SKIP，所以 CI 上跑的正是这几条。）

还有一条与"快"相反、但必须一起记的（**这一条现在只在镜像构建里还成立**，见 §8）：
装工具链那次实测到过 `apt-get` 在镜像上卡住 **10 分钟**（正常 95s）。当时每个分片各装
一遍，于是这种抽风有 N 次机会 —— 那两条步骤当时加了 `timeout-minutes: 10` 与
`Acquire::Retries=3`：**卡住就快点失败、外层重跑**，而不是让 job 那个 60 分钟的上限
兜着（什么都没在跑而 PR 不动，正是"门禁太慢"最坏的形状）。§8 之后 apt 只剩镜像构建
那一条路，那里的 60 分钟上限是可接受的：它不占任何 PR 的墙钟。

## 8. 工具链做成镜像（2026-10-10，第二轮）

**它解决的问题**：那 95s 是"同一份东西装 N 遍"。做成镜像之后，它只在**镜像构建**时付
一次（且只在 `Dockerfile` 改动时），各分片只剩**拉镜像**。顺带把 apt 抽风的暴露面挪出
了 PR 的墙钟 —— 抽风那次发生在构建里。

* 镜像：`.github/ci/Dockerfile`（ubuntu 24.04 + 与原来 `gate.yml` **逐字对应**的那组
  apt + LLVM 19 + `gh`/`python3`/`git`/`make`/`gdb`），由 `.github/workflows/ci-image.yml`
  构建并推到 `ghcr.io/fujojtop/loment-ci`。
* **包名单不许随手增删**：少一个包 = 一条假红，多一个包是白等的下载。依据是逐条扫判据
  （`grep -rn 'which("' tools/*.py`）与原 `gate.yml` 那两条命令的对照。
* **故意不加** `dotnet` / `nvim`：runner 上本来就没有，那几条判据靠"缺"来 SKIP，而
  **那些 SKIP 已经写进基线**（`loment_multisyntax_projects_test` 那条"runner 上 2/3"
  就是它）。加进来是改覆盖面，不是改进。（`rustc`/`cargo` 一开始被错放进这份"不加"里，
  见下面第 4 条 —— runner 上**一直有**。）
* **拉镜像要 ~55s，所以它绝不能串在关键路径前面**：2026-10-10 实测（§5 第三轮）。这一条
  决定了 `static-exclusive` **不拉镜像** —— 它只有 `lompi_sync`，一条外部命令都不碰，
  而它因为 `needs:` 串在其余五片前面：给它一个容器就是把 62s 变成整道门禁的前置等待。
  各分片自己拉那 55s 是**并行**付的（墙钟上算一次），那才是划算的那一半。
* **"档位"这套机制随之作废**：各片现在是同一份镜像，没有"这一片只装什么"可言 ——
  `ci.py` 的 `shard_profile` / `--shard-profile`、gate.yml 里的 `profile` 与"档位自检"
  一起删掉了。**留着的只有 `ISOLATED_STATIC`**，但它的理由换了：不再是"那一片的工具链
  可以压到最小"，而是**争用**（独占之后 seed 244→172s、p8 229→156s，§5）。
* **两处必须一致**：gate.yml 各 job 的 `container.image` 与 `ci-image.yml` 的 `TAG`。
  改镜像要同时抬这两处 —— 抬 tag 是让各 job 用上新镜像的**唯一**动作。
* **可见性**：ghcr 的包默认**私有**，而**外部贡献者从 fork 提的 PR 拿不到私有包** ⇒
  它们的门禁会在拉镜像那一步就死。所以包必须是 **public**（一次性仓库设置）。
* **容器 ≠ runner，差的那些东西只能靠"跑一次"找出来**。换上去第一次跑红了 6 条（14 条红里
  其余是基线里本来就有的 + 一条已知的争用假红），两条根因：

  1. **git 的 "dubious ownership"**（5 条）：工作区是 runner 的用户 checkout 的，而容器里
     是 root ⇒ git 拒绝 `ls-files` / `rev-parse` / `check-attr` / `check-ignore`，而好几条
     判据正靠这几个子命令（`loment_eol` / `loment_src` / `loment_publish` /
     `loment_sign_test` / `loment_tools_test` / `loment_seed_test` 的 gitattributes 那条）。
     **修在调用侧**（`gate.yml` 用 git 的环境变量入口注入一次 `safe.directory`，值取工作区），
     不在镜像里 —— 那是环境的用法，不是工具链的组成。
  2. **`python` 这个命令**（1 条）：runner 镜像上有 `python`（指向 3），ubuntu 24.04 只有
     `python3`；而 `vscode_ext_test` 的 `_python()` 是
     `shutil.which("python") or "python"` —— **找不到就退回裸名**。修在镜像里
     （`python-is-python3`），并把 tag 抬到 `2`。
  3. **`run:` 的默认 shell 在容器里是 `sh`（dash），在 runner 上是 bash**（这一条不红，
     是**静默**的）：`static-manual` 里 `read -ra parts <<< "..."` 直接
     `Syntax error: redirection unexpected`。各分片那几条恰好只用了 POSIX 写法所以没炸
     —— **那是运气**。修法是在 workflow 级声明 `defaults.run.shell: bash`。
  4. **`rustc`/`cargo`（2 条）** —— 而这一条**我把判断做错了**：v1 的注释写着"不加
     rustc/dotnet/nvim，runner 上本来就没有"，依据是"原 `gate.yml` 那两条 apt 命令里
     没有 rustc"。但那份 apt 命令**只是补差**，runner 镜像自带的东西它一个字都不提。
     查旧 runner 的自述才看清 `rustc  /home/runner/.cargo/bin/rustc` —— **一直有**
     （rustup 装的）。于是 `loment_tools_test` 的 `test_m61/m62`（**裸调** `rustc`，没有
     which 守卫）在容器里 `FileNotFoundError`。加 `rustc cargo`（apt 版），并把那条"不加"
     的纪律收窄到 `dotnet`/`nvim`（那两个才是真没有）。
     **顺带**：镜像末尾那条自述 `RUN` 加了 `set -e` + 一串 `--version`，于是**删掉一个包
     会在构建时失败**，而不是变成门禁上一条看着像判据坏了的假红。

  还有一条**顺带看见、不属于容器**的：`safe.directory` 的值别写 `${{ github.workspace }}`
  —— 那是**宿主**路径（`/home/runner/work/loment/loment`），而容器里同一棵树在
  `/__w/loment/loment`（git 自己的提示原文打的就是后者）。**路径写死会静默打不中**，
  于是"看着配上了、其实没配上"，白跑一轮才看得出来。取 `*`。

  **教训**：包名单"照抄原来那两条命令"是不够的 —— 原来那些判据跑在 **runner 镜像**上，
  而 runner 镜像自带的东西（`python` / `gh` / git 的配置行为 / **默认 shell**）在
  `gate.yml` 里**一个字都没写**。能找出来的办法只有一次真跑 + 逐条看红。


## 9. 怎么用

```bash
python tools/ci.py --shard-plan 10               # 打印 10 片的分法（片号 + 条数 + 名字）
python tools/ci.py --static-only --shard 3/10    # 只跑第 3 片
python tools/ci.py --static-only                 # 本机整道（不分片，老行为）
python tools/loment_release.py --emit            # 生成物两件 —— 改了 GLOBS 覆盖的文件就要跑
python tools/loment_release.py --checksums loment/build/SHA256SUMS
```

`gate.yml` 的矩阵里那九行（片号、总数）是 `--shard-plan 10` 的**副本**（**不含片 1** ——
那是独占那个 job）。**改分片形状要同时改 `ci.py` 和这里**；走样的后果由聚合那一步兜住：
它会把各片按**判据名**并一遍，缺失或多余的判据都红（`#443` 之后不再数行数）。

片数的下限也是 `shards()` 守着的：`n` 太小时**直接断言失败**（独占 1 条 + 长尾 3 条已经
占了 4 片，剩下那 80 多条还得有地方放），不会"分不开就合一片" —— 合掉之后
`--shard K/N` 取到的就不是调用方以为的那一片了。

## 10. 2026-10-18 重量：`loment_elf_test` 与 10 片（`#435`）

**症状**：run 38953249045（PR #404, `f498c5f8`）的墙钟是 30.1 分钟，全部落在 6/6；
其余五片 5.1 / 5.0 / 3.5 / 2.2 / 0.1 分钟。门禁的墙钟 = 最慢的那一片。

**成因**：`ci.py:SHARD_COST` 是对"树"的一个断言，而**没有任何东西检查它**。
`loment_elf_test` 在表里没有条目 —— 于是按 `SHARD_DEFAULT_S = 8` 秒算。它实际上
一条判据编一个 ELF 再执行，实测 **245 / 387 / 617 秒**（三次 run）。不是它变慢了，
是它过去**没在 runner 上真跑**（`#382` / `#404` 之后才跑起来），表一直没重量。

**量法**（可复现）：

1. 取三次**成功**门禁 run 的 artifact（`gate-log-<K>/gate.out`）：
   `39162110108` / `39163005969` / `39159523472`（2026-10-18）；
2. 每行 `:: [static] <套件> PASS <秒>s` 取那个秒数；
3. 同一个套件在三份里的**中位数**就是表里的数（89 条，没有缺失）。

这些数是在片内 `-j 4` 并行下量的，**本身就含同片争用** —— 拿它均分是对的，
拿它当"这条判据单独要多久"是错的。

**改了什么**：

* `SHARD_COST` 覆盖 **`STATIC_CHECKS` 全表**（89 条），`unpriced_checks()` 钉住覆盖；
  新加判据没有条目时 `shards()` 当场断言失败 —— 不会再静默按 8 秒算。
  确实量不出的可以放进 `SHARD_COST_EXEMPT`（今天为空），那是**登记过的选择**。
* `GATE_SHARDS` 6 → **10**：按中位数贪心均分，6 片时两个"均分"片各背 **~1180s**；
  10 片时六个均分片的预测和都是 **~394s**，与三条长尾（`loment_p8_test` 173.6 /
  `loment_channel_test` 92.8 / `loment_seed_test` 63.9）同一量级。再往上加片没有收益：
  墙钟的下限变成 `loment_elf_test` 自己（~387s）。
* 判据 `loment_tools_test::test_every_static_check_has_a_measured_shard_cost` 钉住
  "每个名字要么有实测条目、要么明确豁免"；它还做一次反向自检（把 `loment_elf_test`
  从表里拿掉，`unpriced_checks()` 必须点名它）。

**没做的**：没有把 `loment_elf_test` 挪进 `ISOLATED_STATIC`。10 片的分法里它恰好
独占一个均分片（其余 6 条加起来 ~7s），加进名单只会让"长尾"的定义变模糊；
哪天它的实测再涨到与 `loment_p8_test` 同量级（~600s 变成常态），再移。
