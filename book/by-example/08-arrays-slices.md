# Arrays and slices

An array is a fixed-size value; a slice borrows one for a call.

```rust,run
module be_slices

choose runtime

use "helpers.lomt"

fn sum(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}

fn bump(xs: mut [u32]) {
    xs[0] = xs[0] + 100;
}

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    dec(sum(&a));
    out(" ");
    bump(&mut a);
    dec(a[0]);
    out(" ");
    dec(sum(&a));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 10 101 110 -->

- `&a` borrows read-only; `&mut a` borrows mutable. Writing through a read-only slice is E011.
- `slice_len(xs)` is the length of the view.
