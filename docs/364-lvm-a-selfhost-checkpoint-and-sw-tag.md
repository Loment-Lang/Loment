# 364 · 自举 checkpoint 与第二档基质 `sw_tag`

> 状态: **已实装**（2026-11-21），自举镜像 + 参考实现
> 上游: `docs/360`（时间桥梁）· `docs/362`（`sw_cap`）· `docs/371`（P5 / 封印 / 验证器）·
> `docs/359` §0（本世代的六个原语）· `docs/320` §1.2 ⑧（MTE 是检测不是保证）
> 判据: `tools/lvm_cp_test.py`（7 条）· `tools/lvm_cap_test.py`（9 条）·
> `tools/lvm_seal_test.py`（9 条）· `tools/lvm_twin_test.py`（14 条）
> 一句话: **自举镜像也会存/取自己的 guest 状态；第二档基质用 4 位标签模拟 MTE，
> 并把「它抓不住什么」写进封印。**

---

## 0. 两件事

| # | 内容 | 落点 |
|---|---|---|
| **1** | **自举 checkpoint / resume** | `loment/tools/lvm.lomt::exec_stack` |
| **2** | **`sw_tag` 基质**（4 位标签，概率检测） | `tools/lvm.py::SwCap(mode="tag")` + `loment/tools/lvm.lomt`（`sw_tag` 实参） |

配套判据：自举的存/取与参考**各自**跑同一份 IR，最后 `rc + stdout` 相同
（`lvm_twin_test::test_checkpoint_matches_reference`）；`sw_tag` 的碰撞由
`lvm_cap_test::test_sw_tag_reuse_is_probabilistic` 钉住，两侧同答由
`lvm_twin_test::test_sw_tag_matches_reference` 钉住。

---

## 1. 自举 checkpoint：把 Loment 的调用栈搬进 arena

### 1.1 为什么要搬

递归引擎的 guest 状态有一半分住在 **Loment 自己的调用栈**里：调用者的 `bi / j`、
返回值落点 `do_ / dl`、特权标记与 `__loment_alloc/free` 的生命周期事件。`exec_stack`
把这些写进 `CONT_OFF` 的续延记录：每层一帧 16 个 u32。

```
[fi, fr, cfr, ro, rl, bi, j, do_, dl, priv, hook, a0lo, a0hi, prev, –, –]
```

* `cfr / ro / rl`：这一帧的聚合返回值落到**调用者**哪儿；
* `bi / j`：调用返回后从哪条继续；
* `do_ / dl`：下一次调用的返回值落在**本帧**哪儿；
* `hook`：0 无 / 1 `__loment_alloc` / 2 `__loment_free`；`a0` 是它的第一个实参。

`bind_call` 是递归与显式两条路**共用**的绑定器：解析名字/实参、建帧、写事件、返回
`(fi << 32) | fr`。递归的 `do_call` 只是「绑定 -> 递归 -> 收事件」。

### 1.2 快照格式

自举这一侧没有 JSON/zlib，格式是原始二进制。**2026-11-21 起与参考实现同一份**
（`docs/372`）：头 16 B / ctx 256 B / 全局表（名字 + 地址）/ 对象表（7 × u32 一条）/
续延栈 2,112 B / guest 4 MiB / 扩展（帧名字段）。早先那版是自举私有布局
（全局表固定 6,144 B、对象表分段裸拷），统一时被替换掉。

IR 表**不存**：恢复时重解析同一份输入。头里的 FNV-1a 把快照绑到那份字节码上——
换一份 IR 恢复会被点名拒。

### 1.3 命令

```
lvm IN.ll ckpt=40 ckpt-out=/tmp/a.snap [sw_cap]
lvm IN.ll resume=/tmp/a.snap [ckpt=80 ckpt-out=/tmp/b.snap]
```

* `ckpt=0` 也是合法停点（"执行第 0 条之前"）—— 用单独一个 `ckpt_wanted` 位区分
  「没给」与「给了 0」。
