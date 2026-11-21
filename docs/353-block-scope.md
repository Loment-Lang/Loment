# 353 · 块作用域：`tur2027.1Alpha`

> 2026-11-18。对应 issue `#1309`（核心没有块作用域：内层 `let` 静默改绑外层）。
> 上游：`docs/228` §3.1 / `docs/210` §7 / `docs/186` §5.2 / `docs/321` §5。
> 规范面在 `standard/LLS.md` §11.1 / §11.3 / §19.4 / §26.3 / §39.3。

## 0. 一句话

`tur2027.1Alpha` 起，**每个花括号块是一层词法作用域**：内层 `let` 遮蔽外层而不是
改绑它；同一个块里第二次声明同名是 E013；独立 `{ … }` 是语句。旧版次
（`tur2026.1/.2/.3`）保持原来的函数级泄漏语义 —— 冻结承诺不动。

## 1. 版次闸

| 版次 | 状态 | `let` 的作用域 |
|---|---|---|
| `tur2026.1` | 冻结 | 函数级（泄漏） |
| `tur2026.2` | 冻结 | 函数级（泄漏） |
| `tur2026.3` | 开放（默认） | 函数级（泄漏） |
| `tur2027.1Alpha` | 开放、**alpha、显式选用** | **块级** |

`tur2027.1Alpha` **不跟默认走**：默认仍是 `tur2026.3`。理由：alpha 的语义会随反馈
变，把默认挪过去等于让每一份没声明的新旧源先吃一轮预览语义。写法：

```rust
choose write grammar tur2027.1Alpha
```

实现侧只有两处分界：参考实现 `BLOCK_SCOPE_EDITIONS = ("tur2027.1Alpha",)`；
自举侧 `PROF_OFF == 3`（`0/1/2` 仍是旧版次）。旧版次一条新判据都不跑。

## 2. 规则

**作用域面**（`docs/353` 的这一版）：

* 函数体（形参在**外层**，函数体是它的子作用域，所以 `let p` 可以遮蔽形参）；
* `if` / `else` 各一支；
* `while` 体；
* `for` 体（循环变量只在循环里可见；体内 `let i` 可以遮蔽循环变量）；
* `match` 臂（载荷绑定只在该臂可见）；
* `if let` 的 then 支（绑定只在支内可见）；
* `catch |e| { … }` 的 catch 块；
* `domain { … }` 块；
* **独立 `{ … }` 块**（旧版次里这是 E019，`#44`）。

**可见性**：`let` 从声明**之后**到所在块结束；初值在**外层**求值（`let x = x` 读外层）。
`for` 变量在头里**不可见**（`for i in i..3` 的 `i` 读外层），只在体里可见。
`match` / `if let` 的绑定只在臂 / then 支里可见。

**遮蔽与重复**：

* 嵌套块里可以遮蔽外层同名；
* **同一个块里第二次 `let` 同名 = E0241**（E013 重名族的细分码，消息点名首次行号）；
  两种实现发同一个码；
* 两个兄弟块各写一个 `x` 不是重复（各自独立的声明）；
* 参数与函数体不是同一个块：体内 `let` 可以遮蔽参数。

## 3. 存储与槽名

每个声明**各自一格 alloca**。槽名是

```
第一个 x   -> %x.addr
第二个 x   -> %x.1.addr
第三个 x   -> %x.2.addr
参数 p     -> %p.addr      （参数先占 0 号）
```

序号按**函数体内的声明顺序**（token 顺序）分配：`let`、`for` 变量、`match` / `if let`
绑定、`catch` 绑定、`domain:for` 变量都算。合成名（`__t{line}` / `__v` / `__e` /
`__tur2_i` / `__tur2_s`）不加后缀，它们本来就不会撞。

两个实现必须数出**同一张表**：参考侧 `_collect_locals` 在 AST 上按源码序走；
自举侧 `collect_locals` 在 token 上按同一序走，`add_local` 在 alpha 下不去重、
写 `CG_DORD` / `CG_SSTART` / `CG_SEND`。引用解析：

* 参考侧：发射器维护 `env` 栈（按块复制的 `名字 -> 槽名`），`_Ir.slot_of` 取最内层；
* 自举侧：`resolve_decl` 从引用往回找最近的同名声明，用 `CG_SSTART` / `CG_SEND`
  与反向括号扫描判可见性（块外 / 兄弟块的声明不会被选中）。

判据是**逐字节 IR**：同一份源两个实现发出的 `.ll` 必须相同。

## 4. GC：`ReclaimScoped` 与 E12

GC 的 L0/L1/L2 规则原来按 token 流上的**源码名字**记账。块作用域之后，一个名字在
同一函数里可能有两个声明，那些谓词分不清是谁。这一版的边界：

* **按名字保守退出**：只要一个名字在函数里出现两次以上，**这个名字**的 L0/L1/L2
  与 L0.5 / 槽掩码全部退出（其余名字照常优化）。少回收是安全的，free 错一格不是。
  这条与自举侧"token 扫描遇到第二处同名声明就停"是同一道边界。
* **E12 GC 检查器**（`loment/lspe/framework.py` 的 `E12GC`）：每个 `alloc` 一个
  `reclaim` 站点，义务 `ReclaimScoped`。分配名在该函数里只声明一次 ⇒ `Syntactic`
  （回收判决唯一指到一个声明）；出现两次以上 ⇒ `Assumed`（不证明，进地板）。
  `tools/lspe_test.py` 钉住登记表；`lomentc_test::test_lspe_obligation_registry`
  钉住 20 条义务。
