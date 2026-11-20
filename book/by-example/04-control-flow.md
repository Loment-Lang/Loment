# Control flow

`if`/`else`, `while`, `for`, and the absence of `break`.

```rust,run
module be_flow

choose runtime

use "helpers.lomt"

fn sum_to(n: u32) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 1;
    while i <= n {
        acc = acc + i;
        i = i + 1;
    }
    return acc;
}

fn find(xs: [u32], want: u32) -> u32 {
    let found: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) && found == 0 {
        if xs[i] == want {
            found = i + 1;
        }
        i = i + 1;
    }
    return found;
}

fn _start() {
    dec(sum_to(6));
    out(" ");
    let xs: [u32; 4] = [4, 8, 15, 16];
    dec(find(&xs, 15));
    out(" ");
    let total: u32 = 0;
    for i in 0..4 {
        total = total + i;
    }
    dec(total);
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 21 3 6 -->

- `for i in 0..4` runs `i = 0,1,2,3` (upper bound exclusive).
- No `break`: the search loop carries `found` and puts it in the condition.
- The loop variable is function-scoped; do not reuse the name outside.
