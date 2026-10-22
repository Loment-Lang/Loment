# 282 · Java 库作为 lompi 外源包：AOT 成员（无 Java）

> 状态：**设计稿**（2026-10-20）；外源索引/流式取件/锁 v2 已落地（#1056，2026-10-21）；`runtime jrt` 随包发在 `share/loment/jrt/0.1.0/`，包装器每个闭包只加一次（#1060 第一块，2026-10-22）。本文取代 `docs/281` 的**发行与链接层**；
> `docs/281` 的字节码 → IR 内核仍然有效，`--java-lib` 降级为本地/离线逃生舱。
> 基线：`origin/main` `8cdce0cf`（lompi 0.2.0；`docs/222` 外源包模型、`docs/236` 联网）。
> 一句话：**Java 库是 lompi 外源包的一种 `kind`。`lompi install` 下载并验证它，
> 安装时用 Loment 写的 AOT 编译器把 jar 消成可移植 IR；`use <名字>` 之后构建自动
> 把它链进产物。构建不需要 JDK，运行不需要 JVM，Windows 上直接出 `.exe`。**

## 0. 先纠正两条口径

### 0.1 lompi 已经能联网

上一版设计里我写了"lompi 自己不能联网"——**那是本地旧分支的注释，不是事实**。
`origin/main` 的 lompi 0.2.0 已经能自己 `GET`（`docs/236` §1）：取
`lompi-index.txt`，按索引逐个取文件、逐个校验 sha256、落盘；走
`socket/connect/sendto/recvfrom`，PE 与 ELF 同一条路，PE 侧由垫片的 `ws2_32` 顶着
（`docs/234`）。

它不能做的是另外四件事（`docs/236` §3）：`https://`（零 TLS）、主机名（没有解析器）、
3xx 重定向、以及**一次装不下的响应**——单文件上限 **256 KiB**
（`NET_FILE_CAP`，`lpi_cli.lomt:1522`）。所以新设计把联网当主干，同时把"jar 要流式
落盘"写成必做项。

### 0.2 已有 `docs/222` 的外源包模型，本文不另起一套

`docs/222` 已经把"外源库"定义成 lompi 的头等公民：

```
<store>/<name>/<version>/
  <name>.lomt     声明单元（普通 Loment，里面是 extern fn）
  foreign.lomp    清单（生态 / 成员文件 / 取件配方 / 运行期需求）
  libfoo.a        实现（进身份）
```

并且明确"**二进制也要进身份**"。它给出的 Java 实现是 **JNI 垫片 + `libjvm.so`**
（`docs/222` §8.1：JNI 的 `JNIEnv` 是函数指针表，必须用 C 垫片把间接调用摊平）。
那条路已经在 `tools/loment_hosted_test.py` 里真跑通：`Hello.compute(1) → 2037`。
**但它需要装 Java。**

本文要做的不是推翻 `docs/222`，而是给它加一个 **Java 的 AOT 成员**：

| | `docs/222` 的 JNI 路线 | 本文的 AOT 路线 |
|---|---|---|
| `choose port` | `hosted` | `sealed`（默认） |
| 运行时依赖 | `libjvm.so` + 一个 C 垫片 + 已装的 JDK/JRE | **无** |
| Java 的间接调用 | 由 C 垫片摊平 | **AOT 直接消掉**（虚调用闭世界去虚化） |
| Windows | PE 侧还没有 hosted/FFI | **同一份 IR 直接出 PE** |
| 版本/身份 | jar 进身份 | jar 进身份（同一套外源包模型） |

两种后端可以**共存在同一个包里**：`backend aot` 是默认，`backend jni` 是 Linux 上
"有 Java 且要走 hosted"时的可选成员。用户要的"没有 Java"由 AOT 后端满足。

**AOT 为什么能不要 JNI 垫片**：`docs/222` §8.1 说 Java 边界"必然"要一段 C 垫片，
因为 `(*env)->FindClass(...)` 是间接调用，而后端不支持间接调用。这个判断在 **JNI 路线**
上成立；AOT 路线把整个 JVM 消掉之后，**没有 `JNIEnv` 函数表**，每一次 Java 调用都变成
IR 里的直接调用，虚调用则由闭世界去虚化生成直调分派（§6.3）。所以"必然要垫片"只对
运行期嵌入成立，不对本文成立 —— 这正是两条路线的分界，不是文字游戏。

## 1. 为什么是"全新"的，而不是给 278 加一个开关

