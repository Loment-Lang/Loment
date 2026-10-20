# Install and run

## The package (recommended)

Download the archive for your platform from the releases page and run the installer:

```console
# Linux / WSL
$ tar xzf loment-<version>-linux-x64.tar.gz
$ sh install.sh

# Windows (PowerShell)
> Expand-Archive loment-<version>-windows-x64.zip .
> powershell -File install.ps1
```

The installer verifies `SHA256SUMS`, then puts `loment`, `lompi`, the language server and the
`loment-*` tools under one prefix and adds `bin/` to your `PATH`. Nothing else is needed: no
Python, no clang, no WSL.

Check it:

```console
$ loment version
$ loment tools        # every shipped component, and which ones are missing
```

## From a checkout

If you want to change the language itself, work from the repository. The tools in `tools/` are
Python; this path needs Python 3 and nothing else:

```console
$ python3 tools/loment.py ir hello.lomt > hello.ll    # source -> IR
$ python3 tools/lomelf.py hello.ll -o hello           # IR -> executable
$ ./hello
```

The compiler is also written in Loment. `sh loment/bootstrap.sh` rebuilds it from the seed
committed to the repository, using the committed `genesis` binary and no C compiler.

## Your first program

Create `hello.lomt`:

```rust,run
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: hello from Loment -->

Run it:

```console
$ loment run hello.lomt
hello from Loment
```

Three things in that program are worth naming now, because they never go away:

**`_start` is the entry point.** Not `main`, not a runtime calling `main` for you. In a sealed
artifact there is no runtime at all. `_start` takes no arguments and **must not `return`** —
the stack at process entry holds `argc`/`argv`, not a return address, so a `ret` jumps into
garbage. End the program with the `exit` system call, as above.

**`let` needs a type.** `let s: str = …`, never `let s = …`. There is no type inference.

**Output is a system call.** There is no `printf`. `syscall4(1, fd, ptr, len)` is `write`;
`str_ptr` and `str_len` turn a string into a pointer and a byte length. `syscall4(60, code, 0, 0)`
is `exit`.

`loment run` compiles and executes in one step. `loment build hello.lomt` produces the artifact
(`hello.exe` on Windows, `hello` on Linux) and leaves it next to the source.
