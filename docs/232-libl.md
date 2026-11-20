# 232 · LibL：标准库的 libc 面，判据是**与真 glibc 逐字节对跑**

> 状态: **五张脸已落地**（`std` 四张 2026-10-11 + **宿主脸** `libl_host` 2026-10-15）·
> 目录: `lompi/store/std/0.1.0/` 的 `string` `ctype` `stdlib` `stdio`，以及 `lompi/store/host/0.1.0/libl_host`
> 判据: `loment_libl_test`（2/2；**225 项与系统 glibc 2.43 逐字节一致**）· 入口: `use libl` 与 `use std`
> 上游: `docs/199`（std 可达与平名字冲突）· `docs/180`（std 核的分层与三条约定）·
> `docs/183` V1（静态 libc）· `docs/219`（世界端口）· `docs/188`（不猜，报错）
> 一句话: **这个标准库叫 LibL；它对 libc 的那张脸，函数名、参数序、返回值语义都取 libc 的，
> 而判据不拿 Python 当对照物 —— 拿 libc 自己。**
>
> ⚠ **号冲突（如实记）**：同机另一条线（LVM）在本机工作树里也用了 `docs/232-`。
> 本文件先落（写它时全树最高号是 231），而那一份**还没进任何分支**。仓里已有 `docs/228`
> 两份并存的先例，所以不为此改号；**先合进来的那条占号，后合的那条改号**。

---

## 0. 一句话展开

`lompi/store/std/0.1.0/` 里原先有 **127 个模块、1356 个 `pub fn`**，而它们**一个 libc 名字都没有**
（实测：`strlen` `strcmp` `printf` `atoi` `qsort` `malloc` … 24 个常见名字，命中 0）。
那不是遗漏 —— 那是刻意的形状：**长度显式**（`text_cmp(p, ao, al, q, bo, bl)`）、
**偏移寻址**（`mem_copy(dst, doff, src, so, n)`）、**没有 NUL 串、没有变参**。

这一轮给这个包补上**另一张脸**：libc 那张。

|  | `lompi/store/std` 原有 | LibL 这一轮加的 |
|---|---|---|
| 形状 | 这门语言自己的（长度显式、偏移） | **libc 的**（NUL 串、参数序照抄） |
| 名字 | `char_is_digit` `text_cmp` `sort_heap` | **`isdigit` `strcmp` `qsort`** |
| 给谁 | 写 Loment 的人 | **会 C 的人**（"不必学新东西"） |
| 判据的对照物 | Python（`loment_std_test`） | **真 glibc**（`loment_libl_test`） |

两张脸**指同一批字节**：`ctype.lomt` 的 `isdigit` 直接调 `char.lomt` 的 `char_is_digit`，
`memcpy` 直接调 `mem_copy`。所以"同一个字节两个答案"这件事结构上不可能发生。

---

## 1. 名字：LibL 是**库**的名字，`std` 仍然是**包**的名字

用户 2026-10-11 定的形状：**"整合这些 std 模块，成为 LibL，但仍然可以用 std 调用，
这些模块是一个专属名词 std LibL。"**

落成两个入口，指同一个包：

| 入口 | 拉进来什么 | 什么时候能用 |
|---|---|---|
| `use std` | **整包**（131 条 `use`，1403 个 `pub fn`） | 到处都能用 |
| `use libl` | **只拉 libc 那四张脸** + 它们的依赖（`mem` `char`）—— 6 份模块 | 到处都能用（**2026-10-12 到 2026-10-15 之间发行版拒**，见 §1.1） |

`libl` 这一份不是"std 的别名"：`std.lomt` 那个门面把整包拼进**一个单元**，平的发射符号空间里
挤着 1350+ 个顶层名字，编译时间还按模块数**超线性**涨（`docs/199` §1 实测自举侧 n=64 已 187 秒）。
`libl` 只有 6 份模块 —— 面小、编得快、撞名面也小。

**包名仍然是 `std`**：库系统要求"包名与入口文件名一致"（`docs/168`
`test_multi_file_package_survives_materialize`）。所以**包**叫 `std`，**库**叫 LibL。
一个是库系统的键，一个是这门语言里这个库的名字 —— 两者不矛盾。

### 1.1 `use libl` 的发行闸：**2026-10-12 锁，2026-10-15 放开**

> **已放开**（`#358`）。放开的条件是"先把承诺写下来" —— 承诺在 **§1.3**。
> 本节留作**为什么当初要锁**的记录，以及那条闸**怎么落的**（它现在仍然是 `choose vm` 的闸，
> 只是 LibL 这个客户已经不再用了）。

> 「**LibL 仍然在仓库中**，和 LVM 一样**发行再锁**。」（用户 2026-10-12）

