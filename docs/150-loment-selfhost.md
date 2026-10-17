# 150 · Loment 自举（P8，M79–M88）

> 状态: **M79–M84 已完成**（2026-09-11）· 自检: `tools/loment_p8_test.py` **7/7**
> M82 逐字节一致: **目标 39/39**（10 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/
> `native_str`/`native_gen`/`native_trait`/`native_res`/`native_brk`/`bytes`/`native`/`ahci`/
> `fuc_node`/`allocator`/`mathutil`/`user_hello`/`bootprobe`/`demo`/`all_loment`/`selfcheck`/
> `lexer`/`parser`/`checker`/`codegen`/`driver` …）。
> 唯一非目标 `native_raii.lomt`：参考实现自己就报 `native: inb 暂未在 IR 后端实现`。
> **自举四阶段全部能编译自身**，且有一个**能独立跑的驱动**（`loment/selfhost/driver.lomt`）：
> 它编译自己的单元与 `lomentc --emit-llvm` 逐字节相同（973238B），用自己的产物再链一次仍相同。
> 门禁: `ci.py` 静态项 8 PASS（另 4 项因 `LinuxFUAI/` 私有库缺位而失败，非 Loment 回归）

## M79 · Loment 版 lexer ✅

`loment/selfhost/lexer.lomt`：用 Loment 写的词法器，输入字节缓冲，输出 20B/token 记录
（`kind|start|len|line|col`）。规则与 `tools/lomc.py` 的 `lex()` 一致：

- 空白 / `//` 行注释 / `/* */` 块注释；
- 字符串（含 `\` 转义，span 含引号）；
- 十进制与 `0x` 十六进制数；
- 标识符（`[A-Za-z_][A-Za-z0-9_]*`）；
- 单字符 punct 集 `{}()[]:;=@,.+-*/%<>!&|^?`；
- 末尾 `eof`。

**判据（与 Python 版 token 流一致）**：`loment_p8_test.py` 用 IR 路径编译 lexer + C 驱动，
对 4 个真实文件（含中文注释、含 lexer 自身源码）逐 token 比较
`(kind, start, len, line, col)`——**全部一致**。

实现过程中修掉的两个真 bug（都属于编译器，不是 lexer）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `else if` 解析失败 | 语法只支持 `else { }` | parser 支持 `else if` 链 |
| 2 | `&&`/`||` 嵌套时 LLVM 报 `PHI node entries do not match predecessors` | 短路降级把 phi 前驱写死成 `rhs_l`，右操作数自己也可能产生新块 | `_Ir` 跟踪 `cur_label`，phi 用真实前驱；每个函数显式 `entry:` 标签 |

第 2 个 bug 是自举逼出来的：手写示例里的 `&&` 都很简单，只有把真实程序（lexer 的
多重条件）交给后端才暴露。修完后 11 条双路径示例输出与修改前逐值一致（见 docs/145 P8 证据）。

## M80 · Loment 版 parser（收口：全语料 43/43 逐字符一致）✅

`loment/selfhost/parser.lomt`：消费 M79 的 token 记录，产出规范 AST dump，
由 `loment_p8_test` 与 Python 版（`lomentc` 的 AST）**逐字符**比较。

**覆盖**（2026-09-12 收口在 42 个语料，2026-09-15 因 `lomelf.lomt` 的 PE 常量升到 **43**）：
`module` / `fn`（形参、返回、泛型形参跳过）/ `impl`（两种形态：`impl T for X` 与
`impl X`，含方法名折叠 `<类型>_<方法>`、首参改 `__self: <类型>`、体内 `self` 重写）/
`trait`（声明体不进 dump，与参考一致）/ 类型名规范化（`mut [X]`、`[X; N]`、`[X]`、
`Name<A, B>`）/ 语句（`let`、赋值（含 `xs[i] = v`）、`return`、`if`/`else`（含 `else if`
链）、`while`、`for i in a..b`、`match`（臂：`_`、`E::V`、`E::V(bind)`）、`if let`（反糖成
两臂 Match）、`guard CAP(e);`、表达式语句）/ 表达式（整型、字符串（解转义再重转义）、
`true`/`false`、标识符（`self` -> `__self`）、调用、数组字面量、结构体字面量、枚举
构造/路径、方法调用、一元 `!`/`-`/`&`/`&mut`、二元全部运算符（按 `lomentc.PRECEDENCE`
结合）、括号、`a.b`、`a[i]`、`a?`、`x as T`）。

**判据**：`test_m80_loment_parser_ast_dump` 拉进全部语料，要求
`len(files) >= CORPUS_FLOOR`（覆盖不许回退）+ 不在缺口清单里的文件必须逐字符一致 +
**两道棘轮**（`PARSE_KNOWN_GAPS` / `PARSE_HELPER_GAPS`）现在都是空表，且门禁会拒绝
"清单里已经一致"的条目 —— 也就是说这份覆盖面只能往前走。

这一路抓到并修掉的真 bug / 真口径差（都是"扫全语料"扫出来的）：

- `else` 分支的 dump 规则（恰好一条 If 时**无**前导空格）→ 见上一节；
- 字符串字面量的口径：参考的 `tok.val` 是**解转义后**的内容, dump 要按
  (反斜杠 -> 引号 -> 换行) 重转义, 且制表符按 `_ESCAPES` 兜底;
  ⚠ **2026-10-16 起"未知转义取原字符"那条兜底没有了**（`#33` / `#36`）：表是
  `n t r 0 \\ "` 这六个, 表外**是 E019**。旧兜底让 `"\r"` 静默变成字母 `r`、
  `"\x41"` 静默变成 `x41` 三个字符。自举词法器没有失败通道, 所以拒绝落在
  `chk_syntax_prepass`（与"解析错就中止"同一趟）;
- 类型名是**规范化字符串**（`mut [u32]` / `[u32; 4]` / `Name<A, B>`），原先按单 token 读
  会把 `mut [u32]` 拆成多个"形参"；
- `&mut x` / `&x` 是**一元**运算符（op = `&mut`/`&`），不是二元 `&`；
- `..` 不能被当成字段访问（`.`, `.` 两个 token）；
- 方法调用的 dump 是**后缀**形态（`接收者(mcall 名 实参…)`），与 `(idx …)`/`(cast …)` 一致；
- `match` 主语里的 `{` 与结构体字面量的歧义（参考用 `no_struct` 计数器）；
- **整数字面量按数值出、不照抄原文**（2026-09-15）：参考的 `IntLit.value` 本来就是数值，
  所以 `0x1000` 在 Python 侧出 `(int 4096)`；Loment 版原先 `put_tok` 直接抄 token 原文，
  出的是 `(int 0x1000)`。`lomelf.lomt` 里加了 PE 常量（`0x40000000` / `0xC0000040`）后这道
  口子第一次被扫到 —— 补 `put_num`/`put_dec`/`hex_val` 做同样的十进制规范化。
  同一个口径也用在**类型里的数组长度**（`[u8; 0x10]`）—— 那处语料至今没有十六进制样本，
  所以门禁里加了个最小探针（`HEX_PROBE`）钉住两处，不指望语料自己盯。

**已知残留**（不影响语料，写在冻结面之外）：

1. 结构体字面量判定用"`{` 后面是 标识符 + `:` 且再后面不是 `:`"的**前瞻**代替参考的
   `no_struct` 计数器 —— 对合法程序等价，唯一差别是**空字段列表** `Pair {}`（参考当字面量，
   这里当标识符 + 块）。要彻底对齐就得像参考那样把 `no_struct` 穿过表达式递归。
2. `impl ... for <类型>` 里目标类型按**单 token** 读（多 token 类型会折错名）。
3. 泛型形参（`fn f<T>`）两边都**不进 dump**，所以判据不覆盖泛型参数名本身。

实现要点：

- Loment 无元组返回 → `(i << 32) | o` 打包「token 游标 + 输出游标」，函数式传递；
- 表达式 dump 采用「左操作数先输出」的线性形式（`(id a)(bin + (id b))`），
  可无歧义还原树，且天然匹配逐 token 下降的解析器；
- 多字符运算符（`==`/`->`/`&&`…）在词法上是两个 token，用 `op_code`/`op_toks` 识别与步进。

## M81 · Loment 版检查器（收口：规则等价 63/63）✅

`loment/selfhost/checker.lomt`：消费 M79 token 流，做**符号表 + 类型/调用/移动/借用检查**，
输出错误记录（`code | token`）。与 Python 版（`lomentc.check`）对照的口径是**错误码集合**。

历史起点（批次 1 只有 4 条声明级规则）：

| 码 | 规则 | Loment 侧实现 |
|---|---|---|
| `E-DUP` (1) | 顶层名字重复（fn/struct/enum/const） | 符号表线性查重 |
| `E-TYPE` (2) | 类型未声明（参数/返回/const/let） | 基类型表 + 声明表；`[T]`/`[T; N]`/`mut [T]` 用 `skip_type` 整体跨过 |
| `E-UNKNOWN-FN` (3) | 调用未声明的函数 | 符号表 + 内建名单（20 个内建） |
| `E-ARITY` (4) | 实参个数不符 | 顶层逗号计数（识别 `(`/`[` 嵌套） |

**收口判据（2026-09-12）**：token 级类型推断器补齐后，`tools/loment_rule_parity.py`
实测 **63/63 条规则判定等价**（棘轮门禁 `eq ≥ BUDGET=63`，假阳性/口径漂移/探针失效必须为 0，
已进 `tools/ci.py`）；63 条负例**直接喂自举驱动**全部非零退出且无一因信号而死；40/40 语料单元
零诊断；跨模块重名两边都报 E-DUP。三处**刻意保守偏离**（类型未知时少报，方向保守）逐条写在
docs/158 §4 的冻结面开放项里。

