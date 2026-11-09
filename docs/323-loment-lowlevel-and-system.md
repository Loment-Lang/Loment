# 323 — `lowlevel` / `system`：从 3 个模块到各 11 个

`docs/317` 建这两个包时各只有三个模块（`elf` `pe` `abi`、`caps` `seccomp` `ns`）：
刚好够"读一个 ELF 头、装一个 seccomp 过滤器"。真正写底层工具时缺的是**中间层** ——
变长整数、位域、归档、调试信息、重定位名字，以及内核那一侧的常量表。

这一份各补 8 个。

## `lowlevel`（11 个）

| 模块 | 公开面 | 解决什么 |
| --- | --- | --- |
| `leb128` | `lb_*` | LEB128 有/无符号编解码（DWARF、wasm） |
| `bitpack` | `bp_*` | 任意位偏移的取位/置位，LSB-first 与 MSB-first 两套 |
| `ar` | `ar_*` | `.a` 归档成员遍历（60 字节定宽头、偶数对齐） |
| `macho` | `mc_*` | Mach-O 头（四种 magic、7 个 u32） |
| `coff` | `coff_*` | COFF `.obj` 文件头/节头 |
| `dwarf` | `dw_*` | `.debug_info` 单元头（v2–v5 的版本差异） |
| `reloc` | `rc_*` | ELF 重定位类型名（x86-64 / AArch64） |
| `calling` | `reg_*` | 调用约定寄存器表（SysV / Win64 / AAPCS64） |

原有 `elf` `pe` `abi` 不动。

## `system`（11 个）

| 模块 | 公开面 | 解决什么 |
| --- | --- | --- |
| `errno` | `er_*` | 编号 → 名字/人话/`perror` 形态 |
| `syscalls` | `scn_*` | x86-64 系统调用号 ↔ 名字（常用项） |
| `fcntl` | `fcnt_*` | `O_*` / `F_*` 位名与反查 |
| `mmap` | `mm_*` | `PROT_*` / `MAP_*` 位名与反查 |
| `poll` | `pw_*` | `POLL*` / `EPOLL*` 事件位名 |
| `resource` | `rlim_*` | 16 个 `RLIMIT_*` 与 `struct rlimit` 读取 |
| `procfs` | `pf_*` | `/proc/<pid>/stat` 与 `MemTotal:` 这类行 |
| `ioctl` | `iol_*` | `TIOCGWINSZ` 等请求号与 `struct winsize` |

原有 `caps` `seccomp` `ns` 不动。

## 设计边界

- **表都是"结构"，不是会过期的数据**：重定位类型、系统调用号、`O_*` 位、`RLIMIT_*`
  属于 ABI，随内核版本变的机会很小；表里只放常用项，未知返回 `UNKNOWN`/`-1`，
  不猜。
- **位序要写清楚**：`bitpack` 的两套位序（LSB-first / MSB-first）分别命名，
  不留"默认"—— 跨字节边界时这两套结果完全不同。
- **解析器宁严勿松**：`pf_stat` 要处理"进程名里有空格与括号"，所以按**最后一个
  右括号**切；`dw_unit_header` 对 v5 与 v4 的字段顺序分开处理，地址宽度只接受 4/8。
- **不碰 syscall 指令本身**：这些模块只做"编号/名字/结构"的翻译，发系统调用仍由
  调用方（或 `os` 包）负责。

## 判据

- `loment_domains_test::test_lowlevel_and_system_packages`：48 项行为，与 Python
  逐字节一致。其中 `errno` / `fcntl` / `termios` 在 Linux 上用 Python 标准库当
  独立对照物；Windows 上没有这些模块时退回已知常量（`sys.platform` 分支）。
- 孪生：16 个新模块 + 两个门面共 18 份产物，参考实现与自举实现逐字节相同。
- 计数：`lowlevel`、`system` 各 4 → 12 个 `.lomt`，整棵树 246 → **262** 个文件。

## 过程中修掉的真缺陷

- `mmap` 的内部前缀 `mq_` 与 `macho` 撞名（两个包同时 `use` 时是平命名空间硬错）。
- `leb128` 缺少 `use mem`（`used` 回写用 `mem_store32`）。
- `bitpack` 的 `u32 / u64` 混合除法被拒（改为先升 u64 再落回 u32）。
- `procfs` 里 `mut_` 前缀的变量名一度写成 `mut_e`（避开 `mut` 关键字的边界）。
