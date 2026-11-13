# 345 · 类继承与间接引用：`choose` 开关、封闭世界与代价

> 状态: **设计稿**（2026-11-13）
> 基准: `origin/main`（`663b93ff`，2026-11-13）
> 上游: `docs/143`（L1 规范）· `docs/144`（原生后端）· `docs/147`（Potato）·
> `docs/158`（冻结面）· `docs/206`（所有权边界）· `docs/210`（LGC）· `docs/229`（LVM 与静态调用图）·
> `docs/253`（LMS）· `docs/344`（LSPE 框架，第一个消费者）
> 一句话: **加入 `class` / `extends` / `override` / `virtual` 与函数指针/间接调用，
> 默认关闭；`class_off` / `indirect_off` 的产物与今天逐字节相同。
> `indirect_on` 走封闭世界（目标集可静态枚举），否则 `no_runtime` / `sealed`
> 的“缺席”主张必须降级或拒绝。**

---

## 0. 三句话

1. **语言面加两件**：单继承的类（字段 + 方法 + 虚方法 + `super` + 抽象类）与
   类型化函数指针/间接调用。它们各自一个 `choose` 开关，默认关。
2. **默认不变**：两个 `_off` 取值下，词法/语法/类型/IR/产物与今天逐字节相同；
   这不是“顺手加个语法”，而是 `docs/158` 的冻结面改动，要走两套实现与种子纪律。
3. **封闭世界是前提**：`indirect_on` 只接受**目标集可静态枚举**的函数指针
   （类型匹配 + 取址集合；不许 `ptr` 强转、不许 FFI 函数指针）。
   否则调用图不再有限，`docs/229` §4.2 的“缺席可核”与 `no_runtime` / `sealed`
   的保证会被一起打穿。

---

## 1. 语言面

### 1.1 类（拟）

```rust
class Shape {
    x: u32,
    y: u32,

    fn new(x: u32, y: u32) -> Shape {
        return Shape { x: x, y: y };
    }

    virtual fn area(self: &Shape) -> u32 {
        return 0;
    }
}

class Square extends Shape {
    side: u32,

    fn new(x: u32, y: u32, s: u32) -> Square {
        return Square { x: x, y: y, side: s };
    }

    override fn area(self: &Square) -> u32 {
        return self.side * self.side;
    }
}
```

- **单继承**。`extends` 只能有一个父类；接口/多重行为走 `trait`（`docs/205` R4）。
- **字段**：父类字段在前，子类字段在后；布局稳定，偏移可算。
- **方法**：默认静态解析；`virtual` 进 vtable；`override` 必须与父类签名一致；
  `super.method(...)` 是**静态**调用父类实现。
- **抽象类**：`abstract class`；抽象方法没有体，子类必须 `override`。
- **构造**：`Type { fields }` 字面量 + `Type::new(...)` 构造器；构造器是普通函数，
  返回类值/类指针；没有隐式构造器。
- **`self`**：方法的第一实参是 `self: &Shape` / `self: &mut Shape`；与
  `docs/206` 的借用子集同一条规则。
- **不做**：多继承、开放类（运行期加方法）、反射、运行期类型名、友元。

### 1.2 间接引用（拟）

```rust
fn inc(x: u32) -> u32 { return x + 1; }
fn double(x: u32) -> u32 { return x * 2; }

fn apply(f: fn(u32) -> u32, x: u32) -> u32 {
    return f(x);
}

fn _start() {
    let f: fn(u32) -> u32 = inc;
    let g: fn(u32) -> u32 = double;
    let a: u32 = apply(f, 1);   // 2
    let b: u32 = apply(g, 1);   // 2
}
```

- **函数指针类型**：`fn(T, ...) -> R`；只接受**类型匹配**的函数/方法/已取址函数。
- **取址**：`let f: fn(u32) -> u32 = inc;`；没有 `&inc`（`&` 只作用于数组，
  `docs/345` 不扩展它）。
