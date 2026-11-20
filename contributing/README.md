# Loment Development Contribution Plan

This is the second book. [Using Loment](../book/README.md) teaches the language; this one explains
how the project is built and how to change it.

Read it if you want to open a pull request, add a criterion, touch either compiler, cut a release,
or understand why the gate is shaped the way it is. It is also the map for the architecture: the
reference and self-hosted compilers, `lomelf`, the Potato form object, the LVM and the garbage
collector tiers.

## Contents

| Part | Chapters |
|---|---|
| **The working environment** | [The repository](src/01-the-repository.md) · [Environment](src/02-environment.md) · [Working agreements](src/03-working-agreements.md) |
| **The gate** | [The gate](src/04-the-gate.md) · [Adding a criterion](src/05-adding-a-criterion.md) · [Adding a tool](src/06-adding-a-tool.md) |
| **The compiler** | [The two compilers](src/07-the-two-compilers.md) · [The bootstrap chain](src/08-the-bootstrap-chain.md) · [Architecture tour](src/09-architecture.md) |
| **Shipping** | [Releases](src/10-releases.md) · [Docs and the standard](src/11-docs-and-the-standard.md) · [Roadmap and milestones](src/12-roadmap-and-milestones.md) |
| **Joining in** | [Your first PR](src/13-your-first-pr.md) · [Where to help](src/14-where-to-help.md) |

## The rule the whole book comes back to

Every claim in this project is supposed to be measured by a criterion under `tools/`. If you change
behaviour, you add or change a criterion; it must be red before your fix and green after. Deleting
a criterion to make the gate green is itself a failure.

That rule is why the book spends three chapters on the gate before it touches the compiler.
