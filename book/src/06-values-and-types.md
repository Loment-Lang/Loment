# Values and types

Loment's type system is small and explicit. There are eight integer types, `bool`, `str`, `ptr`,
the unit type `()`, fixed-size arrays, borrowed slices, and the structs and enums you declare.
There are no floats, no characters and no tuples in the language itself.

Every binding carries a type, and the type is written out. The compiler never infers one from an
initialiser.

## The complete type table

| Type | Written as | Size / shape | Notes |
|---|---|---|---|
| unsigned integers | `u8 u16 u32 u64` | 1, 2, 4, 8 bytes | wrapping arithmetic |
| signed integers | `i8 i16 i32 i64` | 1, 2, 4, 8 bytes | two's complement, wrapping |
| boolean | `bool` | 1 byte | `true` / `false`; the only condition type |
| string | `str` | pointer + length | a value carrying its own length |
| raw pointer | `ptr` | 8 bytes | unchecked address |
| unit | `()` | no value | the return type of a function with no `->` |
| array | `[T; N]` | N elements inline | `N` is a compile-time constant |
| slice | `[T]`, `mut [T]` | pointer + length | parameter types only |
| struct | its name | fields inline | declared with `struct` |
| enum | its name | tag + payload | declared with `enum` |
| generic parameter | `T` | monomorphised | no runtime representation of its own |
| prelude enums | `Option<T>`, `Result<T, E>` | tag + payload | predefined |

The element type of an array or slice is an integer or `bool` today. A `str` or `ptr` field in a
struct passes `check` and is refused at build time when the struct is lowered.

## Integers

An integer type fixes both its width and its signedness. Arithmetic **wraps** at the boundary, and
that behaviour is defined:

```rust,run
module wrap

choose runtime

fn main() {
    let small: u8 = 255;
    let plus: u8 = small + 1;
    print_num(plus);
    print("\n");
    let signed: i8 = 127;
    let over: i8 = signed + 1;
    print_num(over);
    print("\n");
}
```
<!-- expect: 0
-128 -->

The unsigned byte wraps to 0, and the signed byte wraps to -128. There is no overflow trap and no
saturating variant. When a computation can exceed its type, choose a wider type or check the
bound before doing it.

Division and remainder by zero abort the process. They do not return a value and there is no
error to catch:

```rust
let q: u32 = a / 0;      // the process stops
```

## `bool`

`bool` is the only type a condition accepts. `if x { … }` requires `x: bool`; an integer is
refused rather than treated as zero/non-zero.

```rust
let ready: bool = true;
let done: bool = !ready;         // false
let both: bool = ready and done; // false, short-circuit
```

`and`, `or` and `not` are Tur 2026.2 spellings for `&&`, `||` and `!`. Both forms compile to the
same code.

## `str`

A `str` is a pointer and a byte length, copied as a pair. It is not a growable string and there is
no `+` operator. The builtins are the API:

| Builtin | Meaning |
|---|---|
| `str_len(s)` | byte length |
| `str_byte(s, i)` | the i-th byte |
| `str_eq(a, b)` | content comparison |
| `str_concat(a, b)` | concatenation, allocated on the runtime heap |
| `str_ptr(s)` | the data pointer |

String literals live in the artifact's read-only data. A `str` value can point at them, at a
buffer you allocate, or at memory received from a system call. The length travels with the
pointer, so a `str` never scans for a terminator.

Chapter 14 covers text in detail, including UTF-8 and the standard library's `text` module.

## `ptr`

`ptr` is an untyped address. It converts to and from `u64` with `as`, and the byte operations
`load8`, `store8`, `ptr_add` and `ptr_sub` work on it. The type carries no size, no alignment and
no ownership:

```rust
let p: ptr = alloc(16);
store8(p, 0, 65 as u8);
let b: u32 = load8(p, 0);
let q: ptr = ptr_add(p, 4);
```

Chapter 15 covers allocation and the 64 KiB heap.

## Arrays and slices

An array's length is part of its type:

```rust
let xs: [u32; 4] = [1, 2, 3, 4];
let first: u32 = xs[0];
xs[0] = 9;
```

A slice is a borrowed view and appears in parameter position:

```rust
fn sum(xs: [u32]) -> u32 { … }
fn bump(xs: mut [u32]) { … }
```

The caller passes `&xs` or `&mut xs`. Chapter 13 covers the move and borrow rules that go with
these two forms.

## Unit

A function without `-> T` returns `()`. There is no value of type `()` to store, and a `return`
in such a function takes no expression (Tur 2026.2 allows the bare `return;`).

## Structs and enums

These are declared types and are covered in chapters 10 and 11:

```rust
struct Entry {
    cents: u32,
    tax: u32,
}

enum Kind {
    Small,
    Big(u32),
}
```

Both are values. Passing one to a function, or assigning it to another name, moves it. The old
name is no longer usable afterwards (E006).

## What is not in the language

- **No floats.** Use the standard library's `f64` module, which stores an IEEE-754 value in a
  `u64`, or scale your values into integers.
- **No `char`.** Text is UTF-8 bytes; decode a codepoint when you need one.
- **No tuples.** Return a struct, or pass an out-pointer.
- **No `String`, `Vec` or `HashMap` types.** The standard library provides modules, not language
  types.
- **No function-pointer type or closure.** `thread_spawn` takes a function *name*, and generic
  functions are monomorphised.
- **No `&T` in type position.** `&` exists in call arguments for slices only.

A type that is not in the table is a name the compiler cannot resolve (E030). The fix is to
declare it, import it, or pick a language type.

## Type annotations are mandatory

```rust
let x: u32 = 1;      // correct
let y = 1;           // syntax error: expected `:`
```

The rule applies to every `let`, to every parameter, and to every `const`. It applies to struct
fields and enum payloads as well. A language with mandatory annotations is more verbose than one
with inference, and it makes the width of every intermediate value visible at the point where it
matters.

The next chapter covers the operators that combine these values, and the rule that keeps their
widths aligned.
