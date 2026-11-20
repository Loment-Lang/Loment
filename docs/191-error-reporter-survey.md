# 报错消息的 16 门语言对照（2026-09-18）

> 起因：用户要「收集 Java/C/Python/Rust 等 15 个语言的报错消息，超级进化 `lomenterr`」。
> 这份文档是**收集**那一半；抄了什么、判据在哪，在文末的落点表里。

**方法上的分界要写在最前面**，因为这份文档里两种东西混着：

* **本机实测的 8 门** —— C（clang 22.1.8）、C++（clang++）、Java（javac 23）、
  Rust（rustc 1.96.0）、Go（1.26.4）、C#（dotnet 10 / Roslyn 5.9）、Python（3.11.15）、
  Perl（5.38.2）。结论后面都有**从它们的输出里抄下来的原句**；
* **引述的 8 门** —— Elm、Elixir、Haskell（GHC）、Swift、Kotlin、TypeScript、Ruby、PHP。
  这几门这台机器上没装，所以**只写我的知识，标着「引述」**。凡是引述的地方都写得保守：
  说"它以 X 著称"，不说"它第 N 行会打印 Y"。**引述不该冒充实测** —— 本仓对这件事的
  判词很硬（"静默漂"那条）。

（javac 在这台机器上默认出中文（系统 UI 是中文），实测时用
`javac -J-Duser.language=en -J-Duser.country=US` 强制英文；C# 只有 `dotnet build` 认
`DOTNET_CLI_UI_LANGUAGE=en`。）

## 1. 同一件错事，16 家怎么说

拍的是同一类错：**拼错一个名字**（`leftover_count` 声明着，写成了 `leftove_count`）。

| 语言 | 它怎么报 | 抄了吗 |
|---|---|---|
| **Rust**（实测） | `error[E0425]: cannot find value ...` + `help: a local variable with a similar name exists` + **把改好的整行印出来**，改动处用 `+` 标 | **抄了四条**（见 §3） |
| **C**（实测） | `error: use of undeclared identifier 'leftove_count'; did you mean 'leftover_count'?` + 单独一行 `note: 'leftover_count' declared here` | 抄了 note 的**意思**（我们给行号）；fix-it 的**机器形状**也抄了 |
| **C++**（实测） | 同上；**参数个数不对**那条会列候选函数：`note: candidate function not viable: requires 2 arguments, but 1 was provided` | 没抄（我们没有重载，这条前提不存在） |
| **Java**（实测） | `error: cannot find symbol` / `symbol: variable leftoveCount` / `location: class Test` —— **一个候选都不给** | 反面：只说出"缺什么种类"却不说"是不是想写这个" |
| **Go**（实测） | `.\test.go:12:14: undefined: leftove_count` —— **不给建议，连源码行与插入符都没有** | 只抄了它的**一行式**（`--short`）与**默认卡条数** |
| **C#**（实测） | `test.cs(8,17): error CS0103: The name ... does not exist` —— MSVC 风格，**人看的输出里没有插入符** | 反面：码 + 位置，但不指到 token |
| **Python**（实测） | `NameError: name 'leftove_count' is not defined. Did you mean: 'leftover_count'?` + 指 token 的 `^^^^^` | 抄了"指 token"；它**一次只报一条**（运行时栈崩了就结束）是反面 |
| **Perl**（实测） | `Global symbol "$leftove_count" requires explicit package name (did you forget to declare "my $leftove_count"?) at test.pl line 10.` —— 建议**写在句子里**，无列号无插入符 | —— |
| **Elm**（引述） | 以"用**你的**代码说话的对话"著称：说出问题、给出两段代码对照、再问一句「你是想……吗」 | 抄了**"给改好的那一行"**这个精神（我们的形态是 Rust 那种） |
| **Elixir**（引述） | 以报错体验最好著称：贴出出错的代码片段、标出问题处、常给候选 | 同上（我们的 `help:` 块） |
| **Haskell / GHC**（引述） | `Found:` / `Expected:` 两块对照；**`Possible fix: add an instance declaration ...`**；`-fdiagnostics-show-caret` 给代码框 | 抄了"可能修法"要有**标签**这件事（我们的 `how to fix:` 就是那个位置） |
| **Swift**（引述） | 以 **fix-it**（`replace 'x' with 'y'`）著称，而且是**机器可应用**的 | 抄了机器可应用这件事（进 `--json`） |
| **Kotlin**（引述） | `error: type mismatch: inferred type is X but Y was expected` + IDE 侧 quick-fix | —— |
| **TypeScript**（引述） | `error TS2322: Type 'X' is not assignable to type 'Y'` + **"为什么"的链**：`Types of property 'a' are incompatible` 一路往下说 | 没抄（要结构化的类型信息，我们没有） |
| **Ruby**（引述） | `NameError ... Did you mean?` 后**列出多个**候选 | **抄了**（见 §3 第二条）—— 实测的 8 门里没有一门列多个 |
| **PHP**（引述） | `Fatal error` / `Warning` / `Notice` **三级**，由 `error_reporting` 控制哪些出 | **不抄**：Loment 现在**只有 error**（`lomentc` 里没有 warning），没有 warning 就别造一个假的层级 |

