# Option, Result and `?`

Two generic enums are predefined and need no import:

```rust
enum Option<T> { Some(T), None }
enum Result<T, E> { Ok(T), Err(E) }
```

## Returning a failure without exceptions

```rust
fn divide(a: u32, b: u32) -> Result<u32, u32> {
    if b == 0 {
        return Result::Err(1);
    }
    return Result::Ok(a / b);
}
```

The caller decides what a failure means. There are no exceptions and no `try`/`catch`; a failure is
a value, and the type says so.

## `?` propagates

`?` is only legal in a `let` binding, and only inside a function whose return type is `Result` (or
`Option` for `Option`):

```rust
let v: u32 = divide(10, 2)?;
```

If the expression is `Err`, the function returns that error immediately. If it is `Ok`, the binding
gets the payload. It is the same shape as Rust's `?`, with one restriction: it belongs to a
`let`, not to an arbitrary expression.

## A complete example

```rust,run
module results

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

fn parse_small(v: u32) -> Result<u32, u32> {
    if v == 0 {
        return Result::Err(7);
    }
    return Result::Ok(v * 2);
}

fn twice(v: u32) -> Result<u32, u32> {
    let x: u32 = parse_small(v)?;
    return Result::Ok(x + 1);
}

fn unwrap_or(r: Result<u32, u32>, d: u32) -> u32 {
    match r {
        Result::Ok(v) => { return v; }
        Result::Err(e) => { return d; }
    }
}

fn _start() {
    write_dec(1, unwrap_or(twice(3), 0));
    write_str(1, " ");
    write_dec(1, unwrap_or(twice(0), 99));
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 7 99 -->

`twice(3)` is `Ok(7)`; `twice(0)` propagates the `Err(7)` from `parse_small` without unwrapping it,
and `unwrap_or` turns it into the default `99`.

## Matching on a result

The match that turns a `Result` into a plain value belongs in a function of its own (as
`unwrap_or` above), so the enum is a parameter. Keep payload bindings named differently from the
surrounding locals; they are ordinary locals and the language has function scope.

## When to use which

- `Option<T>` — "there may be nothing here": lookup, search, an empty slot.
- `Result<T, E>` — "this can fail, and the failure has a reason": parsing, I/O, anything whose
  caller must decide.
- A bool is not an error channel. If the failure needs an explanation, use `Result`.
