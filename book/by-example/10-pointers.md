# Pointers

`alloc` returns a `ptr`; `store8`/`load8` read and write bytes; `ptr_add` moves.

```rust,run
module be_pointers

choose runtime

use "helpers.lomt"

fn fill(p: ptr, n: u32, v: u8) {
    let i: u32 = 0;
    while i < n {
        store8(p, i, v);
        i = i + 1;
    }
}

fn checksum(p: ptr, n: u32) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < n {
        acc = acc + (load8(p, i) as u32);
        i = i + 1;
    }
    return acc;
}

fn _start() {
    let p: ptr = alloc(8);
    fill(p, 8, 3 as u8);
    dec(checksum(p, 8));
    out(" ");
    let q: ptr = ptr_add(p, 4);
    store8(q, 0, 9 as u8);
    dec(load8(p, 4));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 24 9 -->

- The default heap is 64 KiB; an allocation that does not fit traps.
- A fresh allocation is not zeroed — write before you read.
- `free` is a placeholder today: it returns 0 and does not reclaim.
