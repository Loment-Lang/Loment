# docs/272 · 诊断通道的两条边界（#773 / #775）

> 两条 issue 是同一个形状的两侧：**诊断系统只能描述"在失败之前已经成功序列化"的失败**。
> 一条在**进程之间**（编译器死了，渲染器看不见），一条在**两个实现之间**（自举发射器
> 崩了，检查器替它背规则）。这一篇记的是这两条边怎么关的、关到哪、还剩什么。

## 0. 先看边界在哪

`lomenterr` 的输入是一份 JSONL（`--diag-out`）。它**不观察进程**，只读文件。于是：

| 失败发生在 | 谁看得见 | 修之前的结局 |
|---|---|---|
| 检查器/装载器 | 编译器自己 | 一条记录 + 一行裸话 |
| **编译器崩溃**（SIGSEGV / SIGILL / 固定表写穿） | 没人 | 空文件 + 退出码被启动器压成 1 |
| **启动器包装的那一层**（lomelf） | 没人 | 同上（lomelf 已经有 `--diag-out`，但崩溃时也写不出） |
| **lomenterr 自己** | 没人 | 退出码丢失，用户只看到少了一段输出 |
| 写诊断文件的 `ENOSPC` / `EACCES` | 只有一句 `exit 2` | 至少是响的，但没有记录 |

第三条以后仍然存在（见 §3）。这一轮关的是**中间那三行**：进程死了，也要有人收尸。

## 1. #773：启动器是"编译器之前"的那条通道

### 1.1 落点

两个启动器（`bin/loment` 的 bash 版与 `loment.cmd` 的 batch 版，都由
`tools/loment_dist.py` 里的字符串生成）各加了同一个函数：

* **bash**：`synthesize_diag` + `signal_name`；`report_diags` 多收两个参数
  （退出状态、工具名）；
* **cmd**：`:synthesize_diag`；`:report_diags` 多收 `%~4`（状态）、`%~5`（工具名）。

规则只有一条：**工具以非 0 退出、而 `--diag-out` 一份记录都没写时，合成一条**。

### 1.2 记录的形状

```json
{"file":"loment-driver","line":0,"col":0,"code":"E900","message":"loment-driver was killed by signal 11 (SIGSEGV) and wrote no diagnostic record"}
```

* `line: 0` = **没有源码位置**。这与 `docs/256` 那三条（E031..E033）同一个口径：
  渲染器不印 `:0`，也不去源里切行。崩溃没有一行可指，编一个行号比不指更坏。
* `file` 写成**工具名**（`loment-driver` / `loment-lomelf` / `lomenterr`）——
  它不是一份源文件，读者要一眼看出是哪一段链死了。
* 消息**纯 ASCII 英文**（`docs/169` §3a），并且**说出信号名**：`$?` 在 POSIX shell
  里是 `128+N`，`signal_name` 把常用的那些翻成 `SIGSEGV` / `SIGILL` / `SIGFPE` …；
  cmd 侧拿到的是 NTSTATUS（`0xC0000005` 等），按字符串比较翻成 access violation /
  illegal instruction / divide by zero / stack overflow。

### 1.3 为什么是 E900，而不是下一个 E0xx

**码按修法分**（本仓老规矩）。E0xx 说的是"你的源码哪一处要改"（1..35 用户码）
或"这一版还没实现"（E023）。E900 说的是"**实现自己崩了**"——用户能做的事完全不同
（报缺陷，而不是改那一行）。而且用户码段有一条"从 1 起连续、`n_codes()` 是上界"的
不变式：把 900 塞进去会让 `loment codes` 从 36 到 899 印出几百行空标题。

所以 `surface_data` 生成器现在给**两段**：

```
n_codes()      -> 35    # 用户码 1..35，连续
internal_lo()  -> 900   # 内部码段，独立连续
internal_hi()  -> 900
```

`lomcli codes` 分两段列出，`explain` 两段都认，中间 36..899 仍是空号（拒绝）。
`loment_tools_test::test_code_tables_cover_the_same_codes` 从"一个连续段"改成
"两段各自连续"。

### 1.4 渲染器自己崩了呢

`report_diags` 现在捕获 `lomenterr` 的退出码：

* 0 / 1 = 正常（无诊断 / 有诊断）；
* 其他 = 渲染器失败。打一行
  `loment: lomenterr died (status 139 (SIGSEGV)) while rendering <file> [E900]`，
  **并把编译器的裸行照旧打出来** —— 渲染器坏了不能让底层错误跟着消失。

这就是同一个边界往下一层：**替渲染器收尸的是启动器**。再往下一层（启动器自己崩）
没人收尸；那是 shell / OS 的事，本文不假装解决了。

### 1.5 判据

`loment_err_test::test_launcher_synthesizes_a_record_when_the_compiler_dies_silently`
跑**真启动器 + 一个 `kill -SEGV $$` 的桩驱动**，三种组合：

1. 有渲染器（桩渲染器 `cat` 出它收到的文件）：记录必须真的出现在 JSONL 里，
   `code == "E900"`、`line == 0`、`file == "loment-driver"`、消息含 `SIGSEGV`；
2. 没有渲染器：同一句话以裸行出现，并且明说"这个包里没有 lomenterr"；
3. 渲染器自己崩：启动器说出它死了，**且**仍然打出底层的 `loment-driver was killed`。

修之前这条判据是红的：空文件 → `report_diags` 只回吐驱动的 stderr（崩溃时为空）→
用户拿到零行输出。

## 2. #775：自举发射器自己的诊断通道

### 2.1 为什么不是"把规则加进检查器"

