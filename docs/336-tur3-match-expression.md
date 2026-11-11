# 336 · Tur 2026.3：`match` 当表达式（`let` 初值）

> 状态：**已落地**（2026-11-10，两套实现）· 版次：`tur2026.3`
> 上游：`docs/296` §5（Tur 3 的语法优化清单）· `standard/LLS.md` §8.10 / §11.2 / §26.3
> 判据：`loment_grammar_test::test_tur2026_3_match_expression`、
> `loment_p8_test::test_m102_match_expression_twin`

---

## 1. 语法

```rust
module matchexpr

enum D { N, E }

fn f(d: D) -> u32 {
    let x: u32 = match d { D::N => 1, D::E => 2 };
    return x;
}
```

- 臂体各是**一个表达式**，用逗号分隔（最后一条逗号可省）；图案沿用 `match`
  语句那一套：`Enum::V` / `Enum::V(bind)` / `_`。
- v1 只收**`let` 的整个初值**这一格；`return match …`、实参、算术里的 `match` 都拒。
- v1 的被匹配值必须是**一个名字**（`match d`，不是 `match d.field` / `match f(x)`）——
  自举侧的 match 发射器按这个形状取主体。
- 臂体里不能再嵌 `if` / `match` 表达式；空臂表直接拒。

## 2. 反糖

```
let x: T;
match d { D::N => { x = 1; } D::E => { x = 2; } }
```

参考实现里 `MatchExpr` 只存在于解析到反糖之间；降完就是普通的无初值 `let` +
普通 `Match`，每臂的表达式变成一条赋值。两套实现因此能逐字节对拍。

## 3. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`parse_primary` 在 2026.3 且 `allow_matchexpr > 0` 时认 `match`；`parse_matchexpr` 收逗号分隔的单表达式臂；`_desugar_matchexpr` 换成 `Let(None)` + `Match` |
| 自举 checker | `checker.lomt`：`let IDENT : T = match NAME …` 单独一支；2026.3 以下报 E019；主体与每臂体各走一遍 `chk_tex`，载荷绑定以"未知类型"临时进环境 |
| 自举 codegen | `codegen.lomt`：match 发射器改成 `emit_match_at(…, mtgt, mty)`；`mtgt != 0` 时臂体按单表达式发（`expr_bin` + 一条 `store`），臂的结束位置由 `m_arm_end` 认 `,` / `}`；`let` 分支在 `=` 后紧跟 `match` 时走它 |
| 自举 prepass | 局部登记在 `let` 那一支里补：`x` 先登记，随后按臂序登记载荷绑定（与普通 match 同一走法） |

## 4. 判据

* `loment_grammar_test`：2026.3 下 `let x: T = match d { D::N => 1, D::E => 2 };`
  与手写的 `let x: T; match d { D::N => { x = 1; } D::E => { x = 2; } }` **IR 完全相同**；
  `tur2026.2` 拒；`return match …` 也拒。
* `loment_p8_test` M102：一份带 `Option` 载荷绑定、通配臂与 tag-only 枚举的程序，
  参考实现与自举驱动的 IR **逐字节相同**；`tur2026.2` 两边都拒。

行为探针（参考链路 `lomentc --emit-llvm` + `lomelf --target pe`）：
`Some(41)` 过 `Some(v) => v + 1` / `None => 0`，印出 **42**。探针文件不入库。

## 5. 版次

只在 `tur2026.3` 及以后收。`tur2026.1` / `tur2026.2` 里 `let x: T = match …` 仍是
语法错（`期望 ;，得到 …`）；封版哈希判据照旧绿。

## 6. 明确不做

* 任意表达式作被匹配值（字段、调用、嵌套）—— v1 只收名字；
* 臂体是块、带语句的臂 —— v1 只收单表达式；
* 臂体里嵌 `if` / `match` 表达式；
* 字面量图案（`0 => 10`）—— 语言的图案集今天只有枚举变体与 `_`；
* `fmt` / `doc` / LSP 对这条拼法没有专门判据：按 token 走不会拒绝，但也没有
  格式化规则；要加时另开一笔（`docs/182` §1.9 的消费者清单）。
