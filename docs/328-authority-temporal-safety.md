# 328 · 授权时序安全：把"授权的寿命"做成语言的一等公民

> 状态: **设计稿**（2026-11-10）
> 上游: `docs/320`（权能时序安全的最终态）· `docs/306`（域代数）· `docs/307`（语境类）·
> `docs/308`（嵌套/循环）· `docs/314`（块域）· `docs/315`（域循环）· `docs/253`（LMS）·
> `docs/262`（LSPE）· `docs/299`（生命周期）· `docs/206`（所有权边界）· `docs/210`（LGC）·
> `docs/207`（逆回收）· `#1535`（S1 对象代）· `#1537`（C2 撤销）
> 一句话: **Rust 让"内存的寿命"成为类型系统的一等公民；本文要让"授权的寿命"成为语言的
> 一等公民——同一个判决点、跨异构指称物、可审计、带可数的地板。**

---

## 0. 先回答"创新在哪里"

这个问题必须答在前面，否则后面全是自我说服。

Rust 的借用/生命周期系统是一组**老件**的集成创新。拆开看是六件：

| Rust 的公式 | 具体是什么 | 老件还是新件 |
|---|---|---|
| 老件 | 线性/仿射类型、区域推断、别名类型、Cyclone 区域 | 1990–2006 的研究 |
| 一个默认机制 | ownership + borrow，编译期零成本 | 集成创新 |
| 一个算法贡献 | NLL（非词法生命周期） | 新算法（2017） |
| 一个证明 | RustBelt（POPL 2018） | 新证明 |
| 一个可用性故事 | 生命周期省略、诊断、迁移 | 工程创新 |
| 一个新保证 | safe Rust 无 UAF / 无数据竞争 | 产品级承诺 |

Loment 要做的对应物，六件都在，但**轴要换**：

| Loment 的公式 | 具体是什么 | 状态 |
|---|---|---|
| 老件 | 撤销（Redell/seL4/EROS/Fuchsia）、代（Verum CBGR/PICASSO/Fuchsia）、每次校验、静默（EBR/HP/RCU）、审计（LSPE） | 都有前作 |
| 一个默认机制 | 一个 `guard`：域/时律 + 对象代 + 授权 epoch；`&T` 与 `ref T` | S1 已有代；其余在 S1.5/S3 |
| 一个算法/定理贡献 | 受守卫子集内撤销免扫描 + 跨种类级联 | **待证明** |
| 一个证明 | A0–A5 + no-sweep + root-set 完整性 | **待机器检查** |
| 一个可用性故事 | 零标注的 `domain`/`ref`、诊断点名钥匙、旧程序不改 | **待做** |
| 一个新保证 | guarded Loment 无陈旧授权，跨 disk/proc/auth/heap，地板可数 | **待测量** |

**本文的窄主张**（可以被证伪的那一句）：

> 在受守卫子集内，一个携带 `(域实例, 对象代, 授权 epoch)` 的引用，其每一次使用都经过
> 同一个判决点；因此撤销是 O(1) 的代/epoch 翻页，不需要扫描；子集外的未验证面是一个
> 可数的数，印在产物里。

这句话里没有"发明新原语"。"代 + 每次校验"是 Verum CBGR 和 PICASSO 已经做过的；
"撤销级联"是 seL4 CDT 做过的；"每次访问查表"是 Fuchsia 句柄做过的。**新的是把三者
压进同一个语言级判决点，并把范围从内存扩到异构指称物。** 这是一个系统设计主张，
不是一条新定理；要让它变成定理，得做 §10 的证明和测量。

---

## 1. 前作地图

### 1.1 撤销理论：五十年

| 前作 | 机制 | 对本文的意义 |
|---|---|---|
| Redell 1974 | revoker capability / 间接层；删掉它，所有派生能力失效 | "依赖撤销"的源头；对应本文的 epoch |
| Gligor 1979 | 撤销的分类：全局/选择性、立即/延迟、独立/依赖 | 本文选"选择性 + 立即判决 + 延迟回收 + 依赖级联" |
| seL4 | CDT/MDB 上的 `Revoke`：删后代；最后一个能力删掉时销毁对象 | C2 级联的成熟形态；#1537 已落 L1 版 |
| EROS / KeyKOS | revoker capability、forwarding object；"没有可撤销的能力，只有可撤销的对象" | 撤销挂在对象上，不挂在语法上 |
| Genode | 会话对象销毁即撤销；撤销是对象销毁的副作用 | 对象死亡是唯一撤销源 |
| Fuchsia Zircon | 句柄表 + generation + rights；close 即失效；KOID 不复用 | 最接近 S1 的内核形态 |

