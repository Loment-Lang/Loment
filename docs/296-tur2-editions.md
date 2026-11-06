# 296 · Tur 2.0：版次、首批语法与向下兼容

> **封版（2026-11-05）**：§4 的六项全部落地，`tur2026.2` 已封死；封版记录、特性面与
> 下一版 `tur2026.3` 已于同日开版；首项新特性是 `lgc`（`docs/309`）。
> 开下一版的程序见 **`docs/305`**。新特性只能进 `tur2026.3` / `tur2027.1`。
>
> 2026-11-04 的用户指令：**优化 Tur 语法**，把 Python / Java / C++ / Zig / Go 的语法吸收
> 进来；**保证 Loment 的 Rust 严格子集时期遗留的功能不被阉割或复杂化**；**全面向下兼容**；
> 新语法即使再改，老语法仍然可以编译，长线不被打断。

**进度**：§4 第 1 项（版次脚手架）已落地 —— 两级版次名 `tur2026.1` / `tur2026.2` 进了
出厂锁，`FrontUnit.profile` / `mod.profile` / 自举 `PROF_OFF` 都记下了版次，三处出口
（编译期提示、产物元数据、`tur_edition()`）也一起落地（§1.2）。此时同一份程序的正文与
代码在 Tur 1 与 Tur 2 下逐字节相同，只有版次元数据按版次取值（判据在
`loment_grammar_test`、`loment_p8_test` M87/M89）。

**§4 第 2 项（纯糖）也已落地**：`def` / `and` / `or` / `not` / `elif` 在前门做**等长**
改写（`potato_from._tur2_sugar` 与驱动的 `tur2_sugar` 同一张规则表），`elif` 缺的那个
`if` 由解析器/检查器/发射器在 `profile == tur2026.2` 时补上。判据：糖版与手写基础拼法
IR 逐字节相同（`loment_grammar_test`）、两套实现逐字节相同且能跑（p8 M90）、Tur 1 的
同一份源仍拒收。

**§4 第 3 项（`for x in xs` + `break` / `continue`）也已落地**：`for-in` 反糖成
`let __tur2_i.N: u32 = 0; while … { let x = src[i]; 体; i = i + 1 }`（数组取静态长度、
切片取 `slice_len`），下标自增在参考侧挂在 `While.step` 上、在自举侧由 `fi_step` 发射 ——
体尾与 `continue` 共用同一份指令。`break` / `continue` 作用于最内层循环，发射前先关掉
体内打开的 L2 纪元；循环外使用是解析错（E19），Tur 1 里两个词仍是普通标识符。判据：
两套实现逐字节相同且能跑（p8 M91）、Tur 1 拒收、循环外 E19。

**§4 第 6 项（`print_num`）与第 4 项的后半（裸 `return`）也已落地**：`return;` 只在
Tur 2 且函数**没有返回类型**时成立（Tur 1 / 有返回类型仍是 E19），退出路径与带值那条
同序（L1 该还的还、`gc_auto` 的根表该退的退）。`print_num` 是 Tur 2 的内建，实参任意
整型、按符号扩到 i64，十进制转换在运行期块里（`no_runtime` 点名拒，E31）。判据：
p8 M94（两套实现逐字节相同、输出 `0/1234/-98765/i64 上限`、Tur 1 的 `return;` 仍拒而
用户自己的 `fn print_num` 照常跑、E19/E31 各一条）。

**§4 第 5 项（`catch` / `orelse`）也已落地**：形状是"恰好两个变体、第一个（成功）带
载荷"—— 预置 `Option` / `Result` 都是这个形状，所以两个词在实现上是同一条规则。只在
`let` / 赋值右侧；`switch` + `phi` 合流，兜底值**惰性**（失败那一支才求值）。判据：
p8 M95（两套实现逐字节相同、成功/失败两支各跑一次、兜底里的 `print_num` 不被求值、
Tur 1 的同一份源解析不过）。

