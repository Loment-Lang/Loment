# 272 · `mode` 行：`std` 解锁什么、`no_std` 拿什么拒绝

> 状态: **规范**（2026-10-19，issue #941 / #539）· 上游: `docs/175` §3.0（核心模式）·
> `docs/219`（世界端口表）· `docs/222` §4（`choose hosted`）· `docs/227`（声明的带牙检查）
> 一句话: `mode` 不是一句形容词 —— **`std` 解锁的是"端口面"**，`no_std` 用**需求链**拒绝它，
> 而两份产物都把这件事**写在自己身上**，链接器核它。

---

## 1. `std` 解锁的那层是什么

`docs/143` §3.2 的两列表把 `std` 写成"核 + 宿主层"，那第二列一直没有名字。**它现在有名字**：
宿主层就是 `docs/219` 的**世界端口面** —— 产物指向世界上那些库的那组名字
（`libc` 的 `malloc`/`printf`/`fopen`、`zlib` 的 `compress`、共享对象导出的一切），
由 `choose hosted` 打开、由链接器在 `--link` 里落到真实文件上（`docs/222` §4、
`docs/183` 的 V1/V2 两条腿）。

**它不是一份 Loment 标准库**：Loment 的"标准库"（`std` 包 / LibL）在另一条线上
（`docs/232`），与 `mode` 无关。`mode` 管的是**产物能不能往下要东西**，不是"包里带了什么"。
这条界线正是 #539 缺的那句话。

所以：

| 写法 | 产物 |
|---|---|
| `std` + `sealed`（默认） | 只用核那一层，端口面**不打开** —— 今天的默认形状 |
| `std` + `hosted` | 端口面打开：可以链真 libc / 真共享库（`docs/219`、`docs/222`） |
| `no_std` + `sealed` | 只用核那一层 |
| `no_std` + `hosted` | **拒绝**，并给出需求链（下一节） |

## 2. `no_std` 的拒绝链

两条实现报的是同一条（`CORE_CONFLICTS[("no_std","hosted")]`，`loment_rule_parity`
的 `choose-hosted-no-std` 判据）：

> `no_std` 的定义是「只能用核那一层」——**没有宿主**；而 `hosted` 要链的是**宿主上的**库
> （libc / 共享库）。这一对是**定义上就矛盾**：一个说「底下没有东西」，一个说「往下链东西」。

拒绝点名的是**两个取值和它们各自的要求**，不是"非法组合"。

## 3. 产物自己说什么

- **Potato** 记 `mode`（`std` / `no_std`）—— 不读源码可判；
- **IR** 在 `no_std` 时多一行 `; loment-mode: no_std`（默认 `std` 的 IR 一个字节不变）；
- **两份链接器**读到那行时核 IR 里**没有** `; loment-port: hosted`；有就点名拒
  （`docs/227` 判据 B 的形状：编译器是那句话的作者，能核一份外来 IR 的只有链接器）。

判据：

- `loment_choose_matrix_test::test_no_std_is_a_claim_the_linker_checks`
  —— 诚实的 `no_std` 编得过、无宿主层；伪造的声明在链接期点名拒（参考实现）；
- `loment_elf_test::test_both_linkers_check_a_no_std_claim_against_the_hosted_layer`
  —— 两份链接器：诚实的收，伪造的拒；
- `loment_p8_test` 的 `ir_no_std.lomt` —— 两份编译器发的标记逐字节相同。

## 4. #539 的四条，逐条落点

| #539 说的 | 落在哪 |
|---|---|
| ① 点名的层 | 本页 §1（端口面 = `docs/219` 那张表） |
| ② `std` 解锁、`no_std` 按需求链拒绝 | §2 + `CORE_CONFLICTS`，两条实现同码同文 |
| ③ 对用户入口也真的拒 | 自举驱动同样报 E022（`loment_rule_parity` 的负例）；`loment_hosted_test` 反过来钉"`std`+`hosted` 真跑" |
| ④ 两个方向各有判据 | `loment_hosted_test`（std + 真 libz 跑通）· 矩阵/elf 两条（no_std 拒绝与带牙检查） |

## 5. 边界（别当成缺陷重开）

- `std` **不要求**带宿主层：它是**许可**，不是**内容**；`hosted` 才是那个开关。
- 端口面**没有逐条白名单**：`docs/219` 量的是"链完之后还悬空的符号"，`--allow-ports`
  是渠道门的开关（`docs/222` §4.5）。`mode` 不重复管这件事，只拒绝 `no_std + hosted`。
- `no_std` **不禁止** `extern fn` 的声明本身：声明是"我要什么"，能不能落地由端口门与
  `--link` 决定；`no_std` 拒绝的是**打开宿主层**这个选择。
