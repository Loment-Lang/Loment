# 320 · Loment 最终态：权能的时序安全

> 状态: **设计稿；S1 已落地（见 §12）**（2026-11-09）
> 上游: `docs/146`（guard 语义）· `docs/306`（一般类/时类）· `docs/307`（语境类）·
> `docs/308`（嵌套/循环）· `docs/314`（块域）· `docs/315`（域循环）· `docs/206`（所有权边界）·
> `docs/299`（生命周期）· `docs/210`（LGC）· `docs/253`（LMS）· `docs/262`（LSPE）· `docs/207`（逆回收）
> 一句话: **空间安全问"这个索引在不在域里"，时序安全问"这个索引现在还是不是你被授权时的那一个"。**
> 前者 `guard` 已经回答；后者要补两样东西——**对象代**与**安全点**。最终态的判决只有一句：
> 同一次 `guard`，同时回答空间、对象身份、授权时效三个问题。

---

## 0. 先把"时序安全"拆成三个词

"权能的时序安全"这句话里有三个词，各自对应一件不同的事：

| 词 | 问什么 | 今天的答案 | 本文补什么 |
|---|---|---|---|
| **权能**（capability） | 凭什么可以碰 | `capability` / `domain` / `guard`（`docs/146`、`docs/314`） | 从"空间的谓词"升级成"对象的引用" |
| **时序**（temporal） | 什么时候还能碰 | 时律 + `tick` + `guard@k`（`docs/306` §4、`docs/315`） | 对象代 + 撤销代 + 静默点 |
| **安全**（safety） | 碰错了会怎样 | trap，不产生副作用（`docs/146` S3） | 失败必须**点名**是哪一钥匙失效（`docs/262`） |

今天这三件事只完成了第一件的一半：
`guard` 保证"索引在域内"，**不保证"索引背后的对象还是当初那个"**。
所以 `docs/146` §5 和 `LLS` §21.10 都诚实写着：**`revocable` 只有表示，撤销语义不存在**。

本文要写死的就是那半句：

> **撤销返回后，所有旧权能都必须失败；对象死亡后，所有旧引用都必须失败；
> 并且这两件事都不需要扫描内存。**

---

## 1. 先例：时序安全的六条技术路线

时序安全不是 Loment 独有的问题。它有一条四十年的谱系，按"元数据放在哪"可以分成六类。
每一类都证到了某个东西，也都死在某个地方。下面先给地图，再逐条讲。

### 1.1 按元数据位置分类

| # | 路线 | 元数据放哪 | 代表 | 它证的时序性质 | 它付的代价 |
|---|---|---|---|---|---|
| 1 | **能力字** | 指针里（tag + bounds + otype） | CHERI、Morello | 空间强；时序要撤销服务 | 新硬件；撤销要扫描 |
| 2 | **带外影子** | 对象头 / 影子内存 | Fil-C、ASan、CETS | 运行期完整 | 运行期成本；ABI 或工具链 |
| 3 | **内核对象表** | 内核的句柄表 / 派生树 | seL4、EROS、KeyKOS、Genode、Fuchsia | 对象级完整 | 只在受管对象上成立 |
| 4 | **类型系统** | 编译期区域 / 生命周期 | Rust NLL、Tofte–Talpin、Cyclone、线性类型 | 编译期完整（独占子集） | 表达力 / FFI 边界 |
| 5 | **回收协议** | 全局代 / 危险指针 | hazard pointers、EBR、RCU、引用计数 | 并发下的静默 | 手工纪律；停顿 / 泄漏 |
| 6 | **硬件概率** | 4 位 tag / 16 字节粒度 | ARM MTE、SPARC ADI | 概率性检测 | 可猜；freed-state 仍需隔离 |

这张表的关键读法是最后一列：**六条路线没有一条同时做到"完整 + 零成本 + 不改语言"**。
`docs/253` §2 已经把这条不可能性写成一句话：

> 完备 + 零成本 + 不改语言，是一条闭合的死链。

Loment 能拿掉的是第一条与第三条假设（它是自造语言，且有逐访问判决点）；
拿不掉的是"元数据必须存在"与"完整性相对于根集"（§4、§8 会反复用到）。

### 1.2 逐条：它证什么、付什么、死在哪

#### ① CHERI / Morello：空间强，时间要扫

CHERI 把能力做成一个带 tag 的机器字：基址、上界、权限、对象类型都在指针里。
越界与类型混淆在**每一次解引用**上被硬件拒绝——这是空间安全的教科书答案。

但 CHERI 的指针里**没有"对象代"**。一块内存被 `free` 后重新分配，旧能力字仍然通过 tag 检查，
仍然指向同一段地址。于是时序安全必须靠**撤销服务**：

- **CHERIvoke**（MICRO 2019）把已释放的对象隔离（quarantine），周期性地用一张影子图
  **扫一遍内存**，把所有悬垂能力清掉；报告在 25% 堆开销下平均 <5% 执行时间开销。
- **Cornucopia**（S&P 2020）把撤销做成 CheriBSD 的并发内核服务：跟踪能力在内存里的流动，
  用 `revoke` 标记与扫描回收；SPEC CPU2006 平均 <2%、最坏 8.9%，代价是 stop-the-world 停顿。
- **Cornucopia Reloaded**（ASPLOS 2024）用**每页的 capability load barrier** 把停顿摊掉。

这三条是同一件事的三个刻度：**CHERI 的时序安全 = 找到所有旧能力副本 + 让它们失效**。
"找"必须扫，因为能力在指针里，没有一张每次访问都经过的表。

> **Loment 拿走的**：能力作为一等值、tag 作为权威、撤回是对象级事件。
> **Loment 不抄的**：扫描。理由见 §4——Loment 的判决点本来就在每次访问上。

#### ② Fil-C：把"原子失效"做成了运行期事实

Fil-C（Pizlo, 2025）用 **InvisiCaps**（不可见能力）把每个指针变成一个带基址与上界的软件能力，
再配 **FUGC**（并发、非搬迁 GC）。`free()` 把对象的所有能力**原子地**重定向到一个 free 单例，
于是 use-after-free、double-free、invalid-free 一律 panic。