**§4 第 4 项的前半（`defer`）也已落地**：`defer f(a, b);` 属于**所在块**，正常走到块尾、
`return`、`?` 提前返回、循环里的 `break` / `continue` 都会按 LIFO 跑完（panic/trap 不跑，
§2.3）。实参在**声明处**求值：参考实现捕获的是 SSA 值，自举侧把值存成文本、退出时内联
重放；返回值在跑 defer 之前先挪到值栈第 38 层，否则重放会把 `ret` 的值覆盖掉。判据：
p8 M96（两套实现逐字节相同；LIFO、块作用域、声明处捕获、`if` / `match` 两条臂、
`return` / `?` / `break` / `continue` 各跑一次；Tur 1 里用户自己的 `fn defer` 照常编、
`defer 1;` 在 Tur 2 里是 E19）。

至此 §4 的六项全部落地 —— `tur2026.2` 随即封版（`docs/305`）。

## 1. 兼容法规：语法按**版次**冻结，版次按**年份族**归档

Tur 现在的parser是"位置识别"的：**没有关键字表**，`defer` / `and` / `def` 今天都可以是
变量名或函数名。直接把新词变成全局关键字，会把老代码里的同名标识符重新解释 —— 那不是
向下兼容。

### 1.1 两级版次名

版次名 = **`tur<年份>.<年内序号>`**，例如 2026 年的第一版是 `tur2026.1`、第二版是
`tur2026.2`。一年走完，这一年的所有版次归到同一个族 **`tur<年份>`**（`tur2026`）；新的一年
从 **`.1` 重新开始**（`tur2027.1`）。

| 声明 | 是什么 | 承诺 |
|---|---|---|
| 不写 / `choose write grammar loment` | **最新版次**（`DEFAULT_EDITION` = `TUR_EDITIONS[-1]`，今天 `tur2026.3`） | 2026-11-06 用户裁定：新写的代码默认就有最新语法（`docs/312`） |
| `choose write grammar tur` / `… tur1` / `… tur2026.1` | **Tur 1**（2026 年第一版，规范名 `tur2026.1`） | **永久冻结**：新语法不进这一版，旧文件的行为一个字节都不变 |
| `choose write grammar tur2` / `… tur2026.2` | **Tur 2**（2026 年第二版，规范名 `tur2026.2`） | 吸收 Python/Zig/Go 的首批语法；Tur 1 的文件**原样**能编 |
| `choose write grammar tur3` / `… tur2026.3` | **Tur 2026.3**（2026 年第三版，**未封顶**） | 继承 2026.2；新特性进这一版（首项 `lgc`，`docs/309`） |
| 将来的 `tur2027.1` … | 2027 年族 | 同上；每个版次的语义与拼法都冻结 |

**短名与全名**：`tur1` / `tur2` 是**当年短名**，只在发布它的那一年由工具链接受。
跨年之后短名**不再接受** —— 报错并给出对应的全名，**绝不复用**到新族。否则 2026 年写的
`tur1` 会在 2027 年被读成另一套语法，老文件就被重新解释了。

`loment new` 不写声明（于是跟着默认走，**今天就是最新版次**），所以没有要展开的东西；
`loment fmt` 目前**拒绝**任何带 `choose write grammar` 的文件（它只处理原生写法、不碰
声明行），所以"写盘时展开短名"要等 fmt 收下带声明的文件之后才有落点 —— 在那之前，
**要钉旧读法就写全名**（`tur2026.1` / `tur2026.2`）。这条边界写在实现里，不假装已经做了。

`tur` 是 Tur 1 的历史拼法（2026-11-03 命名时的写法），永久指向 `tur2026.1`；
它是拼法，不是族名。`tur2026` 只是**归档标签**（一族的名字），不能拿来编译。

规则：

1. **版次只加不改**。规范名一旦发布就永远指那一版；要改就开下一个序号或下一年的 `.1`。
   "新语法即使被修改，老语法仍然可以编译"就是这一条。
2. **版次是**文件头**的读法**，不是 `module` 之后的开关。`choose` 写在 `module` 后面时
   文件已经读完了，选不了读法。所以版次和 `choose write grammar` 同处：**文件头、一次、
   在 `module` 之前**。
3. **版次是原生读法**：不经过翻译器；抹掉声明行之后由同一套 lexer/parser 读，只是多一个
   profile 标志。两套实现必须逐字节一致（`docs/158` §5）。
4. **新词只在新版次保留**。Tur 1 里 `defer` / `and` / `def` 仍是普通标识符；Tur 2 里它们
   在**构造位置/算子位置**是保留词，在名字位置仍可作标识符（能区分的地方不抢）。
