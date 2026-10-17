# 173 · Loment 的 FFI：调用外部库

> 2026-09-16 用户定题"兼容 Python / Java / C / C 系列 / Rust 等至少 7 个主流语言的库"。
> 问清之后的两条前提：**"兼容" = 直接调用外部库**（不是"从声明生成绑定"）；
> **全部自己做**（不把 FFI 构建交回 clang/lld，保住"不依赖任何外部工具链"），
> 用户**接受这是跨多个版本的工程**。
> 规范：`docs/143` §3.1 · 冻结面：`docs/158` §2/§5 · 本文件讲**分阶段与边界**。

## 0. 一句话

**`extern fn` 让 Loment 声明一个外部函数，链接时把那边的机器码接进来。**
纯 Loment 程序仍然是 freestanding 的；**只有声明了 `extern` 的程序**才带外部依赖
（可以让它依赖 libc / 对方运行期 —— 用户 2026-09-16 明确允许）。两条路并存，不是二选一。

## 1. 起点：今天是什么都没有

先说清楚代价，免得把这件事想小了。查过之后，**现有一切为零**：

| 需要的东西 | 今天 |
|---|---|
| 声明外部函数 | 没有 `extern`，也没有任何等价物 |
| 调用约定 | **故意不是 C ABI**：`tools/lomelf.py:17` 写着"实参一律走**栈**……**不保证 System V / C ABI** —— 原生产物 v0 只保证自身自洽，**不给 C 调**" |
| 多输入链接 | `lomelf` 只吃**一个** `.ll`（`tools/lomelf.py:2150` 直接拒绝多个） |
| 符号表 / 重定位 | 自己的产物**没有节表**（`e_shoff=0`）、没有 `.symtab`/`.rela`；重定位只在**同一个缓冲区内**（`Asm.fixups`），未定义标签直接报错 |
| 外部符号 | `parse_ll` **跳过 `declare` 行** —— 外部符号连表示都没有 |
| 归档 / 动态库 | 没有。PE 侧唯一的导入机制是**硬编码的 11 个 kernel32 函数**，只给手写垫片用，Loment 代码够不着 |

唯一能"出去"的原语是裸 syscall（ELF 上任意号可用，PE 上只有垫片放行的 8 个）。
但 syscall 不是 FFI：它不能调用别人写好的库。

## 2. 四个阶段

真正难的是 1、3、4；阶段 2 之后"7 个语言"就只是**加配方加判据**（C ABI 是同一个机制）。

| 阶段 | 内容 | 解锁 | 状态 |
|---|---|---|---|
| **1** | `extern fn` 进语言；C ABI 调用路径；`lomelf` 能读外部 ELF 目标文件并链接（多输入 + 符号解析 + 重定位） | **C** 端到端 | **0.1.4-pre2** |
| 2 | 归档（`.a`/`.lib`）成员选择；每个语言一条构建配方 + 一条判据 | + C++ / Rust / Zig / Go(`-buildmode=c-archive`) / Swift / C#(NativeAOT) / Fortran —— **C ABI 一族凑满 7+** | 后续版本 |
| 3 | 动态库装载：自己解析 ELF `PT_DYNAMIC` / PE 导入表，自己做映射与重定位 | "装好的 `.so`/`.dll`" 能用 | 后续版本 |
| 4 | 运行期嵌入：CPython（`libpython`）、JVM（JNI） | **Python / Java** | 后续版本 |

**为什么 C ABI 一族能一次覆盖 7 个语言**：C++（`extern "C"` 包装）、Rust（`#[no_mangle] extern "C"`）、
Zig（`export fn`）、Go（c-archive）、Swift（`@_cdecl`）、C#（NativeAOT 的 `[UnmanagedCallersOnly]`）、
Fortran —— 它们**导出的都是同一种符号与同一套传参约定**。所以阶段 2 的工作量不在"语言数量"，
而在**归档成员选择**这一件事上。

**第二条腿（进程桥）**：Python / Java / JS 这类**运行期**不是 C ABI 库，嵌进来要先把动态库装载
做出来（阶段 3/4）。在那之前，`loment/lib/proc.lomt` 让它们现在就能用：起一个解释器进程，
把代码交给它，把它的输出读回来 —— 对方照旧 `import` 自己的库。**代价是多了个进程边界：
传的是字节流，不是指针**（不能传结构体）。两个语言在这条腿上是**同一个函数**，换个命令而已。
**只在 Linux/ELF 可用**（PE 垫片没有 fork/pipe）。