#775 列了两种设计：

* **A. 把"可降级"变成类型级谓词**，检查器一次判掉，发射器永不拒绝；
* **B. 给 `codegen.lomt` 一条诊断通道**，两个实现各长一条。

A 是终局形态，但它要求发射器把"降不降得下去"的**全部**知识表达成类型系统里的东西，
而那正是发射器唯一知道、检查器不知道的部分 —— 一轮做不完，而且会把语言面往
"类型系统要能表达后端寄存器/槽位限制"的方向拖。

B 可以**增量**落地，并且直接消掉那个反模式：**规则可以留在发射器里，因为发射器能说话了**。
这一轮选 B。A 没有被否定；它落地时 B 仍然是"最后一道兜底"（发射器真遇到设计外的东西
时，不能回到信号）。

### 2.2 机制

`codegen.lomt` 的状态块里加两格（空出来的 588/592，见文件头注释）：

```
588 = 规则号（0 = 没失败）
592 = 命中的 token 下标（0 = 没有位置）
```

* `cg_fail(s, code, tok)`：**粘性标记**，只记第一次失败。递归下降的每个返回点都改成
  三态会把几百处调用点翻一遍，而且容易漏；"继续发、末尾作废整份输出"代价小得多。
* `emit_error_code(s)` / `emit_error_tok(s)`：`pub`，给驱动器读。
* `emit_module` 入口清零两格（`brk` 是零，但夹具的 `malloc` 会给脏页 —— 文件里
  替换表那条注释记着同一个坑），末尾 `if CG_ERR != 0 { return 0; }`。
* 驱动器在 `emit_module == 0` 时**先问** `emit_error_code`：有规则号就写一条带位置的
  诊断（`wdiag`，码 E023），没有才落回"表装不下"那句（E033）。顺序不能反 ——
  规则失败比容量失败更具体。

### 2.3 第一条搬过去的规则

`codegen.lomt` 原先在一次调用有 **>20 个实参**时 `panic(20)`：

```rust
if a == 20 && !tok_is(src, toks, k, ")") {
    panic(20);
}
```

那 20 是 `CG_PARAM` 每函数的槽数（`#265` 量的），**不是语言规则** —— 参考实现收
21 个实参、也发得出来。这是最干净的一个样本：规则只长在自举发射器里，修之前它的
唯一结局是 ILL + 空诊断（实测 `rc=0xC000001D`）。

现在它写 `cg_fail(s, 1, j)`，驱动器报：

```
E023 @<行>  the self-hosted emitter cannot lower a call with more than 20 arguments
            -- split the call into smaller ones
```

**检查器不动。** 这正是 #775 要的方向：规则待在自己唯一知道真相的那一侧，
用户仍然拿到一条能读的诊断。

### 2.4 孪生与种子

两个实现的输出**对合法单元仍然逐字节相同**：通道只在失败时置位并作废输出，
成功路径一个字节都没变（`emit_error_*` 只读状态块，不写 IR）。自举侧新增的两个
`pub fn` 与常量进了种子，`loment_seed.py --emit` 重算即可。

### 2.5 判据

`loment_cli_test::test_the_emitter_reports_a_rule_only_it_knows`：

1. 21 个实参的单元，**参考检查器收下**（断言它干净 —— 若哪天有人把这条规则抄进
   检查器，这条断言会红，提醒"这不是语言规则"）；
2. 参考发射器发得出来；
3. 自举驱动：`rc == 1`（不是 `0xC000001D`）、`--diag-out` 有且只有一条 `E023`、
   `line > 0`、消息含 `more than 20 arguments`；
4. 驱动自己的 stderr 也有那一行（直接跑驱动的人没有渲染器）。

修之前：`rc=0xC000001D`、JSONL 为空 —— 这条判据在未修的树上**必红**。

## 3. 还没关的（如实记）

* **发射器拒绝点的全量枚举**。#775 的"证据"那一条想要的是：列出每一个发射器拒绝点，
  为每个构造一份"检查器收下、发射器拒绝"的单元，然后断言**关系为空**。这一轮只做了
  通道 + 一个代表样本。全量枚举要先把发射器的拒绝点从"散落在 6k 行里的分支"变成
  一张**可数的表**——那是下一轮的结构性工作。
* **OS 拒绝（ENOSPC / EACCES）本身没有记录**。`--diag-out PATH` 打不开时启动器
  仍然只有一句 `exit 2`；它没死，所以不触发 E900。要给它一个码，得先定义"诊断文件
  写不出去时诊断该写到哪里"——那是一个自指问题。
* **启动器自己崩**没有收尸人。shell / OS 只给退出码。E900 覆盖的是"启动器还活着、
  工具死了"这一层。
* **报错器的渲染上限/截断**（`--max`、1 MiB、16,397 字节）不是这一条边界：记录存在，
  是渲染层丢的。`docs/182` §15 已经让那些情形**说出来**（"还剩 N 条"、"被截断"）。
* **cmd 侧的判据只有静态那半**（本仓造不出能跑 `loment-driver.exe` 的桩包），
  与 `loment_cli_test` 里 cmd 的其余断言同一条纪律。真正的 cmd 行为由发行包冒烟
  （`loment_dist_test`）覆盖。

## 4. 判据计数

* `loment_err_test` +1（`test_launcher_synthesizes_a_record_when_the_compiler_dies_silently`）
* `loment_cli_test` +1（`test_the_emitter_reports_a_rule_only_it_knows`）
* `loment_tools_test` 的 `test_code_tables_cover_the_same_codes` 改为两段连续性；
  条数不变。
