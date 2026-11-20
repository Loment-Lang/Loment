<!-- translated-from: docs/143-l1-loment-v0.md -->
<!-- source-sha256: 5c435f3f723cab6bf8f7730a3da1992fded8f353ea63fd9d36218d7b8a7df3fe -->

# 143 · L1 Loment v0: language specification and compiler

> Status: **implemented and passing self-check** (2026-09-08) · Compiler: `tools/lomentc.py`
> Self-check: `tools/lomentc_test.py` **106/106** · Example: `loment/examples/demo.lomt`
> In one line: **the native syntax is the Rust flavour, adding only the one semantic layer of "capability
> declaration"; the surface syntax may be more than one (`docs/188`), but the semantics are always the set
> this specification states.**
>
> **A wording correction on 2026-09-18 (not a language change)**: this file used to say "the syntax is a
> **strict subset** of Rust". That sentence can no longer describe the language — the surface syntax is more
> than one, and constructs like `capability` / `guard` / `comefor` / `command` were never in Rust to begin
> with. What changed was the **claim**; not one rule in the body of the specification was touched (the
> criterion `lomentc_test` staying at 106/106 is the evidence). What it ever bought was the prior over the
> **overlap**, not "every Loment program is legal Rust".

## 1. Positioning and the two hard constraints

L1 is the language proper of the three-layer architecture in docs/140: **the one language in which a person / an agent writes the system**.

1. **The syntax is not new; the semantics are** (docs/140 §4): the **native** surface syntax is the same shape as Rust — `module` / `use` / `fn` / `let` / `if` / `while` / `return` match Rust, the type names match, the operators match. The only addition is the `capability` declaration. This lets an LLM's Rust prior transfer directly, sidestepping the "zero corpus" veto of docs/110 §1.
2. **Translate first, self-host later**: v0's backend is a **Loment → Rust translator**, not a native backend. The feasibility criterion = **it runs without writing a backend** (see the measurements in §5).

Loment is a superset of `.lom` (L0): **declarations come from L0, behaviour comes from this file**. Layouts/constants are brought in with `use "<path>.lom"`, and the compiler inlines them into the artifact and exports them to the Potato formal object.

## 2. Syntax

```
module <ident>

use "<relative .lom 路径>"                                  // L0 布局复用 (可多次)
use "<相对路径>"                                            // L1 模块导入: 路径形式 (可多次; 后缀任意, .lom 除外)
use <名字>                                                  // L1 模块导入: 名字形式 (可多次; 后缀见 §2.3)

extern fn <ident>(<arg>: <type>, ...) -> <type> ;           // 外部函数声明 (无函数体; 见 §3.1)

capability <ident> : <space>[<lo>..<hi>] [revocable]        // 能力声明

excluded "<说明>"                                           // 出界声明 (可多次)

register <名字> { <条目> }                                   // 命令声明: 名字在语法里 (见 §3.3)

const <NAME>: <int-type> = <int> ;                          // 整型常量

struct <Name> { <field>: <type>, ... }                      // L1 原生数据类型

enum <Name> { <Variant>[(<type>)], ... }                   // 变体可带单载荷 (v1)

fn <ident>(<arg>: <type>, ...) -> <type> {
    let <ident> : <type> = <expr> ;
    <ident> = <expr> ;
    if <expr> { ... } [else { ... }]
    while <expr> { ... }
    for <ident> in <lo>..<hi> { ... }
    match <expr> { <Name>::<V>[(<bind>)] => { ... }  _ => { ... } }
    return <expr> ;
    <call> ;
}
```

- Types: `u8 u16 u32 u64 i8 i16 i32 i64 bool`, a declared `struct` name, a fixed-length array `[T; N]`
- Expressions: literal / identifier / call / field access `a.b` / subscript `a[i]` / enum path `E::V` /
  struct literal `Name { f: e }` / array literal `[e, ...]` / unary `- !` / binary operators
- Operator precedence (low → high, same as Rust): `||` · `&&` · `== !=` · `< <= > >=` ·
  `|` · `^` · `&` · `<< >>` · `+ -` · `* / %`
