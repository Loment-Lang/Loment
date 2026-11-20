<!-- translated-from: docs/169-loment-cli.md -->
<!-- source-sha256: 0174f8f05104f99ba549acd310c884279da686af3f639ef5f001687c768cf853 -->

# 169 · The Loment CLI: command surface and look and feel

> Version `0.1.4` (display name **Loment 0.1.4**)
> · Implementation `loment/tools/lomcli.lomt` (**written in Loment itself**) · Criteria `tools/loment_cli_test.py`
> · Upstream: `docs/148` (toolchain), `docs/162` (release package), `docs/159` (dropping Python from the bootstrap)

## 0. In one line

The `loment` command grew from 9 commands to **38**, and **the command surface itself is written in Loment** —
one file, `loment/tools/lomcli.lomt` (~2300 lines), linked into `bin/loment-cli`, with the two launchers, bash
and batch, **each adding one forwarding line** and that is the whole job.

## 1. Why it is written in Loment and not in the launcher's shell

There are **two** launchers: `bin/loment` is POSIX shell, `bin/loment.cmd` is batch (there is no bash on
Windows — double-clicking, or typing from cmd, goes through that one). If the commands lived in the launcher
they would have to be **written twice and kept in sync** — and writing 30-odd commands in batch is a disaster.

Written as one Loment program there is a single implementation. This is not a new road: `lomfmt` / `lomdoc` /
`lomelf` / `lomstatus` / `lompkg` already have this shape (docs/159 Stage 3); this document only moves the
**command surface** over too.

Two things come for free:

- **It joins the bootstrap chain**: the same file is compiled by stage1, and the reference implementation and
  the self-hosted mirror produce **byte-identical** IR (there is a criterion for this). In other words,
  "installing the package must compile the CLI" does not depend on Python.
- **The CLI is one binary behaviour on both platforms**: the same source, linked once as ELF and once as PE,
  with identical command output.

## 2. The command surface (38 commands)

`loment help` prints the overview (sections + alignment + colour), `loment help <command>` prints a single
entry, and `loment commands` prints one name per line (for completion).

| Section | Commands |
|---|---|
| Compile and run | `ir` `check` `build` `run` `version` `lsp` |
| Source tools | `fmt` `doc` `skill` |
| Reading source | `cat` `stat` `count` `fns` `tokens` `grep` `todo` `hash` |
| Project | `ls` `tree` `new` `examples` `example` |
| Language reference | `syntax` `builtins` `types` `keywords` `caps` `codes` `explain` `cheat` |
| This machine | `tools` `where` `env` `doctor` `about` `color` `commands` `help` |

The first two groups (9 commands) are handled by the **launcher itself** and forwarded to the matching tools
(`ir`/`check`/`build`/`run` → `loment-driver` + `loment-lomelf`, `fmt` → `loment-fmt`, …). The other 29 live in
`loment-cli`.

**`lib` / `pkg` are deliberately not in this table.** The library system (docs/168) is today usable only on the
**source repository** side (`python tools/loment.py lib ...`); the release package has no Python and therefore
has none of it — listing it in `help` while typing it can only return "unknown command" would be lying to the
reader. The criterion `test_every_catalog_command_is_actually_dispatchable` exists to guard exactly this
(see §5).

**`lompi` is not in this table either, and for a different reason — this is a hard boundary.** `lompi` is the
package manager for Loment libraries, **installed alongside Loment** (install `loment` and `lompi` is on
`PATH`, with no separate install), but it **is not an official Loment tool**: it does not compile Loment, does
not read the source tree, and is **a different command**.

So: `loment help` / `loment commands` **do not show it**, and `loment <anything>` does **not forward** to it —
the `loment` command surface describes `loment` itself and nothing else. To use lompi, just type `lompi`; it has
its own usage and its own documentation. **Do not** hang it on this table on the grounds that "it installs with
the package": shipped together ≠ parts of the same tool.

## 3. Look and feel (this section is requirements, not decoration)

- **Sections**: six sections, each with a title — not one flat slab of a list.
- **Alignment**: command names and arguments each get a fixed column. **Column width is measured in display
  width, not in bytes** — a CJK character takes two columns, and padding by byte count comes out crooked every
  time (`bin 目录` is 10 bytes but occupies only 7 columns).
- **Colour**: on by default. `--no-color` / `-C` turns it off (position does not matter), and
  `loment color [on|off]` shows or changes the current setting.
- **All output is pure ASCII** (decided after the user hit mojibake in a real run on 2026-09-15, see §3a). That
  includes **decorations** such as `✓` `✗` `→` — they are non-ASCII too, they mojibake on a 936 console in the
  same way, so they were all replaced with `[ok]` `[--]` `MISSING` `->`. This file used to say "decorations may
  use Unicode, it does not matter if they break" — **that trade-off was wrong**: what breaks is not the
  decoration, it is the whole line. A Chinese Windows console decodes the entire UTF-8 run as GBK.
- **Errors go to stderr and are red**, and do not fight with the non-zero exit code.

