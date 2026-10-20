# The repository

One checkout contains the language, the toolchain, both compilers, the tests, the documentation
and the teaching material. This chapter is the directory map.

## Top level

| Path | What lives there | Can you change it? |
|---|---|---|
| `tools/` | The Python toolchain: the reference compiler (`lomentc.py`), the linker twin (`lomelf.py`), the gate (`ci.py`), every criterion (`*_test.py`), release and packaging tools. | Yes. New tools have registration rules — see [Adding a tool](06-adding-a-tool.md). |
| `loment/selfhost/` | The self-hosted compiler, written in Loment (`.lomt`). | Yes, but read [The two compilers](07-the-two-compilers.md) first. |
| `loment/build/` | The bootstrap seed (`selfhost_driver.ll`) and the genesis binary. Generated artifacts. | Only by regenerating, never by hand. |
| `lom/` | The L0 interface contract: syscall and primitive declarations shared with the kernel side. | **No** — cross-line contract, frozen threshold. |
| `docs/` | Numbered design records (`NNN-slug.md`). | Yes, with a number from the tool. |
| `standard/LLS.md` | The Loment Language Standard: a frame, not a second spec. | Carefully; it tracks the language version. |
| `book/`, `exercises/`, `contributing/` | Teaching material: the usage book, the practice tracks, and this book. | Yes. |
| `compare/` | The defect survey against Rust, CPython and OpenJDK. | Append-only historical record. |
| `lompi/` | The package manager and the store format. | Yes. |
| `editors/` | The VS Code extension (TextMate grammar, LSP, commands). | Yes. |
| `packaging/`, `scripts/`, `restarter/` | Installer, launcher, restart helper. | Yes. |
| `kernel/`, `sdk/` | **Vendored read-only copies** of the FujoOS side. | **No** — change them in the FujoOS repository and re-copy. |
| `.github/workflows/` | Gate, release, manifest, mirror and the book's Pages deploy. | Yes. |

## What is generated

Three things look like source but are not:

- `loment/build/selfhost_driver.ll` — the bootstrap seed. A 46 KB IR file produced by
  `python tools/loment_seed.py --emit` from the self-host sources. It is regenerated, never merged
  by hand; it is marked `-diff` in `.gitattributes` so a conflict forces a regeneration.
- `release-manifest.json` and `SHA256SUMS` — the release ledger for `main`.
- Anything under `loment/dist/` or `_site/` — build output, ignored by git.

The seed gets its own commit, separate from the source change that moved it. Otherwise a 3,800-line
SSA renumbering hides the 150 lines a reviewer actually needs to read.

## Where to start reading

- The language surface: `tools/lomentc.py`, then the checker functions it calls.
- The emitted IR: run `loment ir` on a small program before reading the emitter.
- The native path: `tools/lomelf.py` (the Python twin) and `loment/tools/lomelf.lomt` (the
  self-hosted one).
- The gate: `tools/ci.py`, then any `*_test.py` that fails.

## The rule that applies to every directory

If a directory contains a claim, it contains a criterion. When a chapter of this book or a line of
`docs/` says "the gate checks this", there is a file under `tools/` that fails when the claim stops
being true. When you add a claim, add its criterion.