**已知边界**：不解析 `use` 导入由**驱动器**负责（装载层），检查器本身按"已装载的单元"工作；
驱动器整条链没有 parser，所以解析期错误（如 `const C: bool = true;`）它报不出来（docs/158 §4 #3）。

> **后置修订（2026-09-13，移植 `lomc.lomt` 时抓到的两个真 bug）**。L0 生成器有 357 个 `let`、
> 108 个 `fn`，是迄今喂给自举 checker 的**最大单元**，一下撞出两处：
>
> 1. **复合类型表没按函数清零**。`chk_arena_tyt` 的注释与 `impl` 方法那条路（`checker.lomt`
>    的 `store32(tyt, 0, 0)`）都写明"**每函数**开头清零"，但**普通 `fn` 那条路漏了** ——
>    于是表是整单元单调 bump，写穿 448 条上限、踩进借用名表，**整条扫描游标错位**。
>    症状极具误导性：一堆 `E002`，且 token 指向 `let` 的**变量名**（甚至指向 `;`、`(`）——
>    看起来像"类型未声明"，实际是游标走岔了。修法：普通 `fn` 路径补同一句清零。
> 2. **大单元里拿 `fn` 当变量名会走岔 —— 但不是"关键字被误拒"**（这一条我先记错过，见下）。
>    `lomc.lomt` 里有个局部叫 `fn`：自举 checker 报一串 E002 并以 1 退出，改名 `fname` 即通过。
>    **可最小单元同样写法却逐字节相同** —— 单独写
>    `fn f() -> u32 { let fn: u64 = 1; return fn as u32; }` 走自举链与参考**逐字节一致**、
>    零诊断（2026-09-13 实测）。所以真正的形状是：**某个规模相关的表/游标**被这个 token 触发
>    （与上面第 1 条同族），而**不是**"参考接受、自举误拒"的语言级分歧。本次的做法是把变量
>    改名绕开；**根因未定位，缺口仍开放**。
>
>    > **更正（2026-09-13，同日）**：本条最初写成"把关键字当标识符 → 检查器假阳性"，并据此
>    > 写进了 `docs/160 §2`、`docs/166` 与 tag `v0.1.4-alpha` 的消息。补做最小重现时发现最小
>    > 单元**通过**，那个说法**不成立**，故在此更正。tag 不可变，那一版消息里的表述按本条为准。
>
> 回归判据：`tools/loment_lomc_test.py::test_lomc_selfhost_compiles`（种子 → stage1 →
> 编译 `lomc.lomt`，rc=0，产物 473 408B）。改 `checker.lomt` 会改**种子**，所以同一提交里
> 重跑了 `loment_seed_test`（3/3，含三阶段定点）与 `loment_rule_parity`（**63/63，假阳性 0**）。

## M82 · Loment 版 IR 生成（表达式/控制流/短路/转换/内建）✅

> **后置修订（2026-09-13，移植 `lomc.lomt` 时抓到的第三个真 bug —— 仍在，已绕开）**。
> 自举 codegen 的 `operand_type()`（`codegen.lomt:318`，给 `as T` 取左操作数类型用）
> 在**运算符右侧是"带 `as` 的括号表达式"**时会取错类型，发射**非法 IR**：
> ```rust
> let y: u8 = (48 + (x % 10) as u32) as u8;   // 参考: trunc i32 %t to i8
>                                             // 自举: trunc i64 %t to i8  (clang 直接拒)
> ```
> 根因：`operand_type` 扫到 `+` 后递归去取右操作数的类型，遇到 `(` 就进括号拿里面的
> `x`（u64），**没有把括号后面那个 `as u32` 算进去** —— 而参考实现的 `expr_type(Bin)`
> 取 `lt or rt`，这里正是 `u32`。换成显式中间变量（`let digit: u32 = (x % 10) as u32;`
> 再 `(48 + digit) as u8`）两边就逐字节相同，所以本次**在源码侧绕开**：
> `codegen.lomt` 是冻结链的一部分，改它要连带换种子，该单独一次改动 + 单独验证。
> **这条仍开放**：任何写 `(a + b as T) as U` 的程序走自举链都会拿到非法 IR。

`loment/selfhost/codegen.lomt`：读取 M79 token 流，直接生成 LLVM IR 文本。判据是
**逐字节**：`loment_p8_test::test_m82_*` 把 Loment 版输出与 `lomentc --emit-llvm` 的结果
做字符串相等比较，9 个目标文件共 58 个函数：

- `loment/selfhost/ir_const.lomt`：字面量/参数返回（u32/u64/i16/bool）；
- `loment/selfhost/ir_expr.lomt`：二元运算符（按 `lomentc.PRECEDENCE` 爬升、含
  `a * b + c`、`(a + b) * 2`）、比较（无符号 `ult/ule/ugt/uge` 与有符号 `slt/...` 由类型决定）、
  位运算与移位（`and/shl`）、一元（`sub ty 0, v` / `xor i1 v, true`）、调用
  （直接调用、嵌套调用 `add(a, mul_add(a, b, 1))`、字面量实参）；
- `loment/selfhost/ir_stmt.lomt`：**语句与控制流** —— `let`（含局部 alloca，
  按 `lomentc._collect_locals` 的顺序）、赋值、`if/else`（含 else 里再嵌 `if`）、`while`，
  以及 `terminated` 语义（块已 `ret` 就不再补 `br`）、标签编号 `L1_then/L2_else/L3_end`
  与 `L1_wcond/L2_wbody/L3_wend`（从 1 起、每函数重置）；
- `loment/selfhost/ir_logic.lomt`：**`&&`/`||` 短路**（`br i1` + `%L1_sc_rhs / %L2_sc_short / %L3_sc_end`
  三块 + `phi i1`），phi 的右前驱取"右操作数结束时的真实块"（嵌套短路时是内层的 `sc_end`，
  与 `lomentc` 的 `cur_label` 同义）；
- `loment/selfhost/ir_cast.lomt`：**`as` 转换**（按位宽与符号性选 `trunc` / `sext` / `zext`，
  字面量与 `bool` 转换按 `st or "u32"` 缺省规则处理），**含实参位置**——调用点按被调方的
  **形参类型**强制实参（符号表为每个函数存 8 个形参类型槽，与 `lomentc` 的 `expr(a, p.type)` 同义）；
- `loment/selfhost/ir_mem.lomt`：内建 `load8` / `store8`（`getelementptr i8` + `load i8` +
  `zext i8 ... to i32`；`store8` 的结果值按 `lomentc` 取字面量 `0`），以及 `load8(...) as T` 后缀；
- `loment/selfhost/ir_for.lomt`：**`for i in lo..hi`**（`store lo` → `L_fcond/L_fbody/L_fend` 三块，
  条件块里**重新取**循环变量与上界，自增写回 `%i.addr`；比较的符号性按
  `expr_type(lo) or expr_type(hi) or u32` 决定 `slt`/`ult`，与 `lomentc._collect_locals` 同规则）；
- `loment/selfhost/ir_div.lomt`：**除法与取模**（`udiv/sdiv/urem/srem` 由结果类型的符号性选），
  除零走 `%L_dtrap` 块（`call void @__loment_abort()` + `unreachable`），且一旦出现过 `/` `%` 就
  在横幅之后、函数之前插入**整段 freestanding 运行时文本块**（与 `lomentc._IR_RUNTIME` 逐字节相同）；
- `loment/selfhost/ir_builtin.lomt`：**指针/位域内建** —— `alloc`（bump 堆：`load @__loment_off`
  → `add` → `icmp ule 65536` → `%L_aok`/`%L_aovf`，溢出走 abort，成功后
  `store @__loment_off` + `getelementptr [65536 x i8]`，并在横幅后**再插堆全局**）、`free`（结果值为
  字面量 `0`）、`atomic_add`（`atomicrmw add ... seq_cst`）、`ptr_add`/`ptr_sub`（`zext i32→i64`
  + `getelementptr inbounds i8`，减法先 `sub i64 0, n`）、`get_bits`/`set_bits`（i16 掩码
  `1<<w - 1` 再截回 i8）、`panic`（abort + `unreachable` + `%L_dead` 死块）。
  内建结果的 `as` 后缀走**名字型**转换 `apply_cast_b`（内建结果类型是固定的：`u8`/`u32`/`ptr`），
  含 `ptr→u64` 的 `ptrtoint`。

**覆盖**：函数签名与类型映射（i1/i8/i16/i32/i64/ptr）、入口块、参数 alloca + store、
`%tN` 编号（从 1 起、每函数重置）、头部注释（注释里写的是 **Loment 类型名**而非 LLVM 类型）。

**未覆盖**（这一节写于 M82 收官前）：其余内建（`str_len`/`str_eq`/`str_byte`/
`slice_len`/`str_ptr`/`syscall*`）、`match`、str/切片/数组/struct/枚举等聚合类型、能力域表与 DWARF 元数据。
每次门禁会打印按文件计的缺口分类表（`test_m82_coverage_report`），
`ir_*.lomt` 目标文件是对应的防回归锚点。

**核心设计（两阶段值栈）**：`expr_*` 先把指令写进输出，再把"值文本"落到值栈的第 `lvl` 层；
调用方随后把该值内联到自己的行里。这正是 Python 版用字符串拼接达到的效果——
嵌套表达式因此能与 Python 版保持**完全相同的指令顺序**。值栈 8 层 × 72B，
实参类型槽与函数/参数符号表各自的偏移都在状态块里（`docs` 记于代码注释）。

调试过程中踩到并修掉的真问题（现象 → 根因）：

