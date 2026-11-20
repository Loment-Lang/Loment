# 224 · Potato v12 `surfaces`：把机调用站点按"面"分一分

> 状态: **已落地**（2026-10-10）。这是联网那条线的**声明侧收尾** ——
> `docs/217`/`docs/221` 补的是"PE 上跑得起来"，本文补的是"产物里**数得出来**"。
> 两个实现（`tools/lomentc.py` 与 `loment/selfhost/potato.lomt`）、两条校验器
> （`tools/potato.py` 与 `loment/tools/lompotato.lomt`）各自落地，逐条对齐。

## 0. 一句话

> v10 的 `boundary.syscalls` 只答"有几个越界点"，**不答"越到哪一层去了"**。
> v12 加一个必填对象 `surfaces`：`面名 → 站点数`，外加保留键 `total_sites`。
> 而它比前几个计数器多一条**跨字段**的自洽 ——
> **`surfaces.total_sites` 必须等于 `boundary.syscalls`**：每个机调用站点落在**恰好一个**
> 面里，所以两处数的是**同一批站点**。校验器读不到源码，但判得了这两处打架。

## 1. 为什么要一个新的计数器（而不是复用 `boundary.syscalls`）

`boundary`（v7，`docs/205` R5）把"越过语言保证的每一步"数成一堆**同质的**数：
`extern_declared` / `extern_calls` / `syscalls` / `ptr_transforms`。

可 `syscalls` 内部从来不是同质的 —— 落进 `file` 与落进 `net` 在**审计、准入、审查**
那里完全不是一回事（`docs/218` 整篇讲的就是"联网为什么该单独设一道"）。一个只报总数的
产物，读者没法据此说"这个单元联不联网"，只能自己回去 grep 源码 —— 而
**"不读源码可判"**（R5 的原话）正是这些字段存在的全部理由。

所以 v12 分的是**同一批站点**，不是新增一类站点。这也解释了那条交叉不变量为什么必须在：
两个数如果是两个口径数出来的，它们迟早会各说各话。

## 2. 形状

```text
v12 = v11 + **必填**对象 `surfaces`
      { 面名: 站点数, …, total_sites: 总数 }
```

> **为什么是 `v12` 而不是 `v11`**：这一项与 `port`（`docs/222` §4）**同一天各自占用了
> `v11`**，而两条线互不知情 —— 两篇设计文档当时也各自编号 222。
> `port` 先并进 main，于是它**占住 v11**，`surfaces` 顺延到 `v12`。
> 台阶是**只往上加**的：先到的那一版不会被后来的重编号（那样会让已经发出去的、
> 写着 `"potato": "v11"` 的对象全变非法）。现在两者的字段同在 v12 的对象里，
> 累积规则照旧 —— 一份 v12 对象要**同时**满足两边每一条。
> 本文的文件号也因此从 222 挪到 **224**（222 那天被
> `docs/222-lompi-020-foreign-libs.md` 与 `docs/222-lompicheck.md` 用掉了 ——
> 不止两条线在写文档）。

* **必填**（与 `boundary` / `gc_ladder` / `runtime` / `mode` … 同一条纪律）——
  "可 grep、可计数、可审计"的东西不设"可选的形态"。零站点写 `{"total_sites": 0}`。
* **面名由用的人定**：`file` / `mem` / `net` / `proc` / `other` 只是**本仓库自己的划法**，
  校验器不认名字、只认形状（标识符 + 非负整数）。这与 `ext`/`dialects` 那些**由语言定死**
  的取值不同 —— 面上唯一被规定的是一个保留键 `total_sites`。
* **没有"不认识的键要报"那一档**：面名本来就随便取，没有白名单可对。
  这是它与 `boundary` / `gc_ladder` 唯一一处**故意不同**的地方（那两条都有 extra-key 判据）。
* **零计数的面不写出来**：`emit` 按面名的字母序输出**非零**那几个（`file, mem, net,
  other, proc`），再写 `total_sites`。两份实现都按这一条，所以逐字节可比。

与 `gc_ladder` 的关系是**正交**的：那个分的是 `alloc` 站点（GC），这个分的是机调用站点
（边界）。一份单元可以只有一个、也可以两个都有。

## 3. 号 → 面：一张表（本文那一轮 22 个号；今天 **29** —— `select` 与联网那一族后来陆续进了表，见 `docs/231` / `docs/234`）

`tools/lomentc.py::SURFACE_NUMBERS` 是**单一真源**（自举侧没有第二份 —— 它数的是
`syscall4`/`syscall6`/`syscall7` 的**首参整数字面量**，与参考侧同一张表同一口径）。

| 面 | 号 | 是什么 |
|---|---|---|
| `file` | `0` `1` `3` `217` `257` `262` | `read`/`write`/`close`/`getdents64`/`openat`/`newfstatat` |
| `mem` | `12` | `brk` |
| `net` | `7` `23` `41` `42` `43` `44` `45` `46` `47` `48` `49` `50` `51` `52` `53` `54` `55` `232` `233` `288` `291` | `poll` / `select` 与 socket 那一族（`accept4`、`sendmsg`/`recvmsg`、`socketpair`、`epoll` 那一族都在内） |
| `proc` | `60` | `exit` |
| （兜底） | 其余 | `other` |

三条口径细节，都写在代码注释里：

* **词法口径**，延续 `boundary` 那条（`docs/204` R5）：只看"这个名字被调用了、首参写的是
  哪个数"，不看类型、不判断它危不危险；
* **首参不是整数字面量** ⇒ `other`（变量、表达式、常量名一律如此）。这是**兜底**，
  不是"数不出来就丢掉"——所以它必须出现在产物里；
