# 346 · 代数效应与效应处理器：把"向谁求助"变成可检查的行

> 状态: **A0/A1 报告面 + A2a 参考前端已落地**（2026-11-13）· 上游: `docs/262`（LSPE）·
> `docs/306`–`308`（E9 域检查器）· `docs/317`（E10 借用检查器）· `docs/286`（六点一空间）·
> `docs/219`（链接边界）· `standard/LLS.md` §21–§25
> 一句话: 在 LSPE 里加**第十一个引擎**，把"这段代码会向谁求助"变成函数签名上的
> **有限效应行**与词法处理器；`perform` / `handle` / `resume` 各是一条可证伪的站点，
> 证不出来的每一条都变成问题与地板。

---

## 0. 一句话与管辖面

**代数效应**回答的问题不是"这段代码碰了什么内存"，而是：

> 这段代码**要向谁求助**，以及**谁答应它**。

Loment 已经有三条正交的轴：

| 轴 | 问题 | 今天的落点 |
|---|---|---|
| **能力域** | 这个下标**允许**被碰吗？ | `capability` / `guard` / `domain`，运行期 trap（`docs/146`、`docs/306`） |
| **生命周期** | 这个借用**能活到哪**？ | `&a T` / 区域推断，编译期（`docs/299`、`docs/317`） |
| **效应** | 这段代码**会向谁求助**？ | **本文** |

三者不替代：一个 `perform Net.connect(addr)` 可以同时有域要求（`net` 能力必须活着）、
借用要求（`addr` 必须活到调用结束）与效应行要求（`Net` 必须被处理器或调用方接住）。
把它们压成一条"安全检查"会丢掉"哪一条没证出来"。

**管辖面**：本文定义效应与处理器的语义、LSPE 的第十一个引擎（E11）、以及一份
**语法草案**。它**不**定义运行期实现（A0/A1 只做检查器），也不改 `lom/*.lom`
（效应编译成普通函数与结构体，没有新原语，见 §7.6）。

---

## 1. 起点：今天已经有什么，缺什么

### 1.1 已有的三条控制流构造

| 构造 | 形状 | 它能做的 | 它不能做的 |
|---|---|---|---|
| `orelse` | `let a = maybe orelse 0;` | 二选一，失败取默认 | 不能在失败处**继续**原计算 |
| `defer` | `defer f(a);` | 块退出时按 LIFO 跑一次 | 不能在任意点**暂停 / 恢复** |
| `guard` | `guard d(i);` | 越域就 trap | 不回到调用方，也不带值 |

这三条都是**单向**的：要么继续、要么退出、要么 trap。代数效应的分界是
**可以回到 perform 之后继续算**（`resume`）。这不是"更花哨的异常"，而是
"调用方可以把一段计算交给别人代跑，再拿回结果"。

### 1.2 缺的那一格：能力是显式传的，不能组合

今天要把一个能力交给深层调用，只有两条路：

1. **一路传参**：`fn a(cap: Cap) { b(cap); }` —— 传染到每一层；
2. **全局能力**：`capability` 是模块级的，粗粒度。

代数效应给的是第三条：**词法处理器**。`handle Net { … } with { … }` 在作用域里
"提供" `Net` 的操作，被调的深层函数只写 `perform Net.connect(…)`，不需要知道
处理器在哪一层装的。这就是依赖注入，但它有类型、有行、有检查。

### 1.3 今天就有的、必须接上的东西

* `LSPE_SITE_KINDS` / `LSPE_OBLIGATIONS` / 问题码 / LSPC 族（`docs/262`）；
* E9 的域检查器与 `guard` 的静态包含性（`docs/306`–`308`）；
* E10 的"报告面先落、判决后落"的落地形状（`docs/317` §9.5）；
* 默认 `choose runtime no_runtime` —— **默认档必须能跑效应的常见形状**（§4.4）；
* `choose gc` 三档 + `gc_ladder`（LLS §22）—— 续延捕获的回收归它管。

---

## 2. 先把不可能性写死

这一节回答"为什么不能直接抄 Koka / OCaml 5"。

### 2.1 多射续延 × 可变捕获 = 所有权崩

`resume` 两次意味着 `perform` 与 `resume` 之间的代码跑两遍。若那段代码捕获了
`&mut`、`ptr`、或任何有所有权的值，第二遍就会与第一遍**别名**：同一块内存被两条
时间线同时持有，`free` 会双重释放，`&mut` 的独占性直接不成立。

这不是"实现难"，是**语义不可能**：要让多射成立，必须满足下面至少一条：

1. 捕获集全部 `Copy`（复制语义，两份互不影响）；
2. 捕获集全部只读且不可撤销（共享借用）；
3. 语言放弃所有权 / 放弃 `free`。

