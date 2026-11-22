---
name: loment-runtime
description: Loment runtime and artifact components - LVM execution tiers T0-T3 plus the LVM-A abstract machine (sw_cap, sw_tag, checkpoint, dismemory, native fault diagnostics), the Potato form object v0-v24, LNotary and replay-trust artifacts, hot update (toolchain generations and the native updater), Flomtc, LomentFaster and Lomenter (design only), and DGT (planned). Read when using choose lvm, inspecting or validating Potato, packaging hot updates, or asking about optimization plans. Part of the LomentSkill suite; section numbers are global.
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
- **`choose lvm` only records the dimension today.** The artifact is **byte-identical to
  `no_lvm`** and carries no bytecode. (The spelling was `vm` / `no_vm` before Potato v24;
  the old spelling is no longer a switch.) The interpreter is a separate tool: `loment-lvm FILE`
  (the packaged binary) or `python tools/lvm.py FILE.lomt` in a checkout.
- **The VM accepts two entry shapes.** `_start` and `command_main(argv, argc)` (the VM
  synthesises argv the way `/proc/self/cmdline` looks); an `interrupt fn` entry and a
  `thread_spawn` target are **refused by name**, not reported as "no `_start`" (`docs/243`
  §3.5).
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
- **Deterministic counters**: `lvm --counters-out PATH` writes instructions / decodes /
  tuple-reads / specializations / deopts. T0 decodes once per executed instruction; T2
  decodes once per function and reads compiled tuples per instruction (`docs/226` §2.2.3).
- **Do not grade it by wall-clock.** The repo's rule is that gates use deterministic counts
  (decode counts, instruction counts), not ratios; the same workload varies 3–5× across
  machines.

### 8.3.1 LVM-A: the abstract machine (not a tier)

LVM-A is **not T4**. It is a second kind of machine inside the T0 interpreter: the program's
*addresses* are removed from the guest's algebra, and the run-time keeps the mapping. It is
built from a small set of primitives, each behind its own switch; **every switch off is
byte-identical to the old T0**.

| Primitive | What it gives | Gate / status |
|---|---|---|
| `sw_cap` | an object table plus a generation per object; pointer use is checked against the table | `--substrate sw_cap` (default `none`) |
| `sw_tag` | a 4-bit tag on each pointer (MTE-like); a dangling use is caught with probability 15/16 and recorded as `no_dangling: floored` | `--substrate sw_tag` |
| checkpoint / resume | a P5 explicit executor with a common snapshot format shared by both implementations (flags 0-3: plain, `sw_tag`, dismemory, dismemory + `sw_tag`) | landed |
| dismemory | `choose lvm_dismemory_true`: no address in memory; a pointer is an opaque body handle (`0xD1` + serial), `ptrtoint` and cross-body arithmetic are F04/F05 faults, and the IR carries `; loment-memory: dismemory` (the native linker refuses that line) | landed (reference T0 + mirror) |
| depth 4 | references become **typed paths** rather than in-body byte offsets | partial (`docs/377`) |
| `syscall4p` / `syscall6p` | a syscall whose third argument is a typed `ptr` slot; the lowering does **not** `ptrtoint` it, so an address-free artifact can still pass a token | landed (`docs/375`) |

Phase 2 added sparse-cell bodies, `--dismemory-shuffle`, dismemory snapshots, and the mirror
`sw_tag` snapshot. **LVM-on-PE** is partial: P0/P1/P2/P4 run (the LVM itself is compiled to PE
and runs natively); P3 (byte-for-byte Windows output) is still planned. PE dispatches 146 of
Linux's syscall numbers and returns `-ENOSYS` for the rest (section 8.10; the authoritative
table is `tools/win_syscall_table.py`).

**Faults speak two layers.** The first line is stable and machine-readable
(`error[F0k]: …`), followed by a Lomenterr-shaped card (site, object, how far out of range,
what to check). In the LVM the exit code is `100 + kind`: F01 = 101 … F06 = 106. Native
artifacts opt into the same detail with `--fault-report` on `lomelf` (**default off**): ELF
stages 0/1/2/2.5 are landed; PE stage 4 is landed in the reference emitter only. An ELF
artifact re-raises the signal after printing (`128 + sig`, so SIGSEGV = 139); a PE artifact
reports a VEH exception code (`0xC0000005` / `0xC000001D`). `choose lms_pangu_true` is a
**user switch** (not a dimension) that sends the native fault ABI with `rc = 101`; the default
emits nothing extra.

