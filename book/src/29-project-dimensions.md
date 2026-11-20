# Project dimensions

`choose` states a property of the whole artifact. Each dimension is written **at most once**, and
only in the **entry unit**; a library that writes one is E22. The values land in the Potato form
object, so a consumer that does not read the source can still answer what kind of artifact it is.

## The seven dimensions

| Dimension | Values | Default | What it decides |
|---|---|---|---|
| Layer | `std` · `no_std` | `std` | whether the program may use host modules |
| Reclamation | `gc_manual` · `gc_auto` · `gc_auto_alpha` · `no_gc` | `gc_manual` | how memory is reclaimed |
| Runtime | `runtime` · `no_runtime` | `no_runtime` | whether the runtime block may be carried |
| Port | `sealed` · `hosted` | `sealed` | whether the artifact may link host libraries |
| Execution | `vm` · `no_vm` | `no_vm` | which execution model the program targets |
| Memory safety | `lms_full` · `lms_forensic_admin` · `lms_daily_admin` · `lms_manage_only_admin` · `lms_close_admin` | `lms_full` | which LMS levels are on |
| Audit ledger | `lspe_open_admin` · `lspe_close_admin` | `lspe_open_admin` | whether LSPE records the ledger |

A dimension is a property of the program, not of a file. A library expresses what it needs through
its source; the entry unit makes the choice.

## Layer: `std` and `no_std`

`std` (the default) allows the host-side modules: filesystem, argv, threads, time and network.
`no_std` refuses them. Loment was written for FujoOS, so `no_std` is the mode a systems program
uses when it must run without a host underneath.

Writing `no_std` does not remove the language; it removes the library layer. A program that uses
only builtins and system calls compiles under either.

## Reclamation: `gc_*`

Chapter 27 covers the four values. The short version: `gc_manual` is the sealed heap,
`gc_auto` adds a tracing collector, `gc_auto_alpha` is the aggressive collector, and `no_gc`
declares that no collector is present.

## Runtime: `runtime` and `no_runtime`

`runtime` asks for the runtime block; `no_runtime` (the default) refuses it. The block carries
`print_num`, `alloc`, `guard`, the collector and the `lgc` control state. A construct that needs
the block is refused by name (E031) under `no_runtime`.

```rust,run
module minimal

fn main() {
    print("built without the runtime block\n");
}
```
<!-- expect: built without the runtime block -->

This program writes a string with a system call, so it needs no runtime. Adding `print_num` or
`alloc` would turn the same program into E031 until `choose runtime` appears.

## Port: `sealed` and `hosted`

`sealed` (the default) says the artifact links no host library. The launcher refuses `-L`, `-l`
and `--cc-arg` under it, and the linker rejects objects with undefined symbols and shared
objects.

`hosted` is the other path: `fn main() -> u32` instead of `_start`, real libc and shared libraries
allowed, and the artifact links against the host. The door opened in 2026; its limits are:

- ELF only (no PE import path);
- one shared object on the self-hosted linker;
- function symbols only;
- a host `ld.so` / glibc must be present.

When you invoke the linker directly, pass `--allow-ports` yourself.

## Execution: `vm` and `no_vm`

`vm` records the execution model. Today it only records it: the artifact is byte-identical to
`no_vm`, and there is no bytecode in the product. The interpreted tier is a separate tool
(`loment-lvm`) that runs LLVM IR; it is not carried by the artifact. The release gate opened in
2026, so the value is accepted and recorded.

## Memory safety: `lms_*`

Chapter 28 covers the seven levels and the four tiers. The default is `lms_full`; every other
value is a downgrade and carries `_admin`.

## Audit ledger: `lspe_*`

`lspe_open_admin` (the default) records the ledger. `lspe_close_admin` skips the questioning and
the ledger; every check still runs. Chapter 31 covers what the ledger contains.

## Contradictory pairs

The compiler names a contradiction instead of accepting both values:

| Pair | Reason |
|---|---|
| `gc_auto` + `no_runtime` | automatic reclamation is a runtime component |
| `gc_auto` / `gc_auto_alpha` + `no_std` | the collector needs the host runtime |
| `hosted` + `no_std` | `no_std` says there is nothing underneath; `hosted` says to link it |
| `hosted` + `gc_auto_alpha` | alpha's frontier rollback conflicts with host-held pointers |

A value outside the table is a typo, and a typo is E22.

## A realistic header

```rust
module tool

choose runtime
choose std
choose sealed
choose gc_manual
```

A short command-line tool uses the runtime for printing and allocation, stays on the host layer,
links nothing, and keeps the sealed heap. That is the default shape of every example in this
book.

## Reading the dimensions back

The dimensions are fields in the Potato form object. The repository's tools read them without
the source; `loment version <exe>` reads the grammar edition from the artifact. A build system
can answer "is this binary sealed, does it carry a collector, which LMS tier is on" from the
object alone.

The next chapter covers the compile-time switches that select code without a dimension.
