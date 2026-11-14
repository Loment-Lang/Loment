# 303 · LomentManager：开发者本位的实时资源管理器套件（Windows 先行）

> 文档号说明：原编号 302 已被在飞 PR #1256 / #1259 占用，本文让到 303。

> 状态：**设计**（2026-11-04）· 判据：待建 `tools/lmgr_test.py` · 实现语言：**Loment 原生**
> 上游：`docs/167`（PE 后端）、`docs/173`（FFI 的四阶段）、`docs/210`（GC 与 arena）、`docs/245`（线程）
> 本文的"今天"一律指 `origin/main @ edda7d4e`，不是本地检出。

## 1. 为什么是"套件"，不是 42 个脚本

单个 `top` 回答"这台机器现在怎么样"。套件回答"**我的程序**现在卡在哪"。

差别在**行的归属**：`top` 的行是 PID；这套套件的行是**可归因的槽位** —— 每个槽位对应一类资源、
一个采样源、一条可以进 CI 的阈值断言。由此得到四条硬性质：

1. **可断言** —— 每个槽位都能 `--assert 'mem.phys.avail_bytes > 2GiB'`，不成立时退出码 1。
   这是它和一切"看一眼"的监控工具的分界线。
2. **可归因** —— 行按"谁在吃"组织。构建域尤其：阶段耗时、缓存命中、产物尺寸，任务管理器里根本没有。
3. **可组合** —— 单一二进制、无守护进程、稳定 JSON。谁来读都行，不要求装一整套 agent。
4. **同一契约跨平台** —— 键名、单位、退出码在 Windows / Linux 上一致；实现可以分后端。

一个套件 = 共享一份 CLI 解析、一条采样循环、一套渲染、一套降级规则。42 个独立脚本做不到第 4 条。

## 2. 十个域 × 42 个槽位

四个给定的域共 18 个槽位；后设的六个域各 4 个，合计 24 个。总计 **42**。

槽位名 `<domain>.<slot>`，在 CLI 上就是两个位置参数：`lmgr cpu load`。

### 2.1 CPU（3）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `cpu.freq` | 每核当前/最大频率，为什么降频 | PDH `\Processor Information(*)\% Processor Performance` × 标称频率；`CallNtPowerInformation` | 否 |
| `cpu.load` | 每核 user/kernel/idle、运行队列 | `GetSystemTimes`；`NtQuerySystemInformation(SystemProcessorPerformanceInformation)` | 否 |
| `cpu.sched` | 上下文切换、DPC/中断占比、每进程 on-CPU 时间 | PDH `\System\Context Switches/sec` + `GetProcessTimes` | 否 |

### 2.2 GPU（5）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `gpu.util` | 每个引擎（3D/compute/copy/video）的利用率 | PDH `\GPU Engine(*)\Utilization Percentage` | 否 |
| `gpu.mem` | 专用/共享显存占用、适配器内存 | PDH `\GPU Adapter Memory(*)\Dedicated Usage` | 否 |
| `gpu.clock` | 核心/显存频率、电压、功耗上限、降频原因 | NVML（`nvml.dll`）：`nvmlDeviceGetClockInfo` 等 | 否 |
| `gpu.therm` | 温度、风扇、热点结温 | NVML（`nvmlDeviceGetTemperature*`） | 否 |
| `gpu.proc` | 每进程显存占用 | PDH `\GPU Process Memory(*)\Dedicated Usage` | 否 |

**设计取舍**：厂商中立的那三条（util/mem/proc）只走 PDH，不碰 DXGI/WMI —— 后者是 COM，
需要间接调用（见 7.1 的 F1）。NVML 只用于 PDH 给不出的频率/温度，且必须**可选**：
没有 NVIDIA 驱动时整条槽位 `unavailable`，不是降级成 0。

