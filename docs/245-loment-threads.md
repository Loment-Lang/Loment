# 245 · 真线程 `thread_spawn`，与「函数指针」那条线怎么分家

> 状态: **阶段 1 已落地**（2026-10-14）· 判据: `tools/loment_thread_test.py` +
> `loment_elf_test::test_selfhost_mirror_matches_reference_on_thread_spawn`
> 本文按 `docs/176` §7.3 的规矩先立规范与落点 —— **没有落点的那条，就是这一改动的真实代价**。

## 0. 起因

`os` 库（`docs/242`）落地后要补**真线程**。一动手就撞到两件**实测出来的硬事实**（不是推断），
它们一起决定了这件事该怎么做。

## 1. 硬事实一：`clone(CLONE_THREAD)` 能起真线程，但语言层判父子是**结构性**竞态

`clone` 起线程时只换 `rsp`（`child_stack`），**`rbp` 是继承的**。而本仓的原生后端
**所有局部与临时值都是 `rbp` 相对寻址** —— `tools/lomelf.py` 里一律
`lea(reg, RBP, off)` / `load_sized(reg, RBP, off, sz)`。于是子线程的**每一次读写都落在
父线程的帧里**：

* `clone` 返回值的落点就是那个共享槽；父写 tid、子写 0，**谁后写谁赢**；
* 于是 `if r == 0` 这句判断**本身就在读共享内存** —— 它不是"偶尔错"，是结构上不安全。

**实测**（2026-10-13，ELF 在 WSL 里跑）：第一次 `spawn` 的子线程**始终没走 `r == 0` 那条路**，
却出现了"第二个子线程" —— 那是子线程被当成父线程接着往下跑了。

**所以"起线程"这件事只有编译器能解**：子线程一落地就把 `rbp`/`rsp` 一起换成自己的栈，
然后**直接** `call @入口`。

## 2. 硬事实二：光有"函数地址"不够

`clone` 的**系统调用**签名是 `(flags, child_stack, ptid, ctid, newtls)` —— **没有入口参数**；
子线程继承 RIP，从系统调用**后面那条指令**继续跑。所以拿到一个函数地址也**没有地方放它**：

* 要么语言能"通过地址调用"（间接调用，今天没有）；
* 要么像 glibc 那样把入口压在栈顶、让子线程一进来就 `ret` 跳过去 —— 而那要求**挨着
  `syscall` 的那条指令就是 `ret`**，那是编译器发出来的代码，不是 Loment 能写的。

**用户 2026-10-13 的裁决：两条线都做，用 `choose` 开关分家。**

## 3. 两条线

### 线 A —— `thread_spawn`（默认可用，**不动任何既有保证**）· **已落地**

```rust
let tid: i64 = thread_spawn("worker", stack_top, arg, done);
```

* **入口是字符串字面量**，不是表达式。理由不是审美：产物里那是一条**写死的调用边**
  （`call @worker`），名字必须在编译期定下来；写成变量只会在链接期变成"未定义的标签"。
  这也让三条检查**在编译期做得了**（§4）。
* **结尾是直接调用**，所以 `lomelf` 那条 `Unsupported("v0 不支持间接调用")` 一个字没动，
  **静态调用图仍然是静态的** —— `docs/208` 那两条承重门禁（`closed` / GC 可达性）的前提
  一个字没改。
* **只在 ELF 目标可用**。Windows 没有 `clone`，PE 目标**响亮地拒绝并点名**，不静默错编一个
  "假线程"（`test_pe_refuses_thread_spawn`）。

**第四个实参是完成通知地址**（0 = 不要通知）。它不是一个可有可无的便利 —— 见下面那段
尾声：**子线程的尾声是编译器发的**，入口一 `return` 就 `exit`，所以**工作线程在返回之后
没有机会跑任何代码**。没有这一段，`join` 就只能靠"工作线程自己记得在最后喊一声"，
忘了就是挂死 —— 一个系统语言不该有的地雷。

**语义（定死在这里）**：

