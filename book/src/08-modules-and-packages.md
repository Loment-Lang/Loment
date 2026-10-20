# Modules, libraries and packages

## One file is one unit

A unit starts with `module <name>` and is compiled as a whole. Names are flat: there are no
namespaces inside a unit. `pub` is what makes a name visible to another unit.

```rust
// mathx.lomt
module mathx

pub fn square(x: u32) -> u32 {
    return x * x;
}

fn helper() -> u32 {      // not pub: invisible outside this unit
    return 1;
}
```

## Importing

Two spellings, neither takes a semicolon:

```rust
use "mathx.lomt"     // path form: relative to this file or the working directory
use mathx            // name form: searched, first hit wins
```

The name form searches, in order:

1. `<project>/deps/<name>/<name>.lomt` — what `lompi` materialises;
2. the toolchain's own store (the standard library ships there);
3. the four built-in roots of a checkout: `loment/lib`, `examples`, `selfhost`, `tools`.

A file may carry at most **300** `use` lines. A facade that needs more is a signal to split it.

## Using the standard library

The store ships modules for memory, numbers, text, hashing, JSON, URLs and more. They are ordinary
units: `use` them and call their `pub fn`s.

```rust,run
module usenum

choose runtime

use num

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn _start() {
    let b: ptr = alloc(16);
    let n: u32 = num_to_dec(b, 2026);
    syscall4(1, 1, b as u64, n as u64);
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 2026 -->

`num_to_dec(buf, v)` writes the decimal form into `buf` and returns the length — the printing
helper from the earlier chapters, provided by the library instead of hand-written.

## Packages

A library is a **directory**. The optional manifest `pkg.lomp` is itself a Loment unit with two
well-known functions:

```rust
// pkg.lomp
module pkg

choose runtime

pub fn name() -> str {
    return "mathutil";
}

pub fn version() -> str {
    return "1.0.0";
}
```

A dependency is the `use` line in the source, not an entry in the manifest. `lompi` reads the
graph, computes an identity per package (a content hash over the source files and the edges),
and materialises the closure into a project's `deps/`:

```console
$ lompi plan mathutil --into myproject          # what would be installed
$ lompi install mathutil --into myproject --apply
$ lompi resolve myproject/mathutil@1.0.0 > lompi.lock
$ lompi verify myproject/mathutil@1.0.0 lompi.lock
```

Three consequences worth knowing:

- **Identity is content, not a version number.** Two packages with the same name and version but
  different bytes have different identities; the lock pins the identity.
- **The store is shared.** `lompi` keeps one copy per identity and links packages into projects by
  copying their files.
- **Version selection is "highest version" by name** when no lock is used. A lock is how you make a
  build reproducible.
