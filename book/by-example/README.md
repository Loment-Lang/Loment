# Loment by Example

One concept per file. Every program here is compiled and run by the gate
(`tools/loment_book_test.py`), so the shapes are real.

Files import [`helpers.lomt`](helpers.lomt) for printing. That file is the `write_dec` helper the
book writes out in full in chapter 8 — here it is a library so the examples stay small.

The examples are written for the default grammar, **Tur 2026.3**. Files 15 and 16 show the syntax
that editions 2026.2 and 2026.3 add; the earlier files stick to the base language.

| File | Concept |
|---|---|
| [01-hello.md](01-hello.md) | the smallest program; `_start`; a syscall |
| [02-values.md](02-values.md) | types, literals, conversions, arithmetic |
| [03-functions.md](03-functions.md) | parameters, returns, `()` functions |
| [04-control-flow.md](04-control-flow.md) | `if`, `while`, `for` |
| [05-structs.md](05-structs.md) | fields, literals, values |
| [06-enums-match.md](06-enums-match.md) | variants, payloads, exhaustive `match` |
| [07-result.md](07-result.md) | `Result`, `?`, turning errors into values |
| [08-arrays-slices.md](08-arrays-slices.md) | `[T; N]`, `[T]`, `&` and `&mut` |
| [09-strings.md](09-strings.md) | `str`, `str_eq`, `str_concat`, byte access |
| [10-pointers.md](10-pointers.md) | `alloc`, `load8`/`store8`, `ptr_add` |
| [11-capabilities.md](11-capabilities.md) | `capability`, `guard`, `excluded` |
| [12-generics.md](12-generics.md) | monomorphised type parameters |
| [13-traits.md](13-traits.md) | `trait`, `impl … for …`, static dispatch |
| [14-modules.md](14-modules.md) | `use`, the standard library, one module at a time |
| [15-tur2.md](15-tur2.md) | `def`, `and`/`not`, `elif`, `for x in xs`, `break`/`continue` |
| [16-lgc.md](16-lgc.md) | `lgc end` / `lgc start(budget)` collection windows |