5. **加一个版次 = 改一次出厂锁**：`potato.GRAMMARS`、`GRAMMAR_ALIASES`、`NATIVE_GRAMMARS`、
   自举 `strip_grammar_decl`、VS Code 的 `NATIVE_GRAMMARS`、`surface_data`。这是一笔显式的
   "改锁"，不是顺手加分支。
6. **短名不进对象**：Potato 里只写规范名（`tur2026.2`），同一份源在两台机器上不会因为
   编译年份不同而出两串字节。短名只在**源侧**、只在当年有效。

### 1.2 版次必须**自己说出来**

用户 2026-11-04 的要求：编译期与运行期都要能知道这份源属于 Tur 的哪一版。三处出口，
同一个规范名：

1. **编译期**：`check` / `build` / `run` / `ir` 在 stderr 打一行
   `loment: grammar tur2 -> tur2026.2 (Tur 2)`；没写声明时打
   `grammar default -> tur2026.3 (Tur 2026.3)`（默认是最新版次，`docs/312`）。
   短名在这一行展开 —— 跨年后用户能立刻看到
   "这个旧短名今年已经不被接受了"。**工具**（把驱动当子进程、整段比对输出的程序，如
   `lomcomefor`）传 `--no-edition-notice` 关掉这一行；人敲命令时它永远在。
2. **产物里**：每份 IR 全局区最后一条是
   `@loment.tur.edition = global [21 x i8] c"\4C…\00"`，内容 `LOMENT-TUR:` + 规范名。
   用**外部可见**的 `global` 而不是 `private constant`，因为 clang `-O2` 会丢掉没人引用
   的私有常量，而这一条正是给事后查证用的。`loment version <exe>` 分块扫描这个标记。
3. **运行期**：内建 `tur_edition() -> str` 返回规范名（指向上面那条全局里冒号之后
   那一段）。程序自己可以 `print(tur_edition())`，日志与报错因此带上版次。

## 2. Tur 2 首批语法（本版）

### 2.1 纯糖（不碰语义）

| 写法 | 等价于 | 来源 |
|---|---|---|
| `def f(...)` | `fn f(...)` | Python |
| `elif cond { }` | `else if cond { }` | Python |
| `a and b` / `a or b` / `not a` | `a && b` / `a \|\| b` / `!a` | Python |

这三样只改拼法，类型/代码生成与 Tur 1 完全相同；同一份程序在 `tur` 与 `tur2` 下
**产物逐字节相同**（判据会钉）。

### 2.2 `for x in xs`（数组/切片遍历）

```rust
for x in xs {
    print(x);
}
```

v1 只接受**具名的数组或切片变量**（`let xs: [u32; 4]` / 参数 `xs: [u32]`），因为元素类型
必须能在语法层定下来。降级为：

```rust
{
    let __i: u32 = 0;
    while __i < <len> {
        let x: T = xs[__i];
        <body>
        __i = __i + 1;
    }
}
```

`<len>`：定长数组取静态长度；切片取 `slice_len(xs)`。`x` 是**按值拷贝**（与 `xs[i]`
同义）。对非数组/切片的表达式、对 `mut` 引用的原地遍历，留给 Tur 3。

### 2.3 `defer stmt;`（Zig / Go）

```rust
def f() {
    let p: ptr = alloc(16);
    defer free(p);
    ...
}
```

语义：

* 属于**所在块**；块退出时按 **LIFO** 执行；
* 正常走到块尾、`return`、`?` 提前返回，都执行；
* `panic` / trap / 进程退出**不执行**（与 Zig 的 `defer` 在 panic 上的选择一致：不假装
  能收拾一个已经 abort 的进程）；
* `defer` 的表达式在**声明处求值**，语句在退出时执行（`defer free(p)` 捕获的是当时的
  `p`，不是退出时的 `p`）——这是 Zig/Go 的共识，判据会钉；
* 一个块里多条 `defer` 逆序执行；嵌套块各自负责自己的。

**自举侧的上限**（捕获表定长）：每函数 8 条 `defer`、每条最多 4 个实参、嵌套 64 层 ——
超出时发射器点名报错（"too many defer statements in one function" / "a defer takes at most
4 arguments" / "defer nesting is too deep"），不静默截断。参考实现没有这三个上限（与
`CG_MAXFN` 那类容量同一条不对称：参考侧无界，自举侧定长且说出话来）。

