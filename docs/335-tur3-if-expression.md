# 335 · Tur 2026.3：`if` 当表达式（`let` 初值）

> 状态：**已落地**（2026-11-10，两套实现）· 版次：`tur2026.3`
> 上游：`docs/296` §5（Tur 3 的语法优化清单）· `standard/LLS.md` §8.10 / §11.2 / §11.5 / §26.3
> 判据：`loment_grammar_test::test_tur2026_3_if_expression`、
> `loment_p8_test::test_m101_if_expression_twin`

---

## 1. 语法

```rust
module ifexpr

fn f(c: bool) -> u32 {
    let x: u32 = if c { 10 } else { 20 };
    return x;
}
```

- 两支各是**一个表达式**（没有语句、没有块级 `let`），`else` **必须写**。
- v1 只收**`let` 的整个初值**这一格；`return if …`、`f(if …)`、`1 + if …` 都拒。
- v1 不收 `else if` 与分支里再嵌一个 `if` 表达式（各自会在解析期点名）。

## 2. 反糖

```
let x: T;
if c { x = 10; } else { x = 20; }
```

参考实现里 `IfExpr` 只存在于解析到反糖之间；降完就是普通 `Let`（无初值）+ `If`，
检查、发射、L0/L1/L2、Rust 后端一行都不用认识它。两套实现因此能逐字节对拍。

## 3. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`parse_primary` 在 2026.3 且 `allow_ifexpr > 0` 时认 `if`；`parse_ifexpr` 只收单表达式分支；`_desugar_ifexpr` 在 `_desugar_foreach` **之前**跑，把 `Let(name, ty, IfExpr)` 换成 `Let(name, ty, None)` + `If` |
| 自举 checker | `checker.lomt`：`let IDENT : T = if …` 单独一支；2026.3 以下报 E019；条件与两支各走一遍 `chk_tex`（名字解析）；类型比对留给参考实现的反糖形状 |
| 自举 codegen | `codegen.lomt`：`let` 分支在 `=` 后紧跟 `if` 时走 `let_ifexpr`，按参考实现同一序发条件、`L?_then` / `L?_else` / `L?_end` 三个标签、两支各一条 `store <T> …, ptr %name.addr` |
| 其余 | 局部登记不用改（`let` 的那一条照旧收 `x`）；potato 的扫描不涉及 |

## 4. 判据

* `loment_grammar_test`：2026.3 下 `let x: T = if c { 10 } else { 20 };` 与手写的
  `let x: T; if c { x = 10; } else { x = 20; }` **IR 完全相同**；`tur2026.2` 拒；
  `return if …` 也拒。
* `loment_p8_test` M101：一份带 `&&` 条件、调用臂与 `bool` 类型的程序，参考实现与
  自举驱动的 IR **逐字节相同**；`tur2026.2` 两边都拒。

行为探针（参考链路 `lomentc --emit-llvm` + `lomelf --target pe`）：§1 那段程序印出
**10**。探针文件不入库。

## 5. 版次

只在 `tur2026.3` 及以后收。`tur2026.1` / `tur2026.2` 里 `let x: T = if …` 仍是
语法错（`期望 ;，得到 …`）；封版哈希判据照旧绿。

## 6. 明确不做

* `match` 当表达式 / 臂体当表达式 —— `docs/296` §5 的这一项只做了 `if` 那半，
  `match` 留在后续条目；
* `if` 表达式出现在 `let` 初值以外（`return`、实参、算术里）—— v1 明确拒；
* `else if` 与分支里的嵌套 `if` 表达式 —— v1 明确拒；
* `fmt` / `doc` / LSP 对这条拼法没有专门判据：按 token 走不会拒绝，但也没有
  格式化规则；要加时另开一笔（`docs/182` §1.9 的消费者清单）。