* 恢复时以**本次命令行**的 `ckpt=` 为准：不写就跑到完，写了就在新的点再停。
* 基质与快照必须同档（ctx 里的 `C_SUB` 会被恢复；命令行没带同一档就拒）。
* 剖面/计数不进快照，带 checkpoint 时点名拒；T2 也拒。

### 1.4 判据

* `lvm_cp_test::test_checkpoint_carries_substrate_state`：`sw_tag` 的对象与标签一起进快照。
* `lvm_twin_test::test_checkpoint_matches_reference`：同一份 IR、同一个停点，两边
  **各自**存/取，最后的 `rc + stdout` 相同。
* 实测（本机 WSL）：K=1..120 每个点在自举侧都能存取；链式 30 -> 60 -> 跑完；
  `sw_cap` + checkpoint 也过。

**边界**：本文只证「各自的 guest 状态够全」。快照跨实现交接是 `docs/372` 的事
（同一份字节、两个方向，`lvm_twin_test` 各一条判据）。

---

## 2. `sw_tag`：4 位标签的 MTE 模拟

### 2.1 它是什么

`sw_cap` 用**对象代**判悬垂：`free` 时 `gen += 1`，旧能力必失效（非概率）。
`sw_tag` 换成 MTE 的形状：每个对象有 4 位标签（`1..15`），能力携带**指针创建时**的标签；
访问时比较标签，不等就 fault。

* 标签序列是确定的轮转（`1 + tag_seq % 15`），不是随机数；
* 地址被复用之后，新对象有 **1/15** 的机会拿到旧标签 —— 那时悬垂**漏掉**；
* 这不是缺陷，是声明：`sw_tag` 的 `no_dangling` 只能是 **`floored`**
  （`docs/320` §1.2 ⑧：MTE 是检测，不是保证）。

### 2.2 判据怎么钉

`test_sw_tag_reuse_is_probabilistic` 扫一段**复用长度** N=0..16：

* 每个 N 下 `sw_cap` 都是 **F02**（对象代精确）；
* 至少一个 N 下 `sw_tag` **不 fault**（标签碰撞）。本机是 **N=10**。
* 同一份 IR、同一份故障，只有基质声明不同 —— 这正是 `docs/360` §5.1 的「多基质同答」
  在**故障面**上的反例：答案可以不同，**声明必须跟着降级**。

`test_sw_tag_catches_unreused_dangling`：没有复用的悬垂，`sw_tag` 也抓（F02）。
`test_corpus_has_no_false_positives`：10 份合法语料在 `none / sw_cap / sw_tag` 三档下
逐字节同答。

### 2.3 自举镜像也有一份（2026-11-21）

`loment/tools/lvm.lomt` 用实参 `sw_tag` 打开第二档基质（`C_SUB = 2`）。形状与参考同：

* 对象表旁边一条**并行标签数组**（按下标，归还时随 `obj_copy` 一起搬）；标签在
  `obj_new` 里按 `1 + seq % 15` 轮转。`@__loment_heap` 在外层容器这一档**不登记**，
  但**吃掉一个标签号**，于是整条序列与参考逐位对齐；`brk` 与参考的 `grant_brk` 同形，
  同一块只登记一次、之后只延长上界。
* 每帧每个 SSA 值另存一格 `(对象基址, 标签)`（`CAP_OFF`，按 `深度 * MAX_VALS + 槽号`）；
  `alloca` / 分配器返回值 / `gep` 的基指针 / 调用实参把能力带过去。
* 指针**存进内存再读回**走影子表（`SH_OFF`，`addr / 基址 / 标签` 的环形缓冲）；
  对象归还时把落在它体内的影子条目作废 —— 否则下一次调用复用同一个帧槽地址时，
  旧能力会"复活"。