| | |
|---|---|
| 入口签名 | 恰好**一个**形参（整数类型） |
| 子线程结束 | 它自己结束自己（`exit` = 60，**不是** `exit_group` = 231 —— 后者会把整组杀掉） |
| 退出码 | 入口的返回值 |
| 返回值 | 新线程的 tid；失败时是负数（内核的负 errno） |
| 栈 | **调用方给**（`stack_top` 是栈顶，往下长） |
| 完成通知 | `done != 0` 时，入口返回后对 `*(u32*)done` **原子加一**并 `futex_wake(done, 1)` |

**发出来的东西**（两侧**同一串字面机器码**）：

```asm
                            ; 进门前: 栈顶 → rsi, 子线程实参 → r9, 通知地址 → r12
  BF 00 0F 05 00            mov  edi, 331520   ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM
  31 D2                     xor  edx, edx      ; ptid  = NULL
  45 31 D2                  xor  r10d, r10d    ; ctid  = NULL
  45 31 C0                  xor  r8d, r8d      ; newtls = 0
  B8 38 00 00 00            mov  eax, 56       ; clone
  0F 05                     syscall
  48 85 C0                  test rax, rax
  75 35                     jne  .parent       ; 父线程: rax = tid, 跳过下面 53 字节
  48 89 E5                  mov  rbp, rsp      ; **子线程**: 换掉继承来的 rbp (硬事实一)
  41 51                     push r9            ; 实参 → 被调方的第 0 个形参槽
  E8 rel32                  call @入口          ; **直接调用**
  41 89 C5                  mov  r13d, eax     ; 存住入口的返回值
  4D 85 E4                  test r12, r12      ; 有通知地址吗
  74 19                     jz   .no_done
  F0 41 FF 04 24            lock incl [r12]    ; 原子加一 (也是释放屏障)
  44 89 E7                  mov  edi, r12d
  B8 CA 00 00 00            mov  eax, 202      ; futex
  BE 81 00 00 00            mov  esi, 129      ; FUTEX_WAKE|FUTEX_PRIVATE_FLAG
  BA 01 00 00 00            mov  edx, 1        ; 唤醒一个等待者
  0F 05                     syscall
  44 89 EF                  .no_done: mov edi, r13d
  B8 3C 00 00 00            mov  eax, 60       ; exit
  0F 05                     syscall
```

三条容易看漏、但**少了就错**的：

1. **值走寄存器而不是栈**。`syscall` 只踩 `rcx`/`r11`，不碰 `r9`/`r12`；而子线程在 `clone`
   那一刻**继承整个寄存器组**（只有 `rsp` 被换成 `child_stack`、`rax` 置 0）。栈顶那个
   字节属于**父线程的帧**，子线程不能读。
2. **两个位移都是手算的**：`jne` 跳过后面 **53** 字节（3+2+5+3+3+2+5+3+5+5+5+2+3+5+2），
   `jz` 跳过 **25**（5+3+5+5+5+2）。少一个字节就落到子线程那条路上 —— 而这是两侧各写一遍
   的**字面**序列，所以 `test_selfhost_mirror_matches_reference_on_thread_spawn` 逐字节
   钉着它。
3. **`lock incl` 既是计数也是屏障**：`futex_wake` 必须在计数**之后**，否则等待者会在值
   还没变时被叫醒、然后回去接着睡。

**`clone` 的 flags 一个字都不能错**：`0x50F00` = 331520。少一个 `CLONE_*`（或多一个
`CLONE_PIDFD`）内核直接 `EINVAL` —— 而**产物照样生成成功**。第一版写的是
`0x51300`（332544，少了 `CLONE_SIGHAND`、多了 `CLONE_PIDFD`），是**跑起来**才发现的。

### 线 B —— 函数指针 + 间接调用（挂在 `choose` 开关下）· **未开始**

`fn(A, B) -> C` 类型 + `fn_addr(name) -> u64` + 间接调用 `p(a, b)`。两个必须写在明处的代价：

