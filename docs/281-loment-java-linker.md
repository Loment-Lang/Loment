# 281 · Loment-Java 链接系统：没有 Java 的 Java 库

> 状态：**设计稿**（2026-10-20）。2026-10-22：`jrt` 第一块落地 —— `loment/lib/jrt/jrt.lomt` 生成 `jrt.ll`，覆盖 `System.out`/`err` 的 `print`/`println`、`Math` 整型子集、`String.equals`/`hashCode`；对象头、跨帧异常与装箱仍缺。2026-10-21 起：AOT 纵切片、产品包装器（#1062）与外源取件/锁 v2（#1056）已落地（PR #1148）；其余见 §10。
> **2026-10-20 修订：发行与链接层已由 `docs/282` 重新设计（Java 库是 lompi 包，
> 不再以 `--java-lib` 为主入口）；本文的 class 文件 → IR 内核、验证器、对象布局
> 仍然有效，§4.5 的 `--java-lib` 降为本地/离线逃生舱。**
> 基线：`origin/main` `42c9bc63`（2026-10-21；设计初稿核对于 `8cdce0cf`）。
> 上游：`docs/173`（FFI 四阶段）· `docs/183`（库兼容垂直）· `docs/188`（多语法前端）·
> `docs/212`（外包给 LLVM 的逃生舱）· `docs/225`（运行期空白）。
> 判据口径沿用仓库既有纪律：**先红后绿**、**夹具用真实现的产物**、**不用墙钟比值当门禁**。

## 0. 一句话

**`.jar` 在构建期被 AOT 编译成一份可移植的 Loment IR 模块；这份 IR 与 Loment 程序的 IR
在链接期合并，产物是普通的 ELF / PE。构建不需要 JDK，运行不需要 JVM，Windows 上直接产出
并运行 `.exe`。**

这句话里每一段都是可证伪的，后面逐条给判据。它不是"Java 兼容"：**闭世界、静态子集、
没有反射与动态装载**。说清楚不做什么，比说清楚做什么更重要。

## 1. 起点账：今天 Java 在 Loment 里是什么

| 需要的东西 | 今天 | 证据 |
|---|---|---|
| 调 Java 库 | **两条路，都要装 Java**：① 进程桥 `javac` + `java`（`docs/173` §5b）；② `choose hosted` 下用 JNI 垫片 + `libjvm.so`（`docs/222` §8，`tools/loment_hosted_test.py` 真跑通 `Hello.compute(1) → 2037`）。**没有"不装 Java"的路** | `docs/173` §5b、`docs/222` §8 |
| Java 源码 → Loment | `jtrans` 只读**声明与函数体**，且 `float` / `double` / `String` 直接拒 | `tools/jtrans.py:53` |
| 读 `.class` / `.jar` | **零**。全仓搜 `CAFEBABE` / `classfile` / `constant_pool` 无命中 | 2026-10-20 实测 |
| `--link` 外部对象 | **只吃 ELF64 `.o`**；PE 目标明确拒绝 | `tools/lomelf.py:6680` |
| 间接调用 | **后端不支持**：`call %x` 直接 `Unsupported` | `tools/lomelf.py:1119` |
| 语言自带堆 | **默认 `sealed` 档 64 KiB 固定 arena**；`[65536 x i8]` 写死在运行期 IR 里。`port hosted` 另有 **1 MiB** 宿主 arena（`docs/228-hosted-heap.md`），但它要宿主 C 工具链 | `tools/lomentc.py:205`、`357` |
| PE 侧内核 | **12 个 `kernel32` + 19 个 `ws2_32`** 导入，**29 个** syscall 派发号；没有 JVM 导入 | `tools/lomelf.py:1932-1941`、`1979-2011` |
| PE 侧可用堆 | `brk` 由垫片模拟，上限 **64 MiB** | `tools/lomelf.py:2126` |
| 语言类型 | `u8..u64 / i8..i64 / bool / str / ptr / ()`；**没有浮点类型** | `tools/lomentc.py:31` |
| 产品构建链 | 自举 `loment-driver` 出 IR → 自举 `loment-lomelf` 链接；**包里不需要 Python** | `tools/loment_dist.py:381-476` |
| ELF 动态库 | **已落地**（2026-10-17，`docs/173` 阶段 3）；PE 侧仍无 FFI | `docs/173` §5b |

**结论**：JNI 那条路在 **Linux 的 `hosted` 档上已经能跑**（`docs/222` §8），但它
**需要装 Java**（`libjvm.so`），而且在 PE 上连外部对象 / 动态库都还没有（`docs/173`
§5b："PE 侧 FFI ⛔"）。它满足不了"没有 Java"，也满足不了"Windows 直调"。所以新设计
不能是"把 JVM 搬进来"，只能是**把 Java 字节码在构建期消掉**。AOT 还能顺手去掉
`docs/222` §8.1 那个 JNI C 垫片：没有 `JNIEnv` 函数表，就没有要摊平的间接调用。

## 2. 三条路线与选择

| 路线 | 运行时要不要 Java | Windows 行不行 | 主要代价 | 结论 |
|---|---|---|---|---|
| **A. 嵌 JVM / JNI** | 要（`jvm.dll` / `libjvm`） | Linux `hosted` 已能跑；PE 还缺 FFI/动态装载 | 与"没有 Java"冲突；JNI 的间接调用还要一段 C 垫片 | **否** |
| **B. 自带字节码解释器** | 不要 | 可以（解释器是普通 Loment 代码） | 产物里仍是一台 JVM；启动/执行慢；动态特性容易 | **不作产品，只作对照物** |
| **C. AOT：字节码 → Loment IR** | **不要** | **可以**（同一份 IR 出 PE） | 闭世界；反射/动态装载做不了 | **选中** |

