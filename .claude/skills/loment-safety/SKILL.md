---
name: loment-safety
description: Loment's safety stack - capability domains (capability/guard/excluded/revocable), LGC's reclamation ladder and gc tiers, LMS's seven memory-safety levels, and LSPE's audit ledger (obligations, evidence, levels, lspe pass, Potato v16, E034-E038). Read when choosing gc/lms/lspe, debugging memory behavior, or reading a safety ledger. Part of the LomentSkill suite; section numbers are global.
---

## 5. Capability domains and the safety stack

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


```rust
module blk
choose runtime          // `guard` drags the runtime block (E031 otherwise)

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);   // out of range -> trap; in range -> audit count +1
    return slot;
}
```

**`guard` needs `choose runtime`.** A unit that uses `guard` under the default `no_runtime`
is refused with E031 ("guard drags the runtime block") - the audit table is runtime state. The
same is true of anything else that pulls in the runtime block; see `loment-syntax` 2.1.

Semantics: `guard cap(e);` evaluates `e` to an index; outside `[lo, hi]` it **traps** (with no
other effect), inside it increments the audit table. **A literal out of range is a compile-time
error** (E4); a non-literal is a runtime check.

A domain may also carry a **shape**, a **polarity**, or a **time law** (all kinds share one
decision point; `tick;` advances the virtual clock):

| spelling | guard passes when |
|---|---|
| `[a..b]` / `[a..a]` / `[()..b]` / `[a..()]` / `[()..()]` | index in the shape; `()` = that side unbounded |
| `danger` | index **outside** the shape (a literal inside is a compile error) |
| `epoch(N)` / `decaying(T)` / `growing(G)` | `now < N` / `now <= T` / index within the growing end |
| `far(W)` | index in `[origin, origin+W)`, `origin = floor(now/W)*W` |
| `sliding(W)` | index in `[now-W, now]` |
| `cyclic(P)` | `(index % P)` in the shape (`P > hi`) |
| `generational(N)` | the shape holds, but the polarity flips every `N` ticks |
| `adaptive` | width scales with `quality cap = q;` (0..255, default 255) |
| `consumed(N)` | each position passes at most `N` times |
| `subject(MASK)` | `guard cap(x, subj);` - bit `subj` is set in `MASK` |
| `witnessed(T)` | `guard cap(x, ticket);` - that ticket is used fewer than `T` times |
| `flow` | `guard cap(src, dst);` - packed edge `(src << 16 \| dst)` is inside the shape |
| `reachable(K)` | the position is free; reserves it for `K` ticks, `discharge cap(x);` clears it |

`consumed`, `witnessed` and `reachable` keep per-position state, so their domain must be bounded
and at most 64 wide. `subject` / `witnessed` / `flow` take a second guard argument; every other
kind refuses one.

**Scoped domains (`docs/314`).** A `domain` block is the same algebra with a lexical scope and a
lifetime; values can carry a space, and accesses into that space are guarded for you:

```rust
module fill_scope
choose runtime

fn fill(a: [u32; 64] in disk, i: u32) {
    domain buf : disk[0..63] consumed(1) {
        a[i] = 1;        // implicit guard buf(i)
        guard buf(i);    // explicit form; same decision point
    }
    // guard buf(i);     // compile error: buf is out of scope
}
```

**Walking a domain (`docs/315`).** `domain:for` iterates the positions of a domain. The guard is
checked once at the loop head (not once per access), and every back edge advances the virtual
clock:

```rust
module walk_domain
choose runtime

fn sum(a: [u32; 8] in disk) -> u32 {
    domain buf : disk[0..7] consumed(1) {
        domain:for i {
            a[i] = a[i] + 1;   // head guard covers this position; no second guard
        }
    }
    return a[0];
}
```

- `domain:for i` walks the innermost block's domain; `domain:for i in NAME` names one;
  `step N` walks every Nth position, `step -N` walks backwards; `domain:for i, j in A, B`
  walks two same-shaped domains in parallel (one guard each per round).
- Only bounded, orderable domains can be walked: infinite shapes, `danger`, and
  `subject` / `witnessed` / `flow` (they need a second guard argument) are compile errors.
