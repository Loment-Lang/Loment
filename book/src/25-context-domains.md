# Context domains

A general domain answers a question about a position. A temporal domain answers the same question
against the current instance of a clock. A **context domain** answers it against a context — the
usage so far, the principal asking, the evidence presented, the edge being taken, or the
obligations still open — and updates that context when the answer is yes.

The formal difference is one line. A general guard is a predicate:

```
ok = D(x)
```

A context guard is a transition:

```
(ok, context') = step(x, context)
```

When the guard passes, the context may change. When it fails, the context stays exactly as it
was, and the program traps. A failing guard leaves no trace that a later retry can observe; the
rule holds for every guard in this chapter.

## Usage: `consumed(N)`

`consumed(N)` gives every position in the shape its own counter. A guard passes while that
position has been used fewer than N times, and increments the counter when it passes.

```rust,run
module usage

choose runtime

capability slot : auth[0..3] consumed(2)

fn main() {
    guard slot(1);
    guard slot(1);
    print("both uses allowed\n");
}
```
<!-- expect: both uses allowed -->

A third guard on position 1 traps. Positions 0, 2 and 3 keep their own counters, so exhausting one
slot does not affect the others.

The rule requires a bounded shape at most 64 positions wide, because each position owns a counter.
The counters live in the artifact's domain table and belong to one domain; a guard on one domain
does not decrement another.

`consumed` is the context law with the fewest moving parts, and the other laws in this chapter
share its shape: a per-domain table, a check, and an update on success.

## Principal: `subject(MASK)`

`subject(MASK)` admits a position only for the subjects named by a 64-bit mask. The guard takes
the subject as a second argument:

```rust,run
module principal

choose runtime

capability lane : auth[0..15] subject(3)

fn main() {
    guard lane(5, 1);
    print("subject 1 may use lane 5\n");
}
```
<!-- expect: subject 1 may use lane 5 -->

The mask `3` has bits 0 and 1 set, so subjects 0 and 1 pass and every other subject traps. The
position still has to lie inside the shape; the subject check is applied in addition to the
bounds check.

The subject number is an argument the caller supplies. The language guarantees the mask test; it
does not make the number unforgeable. That property requires a linear value, which the language
does not have today. Use `subject` where the caller is already trusted to report its identity,
and keep authentication outside the language.

## Evidence: `witnessed(T)`

`witnessed(T)` counts how many times each ticket has been presented. A guard passes while the
ticket's count is below T, and increments that count.

```rust,run
module evidence

choose runtime

capability door : nonce[0..15] witnessed(1)

fn main() {
    guard door(3, 7);
    print("ticket 7 opened the door once\n");
}
```
<!-- expect: ticket 7 opened the door once -->

With `T = 1`, a second guard with ticket 7 traps; ticket 8 starts with its own count and passes.
The rule applies per ticket, so one exhausted ticket leaves the others usable.

The ticket number follows the same boundary as the subject number: the language enforces the
usage limit, and the caller supplies the number.

## Edges: `flow`

A `flow` domain treats the pair `(source, destination)` as the member. Its shape is an interval
over packed edges, where the source occupies the high 16 bits and the destination the low 16:

```rust,run
module edges

choose runtime

capability channel : flow[65538..65540] flow

fn main() {
    guard channel(1, 2);
    guard channel(1, 3);
    print("both declared edges\n");
}
```
<!-- expect: both declared edges -->

`65538` is `(1 << 16) | 2` and `65540` is `(1 << 16) | 4`, so the interval admits the edges
`(1, 2)`, `(1, 3)` and `(1, 4)`. An undeclared edge traps. The domain is a relation over pairs
that happen to be encoded as a single integer, which is why the shape is an interval and the
guard takes two arguments.

Sources and destinations are each 16 bits wide. A program that needs a larger identifier space
needs a different encoding, and today the language offers only this one.

## Obligations: `reachable(K)` and `discharge`

`reachable(K)` makes a position usable only while the domain has no open obligation for it. A
passing guard opens an obligation with a deadline K ticks away; `discharge` closes it.

```rust,run
module obligation

choose runtime

capability lease : auth[0..15] reachable(2)

fn main() {
    guard lease(3);
    discharge lease(3);
    guard lease(3);
    discharge lease(3);
    print("obligations settled\n");
}
```
<!-- expect: obligations settled -->

The first guard registers position 3. The following `discharge` clears it, so the second guard can
register it again. The sequence is the shape of a checked-out resource: take it, use it, return
it.

Three failures trap:

- a guard on a position that already has an open obligation;
- a `discharge` for a position with no open obligation;
- a `discharge` after the deadline has passed.

The deadline is checked whenever the domain is next used, by a guard or a discharge. A program
that never touches the domain again does not observe the expiry.

The domain must be bounded and at most 64 positions wide, because each position owns an
obligation slot.

## The five laws side by side

| Law | Context | Guard call | Update on success |
|---|---|---|---|
| `consumed(N)` | usage counter per position | `guard c(x)` | counter for `x` += 1 |
| `subject(MASK)` | principal mask | `guard c(x, subject)` | none |
| `witnessed(T)` | presentation count per ticket | `guard c(x, ticket)` | ticket count += 1 |
| `flow` | edge `(source, destination)` | `guard c(source, destination)` | none |
| `reachable(K)` | open obligation per position | `guard c(x)` | obligation for `x` = `now + K` |

`consumed` and `reachable` are duals in time: one reads what has already been spent, the other
what must still be returned. `subject` and `witnessed` are checks on an argument. `flow` changes
what counts as a member.

## What the compiler checks

The static rules for context domains are the bounds and argument rules:

- `consumed` and `reachable` require a bounded shape at most 64 positions wide.
- `flow` requires a bounded shape.
- `subject`, `witnessed` and `flow` require a second argument; the other laws reject one.
- `quality` applies to `adaptive`; `discharge` applies to `reachable`.

The remaining behavior happens at run time, in the same guard function that implements the other
two classes. A passing guard updates one table entry; a failing guard updates nothing.

## The boundaries of the guarantee

The context class is honest about what it proves:

- **Subjects and tickets are caller-supplied integers.** The language enforces masks and usage
  limits. An unforgeable token would require linear values, which remain a design item.
- **`flow` is an interval over packed 16-bit edges.** The language checks membership in that
  interval. A general set of edges would require a different IR shape.
- **`reachable` registers a dynamic obligation.** It registers a deadline and enforces it.
  Bounded model checking over a known number of future steps lives in a separate tool, outside
  the compiler.

These boundaries matter when choosing a law. `consumed` enforces a count. `subject` and
`witnessed` enforce a policy over an identity the caller reports. `flow` enforces a declared set
of edges. `reachable` enforces a return deadline. Each one is a precise, testable rule, and none
of them replaces a security boundary outside the language.

## The unit in one view

The three classes share one declaration and one guard call:

| Class | Question the guard asks | State it reads |
|---|---|---|
| general | is the position in the shape? | compile-time constants |
| temporal | is the position in the current instance? | the virtual clock |
| context | does the position pass for this context? | usage, principal, evidence, edges, obligations |

A program can mix all three. The declaration carries the class as a trailing word, and the guard
syntax follows the arguments the law needs. The domain table in the artifact holds the shape,
polarity and law for every declaration, and the runtime carries the counters and tables each law
requires.
