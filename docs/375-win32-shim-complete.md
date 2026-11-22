# 375 · PE 垫片补全：385 个 Linux 号，每个都有处置

> 状态：**已落地**（2026-11-21）
> 上游：`docs/217`（socket 面）、`docs/221`（poll/select）、`docs/234`（sendmsg/epoll）、
> `docs/311`（PE 上的 Linux guest ABI）
> 判据：`tools/loment_pe_test.py`（28 条，含本条新增 6 条）

---

## 0. 一句话

PE 运行时过去只有一张"用到了才补"的号表，**补没补**只能靠翻代码。这次把它改成一张
**说明书**：`tools/win_syscall_table.py` 收全了 Linux x86-64 的 385 个系统调用号，
每个号要么在派发面里（146 个），要么带一句**有名字的理由**拒绝（239 个）。
运行时的行为没变 —— 表外的号仍然返回 `-ENOSYS`；变的是**为什么**。

**L0 契约**：`lom/net.lom` 的 `Syscall` 跟着扩到 146 条（只增不减，旧号不挪位）。
下游影响：编译器/链接器/自举 checker/自举 potato 都按这张表逐条对账，新增枚举项
不需要它们改代码；compat 线不读这张表。

---

## 1. 从"29 个号"到"385 个号都有处置"

| 量 | 补全前 | 补全后 |
|---|---|---|
| 派发面的号 | 29（`docs/311` 记的数） | **146** |
| Linux 号有名字的 | —— | **385**（`asm/unistd_64.h`，Linux 6.18 头） |
| 显式拒绝的号 | 0（只是"表外"） | **239**，归入 **37 类**理由 |
| DLL | kernel32 / ws2_32 / pdh / psapi | + `advapi32`（`SystemFunction036`）+ apiset `api-ms-win-core-synch-l1-2-0`（`WaitOnAddress` 族） |

三档处置：

- **forward**：一次 Win32 调用就是答案（`open`/`read`/`write`/`stat`/`mkdir`/`rename`…）；
- **emulate**：shim 合成 Linux 语义（`stat`/`statx` 布局、`poll`/`select`、socket、
  `epoll`、`mmap` 登记表、`utime` 的 FILETIME 换算、`futex` 的等待者计数…）；
- **refuse**：写下理由，运行时照旧 `-ENOSYS`。

判据 `test_pe_syscall_classification_covers_the_linux_table` 把这张表钉成**划分**：
`名字集 == 已实现 ∪ 拒绝`，且**派发面 == 已实现**（两个方向）。理由的键必须来自
`REFUSE_REASONS`，所以"为什么不做"不能是一句随手写的散文。

---

## 2. 这一轮补上的号

### 2.1 位置读写与散列/聚集

`pread64`/`pwrite64`（17/18）不动文件位置（取旧位置 → 定位 → 读/写 → 放回）；
`readv`/`writev`（19/20）**逐 iovec 直接落 ReadFile/WriteFile/send/recv**，不摊平 ——
没有 64 KiB 上限，短读/短写就地停。

### 2.2 链接 / 锁 / 属性 / 时间

