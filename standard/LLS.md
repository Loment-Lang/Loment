# Loment Language Standard

**LLS** · **0.1.4.6** · Edition 1

> **Status.** The language surface this document defines is **frozen**: changing it costs the
> procedure in §6.2, and nothing else does. What is *not* frozen — the implementations, the
> toolchain, the packaging — is listed in §6.3.
>
> **What it is.** Loment is a systems programming language. It compiles a program to a native
> executable, and it is arranged so that **what a program is made of is a decision the program
> states** — written once, in its root unit, and carried into the artifact as a machine-readable
> record.
>
> **What it is not.** This document is a **frame**, not a second copy of the language. The rules
> themselves live in exactly one place each, and §2 names those places. Where this document and a
> normative reference disagree, **the reference is right** — it is the one copy.

---

## 1. Scope

This standard defines what Loment **is**, for two purposes:

1. **Deciding conformance.** What a conforming implementation must do, what a conforming program
   may say, and how that is decided (§5).
2. **Fixing the surface.** Which parts of the language may change only by a defined procedure, and
   what that procedure is (§6).

It does **not** restate the grammar, the type rules, the diagnostic set, the loading rules or the
capability semantics. Those exist once, elsewhere (§2), and a standard that repeated them would be
the second copy this project spends its discipline on not having.

### 1.1 The one sentence

**Loment is not defined by what it lacks.** Every subsystem the language can have is a **switch**
(§4). A conforming implementation must implement the switches; which switch a *given build channel*
exposes is a product matter, and is deliberately not part of this standard (§8).

---

## 2. Normative references

These are the single sources of truth. Read the file, not a quotation of it.

| What it fixes | Where it lives | Written in |
|---|---|---|
| The L1 language: syntax, type rules, move and borrow rules, semantic checks, `extern fn`, `choose` | `docs/143-l1-loment-v0.md` | Chinese (English twin: `docs/143-l1-loment-v0.en.md`) |
| The L0 interface layer — declarations that generate constants and decoders for other languages | `docs/141-l0-lom-spec.md` | Chinese (English twin: `docs/141-l0-lom-spec.en.md`) |
| Capability-domain semantics: what `guard` guarantees, and what it does not | `docs/146-loment-capability-semantics.md` | Chinese |
| Form objects (Potato): the summary a unit exports | `docs/147-potato-v1-spec.md` | Chinese |
| The frozen surface, and the procedure for changing it | `docs/158-loment-freeze.md` | Chinese |
| The switch set and the contradictory pairs | `tools/lomentc.py` — `CORE_DIMS`, `CORE_DEFAULTS`, `CORE_CONFLICTS` | code |
| The diagnostic set: codes and their fixes | `tools/loment_diag.py` — `RULES` | code |
| The built-in function table | `tools/lomentc.py` — `BUILTINS`, together with `slice_len` | code |
| The version of the language | `tools/loment_release.py` — `RELEASE` | code |
| The generated manual: specifications and a page per example | `docs/manual/` (generated; do not hand-edit) | Chinese |

**Read the source, not this table.** Where a file moves, this table is stale and the file is not.

---

## 3. Terms

**Implementation** — a program that reads Loment source and does one of: produce an artifact,
check it, print its IR, or report on it. `tools/lomentc.py` (the reference) and
`loment/selfhost/` (the self-hosted one) are both implementations; they are peers, and neither is
the definition of the language (§5.2).

**Unit** — one source file, together with every unit it loads.

**Root unit** — the unit the build starts from. The root unit carries the switches (§4.2).

**Artifact** — what an implementation emits. The word is deliberately neutral about form: an
executable, an object, or an object for bare metal.

**Switch** — one of the settings in §4. A program declares each at most once, in its root unit.

**Form object** — the declared, machine-readable summary a unit exports of what it contains and
what it reaches (`docs/147`). The switches are among the things it records.

**Frozen surface** — the parts of the language listed in §6.1. Changing one costs §6.2, and
nothing else does.

**Conformance suite** — the criteria in §5.2. It, and not any prose, decides conformance.

---

## 4. The switches

Loment has **five switches**, called dimensions. Each one selects a property of the *whole
program*, not of a function or a file.

