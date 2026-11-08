# 313 · wasm32 后端：让 Loment 源编译器在浏览器里跑

> 状态：**设计稿；目标 0.1.5.2**
> 上游：`docs/158`（两套实现的逐字节约束）· `docs/243`（LVM）· `docs/212` / `docs/226`
> （原生产物链与性能账）· `docs/231`（`choose vm`）· `docs/295`（入口）
> 一句话：**给 `lomelf` 加一个 wasm32 目标，把自举编译器与 LVM 编成 WebAssembly，
> 让官网 Playground 的编译与执行都由 Loment 自己的实现完成。**

---

## 0. 要解决什么

官网的 Playground 现在跑的是**参考编译器**：Pyodide 提供 CPython，`tools/lomentc.py`
把源码编成 LLVM IR，`tools/lvm.py` 解释 IR。这条路能跑，因为它全是 Python。

站点迟早要由**源编译器**驱动。源编译器是 `loment/selfhost/*.lomt`（约 2.9 万行），
由 `genesis` 自举，产物是 x86-64 原生 ELF/PE。浏览器不执行 x86-64 机器码，
`lomelf` 也没有 wasm 目标，所以今天做不到。

本文给出 wasm32 目标的形状、边界与落地顺序，目标版本 **0.1.5.2**。

### 0.1 最终形态

```
浏览器
  ├─ compiler.wasm   ← 自举编译器（Loment 写，lomelf 编成 wasm）
  ├─ lvm.wasm        ← 源 LVM（Loment 写，lomelf 编成 wasm）
  └─ playground.js    写源码 → compiler.wasm → IR → lvm.wasm → stdout/exit
```

参考编译器与 Python LVM 留在仓库里，作为门禁的对照实现；Playground 在两者对齐之前
保留 Pyodide 作为回退。

---

## 1. 为什么今天做不到

| 层 | 今天的形状 | 浏览器的要求 |
|---|---|---|
| 代码生成 | `lomelf.py` 只发 x86-64 ELF 与 PE | WebAssembly 二进制模块 |
| 系统调用 | 编译器直接发 Linux syscall（openat/read/write/mmap…） | WASI 或自定义宿主函数 |
| 进程入口 | `_start` + `exit` syscall（60） | WASI `_start` / `proc_exit` |
| 内存 | 固定的 64 KiB / 1 MiB 堆 + `brk` / `mmap` | 线性内存 + `memory.grow` |
| 线程 | `thread_spawn` 用 Linux `clone` | 0.1.5.2 不做 |

Playground 现在的 Pyodide 方案绕开了全部四条：它跑的是 Python 实现。

---

## 2. 范围

### 2.1 做

1. `lomelf` 的 **wasm32 发射器**，吃同一份 IR 文本，产出一个可验证的 wasm 模块。
2. **WASI 子集 shim**：编译器与 LVM 实际用到的系统调用。
3. 把 `loment/selfhost/` 的编译器驱动与 `loment/tools/lvm.lomt` 编成 wasm，行为与原生侧一致。
4. Playground 的 **wasm 引擎**：源码 → compiler.wasm → IR → lvm.wasm → 输出。
5. 一条**差分判据**：同一份源，原生 ELF 与 wasm 的 stdout/退出码逐字节相同。

### 2.2 不做

- 不做 wasm64、JIT、线程、网络与动态链接。
- 不做完整 WASI；只做清单里的调用。
- 不删参考编译器；它是门禁的对照面。
- 不在 0.1.5.2 要求 wasm 侧的**自举**（用 wasm 编译器编译它自己）。自举链仍在原生侧。

---

## 3. IR → wasm 的映射

`lomelf` 已经能解析这份 IR 子集。wasm 发射器复用同一套解析结果（`Func` / `Block` /
`Instr` / `Global`），只换发射端。IR 到 wasm 的对应关系：

| IR | wasm | 说明 |
|---|---|---|
| `alloca` | 线性内存栈槽 | 复用 `plan_frame` 的槽位布局 |
| `load` / `store` | `i32/i64.load` / `store` | 地址由 i64 截到 i32 |
| `getelementptr` | i64 加法 | 与 x86 后端同一套偏移计算 |
| `add/sub/mul/and/or/xor` | 同名 i32/i64 指令 | 宽度来自 IR 类型 |
| `sdiv/udiv/srem/urem` | `i32/i64.div_s/div_u/rem_*` | 除零两边都 trap |
| `shl/lshr/ashr` | 同名 | 移位数由硬件掩码，语义一致 |
| `icmp` | `i32/i64.eq/ne/lt_s/lt_u/…` | 映射表复用 `ICMP_CC` |
| `zext/sext/trunc` | `i64.extend_i32_u/s`、`i32.wrap_i64` | |
| `ptrtoint/inttoptr` | i64 ↔ i32 截断/扩展 | 见 §4 |
| `br` / 条件 `br` | `br` / `br_if` | 基本块 → `block`/`loop` 结构 |
| `switch` | `br_table` | 默认臂是表尾 |
| `phi` | 本地变量 + 前驱复制 | 先在每条入边上写，再在块首读 |
| `call` | `call` | 直接调用；无间接调用 |
| `call … asm "syscall"` | 宿主导入 | 见 §5 |
| `atomicrmw` | `i32/i64.atomic.rmw` | 单线程语义即可 |
| `extractvalue/insertvalue` | 槽位读写 | 聚合按 `field_offset` 展开 |

