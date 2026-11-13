# 337 · `str_num(n) -> str`：整型十进制转字符串

> 状态：**已落地**（2026-11-11，两套实现）· 版次：全版次的内建（累积命名空间）
> 上游：`docs/296` §5（字符串插值 / 格式化那一项的前置）· `standard/LLS.md` §16 内建表
> 判据：`loment_grammar_test::test_str_num_builtin`、
> `loment_p8_test::test_m103_str_num_twin`

---

## 1. 签名与语义

```rust
module demo
choose runtime

fn f(n: u32) -> str {
    let s: str = str_num(n);     // 0 -> "0"；10 -> "10"；4294967295 -> "4294967295"
    return s;
}
```

- 实参是**任意整型**（`u8`…`u64`、`i8`…`i64`；整型字面量也可以）；
  无符号按无符号十进制输出，有符号带 `-`；没有前导零。
- 返回值是 `str`（`{ ptr, i64 }` 视图），**落在运行期的 arena 上**，与 `str_concat`
  同一条分配路径（`__loment_alloc` 的计数、`gc_auto` 的触发钩子都照旧）。
- 它**把运行期拖进产物**：默认的 `no_runtime` 会在编译期点名拒
  （与 `print_num` / `str_concat` 同一条保证）。要它就在根单元写 `choose runtime`。

## 2. 为什么先做它

`docs/296` §5 的下一项是字符串插值 / 格式化。语言里此前**没有把整数变成 `str` 的
构造**：`print_num` 只写 stdout，`num_to_dec` 只往调用方给的缓冲区写字节，而 `str`
本身没有"从 `(ptr, len)` 构造"的语法。插值若没有转字符串这一步，就只能插 `str`
类型的孔。于是先把这一步落成内建，插值随后接在它上面。

## 3. 两套实现

| 侧 | 落点 |
|---|---|
| 参考实现 | `tools/lomentc.py`：`str_num` 的任意整型检查；`builtin()` 里按符号 `zext`/`sext` 到 i64、`alloc_ir("24")` + 三实参 `call i32 @__loment_num_to_dec` + 两次 `insertvalue`；`_IR_NUM_TO_DEC` 只在产物文本出现该符号时追加 |
| 自举 checker | `checker.lomt`：`is_builtin` 名字表、`chk_builtin_ret`（str）、arity 1、`chk_rt_site`（紧跟 `(` 才算运行期构造） |
| 自举 codegen | `codegen.lomt`：`str_num` 的发射分支（按实参类型 `zext`/`sext` 到 i64、`emit_alloc_lit` 固定 24 字节 + 同一串调用/插入）；`uses_str_num` 为真时 `emit_num_to_dec` 追加与参考逐字节相同的 helper |
| 别的层 | `_RUNTIME_SITES` / `_PKG_FEATURES` / 自举的 `runtime_needed` / `heap_needed` 都把它算作运行期构造；L2 的"体内不许 `str_concat`"规则同时收 `str_num`（它也落堆）；Rust 后端用 `format!("{}", …)` |

## 4. 判据

* `loment_grammar_test`：`choose runtime` 下编得过，产物里有
  `call i32 @__loment_num_to_dec` 与它的定义；改成 `choose no_runtime` 后
  `check()` 点名拒（消息里同时有 `no_runtime` 与 `str_num`）。
* `loment_p8_test` M103：`f(n) -> str` 与 `g(n) -> str_len` 两个用例，参考实现与
  自举驱动的 IR **逐字节相同**（含 helper 文本、临时编号与堆计数的顺序）。

行为探针（参考链路 `lomentc --emit-llvm` + `lomelf --target pe`）：
`str_num(0)` / `str_num(10)` / `str_num(4294967295)` 分别印出 `0` / `10` /
`4294967295`，与 `str_concat` 组合印出 `7-42`。

## 5. 明确不做

* 有符号数的 `INT64_MIN` 边界（取负会回绕）—— 今天的十进制路径按位处理，不做饱和；

* 宽度 / 进制 / 补零等格式化参数 —— 插值和格式化的设计一起做；
* `hex` / 浮点 —— 语言没有浮点，十六进制已有 `num_to_hex` 库函数。