**文件留在仓库**（`lompi/store/std/0.1.0/libl.lomt` 照旧随包发），锁的是"能不能取到它"：
**锁的那段时间里**，开发通道 `use libl` 照旧可用，**发行版里它是编译错误**、点名 `libl`。

这条与 LVM 那条**是同一条闸的两个客户**（`docs/231` §2 的渠道闸）：

| | 被关住的 | 闸在哪一层 |
|---|---|---|
| LVM | `choose vm`（一维核心模式） | `check()`（`docs/231` §2） |
| LibL | `use libl`（**一个名字**） | **名字解析**：参考实现 `resolve_name()`、自举侧 `driver.lomt` 的 `use` 分支 —— **该分支已随闸一并拿掉** |

**为什么当初要锁**：名字一旦在发行版里可用，它就成了一条**事实上的 ABI** —— 有人开始依赖
`use libl`，之后改这四张脸的范围、或把 LibL 从"库的名字"做成别的东西，就是破坏性变更。
而"承诺落空比拒绝更坏"（`docs/231` §2 同一句）：发行版说"不"。
**放开的前提就是把那条 ABI 先写下来** —— 所以 §1.3 是这份记录的另一半，两节要一起读。

**为什么闸在名字解析而不是 `check()`**：被锁的是**一个名字能不能取到**，那是装载器的事。
`check()` 那层手上只有 token 流，判"这个单元 `use` 过谁"要另做一趟全量扫描 —— 而自举侧
那条 60s 护栏只剩 ~1% 余量，不该为它加一趟扫描。名字解析处本来就在走 `use` 语句，
多一次比较。（放开之后这个取舍仍然成立：**以后要再锁一个名字，还是落在这一层**。）

**机制**：与 `vm` 那条**读同一个常量**（`loment/selfhost/checker.lomt` 的 `LOMENT_CHANNEL`，
打包期由 `loment_dist.py` 只替换那一行）。闸在时那一行从 `const` 变成了 `pub const`，
自举侧 `driver.lomt` 才读得到它 —— 代价是**产物层多一处具体化**：两个种子差 **2** 个字符
而不是 1。**放开之后那个分支没了，读取点回到一处**（`CHANNEL_READ_SITES` 从 2 改回 **1**），
"两个种子恰好差一个字节"这条最强的口径也就回来了。当时**没有**为此加一个函数把读取点
合并回一个：实测加函数会让自举那版顶破 60s 护栏 —— 放开闸顺手把它还原了，是白捡的。

**边界（如实列，不假装它堵得住）**：闸只管**名字形式** `use libl`。路径形式
（`use "lompi/store/std/0.1.0/libl.lomt"`）与"把这份文件拷进自己项目"都绕得过去 ——
而那与"那四张脸本身可用"是一回事（它们经 `use std` 或按名 `use string` 本来就在）。
这条闸当时要防的是**名字变成契约**，不是那几份模块不能用。**这条边界仍然成立** ——
今天放开的正是"名字"这一层，别把它读成"LibL 现在整个都可依赖了"（范围见 §1.3）。

**判据**：`loment_channel_test::test_release_driver_refuses_channel_gated_constructs` ——
`use libl` 的探针**留着**、期望**反转**成"发行版必须**收**"（棘轮规矩是**替换**不是删掉）；
同一个判据里也测 `choose vm` / `choose hosted` 两条**仍然关着**的闸，**两端都测**
（只测"拒"的话，一个"什么都拒"的驱动器也能绿）。

### 1.2 LibL 家族：`internet` 是**第二个包**（2026-10-13）

上面 §1–§1.1 说的 `libl` 是 `std` **包内**的一个小入口。家族的另一半是**另一个包**：
`lompi/store/internet/0.1.0/`（`docs/240`）。两者是同一个说法下的两件事 ——

* **`std` 是 libc 那一面**：把"写 C 的人以为语言自带的那些函数"补上（`string` / `ctype` /
  `stdlib` / `stdio`），判据对着**真 glibc** 逐字节跑；
* **`internet` 是网络那一面**：把"接上网"这件事收成库（`ip` / `uri` / `http` / `dns` /
  `sock` / `tcp` / `udp`），判据对着**真的实现**跑 —— CPython 标准库
  （`ipaddress` / `urllib.parse` / `http.client`）与**一个真起的 `http.server`**。
* **`host` 是同一面里要 syscall 的那一半**（2026-10-15 加，`libl_host.lomt`）：`fopen`
  那一套加 `exit`/`abort`。它**不在 `std` 里**，因为 `std` 的不变量是"一个 syscall 都不发"
  —— 详见 §5.1。依赖方向单向：`host -> std`。

两者共用**同一条判据口径**："对照物是真的东西，不是我们手写的期望值"。
这条口径不是风格问题：LibL 那边 `strncpy` 补零、`strcmp` 返符号还是差值这些分歧，
拿 Python 当对照物**对不出来**（§4）；`internet` 这边同理 —— DNS 的线格式没有真解析器
可对（工具链里就没有，`docs/236`），所以那一层**明写**"对的是按 RFC 1035 独立推出来的
字节，不是真实现"，不假装对过。