路线 B 不是白丢：把解释器当作**构建期/测试期的独立对照物**，与真 JVM 的输出三方对拍，
正好满足"两条实现相等不算证据"那条既有教训。它**不进最终产物**。

选 C 的决定性理由不是性能，是**形状**：

1. Java 字节码是**栈机**；Loment 的原生 IR 也是**栈机产物**（`docs/212` §1：`lomelf` 是
   "每个 SSA 值一个栈槽"的栈机）。操作数栈 → SSA 值是一一对应的，不需要寄存器分配器。
2. 字节码是**平台无关**的。把 `.class` 降到 Loment IR 之后，ELF 与 PE 目标**共用同一份
   IR** —— Windows 直调不需要 COFF 链接器，也不需要 PE FFI。
3. 链接发生在 **IR 层**，不是对象层。这绕开了两个已知硬缺口：`--link` 只认 ELF、
   PE 的 extern 调用点仍按 System V 寄存器传参（`tools/lomelf.py:6168`，PE 没有覆盖它）。
   IR 合并后，同一个 IR 里的定义是**直接调用**，两边用 Loment 自己的栈约定，与目标无关。

## 3. 总体架构

```
  foo.jar / classes/                 app.lomt
        │                                │
        ▼                                ▼
  ┌───────────────┐                ┌───────────────┐
  │  jparser      │   zip/deflate  │ loment-driver │
  │  class 模型    │                │   (自举)       │
  └──────┬────────┘                └──────┬────────┘
         ▼                                │
  ┌───────────────┐                       │
  │  verifier     │  类型/栈/控制流        │
  └──────┬────────┘                       │
         ▼                                │
  ┌───────────────┐                       │
  │  lower        │  JVM 栈 → SSA          │
  └──────┬────────┘                       │
         ▼                                ▼
      foo.ll  ────────────────┐      app.ll
                              ▼           │
                        ┌─────────────────┴──┐
                        │  IR 链接器          │
                        │  符号解析 + 合并     │
                        └─────────┬──────────┘
                                  ▼
                         jrt.ll（运行期子集）
                                  │
                                  ▼
                        loment-lomelf → ELF / PE
```

四层各自的边界：

| 层 | 输入 | 输出 | 不许做的事 |
|---|---|---|---|
| **前端** | `.jar` / `.class` / 目录 | 已验证的类模型 | 不执行 Java 代码；不调 `java` / `javac` / `jar` |
| **降级** | 类模型 | 可移植 `.ll` | 不读目标平台；不生成间接调用 |
| **链接** | 多个 `.ll` | 一个符号闭合的 `.ll` | 不区分 Java / 非 Java 模块；不做动态装载 |
| **运行期** | 被链接的 `jrt.ll` | 普通 Loment 函数 | 不依赖 libc、JVM、JDK |

**运行期 jrt 用 Loment 写**，由普通 `loment-driver` 编成 `jrt.ll`，与 Java 库的 IR 一起链接。
它不复制进每个 Java 库，避免"同一个 `String` 类有十份实现"。

**Java 前端本身**先做 Python 参考实现（`tools/lomjava.py`）把判据打通，再按仓库的双实现
纪律移植成自举工具（`loment/tools/lomjava.lomt`）；产品包只带后者，参考实现留在开发口
做对拍。这与 `lomelf` 的既有形状一致：参考实现负责"把行为说清楚"，自举实现负责"随包发"。

## 4. 链接单元：什么是一个 Java 库

沿用 `docs/183` §3 的推广："一个库 = 一份声明面 + 一份实现 + 一个身份"。

### 4.1 目录形状

```
deps/com.example.foo/
  lib.jar                 # 原始字节，原样保留
  loment-java.json        # 链接清单（人可读）
  interface.lomt          # 生成的 Loment 接口单元（人可读）
  lib.<hash>.ll           # AOT 产物（可缓存、可重建）
  lib.<hash>.ll.sha256    # 产物的内容哈希
```

`<hash>` 不是 jar 的字节哈希，而是**身份哈希**：

```
identity = SHA-256(
    ABI 版本 "JL-A/1"        # 本设计定义的边界 ABI
  ⊕ jar 内容 Merkle 根       # 按 zip 成员名排序，逐成员 SHA-256
  ⊕ loment-java.json 字节    # 导出面、native 绑定、排除项、目标 class 版本
  ⊕ 编译器版本 + 运行期版本  # 产物语义会随编译器变
)
```

用**成员 Merkle 根**而不是整个 zip 的字节哈希：zip 的时间戳、压缩级别、成员顺序都会变，
但类内容没变。否则"同一个库重新打包一次"就会被当成另一个库，缓存与锁文件全部失效。

### 4.2 清单（第一版字段）

```json
{
  "abi": "JL-A/1",
  "name": "com.example.foo",
  "jar": "lib.jar",
  "jar_sha256": "…",
  "exports": [
    "com/example/Foo.add(II)I",
    "com/example/Foo.greet(Ljava/lang/String;)Ljava/lang/String;"
  ],
  "natives": {
    "com/example/Foo.readSensor()I": "__loment_read_sensor"
  },
  "java_base": "jrt-0.1",
  "class_version_max": 65,
  "deny": ["java/lang/reflect/*", "java/lang/ClassLoader"]
}
```

