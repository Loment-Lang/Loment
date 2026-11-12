# 145 · Loment 100 里程碑计划

> 状态: **提案 / 执行中**（2026-09-08）· 定位: 超长任务总图（docs/140 三层 → docs/143 L1 → docs/144 原生后端）
>
> 当前进度见 **`docs/154`**（状态矩阵，由 `tools/loment_status.py` 从本文生成）。
> **给内核线的交接单见 `docs/155`**（内核侧还要做什么、跨线 ABI、产物清单、分工纪律）。
> 纪律: 与 FujoOS 同款——**每个里程碑必须绑定一条可复现判据**，证据写进 docs，门禁不全绿不算完成。
> 基线（已完成，记作 M0）: L0 接口层（`tools/lomc.py` + 3 单源 + 审计 + CI 门禁）、Potato v0
> （`tools/potato.py`）、L1 v1（`tools/lomentc.py`，转译 Rust 端到端）、**原生后端 M0**（LLVM IR 标量子集）。
> M1–M100 为待办；每个里程碑独立可验收，允许调整顺序，不允许跳过判据。

## 0. 全局门禁（每个里程碑结束都要跑）

```
python tools/ci.py --static-only                 # 11/11
python tools/fuic.py --check                     # .fuc 逐字节
python tools/lom_spec_emit.py --check            # spec.json 双副本
cd kernel && cargo build --release
FUJO_MON_PORT=14568 FUJO_SER_PORT=14001 python tools/fujoregress.py --only 0
```

## P1 · 语言核心完备（M1–M12）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M1 | 字符串字面量与 UTF-8 字节视图 | `let s: str = "abc";` 转译/IR 双路径输出一致 | ✅ |
| M2 | 字符串操作（`len`/`eq`/`concat`/切片视图） | 三个操作的双路径逐值一致 | ✅（`str_len`/`str_eq`/`str_concat`/`str_byte`；`test_m2_concat_dual_path_runs_equal` 真的跑 rustc 与 clang 两边比输出） |
| M3 | 只读切片 `&[T]` | 函数参数传切片，IR 用 `{ptr,len}` | ✅ |
| M4 | 可变切片 `&mut [T]` | 原地写入经双路径一致 | ✅ |
| M5 | 借用检查 v0（最小规则：不别名可变借用） | 3 个正例通过 + 3 个负例报错 | ✅ |
| M6 | 泛型函数（单态化） | `fn max<T>(a: T, b: T) -> T` 双路径一致 | ✅ |
| M7 | 泛型 struct/enum | `Pair<u32>` 双路径一致 | ✅ |
| M8 | 接口/trait（静态派发） | 两个实现通过同一接口调用 | ✅ |
| M9 | 错误处理 `Result` + `?` | 嵌套调用短路语义正确 | ✅ |
| M10 | 内建 `Option`/`Result` 与 match 糖 | `if let` 形式解析并转译 | ✅ |
| M11 | 模块/包系统（多目录、路径解析） | 三目录工程编译通过 | ✅ |
| M12 | 可见性 `pub` 与 API 边界 | 私有符号跨模块访问报错 | ✅ |

### P1 证据（2026-09-08，M1/M2）

```
loment/examples/native_str.lomt
  Rust 路径 (rustc):  5 1 0 1 0 90 99
  IR 路径  (clang):   5 1 0 1 0 90 99   → diff 逐值一致
```

`str` = `{ ptr, i64 }`（UTF-8 字节视图）；字面量 → 模块级 `private constant [N x i8]`；
`str_len` → `extractvalue 1 + trunc`；`str_eq` → 长度比较 + `memcmp`（长度不等直接 false，
避免越界读）；`str_byte` → GEP + `zext i8`。Rust 路径分别降级为 `.len()` / `==` / `.as_bytes()[i]`。

### P1 证据补充（2026-09-11，M2 尾项 `str_concat`）

```
python tools/lomentc_test.py       # 90/90
# 双路径一致: 5 个探针全 1 (rustc 与 clang 输出逐行相同)
```

`concat` 当初记为"阻塞于 M15 堆分配"，M15 早就完成了 —— 缺的其实是语言没实现这个内建。
现在三条路径都有：

| 路径 | 降级 |
|---|---|
| Rust | `Box::leak(format!("{}{}", a, b).into_boxed_str()) as &'static str`（v0 的 `str` 是 `&'static str`，只能漏内存） |
| IR | 长度相加 → `trunc` → bump 堆分配 → 两次 `@__loment_memcpy` → 拼 `{ ptr, i64 }`（新运行时常量，无 libc） |
| 自举 codegen | 同一份文本逐字节一致（`native_concat.lomt` 进 40/40 目标） |

**顺带把"双路径逐值一致"从文档命令变成判据**：`test_m2_concat_dual_path_runs_equal`
用 rustc 编 `test_*() -> bool` 探针、用 clang 编 C 驱动调同名函数，两边打印的
`名字 0/1` 清单必须逐行相同且全为 1 —— 之前这条只有 docs/143 §5 的手抄命令看着。

`str_concat` 还抓到一个标签表的洞：`&&` 的 phi 前驱要记"右操作数结束时的块"，而那个块
可能是 `str_eq` 的 `L?_send`；`tag_id`/`tag_name` 表里没有 `seq`/`sneq`/`send`，
未知标签一律退化成 `sc_end` → 生成了不存在的 `%L11_sc_end`（LLVM 报前驱不匹配）。
表补齐了这三个（另加 match 的 `mend`/`mwild`，它们同样可能当 `&&` 右操作数的前驱）。

### P1 证据（2026-09-08，M3）

```
loment/examples/native_slice.lomt
  Rust 路径:  10 7      # sum(&a) / first_of(&a)
  IR 路径  :  10 7      → 一致
```

`[T]` = `{ ptr, i64 }`；`&array` 生成切片值（元素指针 + 元素个数）；`slice_len` →
`extractvalue 1 + trunc`；切片下标 → `extractvalue 0 + GEP`。Rust 路径：`&[T]` / `(&a)` /
`.len()`。切片参数在原生路径已放行（`str` 同理），聚合 struct 参数仍拒绝。

### P1 证据（2026-09-08，M4）

```
loment/examples/native_mut.lomt
  Rust 路径:  27 10     # fill(&mut a, 10) 经切片写回 + 只读/可变双调用
  IR 路径  :  27 10     → 一致
```

`mut [T]` = `{ ptr, i64 }`；`&mut array` 生成可变切片值；经切片写回 = `extractvalue 0 + GEP + store`。
Rust 路径：`&mut [T]` / `(&mut a)` / `xs[(i) as usize] = v`。类型规则：可变切片可当只读切片用，
反向不允许；只读切片上赋值在编译期报错（借用检查 v0 的前身，M5 继续）。

### P1/P2 证据（2026-09-08，M5–M14 一批）

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M5 借用检查 | 正例 `f(&a,&a)` 通过；负例 `f(&mut a,&a)` / `f(&mut a,&mut a)` 报错 | ✅ |
| M6 泛型函数 | `native_gen.lomt` 生成 `max_u32` / `max_i32`，双路径 `7 3 44` | ✅ |
| M7 泛型类型 | 生成 `Pair_u32` / `Opt_u32`（tagged union），同上 | ✅ |
| M8 trait | `native_trait.lomt` 生成 `Small_measure`/`Big_measure`，双路径 `7 70` | ✅ |
| M9 `?` | `native_res.lomt`：`?` 降级为 临时绑定 + match 早退，双路径 `6 99` | ✅ |
| M10 `if let` | 同上文件：`if let` 降级为 match + 通配臂 | ✅ |
| M11 多目录 | 三目录工程 `a/→b/→c/` 依赖序解析 + 编译（测试用例） | ✅ |
| M12 `pub` | 未 `pub` 的导入符号不可见（测试用例） | ✅ |
| M13 移动语义 | 非 Copy 赋值后再用报错；Copy 类型放行（测试用例） | ✅ |
| M14 no-alloc | `alloc_audit` 恒为空（语言按构造无堆分配） | ✅ |

门禁：`lomentc_test` **70/70** · `ci.py --static-only` **4/4** · `lom_audit` 0 差异。

