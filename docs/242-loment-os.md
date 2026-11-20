# 242 · `os`：第四个随包库，判据是**与 CPython 在同一个环境里对跑**

> 状态: **已落地**（2026-10-13）· 自检: `tools/loment_os_test.py` 4/4 ·
> 模块自检 4 份全过（`python tools/loment.py test <模块>`）· 门禁: `ci.py --static-only`

## 0. 一句话

`lompi/store/os/0.1.0/` 是 `std` / `host` / `internet` 之后的**第四个随包库**：一个程序向
操作系统要的那些事 —— 目录与文件（`sys`）、时钟与睡眠（`time`）、信号（`signal`）、
终端（`tty`）、身份（`user`）—— 全用 Loment 写，共 5 个模块 + 门面。

## 1. 四个包的分界（**这一节是给下一个加模块的人看的**）

现在仓里有四个随包库，边界是刻意划的：

| 包 | 是什么 | 一句话判据 |
|---|---|---|
| `std` | 可移植层 | **一个系统调用都不发**（`env` 解环境块、`proc` 解 `/proc/self/*`、`clock` 排时间文本） |
| `host` | **工具链自己的手脚** | 它的文件头写着"给跑在 FujoOS 外面的工具链用"：`fs` / `io` / `dir` / `argv` / `stat` / `log` |
| `internet` | 网络那一层 | `ip` / `uri` / `http` / `dns` / `sock` / `tcp` / `udp` |
| **`os`** | **程序自己的手脚** | `sys` / `time` / `signal` / `tty` / `user` |

**重叠最明显的是 `host/fs` 与 `os/sys`** —— 分界写在这里，免得两处都长一点：
`host/fs` 是"读一个文件全部内容"这种**成件事**的接口（`fs_read` / `fs_write` / `fs_exists`），
`os/sys` 是**逐条系统调用**（`mkdir` / `unlink` / `rename` / `chmod` / `link` / `pipe2` / `lseek`）。
要前者用 `use fs`，要后者用 `use sys`。

依赖方向**单向**：`os` 不认识 `host` / `internet`，它们也不认识 `os` —— 这一层是叶子。

## 2. 五个模块

| 模块 | 公开前缀 | 有什么 |
|---|---|---|
| `sys` | `sys_` | `uname`、`getcwd`/`chdir`、`mkdir`/`rmdir`/`unlink`/`rename`/`chmod`/`access`、`symlink`/`link`/`readlink`、`openat`/`close`、`pipe2`/`dup2`/`lseek`/`fsync`/`ftruncate`、`sys_cstr`（把不带 NUL 的 Loment 字面量拼成 C 串） |
| `time` | `time_` | `clock_gettime`（`REALTIME` / `MONOTONIC` / …）、`nanosleep`、timespec 读写 |
| `signal` | `signal_` | 忽略 / 恢复默认 / 发一个；`signal_ignore_pipe` 一行版 |
| `tty` | `tty_` | `isatty`、`TIOCGWINSZ`（行列数）、termios 原始模式（存/还原成对） |
| `user` | `user_` | `getpid`/`getppid`/`getuid`/`geteuid`/`getgid`/`getegid`、`sched_yield` |

**与 `std` 的分工不是重复**：`std/env.lomt` 解**已经拿到的**环境块，`os/user` **拿**身份；
`std/clock.lomt` 把时间戳**排版**成 `1h1m`，`os/time` 去**拿**时间戳。
合起来才是一条链：`os/time` 拿数 → `std/clock` 排版。

## 3. 判据：与 CPython 在**同一个环境里**对跑

`tools/loment_os_test.py`，4 条。第 4 条是关键那条：

* 探针编成 **ELF**（不是本机 PE —— 这一层全是 Linux 的号），
  Windows 上交给 **WSL** 跑，Linux 上直接跑；
