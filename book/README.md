# Using Loment

Two books, one language.

| Book | What it is | Start here |
|---|---|---|
| **Using Loment** (this book) | How to install the toolchain, write programs, and use the language from first principles, with a project in every part. | [book/src/00-introduction.md](src/00-introduction.md) |
| **Loment Development Contribution Plan** | How the project is built and how to change it: the repository, the gate, the two compilers, the bootstrap chain, releases, the roadmap, and the architecture tour. | [contributing/src/00-introduction.md](../contributing/src/00-introduction.md) |

Two practice tracks go with the usage book:

| Track | What it is | Start here |
|---|---|---|
| **Loment by Example** | One concept per file: a short annotated program you can compile and run. Use it when you need a working shape, not an explanation. | [book/by-example/README.md](by-example/README.md) |
| **Lomentlings** | Small broken programs to fix, checked by a runner. Use it to make the syntax stick. | [exercises/README.md](../exercises/README.md) |

All of these are checked into this repository and **compiled by the gate**:
`tools/loment_book_test.py` extracts every block marked `rust,run` from the usage book and the
by-example files, builds it with the repository toolchain, runs it, and compares the output when
the block declares one. A chapter that stops compiling turns the gate red — the book cannot rot
quietly. `tools/loment_book_site.py` renders the two books into the Pages site, and
`tools/loment_book_site_test.py` checks every page and every link.

## The one thing that is different

Loment is a systems language with capabilities. Everything else — the Rust-flavoured syntax,
structs, enums, `match`, `Result`, generics, traits — is familiar. The part worth slowing down for
is [capability domains](src/11-capabilities.md): a unit declares what it may reach, and `guard`
is a runtime check against that declaration.

## What the usage book does not cover

- The compiler's internals, the gate, releases and the roadmap: the
  [contribution book](../contributing/README.md) covers them.
- The full builtin table and the E-code catalogue: `loment builtins`, `loment codes`, and the
  language guide (`loment skill`).