## 2. 三件**十六门里没人做**的事

这三条是子代理实测完 8 门以后专门问的（"哪件事所有语言都没做到"），值得单列：

1. **没有一门为拼错**给出**多个**候选，也没有一门把候选**按种类**标出来（"是局部变量 /
   是函数 / 是类型"）。唯一的种类标注是 rustc 那句 `a local variable with a similar
   name exists` —— 而且它只标**那一个**候选。Java 的 `symbol: variable` 说的是**缺的那个**
   是什么，不是"你可能想写哪个"。
   → **我们这一轮两件都做了**（`help: names that are close:` + 每条带种类与行号）。
   实测 8 门 + 引述里只有 Ruby 列多个，而它不标种类。
2. **没有一门在给人看的输出里给出文档入口**。rustc 给的是**命令**
   （`try 'rustc --explain E0308'`）；C# 的 `helpUri` 只进 SARIF（机器才看得到）；
   clang 只给 `[-Wflag]` 标签；Go / Java / Python / Perl 什么都不给。
   → 我们照**最好的那个**抄：结尾一行 `more: loment explain E002`，而这条命令是真的
   （`lomcli` 的 `explain` 早就有，23 个码各一句话）。
3. **机器可应用的编辑都是"要额外要"的，而且一半语言根本没有**：C 要
   `-fdiagnostics-parseable-fixits`、C# 要 `-p:ErrorLog`（SARIF）、Rust 要
   `--error-format=json`；**Java / Go / Python / Perl 完全没有**这条机制（只有退出码）。
   → 我们的走法与 rustc 同类（`--json` 里带），但**多一条自己的规矩**：见 §3 第三条。

## 3. 这一轮实际抄进 `lomenterr` 的六条

| # | 抄自 | 落成什么 | 判据 |
|---|---|---|---|
| 1 | rustc 的 `help:` | 唯一候选时**印出改好的那一行**（行号 + 竖线 + 替换后的名字 + 新名字下的插入符），而不是只说一句"你是不是想写 X" | `test_the_help_block_prints_the_fixed_line` |
| 2 | Ruby 的 `Did you mean?` | **并列不再闭嘴**：列出最多 3 个候选，每条带**种类**（`a function` / `a local variable`，来自 `decl_kw`）与**行号** | `test_ties_list_candidates_with_kind_and_line` |
| 3 | rustc `--error-format=json` / Swift fix-it | `--json` 的 `suggestions[]` 给 `replacement` + **`byte_start`/`byte_end`** + `applicability`（`MaybeIncorrect`，与 rustc 对同类建议的判定同名同义）。**自己加的两条规矩**：字节偏移**只在那名字于本文件里全局唯一时**才给 —— 我们拿不到编译器的 span，位置是**推**出来的，推不准就只给名字；**没有候选时整个字段缺席**（`#51`），空数组会被读成"没有可用的修改" | `test_json_suggestions_are_machine_applicable` · `test_json_omits_an_empty_suggestions_array` |
| 4 | rustc 的 `try 'rustc --explain E0308'` | 结尾一行 `more: loment explain E002`（由出现过的码驱动） | `test_the_tail_points_at_loment_explain` |
| 5 | clang `-ferror-limit=20` / Go 卡 10 条 / javac `-Xmaxerrs` | `--max N`（默认 **20**，`0` = 全部），超了**明说还剩几条** | `test_max_caps_and_says_how_many_were_hidden` |
| 6 | clang 的 `^` 与 `~` 两种标记、`note: declared here` | 只抄了**精神**（插入符要指到 token）—— 见 §4 | 同 #1（改好的那行下面那个 `^`） |

