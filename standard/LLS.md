# Loment Language Standard

**LLS** · **0.1.5.1** · Edition 2

> **Status.** This document is **normative**. It is the single definition of the Loment
> language. Where an implementation, a tool, a test, or a design record disagrees with it,
> the disagreement is a defect in that thing, not an alternative reading of the language.
> The procedure for changing this document is §6.2.
>
> **What changed in this edition.** Earlier editions described themselves as a *frame*: a
> pointer to rules kept elsewhere. This edition is the frame's replacement. The language is
> now defined here — syntax, types, semantics, safety, artifacts, and the qualification of
> an implementation — and the material in `docs/` is **informative**: rationale, history,
> measurement, and design records. §2 states the standing of every source.
>
> **The one sentence.** Loment is a systems programming language whose programs are
> **native artifacts**, whose whole-program properties are **stated by the program** and
> carried into the artifact, and whose implementations are judged by a **conformance
> suite** rather than by the reputation of a compiler.

---

## Normative language

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**,
**SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** are to be interpreted as
described in RFC 2119 and RFC 8174.

A sentence is normative when it uses one of those words, or when it appears in a table
whose caption says **normative**. Everything else is explanatory, even inside this
document.

An implementation that does not satisfy a **MUST** is not conforming. An implementation
that does not satisfy a **SHOULD** is conforming but must say so, with its reason, in its
conformance declaration (§4.7).

---

# Part I — Charter

## 1. Scope and authority

### 1.1 What this document defines

This standard defines, for the language named **Loment**:

1. the **source language**: lexical structure, grammar, types, statements, expressions,
   patterns, items, entry points, and the rules that decide whether a program is
   well-formed;
2. the **editions** of that language: how an edition is named, declared, resolved,
   frozen, and extended (§8, §26);
3. the **semantics** of a well-formed program: evaluation, memory, capability domains,
   reclamation, memory safety, the audit ledger, and the whole-program switches (§19–§25);
4. the **artifact contract**: what a compiler emits, what it must carry, and how it is
   executed (§27–§34);
5. the **qualification of an implementation**: what a program must do to be called a
   Loment implementation of a given class, and what evidence it must publish (§4, §38);
6. the **stability contract**: which parts of the language may change only by the
   procedure in §6.2, and what that procedure is.

### 1.2 What this document does not define

The following are deliberately outside the standard. They are product facts or
measurements, not properties of the language:

| Not defined here | Why |
|---|---|
| Which switches a particular release channel exposes | A fact about a build. The channel is allowed to refuse an implemented switch; it is not allowed to silently accept one it does not implement. |
| Performance, code size, compile time | Measured and guarded in the repository, not promised by the language. |
| The internal architecture of an implementation | Buffers, table layouts, pass order, and data structures are free, provided the observable contract holds. |
| The spelling of the command-line interface | A conforming toolchain must provide the *behaviours* of §35; the words are a product choice. |
| Which libraries are installed on a machine | The package manager's business (§37). |
| The contents of generated trees | Deterministic outputs of the implementation, not language rules. |
| The exact wording of a diagnostic | The **code**, the **position**, and the **fix class** are normative; the prose is not. |

### 1.3 Authority

**This document is the sole normative original.** In descending order of authority:

1. **LLS** — what Loment is.
2. **The conformance suite** — the instrument that decides whether an implementation
   conforms (§38). A criterion is not a second definition: it is a test of this document.
3. **An implementation** — evidence that the language can be implemented, never the
   definition of it.
4. **Everything in `docs/`** — informative. Useful, measured, often the reason a rule
   exists; never the rule itself.

A conflict between levels is resolved by §5, not by choosing the more convenient level.

### 1.4 Audience

This document is written for a person who must decide **what to build**: the implementer
of a compiler, checker, runtime, or toolchain; the auditor of a claimed implementation;
and the author of a program who needs to know what the language guarantees. It is
deliberately explicit about boundaries and about what is not implemented, because a
standard that hides a hole is worse than one that names it.

### 1.5 How to read a rule

A rule in this document has four parts, whether or not all four are written out:

* **the condition** — when the rule applies;
* **the obligation** — what an implementation or a program must do;
* **the evidence** — what makes the obligation checkable;
* **the boundary** — what the rule does **not** promise.

Where a rule has a known hole, the boundary is stated in place, and again in §39.

---

## 2. Sources and their standing

### 2.1 Normative sources

Only two things are normative:

1. **this document**;
2. **the tables this document explicitly declares normative** — for example the
   diagnostic code table of §18 and the switch matrix of Appendix D. Those tables are
   part of this document even when a copy also exists in code.

### 2.2 Informative sources

The following are informative. They explain, measure, or record; they do not define.
Where one disagrees with this document, this document wins and the other is stale.

| Source | What it is good for |
|---|---|
| `docs/143-l1-loment-v0.md` | The original L1 specification and the loading rules. Historical; superseded where this document speaks. |
| `docs/146-loment-capability-semantics.md` | The formal core of `guard`: soundness assertions S1–S3 and the honest boundaries. |
| `docs/158-loment-freeze.md` | The history of the frozen surface, the four-step procedure, and the running list of known deviations. |
| `docs/188-*` | Surface grammars: why a spelling is not a semantics, and the factory lock. |
| `docs/296`, `docs/305`, `docs/309`, `docs/312` | Editions: the two-level name, the freeze of 2026.2, the opening of 2026.3, and the default-edition decision. |
| `docs/210`, `docs/253`, `docs/262`, `docs/270` | LGC, LMS, LSPE: designs, measurements, and the implemented slices. |
| `docs/299`, `docs/317`, `docs/318` | The lifetime dimension and the borrow-domain work. |
| `docs/306`, `docs/307`, `docs/308`, `docs/314`, `docs/315` | The capability-domain algebra, context domains, nesting, scoped domains, and domain loops. |
| `docs/313` | The wasm32 backend design (planned, not a language feature). |
| `tools/*.py`, `loment/selfhost/*.lomt` | The reference and self-hosted implementations. They are evidence of what is implemented, not of what is correct. |
| `.claude/skills/loment-*` | Practical guides derived from the implementations. Convenient, not normative. |

### 2.3 The one number

The version of this standard **is** the version of the language it defines. There is one
number, not two. §6.4 states the rule and this edition's number.

### 2.4 Reading order for an implementer

An implementer should read, in this order: §3 (terms), §4 (qualification), §7–§18 (the
surface), §19–§25 (semantics), §27–§34 (artifacts), and §38 (how conformance is measured).
The appendices are reference material; Appendix G is the qualification checklist.

---

## 3. Terms and the program model

### 3.1 Terms

**Unit.** One source file together with every unit it loads, directly or transitively.

**Root unit.** The unit from which a build starts. The root unit is the only unit that may
carry whole-program switches (§25) and the only one whose edition declaration decides the
edition of the whole program (§8.4).

**Library.** A unit intended to be loaded by another unit. A library is a directory; its
dependencies are the `use` lines in its source; its exports are the items marked `pub`.
A library MUST NOT declare whole-program switches (§25.5).

**Artifact.** What an implementation emits. The word is neutral about form: a native
executable, an object file for a linker, or a bare-metal image. An artifact is
distinguished from its **form object** (§3.2).

**Form object.** The machine-readable summary a unit exports about itself: what it
contains, what it reaches, which switches it set, which safety levels it selected, and
which edition it was read as. Its schema is §29.

**Switch.** A whole-program setting written with `choose` (§25). A switch is a property of
the program, not of a function or a file.

**Dimension.** A family of switch values that are mutually exclusive; writing two values
of one dimension is an error (§25.2).

**Edition.** A named, frozen-or-open version of the language surface (§8). Editions are
two-level: `tur<year>.<n>`.

**Profile.** The edition an implementation resolves for a unit after reading its grammar
declaration (§8.4). The profile is recorded in the artifact and reported at compile time.

**Implementation.** A program that reads Loment source and performs at least one of:
checking it, emitting an artifact for it, emitting its form object, or executing its
artifact. §4 defines the conformance classes.

**Conformance suite.** The criteria of §38. It, and not prose, decides whether a claimed
implementation conforms.

**Silent failure.** Any outcome in which an implementation accepts a program that this
document says is ill-formed, rejects a program that this document says is well-formed,
emits an artifact that does not mean what the source says, or exits successfully after a
diagnostic. Silent failure is the primary defect class this project exists to prevent.

### 3.2 The program model

A Loment program is:

```
source files → front end (surface translation + edition resolution)
             → unit assembly (use graph, deduplication, prelude)
             → static checking (types, names, ownership, domains, safety)
             → emission (IR + form object)
             → artifact (native executable / object)
```

Each arrow is a **contract boundary**, and each has a normative chapter:

| Stage | Contract | Chapter |
|---|---|---|
| source → front end | lexical and grammar rules; edition declaration; surface-grammar translation | §7, §8 |
| front end → unit | `use` resolution, ordering, deduplication, prelude | §16 |
| unit → checked unit | all static rules, with the diagnostic codes of §18 | §19 |
| checked unit → IR | deterministic lowering; switches applied | §25, §29, §34 |
| IR → artifact | symbol and ABI rules; linker behaviour | §27, §28 |
| artifact → execution | runtime, traps, syscalls, LVM | §30, §33 |

An implementation MAY fuse or reorder these stages internally. It MUST preserve their
observable contracts.

### 3.3 What a program is made of

A program is a sequence of **items** at the top level of a unit (§9): a module header,
imports, constants, functions, types, traits, implementations, capability declarations,
external declarations, command declarations, and switch definitions. Function bodies are
sequences of **statements** (§11), which contain **expressions** (§12). Control flow is
structured: there is no `goto`, no exceptions, and no unstructured label.

### 3.4 Whole-program properties

Loment is arranged so that the properties of an *artifact* that a reader cannot infer
from a function are **stated once** by the program and recorded in the form object. The
switch dimensions of §25 are the primary example. The rule is:

> **A property that changes what the artifact is MUST be expressible in the source, at
> most once, in the root unit, and MUST appear in the form object.**

This is what makes an artifact auditable by a party that does not read the source.

---

## 4. What counts as a Loment implementation

This section is normative. It is the answer to the question: *may this program call
itself a Loment implementation?*

### 4.1 Conformance classes

An implementation MUST declare one or more of the following classes. A class is a set of
requirements, not a badge: an implementation that satisfies only part of a class does not
conform to that class.

| Class | Name | What it does | Required chapters |
|---|---|---|---|
| **C1** | **Compiler** | reads source, checks it, emits an artifact and a form object | §7–§29, §34, §38 |
| **C2** | **Checker** | reads source, checks it, reports diagnostics; emits no artifact | §7–§26, §38 |
| **C3** | **Runtime** | executes an artifact that conforms to §27–§34 | §20, §27–§34 |
| **C4** | **Toolchain** | provides the user-facing behaviours of §35 around a C1/C2/C3 implementation | §35, §38 |

The two implementations maintained in this repository — the reference implementation
(`tools/lomentc.py` plus its companions) and the self-hosted one
(`loment/selfhost/`) — declare **C1 + C2 + C4**, and the self-hosted one additionally
implements **C3** in the form of the LVM (§30). They are peers: neither is the definition
of the language.

A program that merely *contains* a Loment parser, or that can run one example, is not an
implementation of any class.

### 4.2 Common requirements (every class)

An implementation of any class MUST satisfy all of the following.

**R1 — Edition fidelity.** It MUST read the grammar declaration of §8. It MUST resolve a
missing declaration to the newest edition defined by this document, and an explicit
declaration to that edition. It MUST reject a declaration it does not support, by name,
rather than silently reading the file as another edition.

**R2 — Grammar fidelity.** It MUST accept every program that conforms to the grammar of
§7–§14, and it MUST reject every program that does not, with a diagnostic whose code is
the one this document assigns to that class of error (§18). A parse error MUST NOT be
reported as a type error, and a type error MUST NOT be reported as a parse error.

**R3 — Static-rule fidelity.** It MUST enforce every static rule of §19, §21–§26. A
program that violates a rule MUST be rejected; a program that violates none MUST NOT be
rejected for a reason this document does not name.

**R4 — Code fidelity.** Every diagnostic MUST carry a code from §18 and a source position
(line, and a column or token position). The prose may differ from another
implementation; the code and the position may not. A diagnostic without a code is a
defect.

**R5 — No silent failure.** An implementation MUST NOT exit successfully after producing
a diagnostic, MUST NOT truncate an artifact, and MUST NOT silently drop a construct it
does not understand. When a limit is exceeded, it MUST refuse by name (§39 lists the
known limits).

**R6 — Determinism.** For the same input, edition, switches, and target, an
implementation MUST produce the same artifact bytes on every run and on every machine.
The artifact MUST NOT contain timestamps, paths, process ids, or hash-order iteration.

**R7 — Form-object fidelity.** A compiler MUST emit a form object that validates against
§29 and whose semantic fields agree with the source and the artifact. The form object MUST
record at least: the edition, the switches in force, the grammar, the capability
declarations, the boundary counts, and the safety levels.

**R8 — Edition additivity.** An implementation MUST NOT change the meaning of a frozen
edition. A program that was well-formed under a frozen edition MUST remain well-formed
with the same meaning. A new construct MUST enter through the open edition (§8.6).

**R9 — Collision honesty.** An implementation MUST refuse an undeclared unit that
declares a name which a newer edition treats as syntax, unless the unit pins its edition
(§8.7). This is the E040 rule; it exists so that "the default is the newest edition" does
not silently reinterpret old code.

**R10 — Deviation disclosure.** An implementation MUST publish, with its conformance
claim, every place where it does not implement a **MUST**, or where its behaviour differs
from this document. A known difference that is not published is a conformance defect.

**R11 — No false claims.** An implementation MUST NOT claim a class or an edition it has
not demonstrated. §4.7 defines the claim; §4.9 defines withdrawal.

**R12 — Refusal over guessing.** Where this document says a construct is not supported, an
implementation MUST refuse it with the assigned code. It MUST NOT accept it with a
best-effort meaning. Where this document is silent, an implementation MUST NOT invent a
meaning that changes the artifact; it MUST report the gap.

### 4.3 Compiler-specific requirements (C1)

In addition to §4.2, a C1 implementation MUST:

1. emit an artifact for every well-formed program, for every target it claims;
2. emit the form object alongside the artifact;
3. carry the edition marker of §27.4 into the artifact;
4. implement the symbol and calling-convention rules of §28;
5. refuse an artifact it cannot emit, by name, rather than emitting a partial one;
6. be **reference-equivalent** if it claims that tier (§4.4).

### 4.4 Reference equivalence (optional, required of the in-tree pair)

Two implementations are **reference-equivalent** when, for the same conforming input,
edition, switches, and target, they produce **byte-identical artifacts** and the same
diagnostic codes and positions.

Reference equivalence is *stronger* than conformance. A third-party compiler is not
required to be reference-equivalent; it is required to be **observably equivalent**:
same accept/reject decision, same diagnostic codes and positions, same program behaviour,
and a form object whose semantic fields agree.

The two implementations maintained in this repository MUST be reference-equivalent,
except where §39 records a known deviation. That is a project rule, not a rule of the
language; it is stated here because the conformance suite enforces it by comparing their
outputs.

### 4.5 Checker-specific requirements (C2)

A C2 implementation MUST implement the front end and every static rule, and MUST NOT
claim to emit artifacts. Its diagnostics MUST satisfy R4 and R5. A checker that accepts a
program the standard rejects is not conforming, even if it emits no artifact.

### 4.6 Runtime-specific requirements (C3)

A C3 implementation MUST:

1. execute every artifact that conforms to §27–§34 with the semantics of §20, §21, §30,
   and §33;
2. define the trap behaviour of §30.5: an `llvm.trap` terminates the process with exit
   code 132 and reports the site if it has a site channel;
3. implement the syscall numbers of the target it claims, and return the documented
   `-ENOSYS` for a number it does not implement, rather than a wrong result;
4. respect the memory limits it claims, and refuse by name rather than overrun;
5. NOT add a semantic not defined by this document (no hidden type conversions, no
   implicit reclamation, no extra entry point).

### 4.7 Claiming conformance

A conformance claim MUST be a document, published with the implementation, containing:

1. the **LLS version and edition** the claim is against (`LLS 0.1.5.1, Edition 2`);
2. the **classes** claimed (C1–C4);
3. the **editions** supported (by canonical name);
4. the **targets** supported (ELF/PE/aarch64/wasm32, as applicable);
5. the **suite result**: the command used, the date, and the tail line of each suite
   the implementation ran, including SKIPs;
6. the **deviation list** of R10;
7. for a C1 claim, whether it claims **reference equivalence** (§4.4).

A claim without item 5 is an assertion, not a conformance claim.

### 4.8 What is not required

An implementation is **not** required to:

* be self-hosted, or to share the architecture of the reference implementation;
* use the same internal IR, the same buffer sizes, or the same pass order;
* use the same command names as the repository's toolchain (the behaviours of §35 are
  required; the spellings are not);
* implement targets it does not claim;
* implement a planned feature of §40;
* match the prose of another implementation's diagnostics;
* pass a criterion whose prerequisite is absent — that criterion reports SKIP, and a SKIP
  is not a pass (§38.4).

### 4.9 Withdrawing a claim

A claim MUST be withdrawn, or amended to the narrower class, when:

* a **MUST** of the claimed class stops holding;
* a reference-equivalence claim fails on any conforming input;
* the implementation accepts a program the standard rejects, or rejects one it accepts,
  and the difference is not in the published deviation list.

Withdrawal is a publication, not a silence: a claim that is quietly narrowed is
indistinguishable from a false claim.

---

## 5. Precedence, defects, and change

### 5.1 Precedence

Where two of the following disagree, the earlier one wins:

1. this document, including its normative tables;
2. the conformance suite;
3. an implementation;
4. an informative source.

### 5.2 What a disagreement means

A disagreement is a **defect in one of the parties**, not a licence to pick a reading:

* **LLS vs. an implementation** — the implementation is wrong, unless the implementation
  has a published deviation (§4.9) or this document is amended under §6.2.
* **LLS vs. a criterion** — the criterion is wrong, or the LLS text is incomplete. The
  discrepancy MUST be resolved by amending one of them under §6.2; until then, the
  criterion is **red**, not waived.
* **Criterion vs. implementation** — the implementation is wrong if the criterion is
  right; the criterion is wrong if the LLS says otherwise. The change procedure settles
  which.