### P2 证据（2026-09-08，M15–M21）

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M15 堆分配 | `alloc/free/load8/store8` + bump 堆（Rust 静态数组 / IR 全局）；`native_mem` 双路径 | `42` |
| M16 RAII | `impl Drop for Guard` → Rust 原生 `impl Drop`（有析构的类型不 derive Copy）；`rustc --crate-type=lib` 编译通过 | ✅ |
| M17 use-after-free | 移动后使用 + `return &local`（悬垂）均报错 | ✅ |
| M18 语义定规 | `+ - *` 回绕（IR 原生 / Rust 需 `-O`）；除零 = trap（IR 插检查 + `@abort`，Rust 天然 panic） | `0`（wrap）`0`（除零防护） |
| M19 panic | Rust `panic!` / IR `call void @abort()` | ✅ |
| M20 端口 I/O | Rust `core::arch::asm!("in al, dx")`；IR 明确报错不静默 | ✅ 部分 |
| M21 原子 | Rust `AtomicU32::fetch_add` / IR `atomicrmw add … seq_cst` | `13` |

门禁：`lomentc_test` **78/78** · `ci.py --static-only` **4/4** · `lom_audit` 0 差异 · 8 条双路径示例全部逐值一致。
新增 `as` 类型转换（`x as u8` → Rust `as` / IR `trunc|zext|sext`）与 unit 类型 `()`。

### P3 收尾证据（2026-09-11，M30 裸机链接流程接上）

```
python tools/loment_tools_test.py   # 13/13
# 裸机链: 2352B 对象 (0 未定义) -> 5568B 映像, _start @0x100130
```

M30 之前的判据只到"`-c` 出 `.o`"；链接那一步只写在文档里（下面的 P3 手抄命令），
没有测试看着就会随编译器漂移静默失效。现在钉成 `test_m30_bare_metal_object_and_link`：

| 判据 | 结果 |
|---|---|
| `native_entry.lomt` → IR → `x86_64-unknown-none` 对象，未定义符号为 0（M31：运行时不依赖 libc） | ✅ |
| `ld.lld -T loment/build/loment.ld` 链成映像 | ✅ 5568B |
| 脚本布局生效：最低的 text 符号正好是 `0x100000`（= 脚本的 `. = 0x00100000`） | ✅ |
| `ENTRY(_start)` 生效：`llvm-objdump -f` 的入口地址 == `_start` 的符号地址 | ✅ 当前是 `0x100130` |
| `_start` / `timer_isr` 都在符号表里（M33 的 `x86_intrcc` 函数也被链进去） | ✅ |
| 链接产物本身未定义符号也为 0（整套 = 一个能独立跑的映像） | ✅ |

> 判据里**不写死** `_start` 的绝对地址：它随自带运行时的大小漂移（加 `__loment_memcpy`
> 后就从 `0x1000e0` 挪到了 `0x100130`）。能钉的是"最低 text 符号 == 1 MiB"和
> "入口 == `_start`"这两条结构性质。

### P3 收尾证据（2026-09-08，M31–M33）

```
# M31: 自带运行时, 无 libc 依赖
clang --target=x86_64-unknown-none -ffreestanding -c native_str.ll -o native_str.o
llvm-nm native_str.o | grep " U "      # 空 = 无未定义符号

# M32: 独立入口 + 链接脚本
ld.lld -T loment/build/loment.ld native_entry.o -o native_entry.elf
llvm-objdump -f native_entry.elf       # start address == _start 的地址 (随运行时大小漂移)
llvm-nm native_entry.elf               # T _start, T timer_isr

# M33: 中断函数属性
grep x86_intrcc native_entry.ll        # define x86_intrcc void @timer_isr(ptr byval([8 x i8]) %__frame)
```

`__loment_memcmp` / `__loment_memset` / `__loment_abort` 为 IR 内联实现（字节循环 + `llvm.trap`），
不再 declare libc 符号。门禁：`lomentc_test` **81/81** · `ci.py --static-only` **4/4** · `lom_audit` 0 差异。

### P4 证据（2026-09-08，M35–M44）

```
loment/examples/native_cap.lomt
  Rust 路径: 3 2      # guard blk_write(slot) 域 [0..4]
  IR 路径  : 3 2
越界字面量: guard c(9)  → 编译错误「能力 c 域 [0..4]，索引 9 越界」
```

- **M35**：Rust 侧 `pub static CAP_DOMAINS: &[CapDomain]`；IR 侧 `@__loment_caps = internal constant [N x {i64,i64,i64,i64}]`（space 取 FNV-1a 32 位哈希）。
- **M36**：`guard <cap>(idx)` —— 字面量越界编译期拒绝；非字面量生成运行期检查（越界 → trap，无副作用）。
- **M38**：通过分支在 `__LOMENT_AUDIT[cap]` 上加一（Rust 静态数组 / IR 全局 `[16 x i64]`）。
- **M41**：能力空间命中 `excluded "<space>: ..."` → 编译错误。
- **M43**：120 组随机 `(lo,hi,idx)` 的编译期判定与域语义一致。
- **M44**：docs/146 给出形式语义与**明确的未覆盖边界**（无形式化验证、无信息流分析、撤销待内核）。

门禁：`lomentc_test` **85/85** · `ci.py --static-only` **4/4** · `lom_audit` 0 差异。

### P5 证据（2026-09-09，M45–M54）

