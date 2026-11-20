# 217 · 两个平台的产物都能联网：PE 运行时补上 socket 派发面

> 状态: **已落地**（2026-10-10）。起点是一个不对称：同一份 `.lomt`，
> ELF 那边能监听端口，PE 那边 `socket()` **静默返回 -1**。
> 本文记的是补上它之后的样子、几条翻译决定、量到的数，以及**点名不做**的那几件。
> 判据在 `tools/loment_pe_test.py`（三条跨平台形状钉 + 两条真跑）。

## 0. 一句话

> PE 的运行时只有一个 **8 个号**的派发面（`exit`/`write`/`read`/`close`/`openat`/`brk`/
> `getdents64`/`newfstatat`）—— 文件与进程那一半，**一个 socket 号都没有**。
> 所以在那之前，"孪生联网"是个**假命题**：同一份源，Linux 上能监听端口，Windows 上 `socket()`
> 拿到 -1 且**什么都不说**。
> 现在派发面是 **29 个号**（21 + `poll`(7) + `select`(23) + `sendmsg`/`recvmsg`/
> `socketpair`/`epoll` 那一族，见 `docs/221` / `docs/231` / `docs/234`），
> 联网那 13 个由 `ws2_32.dll` 顶着；
> **同一份源在 Windows 原生与 WSL/Linux 上输出逐字节相同、退出码相同**（§4 有数）。

## 1. 缺口在哪一层

`tools/lomelf.py --target pe` 从同一份 IR 出静态 PE32+。x64 Windows 没有 `syscall` 指令，
所以每个内联 syscall 都变成 `call __win_syscall` —— 一段**手写 x86-64 机器码**
（`emit_win_shim`）。ELF 目标上没有这一层：`socket`(41)/`connect`(42)/`setsockopt`(54) …
直接就是 Linux 系统调用，今天就能用（`loment/skills` 那份指南里 §6.2 的 18/19 条就是
实测过的服务端坑）。

于是**缺的不是库，是 PE 侧那段派发面**。所以这次没写任何工具链库、没碰 `lom/*.lom`
（L0 契约）、没动 ELF 那一侧 —— 只改运行时（用户 2026-10-09 的裁决：
"Loment 本来就靠裸系统调用联网"）。

## 2. 补进去的 13 个号

| Linux 号 | 名字 | 顶上它的 WinSock | 翻译点 |
|---|---|---|---|
| 41 | `socket` | `socket` | family 10→23；`SOCK_NONBLOCK`→`ioctlsocket(FIONBIO)`；`SOCK_CLOEXEC` 丢掉 |
| 42 | `connect` | `connect` | sockaddr 的 family（就地改、返回前还原） |
| 43 / 288 | `accept` / `accept4` | `accept` | 新的 SOCKET 换一个 fd；accept4 另认 `SOCK_NONBLOCK` |
| 44 / 45 | `sendto` / `recvfrom` | `sendto` / `recvfrom`（addr=NULL 时落 `send`/`recv`） | 掩 `MSG_NOSIGNAL`；family 进出各一次 |
| 48 | `shutdown` | `shutdown` | 无（`SHUT_*` 两边同值） |
| 49 / 50 | `bind` / `listen` | `bind` / `listen` | 同 connect |
| 51 / 52 | `getsockname` / `getpeername` | 同名 | family 23→10（出口方向） |
| 54 / 55 | `setsockopt` / `getsockopt` | 同名 | level/optname 号表；两个 TIMEO 还要**换量纲** |

`read`(0) / `write`(1) / `close`(3) 也一并认得 socket 型 fd：fd 表新增一种 kind，
read→`recv`、write→`send`、close→`closesocket`。**Windows 上 `ReadFile`/`WriteFile`
读不了 socket 句柄**，不分流就是错的（不是慢）。

## 3. 五条翻译决定（这是仿真，不是转发）

1. **family**：`AF_INET`=2 两边同值，`sockaddr_in`/`sockaddr_in6` **布局逐字节相同** ⇒ 直通；
   但 **`AF_INET6` Linux=10 / Windows=23** ⇒ 进出各补一次 family 字。就地改、返回前还原
   （单线程，系统调用期间程序看不到）。空指针合法（`connect`/`accept` 的 addr 可以是 0）⇒ 跳过。
