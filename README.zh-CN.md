<p align="center">
  <img src="editors/loment-banner.png" width="720" alt="Loment — Programming Language, Program by Fujo">
</p>

<p align="center">
  <a href="https://github.com/Loment-Lang/loment/actions/workflows/gate.yml"><img src="https://github.com/Loment-Lang/loment/actions/workflows/gate.yml/badge.svg" alt="gate"></a>
  <a href="https://github.com/Loment-Lang/loment/releases/latest"><img src="https://img.shields.io/github/v/release/Loment-Lang/loment?label=release&amp;color=4c6ef5" alt="latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Loment--Open-4c6ef5" alt="license: Loment-Open"></a>
  <a href="https://discord.gg/rGw7NRNU"><img src="https://img.shields.io/badge/Discord-join%20chat-5865F2?logo=discord&amp;logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://loment-lang.github.io/loment-site/">官网</a> ·
  <a href="https://github.com/Loment-Lang/loment/releases/tag/v0.1.4.5-quickfix">下载 0.1.4.5</a> ·
  <a href="QUICKSTART.md">快速上手</a> ·
  <a href=".claude/skills/loment/SKILL.md">语言指南</a> ·
  <a href="standard/LLS.md">标准</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a> ·
  <a href="README.md">English</a>
</p>

这是 Loment 的源码：编译器、标准库与工具链都在这里。**开发、issue 与 PR 都在 GitHub**
（[Loment-Lang/loment](https://github.com/Loment-Lang/loment)）；GitLab、Gitee、Codeberg、
Bitbucket 上是它的**只读镜像** —— 在那里提交、开 issue 不会有人看到。

它以发行包的形式提供 —— Linux `tar.gz`、Windows `zip`、Windows 安装包 —— 跑起来不需要
Python、不需要 clang，也不需要 WSL。

## 为什么是 Loment？

- **小。** 一个程序编成原生的 x86-64 可执行文件，产物里**没有运行时、也不依赖 libc** ——
  Linux ELF、Windows PE，或者裸机目标。工具链本身也用 Loment 写成。
- **可问责。** 能力域（capability domain）写明一个单元能伸多远，form object 记录它持有什么、
  够得着什么 —— 一个程序能碰什么，不读源码也能回答。
- **熟悉。** 语法是 Rust 风的，而且同一份程序还可以用另外六门写法来写：C、C++、Java、C#、
  Go、Python。

诊断信息来自一个独立的程序：`lomenterr version` 打印 `lomenterr 0.1.2` —— 那是它自己的
版本号。

## 第一个程序

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

## 去哪里了解更多

| | |
|---|---|
| [快速上手](QUICKSTART.md) | 源码、工具链，以及最先要用的四条命令。 |
| [语言指南](.claude/skills/loment/SKILL.md) | 语法、内建函数、错误码。随包发行，`loment skill --print` 可直接打印。 |
| [标准](standard/LLS.md) | LLS，即《Loment 语言标准》—— 哪些已经冻结、谁判定符合性。 |
| [手册](docs/manual/index.md) · [示例](loment/examples/) | 生成的参考，以及 34 个示例程序。 |
| [Wiki](https://loment.fandom.com/) | 语言、工具链与生态，可浏览。 |
| [docs/](docs/) | 设计与实测记录。大部分用中文写。 |

## 获取帮助

在 [issue](https://github.com/Loment-Lang/loment/issues)、[Discord](https://discord.gg/rGw7NRNU)
或 [Mastodon](https://mastodon.social/@Loment) 上提问。问问题和报 bug 一样受欢迎。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。Loment 是把 AI 编码 agent 放在回路里开发的，而且
它如实说出来：人定方向，程序决定什么能落地，符合性测试决定什么符合标准。工作约定在
`AGENTS.md`。

## 许可

**Loment-Open**：MIT 加两条 —— 衍生源码必须署名 Loment，名字保留。见 [LICENSE](LICENSE)。
