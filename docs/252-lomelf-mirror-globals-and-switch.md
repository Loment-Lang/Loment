# 252 · 镜像链接器上两处「不吭声」：64 位全局与多行 case 表（issue #1）

> 状态: **已落**（2026-10-16，`origin/main` = `e1d2b504`）
> 上游: issue #1（正文）· #21（capability 单独就崩）· #102（模块级 `i64` 全局 SIGILL）·
> #2 / #10（`match` 让回填表满）· `docs/200` §6（本文 §6 纠正它的一条判断）
> 判据: `loment_elf_test::test_mirror_links_the_shapes_that_used_to_take_it_down`（新增）·
> `loment_elf_test::test_lomelf_selfhost_matches_reference`（`MIRROR_GAP` 由 2 条变空）
> 一句话: **issue #1 今天不是"没有诊断"那个问题** —— 它是镜像链接器里两处各自独立的错，
> 一处除零崩掉、一处死循环把自己撑爆；`tour` 只是同时落在两处上的那份文件。

---

## 1. 先把 issue #1 的正文校正过来（今天实测）

issue 正文写的是"构建失败且**一个字都不打印**"。那是 2026-09-18 的形状。今天在
`origin/main`（`e1d2b504`，本机原生后端出的包）上重跑，**第一层已经被 main 自己补掉了**，
后面两层还在：

| 步骤 | 结果 |
|---|---|
| `loment check loment/examples/tour.lomt` | **0**（main 上已给示例补了 `choose runtime`，`alloc` 不再撞 E022） |
| `loment ir` | 0，22485 B |
| `loment build` | **1** —— `loment-lomelf` `Illegal instruction`（`rc=132`） |

而把 `capability` 那两行摘掉之后，**换一种方式死**：

```
$ loment-driver tour_nocap.lomt > t.ll      # exit 0
$ loment-lomelf t.ll                        # exit 1
lomelf: 回填表满了 — 抬高 TB_FIX 的容量 (loment/tools/lomelf.lomt 的 TB_FIX_MAX)
```

**两个失败点、两个成因、互不相干**，而 `MIRROR_GAP` 当时只记了前一个。
这是一条一般性的教训: **"同一份文件链不出来"不是一条判据**，它可能压着两条独立的缺陷，
只按文件记，修完第一条之后第二条仍然没有名字。

## 2. 成因一：`one_global` 拿 u32 做 64 位的事（issue #102）

`loment/tools/lomelf.lomt` 的全局初值原本只有三条路: `c"..."`、`zeroinitializer`、
**一个数**。最后那条是这么铺字节的：

```rust
let nv: u64 = num_at(t, k, le);
let v: u32 = lo32(nv);
let b: u32 = 0;
while b < sz {
    store8(gb, o + b, ((v / pow256(b)) % 256) as u8);   // ← pow256 返回 u32
    b = b + 1;
}
```

`pow256(b) = 256^b` 在 **u32** 里算：`b == 4` 时 `256^4` 回绕成 **0**，于是 `v / 0` ——
这门语言的除法是**检查过**的，当场 trap，进程被 `SIGILL` 掉，**一个字都没来得及打印**。

为什么 `i32` 全局一路没事、`i64` 才炸: `sz` 是类型宽度，`i32` 的循环只到 `b == 3`
（`256^3 = 16777216`，还没回绕），`i64` 走到 `b == 4` 就撞上。

**踩中它的形状**是能力域表 —— 编译器为每个文件发一份：

```llvm
@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }] [{ i64, i64, i64, i64 } { i64 1163935908, i64 0, i64 4, i64 1 }]
```

`i64` 元素 + 非零初值。**所以凡是声明了 `capability` 的程序都链不出来**，而
`loment check` 和 `loment ir` 两条都过 —— issue #21 报的就是这个现象，它的成因就是 #102。

顺带还有第二个错，不崩但更坏: 那段代码**根本不解析聚合初值**。`num_at` 从 `[` 上读不到数字
返回 0，于是数组/结构体全局**被写成全零**。就算把除零修掉，那也是一次**错编译**——
地址表全零，`guard` 在运行期查不到东西。所以这里不是"加个边界检查"就完的。

**修法**: 换成按类型递归的 `emit_init`，逐条对着参考实现 `tools/lomelf.py` 的 `_parse_init`
写（`c"..."` / `zeroinitializer` / 数组 / 结构体 / 标量，含负数与类型前缀剥离），
标量那段改成先用 `num64` 按 **64 位**累计、再按 `lo32`/`hi32` 各 4 字节铺开 ——
`pow256` 的实参永远 ≤ 3。

## 3. 成因二：`do_switch` 的换行盲，以及它把自己撑爆的方式（issue #2 / #10）

自举 codegen 发出来的 `switch` 是**多行**的：

```llvm
  switch i32 %t2, label %L1_mend [
      i32 0, label %L2_mSmall
      i32 1, label %L3_mBig
    ]
```

而 `do_switch` 收到的 `le` 是 `logical_end`（跨行指令的末端，`docs/167` 那套），所以它**看得见**
三行 —— 但推进 case 指针用的是 `skip_sp` / `skip_comma`，**这两个只吃空格/制表/回车，不吃换行**
（它们的语义是"按行工作"，全文件都这么用，所以不能就地改）。于是：

