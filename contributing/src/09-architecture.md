# Architecture tour

This chapter is a map, not a manual. Everything here has a design record under `docs/`; the point
is to know what the pieces are called and which one to open when something goes wrong.

## The pipeline

```
hello.lomt
   │  lex            tokens
   │  check          diagnostics (E001–E033)
   │  emit           LLVM IR (a linear subset)
   ▼
hello.ll
   │  lomelf         assemble + link, no clang, no libc
   ▼
hello        (ELF on Linux, PE on Windows)
```

`loment ir hello.lomt` stops after the IR; `loment build` runs the linker. The IR is deliberately
plain: functions, allocas, loads and stores, calls, branches, a small set of integer instructions.
`lomelf` turns it into machine code directly — there is no optimizer in this path, which is why a
Loment binary is bigger and slower than the equivalent C compiled with `-O2`. `--opt` hands the IR
to clang when one is installed.

The linker exists twice (`tools/lomelf.py` and `loment/tools/lomelf.lomt`) and the two must agree.
The subset they accept is a hard constraint: no `select`, a single pass over `phi`, and types the
native emitter can encode without a register allocator.

## The language surface

The surface is broader than the book's core syntax:

- the Rust-flavoured base grammar;
- six additional surface grammars (C, C++, Java, C#, Go, Python) translated into L0/L1 units;
- `comefor` / `byuse`, which define a new surface syntax at compile time;
- `natural`, the in-progress native surface.

A unit records which grammar it was written in; the checker and the emitter must agree on what that
grammar means. Divergence between grammars is a named class of defect (`docs/188`).

## Capability domains

A domain is a four-tuple `(space, lo, hi, revocable)`. A unit declares what it may reach; `guard`
traps outside the domain and counts the pass. `excluded` declares what it must not reach.
Capability work is the theme of `0.1.5.2` (`docs/146`).

## The Potato form object

`--emit-potato` writes a **form object**: a JSON description of what the unit is, what it imports,
its capabilities, its layouts, its guards, its chosen dimensions and a body fingerprint. The
package manager, the safety tooling and the VM read it. Its schema is versioned (`potato: "v15"`
today; a v16 ledger is in flight for LSPE).

## The LVM

LVM is a bytecode VM. The interpreter (`lvm IN.ll`) exists and is part of the twin criteria, but
`choose vm` is **closed in release builds** — the dimension is a development switch until the
engine ships. Tiers T0–T3 and a compact encoding live under `loment/`; `0.1.5` is the
stabilisation release.

## Memory

- **The bump heap.** A freestanding program allocates from a small bump heap (64 KiB in the
  introductory programs). Exhausting it aborts; `alloc` has no failure return today.
- **The GC tiers.** `choose gc_manual`, `gc_auto`, `gc_auto_alpha`. Manual is the standard;
  `auto` aims at the union of what other systems do well; `alpha` is the aggressive, automatic-only
  tier. `choose gc_auto` is still an empty promise in places — check the current state before
  relying on it.
- **LMS.** The safe memory manager, a second architecture alongside the GC line: seven levels,
  `docs/247`, milestone `0.1.5.1`.
- **LSPE.** The safety police and its audit ledger: `docs/262`, milestones `0.1.5.3` and
  `0.1.5.4`.

## Packaging and the standard

- `lompi` is the package manager: content-addressed identities, a store, locks and a registry
  client. Its guide ships as `lompi skill`.
- `standard/LLS.md` is the Loment Language Standard: a frame around the language, not a second
  specification. Its version number is the language version.
- The L0 interface (`lom/`) is the contract with the kernel side; see
  [Working agreements](03-working-agreements.md).

## Where to read more

| Topic | Document |
|---|---|
| the language | `docs/143` |
| the native backend | `docs/144` |
| the self-host chain | `docs/150`, `docs/167` |
| capability semantics | `docs/146` |
| the library system | `docs/168` |
| surface grammars | `docs/188` |
| the LVM | `docs/243` |
| LMS | `docs/247` |
| LSPE | `docs/262` |
| the standard | `standard/LLS.md` |