三条纪律：

1. **清单是声明，jar 是事实。** 链接器必须核对 `jar_sha256` 与 `exports` 里每个方法确实
   存在；对不上就报错，不许"按清单相信"。
2. **`exports` 之外的类与方法不保证可调**。它们仍可能因可达性被编进 IR，但不在接口面上。
3. **`natives` 是唯一的 Java → Loment 出口**。没有登记 `native` 方法在链接期报错，
   不许静默生成一个空桩。

### 4.3 接口单元（生成的 `.lomt`）

接口单元是**给人看的类型面**，也是 Loment 侧唯一需要 `use` 的东西。示意（围栏用 `rust`
只是为了让查看器上色，文件本身是 `.lomt`）：

```rust
module java_com_example_foo

// 静态方法：com/example/Foo.add(II)I
pub fn foo_add(a: i32, b: i32) -> i32 {
    return __jl_com_example_Foo_add_II(a, b);
}

// 实例方法：第一个参数是 jref（Java 对象句柄，本质是 ptr）
pub fn foo_bar(self: ptr, x: i64) -> i64 {
    return __jl_com_example_Foo_bar_J(self, x);
}

extern fn __jl_com_example_Foo_add_II(a: i32, b: i32) -> i32;
extern fn __jl_com_example_Foo_bar_J(self: ptr, x: i64) -> i64;
```

这里有一个**必须讲清的链接细节**：`extern fn` 在参考链接器里走 C ABI
（`tools/lomelf.py:1127-1141`），而 PE 目标的 extern 调用点**没有**微软 x64 的覆盖，
仍是 System V 寄存器（`tools/lomelf.py:6168`；今天 `--link` 在 PE 上被直接拒绝，
所以这处还没有暴露，但 IR 合并会走到它）。所以接口单元里的 `extern` 只是
**构建期的占位**；IR 链接器在合并时做一件事：

> **任何 `declare`，只要在同一批 IR 里有 `define`，就把它改写成内部直接调用。**

不这样做，在 PE 上会按错误的寄存器约定传参 —— 症状通常是"第一个参数对、后面对、
返回值是垃圾"，而不是链接错误。这条是设计里最容易被漏掉的一处，单列一条判据（§8 C6）。

### 4.4 链接算法

输入：`app.ll`、若干 `lib.*.ll`、`jrt.ll`。输出：一个符号闭合的 `.ll`。

1. **解析所有模块**，收集 `define` 与 `declare`。
2. **定义表**：同名 `define` 出现两次 → 硬错（不给"后者覆盖前者"的语义）。
3. **解析**：每个 `declare` 若在定义表里 → 把调用点改成内部调用；否则保留为外部符号。
4. **未解析**：除非用户显式给了 `--allow-extern`（给 FFI 用），否则硬错，并打印
   `类.方法(描述符)` 与它来自哪个 `.ll`。
5. **保留 `_start` 唯一**：只有主程序 IR 可以有 `_start`；Java 库与 `jrt.ll` 里出现即硬错。
6. **保留名字**：`__jl_` / `__jrt_` 是保留前缀，用户 IR 自己定义这两个前缀 → 硬错。

链接器**不**做死代码消除（第一阶段）：Java 库的所有可达方法都进产物，体积换简单。
后续可以加 `--gc-symbols`，但那是独立的一步，不混进第一版。

### 4.5 CLI 形状

```bash
loment java pack foo.jar -o deps/com.example.foo   # 读 jar, 验证, 生成清单/接口/IR
loment java check deps/com.example.foo             # 清单与 jar 对账, 不写盘
loment build app.lomt --java-lib deps/com.example.foo -o app
loment run app.lomt --java-lib deps/com.example.foo
```

`--java-lib` 接受**目录**或**清单路径**，不接受裸 `.jar`：裸 jar 没有导出面与 `natives`
绑定，等于让链接器猜。

Windows 包装器与 POSIX 包装器必须**同时**认这个开关。仓库已经有过一次"静默丢掉 `--link`"
的教训（`tools/loment_cli_test.py:837` 的注释），这次不接受"Windows 上先不支持"。

## 5. 边界 ABI：JL-A/1

### 5.1 值

| Java 类型 | 边界表示 | 说明 |
|---|---|---|
| `boolean` / `byte` / `short` / `char` / `int` | `i32` | `char` 是 16 位无符号，零扩展 |
| `long` | `i64` | |
| 引用（对象 / 数组 / String） | `ptr`（`jref`） | `0` = `null` |
| `void` | 无返回 | |
| `float` / `double` | **第一阶段拒** | 语言面没有浮点类型（`tools/lomentc.py:31`）；后续用 `std/f64` 软浮点或后端 SSE |

`jref` 是**不透明句柄**，Loment 侧只允许通过生成的接口函数与 `jrt` 句柄函数碰它。
接口单元不导出字段偏移，也不鼓励用户对 `jref` 做 `ptr_add`。

### 5.2 对象布局

对象头 16 字节，8 字节对齐：

```
+0   u32 class_id      类元数据表下标（编译期生成，不是运行时字符串查找）
+4   u32 flags         锁/标记位；单线程阶段恒 0
+8   u32 hash          身份哈希缓存（0 = 未算）
+12  u32 size_or_len   对象 = 总字节数；数组 = 元素个数
+16  …                 字段或数组元素
```

