# 330 · Tur 2026.3：`for i, x in xs`

> 状态：**已落地**（2026-11-10，两套实现）· 版次：`tur2026.3`
> 上游：`docs/296` §5（Tur 3 的语法优化清单）· `standard/LLS.md` §8.10 / §11.2 / §11.6 / §26.3
> 判据：`loment_grammar_test::test_tur2026_3_for_index`、
> `loment_p8_test::test_m99_for_index_twin`

---

## 1. 语法

```rust
module foridx
choose runtime

fn main() -> u32 {
    let xs: [u32; 4] = [5, 6, 7, 8];
    let s: u32 = 0;
    for i, x in xs {
        s = s + i * 10 + x;     // (0,5) (1,6) (2,7) (3,8) -> 5+16+27+38
    }
    print_num(s);               // 86
    return s;
}
```

- 两个名字都是**拷贝**：`i` 是 `u32` 下标，从 0 起、每轮加一；`x` 是元素拷贝。
- 给 `i` 或 `x` 赋值**不写回**数组，也不改变下一轮的下标 —— 与 `for x in xs` 的
  元素拷贝同一条规则。
- 源仍限**具名数组或切片**（`for x in <表达式>` 没做，见 §6）。

## 2. 反糖

与参考实现同形，因此两套实现可以逐字节对拍：

```
let __tur2_i.N: u32 = 0;
while __tur2_i.N < 长度 {
    let i: u32 = __tur2_i.N;   // 体首第一条
    let x: T   = xs[__tur2_i.N];  // 体首第二条
    … 用户体 …
    __tur2_i.N = __tur2_i.N + 1;
}
```

局部表顺序是 `__tur2_i.N`、`i`、`x` —— 自举侧的 prepass 必须按同一序登记，否则
SSA 编号整体错位（`loment_p8_test` M99 钉住）。

## 3. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`parse_stmt` 的 `for` 分支在 `profile in TUR3_PLUS` 且见到 `,` 时把第一个标识符记成 `ForEach.idx`；`_desugar_foreach` 在 `while` 前发 `let __tur2_i.N`、体首按序发 `let i` / `let x` |
| 自举 checker | `checker.lomt`：`for IDENT , IDENT in IDENT {` 的六 token 形状单独一支；`prof < 2` 报 E019；`i` 进环境为 `TY_U32`，`x` 为元素类型 |
| 自举 codegen | `codegen.lomt`：`for` 发射器先按 token 形状判 `fi_new`（`j+1` 是标识符、`j+2` 是 `,`、`j+3` 是标识符、`j+4` 是 `in`、`j+5` 是标识符、`j+6` 是 `{`），再走同一段 while 发射；`wbody` 之后先发 `i` 的绑定再发 `x` 的绑定；局部 prepass 与 `fi_ord` 同步认两种形状；`potato.lomt` 的 loop 扫描也认两种 |
| 自举下标类型 | 用户写的 `i` 记 `CG_TY_U64`（无 token 的 u32 哨兵），**不能**记 `SYN_U32` —— 后者与 UNIT 同值（`4294967295`），`emit_ty` 会印成 `void`，下标就成了 `load void` |

## 4. 判据

* `loment_grammar_test`：2026.3 下编得过，`%i.addr = alloca i32`、`x.addr` 存在且
  有 `load i32, ptr %i.addr`；`tur2026.2` 钉住时**拒**（`期望 in，得到 ','`）。
* `loment_p8_test` M99：同一份程序，参考实现与自举驱动的 IR **逐字节相同**；
  `tur2026.2` 两边都拒（自举侧 rc≠0、stdout 为空）。

行为探针（参考链路 `lomentc --emit-llvm` + `lomelf --target pe`，Windows 上直接跑）：
§1 那段程序印出 **86**，即 `(0*10+5)+(1*10+6)+(2*10+7)+(3*10+8)`。探针文件不入库；
门禁里钉住行为的是 M99 的 IR 逐字节对拍，不是这个数。

## 5. 版次

只在 `tur2026.3` 及以后收。`tur2026.1` / `tur2026.2` 里 `for i, x in xs` 仍是语法错；
两个封版版次一个字节都不动。封版哈希判据（`test_tur2026_2_is_frozen`）照旧绿。

## 6. 明确不做

* `for x in <任意表达式>` —— 仍在 `docs/329` §5 的清单上，下一项；
* `for &x in xs` / `for &mut x in xs` —— 借用循环变量不在这一笔；
* 对 `i` / `x` 的写回语法（没有 `mut` 循环变量）；
* `fmt` / `doc` / LSP 对这条拼法没有专门判据：按 token 走不会拒绝，但也没有
  格式化规则；要加时另开一笔（`docs/182` §1.9 的消费者清单）。