| # | 现象 | 根因 |
|---|---|---|
| 1 | `emit` 实参个数不符 | 与 `lexer.lomt` 的私有 `emit(7 参)` 重名 → 入口改名 `emit_module` |
| 2 | 输出在 `ret i32 ` 处截断 | 值缓冲误用 `emit_mem`（它会更新**输出游标**）→ 改用不碰游标的 `copy_mem` |
| 3 | 返回值打印成 `\x03` | 值长度写到缓冲开头（应为缓冲**之前** 4 字节） |
| 4 | `ret i32   %t0 = load ...` 乱序 | 单阶段发射无法把指令放行首 → 两阶段（先指令+值缓冲，再写行） |
| 5 | 状态块字段互相踩 | 值栈/实参类型/函数表/参数表偏移重叠 → 重新排布（值栈 16..592、实参类型 608、函数计数 640、函数表 704、参数表 1536、局部表 3072） |
| 6 | 局部 alloca 混进了别的函数 | `collect_locals` 扫到了整个文件 → 传入函数体的匹配 `}` 作为上界（`skip_block`） |
| 7 | 标签从 `L0_` 起编号 | `lomentc` 的标签从 1 起 → 基准取 `+1`、计数 `+2` |
| 8 | `if`/`while` 体里第一条语句被跳过 | 体起点算成 `'{' + 2` → 应为 `'{' + 1` |
| 9 | 调用语句整条消失 | `stmt` 缺"表达式语句"分支（`NAME ( ... ) ;`） |
| 10 | `take8(v as u8)` 少一次 `trunc` | `as` 后缀没接在**调用/内建**分支上 |
| 11 | `for` 变量没有 alloca | `collect_locals` 只认 `let` → 把 `for` 变量也登记（类型同 `lomentc` 的回退链） |
| 12 | `for i in 0..n`（`n: i32`）生成 `ult` | 符号性只看了 `lo`（字面量 → 无类型 → 落到 u32）→ 改为 `expr_type(lo) or expr_type(hi)` |
| 13 | 除法结果号比 `icmp eq` 的号小 | `lomentc` 先占 `r` 再占 `z` → 拆出只占号不写输出的 `alloc_temp` |
| 14 | 除法出现在 `&&` 右操作数时 phi 前驱写成 `%L6_sc_end` | 标签 tag_id 表没有 `dok/dtrap/dend`（默认落在 `sc_end`）→ 补 tag_id/tag_name（**实测**：去掉后第 5210 字节起不一致，且是指向不存在块的非法 IR） |
| 15 | 除法目标文件缺整段运行时 | 输出是顺序写的，而运行时块要落在横幅之后、函数之前 → 预扫描 `/` `%` punct token（语义等价于 `lomentc` 的 `"@__loment_" in text_all`） |
| 16 | 内建结果参与更大表达式时被截断（`load8(p,off) + load8(p,off+1) * 256` 只算前半） | 内建分支返回的是 `)` 的位置，而其余原子/调用分支返回的是**其后**位置（`expr_bin` 的契约）→ 统一成"其后"（`+1`，带 `as` 则 `+3`） |
| 17 | unit 函数体尾缺 `ret void` / `store16` 的返回类型写成 `i64` | `emit_ty`/`emitted_name` 不认识 `()`；`emit_fn` 也没有"块未终止则补 `ret void`/`unreachable`"这一条 |
| 18 | `while true` 生成 `%t1 = load i32, ptr %true.addr` | `expr_atom` 缺 `true`/`false` 字面量分支（落到"变量 load"兜底）→ 直接写字面量 `1`/`0` |
| 19 | unit 返回的调用写成 `%t6 = call void @f(...)` | Python 对 `()` 返回不占寄存器（`call void @f(...)` 无赋值）→ 补 unit 分支 |
| 20 | `if blk_free(cur) && sz >= need` 的标签号整体错位（`L7_sc_rhs` vs `L4_sc_rhs`） | `if` 分支**先占**标签再算条件，而条件里的 `&&`/除法自己也要占标签 → 改为先算条件再占（`lomentc` 的 If 同序；同类的 `while` 本来就是先占） |
| 21 | `const HDR` 被当成变量 load（`%t2 = load i32, ptr %HDR.addr`） | 没做**常量内联**：补 `find_const` + `tok_int`（十六进制按**十进制**内联，与 `lomentc` 一致）+ `set_dec_val` |
| 22 | `-> ` 省略返回类型的函数被当成有返回类型 | `pub fn pool_init(...)` 无 `-> T` → 返回类型必须缺省为 `()`；用哨兵 `UNIT=0xFFFFFFFF` 表示，`emit_ty`/`emitted_name`/`is_unit_tok` 一起处理 |
| 23 | `return 0;`（返回 `ptr`）写成 `ret ptr 0` | `lomentc` 的 `expr(IntLit, want="ptr")` 写 `null` → 返回位单独判断 |
| 24 | 大文件下状态块越界 | `alloc(8192)` + 每函数形参类型步长 128B + `emit_dec` 每次 `alloc(16)` 漏堆 → 状态块扩到 49152、步长改 64B、译码缓冲改用状态块内的**共享 scratch**（`emit_dec`/`set_dec_val`/`set_temp_val` 不再动 bump 堆） |
| 25 | 字段访问的类型算成了整个 struct（`add { i32, i32 }`） | `expr_type` 缺 `FieldAccess` 分支（`p.a` 被当成 `p` 的 `Pair` 类型）→ 补"字段类型"分支（与 `lomentc.expr_type` 同义） |

**聚合类型（M82 第 2 步，进行中）**：`struct` 表（名字 + 字段下标/类型，状态块 1024 起、每项 80B）、
`emit_ty` 的 struct 名 → `{ i32, i32 }`、字段访问 `p.a` → `getelementptr inbounds <struct>, ptr %p.addr, i32 0, i32 <idx>`
+ `load`、`str` → `{ ptr, i64 }`。字段仅支持标量（与 `_ll_type` 的限制一致）。
已解锁 `mathutil.lomt`（`for` 变量 + 常量内联 + struct 参数取字段）。

**字符串（M82 第 3 步，本轮到 22/37）**：
- **字面量全局**：`@.str.<fn>.<n> = private unnamed_addr constant [N x i8] c"\XX…"`（大写十六进制、逐字节转义，
  转义表同 `lomc._ESCAPES`；N 是**解码后**的 UTF-8 字节数）。全局必须落在横幅/运行时/堆全局之后、
  函数之前，而输出是顺序写的 → 用**预扫描**（`emit_str_globals`）：逐函数、逐出现，
  与表达式求值顺序一致（同一 token 流的线性扫描）。注意 `strings_needed` 只看**函数体内**的
  kind=2 token —— `use "…"` 也是 kind=2，但它不是字面量（这个坑让 3 个已通过文件回归过）。
- **字面量表达式**：`getelementptr inbounds [N x i8], ptr @.str.f.k, i64 0, i64 0` + 两次
  `insertvalue` 组成 `{ ptr, i64 }`。
- **`str_*` 内建**：`str_len`（`extractvalue …, 1` + `trunc i64→i32`）、`str_ptr`（`extractvalue …, 0`）、
  `str_byte`（`extractvalue 0` + GEP + `load i8` + `zext`）、`str_eq`（先比长度，再
  `call i32 @__loment_memcmp` + `phi i1`，落在 `L_seq/L_sneq/L_send` 三块）。
- **`syscall4`/`syscall6`**：`call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx}[,{r10},{r8}],~{cx},~{r11},~{memory}"(…)`；
  实参按各自类型求值后原样内联为 `i64 <val>`（与 `lomentc` 的 `expr(a,"u64")` 同形）。

**值栈搬到 8192**（`blen/btxt` 基址 16/20 → 8192/8196）：原先值栈与 struct 表（1024..6144）重叠，
深嵌套（如 6 个 syscall 实参）会踩表；现在各表依次为 struct 1024 / 值栈 8192 / 函数表 12288 /
当前函数参数表 16384 / 局部表 20480 / 每函数形参类型 24576（+i*64），scratch 在 656。

新增真 bug：
| # | 现象 | 根因 |
|---|---|---|
| 26 | `str_ptr(s) as u64` 生成 `trunc ptr … to i32` | 内建分支把 `as` 的类型 token 取成了 `as` 本身（应取其后一个）；且**无条件**加转换 |
| 27 | `p as u64`（ptr→u64）生成 `zext ptr … to i64` | `apply_cast` 缺 `ptr→u64/i64` 的 `ptrtoint` 分支（`apply_cast_b` 有，token 版没有） |
| 28 | `let r: i64 = …; if r >= 0` 生成 `icmp uge i32` | `expr_type` 的变量分支只查**参数表**，没查局部表 → 局部的类型丢失（应为 `sge i64`） |
| 29 | 嵌套调用的实参类型错位（`take5(p, q, r, load32(p,n), n)` 里 `q` 变成 `i32`） | 实参类型槽是**一张共享数组**，内层调用把外层已记好的槽覆盖了 → 改为按**值栈层**索引（`720+(lvl+1+a)*4`）。这个 bug 只在"实参里有调用"时现形 |
| 30 | `str_byte(s, i)` 的指令顺序反了 | Python 是"取 arg0 → `extractvalue` → 取 arg1"，我先算完两个实参才 `extractvalue` |
| 31 | 调用实参上限只有 4 | `while … && a < 4` → 自举 lexer 的 `emit(7 参)` 被截断；改为 8（形参类型槽本来就是 8） |
| 32 | 字符串字面量里出现 `DA` | 驱动按**原始字节**读文件，而夹具写临时单元用了文本模式（CRLF）→ 写 `newline="
"`；`lomentc.load` 用 `read_text`（通用换行），两边必须一致 |
| 33 | `0x100000000` 被截成 0 | 字面量走 u32 算术；改为**长乘 16 加**转十进制（数字缓冲 672..704），十进制原样去前导 0 —— 镜像 Python 大整数 |
| 34 | `(r / 4294967296) as u32` 打成 `trunc i32 → i32` | `apply_cast` 的 from 侧传 0（未知）时打印用 `i32` 但位宽按 64 算；现在传真实 `expr_type`，且 0 一律按 u32（= `st or "u32"`） |
| 35 | `x as i64` 参与运算时类型退化成 i32 | `expr_type` 不认识 `as` 后缀 → 补"字面量/标识符/调用/括号 + `as T`"四种后缀识别 |