**命名上的硬约束（加模块前必读）**：包内模块的**顶层名字在一个单元里是平的**，而
`resolve_name` 在包内模块层遇到**同名两处命中直接报错**。所以 `internet` 里不能出现
`url`（`std` 占了）、也不能用 `u_` 这种内部前缀（那是 `std/url.lomt` 的）。
判据 `loment_internet_test::test_internet_names_do_not_collide_with_std` 逐条比
internet 与 std 全部模块的顶层名字 —— 这是**第一个**看着这条规则的判据。

---

### 1.3 承诺：哪些名字算数，以什么条件可以改（`#358` 要求先写下来的那一份）

`use libl` 一旦在发行版里解析得到，它就是一条 ABI。所以**先写清楚它承诺什么** ——
这一节是那份承诺，放开闸的依据就是它。

**一、被承诺的名字**（在本版里**不许删、不许改名、不许改语义**）：

| 模块 | 被承诺的 | 个数 |
|---|---|---|
| `string` | `strlen` `strcpy` `strncpy` `strcat` `strncat` `strcmp` `strncmp` `strchr` `strrchr` `strstr` `strspn` `strcspn` `strpbrk` `strtok_r` `memchr` `memcmp` `memcpy` `memmove` `memset` + **`cstr_of`** | 20 |
| `ctype` | 12 个谓词 + `toupper` `tolower` | 14 |
| `stdlib` | `atoi` `atol` `strtol` `strtoul` `abs` `labs` `llabs` `malloc` `calloc` `qsort` `bsearch` + `qsort_u32` `bsearch_u32` + `enum qsort_kind` | 11 + 1 枚举 |
| `stdio` | `snprintf` `sprintf` + `SF_LEFT` `SF_ZERO` `SF_UNBOUNDED` | 2 + 3 常量 |
| `libl_host` | `fopen` `fclose` `fread` `fwrite` `fseek` `ftell` `fflush` `feof` `fgets` `fputs` `puts` `putchar` `getchar` `exit` `abort` | 15 |

**二、语义的标尺就是判据**：这些名字的行为以**与真 glibc 逐字节对跑**为准
（`tools/loment_libl_test.py`，今天 **225 项**）。LibL 的语义不是"我们觉得像 libc"，
是"与它跑出来一样"。

**三、加是自由的，减不是**。§5.1 里今天写着"不做"的那些行（`realloc` / `free` / `FILE*` 的
其余 / `getenv` / `%f` …）**以后落地不算破坏性变更** —— 承诺只约束"已经发出去的名字"。
反过来，**删一个、改一个名字、改一个 `qsort_kind` 变体的意思**，都是破坏性的：要么按语言的
版本规则升版并写明，要么不做。

**四、偏离**是合约的一部分**，不是免责声明**。今天登记的偏离：

* §3 的语言逼出来的四处（`cstr_of`、`nargs`、`qsort` 的 `kind`、`strtok_r` 而非 `strtok`）；
* `libl_host` 的三处（**不缓冲** ⇒ `fflush` 是空操作；**没有 `stdin`/`stdout`/`stderr`**；
  `fopen` **只认 `"r"`/`"w"`**）+ 一处（`abort` 是 **SIGILL** 不是 `SIGABRT`）；
* §5.1 里"不做"的每一行。

**它们写在模块头注里**，所以是"读得到的前提"，不是"用起来才发现"。

**五、不承诺的**：所有 `sd_*` / `sf_*` / `lf_*` 内部名字、`FILE` 那块 16 字节的布局、
以及各模块的自检函数。它们是实现，随时可改。

**六、边界照旧**（§5.2）：这是**源码**面，不是 C ABI。C 代码调不到 `LibL::strlen`。

**七、这条承诺本身怎么被验**：`loment_channel_test` 里那条判据从"发行版**拒** `use libl`"
**反转**成"发行版**收** `use libl`"（按棘轮规矩是**替换**，不是删掉）—— 而 `choose vm`
那半**原样留着**（它还没到兑现的时候）。所以"闸没被误删"与"闸确实开了"各有各的判据。

---

## 2. 五张脸：libc 头 -> LibL 模块

**一个 libc 头对一个模块**（这条映射本身就是"照 libc 实现"的一部分）。
前四张在 `std`（**不发 syscall**），第五张在 `host`（要发 syscall，所以另开一张包 —— §5.1）。

