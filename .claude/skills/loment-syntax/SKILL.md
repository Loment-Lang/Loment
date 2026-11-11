---
name: loment-syntax
description: Quick reference for Loment - the syntax cheat sheet, the complete builtin table (including Tur 2 print_num, syscall7 and thread_spawn), the standard-library entry points, and the E001-E038 error-code table with what each code asks you to do. Read when you need a signature, a construct, or to decode a diagnostic. Part of the LomentSkill suite; section numbers are global.
---

## 2. Syntax cheat sheet

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


| Construct | How it is written |
|---|---|
| Module | `module <name>` on the first line after the optional grammar declaration (no semicolon) |
| Grammar | `choose write grammar <name>` **before `module`**, once per file; no declaration means the **newest Tur edition** (today `tur2026.3`) - see `loment-tur` 2.0.3 |
| Entry point | `fn main()` (no parameters, no return type) is the normal entry; `fn _start()` is the freestanding entry and wins if both exist - see 2.0.12 |
| Project mode | `choose` - **seven dimensions**, each written at most once and **only in the entry unit**; libraries may not write it (E22). `std` / `no_std` · `gc_manual` / `gc_auto` / `gc_auto_alpha` / `no_gc` · `runtime` / `no_runtime` · `sealed` / `hosted` · `vm` / `no_vm` · `lms_full` / `lms_forensic_admin` / `lms_daily_admin` / `lms_manage_only_admin` / `lms_close_admin` · `lspe_open_admin` / `lspe_close_admin` - see 2.1 |
| Switch setting | `addin <name>` (**entry unit only**) - pulls in a "switch settings" unit, see 2.2 |
| Custom syntax | `comefor let "word" to { … }` … `byuse "word" done` - **define a new syntax at compile time**. The block after `to` is **a Loment program** (`fn main` is its entry; **the return value is how many tokens it ate**) which reads and emits tokens with `ct_n` / `ct_tok` / `ct_out` / `ct_syn`. Definition and terminator are **top-level only** and cannot nest; expansion happens at the token layer **before parsing**, so errors and jumps still point at the line you wrote. See `docs/184` |
| Import | **Two spellings, neither takes a semicolon**: `use name` resolves in this order - (1) `deps/<name>/<name><ext>` in the project; (2) **exactly one** module named `name` across the five built-in roots `loment/lib` → `examples` → `selfhost` → `tools` → `lspe` (several hits is E18 "ambiguous"); (3) **exactly one** module inside the packaged store `share/lompi/store/` (a module inside a package, e.g. `std/0.1.0/vec.lomt`; several hits is E18). An importer that lives **inside the store** flips (2) and (3). `use "path/to/other.lomt"` is relative to the file or the working directory - **use this form for a sibling file in your own directory**. `<ext>` defaults to `.lomt` and a project can change it (section 7). At most **300** `use` lines in one file. **Several name-form `use` lines are allowed in 0.1.5** (older packages stopped at one) - see 6.3 |
| Function | `fn f(a: u32, b: str) -> u32 { ... }` (no return value: `fn f()`); **at most 10 parameters** (a hard constraint of the self-hosted side's layout: the parameter table has 10 slots. It fails in two shapes: a count mismatch between parameters and arguments is the normal E3, while **a call site with more than 10 arguments** goes through `panic` - exit code 132 with **no diagnostic**. When you see "no error but exit 132", count your arguments) |
| Export | Add `pub` for cross-module visibility: `pub fn` / `pub struct` / `pub const` |
| Constant | `const NAME: u32 = 3` - the initialiser is **one integer literal** (decimal or hex, optional `-`); it is not an expression, and there are no string constants - see 6.1.8 |
| Variable | `let x: u32 = e;`, assignment `x = e;` - **the type annotation is required**; the language has no type inference |
| Control flow | `if` / `else if` / `else`, `while`, `for i in lo..hi` (upper bound exclusive), `return <expr>;` (the value is **required**). **Tur 2 adds** `for x in xs`, `break` / `continue`, bare `return;` in unit functions, and `defer f(a, b);` - see 2.0.4. **Tur 2026.3 adds** `for i, x in xs` (index copy + element copy), `for x in <call>` (array/slice return, evaluated once), and `let x: T = if c { a } else { b };` / `let x: T = match e { E::A => a, _ => b };` (expression forms in the initialiser only) - see 2.0.5 |
| Struct | `struct S { a: u32, b: u32 }`; literal `S { a: 1, b: 2 }` (2026.3: `S { a, b }` shorthand); field `s.a` |
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
| Audit bypass | `lspe pass "reason" { … }` - the reason is mandatory (E38), section 5.1.5 |
| Defer | `defer f(a, b);` (Tur 2) - a **call**, block-scoped, LIFO, arguments evaluated at the `defer`; not run on panic. See 2.0.4 |
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
| **Layer** | `std` · `no_std` | `std` | which layer the program may use - `std` unlocks the host-side modules (filesystem / argv / threads / time / network); `no_std` refuses them. Linking real libc or shared libraries is the separate **Port** dimension |
| **Reclamation** | `gc_manual` · `gc_auto` · `gc_auto_alpha` · `no_gc` | `gc_manual` | how memory is reclaimed - see `loment-safety` 8.1 and `docs/210`; `no_gc` declares the artifact carries no collector |
| **Runtime** | `runtime` · `no_runtime` | `no_runtime` | whether the artifact may carry the runtime block. `alloc`, `guard`, `print_num` and the automatic GC tiers need it; `no_runtime` refuses them **by name** (E031) |
| **Port** | `sealed` · `hosted` | `sealed` | whether the artifact may reach outside itself by linking host libraries |
| **Execution model** | `vm` · `no_vm` | `no_vm` | which execution model the program is for - see below |
| **Memory safety** | `lms_full` · `lms_forensic_admin` · `lms_daily_admin` · `lms_manage_only_admin` · `lms_close_admin` | `lms_full` | which of the seven LMS levels are on. **The direction is reversed**: the default is full, and a non-full value is a downgrade; every non-full value carries `_admin` |
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
  `no_std` are refused because a collector needs that runtime; `hosted` with `gc_auto_alpha` is
  refused because alpha's block-epoch L2 rolls the allocation frontier back, while a C library
  keeps pointers in its own structures outside the Loment stack. The diagnostic names the pair
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

**`sealed` and `hosted` are the two declarations of the port dimension.** `sealed` (the
default) says the product has no hosted runtime and links no host library: the launcher refuses
`-L` / `-l` / `--cc-arg` under it, and the linker's own default rejects objects with undefined
symbols and shared objects. **A boundary you can actually hit**: since 2026-10-17 both launchers
pass `--allow-ports` in **both** channels, so an explicit `--link` of a `.o` or `.so` is still
accepted under `sealed` - the dimension records intent, and the launcher enforces the `-L`/`-l`
path rather than every possible `--link`. A name nobody provides is a **link error**, not a
run-time crash. `hosted` is the other path: `fn main() -> u32` instead of `_start`, allowed to
link real libc and shared libraries (the door opened on 2026-10-17; `docs/183` V2, `docs/219`
section 8c). Remaining limits: ELF only (no PE import path), one shared object on the
self-hosted linker, function symbols only, and a host `ld.so`/glibc must be present. When you
call `loment-lomelf` directly, add `--allow-ports` yourself.

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
| `str_concat(a: str, b: str) -> str` | Concatenation - **allocates from the same 64 KiB arena at run time**, so do not build big strings (or loop over it) |
| `str_num(n: u32) -> str` | Unsigned decimal text (`4294967295` max) - **allocates from the same arena**, and it lives in the runtime block, so `no_runtime` refuses it by name (E31). `docs/337` |
| `str_ptr(s: str) -> ptr` | The data pointer (for feeding syscalls) |
| `print(s: str) -> u32` | Write the string's bytes to stdout (one `write` syscall) |
| `tur_edition() -> str` | The Tur edition this program is compiled as, e.g. `tur2026.2` (docs/296). The compiler also prints it on stderr at every check/build/run |
| `print_num(n) -> u32` | **Tur 2 only** (in Tur 1 the name is an ordinary identifier). Decimal, optional `-`, **no newline**, any integer type widened by sign to `i64`. It lives in the runtime block, so `no_runtime` refuses it by name (E31) |
| `alloc(n: u32) -> ptr` | Heap allocation from the sealed arena (64 KiB; bump pointer + address-ordered free list) - **read 6.2.14 before relying on it** |
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
`openat`=257…). **The Windows PE target implements 29 numbers** (`PE_SYSCALLS` in
`tools/lomelf.py`): the file/process eight - `read`(0), `write`(1), `close`(3), `brk`(12),
`exit`(60), `getdents64`(217), `openat`(257), `newfstatat`(262) - plus the networking family -
`poll`(7), `select`(23), `socket`(41), `connect`(42), `accept`(43), `accept4`(288),
`sendto`(44), `recvfrom`(45), `sendmsg`(46), `recvmsg`(47), `shutdown`(48), `bind`(49),
`listen`(50), `getsockname`(51), `getpeername`(52), `socketpair`(53), `setsockopt`(54),
`getsockopt`(55), `epoll_wait`(232), `epoll_ctl`(233), `epoll_create1`(291). **Any other number
returns `-ENOSYS` (-38)** - not `-1`, which would mean EPERM. `/proc/self/cmdline` is
synthesised by the runtime on PE, so reading argv is the same on both.

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
- `mem`, `num` and `sha256` exist in **both** places; `use mem` resolves the **toolchain core
  library** copy (`loment/lib/`), not the one inside `std`. `proc` and `json` are core-library
  only (`loment/lib/proc.lomt`, `loment/lib/json.lomt`).
