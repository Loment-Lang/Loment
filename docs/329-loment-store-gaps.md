# store 的五个缺口：网络与密码学、组合层、平台面、索引、包内复用

> 2026-11-10。`docs/327` 把 `system` / `os` 补到各 40 之后，剩下的五个结构性缺口一起补：
> **3 网络与密码学**、**2 组合层**、**6 平台面**、**5 文档面**、**7 包内复用**。
> 顺带修掉 `docs/327` 那批模块里三个"编译全绿、运行必错"的缺陷。

## 1 网络与密码学（3）

### 原语（`std`）

| 模块 | 内容 | 判据 |
| --- | --- | --- |
| `chacha20` | RFC 8439 分组函数、流式 XOR、32 位计数器 | RFC 2.3.2 向量 |
| `poly1305` | 五肢 26 位累加器、可任意分块 | RFC 2.5.2 向量 + 分块不变 |
| `aead` | ChaCha20-Poly1305（RFC 8439 §2.8），先验后解密 | RFC 向量 + 篡改拒绝 |
| `aes` | AES-128/256、CTR、GCM（GHASH 逐位 128 位乘法） | FIPS-197 / SP 800-38A / 38D |
| `x25519` | TweetNaCl 式 16×16 位肢体、常数时间 Montgomery 梯 | RFC 7748 Alice/Bob + 迭代 1 |
| `ed25519` | 同一套肢体 + Edwards 加法定律 + modL + 压缩/解压 | RFC 8032 §7.1 两条向量 |

**三方对拍**：`tools/loment_crypto_test.py` 里 Python **独立实现**同一批算法（不是拿
RFC 常数抄一遍），期望值由它算；Loment 探针调模块。两套实现 + RFC 向量三方一致才算过
（`docs/155` 那条"两方相等不算证据"）。

### 传输（`internet`）

- `unixsock` —— AF_UNIX：普通路径与 Linux 抽象命名空间（`@name` → 首字节 NUL）、
  `SOCK_CLOEXEC` 的 `accept4`、以及 `SCM_RIGHTS` 传文件描述符（`ux_send_fd` /
  `ux_recv_fd`，96 字节固定 scratch 布局）。
- `tls` —— TLS 1.3 的**客户端核心**：记录层（ChaCha20-Poly1305 + 序列号异或 IV）、
  HKDF-Expand-Label 密钥派生、握手消息编解码、ClientHello 构造、ServerHello 解析、
  X.509 里 Ed25519 SPKI 提取、CertificateVerify 验签。
  **边界（写在文件头）**：只有 `TLS_CHACHA20_POLY1305_SHA256`、只有 x25519、
  只有 Ed25519 叶证书；没有 RSA/ECDSA 证书、会话票据、0-RTT、HelloRetryRequest；
  **不声称与真服务器互通**——它是记录层与握手的算术与解析，由自检与判据覆盖。

## 2 组合层（2）

原语齐了但"拼成一件事"的东西是空的。这一层只补四件，且都受语言约束：

| 模块 | 形状 | 为什么是这个形状 |
| --- | --- | --- |
| `os/eventloop` | epoll + 一个 timerfd + 一个 eventfd + 可选 signalfd，`el_wait` 返回事件记录 | **没有函数指针**，循环不能收回调；按 tag 由调用方分派 |
| `os/fsops` | 读整个文件、原子写（tmp+rename+fsync）、拷文件、删树、拷树、遍历 | 工具里最常自己拼的 200 行 |
| `std/channel` | 有界 MPMC 字节消息环 + 私有 futex 等待 | 进程内消息传递 |
| `os/jobqueue` | u64 任务队列 + 同一套锁/等待 | **线程池写不出来**（`thread_spawn` 要编译期函数名），能给的只有队列，worker 由用户自己写 |
| `internet/httpsrv` | 监听/accept/读请求/最长前缀路由/回复头 | 同上：路由返回 handler id，调用方 if 链分派 |