## 4. 明确**不抄**的，以及为什么

* **javac 的"消息被简化了"坦白**（`Note: Some messages have been simplified; recompile
  with -Xdiags:verbose`）—— 实测**不成立**：这台机器上 javac 23 对类型不匹配与重载两种
  情况，加不加 `-Xdiags:verbose` 输出**逐字节相同**。没有"简化"这件事，也就没有那句坦白。
  （教训：**先实测再抄**。这一条我本来是打算抄的。）
* **warning / error 分级**（C# 的 `warning CS0219`、clang 的 `[-Wunused-variable]`、
  Rust 的 `warning: unused variable` + `#[warn(unused_variables)]`）—— `lomentc` 里
  **一条 warning 都没有**（全是 error）。造一个假的层级比没有更坏：用户会去学一个
  不存在的分类。**这一格等真有了 warning 再说**。
* **clang 的 `note: 'X' declared here` 第二处位置** —— 有价值（它把"为什么"变成一个**位置**），
  但我们的 E002 已经用"改好的那一行 + 候选在第几行"说清了同一件事；而真正需要第二处的
  是"重名"那类，那要**先确认编译器消息里点了名**再扫源码。**留着，没做**（下一轮的候选）。
* **TypeScript 的"because 链"**、**GHC 的 `Found:`/`Expected:` 两块** —— 都要结构化的
  类型信息（"为什么 X 不是 Y"），而我们手上只有一句文本。不是不想抄，是**没有那个输入**。
* **Perl 的 `-Mdiagnostics`**（把每条错误的整篇说明附在后面 —— 1994 年就有了，是我们四段卡
  的同类）—— 设计上该抄，但这台机器上它**坏了**（`perldiag.pod` 不在，报
  `couldn't find diagnostic data`）。**没抄**：抄来的东西得是**能跑的**，从一份坏掉的
  实现里抄形态没有意义。
* **Go 的"没有源码行也没有插入符"** —— 反面。定位到 token 是 8 门里做得到的那几家
  （Rust / clang / Python）都做了的事。

## 5. 顺带修掉的一个**真 bug**（实测逼出来的）

写这轮的时候，judge 用的是**手写的**诊断 JSONL；换成**真编译器产出的**诊断之后，
拼写建议**一条都不出**了，插入符也跑到了错的地方。原因值得记：

编译器的真消息长这样 ——

```
10: 调用未定义的函数 leftove_count（跨模块调用需要 pub）
```

而渲染器原来"取消息里最后一个词"当线索，于是取到的是 **`pub`**（那个括号里的提示文字），
不是 `leftove_count`。**插入符划到了 `pub`、建议去查 `pub` 的邻居。**

**为什么手写夹具没抓到**：我手写的消息是 `调用未定义的函数 leftove_count`，没有尾巴那句。
**夹具比真东西干净 —— 于是它测的是一个不会发生的输入。**

修法不是维护一张"消息里的噪声词"表（那是同一份清单抄第二遍，编译器改一次措辞就过期），
而是**拿源文件当裁判**：从消息末尾往前找，第一个**在本文件里整词恰好出现一次**的词。
"恰好出现一次"正是"它唯一地指着源里的一处" —— 想指某个东西就该有这个性质。
`pub` 要么不出现、要么出现多次，于是自动被跳过。

判据：`test_the_hint_ignores_the_compilers_trailing_note`（用**真诊断**，
不手写 JSONL）。