| libc 头 | LibL 模块 | 提供的名字 | 个数 |
|---|---|---|---|
| `<string.h>`（+ POSIX `strtok_r`） | [`string.lomt`](../lompi/store/std/0.1.0/string.lomt) | `strlen` `strcpy` `strncpy` `strcat` `strncat` `strcmp` `strncmp` `strchr` `strrchr` `strstr` `strspn` `strcspn` `strpbrk` `strtok_r` `memchr` `memcmp` `memcpy` `memmove` `memset` | 19 |
| `<ctype.h>` | [`ctype.lomt`](../lompi/store/std/0.1.0/ctype.lomt) | `isdigit` `isupper` `islower` `isalpha` `isalnum` `isspace` `isxdigit` `isblank` `iscntrl` `isprint` `isgraph` `ispunct` `toupper` `tolower` | 14 |
| `<stdlib.h>`（能做的那一半） | [`stdlib.lomt`](../lompi/store/std/0.1.0/stdlib.lomt) | `atoi` `atol` `strtol` `strtoul` `abs` `labs` `llabs` `malloc` `calloc` `qsort` `bsearch` | 11 |
| `<stdio.h>`（格式化那一半） | [`stdio.lomt`](../lompi/store/std/0.1.0/stdio.lomt) | `snprintf` `sprintf` | 2 |
| `<stdio.h>` + `<stdlib.h>`（**要 syscall 的那一半**） | [`libl_host.lomt`](../lompi/store/host/0.1.0/libl_host.lomt)（**`host` 包**） | `fopen` `fclose` `fread` `fwrite` `fseek` `ftell` `fflush` `feof` `fgets` `fputs` `puts` `putchar` `getchar` `exit` `abort` | 15 |

加三个**不是 libc 名字**的：`cstr_of`（§3.1）与两个便利档 `qsort_u32` / `bsearch_u32`。
合计 —— LibL 发出 **61 个 libc 名字 + 3 个自己的名字**，其中 **15 个在 `host` 包**
（要 syscall 的那 15 个），其余 46 个在 `std`。

---

## 3. 语言逼出来的四处偏离（**每一处都写在这里，不藏着**）

照 libc 实现，但语言不是 C。四处必须偏离，另有一处必须**补**。

### 3.1 补：`cstr_of` —— 因为本语言的字面量**不带 NUL**

这一条是动手时最先撞上的，也是最要紧的。

```rust
// "abc" 的 IR：
@.str._start.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"
```

**三个字节，没有第四个。** 所以 `strlen(str_ptr("abc"))` 会**读过头** —— 实测第一版
探针里 `strlen` 对空串返回 **17**（一路读到偶然的零字节才停）。

C 里没有这个问题（`"abc"` 在 C 里天生带 NUL），所以 **libc 不需要这个函数** ——
它不是我们漏抄，是两种语言的 `str` **不是同一个东西**：Loment 的带长度，C 的不带。

```rust
pub fn cstr_of(dst: ptr, s: str) -> ptr   // 拷 str_len(s) 字节 + 补一个 NUL
```

仓里同一件事已经做过一次：`loment/lib/proc.lomt` 的 `pc_cstr`（发 syscall 前的私有助手，
注释原话"系统调用要的是 C 字符串"）。LibL 把它提成公开的 —— 因为**每一个**用 LibL
的 NUL 串函数的人都要它。

### 3.2 变参：`snprintf` 多一个 `nargs`

libc 的 `printf` 靠**可变参数**；本语言没有 `...`。所以：

```rust
snprintf(buf, cap, fmt, args: ptr, nargs: u32) -> u32
```

`args` 指向一个 **u64 数组**（第 k 个实参在偏移 `k * 8`）。转换说明符**自己**说明该实参
怎么解释 —— 这一点与 libc 完全一样（`%d` 与 `%s` 的读法本来就由格式串决定）。

`nargs` 是 libc **没有**的（它数不出来）。少给实参时 libc 读越界垃圾，这里**停止转换**
并把已产出的部分收尾。**这是唯一一处签名上的偏离。**

### 3.3 函数指针：`qsort` 的第四个参数是 `kind`，不是比较函数

本语言**没有函数指针**（`TYPES` 里没有 `fn` 类型，`lomentc.py:29-30`）。所以：

```rust
pub enum qsort_kind { qk_u32, qk_i64, qk_bytes, qk_cstr }
pub fn qsort(base: ptr, n: u32, width: u32, kind: qsort_kind)
```

这不是降级 —— 它把"按什么比"从**运行期的一个地址**变成**产物里的一个常量**，
于是"这个排序按什么比"是不读源码可判的（与 `gc_ladder`、端口表同族，`docs/210` §3）。
**代价如实列**：自定义比较（比如"按第二个字段排"）写不出来，调用方得自己写循环。

### 3.4 模块级可变全局：`strtok` / `rand` / `errno` 落不下来

本语言**没有模块级可变全局**（顶层只收 `const`，`lomentc.py:1626-1738`）。于是：