所以 v1 **不做多射**。以后要开，也只能开成一条显式效应 `MultiShot`，并要求
捕获集 ⊆ `Copy`（§7.5）。**默认 one-shot 不是保守，是唯一自洽的选择。**

### 2.2 效应推断 × 链接边界 = 不可能

`docs/219` 观察过：Koka / Eff 的效应推断都在**源或字节码**层，没有人放在链接边界。
原因很朴素：**链接器没有源码**。它只有已编译的单元，而单元之间要能组合，就必须有
一份**声明出来的**行。

所以 Loment 的选择是：

* **行是契约，必须声明**（至少导出函数）；
* 单元内部可以推断，但推断只用于**检查声明是否漏了**，不替代声明；
* 行随产物走（§7.6），链接期做 `⊆` 检查，不做推断。

### 2.3 处理器 × `extern` = 不可能

C 侧没有证据记录（evidence vector），也没有 Loment 的 `resume` 调用约定。
让效应穿过 `extern` 等于要求 C 函数在不知道处理器存在的情况下回答 `perform`。
所以：

* `extern fn` **不许声明效应**（E11 的 `EffectBounded`）；
* 效应函数不能作为函数指针交给 C（v1 根本没有带效应的函数指针类型）；
* FFI 调用本身是**无效应**的。

### 2.4 一般处理器 × `no_gc` = 没有自动回收

尾续处理器不需要续延对象（§4.4）。一般处理器需要捕获 `perform` 之后的计算：

* 捕获可以放在栈上（不逃逸）—— LGC 的 L0/L1 能证；
* 一旦逃逸到堆上，`no_gc` 没有自动回收档（LLS §22.2 的 `no_gc` 一行全是 `—`），
  只能靠手写 `free` 证明，或者换 `gc_auto`。

A0/A1 不假装能证这一步：一般处理器在 `no_gc` 下进 **Q022 / Assumed 地板**。

### 2.5 处理器 × 动态安装 = 静态不可判

如果处理器可以作为值被传递、在运行期安装到任意点，那么"这个 `perform` 会被谁接住"
在编译期就是不可判的。v1 只允许**词法处理器**：

* `handle` 写在源码里，作用域是词法的；
* 处理器不是一等值（不能 `let h = handle …;` 之后传给别处）；
* 没有 `install` / `withHandler` 这种运行期入口。

这不是能力缺失，而是把"可检查"作为第一约束。动态安装是 §12 的已关闭路径。

---

## 3. 可以合法拿掉的三条假设

`docs/262` §3 的形状：先把不可能性写死，再指出**哪条假设可以拿掉**，拿掉之后就得到
一个既激进又落得下去的设计。

### 3.1 「处理器必须捕获续延」—— **拿掉**

最常见的处理器形状是**尾续**：

```rust
write(text) => { resume(print(text)); }
```

`resume` 是臂里最后一个动作，之后没有别的计算。此时续延就是"返回 perform 的调用点"，
**不需要捕获任何东西**：`perform` 编译成一次普通调用，臂返回值直接成为 `perform`
的值。

这条拿掉之后，默认档 `no_runtime` 能跑绝大多数处理器（日志、依赖注入、mock、
能力提供），只有真正要"先返回、后恢复"的形状才需要续延对象。

### 3.2 「效应必须全局推断」—— **拿掉**

行是声明出来的契约。单元内部推断只做两件事：

1. 检查 `effects` 行有没有漏；
2. 检查调用点的行包含关系。

于是链接边界只需要比较有限集合，不需要源码，也不需要 Datalog。

### 3.3 「处理器必须动态查表」—— **拿掉**

词法处理器栈在编译期就是已知的，`perform` 的目标处理器可以**静态解析**成一个
证据记录（evidence record），作为隐式参数沿调用链传下去。运行期没有"找处理器"的循环。

这与 `docs/262` 的"证据传递"同一形状：**编译期算清楚，运行期只执行**。

---

## 4. 模型

### 4.1 三个原子

| 原子 | 写法 | 语义 |
|---|---|---|
| **效应声明** | `effect Log { fn write(text: &str) -> (); }` | 一组操作的名字与签名 |
| **求助** | `perform Log.write("hi")` | 把参数交给最近的 `Log` 处理器，拿到它给的值 |
| **处理器** | `handle Log { BODY } with { write(t) => { … } }` | 在 `BODY` 的词法范围内提供 `Log` 的操作 |

### 4.2 行（row）是有限集合

函数签名上写：

```rust
fn serve(req: &Request) -> Response effects Log, Net {
    perform Log.write("begin");
    perform Net.connect(req.host);
    …
}
```

`effects Log, Net` 读作"这个函数可能触发 `Log` 或 `Net`；调用我的人要接住它们"。

**子类型关系是子集**：

