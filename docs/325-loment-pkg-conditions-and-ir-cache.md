# 325 — 包清单的逐维前置条件 + IR 编译缓存

`docs/323` 把运行期需求从 248 个模块上移到 `pkg.lomp`，但只移了一维。这一份把**所有
核心维**和**构造级前置条件**都搬进清单，并给参考链路加一层按内容哈希寻址的 IR 缓存。

## 1. `pkg.lomp` 的两个函数

清单仍然是一份 Loment 源码（`docs/168` §3.2），零语言改动：

```rust
pub fn name() -> str { return "world"; }
pub fn version() -> str { return "0.1.0"; }

/// 包里代码依赖的核心档（空格分隔）。逐维注入 `mod.chooses`。
pub fn choose() -> str { return "std runtime"; }

/// 包里**真代码**实际用到的构造（逗号分隔）。
pub fn requires() -> str { return "alloc,float"; }
```

`choose()` 的每一维按 `CORE_DIM_ORDER` 映射；模块**自己**写的那一维优先，清单只补
没写的那几维。注入之后走的是同一条既有规则：**依赖的需求必须被根满足**，不满足就报，
正文点名包（同一包只报一次）。装载器在自举侧做同一件事：根/`addin` 没写的那一维，
在合并单元末尾补一行 `choose <词>`。

`requires()` 是**可核**的前置条件，不是注释：词表是编译器认得的构造名 ——

| 词 | 触发它的写法 |
| --- | --- |
| `alloc` / `free` | 调用 `alloc(` / `free(` |
| `str_concat` / `str_eq` / `panic` / `print_num` | 同名调用（`print_num` 只在 Tur 2 算） |
| `guard` / `tick` / `quality` / `discharge` / `domain` / `lgc` | 语句位置的出现 |
| `thread_spawn` | 调用 |
| `syscall` | `syscall4/6/7(` |
| `extern` | `extern fn` |
| `float` | 类型/前缀 `f64` |

判据 `loment_lompi_test::test_every_package_manifest_matches_its_code` 把**代码**当唯一
真源：12 个包的 `requires()` 必须与逐模块扫描的并集**逐项相等**（排序也要一致），
`runtime` ↔ `rt_sites`、`free` ↔ `gc_manual`、`extern` ↔ `hosted` 三对也要对上。
清单漂一个字就是红 —— 这一层就是"极其详细"的落点：漏写与多写同样不允许。

### 12 个包今天的样子

| 包 | choose | requires |
| --- | --- | --- |
| baremetal | std runtime | alloc,str_eq |
| dev | std runtime | alloc |
| engineering | std runtime | alloc,float |
| host | std runtime gc_manual | alloc,free,panic,syscall |
| internet | std runtime | alloc,syscall |
| lowlevel | std runtime | alloc,str_eq |
| math | std runtime | alloc,float |
| os | std runtime | alloc,syscall |
| science | std runtime | alloc,float |
| std | std runtime | alloc,float,panic,syscall |
| system | std runtime | alloc,str_eq |
| world | std runtime | alloc,float |

（"为什么都还是 runtime"的诚实账在 `docs/323` §4：自检里的 `alloc` 是今天唯一的墙。）

## 2. IR 缓存

`tools/loment_cache.py`，挂在 `lomentc.emit_llvm` 的开头与结尾。

**键**（少一样就是静默错编）：

* 根 + **全部**依赖的源文件字节（按规范化路径排序，路径也进键 —— 两个同名不同内容的
  文件不能撞）；
* 生效的核心维（`effective_dims`）、版次；
* LSPE 档、回答、`debug` / `coverage` 开关；
* **编译器自身**：`lomentc.py`、`lomc.py`、`lspe.py`、`potato_from.py`、`lomt_from.py`、
  `loment_comefor.py` 的字节摘要。

**存储**：`LOMENT_CACHE_DIR` 或平台用户缓存目录下的 `ir/<前两位>/<key>.ll`。写盘用
临时名 + `os.replace`（与 `build_lomenterr` 同一手法，并行门禁里不会读到半截）。

**不缓存**：没有 `src` 的单元（解析自字符串/翻译单元），输入不在键的控制范围内。

**坏条目**：空文件或读失败一律当未命中并删掉、重算 —— 把半截 IR 当结果返回，是缓存
能犯的最坏的错。

**关掉**：`LOMENT_CACHE=off`；`python tools/loment_cache.py --stats|--clear` 看账与清理。

**自举侧**（`loment/selfhost/driver.lomt`）用同一套键的变体：`check` 通过之后、
codegen 之前算键 = 纪元串 + 版次 + **编译器自身**（`/proc/self/exe`）的 128 位 FNV +
合并后的单元全文；命中就把缓存文件的分块流到 stdout 并跳过 codegen。目录默认 `$HOME/.cache/loment`（没有 `HOME` 才落 `/tmp/loment-cache`），
`LOMENT_CACHE_DIR` 覆盖、`LOMENT_CACHE=off` 关闭、`--no-cache` 也关。回写走临时名 + `rename`，读到的永远是完整文件。
判据 `loment_p8_test::test_m98_selfhost_ir_cache` 用**哨兵**而不是计时：第一次跑完把
缓存文件改写成 `; CACHED`，第二次的 stdout 必须正好是它；再钉 `--no-cache`、
`LOMENT_CACHE=off` 与"改源换键"三条。

## 3. 判据

- `loment_cache_test` 4 条：命中字节相同、依赖改一字节即失效、`off` 不写、坏条目重算。
- `loment_lompi_test` +1：12 个包的清单与代码逐条对得上。
- 既有判据全绿：`lomentc_test` 229、规则等价 133/133、孪生、p8、potato、seed。
