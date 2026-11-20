# 221 · PE 运行时补 `poll`（以及 IPv6 从"能 bind"补到"能连能收"）

> 状态: **已落地**（2026-10-10）。`docs/217` §5 那张"点名不做"的名单里，`poll`/`select` 是并列躺着的
> —— 本文关掉 `poll` 那一半，并说清 `select` 为什么**故意**留在名单上。
> 顺带把 IPv6 的覆盖从"能 bind、能问名字"补到"能连、能收"。

## 0. 一句话

> Windows 上没有 `poll` 的直接对应物，而有 `select` —— 所以这一格的形状是：
> **Linux 的 `pollfd` 数组 ↔ WinSock 的 `fd_set`**，事件位**进出各映射一次**
> （两边不是一套值：`POLLIN`=1 对 `POLLRDNORM`=0x100）。

## 1. 为什么架在 `select` 上，不用 `WSAPoll`

`WSAPoll` 在 Windows 上有名的问题（连接失败时 `POLLOUT` 照样报、`POLLHUP` 不可靠），
而 `select` 是 WinSock 里最老最稳的那一个。两个后果：

* 顺带的：两个 Linux 调用（`poll` 与将来可能的 `select`）落在**同一套集合助手**上
  （`__ws_set_add` / `__ws_set_has`），第二件的边际成本比第一件低；
* 代价：`poll` 的 `timeout` 是**毫秒**，而 WinSock 的 `select` 收 `struct timeval`
  （两个 **32 位** long）—— 所以那一格要做一次除法折换（与 `setsockopt` 的 `SO_RCVTIMEO` 同一手法）。

## 2. 事件位翻译表（两边不同值）

| Linux | 值 | 方向 | Windows | 值 |
|---|---|---|---|---|
| `POLLIN` | 0x001 | 进 | `POLLRDNORM` | 0x100 |
| `POLLOUT` | 0x004 | 进 | `POLLWRNORM` | 0x10 |
| `POLLERR` / `POLLHUP` | 0x008 / 0x010 | 出 | `POLLERR` / `POLLHUP` | 0x1 / 0x2 |
| `POLLNVAL` | 0x020 | 出 | **没有这一档** | —— |

`POLLNVAL` 由 shim 自己补：一个 fd 不是 socket（或压根不在表里）就给它打上这一位，
而不是静默地"不报"。**直接透传这些位就是错值**，所以进出各映射一次。

## 3. 两处点名的偏差（写死在这里，不静默）

* **只认 socket 型 fd**：文件 fd（0/1/2 等）在 PE 上报 `POLLNVAL`，
  而 Linux 会给它们 `POLLIN`/`POLLOUT`。我们的 fd 表里只有 socket 那一类有句柄可等。
* **三个集合全空时不等**（不睡）：WinSock 的 `select` 拿到全空集合直接 `WSAEINVAL`，
  所以那种情况下直接返回（`POLLNVAL` 已经写在 revents 里了）。
  Linux 语义上这时会睡满 timeout —— 这是**已知的**一处不一致。

## 4. 踩到的坑（写给下一个改 shim 的人）

* **`fd_set` 的 `fd_array` 在偏移 8，不是 4。** Win64 上 `SOCKET` 是 8 字节，
  `u_int fd_count` 后面要按 8 对齐。按 4 写会落进**填充区**：编译过、跑起来
  Windows 的 `select` 拿到的是垃圾句柄，症状是它**报错**（离原因很远）。
  提示这一点的是一条算术：`sizeof(fd_set)` = 8 + 64×8 = **520**，正好等于我们给它的格子。
* **帧从 `0x60` 抬到 `0x80`**：这一格要用 `0x60`/`0x68` 两个格子（`rev` 与 `n` 要同时在），
  既有格位一律不动 —— 抬帧只影响 `__win_syscall` 自己的序言，子程序各有各的。
* **`poll` 的 `timeout` 要按 32 位读**：调用方给的是 `int`，上层寄存器里是垃圾；
  `ld32` 之后再判符号，`< 0` 才是"无限等"。

## 5. 实测：三份语料、两个平台、逐字节相同

`tools/loment_pe_test.py` 的 `NET_CASES`（一条判据跑完整份清单）：