`channel` / `jobqueue` 的单线程运行时探针跑了 41 项（满 → `-11`、环绕、`close` →
双向 `-32`、队列顺序与 id），截获一个真 bug（非 4 字节倍数的载荷被误拒）。

## 3 平台面（6）

新包 `windows`（0.1.0，6 个模块）：`kernel32`（文件/句柄/错误）、`console`、
`process`（STARTUPINFOA 104 字节、PROCESS_INFORMATION 24 字节）、`sync`、
`wtime`（FILETIME 两半合成 100ns）、`environment`。

**边界**：今天的 PE **没有 FFI**（`--link` 对 `--target pe` 被拒，#1262），所以这六个
模块是**正确的声明面**（签名、常数、结构体布局、IR），链到 Kernel32 要等 #1262。
自举编译器编 `extern fn` 会丢形参/内建错型（#1045），因此六条登记在
`loment_store_twin_test.KNOWN_GAPS` 里——**修好这两件事之后必须清空**（棘轮）。

## 4 文档面（5）

- `tools/loment_store_index.py` 生成 `docs/store-index.md`：每个模块的**一句话目的**
  （取文件头第一行）+ 公开常量 + 公开函数名，按包分组。判据
  `loment_lompi_test::test_the_store_index_is_current` 逐字节比对。
- `docs/329` 是 **cookbook**：十来个"照抄就能跑"的任务配方（读整个文件、看目录变化、
  事件循环、unix socket、HTTP 服务、TLS 握手、channel + worker、Windows 文件 IO）。

## 5 包内复用（7）

两个共享模块，七处重复代码收口：

- `system/timeabi` —— timespec / timeval / itimerspec / itimerval 的打包与读取；
  `timerfd`、`futex`、`os/interval`、`os/alarm` 改为委托（公开 API 一字不变）。
- `os/fsutil` —— C 串拷贝、读整个文件、写文件、存在性、路径拼接、十进制；
  `pidinfo`、`boot`、`sysctl` 委托，删掉各自的私有副本与 12 个重复系统调用常数。
- `os/watcher` 直接 `use inotify`（system 包），删掉整份 inotify 常数与事件解码。

包间 import 走平名字解析：`os` 现在依赖 `system/inotify` 与 `system/timeabi`，
这在打包上是安全的（整棵 store 随包发），但要记住**包不是 import 的边界**。

## 6 顺带修掉的真缺陷

| 缺陷 | 症状 | 根因 |
| --- | --- | --- |
| `os/interval` 用 `timerfd_create = 290` | 拿到的是 eventfd，`read` 语义全错 | 290 是 `eventfd2`，`timerfd_create` 是 283 |
| `watcher` 自检 `event_size(1)==16` | 自检红 | 内核事件按 16 字节对齐，1 字节名字的事件是 32 字节 |
| `boot::bo_cmdline_has("mitigations")` | `key=value` 形式查不到 | 词尾终止符不认 `=` |
| `eventfd` 自检 `zext i64 → i32` | clang 拒收整个单元 | 先落 `u64` 再比（#1539 同类） |
| `ed25519` 三处十六进制手抄错 + 自别名缓冲 | 验签永远失败 | 常数改为由 `d = -121665/121666` 独立推出；`eq_inv`/`eq_pow2523` 用独立 scratch |
| `channel` 拒非 4 字节倍数载荷 | 有些消息发不出去 | 帧是对齐填充的，长度校验不该按倍数 |

## 7 判据与规模

| 判据 | 钉住什么 |
| --- | --- |
| `loment_lompi_test::test_every_store_module_compiles_on_its_own` | 332 个模块各自过编译面 |
| `loment_lompi_test::test_the_store_index_is_current` | 索引 = 生成物 |
| `loment_crypto_test::test_crypto_primitives_match_independent_python` | 六个原语 × Python 独立实现，12 项逐字节 |
| `loment_store_twin_test` | 除 `windows` 六条登记缺口外，两边 IR 逐字节相同 |

store 从 311 个模块 / 323 个文件长到 **332 个模块 / 345 个文件**，13 个包。
