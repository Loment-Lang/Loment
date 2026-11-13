# 347 · `:=` / `auto`：局部变量的类型推断

> 状态：**已落地**（2026-11-13，两套实现）· 版次：Tur 2026.3 及以后
> 上游：`docs/296` §5 的语法优化清单（第 8 项，也是最后一项）
> 判据：`loment_grammar_test::test_auto_type_inference`、
> `loment_p8_test::test_m107_auto_type_twin`、`loment_fmt_test::test_fmt_keeps_the_inference_shorthand_glued`

---

## 1. 表面语法

两种拼法，同一个意思：

```rust
module demo
choose runtime

struct S { a: u32, b: i64 }

fn main() -> u32 {
    let p: S = S { a: 3, b: 4 };
    let n := p.a;            // 字段：类型从 `p` 的声明来
    let k: auto = p.b;       // 与上面同义，另一种拼法
    let j := k as u32;       // 整条初值是一个 `as T` 转换
    let t: auto = true;      // bool 字面量
    print_num(n + j);
    if t { print_num(0); }
    return 0;
}
```

`:=` 必须**粘着写**（`:` 与 `=` 之间没有空白）：`let x: = 1;` 仍然是"`: ` 后面缺类型"
的语法错。这一条与 `docs/341` 的 `f"…"` 是同一个姿态 —— 一条读法的两个 token 不能拆。

## 2. 收哪些初值

v1 的规则一句话：**初值必须把类型写在源里**。

| 初值 | 收 | 类型来自 |
|---|---|---|
| 局部 / 形参（`let y := x;`） | ✓ | 那个变量的声明 |
| 字段链（`let y := p.b;` / `x.f.g`） | ✓ | 结构体字段的声明 |
| 整条初值是一个转换（`let y := k as u32;`） | ✓ | `as` 后面那个类型 |
| `true` / `false` | ✓ | `bool` |
| 字面量（`let y := 5;`） | ✗ | 整数字面量没有类型（宽度由上下文定） |
| 调用（`let y := f();` / `str_len(s)`） | ✗ | 见下 |
| 运算（`let y := a + 1;`） | ✗ | 见下 |
| `if` / `match` 表达式 | ✗ | 装载期就反糖，没有可赋的类型 |
| 模块级常量（`let y := K;`） | ✗ | 发射期看不见常量类型（见下） |
| 别的（借用、结构体/数组字面量、`orelse`/`catch`） | ✗ | — |

**为什么调用与运算也不收**（它们其实有类型）：类型系统给得出答案，但其中几种来自
**源里没有 token 的类型** —— `alloc` 这类返回 `ptr` 的内建、`syscall*` 的 `i64`。
参考实现的 `expr_type` 能说出 `"ptr"`，自举侧的 `expr_type` 只能回"未知"（没有那个词
可指），于是同一份源会一边过、一边不过 —— 那是最难查的一类分叉。v1 的门槛因此定在
"类型出现在源里"，两边照 token 判，逐字一致。

**为什么模块级常量也不收**：检查期的作用域里有它，而两套实现的发射期都看不见常量
类型（自举的 `var_type` 只查形参与局部；参考的 codegen 那份 scope 同样没有常量）。
收了就会在自举侧落成 `u32`，而参考侧是常量的类型。

## 3. 两套实现

推断在三个地方发生，但只有**一个规则**：

| 侧 | 落点 |
|---|---|
| 参考实现 · 词法 | `lomc.lex` 不合并 `:=` —— `:` 与 `=` 是两个 token，相邻判定用 `off`/`len` |
| 参考实现 · 解析 | `Parser.stmt` 的 `let` 分支：粘着的 `:` `=` → 类型写成 `"auto"`；`auto` 接 `if`/`match` 在**解析期**就拒 |
| 参考实现 · 检查 | `check()` 的 `Let` 分支：`_auto_type` 推不出就报；推得出就把类型**写回 `s.type`**（下游照旧读它） |
| 参考实现 · 发射 | `_collect_locals` 与 `_Ir.stmt`：检查跑在 `prepare()` 的**副本**上，所以发射这一侧要**再推一遍** |
| 自举 checker | `let` 分支：粘着的 `:=` / `auto` 认出来 → `chk_auto_ok`（形状）+ `chk_tex`（类型）各判一次；**走位扫描**（移动、借用、`check_type`）各补一格，免得把 `:=` 的 `=` 当类型 |
| 自举 codegen | `let` 分支与 `collect_locals`：类型 token 换成 `expr_type` 回的那一个（源里某个类型 token 的下标，或 `u32`/`bool` 的哨兵） |
| 两个格式化器 | 粘着的 `:` `=` 是一格：`:=` 内部不留空格，`:` **前面留一个**（`x := n`，不是 `x:= n`） |

推断的落点两侧不同（参考改写类型串，自举换类型 token），但都在同一条 `store` 上、同
一条 `expr_bin` 上用 —— 产物逐字节相同（M107）。

## 4. 诊断

| 码 | 消息 | 何时 |
|---|---|---|
| `E0215` | `` `auto` 推断不出 X 的类型`` | 初值形状不在 §2 的表里，或根本没有初值（`let x: auto;`） |
| `E0216` | `` `auto` 不能接 `if` / `match` 表达式`` | 初值是 `if` / `match` 表达式 |

## 5. 判据

* `loment_grammar_test::test_auto_type_inference`：推断写法与显式写法的 IR
  **逐字节相同**；六种不收的初值各拒一条；`let x: = 1` 仍是语法错；tur2026.2 拒 `:=`。
* `loment_p8_test::test_m107_auto_type_twin`：两套实现的 IR **逐字节相同**。
* `loment_fmt_test::test_fmt_keeps_the_inference_shorthand_glued`：格式化后 `:=` 仍是
  一条读法、且编得过（自举那侧由 `test_loment_fmt_matches_python` 逐字节对拍）。

## 6. 明确不做

* 从**调用返回类型**推断（`let n := str_len(s);`）—— 见 §2 的分叉理由；
* 从**模块常量**推断；
* `auto` 接 `if` / `match` 表达式（要写显式类型；反糖之后那份声明没有可赋的类型）；
* 循环/闭包里的"最小类型"这类全局推断 —— 这条推断是**逐条、只看初值自己**的。