- `choose domain_clock_explicit` turns the automatic back-edge tick off; then only an explicit
  `tick;` advances the clock.

**Trees and graphs (`docs/315`).** Nested blocks are the vertical tree. Two declarations say it
out loud:

```rust
module domain_graph
choose runtime
capability ring  : disk[0..15] adaptive
capability other : mem[16..19]

fn pump(a: [u32; 16] in disk) -> u32 {
    domain outer : disk[0..15] {
        couple outer, ring;   // ⇄ one renewal unit: same position, together
        mutex  ring, other;   // ⊥ shapes must be provably disjoint
        domain leaf : disk[0..3] child_of outer { a[0] = 1; }   // explicit tree edge
    }
    return a[0];
}
```

- `child_of PARENT` requires the parent to be lexically enclosing, the same space, and the child
  shape to sit inside it.
- `couple A, B, …` makes one renewal unit: a guard on any member also (silently) checks the
  others at the same position, and a `quality` write goes to every adaptive member.
- `mutex A, B` is a compile-time promise (finite, non-danger, disjoint shapes); it emits no code.

- `let a: [u32; 64] in disk` / `a: [u32; 64] in disk` (parameter) tags a value's space
  (arrays, slices, pointers only). The tag does not travel through a call - the callee sees a
  plain type.
- Inside a block, an index into a value of that block's space is guarded automatically. Nested
  same-space domains check the whole chain; only the innermost one audits or transfers.
- Stateful kinds (`consumed`, `adaptive`, `witnessed`, `reachable`) get a **fresh instance per
  entry**: the next loop iteration and a recursive call each start from zero.
- A top-level `capability` still means the whole unit; the block form is the local one.

**Honest boundaries.** `guard` constrains **the index** to the domain only; it **is not
authorisation** - it never asks "does the current principal hold this capability", because the
principal-to-capability binding lives on the kernel side. `revocable` is currently just a flag in
the declaration and in the domain-description table.

And the boundary that matters most when you deploy something: **nothing gates the operations that
actually do things**. `syscall4` / `syscall6` are raw builtins, `alloc` / `load8` / `store8` /
`ptr_add` are raw, and FFI links arbitrary C. A program - or a library it `use`s - opens a socket,
reads a file and writes through a raw pointer with **no declaration, no audit entry, and nothing
in the diagnostics that shows it**. `capability` says what a domain is; it does not stop anyone.

### 5.1 LSPE: the audit layer (0.1.5)

**LSPE = Loment Safety Police Espace** (`docs/262`). It is **not a seventh checker**: the
enforcers stay exactly as they are (LGC for lifetime, LMS for legality, `capability` / `guard`
for authority, `runtime` / `port` for the artifact, E006/E007/E012 for ownership). LSPE adds the
two things none of them had: **why this allowance was granted**, and **a record of that
judgement in the artifact**.

> **It audits the compiler's own decisions, not your program and not you.** The signature is
> `questioner(Judgment) -> Derivation | Hole`; there is no "user" and no "defendant program" in
> it. When the compiler cannot prove a hole, it hands the developer a **question** with options,
> a default and consequences - it does not say "your code is wrong".

The **four parts** (`docs/262` §0):

| Part | Where | One line |
|---|---|---|
| **Questioner** | during compilation, **inward** | audits the compiler's own judgement |
| **Counter-asker** | during compilation, **hand-off** | gives a named hole to the developer: options, default, consequence |
| **LSPC (police)** | **before compilation** | posts the residual risk: what you will have to sign for, fix it or not |
| **Ledger** | in the artifact | records who opened it, who closed it, who signed |

The **space** of a unit is `Espace(U) = <Sites, Obligations, Evidence, Questions, Ledger>`. The
boundary of the space is the **site census**: a site outside the census is E035.

#### 5.1.1 The dimension `lspe`

| Value | Meaning | Default |
|---|---|---|
| `lspe_open_admin` | audit on: questioner + counter-asker + LSPC + ledger | **yes** |
| `lspe_close_admin` | **audit off**: skips questioning and the ledger; **every check still runs** | no |

