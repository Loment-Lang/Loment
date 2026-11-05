---
name: loment-runtime
description: Loment runtime and artifact components - LVM execution tiers T0-T3 (compact form, cache, fusion, limits), the Potato form object v0-v19, hot update, LomentFaster and Lomenter (design only), and DGT (planned). Read when using choose vm, inspecting or validating Potato, packaging hot updates, or asking about optimization plans. Part of the LomentSkill suite; section numbers are global.
---

## 8.3 LVM: execution models

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


**LVM is the fourth reader of the same IR** (the other three are clang, `lomelf` and the Rust
translation). It does not invent a bytecode language or a type system; its instruction set is
exactly the subset `lomelf` already parses, plus execution.

| Tier | What it is | Status |
|---|---|---|
| **T0** | an interpreter over the text IR; parses into tables first, then walks them | landed |
| **T1** | feedback profile: block execution counts, branch/switch edge counts, call-site counts | landed |
| **T2** | baseline compile: decode once before execution, execute table-driven | landed (reference and mirror) |
| **T3** | guard specialization + deoptimization (only B1 constant specialization) | landed (reference) |
| **compact form** | a serialization of the parsed tables (not a second language), with a header saying which form it is | landed (reference) |
| **cache** | a compiled-op cache next to the input, keyed by input size, mtime, content hash and **IR hash**; stale caches are named and refused, never half-used | landed |
| **fusion** | A4 superinstructions: two adjacent pure ops dispatched once (`arith+arith`, `load+arith`, `arith+store`, `icmp+br`) | landed |
| **mirror T2** | the same design written in Loment (`loment/tools/lvm.lomt`), shipped in the package as `loment-lvm` | landed |

What matters when you use it:

- **T4 (generational/concurrent collection) is explicitly out of LVM's scope** (`docs/232`
  §4.4): the VM is a reader of the same IR, not a new runtime with its own collector. The
  tiers stop at T3.
- **`choose vm` only records the dimension today.** The artifact is **byte-identical to
  `no_vm`** and carries no bytecode. The interpreter is a separate tool: `loment-lvm FILE`
  (the packaged binary) or `python tools/lvm.py FILE.lomt` in a checkout.
- **T1 does not record types.** The input is typed LLVM IR, so "this `add i32` saw an i32" is
  not new information. It records only what is statically unavailable: block counts, edge
  counts, call-site targets/counts.
- **The profile never enters Potato.** Potato answers "what the unit is"; a profile answers
  "what happened when it ran". Putting the latter in would make the same source produce
  different objects on two runs and break byte-for-byte determinism.
- **The limits are named** (`docs/263`): 16 MiB VM address space, 64 KiB zero page, 4096-byte
  frames, `STEP_CAP = 50,000,000` instructions **per call**, `MAX_DEPTH = 128` (the mirror uses
  32), and `llvm.trap` is defined as **132** with a site line on stderr
  (`lvm: llvm.trap at <fn>:<block>#<index>`).
- **Do not grade it by wall-clock.** The repo's rule is that gates use deterministic counts
  (decode counts, instruction counts), not ratios; the same workload varies 3–5× across
  machines.

### 8.4 Potato: the artifact object

Potato is **the form object written next to a compiled unit**: "what this unit is", answerable
**without reading the source**. It is not a debug dump and not a profile.

- **Versions are cumulative and only grow**: `v0` … `v19`. Each new required field bumps the
  version; older objects stay valid, and a new version never rewrites an old one.
  Examples of the ladder: `v6` grammar, `v7` boundary, `v8` gc, `v9` runtime, `v10`
  `gc_ladder`, `v11` port, `v12` surfaces, `v13` vm, `v14` net_ladder, `v15` lms, `v16` lspe,
  `v17` lms_levels, `v18` lms_budgets, `v19` five-rung `gc_ladder`.
- **Two validators** (`tools/potato.py` and the Loment twin `loment/tools/lompotato.lomt`)
  check shape and **cross-field self-consistency** - for example `surfaces.total_sites ==
  boundary.syscalls`, `w0+w1+w2+w3 == total_net`, `total_net == surfaces.net`, and the ladder
  sums. They cannot check that the numbers are *semantically right* (that would need the
  source); they can and do catch "two places disagree".
- **Two emitters** (`lomentc.emit_potato` and `loment/selfhost/potato.lomt`) must produce
  **byte-identical** objects for the same input.
- **`--emit-potato PATH`** is a compiler flag; `--emit-llvm` / `--emit-rust` require it (M46:
  the compiler forces the form object out alongside code).
- **What it is for**: the side that does not read the source - a linker, a packager, an audit -
  can answer "does this artifact carry a collector?", "does it open a socket?", "which
  capability domains does it declare?", "which safety tier was it built with?".

