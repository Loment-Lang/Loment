# 234 · 联网面补完：`sendmsg` / `recvmsg` / `socketpair` / `epoll`

> 状态：**已落地**（2026-10-12）。`docs/217` §5 与 `docs/227` §6 那张"点名不做"名单上，
> **最后一个号也移出去了** —— 联网面的**运行时层**从此没有"故意不做"的项。
>
> 七个号落地（`select` 在 `docs/231`，本文是剩下六个）：`sendmsg`(46) `recvmsg`(47)
> `socketpair`(53) `epoll_wait`(232) `epoll_ctl`(233) `epoll_create1`(291)。
> 派发面 **23 → 29 个号**，其中 net 面 **15 → 21**。

## 1. 三族各自怎么翻的（以及**没做**什么）

### 1.1 `sendmsg` / `recvmsg`：摊平与散开

Linux 的 `msghdr` 与 WinSock 的 `WSAMSG` **不是同一份东西**：`iovec` 是 `{base, len}`、
`WSABUF` 是 `{len, buf}`（**字段顺序相反**），而要调 `WSASendMsg`/`WSARecvMsg` 还得先
`WSAIoctl(SIO_GET_EXTENSION_FUNCTION_POINTER)` 取扩展函数指针。这里换一条路：

* **`sendmsg`：按块拼、按块发。** 为什么不"逐 iov 直发"——**一次 `sendmsg` 在数据报 socket
  上必须是一个数据报**，逐 iov 会把它拆成好几个（实测：第二个 iov 走 `send`，而未 connect
  的 UDP socket 上 `send` 直接 `WSAENOTCONN`）。数据报上限 65507 < 块大小 64 KiB，
  所以"拼够一块就发"对数据报永远等于"整个数据报一次发出"，分块只可能落在流上。
* **`recvmsg`：先收进暂存，再散回 iovec。** `msg_name` 的对端地址从暂存**拷回调用方的
  缓冲区**（第一版漏了这一格：`msg_namelen` 回写了、地址没拷，症状是"地址还是调用方
  自己填的哨兵值"）。
* 短写按 Linux 语义：返回已发字节数；**一个字节都没出去**才报错。
* **不做的**：`msg_control`（`SCM_RIGHTS` 那一套）不支持 —— 发送忽略、接收 `controllen = 0`；
  流上单次 `recvmsg` 最多搬 64 KiB（Linux 可以更多，但流语义允许"给少了"）。

### 1.2 `socketpair`：回环上的一对真 socket

Windows **没有** `socketpair`，它的 `AF_UNIX` 也不认 Linux 那种路径。做法是造**回环上的
一对真 socket**：`SOCK_STREAM` → 一对 TCP；`SOCK_DGRAM` → 两条互相 `connect` 的 UDP。

实现上有个值得记的决定：**调内部调度器**（把 Linux 号与参数摆好、`call __win_syscall`），
于是 `socket`/`bind`/`getsockname`/`listen`/`connect`/`accept` 全走那些**已经有两平台语料
钉着的**路径 —— 比在这里手搓一串 WinSock 调用少一大截代码，而且每一步都验过。
（调度器的序言自己 `and rsp,-16` + `sub rsp,0x80`，所以嵌套调用不会踩本帧的槽。）

错误码**照 Linux 实测**对齐（不是照 man page 猜）：

| 输入 | Linux | 说明 |
|---|---|---|
| `socketpair(AF_INET, …)` | `-95` | EOPNOTSUPP |
| 类型不认识（如 `99`） | `-22` | **EINVAL** —— 第一版写的 `-91`（EPROTOTYPE），两边一比就露了 |
| `protocol != 0` | `-93` | EPROTONOSUPPORT |

**与 Linux 的差别**（写在这里，不藏在代码里）：这一对是 **AF_INET 回环**、不是 `AF_UNIX`；
没有 `SCM_RIGHTS`；`getsockname` 报 AF_INET；两个 fd **不是"最低的两个"**（建的过程中要
占一个监听者，用完关掉）。

### 1.3 `epoll`：水平触发，架在 `select` 上

Windows 上没有 epoll，也没有"接一下就能用"的东西：**IOCP 是完成端口**（投递异步 IO、等
完成），与 epoll 不是一回事，接进去等于重写整个 IO 模型。所以这里做的是**水平触发**的实现：
登记表（4 个实例 × 64 条）在 shim 自己的状态区里，`epoll_wait` 把登记的 socket 按掩码拼成
读/写集（外加一个**影子异常集**，错误从那儿冒出来）交给 WinSock `select`，再逐条判就绪。

* `epoll_create1`：拿一个空实例 + 一个 fd（`WS_FD_EPOLL` 型，`handle` 记实例号）；
  `close` 时把实例标成没人用 —— **不回收的话四次 create 就撞 `EMFILE`**，语料里专门钉了
  这一格（关掉再造一个）。
* `epoll_ctl`：`EEXIST` / `ENOENT` / `EPERM`（那个 fd 不是 socket）/ `ENOSPC` / `EFAULT`
  都照 Linux；`struct epoll_event` 在 x86-64 Linux 上是 **packed**
  （`events@0`、`data@4`，12 字节一条），写出去时照这个布局。
