# Loment 对照 Rust / CPython / OpenJDK：前人走过的路

**方法**：每仓取 30 条真实 issue（Rust：`label:I-unsound` 按评论数；CPython：`label:type-bug`
按评论数；OpenJDK：GitHub 仓不收 issue，改取 JBS 最近 30 条 `issuetype=Bug AND resolution=Fixed`）。
逐条判断 Loment 是否复现**同一类**缺陷；能实测的都实测，不能实测的写明不适用的原因。
实测探针留在本轮的一次性调查目录里、不随本报告入仓；每条结论都对应一个 issue，最小复现
写在那里。判定四档：**复现**（并给出本轮/Loment 已有 issue）、**同类已在册**、**未复现**、
**不适用**。

> 结论先说：Loment 没有复现 Rust/JDK 的**优化器/并发/GC 屏障**那一路（它没有那些子系统），
> 但把**编译器自身**那一路走得非常完整——"合法输入把编译器打崩""固定容量静默丢数据"
> "检查器与发射器不一致""工具静默截断"。这三条正是 Rust 早期、CPython 和 JDK 都摔过的坑。

---

## 1. 各仓 30 条逐条对照

### 1.1 rust-lang/rust（`I-unsound`，按评论数）

| # | issue | Loment 判定 |
|---|---|---|
| 31844 | specialization tracking | 不适用：无特化/trait object |
| 10184 | float→int 转换 UB | 不适用：无浮点 |
| 27060 | `repr(packed)` 未对齐加载 | 不适用：无 repr/打包控制 |
| 10389 | `type_id` 碰撞 | 不适用：无反射 |
| 126600 | `exit` 与 C 线程不安全 | 不适用：无该 API |
| 156920 | `dyn Allocator` 不健全 | 不适用 |
| 27970 | `set_var` 与 C 读环境竞争 | 不适用：无环境写入 API |
| 81996 | MSVC 上 `repr(C)` + ZST 布局 | 不适用：extern 只收标量/ptr |
| 28728 | LLVM 循环优化导致安全程序崩溃 | 不适用：无优化器（lomelf 是栈机） |
| 107975 | 相等指针比较为不等 | **同类已在册** #205（发 `icmp == ptr`）+ 本轮 #920（lomelf 原始 traceback） |
| 39575 | `before_exec` 应为 unsafe | 不适用 |
| 82523 | `unaligned_references` | 不适用：无引用 |
| **16012** | **栈溢出检查/stack probes** | **复现** → 本轮 #995：无限递归 = 无诊断 SIGSEGV |
| 154321 | SGX `UserRef` | 不适用 |
| 57893 | trait-object 实现重叠 | 不适用：无 trait object |
| 112480 | MSVC x86-32 对齐 | 不适用 |
| 44367 | `repr(simd)` | 不适用 |
| 83994 | `exit()` 线程安全 | 不适用 |
| 52652 | 跨 FFI 展开而非中止 | 不适用：无 unwind |
| 55005 | `Arc::drop` 悬垂共享引用 | 不适用：无 Arc/RC |
| 116573 | 目标特性 + 内联miscompile | 不适用 |
| **46188** | **未使用的 FFI 声明类型错也致 UB** | **同类已在册**：extern 声明不与目标比对签名（#913 覆盖调用点） |
| 114582 | 非临时存储破坏内存模型 | 不适用 |
| 127979 | ThinLTO + dylib | 不适用 |
| 25860 | implied bounds + variance | 不适用：无生命周期 |
| 49206 | const 含非 Sync 引用 | 不适用 |
| 63818 | noalias + 自引用 | 不适用 |
| 68304 | `Box<dyn FnOnce>` 对齐 | 不适用 |
| 114479 | 无 SSE2 的 x86 浮点 | 不适用：无浮点 |
| 63787 | `&` 参数误标 noalias | 不适用 |

复现：1；同类已在册：2；其余不适用（Loment 的特性面小，这一仓的绝大多数前提不存在）。

### 1.2 python/cpython（`type-bug`，按评论数）

| # | issue | Loment 判定 |
|---|---|---|
| 71026 | 启动时 `getrandom` 阻塞 | 不适用：启动不取随机数 |
| 45943 | Windows 控制台 Unicode | 未验证（同类风险）：lomcli 直写 UTF-8 字节，旧代码页控制台会乱码 |
| 47020 | 测试用 issue | 跳过（非缺陷） |
| 56535 | `sys.platform == 'linux2'` | 不适用 |
| 28525 | finalization 时线程抢 GIL 挂死 | 不适用：无 GIL |
| 49203 | cgi multipart | 不适用 |
| 88074 | array 应不可变 | 不适用：无动态类型对象 |
| 111116 | `email.parseaddr` CVE | 不适用 |
| **98110** | **`_PyTrash` 断言（分配器不变量）** | **同类已在册** #789：free 后重用/不变量无断言，静默损坏 |
| 67705 | `datetime.utcfromtimestamp` 舍入 | 未测（`civil` 库在，本轮未比对舍入方向） |
| 72367 | PEP 538 locale 强转 | 不适用 |
| 42382 | `platlibdir` | 不适用 |
| 27434 | assertEqual 性能 | 不适用 |
| **109534** | **asyncio TLS 内存泄漏** | **同类已在册** #517（gc_auto 下只被 u64 引用的活对象被回收）/ GC 台账 |
| 46023 | `Rat.py` 迁移 | 不适用 |
| 46058 | 去掉 `__cmp__` | 不适用 |
| 15735 | asyncio executor 关闭 | 不适用：无线程池 API |
| 103341 | `csv.Sniffer` 假阴性 | 不适用 |
| 70626 | `find_library` | 不适用 |
| 123134 | 子解释器内存断言 | 不适用 |
| 56850 | cygwin 构建 | 不适用 |
| 52148 | `getgroups` on macOS | 不适用 |
| 68178 | callable 不尊重 descriptor | 不适用 |
| 51760 | msvc9compiler | 不适用 |
| **47550** | **`urllib.quote` Unicode** | **未复现**：实测 `url_encode_str("你")` = `%E4%BD%A0`（9 字节），正确 |
| 47437 | `os.listdir` 返回 bytes | 不适用：单一表示 |
| **57362** | **IDLE 非 BMP 字符** | **未复现**：实测 `"😀"` 的 `str_len == 4`，输出正确 |
| 53451 | multiprocessing 挂死 | 不适用 |
| 4003 | `poll` 负超时 | 不适用 |
| 62052 | 引用环里缓冲文件丢数据 | 不适用：无引用计数缓冲层 |