| Dimension | Values | Default | What it selects |
|---|---|---|---|
| `mode` | `std` / `no_std` | `std` | Which layer the program may use: the host's, or the core's alone |
| `gc` | `gc_manual` / `gc_auto` / `gc_auto_alpha` | `gc_manual` | Who reclaims memory: the program, a collector, or a hybrid |
| `runtime` | `runtime` / `no_runtime` | `no_runtime` | Whether the artifact carries a runtime |
| `port` | `sealed` / `hosted` | `sealed` | Whether the artifact has ports to the world |
| `vm` | `vm` / `no_vm` | `no_vm` | Which execution model the program is for |

The set above is **frozen** (§6.1): it is listed here because it will not drift, and a change to it
takes §6.2. The authoritative table is `tools/lomentc.py:CORE_DIMS`. **It is not the whole story** —
§4.5 lists the values that are planned and not yet built.

### 4.1 Invariants

These hold for every dimension, now and for any added later. **An implementation that violates one
does not conform.**

1. **One value per dimension per program.** Writing the same dimension twice is an error, and the
   diagnostic must name *which dimension* — `gc` is not a word a user can be told about.
2. **The root unit carries them, and nothing else does.** A setting written in a library is an
   error, and the diagnostic must name the file it was found in.
3. **Every dimension has a default, and writing nothing gives it.** A default is not a claim about
   the language; it is the position that keeps existing programs behaving as they did. **No change
   to a default may change the behaviour of any program that already compiles.**
4. **Every dimension is recorded in the form object**, so which switches a program set is
   answerable without reading its source.

### 4.2 Value names say *whether*, not *which*

A value name states the presence or absence of a property, never a level or a version of its
contents. `runtime` is `runtime` or `no_runtime` — never `runtime_v2`. `vm` is `vm` or `no_vm` —
never `vm_interpreter`.

This is a rule, not a preference. What sits inside a runtime, or which rung of the execution-model
ladder an implementation has reached, **grows over the years**; a name that encoded today's
contents would have to be renamed to admit tomorrow's — and renaming a value is a change to the
frozen surface. The same reasoning applies to `gc`, where the tiers do have distinct names because
they are selected independently rather than accumulated.

### 4.3 Contradictory pairs

Some pairs of values cannot both be in force. A program that asks for one is refused, and the
diagnostic must **name both values and say why they contradict** — never report an "illegal
combination". The reasons fall into three kinds, and the difference matters, because two of them
are permanent and one is not.

**Definitional.** One value is defined as what the other forbids. No later work opens these:

| Pair | Why |
|---|---|
| `gc_auto` · `no_runtime` | Automatic reclamation **is** the runtime — the root table, the collection points and the collector itself live in it — and `no_runtime` says the artifact carries none. |
| `gc_auto_alpha` · `no_runtime` | The same pair on the hybrid tier, which depends on the runtime more, not less. |
| `no_std` · `hosted` | `no_std` says the core layer alone — nothing underneath. `hosted` says: link downward. One says there is nothing below, the other reaches below. |

**Narrowed.** Refused today because the combination has no criterion behind it yet, and the record
says so, together with the path that would open it. **A narrowed pair is a switch that is currently
off, not one that cannot be turned on** — and the difference is the whole reason the record
distinguishes them.

| Pair | Why it is held shut, and what would open it |
|---|---|
| `no_std` · `gc_auto` | Automatic reclamation needs a runtime, and `no_std` means the core alone — together they would ask the core to carry a collector, which is not "the core stays small". It opens the day someone wants it enough to make it a **constrained subset with its own criterion** (`docs/175` §4). |
| `no_std` · `gc_auto_alpha` | The same, on the tier that depends on the runtime more, not less. |

**A hard boundary with a measured cause.** Not a definition and not a policy — a property found by
building it:

| Pair | Why |
|---|---|
| `gc_auto_alpha` · `hosted` | The hybrid tier reclaims by winding the allocator's front back, which is what buys its stack discipline. A foreign library puts pointers into its own structures — `zlib`'s `z_stream` is the example in the record — and those pointers live outside the program's stack. Winding the front back leaves the C side holding a pointer into a region that has already been given away. |

Note what is **not** contradictory: `gc_manual` with `no_runtime`. "Reclaim memory myself, with a
runtime present" is a position the language must be able to express — a host service or a thread
may be there while the collector is not.

The authoritative table is `tools/lomentc.py:CORE_CONFLICTS`, and each entry carries its reason in
the source. The table may **grow** under §6.2; an implementation may not relax it.

### 4.4 The channel

Which switches a **release build** accepts, as opposed to a development one, is decided by a single
constant — `tools/loment_channel.py:RELEASE`, with its self-hosted twin in
`loment/selfhost/checker.lomt`; a criterion pins the two to the same value. A channel may refuse a
switch whose contents are not yet implemented, on the principle that **a promise that falls through
is worse than a refusal**.

