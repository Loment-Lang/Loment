# 六门表层语法的**大型项目**语料

> 目的：验证 `choose write grammar` 那套多语法系统**扛不扛得住一整份真项目**，
> 而不是只扛得住小夹具。判据在 `tools/loment_multisyntax_projects_test.py`。
>
> 六门 = C / C++ / Java / Python / Go / **Tur**。`tur/` 那一门与另五门不同类：它是
> **本语言自己的原生语法**（2026-11-03，`docs/285`），前门不经过翻译器。它的正文刻意
> 只落在 Rust 的交集里，所以抹掉 `choose` / `module` 两行之后仍能交给 `rustc` 当对照组。

## 与 `loment/examples/multisyntax/` 的分工

| | `examples/multisyntax/`（`docs/179` §8） | **这里** |
|---|---|---|
| 是什么 | 抽**接口**的语料（只记声明，无正文） | **能真跑**的程序（带正文，真编真跑） |
| 谁读 | `potato_from.transcribe`（`docs/179` 那条老路） | 前门 `choose write grammar`（`docs/188`） |
| 判据 | `loment_multisyntax_test`：认对/合法/非空/过检 | `loment_multisyntax_projects_test`：**跑出的数与对照组相等** |
| 目录 | `multisyntax/<语法>/<NN-名字>/` | `multisyntax-projects/<语法>/<NN-名字>/` |

**刻意分成两个目录**：`loment_multisyntax_test` 会把 `multisyntax/` 下**每一个**子目录
当成一个语法名（`assert claim in potato_from.LANGS`），把项目放进去会让它当场红。
两边的判据也问的不是同一个问题（那边问"抽得对不对"，这边问"算得对不对"）。

## 一个项目长什么样

```
<语法>/<NN-名字>/
    README.md      项目说明：做什么、算法、**期望值怎么推出来的**
    main.lomt      宿主 —— **原生 Loment 语法**（没有 `choose write grammar` 行）
    <模块>.lomt    计算主体 —— **表层语法**（首行 `choose write grammar <语法>`）
```

### 契约（三条，判据按它驱动）

1. **模块暴露一个 `entry()`**，返回 `i32` —— 这个项目的**答案**。
   各门它长什么样：
   * C / C++：`int entry()` / `int entry()`（顶层函数）
   * Java / C#：`public static int entry()`（在类里）
   * Go：`func entry() int`（`package main`）
   * Python：`def entry() -> int:`
   * Rust：`pub fn entry() -> i32`
2. **宿主 `main.lomt`** `use "<模块>.lomt"`，在 `_start` 里
   `syscall4(60, entry() as u64, 0, 0)`。宿主薄是**被迫的**：那一门
   （Stage A 子集）**调不到内建函数**（`alloc` / `syscall4` / `str_*` 一律被拒）、
   **也不能跨单元调用**，所以 I/O 只能是宿主的事。
3. **退出码 = `entry() & 255`** —— 两边都过一遍这个掩码（POSIX 只留低 8 位），
   于是"算出的数"直接可比。

## 判据怎么判：**三方**，缺一不可

```
   那份源 ──真实工具链（clang / g++ / javac / go / CPython / rustc）──> rc_control ─┐
                                                                                    ├─ 三者相等
   那份源 ──前门翻成 Loment──> lomelf ──> ELF ──WSL 跑──> rc_loment ───────────────┤
                                                                                    │
   README 里**独立推出来的**期望值 ──────────────────────────────────────────────expected ─┘
```

* **少了对照组**，"翻译器把一个数算错、而那个数谁也没验过"看不出来；
* **少了独立期望值**，"翻译器与对照组**一起**错成同一副样子"会被当成通过
  （`docs/186` §1）。实测过：`expected` 与 `rc_control` 一开始**不一致**，
  见下面那条 UB。

## 语料的纪律：**别踩未定义行为**

`c/01-algo` 写 `isqrt` 时踩了一次，值得留着当教训：

```c
int mid = (lo + hi + 1) / 2;
if (mid * mid <= n) { ... }        /* n > 8.6 万时 mid*mid 溢出有符号 32 位 */
```

三方给出**三个不同的数**：

| | n = 1000000 |
|---|---|
| 独立推的（数学语义） | **1000** |
| `clang -O1` | **1000**（它利用了 C 里有符号溢出是 UB，把它优化成了数学解） |
| 翻译出来的 Loment | **458753**（本语言在这里是**回绕**语义，确定且可复现） |

**两边都对"自己没定义的那件事"做了不同的选择，判据于是不可比。**
改法是让那一处**没有溢出**（`mid <= n / mid`，除法不溢出），三方立刻一致。

⇒ 写这一门的语料时：**整数不许溢出**。这不是翻译器的毛病，是 C 的 UB 与
Loment 的回绕语义在这件事上本来就没有共同答案。
