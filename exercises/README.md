# Lomentlings

Small programs to fix, in order. Each directory holds:

- `main.lomt` — the program you edit;
- `expect.txt` — the stdout the fixed program must produce, byte for byte;
- `solution.lomt` — one answer (the runner does not read it unless you ask).

Run the runner from the repository root:

```console
$ python tools/lomentlings.py            # run them all
$ python tools/lomentlings.py 03         # only 03
$ python tools/lomentlings.py --solution 03   # print the answer
$ python tools/lomentlings.py --apply 03      # copy the answer into main.lomt
$ python tools/lomentlings.py --check-solutions   # every answer must pass (the gate runs this)
```

The runner compiles `main.lomt` with the repository's reference toolchain, runs it, and compares
stdout with `expect.txt`. A compile error is reported with its message and line; a wrong answer
with both byte strings.

## The exercises

| # | Lesson |
|---|---|
| 01 | the program prints a line; get the bytes exactly right |
| 02 | `let` always needs a type |
| 03 | a non-`()` function must return on every path |
| 04 | `while` bounds: `<=` versus `<` |
| 05 | an array literal must match its declared length |
| 06 | field assignment is not an lvalue — build a new struct literal |
| 07 | `match` must be exhaustive |
| 08 | `?` only exists in a function that returns `Result` |
| 09 | writing through a slice needs `mut [T]` and `&mut` |
| 10 | a `guard` index must be inside the capability's range |

Each `main.lomt` starts broken on purpose; the first line the runner prints is what to fix.