```
{Log} ⊆ {Log, Net}           // 少触发的一方可以放到多触发的位置
```

没有"行变量"也能覆盖绝大多数代码；真正的效应多态（`fn map<A, B, E>(…) effects E`）
是 §11 的 A5，不在 v1。

### 4.3 深度处理器与 `resume`

`handle E { BODY } with { ARMS }` 是**深度**处理器：

* `BODY` 里的 `perform E.op(…)` 由这个处理器接住；
* 臂里再 `perform E.op(…)` 归**外层**处理器（处理器在臂执行期间是"退出的"）；
* `resume(v)` 把 `v` 送回 `perform` 的位置，继续跑 `BODY` 剩下的部分；
* 臂不调用 `resume` 就是 **abort**：`perform` 不返回，整个 `handle` 返回臂的值。

浅处理器（shallow）不在 v1：它要求臂自己重新安装处理器，几乎总是写错。

### 4.4 尾续 vs 一般

| 形状 | 判定 | 编译策略 | 需要续延对象？ |
|---|---|---|---|
| **尾续** | 臂里每个 `resume` 都是所在块的最后一条语句 | 直接调用 + 返回 | **否** |
| **一般** | `resume` 之后还有计算（`let y = resume(v); y + 1`） | CPS / 续延闭包 | 是 |
| **abort** | 臂里没有 `resume` | `handle` 直接返回 | 否 |

尾续判定是**语法**的（A0 只认"块尾"这一种形状）；它不是语义上的近似，而是
**保守的可证形状**：认不出的就归到一般处理器，走 Q022，不假装它是尾续。

### 4.5 与能力域的关系：`requires`

操作可以声明它要的能力：

```rust
effect Net {
    fn connect(host: &str) -> Conn requires net;
}
```

`perform Net.connect(h)` 的放行条件变成**二选一**：

1. 作用域里有活着的 `net` 能力（E9 的 `InDomain` 证据）；或者
2. 有 `Net` 的词法处理器（它"提供"了这个能力）。

这让处理器成为**能力的词法铸造者**：`handle Net { … } with { … }` 可以在一个作用域里
提供 `net`，而不必把 `Cap` 从 `main` 一路传到第 8 层。A0/A1 只记录 `requires`，
不检查它（那是 §11 的 A2）。

### 4.6 与生命周期的关系：捕获集

一般处理器的续延捕获了一组局部变量。v1 的规则：

* **one-shot**：捕获集按普通借用/所有权规则处理，捕获期间不得 `free`；
* **多射（未来）**：捕获集必须全部 `Copy`（§2.1），否则拒绝；
* 尾续不捕获，因此**没有额外约束**。

### 4.7 与 GC 的关系：捕获的回收

| 形状 | LGC 责任 |
|---|---|
| 尾续 | 无分配，`gc_ladder` 不看它 |
| 一般 + 不逃逸 | L0/L1 可证，栈上或定活 `free` |
| 一般 + 逃逸 | 需要 `gc_auto`，或手写 `free` 的证明 |
| 多射 | 每次 `resume` 复制捕获集；只在 `gc_auto` + `Copy` 下开 |

`CaptureReclaim` 是 E11 的第五类义务；A0/A1 对一般处理器一律记 `Assumed`（Q022）。

---

## 5. 语法草案（未实装）

> 仅为形状。A2a 起，**参考解析器**在 `choose lspe_effects_true` 打开时认这些拼写
> （默认关，旧源一个字节都不变）；`perform` / `handle` / `resume` 目前只解析到 AST，
> 运行期要到 A3。展示用 `rust` 围栏（`CLAUDE.md` 的显示约定）。报告入口是
> `loment lspe effects FILE`，它直接读 token 流。

### 5.1 声明

```rust
effect Log {
    fn write(text: &str) -> ();
    fn flush() -> ();
}

effect Net {
    fn connect(host: &str) -> Conn requires net;
    fn send(c: Conn, bytes: &[u8]) -> u32;
}
```

### 5.2 函数的行

```rust
fn serve(req: &Request) -> Response effects Log, Net {
    perform Log.write("begin");
    let c: Conn = perform Net.connect(req.host);
    perform Net.send(c, req.body);
    return build(c);
}

pub fn library_entry() -> u32 effects Log;   // 导出函数必须写
```

### 5.3 处理器

```rust
fn run() -> u32 {
    let n: u32 = handle Log {
        let a: u32 = perform Log.write("hello");
        a + 1
    } with {
        write(text) => { resume(len(text)); }
        flush() => { resume(0); }
    };
    return n;
}
```

### 5.4 abort 与一般处理器

```rust
// abort：不 resume，handle 直接返回 0
write(text) => { return 0; }

// 一般：resume 之后还有计算（需要续延对象）
write(text) => { let n: u32 = resume(len(text)); n + 1; }
```

