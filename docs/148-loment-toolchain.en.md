<!-- translated-from: docs/148-loment-toolchain.md -->
<!-- source-sha256: d6e09ea71b75b26fd29b2916b84a366b61bad65ece69dc205c1b3c636ee8495f -->

# 148 · The Loment toolchain (P6, M55–M66)

> Status: **implemented and passing the gate** (2026-09-09) · Entry point: `python tools/loment.py <subcommand>`
> Self-test: `tools/loment_tools_test.py` 11/11 · Gate: `ci.py --static-only` 7/7
> In one line: **from "it compiles" to "it does work" — formatting, language service, package management,
> documentation, debug info, test/bench/coverage, incremental and cache all landed, and every one of them has a
> reproducible criterion.**
>
> Editor host (M56): `editors/vscode/` — syntax highlighting + LSP client (completion/definition/diagnostics/
> formatting; the server is this document's `loment_lsp.py`) + build/check/run commands; packaging
> `tools/vscode_ext.py`, headless acceptance `tools/vscode_ext_test.py` (with a full LSP round trip); details in
> `docs/155` §8.

## 0.1 Syntax highlighting (`editors/vscode/syntaxes/`)

Two TextMate grammars: `loment.tmLanguage.json` (`.lomt`) and `lom.tmLanguage.json` (`.lom`).
**2026-10-12 added seven more**: `loment-<canonical>.tmLanguage.json` (c / cpp / java / csharp / go /
python / natural) — used when a source's head says `choose write grammar <alias>`, and they carry only two
rules (recognise that line + `include` the host grammar `source.c` / `source.python` / …). Design and
rationale: `docs/235`. The same day the main grammar was brought up to the language surface of the time:
the `choose write grammar` declaration, `set choose` / `choose` / `addin` / `command` / `foruse` /
`register` (**recognised by shape, not by word** — those words are not reserved, so bare-keyword rules
would also colour `let set: u32`), the core-dimension values, and the `syscall7` builtin. Four entries
that are **not** builtins were removed (`load16/load32/store16/store32` — ordinary functions in
`loment/examples/bytes.lomt`) along with two that are **not** types (`usize`/`isize`; `-> usize` really
does report "return type usize is not declared").

On 2026-09-11 there was a "thorough" sweep: **using the same engine as VS Code** (TextMate + Oniguruma), the
grammar was run into a scope stream and checked token by token, closing these blind spots:

| Construct | Before | Now |
|---|---|---|
| `=>` (match arm) | split into two colours, `=` + `>` | one, `keyword.operator.match-arrow` |
| `?` (try) | `punctuation` | `keyword.operator.try` |
| `self` | `keyword.control` | `variable.language.self` |
| `Color::Red` | `Red` was taken for a type name | `entity.name.type.enum` + `constant.other.enummember` |
| `fn f(a: u32)` parameter | none | `variable.parameter` (the whole signature goes into `meta.function.signature`) |
| `let x` | none | `variable.other` (the `if let` pattern is not misjudged) |
| `struct S { a: u32 }` field | none | `variable.other.member` (the whole run is `meta.block.struct`) |
| `MAX_BLKS` | taken for a type name | `constant.other` |
| `capability blk : disk[0..4]` | only `capability` recognised | domain name `entity.name.constant.capability` + space name `support.type.capability-space` |
| module name / record name / constant name | none | `entity.name.namespace` / `entity.name.type` / `variable.other.constant` |

Effect (measured with the same engine, the "no-scope share"): two `.lom` corpora **0.0% / 0.1%**; four `.lomt`
corpora 3.4% / 7.9% / 9.6% / 20.9% (the rest are all bare identifiers inside expressions — at the TextMate level
there is no type information, so their inheriting the default foreground colour is normal).

**Two classes of silent failure are now in the gate** (`test_vscode_grammar_lints`) — they raise no error, the
colour is just wrong:

1. **A scope name uses an invented root name** ⇒ the theme does not recognise that run and shows it in the
   default foreground colour (which reads as "not highlighted");
2. **Eating the quote in a `match`** ⇒ it intercepts a string's **opening quote**, so the whole rest of the file
   gets coloured as an unterminated string. I hit this the moment I added the `excluded "` rule: from line 21 on,
   all of `demo.lomt` turned the string colour. Only `begin`/`end` are allowed to touch quotes.

### How to see for yourself whether the highlighting is right

```
python tools/vscode_ext.py --doctor     # 装没装 / 语法是不是旧版 / 有没有人抢 .lomt
```

Checking the grammar itself headlessly needs the same engine as VS Code: `npm i --prefix <dir> vscode-textmate
vscode-oniguruma`, then load the grammar and `tokenizeLine` line by line printing the `scopes` — this is the only
way to "see" how VS Code will colour (`vscode_ext_test.py` only does regex/structure-layer checks; it cannot see
whether the colouring is right).

**The order to debug missing highlighting** (by probability): ① after installing/updating the **window was not
reloaded** (grammars load at window start); ② the file's **language mode** is still Plain Text — a file opened
before the extension was installed remembers the old association, so change it to Loment with `Ctrl+K M`; ③ run
`--doctor` once and look at the three items above. (To see directly what a position is coloured as:
`Developer: Inspect Editor Tokens and Scopes`.)

## 0. Single entry point

```
python tools/loment.py fmt   FILE...        # M55
python tools/loment.py doc   FILE           # M58
python tools/loment.py diag  FILE           # M64
python tools/loment.py ir    FILE [--objdump]   # M60
python tools/loment.py test  FILE           # M61
python tools/loment.py bench FILE [--n N]   # M62
python tools/loment.py cov   FILE [--call F]# M63
python tools/loment.py build DIR            # M65/M66
python tools/loment.py pkg   resolve|verify # M57
python tools/loment.py lsp                  # M56
```

## 1. M55 formatter `lomfmt`

- Reuses the `lomc.lex` lexer → merges multi-character operators → re-lays out by indentation/spacing rules;
  token semantics unchanged.
- Criteria: **idempotent** (formatting twice gives byte-identical results) + **semantics preserved** (the Potato
  formal object is the same before and after formatting).
- Covers all 16 examples (`loment_tools_test.py::test_m55_*`).

### 1b. The Loment formatter `loment/tools/lomfmt.lomt` (the first Python-free block)

The first program in the user-facing toolchain **rewritten in Loment and byte-equivalent to the Python version**:
the same ELF (linked by the reference implementation + clang, or by the seed) eats a `.lomt`, and its stdout is
exactly `tools/lomfmt.py`'s.

- Eats only the lexical layer (it reuses the lexer, just as the Python version does), so it is not constrained by
  the self-hosted parser's subset;
- Criteria: **42 corpora byte-identical** (examples + selfhost + itself) + 4 boundaries (empty file / comments
  only / a string with escaped quotes / CRLF) + idempotence; see `tools/loment_fmt_test.py` (in `ci.py`);
- Semantics **mirror** the Python version line by line, including comparing a string literal's `val` as a
  keyword/punctuation (the corpora are full of `tok_is(src,t,i,"(")`, and without the mirror it would not be
  byte-identical) and the `_render` rule of "unescape then re-escape";
- Cost and known differences: the Python version's dropping of comments is **copied over** (both drop them), not
  changed unilaterally; this is an open-item-level problem of docs/158 §4, and any change has to be made in both
  implementations at once.

## 2. M56 language service `loment_lsp`

A minimal LSP (JSON-RPC over stdio): `initialize` / `didOpen` / `didChange` / `definition` / `completion` /
`shutdown`. `handle()` is a pure function and can be self-tested without an editor (three criteria: diagnostics,
definition, completion).

> Honest boundary: **not tested in a real editor** (no VS Code extension host); the self-test drives the same
> `handle()`.

### 2b. The Loment language service `loment/tools/lsp.lomt` (**no Python needed**)

The editor's "write Loment" path used to require Python (the extension spawned `tools/loment_lsp.py`). Now the
same extension can spawn the **Loment** service:

```powershell
powershell -File scripts/install-lsp.ps1      # 种子 + clang + stage1 编译并装进 WSL, 全程无 Python
# 它会把这两行打出来 (VS Code 设置):
#   "loment.serverCommand": "wsl",
#   "loment.serverArgs": ["-e", "/home/<you>/.local/share/loment/lsp"]
```

Implementation (`loment/tools/lsp.lomt`, JSON in and out through `loment/lib/json.lomt`):

- `initialize` / `initialized` / `shutdown` / `exit`;
- `textDocument/didOpen|didChange|didSave` → runs the **self-hosted checker** → `publishDiagnostics`;
- `textDocument/completion`: 23 keywords + 14 type words + the symbols declared in this file (kind agrees with
  the Python version);
- `textDocument/definition`: take the word at the cursor → its declaration line (returns null when undeclared);
- command-line mode `lsp --check FILE`: diagnostics go to stdout as `路径:行:列: E0NN 标题` [path:line:col: E0NN
  title], exit code 0/1 — usable both by editor tasks (problem matcher, see the extension's
  `contributes.problemMatchers`) and by CI.

Criteria `tools/loment_lsp_test.py` 3/3 (in `ci.py`): a real binary + real `Content-Length` framing, 7 frames
checked round trip (clean 0 diagnostics / E002 line number after didChange / completion contains declared symbols
/ definition reference→declaration / undeclared→null / shutdown null), 3 code-and-line-number cases
(E013/E002/clean), and `--check`'s format and exit code. In addition, "building these tools with the
**self-hosted compiler**" is itself in the corpus gate: `loment_p8_test`'s driver corpus includes
`loment/tools/*.lomt` and `loment/lib/*.lomt` (44/44 byte-identical).

**Boundaries (stated honestly)**: this version **does not provide formatting** (it does not declare
`documentFormattingProvider`; formatting still goes through `loment/tools/lomfmt.lomt` or the Python version);
the diagnostic text is `loment_diag`'s **classification title** (codes are split by fix, and the code is the
E0NN), not the reference implementation's full message wording. The two commands `runTests`/`runInFujoOS` still
go through the Python tools (they are extra features beyond "write and run").

