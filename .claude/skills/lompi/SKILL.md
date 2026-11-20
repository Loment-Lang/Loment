---
name: lompi
description: Use when managing Loment libraries with lompi, the package manager that ships with the Loment toolchain (a library is a directory; identity is a content hash; dependencies are the `use` lines in source). Triggers: installing, listing, or verifying a third-party Loment library / building or maintaining a package store / finding out which Loment libraries exist on this machine, or what a store contains / locating a library's source to read its `pub fn` surface / finding out what a library depends on / producing or checking a lockfile / debugging "missing dependency", "dependency cycle", or "lock does not match store" / questions about how packages are installed in Loment, which version gets picked, or whether two versions can coexist / mapping concepts over from pip, npm, or cargo. Also use for anything involving a Loment package store, `lompi.lock`, `deps/`, instance identity, or resolving `.lomt` library dependencies.
---

# lompi: the Loment package manager

**lompi ships with the Loment toolchain** (from 0.1.4 final onward it is installed together
with `loment`, so `lompi` is already on PATH). It manages **Loment libraries** — not
executables, not binary distributions.

> On an older loment with no `lompi` on PATH: it is written in Loment itself, so
> `loment build lompi.lomt -o lompi` inside the `lompi/` source directory builds it
> (**you must run it from that directory** — path imports resolve against the CWD).

**All of lompi's output is ASCII-only, on purpose.** On a 936 (GBK) Windows console the
PE runtime writes UTF-8 bytes through `WriteFile` and the console decodes them as GBK, so
any non-ASCII turns into mojibake; the runtime has no `WriteConsoleW` to fall back on.
Rather than ask you to fix your console, lompi simply never prints non-ASCII. (Other
Loment programs that print non-ASCII do need `chcp 65001` or
`[Console]::OutputEncoding = [Text.Encoding]::UTF8`.)

## 1. The model — why this is not a pip clone

| | pip / npm / cargo | lompi |
|---|---|---|
| Where dependencies come from | restated in a manifest | **the `use` lines in source**; never restated |
| Identity | name + version | `id(P) = H(own source ⊕ {edge name → id(child)})` |
| Versions | compared, ranged, backtracked | **a human-readable label only**; the machine uses the hash |
| Two versions at once | a conflict to resolve | **allowed** — two versions are two different instances |
| Registry | a server | none; a store is just a directory |
| Distribution | binaries + headers | source (generics are type-directed monomorphization, so the consumer must see source) |

In short: pip's hard part is *picking a version*. lompi's hard part is *getting the
instance identity exactly right*. There is exactly one selection rule — `use name` binds
to the **highest available version** of that name, compared numerically per `.` segment
(so `0.10.0 > 0.9.0`). There is no range language, because identity is a hash and ranges
would have nothing to say about it.

## 2. What a store looks like

```
<store>/
  <name>/
    <version>/
      <name>.lomt     <- the entry file. Its name MUST match the package name
      *.lomt          <- the package's other modules
      pkg.lomp        <- optional. Two human-readable labels
```

`pkg.lomp` is **optional** and is ordinary Loment source (the language has no string
constants, so the labels are functions):

```rust
module pkg

pub fn name() -> str {
    return "mathutil";
}

pub fn version() -> str {
    return "0.1.0";
}
```

Without it the directory name is the package name and the version is `0.0.0`. Note that
`name()` / `version()` are only labels: lompi lists a store by **directory name**, and
`pkg.lomp` does not take part in the content hash. A `deps/` directory inside a package
is not counted as that package's source (otherwise a vendored copy would leak into its
identity).

## 3. Commands