- **间接调用**：`f(args)`；调用点有一个**目标集**（见 §4）。
- **不做**：`ptr` → 函数指针的强转、FFI 函数指针（`hosted` 下也先拒）、
  闭包捕获环境（闭包是另一条线；v1 只做无捕获函数指针）。

---

## 2. `choose` 开关

### 2.1 两个维度

| 维度 | 取值 | 默认 | 含义 |
|---|---|---|---|
| **`class`** | `class_off` | ✅ | 没有 `class` / `extends` / `virtual`；今天的语法 |
| | `class_on` | | 单继承 + 字段 + 方法 + vtable + 抽象类 |
| **`indirect`** | `indirect_off` | ✅ | 没有函数指针类型；没有间接调用 |
| | `indirect_on` | | 函数指针类型 + 封闭世界间接调用 |

- **为什么两个维**：`class_on` 需要 vtable（编译器内部的间接调用），
  `indirect_on` 是**用户可见**的函数指针；两者可以单独开。
- **`class_on` + `indirect_off`**：虚方法仍然走 vtable（编译器生成，不暴露函数指针类型）；
  用户写不出 `fn(...)` 类型。这条是“有继承、没有用户间接引用”。
- **`indirect_on` + `class_off`**：函数指针/回调，没有类。
- **两个都开**：完整形态；LSPE 框架自己用这一档。
- **两个都关**：今天的语言；产物逐字节不变。

### 2.2 与既有维度的关系

- `class_on` + `no_runtime`：**堆上的类实例需要 `alloc`**，而 `alloc` 在 `no_runtime`
  下被拒（`docs/227`）。所以 `no_runtime` + 堆类不可用；栈上类值（如果实现）
  可以，但 v1 先要求 `choose runtime`。
- `indirect_on` + `no_runtime` / `sealed`：只有**封闭世界**的目标集才允许；
  出现 FFI/`ptr` 强转 ⇒ 编译错误，或把 `no_runtime` / `sealed` 降级成
  `Assumed` 并点名（见 §4.3）。**不允许静默**。
- `indirect_on` + `hosted`：FFI 函数指针先拒；`hosted` 的动态库符号不能进目标集。
- `class_on` + `gc_auto`：类实例是 `alloc` 站点，`gc_ladder` 要给它分层；
  类字段里的指针必须进根表/块扫描（见 §5）。
- `class_on` + `lifetime`：`self: &Shape` / `&mut Shape` 走 E10 的 loan 分析；
  字段级借用仍不在 v1（`docs/317` §4.7）。

### 2.3 Potato 与产物

- Potato 当前 **v22**。两维各加一个字段（或并入一个 `oop` 字段）⇒ **v23**：
  `class: class_on|class_off`、`indirect: indirect_on|indirect_off`；
  另加 `vtables: u32`（这份产物有多少张静态 vtable）。
- 不写两维 ⇒ 不要求新字段；写了才有（与 `gc_control` / `lompi` 同一先例）。
- `loment stat` 多一行：这份产物有没有类、有没有间接调用、多少张 vtable。

---

## 3. 语义与布局

### 3.1 单继承布局

```
Shape:  [ vtable | x | y ]
Square: [ vtable | x | y | side ]
```

- 父类字段从偏移 0 开始；子类字段追加在父类之后；`vtable` 指针在对象头
  （偏移 0 或头之后，v1 固定为偏移 0）。
- 子类值可以赋给父类引用（子类型）；反向要显式 `as` + 运行期/静态检查
  （v1 只做 `as` 的静态检查，失败编译期拒）。
- `vtable` 是**静态表**（`.rodata` / `.data`），每个类一张；槽位按
  “父类虚方法顺序 + 子类新增虚方法”固定；`override` 复用父类槽位。
- `super.m()` 是静态调用父类实现，不走 vtable。

### 3.2 方法解析

