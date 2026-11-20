# 241 · Internet：第三个随包库，判据是**对着真东西**跑

> 状态: **已落地**（2026-10-13）· 自检: `tools/loment_internet_test.py` 8/8 ·
> 模块自检 7 份全过（`python tools/loment.py test <模块>`）· 门禁: `ci.py --static-only`

## 0. 一句话

`lompi/store/internet/0.1.0/` 是 `std` / `host` 之后的**第三个随包库**：把"接上网"这件事
（地址、URL、HTTP 报文、DNS 报文、socket）从散落的 app 代码收成 7 个能 `use` 的模块，
**用 Loment 写**，判据对着**真的实现**跑。

## 1. 位置：LibL 家族的第二个包

LibL 是**这个标准库的名字**（`docs/232` §1），不是一个包。

| 包 | 是什么 | 判据的对照物 |
|---|---|---|
| `std` | libc 那一面（`string` / `ctype` / `stdlib` / `stdio` + 127 个可移植模块） | **真 glibc**（`docs/232` §4） |
| `host` | 宿主那一面（`fs` / `io` / `dir` / `argv` / `stat` / `log`） | 宿主自己 |
| **`internet`** | **网络那一面**（`ip` / `uri` / `http` / `dns` / `sock` / `tcp` / `udp`） | **CPython 标准库 + 真 `http.server`** |

三个包共用同一条口径：**对照物是真的东西，不是手写的期望值**。

## 2. 七个模块（+ 门面 + `pkg.lomp`）

依赖方向单向，顺着这张表就是从下往上：

| 模块 | 公开前缀 | 系统调用 | 有什么 |
|---|---|---|---|
| `ip` | `ip_` | 无 | IPv4/IPv6 解析与格式化（RFC 4291 / RFC 5952）、CIDR 归属 |
| `uri` | `uri_` | 无 | RFC 3986 全量拆解 + **相对引用解析**（§5.2.2，含 `remove_dot_segments`） |
| `http` | `http_` | 无 | 状态行 / 头（大小写不敏感、值掐空白）/ 请求构建 / chunked 解码 / 原因短语 |
| `dns` | `dns_` | 无 | RFC 1035 线格式：域名编解码（含压缩指针）、报文头、查询构建、A/AAAA/CNAME 提取 |
| `sock` | `sock_` | 有 | 常量表、`sockaddr_in` 构造、建/关/关半边、`SO_RCVTIMEO` 超时、可重试判定 |
| `tcp` | `tcp_` | 有 | 连接 / 收发（`MSG_NOSIGNAL`）/ 短写循环 / 监听 / 接受 |
| `udp` | `udp_` | 有 | 带地址的收发（`sendto` / `recvfrom`，`syscall7` 存在的理由） |

**前四个是纯逻辑**（一个系统调用都不发、不分配），所以裸机 / 无宿主的消费者只取它们时
不会把 socket 拖进来。**后三个是宿主层**。

**缓冲一律调用方给**（与 `std/url.lomt`、`host/io.lomt` 同一约定）：模块内部不 `alloc`，
解析结果写进调用方给的记录 / 缓冲。`uri` 的 56 字节记录布局写在文件头。

## 3. 边界（**如实列，不假装有**）

* **没有解析器**。工具链里没有 `getaddrinfo`（`docs/236`），`dns` 只做**报文层** ——
  把 `1.1.1.1:53` 接上要调用方自己用 `udp` 发。这不是本库能补的：那需要一条到 libc
  或一个内置解析器的路。
* **没有 TLS**。`https://` 依旧不认（与 `docs/236` 同一条欠账）。
* **不做 IDN/IRI**：非 ASCII 主机名要先 Punycode 成 ASCII，那是另一件事。
* **不做 HTTP/2**、不做压缩体解压（gzip/deflate 在 `std/inflate.lomt`）、不做重定向。
* `uri` **不规范化**（不做百分号归一、不动大小写）—— 只按 RFC 的结构规则拆与拼。

## 4. 判据：对照物是谁

`tools/loment_internet_test.py`，8 条：

