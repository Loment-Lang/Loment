# Commands, extensions and FFI

Two openings make Loment a base other things grow on: your own `loment` subcommands, and your own
source extension. A third lets a Loment program call code written in another language.

## Your own `loment` command

`loment foo …` looks for an executable named **`loment-foo` on `PATH`** and forwards the arguments
untouched; the exit code comes back as it was. That is the whole protocol — a filename convention,
no registry, no configuration file. Official commands (`version`, `build`, `run`, …) always win.

A command written in Loment does not define `_start`. It declares its name and provides
`command_main`; the toolchain generates the entry point, reads `/proc/self/cmdline`, and calls you:

```rust
module cmd_hello

pub fn loment_command() -> str {
    return "cmd-hello";
}

pub fn command_main(argv: ptr, argc: u32) -> u32 {
    syscall4(1, 1, argv as u64, 4);
    return argc;
}
```

Build it as `loment-cmd-hello`, put it on `PATH`, and `loment cmd-hello a b` runs it with
`argc == 3`. `argv` points at the raw command line (`/proc/self/cmdline`), NUL-separated; the
project's tools parse it exactly that way.

## Your own source extension

Put `loment.conf` in the project root:

```rust
module conf

pub fn source_ext() -> str {
    return ".foo";
}
```

Now `use geom` looks for `deps/geom/geom.foo`. The path form (`use "area.foo"`) always carries its
own extension. A broken configuration file falls back to `.lomt` rather than failing the build.

## Calling code in another language

Two legs, chosen by what the other side is.

### C ABI (`extern fn` + `--link`)

Declare the signature; build the other side yourself; link the object:

```rust
module ffi_demo

extern fn c_add(a: i32, b: i32) -> i32;

fn _start() {
    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);
}
```

```console
$ cc -c -O1 -ffreestanding -fno-pic -fno-stack-protector lib.c -o lib.o
$ loment build app.lomt --link lib.o -o app
```

Boundaries that matter:

- `extern fn` parameters are **scalars and `ptr` only**. `str` is a pointer plus a length, not a C
  string; assemble a NUL-terminated buffer yourself when the other side expects one.
- An object with **undefined symbols** (`printf`, `malloc`) is refused in a release build. Compile
  the other side freestanding and let it call only itself.
- Dynamic libraries (`.so`, `.dll`) do not exist on this path yet; FFI is not available on the PE
  target.
- An object may not exceed 4 MiB.

### Interpreted runtimes (the process bridge)

```rust
use proc

// proc_sh("…") starts a shell; proc_python("…") starts a Python interpreter.
// You get its standard output back as text.
```

For Python, Java, JavaScript and the rest, the portable answer today is to start the interpreter
as a process and read its output. It is a bridge, not an embedding.

## Threads

`thread_spawn("entry", stack_top, arg, done)` starts a real Linux thread (ELF target only) whose
entry is a function in the same unit taking one parameter. You supply the stack; `done` can be an
address the product increments and wakes after the thread returns. This is deliberately small:
there is no thread pool and no async runtime in the language.
