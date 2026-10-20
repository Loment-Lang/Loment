# Common concepts

## Variables

```rust
let count: u32 = 0;
count = count + 1;        // assignment is allowed; there is no `mut`
```

`let` declares and **requires a type**. A declared name may be assigned again unless it is
captured by a slice borrow (next chapter). There is no shadowing keyword and no `mut`.

Constants live at module level and must be initialised with a literal:

```rust
const LIMIT: u32 = 3;
```

## Functions

```rust
fn total(a: u32, b: u32) -> u32 {
    return a + b;
}

fn log_line(msg: str) {
    syscall4(1, 1, str_ptr(msg) as u64, str_len(msg) as u64);
}
```

- Every parameter carries a type.
- `-> T` is the return type; a function without it returns nothing, and its `return` takes no value.
- **`return` always takes a value** — there is no bare `return;`. To leave a `()` function early,
  put the rest in an `if`.
- At most **10 parameters**. A call with more arguments than the table holds is a hard limit of the
  self-hosted compiler; keep signatures small.
- A non-`()` function must return on every path (E027). Falling off the end is not "return zero";
  it is a trap instruction.

## Control flow

```rust
if a > b {
    // ...
} else if a == b {
    // ...
} else {
    // ...
}

while i < n {
    i = i + 1;
}

for i in 0..n {
    // i runs 0 .. n-1; the upper bound is exclusive
}
```

**There is no `break` and no `continue`.** A loop ends when its condition says so, or when the
enclosing function returns. The project's own tools are written this way; a "search and stop"
loop carries a flag:

```rust
let found: u32 = 0;
let i: u32 = 0;
while i < n && found == 0 {
    if xs[i] == want {
        found = 1;
    }
    i = i + 1;
}
```

## Scope is the function, not the block

This is the first place where Rust intuition is wrong. Loment has **function scope**: a `let`
inside an `if` or a `for` is the same name in the whole function, and it is still there after the
block ends.

```rust,run
module scope

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
    let x: u32 = 1;
    if true {
        let x: u32 = 2;
        write_dec(1, x);
        write_str(1, " ");
    }
    write_dec(1, x);          // still 2: the inner `let` reused the name
    write_str(1, " ");
    let i: u32 = 100;
    for i in 0..3 {
    }
    write_dec(1, i);          // 3: the loop variable is the same `i`
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 2 2 3 -->

The lesson: name blocks' variables differently. A loop counter and an outer variable with the same
name are one variable; the loop leaves it at the upper bound.

## Expressions and operators

Conditions must be `bool`; there is no truthiness.

```rust
if a > 0 && b != 0 { ... }     // && and || short-circuit
let ok: bool = !flag;
```

Bitwise and shift operators exist: `& | ^ << >>`. Both operands of a binary operator must have
the same integer width; convert explicitly.

## A note on printing

You have now written `write_dec` three times. That is not an accident of this book: the language
deliberately has no formatting layer in the sealed profile, and every program grows a small output
helper. The standard library ships one you can use instead — see
[arrays, slices and text](09-arrays-slices-text.md).