### 2.3 MEM（4）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `mem.phys` | 总量/可用/提交/交换 | `GlobalMemoryStatusEx` | 否 |
| `mem.proc` | 每进程 working set / 私有字节 / 峰值 | `EnumProcesses` + `OpenProcess` + `GetProcessMemoryInfo` | 否 |
| `mem.page` | 缺页、系统缓存、换页压力 | `GetPerformanceInfo`；`GetProcessMemoryInfo.PageFaultCount` | 否 |
| `mem.leak` | 采样差分下的堆增长排行 | 纯 Loment（输入是两次 `mem.proc` 快照） | 半：逻辑可写，输入不可得 |

### 2.4 STORAGE（6）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `storage.space` | 卷容量、可用、文件系统、配额 | `GetLogicalDriveStringsA` + `GetDiskFreeSpaceExA` + `GetVolumeInformationA` | 否 |
| `storage.io` | 每设备吞吐/IOPS/队列深度/延迟分位 | PDH `\PhysicalDisk(*)\*`；`IOCTL_DISK_PERFORMANCE` | 否 |
| `storage.proc` | 每进程读写字节与 IOPS | `GetProcessIoCounters` | 否 |
| `storage.cache` | page cache、dirty、writeback 压力 | PDH `\Cache\*`；`GetSystemFileCacheSize` | 否 |
| `storage.smart` | 健康、温度、SSD 磨损、坏块 | `DeviceIoControl(IOCTL_STORAGE_QUERY_PROPERTY / IOCTL_SMART_RECEIVE_DRIVE_DATA)` | 否 |
| `storage.watch` | 目录/文件增长速率与变更事件 | `ReadDirectoryChangesW` | 否 |

### 2.5 NET（新，4）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `net.link` | 接口速率、错误/丢包、MTU、链路状态 | `GetIfTable2`（iphlpapi） | 否 |
| `net.sock` | 连接状态分布、端口占用、TIME_WAIT | `GetExtendedTcpTable` / `GetExtendedUdpTable` | 否 |
| `net.bw` | 每接口吞吐（每进程需 ETW，v1 不做） | PDH `\Network Interface(*)\Bytes Total/sec` | 否 |
| `net.lat` | 回环/网关/DNS 延迟探针 | TCP connect 计时（socket 已有）；ICMP 需 `IcmpSendEcho` | 半：TCP 路径可写 |

### 2.6 PROC（新，4）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `proc.tree` | 进程树 + 子树资源聚合 | `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)` | 否 |
| `proc.fd` | 句柄数、句柄泄漏排行 | `GetProcessHandleCount`；`NtQuerySystemInformation(SystemHandleInformation)` | 否 |
| `proc.thread` | 线程数、每线程 CPU 时间 | `TH32CS_SNAPTHREAD` + `GetThreadTimes` | 否 |
| `proc.sys` | 采样栈、热点函数 | ETW / 采样剖析器 | 否（v1 只登记，不实现） |

### 2.7 POWER（新，4）

| 槽位 | 回答的问题 | Windows 采样源 | 今天可写 |
|---|---|---|---|
| `power.batt` | 电量、放电速率、剩余时间 | `GetSystemPowerStatus` | 否 |
| `power.rapl` | CPU/GPU/DRAM 功耗域 | PDH `\Energy Meter(*)\Power`（Win11）；MSR 需驱动 | 否 |
| `power.therm` | 温度传感器、风扇、降频阈值 | PDH `\Thermal Zone Information(*)\Temperature`（开尔文） | 否 |
| `power.budget` | 能耗预算与超支告警 | 纯 Loment（输入是 `power.rapl` 的时间序列） | 半 |

### 2.8 BUILD（新，4）

| 槽位 | 回答的问题 | 数据源 | 今天可写 |
|---|---|---|---|
| `build.cache` | 编译缓存命中率与失效原因 | `loment build` 的缓存（需要编译器侧统计） | 否 |
| `build.time` | 阶段耗时分解（parse/check/emit/link） | 编译器 `--timing` 输出 | 否 |
| `build.dep` | `use` 图、循环、未使用依赖 | 纯 Loment：读源文件 + `lompi.lock` | **是** |
| `build.artifact` | 产物尺寸、增量变化、符号数量 | 纯 Loment：文件系统 + ELF/PE 头解析 | **是** |

