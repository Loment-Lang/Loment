# Calling C and other runtimes

A Loment program can call code compiled by another compiler. The bridge has two legs: the C ABI
through `extern fn` and a linked object, and the process bridge for interpreted runtimes that do
not expose a C ABI. This chapter covers both, plus the boundaries that decide which one is usable.

## `extern fn`

Declare the signature and build the other side separately:

```rust
module ffi_demo

extern fn c_add(a: i32, b: i32) -> i32;

fn main() {
    let r: i32 = c_add(3 as i32, 4 as i32);
    print_num(r);
    print("\n");
}
```

The declaration ends with `;` and has no body. The linker resolves the symbol from an object you
supply:

```console
$ cc -c -O1 -ffreestanding -fno-pic -fno-stack-protector lib.c -o lib.o
$ loment build app.lomt --link lib.o -o app
```

Parameters and return values are **scalars and `ptr` only** (E21). `str` is a pointer plus a
length; a `struct` is not passed through the C ABI. A function that needs a C string receives a
`ptr` to a NUL-terminated buffer you build.

## The boundaries of the C leg

| Boundary | Rule |
|---|---|
| Types | integers, `bool`, `ptr`; no structs, no `str`, no floats |
| Symbols | the object must not leave undefined symbols in a release build |
| Libraries | static objects and archives; `.so` on the hosted path, one shared object |
| Target | ELF; the PE target has no import path for this |
| Size | an object may not exceed 4 MiB |
| Port dimension | `sealed` refuses `-L` / `-l` / `--cc-arg`; `--link` of an object is accepted |

An object that calls `printf` or `malloc` has undefined symbols. In a sealed artifact the linker
refuses it; compile the other side freestanding and let it call only itself. In a `hosted`
artifact, libc and shared libraries are available, subject to the hosted limits in chapter 29.

## Building a C string

A Loment `str` carries a length and has no terminator. To pass text to C, copy it into a buffer
and append `\0`:

```rust,run
module cstring

choose runtime

def to_c(s: str) -> ptr {
    let n: u32 = str_len(s);
    let p: ptr = alloc(n + 1);
    let i: u32 = 0;
    while i < n {
        store8(p, i, str_byte(s, i) as u8);
        i = i + 1;
    }
    store8(p, n, 0 as u8);
    return p;
}

fn main() {
    let p: ptr = to_c("loment");
    let n: u32 = 0;
    while load8(p, n) != 0 {
        n = n + 1;
    }
    print_num(n);
    print("\n");
    free(p);
}
```
<!-- expect: 6 -->

The function allocates `n + 1` bytes, copies the text and appends the terminator. The caller owns
the buffer and must free it.

## The process bridge

For Python, Java, JavaScript and other runtimes, the portable answer is to start the interpreter
as a process and read its output. The `proc` module provides two helpers:

```rust
use proc

let out: ptr = alloc(4096);
let n: i64 = proc_sh("echo hello", out, 4096);
let m: i64 = proc_python("print(6 * 7)", out, 4096);
```

Both take a command (or source text), an output buffer and its capacity, and return the number of
bytes written or a negative error. The bridge captures the process's standard output as text.

This is a bridge, not an embedding. The other runtime has its own memory, its own heap and its own
lifetime; the boundary is a pipe.

## LibL

The standard library includes **LibL**, a libc-shaped face over Loment code: allocation, strings,
files, time and the usual C-style functions, written in Loment and shipped with the toolchain. The
point is to let a program use familiar names without linking a C library. `use` the module and
call its functions like any other library.

LibL and `extern fn` solve different problems:

- **LibL** is portable Loment code with C-like names.
- **`extern fn`** calls an object built by another compiler.

A program can use both. Start with LibL when a C-like API is all you need; reach for `extern fn`
when the code already exists outside.

## Threads and the C ABI

A thread entry point that calls C must respect the C ABI's stack and register conventions. The
`thread_spawn` entry is a Loment function with one parameter; calling C from inside it is the same
`extern fn` call as anywhere else. The platform's alignment rules apply to the stack you supply.

## What this chapter does not cover

- C++ name mangling: declare the symbol with `extern "C"` on the other side, or provide a C
  wrapper.
- Java objects: the AOT path is a separate chapter in the contribution material; the process
  bridge is the portable route.
- Loading a shared object at run time: the hosted path links at build time, one shared object.

The next chapter covers threads and the network, the two interfaces where the artifact talks to
the operating system directly.