### 8.5 Hot update (`docs/280`–`docs/292`)

A **Pre-Release** is a projection of **fix commits only** onto a baseline. It is not "a new
version": features are refused at admission (`projection.line[].kind` must be `"fix"`), and the
manifest records the applied line, the evicted commits with reasons, and the tree hash.

- **Build**: `--profile hotswap` emits an entry gate, a slot table and an epoch; the packaged
  `loment-lsp` is built in that profile. A normal build carries the fixed core but no slot
  table, and `hot_install` returns 15 without touching a byte.
- **Swap**: the candidate (`.lomhot`) is installed into the **slot that is not running**
  (A/B alternating), then the frame chain is rewritten so every live frame continues in the new
  image, and the epoch becomes READY. The entry gate is checked at a **safe point** - the top of
  the LSP event loop.
- **Refusals are named, before anything moves**: digest mismatch RC 7, slot-shape mismatch RC 4,
  frame layout change, call-site id change. A refused candidate leaves the process untouched.
- **The `.data` is not swapped.** Strings and initialised globals keep their old values, so
  "fix a message" is not a hot-update fix; it needs a restart. That is a semantic boundary of
  v1, not a bug.
- **Single-threaded only.** Two slots alternating requires other threads to stop inside the
  SWAPPING window, and there is no language-level stop point or thread count yet.
- **Projection is reproducible**: replaying the same line yields the same tree hash. `status`
  lists the line **in application order**, marks a move to an earlier point as `(rollback)`,
  and prints the `restart required` fixes from the last manifest.
- **Not in the packaged `loment` command list yet**: the tooling is
  `tools/loment_update.py` (`manifest` / `check` / `compare`), `tools/loment_hotart.py`,
  `tools/loment_hotload.py` and `tools/loment_staging.py`.

### 8.6 LomentFaster: three speed tiers (**design, not shipped**)

`docs/298` (2026-11-04) designs **one `choose` dimension with six values**, not three stackable
switches:

```
lomentfaster_C_close    (default)  baseline
lomentfaster_C_open               hand the IR to clang -O3
lomentfaster_f1_close             baseline
lomentfaster_f1_open              Loment's own scalar passes
lomentfaster_f2_close             baseline
lomentfaster_f2_open              f1 + loop optimisation + vectorisation
```

- Writing two values of the dimension is E22; a library may not write it.
- **`_close` is a ratchet**: the three closed values must produce artifacts **byte-identical to
  today**. That is why they are separate spellings - so the source and Potato say which tier
  you explicitly closed.
- C tier needs clang and is **ELF only**; PE is refused, not silently downgraded. If clang is
  missing it says so and falls back to `lomelf`.
- f1 is mem2reg/SSA, register allocation, constant folding + DCE + peephole, and small-function
  inlining; the measured target is clang `-O1`/`-O2` level, i.e. parity with `rustc -O3` on the
  benchmark set. f2 adds LICM, strength reduction, unrolling/rotation and vectorisation.
- **It is not in `CORE_DIMS` yet.** Do not write `choose lomentfaster_*` and expect it to
  compile.

### 8.7 Lomenter: the optimizer with a ledger contract (**design, not shipped**)

`docs/297` (2026-11-03) is the answer to "write our own LLVM", and the answer is **change the
centre**: LLVM's contract is "be faster"; Lomenter's is "**after optimisation, the countable
properties in the artifact still hold**".

- Three readings, one chosen: rewriting an LLVM-scale infrastructure is impossible; borrowing
  LLVM is already available (the `--opt` escape hatch); so the project does the third - its own
  IR, analyses, passes and backends, with the ledger as the acceptance criterion.
- **The hard constraint is byte-for-byte twins.** A conventional optimiser (hash-order
  iteration, pointer keys, profile-driven, floating-point cost models) cannot produce a
  reproducible IR order, so two implementations diverge immediately. The design therefore
  includes an explicit allow/deny list for determinism; that is the most expensive part, more
  than writing passes.
- First wave: 7 analyses, 3 passes, LIR = the existing IR subset plus **facts / effects /
  regions**; MIR is separate because native needs virtual/physical registers while VM needs
  stack slots.
- **Not implemented.** There is no `lomenter` binary and no `docs/297` code path; today the
  optimiser story is `--opt` (clang `-O2`) or the LVM tiers.

### 8.8 DGT: compile-time digraphs (**planned, 0.1.5.2**)

`digraph<K>` is planned as a **compile-time graph class value** with a capability lattice,
layout specialisation and static verification (`#1196` umbrella). It is **not a `choose`
dimension** - it is a value in the type system, not a whole-program promise. Five queries are
the planned surface: nodes, edges, acyclicity, topological order, cuts. Nothing is implemented
today; treat any `digraph` syntax as unavailable.