### 2.9 ENV（新，4）

| 槽位 | 回答的问题 | 数据源 | 今天可写 |
|---|---|---|---|
| `env.tool` | 工具链版本、PATH 影子（谁先命中） | `GetEnvironmentVariableA` + `SearchPathA` + 文件系统 | 半：PATH 拿不到 |
| `env.port` | 端口占用与归属进程 | `GetExtendedTcpTable` | 否 |
| `env.svc` | 本地服务健康（dev server / DB） | TCP connect（socket 已有） | **是** |
| `env.watch` | 工作区变更、热重载、脏状态 | `ReadDirectoryChangesW` | 否 |

### 2.10 EFFECT（新，4）

**效应管理器隶属 LMGR**：语义与站点定义在 `docs/341`（LSPE 的 E11），这里只回答
“当前工程的效应面长什么样、哪些问号还没答”。它是**开发流程**的一部分 —— 与
`build.*` 同一层，不碰运行期资源。

| 槽位 | 回答的问题 | 数据源 | 今天可写 |
|---|---|---|---|
| `effect.list` | 声明了哪些效应、每个效应有哪些操作 | 读源文件 + `effect` / `fn` 扫描（A2a 语法） | 否（A2c） |
| `effect.row` | 每个函数的 `effects` 行、未被处理器销掉的 `perform` | 源文件 + 调用图（E11 的 `EffectAllowed`） | 否（A2c） |
| `effect.handler` | 处理器臂、穷尽性、`resume` 的 0/1 | 源文件（E11 的 `HandlerExhaustive` / `ResumeLinear`） | 否（A2c） |
| `effect.audit` | 站点 / 问题 Q018–Q022 / 地板 | Potato v16 台账（A2c）或 `loment lspe effects --json` 的导入件 | 否（A2c） |

**边界（明写）**：运行期处理器要到 `docs/341` 的 A3；在那之前 LMGR 只做**静态**面。
四个槽位都只要求 `fs.read` 能力，`done=0` 让 `lmgr list` 如实报 `unavailable`
（§3.5）—— 登记不等于实现。

## 3. 统一契约

### 3.1 命令行

```
lmgr list [--json]                     # 42 个槽位的注册表，含平台可用性
lmgr doctor [--json]                   # 逐能力探测，报告 ok/degraded/unavailable
lmgr <domain> <slot> [options]
```

选项（所有槽位一致）：

| 选项 | 含义 |
|---|---|
| `--once` | 采一次就退出（默认） |
| `--watch MS` | 每 MS 毫秒采一次，直到 Ctrl-C |
| `--json` | 输出 JSON 封套 |
| `--format table\|tsv\|ndjson` | 人类可读 / 管道 / 流式 |
| `--top N` | 按 `--sort` 只输出前 N 行 |
| `--sort KEY` | 按字段排序；`-KEY` 降序 |
| `--filter K=V` | 等值过滤（v1 不做表达式） |
| `--rows N` | 覆盖槽位默认的 `row_cap` |
| `--assert EXPR` | 断言；不成立退出 1 |
| `--exit-on-breach` | watch 模式下越过阈值立即退出 |
| `--no-header` | 表头关闭 |

入口有三个（详见 §8）：`loment lmgr`（选择页）、`loment lmgr_<slot>`（直达一个槽位）、
以及子系统名 `lmgr`。前两个都要求入口单元写了 `choose lmgr_true`。

### 3.2 JSON 封套（对外契约）

```json
{
  "schema": "lmgr/v1",
  "manager": "cpu.load",
  "ts_unix_ms": 1793740800123,
  "ts_mono_ns": 81234567890,
  "platform": {"os": "windows", "arch": "x86_64", "backend": "win32"},
  "degraded": false,
  "missing": [],
  "rows": [
    {"key": "cpu0", "fields": {"user_pct": 12.5, "kernel_pct": 4.1, "idle_pct": 83.4, "queue": 0}}
  ],
  "meta": {"interval_ms": 1000, "samples": 1}
}
```