## 3. M57 package management `lompkg`

- A package = a directory + `pkg.json` (name/version/deps); dependencies resolve to a **topological order** and
  cycles are detected;
- Checksum = the sha256 of each `*.lomt` in the package (sorted by path) combined file by file; `resolve --write`
  writes `pkg.lock`, `verify` recomputes and compares (change one byte and it reports DIFF).

## 4. M58 documentation generation `lomdoc`

Generates Markdown from `.lomt`: capability-domain table, constant table, struct fields, enum variants (with
payloads), trait/impl, function signatures; doc comments are taken from the consecutive `///` lines before a
declaration.

### 4b. The Loment documentation generator `loment/tools/lomdoc.lomt` (the second Python-free block)

**Byte-identical output** to `tools/lomdoc.py` is the criterion: `python tools/loment_doc_test.py` — 43 corpora
(examples + selfhost + tools) + one boundary case (excluded / hex constant / multiline doc / two-method trait /
generic parameter / empty doc) all agree, and it is in `ci.py`.

- Like the Python version it looks only at the **declaration layer** (not at a syntax tree), so it needs no
  parser: the same road as lomfmt;
- Output is organised by "line" (each line = text + `\n`), equivalent to `"\n".join(out).rstrip() + "\n"`; the
  number of blank lines in each section was **measured against the reference implementation** — with an empty
  doc, the doc line still takes a line, and that is where a newline is most easily off by one;
