---
name: loment-ffi
description: Linking Loment to other languages - the C ABI (extern fn, --link .o/.a/.so, --allow-ports), the process bridge (use proc), LibL's libc face, Java AOT without a JVM, net_ladder, and thread_spawn. Read before calling a foreign library or writing network/thread code. Part of the LomentSkill suite; section numbers are global.
---

## 7.3 Libraries written in other languages (FFI)

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


**Two legs, chosen by what is on the other side** - picking the wrong one wastes the afternoon:

| What the other side is | How to use it | Coverage |
|---|---|---|
| **A C-ABI library** (C / C++ / Rust / Zig / Go(c-archive) / Swift / C#(NativeAOT) / Fortran…) | Declare `extern fn` and pass `--link that.o` when building | Every language that can export C symbols |
| **A runtime** (Python / Java / JS / Ruby / Lua…) | `use proc`, then `proc_sh` / `proc_python` starts an interpreter process and you read its output back | Every language with an interpreter |

### 7.3.1 The C-ABI family (real linking)

```rust
module ffi_demo

extern fn c_add(a: i32, b: i32) -> i32;    // signature only, semicolon at the end
extern fn c_free(p: ptr);                  // no `-> T` means void

fn _start() {
    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);
}
```

```
$ cc -c -O1 -ffreestanding -fno-pic lib.c -o lib.o      # build the other side yourself
$ loment build app.lomt --link lib.o -o app             # we link it
```

**The signature takes scalars only** (`i8..i64` / `u8..u64` / `bool`) **and `ptr`**. `str` is
"pointer plus length", **not a C string**, and a struct passed by value follows another register
classification rule entirely - either of those is **E021**, not a silent miscompile. A string
bound for C has to be assembled in memory as a NUL-terminated byte sequence by you.

Call sites pass arguments by the **platform C ABI** (Linux: the first six integer arguments go in
`rdi rsi rdx rcx r8 r9`; Windows: `rcx rdx r8 r9`), while Loment-to-Loment calls keep Loment's own
convention (arguments on the stack).

**What the other side's `.o` may contain, and the one door that is shut.** Relocations are
applied, several objects may reference each other, and archives (`.a`) are expanded - all of
that works. The boundary is the **port gate** (meeting it produces an **error**, not a guess):

- an object that carries **undefined symbols** (`printf`/`malloc` being the typical ones) is
  **refused by the linker by default**. The launchers pass `--allow-ports` in **both** channels
  since 2026-10-17 (`docs/219` section 8c); calling `loment-lomelf` directly means adding it
  yourself. With the door open the linker can also supply `memcpy` / `memset` / `memcmp` /
  `malloc` / `free` / `__stack_chk_fail` from its own runtime.
- or keep the other side freestanding (`-ffreestanding -fno-stack-protector`) so it calls only
  itself - still the right choice when you do not want a runtime dependency.
- dynamic libraries (`.so`) **work on ELF since 2026-10-17**: the product carries
  `PT_INTERP`/`PT_DYNAMIC`/`DT_NEEDED` and a PLT/GOT, and the host's loader (`ld.so`) performs
  the mapping and relocation binding. Limits today: ELF only (PE FFI is not implemented), one
  shared object on the self-hosted linker, and function symbols (not data symbols) are
  imported. `choose hosted` (section 2.1) is what allows the artifact to reach them.
- several `--link` flags may be given at once; they are appended in order.
- the other side's `.o` **may not exceed 4 MiB** (over that is an error, never a truncated build).

### 7.3.2 Python / Java / JS (the process bridge)

```rust
module py_demo
choose runtime

use proc

fn _start() {
    let buf: ptr = alloc(1024);
    let n: i64 = proc_sh("python3 -c 'import json; print(1)'", buf, 1024);
    if n > 0 {
        syscall4(1, 1, buf as u64, n as u64);
    }
    syscall4(60, 0, 0, 0);
}
```

`proc_python("...")` is sugar for Python. **Changing language means changing the command** -
nothing on the Loment side moves.

Three boundaries: **what crosses is a byte stream, not a pointer** (no structs); it is
**Linux/ELF only** (the Windows shim has no `fork`/`pipe` and returns -1 there); and the machine
has to have that interpreter.

**With the package installed rather than the repository**, `proc` is not in your project and
`use proc` reports "name import not found". The packaged copy is at
`<prefix>/share/loment/lib/proc.lomt`, and the regular way to bring it in is to put it in your
project:

```
mkdir -p deps/proc && cp <prefix>/share/loment/lib/proc.lomt deps/proc/
```

(`deps/<name>/<name>.lomt` is the first place the name form searches - and the shape `lompi` uses
when it installs a library.)

### 8.9 Foreign libraries: the two legs in practice

**Leg 1 - C ABI** (section 7.3.1): `extern fn` signatures take scalars and `ptr` only;
`--link` accepts `.o`, `.a` (fixed-point member selection) and, on ELF, `.so` (the product
carries `PT_INTERP` / `PT_DYNAMIC` / `DT_NEEDED` and a PLT/GOT; the system's `ld.so` binds it).
Objects with undefined symbols are refused by default; the launchers pass `--allow-ports` in
both channels, and the linker can then supply `memcpy` / `memset` / `memcmp` / `malloc` /
`free` / `__stack_chk_fail` itself. PE has **no FFI at all**. Measured end-to-end: C, C++,
Rust and Zig; the same mechanism covers every language that exports C symbols.

**Leg 2 - the process bridge** (`use proc`, section 7.3.2): measured end-to-end for Go, Python,
Java, JavaScript, Perl and Lua. What crosses is a **byte stream**, not a pointer; it is
**Linux/ELF only**; the machine needs the interpreter.

**LibL**: the standard library's **libc face** (`docs/232`). `use libl` brings in the libc-shaped
modules (`string`, `ctype`, `stdlib`, `stdio`, `libl_host`) with libc names, argument order and
return semantics, while the rest of `std` keeps Loment's explicit-length/offset shape. The two
faces share the same bytes, and the criterion compares against **the real glibc** (225 items,
byte-identical on glibc 2.43), not against Python.

**Java without Java** (`docs/282`, `docs/283`): a `.jar` is AOT-compiled at build time into a
portable Loment IR module, merged at link time, and the product is an ordinary ELF/PE - no JDK
at build, no JVM at run. It is a **closed world, static subset**: no reflection, no dynamic
loading. The class-file verifier (#1058), inheritance/closed-world dispatch, `tableswitch`/
`lookupswitch`, object arrays, `checkcast`/`instanceof`, boxed types and `<clinit>` are landed
(#1062 / PR #1148); the JNI path still exists in `hosted` mode for cases the AOT path does not
cover.

### 8.10 Network and threads

**`net_ladder`** (`docs/237`, Potato v14) counts syscall **sites** by the widest reach they
need:

| Rung | Meaning | Numbers |
|---|---|---|
| `w0` | verify the peer's bytes on the same machine | `socketpair` (53) |
| `w1` | one bounded connection, peer chosen by us | `socket`(41) `connect`(42) `sendto`(44) `recvfrom`(45) `sendmsg`(46) `recvmsg`(47) `shutdown`(48) `getsockname`(51) `getpeername`(52) `setsockopt`(54) `getsockopt`(55) |
| `w2` | many concurrent connections (multiplexing only) | `poll`(7) `select`(23) `epoll_wait`(232) `epoll_ctl`(233) `epoll_create1`(291) |
| `w3` | accept any peer | `bind`(49) `listen`(50) `accept`(43) `accept4`(288) |

The four rungs are always present and `w0+w1+w2+w3 == total_net == surfaces.net`. The width
itself (how many connections at once, how many bytes, how long) is not counted.

**Two measured network traps** are in section 6.2: SIGPIPE kills the process on the second
write after the peer's RST, and a blocking `read` on an accepted socket blocks forever unless
you set `SO_RCVTIMEO`. `syscall7` exists so `sendto`/`recvfrom` can carry their sixth argument
(`socklen_t`).

**Threads** (`docs/245`): `thread_spawn("entry", stack_top, arg, done)` starts a real Linux
thread. The entry is a **string literal naming a function in the unit**, so the product contains
a direct `call @entry` and the static call graph stays static. The product switches `rbp`/`rsp`
to the supplied stack before calling the entry (the shared frame is a structural race
otherwise). `done` is a `u32` the product **atomically increments and `futex_wake`s after the
entry returns** - the entry cannot do it itself, because returning exits the thread. **ELF
only**; PE refuses by name. Stage 1 does not include function pointers or indirect calls
(`docs/246` is that line).

