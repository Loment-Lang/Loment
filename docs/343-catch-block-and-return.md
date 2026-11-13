# 343 · `catch |e| { … }` / `catch return`：看错误值、或原样返回

> 状态：**已落地**（2026-11-12，两套实现）· 版次：Tur 2026.3 及以后
> 上游：`docs/296` §5 的语法优化清单（第 7 项）· 已有形状：`docs/296` §2.4 的
> `x orelse d` / `x catch d`
> 判据：`loment_grammar_test::test_catch_block_and_return`、
> `loment_p8_test::test_m106_catch_block_twin`

---

## 1. 表面语法

`catch` 原先只收一个**兜底值**（`let x: u32 = r catch 0;`）。2026.3 起多收两副形状，
位置不变（**只在 `let` / 赋值的右侧**）：

```rust
module demo
choose runtime

fn may(n: u32) -> Result<u32, u32> {
    if n > 3 { return Result::Err(n); }
    return Result::Ok(n + 1);
}

fn f(n: u32) -> Result<u32, u32> {
    let a: u32 = may(n) catch |e| { e + 100 };   // 失败：用错误载荷算出兜底值
    let b: u32 = may(a) catch return;            // 失败：把错误原样返回
    return Result::Ok(b);
}
```

| 形状 | 语义 |
|---|---|
| `v catch d` | （原有）失败支取 `d`；`d` 必须与成功载荷同型 |
| `v catch \|e\| { EXPR }` | 失败支把**错误载荷**绑到 `e`（一个副本），`EXPR` 给出兜底值；必须与成功载荷同型 |
| `v catch return` | 失败支把错误载荷装回 `Err(…)` 并**从函数返回** —— 函数的返回类型必须就是 `v` 的那个枚举 |

`|e| { EXPR }` 的括号里**恰好一个表达式**（与 `if` / `match` 表达式的臂同一条 v1 限制）；
`e` 的作用域就到那个 `}` 为止。`catch return` 是 `?` 的表达位置版本：`?` 只能在 `let`
初值上做一次早退，`catch return` 是同一件事的显式拼法（`docs/296` §2.4 的 `?` 仍然可用）。

**不做**：块里写语句 / 多个表达式；`catch` 出现在 `let`·赋值右侧以外的位置（与
`orelse` / `catch d` 同一条限制）。

## 2. 两种形状怎么发

两条都长在**同一个 `switch`** 上（成功变体 = 枚举的第一个变体，与前两种 `catch` 共用）：

* **`catch |e| { EXPR }`** —— 失败块里先把 `extractvalue …, 1` 的载荷按错误类型截好、
  存进 `e` 的槽（`%e.addr`，与 `match` 的载荷绑定同一种槽），再用带这个变量的作用域
  求 `EXPR`；合流块 `phi` 两支。
* **`catch return`** —— 失败块里构造 `{ i32 1, i64 <载荷> }`（枚举值就是 `{ tag, i64 }`）
  并 `ret`；合流块的 `phi` 只有**成功块**一个前驱。

绑定槽必须在入口块里开 —— 两边的"局部收集"各加了一格：参考实现是 `_collect_locals`
里的 `Fallback` 分支（**先登记 `let` 的名字、再登记绑定**，与自举侧 token 序一致），
自举侧是 `collect_locals` 里对 `catch |e|` 的一趟扫描（值的类型取 `catch` 前面最近那个
`=` 之后那一段）。

## 3. 类型规则（两套实现同一条）

* 值必须是 **Option/Result 形状**（恰好两个变体、第一个带载荷）—— 与 `catch d` 同一条；
* `catch |e|`：第二个变体**必须带载荷**（否则点名拒）；块里的表达式要与成功载荷同型；
* `catch return`：函数返回类型必须**就是**值那个枚举（否则点名）；枚举的失败变体必须带载荷；
* 老版次（tur2026.1 / 2026.2）不收这两副形状 —— 解析器在 `catch` 后面看到 `|` / `return`
  会照旧把它当兜底表达式的开头，报解析错。

## 4. 判据

* `loment_grammar_test::test_catch_block_and_return`：两条都编得过（产物里有 `%e.addr`
  与一条 `ret { i32, i64 }`）；`catch return` 遇上不同的返回类型点名；tur2026.2 拒。
* `loment_p8_test::test_m106_catch_block_twin`：两实现的 IR **逐字节相同**。
* 行为探针（`lomelf` + WSL，参考与自举各一遍）：`f(1)` 得 3、`f(9)` 经 `catch |e|`
  与 `catch return` 得到 `Err(109)`，主线打 `37`。

## 5. 明确不做

* `catch` 的块里写语句（包括 `return`）—— 与 `if` / `match` 表达式的臂同一条 v1 边界；
* 错误类型不一致时的隐式转换（不比 `Err` 载荷更宽的枚举）；
* `catch` 出现在表达式中间（`f(r catch 0)`）—— 与既有 `orelse` / `catch d` 一致。
