# Hello, world

The first chapter printed a fixed string. A program that can only print one string is not a
language, so this chapter adds numbers — and in doing so shows why Loment has no `printf`.

## Values and types

Every `let` carries a type:

```rust
let count: u32 = 42;
let big: u64 = 5000000000;
let signed: i32 = -7;
let flag: bool = true;
let name: str = "Loment";
let p: ptr = alloc(16);
let a: [u32; 3] = [1, 2, 3];
```

| Type | What it holds |
|---|---|
| `u8 u16 u32 u64 i8 i16 i32 i64` | integers, unsigned and signed |
| `bool` | `true` / `false` |
| `str` | a pointer plus a byte length |
| `ptr` | a raw address |
| `[T; N]` | a fixed-size array |
| `[T]` / `mut [T]` | a slice parameter (a borrowed view) |
| a struct or enum name | your own type |

There are no floats. If you need fractional values, scale them into integers (`314` for `3.14`) or
keep them on the other side of an `extern fn` boundary.

Conversions never happen by themselves. `x as u64` is the whole story; the two sides must be
integers or a pointer.

## Printing a number

`write` takes bytes. To print `42` you have to produce the two bytes `4` and `2` yourself. The
standard shape, used all over this project, is: allocate a small buffer, fill it from the end, then
write it.

```rust,run
module numbers

choose runtime

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    // the digits came out backwards; reverse them in place
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}

fn _start() {
    write_dec(1, 42);
    write_str(1, " ");
    write_dec(1, 7);
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 42 7 -->

Read the loop: `let` declares, assignment updates, `while` loops, `%` and `/` are integer
operators, `as u8` converts, and `alloc` hands you a pointer into the program's heap.

## Arithmetic and comparison

```rust
let a: u32 = 17;
let b: u32 = 5;
let q: u32 = a / b;        // 3, integer division truncates
let r: u32 = a % b;        // 2
let ok: bool = a > b && b != 0;
```

Operators, in precedence order as in Rust: `|| && == != < <= > >= | ^ & << >> + - * / %`, with
unary `-` and `!`.

Two rules that surprise people:

- **Both sides of an operator must have the same width.** `u32 + u64` is not “promoted”; it is a
  diagnostic. Convert explicitly.
- **A shift count must have the same width as the shifted value** in the current backend; write
  `x >> (n as u32)` rather than mixing `u64` and `u32`.

## Comments

```rust
// line comment
/* block comment */
/// doc comment — `loment doc` extracts these
```

## What about `main`?

You may see `fn main() -> i32` in examples. `main` is an ordinary function name; it is **not** an
entry point. A program that must run from a process entry point uses `_start`. `loment run` starts
at `_start`; a unit with only `main` has no entry and cannot be executed by itself.
