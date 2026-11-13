# 348 · `args(i)`：进程命令行里的第 i 个参数

> 状态：**已落地**（2026-11-13，两套实现）· 版次：**所有版次**（内建函数不按版次门禁；
> 2026.3 说的是它什么时候加的，与 `str_num` / `str_show` 同一个口径）
> 上游：`docs/296` §5 的语法优化清单（第 5 项，也是清单上最后落的一项）
> 判据：`loment_grammar_test::test_args_builtin`、
> `loment_p8_test::test_m108_args_builtin_twin`

---

## 1. 表面语法

```rust
module demo
choose runtime

fn _start() {
    print(args(0));      // 程序自己的路径
    print(args(1));      // 第一个实参
    print(args(9));      // 越界 —— 空串，不是崩
    syscall4(60, 0, 0, 0);
}
```

| 形状 | 语义 |
|---|---|
| `args(i: u32) -> str` | 命令行里第 `i` 个 NUL 分隔的字段；`0` 是程序自己的路径 |

**为什么带下标、不是裸 `args()`**：Loment 里没有"字符串的数组" —— 结构体字段与切片元素
都是标量面（`docs/299`）。裸 `args()` 要一个 `[str]`，那是另一条线（切片元素类型）。
带下标的形式今天就能落地，而且与 `command_main(argv, argc)` 的 argv 同一种读法。

**越界回空串**：`str` 没有"可能没有"这一档，空串是最不吓人的那个（与 `argc` 之差）。
长度用 `str_len(args(i)) == 0` 判。

## 2. 两侧为什么能用同一段实现

两个目标都提供 `/proc/self/cmdline`：

* **ELF**：真的 procfs，`openat` + `read` 拿到 NUL 分隔的整块；
* **PE**：`tools/lomelf.py` 的垫片把 `GetCommandLineA` 的结果合成成同一个路径
  （`__ws_open_cmd` / `__ws_fill_cmdline`）—— 那份垫片原先就是为 `command_main` 写的。

所以 helper 只有一份：`@.args.get(i32)` 打开那个路径、读进一块 4096 字节的静态缓冲、
数到第 `i` 个 NUL，返回长度并把起点写进 `@.args.out`。

三件必须一起说的：

1. **名字不带 `__loment_` 前缀**：那一串是"发不发整段 freestanding 运行期"的开关
   （`emit_llvm` 里 `"@__loment_" in text_all` 那一句）。带了它，一条只调 `args` 的程序
   会白发出整段运行期 —— 与命令入口桩的 `@.lc.*` 同一条理由。
2. **不回聚合值**：`{ ptr, i64 }` 的返回要让包内链接器按寄存器对拆，那不在它的指令子集里
   （`docs/210` §8）。起点走静态槽，函数只回长度。
3. **静态缓冲、不分配**：`str` 指的是那块缓冲，没有堆、没有根表要登记 —— `args` 因此
   不欠 GC 任何东西。

## 3. 运行期那一维

`args` 在 `_RUNTIME_SITES` 里（自举侧是 `chk_rt_site` 的同一条表）：它走 openat / read /
close 三个系统调用。`no_runtime` 的单元用它会被点名拒（E0208），与 `panic` 同一个口径 ——
"产物里没有运行期"是一个**保证**，这一维往保守那边偏。

## 4. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`BUILTINS["args"]`、`_IR_ARGS`（helper 文本）、`builtin()` 里那四条指令、`_calls_named(mods, ("args",))` 决定发不发 |
| 参考实现 · Rust 后端 | `std::env::args_os().nth(i)` —— CLI 每次编译都会走一遍 Rust 投影（形式对象要它），所以这里不能拒 |
| 自举 checker | `chk_builtin_ret`（str）、`chk_builtin_arity`（1）、`chk_rt_site`（运行期站点）、`is_builtin` 名字表 |
| 自举 codegen | `uses_args` 判据 + `emit_args`（与 `_IR_ARGS` **逐字节相同**）+ 调用点那四条指令 |
| 自举 driver | `rt_phrase` 里那句 `` `args` `` —— `no_runtime` 的报错正文与参考实现同字 |

## 5. 判据

* `loment_grammar_test::test_args_builtin`：编得过、产物里有 `@.args.get` 与 `@.args.buf`；
  实参不是整型、把它当整数用、`no_runtime` 下用它 —— 三种各拒。
* `loment_p8_test::test_m108_args_builtin_twin`：两实现的 IR **逐字节相同**；并把产物链成
  程序、带 `AA BB` 两个实参跑一遍，断言印出来的是 `AA|BB|`（越界那一路回空串）。

## 6. 明确不做

* 裸 `args()` 返回一整块（要 `[str]`，见 §1）；
* 环境变量（`env("NAME")`）—— 同一套 procfs 读法能做，但那是另一条；
* 一次读 4 KiB 缓冲：命令行超过 4 KiB 时最后那个参数会被截断（`read` 的 n 就是上界）；
  要更长的就把缓冲变大 —— 那是一件独立的事；
* PE 上保留 Windows 的引号/转义原样（垫片按"空格分隔串"收拢，与 shell 的分词不同，
  与 `command_main` 拿到的完全一致 —— 这一条是 `docs/223` 早就记下的口径）。