| 方法 | 解析 | 调用 |
|---|---|---|
| 非虚方法 | 静态：按接收者静态类型 | 直接调用 |
| 虚方法 | 按对象头 vtable + 槽位 | `vcall` |
| `super.m()` | 静态：父类实现 | 直接调用 |
| 抽象方法 | 必须在子类 `override` | `vcall`（目标集 = 所有 override） |

**目标集**：虚调用的目标集是**封闭类层次里所有 override**；类层次在编译期完整
（没有开放类/反射），所以目标集有限。这与 `indirect_on` 的封闭世界同一条原则。

### 3.3 构造与销毁

- 构造：`Type { fields }` 分配 + 初始化；父类字段必须先于子类字段；
  没有隐式默认构造；构造器可以 `return` 一个类值。
- 销毁：`Drop`（`docs/205` P7）按“子类先、父类后”的顺序；vtable 在销毁期间
  不被调用（对象已经半死）。
- 失败：构造失败 = 返回 `Result` 或 `panic`；没有构造期异常。

---

## 4. 间接引用：封闭世界

### 4.1 目标集

一个函数指针类型 `fn(u32) -> u32` 的目标集是：

```
Targets(T) = { f | f 的类型与 T 匹配，且 f 在程序里被取址 }
```

- 没有 `ptr` → `fn` 的强转；没有 FFI 函数指针；没有运行期反射；
- 所有目标都在**同一个 closed-world 程序**里（静态链接；`sealed`）；
- 调用点 `f(args)` 的调用图边 = `Targets(T)`；调用图仍是**有限**的。

### 4.2 与 vtable 的关系

- `vcall` 的目标集是类层次里的所有 override；也是有限的。
- `class_on` 的 vtable 调用与 `indirect_on` 的用户函数指针共用同一条
  “目标集可枚举”的规则；区别是 vtable 由编译器生成，用户函数指针由用户取址。

### 4.3 对“缺席”主张的影响

`docs/229` §4.2 的 `no_runtime` / `port` 缺席主张依赖静态调用图。规则：

| 情形 | 结论 |
|---|---|
| 所有函数指针目标集有限且已枚举 | 缺席主张**照旧**；`no_runtime` / `sealed` 不降级 |
| 出现 `ptr` → `fn` 强转 | **编译错误**（E 码待定） |
| 出现 FFI 函数指针 | **编译错误**；`hosted` 下也先拒 |
| 目标集无法枚举（开放世界） | 拒绝 `indirect_on`；或把相关主张降级成 `Assumed` 并点名 |

**不允许静默**：降级要在台账里写一条 `Assumed`，并在 `lom stat` / Potato 里
可读。

---

## 5. 对安全栈的影响

| 栈 | 影响 | v1 边界 |
|---|---|---|
| **LGC**（`docs/210`） | 类实例是 `alloc` 站点；`gc_ladder` 要分层；字段指针进块扫描/根表 | v1 只做 `gc_manual` + `runtime`；`gc_auto` 的类根表是后续 |
| **LMS**（`docs/253`） | 字段访问的界/初始化；虚调用前的非空/初始化 | g0/g1 沿用；类字段的 g1 是后续 |
| **借用/E10**（`docs/317`） | `self: &Shape` / `&mut`；字段级借出不进 v1 | 同 `docs/317` §4.7 |
| **能力域**（`docs/146`） | 类实例作为域对象；`guard` 不变 | 域是对象种类之一，v1 不扩 |
| **LSPE**（`docs/262`） | `TypeWellFormed` 要处理继承/override；`Initialized` 要处理字段；`Exclusive` 要处理 `&mut self` | 站点种类加 `class` / `vcall` / `field`；先报告面 |
| **缺失主张** | `no_runtime` / `sealed` 依赖静态调用图；封闭世界是前提 | §4.3 |

---

## 6. 对编译器与后端的代价

