---
name: loment
description: Read this before writing a program in Loment - FujoOS's own systems language (Rust-flavoured surface syntax plus capability domains; six more surface grammars - C, C++, Java, C#, Go, Python - share its semantics, see docs/188). With the toolchain installed you can write, check and compile native executables with no Python runtime. Use it for: writing a Loment program or a .lomt file; **writing a Loment library** (a library is a directory, dependencies are the `use` lines in the source, exports are `pub`, optional `pkg.lomp` manifest, see section 9); managing or debugging dependencies; compiling or running with the `loment` command; reading the E001-E027 error codes; looking up a builtin or a piece of syntax (`loment builtins` / `loment syntax` / `loment cheat`); expressing a piece of logic in the project's own language; how to write struct / enum / match / capability / guard; **giving a project its own source extension** (`source_ext` in `loment.conf`, section 7.1); **registering a custom `loment` subcommand** (`loment foo` runs `loment-foo` from PATH, section 7.2); and what differs when migrating from another language. **For anything about libraries and dependencies read the lompi guide first** (`~/.claude/skills/lompi/SKILL.md`, or `<prefix>/share/lompi/skill/SKILL.md` when you only have the package) - installing a library, resolving dependencies, which libraries exist on this machine, and what a `use <name>` resolves to all live there. Also use this whenever the task is to author, read or debug Loment source (.lomt / .lom / .lomp) or a Loment library with the toolchain installed.
---

# Loment: the language you write programs in

**Loment is FujoOS's own systems language.** Its **surface syntax is Rust-flavoured** (`module` /
`use` / `fn` / `let` / `if` / `while` / `for` / `match` / `enum` / `struct` / `trait` / the type
names / the operators all match Rust) and it adds exactly one layer of semantics: **capability
domains** (`capability` + `guard`). It compiles straight to a **native x86-64 executable**
(Linux ELF / Windows PE) with **no Python runtime**.

**The surface syntax is not the only one** (`docs/188`): the same program can be written with C /
C++ / Java / C# / Go / Python spelling, **changing the spelling and nothing else**. It is
recognised two ways - **by extension** (`.c` / `.cpp` / `.java` / `.cs` / `.py` / `.go`), or with
an explicit declaration at the top of the file:

```rust
choose write grammar python     // must come before `module`; once per file
```