```
lompi install <name> [--into DIR] [--registry URL]  fetch -> validate -> install a library
lompi check   <dir>                                 is this a valid Loment library?
lompi fetch   [--registry URL]                      plan the registry download
lompi config                                        show global root + registry (and the rule)
lompi version                                       print the version
lompi list    [<store>]                             list packages in the store
lompi show    [<store>] <name[@version]>            metadata / use edges / identity
lompi tree    [<store>] <name[@version]>            dependency tree
lompi resolve [<store>] <name[@version]>            print the lock (hash-pinned)
lompi verify  [<store>] <name[@version]> <lockfile> recompute and compare byte-for-byte
lompi plan    [<store>] <name[@version]> [--into DIR]  closure + materialization plan
lompi hash    <package-dir>                         own-source hash of one package
lompi obj     add [<store>] <file.o>                content-addressed object into <store>
lompi obj     get [<store>] <sha256> [--out OUT.o]  fetch an object's bytes (no compiler needed)
lompi help
```

**`<store>` is optional on every verb above.** Omit it and the **global store** is used —
exactly the directory `lompi config` prints on its `store:` line. `lompi show mathutil` and
`lompi show <store> mathutil` take the same code path (the dispatcher resolves the store once),
so they cannot drift apart. Both spellings stay supported forever: scripts that pass a store
explicitly keep working. `index` is kept as a synonym for `list`.

The reason is the same one pip has: a package manager's user should not have to know where the
library lives. Making the store a required argument turns "where is your library" into a field
you retype on every call — that is something the machine should remember, not the person.

`lompi obj` is how a **pre-optimized object** rides along with the store: `add` writes it as
`<store>/obj-<sha256>.o` (its own content address), `get` pulls the bytes back. The point
(`docs/212` §5 B): the heavy kernel is compiled **once** on a machine that has clang, and every
other machine fetches the bytes and links them (`loment build app.lomt --link obj-….o`) **without
a compiler**. Flat layout on purpose — `sys_mkdir` does not exist on the PE side.

`<name[@version]>`: `mathutil` means "highest version", `mathutil@0.1.0` pins one.

## 3.5 Registry, global root, and where `install` puts things

lompi has a **registry**: an optional git repository laid out exactly like a store
(`<name>/<version>/*.lomt`). It is configured in `lompi.conf`, a **Loment source file that
sits next to `lompi.exe`**:

```rust
module conf

pub fn registry() -> str {
    return "https://github.com/you/loment-libs";
}
```

`--registry <url>` overrides it for one invocation. Same shape as `pkg.lomp` — no new
format to learn, and lompi reads it with the same label scanner it uses for manifests.

**Global root.** `lompi config` prints the effective root *and the rule it used*, because
it has to be derived rather than looked up (the PE runtime has no `getenv`, and
`/proc/self/environ` is unreadable — only `cmdline` is synthesized). The rules, in order:

