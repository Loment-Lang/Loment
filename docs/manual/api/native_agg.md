# API: `native_agg`

> 源: `loment/examples/native_agg.lomt` · 由 lomdoc 生成

## 类型

### `struct Blk`



| 字段 | 类型 | 说明 |
|---|---|---|
| `off` | `u32` |  |
| `len` | `u32` |  |

## 枚举

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

### `fn blk_end() -> u32`



### `fn array_sum() -> u32`



### `fn shape_area(tag: u32, v: u32) -> u32`



### `fn short_circuit(x: u32) -> u32`
