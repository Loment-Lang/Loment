#!/usr/bin/env python3
"""loment_store_index.py — 生成 store 的模块索引（`docs/store-index.md`）。

**为什么需要它**：store 现在有三百多个模块，`lompi store` 只列名字。想知道
"监听目录用哪个模块"、"读一个文件全部内容有没有现成的"，只能一个文件一个文件翻。
这份索引把**每个模块的一句话目的 + 公开名字**摊平成一页，按包分组。

**为什么是生成物**：手写的索引一定漂 —— 模块加了、名字改了，没人会记得回来改它。
所以索引由源码推出来，`--check` 逐字节比；判据 `loment_lompi_test` 里有一条
`test_the_store_index_is_current`。

用法：
    python tools/loment_store_index.py            # 写 docs/store-index.md
    python tools/loment_store_index.py --check    # 只比，不写（门禁用）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "lompi" / "store"
OUT = ROOT / "docs" / "store-index.md"

#: 包在索引里的顺序 —— 从"可移植"到"平台"，与 docs 里的叙述顺序一致。
PACKAGE_ORDER = [
    "std", "host", "internet", "os", "math", "science", "engineering",
    "dev", "world", "lowlevel", "system", "baremetal", "windows",
]

_RE_PFN = re.compile(r"^pub fn ([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M)
_RE_PCONST = re.compile(r"^pub const ([A-Za-z_][A-Za-z0-9_]*)\s*:", re.M)
_RE_FIRST = re.compile(r"^//\s*\S+?\.lomt\s*[—\-]+\s*(.+?)\s*$")


def _purpose(text: str) -> str:
    """模块的一句话目的：文件头第一行 `// <路径> — <一句话>` 里那半句。"""
    for line in text.splitlines()[:6]:
        m = _RE_FIRST.match(line)
        if m:
            return m.group(1)
    return ""


def _module_row(p: Path) -> tuple[str, str, list[str], list[str]]:
    text = p.read_text(encoding="utf-8")
    return (p.stem, _purpose(text), _RE_PFN.findall(text), _RE_PCONST.findall(text))


def render() -> str:
    pkgs = [p for p in STORE.glob("*/*") if p.is_dir()]
    order = {name: i for i, name in enumerate(PACKAGE_ORDER)}
    pkgs.sort(key=lambda p: (order.get(p.parent.name, 99), p.parent.name))

    out: list[str] = []
    total_mods = 0
    total_fns = 0
    for pkgdir in pkgs:
        pkg = pkgdir.parent.name
        mods = sorted(pkgdir.glob("*.lomt"))
        if not mods:
            continue
        rows = [_module_row(p) for p in mods]
        total_mods += len(rows)
        total_fns += sum(len(r[2]) for r in rows)
        out.append(f"## {pkg}（{len(rows)} 个模块）")
        out.append("")
        for name, purpose, fns, cons in rows:
            head = f"- **`{name}`**"
            if purpose:
                head += f" — {purpose}"
            if cons:
                head += f"（`{'`·`'.join(cons)}`）"
            out.append(head)
            if fns:
                out.append(f"  - `{'`, `'.join(fns)}`")
        out.append("")

    head = [
        "# Loment store 模块索引（生成物）",
        "",
        "> 由 `tools/loment_store_index.py` 从 `lompi/store/**` 生成。**不要手改** ——",
        "> 判据 `loment_lompi_test::test_the_store_index_is_current` 逐字节比，",
        "> 改了模块忘了重生成就会红。",
        "",
        f"合计 **{total_mods} 个模块**、**{total_fns} 个公开函数**。"
        "每行末尾括号里是该模块的公开常量。",
        "",
    ]
    return "\n".join(head + out).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in argv:
        try:
            cur = OUT.read_text(encoding="utf-8")
        except OSError:
            print(f"缺 {OUT.relative_to(ROOT)} —— 跑 python tools/loment_store_index.py")
            return 1
        if cur != text:
            print("docs/store-index.md 过期 —— 跑 python tools/loment_store_index.py")
            return 1
        print(f"docs/store-index.md 与 store 一致（{len(text.splitlines())} 行）")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"[OK] {OUT.relative_to(ROOT)}（{len(text.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
