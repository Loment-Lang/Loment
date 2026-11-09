# `standard/`

**Loment Language Standard** — **LLS**.

| | |
|---|---|
| `LLS.md` | The standard. **Normative.** |

The abbreviation follows the project's own family — `LGC` (Loment GC), `LVM` (Loment VM), **`LLS`**
(Loment Language Standard): an `L`, then the domain.

## What this is

**The sole normative original.** LLS defines what Loment is: the surface language, the
editions, the type system, the semantics, the safety layers, the artifact contract, and
what a program must do to be called a Loment implementation.

Everything else is **informative**:

- `docs/` holds design records, measurements, history, and rationale. Useful, often the
  reason a rule exists; not the rule.
- `tools/` and `loment/selfhost/` are implementations — evidence that the language can be
  implemented, not definitions of it.
- `.claude/skills/loment-*` are practical guides derived from the implementations.
- The conformance suite is the **instrument** that decides whether an implementation
  conforms. A criterion that contradicts LLS is a defect in the criterion, not a second
  reading of the language.

Edition 2 of the document changed the authority model: Edition 1 described itself as a
*frame* that pointed at rules kept elsewhere; Edition 2 **is** the rules.

## The one number

The standard's version **is** the version of the language surface it defines — today
**`0.1.5.1`**. There is no separate edition axis for the document beyond Edition 2's
authority-model change; a language rule change produces a new version in the same commit.
`LLS.md` §6.4 states the rule.

## What it does *not* say

Which switches a release channel exposes. That is a fact about a build, not about the
language (`LLS.md` §25.8). Likewise, no totals: the conformance suite prints its own
counts, and §38.5 explains why copying them in would create a second source of truth.

## Changing it

A change to the frozen surface follows §6.2: change LLS first, add the criterion, change
both implementations in one commit, pass the static gate. A change to an informative file
does not change the language and must not be used to smuggle one.

## Where it sits in the tree

`standard/` is a top-level tree, like `papers/`. It is **not** a `docs/NNN` record: those
are design and measurement records, and this is the normative document. It is consequently
outside the doc-numbering gate and outside the translation-stamp gate; the standard is
written in English only, and `LLS.md` §2.2 states that most of the material it cites is
written in Chinese.
