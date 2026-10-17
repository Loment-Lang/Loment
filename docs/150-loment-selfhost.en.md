<!-- translated-from: docs/150-loment-selfhost.md -->
<!-- source-sha256: a6f9a3d47a7ad73cfb2bb2041fc41550ce09a17addba2ecd230259aaeffbed8e -->

# 150 · Loment self-hosting (P8, M79–M88)

> Status: **M79–M84 done** (2026-09-11) · Self-check: `tools/loment_p8_test.py` **7/7**
> M82 byte-identical: **target 39/39** (10 `ir_*.lomt` anchors + `toolchain`/`native_bits`/`native_mem`/
> `native_str`/`native_gen`/`native_trait`/`native_res`/`native_brk`/`bytes`/`native`/`ahci`/
> `fuc_node`/`allocator`/`mathutil`/`user_hello`/`bootprobe`/`demo`/`all_loment`/`selfcheck`/
> `lexer`/`parser`/`checker`/`codegen`/`driver` …).
> The one non-target, `native_raii.lomt`: the reference implementation itself reports `native: inb 暂未在 IR 后端实现` ("native: inb not yet implemented in the IR backend").
> **All four self-hosting stages can compile themselves**, and there is a **driver that runs on its own** (`loment/selfhost/driver.lomt`):
> the unit it compiles for itself is byte-identical to `lomentc --emit-llvm` (973238B), and relinking once more with its own output is still identical.
> Gate: `ci.py` static items 8 PASS (the other 4 fail for want of the `LinuxFUAI/` private library — not a Loment regression)

## M79 · The Loment lexer ✅

`loment/selfhost/lexer.lomt`: a lexer written in Loment; input is a byte buffer, output is 20B/token records
(`kind|start|len|line|col`). The rules match `lex()` in `tools/lomc.py`:

- whitespace / `//` line comments / `/* */` block comments;
- strings (with `\` escapes; the span includes the quotes);
- decimal and `0x` hexadecimal numbers;
- identifiers (`[A-Za-z_][A-Za-z0-9_]*`);
- the single-character punct set `{}()[]:;=@,.+-*/%<>!&|^?`;
- a trailing `eof`.

**Criterion (token stream matches the Python version)**: `loment_p8_test.py` compiles the lexer + a C driver
through the IR path, and compares `(kind, start, len, line, col)` token by token over 4 real files (with
Chinese comments, including the lexer's own source) — **all identical**.

Two real bugs fixed along the way (both in the compiler, not the lexer):

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `else if` fails to parse | the grammar only supports `else { }` | parser supports `else if` chains |
| 2 | with nested `&&`/`||`, LLVM reports `PHI node entries do not match predecessors` | short-circuit lowering hard-codes the phi predecessor as `rhs_l`, but the right operand may itself create new blocks | `_Ir` tracks `cur_label`, so the phi uses the real predecessor; every function gets an explicit `entry:` label |

The second bug was forced out by self-hosting: the `&&` in hand-written examples are all simple, and only
handing a real program (the lexer's multiple conditions) to the backend exposed it. After the fix, the output of
the 11 dual-path examples is value-for-value identical to before the change (see the P8 evidence in docs/145).

## M80 · The Loment parser (closed out: 43/43 corpus-wide, character-identical) ✅

`loment/selfhost/parser.lomt`: consumes M79's token records and produces a canonical AST dump, which
`loment_p8_test` compares **character by character** against the Python version (`lomentc`'s AST).

**Coverage** (closed out at 42 corpus files on 2026-09-12; raised to **43** on 2026-09-15 when a PE constant
was added to `lomelf.lomt`):
`module` / `fn` (formals, return, generic formals skipped) / `impl` (two shapes: `impl T for X` and
`impl X`, including method-name folding `<type>_<method>`, first param rewritten to `__self: <type>`, `self`
rewritten in the body) / `trait` (the declaration body does not enter the dump, matching the reference) /
type-name normalisation (`mut [X]`, `[X; N]`, `[X]`, `Name<A, B>`) / statements (`let`, assignment (including
`xs[i] = v`), `return`, `if`/`else` (including `else if` chains), `while`, `for i in a..b`, `match` (arms:
`_`, `E::V`, `E::V(bind)`), `if let` (desugared into a two-arm Match), `guard CAP(e);`, expression statements) /
expressions (integers, strings (unescape then re-escape), `true`/`false`, identifiers (`self` -> `__self`),
calls, array literals, struct literals, enum construction/paths, method calls, unary `!`/`-`/`&`/`&mut`, all
binary operators (associated per `lomentc.PRECEDENCE`), parentheses, `a.b`, `a[i]`, `a?`, `x as T`).

**Criterion**: `test_m80_loment_parser_ast_dump` pulls in the whole corpus and requires
`len(files) >= CORPUS_FLOOR` (coverage must not regress) + files not on the gap list must be character-identical
+ **two ratchets** (`PARSE_KNOWN_GAPS` / `PARSE_HELPER_GAPS`), now both empty tables, and the gate rejects any
entry that is "already identical" in the list — that is, this coverage can only move forward.

Real bugs / real convention divergences caught and fixed along the way (all of them found by "sweeping the whole
corpus"):

- the dump rule for the `else` branch (when there is exactly one If, **no** leading space) → see the previous section;
- the convention for string literals: the reference's `tok.val` is the content **after unescaping**, and the
  dump must re-escape by (backslash -> quote -> newline), with tab falling back per `_ESCAPES`;
  NOTE **since 2026-10-16 there is no "unknown escape takes the character itself" fallback** (`#33` / `#36`):
  the table is `n t r 0 \ "`, and anything outside it is **E019**. The old fallback turned `""` into the
  letter `r` and `"A"` into the three characters `x41`. The self-host lexer has no failure channel, so the
  refusal lands in `chk_syntax_prepass` (the same pass as "a parse error aborts");
- a type name is a **normalised string** (`mut [u32]` / `[u32; 4]` / `Name<A, B>`); reading it token by token
  would split `mut [u32]` into several "formals";
- `&mut x` / `&x` are **unary** operators (op = `&mut`/`&`), not the binary `&`;
- `..` must not be taken as field access (`.`, `.` two tokens);
- the dump for a method call is a **suffix** form (`receiver (mcall name args…)`), consistent with
  `(idx …)`/`(cast …)`;
- the ambiguity between the `{` in a `match` subject and a struct literal (the reference uses a `no_struct` counter);
- **integer literals come out as their value, not the source text copied verbatim** (2026-09-15): the reference's
  `IntLit.value` is already a value, so `0x1000` comes out as `(int 4096)` on the Python side; the Loment
  version's `put_tok` copied the token text directly, giving `(int 0x1000)`. After PE constants
  (`0x40000000` / `0xC0000040`) were added to `lomelf.lomt`, this hole was swept up for the first time —
  `put_num`/`put_dec`/`hex_val` were added to do the same decimal normalisation.
  The same convention is used for **array lengths in types** (`[u8; 0x10]`) — there is still no hexadecimal
  sample of that in the corpus, so a minimal probe (`HEX_PROBE`) was added to the gate to pin both places,
  rather than hoping the corpus would watch it on its own.

**Known residue** (does not affect the corpus; written outside the freeze surface):

1. struct-literal detection uses a **lookahead** ("after `{` comes an identifier + `:` and after that not `:`")
   instead of the reference's `no_struct` counter — equivalent for legal programs; the only difference is the
   **empty field list** `Pair {}` (the reference treats it as a literal, here it is identifier + block). To
   align fully, `no_struct` would have to be threaded through the expression recursion the way the reference
   does.
2. In `impl ... for <type>`, the target type is read as a **single token** (a multi-token type folds the name
   wrong).
3. Generic formals (`fn f<T>`) **do not enter the dump** on either side, so the criteria do not cover the
   generic parameter names themselves.

Implementation notes:

- Loment has no tuple returns → `(i << 32) | o` packs "token cursor + output cursor", passed functionally;
- the expression dump uses a linear "left operand emitted first" form (`(id a)(bin + (id b))`), which restores
  the tree unambiguously and naturally matches a token-by-token descent parser;
- multi-character operators (`==`/`->`/`&&`…) are two tokens lexically, recognised and stepped with
  `op_code`/`op_toks`.

## M81 · The Loment checker (closed out: rule equivalence 63/63) ✅

`loment/selfhost/checker.lomt`: consumes the M79 token stream and does **symbol table + type/call/move/borrow
checking**, emitting error records (`code | token`). The convention for comparison against the Python version
(`lomentc.check`) is the **set of error codes**.

Historical starting point (batch 1 had only 4 declaration-level rules):

| Code | Rule | Loment-side implementation |
|---|---|---|
| `E-DUP` (1) | duplicate top-level name (fn/struct/enum/const) | linear duplicate search in the symbol table |
| `E-TYPE` (2) | undeclared type (params/return/const/let) | base-type table + declaration table; `[T]`/`[T; N]`/`mut [T]` are stepped over wholesale with `skip_type` |
| `E-UNKNOWN-FN` (3) | call to an undeclared function | symbol table + builtin list (20 builtins) |
| `E-ARITY` (4) | argument count mismatch | top-level comma count (recognising `(`/`[` nesting) |

**Closing criterion (2026-09-12)**: with the token-level type inferer filled in, `tools/loment_rule_parity.py`
measures **63/63 rules judged equivalent** (the ratchet gate `eq ≥ BUDGET=63`; false positives / convention drift
/ dead probes must be 0; already in `tools/ci.py`); all 63 negative cases, **fed straight to the self-hosted
driver**, exit non-zero with none dying from a signal; 40/40 corpus units produce zero diagnostics; a
cross-module duplicate name reports E-DUP on both sides. Three **deliberately conservative deviations**
(under-report when the type is unknown, the conservative direction) are written out one by one in the open items
of the freeze surface in docs/158 §4.

**Known boundary**: resolving `use` imports is the **driver's** job (the loading layer); the checker itself
works on "the loaded unit". The driver's whole chain has no parser, so parse-time errors (such as
`const C: bool = true;`) cannot be reported by it (docs/158 §4 #3).

> **Added later (2026-09-13, two real bugs caught while porting `lomc.lomt`)**. The L0 generator has 357 `let`s
> and 108 `fn`s, the **largest unit** ever fed to the self-hosted checker, and it knocked out two things at once:
>
> 1. **The composite-type table is not zeroed per function**. The comment on `chk_arena_tyt` and the
>    `impl`-method path (`store32(tyt, 0, 0)` in `checker.lomt`) both state that the table is zeroed at the start
>    of **every function**, but **the ordinary `fn` path was missing one** — so the table bumped monotonically
>    over the whole unit, wrote through the 448-entry limit and trod into the borrow-name table, **misaligning
>    the entire scan cursor**.
>    The symptom is thoroughly misleading: a pile of `E002`s with the token pointing at the `let`'s **variable
>    name** (even at `;` or `(`) — it looks like "type not declared" when in fact the cursor went off the rails.
>    Fix: add the same zeroing on the ordinary `fn` path.
> 2. **In a large unit, using `fn` as a variable name goes off the rails — but this is not "the keyword is wrongly
>    rejected"** (I mis-recorded this one first; see below). `lomc.lomt` has a local called `fn`: the self-hosted
>    checker reports a string of E002s and exits 1, and renaming it to `fname` passes.
>    **Yet a minimal unit written the same way is byte-identical** — writing
>    `fn f() -> u32 { let fn: u64 = 1; return fn as u32; }` on its own goes through the self-hosted chain
>    **byte-identically** to the reference, zero diagnostics (measured 2026-09-13). So the real shape is:
>    **some size-related table/cursor** is triggered by this token (the same family as item 1 above), and **not**
>    a language-level divergence of "the reference accepts, self-hosting wrongly rejects". The move this time was
>    to rename the variable and go around it; **the root cause is not located and the gap is still open**.
>
>    > **Correction (2026-09-13, same day)**: this item was first written as "using a keyword as an identifier →
>    > checker false positive", and on that basis went into `docs/160 §2`, `docs/166` and the message of tag
>    > `v0.1.4-alpha`. When the minimal reproduction was filled in, the minimal unit was found to **pass**; that
>    > claim **does not hold**, hence this correction. The tag is immutable, so the wording in that version's
>    > message is superseded by this item.
>
> Regression criterion: `tools/loment_lomc_test.py::test_lomc_selfhost_compiles` (seed → stage1 →
> compile `lomc.lomt`, rc=0, artifact 473 408B). Changing `checker.lomt` changes the **seed**, so the same
> commit re-ran `loment_seed_test` (3/3, including the three-stage fixed point) and `loment_rule_parity`
> (**63/63, false positives 0**).

## M82 · Loment IR generation (expressions/control flow/short-circuit/casts/builtins) ✅

> **Added later (2026-09-13, the third real bug caught while porting `lomc.lomt` — still present, worked
> around)**.
> The self-hosted codegen's `operand_type()` (`codegen.lomt:318`, used to get the left operand type for `as T`)
> takes the wrong type when **the right-hand side of an operator is a "parenthesised expression with `as`"**,
> and emits **illegal IR**:
> ```rust
> let y: u8 = (48 + (x % 10) as u32) as u8;   // 参考: trunc i32 %t to i8
>                                             // 自举: trunc i64 %t to i8  (clang 直接拒)
> ```
> *(the two comments read: reference: `trunc i32 %t to i8`; self-hosted: `trunc i64 %t to i8` — clang rejects it outright.)*
> Root cause: `operand_type` scans to `+` and then recursively takes the right operand's type; hitting `(` it
> enters the parentheses and grabs the `x` inside (u64), **without counting the `as u32` after the parenthesis**
> — whereas the reference's `expr_type(Bin)` takes `lt or rt`, which here is exactly `u32`. Rewriting with an
> explicit temporary (`let digit: u32 = (x % 10) as u32;` then `(48 + digit) as u8`) makes the two sides
> byte-identical, so this time it was **worked around on the source side**: `codegen.lomt` is part of the frozen
> chain, changing it would drag the seed along, and it deserves a separate change + separate verification.
> **This one is still open**: any program writing `(a + b as T) as U` going through the self-hosted chain will
> get illegal IR.

`loment/selfhost/codegen.lomt`: reads the M79 token stream and generates LLVM IR text directly. The criterion is
**byte-for-byte**: `loment_p8_test::test_m82_*` compares the Loment output against `lomentc --emit-llvm` with
string equality, 9 target files, 58 functions in total:

- `loment/selfhost/ir_const.lomt`: literals / parameter returns (u32/u64/i16/bool);
- `loment/selfhost/ir_expr.lomt`: binary operators (precedence climbing per `lomentc.PRECEDENCE`, including
  `a * b + c`, `(a + b) * 2`), comparisons (unsigned `ult/ule/ugt/uge` vs signed `slt/...` chosen by type),
  bitwise ops and shifts (`and/shl`), unary (`sub ty 0, v` / `xor i1 v, true`), calls
  (direct calls, nested calls `add(a, mul_add(a, b, 1))`, literal arguments);
- `loment/selfhost/ir_stmt.lomt`: **statements and control flow** — `let` (with local allocas, in
  `lomentc._collect_locals` order), assignment, `if/else` (including an `if` nested inside the else), `while`,
  plus `terminated` semantics (a block that already `ret`s gets no extra `br`), label numbering
  `L1_then/L2_else/L3_end` and `L1_wcond/L2_wbody/L3_wend` (starting at 1, reset per function);
- `loment/selfhost/ir_logic.lomt`: **`&&`/`||` short-circuit** (`br i1` + three blocks
  `%L1_sc_rhs / %L2_sc_short / %L3_sc_end` + `phi i1`); the phi's right predecessor is "the real block at the
  end of the right operand" (for nested short-circuits, the inner `sc_end`, synonymous with `lomentc`'s
  `cur_label`);
