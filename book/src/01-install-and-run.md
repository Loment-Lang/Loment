# Install and run

There are two ways to get a working toolchain: install a release package, or build from a
checkout. The package is what most readers want. The checkout is what you need when you want to
change the compiler itself.

## From a release package

Download the archive for your platform from the releases page and run the installer:

```console
# Linux / WSL
$ tar xzf loment-<version>-linux-x64.tar.gz
$ sh install.sh

# Windows (PowerShell)
> Expand-Archive loment-<version>-windows-x64.zip .
> powershell -File install.ps1
```

The installer verifies `SHA256SUMS`, then places `loment`, `lompi`, the language server and the
`loment-*` tools under one prefix, and adds `bin/` to your `PATH`. The installed toolchain is the
self-hosted one: it needs no Python, no clang, and no WSL.

Check the installation:

```console
$ loment version
$ loment tools          # every shipped component, and which ones are missing
```

`loment tools` is the quickest way to find out why a command is missing. It prints one line per
component and marks the ones that are not on `PATH`.

## From a checkout

The repository's reference compiler is written in Python. This path needs Python 3 and nothing
else:

```console
$ python3 tools/loment.py ir hello.lomt > hello.ll    # source -> IR
$ python3 tools/lomelf.py hello.ll -o hello           # IR -> executable
$ ./hello
```

The compiler is also written in Loment. `sh loment/bootstrap.sh` rebuilds it from the seed
committed to the repository, using the committed `genesis` binary and no C compiler. That path is
for working on the compiler; the contribution book covers it in detail.

The two toolchains do not accept exactly the same inputs. The reference compiler in a checkout
translates foreign surface grammars in process. An installed package accepts the native Tur
spellings only and refuses a foreign `choose write grammar` declaration. Both accept every
program in this book.

## Your first program

Create `hello.lomt`:

```rust,run
module hello

fn main() {
    print("hello from Loment\n");
}
```
<!-- expect: hello from Loment -->

Run it:

```console
$ loment run hello.lomt
hello from Loment
```

`loment run` compiles the file, links it, runs the result, and passes the program's exit code
through. `loment build` stops after writing the executable, and `loment ir` stops after printing
LLVM IR.

## The entry point

A normal program provides:

```rust
fn main() {
    // no parameters, no return type
}
```

The linker's entry stub calls `main` and exits with status 0 when it returns. A program that
needs to control the process directly uses the freestanding entry:

```rust
fn _start() {
    // no parameters; must not return
}
```

`_start` wins when both exist. It runs at process entry, where the stack holds `argc` and `argv`
instead of a return address, so a `ret` would jump into garbage. End such a program with the exit
system call:

```rust,run
module freestanding

fn _start() {
    print("no runtime, no return address\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: no runtime, no return address -->

A `main` with parameters is refused by both linkers: the stub calls it with no arguments, so a
parameterised `main` would read register garbage. Arguments arrive through
`/proc/self/cmdline`; chapter 32 shows the pattern.

## The tools you will use while reading

The toolchain is also a reference. These commands read from the installed package, so they answer
for the version you actually have:

```console
$ loment check hello.lomt     # parse and type-check, no artifact
$ loment build hello.lomt     # write the executable
$ loment run hello.lomt       # build and run
$ loment ir hello.lomt        # print the LLVM IR
$ loment cheat                # the whole language on one page
$ loment builtins             # every builtin function
$ loment keywords             # the reserved words
$ loment explain E4           # what a diagnostic means, and what to change
$ loment example tour         # the annotated whole-language program
```

`loment explain` is worth remembering. An error code is a number until the toolchain tells you
what the compiler objected to and which way out exists.

## What the compiler prints

Every `check`, `build`, `run` and `ir` prints the resolved grammar edition on standard error:

```console
$ loment run hello.lomt
loment: grammar default -> tur2026.3 (Tur 2026.3)
hello from Loment
```

The notice confirms which reading of the source the compiler used. Chapter 5 explains how to
pin a different edition. The self-hosted driver accepts `--no-edition-notice` for tools that run
it as a subprocess.

## If something does not work

Three checks cover most first-run problems:

1. **`loment tools`** lists missing components.
2. **`loment version`** confirms which toolchain is first on `PATH`. Two installations can
   shadow each other; the one you run is the one `PATH` reaches first.
3. **`loment check`** separates a compile error from a link or run problem. If `check` passes
   and `run` fails, the issue is in linking or in the program's own behaviour.

The installer names the prefix it uses, and the package keeps its tools together. If a command
from this chapter is missing, start with `loment tools` rather than reinstalling.
