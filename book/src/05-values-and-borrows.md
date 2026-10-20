# Values move, slices borrow

Loment has no lifetime annotations and no reference types in the Rust sense. It has two things
that cover the same ground, and they are easy to tell apart once you have seen them:

- **Values move.** Passing an array (or any aggregate) by value hands it over; the old name is
  dead.
- **Slices borrow.** `&xs` and `&mut xs` pass a view for the duration of the call; the array is
  still yours afterwards.

## A value that moves

```rust
fn consume(a: [u32; 4]) -> u32 {
    a[0] = 99;
    return a[0];
}

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let r: u32 = consume(a);     // `a` is moved into consume
    let s: u32 = a[0];           // E006: 变量 a 已被移动, 不能再用
}
```

The checker rejects the last line with **E006** ("value has been moved"). To keep the array, pass a
slice instead.

## A slice that borrows

```rust,run
module slices

choose runtime

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

fn sum(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}

fn _start() {
    let xs: [u32; 3] = [10, 20, 12];
    let total: u32 = sum(&xs);
    write_dec(1, total);
    write_str(1, "\n");
    write_dec(1, xs[0]);         // the array is still ours
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 42
10 -->

A slice parameter is written `[T]`; a mutable one is `mut [T]`, and the call passes `&mut xs`.
Writing through a read-only slice is E011 — the diagnostic tells you to declare the parameter
`mut [T]`.

```rust
fn bump(xs: mut [u32]) {
    xs[0] = xs[0] + 1;
}

fn caller() {
    let a: [u32; 2] = [1, 2];
    bump(&mut a);
}
```

## The borrow rules, in one paragraph

Two borrows of the same name cannot overlap when at least one of them is mutable: passing `&mut a`
twice in the same call is E007, and so is passing `&a` and `&mut a` together. The check is
per-call and syntactic — the language has no lifetimes, so a slice never outlives the call it was
made for. If you need to keep the data, return it by value; values move.

## What has no borrow form

Strings (`str`) are already a pointer and a length; they are copied as a pair, not borrowed.
Structs and enums move by value like arrays. The borrow machinery exists for arrays, because that is
where copying would be expensive and where mutation needs a view.