### 1.2 代际与每次校验：最接近 S1/S1.5 的前作

| 前作 | 机制 | 与本文的关系 |
|---|---|---|
| **Verum CBGR**（Capability-Based Generational References） | 分配头存 generation；引用（ThinRef/FatRef）带签发时的 generation；解引用时比较；epoch 处理回绕 | 与 S1.5 的 `ref` 几乎同构。差别：CBGR 只做内存；本文要做跨种类 |
| **PICASSO**（USENIX Security '26） | capability 里存 provenance id；每次 load/store 查 PVT 有效位；free 即失效；无 quarantine；内存可立即复用 | 与 `@__loment_gen` + `retire` 同族：每次访问查表，撤销 O(1) |
| Fuchsia 句柄 | 句柄表项带 generation；每次 syscall 解析；close 即失效 | 顶层 `capability` + `revision` 的内核版 |
| 代际索引 / slotmap | index + generation；free 时 +1 | 游戏/系统编程里的通用 ABA 防护 |
| Intel US11163569 | `GenCap` / `CheckCap` 指令；指针 tag 里的 temporal-safety 位 | 硬件版"引用带代 + 每次校验" |

**结论：S1 的机制没有新意。** 它是"每槽一个代 + 显式 `(idx, g)` + 每次 `guard` 比较"，
在 Verum CBGR/PICASSO/Fuchsia 里都能找到对应。S1 是干净实现，不是发明。

### 1.3 扫描、概率、原子失效

| 前作 | 机制 | 代价 |
|---|---|---|
| CHERIvoke（MICRO 2019） | 隔离 + 周期扫描，清掉悬垂能力 | <5% 平均，25% 堆开销 |
| Cornucopia（S&P 2020） | VM 跟踪能力流 + 内核撤销服务 | <2% 平均，8.9% 最坏，stop-the-world |
| Cornucopia Reloaded（ASPLOS 2024） | 每页 capability load barrier | 摊掉停顿，要硬件支持 |
| Fil-C（2025） | InvisiCaps + FUGC；free 原子失效所有指针 | 不 ABI 兼容；全运行期；Rowhammer 风险 |
| MTE（Armv8.5） | 4 位 tag / 16 字节粒度；概率检测 | ~93.75%；非确定 |

本文的立场：**受守卫子集内不扫描**；子集外（丢代的副本、FFI、裸指针）仍然需要扫描或
契约。这与 CHERI/Cornucopia 的分界在于"代在不在引用里"和"每次访问有没有判决点"。

### 1.4 编译期：借用、区域、线性

| 前作 | 机制 | 本文拿走什么 |
|---|---|---|
| Rust NLL / Polonius / RustBelt | 借用图、区域、健全性证明 | `&T` 的静态半边；证明方法 |
| Stacked Borrows / Tree Borrows | unsafe 的别名模型 | `ref` 的 aliasing model 要借鉴 |
| Cyclone regions | 区域注解 + 安全 C | 区域/借用的工程先例 |
| Tofte–Talpin | 区域推断 + 效果系统 | 编译期寿命推断 |
| 线性/仿射类型 | 恰好一次 / 可丢不可复制 | `consumed`/`witnessed` 的静态半边 |
| Ownership types / alias types | 别名约束、所有权 | `domain` 的包含关系 |

### 1.5 静默

hazard pointers（Michael 2004）、EBR（Fraser）、RCU：对象死了但线程还在用。本文的
安全点是 quiescent state；线程/FFI 的 S4 直接借这套。

### 1.6 结论：空位在哪

把上面所有行按"指称物种类"和"是否语言级默认机制"排一张表，会发现：

- 内核/硬件前作通常只覆盖**一种**指称物（内存、内核对象、文件描述符）；
- 编译期前作只覆盖**内存/资源**，且没有运行期撤销；
- 审计前作（LSPE/Potato）只在 Loment 里出现。

**空位 = 跨种类 + 语言级默认机制 + 产物里的可数地板。** 本文只主张这一格；
单看"代 + 每次校验"，前作已经做完了。