**This standard states no channel behaviour**, deliberately. Which switch is exposed in which
channel is a fact about a build, not about the language, and a standard that restated it would go
stale at the next release. Read the constant.

### 4.5 Planned values

The switch set grows, and a **value may be planned before it is built**. The distinction is the
whole point of this section: **a value that no implementation accepts is a gap in the
implementations, not a rule of the language.** "Not built" and "not allowed" read the same at the
command line and mean opposite things in a standard.

| Value | Dimension | What it declares |
|---|---|---|
| `no_gc` | `gc` | That the artifact carries **no collector**. |

The position behind `no_gc` is already the language's, and is already checkable: **an internal
absence is a first-class claim.** "This artifact has no runtime, no allocator and no collector" is
enforced by the **compiler** rather than probed at run time, and it is **printed into the
artifact** (`docs/229` §4.2). Absence is the degenerate case of composition — a program whose
allocations are all settled before run time ends with an L3 residue of zero, emits no collector,
and its own summary is what says so (`docs/210` §4.5: *the normal case is that there is no
component called GC*).

So the **property exists today, as a consequence**. What is planned is the **declaration**: a `gc`
value that states it, so that "this artifact carries no collector" is a claim the program makes and
the build records, rather than something a reader has to derive from the numbers. That is the same
move the language makes everywhere else — which is why the value is on this list rather than
written off as unreachable.

Two consequences follow:

- A conforming implementation is **not** required to accept `no_gc` today.
- An implementation that refuses it **must not present the refusal as the language forbidding it.**

When `no_gc` is built it joins the table in §4 and takes §6.2 like any other change to the switch
set.

> **The plan has no record of its own.** There is no `docs/NNN` for `no_gc`; this clause is the only
> place the value is written down, and a standard is the wrong place for a plan. The project
> records plans as numbered documents. Until one exists, read this as a note rather than as a
> decision.

---

## 5. Conformance

### 5.1 What a conforming implementation must do

1. **Accept every conforming program**, and reject every program that violates §4.1, §4.2 or §4.3
   — with a diagnostic that names the specific thing that is wrong.
2. **Emit the same bytes as the reference implementation** for the same conforming input. This is
   the project's central criterion: where two implementations disagree, at least one is wrong, and
   the disagreement is the bug report.
3. **Carry the switches into the form object** (§4.1.4).
4. **Declare no conformance it cannot demonstrate** (§5.3).

### 5.2 What decides conformance

**The conformance suite, and nothing else.** Not this document; not the reference implementation;
not a maintainer's reading of either.

```bash
python tools/loment_rule_parity.py    # equivalence on the rule cases, with a ratchet on the budget
python tools/loment_p8_test.py        # zero diagnostics, byte-identical targets, fixed point, driver gates
python tools/lomentc_test.py          # the reference implementation
python tools/loment_tools_test.py     # diagnostic classification, built-in table, incremental cache
python tools/loment_ir_diff.py --all  # byte-comparison of every target (locating tool)
python tools/loment_status.py --check # the status matrix agrees with its record
python tools/loment_release.py --check # every artifact's sha256 agrees
```

and the gate that runs the static set together:

```bash
python tools/ci.py --static-only
```

**No total is quoted in this document.** Every one of those tools prints its own counts on its last
line, and **that line is the number** — the tools state their own totals. A standard that copied
the numbers in would be a second source of truth, and the project has already been bitten by
exactly that: the numbers written into `docs/158` were measured stale on 2026-09-22.

### 5.3 What the suite does not cover

Named here so that a reader does not take "green" for more than it is. These are properties of the
environment, not of the language, and none of them is a licence to widen a criterion. **A
criterion whose prerequisite is missing reports SKIP, not a pass** — so "the suite is green" means
*the criteria that ran* were green, and the tail of each tool's output is what says which ran.

| Not covered | Why |
|---|---|
| The production path that needs **clang** | Absent on some machines; the criterion skips rather than fails |
| The oversized-project corpus in **Java** | Needs `javac`; skips without it |
| **Running** on `aarch64` | The backend emits for it and has never been executed on real hardware or an emulator |
| Two criteria that read a **sibling repository** | Absent in a standalone checkout; red there by construction, not by regression |
| The package manager's store synchronisation | Known red while a packaging change is in flight |
| **External verification** | The suite is written by the same author as the implementations. Two implementations agreeing is evidence that they *agree*, not that they are *right* — and the project says so in `docs/158` §4 |

