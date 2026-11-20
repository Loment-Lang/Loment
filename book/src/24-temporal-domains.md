# Temporal domains

A temporal domain is a **sequence of general-domain instances**. Each instance has a shape, a
polarity and a lifetime; `guard` checks the position against the instance that is current at the
moment of the call.

The model has one clock and eight laws. The clock is virtual, the laws are small, and every one
of them reduces to a counter and a comparison.

## The virtual clock

Time enters the language through one statement:

```rust
tick;
```

`tick;` advances a program-wide counter by one. The counter starts at zero, and every temporal
law reads it. Two consequences make temporal domains testable in the same way as any other
program:

- The clock is deterministic. The same source with the same tick statements produces the same
  sequence of instances on every run.
- The clock has no timers and no threads behind it. There is nothing to schedule and nothing to
  race.

The sequence of instances is driven by the program's own tick statements. Today the language
advances the clock only at an explicit `tick;`; a loop back edge does not tick on its own. Write
the tick where the domain should move.

Each domain also has a **time origin**: the tick at which its current instance began. The
difference between the current tick and the origin is the instance's age.

## Renewal laws

Four laws keep a fixed-width shape and differ in how the instance moves.

| Law | Syntax | Instance | Passes when |
|---|---|---|---|
| epoch | `epoch(N)` | one instance, lifetime N | `now < N` and the position is in the shape |
| far | `far(N)` | a segment of width N, advancing every N ticks | `lo + (now / N) * N <= x < lo + (now / N) * N + N` |
| sliding | `sliding(N)` | a window ending at the current tick | `max(0, now - N) <= x <= now` |
| cyclic | `cyclic(P)` | the shape repeats with period P | `x % P` is inside the shape |

An **epoch** domain is valid for a fixed number of ticks and then closes permanently. It fits
one-shot budgets and startup phases.

A **far** domain moves in segments. The origin jumps to the end of the current segment when the
segment is exhausted, and a position is admitted only while its own segment is current.

A **sliding** domain follows the clock continuously: the last N positions remain reachable, and
positions older than N ticks drop out. The declared bounds do not narrow this window further
today; the width N is the whole rule.

A **cyclic** domain wraps positions around a period. It is a pure function of the position and P,
so it does not consult the clock at all.

```rust,run
module renewal

choose runtime

capability segment : slot[0..()] far(2)
capability window  : trace[0..()] sliding(3)
capability ring    : gpu[0..15] cyclic(16)

fn main() {
    guard segment(1);
    guard window(0);
    guard ring(20);
    tick;
    tick;
    guard segment(3);
    guard window(2);
    guard ring(20);
    print("renewed\n");
}
```
<!-- expect: renewed -->

After two ticks, `segment` has moved from `[0..2)` to `[2..4)`; `window` has moved from `[0..0]`
to `[0..2]`; and `ring` gives the same answer at both ticks because it does not read the clock.

An epoch domain closes on schedule:

```rust,run
module epoch_domain

choose runtime

capability pass : quota[0..4] epoch(3)

fn main() {
    guard pass(2);
    tick;
    guard pass(2);
    tick;
    guard pass(2);
    print("the epoch is still open\n");
}
```
<!-- expect: the epoch is still open -->

A third `tick;` closes the domain. Every later guard on `pass` traps, because the epoch has no
renewal path.

## Width laws

Two laws keep the origin and change the width of the shape.

| Law | Syntax | Passes when |
|---|---|---|
| growing | `growing(N)` | `x <= lo + N * now`, and the position is in the shape |
| decaying | `decaying(N)` | `now <= N`, and the position is in the shape |

A **growing** domain starts at its lower bound and adds N positions per tick. It describes a
budget that fills over time.

A **decaying** domain stays fixed until its lifetime runs out and then closes. It describes a
lease or a deadline measured in ticks.

```rust,run
module width

choose runtime

capability budget : quota[0..()] growing(2)
capability lease  : auth[0..8] decaying(4)

fn main() {
    guard budget(0);
    guard lease(8);
    tick;
    tick;
    guard budget(4);
    guard lease(8);
    print("budget grew, lease is still alive\n");
}
```
<!-- expect: budget grew, lease is still alive -->

At tick zero the growing domain admits only its lower bound. Two ticks later it admits everything
up to `0 + 2 * 2 = 4`. The decaying domain admits its whole shape until tick four, after which
the lease expires.

## Feedback and generations

Two laws change something other than the origin or the width.

An **adaptive** domain scales its width by a quality value:

```rust
capability trust : auth[0..99] adaptive

quality trust = 128;          // 0..255
guard trust(49);              // allowed: 0 + (99 * 128) / 255 = 49
```

`quality` writes the current quality of one domain. The upper bound becomes
`lo + (hi - lo) * quality / 255`, so a quality of 255 restores the declared shape and a quality
of 0 leaves only the lower bound. The default is 255.

A **generational** domain keeps its shape and flips its polarity once per generation:

```rust,run
module generations

choose runtime

capability page : buf[0..7] generational(2)

fn main() {
    guard page(3);
    tick;
    tick;
    guard page(11);
    print("generation changed sides\n");
}
```
<!-- expect: generation changed sides -->

Generation zero admits positions inside `[0..7]`. Generation one, after two ticks, admits
positions outside it. Generation two admits the original set again.

This law is the temporal form of a stale-handle check. An older object keeps its index while the
generation advances; a guard that reads the object after the switch lands in the new generation's
denied set. The language currently derives the generation from the tick counter, so a program can
test the rule directly and reproduce it deterministically.

## The rules the compiler enforces

The laws share a small set of static requirements:

- Every law with a parameter requires `0 < N < 2^24`.
- `cyclic(P)` requires `P` to be greater than the shape's upper bound, so that wrapping stays
  inside the declared range.
- `generational`, `adaptive` and `flow` require a bounded shape.
- The literal checks that apply to regular and danger domains also apply to `cyclic`, after the
  position is wrapped by `P`.
- Other laws depend on the clock or on a second argument, so their positions are checked at run
  time.

A temporal domain is still a domain. It carries the same space, shape and polarity, and it is
declared in the same place. The law is an extra word on the declaration:

```rust
capability q     : quota[0..64]    epoch(16)
capability ring  : buf[0..1024]    far(256)
capability trace : log[()..()]     sliding(64)
capability frame : gpu[0..31]      cyclic(1024)
capability heap  : mem[0..()]      generational(512)
capability budg  : credit[0..()]   growing(8)
capability ttl   : lease[0..()]    decaying(30)
capability trust : auth[0..16]     adaptive
```

## Reading a temporal guard

The temporal part of a guard is a small arithmetic question. Read the law, find the current tick,
and compute the instance. The runtime performs the same computation in one function shared by
both backends:

```
guard c(x):
  now = current tick
  instance = shape(c, now) with polarity(c)
  if x not in instance: trap
  audit[c] += 1
```

The chapter's examples use `print` after a sequence of passing guards. When a guard fails, the
program stops at that point; the examples keep every failing case out of the runnable blocks and
describe it in prose.

## Where time meets context

The virtual clock is one source of state. The next chapter introduces four others: how many times
a position has been used, which principal is asking, which evidence is presented, and which
obligations remain open. Those domains share the declaration syntax and the guard call, and they
change the state of the domain when a guard passes.