### 5.5 边界

```rust
extern fn puts(s: &str) -> i32;          // 合法：C 调用无效应
extern fn bad(s: &str) -> i32 effects Log;  // E11: EffectBounded
```

---

## 6. 静态规则

记号：`R(f)` 是函数 `f` 的行；`H(i)` 是源码位置 `i` 处**生效的**词法处理器销掉的
效应集合（深度语义：臂内不包含自己）。

### R1 行声明（`EffectAllowed`）

```
inferred(f) ⊆ declared(f)        （写了 effects 时）
```

`inferred(f)` 来自函数体里**没有被当场处理器销掉**的 `perform`，加上调用点的行传播。
证不出来 ⇒ **Q018**（`missing_effect`）。

**直接 `perform` 的额外规则**：当场没有处理器时，效应必须出现在 `declared(f)` 里。
私有函数的**传递**行可以靠调用图推断，但"开口要"的动作不许靠推断蒙混。

### R2 调用（`EffectAllowed`）

```
contract(callee) \ H(call_site) ⊆ R(caller)
```

`contract(g)` 是 `g` 的声明行（声明了）或推断行（没声明）。调用方声明了行却盖不住 ⇒
`unhandled_call` + **Q018**。

### R3 处理器（`EffectAllowed` + `HandlerExhaustive`）

```
R(handle E { BODY } with { ARMS })
  = (R(BODY) \ {E}) ∪ R(ARMS)
```

臂里的 `perform E.op` **不**由本处理器接住（深度语义），归外层。

### R4 穷尽（`HandlerExhaustive`）

`ARMS` 的操作名集合与 `E` 的声明集合**逐条相等**：不许少（`handler_incomplete`），
不许外来（`arm_op_not_in_effect`），不许重复（`duplicate_arm`），参数个数要一致
（`arm_arity`）。任何一条不成立 ⇒ **Q019**。

### R5 `resume` 线性（`ResumeLinear`）

每个臂的每条路径：`resume` **0 次**（abort）或**1 次**。

* 同一块里两次 ⇒ `resume_twice`（构造性反例，**Q020**）；
* 次数 ≥2 但不在同一块、又不是尾续 ⇒ `resume_ambiguous`（Q020）；
* 出现在臂外 ⇒ `resume_outside_handler`（Q020）。

A0 只认"块尾"这一种尾续形状；控制流精确的 0/1 证明是 A2。

### R6 尾续判定（`CaptureReclaim` 的前置）

> 臂里每个 `resume` 都是所在块的**最后一条语句**（后面只剩 `}`）。

这是**充分条件**，不是必要条件：更复杂的尾续形状（例如两个分支各自尾续）会被判成
一般处理器，走 Q022，而不是被误收。

### R7 边界（`EffectBounded`）

`extern fn` 的签名不许出现 `effects`；出现即 `extern_effects` + **Q021**。
v1 没有带效应的函数指针类型，因此效应也不可能通过函数值穿过 FFI。

### 6.1 每一条"证不出来"都有名字

| 规则 | 证不出来 | 问题码 | 默认 |
|---|---|---|---|
| R1 行声明 | 声明盖不住实际 | Q018 | 拒（`missing_effect`） |
| R1 导出 | `pub fn` 没写行 | Q018 | 标注（`undeclared_row`） |
| R2 调用 | 调用方的行盖不住 | Q018 | 拒（`unhandled_call`） |
| R3 残余 | 处理器表达不了残余行 | Q019 | 拒 |
| R4 穷尽 | 臂集合不相等 | Q019 | 拒 |
| R5 线性 | 次数证不出 0/1 | Q020 | 拒 |
| R6 尾续 | 不是块尾 | Q022 | 标注（Assumed） |
| R7 边界 | 效应要过 C | Q021 | 拒 |

---

## 7. 动态语义与编译策略

### 7.1 证据向量

每个效应在函数签名里有一个位置。效应函数编译成多带一个**证据向量**参数：

```
f(args…, ev)
```

`handle E { BODY } with { ARMS }` 在进入 `BODY` 前构造一份 `E` 的证据记录
（每个操作一个函数指针 + 捕获环境），替换 `ev` 里的 `E` 槽位；`BODY` 结束后恢复。

### 7.2 `perform`

```
perform E.op(a…)  ⇒  ev[E].op(ev, a…, k)
```

`k` 是续延。尾续时 `k` 就是"返回调用点"，编译器把它消掉（§7.3）。

### 7.3 尾续：零续延对象

若臂的每个 `resume` 都在块尾，`perform` 直接变成：

```
v = arm(args…)      // 普通函数调用
```