---

## 2. 轴：内存寿命 vs 授权寿命

### 2.1 两个不同的寿命

```
对象寿命（memory lifetime）
  birth(o) → use(o) → retire(o)
  问：这块存储还在不在？

授权寿命（authority lifetime）
  grant(c, p, o, r) → use(c) → revoke(c) / expire(c)
  问：这个主体对这只对象的这项权利，现在还有没有效？
```

两者正交：

| 场景 | 内存安全 | 授权安全 |
|---|---|---|
| 对象没释放，但 fd 被内核关闭 | ✅ | ❌ |
| 对象已释放，但旧指针仍可解引用 | ❌ | ✅（如果授权本来就已经撤销） |
| 对象活着，cap 被父域撤销 | ✅ | ❌ |
| 对象死了，cap 仍指向同一槽位的新对象 | ✅（内存可能复用） | ❌（ABA） |

Rust 的 ownership/borrow 主要解决第一列。本文要解决第二列。

### 2.2 单位变了

Rust 的单位是"对象/引用"。本文的单位是：

```
(principal, object, right, interval)

principal  谁在问（主体域，docs/307）
object     对哪只对象（对象身份 o）
right      能做什么（读/写/执行/委托）
interval   什么时候有效（域时律 + 对象代 + 授权 epoch）
```

`free` 的对偶是 `revoke`；`ownership/borrow` 的对偶是 `domain / generation / epoch`。
这条轴上，前作都是内核或硬件局部；没有语言级的默认机制。

---

## 3. 模型

### 3.1 权能五元组

沿用 `docs/320`：

```
权能 c = (D, o, g, e, r)

D  域      空间 × 形状 × 极性 × 时律（docs/306）
o  对象    指称物的身份（槽号 / 分配 id / block id）
g  对象代  这个对象是第几条命（单调、不复用）
e  权能代  这个授权是第几次发放（撤销时翻页）
r  权利    读 / 写 / 执行 / 委托（只减不增）
```

### 3.2 三把钥匙，一个判决点

```
guard c(x):
  ① 域     x ∈ D(now)                 # 空间 + 时律（docs/306/307/308）
  ② 对象   gen(o_x) = g               # 还是不是当初那个对象
  ③ 授权   epoch(c) = e ∧ r ⊇ 所需     # 这张授权还有效吗
  三真 → 放行 + 审计 + 证据
  任一假 → trap + 点名失效钥匙
```

失败必须点名是哪把钥匙：`domain-expired` / `object-retired` / `authority-revoked` /
`rights-insufficient`。一句 "invalid capability" 不够（`docs/253` §1.2 的"标注的地板"）。

### 3.3 事件

```
grant(c, p, o, r)   签发：g = gen(o), e = epoch(issuer)
use(c)              判决：三把钥匙
revoke(c)           epoch(子树) ← ⊥ / 翻页；C2 级联（docs/306 §6.1）
retire(o)           gen(o) ← gen(o) + 1；O(1)
expire(c)           域的时律到期；旧判决定点失败
quiesce(S)          安全点：在飞操作越过判决点
```

`retire` 与 `revoke` 是两件事：对象死了 vs 授权被收回。对象可以活着但授权被撤；
授权有效但对象已经死了。所以需要两把钥匙，不能合并。

### 3.4 性质 A0–A5

| 编号 | 名称 | 陈述 |
|---|---|---|
| **A0** | 单调 | 对象代与授权 epoch 只增不减；失效后永不复活；代值不复用 |
| **A1** | 无陈旧授权 | `revoke(c)` 之后，`c` 的传递派生全部 trap |
| **A2** | 无 ABA | 槽位复用后，旧 `(o, g)` 不能指向新对象 |
| **A3** | 跨种类完备 | 一次撤销可以同时失效 disk/proc/auth/heap 的派生；级联覆盖传递闭包 |
| **A4** | 撤销静默 | `revoke(c)` 返回后，没有在飞操作还能使用 `c` |
| **A5** | 可审计 | 每次 trap 点名 `(principal, o, g, e, 事件)`；地板可数 |

`docs/320` 的 T0–T5 是这组性质在"内存/对象"上的版本；A3 是本文加的那一条：
撤销的**范围**跨指称物。A1/A2/A4/A5 与 T1–T5 对偶。

### 3.5 为什么是这三把，不是更多

