# Pointers and memory

A `ptr` is an address. The language gives you three ways to obtain one — `alloc`, a cast from an
integer, or a system call — and four operations to use it: `load8`, `store8`, `ptr_add` and
`ptr_sub`. Everything else about memory is either the runtime's policy or your responsibility.

## The default heap

```rust
let p: ptr = alloc(32);
```

`alloc(n)` takes `n` bytes from the artifact's heap and returns the address. In the default
`gc_manual` tier the heap is a **64 KiB arena** with a bump pointer and an address-ordered free
list. Two consequences follow:

- **An allocation that does not fit stops the process.** There is no null return to check.
  Size buffers from what you know, and read input in chunks.
- **`free` returns a block to the list.** It supports the bump-plus-free-list allocator, and it
  does not coalesce adjacent blocks. A long-running program that allocates varied sizes can
  fragment the arena even when the total free space is enough.

`hosted` artifacts use a 1 MiB arena. The `gc_auto` and `gc_auto_alpha` tiers replace the arena
with a collector; chapter 27 covers the tiers.

## Reading and writing bytes

```rust
let p: ptr = alloc(4);
store8(p, 0, 65 as u8);       // p[0] = 'A'
let b: u32 = load8(p, 0);     // 65
```

`load8` returns `u32`; cast it down when you need a `u8`. `store8` takes a `u8`. There is no
bounds check and no alignment requirement: `store8(p, 1000, 1 as u8)` writes wherever that
address happens to be.

## Pointer arithmetic

```rust
let q: ptr = ptr_add(p, 4);   // p + 4
let r: ptr = ptr_sub(q, 4);   // p
```

The offset is in **bytes**, not elements. There is no pointer-typed `+` operator; `ptr_add` and
`ptr_sub` are the operations, and `(p as u64) + n` is the explicit alternative:

```rust
let address: u64 = p as u64;
let next: ptr = (address + 16) as ptr;
```

Casting between `ptr` and an integer is allowed in both directions. Casting a `ptr` to a narrower
integer truncates its address, which is almost always a mistake; use `u64`.

## A buffer in practice

```rust,run
module buffers

choose runtime

def fill(buf: ptr, n: u32, value: u8) {
    let i: u32 = 0;
    while i < n {
        store8(buf, i, value);
        i = i + 1;
    }
}

def checksum(buf: ptr, n: u32) -> u32 {
    let sum: u32 = 0;
    let i: u32 = 0;
    while i < n {
        sum = (sum + load8(buf, i)) & 0xFFFF;
        i = i + 1;
    }
    return sum;
}

fn main() {
    let buf: ptr = alloc(8);
    fill(buf, 8, 3 as u8);
    print_num(checksum(buf, 8));
    print("\n");
    free(buf);
}
```
<!-- expect: 24 -->

`fill` writes eight bytes, `checksum` sums them, and `free` returns the block. The function
`checksum` takes a `ptr` and a length because a pointer alone does not carry its size.

## Memory and the type system

The pointer type holds no length, no element type and no ownership. That is why the length is
passed separately, and why a pointer can be reinterpreted freely:

```rust
let p: ptr = alloc(8);
store8(p, 0, 1 as u8);
let q: ptr = p;             // a copy of the address, not of the data
```

Copying a `ptr` copies the address. Two pointers to the same block see each other's writes. The
language does not track aliasing for raw pointers; the borrow checker applies to slices, not to
`ptr`.

## When to use a pointer

Use a pointer when:

- a function must write into caller-owned storage;
- a value is larger than you want to move, and a stable address is useful;
- you are building a data structure whose shape is not known at compile time;
- a system call takes an address.

Prefer an array or a slice when the size is known and the data is a value. Arrays are checked by
the type system, and slices carry their length. Pointers are the escape hatch, and the compiler
does not help with their bounds.

## Freeing

```rust
free(p);
```

`free` returns the block to the allocator. It does not clear the bytes, and using the pointer
afterwards is a use-after-free; the default `lms_full` level can catch some of these statically
(E029), and the runtime does not track them. Setting a pointer to 0 after freeing is a convention,
not a language feature.

The next part moves from individual values to the ways they are abstracted and shared.
