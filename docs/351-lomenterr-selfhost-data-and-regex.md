# 351 · 报错器的真源搬进自举侧 + 完整正则引擎（2026-11-18）

> 用户 2026-11-18 的指示（原话）：**「将 lomenterr 正式在自举侧实现 lomenterr.lomt，
> 在此之前 lomenterr 在参考侧实现并自举侧调用」**；随后两次追问把范围钉死：
> **「2，且自举编译器文本分类现在不做未来也要做，不如现在做完」**、
> **「`{n}`，前后瞻全部一起做了，一个完整的正则引擎」**。
> 上游：`docs/182`（报错器）、`docs/188`（`choose write grammar`）、
> `docs/158` §5（两个实现别只看一条判据）、`docs/189` §3（对拍判据的三种形态）。

## 0. 一句话

**码表、说明卡、语言卡、分类规则的真源从 `tools/loment_diag.py` 搬到
`loment/tools/errdata.lomt`（自举侧）**；分类与渲染所需的完整正则引擎也落在自举侧
（`lompi/store/std/0.1.0/regex.lomt`），`lomenterr` 走**自举链**编译，并新增
`lomenterr --classify FILE` 作为文本分类的真实入口。

## 1. 之前的形状（用户说的"参考侧实现、自举侧调用"）

| 层 | 之前 | 之后 |
|---|---|---|
| 码表 / 说明卡 / 语言卡 | `tools/loment_diag.py`（参考侧，约 5000 行 Python 数据） | `loment/tools/errdata.lomt`（自举侧，唯一真源） |
| 分类规则（`RULES` / `FINE`） | 同上（Python 正则表） | 同上（模式也在 `errdata`，算法在 `errclass`） |
| 分类**算法** | `loment_diag.classify()`（`re.search`） | `errclass.errc_code_mem()`（自举侧的完整正则引擎） |
| `surface_data.lomt` | 由 `--dump-surface` 从 Python 表**整份生成**（585 KB） | 只生成**派生**部分（3 KB）：语法别名 / 词序 / 版本 |
| `build_lomenterr` | `lomentc.emit_llvm` + `lomelf`（参考链） | `loment_dist.build_host_tool`（种子 → stage1 → IR → lomelf） |

**没有搬的**：`loment_diag.py` 仍然存在，但它的数据不再是真源 —— 它**解析**
`errdata.lomt`，保留 `diag` / `--json` / `--dump-surface` 三个入口。Python 只留
"正则 → 码号"这一层解释器（它本来就不发消息文本，见 `docs/182` §5.3）。

## 2. 正则引擎：为什么必须重写

第一版把匹配器直接写在模式字符串上（递归下降）。它在"外层回退要**进到内层量词**"时
是错的：

```
^(?:.+ )?a b        配  "x a b"
```

内层 `.+ ` 先贪心吃到 `x a `，外层发现后面的 `a b` 不匹配，想让内层**少吃一口** ——
递归下降只能交出一个终点，做不到。CPython `re` 能过，旧引擎给 no-match。
这不是边角：`loment_diag` 的 239 条规则里有两条正是这个形状（`实参期望 .+，得到…`）。

**新引擎**（`lompi/store/std/0.1.0/regex.lomt`，约 1400 行）：

1. **编译成字节码**（每条指令 4 个 u32：`op a b c`），`SPLIT` 把另一条路连同位置与
   捕获撤销点压到**显式回溯栈**上，失配就弹。嵌套量词因此天然可回退。
2. **UTF-8 按码位匹配**，公开接口仍是**字节**下标；`re_search` 的起点只落在码位边界上
   （否则 `\S{2}` 能在汉字中间切出两个"字节匹配"，CPython 不会）。
3. **支持**：`. ^ $ \A \Z \z \b \B`、字符类（范围/取反/类转义）、捕获组与
   `\1..\9` 反向引用、`(?:)`、或、`* + ? {n} {n,} {n,m} {,m}`、**惰性**（`*?` 等）、
   **占有量词**（`*+` / `{n,m}+`）、**前后瞻**（`(?=) (?!) (?<=) (?<!)`，后顾**变长也支持**）、
   `\d \D \w \W \s \S`、`\n \r \t \f \v \0 \xHH \uHHHH`、开头全局开关 `(?i) (?m) (?s)`。
4. **零宽循环守卫**：`(a?)*` 不能原地打转。`OP_LOOP` 从**回溯栈里**找本轮那条
   `SPLIT` 记录取"这一圈开始时在哪"；跟当前 pos 相同就退出循环。**没有用槽** ——
   槽会被上一轮覆盖，回退到更早一轮时就陈旧（实测 `(a|)*a` 配 `aa`）。
5. **护栏**：每条指令扣 1 步预算；程序上限 1024 条、回溯栈 1024 格、占有量词切点 16 个。
   越界一律按"没匹配上"返回 —— **不静默改语义**。`re_ctx_bytes()` ≈ 30.9 KiB。