### 5.4 Precedence

Where this document, an implementation, and the suite disagree, **the suite is right**.

**A red criterion may not be made green by relaxing the criterion.** The only two remedies are:
change the implementation, or change the surface by §6.2. Relaxing a criterion to pass is treated
as concealing a defect, and the ratchet in `loment_rule_parity.py` exists to make it visible.

---

## 6. Stability and change

### 6.1 The frozen surface

The following may change only by §6.2. The list and the reasons are in `docs/158` §2; in short:

- the syntax and type rules, including `extern fn`;
- the diagnostic set, and the principle that codes are split **by the fix a user must make**, not
  by message wording;
- the unit-loading rules, including both spellings of `use`;
- the emitted-symbol conventions, including that a top-level name is unique within a unit (a flat
  namespace, with no mangling), and that the two calling conventions — the language's own, and the
  platform C ABI at `extern fn` call sites — coexist;
- the equivalence of the two back ends: byte-identical output for the same input, and a
  three-stage bootstrap that reaches a fixed point;
- the built-in function table;
- capability-domain semantics.

### 6.2 The procedure

Four steps. Each is necessary; none is sufficient alone. The full statement is `docs/158` §5.

1. **Change the specification first** — write the new rule as a sentence that can be decided. If it
   involves a diagnostic, allocate the code in `tools/loment_diag.py:RULES` first. **Codes are added,
   never changed**: a code is the most stable ABI this project has, because user scripts match on it.
2. **Add the criterion** — a minimal negative case to the suite, and **raise** the budget. Lowering
   a budget is relaxing the gate, which is concealing a gap, and the gate will go red for it.
3. **Change both implementations in one commit** — the reference and the self-hosted one. The
   parity and bootstrap criteria must both be green.
4. **Pass the static gate** — `python tools/ci.py --static-only`.

Where a rule is a *loading* rule rather than a *checker* rule, step 2's criterion is carried by the
driver gates instead of the rule-parity budget, because that suite is fed self-contained single-file
sources and cannot express "resolve an import". `docs/158` §5 records each time this has happened.

### 6.3 What is not frozen

Implementations and toolchain internals; buffer sizes and table layouts; performance; the contents
of generated trees; the command line, so long as its *behaviour* and the criteria are unchanged.

### 6.4 How this standard is reissued

**The version of this standard is the version of the language surface it defines.** There is one
number, not two. A separate edition axis for the document would require a table mapping standard
editions to language versions, and that table would be a second source of truth — which is the
failure mode this document exists to avoid.

So: a change under §6.2 produces a new version of the standard, in the same commit as the change.
The number in the title of this document is the one in `tools/loment_release.py:RELEASE`.

---

## 7. Known deviations and open items

`docs/158` §4 is a list of places where the language as implemented differs from the language as
described, or where a guarantee has a known hole. **That list is part of this standard.** An
implementation is not required to resolve any of them; it **is** required not to hide them.

Two consequences worth stating plainly:

- A conforming implementation must document its own deviations, in the same spirit. Silence about a
  known difference is a defect of the implementation, not a property of the language.
- Where a deviation's cause has been removed — a constraint that no longer holds — the record says
  so, and says that it did. Retracting a claim silently is indistinguishable from never having
  made it, which is why the project writes retractions down.

---

## 8. Not in scope

The following are deliberately outside this standard:

| Not defined here | Where it belongs |
|---|---|
| The spelling of the surface syntaxes | `docs/188`, `docs/179` — a grammar is a spelling; the semantics are Loment's |
| The toolchain's commands, and the package manager | Their own repositories and guides |
| Which switch a build channel exposes | `tools/loment_channel.py` — a product fact, not a language fact (§4.4) |
| Performance, code size, compile time | Measured in `docs/`, with guardrails, not promised here |
| Whether a library is available to install | The package manager's own release notes |

---

## 9. About this document

**Language.** This standard is written in English, because its audience is an implementer deciding
what to build. The normative references in §2 are mostly **Chinese** — that is a real limitation of
the project and it is stated here rather than discovered. Where the two disagree, §5.4 and §2
apply: the file is right, and this document is a pointer to it.

**How to cite.** *LLS 0.1.4.6*, or *Loment Language Standard 0.1.4.6*.

**How to change it.** This document carries no rules of its own. It changes when the things it
points at change, in the same commit, under §6.2 — and if a change to this document would alter
what the language is without changing the surface, that is a sign the change belongs somewhere
else.
