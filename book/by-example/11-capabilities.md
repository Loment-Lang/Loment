# Capabilities

A domain declares a space and an index range; `guard` checks an index at run time.

```rust,run
module be_caps

choose runtime

use "helpers.lomt"

capability slots : disk[0..4] revocable

fn guarded(slot: u32) -> u32 {
    guard slots(slot);
    return slot;
}

fn _start() {
    dec(guarded(0));
    out(" ");
    dec(guarded(4));
    out(" ");
    dec(guarded(2));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 0 4 2 -->

- The range is inclusive: `[0..4]` admits 0, 1, 2, 3, 4.
- A literal out of range is a **compile-time** error (E004); a variable is checked at run time and
  **traps** when out of range.
- `guard` is not authorisation and it does not gate syscalls or memory access. It records the
  index check and carries the declaration into the artifact's domain table.