它证明了一件重要的事：**"释放即失效"是可以做到的**，而且不需要新硬件。
代价写在明面上：**不 ABI 兼容**（要重建 userland），全运行期检查，且对象头在普通 DRAM 里，
2026 年已有 Rowhammer 打穿 InvisiCaps 上界字的报告。

> **Loment 拿走的**：释放是一次**原子失效事件**。
> **Loment 不抄的**：GC + ABI 重写。Loment 已经有逐访问判决点，不需要靠 GC 找副本。

#### ③ seL4：派生树上的撤销是完备的

seL4 的撤销做在**能力派生树（CDT/MDB）**上：删后代。

- `seL4_CNode_Delete` 删除一个能力；若它是某个对象的**最后一个**能力，对象被销毁（finalise）。
- `seL4_CNode_Revoke` 对 CDT 的所有子能力执行 delete，保留自身。
- 撤销可能**部分完成**：撤销线程中途被销毁，或持有目标能力的存储被删除。

它证的是：**在一个受管的对象图里，撤销是完备的、可形式化的**。它不证的是：
C 指针的 use-after-free——因为裸指针根本不在这张图里。

> **Loment 拿走的**：**C2 级联**（`docs/306` §6.1）与"墓碑 id 不复用"就是 CDT 的 L1 版本。
> **Loment 不抄的**：内核对象模型。Loment 的对象可以是 disk block、proc slot、堆块。

#### ④ EROS / KeyKOS / Genode：撤销是对象的属性

EROS/KeyKOS 的经典答案是 **revoker capability**（Redell 1974 的间接层）：
发放能力时先经过一个可撤销的间接对象，删掉它，所有派生能力一起失效。
EROS 还用了 forwarding object（KeyKOS 的 "red segment"）做转发与失效。

这条线留下了一句本文反复要用的话：

> **没有"可撤销的能力"，只有"可撤销的对象"。**

Genode 把这句话推到极致：会话对象（RPC object）的寿命由服务端拥有；
会话一关，指向它的能力**自然失效**——撤销是对象销毁的副作用，不做选择性撤销。

> **Loment 拿走的**：撤销挂在**对象**上，不挂在语法上；对象死亡是唯一的撤销源。
> **Loment 不抄的**：间接层。Loment 把间接层压成一个**代字**（§3.1）。

#### ⑤ Fuchsia / Zircon：句柄表 + 代 + 权利

Zircon 的句柄是进程句柄表里的 32 位索引，低两位固定，**槽位里带 generation count**；
KOID 全局唯一、永不复用。权利只能通过 `zx_handle_duplicate` / `zx_handle_replace` **收窄**，
不能放大。句柄有效 ⇒ 对象活着；最后一个强引用消失 ⇒ 确定性销毁。

这是"内核对象表"路线里最干净的一版，因为它把**三件事**拆开了：

1. **身份**（KOID，永不复用）——排除 ABA；
2. **代**（句柄槽里的 generation）——排除旧句柄别名；
3. **权利**（rights mask，只减不增）——排除授权放大。

> **Loment 拿走的**：这三件拆分几乎是本文 §3 的骨架。
> **Loment 不抄的**：全局内核表。Loment 的"表"可以落在编译产物里。

#### ⑥ Rust / 区域 / 线性类型：编译期赢在独占

Rust 的 NLL / Polonius 用一个借用图证明"引用不会比来源活得长"；
Tofte–Talpin 的区域推断用 `letregion` 把分配与释放绑在词法作用域上（ML Kit）；
Cyclone 把区域写进 C 的方言；线性/仿射类型（Wadler、Clean 的唯一性类型、Linear Haskell）
用"恰好一次"把复制本身禁掉。

它们的共同点是：**只要你能证明"独占 + 词法寿命"，时序安全在编译期就完成了，运行时零成本**。
共同边界也一样：**FFI、别名、动态图、跨函数的所有权**一进来，证明就断。
`docs/299` 与 `docs/317` 已经把 Loment 的静态那一半写到了这个边界上。

> **Loment 拿走的**：静态证明覆盖"独占 + 词法"的子集（`&T`、`consumed`、`witnessed`）。
> **Loment 不抄的**：把整门语言塞进借用检查器。运行时代负责其余部分。

#### ⑦ 回收协议：静默是另一半

hazard pointers（Michael 2004）、epoch-based reclamation（Fraser）、RCU 解决的是同一个问题的
**第二相**：对象已经死了，但还有线程正在用它。它们的答案是**静默（quiescence）**：
要么每个线程公布自己正在用的指针（HP），要么所有线程经过一个 epoch（EBR），
要么等一个 grace period（RCU）。

代价也很清楚：HP 每次访问要两次原子写 + fence；EBR 一个被暂停的线程就能让回收无限延后
（不破坏安全，破坏的是活性）；RCU 要复制再替换。

> **Loment 拿走的**：安全点就是 quiescent state（`docs/246`、`docs/315` 的回边 tick）。
> **Loment 不抄的**：手工纪律。块入口/出口、循环回边已经是安全点。

#### ⑧ MTE：概率性不是时序安全的终点

ARM MTE 给每个 16 字节 granule 一个 4 位 tag，指针顶字节带一个 4 位 tag，访问时比较。
4 位只有 16 个值，检测概率是 `1 − 2⁻⁴ = 93.75%`；而且**释放后未重分配**的 UAF，
在"free 不重打 tag"的简单设计下根本不被缓解。它便宜、可部署，但**概率性**。

> **Loment 拿走的**：tag 的想法（代就是一个更宽的 tag）。
> **Loment 不抄的**：概率性。Loment 的代是精确比较。

### 1.3 五条收敛

把上面八条压成五句话，就是本文的设计前提：

1. **完整性总是相对于一个根集。** CHERI 扫内存；seL4 走派生树；Fuchsia 走句柄表；
   Rust 走类型区域。没有"全局完整"，只有"对这个根集完整"。
2. **非概率要求元数据是权威的**：放在指针里、放在内核表里、放在类型里。
   影子内存与 4 位 tag 都只是**检测**，不是**保证**。
