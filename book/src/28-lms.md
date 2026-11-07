# LMS: memory safety levels

LMS (Loment Memory Safety) answers a different question from the collector. `gc` decides **when**
memory is reclaimed; LMS decides **who may touch it** and what happens when a touch is illegal.
The two are orthogonal: every LMS level is either a compile-time check or an out-of-process
mechanism, so `no_runtime` and `lms_full` can hold at once.

## Seven levels

Each level is a fallback for the one above it:

| Level | Name | What it does | When |
|---|---|---|---|
| 1 | checker | E028 for a literal index out of bounds; E029 for use after free | compile time |
| 2 | manager | prints the unit's memory map: every `alloc` site with unit, function, line and size | compile time |
| 3 | guard | inline bound check on `load8` / `store8` from a direct `alloc(<const>)` pointer | runtime, instructions only |
| 4 | reject | an out-of-range access is skipped: a load returns 0, a store does nothing, execution continues | runtime |
| 5 | crash | an out-of-range access traps; the process dies with SIGILL (exit 132) | runtime |
| 6 | restart | an out-of-process restarter restarts the process, up to a limit | out of process |
| 7 | seed | the bootstrap seed and its name, byte size and hash: the trusted floor is measured | out of process |

Level 1 is always active for E028. E029 (use after free) is active with the checker switch.

## The four official tiers

The tier is written as a `choose` value. The names are the four supported combinations:

```rust
choose lms_full               // all seven (default)
choose lms_forensic_admin     // 1 + 2 + 3 + 4
choose lms_daily_admin        // 1 + 3 + 4
choose lms_manage_only_admin  // 2
```

The direction is reversed: the default is **full**, and writing any other tier is a downgrade.
Every non-full value carries the `_admin` suffix, which marks it as an administrative decision.
The compiler checks the naming rule (`LMS_TIERS`) and refuses a made-up combination (E22).

## Seven fine switches

Behind the tiers are seven switches: `lms_check`, `lms_manage`, `lms_guard`, `lms_reject`,
`lms_crash`, `lms_restart` and `lms_seed`. A tier seeds them, and an explicit switch can override
one. They are defined by the compiler; they are not `set choose` definitions you write.

## What the guard level checks

Level 3 checks a load or store made through a **direct** pointer from `let p: ptr = alloc(<const>)`:

```rust
let p: ptr = alloc(16);
store8(p, 15, 1 as u8);      // checked
store8(p, 16, 1 as u8);      // out of range: rejected or trapped by level 4/5
```

It does not check:

- a pointer derived with `ptr_add` or `ptr_sub`;
- a slice index;
- a pointer stored in a struct field;
- a pointer received from a system call.

The conservative choice is deliberate: deriving a bound from a pointer's block would mean reading
allocator metadata as if it were a length. The checker does less and stays sound about what it
does.

## Reject and crash

Levels 4 and 5 define the response to a failed check:

- **reject:** the load yields 0 and the store is skipped. The program continues.
- **crash:** the process traps with SIGILL and exits 132. The OS reaps it.

The default `lms_full` includes both levels; the driver picks the response. Under `no_std`, level
5 can only end its own process — there is no way to signal another one — and the restarter at
level 6 is the parent that observes the exit.

## Budgets

Potato v17 records `lms_levels`, seven booleans. v18 adds `lms_budgets`, one budget per level:
`none`, `instructions`, `state` or `outside`. A level that needs a run-time state table changes
its budget to `state`, and `no_runtime` is then refused by name. That keeps "no runtime" honest:
the artifact never quietly gains a table it promised not to carry.

## A checked program

```rust,run
module lms

choose runtime

fn main() {
    let p: ptr = alloc(16);
    store8(p, 0, 7 as u8);
    store8(p, 15, 9 as u8);
    print_num(load8(p, 0));
    print(" ");
    print_num(load8(p, 15));
    print("\n");
    free(p);
}
```
<!-- expect: 7 9 -->

Both accesses are inside the block, so every tier accepts the program. Change `15` to `16` and the
default tier rejects the access (the load returns 0 or the process traps, depending on the
response level).

## What LMS does not do

- It does not check derived pointers or slices at level 3.
- It does not rebuild data from a shadow copy.
- It does not make a raw pointer safe; it adds a check where the compiler can see the allocation.
- It does not replace the collector; it is the permission half of memory safety.

The next chapter covers the dimensions that select these behaviours.