`lspe_close_admin` turns off **the police, not the law**. LMS / LGC / `runtime` / capability
decisions are unchanged; only "why was this allowed" stops being recorded. The value carries
`_admin` because closing a safety system is an administrative act (`docs/262` §10.1).

#### 5.1.2 Sites, obligations, evidence

A **site** is one allowance point - `guard cap(i)`, an array/slice index, a `free`, an
`alloc`-derived `load8`/`store8`, a call. Site ids are **content-addressed**
(`unit:kind:fnv8(text#occurrence)`), not line numbers, so formatting a file or inserting an
unrelated site does not renumber the rest.

The **14 obligations** (`loment/lspe/registry.py`), each naming its semantic owner and the
evidence that may discharge it:

| Obligation | Owner | Accepted evidence |
|---|---|---|
| `InBounds` | docs/146 / E028 | Syntactic, Typed, LocalFlow, Interproc, GlobalInvariant, Assumed |
| `Alive` | docs/210 / E029 | LocalFlow, Interproc, Assumed |
| `InDomain` | docs/146 | Typed, GlobalInvariant, Assumed |
| `Exclusive` | docs/206 / E007 | LocalFlow, Interproc, Assumed |
| `Initialized` | docs/206 / E006 | Syntactic, LocalFlow, Interproc, Assumed |
| `NotDangling` | docs/206 / E012 | LocalFlow, Interproc, Assumed |
| `NoRuntime` | docs/182 / E031 | Axiom, Syntactic, Typed |
| `FreeOwnership` | docs/210 | LocalFlow, Interproc, Assumed |
| `LadderLiability` | docs/210 | Typed, Interproc, GlobalInvariant, Assumed |
| `CapabilityLive` | docs/146 | Typed, GlobalInvariant, Assumed |
| `TypeWellFormed` | docs/158 / docs/147 | Axiom, Typed, Interproc, Assumed |
| `DropHonest` | docs/253 §1.2 | **Assumed only** (the floor) |
| `GuardHolds` | docs/146 | Syntactic, Typed, GlobalInvariant, Assumed |
| `SummaryContract` | docs/210 §7 | Axiom, Interproc, Assumed |

The **eight evidence kinds** are `Axiom`, `Syntactic`, `Typed`, `LocalFlow`, `Interproc`,
`GlobalInvariant`, `Assumed`, `Differential`. They form a finite lattice by inclusion.
**`Differential` is never in an accept set**: the twin proves *agreement*, not *correctness*, so
it can be a gate but can never discharge an obligation. That single rule is why a
two-implementation bug cannot turn the ledger green.

#### 5.1.3 Context levels (`--lspe-level`)

| Level | Sees | Typical proofs | Default |
|---|---|---|---|
| L0 | the site plus constants | literal bounds, type domains | - |
| L1 | the function's CFG | initialisation, liveness, exclusivity, local WF | - |
| L2 | call edges + summaries | cross-function deref-only, free ownership | - |
| L3 | whole-unit fixpoint | loop invariants, cross-block flow | **yes** |
| L4 | cross-unit (library summaries) | library contracts | explicit only |

Lowering the level does **not** change a verdict; it changes how many sites can be proved, and
`floors` goes up. The chosen level is written into the artifact as `lspe.level`, so "bought a
green light with a low level" is visible.

#### 5.1.4 The ledger (Potato **v16**)

`lspe_open_admin` writes one object per unit:

```json
"lspe": {
  "state": "lspe_open_admin",
  "set_by": "default",
  "mode": "full",
  "level": 3,
  "sites": 1234,
  "obligations": 1234,
  "proved": 1200,
  "undecided": 34,
  "deferred": 0,
  "refused": 0,
  "floors": 0,
  "floor_detail": {"assumed": 0, "stale_wf": 0, "budget": 0, "drop_honest": 0, "pass": 0},
  "per_kind": {"InBounds": "400/410", "Alive": "390/400"},
  "passes": [{"id": "unit:pass:…", "line": 12, "end_line": 15, "reason": "caller checked", "sites": 2}],
  "pass_sites": 0,
  "passed_sites": 0,
  "pass_ratio": "0/1234",
  "invariants": {"i1": "ok", "i2": "not_run", "i3": "not_run", "i4": "not_run", "i5": "not_run"},
  "acked": 0
}
```