**Python / Java 为什么必须排到阶段 4**：它们不是"导出 C ABI 的库"，而是**运行期**
（CPython 解释器 / JVM）。要调用它们的代码，得把解释器搬进进程 —— 而那需要阶段 3 的动态装载
（`libpython.so` / `libjvm.so`）打底。这一阶段是另一个量级的工程。

## 3. 阶段 1 的边界（这一版做什么、不做什么）

**做**：

- `extern fn name(a: i32, b: ptr) -> i32;` —— 只有签名、没有函数体、末尾分号（`docs/143` §3.1）。
- 签名**只收标量**（`i8..i64` / `u8..u64` / `bool`）与 `ptr`。
- 调用点按平台 C ABI 传参（ELF: System V；PE: Microsoft x64）；纯 Loment 调用**一行不动**。
- `lomelf` 读一个外部 ELF64 可重定位目标文件（`.o`），与 Loment 自己的产物合并、解析符号、
  应用重定位（至少 `R_X86_64_PC32`/`PLT32`/`64`/`32S`）。
- 门面判据：一个 `.c` 编成 `.o`，Loment 调它，**`lomelf` 单独链接**，跑出正确答案。

**不做（阶段 1 明确不主张）**：

- **`str` 不能进出 extern**。`str` 是"指针 + 长度"，**不是 C 字符串**。真要用字符串，
  得先有"谁负责 NUL 结尾、谁负责释放"的层次（`cstr` / `cstr_from` 之类），那是阶段 2。
  签名里出现 `str` 直接报错，**不静默错编**。
- **聚合按值**（结构体/枚举/数组/切片）不进签名。System V 的分类规则（字段拆进寄存器、
  大于 16 字节走内存）是独立一块，留到阶段 2。
- **变参函数**（`printf` 那类）不支持。
- **回调**（把 Loment 函数当函数指针传出去）不支持 —— `lomelf` 至今不支持间接调用。
- **动态库**（`.so`/`.dll`）不支持，只做静态链接。
- **不是"7 个语言完成"**：阶段 1 只到 C。这是把话说在前面的地方。

## 4. 五个接口面（`loment build` 怎么用）

```bash
# 把外部目标文件喂给链接器（阶段 1 只有这一种）
loment build app.lomt --link libfoo.o -o app

# 阶段 2 起（归档与库目录）
loment build app.lomt -L ./lib -l foo -o app
```

`run` 同样接受这些参数（它在临时目录里编完直接跑）。

## 5. 与既有决定的关系（三条被重新划线的地方）

FFI 与三条既有承诺冲突，逐条收窄而不是推翻（原文都留着，只把范围说清）：

| 位置 | 原话 | 收窄成 |
|---|---|---|
| `docs/168` §1/§4.3 | 动态链接"**没有**"、"**不做二进制分发**" | "**Loment 库**不外发二进制 / 不吃动态链接"；**外部库另外算** |
| `docs/158` §2 发射符号约定 | "不保证 C ABI —— 不给 C 调" | "**纯 Loment 调用**不给 C 调"；`extern` 调用点走平台 C ABI |
| `docs/155` §3.7 | "无未定义符号"当不变量 | "**纯 Loment 单元**无未定义符号" |

内核线手上的产物全是纯 Loment，所以**这三条收窄对它们的承诺一字未变**。

## 5b. 实现状态（诚实账，2026-09-16）

| 件 | 状态 |
|---|---|
| 规范（`docs/143` §3.1 / `docs/158` §2/§5） | ✅ |
| 语言面：`extern fn` **两个实现都实现**，IR 逐字节一致 | ✅ `lomentc_test` 112/112 · `loment_p8_test` 16/16 |
| 诊断码 **E021**（签名形态不支持） | ✅ |
| 链接器 `lomelf.py`：读 ELF64 目标文件 + 多节拼接 + 符号解析 + **C ABI 传参** | ✅ |
| **重定位** `R_X86_64_{64,PC32,PLT32,32,32S,PC64}` + **跨对象符号解析** | ✅（2026-09-17，阶段 2） |
| **归档 `.a`**：`ar` 解析 + **固定点成员选择** | ✅（2026-09-17，阶段 2） |
| 构建管线 `loment build --link FILE.o`（两个启动器） | ✅ |
| 端到端判据 | ✅ `loment_ffi_test` **29/29**（已进静态门禁）+ `loment_elf_test` 的自举镜像那格（8/8） |
| **自举链接器 `loment/tools/lomelf.lomt`** | ✅ **已镜像**（产品路径走它）—— 含重定位**与 `ar` 归档的固定点成员选择**（2026-10-15，`#405`），与参考**逐字节相同** |
| **动态库 / libc（阶段 3）** | ✅ **2026-10-17**（`#360`）：读 `ET_DYN` 的**导出表**（`.dynsym`/`.dynstr`/`.gnu.version`，含默认版本判定），产物发 `PT_INTERP` + `PT_DYNAMIC`、`DT_NEEDED`、`.dynstr`/`.dynsym`/`.hash`/`.gnu.hash`/`.rela.plt` 与 PLT/GOT；**参考与自举两侧逐字节相同**，链的是系统 `libz.so`（`DT_NEEDED=libz.so.1`，产物 20 KB） |
| PE 侧 FFI | ⛔ 未开始（`--link` 配 `--target pe` 明确拒绝） |