1. **它是当年刻意排除的。** `docs/232:179-190`（§3.3）：本语言没有函数指针，所以 `qsort`
   的第四个参数是 `kind` 而不是比较函数 —— 它把"按什么比"从**运行期的一个地址**变成
   **产物里的一个常量**，于是"这个排序按什么比"是**不读源码可判的**。
2. **它会掀掉承重门禁的前提。** `docs/208:188-220`：**静态调用图**是 `closed` 与 GC 可达性
   两条门禁的前提；间接分派/间接跳转一进来，那两条的前提要重写。

**开关的形状**：与 `choose vm` 同一条**渠道闸**（`docs/231` §2 —— 读同一个渠道常量，打包期
只换那一行）。**发行版里是编译错误并点名**，于是默认产物保持"静态调用图"那条保证，而
"这份产物有没有间接分派"落在 **Potato 产物**里 —— 仍然是**不读源码可判**的。

## 4. 落点表（`docs/176` §7.3：两个实现各找一个落点；阶段 1 已填）

| 规则 | 参考侧 `tools/lomentc.py` / `tools/lomelf.py` | 自举侧 `loment/selfhost/` 与 `loment/tools/` |
|---|---|---|
| 内建名 + 签名 | `BUILTINS`：`("str","u64","u64") -> i64` | `checker.lomt is_builtin :520` 的名字串；`chk_builtin_arity` / `chk_builtin_arg` / `chk_builtin_ret` |
| 入口三条检查 | `_walk_expr` 的内建分支（字面量 / 名字合法 / 本单元里恰好一个形参 / 已存在） | `chk_tex_atom_call` 内建分支 + **新助手 `chk_lookup_lit_name`**（按字符串字面量的**正文**查表 —— 字面量 token 的 `tok_start` 指着开引号、`tok_len` 含两侧引号） |
| 发射 | `_Ir.expr`：`call i64 asm sideeffect "thread_spawn @入口", …` | `codegen.lomt expr_atom` 的 `thread_spawn` 分支 |
| 链接器 | `Emitter._call` 分派 → `_call_spawn`（`_SPAWN_HEAD` / `_SPAWN_TAIL`）；`CALL_ASM_RE` 改成**捕获 asm 名字**并分派 | `do_call` 分派 → `do_call_spawn`（同一串字面字节） |

**入口名走"字符串字面量"而不是"裸名字"**，是刻意选的：裸名字在语言里**不是表达式**，
要支持它得动解析器，而解析器一动，**所有读 L1 源的入口**（两个 LSP、`lomfmt` 两份、
`lomdoc` 两份、编辑器语法……见 `docs/182` §1.9）都得跟着过一遍。字面量是**已经存在的
表达式形状**，对它们全部透明。代价是入口看起来像字符串 —— 用编译期的三条检查补回来。

## 5. 判据（`tools/loment_thread_test.py`，7 条）

| 判据 | 钉什么 |
|---|---|
| `test_threads_accumulate_on_shared_memory` | N 个线程在**共享**计数器上各原子加 `ROUNDS` 次，和 == `N × ROUNDS`。一条同时钉住 flags 对不对、`CLONE_VM` 在不在、子线程是不是真的从入口跑 |
| `test_each_thread_is_a_distinct_thread` | 每个工作线程写下自己的 `gettid()` —— 必须**互不相同**且**都不等于主线程**。这是"真线程"的**证伪面**：换成顺序调用，上面的和**照样对**，tid 会全相同 |
| `test_completion_wakes_the_joiner` | 主线程**睡在 futex 上**（工作线程先 `nanosleep` 5ms，保证主线程一定已经睡下去），`wakes` 数**真被叫醒**的次数（返回 0；值已经变了是 `EAGAIN`, 不算）。探针里的 `worker` **一句通知都没写** |
| `test_entry_must_be_a_string_literal` | 入口写成变量 → 编译期报 |
| `test_unknown_entry_is_a_compile_error` | 入口不在本单元 → 编译期**点名**，不留到链接期 |
| `test_entry_must_take_exactly_one_parameter` | 入口两个形参 → 编译期报。这条不是洁癖：子线程只压一个槽，第二个形参读到栈上的垃圾，是**静默错编** |
| `test_pe_refuses_thread_spawn` | PE 目标拒绝并点名 |