| libc | 为什么不行 | LibL 给的替代 |
|---|---|---|
| `strtok` | 靠一个模块级静态游标 | **`strtok_r`**（游标由调用方持有）—— POSIX 里本来就有，不是降级 |
| `rand`/`srand` | 模块级静态种子 | 仓里 `rng.lomt` 的形状（`rg_next_u64(state)` —— 状态由调用方持有，种子也由调用方给；它自己的头注就写着"为什么是这一套（不是 `rand()` 那种）"）—— **名字不叫 `rand`** |
| `errno` | 模块级线程局部 | **没有**。`strtol` 越界时饱和到边界值并照常返回，调用方自己比边界（`stdlib.lomt` 头注写明） |

### 3.5 混合宽度：库代码里一次 `ptr == ptr` 都不能有

这一条不是设计，是**实测撞出来的纪律**。参考实现对 `ptr == ptr` 发出**非法 IR**
（`icmp == ptr ...`，clang 当场拒；就是 issue **#205**）。所以：

```rust
// ✗ 编不过（clang: expected icmp predicate）
if s == (0 as ptr) { ... }
// ✓ 同一个判断，合法
if (s as u64) == (0 as u64) { ... }
```

同族的还有"把 u64 右移一个 i32 变量"（§5.3 第 2 条）、以及内联 `0 as ptr` **不能当实参**
（检查器报"实参期望 ptr，得到整型字面量" —— 要先落到一个返回 `ptr` 的函数上）。
三条都写进了库代码的注释里，免得下一个人重踩。

### 3.6 仓库规矩：`runtime` 维要声明（合 main 时才浮上来的一条）

这一条不是 libc 的偏离，是**本仓的一条新规矩**，而它是**合 main 之后**才浮上来的：

`docs/227`（PR #212，2026-10-10）把 `runtime` 从"只声明"变成**一句保证** ——
`no_runtime`（默认）说"产物里没有运行期"，于是凡用到 `/` `%` `alloc` `free`
`str_concat` `str_eq` `panic` `guard` 的程序，**根单元**必须写 `choose runtime`；
而**库写 `choose` 是声明它的需求**，根必须满足。

LibL 的四个模块都用到除法/取模/`alloc`，所以各写了一行 `choose runtime`
（包内 126 个模块早就是同一个形状），`libl.lomt` 门面也一样；判据里的根单元同样。

**这一条把"老程序一行不改照编"作废了** —— 那是 `docs/227` §2.2 明写的代价，不是漏掉的一格。

---

## 3.7 顺手修掉的一处 **main 侧**红（不是本笔的发现）

合 `main` 之后 `loment_std_test::test_std_modules_match_python` 是**红的** —— 而且
**在 `origin/main` 上就红**（在本笔之前）。核过：`origin/main` 的
`loment/tools/lomstdcheck.lomt:20` 有 `choose runtime`，而 `tools/loment_std_test.py` 里那个
**生成的探针字符串没有** —— #212 改了仓内孪生、漏了这个字符串。

它挡着**每一个** PR 的门禁（这条判据在 `ci.py` 的 `STATIC_CHECKS` 里），所以这一笔顺手
把那行补上（只加一行 `choose runtime`，不动行为）。**这不是"顺手修实现"** ——
它是一条因为规则变了而失配的判据，不是 §6 那种要单独开 issue 的缺陷。

---

## 4. 判据：对照物是 **libc 自己**，不是 Python

用户 2026-10-11 定的口径：**"Libc 怎么实现的 LibL 就怎么实现。"** 这句落在判据上就是
一条：**别拿 Python 当对照物。**

`loment_std_test`（`docs/180`）比的是"Loment 算的 == Python 算的"。那条对 std 核是对的，
但**对"像 libc"这个问题是错的**：`strncpy` 补不补零、`strcmp` 返回符号还是**差值**、
`strchr` 找不找得到结尾那个 NUL、`memmove` 重不重叠安全 —— Python 与 libc 在这些边角上
**根本不是同一个答案**，于是"两处一起错"照样绿（`docs/176` §7.3）。

`tools/loment_libl_test.py` 因此这么做：

1. 在 WSL 里用 `cc` 编一段 C（走**系统 glibc 2.43**），跑一遍；
2. Loment 侧跑**同一组调用**；
3. 两边 **stdout 逐字节比**，第一个不同行当场报出来。

**225 项对齐**，含：`strncpy` 的补零与"源够长时不给 NUL"、`strcmp` 的无符号比较
（`0x80 > 'a'`）、`strchr(s, 0)` 命中结尾、`strstr` 空针返回 `hay`、`strtok_r` 的连续分隔符、
`qsort` 的四种 kind、`snprintf` 的**返回值与截断**（`cap=1` 时只写 NUL 却返回长度 3）；
后加上来的两批是**分配**（`malloc`/`calloc`，含溢出返回 NULL）与**宿主脸**（文件那一套：
`fopen`/`fwrite`/`fseek`/`ftell`/`fread`/`fgets`/`feof`）。
最后一行不是 stdout 比较，而是**把两边写出的那个文件 `cmp` 一遍** —— 写出去的东西也要逐字节相同
（今天 300 字节）。