| rule | when | root |
|---|---|---|
| 0 | `argv[0]` is under `\AppData\Local\` | `<that>\lompi` — i.e. `%LOCALAPPDATA%\lompi` |
| 1 | `argv[0]` is under `\Users\<name>\` | `<that>\.lompi` |
| 2 | neither | `<exe dir>\.lompi` |

Under the root: `store\` (the global store) and `cache\` (cloned registries).

**Where `install` puts the package.** No `--into` -> the **global store**, i.e.
`<root>\store\<name>\<version>\`. With `--into DIR` -> `DIR\<name>\`, which is the flat
layout the compiler's `use <name>` actually expects. The *source* is the cached registry
when a registry is configured and has been fetched, otherwise the global store.

## 4. Exit codes

| code | meaning |
|---|---|
| `0` | success (including `lompi help`) |
| `1` | ran and failed: store unreadable / dependency cycle / dependency missing / no such package / lock does not match |
| `2` | usage error: missing argument, unknown command |

Diagnostics go to stderr, normal output to stdout — so `lompi resolve ... > lompi.lock`
produces a clean file.

## 5. Command by command

### 5.0 `install` — get a library, check it, put it somewhere

```
$ lompi install geom --into D:\proj\deps
# lompi install plan
# source:   C:\Users\hooya\.lompi\store  (global store)
# validated: geom@1.0.0 and its closure -- all modules/entries check out
# dest:     --into <DIR> -> DIR\<name>\ (flat, as the compiler expects)
mkdir -p D:\proj\deps
# 2 package instance(s) to materialize:
cp -r C:\Users\hooya\.lompi\store/geom/1.0.0 D:\proj\deps\geom
cp -r C:\Users\hooya\.lompi\store/mathutil/0.2.0 D:\proj\deps\mathutil
# lock (pin these; `lompi verify` compares against the store that produced them)
# lompi lock v1
geom 1.0.0 7af928612aaaf1994c08642a8abf7b55c667159d6fff53c0330c09b79692ded0
mathutil 0.2.0 492ec331d34a26a9f5b524572314eeda47795ed7fb61fcf09910b4a7b85449e3
```

Order of operations: locate the package -> **validate it** -> compute the closure and
identities -> emit the plan -> (with `--apply`) write the files. It refuses to install
anything that fails validation (see 5.0.1) and says which rule failed.

**`--apply` makes it real.** Without it you get the plan and nothing is touched. With it,
lompi writes the files itself — but the PE runtime it is compiled against has no `mkdir`
yet (a toolchain gap, not a lompi decision: on ELF targets it is there, see 5.6), so it
checks **every** target directory first and refuses with the full list of `mkdir`
commands, rather than copying half and leaving a partial install:

```
$ lompi install geom --into D:\proj\deps --apply
mkdir -p D:\Dev\...\_try\deps
# 2 package instance(s):
cp -r C:\Users\hooya\.lompi\store/geom/1.0.0 D:\Dev\...\_try\deps\geom
...
[BAD] lompi: refusing --apply: target director(y/ies) do not exist.
      lompi cannot mkdir on PE -- create them first:
      mkdir -p "D:\Dev\...\_try\deps\geom"
      mkdir -p "D:\Dev\...\_try\deps\mathutil"
```

Create those two directories and re-run, and it copies byte-for-byte (including
`pkg.lomp`). Because it truncates-and-rewrites, install is idempotent.

**`--from-lock F` reproduces a build.** Instead of resolving "highest version", lompi
installs exactly the instances the lock pins — and **refuses if any of them is absent or
its identity changed**:

```
$ lompi resolve C:\Users\hooya\.lompi\store geom > good.lock
$ lompi install geom --from-lock good.lock --into ... --apply   # reproduces those exact ids

$ lompi install geom --from-lock other-store.lock --apply
lompi: lock does not match this store: geom
  (the locked instance is absent, or its identity changed)
```

That last refusal is the whole point of pinning content hashes instead of version numbers:
a lock taken from a store where `geom` bound a different `mathutil` names a *different
instance*, and lompi says so instead of silently installing a lookalike.

**`<name>` comes first and is never optional — not even with `--from-lock`.** A flag sitting
in the name position is read as a package name, and the diagnostic misleads you about the
cause:

```
$ lompi install --from-lock good.lock      # looks reasonable, is not
lompi: no such package in store            # it went looking for a package named "--from-lock"
$ lompi install                            # the real missing-name diagnostic, for contrast
lompi: install needs a <name>
```

(No name at all exits **2**; a flag in the name position exits **1** with that misleading
message. So write it as `lompi install geom --from-lock good.lock --into DIR --apply`.)

### 5.0.1 `check` — is this actually a Loment library?

```
$ lompi check fixture/store/geom/1.0.0
[ok] source files: 1
[ok] package name: geom
[ok] every module name matches its file name
[ok] pkg.lomp name() matches
[ok] pkg.lomp version() matches
[OK] lompi: this is a valid Loment library

