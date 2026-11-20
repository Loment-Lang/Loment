# Where to help

The project has more work than people. This chapter lists the places where a contribution is
useful and the shape it should take.

## Report a defect

The highest-value contribution, if the report is good. Include:

- the smallest unit that shows the problem;
- the exact command;
- `loment version` (and which toolchain — installed package or repository build);
- what happened, and what you expected instead;
- whether it is silent (wrong output, exit 0) or loud (a diagnostic, a crash).

Silent wrong answers are the most valuable: they are the class the project's own history shows
matters most.

## Write a criterion

Many behaviours are known but not measured. A criterion that pins one down is a complete
contribution on its own, and it does not require changing the compiler. Follow
[Adding a criterion](05-adding-a-criterion.md); make it red first.

## Help with the grammars

The six translated surface grammars (C, C++, Java, C#, Go, Python) each have their own semantics
and their own defect history. A real program in the source language, compared against the source
language's own result, is the best test.

## Help with the docs and the books

- `docs/` has an English twin convention and a glossary under `docs/i18n/`.
- The usage book is English; translations of the two books are welcome, as long as they keep the
  `rust,run` blocks byte-identical so the gate can still compile them.
- A dead link or a stale command in a book is a defect: the site criterion checks links, and the
  book criterion compiles the programs.

## Help with packaging

`lompi`, the installer, the VS Code extension and the release workflow all have narrower,
well-defined surfaces. They are good first areas because the result is easy to check: install it
on a clean machine and see whether it works.

## Pick a track

The roadmap chapter lists the four tracks and the two infrastructure lines. Within a track, issues
usually carry a severity label; start with the smaller ones and leave the ones that say "needs a
design" alone until the design lands.

## How to behave in the tracker

- Search before filing.
- One issue per defect; do not bundle unrelated failures.
- Titles and bodies in English.
- If someone else's report helped you, say so; if you disagree, say what you measured.
- Credit external contributors in review before restating the technical point.

## What not to do

- Do not delete a criterion to make the gate green.
- Do not relax a floor or add a skip without an explanation.
- Do not hand-edit generated artifacts.
- Do not change `lom/` while doing something else.
- Do not copy a vendored `kernel/` or `sdk/` file into shape here: change it upstream.