### 4.1 两处刻意的归一化（不归一化就是拿实现细节当语义）

- **`strcmp` 的返回值**：POSIX 只保证**符号**。判据里比两样 —— **符号**（可移植的那条契约）
  与**差值**（glibc 的实际实现）。两条都过了，才说明"既合契约又合这份实现"。
- **`ctype` 的返回值**：glibc 的 `isdigit` 展开成 `(*__ctype_b_loc())[c] & _ISdigit`，
  返回的是**位掩码**（不是 1）。判据两边都归一化成 0/1 —— 比的是"是不是"，不是 glibc 的
  内部常量。做法是**12 个谓词打包成一个位掩码**，两侧同序生成。
- **返回指针的函数**：两边地址不同，比的是**偏移**（`off(base, p)`，找不到 -1）。
  `bsearch` 那条尤其要小心：C 侧一开始写的是**元素下标**，LibL 给的是**字节偏移** —— 判据
  当场指出 `bs_hit_off=8` vs `2`，改成同一个量才对齐。

---

## 5. 落不下来的（**如实列，不假装有**）

### 5.1 每一行的处置（`#357` 要的那张表：**落地**，或者**写明的"不做"**）

`#357` 的要求只有这一句："对 §5.1 的每一行，要么落成一个有 glibc 对照判据的面，
要么写清楚'不做，因为……'"。下表就是那个答案，**没有第三种状态**。

**占位符是补建的**：落地的那几件**事先没有单子**，事后各补了一条 ——
`#402`（分配）、`#401`（宿主脸）、`#403`（其他格式）；两条撞出来的缺陷也各归了位
（`#383` 本笔修掉、`#381` 是本表里那条"不能做"）。它们挂在 **0.1.4.6** 上，
随 PR #397 一起关。**这不是"先做后补"的借口** —— 只是把已经落地的东西记成可检索的号。

| 那一半 | 函数 | 处置 |
|---|---|---|
| 分配 | `malloc` `calloc` | ✅ **已落地**（`stdlib.lomt`；在**内建堆**上按 libc 拼法写，**不带自己的长度头** ⇒ 内建 `free` 收得下）。判据：`loment_libl_test` 里分配那一组，与 glibc 对跑。补建单：**`#402`** |
| 分配 | `realloc` `free` | ⛔ **不做**。`realloc` 要旧长度才能搬，而长度只在**编译器私有**的堆头里（`docs/158` 要防的正是"把实现细节钉进库"）；`free` 更硬 —— **名字被内建占了**：库里的 `pub fn free` 解析不到（调用点静默走内建），而且会与 C 侧那个符号撞名 |
| `FILE*` | `fopen` `fclose` `fread` `fwrite` `fseek` `ftell` `fflush` `feof` `fgets` `fputs` `puts` `putchar` `getchar` | ✅ **已落地**（`lompi/store/host/0.1.0/libl_host.lomt` —— **宿主脸**，因为它要发 syscall）。判据同上，与 glibc 对跑。补建单：**`#401`** |
| `FILE*` | `stdin` `stdout` `stderr` | ⛔ **不做**。它们是**模块级可变全局**，语言没有（§3.4）；而且宿主脸里 `fflush` 是**空操作**（不缓冲），这三个的价值也随之变小 |
| 进程 | `exit` `abort` | ✅ **已落地**（`libl_host.lomt`，同 `#401`）。`abort` 发的是 **SIGILL 不是 SIGABRT** —— 登记的偏离 |
| 进程 | `atexit` | ⛔ **不做**。要一张进程级的回调表 = 模块级可变全局 + 运行期，两样都没有 |
| 环境 | `getenv` `setenv` | ⛔ **不做**。世界面（`docs/219` §4 的 `world` 类）；要用就走 `lompi/store/host` 的 `environ` 口子，不进 `use libl` 这条承诺 |
| 浮点格式化 | `%f` `%e` `%g` | ⛔ **不能做**（不是"没做"）—— 见下，上游是 **`#381`**（挂在 0.1.4.6） |
| 浮点解析 | `strtod` | ⛔ **不做**。上游 `f64fmt.lomt` 自己写着**反向没做**：十进制→double 要大整数**除法**，而那一层刻意只用乘法（"等 T7 的 JSON/TOML 真需要时再补，叫 `f64fmt_parse`"）。所以这不是 LibL 的缺口，是它的上游还没有 |
| 其他格式 | 精度 `.N`、星号宽度 `*`、`%p`、`%n` | ✅ **已落地**（`stdio.lomt` 头注那张表，判据与 glibc 对跑）。补建单：**`#403`** |