* **CPython 的参考脚本跟探针在同一个环境里**（同一个 WSL / 同一台 Linux）跑同一串事；
* 于是 `cwd`、`uname`、`uid`/`gid`、`mkdir`/`rename`/`unlink`/`rmdir` 的**副作用**、
  `isatty` 这些能**逐字节**比 —— 13 项；剩下 5 项是**谓词**（`pid > 0`、单调钟睡 50ms
  之后前进 ≥40ms、墙上时钟与 CPython 差 ≤120s、忽略 SIGPIPE 成功、发给自己之后还活着）。

**两边环境不同的话，比的就不是同一个操作系统了** —— 所以参考那一份也必须进 WSL。

**为什么模块自检里一个系统调用都没有**：`loment.py test` 是把 Loment 译成 Rust 跑，而那里的
`syscall4` 是 Linux 的 `asm!("syscall")`。**在 Windows 上它照样"返回一个数"** —— 实测：
`user.lomt` 第一版有三条读身份的自检，在 Windows 上**过了两条**（`getpid() > 0` 也过了），
而那个数是巧合。所以 `os` 的自检只放**纯逻辑**（C 串拷贝、timeslice 读写、sigaction 结构体
拼装、termios 位运算、winsize 取值），真实行为一律在这里判。

## 4. 判据抓出来的一个真缺陷

**`rt_sigaction` 少给了一个实参。** 第一版写的是
`syscall4(13, sig, sa, 0)` —— 三个实参。而 `rt_sigaction` 有**第四个**：信号掩码的字节数，
恒 8。少了它内核直接返 `-EINVAL`，于是"忽略 SIGPIPE"**静默失败**，而后面那条
"发给自己还活着"就成了**假绿**（进程没死是因为……信号根本没发出去的成功路径也一样）。
改成 `syscall6(13, sig, sa, 0, 8, 0)` 之后才对。

这条值得记一笔的原因：**它是一条纯逻辑自检抓不到的错**（结构体摆得完全正确），
只有真发系统调用、真看返回值才现形。

## 5. 边界（**如实列，不假装有**）

* **号是 x86-64 Linux 的**（与 `lom/net.lom` 同一套）。PE 上除那 8 个之外一律返回 -1 ——
  本模块的函数在 PE 上是**真的失败**，不是静默。
* **不装自己的信号处理函数**。x86-64 上要用 C 函数指针 + `SA_RESTORER` 指向正确的还原桩，
  而本语言今天**没有函数指针**（`docs/232` §3.3 同一条限制）。只放 `SIG_IGN` / `SIG_DFL`
  这两个特殊值 —— 既用得上又不会骗人。
* **不改身份**。`setuid`/`setgid` 在表上，但故意没包：一个库不该有"把进程降权/提权"的
  入口，那是**调用方的决定**。
* **不做进程控制**（`fork`/`exec`/`wait`）。那要连"子进程退出码怎么回传"一起设计，
  是下一笔的事，不是这一笔漏了。

## 6. 加模块前必读：命名硬约束

与 `internet` 同一条（`docs/241` §7）：**包内模块的顶层名字在一个单元里是平的**，
`resolve_name` 在包内模块层遇到**同名两处命中直接报错**。所以：

* `os` 不能用 `env`（std 占了）、`proc`（`loment/lib/` 与 std 都占了）、`clock`（std 占了）
  —— 这也是 `time` 而不叫 `clock` 的原因；
* 内部前缀也要挑没被占的拼写。

判据 `test_os_names_do_not_collide_with_the_other_packages` 逐条比 `os` 与
std + host + internet 全部模块的顶层名字。

## 7. 与其它文档的关系

* `docs/241`：`internet`（第三个包）；`docs/232`：`std` 的 libc 面（LibL 这个名字）；
* `docs/168`：包系统（包名 == 入口文件名、依赖来自源码的 `use`）；
* `docs/234` / `docs/236`：net 运行时面与 lompi 的联网 —— `os` **不参与**，它不发网络号。