`resume(x)` 直接成为 `return x`。没有闭包、没有堆、没有证据记录之外的东西。
这条路径在 `no_runtime` + `gc_manual` 下可用。

### 7.4 一般：CPS 或续延闭包

两种实现，按 A3/A4 的测量选：

| 策略 | 做法 | 成本 | 适用 |
|---|---|---|---|
| **CPS** | 每个效应函数多一个 `k` 参数，`perform` 把 `k` 交给臂 | 调用约定改变，FFI 不可见 | 全量 |
| **续延闭包** | 捕获 `perform` 之后的栈段，装进一个一次性的闭包 | 需要 `alloc` + 定活 | 局部 |

v1 的设计选 **CPS**：它与 Loment 的栈机后端同形（`lomelf` 是栈机，续延就是一段
待执行的计算），也不需要运行期栈复制。续延闭包留给"测量显示 CPS 太贵"的场合。

### 7.5 多射（未来）

`MultiShot` 是一条**显式效应**，不是默认：

```rust
effect MultiShot requires copy;   // 捕获集必须全部 Copy
```

每次 `resume` 复制捕获环境；不带 `copy` 的捕获集在类型检查期拒绝。`no_gc` 下直接拒。

### 7.6 产物与 L0

* 效应**不引入新的 L0 原语**：证据记录是普通结构体，臂是普通函数；
* 行写进产物元数据（Potato 的字段，A3 定名），链接期比较有限集合；
* `lom/*.lom` 一个字节都不动 —— 这与 `docs/306` §10 的"第一阶段不需要动 L0"同形。

---

## 8. 接进 LSPE：E11

### 8.1 站点（`LSPE_SITE_KINDS` 加八种）

| 站点 | 何时产生 | 义务 | 证据 |
|---|---|---|---|
| `effect_decl` | 每个 `effect` 声明 | `EffectAllowed` | `Syntactic` |
| `row` | 每个函数签名 / 推断结果 | `EffectAllowed` | `Typed`（声明）/ `Syntactic`（推断） |
| `perform` | 每个 `perform` | `EffectAllowed` | `Syntactic`（处理器）/ `Typed`（行）/ `none` |
| `handle` | 每个 `handle` | `HandlerExhaustive` | `Syntactic` |
| `resume` | 每个臂的 `resume` | `ResumeLinear` | `LocalFlow` |
| `row_call` | 每个本单元调用点 | `EffectAllowed` | `Interproc` |
| `effect_foreign` | `extern fn` + `effects` | `EffectBounded` | `Axiom` |
| `handler_capture` | 每个一般处理器 | `CaptureReclaim` | `Assumed`（地板） |

### 8.2 五条义务（`LSPE_OBLIGATIONS` 14 → 19）

| 义务 | 语义所有者 | accept 集合 |
|---|---|---|
| `EffectAllowed` | `docs/341` §6 | `Syntactic` / `Typed` / `Interproc` / `Assumed` |
| `HandlerExhaustive` | `docs/341` §6 R4 | `Syntactic` / `Typed` / `Assumed` |
| `ResumeLinear` | `docs/341` §6 R5 | `LocalFlow` / `Syntactic` / `Assumed` |
| `EffectBounded` | `docs/341` §6 R7 | `Axiom` / `Typed` / `Assumed` |
| `CaptureReclaim` | `docs/341` §4.7 | `LocalFlow` / `Interproc` / `Assumed` |

**`Differential` 不在任何 accept 里**（`docs/262` §5.3）：孪生只证一致，不证对。
E11 的孪生判据只证明"两侧看同一份 token 得到同一组站点"，不销任何义务。

### 8.3 五个问题（`LSPE_QUESTIONS` 17 → 22）

| 码 | 问题 | 典型形状 |
|---|---|---|
| **Q018** | 效应行未声明或盖不住 | `perform` 没处理器也没写行；`pub fn` 省略行 |
| **Q019** | 处理器穷尽性无法证明 | 少臂、外来臂、重复臂、参数个数不符 |
| **Q020** | `resume` 线性无法证明 | 同块两次、非尾续的多次、臂外 `resume` |
| **Q021** | 效应要穿越 C 边界 | `extern fn … effects E` |
| **Q022** | 续延捕获的回收未证明 | 一般处理器；`no_gc` 下尤其 |

每一问都有固定模板（`claim` / `chain` / `tried` / `options` / `default` /
`reversible`），`tried` 覆盖三问：来源、反证、对质。

### 8.4 容量（`LSPE_CAPACITIES` 加三条）

| 名字 | 上限 | 越界时 |
|---|---|---|
| `effect_declarations` | 64 | `lspe_effects` 回 1 → `pt_wfail`（点名） |
| `ops_per_effect` | 256 | 同上 |
| `handler_depth` | 64 | Q019，不静默 |

