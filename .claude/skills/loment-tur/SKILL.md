---
name: loment-tur
description: Full Tur grammar system for Loment - the two-level editions (tur2026.1 / tur2026.2), the grammar declaration and factory lock, lexical rules, items, types, statements, expressions and precedence, patterns, entry points, hard limits, the seven foreign surface grammars (C, C++, Java, C#, Go, Python, natural), Rust as interface-only, and a readable grammar sketch. Read before writing Tur 2 features, choosing an edition, or deciding which surface spelling to use. Part of the LomentSkill suite; section numbers are global.
---

## 2.0 The Tur grammar system

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


This section is the reference for **what Tur is and what it accepts**, independent of any
library. The cheat sheet (section 2) is the one-page version; the trap list (section 6) is what
actually costs time.

### 2.0.1 Three layers, and the one rule that holds them together

| Layer | How a file is recognised | How it is read | Semantics |
|---|---|---|---|
| **Tur** (native) | no declaration (the **newest edition**), or `choose write grammar loment` / `tur` / `tur1` / `tur2` / `tur3` / `tur2026.1` / `tur2026.2` / `tur2026.3` | the native lexer and parser, **no translator** | exactly this guide |
| **Foreign surface grammars** | `.c` `.cpp` `.java` `.cs` `.go` `.py`, or an explicit `choose write grammar <name>` | translated into Tur **in process by the reference compiler**; the installed package refuses them | **identical** - only spelling and shape change |
| **Rust** | `.rs`, or `choose write grammar rust` | `from_rust`: **interface extraction only** (declarations), no body translation | a complete program is refused |

> **The governing rule (`docs/188` §0): a surface grammar changes spelling and shape, never
> semantics.** `print(x)` in the Python spelling and `out.println(x)` in the Java spelling are
> the same Loment operation. If a translator ever makes one spelling behave like its namesake
> language, that is a defect - CPython's floor division is the measured example: `//` in the
> Python spelling is Loment's division (truncation toward zero), not Python's.

**Rust is not Tur.** Since 2026-11-03 (`docs/295`) `rust` is a surface grammar like the others:
`from_rust` reads Rust and extracts the interface, so Tur's additions (`module`, `choose`,
`capability`, `comefor`, `print`, …) can never leak into it. That is what keeps "the Rust
surface is a strict subset" true - by construction, not by discipline. To write Tur with Rust's
spelling, declare `grammar tur`.

### 2.0.2 The declaration

<!-- no-compile: 只有声明行，单独不是单元（下面 2.0.3 的完整程序才是） -->
```rust
choose write grammar tur2026.2   // file head, before `module`, at most once per file
```

- **Position**: at the very top, **before `module`** - it decides how the rest is read. Written
  after `module` it is refused (the file has already been read by then).
- **Aliases resolve to a canonical name**, and only the canonical name enters the artifact. Two
  spellings of one grammar must not produce two different bytes.
- **The table is a factory lock** (`potato_from.GRAMMAR_ALIASES`): it is compiled in, cannot be
  extended or overridden, and has no user-side unlock. Four mistakes are reported: the name is
  not in the table / the declaration is after `module` / it is written twice / no name follows.
- **A misspelt keyword is not one of the four, but it is no longer silent.** The front end
  recognises the shape and reports it: `choose language python`, `choose grammar python`,
  `grammar python` and `choose write grammars python` all get "this line looks like
  `choose write grammar <name>` but the word order or spelling is wrong (got …)", with the
  correct form in the message. Older packages read the file as Tur instead and pointed at `def`
  or `class`; if you meet one, check this line first.
- **The line is stripped before parsing** by an equal-length rewrite, so line/column positions
  do not move; the profile flag travels with the unit.
- **`loment fmt` is not reliable on a file that carries a declaration.** The packaged (self-hosted)
  formatter's guard still lists only `loment` and `rust`, so it **refuses `tur` / `tur1` /
  `tur2`** along with every foreign grammar; the reference formatter accepts the native names.
  Since `--write` rewrites in place, format a Tur 2 file with the declaration line removed (and
  put it back), or use an external formatter. Write the full canonical name in new files
  regardless.

