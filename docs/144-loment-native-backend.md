# 144 · 超长任务：Loment 原生后端 → 自举

> 状态: **进行中**（2026-09-08 启动）· 起点: docs/143 §7 v2/v3 · 工具链: clang 22.1.8（`C:\Program Files\LLVM\bin\clang.exe`）
> 总图: 本文件的 M0–M5 已并入 **docs/145 的 100 里程碑计划**（原生后端 = P3 的 M23–M34）；
> 本文件保留为原生后端专项的取舍与证据记录。
> 一句话: **让 Loment 不再借 Rust 走路——直接产出可执行码，最终用它自己写自己。**

## 1. 为什么是这件事

L1 v1 已能端到端跑（写 → 转译 Rust → rustc → 运行 → 导出 Potato），但"转译到 Rust"
意味着：语义受 Rust 约束、无法脱离 Rust 工具链、也无法为 FujoOS 裸机目标定制。
docs/143 §7 把 **v2 原生后端** 与 **v3 自举** 列为剩余的真活——这是整个语言项目里
唯一还没被"借道"解决的问题。

## 2. 里程碑

| 里程碑 | 内容 | 判据 | 状态 |
|---|---|---|---|
| **M0** | LLVM IR 后端（标量子集：整型/布尔、算术/位运算/比较、if/while/for、调用、常量） | `.lomt` → `.ll` → clang → 可执行；输出与 Rust 路径**逐值一致** | ✅ |
| M1 | 聚合类型 IR：struct（GEP）、定长数组、无载荷/带载荷枚举（tagged union） | demo 的 `Blk`/`Shape`/数组函数在原生路径上跑出同一结果 | ✅ |
| M2 | 裸机目标：`--target x86_64-unknown-none`、无 libc、自定义入口与链接脚本 | 生成的 `.o` 能被 FujoOS 链接脚本吃下并启动 | 部分（`.o` 已出，链接流程未接） |
| M3 | 能力声明的运行时表示：`capability` 编译为内核可检查的域描述 | 与 `kernel/src/capability.rs` 的域模型对齐 | 待做 |
| M4 | 自举：用 Loment 写 Loment 编译器（lexer/parser/类型检查/IR 生成） | Loment 版编译器编译自身并产出同一 `.ll` | 待做 |
| M5 | 切换默认后端，Rust 转译路径降级为对照 | 全部门禁 + 回归在原生路径上通过 | 待做 |

## 3. M0 的取舍（ponytail 记账）

- **借道 LLVM IR，不写机器码后端**：寄存器分配/指令选择交给 clang，M0 只做"结构化
  发射"（alloca/load/store + 基本块），优化交给 `clang -O1`。写机器码后端是自举之后的事。
- **`&&` / `||` 暂不短路**：M0 用 `and`/`or i1`，无短路语义。当前语言里唯一有副作用的是
  函数调用，若调用出现在逻辑右操作数会被求值——已在 docs 记账，M1 起用 phi 修正。
- **函数末尾不可达时发 `unreachable`**：语言允许"并非所有路径都 return"，M0 不为此加
  检查规则（YAGNI），代价是落到该块的执行是 UB。
- **有符号/无符号按声明类型选指令**：`icmp slt/ult`、`sdiv/udiv`、`ashr/lshr`、`srem/urem`。
- **除零/溢出语义**：**2026-10-16 定了**（issue #19 的第 1、3 条；这一行从前写的是
  "M0 不处理，M2 前必须定规则"）——
  * **溢出按模回绕**，而且它是**定义好的**行为，不是 UB：发射出来的 `add`/`sub`/`mul`
    **从不带 `nsw`/`nuw`**（带了才是"溢出即 UB"），判据 `lomentc_test::
    test_arithmetic_is_defined_to_wrap_never_undefined` 钉着这一点。语言没有
    `checked_add` 一类的内建，要判就自己写在算式**之前**。
  * **除零/取模零是 trap**：`b == 0` 时发射器走一个 `call void @__loment_abort()` 的块
    （`__loment_abort` = `llvm.trap` + `unreachable`），所以是**定义好的 abort**，
    不是"看 CPU 心情"（`test_m18_wrap_and_divzero` 钉着 IR 形状）。
  * 两条都写进了用户读的那张表（`SKILL.md` §6.2 第 15/16 条）。

## 4. 验证方式

```bash
# 1) 生成 IR
python tools/lomentc.py loment/examples/native.lomt --emit-llvm loment/build/native.ll
# 2) 与 C 驱动一起编译成原生可执行
"C:\Program Files\LLVM\bin\clang.exe" -O1 -o loment/build/native_exe.exe \
    loment/build/native_driver.c loment/build/native.ll
# 3) 运行，输出须与 Rust 路径一致
./loment/build/native_exe.exe
```

判据：同一组函数在两条路径（Rust 转译 / LLVM IR）上输出**逐值一致**。

### M0 实测（2026-09-08）

```
python tools/lomentc.py loment/examples/native.lomt --emit-llvm loment/build/native.ll   # 3911 B
clang -O1 -o loment/build/native_exe.exe loment/build/native_driver.c loment/build/native.ll
./loment/build/native_exe.exe
55 21 8 45 205 1 21
```

七个函数（fib / gcd / popcount / sum_range / mask_low / in_domain / scaled）在原生路径上的
输出与 Rust 转译路径**逐值一致**。IR 形态：参数与局部统一在入口块 `alloca`（避免循环内反复
分配），`icmp ult/slt` 按声明符号性选择，常量在生成期内联（无全局变量）。
`clang` 会提示一次 `overriding the module target triple`——IR 未声明 triple，由 clang 决定，
属预期。

自检：`lomentc_test` 含 4 项 M0 测试（IR 确定性/关键指令、符号性选指令、拒绝聚合类型、
`--check` 漂移检出）。

## 5. 风险与止损

- **最大风险**：后端吞掉论文与语言维护的时间。→ 硬门禁：每里程碑结束必须全门禁绿
  （`ci.py --static-only` 4/4 + 内核构建 + 一条回归）；论文优先级不变。
- **自举风险**：语言尚无字符串/文件/动态内存，M4 之前必须补这些能力；若 M1–M3 任一
  停摆，M4 自动顺延，不硬推。
- **止损线**：M2 若无法让产物进入 FujoOS 链接流程，回退"IR 后端 + 宿主可执行"定位，
  不再宣称可替换 Rust 路径。