$ lompi check fixture/bad/mismatch/1.0.0
[ok] source files: 1
[ok] package name: mismatch
[BAD] module name does not match file name: mismatch.lomt declares `wrongname`
[ok] no pkg.lomp (optional)
[BAD] lompi: 1 check(s) failed
$ echo $?
1
```

Every rule is a precondition for `use <name>` working after install, not a style check:

- the directory is readable and contains at least one `*.lomt`;
- there is an **entry file named after the package** (`<name>.lomt`) — the compiler
  resolves `use <name>` to exactly that path. The package name is inferred: the directory's
  own name if `<dirname>.lomt` exists, else the parent's name if `<parent>.lomt` exists —
  so both `check <store>/<name>/<version>` and `check <name>` work;
- every file's `module <x>` declaration matches its **file name** (in a flat namespace that
  *is* the module's identity);
- each file is readable and within the size cap (256 KiB) — past it lompi refuses rather
  than truncate, because a truncated hash is a *wrong* identity;
- `pkg.lomp`, if present, agrees with the directory (`name()` matching is a failure,
  `version()` mismatch is a warning).

### 5.0.2 `fetch` — download the registry (0.2.0: lompi does this itself)

```
$ lompi fetch --registry http://127.0.0.1:8000/libs
lompi: registry fetched into C:\Users\hooya\.lompi\cache\8000
```

It really does, over a raw socket, on **both** targets: `GET <base>/lompi-index.txt`,
then one `GET <base>/<path>` per line of the index, **verifying the sha256 the index
states** — a mismatch means the file is refused, not written. The result lands in
`<global root>\cache\<slug>\`, which is exactly where `install` looks (§5.6).

The index is a text file, one line per package:

```
<name> <version> <path> <sha256hex>
```

**What it will not do.** If the registry is not `http://` with an IPv4 literal, `fetch`
does not fail silently — it says which piece is missing and then prints the clone plan
you can run instead:

```
$ lompi fetch --registry https://github.com/you/loment-libs
# lompi will not fetch this registry itself: only http:// with an IPv4 literal
#   (https needs TLS; a hostname needs a resolver -- neither is in the toolchain)
# registry: https://github.com/you/loment-libs  (from --registry)
mkdir -p C:\Users\hooya\.lompi\cache
git clone --depth 1 https://github.com/you/loment-libs C:\Users\hooya\.lompi\cache\loment-libs
# to update later:  git -C C:\Users\hooya\.lompi\cache\loment-libs pull
```

So the plan path is now a **fallback with a stated reason**, not the only behaviour:
`https://` and hostnames are the two shapes it hands back. Also outside the contract —
3xx redirects (non-200 is a failure) and a response larger than the buffer (refused, not
truncated). The directory it writes into must already exist in the `cache\` tree; on a
write failure it prints the exact `mkdir -p` (the same PE gap as §5.6) rather than
leaving you with a partial cache. Run the printed command, then `lompi install <name>`
will find the package in the cache.

### 5.0.3 `config` and `version`

```
$ lompi version
lompi 0.2.0

$ lompi config
version:  0.2.0
exe:      C:\Users\hooya\.local\bin
global:   C:\Users\hooya\.lompi
  rule:   argv[0] sits under \Users\<name>\, so <that>\.lompi
store:    C:\Users\hooya\.lompi\store
cache:    C:\Users\hooya\.lompi\cache
conf:     C:\Users\hooya\.local\bin\lompi.conf  (absent)
registry: (not set) -- install will use the global store
```

`config` exists mainly so a mis-derived root is **visible** rather than silently sending
installs to the wrong directory.

### 5.1 `index` — what is in this store

```
$ lompi index fixture/store
5 package(s) in store
geom 1.0.0 7af928612aaaf199
mathutil 0.1.0 5d46f4477237f3bf
mathutil 0.2.0 492ec331d34a26a9
multi 2.0.0 5d6cff93ffc7edb1
util 0.3.0 0dc87d1ec3449e92
```

Each line is `name version first-16-of-identity`. **The same name appearing twice is
normal** — those are two instances (multi-version coexistence), not duplicates. The first
line counts **instances**.

### 5.2 `show` — the full picture of one package

```
$ lompi show fixture/store geom
name:    geom
version: 1.0.0
id:      7af928612aaaf1994c08642a8abf7b55c667159d6fff53c0330c09b79692ded0
dir:     fixture/store/geom/1.0.0
1 use edge(s):
  name mathutil
