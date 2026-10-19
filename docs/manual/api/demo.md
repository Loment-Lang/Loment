# API: `demo`

> 源: `loment/examples/demo.lomt` · 由 lomdoc 生成

## 能力域

| 能力 | 空间 | 区间 | 可撤销 | 说明 |
|---|---|---|---|---|
| `blk_write` | `disk` | [0..4] | 是 |  |

## 出界声明

- network: 本单元不申请任何 net 能力
- usb: 不触碰 USB 子系统

## 常量

| 常量 | 类型 | 值 | 说明 |
|---|---|---|---|
| `MAX_BLKS` | `u32` | 8 |  |

## 类型

### `struct Blk`



| 字段 | 类型 | 说明 |
|---|---|---|
| `off` | `u32` |  |
| `len` | `u32` |  |

## 枚举

### `enum Color`



- `Color::Red`
- `Color::Green`
- `Color::Blue`

### `enum Shape`



- `Shape::Circle` (u32)
- `Shape::Square` (u32)
- `Shape::Empty`

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn fib(n: u32) -> u32`



### `fn gcd(a: u32, b: u32) -> u32`



### `fn popcount(x: u32) -> u32`



### `fn in_domain(off: u32) -> bool`



### `fn blk_end(b: Blk) -> u32`



### `fn mask_low(x: u32, n: u32) -> u32`



### `fn has_flag(flags: u32, bit: u32) -> bool`



### `fn sum_array(xs: [u32; 4]) -> u32`



### `fn fill_incr() -> [u32; 4]`



### `fn color_code(c: Color) -> u32`



### `fn sum_range(n: u32) -> u32`



### `fn quadruple(x: u32) -> u32`



### `fn max_blocks() -> u32`



### `fn shape_area(s: Shape) -> u32`