3. **撤销有两相：找 + 静。** 找是"还有哪些副本"，静是"还有哪些在飞的操作"。
   第二相才是贵的那一半——Cornucopia Reloaded 的存在就是证据。
4. **ABA 是独立问题。** 代 / 墓碑是通用解：Fuchsia 的 generation、seL4 的 KOID、
   EROS 的 allocation count，都在做同一件事。
5. **静态赢在独占与词法，运行期赢在完整。** 混合是常态：Rust + GC、CHERI + revoker、
   Fil-C + FUGC，没有纯种。

### 1.4 撤销的分类学：本文要选哪一格

Redell（1974）与 Gligor（1979）把撤销拆成几个正交维度。本文的选择先写在这里，
后面 §3–§5 只是把它兑现：

| 维度 | 取值 | 本文的选择 |
|---|---|---|
| 范围 | 全局 / 选择性 | **选择性**：对象级、子树级 |
| 时机 | 立即 / 延迟 | **判决立即**（fail-closed），**回收延迟**（静默后） |
| 依赖 | 独立 / 依赖 | **依赖**：派生树上的撤销向下级联 |
| 机制 | 间接 / 代 / 扫描 / 标签 | **代 + 派生树 + 逐访问判决** |
| 完整性 | 绝对 / 相对根集 | **相对根集**（§4.3 推论 3） |

---

## 2. Loment 今天有什么、缺什么

### 2.1 已有：判决点、时律、安全点、账本

| 已有物 | 出处 | 它已经回答了什么 |
|---|---|---|
| `capability` / `domain` / `guard` | `docs/146`、`docs/314` | 索引在不在域里 |
| 18 种形状 / 极性 / 时律 / 语境类 | `docs/306`、`docs/307` | 域怎样随时间/语境变 |
| `guard@k` 钉住实例 | `docs/306` §4.3 | 域实例换代后旧守卫失败 |
| `generational(N)` | `docs/306` §5.2 #8 | 域极性按代翻转 |
| C1t 寿命包含 | `docs/308` §1.3 | 祖先续期截断子域 |
| C2 撤销级联 / 墓碑 | `docs/306` §6.1 | 设计，未实装 |
| 安全点 / 回边 tick | `docs/246`、`docs/315` | 虚拟钟何时前进 |
| 块域自动守卫 | `docs/314` | **每次下标访问有一次判决** |
| 借用 / 生命周期 | `docs/299`、`docs/317` | 独占子集的静态证明 |
| LGC 回收 | `docs/210`、`docs/207` | 内存何时归还 |
| LSPE 台账 | `docs/262` | 每个判决的证据/义务 |
| `revocable` 标志 | `docs/146` §5、`LLS` §21.10 | **只有表示** |

这张表里有两格是关键：

- **块域自动守卫**把判决点放到了每一次访问上。这是本文 §4 全部论证的地基。
- **`revocable` 只有表示**。这是本文要兑现的空格。

### 2.2 缺口：权能今天停在"空间的谓词"上

今天的 `capability blk : disk[0..63]` 是一张**编译期常量表**。
`guard blk(i)` 判的是 `i ∈ [0,63]`，**不判 `i` 背后的块还是不是当初那块**。

于是下面这段今天会静默通过：

```rust
capability blk : disk[0..63]

fn stale(i: u64) {
    guard blk(i);          // 第一次：块 i 属于你
    // ... 块 i 被释放、重新分配给另一个对象 ...
    guard blk(i);          // 第二次：仍然通过——域没变，对象变了
}
```

`guard@k` 抓不到它，因为 `k` 钉的是**域实例**；对象身份不在其中。
`generational(N)` 也抓不到它，因为那条律翻的是**域极性**；对象身份不在其中。

一句话：

> **今天的权能命名的是一个"位置"；它不知道"指称物"这层身份。**
> 位置在时间里可以换主人；权能不知道这件事。

### 2.3 三个"代"必须分开

仓里已经有"代域"这个词（`generational`）。本文要再引入两个"代"，**三者不是一回事**，
混在一起会让实现撞车：

| 名称 | 谁在变 | 变的那一刻 | 今天 |
|---|---|---|---|
| **域代**（domain generation） | 域的极性/实例 | 时律续期、`tick` | ✅ `generational` / `guard@k` |
| **对象代**（object generation） | 指称物的生命 | `alloc` / `free` / 重置 | ❌ |
| **权能代**（authority epoch） | 授权链的有效性 | `revoke` / 委托撤销 | ❌ |

它们各自的失效条件也不同：

```
域代失效   ⇒ 旧守卫抛「陈旧实例」          （docs/306 §4.3）
对象代失效 ⇒ 旧引用抛「对象已退休」        （本文）
权能代失效 ⇒ 旧权能抛「授权已撤销」        （本文）
```

最终态的判决要同时过三把钥匙（§3.4）。

---

## 3. 定义：时序安全的五条性质

### 3.1 权能五元组

`docs/146` 的定义是四元组 `D = (space, lo, hi, revocable)`。
`revocable` 是其中唯一的"时间位"，而且只有表示。本文把它展开成：

```
权能 c = (D, o, g, e, r)

D  域      空间 × 形状 × 极性 × 时律（docs/306）
o  对象    指称物的身份（slot id / allocation id / block id）
g  对象代  这个对象是第几条命（单调、不复用）
e  权能代  这个授权是第几次发放（撤销时翻页）
r  权利    读 / 写 / 执行 / 委托（只减不增）
```

`o`、`g`、`e`、`r` 都是新的语法面；它们把今天**只存在于注释里**的假设写进类型：

- `o` 回答"你在说哪一个对象"；
- `g` 回答"是它的第几条命"；
- `e` 回答"这张授权还有效吗"；
- `r` 回答"你能拿它做什么"。

一个权能**有效**，当且仅当：

```
valid(c, x, now) ⟺ D(now, x) ∧ gen(o_x) = g ∧ epoch(c) = e ∧ r ⊇ 所需
```

### 3.2 四个事件

时序安全只由四个事件驱动。把它们写成一张事件表，后面所有不变量都从这里出：