* `epoll_wait`：超时是毫秒；空登记表 = 睡超时再回 0（与 `select`/`poll` 那一格同形）；
  已关闭的 fd 在 `epoll_wait` 里被跳过（Linux 是自动摘掉，结果一样）。

**四条写明的差别**：

0. **只认 socket 型 fd** —— Linux 的 epoll 还能收**管道**（以及 `eventfd`、`signalfd` 那一类），
   往这儿 `epoll_ctl` 会报 `EPERM`。**这一条是实测出来的，不是猜的**：CI runner 上 stdout
   是管道，语料里原本那格"拿 fd 1 当不是 socket 的 fd"在那儿给出 `0`、在本机给出 `-1` ——
   于是它成了**环境相关**的判据，被换掉了（现存的那格改成"op 不认识"，两边稳定 `EINVAL`）。
   要补这一条得把 shim 的"是不是能等的东西"从"是不是 socket"放宽到"是不是可等句柄"，
   而 Windows 侧对应的东西是 `WaitForSingleObject` 那一族，语义不同（见 `docs/221` 那条
   "只认 socket 型 fd"的偏差）；
1. **`EPOLLET`（边沿）不兑现** —— 当水平触发处理。安全（不会丢事件），代价是"靠边沿省
   唤醒"的程序在 Windows 上会多醒几次；
2. **`EPOLLHUP` 不合成** —— Linux 在对端关闭时会给 HUP，这里探测不到（`EPOLLERR` 会给，
   因为那正是 WinSock 的 except 集报出来的形状）；
3. **返回顺序是登记顺序** —— Linux 说"顺序没规定"，但同一个程序在两个平台上可能看到
   不同的次序。

## 2. 契约：`NotDispatched` 这一族**删掉了**

六个号从 `NotDispatched` 移进 `Syscall` 之后，那一族**空了**。而 Loment 的**检查器不许空
枚举**（`loment_net_test` 当场报「枚举 NotDispatched 为空」）—— 所以它是**删掉**的，不是
留个空壳。契约的 `version` 到 `2026-10-12.1`，头注里写明了这件事。

四处对账面同步（`lom_audit` 的 `[net/…]` 四节）：`lomentc.SURFACE_NUMBERS`、
`lomelf.PE_SYSCALLS`、自举 `checker.lomt::chk_num_is_net`、自举
`potato.lomt::pt_surf_code`。计数 **23 / net 15 / 点名不做 6 → 29 / net 21 / 点名不做 0**。

`lom_audit` 里那条"点名不做 ∩ 派发面 = ∅"在枚举缺席时按**空集**判 —— 判据留着：
哪天谁又点一个号进"不做"，或"顺手"实现了一个没进表的号，两边**都会**红
（后者由 `loment_net_test` 的第四个反向用例钉：往派发面塞一个 `9999`）。

## 3. 判据

新加三份语料，与已有的五份一起走同一条路（`NET_CASES`，两条动态判据都跑它）：

| 语料 | 钉住什么 |
|---|---|
| `msgdemo` | TCP 三条 iovec 散出去、按 2+2+2 收回来（`ABCDEF` 落位）；UDP 用 `sendmsg` 带 `msg_name` 发一个数据报（两个 iovec 拼成 `hey`）、用 `recvmsg` 收回来并核对来源地址与 `msg_namelen` |
| `spdemo` | STREAM 来回各一次、DGRAM 一次收发；两条**错误路**的错误码（`-95` / `-22`）**打印出来比**，不是只看符号 |
| `epdemo` | 空等超时 0；写两个字节后 `n=1 / ev=1 / data=7`；改 `EPOLLOUT` 后 `n=1 / ev=4 / data=9`；四条错误路（`-17` `-2` `-22` `-22`）逐字比；**关掉再造一个实例** |

判定方式与既有语料相同：**同一份 `.lomt`，PE（本机原生）与 ELF（WSL）输出逐字节相同、
退出码相同**。本机实测 `loment_pe_test` **16/16**、`loment_net_test` **3/3**；冻结的 shim
**15208 B**（29 个号 —— 复测 2026-10-12；`epoll_ctl` 那条 op 校验是后来补的，见 §1.3 第 0 条）。

## 4. 三层账（`docs/227` §5 的更新）

| 层 | 之前 | 现在 | 剩下的是什么 |
|---|---|---|---|
| 运行时 | ≈97% | **100%** | 没有"点名不做"了（§2）。已知差别都写在第 1 节，不是欠账 |
| 声明 | ≈85% | ≈85% | `net_ladder`（照 `gc_ladder` 的形"按档拆"）—— **待定题**：要先回答"按哪一维拆" |
| 准入 | ≈5% | ≈5% | 那一层缺的不是工作量，是**还没决定要不要做**（`docs/218` §4） |

也就是说：**"彻底完成联网模块"在运行时层已经做到了**；剩下两层的入口都是一句话的决定，
不是一个补丁 —— 要么定下 `net_ladder` 拆哪一维，要么明确"准入那一层不做"。
