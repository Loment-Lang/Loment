# Switches and custom syntax

Two compile-time mechanisms let a source file contain more than the parser sees: **switches**,
which include or exclude code by a named value, and **custom syntax**, which defines new tokens
before parsing. Both are small, and both change what the compiler reads rather than what the
runtime does.

## Switches

A switch is code that is compiled only when the switch is on. The closed branch does not reach
the parser, so names inside it do not have to exist.

```rust,run
module switched

choose runtime

set choose verbose {
    pub fn banner() -> u32 { return 42; }
}

choose verbose

fn main() {
    print_num(banner());
    print("\n");
}
```
<!-- expect: 42 -->

The definition names the switch and gives the code. `choose verbose` turns it on; a missing
`choose` means off, and `choose close verbose` turns it off explicitly.

Rules:

- A file may carry many switches, up to **500**, and each name once.
- A name must be defined with `set choose <name> { … }` before it is used.
- A switch definition may not nest inside another switch body.
- **A library may not write `choose`.** It states its needs through capability declarations, and
  the project decides.

The closed branch is not parsed, which is why a reference to a name that only exists when the
switch is on is safe:

```rust
set choose tracing {
    pub fn trace(s: str) { print(s); }
}

choose close tracing

fn main() {
    // trace("…") inside a closed branch would not be parsed
}
```

### Across files: `addin`

A switch-setting unit gathers definitions for several files. It looks like this:

```rust
// chooseset.lomt
module chooseset
addin chooseset

set choose verbose {
    pub fn banner() -> u32 { return 0x5EED; }
}
```

The entry unit pulls it in:

```rust
module myapp

addin chooseset
choose verbose
```

An `addin` unit may contain only choose-related code (`module`, `addin`, the three `choose`
forms). To load functions, use `use`; `addin` is for switches. Only the entry unit may write
`addin`, and a function the entry calls must be `pub`.

## Custom syntax: `comefor` and `byuse`

`comefor` defines a new syntax at compile time. The body is a **Loment program** (entry `main`)
that reads tokens and emits tokens; its return value is how many source tokens it consumed. The
region begins after `to { … }` and ends at `byuse "word" done`.

```rust
module cfdef

comefor let "def" to {
    fn emit_s(s: str, k: u64, line: u64, col: u64) -> u64 {
        let n: u32 = str_len(s) as u32;
        let p: ptr = alloc(32);
        let i: u32 = 0;
        while i < n {
            store8(p, i, str_byte(s, i) as u8);
            i = i + 1;
        }
        return ct_syn(k, p, n as u64, line, col);
    }

    fn main() -> u64 {
        let buf: ptr = alloc(32);
        ct_tok(0, buf);
        let ln: u64 = load8(buf, 12) as u64;
        let cl: u64 = load8(buf, 16) as u64;
        emit_s("const", 0, ln, cl);
        ct_out(buf);
        emit_s(":", 3, ln, cl);
        emit_s("u32", 0, ln, cl);
        ct_tok(1, buf); ct_out(buf);
        ct_tok(2, buf); ct_out(buf);
        ct_tok(3, buf); ct_out(buf);
        return 4;
    }
}

def ANSWER = 42;

fn main() -> u64 { return ANSWER as u64; }

byuse "def" done
```

The region turns `def ANSWER = 42;` into `const ANSWER: u32 = 42;`. The four token builtins are
the whole interface:

| Builtin | Meaning |
|---|---|
| `ct_n()` | how many tokens remain from the cursor |
| `ct_tok(i, buf)` | write token `i`'s fields into `buf` |
| `ct_out(buf)` | append `buf` to the output stream |
| `ct_syn(k, txt, ln, line, col)` | synthesise a token from the body's heap |

A record is 20 bytes: kind at +0 (0 ident, 1 number, 2 string, 3 punct, 4 eof), offset at +4,
length at +8, line at +12, column at +16. Read and write it with `load8` / `store8`.

Rules:

- Definition and terminator are top-level only and cannot nest.
- A word may not be defined twice.
- Expansion happens at the token layer **before parsing**, so `comefor` and `byuse` are not
  keywords and the word remains usable elsewhere.
- `ct_out` returns a token's source position automatically; `ct_syn` needs line and column filled
  in, or errors will point elsewhere.
- `use` is not supported inside the body; write helper functions in it.

## When to use which

Use a switch for:

- optional diagnostics;
- a platform variant;
- a feature that is compiled out in release builds;
- code that depends on a tool the build may not have.

Use custom syntax for:

- a project-specific shorthand that expands mechanically;
- a dialect on top of Tur;
- a migration aid that reads old syntax into new constructs.

Both mechanisms are compile-time. Neither changes the runtime model, and neither is a macro
system with hygiene or arbitrary expansion; the surface is deliberately narrow.

The next chapter covers the audit ledger, the last of the artifact-level dimensions.