What is **actually implemented today** (0.1.5, `loment/lspe/`):

- the site census for `guard` / `index` / `bounds` / `free` / `alive` / `init` / `exclusive` /
  `call` (`census.py`, 623 lines, mirrored in `lspe.lomt`);
- the 14-obligation registry and the evidence lattice (`registry.py`);
- a worklist solver whose only current edge is "one obligation per site"; the provenance /
  falsification / cross-examination engines (E1–E4) are **partially** wired;
- the ledger, `per_kind`, `pass_ratio`, and **I1 only** (every site kind is registered). **I2–I5
  report `not_run`**; `deferred` / `refused` / `acked` are 0; `floor_detail` is 0 except `pass`;
- `lspe pass` blocks (below);
- `lspe_close_admin` zeroes the counts but still records `state` and `set_by`.

So the honest summary is: **the ledger exists and is checkable; the full proof search is the
0.1.5.2+ work.** Do not read `"proved"` as "the compiler proved my program safe" - read it as
"this many sites had at least one accepted evidence item at this level".

#### 5.1.5 `lspe pass "reason" { … }`

The minimal, named, recorded bypass for something you know but the compiler cannot prove:

```rust
module lspepass

fn f(a: [u32; 4], i: u32) -> u32 {
    lspe pass "caller checked 0 <= i < 4" {
        let x: u32 = a[i];
    }
    return x;
}
```

- The header and braces are flattened at parse time; the body **is** the surrounding statement
  sequence, not a new scope. A `let` inside is visible outside.
- **The reason is mandatory**: missing or blank is **E38**, a parse-time abort. An anonymous
  bypass is exactly the unlabelled floor this design exists to remove.
- Sites inside are still counted in `sites` / `obligations` / `per_kind`; they get
  `covered_by`, the solver does not prove them, and only `pass_ratio` rises. A bypass cannot
  make the coverage numbers look better.
- It does **not** propagate across calls: a function containing a bypass is excluded from the
  deref-only safe table, so its callers stay UNDECIDED.
- `pass_ratio` is a **ratchet**: on the corpus it may only go down. Adding a bypass requires
  `python tools/lspe_ratchet.py --emit` - that step is the acknowledgement that a site was sold.

#### 5.1.6 Commands and what is not there yet

| Surface | Today |
|---|---|
| `loment lspe state FILE [--lspe-level 0..4]` | **works** (checkout launcher): prints the ledger of one unit |
| `loment lspe why SITE` | prints "lands in 0.1.5.2" - derivation trees are not shipped |
| `loment lspe verify ARTIFACT [SIDECAR]` | same - proof hashes and the `.lspe` sidecar are not shipped |
| `--lspe-mode=full\|quiet\|strict` | designed as a build knob; the driver has the parameter, **the CLI does not expose it yet**, so E034 (undecided under strict) is not reachable from the command line |
| `--lspc=full\|quiet\|strict\|off` | **designed, not implemented**; the LSPC pre-flight sheet does not exist yet |
| `E37` policy lock | reserved; the policy layer does not exist and the code never emits it |

#### 5.1.7 The question space and the five invariants (design; mostly not shipped)

When the questioner cannot close a hole, the design hands over a **question** in `Q001`–`Q012`
(these are *questions*, not errors - they never block unless strict mode upgrades them):

| Code | Asks |
|---|---|
| `Q001` | no source found for this allowance |
| `Q002` | the check is inert (turning it off would not make anything red) |
| `Q003` | the two implementations disagree |
| `Q004` | `TypeWellFormed` was not re-proved after a substitution (`stale_wf`) |
| `Q005` | the step budget ran out |
| `Q006` | the interprocedural depth limit was reached |
| `Q007` | destructor honesty cannot be proved |
| `Q008` | the domain is unknown (a capability does not cover it) |
| `Q009` | aliasing is uncertain |
| `Q010` | `free` ownership is ambiguous |
| `Q011` | a runtime leak |
| `Q012` | an assumption has expired (the source changed) |

