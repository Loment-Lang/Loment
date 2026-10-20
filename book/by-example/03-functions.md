# Functions

Every parameter has a type; `return` always carries a value.

```rust,run
module be_functions

choose runtime

use "helpers.lomt"

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn square(x: u32) -> u32 {
    return x * x;
}

fn banner() {
    out("sum=");
}

fn _start() {
    banner();
    dec(add(square(3), square(4)));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: sum=25 -->

- A function without `-> T` returns nothing and cannot be used as a value.
- There is no bare `return;` — a `()` function simply reaches its closing brace.
