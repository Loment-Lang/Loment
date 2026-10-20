# compare/ — Loment 与 Rust / CPython / OpenJDK 的缺陷对照

2026-10-19 的一次调查：从三个成熟仓各取 30 条真实 issue，逐条判断 Loment 是否复现**同一类**
缺陷。判定的目的不是"像不像"，而是看前人踩过的哪几条路，Loment 已经走到、或者正在走过去。

## 文件

| 文件 | 内容 |
|---|---|
| `report.md` | 90 条逐条判定，外加"四条前人走过的路"在本轮的实测证据 |
| `rust.txt` | rust-lang/rust 的 30 条（`label:I-unsound`，按评论数取） |
| `python.txt` | python/cpython 的 30 条（`label:type-bug`，按评论数取） |
| `jdk.txt` | JBS 最近 30 条已修 Bug（OpenJDK 的 GitHub 镜不收 issue）；编号 + 主题，判定在 `report.md` 里 |

## 怎么读

判定分四档：

- **复现** —— Loment 有同一类缺陷，并给出本轮或已存在的 issue 编号；
- **同类已在册** —— 同类问题此前已经开过 issue；
- **未复现** —— 实测过，Loment 不犯；
- **不适用** —— 这条路的前提在 Loment 里不存在（没有优化器、没有并发运行时、没有 GC 屏障……）。
  这一档是"没走到那条路"，不是"走对了"。

`report.md` 里能实测的结论都有最小复现；探针留在本轮的一次性调查目录里，不随本报告入仓。

## 时效

这是 2026-10-19 的快照。被对照的仓、以及 Loment 自己的 issue 状态都会变；引用前先看日期。