```
birth(o)      : g(o) ← fresh（全局单调，永不复用）
issue(c, o)   : c ← (D, o, g(o), e(issuer), r)
use(c, x)     : valid(c, x, now) ? 放行 + 审计 : trap
retire(o)     : g(o) ← g(o) + 1          // 对象死亡，O(1)
revoke(c)     : e(子树) ← ⊥ / 翻页        // 授权撤销，O(1) 或 O(子树)
quiesce(S)    : S 之前进入的飞行操作全部越过安全点
```

三个关键区分：

1. **`retire` 与 `revoke` 不是一件事。**
   `retire(o)` 让**对象**的旧引用全部失效（对象死了）；
   `revoke(c)` 让**授权**的旧副本全部失效（对象可能还活着，但你不再有权利）。
   两者可以独立发生，所以需要两把钥匙。
2. **`retire` 是 O(1) 的。** 它只做一次代加一，不扫描内存。
3. **`revoke` 可以是 O(1) 的。** 如果被撤销的子树根持有一个 epoch 字，翻页即完成；
   没有 epoch 字时才退化成 C2 级联（O(子树)）。

### 3.3 T0–T5：五条可判性质

| 编号 | 名称 | 陈述 | 判据形态 |
|---|---|---|---|
| **T0** | 单调 | 对象代与权能代只增不减；失效后永不复活；代值不复用 | 事件序列回放 |
| **T1** | 不悬垂 | `retire(o)` 之后，任何携带旧 `g` 的引用在下次 `use` 时 trap | alloc→save→free→use 必须 trap |
| **T2** | 不 ABA | 槽位复用后，旧引用不能指向新对象 | alloc A→free→alloc B 同槽→旧引用 trap |
| **T3** | 撤销完备 | `revoke(c)` 返回后，`c` 的传递派生全部 trap | 委托→撤销根→用孙权能必须 trap |
| **T4** | 撤销静默 | `revoke(c)` 返回后，没有在飞操作还能碰对象 | 线程 A 在飞→线程 B 撤销→A 下一次访问 trap |
| **T5** | 可复核 | 每次 trap 点名 `(space, o, g, e, 事件)`；LSPE 有证据/义务 | 诊断字段 + 台账条目 |

T0 是所有性质的地基：没有单调性，T1–T4 都可以被"代回绕到旧值"绕过。
T5 是 `docs/253` 那条"标注的地板"在时间轴上的兑现：
**每一个失败都必须说清是哪一把钥匙失效**；一句 "invalid capability" 不够。

### 3.4 判决点：三把钥匙，一次比较

最终态的 `guard` 是**一次判决、三把钥匙**：

```
guard c(r):          # r 是携带 (o, g) 的引用
  ① 空间：r.idx ∈ D(now)            # docs/146/306 已有的
  ② 对象：gen(o(r.idx)) = r.g       # 本文新增
  ③ 授权：epoch(c) = r.e            # 本文新增
  三真 → 放行 + 审计 + 证据
  任一假 → trap + 点名失效钥匙
```

三把钥匙的顺序可以按成本排：空间（可能编译期折掉）→ 对象代（一次 load + 比较）
→ 权能代（一次 load + 比较）。常量形状仍然在编译期折掉；动态形状付两次比较。

**失败即无副作用**（`docs/146` S3、`docs/307` §7）在这里继续成立：
任一钥匙失败都只 trap，不改写任何域状态。

---

## 4. 核心论证：为什么 Loment 不需要 sweep

这是本文与 CHERI/Cornucopia 分道的地方，也是唯一一个"看起来不可能"的断言：

> **在"每次访问都过判决点"的前提下，撤销不需要扫描内存。**

### 4.1 扫描式撤销 vs 判决式撤销

| | 扫描式（CHERI/Cornucopia） | 判决式（本文） |
|---|---|---|
| 失效在哪发生 | 撤销服务**遍历内存**，找到旧能力并清掉 | **下一次 use** 自己比较代 |
| 需要枚举根集吗 | 需要：必须知道能力可能藏在哪 | **不需要**：每个副本自己带证 |
| 撤销的复杂度 | O(内存) 周期扫描 | O(1) 代加一 / epoch 翻页 |
| 失败延迟 | 扫描到之前，旧能力仍可用 | 下一次 use 立即失败 |
| 前提 | 能力里没有代 | **每次访问有一次判决** |

第三行是本文的枢纽：

> **扫描式撤销问"副本在哪"；判决式撤销问"副本带了证吗"。**
> 前者是**发现**问题，后者是**验证**问题。验证比发现便宜，因为它不需要知道全集。

这就是为什么 Loment 的 `ref` 必须**携带代**：一个丢掉代的副本（`u64`、裸指针）
就退化成"没有证"，判决点救不了它。§8 会把这类副本列成地板。

### 4.2 定理（时序安全，受守卫子集内）

**设**：

- **A1（全判决）** 对对象 `o` 的每一次访问都经过 `guard`，且实参是携带 `(o, g)` 的引用；
- **A2（单调 + 墓碑）** 对象的代 `g(o)` 单调不减，且一旦离开某个值就不再回来；
- **A3（安全点）** 每一次 `retire(o)` / `revoke(c)` 都发生在安全点；
- **A4（无丢失副本）** 受守卫子集内不存在绕过 `guard` 的、丢失了代的引用副本。

**则** T0–T4 成立，T5 由判决点的审计/证据给出。

**证明思路**（对事件序列归纳）：

- **T1**：`retire(o)` 后 `g(o)` 改变；任何携带旧 `g` 的引用在下次 `use` 时第②钥失败；
  A1 保证没有不经 `guard` 的访问；A2 保证旧 `g` 不会回来。
- **T2**：同上；A2 的"不复用"直接排除 ABA。
- **T3**：`revoke(c)` 后子树 `epoch` 改变或墓碑化；每个派生副本的第③钥失败；
  C2 级联覆盖传递闭包。
- **T4**：A3 把撤销放在安全点；撤销点之前已经通过判决的操作，要么在安全点前完成，
  要么在下一个安全点被拒（fail-closed）。没有第三种状态。
- **T0**：A2 与 epoch 的单调性直接给出。
- **T5**：判决点把 `(space, o, g, e, 事件)` 写进审计/证据（`docs/262`）。