规则（改它们等于改对外契约，按 `CLAUDE.md` 的 L0 纪律办）：

1. **只增不删**：`schema` 不变时，字段只允许新增；删或改名 = `lmgr/v2`。
2. **单位进键名**：`_pct` 百分比（0–100 的浮点）、`_bytes` 字节、`_hz` 赫兹、`_ms` 毫秒、
   `_ns` 纳秒、`_c` 摄氏度。不在 `meta` 里放单位表。
3. **数值就是数值**：不把数字塞进字符串，不用 `"1.2 GB"`。
4. **`rows[].key` 稳定**：`cpu0`、`pid:4242`、`\\.\PhysicalDrive0`。同一台机器两次采样同一个对象，
   `key` 必须相同 —— 这是 `--watch` 能做差分的前提。
5. **顺序由 `--sort` 决定**，默认是槽位定义的稳定序。
6. **`degraded`/`missing` 必须成对出现**：降级时点名缺什么，绝不静默补 0。

### 3.3 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常 |
| 1 | `--assert` 不成立 |
| 2 | 槽位在当前平台不可用（能力缺失） |
| 3 | 输出了数据，但 `degraded` |
| 64 | 用法错误 |

### 3.4 采样循环与内存预算

运行期 arena 是**写死的 64 KiB**（`tools/lomentc.py:453`：
`@__loment_heap = internal global [65536 x i8]`），`gc_auto` 在活集超过约 32 KiB 时也会耗尽
（`docs/210` §2.4）。所以这套套件的内存模型是硬约束，不是优化项：

- 每个槽位在注册表里声明 `row_cap` 与 `row_bytes`；
- 采样循环**零 `alloc`**：行存在调用方给的固定缓冲里（`std/arena.lomt` 的用法就是样板）；
- 总预算 ≤ 48 KiB，留 16 KiB 给渲染与库；
- `--watch` 只覆盖同一块缓冲，不追加历史（历史是使用者的重定向，不是套件的职责）；
- 超预算的槽位（如 `proc.tree` 在 500+ 进程的机器上）必须**截断并报告** `truncated: true`，
  不是崩，也不是悄悄少几行。

### 3.5 降级规则

每个槽位声明一组 `requires`（能力 id，见 §4）。启动时探测一次：

- 全部具备 → `degraded: false`；
- 部分具备 → `degraded: true` + `missing: ["pdh.gpu_engine"]`，退出 3；
- 核心能力缺失 → 退出 2，**不输出伪造的行**。

这条规则是照着 PE 垫片的历史教训写的：早期未实现的 syscall 返回 `-1`，
被调用方当成"权限不够"（`docs/167` §5）。**"不知道"必须长得像"不知道"**。

## 4. 能力面矩阵（origin/main 今天）

| 能力 | 今天的 Windows 状态 | 依据 |
|---|---|---|
| 文件读写 / `openat` / `getdents64` / `newfstatat` | ✅ | `tools/lomelf.py:2003` 起的 29 个号 |
| socket / connect / select / poll / epoll | ✅ | 同上（`ws2_32.dll`） |
| `Sleep` | ⚠️ 垫片内部用，**语言不可达**（没有 `nanosleep` 号） | `PE_IMPORTS` 有 `Sleep`，派发表没有 |
| `clock_gettime` / `nanosleep` | ❌ `-ENOSYS` | `os/time.lomt:35` 用 228，PE 派发表里没有 |
| 控制台尺寸 / raw 模式 | ❌ | `os/tty.lomt:19`："PE 上没有 ioctl" |
| 线程 `thread_spawn` | ❌ 明确拒绝 | `tools/lomelf.py:5296` |
| 子进程 `proc_sh` | ❌ 一律返回 -1 | `loment/lib/proc.lomt:18-19` |
| `extern fn` + `--link` | ❌ PE 侧未开始 | `docs/173` §4 表第 121 行 |
| 任意 Win32 DLL | ❌ 导入表是**硬编码的 kernel32 + ws2_32 函数表**，Loment 代码够不着 | `docs/173:24-26`；`tools/lomelf.py:2036` |
| 32/64 位访存 | ❌ 只有 `load8`/`store8` | `loment/lib/proc.lomt:22` |
| 环境变量 | ❌ 无 `GetEnvironmentVariable` | — |
| PDH 计数器 | ❌ | — |
| `DeviceIoControl` | ❌ | — |