| 层 | 要动什么 | 代价 |
|---|---|---|
| **L0 契约**（`lom/*.lom`） | 新 opcode/ABI：`vcall`、`load_vtable`、`class_layout`；`Opcode` 变体数变化 | **跨线契约变更**：单独提交、通知下游；`lomc_test` 的变体数断言会红（预期信号） |
| **参考实现** | 词法/语法/类型/布局/vtable/间接调用 | 大；`class_on` / `indirect_on` 两条新路径 |
| **自举实现** | `checker.lomt` / `codegen.lomt` / `native_*.lomt`：继承、vtable、`vcall` | **最大成本**：自举是 token 级，没有 AST；每加一个函数 ≈ +4.7s，60s 护栏只剩 ~1%；种子 ~3.88MB 悬崖 |
| **`lomelf`** | 间接调用（`call *reg` / 分派表）、vtable 发射 | 今天不支持间接调用（`lomelf.py:1009`）；要加，且孪生 `lomelf.lomt` 同步 |
| **C 档**（`docs/298`） | clang 直接吃 IR；间接调用/vtable 天然支持 | 只要 IR 合法 |
| **LVM T0–T3** | 解释 `vcall`；T1 反馈、T3 去优化 | 晚一档；T0 先跑通 |
| **PE/Windows** | 间接调用/分派表 | 与 ELF 同一 IR；垫片不涉及 |
| **Potato** | v23 的 `class` / `indirect` / `vtables` | 默认不写不要求 |
| **编辑器/LSP** | 类/方法/override 的着色、跳转、补全 | VS Code 扩展与 LSP 各一条判据 |
| **门禁** | 两维新取值 + 判据 + `CORE_DIMS` / `CORE_DIM_OF` / `CORE_DEFAULTS` | 每维全程序一次；默认 `_off` 字节不变 |

---

## 7. 前史

| 先例 | 机制 | 本文的选择 |
|---|---|---|
| **Simula 67** | 类、继承、虚方法（第一个 OOP 语言） | 单继承的源头 |
| **Smalltalk** | 消息发送、动态派发、开放类 | 不做开放类；消息发送退化成 vtable |
| **C++ / Itanium C++ ABI** | vtable 布局、单/多继承、RTTI | 单继承 + 静态 vtable；不做多继承/RTTI |
| **Java / C#** | 单继承 + 接口、vtable/接口表 | 单继承 + `trait` 做接口 |
| **Rust** | trait + 单态化，没有类继承 | 用 `trait` 做行为，`class` 做布局/继承 |
| **Go** | 没有继承，接口是隐式的 | 不做隐式接口；显式 `extends` |
| **Obj-C / COM** | 消息/接口指针、vtable | 不做运行期消息；vtable 静态 |
| **LLVM `call` / `invoke`** | 间接调用、`!callees` 元数据 | 目标集可枚举；IR 带目标集 |
| **C 函数指针** | `fn` 指针、取址、回调 | 只允许类型匹配 + 封闭世界 |
| **去虚化 / 封闭世界优化** | 类层次分析（CHA）、PGO 去虚化 | 目标集分析是 CHA 的轻量版 |

**自报**：类继承、vtable、函数指针都不是新东西。本文的新颖面只有两条：
**`choose` 开关下的默认零改动**，以及**“间接引用必须封闭世界，否则缺席主张降级”**
这条纪律 —— 后者是 Loment 既有“可核的缺席”（`docs/229` §4.2）的直接延伸。

---

## 8. 不可能性与边界（先写死）

