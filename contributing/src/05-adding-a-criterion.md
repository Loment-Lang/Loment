# Adding a criterion

A criterion is a Python file under `tools/` with a `TESTS` list and a `main()` that hands it to the
shared runner. The smallest useful one is thirty lines. This chapter walks through the shape and
the three registration points.

## The shape

```python
#!/usr/bin/env python3
# loment_widget_test.py — one sentence saying what claim this measures.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite

TESTS = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_widget_round_trips():
    """A widget written back out is the same bytes it was read from."""
    ...


if __name__ == "__main__":
    sys.exit(loment_suite.run(TESTS, "loment_widget_test"))
```

The docstring is what the gate prints when the test fails. Write it as the claim, not as the
mechanics.

## Register it in three places

1. `tools/ci.py` — add the suite name to `STATIC_CHECKS` and a cost in seconds to `SHARD_COST`.
   An unregistered suite is a file nobody runs.
2. `.github/gate-floors.txt` — add `<suite> <len(TESTS)>`. The ratchet checks it in both
   directions.
3. If the suite is new to the release ledger, see [Adding a tool](06-adding-a-tool.md) — the file
   itself is a tracked `tools/*.py` and has its own registration.

## Write it against the unfixed tree

Run the new criterion before changing any behaviour. It must fail, and it must fail for the reason
you intend. A criterion that is green on its first run usually means:

- the assertion never reaches the code path (a downstream check rejected the input first);
- the fixture does not contain the case;
- the test compares something that is equal for unrelated reasons.

If it cannot be made red, the change might already be covered — search the existing suites before
adding a duplicate.

## Prefer bytes to shapes

- Compare produced bytes, exit codes and stderr, not "contains a message".
- Run both sides of a twin and compare outputs byte for byte.
- Use a real artifact (a real `.o`, a real form object, a real release tarball) as the input.

## Keep it deterministic

- No wall-clock ratios as thresholds. The same quantity varies several-fold between machines and
  between runs; a bound inside that variance is a flaky test.
- No network. If a criterion needs an HTTP server, start one on a local port and shut it down.
- Write temporary files to a temporary directory, never into a directory another criterion globs.

## Measure the cost

`SHARD_COST` is the gate's only load-balancing signal. Time the suite on the CI runner or use a
conservative estimate from a similar suite, and say in a comment that it is an estimate. An
unregistered cost falls back to a default and can push one shard far past its neighbours.

## When the criterion needs another host

If it must run on Linux or inside WSL, make the skip **visible** and record the reason. A criterion
that silently returns 0 on the wrong host is worse than one that does not exist: the skip count is
one of the gate's signals.