**结论**：42 个槽位里，今天能完整写出来的只有 4 个
（`build.dep`、`build.artifact`、`env.svc`、以及 `net.lat` 的 TCP 路径）。
其余 38 个全部卡在 §7 的能力缺口上。这就是本设计要同时交付一份 issue 清单的原因。

## 5. 实现分层

| 层 | 内容 | 今天的可写性 |
|---|---|---|
| **L1** | 参数解析、槽位注册表、表格/JSON 渲染、退出码 | ✅ 可写（`print` + 文件系统 + arena） |
| **L2** | 采样循环：单调时钟、间隔、watch、差分 | ❌ 缺 `clock_gettime` / `nanosleep` |
| **L3** | 探针：每个槽位的 Win32/WinSock 调用 | ❌ 缺 PE FFI 与 PDH |
| **L4** | `--assert` 表达式、CI 集成、阈值 | ✅ 可写（纯逻辑） |

分层的目的：**L1 和 L4 今天就能落地并被判据覆盖**，不必等 L3。`lmgr list` 与 `lmgr doctor`
在能力全缺的机器上照样应该输出一张真话表，而不是崩。

## 6. 分期

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| **M0** | 第八维 `lmgr` 登记 + 门禁 / `loment lmgr` 选择页 / `lmgr list` / `lmgr doctor` / JSON 封套 / `build.dep` / `build.artifact` / `env.svc` | 无（L1 + L4） |
| **M1** | 采样循环 + `cpu.load` / `mem.phys` / `mem.proc` / `proc.tree` | 时钟、PDH、进程与内存 API |
| **M2** | `storage.*` / `net.*` / `power.*` 的只读槽位 | PDH + iphlpapi + DeviceIoControl |
| **M3** | `gpu.*` | NVML + PDH GPU 计数器 |
| **M4** | `--assert` 进 CI、`--exit-on-breach`、阈值基线 | M1–M3 |

## 7. 缺口 → issue 图谱

每条缺口单独一条 issue（英文，按仓库惯例）。这里的编号是**设计编号**，与 GitHub 号无关；
GitHub 号在落地后回填到本节末尾的表里。

### 7.1 PE 能力面（阻塞一切探针）

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| A1 | PE 导入表是硬编码的 kernel32/ws2_32 函数表，应改成数据驱动、可扩展 | 所有 Win32 探针 | #1261 |
| A2 | PE 侧没有 FFI：`--link` 配 `--target pe` 被拒，也没有 import library 的等价物 | 任意外部 DLL | #1262 |
| A3 | 没有 `LoadLibrary`/`GetProcAddress`：可选 DLL（`nvml.dll`）在运行期够不着 | GPU 频率/温度 | #1263 |
| A4 | 派发表外的 syscall 号在 PE 上只到运行期才报 `-ENOSYS`；编译期就该点名 | 跨平台可移植性 | #1264 |
| A5 | Windows 失败没有原因：没有 `GetLastError` / `FormatMessage` | 所有探针的诊断 | #1265 |
| A6 | 没有 32/64 位访存：每个 Win32 结构字段要 8 次 `load8` | 所有 out-struct | #1266 |
| A7 | 没有 `memset`/`memcpy`：清零与拼装 API 结构体只能逐字节 | 所有 out-struct | #1267 |
| A8 | `alloc` 的对齐没有规定；Win32 结构体要求 8/16 字节对齐 | 所有 out-struct | #1268 |
| A9 | 运行期 arena 写死 64 KiB，没有构建开关 | 行缓冲 | #1269 |
| A10 | `gc_auto` 活集过 32 KiB 就耗尽 | 长跑 watch | #1270 |
| F1 | 没有函数指针/间接调用 → COM（DXGI/WMI）整类不可达 | GPU 枚举、WMI 温度 | #1051（同根，已在该 issue 评论） |

