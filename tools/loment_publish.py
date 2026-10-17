#!/usr/bin/env python3
# loment_publish.py — 把单仓里的 Loment / lompi 切出来，发到各自的私有库 (docs/171)
#
# 分工（用户 2026-09-15 定，2026-09-17 修订）：
#   * **开发在主仓**（FujoOS 单仓，`D:\Dev\FujoOS-FujoLang`），单仓里的内容**原样不动**；
#   * 两个库是**发布口**，而**每次开发提交之后都要 `--push` 一次**把主仓当时的状态发过去
#     —— 用户 2026-09-17："以后提交到这，开发提交也在这"。所以**开发提交也会落在那两个库里**，
#     只是**由这里发**，不是在那边写的。
#   * **发布是整体覆盖**（force-push 成 `main`）：**别在那边直接提交**，一定被下次发布盖掉。
#     那边的 `README.md` 与 `.gitattributes` 也是本工具直出的（见 §cmd_push），不是手写的。
#   * 名字与可见性：`Loment-Lang/loment` 与 `Loment-Lang/lompi`，**两个都公开**
#     （Loment 是用户 2026-09-16 定的；lompi 是 2026-09-17 定的 —— 见各自 spec 里的注解）。
#     可见性写进 spec 而不是手敲 `gh repo edit`，因为手敲的只生效一次，换台机器就丢。
#
#   python tools/loment_publish.py --status          # 切出来会是什么样（路径数 + 树哈希）
#   python tools/loment_publish.py --check           # 门禁: 清单自洽（路径都存在、有条目）
#   python tools/loment_publish.py --push loment     # 克隆 -> 过滤 -> 建库(如无) -> 推
#   python tools/loment_publish.py --push all
# 退出码: 0 = 好 / 1 = 清单有问题 / 2 = 用法错
#
# **为什么路径不重排**：Loment 那边**到处是按路径引用的**（`loment/tools/lomcli.lomt`、
# `lom/*.lom`、`docs/169`…），路径一改，文档与注释全失效。所以 Loment 保留原结构。
# lompi 那边是自足的小项目，把 `lompi/` **摊平到根**更像一个正经仓库（`lompi.lomt` 就在根上）。
#
# **历史**：用 `git filter-repo` 保留（在**克隆副本**里跑 —— 它在原仓是就地重写，绝不能对
# 工作仓下手）。切出来的每个库都带自己那段历史。

from __future__ import annotations

import argparse
import fnmatch
import re
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORG = "Loment-Lang"

#: L0 契约的**对照物** —— 契约对面那一侧的源码，判据拿它们做逐字段对账。
#:
#: 它们不是 Loment 的实现，但**没有它们，对账判据就没有对照面**：`lom_audit`、
#: `fuai_contract_check`、`lomc_test`、`loment_p7_test` 都直接 `ROOT / "kernel" / …`
#: 这样读它们，读不到就是崩或红。
#:
#: 2026-09-17 搬开发口时，`sdk/`、`kernel/` 被整目录当 FujoOS 排除，于是这几条判据
#: **静默失去对照面**。搬完只跑了三条判据就下了"验证好"的结论，是错的 —— 全量跑一遍
#: 才撞出来。所以这一类要写成清单 + 判据（见 `cmd_check` 里的 COUNTERPARTS 那两条），
#: 而不是靠人记得。
#:
#: **本仓这些是 vendored 副本**（与 `kernel/` 的既有约定一致：活的在 FujoOS，
#: 本仓别当活的改）。它们只被读、不被构建。
COUNTERPARTS = [
    "kernel/src/syscall.rs",       # lom/fuai.lom 的 opcode + fujo_fn 对账面
    "kernel/src/capability.rs",    # lom/fuc.lom 的 fujo_files
    "kernel/src/fujr.rs",          # lom/fujr.lom 的 FUJR 魔数与版式
    "kernel/src/fui/fuc.rs",       # fuc 版式常量
    "kernel/src/fui/fuc_gen.rs",   # 由 lomc 生成的 fuc 常量（生成-消费闭合）
    "sdk/fuai-spec/spec.json",     # 权威 = lom/fuai.lom 的生成物，双向逐字段
    "tools/fuic.py",               # fuc 格式串的生成器（被对账的另一侧）
    "tools/fujopack.py",           # 打包器，被 lomc_test 核对"用生成物而非手写"
]