| | `docs/281` | 本文 |
|---|---|---|
| Java 库从哪来 | 本地目录里的 `.jar` | **lompi registry**（HTTP 索引） |
| 怎么声明 | `loment build --java-lib deps/foo` | **`use foo`**，没有额外开关 |
| 包模型 | 无 | `docs/222` 的外源包（声明 + 实现 + 身份） |
| 版本与身份 | 用户自己管的文件 | lompi 的 **内容身份 + 锁文件** |
| 依赖闭包 | 不处理 | lompi 解析并拒绝 JVM 类的重复定义 |
| 安装时做什么 | 无 | 下载 → 校验 → AOT 编译 → 写 `deps/<name>/` |
| 链接谁 | `--java-lib` 指定的那一个 | 构建扫描 `deps/*/link.txt`，自动全部加入 |
| 离线 | 手工拷 jar | 锁文件 + 缓存，`--from-lock` 可复现 |
| 与 `docs/222` | 无关系 | **同一外源包的 Java `kind`** |

一句话：**278 把 Java 当"外部对象"，本文把 Java 当"包"，而且接进 222 已有的包模型。**

## 2. 目标与非目标

### 2.1 目标

1. **一条命令**：`lompi install guava --into deps`，之后源码里 `use guava`。
2. **构建期不需要 Java**：下载、验证、AOT 都由 Loment 工具链完成。
3. **运行期不需要 Java**：产物里没有 JVM、没有 `jvm.dll` / `java.dll`；PE 导入表是
   `kernel32` + `ws2_32`（联网垫片是公共部分），没有 JVM 库。
4. **Windows 与 Linux 同一份包**：jar 平台无关，AOT 产物是平台无关的 IR。
5. **可复现**：锁文件同时钉住 jar、清单、IR 与编译器版本；`verify` 逐字节对账。
6. **安全默认**：未通过验证的 jar 不进链接；明文索引必须带外钉住。

### 2.2 非目标（第一版）

- 不直接连 Maven Central：它要 HTTPS + DNS，而工具链两样都没有。**第一版连的是
  Loment 的外源包索引**；Maven 只作为索引服务端的上游。
- 不做 JVM 的类加载器隔离：闭包内同名类一律拒绝，不模拟多 ClassLoader。
- 不做反射、动态代理、`Class.forName`、`ServiceLoader`。
- 不做完整 JDK：只做清单里声明的 `java.base` 子集。
- 不做 `float` / `double`（第一版），不做线程。
- 不把"能下载"说成"任意 Maven 库都能下载"：**只有被索引收录、通过验证的包**。

## 3. 包模型：外源包的 `kind = java`

### 3.1 目录形状

```
deps/guava/
  guava.lomt              # 声明单元；编译器 `use guava` 找的就是它
  foreign.lomp            # 清单标签：name / version / kind / ecosystem / runtime
  foreign.txt             # 规范的行式清单：成员文件、导出面、依赖、native
  lib.jar                 # 实现（原样保留，用于复算身份与重编译）
  guava.ll                # AOT 产物（安装时生成；可缓存、可重建）
  link.txt                # 构建期要加入的 IR 清单 + 哈希
```

`guava.lomt` 必须是合法入口（文件名 = 包名，`module guava` 与文件名一致），
这样 `pkg_check` 与编译器的 `use` **一行都不用改**。jar / IR / 清单是**额外载荷**，
今天的 `pkg_list_sources` 只列 `*.lomt`，不会把它们算进源码哈希——所以身份要按
`docs/222` §5 的"二进制也要进身份"单独扩展（§3.3）。

### 3.2 `foreign.lomp` + `foreign.txt`

`foreign.lomp` 沿用 lompi 的标签扫描（Loment 源码，函数返回字符串）：

```rust
module foreign

pub fn name() -> str { return "guava"; }
pub fn version() -> str { return "33.0.0-jre"; }
pub fn kind() -> str { return "java"; }
pub fn ecosystem() -> str { return "java"; }
pub fn runtime() -> str { return "none"; }   // AOT 后端：不要求运行期
```

`foreign.txt` 是**排序后的行式文本**，每行一个事实；它的 SHA-256 进身份：

```
# lompi foreign v1
kind java
abi JL-A/1
backend aot
artifact jar lib.jar <jar-sha> <jar-merkle>
artifact interface guava.lomt <sha>
artifact manifest foreign.lomp <sha>
maven com.google.guava:guava:33.0.0-jre
entry com/google/common/base/Preconditions
export com/google/common/base/Preconditions.checkNotNull (Ljava/lang/Object;)Ljava/lang/Object;
dep failureaccess 1.0.2
native com/example/Sensor.read ()I __jl_native_Sensor_read
class-version-max 65
```

