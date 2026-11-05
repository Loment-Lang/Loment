---
name: loment
description: Read this before writing a program in Loment - FujoOS's own systems language. Its own grammar is **Tur** (Tur 1 = `tur2026.1`, Tur 2 = `tur2026.2`, editions frozen by year, see section 2.0) plus capability domains; seven foreign surface grammars - C, C++, Java, C#, Go, Python and the sentence-shaped `natural` - share its semantics and only change spelling (docs/188), and `rust` is interface-extraction only. With the toolchain installed you can write, check and compile native executables with no Python runtime. Use it for: writing a Loment program or a .lomt file; **writing a Loment library** (a library is a directory, dependencies are the `use` lines in the source, exports are `pub`, optional `pkg.lomp` manifest, see section 10); managing or debugging dependencies; compiling or running with the `loment` command; reading the E001-E038 error codes; looking up a builtin or a piece of syntax (`loment builtins` / `loment syntax` / `loment cheat`); expressing a piece of logic in the project's own language; how to write struct / enum / match / capability / guard; the full Tur grammar system (editions, declarations, items, statements, expressions, patterns); **the safety stack** (LGC reclamation, LMS memory safety, LSPE audit ledger), the LVM execution tiers, the Potato artifact object and hot update (section 8); **giving a project its own source extension** (`source_ext` in `loment.conf`, section 7.1); **registering a custom `loment` subcommand** (`loment foo` runs `loment-foo` from PATH, section 7.2); and what differs when migrating from another language. **For anything about libraries and dependencies read the lompi guide first** (`~/.claude/skills/lompi/SKILL.md`, or `<prefix>/share/lompi/skill/SKILL.md` when you only have the package) - installing a library, resolving dependencies, which libraries exist on this machine, and what a `use <name>` resolves to all live there. Also use this whenever the task is to author, read or debug Loment source (.lomt / .lom / .lomp) or a Loment library with the toolchain installed.
---

# Loment: the language you write programs in

**Loment is FujoOS's own systems language.** Its **surface syntax is Rust-flavoured** (`module` /
`use` / `fn` / `let` / `if` / `while` / `for` / `match` / `enum` / `struct` / `trait` / the type
names / the operators all match Rust) and it adds exactly one layer of semantics: **capability
domains** (`capability` + `guard`). It compiles straight to a **native x86-64 executable**
(Linux ELF / Windows PE) with **no Python runtime**.

