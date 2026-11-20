# 310 · Loment Studio —— 源码参数台

> 代码：`editors/loment-studio/`（Rust workspace + 无构建步骤的前端）
> 判据：`tools/loment_studio_test.py`（登记在 `tools/ci.py:STATIC_CHECKS`、
> `tools/loment_release.py:GLOBS`、`loment/tools/lomrel.lomt`）
> 上游：`docs/175`（GC 三档）· `docs/182`（`choose` 开关）· `docs/298`（LomentFaster）·
> `docs/306` / `docs/307` / `docs/308`（能力域的新模型）· `docs/196`（LumtUI，视觉层的长期对照）
> 基线：`origin/main@d07d2f00`（2026-11-06 对齐）。本目录**只读权威那一份**：本地旧树不作为判断依据。

## 0. 一句话

**把一份 `.lomt` 源码里所有能调的东西摊成一张表，改一个值、看差异、再写回。**
两个入口：命令行（`loment-studio`）与本地网页；同一个 Rust 后端，同一套参数模型。

服务对象是**Loment 自身的开发**：给编译器、自举源、标准库、未来新功能调参数用。
不是给"用 Loment 写应用"的人做应用构建器。

## 1. 为什么不是节点图

第一版做的是节点式可视化编程（Blueprint 风格），被否了两次：

1. **画布上什么都没有**：编译器源码里的构造（`guard` / `match` / 方法调用 / `extern`）
   大多没有专属节点，全变成"原样文本"节点；图看起来完整，实际不可编辑。
2. **参数不可见**：真正每天要动的不是控制流，而是**数值**——表容量、偏移、GC 档位、
   `choose` 开关。这些在图里既不好找也不好改。

结论：**语言开发的界面应该是仪表盘，不是画板。** 节点图与 `lomt -> 图` 的导入
全部删除（`crates/graph` 已移除）。

## 2. 参数面：五类，且可扩展

| 类 | 来源 | 编辑方式 |
|---|---|---|
| **语言环境** | `choose std/no_std`、`choose gc_*`、`choose runtime`、`choose write grammar X` | 整行替换/插入；缺省值也列出来（标"源码未写 · 用默认值"） |
| **自定义开关** | `set choose NAME { … }` + `choose NAME` / `choose close NAME` | 改取值行；没有取值行时插在定义块之后 |
| **能力域** | `capability NAME : SPACE[LO..HI] revocable` | 四个字段分别可改（空间/下界/上界/可撤销） |
| **常量** | 深度 0 的 `const NAME: TY = N;` | 只替换 `=` 与 `;` 之间的**整数**（Loment 的 `const` 只收整数字面量） |
| **项目配置** | `loment.conf` 的单行 `pub fn key() -> ty { return v; }` | 只替换 `return` 后的值 |

**每一类都必须有说明**：

- 语言环境：内置文案（默认值、可选值、与其它维的冲突）。
- 开关 / 能力域 / 常量 / 配置：优先用**目录**（`param-catalog.json`），
  其次用**行尾注释**，再次用**上方 `//` 注释块**；都没有时说明"没有注释 + 被引用几次"。

**目录是可扩展的**（`editors/loment-studio/param-catalog.json`）：
按名字或前缀（`CG_*`、`TB_*`、`MAX_*`）给 help / 分组 / 单位 / 范围 / 警告。
**新功能加参数时先改目录**，不用改 Rust —— 这就是"未来新功能也装得下"的机制。

**新增参数**：`const` / `switch` / `capability` / `conf` 四类都能从 CLI 或网页新增，
插入点按语法确定（常量接在最后一个常量后；能力域接在最后一个能力域后；
开关与配置接在文件末尾）。

## 2.5 与 `docs/306` 的域模型对齐

语言侧已经落地了十八种域（`docs/306` §12）：五种形状、危险极性、八条时律、
语境类五条，以及 `tick;` / `quality` / `discharge`。**工具这一侧还没跟上**：能力面板
仍按 `docs/146` 的四元组
`(space, lo, hi, revocable)` 解析，所以

- 常规域 / 点域（`[lo..hi]`，`revocable`）—— 四个字段照旧可读可改；
- 无界形状（`[()..hi]` 等）与 `danger` / `epoch(N)` 等尾巴词 —— **工具不认**，
  面板不猜测、不写回。

这是有意的边界：工具按"今天能解析什么就显示什么"处理，不假装能改它读不懂的东西。
补齐的路径是先扩 `crates/params` 的 capability 解析（形状位 + law + param），
再在 `param-catalog.json` 里加对应的分组与说明。

| `docs/306` | 已实装的写法 | 工具里的表现 |
|---|---|---|
| 空间 space | `capability x : space[...]` | `space` 字段 |
| 形状 shape | `[lo..hi]` / `[()..hi]` / `[lo..()]` / `[()..()]`；`lo == hi` 即**点域** | 只认第一种；相等时说明写"点域" |
| 极性 polarity | `... danger` | 不认（尚未扩解析） |
| 时律 time law | `... epoch(N)` 等六条 | 不认（尚未扩解析） |
| `revocable` | 一个尾巴词 | 布尔字段；说明标"时律的终态" |

嵌套与域循环仍是设计稿（`docs/308`），工具不涉及。

## 3. 环境矩阵

把 `mode × gc × runtime`（可再加 `grammar`）的**所有组合**列出来，并标出编译器会拒绝的：