- 域回答"在不在允许的位置/时间"（空间 + 时律）；
- 对象代回答"还是不是当初那只对象"（对象寿命）；
- 授权 epoch 回答"这张授权还有没有效"（授权寿命）。

权利 `r` 是第三把的一部分：epoch 有效但权利不够，仍然拒绝；权利只减不增，防止放大。
再加第四把（比如"主体"）就是 `D` 的语境类参数，不另立一把钥匙。

---

## 4. 受守卫子集定理（免扫描）

### 4.1 假设

- **G1（全判决）** 对对象 `o` 的每一次访问都经过 `guard`，实参是携带 `(o, g, e)` 的引用；
- **G2（单调 + 墓碑）** `gen` 与 `epoch` 单调不减，失效后不复用；
- **G3（安全点）** 每次 `retire`/`revoke` 都发生在安全点；
- **G4（无丢失副本）** 子集内不存在绕过 `guard` 且丢失了 `g`/`e` 的引用副本。

### 4.2 定理

在 G1–G4 下，A0–A4 成立；T5 由判决点的审计/证据给出。

**证明思路**（对事件序列归纳）：

- A0：G2 直接给出。
- A1：`revoke(c)` 后子树 epoch 改变；每个派生副本的下一次 `guard` 第③钥失败；
  C2 级联覆盖传递闭包。
- A2：`retire(o)` 后 `gen(o)` 改变；旧 `(o, g)` 的第②钥失败；G2 排除回绕/复用。
- A3：一次 `revoke` 作用于多个种类的目标（§5.4 的 targets 表）；每个目标的派生
  各自读自己的 epoch，级联是判决的自然结果，不需要跨种类传播指令。
- A4：G3 把撤销放在安全点；撤销点之前通过判决的操作要么完成，要么在下一个安全点
  被拒（fail-closed）。
- A5：判决点写 `(principal, o, g, e, 事件)` 进审计/证据。

∎

### 4.3 推论

1. **免扫描**：受守卫子集内不需要遍历内存；对象退休是 O(1) 代加一，授权撤销是
   O(1) epoch 翻页（或 O(子树) 级联）。
2. **成本**：每个槽/对象一个代字 + 每个授权一个 epoch 字；每次访问一次或两次比较。
   没有隔离区、没有 stop-the-world、没有写屏障。
3. **相对完整性**：完整性相对于**根集**。G4 之外的副本（FFI 存下的裸指针、`ptr as u64`、
   `memcpy` 截断）不受保护；它们是**地板**，必须被标注或契约覆盖。

### 4.4 与前作的关系

Verum CBGR 和 PICASSO 已经证明"代 + 每次校验"在内存上可行。本文的定理**多出的部分**
只有两条：

- **跨种类**：同一个判决点覆盖 disk/proc/auth/heap；
- **相对完整性**：把"根集之外的副本"显式定义成地板，并让产物报出它的规模。

如果这两条站不住，本文就退化成"把 CBGR 再实现一遍"。所以 §10 的证明与测量必须做。

---

## 5. 静态/动态引用对偶

### 5.1 三种引用

| 形态 | 例子 | 有效性由谁证 | 检查时机 | 失效行为 |
|---|---|---|---|---|
| 静态借用 | `&T` / `&mut T` | 区域/生命周期（docs/299） | 编译期 | 编译错误 |
| 运行时权能 | `ref T` | 域 + 对象代 + 授权 epoch | 每次访问 | trap |
| 裸引用 | `ptr` | 无（标注的地板） | 不检查 | UB 或外部契约 |

一句话：`&T` 是编译期的 `ref`，`ref` 是运行期的 `&T`；`ptr` 是放弃检查的那一态。

### 5.2 `ref` 的形状

```rust
// S1 的显式形状（已在 #1535）
capability blk : disk[0..7] revision
let g: u64 = generation blk(i);
guard blk(i, g);
retire blk(i);

// S1.5 的目标形状
let r: ref [u8; 512] in disk = alloc_block(blk, i);  // r = (idx, gen)
guard blk(r);                                         // 自动带 gen
free(r);                                              // gen += 1；r 之后不可用
```

`ref` 是胖值：`(idx, gen)` 两个机器字；跨调用边界时代跟着走。这解决了 `docs/314` §8
那条"空间标签不过调用边界"的诚实边界。

