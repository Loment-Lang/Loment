---
name: loment-toolchain
description: Loment's toolchain and extension points - the 39 packaged CLI commands, LSP/DAP/VS Code (the packaged self-hosted LSP does not format; the reference LSP and the VS Code extension do), LLS 0.1.6 Edition 2, the L0 contract, packaging and signing, hot-update channels, the library system, Flomtc, and repository-only development paths. Read when operating the toolchain, writing a library, or contributing to the compiler repo. Part of the LomentSkill suite; section numbers are global.
---

## 8.11 Tools and editors

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


| Tool | What it does |
|---|---|
| `loment check` / `ir` / `build` / `run` | the driver; diagnostics from `lomenterr` (position, source line, caret, what/why/at least three fixes, supported/not supported) |
| `loment lsp` | language server over stdio (completion, definition, diagnostics). The **packaged self-hosted** LSP declares **no formatting provider**; the **reference** LSP (`tools/loment_lsp.py`, what the VS Code extension launches) **does** format. The packaged binary is built in the **hotswap** profile |
| `loment hotupdate ...` | install / `--check` / `--rollback` / `--status` a signed toolchain generation (hot update); the native `loment-hotupdate` is the trust root (`loment-runtime` 8.5) |
| `tools/loment_dap.py` | Debug Adapter Protocol + `ptrace` backend, launched by the VS Code extension so F5 works; it must run on a real Linux kernel (WSL2 yes, WSL1 no). It is **not** a `loment` subcommand |
| `loment fmt` / `doc` | formatter / API docs from `///` |
| `loment stat` / `fns` / `grep` / `hash` / `cat` / `tokens` / `todo` | source reading without an editor |
| `loment skill` / `cheat` / `syntax` / `builtins` / `codes` | the guide and the language reference |
| `loment lompi` | **not a subcommand** - run `lompi`; the package manager is a separate command |
| `lomenterr` | the reporter; the launcher starts it on failure. It is not a `loment` subcommand |
| `loment-lomelf` | the self-hosted linker; `--allow-ports` is needed when you call it directly instead of through `loment` |
| `loment-lvm` | the LVM interpreter (section 8.3) |
| VS Code extension | `hooyan2022.loment` - TextMate grammar + LSP; it is a **client**, it needs a toolchain checkout/install; `tools/vscode_ext.py --install` side-loads it |

`loment doc` takes the `///` comments and prints an API summary:

```rust
module docs

/// Returns the answer.
pub fn answer() -> u32 { return 42; }
```

**Boundary**: the packaged CLI's own `codes` banner and the builtins page lag the compiler.
The reporter knows **E001–E041 plus 241 fine `E0xxx` codes** (section 4 of `loment-syntax`);
`loment builtins` also omits the newer `str_show` / `str_repr` / `str_pad` / `str_num` rows.
Trust the code table and `loment explain <code>`, not the banner.

### 8.12 L0, the language standard, and packaging

- **`lom/*.lom` is the L0 interface contract, not an implementation detail.** It is the
  cross-line surface other components compile against (for example the content-trust
  primitives `qual_feed` / `qual_seq` and the admission gate `dom_admit`). It is generated into
  spec files and checked by `fuai_contract_check`. **Changing it is a contract change**: its own
  commit, naming who depends on it, what changed and whether downstream must move - never a
  drive-by edit during a compiler refactor.
- **`standard/LLS.md` is the language standard, and the sole normative original** - **LLS 0.1.6 ·
  Edition 2**. It defines the grammar, types, semantics, safety layers, artifact contract, and
  implementation qualification; `docs/` is informative. **Its version is the language version -
  one number**. §4 is **implementation qualification** (four conformance classes: C1 compiler /
  C2 checker / C3 runtime / C4 toolchain); §25 is the **switch** table: Loment is defined by
  what it switches on, not by what it lacks (`no_runtime` is a guarantee the linker checks;
  `lvm` records the execution model but the artifact is the same as `no_lvm`, because the
  interpreter is a separate tool). **Boundary:** §25 lists **nine** dimensions (mode, gc,
  runtime, port, lvm, lms, lspe, lifetime, fp); the code's `CORE_DIMS` has **11** (adding
  `class` and `flomtc`) plus two slots (`lspe_aicheck_*`, `lspe_effects_*`). The two extra
  dimensions and the slots are real, accepted code-level switches; the standard has not caught
  up yet, so treat the code as the behaviour and the standard as the intent.
