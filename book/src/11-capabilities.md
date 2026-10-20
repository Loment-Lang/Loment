# Capability domains

This is the part of Loment that has no counterpart in Rust, C or Go. It is small, and it is worth
reading carefully because its limits are as important as its guarantees.

## Declaring a domain

```rust
capability blk_write : disk[0..4] revocable
```

A capability names a **space** (here `disk`), an inclusive index range `[lo..hi]`, and whether it
is `revocable`. It is declared at module level, next to `const`s and `fn`s.

## Guarding an index

```rust,run
module caps

choose runtime

capability slots : disk[0..4] revocable

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let b: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(b, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(b, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(b, i) as u8;
        let hi: u8 = load8(b, n - 1 - i) as u8;
        store8(b, i, hi);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, b as u64, n as u64);
}

fn guarded(slot: u32) -> u32 {
    guard slots(slot);
    return slot;
}

fn _start() {
    write_dec(1, guarded(2));
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 2 -->

`guard slots(slot)` evaluates the index:

- inside `[lo, hi]` — the audit counter for the domain is incremented and execution continues;
- outside — the program **traps**. There is no error value to handle; a violation is fatal.

A **literal** index outside the domain is rejected at compile time (E004), so the runtime check is
for values that only exist at run time.

## Excluding a space

```rust
excluded "network: this unit does not open sockets"
```

`excluded` declares a space the unit promises not to touch. A `capability` that uses the same
space conflicts with it (E005). The point is to make the promise explicit in the source, next to
the code — not to enforce it at run time.

## What a guard is not

The honest boundaries, from the language guide:

- **A guard constrains an index; it is not authorisation.** It never asks "does this process hold
  this capability". The principal-to-capability binding lives on the kernel side.
- **Nothing gates the operations that actually do things.** `syscall4`, `alloc`, `load8`,
  `store8`, `ptr_add` and FFI are raw. A program — or a library it `use`s — can open a socket,
  read a file and write through a pointer with no declaration and no audit entry.
- `revocable` is a flag in the declaration and in the domain table; revocation itself is a kernel
  responsibility.

So what have you bought? A unit that **states** the domains it uses, a table the runtime carries,
and a checked index at the points you write `guard`. The rest is on you and on the platform. That
is the current design, and the book says so rather than overselling it.