* `syscall4` / `syscall6` / `syscall7` **三个内建都算**，与
  `potato.BOUNDARY_BUILTINS` 里 `syscall` 开头那几条一致（那条子集判据钉着）。

## 4. 判据

| 判据 | 钉住什么 |
|---|---|
| `lomentc_test::test_surfaces_rule` | **逐格**：`{file:2, mem:1, net:2, other:2, proc:1, total:8}`；`total == boundary.syscalls`；`other < total`（两处都非平凡，否则"全归 other"也能过）；空程序 = `{"total_sites": 0}` |
| `potato_test::MUTATORS_V11` | 九条负例：缺项 / 不是对象 / `total_sites` 缺或非整数 / 面名不是标识符 / 面是负数 / 面不是整数 / 各面之和对不上 / **与 `boundary.syscalls` 打架** |
| `potato_test::test_lompotato_twin_matches_python` | 两条校验器在 **134 份**对象上判决与**错误条数**逐条一致 |
| `loment_potato_emit_test::test_potato_emit_matches_reference` | 两个实现的形式对象**逐字节相同** |
| `loment_pe_test::test_pe_surface_table_matches_the_artifact_counter` | 声明侧的号表 == **运行时**侧的面表（见 §5） |

"与 `boundary.syscalls` 打架"那一条是**故意让和数自洽**的（`2+3=5`），这样报出来的
只能是交叉那一条 —— 否则它会被"各面之和对不上"遮住，判据看着绿、其实没走到那一行。

## 5. 声明侧 == 运行时侧

这条是联网那条线的**接口**，也是 v12 存在的理由之一：

> `lomentc.SURFACE_NUMBERS`（产物怎么数）与 `lomelf.PE_SURFACE_BY_NUMBER`
> （PE 上哪些号真的被派发、算哪个面）必须**是同一张表**。

两份各自漂会产出最难看的那一格：产物说"这个单元有 3 个 `net` 站点"，而 Windows 上那个号
拿到 `-1` —— **数得出来、跑不起来**。`docs/217` 堵的是后半句，这一条把前半句也钉住。
判据两个方向都报（运行时多 / 声明多 / 同名不同面），实测都是**号数与面名逐条相同**
（本文那一轮 22 个号，今天 29）。

顺带一句"万物可改"落在哪儿：`lomelf.PE_SYSCALLS`（号、面名、处理块）是**用户可改的数据**，
不是手写的发射序列（`docs/218` §12）。改它没问题 —— 改完这一条判据就红，于是
"换面名 / 挪号"必须**两边一起显式改**，不能悄悄漂。

## 6. 实测数字（本机，`emit_potato` 现算）

| 语料 | `surfaces` | `boundary.syscalls` |
|---|---|---|
| `loment/examples/demo.lomt` | `{total_sites: 0}` | 0 |
| `loment/examples/user_hello.lomt` | `{file:1, proc:1, total_sites:2}` | 2 |
| `loment/examples/all_loment.lomt` | `{file:2, proc:2, total_sites:4}` | 4 |
| `loment/examples/bootprobe.lomt` | `{file:2, other:1, proc:1, total_sites:4}` | 4 |
| `loment/tools/lompotato.lomt`（自举校验器自己） | `{file:6, mem:2, proc:1, total_sites:9}` | 9 |
| `polldemo`（`loment_pe_test` 的网络语料） | `{file:8, net:12, proc:2, total_sites:22}` | 22 |
| `sockdemo`（同上） | `{file:18, net:24, proc:2, total_sites:44}` | 44 |

最后两行是这一项**存在的意义**：把 `net` 这个词从"读者自己 grep"变成产物里的一个数。

## 7. 顺带撞破的一格：`lomelf` 的标签表（16384 → 32768）

自举侧多这一项，就是多种子 193 条标签 —— 于是**当场**撞上 `loment/tools/lomelf.lomt`
的标签表上限。值得记的是撞的姿势：

> 抬之前，种子已经用掉 **16375 / 16384** —— **余量 9 条**。

这堵墙早就站满了（`docs/200` §3 那两次抬高的后续）。抬法照旧（`docs/200` §7 记了这次的
数）：`TB_LBL_MAX` 翻倍、下游各表偏移整条 +393216、表尾 2244360 → 2637576（仍在 4 MiB
表区内）、**genesis 二进制一起重建**（`python tools/loment_genesis.py --emit` —— 没有它，
`bootstrap.sh` 的 1/4 步会拿冻着旧上限的链接器去汇编新种子）。

实测余量：标签表 **16568 / 32768**、全局表 2654 / 4096、回填表 21036 / 32768
（`loment_genesis_test::test_seed_fits_lomelf_table_caps`，本机）。

## 8. 点名不做

* **不给面名定白名单**。理由见 §2：面名是用的人的词汇，不是语言的。要"约束"就去
  `docs/218` 那条准入线上约束，别把校验器变成一张该由人维护的枚举表。
* **不报"哪个函数里越界"**。站点级定位归诊断（E 码）与 `loment stat`，
  产物这一层要的是**可计数**，加定位只会让它变成第二份源码索引。
* **不把 `surfaces` 回填进 v11 及更早**。台阶是累积的（`v12` 也验 `v10` 的 `gc_ladder`、
  `v7` 的 `boundary`），但**不回填**：老产物的自描述按它自己那版回放校验
  （M51，`loment/build/legacy/demo.v0.json` 就是这条判据的钉子）。

## 9. 文档同步

* `docs/147-potato-v1-spec.md` §版本台阶表补 v12 那一行；
* `docs/205-loment-015-alpha1-target.md`、`docs/210` 里"下一个版本台阶"的说法随之更新。
