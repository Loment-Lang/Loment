# `tur/01-bitkit` —— 整数校验与位操作工具箱（Tur 语法，**原生读法**）

**用 Tur 的拼法写的 Loment。** `bitkit.lomt` 首行 `choose write grammar tur` 说的是
"按 Tur 的读法读这份文件"；Tur 是本语言自己的正式语法（从 Rust **直接修改**），而它的
语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定拼法与形状，不决定语义**）。
宿主 `main.lomt` 是默认原生语法，只负责调 `entry()` 与退出。

主题：一个确定性的"字节流"（`byte_at`）喂给一族哈希 / 校验和，再配一组位运算原语。
`entry()` 把二十个中间值折成一个数交给宿主 —— **每一步都参与**，任一算法错了退出码就变。

EXPECTED: 27

## 这一门与另五门**不一样**的两处（都实测过）

### 1. 模块必须**自带 `module <名字>` 行**（题面已提醒）

`tur` 是 Loment 的**原生语法**，前门**不经过任何翻译器** —— 它只把首行声明抹成空白，
原样交给**原生解析器**。原生解析器要求"文件必须以 `module` 开头"，所以这一份的第二行
必须是 `module bitkit`（名字与文件 stem 一致）。另五门的模块名是**翻译器从文件名造的**，
那五份里**不该**有这一行（在那些源语言里它是语法错）。

```
$ printf 'choose write grammar tur\n\npub fn entry() -> i32 { return 5; }\n' > x.lomt
$ python tools/lomentc.py --check x.lomt
[ERR] 期望 module（文件必须以 module 开头），得到 'pub'
```

**对照组的做法**因此比另五门多抹一行：`_control_source` 里除了抹掉
`choose write grammar tur`，还要抹掉 `module bitkit`（它在真 Rust 里是语法错）
—— 抹法是**等长空白**，行号不漂。抹完之后这份源仍然**是合法的 Rust**，所以
`rustc` 继续当对照组：Tur 是从 Rust 直接修改出来的，这一份刻意只落在两者的交集里。

### 2. 这份**没有 `while`/`for`，全用递归** —— 一个**原生读法独有的坑**

这一份要**同时是合法的 Rust**（对照组用 `rustc` 编它）。两种语言在**赋值**上撞了一个
**没有交集**的地方：

| | 再赋值一个局部变量 | 改形参 |
|---|---|---|
| Rust | `let mut x: T = e;` 才能改（`let x` 不可变） | `fn f(mut a: T)` |
| Tur（不经前门映射） | `let x: T = e;` **本就可变**，直接 `x = e;` | 形参直接可改 |

`mut` **不是本语言的关键字** —— 不经前门映射时 `let mut x: T` 当场解析失败
（`loment_diag.py` 把 `let mut x: T` 明列在"不支持"一栏；`docs/143` 的对照表把
Rust 的 `let mut x: T = e;` 映成本语言的 `let x: T = e;`）：

```
$ cat m.lomt      # choose write grammar rust / module m / fn f 里写 let mut v: i32 = n;
$ python tools/lomentc.py --check m.lomt
[ERR] 5:13: 期望 :，得到 'v'

$ cat r.rs        # 真 Rust：let x: i32 = n; x = x + 1;
$ rustc r.rs
error[E0384]: cannot assign twice to immutable variable `x`
```

⇒ "**改一个局部变量**"在两个表层里**没有共同写法**。而"带着状态的 `while` 循环"
恰恰要求这么一件事，所以它**进不了这个交集**。出路只有一条：**不用赋值** —— 把每一步的
中间值当**参数**传给下一次**递归**。本文件的每个"循环"都是这么写的（`*_go` 这些内部
函数）。

**这是原生读法独有的**：另五门（C / C++ / Java / C# / Go / Python）里局部变量本来就可变，
翻译器把 `x = e;` 原样搬成本语言的 `x = e;`，一门都不用绕。只有 Tur 这一门同时受
"Rust 要 `mut`"和"本语言没有 `mut`"两头夹 —— 因为它是**原生读法**，没有翻译层去补这个差。
（前门现在会把 `let mut` / `mut a:` 这两种声明标记**等长映射掉**（`#77`，
`_strip_tur_mut`），所以这份源也可以改写成 `while`；它保持递归写法，是为了同一份字节
**不依赖**前门映射也仍然能被 `rustc` 当对照组编译。）

`crc8_like` 与 `checksum_rounds` 仍然钉住了"**两层嵌套的迭代**"（外层字节 / 内层位；
外层轮数 / 内层一趟），只是那两层是**嵌套递归**而不是嵌套 `while`。

## 二十三个 `pub fn`，各钉一类写法

