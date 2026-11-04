# 295 · Tur：本语言自己的语法，以及 `print` / `fn main`

> 2026-11-03 的用户裁定：
>
> * **`choose write grammar rust` 保持严格 Rust 子集，不做修改；`rust` 和其他几种语法一样。**
> * **Tur 是本语言自己的正式语法，从 Rust 直接修改。**
> * **之前开发的非 Rust 语法，全部归到 Tur 名下。**
>
> 这条把 2026-09-22 的裁定（"rust 语法是 Loment 基础语法，不需要翻译"）
> **正式改写**为：原生语法有了自己的名字 **Tur**，`rust` 退回成一门**表层语法**。

## 1. 语法面的分工

| 读法 | 是什么 | 走哪条路 |
|---|---|---|
| 没写声明 / `choose write grammar loment` / `choose write grammar tur` | **Tur** —— 本语言自己的完整语法 | 原生 lexer/parser，**没有翻译器** |
| `choose write grammar rust` / `.rs` | 一门**表层语法**：严格的 Rust | `from_rust`（抽接口；没有正文翻译） |
| `choose write grammar c|python|java|csharp|cpp|go` | 别的表层语法 | 各自的翻译器 |

Tur 的完整定义 = **Rust 直接修改出来的底子** + **本语言自己的构造**：

* 底子（与 Rust 同拼法）：`fn` / `let` / 控制流 / 表达式 / 类型语法；
* 本语言加的：`module`、`choose`、`capability`、`guard`、`extern fn`、`comefor`、
  `print`、`fn main` 入口。

`loment` 与 `tur` 是**同一个原生语法**的两个拼法；`loment` 作为默认拼法保留。

**`rust` 为什么必须退出去**：只要 `rust` 还是"原生读法"，我们给 Tur 加的每一样东西
都会落到"`grammar rust` 是严格 Rust 子集"这条承诺上。退回表层语法之后，那条承诺由
**读法本身**保证 —— `from_rust` 读的就是 Rust，Tur 的修改漏不过去。完整正文的
`choose write grammar rust` 因此会被拒（与其它"只有接口抽取、没有正文翻译"的写法
同一条失败模式）。

## 2. Hello world

```rust
module hello

fn main() {
    print("hello from Loment\n");
}
```

三件事：

1. **`print(s: str) -> u32`** —— 一个内建，落地就是 `write(1, ptr, len)`。它不分配、
   不需要运行期，`no_runtime` 下可用；返回写出的字节数（截成 `u32`）。
2. **`fn main()`** —— 用户程序的正常入口：**无参、无返回类型**。链接器的入口桩
   （`lomelf`，ELF 与 PE 两条）在找不到 `_start` 时改用 `main`，调用它、然后 `exit(0)`。
   `_start` 仍然优先（裸机/内核那一路不受影响）；两者都在时 `_start` 赢。
3. **`main` 不许有形参** —— 入口桩按无参 ABI 调它，带形参的 `main` 会读到寄存器残留值。
   这是**静默错**，所以两个链接器都当场拒（Python 的 `_entry_name` / 自举侧的
   `entry_bare`）。

`_start` 的旧写法仍然有效；用外部 `-Wl,-e,_start` 链接时，它仍然要自己
`syscall4(60, …)` 退出（没有返回地址）。`loment run` 走包内链接器，所以 `main` 返回
就是退出 0。

## 3. 落地清单（两套实现 + 登记处）

* `tools/lomentc.py`：`BUILTINS` 加 `print`；IR 降级发 `extractvalue` ×2 +
  `ptrtoint` + `write` syscall + `trunc`；Rust 转译路径同样落成 syscall（`no_std` 友好）。
* `loment/selfhost/checker.lomt`：`is_builtin` / `chk_builtin_arity` /
  `chk_builtin_arg` 三处同步。
* `loment/selfhost/codegen.lomt`：与参考实现**逐字节相同**的 `print` 降级。
* `tools/lomelf.py` 与 `loment/tools/lomelf.lomt`：入口名查找加 `main` 回退；两个形态
  （ELF / PE）共用同一条规则。
* **边界计数**：`print` 是一次机调用，记进 `boundary.syscalls`；
  `potato.BOUNDARY_SYSCALLS` / `BOUNDARY_PTR_TRANSFORMS` 是显式两张表（不再按名字前缀猜）。
  参考侧 `_machine_call_sites` 给 `print` 填号 1（file 面），自举侧 `pt_surf_print` 对齐。
* 语法登记处：`potato.GRAMMARS` 加 `tur`；`potato_from.GRAMMAR_ALIASES` 加 `tur`；
  `NATIVE_GRAMMARS = ("loment", "tur")`；自举驱动 `strip_grammar_decl` 收 `tur`；
  `_strip_tur_mut`（原 `_strip_rust_mut`）只作用于 `tur`。

## 4. 下游影响

* **`grammar rust` 的完整程序不再可编**：它现在和 `.rs` 一样只走接口抽取。这是一次
  **可见的行为回退**，也是这次裁定的目的（严格子集由读法保证）。现存的
  `examples/multisyntax-projects/rust/` 已迁到 `tur/`，声明改成 `tur`。
* **表单对象**：`grammar` 字段的取值域多了 `tur`；`boundary.syscalls` 会把 `print`
  数进去。任何手写/缓存的 v7+ 对象如果用了 `print`，计数要跟着变（当前语料里没有）。
* **hello world 的产物变了**：`print` 发一条 `write` syscall；入口从 `_start` 变成
  `main` → 链接器合成桩。两者都仍产出同样的 8 KB 静态 ELF。