```
# M45/M46: 15 个示例各有一份 v1 形式对象, 全部通过独立校验器
python tools/lomentc.py loment/examples/native_slice.lomt --emit-potato ...
python tools/potato.py validate loment/build/*.potato.json     # 15/15 OK
python tools/lomentc.py demo.lomt --emit-rust out.rs           # 退出码 2: 强制 --emit-potato

# M47: 校验器完备性 (不 import 编译器 + 33 条反例)
python tools/potato_test.py        # 7/7 通过 (反例 33 条)

# M48/M54: 波 C 主表 (3 语言 x 2 臂, Wilson 95% CI)
python tools/potato_measure.py     # -> loment/build/wave-c-table.md
  python 严格 27.1% [16.6,41.0] / 宽松 93.8% [83.2,97.9]
  c      严格/宽松 100.0% [89.6,100.0] (识别率 89.2%)
  rust   严格 91.2% [84.6,95.2] / 宽松 98.2% [93.8,99.5]

# M49: 转写工具
python tools/potato_from.py tools/potato.py --mode strict --report r.json

# M50: 形式对象 -> 内核断言表 (A1-A4)
python tools/potato_assert.py --check   # cap_asserts.rs 一致 (2 条, 失败 0)

# M51: 旧版本回放
python tools/potato.py replay loment/build/legacy/demo.v0.json   # v0 回放通过 (legacy)

# M53: 跨实现一致性 (FujoOS potato.py <-> LinuxFUAI potato_verify.py)
python tools/potato_cross.py       # 50/50 判定一致
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M45 形式对象 v1 | 泛型/实例/trait/impl/切片/`str`/`()`/`guards` 全部导出并通过校验 | ✅ |
| M46 强制导出 | 编译器自检 + CLI 硬要求 + `lom_audit` 逐字节对账，三层不可关闭 | ✅ |
| M47 校验器完备性 | 不 import 编译器 + 33 条反例覆盖每条规则 | ✅ |
| M48 波 C 主表 | 3 语言 × 2 臂（严格/宽松）+ **LLM 臂已跑**（本机 Ollama 两个小模型：`qwen3:4b` / `llama3.2:3b`，24 行，语料 sha256 与工具链臂同源） | ✅ |
| M49 转写工具 | Python/C/Rust → 形式对象，报告可复现（无随机数） | ✅ |
| M50 断言绑定 | `cap_asserts.rs` 由形式对象生成并逐字节对账；内核侧接入归 P7 | ✅ 部分 |
| M51 版本化回放 | v0/v1 双版本接受 + `replay` 子命令 + 冻结 v0 样本持续回放 | ✅ |
| M52 规范文本 | docs/147（schema + 规则 + 协议 + 未覆盖边界） | ✅ |
| M53 跨实现一致 | 两份独立实现 50/50 判定一致（含 33 反例 + 跨表探针） | ✅ |
| M54 测量协议 | Wilson CI、样本量定义、sha256 绑定、实体粒度定义 | ✅ |

门禁：`lomentc_test` **89/89** · `ci.py --static-only` **6/6** · `lom_audit` 0 差异 ·
`potato_test` 7/7 · `potato_cross` 50/50。

### P6 证据（2026-09-09，M55–M66）

```
python tools/loment.py fmt  loment/examples/*.lomt          # M55 幂等 + 语义保持
python tools/loment.py doc  loment/examples/toolchain.lomt  # M58
python tools/loment.py diag loment/examples/toolchain.lomt  # M64 13 类错误码 + 建议
python tools/loment.py ir   loment/examples/toolchain.lomt --objdump   # M60
python tools/loment.py test loment/examples/toolchain.lomt  # M61 RESULT: 4/4 PASS
python tools/loment.py bench loment/examples/toolchain.lomt --n 2000000  # M62 对照表
python tools/loment.py cov  loment/examples/toolchain.lomt --call cov_main  # M63 COV 6/31
python tools/loment.py build loment/examples --out /tmp/o    # M65/M66 冷 82.6ms → 热 9.0ms
python tools/loment.py pkg  resolve|verify                   # M57 拓扑序 + 校验和
python tools/loment.py lsp  --demo loment/examples/toolchain.lomt  # M56 三能力自测

# M59: DWARF 行表 + 变量信息
python tools/lomentc.py loment/examples/toolchain.lomt --emit-llvm x.ll \
       --emit-potato x.json --debug
clang --target=x86_64-unknown-none -ffreestanding -g -c x.ll -o x.o
llvm-objdump -d -l x.o | grep toolchain.lomt      # ; .\toolchain.lomt:7 ... :14
llvm-objdump -d -l --debug-vars=ascii x.o | sed -n '/<fib>:/,/^$/p'
#   |- n = <unknown op DW_OP_fbreg (145)>
#   | |- a = ...  | | |- b = ...  | | | |- i = ...  | | | | |- t = ...
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M55 格式化 | 16 示例幂等 + 形式对象逐字节不变 | ✅ |
| M56 LSP | `handle()` 驱动：诊断/补全/跳转三项 | ✅ 部分（无编辑器宿主） |
| M57 包管理 | 三级依赖拓扑序 + 锁文件 + 篡改检出 + 环检测 | ✅ |
| M58 文档 | 签名/能力域/`///` 注释入文档 | ✅ |
| M59 DWARF | `define` 挂 scope + 语句级 `!dbg` + **变量**（`DILocalVariable` / `#dbg_declare` 记录）；`objdump -l --debug-vars` 出源行与变量名 | ✅ |
| M60 IR 查看 | IR + `llvm-objdump -d` 一条命令 | ✅ |
| M61 测试 | `RESULT: 4/4 PASS`，失败退出码 1 | ✅ |
| M62 基准 | Rust vs IR 对照表（`bench_fib` 2.46x） | ✅ |
| M63 覆盖率 | `COV 6/31 19.4%` 可复现 | ✅ |
| M64 诊断 | 13 类错误各有码 + 建议（无 E0999） | ✅ |
| M65/M66 增量与缓存 | 冷 82.6ms → 热 9.0ms（9.2x），16/16 命中 | ✅ |

门禁：`loment_tools_test` **11/11** · `ci.py --static-only` **7/7** · `potato_cross` 51/51
（新增示例后总数 +1）。

> 后置修订（2026-09-13，M59 收口）：变量信息补上了。每个形参/局部在发射期拿到
> `DILocalVariable`（形参带 `arg:`）与一条 `#dbg_declare(ptr %x.addr, !N, !DIExpression(), !LOC)`
> **调试记录**（clang 22 自己发射的就是记录形态，不是旧内建 `llvm.dbg.declare`；记录附着于
> 下一条指令，序言里每个 alloca 后面总还有指令，位置安全）。只读 `--debug` 分支，
> 非 debug 路径不长出任何调试元数据 —— 与"两后端逐字节等价"的判据不冲突。
>
> `llvm-objdump --debug-vars` 把位置显示成 `<unknown op DW_OP_fbreg (145)>`：**不是缺陷**。
> 对照实验：clang 22 自己发射的 C 源码产物在同一条命令下显示完全相同 —— 那是 llvm-objdump
> 的变量位置求值器在 freestanding 目标上的限制。变量**名字**与它绑定的**源码行**由 DWARF
> 正确给出（`--debug-vars` 会逐个列出 `n/a/b/i/t`）；要把位置解成具体寄存器/栈偏移，
> 需要完整调试器。判据 `loment_tools_test::test_m59_dwarf_local_variables`。
>
> 顺带抓到一个**既有 bug**（不是本次引入）：`--debug` + `match` 会产出**非法 IR**。
> `w()` 给所追加的每一行都挂 `, !dbg !N`，而 `switch` 是**多行指令** —— `!dbg` 只能挂在
> 整条指令末尾（`]` 那一行）。现象是 `switch i32 %t, label %L [, !dbg !7` 被 clang 直接拒。
> 旧用例只喂了没有 `match` 的 `toolchain.lomt`，所以从未触发。修法：多行指令的续行走
> `w_raw`（不加后缀），`!dbg` 只挂 `]`。回归判据
> `test_m59_debug_ir_compiles_across_corpus`（对含 match/枚举/泛型/数组的 5 个语料逐个
> `clang -g` 编译）—— 调试信息不能把编译搞崩，这条比"发没发元数据"更靠前。

### P7 证据（2026-09-09，M67–M78）

```
python tools/loment_boot.py loment/examples/user_hello.lomt   # M67
  [PASS] user_hello.lomt -> M67 RESULT: PASS
python tools/loment_boot.py loment/examples/bootprobe.lomt    # M76
python tools/loment_boot.py loment/examples/selfcheck.lomt    # M77
python tools/loment_boot.py loment/examples/all_loment.lomt   # M78 (多模块+泛型)
python tools/loment_syscalls.py --check                       # M72 46/46 opcode 覆盖
python tools/loment.py dbg loment/examples/toolchain.lomt --fn fib   # M75
  fn fib: 21 条指令, 源行 7..16 (10 个)
python tools/loment_p7_test.py                                # 8/8
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M67 用户程序 | QEMU + `fujo.run` 自启动, 串口断言 | ✅ |
| M68 AHCI 核心 | mock 寄存器块 `6 31 1 1` | ✅ 部分 |
| M69 FUI 控件 | 与 `lom/fuc.lom` NODE_FMT 逐字节一致 | ✅ |
| M70 中断 | `x86_intrcc` + byval 帧 IR 形状 | ✅ 部分 |
| M71 模块 ABI | `_start` 零参 + ELF 入口 0x4000xx | ✅ 部分 |
| M72 syscall 层 | 46 个包装 + 内核 dispatch 46/46 | ✅ |
| M73 分配器 | first-fit 复用 `1 1 1 1` | ✅ |
| M74 调度钩子 | ABI 约定（未接调度器） | ✅ 部分 |
| M75 调试器 | `--fn`/`--addr` 符号化 | ✅ |
| M76 bootprobe | 串口 `M76 RESULT: PASS` | ✅ |
| M77 自检 | 串口 `M77 RESULT: PASS` | ✅ |
| M78 全 Loment demo | 串口 `M78 RESULT: PASS` | ✅ |

门禁：`loment_p7_test` **8/8** · `ci.py --static-only` **8/8** · `potato_cross` 60/60。

### P8 证据（2026-09-09，M79）

```
python tools/loment_p8_test.py     # 1/1: Loment 版 lexer token 流 == Python 版
# 4 个真实文件逐 token 比较 (kind,start,len,line,col), 含中文注释与 lexer 自身源码
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M79 自举 lexer | `loment/selfhost/lexer.lomt` 经 IR 路径编译 + C 驱动, 与 `lomc.lex` 逐 token 一致 | ✅ |

顺带修掉两个后端真 bug（`else if` 语法、嵌套 `&&` 的 phi 前驱），见 docs/150 §M79。

门禁：`loment_p8_test` **2/2** · `ci.py --static-only` **10/10**。

### P8 证据补充（2026-09-09，M80 部分）

```
python tools/loment_p8_test.py     # 2/2: M79 token 流 + M80 签名 AST dump
# M80: (module m (fn f (p x u32) -> u32) ...) 与 Python 版逐字符一致 (5 文件)
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M80 自举 parser | `loment/selfhost/parser.lomt` 的 AST dump == Python 版（全语料逐字符） | ✅（43/43 语料一致；覆盖含 impl/trait 折叠、match/if-let、数组/结构体字面量、方法调用、`?`、多 token 类型、整数字面量按数值出） |
| M81 自举 checker | 6 负例两边都拒（码集 ⊆）+ 4 正例两边都收 + 拼接单元放行 **40/40** + 跨模块重名两边**都报 E-DUP** | ✅（收口：**63/63 规则等价**、负例直接喂驱动全被拒、40/40 零诊断、0 假阳性；见 docs/150 M81 节） |
| M82 自举 codegen | `codegen.lomt` 的 `.ll` 与 `lomentc --emit-llvm` 逐字节一致 | ✅（目标覆盖 37/37；唯一非目标 `native_raii.lomt` 参考实现自身报 `inb` 未实现） |
| M87 一键引导 | `python tools/loment_bootstrap.py` 全绿 | ✅ |
| M88 校验和 | `loment_release --checksums` 清单 sha256（行数 = 工件数） | ✅（tag `v0.1.3.4-alpha` 已推送） |

### P8 证据补充（2026-09-10，M82 除法/取模）

```
python tools/loment_p8_test.py     # 5/5
# 示例覆盖 9/36 字节一致 (8 个 ir_*.lomt 锚点 + toolchain.lomt)
# 缺口分类: 聚合/切片/字符串 18 · 内建 16 · 除法 9 · for 7 · syscall 5 · match/枚举 4 · 能力域 2
python tools/lomentc_test.py       # 89/89
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M82 除法/取模 | `ir_div.lomt`（`/` `%` 的 8 个函数，含 `10 / d`、`if` 后再除、`&&` 里的除法）逐字节一致 | ✅ |
| M82 运行时文本块 | 出现过 `/` `%` 时插入整段 `_IR_RUNTIME`（横幅后、函数前） | ✅ |
| M82 phi 前驱 | 除法落在 `&&` 右操作数时前驱必须是 `%L_dend`（标签 tag_id 表补 `dok/dtrap/dend`） | ✅ 实测（去掉后第 5210 字节起不一致） |
| M82 `for` 循环 | `ir_for.lomt`（`for i in 0..n`，u32/i32 两种符号性）逐字节一致 | ✅ |

### P8 证据补充（2026-09-10，M82 指针/位域内建）

```
python tools/loment_p8_test.py     # 5/5
# 示例覆盖 35/38 字节一致 (10 个 ir_*.lomt 锚点 + toolchain/native_bits/native_mem/native_str/native/ahci/fuc_node/bytes/allocator/mathutil/user_hello/bootprobe)
# 自举四阶段自编译: lexer / parser / checker / codegen 的 .ll 与 lomentc --emit-llvm 逐字节一致
# 缺口分类: 聚合 9 · 内建 5 · for 5 · match/枚举 3 · 除法 2 · 能力域 2 · syscall 1
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M82 `alloc`/`free` | `ir_builtin.lomt`：bump 堆 + `%L_aok`/`%L_aovf` 溢出分支 + 堆全局位置 | ✅ |
| M82 `atomic_add`/`ptr_add`/`ptr_sub` | `atomicrmw add seq_cst` / `zext`+`getelementptr inbounds i8`（减法先 `sub i64 0`） | ✅ |
| M82 `get_bits`/`set_bits`/`panic` | i16 掩码链 + `%L_dead` 死块；结果上的 `as` 走名字型 `apply_cast_b` | ✅ |
| 真实示例解锁 | `native_bits`/`native_mem`/`bytes`/`native`/`ahci`/`fuc_node`/`allocator` 从"有差异"变为**逐字节一致** | ✅ |
| M82 常量内联 | `const HDR: u32 = 8;` 生成期内联为十进制字面量（含十六进制字面量归一） | ✅ |
| M82 unit 返回类型 | 无 `-> T` 的函数按 `()` 处理（`UNIT=0xFFFFFFFF` 哨兵）；`return 0` 返回 `ptr` 写 `null` | ✅ |
| M82 依赖拼接单元 | 夹具按 `resolve_deps` 规则拼接并把"模块名序列与 lomentc 一致"作为断言（规则漂移即失败） | ✅ |
| M82 表达式契约修正 | 内建分支返回位置统一为"其后"（`load8(p,off) + load8(p,off+1)*256` 曾只算前半） | ✅ |
| M82 unit 类型 | `()` → `void`、函数尾补 `ret void`/`unreachable`、unit 调用不占寄存器 | ✅ |
| M82 布尔字面量 | `while true` 曾生成 `load i32, ptr %true.addr` → 直接写字面量 `1` | ✅ |

### P8 证据补充（2026-09-11，M82 收官：泛型实例名/预置枚举/`?`/`if let`）

```
python tools/loment_p8_test.py     # 6/6
# 目标覆盖 37/37 字节一致
# 非目标: native_raii.lomt (参考实现自己就发不出来 -> LomError 20:1: native: inb 暂未在 IR 后端实现)
# 定点: stage1 == stage2 == stage3 (958762B); stage2 对 checker/ir_div 亦一致
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M82 类型名折叠 | 注释行的泛型实例名 = `基名_实参...`（`Pair<u32>`→`Pair_u32`、`Result<u32,u32>`→`Result_u32_u32`）。参考实现靠 `_rewrite_types` 改字面串，这里在 `emitted_name` 里等价折叠 | ✅ |
| M82 数组/切片类型名 | 注释里按源码形态写回：`[u32; 4]` / `[T]` / `mut [T]`（`demo.lomt` 的 `fill_incr -> [u32; 4]` 曾是唯一残差） | ✅ |
| M82 预置枚举可见性 | `Option`/`Result` 由 `lomentc.load` 注入 `mod.enums`；原生后端按声明发聚合类型，故夹具 `_unit_text` 必须同样注入（判据要用**注入前**的模块，拿 `load` 的返回值判断永远为真） | ✅ |
| M82 泛型实例名解析 | `return Result::Ok(v)` 里的 `Result` 是**泛型基名**，实例要靠函数返回类型解析（镜像 `_fix_generic_literals` 的 `e.expr.enum = f.ret`）；`let x: T = Enum::V(..)` 由 `let` 的类型标注提供 | ✅ |
| M82 `?` 早退 | `let x: T = e?;` 直发 `_desugar_try` 的产物（`__t{line}`/`__v{line}`/`__e{line}` + `switch` + Err 臂 `return Err(e)`），标签与临时编号同序占号 | ✅ |
| M82 `if let` | `if let Enum::V(b) = e { } else { }` 直发 parser 反糖出的 Match（标签序 = `mend` / 命中臂 / `mwild`） | ✅ |
| M82 合成局部名 | `?` 造出的三个局部没有源码 token；局部表名字槽用位 31 兼作"合成名"标志（编码 `0x80000000 + k*0x1000000 + line`），按 token 文本比对前必须先挡掉它（否则按合成值当下标读 token 缓冲会越界） | ✅ |
| 真实示例解锁 | `native_res.lomt`（3022B）、`demo.lomt`（10141B）从"有差异"变为**逐字节一致** | ✅ |
| M82 目标集 | 全部 37 个可发射示例逐字节一致（唯一剩下的 `native_raii.lomt` 参考实现自己就报错，非目标） | ✅ 完成 |

### P8 证据补充（2026-09-11，M83：自举驱动 = 一个能独立跑的编译器）

```
python tools/loment_p8_test.py     # 8/8
# 自举驱动: 1043790B 自身单元 -> ELF -> 逐字节相同; 二阶段定点成立; 另 2 例一致
# 自举驱动按路径编译语料: 40/40 逐字节一致
# 定点: stage1 == stage2 == stage3 (1002385B); 目标覆盖 40/40
```

在这之前自举链的每一环都是"被 C 驱动调用的函数"：能编译自己，但没有**能独立跑的编译器**。
`loment/selfhost/driver.lomt` 把 lexer 与 codegen 接成一个 ELF：

```
clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld \
      -o fujoc-s driver.ll
./fujoc-s < unit.lomt > unit.ll        # 与 lomentc --emit-llvm 逐字节相同
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M83 独立驱动 | `driver.lomt` + `lexer.lomt` 编译成 x86_64 Linux ELF（WSL 里执行），吃入口路径吐 stdout | ✅ |
| M83 驱动编译自己 | 驱动跑自己的入口 → 产物与参考逐字节相同（1043790B）；再用**它的产物**链一个 ELF，产物不变 | ✅ 定点 |
| M83 驱动不是"只会编译自己" | 同一个二进制对 `native_res.lomt` / `demo.lomt` 也与参考逐字节相同 | ✅ |
| M83 整数 → 指针 | M67 只做了 `ptr as u64`；托管驱动要向内核要内存（`brk` 返回整数）就缺反方向。补进 `as` 规则（Rust 路径 `as *mut u8`，IR 路径 `inttoptr`），示例 `native_brk.lomt` 双路径一致 | ✅ |
| 为什么不用自带堆 | bump 堆 64 KiB（`alloc(49152)` 只是生成器自己的状态块）装不下 4 MiB token 表；而把静态堆调大等于给每个 `alloc` 用户——**内核模块尤其**——的 `.bss` 塞几 MB。所以走内核 `brk` | 设计取舍 |
| M83 自举抓到的真 bug ① | 每函数形参类型表 `24576+i*128` 撞枚举表 `40960`：第 128 个函数正好压上去。`codegen.lomt` 自己的单元只有 118 个函数所以一直没露；驱动把 `bytes`/`lexer` 一起装进来（134 个函数）才暴露 | ✅ 步长改 80 |
| M83 自举抓到的真 bug ② | 同宽异名转型（`i64 as u64`）被写成 `sext i64 %v to i64` —— clang 直接报 `invalid cast opcode`。同宽时 LLVM 里本来就是同一个类型，不再写指令 | ✅ 参考与自举两侧同步修 |
| M83 自举抓到的真 bug ③ | clang 报 `unable to create block named 'entry'`：形参叫 `entry`，而 LLVM 的块标签与局部值**共用名字空间**，每个函数的第一块都叫 `entry` | ✅ 驱动改名绕开；**语言侧仍是缺口**（`let entry: u32` 会踩到），留给 M85 后半 |
| M83 自举抓到的真 bug ④ | `(v / 256) as u8` 的 `trunc` 整个丢了：`as` 左操作数类型原先取的是**整个转型表达式**的类型（u8），于是被判成"同宽转型"。新增 `operand_type()` 按"第一个操作数，或某个顶层二元运算符右侧的操作数"取型（镜像 `expr_type(Bin) = lt or rt`），不看括号内的实参 | ✅ |
| M85 自举抓到的真 bug ⑤ | 两个模块 import 同一个依赖时它被装了**两遍**（checker → bytes, lexer；lexer → bytes）：去重表的长度按值传递，兄弟递归之间不共享 | ✅ 表长放进一格内存（Loment 无 out 参数/可变全局） |
| M85 自举抓到的真 bug ⑥ | IR 里的字符串常量写成 `c"\0D\0A"`（CRLF）：驱动按**原始字节**读源码，而参考用 `read_text`（通用换行），源码里到处是**跨行字符串字面量** | ✅ 读入后 `strip_cr()`（docs/150 那条 CRLF 老坑的同源变体） |

### M85 前半证据（2026-09-11，驱动自己做装载）

```
./fujoc-s loment/examples/demo.lomt > demo.ll     # 单二进制、单入口路径
python tools/loment_p8_test.py                     # 8/8
# 自举驱动按路径编译语料: 40/40 逐字节一致
```

| 判据 | 结果 |
|---|---|
| 入口路径来自 `/proc/self/cmdline`（`_start` 的 argc/argv 在栈上，不写内联汇编拿不到；`/proc` 给同样的信息） | ✅ |
| `use "..."` 递归解析，依赖**先写**（父文件先读进单元缓冲顶部自己的槽，递归完再补上自己），同一路径只装一次 | ✅ |
| 只有 **`.lomt`** 算依赖（`use "...lom"` 进 `mod.uses`，由 lomc 处理）——与 parser 同规则，否则 `demo.lomt` 会多装一个 `fujr.lom` | ✅ |
| 缺 `Option`/`Result` 时注入预置枚举，注入点 = 单元末尾（与 `lomentc.load` 的 append 同位）；判据是词法扫 `enum X`，字符串字面量里的 `enum Option<T>` 不会误判 | ✅ |
| `test_m85_selfhosted_driver_compiles_corpus`：同一个二进制按入口路径把 `loment/selfhost/*.lomt` 与 `loment/examples/*.lomt` 全部编译一遍，逐个与参考**逐字节**比对 | ✅ 40/40 |

`use` 装载与预置注入原本在夹具（`_unit_text`）里 —— 这一步把它们搬进编译器本身，
M83 的"单编译单元"边界就此消失。

### M85 后半证据（2026-09-11，checker 覆盖面 0/41 → 40/40 + 驱动闸门打开）

上一轮把 `checker.lomt` 接进驱动时它**拒绝一切合法程序**（0/41 单元无诊断）。这一轮按当时
列出的假报清单逐条补，现在 **40/40 个拼接单元零诊断**（唯一剩下的 `native_raii.lomt` 是
非目标文件），**驱动因此可以"先 check 再发射"了**：

```
python tools/loment_p8_test.py     # 14/14
# checker 放行单元: 40/40 无诊断 (已登记缺口 1 个 = 非目标文件)
# 驱动闸门: 负例 12/12 被拒, 正例 1/1 过检
python tools/loment_rule_parity.py # 63/63 等价 (预算 63), 假阳性 0 / 码漂移 0
```

| 判据 | 结果 |
|---|---|
| `enum`/`struct`/`trait` 声明体不当代码（`Some(T)` 这种变体声明不再被当成调用） | ✅ |
| 关键字不当调用（`return (a+b)` / `if (b)`） | ✅ |
| `guard NAME(idx)` 的域名不当函数；`E::V(x)` 变体构造、`x.m()` 方法调用同理 | ✅ |
| 泛型：`fn f<T>` 的 tparam 登记为类型名（不查重）；`Pair<u32>` 的 `<...>` 一起跳过 | ✅ |
| 类型名的**第零遍**收集（预置枚举 append 在单元末尾，而 `-> Result<u32,u32>` 在文件开头） | ✅ |
| `if let E::V(x) = e` 里的 `let` 是模式不是带类型绑定 | ✅ |
| 内建表跟上（`str_concat` 是 M2 新加的） | ✅ |
| `impl`/方法：裸 `self` 接收者、签名式方法没有函数体（`;` 也要终止返回类型扫描）、两个 impl 重名 | ✅ |
| `test_m85_checker_accepts_corpus_units` 钉住覆盖面 + 登记缺口（修好会让测试提醒更新清单） | ✅ 40/40 + 缺口 1 |
| `test_m85_driver_checks_before_emitting`：12 个负例全部非零退出 + 带诊断 + **不产出 IR**；正例零退出且产物与参考逐字节一致 | ✅ 闸门打开 |
| `loment_rule_parity`：63 条最小负例逐规则比对两边**码集**，棘轮门禁（等价数不低于预算 + 假阳性/漂移必须为 0）已进 `ci.py` | ✅ **63/63 等价**、0 假阳性 |
| `test_m64_all_reference_messages_are_classified`：参考实现 81 条消息模板逐条可分类（分类表漏一条 = 那条规则在对照里静默消失） | ✅ 81/81 有码 |
| `test_m81_builtin_tables_match`：自举 `is_builtin` 名字集合 == `lomentc.BUILTINS` ∪ `{slice_len}` | ✅ 20 个一致 |
| `test_m85_heap_budget`：checker 与 codegen 的 `alloc` 之和 + 4 KiB 余量 <= 64 KiB 语言堆（批次 2 里这里超了，表现为驱动 **SIGILL**） | ✅ 61200/65536 |
| `test_m85_codegen_arg_arity_is_loud`：自举 codegen 的 10 实参上限必须 `panic` 而不是静默截断，且语料最大形参数 <= 10 | ✅ 闸门在，最大 10 |

**批次 1（声明级规则）新增的负例**（都已进驱动闸门）：形参重名 / 空结构体 /
与基类型同名 / 能力域下界>上界 / 内建实参个数 / 常量类型不是整型。
剩余 36 条缺口**几乎全在表达式类型推断与移动借用**（批次 2，需要 token 级类型推断器）
—— 见 `docs/150` 缺口③的诚实口径。

| M85 自举抓到的真 bug ⑦ | 游标越过 eof：声明体跳过正好落在文件尾时循环尾又 `i = i + 1`，进到词法缓冲**之外**的未初始化字节，一路读下去直到段错误。症状是"一个 enum 崩、两个 enum 正常"且**重编译一次结果就变**（堆布局运气） | ✅ 跳过了就不再加一（用 `skipped` 标志，Loment 没有 `continue`） |
| M85 自举抓到的真 bug ⑧ | 跳过之后又前进：`i = chk_skip_body(..)` 返回的正好是**下一个声明的关键字**，循环尾再加一就把它整个略过（`struct` 后紧跟 `enum` 时，后者变体被当成调用） | ✅ 同上 |

### P9/P10 证据（2026-09-09，M89–M99）

```
python tools/loment_p9_test.py           # 2/2 (M89 25 示例 / M92 aarch64 交叉)
python tools/loment_manual.py --check    # 25/25 与编译器版本一致 (M93)
python tools/loment_release.py --check   # 工件 sha256 全部一致 (M95/M99)
python tools/loment_seed_test.py         # 3/3 无 Python 自举: 种子自复现 + 三阶段定点 (docs/159)
python tools/loment_eol.py               # 检出行尾 (LF 契约): CRLF 检出不该伪装成逻辑红 (docs/161)
python tools/loment_dist_test.py         # 发行包: 命令安装 + 自解压安装包, 装出来的产物与参考逐字节相同 (docs/162)
python tools/loment_sign_test.py         # 签名: Authenticode + 分离签名; 篡改/无签名必须被验出来 (docs/163)
python tools/loment_src.py               # 源码包: 语言源码 + 编辑器工具, 只从 git 索引取且两次构建字节相同 (docs/164)
```

| 里程碑 | 验证方式 | 结果 |
|---|---|---|
| M89 示例集 | 25 个示例全部编译通过 | ✅ |
| M90 库共享 | 生成物 + `use` 模块复用, 零复制 | ✅ |
| M91/M94/M95 | 版本策略/迁移指南/发布检查单成文 | ✅ |
| M92 aarch64 | `elf64-littleaarch64` + aarch64 指令 | ✅ 部分（未执行） |
| M93 手册站点 | `docs/manual/` 带编译器版本戳 | ✅ |
| M96 冻结 | 冻结面成文 + 改动流程 + 一致性套件清单 | ✅（`docs/158-loment-freeze.md`：冻结面 = 语法/类型规则、诊断口径 E001–E017、单元装载规则、**发射符号约定（跨线 ABI）**、两后端逐字节等价、内建表、能力域语义；不冻结实现内部与性能。**12 条已知开放项/刻意偏离逐条写明**（三处保守偏离、同名 let 双 alloca、驱动器无 parser、aarch64 未执行、自举性能、无 DWARF 变量信息、未外部审计、ptr 表达式 `as` 整数、FFI 4 MiB 上限、`R_X86_64_PC64` 符号扩展、混宽整数比较、关键字当标识符）；改冻结面四步流程（改规范 -> 加探针并**上调**预算 -> 两实现同提交 -> 过静态门禁）） |
| M97/M98 | 设计决策表 + 四语言对比矩阵 | ✅ |
| M99 复现包 | 工件 sha256 可复现（件数见 release-manifest.json） | ✅ |
| M100 发布审计 | 审计包就位（一条命令 **24/24** + 证据落盘）+ **第三方复核已做**（`docs/203`：独立 agent，24 条里 22 接受 / 1 驳回 / 1 未验证）；**仍缺"全门禁绿"** | ⚠️ 0.1.4 Alpha |
| 无 Python 自举（种子上线） | `sh loment/bootstrap.sh` 只用 clang: 种子自复现 + stage2/stage3 定点 | ✅（种子 `loment/build/selfhost_driver.ll` 1.63MB 已提交并被 sha256 钉住；启动脚本静态判据禁解释器；`loment_seed_test` 进门禁；docs/159） |
| 工具链去 Python 第一块 | Loment 版格式化器与 Python 版**逐字节相同** | ✅（`loment/tools/lomfmt.lomt`，判据 = 42 语料 + 4 边界 + 幂等，`loment_fmt_test` 3/3 进门禁；镜像 val/转义/合并那些怪癖；docs/159 §4b） |
| 工具链去 Python 第二块 | Loment 版文档生成器与 Python 版**逐字节相同** | ✅（`loment/tools/lomdoc.lomt`，判据 = 43 语料 + 1 边界（excluded/hex/多行 doc/双方法 trait/泛型/空 doc），`loment_doc_test` 2/2 进门禁；顺手修了参考实现注入预置枚举的行号 bug；docs/148 §4b） |

## P2 · 内存与运行时语义（M13–M22）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M13 | 所有权最小规则（移动/借用/复制） | 移动后使用报错；Copy 类型放行 | ✅ |
| M14 | no-alloc 子集审计（无堆分配） | 编译器报告分配点，no-alloc 模块为 0 | ✅ |
| M15 | 可选堆分配器接口 | 显式 `alloc` 后运行通过 | ✅ |
| M16 | 确定性析构（RAII） | Drop 顺序测试 | ✅ Rust 路径（`impl Drop`）；IR 待做 |
| M17 | 无 use-after-free 静态检查（子集） | 5 个负例被拒 | ✅ |
| M18 | 溢出/除零语义定规 + 检查模式 | 两种模式各一条用例，语义写进规范 | ✅ |
| M19 | panic/abort 策略（no_std 友好） | 裸机目标下 panic 走 abort | ✅ |
| M20 | 内联汇编与端口 I/O 原语 | 读写端口 demo 在 QEMU 中生效 | ✅ Rust 路径；IR 明确拒绝 |
| M21 | volatile/原子操作原语 | 原子自增在多核下无丢失 | ✅ 单线程验证 |
| M22 | 位域与打包结构 | 与 `.lom` 布局单源一致 | ✅（位域内建 `get_bits`/`set_bits`；打包布局由 L0 单源） |

## P3 · 原生后端（M23–M34）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M23 | 聚合类型 IR：struct（GEP） | `Blk` 在原生路径跑出同一结果 | ✅ |
| M24 | 定长数组 IR | `sum_array`/`fill_incr` 双路径一致 | ✅ |
| M25 | 枚举 IR（tagged union） | 无载荷/带载荷枚举双路径一致 | ✅ |
| M26 | `match` 降级（switch + phi） | 穷尽性用例双路径一致 | ✅ |
| M27 | 短路 `&&`/`\|\|`（phi 修正） | 副作用调用只执行一次 | ✅ |
| M28 | 字符串/切片 IR | M1–M4 用例在原生路径通过 | ✅（字符串/切片 IR 与 Rust 路径逐值一致，含 `str_concat`：`__loment_memcpy` 进自带运行时 + bump 堆拼接；见 docs/145 M2 证据） |
| M29 | 泛型单态化 IR | M6/M7 用例在原生路径通过 | ✅ |
| M30 | 裸机目标 `x86_64-unknown-none` | 产出 `.o` 无 libc 依赖 | ✅（对象 0 未定义符号；`ld.lld -T loment/build/loment.ld` 链成独立映像，最低 text 符号 == 1 MiB、入口 == `_start`；`test_m30_bare_metal_object_and_link` 钉住） |
| M31 | 无 libc 运行时（memcpy/memset 内联） | 链接后无未定义符号 | ✅ |
| M32 | 自定义入口 + 链接脚本（与 FujoOS 对齐） | 产物能被 `kernel.ld` 布局吃下 | ✅ |
| M33 | 中断/异常函数属性（naked/interrupt） | QEMU 中触发中断并返回 | ✅（`x86_intrcc` 就绪；内核侧 IDT 已装并在 QEMU 跑通 · 用例 `m188-loment-isr`，据 compat 线 2026-09-15 报告） |
| M34 | 后端一致性差分回归 | IR 路径 vs Rust 路径全用例逐值一致 | ✅（native 7 函数逐值一致） |

### P3 证据（2026-09-08）

```
python tools/lomentc.py loment/examples/native_agg.lomt --emit-llvm loment/build/native_agg.ll
clang -O1 -o native_agg_exe.exe native_agg_driver.c native_agg.ll && ./native_agg_exe.exe
8 110 12 9 11 10          # blk_end / array_sum / Circle(2) / Square(3) / 短路两例
clang --target=x86_64-unknown-none -ffreestanding -c native.ll -o native_bare.o   # 1672 B
```

IR 形态：struct → `{ i32, i32 }` + `getelementptr`；数组 → `[4 x i32]` + GEP；枚举 →
`{ i32, i64 }`（tag + 载荷槽）；`match` → `switch` + `extractvalue`；`&&`/`||` → 分支 + `phi`。
聚合参数/返回值暂不支持（ABI 未定，明确报错而非静默错编）。

## P4 · 能力与安全（M35–M44）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M35 | `capability` 的运行时表示 | 生成物含域描述常量 | ✅（`CapDomain` 表 / `@__loment_caps`） |
| M36 | 能力域静态检查 | 越界访问编译期拒绝 | ✅（`guard` 语句） |
| M37 | 撤销语义代码生成（revocable） | 撤销后调用返回拒绝 | ✅（标志进域表/Potato；撤销已由内核实施 · 用例 `m185-domcaps` + 串口 `INV_RUN … rev=2 nonrev=0`，据 compat 线 2026-09-15 报告） |
| M38 | 审计钩子（每次能力使用） | 审计条目数与调用次数一致 | ✅ |
| M39 | 与 `kernel/src/capability.rs` 域模型对齐 | 双向 diff 0 差异 | 未开始（内核侧改动，P7）—— **2026-09-15 复核：不是内核真缺，是判据工具的解析口径过期**。双向 diff 的 F-K / K-F / 字段差**全是 0**，只有 2 条 `WIRE` 红，且两条都是假阳性：W64c 之后域表已从静态初值**搬进 arena**（`kernel/src/capability.rs:72` 的 `dom()` 读 `seg(OFF_DOM)`），改由 `replay_init()` 在启动时填满 `DOM_MAX` 行（`main.rs:424` 调用；0 号=系统域 `revocable=false`，1..4=`revocable=true`）。而 `tools/loment_caps_diff.py` 仍在找 `static mut DOM: [Domain; DOM_MAX] = […]` 的**编译期初值** → 解析到 0 行 → 报「行数 0 != DOM_MAX 5」与「形式侧有 revocable=true 但内核域表无」。所以既不改形式侧（形式侧没多声明），也不排 C 段（内核侧没缺东西）—— **要修的是 compat 侧的判据工具**（改解析 `replay_init()`，或加一条运行期探针读真表），修完再按 T3 那半句进 `parallel_gate`；**在那之前别把它当红的门禁接进去**，那等于把一条假红钉成门禁。 |
| M40 | A1–A4 断言的 Loment 表达 | `inv_run` 自检通过 | ✅（内核侧 `inv_run` 自检通过 · 串口 `CAP_ASSERTS n=2 bad=0 guards=2 (A1-A4 ok)`，据 compat 线 2026-09-15 报告） |
| M41 | `excluded` 出界声明强制 | 越界能力声明编译期报错 | ✅ |
| M42 | 信任自适应域宽接口 | 域宽随质量台账变化可观测 | ✅（域宽随质量台账可观测 · 用例 `m185-domcaps` + 串口 `dom=5 probes=3`，据 compat 线 2026-09-15 报告） |
| M43 | 能力域模糊测试 | 无越权放行 | ✅（120 组随机域/索引） |
| M44 | 能力安全形式化说明 | docs 定稿并进论文素材 | ✅（docs/146） |

## P5 · Potato 与表示层（M45–M54）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M45 | 形式对象 v1（泛型/切片/字符串） | 新类型全部导出且校验通过 | ✅ |
| M46 | 编译器强制导出（不可关闭） | 缺形式对象则编译失败 | ✅ |
| M47 | 独立校验器完备性 | 校验器不 import 编译器代码 | ✅ |
| M48 | 结构识别转换率测量（波 C 主表） | 三语言 × 两模型对照表 | ✅（三语言 × 两模型表已产出：本机 `qwen3:4b` / `llama3.2:3b`，24 行，语料 sha256 与工具链臂同源；**是本地小模型，不是前沿 LLM 对照**，读法与四条限定见 docs/147 §8） |
| M49 | Python/C/Rust → Potato 转写工具 | 转写率与错误率可复现 | ✅ |
| M50 | 形式对象 → 内核断言绑定 | A1–A4 由形式对象驱动 | ✅（内核已接入并启动自检 · 串口 `CAP_ASSERTS n=2 bad=0 guards=2`，据 compat 线 2026-09-15 报告） |
| M51 | 形式对象版本化与回放 | 旧版本可回放校验 | ✅ |
| M52 | Potato 规范文本（论文三素材） | 规范 + 测量协议成稿 | ✅ |
| M53 | 跨实现一致性（FujoOS/LinuxFUAI） | 同一形式对象双实现同判定 | ✅ |
| M54 | 测量协议与统计 | 置信区间与样本量定义 | ✅ |

## P6 · 工具链（M55–M66）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M55 | 格式化器 `lomfmt` | 幂等：格式化两次结果相同 | ✅ |
| M56 | LSP（补全/跳转/诊断） | 三个能力在编辑器实测 | ✅ 部分（**宿主已就位**：`editors/vscode/` VS Code 扩展已装，`vscode_ext_test` 5/5 无头验收含完整 LSP 往返；编辑器内人工点验待做） |
| M57 | 包管理器 `lompkg` | 依赖解析 + 校验和 | ✅ |
| M58 | 文档生成器 `lomdoc` | 从 `.lomt` 生成 API 文档 | ✅ |
| M59 | 调试信息（DWARF） | 调试器能按源码行断点 | ✅（行表 + 变量信息；位置求值要完整调试器，见 P6 证据） |
| M60 | IR 查看器 / 反汇编 | 一条命令看 IR 与机器码 | ✅ |
| M61 | 内建测试框架 | `loment test` 跑通 | ✅ |
| M62 | 基准框架 | 与 Rust 路径对比表 | ✅ |
| M63 | IR 级覆盖率 | 覆盖率报告可复现 | ✅ |
| M64 | 错误信息质量（含修复建议） | 10 类错误各有建议 | ✅（13 类） |
| M65 | 增量编译 | 改动单文件重编译时间下降 | ✅ |
| M66 | 构建缓存 | 冷/热构建时间对照 | ✅（82.6ms → 9.0ms） |

## P7 · 内核集成（M67–M78）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M67 | 第一个 Loment 用户态程序跑在 FujoOS | 输出与宿主一致 | ✅ |
| M68 | 第一个 Loment 驱动（AHCI 子集） | 读扇区成功 | ✅（核心逻辑 + 真机寄存器块已换 · 用例 `m186-ahci-real`，据 compat 线 2026-09-15 报告） |
| M69 | FUI 运行时用 Loment 重写一个控件 | 桌面渲染无回归 | ✅（Node 打包与 `lom/fuc.lom` 逐字节一致） |
| M70 | 中断处理用 Loment | 键盘中断路径生效 | ✅（IR 形状 + 内核侧 IDT 安装已接 · 用例 `m188-loment-isr`，据 compat 线 2026-09-15 报告） |
| M71 | 内核模块 ABI（Loment ↔ Rust 互操作） | 双向调用通过 | ✅（入口/装载 ABI 形状 + 内核模块加载器已接 · 用例 `m187-loment-abi`，据 compat 线 2026-09-15 报告） |
| M72 | Loment 版 syscall 层 | 兼容矩阵不回归 | ✅（46/46 opcode 覆盖） |
| M73 | 内存管理子系统（一个分配器） | 压力测试无泄漏 | ✅（固定块池 first-fit） |
| M74 | 调度器钩子 | 上下文切换计数正确 | ✅（ABI 约定 + 调度器钩子已接 · 用例 `m187-loment-abi`，据 compat 线 2026-09-15 报告） |
| M75 | Loment 版调试器（"不再读二进制"） | 按符号名断点 | ✅（符号化 + 源行映射） |
| M76 | Loment 版 bootprobe | 取代 bootstrap 读二进制 | ✅ |
| M77 | 内核自检用 Loment 写 | 自检项全绿 | ✅ |
| M78 | 首个全 Loment 的 demo 程序 | 进回归矩阵 | ✅ |

## P8 · 自举（M79–M88）

| # | 里程碑 | 判据 |
|---|---|---|
| M79 | Loment 版 lexer | 与 Python 版 token 流一致 | ✅ |
| M80 | Loment 版 parser | AST 与 Python 版结构一致 | ✅（**全语料 43/43 逐字符一致**，dump 助手零跳过；两道棘轮 `PARSE_KNOWN_GAPS`/`PARSE_HELPER_GAPS` 已清空且门禁拒绝过期条目，覆盖面下限钉成 `CORPUS_FLOOR`；扫全语料抓到并修掉 `else if` 空格、字符串解转义口径、多 token 类型、`&mut` 一元、`..` vs 字段、方法调用后缀、`no_struct` 歧义、整数字面量按数值出等 8 处；3 条已知残留写在 docs/150 M80 节） |
| M81 | Loment 版类型检查 | 负例集判定一致 | ✅（**规则等价 63/63**（`loment_rule_parity` 棘轮门禁，假阳性/漂移 0，已进 `ci.py`）+ 63 条负例直接喂自举驱动全部非零退出且无信号 + 40/40 语料零诊断 + 跨模块重名两边都报 E-DUP；三处刻意保守偏离见 docs/158 §4） |
| M82 | Loment 版 IR 生成 | `.ll` 与 Python 版逐字节一致 | ✅（目标覆盖 39/39：标量/控制流/短路/转换/`for`/除法/内建/常量内联/struct/数组切片/字符串/枚举 match/泛型单态化/trait 派发/能力域/`?`/`if let`/整数↔指针；**自举四阶段全部能编译自身**、M83/M84 定点达成；见 docs/150、docs/156） |
| M83 | 自编译：编译器编译自身 | 产出可运行二进制 | ✅（`loment/selfhost/driver.lomt` 把 lexer + codegen 接成**一个能独立跑的 ELF**：brk 取内存、stdin 吃单元、stdout 吐 IR；它编译自己的单元与参考逐字节相同，且用它自己的产物再链一次仍逐字节相同。边界：单编译单元，`use` 装载仍在夹具侧——与 M80/M81 同边界） |
| M84 | 三阶段自举定点校验 | 第 2/3 阶段产物逐字节相同 | ✅（stage1/2/3 的 IR 逐字节全等 1002385B；自举驱动也做了二阶段定点；stage2 对 checker/ir_div 亦与参考一致） |
| M85 | 自举编译器跑全部测试 | `lomentc_test` 在自举版上通过 | ✅（**可映射部分全部达成**：驱动自己装载 + 先 check 再发射；**规则等价 63/63**（`tools/loment_rule_parity.py`，棘轮门禁 `eq ≥ BUDGET`、假阳性/漂移为 0，已进 `ci.py`）；**63 条规则负例直接喂驱动器全部非零退出且无信号**（`test_m85_driver_gate_on_probe_cases`）；40/40 语料零诊断、40/40 目标逐字节一致、三阶段定点、跨模块重名被拒；`lomentc_test` 91 条里**不可映射的三类**（运行时/双后端实跑、Python API 形状断言、夹具侧 3 目录加载 —— 后者已被 driver→codegen→lexer/bytes 四级 `use` 覆盖）逐条列在 docs/150。这一路抓到**五个真 bug**（实参上限 10 静默截断、checker/codegen 共用 64 KiB 堆 SIGILL、每函数表容量 192 写穿、字符串字面量撞关键字判定、同名 let 双 alloca），前四个都配了静态闸门） |
| M86 | 自举性能优化 | 编译自身时间进入预算 | ✅ 基线 + 护栏（实测自编译 **12.8s**；参考实现 Python 1.2s；mathutil/lexer <0.1s、codegen 4.6s —— 自举版慢约 10 倍且略超线性，原因是符号查找是线性扫（`chk_lookup_slot`/`find_fn`）。`test_m86_selfhost_perf_budget` 以 30s 为护栏并打印数字，便于看趋势；真正的优化（表索引）留给后续） |
| M87 | 引导脚本与发布包 | 干净环境一键引导 | ✅（`tools/loment_bootstrap.py`） |
| M88 | 自举版本发布 | 打 tag + 校验和 | ✅（tag `v0.1.3.4-alpha` 已推送 origin；`SHA256SUMS` 与 `release-manifest.json` 同源，行数 = 工件数） |

## P9 · 生态与平台（M89–M96）

| # | 里程碑 | 判据 |
|---|---|---|
| M89 | SDK：Loment 版示例集 | 10 个示例全通过 | ✅（25 个） |
| M90 | 第三方库加载（L0 单源共享） | 外部库接入不复制常量 | ✅ |
| M91 | 版本与兼容策略 | 语义化版本规则成文 | ✅ |
| M92 | 跨平台目标（aarch64） | 交叉编译产物可运行 | ✅ 部分（交叉编译通过；无模拟器执行） |
| M93 | 语言手册站点 | 手册与编译器同版本 | ✅ |
| M94 | 教程与迁移指南 | Rust → Loment 迁移案例 | ✅ |
| M95 | 发布流程与社区规范 | 发布检查单 | ✅ |
| M96 | 语言稳定性承诺（0.1.4 Alpha 冻结） | 冻结后 API 不再破坏 | ✅（0.1.4 Alpha 冻结面成文：`docs/158-loment-freeze.md` —— 语法/类型规则、诊断 E001–E017、单元装载、**发射符号约定（跨线 ABI）**、两后端逐字节等价、内建表、能力域语义；7 条已知开放项/刻意偏离 + 改冻结面四步流程） |

## P10 · 论文与验证（M97–M100）

| # | 里程碑 | 判据 |
|---|---|---|
| M97 | 语言设计与实现的论文素材 | 设计决策有据可查 | ✅ |
| M98 | 与 Rust/C/Zig 的形式化对比 | 对比矩阵成文 | ✅ |
| M99 | 端到端可复现实验包 | 第三方机器可复现 | ✅（工件 sha256；件数见 release-manifest.json） |
| M100 | 0.1.4 Alpha 发布与审计 | 全门禁绿 + 外部审计 | ⚠️ 未达（**门禁侧**: `python tools/loment_audit.py --json` **24/24** 条主张通过并落盘证据（审计包 `docs/160-loment-audit-kit.md` 含主张/不主张/复核步骤/对抗性尝试）; **外部审计**: **已做**（`docs/203-third-party-audit.md` —— 一次**独立 agent、无创作会话上下文**的复核，24 条里 **22 接受 / 1 驳回 / 1 未验证**，并交回几处账目错误，见 `docs/202` §2.2）; **仍不能翻 ✅ 的是"全门禁绿"** —— 其中 4 条要读私有姊妹仓 `FujoJTOP/LinuxFUAI`，公开 runner 上按字面拿不到（用户 2026-09-22 裁决：这一项不做，口径保持"不新增红"，见 `docs/202` §2.1） |

## 依赖与风险

- **硬依赖链**：P1/P2（语言语义）→ P3（后端）→ P4（能力）→ P7（内核集成）→ P8（自举）。
  P5/P6 可与 P1–P3 并行，但 P5 的 M45 必须早于 P3 的 M28（形式对象要覆盖新类型）。
- **自举前置**：M79 之前语言必须补上字符串（M1–M2）、切片（M3–M4）、模块（M11）、
  文件与动态内存（M15）——否则编译器写不出来。
- **最大风险**：后端与工具链吞掉论文时间。→ 每里程碑结束必跑 §0 门禁；论文优先级不变。
- **止损**：M30（裸机目标）若无法进入 FujoOS 链接流程，P7 降级为"宿主可执行 + 宿主驱动"，
  M79 之后的"替换 Rust 路径"重新评估。
- **环境风险**：回归门禁受端口占用/负载影响，见 [[fujoos-qemu-port-collision]]——先查环境再判回归。
