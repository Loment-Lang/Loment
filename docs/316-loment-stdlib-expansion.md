# 316 · 外挂标准库补全：8 份新模块 + 3 处老库对齐（2026-11-08）

> 状态：**已落地**（`lompi/store/std/0.1.0/` 与 `lompi/store/host/0.1.0/`）
> 判据：`loment_std_test`（+1，新增 29 项与 Python 逐字节对拍）· `loment_libl_test`（225 项与 glibc）
> · `loment_store_twin_test`（全 store 逐模块双实现对拍）· `lomentc_test::test_std_package_…`
> 上游：`docs/180`（std 的三条约定）· `docs/199`（包内模块按名可达）· `docs/232`（LibL）
> 议题：`#1353`（正则）· `#1369`–`#1372`（zip/tar/gzip 容器）· `#1375`（UUID）·
> `#1402`（SHA-3/BLAKE2）· `#1374`（struct.pack/unpack）· `#381`（软浮点非法 IR，已关）

## 0. 一句话

**老库先量、新库再补、每个新库都要与一个独立实现逐字节对拍。** 量的结果是：156 个模块里
154 个两实现逐字节一致、2 个两边都拒（`host` 与 `libl_host`）；新补 8 份，store 从
133 个 `std` 文件长到 141（整棵树 160 → 168）。

## 1. 老库对 main 的更新状态（先量）

| 现象 | 根因 | 处置 |
|---|---|---|
| `libl_host.lomt::getchar` 过不了 `check`（E029：`free` 之后又用 `one`） | 2026-10-15 落地的 LMS 第 1 级检查器是**流不敏感**的：`if got == 0` 分支里 `free(one)` 之后，另一条路径上还在读 `one` | **修了**：先读、再 `free`，两条路径共用同一次释放 |
| `loment check lompi/store/host/0.1.0/libl_host.lomt` 报"rust 后端不支持 `free`" | `--check` 的默认后端是 Rust 翻译；Rust 运行期只有 bump 分配器 | **不在本笔修**（已有 **#557**）；`lomentc.check()` 与孪生判据都是绿的 |
| `module struct` 参考实现收、自举侧拒（E31 + E2 一串） | 自举侧丢了单元的 `choose runtime` 与顶层名字 | **换名**为 `pack`；分歧已开 **#1512** |
| `stdio` 里"`%f/%e/%g` 不能做" | 上游 `#381`（`wide`/`bigint`/`f64` 27 处混宽非法 IR）已于 2026-10-17 关闭 | **改成能做**，见 §3 |
| `f64fmt` 的 `%e` 指数只打一位（`e+0`） | C 的 `%e` 至少两位（`e+00`） | **修了**；自检的期望值同步 |

逐模块审计脚本（不进树）跑完 164 个模块：**0 失败**。

## 2. 新增的 8 份（全部 `choose runtime`，无 syscall）

| 模块 | 内容 | 对照物 |
|---|---|---|
| `pack` | 二进制打包/解包：`>` `<` `!` `=` + `b B h H i I q Q f d x` + 数字重复 | Python `struct` |
| `uuid` | v4 / v7、字节<->36 字符文本、版本/变体/比较；随机字节由调用方给 | Python `uuid` + 手算向量 |
| `sha3` | SHA3-224/256/384/512（Keccak-f[1600]，24 轮） | `hashlib.sha3_*` |
| `blake2` | BLAKE2b-512/256 与 BLAKE2s-256，含**密钥模式**（MAC） | `hashlib.blake2b/blake2s` |
| `gzip` | RFC 1952 容器：头（含 FEXTRA/FNAME/FCOMMENT/FHCRC）+ 裸 DEFLATE + CRC-32/ISIZE | Python `gzip` 双向 |
| `tar` | ustar 头：`name`/`prefix` 两段式、八进制字段、**把校验和字段当空格**的校验 | Python `tarfile` 逐字节 |
| `jsonw` | JSON 写出状态机（逗号/冒号/转义/嵌套），与 `jsn` 的只读游标互补 | `json.dumps(separators=(...))` |
| `regex` | 回溯引擎：`.` `^` `$` `[...]` `(...)` `\|` `* + ? {m,n}` `\d \w \s` 及补集 | Python `re`（19 项） |