规矩：

1. **`jar-merkle` 是 jar 内容的 Merkle 根**：按 zip 成员名排序，逐成员
   `名字 0x00 SHA-256(内容) 0x00` 喂进 SHA-256。不用整个 zip 的字节哈希，因为
   时间戳/压缩级别/成员顺序都会变，而类内容没变。
   **它与 `jar-sha` 不是同一个东西**：`jar-sha` 是传输完整性（整个文件逐字节），
   `jar-merkle` 是身份（内容语义）。两个都要验。
2. **`export` 只列公开面**；清单里没有的方法不保证可调。
3. **`dep` 写包别名与版本**；实际的子身份由 lompi 解析后混入实例身份。
4. **`native` 是唯一出口**；没有登记就拒绝。
5. **所有行按 ASCII 排序**（`#` 头除外），生成器负责；否则同一份内容两种顺序会得到
   两个身份。
6. **`backend` 是后端标签**：`aot`（默认，无 Java）或 `jni`（`docs/222` §8 的
   hosted 路线，要求 `runtime jvm`）。同一个包可以同时提供两个后端；第一版只要求
   `aot` 存在。

### 3.3 身份：外源包公式，域分隔

`docs/222` §5 已经定了"二进制也要进身份"。把它写成类型化的公式：

```
loment:   id = H( own_source_hex ⊕ 0x00 ⊕ sorted(edge_name ⊕ 0x00 ⊕ child_id ⊕ 0x00) )
foreign:  id = H( "lompi-foreign/1\0" ⊕ kind ⊕ 0x00
                  ⊕ artifact_merkle_hex ⊕ 0x00
                  ⊕ manifest_sha256_hex ⊕ 0x00
                  ⊕ sorted(edge_name ⊕ 0x00 ⊕ child_id ⊕ 0x00) )
```

- 旧 loment 公式**一个字节不动**：现有 store 的 `lompi show` / 锁文件继续成立。
- 外源身份以 `lompi-foreign/1\0` 开头做**域分隔**，与源码身份不可能被构造混淆。
- `artifact_merkle`：Java 是 jar Merkle；C/C++ 等是 `libfoo.a` 的 Merkle；
  `kind=obj` 是单文件 sha256。
- **编译器版本不进包身份**，但要进**产物身份**（§5.3）：同一份 jar 用不同编译器编出的
  IR 可以不同，锁文件必须把两者分开钉住。

### 3.4 lompi 记录要加的东西

`lpi_pkg.lomt` 的包记录目前假设 payload 是 `.lomt`。按 `docs/222` §5，需要加三个字段
（按命名偏移，保持标量签名）：

| 字段 | 用途 |
|---|---|
| `kind` | 0 = loment / 1 = java / 2 = c / 3 = obj … |
| `payload_root` | 源码 Merkle / jar Merkle / `.a` Merkle |
| `link_manifest` | 可选：`link.txt` 的路径与哈希 |

`pkg_list_sources` 保持"只列 `.lomt`"——**它是给源码扫描与 `pkg_check` 用的，
不是身份的全部**。新增 `pkg_list_payload` 给身份与拷贝用：`kind=java` 时列
`*.jar`、`foreign.lomp`、`foreign.txt`、`*.ll`、`link.txt`。

`cli_copy_pkg` 今天只拷 `*.lomt` + `pkg.lomp`（`lpi_cli.lomt:1938`）；外源包必须走
`pkg_list_payload`，否则装出来的包只有接口，没有 jar 与 IR。这是一处**必须改的
静默错**：不改的话 `lompi install` 会"成功"但构建时链接不到任何东西。

## 4. Registry：源码索引 + 外源索引

### 4.1 索引格式

保留 `lompi-index.txt`（源码包）不动。新增一个并列文件，**沿用同样的四段格式**：

```
# lompi-foreign-index.txt
# name version manifest-path manifest-sha256
guava 33.0.0-jre guava/33.0.0-jre/foreign.txt <sha>
```

索引只指向 `foreign.txt`；`foreign.txt` 里的 `artifact` 行再列出 jar / 接口 / 清单
的路径与哈希。这样：

- 索引格式与 lompi 现有解析器一致（四段），不必为外源包发明新解析；
- 一个包可以有多个成员文件，仍然逐个 sha256 校验；
- 旧 lompi 只读 `lompi-index.txt`，新增索引不破坏兼容。