- `no_std` × `gc_auto` / `gc_auto_alpha`
- `gc_auto` / `gc_auto_alpha` × `no_runtime`

理由照抄 `tools/lomentc.py` 的 `CORE_CONFLICTS`，不另写一份。当前源码所在的组合高亮。
点一行"写入头部"会把这一组取值加入待应用（不直接写盘）。

这是新功能开发时最容易漏的一格：**一个开关在某个环境里合法、在另一个环境里必须拒绝**。
矩阵让"哪些环境要测"从记忆变成清单。

## 4. 修改怎么落地

1. **外科式拼接**：每处修改只替换它自己的那一小段（值 / 区间 / 一行 `choose`），
   注释、空行、缩进全部原样保留。
2. **先预览**：所有改动进入"待应用"，右侧显示 `第 N 行 - 旧 / + 新`；
   网页的“应用”与 CLI 的 `--dry-run` 是同一个 `apply()`。
3. **再校验**：写回后点/跑 `lomentc --check`；诊断按 `file:line:col` 回显。

**没有解析器重排**，所以不会把 6000 行的 `codegen.lomt` 重排成一份无法 review 的 diff。

## 5. 两个入口

```
editors/loment-studio/
  crates/params/   参数模型：解析 / 拼接 / 新增 / 目录 / 矩阵（无 IO 依赖）
  crates/cli/      loment-studio 命令：list / explain / set / add / matrix / check
  crates/server/   本地 HTTP：静态文件 + /api/{params,preview,apply,add,matrix,check,source}
  web/             index.html / app.css / app.js（无构建步骤）
  param-catalog.json
  examples/demo_params.lomt
```

- **CLI**：`loment-studio list <file>`、`explain <id>`、`set <file> <id> <value> [--dry-run]`、
  `add <file> const|switch|capability|conf …`、`matrix <file> [--axes …]`、`check <file>`。
- **网页**：`cargo run -p loment-studio-server` → `http://127.0.0.1:8710`。
  参数表 + 环境矩阵 + 待应用差异 + 说明面板。
- 浏览器**不解析 Loment**：参数表、补丁、校验全部由 Rust 后端回答。

## 6. 判据

`tools/loment_studio_test.py`（无 `cargo` / `node` 时逐条 SKIP）：

| 判据 | 量的是什么 |
|---|---|
| `test_cargo_tests_pass` | 解析 / 拼接 / 新增 / 矩阵 / 目录覆盖（Rust 单测） |
| `test_cli_lists_a_real_selfhost_file` | 真实自举源能列全，目录分组与说明合进去了 |
| `test_cli_dry_run_is_surgical` | 只换一个值、保留注释、**不写盘** |
| `test_matrix_reports_conflicts` | 12 个组合 / 6 合法；冲突带理由 |
| `test_demo_passes_lomentc_check` | 示例文件是合法 Loment |
| `test_frontend_assets_are_wired` | 前端资源在；`app.js` 过 `node --check` |

## 7. 不做的

- **不做节点图 / Blueprint**（§1）。
- **不做应用构建器**：目标是 Loment 自己的源码。
- **不重排文件**：只做外科式替换。
- **不改 `lom/`**；不改 `loment/lib/`（LumtUI 的回填是另一条线）。
- **不在没有预览的情况下写盘**。

## 8. 第一次实跑：最小能力域（点域）

目标：用参数台在一份源码里落一个**点域** —— `docs/306` §2.2 的五种形状里最简单的一种
（两个端点重合，guard 只需要一次 `x == a`）。

起点（故意不带能力域）：

```rust
module cap_point

fn main() {
    let slot: u32 = 7;
    guard one_slot(slot);
}
```

`lomentc --check` 报两条：`guard` 需要运行期、`one_slot` 未声明。两条都用工具补：

```
loment-studio add cap_point.lomt capability one_slot disk 7 7 --revocable
  + capability one_slot : disk[7..7] revocable

loment-studio set cap_point.lomt runtime runtime
  + choose runtime
```

工具里的能力域是四个字段（空间 / 下界 / 上界 / 可撤销）；因为 `lo == hi`，说明直接标
"点域：只有这一个下标能通过"，改 `hi` 就变成常规域 `[a..b]`。

**产物侧的证据**（`lomentc --print llvm`）：

```
@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }]
                 [{ ... { i64 1163935908, i64 7, i64 7, i64 1 } }]   ; disk 的哈希, lo, hi, revocable

%t3 = icmp uge i64 %t2, 7      ; 下界
%t4 = icmp ule i64 %t2, 7      ; 上界（点域时两端相同）
     call void @__loment_abort()   ; 不通过 -> trap
     ... @__loment_audit ...        ; 通过 -> 审计 +1
```

也就是说：**点域在实现上就是两次比较 + 一次 trap + 一次审计**；它和常规域共用同一段
发射逻辑，差别只在 `lo == hi`。这正是 `docs/306` 说"五种形状按有限端点个数分类"的
最小一格。

## 9. 现状（v0.2）

已实装：五类参数、目录层、四类新增、环境矩阵、CLI、网页、6 条判据；
点域示例 `examples/cap_point.lomt`（由工具生成并过 `lomentc --check`）。
未实装：多文件批量改（一次打开一个文件）、撤销/重做、把"构建/运行命令"也做成参数
（当前只做源码内的参数）。
