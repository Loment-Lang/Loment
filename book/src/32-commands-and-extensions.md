# Commands and source extensions

Two openings let a Loment program plug into the toolchain: it can become a `loment` subcommand,
and a project can declare its own source extension. Both are conventions around a build, and both
are small enough to describe in one chapter.

## A `loment` subcommand

`loment foo …` looks for an executable named **`loment-foo` on `PATH`** and forwards the
arguments untouched. The exit code comes back unchanged. There is no registry and no
configuration file; the filename is the protocol.

Official commands (`version`, `build`, `run`, …) always win. A program cannot shadow them.

## A command written in Loment

A command does not define `_start`. It declares its name and provides an entry with two
parameters:

```rust
module cmd_hello

pub fn loment_command() -> str {
    return "cmd-hello";
}

pub fn command_main(argv: ptr, argc: u32) -> u32 {
    print("hello from a command\n");
    return argc;
}
```

Build it as `loment-cmd-hello`, put it on `PATH`, and `loment cmd-hello a b` runs it with
`argc == 3` (the program name plus two arguments). The toolchain generates the process entry,
reads `/proc/self/cmdline`, and calls `command_main`.

`argv` points at the raw command line: NUL-separated strings. A program that needs its arguments
walks that buffer:

```rust
def print_arg(argv: ptr, index: u32) {
    let p: ptr = argv;
    let i: u32 = 0;
    while i < index {
        let n: u32 = 0;
        while load8(p, n) != 0 {
            n = n + 1;
        }
        p = ptr_add(p, n + 1);
        i = i + 1;
    }
    let len: u32 = 0;
    while load8(p, len) != 0 {
        len = len + 1;
    }
    syscall4(1, 1, p as u64, len as u64);
}
```

The walk scans for NUL bytes, so it assumes each argument is a valid string and that `argv` ends
with a NUL. Both hold for `/proc/self/cmdline` on Linux. The PE runtime synthesises the same
buffer, so a command reads its arguments the same way on both targets.

Two rules:

- A unit is either a normal program (`main` / `_start`) or a command (`command_main`). Having both
  entries is E025; a command without its declaration is E026.
- A command may declare its own `loment-<name>` file; the build emits that name from
  `loment_command()`.

## A source extension

A project can declare an extension other than `.lomt`. Put `loment.conf` in the project root:

```rust
module conf

pub fn source_ext() -> str {
    return ".foo";
}
```

Now `use geom` looks for `deps/geom/geom.foo`. The path form (`use "area.foo"`) always carries its
own extension, so a file can mix extensions when it has to.

A broken configuration file falls back to `.lomt` rather than failing the build. That choice keeps
a missing or malformed `loment.conf` from turning into a mysterious "module not found"; the
diagnostic reports the fallback.

## Building a command

The build produces an executable named from `loment_command()`:

```console
$ loment build cmd_hello.lomt
$ ls loment-cmd-hello
```

Place the binary on `PATH`, or in the same directory as the `loment` launcher, and the
subcommand is available. The launcher does not cache anything; it resolves the name on every
call.

## When a command is the right shape

Use a command when:

- the program is a tool that operates on files or on the project;
- the output should follow `loment`'s conventions;
- the program benefits from `loment <name>` discovery.

Use a normal program when the artifact is the product. A command is a product plus a naming
convention, and the convention is the only difference.

## Boundaries

- A command is a normal executable; it can do anything a program can do.
- The argument protocol is byte strings, not structured values. Parse them yourself.
- The command name must be unique on `PATH`; the first `loment-<name>` wins.
- A command that needs the runtime block declares it like any other program.

The next chapter crosses the language boundary in the other direction: calling code written in C.
