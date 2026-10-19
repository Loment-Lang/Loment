# API: `native_raii`

> 源: `loment/examples/native_raii.lomt` · 由 lomdoc 生成

## 类型

### `struct Guard`



| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `u32` |  |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## impl

- `impl Drop for Guard`

## 函数

### `fn Guard_drop(__self: Guard) -> ()`



### `fn port_read(port: u16) -> u32`



### `fn port_write(port: u16, v: u8) -> u32`



### `fn make_guard(v: u32) -> u32`