2. **`SOL_SOCKET` 与 `SO_*`**：level 1→`0xffff`；option 号两边**都不一样**
   （`SO_REUSEADDR` 2→4、`SO_KEEPALIVE` 9→8、`SO_SNDBUF` 7→0x1001、`SO_RCVBUF` 8→0x1002、
   `SO_SNDTIMEO` 21→0x1005、`SO_RCVTIMEO` 20→0x1006、`SO_ERROR` 4→0x1007）。
   没映射的 `SO_*` 报 **-EOPNOTSUPP（-95）**，不是静默成功。
   `IPPROTO_TCP`=6 与 `TCP_NODELAY`=1 两边同值 ⇒ 直通。
3. **超时的量纲**：`SO_RCVTIMEO`/`SO_SNDTIMEO` 在 Linux 收 `struct timeval{i64 sec; i64 usec}`，
   在 Windows 只认 **32 位毫秒**。两个方向都算（`setsockopt` 折成毫秒、`getsockopt` 摊回 timeval
   并把 `*optlen` 写回 16）。
4. **错误码**：API 失败后取 `WSAGetLastError`，映射成 **负 errno** —— 消费方读的是 Linux 语义。
   其中 **`WSAETIMEDOUT` 与 `WSAEWOULDBLOCK` 都归 `-EAGAIN`(-11)**：Linux 上设了
   `SO_RCVTIMEO` 的 `read` 到点返回的就是 -11，不归一到这里，"同一份源两边行为一致"就是假的。
   表外的一律 -1（**明写的上限**）。
5. **两个 Linux 位**：`MSG_NOSIGNAL`(0x4000) Windows 没有这一位（那边也没有 SIGPIPE）⇒ 掩掉；
   `SOCK_NONBLOCK`(0x800) → 成功建 socket 之后补一次 `ioctlsocket(FIONBIO)`
   （**设不上就关掉这个 socket 再报错**，不留一个"以为非阻塞其实阻塞"的 fd）；
   `SOCK_CLOEXEC`(0x80000) 丢掉。

外加一条**分流**：`sendto`/`recvfrom` 的地址为 NULL 时走 4 参的 `send`/`recv`。
Linux 上"`sendto(已连接, …, NULL, …)`"就是 `send`；WinSock 的 `sendto` 拿 NULL 加非零
`tolen` 会报 `WSAEFAULT` —— 不分流就是一个"Linux 能跑、Windows 报错"的暗沟。

## 4. 量到的数

| 什么 | 之前 | 之后 |
|---|---|---|
| 派发面 | 8 个号 | **21 个号**（`PE_DISPATCH` 那张表就是真话） |
| 导入 DLL | `kernel32.dll` 一个 | **`kernel32.dll` + `ws2_32.dll`** |
| 导入函数 | 11 | **29**（ws2_32 那 18 个） |
| 导入表 blob | 4115 B | **4115 B**（两条描述符仍挤在 `/proc/self/cmdline` 字面量那 4096 B 之内） |
| shim 机器码 | 2398 B | **6296 B**（`--dump-win-shim` 的分片 12 → 32 片） |
| 运行时状态区 `WS_SIZE` | 1066840 | **1067616**（`WSADATA` 512 + 暂存 256 + 标志 8） |

**同一份 `.lomt`（`tools/loment_pe_test.py` 的 `SOCK_DEMO`）两个平台的实测**：

| 平台 | 怎么跑 | 退出码 | stdout |
|---|---|---|---|
| PE（Windows 原生） | 本机直接跑 `.exe` | 0 | `ECHO OK` / `TIMEOUT OK` / `NONBLOCK OK` / `IPV6 OK` / `SURVIVED` |
| ELF（Linux） | WSL 里直接跑 | 0 | 同上，**逐字节相同** |

五行输出各钉一件事：**回显对得上**（socket 型 fd 上的 read/write 分流对）、
**读超时返回 -EAGAIN**（`setsockopt` 的 timeval→毫秒那条路）、
**`accept4(SOCK_NONBLOCK)` 的 socket 真的非阻塞**（读立刻 -EAGAIN ⇒ `ioctlsocket(FIONBIO)` 那条路；
**这一格正是这次抓出真 bug 的地方**，见 §7）、**`AF_INET6` 的 family 两侧都翻译**（`getsockname`
回来必须是 10 而不是 Windows 的 23）、**对端挂断之后进程还活着**（`MSG_NOSIGNAL` 那条路；
Linux 上不带它这里会被 SIGPIPE 杀掉）。中间还有 `getpeername`（出口方向的 sockaddr）与
`getsockopt(SO_RCVTIMEO)` 的**反向**换算（毫秒摊回 timeval，并把 `*optlen` 写回 16）。

