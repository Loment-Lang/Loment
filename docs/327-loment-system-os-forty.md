# system / os 补到各 40 个模块

> 2026-11-09。`system` 12 → 40、`os` 7 → 40，共 **61 个新模块**，整棵 store 262 → 323 个文件。

## 1 为什么补

`docs/317` 拆出八个领域包时，`system` 只有 12 个、`os` 只有 7 个 —— 这两包是"写系统
程序"时的地面：内核常数、结构体布局、系统调用包装。此前薄到无法支撑一个真实的
命令行工具：想用 `epoll` 得自己抄事件位；想读 `cpu.max` 得自己想 `max` 的意思；
想 `statx` 得自己数 256 字节里的偏移。

这一轮按"一个 Linux 系统程序员会先后伸手去拿的东西"补齐，每个模块都带：

- **内核 ABI 的常数表**（编号、位掩码、结构体偏移）—— 魔数写错一位从来不报错；
- **纯逻辑**的打包/解码/文本渲染 —— 结构体字节与名字之间的往返；
- **薄系统调用包装** —— 只在必须发调用的地方发一条；
- **纯逻辑自检**（2–5 条）—— 不依赖机器环境，编译即跑。

## 2 system 新增 28 个

| 模块 | 覆盖 |
| --- | --- |
| `epoll` | `EPOLL*` 位、`epoll_ctl` 操作、12 字节事件打包、`epoll_create1/wait` |
| `eventfd` | `EFD_*`、8 字节计数器读写、`eventfd2` |
| `timerfd` | `TFD_*`、`itimerspec` 打包、`timerfd_create/settime/gettime` |
| `signalfd` | `SFD_*`、sigset 位集合、`siginfo` 解码、`signalfd4` |
| `inotify` | `IN_*`、16 字节事件头、`inotify_init1/add_watch/rm_watch` |
| `fanotify` | `FAN_*` 类与掩码、元数据布局、`fanotify_init/mark` |
| `mount` | `MS_*` / `UMOUNT_*`、`mount/umount2`、位名渲染 |
| `statfs` | `statfs` 结构体偏移、16 个文件系统魔数、`statfs/fstatfs` |
| `ptrace` | 24 个请求号、选项位、`user_regs_struct` 偏移、`ptrace` |
| `prctl` | 22 个选项号、名字/反查、`prctl`、进程名读写 |
| `futex` | 操作号与标志位、timespec 打包、`futex`、wait/wake 包装 |
| `cpusched` | `SCHED_*`、1024 位亲和性位图、`sched_*` |
| `cgroup` | cgroup v2 控制器文件名、`max` 语义、`cpu.max` 双字段、容量文本 |
| `netlink` | 协议号、`nlmsghdr`/`sockaddr_nl` 打包解码、`RTM_*` 名字 |
| `sysinfo` | `sysinfo` 结构体偏移、`mem_unit` 因子、开机时长文本 |
| `utsname` | 六段 65 字节字段、`uname`、内核三元组文本 |
| `memfd` | `MFD_*` / `F_SEAL_*`、`memfd_create`、封印读写 |
| `keyctl` | 命令号、四组权限位、`keyctl`、权限文本 |
| `iouring` | 20 个 opcode、64 字节 SQE 打包与 16 字节 CQE 解码、`io_uring_*` |
| `syslogk` | 内核环缓冲动作号、facility/level、优先级组合与拆分 |
| `reboot` | 两个魔数、七种命令、`reboot` |
| `ipc` | SysV IPC 标志与命令、`ftok` 推导、shm/sem/msg 包装 |
| `mqueue` | `mq_open` 标志、`mq_attr` 布局、六个包装、优先级 |
| `aio` | `iocb`/`io_event` 布局、八个 opcode、`io_*` |
| `bpf` | 命令/map/prog 类型、指令编码（含 64 位立即数）、`bpf` |
| `perf` | 128 字节 `perf_event_attr`、硬件/软件事件、`perf_event_open` |
| `procstatus` | `/proc/<pid>/status` 取值、数值解析、状态字母 |
| `mountinfo` | `/proc/self/mountinfo` 字段、`\040` 转义、major:minor |

## 3 os 新增 33 个

