# Introduction

[Using Loment](../../book/src/00-introduction.md) teaches the language. This book teaches the
project.

Loment is not a single program. It is a language surface that exists twice — a reference compiler
in Python and a self-hosted compiler written in Loment — plus a linker that turns a deliberately
plain IR into machine code, a bootstrapping chain rooted in a committed binary, a package manager
with content-addressed identities, a bytecode VM, two memory-safety lines, and a gate that runs
close to a thousand criteria in parallel shards before anything merges.

That is too much to hold in your head from reading the source. This book is the map: what each
piece is for, which file to open when something goes wrong, and what a change is expected to cost.

## Who this is for

You want to change Loment, not just use it. You can read Python and are willing to read Loment
source. You do not need to know Rust.

## What this book assumes

- You have a checkout and a working `loment` command: see
  [Environment](02-environment.md).
- You know the language at the level of [Using Loment](../../book/src/00-introduction.md). If a
  chapter here says `guard` or `choose runtime`, it will not explain them again.

## What this book does not replace

Three things stay authoritative, and they change faster than a book can:

- **`CLAUDE.md` / `AGENTS.md`** — the working agreements that code cannot show you. This book
  summarises them in [Working agreements](03-working-agreements.md), but the file is the rule.
- **`docs/`** — the design records. Every subsystem has one, and the chapter that covers it names
  the number.
- **`CONTRIBUTING.md`** — the short version of how to submit a change.

## How the book is checked

The usage book's `rust,run` blocks are compiled and executed by `tools/loment_book_test.py`. This
book contains shell commands and pointers rather than complete programs, so it has no runnable
blocks; the site generator still renders it and `tools/loment_book_site_test.py` still checks that
every chapter has a page and every link resolves. A dead link here is a red gate.

## The shape of a change

Most changes in this repository have the same skeleton:

1. a criterion that fails on today's tree;
2. the smallest change that makes it pass;
3. the criterion and the change in the same pull request;
4. the gate green, with the two capacity ratchets updated in the same commit.

The chapters that follow explain each of those pieces.