### 5.3 推断与省略（Rust 的可用性教训）

Rust 的借用检查器能普及，靠的是生命周期省略和推断。Loment 的 `ref` 也要有默认路径：

- `alloc_block` 的返回值自动是 `ref`，不需要手写 `g`；
- `guard blk(r)` 自动从 `r` 取 `(o, g)`；
- 函数签名只在"分叉"时需要标记，例如 `fn pick(a: ref T, b: ref T) -> ref a T`；
- 常见 API 零标注；诊断点名"哪把钥匙失效"。

### 5.4 借用 × 撤销（开放问题）

Rust 没有运行期撤销；CHERI 没有借用类型。两者的交叉是 open 的：

```
domain d : disk[0..7] {
    let r: ref [u8; 512] in disk = ...;
    borrow(r) 期间，另一个线程 revoke(d);
    // 借用应该何时失效？编译期拒绝撤销，还是运行期让借用 trap？
}
```

本文的倾向：**借用是静态证明，撤销是运行期事件；两者冲突时，撤销必须让借用在下一次
判决点失败**。这需要一条"借用不跨撤销点"的规则，S3/S4 再定。

### 5.5 FFI 契约

外部代码可以存下裸指针/整数。`ref` 过 FFI 时，只有两种选择：

- 契约声明"这个外部函数不留存"（像 `docs/246` §5.3 的 syscall 白名单）；
- 把这次调用标成地板，产物记一条。

没有第三种。FFI 的契约语法是 §13 的开放问题。

---

## 6. 默认路径与可用性

### 6.1 零标注的常见路径

```rust
fn fill(a: [u32; 64] in disk, i: u32) {
    domain buf : disk[0..63] {
        a[i] = 1;        // 自动 guard buf(i)
    }
}
```

块域已经做到"声明一次，块内自动守卫"（docs/314）。S1.5 要把 `ref` 也接进来：

- `let r = alloc_block(...)` 自动得到 `(idx, gen)`；
- `guard blk(r)` 不需要手写代；
- 失败时诊断写清 `object-retired` / `authority-revoked` / `domain-expired`。

### 6.2 旧程序不改

S1/S1.5 的新语法都是**增量**：没有 `revision` 的旧程序逐字节不变；`guard cap(i)` 仍然
只过第①钥。旧程序的产物不变，这是 `docs/210` 那条"不改现有程序"的纪律。

### 6.3 迁移

- `choose authority_auto`（默认）：`ref` 推断 + 自动守卫；
- `choose authority_manual`：允许显式 `(idx, gen, epoch)`；
- `choose authority_off`：旧行为，只有域检查；
- 三档与 `gc`/`lms`/`lifetime` 正交，登记处按 `docs/274` 的清单走。

### 6.4 工具

LSP 要在 `guard` 失败处报"哪把钥匙、哪个事件"；Potato 要带 `authority` 段；
`loment check --authority-report` 打印地板计数。VS Code 扩展与 LSP 是两个入口，
各要一条判据（`docs/274` / `feedback_consumers_axis`）。

---

## 7. 产物里的安全论证

### 7.1 四样东西

| 产物字段 | 内容 | 谁读 |
|---|---|---|
| `domains` | 顶层能力 + 块域 | 内核 / lompi / 审计 |
| `generations` | 有界槽的代初值/上界 | 运行时 / 审计 |
| `epochs` | 授权链的 epoch 根 | 运行时 / 审计 |
| `floor` | 裸指针、FFI、syscall 的站点计数与契约名 | 审计 / 发布闸 |

### 7.2 地板是一个数

```
floor = {
  raw_ptr_sites: N,        // ptr 的读写/变换站点
  ffi_sites: M,            // 外部调用站点
  syscall_sites: K,        // syscall4/6/7 站点
  lossy_copies: L,         // 把 ref 截成整数/字节的站点
  contracts: [...]         // 声明"不留存"的外部函数名
}
```

这个数不承诺"零"，它承诺"可数、可审、可比"。发布闸可以规定"floor 不得比上一版大"，
这与 `.github/gate-floors.txt` 的棘轮同形。

### 7.3 LSPE 的义务