A red suite is never made green by relaxing the criterion. It is made green by changing
the implementation, or by changing the language under §6.2 — and the second is a visible,
recorded act.

### 5.3 Defect classes

| Class | Example | Required response |
|---|---|---|
| **Silent acceptance** | a program with an out-of-range literal index compiles | blocker; fix the implementation |
| **Silent rejection** | a conforming program is refused for an unnamed reason | blocker; fix or amend the LLS |
| **Miscompile** | the artifact does not mean what the source says | blocker; fix |
| **Diagnostic drift** | the right rejection, the wrong code | conformance defect; fix the code |
| **Missing deviation** | a known hole is not published | conformance defect; publish |
| **Documentation drift** | an informative file contradicts the LLS | amend the file; the LLS wins |

### 5.4 Change

A change to the frozen surface (§6.1) follows §6.2. A change to an informative file does
not change the language; it is a documentation change and MUST NOT be used to smuggle a
language change.

---

## 6. Stability, the frozen surface, and versioning

### 6.1 The frozen surface

The following are **frozen**: they may change only by the procedure in §6.2.

1. **The syntax and type rules** of §7–§19, including `extern` declarations.
2. **The diagnostic codes** of §18, and the principle that codes are split by the **fix a
   user must make**, not by message wording. Codes are added, never changed.
3. **The unit-loading rules** of §16: both spellings of `use`, the ordering and
   deduplication of dependencies, and the prelude injection point.
4. **The symbol and calling-convention rules** of §28, including the flat namespace and
   the coexistence of Loment's own convention with the platform C ABI at `extern` sites.
5. **The artifact contract** of §27: the edition marker, the entry stub, and the rule
   that an artifact is self-contained for the targets that claim it.
6. **The form-object schema** of §29, in the forward-only sense: a version may add
   required fields, never rewrite an older version.
7. **The built-in function table** of §18.3.
8. **The capability-domain semantics** of §21.
9. **The switch dimensions, their values, defaults, and contradictions** of §25.
10. **The edition rules** of §8 and §26: the two-level name, additive-only editions, the
    freeze of a closed edition, and the collision rule.
11. **The conformance classes and requirements** of §4.

### 6.2 The procedure

Four steps. Each is necessary; none is sufficient alone.

1. **Change this document first.** Write the new rule as a sentence that can be decided.
   If it involves a diagnostic, allocate the code in §18 first. Codes are added, never
   changed.
2. **Add the criterion.** Add a minimal positive or negative case to the conformance
   suite, and raise the ratchet budget if the suite has one. Lowering a budget is
   relaxing the gate, which is concealing a gap.
3. **Change both implementations in one commit.** The reference and the self-hosted one
   must change together; the parity and bootstrap criteria must be green.
4. **Pass the static gate.** Run the repository's static gate (the command is published
   with the suite; §38 lists the criteria it must include).

Where a rule is a **loading** rule rather than a **checking** rule, step 2's criterion is
carried by the driver gates instead of the rule-parity budget, because that suite is fed
self-contained single-file sources and cannot express import resolution.

### 6.3 What is not frozen

Implementations and toolchain internals; buffer sizes; table layouts; pass order;
performance; the contents of generated trees; the command-line spelling, so long as its
behaviour and the criteria are unchanged; the prose of diagnostics.

### 6.4 Versioning

**The version of this standard is the version of the language surface it defines.** There
is one number, not two. This edition is **0.1.5.1**, the language version at the time of
issue.

A change under §6.2 produces a new version of the standard in the same commit as the
change. A change to an informative file does not change the version.

The **edition** of the document (Edition 2) changes only when the document's authority
model or structure changes, not when a language rule changes. Edition 1 was a frame;
Edition 2 is the sole normative original.

### 6.5 What this edition adds

Relative to Edition 1, this edition:

* makes this document the sole normative original and demotes `docs/` to informative;
* adds the grammar, type system, statements, expressions, patterns, items, units,
  built-ins, diagnostics, and entry-point rules as normative text;
* adds the memory model, capability domains, LGC, LMS, and LSPE as normative chapters;
* adds the eighth switch dimension (`lifetime`) and the default-by-edition rule;
* adds the implementation-qualification chapter (§4) and the conformance checklist
  (Appendix G);
* records the 2026.3 default-edition decision (§8.4) and the collision rule (§8.7);
* names the known deviations of this issue in §39.

---

# Part II — The surface language

## 7. Source text and lexical structure

### 7.1 Encoding and line endings

A Loment source file MUST be valid UTF-8. Lines MAY end in LF or CRLF; both are accepted,
and the two forms MUST produce the same tokens, the same positions after normalisation,
and the same artifact. A UTF-8 byte-order mark at the very start of the file MUST be
ignored; it is not a character of the source and MUST NOT shift the column of the first
line.

An implementation MUST accept source files whose extension is not `.lomt` when the file
is named explicitly on the command line or reached by a path-form `use` (§16.2). The
extension `.lom` is special: it names an L0 interface file (§36.2). Every other extension
is L1 source.

### 7.2 Comments

Two comment forms exist:

| Form | Extent | Notes |
|---|---|---|
| `// …` | to the end of the line | |
| `/* … */` | until the matching `*/` | MUST NOT nest; an unterminated block comment is an error |
| `/// …` | to the end of the line | a **doc comment**; `loment doc` extracts it (§35.6). It is lexically a line comment. |

A comment is not a token. Text inside a comment MUST NOT affect parsing, name resolution,
or diagnostics.

### 7.3 Whitespace

Spaces, tabs, and newlines separate tokens. Whitespace is otherwise insignificant except
as a token separator. There is no indentation-sensitive syntax.

### 7.4 Identifiers

```
ident := [A-Za-z_][A-Za-z0-9_]*
```

Identifiers are ASCII only. A non-ASCII letter MUST be rejected as an illegal character
(E019), not silently accepted.

### 7.5 Keywords

**Loment has no fixed keyword table.** A word is syntax or an identifier depending on its
**position**, and on the **edition**:

| Word | Where it is syntax | Where it is an ordinary identifier |
|---|---|---|
| `module`, `use`, `fn`, `struct`, `enum`, `trait`, `impl`, `const`, `capability`, `excluded`, `extern`, `choose`, `set`, `addin`, `comefor`, `byuse`, `command`, `foruse`, `interrupt`, `register`, `return`, `if`, `else`, `while`, `for`, `in`, `match`, `let`, `as`, `true`, `false`, `pub`, `mut` | their grammatical positions | most are reserved by the parser; `mut` is an identifier in the default `loment` spelling (§8.10) |
| `def`, `elif`, `and`, `or`, `not` | Tur 2026.2+ only, and only in construct or operator position | anywhere a name is expected |
| `defer`, `break`, `continue` | Tur 2026.2+ only, in statement position | anywhere a name is expected |
| `lgc` | Tur 2026.3 only, in statement position | anywhere a name is expected |
| `domain`, `tick`, `quality`, `discharge`, `solid`, `couple`, `mutex` | statement position, by context | anywhere a name is expected |
| `orelse`, `catch` | Tur 2026.2+, immediately after an expression on the right of `let` / assignment | anywhere a name is expected |

The consequence is normative: **an implementation MUST NOT reject a program merely
because a word that is special in one edition appears as a name.** A program that uses
`break` as a variable name is well-formed in Tur 2026.1; the same program declared
`tur2026.2` and using `break;` in a loop body means loop control.

The position-dependence is what makes the collision rule of §8.7 necessary: a word that
becomes syntax in a newer edition can collide with a name in an older one.

### 7.6 Literals

**Integer literals.** Decimal (`0`, `42`) or hexadecimal (`0x2A`, `0X2A`). There are **no**
binary or octal literals, no digit separators (`1_000` is an error), and no type suffixes
(`42u32` is an error). An integer literal has no intrinsic width; its type is fixed by its
context (§10.6). A leading `-` is a unary operator, not part of the literal.

**Boolean literals.** `true` and `false`.

**String literals.** A double-quoted sequence of UTF-8 characters. The accepted escapes
are exactly:

| Escape | Meaning |
|---|---|
| `\n` | line feed |
| `\t` | tab |
| `\r` | carriage return |
| `\0` | NUL |
| `\\` | backslash |
| `\"` | double quote |

Any other escape is an **error** (E019), naming the accepted set. A string literal MUST
close; a newline inside it is an error. The value of a string is a `str`: a pointer plus a
length, not a NUL-terminated C string (§10.4).

**No other literal forms exist.** There is no character literal and no floating-point
literal. A floating-point value is a library type over integers (§36.4).

### 7.7 Punctuation and operators

The punctuation set is:

```
{ } ( ) [ ] : ; = @ , . + - * / % < > ! & | ^ ?
```

Multi-character operators are lexed as single-character tokens followed by the parser's
combination step: `->`, `::`, `==`, `!=`, `<=`, `>=`, `&&`, `||`, `<<`, `>>`, `..`.
An implementation MUST treat the token stream and the combined forms consistently; a
diagnostic position is the position of the **first** token of the operator.

### 7.8 Statement terminators

* A statement ends with `;`.
* `module` and `use` take **no** terminator. A `use` written with `;` is accepted by the
  reference front end as a tolerated spelling; new code SHOULD omit it.
* `struct`, `enum`, `trait`, `impl`, `fn`, and `capability` declarations take no
  terminator after their closing `}`.
* `const NAME: T = value;` ends with `;`.
* A `match` arm body is a block; no comma is required or accepted between arms.

### 7.9 Positions

A diagnostic position is a **line** plus a **column** (1-based) or a token index,
depending on the implementation's rendering. The line number MUST refer to the source as
written, after the equal-length rewrites of §8 that preserve line and column. A CRLF
line ending MUST NOT shift a line number. An implementation that strips a grammar
declaration or a sugar rewrite MUST do so with an **equal-length** replacement so that
positions do not move.

---

## 8. Grammar declarations and editions

### 8.1 The declaration

A source file MAY begin with exactly one grammar declaration:

```
choose write grammar <alias>
```

It MUST appear **before** `module`, at the start of the file, and at most once. A
declaration after `module`, a second declaration, or a declaration with no alias is an
error (E019 for the shape; E023 for a value outside the factory lock, per §18).

A misspelling of the three words (`choose language …`, `choose grammar …`, `grammar …`,
`choose write grammars …`) MUST be **reported**, not silently treated as an absence, when
it appears in the position where a declaration is expected. The message MUST quote the
line and show the correct form.

The declaration line is **not part of the program**: it says how the file is read. It is
removed before parsing by an equal-length rewrite; the resolved edition travels with the
unit.

### 8.2 The alias table (normative)

The alias table is a **factory lock**: compiled into the implementation, not extensible
or overridable by a user. The table is:

| Source spelling | Canonical name | Layer |
|---|---|---|
| `loment` | `loment` | native; resolves to the default edition (§8.4) |
| `tur`, `tur1`, `tur2026.1` | `tur2026.1` | native, Tur 1 |
| `tur2`, `tur2026.2` | `tur2026.2` | native, Tur 2 |
| `tur3`, `tur2026.3` | `tur2026.3` | native, Tur 2026.3 |
| `c` | `c` | foreign surface grammar |
| `cpp`, `c++`, `cxx`, `cc` | `cpp` | foreign surface grammar |
| `java` | `java` | foreign surface grammar |
| `csharp`, `cs`, `c#` | `csharp` | foreign surface grammar |
| `go`, `golang` | `go` | foreign surface grammar |
| `python`, `py` | `python` | foreign surface grammar |
| `rust`, `rs` | `rust` | interface extraction only |
| `natural`, `nl`, `lument` | `natural` | foreign surface grammar |

An alias is matched after trimming and lower-casing. **Only the canonical name may enter
the artifact.** Two spellings of the same grammar MUST NOT produce different artifact
bytes.

An implementation MUST reject an alias outside this table with E023, listing the accepted
values. It MUST NOT extend the table.

### 8.3 Native and foreign readings

A **native** reading (`loment` and the Tur editions) is read by the Loment lexer and
parser; no translator is involved. A **foreign** reading translates the file into Loment
in process; the translated unit has the same semantics as the equivalent native program.

The governing rule is: **a surface grammar changes spelling and shape, never semantics.**
If a translator makes a spelling behave like its namesake language, that is a defect. The
measured example is Python's `//`: in the Python spelling it is Loment's division
(truncation toward zero), not CPython's floor division.

`rust` is **interface extraction only**: declarations are read; a complete program is
refused. To write a program with Rust's spelling, declare `grammar tur`.

The reference implementation performs foreign translation in process. A packaged,
self-hosted toolchain MAY accept only native spellings; it MUST then refuse a foreign
declaration **by name** (§4.2 R12), not read the file as Tur.

### 8.4 Edition resolution and the default

The language defines the following editions:

| Canonical name | Family | Status |
|---|---|---|
| `tur2026.1` | 2026 | **frozen** |
| `tur2026.2` | 2026 | **frozen** |
| `tur2026.3` | 2026 | **open** |
| `tur2027.1` and later | future | not defined by this edition |

**Resolution.** A unit with no declaration, or with `choose write grammar loment`, is read
as the **newest edition defined by this document** — for this edition, `tur2026.3`. A
unit with an explicit edition spelling is read as that edition. `tur` and `tur1` are
explicit historical spellings of Tur 1; they do not follow the default.

The default is a property of the **standard**, not of a release: when this document
defines a newer edition, the default moves with it, and the collision rule of §8.7 keeps
that move from silently reinterpreting old code.

**Root rule.** The edition is a whole-program property. Only the root unit's declaration
is authoritative; a declaration in a dependency is stripped and does not change the
program's edition. A dependency written for an older edition is read under the root's
edition, and the collision rule applies to it.

### 8.5 The edition is reported

An implementation MUST make the resolved edition observable in three places:

1. **At compile time** — a diagnostic-channel line naming the spelling and the canonical
   name (for example `loment: grammar default -> tur2026.3 (Tur 2026.3)`). A tool that
   compares the compiler's output byte for byte MAY suppress this line with a documented
   flag.
2. **In the artifact** — an externally visible marker holding `LOMENT-TUR:` followed by
   the canonical name (§27.4). The marker MUST NOT be a private constant that an optimiser
   may drop.
3. **At run time** — the built-in `tur_edition() -> str` returns the canonical name.

### 8.6 Additivity and freezing

**Editions only add.** A canonical edition name never changes meaning. A new construct
MUST enter through the open edition (or through a newly opened edition); it MUST NOT be
added to a frozen one.

The frozen guarantee is about **conforming programs**: a program that was well-formed
under edition E MUST remain well-formed with the same meaning under every later
implementation of E. The guarantee is **not** a promise that the set of syntax errors is
frozen: a construct that was previously a syntax error MAY become accepted in an older
edition, because no conforming program contained it. A change that would give a new
meaning to a program that already conformed is forbidden.

The frozen editions of this issue are `tur2026.1` and `tur2026.2`. `tur2026.3` is open: new
constructs land there until it is frozen by the procedure of §6.2.

### 8.7 The collision rule (normative)

A new edition may need to use, as syntax, a word that was a legal identifier in an older
edition. The following words are registered as **collision names**:

| Edition that introduced the syntax | Collision names |
|---|---|
| `tur2026.2` | `break`, `continue`, `defer`, `print_num` |
| `tur2026.3` | `lgc` |

An implementation MUST refuse a unit that:

1. does not pin its edition to the effective edition (no declaration, or a declaration of
   an older edition), and
2. **declares** one of the collision names — as `let` / `const` / `fn` / `for` binding, or
   as a parameter or field name in the `(` / `,` … `:` position.

The diagnostic is **E040** and MUST offer the two remedies: rename the binding, or pin the
reading with an older edition declaration. **Using** a collision name as syntax (for
example `break;` inside a loop) MUST NOT trigger the rule.

For every open edition, the collision table MUST contain an entry, even if empty; an
empty entry is the explicit answer "this edition introduces no colliding spelling". An
implementation MUST NOT treat an unregistered open edition as collision-free.

### 8.8 Short names

`tur1`, `tur2`, and `tur3` are **release-year shorthands**. They are accepted in the year
that released them. After that year an implementation MUST reject them and name the full
edition. A shorthand MUST NEVER be reused for a different edition family.

### 8.9 The 2026.2 surface

Tur 2026.2 adds the following constructs. They are **not** available in 2026.1.

| Construct | Written as | Meaning | Restrictions |
|---|---|---|---|
| `def` | `def f(…) { … }` | identical to `fn f(…) { … }` | sugar; same artifact bytes |
| `elif` | `elif cond { … }` | `else if cond { … }` | sugar |
| `and`, `or`, `not` | `a and b`, `a or b`, `not a` | `a && b`, `a || b`, `!a` | sugar; same short-circuit |
| for-in | `for x in xs { … }` | index loop over a named array or slice | v1 accepts a named array/slice, or (2026.3+) a call returning one; `x` is a copy; other expressions and `mut` are later editions; the index-and-element form `for i, x` is 2026.3+ (§8.10) |
| `break`, `continue` | inside a loop | exit / next iteration of the innermost loop | outside a loop is E019 |
| bare `return;` | in a unit function | early return | only when the function has no return type |
| `orelse`, `catch` | `let a: u32 = maybe orelse 0;` | switch on a two-variant enum; success takes the payload, failure the fallback | v1: right-hand side of `let`/assignment only; the enum has exactly two variants, the first carrying the payload; the fallback is lazy |
| `print_num` | `print_num(n);` | decimal integer, no newline, one write | a Tur 2 built-in; integer types widened by sign to `i64`; needs the runtime block (§25.4) |
| `defer` | `defer f(a, b);` | runs the **call** when the enclosing block exits | v1: call only; LIFO; arguments evaluated at the `defer`; not run on panic/trap; self-hosted caps: 8 per function, 4 arguments, 64 nesting levels |

The sugar constructs MUST be **byte-identical** to their explicit spelling: the same
program written with `def` / `and` / `elif` and with `fn` / `&&` / `else if` produces the
same artifact (modulo nothing; the edition is the same).

### 8.10 The 2026.3 surface

Tur 2026.3 adds:

| Construct | Written as | Meaning | Restrictions |
|---|---|---|---|
| `lgc` | `lgc end;` / `lgc start;` / `lgc start(10ms) { … }` | GC timing control: pause, resume, or open a lexical window with a cooperative budget | requires a collector: `no_gc` + `lgc` is E039; the budget gates *starting* a collection, not preempting one |
| `lifetime` | `choose lifetime_no` / `lifetime_auto` / `lifetime_manual` | the eighth dimension (§25.7) | the default is `lifetime_auto` from 2026.3; `lifetime_no` in older editions |
| `&T` in type position | `&[u32]`, `&mut Cursor`, `&str` | a reference type; implicit regions; zero annotations in `lifetime_auto` | in `lifetime_manual`, an explicit region marker `&a T`; unary `*` dereferences |
| domain blocks and laws | `domain buf : disk[0..63] consumed(1) { … }`, `domain:for i`, `solid`, `couple`, `mutex`, `tick`, `quality`, `discharge` | scoped capability domains, domain loops, groups, and the stateful laws of §21 | only bounded, orderable domains can be walked; §21.6–§21.9 |
| struct literal shorthand | `S { a }` | identical to `S { a: a }`; the value is the same-named binding | 2026.3+ only; explicit and shorthand fields may be mixed; missing/duplicate/unknown fields remain E015 |
| for-index | `for i, x in xs { … }` | an index loop that also binds the position: `i` is a `u32` copy of the current index, `x` a copy of the element | 2026.3+ only; the source is still a named array or slice; `i` and `x` are copies, so assignments to either do not write back |
| for-call | `for x in buf(&xs) { … }` | a for-in whose source is a call returning an array or slice; the call is evaluated once | 2026.3+ only; the source MUST be a call (array literals and other expressions are later editions); `x` is a copy |
| if expression | `let x: u32 = if c { 10 } else { 20 };` | a `let` initialiser picks one of two expressions | 2026.3+ only; initialiser position only, one expression per arm, `else` required; no `else if` or nesting |
| match expression | `let x: u32 = match d { D::N => 1, _ => 0 };` | a `let` initialiser picks one arm's expression | 2026.3+ only; initialiser position only, subject is a name, one expression per arm, arms comma-separated; no nesting or literal patterns |

The lifetime dimension is **edition-gated**: writing `choose lifetime_*` in 2026.1 or
2026.2 is refused; those editions keep the old default (`lifetime_no`) and the old
restriction that `&` is not a type.

### 8.11 Surface-grammar coverage

The foreign surface grammars map a subset of their source language onto Loment semantics.
The mapping is normative only in one direction: **the same Loment operation results**.
The extent of the accepted foreign syntax is an implementation fact, reported in that
implementation's conformance declaration.

The known boundaries are listed in Appendix H. A program written in a foreign spelling
MUST NOT acquire a semantic that the native spelling does not have; a translator that
cannot represent a construct MUST refuse the unit, not drop the construct.

---

## 9. Items and declarations

The top level of a unit is a sequence of **items**. The first non-declaration item MUST be
`module <name>`. A file without it is E019.

### 9.1 Item forms

| Item | Form | Notes |
|---|---|---|
| module | `module IDENT` | no semicolon; exactly one per file |
| grammar | `choose write grammar ALIAS` | before `module`; §8 |
| project dimension | `choose VALUE` | root unit only; §25 |
| switch definition | `set choose NAME { ITEM* }` | a switch is a named set of items compiled only when on; §25.6 |
| switch settings unit | `addin NAME` | root unit only; loads a unit that may contain only switch code |
| import | `use NAME` or `use "PATH"` | §16 |
| constant | `const NAME: TYPE = INT;` | the value MUST be one integer literal, optionally negated; §17 |
| function | `fn NAME GENERICS? ( PARAMS? ) (-> TYPE)? BLOCK` | at most 10 parameters; §9.2 |
| struct | `struct NAME GENERICS? { FIELDS }` | fields are scalars only (§10.7) |
| enum | `enum NAME GENERICS? { VARIANTS }` | at most one payload per variant; scalar payloads |
| trait | `trait NAME { FN_SIG* }` | static dispatch only; §15 |
| impl | `impl TRAIT for TYPE { FN* }` | methods; §15 |
| external function | `extern fn NAME ( PARAMS? ) (-> TYPE)? ;` | scalars and `ptr` only; §28.5 |
| capability | `capability NAME : SPACE[lo..hi] LAWS?` | §21 |
| excluded space | `excluded "SPACE: …"` | using that space in a capability or syscall site is E005 |
| custom syntax | `comefor let "WORD" to BLOCK … byuse "WORD" done` | a compile-time token transformer; §18.4 |
| command language | `command LANG` / `foruse LANG` | declares an external-code language; §14.6 |
| external code block | `let LANG { RAW }` or `LANG { RAW }` | raw bytes, not a string literal |
| command registration | `register NAME { FN }` or `pub fn loment_command() -> str` | §14.5 |
| visibility | `pub` before `fn`, `struct`, `const`, `enum`, `trait` | cross-module export |

### 9.2 Functions

```
fn NAME (<PARAM>,* ) (-> TYPE)? BLOCK
PARAM := IDENT ":" TYPE          // `mut IDENT: TYPE` is accepted in Tur spellings
```

* Every parameter MUST be typed. There is no inference.
* At most **10** parameters. A count mismatch at a call is E003. A call with **more than
  10 arguments** is a hard limit: the self-hosted product panics (exit 132, no
  diagnostic). An implementation MUST refuse the excess by name, not silently drop an
  argument.
* A missing return type means the unit type `()`.
* A non-unit function MUST NOT be able to fall off the end of its body: E027.
* A function body is a block (§11.1).
* Functions are declared before use within a unit; there are no forward references.

### 9.3 Structs and enums

```
struct NAME { FIELD,* }           FIELD := IDENT ":" TYPE
enum NAME { VARIANT,* }           VARIANT := IDENT ("(" TYPE ")")?
```

* A struct field MUST be an integer type, `bool`, or — where the implementation supports
  it — a reference/pointer form admitted by §10.7. A `str` or `ptr` field passes some
  checks but is refused when lowered; an implementation MUST refuse it at the point it
  cannot lower, by name.
* A struct MUST have at least one field; an empty struct is E009.
* An enum MUST have at least one variant; an empty enum is E009.
* A variant carries at most one payload.
* The name of a struct or enum MUST NOT collide with a base type name (E009).

### 9.4 Capabilities and `excluded`

```
capability NAME : SPACE "[" SHAPE "]" LAWS?
```

The shape and laws are defined in §21. `excluded "SPACE: …"` declares a space that may
not be used by a capability or a syscall site; a use is E005.

### 9.5 `comefor`

A `comefor` definition names a word and a Loment program that transforms tokens; the
program is run by the compiler's token engine, reads tokens with `ct_n` / `ct_tok`, and
emits with `ct_out` / `ct_syn`. The region between the definition and `byuse` is expanded
**before parsing**. Definition and terminator are top-level only and cannot nest; a word
may not be defined twice. `use` is not available inside the body. §18.4 states the
built-ins.

### 9.6 Item order and visibility

Within a unit, an item MUST be declared before it is used. `pub` exports an item to units
that load this one. There is no forward reference and no hoisting. The unit's top-level
namespace is **flat**: no mangling, no modules inside a unit (§28.2).

---

## 10. Types

### 10.1 The type set

| Type | Spelling | Size / representation |
|---|---|---|
| unsigned integers | `u8`, `u16`, `u32`, `u64` | 8/16/32/64 bits |
| signed integers | `i8`, `i16`, `i32`, `i64` | 8/16/32/64 bits, two's complement |
| boolean | `bool` | one logical bit; representation is implementation-defined |
| unit | `()` | zero-sized; a function with no `-> T` |
| string | `str` | pointer plus length (a value, not a borrow) |
| raw pointer | `ptr` | an untyped, unchecked address |
| array | `[T; N]` | `N` a compile-time integer constant; `T` an integer or `bool` |
| slice | `[T]` / `mut [T]` | parameter types only; `T` an integer or `bool` |
| struct / enum | the declared name | by value |
| generic parameter | `T` | monomorphised at compile time |
| prelude enums | `Option<T>`, `Result<T, E>` | injected at the end of the unit (§16.5) |
| reference (2026.3) | `&T`, `&mut T` | a borrow with an implicit region |

**Not types.** There is no float type, no `char`, no tuple, no function-pointer type, no
closure type, no `impl Trait`, and no `dyn`. A floating-point value is a library type over
integers. A string literal is a `str`, never a C string.

### 10.2 Integer types and widths

The eight integer types are distinct. **A binary operation requires both operands to have
the same width and signedness.** Mixing widths is E001 and MUST NOT be silently widened
or truncated. `as` is the only conversion (§12.6).

Arithmetic is **wrapping** and is defined behaviour: `+`, `-`, `*` wrap modulo 2^N for the
operand width. There are no overflow traps and no `nsw`/`nuw` flags in the emitted
arithmetic. Division or remainder by zero aborts the process (a trap, §30.5); it does not
produce a value.

### 10.3 Boolean

`bool` is a distinct type. Conditions of `if`, `while`, `for`-in, and the operands of
`&&`, `||`, `!` MUST be `bool`. An integer is not a boolean; write `x != 0`.

### 10.4 `str`

A `str` is a value consisting of a pointer and a byte length. It is not NUL-terminated
and is not a borrowed `&str`. The operations are the `str_*` built-ins (§18.3). Passing a
`str` to `extern` is E021: a C function expects a NUL-terminated pointer, and the
conversion is the program's job.

### 10.5 `ptr`

`ptr` is an untyped, unchecked address. `alloc`, `load8`, `store8`, `ptr_add`, `ptr_sub`,
`free`, `atomic_add`, and pointer/integer casts operate on it. A `ptr` is Copy. There is no
bounds check, no lifetime, and no aliasing rule on a raw pointer; capability domains
(§21) are what make accesses auditable, and they do not stop a raw pointer by themselves.

### 10.6 Literal typing

An integer literal has no intrinsic type. Its type is fixed by context:

* the declared type of a `let`, parameter, return, or field;
* the other operand of a binary operation;
* `u32` when the context does not fix it (for example the default type of a `for` range
  whose bounds are both literals).

An implementation MUST reject a literal that does not fit the context type, rather than
truncate it. A negative literal is a unary minus applied to a literal; its type is the
context type.

### 10.7 Arrays, slices, and aggregate lowering

* `[T; N]`: `N` MUST be a positive integer constant. An array literal MUST be non-empty,
  its elements MUST have one type, and its length MUST equal `N` (E016).
* `[T]` and `mut [T]` are **parameter types only**. A caller passes `&xs` for `[T]` and
  `&mut xs` for `mut [T]`. Length is obtained with `slice_len`.
* `T` of an array or slice MUST be an integer or `bool`.
* Struct fields and enum payloads are scalar-only unless the implementation's lowering
  admits a reference or pointer form. A construct that passes checking but cannot be
  lowered MUST be refused **before emission**, by name (E023), not emitted as a partial
  artifact.

### 10.8 Generics

A generic function or type parameter is monomorphised at compile time. A generic enum
used in a pattern is written with its **plain** name (`Result::Ok(v)`), and the checker
rewrites it to the monomorphised instance. There is no runtime type information and no
dynamic dispatch.

### 10.9 References and regions (2026.3)

In Tur 2026.3 and later, `&T` and `&mut T` are types: they may appear in parameters,
return types, struct fields, nested types, and generic arguments. A reference carries an
**implicit region**; the source contains no `'a`, no `<'a>`, and no `where` clause.

Elision rules:

1. one reference parameter → every reference output borrows from it;
2. several reference parameters and one reference output → the output's region is the
   **shortest** of the inputs;
3. a method's `&self` / `&mut self` counts as one input, and outputs default to borrowing
   from it.

A genuine fork (several outputs, or an output tied to one specific input) is written with
a **region marker** in `lifetime_manual`: `fn pick(a: &[u32], b: &[u32]) -> &a [u32]`. In
`lifetime_auto`, writing a marker is E019 with the message that automatic mode requires
zero annotations. In `lifetime_no`, `&` in a type position is a parse error.

Regions are static proofs of borrow duration. They do not replace reclamation (§22) or
memory safety (§23); the three are orthogonal.

---

## 11. Statements

### 11.1 Blocks and scope

A block is `{ statement* }`. **Blocks do not introduce a `let` scope** in the current
language: a `let` inside an `if` branch is visible after the branch. An implementation
MUST implement the language as it is, and a program MUST NOT rely on block scoping for
correctness. (This is a known limitation, recorded in §39.)

### 11.2 Statement forms

| Statement | Form | Notes |
|---|---|---|
| binding | `let NAME: TYPE = EXPR;` | the type is REQUIRED; no inference. In Tur spellings, `let mut NAME: TYPE` is accepted and `mut` is stripped. |
| assignment | `LVALUE = EXPR;` | the target MUST be an lvalue (E014): a variable, field, or index |
| if | `if EXPR BLOCK (else if EXPR BLOCK)* (else BLOCK)?` | the condition MUST be `bool` |
| if-let | `if let PATTERN = EXPR BLOCK (else BLOCK)?` | one pattern |
| while | `while EXPR BLOCK` | |
| range for | `for IDENT in LO..HI BLOCK` | `HI` exclusive; bounds integer; the loop variable has the bounds' type (`u32` if both are literals) |
| for-in | `for IDENT in ARRAY_OR_SLICE BLOCK` | 2026.2+; a copy of each element |
| for-index | `for IDENT "," IDENT in ARRAY_OR_SLICE BLOCK` | 2026.3+; the first name is a `u32` index copy, the second the element copy |
| for-call | `for IDENT in CALL BLOCK` | 2026.3+; the call MUST return an array or slice and is evaluated once |
| match | `match EXPR { ARM* }` | §13 |
| return | `return EXPR;` or bare `return;` | the value is required unless the function is unit and the edition is 2026.2+ |
| break / continue | `break;` / `continue;` | 2026.2+; innermost loop; outside a loop is E019 |
| block | `BLOCK` | |
| audit bypass | `lspe pass "REASON" { BLOCK }` | §24.7; the reason is mandatory (E038) |
| defer | `defer CALL;` | 2026.2+; §8.9 |
| lgc | `lgc end;` / `lgc start;` / `lgc start(DURATION)? BLOCK` | 2026.3; §22.7 |
| domain block | `domain [NAME :] SPACE[SHAPE] LAWS? BLOCK` | §21.6 |
| domain loop | `domain:for IDENT ("," IDENT)* ("in" NAME ("," NAME)*)? ("step" "-"? INT)? BLOCK` | §21.7 |
| tick | `tick;` | advances the virtual clock |
| quality | `quality CAP = EXPR;` | adaptive-domain quality, 0..255 |
| discharge | `discharge CAP(EXPR);` | clears a `reachable` obligation |
| solid | `solid NAME : CAP ("," CAP)* ;` | names a renewal unit (§21.8) |
| couple / mutex | `couple A, B, …;` / `mutex A, B, …;` | §21.8 |
| expression statement | `EXPR;` | typically a call |

### 11.3 `let`