1. 最后一条 case 的 label 之后，`q = skip_comma(...)` 落在 `\n` 上，`q < rb` 仍成立；
2. 下一轮 `next_label` 在剩下的空白里找不到 `label`，按约定退回 **`(0,0)`**；
3. `q = skip_comma(t, 0, rb)` = **0** —— **指针回到文件开头，从头再来**；
4. 每绕一圈都发一条 `cmp`/`jcc`/`jmp`，也就是发一条回填，32768 圈之后报"回填表满了"。

`loment check` 和 `loment ir` 照旧全绿 —— 这是**链接期**的事。

**修法**: 加一对吃换行的 `ws_after` / `ws_after_comma` 只在这条路上用，并补两条前进保证
（找不到下一条 case 就停；新位置不大于旧位置就停）。多一个字的正面效果:
**那条"回填表满了"从此不再是这套输入的出口**。

## 4. 为什么它不是容量问题（这一条要留证据）

`docs/200` §6 与门禁里的 `MIRROR_GAP` 都把 `native_match_full` 那次记成
"回填表容量上限（`TB_FIX_MAX=32768`）…… **属容量, 不属缺陷**"。这个判断是错的，
推翻它只需要一个输入：**case 表为空、只是把 `]` 写到下一行**的 switch。

```
switch i32 %x, label %z [
  ]
```

它一条 case 都没有，按任何口径都**不该要一条回填**。原实现照样报"回填表满了"。
能一条都不要却报满，`TB_FIX_MAX` 是多少都救不了 —— 是第 3 节那个回到 0 的死循环。

**所以抬常数是错误的方向**（issue #10 正文自己就点了"先弄清 `tour` 撞的是不是同一次"，
现在有答案了: 是，而且两次都不是容量）。这条空 case 表的形状进了判据。

## 5. 判据（红过，再绿）

新增 `test_mirror_links_the_shapes_that_used_to_take_it_down`: 两个**手写 `.ll` 夹具**，
一个只放各类 64 位全局（标量 / 负值 / `4294967296` / 数组 / 能力域表那种嵌套结构体 / 短字节串），
一个只放多行 `switch`（含那个空 case 表）。两条路各编一次，与参考实现**逐字节比**。

| 夹具 | 未修（`origin/main` 的镜像） | 修后 |
|---|---|---|
| `i64_global` | `rc=3221225501`（`0xC000001D`，即 `SIGILL`），**stdout/stderr 全空**，无产物 | 与参考逐字节同（8280 B） |
| `multiline_switch` | `rc=1`，`lomelf: 回填表满了 … TB_FIX_MAX` | 与参考逐字节同（8192 B） |

另外把 `tour` 与 `native_match_full` 从 `MIRROR_GAP` 挪回语料 —— 那张表**只许变短**，
反向核那段会逼着做（现在是**空的**）。

端到端（`loment build` 那条路，用本仓源现出的包）:

```
$ ./loment build tour.lomt -o tour && ./tour       # 在 WSL 里跑 ELF
42 10 9 6 2 3
TOUR RESULT: PASS                                   # exit 0
```

`loment/examples/` 下**每一份能链的**现在都与参考逐字节相同（原先 `tour` /
`native_match_full` 两份不行）；剩下的差异只有既有的容量型干净拒绝
（`native_entry` → `TB_SLOT_MAX`）与 IR 后端缺口（`native_raii`），与本文无关。

## 6. 顺手纠正的三处口径

* `MIRROR_GAP`里 `native_match_full` 那条"属容量, 不属缺陷"——**错的**，见 §4。表已清空。
* `.claude/skills/loment/SKILL.md` §6.3 第 20、21 条（"capability 单独就让 build 崩" /
  "回填表在小程序上就满"）**已不成立**，删掉并把后面的条目重新编号（引用点一起改）。
  §1 那段"`tour` 在纯包环境下链不出来"的注也一并改写 —— 现在它是**能编能跑**的，
  并写清"老包上仍然会失败，那是老包不是你的程序"。
* 同一份指南 §6.1 第 1 条引用的那个"unrelated linker limit"不再是回填表 ——
  `match` 的表达式臂今天仍然编不过，但换成链接器对该处**值**的干净拒绝
  （两边实现都拒），措辞按实测改了。

## 7. 没做的

* **没有抬任何容量常数**。`TB_FIX_MAX` 保持 32768 —— §4 说明了这次不是容量。
  `docs/200` §6 那句"守卫只报错退出, 不自动扩容"本身仍然成立（它是关于**真**越界的），
  本文只纠正它被误用到的这一处。
* **`lomelf.lomt` 不在自举闭包里**（种子是 `loment/selfhost/driver.lomt`），
  所以**不用重算种子**；要重算的是 **genesis**（`python tools/loment_genesis.py --emit`，
  它冻着镜像那份产物）与发布清单 / `dist`。
* `tools/lomelf.py`（参考侧）**一行没动** —— 两个形状它本来就对。这次是把镜像拉齐到它。