| 性质 | LSPE 形状 |
|---|---|
| A1 无陈旧授权 | `AuthorityLive(c)`；证据 `Guard(c, o, g, e)`；违反 = 问题 |
| A2 无 ABA | `GenMonotone(o)`；证据 `Retire(o, g→g+1)` |
| A3 跨种类完备 | `RevokedSubtree(c)`；证据 `Revoke(c, e→e+1)` + `targets` |
| A4 撤销静默 | `Quiescent(S)`；证据 `Safepoint(S)` |
| A5 可审计 | 每个 trap 一条问题 + 一个来源 |

`docs/262` 里那条"`revocable` 能力撤销后不再访问"的 `Assumed`，在 S3 之后可以升成
`Checked`（单进程、受守卫子集内）。

---

## 8. 落地阶梯

| 级 | 内容 | 依赖 | 状态 | Issue |
|---|---|---|---|---|
| **S1** | `revision` 律 + `generation`/`retire` + 带代 `guard` | — | #1535（PR） | #1551 |
| **S1.5** | `ref T = (idx, gen)`、推断、自动守卫、跨调用 | S1 | 待做 | #1551、#1592 |
| **S2** | 堆对象头代；`free` 翻代；与 LGC 对齐 | S1 | 待做 | #1554 |
| **S3** | 授权 epoch + C2 级联 + 与 `revoke` 合流 | S1/S2、#1537 | 待做 | #1557、#1593 |
| **S4** | 静默：线程 EBR、FFI barrier、借用×撤销 | S3 | 待做 | #1559 |
| **S5** | L0 契约：`dom_revoke`/`dom_info` 带 gen/epoch | S3/S4 | 待定（对外契约） | #1560 |
| **F1** | 域代数/时律的操作语义 + 健全性 | 并行 | 待做 | #1589 |
| **F2** | A0–A5 + no-sweep 的机器检查证明 | F1 | 待做 | #1590 |
| **M1** | 跨种类撤销基准 + 地板度量 + 与 CBGR/PICASSO/Cornucopia/Fil-C 对比 | S3 | 待做 | #1591、#1594 |

**跟踪**：伞 issue 是 #1540（docs/320 的能力时序安全）。本文新增的六条是 F1 #1589、
F2 #1590、M1 #1591、ref 别名模型 #1592、跨种类撤销 #1593、地板报告 #1594；
S1–S5 的既有跟踪是 #1551 / #1554 / #1557 / #1559 / #1560。

S1–S4 全在语言侧，不动 `lom/*.lom`；S5 才是对外契约变更（`CLAUDE.md` 第一节）。

---

## 9. 判据

| 判据 | 喂什么 | 期望 |
|---|---|---|
| 陈旧授权 | `grant; revoke; guard` | trap，点名 `authority-revoked` |
| 对象退休 | `alloc; retire; guard` | trap，点名 `object-retired` |
| ABA | 同槽复用后旧代 | trap，点名代不匹配 |
| 撤销级联 | 父→子→孙，撤父，用孙 | trap，审计条目 = 子树 guard 次数 |
| 跨种类 | 一次 `revoke` 撤 disk+proc+auth 的派生 | 三类都 trap；无跨种类传播指令 |
| 权利单调 | 派生时放大权利 | 编译期拒 |
| 静默 | 线程 A 在飞，线程 B 撤销 | A 的下一次访问 trap；撤销返回后无访问 |
| 地板计数 | 一个含 raw ptr/FFI 的程序 | `floor.raw_ptr_sites` 与手数一致 |
| 双实现 | 同一份源 | `lomentc.py` 与 selfhost 的 IR/Potato 逐字节一致 |
| 成本（确定性量） | 同一段程序 | IR 里只多一次 load+compare，不用墙钟比值 |
| 静态/动态一致 | 同一份源，常量折掉 vs 运行期判 | 判决结果逐字节相同 |

第一条红判据必须是"陈旧授权"：在未加 epoch 的实现上，`grant→revoke→guard` 必须静默通过。
先红，才能证明后面的修复真的做了什么。

---

## 10. 形式化与测量计划

### 10.1 形式语义

- 域代数：`D = (S, P, π, λ)`；`λ` 的八条时律各给一个状态转移；证明 S1–S3 健全性
  （`docs/146`/`docs/306`）。
- 组合：树/图的包含、互斥、联动、级联；证明 C1/C1t/C2 的封闭性。
- 引用：`&T` / `ref T` / `ptr` 三段语义；`ref` 的 aliasing model 参照 Stacked/Tree Borrows。

### 10.2 机器检查

