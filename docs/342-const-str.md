# 342 · `const NAME: str = "…"`：命名字符串

> 状态：**已落地**（2026-11-12，两套实现）· 版次：Tur 2026.3 及以后
> 上游：`docs/296` §5 的语法优化清单（第 6 项）· `docs/332` §5 的顺序表
> 判据：`loment_grammar_test::test_const_str`、`loment_p8_test::test_m105_const_str_twin`

---

## 1. 表面语法

```rust
module demo
choose runtime

const GREETING: str = "hello";
const N: u32 = 42;

fn f(n: u32) -> str {
    let a: str = str_concat(GREETING, GREETING);   // hellohello
    let b: str = f"{GREETING}-{N}-{n}";             // hello-42-7
    return a;
}
```

- **只收字符串字面量**（不是任意表达式、不是拼接、不是另一个常量）—— 与整型常量
  "只吃整数字面量"同一条形状；
- 只有 Tur 2026.3+ 收：旧版次的 `const` 仍然只吃整数字面量，写字符串照旧是解析错；
- 用在**任何表达式位置**都行（实参、插值孔、`str_concat`、`str_len`、`return`）；
- 每个**使用点**各发一条字符串常量（`.str.<fn>.<n>`），与普通字面量同一条路 ——
  常量是**编译期内联**，不是运行期对象。

## 2. 为什么不是"运行时读一块内存"

`const` 在这个语言里一直是**编译期文本替换**（整型常量在生成期直接换成十进制
字面量）。字符串常量沿用同一条：类型检查时它就是一个 `str`，发射时它就是那个
字面量。于是它**不需要**新的运行期表示、不需要 `.data` 里的一块地址、也不会
引出初始化顺序一类的问题。

## 3. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `parse_const` 收字符串（版次门）；`check()` 放行 `str` 类型（`docs/342` 的条件：2026.3+ 且值是字符串）；`_Ir.expr(Ident)` 的常量分支多一条 `str` 支路，与 `StrLit` 共用 `_str_lit_ir`（`.str` 编号按出现顺序）；Rust 后端发 `&'static str` |
| 自举 checker | `const` 那一支：类型是 `str` 且 `prof >= 2` 也算合格（否则照旧报细分码 57） |
| 自举 codegen | `find_const` 的**值 token** 是字符串时，`expr_atom` 走与字面量同一段发射；`emit_str_globals` 与 `strings_needed` 都多认一类"字符串常量的使用点"；`expr_type_atom` 的类型回退补一格"常量"（参考实现的 `expr_type` 读的是 checker 的 scope，常量与局部同表） |
| Potato | 值的形状从"JSON 数字"扩成"整型 → 数字 / `str` → 字符串"：`tools/potato.py` 与 `loment/tools/lompotato.lomt` 各一条；自举侧表单写出用 `pt_q_dec`（解码后再 JSON 转义，与 `json.dumps` 同字节） |

**自举侧的准备扫描顺序**：常量表原先在 `strings_needed` **之后**才填，而新的
"使用点也算字符串"要靠 `find_const` —— 于是把常量表那一趟挪到 `strings_needed`
之前（同一个函数里，前后不依赖）。

## 4. 判据

* `loment_grammar_test::test_const_str`：编得过、`hello` 出现三次（两次
  `str_concat` 实参 + 一次插值孔）、`-` 出现两次、整数常量仍按十进制内联；
  `tur2026.2` 收不了它。
* `loment_p8_test::test_m105_const_str_twin`：两实现的 IR **逐字节相同**
  （含 `.str` 编号与全局段顺序）。
* 行为探针（`lomelf` + WSL，参考链路）：印出 `hello-42-7hellohello`。

## 5. 明确不做

* 常量值不是字面量（`const X: str = GREETING;`、`const X: str = "a" + "b";`）；
* 字符串常量之间的比较/拼接语法糖 —— 用 `str_eq` / `str_concat`；
* `const` 的可见性（`pub const`）行为不变：跨单元用**整数**常量照旧，
  字符串常量同样按 `pub` 导出 —— 但引用点在**其它单元**时，使用点内联依然成立
  （字符串值在单元合并后的 token 流里找得到）。
