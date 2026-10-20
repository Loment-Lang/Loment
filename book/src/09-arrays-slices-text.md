# Arrays, slices and text

## Arrays

An array is a fixed-size value:

```rust
let xs: [u32; 4] = [1, 2, 3, 4];
xs[0] = 9;
let y: u32 = xs[3];
```

The length is part of the type. There is no `push`, no resize; a growable sequence is a library
type (`use vec`).

## Slices

A slice is a view into an array, passed as `&xs` or `&mut xs`:

```rust
fn sum(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}
```

`slice_len(xs)` is the length; `xs[i]` reads and, for a `mut [T]` parameter, writes. Writing
through a read-only slice is E011.

## Strings

`str` is a pointer plus a byte length. It is not a `String`: there is no growth, no `+`, and no
`==` operator. The builtins are the API:

| Builtin | Meaning |
|---|---|
| `str_len(s)` | byte length |
| `str_byte(s, i)` | the i-th byte |
| `str_eq(a, b)` | **content** comparison |
| `str_concat(a, b)` | concatenation on the compile-time heap (64 KiB total — keep it small) |
| `str_ptr(s)` | the data pointer, for syscalls |

Because the language has no `str ==`, comparisons go through `str_eq`:

```rust,run
module text

choose runtime

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let b: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(b, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(b, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(b, i) as u8;
        let hi: u8 = load8(b, n - 1 - i) as u8;
        store8(b, i, hi);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, b as u64, n as u64);
}

fn _start() {
    let a: str = "loment";
    let c: str = str_concat("lo", "ment");
    write_dec(1, str_eq(a, c) as u32);
    write_str(1, " ");
    write_dec(1, str_len(a));
    write_str(1, " ");
    write_dec(1, str_byte(a, 0));
    write_str(1, "\n");
    let xs: [u32; 4] = [1, 2, 3, 4];
    xs[0] = 9;
    write_dec(1, xs[0] + xs[3]);
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 1 6 108
13 -->

`str_byte(a, 0)` is `108` — the byte value of `l`. Strings are UTF-8 bytes; `str_len` counts
bytes, not characters. For character-aware work, `use utf8` (below).

## Text beyond the builtins

The standard library adds what the builtins deliberately leave out:

- `use text` — searching, splitting, trimming, case conversion;
- `use utf8` — codepoint iteration and validation;
- `use parse` — integers and floats from text;
- `use fmt` / `use numfmt` — formatting.

Two facts about `use` today:

- **One name-form `use` per unit.** To combine modules, use the path form
  (`use "…/store/std/0.1.0/text.lomt"`), which has no such limit.
- **Do not `use std`** — the facade pulls all 127 modules into one unit; today that compile is
  pathologically slow and ends in a signal. Take modules one at a time.