∎

### 4.3 三条推论

**推论 1（无需扫描）**：在 A1–A4 下，撤销不需要遍历内存。
对象退休是 O(1) 的代加一；权能撤销是 O(1) 的 epoch 翻页，
只有在没有 epoch 字时才退化成 O(子树) 的级联。

**推论 2（成本）**：每个槽/对象一个代字；每次访问一次比较（或两次：对象代 + 权能代）。
没有隔离区、没有 stop-the-world、没有写屏障。

**推论 3（相对完整性）**：完整性相对于**根集**。
A4 之外的副本（FFI 存下的裸指针、`ptr as u64`、被 `memcpy` 截断的值）
不受 T1–T4 保护。它们要么被扫描覆盖，要么被契约声明为**地板**（§8）。

这三条合起来就是本文的"最终态"：
**确定性、非概率、无扫描的时序安全，代价是一次判决 + 每个对象一个字。**

### 4.4 什么时候仍然需要 sweep

判决式撤销不是万能的。下面三种情况必须回到扫描或契约：

1. **副本丢了代**：`ptr as u64`、`memcpy` 成别的类型、FFI 存下裸值。
   这些副本无法参与第②钥比较，只能由边界契约（"这个 FFI 不留存"）或一次扫描覆盖。
2. **对象不是 Loment 管的**：内核对象、外部设备、共享内存。
   它们没有 Loment 的 `gen` 字，只能借用 seL4/Genode 式的对象销毁语义。
3. **物理攻击**：Rowhammer 改掉代字。这是 Fil-C 已经在 2026 年踩到的坑，
   本文明确把它排除在威胁模型外（§8）。

所以正确的说法是：

> **Loment 在"受守卫子集"内不需要 sweep；在子集外，sweep 或契约是必须的。**
> 把这条边界画出来，比声称"我们不需要 GC"诚实得多。

---

## 5. 最终态架构

### 5.1 三层：域、代、静默

```
┌─────────────────────────────────────────────────────────┐
│ 层 C  静默   safety point / epoch / FFI barrier          │
│              → 撤销什么时候"算数"                         │
├─────────────────────────────────────────────────────────┤
│ 层 B  代     object gen / authority epoch                │
│              → 对象和授权现在是什么身份                   │
├─────────────────────────────────────────────────────────┤
│ 层 A  域     space / shape / polarity / law              │
│              → 这个位置让不让你碰（docs/146/306）         │
└─────────────────────────────────────────────────────────┘
```

三层各自回答一个问题，合起来才是时序安全：

- **层 A** 回答"在不在域里"（空间）；
- **层 B** 回答"还是不是那个对象/那张授权"（时间）；
- **层 C** 回答"撤销从哪一刻起算数"（顺序）。

这三层已经有一层半在仓里：层 A 完整，层 B 的域代有了、对象代没有，层 C 有单线程版本
（`tick` / 回边），多线程版本没有。

### 5.2 引用三态：`&T` / `ref T` / `ptr`

最终态只有三种引用，每种对应一种检查时机。这是本文最想写死的一张表：

| 形态 | 例子 | 有效性由谁证 | 检查时机 | 失效后的行为 |
|---|---|---|---|---|
| **静态借用** | `&T` / `&mut T` | 区域 / 生命周期（`docs/299`、`docs/317`） | 编译期 | 编译错误 |
| **运行时权能** | `ref T` | 域 + 对象代 + 权能代 | 每次访问 | trap |
| **裸引用** | `ptr` | 无（标注的地板） | 不检查 | UB 或外部契约 |

一句话：

> **`&T` 是编译期的 `ref`，`ref` 是运行期的 `&T`。**
> 两者是同一个"带有效期的引用"在两个检查时刻上的形态；`ptr` 是明确放弃检查的那一态。

这条对称性让 Loment 不需要再发明第四种引用：

- 能在编译期证明独占与词法寿命的，用 `&T`，零成本；
- 不能的（动态图、跨调用、堆对象），用 `ref`，一次判决；
- 两者都不适用的（FFI、裸内存），用 `ptr`，并**标注**它是地板。

### 5.3 对象表与槽表

`o` 与 `g` 存在哪，按指称物种类分三种。它们共用同一条判决规则，只是载体不同：

| 指称物 | 身份 `o` | 代 `g` 存哪 | 谁翻代 |
|---|---|---|---|
| 有界槽（disk block、proc slot、数组槽） | 槽号 | 槽表 `gen[slot]`（每槽一个字） | `retire(slot)` |
| 堆对象 | 分配 id | 对象头 `header.gen` | `free` / `reclaim` |
| 栈对象 | 帧 + 槽 | 编译期区域（`&T` 已覆盖） | 作用域退出（静态） |
| 外部对象 | 外部句柄 | 外部系统的代 / 契约 | 外部系统 |

有界槽用槽表，因为槽数有限（`docs/306` 已经把有状态域限制在 ≤64 宽，可以扩展）；
无界对象用对象头，避免一张无界的表；栈对象不需要运行时代，静态借用已经证完。

**为什么不用一张全局句柄表？** 因为句柄表要一次间接寻址，而 Loment 的 `ref` 可以直接
是 `(idx, gen)` 两个机器字。对于有界槽，`(idx, gen)` 比"句柄 → 表项 → idx"少一次加载。
对于无界/外部对象，句柄表仍然是正确的退路（Fuchsia 的形状）。

### 5.4 与 LGC 的分工

LGC 管"什么时候归还"，本文管"归还之后还能不能用"。两者是同一张图的两个方向
（`docs/210` §6.2、`docs/253` §0 已经写过这句话）：

| | LGC | 本文 |
|---|---|---|
| 问题 | 这块内存什么时候归还 | 归还后旧引用还能不能碰 |
| 机制 | 梯子 / 标记 / 分代 | 对象代 + 判决 |
| 失效点 | `free` / 收集 | `retire` / 下一次 `use` |
| 需要扫描吗 | 需要（tracing） | 不需要（判决） |

两者可以独立开关：

