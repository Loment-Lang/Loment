# `lgc`: collection windows

Tur 2026.3 adds `lgc`. A block can pause collection, or open it with a cooperative time budget.

```rust,run
module be_lgc

choose runtime
choose gc_auto

use "helpers.lomt"

fn main() {
    lgc end {
        let a: ptr = alloc(64);
        store8(a, 0, 1 as u8);
        dec(load8(a, 0));
        nl();
    }
    lgc start(2ms) {
        let b: ptr = alloc(64);
        store8(b, 0, 2 as u8);
        dec(load8(b, 0));
        nl();
    }
}
```
<!-- expect: 1
2 -->

- `lgc end { … }` pauses collection for the block; the state from before the block is restored on
  exit.
- `lgc start(2ms) { … }` allows collection, with a budget that gates whether a new collection may
  start. It does not preempt a collection already running.
- Under `no_gc`, `lgc` is E039.
