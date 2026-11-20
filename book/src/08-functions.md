# Functions

A function is the unit of behaviour in Loment. It has typed parameters, an optional return type,
and a body. There are no methods outside `impl` blocks, no closures, and no function-pointer
values. What a function can do is call other functions, recurse, and return a value.

## Declaration

```rust
fn add(a: u32, b: u32) -> u32 {
    return a + b;
}
```

- Every parameter carries a type.
- `-> T` states the return type. Without it, the function returns unit `()`.
- The body is a block of statements.
- `def` is an alternative spelling in Tur 2026.2 and later, and means exactly `fn`.

A function may appear before or after its callers. There are no forward declarations; the checker
resolves names across the unit.

## Returning

A non-unit function must return a value on every path (E027):

```rust
fn sign(x: i32) -> i32 {
    if x < 0 {
        return -1;
    }
    if x > 0 {
        return 1;
    }
    return 0;
}
```

The checker accepts a body that ends in `return`, in an `if`/`else` where both branches return,
or in a `match` where every arm returns. Falling off the end of a non-unit function is an error,
not a zero value.

A unit function returns nothing. Tur 2026.2 adds the bare `return;` for an early exit:

```rust
fn log_if(flag: bool) {
    if !flag {
        return;
    }
    print("flag set\n");
}
```

In a function with `-> T`, `return;` remains a syntax error; the value is required.

## Recursion

Recursion works directly:

```rust,run
module recursion

choose runtime

def fact(n: u32) -> u32 {
    if n <= 1 {
        return 1;
    }
    return n * fact(n - 1);
}

def gcd(a: u32, b: u32) -> u32 {
    if b == 0 {
        return a;
    }
    return gcd(b, a % b);
}

fn main() {
    print_num(fact(5));
    print(" ");
    print_num(gcd(84, 30));
    print("\n");
}
```
<!-- expect: 120 6 -->

There is no tail-call guarantee. Deep recursion consumes the machine stack, and the stack size is
the platform's. For a loop that may run long, use `while`.

## Parameters and arguments

A call passes as many arguments as the declaration has parameters. A mismatch is E003. The limit
is **10 parameters**; a call site with more than 10 arguments is a hard failure of the self-hosted
layout and exits with code 132 without a diagnostic. Keep signatures small.

Scalars are passed by value: the callee gets a copy. Arrays, structs and enums are values too, and
passing one **moves** it:

```rust
fn consume(a: [u32; 4]) -> u32 {
    return a[0];
}

fn caller() {
    let xs: [u32; 4] = [1, 2, 3, 4];
    let first: u32 = consume(xs);   // xs is moved
    // let again: u32 = xs[0];      // E006: already moved
}
```

To keep the array, pass a slice (`&xs`) instead. Chapter 13 covers the rule in full.

## Functions call functions

A helper can call another helper, and the compiler keeps the calls direct:

```rust,run
module calls

choose runtime

def square(x: u32) -> u32 {
    return x * x;
}

def sum_squares(n: u32) -> u32 {
    let total: u32 = 0;
    for i in 0..n {
        total = total + square(i);
    }
    return total;
}

fn main() {
    print_num(sum_squares(4));
    print("\n");
}
```
<!-- expect: 14 -->

`sum_squares(4)` adds `0 + 1 + 4 + 9`. The loop bound is exclusive, so `i` takes 0, 1, 2 and 3.

## A function that writes a number

Before `print_num`, every program carried its own decimal printer. The function is a useful case
study because it combines a pointer, a loop and a write:

```rust,run
module decimal

choose runtime

def write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        n = n + 1;
        x = x / 10;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}

fn main() {
    write_dec(1, 2026);
    print("\n");
}
```
<!-- expect: 2026 -->

The function writes digits into a buffer from the end, reverses the buffer in place, and hands the
result to `write`. The same shape works for hexadecimal (a different digit table) and for
fixed-width fields (pad before writing).

## What functions do not have

- **No default arguments.** Every parameter is supplied at every call.
- **No variadic functions.** Use a slice or a struct.
- **No overloading.** A name has one declaration per unit.
- **No function-pointer type or closure.** `thread_spawn` takes a function name; generic
  functions are monomorphised; there is no value that holds a function.
- **No methods outside `impl`.** A method is a function in a trait implementation (chapter 17).

## Functions and the rest of the book

Every later chapter adds one of three things: a new statement form inside a function, a new type
a function can take or return, or a property of the whole artifact that changes what a function
may do. The function itself stays the same shape.

The next chapter covers the statements that decide which function body runs next.
