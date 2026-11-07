# General domains

A capability domain is a claim about a **space**: which positions in that space a unit may
reach. The claim has three parts — a space, a shape inside it, and a polarity — and one
operation, `guard`, which checks a position against the claim at run time.

Domains are the part of Loment with no counterpart in Rust, C or Go. This chapter covers the
general case, where the shape is fixed at compile time and the claim does not change while the
program runs. The two chapters that follow add time and context to the same model.

## Declaring a domain

```rust
capability disk_window : disk[0..4]
```

- `disk` is the **space**. It is a name the program uses for positions that belong together; two
  domains in the same space can be compared, and a position from one space has no meaning in
  another.
- `[0..4]` is the **shape**: an inclusive interval. `0` and `4` are both inside.
- The default polarity is **allow**: a position passes when it is inside the shape.

The declaration sits at module level, next to `const`s and `fn`s. A program may declare as many
domains as it needs.

## The five shapes

Every shape is a connected interval with two ends, and each end is either a number or open. That
gives exactly five cases:

| Shape | Written as | Passes when | Use |
|---|---|---|---|
| point | `[7..7]` | `x == 7` | one reserved slot |
| regular | `[2..4]` | `2 <= x <= 4` | a bounded window |
| prefix | `[()..3]` | `x <= 3` | everything up to a limit |
| suffix | `[5..()]` | `x >= 5` | everything after an offset |
| whole | `[()..()]` | always | the entire space |

`()` marks an open end. A prefix domain in a space with a natural zero such as `disk` is the same
set as `[0..3]`; the spelling matters in spaces whose lower bound is not zero.

Five shapes cover every interval, and no sixth shape is needed. A set with a hole is expressed by
combining a domain with a danger domain, which the next section introduces.

```rust,run
module shapes

choose runtime

capability point   : disk[7..7]
capability regular : disk[2..4]
capability prefix  : disk[()..3]
capability suffix  : disk[5..()]
capability whole   : disk[()..()]

fn main() {
    guard point(7);
    guard regular(3);
    guard prefix(2);
    guard suffix(9);
    guard whole(123);
    print("five shapes\n");
}
```
<!-- expect: five shapes -->

Each `guard` names a domain and evaluates one position against it. A passing guard increments the
domain's audit counter and execution continues.

## What happens when a guard fails

A failed guard **traps**. There is no error value to inspect and no recovery path: the program
stops with a signal. This is deliberate. A guard marks a position the unit claimed it could
reach; reaching outside that claim is a defect in the unit, and the runtime treats it the way it
treats a null dereference.

The check has two stages:

- A **literal** position that fails the shape is rejected by the compiler (E004). A guard that
  can never pass is dead code, and the compiler says so.
- A position that only exists at run time is checked by the emitted guard. Passing it continues;
  failing it traps.

The same rule applies inside a function that receives the position as a parameter. The compiler
sees a value whose range is unknown, and leaves the decision to the runtime check.

## Polarity: allow and deny

A domain written with `danger` passes when the position is **outside** its shape:

```rust
capability reserved : mem[2..4] danger
```

`reserved` admits every index in `mem` except 2, 3 and 4. Two consequences follow directly:

- A literal inside `[2..4]` is a compile-time error, because the guard can never pass.
- A run-time position inside `[2..4]` traps.

```rust,run
module danger_domain

choose runtime

capability reserved : mem[2..4] danger

fn main() {
    guard reserved(9);
    print("9 is outside the reserved window\n");
}
```
<!-- expect: 9 is outside the reserved window -->

Danger domains describe memory or devices that must stay untouched: holes in an address range,
pages handed to a device, registers owned by firmware. The shape names the excluded region, and
the compiler applies the same interval arithmetic as for an allow domain with the comparison
inverted.

The two polarities are duals. `allow(P)` accepts the same positions as `deny(S \ P)`, where `S`
is the whole space, so the emitted check differs by one branch.

## Promising not to touch a space

A unit can also declare a space it does not use at all:

```rust
excluded "network: this unit does not open sockets"
```

`excluded` takes a message. A `capability` in the same unit that uses the same space conflicts
with it (E005). The declaration is a promise in the source, placed next to the code, and the
compiler enforces the consistency of the promise with the domains the unit declares.

## What a guard does

A guard checks one position against one declaration. It records the check in the domain's audit
counter. It does not gate the operations that act on the position:

- `syscall4`, `alloc`, `load8`, `store8`, `ptr_add` and `extern fn` calls are raw. A unit can open
  a socket, read a file or write through a pointer without a declaration and without an audit
  entry.
- A guard does not ask which process holds a capability. The binding between a principal and a
  capability lives on the kernel side.
- `revocable` is a flag carried in the declaration and in the artifact's domain table. Revocation
  itself is performed by the platform.

What the language provides is a written claim, a table the runtime carries, and a checked
position at each point where the program writes `guard`. This chapter describes that mechanism
as it is. The design notes in `docs/146` and `docs/306` record the larger contract the mechanism
is being built toward.

## The shape of the next two chapters

A general domain is fixed. Its shape and polarity are compile-time constants, and the same
position always receives the same answer.

The next chapter adds a clock: a domain whose current instance is chosen by a virtual time. The
chapter after that adds context: a domain whose answer depends on usage, principal, evidence or
an obligation, and whose guard updates that context when it passes.
