# The gate

The gate is `tools/ci.py`. It discovers every criterion, runs them in parallel shards, and compares
the result with three files in `.github/`. This chapter is how it decides red.

## What it runs

Each criterion is a Python file under `tools/` named `<suite>_test.py`. `ci.py` holds the list in
`STATIC_CHECKS`: adding a file is not enough; the suite must be registered there and given a cost
estimate in `SHARD_COST`.

Two kinds of work are separated:

- **static criteria** — everything that does not depend on wall-clock timing. These are what the
  ten shards run.
- **exclusive criteria** — the few that write a shared location in the repository. They run alone
  (`EXCLUSIVE_STATIC`) because a parallel neighbour would read a half-written tree.

Criteria that need another host (WSL, a vendored counterpart) either skip visibly or fail with a
baseline reason.

## The three ratchets

| File | What it pins | Direction |
|---|---|---|
| `.github/gate-floors.txt` | The number of criteria per suite. | **Both** — fewer means someone deleted a test; more means a test was added without updating the file. |
| `.github/gate-skips.txt` | Criteria allowed to skip, per host. | **Only shrinks.** A skip that disappears is fine; a new one is a change to explain. |
| `.github/gate-baseline.txt` | Red criteria that already fail on `main` for environmental reasons. | Named and justified; not a place to park regressions. |

The floors file is a two-way ratchet. Add a criterion without touching it and `main` goes red.
This is deliberate: the file is supposed to tell the truth about how many criteria exist.

## New red versus flake

When a shard fails, the gate re-runs each new red **alone**. If the suite passes on its own, it is
recorded as a parallel-run flake; if it fails alone, it is confirmed as a new red and the job ends
with that name.

That distinction matters locally too. Before you call something a regression:

1. run the suite three times;
2. run it on an untouched checkout of `origin/main`;
3. compare the failure signature, not just the colour.

## Running it locally

```console
$ python tools/ci.py --fast           # quick set
$ python tools/ci.py --static-only    # all static criteria, one host
$ python tools/loment_tools_test.py   # one suite, useful while iterating
```

Run from the repository root. Several criteria resolve paths relative to the current directory, and
a run from elsewhere can write temporary artifacts into a different checkout.

## The CI side

`.github/workflows/gate.yml` runs the static checks in ten shards inside a pinned image, plus a
separate job for the exclusive suite, the `lom/` door check, and the generated-artifact check.
The image tag is part of the contract: a change that only passes on a newer image is a change that
has not passed yet.

## What the gate is not

It does not prove the product is correct. A twin criterion can be green while both implementations
are wrong in the same way. It proves that the claims the project makes are still measured, and that
the measurements still agree with the recorded expectations. For anything a twin cannot catch, the
project adds a third, independent expectation — usually a real program compared against an
independent result.
