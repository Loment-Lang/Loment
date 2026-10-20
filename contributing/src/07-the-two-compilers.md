# The two compilers

The language surface exists twice:

| Where | What it is | Why it exists |
|---|---|---|
| `tools/lomentc.py` + `tools/lomelf.py` | The reference implementation in Python: lexer, checker, emitter, linker. | It is the readable definition and the tool the gate can run anywhere. |
| `loment/selfhost/*.lomt` | The self-hosted compiler, written in Loment. | It is what ships; it needs no Python. |

They are required to agree byte for byte: the same program must produce the same diagnostics and
the same IR. The gate runs both and compares.

## The twin criterion proves agreement, not correctness

If both implementations share a wrong assumption, the comparison stays green. That is why the
project also keeps criteria that measure the product itself — a program's output against an
independent result, a binary against a reference, a form object against its declared schema.

When you change the language, change both sides. When you change only one, the twin goes red —
which is the point, not an obstacle.

## The seed

The self-hosted compiler cannot be built from nothing. The chain starts from a committed IR file:

```console
$ python tools/loment_seed.py --emit
```

That regenerates `loment/build/selfhost_driver.ll`. It is marked `-diff` in `.gitattributes`, so a
merge conflict on it is a whole-file conflict: regenerate instead of merging. Commit it
**separately**, with a message saying it contains only generated content — otherwise its thousands
of renumbered SSA lines bury the handwritten change.

`loment_seed_test` fails when the committed seed no longer matches what the sources produce.

## Costs to know before you edit the self-host side

- **Every added function moves the seed.** A small helper can add several seconds to the bootstrap
  runs, because the seed is the input to every stage.
- **The seed has a size budget.** As of October 2026 it is about 4.24 MB against a normalization
  buffer of 6 MB; the criterion requires at least 256 KB of headroom. Watch the number, not the
  percentage.
- **Fixed capacities fail as crashes, not diagnostics.** The self-hosted side has tables with hard
  limits (source slots, labels). Overflowing one is a segmentation fault in the compiler, not an
  error message. Measure the size of what you are adding before adding it.
- **Multi-line string literals cannot contain an ASCII double quote.** A quote inside one truncates
  the literal and the failure shows up as an unrelated `emit_str` error.

## Where the two sides live

- `tools/lomentc.py` — lexer, checker, emitters.
- `tools/lomelf.py` — the Python twin of the linker.
- `loment/selfhost/*.lomt` — the same pipeline in Loment: lexer, parser, checker, code generation,
  the driver, the linker twin.

The self-hosted linker and the Python linker must also agree; the subset they implement is a hard
constraint (no `select`, a single pass over `phi`). A construct that only one of them supports is a
divergence, not an extension.
