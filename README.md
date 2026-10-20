<p align="center">
  <img src="editors/loment-banner.png" width="720" alt="Loment — Programming Language, Program by Fujo">
</p>

<p align="center">
  <a href="https://github.com/Loment-Lang/loment/actions/workflows/gate.yml"><img src="https://github.com/Loment-Lang/loment/actions/workflows/gate.yml/badge.svg" alt="gate"></a>
  <a href="https://github.com/Loment-Lang/loment/releases/latest"><img src="https://img.shields.io/github/v/release/Loment-Lang/loment?label=release&amp;color=4c6ef5" alt="latest release"></a>
  <a href="LICENSE-MIT"><img src="https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-4c6ef5" alt="license: MIT OR Apache-2.0"></a>
  <a href="https://discord.gg/rGw7NRNU"><img src="https://img.shields.io/badge/Discord-join%20chat-5865F2?logo=discord&amp;logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://loment-lang.github.io/loment-site/">Website</a> ·
  <a href="https://github.com/Loment-Lang/loment/releases/tag/v0.1.4.6">Download 0.1.4.6</a> ·
  <a href="QUICKSTART.md">Quick start</a> ·
  <a href=".claude/skills/loment/SKILL.md">Language guide</a> ·
  <a href="standard/LLS.md">Standard</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

This is the source for Loment: the compiler, the standard library and the toolchain. **Development,
issues and pull requests are on GitHub**; GitLab, Gitee, Codeberg and Bitbucket carry
**read-only mirrors** — anything opened there goes unseen.

It ships as a package — Linux `tar.gz`, Windows `zip`, Windows installer — and running it needs no
Python, no clang and no WSL.

## Why Loment?

- **Small.** A program becomes a native x86-64 executable with no runtime and no libc — Linux
  ELF, Windows PE, or a bare object for metal. The toolchain is itself written in Loment.
- **Accountable.** A capability domain says how far a unit may reach, and a form object records
  what it holds and reaches, so what a program can touch is answerable without reading the source.
- **Familiar.** The syntax is Rust-flavored, and the same program can be written in six more:
  C, C++, Java, C#, Go or Python.
- **Gentle where Rust is strict.** LSPE, the safety audit layer, is gentler than Rust's borrow
  checker while offering nearly the same safety: it audits the compiler's own decisions and
  records every allowance, so no permit is silent.

Diagnostics come from a separate program: `lomenterr version` prints `lomenterr 0.1.2`, its own
number.

## A first program

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

```console
$ loment run hello.lomt
hello from Loment
```

## Where to learn more

| | |
|---|---|
| [Quick start](QUICKSTART.md) | Sources, toolchain, the first four commands. |
| [Language guide](.claude/skills/loment/SKILL.md) | Syntax, built-ins, error codes. Ships inside the package as `loment skill --print`. |
| [Standard](standard/LLS.md) | LLS, the *Loment Language Standard* — what is frozen, who decides conformance. |
| [Manual](docs/manual/index.md) · [Examples](loment/examples/) | The generated reference, and 34 example programs. |
| [Wiki](https://loment.fandom.com/) | The language, the toolchain and the ecosystem, browsable. |
| [docs/](docs/) | Design and measurement records. Most are written in Chinese. |

## Getting help

Ask in an [issue](https://github.com/Loment-Lang/loment/issues), on
[Discord](https://discord.gg/rGw7NRNU) or on [Mastodon](https://mastodon.social/@Loment).
Questions are as welcome as bug reports.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Loment is developed with AI coding agents in the loop,
and says so: a person directs the work, a program decides what may land, and the conformance
suite decides what conforms. The working conventions are in `AGENTS.md`.

## License

**MIT OR Apache-2.0**, at your option. See [LICENSE-MIT](LICENSE-MIT) and [LICENSE-APACHE](LICENSE-APACHE).