| 模块 | 覆盖 |
| --- | --- |
| `cmdline` | NUL 分隔的 argv 块：计数、取项、拼回命令行（含引号） |
| `paths` | 尾名/目录/扩展名/拼接/文本归一化/深度 |
| `readdir` | `getdents64` 变长记录解码、类型名、`.`/`..` |
| `file` | 读到底、写到底（EINTR 重试）、取大小、逐块拷贝 |
| `exec` | argv 向量构造、`execve`、`PATH` 搜索 |
| `spawn` | `fork` + `execve` + `wait4`、状态解码 |
| `wait` | `wait4` 状态字：退出/被信号/被停 |
| `pwd` | `/etc/passwd` 记录/字段/uid 换算 |
| `group` | `/etc/group`、成员名单二级切分 |
| `hostname` | `uname` 读、`sethostname`、RFC 1123 校验 |
| `cwd` | `getcwd/chdir`、相对路径解析 |
| `umask` | 读-改-还原、`rwx` 文本互转 |
| `tmpfile` | `O_CREAT|O_EXCL` 独占建立、撞号重试 |
| `lockfile` | `flock` 加解锁、pid 文件、存活判断 |
| `daemon` | 双 fork、`setsid`、pid 文件、就绪管道 |
| `logfile` | 追加写、`tag: body`、轮转名与级别前缀 |
| `watcher` | 自带 inotify：建立/监听/事件解码 |
| `interval` | timerfd 周期、`itimerspec` 打包、到期计数 |
| `alarm` | `alarm/setitimer/pause`、`itimerval` 打包 |
| `sigwait` | 信号屏蔽集、`signalfd`、同步接收 |
| `filestat` | `statx` 布局、模式文本、字段解码 |
| `tempdir` | 唯一目录名、递归删除（不跟随符号链接） |
| `random` | `getrandom`、十六进制、无偏随机与洗牌 |
| `sysconf` | 页大小/时钟频率/CPU 数、`online` 列表解析 |
| `select` | `fd_set` 位图、`select`、最低/计数 |
| `memory` | 页取整、`brk/mremap/mprotect/munmap` |
| `mmapfile` | 打开+映射一整个文件、同步、建议 |
| `fdio` | EINTR 安全的读/写/`pread`/`readv` |
| `pidinfo` | `/proc/<pid>/*` 路径与读取、存在性 |
| `civildate` | 公历换算（days-from-civil）、星期、`YYYY-MM-DD` |
| `boot` | `/proc/uptime`、`/proc/cmdline`、boot_id |
| `sysctl` | 点号键换路径、读 `/proc/sys`、内核版本 |
| `pgroup` | 进程组/会话、`kill`/`killpg` 语义 |

## 4 包清单跟着变

两包的真代码用到的构造并集都是 `alloc,str_eq,syscall`：

```rust
pub fn choose() -> str { return "std runtime"; }
pub fn requires() -> str { return "alloc,str_eq,syscall"; }
```

`test_every_package_manifest_matches_its_code` 拿代码当唯一真源，少写多写都红。

## 5 平名字唯一性：七次改名

store 是按包组织的，但 `use <模块名>` 是**平名字解析** —— 同名模块立刻变成
"名字导入有歧义"。第一版有七个撞名：

| 原名 | 撞到 | 改为 |
| --- | --- | --- |
| `os/args` | `dev/args` | `os/cmdline` |
| `os/calendar` | `world/calendar` | `os/civildate` |
| `os/dir` | `host/dir` | `os/readdir` |
| `os/io` | `host/io` | `os/fdio` |
| `os/proc` | `std/proc` | `os/pidinfo` |
| `os/stat` | `host/stat` | `os/filestat` |
| `system/sched` | `std/sched` | `system/cpusched` |

`math/math` 与 `std/math` 的撞名是包门面的既有形态，不在这条里。

## 6 判据

| 判据 | 钉住什么 |
| --- | --- |
| `loment_lompi_test::test_every_store_module_compiles_on_its_own` | 311 个模块各自过语法/类型/依赖/内建实参；没被任何探针 `use` 到的模块也有编译面 |
| `loment_domains_test::test_system_and_os_survey_packages` | 13 个新模块的 47 个纯逻辑输出与 Python 独立算的值逐字节一致 |
| `loment_lompi_test::test_package_carries_the_whole_store` | 随包的 store 323 个文件一件不少 |
| `loment_lompi_test::test_every_package_manifest_matches_its_code` | 两包 `requires()` 与代码并集相等 |

模块自己的自检（共 200 余条）全部是纯逻辑，不发起系统调用 —— 这与
`os/user.lomt` 头部那条边界一致：自检那条路跑不了系统调用，行为交给
`loment_domains_test` / `loment_os_test` 那一侧。