- It mirrors the reference implementation's details: the injected `Option`/`Result` are appended at the end, impl
  methods are folded into `<类型>_<方法>` [type_method] with the first parameter written `__self: <类型>` [type],
  type names are normalised, hex constants print as decimal, and the path is written into the document header
  **exactly as the command line gave it**;
- **and it fixed a real bug in the reference implementation as a side effect**: the injected prelude enum used to
  carry the **prelude's line numbers**, and the documentation generator used those to look up lines in the
  **target file**, so `Result` would take some earlier `capability`'s comment as its own doc (`lomentc.load` now
  zeroes the `line` of injected items; the Loment version handled them as line 0 to begin with).

## 5. M59 debug info (DWARF)

`lomentc --debug` (together with `--emit-llvm`) attaches to the IR:

- `!llvm.dbg.cu` / `!DIFile` / `!DISubroutineType`;
- one `!DISubprogram` per function, **attached to the `define` line** (the crucial point: if only instructions
  carry `!dbg` and the `define` carries no scope, LLVM throws the whole line table away);
- one `!DILocation` per statement, with `w()` uniformly appending `, !dbg !N`.

Verification: after `clang -g -c`, `llvm-objdump -d -l` outputs source-line annotations like `; toolchain.lomt:7`
(the local LLVM 22 has no `llvm-dwarfdump`, so `objdump -l` verifies it equivalently).

> Honest boundary: the line table goes down to **statement** granularity; there is no variable-location table
> (`llvm.dbg.declare`/`dbg.value`), so a debugger can break by line but cannot print local variables.

## 6. M60 IR viewer

`loment ir FILE` prints the IR; `--objdump` appends the machine code from `clang -c` + `llvm-objdump -d`.

## 7. M61 test framework

The convention is `fn test_*() -> bool`: `loment test FILE` generates a Rust harness (`include!` + counting),
compiles and runs it with `rustc -O`, outputs `PASS/FAIL` and `RESULT: n/m PASS`, and exits 1 on failure.

## 8. M62 benchmark framework