def _corpus_paths() -> list[str]:
    """`loment/corpus.json` 里点名的语料文件。

    **推导而不是手抄**：语料是 Potato LLM 那一臂的输入（`potato_llm_arm.py`），而
    `docs/175` §4 把 AI 训练列成两条主线之一 —— 它会**经常变**。手抄一份清单必然漂，
    而漂法是静默的：多一条语料而清单没跟上，`potato_test` 就在开发口里红，人却以为
    是"那台机器的问题"。所以直接从数据源读。

    这些是**语料**不是契约对照物（见 `COUNTERPARTS`），但同一个道理：判据要读它们，
    它们在 FujoOS 侧，本仓需要**只读副本**。
    """
    doc = json.loads((ROOT / "loment" / "corpus.json").read_text(encoding="utf-8"))
    out: list[str] = []

    def walk(o: object) -> None:
        if isinstance(o, dict):
            p = o.get("path")
            if isinstance(p, str):
                out.append(p)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


#: 每个库：仓库名、切哪些路径（支持 glob）、重排规则、根 README。
REPOS: dict[str, dict] = {
    "loment": {
        "repo": f"{ORG}/loment",
        "paths": [
            # 目录（整棵）。**`lompi/` 在这儿**：Loment 开发树离不开它
            # （`loment_dist.py:67` 拿它构建随包发行的 lompi 工具，`loment_lompi_test.py`
            # 读它的源）—— 所以 lompi 的可见性必须与这个库一致。用户 2026-09-17 定：
            # **lompi 也开源**，于是两边都是 public（原先"只开源 Loment 本体"的划分作废）。
            "loment/", "lom/", "lompi/", "editors/",
            "docs/manual/",
            ".claude/skills/loment/", ".claude/skills/lompi/",
            # 工具：`tools/lom*` 一次收全（lomelf / lomentc / lomc / lomdoc / lom_audit …）
            "tools/lom*",
            # 文档：**一条 `docs/*.md` 收全**。
            #
            # 原文是按名字分段收（`docs/14*-loment-*.md` + 一串 explicit 补漏），那是在
            # **旧树**（`docs/` 里 FujoOS 与 Loment 两线混放）里不得不那么写。它两头不讨好：
            # `14*` 会把内核文档也捞进来，而 `-loment-` 的收紧又漏掉名字里没有 loment 的
            # （110/142/147/176/177，其中 142/147 还被 `docs/175` 正文引用着 —— 漏了就是断链）。
            # 补 explicit 能修一次，但每加一篇非 `*-loment-*` 的文档就得再想一遍。
            #
            # 本仓就是 Loment 的开发口，`docs/` 里全是这条线的文档，所以按目录收才是**说对了意图**。
            # 2026-09-17 发布清单（`loment_release.GLOBS` 与自举的 `lomrel.lomt`）先这么改了；
            # 这里跟上，三处口径一致。
            "docs/*.md",
            # 仓库约定与许可：**开发口缺了它们就没法按本仓的规矩工作**
            # （`CLAUDE.md` 是工作区级指令；`.gitignore` 不管住的话 `selfhost_driver.ll`
            #   那种大件与构建产物会跟着进来）。
            # **`.gitattributes` 必须在这里** —— 工具会往它**追加**一段（不再覆盖，见
            # `cmd_push`），前提是它已经跟着筛出来；不在清单里的话追加的是空文件，
            # `*.lomt text eol=lf` 那条规矩照样丢。2026-09-17 第一次修漏写这一条，
            # 远端那份仍只有工具自己那段，是查"克隆出来还是 CRLF"才发现的。
            "CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", "LICENSE",
            # 新读者走的第二份文档 (**英文**), 与 README 分工: README 说"这是什么",
            # 它只管"怎么跑起来"。README 顶部指向它。不在 `loment_release.GLOBS` 里,
            # 所以不进发布清单 —— 它只服务于仓库读者, 不进包。
            "QUICKSTART.md",
            # **`scripts/` 里只有这两个是 Loment 线的** —— 别整目录收（其余是 FujoOS 的
            # 内核/ISO 构建脚本）。这两个被工具链当成**必需的启动脚本**引用：
            # `loment_seed.py:72` 的 LAUNCH_SCRIPTS 点名 `scripts/lomc.ps1`（缺了它
            # "装工具不需要 Python"这条会静默退化），`loment_release.py:91` 与
            # `loment_src.py:49` 两个都点名。2026-09-17 把整个 `scripts/` 当 FujoOS 排除
            # 是**错的**，是 `loment_seed_test` 1/4 红查出来的。
            "scripts/lomc.ps1", "scripts/install-lsp.ps1",
            # **工具集按"门禁跑得起来"算, 不按名字 glob。** `tools/lom*` 那个 glob 实测
            # 只捞到 **57/127** —— 静默漏掉 `ci.py`（门禁本身!）与整个 Potato 工具集,
            # 而 `lomentc.py` 会 `import potato`, 于是开发口里**连编译器都跑不起来**。
            # 2026-09-17 搬开发口时撞出来的; `--check` 里有一条闭包判据钉住它。
            "tools/ci.py", "tools/_safepath.py", "tools/mono_trace.py",
            "tools/potato.py", "tools/potato_assert.py", "tools/potato_cross.py",
            "tools/potato_from.py", "tools/potato_llm_arm.py", "tools/potato_measure.py",
            "tools/potato_test.py", "tools/vscode_ext.py", "tools/vscode_ext_test.py",
            "tools/fuai_contract_check.py",
            # 花括号族的共享前端核（`docs/188` §7.1）。同样不以 `lom` 开头。
            "tools/trans_core.py",
            # C -> Loment 的翻译器（`docs/186` Stage A）。名字**不以 `lom` 开头**，
            # `tools/lom*` 那个 glob 收不到它 —— 而 `loment_ctrans_test` 会 import 它，
            # 于是闭包判据当场点了名（那正是它该干的事：别靠手数）。
            "tools/ctrans.py",
            # Python -> Loment 的翻译器（`docs/187`）。同上，名字不以 `lom` 开头。
            "tools/pytrans.py",
            # Java -> Loment 的方言表（`docs/188` §7.1）。同上。
            "tools/jtrans.py",
            # C# -> Loment 的方言表（`docs/188` §7.1）。同上 —— `loment_cstrans_test`
            # 会 import 它。（共享核 `tools/trans_core.py` 在它自己那一条上，同批。）
            "tools/cstrans.py",
            # C++ -> Loment 的方言表（`docs/188` §7.1）。同上。
            "tools/cpptrans.py",
            # Go -> Loment（`docs/188` §7.1 的第六门）。**它不共用那份共享核** ——
            # 类型写在名字后面、条件不带括号、没有 `while`，所以自足一份。
            "tools/gotrans.py",
            # Loment 的**自然语言写法**（`docs/197`）。同上 —— 名字不以 `lom` 开头。
            # **这一门与上面六门不同类**：那六门是别人已有的语言，这一门是我们自己
            # 发明的表层语法（`docs/197`）。
            "tools/nltrans.py",
            # 自举快速对照探针（定位工具，非检查项 —— 与 `loment_ir_diff.py` 同一分工）。
            # 改语言面时要先用它把差异钉到一行，再决定惊不惊动 p8；开发口该带它。
            "tools/loment_probe.py",
            # LVM 的 T0（`docs/243`）。**名字不以 `lom` 开头**（是 `lvm`），所以上面那个
            # `tools/lom*` 收不到 —— 正是闭包判据点名的那一类（2026-10-13 CI 上真红了一次：
            # 参考解释器与两条判据把 `lvm`/`lvm_test` 拉进了门禁闭包，而它们不在清单里）。
            "tools/lvm.py", "tools/lvm_test.py", "tools/lvm_twin_test.py",
            "tools/lvm_profile_test.py",
            # **L0 契约的对照物**（见文件顶部 `COUNTERPARTS` 的说明）。整目录收不得：
            # `kernel/` 其余是 FujoOS 内核（几 MB 的 Rust），`sdk/` 其余是 ISO/字库/驱动。
            # 这 8 条是判据真正读的那些，一条一条点名。
            *COUNTERPARTS,
            # 语料（Potato LLM 那一臂的输入）—— 推导，见 `_corpus_paths`。
            *_corpus_paths(),
        ],
        "renames": [],
        "floor": 250,
        #: 仓库的对外元数据。**放在这里而不是手敲 `gh repo edit`** —— 手敲的只生效一次,
        #: 下次建库/换机器就丢了; 写进 spec 则每次 `--push` 都会对齐 (值一样时是无操作)。
        #: 用户 2026-09-16 定: **Loment 公开**, 且**不打 tag** (tag 要等真发布)。
        "visibility": "public",
        "description": "Loment — a systems programming language with a Rust-flavored syntax "
                       "and capability domains; artifacts are sealed (no runtime, no libc) by "
                       "default, hosted on request",
        "homepage": "https://loment-lang.github.io/loment-site/",
        "topics": ["programming-language", "compiler", "rust", "self-hosted",
                   "systems-programming", "capability-security", "no-std"],
        #: 写进仓库根的 `.gitattributes`（与 README 同一种做法：发布工具直出，不靠手敲）。
        #: 两件事：**把 Loment 源码认到某个语言名下**，以及**把生成物排除出统计**。
        #: 详见文件里的注释 —— 尤其是"为什么现在写 Rust 而不是 Loment"。
        "gitattributes": """# GitHub classifies a repository with Linguist. This repository has two kinds of bytes
# that Linguist would otherwise get wrong:
#
#   * `.lomt` / `.lom` are Loment source. Linguist does not list those extensions yet, and
#     it refuses to guess: `Language.find_by_alias` returns nil for a name it does not know
#     and caches that, so `linguist-language=Loment` would not "declare it in advance" — it
#     would drop these files out of the statistics entirely. They are attributed to Rust
#     instead: Loment's syntax overlaps Rust's, so keywords, strings, comments
#     and the resulting highlighting line up.
#     Loment is not yet eligible for inclusion — Linguist asks for roughly 2000 files per
#     extension per year, spread across repositories, and does not accept very new
#     languages. **Once it is included, change these two lines to `Loment`** — or drop them
#     and let the extensions be detected on their own.
#   * `loment/build/` holds build output — the compiler seed as LLVM IR, per-target Potato
#     form objects, transpiled Rust and C, the genesis assembler. Excluding it keeps a
#     1.8 MB generated file from deciding that this repository is "LLVM".
*.lomt linguist-language=Rust
*.lom  linguist-language=Rust
loment/build/** linguist-generated
""",
        "readme": """![Loment — Programming Language, Program by Fujo](editors/loment-banner.png)

# Loment

Loment is a **systems programming language**: it compiles to native x86-64 executables —
Linux ELF and Windows PE, or a freestanding object for bare metal. What goes into one is the
program's decision, and the build records it: `choose sealed`, the default, is an artifact
with no runtime, no libc and no undefined symbols, and `choose hosted` is one that may link
against real libc and shared libraries. The toolchain is itself written in Loment. Its own
syntax is Rust-flavored, and the same program can be written in six more: C, C++, Java, C#, Go
or Python.

**[Quick start](QUICKSTART.md)** ·
[Project site](https://loment-lang.github.io/loment-site/) ·
[Manual](docs/manual/index.md) ·
[Language guide](.claude/skills/loment/SKILL.md) ·
[Examples](loment/examples/) ·
[Issues](https://github.com/Loment-Lang/loment/issues)

## Status

`0.1.4.5 QuickFix` **ships as a package** — a Linux `tar.gz`, a Windows `zip` and a Windows installer.
The toolchain is **self-hosted**: it
compiles and runs with no Python and no libc at run time. **Not yet finished**: the
*development-side* self-hosting is incomplete (`docs/189`) — the reference implementation
(`tools/lomentc.py` and friends) and the Python test suites are still in the repository, and
the six surface-syntax translators have no Loment twins yet. So this release ships the
language and the toolchain, **not** the "repository contains only Loment" milestone. The
language surface is frozen — `docs/158-loment-freeze.md` says what changing it costs — and
the implementation is not.

## A first program

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

```
$ loment run hello.lomt
hello from Loment
```

`_start` is the entry point, because a sealed artifact has no runtime to call one for you, and
output goes through the `write` system call, because there is no `printf`. A `hosted` artifact
is a C-runtime program instead, with `fn main() -> u32` as its entry point.
[QUICKSTART.md](QUICKSTART.md) takes it from here — sources, toolchain, and the first four rows
below.

## What it does that others do not

| | |
|---|---|
| **Calls libraries written in other languages** | Ten of them, two mechanisms: C, C++, Rust and Zig behind the C ABI, then Go, Python, Java, JavaScript, Perl and Lua over a process bridge. `--link` takes an object that is **self-contained** (relocations are applied, objects may reference each other); one that carries **undefined symbols** (`printf`/`malloc`) is **refused by default**. `choose hosted` — the dimension that reaches real libraries, exercised by criteria that link libstdc++ and libz and embed CPython — is **not available in a release build**: that path is unfinished, so the release toolchain refuses `choose hosted` and keeps the door shut ([docs/222](docs/222-lompi-020-foreign-libs.md) sec 4.5, [docs/219](docs/219-world-ports.md) sec 8c). What is **not** there either is the **library system** — lompi still installs `.lomt` sources only, so a real C library is not yet an installable dependency. [docs/173](docs/173-loment-ffi.md) is the ledger. |
| **Can be written in six other syntaxes** | C, C++, Java, C#, Go, Python. Only the spelling changes; the semantics are always Loment's. Declaring it in the file (`choose write grammar`) is not wired into the compiler yet — the translators in `tools/` are the path. [docs/188](docs/188-grammar-declaration.md) |
| **Everything is customizable** | The source suffix, the command surface (`loment-<name>` on `PATH`; official commands always win), the libraries (a directory whose identity is a content hash), and the toolchain itself. No registry — the extension points are files on disk. |
| **Reports errors out of a separate program** | The compiler emits structured diagnostics; `lomenterr` adds the title, the location and how to fix it, so the compiler carries no message table of its own. [docs/182](docs/182-lomenterr-and-choose-switches.md) |
| **Capability domains** | The one thing the language adds to Rust: `capability blk : disk[0..4]` and `guard blk(i);`. A guard bounds the *index*, not the *subject* — [docs/146](docs/146-loment-capability-semantics.md) states that boundary rather than glossing it. |

## Documentation

| | |
|---|---|
| `.claude/skills/loment/SKILL.md` | The language guide: syntax, built-ins, error codes, and the deviations from Rust. Self-contained, shipped in the package (`loment skill --print`), and the fastest way in. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary (`loment example tour`). |
| `docs/manual/` | The generated manual: specifications, plus a page per example. |
| `docs/143-l1-loment-v0.md` · `docs/146` · `docs/158` | The specification, capability semantics, and what is frozen. |
| `docs/173` · `docs/188` | The two capabilities in the table above. |
| `docs/` | Design and measurement records, numbered by document. Most are written in Chinese. |
| [Loment-Lang/lompi](https://github.com/Loment-Lang/lompi) | The package manager. |

## Repository layout

| | |
|---|---|
| `loment/selfhost/` | The compiler. It is written in Loment. |
| `loment/tools/` | CLI, formatter, documentation generator, language server, linker, error reporter. |
| `loment/lib/` | Core library modules: `mem`, `num`, `json`, `sha256`, `proc`. |
| `loment/examples/` | Example programs. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `lompi/` | The package manager, written in Loment. |
| `editors/` | Editor support: VS Code and Vim. |
| `tools/` · `docs/` | Build and verification tools; design records. |

## Getting help and contributing

Ask in an [issue](https://github.com/Loment-Lang/loment/issues) — questions are as welcome as
bug reports. Loment is developed in this repository, and the working conventions are in
`AGENTS.md`.

## License

The **Loment-Open License** — the MIT License plus two conditions: source derived from Loment
must credit that base, and the name "Loment" is reserved. File extensions and syntax are free:
you may build a different language from this source without `.lomt` or `.lom`. See
[LICENSE](LICENSE).
""",
    },
    "lompi": {
        "repo": f"{ORG}/lompi",
        "paths": ["lompi/", ".claude/skills/lompi/"],
        # 摊平: 让 `lompi.lomt` / `lpi_*.lomt` / `fixture/` 直接在根上
        "renames": [("lompi/", "")],
        "floor": 30,
        # **公开** (用户 2026-09-17 定)。原先 2026-09-16 是私有的, 理由是"用户只说了开源
        # Loment 本体"; 但 Loment 开发树离不开 `lompi/` (`loment_dist.py:67` 拿它构建随包
        # 发行的 lompi 工具), 而 `Loment-Lang/loment` 是开发口且公开 —— 两边可见性必须一致,
        # 否则要么公开库缺件、要么开发口得转私有。用户选了"lompi 也开源"。
        "visibility": "public",
        # 简介与正文一律英文（2026-10-12 用户定）：这两个库都是 public，读者不一定是
        # 中文使用者；仓里其它对外文字（README / 发布说明 / issue）也已经是英文。
        "description": "lompi — the package manager for Loment libraries "
                       "(a library is a directory, its identity is a content hash)",
        "homepage": "https://loment-lang.github.io/loment-site/",
        "topics": ["package-manager", "loment"],
        "readme": """# lompi

**The package manager for Loment libraries.** A library is a *directory*, its identity is a
**content hash**, and its dependencies are the `use` lines in the source. lompi is written
**in Loment** and built by the Loment toolchain.

**It is a standalone command, not a `loment` subcommand**: `loment help` does not list it; you
run `lompi` directly.

## Building it

You need the Loment toolchain first (<https://github.com/Loment-Lang/loment>), then **from this
directory**:

```
loment build lompi.lomt -o lompi
```

(The directory matters: this toolchain resolves relative imports such as `use "lpi_cli.lomt"`
against the **current working directory**.)

```
lompi version
lompi list  [<store>]                     # what is in the store (index is a synonym)
lompi show  [<store>] <name[@version]>
lompi tree  [<store>] <name[@version]>
lompi resolve [<store>] <name[@version]>  # write a lock, pinned by content hash
lompi check <dir>                         # validate a library directory
lompi fetch --registry <url>              # download a registry over http
```

`<store>` is optional — omit it and the **global store** is used (the `store:` line that
`lompi config` prints), the same way `pip` never asks you where site-packages lives.

`fetch` does the download itself when the registry is `http://` with an IPv4 literal: it reads
the index, GETs each file, and **verifies the sha256 the index states**. It will not do
`https://` (there is no TLS in the toolchain) or a hostname (no resolver) — for those it prints
the `git clone` to run and names the piece that is missing.

The self-test driver is `lpi_test.lomt`: one `selftest_<module>()` per module, summarised with
the **exit code as the verdict**.

## What this repository is

**It is the publish outlet of the Loment repository, not a development tree.** Development
happens in <https://github.com/Loment-Lang/loment>, under `lompi/`; this repository is regenerated
from it by that repository's `tools/loment_publish.py`. **Do not commit here** — the next
publish overwrites it.

The guide is `.claude/skills/lompi/SKILL.md`.
""",
    },
}


