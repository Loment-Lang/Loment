# multilang —— 一条流水线，三段各用一门语法写

**Loment 当宿主**：这个程序的四个部分里，**三段不是用 Loment 写的**，Loment 只负责
把它们接起来。

```
   C（翻译腿）        Python（运行期腿）     Java（运行期腿）        Loment
   ──────────        ──────────────        ──────────────        ──────
   装帧 / 拆帧   ->   判策略等级       ->   走状态机         ->   合起来 + 打印
   01-c/pack.lomt    02-python/policy.lomt  03-java/Machine.lomt  main.lomt
```

跑出来是：

```
frame=459752 level=2 state=2        （退出码 200）
```

## 为什么每一段是那个语言

| 段 | 语言 | 为什么这一段归它 |
|---|---|---|
| 装帧 / 拆帧 | **C** | 位运算 —— C 那一族的主场；也正好把**翻译腿**走完（`--impl` 把 C 写法翻成真 `pub fn`，**不起 clang、不链 `.o`**） |
| 阈值判级 | **Python** | 阈值天然是**配置**；写成模块级常量，换阈值不用碰 Loment 那一侧 |
| 状态迁移 | **Java** | 这一类东西在 Java 那一族里通常写成带常量与静态方法的类 —— 保持那个形状 |

**这三份 `.lomt` 里装的不是 Loment**：后缀说"这是 Loment 的源文件"，里面装的东西
可以是别的语法（`docs/179` §1.1 的第二层"按内容识别"就是认这种文件）。

## 两条腿（`docs/173` §2）—— 以及它们之间为什么只能传字节

* **C 走翻译腿**（`docs/188` §3）：`lomt_from --impl` 把 `01-c/pack.lomt` **翻成真
  `pub fn`** —— **不起 clang、不链 `.o`**。一份 C 写法写的单元**就是 Loment**，
  不是"实现在外面"的外国函数。
  > **2026-10-09 更正**：这里原来说的是"生成**接口单元**（`pub extern fn`）、实现在
  > 编好的 `.o` 里、链进来" —— 那是 **2026-09-18 之前的旧模型**，实现与
  > `loment_multilang_test` 的夹具当天就改了，README 没跟上（`docs/211` 记了这条）。
* **Python / Java 走运行期腿**：它们不导出 C ABI（是解释器 / JVM），所以
  `loment/lib/proc.lomt` 起一个进程，把 **stdout 读回来**。

⇒ **两条腿之间传不了指针**。所以帧要落成**字节**（`frame.bin`，4 字节小端），
等级也要落成**字节**（`level.txt`）。**这个边界是这一节的要害，不是实现偷懒**：
进程桥那一边没有我们的地址空间。

## 怎么跑

```bash
wsl -e bash -c "cd /mnt/d/Dev/Loment-DEV && python3 tools/loment_multilang_test.py"
```

或者直接：

```bash
python tools/loment_multilang_test.py
```

判据 `loment_multilang_test` 会**物化**这三份外源语法（`pack.lomt` → `pack.c`、
`policy.lomt` → `policy.py`、`Machine.lomt` → `Machine.java`），跑，比对 stdout 与退出码。
**C 那一段在物化之后走的是翻译**（`--impl` 出真 `pub fn`，**不起 clang、不链 `.o`**），
Python / Java 那两段才起各自的运行期。

**单一真源是那三份 `.lomt`** —— 物化出来的副本全在临时目录里，谁都不该去手改
（手改必然与 `.lomt` 漂，判据里有一条专门钉这个）。

## 这里有多少是"调节器"该做的

夹具现在替 **S2**（`docs/183` §8.2 的 `foruse` / `command`）做了两件本该属于编译器的事：

1. **把 `.lomt` 里的外源语法物化成目标语言要的形状**，再起它的运行期
   （Python / Java 那两段）—— C 那一段是**翻译**掉的，不起编译器；
2. **找到目标语言的运行时并摆到进程能看见的地方** —— `proc_sh` exec 的是
   `/bin/sh -c <命令>` 而**不传环境**（`loment/lib/proc.lomt:120` 是
   `execve(path, argv, NULL)`），所以自建 JDK 不在子进程的 `PATH` 上，
   夹具为此生成一个 `run-java.sh`。

两件都是"调节器"的活儿。**S2 落地时它们搬进编译器，本判据一行都不用改** ——
它比的是"这个程序跑出什么"，不是"谁去编的、谁去找到 java"。

## 它同时是 S1 的邻居

`docs/185`（S1：外部代码块）要的 `let c { … }` 是**同一件事的另一半**：
S1 让外语言正文进得了 `.lomt`，这一份演示的是**外语言正文已经在 `.lomt` 里**
（按内容识别）时，整条链怎么走通。两条路将来会合并 —— 但今天它们各自都能跑。