```

`id` is the **instance identity** (64 hex chars) — compare it against the same package in
another build to know whether they are the same thing. The edges come in two kinds:

- `name <name>` — a dependency on **another package**. Resolution, identity, and the lock
  all use these.
- `path <path.lomt>` — an **intra-package** reference (`use "io.lomt"`). Listed so you can
  see it, but the file it points at is already part of this package's own source, so it
  does **not** take part in resolution or in the Merkle hash.

> Boundary: a path edge that points **outside** the package (`use "../other/x.lomt"`) is
> not tracked. Cross-package references should always be written `use <name>` and live in
> the store.

### 5.3 `tree` — the dependency tree

```
$ lompi tree fixture/store_diamond diamond
diamond@1.0.0 12743554830f
  geom@1.0.0 711abded0e3c
    mathutil@0.1.0 5d46f4477237
  mathutil@0.1.0 5d46f4477237  (already shown)
```

Indentation is depth. When a diamond dependency makes the same package appear a second
time it is marked `(already shown)` rather than expanded again — it is **one instance**,
and expanding it twice would misrepresent the tree.

### 5.4 `resolve` — produce a lock

```
$ lompi resolve fixture/store geom > lompi.lock
$ cat lompi.lock
# lompi lock v1
geom 1.0.0 7af928612aaaf1994c08642a8abf7b55c667159d6fff53c0330c09b79692ded0
mathutil 0.2.0 492ec331d34a26a9f5b524572314eeda47795ed7fb61fcf09910b4a7b85449e3
```

Each line is `name version identity`. **The version is for humans; the identity is what the
machine uses.** This file is the input to `lompi verify`, and it is also a good record of
"what exactly did this build use" to commit alongside the project.

### 5.5 `verify` — is this still the store that lock describes

```
$ lompi verify fixture/store geom lompi.lock
[OK] lompi: lock matches store (172 bytes)

$ lompi verify fixture/store geom other.lock      # lock taken from another store
[DIFF] lompi: lock does not match store
$ echo $?
1
```

This is **not** "check the version numbers". It recomputes the lock and compares
**byte for byte**, so any source edit, dependency version change, or swapped package is
caught. Green `verify` means this build is the same thing as last time.

### 5.6 `plan` — the materialization plan (this is the install)

```
$ lompi plan fixture/store geom --into mydeps
# lompi plan (target dirs must already exist -- no mkdir on PE)
mkdir -p mydeps
# 2 package instance(s) in the closure:
#   geom@1.0.0 7af928612aaa
#     from fixture/store/geom/1.0.0
#   mathutil@0.2.0 492ec331d34a
#     from fixture/store/mathutil/0.2.0
```

It resolves the **closure** (which instances are needed) and each instance's **source
directory**, and prints the one `mkdir` that is required (`--into` sets the target,
defaulting to `deps`).

**Why a plan instead of writing files directly**: the Windows PE target has no `mkdir` /
`unlink` in its syscall surface — it can write into directories that already exist, but
cannot create one. Installing a package means creating
`deps/<name>/`, so that step is handed to the caller (a shell, CI, or the reference
toolchain). On ELF targets `mkdir` is available and the same plan can be executed
directly. **The boundary belongs to the target platform, not to lompi.**

Every plan line starting with `#` is a comment, so the whole output can be piped straight
into a shell.

### 5.7 `hash` — own-source hash of a single package

```
$ lompi hash fixture/store/multi/2.0.0
files: 3
self:  61ee6f19964aa745f1ed2d4bdf60f0fdd9b49d7f1fc96fc66211c60e82c9033e
```

`self` is the hash of **this package's own source only**, which is **not** the instance
identity from `show` — the instance identity also mixes in the identities of its
dependencies (§8). Use this command to see whether the package body itself changed.