**Dissigsegv is a research direction, not a delivered promise** (`docs/356`): the safe subset
still segfaults today (array and slice indices are not checked; `__loment_abort` is
`ud2`/SIGILL). What exists is the LVM's `sw_cap` F01-F03 and the native `--fault-report` site
table; read the design as a plan, not as current behaviour.

### 8.4 Potato: the artifact object

Potato is **the form object written next to a compiled unit**: "what this unit is", answerable
**without reading the source**. It is not a debug dump and not a profile.

- **Versions are cumulative and only grow**: `v0` … `v24` (**current is `v24`**). Each new
  required field bumps the version; older objects stay valid, and a new version never rewrites
  an old one. The ladder: `v6` grammar, `v7` boundary, `v8` gc, `v9` runtime, `v10`
  `gc_ladder`, `v11` port, `v12` surfaces, `v13` vm, `v14` net_ladder, `v15` lms, `v16` lspe,
  `v17` lms_levels, `v18` lms_budgets, `v19` five-rung `gc_ladder`, `v20` `systems` (six
  systems, each with state/detail/cost), `v21` `gc_control` (`lgc` windows/pause/resume),
  `v22` `lifetime`, `v23` `fp`, `v24` the `vm` → `lvm` rename (key and values together; an old
  spelling in a v24 object is an unknown field, not an alias).
- **`lnotary_true` is a user switch, not a Potato top-level field.** A product that wants the
  notary declares `set choose lnotary_true { }` + `choose lnotary_true`; the name is recorded
  in `switches` like any other user switch (section 8.6.2).
- **Three validators** now: `tools/potato.py`, the Loment twin
  `loment/tools/lompotato.lomt`, and the independent native notary
  `loment/tools/lomlnotary.lomt` (section 8.6.2). They check shape and **cross-field
  self-consistency** - for example `surfaces.total_sites == boundary.syscalls`,
  `w0+w1+w2+w3 == total_net`, `total_net == surfaces.net`, and the ladder sums. They cannot
  check that the numbers are *semantically right* (that would need the source); they can and
  do catch "two places disagree".
- **Two emitters** (`lomentc.emit_potato` and `loment/selfhost/potato.lomt`) must produce
  **byte-identical** objects for the same input.
- **`--emit-potato PATH`** is a compiler flag; `--emit-llvm` / `--emit-rust` require it (M46:
  the compiler forces the form object out alongside code).
- **What it is for**: the side that does not read the source - a linker, a packager, an audit -
  can answer "does this artifact carry a collector?", "does it open a socket?", "which
  capability domains does it declare?", "which safety tier was it built with?".

### 8.5 Hot update (`docs/280`–`docs/292`, `docs/368`–`docs/369`)

A **Pre-Release** is a projection of **fix commits only** onto a baseline. It is not "a new
version": features are refused at admission (`projection.line[].kind` must be `"fix"`), and the
manifest records the applied line, the evicted commits with reasons, and the tree hash.

**Toolchain generations (2026-11-21, `docs/368`).** A hot update is no longer only the running
LSP image. The payload `HOTG` v1 can carry `bin/` tools too, and it is installed by a **native
updater** (`bin/loment-hotupdate`) that never participates in a hot update itself - it is the
trust root. Two generation directories alternate and a `current` pointer selects one; the
launcher reads `current` first. The payload is signed with Ed25519 as a whole, and its
`fixed_core` names `release-key.ed25519` in the **running tree**: the SHA-512 of that key must
equal both the baseline and the candidate manifest. The channel shape is
`v<RELEASE>-hot.<N>` (the `-` makes it a pre-release). Commands:

```bash
loment hotupdate --payload FILE --sig FILE      # install
loment hotupdate --payload FILE --sig FILE --check
loment hotupdate --rollback
loment hotupdate --status
```

Older installs (`v0.1.5.1-hot.1`) have no updater, no pinned public key and no admission
surface - `loment hotupdate` does not exist there, which is why a build-time fix (GEP first
index, PE `openat`) needed a full package upgrade until this generation.