### 2.4 `catch` / `orelse`（Zig）

```rust
let a: u32 = maybe orelse 0;          // Option：None -> 0
let b: u32 = risky() catch 0;         // Result：Err -> 0
```

v1 只做**表达式位置的兜底**，且只用于 `let` / 赋值右侧：

* 形状规则（与预置枚举对齐）：**恰好两个变体，第一个（成功）带载荷、第二个不带**。
  `__prelude` 里 `Option { Some(T), None }` 与 `Result { Ok(T), Err(E) }` 都是这个形状；
* `opt orelse d`：成功那一支取载荷（`T`），另一支取 `d`（`T`）；
* `res catch d`：同上（`Ok(T)` 取载荷、`Err(_)` 取 `d`）—— v1 里 `orelse` 与 `catch`
  是**同一条规则的两个拼法**，分开只为读起来顺；
* 兜底值**惰性**：只在失败那一支求值（与 Zig 一致）；两支在 `switch` + `phi` 里合流；
* 需要看错误值的 `catch |e| { ... }`、需要提前传播的 `catch return` 留给 Tur 3。

### 2.5 `break` / `continue`（C / Go / Zig / Python）

```rust
while go {
    if done { break; }
    if skip { continue; }
    ...
}
```

* 作用于**最内层**的 `while` / `for i in lo..hi` / `for x in xs`；
* `continue` 去该循环的**更新步**（range `for` 是 `i = i + 1`，`for x in xs` 是下标加一，
  `while` 是条件）；
* 不在循环里写 `break` / `continue` 是**诊断**（新码），不是静默；
* 发射时先**关闭体内打开的 L2 epoch** 再跳（`docs/210` —— 跳出去不收尾就是泄漏），
  两套实现的标签编号必须一致；
* 只在 Tur 2：Tur 1 里这两个词仍是普通标识符（今天 `break;` 报"使用未声明的变量"，
  一个合法的变量名 `break` 在 Tur 1 里照旧可用）。

### 2.6 裸 `return`（C / Go / Python）

```rust
def f() {
    if bad { return; }     // 只许出现在**没有返回类型**的函数里
    ...
}
```

* `fn f()`（无返回类型）里 `return;` 提前退出；`fn f() -> T` 里 `return;` 仍是错
  （必须给值），与今天一致；
* 有 `defer` 时，它按 `defer` 的退出路径执行（§2.3）；
* 只在 Tur 2。

### 2.7 `print_num`（C / Go 的 `printf("%d")` 那一格）

```rust
print_num(n);            // 十进制，负数带 '-'，不带换行
```

* 内建 `print_num(n) -> u32`：十进制、无堆（24 字节栈缓冲）、一次 `write`；
* **实参是任意整型**：按它自己的类型求值，再按符号扩到 i64（`u8/u16/u32` 走 `zext`，
  `i8/i16/i32` 走 `sext`，`i64/u64` 原样）。无类型的**单个字面量**落 i32（与
  `expr(IntLit, None)` 同侧），其余无类型表达式落 u32（与 `Bin` 的 `want or "u32"` 同侧）；
* 十进制转换在**运行期**那一块里（`__loment_print_num`），所以它会把运行期拖进产物 ——
  `no_runtime`（默认档）下点名拒（E31），要它就在单元里写 `choose runtime`；
* 换行仍用 `print("\n")` —— 与 `print` 一样**只写字节**，不擅自加格式；
* **只在 Tur 2 是内建**：Tur 1 里 `print_num` 仍是普通标识符（用户自己定义的
  `fn print_num` 不会被顶掉）。

## 3. 兼容性判据（这一版的"不打断长线"是可验证的）

1. **Tur 1 回归族**：`loment/examples/**` 与 `loment/selfhost/**` 全部在 `tur` 下照旧编译，
   产物与改动前逐字节相同（已有的 M80/M82/M85 判据就是这一族）。
2. **同义糖**：2.1 的三种写法各一份夹具，Tur 1 / Tur 2 两版产物逐字节相同。
3. **版次隔离**：一份 Tur 1 文件用 `defer` 作变量名，在 `tur` 下编得过；同一份文件改成
   `tur2` 并以构造位置使用 `defer`，编不过；两份都留判据。