`flock`（`LockFileEx`/`UnlockFileEx`，整段 4 GiB）；`link`（`CreateHardLinkA`）；
`symlink`（`CreateSymbolicLinkA`，没有开发者模式时按 EPERM 失败）；`readlink`
（`GetFinalPathNameByHandleA`，**会解穿整条链**并剥掉 `\\?\`）；`chown` 族与
`fchmod`/`fchmodat`（Windows 没有 POSIX 所有权，**存在即成功**，与 `chmod` 同一口径）；
`utime`/`utimes`/`utimensat`（含 `UTIME_NOW`/`UTIME_OMIT`）；`fallocate`（只扩长度）；
`fadvise64`、`sync`/`syncfs`/`sync_file_range`（0）。

### 2.3 内存 / 进程 / 杂项

`mremap`（**只做匿名映射**：分配新块、搬 min、放旧块；文件映射明确拒绝）；
`sched_getaffinity`（按 `GetProcessAffinityMask` 填位图，返回 8）/`sched_setaffinity`；
`futex`（WAIT 走 `WaitOnAddress`，WAKE 走 `WakeByAddressSingle`，并自己维护
**等待者计数表**——`WaitOnAddress` 不报"唤醒了几个"）；`getrandom`
（`SystemFunction036`）；`ioctl`（`TIOCGWINSZ`/`FIONREAD`，其余 `-ENOTTY`）；
`fcntl`（`F_GETFD`/`F_SETFD`/`F_GETFL`/`F_SETFL`/`F_DUPFD`/`F_DUPFD_CLOEXEC`）；
`sendfile`（4096 一块搬，支持回写 `offset*`）。

### 2.4 同义号

`creat`、`ppoll`、`pselect6`、`epoll_create`、`epoll_pwait`、`epoll_pwait2`、
`renameat2`（flags=0）、`fchmodat2`、`mknod`/`mknodat`（只建普通文件）、
`fchownat`、`futimesat`、`linkat`/`symlinkat`/`readlinkat`、`faccessat2`、
`openat2`（只认 `resolve = 0`）、`close_range`、`getcpu`、`set_tid_address`、
`ioprio_set`/`ioprio_get`，以及一组进程身份号
（`setuid`/`setgid`/`setpgid`/`getpgrp`/`setsid`/`setre*`/`getres*`/`getgroups`/
`setgroups`/`getpgid`/`getsid`/`setfs*`）—— 设置类成功、查询类给固定值。

---

## 3. 明确拒绝的几大类（理由在表里）

| 类 | 代表号 | 一句话 |
|---|---|---|
| 信号 | 13–15、62、127–131、200、234、282、289、297、424 | Win32 没有 POSIX 信号模型 |
| 进程创建/执行/等待 | 56–59、61、247、322、435、101、310–312、434、438、440、448 | 没有等价的 Win32 语义 |
| 命名空间/挂载 | 155、165、166、428–433、442、457、458 | Linux 特有对象 |
| System V / POSIX IPC | 29–31、64–71、240–245 | 没有等价物 |
| aio / io_uring | 206–210、333、425–427 | 没有内核异步提交面 |
| xattr | 188–199、463–466 | 替代是 NTFS ADS，语义不同 |
| 定时器/信号 | 36–38、222–226、283–287 | 依赖信号投递 |
| 权限/配额/模块/端口 | 125、126、179、443、174–178、313、172、173 | 特权/内核面 |
| 安全（seccomp/bpf/LSM/landlock） | 317、321、444–446、459–461 | 没有等价物 |
| robust futex / 新 futex ABI | 273、274、449、454–456 | 要内核参与；用 202 的 WAIT/WAKE |

**私有号** 4096（lmgr PDH）与 4097（通用 Win32 调用器）**不在这张表里** —— 它们不是
Linux 号，`lom/*.lom` 那道门也没收它们（`docs/303`）。

---

## 4. 与 Linux 的已知语义差（写在这里，不藏）

1. `readlink` 拿到的是**最终目标**（Windows 的 API 只给这个），不是中间一跳；
2. `ioctl(TIOCGWINSZ)` 在非控制台上返回 `-ENOTTY`（与 Linux 一致），控制台上按
   当前控制台缓冲区的窗口矩形填；
3. `chown` 族、`fchmod` 族、`umask` 在 Windows 上没有对应物：存在即成功 / 只记值；
4. `sched_getaffinity` 只模拟当前进程，返回 8；`getcpu` 固定写 0；
5. `mremap` 只在匿名映射上做，且**总是可能换地址**（按 `MREMAP_MAYMOVE` 的语义实现）；
6. `futex` 的等待者计数表有 64 个槽；表满时仍会等待/唤醒，只是唤醒数会少报；
7. `mknod` 只建普通文件；`openat2` 只认 `resolve = 0`；`renameat2` 只认 `flags = 0`；
   所有 `*at` 变体都**忽略 dirfd**（Windows 没有"目录 fd 作基准"这条命名空间）；
   `mremap` 忽略 `MREMAP_FIXED`（总是按"可以搬家"实现）。

---

## 5. 这一轮暴露并修掉的问题

1. **`statx` 的 `stx_mtime` 写到了 80**（那是 `stx_btime`）。Linux 的布局是
   atime@64 / btime@80 / ctime@96 / **mtime@112**。
2. **`sched_getaffinity` 原来用 124/125** —— 那是 x86-64 的 `getsid`/`capget`；
   正确的是 **204/203**。这条是拿 Linux 侧对拍探针时现形的。
3. **`mov_rr(R11, RDI)` 有两处漏了 `a.emit`**（模块级函数只**返回**字节），
   使清零循环写到了野指针 —— 症状是 stat 家族整族段错误。
4. **`open` 的访问位**：本轮的 `pread`/`pwrite` 探针先暴露了 `O_RDWR` 被开成只读句柄；
   同一时段上游 main 已把它按 `O_ACCMODE` 重写（含 `O_APPEND`/`O_EXCL`/`O_TRUNC`），
   本分支**以那份为准**，不再重复实现。
5. **`WaitOnAddress` 族不在 `kernel32.dll`**：实测 Win11 的导出表里没有，写成 kernel32
   会让进程起不来（退出码 127）。正确写法是 apiset `api-ms-win-core-synch-l1-2-0.dll`。

## 6. 加一个号的登记处（照这张清单走）

1. `tools/lomelf.py`：常量 + `PE_SYSCALLS` 一行 + 处理器（必要时加导入/DLL）；
2. `tools/lomentc.py`：`SURFACE_NUMBERS`（面名必须与 `PE_SYSCALLS` 逐条相同）；
3. `tools/lomentc.py`：若是 `net` 号，还要进 `NET_WIDTH_RUNGS`（否则 Potato 自检报
   `total_net` 与 `surfaces.net` 不一致）；
4. `loment/selfhost/potato.lomt`：`pt_surf_code` 的同一个号；
5. `tools/win_syscall_table.py`：重新生成（已实现集由 `PE_SYSCALLS` 派生，不用手抄）；
6. `loment/tools/lomelf.lomt`：`PE_WS_SIZE` / `PE_IMPORT_DESC`（加 DLL 时）；
7. `python tools/lomelf.py --dump-win-shim loment/tools/win_shim_data.lomt`；
8. 判据：在 `loment_pe_test.py` 加一条**行为**判据（不是只看返回 0），并抬
   `.github/gate-floors.txt`。
