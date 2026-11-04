# 296 · Tur 2.0：版次、首批语法与向下兼容

> 2026-11-04 的用户指令：**优化 Tur 语法**，把 Python / Java / C++ / Zig / Go 的语法吸收
> 进来；**保证 Loment 的 Rust 严格子集时期遗留的功能不被阉割或复杂化**；**全面向下兼容**；
> 新语法即使再改，老语法仍然可以编译，长线不被打断。

**进度**：§4 第 1 项（版次脚手架）已落地 —— `tur2` 进了出厂锁、`FrontUnit.profile` /
`mod.profile` / 自举 `PROF_OFF` 都记下了版次；此时 `tur2` 与 `tur` 的产物逐字节相同
（判据在 `loment_grammar_test` 与 `loment_p8_test` M87）。第 2–5 项按下面的顺序推进。

## 1. 兼容法规：语法按**版次**冻结

Tur 现在的parser是"位置识别"的：**没有关键字表**，`defer` / `and` / `def` 今天都可以是
变量名或函数名。直接把新词变成全局关键字，会把老代码里的同名标识符重新解释 —— 那不是
向下兼容。

所以：

| 声明 | 是什么 | 承诺 |
|---|---|---|
| 不写 / `choose write grammar loment` / `… tur` | **Tur 1**（2026-11 版） | **永久冻结**：新语法不进这一版，旧文件的行为一个字节都不变 |
| `choose write grammar tur2` | **Tur 2**（本版） | 吸收 Python/Zig/Go 的首批语法；Tur 1 的文件**原样**能编 |
| 将来的 `tur3` … | 后续版次 | 同上；每个版次的语义与拼法都冻结 |

规则：

1. **版次只加不改**。Tur 2 里定下的语法，`tur2` 这个名字就永远指它；要改就开 `tur3`。
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

### 2.4 `catch` / `orelse`（Zig）

```rust
let a: u32 = maybe orelse 0;          // Option：None -> 0
let b: u32 = risky() catch 0;         // Result：Err -> 0
```

v1 只做**表达式位置的兜底**，且只用于 `let` / 赋值右侧：

* `opt orelse d`：`opt` 是 `Option<T>`，`None` 取 `d`（`T`）；
* `res catch d`：`res` 是 `Result<T, E>`，`Err(_)` 取 `d`（`T`）；
* 需要看错误值的 `catch |e| { ... }`、需要提前传播的 `catch return` 留给 Tur 3。

## 3. 兼容性判据（这一版的"不打断长线"是可验证的）

1. **Tur 1 回归族**：`loment/examples/**` 与 `loment/selfhost/**` 全部在 `tur` 下照旧编译，
   产物与改动前逐字节相同（已有的 M80/M82/M85 判据就是这一族）。
2. **同义糖**：2.1 的三种写法各一份夹具，`tur` / `tur2` 两版产物逐字节相同。
3. **版次隔离**：一份 Tur 1 文件用 `defer` 作变量名，在 `tur` 下编得过；同一份文件改成
   `tur2` 并以构造位置使用 `defer`，编不过；两份都留判据。
4. **旧声明不变**：`grammar rust` 仍是严格 Rust 面；`loment` / ` tur` 的 `NATIVE_GRAMMARS`
   成员与语义不变。
5. **双实现**：每条新语法在 `tools/lomentc.py` 与 `loment/selfhost/*` 上逐字节一致；
   `loment_rule_parity` 的新规则只增不减。

## 4. 落地顺序（每一笔都可独立回滚）

1. **版次脚手架**：`tur2` 进锁；`front_door` 返回 profile；自举驱动记录 profile；
   两套实现都能"接受 tur2 但不启用任何新语法"（等价于 tur）。判据：`tur2` 的产物与
   `tur` 逐字节相同，`loment`/`tur` 的旧判据全绿。
2. **纯糖**：`def` / `elif` / `and` / `or` / `not`；两套实现；同义糖判据。
3. **`for x in xs`**：parser + checker + codegen（两套）；负例（非数组/切片）有诊断码。
4. **`defer`**：parser + checker + codegen（两套）；LIFO/提前返回/不执行的负例。
5. **`catch` / `orelse`**：parser + checker + codegen（两套）；`Option`/`Result` 两族。

第 3–5 步各自要新增诊断码（`loment_diag.RULES`，只增不改）与
`loment_rule_parity._CASES` 的负例，`BUDGET` 跟着抬。种子在**独立的 `[生成物]` 提交**里
重算；手写与生成物分开。

## 5. 非目标（这一版不做，别拿它们当"没做完"）

* `:=` / `auto` 的类型推断（Tur 3）；
* `class` / `new` / 继承（Tur 3）；
* `comptime`（Tur 3）；
* `chan` / `select` / `go` 的运行时（Tur 3+）；
* 把 `tur2` 变成 `tur` 的默认：**默认永远是 Tur 1**，除非文件头显式选择。