- **对象字段**按类元数据里的固定偏移访问；继承字段在前，子类字段在后。
- **数组**的元素宽度由 `class_id` 指向的元数据给出（1/2/4/8/`jref`）。
- **String** 在 `jrt` 里按 Java 语义存 **UTF-16 码元**（`charAt`/`length` 必须与 Java 一致），
  对象头 + `data jref` + `length i32` + `hash i32`。`ldc` 的 modified UTF-8 在类初始化时
  解码成 UTF-16。
- **静态字段**：每个类一块 IR 全局 `[N x i8]`，符号名 `__jl_static_<mangled>`；不放进对象堆，
  因此不会被 GC 误扫。

### 5.3 符号与重载

可读前缀 + 描述符摘要，保证单射且可调试：

```
__jl_<class 的内部名，/ → _>_<方法名>_<描述符的 8 位十六进制摘要>
```

`(II)I` 与 `(J)J` 的摘要不同，重载不会撞。生成器在同一个库内做一次**碰撞检查**：
摘要相同但描述符不同 → 硬错，改摘要长度重来。**不允许"加个序号"糊过去**。

### 5.4 参数个数与返回

- Loment 函数最多 10 个参数（自举侧参数表 10 槽，`SKILL.md` §2 的硬约束）。
- Java 方法超过 10 个参数（含 `this`）时，边界函数改成一个 `ptr` 指向**实参块**，
  由生成器给出块布局；接口单元把它包装成多个普通参数。
- 返回聚合（Java 没有多返回值，但 `jrt` 内部可能用）不进边界；边界只返回标量 / `ptr`。

### 5.5 异常跨越边界

`jrt` 用一个全局 `__jrt_exc: ptr` 保存"当前未处理异常"。规则：

- 每个可能抛异常的 Java 调用返回后，调用方**立刻**检查 `__jrt_exc`；非 0 就跳到本方法的
  处理器或直接返回默认值（继续向上传播）。
- 跨越 Loment 边界时，生成的接口包装检查 `__jrt_exc`：
  - `--java-uncaught=abort`（默认）：打印异常类名 + 消息 + Java 栈（类/方法表），退出非 0。
  - `--java-uncaught=result`（后续）：接口单元返回 `Result<T, ptr>`，由 Loment 侧处理。
- 第一版**不做 `finally` 的完全语义**之外的花活：`finally` 按复制清理块实现，`athrow`
  在 `finally` 里覆盖前一个异常 —— 与 Java 语义一致，判据要专门测。

## 6. 字节码 → IR 的降级

### 6.1 方法帧

- `max_locals` → 一组 IR 栈槽（`alloca` 风格），按 JVM 类型宽度分配；`long`/`double`
  占两个局部变量槽的规则照 JVM 规范。
- 操作数栈 → 每个基本块入口的 SSA 值。JVM 的栈高度在合并点必须一致；不一致由验证器
  拒绝，不靠降级器猜。
- 控制流 → 基本块 + `br` / `switch` / `phi`。`tableswitch` / `lookupswitch` 直接映射。

### 6.2 指令映射（节选）

| JVM | IR | 备注 |
|---|---|---|
| `iload/istore/…` | 局部槽 load/store | 宽度按描述符 |
| `iadd/ladd/…` | `add` 等 | 二元算子两边位宽必须一致（issue #316 的坑） |
| `idiv/irem` | `sdiv/srem` + 除零检查 | 除零 → `ArithmeticException` |
| `iushr` vs `ishr` | `lshr` vs `ashr` | Java 的 `>>` 是算术，`>>>` 是逻辑 |
| `i2l/l2i/…` | `sext/trunc` | `char` 零扩展 |
| `new` | `__jrt_alloc(class_id)` | 分配后清零；OOM 检查 |
| `newarray/anewarray` | `__jrt_new_array(class_id, len)` | 负长度 → `NegativeArraySizeException` |
| `arraylength` | 读 `+12` | 空引用检查 |
| `xaload/xastore` | 边界 + 空检查 + 宽度 load/store | |
| `getfield/putfield` | 固定偏移 + 空检查 | 偏移来自类元数据，不来自字节码算术 |
| `getstatic/putstatic` | 静态块全局 + 类初始化守卫 | |
| `invokestatic` | 直接调用 | 解析到具体类 |
| `invokespecial` | 直接调用 | 构造器/私有/`super` |
| `invokevirtual` | 闭世界去虚化；多目标 → 类 id 分派函数 | 见 §6.3 |
| `invokeinterface` | 同上，按接口实现集 | |
| `athrow` | 设 `__jrt_exc`，跳处理器/返回 | |
| `checkcast/instanceof` | 类 id 的子类型检查 | 失败 → `ClassCastException` |
| `monitorenter/exit` | 单线程阶段 no-op | 多线程阶段改成真正的锁 |
| `ldc` | 标量常量 / 静态 String 对象 | String 在 `<clinit>` 里构造 |

### 6.3 没有间接调用，虚分派怎么做

`tools/lomelf.py:1119` 明确拒绝 `call %x`。第一版**不要求后端加间接调用**，用
**闭世界去虚化 + 直调分派**：

