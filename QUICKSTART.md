# Quick start

Loment is a systems programming language: its own syntax is Rust-flavored, and it can also be
written in C, C++, Java, C#, Go or Python; it emits native x86-64 executables (Linux ELF,
Windows PE). By default the artifact is **sealed** — no runtime, no libc, no undefined symbols —
and `choose hosted` is the other path (not finished; a release build refuses it). The
[README](README.md) says what it is; this page is
only about getting a program to run.

## 0. Get it

**0.1.4 is packaged** — download it from
[Releases](https://github.com/Loment-Lang/loment/releases/tag/v0.1.4):

| Platform | File | How |
|---|---|---|
| Linux / WSL | `loment-0.1.4-linux-x64.tar.gz` | unpack → `sh install.sh` |
| Windows | `loment-0.1.4-windows-x64.zip` | unpack → `powershell -File install.ps1` |
| Windows | `loment-0.1.4-windows-x64-setup.exe` | **double-click** |

You get the `loment` command, `lompi`, and the language server. Compiling and running needs
**no Python, no clang and no WSL** — the package is native binaries plus the standard library,
and the installer checks `SHA256SUMS` before it writes anything. Then jump to
[§3](#3-with-loment-on-your-path).

**Or work from a checkout** — needed if you want to change the language itself:

```
$ git clone https://github.com/Loment-Lang/loment.git
$ cd loment
```

## 1. Write a program

`hello.lomt`:

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

`_start` is the entry point, because a sealed artifact has no runtime to call one for you.
There is no `printf` either: output goes through the `write` system call, whose arguments are
the file descriptor, the string's pointer and its length. The second call is `exit`.

## 2. Compile and run it

### With the repository's tools

The tools in `tools/` are Python, so this path needs Python 3 and nothing else:

```
$ python3 tools/loment.py ir hello.lomt > hello.ll     # source -> LLVM IR
$ python3 tools/lomelf.py hello.ll -o hello            # IR -> executable
$ ./hello
hello from Loment
```

On Windows the target is PE, so name the output `.exe` — the linker picks the format from
that name:

```
> python tools\lomelf.py hello.ll --target pe -o hello.exe
> hello.exe
hello from Loment
```

### Or build the toolchain itself

The compiler is written in Loment and rebuilds from a seed committed to this repository.
POSIX `sh` on x86-64 Linux — natively or under WSL — is the only requirement: no Python,
no interpreter and no C compiler.

```
$ sh loment/bootstrap.sh
...
SEED BOOTSTRAP OK: ...
```

It performs four checks: the seed builds a `stage1`, that `stage1` reproduces the seed byte
for byte, `stage2 -> stage3` reaches a fixed point, and `stage1` agrees with `stage2` on a
foreign entry file. Hand it an entry file and it prints that file's IR instead of running
the checks:

```
$ sh loment/bootstrap.sh hello.lomt > hello.ll
```

To get the packaged toolchain (the `loment` command, `lompi`, the language server) you can
either download it ([§0](#0-get-it)) or build the distribution yourself. Python is needed for
the build step and for nothing after it — the package contains no Python:

```
$ python tools/loment_dist.py --emit
$ cd loment/dist && tar xzf loment-*-linux-x64.tar.gz
$ cd loment-*-linux-x64 && sh install.sh          # Windows: powershell -File install.ps1
```

Details, prefixes and checksums: `docs/162-loment-distribution.md`.

## 3. With `loment` on your PATH

```
$ loment run hello.lomt          # compile, link, run
$ loment build hello.lomt -o hello
$ loment check hello.lomt        # check only, nothing written
$ loment ir hello.lomt           # the LLVM IR it generates
```

## 4. Look around the language

```
$ loment example tour      # the whole language in one file — const, capability, struct,
                           # enum, match, generics, slices, arrays, syscalls
$ loment cheat             # the traps, ordered by how easily they bite
$ loment builtins          # every built-in function there is
$ loment syntax            # syntax summary
$ loment help              # all 38 commands
```

The full guide is `.claude/skills/loment/SKILL.md`, and `loment skill --print` prints it.
It is written to be read start to finish before you write much code, and it is the same
document that ships inside the package.

## 5. Call a function written in C

Declare it with `extern fn` — a signature and a semicolon, no body — and the call site uses
the platform C ABI.

`add.c`:

```c
int c_add(int a, int b) {
    return a + b;
}
```

`app.lomt`:

```rust
module app

extern fn c_add(a: i32, b: i32) -> i32;

fn _start() {
    let n: i32 = c_add(20, 22);
    syscall4(60, n as u64, 0, 0);     // exit with the result
}
```

```
$ cc -c add.c -o add.o
$ python3 tools/loment.py ir app.lomt > app.ll
$ python3 tools/lomelf.py app.ll --link add.o -o app
$ ./app; echo $?
42
```

With an installed toolchain the middle two steps are one command:

```
$ loment build app.lomt --link add.o -o app
```

The object has to be self-contained: no undefined symbols, so no libc. C++ (`extern "C"`),
Rust (`#[no_mangle] pub extern "C"`) and Zig (`export fn`) work the same way.

**This leg is Linux/ELF only today.** A Windows build with a foreign object is refused
rather than producing something that does not link. Languages whose runtime is not a C
library — Python, JavaScript, Java, Perl, Lua, Go — are reached through a subprocess
instead (`loment/lib/proc.lomt`); that is Linux/ELF only as well. What this leg does *not* do is
reach a real system library. Dynamic libraries and libc are what `choose hosted` opens — and it
is **not available in a release build**: that path is unfinished, so the release toolchain
refuses `choose hosted`, and an object carrying undefined symbols is refused by the linker by
default. The development channel is where it is built; `docs/222-lompi-020-foreign-libs.md` is
the build-out. The honest ledger for where things stand
today is `docs/173-loment-ffi.md`.

## 6. Write it in the syntax you already know

Loment can be written in six other syntaxes — C, C++, Java, C#, Go and Python. Only the
spelling changes: the surface grammar says how a file is *read*, and the meaning is always
Loment's. So you do not have to learn a new language to write Loment.

Declaring it in the file is meant to be a line at the top, before `module`:

```
choose write grammar python
```

**That is not wired into the compiler yet** — today the six are reached through their
translators, and each has a criterion that settles it the only way it can be settled: run
the original, run the translation, compare the numbers.

```
$ python tools/ctrans.py sample.c --out sample.lomt        # C  -> Loment
$ python tools/potato_from.py Sample.go --json s.json      # Go -> form object
$ python tools/lomt_from.py s.json --impl --out s.lomt     #    -> Loment, with bodies
```

```
module Sample

pub fn level(n: i64) -> i64 {
    if (n < 10) { return 0; }
    ...
```

One translator per language — `ctrans.py`, `pytrans.py`, `jtrans.py`, `cstrans.py`,
`cpptrans.py`, `gotrans.py` — with one shared parser for the brace-and-semicolon family
(`trans_core.py`) and one dialect table each, because what really differs between C, C++,
Java and C# is small and worth writing down. What cannot be translated is reported with a
name and a reason; nothing is dropped silently.

Details: `docs/188-grammar-declaration.md`.

## 7. When it does not compile

```
$ loment check hello.lomt      # diagnostics on stderr, nothing written
$ loment explain E4            # one error code, in detail
$ loment codes                 # the whole table
```

When `check`, `build` or `run` fails, the launcher hands the compiler's structured
diagnostics to `lomenterr`, which renders them — the report you actually read:

```
error[E002]: undeclared name
  --> z.lomt:4
  |
4 |     return z;
  |     ^^^^^^^^^
  | message: 使用未声明的变量 z
  | what went wrong: A name was used without being declared - a variable, a function, ...
  | why: This is the one beginners hit most, and in Loment it is almost always the same ...
  | how to fix:
  |   1. Add the type annotation: `let x: u32 = 1;` ...
  |   2. Misspelt: fix the spelling. Cross-module: put `pub` on the declaration ...
  |   3. Generic functions need their arguments to pin down `T`: ...
  |   4. The name really does come from elsewhere: ...
  | supported: generics (when the call site determines T) / static trait dispatch / ...
  | not supported: type inference / implicit globals / forward references ...
```

(`...` marks where this page trims the prose. The real output spells each one out — the
card is meant to teach the rule, not just this one line.)

Three things about it. The prose is **English by default**, and colour is on by default
(`-C` turns it off). `lomenterr` is also a standalone command, so `lomenterr diag.jsonl`
renders a diagnostics file yourself if you want to. The other two exits are `--short` and
`--json`:

```
$ loment check z.lomt --short        # one grep-able line per diagnostic
z.lomt:4: error[E002]: 使用未声明的变量 z
$ loment check z.lomt --json         # one object per diagnostic, card included
{"code":"E002","title":"undeclared name","file":"z.lomt","line":4,"col":0, ...
```

`--json` is what an editor or a CI job wants; both modes turn colour off on their own. There is
also `--max N`, which caps how many are rendered (default 20, `--max 0` shows all) — a file with
300 errors otherwise buries the first one you need to read. When it does cap, it says so:

One line above deserves a note: `message:` is the **compiler's** text and is still
Chinese. The renderer's own text and the compiler's text are different layers
(`docs/182` §5.3), and only the first one is translated here. To read the whole report in
Chinese, drop an `errconfig` next to your sources:

```rust
// errconfig
module errconfig

pub fn error_lang() -> str {
    return "zh";
}
```

It is read from the project root (the directory you run `loment` in), falling back to one
beside the toolchain; anything it cannot parse counts as "no setting", so a broken file
gives you English rather than an error (`docs/182` §14).

One more number, and it is deliberately **not** the language's:

```
$ lomenterr version
lomenterr 0.1.2
```

The reporter changes on its own schedule — its wording, its exits, and what it offers to
suggest — so it carries a version of its own rather than riding on `loment version`. If you
report a rendering bug, that number is what the fix will be checked against (`docs/182` §17).

Compile-time only, today. Rendering a **trap** — a division by zero, a capability guard
going out of range — from a copy of the reporter linked into your program is designed but
not implemented; `docs/182-lomenterr-and-choose-switches.md` §10.4 says what it waits on.

Two traps are worth knowing before your first real program, because neither is a compile
error: a non-void function that falls off its end crashes at run time (the last statement
must be a `return`), and every `match` arm body must be a block — `E::A => { return 0; }`,
not `E::A => 0,`. `loment cheat` lists these first, in the order they bite.

## Where to go next

| | |
|---|---|
| [README](README.md) | What the language is, and the repository layout. |
| `.claude/skills/loment/SKILL.md` | The language guide. Read this one. |
| `docs/manual/` | The specifications, plus a page per example. |
| `loment/examples/` | 34 example programs; `loment example NAME` prints one. |
| `docs/158-loment-freeze.md` | What is frozen, and what changing it costs. |
| [Loment-Lang/lompi](https://github.com/Loment-Lang/lompi) | The package manager. |
| [Discord](https://discord.gg/rGw7NRNU) | Where to ask, and where to say what broke. |