def _git(*a: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *a], cwd=str(cwd or ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", shell=False)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(a)} 失败: {r.stderr[-300:]}")
    return r.stdout


def _all_rows() -> list[str]:
    """整棵 HEAD 的 `ls-tree -r` 输出（`<mode> <type> <sha>\\t<path>`）。

    **不能用 `git ls-tree <路径>` 逐个取** —— `ls-tree` **不展开 glob**（`ls-files` 才展开），
    于是 `tools/lom*` 这种模式一个文件都匹配不到（2026-09-15 踩过：文件数凭空少了 81）。
    取全表，再在 Python 里按模式筛。
    """
    return [x for x in _git("ls-tree", "-r", "HEAD").splitlines() if x.strip()]


def _hit(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif fnmatch.fnmatchcase(path, p):
            return True
    return False


def _selected(name: str) -> list[str]:
    spec = REPOS[name]
    return sorted(r for r in _all_rows() if _hit(r.split("\t", 1)[1], spec["paths"]))


def _rel_ok(p: str) -> int:
    """这个模式匹配到几个**已跟踪**文件。"""
    return len([r for r in _all_rows() if _hit(r.split("\t", 1)[1], [p])])


def _tree_hash(name: str) -> tuple[int, str]:
    """(文件数, 内容树哈希)。哈希按 `ls-tree -r` 的确定性输出算 —— "要发的东西变了没"。"""
    rows = _selected(name)
    h = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:12]
    return len(rows), h