1. 从导出面、`main`、`<clinit>` 出发做可达性分析。
2. 收集每个 `new` 站点的具体类（rapid type analysis）。
3. 对每个 `invokevirtual` / `invokeinterface` 调用点，求可能接收者集合：
   - 1 个 → 直接调用；
   - n 个 → 生成 `__jl_dispatch_<n>(recv, args…)`，按 `class_id` 的 `switch` / 二分链
     直接调用具体实现。
4. 集合为空 → 硬错（可达性分析漏了，或者 jar 不闭合）。

代价是分派函数的分支数，不是间接调用的缺失。对绝大多数库，n 很小。
后续如果确实需要函数指针（回调、`MethodHandle`），再给两个链接器同时加 `call ptr`，
那是独立的一步，不塞进本设计。

### 6.4 `invokedynamic`

现代 javac 会用 `invokedynamic` 做两件事：**字符串拼接**（`StringConcatFactory`）与
**lambda**（`LambdaMetafactory`）。

- 第一阶段（P0/P1）：**遇到就报错**，错误信息写出 bootstrap 方法名与调用点。夹具用
  `javac --release 8` 或不用 lambda 的库。
- P2：展开 `StringConcatFactory` 的 recipe 成 `StringBuilder` 调用链。这是纯机械变换，
  风险低、覆盖面大。
- P3：展开 `LambdaMetafactory` 成 `{fn_id, captured env}` 的闭包对象，调用点再走
  `fn_id` 的直调分派。没有间接调用也能做，代价是分派链。

### 6.5 类初始化

Java 的 `<clinit>` 是**首次主动使用**时执行，不是启动时全跑。设计采用**惰性初始化**：

- 每个类一个状态字：0 未初始化 / 1 进行中 / 2 完成。
- `getstatic` / `putstatic` / `new` / `invokestatic` 前插守卫；首次进入调用 `<clinit>`。
- 单线程阶段不需要锁；状态 1 时重入 → `ExceptionInInitializerError`。
- 启动时只初始化用户显式指定的入口类（通常是 `main` 所在类）。

这条会影响可观测行为（静态初始化顺序、副作用、异常时机），判据必须与真 JVM 对拍。

### 6.6 明确拒绝的字节码特性

| 特性 | 处理 | 理由 |
|---|---|---|
| `float` / `double` 运算 | 报错 | 语言面没有浮点类型 |
| `jsr` / `ret`（老 class 文件） | 报错 | 已有 `StackMapTable` 的版本不需要 |
| 反射、`Class.forName`、`ClassLoader` | 报错 | 闭世界没有运行时类加载 |
| `MethodHandle` / `VarHandle` | 报错 | 需要函数指针与运行时元数据 |
| 自定义 `invokedynamic` bootstrap | 报错 | 只有白名单 bootstrap 可展开 |
| 线程、`Thread`、`synchronized` 的真实并发 | P1 no-op / P3 再做 | 运行期无线程（`docs/225` §3.1） |
| `finalize` / 弱引用 / 软引用 | 报错 | 语言面没有对应构造 |
| `native` 未登记 | 报错 | 不许空桩 |
| `System.loadLibrary` / `Runtime.exec` | 报错 | 安全边界 |

## 7. 运行期 jrt

### 7.1 组成

| 模块 | 内容 | 阶段 |
|---|---|---|
| `jrt/core` | 分配、对象头、类元数据表、`Object` 的 `equals/hashCode/toString` 默认实现 | P0 |
| `jrt/array` | 基本类型数组、引用数组、边界检查 | P0 |
| `jrt/string` | UTF-16 `String`、`StringBuilder`、字面量解码 | P1 |
| `jrt/exception` | `Throwable` 家族、`__jrt_exc`、栈帧表、未捕获打印 | P1 |
| `jrt/io` | `System.out/err`、`PrintStream` 的 `print/println` | P1 |
| `jrt/math` | `Math` 的整数子集 | P1 |
| `jrt/box` | `Integer/Long/Boolean/Byte/Short/Character` | P2 |
| `jrt/util` | `Arrays`、`Objects`；`ArrayList` / `HashMap` 按需 | P2/P3 |

`jrt` **不是 JDK 的复制**：它只实现清单里声明的那部分 `java.base` 签名，未实现的类在
链接期就是"未解析"，报错并指出类名。这比"运行时才发现 `NoClassDefFoundError`"好。

### 7.2 堆与分配

- 第一版（P0-P2）：`__jrt_alloc` 从 **`brk`  arena** 线性分配。`brk` 在 ELF 与 PE 上都能用；
  PE 垫片给 **64 MiB**（`tools/lomelf.py:2126`）。用完 → 明确报错并退出，不静默踩内存。
- **不用**默认 `sealed` 档的 64 KiB `@__loment_heap`：那个尺寸是做编译期工具用的，
  不是给 Java 对象图的。`port hosted` 的 1 MiB 宿主 arena 也不能用 —— 它要求宿主
  C 工具链（`docs/228-hosted-heap.md` §3），与"构建不依赖 Java/clang"的目标冲突。
- P3 接 GC：把 `jrt` arena 交给 LGC，要求两处改动（都属于独立提交）：
  1. arena 大小参数化（现在是 `[65536 x i8]` 写死；mark 数组 `[8192 x i32]` 也要按比例放大）；
  2. 根集合包含 `jref` 类型的栈槽。LGC 已经是精确根 + 块内保守扫，**非搬移**，
     所以 `jref` 在 GC 期间保持有效 —— 这与 Java 对象图兼容。
