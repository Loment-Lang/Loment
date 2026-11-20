# Modules

`use` imports a unit. The name form searches the project's `deps/`, then the toolchain store, then
the four built-in roots.

```rust,run
module be_modules

choose runtime

use num

use "helpers.lomt"

fn _start() {
    let buf: ptr = alloc(20);
    let n: u32 = num_to_dec(buf, 123456);
    syscall4(1, 1, buf as u64, n as u64);
    out(" ");
    dec(str_len("abc"));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 123456 3 -->

- `use num` resolves to the toolchain's `num` module (`num_to_dec(buf, v)` returns the length).
- **One name-form `use` per unit today.** A second name-form import is a hard limit; use the path
  form (`use "…/store/std/0.1.0/text.lomt"`) when you need more.
- Do not `use std`: the facade pulls 127 modules into one unit and today's compile is pathological.
