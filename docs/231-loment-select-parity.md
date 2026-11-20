# 231 · PE 运行时补 `select`(23)：位图 ↔ `fd_set`，外加出参归一化

> 状态：**已落地**（2026-10-11）。`docs/217` §5 / `docs/221` §7 那张"点名不做"名单里，
> `select` 从**故意不做**变成**已做** —— 这是名单里第一个**因为"分歧能被说清、能被翻译"
> 而移出去的号**（`epoll`/`sendmsg`/`socketpair` 仍留着，理由照旧）。
>
> 一句话：**Linux 的 `select` 与 WinSock 的 `select` 不是同一个函数**。同名、同签名、
> 同参数个数，但有两处语义真的不同；把它们翻译掉，是这一格的**全部工作量**。

## 1. 为什么当初不做，现在为什么能做

当初（`docs/221` §7）列了两条：

1. **能力上重复** —— `poll` 已经能等 socket，而 `poll` 结构体更简单、没有 `FD_SETSIZE` 那堵墙。
   这一条**不构成"不做"的理由**（重复的 API 也得在，因为**别人写的程序**在用它）；
   它只是解释了为什么这件事排得靠后。
2. **`exceptfds` 没有一对一映射** —— WinSock 的 except 集是"带外数据 **+ 连接失败**"，
   Linux 的 except 集是"异常条件"，连接失败在 Linux 上走 **writefds**。

第 2 条**至今成立**。改变的是结论：分歧不在"能不能翻译"，而在**翻译要不要在出参上做**——
不做归一化的 `select` 会让"等 writefds 判 connect 成没成"这个**最常见**的写法在 Windows 上
永远等不到（见 §3）。既然分歧点只有两处、且都能在 shim 内部闭合，那就补。

## 2. 两处机械翻译

**① 集合：Linux 位图 ↔ WinSock `fd_set`。**
Linux 的 `fd_set` 是 `__fds_bits`（每 8 字节一个 `u64`，`nfds` 给上界）；
WinSock 的是 `{ u_int fd_count; SOCKET fd_array[64]; }`。翻译靠 `loment_pe_test` 里
`poll` 那一格已经在用的那套集合助手（`__ws_set_init/add/has`）+ 三个新助手
（`__ws_bit_get/set/clr`，**只认第一个 `u64`** —— `nfds > 64` 直接 `EINVAL`，
与 WinSock 的 `FD_SETSIZE = 64` 同一堵墙，不假装能等更多）。

**② 时间：Linux `timeval` 是两个 `i64`，Windows 的是两个 `long`。**
所以 `__ws_sel_go` 把秒夹到 32 位再写进 `WS_POLLTV`（与 `poll` 共用那一格）。
夹之前**比的是秒不是指针** —— 这是实测撞出来的：`alloc` 返回的是高地址
（PE 基址 `0x140000000`），拿指针去 `jle` **从不跳**，于是每一格都被夹成
`0x7FFFFFFF`（≈36 分钟），症状是**整支挂住**。

**③ 全空集合 = 睡。** Linux 的 `select(0, NULL, NULL, NULL, &tv)` 是"睡 `tv`"，
而 WinSock 拿全空集合直接 `WSAEINVAL`。所以三个集合都没有成员时走 `Sleep`
（无 `tv` = 无限等，与 Linux 同义）—— 这一条与 `poll` 那一格同一个处理。

## 3. 出参归一化：这一格的**真工作量**

### 3.1 连接失败报在哪个集合

实测（本机 Windows + WSL，同一支 `.lomt`）：

| | 非阻塞 `connect` 失败后，`select` 把 socket 报在 |
|---|---|
| Linux | **writefds**（`R=1 / IN WRITE`） |
| WinSock | **exceptfds**（把同一个 socket 同时放进两边，只有 except 那一边亮） |

所以 shim 里多了一个**影子 except 集** `WS_SHADOW_EX`：**调用方 write 与 except 两栏的成员
都灌进去**，交给 WinSock 当 exceptfds。

