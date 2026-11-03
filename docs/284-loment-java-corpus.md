# 284 · Java AOT 语料实测：70 只真实 jar

> 状态：**实测报告**（2026-11-03）。工具 `tools/lomjava_corpus.py`，语料坐标与哈希在
> `tools/fixtures/java-corpus.txt`（jar 本身不入树）。上游设计见 `docs/282`（AOT 内核）
> 与 `docs/283`（lompi 发行层）。

## 1. 问题

`loment_java_test` 的夹具是**我们自己写的** Java：整型、字符串、对象、异常、继承、
接口、跳转表。它们证明这条链能跑，但证明不了"真实世界的 jar 能过多少"。
所以拿 50 只大众库 + 20 只小众库（Maven Central，`--fetch` 可重放）直接压一遍前端。

## 2. 怎么跑

```
python tools/lomjava_corpus.py --fetch --dir <DIR>     # 下载并核哈希
python tools/lomjava_corpus.py --dir <DIR> --json r.json
```

每只 jar 在**独立子进程**里跑三个阶段：`parse`（结构，`J001`）、`verify`（验证器，
`J002`）、`lower`（降级，`J003`）。报的是**第一条**失败 —— 一只 jar 修掉一个门，
下一次会撞上下一个门，所以"通过数"是单调的（只会涨）。

## 3. 结果（2026-11-03）

**1/70** 只 jar 能整只过完前端 —— `jcip-annotations`（纯注解，没有方法体）。
其余 69 只按第一条失败归类：

| 条数 | 桶 | 说明 |
|---:|---|---|
| 12 | `invokedynamic` / `LambdaMetafactory` | lambda、方法引用；子集只认 `StringConcatFactory` |
| 14 | `float` / `double` | 按设计拒（`J003`），但**是最大的单一语言缺口** |
| 10 | `J002 other` | 验证器与 `StackMapTable` 的分歧（见 §5） |
| 10 | `? other` | 调用 JDK 类（`java/util/Collections`、`java/lang/AssertionError` …）、返回类型 |
| 3 | `monitorenter` | `synchronized` |
| 6 | 栈指令 / 转换 | `dup2`、`swap`、`wide`、`dload_0`、`i2b` 之类 |
| 4 | 其余一次性 opcode | `tableswitch` 之外的长尾 |

**结论**：以今天的前端，真实 jar 基本过不去。卡点不是"某个指令没写"，而是三件结构性的
事：**lambda（`invokedynamic` 全族）**、**浮点**、**JDK 表面**（任何 `java.util.*` 调用都
不在闭包里）。这三件都在 `docs/282` §9 的 P2 里，不是 P0/P1。

## 4. 这一轮被语料抓出来的真缺陷（都已修 + 有判据）

| 缺陷 | 症状 | 判据 |
|---|---|---|
| `StackMapTable` 的 `Uninitialized` 偏移读成 u1（规范是 u2） | 帧流读歪，报"与自己无关"的 `bad verification type`；commons-collections4 等一批 jar 解析失败 | `test_stack_map_uninitialized_offset_is_two_bytes` |
| `aconst_null` 的栈增量为 0 | `null` 之后的高度少一，`ifnull` 处"栈下溢" | `test_ifnull_lowers`（同时钉住 `ifnull`/`ifnonnull` 的降级） |
| `aconst_null` 根本没有降级路径 | `J003: opcode aconst_null` | 同上 |
| 匿名内部类构造器里对 `uninitializedThis` 的 `putfield` 被拒 | `expected a reference, found uninitialized-this`；一批 jar 的 `$1.<init>` 全中 | 由 `loment_java_test` 全量 + 语料复跑覆盖 |
| 方法**返回引用**被拒（`areturn` 没写） | `unsupported return type in ()Ljava/lang/Object;`；`values()`、`toString()` 全中 | `test_reference_returns_lower` |
| 类数上限 1024 太小 | commons-math3 / freemarker / rxjava 整只被拒 | 上限抬到 8192（`J002` 仍管着） |
| 验证器不动点不单调 | 大方法报 `did not reach a fixed point` | 处理器状态只往 `top` 走 |
| `ldc` 的类常量（`Foo.class`）被当成"坏常量" | 枚举 `valueOf` 一类路径全中 | 降成类号 token |

## 5. 仍缺的（已开 issue）

- lambda / 方法引用（`invokedynamic` × `LambdaMetafactory`）。
- 浮点（`float`/`double`）：十几只 jar 的第一门就是它。
- `synchronized`（`monitorenter`/`monitorexit`）。
- 验证器与 `StackMapTable` 的分歧：有的地方我们**推得比帧弱**（帧说 `i64`、我们推
  `top`），这说明我们的数据流在某类合并点上丢精度；已按 jar 记坐标，逐个查。
- JDK 表面：`java.util.*` / `java.lang.*` 不在闭包里 —— 这是 P2 的 JDK 子集，
  不是前端的缺陷。

## 6. 复现

`tools/fixtures/java-corpus.txt` 是**唯一真源**：70 行坐标 + 字节数 + sha256。
换机器只要 `--fetch`，结果应当逐只相同（前端是纯 Python，没有平台分支）。