The ledger's **five invariants** are how LSPE audits itself. Only **I1** is evaluated today;
**I2–I5 are emitted as `"not_run"`** (`loment/lspe/ledger.py`):

| # | Invariant | Violation | Today |
|---|---|---|---|
| **I1** | every site kind is registered (no ghost, no missing kind) | E35 | **checked** |
| **I2** | every allowance has a Q1 source or an `assumption(id)` label | E34 | not run |
| **I3** | the dimensions do not contradict (`no_runtime` has no runtime site, `gc_manual` has no unrecorded `free`, `sealed` has no hosted call) | E35 | not run |
| **I4** | the floor is countable (`floors == assumed + stale_wf + budget + drop_honest`; no UNDECIDED that is neither answered nor defaulted) | E36 | not run |
| **I5** | twin sameness: the same site gets the same verdict and source from both implementations | E35 | not run |

**What the seven `choose` dimensions do not do**: `lspe` is orthogonal to all of them. It never
changes a verdict, never changes the IR, and two artifacts that differ only in `lspe` are
byte-identical (only the Potato ledger differs).

### 8.1 LGC: reclamation (`docs/210`)

The ladder has **five rungs** - `L0`, `L0.5`, `L1`, `L2`, `L3` - and the `choose` value decides
which of them are attempted. The compiler tries **strongest first**: every `alloc` site is
proved into the highest rung it qualifies for.

| Rung | What the compiler proves | Who reclaims | What it costs |
|---|---|---|---|
| **L0 static** | `let p: ptr = alloc(<integer literal>)` and `p` appears only in that declaration plus as **the whole argument** of a callee whose parameter is non-escaping | the **stack** - it never enters the heap | a larger frame |
| **L0.5 shared frame area** | L0's candidate, but a function has **at least two** such sites; their sizes are summed | one frame area shared by the function | `L05_MAX = 2048` bytes; first-declared wins, the rest stay L0 |
| **L1 last use** | the declaration is at the **function-body top level**, the pointer only passes through to safe parameters, so the last use is countable | the compiler inserts `free` after the last use and before the earliest `return` | a path that the insertion point cannot reach (an early return) **leaks once** - a leak, not an error |
| **L2 epoch** | inside a `while` / `for` **body**, every `alloc` is a `let` whose name never leaves the body | the body is unwound in bulk at its end (pointer rollback, no list, no scan) | **one** allocation escaping the body cancels the whole body's rung |
| **L3 runtime** | nothing above applies | the collector | the collector's full cost |

| `choose` value | L0 | L0.5 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| `gc_manual` (default) | - | - | - | - | **the compiler inserts no reclamation**; a manual `free` still works, the program manages memory |
| `gc_auto` | - | - | - | - | **mark-sweep + precise roots** |
| `gc_auto_alpha` | **yes** | **yes** | **yes** | **yes** | **adaptive + generational** |
| `no_gc` | - | - | - | - | **declares** the artifact carries no collector; the linker verifies it |

Facts that change how you write code:

- **`gc_manual` is byte-for-byte the old behaviour.** Choosing a tier is the only way to get a
  collector; a default must not change an existing program.
- **`free` really reclaims now** (bump pointer + address-ordered free list), but it cannot grow
  past the arena and **does not coalesce**. The arena is 64 KiB sealed / 1 MiB `hosted`.
- **`gc_auto` triggers every 32 KiB of allocation** and its arena is 64 KiB, so a program whose
  **live set exceeds about 32 KB still exhausts**. That is a limit of the tier, not of the
  language: `gc_auto_alpha` raises it to "live set close to the whole arena" by making the
  threshold follow the live set and adding a space trigger. The difference is measurable: a
  44 KB live set exhausts under `gc_auto` and runs under alpha.
- **The collector is not free even when it does nothing**: a `gc_auto` unit that uses `alloc`
  carries about 5.2 KB of collector text; a unit that never allocates does not.
