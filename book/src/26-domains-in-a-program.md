# Domains in a program

The previous three chapters describe the three classes of capability domain one at a time. This
chapter covers what happens when a unit uses several of them: the `excluded` promise, the audit
counter, the interaction between classes, the boundaries that remain, and a checklist for
designing a set of domains.

## `excluded`

A unit can declare a space it does not use:

```rust
excluded "network: this unit does not open sockets"
```

The argument is a message. A `capability` in the same unit that uses the same space conflicts
with it (E005). The declaration is a promise in the source, next to the code, and the compiler
checks that the unit's domains agree with it.

`excluded` is useful for two cases:

- a library that must not reach a subsystem, where the promise documents the boundary;
- a unit under review, where the absence of a capability in the source is the evidence.

## The audit counter

Every **passing** guard increments the audit counter of its domain. A failing guard traps, and the
program stops; there is no audit entry for a failure because there is no continued execution to
observe.

The counter is per domain, not per position. Two guards on the same domain add two entries. A
guard on a general domain, a temporal domain and a context domain updates three separate
counters.

The audit table lives in the artifact's runtime block. It is the same block that `choose runtime`
asks for; a program with no runtime has no guard and no counter.

## Mixing the three classes

A unit may declare as many domains as it needs, from any class:

```rust
capability window  : disk[0..4]
capability quota   : disk[0..4] epoch(16)
capability session : auth[0..7] consumed(4)
capability edge    : flow[65538..65540] flow
```

Each guard names one domain and uses the arguments that domain's law requires:

```rust
guard window(2);
guard quota(2);
guard session(2);
guard edge(1, 2);
```

The checker enforces the argument count: context laws that need a second value reject a
one-argument guard, and the other laws reject two arguments.

A domain belongs to **one space**. Two domains in the same space can describe overlapping sets;
the language does not compare them automatically. When containment matters, encode it in the
shapes and keep the relationship in one place in the source.

## Domains in a library

A library may declare domains and guard positions. Its declarations are part of its source and
travel with it. A consumer that wants a stronger or weaker policy declares its own domains; the
library's declarations do not merge with the consumer's.

The project's `choose` dimensions are the only settings a library cannot write. A library states
what it needs through its source, and the entry unit chooses the artifact's dimensions.

## What the runtime does and does not enforce

A guard checks a position against a declaration. The operations that act on the position remain
raw:

- a system call is not gated by a domain;
- `alloc`, `load8` and `store8` are not gated;
- an `extern fn` call is not gated.

A domain is a written claim plus a checked position. The binding between a principal and a
capability, and the enforcement of a space at the kernel boundary, are platform responsibilities.
The design notes in `docs/146` and `docs/306` describe the larger contract the mechanism is being
built toward.

`revocable` is a flag in the declaration and in the artifact's domain table. Revocation itself is
performed by the platform, not by the language.

## Current boundaries

These are the limits a program should know before designing around domains:

- **No domain trees or nesting chains.** A domain is declared on its own; `guard` checks that one
  domain.
- **No extension domains.** A set cannot be widened or narrowed at run time; the shape is a
  compile-time constant, and the law is the only dynamic part.
- **No tool domain.** There is no language value that creates, revokes or widens a domain.
- **One space per domain.** A cross-space check is not a guard.
- **No enumeration.** A domain answers "is this position in the set"; it does not list the set.
- **Context tokens are caller-supplied integers.** The mask and usage rules are enforced; the
  identity is not unforgeable.

The temporal and context laws that are implemented cover the language-side half of the design.
The kernel-side primitives (`dom_create`, `dom_bind`, `dom_revoke`, `dom_admit`) remain a separate
contract; chapter 23's boundaries section explains the split.

## A design checklist

When a unit needs domains, answer these questions in order:

1. **What spaces does the unit touch?** Name them. A space that appears through no declaration is
   a sign the program is relying on ambient access.
2. **Which positions are ordinary, and which are special?** A regular window is a general domain.
   A hole or a reserved region is a danger domain.
3. **Does the answer change with time?** If it does, choose a temporal law and decide where ticks
   happen.
4. **Does the answer depend on usage, identity, evidence or obligations?** If it does, choose the
   context law that matches the state.
5. **Can the position be a literal?** If so, the compiler can reject a failing guard before the
   program runs. Prefer the static form where it exists.
6. **What should happen on failure?** A failed guard traps. Design the program so that a trap is a
   defect, and use a `Result` for ordinary recoverable failures.
7. **Which spaces must stay untouched?** Write `excluded` for each one.

## A worked set

A device driver for a small disk might declare:

```rust
capability header : disk[0..3]
capability data   : disk[4..()] 
capability ring   : dma[0..63] cyclic(256)
capability slot   : dma[0..63] consumed(1)
excluded "network: this unit does not open sockets"
```

- `header` is a general domain: four fixed slots.
- `data` is an open-ended general domain: everything after the header.
- `ring` is a temporal domain: the device's ring wraps every 256 ticks.
- `slot` is a context domain: each DMA slot is used once.
- `excluded` records the space the driver never opens.

The set is small enough to read, and every guard in the driver names one of the four. That is the
shape the language is designed for.

The next part moves from the source's claims to the artifact that carries them.
