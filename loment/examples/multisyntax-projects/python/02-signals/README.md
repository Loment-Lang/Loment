# `python/02-signals` —— 定点信号流水线（Python 拼法）

**用 Python 的拼法写的 Loment。** `signals.lomt` 首行 `choose write grammar python` 说的是
"用 Python 的读法读这份文件"；语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定拼法与
形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

模块暴露 `def entry() -> int:`，返回整个项目的答案（退出码 = `entry() & 255`）。

与 `python/01-numtheory`（纯数论：整除结构、数位）**刻意不重叠**：这一门盯的是
**位模式、状态机、定点算术** —— LFSR、格雷码、CRC-16、饱和/夹取、一阶平滑、3 抽头滤波、
字节打包。两者都真编真跑，都走三方对照。

## 项目做什么

二十三 个函数围着同一条流水线：**一串由 16 位 LFSR 生成的样本**，经过饱和加、定点乘除、
一阶平滑、3 抽头滤波、CRC-16 校验，最后折算成一个数。函数互相调用
（`median3`→`min3`/`max3`、`crc16_stream`→`crc16_step`/`lfsr_step`、`smooth_stream`→`ema`、
`window_energy`→`sx_mul`/`lfsr_step`、`lfsr_advance`→`lfsr_step`），`entry()` 把每个函数的结果
折成一个数 —— **每一步都参与**最后的取余，所以任一算法错了退出码就变。

八 个模块常量（`SCALE` / `WARN` / `ALARM` / `CEILING` / `POLY16` / `SEED` / `MASK16` / `TAPS`）
是刻意留的：它们钉住"模块级全大写常量在函数体里当名字用"，而且**参数名与它们全部不同** ——
理由见文末「撞到的工具链问题」。

## 每个函数钉住什么

| 函数 | 钉住什么 |
|---|---|
| `clamp` | 两条提前 `return`，边界含 `lo` / `hi` |
| `sat_add` | 饱和加：溢出到上限就停住（不靠溢出回绕） |
| `sat_sub` | 饱和减：0 是地板（这一门没有无符号类型） |
| `min3` / `max3` | 两个独立 `if` 依次收窄，**顺序敏感** |
| `median3` | **跨函数调用** + 用和减两端 |
| `abs_diff` | 不借内建（这一门调不到 `alloc` / `str_*` / `abs` 之类） |
| `sx_mul` | 定点乘 `a * b / SCALE`，`//` 两边非负 |
| `sx_div` | 定点除 `a * SCALE / b`，中间值放大到 6400 级 |
| `lerp` | 插值 `a + (b - a) * w / SCALE`，调用方保证 `b >= a`（否则差为负、取整两边不同） |
| `ema` | 一阶平滑：两个权重 `alpha` / `SCALE - alpha` 之和恰是 `SCALE` |
| `wrap16` | 掩码取低 16 位 |
| `lfsr_step` | Galois LFSR 一步：`if` / `else` 两条提前 `return` 的**状态机** |
| `lfsr_advance` | `while` + 跨函数调用推进状态 |
| `gray_enc` | 纯表达式 `x ^ (x >> 1)` |
| `gray_dec` | `while` 里反复异或右移，直到移位结果为 0 |
| `bit_length` | 变量右移 + 累加 |
| `crc16_step` | **逐位** CRC：8 轮，按最高位决定异或 `0x1021`，掩码走常量 |
| `crc16_stream` | **同时推进两个状态**（CRC 寄存器与 LFSR） |
| `convolve3` | `[1 2 1]` 抽头；和一定被 `TAPS` 整除 |
| `classify` | **elif 链**四档 |
| `window_energy` | `while` 里累加平方（每次经 `sx_mul` 缩放） |
| `smooth_stream` | 固定初值 128 的递推，结果只由输入序列与 `alpha` 决定 |
| `pack_pair` / `unpack_hi` / `unpack_lo` | 移位与掩码拼字、取高/低字节 |
| `sum_squares` | `for i in range(1, n + 1)` 降级成 `while` |
| `nested_trace` | **嵌套 `for`**：3x3 邻域里 `i * j` 的最大值 |
| `entry` | 把上面每一个结果折成一个数 |

## 期望值怎么推出来的（**不抄任何输出**）

每一项都是上面那个函数在给定输入下的结果，**每一步都进最后的和**：

```
a  = clamp(300,100,200)          = 200
b  = sat_add(150,100,200)        = 200
c  = sat_sub(9,4)                = 5
d  = median3(7,3,5)              = 5          （排序 3,5,7 取中）
e  = abs_diff(3,9)               = 6
f  = sx_mul(128,128)             = 64         （128*128/256）
g  = sx_div(100,4) % 100         = 0          （100*256/4 = 6400）
h  = lerp(100,200,128)           = 150        （100 + 100*128/256）
i  = ema(100,200,64)             = 125        （200*64 + 100*192 = 32000, /256）
j  = wrap16(0x1FFFF) % 100       = 35         （0x1FFFF & 0xFFFF = 65535）
k  = gray_enc(5)                 = 7          （5 ^ 2）
l  = gray_dec(gray_enc(42))      = 42         （往返恒等）
m  = bit_length(255)             = 8
n  = crc16_stream(8) % 100       = 91
o  = convolve3(10,20,30)         = 20         （(10 + 40 + 30)/4）
p  = classify(500)*10 + classify(1000) = 12   （1*10 + 2）
q  = window_energy(8) % 100      = 55
r  = smooth_stream(8,64) % 100   = 91
s  = pack_pair(3,200) % 100      = 68         （3<<8 | 200 = 968）
t  = unpack_hi(pack_pair(3,200)) = 3
u  = unpack_lo(pack_pair(3,200)) % 100 = 0    （& 255 = 200）
v  = sum_squares(6)              = 91         （1+4+9+16+25+36）
w  = nested_trace(4)             = 9          （最大的 i*j 在 i=j=3）
```