- The `test_*` functions inside standard-library files are each module's self-check; they may be
  part of the source at no extra cost.

## 4. How to read the error codes

The **self-hosted driver** (what an installed package runs) prints lines shaped like:

```
E2 @13 line 4: 1
```

The **reference compiler** in a checkout prints `4: message` instead; the reporter turns
either into the same card. Read the first as: `E2` is **E002** in the unified numbering; `@13` is **the 13th token** (not a column);
`line 4` is the line; everything after the colon is that token's text. **The codes are grouped by
what you should do, not by wording**:

| Code | What it asks you to do |
|---|---|
| E1 | Types do not line up (including return / payload / builtin arguments) |
| E2 | **Something is not declared** (undeclared variable / undefined function / unknown struct or enum; a local used before its `let`). An unknown name in a **type position** is E30, not E2 |
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

`E0900` / `E0999` are internal ("wrote no diagnostic record" / "unclassified - please report");
they are not source errors.

**Every code has a full card** (what went wrong / why / at least three fixes / supported and not
supported) which `loment check` renders; `loment explain E4` gives the ASCII version of one. The
table above is only an overview.

**The most common ones**: a **missing type annotation** is a **parse error** (E19: expected
`:`, got `=` for `let x = 1;`), not E2 - write `let x: u32 = 1;`. `E2` is a name that is not
declared; `E3` is a call with the wrong argument count; `E19` see 6.1.

