# Releases

A release is produced by pushing a tag, not from someone's laptop. `.github/workflows/release.yml`
builds the packages and creates the release; the Deployments tab records each run under the
`release` environment.

## The trigger

```console
$ git tag v0.1.4.7
$ git push origin v0.1.4.7
```

Tags ending in `-pre` are skipped: they carry release notes, not artifacts. A manual
`workflow_dispatch` can run the same job with a `dry_run` input.

## What it builds

`tools/loment_dist.py --emit` produces the distribution from the **committed seed**, so the runner
needs no clang, no LLVM and no WSL:

- a stage1 is linked from `loment/build/selfhost_driver.ll` and executed;
- both platform packages are built from the same IR;
- the Windows installer is produced with `iexpress`, which is why the job runs on
  `windows-latest`;
- `--check` compares the artifacts against the ledger before anything is published.

The job has a 60-minute timeout. Historically the Windows runner has exceeded it for a build that
takes under three minutes locally; if the job times out, that is a workflow problem, not a compiler
problem.

## Generated artifacts belong to `main`

A pull request carries the seed if it changed. It does **not** carry `release-manifest.json` or
`SHA256SUMS`: those are regenerated on `main` by the manifest workflow, which is the single writer.
This keeps every PR reviewable and avoids two branches racing to update the same ledger.

## The environment

The job declares `environment: release`. The environment's deployment branch policy allows `main`
and `v*` tags. It is a record, not an approval gate: a tag push deploys.

The Pages site has its own environment, `github-pages`, managed by `book.yml`; the two appear
separately in Deployments.

## What a tag does not guarantee

Today a published release can still be edited, and the workflow can overwrite assets on an existing
tag. Until that changes, a tag is a pointer to a commit, not a promise about the bytes attached to
it. Verify the checksums in `SHA256SUMS` if the bytes matter.

## Before you tag

- the gate is green on the commit you are tagging;
- the seed is current (`loment_seed_test`);
- the release notes say what is new, what is fixed, and what is not in this release;
- the version string in the package matches the tag.