**切片与数组（M3/M4/M24）**：类型映射 `[T]`/`mut [T]` → `{ ptr, i64 }`（注意 `[T; N]` 走数组分支，
判定要看 `[` 后第 2 个 token 是不是 `;`）、`[T; N]` → `[N x T]`；`&a` / `&mut a` 取切片 =
`getelementptr inbounds <数组>, ptr %a.addr, i32 0, i32 0` + 两次 `insertvalue`（长度来自数组字面量）；
切片下标**读** = `extractvalue 0` + GEP(元素) + `load`，数组下标读 = GEP(数组,0,i) + `load`；
下标**写**（M4 可变切片）= `extractvalue 0` + 下标 + GEP + 求值 + `store`（顺序与 `lomentc` 一致）；
数组字面量 `[1,2,3,4]` = 逐元素 GEP + 求值 + store；`slice_len` = 切片取 `extractvalue 1` 再 `trunc`，
数组则用字面量长度。**`let` 的类型位置改成 `skip_type`**（类型可能是 `[T]`/`[T; N]`）；
**形参扫描也必须用 `skip_type`** —— 原来按"3 个 token 一个参数"步进，遇到 `xs: [u32]` 会多出一个幻影参数
（`i64 %u32`），函数表的形参类型扫描同样中招。

**泛型单态化（M6/M7）**：状态块低区（值栈搬到 8192 后 `16..475` 全空）放**类型参数替换表**
（计数 16，表项 `24+i*8` = `param_tok|value_tok`），`subst_tok` 挂在三类"类型来源"上：
`emit_ty`/`emitted_name` 的入口、`param_type`/`var_type`/`field_type`/`enum_payload_type` 的返回。
类型实参**不需要额外存**——`let p: Pair<u32>` 的类型 token 后面就是 `<` `u32` `>`，
所以 `var_targ`/`push_ty_subst` 直接从标注位置读；要区分"声明的形参"与"使用的实参"（同一个 `Pair` token
两处含义不同），就在 struct/enum 表里各留两个槽（stride 80 的空位 `+72`/`+76`）记下声明处的形参 token。
实例按"首次被用到"的顺序登记在 `64` 起的表里（`72+i*12` = `fn_tok|t1|t2`），非泛型函数发完后逐个发射，
每个实例推入自己的实参映射、名字拼成 `基名_实参`；调用点用 `expr_type(第一个实参)` 推断类型实参、
登记实例、再发 mangled 名与替换后的形参类型。泛型声明本身整条跳过。
`skip_type` 也要会跳过 `<...>`（`let p: Pair<u32>` 的类型解析）；struct/enum 表扫描要跳过 `<T>` 找 `{`。
解锁 `native_gen.lomt` 与 `all_loment.lomt`（多模块+泛型）。

**trait 静态派发（M8）**：注册期识别 `impl`/`trait` 块 —— `trait` 声明里的 `fn` 只有签名（整条跳过，
不然会被当成空函数发射），`impl X for Y` 的方法登记为 kind=2 并记住接收者类型；发射时名字变成
`<接收者类型>_<方法>`（注释行与 `define` 都要改），**第 0 个形参 `self` 的类型取接收者类型**
（`self` 没有类型注解，原来会解析成 `)` 变成 i64）。方法调用 `x.m(args)` 在 `expr_atom` 里优先于
字段访问处理：接收者按第一形参类型求值，其余实参按被调方形参类型强制，最后
`call <ret> @<接收者类型>_m(<接收者> x, …)`。变量名的输出统一走 `emit_name_tok`（`self` → `__self`），
覆盖形参表/alloca/store/load/字段与下标访问等全部名字位置。解锁 `native_trait.lomt`。

**枚举与 `match`（M25/M26）+ struct/数组字面量（M23/M24）**：枚举表（name | nvariants | has_payload |
8×(variant, payload)，状态块 40960 起 80B/项）；`E::V` → 无载荷枚举写字面量下标、带载荷枚举只写
tag（`insertvalue {i32,i64} undef, i32 idx, 0`）；`E::V(x)` → 求值 → tag → `sext/zext` 到 i64 →
载荷 insertvalue；`match` → `switch i32 <tag>, label %L_mwild [ ... ]` + 逐臂
`extractvalue …, 1` → `trunc` → `store %bind.addr` → 臂体 → `br %L_mend`；
struct 字面量 `Blk { off: 3, len: 5 }` 与数组字面量 `[1,2,3,4]` 都是逐字段/元素 GEP + 求值 + store。
解锁 `native_agg.lomt`。

**这三个坑值得单独记（都是"多字符运算符其实是多个 token"）**：`::` 是 **两个 `:` token**、
`=>` 是 **两个 token（`=` 与 `>`）**、`->` 同理（返回类型在 `)` 之后第 3 个位置）。凡是按"一个运算符一个 token"
写偏移，遇到它们就会静默错位 —— `match` 的臂扫描因此几乎全错（通配臂判定从没命中）。
另外两个：臂遍要显式以 match 体的 `}` 终止（否则最后一个臂之后还会多跑一轮，把后续语句吞进臂体）；
通配臂的发射顺序是**标签 → 臂体 → 跳转**（我一开始写成标签 → 跳转 → 臂体，多出一条 `br`）。

**能力域（P4：M35/M36/M38）**：`capability blk_write : disk[0..4] revocable` 扫成域表
（name_tok | space_tok | lo | hi | revocable，状态块 6144 起 24B/项）；`guard NAME(expr);` 降级为
`zext i32→i64` → `icmp uge/ule`（lo/hi 是编译期常量）→ `and i1` → `br` 到 `%L_gok`/`%L_gtrap`
（trap 走 `__loment_abort`）→ 通过分支在 `@__loment_audit[cap_id]` 上加一。模块级还要插两个块：
`@__loment_audit`（有 `guard` 时）与 `@__loment_caps`（有 `capability` 时，space 名取
**FNV-1a 32 位**），顺序是 **运行时 → audit → caps → 堆全局 → 字符串全局 → 函数**。
解锁 `native_cap.lomt`（2408B 零差异）。

**中断处理函数（M33）**：`interrupt fn f() { … }` → 注释写 `; f -> interrupt (x86_intrcc)`，
签名是 `define x86_intrcc void @f(ptr byval([8 x i8]) %__frame)`（**无参数**，只列局部 alloca）。
注意 `fn` 的前一个 token 是 `interrupt`，用 `base - 1` 判定。

**自举阶段的现状（本轮最大成果）**：`lexer.lomt`、`parser.lomt`、`codegen.lomt` 三个自举阶段都能被
Loment 版 codegen 编译出与 `lomentc --emit-llvm` **逐字节相同**的 IR（lexer 26028B / parser 115464B /
codegen 557823B，差异行均为 0）—— 即"用 Loment 写的编译器生成自己的 IR 与参考实现完全一致"。
`checker.lomt` 的最后一块是 9 实参调用（`declare(...)`）：调用实参上限与形参类型槽都只有 8，
第 9 个实参被整条丢掉 → 上限提到 16、步长改 128B（函数数上限 128）。至此 **M82 的自编译判据全部达成**：
用 Loment 写的四段编译器生成自己的 IR 与参考实现逐字节相同 —— M83（三阶段定点）的前置条件已满足。

**依赖装载的边界（已落实为可核对的夹具）**：`lomentc.emit_llvm` 吃的是 `prepare()` 之后的
**依赖拼接单元**（`mods = deps + [mod]`）。测试夹具 `_unit_text()` 按 `resolve_deps` 的路径规则
取依赖、被依赖者在前拼接，并**断言模块名序列与 `lomentc.resolve_deps` 完全一致**
（规则一漂移就失败）。因此 Loment 版 codegen 的输入是"已装载的单元"，与 M80/M81 自举阶段的
边界一致；把 `resolve_deps` 搬进 Loment（需要文件 I/O）仍是 M83 之前的待办。

**已知的结构性边界（M82 之后要补）**：`lomentc.emit_llvm` 走的是 `prepare()` 之后的
**依赖拼接单元**（`mods = deps + [mod]`），所以像 `allocator.lomt` 这种自身不含 `/` 但
`use "bytes.lomt"` 的文件，运行时块是由**依赖里的除法**触发的。Loment 版 codegen 只吃
**单个编译单元**（不解析 `use`），因此这类文件当前必然不一致 —— 解法有两条：
①把 `resolve_deps` 也搬进 Loment（需要文件 I/O，属 P7 机器）；②明确把"前端装载器"拆给驱动
（驱动按拓扑序拼接缓冲，codegen 只编译拼接后的单元）。这条边界要在 M83 之前定下来。

另有一处 token 层 off-by-one：`->` 是两个 token，所以返回类型在 `)` 之后第 3 个位置。

## M83 · 自编译（编译器编译自身）✅ · M84 · 三阶段定点 ✅

**判据与证据**（`loment_p8_test::test_m83_m84_self_compile_and_fixed_point`，
也可由 `python tools/loment_bootstrap.py` 一条命令复现）：

| 阶段 | 怎么来的 | 结果 |
|---|---|---|
| stage1 | **Python 版** `lomentc` 编译 `loment/selfhost/codegen.lomt`（连同 Loment 版 lexer）→ `.ll` → clang 链出可执行文件 | 可运行 |
| stage2 | **stage1 自己产出的 `.ll`** → clang 链出（M83：编译器编译自己的产出可运行） | 可运行 |
| stage3 | stage2 的产出 → clang 链出 | 可运行 |
| **M84 定点** | stage1 / stage2 / stage3 各自生成 `codegen.lomt` 的 IR，三者**逐字节相同** | **965680 B，全等** |