- 单线程阶段不做写屏障；分代/并发不做（`docs/225` §3.1 的理由同样适用）。

### 7.3 启动与入口

```
PE/ELF 入口 __entry
  → _start（Loment 程序自己的入口）
    → __jrt_init()            初始化类元数据、静态区、stdout 句柄
    → <用户入口类的 clinit>    惰性触发的第一条
    → 用户代码
```

Java 库如果有 `public static void main(String[])`，`loment java pack` 可以生成一个
`_start` 包装；但**默认不生成**：入口仍由 Loment 程序决定，避免一个 jar 抢走进程入口。

### 7.4 Windows 直调：具体路径

在 Windows 上：

```bash
loment build app.lomt --java-lib deps/com.example.foo -o app
```

实际发生的事：

1. `loment-driver.exe app.lomt` → `app.ll`（现有路径，不动）。
2. 新工具 `loment-java`（Windows 上是 `loment-java.exe`）读清单与 jar →
   `foo.<hash>.ll`（缓存命中则跳过）。
3. `loment-lomelf.exe app.ll foo.<hash>.ll jrt.ll app.exe` —— **多个 IR 输入**。
4. 运行 `app.exe`。不需要 WSL、clang、`java.exe`、`jvm.dll`。

PE 侧的已知边界（以 `tools/lomelf.py` 的 `PE_SYSCALLS` / `PE_DLLS` 为准；
`tools/loment_pe_test.py` 的头注还停在旧版，别拿它当现状）：

- **29 个** syscall 派发号（`PE_SYSCALLS`，`tools/lomelf.py:1979-2011`），
  文件 / 内存 / 进程 / net 四族都有。`jrt` 只需要
  `exit/write/read/close/openat/brk/getdents64/newfstatat` 这 8 个；**禁止裸 syscall**，
  统一走 `jrt/sys` 一层。用了表外的号在 PE 上是静默 `-1`，所以这一层要收敛。
- 栈对齐由 `__win_syscall` 垫片负责；`jrt` 不手写汇编。
- 导入表是 **`kernel32` + `ws2_32`** 两个 DLL（`tools/lomelf.py:1932-1941`；
  `ws2_32` 是联网垫片，所有 PE 产物都带）。**判据要扫描产物的导入表，确认没有
  `jvm.dll` / `java.dll`，而不是要求"只有 kernel32"**。
- `brk` 上限 64 MiB；超过就报错。P3 如果扩到 `VirtualAlloc`，要同时改 Python 与自举
  两份垫片，并重算冻结的 `win_shim_data.lomt`。

## 8. 安全边界：验证器是硬门槛

字节码降到 IR 之后，**内存访问是裸的**。如果信任一个未验证的 jar，它就能读任意地址 ——
这会把 Loment 的安全承诺直接打穿。所以验证器不是"以后再加"的优化，是**第一版的一部分**。

### 8.1 必须检查的

- **结构**：魔数、版本、常量池 tag/下标、UTF-8（modified UTF-8）、描述符、属性长度、
  代码长度、`max_stack` / `max_locals`、异常表范围。
- **控制流**：所有分支目标落在指令边界；没有 fallthrough 到代码外；`tableswitch` /
  `lookupswitch` 的 default 与 case 合法。
- **类型**：局部变量与操作数栈的数据流合并；`StackMapTable` 与推导结果一致；未初始化
  对象（`uninitializedThis`）的规则；`invokespecial` 只调构造器/私有/超类。
- **访问控制**：`private/protected/public` 与包边界；`final` 字段只在本类构造器里赋值。
- **资源上限**：类数、方法数、单方法代码长度、常量池大小、总 IR 体积 —— 防止一个 jar
  在编译期把工具撑死。

### 8.2 降级器的类型纪律

- 字段偏移来自**类元数据**，不是字节码里的任何算术。
- 数组元素宽度来自 `class_id` 的元数据，`xaload/xastore` 不能自己算偏移。
- Java 字节码没有指针算术；只要验证器通过，降级产物就不会出现"字节码指定的任意地址"。
- 运行时检查照 Java 语义发：空引用、数组越界、负数组长度、除零、类转换失败、监视器。
  这些既是语义，也是安全网。

### 8.3 能力域（设计项，不是已兑现）

Loment 的 `capability` 是编译期概念。生成的 Java IR 不经过 L1 检查器，所以**不能声称**
"Java 库自动受能力域约束"。P3 的做法是：给 `jrt/sys` 的每个出口（open/read/write/brk）
加一个能力域，链接时要求 Java 库清单声明它需要哪些；生成器在调用点插入 `guard` 等价物
（IR 层的能力表检查）。在它落地之前，文档与 CLI 都只能说"Java 库通过 jrt 的固定出口访问
系统"，不能说"受能力域保护"。

## 9. 分阶段与判据

每个阶段都遵守"判据先红"：新判据在未实现的树上必须**因为功能不存在而红**（例如
`--java-lib` 今天是 unknown option），不是靠下游替它挡。

### P0 · 最小闭环（先证明 Windows 直调）

范围：单个 `.class`（无依赖）、静态方法、整型/`long`、分支、循环、`return`。
没有对象、数组、字符串、异常、类初始化。

判据：

