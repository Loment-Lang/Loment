# Generics

A generic function is monomorphised: one copy per concrete type, direct calls.

```rust,run
module be_generics

choose runtime

use "helpers.lomt"

fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

fn _start() {
    let a: u32 = 7;
    let b: u32 = 9;
    dec(max_of(a, b));
    out(" ");
    let x: u64 = 100 as u64;
    let y: u64 = 42 as u64;
    dec(max_of(x, y) as u32);
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 9 100 -->

- Call sites need concrete types; `max_of(7, 9)` with bare literals is not enough.
- Keep generics one deep: a generic function calling another generic function is a known rough
  edge today. Write the inner call concretely.
- Generic **structs** are not working yet; keep aggregates concrete.
