# From source to an executable

This chapter is a map, not a manual. Everything here has a design document under `docs/`; the
point is to know what the pieces are called and which one to open when something goes wrong.

## The pipeline

```
hello.lomt
   │  lex            tokens
   │  check          diagnostics (E001–E033)
   │  emit           LLVM IR (a linear subset)
   ▼
hello.ll
   │  lomelf         assemble + link, no clang, no libc
   ▼
hello        (ELF on Linux, PE on Windows)
```

`loment ir hello.lomt` stops after the IR; `loment build` runs the linker. The IR is deliberately
plain: functions, allocas, loads and stores, calls, branches, a small set of integer instructions.
`lomelf` turns it into machine code directly — there is no optimizer in this path, which is why a
Loment binary is bigger and slower than the equivalent C compiled with `-O2`. `--opt` hands the IR
to clang when one is installed.

## Two implementations of the same language

The language surface exists twice:

- `tools/lomentc.py` — the **reference** implementation (lexer, checker, three emitters);
- `loment/selfhost/*.lomt` — the **self-hosted** compiler, written in Loment.

They are required to agree byte for byte: same diagnostics, same IR. The gate compares them.
The self-host side is what ships, because it needs no Python.

## The bootstrap chain

The self-hosted compiler cannot be compiled by itself from nothing. The chain is:

```
genesis ELF (committed binary)
   └─ assembles the seed  loment/build/selfhost_driver.ll
        └─ stage1 (a compiler)
             └─ stage2 (a compiler that compiles the compiler)
                  └─ fixed point: stage2 == stage3
```

`sh loment/bootstrap.sh` runs the chain and proves the fixed point. It takes twelve to fourteen
minutes on a laptop. The genesis binary is the one committed artifact in the chain; its
reproducibility is a supply-chain question, and it has its own criteria.

## The form object (Potato)

`--emit-potato` writes a **form object** (the packaged driver prints it to stdout; the reference
CLI takes an output path):
a JSON description of what the unit is, what it imports, its capabilities, its layouts, its
guards, its chosen dimensions, and a body fingerprint. It is the artifact the package manager, the
safety tooling and the VM read. Its schema is versioned (`potato: "v15"` today).

## The LVM

LVM is a bytecode VM planned for the language. The interpreter (`lvm IN.ll`) exists and is part of
the twin criteria, but `choose vm` is **closed in release builds** — the dimension is a development
switch until the engine ships. A program that says `choose vm` will be refused by a packaged
compiler, and that refusal is intentional.

## The gate

Every claim in this project is supposed to be measured by a criterion under `tools/`. The gate
(`tools/ci.py`) runs them in parallel shards; the API criterion for this book is
`tools/loment_book_test.py`, which compiles every `rust,run` block you have read. If a chapter
stops compiling, the gate goes red before the book is published.

Where to read more:

| Topic | Document |
|---|---|
| the language | `docs/143` |
| the native backend | `docs/144` |
| the self-host chain | `docs/150`, `docs/167` |
| capability semantics | `docs/146` |
| the library system | `docs/168` |
| the LVM | `docs/243` |
| the standard | `standard/LLS.md` |