求和：

```
200+200+5+5+6+64+0+150+125+35+7+42+8+91+20+12+55+91+68+3+0+91+9 = 1287
1287 % 256 = 7
```

⇒ **期望值 = 7**：

```
EXPECTED: 7
```

（判据 `loment_multisyntax_projects_test` 从这一行读走期望值 —— 它要的是**写语料的人
独立推出来的**那个数，所以这里不能抄对照组。）

## 三方（三个数必须相等，都过 `& 255`）

| | 值 | 怎么来的 |
|---|---|---|
| control（真 CPython） | **7** | 把模块首行抹成等长空白写成 `.py`，再 `runpy` 跑 `entry()` |
| loment（前门 → lomelf → WSL） | **7** | `python .tmp-probe/verify_signals.py`（前门翻译 → `lomentc` 检查 → LLVM IR → `lomelf` → WSL 跑） |
| expected（独立推的） | **7** | 上面那张逐项表；**另写一遍**的 oracle（能用闭式的用闭式：`sum_squares` 用 `n(n+1)(2n+1)/6`、`nested_trace` 用 `(n-1)²`；LFSR 用位串推进、CRC 用另一套循环写法、`median3` 用 `sorted()`）也逐项一致 |

三个数一致 ⇒ **7 / 7 / 7**。

## 这一门的语义差，以及我怎么绕的

这一份刻意走**同意集**（全非负操作数，CPython 才是个有意义的对照）：

1. **`//` 与 `%` 在负号上两边不一样**（`loment/pytrans/intdiv.py`）。**绕法**：每个 `//`、`%`
   的两个操作数都保证非负 —— `lerp` 由调用方保证 `b >= a`，`sx_*` 的参数是定点正量，
   `lfsr` / `crc` / `gray` 全是位运算与掩码。
2. **`int` 是 i64（回绕）**（`loment/pytrans/overflow.py`）。**绕法**：所有中间值都远小于 2^63 ——
   最大的一处是 `sx_div(100,4)` 的 `100 * 256 = 25600`，以及 `crc16` 里的 16 位量。
3. **`bool` 不是 `int` 的子类；`and` / `or` 返回操作数**（`loment/pytrans/bool_as_int.py`）。
   **绕法**：本模块里**没有布尔参数**（原因见下），`and` / `or` 不出现在条件之外的任何地方。
4. **没有三元 `a if c else b`** —— 用 `if` / `else` 两条 `return` 顶（`clamp`、`sat_sub` 都是）。
5. **没有 `break` / `continue`** —— 所有循环条件正常收尾（`gray_dec` 的 `s != 0`、`lfsr` 的计数器）。
6. **`for i in range(a, b)` 降级成 `while`**，循环结束后 `i == b`（不是 `b - 1`），
   且循环体里不许给 `i` 赋值 —— 本模块两处 `for`（`sum_squares` / `nested_trace`）都不依赖终值。
7. **没有 `~` / `**` / `/`（真除）** —— 取反一律用掩码（`wrap16` 的 `& MASK16`）。
8. **模块级常量必须是整数字面量** —— `POLY16 = 0xB400` 可以，`A = 2` 再 `B = A * 3` **不行**
   （前门会明说"模块级常量的值不是整数字面量"并整份拒掉，全有或全无）。

## 这一份撞到的工具链问题

写这份语料时探出来的三条，都已单独开 issue（**这一笔 PR 不改实现**）：

| # | 问题 | 影响 |
|---|---|---|
| [#216](https://github.com/FujoJTOP/loment/issues/216) | 函数内 `let K` **遮蔽模块 `const K`** 被检查器放行，运行时却**用常量值**（本机实测 `f(3)` 给 10 而不是 8） | **静默算错**，原生 Loment 即可复现 |
| [#217](https://github.com/FujoJTOP/loment/issues/217) | **参数**与模块常量同名时，报的是 `函数 f 参数 K 重复`（E013 —— 其实只有一个参数）；经 Python 前门时行号还指向**生成出来的**那份 | 诊断指向假原因、假位置 |
| [#218](https://github.com/FujoJTOP/loment/issues/218) | `grammar python`：**`bool` 参数调不了** —— 调用点把每个实参都按 `i64` 检查（`tools/pytrans.py:278`）。`pick(True)` 被前端以**说反了**的理由拒；`pick(1)` 过了前端、在生成文件的行上报错 | 文档里写着允许的注解，实际不可用 |

所以本模块**刻意避开了这三条**：没有布尔参数、参数名与常量名互不相同、函数内不写与模块常量
同名的局部。避开不是"绕过"——这三点都是**缺陷**，不是语义差；语料走能跑通的那一档，
缺陷留在 issue 里。

**没撞到的（也就没上报）**：翻译本身在这些形状上一次通过 —— 优先级
（`a | b & c`、`a + b << 1`、`a & b == 1`）、`and` / `or` 短路、嵌套 `for`、`elif` 链、
深递归（200 层）、大模块（60 函数链、120 函数扇出）都逐例与 CPython 对上。