发射顺序：类型段 → 导入段 → 函数段 → 表/内存 → 全局 → 导出 → 起始 → 代码 → 数据。
每个函数先算 `plan_frame` 的槽数，再用一个 `__stack_pointer` 全局做线性内存栈。

### 3.1 栈与调用约定

wasm 没有原生栈。做法与 x86 后端保持同一套帧布局：

```
prologue:  __sp = __sp - frame_size
           (槽位 = __sp + offset)
epilogue:  __sp = __sp + frame_size
```

参数与返回值走 wasm 的 locals，SSA 值也落在 locals；跨调用的值在调用前 spill 到帧槽。
这样 `alloca`、数组与结构体的地址仍然是线性内存里的确定偏移，和原生侧对得上。

### 3.2 入口与退出

- `_start` 导出为 WASI 的 `_start`，返回即 `proc_exit(0)`。
- 显式 `syscall4(60, code, …)` 映射成 `proc_exit(code)`。
- `main` 的垫片仍由链接器生成，与原生侧同一处代码。

---

## 4. 指针宽度

这是整个移植里唯一会静默出错的地方。IR 里的指针是 **i64**；wasm32 的线性内存地址是
**i32**。

规则：

1. 指针值在局部变量与运算里保持 i64；`load`/`store`/`call` 的地址参数发射前 `i32.wrap_i64`。
2. `ptrtoint` 得到 i64；`inttoptr` 接受 i64，使用时再截断。
3. 线性内存上限 4 GiB；编译器的堆远小于它。
4. **调试档加一条显式检查**：地址 > `0xFFFF_FFFF` 时 `unreachable`，不要让它静默回绕。
   发布档是否保留这条检查，由体积预算决定（§7）。

不选 wasm64：浏览器支持面不够，且与现有 i64 值模型的收益不成比例。

---

## 5. WASI 面

对 `loment/selfhost/` 与 `loment/tools/lvm.lomt` 做了一次静态盘点，实际用到的系统调用是：

| 号 | 调用 | 用途 | wasm 侧 |
|---|---|---|---|
| 0 | `read` | 读源文件 / 读 IR | `fd_read` |
| 1 | `write` | 诊断 / 产物 / stdout | `fd_write` |
| 2 | `open` | 打开文件 | `path_open`（固定 flags） |
| 3 | `close` | 关闭 | `fd_close` |
| 8 | `lseek` | 定位 | `fd_seek` |
| 9 | `mmap` | 大块内存 | `memory.grow` + bump |
| 12 | `brk` | 堆边界 | `memory.grow` |
| 39 | `getpid` | 日志里的进程号 | 固定返回 1 |
| 60 | `exit` | 退出 | `proc_exit` |
| 63 | `uname` | 目标平台串 | 固定 `wasm32` |
| 201 | `time` | 时间戳 | `clock_time_get`（秒） |
| 217 | `getdents64` | 目录枚举 | `fd_readdir` + 结构体转写 |
| 228 | `clock_gettime` | `lgc` 预算 | `clock_time_get`（纳秒） |
| 257 | `openat` | 读源文件主路径 | `path_open`（相对 preopen） |
| 262 | `newfstatat` | 文件大小 / 类型 | `path_filestat_get` |

`getdents64` 是唯一需要**转写结构**的一条：WASI 的 `fd_readdir` 返回 `dirent` 缓冲，
shim 要把它写成 Linux 的 `linux_dirent64` 布局，否则编译器的目录遍历读不懂。

### 5.1 虚拟文件系统

Playground 只给两个 preopen：

```
/work     读写；源码 main.lomt、产物 main.ll
/lib      只读；标准库与核心库
```

编译器读 `/work/main.lomt`，写 `/work/main.ll`；LVM 读 `/work/main.ll` 并执行。
标准库沿用仓库布局：`/lib/loment/lib` 与 `/lib/lompi/store`。

### 5.2 线程与网络

`thread_spawn` 与 socket 系列在 0.1.5.2 返回 `-ENOSYS`。编译器与 LVM 都不用它们；
用户程序如果需要，Playground 会显示一条明确的诊断。

---

## 6. 编译源编译器与 LVM