The type annotation is required. `let x = 1;` is a **parse error** (E019, "expected `:`,
got `=`"), not a type error. A name MUST be declared before use; use before `let` is E002.
Re-declaring the same name in the same function is allowed by the reference implementation
and shadowing behaviour differs in one known case (§39).

### 11.4 Assignment

The left side MUST be an lvalue: a variable, a field access, or an index. The value's type
MUST equal the target's type. Assignment to a non-lvalue is E014.

### 11.5 `if` and `while`

Conditions MUST be `bool`. `&&` and `||` short-circuit. A struct literal in a condition
position is parsed as a block where the grammar would be ambiguous; the rule is the same
as the reference parser's "no struct literal in condition" guard.

From Tur 2026.3, `if` is also an expression in **one** position: the whole initialiser of
a `let` (`let x: T = if c { a } else { b };`). Each arm is a single expression, `else` is
mandatory, and the construct lowers to `let x: T;` followed by an ordinary `if`. `else if`,
nested `if` expressions, and `if` in any other expression position are not accepted in
this edition round (§8.10).

### 11.6 `for`

Range-for iterates `LO` (inclusive) to `HI` (exclusive). For-in iterates the elements of a
named array or slice; the element is a **copy**, and mutating the loop variable does not
write back. From Tur 2026.3, `for i, x in xs` additionally binds the position to `i`, a
`u32` copy, before the element is bound; `i` starts at 0 and advances with `x`. Both names
are ordinary bindings in the loop body's scope, and assigning to either does not write
back. Also from 2026.3, the source may be a **call** returning an array or slice
(`for x in buf(&xs) { … }`); the call is evaluated once, into a temporary, and the loop
walks that temporary. Other expressions (array literals, arithmetic, parenthesised names)
are not accepted yet. The v1 for-in still does not accept a `&mut` reference.

### 11.7 `return`, `break`, `continue`

`return EXPR;` returns from the innermost function. Bare `return;` is allowed only in a
unit function and only in Tur 2026.2+. `break` and `continue` apply to the innermost loop
and are available only in 2026.2+. Using either outside a loop is E019.

### 11.8 `defer`

`defer CALL;` records a call whose arguments are evaluated **where the `defer` is
written**. The call runs when the enclosing block exits normally, by `return`, by `?`, or
by `break`/`continue`; it does **not** run on panic, trap, or process exit. Multiple
defers run in LIFO order within a block. The self-hosted implementation limits a function
to 8 defer statements, 4 arguments each, and 64 nesting levels; exceeding a limit is a
named error. The reference implementation has no such caps.

### 11.9 `lgc`

`lgc end;` pauses the collector; `lgc start;` resumes the tier's default; `lgc end {
… }` and `lgc start { … }` are lexical windows that restore the previous state on exit,
including an early `return`. `lgc start(DURATION) { … }` additionally sets a cooperative
budget: the collector will not **start** a new collection while `now >= deadline`. A
collection already running is not preempted. The duration is an integer literal plus a
unit (`ms`, `us`, `ns`). `lgc` under `no_gc` is E039.

### 11.10 Audit bypass

`lspe pass "REASON" { BLOCK }` is a named, recorded bypass for a site the compiler cannot
prove. The header is flattened at parse time: the body is the surrounding statement
sequence, not a new scope. The reason is mandatory; missing or blank is E038. Sites inside
are still counted, and the pass ratio is a ratchet (§24.7).

---

## 12. Expressions and precedence

### 12.1 Primary expressions

Integer and string literals, `true` / `false`, identifiers, enum paths `E::V`,
calls `f(a, b)`, method calls `obj.m(a)`, field access `e.f`, indexing `e[i]`,
parenthesised `(e)`, struct literals `S { a: 1 }`, array literals `[1, 2, 3]`, and
`&e` / `&mut e` in **call-argument position** (and, from 2026.3, as reference
expressions in the positions admitted by §10.9).

### 12.2 Postfix

Call, field access, index, and `?` (propagation), applied left to right.

### 12.3 Unary

`-` and `!` only. There is no `~`; write `v ^ -1` for a signed value or `v ^ 255` for a
`u8`. In 2026.3, unary `*` dereferences a reference.

### 12.4 Binary operators and precedence

Loosest first:

| Level | Operators |
|---|---|
| 1 | `\|\|` |
| 2 | `&&` |
| 3 | `==` `!=` |
| 4 | `<` `<=` `>` `>=` |
| 5 | `\|` |
| 6 | `^` |
| 7 | `&` |
| 8 | `<<` `>>` |
| 9 | `+` `-` |
| 10 | `*` `/` `%` |

`as` binds tighter than any binary operator. Both operands of a binary operator MUST have
the same type (hence the same width and signedness). A mismatch is E001.

### 12.5 Short-circuit and side effects

`&&` and `||` evaluate the right operand only when needed. Other binary operators evaluate
left then right. There is no operator overloading.

### 12.6 Casts

`EXPR as TYPE` converts:

* integer to integer (truncating or sign-extending as the target width requires);
* integer to pointer and pointer to integer (the pointer is an address);
* never to or from `bool` or `str` implicitly; `bool` has no cast.

An illegal cast is E017. A cast is always explicit; there is no implicit numeric
conversion.

### 12.7 `?`

`EXPR?` propagates the failure variant of `Result` or `Option`. It is E010 when the value
is not one of those types. `?` is an early return from the enclosing function.

### 12.8 `orelse` and `catch`

`EXPR orelse FALLBACK` and `EXPR catch FALLBACK` switch on a two-variant enum whose first
variant carries the payload. On success the payload is the value; on failure the fallback
is evaluated **lazily**. They are available only as the right-hand side of `let` or
assignment in 2026.2+.

### 12.9 Struct literals

`S { a: 1, b: 2 }` requires every field exactly once (E015). A missing, duplicate, or
unknown field is an error. From Tur 2026.3, a field may be written as a bare identifier:
`S { a, b }` is exactly `S { a: a, b: b }`. The shorthand is **edition-gated**: in 2026.1
and 2026.2 the same text is a syntax error, so the frozen editions are unchanged. Mixed
forms (`S { a, b: 2 }`) are allowed.

### 12.10 Array literals

`[e1, e2, …]` is non-empty and homogeneous; its length must match the declared array type
when one is known (E016). Indexing requires an array or slice and an integer index; the
index is not bounds-checked by the language (E028 catches literal out-of-range cases;
LMS adds runtime checks where configured, §23).

---

## 13. Patterns and `match`

### 13.1 Patterns

Exactly two pattern forms exist:

| Pattern | Form | Meaning |
|---|---|---|
| enum variant | `E::V` / `E::V(name)` | matches that variant; a payload variant MUST bind a name; a payload-free variant MUST NOT |
| wildcard | `_` | matches anything |

There are no literal patterns, range patterns, struct patterns, or tuple patterns.
`match` on a generic enum uses the plain enum name; the checker rewrites it to the
monomorphised instance.

### 13.2 `match`

```
match EXPR { ARM* }
ARM := (PATTERN | "_") "=>" BLOCK
```

The subject MUST be an enum (E008 otherwise). Patterns MUST belong to that enum, MUST NOT
duplicate, and MUST be exhaustive or include `_`. In statement position an arm body is a
**block**; the `=> 0,` form belongs to the 2026.3 expression form below. A payload binding
is in scope in its arm.

From Tur 2026.3, a `match` may be the whole initialiser of a `let`:

```
let x: T = match NAME { PATTERN => EXPR, … };
```

The subject MUST be a **name**, every arm body is a **single expression**, and arms are
separated by commas. It lowers to `let x: T;` followed by an ordinary `match` whose arms
assign. Other positions, expression subjects, nested `if` / `match` expressions in an arm,
and literal patterns are not accepted in this edition round (§8.10).

### 13.3 `if let`

`if let PATTERN = EXPR BLOCK (else BLOCK)?` tests one pattern. The pattern rules are those
of §13.1.

---

## 14. Entry points and program shape

### 14.1 `main`

`fn main()` takes no parameters and has no return type. The linker's entry stub calls it
and exits with code 0. A `main` with parameters is refused by both linkers: the stub
would pass register garbage. A `main` with a return type is refused.

### 14.2 `_start`

`fn _start()` is the freestanding entry. It takes no arguments and wins over `main` when
both exist. If it is linked as the process entry (`-Wl,-e,_start`), it has no return
address; it MUST end with a syscall (`syscall4(60, …)`), not `return`.

### 14.3 Commands

A unit may declare itself a `loment` command. Two equivalent spellings exist:

* `register NAME { FN }` where `FN` is exactly one function (it becomes `command_main`);
* `pub fn loment_command() -> str { return "NAME"; }` plus
  `pub fn command_main(argv: ptr, argc: u32) -> u32`.

A command unit MUST NOT also declare `_start`. The command entry signature is fixed:
`command_main(argv: ptr, argc: u32) -> u32`; `argv` is the whole `/proc/self/cmdline`
block. The declared name is `[A-Za-z0-9_-]`, 1..64 characters, and MUST NOT collide with
an official `loment` command. A library MUST NOT declare a command.

### 14.4 Interrupt handlers

`interrupt fn NAME(…) { … }` declares an interrupt handler (the `fn` is part of the
spelling). The name is resolved by the kernel line. A handler is a function with the
normal body rules.

### 14.5 External code blocks

`command LANG` / `foruse LANG` declare the languages used by raw blocks in the file.
`let LANG { … }` (or, after `foruse`, `LANG { … }`) carries the body as **raw bytes**,
not a string literal. Raw blocks are copied byte for byte; they are not lexed as Tur. This
is how NUL and non-UTF-8 bytes survive.

### 14.6 The shape of a program

A program has exactly one process entry: `main`, `_start`, or a command entry. A unit
that combines incompatible entries is refused (E025). A unit with no entry is a library
and cannot be built as an executable.

---

## 15. Generics, traits, and implementations

### 15.1 Generic functions and types

A generic parameter is written in angle brackets after the name: `fn f<T>(…)`,
`struct S<T> { … }`, `enum E<T> { … }`. Generic functions are monomorphised; there is no
erasure and no runtime dictionary.

### 15.2 Traits

```
trait NAME { fn METHOD(self, …) -> TYPE; … }
```

A trait declares method signatures only. The first parameter MUST be `self`. Traits are
used for **static dispatch**; there is no trait object, no `dyn`, and no vtable.

### 15.3 Implementations

```
impl TRAIT for TYPE { fn METHOD(self, …) -> TYPE { … } }
```

An `impl` method's first parameter MUST be `self`; the receiver type is the impl's type.
A method is called as `value.method(…)`. The implementation renames the receiver to an
internal name and mangles the method to `<Type>_<method>` in the artifact's symbol
convention (§28.2).

### 15.4 Limits

There is no `impl Trait`, no associated type, no default method body, no operator trait,
and no trait inheritance. A construct outside this section is not in this edition.

---

## 16. Units, imports, and name resolution

### 16.1 `use` forms

A unit may import another unit in two forms:

| Form | Syntax | Resolution |
|---|---|---|
| path | `use "relative/or/absolute/path"` | relative to the importing file's directory, then the repository root (or the working directory when there is no root); the suffix is part of the path |
| name | `use NAME` | the layered search of §16.3; the suffix is configured (§16.4) |

A file may contain at most **300** `use` lines of both forms combined; exceeding is E020
and MUST NOT be a silent drop.

### 16.2 Path form

The path is resolved against the importing file's directory first, then the build root,
then the working directory. A path ending in `.lom` is an **L0 interface** import
(§36.2); every other suffix is L1 source. A path-form import is not affected by the
project suffix configuration.

### 16.3 Name form

The search layers are:

1. the project's `deps/<name>/<name><ext>` (and the package-internal
   `deps/<package>/<name><ext>`);
2. the built-in roots (`loment/lib`, `loment/examples`, `loment/selfhost`,
   `loment/tools`), requiring a **unique** match;
3. the packaged store (`<prefix>/share/lompi/store/<package>/<version>/<name><ext>`), and
   the package-internal form inside the store, requiring a unique match.

Layers 1 and 1b always win. The order of layers 2 and 3 depends on where the importer
lives: an importer inside the store searches the store first; every other importer
searches the built-in roots first. A name that resolves to several modules is E018
("ambiguous"); a name that resolves to none is E018 ("not found"). The compiler does not
choose a version: the store layer admits exactly one version per package.

### 16.4 The project suffix

The suffix used by the name form is configured by the **project root**'s `loment.conf`:

```
module conf
pub fn source_ext() -> str { return ".foo"; }
```

The scanner is a lexical label scan, not a full parser: comments do not count. The rules
are: no file → default `.lomt`; no `source_ext` identifier → default; the first string
literal after it does not start with `.` → default; a value containing a backslash →
default; otherwise use the value. The default is **always** a fallback candidate, so a
custom suffix never loses the toolchain's own modules.

### 16.5 Unit assembly

The root unit's imports are loaded depth-first, in source order. A dependency is emitted
before the unit that imports it, once only. A cycle is an error. A symbol declared by the
unit MUST NOT collide with an imported symbol. A name-form import that cannot be resolved
MUST abort the build with a non-zero exit and name the import; it MUST NOT continue with a
missing dependency.

At the **end** of the assembled unit, the prelude enums are injected:

```
pub enum Option<T> { Some(T), None }
pub enum Result<T, E> { Ok(T), Err(E) }
```

They are injected once, after the module's own enums, and are visible to every unit.

### 16.6 The edition of the unit

The edition is decided by the root unit's declaration (§8.4). A dependency's own
declaration is stripped and does not change the edition. The collision rule (§8.7) is
applied to every loaded file that does not pin the effective edition.

---

## 17. Constants and literals

### 17.1 Constants

```
const NAME: TYPE = INT;
```

The initialiser is **one integer literal**, optionally negated. It is not an expression,
and there are no string constants. The type MUST be an integer type. A constant name MUST
NOT collide with a struct, function, or another constant (E013). A module-level constant
is visible inside function bodies.

### 17.2 Literal limits

An integer literal MUST fit the target type. A literal that does not fit is an error; it
MUST NOT wrap silently. Hex literals use the same width rules as decimal literals.

### 17.3 No constant folding of names

Only a literal is accepted where a constant value is required. A named constant is not a
literal and cannot be used in a position that requires a compile-time literal (for
example an array length or a `const` initialiser).

---

## 18. Built-in functions and the diagnostic set

### 18.1 Built-ins

The following are the **only** built-in functions. A name outside this table and outside
the user's declarations is E002.

| Signature | Meaning |
|---|---|
| `str_len(s: str) -> u32` | byte length |
| `str_byte(s: str, i: u32) -> u32` | the i-th byte |
| `str_eq(a: str, b: str) -> bool` | content equality |
| `str_concat(a: str, b: str) -> str` | concatenation; allocates from the runtime arena |
| `str_num(n: u32) -> str` | unsigned decimal representation; allocates from the runtime arena, so `no_runtime` refuses it by name |
| `str_ptr(s: str) -> ptr` | data pointer |
| `print(s: str) -> u32` | write the bytes to stdout |
| `tur_edition() -> str` | the resolved canonical edition |
| `alloc(n: u32) -> ptr` | allocate from the arena |
| `free(p: ptr) -> u32` | return a block to the allocator |
| `load8(p: ptr, off: u32) -> u32` | read a byte |
| `store8(p: ptr, off: u32, v: u8) -> u32` | write a byte |
| `ptr_add(p: ptr, n: u32) -> ptr` / `ptr_sub` | pointer offset |
| `slice_len(s: [T]) -> u32` | slice length |
| `panic(code: u32) -> u32` | does not return |
| `atomic_add(p: ptr, n: u32) -> u32` | atomic add |
| `get_bits(v: u8, shift: u32, width: u32) -> u8` / `set_bits(v: u8, shift: u32, width: u32, x: u8) -> u8` | bitfield access; the arguments are shift and width |
| `inb(port: u16) -> u32` / `outb(port: u16, v: u8) -> u32` | port I/O (translation path only) |
| `syscall4(nr: u64, a0..a2: u64) -> i64` | raw syscall |
| `syscall6(nr: u64, a0..a4: u64) -> i64` | raw syscall |
| `syscall7(nr: u64, a0..a5: u64) -> i64` | raw syscall with all six argument registers |
| `thread_spawn(name: str, stack_top: u64, arg: u64, done: u64) -> i64` | start a thread whose entry is named by a string literal |

`print_num` is a **Tur 2026.2+ built-in** (`(i64) -> u32`), not part of the Tur 1 table.
`inb` / `outb` are available only on the translation backend that maps them.

The built-in table is frozen (§6.1). Adding a built-in follows §6.2.

### 18.2 The names a built-in does not shadow

In Tur 1, `print_num` is an ordinary identifier; a user-defined `fn print_num` is not
shadowed. In 2026.2+, the built-in wins at a call site unless a user function of that name
is declared, in which case the user function is used. The collision rule (§8.7) protects
the older program that declared `print_num`.

### 18.3 Syscall numbers

On the Linux ELF target, syscall numbers are Linux's own (`write`=1, `exit`=60,
`openat`=257, …). On the Windows PE target, the implementation defines a **fixed set** of
29 numbers: the file/process eight (`read`=0, `write`=1, `close`=3, `brk`=12, `exit`=60,
`getdents64`=217, `openat`=257, `newfstatat`=262), the networking family (`poll`=7,
`select`=23, `socket`=41, `connect`=42, `accept`=43, `accept4`=288, `sendto`=44,
`recvfrom`=45, `sendmsg`=46, `recvmsg`=47, `shutdown`=48, `bind`=49, `listen`=50,
`getsockname`=51, `getpeername`=52, `socketpair`=53, `setsockopt`=54, `getsockopt`=55,
`epoll_wait`=232, `epoll_ctl`=233, `epoll_create1`=291). Any other number returns
`-ENOSYS` (-38), not `-1`. `/proc/self/cmdline` is synthesised by the PE runtime, so argv
reads the same way on both targets.

### 18.4 `comefor` built-ins

A `comefor` program may use exactly these four functions and no others:

| Signature | Meaning |
|---|---|
| `ct_n() -> u64` | tokens remaining from the cursor |
| `ct_tok(i: u64, buf: ptr) -> u64` | write the source token's record into `buf` |
| `ct_out(buf: ptr) -> u64` | append a token record to the output |
| `ct_syn(k: u64, txt: ptr, len: u64, line: u64, col: u64) -> u64` | synthesise a token |

A token record is 20 bytes: kind at +0 (0 ident, 1 number, 2 string, 3 punct, 4 eof),
offset at +4, length at +8, line at +12, column at +16.

### 18.5 Diagnostic codes (normative)

Every diagnostic MUST carry one of the following codes. The codes are grouped **by the
fix a user must make**, not by wording.

| Code | The fix class it names |
|---|---|
| E001 | Types do not line up (including return, payload, builtin arguments, and mixed-width operands). |
| E002 | Something is not declared (variable, function, struct, enum); a local used before its `let`. |
| E003 | Wrong number of arguments. |
| E004 | Capability-domain problem (undeclared, out of range, duplicate). |
| E005 | A space declared with `excluded` was used. |
| E006 | Value already moved. |
| E007 | Borrow conflict (mutable borrow alongside another borrow, or two mutable borrows). |
| E008 | `match` / enum error (not exhaustive, duplicate pattern, not an enum, wrong payload binding). |
| E009 | A name collides with a base type, or an empty struct/enum. |
| E010 | `?` used where the value is not `Result`/`Option`. |
| E011 | Slice mutability (writing through a read-only slice, or a parameter missing `mut`). |
| E012 | Dangling reference. |
| E013 | Duplicate definition or duplicate name. |
| E014 | Assignment target is not an lvalue. |
| E015 | Field or indexing error (no such field, missing field, duplicate initialisation, field access on a non-struct). |
| E016 | Array literal or length disagrees with the declaration. |
| E017 | Illegal `as` conversion. |
| E018 | `use NAME` does not resolve (not found, or several hits). |
| E019 | Syntax error (parse time). |
| E020 | More than 300 `use` lines in one file. |
| E021 | An `extern` signature outside FFI stage 1 (scalars and `ptr` only). |
| E022 | `choose` used wrongly (twice, misspelled value, a library wrote it, or a switch conflict). |
| E023 | Not implemented in this version (a compiler limit), including an IR lowering the backend cannot do. |
| E024 | A command declaration has the wrong shape, or tries to declare an official command. |
| E025 | `_start` and a command entry both exist, or another incompatible entry combination. |
| E026 | `command_main` without a declaration, with the wrong signature, or a declaration without the function. |
| E027 | A non-unit function can fall off its end. |
| E028 | A literal index is out of range for a fixed-size array (LMS level 1). |
| E029 | Use after free (LMS level 1). |
| E030 | Unknown name in a type position (distinct from E002: the fix is to declare the type). |
| E031 | A construct that would drag in the runtime while the artifact says `no_runtime`. |
| E032 | Cannot write the output file. |
| E033 | Unit too large / capacity exceeded. |
| E034 | LSPE: an allowance with no source (strict mode only). |
| E035 | LSPE ledger contradiction (unregistered site kind, mutually exclusive dimensions, twin disagreement, capacity overflow). |
| E036 | LSPE self-check failed. |
| E037 | LSPE policy lock (reserved; not emitted today). |
| E038 | `lspe pass` without a reason. |
| E039 | `lgc` requires a collector, but the unit chose `no_gc`. |
| E040 | An undeclared unit declares a name that a newer edition treats as syntax (§8.7). |

`E900` (the tool died without writing a diagnostic) and `E999` (unclassified; please
report) are internal and MUST NOT be produced for a well-formed program.

A diagnostic MUST include a source position (line, and column or token) except for the
file-level codes E020, E032, E033, and E035–E037, which name the file instead.

---

# Part III — Semantics and safety

## 19. Type checking and static rules

### 19.1 The checking model

An implementation MUST check a unit as a whole before emitting anything. The checker
walks items, builds a symbol table per unit, and then checks each function body with:

* the unit's functions, structs, enums, traits, constants, capabilities, and imported
  symbols;
* the function's parameters and locals;
* the prelude enums;
* the edition and switch values resolved for the unit.

A name MUST be declared before use. A function MUST be declared before it is called within
the unit; imports are assembled before checking.

### 19.2 The static rules

The following table is normative. Each row names the rule, the code, and the boundary.

| Rule | Code | Boundary |
|---|---|---|
| Every variable is declared before use; a local cannot be used before its `let`. | E002 | Module-level names are visible in bodies; parameters are in scope for the whole body. |
| A called function exists and the argument count matches. | E002 / E003 | Overloading does not exist; one name, one signature. |
| Every expression's type matches its context (binding, assignment, return, argument, field, index, condition). | E001 | Integer literals are typed by context (§10.6). |
| `if`/`while` conditions are `bool`; `&&`/`||`/`!` operands are `bool`. | E001 | `bool` and integers do not mix. |
| Return type matches; a non-unit function cannot fall off the end. | E001 / E027 | Every path must return. |
| Struct literals initialise every field exactly once; field access names an existing field. | E015 | Field shorthand is 2026.3+ only; missing/duplicate/unknown fields remain E015. |
| Array literals are non-empty, homogeneous, and match the declared length. | E016 | `N` is a positive constant. |
| Indexing applies to arrays/slices and an integer index. | E001 / E014 | Bounds are not checked by the type system; E028 catches literal out-of-range. |
| Assignment targets are lvalues. | E014 | Variable, field, index. |
| Enum patterns belong to the subject enum, do not duplicate, and are exhaustive or use `_`. | E008 | Payload binding rules of §13.1. |
| A moved value is not used again. | E006 | The ownership subset of §20.3. |
| A borrow conflict is rejected. | E007 | The borrow rules of §20.4. |
| A reference does not outlive its source. | E012 | The region rules of §10.9 and the LSPE obligation `NotDangling`. |
| Capability indices are in range; a capability is declared; duplicates are rejected. | E004 | §21.5. |
| A declared capability space is not `excluded`. | E005 | §9.4. |
| `?` applies to `Result`/`Option`. | E010 | §12.7. |
| Slice mutability matches the parameter (`[T]` vs `mut [T]`). | E011 | §10.7. |
| Names do not collide with base types; structs/enums are non-empty. | E009 | §9.3. |
| A constant's value is one integer literal and its type is integer. | E019 / E001 | §17. |
| `extern` signatures use scalars and `ptr` only. | E021 | §28.5. |
| `choose` values are known, at most once per dimension, and only in the root. | E022 | §25. |
| A literal index is within a fixed array's bounds. | E028 | LMS level 1. |
| A freed pointer is not used. | E029 | LMS level 1. |
| A type position names a declared type. | E030 | Distinct from E002. |
| A runtime construct is not used under `no_runtime`. | E031 | §25.4. |
| `lgc` is not used under `no_gc`. | E039 | §22.7. |
| An undeclared unit does not declare a collision name. | E040 | §8.7. |

### 19.3 Type equality

Two types are equal when they have the same constructor and the same arguments. There is
no subtyping, no implicit conversion, and no structural equivalence between a named struct
and an anonymous aggregate. A generic instance is equal only to the same instance.

`()` is a type and is the type of a function with no return type. `()` is not spelled as a
type name in source; it is implied by the absence of `-> T`.

### 19.4 Names and scopes

The unit's top-level namespace is flat. Within a function, parameters and locals share one
scope; a nested block does **not** introduce a new binding scope (§11.1). A local
declaration may shadow an earlier local; the reference implementation and the self-hosted
one have a recorded divergence for the same-name double-`alloca` case (§39).

Module-level constants are visible in bodies. Module-level functions are visible after
their declaration. Imported symbols join the unit's flat namespace; a collision is E013.

### 19.5 What the checker does not do

The checker does not prove general memory safety, does not perform information-flow
analysis, and does not replace LSPE. It enforces the rules of §19.2; the guarantees beyond
them are those of LGC (§22), LMS (§23), and LSPE (§24).

---

## 20. The memory model

### 20.1 Values

Loment values are:

* scalars (integers, `bool`), copied on assignment;
* `str` (pointer + length), a value; copying it does not copy the bytes;
* `ptr`, Copy and unchecked;
* arrays and slices, by value or by borrow according to their type;
* structs and enums, by value;
* references (`&T` / `&mut T`, 2026.3+), which borrow.

There is no garbage-collected object identity in the language; a struct is a value, not a
reference.

### 20.2 Allocation

The only language-level allocator is `alloc(n) -> ptr`. It returns a block from the
program's arena (64 KiB under `sealed`, 1 MiB under `hosted`; §25). `free(p)` returns a
block to the address-ordered free list. The allocator is a bump pointer plus that free
list: it does not coalesce and cannot grow past the arena. Exhaustion is a hard failure,
not a null return.

### 20.3 Ownership and moves

The language has a **small ownership subset**, not a Rust-style borrow checker:

* a value assigned to a new binding is moved when its type is not Copy;
* using a moved value is E006;
* a `ptr` is Copy and is not tracked;
* arrays and structs are values; assigning one copies it unless the implementation's
  move analysis has marked it moved.

The subset is intentionally small. Its boundary is stated in §39. An implementation MUST
reject the cases the subset covers and MUST NOT claim the cases it does not.

### 20.4 Borrowing

A borrow appears:

* in call arguments: `&xs` for a `[T]` parameter, `&mut xs` for a `mut [T]` parameter;
* in 2026.3: as a reference expression and in reference types (§10.9).

A shared borrow and a mutable borrow of the same place may not overlap (E007). Two mutable
borrows may not overlap (E007). A reference may not outlive its source (E012). The
implementation enforces these rules to the depth described in §20.3 and §24; it MUST
report a case it cannot decide as undecided, never as safe by default.

### 20.5 Drops

There is no `Drop` trait and no destructor in the language. `free` is an explicit call.
The LSPE obligation `DropHonest` is discharged only by `Assumed` or by a developer-written
`drop:<type>=prove` marker; it is the floor of the ledger (§24.6).

### 20.6 The arena and the root table

Under the automatic tiers, the collector uses a **precise root table**: the compiler emits
the addresses of root slots, so a value that merely looks like a pointer is not followed.
The table has a fixed capacity (1024 slots in the current implementation); overflow is
refused by name, never silently written past.

---

## 21. Capability domains

### 21.1 What a domain is

A capability domain is a predicate over an **index**. `guard CAP(i);` evaluates `i` and
either passes or traps. A passing guard increments the audit table for that capability.
The core soundness assertions are:

* **S1 (in-domain):** a guard that returns normally has an index inside the domain.
* **S2 (audit fidelity):** the audit counter equals the number of passing guards.
* **S3 (no side effect on the failing path):** an out-of-range index traps and changes
  no user-visible state.

These are implementation-level assertions checked by tests, not a machine-checked proof.

### 21.2 Declaration

```
capability NAME : SPACE [ SHAPE ] LAWS? [revocable]
```

`SPACE` is an identifier. `SHAPE` is one of:

| Shape | Written as | Meaning |
|---|---|---|
| regular | `[a..b]` | `a ≤ i ≤ b` |
| point | `[a..a]` | only `a` |
| upper-bounded | `[()..b]` | `i ≤ b` |
| lower-bounded | `[a..()]` | `i ≥ a` |
| full | `[()..()]` | every index |

`LAWS` is zero or one **law**, from §21.3. `revocable` is a declaration flag; the language
records it but does not implement revocation (the kernel side owns that, §21.10).

**Capability names are unit-level.** A dependency's declaration and the entry's
declaration are merged into one table; a `guard` resolves to the declaration in the file
that declared it, with that file's bounds. Declaring the same name in two files is E004,
reported at the later declaration. An entry's wider range MUST NOT silently override a
dependency's narrower one.

### 21.3 The laws (normative)

A law refines when a guard passes. The kinds are:

| Law | Written as | Passes when |
|---|---|---|
| epoch | `epoch(N)` | `now < N` |
| far | `far(W)` | `i` is in `[origin, origin+W)`, where `origin = floor(now/W)*W` |
| sliding | `sliding(W)` | `i` is in `[now-W, now]` |
| cyclic | `cyclic(P)` | `(i mod P)` is in the shape; `P > hi` is required |
| decaying | `decaying(T)` | `now ≤ T` |
| growing | `growing(G)` | `i` is within the growing end |
| adaptive | `adaptive` | `i ≤ lo + (hi−lo)·q/255`, where `q` is set by `quality cap = q;` (0..255, default 255) |
| generational | `generational(N)` | the shape holds, but the polarity flips every `N` ticks |
| subject | `subject(MASK)` | the shape holds and bit `aux` is set in `MASK`; `guard cap(i, aux);` |
| witnessed | `witnessed(T)` | the shape holds and ticket `aux` has been used fewer than `T` times; a pass increments it |
| flow | `flow` | the packed edge `(i << 16 | aux)` is in the shape; `i` is the source, `aux` the target |
| reachable | `reachable(K)` | the position is free; a pass reserves it for `K` ticks; `discharge cap(i);` clears it |
| revision | `revision` | the slot's **object generation** equals `aux`; `guard cap(i, aux);` |
| consumed | `consumed(N)` | each position passes at most `N` times |
| danger | `danger` | the index is **outside** the shape (a literal inside is a compile-time error) |

The `regular` kind is the base shape with no law. `revocable` is not a law; it is the
declaration flag of §21.2.

### 21.4 Arguments and state

* `epoch`, `far`, `sliding`, `decaying`, `growing`, `consumed`, `cyclic`, `generational`,
  `subject`, `witnessed`, and `reachable` take one integer parameter.
* `subject`, `witnessed`, `flow`, and `revision` require a **second guard argument**:
  `guard cap(i, aux);`. Every other law MUST reject a second argument.
* `consumed`, `adaptive`, `witnessed`, `reachable`, and `revision` keep **per-position
  state**. Their domain MUST be bounded and at most 64 positions wide.
* `revision` is the **object generation** (`docs/320`): `generation cap(i)` reads it,
  `retire cap(i);` increments it, and `guard cap(i, g)` passes only when the slot's
  current generation is `g`. In this edition it is available on top-level
  `capability` declarations only.
* `cyclic` requires a bounded shape and `P > hi`.
* `generational`, `adaptive`, and `flow` require a bounded shape.
* `quality` applies only to `adaptive`; `discharge` applies only to `reachable`.
* A law's parameter MUST be in `(0, 2^24)`.
* Static literal checking covers `regular`, `danger`, and `cyclic` (after wrapping). The
  other laws depend on the clock or a second argument and are checked at run time.

### 21.5 `guard`

```
guard CAP(EXPR);
guard CAP(EXPR, AUX);   // subject / witnessed / flow / revision only
```

For a `revision` domain the second argument is the **expected object generation**
(`docs/320`). `generation CAP(EXPR)` is an expression of type `u64` that reads the
slot's current generation; `retire CAP(EXPR);` increments it in O(1). A guard with a
stale generation traps, and only a passing guard increments the audit entry.

An implementation MUST:

1. evaluate the index (and the auxiliary argument where the law takes one);
2. if the index is a literal and is outside the domain (or inside a `danger` domain), emit
   E004 at compile time;
3. otherwise emit a runtime check at the judgment point
   `__loment_guard2(cap, idx, aux, lo, hi, kind, param, flags)`;
4. on failure, trap with no other effect; on success, increment the audit entry.

**`guard` needs the runtime.** A guard under `no_runtime` is E031, because the audit table
is runtime state. The same applies to any construct that pulls in the runtime block.

### 21.6 Scoped domains

```
domain [NAME :] SPACE[SHAPE] LAWS? [revocable] [child_of PARENT] { BLOCK }
domain [NAME :] borrow(VALUE) { BLOCK }
domain [NAME :] borrow_mut(VALUE) { BLOCK }
```

A `domain` block introduces a lexical domain:

* an index into a value whose type carries the block's space is guarded implicitly;
  an explicit `guard` at the same position is the same decision point and MUST NOT double
  count;
* the domain name is not visible outside the block;
* `child_of PARENT` requires the parent to be lexically enclosing, the same space, and the
  child's shape to sit inside it;
* the block form is the local form; a top-level `capability` still covers the whole unit.

A value's space is tagged in its type: `let a: [u32; 64] in disk` or a parameter
`a: [u32; 64] in disk`. The tag applies to arrays, slices, and pointers. It does not travel
through a call: a callee sees the plain type.

### 21.7 Domain loops

```
domain:for i [, j] [in NAME [, NAME]] [step [-N]] { BLOCK }
```

* With no `in`, the loop walks the innermost enclosing domain; the loop variable is guarded
  once at the head, and the body does not repeat the guard.
* With `in NAME`, it walks the named domain. The name may refer to an outer domain; in
  that case a same-space index in the body is **not** exempt, because the innermost domain
  may be narrower.
* `step N` walks every Nth position; `step -N` walks backwards from `hi`.
* Multiple variables and domains walk in parallel; their spans MUST match.
* Only bounded, orderable domains can be walked. An unbounded side, `danger`, and
  `subject` / `witnessed` / `flow` (which need a second argument) are rejected.
* Every back edge advances the virtual clock. `choose domain_clock_explicit` disables the
  automatic tick; then only `tick;` advances it.

### 21.8 Groups, solid bodies, and trees

* `couple A, B, …;` makes one renewal unit: a guard on any member silently checks the
  others at the same position, and a `quality` write goes to every adaptive member.
* `mutex A, B, …;` is a compile-time promise that the domains are finite, non-danger, and
  disjoint. It emits no code.
* `solid NAME : A, B, …;` names a body: `guard NAME(x)` judges the root member and checks
  the rest silently; `quality NAME = q;` writes the group. A body's audit entry lands on
  its root member.
* Nested blocks form the vertical tree. `child_of` states an edge.

### 21.9 The virtual clock

`tick;` increments `__loment_now` by one. Every time law except `consumed`, `witnessed`,
and `reachable` is a pure function of `now` (and the position). A domain loop's back edge
is a tick unless `choose domain_clock_explicit` is set.

### 21.10 Honest boundaries

* `guard` constrains the **index**; it is not authorisation. It does not ask "does the
  current principal hold this capability"; that binding is on the kernel side.
* `revocable` is a flag in the declaration and the domain table; revocation is not
  implemented in the language. `revision` is different: its generation table and
  `generation` / `retire` are implemented (`docs/320`), but only for bounded top-level
  capabilities, and only inside the guarded subset.
* `subject`'s subject number and `witnessed`'s ticket number are **caller-supplied
  arguments**, not unforgeable tokens.
* `flow`'s shape is an interval over packed edges, not an arbitrary edge set.
* `reachable` is the dynamic form: registration, deadline, and discharge; it is not a
  bounded model check.
* Nothing in the language gates the operations that do things: `syscall4`/`syscall6`,
  `alloc`/`load8`/`store8`/`ptr_add`, and FFI are raw. A capability declaration says what a
  domain is; it does not stop a raw pointer.

---

## 22. LGC: reclamation

### 22.1 The ladder

LGC (Loment GC) has five rungs. The compiler tries the **strongest first**; every `alloc`
site is proved into the highest rung it qualifies for.

| Rung | What is proved | Who reclaims | Cost |
|---|---|---|---|
| **L0 static** | `let p: ptr = alloc(<integer literal>)` and `p` is used only in that declaration and as the whole argument to a callee whose parameter is non-escaping | the stack; the block never enters the heap | a larger frame |
| **L0.5 shared frame area** | L0's candidate, but a function has at least two such sites; their sizes are summed | one frame area shared by the function | `L05_MAX = 2048` bytes; first-declared wins |
| **L1 last use** | the declaration is at function-body top level and the pointer only passes to safe parameters | the compiler inserts `free` after the last use, before the earliest `return` | an early-return path the insertion cannot reach leaks once |
| **L2 epoch** | inside a `while`/`for` body, every `alloc` is a `let` whose name never leaves the body | the body is unwound in bulk at its end (pointer rollback) | one escaping allocation cancels the whole body's rung |
| **L3 runtime** | nothing above applies | the collector | the collector's cost |

### 22.2 The tiers

| `choose gc` | L0 | L0.5 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| `gc_manual` (default) | — | — | — | — | no automatic reclamation; a manual `free` works |
| `gc_auto` | — | — | — | — | mark-sweep with precise roots |
| `gc_auto_alpha` | yes | yes | yes | yes | adaptive + generational |
| `no_gc` | — | — | — | — | declares the artifact carries no collector; the linker verifies it |

**`gc_manual` is byte-for-byte the pre-tier behaviour.** Choosing a tier is the only way to
get a collector. A default MUST NOT change an existing program.

### 22.3 Triggers and roots

* `gc_auto` triggers every 32 KiB of allocation; its arena is 64 KiB, so a live set above
  about 32 KB exhausts. This is a limit of the tier, not the language.
* `gc_auto_alpha` follows the live set and adds a space trigger; it raises the effective
  ceiling to "live set close to the whole arena".
* Roots are precise: the compiler emits a slot table of root addresses. The table has
  1024 slots; overflow is refused by name.

### 22.4 `free`

`free(p)` returns the block to the allocator (bump pointer + address-ordered free list).
It does not coalesce and cannot grow past the arena. A double free or a free of a
non-block is not defined by the language; LMS and LSPE record what they can.

### 22.5 `no_gc`

`no_gc` says "the artifact carries no collector", not "no allocation". The IR carries a
`loment-gc: no_gc` claim; both linkers MUST refuse an artifact that contradicts it (for
example an artifact that carries `@__loment_collect`).

### 22.6 What the artifact records

The form object records the tier (`gc`) and the ladder (`gc_ladder`). From Potato v19 the
ladder has five components and the self-consistency rule is
`l0 + l0_5 + l1 + l2 + l3 == total_sites`. Older objects keep four components and remain
valid; the ladder only grows forward.

### 22.7 `lgc` (2026.3)

`lgc end` pauses the collector; `lgc start` resumes the tier default; a window restores
the previous state on exit, including an early `return`. `lgc start(DURATION)` sets a
cooperative budget: a new collection will not **start** while `now >= deadline`; a running
collection is not preempted. `lgc` under `no_gc` is E039. The `lgc` construct is available
in 2026.3 only.

---

## 23. LMS: memory safety

### 23.1 Two directions

`gc` answers **when memory is returned** (inward reachability). `lms` answers **who may
touch it** (outward permission). They are orthogonal: `no_runtime` + `lms_full` is legal
because every LMS level is instructions or out-of-process, not a runtime component.

### 23.2 The seven levels

| Level | Name | What it does | When |
|---|---|---|---|
| 1 | checker | compile-time E028 (literal index out of bounds) and E029 (use after free) | always (E028); E029 under `lms_check` |
| 2 | manager | a memory report: every `alloc` site with unit/function/line/size | compile time |
| 3 | guard | inline bound check on `load8`/`store8` from `let p: ptr = alloc(<const>)`; only direct pointers | runtime, instructions only |
| 4 | reject | an out-of-range access is skipped (load returns 0, store does nothing); the program continues | runtime |
| 5 | crash | `llvm.trap`; the process dies with exit 132 | runtime |
| 6 | restart | an out-of-process restarter restarts the process, up to a limit | out of process |
| 7 | seed | the bootstrap seed plus its name, byte size, and hash; the trusted floor is named and measured | out of process |

Level 3 checks only direct pointers; it does not check derived pointers, slices, or struct
fields. Level 5 cannot kill other processes under `no_std`; it exits itself and lets the
OS reap it.

### 23.3 The tiers

The four official combinations are:

```
lms_full               all seven (default)
lms_forensic_admin     1 + 2 + 3 + 4
lms_daily_admin        1 + 3 + 4
lms_manage_only_admin  2
```

The direction is reversed: the default is full, and writing a tier is a **downgrade**.
Every non-full value carries `_admin`. The fine switches (`lms_check`, `lms_manage`,
`lms_guard`, `lms_reject`, `lms_crash`, `lms_restart`, `lms_seed`) seed a tier; an explicit
switch overrides.

### 23.4 Budgets and `no_runtime`

Potato v17 records the seven level booleans (`lms_levels`); v18 adds one budget per level
(`lms_budgets`: `none` / `instructions` / `state` / `outside`). If a level ever needs a
runtime state table, its budget changes to `state` and `no_runtime` is refused **by
name** at that moment. LMS introduces no runtime symbol today.

### 23.5 What LMS does not do

It does not prove memory safety in general; it enforces the listed checks. It does not
check derived pointers, slices, or struct fields at level 3. Rebuilding from a shadow copy
is not implemented.

---

## 24. LSPE: the audit ledger

### 24.1 What LSPE is

LSPE (Loment Safety Police Espace) is not a seventh checker. The enforcers stay as they
are (LGC, LMS, capability domains, the runtime/port dimensions, the ownership subset).
LSPE adds two things: **why an allowance was granted**, and **a record of that judgement in
the artifact**. It audits the compiler's own decisions; it does not judge the user.

The four parts are:

| Part | When | What it does |
|---|---|---|
| Questioner | during compilation, inward | audits the compiler's judgement |
| Counter-asker | during compilation, hand-off | gives a named hole to the developer: options, default, consequence |
| LSPC | before compilation | posts the residual risk |
| Ledger | in the artifact | records who opened, closed, and signed |

### 24.2 The dimension

`choose lspe_open_admin` (default) turns the audit on; `choose lspe_close_admin` turns it
off. Closing turns off the police, not the law: every check still runs. The value carries
`_admin` because closing a safety system is an administrative act.

### 24.3 Sites and evidence

A **site** is one allowance point: `guard cap(i)`, an array or slice index, a `free`, an
alloc-derived load/store, or a call. Site ids are content-addressed
(`unit:kind:fnv8(text#occurrence)`), not line numbers, so formatting does not renumber
them.

The **eight evidence kinds** are `Axiom`, `Syntactic`, `Typed`, `LocalFlow`, `Interproc`,
`GlobalInvariant`, `Assumed`, `Differential`. They form a finite lattice by inclusion.
**`Differential` is never in an accept set**: the twin proves agreement, not correctness.

### 24.4 Obligations (normative)

| Obligation | Owner | Accepted evidence |
|---|---|---|
| `InBounds` | E028 | Syntactic, Typed, LocalFlow, Interproc, GlobalInvariant, Assumed |
| `Alive` | E029 | LocalFlow, Interproc, Assumed |
| `InDomain` | §21 | Syntactic, Typed, GlobalInvariant, Assumed |
| `Exclusive` | E007 | LocalFlow, Interproc, Assumed |
| `Initialized` | E006 | Syntactic, LocalFlow, Interproc, Assumed |
| `NotDangling` | E012 | LocalFlow, Interproc, Assumed |
| `NoRuntime` | E031 | Axiom, Syntactic, Typed |
| `FreeOwnership` | §22 | LocalFlow, Interproc, Assumed |
| `LadderLiability` | §22 | Typed, Interproc, GlobalInvariant, Assumed |
| `CapabilityLive` | §21 | Typed, GlobalInvariant, Assumed |
| `TypeWellFormed` | §19 | Axiom, Typed, Interproc, Assumed |
| `DropHonest` | §20.5 | Assumed, Syntactic |
| `GuardHolds` | §21 | Syntactic, Typed, GlobalInvariant, Assumed |
| `SummaryContract` | §22 | Axiom, Interproc, Assumed |

### 24.5 Levels

| Level | Sees | Typical proofs |
|---|---|---|
| L0 | the site plus constants | literal bounds, type domains |
| L1 | the function's CFG | initialisation, liveness, exclusivity, local WF |
| L2 | call edges and summaries | cross-function deref-only, free ownership |
| L3 (default) | whole-unit fixpoint | loop invariants, cross-block flow |
| L4 | cross-unit summaries | library contracts |

Lowering the level does not change a verdict; it changes how many sites can be proved, and
`floors` goes up. The level is written into the artifact as `lspe.level`.

### 24.6 The ledger

`lspe_open_admin` writes one object per unit, with at least: `state`, `set_by`, `mode`,
`level`, `sites`, `obligations`, `proved`, `undecided`, `deferred`, `refused`, `floors`,
`floor_detail`, `per_kind`, `passes`, `pass_ratio`, `invariants`, `acked`.

The honest reading is: **`proved` is "this many sites had at least one accepted evidence
item at this level"**, not "the compiler proved the program safe". The full proof search is
later work (§39).

### 24.7 `lspe pass`

`lspe pass "REASON" { … }` is a named, recorded bypass. The reason is mandatory; a missing
or blank reason is E038. The body is flattened into the surrounding statement sequence.
Sites inside are still counted; they get `covered_by`, the solver does not prove them, and
only `pass_ratio` rises. The pass ratio is a ratchet: adding a bypass requires regenerating
it. A bypass does not propagate across calls.

### 24.8 Questions and invariants

The question set is `Q001`–`Q014`: no source for an allowance, an inert check, twin
disagreement, stale well-formedness, budget exhaustion, interprocedural depth, destructor
honesty, unknown domain, uncertain aliasing, ambiguous free ownership, a runtime leak, an
expired assumption, an unregistered site, and the AI-checker slot. Questions do not block
unless strict mode upgrades them.

The ledger's five invariants: I1 every site kind is registered; I2 every allowance has a
source or an `assumption` label; I3 the dimensions do not contradict; I4 the floor is
countable; I5 twin sameness. Only I1 is evaluated today; I2–I5 are emitted as `not_run`
(§39).

### 24.9 What LSPE does not do

It does not change a verdict, does not change the IR, and does not replace any enforcer.
Two artifacts that differ only in `lspe` are byte-identical; only the ledger differs. The
full derivation trees, the policy lock, and `--lspc` are not shipped.

---

## 25. Switches (the `choose` dimensions)

### 25.1 The dimensions (normative)

Loment has **eight** dimensions. Each selects a property of the whole program.

| Dimension | Values | Default | What it selects |
|---|---|---|---|
| `mode` | `std`, `no_std` | `std` | the layer the program may use |
| `gc` | `gc_manual`, `gc_auto`, `gc_auto_alpha`, `no_gc` | `gc_manual` | who reclaims memory |
| `runtime` | `runtime`, `no_runtime` | `no_runtime` | whether the artifact may carry the runtime block |
| `port` | `sealed`, `hosted` | `sealed` | whether the artifact may link host libraries |
| `vm` | `vm`, `no_vm` | `no_vm` | the execution model |
| `lms` | `lms_full`, `lms_forensic_admin`, `lms_daily_admin`, `lms_manage_only_admin` | `lms_full` | which memory-safety levels are on |
| `lspe` | `lspe_open_admin`, `lspe_close_admin` | `lspe_open_admin` | whether the audit ledger is recorded |
| `lifetime` | `lifetime_no`, `lifetime_auto`, `lifetime_manual` | `lifetime_auto` from 2026.3; `lifetime_no` before | compile-time borrow regions |

The `lifetime` default is **edition-dependent**: `core_default("lifetime", profile)` is
`lifetime_auto` for `tur2026.3` and later, and `lifetime_no` for the frozen editions. An
implementation MUST NOT read the `CORE_DEFAULTS` cell directly; it MUST apply the edition
rule.

### 25.2 Invariants

1. **One value per dimension per program.** Writing a dimension twice is E022, and the
   diagnostic MUST name the dimension.
2. **The root unit carries the switches; no other unit does.** A library that writes
   `choose` is E022, naming the file.
3. **Every dimension has a default, and writing nothing gives it.** A default MUST NOT
   change the behaviour of a program that already compiles. The `lms_full` default is a
   recorded exception (§23.3); the `lifetime_auto` default is gated by edition.
4. **Every dimension is recorded in the form object.**

### 25.3 Value names say *whether*, not *which*

A value states presence or absence, never a level or version of its contents. `runtime` is
`runtime` or `no_runtime`; `vm` is `vm` or `no_vm`. The contents grow over years; a name
that encoded today's contents would need renaming to admit tomorrow's, and renaming a
value is a change to the frozen surface.

### 25.4 Contradictory pairs

A program that asks for a contradictory pair is refused, and the diagnostic MUST name both
values and the reason. The pairs are:

| Pair | Kind | Why |
|---|---|---|
| `gc_auto` · `no_runtime` | definitional | automatic reclamation is the runtime |
| `gc_auto_alpha` · `no_runtime` | definitional | the hybrid tier depends on the runtime more, not less |
| `no_std` · `hosted` | definitional | `no_std` says nothing below; `hosted` reaches below |
| `no_std` · `gc_auto` | narrowed | the core would have to carry a collector; opens with a constrained subset |
| `no_std` · `gc_auto_alpha` | narrowed | the same, on the hybrid tier |
| `gc_auto_alpha` · `hosted` | measured boundary | winding the allocation front back leaves foreign pointers dangling |

**Not contradictory:** `gc_manual` with `no_runtime` is a legal position (a host service
or a thread may exist while the collector does not).

### 25.5 Switches are whole-program

A `choose` in a library is E022. A dependency expresses what it needs through capability
requirements; the project decides what it gives. A switch value is recorded in the form
object, so the artifact answers "which switches are on" without the source.

### 25.6 User switches

A program may define its own switches:

```
set choose NAME { ITEMS }
choose NAME          // on
choose close NAME    // off
```

A switch's body is compiled only when it is on; when off, its tokens are removed before
parsing, so names in it need not exist. A name MUST be defined before use; a file may carry
at most 500 switches; a switch declaration may not nest. A unit pulled in by `addin` may
contain only switch-related code (`module`, `addin`, the three `choose` forms). Only the
root unit may write `addin`.

### 25.7 Slots

A dimension may carry a **slot** in the second position: `<dimension>_<slot>_<value>`. A
slot is orthogonal to the dimension value; writing it does not count as a second value. The
only slot today is `lspe_aicheck_true` / `lspe_aicheck_false` (default `false`), which
turns on an external AI checker as a question source. A slot MUST be recorded in the form
object.

### 25.8 The channel

Which switches a release build exposes is a product fact, not a language fact. A channel
MAY refuse a switch whose contents are not implemented; it MUST NOT accept it and ignore
it. This standard states no channel behaviour. The authoritative channel constant lives
with the implementation, and an implementation MUST publish its channel policy.

---

## 26. Editions and compatibility

### 26.1 The compatibility promise

For a frozen edition E:

* every program that was well-formed under E remains well-formed;
* its meaning is unchanged;
* its artifact metadata names E;
* a new construct does not enter E.

The promise is about programs, not about the set of rejected non-programs (§8.6).

### 26.2 Compatibility across editions

An implementation that supports several editions MUST keep their surfaces separate:

* a construct of edition N is accepted only under an edition that contains it;
* a keyword of edition N is not a keyword under an older edition;
* a program's edition is the root's declaration, and the collision rule protects
  undeclared units.

### 26.3 The feature matrix

| Feature | 2026.1 | 2026.2 | 2026.3 |
|---|---|---|---|
| Base Tur syntax | yes | yes | yes |
| `def`, `elif`, `and`, `or`, `not` | — | yes | yes |
| `for x in xs`, `break`, `continue` | — | yes | yes |
| `for i, x in xs` | — | — | yes |
| `for x in <call>` (array/slice return) | — | — | yes |
| `if` expression (let initialiser) | — | — | yes |
| `match` expression (let initialiser) | — | — | yes |
| bare `return;` in unit functions | — | yes | yes |
| `orelse`, `catch` | — | yes | yes |
| `print_num` | — | yes | yes |
| `defer` | — | yes | yes |
| `lgc` | — | — | yes |
| `lifetime` dimension | — | — | yes (default `lifetime_auto`) |
| `&T` in type position | — | — | yes |
| struct literal shorthand `S { a }` | — | — | yes |
| domain blocks, laws, loops | additive | additive | additive |

"Additive" means the construct was previously a syntax error and becomes accepted without
changing a conforming program. An implementation MAY accept it in an older edition only if
it does not change the meaning of a conforming program; the frozen editions' guarantees
still hold.

### 26.4 The edition registry

The canonical edition list, the frozen set, and the collision table are part of this
document (Appendix D). An implementation MUST NOT invent an edition name. A new edition is
opened by the procedure of §6.2, and the default moves to it.

---

# Part IV — Artifacts and execution

## 27. The artifact model and targets

### 27.1 What an artifact is

An artifact is the output of a compiler: a native executable, a relocatable object, or a
bare-metal image. It MUST be a real target format, not a bytecode or a source bundle. The
language does not define a bytecode; the LVM (§30) is a separate reader of the same IR.

### 27.2 The targets

An implementation MUST declare the targets it supports. The targets defined by this issue
are:

| Target | Status | Notes |
|---|---|---|
| `x86_64` ELF | supported | Linux syscall ABI; the primary target |
| `x86_64` PE | supported | a fixed 29-syscall shim; no PE-side FFI, no PE threads, no PE process bridge |
| `aarch64` ELF | emission only | the backend emits; it has not been executed on hardware or an emulator |
| `wasm32` | planned | not a language feature of this issue |

An implementation MUST NOT silently downgrade a requested target. A target it does not
support MUST be refused by name.

### 27.3 Entry

The artifact's process entry is one of:

* the language's `main` stub: call `main`, then exit 0;
* `_start`: used directly when the program is freestanding;
* a command entry: read `/proc/self/cmdline`, parse the numeric fields, call
  `command_main`, exit with its return value.

The entry MUST be generated by the toolchain; a program MUST NOT rely on a hand-written
entry except `_start`, which is the freestanding contract.

### 27.4 The edition marker

Every artifact MUST carry an externally visible marker:

```
@loment.tur.edition = "LOMENT-TUR:" + <canonical edition> + NUL
```

The marker MUST NOT be a private constant that an optimiser may drop. Its size is 21 bytes
for the current 9-character edition names. A reader MUST be able to recover the edition
from the artifact without the source; `loment version <artifact>` is the reference
behaviour.

### 27.5 Self-containment

Under `sealed`, the artifact MUST be self-contained: no host library, no dynamic loader,
no import of a symbol the artifact does not define. Under `hosted`, the artifact MAY
depend on the host's loader and libraries, and MUST declare that dependence in its
headers and in the form object.

The port dimension records intent; the launcher and linker enforce the path. A name that
nobody provides is a **link error**, not a run-time crash.

### 27.6 Refusal, not truncation

If an artifact cannot be emitted — a construct the backend cannot lower, a unit too large,
a target it does not support — the implementation MUST refuse with the assigned code and
emit no artifact. A truncated artifact is a conformance defect.

---

## 28. Symbols, ABI, and calling conventions

### 28.1 The symbol model

The unit's top-level namespace is **flat**: a top-level name is unique within the unit, and
no name mangling is applied to ordinary functions. This is a cross-line ABI: the kernel and
C fixtures look up entry names by name. Renaming one is a contract change.

### 28.2 Implemented names

An `impl` method is mangled as `<Type>_<method>`. The receiver is renamed to an internal
name and its type is the impl's type. The mangling is part of the artifact contract; a
caller sees the method syntax, not the symbol.

### 28.3 Calling conventions

Two conventions coexist:

* **Loment to Loment:** the language's own convention; arguments are passed on the stack.
* **At an `extern` call site:** the platform C ABI. On Linux x86_64 (System V), the first
  six integer arguments go in `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9`; the return is in
  `rax`. On Windows x86_64, the first four go in `rcx`, `rdx`, `r8`, `r9`.

An implementation MUST dispatch on whether the callee is `extern`. Mixing the conventions
is a silent miscompile and is forbidden.

### 28.4 Entry names

`main`, `_start`, `command_main`, and interrupt handler names are looked up by name by the
entry stub or the kernel line. They MUST NOT be privatised or renamed.

### 28.5 `extern` signatures

An `extern` declaration takes scalars (`i8..i64`, `u8..u64`, `bool`) and `ptr` only. A
`str`, an array, a slice, or an aggregate passed by value is E021. A string bound for C
must be assembled as a NUL-terminated byte sequence by the program. `extern fn` has no
body and ends with `;`; omitting the return type means `void`.

Two modules may declare the same external name; it refers to the same symbol. A
declaration that is never called produces no reference.

---

## 29. The Potato form object

### 29.1 What Potato is

Potato is the machine-readable summary written next to a compiled unit: **what this unit
is**, answerable without reading the source. It is not a debug dump and not a profile.

### 29.2 Versions

Versions are cumulative and forward-only: `v0` … `v22`. Each new required field bumps the
version; older objects remain valid; a new version never rewrites an older one. The
current version is **v22**, which adds the `lifetime` dimension.

The ladder includes, in order: grammar, boundary, gc, runtime, gc ladder, port, surfaces,
vm, net ladder, lms, lspe, lms levels, lms budgets, gc control, lifetime.

### 29.3 Required content

A form object MUST record at least:

* the grammar and the canonical edition;
* the switches in force (all eight dimensions and any slots);
* the capability declarations (space, bounds, law, flags);
* the boundary counts: `extern` declarations, `extern` calls, syscall sites, pointer
  transforms;
* the GC tier and ladder;
* the runtime and port values;
* the surface counts (native and foreign entry points);
* the VM value;
* the network ladder (`w0..w3`);
* the LMS levels and budgets;
* the LSPE ledger (or its closed state);
* the lifetime value.

### 29.4 Validation

An implementation MUST provide a validator that checks the object's shape and its
**cross-field self-consistency** (for example `surfaces.total_sites == boundary.syscalls`,
`w0+w1+w2+w3 == total_net`, and the ladder sums). The validator cannot check that a number
is semantically right without the source; it can and MUST catch "two places disagree".

Two emitters (a reference and a self-hosted one) MUST produce byte-identical objects for
the same input.

### 29.5 When it is emitted

A compiler that emits code MUST emit the form object alongside it. The object is part of
the artifact contract, not an optional debugging aid. A form object that does not validate
is a conformance defect.

### 29.6 What it is for

The side that does not read the source — a linker, a packager, an auditor — can answer:
does this artifact carry a collector? does it open a socket? which domains does it declare?
which safety tier was it built with? which edition is it?

---

## 30. LVM: execution tiers

### 30.1 The position of the VM

LVM is the fourth reader of the same IR (the others are the native backend, the linker, and
the translation backend). It does not invent a bytecode language or a type system; its
instruction set is the subset the linker already parses, plus execution.

### 30.2 Tiers

| Tier | What it is | Status |
|---|---|---|
| T0 | interpreter over the text IR; parse into tables, then walk | landed |
| T1 | feedback profile: block counts, edge counts, call-site counts | landed |
| T2 | baseline compile: decode once, execute table-driven | landed (reference and mirror) |
| T3 | guard specialisation and deoptimisation (constant specialisation) | landed (reference) |
| compact form | serialisation of the parsed tables, with a form header | landed (reference) |
| cache | a compiled-op cache keyed by input size, mtime, content hash, and IR hash; stale caches are refused, never half-used | landed |
| fusion | two adjacent pure ops dispatched once (`arith+arith`, `load+arith`, `arith+store`, `icmp+br`) | landed |
| mirror | the same design written in Loment, shipped as `loment-lvm` | landed |

T4 (generational/concurrent collection) is out of LVM's scope: the VM is a reader of the
IR, not a runtime with its own collector.

### 30.3 `choose vm`

`choose vm` records the dimension; the artifact is currently **byte-identical to
`no_vm`** and carries no bytecode. The interpreter is a separate tool. A program MUST NOT
assume that `choose vm` changes the artifact.

### 30.4 Entry shapes

The VM accepts two entry shapes: `_start` and `command_main(argv, argc)` (it synthesises
argv the way `/proc/self/cmdline` looks). An `interrupt` entry and a `thread_spawn` target
are refused **by name**, not reported as "no `_start`".

### 30.5 Traps and limits

The limits are named: 16 MiB VM address space, a 64 KiB zero page, 4096-byte frames,
`STEP_CAP = 50,000,000` instructions per call, and `MAX_DEPTH = 128` (the mirror uses 32).
`llvm.trap` is defined as exit code **132** with a site line on stderr:
`lvm: llvm.trap at <fn>:<block>#<index>`.

An implementation MUST name a limit it enforces; it MUST NOT silently truncate a program
to fit.

### 30.6 Determinism

The VM's counters are deterministic: `--counters-out PATH` writes instructions, decodes,
tuple reads, specialisations, and deopts. T0 decodes once per executed instruction; T2
decodes once per function and reads compiled tuples per instruction. A gate MUST use
counts, not wall-clock ratios.

### 30.7 The profile does not enter Potato

A profile answers "what happened when it ran"; Potato answers "what the unit is". Putting
a profile into Potato would make the same source produce different objects on two runs and
break byte-for-byte determinism.

---

## 31. Hot update

### 31.1 Pre-Release

A **Pre-Release** is a projection of **fix commits only** onto a baseline. It is not a new
version: features are refused at admission (`projection.line[].kind` must be `"fix"`). The
manifest records the applied line, the evicted commits with reasons, and the tree hash.

### 31.2 The hotswap profile

A build with `--profile hotswap` emits an entry gate, a slot table, and an epoch. A normal
build carries the fixed core but no slot table; `hot_install` returns 15 without touching a
byte.

### 31.3 The swap

A candidate (`.lomhot`) is installed into the **slot that is not running** (A/B
alternating). The frame chain is rewritten so every live frame continues in the new image,
and the epoch becomes READY. The gate is checked at a **safe point** — the top of the LSP
event loop in the packaged server.

### 31.4 Refusals

A candidate is refused **before anything moves** for: digest mismatch (RC 7), slot-shape
mismatch (RC 4), frame layout change, or call-site id change. A refused candidate leaves
the process untouched.

### 31.5 Boundaries

* **The `.data` is not swapped.** Strings and initialised globals keep their old values;
  "fix a message" is not a hot-update fix.
* **Single-threaded only.** Two slots alternating requires other threads to stop inside
  the SWAPPING window; there is no language-level stop point or thread count yet.
* **Projection is reproducible.** Replaying the same line yields the same tree hash.
* **Not part of the `loment` command list yet.** The tooling lives in the repository
  (`loment_update.py`, `hotart`, `hotload`, `staging`).

---

## 32. Foreign functions and libraries

### 32.1 Two legs

| The other side is | How to use it | Coverage |
|---|---|---|
| a C-ABI library (C, C++, Rust, Zig, Go c-archive, Swift, C# NativeAOT, Fortran, …) | declare `extern fn` and pass the object/archive/shared object to the linker | every language that exports C symbols |
| a runtime (Python, Java, JavaScript, Ruby, Lua, …) | use the process bridge (`use proc`; `proc_sh` / `proc_python`) | every language with an interpreter |

### 32.2 Linking

`--link` accepts `.o`, `.a` (fixed-point member selection), and, on ELF, `.so`. A shared
object product carries `PT_INTERP`, `PT_DYNAMIC`, `DT_NEEDED`, and a PLT/GOT; the host's
loader performs mapping and relocation. Limits: ELF only (no PE-side FFI), one shared
object on the self-hosted linker, function symbols (not data symbols) imported.

An object with undefined symbols is refused by default. The launchers pass `--allow-ports`
in both channels, so an explicit `--link` is accepted; a direct call to the link binary
requires the flag. With the door open, the linker may supply `memcpy`, `memset`, `memcmp`,
`malloc`, `free`, and `__stack_chk_fail`.

### 32.3 Size cap

The linker's input and output text are capped at 4 MiB each. The IR plus the linked
sections of every `--link` object must fit. Over the cap is an error, never a truncated
build.

### 32.4 The process bridge

`proc_sh(command, buf, cap)` starts an interpreter process and reads its output into `buf`.
`proc_python` is sugar for a Python command. What crosses is a **byte stream**, not a
pointer; there are no structs. It is Linux/ELF only and requires the interpreter on the
machine.

### 32.5 LibL

LibL is the standard library's **libc face**: `use libl` brings in libc-shaped modules
(`string`, `ctype`, `stdlib`, `stdio`, `libl_host`) with libc names, argument order, and
return semantics, while the rest of the standard library keeps Loment's
explicit-length/offset shape. The criterion compares against the real glibc, not against a
Python model.

### 32.6 Java without Java

A `.jar` can be AOT-compiled at build time into a portable Loment IR module, merged at
link time; the product is an ordinary ELF/PE with no JDK at build and no JVM at run. It is
a **closed-world, static subset**: no reflection, no dynamic loading. JNI remains available
in `hosted` mode for cases the AOT path does not cover.

### 32.7 PE

PE has no FFI, no threads, and no process bridge. An implementation MUST refuse them by
name on PE, not silently degrade.

---

## 33. Threads, networking, and syscalls

### 33.1 Threads

`thread_spawn("entry", stack_top, arg, done)` starts a real thread. The entry is a **string
literal naming a function in the unit**, so the product contains a direct call and the
static call graph stays static. The product switches `rbp`/`rsp` to the supplied stack
before calling the entry. `done` is the address of a `u32` that the product atomically
increments and `futex_wake`s after the entry returns, before the thread exits (`0` = no
notification). The entry cannot do this itself, because returning exits the thread.

`thread_spawn` is ELF only. PE refuses it by name. The language has no function-pointer
type and no indirect call; this is the boundary between `thread_spawn` and a general
function pointer.

### 33.2 The network ladder

`net_ladder` counts syscall **sites** by the widest reach they need:

| Rung | Meaning | Numbers |
|---|---|---|
| `w0` | verify the peer's bytes on the same machine | `socketpair` (53) |
| `w1` | one bounded connection, peer chosen by us | `socket`(41) `connect`(42) `sendto`(44) `recvfrom`(45) `sendmsg`(46) `recvmsg`(47) `shutdown`(48) `getsockname`(51) `getpeername`(52) `setsockopt`(54) `getsockopt`(55) |
| `w2` | many concurrent connections (multiplexing only) | `poll`(7) `select`(23) `epoll_wait`(232) `epoll_ctl`(233) `epoll_create1`(291) |
| `w3` | accept any peer | `bind`(49) `listen`(50) `accept`(43) `accept4`(288) |

The four rungs are always present and `w0+w1+w2+w3 == total_net == surfaces.net`. The
ladder counts sites, not connections, bytes, or duration.

### 33.3 Two measured traps

* SIGPIPE kills the process on the second write after the peer's RST; handle it or avoid a
  second write.
* A blocking `read` on an accepted socket blocks forever unless `SO_RCVTIMEO` is set.

`syscall7` exists so `sendto`/`recvfrom` can carry their sixth argument (`socklen_t`).

---

## 34. Determinism and reproducibility

### 34.1 The rule

For the same source, edition, switches, and target, an implementation MUST produce the
same artifact bytes on every run and on every machine.

### 34.2 What must not enter an artifact

* timestamps, build ids that vary per run, process ids, user names, paths outside the
  source;
* hash-order iteration over a dictionary or set;
* pointer-keyed iteration;
* uninitialised padding;
* a profile or a run-time measurement.

### 34.3 Hash order

Where an implementation iterates a hash-coloured collection, it MUST sort by a stable key
before emitting. A pass that cannot be made deterministic MUST be excluded from the
conforming pipeline, or the implementation MUST declare the non-determinism in its
conformance claim.

### 34.4 Byte equality

The in-tree reference and self-hosted implementations MUST produce byte-identical IR and
artifacts for the same conforming input. A difference is a bug report; the suite's
diagnostic tool is the way to locate it. A third-party implementation is bound by §4.4:
observable equivalence, not byte equality.

### 34.5 Reproducible generated trees

A generated tree that is tracked (the bootstrap seed, the genesis image, the form-object
data, the generated manual) MUST be reproducible from its source and MUST be recorded with
its input hash. A `--check` that only compares the output hash cannot tell whether the
source changed and the output is stale; the record MUST bind the input.

---

# Part V — Toolchain and conformance

## 35. The toolchain surface

This chapter defines **behaviours**, not spellings. A conforming toolchain MUST provide
them under some command name; the repository's own spellings are given as examples.

### 35.1 Checking and building

* **check** — read a unit, run the static rules, report diagnostics, exit non-zero if any.
* **ir** — emit the intermediate representation to a stream or file, for inspection.
* **build** — emit an artifact and its form object.
* **run** — build and execute, returning the program's own exit code.

A toolchain MUST NOT conflate "produced a diagnostic" with "success". Any diagnostic of
severity error means a non-zero exit and no artifact (or, for `check`, no success).

### 35.2 Source reading

A toolchain SHOULD provide source-reading commands that do not require an editor: line
counts, function signatures, a token histogram, a substring search, a content hash, a TODO
list, and a file listing. These are conveniences; they are not part of conformance beyond
not corrupting the source.

### 35.3 Formatting and documentation

* **fmt** — format a native-spelling file. It MUST refuse a file whose grammar
  declaration it does not handle, by name, rather than rewrite it under the wrong
  grammar. A formatter MUST NOT change the meaning of a program.
* **doc** — extract `///` comments and print an API summary. It MUST attach a doc comment
  to the item that follows it.

### 35.4 Diagnostics

A diagnostic MUST carry:

* the **code** (§18.5) and the **position** (line, and column or token);
* the **source line** and, where the renderer supports it, a caret under the span;
* **what** is wrong, **why** it matters, and **at least three** concrete fixes;
* what is **supported** and what is **not**, when the distinction matters.

The reference renderer is `lomenterr`: it consumes the structured JSONL a compiler writes
with a diagnostic-output flag and renders the titled card. A toolchain MAY render directly;
it MUST NOT drop the code or the position.

The reporter's language is a rendering preference, not a language property. English is the
default because the CLI output is ASCII on Windows code pages; a Chinese rendering is a
supported option.

### 35.5 Language services

An editor integration MAY provide completion, go-to-definition, diagnostics, hover, and
document symbols. The language server:

* MUST use the same front end and the same codes as the compiler for diagnostics;
* MUST NOT declare a formatting provider if it does not format (the reference server
  deliberately does not);
* MUST NOT present a construct as available when the effective edition does not contain
  it.

A debug adapter MAY be provided out of process. The reference adapter is ptrace-based and
requires a real Linux kernel (WSL2 yes, WSL1 no).

### 35.6 Version reporting

A toolchain MUST be able to report:

* its own version;
* the edition of an artifact (read from the marker, §27.4);
* the built-in and switch tables it implements, in a form a script can read.

### 35.7 Exit codes

The following meanings are normative for a toolchain that exits with a code:

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | a diagnostic of severity error, or a refused build |
| 2 | usage error (bad arguments, missing file) |
| 132 | trap (`SIGILL`, `llvm.trap`, panic) |
| 124 | a watchdog timeout, where a tool defines one |

A program's own exit code from `run` is the program's, not the toolchain's; `run` MUST
propagate it.

---

## 36. The standard library and L0

### 36.1 The standard library is not the language

The language defines the built-ins of §18.1 and nothing else. A standard library is
**shipped** with a toolchain, not defined by the standard. A program that uses a library
is conforming only if that library is present; a missing library is a resolution error
(E018), not a language error.

### 36.2 L0

`lom/*.lom` is the **L0 interface layer**: declarations from which constants and decoders
are generated for other languages. It is a cross-line contract, not an implementation
detail. A source file with the `.lom` suffix is an L0 import (§16.2), not L1 source.

L0 is not part of the L1 language surface defined by this document, but it is part of the
artifact contract: a change to `lom/*.lom` is a contract change with its own review and its
own record. An implementation that consumes L0 MUST treat a `.lom` file as declarations,
not as L1 code.

### 36.3 The library system

The repository's older library system (`loment.py lib …`) provides a dependency tree, an
instance identity, a capability-requirement closure, conflict checking, and a
materialisation step. The package manager (`lompi`) is the current tool. The two are not
the same thing and MUST NOT be mixed. A library is a directory; its identity is a content
hash; its dependencies are the `use` lines in its source; its exports are `pub`.

The optional `pkg.lomp` carries two labels:

```
pub fn name() -> str { return "…"; }
pub fn version() -> str { return "…"; }
```

They are functions, not constants, because the language has no string constants.

### 36.4 What the shipped library provides

The shipped store provides portable modules (containers, text, big integers, floating
point, hashing, checksums, compression, bit twiddling) and host modules (files,
directories, argv, stat, logging). The name form of `use` brings in one module. A facade
module may bring in many; it is large and collision-prone and SHOULD be avoided in favour
of per-module imports.

`f64` is a library type over integers, not a language type. `String`, `Vec`, and `HashMap`
are library types, not built-ins.

---

## 37. Packaging and distribution

### 37.1 The layout

A release is a `bin/` + `share/` tree:

* `bin/` — the driver, linker, CLI, formatter, language server, LVM, and package manager;
* `share/` — the standard library, the guides, the skills, and the L0 sources.

A release records a `release-manifest.json` and a `SHA256SUMS`. The manifest's file set
MUST equal the tracked file set; a packager MUST check it.

### 37.2 Signing

Signing is two things: an Authenticode signature on the Windows executable, and a detached
signature over `SHA256SUMS` for the archive. The key lives only in the build secret. A
self-signed certificate still triggers SmartScreen; that is a property of the platform, not
of the signature.

### 37.3 Channels

A release channel declares which switches it exposes and which features it includes. A
channel MAY refuse a switch whose contents are not implemented. It MUST NOT accept a
switch and ignore it. The channel policy is published with the release; the standard does
not state it (§25.8).

### 37.4 The package manager

`lompi` is a separate command, not a subcommand of the driver. A package is a directory; a
store is a content-addressed tree; a lock file pins the instances. Installing a library is
a package-manager operation; it does not change the language.

### 37.5 Generated artifacts

Tracked generated artifacts (the bootstrap seed, the genesis image, the form-object data,
the generated manual) MUST be reproducible and MUST be committed separately from the
hand-written change that produced them, with their input hashes recorded. A seed change
MUST land serially, not in parallel with another seed change.

---

## 38. The conformance suite

### 38.1 What it is

The conformance suite is the instrument that decides whether a claimed implementation
conforms. It is a set of criteria, each of which passes, fails, or skips. It is not a second
definition of the language; a criterion that contradicts this document is a defect in the
criterion (§5.2).

### 38.2 What it must cover

A suite that claims to measure this standard MUST include, at minimum:

| Area | What is measured |
|---|---|
| rule parity | the reference and self-hosted checkers produce the same verdict and code on a corpus of rule cases, with a ratchet budget |
| twin artifacts | byte-identical IR/artifacts for the corpus, plus a three-stage bootstrap fixed point |
| reference behaviour | the reference implementation's positive and negative cases, including the front door and the loader |
| diagnostics | every diagnostic code is classified; every reference message maps to a code |
| built-ins | the built-in table and its twin are the same |
| loaders | name and path resolution, ambiguity, cycles, the 300-line cap, the project suffix |
| form object | shape and cross-field consistency; both emitters agree byte for byte |
| editions | resolution, freeze, collision, aliases, artifact marker |
| domains | each law's pass/fail, the audit count, the traps, the scoped forms |
| LGC / LMS / LSPE | the tiers, the ladder sums, the levels, the ledger invariants |
| artifacts | each target's emitter and, where possible, execution |
| deviating platforms | a target that is emitted but not executed is marked as such |

### 38.3 The ratchets

The suite uses ratchets, and an implementation MUST NOT lower one:

* **budgets** — the number of equivalence rules may only rise;
* **floors** — the number of criteria per suite may only rise;
* **skips** — the allowed skip list may only shrink;
* **pass ratio** — the LSPE bypass ratio may only fall.

Lowering a budget, floor, or pass ratio is relaxing the gate; the gate MUST go red for it.

### 38.4 Green, red, and skip

* **PASS** — the criterion ran and held.
* **FAIL** — the criterion ran and did not hold; the suite is red.
* **SKIP** — a prerequisite is absent. A skip is **not a pass**. "The suite is green"
  means the criteria that ran were green; the tail of each tool's output says which ran.

A criterion whose prerequisite is missing MUST report SKIP, not a pass.

### 38.5 No totals in the standard

The suite prints its own counts on its last line. Those lines are the numbers. This
document does not copy them, because a copied total becomes a second source of truth and
is measured stale within weeks.

### 38.6 What the suite cannot cover

* A production path that needs `clang`: absent on some machines, skipped.
* A Java corpus: needs `javac`, skipped.
* Running on `aarch64`: emitted, never executed on hardware.
* Criteria that read a sibling repository: absent in a standalone checkout, red by
  construction.
* The package manager's store synchronisation: a packaging fact, not a language fact.
* **External verification**: two implementations by the same author agreeing is evidence
  that they agree, not that they are right. This is stated, not hidden.

### 38.7 Claiming a result

A conformance claim MUST quote the command and the tail line of each suite it ran, plus
the SKIPs. A claim that says only "all green" without the tail line is an assertion, not a
measurement.

---

## 39. Known deviations

This chapter is normative in one respect: an implementation MUST publish its own
deviations. The list below is the state of this issue. The living list is the repository's
freeze record (`docs/158` §4); where this chapter and that record disagree, this chapter
is the standard's summary and the record is the evidence — the discrepancy is resolved by
§5.2.

### 39.1 Language and checker

| # | Deviation | Effect |
|---|---|---|
| 1 | The self-hosted checker may under-report three cases (subscript/`&` when the type is unknown, `match` on an unknown subject, `?` when the type is not known to be `Result`) | conservative: it may miss an error, not invent one |
| 2 | A same-name double `let` in one function is lowered differently (the reference reuses a slot; the self-hosted emits two allocas) | a shadowing case may differ |
| 3 | The self-hosted driver has no separate parser pass for some parse-time errors | a parse error may reach the checker's message instead of E019 |
| 4 | The reference parser accepts some words as binding names where another implementation may not | the collision rule (E040) covers the edition-introduced words; this is the residual |
| 5 | `choose lifetime_no` is the pre-2026.3 shape; `&` in type position is a parse error there | by design, not a hole |
| 6 | Borrow-domain rules (phase 1) are implemented in the reference only; the self-hosted side is not yet aligned | a twin divergence; the suite MUST mark it |

### 39.2 Artifacts and targets

| # | Deviation | Effect |
|---|---|---|
| 7 | `aarch64` is emitted but not executed | "emits" is not "runs" |
| 8 | The FFI input/output text is capped at 4 MiB on the self-hosted linker | a larger object is refused, not truncated |
| 9 | One relocation form (`R_X86_64_PC64`) is sign-extended in the reference and zero-extended in the self-hosted linker | measured unreachable today; recorded so the equivalence claim has no unrecorded exception |
| 10 | `choose vm` does not change the artifact | the interpreter is a separate tool |

### 39.3 Tooling and records

| # | Deviation | Effect |
|---|---|---|
| 11 | LSPE invariants I2–I5 are emitted as `not_run` | the ledger exists; the full self-audit does not |
| 12 | LSPE derivation trees, the policy lock (E037), and the LSPC pre-flight are not shipped | the codes are reserved, not produced |
| 13 | The compact form and T2 have no self-hosted mirror | the twin criterion does not apply to them |
| 14 | No external human or organizational verification of the frozen surface | the suite is self-certified |
| 15 | DWARF carries statement lines, not variable locations | a debugger can break on a line, not inspect a variable |
| 16 | Block scope for `let` does not exist | a branch-local binding is visible after the branch |

### 39.4 Planned, not deviations

The following are non-goals or planned work, not deviations:

* floats, `char`, tuples, closures, function pointers, `impl Trait`, `dyn`, exceptions;
* runtime type information, reflection, GC compaction, weak references, finalizers;
* a parallel language construct; the compiler is single-threaded;
* PE-side FFI, PE threads, the PE process bridge;
* LomentFaster, Lomenter, DGT, the `lmgr` dimension, and wasm32;
* a third-party debugger path.

An implementation MUST NOT present a planned item as available.

---

## 40. Non-goals and planned work

### 40.1 Non-goals of the language

These are deliberate absences, not backlog items:

* implicit numeric conversion and implicit widening;
* a garbage collector by default;
* a hidden runtime;
* runtime type information or reflection;
* floats in the core;
* exceptions;
* operator overloading;
* a second definition of the language in generated documentation.

### 40.2 Planned work

The following are designed or planned and are **not** part of this edition. An
implementation MUST NOT claim them.

| Item | State |
|---|---|
| LomentFaster (three speed tiers) | design |
| Lomenter (an optimiser with a ledger contract) | design |
| DGT (compile-time digraphs) | planned |
| `lmgr` dimension and CLI | design |
| wasm32 backend | design |
| Full LSPE proof search, derivation trees, policy lock | planned |
| Borrow-domain phases 2/3, self-hosted alignment | phase 1 reference only |
| Hot-update tooling as a shipped command | repository tools only |
| `aarch64` execution verification | emission only |
| A third-party LLDB/GDB path | not planned; the DAP backend is ptrace-based |

### 40.3 How planned work enters

A planned item enters the language only by the procedure of §6.2: this document first, a
criterion second, both implementations in one commit, then the gate. A design record is
not a feature.

---

# Appendices

## Appendix A — Grammar (normative sketch)

This is a readable grammar. The parser is authoritative for the exact acceptance; this
appendix states the shape. `BLOCK` is `{ statement* }`; `(x),*` is comma-separated;
`?` is optional.

```
program       := grammar_decl? "module" IDENT item*
grammar_decl  := "choose" "write" "grammar" IDENT

item          := "use" (IDENT | STRING)
               | "const" IDENT ":" type "=" const_lit ";"
               | "fn" IDENT generics? "(" params? ")" ("->" type)? block
               | "struct" IDENT generics? "{" (IDENT ":" type),* "}"
               | "enum" IDENT generics? "{" variant,* "}"
               | "trait" IDENT "{" fn_sig* "}"
               | "impl" IDENT "for" type block
               | "extern" "fn" IDENT "(" params? ")" ("->" type)? ";"
               | "interrupt" "fn" IDENT "(" params? ")" ("->" type)? block
               | "capability" IDENT ":" IDENT shape law? "revocable"?
               | "excluded" STRING
               | "set" "choose" IDENT block
               | "addin" IDENT
               | "choose" core_value
               | "comefor" "let" STRING "to" block
               | "byuse" STRING "done"
               | "command" IDENT | "foruse" IDENT
               | "let"? IDENT "{" raw_bytes "}"
               | "register" (IDENT | STRING) block
               | "pub" item

const_lit     := "-"? INT
params        := param ("," param)*
param         := "mut"? IDENT ":" type
generics      := "<" IDENT ("," IDENT)* ">"
variant       := IDENT ("(" type ")")?
fn_sig        := "fn" IDENT "(" params? ")" ("->" type)? ";"

statement     := "let" "mut"? IDENT ":" type "=" expr ";"
               | lvalue "=" expr ";"
               | "if" expr block ("else" "if" expr block)* ("else" block)?
               | "if" "let" pattern "=" expr block ("else" block)?
               | "while" expr block
               | "for" IDENT "in" expr ".." expr block
               | "for" IDENT "in" IDENT block
               | "for" IDENT "," IDENT "in" IDENT block        // 2026.3+
               | "for" IDENT "in" IDENT "(" args? ")" block     // 2026.3+ (call source)
               | "match" expr "{" arm* "}"
               | "return" expr? ";"
               | "break" ";" | "continue" ";"
               | "lspe" "pass" STRING block
               | "defer" expr ";"
               | "lgc" ("end" | "start") ("(" INT unit ")")? (";" | block)
               | "domain" domain_header block
               | "domain" ":" "for" IDENT ("," IDENT)*
                    ("in" IDENT ("," IDENT)*)? ("step" "-"? INT)? block
               | "tick" ";"
               | "quality" IDENT "=" expr ";"
               | "discharge" IDENT "(" expr ")" ";"
               | "solid" IDENT ":" IDENT ("," IDENT)* ";"
               | ("couple" | "mutex") IDENT ("," IDENT)* ";"
               | block
               | expr ";"

domain_header := (IDENT ":")? ("borrow" | "borrow_mut") "(" IDENT ")"
               | (IDENT ":")? IDENT shape law? "revocable"? ("child_of" IDENT)?
shape         := "[" ("()" | INT) ".." ("()" | INT) "]"
law           := "epoch" "(" INT ")" | "far" "(" INT ")" | "sliding" "(" INT ")"
               | "cyclic" "(" INT ")" | "decaying" "(" INT ")" | "growing" "(" INT ")"
               | "adaptive" | "generational" "(" INT ")" | "consumed" "(" INT ")"
               | "subject" "(" INT ")" | "witnessed" "(" INT ")" | "flow"
               | "reachable" "(" INT ")" | "danger"

arm           := (pattern | "_") "=>" block
pattern       := IDENT "::" IDENT ("(" IDENT ")")?

expr          := unary (binop unary)*
unary         := ("-" | "!" | "*") unary | postfix
postfix       := primary ("(" args? ")" | "." IDENT | "[" expr "]" | "?")*
primary       := INT | STRING | "true" | "false"
               | IDENT | IDENT "::" IDENT ("(" expr ")")?
               | "(" expr ")"
               | IDENT "{" (IDENT (":" expr)?),* "}"           // shorthand: 2026.3+
               | "[" (expr),* "]" | "&" ("mut")? expr
               | "if" expr block "else" block                  // 2026.3+, let initialiser only
               | "match" IDENT "{" (pattern "=>" expr),* "}"    // 2026.3+, let initialiser only
cast          := expr "as" type
fallback      := expr ("orelse" | "catch") expr

binop         := "||" | "&&" | "==" | "!=" | "<" | "<=" | ">" | ">="
               | "|" | "^" | "&" | "<<" | ">>" | "+" | "-" | "*" | "/" | "%"

type          := INT_TYPE | "bool" | "ptr" | "str" | "()"
               | IDENT | "[" type ";" INT "]" | "[" type "]"
               | "mut" "[" type "]" | IDENT "<" (type),* ">"
               | "&" ("mut")? type | "&" IDENT type
```

Three properties the sketch makes visible because they surprise people: there is no float
or `char` type; `&` is an expression in argument position (and, from 2026.3, a type); and
a `match` arm is a block, not an expression.

## Appendix B — Diagnostic index

The normative table is §18.5. This index gives the fix class in one line; use §18.5 for
the full statement.

| Codes | Fix class |
|---|---|
| E001 | make the types line up |
| E002, E030 | declare the name / declare the type |
| E003 | fix the argument count |
| E004, E005 | fix the capability declaration or its use |
| E006, E007, E012 | fix the ownership or borrow |
| E008 | fix the match or enum |
| E009 | rename, or add a field/variant |
| E010 | use `?` on `Result`/`Option` |
| E011 | add or remove `mut` on the slice |
| E013 | rename the duplicate |
| E014 | assign to a variable/field/index |
| E015, E016 | fix the field or array shape |
| E017 | use a legal `as` |
| E018 | fix the import |
| E019 | fix the syntax |
| E020 | split the facade |
| E021 | use scalars/`ptr` in `extern` |
| E022 | fix the `choose` |
| E023 | write around the limit, or report it |
| E024–E026 | fix the command declaration |
| E027 | add the final `return` |
| E028, E029 | fix the index / the free |
| E031 | add `choose runtime`, or remove the construct |
| E032, E033 | fix the output path / split the unit |
| E034–E038 | fix the audit ledger or the bypass |
| E039 | add a collector, or remove `lgc` |
| E040 | rename, or pin the edition |

## Appendix C — Built-in availability

| Built-in | Tur 1 | Tur 2026.2+ | Needs runtime | Target notes |
|---|---|---|---|---|
| `str_len`, `str_eq`, `str_byte`, `str_ptr`, `print`, `ptr_*`, `slice_len` | yes | yes | no | |
| `str_concat`, `str_num` | yes | yes | yes | allocate from the arena; E031 under `no_runtime` |
| `alloc`, `free`, `load8`, `store8` | yes | yes | no (the allocator is in the artifact) | |
| `atomic_add` | yes | yes | no | |
| `inb`, `outb` | yes | yes | no | translation backend only |
| `syscall4/6/7` | yes | yes | no | numbers are target-specific |
| `thread_spawn` | yes | yes | no | ELF only |
| `tur_edition` | yes | yes | no | |
| `print_num` | — | yes | yes | E031 under `no_runtime` |

## Appendix D — Switch matrix

| Dimension | Values | Default | Edition note |
|---|---|---|---|
| `mode` | `std`, `no_std` | `std` | |
| `gc` | `gc_manual`, `gc_auto`, `gc_auto_alpha`, `no_gc` | `gc_manual` | |
| `runtime` | `runtime`, `no_runtime` | `no_runtime` | |
| `port` | `sealed`, `hosted` | `sealed` | |
| `vm` | `vm`, `no_vm` | `no_vm` | artifact unchanged today |
| `lms` | `lms_full`, `lms_forensic_admin`, `lms_daily_admin`, `lms_manage_only_admin` | `lms_full` | |
| `lspe` | `lspe_open_admin`, `lspe_close_admin` | `lspe_open_admin` | |
| `lifetime` | `lifetime_no`, `lifetime_auto`, `lifetime_manual` | `lifetime_auto` from 2026.3; `lifetime_no` before | edition-gated |

Slots: `lspe_aicheck_true` / `lspe_aicheck_false` (default `false`).

Contradictions: `gc_auto`·`no_runtime`, `gc_auto_alpha`·`no_runtime`, `no_std`·`hosted`,
`no_std`·`gc_auto`, `no_std`·`gc_auto_alpha`, `gc_auto_alpha`·`hosted`. The reasons are
§25.4.

## Appendix E — Domain kinds

| Kind | Parameter | Second guard arg | Stateful | Bounded required | Walkable |
|---|---|---|---|---|---|
| `regular` | — | — | — | — | yes |
| `danger` | — | — | — | — | no |
| `epoch` | N | — | — | — | yes |
| `far` | W | — | — | yes | yes |
| `sliding` | W | — | — | — | yes |
| `cyclic` | P | — | — | yes | yes |
| `decaying` | T | — | — | — | yes |
| `growing` | G | — | — | yes | yes |
| `adaptive` | — | — | yes | yes | yes |
| `generational` | N | — | — | yes | yes |
| `consumed` | N | — | yes | yes (≤64) | yes |
| `subject` | MASK | yes | — | — | no |
| `witnessed` | T | yes | yes | yes (≤64) | no |
| `flow` | — | yes | — | yes | no |
| `reachable` | K | — | yes | yes (≤64) | no |

The shape spellings are `[a..b]`, `[a..a]`, `[()..b]`, `[a..()]`, `[()..()]`.

## Appendix F — Potato versions

| Version | Added |
|---|---|
| v0–v5 | initial shape and early fields |
| v6 | grammar |
| v7 | boundary |
| v8 | gc |
| v9 | runtime |
| v10 | gc ladder (four rungs) |
| v11 | port |
| v12 | surfaces |
| v13 | vm |
| v14 | net ladder |
| v15 | lms |
| v16 | lspe |
| v17 | lms levels |
| v18 | lms budgets |
| v19 | five-rung gc ladder |
| v20 | systems fields (the v20 record) |
| v21 | gc control (`lgc`) |
| v22 | lifetime dimension |

A version adds required fields; an older object remains valid. The current version is v22.

## Appendix G — Conformance checklist

An implementation claiming **C1** against this edition MUST be able to check every box,
or list the exception in its deviation list.

- [ ] Reads and resolves the grammar declaration (R1).
- [ ] Accepts the grammar of §7–§14 and rejects the rest with E019 (R2).
- [ ] Enforces every static rule of §19.2 with the assigned code (R3).
- [ ] Every diagnostic has a code and a position (R4).
- [ ] No success after a diagnostic; no truncation; no silent drop (R5).
- [ ] Deterministic artifacts (R6).
- [ ] Emits a valid form object that agrees with the source (R7).
- [ ] Does not change a frozen edition's meaning (R8).
- [ ] Implements the collision rule E040 (R9).
- [ ] Publishes its deviations (R10).
- [ ] Claims no class or edition it has not demonstrated (R11).
- [ ] Refuses unsupported constructs by name (R12).
- [ ] Implements the eight switch dimensions, defaults, and conflicts.
- [ ] Implements the capability domains of §21.
- [ ] Implements LGC, LMS, and LSPE to the stated level, or declares the gap.
- [ ] Emits the edition marker.
- [ ] Publishes the suite command and tail lines.
- [ ] Lists the SKIPs and why each skipped.

## Appendix H — Surface-grammar boundaries

| Spelling | Typical source | Known boundaries |
|---|---|---|
| C | `.c`, `grammar c` | no arrays or pointers; scalars only |
| C++ | `.cpp` | classes only as far as the table goes; no templates or references |
| Java | `.java` | no arrays/objects with runtime dispatch; `out.println` maps to `print` |
| C# | `.cs` | the same scalar subset |
| Go | `.go` | the same scalar subset; no goroutines or channels |
| Python | `.py` | `int` is a bounded wrapping `i64`, `//` truncates toward zero, lists/dicts are not mapped |
| Rust | `.rs` | interface extraction only; a complete program is refused |
| Natural | `.nl` | an invented sentence-shaped grammar, translated to the same semantics |

The reference implementation translates these in process; a packaged self-hosted toolchain
accepts native spellings only and refuses the rest by name.

## Appendix I — References

Normative: this document.

Informative: the files named in §2.2, the implementations under `tools/` and
`loment/selfhost/`, and the conformance suite under `tools/*_test.py`.

---

*End of LLS 0.1.5.1, Edition 2.*