- A0–A5 + no-sweep 定理，Coq/Lean/Isabelle 任选；
- 子集外的反例：`ptr as u64`、`memcpy` 截断、FFI 留存，各一条；
- 证明"root-set 完整性"：G4 之外没有免费午餐。

### 10.3 测量

| 量 | 怎么量 | 对照 |
|---|---|---|
| 每次判决成本 | IR 指令数（load+compare），不取墙钟比值 | 无 guard 基线 |
| 撤销延迟 | `revoke` 到第一次 trap 的判决点数 | Cornucopia 扫描窗口 |
| 内存开销 | 每槽一个代字 + 每授权一个 epoch 字 | CBGR 16/32 字节引用 |
| 地板比例 | `floor` 站点数 / 总访问站点数 | Rust unsafe 比例（不可直接比，需说明） |
| 跨种类 | 一次 revoke 的 targets 数 | 前作通常 1 类 |

### 10.4 对照系统

Verum CBGR、PICASSO、Cornucopia、Fil-C、MTE、Fuchsia 句柄（模型）。每一条给同一组
微基准：UAF、ABA、撤销级联、跨种类。不能真跑的（CHERI 硬件）用论文数据，明确标注。

### 10.5 负结果

如果跨种类撤销的收益量不出来，或者每次判决成本高于预期，就把这条件作为负结果写下来。
负结果也是贡献；它把"看起来不可能"变成"测过，确实不行"。

---

## 11. 相关工作报告

| 系统 | 指称物种类 | 每次校验 | 撤销 | 代/ABA | 跨种类 | 产物审计 | 证明 | 默认可用 |
|---|---|---|---|---|---|---|---|---|
| Verum CBGR | 内存 | ✅ 解引用 | ✅ 翻代 | ✅ | ❌ | ? | ? | ? |
| PICASSO | CHERI 堆 | ✅ load/store | ✅ PVT | ✅ | ❌ | ❌ | ? | ❌（硬件） |
| Fuchsia | 内核对象 | ✅ syscall | ✅ close | ✅ | ❌ | ❌ | ❌ | ✅（OS） |
| seL4 | 内核对象 | ✅ cap lookup | ✅ CDT | ✅ KOID | ❌ | ❌ | ✅ | ✅（OS） |
| EROS/KeyKOS | 内核对象 | ✅ | ✅ revoker | ✅ | ❌ | ❌ | ❌ | ❌ |
| Cornucopia | 内存 | ❌（扫描） | ✅ 扫描 | ✅ | ❌ | ❌ | ? | ❌（硬件） |
| Fil-C | 内存 | ✅ 每次访问 | ✅ 原子失效 | ✅ | ❌ | ❌ | ❌ | ❌（ABI） |
| MTE | 内存 | ✅ 概率 | ✅ 重打 tag | 概率 | ❌ | ❌ | ❌ | 部分 |
| Rust | 内存 | ✅ 编译期 | ❌ 运行期 | ✅ 类型 | ❌ | ❌ | ✅ RustBelt | ✅ |
| Cyclone | 内存 | ✅ 编译期 | ❌ | ✅ 区域 | ❌ | ❌ | ❌ | ❌ |
| **Loment（目标）** | **disk/proc/auth/heap** | **✅ 每次 guard** | **✅ 代+epoch** | **✅** | **✅** | **✅ Potato/LSPE** | **待做** | **待做** |

这张表的作用是防止自欺：Loment 的"✅"必须在每一列都有判据，不能只写在文档里。

---

## 12. 对外措辞

**可以说的：**

> 我们把 generational reference 与 capability domain 合成一个判决点，并在受守卫子集内
> 给出无扫描、非概率的授权时序安全；范围覆盖 disk/proc/auth/heap；子集外的地板是一个
> 可数的数，印在产物里。

**不要说的：**

- "我们发明了权能时序安全"——前作太多（Redell/seL4/EROS/Fuchsia/CHERI/Fil-C/CBGR/PICASSO）。
- "我们比 Fil-C 更安全"——ABI、硬件、GC 的差异没有可比性。
- "零成本"——每次访问有一次比较；成本是真实的，只是小。
- "完整"——只对受守卫子集完整；子集外是地板。

---

## 13. 开放问题

