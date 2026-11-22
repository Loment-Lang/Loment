---
name: loment-traps
description: Measured Loment traps that cost the most time - syntax and type traps, runtime behavior (heap, escapes, bounds, wrapping, signals, sockets), toolchain limits, Tur 2/2026.3 traps, and the opt-in surfaces (effects, classes, function pointers, block scope). Read before writing or debugging a nontrivial Loment program. Part of the LomentSkill suite; section numbers are global.
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

1. **A `match` arm body is a block, not an expression.** `Kind::A => 0,` is a **parse error**
   ("expected `{`, got `0`"); write `Kind::A => { return 0; }`. Making arms expressions is
   Tur 2026.3 work, not a switch you can flip.
2. **Generic enum patterns use the plain enum name.** `Result::Ok(v)` on a `Result<i64, u32>` is
   accepted; the checker rewrites the pattern's enum to the monomorphised instance
   (`Result_i64_u32`). If you see `E8 与主体枚举 … 不符` (*does not match the subject enum*), you
   named a **different** enum, not the wrong instance. Non-generic enums are written normally:
   `Kind::Big(w)`.
3. **A valueless `return;` exists only in Tur 2, and only in a unit function.** In Tur 1 (or in
   a `-> T` function) `return;` gives `E19 期望表达式，得到 ';'` (*expected an expression, got
   ';'*). Either `return <expr>;`, finish with if/else, or declare `tur2` and use a `fn f()`
   with no return type.
4. **Forward references work at the top level.** Functions, structs and constants are collected
   before bodies are checked, so calling `g()` before `fn g` and using `struct S` before its
   declaration both compile. The exception is a **`const` initialiser**, which must be a literal
   (see 6.1.8) - it cannot refer to another constant.
5. **A struct literal cannot be an argument**: `f(S { a: 1 })` is rejected by the native backend
   (`native 后端不支持该表达式: StructLit`). Bind it first - `let x: S = S { a: 1 };` then `f(x)`.