复现：0；同类已在册：2；未复现：2（实测）；未验证：1；其余不适用。

### 1.3 openjdk（JBS 最近 30 条已修 Bug）

| JBS | 主题 | Loment 判定 |
|---|---|---|
| 8394819 | AOT 超接口初始化状态 | 不适用 |
| 8394807 | CRaC 跨主机 glibc | 不适用 |
| 8394775 | 测试文件改名 | 不适用 |
| 8394744 | Valhalla 数组断言回滚 | 不适用 |
| 8394716 | 事件回调语义 | 不适用 |
| 8394696 | JFR 采样规格 | 不适用 |
| **8394662** | **限定内部类构造丢外层接收者类型** | **同类已在册** #959（链式泛型实参域名丢失外层上下文）、#964（泛型结构体字段按错实例取型） |
| 8394654 | Valhalla 缺 return | 不适用 |
| 8394633 | Shenandoah 屏障缺空检查 | 不适用：无 GC 屏障 |
| 8394622 | RISC-V movptr2 回滚 | 不适用 |
| 8394583 | ProblemList | 不适用 |
| 8394580 | 绑 127.x.x.255 失败 | 不适用 |
| 8394564 | utsname 包含 | 不适用 |
| **8394515** | **"Field too big for insn" 保证失败** | **同类已在册** #951：lomelf 对合法 case 常量 `struct.error: argument out of range`（立即数编码溢出） |
| 8394502 | s390x ZGC 测试 | 不适用 |
| 8394492 | FreeType 升级 | 不适用 |
| 8394487 | medialib 原型 | 不适用 |
| 8394406 | RISC-V 屏障 | 不适用 |
| **8394375** | **非字面量布尔常量进 switch 使编译器崩溃** | **未复现**：实测 `match <bool>`/`match <字面量>` 两侧都以 E8 干净拒绝 |
| 8394343 | lshl 结果错误 | 未测（移位宽度类；#316 已关，docs/228 记了混宽比较） |
| **8394316** | **skip 在 position > size 时返回负值** | **同类已在册** #987（LSP 表满静默返回 0）、本轮各工具 `read_all` 截断类 |
| 8394269 | 弃用 OpenType 名单 | 不适用 |
| 8394265 | 时区偏移测试 | 不适用 |
| 8394236 | RISC-V StoreLoad | 不适用 |
| 8394225 | Valhalla C2 断言 | 不适用 |
| 8394217 | poller 组配置 | 不适用 |
| **8394181** | **free 了错误指针** | **同类已在册** #789（free 后字节保留、alloc 不清零） |
| 8394146 | hsdis + binutils | 不适用 |
| 8394091 | ZGC 堆参数 | 不适用 |
| 8394032 | medialib 数学表 | 不适用 |

复现：0；同类已在册：4；未复现：1（实测）；未测：1；其余不适用。

---

## 2. 四条"走过的路"本轮实测复现情况

| 路 | 前人代表 | Loment 本轮证据 |
|---|---|---|
| **合法输入把编译器打崩** | rustc 的 ICE 传统、JDK-8394375 | **大量复现**：#868–#874（嵌套解析）、#933/#934（表达式树/形参）、#946（lomdoc EOF）、#948（lvm switch）、#957（385 臂 match） |
| **固定容量静默丢数据** | Rust `MAX_*` 表、JDK insn 编码上限 | **大量复现**：#949（局部表 512）、#955（64 struct）、#960/#961（枚举槽/12 臂）、#966（8 字段）、#975/#976/#986（lomlib 32 文件/16 边/16 KiB）、#985（capasserts 128）、#990（lomeol 2 MiB）、#951（立即数 i32）、#953/#973（1 MiB 截断） |
| **检查器与发射器不一致** | CPython 的 compile/runtime 分歧、JDK 前端/后端 | **复现**：#532 同类（str==）、#970（非标量字段）、#913（调用签名不验）、#983（`(ptr) as u64` 截断） |
| **工具链信任面** | CPython tarfile 路径穿越、pip 依赖混淆 | **复现**：#981（fetch 路径穿越→任意文件写）、#894（命令注入）、#980（std 依赖混淆）、#978（安装器跳过校验）、#979（genesis 不可复现） |
| **优化器/并发/GC 屏障类** | Rust #28728、JDK 8394633 | **未复现**（没有优化器、没有并发运行时、没有 GC 屏障——这是"没走到那条路"，不是"走对了"） |
| **运行时栈上限** | Rust #16012 | **复现** → 本轮 #995（无限递归 SIGSEGV 无诊断） |

---

## 3. 本轮对照新开的 issue

- **#995**（sev-4）：无限递归 → 无任何诊断的 SIGSEGV；无 stack probe、无 guard page 处理。
  对照 rust-lang/rust#16012。

其余复现项都已在册（#205/#920、#913、#789、#517、#959/#964、#951、#987、#789）。