**宿主脸另开一张包**（`host`，不是 `std`）是**用户的裁定**（2026-10-15）：`std` 的不变量是
"**一个 syscall 都不发**"，把 `fopen` 那 15 个塞进去就把那条不变量破了。依赖方向仍然单向：
`host -> std`。代价是 `use libl` **拉不到**这 15 个（它们要 `use libl_host`）—— 这一点写在
§1.3 的承诺里，是**合约的一部分**，不是漏记。

**`%f` / `%e` / `%g`：从"没做"改成"不能做"**（2026-10-15，`#357` 的两轮之间）。

原记录是"语言层没有浮点类型；仓里是软浮点，接进格式串是独立的一件事"。接线本身确实只是几行 ——
`stdio.lomt` 加一条 `use "f64fmt.lomt"`，`f64fmt_fixed`/`f64fmt_sci` 就是现成的（而且那套是
**精确**展开，正是逐字节比 glibc 需要的东西）。**但那条 `use` 让整个单元编不过**：
实测 **27 处非法 IR**，全是 `i32 -> i64` 的混宽运算（**#289** 那一类），散布在
`wide`（5 个函数）、`bigint`（2 个）、`f64`（4 个，含 `f64_mul`/`f64_div`/`fa_*`）。

**比那 27 处更要紧的是它们推出的第二件事**：那串模块**从来没有被编译过** —— 所以
**它们的正确性也没被验证过**。把 `%f` 接上去、再与 glibc 逐字节比，多半只是把"没编译过"
换成一条**长尾的数值错**（dtoa 正是"只在某些输入上差一位"的那种）。

所以这一格的处置是：**记为不做，并把理由指向 issue #381**（那里有逐函数的分档、
"为什么不是纯机械地加转换"、以及"做完长什么样"的三条）。**这不是向后退** —— `#357` 要的
正是"落地并配判据，或者写明为什么不做"，而这里两者都有了。

> **2026-11-08 更新（`docs/316` §3）**：`#381` 已于 2026-10-17 关闭，`%f`/`%e`/`%g` 与
> `strtod`（`f64fmt_parse`）已经落地，判据是 `loment_std_test` 的 29 项 Python 对拍；
> `loment_libl_test` 的 225 项 glibc 对拍仍然全绿。**上文这一格的状态已过时**，
> 保留它是为了记下"当时为什么不做"。

### 5.2 边界：**这不是 C ABI**

LibL 是**源码库**，不是一份能被 C 对象链进去的 `.a`。C 代码调不到 `LibL::strlen` ——
那要"Loment -> 目标文件/归档"这条路，属于 §6。**别说它"能替换 libc"**：它替换的是
"写 Loment 的人要自己手写 `strcmp`" 这件事。

---

## 6. 顺带撞出来的四个既有缺陷（**都在 LibL 之外**）

写这一轮时撞出来的，**不是 LibL 的 bug**，也不是这一轮修 —— 按
`feedback_test_findings_go_to_issues` 的规矩单独记：

1. **`mem.lomt::mem_move` 按偏移判重叠方向** —— `if doff <= so` 走前向，而偏移是**相对各自
   基址**的。跨基址调用时判断是错的：`mem_move(b + 1, 0, b, 0, 4)` 两边偏移都是 0，
   于是它走前向，而真实地址是 `dst > src`，必须倒着拷 —— 结果是第 2 个字节被写成第 1 个
   还没读的字节（实测 `memmove_b2` 读回 65，glibc 是 66）。**libc 比的是地址。**
   LibL 的 `memmove` 因此自己按指针判方向，不转给它。
2. **`numfmt.lomt::numfmt_hex_into` 发出非法 IR** —— 它把 `u64` 右移一个 **i32 变量**
   （`v >> shift`），参考实现发出 `lshr i64 %v, i32 %shift`，clang 当场拒。这是既有的
   **混宽缺陷**（`docs/180` §4.2 / `docs/188` §7.1.1）落在 std 代码上 —— 一个**谁都没调用**
   的函数，直到有人 `use numfmt` 才把编译打断。探针因此自带了十进制输出。
3. **`ptr == ptr` 发出 `icmp == ptr`**（issue **#205**，已知）。库代码一律比 `u64`（§3.5）。
4. **dep pass 拿"兄弟依赖"当"这个依赖"查**（issue **#246**，这一轮开的）。`check()` 末尾
   把每个依赖**当入口**再查一遍（`#149`），递下去的是 `others = 其余依赖整份` —— 对
   "符号可见面"是对的（更宽只少报），但 `runtime` 维问的是"这个单元**真正依赖**的那些
   单元的要求"，而递下去的根本不是。于是任何闭包里同时有"声明了 `choose runtime` 的"
   与"什么都没声明的叶子"的单元，都会收到一条**假**报错，还报在那个无辜的叶子上：
   `char.lomt`（它**一条 `use` 都没有**）被当成"依赖了 `mem`/`string`/`ctype` 的程序"。
   实测 `main` 上 **九个模块**已经栽在这上面：`f64(2) f64conv(3) f64fmt(4) mathf(5)
   round(3) std(258) text(1) trim(1) word(3)`。**没有门禁拿它们当入口查，所以一直潜伏。**

