# 320 · 领域库独立成包：std 只留核心（2026-11-08）

> 状态：**已落地**（`lompi/store/` 新增 8 个包，共 41 个 `.lomt`）
> 判据：`loment_domains_test`（3 组、57 项）· `loment_store_twin_test`（全 store）·
> `loment_release.build()`（覆盖 217 个 store 文件）
> 上游：`docs/168`（库系统）· `docs/170`（lompi 与 store）· `docs/199`（包内模块按名可达）
> · `docs/316`（std 补的 8 份）

## 0. 一句话

**`std` 是可移植核心，不是杂物间。** 领域库各自成包：一个包一个目录、一个入口、
一份 `pkg.lomp`；包内模块按名字可达（`use linalg`），门面只给"我全都要"。

## 1. 八个包

| 包 | 模块 | 领域 |
|---|---|---|
| `math` | `linalg` `poly` `calc` `prob` `complex` `geometry` `bitstream` `toposort` | 数学与计算 |
| `science` | `elements` `chem` `physics` `thermo` `optics` | 化学 / 物理 |
| `engineering` | `units` `electronics` `control` | 工程 |
| `world` | `money` `geo` | 现实/实用 |
| `dev` | `semver` `semrange` `spdx` `args` | 软件开发 |
| `lowlevel` | `elf` `pe` `abi` | 底层开发 |
| `system` | `caps` `seccomp` `ns` | 系统开发 |
| `baremetal` | `multiboot` `gdt` `paging` `pci` `vga` | 金属/裸机开发 |

每个包有 `<包>/0.1.0/<包>.lomt` 入口（`module <包>`，零函数门面）与 `pkg.lomp`
（`name()` / `version()`）。包内模块用**名字形式**引用 `std` 的依赖（`use f64`），
引用同包模块用路径形式（`use "elements.lomt"`）—— 前者走 store 的 ③b 层，后者按
导入方目录解析。

## 2. 为什么不是都塞进 std

1. **承诺不同**：`std` 的不变量是"**一个 syscall 都不发**、任何目标都能编"。领域库
   各自有额外假设（ELF 是 x86-64 小端、`baremetal` 是特权模式、`dev` 是文本工具），
   混进 `std` 会让那条不变量变成一句空话。
2. **编译代价**：`std` 门面已经是 140 个模块；再塞 33 个，`use std` 的平名字空间与
   编译时间继续涨。分包的按模块取用不受这个影响。
3. **发现与发布**：一个包是一份可单独列出的身份（内容哈希），领域库升版/替换不必
   动 `std`。

## 3. 登记处（少一处就漏发/漏跑）

| 位置 | 改什么 |
|---|---|
| `tools/loment_lompi_test.py` | `STORE_MODULES` 加 8 个包（`math` 9 … `baremetal` 6）；`STORE_FILES` 168 -> **217** |
| `tools/loment_release.py` | `GLOBS` 加 8 条 `lompi/store/<包>/0.1.0/*` |
| `loment/tools/lomrel.lomt` | **同序**加 8 条（自举侧的 glob 只认名字，没有注释可依） |
| `tools/ci.py` | 套件列表 + `TIMINGS` 加 `loment_domains_test` |
| `.github/gate-floors.txt` | `loment_domains_test 3` |
| `tools/loment_dist.py` | 说明文字里的 168 -> 217 |

`loment_lompi_test::test_release_manifest_covers_every_store_file` 钉住"清单逐条覆盖
store"；`loment_release` 与 `lomrel` 两份 glob 的顺序不一致会给"同集合不同顺序"的
两份清单，判据会红。

## 4. 判据

- `tools/loment_domains_test.py`：三组探针，**Python/已知常量**当对照物，共 57 项。
  - math + science：19 项（含 `erf` 的近似式、`lens`、`haversine` 等）；
  - engineering + world + dev：13 项；
  - lowlevel + system + baremetal：25 项（合成 ELF/PE 头、seccomp 指令、GDT/PTE 位）。
- `loment_store_twin_test`：每个模块当根单元，两实现 IR 逐字节（含新包）。
- 三组探针都**真的 clang 链接 + WSL 真跑**，所以能抓到"检查过、IR 发得出、但
  clang 拒"的那一类（下面两条就是这么抓到的）。

## 5. 顺带抓到的两个既有缺陷

1. **`mathf_exp` 的负溢出判断写反**：`if f64_lt(x, mf_i(745))` 应为 `x < -745`。
   于是 `exp(-1)` 返回 0，`pow(x, 负数)`、`pb_erf`、复数的 `exp/log/pow` 全错。
   由 `math/science` 探针的 `erf(1)` 抓到；已修并补自检。
2. **块内同名变量复用类型槽**：`money.mo_parse_ok` 里先有 `let c2: u32`（循环内），
   后有 `let c2: i64`；本语言核心**没有块作用域**（#1309），后者沿用了前者的 i32，
   alloca 发成 i32 而右值是 i64 —— clang 当场拒。改名为 `cents`。`lomentc.check`
   看不见这一类；只有真编译（探针）能看见。

## 6. 不主张 / 未做

- **包名未进 `RESERVED_PACKAGE_NAMES`**：用户的 `deps/math` 可以遮蔽随包的 `math`
  （与第三方包同一规则）。要不要把 8 个名字也列为保留名，是编译器的决定，本文不做。
- 没有跨领域包的依赖（`science` 不依赖 `math` 的 `linalg`，`engineering` 不依赖
  `science` 的 `physics`）—— 各包只向下依赖 `std`，保持可单独替换。
- 不做包级版本选择（编译器只认一个版本；多版本是 lompi materialize 的事）。