### 7.2 时间、线程、进程、控制台

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| B1 | PE 没有时钟：`clock_gettime`（墙钟与单调）都不在派发表里 | 所有时间戳与差分 | #1271 |
| B2 | 语言层没有 sleep（垫片里的 `Sleep` 不可达） | `--watch` | #1272 |
| B3 | `thread_spawn` 在 PE 上被拒 | 后台采样 | #1273 |
| B4 | `proc_sh` 在 Windows 上一律 -1 | `env.tool`、兜底探针 | #1274 |
| B5 | 没有环境变量读取 | `env.tool`、PATH 影子 | #1275 |
| B6 | 控制台尺寸与 VT 模式不可达 | 表格宽度、颜色 | #1276 |

### 7.3 CPU / 内存

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| C1 | 没有每核 CPU 时间（`GetSystemTimes` / `NtQuerySystemInformation(8)`） | `cpu.load`、`cpu.sched` | #1277 |
| C2 | 没有频率与拓扑（`CallNtPowerInformation`） | `cpu.freq` | #1278 |
| C3 | 没有 PDH：一张平 API 能同时解开 CPU/GPU/磁盘/网络计数器 | 9 个槽位 | #1279 |
| C4 | 没有系统内存快照（`GlobalMemoryStatusEx`） | `mem.phys` | #1280 |
| C5 | 没有每进程内存（`EnumProcesses`/`OpenProcess`/`GetProcessMemoryInfo`） | `mem.proc`、`mem.leak` | #1281 |
| C6 | 没有系统性能信息（`GetPerformanceInfo`） | `mem.page` | #1282 |

### 7.4 存储

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| D1 | 没有卷枚举与可用空间 | `storage.space` | #1283 |
| D2 | 没有每设备 IO 计数器 | `storage.io` | #1284 |
| D3 | 没有每进程 IO 计数器 | `storage.proc` | #1285 |
| D4 | 没有文件缓存/回写计数器 | `storage.cache` | #1286 |
| D5 | 没有 `DeviceIoControl` 路径 | `storage.smart` | #1287 |
| D6 | 没有目录变更监听；PE 垫片也打不开目录句柄 | `storage.watch`、`env.watch` | #1288 |

### 7.5 网络 / 进程 / 电源 / GPU

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| E1 | 没有接口表（`GetIfTable2`） | `net.link`、`net.bw` | #1289 |
| E2 | 没有 TCP/UDP 连接表（`GetExtendedTcpTable`） | `net.sock`、`env.port` | #1290 |
| E3 | 没有 ICMP（`IcmpSendEcho`） | `net.lat` 的 ICMP 路径 | #1291 |
| G1 | 没有进程快照（`CreateToolhelp32Snapshot`） | `proc.tree`、`proc.fd`、`proc.thread` | #1292 |
| G2 | 没有每线程 CPU 时间（`GetThreadTimes`） | `proc.thread` | #1293 |
| G3 | 没有电池状态（`GetSystemPowerStatus`） | `power.batt` | #1294 |
| G4 | 没有温度区（PDH `\Thermal Zone Information`） | `power.therm` | #1295 |
| G5 | 没有能耗表（`\Energy Meter` / MSR） | `power.rapl`、`power.budget` | #1296 |
| H1 | PDH 的 GPU Engine / GPU Adapter Memory 计数器 | `gpu.util`、`gpu.mem`、`gpu.proc` | #1297 |
| H2 | NVML 绑定（依赖 A3 + A6 + A8） | `gpu.clock`、`gpu.therm` | #1298 |

### 7.6 工具链自身

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| K1 | `loment build` 没有 `--timing` | `build.time` | #1299 |
| K2 | 构建缓存没有统计输出 | `build.cache` | #1300 |
| K3 | 没有采样剖析器 / ETW 方案 | `proc.sys`（v1 只登记） | #1301 |
| K4 | 拿不到自身可执行文件路径与版本 | `env.tool` | #1302 |

### 7.7 套件自己的实现项