这里有个必须踩到的坑：**光给 WinSock 一个空集合没有用** —— `fd_set` 是"要等谁"的入参，
WinSock 只报**集合里**的句柄。所以调用方只给 writefds（等 connect 成没成的标准写法）时，
影子集必须是 **write 那一栏的副本**，不能是空集。这一条是实测撞出来的：
影子集装 write 成员**之前**，`seldemo` 的第 ③ 格一直报"返回 0"（干等到超时）。

### 3.2 `SO_ERROR` 会被读掉，所以要自己留一份

归一化的判据是"这个 socket 身上有没有错"—— 只能问 `getsockopt(SO_ERROR)`，而它
**读即清零**（两个平台都一样）。于是：

* `select` 内部读完 → 存进 `WS_SERR[fd]`；
* 应用自己再读 `SO_ERROR` 时，`__ws_getsockopt` **先把那份还回去**（`WS_SERR[fd]` 非零就返回它，
  并把格子清掉），两边都不丢。

### 3.3 顺序：先分"连接失败 vs 真 OOB"，再扫 write 名单

两趟归一化的**先后是有讲究的**（第一版写反了，`seldemo` 的计数翻倍）：

1. **归一化（一）**：走影子集，逐个问 `SO_ERROR`。非零 = 连接失败 ⇒ 存一份、
   **从影子里摘掉**、按调用方是否要过 writefds 决定搬不搬进 write 集；
   零 = 真正的 except 事件（带外数据）⇒ 只有调用方**点名要过** exceptfds 才留着。
2. **归一化（二）**：按调用方的 **write 名单**（自己留的 `WS_SELFLAG[fd]` 位图）再问一遍
   `SO_ERROR` —— 这一趟是给"**同步就失败**的 connect"兜底的：WinSock 对这种 socket
   在 `select` 里**什么都不报**（实测），但 Linux 会把它报成**可写**。

两趟都必须**去重**：同一个 socket 可能既被 WinSock 报进 write 集、又被归一化搬一次，
加两遍会让返回的**计数**翻倍（Linux 数的是三个集合里置位的**位数**，不是"处理了几次"）。

> 顺序反了会怎样（第一版的形状）：归一化（二）先跑，它把 `SO_ERROR` 读掉 ⇒ 归一化（一）
> 随后读到 0 ⇒ 把一次**连接失败**误判成 OOB，塞进调用方的 except 集 ⇒ 计数 2、位也错。

### 3.4 第三趟的 except 栏读**影子集**

WinSock 的结果只落在影子集里，三个 `WS_FDSETS` 集合自始至终只是**入参**。第一版忘了这一点，
第三趟照样去读 `WS_FDSETS + 2×CAP` —— 读到的是**调用方输入的陈旧拷贝**，于是"凡是问过的
fd 都被当成就绪"（`seldemo` 里表现为：只问 except 的那一格报了个满的）。

## 4. 留下的平台差异（写明的，不是漏）

* **Windows 把"本地端口被拒"升上来要 ~2–3 秒**（实测 2.68 s / 2.99 s），Linux 是**瞬间**。
  这不是 `select` 的翻译问题，是 connect 路径本身 —— 所以 `seldemo` 的第 ③ 格超时给的是
  **3 秒**而不是 300 ms（300 ms 在两个平台上都"没等到"，但那说明不了归一化对错，
  只会把一条正确的判据写成红的）。**这一条对用的人是真的**：同一个"连本地没人听的端口"
  的程序，Windows 上要多等两三秒。
* **`SO_ERROR` 的取值时机**：WinSock 在 exceptfds 报出 socket 的**那一刻**，`SO_ERROR`
  可能还是 0（错误随后才挂上）；Linux 上失败与可写是同一瞬间的事。所以归一化读到的 0
  不能当"没失败"用 —— 影子集里的成员只要被报出来、且调用方要过 writefds，就按失败处理。
