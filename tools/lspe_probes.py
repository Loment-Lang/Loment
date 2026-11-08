#!/usr/bin/env python3
"""E8 反证探针登记表（`docs/262` §5.4 / `#766`）。

**机械生成，不手写。** 判据文件里的一行标记就是一条探针：

    # lspe-probe: InBounds

`PROBES` 是 `{义务: [(文件, 行号), …]}`。`missing()` 给出"有站点义务但没有探针"
的那些 —— 那正是 `UNDECIDED(inert)` / Q002 的输入。

用法：

    python tools/lspe_probes.py --check     # 缺探针就红（判据用）
    python tools/lspe_probes.py --list      # 打印登记表
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "loment"))

import lspe  # noqa: E402

MARK = re.compile(r"#\s*lspe-probe:\s*([A-Za-z_][A-Za-z0-9_]*)")


def scan(root: Path = ROOT) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for p in sorted((root / "tools").glob("*_test.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in MARK.finditer(line):
                out.setdefault(m.group(1), []).append((p.name, i))
    return out


#: 生成的登记表（导入时就扫一次；`--check` 只是把它和语义面比一遍）。
PROBES = scan()


def missing() -> list[str]:
    """有义务、但没有配对探针的那些。"""
    return sorted(lspe.LSPE_PROBED_OBLIGATIONS - set(PROBES))


def unknown() -> list[str]:
    """标了探针、但义务名不在登记表里的（改名后的残骸）。"""
    return sorted(set(PROBES) - set(lspe.LSPE_OBLIGATIONS))


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="lspe_probes")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    bad = missing()
    unk = unknown()
    if a.list or not a.check:
        for ob in sorted(PROBES):
            print(f"{ob}: " + ", ".join(f"{f}:{n}" for f, n in PROBES[ob]))
        if bad:
            print("缺探针: " + ", ".join(bad))
        if unk:
            print("不认识的义务: " + ", ".join(unk))
    if a.check and (bad or unk):
        for ob in bad:
            print(f"[ERR] {ob} 没有反证探针 —— 它的检查是惰性的（Q002）",
                  file=sys.stderr)
        for ob in unk:
            print(f"[ERR] 探针指向不存在的义务 {ob}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
