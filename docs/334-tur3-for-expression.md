# 334 · Tur 2026.3：`for x in <call>`

> 状态：**已落地**（2026-11-10，两套实现）· 版次：`tur2026.3`
> 上游：`docs/296` §5（Tur 3 的语法优化清单）· `standard/LLS.md` §8.10 / §11.2 / §11.6 / §26.3
> 判据：`loment_grammar_test::test_tur2026_3_for_expression`、
> `loment_p8_test::test_m100_for_expression_twin`

---

## 1. 语法

```rust
module forexpr

fn buf(b: [u32]) -> [u32] {
    return b;
}

fn main() -> u32 {
    let xs: [u32; 3] = [1, 2, 3];
    let s: u32 = 0;
    for x in buf(&xs) {      // 源是调用：只求值一次
        s = s + x;
    }
    return s;                // 6
}
```

- 源表达式**求值一次**，落一个临时局部 `__tur2_s.N`，循环体里只读它。
- 返回**数组**或**切片**都收；返回别的类型是错误（参考实现反糖时报，自举
  侧在 codegen 报）。
- `x` 仍是元素拷贝：给 `x` 赋值不写回源。

## 2. v1 的边界：只收调用

`docs/296` §5 的原话是 `for x in <表达式>`，但**自举侧的类型来自 token**：返回类型
是一个真实 token（`fn_ret`），而任意表达式（数组字面量、条件、算术）没有现成的
类型 token，需要"合成类型 token"这种更大的改动（局部表只存 token 下标）。所以
v1 的契约是：

> 源表达式 MUST 是**函数调用**，且返回类型是 `[T; N]` 或 `[T]`。

参考实现里非调用的表达式在反糖时拒（`docs/334` 的 `LomError`）；自举侧同样的形状
根本不会进 `fi_expr` 分支（要求 `IDENT "("`），落到区间 `for` 的解析错上。两边
因此对同一个非法输入给出"拒"，不产生伪一致。

数组字面量、`for x in (xs)`、`for x in a[i]` 都在**后续**条目里，各自要一套类型
合成；这一页不提前承诺。

## 3. 反糖

```
let __tur2_s.N: <返回类型> = <call>;   // 只求值一次
let __tur2_i.N: u32 = 0;
while __tur2_i.N < len(__tur2_s.N) {
    let x: T = __tur2_s.N[__tur2_i.N];
    … 用户体 …
    __tur2_i.N = __tur2_i.N + 1;
}
```

数组返回用常量长度，切片返回用 `slice_len(__tur2_s.N)`。局部表顺序是
`__tur2_s.N`、`__tur2_i.N`、`x` —— 自举 prepass 必须按同一序登记。

`__tur2_s.` 是第四种合成名（`emit_syn` 的 `k=4`），与 `__tur2_i.` 一样带一个源码
写不出来的点号。

## 4. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`for` 分支在 2026.3 且 `in` 之后不是"具名变量 + `{`"时按表达式解析；`{` 紧随时走 `ForEach(src_expr=…)`。`_desugar_foreach` 用 `expr_type` 取调用返回类型，插 `Let(__tur2_s.N, ty, call)` 再走原来的 while 反糖 |
| 自举 checker | `checker.lomt`：`for IDENT in IDENT "(" … ")" "{"` 单独一支；2026.3 以下报 E019；`x` 以"未知类型"进环境（checker 没有调用表达式的一般类型），元素类型由 codegen 定 |
| 自举 codegen | `codegen.lomt`：`fi_expr` 形状检测（`j+4` 是 `(`、配对 `)` 之后是 `{`）；`sty = expr_type(调用)`；先发 `let __tur2_s.N`（`expr_bin` + `store`），再发原来的下标循环；切片源的 `load` 走 `load_syn_var`，数组源的 `gep` 用合成名；`fi_ord` 与 prepass 同步认这一形状 |
| 自举 potato | `potato.lomt` 的 loop 扫描按 `in` 识别，这一形状本来就算 loop，无需改动 |

## 5. 判据

* `loment_grammar_test`：2026.3 下编得过，IR 里有 `__tur2_s.1.addr = alloca
  { ptr, i64 }`，且 `call … @buf` **只出现一次**；`tur2026.2` 钉住时拒。
* `loment_p8_test` M100：同一份程序，参考实现与自举驱动的 IR **逐字节相同**；
  `tur2026.2` 两边都拒（自举侧 rc≠0、stdout 为空）。

行为探针（参考链路 `lomentc --emit-llvm` + `lomelf --target pe`）：§1 那段程序印出
**6**。探针文件不入库。

## 6. 自举成本：镜像的回填表抬了一格

种子从 6,258,831 长到 6,288,971 B 之后，`bootstrap.sh` 的 1/4 步（genesis 链种子）
点名 `the backfill table is full -- raise TB_FIX_MAX`。标签表还有余量
（32,097 / 49,152），所以只抬回填表：**49,152 → 65,536**，`loment/tools/lomelf.lomt`
里 `TB_FIX` 之后的每张表整体后移 `28 × 16,384 = 458,752`；表尾 6,352,672 B，仍在
8 MiB 表区内。genesis 与 `SHA256SUMS` / `provenance.json` 因此重算（生成物提交）。

## 7. 明确不做

* 数组字面量源 `for x in [1, 2, 3]` —— 需要合成类型 token，后续条目；
* 任意表达式源（算术、条件、括号）—— 同上；
* 对 `x` 的借用 / 写回（没有 `mut` 循环变量）；
* `fmt` / `doc` / LSP 对这条拼法没有专门判据：按 token 走不会拒绝，但也没有
  格式化规则；要加时另开一笔（`docs/182` §1.9 的消费者清单）。
