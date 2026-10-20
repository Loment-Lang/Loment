# Generics and traits

## Generic functions

```rust
fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

fn _start() {
    let m: u32 = max_of(7 as u32, 9 as u32);
}
```

A generic function is **monomorphised**: the compiler emits one copy per concrete type it is
called with, and the call is direct. There is no runtime dispatch cost and no type erasure.

Call sites need concrete types. Untyped literals are not enough — write `max_of(7 as u32, 9 as u32)`,
not `max_of(7, 9)`.

## Traits

```rust
trait Measurable {
    fn measure(self) -> u32;
}

struct Small {
    v: u32,
}

struct Big {
    v: u32,
}

impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}

impl Measurable for Big {
    fn measure(self) -> u32 {
        return self.v * 10;
    }
}
```

A method call is resolved from the **receiver's type** at compile time:

```rust,run
module traits

choose runtime

trait Measurable {
    fn measure(self) -> u32;
}

struct Small {
    v: u32,
}

struct Big {
    v: u32,
}

impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}

impl Measurable for Big {
    fn measure(self) -> u32 {
        return self.v * 10;
    }
}

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
    let s: Small = Small { v: 7 };
    let b: Big = Big { v: 7 };
    write_dec(1, s.measure());
    write_str(1, " ");
    write_dec(1, b.measure());
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 7 70 -->

`s.measure()` calls `Small`'s implementation; `b.measure()` calls `Big`'s. Both are direct calls in
the product.

## What is not here

- **No trait objects.** There is no `dyn Trait`; a trait is a way to name a set of methods, not a
  value you can store. Write a `match` over an enum when you need a closed set of alternatives.
- **No generic structs in working order yet.** A struct whose field type is a type parameter is
  accepted by the checker but the emitter reads its fields with the wrong width today. Use generic
  *functions* and plain structs; keep aggregates concrete.
- **No generic calling generic.** A generic function whose body calls another generic function
  currently compiles to the wrong callee. Keep the generic layer one deep, or write the inner call
  concretely.

These are current limits, not design positions; each is tracked in the issue tracker.