- 有 LGC 无本文：内存会回收，但旧引用悬垂（今天的状态）；
- 有本文无 LGC：旧引用安全，但内存不回收（可以接受，`gc_no` 档）；
- 两者都有：完整的内存生命周期管理。

这是本文能"先于 GC 落地"的原因：**它不依赖回收器，只依赖判决点**。

### 5.5 与 LSPE 的分工

`docs/262` 的 LSPE 已经把"证据 / 义务 / 地板"写成了台账。本文的 T0–T5 直接接进去：

| 本文 | LSPE 里的形状 |
|---|---|
| T1 不悬垂 | 义务 `Alive(o)`；证据 `Guard(c, o, g)`；违反 = 问题 |
| T2 不 ABA | 义务 `GenMonotone(o)`；证据 `Retire(o, g→g+1)` |
| T3 撤销完备 | 义务 `RevokedSubtree(c)`；证据 `Revoke(c, e→e+1)` |
| T4 撤销静默 | 义务 `Quiescent(S)`；证据 `Safepoint(S)` |
| T5 可复核 | 每个 trap 一条问题 + 一个来源 |

`docs/262` §5 里那条"`revocable` 能力撤销后不再访问"的假设，在本文落地后可以从
`Assumed` 升成 `Checked`（单进程、受守卫子集内）。这是本文对 LSPE 的直接兑现。

### 5.6 语法草案

> 仅为形状，不承诺这些拼写会进语言。展示用 `rust` 围栏，理由见 `CLAUDE.md`。

```rust
// 一、有界槽：ref 携带 (idx, gen)
capability blk : disk[0..63]

fn take(i: u64) -> ref [u8; 512] in disk {
    let r: ref [u8; 512] in disk = alloc_block(blk, i);  // r.g = gen(i)
    return r;
}

fn use_it(r: ref [u8; 512] in disk) {
    guard blk(r);        // ① 域 ② gen(i) == r.g ③ epoch
    r[0] = 1;
}

// 二、对象死亡：O(1) 翻代
fn drop_it(r: ref [u8; 512] in disk) {
    free(r);             // gen(i) += 1；r 之后不能再用
}

// 三、授权撤销：epoch 翻页
fn delegate(r: ref [u8; 512] in disk) -> cap {
    let c: cap = grant(blk, r, read);   // 子权能，记 parent
    return c;
}

fn withdraw(c: cap) {
    revoke(c);           // 子树 epoch 翻页；所有派生副本下一次 use 失败
}
```

两个刻意的设计：

1. **`guard blk(i)` 仍然合法**，但它只过第①钥（空间）。
   这是兼容旧代码的逃生舱，也是**不完整**的那一档——编译器应当要求显式标注
   （例如 `guard blk(i) unchecked_gen;`），否则默认按 `ref` 走。
2. **`guard blk(r)` 的实参是引用；整数只过第①钥。** 这把"代"带进了判决点，
   同时解决了 `docs/314` §8 那条"空间标签不过调用边界"的诚实边界：
   `ref` 过调用边界时，代跟着走。

---

## 6. 判据（每条先红）

| 判据 | 喂什么 | 期望 |
|---|---|---|
| 悬垂 | `alloc r; free r; guard` | trap，点名"对象已退休" |
| ABA | `alloc A; free A; alloc B 同槽; 用旧 r` | trap，点名代不匹配 |
| double free | 同一引用 `free` 两次 | 第二次 trap |
| 撤销级联 | 父→子→孙，撤销父，用孙 | trap，审计条目数 = 子树 guard 次数 |
| 撤销完备 | 同一权能复制到两个结构体，撤销源 | 两个副本都 trap |
| 撤销静默 | 线程 A 在飞，线程 B 撤销 | A 的下一次访问 trap；撤销返回后无访问 |
| 静态/动态一致 | 同一份源，常量折掉 vs 运行期判 | 判决结果逐字节相同 |
| 双实现 | 同一份源 | `tools/lomentc.py` 与 `selfhost` 的 IR/Potato 逐字节一致 |
| 不丢静态 | 常量字面量越界 | 仍是编译错误，不退化成运行期 trap（`docs/306` §9 那条注意） |
| 成本（确定性量） | 同一段程序 | IR 里只多一次 load + 一次比较，不用墙钟比值 |
| 地板标注 | `ptr` 在 `free` 后使用 | 判据期望**地板标记**；出现 trap 反而说明边界被误判 |
| 无扫描 | 撤销一个有 10^6 个槽的域 | 撤销的指令数不随槽数增长（O(1)） |

**第一条红判据**必须是"悬垂"：在未加对象代的实现上，`alloc→free→guard` 必须静默通过。
它先红，才能证明后面的修复真的做了什么。

**成本判据**按 `docs/feedback_no_wallclock_ratio_criteria` 的教训，用 IR 里的确定性量
（load/compare 的次数），不用墙钟比值。

---

## 7. 落地阶梯与 L0 影响

| 级 | 内容 | 改哪里 | 需要 L0 契约变更？ |
|---|---|---|---|
| **S0** | 定义 `ref` / `gen` / `epoch` 与 T0–T5 判据 | 本文 + 判据 | 否 |
| **S1** | 有界槽的槽表 + `guard3`（对象代） | `lomentc.py` + selfhost + 运行时块 | 否 |
| **S2** | 堆对象头 + `free` 翻代 | `tools/lomentc.py`、`lomelf.py`、LGC | 否（运行时内部） |
| **S3** | 权能代 + C2 级联 + 墓碑 | L1 表 + 检查器 | 否（单进程） |
| **S4** | 静默：单线程安全点；线程 EBR；FFI barrier | `docs/246` 安全点、线程线 | 线程那半可能要 L0 |
| **S5** | 内核强制的撤销/代（跨进程） | `dom_revoke` / `dom_info` / `dom_bind` | **是**，单独一次提交 + 知会 compat 线 |

关键结论与 `docs/306`、`docs/307` 一致：**S1–S3 可以全在语言侧落地，不动 `lom/*.lom`。**
`dom_revoke`（0x8109）今天只是表示；S3 先在 L1 里兑现"翻 epoch"，S5 才把语义推给内核。

