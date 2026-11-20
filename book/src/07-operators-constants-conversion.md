# Operators, constants and conversion

Loment's operators look familiar and follow two rules that differ from most languages: **both
operands of a binary operator must have the same width**, and **conversion is always written
out**. This chapter covers the operators, the width rule, the `as` conversions, and the shape of
a constant.

## Arithmetic

The arithmetic operators are `+`, `-`, `*`, `/` and `%`. They work on any integer type, they wrap
at the type's boundary, and division by zero stops the process.

```rust,run
module arithmetic

choose runtime

fn main() {
    let a: u32 = 17;
    let b: u32 = 5;
    print_num(a + b);
    print(" ");
    print_num(a - b);
    print(" ");
    print_num(a * b);
    print(" ");
    print_num(a / b);
    print(" ");
    print_num(a % b);
    print("\n");
}
```
<!-- expect: 22 12 85 3 2 -->

Integer division truncates toward zero. `-7 / 2` is `-3`, and `-7 % 2` is `-1`. The rule is the
machine's, and it applies to signed values.

## The width rule

Both operands must have the same width. A mismatch is a type error (E001); the compiler never
widens silently:

```rust
let a: u32 = 1;
let b: u64 = 2;
let c: u32 = a + b;        // E001: widths do not match
let d: u32 = a + (b as u32);
```

A **literal** takes its width from the other operand, so `a + 0` and `a > 3` are fine: `0` and `3`
become `u32`. Two variables of different widths need an explicit `as` on one of them.

The same rule applies to comparisons, bitwise operators, shifts and assignment. `let x: u32 = …; x
= some_u64;` is a type error.

## Comparison and logic

Comparisons produce `bool`:

| Operator | Meaning |
|---|---|
| `==` `!=` | equality and inequality |
| `<` `<=` `>` `>=` | ordering |

`==` and `!=` compare integers. For strings, use `str_eq`; a `str ==` is a type error.

Boolean operators are `&&`, `||` and `!`. Tur 2026.2 adds `and`, `or` and `not` with the same
meaning and the same short-circuit:

```rust
if ready and not done { … }
if a > 0 or b > 0 { … }
```

`&&` and `and` evaluate the right side only when the left side does not decide the result.
Conditions must be `bool`; an integer is refused.

## Bitwise operators

`|`, `^`, `&`, `<<` and `>>` work on integers. There is no unary `~`; write the complement to a
width:

```rust
let a: u32 = 0;
let b: u32 = a ^ 0xFFFFFFFF;    // all ones
let c: u8 = a as u8;
let d: u8 = c ^ 255;            // all ones in a byte
```

Bitfield helpers are builtins: `get_bits(v, shift, width)` and
`set_bits(v, shift, width, x)`. The second argument is the shift and the third is the width.

## Precedence

From loosest to tightest:

| Level | Operators |
|---|---|
| 1 | `\|\|` |
| 2 | `&&` |
| 3 | `==` `!=` |
| 4 | `<` `<=` `>` `>=` |
| 5 | `\|` |
| 6 | `^` |
| 7 | `&` |
| 8 | `<<` `>>` |
| 9 | `+` `-` |
| 10 | `*` `/` `%` |

`as` binds tighter than any binary operator. Parentheses are always allowed and often clearer
than a precedence argument.

## Conversion with `as`

`as` converts between integer types, and between an integer and a `ptr`:

```rust,run
module casts

choose runtime

fn main() {
    let a: u32 = 300;
    let b: u8 = a as u8;
    print_num(b);
    print(" ");
    let c: i32 = -1;
    let d: u32 = c as u32;
    print_num(d);
    print(" ");
    let e: u8 = 255;
    let f: i8 = e as i8;
    print_num(f);
    print("\n");
}
```
<!-- expect: 44 4294967295 -1 -->

The rules follow the machine:

- **Narrowing keeps the low bits.** `300 as u8` is `44`.
- **Widening from unsigned zero-extends.** `44 as u64` is `44`.
- **Widening from signed sign-extends.** `-1 as i64` is `-1`.
- **Signedness changes reinterpret the bits.** `-1 as u32` is `4294967295`, and `255 as i8` is
  `-1`.

A conversion that does not fit these rules is E017. `bool as u32`, `str as u32` and a struct
conversion are all refused. There is no implicit conversion in either direction: every change of
width or signedness is a written `as`.

Pointers convert the same way:

```rust
let p: ptr = alloc(8);
let address: u64 = p as u64;
let q: ptr = address as ptr;
```

## Constants

A constant is a module-level name with a fixed type and a literal value:

```rust
const LIMIT: u32 = 3;
const MASK: u64 = 0xFF00;
const OFFSET: i32 = -4;
```

The initialiser is **one integer literal**, decimal or hexadecimal, optionally negative. It is not
an expression, and there are no string constants:

```rust
const A: u32 = 1 + 2;        // parse error
const NAME: str = "loment";  // parse error
```

The restriction keeps constants evaluable without a compiler at run time: a constant is a value
in the artifact, not a computation whose result depends on evaluation order.

Constants are visible to the whole unit. `pub const` makes one visible to another unit.

## Assignment

Assignment writes to a name, an array element, or a struct field. The target must be an lvalue
(E014):

```rust
x = 3;
xs[0] = 9;
```

A struct field is not an lvalue today:

```rust
let p: Point = Point { x: 1, y: 2 };
p.x = 5;                  // E014: assignment target is not an lvalue
```

To change a field, build a new literal with the new value. Chapter 10 shows the pattern.

## Operators in a real computation

A checksum is a small example that uses most of this chapter:

```rust,run
module checksum

choose runtime

fn main() {
    let bytes: [u8; 5] = [104, 101, 108, 108, 111];
    let sum: u32 = 0;
    for b in bytes {
        sum = (sum + (b as u32)) & 0xFFFF;
    }
    print_num(sum);
    print("\n");
}
```
<!-- expect: 532 -->

The loop walks the array with `for b in bytes` (Tur 2026.2), widens each byte to `u32` before
adding, and masks the result to 16 bits. Every type change is visible, and the mask is explicit.
That is the style the rest of the book follows.

The next chapter turns these expressions into functions.
