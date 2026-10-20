# Introduction

Loment is a systems programming language. You write Rust-flavoured source; it becomes a native
x86-64 executable with no runtime and no libc.

It started small enough that a cheat sheet was the whole manual. That is no longer true. There are
five project dimensions (`choose`), three garbage-collection tiers, capability domains, a package
manager with content-addressed identities, six additional surface grammars, and a self-hosting
compiler. This book is the long way in: one concept at a time, with a program that does something
real.

## Who this is for

You have written code before — in any language. You do **not** need to know Rust. Loment looks
Rust-flavoured, but the accents are different, and the book points them out as they appear:

- `let` always needs a type. There is no inference.
- The entry point of a freestanding program is `_start`, not `main`, and it must not `return`.
- There is no `printf`. Output is a system call.
- Arm bodies in `match` are blocks, not expressions.
- A unit can declare **capability domains** — what it is allowed to reach — and `guard` checks
  them at run time.

## The three tracks

- **This book** — read it in order. It has three parts: getting started, the language, and what
  happens under the hood.
- **[Loment by Example](../by-example/README.md)** — one concept per file, annotated. Use it as a
  reference for shapes.
- **[Lomentlings](../../exercises/README.md)** — broken programs to fix, with a runner that tells
  you when you are done.

Every program marked with a **Run** block in this book is compiled and executed by the gate
(`tools/loment_book_test.py`). If a chapter stops working, the build goes red. You can trust that
the code you read here actually runs.

## How to read a code block

Blocks that are complete programs are marked like this:

```rust,run
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: hello from Loment -->

Everything else is a fragment: it shows a shape, not a runnable file. Fragments are not executed.