* 访问检查 `cap_check_tag`：标签对上按活对象判边界；对不上而能力的对象还在就按它判；
  否则 F02。`ptrtoint`/`inttoptr` 照参考一样丢能力，退回按地址那一半。
* 判据：`lvm_twin_test::test_sw_tag_matches_reference` —— 合法程序、未复用悬垂、
  复用长度 N=0..16：两边的 rc/stdout 相同，故障的 `F0k + 站点` 相同，**碰撞漏在同一批 N**。
  本机是 16 抓 / 1 漏（N=10）。

### 2.4 封印跟着降级

`lvm_seal_test::test_sw_tag_declares_no_dangling_floored`：`sw_tag` 封印里
`no_segv` 是 `checked`、`no_dangling` 是 `floored`，`P3.gen` 不在 guarantees，
`P3.probabilistic` 在 assumptions。验证器对这份封印给 PASS —— 它验的是**声明自洽**，
不是「足够安全」。

---

## 3. 顺手修掉的一个真 bug：影子表的键

`sw_cap` 的影子表原先在 `load ptr` 时拿**被读出的值**当键，而不是读取的**槽地址**。
后果：一个 `let p: ptr` 只要经过栈槽就被读成「无能力」，只有把指针洗成整数那条路
还会被地址回退抓到。判据 J（复用碰撞）先红：`sw_cap` 抓不到 `p` 的悬垂。

修法一行：`slot_s` 求值后 `shadow.get(int(slot))`。修完以后，能力能过栈槽，
`sw_cap` 对同一程序给出 F02；`sw_tag` 则在标签碰撞的那个 N 上放过去 —— 两个基质的
差别才真正显出来。

**教训**：能力传播的每一格都要有判据。「大多数程序不把指针存进栈槽再读」不是理由；
`let p: ptr` 本身就是这条路径。

---

## 4. 判据汇总

| 套件 | 条数 | 新增 |
|---|---|---|
| `lvm_cap_test` | 9 | `sw_tag` 未复用悬垂 / 复用碰撞（概率） |
| `lvm_cp_test` | 7 | `sw_tag` 快照带对象与标签 |
| `lvm_seal_test` | 9 | `sw_tag` 的 `no_dangling` 必须 `floored` |
| `lvm_twin_test` | 10 | 自举 checkpoint 与参考同答（含跨实现两条，`docs/372`） |

---

## 5. 我不主张

1. **快照跨实现已在 `docs/372` 落地。** 这条原先写的是"两种格式，不互通"；
   2026-11-21 起两边写同一份字节，`lvm_twin_test` 有两条跨方向判据。
2. **不主张 `sw_tag` 是随机标签。** 确定轮转，便于判据；MTE 真机是随机/派生。
3. **不主张 `sw_tag` 抓得住所有悬垂。** 碰撞就漏，判据里明写了 N=10。
4. **不主张自举 checkpoint 覆盖外部输入。** 合成 cmdline 与真实文件不在快照里；
   恢复要在同一输入下。
5. **不主张性能。** 快照 4 MiB 裸写、每次访问多一次对象表/标签查，全部未测。
6. ~~不主张自举有 `sw_tag`~~ —— **2026-11-21 已补**：镜像有第二档基质，孪生判据 M 钉两侧同答；
   自举侧的 `sw_tag` **checkpoint** 仍不支持（跨实现恢复只认 `none` / `sw_cap`）。

---

## 6. 与其它文档的关系

* `docs/371` —— 参考实现的 P5；本文把同一件事搬到自举镜像，并补第二档基质。
* `docs/362` —— `sw_cap` 的 T0 实现；§3 的影子表 bug 是它的一处真缺陷，本文修掉。
* `docs/360` §3.3 —— `sw_tag` 在四档表里就是「中间态，声明要降级」；本文给它执行与判据。
* `docs/320` §1.2 ⑧ —— MTE 是检测不是保证；`no_dangling: floored` 是那句话的机械形态。