| 判据 | 对照物 | 规模 |
|---|---|---|
| `test_internet_modules_are_checkable` | 编译器自己（`_dep_pass=True`，理由同 LibL） | 7 模块 + 入口 + `pkg.lomp` |
| `test_internet_package_is_one_unit_without_name_collisions` | 编译器自己（E13） | 门面 7 模块一个单元 |
| `test_internet_names_do_not_collide_with_std` | std 全部模块的顶层名字 | 160 vs 4686，**空交集** |
| `test_ip_matches_python_ipaddress` | CPython `ipaddress` | 39 项逐字节 |
| `test_uri_matches_python_urllib` | CPython `urllib.parse`（`urlsplit` / `urljoin`） | 34 项逐字节 |
| `test_http_matches_python_client` | CPython `http.client` | 原因短语 61 条 + 报文框架 7 项 |
| `test_http_client_against_real_server` | **一个真起的 `http.server`** | 端到端取回 200 + 真文件 |
| `test_dns_wire_format` | **RFC 1035 独立推出来的字节**（见下） | 7 个域名 + 1 条查询 |

**探针编成本机原生可执行文件**：走参考编译器的 IR + 仓内 `lomelf`（`compile_pe` on
Windows / `compile_ll` else），于是探针里**真发系统调用**也能跑 —— 模块自检那条
Loment→Rust 的路走不了 socket（那里的 `syscall4/6/7` 是 Linux 的 `asm!("syscall")`，
在 Windows 上跑不了）。**这条分界写在这里**：模块自检只验纯逻辑，真实网络行为在判据里。

**DNS 那条的诚实边界**：工具链里没有真解析器可当对照物，所以期望值是判据文件里按
RFC 1035 手推的（`_rfc1035_name` / `_rfc1035_query`）。这条判据能证"**我们的编码 ==
RFC 的编码**"，**不能**证"我们和某个真实现对上"。不假装。

## 5. 判据逼出来的两个缺陷（都在本笔里修掉）

1. **`ip_v4_parse` 的哨兵与合法地址撞值**。原设计用 `4294967295` 当"解析失败"，
   而 `255.255.255.255` 的整数值**正好**是它 —— 于是一个完全合法的地址（广播地址）
   被读成失败。**判据第 3 行就红了**，改成"返回 1/0 + 值走出参"（与 `ip_v6_parse` 同形），
   公开面不再有哨兵；模块内部仍用哨兵（内部的值域受控）。
2. **`uri_part` 的分量 → 槽位映射算错**。记录里 `6` 那一格被 `port` 占着，所以
   `lo = 2*which` 只对 `scheme`/`user`/`host` 成立；`path`/`query`/`fragment` 要挪一格。
   症状是"拆解出来的是另一个分量的字节"，而**它只在跨分量比对时才现形** ——
   单元自检（`test_uri_split_basic`）当时是绿的。

两个都是**判据第一次跑就抓住**的，单元自检都没抓住 —— 这正是"对照物要真的是另一个实现"
的价值。

## 6. 收编：`lpi_cli` 里那份客户端

在此之前，仓里**已经有一个能跑的明文 HTTP 客户端**，但它住在 `lompi/lpi_cli.lomt:1150–1430`
（`net_tcp4` / `net_connect4` / `net_send` / `net_recv` / `net_http_get` / `net_parse_http`），
**不是库** —— 收在 `lpi_cli` 里纯因为依赖嵌套上限 8 层插不进去（那个文件头写着这条）。
`internet` 把那一层正式收编：连接/收发 → `tcp`，报文 → `http`，URL 拆解 → `uri`，
并补上那份 app 代码**刻意没做**的（完整 URL 结构、chunked、DNS 报文）。

**`lpi_cli` 自己**这一笔**没动** —— 换库要重算它的依赖与自举产物，那是另一笔的事。

## 7. 加模块前必读：命名硬约束

`resolve_name` 在**包内模块层**（`store/<包>/<版本>/<名>.ext`）遇到**同名两处命中直接报错**
（`lomentc._pkg_module_hits`），而顶层名字在一个单元里是**平的**（不做 mangling）。
所以：

* 模块**文件名**不能与别的包重名 —— `std` 占了 `url`，所以这一份叫 `uri`；
* 内部前缀也要挑没被占的拼写 —— `u_` 是 `std/url.lomt` 的，`mem` 是 `std/mem.lomt` 的。

判据 `test_internet_names_do_not_collide_with_std` 是**第一个**看着这条规则的判据
（在此之前它没有判据，靠人记）。

## 8. 与其它文档的关系

* `docs/232`：LibL 的 libc 面（§1.2 里指到这份）；
* `docs/219` / `docs/173`：FFI 与端口表 —— `internet` 走的是**内建 `syscall4/6/7`**，
  不依赖端口表；
* `docs/234` / `docs/236`：net 运行时面补完（29 个号两侧都能派发）与 lompi 的联网；
* `docs/218`：net 的准入设计（`dom_admit`）—— **与本库无关**，那条至今没接到网络上；
* `docs/168`：包系统（包名 == 入口文件名、依赖来自源码的 `use`）。