外加 `loment_elf_test::test_selfhost_mirror_matches_reference_on_thread_spawn`：同一份 IR
过**参考链接器**与**自举镜像**，产出的 ELF **逐字节相同**（那条字面序列两侧各写一遍，
漂一个字节别的判据一条都看不见）。

## 6. 这一笔顺带查出来的缺陷（**已修**）

**自举镜像链接器编不了任何用 `atomic_add` 的程序。** `loment/tools/lomelf.lomt` 的
`do_atomic` 只跳过 `atomicrmw `（10 字节），而 IR 里那后面还有**一个操作名**：
`atomicrmw add ptr %v, i32 1 seq_cst`。于是它把 `add` 当成了类型，`skip_sp` 落到 `ptr` 上，
拿值为 `ptr` 去查指针表，报出来的是 `lomelf: 指针值查不到: ptr` —— 离现场很远。
参考实现的正则 `atomicrmw\s+add\s+` 里**一直有那个 `add`**，所以这是镜像单方面的漏抄。

它潜伏至今的原因是**没有任何语料用过 `atomic_add`**：`loment_elf_test` 的镜像语料
（`CORPUS`）里一份都没有，所以那条"镜像与参考逐字节相同"的判据从没走到这条路。
线程探针的原子累加是**第一次**把它走通（2026-10-14）。

## 7. 刻意**没**做的（记账，不是遗漏）

**`thread_spawn` 没有进 `potato.BOUNDARY_BUILTINS`。** 它确实是一次"越过语言保证的每一步"
（发的是 `clone`），但那份清单的**分类是两族**：`syscall*` 一族、裸指针变换一族
（`ptr_add`/`ptr_sub`/`str_ptr`），加一族自声明的 `extern`。`thread_spawn` 是**第三类**，
硬塞进任一族都会让那个计数说假话，而加一族是 **Potato 产物 schema 的改动**
（`potato.lomt` / `lompotato.lomt chk_*` / `lomcli` 三处 + 两条计数判据），该单独一笔做。
在那之前，**用 `thread_spawn` 的程序，Potato 的边界计数是少报的**。

`BOUNDARY_BUILTINS` ⊆ `lomentc.BUILTINS` 那条判据不受影响（它只要求子集关系）。

## 8. 阶段 2 / 3

### 阶段 2 · `os/thread` 库 · **已落地**

`lompi/store/os/0.1.0/thread.lomt`：`thread_stack`（mmap 拿栈，给的是**栈顶**）、
`thread_self`（gettid）、`thread_yield`、`thread_wait` / `thread_wake`（futex 原样）、
`thread_lock` / `thread_unlock`、`thread_cv_wait` / `signal` / `broadcast`、
`thread_join` / `thread_join_all`。

**它不包 `thread_spawn`，而且包不了**：第一个实参是**编译期的名字**，包装函数里写的那个
名字指的是**包装函数自己单元里**的函数，不是调用方的。所以 `thread_spawn("my_worker", …)`
必须由用户在自己的单元里直接写 —— 这正是"不动静态调用图"买来的东西。这一层因此只提供
**周围**那一圈，而它们恰好是"多线程系统"缺的那部分。

**互斥量为什么是取号锁（ticket lock）**：语言只有 `atomic_add`（返回**旧值**），**没有**
CAS、没有 xchg。而"加一试试、失败再减回去"那种写法是**错的** —— A 拿到锁（值 1）之后 B
加一减一，值回到 0，锁看起来空了而 A 还拿着；那个坑很安静，表现为"偶发两个线程同时在临界区
里"。取号锁只用"取号"一个原子操作，天然公平，记录就是两个 u32。

