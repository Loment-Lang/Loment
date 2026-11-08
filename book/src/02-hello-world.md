# Hello, world

The first chapter printed a fixed string. A language needs numbers as well, and Loment has no
`printf`, so this chapter builds the two pieces of output the rest of the book uses: `print` for
text and `print_num` for integers.

## A program has a module and an entry

```rust,run
module hello

choose runtime

fn main() {
    print("hello from Loment\n");
    print_num(2026);
    print("\n");
}
```
<!-- expect: hello from Loment
2026 -->

Every file starts with `module <name>`. The name identifies the unit; it does not have to match
the filename. `choose runtime` is a project dimension (chapter 30): it asks for the runtime block,
which carries `print_num`, `alloc`, `guard` and the collector. A program that writes only
`print` can omit it; the examples in this book include it because they also print numbers.

`fn main()` takes no parameters and returns nothing. The linker's stub calls it and exits with
status 0 when it returns.

## Values and types

Every binding carries a type:

```rust
let count: u32 = 42;
let big: u64 = 5000000000;
let signed: i32 = -7;
let flag: bool = true;
let name: str = "Loment";
let p: ptr = alloc(16);
let a: [u32; 3] = [1, 2, 3];
```

| Type | What it holds |
|---|---|
| `u8 u16 u32 u64 i8 i16 i32 i64` | integers, unsigned and signed |
| `bool` | `true` or `false` |
| `str` | a pointer plus a byte length |
| `ptr` | a raw address |
| `[T; N]` | a fixed-size array |
| `[T]` and `mut [T]` | a borrowed slice, used as a parameter type |
| a struct or enum name | a value of that type |

There are no floats. If you need fractional values, scale them into integers (`314` for `3.14`),
or use the standard library's `f64` module, which stores IEEE-754 bit patterns in a `u64`. There
is no `char` either: text is UTF-8 bytes, and a character is a byte or a codepoint you decode
yourself.

Conversions never happen by themselves. `x as u64` is the whole story, and both sides must be
integers or a pointer.

## Printing a number

`print` takes a `str` and writes its bytes to standard output. `print_num` takes any integer
type, writes its decimal form, and adds no newline:

```rust,run
module numbers

choose runtime

fn main() {
    let a: u32 = 7;
    let b: i32 = -3;
    print("a=");
    print_num(a);
    print(" b=");
    print_num(b);
    print("\n");
}
```
<!-- expect: a=7 b=-3 -->

The two calls are builtins, and they are the whole output layer of the language. Everything else
is a system call or a library.

Before `print_num` existed, every program carried its own decimal printer: allocate a buffer, fill
it from the end, reverse it, call `write`. Chapter 8 shows that version, because the same
technique is what you need for hexadecimal and for fixed-width fields. For ordinary debugging,
`print_num` is the shorter path.

## String escapes

String literals accept exactly five escapes:

| Escape | Byte |
|---|---|
| `\n` | newline |
| `\t` | tab |
| `\r` | carriage return |
| `\0` | NUL |
| `\\` | backslash |
| `\"` | double quote |

Any other escape is a syntax error, and the diagnostic names the accepted set. `"\x41"` is
refused; write `"A"`. The restriction is deliberate: a string literal is a byte sequence, and a
silent interpretation of an unknown escape would change those bytes.

```rust,run
module escapes

fn main() {
    print("tab:\there\nquote:\" done\n");
}
```
<!-- expect: tab:	here
quote:" done -->

## Comments and layout

```rust
// a line comment
/* a block comment */
/// a doc comment; `loment doc` extracts these
```

Whitespace is free-form. Statements end with `;`. `module` and `use` take no semicolon. There is
no semicolon after a `struct`, `enum`, `fn`, `trait` or `impl` body.

## Exit status

A program that returns from `main` exits 0. A program can choose the status itself:

```rust,run
module status

fn main() {
    print("exiting with 0\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: exiting with 0 -->

The second argument of the `exit` call is the status. Replace `0` with `3` to exit with 3; the
book's gate requires a runnable block to end at 0, so the example keeps the successful value.
Chapter 19 turns that number into a test result.

## The shape you will see again

Almost every program in this book follows the same outline:

```rust
module <name>

choose runtime

fn helper(...) -> T { ... }

fn main() {
    // build values
    // call helpers
    // print the result
}
```

The module names the unit, `choose` states a property of the whole artifact, helpers carry the
logic, and `main` is a thin driver. When a chapter introduces a new construct, it slots into one
of those four places.
