# Your first PR

This chapter is the short path from "I want to change something" to "the gate is green and the
pull request is merged". The details are in the chapters before it.

## 1. Find or file an issue

Search the issue list first; a duplicate costs more than it saves. If there is no issue, write one:
the unit, the command, `loment version`, what happened, what you expected. A minimal reproduction
is worth more than a paragraph.

## 2. Branch from `origin/main`

```console
$ git fetch origin
$ git switch -c fix/the-thing origin/main
```

A branch from a stale local `main` reintroduces failures that were fixed upstream.

## 3. Write the criterion first

Add or extend a suite under `tools/`, run it, and watch it fail for the reason the issue describes.
If it passes, the assertion does not reach the behaviour. Then fix the code until it passes.

## 4. Register what you added

- new suite → `STATIC_CHECKS` and `SHARD_COST` in `tools/ci.py`, plus a line in
  `.github/gate-floors.txt`;
- new `tools/*.py` → `loment_release.GLOBS` and `lomrel.lomt`, at the same position;
- language change → both compilers, plus a separately committed regenerated seed.

## 5. Run it locally

```console
$ python tools/ci.py --fast
$ python tools/<the-suite-you-touched>_test.py
```

Then the neighbouring suites for the area, and, for a language change, a bootstrap run.

## 6. Open the pull request

- Title and body in English; commit messages in Chinese.
- Say which issue it closes and what the criterion proves.
- Keep generated artifacts out of the PR unless the change is to a generated file's source; the
  seed is the exception, and it gets its own commit.
- Expect the gate to fail on the first run in one of the registration places above. That is the
  ratchet working, not a rejection.

## 7. Read the red

The gate separates:

- **new red** — re-run alone; if it fails alone, it is yours;
- **baseline red** — already failing on `main` for environmental reasons;
- **flake** — green when run alone, red in the shard.

Fix the first, ignore the second, re-run the third.

## 8. Merge shape

- Handwritten only → squash.
- Source plus generated artifacts → merge commit, so the generated commit stays visible.

## What reviewers look for

- A criterion that fails before the fix.
- A change small enough to read.
- Generated files regenerated, never hand-edited.
- Claims in the PR body that are measured, not estimated.
- No deleted criteria, no relaxed floors, no new skips without a reason.

## If the change is rejected

Usually the fix is to split it. A PR that changes the language, the gate and the release workflow
at once cannot be reviewed; three PRs that each carry their own criterion can.
