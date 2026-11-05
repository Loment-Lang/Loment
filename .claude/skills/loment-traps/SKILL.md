---
name: loment-traps
description: Measured Loment traps that cost the most time - syntax and type traps, runtime behavior (heap, escapes, bounds, wrapping, signals, sockets), toolchain limits, and Tur 2 traps. Read before writing or debugging a nontrivial Loment program. Part of the LomentSkill suite; section numbers are global.
---

## 6. The trap list (every entry was measured; reading this first saves most of the rework)

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


Rust intuition carries you through 90% (scalars / strings / slices / control flow / generics /
traits). **What is left is almost entirely in the part Loment invented.** 6.1 is language and
syntax, 6.2 is what the runtime does to you, 6.3 is what the toolchain cannot do today, 6.4 is
the Tur 2 constructs that do not behave like their namesake languages - the four are different
things, and it is worth knowing which one you are looking at.

### 6.1 Syntax and types

1. **A `match` arm body is a block, not an expression.** `Kind::A => 0,` is not accepted; write
   `Kind::A => { return 0; }`. (At this commit the *checker* no longer rejects the expression
   form, and what you get instead is a build failure from the linker - it stops on the value the
   codegen emits for it, not on the `match`. Both implementations refuse it; write the block.)
2. **Generic enum patterns use the monomorphised name.** When the matched value is
   `Result<i64, u32>`, `Result::Ok(v)` gives `E8 与主体枚举 Result_i64_u32 不符` (*does not match
   the subject enum*); the answer is **`Result_i64_u32::Ok(v)`**. Non-generic enums are written
   normally: `Kind::Big(w)`.
3. **A valueless `return;` exists only in Tur 2, and only in a unit function.** In Tur 1 (or in
   a `-> T` function) `return;` gives `E19 期望表达式，得到 ';'` (*expected an expression, got
   ';'*). Either `return <expr>;`, finish with if/else, or declare `tur2` and use a `fn f()`
   with no return type.
4. **Modules read top to bottom**: define before use, do not rely on forward references.
5. **A struct literal cannot be an argument**: `f(S { a: 1 })` is rejected by the native backend
   (`native 后端不支持该表达式: StructLit`). Bind it first - `let x: S = S { a: 1 };` then `f(x)`.
6. **A struct literal cannot sit in a condition** (`if p { }` is resolved by Rust's ambiguity
   rule); add parentheses.
7. **One parse error stops the whole check** - every diagnostic after it is missing. So change one
   thing at a time and re-run `loment check`; "no other errors" does not mean there are none.
8. **`const` initialisers are restricted**: the initialiser has to be something the constant
   folder accepts, and `const B: u32 = A + 1;` is *accepted by the checker* and then fails in the
   **build** with a backend-internal message (`nalloc=0 nslot=10 …`) rather than a diagnostic.
   Long tables of offsets end up written out as plain numbers.
9. **There are no string constants**: `pub const NAME: str = "x";` is an error (E001). For a string
   label write `pub fn name() -> str { return "x"; }` - that is how `.lomp` manifests do it
   (section 10).
10. **`use` takes no semicolon** in the house style. `use parse;` is *accepted* today, but every
    file in this repository is written without it, and a semicolon there is noise a reader has to
    wonder about.
11. **`mut` is only rewritten in the `tur` spelling.** In the default `loment` spelling `mut` is
    an ordinary identifier (`loment/selfhost/checker.lomt` really does have a variable called
    `mut`), so `let mut len: u32 = …` declares a variable named `mut` and then trips over
    `len`, pointing somewhere else. In `grammar tur*` the front end strips `let mut NAME` and
    `( mut NAME :` / `, mut NAME :` with an equal-length rewrite, so Rust-style mutable
    bindings work. Reassignment never needs a marker. **Do not rely on `mut` as a variable name
    in a file you may later switch to `tur`.**
12. **Non-ASCII output is mojibake in a Windows console.** PE writes bytes **straight to the
    console** and Windows decodes them with the current code page - 936 (GBK) on a Chinese
    Windows - so UTF-8 Chinese comes out as `婧愮爜`. **The shim has no `WriteConsoleW`** and the
    program cannot fix it from its side. Anything a person will read goes out **in ASCII**; that
    is the only set that decodes the same under every code page. (`loment`'s own help and cheat
    pages were moved from Chinese to English for exactly this reason, `docs/169` section 3a.)