定点不是巧合：stage2 对 `checker.lomt`、`ir_div.lomt` 的产出也与参考实现逐字节一致。

### 独立驱动（2026-09-11 补上的一步）

上面三个 stage 都还是"**被 C 驱动调用的函数**"：能编译自己，但没有能独立跑的编译器。
`loment/selfhost/driver.lomt` 把它补齐 —— lexer + codegen 接成**一个 ELF**：

```
clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld \
      -o fujoc-s loment/build/driver.ll
./fujoc-s loment/examples/demo.lomt > demo.ll   # 与 lomentc --emit-llvm 逐字节相同
```

内存向内核要（`brk`），**不用**语言自带的 64 KiB bump 堆：一个 190 KiB 的单元要约 4 MiB
token 表。把那个静态堆调大是错的方向 —— 每个 `alloc` 用户（内核模块尤其）的 `.bss`
都要跟着涨。这也是 M83 顺手补上 `整数 as ptr` 的原因（M67 只做了反方向的 `ptr as u64`）。

判据（`test_m83_selfhosted_driver_compiles_itself`，在 WSL 里执行）：

| 判据 | 结果 |
|---|---|
| 驱动跑自己的入口 → 与参考逐字节相同 | ✅ 1043790B |
| 用**驱动自己的产物**再链一个 ELF → 产物不变 | ✅ 二阶段定点 |
| 同一个二进制对 `native_res.lomt` / `demo.lomt` 也与参考逐字节相同 | ✅ 不是"只会编译自己" |

**自举抓到的六个真 bug**（都是"只有真实程序才暴露"的那类）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 驱动的单元里 `die(1)` 的实参类型变成了 `ptr` | 每函数形参类型表 `24576+i*128` 撞枚举表 `40960`：第 128 个函数正好压上去。`codegen.lomt` 自己的单元只有 118 个函数所以一直没露，驱动把 `bytes`/`lexer` 一起装进来（134 个函数）才暴露 | 步长改 80 |
| 2 | clang 报 `invalid cast opcode for cast from 'i64' to 'i64'` | 同宽异名转型（`i64 as u64`）被写成 `sext i64 %v to i64` —— 同宽时 LLVM 里本来就是同一个类型 | 同宽不再写指令 |
| 3 | clang 报 `unable to create block named 'entry'` | 形参叫 `entry`：LLVM 的块标签与局部值**共用名字空间**，每个函数的第一块都叫 `entry` | 驱动里改名；**语言侧仍是缺口**（`let entry: u32` 会踩到） |
| 4 | `(v / 256) as u8` 的 `trunc` 整个丢了 | `as` 左操作数类型原先取的是**整个转型表达式**的类型（u8），于是被判成"同宽转型" | 新增 `operand_type()`（见下） |
| 5 | 两个模块 import 同一个依赖时它被装了**两遍** | 去重表的长度按值传递，兄弟递归之间不共享：父先装 A（表长变 1），再装 B 时仍按旧表长查找，B 里 import 的 A 查不到 | 表长放进**一格内存**（指针传参）—— Loment 没有 out 参数也没有可变全局 |
| 6 | IR 里的字符串常量写成 `c"\0D\0A"`（CRLF）而不是 `c"\0A"` | 驱动按**原始字节**读源码，而参考实现用 `read_text`（通用换行）；源码里到处是**跨行的字符串字面量**，CR 就原样进了字面量 | 读入后 `strip_cr()`（丢 CR 保 LF） |

第 5、6 条尤其值得记：第 5 条是"没有 out 参数/可变全局时，共享可变状态只能装箱"这个语言事实
第一次咬到真实程序；第 6 条是 docs/150 里那条 CRLF 老坑的**同源变体**（上次是夹具写单元时踩，
这次是驱动器读文件时踩）—— 凡是"把源文本交给编译器"的地方都要问一句：换行归一了吗？

第 3 条是**语言侧的真缺口**，驱动只是绕开了：任何用户写 `let entry: u32` 都会踩到。
要么在生成期把撞名的局部改名，要么把首块标签改掉 —— 留给 M85 后半或冻结前处理。

### M85 前半：驱动自己装载 ✅（2026-09-11）

装载原本在夹具里（`_unit_text`）。现在驱动自己做：

1. 入口路径来自 `/proc/self/cmdline` —— `_start` 的 argc/argv 在栈上，不写内联汇编拿不到，
   而 `/proc` 给了同样的信息（代价是一次 `openat`+`read`）。
2. `use "..."` 递归解析：父文件先读进**自己的槽**（单元缓冲顶部每层一个 `SLOT`），
   再递归处理依赖，最后才把自己补上去 —— 顺序即"依赖在前"，与 `resolve_deps` 一致；
   同一路径只装一次（去重表按字节比较）。
3. **只有 `.lomt` 算依赖**：`lomentc` 的 parser 把 `use "...lom"` 记进 `mod.uses`（L0 声明，
   由 lomc 处理），只有 `.lomt` 进 `mod.imports`。驱动按同一规则过滤 —— 否则 `demo.lomt`
   会把 `lom/fujr.lom` 也拼进单元（多出整整一个模块）。
4. 单元里缺 `Option`/`Result` 时注入预置枚举（判据 = 词法扫 `enum X`，所以**字符串字面量里**
   写着 `enum Option<T>` 不会误判），注入点必须在**单元末尾**（参考实现是把预置枚举 append
   到模块枚举之后）。

判据 `test_m85_selfhosted_driver_compiles_corpus`：同一个二进制、逐个入口路径，
把 `loment/examples/*.lomt` 与 `loment/selfhost/*.lomt` 全部编译一遍并逐字节比对 ——
**40/40 一致**。

**顺带修掉的取型 bug**（第 4 条）：`(v / 256) as u8` 里的 `as` 左操作数
类型，原先把**整个转型表达式**的类型（u8）当成了操作数类型 → `as u8` 被当成同宽转型
整个丢掉。新增 `operand_type()`：括号里按"第一个操作数，或某个**顶层**二元运算符右侧的
操作数"取型（镜像 `expr_type(Bin) = lt or rt`），不看括号内的实参 ——
按 token 逐个看会拿到 `load8(tmp, ..)` 里 `tmp` 的 `ptr` 类型。

### M85 后半 · 缺口①：符号名是平的 —— 已修（**诊断层**），并说明为什么不 mangling

**现象**：驱动同时装 `codegen.lomt` 与 `checker.lomt` 时立刻炸：

```
error: redefinition of global '@.str.find_paren.0'    # 字符串全局名 = @.str.<函数名>.<序号>
error: invalid redefinition of function 'tok_kind'
```

`checker.lomt` 与 `codegen.lomt` 有 **5 个同名私有函数**：`count_args` / `find_paren` /
`skip_type` / `tok_is` / `tok_kind`；其中 `count_args` 与 `skip_type` **语义还不同**
（一个数形参一个数实参；一个跳过 `<T>` 一个不跳），撞名会**静默算错**。当时把 checker 侧五个
都加 `chk_` 前缀绕开（这个改动保留 —— 它顺带澄清了语义差别）。

**2026-09-11 补上规则之后**，这事有了正确答案：**这不是"绕开"，而是语言该报的错。**

- 发射出来的符号名**本来就是平的**。内核线按**名字**找入口（`_start` / `timer_isr` /
  syscall 包装，见 docs/155 §3），所以私有符号不能靠 mangling 变成 `<模块>_<名字>` ——
  平名字就是跨线 ABI，改名会把内核侧的符号查找打断。（这是"不做 mangling"的**理由**，
  不是偷懒。）
- 平名字的代价是：**一个单元里顶层名字必须唯一**。违反时后端会发出两条 `define @helper`
  （非法 IR），而调用点还会解析到同一个函数（**静默错编**）—— 比报错严重得多。
- 所以补的是**编译器规则**：`lomentc.check` 现在跨 `deps + [mod]` 检查顶层同名
  （函数/结构体/枚举/常量，排除由 `load()` 注入每个模块的预置 `Option`/`Result`），
  报 E-DUP 口径的"与模块 X 重名 —— 单元的发射符号是平的 (ABI), 请改名"。
- **两个实现一致**：自举 checker 因为**不分模块**，早就把这类重名报成 E-DUP 了 ——
  所以这一轮是"参考实现补齐到自举版的行为"，顺手把 M81 的判据从"码集 ⊆"钉成"码集 ="
  （`test_m81_cross_module_dup_is_rejected`）。驱动的闸门也覆盖了它：驱动自己装载两个文件
  之后才发现，同样拒（`neg_across/entry.lomt`）。

判据：`lomentc_test` 91/91（新增 `test_unit_wide_unique_names`）、`loment_p8_test` 11/11
（新增跨模块负例 + 驱动闸门用例），语料 41 个目标上**零误报**（都验过）。

### M85 后半 · 缺口②：checker 覆盖面 0/41 → 40/40，闸门已开（2026-09-11 本轮）

上一轮把 `checker.lomt` 接进驱动时它**拒绝一切合法程序**（0/41 单元无诊断）。那一轮只是
把缺口列了出来；这一轮按清单逐个补，现在**40/40 个拼接单元一条诊断都没有** ——
唯一剩下的 `native_raii.lomt` 是**非目标**（参考实现的 IR 后端自己也发不出来，它本就不是
单元的合法形状），不进语料，所以不算 checker 的缺口。**驱动那道闸门因此打开了。**

判据：`loment_p8_test::test_m85_checker_accepts_corpus_units` —— 41 个单元（依赖 + 本文件 +
预置枚举，与驱动器装载的同一份）跑自举 checker，除登记缺口外必须零诊断；缺口一旦被修好，
测试会提醒更新清单（不让它悄悄过期）。

补的九处（都是"token 层线性扫描"这个实现的固有盲区）：

