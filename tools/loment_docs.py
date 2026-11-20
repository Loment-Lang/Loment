#!/usr/bin/env python3
"""loment_docs.py — `docs/NNN-slug.md` 的**编号**：查重号、取下一个空号、撞了改号。

## 它修的是什么

`docs/NNN-slug.md` 里的 `NNN` 是约定的一部分：`docs/i18n/glossary.md` 记着它
"让 ~1700 处 `docs/NNN` / `docs/NNN §N` 形式的引用**免费成立**"。而**两条线并行**时，
两边都按 `CONTRIBUTING.md` 那句"用下一个空号"去取 —— 于是**撞号**。本仓实测过一次：
一份 `docs/227` 在合 main 时撞上了另一条线先合的 `docs/227`。

撞号**没有判据看着**，落地的形状是"两份文档共用一个号"，按那个号写的引用从此指哪份都不对。

## 三条决定

1. **只改"这一次新增的那一份"，不碰已经在 base 上的号。** 已经合进 base 的号是既成事实，
   动它就要动引用它的一切。
2. **只重写无歧义的引用**：全路径形式 `docs/NNN-slug`（slug 唯一，指谁没疑问）。
   **裸 `docs/NNN` 不动** —— 撞号恰恰意味着这个号现在指两份，替你猜是错的。
   工具会把裸引用**列出来**让人自己看。
3. **空号取 `max(已用) + 1`** —— 与 `CONTRIBUTING.md` 的 "next free" 同读；单调递增，
   不去填历史上的空洞（填洞会让号段的历史读起来是错的）。

用法::

    python tools/loment_docs.py --check [ROOT]                      # 判据：树里有重号吗（1 = 有）
    python tools/loment_docs.py --next  [ROOT]                      # 打印下一个空号
    python tools/loment_docs.py --fix [--base origin/main] [ROOT]   # 撞了就改号
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: `docs/NNN-slug.md`（也吃英文孪生 `docs/NNN-slug.en.md` —— 号在前、slug 在后）。
DOC_RE = re.compile(r"^(\d{1,4})-([A-Za-z0-9][A-Za-z0-9._-]*)\.md$")

#: 文档体里那行 H1 的编号（`# 227 · 标题`）。
H1_RE = re.compile(r"^#\s+(\d{1,4})(\s*[·\-—:]|\s)")

#: 一棵 `docs/` 之外还会引用文档号的地方：整个仓的 `.md`。
SKIP_DIRS = {".git", "build"}


def number_of(path: Path) -> int | None:
    m = DOC_RE.match(path.name)
    return int(m.group(1)) if m else None


def tail_of(path: Path) -> str:
    """文件名里号之后的那一段（`227-grammar-semantics-audit.md` -> `grammar-semantics-audit`）。"""
    m = DOC_RE.match(path.name)
    return m.group(2) if m else ""


def slug_key(path: Path) -> str:
    """同号不同**文档**的判据用这个：**英文孪生不算另一份**。

    `docs/i18n/glossary.md` §1 写死了：`docs/NNN-slug.en.md` 与它的源**同号同 slug**，
    体例要求 "Keep the number and the slug unchanged"。所以 `141-x.md` 与 `141-x.en.md`
    是**一份文档的两面**，不是撞号。
    """
    t = tail_of(path)
    return t[:-3] if t.endswith(".en") else t


def width_of(path: Path) -> int:
    m = DOC_RE.match(path.name)
    return len(m.group(1)) if m else 3


def scan(root: Path) -> dict[int, list[Path]]:
    """`docs/` 下所有带号的 `.md`，按号分组。"""
    out: dict[int, list[Path]] = {}
    docs = root / "docs"
    if not docs.is_dir():
        return out
    for p in sorted(docs.iterdir()):
        if p.is_file():
            n = number_of(p)
            if n is not None:
                out.setdefault(n, []).append(p)
    return out


def duplicates(root: Path) -> dict[int, list[Path]]:
    """**真撞号**：同号之下出现了**两个不同的文档**（英文孪生不算，见 `slug_key`）。"""
    out: dict[int, list[Path]] = {}
    for n, paths in scan(root).items():
        if len({slug_key(p) for p in paths}) > 1:
            out[n] = paths
    return out


def next_free(used: set[int]) -> int:
    return max(used) + 1 if used else 1


# ------------------------------------------------------------------ git
def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root)] + list(args),
                       capture_output=True, text=True, errors="replace", shell=False)
    if r.returncode:
        raise RuntimeError((r.stderr or "").strip()[:200])
    return r.stdout


def base_numbers(root: Path, base: str) -> set[int] | None:
    """`<base>:docs` 上已经用掉的号。**取不到就 None**（调用方按"不确定"处理，不改号）。"""
    try:
        out = _git(root, "ls-tree", "--name-only", f"{base}:docs")
    except RuntimeError:
        return None
    used = set()
    for line in out.splitlines():
        n = number_of(Path(line.strip()))
        if n is not None:
            used.add(n)
    return used


def added_docs(root: Path, base: str) -> list[Path] | None:
    """这一次相对 base **新增或改名**进来的带号 `.md`（`A` 与 `R` 的新路径）。"""
    try:
        out = _git(root, "diff", "--name-status", "--diff-filter=AR",
                   f"{base}...HEAD", "--", "docs")
    except RuntimeError:
        return None
    res = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            p = Path(parts[-1].strip())
            if p.suffix == ".md" and number_of(p) is not None:
                res.append(root / p)
    return res


# ------------------------------------------------------------------ 改号
def retitle(path: Path, new: int) -> bool:
    """把文档体 H1 里那个号换掉。返回是否真的换了。"""
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, ln in enumerate(lines):
        m = H1_RE.match(ln)
        if m:
            if int(m.group(1)) == new:
                return False
            lines[i] = f"# {new}{ln[len('# ') + len(m.group(1)):]}"
            path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
            return True
    return False


def rewrite_full_refs(root: Path, old_tail: str, new_stem: str, skip: Path | None = None) -> list[tuple[Path, int]]:
    """把**全路径**引用 `docs/NNN-<tail>` 换成新号（含 `.en.md` 跟随形式）。

    **只动全路径**：`docs/NNN-<tail>` 的 tail 唯一，指谁没有疑问。裸 `docs/NNN` 不碰。

    `new_stem` 是**补好零**的新文件名干（`002-b`）—— 传 int 会把 `002` 写成 `2`。
    """
    pat = re.compile(r"docs/\d{1,4}-" + re.escape(old_tail) + r"(?![\w-])")
    hits: list[tuple[Path, int]] = []
    for p in root.rglob("*.md"):
        if (skip is not None and p == skip) or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new, n = pat.subn(f"docs/{new_stem}", text)
        if n:
            p.write_text(new, encoding="utf-8", newline="\n")
            hits.append((p, n))
    return hits


def bare_refs(root: Path, numbers: set[int], skip: Path) -> list[tuple[Path, int]]:
    """**只报不改**：树里裸 `docs/NNN` 的引用（撞号时它是有歧义的）。"""
    pat = re.compile(r"docs/(\d{1,4})(?![\d-])")
    out = []
    for p in root.rglob("*.md"):
        if p == skip or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n = sum(1 for m in pat.finditer(text) if int(m.group(1)) in numbers)
        if n:
            out.append((p, n))
    return out


# ------------------------------------------------------------------ 命令
def cmd_check(root: Path) -> int:
    dups = duplicates(root)
    if not dups:
        print(f"[OK] docs/ 下 {len(scan(root))} 个号，无重号")
        return 0
    print(f"[ERR] docs/ 下 {len(dups)} 个号被两份文档共用：", file=sys.stderr)
    for num, paths in sorted(dups.items()):
        print(f"  {num}: " + "、".join(p.name for p in paths), file=sys.stderr)
    print("  改法：`python tools/loment_docs.py --fix`（撞的那一份挪到下一个空号）", file=sys.stderr)
    return 1


def cmd_fix(root: Path, base: str) -> int:
    used_base = base_numbers(root, base)
    if used_base is None:
        print(f"[ERR] 取不到 `{base}:docs` —— 先 `git fetch origin`（**不确定时不改号**）",
              file=sys.stderr)
        return 2
    added = added_docs(root, base)
    if added is None:
        print(f"[ERR] 取不到相对 `{base}` 的改动清单", file=sys.stderr)
        return 2

    taken = set(used_base)
    changed: list[str] = []
    assigned: list[int] = []
    for p in sorted(added, key=lambda q: (number_of(q) or 0, q.name)):
        n = number_of(p)
        if n is None:
            continue
        if n not in taken:
            taken.add(n)
            continue
        new_num = next_free(taken)
        old_tail, w = tail_of(p), width_of(p)
        new_stem = f"{new_num:0{w}d}-{old_tail}"
        target = p.with_name(new_stem + ".md")
        retitle(p, new_num)
        p.rename(target)
        # **被改号的那份自己也在重写范围内** —— 它正文里可能引用自己。
        hits = rewrite_full_refs(root, old_tail, new_stem)
        taken.add(new_num)
        assigned.append(new_num)
        extra = f"，另改了 {sum(h[1] for h in hits)} 处全路径引用" if hits else ""
        changed.append(f"{p.name} -> {target.name}（{n} 已被 `{base}` 占用）{extra}")

    if not changed:
        print(f"[OK] 相对 `{base}` 没有撞号；docs/ 下 {len(scan(root))} 个号")
        return 0

    for line in changed:
        print(f"[改号] {line}")

    # **只看"我刚发的那些号"**，不看整棵树 —— base 上本来就有的撞号（2026-10-11 实测 7 个）
    # 不归这一趟管，拿它们判会让机器人**每一次推都红**（第一版就是这么错的：改号明明成功，
    # 却在全树查重时撞上那些既有的，于是自判失败）。
    cur = scan(root)
    bad = [n for n in assigned if len({slug_key(p) for p in cur.get(n, [])}) > 1]
    if bad:
        print(f"[ERR] 我新发的号里还有撞的（同一次里两处撞到同一个空号？）：{sorted(bad)}",
              file=sys.stderr)
        for n in bad:
            print(f"  {n}: " + "、".join(p.name for p in cur[n]), file=sys.stderr)
        return 1
    loose = bare_refs(root, assigned, root / "docs" / "none")
    print(f"[OK] 改了 {len(changed)} 份；我发的那些号都不撞。")
    print("     **裸 `docs/NNN` 引用一概没动**（撞号时它指两份里的哪一份没法替你决定）。")
    if loose:
        print("     下面这些文件里有撞号那几个号的裸引用，请自己看一眼是不是指新文档：")
        for p, n in loose[:10]:
            print(f"       {p.relative_to(root)}  ({n} 处)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="docs/NNN 编号：查重号 / 取空号 / 撞了改号")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--next", action="store_true")
    g.add_argument("--fix", action="store_true")
    ap.add_argument("--base", default="origin/main",
                    help="改号时把谁算作已占用（默认 origin/main）")
    ap.add_argument("root", nargs="?", default=".")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.check:
        return cmd_check(root)
    if a.next:
        print(next_free(set(scan(root))))
        return 0
    return cmd_fix(root, a.base)


if __name__ == "__main__":
    sys.exit(main())