def cmd_status() -> int:
    for name, spec in REPOS.items():
        n, h = _tree_hash(name)
        print(f"{name:8s} -> {spec['repo']}")
        print(f"   文件 {n} 个 ({'/'.join(spec['paths'][:3])} …)   树哈希 {h}")
        for p in spec["paths"]:
            c = _rel_ok(p)
            if c == 0:
                print(f"   [空] {p}")
    return 0


def _gate_missing(spec: dict) -> list[str]:
    """`ci.py --static-only` 跑得起来所需的 `tools/*.py`，减去路径清单已经覆盖的。

    **为什么必须有这条判据**：清单原先是靠名字 glob（`tools/lom*`）定的，实测只捞到
    **57/127** —— 静默漏掉 `ci.py`（门禁本身）与整个 Potato 工具集，而 `lomentc.py`
    会 `import potato`，于是开发口里**连编译器都跑不起来**（`python tools/lomentc.py`
    在 `--emit-potato` 那条路上直接 ImportError）。

    **靠手数清单是数不对的** —— 那个漏是在搬到开发口、真去跑门禁时才撞出来的。
    所以把"门禁闭包"写成判据：门禁要跑的东西必须都在。
    """
    tools = ROOT / "tools"
    allpy = {p.stem for p in tools.glob("*.py")}

    def covered(stem: str) -> bool:
        f = f"tools/{stem}.py"
        return any(fnmatch.fnmatch(f, g) for g in spec["paths"])

    have = {s for s in allpy if covered(s)}
    need = set(have)
    ci = tools / "ci.py"
    if ci.exists():
        head = ci.read_text(encoding="utf-8").split("STATIC_CHECKS")[1][:3000]
        for m in re.findall(r'"([a-z0-9_]+)"', head):
            if m in allpy:
                need.add(m)
    changed = True
    while changed:                      # import 闭包
        changed = False
        for n in sorted(need):
            src = (tools / f"{n}.py").read_text(encoding="utf-8", errors="replace")
            for m in re.findall(r'^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)', src, re.M):
                if m in allpy and m not in need:
                    need.add(m)
                    changed = True
    return sorted(need - have)