| # | 内容 | Issue |
|---|---|---|
| M1 | `lmgr list` / `lmgr doctor` / JSON 封套 | #1303 |
| M2 | M0 的三个纯文件系统槽位（`build.dep` / `build.artifact` / `env.svc`） | #1304 |
| M3 | 采样循环契约（可注入的时钟与睡眠，先用假实现测） | #1305 |

**伞**：#1260 收录以上全部，并按本节的分组列出。

### 7.8 效应管理器（EFFECT）

效应管理器隶属 LMGR，但它的语义与站点定义在 `docs/341`；这一节只记 LMGR 侧的缺口。

| # | 缺口 | 解锁 | Issue |
|---|---|---|---|
| X1 | E11 站点进 Potato 台账（`docs/341` A2c） | `effect.audit` | #1632 |
| X2 | 纯 Loment 的源文件 token 扫描（`lexer` 进 lmgr） | `effect.list` / `effect.row` / `effect.handler` 的静态半 | #1633 |
| X3 | 报告导入格式（`loment lspe effects --lines` 的稳定子集） | `effect.audit` 的无 Python 路径 | #1634 |
| X4 | 运行期处理器栈（`docs/341` A3） | `effect.handler` 的运行期半 | #1635 |

**今天一律 `done=0`**：登记让 `lmgr list` / 选择页看见这四个槽位，状态如实报
`unavailable`；不输出伪行（§3.5）。

## 8. 入口：`choose lmgr_true` 与 `loment lmgr`

> 本节是 2026-11-04 你口述的形状；三个决定同日按推荐值**定下**（`lmgr_true` / `lmgr_false`、
> 不下沉到产物、lmgr 观察程序）。改它们要动两套实现加种子，别再当草稿改。

### 8.1 第八维：`lmgr`

| 项 | 取值 |
|---|---|
| 维名 | `lmgr` |
| 取值 | `lmgr_true` / `lmgr_false`（已定 2026-11-04） |
| 默认 | `lmgr_false` —— 默认档不改变任何现有程序 |
| 出现位置 | **入口单元一次**；库写了是 E22（与其它七维同一条规则） |
| 冲突 | 无（已定）：它不下沉到产物，不引入运行期组件，`CORE_CONFLICTS` 不加项 |

**它下沉成什么**：什么也不下沉。产物一个字节都不变，这个值只进 Potato 的 `mode` 表，
作为"这个程序同意被 lmgr 观察"的声明。

为什么不做成"产物带探针"：那要先把第一条探针做通，再定义它与 `no_std` 的冲突，
再改自举镜像与种子 —— 一次 CLI 设计会变成一次语言实现工程。等第一条探针真的能用，
再把这一维从"声明"升级成"带组件"，那时它才需要一条 `CORE_CONFLICTS`。

**加第八维的固定成本**（不是估算，是登记处）：

1. `tools/lomentc.py` 的 `CORE_DIMS`、`CORE_DIM_ORDER`、`CORE_DEFAULTS`、`CORE_DIM_ZH`；
   冲突表不加项。
2. 自举侧 `loment/selfhost/switches.lomt`、`potato.lomt`、`checker.lomt` 各一份镜像表 ——
   两边逐字节一致（`docs/158` §5）。
3. `tools/loment_choose_matrix_test.py` 的矩阵从 `CORE_DIMS` 推出来，会自动多一格；
   要确认它从全绿变成全绿。
4. LSP 补全、TextMate 语法、手册：各加一个词。
5. **种子重算**（`python tools/loment_seed.py --emit`），按 `CLAUDE.md` 单独一条提交。

`CORE_DIM_ORDER` 把它**追加在尾部**：报错文本里两个取值的先后由这张表决定，
插在中间会改动既有配对的字节输出。

### 8.2 三个入口

```
loment lmgr                  # 选择页：10 个域、42 个槽位
loment lmgr_<slot> [options] # 直达一个槽位
lmgr <domain> <slot> ...     # 子系统入口（§3.1 的完整选项）
```