## 6. Reading the store: what does this machine have?

The entry point for "which Loment libraries are available here", "what is in the store",
"where is the source for X".

**Step 1 — locate the store. Ask, do not guess.**

```
$ lompi config
version:  0.2.0
exe:      C:\Users\hooya\.local\bin
global:   C:\Users\hooya\.lompi
  rule:   argv[0] sits under \Users\<name>\, so <that>\.lompi
store:    C:\Users\hooya\.lompi\store
cache:    C:\Users\hooya\.lompi\cache
conf:     C:\Users\hooya\.local\bin\lompi.conf  (absent)
registry: (not set) -- install will use the global store
```

The `store:` line **is** the store. The root is derived from **where the exe sits**, not
compiled in — the same exe in two different locations reports two different roots — so ask
the exe you will actually use and take that line verbatim. Never construct the path
yourself.

> A copy of lompi that lives in a build tree rather than on PATH falls to the last rule
> (`<exe dir>\.lompi`) and reports a store that may **not exist at all** — `config` prints
> the path whether or not it is there. An empty inventory can mean "wrong exe", not
> "no packages".

**Step 2 — inventory it.**

```
$ lompi index "<the store: path from above>"
6 package(s) in store
geom 1.0.0 7af928612aaaf199
host 0.1.0 d127b1b91410747a
mathutil 0.1.0 5d46f4477237f3bf
mathutil 0.2.0 492ec331d34a26a9
std 0.1.0 ccd59fc4f668bef0
util 0.3.0 0dc87d1ec3449e92
```

One line per **instance**: `name version first-16-of-identity`. A name appearing twice is
multi-version coexistence, not a duplicate. This answers "what exists" — and nothing else.

**Step 3 — read one library.**

```
$ lompi show "<store>" host
name:    host
version: 0.1.0
id:      d127b1b91410747a9cbc8abb2250cd5cadd9ed0677d2cbb78e8519f6f42e3803
dir:     <store>/host/0.1.0
7 use edge(s):
  path argv.lomt
  path dir.lomt
  path fs.lomt
  path io.lomt
  path log.lomt
  path stat.lomt
  name std
```

**`dir:` is where you read the code.** lompi does not model an API surface — there is no
"list the exported functions" command. To learn what a library offers, list `dir:` and read
the `*.lomt` files, looking for `pub fn`. `show` gives you the two things the machine cares
about: the identity, and which edges are `name` (a real dependency — another package, used
for resolution and the hash) versus `path` (this package's own module, already part of its
source).

**The trap: `index` never checks that its argument is a store.** It reads exactly two
directory levels — `<store>/<name>/<version>` — and believes what it finds. Pointed at a
project root it prints a plausible table of lies (sample: pointing it at lompi's own source
tree, where the first level is `fixture/`):

```
$ lompi index <a project root, not a store>
9 package(s) in store
fixture bad c19f11ab2d308355
fixture iso_solo c19f11ab2d308355
fixture store c19f11ab2d308355
...            one row per first-level directory of whatever you pointed it at
```

the "names" are the root's first-level directories, the "versions" its second-level ones, and
**every identity is the same** because those directories hold zero `.lomt` at that level — an
all-equal id column is the tell that nothing real was read. Pointed instead at the flat
`deps/` layout the compiler actually consumes, `index` prints **nothing** and exits **1** —
because `deps/<name>/` holds files, not version directories.

So:

| you want to know | use |
|---|---|
| what is in a store | `lompi index <store>` |
| whether a directory really is a Loment library | `lompi check <dir>` |
| what a library provides | read `dir:` and the files themselves |
| the own-source hash of one package directory | `lompi hash <package-dir>` |

`check` accepts any directory that is supposed to be a package — a store entry, a
`deps/<name>/` leaf, a work-in-progress directory — and it is the only command that tells you
the truth about a directory whose layout you are unsure of:

```
$ lompi check "<store>\std\0.1.0"
[ok] source files: 128
[ok] package name: std
[ok] every module name matches its file name
[ok] pkg.lomp name() matches
[ok] pkg.lomp version() matches
[OK] lompi: this is a valid Loment library
```

**Inventorying a project's dependencies.** The compiler sees `<project>/deps/<name>/` (flat,
no version level), so `index` is the wrong tool there. List the directory and check each
entry:

```
ls <project>/deps
lompi check <project>/deps/<name>
```

**Which version does `use <name>` bind?** The highest version present, compared numerically
per `.` segment (`0.10.0 > 0.9.0`). In the store above, `use mathutil` means `0.2.0`.
`lompi show <store> mathutil` prints the one that would be picked; `lompi index <store>` shows
the alternatives. To pin one, use a lock (§5.4).

**Do not read `pkg.lomp` to learn what a library does.** It is optional, is excluded from the
identity hash, and carries two human labels only (`name()`, `version()`).

## 7. Four common workflows

**A. Make a library visible to a project**
```
1. Place the library at <store>/<name>/<version>/ (or into your own store directory)
2. lompi index <store>                        confirm it is recognized
3. lompi plan  <store> <name> --into deps
4. run the mkdir from the plan; copy the source dir's *.lomt into deps/<name>/
```

**B. Pin one build**
```
lompi resolve <store> <name> > lompi.lock     # commit this file
lompi verify  <store> <name> lompi.lock       # run after every build; green means unchanged
```

**C. Find out why a package looks the way it does**
```
lompi show  <store> <name>     # edges + identity
lompi tree  <store> <name>     # transitive closure
lompi index <store>            # is another version of this same name present
```

**D. Check whether the same source is the same thing in two environments**
Run `lompi show <store> <name>` in both and compare `id`. **A different id means a
different instance**, even if the source looks identical — which says it bound to
different dependencies. That is exactly what the design is meant to catch.

## 8. How identity is computed (this explains the behaviour)

```
id(P) = sha256( own source ⊕ for each name edge: edge-name 0x00 child-identity 0x00 )
```

Own source = the package's `*.lomt` files (excluding `pkg.lomp` and `deps/`), fed in
**filename order** as `name 0x00 content 0x00`. Edges are **sorted by name** before
hashing, so reordering `use` lines in a file does not change the identity.

The practical consequence, worth remembering:

```
The geom source is byte-identical, but sits in two different stores:
  store A has mathutil 0.1.0 / 0.2.0  ->  geom id = 7af928612aaa...  (binds 0.2.0)
  store B has only mathutil 0.1.0     ->  geom id = 711abded0e3c...  (binds 0.1.0)
```

**The two ids differing is correct.** The same source bound to different dependencies
means a different meaning, and merging them would be the harmful outcome.

## 9. Boundaries (honest list)

Two kinds are listed here, and the difference matters when you are planning work:
**by design** (settled — do not design around it changing) and **not yet in the
toolchain** (a gap in the runtime lompi is compiled with, not a decision lompi made).

**By design**

- **No server.** A "registry" is just a repository URL; lompi never talks to a service.
- **No version range language.** Selection is "highest version" only; pin with
  `name@version` or with a lock.
- **No binary distribution.** Generics are type-directed monomorphization, so consumers
  must see source.

**Not yet in the toolchain** — treat as "not yet", and check the version before designing
around one

- **No TLS and no resolver.** The socket syscalls *are* there (both targets — `docs/234`),
  so `lompi fetch` does its own HTTP, but the toolchain has no TLS handshake and no
  `getaddrinfo`, so only `http://` with an IPv4 literal is fetchable (§5.0.2). **Both are
  toolchain gaps, not design positions** — do not design a workflow around them being
  permanent. Note this list is about the *toolchain*: what lompi itself will never do is
  the "By design" list above.