S5 是对外契约变更，按 `CLAUDE.md` 第一节的规矩：单独一次提交，消息写清
"谁依赖它、变了什么、下游要不要动"，并在 `docs/` 留一行下游影响。

---

## 8. 不可能性与诚实边界

### 8.1 先写死四条不可能

1. **I1：任意裸指针的零成本完整时序安全不可能。**
   元数据必须在某处（指针、表、影子内存、硬件 tag），而检查必须在某时
   （每次访问、周期扫描、编译期）。没有免费的那一格。
2. **I2：完整性必须相对于一个根集。**
   任何声称"完整"的系统都要回答"副本可能藏在哪"。寄存器、栈、堆、FFI、内核、网络——
   根集越大，完整性越贵。把根集写出来，是诚实的最低要求。
3. **I3：非概率 + 无扫描 + 无逐访问检查，三者不可兼得。**
   MTE 选了概率，CHERI/Cornucopia 选了扫描，Loment 选了逐访问检查。
   第四条路（免费地知道一切）不存在。
4. **I4：跨任意 FFI 的静态时序安全不可能。**
   外部代码可以把引用存进任何地方。没有契约（"这个函数不留存"）或 ABI 改造，
   静态证明在边界上必然断。

### 8.2 本文的地板

- **`ptr` / `syscall*` / `load8` / `store8` / FFI 绕过 `guard`**（`LLS` §21.10 已经写明）。
  T1–T4 只在**受守卫子集**内成立；子集外是**标注的地板**。
- **`revocable` 今天只有表示**（`docs/146` §5）。本文把它变成代数，但要到 S3 才实装。
- **没有机器检查的证明**。本文的"定理"是"实现级断言 + 判据"，与 `docs/146` §4 同一档。
- **并发静默可以阻塞**。EBR 里一个被暂停的线程会让回收无限延后——这破坏的是**活性**，
  不破坏**安全**（旧引用仍然 trap）。
- **物理攻击不在威胁模型内**。Rowhammer 改代字，超出本文范围（Fil-C 2026 年已经踩过）。
- **无界域的每槽代不可行**。`2^64` 个槽不可能每个一个代字；无界/动态对象必须走对象头
  或句柄表，这是 §5.3 的分工。
- **`memcpy` 式复制可能丢代**。类型系统必须禁止把 `ref` 当裸字节复制；
  否则它退化成地板。

### 8.3 一句话

> **本文证的范围是"受守卫子集内的确定性时序安全"；子集之外不在定理内。**
> 边界之外的东西，要么有契约，要么有扫描，要么被标注成地板。
> 把这条线画出来，比声称"Loment 没有 UAF"有用。

---

## 9. 开放问题

1. **`ref` 的拼写**：`ref T` 是类型构造器，还是 `let r: [T] in disk` 的扩展？
   现有 `docs/314` 的空间标签能不能直接升级成 `ref`？
2. **代的宽度**：32 位还是 64 位？32 位要定义回绕策略（回绕即墓碑？），
   64 位在物理上不会回绕，但每个槽多 4 字节。
3. **对象身份怎么给**：有界槽用槽号，堆对象用分配 id，外部对象用外部句柄——
   三者的 `o` 要不要统一成一种 `ObjId`，还是各层各管？
4. **两把钥匙能不能压成一个字**：`(g, e)` 打包成 64 位（各 32 位）可以少一次比较，
   但两个都回绕时会互相污染。要不要接受？
5. **FFI 契约的语法**：`borrows` / `retains` 标注？`docs/246` §5.3 的 syscall 白名单
   是第一个先例，能不能推广成一张可审的表？
6. **fail-closed 还是 quiesce-first**：撤销后立即 trap 所有旧引用（本文选的），
   还是先等在飞操作完成再 trap？前者安全、后者少一次重试。
7. **LSPE 怎么记 T1–T5**：每条是一条义务还是四条？`docs/262` §5 的
   `CapabilityLive` 要不要拆成 `ObjectAlive` / `AuthorityLive`？
8. **`revocable` 要不要升成一条时律**：今天的 `revocable` 是声明标志；
   如果撤销变成 `revocable` 律（epoch），它就和 18 种时律同构了。
9. **跨进程 / IPC**：权能能不能穿过 `sendmsg`？Fuchsia 用句柄转移，
   seL4 用 CDT 跨 CSpace。Loment 第一阶段建议**不穿**，只在本进程内保证。
10. **与 `generational` / `consumed` 的交互**：一个域同时有代律与对象代时，
    两把钥匙都要过；`consumed` 的线性性是不是已经隐含了"用一次就死"的时序安全？

---

## 10. 先例索引

> 只列本文直接引用的条目。标题、作者、年份按检索到的原文；链接给可读的版本。

**能力与撤销**

1. D. D. Redell, *Naming and Protection in Extendable Operating Systems*, PhD thesis,
   UC Berkeley, 1974. 能力撤销的 revoker/间接层与"依赖撤销"的源头。
   <https://caltss.computerhistory.org/archive/7409-thesis-ddr.pdf>
2. V. D. Gligor, *Review and Revocation of Access Privileges Distributed Through Capabilities*,
   IEEE TSE SE-5(6):575–586, 1979. 撤销的分类学基础。
3. R. N. M. Watson et al., *Capability Hardware Enhanced RISC Instructions (CHERI)*.
   CHERI 的 tag/bounds/otype 能力模型。
4. H. Xia et al., *CHERIvoke: Characterising Pointer Revocation Using CHERI Capabilities
   for Temporal Memory Safety*, MICRO 2019.
   <https://dl.acm.org/doi/10.1145/3352460.3358288>
5. N. W. Filardo et al., *Cornucopia: Temporal Safety for CHERI Heaps*, IEEE S&P 2020.
   <https://www.cl.cam.ac.uk/research/security/ctsrd/pdfs/2020oakland-cornucopia.pdf>
6. N. W. Filardo et al., *Cornucopia Reloaded: Load Barriers for CHERI Heap Temporal Safety*,
   ASPLOS 2024. <https://dl.acm.org/doi/10.1145/3620665.3640416>