- Comments: `//` and `/* */`
- Struct literals are forbidden in condition position (the ambiguity of `if p { }` is resolved by Rust's rules)

## 3. Semantic rules (enforced at generation time, `lomentc.check`)

| Rule | Criterion |
|---|---|
| Names unique | function names, capability names and parameter names must not repeat |
| Defined before use | a variable must be `let` before it is used or assigned |
| Types agree | `let x: T = e` and `x = e` require the type of `e` to equal `T` (an integer literal takes its width from context) |
| Condition is boolean | the condition type of `if` / `while` must be `bool` |
| Return type | the type of `return e` must equal the function's declared return type |
| Call exists | the callee must already be defined; the number of arguments must match |
| External function | `extern fn` has only a signature, no body; the signature takes only scalars and `ptr`; it must not share a name with an ordinary function (see §3.1) |
| Capability domain well-formed | `0 ≤ lo ≤ hi` |
| Struct well-formed | the name must not collide with a base type; field names are unique and non-empty; field types must already be declared |
| Struct literal | no field may be missing, repeated, or unknown |
| Field access | a field may be taken only from a struct, and the field must exist |
| Constant | the name must not collide with a struct / function / other constant; the type must be an integer type |
| Array | the length must be positive; an array literal must not be empty, its element types must agree, and its length must match the declaration; subscript applies only to arrays and the subscript must be an integer |
| Assignment lvalue | may only be a variable or an array element (`a[i] = e`) |
| Enum | the name must not collide with a base type/struct; variant names are unique and non-empty |
| `match` | the subject must be an enum; each pattern must be a variant of that enum; patterns must not repeat; it must be exhaustive (cover all variants or carry `_`) |
| Payload | the payload type must already be declared; a variant with a payload must bind a variable in the pattern, one without must not; the bound variable has the payload type in scope inside the arm; the construction `E::V(e)` is legal only for a variant with a payload, and the argument type must match |
| `for` | the bounds must be integers; the loop variable's type is taken from the integer type of the bounds (when both are literals, the default is `u32`) |
| Module import | **two forms**. The path form `use "a/b.lomt"` resolves relative to the **repository root** or the **directory of the importing file** (the former first, the latter second); the name form `use name` **searches level by level, first hit first used**: ① project-local `<project root>/deps/<name>/<name><suffix>` (project root = the directory of the entry file; where lompi materialises) ①b project-local **in-package modules** `<project root>/deps/<package>/<name><suffix>` ② **the four built-in roots** `<name><suffix>` under `loment/lib`, `loment/examples`, `loment/selfhost`, `loment/tools` (this level requires a **unique hit**: not found, or more than one hit, is an error — it does not silently take the first) ③ libraries shipped with the toolchain `<store>/<name>/<version>/<name><suffix>` (`<store>` has two candidates: the installed prefix `<tool dir>/../share/lompi/store`, or a development checkout's `<repo root>/lompi/store`; the version level **allows only one**: the compiler does no version selection, and more than one is an error pointing at `deps/`) ③b **in-package modules** in shipped libraries `<store>/<package>/<version>/<name><suffix>`. **Whether ② or ③ comes first depends on which side the importer lives on**: if the importer is itself in the store (under one of ③'s two `<store>`s) it searches ③ before ②, and every other file the other way round — both sides have same-named modules (`mem`, `interp`), and both directions really do collide (see the criterion in §6). ①/①b always come first. **The two "in-package module" levels (①b/③b) error on more than one hit** (when both packages have `<name>.lomt`, "which is found first" must not become hidden semantics). **The suffix is not part of the language**: only `.lom` is special (the L0 interface contract, split out by suffix), every other suffix is L1 source. Which suffix the name form uses is fixed by the **project's `loment.conf`** (§2.3), defaulting to `.lomt`; a configured custom suffix **still falls back to looking for `.lomt`**, so shipped modules are not lost because of it. The path form carries its own suffix and is unaffected by the configuration; **companion files in a package's own directory must use the path form**. The **cap of 300** uses of the two forms in one file (counted together); past that it reports E020 — over the cap it errors, and **does not silently drop**. The same module is resolved only once; a circular import is an error; a symbol declared in this module must not share a name with an imported symbol |
| Constant visibility | a module-level `const` may be referenced directly inside a function body |

### 2.3 Suffixes and `loment.conf`

**The suffix does not belong to the language, it belongs to the project.** The only suffix with semantics in the syntax is `.lom` (the L0 interface contract): `use "x.lom"` is L0 reuse, and every other suffix (`.lomt`, `.foo`, whatever) is L1 source. So a project can do entirely without `.lomt` and invent its own suffix the way C does — Loment is "a substrate other things can grow on", not a tool that recognises only one filename.

Which suffix the **name form** looks for is fixed by the `loment.conf` in the **project root** (or beside the toolchain):

```
// <项目根>/loment.conf —— 与 lompi.conf / pkg.lomp 同一种形状
module conf

pub fn source_ext() -> str { return ".foo"; }
```

Decision rules (**decidable sentences**; the two implementations must agree):

| Condition | Result |
|---|---|
| No `loment.conf` | use the default `.lomt` |
| There is one, but the identifier `source_ext` cannot be scanned | use the default |
| The value of the **first string literal** after `source_ext` does not start with `.` | use the default |
| The value contains `\` (an escape) | use the default |
| Otherwise | use that value |

- **Comments do not count**: what is read is the **lexical stream**, so the one in `// source_ext ".bar"` does not count (the same approach `lompi` uses to read `lompi.conf` / `pkg.lomp` — a tag scanner, not a full parser).
- **Project before toolchain**: `<project root>/loment.conf` is looked at first, then `<tool dir>/loment.conf` (the global default).
- **It governs the name form only**: the path form `use "area.foo"` carries its own suffix, and the configuration has no say.
- **The default always falls back**: after configuring `.foo`, the candidate order is `.foo` then `.lomt`; the repository's built-in modules and the bundled store are all `.lomt`, and without this step, changing the suffix is the same as losing the standard library.

**Why "if it cannot be read, treat it as unconfigured"**: a config file is meant to be edited by a person, and one broken letter would point the name form at a pile of strange files, far worse than "fall back to the default" — the former is a string of incomprehensible compile-time errors, the latter at least still compiles.

### 3.1 External functions (`extern fn`)

**Shape**: only a signature, no body, terminated by a semicolon.

```
extern fn c_add(a: i32, b: i32) -> i32;
extern fn c_memset(p: ptr, v: i32, n: u64) -> ptr;
```

**Semantics**: this name is not defined in this Loment source; it is **provided by an external object file that gets linked in**. A call site passes arguments by the **platform C ABI** (Linux/ELF: System V — `rdi rsi rdx rcx r8 r9`, return value `rax`; Windows/PE: Microsoft x64 — `rcx rdx r8 r9`), while **a call purely between Loment code still uses Loment's own convention** (arguments on the stack). The two conventions coexist, dispatched on whether the callee is extern.

**Decision rules** (decidable; the two implementations must agree):

| Condition | Result |
|---|---|
| `str` appears in the signature | error (`str` is ptr + length, not a C string) |
| An array / slice / struct / enum (by value) appears in the signature | error |
| A parameter type is `i8..i64` / `u8..u64` / `bool` / `ptr` | legal |
| The return type is one of those, **or the whole `-> T` is omitted** (i.e. no value is returned) | legal |
| It shares a name with an **ordinary function** in the same module (or imported unit) | error (E013's duplicate-name convention) |
| **Two modules each declare the same external name** | **legal** — they mean the same external symbol (equivalent to including one header twice in C), and `declare` emits only one |
| Declared but never called | legal, but **produces** no reference |

**Why aggregates and `str` are not accepted**: in the C ABI, "passing a struct by value" means splitting it into registers by field (System V's classification rules), and between `str` and a C string there is an extra conversion layer to bridge (who is responsible for the NUL terminator, who is responsible for freeing). Both **change the shape of the call-site code**, so only scalars and `ptr` are opened up for now — any other shape **errors out, rather than silently miscompiling**.

**Omitting the return type means `void`**: `extern fn c_free(p: ptr);` is legal and means that C function returns no value. (In this language `void` is always written as "omit `-> T`"; `-> ()` is not a legal type name — the same rule as for ordinary functions.)

**Relation to the freeze surface**: this changed two columns of the freeze surface, "syntax and type rules" and "emitted symbol conventions", and went through the four procedures of `docs/158` §5, recorded in that file's §5 entry for 2026-09-16.

### 3.2 Project mode (`choose`)

**Shape**: one top-level declaration carrying only a mode name.

```
choose no_std      // 或
choose std
```

**Semantics**: it declares the run mode of the **whole program**, not of a file or a module. The two modes differ in **whether the hosted layer may be used** (libc / filesystem / threads / time / network…):

| Mode | core (freestanding) | hosted layer |
|---|---|---|
| `std` | ✅ | ✅ |
| `no_std` | ✅ | ⛔ referencing it is an error |

**Core** means the pieces that already exist: `alloc` / `free` / `str_*` / the syscall wrappers / the platform shims, plus the few in `loment/lib/` that do not depend on a host. **`no_std` is not "nothing at all", it is "only the core"** — Loment was founded from the start to write FujoOS, freestanding is its true nature, and hosted is the layer added on top (`docs/175` §4).

**Decision rules** (decidable; the two implementations must agree):

| Condition | Result |
|---|---|
| No `choose` written | **`std`** (the default) |
| `choose std` | `std`, equivalent to omitting it; **recommended** (makes the convention explicit), but not required |
| `choose no_std` | `no_std` |
| **Written twice** (any combination) | error |
| It appears in a **non-root unit** (a library brought in by `use`) | error — **a library may not `choose`** |
| A `no_std` project references something in the hosted layer | error, **with the chain of demand sources** |

**Why "at most once" and only in the root unit**: it is an **invariant of the whole program**, so the compiler can stop "half hosted, half not" **globally** (that mixture only blows up at link time, which is the hardest kind to chase). Relaxing it to a per-file / per-library declaration would fall into the known composition pain of Rust's `#![no_std]` — and Loment's unit model (a library = a directory, dependencies = the `use` lines in source) would fall into the same pit.

**How a library expresses what it needs**: **declare capability requirements, not a mode**. If some library in a `no_std` project wants the hosted filesystem, the error **says who pulled it in** (`docs/168`'s `lib cap` already computes the capability-requirement closure, with source chains). The library writes "what I want", the project decides "what this project grants".

**Why no flexibility beyond the default (decided by the user 2026-09-17)**: omitting it = `std`, because `std` is the superset that includes the core — forgetting to write it does not fail to compile, it only pulls in surplus host dependencies; and "to use freestanding you must say so explicitly" preserves the **auditability** of that fact: the Potato formal object records `mode` and "whether it was declared explicitly" **separately**, so "the intent was freestanding but it was not declared" is still visible, without having to buy it with mandatory syntax.

**Relation to the multi-syntax frontends**: the frontends are **mode-dependent**. In a `choose std` project the Python/Java syntax frontends are usable (they need a runtime anyway); in a `choose no_std` project the Python frontend should be **refused**, or only a freestanding subset of it allowed — otherwise you create something that "looks like Python but can import nothing", which is worse than not supporting it (`docs/175` §5.4).

**Relation to the freeze surface**: this is a new top-level form = a change to the "syntax and type rules" column of the freeze surface, and the mode **must enter the Potato formal object** (otherwise the "at most once" rule cannot be audited). It went through the four procedures of `docs/158` §5, recorded in that file's §5 entry for 2026-09-17.

### 3.3 Command declaration (`register`, 2026-10-10)

**A unit can declare itself to be one `loment` command** (design: `docs/223`):

```rust
module mycmd

register mycmd {
    pub fn command_main(argv: ptr, argc: u32) -> u32 { return 0; }
}
```

The artifact is therefore called `loment-mycmd` — put it on `PATH` and `loment mycmd …` works
(the launcher's `loment foo` -> `loment-foo` lookup, `docs/169` section 3b).

**The same thing, spelled another way** (the spelling that landed first, still valid):

```rust
pub fn loment_command() -> str { return "mycmd"; }
pub fn command_main(argv: ptr, argc: u32) -> u32 { return 0; }
```

The two are **semantically the same**: both say "this unit is the command `<name>`", both require
`fn command_main`. The only difference is **where the name comes from** — the block puts it in the
grammar, the function spelling makes it a string that is **scanned** out of a function body. A
scanned name leaves a step to get wrong (a literal must sit directly after `return`, and there is
no constant folding); the block does not, which is why it is the recommended spelling.

| Where | Rule |
|---|---|
| Declaration | `register <name> { <one function> }` — the name is an **identifier**, or a string literal (a `-` is not an identifier character, so use that form for names containing one); or `pub fn loment_command() -> str { return "<name>"; }`, where the literal must sit **directly after `return`**. Only in the **entry unit** |
| Inside the block | **exactly one function** (it may carry `pub`) — more, fewer, or a `const`/`impl` is a shape error (E024). That function's **name** is not constrained by this layer: the entry rule is what requires a `command_main` in the unit |
| Name | `[A-Za-z0-9_-]`, length 1..64; it must **not collide with an official `loment` command** (official commands win, so a colliding name is unreachable) |
| Entry | `fn command_main(argv: ptr, argc: u32) -> u32` — the signature is fixed; `argv` is the whole `/proc/self/cmdline` block and `argv[0]` is the command's own path |
| Excluded | once a command is declared the unit must **not** also write `_start` — the process entry is generated (read cmdline, count the fields, call `command_main`, hand the return value to `exit`) |
| Paired | the declaration and `command_main` must **come in pairs**: one without the other is E026 (a body without a declaration is dead code; a declaration without a body only blows up at **link** time, so it is caught here instead) |
| Libraries | a **library may not declare** one: a command is the identity of an **executable**, a library is code other people `use` |

**The function in the block is an ordinary item** — it joins the function table and is emitted like
any other, so parsing, type checking and the L0/L1 analyses **never have to know `register`
exists**.

> **Why "exactly one" is nailed down** (this is a measurement, not taste): the wrapper is
> **visible** to readers that walk by brace depth (the self-host `potato`'s two function passes),
> while semantically it is **transparent** (a function in the block is a top-level function — the
> reference collects it from the AST and never sees the wrapper). If the block could hold other
> items, every depth-walking reader would have to learn to see through the wrapper, and missing
> any one of them shows up as a **missing field in the form object** — visible only on the corpus
> gate (that is exactly how it surfaced: `register_hello.lomt: line 14 differs`). With "one
> function only", the places that must see through the wrapper are down to those two function
> passes, and other items **cannot get in at all** (the shape check rejects them).

**The read is a lexer label scan** (the same read as `loment.conf`'s `source_ext`, `docs/158`'s
"the two implementations must read byte-identically"), so there is **no new AST node**;
`register` is a **contextual keyword** — it is a declaration only at the start of an item, and
elsewhere the same name is still an identifier (`let register: u32 = 1;` compiles). The function
spelling's label is `loment_command` rather than `command` because **`command` is already a
keyword** (`command <language>`, `docs/185`) — measured: `pub fn command()` does not even parse.

Diagnostics: **E024** bad command declaration (shape / name / an official name / a library),
**E025** two process entries, **E026** the command body and the declaration do not match (one
half missing / bad signature). **"Two commands in one unit" gets no code of its own** — that is the existing
**E013** (duplicate definition).

## 4. Translation contract (Loment → Rust)

| Loment | Rust |
|---|---|
| `module X` | comment header |
| `use "lom/y.lom"` | inline the constant block from `lomc.emit_rust` (the same L0 single source) |
| `capability c : s[a..b] revocable` | the four constants `CAP_C_SPACE/LO/HI/REVOCABLE` |
| `fn f(...) -> T` | `pub fn f(...) -> T` |
| `struct S { a: T }` | `#[derive(Clone, Copy)] pub struct S { pub a: T }` |
| `const C: T = v;` | `pub const C: T = v;` |
| `enum E { A, B }` | `#[derive(Clone, Copy, PartialEq)] pub enum E { A, B }` |
| `enum E { A(u32) }` | `pub enum E { A(u32) }` |
| `E::A(e)` | `E::A(e)` |
| `E::A(x) => { .. }` | `E::A(x) => { .. }` |
| `E::V` | `E::V` |
| `match x { E::A => { .. } _ => { .. } }` | same shape |
| `for i in a..b { }` | `for i in a..b { }` |
| `use "x.lomt"` | the imported modules' code is emitted first in dependency order (each once), this module last |
| `[T; N]` / `[e, ...]` | same shape |
| `a[i]` | `a[(i) as usize]` (a Rust subscript must be `usize`; Loment allows any integer type) |
| `S { a: e }` | `S { a: e }` |
| `x.a` | `x.a` |
| `excluded "..."` | enters the Potato `excluded` (does not go into Rust) |
| `let x: T = e;` | `let mut x: T = e;` |
| `if` / `while` / `return` | same shape |
| expressions | parenthesised to preserve associativity, semantics bit-for-bit identical |

Artifact determinism: no timestamps, stable declaration order, LF line endings → a byte-for-byte `--check` reconciliation is possible.

## 5. Verification evidence (measured)

The example `loment/examples/demo.lomt` (`fib` / `gcd` / `popcount` / `in_domain` + one capability declaration + `use "lom/fujr.lom"`):

```
python tools/lomentc.py loment/examples/demo.lomt \
    --emit-rust loment/build/demo.rs --emit-potato loment/build/demo.potato.json
python tools/potato.py validate loment/build/demo.potato.json      # [OK]
rustc -O -o loment/build/demo_exe.exe loment/build/demo_main.rs
./loment/build/demo_exe.exe
```

Output (verified line by line):

```
55            # fib(10)
21            # gcd(1071, 462)
8             # popcount(0xF0F0)
true          # in_domain(4)
false         # in_domain(5)
cap=disk[0..4] revocable=true
fujr magic=0x524A5546 header=64 section=32     # 来自 use 的 L0 布局
8             # blk_end(Blk { off: 3, len: 5 })   — L1 原生 struct
205           # mask_low(0xABCD, 8)  = 0xCD       — 位运算
true          # has_flag(0b1000, 3)               — 位运算 + bool
8             # MAX_BLKS                          — 常量
10            # sum_array([1, 2, 3, 4])           — 数组参数 + 下标
0 2 4 6       # fill_incr()                       — 数组元素赋值
1 2 0         # color_code(Red/Green/Blue)        — 枚举 + match
45            # sum_range(10)                     — for 0..n
40 8 3        # quadruple(10) / max_blocks() / pair_sum(Pair{1,2})
              #   ↑ 跨模块调用 double() / 模块级常量 / 导入的 struct
12 9 0        # shape_area(Circle(2)/Square(3)/Empty)  — 带载荷枚举 + 绑定
```

That is: **language → translation → native compilation → correct execution**, with capability declarations, out-of-bounds declarations, constants, enums, native structs, arrays and L0 layout constants all wired through to the Potato formal object.

## 6. Acceptance criteria

- `lomentc_test` **121/121**: positive-case parsing / translation determinism / artifact shape
  + import resolution (deduplication / cycles / missing / duplicate names)
  + **the name form's per-level search and the 300 cap** + **in-package modules reachable by name (including the ambiguity of multiple hits)**
  + **store packages do not shadow the compiler's own source (`interp`/`mem`)** + **the std facade has no conflict in the flat namespace**
  + **`loment.conf`'s custom suffix (in effect / a broken config falls back / the fallback)**
  + 33 kinds of semantic negative case + 9 kinds of Potato negative case + `--check` drift detection.
- `ci.py --static-only`'s static gate includes `lomentc_test`; **the two implementations' agreement** is carried by `loment_p8_test`'s driver gate (54/54 of the corpus byte-for-byte, plus the custom-suffix case byte-for-byte) and by `loment_rule_parity`.
- The generated Rust compiles with `rustc` and its output matches the expected output (§5).

## 7. Roadmap

| Phase | Content | Status |
|---|---|---|
| v0 | the Rust subset + capability declaration + translation + Potato export | ✅ |
| v1 | bitwise / shifts, native struct and field access, the `excluded` out-of-bounds declaration | ✅ |
| v1 | integer constants, fixed-length arrays and subscripts, array-element assignment | ✅ |
| v1 | payload-free enums + `match` (with exhaustiveness checking), the `for` range loop | ✅ |
| v1 | inter-module `use .lomt` (dependency-order emission / deduplication / cycle detection / duplicate-name checking) | ✅ |
| v1 | enums with payloads + pattern binding + construction | ✅ |
| v1 | slices (entangled with Rust's borrow semantics, and the repository has no consumer yet — deferred) | undecided |
| v2 | a native backend (LLVM or in-house), leaving the Rust translation behind | outstanding |
| v3 | self-hosting (write the Loment compiler in Loment) | outstanding |
