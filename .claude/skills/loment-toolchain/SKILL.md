---
name: loment-toolchain
description: Loment's toolchain and extension points - the CLI commands, LSP/DAP/VS Code, the L0 contract and LLS standard, packaging and signing, the library system, and repository-only development paths. Read when operating the toolchain, writing a library, or contributing to the compiler repo. Part of the LomentSkill suite; section numbers are global.
---

## 8.11 Tools and editors

**This file is part of the LomentSkill suite.** Section numbers are **global across the suite**, so a
reference like "section 6.2" or "section 8.3" means the same section no matter which skill file
you are reading. The entry skill (`loment`) carries the map; this file carries its own sections.


| Tool | What it does |
|---|---|
| `loment check` / `ir` / `build` / `run` | the driver; diagnostics from `lomenterr` (position, source line, caret, what/why/at least three fixes, supported/not supported) |
| `loment lsp` | language server over stdio (completion, definition, diagnostics; **no formatting** - it does not declare a formatting provider). The packaged binary is built in the **hotswap** profile |
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

**Boundary**: the packaged CLI's own `codes` page still prints "E1-E23" while the reporter knows
E001–E038. Trust the codes section of this guide (and `loment explain E34`), not that banner.

### 8.12 L0, the language standard, and packaging

- **`lom/*.lom` is the L0 interface contract, not an implementation detail.** It is the
  cross-line surface other components compile against (for example the content-trust
  primitives `qual_feed` / `qual_seq` and the admission gate `dom_admit`). It is generated into
  spec files and checked by `fuai_contract_check`. **Changing it is a contract change**: its own
  commit, naming who depends on it, what changed and whether downstream must move - never a
  drive-by edit during a compiler refactor.
- **`standard/LLS.md` is the language standard.** It is one "frame", not a second
  specification, and **its version is the language version - one number**. §4 is the **switch**
  table: Loment is defined by what it switches on, not by what it lacks (`no_gc` moved from
  "planned" to a core value; `no_runtime` is a guarantee the linker checks; `vm` is accepted but
  does not change the artifact yet, because the interpreter is a separate tool).
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
  closures, no function pointers/indirect calls, no `impl Trait`, no `dyn`, no exceptions.
- **No block scope for `let`** today (a branch-local `let` is visible after the branch).
- **No lifecycle annotations** (`&T` in type position) - Tur 2026.3 is a design.
- **No `defer`** - designed for Tur 2026.2 but not implemented.
- **No moves/borrow checker in the Rust sense**: E006/E007/E012 cover a small ownership
  subset; `ptr` is Copy and unchecked.
- **No runtime type information, reflection, GC compaction, weak references, or finalizers.**
- **No PE-side FFI**, no PE threads, no PE `proc` bridge.
- **No parallel language construct**: `par` is a design (`docs/285`); the compiler is
  single-threaded.
- **No `lmgr` dimension yet**: `choose lmgr_true` and `loment lmgr` are designed in
  `docs/303`; `CORE_DIMS` still has seven dimensions and the `lmgr` CLI is not shipped.
- **No third-party debugger/LLDB path**: the DAP backend is ptrace-based and self-contained
  because this machine has no gdb/lldb.

## 10. These paths exist **only in the source repository** (do not go looking without one)

The repository is FujoOS's Loment line (`loment/`, `lom/`, `tools/loment*.py`,
`docs/14?–16?-loment-*.md`). With a checkout you also get:

- **To actually manage libraries, use `lompi`** (section 0) - it is **installed** and needs no
  repository. The Python tools below are the other set, the older model of `docs/168` (the
  `materialize` path). The two are not the same thing; do not mix them.

- `python tools/loment_dev.py` and `.devcontainer/` - the **development kit** for changing
  Loment itself (`docs/302`): environment doctor, gate shard plan, bootstrap.
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
  costs) · `docs/145` (milestones) · `docs/154` (status matrix);
- the development compile path: `python tools/loment.py ir F.lomt > f.ll` plus
  `python tools/lomelf.py f.ll --target pe -o f.exe` (**the package has no Python, so this does
  not apply there**).

**Repository rules** (they apply only with a checkout): `lom/*.lom` is a **cross-line interface
contract** - changing it takes a commit of its own and telling the compat line; do not change it
in passing during a refactor. `.lomt` is yours to change.