### 6.2 What the runtime does to you

13. **The string escape set is exactly `\n \t \r \0 \\ \"` - and anything else is an error.**
    This was measured the hard way and **changed on 2026-10-16** (`#33`, `#36`): until then
    `"\r"` compiled to the letter `r`, `"\0"` to the digit `0`, `"\x41"` to the three bytes
    `x41`, and an unknown escape such as `"\q"` silently lost its backslash. All four are now
    **E19**, and the message names the accepted set. Older packages still have the old
    behaviour - if a literal comes out shifted, check the compiler generation first.

    What is still true: there is **no `\xNN`, no `\uNNNN`, no `\e`**, and no way to spell an
    arbitrary byte in a literal. For a byte that has no escape, write it numerically -
    `store8(p, o, 13 as u8); store8(p, o + 1, 10 as u8);` - or put the text in a raw foreign
    block (`let c { … }`, section 2.0.12), whose body is carried byte for byte.

14. **The heap is a fixed 64 KiB, and running out aborts the process.** `alloc` returns a bare
    `ptr`: no null, no error, no `Result`. Measured: `alloc(65536)` succeeds and `alloc(64)` right
    after it **kills the process with SIGILL** (exit code 132) - nothing is printed and the
    program has no way to notice. **The ceiling is the constraint, not the reclamation**: `free`
    does reclaim (a bump pointer plus an address-ordered free list, `docs/210` section 2.8) - what
    it cannot do is grow past 64 KiB, and it does not coalesce. So a service that allocates ~900
    bytes **and never frees** dies after about 72 requests, and one request asking for `n = 60000`
    kills it outright. Two ways out: free as you go, or bring in a host - under `choose hosted`
    the arena comes from the host's `malloc` and is 1 MiB. If you are writing something that
    serves, this is the first constraint to design around.

15. **Array and slice indices are not bounds-checked; the default LMS tier checks the
    alloc-derived accesses it can see.** The answers differ by site:
    - **Literal index, out of range, fixed-size array** - `xs[5]` on `[u32; 3]` - is
      **refused by `check` (E028)**, at compile time, in the default tier.
    - **Any other array/slice index** - a variable, an expression, or anything into a slice
      `[T]` - is **unchecked at runtime**: a little out of range writes elsewhere with no trap,
      and `xs[100000] = 1` **segfaults** the process.
    - **`load8`/`store8` on an `alloc(<const>)` block** are checked under the default
      `lms_full` (level 3B): level 4 skips the out-of-range access (read returns 0, the write
      does not happen) or level 5 traps. See `docs/270` section 1.

    An index that comes from outside your program - a request, a file, a length field - is
    therefore a memory-safety question, and the language will not raise it for you. **Guard it
    yourself**: a fixed-size array has a compile-time length, so write `if i >= 3 { … }`; a
    slice carries its own length, so `slice_len(x)` gives it to you. The old `choose lms_open`
    escape hatch is gone; select a tier with `choose lms_*` (section 2.1).

16. **Integer arithmetic is defined to wrap, there is no checked arithmetic, and division by
    zero kills the process.** The wrap is a **rule, not an accident**: `4294967295 + 1` is `0`
    and `0 - 1` is `4294967295`, on purpose, and the emitted IR never carries LLVM's
    `nsw`/`nuw` - so this is defined behaviour, not the undefined behaviour C leaves you with.
    Nothing traps and nothing warns, so a length or a size computed from untrusted input can be
    quietly wrong. There is no `checked_add` / `checked_mul` to reach for; write the guard
    yourself, *before* the arithmetic, e.g. `if a > 4294967295 - b` for `a + b` on `u32`.
    `a / b` and `a % b` with `b == 0` **abort the process** (SIGILL, exit code 132) - the
    compiler emits the trap, so it is a defined abort rather than whatever the CPU happens to do;
    check the divisor first if a zero can reach you. (Both rules are settled in `docs/144`
    section 3 - the note there used to read "the rule must be settled before M2".)