def cmd_check() -> int:
    bad = []
    for name, spec in REPOS.items():
        n, _ = _tree_hash(name)
        if n < spec["floor"]:
            bad.append(f"{name}: 只切出 {n} 个文件（下限 {spec['floor']}）—— 路径清单可能写漏了")
        for p in spec["paths"]:
            if _rel_ok(p) == 0:
                bad.append(f"{name}: {p} 一个文件都没匹配到")
        if name == "loment":
            miss = _gate_missing(spec)
            if miss:
                bad.append(f"{name}: 门禁闭包还缺 {len(miss)} 个 —— "
                           f"{' '.join('tools/' + m + '.py' for m in miss[:5])}"
                           f"{' …' if len(miss) > 5 else ''}（加进 paths，别靠手数）")
            # L0 契约的对照物：**在树里**（不然本仓自己都红）且**在清单里**
            # （不然开发口那侧的对账判据没有对照面）。两条分开报，因为修法不同。
            for c in COUNTERPARTS + _corpus_paths():
                if not (ROOT / c).exists():
                    bad.append(f"{name}: 对照物/语料 {c} 在树里就没有 —— 判据没有对照面")
                elif not _hit(c, spec["paths"]):
                    bad.append(f"{name}: 对照物/语料 {c} 没进路径清单 —— "
                               f"开发口那侧会崩/会红")
    for b in bad:
        print(f"[ERR] {b}")
    if bad:
        return 1
    print("loment_publish: 两个库的路径清单自洽")
    return 0