- `loment/selfhost/ir_cast.lomt`: **`as` casts** (choosing `trunc` / `sext` / `zext` by bit width and
  signedness; literals and `bool` casts handled by the `st or "u32"` default rule), **including argument
  positions** — a call site coerces its arguments by the callee's **formal types** (the symbol table stores 8
  formal-type slots per function, synonymous with `lomentc`'s `expr(a, p.type)`);
- `loment/selfhost/ir_mem.lomt`: the builtins `load8` / `store8` (`getelementptr i8` + `load i8` +
  `zext i8 ... to i32`; `store8`'s result value is the literal `0` per `lomentc`), plus the `load8(...) as T`
  suffix;
- `loment/selfhost/ir_for.lomt`: **`for i in lo..hi`** (`store lo` → three blocks
  `L_fcond/L_fbody/L_fend`; the condition block **re-fetches** the loop variable and the upper bound, and the
  increment is written back to `%i.addr`; the signedness of the comparison is
  `expr_type(lo) or expr_type(hi) or u32`, deciding `slt`/`ult`, the same rule as `lomentc._collect_locals`);
- `loment/selfhost/ir_div.lomt`: **division and modulo** (`udiv/sdiv/urem/srem` chosen by the result type's
  signedness); division by zero goes through a `%L_dtrap` block (`call void @__loment_abort()` + `unreachable`);
  and once a `/` or `%` has appeared, the **whole freestanding runtime text block** is inserted after the banner
  and before the functions (byte-identical to `lomentc._IR_RUNTIME`);
- `loment/selfhost/ir_builtin.lomt`: **pointer/bitfield builtins** — `alloc` (bump heap: `load @__loment_off`
  → `add` → `icmp ule 65536` → `%L_aok`/`%L_aovf`, overflow goes to abort, and on success `store @__loment_off`
  + `getelementptr [65536 x i8]`, with the heap global **also inserted** after the banner), `free` (result value
  is the literal `0`), `atomic_add` (`atomicrmw add ... seq_cst`), `ptr_add`/`ptr_sub` (`zext i32→i64` +
  `getelementptr inbounds i8`; subtraction does `sub i64 0, n` first), `get_bits`/`set_bits` (i16 mask
  `1<<w - 1` then truncated back to i8), `panic` (abort + `unreachable` + a `%L_dead` dead block).
  The `as` suffix on a builtin result goes through the **name-based** cast `apply_cast_b` (builtin result types
  are fixed: `u8`/`u32`/`ptr`), including `ptr→u64` via `ptrtoint`.

**Coverage**: function signatures and type mapping (i1/i8/i16/i32/i64/ptr), the entry block, parameter
alloca + store, `%tN` numbering (from 1, reset per function), header comments (the comments carry **Loment type
names**, not LLVM types).

**Not covered** (this section was written just before M82 was closed out): the remaining builtins
(`str_len`/`str_eq`/`str_byte`/`slice_len`/`str_ptr`/`syscall*`), `match`, aggregate types such as
str/slice/array/struct/enum, the capability-domain table and DWARF metadata. Every gate run prints a per-file
gap breakdown (`test_m82_coverage_report`), and the `ir_*.lomt` target files are the corresponding regression
anchors.

**Core design (two-phase value stack)**: `expr_*` first writes the instructions to the output, then drops the
"value text" onto level `lvl` of the value stack; the caller then inlines that value into its own line. This is
exactly the effect the Python version achieves with string concatenation — so nested expressions keep **exactly
the same instruction order** as the Python version. The value stack is 8 levels × 72B; the argument-type slots
and the function/parameter symbol tables each have their offsets in the state block (`docs` recorded in the code
comments).

Real problems hit and fixed during debugging (symptom → root cause):

| # | Symptom | Root cause |
|---|---|---|
| 1 | `emit` has the wrong number of arguments | name clash with `lexer.lomt`'s private `emit(7 args)` → entry renamed to `emit_module` |
| 2 | output truncated at `ret i32 ` | the value buffer wrongly used `emit_mem` (which updates the **output cursor**) → switched to `copy_mem`, which does not touch the cursor |
| 3 | the return value printed as `\x03` | the value length was written to the start of the buffer (it should be the 4 bytes **before** the buffer) |
| 4 | `ret i32   %t0 = load ...` out of order | one-phase emission cannot place instructions at the head of the line → two phases (instructions + value buffer first, then the line) |
| 5 | state-block fields step on each other | the value stack / arg types / function table / parameter table offsets overlapped → re-laid-out (value stack 16..592, arg types 608, function count 640, function table 704, parameter table 1536, local table 3072) |
| 6 | local allocas leaked into other functions | `collect_locals` scanned the whole file → pass the matching `}` of the function body as the upper bound (`skip_block`) |
| 7 | labels numbered from `L0_` | `lomentc` labels start at 1 → base `+1`, count `+2` |
| 8 | the first statement inside an `if`/`while` body was skipped | the body start was computed as `'{' + 2` → it should be `'{' + 1` |
| 9 | a whole call statement vanished | `stmt` lacked the "expression statement" branch (`NAME ( ... ) ;`) |
| 10 | `take8(v as u8)` missing a `trunc` | the `as` suffix was not attached to the **call/builtin** branch |
| 11 | the `for` variable had no alloca | `collect_locals` recognised only `let` → also register the `for` variable (type per `lomentc`'s fallback chain) |
| 12 | `for i in 0..n` (`n: i32`) generated `ult` | signedness looked only at `lo` (a literal → untyped → falls to u32) → changed to `expr_type(lo) or expr_type(hi)` |
| 13 | the division result's number was smaller than `icmp eq`'s | `lomentc` takes `r` before `z` → split out `alloc_temp`, which takes a number without emitting output |
| 14 | a division on the right of `&&` wrote the phi predecessor as `%L6_sc_end` | the label tag_id table had no `dok/dtrap/dend` (defaulting to `sc_end`) → added tag_id/tag_name (**measured**: without it the output diverges from byte 5210, and it is illegal IR pointing at a non-existent block) |
| 15 | the division target file was missing the whole runtime block | output is written sequentially, but the runtime block has to land after the banner and before the functions → pre-scan for `/` `%` punct tokens (semantically equivalent to `lomentc`'s `"@__loment_" in text_all`) |
| 16 | a builtin result was truncated when it took part in a larger expression (`load8(p,off) + load8(p,off+1) * 256` computed only the first half) | the builtin branch returned the position of `)`, while the other atom/call branches return the position **after** it (the `expr_bin` contract) → unified to "after it" (`+1`, or `+3` with `as`) |
| 17 | a unit function body was missing a trailing `ret void` / `store16`'s return type was written as `i64` | `emit_ty`/`emitted_name` did not recognise `()`; and `emit_fn` had no "append `ret void`/`unreachable` when a block is unterminated" |
| 18 | `while true` generated `%t1 = load i32, ptr %true.addr` | `expr_atom` lacked the `true`/`false` literal branch (falling through to the "variable load" fallback) → write the literal `1`/`0` directly |
| 19 | a unit-returning call was written as `%t6 = call void @f(...)` | Python does not take a register for a `()` return (`call void @f(...)` with no assignment) → added a unit branch |
| 20 | the label numbers in `if blk_free(cur) && sz >= need` were all misaligned (`L7_sc_rhs` vs `L4_sc_rhs`) | the `if` branch **reserved** labels before computing the condition, but the `&&`/division inside the condition also reserve labels → changed to compute the condition first, then reserve (`lomentc`'s If is in the same order; the analogous `while` already reserved first) |
| 21 | `const HDR` was treated as a variable load (`%t2 = load i32, ptr %HDR.addr`) | **constant inlining** was not done: added `find_const` + `tok_int` (hexadecimal inlined as **decimal**, matching `lomentc`) + `set_dec_val` |
| 22 | a function omitting its return type after `-> ` was treated as having one | `pub fn pool_init(...)` has no `-> T` → the return type must default to `()`; represented by the sentinel `UNIT=0xFFFFFFFF`, handled together by `emit_ty`/`emitted_name`/`is_unit_tok` |
| 23 | `return 0;` (returning `ptr`) was written as `ret ptr 0` | `lomentc`'s `expr(IntLit, want="ptr")` writes `null` → the return position is decided separately |
| 24 | the state block went out of range on large files | `alloc(8192)` + a per-function formal-type stride of 128B + `emit_dec` leaking 16 bytes of heap per call → the state block was enlarged to 49152, the stride changed to 64B, and the decode buffer switched to a **shared scratch** inside the state block (`emit_dec`/`set_dec_val`/`set_temp_val` no longer touch the bump heap) |
| 25 | a field access's type was computed as the whole struct (`add { i32, i32 }`) | `expr_type` lacked the `FieldAccess` branch (`p.a` was taken as `p`'s `Pair` type) → added the "field type" branch (synonymous with `lomentc.expr_type`) |

**Aggregate types (M82 step 2, in progress)**: the `struct` table (name + field index/type; state block from
1024, 80B per entry), `emit_ty`'s struct name → `{ i32, i32 }`, field access `p.a` →
`getelementptr inbounds <struct>, ptr %p.addr, i32 0, i32 <idx>` + `load`, `str` → `{ ptr, i64 }`. Fields support
scalars only (matching the `_ll_type` restriction).
It unlocked `mathutil.lomt` (`for` variable + constant inlining + struct parameter field access).

**Strings (M82 step 3, this round reached 22/37)**:
- **Literal globals**: `@.str.<fn>.<n> = private unnamed_addr constant [N x i8] c"\XX…"` (uppercase hex,
  byte-by-byte escaping, the escape table as in `lomc._ESCAPES`; N is the **decoded** UTF-8 byte count). The
  global must land after the banner/runtime/heap global and before the functions, and output is written
  sequentially → use a **pre-scan** (`emit_str_globals`): per function, per occurrence, in the same order as
  expression evaluation (a linear scan of the same token stream). Note that `strings_needed` looks only at
  kind=2 tokens **inside a function body** — `use "…"` is also kind=2, but it is not a literal (this trap
  regressed 3 already-passing files).
- **Literal expressions**: `getelementptr inbounds [N x i8], ptr @.str.f.k, i64 0, i64 0` + two `insertvalue`s
  forming `{ ptr, i64 }`.
- **`str_*` builtins**: `str_len` (`extractvalue …, 1` + `trunc i64→i32`), `str_ptr` (`extractvalue …, 0`),
  `str_byte` (`extractvalue 0` + GEP + `load i8` + `zext`), `str_eq` (compare lengths first, then
  `call i32 @__loment_memcmp` + `phi i1`, landing in the three blocks `L_seq/L_sneq/L_send`).
- **`syscall4`/`syscall6`**: `call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx}[,{r10},{r8}],~{cx},~{r11},~{memory}"(…)`;
  the arguments are evaluated by their own types and inlined as-is as `i64 <val>` (the same shape as `lomentc`'s
  `expr(a,"u64")`).

**Value stack moved to 8192** (`blen/btxt` bases 16/20 → 8192/8196): the value stack used to overlap the struct
table (1024..6144), and deep nesting (such as 6 syscall arguments) stepped on the table; now the tables run
struct 1024 / value stack 8192 / function table 12288 / current-function parameter table 16384 / local table
20480 / per-function formal types 24576 (+i*64), with scratch at 656.

New real bugs:
| # | Symptom | Root cause |
|---|---|---|
| 26 | `str_ptr(s) as u64` generated `trunc ptr … to i32` | the builtin branch took the `as` type token as `as` itself (it should take the one after it); and it added a cast **unconditionally** |
| 27 | `p as u64` (ptr→u64) generated `zext ptr … to i64` | `apply_cast` lacked the `ptr→u64/i64` `ptrtoint` branch (`apply_cast_b` had it, the token version did not) |
| 28 | `let r: i64 = …; if r >= 0` generated `icmp uge i32` | `expr_type`'s variable branch looked only at the **parameter table**, not the local table → the local's type was lost (it should be `sge i64`) |
| 29 | nested calls' argument types were misaligned (`q` became `i32` in `take5(p, q, r, load32(p,n), n)`) | the argument-type slots are **one shared array**, and the inner call overwrote the slots the outer call had already recorded → changed to index by **value-stack level** (`720+(lvl+1+a)*4`). This bug shows itself only when "a call appears among the arguments" |
| 30 | `str_byte(s, i)`'s instruction order was reversed | Python is "take arg0 → `extractvalue` → take arg1", while I computed both arguments before the `extractvalue` |
| 31 | the call argument limit was only 4 | `while … && a < 4` → the self-hosted lexer's `emit(7 args)` was truncated; changed to 8 (the formal-type slots were 8 all along) |
| 32 | `DA` appeared inside a string literal | the driver reads the file by **raw bytes**, while the fixture wrote the temporary unit in text mode (CRLF) → wrote `newline="
"`; `lomentc.load` uses `read_text` (universal newlines), and the two sides must agree |
| 33 | `0x100000000` was truncated to 0 | the literal went through u32 arithmetic; changed to **long multiply-by-16-add** conversion to decimal (digit buffer 672..704), decimal kept as-is minus leading zeros — mirroring Python's big integers |
| 34 | `(r / 4294967296) as u32` came out as `trunc i32 → i32` | when `apply_cast`'s from-side got 0 (unknown), the print used `i32` but the bit width was computed as 64; now it passes the real `expr_type`, and 0 is always u32 (= `st or "u32"`) |
| 35 | `x as i64`'s type degraded to i32 when used in arithmetic | `expr_type` did not recognise the `as` suffix → added recognition for all four suffixes "literal/identifier/call/parenthesis + `as T`" |

**Slices and arrays (M3/M4/M24)**: type mapping `[T]`/`mut [T]` → `{ ptr, i64 }` (note `[T; N]` takes the array
branch, decided by whether the 2nd token after `[` is a `;`), `[T; N]` → `[N x T]`; `&a` / `&mut a` taking a slice
= `getelementptr inbounds <array>, ptr %a.addr, i32 0, i32 0` + two `insertvalue`s (the length comes from the
array literal); a slice index **read** = `extractvalue 0` + GEP(element) + `load`, an array index read =
GEP(array,0,i) + `load`; an index **write** (M4 mutable slice) = `extractvalue 0` + index + GEP + evaluate +
`store` (order matching `lomentc`); an array literal `[1,2,3,4]` = GEP + evaluate + store per element;
`slice_len` = `extractvalue 1` on a slice then `trunc`, while for an array it uses the literal length.
**The type position of `let` was changed to `skip_type`** (the type may be `[T]`/`[T; N]`); **formal scanning
must also use `skip_type`** — it used to step "3 tokens per parameter", so `xs: [u32]` produced a phantom extra
parameter (`i64 %u32`), and the function table's formal-type scan fell for it too.

**Generic monomorphisation (M6/M7)**: the low region of the state block (`16..475`, all free once the value stack
moved to 8192) holds the **type-parameter substitution table** (count 16, entry `24+i*8` = `param_tok|value_tok`),
with `subst_tok` hooked onto three "type sources": the entry points of `emit_ty`/`emitted_name`, and the returns
of `param_type`/`var_type`/`field_type`/`enum_payload_type`. Type arguments **need no extra storage** — the type
tokens of `let p: Pair<u32>` are followed by `<` `u32` `>`, so `var_targ`/`push_ty_subst` read straight from the
annotation position; to distinguish "declared formal" from "used argument" (the same `Pair` token means different
things in the two places), two slots are reserved in the struct/enum tables (free slots at `+72`/`+76` in the
stride-80 layout) recording the formal token at the declaration site. Instances are registered in first-use order
in the table from `64` (`72+i*12` = `fn_tok|t1|t2`); once the non-generic functions are emitted, they are emitted
one by one, each instance pushing its own argument mapping and its name concatenated as `base_arg`; a call site
uses `expr_type(first argument)` to infer the type arguments, register the instance, then emit the mangled name
and the substituted formal types. The generic declaration itself is skipped wholesale.
`skip_type` must also skip `<...>` (parsing the type of `let p: Pair<u32>`); the struct/enum table scan must skip
`<T>` to find `{`. Unlocked `native_gen.lomt` and `all_loment.lomt` (multi-module + generics).

**trait static dispatch (M8)**: the registration pass recognises `impl`/`trait` blocks — a `fn` inside a `trait`
declaration has only a signature (skipped wholesale, otherwise it would be emitted as an empty function), and a
method of `impl X for Y` is registered as kind=2 with the receiver type remembered; on emission the name becomes
`<receiver type>_<method>` (both the comment line and the `define` change), and **the 0th formal, `self`, takes
the receiver type** (`self` has no type annotation and used to parse as `)` turning into i64). The method call
`x.m(args)` is handled in `expr_atom` ahead of field access: the receiver is evaluated by the first formal's
type, the remaining arguments are coerced by the callee's formal types, and finally
`call <ret> @<receiver type>_m(<receiver> x, …)`. Variable-name output goes uniformly through `emit_name_tok`
(`self` → `__self`), covering every name position: the formal table / alloca / store / load / field and index
access. Unlocked `native_trait.lomt`.

**Enums and `match` (M25/M26) + struct/array literals (M23/M24)**: the enum table (name | nvariants |
has_payload | 8×(variant, payload); state block from 40960, 80B per entry); `E::V` → a payload-less enum writes
the literal index, a payload-carrying enum writes just the tag (`insertvalue {i32,i64} undef, i32 idx, 0`);
`E::V(x)` → evaluate → tag → `sext/zext` to i64 → payload insertvalue; `match` →
`switch i32 <tag>, label %L_mwild [ ... ]` + per arm `extractvalue …, 1` → `trunc` → `store %bind.addr` → arm body
→ `br %L_mend`; the struct literal `Blk { off: 3, len: 5 }` and the array literal `[1,2,3,4]` are both GEP +
evaluate + store per field/element. Unlocked `native_agg.lomt`.

**These three traps deserve a note of their own (all of them are "a multi-character operator is really several
tokens")**: `::` is **two `:` tokens**, `=>` is **two tokens (`=` and `>`)** and `->` likewise (the return type
is at the 3rd position after `)`). Anything that writes an offset on the assumption of "one operator, one token"
will silently misalign on these — the arm scan of `match` was almost entirely wrong because of it (the
wildcard-arm test never hit). Two more: the arm pass must explicitly terminate on the match body's `}`
(otherwise it runs one extra round past the last arm, swallowing the following statements into the arm body);
and the wildcard arm's emission order is **label → arm body → jump** (I first wrote label → jump → arm body,
which produced one extra `br`).

**Capability domains (P4: M35/M36/M38)**: `capability blk_write : disk[0..4] revocable` is scanned into a domain
table (name_tok | space_tok | lo | hi | revocable; state block from 6144, 24B per entry); `guard NAME(expr);`
lowers to `zext i32→i64` → `icmp uge/ule` (lo/hi are compile-time constants) → `and i1` → `br` to
`%L_gok`/`%L_gtrap` (the trap goes through `__loment_abort`) → the pass branch adds one to
`@__loment_audit[cap_id]`. At module level two more blocks are inserted: `@__loment_audit` (when a `guard` is
present) and `@__loment_caps` (when a `capability` is present, with the space name hashed to **FNV-1a 32-bit**),
in the order **runtime → audit → caps → heap global → string globals → functions**. Unlocked `native_cap.lomt`
(2408B, zero diff).

**Interrupt handler functions (M33)**: `interrupt fn f() { … }` → the comment reads
`; f -> interrupt (x86_intrcc)`, and the signature is `define x86_intrcc void @f(ptr byval([8 x i8]) %__frame)`
(**no parameters**, only local allocas listed). Note that the token before `fn` is `interrupt`, decided with
`base - 1`.

**State of the self-hosting stages (the biggest result this round)**: all three self-hosting stages —
`lexer.lomt`, `parser.lomt`, `codegen.lomt` — can be compiled by the Loment codegen into IR that is
**byte-identical** to `lomentc --emit-llvm` (lexer 26028B / parser 115464B / codegen 557823B, zero differing
lines) — that is, "a compiler written in Loment generates its own IR exactly matching the reference
implementation". The last piece of `checker.lomt` is a 9-argument call (`declare(...)`): both the call argument
limit and the formal-type slots were only 8, so the 9th argument was dropped wholesale → the limit was raised to
16 and the stride changed to 128B (function-count limit 128). With this, **all of M82's self-compilation criteria
are met**: the four compiler stages written in Loment generate their own IR byte-identically to the reference
implementation — the precondition for M83 (three-stage fixed point) is satisfied.

**The dependency-loading boundary (now concretised as a checkable fixture)**: `lomentc.emit_llvm` consumes the
**dependency-concatenated unit** after `prepare()` (`mods = deps + [mod]`). The test fixture `_unit_text()`
fetches dependencies by `resolve_deps`' path rules, concatenating dependents last, and **asserts that the
module-name sequence is exactly `lomentc.resolve_deps`'** (it fails the moment the rule drifts). So the Loment
codegen's input is "the loaded unit", consistent with the boundary of the M80/M81 self-hosting stages; moving
`resolve_deps` into Loment (which needs file I/O) is still a to-do before M83.

**Known structural boundary (to be filled in after M82)**: `lomentc.emit_llvm` goes through the
**dependency-concatenated unit** after `prepare()` (`mods = deps + [mod]`), so for a file like `allocator.lomt`
that contains no `/` itself but has `use "bytes.lomt"`, the runtime block is triggered by a **division inside a
dependency**. The Loment codegen consumes only a **single compilation unit** (it does not resolve `use`), so such
files are necessarily inconsistent for now — there are two ways out: ① move `resolve_deps` into Loment too (it
needs file I/O, a P7-machine matter); ② explicitly split the "front-end loader" off to the driver (the driver
concatenates buffers in topological order, and the codegen compiles only the concatenated unit). This boundary
must be settled before M83.

There is one more off-by-one at the token level: `->` is two tokens, so the return type is at the 3rd position
after `)`.

## M83 · Self-compilation (the compiler compiles itself) ✅ · M84 · Three-stage fixed point ✅

**Criteria and evidence** (`loment_p8_test::test_m83_m84_self_compile_and_fixed_point`, also reproducible with
one command, `python tools/loment_bootstrap.py`):

| Stage | How it comes about | Result |
|---|---|---|
| stage1 | the **Python** `lomentc` compiles `loment/selfhost/codegen.lomt` (together with the Loment lexer) → `.ll` → clang links an executable | runs |
| stage2 | **stage1's own `.ll` output** → clang links (M83: the compiler compiles its own output, which runs) | runs |
| stage3 | stage2's output → clang links | runs |
| **M84 fixed point** | stage1 / stage2 / stage3 each generate IR for `codegen.lomt`, and the three are **byte-identical** | **965680 B, all equal** |

The fixed point is no coincidence: stage2's output for `checker.lomt` and `ir_div.lomt` is also byte-identical to
the reference implementation.

### The standalone driver (a step added on 2026-09-11)

The three stages above are still "**functions called by a C driver**": they can compile themselves, but there is
no compiler that runs on its own. `loment/selfhost/driver.lomt` fills that in — lexer + codegen joined into
**one ELF**:

```
clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld \
      -o fujoc-s loment/build/driver.ll
./fujoc-s loment/examples/demo.lomt > demo.ll   # 与 lomentc --emit-llvm 逐字节相同
```

Memory is requested from the kernel (`brk`), and the language's own 64 KiB bump heap is **not** used: a 190 KiB
unit wants about 4 MiB of token table. Growing that static heap is the wrong direction — every `alloc` user's
`.bss` (kernel modules especially) would grow with it. This is also why M83 filled in `integer as ptr` along the
way (M67 had only done the reverse, `ptr as u64`).

Criteria (`test_m83_selfhosted_driver_compiles_itself`, run in WSL):

| Criterion | Result |
|---|---|
| the driver runs its own entry point → byte-identical to the reference | ✅ 1043790B |
| relink an ELF with the **driver's own output** → the artifact is unchanged | ✅ two-stage fixed point |
| the same binary is also byte-identical to the reference for `native_res.lomt` / `demo.lomt` | ✅ it is not "only able to compile itself" |

**Six real bugs caught by self-hosting** (all of the "only a real program exposes it" kind):

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | in the driver's unit, `die(1)`'s argument type became `ptr` | the per-function formal-type table `24576+i*128` collided with the enum table `40960`: the 128th function landed right on top of it. `codegen.lomt`'s own unit has only 118 functions so it never showed; the driver pulls in `bytes`/`lexer` too (134 functions) and exposed it | stride changed to 80 |
| 2 | clang reports `invalid cast opcode for cast from 'i64' to 'i64'` | a same-width differently-named cast (`i64 as u64`) was written as `sext i64 %v to i64` — at the same width these are the same type in LLVM | same width emits no instruction any more |
| 3 | clang reports `unable to create block named 'entry'` | a formal is named `entry`: LLVM block labels and local values **share one namespace**, and every function's first block is called `entry` | renamed in the driver; **still a gap on the language side** (`let entry: u32` will hit it) |
| 4 | the `trunc` for `(v / 256) as u8` was lost entirely | the type of `as`'s left operand was taken as the type of **the whole cast expression** (u8), so it was judged a "same-width cast" | added `operand_type()` (see below) |
| 5 | when two modules import the same dependency it was loaded **twice** | the dedup table's length was passed by value and not shared between sibling recursions: the parent loads A first (length becomes 1), then when loading B it still searches by the old length, so the A imported inside B is not found | the length was put into **one memory slot** (passed by pointer) — Loment has no out-parameters and no mutable globals |
| 6 | string constants in the IR were written `c"\0D\0A"` (CRLF) instead of `c"\0A"` | the driver reads the source by **raw bytes**, while the reference uses `read_text` (universal newlines); the source is full of **multi-line string literals**, so the CR went straight into the literal | `strip_cr()` after reading (drop CR, keep LF) |

Items 5 and 6 are especially worth recording: item 5 is the language fact "with no out-parameters and no mutable
globals, shared mutable state can only be boxed" biting a real program for the first time; item 6 is a
**same-origin variant** of the old CRLF trap in docs/150 (last time the fixture writing the unit hit it, this
time the driver reading the file did) — anywhere "source text is handed to a compiler", ask the question: are the
newlines normalised?

Item 3 is a **real gap on the language side**; the driver only worked around it: any user writing
`let entry: u32` will hit it. Either rename colliding locals at generation time, or change the first-block label
— left for the second half of M85 or before the freeze.

### First half of M85: the driver loads by itself ✅ (2026-09-11)

Loading used to be in the fixture (`_unit_text`). Now the driver does it itself:

1. The entry path comes from `/proc/self/cmdline` — `_start`'s argc/argv are on the stack and unreachable
   without inline assembly, while `/proc` gives the same information (at the cost of one `openat`+`read`).
2. `use "..."` is resolved recursively: a parent file is first read into **its own slot** (one `SLOT` per level
   at the top of the unit buffer), then the dependencies are processed recursively, and only then is the parent
   filled in — the order is "dependencies first", matching `resolve_deps`; the same path is loaded only once
   (the dedup table compares by bytes).
3. **Only `.lomt` counts as a dependency**: `lomentc`'s parser records `use "...lom"` in `mod.uses` (an L0
   declaration, handled by lomc), and only `.lomt` goes into `mod.imports`. The driver filters by the same rule
   — otherwise `demo.lomt` would concatenate `lom/fujr.lom` into the unit as well (a whole extra module).
4. When the unit lacks `Option`/`Result`, the preset enums are injected (the test is a lexical scan for
   `enum X`, so `enum Option<T>` written **inside a string literal** is not misjudged), and the injection point
   must be at the **end of the unit** (the reference appends the preset enums after the module's enums).

Criterion `test_m85_selfhosted_driver_compiles_corpus`: the same binary, entry path by entry path, compiles
everything in `loment/examples/*.lomt` and `loment/selfhost/*.lomt` and compares byte by byte — **40/40
identical**.

**The type-taking bug fixed along the way** (item 4): for `(v / 256) as u8`, the type of `as`'s left operand used
to be taken as the type of **the whole cast expression** (u8) → `as u8` was judged a same-width cast and lost
entirely. `operand_type()` was added: inside parentheses it takes the type of "the first operand, or the operand
to the right of some **top-level** binary operator" (mirroring `expr_type(Bin) = lt or rt`), and does not look at
arguments inside the parentheses — looking token by token would grab the `ptr` type of `tmp` in
`load8(tmp, ..)`.

### Second half of M85 · Gap ①: symbol names are flat — fixed (**diagnostics layer**), and why there is no mangling

**Symptom**: the driver blows up immediately when it loads `codegen.lomt` and `checker.lomt` at the same time:

```
error: redefinition of global '@.str.find_paren.0'    # 字符串全局名 = @.str.<函数名>.<序号>
error: invalid redefinition of function 'tok_kind'
```

*(the comment reads: string global name = `@.str.<function name>.<index>`)*

`checker.lomt` and `codegen.lomt` have **5 private functions of the same name**: `count_args` / `find_paren` /
`skip_type` / `tok_is` / `tok_kind`; two of them, `count_args` and `skip_type`, even have **different semantics**
(one counts formals, the other counts arguments; one skips `<T>`, the other does not), so a name clash means
**silently computing the wrong thing**. At the time, all five on the checker side were given a `chk_` prefix to
go around it (this change was kept — it incidentally clarified the semantic difference).

**Once the rule was added on 2026-09-11**, this got its correct answer: **this was not a "workaround" but an
error the language should report.**

- The emitted symbol names **are flat to begin with**. The kernel line finds entry points **by name** (`_start` /
  `timer_isr` / syscall wrappers, see docs/155 §3), so private symbols cannot be turned into `<module>_<name>` by
  mangling — the flat name *is* the cross-line ABI, and renaming would break the kernel-side symbol lookup.
  (This is the **reason** for "no mangling", not laziness.)
- The price of flat names is: **top-level names must be unique within a unit**. When that is violated, the
  backend emits two `define @helper`s (illegal IR), and a call site still resolves to the same function
  (**silent miscompilation**) — far worse than an error.
- So what was added is a **compiler rule**: `lomentc.check` now checks top-level duplicate names across
  `deps + [mod]` (functions/structs/enums/constants, excluding the preset `Option`/`Result` that `load()` injects
  into every module), reporting the E-DUP message "与模块 X 重名 —— 单元的发射符号是平的 (ABI), 请改名" ("name
  clashes with module X — the unit's emitted symbols are flat (ABI), please rename").
- **The two implementations agree**: the self-hosted checker, **not being module-aware**, had long reported this
  kind of duplicate as E-DUP — so this round is "the reference implementation catching up to the self-hosted
  version's behaviour", which incidentally tightened M81's criterion from "code set ⊆" to "code set ="
  (`test_m81_cross_module_dup_is_rejected`). The driver's gate covers it too: the driver only notices after
  loading the two files itself, and rejects it the same way (`neg_across/entry.lomt`).

Criteria: `lomentc_test` 91/91 (new `test_unit_wide_unique_names`), `loment_p8_test` 11/11 (the new cross-module
negative + the driver-gate case), **zero false positives** on the 41 corpus targets (all verified).

### Second half of M85 · Gap ②: checker coverage 0/41 → 40/40, the gate is open (this round, 2026-09-11)

When the previous round wired `checker.lomt` into the driver it **rejected every legal program** (0/41 units with
no diagnostics). That round only listed the gaps; this round filled them in one by one from the list, and now
**40/40 concatenated units give not one diagnostic** — the only remaining one, `native_raii.lomt`, is a
**non-target** (the reference's IR backend cannot emit it either; it was never a legal unit shape), does not
enter the corpus, and so does not count as a checker gap. **The driver's gate is therefore open.**

Criterion: `loment_p8_test::test_m85_checker_accepts_corpus_units` — 41 units (dependencies + this file + preset
enums, the same thing the driver loads) run through the self-hosted checker and must give zero diagnostics apart
from registered gaps; once a gap is fixed, the test reminds you to update the list (it is not allowed to quietly
go stale).

The nine filled in (all inherent blind spots of the "token-level linear scan" implementation):

| # | False report | Root cause | Fix |
|---|---|---|---|
| 1 | `3@n:Some` `Ok` `Err` (**every** unit) | the variant declarations in `enum Option<T> { Some(T), None }` look like calls | the second pass skips the declaration bodies of `enum`/`struct`/`trait` |
| 2 | `3@n:return` | `return (a + b)` was taken as "a call to a function named return" | keyword table (`chk_is_kw`): a keyword followed by `(` is still not a call |
| 3 | `3@n:blk_write` (guard) | the domain name in `guard NAME(idx)` was taken as a function | skip when the previous token is `guard` |
| 4 | `2@n:T` `2@n:Pair` | generics: the `T` of `fn f<T>` was not registered; `Pair<u32>`'s `<...>` was not skipped | register the tparam as a type name at the declaration (**no duplicate check** — several declarations each using `T` is legal); `chk_skip_type` skips `<...>` |
| 5 | `2@n:Result` (`native_res`) | the preset enums are at the **end of the unit** (`lomentc.load` appends), while `-> Result<u32,u32>` is at the top of the file | added a "pass zero" that collects type names before checking signatures |
| 6 | `2@n::` (`native_res`) | the `let` in `if let Result::Ok(v) = r` is a **pattern**, not a typed binding | skip when the previous token is `if` (the same guard as in codegen) |
| 7 | `native_concat` reports `str_concat` throughout | the builtin table had not kept up (the builtin added in M2 was only in `lomentc`) | added it to `is_builtin`'s name table |
| 8 | `native_trait` misaligned throughout (`2@10:-`) | `fn measure(self) -> u32;` has a **bare `self`** (no `&`), read as "parameter named self, type ;"; and a signature-only method has no body, so the return-type scan walked all the way to the impl block's `{` | `scan_params` recognises a bare `self`; the return-type scan also terminates at `;` |
| 9 | `native_trait` reports E-DUP | both impls declare `measure` — an inevitable clash in the flat symbol table (the compiler's real dispatch names are `Small_measure`/`Big_measure`) | impl blocks are **not inside the checker subset** (method calls all go through `x.m()`, already excluded by the `.` rule; call checking inside method bodies is still done) |

**Two real bugs caught along the way (both "a crash rather than an error"; worth recording on their own)**:

1. **A cursor past eof**: when the second pass's declaration-body skip landed exactly at the end of the file, the
   loop tail still did one `i = i + 1` → the cursor went into uninitialised bytes **outside** the lexical buffer
   → if what is there happens not to be `eof`, it reads on until it reaches unmapped memory and segfaults. The
   symptom is "one enum crashes, two enums are fine", and **recompiling changes the result** (heap-layout luck) —
   this kind of "layout-related" crash is the hardest to recognise, and it was finally pinned down by "bump
   `errs`/`toks` to 1 MiB and see whether it still crashes": the conclusion is not that the buffer is too small
   but that the cursor flew off.
2. **Advancing again after a skip**: `i = chk_skip_body(...)` returns exactly the **next declaration's keyword**,
   and the loop tail did `i = i + 1` again → skipping that whole declaration (when `struct B {..}` is immediately
   followed by `enum C {..}`, `C`'s variants get taken as calls). The fix is "if skipped, do not add one" (with a
   `skipped` flag; Loment has no `continue`).

### Second half of M85 · Gap ③: rule equivalence is now **measurable**; batch 1 (declaration-level rules) has landed (this round, 2026-09-11)

`0/41 → 40/40` only says "the self-hosted checker no longer wrongly rejects legal programs"; it **does not say**
that the self-hosted checker judges the same as the reference implementation — a more permissive implementation
can also pass 40/40. Deciding equivalence needs a negative-case matrix, so `tools/loment_rule_parity.py` exists:

- one reference rule paired with one **minimal negative case**, run on both sides, comparing **code sets** under
  `loment_diag`'s unified error-code convention;
- five states: `EQUAL` / `MISSING` (self-hosting under-reports) / `EXTRA` (self-hosting over-reports = false
  positive) / `DIFF` (code-convention drift) / `NOPY` (the probe itself is dead);
- **the gate is a ratchet**: `eq >= BUDGET` and `EXTRA == DIFF == NOPY == 0`; when a batch is done, `BUDGET` is
  raised, and **lowering it is concealing a gap**. `loment_rule_parity` is now in `ci.py`'s static gate.

First, the measured floor: **on first wiring it measured 9/60 equivalent, 51 gaps** (60 rule negatives, covering
the 81 message templates of the reference's `errs.append`). This round, after **batch 1 (declaration-level
rules)**, it is 24/60, with false positives and code drift both **0**. The rules batch 1 filled in (all of them
consume only the token stream, no expression type inference needed):

| Rule | Reference wording | Self-hosted implementation |
|---|---|---|
| duplicate formal name | `函数 f 参数 a 重复` ("function f parameter a is duplicated") (E013) | the formal-name table in `scan_params` + `chk_tab_has` |
| duplicate field/variant name | `结构体 S 字段 a 重复` / `枚举 E 变体 A 重复` ("struct S field a is duplicated" / "enum E variant A is duplicated") (E013) | `chk_struct_body` / `chk_enum_body` |
| empty type / same name as a base type | `结构体 S 为空` / `与基类型同名` ("struct S is empty" / "same name as a base type") (E009) | the above + an `is_base_type` pre-check |
| undeclared field/payload type | `类型 Foo 未声明` ("type Foo is not declared") (E002) | run `check_type` on fields and variant payloads |
| a constant's type must be integral | `常量 C 类型必须是整型` ("the type of constant C must be integral") (E001) | `chk_is_int`; **no** `check_type` (the reference does not either — over-reporting would be a false positive) |
| duplicate capability / reversed domain / occupying `excluded` space | `能力 c 域下界 5 > 上界 2` etc. ("capability c domain lower bound 5 > upper bound 2") (E004) | capability table + **a pass-and-a-half** (it waits until both caps and excluded are all collected, so it is independent of declaration order) |
| `guard` of an undeclared capability / literal index out of range | `guard 引用了未声明的能力` ("guard references an undeclared capability") (E004) | decided in the second pass using the capability table |
| builtin argument count | `内建 str_len 需要 1 个实参` ("builtin str_len needs 1 argument") (E003) | `chk_builtin_arity` (the table and the `is_builtin` name list are pinned together by a test) |

**Two classification disciplines** (fixed along the way this round; both would distort the measurement):

1. **The order of `tools/loment_diag.RULES` is the priority**, and `E002` must come before `E001` —
   `载荷类型 Foo 未声明` ("payload type Foo is not declared") contains both "type" and "not declared", and what
   the user has to do is "declare Foo first", not "check the types on both sides". Also added: `E014` (assignment
   target is not an lvalue) / `E015` (field/index/addressing) / `E016` (array literal) / `E017` (`as` cast), and
   `无字段/缺字段/重复初始化` ("no field / missing field / duplicate initialisation") moved from E009 to E015 —
   the code is assigned by **the fix**, not by the wording of the message.
2. **Every message template the reference can emit must be classifiable**: miss one in the classification table
   and that rule becomes "both sides turn into E999 and are dropped" in the comparison, so **the gap silently
   disappears**. `loment_tools_test` gained `test_m64_all_reference_messages_are_classified` (ast-extract all 81
   templates and classify them one by one), plus `test_m81_builtin_tables_match` (the self-hosted `is_builtin`
   name set == `lomentc.BUILTINS` ∪ `{slice_len}`).

**What is still missing**: 28 of the 60 remain, falling in two places —

- **the second batch of expression-level rules**: field/index/struct literal (E015), array literal (E016),
  `as` validity (E017), argument and builtin argument types (E001), `match`'s patterns/exhaustiveness (E008),
  `?` misuse (E010), method calls (E002);
- **move/borrow checking** (E006/E007/E011/E012) — the reference is `_move_check` + `_borrows_of`, two AST
  traversals, and the token version first has to model "scope/paths".

Honest convention: **the self-hosted checker today is the subset "declaration-level rules + statement-level type
comparison equivalent; the rest of the expression-level and the move/borrow missing".**

### Second half of M85 · Batch 2 (statement-level type comparison) and the two real bugs it caught along the way (this round, 2026-09-11)

The first stretch of batch 2 built the token-level type inferer `chk_ty_expr` (the counterpart of the AST version
`lomentc.expr_type`):

- **Type representation**: `(kind << 28) + payload`, 0 = unknown. Base types use internal codes; named types use
  a **symbol-table slot** (not a token index — the same name appearing twice in the source is two tokens, and
  using the index as identity would judge the `T` of `fn max_of<T>(a: T, b: T) -> T` as two different types).
- **Conservative exit**: composite types (array/slice/generic instance), a type formal `T`, field access, index
  and method call are all recorded as **unknown** — unknown can only "under-report", whereas a false positive is
  caught at once by the corpus's 40/40 zero-diagnostic criterion.
- **Reference semantics**: at depth 0, a comparison/logical operator -> bool (comparison takes priority over
  arithmetic, `v % 2 == 0` is bool); otherwise take the **leftmost** operand's type (`Bin` returns `lt or rt`;
  for a left-associative chain the leftmost is it); `as T` -> T.
- the statement rules that landed: `let` type comparison + array-length comparison, `return` comparison,
  `if`/`while` conditions must be bool, `for` bounds must be integral, assignment (undeclared -> E002; type
  mismatch -> E001).
- measured **32/60 equivalent, 0 false positives, 0 code drift** (24/60 after batch 1).

Two **real bugs** caught along the way (both of the "silently miscompiles / halts" level, not typos):

| # | Bug | Symptom and root cause | Fix |
|---|---|---|---|
| ⑨ | the self-hosted codegen's **argument limit of 10** silently truncates | the formal-type table's stride 80 = 10 slots × 8 bytes (widening would hit the enum table at 40960), and the argument loop reads `a < 10`. The first call with 11 arguments (the new `chk_body`) dropped its last argument — the IR is one argument short, the reference emits all of them, and the byte criterion reports "the two compilers disagree". | ① squeeze `chk_body` to 10 formals (pack the range into u64); ② `panic(10)` to **fail loudly** when over the limit; ③ `test_m85_codegen_arg_arity_is_loud` pins both the gate and "the corpus's maximum formal count <= 10" |
| ⑩ | the checker and codegen **share a 64 KiB language heap**, and batch 2's `alloc(2048)` went out of range | `alloc`'s bounds check goes through `@__loment_abort`, which in the self-hosted driver is an **illegal instruction (SIGILL)** — it looks from the output like "the compiler crashed". `emit_module` in codegen wants 49152B at once, and the checker, originally 14672B, crossed 65536 when raised to 16720B. | shrink the tables to the corpus's measured needs (fields<=2 / variants<=3 / formals<=11 / lets per function<=152), down to 12048B; `test_m85_heap_budget` statically pins "the sum of the two sides' allocs + a 4 KiB margin <= 64 KiB" |

⑩ deserves a sentence of its own: **this kind of "resource coupling" defect appears only when both sides grow**,
and its manifestation (SIGILL) looks exactly like "the code generation is wrong". The structural fix has landed:
`checker.lomt` split out `check_arena` (the buffer is supplied by the caller), and **the driver takes those
12048B from `brk` per `chk_arena_*`'s segment convention** (the driver's comment already said "memory comes from
the kernel, not the language's own 64 KiB bump heap"), so only codegen's own 49152B is left in the language heap
— the driver path's margin went from 4.3 KiB to 16.4 KiB. `check()` is kept as a thin wrapper that "opens an arena
from the language heap" (used by the C fixtures and tests), and `test_m85_heap_budget` reports the numbers for
both paths.

### Second half of M85 · Next stretch (not done): composite type representation + expression walker

Among the remaining 28 gaps, a batch **cannot** be fudged with "conservatively record unknown" — the reference
implementation's decision goes the other way:

```python
# lomentc.py: Index
ot = expr_type(e.obj, ...)
if ot is None or not (_is_array(ot) or _is_slice(ot)):
    errs.append(f"... 对非数组/切片类型取下标")     # <-- 类型**未知**时也报
```

That is, `对非数组/切片类型取下标` ("indexing into a non-array/slice type", E015) **must be reported** when the
type is "unknown", while this checker's "unknown -> skip" policy necessarily under-reports on this one;
conversely, copying "report when unknown" over would turn any array I cannot type (`let a: [u32; 4]`, a slice
formal, `&arr`) into a **false positive**. The same goes for: `&x`/`&mut x` may only apply to arrays (E015),
`slice_len`'s argument must be an array/slice (E001), a read-only slice cannot be written (E011), and the
length/element consistency of an array literal (E016, which must be compared against the declared type).

Conclusion: the precondition for this batch is to **build composite types**, not to keep papering over it with
"0 = unknown". The concrete plan:

1. The type encoding gains two kinds pointing at a small **type table** (kept in the arena, another 1 KiB =
   85 entries × 12B):
   - `kind 3 = array`, entry = (element type id, length)
   - `kind 4 = slice`, entry = (element type id, is-mut)
   (base/named types keep using the inline `(kind<<28)+payload`; they have no components.)
2. `chk_ty_range(src, t, syms, n, tyt, lo, hi)` — build a type from a **type token range**: `mut [T]` ->
   slice(mut=1), `[T]` -> slice(mut=0), `[T; N]` -> array(T, N), `Name<...>` -> unknown (generic instance, see
   above), a single token -> base/named. The type at a declaration, the target of `as`, and a `let`'s annotation
   all go through it.
3. `chk_ty_expr` learns to produce composite types: `&arr` -> slice(element), `&mut arr` -> slice(element, mut),
   an array literal -> array(element, count); an index -> the element's type. **Argument count**: the existing
   `chk_ty_expr(src,t,syms,n,env,envc,lo,hi)` plus a `tyt` = 9; wrapping one more layer — the diagnostic-emitting
   walker `chk_walk(...)` — must pack (lo,hi) into a u64 to stay under codegen's 10-formal limit (we already
   tripped once over 11 formals, see bug ⑨ above).
4. The walker `chk_walk` recurses by "split at depth-0 operators + atomic forms" (the counterpart of
   `_walk_expr`): within a segment it handles array literals / struct literals / enum construction / call
   arguments / method calls / fields / indices. Argument types come from the symbol table's `aux` (the `(` of the
   formal table), **re-scanned on demand**, with no two-dimensional table.
5. Only after that come moves/borrows (E006/E007/E011/E012) — the reference is `_move_check` + `_borrows_of`, two
   AST traversals, and the token version first needs the concepts of scope and "variable path".

The groundwork already laid for this (this commit): the symbol table's stride 20 with one `aux` slot, the brk
decoupling of `check_arena` (the arena has room for the type table), and the two static gates on heap budget and
argument limit.

### Second half of M85 · Batch 2 done: the self-hosted checker and the reference implementation are **63/63 rule-equivalent** (2026-09-12)

The previous round took batch 2's first stretch (statement-level type comparison) to 32/60; this round finished
the rest, measuring **63/63 equivalent, 0 false positives, 0 code drift**, and `loment_rule_parity`'s ratchet
budget went from 32 to 63. (The last three probes are M13 move E006 / M17 dangling E012 — the token version of
the reference's `_move_check`.)
What was added:

| Group | Content |
|---|---|
| Composite types | the type table gains **array/slice** kinds (element type + length/mutability); `chk_ty_range` builds types from a type token range: `[T; N]` / `[T]` / `mut [T]` / base / named |
| Expression walker | `chk_tex` = walking + type inference + diagnostics in one (the counterpart of `_walk_expr` + `expr_type`): split at depth 0, **comparison takes priority over `as`** (the top node of `i < n as u64 && …` is `&&`), arithmetic takes the leftmost operand (`Bin` returns `lt or rt`) |
| Atomic forms | array literal (empty/length/element consistency), struct literal (unknown struct / no field / duplicate initialisation / missing field), enum construction `E::V(x)` (unknown enum / no variant / payload-less with argument / payload type), call argument types (including array→slice covariance and the mutable-slice requirement), `slice_len`'s argument, field access, index, `as` validity (including M67/M83's two integer-pointer conversions), `match` (the subject must be an enum / no variant / duplicate pattern / payload binding / exhaustiveness), `?` (E010), **borrow conflict** (E007: both `&mut` and `&` in one call, or `&mut` twice) |
| Variable declaredness | "use of an undeclared variable" (E002) — the test is "is it in the environment", **not** "is the type 0", otherwise every generic formal `a: T` would be misreported |
| Moves/dangling | `chk_moves` (the counterpart of `_move_check`): a non-Copy variable (struct/array) is **moved** once passed out by `let t: T = s;`, a non-slice argument, or assignment, and using it again -> E006; `return &local` -> E012 (formals do not count as locals). The moved table sits at the tail of the type-table block (names compared by **text**, not token index — the same variable is a different token in each statement) |
| impl methods | the symbol table registers `K_METHOD` by (receiver type encoding, method name) (the reference renames them to `Type_method`); `chk_lookup_slot` deliberately skips methods — a bare `measure(...)` is "an undefined function" on both sides |

**Three deliberate deviations** (all in the conservative direction: prefer under-reporting to a false positive;
written out here one by one for the record):

1. **Index/`&` are not reported when the type is "unknown"**: the reference's decision is "`ot is None` or not
   array/slice -> report", i.e. "report even when the type is unknown". This checker records any array it cannot
   type (generic instances etc.) as unknown, and copying that over would be a false positive; so it reports only
   when the type is "known and definitely not array/slice".
2. **`match` is not reported when the subject's type is unknown** (the reference reports it); it reports when the
   subject is known to be a non-enum or a literal.
3. **`?` is reported only when it can be established that the thing is "not a Result"**: the reference desugars
   by the monomorphised `Result_*` instance; here the instance cannot be computed, so it decides in reverse — it
   reports E010 only when the subject is an integer literal, or when the called function's return-type token is
   not `Result` (so `parse_small(v)?` in `native_res.lomt` is not misreported).

**This round caught three more real bugs** (all of the "silently miscompiles" level):

| # | Bug | Symptom and root cause | Fix |
|---|---|---|---|
| ⑪ | the self-hosted codegen's **per-function table capacity is only 192** | the layout is `function table 12288+i*12 / fk table 14848+i*8 / formal table 24576+i*80`, and a unit with more than 192 functions writes through into the parameter-substitution table — `fkind` reads out as 2 and a few functions get renamed to `<receiver>_<method>` (`is_lomt_emit_div_mnemonic`). Batch 2 pushed the driver's unit to 246 functions before it showed (the byte criterion only reported "the two compilers disagree") | re-laid-out the three tables for **256** (fn 12288+i*12, fk 15360+i*8, formals 24576+i*80, enum table 45056+i*80), and added `test_m85_codegen_table_capacity` statically pinning "the largest unit's function count <= the capacity" |
| ⑫ | **a string literal token's raw text = its content** | the lexer gives a string token a `val` that is the content **inside** it (the raw text of `"impl"` is `impl`), so `tok_is(i, "impl")` matches the string `"impl"` in the source. Writing a compiler in Loment necessarily writes these strings (keyword tables, builtin-name tables), so the self-hosted codegen took the string in `chk_tok_is(src,t,i,"impl")` for an impl block and renamed every function after it as a method — the reference does not have this trap because its parser judges with `at(kind, val)`, **kind included** | `tok_is` (codegen) and `chk_tok_is` (checker) return false directly for string tokens (kind 2) |
| ⑬ | two `let`s of a **same-named local** in one function | the reference reuses the slot by name (emitting only one `%x.addr = alloca`), while the self-hosted codegen emits **two** — not byte-identical, and the second shadows the first's value | the shadowing `let oc` in the checker was deleted; **the self-hosted codegen side is still a gap** (the language rule should forbid it or make explicit "a later declaration reuses the same slot"), left for closing before M88 |

### M85's literal criterion: the mappable part of `lomentc_test` now runs on the driver (2026-09-12)

M85's literal criterion is "`lomentc_test` passes on the self-hosted version". Those 91 criteria fall into three
classes, and **the reason the third class cannot be mapped is hard**, written out here one by one for the record:

| Class | Count (approx.) | Mappable | Criterion / reason |
|---|---|---|---|
| **Rule decisions** (should a given program be rejected) | ~30 | ✅ | the 63 rule negatives of `tools/loment_rule_parity.py` are **fed straight to the driver**: all exit non-zero + produce no IR, and **not one dies from a signal** (`test_m85_driver_gate_on_probe_cases`). The checker crashed twice during development (a cursor past eof, an infinite recursion), so "the process just vanished" and "it reported an error" must be judged apart |
| **Positive cases and emission** (what should pass must pass, and the artifact must be right) | ~10 | ✅ | 40/40 corpus zero diagnostics + 40/40 targets **byte-identical** to the reference + the three-stage fixed point. Determinism is implicitly covered by "against the same reference bytes" |
| **Fixture-side 3-directory loading** | 1 | ✅ (already covered) | `driver.lomt`'s unit is itself a four-level `use` chain (driver → codegen → lexer / bytes), and recursive loading and dedup are already covered by "the driver self-compiling" |
| Runtime / dual-backend real runs | ~15 | ❌ | `m2`'s dual-path real run, `m4`/`m15`/`m18`/`m19`/`m20`/`m21`/`m22` need the **compiled artifact actually run** or a comparison against the Rust path; the driver stops at IR, and running needs a bare-metal/ELF host (that part belongs to p9 and the QEMU matrix) |
| Python API shapes | ~25 | ❌ | `llvm_*` asserting IR text fragments, `potato_*` asserting kernel objects/shapes, `m14`'s allocation audit, `m45`/`m46` judging the Python-side toolchain interfaces — these judge **the reference implementation's own API**, not language semantics; the language-semantics part is covered in a stronger form by "byte-identical" |
| Parse-time errors | ~5 | ❌ (covered by the reference) | the driver is lex → check → emit, **with no parser**; shapes like `const C: bool = true;` that are rejected at parse time (`int_lit`) are invisible to it. Of the current 63 probes, **0** belong to this class (all are constructed in the check phase), and the `PARSE_LEVEL` table is empty |

**Five real bugs were caught along this path** (the first four all got a static gate; the fifth is still open):
the argument limit of 10 silently truncating (⑨), the checker and codegen sharing a 64 KiB heap causing a driver
SIGILL (⑩), the per-function table capacity of 192 being written through so functions got renamed (⑪),
**a string literal's raw token text colliding with keyword tests** (⑫), and two same-named `let`s in one function
producing a double `alloca` (⑬, still open).

### The gate opens: the driver checks before it emits ✅

Once coverage reached 40/40, `driver.lomt` wired the checker in and **checks before emitting**:

```
./fujoc-s loment/selfhost/neg/unknown_fn.lomt    # 退出 1, stderr 报诊断, stdout 无 IR
./fujoc-s loment/examples/demo.lomt > demo.ll    # 退出 0, 产物与参考逐字节相同
```

The diagnostic format is the same origin as p8's C fixture (`E<code> @<token> line <line>: <snippet>`).
Criterion `test_m85_driver_checks_before_emitting`: **all 12 negatives exit non-zero** + carry a diagnostic +
**produce no IR**; positives exit zero. The 6 negatives added in batch 1 (duplicate formal / empty struct / same
name as a base type / reversed capability domain / builtin argument count / constant type) have all entered this
gate. **The load test on those 40 corpus units is watching this too** — if the gate misfired, that test would red
first.

**The original false-report list (early 2026-09-11, the 13-file version; each has been cleared per the table
below)**:

| Construct | Symptom (error code@token) | What was missing |
|---|---|---|
| Builtin functions | `3@70:load32` `3@140:store32` `3@15:str_concat` | call sites looked only at the function table; **the builtin table was not checked** |
| Enum construction | `3@53:Circle` `3@23:Some` `3@27:Err` | `E::V(x)` was taken as a function call |
| Cross-module calls | `3@358:double` (demo → mathutil) | the checker **does not resolve `use`** (M81's subset boundary), so running a single file necessarily reports |
| Generic declarations | `2@39:T` `2@234:Pair` | the `<...>` of `fn f<T>` / `Pair<u32>` was not skipped, so formals and types were read out of position |
| `guard` | `3@25:blk_write` | the domain-check statement was taken as a function call |
| Methods and traits | `native_trait.lomt` reports codes 1/2/4 | `impl`/`trait` blocks and `x.m()` entirely unrecognised |
| Statement boundary | `3@335:return` `2@20:{` (`native_raii`) | a parse misalignment somewhere took later tokens as call arguments |

The list is informative in itself: it shows that what separates a "subset checker" from "a checker that can serve
as a gate" is not the number of rules but **the modelling of this batch of constructs —
builtins/generics/enums/methods/traits**. Until it was filled in, it could only serve as M81-style "negative-set
judging".

## M86 · Not done (an honest account)

| # | Milestone | Why it cannot be done now |
|---|---|---|
| M86 | self-hosting performance optimisation | depends on M85 wrapping up (a stable end-to-end baseline exists only once the checker is wired into the driver) |

## Second half of M85 · The part not finished yet (an honest account)

| # | Gap | Note |
|---|---|---|
| ~~symbol table flat across the unit~~ | **fixed** (see Gap ① above): unit-level unique names became a compiler rule, and both implementations report E-DUP consistently; no mangling because flat names are the cross-line ABI (docs/155 §3) |
| ~~the checker's **expression-level** rules~~ | **closed** (see "Batch 2 done" below): with the token-level type inferer landed, **63/63 equivalent, 0 false positives, 0 code drift**, the ratchet budget 24→32→60→63 only rising (`tools/loment_rule_parity.py`, now in `ci.py`). The three **deliberately conservative deviations** are recorded one by one below and written into the open items of the freeze surface in `docs/158` |
| ~~`lomentc_test`'s criteria have not been moved to the self-hosted version~~ | **the mappable part is closed** (M85): `test_m85_driver_gate_on_probe_cases` feeds the 63 rule negatives straight to the driver (non-zero exit, no signal), 40/40 corpus zero diagnostics, 40/40 targets byte-identical; **the three unmappable classes** (runtime/dual-backend real runs, Python API shape assertions, fixture-side 3-directory loading) are listed one by one at the end of docs/150, with no `PARSE_LEVEL` exemption |

The self-hosting progress is real: **all four stages — lexer → parser → checker → codegen — are implemented in
Loment, and have each been matched against the Python version token by token / character by character /
error-code by error-code / byte by byte** (M79–M82), and **all four can be compiled by the Loment codegen into IR
byte-identical to the reference**, with the backend then reaching the three-stage fixed point (M84).

**M82 is closed out**: all 40 emittable targets are byte-identical (the only one left, `native_raii.lomt`, has
the reference implementation itself report `native: inb 暂未在 IR 后端实现` ("native: inb not yet implemented in
the IR backend"), so it is a non-target). **The first half of M85** (the driver doing its own loading) is
achieved too — the same binary compiles the whole corpus, entry path by entry path, byte-identically one at a
time. **The only self-hosting long poles left now are "symbol scope" and "the checker's impl/trait modelling".**

## M87 · The bootstrap script ✅

`python tools/loment_bootstrap.py` — one command takes you through the whole self-hosting front end:

1. regenerate the formal objects of lexer/parser/checker and check them against disk **byte by byte**;
2. run the three comparisons M79/M80/M81;
3. print a report (`--json` for machine reading).

## M87 extension · Python-free self-hosting (the seed) ✅

The bootstrap chain used to be able to start only from the **Python compiler** (M83's stage1 was compiled on the
spot by `lomentc`). Now the starting point is frozen into an artifact,
`loment/build/selfhost_driver.ll` (1.63 MB, the full-unit IR emitted by the reference implementation for
`selfhost/driver.lomt`), so rebuilding the compiler needs only **clang + POSIX sh**:

```sh
sh loment/bootstrap.sh          # 1) clang(种子)->stage1 2) stage1 产出==种子
                                # 3) stage2/stage3 定点 4) 跨阶段对 native_res 一致
python tools/loment_seed_test.py  # 3/3: 种子不许过期 + 脚本禁解释器 + 定点证明
```

Three criteria: the seed is character-identical to the reference (**stale means red**), no interpreter may appear
in a command position in the bootstrap script (a static scan + enforced LF), and the self-hosting fixed point
holds under the condition "clang only". Details in `docs/159-loment-seed-bootstrap.md` (including an honest list
of the remaining third-party languages). The gate is now in `ci.py`.

## M88 · Release checksums ✅

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` produces a sha256 listing (**line count =
number of artifacts**, the same origin as `release-manifest.json`, line-ending independent).

tag: **`v0.1.3.4-alpha`** (annotated) tags the frozen commit of `Fujoos-FujoLang-DEV` and has been pushed to
origin. It is a **dev-branch snapshot**, not a release branch: the freeze surface is in
`docs/158-loment-freeze.md`, and the release line waits for M100 (external audit). The tag can be revoked at any
time with `git tag -d` / `git push origin :refs/tags/...`.