| # | 假报 | 根因 | 修法 |
|---|---|---|---|
| 1 | `3@n:Some` `Ok` `Err`（**每个**单元） | `enum Option<T> { Some(T), None }` 里的变体声明长得像调用 | 第二遍跳过 `enum`/`struct`/`trait` 的声明体 |
| 2 | `3@n:return` | `return (a + b)` 被当成"调用名为 return 的函数" | 关键字表（`chk_is_kw`）：关键字后面就算跟 `(` 也不是调用 |
| 3 | `3@n:blk_write`（guard） | `guard NAME(idx)` 里的域名被当函数 | 前一个 token 是 `guard` 就跳过 |
| 4 | `2@n:T` `2@n:Pair` | 泛型：`fn f<T>` 的 `T` 未登记；`Pair<u32>` 的 `<...>` 没跳过 | 声明处把 tparam 登记成类型名（**不查重**——多个声明各用 `T` 合法）；`chk_skip_type` 跳过 `<...>` |
| 5 | `2@n:Result`（`native_res`） | 预置枚举在**单元末尾**（`lomentc.load` 是 append），而 `-> Result<u32,u32>` 在文件开头 | 加"第零遍"先把类型名收齐再查签名 |
| 6 | `2@n::`（`native_res`） | `if let Result::Ok(v) = r` 里的 `let` 是**模式**不是带类型的绑定 | 前一个 token 是 `if` 就跳过（与 codegen 同一条守卫） |
| 7 | `native_concat` 全篇 `str_concat` | 内建表没跟上（M2 新加的内建只在 `lomentc` 里） | 补进 `is_builtin` 的名字表 |
| 8 | `native_trait` 全篇错位（`2@10:-`） | `fn measure(self) -> u32;` 是**裸 `self`**（不带 `&`），被读成"参数名 self、类型 ;"；而且签名式方法没有函数体，返回类型扫描会一路走到 impl 块的 `{` | `scan_params` 认裸 `self`；返回类型扫描在 `;` 也终止 |
| 9 | `native_trait` 报 E-DUP | 两个 impl 都声明 `measure` —— 平坦符号表里必然撞名（编译器真实的派发名是 `Small_measure`/`Big_measure`） | impl 块**不在检查器子集内**（方法调用一律走 `x.m()`，已被 `.` 那条规则排除；方法体内的调用检查照做） |

**过程中抓到的两个真 bug（都是"崩溃而不是报错"，值得单独记）**：

1. **越过 eof 的游标**：第二遍的声明体跳过正好落在文件尾时，循环尾还做了一次 `i = i + 1`
   → 游标进到词法缓冲**之外**的未初始化字节 → 那里若恰好不是 `eof` 就一路读下去，直到
   读到未映射内存而段错误。症状是"单个 enum 崩溃、两个 enum 正常"，而且**重编译一次结果
   就变**（堆布局运气）—— 这类"布局相关"的崩溃最难认，最后靠"把 `errs`/`toks` 换成 1 MiB
   看它还崩不崩"分离出结论：不是缓冲太小，是游标跑飞了。
2. **跳过之后又前进**：`i = chk_skip_body(...)` 返回的正好是**下一个声明的关键字**，而循环尾
   又 `i = i + 1` → 把那个声明整个略过（`struct B {..}` 后面紧跟 `enum C {..}` 时，
   `C` 的变体就会被当成调用）。修法是"跳过了就不再加一"（用 `skipped` 标志，Loment 没有
   `continue`）。

### M85 后半 · 缺口③：规则等价性**可测量**了，批次 1（声明级规则）已落地（2026-09-11 本轮）

`0/41 → 40/40` 只说明"自举 checker 不再误拒合法程序"，它**不说明**自举 checker 与参考实现
判定相同 —— 更宽松的实现同样能放行 40/40。判定是否等价需要一个负例矩阵，于是有了
`tools/loment_rule_parity.py`：

- 一条参考规则配一个**最小负例**，两边各跑一遍，按 `loment_diag` 的统一错误码口径比**码集**；
- 状态分五档：`EQUAL` / `MISSING`（自举少报）/ `EXTRA`（自举多报 = 假阳性）/
  `DIFF`（码口径漂移）/ `NOPY`（探针本身失效）；
- **门禁是棘轮**：`eq >= BUDGET` 且 `EXTRA == DIFF == NOPY == 0`；补完一批就把 `BUDGET`
  往上调，**只调低等于隐瞒缺口**。`loment_rule_parity` 已进 `ci.py` 的静态门禁。

先说测得的地板：**接入时的实测是 9/60 等价、51 个缺口**（60 条规则负例，覆盖参考实现
`errs.append` 的 81 条消息模板）。这一轮补完**批次 1（声明级规则）后是 24/60**，且
假阳性与码漂移都是 **0**。批次 1 补的规则（都只吃 token 流，不需要表达式类型推断）：

| 规则 | 参考口径 | 自举实现 |
|---|---|---|
| 形参重名 | `函数 f 参数 a 重复` (E013) | `scan_params` 里的形参名表 + `chk_tab_has` |
| 字段/变体重名 | `结构体 S 字段 a 重复` / `枚举 E 变体 A 重复` (E013) | `chk_struct_body` / `chk_enum_body` |
| 空类型 / 与基类型同名 | `结构体 S 为空` / `与基类型同名` (E009) | 同上 + `is_base_type` 前置判断 |
| 字段/载荷类型未声明 | `类型 Foo 未声明` (E002) | 对字段与变体载荷跑 `check_type` |
| 常量类型必须是整型 | `常量 C 类型必须是整型` (E001) | `chk_is_int`；**不做** `check_type`（参考实现也不做，多报就是假阳性） |
| 能力重复/域反序/占用 excluded 空间 | `能力 c 域下界 5 > 上界 2` 等 (E004) | 能力表 + **第一遍半**（等 caps 与 excluded 都收齐，故与声明顺序无关） |
| `guard` 未声明的能力 / 字面量索引越界 | `guard 引用了未声明的能力` (E004) | 第二遍用能力表判定 |
| 内建实参个数 | `内建 str_len 需要 1 个实参` (E003) | `chk_builtin_arity`（表与 `is_builtin` 同名清单由测试钉住） |

**两条分类纪律**（这一轮顺手修掉的、会让测量结果失真的东西）：

1. `tools/loment_diag.RULES` 的**顺序即优先级**，而 `E002` 必须排在 `E001` 前面 ——
   `载荷类型 Foo 未声明` 里既有"类型"又有"未声明"，用户要做的是"先声明 Foo"，不是
   "检查两侧类型"。另外新增 `E014`（赋值目标非左值）/`E015`（字段/下标/取址）/
   `E016`（数组字面量）/`E017`（`as` 转换），并把 `无字段/缺字段/重复初始化` 从 E009
   挪到 E015 —— 码按**修法**分，不按消息措辞分。
2. 参考实现能发出的消息模板**必须全部可分类**：分类表漏一条，那条规则在对照里就变成
   "两边都成 E999 被丢掉"，**缺口会静默消失**。`loment_tools_test` 新增
   `test_m64_all_reference_messages_are_classified`（ast 抽取 81 条模板逐条分类），
   以及 `test_m81_builtin_tables_match`（自举 `is_builtin` 的名字集合 == `lomentc.BUILTINS` ∪ `{slice_len}`）。

**还差什么**：60 条里剩 28 条，落在两处 ——

- **表达式级规则的第二批**：字段/下标/结构体字面量（E015）、数组字面量（E016）、`as`
  合法性（E017）、实参与内建实参类型（E001）、`match` 的模式/穷尽性（E008）、`?` 误用（E010）、
  方法调用（E002）；
- **移动/借用检查**（E006/E007/E011/E012）—— 参考实现是 `_move_check` + `_borrows_of`
  两趟 AST 遍历，token 版要先把"作用域/路径"建模起来。

诚实口径：**当前自举 checker 是"声明级规则 + 语句级类型比对等价、其余表达式级与移动借用缺失"的子集**。

### M85 后半 · 批次 2（语句级类型比对）与它顺带抓到的两个真 bug（2026-09-11 本轮）

批次 2 的第一段做了 token 级类型推断器 `chk_ty_expr`（对照 `lomentc.expr_type` 的 AST 版）：

- **类型表示**：`(kind << 28) + payload`，0 = 未知。基类型用内码，具名类型用**符号表槽位**
  （不是 token 下标 —— 同一个名字在源码里出现两次是两个 token，用下标当身份会把
  `fn max_of<T>(a: T, b: T) -> T` 的 `T` 判成两种类型）。
- **保守出口**：复合类型（数组/切片/泛型实例）、类型形参 `T`、字段访问、下标、方法调用
  一律记**未知** —— 未知只可能"少报"，而假阳性会被语料 40/40 的零诊断判据立刻抓住。
- **参照语义**：深度 0 有比较/逻辑运算符 -> bool（比较优先于算术，`v % 2 == 0` 是 bool）；
  否则取**最左**操作数的类型（`Bin` 返回 `lt or rt`，左结合链最左即它）；`as T` -> T。
- 落地的语句规则：`let` 类型比对 + 数组长度比对、`return` 比对、`if`/`while` 条件必须是
  bool、`for` 上下界必须是整型、赋值（未声明 -> E002；类型不符 -> E001）。
- 实测 **32/60 等价、0 假阳性、0 码漂移**（批次 1 后是 24/60）。

顺带抓到的**两个真 bug**（都是"能静默错编/停机"级别，不是笔误）：