**The language's own grammar is called Tur** (`docs/188`, `docs/295`). It is **derived directly
from Rust** (types, expressions, `fn`, `let`, control flow keep Rust's spelling) and **adds what
Rust does not have**: `module`, `choose`, `capability`, `guard`, `extern fn`, `comefor`,
`command`, and the `print` builtin. Everything in this guide is Tur. **Section 2.0 is the full
grammar system** - editions, the declaration, every item/statement/expression/pattern, and the
foreign surface grammars.

A file is read as **Tur 1** when it has **no declaration** (the default) or says
`choose write grammar loment` / `tur` / `tur1` / `tur2026.1`. Editions are **two-level**:
`tur<year>.<n>` (`docs/296`). The canonical name never changes meaning; `tur1` / `tur2` are the
2026 shorthands for `tur2026.1` / `tur2026.2` and are only accepted in their release year;
write the full name in new files (`loment fmt` does not take files with a grammar declaration
yet). **Every `check` / `build` / `run` / `ir` prints the resolved edition on stderr** (e.g.
`grammar tur2 -> tur2026.2 (Tur 2)`); `tur_edition()` returns it at runtime, and
`loment version <exe>` reads it back from the artifact.

**Tur 2026.2** adds `for x in xs`, `break` / `continue`, bare `return;` in unit functions,
`orelse` / `catch`, `print_num`, and the sugar `def` / `elif` / `and` / `or` / `not`. They are
**Tur 2 only**: Tur 1 keeps those words as ordinary identifiers, so old files keep their
meaning. `defer` is designed but **not in this edition**. The details and every restriction are
in 2.0.3.

The same program can also be written with C / C++ / Java / C# / Go / Python spelling, **changing
the spelling and nothing else** - recognised **by extension** (`.c` / `.cpp` / `.java` / `.cs` /
`.py` / `.go`), or with an explicit declaration at the top of the file:

```rust
choose write grammar python     // must come before `module`; once per file
```

**Rust is not Tur.** `.rs` files and `choose write grammar rust` are a *surface grammar* like the
others: `from_rust` reads them as Rust and extracts the interface (declarations), not a
translated body - a complete `choose write grammar rust` program is refused. That is what keeps
"the Rust surface is a strict subset" true: it is literally Rust, and Tur's additions never leak
into it. To write Tur with Rust's spelling, declare `grammar tur`.

> **The two toolchains accept different sets today - read this before you start.**
> **The reference compiler in the source repository** (`tools/lomentc.py`, which is also what
> `loment ir` / `build` use when you run them from a checkout) **accepts** both spellings: it
> translates the file into Loment **in process** and compiles that, with no external compiler
> involved. **An installed package (the self-hosted toolchain) accepts the `loment` spelling
> only**: anything else is refused outright ("这份源的 `choose write grammar` 自举侧收不了…" /
> *the self-hosted side cannot take this file's `choose write grammar`*), because the six
> translators have no Loment twin yet (`docs/189` section 4.1). So "write `.py` inside the
> package and it runs" **is not true today**.

The alias table is **frozen at the factory**: the native grammar (`loment`, `tur`, `tur1`,
`tur2`, `tur2026.1`, `tur2026.2`) plus the foreign grammars (`c`, `cpp`, `java`, `csharp`, `go`,
`python`, `rust`, `natural`). It cannot be extended or overridden (`docs/188` section 1.1).
Getting it wrong is one of four errors: the name is not in the table / it is written after
`module` / it is written twice / no name follows it.

**Boundaries to know before writing in one of those spellings** (measured; each one saves a
round trip):

- **No arrays and no pointers** (C and Java both refuse: "unsupported pointer/array declaration";
  array literals hit "unsupported bare block"). Use scalars.
- **One integer width only**: mixing them **fails hard** in the checker (`docs/188` section 7.1.1).
- **No bitwise `~`** (Loment's unary operators are `-` and `!` only): write it **to a width** -
  `v ^ -1` for i32, `v ^ 255` for u8. The front end names the refusal and puts both ways out in
  the message.
- **In the Python spelling `int` is `i64`** (bounded, **wrapping** on overflow), not CPython's
  arbitrary precision. Nothing in the source says so - do not expect an error.

> **This file is self-contained**: syntax, builtins, error codes and **the traps that cost the
> most time** are all here. Do not go looking elsewhere first (the exception is when you have
> the source repository - that is the last section).

**Read the program in section 1 first, then come back to the tables.** It walks the whole
language in one file, and it **compiles and runs**; what is printed here is the same file as
`loment/examples/tour.lomt` in the repository (a copy ships in the package), so copying it is
safe.

## 0. Check which toolchain you have

```bash
loment version
```

You get something like `Loment 0.1.5 (0.1.5), commit <short>`. **The commit is what
counts**: different checkouts can do different things (this file describes the 0.1.5
generation; older packages print `0.1.4`, `0.1.4 Alpha` / `0.1.4 Alpha2.3` / `0.1.4 Pre2`).

**There are 38 commands; `loment help` lists all of them** (grouped, aligned, coloured) and
`loment help <command>` documents one. The ones used most:

| Command | What it does |
|---|---|
| `loment check FILE` | Check only, produce nothing (diagnostics on stderr - rendered by the reporter `lomenterr`: **position + source line + caret + what went wrong + why + how to fix (at least 3 ways) + supported/not supported**; when the file is not Loment at all it also offers the three ways to `translate it in`. See section 10) |
| `loment ir FILE` | Print LLVM IR to stdout |
| `loment build FILE [-o OUT]` | Compile to an executable |
| `loment run FILE` | Compile and run |
| `loment fmt FILE` | Format (prints the result) |
| `loment doc FILE` | Generate API docs |
| `loment lsp` | Language server (LSP over stdio) |
| `loment skill [--print]` | Print the path to **this guide** / its whole text - depends on no directory convention |

**With no copy of this guide to hand, the language reference is still one command away** (that is
what self-contained means here):

| Command | What it does |
|---|---|
| `loment cheat` | One page of the traps, ordered by how easy they are to hit |
| `loment syntax` | The syntax cheat sheet |
| `loment builtins` | The builtin table (all of them, there are no others) |
| `loment types` / `keywords` / `caps` | Type table / keywords / capability domains |
| `loment codes` / `loment explain E4` | The code table / one code in detail (the packaged banner still says "E1-E23"; the reporter knows E001–E038) |
| `loment new NAME` | Write a skeleton that `loment run` accepts immediately |
| `loment stat` / `fns` / `grep` / `hash` / `cat` | Read source (lines, signatures, search, sha256, numbered print) |
| `loment ls` / `tree` / `examples` / `example tour` | Directory and examples |
| `loment doctor` / `where` / `env` / `tools` | Diagnose the installation (which component is missing, where things live) |

`--no-color` (or `loment color off`) turns ANSI off - use it when the output goes into a pipe.
**It governs the diagnostics side too**: `loment check FILE --no-color` passes the switch to the
reporter, and afterwards not one escape byte is left in the output (`loment check f.lomt | less`
is meant to be used this way).

Two more diagnostic outlets are switches on `loment check FILE` (each also turns colour off):

- `--short` - **one line per diagnostic** (`file:line:col: error[code]: message`), for grep and
  CI logs. Note that the column is reported as `0` today;
- `--json` - **one object per line**, carrying `title`/`what`/`why`/`fixes`/`yes`/`no` as well as
  the code and position. When the reporter has an applicable fix, the object also carries a
  non-empty `suggestions` array an editor can **apply directly** (`replacement` with
  `byte_start`/`byte_end`). The key is **absent** when there is no suggestion - an empty array
  would read as "no fix available", which is a different claim.

Two more switches cap and capture the same stream:

- `--max N` - render at most `N` diagnostics (`0` = all) and say how many were withheld;
- `--diag-out PATH` - write the raw JSONL records to `PATH` (the file is created even when the
  unit is clean). That file is what the reporter reads, and it is where the `byte_start` /
  `byte_end` autofix fields live.

The reporter's own text is **English by default**; to have the whole report in Chinese, put an
`errconfig` in the **project root**:

```rust
// errconfig
module errconfig

pub fn error_lang() -> str {
    return "zh";
}
```

The value is `"zh"` (Chinese) or `"en"` (English, also the default), any case; anything else is
treated as **not configured** and falls back to the default. The `message:` line the compiler
contributes is not governed by it - that is the compiler's own text, a separate layer from the
reporter's prose.

> ### When the task touches "libraries / dependencies", **go read the lompi guide first**
>
> lompi is **the package manager for Loment libraries** (it owns the store, the lock file, and
> `deps/`). Every one of these questions is answered there, and **this guide does not cover
> them**:
>
> - installing or fetching a library, resolving dependencies, writing the lock file;
> - "which Loment libraries exist on this machine", "what is in this store", "where is the source
>   of a given library";
> - what `use <name>` actually resolves to, which version gets picked, whether two versions can
>   coexist;
> - what shape the `deps/` inside a project has, and where the compiler looks for libraries.
>
> **Read it before you start**, and do not guess from other languages' package managers - the
> model is different (a library **is a directory**, its identity is a **content hash**, and its
> dependencies are the `use` lines in its source, declared nowhere else).
>
> Path: `~/.claude/skills/lompi/SKILL.md`, or `<prefix>/share/lompi/skill/SKILL.md` with only the
> package installed (the same file). **The two guides are a pair**: syntax here, libraries and
> dependencies there.
>
> It is **a command of its own, not a `loment` subcommand**: `loment help` does not list it, and
> `loment lompi` is not a thing - run `lompi`.

**If `build` / `run` fails with `clang not found` and exit code 3**, you have an **older
package** (from when the toolchain lived in WSL and linked through clang). Upgrade to this
generation: linking is now done by `loment-lomelf` inside the package, producing PE/ELF directly,
touching neither clang nor WSL.

**Three traps in the packaged CLI** (on Windows they are how you misread success as failure):

- `-o` takes a name **without an extension**: `loment build hi.lomt -o hi` produces `hi.exe`;
  writing `-o hi.exe` produces `hi.exe.exe`.
- On Windows, call **`loment.cmd`** from Git Bash / MSYS; `bin/loment` is the POSIX script and on
  Windows it will try to link an ELF and exec it, reporting `Exec format error`.
- The line `loment: hi.exe` is a **success echo**, not an error; on failure it prints
  `loment: failed` and exits non-zero.

## 1. One program across the whole language

**Get this smallest skeleton running first** (this one `loment run`s as-is):

```rust
module hello

fn main() {
    print("hello from Loment\n");
}
```

`loment run hello.lomt` prints `hello from Loment`. It settles three things: **the entry point
is `fn main()`** (no arguments, no return type), **`print` writes a string to stdout** (one
`write` syscall; there is no printf and no formatting), and **`let` needs a type** when you do
declare a local. The freestanding entry is still `_start` (kernel / no-libc code): it takes no
arguments and wins when both exist. A normal user program should use `main` — the linker's entry
stub calls it and exits 0.

Below is the whole thing (it is called `tour`; the walkthrough follows the program):

```rust
module tour
choose runtime

const LIMIT: u32 = 3;

capability slots : disk[0..4] revocable

struct Entry {
    cents: u32,
    tax: u32,
}

enum Kind {
    Small,
    Big(u32),
}

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}

fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

fn total(e: Entry) -> u32 {
    return e.cents + e.tax;
}

fn score(k: Kind) -> u32 {
    match k {
        Kind::Small => { return 1; }
        Kind::Big(w) => { return w * 2; }
    }
}

fn sum_slice(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}

fn guarded(slot: u32) -> u32 {
    guard slots(slot);
    return slot;
}

fn _start() {
    let e: Entry = Entry { cents: 40, tax: 2 };
    let t: u32 = total(e);
    let s: u32 = score(Kind::Big(5));
    let a: u32 = 7;
    let b: u32 = 9;
    let m: u32 = max_of(a, b);
    let xs: [u32; 3] = [1, 2, 3];
    let sum: u32 = sum_slice(&xs);
    let g: u32 = guarded(2);
    let acc: u32 = 0;
    for i in 0..LIMIT {
        acc = acc + i;
    }
    write_dec(1, t);
    write_str(1, " ");
    write_dec(1, s);
    write_str(1, " ");
    write_dec(1, m);
    write_str(1, " ");
    write_dec(1, sum);
    write_str(1, " ");
    write_dec(1, g);
    write_str(1, " ");
    write_dec(1, acc);
    write_str(1, "\n");
    if t == 42 && s == 10 && m == 9 && sum == 6 && g == 2 && acc == 3 {
        write_str(1, "TOUR RESULT: PASS\n");
        syscall4(60, 0, 0, 0);
    }
    write_str(1, "TOUR RESULT: FAIL\n");
    syscall4(60, 1, 0, 0);
}
```

Running it should print `42 10 9 6 2 3` and `TOUR RESULT: PASS`.

> **`tour` links and runs on the packaged toolchain** (measured 2026-10-16, `0.1.4.5-quickfix`:
> `loment build tour.lomt`, then run the ELF) - it prints `42 10 9 6 2 3` and
> `TOUR RESULT: PASS`, exit 0. Two linker defects used to stop it: a 64-bit global crashed
> `loment-lomelf` with `Illegal instruction`, and a multi-line case table made the backfill table
> spin until it reported a "capacity" error. Both are fixed, and each has its own criterion. On
> an **older** package `check` and `ir` pass and the build still fails - that is the old build,
> not your program.
>
> **The `hello` skeleton above uses `print`, a string and no runtime, so it runs inside the
> package** - use it to confirm your environment before going further.

**Walkthrough** (in the order things appear):

| Fragment | What it is |
|---|---|
| `const LIMIT: u32 = 3;` | Module-level constant; usable directly in a function body |
| `capability slots : disk[0..4] revocable` | A capability domain declaration (section 5) |
| `struct Entry { … }` | A struct; every field carries a type |
| `enum Kind { Small, Big(u32) }` | An enum; a variant may carry **one** payload |
| `fn write_str(fd: u64, s: str) -> i64` | A function; **every parameter needs a type**, returns are written `-> T`, no return type means none |
| `print(s)` | Write a string to stdout (one `write` syscall). Use `syscall4` only when you need another fd or another call |
| `tur_edition()` | Which Tur edition this source is read as (`tur2026.1` / `tur2026.2`); the compiler also reports it on stderr |
| `syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64)` | A raw syscall: `(number, args…)`; **there is no printf** |
| `fn write_dec(…)` | The standard way to print a decimal: `alloc` + `store8` + reverse |
| `let buf: ptr = alloc(12)` | **`let` always needs a type**; `ptr` is a raw pointer |
| `48 as u8` | Conversions are always explicit `as` (integer/pointer both ways) |
| `fn max_of<T>(a: T, b: T) -> T` | A generic function (monomorphised at compile time) |
| `e.cents` | Field access |
| `match k { Kind::Small => { return 1; } … }` | **Arm bodies are blocks**, not expressions (6.1.1) |
| `Kind::Big(w)` | A payload-carrying variant pattern; `w` is the binding |
| `fn sum_slice(xs: [u32])` | A slice parameter; `&xs` passes an array as a slice; `slice_len(xs)` is its length |
| `guard slots(slot);` | A capability-domain guard (section 5) |
| `fn main()` | The **normal entry point**: no arguments, no return type. The linker's entry stub calls it and exits 0 |
| `fn _start()` | The **freestanding entry point** (kernel / no-libc): no arguments. If both exist, `_start` wins. When you link it yourself with `-Wl,-e,_start`, end it with `syscall4(60, 0, 0, 0)` - there is no return address |
| `let xs: [u32; 3] = [1, 2, 3]` | A fixed-size array; `xs[0]` reads and writes |
| `for i in 0..LIMIT { … }` | A range loop (upper bound **exclusive**) |
| `while i < n { … }` | A loop |
| `if a > b && c == 3 { … }` | The condition must be `bool`; `&&`/`\|\|` short-circuit |
| `syscall4(60, 0, 0, 0)` | `exit(0)` - a program exits through a syscall |

## 2.0 The Tur grammar system

This section is the reference for **what Tur is and what it accepts**, independent of any
library. The cheat sheet (section 2) is the one-page version; the trap list (section 6) is what
actually costs time.

### 2.0.1 Three layers, and the one rule that holds them together

| Layer | How a file is recognised | How it is read | Semantics |
|---|---|---|---|
| **Tur** (native) | no declaration (default), or `choose write grammar loment` / `tur` / `tur1` / `tur2` / `tur2026.1` / `tur2026.2` | the native lexer and parser, **no translator** | exactly this guide |
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
- **A misspelt keyword is not one of the four.** Measured (`docs/198` §2): `choose language
  python`, `choose grammar python`, `grammar python` and `choose write grammars python` all
  silently mean "no declaration", and the file is read as Tur - you then get an error pointing
  at `def` or `class`, not at the declaration. **If a foreign file reports "unknown top-level
  keyword", check this line first.**
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
| none / `loment` / `tur` / `tur1` / `tur2026.1` | **Tur 1** (`tur2026.1`) | **permanently frozen** - no new syntax ever enters it, old files keep every byte of meaning |
| `tur2` / `tur2026.2` | **Tur 2** (`tur2026.2`) | first batch of absorbed Python/Zig/Go syntax; Tur 1 files compile unchanged |
| future `tur2027.1` … | next year's family | same rule, each edition frozen on release |

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

**The edition says itself out loud in three places** (`docs/296` §1.2):

1. **At compile time**: `check` / `build` / `run` / `ir` print one line on stderr -
   `loment: grammar tur2 -> tur2026.2 (Tur 2)`, or `grammar default -> tur2026.1 (Tur 1)`.
   Tools that treat the driver as a subprocess pass `--no-edition-notice` to suppress it.
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
| **`for x in xs`** | `for x in xs { … }` | index loop: `let i = 0; while i < len { let x = xs[i]; body; i = i + 1 }` | v1 accepts **only a named array or slice** (`let xs: [u32; 4]`, or a `[T]` parameter). `x` is a **copy**. Expressions, `mut` references, and `for i, x in xs` are Tur 2026.3 |
| **`break` / `continue`** | inside `while` / `for i in lo..hi` / `for x in xs` | jump to the innermost loop's exit / update step | outside a loop is E19. The emitter closes the L2 epoch opened in the body **before** jumping (`docs/210`) - jumping out without closing is a leak |
| **bare `return;`** | `fn f() { if bad { return; } … }` | early exit from a **unit** function (`fn f()`, no `-> T`) | only in Tur 2 **and** only when the function has no return type; `return;` in a `-> T` function is still E19 |
| **`orelse` / `catch`** | `let a: u32 = maybe orelse 0;` / `let b: u32 = risky() catch 0;` | switch on the two-variant enum; success takes the payload, failure takes the fallback | v1 is **only the right-hand side of `let` / assignment**, and the enum must have **exactly two variants with the first carrying the payload** (`Option { Some(T), None }`, `Result { Ok(T), Err(E) }`). The fallback is **lazy** (evaluated only on the failing branch). `catch \|e\| { … }` and `catch return` are Tur 3 |
| **`print_num`** | `print_num(n);` | decimal, optional `-`, **no newline**, one `write` | a **Tur 2 builtin**, any integer type, widened by sign to `i64`. Lives in the runtime block, so `no_runtime` refuses it **by name (E31)** - add `choose runtime`. In Tur 1 the name is an ordinary identifier, so a user-defined `fn print_num` is not shadowed |
| **`defer stmt;`** | - | - | **designed, not implemented** (`docs/296` §2.3). Do not use it; it is not in this edition |

Two properties worth stating because they are checked, not assumed:

- **The sugar is byte-identical.** The same program written with `def` / `and` / `elif` and with
  `fn` / `&&` / `else if` produces exactly the same IR in Tur 1 and Tur 2 (`loment_grammar_test`).
- **Tur 1 never sees a new word as a keyword.** A Tur 1 file that uses `break` as a variable
  name still compiles; the same file declared `tur2` and using `break;` in a loop body does
  loop control. Both directions have criteria (p8 M91/M94/M95).

### 2.0.5 Tur 2026.3: lifetimes (design, **not available today**)

The next edition is designed in `docs/299`; nothing below is implemented in 0.1.5. It is listed
so you do not mistake "Rust has it" for "Tur has it":

- `&T` / `&mut T` become first-class **type** positions (struct fields, return types, nested,
  generic arguments), with an **implicit region parameter** - no `'a`, no `<'a>`, no `where`.
- Elision: one reference parameter → all reference outputs borrow from it; several reference
  parameters and one output → the output's region is the **shortest** of the inputs; methods
  borrow from `self`. Only a genuine fork needs a **source marker**: `fn pick(a: &[u32], b:
  &[u32]) -> &a [u32]`.
- Regions are static proofs; GC is run-time reclamation; they do not replace each other.

Today `&` in a type position is a **parse error** (`struct V { data: &str }` and
`fn f(a: &str) -> &str` both fail), and a struct field holds **scalars only**. Borrowing exists
only in call arguments (`&xs` / `&mut xs` for a `[T]` / `mut [T]` parameter).

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
| Constant | `const NAME: <type> = <literal>` | the initialiser must be constant-foldable; **no string constants** |
| Function | `fn f(a: u32, b: str) -> u32 { … }` | every parameter typed; no return type = unit; **≤ 10 parameters** |
| Generic function | `fn f<T>(a: T) -> T { … }` | monomorphised at compile time |
| Struct | `struct S { a: u32, b: bool }` | fields are **integer / `bool` scalars only** - no `str`, no `ptr`, no nested struct/array, no reference field (`docs/299` §1). A `str` field is refused by name |
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
| Audit bypass | `lspe pass "reason" { … }` | section 5.2; the reason is mandatory (E038) |
| Expression statement | `f(x);` | a call for its effect |

### 2.0.10 Expressions and precedence

Primary expressions: integer / string / `true` / `false` literals, identifiers, `E::Variant`,
calls `f(a, b)`, method calls `obj.m(a)`, field access `e.f`, indexing `e[i]`, parenthesised
`(e)`, struct literals `S { a: 1 }`, array literals `[1, 2, 3]`, and `&e` / `&mut e` in
**call-argument position**.

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
`match` on a generic enum uses the **monomorphised** name (`Result_i64_u32::Ok(v)`, trap 6.1.2).

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
| Nesting | 64 levels in the reference parser | a diagnostic; some deeply nested forms still crash the self-hosted parser (`#868`–`#874`) |
| Bump heap | 64 KiB (sealed) / 1 MiB (`hosted`) | SIGILL, exit 132, nothing printed |
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
             | "const" IDENT ":" type "=" const_expr ";"
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
             | IDENT "{" (IDENT ":" expr),* "}"            // struct literal
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

## 2. Syntax cheat sheet

| Construct | How it is written |
|---|---|
| Module | `module <name>` on the first line (no semicolon) |
| Grammar | `choose write grammar <name>` **before `module`**, once per file; no declaration means Tur 1 - see 2.0 |
| Entry point | `fn main()` (no parameters, no return type) is the normal entry; `fn _start()` is the freestanding entry and wins if both exist - see 2.0.12 |
| Project mode | `choose` - **seven dimensions**, each written at most once and **only in the entry unit**; libraries may not write it (E22). `std` / `no_std` · `gc_manual` / `gc_auto` / `gc_auto_alpha` / `no_gc` · `runtime` / `no_runtime` · `sealed` / `hosted` · `vm` / `no_vm` · `lms_full` / `lms_forensic_admin` / `lms_daily_admin` / `lms_manage_only_admin` · `lspe_open_admin` / `lspe_close_admin` - see 2.1 |
| Switch setting | `addin <name>` (**entry unit only**) - pulls in a "switch settings" unit, see 2.2 |
| Custom syntax | `comefor let "word" to { … }` … `byuse "word" done` - **define a new syntax at compile time**. The block after `to` is **a Loment program** (`fn main` is its entry; **the return value is how many tokens it ate**) which reads and emits tokens with `ct_n` / `ct_tok` / `ct_out` / `ct_syn`. Definition and terminator are **top-level only** and cannot nest; expansion happens at the token layer **before parsing**, so errors and jumps still point at the line you wrote. See `docs/184` |
| Import | **Two spellings, neither takes a semicolon**: `use name` (**searched in layers, first hit wins**: (1) project root `<project>/deps/<name>/<name><ext>` (2) the toolchain's own `<tool dir>/../share/lompi/store/<name>/<version>/<name><ext>` (3) the five built-in roots `loment/lib` → `examples` → `selfhost` → `tools` → `lspe`, where **only layer 3 requires the name to be unique**); `use "path/to/other.lomt"` (relative to the current file or the working directory - **use this form for a sibling file in your own directory**). `<ext>` defaults to `.lomt` and a project can change it (section 7). At most **300** `use` lines in one file. **Several name-form `use` lines are allowed in 0.1.5** (older packages stopped at one) - see 6.3 |
| Function | `fn f(a: u32, b: str) -> u32 { ... }` (no return value: `fn f()`); **at most 10 parameters** (a hard constraint of the self-hosted side's layout: the parameter table has 10 slots. It fails in two shapes: a count mismatch between parameters and arguments is the normal E3, while **a call site with more than 10 arguments** goes through `panic` - exit code 132 with **no diagnostic**. When you see "no error but exit 132", count your arguments) |
| Export | Add `pub` for cross-module visibility: `pub fn` / `pub struct` / `pub const` |
| Constant | `const NAME: u32 = 3` |
| Variable | `let x: u32 = e;`, assignment `x = e;` - **the type annotation is required**; the language has no type inference |
| Control flow | `if` / `else if` / `else`, `while`, `for i in lo..hi` (upper bound exclusive), `return <expr>;` (the value is **required**). **Tur 2 adds** `for x in xs`, `break` / `continue`, and bare `return;` in unit functions - see 2.0.4 |
| Struct | `struct S { a: u32, b: u32 }`; literal `S { a: 1, b: 2 }`; field `s.a` |
| Enum | `enum E { A, B(u32) }`; construction `E::B(3)`; no payload `E::A` |
| `match` | `match x { E::A => { … } E::B(v) => { … } _ => { … } }` - **arm bodies are blocks**; exhaustive (or use `_`) |
| `if let` | `if let E::B(v) = x { … } else { … }` |
| `Option`/`Result` | Predefined generic enums, with `?` propagation |
| Array | Type `[u8; 16]`; literal `[1, 2, 3]`; index `xs[0]` (assignment `xs[0] = 1` works) |
| Slice | Parameter types `[T]` / `mut [T]`, pass `&xs` / `&mut xs`, `slice_len(s)` for the length |
| Generics | `fn max_of<T>(a: T, b: T) -> T` (monomorphised at compile time) |
| trait | `trait M { fn m(self) -> u32; }` + `impl M for S { … }`, static dispatch `obj.m()` |
| Types | `u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str` `()`, a struct/enum name, `[T; N]`, `[T]`, `mut [T]`. **No floats, no `char`, no tuples**; `f64` is a library over `u64` bit patterns, not a language type - see 2.0.8 |
| Conversion | `x as u64` - integer/pointer conversions are always written out |
| Strings | `"..."`, escapes exactly `\n \t \r \0 \\ \"`; **any other escape is an error** (E19), not a silent degradation; the operations are the `str_*` builtins in section 3 |
| Raw foreign block | `let c { …raw bytes… }` / `py { … }`, after `command <lang>` / `foruse <lang>` - see 2.0.12 |
| Audit bypass | `lspe pass "reason" { … }` - the reason is mandatory (E38), section 5.2 |
| Comments | `//`, `/* */`; `///` is a doc comment (`loment doc` extracts those) |
| Operators | Precedence as in Rust: `\|\| && == != < <= > >= \| ^ & << >> + - * / %`, unary `- !` |

### 2.1 Project mode `choose`

`choose` is how a program states a property of **the whole artifact**. There are **seven
dimensions**, each written at most once, and each one lands in the Potato form object - so
"what kind of artifact is this" is answerable on the side that does **not** read the source.

```rust
module myapp

choose no_std          // ← once for the whole program; the default is std
```

| Dimension | Values | Default | What it decides |
|---|---|---|---|
| **Layer** | `std` · `no_std` | `std` | which layer the program may use - `std` unlocks the hosted layer (libc / filesystem / threads / time / network), `no_std` refuses it |
| **Reclamation** | `gc_manual` · `gc_auto` · `gc_auto_alpha` · `no_gc` | `gc_manual` | how memory is reclaimed - see section 2.1 and `docs/210`; `no_gc` declares the artifact carries no collector |
| **Runtime** | `runtime` · `no_runtime` | `no_runtime` | whether the artifact may carry a runtime (the heap is still on demand) |
| **Port** | `sealed` · `hosted` | `sealed` | whether the artifact may reach outside itself by linking host libraries |
| **Execution model** | `vm` · `no_vm` | `no_vm` | which execution model the program is for - see below |
| **Memory safety** | `lms_full` · `lms_forensic_admin` · `lms_daily_admin` · `lms_manage_only_admin` | `lms_full` | which of the seven LMS levels are on. **The direction is reversed**: the default is full, and a non-full value is a downgrade; every non-full value carries `_admin` |
| **Audit ledger** | `lspe_open_admin` · `lspe_close_admin` | `lspe_open_admin` | whether LSPE records the ledger (off changes the ledger, never a verdict); the Potato v16 ledger records the site, obligation and evidence |

What holds for all seven:

- **It is a property of the program, not of a file**: each dimension appears at most once, and
  **only in the entry unit** (a library pulled in by `use` that writes one is E22). Both compilers
  enforce this.
- **A default preserves the pre-dimension behaviour - with one recorded exception.** `lms`
  defaults to `lms_full`, and that tier adds inline bounds checks on `alloc(<const>)`-derived
  accesses, so it does change the artifact and some out-of-bounds behaviour (`docs/270` section
  6). Treat that as a documented exception, not as the rule.
- **Contradictory pairs are named, not lumped.** `gc_auto` with `no_runtime` is refused because
  automatic reclamation *is* that runtime; `hosted` with `no_std` is refused because `no_std` says
  there is nothing underneath and `hosted` says to link against it; both automatic tiers with
  `no_std` are refused because a collector needs that runtime. The diagnostic names the pair
  and the reason; it never says "illegal combination".
- A value that is not in the table is a typo, and a typo **is E22**.

**`vm` says "which execution model", not "which tier".** The release gate opened on 2026-10-17
(`docs/231`), so an installed toolchain accepts `choose vm`. Today it only records the
dimension: the artifact is **byte-identical to `no_vm`**, and there is no bytecode. The
interpreted tier is a separate tool - `loment-lvm file.ll` runs the LVM interpreter over LLVM IR
(`docs/243`) - and the artifact does not carry it.

**`no_std` is what Loment actually is** (it was written for FujoOS in the first place). `std` is
the default and means you may use host capabilities. **When it is required**: writing systems,
writing something that runs on bare metal - writing it out is for readers and tools; the
language does not force it.

**`sealed` is what makes the artifact checkable.** An undefined symbol is an error, not a
warning, so a sealed artifact provably has no ports to the world. `hosted` is the other path: a
C-runtime program whose entry point is `fn main() -> u32` rather than `_start`, declared as
allowed to link real libc and shared libraries. **The door opened on 2026-10-17**: both release
and development launchers pass `--allow-ports` to the linker, which links real ELF shared
objects (`.so`) and lets the system loader map and bind them (`docs/183` V2, `docs/219` section
8c). Remaining limits: ELF only (no PE import path), one shared object on the self-hosted
linker, function symbols only, and a host `ld.so`/glibc must be present. When you call
`loment-lomelf` directly rather than through `loment`, add `--allow-ports` yourself.

### 2.2 Switches `set choose` / `choose` / `addin`

A **switch** is code that is only compiled in when it is on. The closed branch **does not even
reach the parser as tokens** - so "closed means not depended on" is literally true: what the body
refers to **does not have to exist**.

> **Turning a switch on for this machine** (the `lock` / `chooseunlock.lomlock` mechanism) **does
> not exist yet** (`docs/182` section 3). It is blocked on an undecided rule: the lock would be
> **per-machine**, while this repository's identity is "the same source produces the same bytes" -
> once locked, one source would compile differently on two machines. To make a switch the default
> today you **write it in the source**. (The package manager's lock file `lompi.lock` is a
> **different thing** - see the lompi guide.)

```rust
module myapp

// definition: a name plus "the code compiled in when it is on"
set choose verbose {
    pub fn banner() -> u32 { return 0x5EED; }
}

choose verbose          // the value: on (`choose close verbose` turns it off)
                        // **absent means off**

fn _start() {
    syscall4(60, banner() as u64, 0, 0);   // only compiles when verbose is on
}
```

The rules (all three are **E22**, enforced by both compilers):

- a file may carry many switches (**the cap is 500**; over that is an error, never a silent
  drop), but the **same name only once**;
- a name has to be **defined** with `set choose <name> { … }` before it is used, otherwise you get
  "undefined switch";
- a switch declaration may **not nest inside another switch body** (that makes "is it on" an
  egg-and-chicken question);
- **libraries may not write `choose`** - a library expresses what it needs by declaring
  capability requirements and lets the project decide.

**Across files you use `addin`** (`addin chooseset` pulls in a switch-settings unit; written only
in the entry file). `chooseset.lomt` looks like this (**this block compiles by itself**, so you
can copy it whole):

```rust
module chooseset            // chooseset.lomt, in the same directory as the entry
addin chooseset             // it names itself at the top (the convention)

set choose verbose {
    pub fn banner() -> u32 { return 0x5EED; }
}
```

The entry file is (**this block does not compile on its own** - its definition is in the
`chooseset.lomt` beside it; only the two halves together are the example, and the runnable pair is
`loment/examples/addin/` in the repository):

<!-- no-compile -->
```rust
module myapp                // the entry

addin chooseset             // pull it in; its `choose` **applies to the whole program**
choose verbose
```

- A unit pulled in by `addin` may contain **only choose-related code** (`module` / `addin` / the
  three `choose` forms); anything else (`fn`/`struct`/`use`) is refused - to load code use `use`
  (that is a library), `addin` is for switches.
- **It takes part in compilation**, so a function the entry uses must be `pub`.
- **Only the entry may write `addin`**; in a library or an addin unit it is refused (and would not
  take effect anyway).
- Switch values go into the Potato object (the `switches` field), so "which switches are on" is
  visible on the side that does **not** read the source.

### 2.3 Custom syntax `comefor` / `byuse`

**Define a new syntax at compile time.** The program in the definition is **run** by the kernel;
it reads tokens and emits tokens. What follows is a **complete, compiling** example - `def ANSWER
= 42;` expands to `const ANSWER: u32 = 42;`:

```rust
module cfdef                    // in the repository: loment/comefor/def_dialect.lomt

comefor let "def" to {
    /// Write a string literal's bytes into the **macro body's own host memory**, then
    /// synthesise a token from them.
    fn emit_s(s: str, k: u64, line: u64, col: u64) -> u64 {
        let n: u32 = str_len(s) as u32;
        let p: ptr = alloc(32);
        let i: u32 = 0;
        while i < n {
            store8(p, i, str_byte(s, i) as u8);
            i = i + 1;
        }
        return ct_syn(k, p, n as u64, line, col);
    }

    /// Eat `name = number ;` (four) and emit `const name : u32 = number ;` (seven).
    fn main() -> u64 {
        let buf: ptr = alloc(32);
        ct_tok(0, buf);                         // the name -> buf
        let ln: u64 = load8(buf, 12) as u64;    // read line/column off the source token
        let cl: u64 = load8(buf, 16) as u64;
        emit_s("const", 0, ln, cl);
        ct_out(buf);                            // the name: copy the source token
        emit_s(":", 3, ln, cl);                 // 3 = punct
        emit_s("u32", 0, ln, cl);
        ct_tok(1, buf); ct_out(buf);            // `=`
        ct_tok(2, buf); ct_out(buf);            // `42`
        ct_tok(3, buf); ct_out(buf);            // `;`
        return 4;                               // four eaten
    }
}

def ANSWER = 42;                // from here to `byuse`, `def` is syntax

fn main() -> u64 { return ANSWER as u64; }

byuse "def" done
```

**What the body gets** (`docs/184` section 3.2) - these four and nothing else; no other builtin is
available in this context:

| Signature | Meaning |
|---|---|
| `ct_n() -> u64` | How many tokens remain from the cursor |
| `ct_tok(i: u64, buf: ptr) -> u64` | Write the source token's fields into `buf`; out of range returns 0 |
| `ct_out(buf: ptr) -> u64` | Append `buf` to the output stream (the text is looked up **in the source** by span) |
| `ct_syn(k: u64, txt: ptr, ln: u64, line: u64, col: u64) -> u64` | Synthesise a token (the text comes from the **macro body's own heap**) |

A record is **20 bytes**: `+0 kind` (0=ident 1=number 2=string 3=punct 4=eof) `+4 off` `+8 len`
`+12 line` `+16 col`; read and write it with `load8`/`store8`. `off`/`len` cover the **raw text**
in the source (the len of `"hello"` is 7, not 5).

**Rules**:

- **Definition and terminator are top-level only** and cannot nest; a word may not be defined
  twice;
- `byuse` takes the name from the `comefor` (`comefor let "def"` → `byuse "def" done`);
- expansion happens at the token layer **before parsing** - so `comefor`/`byuse` are not keywords,
  and you may use `def` as an ordinary identifier elsewhere (expansion only applies inside the
  region you declared);
- **position is the body's responsibility**: a token emitted with `ct_out` automatically gets its
  position back from the source; a token made by `ct_syn` needs its `line`/`col` filled in. If you
  get them wrong the kernel **cannot tell**, and errors will point elsewhere;
- `use` is **not supported inside the body yet** - write helper `fn`s inside it (the body is a
  complete program).

Runnable example: `loment/comefor/def_dialect.lomt` (its hand-expanded twin is `def_hand.lomt`;
a criterion checks the two compile to **byte-identical** IR).

## 3. Builtins (all of them, there are no others)

**These are the only builtins.** There are no built-in types like `String` / `Vec` / `HashMap`,
no I/O wrappers, no string formatting. **But the toolchain ships a standard library**
(`lompi/store/`, installed with the package, nothing to fetch) - `use vec` / `use map` / `use fs`
each bring in one module, see 3.1 at the end of this section.

| Signature | Meaning |
|---|---|
| `str_len(s: str) -> u32` | Length in bytes |
| `str_byte(s: str, i: u32) -> u32` | The i-th byte |
| `str_eq(a: str, b: str) -> bool` | Content comparison |
| `str_concat(a: str, b: str) -> str` | Concatenation (**the compile-time bump heap, which is only 64 KiB** - do not build big strings) |
| `str_ptr(s: str) -> ptr` | The data pointer (for feeding syscalls) |
| `print(s: str) -> u32` | Write the string's bytes to stdout (one `write` syscall) |
| `tur_edition() -> str` | The Tur edition this program is compiled as, e.g. `tur2026.2` (docs/296). The compiler also prints it on stderr at every check/build/run |
| `print_num(n) -> u32` | **Tur 2 only** (in Tur 1 the name is an ordinary identifier). Decimal, optional `-`, **no newline**, any integer type widened by sign to `i64`. It lives in the runtime block, so `no_runtime` refuses it by name (E31) |
| `alloc(n: u32) -> ptr` | Bump-heap allocation (the same 64 KiB cap - **and read 6.2.14 before relying on it**) |
| `free(p: ptr) -> u32` | Returns the block to the allocator (bump pointer + address-ordered free list); it cannot grow past 64 KiB and does not coalesce (`docs/210` section 2.8) |
| `load8(p: ptr, off: u32) -> u32` | Read a byte |
| `store8(p: ptr, off: u32, v: u8) -> u32` | Write a byte |
| `ptr_add(p: ptr, n: u32) -> ptr` / `ptr_sub` | Pointer offset |
| `slice_len(s: [T]) -> u32` | Slice length |
| `panic(code: u32) -> u32` | Does not return (the type is a placeholder) |
| `atomic_add(p: ptr, n: u32) -> u32` | Atomic add |
| `get_bits(v: u8, shift: u32, width: u32) -> u8` / `set_bits(v: u8, shift: u32, width: u32, x: u8) -> u8` | Bitfield read/write; the second argument is the **shift**, the third is the **width** - not `hi`/`lo` |
| `inb(port: u16) -> u32` / `outb(port: u16, v: u8) -> u32` | Port I/O (**only on the Rust translation path**, not the native one) |
| `syscall4(nr: u64, a0: u64, a1: u64, a2: u64) -> i64` | A raw syscall (rax/rdi/rsi/rdx) |
| `syscall6(nr: u64, a0..a4: u64) -> i64` | The same, with two more arguments |
| `syscall7(nr: u64, a0..a5: u64) -> i64` | The same, with **all six** argument registers used (`rdi/rsi/rdx/r10/r8/r9`). This is what makes **UDP with addresses writable**: `sendto`(44) / `recvfrom`(45) take a sixth argument (a `socklen_t`), which had nowhere to go on `syscall6` |
| `thread_spawn("entry", stack_top: u64, arg: u64, done: u64) -> i64` | Start a **real thread** (Linux `clone`). The first argument is the **name** of a function written in this unit that takes exactly one parameter — it becomes a **direct call** in the product, so the call graph stays static. Returns the new thread's tid. You supply the stack (see `thread_stack` in `os/thread`). `done` is the address of a `u32` the product **atomically increments and `futex_wake`s after the entry returns**, before the thread exits (`0` = no notification): the entry itself cannot do this, because the product exits the thread the moment it returns. **ELF target only** (Windows has no `clone`) |

**To print a number**: under **Tur 2**, `print_num(n)` (add `choose runtime`). Under Tur 1,
copy `write_dec` from section 1, or use `fmt` / `numfmt` from the standard library. There is no
`printf`.

**Syscall numbers**: on the Linux ELF target they are Linux's own (`write`=1, `exit`=60,
`openat`=257…). **The Windows PE target implements only eight**: `read`(0) / `write`(1) /
`close`(3) / `brk`(12) / `exit`(60) / `getdents64`(217) / `openat`(257) / `newfstatat`(262), and
**every other number returns -1 (failing silently)** - stick to those eight if it has to run on
both. `/proc/self/cmdline` is synthesised by the runtime on PE, so reading argv is the same on
both.

### 3.1 The toolchain's standard library (one module at a time)

It lives under the toolchain prefix in `share/lompi/store/` (in the source repository that is
`lompi/store/`), and **the name form of `use` brings in one module**:

```rust
module myapp
choose runtime

use vec            // dynamic array (u32 elements, the caller owns the buffer)
use numfmt         // decimal/hex formatting

fn _start() {
    syscall4(60, 0, 0, 0);
}
```

| Package | Roughly what is in it | Examples |
|---|---|---|
| `std` | 133 **portable** modules (not one syscall): containers, text, big integers, floating point, hashing and checksums, compression, bit twiddling | `vec` `map` `set` `heap` `deque` `trie` `text` `utf8` `parse` `fmt` `bigint` `f64` `crc32` `deflate` `png` |
| `host` | The host side (needs syscalls): files, directories, argv, stat, logging | `fs` `io` `dir` `argv` `stat` `log` |

- **Several name-form `use` lines are allowed in 0.1.5** (verified: `use vec` plus `use numfmt`
  compiles). Older packages limited a unit to one name-form `use`; on those, the **path** form
  (`use "<prefix>/share/lompi/store/std/0.1.0/text.lomt"`) was the workaround. **Take modules one
  at a time anyway; a facade is huge and collision-prone.**
- `use std` exists and drags all **133** modules into one unit. The 0.1.4.6 fix removed the old
  "18 minutes then SIGILL" failure (measured in 0.1.5: about 16 seconds to IR), but the facade is
  still large; prefer per-module imports.
- Only the name form works this way; `store` is a **directory tree** - do not reach into it by
  path unless you mean the path form above.
- `mem` / `num` / `proc` / `sha256` / `json` exist **on both sides**: `use mem` gets the
  **toolchain core library** copy (`loment/lib/`), not the one in the `std` package.
- The `test_*` functions inside standard-library files are each module's self-check; they may be
  part of the source at no extra cost.

## 4. How to read the error codes

The compiler prints lines shaped like:

```
E2 @13 line 4: 1
```

Read it as: `E2` is **E002** in the unified numbering; `@13` is **the 13th token** (not a column);
`line 4` is the line; everything after the colon is that token's text. **The codes are grouped by
what you should do, not by wording**:

| Code | What it asks you to do |
|---|---|
| E1 | Types do not line up (including return / payload / builtin arguments) |
| E2 | **Something is not declared** (undeclared variable / undefined function / unknown type, including use-before-definition) |
| E3 | **Wrong number of arguments** |
| E4 | Capability-domain problem (undeclared / out of range / duplicate) |
| E5 | A space declared out of bounds with `excluded` was used |
| E6 | Value already moved |
| E7 | Borrow conflict (mutable borrow alongside a borrow / two mutable borrows) |
| E8 | `match` / enums (not exhaustive, duplicate pattern, not an enum, wrong payload binding) |
| E9 | Name collides with a base type / empty struct / empty enum |
| E10 | `?` used where the value is not `Result`/`Option` |
| E11 | Slice mutability (writing through a read-only slice, parameter missing `mut`) |
| E12 | Dangling reference |
| E13 | Duplicate definition / duplicate name |
| E14 | Assignment target is not an lvalue |
| E15 | Fields and indexing (no such field, missing field, duplicate initialisation, field access on a non-struct…) |
| E16 | Array literals / length disagrees with the declaration |
| E17 | Illegal `as` conversion |
| E18 | **`use <name>` does not resolve** (not found, or several hits) - rename it, add the file, or switch to the path form `use "...lomt"` |
| E19 | **Syntax error (parse time)** - fix the line the message points at with `line:col` |
| E20 | **More than 300 `use` lines in one file** - split the facade, do not stuff a whole library into one file |
| E21 | **An `extern fn` signature outside FFI stage 1** (scalars and `ptr` only) - see 7.3.1 |
| E22 | **`choose` used wrongly** (written twice / misspelled mode / a library wrote it) - see 2.1 |
| E23 | **Not implemented in this version (a compiler limit)** - **not your source's fault**; write around it, and report it if you cannot. Progress is the milestone table in `docs/145` |
| E24 | A **command declaration** has the wrong shape (or tries to declare an official `loment` command) |
| E25 | `_start` and `command_main` **both exist** - a unit is either a normal program or a command |
| E26 | `command_main` without a command declaration / with the wrong signature |
| E27 | **A non-`()` function can fall off its end** - add the final `return <expr>;` (structurally: body ends in `return`, an `if`/`else` where both return, or a `match` where every arm returns) |
| E28 | **A literal index is out of range** for a fixed-size array (LMS level 1) |
| E29 | **Use after free** (LMS level 1, under the default `lms_full`) |
| E30 | **Unknown name in a type position** (separate from E2: the fix is "declare the type", not "declare the value") |
| E31 | **A construct that would drag in the runtime** while the artifact says `no_runtime`; it also refuses `print_num` (Tur 2) by name |
| E32 | Cannot write the output file |
| E33 | **Unit too large / capacity exceeded** - split the unit; never a truncated build |
| E34 | **LSPE: an allowance with no source** - produced only in strict mode (`--lspe-mode=strict`); `lspe_close_admin` does not produce it |
| E35 | **LSPE ledger contradiction** - an unregistered site kind, mutually exclusive dimensions, twin disagreement, or capacity overflow |
| E36 | **LSPE self-check failed** - should never appear in production; if it does, the ledger itself missed an entry |
| E37 | **LSPE policy lock** - reserved; the policy layer does not exist today, so it is never emitted |
| E38 | **`lspe pass` without a reason** - the reason is mandatory; an anonymous bypass is an unlabelled floor |

`E900` / `E999` are internal ("wrote no diagnostic record" / "unclassified - please report");
they are not source errors.

**Every code has a full card** (what went wrong / why / at least three fixes / supported and not
supported) which `loment check` renders; `loment explain E4` gives the ASCII version of one. The
table above is only an overview.

**The most common ones**: `E2` is nine times out of ten a **missing type annotation** (`let x =
1;` is not legal; write `let x: u32 = 1;`) or an undefined function name; `E3` is a call with the
wrong argument count; `E19` see 6.1.

## 5. Capability domains (the one new thing Loment adds)

```rust
module blk
choose runtime

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);   // out of range -> trap; in range -> audit count +1
    return slot;
}
```

Semantics: `guard cap(e);` evaluates `e` to an index; outside `[lo, hi]` it **traps** (with no
other effect), inside it increments the audit table. **A literal out of range is a compile-time
error** (E4); a non-literal is a runtime check.

**Honest boundaries.** `guard` constrains **the index** to the domain only; it **is not
authorisation** - it never asks "does the current principal hold this capability", because the
principal-to-capability binding lives on the kernel side. `revocable` is currently just a flag in
the declaration and in the domain-description table.

And the boundary that matters most when you deploy something: **nothing gates the operations that
actually do things**. `syscall4` / `syscall6` are raw builtins, `alloc` / `load8` / `store8` /
`ptr_add` are raw, and FFI links arbitrary C. A program - or a library it `use`s - opens a socket,
reads a file and writes through a raw pointer with **no declaration, no audit entry, and nothing
in the diagnostics that shows it**. `capability` says what a domain is; it does not stop anyone.

### 5.1 LSPE: the audit layer (0.1.5)

**LSPE = Loment Safety Police Espace** (`docs/262`). It is **not a seventh checker**: the
enforcers stay exactly as they are (LGC for lifetime, LMS for legality, `capability` / `guard`
for authority, `runtime` / `port` for the artifact, E006/E007/E012 for ownership). LSPE adds the
two things none of them had: **why this allowance was granted**, and **a record of that
judgement in the artifact**.

> **It audits the compiler's own decisions, not your program and not you.** The signature is
> `questioner(Judgment) -> Derivation | Hole`; there is no "user" and no "defendant program" in
> it. When the compiler cannot prove a hole, it hands the developer a **question** with options,
> a default and consequences - it does not say "your code is wrong".

The **four parts** (`docs/262` §0):

| Part | Where | One line |
|---|---|---|
| **Questioner** | during compilation, **inward** | audits the compiler's own judgement |
| **Counter-asker** | during compilation, **hand-off** | gives a named hole to the developer: options, default, consequence |
| **LSPC (police)** | **before compilation** | posts the residual risk: what you will have to sign for, fix it or not |
| **Ledger** | in the artifact | records who opened it, who closed it, who signed |

The **space** of a unit is `Espace(U) = <Sites, Obligations, Evidence, Questions, Ledger>`. The
boundary of the space is the **site census**: a site outside the census is E035.

#### 5.1.1 The dimension `lspe`

| Value | Meaning | Default |
|---|---|---|
| `lspe_open_admin` | audit on: questioner + counter-asker + LSPC + ledger | **yes** |
| `lspe_close_admin` | **audit off**: skips questioning and the ledger; **every check still runs** | no |

`lspe_close_admin` turns off **the police, not the law**. LMS / LGC / `runtime` / capability
decisions are unchanged; only "why was this allowed" stops being recorded. The value carries
`_admin` because closing a safety system is an administrative act (`docs/262` §10.1).

#### 5.1.2 Sites, obligations, evidence

A **site** is one allowance point - `guard cap(i)`, an array/slice index, a `free`, an
`alloc`-derived `load8`/`store8`, a call. Site ids are **content-addressed**
(`unit:kind:fnv8(text#occurrence)`), not line numbers, so formatting a file or inserting an
unrelated site does not renumber the rest.

The **14 obligations** (`loment/lspe/registry.py`), each naming its semantic owner and the
evidence that may discharge it:

| Obligation | Owner | Accepted evidence |
|---|---|---|
| `InBounds` | docs/146 / E028 | Syntactic, Typed, LocalFlow, Interproc, GlobalInvariant, Assumed |
| `Alive` | docs/210 / E029 | LocalFlow, Interproc, Assumed |
| `InDomain` | docs/146 | Typed, GlobalInvariant, Assumed |
| `Exclusive` | docs/206 / E007 | LocalFlow, Interproc, Assumed |
| `Initialized` | docs/206 / E006 | Syntactic, LocalFlow, Interproc, Assumed |
| `NotDangling` | docs/206 / E012 | LocalFlow, Interproc, Assumed |
| `NoRuntime` | docs/182 / E031 | Axiom, Syntactic, Typed |
| `FreeOwnership` | docs/210 | LocalFlow, Interproc, Assumed |
| `LadderLiability` | docs/210 | Typed, Interproc, GlobalInvariant, Assumed |
| `CapabilityLive` | docs/146 | Typed, GlobalInvariant, Assumed |
| `TypeWellFormed` | docs/158 / docs/147 | Axiom, Typed, Interproc, Assumed |
| `DropHonest` | docs/253 §1.2 | **Assumed only** (the floor) |
| `GuardHolds` | docs/146 | Syntactic, Typed, GlobalInvariant, Assumed |
| `SummaryContract` | docs/210 §7 | Axiom, Interproc, Assumed |

The **eight evidence kinds** are `Axiom`, `Syntactic`, `Typed`, `LocalFlow`, `Interproc`,
`GlobalInvariant`, `Assumed`, `Differential`. They form a finite lattice by inclusion.
**`Differential` is never in an accept set**: the twin proves *agreement*, not *correctness*, so
it can be a gate but can never discharge an obligation. That single rule is why a
two-implementation bug cannot turn the ledger green.

#### 5.1.3 Context levels (`--lspe-level`)

| Level | Sees | Typical proofs | Default |
|---|---|---|---|
| L0 | the site plus constants | literal bounds, type domains | - |
| L1 | the function's CFG | initialisation, liveness, exclusivity, local WF | - |
| L2 | call edges + summaries | cross-function deref-only, free ownership | - |
| L3 | whole-unit fixpoint | loop invariants, cross-block flow | **yes** |
| L4 | cross-unit (library summaries) | library contracts | explicit only |

Lowering the level does **not** change a verdict; it changes how many sites can be proved, and
`floors` goes up. The chosen level is written into the artifact as `lspe.level`, so "bought a
green light with a low level" is visible.

#### 5.1.4 The ledger (Potato **v16**)

`lspe_open_admin` writes one object per unit:

```json
"lspe": {
  "state": "lspe_open_admin",
  "set_by": "default",
  "mode": "full",
  "level": 3,
  "sites": 1234,
  "obligations": 1234,
  "proved": 1200,
  "undecided": 34,
  "deferred": 0,
  "refused": 0,
  "floors": 0,
  "floor_detail": {"assumed": 0, "stale_wf": 0, "budget": 0, "drop_honest": 0, "pass": 0},
  "per_kind": {"InBounds": "400/410", "Alive": "390/400"},
  "passes": [{"id": "unit:pass:…", "line": 12, "end_line": 15, "reason": "caller checked", "sites": 2}],
  "pass_sites": 0,
  "passed_sites": 0,
  "pass_ratio": "0/1234",
  "invariants": {"i1": "ok", "i2": "not_run", "i3": "not_run", "i4": "not_run", "i5": "not_run"},
  "acked": 0
}
```

What is **actually implemented today** (0.1.5, `loment/lspe/`):

- the site census for `guard` / `index` / `bounds` / `free` / `alive` / `init` / `exclusive` /
  `call` (`census.py`, 623 lines, mirrored in `lspe.lomt`);
- the 14-obligation registry and the evidence lattice (`registry.py`);
- a worklist solver whose only current edge is "one obligation per site"; the provenance /
  falsification / cross-examination engines (E1–E4) are **partially** wired;
- the ledger, `per_kind`, `pass_ratio`, and **I1 only** (every site kind is registered). **I2–I5
  report `not_run`**; `deferred` / `refused` / `acked` are 0; `floor_detail` is 0 except `pass`;
- `lspe pass` blocks (below);
- `lspe_close_admin` zeroes the counts but still records `state` and `set_by`.

So the honest summary is: **the ledger exists and is checkable; the full proof search is the
0.1.5.2+ work.** Do not read `"proved"` as "the compiler proved my program safe" - read it as
"this many sites had at least one accepted evidence item at this level".

#### 5.1.5 `lspe pass "reason" { … }`

The minimal, named, recorded bypass for something you know but the compiler cannot prove:

```rust
module lspepass

fn f(a: [u32; 4], i: u32) -> u32 {
    lspe pass "caller checked 0 <= i < 4" {
        let x: u32 = a[i];
    }
    return x;
}
```

- The header and braces are flattened at parse time; the body **is** the surrounding statement
  sequence, not a new scope. A `let` inside is visible outside.
- **The reason is mandatory**: missing or blank is **E38**, a parse-time abort. An anonymous
  bypass is exactly the unlabelled floor this design exists to remove.
- Sites inside are still counted in `sites` / `obligations` / `per_kind`; they get
  `covered_by`, the solver does not prove them, and only `pass_ratio` rises. A bypass cannot
  make the coverage numbers look better.
- It does **not** propagate across calls: a function containing a bypass is excluded from the
  deref-only safe table, so its callers stay UNDECIDED.
- `pass_ratio` is a **ratchet**: on the corpus it may only go down. Adding a bypass requires
  `python tools/lspe_ratchet.py --emit` - that step is the acknowledgement that a site was sold.

#### 5.1.6 Commands and what is not there yet

| Surface | Today |
|---|---|
| `loment lspe state FILE [--lspe-level 0..4]` | **works** (checkout launcher): prints the ledger of one unit |
| `loment lspe why SITE` | prints "lands in 0.1.5.2" - derivation trees are not shipped |
| `loment lspe verify ARTIFACT [SIDECAR]` | same - proof hashes and the `.lspe` sidecar are not shipped |
| `--lspe-mode=full\|quiet\|strict` | designed as a build knob; the driver has the parameter, **the CLI does not expose it yet**, so E034 (undecided under strict) is not reachable from the command line |
| `--lspc=full\|quiet\|strict\|off` | **designed, not implemented**; the LSPC pre-flight sheet does not exist yet |
| `E37` policy lock | reserved; the policy layer does not exist and the code never emits it |

#### 5.1.7 The question space and the five invariants (design; mostly not shipped)

When the questioner cannot close a hole, the design hands over a **question** in `Q001`–`Q012`
(these are *questions*, not errors - they never block unless strict mode upgrades them):

| Code | Asks |
|---|---|
| `Q001` | no source found for this allowance |
| `Q002` | the check is inert (turning it off would not make anything red) |
| `Q003` | the two implementations disagree |
| `Q004` | `TypeWellFormed` was not re-proved after a substitution (`stale_wf`) |
| `Q005` | the step budget ran out |
| `Q006` | the interprocedural depth limit was reached |
| `Q007` | destructor honesty cannot be proved |
| `Q008` | the domain is unknown (a capability does not cover it) |
| `Q009` | aliasing is uncertain |
| `Q010` | `free` ownership is ambiguous |
| `Q011` | a runtime leak |
| `Q012` | an assumption has expired (the source changed) |

The ledger's **five invariants** are how LSPE audits itself. Only **I1** is evaluated today;
**I2–I5 are emitted as `"not_run"`** (`loment/lspe/ledger.py`):

| # | Invariant | Violation | Today |
|---|---|---|---|
| **I1** | every site kind is registered (no ghost, no missing kind) | E35 | **checked** |
| **I2** | every allowance has a Q1 source or an `assumption(id)` label | E34 | not run |
| **I3** | the dimensions do not contradict (`no_runtime` has no runtime site, `gc_manual` has no unrecorded `free`, `sealed` has no hosted call) | E35 | not run |
| **I4** | the floor is countable (`floors == assumed + stale_wf + budget + drop_honest`; no UNDECIDED that is neither answered nor defaulted) | E36 | not run |
| **I5** | twin sameness: the same site gets the same verdict and source from both implementations | E35 | not run |

**What the seven `choose` dimensions do not do**: `lspe` is orthogonal to all of them. It never
changes a verdict, never changes the IR, and two artifacts that differ only in `lspe` are
byte-identical (only the Potato ledger differs).

## 6. The trap list (every entry was measured; **reading this first saves most of the rework**)

Rust intuition carries you through 90% (scalars / strings / slices / control flow / generics /
traits). **What is left is almost entirely in the part Loment invented.** 6.1 is language and
syntax, 6.2 is what the runtime does to you, 6.3 is what the toolchain cannot do today, 6.4 is
the Tur 2 constructs that do not behave like their namesake languages - the four are different
things, and it is worth knowing which one you are looking at.

### 6.1 Syntax and types

1. **A `match` arm body is a block, not an expression.** `Kind::A => 0,` is not accepted; write
   `Kind::A => { return 0; }`. (At this commit the *checker* no longer rejects the expression
   form, and what you get instead is a build failure from the linker - it stops on the value the
   codegen emits for it, not on the `match`. Both implementations refuse it; write the block.)
2. **Generic enum patterns use the monomorphised name.** When the matched value is
   `Result<i64, u32>`, `Result::Ok(v)` gives `E8 与主体枚举 Result_i64_u32 不符` (*does not match
   the subject enum*); the answer is **`Result_i64_u32::Ok(v)`**. Non-generic enums are written
   normally: `Kind::Big(w)`.
3. **A valueless `return;` exists only in Tur 2, and only in a unit function.** In Tur 1 (or in
   a `-> T` function) `return;` gives `E19 期望表达式，得到 ';'` (*expected an expression, got
   ';'*). Either `return <expr>;`, finish with if/else, or declare `tur2` and use a `fn f()`
   with no return type.
4. **Modules read top to bottom**: define before use, do not rely on forward references.
5. **A struct literal cannot be an argument**: `f(S { a: 1 })` is rejected by the native backend
   (`native 后端不支持该表达式: StructLit`). Bind it first - `let x: S = S { a: 1 };` then `f(x)`.
6. **A struct literal cannot sit in a condition** (`if p { }` is resolved by Rust's ambiguity
   rule); add parentheses.
7. **One parse error stops the whole check** - every diagnostic after it is missing. So change one
   thing at a time and re-run `loment check`; "no other errors" does not mean there are none.
8. **`const` initialisers are restricted**: the initialiser has to be something the constant
   folder accepts, and `const B: u32 = A + 1;` is *accepted by the checker* and then fails in the
   **build** with a backend-internal message (`nalloc=0 nslot=10 …`) rather than a diagnostic.
   Long tables of offsets end up written out as plain numbers.
9. **There are no string constants**: `pub const NAME: str = "x";` is an error (E001). For a string
   label write `pub fn name() -> str { return "x"; }` - that is how `.lomp` manifests do it
   (section 10).
10. **`use` takes no semicolon** in the house style. `use parse;` is *accepted* today, but every
    file in this repository is written without it, and a semicolon there is noise a reader has to
    wonder about.
11. **`mut` is only rewritten in the `tur` spelling.** In the default `loment` spelling `mut` is
    an ordinary identifier (`loment/selfhost/checker.lomt` really does have a variable called
    `mut`), so `let mut len: u32 = …` declares a variable named `mut` and then trips over
    `len`, pointing somewhere else. In `grammar tur*` the front end strips `let mut NAME` and
    `( mut NAME :` / `, mut NAME :` with an equal-length rewrite, so Rust-style mutable
    bindings work. Reassignment never needs a marker. **Do not rely on `mut` as a variable name
    in a file you may later switch to `tur`.**
12. **Non-ASCII output is mojibake in a Windows console.** PE writes bytes **straight to the
    console** and Windows decodes them with the current code page - 936 (GBK) on a Chinese
    Windows - so UTF-8 Chinese comes out as `婧愮爜`. **The shim has no `WriteConsoleW`** and the
    program cannot fix it from its side. Anything a person will read goes out **in ASCII**; that
    is the only set that decodes the same under every code page. (`loment`'s own help and cheat
    pages were moved from Chinese to English for exactly this reason, `docs/169` section 3a.)

### 6.2 What the runtime does to you

13. **The string escape set is exactly `\n \t \r \0 \\ \"` - and anything else is an error.**
    This was measured the hard way and **changed on 2026-10-16** (`#33`, `#36`): until then
    `"\r"` compiled to the letter `r`, `"\0"` to the digit `0`, `"\x41"` to the three bytes
    `x41`, and an unknown escape such as `"\q"` silently lost its backslash. All four are now
    **E19**, and the message names the accepted set. Older packages still have the old
    behaviour - if a literal comes out shifted, check the compiler generation first.

    What is still true: there is **no `\xNN`, no `\uNNNN`, no `\e`**, and no way to spell an
    arbitrary byte in a literal. For a byte that has no escape, write it numerically -
    `store8(p, o, 13 as u8); store8(p, o + 1, 10 as u8);` - or put the text in a raw foreign
    block (`let c { … }`, section 2.0.12), whose body is carried byte for byte.

14. **The heap is a fixed 64 KiB, and running out aborts the process.** `alloc` returns a bare
    `ptr`: no null, no error, no `Result`. Measured: `alloc(65536)` succeeds and `alloc(64)` right
    after it **kills the process with SIGILL** (exit code 132) - nothing is printed and the
    program has no way to notice. **The ceiling is the constraint, not the reclamation**: `free`
    does reclaim (a bump pointer plus an address-ordered free list, `docs/210` section 2.8) - what
    it cannot do is grow past 64 KiB, and it does not coalesce. So a service that allocates ~900
    bytes **and never frees** dies after about 72 requests, and one request asking for `n = 60000`
    kills it outright. Two ways out: free as you go, or bring in a host - under `choose hosted`
    the arena comes from the host's `malloc` and is 1 MiB. If you are writing something that
    serves, this is the first constraint to design around.

15. **Array and slice indices are not bounds-checked; the default LMS tier checks the
    alloc-derived accesses it can see.** The answers differ by site:
    - **Literal index, out of range, fixed-size array** - `xs[5]` on `[u32; 3]` - is
      **refused by `check` (E028)**, at compile time, in the default tier.
    - **Any other array/slice index** - a variable, an expression, or anything into a slice
      `[T]` - is **unchecked at runtime**: a little out of range writes elsewhere with no trap,
      and `xs[100000] = 1` **segfaults** the process.
    - **`load8`/`store8` on an `alloc(<const>)` block** are checked under the default
      `lms_full` (level 3B): level 4 skips the out-of-range access (read returns 0, the write
      does not happen) or level 5 traps. See `docs/270` section 1.

    An index that comes from outside your program - a request, a file, a length field - is
    therefore a memory-safety question, and the language will not raise it for you. **Guard it
    yourself**: a fixed-size array has a compile-time length, so write `if i >= 3 { … }`; a
    slice carries its own length, so `slice_len(x)` gives it to you. The old `choose lms_open`
    escape hatch is gone; select a tier with `choose lms_*` (section 2.1).

16. **Integer arithmetic is defined to wrap, there is no checked arithmetic, and division by
    zero kills the process.** The wrap is a **rule, not an accident**: `4294967295 + 1` is `0`
    and `0 - 1` is `4294967295`, on purpose, and the emitted IR never carries LLVM's
    `nsw`/`nuw` - so this is defined behaviour, not the undefined behaviour C leaves you with.
    Nothing traps and nothing warns, so a length or a size computed from untrusted input can be
    quietly wrong. There is no `checked_add` / `checked_mul` to reach for; write the guard
    yourself, *before* the arithmetic, e.g. `if a > 4294967295 - b` for `a + b` on `u32`.
    `a / b` and `a % b` with `b == 0` **abort the process** (SIGILL, exit code 132) - the
    compiler emits the trap, so it is a defined abort rather than whatever the CPU happens to do;
    check the divisor first if a zero can reach you. (Both rules are settled in `docs/144`
    section 3 - the note there used to read "the rule must be settled before M2".)

17. **A non-`()` function that can fall off its end is refused by `check` (E027).** Write
    `fn f() -> u32 { … }`, forget the final `return <expr>;`, and `loment check` says so - before
    2026-10-10 it **passed** and the binary died with `Illegal instruction` when execution reached
    that path (measured while writing `loment/tools/lomcli.lomt` on 2026-09-15: a 40-line
    directory walk crashed exactly here). The rule is structural: the body must end with `return`,
    with an `if`/`else` where both branches return, or with a `match` whose every arm returns.
    This is also why early exits are written `return 0;` rather than a bare `return;` (6.1.3). A
    function that exists only to allow early exits can still be declared `-> u32` and end with
    `return 0;`, and callers ignore the value.

18. **SIGPIPE is not ignored, so a peer that hangs up can kill you.** Writing to a socket whose
    peer has gone raises SIGPIPE, whose default action terminates the process. Measured: the first
    write after the peer's RST returns `-104` (ECONNRESET, recoverable), and **the second one kills
    the process with exit code 13**, printing nothing. A response written as headers-then-body, or
    any body larger than the socket buffer, puts a write after the RST. Either ignore the signal or
    use `send`(44) with `MSG_NOSIGNAL` (`0x4000`) instead of `write`.

19. **A blocking `read` on an accepted socket blocks forever unless you set a timeout.** Measured
    on a live server: one client that connects and sends **nothing** takes the whole service out of
    service - every other request times out with zero bytes until that client goes away. A
    half-sent request line and a client dribbling one byte every 300 ms do the same. The primitive
    to fix it exists - `setsockopt`(54) with `SO_RCVTIMEO` (20) and a `struct timeval` of two
    64-bit fields, through `syscall6` - and it works: measured, a `read` on a socket nobody writes
    to returned `-11` (EAGAIN) after 1107 ms with a one-second timeout. This guide used to say
    nothing about needing it; now it does.

### 6.3 What the toolchain cannot do today

These are defects rather than language rules, and each one is here because meeting it without
knowing costs half a day. The text says which ones 0.1.5 already fixed.

20. **Several name-form `use` lines are fine in 0.1.5** (verified: `use vec` plus `use numfmt`
    compiles). Older packages stopped at one with `loment: cannot open <garbage>`, and on those
    the path form was the workaround; a unit with imports also lost most of its string-literal
    capacity (measured: eight ~34-byte literals could bring the old compiler down).
21. **Deep nesting is still a wall, but the number moved.** The reference parser refuses nesting
    beyond **64 levels** with a diagnostic ("嵌套超过 64 层"); the self-hosted parser still
    segfaults on some deeply nested forms (nested parentheses / indexes / generic arguments,
    #868-#874). Flatten it or split it into functions.
22. **A `.lomt` file with no `module` declaration is a clear diagnostic now** ("期望 module").
    Older packages crashed with SIGSEGV; 0.1.5 does not.
23. **`use std` is no longer an 18-minute SIGILL.** 0.1.4.6 fixed it; measured in 0.1.5 it
    reaches IR in about 16 seconds. It is still a 133-module facade - prefer per-module imports.

### 6.4 Tur 2 traps (the ones that are new, not inherited from Rust)

24. **`defer` does not exist yet.** It is designed in `docs/296` §2.3 and listed for Tur 2, but
    it is not implemented. `defer free(p);` is parsed as an expression statement and fails.
    Use an explicit `free` on every exit path.
25. **`orelse` / `catch` are not general expression operators.** They work only on the right-hand
    side of a `let` or an assignment, and only for an enum with **exactly two variants whose
    first carries the payload** (`Option`, `Result`). `f(a orelse 0)`, `return x orelse 0;` and
    `catch |e| { … }` are not accepted.
26. **`for x in xs` only walks a named array or slice variable.** `for x in f()` and
    `for x in self.items` are Tur 2026.3. A slice parameter works (`xs: [u32]`); a fixed array
    works (`let xs: [u32; 4]`). `x` is a **copy**, so assigning to it does not write back.
27. **Bare `return;` is for unit functions only.** The moment the signature says `-> T`, `return;`
    is E19 again - including inside an `if` that is meant as an early exit.
28. **`break` / `continue` need a loop, and in Tur 1 they are just names.** Outside a loop in
    Tur 2 they are E19. In Tur 1 a variable called `break` is legal, so a file that uses it as
    a name cannot simply be switched to `tur2`.
29. **`&T` in a type position does not parse.** `struct V { data: &str }`, `fn f(a: &str) ->
    &str`, and a struct field of `str`/`ptr` are all refused. Borrowing is argument-only; use
    `ptr` + an explicit length when the value has to be stored.

## 7. Two openings: your own extension, your own `loment` command

These two are what make Loment **a base that other things can grow on** rather than a language
with one kind of file and one set of commands.

### 7.1 Your own source extension

**The extension is not part of the language** - only `.lom` is special in the syntax (it is the L0
interface contract). Every other extension is treated as L1 source, and `.lomt` is a habit. To
give a project its own extension, put a `loment.conf` in the **project root** (the same shape as
`lompi.conf`: a lexer scans for a label; no `module` header and no otherwise-valid file needed):

```rust
// loment.conf
module conf

pub fn source_ext() -> str {
    return ".foo";
}
```

After that `use geom` looks for `deps/geom/geom.foo` (the path form is unaffected: `use
"area.foo"` still works). Three boundaries:

- **The name form only.** `use "area.foo"` carries its own extension and the configuration cannot
  touch it.
- **The default still catches the rest**: with `.foo` configured, the toolchain's own modules
  (still `.lomt`) are found as before.
- **Unreadable means unconfigured**: no such file / no `source_ext` / a value not starting with
  `.` / a value containing an escape - all fall back to `.lomt`. Configuration files get broken by
  people, and falling back beats compiling a string of incomprehensible errors.

The copy next to the toolchain (`<tool dir>/loment.conf`) is the **global default** for the same
key; a project's own file wins.

### 7.2 Your own `loment` command

Like `git`: `loment foo ...` first looks for `loment-foo` on `PATH`, and if it is there it
**forwards the arguments untouched** (the name is shifted off, nothing else is touched, and the
exit code comes back as it was); otherwise it falls back to the official CLI (which means "unknown
command"). So:

```bash
# put a loment-git on PATH and you have `loment git ...`
loment git status      # -> loment-git status
```

Two boundaries:

- **Official commands win.** `loment version` / `loment build` and the rest always run the
  official implementation; a `loment-version` on `PATH` cannot displace it.
- **The registrant is software, not user configuration.** It is a **filename convention** (the
  executable is called `loment-<name>`); there is no registry and no configuration file - install
  it and it works, remove it and it is gone.

**You can also put the registration in the source** (`docs/223`, landed 2026-10-10): a unit
that declares

```rust
module mycmd

pub fn loment_command() -> str { return "mycmd"; }
pub fn command_main(argv: ptr, argc: u32) -> u32 { return 0; }
```

builds into a `loment-mycmd` program, which the launcher's PATH lookup then finds. Two
boundaries: the name must be a **function** called `loment_command` (the bare word `command` is
already a keyword for external code blocks, `docs/185`), and a unit cannot have both
`command_main` and `_start` (E25) - it is either a command or a normal program. A library may
not declare a command (E24).

## 7.3 Libraries written in other languages (FFI)

**Two legs, chosen by what is on the other side** - picking the wrong one wastes the afternoon:

| What the other side is | How to use it | Coverage |
|---|---|---|
| **A C-ABI library** (C / C++ / Rust / Zig / Go(c-archive) / Swift / C#(NativeAOT) / Fortran…) | Declare `extern fn` and pass `--link that.o` when building | Every language that can export C symbols |
| **A runtime** (Python / Java / JS / Ruby / Lua…) | `use proc`, then `proc_sh` / `proc_python` starts an interpreter process and you read its output back | Every language with an interpreter |

### 7.3.1 The C-ABI family (real linking)

```rust
module ffi_demo

extern fn c_add(a: i32, b: i32) -> i32;    // signature only, semicolon at the end
extern fn c_free(p: ptr);                  // no `-> T` means void

fn _start() {
    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);
}
```

```
$ cc -c -O1 -ffreestanding -fno-pic lib.c -o lib.o      # build the other side yourself
$ loment build app.lomt --link lib.o -o app             # we link it
```

**The signature takes scalars only** (`i8..i64` / `u8..u64` / `bool`) **and `ptr`**. `str` is
"pointer plus length", **not a C string**, and a struct passed by value follows another register
classification rule entirely - either of those is **E021**, not a silent miscompile. A string
bound for C has to be assembled in memory as a NUL-terminated byte sequence by you.

Call sites pass arguments by the **platform C ABI** (Linux: the first six integer arguments go in
`rdi rsi rdx rcx r8 r9`; Windows: `rcx rdx r8 r9`), while Loment-to-Loment calls keep Loment's own
convention (arguments on the stack).

**What the other side's `.o` may contain, and the one door that is shut.** Relocations are
applied, several objects may reference each other, and archives (`.a`) are expanded - all of
that works. The boundary is the **port gate** (meeting it produces an **error**, not a guess):

- an object that carries **undefined symbols** (`printf`/`malloc` being the typical ones) is
  **refused by the linker by default**. The launchers pass `--allow-ports` in **both** channels
  since 2026-10-17 (`docs/219` section 8c); calling `loment-lomelf` directly means adding it
  yourself. With the door open the linker can also supply `memcpy` / `memset` / `memcmp` /
  `malloc` / `free` / `__stack_chk_fail` from its own runtime.
- or keep the other side freestanding (`-ffreestanding -fno-stack-protector`) so it calls only
  itself - still the right choice when you do not want a runtime dependency.
- dynamic libraries (`.so`) **work on ELF since 2026-10-17**: the product carries
  `PT_INTERP`/`PT_DYNAMIC`/`DT_NEEDED` and a PLT/GOT, and the host's loader (`ld.so`) performs
  the mapping and relocation binding. Limits today: ELF only (PE FFI is not implemented), one
  shared object on the self-hosted linker, and function symbols (not data symbols) are
  imported. `choose hosted` (section 2.1) is what allows the artifact to reach them.
- several `--link` flags may be given at once; they are appended in order.
- the other side's `.o` **may not exceed 4 MiB** (over that is an error, never a truncated build).

### 7.3.2 Python / Java / JS (the process bridge)

```rust
module py_demo
choose runtime

use proc

fn _start() {
    let buf: ptr = alloc(1024);
    let n: i64 = proc_sh("python3 -c 'import json; print(1)'", buf, 1024);
    if n > 0 {
        syscall4(1, 1, buf as u64, n as u64);
    }
    syscall4(60, 0, 0, 0);
}
```

`proc_python("...")` is sugar for Python. **Changing language means changing the command** -
nothing on the Loment side moves.

Three boundaries: **what crosses is a byte stream, not a pointer** (no structs); it is
**Linux/ELF only** (the Windows shim has no `fork`/`pipe` and returns -1 there); and the machine
has to have that interpreter.

**With the package installed rather than the repository**, `proc` is not in your project and
`use proc` reports "name import not found". The packaged copy is at
`<prefix>/share/loment/lib/proc.lomt`, and the regular way to bring it in is to put it in your
project:

```
mkdir -p deps/proc && cp <prefix>/share/loment/lib/proc.lomt deps/proc/
```

(`deps/<name>/<name>.lomt` is the first place the name form searches - and the shape `lompi` uses
when it installs a library.)

## 8. The subsystems: what each one is and what it guarantees

The language is the visible part. This section is the rest of the system, one subsection per
component, in the order a program meets them: memory reclamation, memory safety, the audit
ledger (section 5.1), execution models, the artifact object, hot update, the optimizer lines,
foreign libraries, the network/thread surface, and the tools. **Every subsection separates
"works today" from "designed".** A component being described here is not a promise that every
part of it is shipped.

### 8.1 LGC: reclamation (`docs/210`)

The ladder has **five rungs** - `L0`, `L0.5`, `L1`, `L2`, `L3` - and the `choose` value decides
which of them are attempted. The compiler tries **strongest first**: every `alloc` site is
proved into the highest rung it qualifies for.

| Rung | What the compiler proves | Who reclaims | What it costs |
|---|---|---|---|
| **L0 static** | `let p: ptr = alloc(<integer literal>)` and `p` appears only in that declaration plus as **the whole argument** of a callee whose parameter is non-escaping | the **stack** - it never enters the heap | a larger frame |
| **L0.5 shared frame area** | L0's candidate, but a function has **at least two** such sites; their sizes are summed | one frame area shared by the function | `L05_MAX = 2048` bytes; first-declared wins, the rest stay L0 |
| **L1 last use** | the declaration is at the **function-body top level**, the pointer only passes through to safe parameters, so the last use is countable | the compiler inserts `free` after the last use and before the earliest `return` | a path that the insertion point cannot reach (an early return) **leaks once** - a leak, not an error |
| **L2 epoch** | inside a `while` / `for` **body**, every `alloc` is a `let` whose name never leaves the body | the body is unwound in bulk at its end (pointer rollback, no list, no scan) | **one** allocation escaping the body cancels the whole body's rung |
| **L3 runtime** | nothing above applies | the collector | the collector's full cost |

| `choose` value | L0 | L0.5 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| `gc_manual` (default) | - | - | - | - | **the compiler inserts no reclamation**; a manual `free` still works, the program manages memory |
| `gc_auto` | - | - | - | - | **mark-sweep + precise roots** |
| `gc_auto_alpha` | **yes** | **yes** | **yes** | **yes** | **adaptive + generational** |
| `no_gc` | - | - | - | - | **declares** the artifact carries no collector; the linker verifies it |

Facts that change how you write code:

- **`gc_manual` is byte-for-byte the old behaviour.** Choosing a tier is the only way to get a
  collector; a default must not change an existing program.
- **`free` really reclaims now** (bump pointer + address-ordered free list), but it cannot grow
  past the arena and **does not coalesce**. The arena is 64 KiB sealed / 1 MiB `hosted`.
- **`gc_auto` triggers every 32 KiB of allocation** and its arena is 64 KiB, so a program whose
  **live set exceeds about 32 KB still exhausts**. That is a limit of the tier, not of the
  language: `gc_auto_alpha` raises it to "live set close to the whole arena" by making the
  threshold follow the live set and adding a space trigger. The difference is measurable: a
  44 KB live set exhausts under `gc_auto` and runs under alpha.
- **The collector is not free even when it does nothing**: a `gc_auto` unit that uses `alloc`
  carries about 5.2 KB of collector text; a unit that never allocates does not.
- **Roots are precise, not guessed**: the compiler emits a slot table (`@__loment_roots`) with
  addresses of slots, so a fake pointer never appears on the root stack. The table has 1024
  slots; overflow is refused by name, never silently written past.
- **`gc_auto` is stop-the-world and non-moving.** No incremental/concurrent collector, no
  compaction, no weak references, no finalizers, no TLAB/region. Those are recorded
  non-goals, not backlog items.
- **`no_gc` is a claim checked in the linker.** It says "no collector in the artifact", not "no
  allocation"; the IR carries `; loment-gc: no_gc` and both linkers refuse an artifact that
  contradicts the claim.

**What the artifact records**: Potato `gc` (the tier) and `gc_ladder` (the rung counts). From
**v19** the ladder has **five** components - `l0`, `l0_5`, `l1`, `l2`, `l3`, `total_sites` - and
the self-consistency rule is `l0 + l0_5 + l1 + l2 + l3 == total_sites`. v10–v18 objects keep
four rungs and are still valid; the ladder only grows forward.

### 8.2 LMS: memory safety (`docs/253`, `docs/270`)

`gc` answers **when memory is returned** (inward reachability). `lms` answers **who may touch
it** (outward permission). They are two directions of the same graph, and they are orthogonal:
`no_runtime` + `lms_full` is legal because every LMS level is instructions or out-of-process,
not a runtime component.

**Seven levels**, each a fallback for the one above:

| Level | Name | What it does | When |
|---|---|---|---|
| 1 | **checker** | compile-time E028 (literal index out of bounds) and E029 (use after free) | always (E028), `lms_check` (E029) |
| 2 | **manager** | `--lms-report PATH` prints the unit's memory map: every `alloc` site with unit/function/line/size | compile time |
| 3 | **guard** | inline bound check on `load8`/`store8` from `let p: ptr = alloc(<const>)`; **only direct pointers** - `ptr_add`, slices and struct fields are not checked | runtime, instructions only |
| 4 | **reject** | out-of-range access is **skipped** (load returns 0, store does not happen) and the program continues | runtime |
| 5 | **crash** | `llvm.trap` - the process dies with SIGILL (exit 132), the OS reaps it | runtime |
| 6 | **restart** | `restarter/loment-restart.c` (written in C, **outside the process**) restarts it, up to a limit | out of process |
| 7 | **seed** | the bootstrap seed plus its **name, byte size and hash** - the trusted floor is named and measured | out of process |

**The tier names are the four official combinations** and there are no separate `chooseset`
files for them:

```
lms_full               all seven (default, no `_admin`)
lms_forensic_admin     1 + 2 + 3 + 4
lms_daily_admin        1 + 3 + 4
lms_manage_only_admin  2
```

- **The direction is reversed**: the default is full, and writing a tier is a **downgrade**. Any
  non-full value carries `_admin`; the rule is mechanically checked in `tools/lomentc.py`
  (`LMS_TIERS`) and by `loment_lms_test`.
- **Seven fine switches** (`lms_check`, `lms_manage`, `lms_guard`, `lms_reject`, `lms_crash`,
  `lms_restart`, `lms_seed`) are defined by the compiler; a tier seeds them and an explicit
  switch overrides. They are not `set choose` definitions you write.
- **Potato v17** records `lms_levels` (seven booleans, the final result); **v18** adds
  `lms_budgets`, one budget per level (`none` / `instructions` / `state` / `outside`). If a
  level ever needs a runtime state table, its budget changes to `state` and `no_runtime` is
  refused **by name** at that moment - rather than users discovering a table in an artifact
  that promised no runtime.
- **What level 3 B does not do**: it does not check derived pointers, slices or struct fields.
  Conservatively not emitting a check is safer than reading block data as a length. The
  "response" half of level 3 is delegated to levels 4/5; rebuilding from a shadow copy is not
  implemented.
- **Level 5 cannot kill other processes under `no_std`** - only syscalls exist. It exits itself
  and lets the OS reap it; the restarter (level 6) is the parent.
- `no_std` and `no_runtime` are unaffected: LMS introduces no runtime symbol.

### 8.3 LVM: execution models (`docs/232`, `docs/243`, `docs/250`–`docs/267`)

**LVM is the fourth reader of the same IR** (the other three are clang, `lomelf` and the Rust
translation). It does not invent a bytecode language or a type system; its instruction set is
exactly the subset `lomelf` already parses, plus execution.

| Tier | What it is | Status |
|---|---|---|
| **T0** | an interpreter over the text IR; parses into tables first, then walks them | landed |
| **T1** | feedback profile: block execution counts, branch/switch edge counts, call-site counts | landed |
| **T2** | baseline compile: decode once before execution, execute table-driven | landed (reference and mirror) |
| **T3** | guard specialization + deoptimization (only B1 constant specialization) | landed (reference) |
| **compact form** | a serialization of the parsed tables (not a second language), with a header saying which form it is | landed (reference) |
| **cache** | a compiled-op cache next to the input, keyed by input size, mtime, content hash and **IR hash**; stale caches are named and refused, never half-used | landed |
| **fusion** | A4 superinstructions: two adjacent pure ops dispatched once (`arith+arith`, `load+arith`, `arith+store`, `icmp+br`) | landed |
| **mirror T2** | the same design written in Loment (`loment/tools/lvm.lomt`), shipped in the package as `loment-lvm` | landed |

What matters when you use it:

- **`choose vm` only records the dimension today.** The artifact is **byte-identical to
  `no_vm`** and carries no bytecode. The interpreter is a separate tool: `loment-lvm FILE`
  (the packaged binary) or `python tools/lvm.py FILE.lomt` in a checkout.
- **T1 does not record types.** The input is typed LLVM IR, so "this `add i32` saw an i32" is
  not new information. It records only what is statically unavailable: block counts, edge
  counts, call-site targets/counts.
- **The profile never enters Potato.** Potato answers "what the unit is"; a profile answers
  "what happened when it ran". Putting the latter in would make the same source produce
  different objects on two runs and break byte-for-byte determinism.
- **The limits are named** (`docs/263`): 16 MiB VM address space, 64 KiB zero page, 4096-byte
  frames, `STEP_CAP = 50,000,000` instructions **per call**, `MAX_DEPTH = 128` (the mirror uses
  32), and `llvm.trap` is defined as **132** with a site line on stderr
  (`lvm: llvm.trap at <fn>:<block>#<index>`).
- **Do not grade it by wall-clock.** The repo's rule is that gates use deterministic counts
  (decode counts, instruction counts), not ratios; the same workload varies 3–5× across
  machines.

### 8.4 Potato: the artifact object

Potato is **the form object written next to a compiled unit**: "what this unit is", answerable
**without reading the source**. It is not a debug dump and not a profile.

- **Versions are cumulative and only grow**: `v0` … `v19`. Each new required field bumps the
  version; older objects stay valid, and a new version never rewrites an old one.
  Examples of the ladder: `v6` grammar, `v7` boundary, `v8` gc, `v9` runtime, `v10`
  `gc_ladder`, `v11` port, `v12` surfaces, `v13` vm, `v14` net_ladder, `v15` lms, `v16` lspe,
  `v17` lms_levels, `v18` lms_budgets, `v19` five-rung `gc_ladder`.
- **Two validators** (`tools/potato.py` and the Loment twin `loment/tools/lompotato.lomt`)
  check shape and **cross-field self-consistency** - for example `surfaces.total_sites ==
  boundary.syscalls`, `w0+w1+w2+w3 == total_net`, `total_net == surfaces.net`, and the ladder
  sums. They cannot check that the numbers are *semantically right* (that would need the
  source); they can and do catch "two places disagree".
- **Two emitters** (`lomentc.emit_potato` and `loment/selfhost/potato.lomt`) must produce
  **byte-identical** objects for the same input.
- **`--emit-potato PATH`** is a compiler flag; `--emit-llvm` / `--emit-rust` require it (M46:
  the compiler forces the form object out alongside code).
- **What it is for**: the side that does not read the source - a linker, a packager, an audit -
  can answer "does this artifact carry a collector?", "does it open a socket?", "which
  capability domains does it declare?", "which safety tier was it built with?".

### 8.5 Hot update (`docs/280`–`docs/292`)

A **Pre-Release** is a projection of **fix commits only** onto a baseline. It is not "a new
version": features are refused at admission (`projection.line[].kind` must be `"fix"`), and the
manifest records the applied line, the evicted commits with reasons, and the tree hash.

- **Build**: `--profile hotswap` emits an entry gate, a slot table and an epoch; the packaged
  `loment-lsp` is built in that profile. A normal build carries the fixed core but no slot
  table, and `hot_install` returns 15 without touching a byte.
- **Swap**: the candidate (`.lomhot`) is installed into the **slot that is not running**
  (A/B alternating), then the frame chain is rewritten so every live frame continues in the new
  image, and the epoch becomes READY. The entry gate is checked at a **safe point** - the top of
  the LSP event loop.
- **Refusals are named, before anything moves**: digest mismatch RC 7, slot-shape mismatch RC 4,
  frame layout change, call-site id change. A refused candidate leaves the process untouched.
- **The `.data` is not swapped.** Strings and initialised globals keep their old values, so
  "fix a message" is not a hot-update fix; it needs a restart. That is a semantic boundary of
  v1, not a bug.
- **Single-threaded only.** Two slots alternating requires other threads to stop inside the
  SWAPPING window, and there is no language-level stop point or thread count yet.
- **Projection is reproducible**: replaying the same line yields the same tree hash. `status`
  lists the line **in application order**, marks a move to an earlier point as `(rollback)`,
  and prints the `restart required` fixes from the last manifest.
- **Not in the packaged `loment` command list yet**: the tooling is
  `tools/loment_update.py` (`manifest` / `check` / `compare`), `tools/loment_hotart.py`,
  `tools/loment_hotload.py` and `tools/loment_staging.py`.

### 8.6 LomentFaster: three speed tiers (**design, not shipped**)

`docs/298` (2026-11-04) designs **one `choose` dimension with six values**, not three stackable
switches:

```
lomentfaster_C_close    (default)  baseline
lomentfaster_C_open               hand the IR to clang -O3
lomentfaster_f1_close             baseline
lomentfaster_f1_open              Loment's own scalar passes
lomentfaster_f2_close             baseline
lomentfaster_f2_open              f1 + loop optimisation + vectorisation
```

- Writing two values of the dimension is E22; a library may not write it.
- **`_close` is a ratchet**: the three closed values must produce artifacts **byte-identical to
  today**. That is why they are separate spellings - so the source and Potato say which tier
  you explicitly closed.
- C tier needs clang and is **ELF only**; PE is refused, not silently downgraded. If clang is
  missing it says so and falls back to `lomelf`.
- f1 is mem2reg/SSA, register allocation, constant folding + DCE + peephole, and small-function
  inlining; the measured target is clang `-O1`/`-O2` level, i.e. parity with `rustc -O3` on the
  benchmark set. f2 adds LICM, strength reduction, unrolling/rotation and vectorisation.
- **It is not in `CORE_DIMS` yet.** Do not write `choose lomentfaster_*` and expect it to
  compile.

### 8.7 Lomenter: the optimizer with a ledger contract (**design, not shipped**)

`docs/297` (2026-11-03) is the answer to "write our own LLVM", and the answer is **change the
centre**: LLVM's contract is "be faster"; Lomenter's is "**after optimisation, the countable
properties in the artifact still hold**".

- Three readings, one chosen: rewriting an LLVM-scale infrastructure is impossible; borrowing
  LLVM is already available (the `--opt` escape hatch); so the project does the third - its own
  IR, analyses, passes and backends, with the ledger as the acceptance criterion.
- **The hard constraint is byte-for-byte twins.** A conventional optimiser (hash-order
  iteration, pointer keys, profile-driven, floating-point cost models) cannot produce a
  reproducible IR order, so two implementations diverge immediately. The design therefore
  includes an explicit allow/deny list for determinism; that is the most expensive part, more
  than writing passes.
- First wave: 7 analyses, 3 passes, LIR = the existing IR subset plus **facts / effects /
  regions**; MIR is separate because native needs virtual/physical registers while VM needs
  stack slots.
- **Not implemented.** There is no `lomenter` binary and no `docs/297` code path; today the
  optimiser story is `--opt` (clang `-O2`) or the LVM tiers.

### 8.8 DGT: compile-time digraphs (**planned, 0.1.5.2**)

`digraph<K>` is planned as a **compile-time graph class value** with a capability lattice,
layout specialisation and static verification (`#1196` umbrella). It is **not a `choose`
dimension** - it is a value in the type system, not a whole-program promise. Five queries are
the planned surface: nodes, edges, acyclicity, topological order, cuts. Nothing is implemented
today; treat any `digraph` syntax as unavailable.

### 8.9 Foreign libraries: the two legs in practice

**Leg 1 - C ABI** (section 7.3.1): `extern fn` signatures take scalars and `ptr` only;
`--link` accepts `.o`, `.a` (fixed-point member selection) and, on ELF, `.so` (the product
carries `PT_INTERP` / `PT_DYNAMIC` / `DT_NEEDED` and a PLT/GOT; the system's `ld.so` binds it).
Objects with undefined symbols are refused by default; the launchers pass `--allow-ports` in
both channels, and the linker can then supply `memcpy` / `memset` / `memcmp` / `malloc` /
`free` / `__stack_chk_fail` itself. PE has **no FFI at all**. Measured end-to-end: C, C++,
Rust and Zig; the same mechanism covers every language that exports C symbols.

**Leg 2 - the process bridge** (`use proc`, section 7.3.2): measured end-to-end for Go, Python,
Java, JavaScript, Perl and Lua. What crosses is a **byte stream**, not a pointer; it is
**Linux/ELF only**; the machine needs the interpreter.

**LibL**: the standard library's **libc face** (`docs/232`). `use libl` brings in the libc-shaped
modules (`string`, `ctype`, `stdlib`, `stdio`, `libl_host`) with libc names, argument order and
return semantics, while the rest of `std` keeps Loment's explicit-length/offset shape. The two
faces share the same bytes, and the criterion compares against **the real glibc** (225 items,
byte-identical on glibc 2.43), not against Python.

**Java without Java** (`docs/282`, `docs/283`): a `.jar` is AOT-compiled at build time into a
portable Loment IR module, merged at link time, and the product is an ordinary ELF/PE - no JDK
at build, no JVM at run. It is a **closed world, static subset**: no reflection, no dynamic
loading. The class-file verifier (#1058), inheritance/closed-world dispatch, `tableswitch`/
`lookupswitch`, object arrays, `checkcast`/`instanceof`, boxed types and `<clinit>` are landed
(#1062 / PR #1148); the JNI path still exists in `hosted` mode for cases the AOT path does not
cover.

### 8.10 Network and threads

**`net_ladder`** (`docs/237`, Potato v14) counts syscall **sites** by the widest reach they
need:

| Rung | Meaning | Numbers |
|---|---|---|
| `w0` | verify the peer's bytes on the same machine | `socketpair` (53) |
| `w1` | one bounded connection, peer chosen by us | `socket`(41) `connect`(42) `sendto`(44) `recvfrom`(45) `sendmsg`(46) `recvmsg`(47) `shutdown`(48) `getsockname`(51) `getpeername`(52) `setsockopt`(54) `getsockopt`(55) |
| `w2` | many concurrent connections (multiplexing only) | `poll`(7) `select`(23) `epoll_wait`(232) `epoll_ctl`(233) `epoll_create1`(291) |
| `w3` | accept any peer | `bind`(49) `listen`(50) `accept`(43) `accept4`(288) |

The four rungs are always present and `w0+w1+w2+w3 == total_net == surfaces.net`. The width
itself (how many connections at once, how many bytes, how long) is not counted.

**Two measured network traps** are in section 6.2: SIGPIPE kills the process on the second
write after the peer's RST, and a blocking `read` on an accepted socket blocks forever unless
you set `SO_RCVTIMEO`. `syscall7` exists so `sendto`/`recvfrom` can carry their sixth argument
(`socklen_t`).

**Threads** (`docs/245`): `thread_spawn("entry", stack_top, arg, done)` starts a real Linux
thread. The entry is a **string literal naming a function in the unit**, so the product contains
a direct `call @entry` and the static call graph stays static. The product switches `rbp`/`rsp`
to the supplied stack before calling the entry (the shared frame is a structural race
otherwise). `done` is a `u32` the product **atomically increments and `futex_wake`s after the
entry returns** - the entry cannot do it itself, because returning exits the thread. **ELF
only**; PE refuses by name. Stage 1 does not include function pointers or indirect calls
(`docs/246` is that line).

### 8.11 Tools and editors

| Tool | What it does |
|---|---|
| `loment check` / `ir` / `build` / `run` | the driver; diagnostics from `lomenterr` (position, source line, caret, what/why/at least three fixes, supported/not supported) |
| `loment lsp` | language server over stdio (completion, definition, diagnostics, formatting); the packaged binary is built in the **hotswap** profile |
| `tools/loment_dap.py` | Debug Adapter Protocol + `ptrace` backend, launched by the VS Code extension so F5 works; it must run on a real Linux kernel (WSL2 yes, WSL1 no). It is **not** a `loment` subcommand |
| `loment fmt` / `doc` | formatter / API docs from `///` |
| `loment stat` / `fns` / `grep` / `hash` / `cat` / `tokens` / `todo` | source reading without an editor |
| `loment skill` / `cheat` / `syntax` / `builtins` / `codes` | the guide and the language reference |
| `loment lompi` | **not a subcommand** - run `lompi`; the package manager is a separate command |
| `lomenterr` | the reporter; the launcher starts it on failure. It is not a `loment` subcommand |
| `loment-lomelf` | the self-hosted linker; `--allow-ports` is needed when you call it directly instead of through `loment` |
| `loment-lvm` | the LVM interpreter (section 8.3) |
| VS Code extension | `hooyan2022.loment` - TextMate grammar + LSP; it is a **client**, it needs a toolchain checkout/install; `tools/vscode_ext.py --install` side-loads it |

**Boundary**: the packaged CLI's own `codes` page still prints "E1-E23" while the reporter knows
E001–E038. Trust the codes section of this guide (and `loment explain E34`), not that banner.

### 8.12 L0, the language standard, and packaging

- **`lom/*.lom` is the L0 interface contract, not an implementation detail.** It is the
  cross-line surface other components compile against (for example the content-trust
  primitives `qual_feed` / `qual_seq` and the admission gate `dom_admit`). It is generated into
  spec files and checked by `fuai_contract_check`. **Changing it is a contract change**: its own
  commit, naming who depends on it, what changed and whether downstream must move - never a
  drive-by edit during a compiler refactor.
- **`standard/LLS.md` is the language standard.** It is one "frame", not a second
  specification, and **its version is the language version - one number**. §4 is the **switch**
  table: Loment is defined by what it switches on, not by what it lacks (`no_gc` moved from
  "planned" to a core value; `no_runtime` is a guarantee the linker checks; `vm` is accepted but
  does not change the artifact yet, because the interpreter is a separate tool).
- **`lompi` is the package manager**, and it is a **separate command**, not `loment lompi`.
  A library is a directory, its identity is a content hash, and its dependencies are the `use`
  lines in its source. Read the lompi guide before touching dependencies.
- **Packaging**: `tools/loment_dist.py` builds the `bin/` + `share/` layout (including the
  self-hosted driver, linker, CLI, formatter, LSP, LVM and `lompi`). A release records a
  `release-manifest.json` and `SHA256SUMS`. Signing is two things (`docs/163`): Authenticode on
  the Windows executable, and a detached signature over `SHA256SUMS` for the archive; the key
  lives only in the CI secret, and self-signed certificates still trigger SmartScreen.
  `loment_publish.py --check` is the gate that the published file set is exactly the tracked set.

### 8.13 What is not there (so you do not design around it)

- **No floats** (`f64` exists only as a library over integers), no `char`, no tuples, no
  closures, no function pointers/indirect calls, no `impl Trait`, no `dyn`, no exceptions.
- **No block scope for `let`** today (a branch-local `let` is visible after the branch).
- **No lifecycle annotations** (`&T` in type position) - Tur 2026.3 is a design.
- **No `defer`** - designed for Tur 2026.2 but not implemented.
- **No moves/borrow checker in the Rust sense**: E006/E007/E012 cover a small ownership
  subset; `ptr` is Copy and unchecked.
- **No runtime type information, reflection, GC compaction, weak references, or finalizers.**
- **No PE-side FFI**, no PE threads, no PE `proc` bridge.
- **No parallel language construct**: `par` is a design (`docs/285`); the compiler is
  single-threaded.
- **No third-party debugger/LLDB path**: the DAP backend is ptrace-based and self-contained
  because this machine has no gdb/lldb.

## 9. When you do not have the source repository

- **Copy a working program**: the package's `share/loment/examples/` has `tour.lomt` (the program
  in section 1) and `user_hello.lomt`; more examples are in the repository's `loment/examples/`.
- **Use the compiler as an oracle**: `loment check` → fix → `loment ir` to see the LLVM IR →
  `loment run`. That loop is enough to make progress with no documentation at all, and when you
  are stuck, **read the IR first** - it carries far more than the error code does.
- **`loment doc FILE`** prints an API summary of the file in front of you, which is how you check
  an interface you just wrote.
- **`loment skill --print`** is this guide - on a machine with no Claude/Codex it is the only way
  in, and it depends on no filesystem convention (if you can run the CLI, you can read it).
- **This guide is also written for other agents**: installing Loment writes it into Claude's
  user-level skill directory, writes a **marked pointer** into Codex's `~/.codex/AGENTS.md`, and
  sets `LOMENT_SKILL` to the packaged copy. There is exactly one original - do not copy it
  somewhere else and edit that.

## 10. These paths exist **only in the source repository** (do not go looking without one)

The repository is FujoOS's Loment line (`loment/`, `lom/`, `tools/loment*.py`,
`docs/14?–16?-loment-*.md`). With a checkout you also get:

- **To actually manage libraries, use `lompi`** (section 0) - it is **installed** and needs no
  repository. The Python tools below are the other set, the older model of `docs/168` (the
  `materialize` path). The two are not the same thing; do not mix them.

- `python tools/loment.py lib ...` - **the library system** (`docs/168`): `tree` (dependency tree
  plus each library's instance count), `id` (instance identity), `cap` (capability-requirement
  closure with provenance), `check` (conflicts), `materialize` (flatten nesting and multiple
  versions into a tree the compiler eats directly). **Writing a library teaches you nothing new**:
  a library is a directory; dependencies are the `use` lines in its source (declared nowhere
  else); exports are `pub`; and the optional `pkg.lomp` carries two labels only -
  `pub fn name() -> str` and `pub fn version() -> str` (functions rather than `const`, because the
  language has no string constants);
- `python tools/loment.py diag FILE` - translates error codes into Chinese repair advice (not in
  the package);
- `python tools/loment.py err 诊断.jsonl` - **the reporter itself**
  (`loment/tools/lomenterr.lomt`): it renders the structured diagnostics the compiler writes with
  `--diag-out PATH` into the titled / source-line / caret / suggestion form. **The package has it
  too**, but there the launcher starts it automatically (when `loment check/build/run` fails)
  rather than exposing a `loment err` subcommand - so this is also its development entry point.
  See `docs/182` section 6;
- every example under `loment/examples/` (`tour.lomt` is the whole-language walkthrough);
  `loment/selfhost/` is "the Loment compiler written in Loment";
- documentation: `docs/143` (language spec) · `docs/146` (capability-domain formal semantics) ·
  `docs/148` (toolchain) · `docs/158` (the frozen surface, including what changing the language
  costs) · `docs/145` (milestones) · `docs/154` (status matrix);
- the development compile path: `python tools/loment.py ir F.lomt > f.ll` plus
  `python tools/lomelf.py f.ll --target pe -o f.exe` (**the package has no Python, so this does
  not apply there**).

**Repository rules** (they apply only with a checkout): `lom/*.lom` is a **cross-line interface
contract** - changing it takes a commit of its own and telling the compat line; do not change it
in passing during a refactor. `.lomt` is yours to change.
