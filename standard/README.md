# `standard/`

**Loment Language Standard** — **LLS**.

| | |
|---|---|
| `LLS.md` | The standard. |

The abbreviation follows the project's own family — `LGC` (Loment GC), `LVM` (Loment VM), **`LLS`**
(Loment Language Standard): an `L`, then the domain.

## What this is, and what it deliberately is not

**A frame, not a second copy.** The repository's rule is that the authoritative copy of anything
exists in exactly one place, and that everything else is a pointer or a byte-identical copy —
because a second copy drifts, and nothing notices. The standard therefore **states no language
rules of its own**. It states what the language *is* for two purposes: deciding conformance, and
fixing which parts may change and how.

Everything normative is named by path in `LLS.md` §2. If you are looking for what `guard` does,
read `docs/146`; the standard will not tell you, on purpose.

## The one number

The standard's version **is** the version of the language surface it defines — today `0.1.4.6`.
There is no separate edition axis, because that would require a table mapping standard editions to
language versions, and that table would be a second source of truth. `LLS.md` §6.4 says so in
full.

## What it does *not* say

Which switches a release build accepts. That is a fact about a build, not about the language, and
it is read from `tools/loment_channel.py:RELEASE` (`LLS.md` §4.4). Restating it here would make
this directory go stale at the next release.

Likewise, no totals: the conformance suite prints its own counts, and `LLS.md` §5.2 explains why
copying them in would be a mistake this project has already made once.

## Changing it

The standard has no rules to change. It changes when the things it points at change:

- a change to the **frozen surface** follows `docs/158` §5 — four steps, and the standard is
  reissued with the new version **in the same commit**;
- a change to the **switch set or the contradictory pairs** is a change to the frozen surface, so
  it goes the same way;
- anything that would alter what the language is without changing the surface is a sign the change
  belongs somewhere else — not here.

## Where it sits in the tree

`standard/` is a top-level tree, like `papers/`. It is **not** a `docs/NNN` record: those are
design and measurement records, and this is a normative document. It is consequently outside the
doc-numbering gate and outside the translation-stamp gate; the standard is written in English
only, and `LLS.md` §9 states plainly that most of what it references is written in Chinese.