1. 用**原生参考链路**把 `loment/selfhost/driver.lomt`（及其依赖）编成 IR。
2. 用新的 wasm32 发射器把这份 IR 编成 `compiler.wasm`。
3. 同样编 `loment/tools/lvm.lomt` 得到 `lvm.wasm`。
4. 两个模块都导出 `_start`，由 JS 依次调用；编译器的 stdout 不直接给用户，
   IR 走 `/work/main.ll`。

这一步不需要 wasm 侧自举：编译器仍是原生侧产出的 IR。等 wasm 目标稳定后，
再评估"用 compiler.wasm 编它自己"。

---

## 7. 判据

| 判据 | 喂什么 | 期望 |
|---|---|---|
| 模块可验证 | 每个 wasm 产物 | 段结构、LEB、类型全部合法；`wasm-tools validate` 或自写的结构校验器 |
| 差分输出 | 书里的 `rust,run` 块 + `examples/` | 原生 ELF 与 wasm 的 stdout、退出码逐字节相同 |
| 差分陷阱 | 除零、数组越界、`unreachable` | 两边都以非零退出，且 stderr 类别一致 |
| 编译器行为 | 同一份源分别经 compiler.wasm 与原生 driver | 产出的 IR 文本逐字节相同 |
| LVM 行为 | 同一份 IR 分别经 lvm.wasm 与原生 LVM | stdout/退出码相同 |
| 体积预算 | `compiler.wasm` / `lvm.wasm` | gzip 后各自 ≤ 2 MB；超出要写进本文并解释 |
| 启动预算 | 浏览器里从点击到第一次输出 | 引擎加载后 ≤ 1 s（桌面、冷缓存除外） |
| Playground 冒烟 | 四个内置示例 + 一个 `use num` 程序 | 输出与 `expect` 相同 |

差分判据要先在**未实现**的树上红：wasm 目标不存在时，发射器应当直接报"没有这个 target"，
这条先红一次，再落地。

CI 里跑 wasm 需要执行器：优先用 `wasmtime`/`wasm3`；没有就在判据里**具名跳过**，
并让跳过数进入 `gate-skips.txt` 的账。浏览器的冒烟测试放在站点仓，作为发布前手工步骤。

---

## 8. 落地阶梯（0.1.5.2）

| 步 | 内容 | 完成判据 |
|---|---|---|
| W1 | wasm32 发射器骨架：类型段、函数、`i64` 算术、`return` | `add(2,3)` 在 wasm 里返回 5 |
| W2 | 控制流：`br`、`br_if`、`br_table`、`phi`、`alloca`、`load/store` | 书里第 9–15 章的块全部通过 |
| W3 | 聚合与字符串：GEP、`extractvalue`、数据段、`alloc/free` | 书里 by-example 全部通过 |
| W4 | WASI shim + 虚拟 FS；编出 `compiler.wasm` 与 `lvm.wasm` | 编译器与 LVM 的差分判据绿 |
| W5 | Playground 接 wasm 引擎（`?engine=wasm`），Pyodide 保留回退 | 站点冒烟通过；默认仍可切回 |

W1–W3 只动 `tools/lomelf.py` 与判据；W4–W5 才动 Playground。每一步都单独可合。

---

## 9. 风险与开放问题

1. **指针截断的静默性**：i64 地址截到 i32 后回绕不会报错。调试档的 `unreachable`
   检查是唯一防线；发布档要不要保留，取决于体积与性能测量。
2. **`phi` 的实现方式**：本地变量 + 前驱复制是最稳的，但每条入边都要插指令；
   wasm 的 block 参数（multi-value）在主流浏览器可用，是否用它需要一轮测量。
3. **`plan_frame` 的复用**：x86 帧布局与 wasm 栈槽共享同一份计算，能减少两套后端的漂移；
   但 x86 有寄存器分配，wasm 没有，槽位可能比必要多。先复用，再按性能账优化。
4. **双实现约束**：`lomelf` 在自举侧也有镜像（`loment/tools/lomelf.lomt`）。
   0.1.5.2 的 wasm 发射器先落在参考实现；镜像何时跟上，要在 W5 之后单独立项，
   否则"同一份源两个链接器"的既有判据会被撕开。
5. **编译器的固定容量**：自举侧有源文件槽、标签表等固定上限（`docs/` 里有记录）。
   wasm 的线性内存比原生更宽松，但容量检查仍按自举侧的口径走。
6. **诊断语言**：源编译器的诊断是中文；Playground 直接显示，不翻译。
7. **把 Pyodide 删掉的条件**：wasm 引擎在四个示例、书内全部块与 `use` 标准库的
   程序上都通过后，才允许删回退路径。

---

## 10. 与 Playground 的分界

Playground 是这条线的**验收场景**，不是它的全部。wasm32 目标本身对以下场景同样有用：

- 在浏览器里跑任意 Loment 程序（不依赖服务端）；
- 把编译器嵌进编辑器插件；
- 在没有原生工具链的机器上做教学与演示。

站点侧只做两件事：加载两个 wasm 模块、把源码与输出接上。编译与执行的语义由语言仓负责。