* **自举孪生**：`potato.lomt` 的 `pt_reclaim_census` 按同一判据数 `(rt, rp)`，
  `per_kind` 里输出 `ReclaimScoped`；只在 `prof == 3` 时算，旧版次一个字节不变。

**没做**：真正按**声明**给 L0/L1/L2 记账（`_l0_promotable` / `_l2_ok` 不再看名字，
而是看 `resolve_decl` 的结果）。今天的按名字退出是**安全**的中间态；E12 的表已经
把声明身份算出来了，下一步可以直接接。写明在这里，别把"保守退出"读成"已按声明优化"。

## 5. 解析器 / 检查器

* 参考侧新增 `Block(body, line, kind, tok_at, tok_end)`；`parse_stmt` 在 alpha 下
  把语句位的 `{` 解析成块。`lspe pass` 与 `lgc` 窗口在 alpha 下也包一层 `Block`
  （它们是块），旧版次照旧摊平。
* 自举侧 parser（只给 AST dump 判据用）在 `parse_alpha` 为真时把独立块 dump 成
  `(block ( … ))`；`lspe pass` 与 `lgc` 窗口的体也一样包一层。与参考侧 `_st` 的
  口径一致（窗口本身仍摊平成 `(lgc start …)` / `(lgc close)` 两个兄弟结点）。
* 自举侧 `switches.lomt` 的 `desugar_lspe_pass`：alpha 下**只删头、留下 `{` / `}`**
  （旧版次连括号一起删）。括号一并删掉的话，体里的 `let` 会漏进外层 —— checker
  会多报同块重名，发射器也会把遮蔽的名字解析到错误的声明上。
* `lgc` 在自举侧的版次判据从 `== 2` 改成 `>= 2`（checker 三处、codegen 两处、
  driver 的插值改写一处）：alpha 是 3，`== 2` 会把它当成旧版次 —— 症状是
  `lgc start` 里的 `start` 被当成未定义函数调用（E096）、整句被当成赋值（E137）。
* 自举 checker：`chk_body` 在 alpha 下对**语句位 / 分支体**的 `{` 递归检查整块，
  子调用拿到的 `envc` 就是块起点；同块重复 E013（码 13）。`chk_env_get` 改为
  **从最近一格往回找**（内层遮蔽外层；旧版次里它与参考的"后写覆盖"同侧）。
  `chk_stmt_end` / `chk_falls` 认识独立块，块后的 `return` 不再被吞掉。
* 旧版次：`chk_body` 的 `{` 只跳过（今天的形状），`chk_env_get` 反向搜索的差异
  由 `loment_rule_parity` / `loment_p8_test` 的码集判据盯着。

## 6. 判据

新增（在本 PR 内）：

* `lomentc_test`：遮蔽值、兄弟块、同块重复 E013、独立块、块后用名 E002、
  `for` 变量遮蔽、`match` / `if let` 绑定遮蔽、旧版次保持不变；
* `loment_p8_test`：alpha 语料的 **IR 逐字节**（block / forvar / matchbind /
  ifletbind / gcshadow / `lgc` 三种窗口）；
* `loment_p8_test` M80：alpha 独立块与 `lspe pass` 体的 AST dump 与参考逐字符；
* 自举 checker 的码集与参考一致（E013 / E002 / `lgc` 与 `lspe pass` 块里的遮蔽）。

GC 的 E12 不进 `gate-skips`；它只在 alpha 语料上有站点，旧语料的计数不变。

## 7. 没做 / 边界

* **独立块的表达式值**：没有。块是语句，不是表达式；要值请用 `if` / `match`
  表达式（2026.3 已有的那两门）。
* **借用跨越块边界**：`&x` 借着内层 `x` 出块，今天的检查器按"返回局部借用"那一格
  处理（E012），不另立块级借出规则。
* **`lspe pass` / `lgc` 窗口的覆盖计数**：alpha 下它们是块，但 `pass_sites` 的
  覆盖仍按旧 token 扫描；alpha 语料暂不把窗口与 E12 站点叠在一起。
* **`domain` 块的 IR 对拍只比函数体**：自举发射器见到 `domain … { }` 就带上能力域运行时
  段，参考实现只在真的发出 `guard` / `tick` 调用时才带（旧版次差得一样）。块里的槽名与
  可见性已经逐字节比过（`%x.addr` / `%x.1.addr`），差的只是那段没被调用的运行期。
* **`lspe pass` 的理由字符串**：自举发射器会把理由字面量收进全局字符串段，参考实现
  不收（理由不进 AST）。这与版次无关，旧版次同样差一行；块作用域这一版没有顺带改它。
  它不影响块内的槽名与可见性 —— alpha 语料的 IR 对拍在这条上是"除那一行外逐字节一致"。
* **L0/L1/L2 按声明记账**：见 §4 的"没做"。
* **旧版次的 `chk_env_get` 反向搜索**：会与旧版次"同名声明复用一格"的发射路径
  产生更多早报；这是码集对齐的方向，不是新增语义。

## 8. 复现

```rust
choose write grammar tur2027.1Alpha
module shadow
choose runtime

fn probe() -> u32 {
    let x: u32 = 1;
    let y: u32 = 0;
    if 1 == 1 {
        let x: u32 = 2;
        y = x;
    }
    return x + y;
}
```

参考实现：`%x.addr` 与 `%x.1.addr` 各一格；返回 `1 + 2`。旧版次（去掉第一行）：
只有 `%x.addr`，返回 `2 + 2` —— 那是冻结版次的行为，不是缺陷。
