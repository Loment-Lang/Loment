# Strings

`str` is a pointer and a byte length. Compare with `str_eq`, not `==`.

```rust,run
module be_strings

choose runtime

use "helpers.lomt"

fn _start() {
    let a: str = "loment";
    let b: str = str_concat("lo", "ment");
    dec(str_eq(a, b) as u32);
    out(" ");
    dec(str_len(a));
    out(" ");
    dec(str_byte(a, 0));
    out(" ");
    out(a);
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 1 6 108 loment -->

- `str_eq(a, b)` compares contents; the `==` operator is not defined for `str`.
- `str_len` counts bytes; `str_byte(s, i)` reads one.
- `str_concat` allocates on a 64 KiB heap — keep concatenations small.