| 语料 | 钉什么 | 输出 |
|---|---|---|
| `sockdemo` | TCP：回显 / 读超时 / 非阻塞 / **IPv6 端到端** / 对端挂断存活 | `ECHO OK` · `TIMEOUT OK` · `NONBLOCK OK` · `IPV6 OK` · `SURVIVED` |
| `udpdemo` | 带地址的 UDP（`syscall7`，两个方向） | `UDP RECV OK` · `UDP OK` |
| `polldemo` | 两个连接只有一个有数据 ⇒ 只报它；读掉再 poll ⇒ **超时返回 0** | `POLL READY OK` · `POLL TIMEOUT OK` |

PE（Windows 原生）与 ELF（WSL）的输出**逐字节相同**，退出码相同。

## 6. 顺带：IPv6 从"能 bind"补到"能连能收"

原来只验了 `bind` + `getsockname`（即 family 翻译的**一个**方向、**一条**路径）。
现在 `listen` → v6 客户端 `connect` → `accept` → 写一个字节 → 读回来，全走一遍：
亦即 `10↔23` 的翻译在**经 connect/accept 这条路径上、进出口两个方向**都被判据盖住了。
（对端地址回来时也要从 23 翻回 10 —— 这一格现在也真的被执行了。）

## 7. 名单更新（`docs/217` §5 那张表的现状）

| 项 | 现状 |
|---|---|
| `poll`(7) | ✅ **已做**（本文） |
| `select`(23) | 见下一行（同一套集合助手，另加出参归一化） |
| `select`(23) | ✅ **已做**（2026-10-11，`docs/231`）。当时列的两条理由里，① 是"重复"（不构成不做的理由）；② `exceptfds` 没有一对一映射 —— **仍然成立**，所以翻译里加的是**出参归一化**（WinSock 报在 except 的连接失败搬进 write 集 + `SO_ERROR` 自己留一份、读时还回去），而不是把 except 集硬塞给调用方。这一条现在由 `seldemo` 那一格钉住（PE×ELF 逐字节） |
| `epoll` | 不做：IOCP，不是一次翻译能补的（原文理由仍成立） |
| `sendmsg` / `recvmsg` | 不做：`iovec` 那一套；`sendto`/`recvfrom` + `syscall7` 已经够写 UDP 回显 |
| `socketpair` / `AF_UNIX` | 不做：Windows 的语义与路径规则都不同，转发会**假装成功** |

## 8. 判据

| 主张 | 判据 |
|---|---|
| `poll` 在 PE 上真的会报"谁就绪" | `polldemo`：两个连接只有一个有数据 ⇒ `poll` 返回 1，且**只有那一条**的 `revents` 非零；读掉之后再 poll ⇒ 返回 **0**（超时） |
| 两个平台行为一致 | `test_pe_and_elf_agree_on_net_programs`：三份语料 PE vs ELF **逐字节相同** |
| 派发面没有偷偷少一个号 | `test_pe_shim_dispatches_the_documented_numbers`：逐个对上 `PE_DISPATCH`（本文那一轮是 22 个号；`select` 落地后是 23，见 `docs/231`） |
| 冻出来的 blob 没漂 | `test_frozen_shim_blob_matches_the_reference`（7362 B） |
| 孪生那条不变量还在 | `test_pe_selfhost_mirror_matches_reference`（镜像与参考的 PE 逐字节相同；状态区跟着长到 1069192） |

## 9. 与其它文档的关系

* **`docs/217`（两个平台的联网面）** —— 本文关掉它 §5 名单上的第一条；那份表按 §7 更新。
* **`docs/220`（`syscall7`）** —— 同一批工作：那一格让**带地址的 UDP**写得出来，本文让**多路复用**可用。
* **`docs/218`（准入：不设守卫设档案）** —— 正交，本文不碰准入那一轴；
  但"运行时面有多大"现在是 `{proc: 1, file: 6, mem: 1, net: 14}`（**算出来的**，见 `docs/218` §12）。
* **`docs/219`（世界端口）** —— 有名字的边归它；本文动的仍是**没名字的边**（内联 syscall）那一族。
