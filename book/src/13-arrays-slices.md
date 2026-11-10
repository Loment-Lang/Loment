# Arrays, slices and borrowing

An array is a fixed-size value. A slice is a borrowed view of an array, passed to a function for
the duration of one call. These two forms cover the same ground as Rust's arrays and references,
with a much smaller rule set.

## Arrays

The length is part of the type:

```rust
let xs: [u32; 4] = [1, 2, 3, 4];
let first: u32 = xs[0];
xs[0] = 9;
```

- The literal must have exactly the declared length (E016).
- Indexing is `xs[i]`, and an element **is** an assignment target.
- The length is not stored at run time. `xs` is four `u32`s inline.
- There is no `push`, no resize and no length query for an array. A growable sequence is a
  library type (`use vec`).

Indexing is not bounds-checked in a release artifact. The default `lms_full` level adds a
compile-time check for a **literal** index (E028); a runtime index is the programmer's
responsibility. Chapter 29 covers the memory-safety levels.

## Slices

A slice parameter is written `[T]` (read-only) or `mut [T]` (writable):

```rust
def sum(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}
```

The caller passes `&xs` for `[T]` and `&mut xs` for `mut [T]`:

```rust,run
module slices

choose runtime

def sum(xs: [u32]) -> u32 {
    let total: u32 = 0;
    for x in xs {
        total = total + x;
    }
    return total;
}

def bump(xs: mut [u32]) {
    xs[0] = xs[0] + 1;
}

fn main() {
    let a: [u32; 3] = [10, 20, 12];
    print_num(sum(&a));
    print(" ");
    bump(&mut a);
    print_num(a[0]);
    print("\n");
}
```
<!-- expect: 42 11 -->

A slice is a pointer plus a length. `slice_len(xs)` reads the length; the data lives in the
caller's array. The array is still usable after the call.

Writing through a read-only slice is E011. The diagnostic tells you to declare the parameter
`mut [T]` and pass `&mut xs`.

## Values move

Passing an array **by value** moves it:

```rust
def consume(xs: [u32; 4]) -> u32 {
    return xs[0];
}

fn caller() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let n: u32 = consume(a);
    // let m: u32 = a[0];     // E006: a was moved
}
```

Use a slice when the caller still needs the array. Use a by-value array when the function takes
ownership and the caller is done with it. The same rule applies to structs and enums.

## Borrow rules

A borrow (`&T` / `&mut T`) is a first-class type: a parameter, a return type, a struct field, or
a generic argument. Regions are **inferred** - there are no lifetime annotations to write:

```rust
struct Cursor { data: &[u32], pos: u32 }

fn first(c: &Cursor) -> u32 {
    return c.data[0];
}
```

Two borrows of the same place cannot overlap when at least one is mutable, and a mutable borrow
that is **stored** excludes later borrows of that place until the end of the function:

```rust
let a: [u32; 2] = [1, 2];
both(&a, &mut a);        // E007: a read-only and a mutable borrow overlap
```

The check is static and per region: a borrow may not outlive its source, `free(p)` while `p` is
borrowed is E041, and a returned borrow must come from a parameter. Disjoint fields (`v.x` and
`v.y`) can be borrowed separately. Older editions (`tur2026.1` / `tur2026.2`, and
`choose lifetime_no`) keep the earlier shape: borrows exist only in call arguments and cannot be
stored or returned.

## Slices and `for`

A collection `for` accepts a slice parameter and an array:

```rust
for x in xs { … }        // xs: [u32] or [u32; N]
```

The loop variable is a copy. To modify elements, index the slice with a counter:

```rust
let i: u32 = 0;
while i < slice_len(xs) {
    xs[i] = xs[i] * 2;
    i = i + 1;
}
```

## A worked example: reverse in place

```rust,run
module reverse

choose runtime

def reverse(xs: mut [u32]) {
    let i: u32 = 0;
    let n: u32 = slice_len(xs);
    while i < n / 2 {
        let lo: u32 = xs[i];
        let hi: u32 = xs[n - 1 - i];
        xs[i] = hi;
        xs[n - 1 - i] = lo;
        i = i + 1;
    }
}

fn main() {
    let a: [u32; 5] = [1, 2, 3, 4, 5];
    reverse(&mut a);
    let first: bool = true;
    for x in a {
        if !first {
            print(" ");
        }
        print_num(x);
        first = false;
    }
    print("\n");
}
```
<!-- expect: 5 4 3 2 1  -->

The function swaps from both ends and stops at the middle. `n - 1 - i` is computed with `u32`
arithmetic; the loop condition guarantees `i < n / 2`, so the subtraction never wraps.

## The boundary

| Operation | Array | Slice |
|---|---|---|
| declare a value | `[u32; 4]` | only as a parameter |
| pass to a function | by value (moves) or `&` | `&xs` / `&mut xs` |
| read length | the type | `slice_len` |
| write an element | `xs[i] = …` | only through `mut [T]` |
| keep after the call | the original name | the caller's array |

The next chapter applies these shapes to text.