### 8.5 LSPC 三个新族

| id | 家族 | 在场条件 |
|---|---|---|
| `effect_unhandled` | 未处理的效应 | 有 `perform` 站点的 `proved == False` |
| `effect_general_handler` | 非尾续处理器 | 有 `handler_capture` 站点 |
| `effect_foreign` | 效应穿越 extern | 有 `effect_foreign` 站点 |

### 8.6 为什么这一轮不接台账

与 `docs/317` §9.5 同一条理由：台账要求参考侧与自举侧**逐字节一致**。效应站点一旦
进 Potato，自举侧必须同计数，而自举检查器是 token 级、没有 AST —— 那是一次独立的、
要重算种子与 genesis 的改动。所以：

* 本轮只落**分析核心 + 查看入口**；
* 默认构建的产物与台账一个字节都不变；
* 下一步（A2c）才是两侧同计数。

---

## 9. 算法、预算与确定性

### 9.1 固定顺序

1. 扫效应声明与操作表；
2. 扫函数签名与声明行；
3. 扫处理器与臂（含 `resume` 次数、尾续判定）；
4. 扫 `perform`；
5. 扫本单元调用点；
6. 固定点：从局部的 `perform` 行出发，按调用图并集传播；
7. 逐条判定、生成站点、问题、发现；
8. 排序输出（`line` → `kind` → `message`；站点按 id）。

### 9.2 固定点收敛

行只增不减，名字有限，所以轮数 ≤ 函数数 + 1；实现里就是这个上界，没有外部求解器
（`docs/317` L1 的同一条约束）。超出上界不会发生；真发生了就停在最后一轮并报
Q005 的形状（A2 再补）。

### 9.3 词法近似的边界（写出来，不假装没有）

* 没有 AST：臂参数只认 `op(p1, p2)`，不认解构模式；
* 尾续只认"块尾 + 尾随 `}`"，更复杂的形状归一般处理器；
* `resume` 的 0/1 只看块骨架，不做控制流敏感（A2）；
* 泛型/替换不建模；跨单元摘要不建模（A4）；
* R3 在 A0 只到**函数粒度**：`handle` 表达式的残余行不单独建模，臂里的 perform
  直接算到外层函数；
* 同名局部变量遮蔽函数名时，调用点可能多认一条（A2 的符号表解决）。

### 9.4 确定性

所有列表排序；站点 id 内容寻址（`unit:kind:fnv1a32(key#ord)`）；同一份 token 流
两次分析逐字节相同。判据 `test_effect_declarations_rows_and_determinism` 钉住它。

---

## 10. 判据（先红后绿）与本次落地记录

### 10.1 未修先红

2026-11-13 先写判据，在**没有 `effects.py`、没有登记项、没有 CLI** 的树上跑：
`lspe_test` 53/67，14 条红（13 条新判据 + 义务计数那条）。之后实现，67/67 绿。

### 10.2 每条判据钉什么

| 判据 | 喂什么 | 期望 |
|---|---|---|
| `test_effect_registry_entries_are_complete` | 登记表 | 5 义务 / 8 站点 / 5 问 / 3 LSPC 族 |
| `test_effect_declarations_rows_and_determinism` | 声明 + `effects` 行 | 操作表、参数个数、两次分析逐字节相同 |
| `test_perform_without_handler_or_row_is_unhandled` | 裸 `perform` | `missing_effect` + Q018 |
| `test_handle_discharges_the_effect_from_the_row` | 尾续处理器 | 无发现；穷尽为真 |
| `test_handler_must_cover_every_operation` | 少一条臂 | `handler_incomplete` + Q019 |
| `test_duplicate_arm_and_foreign_operation_are_reported` | 重复臂 / 外来 op | 两个发现 |
| `test_resume_twice_is_refuted_but_abort_is_allowed` | 同块两次 / abort | 反例 / 合法 |
| `test_tail_resumptive_is_distinguished_from_general` | 块尾 / `let y = resume(v)` | 尾续 / 一般 + Q022 |
| `test_call_rows_propagate_and_a_declared_contract_must_cover_them` | `outer effects Ping` 调 `inner effects Log` | `unhandled_call` |
| `test_extern_fn_cannot_declare_effects_and_resume_stays_inside_an_arm` | `extern … effects` / 臂外 `resume` | `extern_effects` / `resume_outside_handler` |
| `test_general_handler_under_no_gc_is_an_assumed_capture` | `choose gc no_gc` + 一般处理器 | `capture_unproved` |
| `test_effect_sites_are_lspe_shaped` | 站点 | kind/义务/证据/问题都在册 |
| `test_effects_cli_is_a_report_not_a_verdict` | CLI | 不改源码；`--strict` 才用退出码 |

### 10.3 本次落地记录（A0/A1）