1. `ref` 的拼写与推断：`ref T` 是类型构造器，还是 `let r in disk` 的扩展？
2. 代/epoch 的宽度：32 位 + 回绕策略，还是 64 位？
3. 权利代数：`r` 是位集还是格？委托时如何取交？
4. 借用 × 撤销：借用期间撤销是编译期拒还是运行期 trap？
5. FFI 契约的语法：`borrows` / `retains` 标注？白名单怎么审？
6. 跨进程/IPC：权能能不能穿过 `sendmsg`？
7. 跨种类 targets 的语法：`revoke a, b, c;` 还是 `revoke group;`？
8. 地板计数怎么定义：按站点、按字节、按执行路径？三者的可比性不同。
9. 形式化的范围：只证核心演算，还是连 18 种时律一起证？
10. 与 `lifetime` 第八维的交互：`&T` 的区域和 `ref` 的代在同一个值上如何共存？

---

## 14. 诚实边界

- **没有新原语**：代、epoch、级联、每次校验都是老件。本文的新意只在组合与范围。
- **没有机器检查的证明**：§4 的定理目前是实现级断言 + 判据，不是 Coq 证明。
- **没有测量**：跨种类撤销的收益、每次判决的成本，都还没有数据。
- **只对受守卫子集完整**：`ptr`/FFI/syscall/`memcpy` 是地板；`floor` 会把它数出来。
- **Rust 级的采用需要多年**：机制是最容易的部分；诊断、省略、迁移、生态才是难点。
- **CHERI/Fil-C 的硬件/ABI 优势不可复制**：本文不主张在这些维度上竞争。

---

## 15. 先例索引

1. D. D. Redell, *Naming and Protection in Extendable Operating Systems*, PhD thesis,
   UC Berkeley, 1974. <https://caltss.computerhistory.org/archive/7409-thesis-ddr.pdf>
2. V. D. Gligor, *Review and Revocation of Access Privileges Distributed Through
   Capabilities*, IEEE TSE SE-5(6):575–586, 1979.
3. seL4 Reference Manual（CNode / Revoke / finalise）；形式化规格 `l4v` 的
   `CSpace_A.thy`. <https://github.com/seL4/l4v>
4. EROS / KeyKOS 的 revoker capability 与 forwarding object；
   <https://www.cse.psu.edu/~trj1/cse543-f16/docs/capability-systems.pdf>
5. Genode 会话对象生命周期；
   <https://lists.genode.org/pipermail/users/2015-April/003252.html>
6. Fuchsia/Zircon 句柄与权利；
   <https://fuchsia.dev/fuchsia-src/concepts/kernel/handles>
7. H. Xia et al., *CHERIvoke*, MICRO 2019.
   <https://dl.acm.org/doi/10.1145/3352460.3358288>
8. N. W. Filardo et al., *Cornucopia: Temporal Safety for CHERI Heaps*, S&P 2020.
   <https://www.cl.cam.ac.uk/research/security/ctsrd/pdfs/2020oakland-cornucopia.pdf>
9. N. W. Filardo et al., *Cornucopia Reloaded*, ASPLOS 2024.
   <https://dl.acm.org/doi/10.1145/3620665.3640416>
10. Fil-C: How It Works. <https://fil-c.org/how>
11. *PICASSO: Colored Capabilities*, USENIX Security 2026.
    <https://www.usenix.org/system/files/usenixsecurity26-guelmez.pdf>
12. Verum, *Capability-Based Generational References (CBGR)*.
    <https://verum-lang.org/docs/language/cbgr>
13. Intel patent US11163569, *Individually revocable capabilities for temporal memory safety*.
    <https://patents.google.com/patent/US11163569>
14. ARM MTE；Microsoft, *Security Analysis of Memory Tagging*, 2020.
    <https://raw.githubusercontent.com/microsoft/MSRC-Security-Research/master/papers/2020/Security%20analysis%20of%20memory%20tagging.pdf>
15. Rust NLL / Polonius / RustBelt / Stacked Borrows / Tree Borrows；
    本仓的落地见 `docs/317`。
16. M. Tofte, J.-P. Talpin, *Region-Based Memory Management*, Information and Computation
    132(2), 1997；Cyclone（USENIX 2002）。
17. P. Wadler, *Linear Types Can Change the World!*, 1990；Linear Haskell（POPL 2018）。
18. M. Michael, *Hazard Pointers*, 2004；EBR（Fraser）；RCU。