### 已经能被调到的语言（这台机器上真跑通的）

| 语言 | 走哪条腿 | 判据（`loment_ffi_test` 18/18，**没有一个 SKIP**） |
|---|---|---|
| **C** | `extern fn` + `--link`（clang 交叉编出 ELF 目标文件） | `test_c_end_to_end` 52 · `test_three_args_and_void_call` 123 · **多目标** `test_multi_object_cross_reference` 22 · **归档** `test_archive_selects_only_needed_members` 22 |
| **C++** | 同上，`extern "C"` 包一层 | `test_cpp_end_to_end` 42 |
| **Rust** | 同上，`#[no_mangle] pub extern "C"` | `test_rust_end_to_end` 42 |
| **Zig** | 同上，`export fn` + **`-OReleaseSmall`** | `test_zig_end_to_end` 42 |
| **Go** | **进程桥**：`go run`（编译器在主机上，靠 WSL interop） | `test_go_via_process_bridge` 5 |
| **Python** | 进程桥：起 `python3`，让它 `import json` | `test_python_via_process_bridge` 9 |
| **Java** | 进程桥：`javac` + `java`（JDK 免 sudo 下载） | `test_java_via_process_bridge` 7 |
| **JavaScript** | 进程桥：起 `node` | `test_javascript_via_process_bridge` 7 |
| **Perl** | 进程桥：`perl -MJSON::PP`（WSL 里本来就有） | `test_perl_via_process_bridge` 7 |
| **Lua** | 进程桥：自建解释器（`make linux`） | `test_lua_via_process_bridge` 5 |
| Swift / C# / Fortran … | C ABI 那一族，**同一个机制** | 见下面"机制覆盖 ≠ 现在就能链"那三条 |

**"7 个语言"这句话要拆开说**，不然是虚的：

- **机制上覆盖 7+**：第一条腿覆盖所有能导出 C 符号的语言（C、C++、Rust、Zig、Go、
  Swift、C#(NativeAOT)、Fortran、Ada…）——**同一个 `extern fn`，同一套寄存器约定**，加一个
  语言就是加一条构建配方 + 一条判据。第二条腿覆盖所有有解释器 / 有 CLI 的语言
  （Python、Java、JS、Perl、Lua、Go…）——**连构建配方都不用**，换个命令。
- **本机实测覆盖 10**（2026-09-17，`loment_ffi_test` 18/18 且**没有一个 SKIP**）：
  C / C++ / Rust / Zig（腿 1）+ Go / Python / Java / JavaScript / Perl / Lua（腿 2）。
- **"机制覆盖"与"现在就能链"仍是两件事**，但 2026-09-17 之后卡点换了：
  1. ~~没装工具链~~ → 已用**免 sudo** 补齐（`tools/loment_toolchain.sh`：tarball 或源码自建）。
     Swift / C# / Fortran 的路线仍是单个 `.o`，本机没有工具链，**未实测**。
  2. ~~卡在归档~~ → **归档做完了**（阶段 2）。但 Go 走的是**进程桥**而不是 `c-archive`：
     它的对象带自己的运行期（未定义符号 + `.init_array`），那是阶段 3 的事。
  3. **卡在运行期**（阶段 3）：C#(NativeAOT) / Swift 的对象引用各自运行期 ——
     那种对象带一堆未定义符号，**默认会被拒**（见下面那条门），不猜地址。
- **那条门：对象带未定义符号 = 这份产物有端口。** 这条边界 2026-10-12 起不是"一眼拒"，
  而是一条**带渠道的门**（`docs/219` §8c、`docs/222` §4）：**默认拒**，开发通道显式
  `--allow-ports` 打开；打开之后，"可闭合"的那几个名字（`memcpy`/`memset`/`memcmp`/
  `malloc`/`free` 与 `__stack_chk_fail`）由链接器**自己发**出来（`docs/219` §6 的收编面），
  其余照旧在链接期报"引用了未定义的符号"。**发行版（0.1.4.5）里这条路是关着的** ——
  用户 2026-10-12 定：「锁掉 LibC … 因为这尚未完工」。
  **自闭合**的对象（没有未定义符号）不受这条影响，走的还是老路。
  量"能不能用一个语言的库"仍然量的是**那份对象**本身：Zig 就是例子 —— 默认优化档把它的
  运行期编进来（9.7 MB，还带一整张 `.rela.data`），`-OReleaseSmall` 才剩 880 字节。