| 内容 | 位置 | 判据 |
|---|---|---|
| 效应表 / 函数行 / 处理器 / `perform` / 调用传播 | `loment/lspe/effects.py` | 上面 12 条 |
| 登记（义务、站点、问题、模板、容量、LSPC） | `loment/lspe/registry.py`、`lspc.py` | `test_effect_registry_entries_are_complete` |
| 查看入口 `loment lspe effects FILE [--json] [--strict]` | `tools/loment.py` | `test_effects_cli_is_a_report_not_a_verdict` |
| 门禁地板 | `.github/gate-floors.txt`（`lspe_test` 54 → 67） | 双向棘轮 |

**已知边界**：见 §9.3；一般处理器只到 Q022/Assumed；`requires` 只记录不检查；
自举侧还没有同形分析（A2b）。

### 10.4 A2a 落地记录（2026-11-13，参考前端）

| 内容 | 位置 | 判据 |
|---|---|---|
| 构建旋钮 `lspe_effects_true\|false`（默认 `false`） | `tools/lomentc.py` 的 `CORE_SLOTS` / `CORE_SLOT_DEFAULTS` | `test_effects_slot_is_registered_and_defaults_off` |
| `effect` 声明、`effects` 行（AST + 擦除） | `EffectDecl` / `EffectOp` / `Func.effects` / `Module.effects` | `test_effect_declarations_and_rows_are_accepted_and_erased` |
| `perform` / `handle` / `resume` 解析成 AST，检查器报 **E023**（A3 才落） | `Perform` / `Handle` / `HandlerArm` / `Resume`、`Module.effect_runtime` | `test_perform_parses_then_refuses_as_e023`、`test_handle_and_resume_parse_then_refuse_as_e023` |
| 默认关 ⇒ 旧拼写仍是未知关键字 | `_prescan_slot` + `Parser.effects` | `test_effects_are_off_by_default` |
| 行里的未声明效应 / 重名 | `_effect_front_errors` | `test_effect_row_names_must_be_declared_and_unique` |
| `extern fn … effects` 的点名拒 | 同上 | `test_extern_cannot_declare_effects` |
| 语言服务读同一个开关（消费方轴） | `tools/loment_lsp.py` 走 `lomentc.Parser` | `test_python_lsp_knows_the_effects_switch` |
| 格式化器把 `effect` 当顶层声明 | `tools/lomfmt.py` 的 `TOP_KW` | `loment_fmt_test` |

**自举侧的状态（明写）**：`loment/selfhost/*` 本轮**不动**。它不认识
`lspe_effects_true`，所以同一份源在自举编译器上会停在 E022 / 未知顶层关键字 ——
**是点名拒绝，不是静默改义**。A2b 才做镜像；在那之前，效应源只由参考编译器读。
这与「两侧逐字节」不冲突：默认关时两侧的产物仍然逐字节相同。

---

## 11. 落地阶梯

每一步都要求：先红后绿、没有静默、旧版次一个字节不变。

| 步 | 内容 | 先红的判据 | 出口 |
|---|---|---|---|
| **A0**（已完成） | 声明 / 行 / 处理器 / `perform` 的报告面 | 13 条新判据全红 | `lspe effects` 可看 |
| **A1**（已完成） | 调用传播 + 尾续/一般分类 + Q018–Q022 | 调用行与尾续判据红 | 报告面完整 |
| **A2a**（本轮） | **参考前端**接受 `effect` / `effects` / `handle` / `perform` / `resume`，由构建旋钮 `choose lspe_effects_true` 打开（默认关）；声明与行擦除，运行期构造 E023 | 开关关时旧源逐字节不变；开时 AST 有节点、E023 点名 | 参考编译器可解析；IR 不变成 |
| **A2b** | **自举侧镜像**（parser / checker / codegen + 种子），并与参考逐字节对拍 | 同一份源两侧同判 | 两侧同语法面 |
| **A2c** | E11 站点进 Potato 台账（两侧同计数） | 台账计数判据 | `lspe state` 列得出 |
| **A3** | 尾续处理器的代码生成（两侧链接器）+ 行进产物元数据 | 正例跑通、反例仍拒 | `no_runtime` 下可用；链接期可查 |
| **A4** | 一般处理器的 CPS + 捕获回收 | `no_gc` 反例 / `gc_auto` 正例 | Q022 有答案 |
| **A5** | `MultiShot` + `Copy` 捕获集 | 多射反例在 A4 下红、A5 下按规则收/拒 | 默认仍是 one-shot |

**A0/A1 是纯报告步**：不改判决、不写台账、不动 `lom/*.lom`。这与 E9/E10 的落地顺序
同形 —— 先把事实变成站点，再让事实改变判决。

