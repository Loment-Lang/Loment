# Loment for VS Code

Language support for **Loment** — a systems programming language: an explicit, capability-aware
language whose native surface syntax is Rust-flavoured, and whose semantics can equally be written
in **six other surface grammars** (C, C++, Java, C#, Go, Python). It compiles to native executables
with its own backend, and the compiler is written in Loment itself.

Source and toolchain: <https://github.com/Loment-Lang/loment>

> **This extension ships no compiler.** The language server, the build/run/check commands and the
> debugger all go through a Loment checkout in your workspace (`tools/loment_lsp.py`, `lomentc.py`,
> `loment.py`). Without one, only syntax highlighting is active — see [Requirements](#requirements).

## Features

| Feature | Comes from | Notes |
|---|---|---|
| Syntax highlighting | `syntaxes/loment.tmLanguage.json` (`.lomt`) + `syntaxes/lom.tmLanguage.json` (`.lom`) | keywords, types, builtins, capability domains, comments, strings, numbers, operators |
| Syntax highlighting for the **seven surface grammars** | `syntaxes/loment-<canonical>.tmLanguage.json` | when a file's head says `choose write grammar python` (c / cpp / java / csharp / go / natural likewise), the document switches to `loment-<canonical>` and is coloured by the **host** grammar — see below |
| Completion | `tools/loment_lsp.py` → `textDocument/completion` | keywords, types, and the symbols declared in the file (fn / struct / enum / trait / const / capability) |
| Go to definition | same → `textDocument/definition` | jumps to the declaration line in the same file |
| Diagnostics | same → `textDocument/publishDiagnostics` | type checking on open/edit, reported in the Problems panel |
| Formatting | same → `textDocument/formatting` (implemented by `lomfmt`) | formatting is done **by the language server**; the extension starts no process of its own |
| Emit LLVM IR | command → VS Code task running `tools/lomentc.py` | writes `<buildDir>/<stem>.ll` and opens it |
| **Build an executable** | command → `loment build FILE` (or `scripts/lomc.ps1`) | produces a native executable (Linux ELF) |
| **Build and run** | command → `loment run FILE` (or `lomc.ps1 -Run`) | runs it immediately, output in the terminal |
| Check | command → `tools/loment.py diag` | output in the terminal |
| Run `test_*` functions | command → `tools/loment.py test` | output in the terminal |
| Debug (F5) | `tools/loment_dap.py` (DAP with a `ptrace` backend) | breakpoints / stepping / call stack — see [Debugging](#debugging-f5) |
| Run on the FujoOS kernel | command → `tools/loment_boot.py` | boots a Loment program under QEMU on the FujoOS kernel; needs a kernel image, and is not needed for normal use |

Every command is available from the Command Palette under `Loment:`; **Build** and **Build and run**
are also in the editor context menu, and **`Ctrl+Shift+B`** (Run Build Task) builds the current file.

### The seven surface grammars (`choose write grammar`)

A Loment source may declare, in its **file head**, which *writing style* it is written in:

```
choose write grammar python
module demo
...
```

The extension reads that line, switches the document to `loment-<canonical>`
(`loment-python` / `loment-c` / `loment-cpp` / `loment-java` / `loment-csharp` / `loment-go` /
`loment-natural`) and colours it with the **host** grammar (the Python / C / C++ / Java / C# / Go
extensions). `rust` and `loment` are *native* spellings and are not switched.

Three things worth knowing:

- If the **host extension is not installed**, only the declaration line is coloured in that file.
  That is deliberate: painting foreign source with Loment's rules would give colours that *look*
  like highlighting and are wrong. (`natural` has no host grammar to borrow in the first place.)
- **Those files are not attached to the language server**: both language servers in the repository
  parse every document as native Loment, so attaching them would only produce false diagnostics.
- **Build / run / debug / breakpoints still work** — it is still a Loment program, just written in
  another style.

If a project renames its L1 source extension through `source_ext()` in `loment.conf` (the default is
`.lomt`), files with that extension are recognised as Loment too: the extension walks up to find
that conf. Without a conf, list the suffixes in the `loment.extraExtensions` setting.

### Debugging (F5)

F5 is *Start Debugging*, so it needs a real debugger — the extension registers a debug adapter of
type `loment` (a `debuggers` contribution plus a `DebugAdapterDescriptorFactory`).

The first time you press F5, VS Code asks which configuration to use; choose **Loment** and it
generates this (or write it yourself in `.vscode/launch.json`):

```json
{
  "type": "loment",
  "request": "launch",
  "name": "Debug current Loment file",
  "program": "${file}",
  "stopOnEntry": false
}
```

Click in the gutter to set a breakpoint, F5 to start, F10 to step over, F11 to step in, Shift+F11 to
step out, F5 to continue.

**What it needs**: the backend is `ptrace`, so the adapter runs under **WSL**
(`wsl -e python3 …`), and the DWARF-carrying ELF is compiled with **LLVM on the Windows side**
(`clang.exe` / `llvm-objdump.exe`). If either is missing, F5 reports it explicitly rather than
starting a session that does nothing.

**Not in this version**: conditional breakpoints, expression evaluation / hover values, source-level
locals (the *Variables* panel lists registers), multi-frame stack unwinding, threads. `stepOut` is
an approximation that steps until the function changes.

### Which path a build takes

This is an environment question, not a configuration one, so the extension detects it
(`src/build-cmd.js` — a pure module, testable headlessly):

| Order | Condition | What is used |
|---|---|---|
| ⓪ | the file has no `fn _start` and you asked to *run* | **refused, with an explanation** — that is a library module: it links into an ELF but segfaults immediately. *Build* still works |
| ① | `loment.toolCommand` is set | the installed Loment CLI: `<cmd> <args> build FILE [-o NAME]` / `run FILE`. **No Python needed** |
| ② | Windows, and `scripts/lomc.ps1` is found by walking up from the workspace or from the file | the development-tree path: seed + clang + WSL, producing `.ll` + `.elf`. **No workspace folder required** — opening a single `.lomt` is enough |
| ③ | neither | **an explicit error** naming the setting to fill in — never silence (a command that does nothing is the hardest kind of failure to diagnose) |

> Note: the repository's Python CLI has **no single-file "compile to executable" command** —
> `loment build DIR` is an *incremental, whole-directory* build that only emits IR. Single-file
> executables come from the two paths above.

## Requirements

- No npm packages need to be installed — they are bundled into the VSIX. What it does need:
- **Python** (a `python` on `PATH`) — the language server and the toolchain are Python implementations;
- **a checkout containing `tools/loment_lsp.py`** — the extension walks up from the workspace to find
  it, or you can point `loment.serverPath` at an absolute path.

The interpreter is fixed to `python`. If you only have `py` / `python3`, change `SERVER_COMMAND` at
the top of `src/extension.js` (**this is deliberate**: the executable position holds a literal only,
configuration strings never take part in constructing a command).

**Neither the language server nor build/run need Python**: use `loment.serverCommand` for the former
and `loment.toolCommand` for the latter.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `loment.serverPath` | empty | absolute path to `tools/loment_lsp.py`; when empty, searched upward |
| `loment.serverCommand` / `serverArgs` | empty | start the language server with another command (**no Python needed**), e.g. `wsl` + `["-e", "/home/<you>/.local/share/loment/lsp"]` |
| `loment.toolCommand` / `toolArgs` | empty | which Loment command *Build / Run* uses, e.g. `wsl` + `["-e", "/home/<you>/.local/share/loment/bin/loment"]` |
| `loment.dapScript` | empty | absolute path to the debug adapter `tools/loment_dap.py`; when empty, searched upward |
| `loment.dapCommand` / `dapArgs` | empty | start the debug adapter with another command. Default: `wsl -e python3 <dapScript>` on Windows, `python3 <dapScript>` elsewhere |
| `loment.enableLsp` | `true` | turn off to keep only highlighting and the commands |
| `loment.extraExtensions` | `[]` | extra suffixes to treat as L1 source (e.g. `[".foo"]`). Not needed when the project has a `loment.conf` |
| `loment.buildDir` | `loment/build` | output directory for `.ll` / `.potato.json`, relative to the workspace root |

**Workspace Trust**: the eight settings that can turn into "what gets executed"
(`serverCommand` / `serverArgs` / `toolCommand` / `toolArgs` / `dapCommand` / `dapArgs` /
`serverPath` / `dapScript`) are listed in
`capabilities.untrustedWorkspaces.restrictedConfigurations` — in an **untrusted** workspace a
`.vscode/settings.json` cannot change them and the extension falls back to searching. In a trusted
workspace they are overridable as usual.

## Install / packaging

**From the Marketplace** (recommended): search for `Loment` in VS Code, or

```bash
code --install-extension hooyan2022.loment
```

<https://marketplace.visualstudio.com/items?itemName=hooyan2022.loment>

Working inside the repository:

```bash
# one command: repackage + side-load into the local VS Code (then Developer: Reload Window)
python tools/vscode_ext.py --install
# just produce a .vsix
python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
# health-check the installed copy (run this first when highlighting misbehaves)
python tools/vscode_ext.py --doctor
```

Packaging requires one `npm install --omit=dev` in the workspace first (it puts
`vscode-languageclient` into `editors/vscode/node_modules`, which the VSIX ships inside
`extension/node_modules`). The `.vsix` bytes are **deterministic** (fixed timestamps), so it can go
into a checksum manifest.

`--install` **does not use `code --install-extension`**: that path frequently fails in sandboxed
environments (`Code.exe` reports "the system cannot find the file specified" while `cmd.exe` is
fine — it is not a permissions problem). It unpacks into `~/.vscode/extensions/<publisher>.<name>-<version>/`
following VS Code's own layout, and rewrites the registration in `extensions.json` to point at that
real directory.

> **After installing, always run `Developer: Reload Window`.** Language associations, grammars,
> commands and debuggers are registered **at window start** — without a reload you keep the old
> ones (the files are there and `--doctor` is green, yet nothing appears to have been installed).
>
> When side-loading, a backup of the old copy **must not stay inside `extensions/`**: any folder
> there containing a `package.json` is scanned as an extension, and VS Code may register the
> extension under the name that is about to be deleted — after which highlighting, commands and F5
> all disappear while everything on disk "looks fine". `--install` moves backups to the system temp
> directory, and `--doctor` verifies that the registration points somewhere real.

## Headless verification (no GUI needed)

```bash
python tools/vscode_ext_test.py
```

It checks the manifest/grammar structure (compiling every regex), that `server-path.js` really finds
the server, a **full LSP round trip against the real language server** (initialize → didOpen →
completion → definition → formatting), the `.vsix` structure, that **`activate()` really runs**
(with every declared command, task and debugger registered), that **the surface-grammar declaration
reader agrees with the reference implementation** over 769 in-tree sources, that **opening a document
really switches its language id**, the untrusted-workspace declarations, and the stale-directory
cleanup on upgrade.