`lompi fetch` 同时取两个索引：源码索引照旧；外源索引存在就一起取。取到
`foreign.txt` 后，按它的 `artifact` 行继续取成员文件，**每个都校验索引/清单里的
sha256**（`docs/236` §1 的同一条规矩）。

完成标记（`lompi-fetch-complete`，`docs/236` §7.2）只在**两个索引 + 所有 artifact
都取齐且逐个校验过**之后才写；任一步失败就留 0 字节。否则一次半截的 jar 下载会被
`install` 当成完整缓存。

### 4.2 服务端做什么（不在 lompi 里做）

- 把 Maven 坐标解析成 jar（POM 的传递依赖在**服务端**展开），写进 `foreign.txt`。
- 生成 `foreign.lomp` / `foreign.txt`（`export` 从 jar 的公开方法导出；`dep` 从 POM
  的传递闭包展开）。
- 可选：预编译 IR + 构建证明（§5.3）。第一版**不发布 IR**，让安装端自己编。
- 托管 jar 与清单；明文 HTTP，IPv4 字面量（或者由用户配一个本地反代）。

**为什么不给 lompi 写 POM 解析器**：POM 是 XML + 属性继承 + 版本范围 + exclusions，
那是另一个包管理器。把它放在服务端，lompi 只认"已经平铺好的依赖行"，与它今天的
"只读索引"模型一致。

### 4.3 主机名与 HTTPS

第一版明确不支持公网域名。三条可选路线：

1. **固定 IPv4 的镜像**（最快）：索引服务提供一个 IPv4 字面量；用户把它写进
   `lompi.conf` 的 `registry()`。
2. **加 DNS**（中等）：运行时用 UDP 53 发 A 查询，拿到 IPv4 再连。这能解决主机名，
   不解决 TLS。
3. **加 TLS**（大工程）：握手、证书链、密钥交换。不是这一版的事。

**不要**用"打印 `git clone` 计划"冒充 Java 包下载：jar 不是 git 仓库，用户也没有理由
为了一次安装去手工 clone。第一版宁可说"这个 registry 不认"，也不假装能下。

## 5. 安装流水线：下载 → 验证 → 编译 → 落盘

`lompi install guava --into deps`（或 `lompi java add guava`）做这些步：

### 5.1 下载

1. 取 `lompi-foreign-index.txt`（可选带外钉住 `--registry-sha256`）。
2. 按名字选**最高版本**（与 lompi 现有规则一致：逐段数字比较，`0.10.0 > 0.9.0`）。
3. 下载 `foreign.txt`（小文件，走现有整块路径）；按它的 `artifact` 行取
   `foreign.lomp`、`guava.lomt`（若有）与 `lib.jar`。
4. 下载 `lib.jar`——**必须流式**：现有 `net_http_get` 把整条响应收进
   `NET_FILE_CAP = 256 KiB` 的缓冲（`lpi_cli.lomt:1522`），而真实 jar 常有几 MB。
   新增 `net_http_get_to_file(...)`：`tcp_recv` 循环、分块写盘、**增量 SHA-256**，
   最后与 `foreign.txt` 里的 `jar-sha` 比对；中途失败就删掉半份（PE 没有 `unlink`，
   所以要写进一个临时名并在完成标记里记状态，或复用 `lompi-fetch-complete` 的
   完成标记思路）。

**这一步是本次设计里最具体的运行时改动**：不是"加大缓冲"，而是**不改缓冲、改落盘方式**。
把 256 KiB 的整块限制原样留在小文件路径上，jar 走流式，两者共用同一个 sha 校验。

### 5.2 验证

5. **jar 结构验证**：zip 中央目录、成员名、大小上限、无 zip 炸弹（解压比与总解压量上限）。
6. **字节码验证**：按 `docs/281` §8 的验证器；任何一条不过就拒装。
7. **清单与 jar 对账**：`jar-sha`（传输）与 `jar-merkle`（身份）都要重算并比对；
   `export` 的每个方法必须真存在；`dep` 的名字必须在解析闭包里。
   **`pkg_own_hash` 不能直接读整个 jar**：它今天用 256 KiB 的 `WS_FILE` 整块读
   （`lpi_pkg.lomt` 的 `pkg_own_hash`），超过就返回 0。外源包必须走**流式哈希**
   （边读边 `sha_update`），否则真实的 jar 一律被当成"读不了"。