The complete value domain (`potato_from.GRAMMAR_ALIASES`, `NATIVE_GRAMMARS`):

| Source spelling | Canonical name | Layer |
|---|---|---|
| `loment` | `loment` | native (the historical name of Tur) |
| `tur`, `tur1`, `tur2026.1` | `tur2026.1` | native, **Tur 1** |
| `tur2`, `tur2026.2` | `tur2026.2` | native, **Tur 2** |
| `c` | `c` | foreign (`tools/ctrans.py`) |
| `cpp`, `c++`, `cxx`, `cc` | `cpp` | foreign (`tools/cpptrans.py`) |
| `java` | `java` | foreign (`tools/jtrans.py`) |
| `csharp`, `cs`, `c#` | `csharp` | foreign (`tools/cstrans.py`) |
| `go`, `golang` | `go` | foreign (`tools/gotrans.py`) |
| `python`, `py` | `python` | foreign (`tools/pytrans.py`) |
| `rust`, `rs` | `rust` | interface extraction only (`from_rust`) |
| `natural`, `nl`, `lument` | `natural` | foreign; the natural-language reading (`.nl`, `tools/nltrans.py`, `docs/197`, 19/19 criteria). A sentence per statement - `remember x as 3`, `give back`, `a Point has x as a whole number` |

### 2.0.3 Editions, and why old files never change meaning

Editions are **two-level**: `tur<year>.<n>` (`docs/296` §1.1).

| Declaration | Edition | Promise |
|---|---|---|
| none / `loment` | **newest edition** (`TUR_EDITIONS[-1]`; today `tur2026.3`) | 2026-11-06: new code gets the newest syntax by default (`docs/312`) |
| `tur` / `tur1` / `tur2026.1` | **Tur 1** (`tur2026.1`) | **permanently frozen** - no new syntax ever enters it, old files keep every byte of meaning |
| `tur2` / `tur2026.2` | **Tur 2** (`tur2026.2`) | first batch of absorbed Python/Zig/Go syntax; Tur 1 files compile unchanged. **Closed 2026-11-05** - the set in 2.0.4 is final |
| `tur3` / `tur2026.3` | **Tur 2026.3** (`tur2026.3`) | inherits 2026.2; **open** - new syntax lands here (first: `lgc`, `docs/309`) |
| future `tur2027.1` … | next edition | same rule, each edition frozen on release |

Rules that make "old syntax keeps compiling" a fact rather than a promise:

1. **Editions only add.** A canonical name never changes meaning; to change anything, open the
   next `.n` (or next year's `.1`).
2. **The edition is the file head's reading, not a `choose` after `module`** - same place as
   `choose write grammar`, once, before `module`.
3. **It is a native reading**: no translator, the same lexer/parser plus a profile flag, and the
   two implementations must agree byte for byte (`docs/158` §5).
4. **New words are reserved only in the new edition**, and even there only in **construct or
   operator position** - a name position may still use them. Tur 1 keeps `break`, `orelse`,
   `catch`, `print_num`, `defer`, `and`, `or`, `not`, `def`, `elif` as ordinary identifiers.
5. **Shorthands expire.** `tur1` / `tur2` are accepted only in the year that released them;
   afterwards the error names the full edition. A shorthand is **never reused** for a new
   family - otherwise a 2026 file would be reinterpreted in 2027.
6. **Only the canonical name enters the artifact**, so the same source produces the same bytes
   on any machine in any year. `tur2026` is an **archive label** for the year family, not a
   compilable name.
7. **Adding an edition means changing the factory lock**: `potato.GRAMMARS`,
   `GRAMMAR_ALIASES`, `NATIVE_GRAMMARS`, the self-hosted `strip_grammar_decl`, the VS Code
   `NATIVE_GRAMMARS`, and `surface_data` (`docs/296` §1.1 rule 5). It is an explicit act, not a
   branch added in passing.
8. **A new edition may not silently reinterpret an old file.** If a construct has to use a
   spelling that was a valid identifier in an earlier edition (`break`, `continue`, `defer`,
   `print_num`, `lgc`), register it in `potato_from.EDITION_COLLISIONS`. An undeclared file that
   **declares** one of those names is then refused with **E040** ("rename it, or pin the old
   reading") instead of quietly changing meaning. `loment_grammar_test` requires every open
   edition to appear in that table (`docs/312`).

**Tur 2026.2 is closed (2026-11-05).** The construct list in 2.0.4 is final: `def`, `elif`,
`and`, `or`, `not`, `for x in xs`, `break`, `continue`, bare `return;`, `orelse`, `catch`,
`print_num`, `defer`. New syntax goes to **`tur2026.3`** (2026) or **`tur2027.1`** (next year),
never into `tur2026.2`. The registry is `FROZEN_EDITIONS` / `TUR2026_2_FEATURES` in
`tools/potato_from.py`, and `loment_grammar_test::test_tur2026_2_is_frozen` pins the feature-set
hash and refuses an unopened edition (`docs/305`).

**The edition says itself out loud in three places** (`docs/296` §1.2):

1. **At compile time**: `check` / `build` / `run` / `ir` print one line on stderr -
   `loment: grammar tur2 -> tur2026.2 (Tur 2)`, or `grammar default -> tur2026.3 (Tur 2026.3)`.
   The **self-hosted driver** accepts `--no-edition-notice` for tools that run it as a
   subprocess (the reference launcher does not expose that switch yet).
2. **In the artifact**: the IR carries an externally visible global
   `@loment.tur.edition` holding `LOMENT-TUR:` + the canonical name. It is deliberately
   **not** a private constant - `clang -O2` drops unreferenced private constants, and this one
   exists for after-the-fact verification. `loment version <exe>` scans the artifact and reads
   it back.
3. **At run time**: the builtin `tur_edition() -> str` returns the canonical name, so logs and
   error messages can carry it.

### 2.0.4 Tur 2026.2, construct by construct

Everything here is **Tur 2 only**. Each entry says what it lowers to and what it refuses.

| Construct | Written as | Lowers to / means | Restrictions |
|---|---|---|---|
| **`def`** | `def f(a: u32) -> u32 { … }` | `fn f(…)` | pure sugar, identical code |
| **`elif`** | `elif cond { … }` | `else if cond { … }` | pure sugar |
| **`and` / `or` / `not`** | `a and b`, `a or b`, `not a` | `a && b`, `a \|\| b`, `!a` | pure sugar, same short-circuit |
| **`for x in xs`** | `for x in xs { … }` | index loop: `let i = 0; while i < len { let x = xs[i]; body; i = i + 1 }` | v1 accepts **only a named array or slice** (`let xs: [u32; 4]`, or a `[T]` parameter). `x` is a **copy**. Arbitrary source expressions and `mut` references are not accepted yet; `for i, x in xs` landed in Tur 2026.3 (§2.0.5) |
| **`break` / `continue`** | inside `while` / `for i in lo..hi` / `for x in xs` | jump to the innermost loop's exit / update step | outside a loop is E19. The emitter closes the L2 epoch opened in the body **before** jumping (`docs/210`) - jumping out without closing is a leak |
| **bare `return;`** | `fn f() { if bad { return; } … }` | early exit from a **unit** function (`fn f()`, no `-> T`) | only in Tur 2 **and** only when the function has no return type; `return;` in a `-> T` function is still E19 |
| **`orelse` / `catch`** | `let a: u32 = maybe orelse 0;` / `let b: u32 = risky() catch 0;` | switch on the two-variant enum; success takes the payload, failure takes the fallback | v1 is **only the right-hand side of `let` / assignment**, and the enum must have **exactly two variants with the first carrying the payload** (`Option { Some(T), None }`, `Result { Ok(T), Err(E) }`). The fallback is **lazy** (evaluated only on the failing branch). `catch \|e\| { … }` and `catch return` are Tur 3 |
| **`print_num`** | `print_num(n);` | decimal, optional `-`, **no newline**, one `write` | a **Tur 2 builtin**, any integer type, widened by sign to `i64`. Lives in the runtime block, so `no_runtime` refuses it **by name (E31)** - add `choose runtime`. In Tur 1 the name is an ordinary identifier, so a user-defined `fn print_num` is not shadowed |
| **`defer f(a, b);`** | `defer free(p);` | the **call** runs when the enclosing block exits: normal fall-through, `return`, `?`, `break` / `continue`; the arguments are evaluated where the `defer` is written | v1 accepts a **call only** (`defer 1;`, and `defer` outside a function body, are E19). LIFO within one block; **not** run on panic / trap / process exit. Self-hosted limits: **8 per function, 4 arguments, 64 nesting levels**, refused by name; the reference implementation has no caps. The Rust translation backend refuses `defer` (`docs/296` §2.3) |

Two properties worth stating because they are checked, not assumed:

- **The sugar is byte-identical.** The same program written with `def` / `and` / `elif` and with
  `fn` / `&&` / `else if` produces exactly the same IR in Tur 1 and Tur 2 (`loment_grammar_test`).
- **Tur 1 never sees a new word as a keyword.** A Tur 1 file that uses `break` as a variable
  name still compiles; the same file declared `tur2` and using `break;` in a loop body does
  loop control. Both directions have criteria (p8 M91/M94/M95).

### 2.0.5 Tur 2026.3: lifetimes (landed in 0.1.5)

The dimension is `choose lifetime_no | lifetime_auto | lifetime_manual`; `lifetime_auto` is
the default from `tur2026.3` on, and older editions keep `lifetime_no` so their artifacts do
not change. Design and the standing boundary list: `docs/299`.

- `&T` / `&mut T` are first-class **type** positions (struct fields, return types, nested,
  generic arguments), with an **implicit region parameter** - no `'a`, no `<'a>`, no `where`.
- Elision: one reference parameter → all reference outputs borrow from it; several reference
  parameters and one output → the output's region is the **shortest** of the inputs; methods
  borrow from `self`. Only a genuine fork needs a **source marker**: `fn pick(a: &[u32], b:
  &[u32]) -> &a [u32]` - and that marker exists only in `lifetime_manual`.
- `*r` dereferences one borrow; `r.field`, `*r = v` and `r[i]` auto-deref. A borrow is a `ptr`
  in the artifact; slice borrows normalise to the existing `[T]` / `mut [T]` views and can be
  struct fields (a `{ ptr, i64 }` view, e.g. `Cursor { data: &[u32] }`).
- Regions are static proofs; GC is run-time reclamation; they do not replace each other.
- `Box<&u32>` instantiates as `Box_ref__u32`. Composite generic arguments (`Box<&T>` /
  `Box<[T]>`) are refused **by name**: the self-hosted compiler's substitution table holds one
  token, and the reference has no naming rule for array/slice arguments (`docs/147` §2).

With `lifetime_no` (the default before 2026.3, and still selectable) `&` in a type position is
a parse error and struct fields hold scalars only - the pre-2026.3 shape, unchanged.

2026.3 also adds six spelling-level constructs (no lifetimes involved; all are refused by
the frozen editions):

- **struct literal field shorthand** `S { a, b: 2 }` - a bare name means `a: a`
  (`docs/332`);
- **`for i, x in xs`** - the index loop also binds the position: `i` is a `u32` copy
  (0, 1, 2, ...), `x` the element copy. Source is still a named array or slice; assigning
  to `i` or `x` does not write back (`docs/333`);
- **`for x in <call> { … }`** - the source may be a function call returning an array or
  slice; the call is evaluated once into a temporary and the loop walks that. Array
  literals and other expressions are not accepted yet (`docs/334`);
- **`if` as an expression in a `let` initialiser** - `let x: u32 = if c { 10 } else { 20 };`.
  One expression per arm, `else` required; `else if`, nesting, and other positions are not
  accepted in this round (`docs/335`).
- **`match` as an expression in a `let` initialiser** - `let x: u32 = match d { D::N => 1,
  _ => 0 };`. Subject must be a name, one expression per arm, arms comma-separated;
  blocks, literal patterns, and other positions are not accepted in this round
  (`docs/336`);
- **interpolated strings** `f"n={n}"` / `rf"raw
{n}"` - a hole is any expression of type
  `str`, `bool`, or an integer; `{{` / `}}` escape braces; `{expr=}` is the debug form and
  `!r` quotes and escapes a `str`. It is a **front-end rewrite** to `str_concat` /
  `str_show`, not a grammar production. Single line only; no format spec
  (`{x:>5}`) and no nested interpolation yet (`docs/338`).

### 2.0.6 Lexical grammar

| Element | Rule |
|---|---|
| Encoding | UTF-8 text; lines end in LF or CRLF |
| Comments | `//` to end of line; `/* … */` (unterminated is an error); `///` is a doc comment (`loment doc` extracts them) |
| Identifiers | `[A-Za-z_][A-Za-z0-9_]*` - ASCII only |
| Integer literals | decimal (`42`) or hexadecimal (`0x2A`, `0X2A`). **No** binary, octal, digit separators, or suffixes |
| String literals | `"…"`. Escapes are exactly **`\n \t \r \0 \\ \"`**. **Any other escape is an error** (E19), naming the accepted set - `"\x41"` and `"\q"` are refused, not silently degraded (`tools/lomc.py`, 2026-10-16 / #33, #36) |
| Booleans | `true` / `false` |
| Character / float literals | **do not exist** (there is no `char` and no float type) |
| Keywords | **there is no keyword table.** Words are recognised by **position**: `defer`, `and`, `def`, `break` are ordinary identifiers in Tur 1; Tur 2 reserves them only where a construct or operator can start |
| Punctuation | `{ } ( ) [ ] : ; = @ , . + - * / % < > ! & \| ^ ?` |
| Statement terminator | `;` after statements and after `const NAME: T = …;`; **none** after `module` or `use` (house style omits the accepted `use x;`), and none after a `struct` / `enum` / `fn` / `trait` / `impl` body |

### 2.0.7 Items (top level)

| Item | Form | Notes |
|---|---|---|
| Module | `module <name>` | first non-declaration line; a file without it is E19 ("expected module") |
| Grammar declaration | `choose write grammar <name>` | before `module`, section 2.0.2 |
| Project dimension | `choose <value>` | seven dimensions, section 2.1 |
| Switch definition | `set choose <name> { … }` | section 2.2 |
| Switch setting unit | `addin <name>` | entry unit only, section 2.2 |
| Import | `use <name>` / `use "<path>"` | layers, section 2; 300 lines max; several name-form imports allowed in 0.1.5 |
| Constant | `const NAME: <type> = <literal>` | the initialiser must be **one integer literal** (decimal or hex, optional `-`); expressions and string constants are parse errors - see 6.1.8 |
| Function | `fn f(a: u32, b: str) -> u32 { … }` | every parameter typed; no return type = unit; **≤ 10 parameters** |
| Generic function | `fn f<T>(a: T) -> T { … }` | monomorphised at compile time |
| Struct | `struct S { a: u32, b: bool }` | fields are **integer / `bool` scalars only** - no `str`, no `ptr`, no nested struct/array, no reference field (`docs/299` §1). A `str`/`ptr` field passes `check` but is refused **at build** when the struct is lowered ("field type … supports scalars only") |
| Enum | `enum E { A, B(u32) }` | a variant carries **at most one** payload, and the payload is an **integer / `bool` scalar** |
| Trait | `trait M { fn m(self) -> u32; }` | static dispatch only |
| Impl | `impl M for S { fn m(self) -> u32 { … } }` | method call `obj.m()` |
| External function | `extern fn c_add(a: i32, b: i32) -> i32;` | scalars and `ptr` only (E21), section 7.3.1 |
| Capability | `capability name : domain[lo..hi] revocable` | section 5 |
| Excluded space | `excluded "space"` | a module-level declaration; using that space in a capability or syscall site is E005 |
| Custom syntax | `comefor let "word" to { … }` … `byuse "word" done` | section 2.3 |
| Command language | `command c` / `foruse py` | declares an external-code-block language, section 2.0.12 |
| External code block | `let c { …raw C… }` / `py { …raw Python… }` | the body is **raw bytes**, not a string literal |
| Command registration | `pub fn loment_command() -> str` + `pub fn command_main(argv: ptr, argc: u32) -> u32` | section 7.2 |
| Visibility | `pub` on `fn` / `struct` / `const` / `enum` / `trait` | cross-module |

**Modules read top to bottom**: define before use. There are no forward references.

### 2.0.8 Types

| Type | Written as | Notes |
|---|---|---|
| Integers | `u8 u16 u32 u64 i8 i16 i32 i64` | **mixing widths in one operation is refused** - convert explicitly |
| Boolean | `bool` | conditions must be `bool` |
| Unit | `()` | a function with no `-> T` |
| Raw pointer | `ptr` | untyped, unchecked, `alloc` / `load8` / `store8` / `ptr_add` |
| String | `str` | a **value type** (pointer + length), not a borrowed `&str` |
| Array | `[T; N]` | fixed size, `N` a compile-time constant; **`T` must be an integer or `bool`** |
| Slice | `[T]` (read-only), `mut [T]` (mutable) | parameter types only; **`T` must be an integer or `bool`**; pass `&xs` / `&mut xs`; length via `slice_len` |
| Struct / enum | the declared name | by value; the backend's LLVM lowering requires scalar fields/payloads (above) |
| Generic parameter | `T` | monomorphised |
| Prelude enums | `Option<T>` (`Some` / `None`), `Result<T, E>` (`Ok` / `Err`) | `?` propagates both |
| Conversion | `x as T` | integer ↔ integer, integer ↔ `ptr`; always explicit |
| **Not present** | floats, `char`, `&T` in type position, function-pointer types, closures, `impl Trait`, tuples, `String` / `Vec` / `HashMap` | these are library types or Tur 2026.3 |

### 2.0.9 Statements

| Statement | Form | Notes |
|---|---|---|
| Binding | `let x: T = e;` | **the type is required**; no inference. In `grammar tur*`, `let mut x: T` is accepted and the `mut` is stripped; in the default `loment` spelling `mut` is an ordinary identifier (trap 6.1.11) |
| Assignment | `x = e;`, `s.f = e;`, `xs[i] = e;` | the target must be an lvalue (E14) |
| If | `if c { … } else if c { … } else { … }` | `c` must be `bool`; `&&` / `\|\|` short-circuit |
| If-let | `if let E::B(v) = x { … } else { … }` | one pattern; the else is optional |
| While | `while c { … }` | |
| Range for | `for i in lo..hi { … }` | `hi` **exclusive** |
| For-in | `for x in xs { … }` | **Tur 2 only**, section 2.0.4 |
| Match | `match x { E::A => { … } E::B(v) => { … } _ => { … } }` | arm bodies are **blocks**; exhaustive or `_` |
| Return | `return <expr>;` | the value is required, **except** bare `return;` in a unit function under Tur 2 |
| Break / continue | `break;` / `continue;` | **Tur 2 only**, innermost loop |
| Block | `{ … }` | **blocks do not scope `let`** today: a `let` inside an `if` branch is visible after it (measured; `docs/188` §0.1.1). Do not rely on block scope for correctness |
| Audit bypass | `lspe pass "reason" { … }` | section 5.1.5; the reason is mandatory (E038) |
| Expression statement | `f(x);` | a call for its effect |

### 2.0.10 Expressions and precedence

Primary expressions: integer / string / `true` / `false` literals, identifiers, `E::Variant`,
calls `f(a, b)`, method calls `obj.m(a)`, field access `e.f`, indexing `e[i]`, parenthesised
`(e)`, struct literals `S { a: 1 }` (2026.3 also accepts the shorthand `S { a }`, meaning
`S { a: a }`), array literals `[1, 2, 3]`, and `&e` / `&mut e` in **call-argument position**.

Postfix: call, field, index, `?` (propagation).

Unary: `-` and `!` only. There is **no `~`**; write `v ^ -1` (i32) or `v ^ 255` (u8).

Binary precedence, **loosest first** (higher number binds tighter):

| Level | Operators |
|---|---|
| 1 | `\|\|` |
| 2 | `&&` |
| 3 | `==` `!=` |
| 4 | `<` `<=` `>` `>=` |
| 5 | `\|` |
| 6 | `^` |
| 7 | `&` |
| 8 | `<<` `>>` |
| 9 | `+` `-` |
| 10 | `*` `/` `%` |

`as` binds tighter than any binary operator. **Both operands of a binary operator must have the
same width** - a mismatch is E001, not a silent widening. Arithmetic **wraps** (defined
behaviour, no `nsw`/`nuw`), and `x / 0` / `x % 0` abort the process.

### 2.0.11 Patterns

Only two pattern forms exist:

| Pattern | Form | Meaning |
|---|---|---|
| Enum variant | `E::V` / `E::V(name)` | matches that variant; a payload variant **must** bind a name, a payload-free variant **must not** |
| Wildcard | `_` | matches anything |

There are **no literal patterns, no range patterns, no struct patterns, no tuple patterns**.
`match` on a generic enum uses the **plain** enum name (`Result::Ok(v)`); the checker rewrites it to the monomorphised instance (trap 6.1.2).

### 2.0.12 Entry points, commands, and raw foreign blocks

| Entry | Form | Behaviour |
|---|---|---|
| Normal program | `fn main() { … }` | **no parameters, no return type**. The linker's entry stub calls it and exits 0 |
| Freestanding | `fn _start() { … }` | kernel / no-libc. Takes no arguments, wins over `main` when both exist. If you link it yourself with `-Wl,-e,_start`, it has **no return address** - end with `syscall4(60, …)`, never `return` |
| Registered command | `pub fn loment_command() -> str` + `pub fn command_main(argv: ptr, argc: u32) -> u32` | the build produces a `loment-<name>` program, so `loment <name> …` runs it (`docs/223`) |

**`main` with parameters is refused by both linkers.** The stub calls it with no arguments, so
a parameterised `main` would read register garbage - a silent wrong answer - so it is a hard
error (`docs/295` §2).

**Raw foreign blocks** (`docs/185`) carry another language's text **byte for byte**:

```rust
module myapp

command c                 // the main language of this file is C
foruse py                 // this file also uses Python

let c {                   // an external code block: the body is raw C bytes
int add(int a, int b) {
    return a + b;
}
}

py {                      // the `let` may be omitted when `py` was declared with `foruse`
def sub(a, b):
    return a - b
}
```

`command` and `foruse` are only language declarations; a block's body is not lexed as Tur, which
is why bytes that no string literal can carry (NUL, non-UTF-8) survive.

### 2.0.13 The grammar's own limits

| Limit | Value | On overflow |
|---|---|---|
| Parameters per function | 10 | E3 for a count mismatch; **more than 10 arguments at a call site** goes through `panic` - **exit 132, no diagnostic** |
| `use` lines per file | 300 | E20 |
| Switches per file | 500 | E22 |
| Nesting | 64 levels (blocks and brackets share the counter) | a diagnostic (E023) on both implementations |
| Bump heap | 64 KiB (sealed) / 1 MiB (`hosted`) | SIGILL, exit 132, nothing printed |
| `defer` captures (self-hosted) | 8 per function, 4 arguments each, 64 nesting levels | named error (`too many defer statements…`) |
| Root table (`gc_auto`) | 1024 slots | refused by name, never silently written past |
| Unit size | fixed capacity | E33 |

### 2.0.14 Foreign surface grammars: what each one accepts

They are **not** separate languages. The translator maps spelling onto the same Tur semantics,
so the same restrictions apply. What differs is how much of the foreign syntax is mapped.

| Spelling | Typical source | Known boundaries |
|---|---|---|
| C | `.c`, `choose write grammar c` | **no arrays, no pointers** ("unsupported pointer/array declaration"; array literals hit "unsupported bare block"). Scalars only. `int` keeps its width; mixing widths fails hard in the checker |
| C++ | `.cpp` | classes/objects map only as far as the translator's table goes; no templates, no references |
| Java | `.java` | same no-array/no-pointer boundary; `out.println` maps to `print`; no objects with runtime dispatch |
| C# | `.cs`, `choose write grammar csharp` | the same scalar subset |
| Go | `.go` | the same scalar subset; goroutines/channels are not mapped |
| Python | `.py` | `int` is **`i64`, bounded and wrapping**, not CPython's arbitrary precision; `//` truncates toward zero (Loment semantics); lists/dicts are not mapped |
| Rust | `.rs`, `choose write grammar rust` | **interface extraction only** - declarations, not bodies. A complete program is refused. Write `grammar tur` for a full program |
| Natural | `.nl`, `choose write grammar natural` | an invented sentence-shaped grammar (`tools/nltrans.py`, `docs/197`): `remember LIMIT as 3`, `a Point has x as a whole number`, `give back`. It translates to the same Tur semantics; it is not English prose |

The **two toolchains accept different sets**: the reference compiler (`tools/lomentc.py`, used by
`loment ir/build` from a checkout) translates foreign spellings **in process**; an installed
package (the self-hosted toolchain) **accepts native spellings only** and refuses anything else
(`docs/189` §4.1). So "write `.py` inside the package and it runs" is not true today.

### 2.0.15 A readable grammar sketch

This is a **sketch**, not a machine-readable grammar: it is written to be read, and the parser
(`tools/lomentc.py`, mirrored by `loment/selfhost/*.lomt`) is authoritative. `block` means
`{ statement* }`; `(x),*` means comma-separated; `?` means optional.

```
program     := grammar_decl? "module" IDENT item*
grammar_decl:= "choose" "write" "grammar" IDENT

item        := "use" (IDENT | STRING)
             | "const" IDENT ":" type "=" const_lit ";"
             | "fn" IDENT generics? "(" params? ")" ("->" type)? block
             | "struct" IDENT "{" (IDENT ":" type),* "}"
             | "enum" IDENT "{" variant,* "}"
             | "trait" IDENT "{" fn_sig* "}"
             | "impl" IDENT "for" IDENT block
             | "extern" "fn" IDENT "(" params? ")" ("->" type)? ";"
             | "capability" IDENT ":" IDENT "[" INT ".." INT "]" "revocable"?
             | "excluded" STRING
             | "set" "choose" IDENT block
             | "addin" IDENT
             | "choose" core_value
             | "comefor" "let" STRING "to" block
             | "byuse" STRING "done"
             | "command" IDENT | "foruse" IDENT
             | "let"? IDENT "{" raw_bytes "}"

const_lit   := "-"? INT
params      := param ("," param)*
param       := "mut"? IDENT ":" type
generics    := "<" IDENT ">"
variant     := IDENT ("(" type ")")?
fn_sig      := "fn" IDENT "(" params? ")" ("->" type)? ";"

statement   := "let" "mut"? IDENT ":" type "=" expr ";"
             | lvalue "=" expr ";"
             | "if" expr block ("else" "if" expr block)* ("else" block)?
             | "if" "let" pattern "=" expr block ("else" block)?
             | "while" expr block
             | "for" IDENT "in" expr ".." expr block
             | "for" IDENT "in" IDENT block                  // Tur 2
             | "match" expr "{" arm* "}"
             | "return" expr? ";"
             | "break" ";" | "continue" ";"                  // Tur 2
             | "lspe" "pass" STRING block
             | "defer" expr ";"                               // Tur 2; the expression must be a call
             | block
             | expr ";"

arm         := (pattern | "_") "=>" block
pattern     := IDENT "::" IDENT ("(" IDENT ")")?

expr        := unary (binop unary)*
unary       := ("-" | "!") unary | postfix
postfix     := primary ("(" args? ")" | "." IDENT | "[" expr "]" | "?")*
primary     := INT | STRING | "true" | "false"
             | IDENT | IDENT "::" IDENT ("(" expr ")")?
             | "(" expr ")"
             | IDENT "{" (IDENT (":" expr)?),* "}"         // bare name = 2026.3+ shorthand
             | "[" (expr),* "]" | "&" ("mut")? expr
cast        := expr "as" type                              // binds tighter than any binop
fallback    := expr ("orelse" | "catch") expr              // Tur 2, let/assignment RHS

binop       := "||" | "&&" | "==" | "!=" | "<" | "<=" | ">" | ">="
             | "|" | "^" | "&" | "<<" | ">>" | "+" | "-" | "*" | "/" | "%"

type        := INT_TYPE | "bool" | "ptr" | "str" | "()"
             | IDENT | "[" type ";" INT "]" | "[" type "]"
             | "mut" "[" type "]" | IDENT "<" (type),* ">"
```

Three things the sketch makes visible because they surprise people: **there is no float or char
type**, **`&` is an expression only in argument position**, and **a `match` arm is a block, not
an expression** (the `=> 0,` form is not in the grammar).

