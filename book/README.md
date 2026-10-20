# Learn Loment

Three tracks, one language. Pick the one that matches how you learn.

| Track | What it is | Start here |
|---|---|---|
| **The Loment Book** | A long-form guide from first principles, with a project in every part. Read it end to end if you are new. | [book/src/00-introduction.md](src/00-introduction.md) |
| **Loment by Example** | One concept per file: a short annotated program you can compile and run. Use it when you need a working shape, not an explanation. | [book/by-example/README.md](by-example/README.md) |
| **Lomentlings** | Small broken programs to fix, checked by a runner. Use it to make the syntax stick. | [exercises/README.md](../exercises/README.md) |

All three are checked into this repository and **compiled by the gate**:
`tools/loment_book_test.py` extracts every block marked `rust,run` from the book and by-example
files, builds it with the repository toolchain, runs it, and compares the output when the block
declares one. A chapter that stops compiling turns the gate red — the book cannot rot quietly.

## The one thing that is different

Loment is a systems language with capabilities. Everything else — the Rust-flavoured syntax,
structs, enums, `match`, `Result`, generics, traits — is familiar. The part worth slowing down for
is [capability domains](src/10-capabilities.md): a unit declares what it may reach, and `guard`
is a runtime check against that declaration.

## What this book does not cover

- The compiler's internals: see `docs/`, starting with `docs/143` (the language), `docs/144` (the
  native backend) and `docs/150` (the self-host chain).
- The package manager's model: see the lompi guide, shipped as `lompi skill`.
- The full builtin table and the E-code catalogue: `loment builtins`, `loment codes`, and the
  language guide (`loment skill`).
