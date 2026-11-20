# Modules, libraries and packages

A file is a **unit**, and a unit is one module. This chapter covers how units expose names, how
one unit imports another, what the toolchain's standard library provides, and how a package is
identified and installed.

## One file, one module

A unit starts with `module <name>` and is compiled as a whole. Names inside a unit are flat:
there are no nested namespaces. `pub` is what makes a name visible to another unit.

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

`pub` applies to `fn`, `struct`, `enum`, `const` and `trait`. A non-`pub` name is private to its
unit.

## Importing

Two spellings, neither takes a semicolon:

```rust
use "mathx.lomt"     // path form: relative to this file or the working directory
use mathx            // name form: searched
```

The name form resolves in this order:

1. `<project>/deps/<name>/<name><ext>` — where `lompi` materialises a dependency;
2. exactly one module named `name` across the toolchain's built-in roots
   (`loment/lib`, `examples`, `selfhost`, `tools`, `lspe`);
3. exactly one module inside the packaged store (`share/lompi/store/`).

Two hits in one layer is E018; the diagnostic asks you to rename or to use the path form. The
path form is the reliable choice for a sibling file you control.

A file may carry at most **300** `use` lines (E020). Several name-form imports are allowed in
0.1.5; take modules one at a time, because a large facade costs compile time.

## Using the standard library

The store ships modules for containers, text, numbers, hashing, compression and host I/O. They
are ordinary units: import one, call its `pub fn`s.

```rust,run
module usenum

choose runtime

use num

fn main() {
    let buf: ptr = alloc(16);
    let n: u32 = num_to_dec(buf, 2026);
    syscall4(1, 1, buf as u64, n as u64);
    print("\n");
}
```
<!-- expect: 2026 -->

`num_to_dec(buf, v)` writes the decimal form into `buf` and returns its length. The printing
helper from chapter 8 is the same idea; the library version is already written and tested.

Two families are worth knowing:

| Package | What it contains |
|---|---|
| `std` | portable modules: `vec`, `map`, `set`, `text`, `utf8`, `parse`, `fmt`, `bigint`, `f64`, hashing, compression |
| `host` | modules that need system calls: `fs`, `io`, `dir`, `argv`, `stat`, `log` |

`use std` exists and imports the whole facade. It is large; prefer one module at a time.
`mem`, `num` and `sha256` exist in both the core library and `std`; `proc` and `json` are
core-library only.

## Packages

A library distributed as a package is a **directory**. The optional manifest `pkg.lomp` is
itself a Loment unit with two well-known functions:

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

A dependency is the `use` line in the source, not a manifest entry. `lompi` reads the graph,
computes an identity per package, and materialises the closure into a project's `deps/`:

```console
$ lompi plan mathutil --into myproject          # what would be installed
$ lompi install mathutil --into myproject --apply
$ lompi resolve myproject/mathutil@1.0.0 > lompi.lock
$ lompi verify myproject/mathutil@1.0.0 lompi.lock
```

Three properties follow from the design:

- **Identity is content.** A package's identity is a hash over its source files and their
  dependencies. Two packages with the same name and version but different bytes have different
  identities.
- **The store is shared.** `lompi` keeps one copy per identity and links it into projects.
- **Version selection is by name unless a lock pins an identity.** The lock is what makes a build
  reproducible.

## A project layout

A small project looks like this:

```
myproject/
  main.lomt          module main; choose ...
  deps/
    mathutil/
      mathutil.lomt
      pkg.lomp
  lompi.lock
```

`main.lomt` writes `use mathutil`, and the name form finds `deps/mathutil/mathutil.lomt`. A
sibling file can use the path form instead.

## Rules of thumb

1. **Export only what a consumer needs.** A `pub` name is part of the unit's contract.
2. **Prefer the path form inside one project.** It does not depend on search order.
3. **Take standard-library modules one at a time.** The facade is for convenience, not for
   routine builds.
4. **Pin a lock for a reproducible build.** A name and version alone do not identify content.

The next chapter turns a program into a test.