17. **A non-`()` function that can fall off its end is refused by `check` (E027).** Write
    `fn f() -> u32 { … }`, forget the final `return <expr>;`, and `loment check` says so - before
    2026-10-10 it **passed** and the binary died with `Illegal instruction` when execution reached
    that path (measured while writing `loment/tools/lomcli.lomt` on 2026-09-15: a 40-line
    directory walk crashed exactly here). The rule is structural: the body must end with `return`,
    with an `if`/`else` where both branches return, or with a `match` whose every arm returns.
    This is also why early exits are written `return 0;` rather than a bare `return;` (6.1.3). A
    function that exists only to allow early exits can still be declared `-> u32` and end with
    `return 0;`, and callers ignore the value.

18. **SIGPIPE is not ignored, so a peer that hangs up can kill you.** Writing to a socket whose
    peer has gone raises SIGPIPE, whose default action terminates the process. Measured: the first
    write after the peer's RST returns `-104` (ECONNRESET, recoverable), and **the second one kills
    the process with exit code 13**, printing nothing. A response written as headers-then-body, or
    any body larger than the socket buffer, puts a write after the RST. Either ignore the signal or
    use `send`(44) with `MSG_NOSIGNAL` (`0x4000`) instead of `write`.

19. **A blocking `read` on an accepted socket blocks forever unless you set a timeout.** Measured
    on a live server: one client that connects and sends **nothing** takes the whole service out of
    service - every other request times out with zero bytes until that client goes away. A
    half-sent request line and a client dribbling one byte every 300 ms do the same. The primitive
    to fix it exists - `setsockopt`(54) with `SO_RCVTIMEO` (20) and a `struct timeval` of two
    64-bit fields, through `syscall6` - and it works: measured, a `read` on a socket nobody writes
    to returned `-11` (EAGAIN) after 1107 ms with a one-second timeout. This guide used to say
    nothing about needing it; now it does.

### 6.3 What the toolchain cannot do today

These are defects rather than language rules, and each one is here because meeting it without
knowing costs half a day. The text says which ones 0.1.5 already fixed.

20. **Several name-form `use` lines are fine in 0.1.5** (verified: `use vec` plus `use numfmt`
    compiles). Older packages stopped at one with `loment: cannot open <garbage>`, and on those
    the path form was the workaround; a unit with imports also lost most of its string-literal
    capacity (measured: eight ~34-byte literals could bring the old compiler down).
21. **Deep nesting is still a wall, but the number moved.** The reference parser refuses nesting
    beyond **64 levels** with a diagnostic ("嵌套超过 64 层"); the self-hosted parser still
    segfaults on some deeply nested forms (nested parentheses / indexes / generic arguments,
    #868-#874). Flatten it or split it into functions.
22. **A `.lomt` file with no `module` declaration is a clear diagnostic now** ("期望 module").
    Older packages crashed with SIGSEGV; 0.1.5 does not.
23. **`use std` is no longer an 18-minute SIGILL.** 0.1.4.6 fixed it; measured in 0.1.5 it
    reaches IR in about 16 seconds. It is still a 133-module facade - prefer per-module imports.

### 6.4 Tur 2 traps (the ones that are new, not inherited from Rust)

24. **`defer` does not exist yet.** It is designed in `docs/296` §2.3 and listed for Tur 2, but
    it is not implemented. `defer free(p);` is parsed as an expression statement and fails.
    Use an explicit `free` on every exit path.
25. **`orelse` / `catch` are not general expression operators.** They work only on the right-hand
    side of a `let` or an assignment, and only for an enum with **exactly two variants whose
    first carries the payload** (`Option`, `Result`). `f(a orelse 0)`, `return x orelse 0;` and
    `catch |e| { … }` are not accepted.
26. **`for x in xs` only walks a named array or slice variable.** `for x in f()` and
    `for x in self.items` are Tur 2026.3. A slice parameter works (`xs: [u32]`); a fixed array
    works (`let xs: [u32; 4]`). `x` is a **copy**, so assigning to it does not write back.
27. **Bare `return;` is for unit functions only.** The moment the signature says `-> T`, `return;`
    is E19 again - including inside an `if` that is meant as an early exit.
28. **`break` / `continue` need a loop, and in Tur 1 they are just names.** Outside a loop in
    Tur 2 they are E19. In Tur 1 a variable called `break` is legal, so a file that uses it as
    a name cannot simply be switched to `tur2`.
29. **`&T` in a type position does not parse.** `struct V { data: &str }`, `fn f(a: &str) ->
    &str`, and a struct field of `str`/`ptr` are all refused. Borrowing is argument-only; use
    `ptr` + an explicit length when the value has to be stored.

