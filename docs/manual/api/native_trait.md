# API: `native_trait`

> 源: `loment/examples/native_trait.lomt` · 由 lomdoc 生成

## 类型

### `struct Small`



| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | `u32` |  |

### `struct Big`



| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | `u32` |  |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## trait

### `trait Measurable`



方法: `measure() -> u32`

## impl

- `impl Measurable for Small`
- `impl Measurable for Big`

## 函数

### `fn Small_measure(__self: Small) -> u32`



### `fn Big_measure(__self: Big) -> u32`



### `fn call_small() -> u32`



### `fn call_big() -> u32`