- **No `mkdir`.** `install --apply` writes files into directories that already exist; it
  checks them all up front and lists the `mkdir` commands you need instead of leaving a
  partial install. The PE runtime still does not offer it. On ELF targets it *is* available
  (see 5.6), which is the tell that this is the PE shim speaking, not lompi.

**Behaviour you should know** (settled, and these are not limitations)

- **Within a store, `use <name>` must resolve.** No version at all gives
  `lompi: missing dependency in store: <name>` and exit 1; several versions is **normal**
  (highest wins).
- **Cycles are rejected**: `A use B` with `B use A` gives `lompi: dependency cycle in
  store`. The Merkle identity cannot replace this check (both hashes are computable), so
  it is done separately.
- **A single source file is capped at 256 KiB.** Beyond that lompi refuses to compute the
  package's identity rather than truncate — a silent truncation would produce a **wrong**
  identity, which is far worse than an error. Message:
  `lompi: source file unreadable or too large (refusing to hash truncated content)`.
- **Capacity guards** (silent truncation past these; known limits): 512 instances per
  store, 2048 `use` edges per package, 4096 directory entries, 64 KiB name pool and
  256 KiB path pool.
- **Tree/closure depth 256.**

## 10. Error reference

| what you see | meaning | what to do |
|---|---|---|
| `lompi: no such package in store` | name (or version) does not match | `lompi index <store>` to see directory names and versions |
| `lompi: missing dependency in store: X` | some package's `use X` found no X | add X to the store, or fix that package |
| `lompi: dependency cycle in store` | A→B→A | break the cycle (the language should reject this at resolve time) |
| `[DIFF] lompi: lock does not match store` | the recomputed lock differs | source or dependencies changed; re-issue the lock once confirmed |
| `lompi: cannot read lock file` | lockfile path wrong or missing | check the path (exit 2) |
| `lompi: unknown command: X` | typo | `lompi help` |
| `lompi: cannot open directory` | package dir path is wrong | check the path in `show` / `hash` |
| `lompi: source file unreadable or too large ...` | a single file is > 256 KiB or unreadable | split the file |
| `lompi: out of memory` | the store/closure exceeded the workspace | smaller store, or raise the workspace size |
| non-zero exit with no output | usage error (exit 2) | supply the missing argument |

## 11. Mapping from pip

| pip | lompi | difference |
|---|---|---|
| `pip install X` | `lompi install X --apply` (or `--into DIR`) | validates first; without `--apply` it only prints the plan; needs the target directories to exist (PE shim has no `mkdir` yet — see 9) |
| `pip download` | `lompi fetch` | does the download itself over `http://` + IPv4; `https://` and hostnames fall back to printing the `git clone` (no TLS, no resolver — see 9) |
| `pip config` | `lompi config` | also shows *how* the global root was derived |
| `twine check` / wheel validation | `lompi check <dir>` | structural rules that `use <name>` will actually depend on |
| `pip list` / `pip index versions X` | `lompi list [<store>]` | lists every instance, not just one package; `index` is a synonym |
| `pip show X` | `lompi show [<store>] X` | also gives instance identity and use edges |
| `pip freeze` | `lompi resolve [<store>] X` | pins content hashes, not version numbers |
| `pip install -r requirements.txt` | `lompi install X --from-lock lompi.lock --apply` | refuses if any pinned instance is missing or its identity changed |
| `pip check` | `lompi verify [<store>] X <lock>` | recompute + byte-for-byte compare; much stronger |
| `pipdeptree` | `lompi tree [<store>] X` | two versions of one package each get their own line |
| `requirements.txt` | `lompi.lock` | no ranges to write; the hash *is* the identity |
| `~/.cache/pip` | `<global root>\cache` | holds cloned registries |

**The difference that used to be here is gone**: pip never makes you name site-packages, and
neither does lompi any more — `<store>` is optional on every verb above (see 3). What is *not*
the same as pip, deliberately: `install` still refuses to create directories on PE and prints a
plan instead. (`fetch`, which used to be on that list, now does its own download — see 5.0.2.)