7. *A CHERI C Memory Model for Verified Temporal Safety*, CPP 2025.
   <https://dl.acm.org/doi/10.1145/3703595.3705878>
8. F. Pizlo, *Fil-C: How It Works* / *InvisiCaps by Example*.
   <https://fil-c.org/how>
   <https://github.com/pizlonator/llvm-project-deluge/blob/deluge/invisicaps_by_example.md>
9. *A Rowhammer Attack on Fil-C's Software Capabilities*, arXiv 2026.
   <https://export.arxiv.org/pdf/2610.33748>
10. *PICASSO: Colored Capabilities*（USENIX Security 2026）——
    CHERI 上"不隔离、用 provenance-validity table 批量撤回"的最新方向。
    <https://www.usenix.org/system/files/usenixsecurity26-guelmez.pdf>

**内核对象与句柄**

11. seL4 Reference Manual，CNode / `seL4_CNode_Delete` / `seL4_CNode_Revoke` /
    finalise 语义。形式化规格见 `l4v` 的 `CSpace_A.thy`。
    <https://github.com/seL4/l4v>
12. J. S. Shapiro et al., EROS / KeyKOS 的能力与撤销设计；
    revoker capability、forwarding object。
    <https://www.cse.psu.edu/~trj1/cse543-f16/docs/capability-systems.pdf>
13. Genode 手册与邮件列表：会话对象销毁即撤销。
    <https://lists.genode.org/pipermail/users/2015-April/003252.html>
14. Fuchsia/Zircon 句柄与权利：句柄表、generation count、KOID 不复用、权利只减。
    <https://fuchsia.dev/fuchsia-src/concepts/kernel/handles>
    <https://fuchsia.googlesource.com/fuchsia/+/refs/heads/main/docs/concepts/kernel/rights.md>

**类型、区域与回收协议**

15. M. Tofte, J.-P. Talpin, *Region-Based Memory Management*,
    Information and Computation 132(2), 1997；区域推断与 ML Kit。
16. P. Wadler, *Linear Types Can Change the World!*, 1990；线性/仿射类型。
17. Rust NLL / Polonius：借用图与区域；本仓的落地见 `docs/317`。
18. M. Michael, *Hazard Pointers*, 2004；K. Fraser, *Epoch-Based Reclamation*, 2004；
    T. Brown, *DEBRA+*, PODC 2015.
    <https://dl.acm.org/doi/abs/10.1145/2767386.2767436>
19. ARM Memory Tagging Extension：4 位 tag、16 字节 granule、概率性；
    Microsoft, *Security Analysis of Memory Tagging*, 2020.
    <https://raw.githubusercontent.com/microsoft/MSRC-Security-Research/master/papers/2020/Security%20analysis%20of%20memory%20tagging.pdf>
20. SoftBound / CETS：编译期指针边界 + 运行期时序检查，约 2× 成本。

---

## 11. 一句话收口

`docs/146` 给了空间的谓词，`docs/306`–`docs/315` 给了时间与结构的形状；
本文补上最后一块——**指称物在时间里的身份**。

最终态的 Loment 里，每一个引用只可能是三种之一：

```
&T      编译期证明的借用     零成本，越界即编译错误
ref T   运行期判决的权能     一次比较，失效即 trap
ptr     标注的地板           不检查，责任在写的人
```

每一个权能只可能带三把钥匙：

```
域     在不在域里        docs/146 / 306
对象代 还是不是那个对象   本文
权能代 这张授权还有效吗   本文
```

同一次 `guard`，回答三个问题。
**这就是"权能的时序安全"在 Loment 里的最终形态。**

---

## 12. S1 落地记录（2026-11-09）

S1 落地的是"对象代"的最小面：**显式 `(idx, gen)`**，还没有 `ref` 类型。

### 12.1 语法

```rust
capability blk : disk[0..7] revision      // 新的第九种时律：对象代

let g: u64 = generation blk(i);           // 读当前代（u64）
guard blk(i, g);                          // 域 ∧ 代 == g（第二实参是期望的代）
retire blk(i);                            // 代 +1（O(1)）
```

`revision` 的约束与 `consumed` / `reachable` 同档：**必须有界、域宽 ≤64、只用于顶层
`capability`**。块级域的寿命已经由块本身给出，不需要第二张代。
`generation` / `retire` 用在非 `revision` 域上是编译错误；`revision` 的 `guard`
不带第二实参也是编译错误。

### 12.2 运行期

| 层 | 形状 |
|---|---|
| IR | `@__loment_gen: [16 x [64 x i64]]`；`@__loment_guard2` 的 `kind == 15`；`@__loment_gen_read` / `@__loment_gen_bump` |
| Rust | `__LOMENT_GEN: [[u64; 64]; 16]`；同一个 `__loment_guard2` 的 `15 =>` 分支；同两个函数 |
| 判决 | `kind 15`：`bound && gen[idx-lo] == aux`；失败 trap，放行才记审计 |
| 翻代 | `retire`：`gen[idx-lo] += 1`；越界 trap，不静默回绕 |

### 12.3 判据

- `lomentc_test::test_s1_revision_generation_catches_stale_reference`：Rust 后端**真跑**——
  `g=0` 放行；`retire` 后旧 `g` trap；新代 `g=1` 放行。IR 侧钉住 gen 表与两个运行时符号。
- `lomentc_test::test_s1_revision_requires_the_right_domain_kind`：非 `revision` 域上的
  `generation` / `retire`、缺第二实参的 `guard`、域宽 >64，各一条编译错误。
- 自举侧：`loment/selfhost/pos/revision.lomt`（正例）、
  `neg/revision_regular.lomt`、`neg/revision_no_gen.lomt`（负例）。
- 双实现对拍：同一份 `revision` 程序，参考实现与自举 `codegen` 的 IR **逐字节相同**
  （实测 11,656 字节）。

### 12.4 没做的

- `ref` 类型（引用携带代的那一层）仍是 S1.5；
- 撤销代（`e`）与 C2 级联仍是 S3；
- 静默（线程 EBR / FFI barrier）仍是 S4；
- 无界域与块级域的 `revision` 不在 S1；
- `lom/*.lom` 一行未动。