`IPV6` 那一行**允许两种结果**（这台机器没有 IPv6 时两边都打 `IPV6 NO`）—— 判据是
"两个平台看到同一件事"，不是"必须有 IPv6"。

## 5. 点名不做

* **`poll` / `select` / `epoll`**、**`sendmsg` / `recvmsg`**、**`socketpair`**、**`AF_UNIX`** ——
  （`poll`、`select` **与这一节点名的每一个**后来都补上了：`sendmsg`/`recvmsg`/
  `socketpair`/`epoll` 在 `docs/234`，其余在 `docs/221` / `docs/231`）
  表外的号一律 **`-ENOSYS`（-38）**（派发面的兜底；2026-10-11 之前是 `-1`，那次改动见
  `docs/227` §2 —— `-1` 是 `EPERM`，拿"权限不够"说"没实现"是假话，而 Linux 对不认得的号
  返回的就是 `-ENOSYS`）。
  **`poll`(7) 已做**（2026-10-10，`docs/221`）：架在 WinSock 的 `select` 上，
  `pollfd` ↔ `fd_set` 两边翻译。**名单的现状与逐条理由见 `docs/221` §7** ——
  其中 `epoll`、`sendmsg`/`recvmsg`、`socketpair`/`AF_UNIX` **2026-10-12 起也都补上了**
  （`docs/234`：epoll 走**水平触发**、socketpair 造**回环上的一对**）—— 于是这一节
  的"点名不做"**清空**，`lom/net.lom` 里连 `NotDispatched` 这个枚举都删了。
  **`select`(23) 已做**（2026-10-11，`docs/231`）：`__fds_bits` 位图 ↔ WinSock `fd_set`，
  外加**出参归一化** —— WinSock 把"非阻塞 connect 失败"报在 except 集里（Linux 报在
  writefds），且那种 socket 的 `SO_ERROR` 会被读掉，所以 shim 自己留一份、`getsockopt`
  时还回去。两条**平台差异**写在那儿了（Windows 把"本地端口被拒"升上来要 ~2–3 秒；
  同步就失败的 connect 在 WinSock 的 `select` 里**什么都不报**）。
* ~~**语言侧的一条真限制**：`syscall6` 的内建签名是 `nr + a0..a4`（5 个实参），
  所以 6 参的 `sendto`/`recvfrom` 从 `.lomt` 里**递不满**（`addrlen` 那一格没地方放，
  递过去的是寄存器里的残留值）……~~
  **已关掉（2026-10-10，`docs/220`）**：语言侧补了 `syscall7`（`nr` + 6 个实参，
  `rdi/rsi/rdx/r10/r8/r9` 用满），于是**带地址的 UDP 写得出来了**，
  这一支也从"没有人碰过"变成**判据**（`test_pe_and_elf_agree_on_net_programs` 的 UDP 语料，
  两个方向都走）。上面那条"addr=NULL 落 `send`/`recv`"的分流**留着**：它是 Linux 的语义
  （已连接的 socket 上 `sendto(…, NULL, …)` 就是 `send`），不是补丁。
* **`capability` 域的联网面**：这次没动。今天 `guard` 管的是域的**下标**，
  而 socket 是裸 `syscall6` —— 指南 §5 那句"没有任何东西拦得住真正做事的操作"照旧成立。

## 6. 判据（每条都能证伪）