**如实列的边界**（`regex.lomt` 头注也写着）：命名组 `(?P<name>)`、`\p{...}`、条件分支、
递归、原子组、`\G`、作用域内联开关 `(?i:...)` 不做；`(?i)` 只折 ASCII A-Z/a-z；
`\w` / `\s` 按 ASCII 口径（`\w` 不认汉字，CPython 认）。

## 3. 分类：自举侧的真实入口

- `loment/tools/errdata.lomt`：数据（`rule_count` / `rule_pat` / `rule_code` / 卡 / 语言卡）。
- `loment/tools/errclass.lomt`：算法（逐条 `re_search`，第一条命中者胜）。
- `lomenterr --classify FILE`：吃**文件**里的消息原文，认出来打码退 0，认不出什么都不打退 1。
  **为什么吃文件而不是命令行文本**：Windows 的 PE 垫片合成 argv 时按空格切，
  `--classify "is not an array or slice"` 只收到 `is`（实测，长度 2）。
- 判据 `loment_err_test::test_classify_matches_the_reference_classifier`：把编译器的
  全部诊断模板（170+ 条）逐条喂给自举侧分类器，与 `loment_diag.classify` **同码**；
  另有 4 条真实消息与 1 条认不出的消息。

**编译器的 999 兜底这一轮没接**：`loment/selfhost/driver.lomt` 里 `comefor_fail` 的九支
仍写死 `999`。把 `errdata`（646 KB）+ 引擎链进**种子**的代价与收益不成比例（种子是每次
编译都要过的）；要接的话，落点是"运行时从 `share/loment/errdata` 读表"，那是另一轮。

## 4. 落点与判据

| 件 | 路径 |
|---|---|
| 引擎 | `lompi/store/std/0.1.0/regex.lomt`（自检 `test_regex_all`） |
| 真源数据 | `loment/tools/errdata.lomt`（手写；由一次性迁移生成初稿） |
| 分类器 | `loment/tools/errclass.lomt` |
| 对拍驱动器 | `loment/tools/lomrxcheck.lomt`（JSONL → 捕获跨度） |
| 派生数据 | `loment/tools/surface_data.lomt`（生成物，3 KB） |
| 参考侧解析 | `tools/loment_diag.py`（`_errdata_load` 在 import 期解析 + `_validate`） |
| 自举链构建 | `tools/loment_dist.py::build_host_tool` + `tools/loment.py::build_lomenterr` |

判据：

- **新增** `tools/loment_regex_test.py`（3 条）：14000 条语料（特性电池 +
  239 条真实规则 × 编译器诊断模板 + 每条规则的**见证串**）与 CPython `re` 逐字节对拍；
  语料非空转（两种判决都要有）；引擎自带自检也过。**对拍走自举链编的驱动器**。
- `loment_err_test` 58/58：新增分类同码那一条；渲染、启动器、PE/ELF 孪生全部照旧。
- 容量盘点：`CAPACITY_SITES 265 → 270`、`SILENT_SITES 173 → 178`、
  `LITERAL_SITES 67 → 68`、`LITERAL_SILENT 66 → 67`；三处新容量写进 `EXPLAINED`
  （`FILE_CAP` / `PAT_CAP` / `TXT_CAP` / `FIX_STRIDE`），并给 `FIX_STRIDE` 补了一条
  `_validate`（每码 >8 条修法直接炸）。
- 门禁：`ci.py` 加 `loment_regex_test`；`gate-floors.txt` 同步（58 / 3）。

## 5. 撞到的三个坑（如实记）

1. **没有块作用域，同名 `let` 会串类型**。`_start` 里新加的 `let cap: ptr` 与既有的
   `let cap: u32` 同名，参考实现把 `cap` 当成 ptr：IR 里出现
   `icmp != ptr %cap, null`（clang 直接拒收）。改名（`zcap` 等）后过。
   `rx_c_atom` 里两处 `let d`（u32 / u64）更直接 —— 字面量分支拿到 u32 的 `d`，
   `/ 4294967296` 变成**除零**，SIGILL。这类错误只有真跑才看得见。
2. **旧匹配器进不去内层量词**（§2）。第一版对拍只测简单构造时全绿；换成真实规则
   × 真实消息才露出来。
3. **`--classify TEXT` 在 Windows 上被 argv 合成切开**（§3）。

## 6. 代价（明写）

- `build_lomenterr` 从"参考链几秒"变成"自举链 **冷构建 ~133 s**"（本机实测）。
  包里本来就是自举链，这一轮只是把仓库侧开发入口对齐；`loment_err_test` 的权重
  因此从 11 s 改成 150 s。
- 自举编译器编 `errclass`（含 646 KB 的 errdata）本机 ~71 s。包里的 `lomenterr`
  也有同样的账（之前编 585 KB 的 surface_data 时已经付过一次）。
- `lomcli` / `lsp` 只 `use errdata`（不拖正则引擎）；只有 `lomenterr` 同时拖 `errclass`。
