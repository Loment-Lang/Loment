# Editions: Tur 2026.1 to 2026.3

Loment's grammar is called **Tur**. Tur is not a single frozen syntax: it has numbered editions,
and a file states which edition it is written in. The declaration sits before `module`, and a file
without one is read as the newest edition.

This chapter explains the edition system, how to pin an older reading, and what each edition
adds. It is the reference for every "Tur 2" and "Tur 2026.3" note in later chapters.

## The declaration

```rust
choose write grammar tur2026.2

module myapp
```

Rules:

- The declaration comes **before `module`**.
- A file carries **at most one**.
- A missing declaration means **the newest edition**, which today is `tur2026.3`.
- The compiler prints the resolved edition on standard error at every `check`, `build`, `run`
  and `ir`.

The canonical names are two-level: `tur<year>.<n>`. For 2026 there are three:

| Canonical | Shorthand | Status | What it is |
|---|---|---|---|
| `tur2026.1` | `tur1` | frozen | the base language |
| `tur2026.2` | `tur2` | closed | the first batch of absorbed syntax |
| `tur2026.3` | `tur3` | open | inherits 2026.2; new syntax lands here |

The shorthand names are accepted during 2026. The canonical name never changes meaning.

## Why editions exist

A grammar that grows creates a compatibility problem: a word that was an ordinary identifier in
one edition can become syntax in the next. A file that used `break` as a variable name would
silently change meaning when `break` became a loop statement.

Tur solves this with two rules:

1. **An older edition never learns a new word as a keyword.** A Tur 2026.1 file still treats
   `break`, `defer`, `print_num` and `lgc` as ordinary identifiers.
2. **An undeclared file that declares one of those words is refused with E040.** The compiler
   does not guess; it asks you to rename the identifier or to pin the older reading.

```rust
// undeclared -> tur2026.3; declaring `break` here is E040
let break: u32 = 1;
```

To keep a file that uses those words as identifiers, state its edition:

```rust
choose write grammar tur2026.1

module oldcode

const break: u32 = 1;     // fine: 2026.1 has no loop statement named break
```

## What each edition adds

### Tur 2026.1

The base language: modules, `choose`, functions, structs, enums, `match`, generics, traits,
capability domains, pointers, arrays and slices, `extern fn`, and the system-call builtins.

### Tur 2026.2

The second edition absorbs syntax that makes daily programs shorter:

| Construct | Written as | Meaning |
|---|---|---|
| `def` | `def f(a: u32) -> u32 { … }` | the same declaration as `fn` |
| `elif` | `elif cond { … }` | `else if cond { … }` |
| `and` / `or` / `not` | `a and b`, `a or b`, `not a` | `&&`, `\|\|`, `!`, with the same short-circuit |
| `for x in xs` | `for x in xs { … }` | iterate a named array or slice |
| `break` / `continue` | inside a loop | leave the innermost loop / skip to its update |
| bare `return;` | in a unit function | early exit from `fn f()` |
| `orelse` / `catch` | `let a: u32 = maybe orelse 0;` | take the payload, or a lazy fallback |
| `print_num` | `print_num(n);` | decimal output without a newline |
| `defer` | `defer free(p);` | run a call when the enclosing block exits |

Every sugar item compiles to the same IR as its older spelling. A file that uses `def` and
`and` produces byte-identical code to one that uses `fn` and `&&`.

### Tur 2026.3

The newest edition inherits all of the above and adds **`lgc`**, manual control over when the
collector may start:

```rust
lgc end { … }           // do not collect inside this block
lgc start(2ms) { … }    // collect inside this block, with a cooperative time budget
```

`choose gc` remains the tier choice; `lgc` controls that tier's timing. Chapter 22 covers the
semantics, including the meaning of the budget and the cases where it cannot be enforced.

## Reading the edition at run time

The builtin `tur_edition()` returns the canonical name:

```rust,run
module edition

choose runtime

fn main() {
    print(tur_edition());
    print("\n");
}
```
<!-- expect: tur2026.3 -->

The same value is stored in the artifact as `@loment.tur.edition`, and `loment version <exe>`
reads it back. That makes "which grammar produced this binary" answerable without the source.

## Foreign surface grammars

Tur is the native grammar. The compiler also accepts six foreign spellings — C, C++, Java, C#,
Go and Python — plus a natural-language grammar and a Rust interface grammar. They are selected
by file extension (`.c`, `.py`, …) or by an explicit declaration:

```rust
choose write grammar python

module myapp
```

These spellings are translated into Tur semantics; they are not separate languages. Chapter 34
lists what each accepts. Two boundaries matter here:

- The **reference compiler in a checkout** translates them in process.
- An **installed package** accepts native Tur spellings only.

Rust is interface-only: `choose write grammar rust` extracts declarations, not bodies. Write
`choose write grammar tur` when you want Rust-like spelling with a full program.

## Choosing an edition for a project

- **New code:** leave the declaration out. The newest edition is the intended default.
- **A file that must keep its exact meaning:** pin its edition. This is what old examples and
  generated corpora do.
- **A library:** pinning is not required, but it is the safe choice if the library is shared with
  projects that use older editions. The edition is a property of the source file, so a library
  file and its consumer can differ.
- **A migration:** run the file undeclared first. If it compiles, it was already compatible. If
  it reports E040, the fix is either a rename or a declaration, and the message says which
  identifiers collided.

The next chapters move from the shape of a program to the values it manipulates.