- **`lompi` is the package manager**, and it is a **separate command**, not `loment lompi`.
  A library is a directory, its identity is a content hash, and its dependencies are the `use`
  lines in its source. Read the lompi guide before touching dependencies.
- **Packaging**: `tools/loment_dist.py` builds the `bin/` + `share/` layout (including the
  self-hosted driver, linker, CLI, formatter, LSP, LVM and `lompi`). A release records a
  `release-manifest.json` and `SHA256SUMS`. Signing is two things (`docs/163`): Authenticode on
  the Windows executable, and a detached signature over `SHA256SUMS` for the archive; the key
  lives only in the CI secret, and self-signed certificates still trigger SmartScreen.
  `loment_publish.py --check` is the gate that the published file set is exactly the tracked set.

### 8.13 What is not there (so you do not design around it)

- **No floats** (`f64` exists only as a library over integers), no `char`, no tuples, no
  `impl Trait`, no `dyn`, no exceptions. Function values and closures exist only under
  `choose fp` (off by default).
- **No block scope in the default edition.** `tur2026.3` still leaks a branch-local `let` to
  the function; block scope is the opt-in `tur2027.1Alpha` (`docs/353`).
- **Borrow lifetimes exist, but NLL does not.** `&T` / `&mut T` are first-class under
  `lifetime_auto` (the 2026.3 default); a stored borrow lives to the end of the function, and
  `lifetime_no` restores the pre-2026.3 parse error. The `safe_borrow` joint domain is design.
- **`defer` exists** (Tur 2026.2): a call only, block-scoped, LIFO, not run on panic/exit.
- **The borrow checker is not Rust's.** E006/E007/E012 + E0240/E041 cover a real subset;
  field-level partial moves are tracked in the reference only, and `ptr` is Copy and unchecked.
- **Effects are reference-only**: `choose lspe_effects_true` opens the syntax, but the
  self-hosted compiler refuses `perform` / `handle` / `resume` with E0217, and general
  (non-tail) handlers + cross-function effects are not implemented.
- **Classes stop at different points**: the reference has C1-C3 (C2 needs `choose runtime`);
  the self-hosted side has C4a (non-virtual) only.
- **No runtime type information, reflection, GC compaction, weak references, or finalizers.**
- **No PE-side FFI**, no PE threads, no PE `proc` bridge; PE also refuses `--profile hotswap`
  and `--fault-heal`.
- **No parallel language construct**: `par` is a design (`docs/285`); the compiler is
  single-threaded.
- **No `lmgr` dimension yet**: `choose lmgr_true` and `loment lmgr` are designed in
  `docs/303`; the standalone `loment/tools/lmgr.lomt` tool is checkout-only.
- **No third-party debugger/LLDB path**: the DAP backend is ptrace-based and self-contained
  because this machine has no gdb/lldb.
- **No `wasm32` target yet**: `docs/313` is a design; there is no wasm emitter.
- **No LomentFaster, Lomenter, DGT, or full Dissigsegv**: each is a design record, not a shipped
  surface (`loment-runtime` 8.6-8.8, `docs/356`). Flomtc is shipped, but it is a compile-speed
  path, not a runtime optimizer.

## 10. These paths exist **only in the source repository** (do not go looking without one)

The repository is FujoOS's Loment line (`loment/`, `lom/`, `tools/loment*.py`,
`docs/14?–16?-loment-*.md`). With a checkout you also get:

- **To actually manage libraries, use `lompi`** (section 0) - it is **installed** and needs no
  repository. The Python tools below are the other set, the older model of `docs/168` (the
  `materialize` path). The two are not the same thing; do not mix them.