- `<slot>` 是槽位名把 `.` 换成 `_`：`cpu.load` → `loment lmgr_cpu_load`，
  `mem.phys` → `loment lmgr_mem_phys`。域级形式 `loment lmgr_cpu` 列出该域全部槽位。
- **分发**：`loment` 的顶层解析器加一条前缀规则 —— `argv[1]` 以 `lmgr_` 开头就交给
  lmgr 模块，不生成 42 个 `loment-lmgr_*` 可执行文件。这条规则要排在"未知子命令"之前。
- **选择页**：纯文本编号菜单，按 10 个域分组，每行带 `available` / `degraded` /
  `unavailable`。**不做 TUI**：`lumtui` 在 PE 上拿不到终端尺寸与 raw 模式（#1276），
  v1 只认数字键与 `q`，不依赖任何 ioctl。

### 8.3 门禁：没有 `lmgr_true` 就拒绝

三个入口都先确定**入口单元**，再读它的 `choose` 行：

- 入口单元的选法与 `loment build` 一致；显式 `--entry FILE` 可覆盖。
- 没有 `lmgr_true` → 退出 2，stderr 一行命名诊断：
  `lmgr: this program does not opt in; add "choose lmgr_true" to its entry unit`
- 判定**用编译器自己的解析器**，不用正则扫源。理由与 §10 第 5 条相同：门禁不能和语言
  对 `choose` 的理解分叉。落地时 `lmgr` 侧实现一份，判据拿 `lomentc` 的解析结果对拍。
- 门禁只读：不改源文件、不生成配置、不写缓存。

**已定**：方向是"lmgr 观察程序"，不是"程序拉起 lmgr"。后者要在产物里 exec/spawn，
而 Windows 上 PE 没有子进程（#1274 `proc_sh` 一律 -1，也没有 `exec`）——
选它等于把 Windows 先行直接堵死。

### 8.4 不引入第二份配置

维度是唯一真源。`lompi` 的 manifest、`loment.conf`、任何 `.lmgr` 文件都**不重复记**
这件事。否则会出现"源码头说没开、配置文件说开了"的分叉，而门禁必须先选一个信。

### 8.5 分期

| 里程碑 | 内容 |
|---|---|
| **M0** | 第八维登记（两套实现 + 种子）+ 门禁 + `loment lmgr` 选择页（空壳，列 42 槽与可用性）+ `loment lmgr_<slot>` 直达（不可用槽位退出 2） |
| **M1** | 选择页接上采样循环；`lmgr_<slot>` 走真实探针 |

## 9. 不做什么

- **不做守护进程、时序数据库、Web UI**。套件的输出是字节流，存储与展示是别人的事。
- **不做跨机聚合**。一台机器一套输出；要聚合就把 ndjson 喂给外部。
- **v1 不做 ETW 采样剖析**（`proc.sys`）。它需要另一套基础设施，单独设计。
- **不引入外部 exe 依赖**（`nvidia-smi`、`wmic`、`powershell`）。探针必须走 API；
  拿不到就 `unavailable`。这条在 Windows 上今天是被迫的（`proc_sh` 返回 -1），
  但不能把它变成设计。
- **不改 `lom/*.lom`**。这套套件是消费者，不是契约的改动者。

## 10. 判据怎么建

1. **形状判据**（`tools/lmgr_test.py`）：每个槽位一条 —— JSON 封套的字段齐全、
   `rows[].key` 唯一、单位后缀合法、退出码符合 §3.3。
2. **降级判据**：在没有对应能力的平台上，槽位必须退出 2 并给出 `missing`，
   **而不是输出一行 0**。夹具用假的能力表。
3. **差分判据**：`--watch` 两帧之间的差，喂固定输入（预录的计数器序列），比对期望值。
4. **不变量**：`--top N` 只影响行数，不影响 `meta`；`--json` 与 `--format table` 的数值逐位相同。
5. **能力面对拍**：`lmgr doctor --json` 的每一条，与独立探针（Windows 侧用一次性 C/Python）
   比对。这条是防止 doctor 自己说谎的唯一办法。