- **Roots are precise, not guessed**: the compiler emits a slot table (`@__loment_roots`) with
  addresses of slots, so a fake pointer never appears on the root stack. The table has 1024
  slots; overflow is refused by name, never silently written past.
- **`gc_auto` is stop-the-world and non-moving.** No incremental/concurrent collector, no
  compaction, no weak references, no finalizers, no TLAB/region. Those are recorded
  non-goals, not backlog items.
- **`no_gc` is a claim checked in the linker.** It says "no collector in the artifact", not "no
  allocation"; the IR carries `; loment-gc: no_gc` and both linkers refuse an artifact that
  contradicts the claim.

**What the artifact records**: Potato `gc` (the tier) and `gc_ladder` (the rung counts). From
**v19** the ladder has **five** components - `l0`, `l0_5`, `l1`, `l2`, `l3`, `total_sites` - and
the self-consistency rule is `l0 + l0_5 + l1 + l2 + l3 == total_sites`. v10–v18 objects keep
four rungs and are still valid; the ladder only grows forward.

### 8.2 LMS: memory safety (`docs/253`, `docs/270`)

`gc` answers **when memory is returned** (inward reachability). `lms` answers **who may touch
it** (outward permission). They are two directions of the same graph, and they are orthogonal:
`no_runtime` + `lms_full` is legal because every LMS level is instructions or out-of-process,
not a runtime component.

**Seven levels**, each a fallback for the one above:

| Level | Name | What it does | When |
|---|---|---|---|
| 1 | **checker** | compile-time E028 (literal index out of bounds) and E029 (use after free) | always (E028), `lms_check` (E029) |
| 2 | **manager** | `python tools/lomentc.py FILE --lms-report PATH` (reference compiler; not on the packaged launcher yet) prints the unit's memory map: every `alloc` site with unit/function/line/size | compile time |
| 3 | **guard** | inline bound check on `load8`/`store8` from `let p: ptr = alloc(<const>)`; **only direct pointers** - `ptr_add`, slices and struct fields are not checked | runtime, instructions only |
| 4 | **reject** | out-of-range access is **skipped** (load returns 0, store does not happen) and the program continues | runtime |
| 5 | **crash** | `llvm.trap` - the process dies with SIGILL (exit 132), the OS reaps it | runtime |
| 6 | **restart** | `restarter/loment-restart.c` (written in C, **outside the process**) restarts it, up to a limit | out of process |
| 7 | **seed** | the bootstrap seed plus its **name, byte size and hash** - the trusted floor is named and measured | out of process |

**The tier names are the four official combinations** and there are no separate `chooseset`
files for them:

```
lms_full               all seven (default, no `_admin`)
lms_forensic_admin     1 + 2 + 3 + 4
lms_daily_admin        1 + 3 + 4
lms_manage_only_admin  2
```

- **The direction is reversed**: the default is full, and writing a tier is a **downgrade**. Any
  non-full value carries `_admin`; the rule is mechanically checked in `tools/lomentc.py`
  (`LMS_TIERS`) and by `loment_lms_test`.
- **Seven fine switches** (`lms_check`, `lms_manage`, `lms_guard`, `lms_reject`, `lms_crash`,
  `lms_restart`, `lms_seed`) are defined by the compiler; a tier seeds them and an explicit
  switch overrides. They are not `set choose` definitions you write.
- **Potato v17** records `lms_levels` (seven booleans, the final result); **v18** adds
  `lms_budgets`, one budget per level (`none` / `instructions` / `state` / `outside`). If a
  level ever needs a runtime state table, its budget changes to `state` and `no_runtime` is
  refused **by name** at that moment - rather than users discovering a table in an artifact
  that promised no runtime.
- **What level 3 B does not do**: it does not check derived pointers, slices or struct fields.
  Conservatively not emitting a check is safer than reading block data as a length. The
  "response" half of level 3 is delegated to levels 4/5; rebuilding from a shadow copy is not
  implemented.
- **Level 5 cannot kill other processes under `no_std`** - only syscalls exist. It exits itself
  and lets the OS reap it; the restarter (level 6) is the parent.
- `no_std` and `no_runtime` are unaffected: LMS introduces no runtime symbol.