| # | bug | 现象与根因 | 修法 |
|---|---|---|---|
| ⑨ | 自举 codegen 的**实参上限 10** 会静默截断 | 形参类型表步长 80 = 10 槽 x 8 字节（加宽会撞 40960 的枚举表），实参循环写 `a < 10`。第一个 11 实参的调用（新增的 `chk_body`）被丢掉最后一个实参 —— IR 少一个实参，参考实现照发，逐字节判据报"两个编译器不一致"。 | ① `chk_body` 压到 10 个形参（区间打包进 u64）；② 超限时 `panic(10)` **响亮失败**；③ `test_m85_codegen_arg_arity_is_loud` 同时钉住闸门与"语料最大形参数 <= 10" |
| ⑩ | checker 与 codegen **共用 64 KiB 语言堆**，批次 2 的 `alloc(2048)` 越界 | `alloc` 的边界检查走 `@__loment_abort`，在自举驱动里就是一条**非法指令 (SIGILL)** —— 从输出看像"编译器崩了"。codegen 的 `emit_module` 一次要 49152B，checker 原来 14672B，加到 16720B 就越过 65536。 | 按语料实测收缩各表（字段<=2/变体<=3/形参<=11/每函数 let<=152），降到 12048B；`test_m85_heap_budget` 静态钉住"两边 alloc 之和 + 4 KiB 余量 <= 64 KiB" |

⑩ 值得单独记一句：**这类"资源耦合"缺陷只会在两边都变大时出现**，而它的表现形式
（SIGILL）与"代码生成错了"极像。结构解已经落地：`checker.lomt` 拆出 `check_arena`
（缓冲由调用方提供），**驱动按 `chk_arena_*` 的分段约定从 `brk` 拿那 12048B**
（driver 的注释里本来就写着"内存来自内核，不用语言自带的 64 KiB bump 堆"），
语言堆里只剩 codegen 自己的 49152B —— 驱动路径余量从 4.3 KiB 变成 16.4 KiB。
`check()` 保留为"从语言堆开 arena"的薄包装（C 夹具与测试用），
`test_m85_heap_budget` 把两条路径的数字都报出来。

### M85 后半 · 下一段（未做）：复合类型表示 + 表达式遍历器

剩下的 28 条缺口里，有一批**不能**靠"保守记未知"混过去 —— 参考实现的判定方向是反的：

```python
# lomentc.py: Index
ot = expr_type(e.obj, ...)
if ot is None or not (_is_array(ot) or _is_slice(ot)):
    errs.append(f"... 对非数组/切片类型取下标")     # <-- 类型**未知**时也报
```

也就是说 `对非数组/切片类型取下标`（E015）在"类型未知"时**要报**，而本 checker 的
"未知 -> 跳过"策略在这条上必然少报；反过来若照搬"未知就报"，任何我算不出类型的数组
（`let a: [u32; 4]`、切片形参、`&arr`）都会变成**假阳性**。同理还有：
`&x`/`&mut x` 只能作用于数组（E015）、`slice_len` 实参必须是数组/切片（E001）、
只读切片不能写（E011）、数组字面量的长度/元素一致性（E016，要和声明类型比）。

结论：这一批的前提是**把复合类型建起来**，而不是继续用"0 = 未知"糊。具体计划：

1. 类型编码加两种 kind，指向一张小的**类型表**（放在 arena 里，另开 1 KiB = 85 条 x 12B）：
   - `kind 3 = 数组`，表项 = (元素类型 id, 长度)
   - `kind 4 = 切片`，表项 = (元素类型 id, 是否 mut)
   （基类型/具名类型继续用内联的 `(kind<<28)+payload`，它们没有分量。）
2. `chk_ty_range(src, t, syms, n, tyt, lo, hi)` —— 从一个**类型 token 区间**建类型：
   `mut [T]` -> 切片(mut=1)、`[T]` -> 切片(mut=0)、`[T; N]` -> 数组(T, N)、
   `Name<...>` -> 未知（泛型实例，见上文）、单 token -> 基类型/具名。
   声明处的类型、`as` 的目标、以及 `let` 的注解都改走它。
3. `chk_ty_expr` 学会产出复合类型：`&arr` -> 切片(元素)、`&mut arr` -> 切片(元素, mut)、
   数组字面量 -> 数组(元素, 个数)；下标 -> 元素的类型。**参数量**：现有的
   `chk_ty_expr(src,t,syms,n,env,envc,lo,hi)` 加一个 `tyt` = 9 个；再往上包一层
   会发诊断的遍历器 `chk_walk(...)` 必须把 (lo,hi) 打包成 u64 才能守住 codegen 的
   10 形参上限（这次已经因为 11 个形参踩过一次，见上文 bug ⑨）。
4. 遍历器 `chk_walk` 按"深度 0 运算符切段 + 原子形式"递归（对照 `_walk_expr`）：
   段内处理数组字面量 / 结构体字面量 / 枚举构造 / 调用实参 / 方法调用 / 字段 / 下标。
   实参类型靠符号表里的 `aux`（形参表的 `(`）**按需重扫**，不建二维表。
5. 之后才是移动/借用（E006/E007/E011/E012）—— 参考实现是 `_move_check` + `_borrows_of`
   两趟 AST 遍历，token 版要先有作用域与"变量路径"的概念。

已经为此打好的地基（本次提交）：符号表 stride 20 带 `aux` 一格、`check_arena` 的
brk 解耦（arena 有空间放类型表）、堆预算与实参上限两条静态闸门。

### M85 后半 · 批次 2 完成：自举 checker 与参考实现 **63/63 规则等价**（2026-09-12）

上一轮把批次 2 第一段（语句级类型比对）做到 32/60；这一轮把剩下的补完，实测
**63/63 等价、假阳性 0、码漂移 0**，`loment_rule_parity` 的棘轮预算从 32 调到 63。
（探针最后三条是 M13 移动 E006 / M17 悬垂 E012 —— 参考实现 `_move_check` 的 token 版。）
补的东西：

| 组 | 内容 |
|---|---|
| 复合类型 | 类型表加**数组/切片**两种 kind（元素类型 + 长度/可变性），`chk_ty_range` 从类型 token 区间建类型：`[T; N]` / `[T]` / `mut [T]` / 基类型 / 具名 |
| 表达式遍历器 | `chk_tex` = 遍历 + 类型推断 + 诊断三合一（对照 `_walk_expr` + `expr_type`）：深度 0 分段，**比较优先于 `as`**（`i < n as u64 && …` 的顶节点是 `&&`），算术取最左操作数（`Bin` 返回 `lt or rt`） |
| 原子形式 | 数组字面量（空/长度/元素一致性）、结构体字面量（未知结构体/无字段/重复初始化/缺字段）、枚举构造 `E::V(x)`（未知枚举/无变体/无载荷带参/载荷类型）、调用实参类型（含数组→切片协变、可变切片要求）、`slice_len` 实参、字段访问、下标、`as` 合法性（含 M67/M83 的两条整数-指针互转）、`match`（主体必须枚举/无变体/重复模式/载荷绑定/穷尽性）、`?`（E010）、**借用冲突**（E007：同一次调用里既 `&mut` 又 `&`、或 `&mut` 两次） |
| 变量声明性 | "使用未声明的变量"（E002）—— 判据是"在不在环境里"而**不是**"类型是不是 0"，否则每个泛型形参 `a: T` 都会被误报 |
| 移动/悬垂 | `chk_moves`（对照 `_move_check`）：非 Copy 变量（struct/数组）被 `let t: T = s;`、非切片实参、赋值传出去后就**已移动**，再用 -> E006；`return &局部` -> E012（形参不算局部）。已移动表放在类型表块尾（名字按**文本**比，不是 token 下标 —— 同一变量在每条语句里都是不同的 token） |
| impl 方法 | 符号表按 (接收者类型编码, 方法名) 登记 `K_METHOD`（参考实现是改名成 `Type_method`），`chk_lookup_slot` 刻意跳过方法 —— 裸 `measure(...)` 在两边都是"未定义的函数" |

**三处刻意偏离**（都是保守方向：宁可少报也不假阳性，逐条写在这里备查）：

1. **下标/`&` 在"类型未知"时不报**：参考实现的判定是 `ot is None or not 数组/切片 -> 报`，
   即"类型未知也要报"。本 checker 算不出类型的数组（泛型实例等）一律记未知，照搬就会
   假阳性；所以只在"已知且确定不是数组/切片"时报。
2. **`match` 主体类型未知时不报**（参考实现会报）；已知非枚举或字面量主体时报。
3. **`?` 只在能确定"不是 Result"时报**：参考实现按单态化后的 `Result_*` 实例解糖；
   这里算不到实例，于是反着判 —— 整型字面量、或被调函数返回类型 token 不是 `Result`
   时才报 E010（`native_res.lomt` 的 `parse_small(v)?` 因此不会被误报）。

**这一轮又抓到三个真 bug**（都是"能静默错编"级别）：

| # | bug | 现象与根因 | 修法 |
|---|---|---|---|
| ⑪ | 自举 codegen 的**每函数表容量只有 192** | 布局是 `函数表 12288+i*12 / fk 表 14848+i*8 / 形参表 24576+i*80`，单元函数数超过 192 就写穿到参数替换表上 —— `fkind` 读出来是 2，少数函数被改名成 `<接收者>_<方法>`（`is_lomt_emit_div_mnemonic`）。批次 2 把驱动的单元推到 246 个函数才现形（逐字节判据只报"两个编译器不一致"） | 按 **256** 重排三张表（fn 12288+i*12、fk 15360+i*8、形参 24576+i*80、枚举表 45056+i*80），并加 `test_m85_codegen_table_capacity` 静态钉住"最大单元函数数 <= 容量" |
| ⑫ | **字符串字面量的 token 原文 = 内容本身** | 词法器给字符串 token 的 `val` 是它**里面**的内容（`"impl"` 的原文就是 `impl`），于是 `tok_is(i, "impl")` 会匹配源码里的字符串 `"impl"`。用 Loment 写编译器必然写出这些字符串（关键字表、内建名表），所以自举 codegen 把 `chk_tok_is(src,t,i,"impl")` 里的字符串当成了 impl 块，之后的函数全被按方法改名 —— 参考实现没这个坑是因为它的 parser 用 `at(kind, val)` **一起判 kind** | `tok_is`（codegen）与 `chk_tok_is`（checker）对 string token（kind 2）直接返回 false |
| ⑬ | 同一函数里两次 `let` **同名局部** | 参考实现按名字复用槽位（只发一条 `%x.addr = alloca`），自举 codegen 发**两条** —— 逐字节不一致，而且第二条会遮蔽前一个值 | checker 里那个遮蔽的 `let oc` 已删除；**自举 codegen 侧仍是缺口**（语言规则应禁止或明确"后声明复用同一槽"），留给 M88 前收口 |