| 函数 | 钉住什么 |
|---|---|
| `byte_at` | 确定性"字节流"，纯算术、无数组 |
| `gcd` | **递归**（把新值当参数传动）、两变量互换 |
| `log2_floor` | **递归**右移计数 |
| `poly_hash` | 多项式滚动哈希，**跨函数调用**（`byte_at`），`% MOD` 收界 |
| `djb_like` | djb2（`h<<5 + h`）—— 移位当乘 33 用，**无溢出** |
| `popcount` | Kernighan 那一招（`n & (n-1)`），**递归**；负数也成立 |
| `parity` | **跨函数调用**（`popcount`）+ 位与 |
| `rotl16` / `rotr16` | 移位与或拼一个表达式、按 16 位环回；后者**跨函数调用**前者 |
| `reverse_bits16` | **递归**：取最低位拼到结果高位 |
| `gray_encode` / `gray_decode` | 异或移位；解码是**递归**的展开式 |
| `is_pow2` | 两条**提前 `return`** |
| `lowbit` | `x & -x`（先收到 16 位，负号不撞 i32 最小值） |
| `collatz_steps` | **递归**，两条分支各一次自调用 |
| `pow_mod` | **递归** + 平方（`base`/`half` 都 < m） |
| `digit_sum_rec` | **递归**取余整除 |
| `luhn_sum` | 隔位翻倍、超 9 减 9；**递归**推进 |
| `crc8_like` | **两层嵌套迭代**（字节 × 位）+ 位运算与分支 |
| `adler_like` | 两个累加器取模 65521；**递归**推进 |
| `mix32` | 异或右移 + 乘小常数，两轮（无循环） |
| `checksum_rounds` | 又一个**两层嵌套迭代**（轮数 × 一趟） |
| `entry` | **每一步都参与**最后的取余 —— 任一算法错了退出码就变 |

## 期望值怎么推出来的（**不抄对照组**）

`entry()` 的每一项都收进 0..255，再全加、`% 256`。各中间量：

```
g  = gcd(1071, 462)              = 21
lg = log2_floor(1000000)         = 19          (2^19 = 524288 <= 1e6 < 2^20)
ph = poly_hash(7, 64, 31) % 256  = 189
dj = djb_like(64) % 256          = 101
pc = popcount(1048575)           = 20          (2^20 - 1，全是 1)
pa = parity(1234567890) & 255    = 0
rl = rotl16(4660, 4) % 256       = 65          (0x1234 左环 4 = 0x2341 = 9025; 9025%256 = 65)
rr = rotr16(4660, 4) % 256       = 35          (0x1234 右环 4 = 0x4123 = 16675; 16675%256 = 35)
rb = reverse_bits16(1) / 128     = 256         (0x0001 -> 0x8000 = 32768; 32768/128 = 256)
gd = gray_decode(gray_encode(100))= 100         (编解码往返；100 ^ 50 = 86 -> 解回 100)
p2 = is_pow2(1024)               = 1
lb = lowbit(96)                  = 32          (96 = 0b1100000)
cs = collatz_steps(27)           = 111
pm = pow_mod(3, 13, 10007) % 256 = 138         (3^13 = 1594323; %10007 = 3210; 3210%256 = 138)
ds = digit_sum_rec(987654)       = 39          (9+8+7+6+5+4)
lu = luhn_sum(32) & 255          = 129
cr = crc8_like(32)               = 47
ad = adler_like(32) % 256        = 126
mx = mix32(65535) % 256          = 61
ck = checksum_rounds(1, 4, 16)%256 = 73

21+19+189+101+20+0+65+35+256+100+1+32+111+138+39+129+47+126+61+73
= 1563
1563 % 256 = 27          ⇒  EXPECTED: 27
```

（`ph` / `dj` / `lu` / `cr` / `ad` / `mx` / `ck` 是哈希 / 校验类，手推太长，由 README
同目录下那份从零重写的 Python 逐条算出 —— **不是抄 rustc 或本语言的输出**。）

## 为什么不会溢出

这一门特别容易踩 C 那份的 UB（见上级 README「语料的纪律」）。本文件把每个中间量都界死：

* `poly_hash` / `djb_like` / `checksum_rounds`：`h < MOD = 1000003`，乘数最大 131、251
  ⇒ `h * mult` ≤ 2.5e8 < 2^31-1；
* `djb_like` 的 `h << 5` < 3.2e7；
* `rotl16` 的 `x << s` ≤ 65535 << 15 ≈ 2.1475e9 < 2^31-1；
* `crc8_like` / `reverse_bits16` / `mix32` / `lowbit` 都先把值收到 16 位或 8 位；
* `pow_mod` 底与半值都 < m = 10007 ⇒ 乘积 < 1.0e8；
* `collatz_steps(27)` 轨道最大 9232。

所以三方（rustc / Loment / 独立 Python）逐位相同。

## 跑

```bash
# Loment 侧（在 WSL 里编成 ELF 再跑，读退出码）
python .tmp-recon/runlomt.py loment/examples/multisyntax-projects/tur/01-bitkit/main.lomt

# 对照组：把声明行与 module 行各抹成等长空白，追加 fn main，再 rustc
#   fn main() { std::process::exit(entry() % 256); }
# 判据（六门一起，含 rustc 对照组与 README 里那个 EXPECTED）
python tools/loment_multisyntax_projects_test.py
```