* **不带外数据（`MSG_OOB`）没做**：Linux 的 exceptfds 主要就是给 OOB 用的，而本仓的
  socket 路径上没有任何 OOB 的使用方式（`MSG_OOB` 不翻译）。真要报 OOB 的话，
  归一化（一）里那条"零 = 真 except 事件"的分支就是挂载点。
* **同步失败的 connect**：Windows 上 `connect()` 直接返回 `WSAECONNREFUSED`，
  且此后 `getsockopt(SO_ERROR)` **一直是 0**（实测；与 Linux 不同，Linux 会留 111）。
  归一化（二）会让它**在 select 里报成可写**，但 `SO_ERROR` 补不出那个 111 ——
  这一格没有判据盖，写在这里当已知偏差。

## 5. 判据

| 判据 | 钉住什么 |
|---|---|
| `loment_pe_test::test_pe_and_elf_agree_on_net_programs` 的 `seldemo` | **同一个 `.lomt`，PE 与 ELF 输出逐字节相同**：可读 / 超时 / 连接失败在 writefds / `select` 当睡（四格，`SELECT READY OK` … `SELECT SLEEP OK`） |
| `loment_pe_test::test_frozen_shim_blob_matches_the_reference` | 冻在 `win_shim_data.lomt` 里的那份 == 现算的（孪生照抄的就是它） |
| `loment_pe_test::test_pe_selfhost_mirror_matches_reference` | 自举镜像产的 PE 与参考**逐字节**相同（含新的状态区大小） |
| `loment_pe_test::test_pe_shim_dispatches_the_documented_numbers` | 派发面 == `PE_SYSCALLS`（`select` 在里面） |
| `loment_net_test` | `lom/net.lom` 里 `select` 在 `Syscall`、不在 `NotDispatched`；四处对账逐条一致 |

实测（本机 Windows，2026-10-11）：

```
loment_pe_test            16/16
seldemo                   PE 与 ELF 逐字节相同（含连接失败那一格）
shim                      9751 B（23 个号）／状态区 WS_SIZE = 1070032
```

## 6. 顺带撞出来的两个真 bug（都不在 `select` 里）

* **`select` 处理段里插进去的小助手被"直落"执行** —— 助手 `__ws_sel_save_err` 一开始
  插在**主流程正中间**，于是 `select` 调用之后会**直落**进它，那个 `ret` 弹掉一个从没压过的
  返回地址 ⇒ **访问违例**（`0xC0000005`，无声、无输出）。教训：shim 是"顺序机器码"，
  子程序得**挪到流程之外**（现在排在整段的末尾）。
* **夹具自己的位图助手只写了第一个字节** —— `seldemo` 的第 ③ 格用的是第 4 个 socket，
  它的 fd 是 **8**，而 `fs_set` 把 `1 << fd` 截成 `u8` 再 `store8(p, 0, …)` ⇒ 写 0。
  于是 writefds 是空的、`select` 干等 —— 症状长得像"shim 没归一化"，其实是语料没置上位。
  修法是按 `fd / 8` 定位字节（现在语料里写着注释）。

## 7. 契约跟着挪（`lom/net.lom` 2026-10-11.2）

`select = 23` 从 `NotDispatched` **移进** `Syscall`（面 `net`）。这是**契约变更**不是新增：
`gates.why_equal` 保证 `Syscall` 与 PE 派发面**同宽**，运行时落地了，表就必须跟着挪
（审计 `[net/lomelf]` 会先红，那正是它该有的表现）。四处对账面一起动：
`lomentc.SURFACE_NUMBERS`、`lomelf.PE_SYSCALLS`、自举 `checker.lomt::chk_num_is_net`、
自举 `potato.lomt::pt_surf_code`；计数从 **22 / net 14 / 点名不做 7** 变成
**23 / net 15 / 点名不做 6**。