> **The two toolchains accept different sets today - read this before you start.**
> **The reference compiler in the source repository** (`tools/lomentc.py`, which is also what
> `loment ir` / `build` use when you run them from a checkout) **accepts** both spellings: it
> translates the file into Loment **in process** and compiles that, with no external compiler
> involved. **An installed package (the self-hosted toolchain) accepts the `loment` spelling
> only**: anything else is refused outright ("这份源的 `choose write grammar` 自举侧收不了…" /
> *the self-hosted side cannot take this file's `choose write grammar`*), because the six
> translators have no Loment twin yet (`docs/189` section 4.1). So "write `.py` inside the
> package and it runs" **is not true today**.

The alias table is **frozen at the factory** (16 spellings to 8 canonical names; it cannot be
extended or overridden, `docs/188` section 1.1). Getting it wrong is one of four errors: the name
is not in the table / it is written after `module` / it is written twice / no name follows it.

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

You get something like `Loment 0.1.4 (0.1.4), commit <short>`. **The commit is what
counts**: different checkouts can do different things (this file describes the 0.1.4
generation; older packages print `0.1.4 Alpha` / `0.1.4 Alpha2.3` / `0.1.4 Pre2`).

**There are 38 commands; `loment help` lists all of them** (grouped, aligned, coloured) and
`loment help <command>` documents one. The ones used most:

| Command | What it does |
|---|---|
| `loment check FILE` | Check only, produce nothing (diagnostics on stderr - rendered by the reporter `lomenterr`: **position + source line + caret + what went wrong + why + how to fix (at least 3 ways) + supported/not supported**; when the file is not Loment at all it also offers the three ways to `translate it in`. See section 9) |
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
| `loment codes` / `loment explain E4` | The E001-E027 table / one code in detail |
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
  the code and position, plus `suggestions` an editor can **apply directly** (`replacement` with
  `byte_start`/`byte_end`), or count by code.

**Two switches in the launcher's help do not work today**: `--max N` and `--diag-out PATH`.
`--max N` currently prints **no diagnostics at all** rather than capping them, and `--diag-out`
does not write its file. They are known defects, not features you are using wrong.

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

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

`loment run hello.lomt` prints `hello from Loment`. It already settles three things: **the entry
point is `_start`** (not `main`, and it takes no arguments), **`let` needs a type**, and **there
is no printf** (output goes through `syscall4`, with `str_ptr` / `str_len` turning a string into
a raw pointer and a length).

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
> **The `hello` skeleton above uses scalars, a string and syscalls only, so it runs inside the
> package** - use it to confirm your environment before going further.

**Walkthrough** (in the order things appear):

| Fragment | What it is |
|---|---|
| `const LIMIT: u32 = 3;` | Module-level constant; usable directly in a function body |
| `capability slots : disk[0..4] revocable` | A capability domain declaration (section 5) |
| `struct Entry { … }` | A struct; every field carries a type |
| `enum Kind { Small, Big(u32) }` | An enum; a variant may carry **one** payload |
| `fn write_str(fd: u64, s: str) -> i64` | A function; **every parameter needs a type**, returns are written `-> T`, no return type means none |
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
| `fn _start()` | The **freestanding entry point**: no `main`, and no arguments. **It must not `return`** - the stack holds argc/argv rather than a return address, so a `ret` jumps into garbage (symptom: segfault, not one byte printed). End it with `syscall4(60, 0, 0, 0)`, never with `return 0` |
| `let xs: [u32; 3] = [1, 2, 3]` | A fixed-size array; `xs[0]` reads and writes |
| `for i in 0..LIMIT { … }` | A range loop (upper bound **exclusive**) |
| `while i < n { … }` | A loop |
| `if a > b && c == 3 { … }` | The condition must be `bool`; `&&`/`\|\|` short-circuit |
| `syscall4(60, 0, 0, 0)` | `exit(0)` - a program exits through a syscall |

## 2. Syntax cheat sheet

| Construct | How it is written |
|---|---|
| Module | `module <name>` on the first line (no semicolon) |
| Project mode | `choose` - **five dimensions**, each written at most once and **only in the entry unit**; libraries may not write it (E22). `std` / `no_std` · `gc_manual` / `gc_auto` / `gc_auto_alpha` · `runtime` / `no_runtime` · `sealed` / `hosted` · `vm` / `no_vm` - see 2.1 |
| Switch setting | `addin <name>` (**entry unit only**) - pulls in a "switch settings" unit, see 2.2 |
| Custom syntax | `comefor let "word" to { … }` … `byuse "word" done` - **define a new syntax at compile time**. The block after `to` is **a Loment program** (`fn main` is its entry; **the return value is how many tokens it ate**) which reads and emits tokens with `ct_n` / `ct_tok` / `ct_out` / `ct_syn`. Definition and terminator are **top-level only** and cannot nest; expansion happens at the token layer **before parsing**, so errors and jumps still point at the line you wrote. See `docs/184` |
| Import | **Two spellings, neither takes a semicolon**: `use name` (**searched in layers, first hit wins**: (1) project root `<project>/deps/<name>/<name><ext>` (2) the toolchain's own `<tool dir>/../share/lompi/store/<name>/<version>/<name><ext>` (3) the four built-in roots `loment/lib` → `examples` → `selfhost` → `tools`, where **only layer 3 requires the name to be unique**); `use "path/to/other.lomt"` (relative to the current file or the working directory - **use this form for a sibling file in your own directory**). `<ext>` defaults to `.lomt` and a project can change it (section 7). At most **300** `use` lines in one file. **At most one name-form `use` per unit today** - see 6.3 |
| Function | `fn f(a: u32, b: str) -> u32 { ... }` (no return value: `fn f()`); **at most 10 parameters** (a hard constraint of the self-hosted side's layout: the parameter table has 10 slots. It fails in two shapes: a count mismatch between parameters and arguments is the normal E3, while **a call site with more than 10 arguments** goes through `panic` - exit code 132 with **no diagnostic**. When you see "no error but exit 132", count your arguments) |
| Export | Add `pub` for cross-module visibility: `pub fn` / `pub struct` / `pub const` |
| Constant | `const NAME: u32 = 3` |
| Variable | `let x: u32 = e;`, assignment `x = e;` - **the type annotation is required**; the language has no type inference |
| Control flow | `if` / `else if` / `else`, `while`, `for i in lo..hi`, `return <expr>;` (the value is **required**) |
| Struct | `struct S { a: u32, b: u32 }`; literal `S { a: 1, b: 2 }`; field `s.a` |
| Enum | `enum E { A, B(u32) }`; construction `E::B(3)`; no payload `E::A` |
| `match` | `match x { E::A => { … } E::B(v) => { … } _ => { … } }` - **arm bodies are blocks**; exhaustive (or use `_`) |
| `if let` | `if let E::B(v) = x { … } else { … }` |
| `Option`/`Result` | Predefined generic enums, with `?` propagation |
| Array | Type `[u8; 16]`; literal `[1, 2, 3]`; index `xs[0]` (assignment `xs[0] = 1` works) |
| Slice | Parameter types `[T]` / `mut [T]`, pass `&xs` / `&mut xs`, `slice_len(s)` for the length |
| Generics | `fn max_of<T>(a: T, b: T) -> T` (monomorphised at compile time) |
| trait | `trait M { fn m(self) -> u32; }` + `impl M for S { … }`, static dispatch `obj.m()` |
| Types | `u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str`, a struct name, `[T; N]`, `[T]`, `mut [T]` |
| Conversion | `x as u64` - integer/pointer conversions are always written out |
| Strings | `"..."`, escapes `\n \t \" \\` - **and nothing else: read 6.2 before you need a carriage return**; the operations are the `str_*` builtins in section 3 |
| Comments | `//`, `/* */`; `///` is a doc comment (`loment doc` extracts those) |
| Operators | Precedence as in Rust: `\|\| && == != < <= > >= \| ^ & << >> + - * / %`, unary `- !` |

### 2.1 Project mode `choose`

`choose` is how a program states a property of **the whole artifact**. There are **five
dimensions**, each written at most once, and each one lands in the Potato form object - so
"what kind of artifact is this" is answerable on the side that does **not** read the source.

```rust
module myapp

choose no_std          // ← once for the whole program; the default is std
```

| Dimension | Values | Default | What it decides |
|---|---|---|---|
| **Layer** | `std` · `no_std` | `std` | which layer the program may use |
| **Reclamation** | `gc_manual` · `gc_auto` · `gc_auto_alpha` | `gc_manual` | how memory is reclaimed - see section 3.2 |
| **Runtime** | `runtime` · `no_runtime` | `no_runtime` | whether the artifact may carry a runtime |
| **Port** | `sealed` · `hosted` | `sealed` | whether the artifact may reach outside itself by linking host libraries |
| **Execution model** | `vm` · `no_vm` | `no_vm` | which execution model the program is for |

What holds for all five:

- **It is a property of the program, not of a file**: each dimension appears at most once, and
  **only in the entry unit** (a library pulled in by `use` that writes one is E22). Both compilers
  enforce this.
- **A default never changes what an existing program does** - which is why every default is the
  behaviour that was there before the dimension existed.
- **Contradictory pairs are named, not lumped.** `gc_auto` with `no_runtime` is refused because
  automatic reclamation *is* that runtime; `hosted` with `no_std` is refused because `no_std` says
  there is nothing underneath and `hosted` says to link against it. The diagnostic names the pair
  and the reason; it never says "illegal combination".
- A value that is not in the table is a typo, and a typo **is E22**.

**`vm` is the fifth, and it does nothing yet.** It answers *which execution model is this program
for?*, and like every other dimension the value says **whether**, never **which** - a name that
encoded today's tier would have to be renamed in order to add tomorrow's. **In a release build
`choose vm` is a compile error**; only the development channel accepts it, so an installed
toolchain can never accept a promise it cannot keep. What that channel produces is
**byte-identical to `no_vm`** - there is no bytecode and no interpreter yet (`docs/231`,
`docs/232`).

**`no_std` is what Loment actually is** (it was written for FujoOS in the first place). `std` is
the default and means you may use host capabilities. **When it is required**: writing systems,
writing something that runs on bare metal - writing it out is for readers and tools; the
language does not force it.

**`sealed` is what makes the artifact checkable.** An undefined symbol is an error, not a
warning, so a sealed artifact provably has no ports to the world. `hosted` is the other path: a
C-runtime program whose entry point is `fn main() -> u32` rather than `_start`, declared as
allowed to link real libc and shared libraries. **It is not finished, and a release build
refuses it by name** - `choose hosted` is a compile error there, the launcher refuses `--hosted`
too, and an object that carries undefined symbols is refused by the linker
(`docs/222` section 4.5, `docs/219` section 8c). The development channel is where it is built.

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
| `alloc(n: u32) -> ptr` | Bump-heap allocation (the same 64 KiB cap - **and read 6.2.14 before relying on it**) |
| `free(p: ptr) -> u32` | A placeholder (the bump heap does not really reclaim) |
| `load8(p: ptr, off: u32) -> u32` | Read a byte |
| `store8(p: ptr, off: u32, v: u8) -> u32` | Write a byte |
| `ptr_add(p: ptr, n: u32) -> ptr` / `ptr_sub` | Pointer offset |
| `slice_len(s: [T]) -> u32` | Slice length |
| `panic(code: u32) -> u32` | Does not return (the type is a placeholder) |
| `atomic_add(p: ptr, n: u32) -> u32` | Atomic add |
| `get_bits(v: u8, hi: u32, lo: u32) -> u8` / `set_bits(v: u8, hi: u32, lo: u32, x: u8) -> u8` | Bitfield read/write |
| `inb(port: u16) -> u32` / `outb(port: u16, v: u8) -> u32` | Port I/O (**only on the Rust translation path**, not the native one) |
| `syscall4(nr: u64, a0: u64, a1: u64, a2: u64) -> i64` | A raw syscall (rax/rdi/rsi/rdx) |
| `syscall6(nr: u64, a0..a4: u64) -> i64` | The same, with two more arguments |
| `syscall7(nr: u64, a0..a5: u64) -> i64` | The same, with **all six** argument registers used (`rdi/rsi/rdx/r10/r8/r9`). This is what makes **UDP with addresses writable**: `sendto`(44) / `recvfrom`(45) take a sixth argument (a `socklen_t`), which had nowhere to go on `syscall6` |
| `thread_spawn("entry", stack_top: u64, arg: u64, done: u64) -> i64` | Start a **real thread** (Linux `clone`). The first argument is the **name** of a function written in this unit that takes exactly one parameter — it becomes a **direct call** in the product, so the call graph stays static. Returns the new thread's tid. You supply the stack (see `thread_stack` in `os/thread`). `done` is the address of a `u32` the product **atomically increments and `futex_wake`s after the entry returns**, before the thread exits (`0` = no notification): the entry itself cannot do this, because the product exits the thread the moment it returns. **ELF target only** (Windows has no `clone`) |

**To print a number**: copy `write_dec` from section 1. There is no `printf`.

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
| `std` | 127 **portable** modules (not one syscall): containers, text, big integers, floating point, hashing and checksums, compression, bit twiddling | `vec` `map` `set` `heap` `deque` `trie` `text` `utf8` `parse` `fmt` `bigint` `f64` `crc32` `deflate` `png` |
| `host` | The host side (needs syscalls): files, directories, argv, stat, logging | `fs` `io` `dir` `argv` `stat` `log` |

- **One name-form `use` per unit is the limit today** - a second one fails, with a message that
  names no file and no line (6.3.20). To pull in several modules, use the **path** form
  (`use "<prefix>/share/lompi/store/std/0.1.0/text.lomt"`), which has no such limit. **Take the
  modules one at a time; do not write a facade.**
- `use std` exists but drags all 127 modules into **one unit**, and measured today it takes
  **18 minutes and then dies with SIGILL**. Do not use it.
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

**Every code has a full card** (what went wrong / why / at least three fixes / supported and not
supported) which `loment check` renders; `loment explain E4` gives the ASCII version of one. The
table above is only an overview.

**The most common ones**: `E2` is nine times out of ten a **missing type annotation** (`let x =
1;` is not legal; write `let x: u32 = 1;`) or an undefined function name; `E3` is a call with the
wrong argument count; `E19` see 6.1.

## 5. Capability domains (the one new thing Loment module blk
choose runtime

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);   // out of range -> trap; in range -> audit count +1
    return slot;
}
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

## 6. The trap list (every entry was measured; **reading this first saves most of the rework**)

Rust intuition carries you through 90% (scalars / strings / slices / control flow / generics /
traits). **What is left is almost entirely in the part Loment invented.** 6.1 is language and
syntax, 6.2 is what the runtime does to you, 6.3 is what the toolchain cannot do today - the three
are different things, and it is worth knowing which one you are looking at.

### 6.1 Syntax and types

1. **A `match` arm body is a block, not an expression.** `Kind::A => 0,` is not accepted; write
   `Kind::A => { return 0; }`. (At this commit the *checker* no longer rejects the expression
   form, and what you get instead is a build failure from the linker - it stops on the value the
   codegen emits for it, not on the `match`. Both implementations refuse it; write the block.)
2. **Generic enum patterns use the monomorphised name.** When the matched value is
   `Result<i64, u32>`, `Result::Ok(v)` gives `E8 与主体枚举 Result_i64_u32 不符` (*does not match
   the subject enum*); the answer is **`Result_i64_u32::Ok(v)`**. Non-generic enums are written
   normally: `Kind::Big(w)`.
3. **There is no valueless `return;`.** `return;` gives `E19 期望表达式，得到 ';'` (*expected an
   expression, got ';'*). Either `return <expr>;` or finish with if/else.
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
   (section 9).
10. **`use` takes no semicolon** in the house style. `use parse;` is *accepted* today, but every
    file in this repository is written without it, and a semicolon there is noise a reader has to
    wonder about.
11. **`mut` in `let mut x: T = …` is not a keyword** - it is an ordinary identifier
    (`loment/selfhost/checker.lomt` really does have a variable called `mut`). `let mut len: u32 =
    …` therefore declares a variable named `mut` and then trips over a `len`, and the error points
    somewhere else. Write `let x: T = ...;`; reassignment needs no marker.
12. **Non-ASCII output is mojibake in a Windows console.** PE writes bytes **straight to the
    console** and Windows decodes them with the current code page - 936 (GBK) on a Chinese
    Windows - so UTF-8 Chinese comes out as `婧愮爜`. **The shim has no `WriteConsoleW`** and the
    program cannot fix it from its side. Anything a person will read goes out **in ASCII**; that
    is the only set that decodes the same under every code page. (`loment`'s own help and cheat
    pages were moved from Chinese to English for exactly this reason, `docs/169` section 3a.)

### 6.2 What the runtime does to you

13. **The string escape set is `\n \t \" \\` - and there is no escape for a carriage return.**
    Measured on a running program: a literal written `"\r"` compiles to **one byte, `0x72`, the
    letter `r`**; `"\0"` becomes `0x30`, the digit `0`; `"\x41"` becomes the three bytes `x41`;
    and an unrecognised escape such as `"\q"` silently loses its backslash and yields `q`.
    **Nothing warns you** - an unknown escape is not an error. The reference compiler emits the
    same bytes, so this is the language rather than one implementation.

    What it costs in practice: **you cannot write `\r\n`, the line ending of every text
    protocol.** An HTTP status line built from a literal comes out `HTTP/1.1 200 OKr\n`, and a
    search for `"\r\n\r\n"` - the end of HTTP headers - compiles to a search for
    `0x72 0x0A 0x72 0x0A` that never matches a real request, so the read loop runs to the end of
    the buffer instead of stopping at the headers. **Write protocol bytes one at a time**:
    `store8(p, o, 13 as u8); store8(p, o + 1, 10 as u8);`.

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

15. **A runtime index is not bounds-checked. A literal one is now a compile error.** The two
    halves have different answers, and it matters which one you are holding:
    - **Literal index, out of range, fixed-size array** - `xs[5]` on `[u32; 3]` - is
      **refused by `check` (E028)**, at compile time, in the default tier. (Before 2026-10-16
      it was accepted and read whatever was next in memory; the refusal used to be reachable
      only under `choose lms_open`, which does not ship. This is `docs/205` section 1.1 C5,
      which had only ever covered `guard` - see section 5.)
    - **Any other index** - a variable, an expression, or anything into a slice `[T]` - is
      **unchecked at runtime**: a little out of range writes elsewhere with no trap, and
      `xs[100000] = 1` **segfaults** the process.

    An index that comes from outside your program - a request, a file, a length field - is
    therefore a memory-safety question, and the language will not raise it for you. **Guard it
    yourself**: a fixed-size array has a compile-time length, so write `if i >= 3 { … }`; a
    slice carries its own length, so `slice_len(x)` gives it to you. (Automatic per-site inline
    bounds checks exist as `choose lms_open` in the root unit - it is a development-channel
    intermediate and does not ship, `docs/253` section 14.2.)

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
knowing costs half a day.

20. **A unit can carry only one name-form `use`.** The second one fails with
    `loment: cannot open <garbage>` where the "path" is a fragment of your own
    source - exit 1, no file name, no line. The path form `use "…/text.lomt"` has no such limit,
    so that is the workaround. A unit that carries imports also loses most of its string-literal
    capacity: measured, **eight ~34-byte literals** are enough to bring the compiler down with
    the same message, while a unit with no imports takes forty of them. The length of the store
    path matters too (five imports compile from a 90-character store root and fail at 91).
21. **About 400 levels of nested blocks crash the compiler** with SIGSEGV and print nothing (200
    levels are fine; the same file flattened into 400 sequential statements, which is bigger,
    compiles). A generated parser table or a long `if`/`else if` chain can reach that by accident.
22. **A `.lomt` file with no `module` declaration crashes the compiler driver** with SIGSEGV and
    no diagnostic. (A *missing* file, by contrast, gets a clear message - it is the broken unit
    that gets the silent crash.)
23. **`use std` takes 18 minutes and then dies.** See 3.1.

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
  **refused by default**. That door - real libc, real shared libraries - is unfinished, and a
  release build keeps it shut (`docs/222` section 4.5, `docs/219` section 8c). The development
  channel opens it explicitly (`--allow-ports`), and there the closed-off names it can supply
  itself are `memcpy` / `memset` / `memcmp` / `malloc` / `free` / `__stack_chk_fail`.
- so in a release build: compile the other side freestanding
  (`-ffreestanding -fno-stack-protector`) and let it call only itself.
- dynamic libraries (`.so`/`.dll`) **do not exist yet** on this path, and neither does FFI on
  the PE target. Reaching a real system library is what `choose hosted` opens (section 2.1) -
  and that dimension is itself **not available in a release build** right now.
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

## 8. When you do not have the source repository

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

## 9. These paths exist **only in the source repository** (do not go looking without one)

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