| 主张 | 判据（`tools/loment_pe_test.py`） |
|---|---|
| 派发面 == 文档/注释登记的那张表 | `test_pe_shim_dispatches_the_documented_numbers`：解出来的 `cmp eax,imm32` 序列逐条对上 `PE_DISPATCH`，兜底是 `mov rax,PE_ENOSYS`（按常量比，不抄字节）。**跨平台**，门禁的 ubuntu 也跑 |
| 表外的号两个平台说同一句话 | `test_pe_and_elf_agree_on_net_programs` 的 `enosysdemo` 语料：Linux 自己也不认得的号（9999/9998/9997）两边都回 `-38`。**Windows 机器**才跑；反证已验（把 `PE_ENOSYS` 改回 `-1`，这条当场红） |
| 导入面真的是两个 DLL | `test_pe_imports_two_dlls_with_the_socket_surface`：描述符表读到底、两个 DLL 名、ws2_32 的 18 个函数都在、可选头里的 Import Directory size 跟着条数走。**跨平台** |
| 冻出来的那份没漂 | `test_frozen_shim_blob_matches_the_reference`：树里的 `win_shim_data.lomt` 与现算的 shim/导入表**逐字节相同**（防止"改了 shim 忘了重冻"，那样自举链接器会照抄旧的）。**跨平台** |
| 导入表塞得进孪生那块落点 | 同上的末段：`len(idata) ≤ TB_EXT - TB_IDATA`（撑破是**静默截断**，不是报错） |
| 两个平台行为一致 | `test_pe_and_elf_agree_on_a_socket_program`：同一份源，PE 原生与 WSL 的 ELF 输出逐字节相同 —— 里面覆盖 socket/bind/listen/getsockname/connect/accept/**accept4(FIONBIO)**/read/write/**getpeername**/**getsockopt 的毫秒→timeval**/**IPv6 family 两侧**/sendto(MSG_NOSIGNAL)/shutdown/close（**Windows 机器**才跑） |
| Linux 侧也真的收发过字节 | `test_elf_socket_program_runs_on_linux`：Linux 原生真跑同一份语料（**CI 的 ubuntu runner 跑得到**；PE 那半在 CI 里没有任何 runner） |

门禁里没有 Windows runner，所以"PE 确实能跑"这条**只能在本机**验；做 CI 兜底的是那三条
形状钉子（纯数据，平台无关）—— 它们盯的是"文档说的和机器里冻的是不是同一件事"。

## 7. 踩到的坑（写给下一个改 shim 的人）

* `alu_rr` 收的是**真 opcode**（`0x21` = `and`），而 `ADD`/`SUB`/`AND_` 那些模块常量是给
  `alu_ri` 用的 **Group1 `/digit`**（`4` 也是 `and`）。把 `AND_` 传给 `alu_rr`，`and rcx,rax`
  会静默变成 `add al,0xC1` —— 症状是**每个 socket 都被建成非阻塞**（那一次 `type & 0x800`
  算成了 `0xD9` ≠ 0），于是 `connect` 立刻返回 `WSAEWOULDBLOCK`，而 TCP 连接**其实是通的**
  （对端 `accept` 得到连接）。文件头那句警告说的就是这件事。
* 排这类错的办法是**让运行时自己说话**：临时把 errno 映射改成"回原始 WSA 码的两个字节"，
  再从 Loment 侧把那两个字节写 stderr 读回来，比猜快得多。⚠ 注意：调试用的映射若把错误
  映射成**正数**，调用方（`if r < 0`）分不出失败与成功，会看着像"成功"。
* `imul_ri` 只吃 imm8 —— 乘 1000 得用 `imul_rr`；除法只有 `group3(6, reg)`（`div`）。
* 派发 case 直接坐在 `__win_syscall` 的序言栈帧上（`rsp` 16 对齐、`0x60` 里已含影子空间），
  所以**不要再 `sub rsp,0x28`**；子程序是 `call` 进来的，只有它们要自己 sub。
  Win64 只保 `rbx/rbp/rdi/rsi/r12-r15` ⇒ Linux 的实参在第一次 API 调用前**全部落栈槽**。

## 8. 与其它文档的关系

* **`docs/167`（原生 ELF 后端 / PE 目标）** —— PE 目标那一节的号表与 blob 字节数按本文更正。
* **`docs/173`（FFI）** —— 与本文正交：那条是"链别人的 `.o`"，本文是"自己就能联网"。
* **`docs/212`（逃生舱）** —— 正交：那条管"产物多快"，本文管"产物能不能联网"。
* **SKILL 指南 §6.2 的 18/19 条** —— 那两条实测（SIGPIPE 杀进程、阻塞 `read` 拖垮服务）
  现在**两个平台都成立**，而且 PE 侧的 `SO_RCVTIMEO` 已经能用了。