**A2 拆成三小步**（2026-11-13 的实测判断）：两侧同时接语法要动自举解析器与种子，
而自举侧的容量/墙钟成本是独立的。A2a 先把**参考前端**与开关落地（本轮），A2b 再做
自举镜像，A2c 才是台账。**A2a 之后，用 `lspe_effects_false`（默认）的旧源产物
一个字节都不变** —— 这是能先合入的理由。

---

## 12. 已关闭的路（带理由）

| 路 | 为什么关 |
|---|---|
| 动态安装处理器 / 处理器作为一等值 | 静态不可判（§2.5） |
| 链接期效应推断 | 链接器没有源码（§2.2） |
| 多射默认 | 与所有权冲突（§2.1） |
| 带效应的函数指针 | 调用约定未定，FFI 不可见（§6 R7） |
| 既有的 `orelse` 改写成处理器 | 单向控制流不是效应；混在一起会让行失去意义 |
| 用 Datalog / 外部求解器算行 | `docs/317` L1：精度必须有成本模型 |
| 浅处理器 | 臂要自己重装处理器，几乎总是写错；先不开 |
| 在 `extern` 上做效应适配层 | C 侧没有证据记录；适配层是另一个语言的事 |

---

## 13. 与 Koka / OCaml 5 / Rust 的对比

| 维度 | Koka | OCaml 5 | Rust（stable） | **Loment（本设计）** |
|---|---|---|---|---|
| 行的位置 | 类型推断 | 无显式行 | 无 | **签名 + 产物** |
| 链接边界 | 不跨 | 不跨 | 不跨 | **跨（行随产物）** |
| 多射 | 有 | 有（fibers） | 无 | **默认无；显式 `MultiShot` + `Copy`** |
| 默认运行期 | 运行期系统 | 运行期 | 无 | **`no_runtime` 可跑尾续** |
| 线性 | 运行期/约定 | 运行期 | — | **静态 `ResumeLinear`** |
| 检查器 | 类型系统 | 类型系统 | — | **LSPE 引擎，证据/问题/地板** |

**可证伪的三条**：

1. 尾续处理器在 `no_runtime` + `gc_manual` 下可用（A3 的判据）；
2. 跨单元的行包含关系在链接期可查（A3 的判据）；
3. `resume` 两次在没有 `Copy` 捕获集时**编译期**拒（A5 的判据）。

---

## 14. 我不主张

1. **不主张比 Koka 更强**：它的行多态与推理比本设计完整；本设计换的是"链接边界 +
   默认无运行期 + 静态线性"。
2. **不主张今天的语法会定稿**：§5 是草案；A2a 之前参考编译器不认，自举侧要到 A2b。
3. **不主张 A0/A1 是"已实现效应"**：它只把形状变成可检查的站点，运行期是 A3/A4。
4. **不主张尾续判定完备**：它只认块尾；更复杂的形状会被判成一般处理器。
5. **不主张 `requires` 已经接上 E9**：A0/A1 只记录，A3 之后才检查。

---

## 15. 开放问题

1. **行变量**：`fn map<A, B, E>(…, f: fn(A) -> B effects E) … effects E` 需要带效应的
   函数类型；v1 不开，A5 再评估。
2. **处理器状态**：臂之间共享的可变状态（`handle` 自己的 `let mut`）今天没有语法；
   如果加，它属于捕获集还是处理器环境？
3. **trait 方法上的行**：`trait Source { fn read(&self) -> u32 effects Io; }` 与
   impl 的行包含关系需要一条规则（协变/不变）。
4. **跨单元摘要**：A4 的 `Interproc` 证据从库的产物元数据读；库没有行时怎么办？
5. **`abort` 的析构**：abort 跳过 `BODY` 剩余部分，`defer` 是否要跑？未定。
6. **与 `defer` 的顺序**：`handle` 退出与 `defer` 的 LIFO 顺序需要一条规范句子。

---

## 16. 引用

* `docs/262` — LSPE：义务、证据、问题、LSPC、台账。
* `docs/306`–`308` — E9 域检查器：域代数、上下文域、嵌套与循环。
* `docs/317` — E10 借用检查器：loan 一等对象、报告面先落。
* `docs/286` — 六点一空间：效应与能力域在"型"这一轴上的位置。
* `docs/219` — 世界端口：效应推断为什么不放在链接边界。
* `standard/LLS.md` §21–§25 — 能力域、LGC、LMS、LSPE 的规范面。
* Leijen, *Type Directed Compilation of Row-typed Algebraic Effects* (POPL 2017) —
  行多态与证据传递。
* Leijen, *Inferring Algebraic Effects* (LMCS 2014) — 效应推断的边界。
* OCaml 5 effect handlers 文档 — 深度/浅层、one-shot 与 multi-shot 的运行期代价。
