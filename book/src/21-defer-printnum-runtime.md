# `defer`, `print_num` and the runtime block

Three features arrived with Tur 2026.2 and share one dependency: the runtime block. `print_num`
lives in it, `defer` interacts with the function epilogue, and a program that asks for neither can
leave the block out. This chapter covers all three together because their boundaries meet.

## `print_num`

```rust
print_num(n);
```

- Accepts any integer type.
- Writes decimal digits, with a minus sign for negative values.
- Adds no newline.
- Requires `choose runtime`.

```rust,run
module numbers

choose runtime

fn main() {
    let a: u8 = 255;
    let b: i64 = -40;
    print_num(a);
    print(" ");
    print_num(b);
    print("\n");
}
```
<!-- expect: 255 -40 -->

The builtin widens by sign to `i64` internally, so an `i64` value passes through unchanged and a
smaller type is extended. A `u64` above the `i64` maximum prints as a negative value; use the
standard library's formatting module when the full unsigned range matters.

## The runtime block

`choose runtime` asks for the runtime block. These constructs need it:

| Construct | Why |
|---|---|
| `print_num` | the decimal printer and the write wrapper |
| `alloc` / `free` | the heap |
| `guard` | the domain tables and the audit counter |
| `gc_auto` / `gc_auto_alpha` | the collector |
| `lgc` | the collector's control state |

A program declares `choose no_runtime` when it wants an artifact with no runtime. Then a use of
any of those constructs is refused **by name** (E031), before linking. The default is
`no_runtime`, and the examples in this book declare `choose runtime` because they print numbers.

A program that writes only `print` and system calls can omit the dimension:

```rust,run
module noruntime

fn main() {
    print("no runtime block\n");
}
```
<!-- expect: no runtime block -->

`print` writes a string with one system call and does not need the block.

## `defer`

`defer` registers a **call** to run when the enclosing block exits:

```rust
defer free(p);
```

Rules:

- Only a call is accepted. `defer 1;` is a syntax error.
- The arguments are evaluated **where the `defer` is written**, not at exit.
- Within one block, defers run in **LIFO** order: the last registered runs first.
- They run on normal fall-through, on `return <value>`, on `?`, and on `break` / `continue`.
- They do not run on a panic, a trap, or process exit.
- An `if` body and a loop body are blocks; a defer inside one runs at the end of that block.

```rust,run
module deferred

choose runtime

def note(n: u32) {
    print("note ");
    print_num(n);
    print("\n");
}

def work(n: u32) -> u32 {
    defer note(n);
    if n == 0 {
        return 1;
    }
    return 2;
}

fn main() {
    print_num(work(0));
    print("\n");
    print_num(work(1));
    print("\n");
}
```
<!-- expect: note 0
1
note 1
2 -->

Both returns run their deferred call before the function returns. The order is visible: the note
comes before the caller prints the result.

Arguments are captured at the defer:

```rust
let x: u32 = 1;
defer note(x);      // captures 1
x = 2;              // does not change the deferred call
```

### Limits and a measured boundary

- The self-hosted compiler allows **8 defers per function**, **4 arguments** each, and **64**
  nesting levels. The reference compiler has no such caps.
- A deferred call in a unit function that leaves through a **bare `return;`** currently skips the
  deferred call in the reference compiler; a `return <value>` path runs it. Put cleanup in a
  function that returns a value, or let the block fall through, until this is fixed.

`defer` is not a finalizer for the process. A program that must release a resource before exit
uses an explicit call on every exit path, or arranges the exit through a function that returns.

## Choosing between `defer` and explicit cleanup

Use `defer` when:

- a function acquires a resource and has several exit paths;
- the cleanup is one call;
- the call's arguments are known at the point of acquisition.

Use explicit cleanup when:

- the cleanup depends on the path taken;
- the function is long and the defer would be far from the work;
- the release must happen before a specific later step, not at function exit.

## The block in one view

| Need | Dimension | What it costs |
|---|---|---|
| print an integer | `choose runtime` | the runtime block |
| allocate and free | `choose runtime` | the heap and allocator |
| check a domain | `choose runtime` | the domain tables |
| collect automatically | `choose runtime` + a GC tier | the collector |
| none of the above | default `no_runtime` | nothing |

The next chapter controls the collector's timing, which is the part of the runtime that Tur
2026.3 adds.
