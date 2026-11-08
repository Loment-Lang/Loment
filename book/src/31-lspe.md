# LSPE: the audit ledger

LSPE (Loment Safety Police Espace) records **why** the compiler allowed something, and it keeps
that record in the artifact. The enforcers stay exactly where they were: LGC decides lifetime,
LMS decides legality, capability domains decide authority, and the ownership checks decide
moves and borrows. LSPE adds the question behind each allowance and a ledger of the answer.

## What it audits

LSPE audits the **compiler's own decisions**. The shape is a question about a judgement:
`questioner(Judgment) -> Derivation | Hole`. When the compiler cannot prove a hole, it hands the
developer a named choice with options, a default and a consequence.

The four parts are:

| Part | When | What it does |
|---|---|---|
| Questioner | during compilation | audits the compiler's judgement |
| Counter-asker | during compilation | presents a hole: options, default, consequence |
| LSPC (police) | before compilation | posts the residual risk |
| Ledger | in the artifact | records who opened, closed and signed |

## The dimension

```rust
choose lspe_open_admin     // audit on: the default
choose lspe_close_admin    // audit off
```

`lspe_close_admin` skips the questioning and the ledger. Every check still runs. Closing the audit
does not weaken LMS, LGC, the runtime dimension or capability domains; it stops recording why an
allowance was granted.

The `_admin` suffix marks closing a safety system as an administrative act.

## Sites

A **site** is one allowance point:

- a `guard cap(i)`;
- an array or slice index;
- a `free`;
- a `load8` / `store8` derived from `alloc`;
- a call.

Site ids are content-addressed (`unit:kind:fnv8(text#occurrence)`), not line numbers. Reformatting
a file or inserting an unrelated site does not renumber the rest.

## Obligations and evidence

Each site carries an obligation, and each obligation names the evidence that may discharge it.
The registry has fourteen obligations. A representative set:

| Obligation | Question | Accepted evidence |
|---|---|---|
| `InBounds` | is the index in range? | syntactic, typed, local flow, interprocedural, global invariant, assumed |
| `Alive` | is the block still allocated? | local flow, interprocedural, assumed |
| `InDomain` | is the position inside the domain? | typed, global invariant, assumed |
| `Exclusive` | is the borrow exclusive? | local flow, interprocedural, assumed |
| `NotDangling` | does the reference outlive its owner? | local flow, interprocedural, assumed |
| `NoRuntime` | does the construct need the runtime? | axiom, syntactic, typed |

`Assumed` is the floor: the compiler has no proof, and the site is explicitly marked. That is the
honest end of the ladder, and it is why the ledger records the decision rather than hiding it.

## `lspe pass`

A block that deliberately relies on an unproven fact states its reason:

```rust,run
module lspepass

choose runtime

fn main() {
    let i: u32 = 1;
    lspe pass "caller checked the index" {
        print_num(i);
        print(" ");
        print("inside the pass\n");
    }
}
```
<!-- expect: 1 inside the pass -->

The reason is mandatory; an empty reason is E038. The block's statements are compiled normally —
`lspe pass` is an annotation, not a change of control flow or of scoping.

## The ledger

The ledger is part of the Potato form object. With `lspe_open_admin` it records, per unit:

- `state`: which dimension value was chosen;
- `set_by`: where the choice was made;
- the sites, obligations and evidence;
- counts for the allowances, and `interrogations` (how many sites passed provenance,
  falsification and cross-examination);
- `floor_detail`: where the unproved allowances went (`assumed`, `stale_wf`, `budget`,
  `depth`, `drop_honest`, `pass`).

The floors add up: `floors == assumed + stale_wf + budget + depth + drop_honest + pass`.
Five invariants audit the ledger itself: I1 site reconciliation, I2 source coverage,
I3 dimension consistency, I4 floor accounting (a corrupted ledger is E036) and I5 twin parity.
When the audit is closed the invariants are `"not_run"` — a stated boundary, not a silent pass.

`TypeWellFormed` is keyed by `(type, substitution)`: the same type under two substitutions is two
sites, so evidence cannot be reused across a substitution. `DropHonest` is one site per
destructor, discharged by the labelled `Assumed` floor or by a hand-written proof
(`drop:<type>=prove` in `lspe.answers`).

## The counter-asker and LSPC

An `UNDECIDED` site becomes a **question** (Q001–Q013) only after three interrogations have run:
provenance, falsification and cross-examination. The default answer is *allow but label*: the site
stays unproved and the ledger records an `Assumed` floor. `defer(reason)` and `refuse` are recorded
too. Under `--lspe-mode=strict` an unanswered question becomes E034 and the build stops.

LSPC is the before-codegen sheet: twelve risk families, five columns each, and a final row that
reports the closure of the audit itself. It never blocks by default; `--lspc=strict` blocks only
unacknowledged high-risk families, `--lspc=off` is written into the artifact.

## What the toolchain exposes

| Command | What it does |
|---|---|
| `loment lspe state FILE` | prints one unit's ledger, plus the LSPC strip |
| `loment lspe why SITE FILE` | rebuilds and prints that site's derivation chain |
| `loment lspe verify ARTIFACT SIDECAR` | checks per-site proof hashes without recompiling |
| `loment lspc check FILE` | runs the front end, prints the pre-flight sheet, stops before codegen |

`--lspe-mode=full|quiet|strict`, `--lspe-level 0..4`, `--answer SITE=B` and `lspe.answers` are all
wired through the CLI. `loment build --lspe-sidecar` writes the `.lspe` sidecar.

## What LSPE does not do

- It does not change a verdict. Two artifacts that differ only in `lspe` have the same behaviour.
- It does not replace the checker; the enforcers run with the audit closed.
- It does not promise that an `Assumed` site is safe. It promises that the assumption is printed.
- It does not audit the user. The subject is the compiler's judgement.

## When to turn it off

`lspe_close_admin` is appropriate when:

- the ledger's size matters and the program is not under audit;
- a build profile deliberately discards the record.

It is a downgrade of observability. The checks continue, so a program does not become less safe;
it becomes less explainable.

The next part covers the interfaces that leave the language: commands, FFI, threads and the
network.