### 5.3 编译

8. 调用 `loment-java`（源文件 `lomjava.lomt`；Loment 实现，不需要 Java）把 jar
   编成 `guava.ll`。
9. 产物身份：
   ```
   artifact_id = H( "JL-ART/1\0" ⊕ package_id ⊕ 0x00 ⊕ abi ⊕ 0x00
                    ⊕ backend ("aot") ⊕ 0x00
                    ⊕ compiler_version ⊕ 0x00 ⊕ ir_sha256 )
   ```
   锁文件同时记 `package_id` 与 `artifact_id`。编译器升级后，`verify` 会明确报
   "IR 由另一个编译器版本产生"，而不是假装一致。
10. **确定性**：同一 jar + 同一编译器版本，两次编译的 `.ll` 必须逐字节相同。
    类遍历、方法遍历、常量池顺序全部排序；不许有时间戳/临时路径进入 IR。

### 5.4 落盘

11. 写 `deps/guava/`：`guava.lomt`、`foreign.lomp`、`foreign.txt`、`lib.jar`、
    `guava.ll`、`link.txt`。
12. `link.txt` 是**构建期唯一的发现入口**：
    ```
    # lompi link v1
    ir guava.ll <sha256>
    runtime jrt 0.1.0
    ```
    构建包装器扫描 `deps/*/link.txt`，把列出的 IR 全部加入链接；`runtime` 去重后
    只加一次 `jrt.ll`。`jrt` 是**工具链随包提供**的运行期，不通过 registry 下载；
    `runtime` 行只负责版本选择。
13. 目录创建仍是 PE 的缺口：`--apply` 照旧先查目录、打印 `mkdir`。要么用户先建，
    要么另开一次"给 PE 垫片加 mkdir"的决定；**本文不把 mkdir 偷塞进安装**。

## 6. 链接：`use` 之后自动进产物

### 6.1 构建流程

```
loment build app.lomt

  1. loment-driver app.lomt → app.ll
     （`use guava` 解析到 deps/guava/guava.lomt，和普通库完全一样）
  2. 包装器扫描 deps/*/link.txt
  3. loment-lomelf app.ll deps/guava/guava.ll jrt/0.1.0/jrt.ll ... → app.exe
```

**没有 `--java-lib`。** 用户源码里只有 `use guava`；构建包装器根据 `link.txt` 自动加
IR。这样：

- Loment 库与 Java 库在**源码层长得一样**；
- 链接器不需要知道"这是 Java"；
- 删除一个依赖只需要删 `deps/guava/`，不会留下一个悬空的命令行参数。

### 6.2 符号解析

`guava.lomt` 里的 `extern fn __jl_...` 在合并时按 `docs/281` §4.3 解析：
**同一批 IR 里有 `define`，就改成内部直接调用**。这是 PE 上正确传参的前提
（PE 的 extern 调用点仍是 System V 寄存器，`lomelf.py:6168`；`--link` 在 PE 上今天
仍被直接拒绝，所以这处还没暴露，但 IR 合并会走到它）。

### 6.3 重复类检测（JVM 的平名字空间）

lompi 允许同名包多版本共存；JVM 不允许同一个全限定类名有两个定义（除非用多个
ClassLoader，而本文明确不做）。所以**安装闭包**必须做一次全局检查：

> 闭包里所有 jar 的 `com/foo/Bar.class` 全名收集起来；同一个全名出现在两个包实例里 →
> 硬错，打印两个包的名字、版本与身份。

这不是 lompi 的 Merkle 模型能自动解决的：身份不同不代表可以同时进同一个 JVM 名字空间。
把这条写成判据，别留到运行时 `LinkageError`。

## 7. 信任：明文索引必须带外钉住

lompi 已经能逐个校验文件 sha256，但**索引与索引里的哈希走同一条明文通道**
（`docs/236` §7.1）。对 Java 包，这比源码包更严重：jar 是**可执行代码**，
被换掉的索引可以同时换掉 jar 与它的哈希。

规则：

1. **外源包默认要求索引钉住**：`--registry-sha256 <64 hex>` 钉源码索引，
   `--foreign-index-sha256 <64 hex>` 钉外源索引（两个索引是两份文件，不能用一个
   哈希同时钉住）；或在 `lompi.conf` 里配固定的钉。没有钉 → 拒装，错误里给出获取
   钉子的三步（照抄 `docs/236` §7.1）。
