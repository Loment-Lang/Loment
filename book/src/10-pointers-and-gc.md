# Pointers, alloc and the GC tiers

## A pointer is an address

```rust
let p: ptr = alloc(32);
store8(p, 0, 65 as u8);        // p[0] = 'A'
let b: u32 = load8(p, 0);      // 65
let q: ptr = ptr_add(p, 4);    // p + 4, a new pointer
let r: ptr = ptr_sub(q, 4);    // back to p
```

`ptr` converts to `u64` and back with `as`; arithmetic is explicit (`ptr_add` / `ptr_sub`, or
`(p as u64) + n`). There is no bounds checking: `load8(p, 1000)` reads whatever is there. This is
the systems programming part of the language, and it is not hidden.

## alloc and the default heap

`alloc(n)` takes bytes from a **64 KiB bump heap** and returns a pointer. "Bump" means the pointer
moves forward and never comes back on its own. Two consequences:

- **An allocation that does not fit traps.** There is no `null` return to check; the program dies
  with a signal. Size buffers from what you know, and read input in chunks (as `wc` did).
- **`free` is a placeholder today.** It returns 0 and does not hand the block back to the bump
  heap. A long-running program that allocates per iteration will exhaust the 64 KiB.

If you need more, say so in the source with a GC tier.

## The three tiers

```rust
module app
choose runtime
choose gc_auto
```

`choose` is written **once, in the entry unit**; a library may not choose for the program. The
memory dimension has three values:

| Tier | What it means today |
|---|---|
| `gc_manual` (default) | You allocate; the heap is the 64 KiB bump heap. |
| `gc_auto` | The runtime's collector manages a larger arena; allocations are traced. |
| `gc_auto_alpha` | The aggressive tier — the newest collector, not the default. |

The tier is a property of the whole program, not of a module. Libraries state what they *need*
(`choose runtime`), and the root has to agree.

## A program on the collector's heap

```rust,run
module heap

choose runtime
choose gc_auto

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn _start() {
    let p: ptr = alloc(32);
    store8(p, 0, 65 as u8);
    syscall4(1, 1, p as u64, 1);
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: A -->

The same program without `choose gc_auto` uses the bump heap and prints the same byte; the tier
changes who owns the memory, not how you address it.

## Reading and writing bytes

`store8`/`load8` are the primitive. Everything else — strings, arrays, structs — is laid out on
top. When you write a parser or a codec, this is the layer you work at:

```rust
let buf: ptr = alloc(16);
let i: u32 = 0;
while i < 16 {
    store8(buf, i, 0 as u8);
    i = i + 1;
}
```

Zeroing is not automatic. A fresh allocation contains whatever the previous owner left there.
