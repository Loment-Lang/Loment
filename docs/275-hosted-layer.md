# 275 · hosted 层：它是什么、在哪儿声明、`std` 解锁什么、`no_std` 拒绝什么

> 状态: **规范**（2026-10-20，issue #539 / #941）· 上游: `docs/143` §3.2（模式表）·
> `docs/175` §3.0（核心模式）· `docs/219` §4（世界端口表）· `docs/222` §4（`choose hosted`）
> 一句话: **hosted 层不是一句形容词，是端口表上那些通向世界的名字** —— `mode` 决定这份
> 产物**允不允许**有它们，`port` 决定链接器**放不放**它们进来。

## 1. 定义

`mode` 的两档（`CORE_DIMS["mode"]`）说的是**整个程序**的运行模式（`docs/143` §3.2）：

| 模式 | 核（freestanding） | hosted 层 | 判据 |
|---|---|---|---|
| `std`（默认） | ✅ | ✅ 允许 | `test_std_hosted_image_carries_the_hosted_layer` |
| `no_std` | ✅ | ⛔ 拒绝 | `test_no_std_image_carries_no_hosted_layer` |

**核** = `alloc` / `free` / `str_*` / `syscall4` 与 `loment/lib/` 里不依赖宿主的那几份。
**hosted 层** = 下面五类名字，来源只有两个：

1. **`lom/*.lom` 的 L0 原语**（仓库内契约，`docs/229`）—— 它们是本项目**自己的**接口，
   不是"宿主的东西"；
2. **外部符号**：`extern fn` 写下的名字，由链接器从真 `.so` / `.a` 的导出表里解析。
   这条路的代价与边界在 `docs/219`（世界端口表）与 `docs/183`（V1/V2）里。

按 `docs/143` §3.2 的表，hosted 层覆盖的是 **libc / 文件系统 / 线程 / 时间 / 网络** 这五面。
落到**名字**上，`docs/219` §4 已经把它们分好类了：

| 类别 | 名字（举例） | 谁满足 |
|---|---|---|
| **可闭合** | `memcpy` `memset` `memcmp` `malloc` `free` | 产物自带（收编面 / Loment 的堆）——**证据是地址同一**，不是"能跑" |
| **世界** | `write` `read` `time` `getrandom` `getenv` | 宿主的库（`libc.so.6` 等）；**端口表上留着**的就是世界面 |

所以"命名这一层"不需要第二张白名单：**链接结束后仍未闭合的符号集合**（`docs/219` §4 的
端口表）就是这一层，逐条带类别。编译器不抄一份名单，链接器本来就在算它。

## 2. 两个方向都要有牙

**`std` 解锁它**，但**不是无条件**：`port` 这一维（`sealed` / `hosted`）是链接器的门
（`docs/222` §4）。今天的默认是 `sealed` —— 默认档不改变任何既有程序的行为；要链真库就写
`choose hosted`。于是完整的句子是：

* `mode: std` + `port: hosted` + `extern fn abs` ⇒ 编得过、链得上、跑得起来
  （判据 `test_std_hosted_image_carries_the_hosted_layer`：真 `libc.so.6`、导入表里有
  `abs`、产物跑出 7）；
* `mode: std` + `port: sealed` ⇒ 仍然编得过，但**不允许**有外部端口（端口门在链接期拒）。

**`no_std` 拒绝它**，用的是 `docs/143` 那条**需求链**：`no_std` = 只用核 ⇒ 核里没有宿主 ⇒
`hosted` 要的是"往下链宿主上的库" ⇒ **两者定义上矛盾**。这条拒绝在**两个前端**都发生：

* 参考实现：`CORE_CONFLICTS[("no_std", "hosted")]` 的原文（点名两个取值与理由）；
* 自举驱动：同一个冲突（E22），锚点落在**后写的那一行**；
* 判据：`test_no_std_with_hosted_is_refused_by_both_front_ends`。

链接器还有第二道（防伪造）：一份 IR **自称** `no_std` 却带着 `; loment-port: hosted` 标记，
两份链接器都点名拒（`test_no_std_is_a_claim_the_linker_checks`、`loment_elf_test` 里
同名的镜像判据）—— 编译器是那句话的作者，能拿一份不是自己生成的 IR 去核它的只有链接器。

## 3. 产物上怎么核

| 方向 | 产物上看得见的东西 |
|---|---|
| `no_std` | 静态镜像：**没有** `.dynsym` / `.interp` / `.dynamic`，链接器报零导入 |
| `std` + `hosted` | 动态镜像：`.interp` + `.dynsym`，`DT_NEEDED libc.so.6`，导入表里有 `abs` |

这条判据不用 `readelf`：`lomelf.compile_ll` 返回的 `shared` / `imports` 就是端口表的
链接期视图（`docs/219` 的同一份数据），而段名直接在这份 ELF 的字节里。

## 4. 边界（如实列）

* `no_std` **不是"什么都没有"**：核、LMS、LGC 都在；它拒绝的是**宿主**那一层。
* `std` **不是"所有 libc 名字随便写"**：没人在链上提供、也不在收编面里的名字，链接期仍然
  点名拒（`test_a_release_build_links_the_real_libc_end_to_end` 的第二半）。
* 多语法前端与 `mode` 的关系（Python/Java 前端在 `no_std` 下应当拒绝）仍是
  `docs/143` §3.2 记录的**待办**，不在本文的验收面里。
