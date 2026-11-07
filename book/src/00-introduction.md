# Introduction

Loment is a systems programming language. You write source in the **Tur** grammar; it becomes a
native x86-64 executable for Linux (ELF) or Windows (PE), with no interpreter and no required
C library.

It started small enough that a cheat sheet was the whole manual. That is no longer true. The
language now has eight integer types and a pointer type, arrays and slices, structs, enums,
pattern matching, generics, traits, modules and packages, capability domains, three
garbage-collection tiers, a memory-safety ladder, an audit ledger, project dimensions, custom
syntax macros, foreign surface grammars, and a self-hosting compiler. This book is the long way
in: one concept at a time, each with a program you can run.

## What this book assumes

You have written code before, in any language. You do not need to know Rust. Loment's surface
syntax borrows from Rust, and the places where it differs are pointed out as they appear. The
short list of differences that surprise people first:

- `let` always carries a type. There is no type inference.
- The normal entry point is `fn main()`; a freestanding entry is `fn _start()`, which must not
  `return`.
- There is no `printf`. `print` writes a string and `print_num` writes an integer.
- A `match` arm is a block; an expression in that position is a syntax error.
- There are no floats, no `char`, and no tuples in the language. The standard library provides
  `f64` as a module over bit patterns.
- A unit can declare **capability domains** — what it may reach — and `guard` checks a position
  against the declaration at run time.

## Tur editions

Tur is the grammar family. It has numbered editions, and a file chooses one with
`choose write grammar <name>` before `module`. A file with no declaration is read as the
**newest edition**, which today is `tur2026.3`:

| Edition | Status | What it adds |
|---|---|---|
| `tur2026.1` (shorthand `tur1`) | frozen | the base language |
| `tur2026.2` (`tur2`) | closed | `def`, `and` / `or` / `not`, `elif`, `for x in xs`, `break` / `continue`, bare `return;`, `orelse` / `catch`, `print_num`, `defer` |
| `tur2026.3` (`tur3`) | open | everything above, plus `lgc` for controlling when the collector may run |

The canonical names are `tur2026.1`, `tur2026.2` and `tur2026.3`. The short names are accepted
during 2026. Chapter 5 covers editions in full, including how to pin an older reading.

This book writes its examples in the default edition, **Tur 2026.3**, and uses the newer spelling
where it makes a program clearer: `def` for definitions, `and` / `or` / `not` for boolean
operators, `elif` for chained conditions, and `for x in xs` where an index adds nothing. Every
construct also has its older spelling, and the chapters show both when the distinction matters.

## The book's structure

The book is arranged in eight parts. Read them in order the first time.

| Part | Chapters | What it covers |
|---|---|---|
| I — Getting started | 1–5 | install, first programs, a full tour, editions |
| II — Values, expressions and functions | 6–8 | types, operators, conversion, functions |
| III — Control flow and data | 9–15 | control flow, structs, enums, errors, arrays, text, pointers |
| IV — Abstraction and organisation | 16–19 | generics, traits, modules, tests |
| V — Tur 2026.3 | 20–22 | the Tur 2 surface, `defer`, `print_num`, `lgc` |
| VI — Capability domains | 23–26 | general, temporal and context domains |
| VII — The artifact | 27–31 | GC tiers, LMS, project dimensions, switches, LSPE |
| VIII — Interfaces | 32–35 | commands, FFI, threads, networking |

Two practice tracks sit beside the book:

- **[Loment by Example](../by-example/README.md)** — one concept per file, annotated, short.
- **[Lomentlings](../../exercises/README.md)** — broken programs to fix, with a runner.

## How to read a code block

A block that is a complete program is marked like this:

```rust,run
module hello

fn main() {
    print("hello from Loment\n");
}
```
<!-- expect: hello from Loment -->

The marker `rust,run` means the gate extracts the block, compiles it with the repository
toolchain, runs it, and compares its output with the `expect` comment. A chapter whose program
stops working turns the build red, so the code in this book is executed code.

Everything else is a fragment. It shows a shape — a signature, a declaration, a piece of a
program — and is not run on its own. Fragments are useful for reading; the runnable blocks are
useful for learning by changing them.

## A note on honesty

Loment is under active development. Some features are complete, some have narrow boundaries, and
some are designed but not implemented. Each chapter marks the difference where it matters: a
runnable block is real, and prose that describes a boundary names the boundary. When a chapter
says "today", it means the version this book ships with.