2. **本地开发豁免**：`--insecure` 只对 `127.0.0.1` / `localhost` 生效，且打印一行警告。
3. **验证器是第二道门**：即使索引被钉，jar 仍要通过字节码验证；不许"哈希对就信"。
4. **锁文件是第三道门**：`--from-lock` 只装锁里钉住的 jar 与 IR；任何一项缺失或哈希
   变化 → 拒装。锁里还要记 registry 的**索引钉**（或至少记 jar 的 `jar-sha`），
   否则换一个 registry 就可能在"同一版本"下装到另一颗 jar；`--from-lock` 先查缓存，
   缓存没有才联网，联网时仍按锁里的哈希逐项验。
5. **签名（后续）**：在 SHA-256 之上加发布者签名（Ed25519），让索引可以来自不可信
   通道。第一版不做，但在清单里预留 `sig` 行。

## 8. Windows 直调

### 8.1 下载

lompi 在 PE 上已经能 `socket/connect/sendto/recvfrom`（`docs/234`/`236`）。
需要补的是**流式落盘**：

- `lpi_sys.lomt` 现在有整文件 `sys_read_file` / `sys_write_file`；流式要新增
  "创建 → 逐块写 → 关闭" 三个原语（PE 侧走 `openat/write/close` 垫片，ELF 侧走裸 syscall）。
- 增量 SHA-256 直接喂给 `lpi_sha` 的 `sha_update`，不额外缓冲整包。
- 失败时半份文件不能删（PE 没有 `unlink`）：写进 `<name>.part`，完成后再改名？
  PE 也没有 rename。退而求其次：缓存目录用完成标记（`lompi-fetch-complete` 的同一套
  思路），半份文件在下次 fetch 时被截断重写。

### 8.2 编译与运行

- `loment-java` 本身是 Loment 程序，在 Windows 上就是 `loment-java.exe`；不需要 JDK。
- 链接仍走 `loment-lomelf.exe`，同一份 IR 出 PE。
- **Java 产物**的导入表是 `kernel32` + `ws2_32`（`tools/lomelf.py:1932-1941`；
  联网垫片是所有 PE 产物的公共部分），**不含** `jvm.dll` / `java.dll`。判据扫描产物
  导入表，确认没有这两个 JVM 库 —— 不要写成"只有 kernel32"，那在 origin/main 上
  已经不对了。
- 堆：`jrt` 走 `brk` arena，PE 垫片给 64 MiB（`tools/lomelf.py:2126`）；超过就明确报错。
- 默认 `sealed` 档的语言堆是 64 KiB；`port hosted` 的 1 MiB 宿主 arena 要宿主 C
  工具链（`docs/228-hosted-heap.md` §3），**不是**这里的方案。

### 8.3 目录

`deps/guava/` 必须在安装前存在（PE 无 mkdir）。两条路：

- 保持现状：`lompi install --apply` 打印 `mkdir`，用户/脚本执行；
- 另开决定：给 PE 垫片加 `mkdir`（`docs/236` §6 说 `sys_can_mkdir()` 今天是**策略**，
  不是平台探针）。本文不替这个决定做主，但把"装不上"的症状与提示写清楚。

## 9. lompi 要改的代码（按文件）

| 文件 | 改动 | 判据 |
|---|---|---|
| `lpi_pkg.lomt` | 包记录加 `kind` / `payload_root` / `link_manifest`；新增 `pkg_list_payload`；`pkg_check` 认 `foreign.lomp` + `foreign.txt` | `test_foreign_package_passes_check` |
| `lpi_pkg.lomt` | `pkg_own_hash` 按 kind 分派；loment **逐字节不变**；外源走流式 artifact 哈希 | `test_loment_identity_unchanged` |
| `lpi_idn.lomt` | 域分隔的外源身份；依赖边照旧混入 | `test_foreign_identity_includes_artifact_and_children` |
| `lpi_cli.lomt` | `install` / `java add` 两索引；流式 jar；`--insecure`；`link.txt` 生成 | `test_install_java_from_http_registry` |
| `lpi_cli.lomt` | `net_fetch_registry` 取完索引后**跟随 `foreign.txt` 的 `artifact` 行**再取成员文件 | `test_fetch_follows_foreign_artifacts` |
| `lpi_pkg.lomt` | `pkg_label` 除 `pkg.lomp` 外也读 `foreign.lomp`（kind/ecosystem/runtime） | `test_foreign_labels` |
| `lpi_cli.lomt` | `index` / `show` 打出 `kind`（外源包与源码包在同一张表里可分辨） | `test_show_kind` |
| `lpi_cli.lomt` | `cli_copy_pkg` 走 `pkg_list_payload` | `test_install_copies_jar_and_ir` |
| `lpi_cli.lomt` | 锁 v2 行（kind + jar + ir + compiler）；v1 仍可读 | `test_lock_v2_roundtrip` |
| `lpi_sys.lomt` | 流式写原语（create/write/close） | `test_stream_large_jar` |
| `lompi.lomt` | `java` 子命令分发 | `test_cli_lists_java` |
| `tools/loment_dist.py` | 构建包装器扫 `deps/*/link.txt`，多 IR 输入 | `test_build_autolinks_deps` |
| `loment/tools/lomelf.lomt` + `tools/lomelf.py` | 多 IR 输入 + `declare`→`define` 解析（双实现） | `test_ir_link_resolves_declaration` |
| `loment/tools/lomjava.lomt` + `tools/lomjava.py` | AOT 编译器（278 的内核） | `test_aot_deterministic` |

