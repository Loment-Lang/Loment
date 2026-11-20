# Docs and the standard

Two written artifacts have rules of their own: the numbered design records under `docs/`, and the
language standard at `standard/LLS.md`.

## `docs/NNN-slug.md`

- The number is unique and monotonic. Take the next one with
  `python tools/loment_docs.py --next`; if two branches collide, the tool can renumber.
- The slug stays with the number for the life of the document.
- An English twin is `docs/NNN-slug.en.md` — same number, same slug.
- Translations live in `docs/i18n/`; the glossary there fixes terminology across languages.
- `docs/manual/index.md` is the index of the manual; a new design record is not automatically part
  of it.

A design record is not a promise that the feature exists. Several documents describe work that is
scheduled or partially landed; the chapter or record should say which. When you cite behaviour,
cite the implementation, not the design.

## `standard/LLS.md`

The LLS is the **sole normative original** (Edition 2). It defines what Loment is — the surface
language, editions, type system, semantics, safety layers, artifact contract, and what a program
must do to be called a Loment implementation. `docs/` is **informative**: rationale, history, and
measurement, never the rule.

- The standard's version number is the language version. There is one number, not two.
- Section 4 is **implementation qualification** (conformance classes, the common MUSTs, how to
  claim and withdraw). Section 25 is the **switch** system. Section 38 is the conformance suite.
- Changing the standard is a language-level change: it follows §6.2 (LLS first, criterion second,
  both implementations in one commit, then the gate) and the generated-seed rule.
- A criterion that contradicts the standard is a defect in the criterion. Neither the suite nor an
  implementation is a second reading of the language.

## Displaying source

`.lomt` and `.lom` files are not recognised by common Markdown highlighters. When displaying Loment
source — in chat, in `docs/`, in an issue — use a `rust` fence. The overlapping syntax colours
correctly; only `capability`, `guard`, `excluded` and `revocable` appear as plain identifiers. The
file itself stays `.lomt` or `.lom`; the fence is a display choice, not a dialect.

## Writing a claim

The project's habit is to write claims in a form a criterion can check:

- name the command and the input;
- give the measured number, not "fast" or "small";
- say when it was measured and on which host;
- if it is a plan, say "planned" — a future tense read as present tense is a bug report waiting to
  happen.