**What is still a boundary:** the candidate's `.data` is **not** swapped (strings and
initialised globals keep their old values, so a message fix needs a restart), and the process
must still be single-threaded. The **patch-conservation conjecture** (`docs/369`) - an admitted
update only moves a program from wrong to right - is a **conjecture with no implementation**;
the five digests are a syntactic firewall, not a proof.

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
- **Tooling**: the package exposes `loment hotupdate` (install / `--check` / `--rollback` /
  `--status`); the source repository has the generators `tools/loment_update.py`
  (`manifest` / `check` / `compare`), `tools/loment_hotart.py`, `tools/loment_hotload.py`,
  `tools/loment_hotpack.py` and `tools/loment_staging.py`, plus the workflows that publish a
  `v*-hot.*` channel.

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

**What is shipped instead is `--opt`.** `loment build FILE --opt` hands the IR to clang
(`-O2` by default; the measured profile adds `-O3 -march=native -mllvm
-align-all-functions=6`), **ELF only**, requires clang, and falls back with a message when it
is missing. `--portable` selects generic `-O2` for distribution. The 2026-11-21 race
(`docs/365`) measured the default profile at **5.5–20× C** and `--opt` at **0.6–2.1× C**
(faster than C `-O2` on memory-bound kernels, and faster than Rust `-O` on all five kernels).
Quote the ratios, not absolute milliseconds; `--opt` is not a gate and the default profile
stays byte-for-byte reproducible with no optimizer.

### 8.6.1 Flomtc: the compile-speed accelerator (landed)

Flomtc is **a third implementation of the same front end**, not a new language and not a
runtime. It compiles the same source to the same IR/ELF through a native front end, checker,
IR emitter and linker; anything it does not support **falls back to the reference** and is
recorded as `flomtc_fallback`, and the artifact must be **byte-identical** to the reference
one.

- Its dimension is `flomtc` (`flomtc_false` default / `flomtc_true`). It is a build
  fast-path, **not** a Potato top-level field and not a semantic promise.
- Measured: the reference cold path **2,122 ms → 991 ms**; the self-hosted packaged compiler
  **15.8 s → about 441 ms**. The self-hosted fast path is the default since 2026-11-20; the
  Python side is opened by `choose flomtc_true`.
- An unsupported shape is not a silent difference: it falls back and records the reason.

### 8.6.2 LNotary and replay trust

**LNotary** (`docs/370`) is a **third-party artifact auditor**: it reads Potato, imports zero
toolchain modules (a subprocess check pins that), and proves two narrow things - the artifact
contains **no false accounting** internally, and its sidecar is bound to the artifact bytes.
It does **not** prove the program is safe.

- Status: V0-V2 + V5 landed; V3's first half (bad-block exit `edge` / `kind` + switch
  reconciliation) landed, R4 is not; V4 (rule references R1-R3) landed. The native notary
  `loment/tools/lomlnotary.lomt` matches the Python side on 12 groups.
- It is enabled only when the artifact declares `choose lvm` (the old `choose vm` spelling is
  accepted by `lvm_enabled`; today the check is the literal value `lvm`, so
  `lvm_dismemory_true` is **not** accepted). Otherwise the result is `LNT-VM`.
- A product asks for notarisation with the **user switch** `lnotary_true` (a `set choose`, not
  a dimension); the sidecar carries `schema/potato/artifact{sha256,bytes}/dims/dims_hash/
  laws/sites/genesis/seal`.

**Replay trust** (`docs/376`, V0) makes the bootstrap chain replayable: `provenance.json`
format 2 adds `source_closure`, `genesis_source_closure` and `seed_env`; `bootstrap.sh` checks
`seed.sha256` over the seed before executing genesis and **fails closed** on drift
(`LOMENT_ALLOW_DRIFT=1` is an escape hatch for experiments only). `loment/build/trust.json`
records the root, recipe, trust surface and budget - for the current tree: seed
**7,501,283 bytes**, input cap **8,388,608 bytes**. The enforced criteria are C1-C6, C8 and
C10 (`loment_trust_test`); C5 (cross-clang), C7 (independent oracle), C9 (self-referential
corpus) and C11/C12 (transparency log) are **not** landed.

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

### 8.8 DGT: compile-time digraphs (**planned, 0.1.6**)

`digraph<K>` is planned as a **compile-time graph class value** with a capability lattice,
layout specialisation and static verification (`#1196` umbrella). It is **not a `choose`
dimension** - it is a value in the type system, not a whole-program promise. Five queries are
the planned surface: nodes, edges, acyclicity, topological order, cuts. Nothing is implemented
today; treat any `digraph` syntax as unavailable.

