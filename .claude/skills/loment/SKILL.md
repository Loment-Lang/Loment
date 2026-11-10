---
name: loment
description: Entry point of the LomentSkill suite for Loment (FujoOS's own systems language - Tur grammar plus capability domains, native ELF/PE, no Python runtime). Start here for the toolchain check, hello world, the whole-language tour, and the map of the suite. The deep material lives in sibling skills - `loment-syntax` (cheat sheet, builtins, E001-E038), `loment-tur` (editions and full grammar), `loment-traps` (measured traps), `loment-safety` (capabilities, LGC, LMS, LSPE), `loment-runtime` (LVM, Potato, hot update, optimizers, DGT), `loment-ffi` (C ABI, LibL, Java, network, threads), `loment-toolchain` (CLI, editors, L0/LLS, packaging, libraries). Use for writing, reading or debugging Loment source (.lomt / .lom / .lomp) with the toolchain installed. For libraries and dependencies read the lompi guide first (`~/.claude/skills/lompi/SKILL.md`, or `<prefix>/share/lompi/skill/SKILL.md`), because a library is a directory, its identity is a content hash, and its dependencies are the `use` lines in its source.
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
`command`, and the `print` builtin. Everything in this suite is Tur. The **full grammar
system** - editions, the declaration, every item/statement/expression/pattern, and the seven
foreign surface grammars - is the **`loment-tur`** skill (sections 2.0.x).

A file with **no declaration** (or `choose write grammar loment`) is read as the **newest
edition** - today **`tur2026.3`** (`docs/312`). Editions are **two-level**: `tur<year>.<n>`
(`docs/296`). The canonical name never changes meaning; `tur1` / `tur2` / `tur3` are the 2026
shorthands for `tur2026.1` / `tur2026.2` / `tur2026.3` and are only accepted in their release
year. To pin an **older** reading, say so explicitly: `choose write grammar tur2026.1` (or
`tur` / `tur1`) keeps Tur 1. **Every `check` / `build` / `run` / `ir` prints the resolved
edition on stderr** (e.g. `grammar default -> tur2026.3 (Tur 2026.3)`); `tur_edition()` returns
it at runtime, and `loment version <exe>` reads it back from the artifact.

**New syntax and old names collide - loudly.** A few words that are syntax in a newer edition
(`break`, `continue`, `defer`, `print_num`, `lgc`) were ordinary identifiers in an older one.
If an undeclared file **declares** one of them, the compiler refuses with **E040** instead of
silently changing what the file means: rename it, or pin the old reading with
`choose write grammar tur2026.1` / `tur2026.2` (`docs/312`).

**Tur 2026.2** (`tur2026.2`, or the 2026 shorthand `tur2`) adds the sugar (`def f()`, `a and
b` / `a or b` / `not a`, `elif cond { }`), `for x in xs`, `break` / `continue`, bare `return;`
in unit functions, `orelse` / `catch`, `print_num`, and **`defer`**. They are **Tur 2 and
later**: Tur 1 keeps those words as ordinary identifiers, so old files keep their meaning (and
`choose write grammar tur2026.1` pins that reading). Because the default is now the newest
edition, a new file gets all of them without a declaration. The details
and every restriction are in `loment-tur` 2.0.4 (the runnable versions are `loment_p8_test`
M91, M94, M95 and M96). **Tur 2026.2 is closed** (2026-11-05): that list is final; new
syntax goes to `tur2026.3` (`loment-tur` 2.0.3, `docs/305`).

**Tur 2026.3** (`tur2026.3`, shorthand `tur3`) inherits everything above. Its first feature is
**`lgc`**: manual GC timing control. `lgc start(10ms) { … }` opens a collection window with a
cooperative time budget; `lgc start { … }` opens one without a budget; `lgc end;` pauses the
collector and `lgc start;` resumes it. It works in every `choose gc` mode, but `no_gc` + `lgc` is
E039 (there is no collector to control). The budget gates *starting* a collection, not preempting
one already running; see `docs/309`.

Its second feature is **borrow lifetimes**: `&T` / `&mut T` are first-class types (parameters,
returns, struct fields, generic arguments), with region inference and **zero lifetime
annotations**. The dimension is `choose lifetime_no | lifetime_auto | lifetime_manual`:
`lifetime_auto` is the default from `tur2026.3` on (older editions default to `lifetime_no`,
so their artifacts do not change); `lifetime_manual` adds the borrow-source marker `&a T`
(`a` must be a reference parameter), and `lifetime_no` is today's shape (`&` in a type
position is a parse error). `*r` dereferences one borrow; `r.field` and `*r = v` auto-deref.
A borrow is a `ptr` in the artifact; slice borrows `&[T]` / `&mut [T]` normalise to the
existing `[T]` / `mut [T]` views, and a borrow may be a struct field (`Cursor { data: &[u32] }`
is a `{ ptr, i64 }` zero-copy view; `c.data[0]` and `slice_len(c.data)` work). A returned
borrow must come from a parameter, `free(p)` while `p` is borrowed is E041, and a **stored**
`&mut` excludes later borrows (E007) - conflicts are judged per place, so `v.x` and `v.y` are
separate. `Box<&u32>` instantiates as `Box_ref__u32`; composite generic arguments
(`Box<&T>` / `Box<[T]>`) are refused **by name** on the self-hosted side (E041). Both
implementations carry the dimension, Potato v22 and the region pass; see `docs/299` §8-§9.