1. **不做多继承。** 菱形、布局合并、`super` 歧义都不做；接口走 `trait`。
2. **不做开放类 / 反射 / 运行期方法表。** 类层次在编译期完整；没有 `getClass()`。
3. **不做 `ptr` → 函数指针强转。** 目标集会变成开放世界；缺席主张会破。
4. **不做 FFI 函数指针。** `hosted` 下也先拒；动态库符号不进目标集。
5. **不做闭包捕获。** v1 只做无捕获函数指针；闭包是另一条线。
6. **堆类需要 `runtime`。** `no_runtime` + 堆类不可用；栈上类值是后续。
7. **字段级借用不进 v1。** 与 `docs/317` §4.7 同一条边界。
8. **自举侧是最大成本。** 它可能先落后；但孪生纪律不允许“只做参考侧”长期存在。
   要么先只开 `--emit` 到 clang 的参考路径，要么分阶段把自举补齐。
9. **`indirect_on` 不能保留开放世界的缺席主张。** 目标集不可枚举 ⇒ 拒绝或降级。

---

## 9. 判据（可证伪）

| # | 判据 | 证伪方式 |
|---|---|---|
| **A** | 两个 `_off` 取值下，现有语料产物与今天逐字节相同 | 变了 ⇒ 默认档被破坏 |
| **B** | 一个基类 + 一个子类 + 一个虚方法：静态调用与 `vcall` 结果相同 | 不同 ⇒ 派发错 |
| **C** | 子类布局：父类字段偏移不变；`override` 复用父类槽；`super` 调父类实现 | 偏移/槽错 ⇒ ABI 错 |
| **D** | 两个同型函数取址，`apply` 分别调到两个目标；调用图含两个 | 只含一个 ⇒ 目标集错 |
| **E** | `ptr` → `fn` 强转 ⇒ 编译错误；FFI 函数指针 ⇒ 编译错误 | 放行 ⇒ 开放世界 |
| **F** | `class_on` + `gc_auto`：类实例是 `alloc` 站点，字段指针进根表 | 漏根 ⇒ GC 漏对象 |
| **G** | 两侧（参考/自举）对 `class_on` / `indirect_on` 夹具逐字节一致 | 不同 ⇒ 孪生破 |
| **H** | Potato v23 记录 `class` / `indirect` / `vtables`；`loment stat` 可读 | 读不出 ⇒ 产物不可审计 |
| **I** | 自举侧加类/间接后，种子与 60s 护栏仍在 | 超限 ⇒ 先拆模块/分阶段 |

---

## 10. 落地阶梯

| 步 | 内容 | 出口 |
|---|---|---|
| **C0** | 两维 `choose` + 语法骨架（`class` 关键字/`fn` 类型）+ Potato 字段；`_off` 字节不变 | A / H |
| **C1** | 单继承布局 + 非虚方法 + `self` + 构造；参考侧 | B / C |
| **C2** | 虚方法 + vtable + `super` + 抽象类；`lomelf` vcall | B / C / G |
| **C3** | `indirect_on` 函数指针 + 封闭世界目标集 + 调用图 + 缺席降级 | D / E |
| **C4** | 自举侧补齐；种子/容量/双子 | G / I |
| **C5** | LSPE 框架改为类继承 + 间接派发（`docs/344` 的 `MacroChecker` 基类） | `docs/344` 的判据 |

每一步都走 `docs/158` §5：手写一条、生成物一条；种子 `--emit` 不手合；
判据先红后绿；`lom/*.lom` 的变更单独提交并通知下游。

---

## 11. 我不主张

1. **不主张这是小改动。** 它动语言面、L0 契约、两套实现、后端、安全栈与门禁。
2. **不主张默认会变。** 两个 `_off` 取值下，产物逐字节不变；这是硬棘轮。
3. **不主张开放世界。** `indirect_on` 必须封闭；否则缺席主张降级或拒绝。
4. **不主张多继承/反射/运行期方法表。** 单继承 + `trait`，类层次编译期完整。
5. **不主张自举能立刻跟上。** 先参考侧 + `--emit`，再补自举；孪生纪律不放松。
6. **不主张 LSPE SDK 现在公开。** 类/间接是 SDK 的地基；地基没稳，SDK 1.0 不发。