- `python tools/loment_dev.py` and `.devcontainer/` - the **development kit** for changing
  Loment itself (`docs/302`): `doctor` (Python version, `PYTHONUTF8`, clang/WSL, gate baseline
  files), `gate` (wraps `python tools/ci.py --static-only`, parallel and UTF-8), `bootstrap`
  (12-14 minutes; WSL on Windows). The gate now has **10 shards**, balanced by measured cost
  (`ci.py` `SHARD_COST`), not by criterion count.
- **Generated artifacts and their emitters**: `python tools/loment_seed.py --emit` rewrites
  `loment/build/selfhost_driver.ll`; `python tools/loment_genesis.py --emit` rebuilds genesis +
  `SHA256SUMS` + `provenance.json` (needs clang + WSL on Windows);
  `python tools/loment_trust.py --emit` rewrites `loment/build/trust.json`; release manifests
  and `SHA256SUMS` are emitted **main-side**, not per PR.
- **Hot-update tooling**: `tools/loment_hotpack.py` (payload), `loment_staging.py`
  (generation directories), `loment_update.py` (`manifest` / `check` / `compare`),
  `loment_hotart.py`, `loment_hotload.py`; the `v*-hot.*` workflows publish the channel.
- **The choose matrix** (`tools/loment_choose_matrix_test.py`) enumerates every legal
  dimension cell and checks the artifact records it; a new dimension makes the matrix grow
  automatically. Flomtc's own criteria are `loment_flomtc_*` and the oracle is
  `loment_oracle_test`.
- `python tools/loment.py lib ...` - **the library system** (`docs/168`): `tree` (dependency tree
  plus each library's instance count), `id` (instance identity), `cap` (capability-requirement
  closure with provenance), `check` (conflicts), `materialize` (flatten nesting and multiple
  versions into a tree the compiler eats directly). **Writing a library teaches you nothing new**:
  a library is a directory; dependencies are the `use` lines in its source (declared nowhere
  else); exports are `pub`; and the optional `pkg.lomp` carries two labels only -
  `pub fn name() -> str` and `pub fn version() -> str` (functions rather than `const`, because the
  language has no string constants);
- `python tools/loment.py diag FILE` - translates error codes into Chinese repair advice (not in
  the package);
- `python tools/loment.py err 诊断.jsonl` - **the reporter itself**
  (`loment/tools/lomenterr.lomt`): it renders the structured diagnostics the compiler writes with
  `--diag-out PATH` into the titled / source-line / caret / suggestion form. **The package has it
  too**, but there the launcher starts it automatically (when `loment check/build/run` fails)
  rather than exposing a `loment err` subcommand - so this is also its development entry point.
  See `docs/182` section 6;
- every example under `loment/examples/` (`tour.lomt` is the whole-language walkthrough);
  `loment/selfhost/` is "the Loment compiler written in Loment";
- documentation: `docs/143` (language spec) · `docs/146` (capability-domain formal semantics) ·
  `docs/148` (toolchain) · `docs/158` (the frozen surface, including what changing the language
  costs) · `docs/145` (milestones) · `docs/154` (status matrix); for the 2026-11 surfaces:
  `docs/299` (lifetimes) · `docs/341` (effects) · `docs/344`/`345` (LSPE framework, classes) ·
  `docs/348` (`fp`) · `docs/351` (borrow) · `docs/353` (block scope) · `docs/361` (LSPE SDK) ·
  `docs/370` (LNotary) · `docs/378` (PE shim);
- the development compile path: `python tools/loment.py ir F.lomt > f.ll` plus
  `python tools/lomelf.py f.ll --target pe -o f.exe` (**the package has no Python, so this does
  not apply there**).

**Repository rules** (they apply only with a checkout): `lom/*.lom` is a **cross-line interface
contract** - changing it takes a commit of its own and telling the compat line; do not change it
in passing during a refactor. `.lomt` is yours to change.