**双实现纪律**：`lpi_*` 只有一份（lompi 本身是 Loment），但 `lomelf` 是两份，
`lomjava` 也按 278 §3 的"Python 参考 + Loment 自举"两份。改 `lomelf` 的合并逻辑时，
两边必须逐字节一致；种子按 `CLAUDE.md` 的纪律单独提交。

## 10. 分阶段与判据

### P0 · 从 registry 装一个 jar（Windows 先跑通）

范围：本地起一个 HTTP registry（`127.0.0.1`），只有一个包、一个静态整型方法；
`lompi install` 下载、校验、AOT、落盘；`app.lomt` 里 `use` 它；构建出 `.exe`。

判据：

| # | 判据 | 断言 |
|---|---|---|
| J1 | `test_install_java_from_http_registry` | 起真 HTTP 服务器；索引 + `foreign.txt` + jar；lompi 下载并校验；落盘内容逐字节相同 |
| J2 | `test_install_refuses_bad_jar_hash` | `foreign.txt` 里的 jar sha 改一位 → 拒装，且不落盘 |
| J3 | `test_install_without_java` | PATH 里没有 `java/javac/jar`；安装与构建仍成功 |
| J4 | `test_build_autolinks_deps` | `use guava` 后构建自动加入 `deps/guava/guava.ll`；不需要 `--java-lib` |
| J5 | `test_pe_runs_natively_no_jvm` | PE 原生跑；导入表是 `kernel32` + `ws2_32`；无 `jvm.dll` / `java.dll` |
| J6 | `test_install_copies_jar_and_ir` | 装出来的目录里 jar、清单、接口、IR 都在（防 `cli_copy_pkg` 只拷 `.lomt`） |
| J7 | `test_foreign_identity_includes_jar` | 换一个类字节 → 包身份变；jar 重新打包但内容不变 → 身份不变 |
| J8 | `test_index_pin_required` | 没有 `--foreign-index-sha256`（或 `lompi.conf` 里的钉）→ 外源包默认拒装；本地回环 `--insecure` 才放行 |

### P1 · 大 jar、依赖闭包、锁

范围：>256 KiB 的 jar 流式下载；两个包有 `dep` 边；锁 v2；`--from-lock` 复现；
重复类检测。

判据：

| # | 判据 | 断言 |
|---|---|---|
| J9 | `test_stream_large_jar` | 1 MiB（或更大）jar 通过 256 KiB 的旧缓冲路径会失败，流式路径成功且哈希一致 |
| J10 | `test_transitive_dep_closure` | A→B→C 的闭包全部落盘；`link.txt` 里三条 IR 都进链接 |
| J11 | `test_duplicate_class_rejected` | 闭包里两个包都定义 `com/foo/Bar` → 硬错，打印两个身份 |
| J12 | `test_lock_v2_pins_jar_and_ir` | 改动 jar 或编译器版本 → `verify` 红，并指出是哪一项 |
| J13 | `test_from_lock_reproduces` | 用锁在另一份 store 里装出逐字节相同的 `deps/` |

### P2 · 索引服务、DNS/TLS 研究、JNI 共存

范围：服务端从 Maven 生成外源索引；可选预编译 IR + 构建证明；DNS 解析；
`docs/222` 的 JNI 后端作为 `backend jni` 共存（有 Java 的 Linux 上）；HTTPS 仍不做。
判据按实际落地再列，不提前声称。

## 11. 风险与不做

