# Text and bytes

A `str` in Loment is a pointer and a byte length. It is a value, not a growable string, and it is
not NUL-terminated. The language provides six string builtins; everything else is a library or a
system call.

## The value

```rust
let name: str = "Loment";
let n: u32 = str_len(name);       // 6 bytes
let first: u32 = str_byte(name, 0);   // 76, 'L'
```

String literals live in the artifact's read-only data. A `str` can also point at a buffer you
allocate, at memory returned by a system call, or at a slice of another string. The length
travels with the pointer, so no operation scans for a terminator.

## The builtins

| Builtin | Meaning | Cost |
|---|---|---|
| `str_len(s)` | length in bytes | constant |
| `str_byte(s, i)` | the i-th byte as `u32` | constant |
| `str_eq(a, b)` | content equality | proportional to length |
| `str_concat(a, b)` | concatenation | allocates from the runtime heap |
| `str_ptr(s)` | the data pointer | constant |
| `print(s)` | write the bytes to standard output | one `write` |

`str_eq` compares contents, not pointers. There is no `str ==` operator.

`str_concat` allocates. In a loop it consumes the 64 KiB arena quickly; build into a buffer with
`store8` when the output grows.

## A complete example

```rust,run
module text

choose runtime

def eq_report(a: str, b: str) {
    if str_eq(a, b) {
        print("equal\n");
    } else {
        print("different\n");
    }
}

fn main() {
    let a: str = "loment";
    let b: str = str_concat("lo", "ment");
    eq_report(a, b);
    print_num(str_len(a));
    print(" ");
    print_num(str_byte(a, 0));
    print("\n");
}
```
<!-- expect: equal
6 108 -->

`str_concat("lo", "ment")` produces the same bytes as the literal, and `str_eq` reports them
equal. The byte value of the first character is 108.

## Bytes and UTF-8

Text is UTF-8. `str_len` counts **bytes**, not characters, and `str_byte` returns one byte:

```rust,run
module utf8bytes

choose runtime

fn main() {
    let s: str = "é";
    print_num(str_len(s));
    print("\n");
}
```
<!-- expect: 2 -->

`é` is two bytes in UTF-8. A character-aware operation needs to decode the sequence; a byte-wise
operation can use `str_len` and `str_byte` directly. The standard library's `utf8` module does the
decoding when you need codepoints.

## Escapes

String literals accept exactly these escapes:

| Escape | Byte |
|---|---|
| `\n` | 10 |
| `\t` | 9 |
| `\r` | 13 |
| `\0` | 0 |
| `\\` | 92 |
| `\"` | 34 |

Any other escape is a syntax error. `"\x41"` is refused; write `"A"`. Non-ASCII text is written
directly in the source, which is UTF-8.

## Comparing and ordering

- Equality: `str_eq(a, b)`.
- Ordering: compare bytes with `str_byte` and `str_len`; the standard library's `text` module
  provides the usual comparisons.
- Prefix and suffix: compare the relevant prefix with a loop, or use `text`.

There is no locale-aware comparison in the language. A byte comparison is the primitive, and a
library decides what else "ordered" means.

## Building text

`str_concat` is convenient for a small number of pieces. For a larger string, allocate a buffer
and write into it:

```rust,run
module build

choose runtime

def put(buf: ptr, at: u32, s: str) -> u32 {
    let i: u32 = 0;
    let n: u32 = str_len(s);
    while i < n {
        store8(buf, at + i, str_byte(s, i) as u8);
        i = i + 1;
    }
    return at + n;
}

fn main() {
    let buf: ptr = alloc(32);
    let at: u32 = put(buf, 0, "lo");
    let end: u32 = put(buf, at, "ment");
    syscall4(1, 1, buf as u64, end as u64);
    print("\n");
}
```
<!-- expect: loment -->

The function returns the new write position, so calls compose. This is the shape to use when the
text is produced in pieces or in a loop.

## The standard library

The toolchain ships modules for text work. `use text` covers searching, splitting, trimming and
case conversion; `use utf8` covers codepoint iteration; `use parse` covers integers and floats;
`use fmt` and `use numfmt` cover formatting.

```rust
module myapp

choose runtime

use text
use utf8
```

Several name-form `use` lines are allowed in 0.1.5. Each module is an ordinary unit: it exports
`pub fn`s, and the calls are direct.

## Boundaries

- `str` is not NUL-terminated. A system call that expects a C string needs a buffer with a
  trailing `\0`.
- `str_len` is bytes. Codepoints and display width are library concerns.
- `str_concat` allocates; repeated concatenation is the slow path.
- There is no string comparison operator; use `str_eq`.
- There is no formatting language. Formatting is a module that returns text, or a sequence of
  `print` calls.

The next chapter goes below the text value to the raw memory it points at.