**Honest boundary**: **there is no TTY detection** — output in a pipe carries colour too; add `--no-color` if
you want it clean. (PE has no `ioctl`; ELF could do it, but the two would then diverge in behaviour, so we
simply do not do it — consistency is preferable.)

### 3a. Why it must be pure ASCII (a hard constraint, not a matter of taste)

On Windows, PE writes the bytes **straight into the console**, and the console decodes them by its **current
codepage** — on Chinese Windows that is **936 (GBK)**, so UTF-8 Chinese comes out as mojibake:

```
"源码统计" 的 UTF-8 字节 e6 ba 90 e7 a0 81 ... 按 GBK 解出来是  婧愮爜缁熻
```

**The shim has no `WriteConsoleW`**, so there is no remedy on the program side (you cannot "tell the console
to use UTF-8"). ASCII is the only set that **decodes to the same result under every codepage** — that is the
whole reason.

The cost should be stated honestly: **the help and reference pages went from Chinese to English.** This is not
opportunistic internationalisation; codepage 936 forced it. The repository's documents, guides and comments are
still Chinese (those are **files being read**, which goes over UTF-8 and has nothing to do with the console).

**One path remains outside this rule**: `loment skill --print` **`cat`s/`type`s a file from the launcher**, and
that guide is Chinese — it mojibakes on a 936 console just the same. It was left alone, because changing it
means changing the language of the guide (that is a **file being read**, which goes over UTF-8 perfectly well;
only the step of "pouring its bytes into the console" does not). To take that path, use `loment skill` (which
prints only the **path**, for an agent to go and read the file), or switch the console to UTF-8 first
(`chcp 65001`).

**A criterion guards this**: `test_every_command_outputs_pure_ascii` runs **every single** command and checks
that stdout+stderr are entirely ASCII — not a spot check, a full sweep. Add a new command and forget this rule,
and it reds immediately.

## 3b. Custom commands: `loment foo` → `loment-foo`

**38 official commands, but the command surface is more than 38.** A user (or software the user installed) can
put an executable named `loment-<name>` on `PATH`, and then `loment <name> ...` works — the way `git` does it:

```bash
# PATH 上有 loment-git，就有了 `loment git`
loment git status        # 转发给 loment-git，参数原样
```

Rules (**the two launchers must agree**; the criteria are in `loment_cli_test`):

| Case | Behaviour |
|---|---|
| `loment <name>` and `PATH` has `loment-<name>` | **forward as-is**: `shift` off the name, not one argument after it is touched, exit code carried out unchanged |
| `loment <name>` but `PATH` does not have it | back to the official CLI — that is "unknown command" (red text + exit 2) |
| `loment <official command>` | **always the official implementation**; a same-named `loment-<official command>` on `PATH` cannot override it |

Three design trade-offs, written down so the next person does not "optimise" them away:

- **Forward rather than delegate to the CLI**: the official CLI is a **compiled** Loment program, and it can
  see only its own 38. "New commands can be added" must therefore happen at the **launcher** layer — the only
  place that can see `PATH`.
- **Official wins**: otherwise installing a `loment-version` would swap out the version number, and `doctor`
  and the criteria would all lose their meaning. This order also means "registering a custom command" can never
  break an existing script.
- **No registry**: the convention is the **filename**. No config file, no central directory — install it and it
  works, remove it and it is gone. (Consistent with the taste that "configuration is Loment source": what the
  filesystem can express does not get a new format.)

## 4. Implementation notes

- **Only 8 cross-platform syscalls**: `read` `write` `close` `brk` `exit` `getdents64` `openat` `newfstatat`
  (the same list as docs/167 §5). That is why the same source behaves identically on ELF and PE.
- **Self-location via `argv[0]`**, not environment variables: the `bin/` directory and `share/loment/` are both
  computed from where the binary itself sits — `doctor` / `where` / `env` / `examples` / `skill` all rest on
  this.
- **One block of brk memory + named offsets** (the same method as `lompkg.lomt`). Loment has no mutable globals
  and no out-parameters, so all state shared across functions goes through that block.
- **On PE `newfstatat` fills only `st_mode`** (measured: `st_size` is always 0), so **file size always comes
  from "read to the end and count"**, never from stat.
- **`argv` is NUL-separated**, so it cannot be measured with `strlen` (that only gives the length of
  `argv[0]`). The real byte count is kept in a global slot, and `arg_at`/`split_argv` both read it.
- **Global flags have to be "compacted away"**: the `--no-color` in `loment --no-color grep P F` is removed in
  place from `argv` before dispatch, so no "take arguments by index" command logic needs to change.

## 5. Traps we fell into (all of them are now in the table above or in a criterion)

1. **Falling off a non-void function → SIGILL**. `ls_dir` was marked `-> u32` so that it could early-exit with
   `return 0;`, but the normal path had no `return` — at the time the checker allowed it and **it crashed at run
   time** (measured as `Illegal instruction`). Any function with a `-> T` must end with a `return`; since
   2026-10-10 the checker refuses the shape outright (E027) instead of letting it through.
2. **The `mut` in `let mut x` is not a keyword**, it is an ordinary identifier (there is a variable genuinely
   called `mut` in `loment/selfhost/checker.lomt`). Writing `let mut len: u32 = ...` means "declare a variable
   called `mut`, then a `len`" — a real syntax error. Local variables do not need `mut`.
3. **Shadowing a parameter makes the self-hosted mirror emit an extra alloca** — the IR is no longer
   byte-identical to the reference. `dispatch` originally had `let argc = strip_flags(...)` shadowing the
   parameter of the same name, and the comparison came out one line short:
   `%argc.addr = alloca i32`. Renaming it was enough.
4. **`ptr` does not become `str` implicitly**, and there is no "make a str from ptr+len" builtin. Computed text
   (paths, version numbers) always goes through `wbuf`; only literals go through `wstr`.
5. **Read a directory level in full before recursing**: the `getdents64` buffer is shared globally, and
   recursing while reading lets a subdirectory's read overwrite it (`lompkg.lomt` hit this first, docs/168 §5).
   Each level gets its own buffer, sliced by depth.
6. **Buffer aliasing**: `read_arg` uses `M_P1` as a path buffer, and `grep`'s pattern is in `M_P1` too — so the
   pattern was overwritten by the filename and **nothing could be found**. The `grep` cases in the criteria
   exist to pin this down.
7. **`--no-color` used to eat the command itself**: the global-flag scan took `argv[1]` as the command, so
   `loment help --no-color` became "explain the command `--no-color`". Changed to compact first, then dispatch.
8. **dispatch passed the pre-strip `argc` to the command**: with `loment grep PAT --no-color` the command got
   the old count, the index ran past the buffer, and it reported "cannot open ". Changed to pass the stripped
   one.
9. **A trailing blank line does not count as a line**: when a file ends with `\n`, the line walk counts that
   last empty segment as a line. Changed to close only on `ls < n`, following the `cat -n` convention.

## 6. Criteria (32 of them, `tools/loment_cli_test.py`, in the gate)

| Group | What it judges |
|---|---|
| Bootstrap | the linked binary runs; **the self-hosted mirror's IR for lomcli is byte-identical to the reference** (through `loment_dist.build_stage1`, the same path as packaging) |
| Catalogue | ≥ 30 commands; `help` mentions every one of them; **every one is really dispatchable** (not "it is listed, but typing it says unknown command") |
| Look and feel | `about` carries ANSI by default and not one escape survives `--no-color`; flags in any position do not swallow the command |
| **Pure ASCII** | the stdout+stderr of **every single command** must be pure ASCII (non-ASCII mojibakes on a 936 console, see §3a) |
| Real arithmetic | `hash` == `hashlib.sha256`; `stat`'s byte and line counts equal Python's own count; `cat`'s line count matches `nl`; `grep`'s line numbers are right, and "no hit exits 1 / missing argument exits 2"; `count`/`fns`/`tokens` against known files |
| Files | directory marks `/`, `tree` indentation reflects depth; the skeleton `new` writes **passes the reference implementation's check + emit**; `new` does not overwrite an existing file |
| Self-location | `version` reads `share/loment/version`; `doctor` exits 1 in red when a component is missing and 0 in green when the set is complete (**it has resolving power**); `where` resolves paths, and exits 2 on a name it does not recognise |
| Anti-drift | the sets of commands the two launchers handle by name **agree with each other** and **are all in the catalogue**; both launchers are pure ASCII; the fallback **forwards** to loment-cli rather than each writing its own usage |

**Falsified** (we broke the criteria on purpose to confirm they red): putting a command into the batch launcher
that is not in the catalogue → caught; changing the bash fallback from "forward" back to "print its own usage"
→ caught; adding one Chinese character to a launcher → caught; raising the catalogue threshold by one →
caught. Items 3, 7, 8 and 9 in §5 were likewise **red before the fix and green only after it**.

## 7. Reproducing

```bash
python tools/loment_cli_test.py                     # 32 条判据 (本机原生跑生成的 PE)
python tools/loment_dist.py --emit                  # 四个产物; bin/loment-cli 从 lomcli.lomt 编出来
loment help                                         # 装完之后看总览
loment commands                                     # 拿命令名列表
loment --no-color codes                             # 不要转义序列
```

## 8. Not done yet (honest list)

- **Per-command manual pages cover only 13 commands** (`ir`/`check`/`build`/`run`/`fmt`/`grep`/`new`/`ls`/
  `tree`/`color`/`explain`/`skill`/`help`). For the rest, `help <name>` gives one line saying "no more detailed
  manual page" and points back to the overview — no pretending.
- **No TTY detection** (§3).
- **`tokens` is a lexical approximation** (scanning identifiers/numbers/strings/comments by byte), not a real
  lexer; exact lexical results need `lomc`.
- **`grep` is substring matching, not a regex.**
- **`tree` is capped at 16 levels deep and 256 entries per directory** (silently truncated beyond that) — a
  guardrail, not a feature.
