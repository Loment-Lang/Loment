---
name: loment-dev
description: Development workflow for the Loment-DEV repository (FujoJTOP/loment, mirrored as Loment-Lang/Loment), currently at the 0.1.6 generation. Use before any commit, push, or PR work in this repo - especially when continuing work in a branch or worktree from an earlier session. Covers the PR-bound check (a branch whose PR is already merged/closed must not receive new commits), branch/worktree discipline from origin/main, the hand-written vs generated commit split (seed, genesis provenance, trust.json, gate floors/skips), the serial landing rule for seed-touching changes, and the verification set that must pass before a push is called done.
---

# Loment-DEV: how work lands in this repo

This is the **development** repo for Loment. The language itself is documented by the sibling
`loment*` skills; this skill is about **how a change gets committed**, which has cost several
sessions when the rules were assumed instead of checked.

## 0. The first rule: a PR that is closed stays closed

**Check the PR's state before every commit and every push.** A branch whose PR was merged (or
closed) still accepts pushes, and those pushes are invisible: no PR updates, no CI, nothing
lands. This happened with PR #1504: two LSPE/E9 commits were pushed to
`feat/lspe-0152-complete` after it had been merged, and had to be cherry-picked onto a new
branch afterwards.

The check, before committing *and* before pushing:

```bash
gh pr view --json number,state -q '"\(.number) \(.state)"' || true
gh pr list --head "$(git branch --show-current)" --state all --json number,state,mergedAt
```

* `OPEN` → proceed.
* `MERGED` / `CLOSED`, or no PR at all for the branch → **do not push to it**. Branch a fresh one
  from `origin/main`, bring the new commits over (`git cherry-pick <sha>` or `git merge`), and
  open a new PR.
* After opening a PR, write its number down in the session notes; re-check before each push.
  Auto-merge can land the PR between two of your own commits.

**Never assume "the PR is still open because I opened it this session."** PRs merge while you
work; that is what auto-merge is for.

## 1. Branch and worktree

* Branch from **`origin/main`**, not local `main`. Local `main` is routinely hundreds of commits
  stale, and an old base turns other people's regressions into "your new red".
* One worktree per task (`git worktree add <path> -b <branch> origin/main`). Do not switch
  branches in the user's main checkout; another session may be working there.
* Before starting, `git fetch origin` and compare `HEAD..origin/main`.

## 2. Commit shape

* **Hand-written commit** and **generated-artifact commit** are separate. The generated one is
  titled `[生成物] 只有生成物：…` and contains nothing else.
* Generated artifacts that must follow a source change (the current generation, 0.1.6):
  * `loment/build/selfhost_driver.ll` — `python tools/loment_seed.py --emit`;
  * `loment/build/genesis/provenance.json` + `SHA256SUMS` + `seed.sha256` —
    `PATH="/c/Program Files/LLVM/bin:$PATH" python tools/loment_genesis.py --emit`
    (clang is installed but not on PATH on this machine; it is 22.1.8, the same version the
    record pins). `provenance.json` is now **format 2**: it carries `source_closure`,
    `genesis_source_closure` and `seed_env`, and `bootstrap.sh` verifies `seed.sha256`
    before executing genesis, failing closed on drift (`LOMENT_ALLOW_DRIFT=1` is for
    experiments only);
  * `loment/build/trust.json` — `python tools/loment_trust.py --emit`; it records the root,
    recipe, trust surface and budget (the current root is seed `7,501,283` B / cap
    `8,388,608` B). A stale one is a red criterion (`loment_trust_test`);
  * `.github/gate-floors.txt` when a suite gains/loses criteria — it is a **two-way ratchet**
    (a new criterion without the floor line turns main red too); `.github/gate-skips.txt` may
    only **shrink**;
  * `.github/lspe-pass-baseline.txt` and `.github/lspe-coverage-baseline.txt` when a corpus
    unit's sites change (`python tools/lspe_ratchet.py --emit` — that call is the explicit
    acknowledgment);
  * `docs/270-lms-0151.md` seed `bytes:`/`sha256:` whenever the seed changes
    (`loment_lms_test` recomputes them and fails otherwise).
* **Seed-touching changes land one at a time.** Genesis binds the seed hash and the floors
  bind the seed bytes, so two parallel PRs that both change the seed make each other red.
  Serialise them or fold them into one PR.
* A new tracked `tools/*.py` must be added to **both** `tools/loment_release.py:GLOBS` and
  `loment/tools/lomrel.lomt`'s `globs_text()` at the same position, and to
  `tools/loment_publish.py`'s `paths` if the gate closure imports it.
* Commit messages are **Chinese**; PR titles and bodies are **English** (user's rule). No bare
  `#123` in prose — write `owner/repo#123`.

## 3. Before calling a push done

Run the suites that the change can reach; do not dispatch a PR with a green tree you did not
test. The usual minimum:

```bash
PYTHONUTF8=1 python tools/lspe_test.py
PYTHONUTF8=1 python tools/loment_potato_emit_test.py   # seed freshness + twin
PYTHONUTF8=1 python tools/loment_lms_test.py           # seed floor numbers
PYTHONUTF8=1 python tools/loment_publish.py --check    # gate closure
PYTHONUTF8=1 python tools/loment_caps_test.py
PYTHONUTF8=1 python tools/loment_choose_matrix_test.py # every legal choose cell
PYTHONUTF8=1 python tools/loment_trust_test.py         # trust.json / provenance
```

Add `loment_p8_test.py` (self-host IR), `loment_seed_test.py` (bootstrap fixed point),
`loment_tools_test.py` and `loment_cli_test.py` when `loment/selfhost/**` or `tools/**`
changed; `lnotary_test.py` when the artifact/Potato shape changed; `loment_oracle_test.py`
when the semantics oracle or a builtin changed; `loment_pe_test.py` and `loment_ffi_test.py`
for the PE shim / FFI paths. On this machine set `PYTHONUTF8=1`; without it subprocess output
is decoded as GBK and the suite reports false reds.

The CI gate now runs **10 shards** (`ci.py` balances by measured cost, not by criterion count)
and `python tools/loment_dev.py gate` wraps the same run locally. The gate criteria are
`ci.py --static-only`; a new `tools/*.py` must be added to both `loment_release.py:GLOBS` and
`lomrel.lomt`'s `globs_text()`, and a new criterion needs its `gate-floors.txt` line in the
same PR.

## 4. PR mechanics

* `gh pr create` from the checkout so the remote's repo is used; the repo may be
  `Loment-Lang/Loment` even when the memory says `FujoJTOP/loment` (the same repo, renamed).
* The user's standing preference is branch + PR + **auto-merge**. Use `--merge` when the PR mixes
  hand-written and generated commits (never squash those); `--squash` otherwise.
* Auto-fix is enabled for the session: CI failures, merge conflicts and review comments wake the
  session; fix, verify, push — no need to ask, and do not poll CI yourself.
* If the base branch moved and the PR is `CONFLICTING`, merge `origin/main` into the branch
  (never rebase/force-push), resolve, **regenerate the generated artifacts**, and push.
  `gate-floors.txt` is a two-way ratchet: take the new count from both sides.

## 5. The 30-second pre-push checklist

1. `git status` clean, nothing unintended staged (no `.lspe-tmp`, no `.lspe-cache`).
2. `gh pr view --json state` → `OPEN` (or you are on a brand-new branch).
3. Seed/floor/provenance regenerated if sources changed.
4. The reachable suites above are green.
5. Hand-written and generated commits are separate.
