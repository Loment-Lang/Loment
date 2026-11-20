# Security policy

## What counts as a vulnerability here

Loment's security claim is narrow, and it is written down rather than implied. The claim is not
"programs are safe" — it is that **what a unit may reach is declared, bounded, and auditable**,
and the repository says exactly how far that goes.

Three properties are supposed to hold today ([docs/146](docs/146-loment-capability-semantics.md),
§4):

1. A `guard` that returns has an index inside `[lo..hi]`; an index outside it **traps, with no
   other side effect**.
2. A domain's audit entry count equals the number of guards on it that passed.
3. The two implementations of the language surface (`tools/lomentc.py` and
   [`loment/selfhost/`](loment/selfhost/)) emit **byte-identical** IR.

**A bug that breaks one of those is a vulnerability, not a missing feature.** So is anything that
lets a form object claim a reach the source does not have — the entire point of exporting that
object is that a third party can trust it without reading the source.

## What is *not* a vulnerability here

These are known and documented. Please check this list first:

- **A guard is not an authorization check.** It bounds an *index*, not a *subject*; binding a
  subject to a capability is the kernel's job ([docs/146](docs/146-loment-capability-semantics.md)
  §5 states this boundary rather than glossing it).
- **`revocable` is a declaration** in the language layer, not enforcement.
- **The FFI boundary is not a sandbox.** A statically linked C library can do whatever the
  process can, and the process bridge runs another interpreter with your privileges.
- **Nothing is published as a package yet**, so there is no installed artifact to attack, and no
  version to patch.

## Release and CI integrity

Two repository properties keep a change from certifying itself. Both are deliberate and
neither is optional:

- **`.github/CODEOWNERS` covers the judge and the release tools** — `.github/` (all
  workflows and the three `gate-*` budget files), `tools/ci.py`, `tools/loment_suite.py`,
  `tools/loment_dist.py` and `tools/loment_sign.py`. Branch protection on `main` must
  require **a review from a code owner** for these paths, and must require the branch to be
  up to date before merging. That requirement lives in GitHub's repository settings, not in
  this file; a change that removes CODEOWNERS coverage is itself covered by it.
- **The release has an anchor outside the archive.** `SHA256SUMS` ships inside the package
  it describes, so it cannot prove the package. Every release therefore publishes
  `SHA256SUMS.asc` (detached GPG signature), `loment-signing.asc` (public key) and
  `FINGERPRINT`, and the release **body** lists the digest of every asset. The installer
  prints the digest of `SHA256SUMS`; compare it with the release body. The release workflow
  refuses to publish when the signing secret (`LOMENT_RELEASE_SIGNING_KEY`) is absent.

The installers additionally refuse to install when no SHA-256 verifier exists (rather than
silently skipping verification), copy only files named by the verified manifest, invoke
PowerShell by absolute System32 path, and reject archive entries that resolve outside the
extraction root. Those are enforced by `tools/loment_dist_test.py`.

## Reporting

**Privately**, through *Security* → *Report a vulnerability* on this repository. That channel is
enabled and reaches the maintainer. Please do not open a public issue for a suspected
vulnerability.

A report is much easier to act on with four things: the exact source, the exact commands, what you
expected, and what happened instead. This repository's bug template asks for the same four.

## Versions

`0.1.4` is pre-release. No package is published, no version is supported, and there are no
backports — a fix lands on `main` and nowhere else yet.