**放锁为什么是"全叫醒"而不是"叫醒一个"** —— 这条是**实测**出来的：`futex_wake` 叫醒的是
队列里的**某一个**等待者，不是"票号轮到的那个人"。被误叫的那个重读一眼、发现没到自己、
**又睡了**，而它把本该属于下一个号的那次唤醒**吃掉了**。于是每多一次误叫就少一次该到的
唤醒，最后某个号永远等不到人叫它。**4 线程 × 10 次那种规模根本撞不出来**（第一版档 3 全绿），
一上到 4 × 5 万次就是 300s 死锁。判定：全叫醒有"惊群"（每次放锁最多叫醒 n-1 个，它们重读
一眼又睡回去），但**丢唤醒是错，惊群只是慢**。

**判据**（`loment_os_test.py::test_os_thread_mutex_serializes_writers`）：4 个线程各
50000 次临界区，临界区里是**非原子**的 `v = load; store(v+1)` —— 对照物是"没有锁的结果"，
所以锁一坏，丢更新是**必然**而不是"碰巧没撞上"。同时钉住 `thread_stack`（4 个栈地址互不
相同）与完成通知（`thread_join_all` 等到 4，而工作线程里一句"喊一声"都没有）。

### 阶段 3 · 函数指针 + 间接调用（线 B，开关下）· **未开始**
* **阶段 3 · 函数指针 + 间接调用（线 B，开关下）**：按 `docs/158` §5 四步；两个链接器都拒绝
  间接调用（`tools/lomelf.py:1083` / `loment/tools/lomelf.lomt`），要四份实现一起改；
  外加 `docs/208` 那两条门禁前提改成"带开关的模式"。消费方清单见 `docs/182` §1.9
  （两个 LSP 各带自己的类型表、`lomfmt` 两份、`lomdoc` 两份、`lomcli` 五张表、
  `trans_core.py:519` 七门翻译器、`potato` 的词法计数器、编辑器两份、`lompotato chk_types`）。

## 9. 约束（这一笔守的）

1. `docs/158` §5：两个实现在**同一次提交**；判据 `loment_rule_parity` 与
   `loment_tools_test::test_m81_builtin_tables_match` 两边都过。
2. `docs/176` §4 A：**语言面改动两条提交**（手写 / `--emit` 产物）。种子
   `loment/build/selfhost_driver.ll` 重算，不手工合并。
3. 门禁口径是**不新增红**（`.github/gate-baseline.txt`）。
4. **不碰 `lom/*.lom`**（L0 跨线契约）；两条线都在 L1。

---

## 10. 并发模型（2026-10-19，`FujoJTOP/loment#778`）

`docs/205` J6 说"内存模型今天这条是空的"。这一节把它写出来 —— **只写今天真实兑现的**，
不写计划。范围是**运行期**（分配器、收集器、根表）与线程之间那一条边界；用户的
数据竞争**不在**本节的保证里。

### 10.1 什么保证、什么不保证

| 问题 | 今天的答案 |
|---|---|
| 什么是原子的？ | 只有 `atomic_add`（`atomicrmw add … seq_cst`，`lomentc.py:7893` 那一处发射）。它同时是**唯一的**同步原语：没有 CAS、没有 `xchg`、没有 fence 内建 |
| 线程之间有什么顺序？ | `thread_spawn` 返回之前的一切 → 子线程入口（`clone` 的继承）；子线程返回后产物尾声里的 `lock incl [done]` → joiner 在 futex 上看到 `done`（那一对是释放/获取）。除此之外**没有** happens-before |
| 非原子的同址读写？ | **数据竞争**，行为未定义（用户自己用 `atomic_add` + futex 建协议，`os/thread` 就是这么写的） |
| 堆分配能从多个线程调吗？ | **能**。`alloc` / `free` 在运行期锁里（10.2），不会把同一块发给两个人 |
| 收集器会与线程交错吗？ | **不会**。起过线程之后自动收集**整个不跑**（10.3）—— 这是"最小诚实"的那一半：与其假装有一个 stop-the-world 协议，不如把收集器**推迟到它唯一安全的时段** |
| 共享一个可变 `ptr` 呢？ | **允许，且不检查**。`thread_spawn` 的实参是 `u64`，把指针传过去要写一次显式 `as u64` —— 那一次转换就是"我知道这是共享的可变内存"的标记。没有 `Send`/`Sync` 的自动判定，也没有 `unsafe` 关键字；越界的用法由 10.1 第二行管 |

