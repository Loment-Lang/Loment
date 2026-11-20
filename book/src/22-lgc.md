# `lgc`: controlling collection time

Tur 2026.3 adds one feature: `lgc`. It gives a program control over **when a collection may
start**, in every GC tier. This chapter covers the statements, the budget, the modes they interact
with, and the cases where the budget cannot be enforced.

## The statements

```rust
lgc end;                 // pause: no collection may start from here on
lgc end { … }            // pause inside the block, restore the previous state on exit
lgc start;               // resume the tier's default state
lgc start { … }          // allow collection inside the block, restore on exit
lgc start(10ms) { … }    // the same, plus a cooperative time budget
```

`lgc end;` and `lgc start;` are explicit state changes. They last until another statement or a
window restores the state. A window saves the state on entry and restores it on exit, including an
early `return`.

## What each tier starts with

| `choose gc` | Initial state | After `lgc end` | Inside `lgc start { … }` |
|---|---|---|---|
| `gc_manual` (default) | collection off | off | on |
| `gc_auto` | collection on | off | on |
| `gc_auto_alpha` | collection on | off | on |
| `no_gc` | — | compile error E039 | compile error E039 |

In `gc_manual`, an `lgc start` window is the only way to run the collector. In the automatic
tiers, `lgc end` is the way to protect a critical section from a collection starting inside it.

`no_gc` means the artifact carries no collector. `lgc` under it is a contradiction, and the
compiler refuses it (E039) instead of degrading at run time.

## A pause and a window

```rust,run
module lgc_demo

choose runtime
choose gc_auto

fn main() {
    lgc end {
        let a: ptr = alloc(32);
        store8(a, 0, 1 as u8);
        print("protected\n");
    }
    lgc start(2ms) {
        let b: ptr = alloc(32);
        store8(b, 0, 2 as u8);
        print("open\n");
    }
    print("done\n");
}
```
<!-- expect: protected
open
done -->

The first block cannot start a collection. The second may, with a two-millisecond budget. On exit
from each block, the state from before the block is restored.

## The budget

`lgc start(<duration>)` accepts a compile-time integer and a unit:

| Unit | Nanoseconds |
|---|---|
| `ns` | 1 |
| `us` | 1,000 |
| `ms` | 1,000,000 |
| `s` | 1,000,000,000 |
| `m` | 60,000,000,000 |

A bare number is nanoseconds. `0` means no deadline: the window is open and no budget is applied.
A variable budget is refused; `lgc start(frame_budget)` is a parse error.

The budget is **cooperative**. It gates whether a collection may **start**:

```
state off            -> no collection
deadline 0           -> no budget, use the tier's trigger
now >= deadline      -> no collection
otherwise            -> use the tier's trigger
```

Once a collection starts, it runs to completion. Stopping a marker halfway would leave the heap's
mark set, free list and allocation frontier inconsistent; that requires an interruptible
collector, which this version does not have. The budget buys "no new collection starts in this
window", and it does not promise that a collection finishes within the duration.

## The clock

The budget reads `clock_gettime(CLOCK_MONOTONIC)`. The behaviour depends on the target:

- **ELF / native:** the real monotonic clock; the budget applies.
- **LVM interpreter:** syscall 228 is forwarded to the host's monotonic clock.
- **PE:** syscall 228 is not in the Windows shim's table, so the runtime reads 0 and the budget
  does not tighten. The window still opens; the tier's normal trigger applies.
- **FujoOS kernel:** `clock_gettime` is a placeholder returning 0 today, with the same effect.

Code that depends on the budget belongs on ELF or the LVM.

## Interaction with `return`

A window is lexical. On `return`, the emitter restores every open window in reverse order before
the `ret`:

```rust
fn work() -> u32 {
    lgc end {
        let p: ptr = alloc(16);
        return load8(p, 0);
    }
}
```

The state is restored whether the function falls through or returns. `break` and `continue` stay
inside the same function, so an enclosing window stays open; the statement only changes control
flow within the window's block.

## What `lgc` does not do

- It does not change the GC tier. `choose gc` remains the tier choice.
- It does not preempt a collection that has already started.
- It does not accept a run-time budget.
- It does not make `no_gc` collect.

## When to use it

Use `lgc end { … }` around a section that must not pause for a collection: a latency-sensitive
loop, a lock-like critical section, or a region that holds a raw pointer across an allocation.
Use `lgc start { … }` in `gc_manual` when a section should reclaim before it continues. Use a
budget when the section can tolerate a collection but not an unbounded one.

The next part returns to capability domains, which use the same runtime block for their tables.