| # | 判据 | 断言 |
|---|---|---|
| C1 | `test_no_java_on_path` | PATH 收成只剩 python/系统目录；`where java` 失败；`loment build --java-lib` 仍成功 |
| C2 | `test_pe_runs_natively` | 产物在 Windows 原生跑，stdout + 退出码等于期望；不经过 WSL/clang |
| C3 | `test_pe_imports_have_no_jvm` | 扫描 PE 导入表，只有 `kernel32`，没有 `jvm.dll` / `java.dll` |
| C4 | `test_ir_is_deterministic` | 同一 jar 连续两次 `pack`，`.ll` 逐字节相同；顺序打乱的 zip 成员也相同 |
| C5 | `test_unsupported_opcode_is_an_error` | 含浮点/`jsr` 的方法 → 非零退出 + 指到类/方法/偏移的诊断 |
| C6 | `test_declaration_resolves_to_internal_call` | 合并后的 IR 里，接口符号的调用点不再走 extern；PE 与 ELF 结果一致 |
| C7 | `test_bad_class_rejected` | 魔数/常量池/栈图损坏的 class → 验证器拒绝，不崩溃 |

P0 的"不做"要写进 CLI 的 `--help` 与错误信息：不支持对象、字符串、异常。

### P1 · 对象、数组、字符串、异常、虚分派

范围：构造器、实例字段、静态字段、对象数组与基本类型数组、`String` 字面量与
`length/charAt/equals/hashCode`、`StringBuilder`、`Throwable` 家族、`try/catch/finally`、
`invokevirtual/interface` 的闭世界去虚化、惰性 `<clinit>`、`System.out/println`。

判据：

| # | 判据 | 断言 |
|---|---|---|
| C8 | `test_virtual_dispatch_closed_world` | 一个基类 + 三个子类的调用点，四组输入都命中正确实现 |
| C9 | `test_exception_propagation` | 跨三层调用传播、`finally` 覆盖、未捕获时退出码与消息正确 |
| C10 | `test_class_init_order` | 与真 JVM 的 `<clinit>` 顺序/副作用逐行一致 |
| C11 | `test_object_graph_survives` | 对象互相引用 + 数组，跑完后结果正确（P1 无 GC，长生命周期） |
| C12 | `test_three_way_oracle` | 真 JVM（开发机有 Java 时）/ 字节码解释器 / AOT 产物三方输出一致；JVM 不在时 SKIP 并计数 |
| C13 | `test_pe_and_elf_agree` | 同一份 IR 的 ELF（WSL）与 PE（Windows）stdout + 退出码一致 |

C12 的夹具必须是**真 javac 产出的 jar**，连同源文件、编译命令、JDK 版本、jar 哈希一起入仓；
测试运行时不需要 Java（用录好的 golden）。这符合"夹具要用真实现的产物"。

### P2 · jar、`java.base` 子集、`invokedynamic`

范围：zip/deflate 读取（`std/deflate`）、多类 jar、接口默认方法、装箱、
`StringConcatFactory` 展开、`Arrays`/`Objects`、`ArrayList`/`HashMap` 的最小实现、
`brk` 大堆。

判据：

| # | 判据 | 断言 |
|---|---|---|
| C14 | `test_jar_identity_stable` | 重新压缩/换时间戳后身份哈希不变；改一个字节的类 → 哈希变 |
| C15 | `test_export_surface_enforced` | 调清单外的方法 → 链接期报错，不是运行时崩溃 |
| C16 | `test_native_binding_required` | 未登记的 `native` → 链接期报错，并指出方法 |
| C17 | `test_string_concat_recipe` | Java 9+ 的字符串拼接与真 JVM 逐字符一致 |
| C18 | `test_real_library_end_to_end` | 一个真实小型纯 Java 库（如 CRC32/JSON tokenizer）的公开 API 跑通 |

### P3 · GC、能力域、lambda、浮点、线程

范围：参数化 LGC arena、`jref` 根、能力域出口、`LambdaMetafactory`、软 `f64`/后端 SSE、
真实监视器与线程（若语言线已具备线程）。

判据：

| # | 判据 | 断言 |
|---|---|---|
| C19 | `test_gc_keeps_live_objects` | 分配超过 `sealed` 档的 64 KiB（走 `brk` arena）后活对象仍正确；垃圾被回收（用确定性计数，不用墙钟） |
| C20 | `test_capability_denied` | 未声明文件能力的 Java 库调用 `open` → 链接期拒绝 |
| C21 | `test_lambda_capture` | 捕获变量的 lambda 在多次调用后结果正确 |
| C22 | `test_float_semantics` | `float`/`double` 的 NaN、-0.0、舍入与 JVM 一致 |

### 9.1 工作量的量级（诚实账）

| 件 | Python 参考实现 | 自举孪生 |
|---|---|---|
| class 文件解析 + zip/deflate | 2,000–2,500 行 | 同量级；受 `SLOT` / `TB_LBL_MAX` 固定容量约束 |
| 验证器 | 1,500–2,500 行 | 同量级；是安全关键，不能省 |
| 降级器 | 3,000–4,000 行 | 同量级 |
| `jrt` | 3,000–5,000 行 Loment | 同一份源码，不需要孪生 |
| IR 链接器 | 300–600 行 | 必须两份（`tools/lomelf.py` + `loment/tools/lomelf.lomt`） |
| CLI / 清单 / 缓存 | 800–1,200 行 | 两份包装器（POSIX + Windows） |
| 判据 | 1,500–2,500 行 | 同一套 |