6. **A struct-typed value is not a condition.** `if p { }` with `let p: P` is E001 ("if condition
   type P, should be bool"); parentheses do not help. Compare a field: `if p.a { }`.
7. **One parse error stops the whole check** - every diagnostic after it is missing. So change one
   thing at a time and re-run `loment check`; "no other errors" does not mean there are none.
8. **A `const` initialiser is one literal, not an expression.** Since Tur 2026.3 a `str`
   constant takes **one string literal** (`const GREETING: str = "hello";`, `docs/342`);
   otherwise it is one integer literal (decimal or hex, optional `-`). It is **not** an
   expression: `const B: u32 = A + 1;` and `const B: u32 = A;` are parse errors ("expected
   number, got `A`"). Long tables of offsets end up written out as plain numbers. Older
   editions keep `const` integer-only.
9. **A string constant is inlined at every use, not stored once.** `const NAME: str = "x";`
   works from 2026.3 on, but each use site gets its own `.str.<fn>.<n>` literal - it is a
   spelling, not a shared object. For a manifest label the `pub fn name() -> str { return "x"; }`
   form still works and is what `.lomp` files use (section 10).
10. **`use` takes no semicolon.** `use parse;` is a parse error ("top level allows
    use/capability/fn, got `;`"). Write `use parse` on its own line.
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
21. **Deep nesting is capped at 64 levels.** Both implementations now diagnose it (E023,
    "嵌套超过 64 层") instead of crashing; the 2026-10-18 self-hosted segfaults on nested
    parentheses / indexes / generic arguments / array types (#868-#874) are closed. Flatten the
    expression or split it into functions.
22. **A `.lomt` file with no `module` declaration is a clear diagnostic now** ("期望 module").
    Older packages crashed with SIGSEGV; 0.1.5 does not.
23. **`use std` is no longer an 18-minute SIGILL.** 0.1.4.6 fixed it; measured in 0.1.5 it
    reaches IR in about 16 seconds. It is still a facade over every module in the package -
    prefer per-module imports.

### 6.4 Tur 2 traps (the ones that are new, not inherited from Rust)

24. **`defer` is Tur 2 only, call-shaped, and capped.** `defer free(p);` runs the **call** when
    the enclosing block exits - on normal fall-through, `return`, `?`, and loop `break` /
    `continue`, but **not** on panic / trap / exit. Arguments are evaluated where the `defer` is
    written, and multiple defers in one block run LIFO. v1 takes a **call only**: `defer 1;` and
    a `defer` outside a function body are E19. The self-hosted compiler caps it at **8 per
    function, 4 arguments, 64 nesting levels** and refuses beyond that by name; the reference
    compiler has no caps. The Rust translation backend refuses `defer`. In Tur 1 the word is an
    ordinary identifier, so a user `fn defer` still works.
25. **`orelse` / `catch` are still not general expression operators.** They work only on the
    right-hand side of a `let` or an assignment, and only for an enum with **exactly two
    variants whose first carries the payload** (`Option`, `Result`). `f(a orelse 0)` and
    `return x orelse 0;` are not accepted. Since 2026.3 the RHS also accepts `catch |e| { EXPR }`
    (one expression, binds the error payload) and `catch return` (returns the error as
    `Err(…)`); the old "handler is not accepted" note is stale (`docs/343`).
26. **`for x in xs` walks a named array/slice, or a call returning one.** Since 2026.3
    `for x in f()` is accepted: the call is evaluated once into a temporary and the loop walks
    that; `for i, x in xs` also binds the index. `for x in self.items` and **array literals**
    are still refused, and `x` / `i` are **copies**, so assigning to them does not write back.
27. **Bare `return;` is for unit functions only.** The moment the signature says `-> T`, `return;`
    is E19 again - including inside an `if` that is meant as an early exit.
28. **`break` / `continue` need a loop, and in Tur 1 they are just names.** Outside a loop in
    Tur 2 they are E19. In Tur 1 a variable called `break` is legal, so a file that uses it as
    a name cannot simply be switched to `tur2`.
29. **`&T` in a type position parses from 2026.3 on - under `lifetime_auto` (the default).**
    `fn f(a: &str) -> &str`, `struct Cursor { data: &[u32] }` and `Box<&u32>` are accepted;
    `&` in a type position is a parse error only under `lifetime_no` (the default before 2026.3,
    still selectable). The boundaries: a returned borrow must come from a parameter; `free(p)`
    while `p` is borrowed is E041; a stored `&mut` excludes later borrows (E007, judged per
    place, so `v.x` and `v.y` are separate); composite generic arguments (`Box<&T>`, `Box<[T]>`)
    are refused **by name** (E041). A borrowed `&mut` local may be used **once** in the
    reference implementation; field-level partial moves are tracked by path in the reference
    (E0240), while the self-hosted side is still name-level (`docs/351`).

### 6.5 The opt-in surfaces (2026-11, each behind its own switch)

30. **There are 11 `choose` dimensions, not seven, plus two build slots.** The dimensions are
    `mode`, `gc`, `runtime`, `port`, `lvm`, `lms`, `lspe`, `lifetime`, `fp`, `class`, `flomtc`;
    the slots are `lspe_aicheck_true|false` and `lspe_effects_true|false`. A dimension is
    written **at most once and only in the entry unit**; an unknown or misspelled value is
    E022. Do not assume a feature is on because the edition is new: `fp`, `class` and the
    effects slot default to **off**.
31. **`let x := e` / `let x: auto = e` only accepts an initialiser that spells a type.** A
    local or parameter, a field chain, a whole `as T` cast, or `true`/`false` - nothing else.
    `let x := 5;`, `let x := f();`, `let x := a + 1;` and `let x := K;` are **E0215**; `auto`
    with an `if`/`match` expression is **E0216**. The `:=` must be glued (`let x: = 1` is a
    parse error).
32. **An interpolated string is a front-end rewrite, not a formatter.** `f"…"` / `rf"…"`
    accept holes of type `str`, `bool` or an integer; `{{` / `}}` escape braces; `{expr=}` is
    the debug form and `!r` quotes/escapes a `str`. **One line only**, no dynamic width and no
    nested interpolation. A hole that is a struct, an enum or an array is not accepted.
33. **Effects are reference-only, opt-in, and refused by the package.** `effect` / `effects` /
    `perform` / `handle` / `resume` are parsed only under `choose lspe_effects_true`; the
    reference implements **tail-resumptive handlers by inlining** (abort = arm without
    `resume`) with the handler in the **same function**, `handle` only as a `let` initialiser,
    `perform` only as a statement or `let` initialiser, and arms that are flat statements.
    General (non-tail) handlers, multi-shot `resume`, cross-function `perform` and capture
    writes are not implemented. **The self-hosted compiler refuses all of them with E0217**
    until the A3b-3 rewrite - so "it compiles in the package" is false today.
34. **Classes are opt-in and the two implementations stop at different points.** `class` /
    `extends` / `virtual` / `override` / `abstract` / `super` need `choose class_on`. The
    reference has C1 (layout + non-virtual methods + `self` + construction), C2 (vtables,
    virtual dispatch, `super`, abstract classes - **requires `choose runtime`**, the vtable is
    allocated) and C3 (closed-world function-pointer targets); the self-hosted side has **C4a
    only - non-virtual classes** - and still refuses vtable/virtual/override/super/abstract.
    If a class program builds on the package where the reference expected, check this boundary
    first.
35. **Function pointers and closures are a dimension (`choose fp`), not a default.** Off
    (`no_fp`, the default) every function value / closure / indirect call is **E0222**. With
    `fp`: a function type is `(u64, u64) -> u64`; a value is a bare name or `&name`; a closure
    is `(x) => expr` - **expression body only** (`(x) => { … }` is illegal); `extern fn` is not
    a value (E0223); a capture holds one 8-byte slot (integers / `bool` / `ptr` only, E0224);
    a by-ref-captured closure cannot escape (E0226); a missing param type and no expected type
    is E0225. There are no module-level function values, no `ptr`→fn cast and no FFI function
    pointers.
36. **Block scope is `tur2027.1Alpha`, not the default.** `choose write grammar
    tur2027.1Alpha` makes every `{ }` a lexical scope (inner `let` shadows; same-block second
    declaration is **E0241**; standalone `{ }` is a statement). The default `tur2026.3` and
    every older edition keep the **function-level leak**: a `let` inside an `if` is still
    visible after it. GC is conservative here: a name declared more than once in a function
    drops that **name's** L0/L1/L2 and slot masks, and its `ReclaimScoped` answer is
    `Assumed` - which is **not** in that obligation's accept set, so the site stays
    **undecided** (floored), not proved.
37. **PE and fault behaviour differ from ELF.** PE dispatches **146** Linux syscall numbers;
    every other number returns **`-ENOSYS` (-38)**, not `-1`. `--fault-report` (on `lomelf`)
    is **default off**; when on, an ELF artifact re-raises the signal (SIGSEGV = **139**), while
    a PE artifact reports a **VEH exception code** (`0xC0000005` / `0xC000001D`) and refuses
    `--fault-heal`. `choose lvm_dismemory_true` emits `; loment-memory: dismemory`, which the
    native linker **refuses** - dismemory is an LVM tier, not a native artifact.
38. **The code table has 241 fine codes, not 27.** `E001`–`E041` remain family fallbacks;
    `E0xxx` names one diagnosis each. The ones you will meet in the new surface: E0215/E0216
    (`auto`), E0217–E0221 (effects), E0222–E0227 (`fp`), E0236 (a construct outside the
    selected surface grammar), E0240 (partial move), E0241 (same-block duplicate), E040
    (new keyword collides with a name), E041 (borrow region does not hold). Use `loment codes`
    / `loment explain E0236`; do not infer a code from its family.
39. **`lnotary_true` and `lms_pangu_true` are user switches, not dimensions.** They are
    declared with `set choose NAME { }` like any user switch; `lnotary_true` additionally
    requires the artifact to declare `choose lvm` (otherwise `LNT-VM`), and `lms_pangu_true`
    changes the native fault exit to **101**. Neither is in `CORE_DIMS`; writing them as a
    core value is a different thing from writing a user switch.