**共同约定**（`docs/180` §3）：调用方给缓冲、(ptr, 长度) 输入、"找不到"用 `i64` -1 或显式
哨兵；每份都带 `test_<模块>_*` 自检。

### 2.1 每份都有一条"不做的"清单

- `pack`：不做 `s/p/c`、不做本机对齐/指针大小（**这不是 C ABI**）；
- `uuid`：不做 v1/v2（要 MAC 与时钟序列）、v3/v5（要命名空间哈希）；
- `sha3`：不做 SHAKE / KMAC（可扩展输出的 API 形状不同）；
- `blake2`：不做树哈希/并行模式（只实现顺序模式 fanout=1, depth=1）；
- `gzip`：不做多成员 concat（把循环留给调用方）、不解释可选字段；
- `tar`：不做 GNU 长名/pax 扩展头、不递归目录；
- `jsonw`：不建 DOM、不美化、不验证 UTF-8；
- `regex`：**不捕获**、不做非贪婪/后行断言/反向引用、按**字节**匹配。

## 3. LibL 的浮点补齐（`docs/232` §5.1 的两格）

1. **`stdio` 的 `%f` / `%e` / `%g` 落地**：`f64fmt_fixed` / `f64fmt_sci` 接线；`%g` 按
   "科学计数法取 p 位 -> 读指数 -> 定点或科学 -> 去尾零"实现。精度上限 512（临时区 1 KiB），
   如实写在头注。新增 `snprintf_ws`：循环里复用工作区；`snprintf` 只在格式串含 `f/e/g`
   时才开工作区（整数/字符串路径**零分配**）。
2. **`f64fmt_parse` / `f64fmt_parse_n`（文本 -> double）落地**：含 `inf/infinity/nan`、
   前后空白、`strtod` 语义的 `endptr`。**近似正确舍入**（尾数最多 19 位，超出丢位），
   实测常见值与 Python 逐位一致；`1.7976931348623157e308` 差 3 ulp —— 正确舍入要 bigint
   除法，单独一件。
3. **`stdlib` 的 `strtod` / `atof` / `strtof` 落地**：libc 的指针形状，内部调
   `f64fmt_parse_n`。

## 4. 判据

- `loment_std_test::test_new_stdlib_modules_match_python`（+1）：八份库 + `strtod` +
  `printf("%.2f")` 共 **29 项**与 Python 逐字节一致。**验过会红**：把 `sha3` 的轮常量
  写错一位，第一条摘要就对不上。
- `loment_libl_test`：225 项仍与 glibc 一致（`stdio` 加浮点后重跑）。
- `loment_store_twin_test`：全 store 逐模块双实现 IR 逐字节。（新增模块没有登记缺口。）
- `lomentc_test::test_std_package_is_one_unit_without_name_collisions`：140 个模块的
  门面零平名字冲突（`uuid` 的内部 `u_*` 与 `url` 撞了，改成 `uu_*`）。

## 5. 计数与登记处

| 位置 | 旧 -> 新 |
|---|---|
| `loment_lompi_test.STORE_MODULES["std"]` | 133 -> 141 |
| `loment_lompi_test.STORE_FILES` | 160 -> 168 |
| `.github/gate-floors.txt: loment_std_test` | 4 -> 5 |
| `std.lomt` 门面 | 127 -> 140 个模块 / 140 条 `use` |

`loment_release.GLOBS` 与 `lomrel.lomt` 的 glob 是**模式**（`std/0.1.0/*.lomt`），新模块自动
进清单；随包文件数钉在 `STORE_FILES`。

## 6. 不主张

- **不是正确舍入的 `strtod`**：见 §3.2；
- **不是 PCRE**：`regex` 是 ERE 子集，且不捕获；
- **`%g` 的边界舍入**与 glibc 可能差一位（头注写明，判据覆盖常见值）；
- 没有动 `loment/lib/` 的同名核心模块（`mem`/`num`/`sha256`），也没有动 L0（`lom/*.lom`）。
