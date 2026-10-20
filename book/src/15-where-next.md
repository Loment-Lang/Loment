# Where next

You can now write, build and run Loment programs. The rest is practice and reference.

## Practice

- **[Loment by Example](../../book/by-example/README.md)** — one concept per file, all compiled by
  the gate. Read the file closest to what you are trying to write.
- **[Lomentlings](../../exercises/README.md)** — small broken programs with a runner. Fix them in
  order; each one names the idea it is teaching.

## Reference, without leaving the terminal

```console
$ loment builtins       # every builtin, with signatures
$ loment syntax         # the syntax cheat sheet
$ loment types          # the type table
$ loment codes          # the E001–E033 catalogue
$ loment explain E6     # one code, in full
$ loment cheat          # one page
```

The language guide (`loment skill`) is the long reference. The full manual and the design
documents live in the repository under `docs/`; `docs/manual/index.md` is the index.

## The parts of the language this book only touched

- **`comefor` / `byuse`** — define a new surface syntax at compile time. `docs/184` has the shape.
- **`choose` switches and `addin`** — `set choose` defines a named bundle of dimension values;
  `addin` pulls one in. `docs/182` and `docs/217`.
- **`choose hosted`** — the unfinished path to real libc and shared libraries. A release build
  refuses it.
- **LMS / LSPE** — the memory-safety and safety-audit lines. They are landing in `0.1.5.x`; the
  design documents are `docs/247` and `docs/262`.

## If something breaks

The project's issue tracker is the place. A useful report contains the unit, the command, the
toolchain version (`loment version`) and what you expected. The compiler's diagnostics carry a
code (`E001`…`E033`); `loment explain <code>` turns it into the paragraph the maintainers wrote for
it.

## Contributing

The gate is the contract: every claim has a criterion. `python tools/ci.py --fast` runs the quick
set; `CONTRIBUTING.md` explains the rest. If you change the language, the reference and the
self-hosted implementation must change in the same commit, and the book's runnable blocks must
still compile — the gate checks all three.

The second book goes through all of it:
[Loment Development Contribution Plan](../../contributing/src/00-introduction.md) — the
repository layout, the gate and its ratchets, the two compilers and the seed, the bootstrap
chain, releases, the docs numbering, the roadmap, and an architecture tour.