前三条**没人报过**（第 3 条早有 issue）；第 4 条是这一轮开的 issue #246。

**这一条对判据的影响**：LibL 的 `test_libl_modules_are_checkable` 与探针编译都走
`_dep_pass=True`（`loment_libl_test.py` 的 `_check_unit`），即**只量这个单元自己的正文**。
理由写在那个函数的注释里：LibL 主张的是"这几个模块自己是合法单元"，**不主张**
"它们的依赖也合法"。**这不是把一条红绕过去** —— 那条红指的是别人的文件，而且
`main` 上九个模块同样红。

---

## 7. 未来：把收编面与运行时落成 Loment 写

用户 2026-10-11 的裁决：这一条**进本文档，不是这一版**。

### 7.1 要动的是什么

今天有三处**手写的、不是 Loment 写的**东西：

| 在哪 | 是什么 | 为什么手写 |
|---|---|---|
| `tools/lomelf.py` 的 `emit_conscript` + 自举侧孪生 | `memcpy` `memset` `memcmp` `malloc` `free` 的**机器码字节**（各约 102 字节） | 链接器要发它们，而编译器编不了自己 |
| `tools/lomentc.py` 的 `_IR_HEAP`（4627 B） | `gc_manual` 的分配器（bump + 地址序空闲链 + 前沿回退） | 同上 |
| `tools/lomentc.py` 的 `_IR_RUNTIME`（1465 B） | freestanding 运行时（`__loment_memcmp` 一族） | 同上 |

`docs/219` §6 第 2 条说的是：`malloc` 之所以能从端口表上**划掉**，唯一理由是
**名字的另一端在我们手里**。而今天那"另一端"是一串字节，不是一份能被读、被测、
被判据覆盖的源码。

**落成 Loment 写的**，才有三件今天没有的东西：① 一份实现而不是两份手抄；
② 它进 `std` 的判据网（今天那 102 字节没有行为判据，只有"两侧逐字节相同"）；
③ 端口表上"另一端在我们手里"这句话变成**可读的**。

### 7.2 为什么不能现在就做：鸡生蛋

编译器要编出**它自己的一部分**。三个形态，代价从小到大：

- **A. 编译期注入**：编译器在发产物时，把运行时的 Loment 源码**先编一遍**再拼进去。
  参考实现（Python）做得到；自举侧要能编它自己 —— 而自举侧今天已经在编自己（种子固定点），
  多一层不是原理问题，是**种子大小与编译时间**的问题（`lomrel` 的 4 MiB 归一化缓冲
  今天用在 3469847 B，余量 724457）。
- **B. 预编成一份 `.lomt` 常量表**：运行时用 Loment 写成，构建时编成 IR **文本**，
  存进仓库当生成物（与 `selfhost_driver.ll` 同一个形状）。
  **这条路最稳**：`docs/158` §5 那条"两个实现逐字节一致"照旧成立，而运行时从此是**一份源**。
- **C. 真把它做成可链接的库**：要"Loment -> 目标文件"这条路，今天不存在（CLI 只有
  `ir`/`build`/`pkg`/`lib`，没有 `--emit-obj`）。

### 7.3 与端口表的交点（`docs/219` §5.3 那条**没拍的价**）

`docs/219` §5.3：**完备 ⇔ 没有按名解析 ⇔ 没有动态链接**。所以 `docs/173` 的**阶段 3
（动态库装载）与端口表互斥**。这一条**到今天仍然没有裁决**，到 S2/S3 会撞上。
LibL 不退让：它只是"名字的一端在我们手里"这件事在**源码层**的第一步。

---

## 8. 与其它文档的关系

* `docs/199` —— **上游**：std 可达性、平名字冲突、门面为什么别用。`libl` 这个入口正是
  那条"面小、不撞名、编得快"的推论。
* `docs/180` §3 —— **约定**：输出写进调用方缓冲、输入 `(ptr, 长度)`、找不到返回 `-1`。
  LibL 的 `str*`/`mem*` 遵守它；`cstr_of` 是它在这一层的补丁（§3.1）。
* `docs/183` V1 —— **上游**：静态 libc。本文是它在**源码层**先落的一步（不链二进制）。
* `docs/219` §6 第 2 条 —— **未来那一格的支点**：`malloc` 从表上划掉的理由是"另一端在我们手里"。
* `docs/210` §3 —— `kind` 代替比较函数这个形状的来源（"不读源码可判的数"同族）。
* `docs/188` —— "不猜，报错"：不认识的格式说明符**原样输出** `%` 与那个字符，不静默吞。