def _edit_meta(spec: dict) -> int:
    """把仓库的对外元数据对齐到 spec：**可见性 / 简介 / 官网 / 主题**。

    为什么要它: 这些值原先靠手敲 `gh repo edit`, 只生效一次 —— 换机器、重建库、
    或者谁手动改回私有, 就悄悄漂了, 而 `--check` 看不见(它只看路径清单)。
    写进 spec 之后, **每次 `--push` 都对齐一次**, 值本来就对时是空操作。

    **可见性是唯一有"外向"后果的那一项**: 从私有变公开会在 GitHub 上留痕且基本不可逆,
    所以变了要**打出来**, 不安静地做。
    """
    repo = spec["repo"]
    r = subprocess.run(["gh", "repo", "view", repo, "--json",
                        "visibility,description,homepageUrl,repositoryTopics"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False)
    if r.returncode != 0:
        print(f"[{repo}] 读不到元数据: {r.stderr[-200:]}")
        return 1
    cur = json.loads(r.stdout)
    args, notes = [], []
    if cur.get("visibility", "").lower() != spec["visibility"]:
        # `gh` 在这里有一道刻意的闸: 改可见性必须**同时**给 `--accept-visibility-change-consequences`,
        # 不然直接拒绝 (实测: "use of --visibility flag requires ...")。别把它当成要绕的障碍 ——
        # 它就是"这一步有外向后果"的那句话, 我们的 spec 已经把它写明了, 照给。
        args += ["--visibility", spec["visibility"], "--accept-visibility-change-consequences"]
        notes.append(f"可见性 {cur.get('visibility','?')} -> {spec['visibility']}")
    if (cur.get("description") or "") != spec["description"]:
        args += ["--description", spec["description"]]
        notes.append("简介")
    if (cur.get("homepageUrl") or "") != spec["homepage"]:
        args += ["--homepage", spec["homepage"]]
        notes.append("官网地址")
    have = {t["name"] for t in (cur.get("repositoryTopics") or [])}
    add = [t for t in spec["topics"] if t not in have]
    if add:
        args += ["--add-topic", ",".join(add)]
        notes.append("主题 +" + ",".join(add))
    if not args:
        print(f"[{repo}] 元数据已对齐")
        return 0
    e = subprocess.run(["gh", "repo", "edit", repo, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False)
    if e.returncode != 0:
        print(f"[{repo}] 改元数据失败: {((e.stdout or '') + (e.stderr or ''))[-300:]}")
        return 1
    print(f"[{repo}] 元数据: " + "; ".join(notes))
    return 0


def cmd_push(which: str) -> int:
    names = list(REPOS) if which == "all" else [which]
    for name in names:
        spec = REPOS[name]
        td = Path(tempfile.mkdtemp(prefix=f"pub-{name}-"))
        try:
            print(f"[{name}] 克隆到临时目录（filter-repo 会就地重写，绝不在工作仓里跑）")
            _git("clone", "--no-local", "--quiet", str(ROOT), str(td))
            args = ["filter-repo", "--force"]
            for p in spec["paths"]:
                # `--path` **只吃字面路径**；带通配的必须走 `--path-glob`（2026-09-15 踩过：
                # 用 --path 传 `tools/lom*` 一个文件都不匹配，loment 那侧凭空少了 81 个文件）。
                args += ["--path-glob", p] if ("*" in p or "?" in p) else ["--path", p]
            for a, b in spec["renames"]:
                args += ["--path-rename", f"{a}:{b}"]
            g = subprocess.run(["git", *args], cwd=str(td), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", shell=False)
            if g.returncode != 0:
                print(g.stderr[-500:])
                return 1
            (td / "README.md").write_text(spec["readme"], encoding="utf-8", newline="\n")
            _git("add", "README.md", cwd=td)
            if spec.get("gitattributes"):
                # **追加，不覆盖**（2026-09-17 修）。这个文件**不是发布工具的私产**：它带着
                # `*.lomt text eol=lf` 那条规矩，而自举的逐字节判据依赖"检出换行不漂"
                # （仓库里那段注释写着为什么：同一 commit 在两台机器上文件字节不同，
                # 就会冒出"只在某个 worktree 才红"的假回归）。
                #
                # 原实现无条件覆盖，于是 `loment` 克隆出来时 `codegen.lomt` 是 **CRLF**,
                # `loment_p8_test` 的 AST 逐字节判据当场红（Loment 侧看到 `\n\n`、Python 侧
                # 看到 `\n`）。以前 `loment` 只是"没人从它构建"的发布镜像所以没暴露；
                # 2026-09-17 它成了**开发口**，就直接踩上了。
                # **前插, 不是追加。** `.gitattributes` 是**后者覆盖前者**, 而工具这段是
                # **通用**规则 (`loment/build/** linguist-generated`), 仓库那份是**具体**
                # 规则 (`loment/build/selfhost_driver.ll … linguist-generated=true -diff`)。
                # 追加的话通用规则会盖掉具体的 —— 今天两者一致所以看不出, 明天不一致就是
                # 静默改变语义。惯例也是"通用在前、具体在后"。
                ga = td / ".gitattributes"
                cur = ga.read_text(encoding="utf-8") if ga.exists() else ""
                if spec["gitattributes"].strip() not in cur:
                    ga.write_text((spec["gitattributes"].rstrip("\n") + "\n\n" + cur).lstrip("\n"),
                                  encoding="utf-8", newline="\n")
                _git("add", ".gitattributes", cwd=td)
            _git("-c", "user.name=loment_publish", "-c", "user.email=noreply@fujo.invalid",
                 "commit", "--quiet", "-m", "README — 说清这是什么、怎么开始",
                 cwd=td)
            n = len(_git("ls-files", cwd=td).splitlines())
            print(f"[{name}] 切出 {n} 个文件；建库(如无)并推送")
            # 先看看库在不在（在就只推，不在才建）—— `gh repo create` 对已存在的库会报错。
            have = subprocess.run(["gh", "repo", "view", spec["repo"], "--json", "name"],
                                  capture_output=True, text=True, shell=False)
            if have.returncode != 0:
                r = subprocess.run(["gh", "repo", "create", spec["repo"],
                                    f"--{spec['visibility']}",
                                    f"--description={spec['description']}"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", shell=False)
                if r.returncode != 0:
                    print(((r.stdout or "") + (r.stderr or ""))[-500:])
                    return 1
                print(f"[{name}] 建库 {spec['repo']} ({spec['visibility']})")
            # **推成 `main`**：`gh repo create --source --push` 会把**当前分支名**
            # （这里是 `Fujoos-FujoLang-DEV`）当成默认分支 —— 一个新库顶着单仓的开发分支名
            # 很怪（2026-09-15 第一次跑就撞上了）。空库的**第一个 ref** 会成为默认分支。
            _git("remote", "add", "origin", f"https://github.com/{spec['repo']}.git", cwd=td)
            _git("push", "--quiet", "--force", "-u", "origin", "HEAD:main", cwd=td)
            print(f"[{name}] 推到 main")
            if _edit_meta(spec) != 0:
                return 1
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_publish")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--push", metavar="NAME", choices=[*REPOS, "all"])
    a = ap.parse_args(argv)
    if a.push:
        return cmd_push(a.push)
    if a.status:
        return cmd_status()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
