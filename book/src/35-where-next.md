# Where next

You can now write, build and run Loment programs, read the artifact's dimensions, and explain a
domain, a GC tier and an LMS level. The rest is practice, reference, and the parts of the project
that live outside a single source file.

## Practice

- **[Loment by Example](../by-example/README.md)** — one concept per file, every block compiled
  by the gate. Read the file closest to what you are trying to write.
- **[Lomentlings](../../exercises/README.md)** — small broken programs with a runner. Fix them in
  order; each one names the idea it teaches.

Both tracks are checked by the same suite as this book. A file that stops compiling turns the
gate red.

## Reference without leaving the terminal

```console
$ loment builtins       # every builtin, with signatures
$ loment syntax         # the syntax cheat sheet
$ loment types          # the type table
$ loment codes          # the diagnostic catalogue
$ loment explain E6     # one code, in full
$ loment cheat          # one page
$ loment example tour   # the annotated whole-language program
```

`loment explain` is the one to remember. Every diagnostic has a card: what went wrong, why, at
least three fixes, and what is supported. The terminal version is the same text.

## The reference material

| Resource | What it is |
|---|---|
| `loment skill` | the language guide, self-contained, printed in full |
| `wiki/` and the Fandom wiki | the language, toolchain and roadmap, one page at a time |
| `docs/` | the numbered design records: every decision and its cost |
| `docs/manual/index.md` | the manual's index |
| [contribution book](../../contributing/src/00-introduction.md) | how the project is built and changed |

The design records are written for maintainers. When a chapter says "the boundary is documented",
the document is the place to look.

## The parts this book touched briefly

- **`comefor` / `byuse`** — define a surface syntax at compile time (chapter 30, `docs/184`).
- **Switches and `addin`** — chapter 30, `docs/182`.
- **LMS** — the seven memory-safety levels and their tiers (chapter 28, `docs/253` and
  `docs/270`).
- **LSPE** — the audit ledger (chapter 31, `docs/262`).
- **`lgc`** — collection timing (chapter 22, `docs/309`).
- **Domains** — the algebra, the context class and domain loops (chapters 23–26, `docs/306` to
  `docs/308`).

Each of these has a design record that goes further than a tutorial can.

## Projects that exercise the language

A few shapes that use most of the book:

1. **A file indexer.** Read a tree with `openat` / `getdents64`, parse names, store records in a
   `vec`, print a report. Exercises streaming, text, arrays and the standard library.
2. **A tiny HTTP server.** `socket`, `bind`, `listen`, `accept`, then parse a request line and
   write a response. Exercises bytes, sockets and buffering.
3. **A tokenizer and calculator.** A hand-written lexer, an enum of tokens, a `match` evaluator,
   and a `Result` for errors. Exercises enums, `match`, `Option`/`Result` and tests.
4. **A command-line tool.** `loment-<name>` on `PATH`, `/proc/self/cmdline`, a switch for verbose
   output. Exercises commands, switches and the project dimensions.
5. **A small library with a `pkg.lomp`.** Publish it locally with `lompi`, use it from a second
   project, pin a lock. Exercises modules and the package identity rules.

Each one is small enough to finish and large enough to hit a real boundary.

## Reporting a problem

The issue tracker is the place. A useful report contains:

- the unit (as small as you can make it);
- the command and its output;
- `loment version`;
- the toolchain you used (reference checkout or installed package);
- what you expected and what happened.

A diagnostic carries a code. `loment explain <code>` prints the maintainers' paragraph for it, and
quoting that paragraph saves a round trip.

## Contributing

The gate is the contract: every claim has a criterion. `python tools/ci.py --fast` runs the quick
set; `CONTRIBUTING.md` explains the rest. If you change the language, the reference compiler and
the self-hosted implementation change in the same commit, and this book's runnable blocks must
still compile. The gate checks all three.

The contribution book goes through the repository layout, the ratchets, the two compilers and the
seed, the bootstrap chain, releases, the docs numbering, the roadmap, and an architecture tour:
[Loment Development Contribution Plan](../../contributing/src/00-introduction.md).

## A closing note

Loment is a language with a small core and a set of explicit boundaries. The core is the type
system, the control flow and the functions; the boundaries are the runtime, the collector, the
memory-safety levels and the capability domains. Every chapter in this book tried to name both.
When you write your own programs, the same habit helps: know which part of the artifact you are
relying on, and write the claim into the source where the next reader can see it.