| 风险 | 影响 | 处理 |
|---|---|---|
| 明文 HTTP 索引 | jar 与索引可被一起替换 | 默认要求带外钉住；本地回环才允许 `--insecure` |
| 256 KiB 单文件上限 | 真实 jar 装不下 | 流式落盘；小文件路径不变 |
| 无 DNS/TLS | 连不上 Maven Central | 第一版只连 Loment 索引；服务端做 Maven 镜像 |
| PE 无 mkdir | 安装目标目录不存在 | 沿用 plan/`--apply` 的 mkdir 提示；不偷塞 |
| JVM 平名字空间 | 多版本同名类冲突 | 闭包级重复类检测，硬错 |
| 编译器版本影响 IR | 锁不可复现 | 锁里钉 artifact_id；`verify` 报编译器版本差异 |
| 验证器有洞 | 恶意 jar 越界读写 | 验证器是安全边界；拒绝优先；差分模糊测试 |
| lompi 工作区固定大小 | 大索引/大 jar 撑爆 | jar 流式；索引分页或分文件；设明确上限 |
| 自举容量 | 大包编译时 SIGSEGV | 先量 SLOT/标签表余量；分模块编译 |
| 与 `docs/222` 的 JNI 路线重叠 | 两套 Java 后端漂移 | 共用同一个外源包与声明面；后端只是 `backend` 标签 |

**不做**：直接 HTTPS Maven、ClassLoader 隔离、反射/动态代理、完整 JDK、
`float/double`（第一版）、线程、AWT/Swing、序列化、任意未索引 jar 的"即插即用"。

## 12. 与既有文档的关系

- `docs/222`：外源包的**模型正本**（声明 + 实现 + 身份；Java 的 JNI 路线）。
  本文复用模型，新增 `kind=java` 的 **AOT 后端**，并把"没有 Java"作为默认。
- `docs/281`：AOT 内核（class 文件 → IR、验证器、对象布局、虚分派）**继续有效**；
  本文取代它的 `--java-lib` 发行方式与链接入口。
- `docs/236`：lompi 联网能力与四条边界；本文在它之上加"外源索引 + 流式 jar"，
  不改变它的明文 HTTP / IPv4 / 无 TLS 事实。
- `docs/168` / `docs/183`：库系统的身份与"声明面 + 实现 + 身份"；本文把 Java 包
  接进同一套，而不是另起一个包管理器。
- `docs/212`：预编译内核的仓；本文对应"预编译 IR / 安装时编译"两种形态，
  第一版选安装时编译，避免信任来源不明的 IR。

## 附录 A · 索引与清单

`lompi-foreign-index.txt`：

```
# name version manifest-path manifest-sha256
guava 33.0.0-jre guava/33.0.0-jre/foreign.txt 5f2c…
```

`foreign.txt`：见 §3.2。

`link.txt`：

```
# lompi link v1
ir guava.ll 4d0e…
runtime jrt 0.1.0
```

## 附录 B · CLI

```bash
lompi install guava --into deps            # 先查本地 store，再查外源索引（P0 起）
lompi java add guava --into deps           # 显式走外源索引；与上面同一条流水线
lompi java add guava --registry http://127.0.0.1:8000/foreign \
                 --registry-sha256 <64 hex> \
                 --foreign-index-sha256 <64 hex>
lompi resolve guava --into deps > lompi.lock
lompi install guava --from-lock lompi.lock --into deps --apply
loment build app.lomt                      # 自动链接 deps/*/link.txt 里的 IR
```

`--insecure` 只对 `127.0.0.1` / `localhost` 生效；别处用了会明确拒绝，而不是悄悄降级。

## 附录 C · 第一个演示（P0 验收）

```bash
# 开发机一次性准备夹具（有 JDK）：
javac --release 8 -d classes src/com/example/MathLib.java
jar --create --file lib.jar -C classes .
# 生成 foreign.lomp + foreign.txt + mathlib.lomt + index，起本地 HTTP registry

# 目标机（无 Java）：
lompi java add mathlib --into deps \
      --registry http://127.0.0.1:8000/foreign \
      --registry-sha256 <64 hex>
loment build app.lomt -o app
./app.exe
```

`app.lomt`：

```rust
module app
use mathlib

fn _start() {
    let r: i32 = mathlib_add(2, 3);
    // ... 打印 r，退出
}
```

期望 stdout + 退出码由真 JVM 在夹具生成时录下；目标机的 PATH 里没有
`java`、`javac`、`jar`、`wsl`、`clang`。这就是 P0 的全部证明。