Its third feature is **struct literal shorthand**: in 2026.3, `S { a, b }` means
`S { a: a, b: b }` (explicit and shorthand fields may be mixed; `docs/329`). It is a spelling
only - the artifact is identical to the explicit form.

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
> involved. **An installed package (the self-hosted toolchain) accepts the native spellings
> only** - `loment` and the Tur editions (`tur`, `tur1`, `tur2`, `tur3`, `tur2026.1`,
> `tur2026.2`, `tur2026.3`) -
> and refuses every foreign declaration outright ("这份源的 `choose write grammar` 自举侧收不了…"
> / *the self-hosted side cannot take this file's `choose write grammar`*), because the foreign
> translators have no Loment twin yet (`docs/189` section 4.1). So "write `.py` inside the
> package and it runs" **is not true today**.

The alias table is **frozen at the factory**: the native grammar (`loment`, `tur`, `tur1`,
`tur2`, `tur3`, `tur2026.1`, `tur2026.2`, `tur2026.3`) plus the foreign grammars (`c`, `cpp`, `java`, `csharp`, `go`,
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

> **The suite is self-contained; this file is the entry.** Section numbers are **global across
> the suite** - "section 6.2" or "section 8.3" means the same section whichever skill file you
> are reading. An installed toolchain puts every skill in `~/.claude/skills/`; the package keeps
> them beside this file as `loment-tur.md`, `loment-safety.md`, and so on.

| Skill | Sections | Read it when you need |
|---|---|---|
| **`loment`** (this) | 0-1, 7, 9, suite map | first contact, the tour, entry points, custom commands |
| **`loment-syntax`** | 2, 3, 4 | the cheat sheet, a builtin signature, an error code |
| **`loment-tur`** | 2.0 | grammar, editions, syntax details, surface spellings |
| **`loment-traps`** | 6 | measured traps before writing or debugging |
| **`loment-safety`** | 5, 5.1, 8.1-8.2 | capabilities, GC, LMS, LSPE |
| **`loment-runtime`** | 8.3-8.8 | LVM, Potato, hot update, optimizers, DGT |
| **`loment-ffi`** | 7.3, 8.9-8.10 | C ABI, LibL, Java, network, threads |
| **`loment-toolchain`** | 8.11-8.13, 10 | CLI, editors, L0/LLS, packaging, libraries, repo paths |

**Read the program in section 1 first, then come back to the reference skills.** It walks the whole
language in one file, and it **compiles and runs**; what is printed here is the same file as
`loment/examples/tour.lomt` in the repository (a copy ships in the package), so copying it is
safe.

## 0. Check which toolchain you have

```bash
loment version
```

You get two lines, for example:

```
Loment 0.1.5 (0.1.5)
commit edda7d4e (2026-11-04)
```

**The commit is what counts**: different checkouts can do different things (this suite
describes the 0.1.5 generation; older packages print `0.1.4`, `0.1.4 Alpha` / `0.1.4 Alpha2.3`
/ `0.1.4 Pre2`).

**There are 38 commands; `loment help` lists all of them** (grouped, aligned, coloured) and
`loment help <command>` documents one. The ones used most:

| Command | What it does |
|---|---|
| `loment check FILE` | Check only, produce nothing (diagnostics on stderr - rendered by the reporter `lomenterr`: **position + source line + caret + what went wrong + why + how to fix (at least 3 ways) + supported/not supported**; when the file is not Loment at all it also offers the three ways to `translate it in`. See `loment-tur` 2.0.14) |
| `loment ir FILE` | Print LLVM IR to stdout |
| `loment build FILE [-o OUT]` | Compile to an executable |
| `loment run FILE` | Compile and run |
| `loment fmt FILE` | Format (prints the result) |
| `loment doc FILE` | Generate API docs |
| `loment lsp` | Language server (LSP over stdio) |
| `loment skill [--print]` | Print the path to **this suite's entry** / its whole text - depends on no directory convention |

**With no copy of this suite to hand, the language reference is still one command away** (that is
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
  CI logs. The column is printed only when the compiler knows it; a semantic diagnostic that
  carries just a line comes out as `file:line: error[code]: message` - the reporter does not
  invent a `0` column that would make an editor jump to the file start;
- `--json` - **one object per line**, carrying `title`/`what`/`why`/`fixes`/`yes`/`no` as well as
  the code and position. When the reporter has an applicable fix, the object also carries a
  non-empty `suggestions` array an editor can **apply directly** (`replacement` with
  `byte_start`/`byte_end`). The key is **absent** when there is no suggestion - an empty array
  would read as "no fix available", which is a different claim.

Two more switches cap and capture the same stream:

- `--max N` - render at most `N` diagnostics (default **20**, `0` = all) and say how many were
  withheld;
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
> `deps/`). Every one of these questions is answered there, and **this suite does not cover
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

**The tour needs `choose runtime`.** `alloc` and `guard` both pull in the runtime block; under the
default `no_runtime` the compiler refuses them with E031. `no_runtime` is a different axis from
`no_std` - see `loment-syntax` 2.1.

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
| `tur_edition()` | Which Tur edition this source is read as (`tur2026.1` / `tur2026.2` / `tur2026.3`); the compiler also reports it on stderr |
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
| `lgc start(2ms) { … }` | **Tur 2026.3**: a GC window — collection is allowed inside, and the budget caps when a *new* collection may start. Also `lgc end;` / `lgc start;` to pause/resume, `lgc end { … }` for a scoped pause, `lgc start { … }` for an unbudgeted window |
| `fn main()` | The **normal entry point**: no arguments, no return type. The linker's entry stub calls it and exits 0 |
| `fn _start()` | The **freestanding entry point** (kernel / no-libc): no arguments. If both exist, `_start` wins. When you link it yourself with `-Wl,-e,_start`, end it with `syscall4(60, 0, 0, 0)` - there is no return address |
| `let xs: [u32; 3] = [1, 2, 3]` | A fixed-size array; `xs[0]` reads and writes |
| `for i in 0..LIMIT { … }` | A range loop (upper bound **exclusive**) |
| `while i < n { … }` | A loop |
| `if a > b && c == 3 { … }` | The condition must be `bool`; `&&`/`\|\|` short-circuit |
| `syscall4(60, 0, 0, 0)` | `exit(0)` - a program exits through a syscall |

## 2-4. Syntax, builtins, error codes

The cheat sheet, the complete builtin table and the E001-E038 code table are the subject of the
**`loment-syntax`** skill. Read it when you need a signature, a piece of syntax, or a code.

## 5. Capability domains and the safety stack

The capability layer, `gc` reclamation, the seven LMS levels and the LSPE audit ledger are the
subject of the **`loment-safety`** skill. In one line: `capability` declares a domain, `guard`
bounds the index (not the principal), `gc_*` chooses reclamation, `lms_*` chooses memory safety,
and `lspe` records why each allowance was granted.

## 6. The trap list

The measured traps are the subject of the **`loment-traps`** skill. Read it before writing a
nontrivial program; it is the part that saves the most rework.

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

FFI, the process bridge, LibL, Java AOT, network and threads are the subject of the
**`loment-ffi`** skill.

## 8. The subsystems

Split across three skills:

- **`loment-safety`** - LGC (8.1), LMS (8.2)
- **`loment-runtime`** - LVM (8.3), Potato (8.4), hot update (8.5), LomentFaster (8.6),
  Lomenter (8.7), DGT (8.8)
- **`loment-ffi`** - foreign libraries (8.9), network and threads (8.10)
- **`loment-toolchain`** - tools/editors (8.11), L0/LLS/packaging (8.12), what is not there
  (8.13), and the source-repository paths (10)

## 9. When you do not have the source repository

- **Copy a working program**: the package's `share/loment/examples/` has `tour.lomt` (the program
  in section 1) and `user_hello.lomt`; more examples are in the repository's `loment/examples/`.
- **Use the compiler as an oracle**: `loment check` → fix → `loment ir` to see the LLVM IR →
  `loment run`. That loop is enough to make progress with no documentation at all, and when you
  are stuck, **read the IR first** - it carries far more than the error code does.
- **`loment doc FILE`** prints an API summary of the file in front of you, which is how you check
  an interface you just wrote.
- **`loment skill --print`** prints this entry - on a machine with no Claude/Codex it is the way
  in, and it depends on no filesystem convention (if you can run the CLI, you can read it). The
  topic skills are `loment-*.md` **beside that file** in the package, and separate skill
  directories after installation.
- **This suite is also written for other agents**: installing Loment writes every skill into
  Claude's user-level skill directories, writes a **marked pointer** into Codex's
  `~/.codex/AGENTS.md`, and sets `LOMENT_SKILL` to the packaged entry. There is exactly one
  original per skill - do not copy one somewhere else and edit that.