4. **短名 == 全名**：`tur2` 与 `tur2026.2` 编同一份源，产物逐字节相同；`tur`、`tur1`、
   `tur2026.1` 三者同样。跨年之后短名不再被接受（见 §1.1），这条由当年的锁表决定。
5. **旧声明不变**：`grammar rust` 仍是严格 Rust 面；`loment` / `tur` 的语义不变，
   `NATIVE_GRAMMARS` 里放的是规范名（`tur2026.1` / `tur2026.2`）。
6. **双实现**：每条新语法在 `tools/lomentc.py` 与 `loment/selfhost/*` 上逐字节一致；
   `loment_rule_parity` 的新规则只增不减。
7. **控制流那一族的版次隔离**：`break` / `continue` / `print_num` 在 Tur 1 里仍是普通
   标识符（用它们当变量名、或自己定义 `fn print_num`，照旧能编）；`return;` 只在
   Tur 2 且函数**没有返回类型**时成立，带返回类型的 `return;` 与今天一样报错。
8. **退出路径**：`break` / `continue` / `return;` / `?` 跳出时都收掉体内打开的 L2
   epoch；`defer` 的 LIFO、提前退出执行、panic/trap 不执行各有一条运行期判据。

## 4. 落地顺序（每一笔都可独立回滚）

1. **版次脚手架**：`tur2026.1` / `tur2026.2` 进锁，`tur` / `tur1` / `tur2` 作为当年
   拼法；`front_door` 返回 profile；自举驱动记录 profile；两套实现都能"接受 Tur 2 但
   不启用任何新语法"（等价于 Tur 1）。判据：短名与全名的产物逐字节相同，且与 Tur 1 相同。
2. **纯糖**：`def` / `elif` / `and` / `or` / `not`；两套实现；同义糖判据。
3. **`for x in xs` + `break` / `continue`**：循环与"怎么离开循环"是同一条控制流账本，
   一起做。parser + checker + codegen（两套）；非数组/切片、循环外的 `break` 有诊断码。
4. **`defer` + 裸 `return`**：提前退出与"退出时做什么"是同一条账本。LIFO / `?` /
   `return` 都执行、panic/trap 不执行的判据。
5. **`catch` / `orelse`**：parser + checker + codegen（两套）；`Option`/`Result` 两族。
6. **`print_num`**：一个内建、两套实现；纯增量，可与任一步并行。

第 3–5 步各自要新增诊断码（`loment_diag.RULES`，只增不改）与
`loment_rule_parity._CASES` 的负例，`BUDGET` 跟着抬。种子在**独立的 `[生成物]` 提交**里
重算；手写与生成物分开。

## 5. Tur 3（下一个版次）—— 明确**不在**这一版

> 用户说的 "Tur 3" 落到版次名上：现在（2026 年内）是 **`tur2026.3`**；如果跨年，
> 就是 **`tur2027.1`**。下面这些不是"没做完"，是**有意留给下一版**。

* **生命周期控制（`docs/299`）**：`&T` / `&mut T` 成为一等类型（结构体字段、返回值），
  区域推断、**零 `'a` 标注**；分叉时只多一个参数名（`&a [u32]`）。保 Rust 的安全性，
  但把 `'a`/`<'a>`/`where` 全部去掉；
* `:=` / `auto` 的局部变量类型推断（这条最值钱，但动类型系统，必须单独一版）；
* `match` 臂与 `if` 当**表达式**用（`0 => 10, _ => 20` / `let x = if c { 1 } else { 2 };`）；
* 字符串插值 / 格式化（`f"{x}"`），以及随之而来的 `str_concat` 之外的一条路；
* `args()`：把命令行参数做成内建（今天每个 CLI 自己读 argv）；
* 结构体字面量简写 `S { a }`；
* `const NAME: str = "...";`（今天 `const` 只吃整数字面量，命名字符串得写成函数）；
* `catch |e| { ... }` / `catch return`（需要看错误值或提前传播）；
* `for x in <表达式>`（不限具名数组/切片）与 `for i, x in xs`；
* `class` / `new` / 继承；`comptime`；`chan` / `select` / `go` 的运行时；
* ~~把 `tur2` 变成 `tur` 的默认~~：**2026-11-06 已改判** —— 默认改为**最新版次**
  （`docs/312`）。旧读法仍可显式钉住；开新版次时的"静默改义"由碰撞登记表 + E040 拦住。
