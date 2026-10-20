# Environment

The toolchain is Python plus whatever the native path needs — which is nothing beyond Python for
the default build. This chapter is what to install, and what to expect on each host.

## What you need

- **Python 3.11 or newer.** The CI gate runs 3.12. If you are chasing a criterion that only fails
  in CI, check the version before checking the code.
- **A checkout of this repository.** Most criteria resolve paths from the repository root; run
  them with the current directory at the root.
- **Nothing else for the native path.** `lomelf` turns the emitted IR into machine code directly;
  there is no clang, no LLVM and no libc in it. `--opt` is the exception: it hands the IR to clang
  when one is installed.

## Windows

- Set `PYTHONUTF8=1` before running criteria. Without it, Python decodes subprocess output with the
  console code page and a passing suite can fail with a decoding error, or a failing one can report
  something unrelated.
- The native backend emits **PE**, not ELF. A program built on Windows runs on Windows; it is not
  the same artifact as the Linux build.
- Criteria that need a real Linux ELF run under WSL. When WSL is unavailable they are skipped
  visibly; a skip count that drops to zero is worth looking at.

## Linux

- This is the CI host. `lomelf.compile_ll` produces ELF and the tests go through the same path the
  gate uses.
- The gate itself runs inside a pinned container image
  (`ghcr.io/loment-lang/loment-ci:<tag>`), so "works on my machine" and "works in the gate" can
  still differ in the image tag. `CI_IMAGE_TAG` and `CI_IMAGE_DIGEST` are printed in the job log.

## Two toolchains

There are often two `loment` commands on a machine:

- the one from an installed package (which may lag `main` by dozens of commits);
- the one built from this checkout (`loment/dist/` or `python tools/loment_dist.py --emit`).

When a bug looks impossible, check which one you ran. `loment version` prints where it came from.

## First commands

```console
$ python tools/ci.py --fast          # the quick set of criteria
$ python tools/ci.py --static-only   # every static criterion, no wall-clock suites
$ python tools/loment_book_test.py   # just the book's runnable blocks
```

A single suite is a single file:

```console
$ python tools/loment_tools_test.py
$ python tools/loment_rel_test.py
```

## What "red" means here

A criterion exits non-zero and prints the failing test name. The gate distinguishes:

- **baseline red** — already failing on `main` for an environmental reason (for example a vendored
  counterpart that only exists in the FujoOS repository);
- **new red** — introduced by the change under test. The gate re-runs a new red alone to separate
  a parallel-run flake from a real regression;
- **skip** — the criterion cannot run on this host. Skips are visible, never silent.

If you are unsure which one you are looking at, run the same suite three times and compare with
`origin/main` before calling it a regression.
