# The bootstrap chain

A compiler written in its own language needs a starting point. Loment's is a committed ELF binary
called **genesis**, and the chain that grows out of it is the strongest self-check the project has:
the compiler must be able to compile itself into an identical compiler.

## The chain

```
genesis ELF (committed)
   └─ assembles the seed  loment/build/selfhost_driver.ll
        └─ stage1 (a compiler)
             └─ stage2 (a compiler that compiles the compiler)
                  └─ stage3
                       └─ fixed point: stage2 == stage3
```

At each stage the input is the compiler's own source. The chain is done when two consecutive stages
produce byte-identical output.

## Running it

```console
$ sh loment/bootstrap.sh
```

It takes twelve to fourteen minutes on a laptop. That is normal; it is not hung.

The seed is the file every stage reads. It is regenerated from source with
`python tools/loment_seed.py --emit`, and `loment_seed_test` fails if the committed seed is stale.

## Genesis

Genesis is the one committed binary in the chain:

- `loment/bootstrap.sh` executes it; it is the root of trust;
- its reproducibility is its own criterion (`loment_genesis_test`), because "the committed binary
  is the one this source produces" is a supply-chain claim, not a build detail;
- its bytes depend on the local clang: the same source built with a different LLVM version can
  differ by several kilobytes. The hash check proves the file has not changed; it does not prove
  the source still produces it.

Do not rebuild genesis casually, and do not assume a hash match means the source is unchanged.

## The size cliff

The seed is IR, and it grows. There is a size beyond which the compiled stage1 starts failing in
ways unrelated to the change that grew it. In October 2026 the seed is about 4.24 MB and
bootstraps; the historical cliff was observed around 3.71 MB and has moved as the chain changed.

The lesson is not the number. It is that **a growth of a few kilobytes can change whether the
chain runs at all**, so a change that adds a lot of generated IR is tested by bootstrapping, not by
reading the diff.

## Hosts

`build_stage1` builds a stage1 for the host platform: PE on Windows, ELF on Linux. The packaged
release builds both from the same IR. The bootstrap chain itself is normally run on Linux; when you
change it, say which host you ran it on.

## What to do when the chain disagrees

1. Check that the seed is current (`loment_seed_test`).
2. Bootstrap on a clean tree at `origin/main` and compare: if `main` also disagrees, it is not your
   change.
3. If only your branch disagrees, look for a construct the two compiler implementations handle
   differently before looking at code generation.
4. Never "fix" a disagreement by hand-editing the seed or a stage output.
