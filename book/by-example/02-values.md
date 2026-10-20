# Values

`let` always carries a type. Conversions are explicit `as`.

```rust,run
module be_values

choose runtime

use "helpers.lomt"

fn _start() {
    let a: u32 = 17;
    let b: u32 = 5;
    dec(a / b);
    out(" ");
    dec(a % b);
    out(" ");
    let big: u64 = (a as u64) * (1000000000 as u64);
    dec((big / 1000000000 as u64) as u32);
    out(" ");
    let neg: i32 = -7;
    dec((-neg) as u32);
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 3 2 17 7 -->

- Integer division truncates; `%` is the remainder.
- `a as u64` widens; the other side must be the same width for operators.
- `-neg` negates a signed value; `as u32` reinterprets it.