### 三处"响的失败"（宁可不支持，也不静默错编）

1. **自举链接器对 `--link` 硬拒**（**已补**，见 §5b 那一格）。产品路径走的是自举链，所以在
   补上之前**装好的工具链根本链不了 `extern`** —— 仓库里能跑、包里的不能；那时它明说
   "尚未在自举链接器实现"而不是静静忽略 `argv[3]`（忽略了就会在 `finalize` 报一句
   "未定义的标签: c_add"，用户看到的是"符号找不到"，方向完全错了）。补它时踩到三件
   **只有真做才看得见**的事：
   - **外部符号名必须拷进输入缓冲的尾部**。标签表存的是偏移，而 `lbl_find` 拿**输入缓冲**
     当基 —— 名字留在 `.o` 的缓冲里，下一个 `.o` 一读进来就被盖掉了。名字池的基**必须**
     与标签表一致，没有第二条路。
   - **接进来的那段必须显式把对齐缝隙清零**。参考实现是 `while len(buf) % 16:
     buf.append(0)`，而那些补齐字节**会进最终的 ELF**（`build_elf` 从 `[0, tlen)` 整段拷）。
     自举侧若只跳到对齐位置而**不写**，拷出去的是 arena 里的残留字节：首次分配恰好是内核
     给的零页，于是**能对上** —— 但那是巧合不是性质，arena 一旦被复用过就会悄悄与参考不同。
   - **`--link` 的参数必须在读输入之前收完**：`argv_n` 每次都把 `/proc/self/cmdline` 重读
     进 argv 缓冲，而它存放路径字符串的落点（argv 缓冲 + 8192）**正是输入缓冲的起点** ——
     读过输入之后再调 `argv_n`，会把输入的头 19 个字节踩掉。
2. **自举编译器曾经把 `extern fn` 静默编错**（已修）。第一版自举 codegen 把它当普通函数，
   发出一条没有函数体的 `define`，游标从签名滑进下一个函数的体 —— 实测
   `extern fn c_add(a,b); fn main() { return c_add(3,4); }` 得到
   `define i32 @c_add(...) {<main 的体>}` 而 `main` 整个消失，**退出码 0**。
   修法是把它当第三类（与 trait 方法同类：只声明不发射），并判**前一个 token**而不是挂粘性
   标志（`tok_is` 比的是 token 文本，源码里的字符串字面量 `"extern"` 也会被匹配上）。

## 6. 判据

```bash
python tools/loment_ffi_test.py     # 12/12: C / C++ / Rust 端到端 + Python / JS 桥 + 五条边界
python tools/lomentc_test.py        # 112/112: extern 正负例 (签名形态 / 重名 / 无体)
python tools/loment_p8_test.py      # 16/16: extern 的两个实现**逐字节一致** + 语料 54/54
python tools/loment_elf_test.py     # 8/8: 含"自举镜像 + --link 与参考逐字节相同, 且跑出 52"
python tools/loment_pe_test.py      # 9/9: PE 侧不受影响
```

`loment_ffi_test` 比的是**运行结果**而不是 IR —— 这条路上最容易错的几处都不改 IR:
传参进哪个寄存器、调用结果有没有落槽、进程桥读回的是不是对方真的算出来的东西。

`loment_elf_test` 里那条 `test_lomelf_selfhost_links_foreign_object` 比的是**两份产物的
字节** —— 它钉的是"**装好的**工具链也能链 `extern`"。**两条判据缺一不可**：参考侧对而
自举侧错的话，用户装到的那个包就是坏的，而门面判据（走参考实现）照样绿。

**证伪要求**（先把判据弄坏，确认它会红）：把 C ABI 传参改回栈 → 门面判据必须红；
把 `parse_ll` 跳过 `declare` 改回去 → 必须红；把外部符号名池的基从输入缓冲换成对象缓冲
（`copy_name(tab, t, …)` → `copy_name(tab, mem, …)`）→ 镜像那条必须红。**第三条真做过**：
镜像退出 1、报 `未定义的标签: c_add` —— 名字池的基**必须**与标签表一致（两者用的是同一
份 `span_eq`，基不同就谁也认不出谁）。判据里那条**双对象**场景是为了让"名字要从被复用的
缓冲里拷出去"这条路真的被走到：只链一个 `.o` 时那块缓冲始终完整。
