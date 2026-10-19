# API: `tour`

> 源: `loment/examples/tour.lomt` · 由 lomdoc 生成

## 能力域

| 能力 | 空间 | 区间 | 可撤销 | 说明 |
|---|---|---|---|---|
| `slots` | `disk` | [0..4] | 是 |  |

## 常量

| 常量 | 类型 | 值 | 说明 |
|---|---|---|---|
| `LIMIT` | `u32` | 3 |  |

## 类型

### `struct Entry`



| 字段 | 类型 | 说明 |
|---|---|---|
| `cents` | `u32` |  |
| `tax` | `u32` |  |

## 枚举

### `enum Kind`



- `Kind::Small`
- `Kind::Big` (u32)

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn write_str(fd: u64, s: str) -> i64`



### `fn write_dec(fd: u64, v: u32) -> ()`



### `fn max_of(a: T, b: T) -> T`



### `fn total(e: Entry) -> u32`



### `fn score(k: Kind) -> u32`



### `fn sum_slice(xs: [u32]) -> u32`



### `fn guarded(slot: u32) -> u32`



### `fn _start() -> ()`
