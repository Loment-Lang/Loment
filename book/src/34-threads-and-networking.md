# Threads and networking

Two interfaces leave the language and reach the operating system: threads and sockets. Both are
built from system calls and a small set of builtins. This chapter describes the shapes, the
platform boundaries, and the parts the language deliberately leaves to a library.

## Threads

`thread_spawn` starts a real Linux thread:

```rust
thread_spawn("worker", stack_top, arg, done) -> i64
```

- The first argument is the **name of a function in this unit** taking exactly one parameter. It
  becomes a direct call in the product, so the call graph stays static.
- `stack_top` is the highest address of the thread's stack. You supply the stack.
- `arg` is passed to the entry function.
- `done` is the address of a `u32`. After the entry returns, the product atomically increments it
  and wakes a futex. Pass `0` for no notification.

The return value is the new thread's id.

The `thread` module (`use thread`) provides the surrounding pieces: `thread_stack(bytes)` maps a
stack and returns its top, `thread_stack_top(base, bytes)` computes the top from a base, and the
module also has futex, mutex, condition-variable and join helpers.

```rust
use thread

fn worker(arg: u64) {
    // do work; `arg` is the fourth argument of thread_spawn
}

fn main() {
    let done: ptr = alloc(4);
    store8(done, 0, 0 as u8);
    let stack: ptr = thread_stack(65536);
    let tid: i64 = thread_spawn("worker", stack as u64, 7, done as u64);
    // wait for the futex on `done`
}
```

The entry cannot notify after it returns, because the product exits the thread at that point. The
`done` counter is the notification, and the wait must be on the same address.

Threads are ELF-only. The PE target has no `clone`.

## The rules a thread adds

- The entry function is named at compile time; there is no function-pointer argument.
- The stack is the caller's responsibility. A stack that is too small overflows silently.
- `done` is an address; it must point at writable memory that outlives the thread.
- There is no thread pool and no async runtime in the language. A library can build one, but the
  primitives are the system calls and the futex helpers.

## Sockets

Networking is a set of system calls. The numbers are Linux's own on ELF:

| Call | Number | Purpose |
|---|---|---|
| `socket` | 41 | create a socket |
| `connect` | 42 | connect to an address |
| `accept` | 43 | accept a connection |
| `sendto` | 44 | send a datagram or message |
| `recvfrom` | 45 | receive a datagram or message |
| `bind` | 49 | bind an address |
| `listen` | 50 | listen for connections |
| `epoll_create1` | 291 | create an epoll set |
| `epoll_ctl` | 233 | add or remove a descriptor |
| `epoll_wait` | 232 | wait for events |

`syscall4` covers up to three arguments, `syscall6` up to five, and `syscall7` uses all six
argument registers:

```rust
let n: i64 = syscall7(44, fd, buf as u64, len as u64, 0, addr as u64, addrlen as u64);
```

`sendto` and `recvfrom` need the sixth argument (a `socklen_t`), which is why `syscall7` exists.
UDP with addresses is writable with it; the earlier two helpers ran out of registers.

A minimal client follows the four stages from chapter 3:

```rust
let fd: i64 = syscall4(41, 2, 1, 0);        // AF_INET, SOCK_STREAM
let r: i64 = syscall4(42, fd as u64, addr as u64, 16);
let n: i64 = syscall4(1, fd as u64, buf as u64, cap as u64);   // write
```

The address structures are built in memory with `store8`; the language has no socket type and no
address type. A library (`net`, `http`) wraps the calls with the structure layouts.

## What the PE target supports

The PE runtime implements **29 system-call numbers**: the file and process eight (`read`, `write`,
`close`, `brk`, `exit`, `getdents64`, `openat`, `newfstatat`) plus the networking family (`poll`,
`select`, `socket`, `connect`, `accept`, `accept4`, `sendto`, `recvfrom`, `sendmsg`, `recvmsg`,
`shutdown`, `bind`, `listen`, `getsockname`, `getpeername`, `socketpair`, `setsockopt`,
`getsockopt`, `epoll_wait`, `epoll_ctl`, `epoll_create1`).

Any other number returns `-ENOSYS` (38). A program that needs a call outside the table runs on
ELF, or waits for the shim to grow.

## The network ladder

Potato records a `net_ladder` field: four widths, one per site kind, from narrow to wide. It
answers "how much networking does this artifact contain" without reading the source. The widths
are a property of the sites in the unit, not a run-time permission.

## Boundaries

- There is no async runtime, no event loop and no socket type in the language. `epoll` is a
  system call; a library builds the loop.
- Threads are ELF-only.
- The PE shim has a fixed syscall table; other numbers return `-ENOSYS`.
- A network program needs address structures built by hand or by a library.
- The language gives no guarantees about ordering, blocking or fairness; those are the kernel's.

The last chapter points to the material that goes deeper than this book.
