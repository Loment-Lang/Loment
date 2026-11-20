# Roadmap and milestones

The roadmap is a direction, not a schedule. GitHub milestones are where the direction becomes
concrete enough to attach issues to.

## The milestones

| Milestone | Theme |
|---|---|
| **0.1.5** | The umbrella release: safety infrastructure plus a large defect sweep. LSPE, Potato v16, LMS, LVM stabilisation, `natural`, the C surface grammar's filed defects, LibL/LibC. |
| **0.1.5.1** | LMS — the safe memory manager, seven levels (`docs/247`). |
| **0.1.5.2** | Capability domains in depth: revocation, the subject a guard does not check, information flow, an observable audit, composition, a conformance suite, the descriptor as a pinned contract (`docs/146`). |
| **0.1.5.3** | LSPE — the counter-asker and the police: questions, answers, defer/refuse, strict mode, the pre-flight sheet, acknowledgements, cross-build memory (`docs/262`). |
| **0.1.5.4** | LSPE — the ledger invariants and self-audit: I1–I5, E036/E037, the remaining engines, the coverage ratchet, release wiring. |
| **1.0.0** | The version order from 0.1.4.6 onward, as a single planning umbrella. |

Every milestone description ends with the same sentence in spirit: this is a snapshot of intent,
not a commitment. The roadmap that counts is the one on the wiki.

## The four tracks

The original plan still stands, with two infrastructure lines underneath it:

1. **`natural`** — a real native surface grammar.
2. **LVM** — optimisation and stabilisation after the engine lands.
3. **The C surface grammar** — clearing its filed defects so a real C file stops computing the
   wrong number.
4. **LibL / LibC** — the standard library's libc face, static first and dynamic after.

**LSPE** and **LMS** are the infrastructure those tracks now stand on.

## How issues reach a milestone

- Severity labels (`sev-N`) say how bad a defect is; domain labels (`ci`, `linker`, `compiler`,
  `tooling`) say where it lives.
- A milestone is assigned when someone intends to clear the issue in that release. Being on a
  milestone is a plan, not a promise that it will be fixed by a date.
- Umbrella issues (LMS levels, LSPE invariants) carry their own milestone; their sub-issues
  inherit it.

## Reading the plan without being misled

- `docs/` records design; it does not record completion. A design document may describe work that
  is not implemented.
- A milestone description describes intent at the time it was written.
- When in doubt, check the issue state and the code before repeating a plan as a fact.

## How to influence it

The most effective contribution to the roadmap is a measured defect: an issue with a minimal
reproduction, the command, the toolchain version and what should have happened. A criterion that
turns that defect into a red test is better still. Plans move when the evidence does.