### 10.2 运行期锁

一条**全局取号锁**（ticket lock），只用 `atomic_add`：

* 取号 `@__loment_rt_next`，在 `@__loment_rt_serving` 上自旋；
* `@__loment_rt_own` 记持有者的 `gettid()`，`@__loment_rt_depth` 记重入深度 ——
  同一线程在 `alloc` 里再 `alloc`、或在根帧里 `alloc`，只加深度；
* **谁拿它**：`alloc`、`free`、根表的 push/pop。临界区都**很短**（一次链表走查 /
  一次游标加减），线程之间不会互相等一整个函数体。

为什么不是"加一试试、失败再减回去"：那个形状会被另一个线程的加一减一骗回 0
（§8 实测）。取号锁天然公平，且只用本后端唯一有的原子。

### 10.3 收集器：推迟，不是停世界

`maybe_collect` 的第一句是：

```
%lv = load i32, ptr @__loment_rt_threads
%defer = icmp ne i32 %lv, 0
br i1 %defer, label %out, label %count
```

`@__loment_rt_threads` 在 `thread_spawn` 的调用点、`clone` 之前被置 1，**单调**。
于是：**起过线程的程序，自动收集在本进程剩余的生命里不再运行**。

这是本版**明写的边界**，不是遗漏：

* 收集器今天没有 safepoint 协议。扫另一个线程的根槽要求那个线程停在安全点上，
  而 `alloc` 返回与"指针落到根槽"之间有一个编译器看得见、运行期看不见的窗口 ——
  没有 safepoint 就去扫，可能把一个刚分配、还没登记的块当垃圾收掉。
* 推迟把这个窗口**整个消掉**：收集只在"线程从未启动过"的时段发生，而那时只有
  一个 mutator（主线程），它自己的根表是完整的。
* 代价：线程程序里堆**只增不减**（`free` 仍然兑现、LIFO 仍然回退前沿）。需要长跑
  的线程程序应选 `gc_manual` 并自己 `free`；`gc_auto` + 线程是"能跑、但别指望自动
  回收"的那一档。

真正把它修成"收集与线程并存"要的是 safepoint（或在分配点上的读屏障），那是
`#778` 正文点名的语言级设计，不是这一笔。

### 10.4 判据

| 判什么 | 怎么判 |
|---|---|
| 分配器不会把同一块发给两个线程 | `loment_thread_test::test_concurrent_alloc_never_aliases`：4 线程 × 1500 轮 × 每轮 9 次 `alloc`/`free`，两两比较指针、读回写进去的 64 位标签 |
| 线程真的并发且都跑完 | 同一探针的 `done == N`（有界自旋，挂死会以 `done < N` 现形，而不是把判据卡死） |
| 锁只落在真线程的产物上 | `::test_threaded_output_carries_the_runtime_lock` / `::test_non_threaded_output_is_lock_free` |
| 收集器不与线程交错 | 同一探针开 `choose gc_auto`：`maybe_collect` 每个分配点都被问一次，线程标志置 1 后一次都不收；探针的标签检查同时证明没有块被收掉 |

### 10.5 没写的部分

`Send`/`Sync` 式的**类型级**约束、`unsafe` 边界标记、以及"共享可变指针在编译期
被拒"——都没有。今天那条边界是**文档 + 实参类型**（`u64`），不是检查器。把它变成
检查需要先回答"一个 `ptr` 跨线程传是不是永远要到 `u64` 为止"，那是语言规则，
不是运行期能替用户决定的。