**P0 是唯一能快速拿到的演示**（约 2–3k 行）；P1 才是一个"能调真实小库"的版本。
不要把它说成"几个月就能完整兼容 Java" —— 那是假的。

### 9.2 风险与前置

| 风险 | 影响 | 处理 |
|---|---|---|
| PE extern 是 System V | 参数错位、假结果 | IR 合并把 `declare` 解析成内部直调（C6） |
| 默认 64 KiB 语言堆 | 对象图立刻 OOM | `jrt` 走 `brk` arena；P3 参数化 LGC（`hosted` 的 1 MiB 要宿主 C 工具链，不选） |
| 没有间接调用 | 虚分派/回调做不了 | 闭世界去虚化 + 直调分派；需要时再给两份链接器加 `call ptr` |
| 验证器有洞 | 字节码越界读写 | 把它当安全边界；差分模糊测试 + 拒绝优先 |
| `java.base` 面太大 | "这个库调不了" | 清单化的 supported 列表；未实现就报"未解析类" |
| `invokedynamic` 普遍 | 现代 jar 大量拒绝 | P2 先做 `StringConcatFactory` |
| 自举容量 | 大 jar 编译时段错误 | 先量 `SLOT` / 标签表余量（仓库有既有教训）；分模块编译 |
| 生成物纪律 | 种子 diff 淹没手写改动 | 遵照 `CLAUDE.md`：机械产物单独提交，种子重生成不手合 |

## 10. 不做什么（第一版）

- **不做** JNI、`jvm.dll`、`libjvm`、任何"运行时 Java"。
- **不做**反射、动态代理、`Class.forName`、自定义 `ClassLoader`、`ServiceLoader`。
- **不做**完整 JDK / `java.base` 全量；只做清单里的子集。
- **不做** AWT / Swing / JavaFX / 网络 / 序列化。
- **不做**任意 jar 的"即插即用"：必须闭世界、必须通过验证、必须声明导出面。
- **不做**浮点（P3 之前）、线程（P3 之前）、`finalize` / 弱引用。
- **不声称**"Java 兼容"。对外的准确说法是：**"把 Java 库在构建期编译进 Loment 原生产物，
  不需要 JVM"**。

## 11. 附录

### A. 清单与接口的最小例子

`loment-java.json`：

```json
{
  "abi": "JL-A/1",
  "name": "com.example.crc",
  "jar": "crc.jar",
  "jar_sha256": "b7c0…",
  "exports": ["com/example/CRC32.update(I[B)I"],
  "natives": {},
  "java_base": "jrt-0.1",
  "class_version_max": 65,
  "deny": ["java/lang/reflect/*"]
}
```

`interface.lomt`：

```rust
module java_com_example_crc

pub fn crc32_update(state: i32, data: ptr, len: u32) -> i32 {
    return __jl_com_example_CRC32_update_I_ary(data, len, state);
}

extern fn __jl_com_example_CRC32_update_I_ary(data: ptr, len: u32, state: i32) -> i32;
```

数组在边界上用 `(ptr, len)` 传递；接口包装在调用前检查 `len` 与数组头一致，
避免 Loment 侧传一个与 Java 数组长度不符的切片。

### B. 错误分类（第一版）

| 码 | 含义 | 例子 |
|---|---|---|
| `J001` | class 文件结构错 | 魔数不对、常量池下标越界 |
| `J002` | 验证失败 | 栈高度在合并点不一致、未初始化对象 |
| `J003` | 不支持的字节码/特性 | `float`、`invokedynamic`（P1 前）、`jsr` |
| `J004` | 未解析的类/方法/字段 | 清单外调用、`java.base` 未实现 |
| `J005` | 清单与 jar 不一致 | 哈希不符、导出方法不存在 |
| `J006` | `native` 未绑定 | 没有 `natives` 条目 |
| `J007` | 链接冲突 | 重复定义、`_start` 在库 IR 里、保留前缀被占用 |

错误必须带 `类.方法(描述符)` 与字节码偏移；**不许只打印一个"unsupported"**。

### C. 与既有文档的关系

- `docs/173` 的 `extern fn` / `--link` 继续服务 C ABI 一族；本设计**不替换**它。
- `docs/183` 的"库 = 声明面 + 实现 + 身份"在这里被具体化成 jar + 清单 + IR + 哈希。
- `docs/188` 的 `jtrans` 是**源码**转译，解决"用 Java 写法写 Loment"；
  本设计解决"调用已经编译好的 Java 库"。两者不重叠。
- `docs/212` 的"预编译内核仓"在这里对应"预编译 IR 模块仓"：`lompi` 多认一种成员。
- `docs/225` 列出的运行期空白（无线程、无搬移、无并发）在第一版仍然成立，不假装消失。

### D. 第一个演示的命令序列（P0 验收）

```bash
# 开发机（有 JDK）一次性产出夹具；产物入仓，测试机不再需要 Java
javac --release 8 -d classes src/com/example/MathLib.java
jar --create --file fixtures/mathlib.jar -C classes .

# 构建机（无 Java）
loment java pack fixtures/mathlib.jar -o deps/mathlib
loment build app.lomt --java-lib deps/mathlib -o app
./app.exe
```

`app.lomt` 只 `use java_com_example_mathlib`，调用 `mathlib_add(2, 3)`；期望 stdout
与退出码由**真 JVM** 在夹具生成时录下。构建与运行的 PATH 里没有 `java`、`javac`、
`jar`、`wsl`、`clang`。