The convention is `fn bench_*() -> u32` (zero parameters): the same module goes down the Rust path and the IR
path separately, loops N times taking nanosecond-level timing, and outputs a comparison table. The Rust side uses
`std::hint::black_box` to prevent constant folding; the IR side uses `timespec_get`.

Sample (`toolchain.lomt`, N=2e6, local machine):

| Function | Rust path (ns) | IR path (ns) | Ratio |
|---|---|---|---|
| `bench_fib` | 806000 | 1984100 | 2.46x |
| `bench_popcount` | 805600 | 1990100 | 2.47x |

> How to read it: the IR path is about 2.5x slower (not inlined + the accumulator is volatile). This is a
> **measured fact**, not a verdict on the merits of the languages.

## 9. M63 IR-level coverage

`--coverage` increments `@__loment_cov[i]` at the start of every basic block and exports the block total
`@__loment_cov_n`; `loment cov FILE --call F` generates a C driver that calls the entry and prints
`COV hit/total pct`. Sample: `toolchain.lomt: COV 6/31 19.4%` (`cov_main` takes only the true branch of the if).

## 10. M64 diagnostic classification

`tools/loment_diag.py` pattern-classifies compiler messages; each of the 20 error classes has a stable error code
(E001–E020) and an actionable suggestion; the test uses 13 counterexample snippets to assert that **every one is
classified** (no E0999) and carries a suggestion.

Two disciplines directly tied to "the classification table is trustworthy":

- **Codes split by the fix, not by the message wording**: `实参类型 …` [argument type …] / builtin argument /
  `载荷类型 …` [payload type …] / `return 类型 …` [return type …] all fall under E001 (they all say "the types do
  not match here"), and what the user has to do is the same thing.
- **Order is priority**: `E002` must come before `E001` (`载荷类型 Foo 未声明` [payload type Foo is not declared]
  calls for "declare Foo first"), and `字段 a 重复$` [field a duplicated] must anchor to the end (otherwise it eats
  E015's `重复初始化` [duplicate initialisation]).
- `test_m64_all_reference_messages_are_classified` uses `ast` to extract **all** the `errs.append` message
  templates in `lomentc.py` (81 of them) and classifies each one — if the classification table misses one, that
  rule becomes E0999 on both sides in the "self-hosted vs reference" code-set comparison and is dropped, and **the
  gap disappears silently**.

## 10b. M85 rule-parity comparison

`tools/loment_rule_parity.py`: one reference rule paired with one **minimal negative case** (60 of them), each run
on both sides, comparing the **code set** under `loment_diag`'s uniform code convention; the status is one of
`EQUAL`/`MISSING`/`EXTRA`/`DIFF`/`NOPY`. The gate is a **ratchet**: `eq >= BUDGET` and `EXTRA == DIFF == NOPY ==
0` — `BUDGET` goes up only after a batch is finished, and lowering it is the same as hiding a gap. It is in
`tools/ci.py`'s static gate.

Currently measured: **63/63 equivalent, 0 false positives, 0 code drift** — the self-hosted checker and the
reference implementation are fully equivalent on these 63 rules (batch 1 declaration-level rules + batch 2
statement-level/expression-level type comparison + match/`?`/borrowing + move/dangling). Three **deliberate
deviations** (indexing and `&` are not reported when the type is unknown, a `match` scrutinee that is unknown is
not reported, and `?` is reported only when it can be determined not to be a `Result`) are all in the conservative
direction, and each is recorded item by item in `docs/150`.

## 11. M65/M66 incremental build and cache

`tools/loment_build.py`:

- Cache key = sha256(source file + the contents of recursively `use`d `.lomt` files);
- key hit and the artifact is on disk → skip; otherwise recompile that unit;
- index `.loment-cache.json`; `--report` prints a per-file hit/recompile table.

Measured (16 examples): cold build **82.6 ms** → warm build **9.0 ms** (9.2x).

## 12. Uncovered boundaries (honest list)

- The LSP is not wired to a real editor, and has no cross-file symbol index (it indexes only the current
  document);
- `lompkg` supports only local path dependencies (no network repository, no version-range solving);
- DWARF has no variable info; coverage is **block coverage**, not line/branch coverage;
- the test framework runs only the Rust path (the IR path is covered by `bench`/`cov`);
- incremental build is at file granularity, with no function-level increment.
- Line endings (CRLF/LF) are policed separately by `tools/loment_eol.py`: git is "blind" to newlines, and a CRLF
  checkout masquerades as a logic red on a byte-for-byte criterion (docs/161).
