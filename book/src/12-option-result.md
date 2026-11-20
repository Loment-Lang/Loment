# Option, Result and error flow

Loment has no exceptions. A failure is a value, and the type says so. Two generic enums are
predefined and need no import:

```rust
enum Option<T> { Some(T), None }
enum Result<T, E> { Ok(T), Err(E) }
```

`Option` answers "there may be nothing here". `Result` answers "this can fail, and the failure has
a reason". This chapter covers both, the `?` operator that propagates a failure, and the two
fallback forms that Tur 2026.2 adds.

## Returning a failure

```rust
def divide(a: u32, b: u32) -> Result<u32, u32> {
    if b == 0 {
        return Result::Err(1);
    }
    return Result::Ok(a / b);
}
```

The caller decides what a failure means. There is no hidden control flow and no unwinding.

```rust,run
module results

choose runtime

def divide(a: u32, b: u32) -> Result<u32, u32> {
    if b == 0 {
        return Result::Err(1);
    }
    return Result::Ok(a / b);
}

def unwrap_or(r: Result<u32, u32>, d: u32) -> u32 {
    match r {
        Result::Ok(v) => { return v; }
        Result::Err(e) => { return d; }
    }
}

fn main() {
    print_num(unwrap_or(divide(10, 2), 0));
    print(" ");
    print_num(unwrap_or(divide(10, 0), 99));
    print("\n");
}
```
<!-- expect: 5 99 -->

`unwrap_or` turns a `Result` into a plain value. It is the function you write once and reuse; the
match belongs in one place, and the rest of the program works with `u32`.

## `?` propagates

`?` is legal on the right-hand side of a `let` and only inside a function whose return type is
`Result` (or `Option`):

```rust
def twice(v: u32) -> Result<u32, u32> {
    let x: u32 = parse_small(v)?;
    return Result::Ok(x + 1);
}
```

If the expression is `Err`, the function returns that error immediately. If it is `Ok`, the
binding receives the payload. The error type must match the function's error type.

`?` belongs to a `let` binding. `f(g()?)` is not in the grammar; bind the intermediate value
first.

## `orelse` and `catch`

Tur 2026.2 adds two shorter fallbacks. They apply to the right-hand side of `let` or an
assignment:

```rust
let a: u32 = maybe() orelse 0;      // Option: payload or fallback
let b: u32 = risky() catch 0;       // Result: payload or fallback
```

The fallback is **lazy**: it is evaluated only when the value takes the failing branch. `orelse`
takes the payload of the first variant; `catch` does the same for a `Result`.

The v1 restriction is that the enum must have **exactly two variants with the first carrying the
payload**. `Option` and `Result` fit by definition. A custom two-variant enum fits if its first
variant carries the value:

```rust,run
module fallback

choose runtime

enum Maybe {
    Value(u32),
    Missing,
}

def pick(flag: bool) -> Maybe {
    if flag {
        return Maybe::Value(9);
    }
    return Maybe::Missing;
}

def fallback() -> u32 {
    panic(4);
    return 0;
}

fn main() {
    let a: u32 = pick(true) orelse 2;
    let b: u32 = pick(false) orelse 2;
    print_num(a);
    print(" ");
    print_num(b);
    print("\n");
}
```
<!-- expect: 9 2 -->

The second binding takes the fallback because `pick(false)` is `Missing`. The function
`fallback` is not called; the fallback expression in this example is the literal `2`. Replace it
with a function call to see laziness: the call happens only on the failing branch.

`catch |e| { … }` and `catch return` are later-edition designs and are not accepted today. The
fallback is a value.

## Option

`Option` is for absence rather than failure:

```rust,run
module option

choose runtime

def find(xs: [u32], target: u32) -> Option<u32> {
    let i: u32 = 0;
    while i < slice_len(xs) {
        if xs[i] == target {
            return Option::Some(i);
        }
        i = i + 1;
    }
    return Option::None;
}

fn main() {
    let xs: [u32; 4] = [4, 8, 15, 16];
    let r: Option<u32> = find(&xs, 15);
    match r {
        Option::Some(i) => { print_num(i); print("\n"); }
        Option::None => { print("missing\n"); }
    }
}
```
<!-- expect: 2 -->

The function returns the index of the first match, or `None`. The caller cannot ignore the
failure by accident: the value is an enum, and using it requires a match or a fallback.

## Which one

| Situation | Type |
|---|---|
| a lookup that may find nothing | `Option<T>` |
| a parse or I/O step with a reason to fail | `Result<T, E>` |
| a boolean question with no other outcome | `bool` |
| a value plus a status code | a struct, or `Result` |

A `bool` is not an error channel. If the failure needs an explanation, use `Result`.

## Boundaries

- `?` propagates the error value; it does not print, log or convert it.
- `orelse` and `catch` need a two-variant enum with the payload in the first variant.
- There is no `try` block, no exception type, and no stack unwinding.
- The error type is whatever you choose: an integer code, a small enum, or a struct payload
  flattened into a scalar.

The next chapter covers the containers the language does have: arrays and slices.
