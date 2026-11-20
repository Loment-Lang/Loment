# Working agreements

These are the rules that the source cannot show you. `CLAUDE.md` and `AGENTS.md` at the repository
root are authoritative; this chapter is the explanation.

## Branch from `origin/main`, not from your local `main`

A local `main` is routinely dozens of commits behind. A branch cut from it looks like it introduces
failures that `main` fixed weeks ago. Fetch, then branch from `origin/main`.

## `lom/` is a contract, not an implementation

`lom/*.lom` is the L0 interface layer that the kernel side depends on. It has a frozen threshold:

- a change there is a **contract change** and gets its own commit, with a note saying who depends
  on it and what downstream must do;
- adding or removing a primitive is a version bump, and downstream assertions about the primitive
  count are expected to fail — that is the signal, not something to relax;
- do not change `lom/` while refactoring the compiler.

## `kernel/` and `sdk/` are vendored read-only copies

They are the counterpart of the L0 interface, copied from the FujoOS repository at the same paths.
Changing them here changes nothing upstream, and criteria will go green against a copy that only
exists on your disk. Change them in FujoOS and re-copy.

## Two implementations, one commit

The language surface exists twice: `tools/lomentc.py` and `loment/selfhost/*.lomt`. They must agree
byte for byte on diagnostics and IR. A language change modifies both, in the same pull request.

The generated seed (`loment/build/selfhost_driver.ll`) goes in its **own** commit, marked as
generated. Never merge it by hand; regenerate it with `python tools/loment_seed.py --emit`.

## Issues and pull requests are written in English

Titles and bodies in the tracker are English. Commit messages in this repository are Chinese. Both
are conventions, not preferences of the moment.

## Show Loment source in a `rust` fence

`.lomt` and `.lom` are not known to any Markdown highlighter (GitHub, VS Code, the terminal). Use a
`rust` fence when displaying them: the overlapping syntax colours correctly, and only `capability`,
`guard`, `excluded` and `revocable` show as plain identifiers. The file extension stays `.lomt`.

## Documentation numbers are taken, not chosen

`docs/NNN-slug.md` numbers are unique and monotonic. Get the next one from
`python tools/loment_docs.py --next`; if a collision appears, the tool can renumber. English twins
are `docs/NNN-slug.en.md` and keep the number and slug unchanged.

## Criteria are red first

A criterion written after the fix, against a tree that already passes, proves nothing. Write it
against the current tree, watch it fail for the reason you expect, then fix. If it is green on the
first run, the assertion is not reaching the behaviour.

## Fixtures come from the real thing

A hand-written fixture is cleaner than reality and tests an input that never occurs. Take the input
from the real compiler, linker, package or release, and compare bytes rather than shapes.

## Temporary files stay out of globbed directories

`pathlib.Path.glob` matches dotfiles, and several criteria glob a directory for inputs. A temporary
file left in one of those directories can turn a passing suite red — or worse, be read as a real
input. Write to a `tempfile.TemporaryDirectory`.

## Merge shape

- A pull request that is only handwritten changes is squash-merged.
- A pull request that carries generated artifacts alongside source is merge-committed, so the
  generated commit stays identifiable.
