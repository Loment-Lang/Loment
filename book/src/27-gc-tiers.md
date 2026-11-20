# The GC tiers

`choose gc` selects how the artifact reclaims memory. There are three tiers plus a declaration
that says no collector is present. The choice belongs to the entry unit, and it applies to the
whole program.

## The four values

| Value | What it provides | Default |
|---|---|---|
| `gc_manual` | the sealed heap: 64 KiB, bump pointer plus an address-ordered free list | yes |
| `gc_auto` | a larger arena and a tracing collector | no |
| `gc_auto_alpha` | the aggressive tier: adaptive threshold, block epochs, frontier rollback | no |
| `no_gc` | no collector at all | no |

```rust
module app
choose runtime
choose gc_auto
```

A library may not write `choose` (E22). It states what it needs through its source, and the entry
unit decides the artifact's dimensions.

## `gc_manual`

This is the default, and it is what the pointer chapter described. `alloc` takes bytes from the
64 KiB arena (1 MiB in a `hosted` artifact), `free` returns a block, and no collection runs.
Memory returns only when the program calls `free`.

Two properties keep the tier predictable:

- the allocator's state is a bump pointer and a free list, with no background work;
- the number of bytes available is fixed, so exhaustion is deterministic.

A program that allocates per iteration without freeing eventually exhausts the arena. The
process stops; there is no growing heap and no fallback.

## `gc_auto`

The automatic tier adds a tracing collector. The runtime scans the roots, marks reachable
objects and reclaims the rest. The allocation trigger is a budget (32 KiB of allocation in the
current implementation), and the collector runs when the budget is crossed or when the arena
cannot satisfy a request.

The root table holds **1024 slots**. A program with more live roots than the table holds is
refused by name; it never writes past the table.

Automatic collection changes the cost model: an allocation may pause for a collection, and the
pause is not bounded by the allocator's arithmetic. `lgc` (chapter 22) controls when a
collection may start.

```rust,run
module collector

choose runtime
choose gc_auto

fn main() {
    let total: u32 = 0;
    for i in 0..64 {
        let p: ptr = alloc(256);
        store8(p, 0, i as u8);
        total = total + load8(p, 0);
    }
    print_num(total);
    print("\n");
}
```
<!-- expect: 2016 -->

The loop allocates 16 KiB in total, below the default trigger, so it may run without collecting.
Raise the count to see a collection; the output stays the same.

## `gc_auto_alpha`

The alpha tier is the aggressive collector. It uses an adaptive threshold derived from the
program's allocation behaviour, and it has block-epoch behaviour: a collection can roll the
allocation frontier back for a block. That makes it the fastest tier for allocation-heavy
programs and the least predictable for programs that keep raw pointers into the heap across a
collection.

The tier is not the default, and it is not a drop-in replacement for `gc_auto` in a program that
holds interior pointers. Read the design notes before choosing it for a long-lived service.

## `no_gc`

`no_gc` declares that the artifact carries no collector. `alloc` still exists as the sealed
heap; automatic reclamation does not. `lgc` under `no_gc` is E039.

## Contradictions the compiler names

Some dimension pairs cannot both hold. The compiler refuses them by naming the pair and the
reason:

| Pair | Why |
|---|---|
| `gc_auto` + `no_runtime` | automatic reclamation is a runtime component |
| `gc_auto` / `gc_auto_alpha` + `no_std` | the collector needs the host runtime |
| `hosted` + `gc_auto_alpha` | alpha's frontier rollback conflicts with pointers held by a C library |

The diagnostic never says "illegal combination"; it states which two declarations disagree.

## Choosing a tier

| Program | Tier |
|---|---|
| a short filter, a tool, a command | `gc_manual` |
| a long-running program with shared structures | `gc_auto` |
| an allocation-heavy program whose pauses are measured | `gc_auto_alpha`, after testing |
| an artifact that must carry no collector | `no_gc` |

The tier is a property of the artifact, and it is recorded in the Potato form object. A build
that does not read the source can still answer "which collector does this binary carry".

The next chapter covers the other half of memory safety: who may touch a block.
