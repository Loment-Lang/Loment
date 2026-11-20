# 312 · 默认版次 = 最新版 `tur2026.3`，以及"版次不静默"

> 状态：**已落地**（2026-11-06）· 上游：`docs/296`（版次与首批语法）、
> `docs/305`（2026.2 封版与 2026.3 开版）、`docs/309`（`lgc`，2026.3 首项新特性）
> · 落点：`tools/potato_from.py` 的 `DEFAULT_EDITION` / `EDITION_COLLISIONS`、
> `loment/selfhost/driver.lomt` 的 `PROF_DEFAULT` / `coll_scan`、诊断 **E040**
>
> 一句话：**没写 `choose write grammar` 的源一律按最新版次读**；旧读法仍可显式钉住。
> 新版次与旧标识符撞车时，**拒编译**，绝不静默改义。

---

## 1. 决定

2026-11-06 用户裁定：**Loment 永远用最新的那一版 Tur，不是最老的。**

旧口径是"没声明 = Tur 1，永远不变"（`docs/296` §1.1）。它把最保守的读法给了
**新写的代码**，而 Tur 1 缺掉的正是今天要用的那批构造 —— `def` / `for x in xs` /
`break` / `continue` / `orelse` / `catch` / `defer` / `print_num` / `lgc`。结果是
每一份新文件都得先背一行声明，否则写出来的东西在默认读法下根本不存在。

新口径：

| 源里写的 | 读作 |
|---|---|
| 什么都不写 | `DEFAULT_EDITION` = `TUR_EDITIONS[-1]`（今天 `tur2026.3`） |
| `choose write grammar loment` | 同上（`loment` 是语言名，不钉版次） |
| `choose write grammar tur3` / `tur2026.3` | `tur2026.3` |
| `choose write grammar tur2` / `tur2026.2` | `tur2026.2` |
| `choose write grammar tur` / `tur1` / `tur2026.1` | `tur2026.1`（**显式**钉旧读法） |

`tur` / `tur1` 仍是历史拼法，**不跟默认走** —— 写了它们就是作者明确要旧读法。
`loment` 跟默认走：它不是版次名。

## 2. "开新版次会静默改义"是怎么解决的

这是旧口径唯一的正当理由，也是这次必须一起解决的问题。**不靠退回旧默认，靠两道技术闸。**

### 2.1 只加不改的构造必须"位置守卫"

新版次只加语法，不加语义。对**本来不是合法写法**的拼法，旧源里不可能出现，
所以加了也不影响旧源。已落地的位置守卫：

* `def` 只认顶层、且后面是 `标识符 (`；
* `and` / `or` 只在**算子位置**（左边能结束表达式、右边能开始表达式）；
* `not` 只在**前缀位置**，且左边不是 `.`、不是表达式结尾；
* `elif` 只在 `}` 之后；
* `orelse` / `catch` 只在一条表达式**紧跟**时；
* 裸 `return;` 只在 `return` 后面直接是 `;` 时。

以上都在前门（`potato_from._tur2_sugar` 与自举 `tur2_sugar`）里，**名字位置一个词不动**。

### 2.2 会与旧标识符撞车的词：登记 + 拒编译

有些新语法**必须**用旧版次里合法的标识符（`break` / `continue` / `defer` /
`print_num` / `lgc`）。这类拼法在旧版次里可以是变量名、函数名、参数名；默认切到
新版次以后，同一份源会**静默改义**：

```rust
module m

fn main() -> u32 {
    let break: u32 = 1;
    let c: u32 = 3;
    while c > 0 {
        break;          // Tur 1：一条空表达式语句；2026.2 起：退出循环
        c = c - 1;
    }
    return 0;
}
```

对策是**登记表 + 前门闸**：

```rust
EDITION_COLLISIONS = {
    "tur2026.2": ("break", "continue", "defer", "print_num"),
    "tur2026.3": ("lgc",),
}
```

* 一份**没有把读法钉在生效版次上**的源（没写声明，或写的是旧版次），
  如果在**声明位置**用了这些名字 —— `let X` / `const X` / `fn X` / `for X in`，
  或 `(` / `,` 后紧跟 `X:` 的参数位 —— **拒编译**，报 **E040**。
* **用**它们不触发：循环里的 `break;` 正是新版次要的写法。
* 修法只有两条，诊断里都写清：**改名**，或**在文件头钉旧版次**
  （`choose write grammar tur2026.1` / `tur2026.2`）。

闸在装载层，两个实现各一处：参考实现 `lomentc.load`、自举 `driver.load_file`。
它们用同一张登记表（自举侧是 `coll_scan` 的硬编码孪生；新增版次要一起改）。

### 2.3 开版次时必须回答"有没有撞车"

判据 `loment_grammar_test::test_every_open_edition_declares_its_collision_set`
要求：**每个未封版的版次都必须在 `EDITION_COLLISIONS` 里出现**，空也要写成 `()`。
这不是风格检查 —— 那张表就是闸的输入；漏登记 = 默认读法会静默改义，
而那是"默认 = 最新"唯一不能接受的事。

## 3. 落点清单

| 面 | 落点 |
|---|---|
| 参考实现 | `potato_from.DEFAULT_EDITION`、`FrontUnit` 缺省、前门 `profile=`、`lomentc` 的全部 `profile=` 缺省 |
| 自举 | `driver.lomt` 的 `PROF_DEFAULT = 2`；`tur` / `tur1` / `tur2026.1` 显式压回 0；编译期提示默认 -> `tur2026.3` |
| 诊断 | **E040**（`loment_diag.RULES`），两套消息同一张卡片 |
| 生成物 | `loment/tools/surface_data.lomt`（`--dump-surface` 重生成） |
| 判据 | `loment_grammar_test` 的三条新判据；`loment_p8_test` M87 期望与 `tur3` 夹具 |

## 4. 明确不做的

* **不给 `loment new` 盖章**。新文件不写声明，于是**永远**跟着默认走 —— 这才符合
  "永远用最新"。要钉住旧读法的人自己写那一行。
* **不引入 lock 文件**。默认 + 碰撞闸已经覆盖"静默改义"；再加一层项目锁会把
  "新文件也用最新"这条一起锁死。
* **不改 `tur` 的含义**。它仍是 Tur 1 的历史拼法；`tur1` / `tur2026.1` 与它同义。

## 5. 判据

| 判据 | 钉住什么 |
|---|---|
| `test_the_default_edition_is_the_newest_open_one` | 默认 == `TUR_EDITIONS[-1]`；`loment` 跟默认；`tur` / `tur1` 仍钉 Tur 1；默认下 `def` / `and` 真的被抹成基础拼法 |
| `test_collision_names_are_refused_when_the_reading_is_not_pinned` | `let break` / `let lgc` 无声明 -> 拒；显式钉旧版次 -> 照旧编；循环里**用** `break;` 不拒 |
| `test_every_open_edition_declares_its_collision_set` | 每个未封版次都有碰撞登记（空元组也算） |
| `loment_p8_test` M87 | 自举侧：`plain` / `loment` -> `tur2026.3`，`tur3` / `tur2026.3` -> `tur2026.3`，其余按显式版次 |