### M85 字面判据：`lomentc_test` 的可映射部分已经在驱动器上跑（2026-09-12）

M85 的字面判据是"`lomentc_test` 在自举版上通过"。那 91 条判据分三类，**第三类不可映射的原因是硬的**，
逐条记在这里备查：

| 类别 | 条数（约） | 可映射 | 判据 / 理由 |
|---|---|---|---|
| **规则判定**（某程序该不该被拒） | ~30 | ✅ | `tools/loment_rule_parity.py` 的 63 条规则负例**直接喂驱动器**：全部非零退出 + 不产 IR，而且**没有一条因信号而死**（`test_m85_driver_gate_on_probe_cases`）。检查器开发期崩过两次（游标越过 eof、递归死循环），"进程直接没了"与"报了个错"必须分开判 |
| **正例与发射**（该过的要过、产物要对） | ~10 | ✅ | 40/40 语料零诊断 + 40/40 目标与参考**逐字节一致** + 三阶段定点。确定性由"对着同一份参考字节"隐含覆盖 |
| **夹具侧 3 目录加载** | 1 | ✅（已被覆盖） | `driver.lomt` 的单元本身就是四级 `use` 链（driver → codegen → lexer / bytes），递归装载与去重已由"驱动自编译"覆盖 |
| 运行时 / 双后端实跑 | ~15 | ❌ | `m2` 双路径实跑、`m4`/`m15`/`m18`/`m19`/`m20`/`m21`/`m22` 要**真跑编译产物**或对比 Rust 路径；驱动只到 IR，运行需要裸机/ELF 宿主（那部分由 p9 与 QEMU 矩阵负责） |
| Python API 形状 | ~25 | ❌ | `llvm_*` 断言 IR 文本片段、`potato_*` 断言内核对象/形状、`m14` 分配审计、`m45`/`m46` 判 Python 侧工具链接口 —— 判的是**参考实现自己的 API**，不是语言语义；语言语义那部分被"逐字节一致"以更强的形式覆盖 |
| 解析期错误 | ~5 | ❌（靠参考实现兜） | 驱动器是 lex → check → emit，**没有 parser**；`const C: bool = true;` 这类在解析期（`int_lit`）就被拒的形状它看不见。当前 63 条探针里 **0 条**属于这类（都构造在检查期），`PARSE_LEVEL` 表是空的 |

**这一路抓到五个真 bug**（前四个都配了静态闸门，第五个仍开放）：实参上限 10 静默截断（⑨）、
checker 与 codegen 共用 64 KiB 堆导致驱动 SIGILL（⑩）、每函数表容量 192 被写穿导致函数被改名（⑪）、
**字符串字面量的 token 原文撞关键字判定**（⑫）、同一函数里同名 `let` 产生双 `alloca`（⑬，仍开放）。

### 闸门打开：驱动先 check 再发射 ✅

覆盖面到 40/40 之后，`driver.lomt` 把 checker 接上并**先检查后发射**：

```
./fujoc-s loment/selfhost/neg/unknown_fn.lomt    # 退出 1, stderr 报诊断, stdout 无 IR
./fujoc-s loment/examples/demo.lomt > demo.ll    # 退出 0, 产物与参考逐字节相同
```

诊断格式与 p8 的 C 夹具同源（`E<码> @<token> line <行>: <片段>`）。判据
`test_m85_driver_checks_before_emitting`：**12 个负例全部非零退出** + 带诊断 + **不产出 IR**；
正例零退出。批次 1 新增的 6 个负例（形参重名 / 空结构体 / 与基类型同名 / 能力域反序 /
内建实参个数 / 常量类型）都已进入这道闸门。**语料那 40 个单元的装载测试同时也在守着这件事**
——闸门要是误报，那个测试会先红。

**原来的假报清单（2026-09-11 早，13 文件版，已按下表逐条销掉）**：

| 构造 | 现象（错误码@token） | 缺什么 |
|---|---|---|
| 内建函数 | `3@70:load32` `3@140:store32` `3@15:str_concat` | 调用点只查函数表，**内建表没进检查** |
| 枚举构造 | `3@53:Circle` `3@23:Some` `3@27:Err` | 把 `E::V(x)` 当函数调用 |
| 跨模块调用 | `3@358:double`（demo → mathutil） | 检查器**不解析 `use`**（M81 的子集边界），单文件跑必然报 |
| 泛型声明 | `2@39:T` `2@234:Pair` | `fn f<T>` / `Pair<u32>` 的 `<...>` 没跳过，形参与类型都读错位 |
| `guard` | `3@25:blk_write` | 域检查语句被当成函数调用 |
| 方法与 trait | `native_trait.lomt` 报 1/2/4 三种码 | `impl`/`trait` 块、`x.m()` 全不认识 |
| 语句边界 | `3@335:return` `2@20:{`（`native_raii`） | 某处解析错位后把后续 token 当成调用实参 |

清单本身有信息量：它说明"子集检查器"与"能当闸门的检查器"之间差的不是规则条数，
而是**对内建/泛型/枚举/方法/trait 这批构造的建模**。补完之前它只能继续当 M81 那种
"负例集判定"用。

## M86 · 未做（诚实说明）

| # | 里程碑 | 为什么现在做不了 |
|---|---|---|
| M86 | 自举性能优化 | 依赖 M85 收尾（checker 接进驱动后才有稳定的端到端基线） |

## M85 后半 · 还没做完的部分（诚实说明）

| # | 缺口 | 说明 |
|---|---|---|
| ~~符号表全单元扁平~~ | **已修**（见上文缺口①）：单元级名字唯一成了编译规则，两个实现一致报 E-DUP；不做 mangling 是因为平名字就是跨线 ABI（docs/155 §3） |
| ~~checker 的**表达式级**规则~~ | **已闭合**（见下文"批次 2 完成"）：token 级类型推断器落地后 **63/63 等价、0 假阳性、0 码漂移**，棘轮预算 24→32→60→63 只升不降（`tools/loment_rule_parity.py`，已进 `ci.py`）。三处**刻意保守偏离**逐条记在下文并写进 `docs/158` 冻结面开放项 |
| ~~`lomentc_test` 的判据还没搬到自举版~~ | **可映射部分已闭**（M85）：`test_m85_driver_gate_on_probe_cases` 把 63 条规则负例直接喂驱动（非零退出、无信号），40/40 语料零诊断、40/40 目标逐字节一致；**不可映射的三类**（运行时/双后端实跑、Python API 形状断言、夹具侧 3 目录加载）逐条列在 docs/150 末尾，无 `PARSE_LEVEL` 豁免 |

自举进度是真实的：**lexer → parser → checker → codegen 四段都已用 Loment 实现，
并分别与 Python 版逐 token / 逐字符 / 逐错误码 / 逐字节对照通过**（M79–M82），
且**四段都能被 Loment 版 codegen 编译出与参考逐字节相同的 IR**，后端进而达到三阶段定点（M84）。

**M82 已收官**：全部 40 个可发射目标逐字节一致（唯一剩下的 `native_raii.lomt` 参考实现自己就报
`native: inb 暂未在 IR 后端实现`，是非目标）。**M85 前半**（驱动器自己做装载）也已达成 ——
同一个二进制按入口路径把整个语料编译一遍，逐个逐字节一致。**现在剩下的自举长杆只有
"符号作用域"与"checker 的 impl/trait 建模"这两件。**

## M87 · 引导脚本 ✅

`python tools/loment_bootstrap.py` —— 一条命令走完自举前端：

1. 重新生成 lexer/parser/checker 的形式对象并与磁盘**逐字节**核对；
2. 跑 M79/M80/M81 三项对照；
3. 打印报告（`--json` 可机器读）。

## M87 延伸 · 无 Python 自举（种子）✅

自举链原先只能从 **Python 版编译器**起步（M83 的 stage1 由 `lomentc` 现编）。现在起点固化
成工件 `loment/build/selfhost_driver.ll`（1.63 MB，参考实现为 `selfhost/driver.lomt`
发射的完整单元 IR），于是重建编译器只需要 **clang + POSIX sh**：

```sh
sh loment/bootstrap.sh          # 1) clang(种子)->stage1 2) stage1 产出==种子
                                # 3) stage2/stage3 定点 4) 跨阶段对 native_res 一致
python tools/loment_seed_test.py  # 3/3: 种子不许过期 + 脚本禁解释器 + 定点证明
```

三条判据：种子与参考逐字符一致（**过期即红**）、启动脚本命令位置不许出现任何解释器
（静态扫描 + 强制 LF）、自举定点在"只有 clang"的条件下成立。详见
`docs/159-loment-seed-bootstrap.md`（含剩余第三方语言的诚实清单）。门禁已进 `ci.py`。

## M88 · 发布校验和 ✅

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` 产出
一份 sha256 清单（**行数 = 工件数**，与 `release-manifest.json` 同源、换行无关）。

tag：**`v0.1.3.4-alpha`**（annotated）打在 `Fujoos-FujoLang-DEV` 的冻结提交上并已推送
origin。它是 **dev 分支快照**，不是发布分支：冻结面见 `docs/158-loment-freeze.md`，
发布线要等 M100（外部审计）之后。tag 可随时 `git tag -d` / `git push origin :refs/tags/...` 撤销。

