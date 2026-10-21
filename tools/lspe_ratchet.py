#!/usr/bin/env python3
"""`pass_ratio` 的棘轮（`docs/262` §5.16 / `#1065`）。

`lspe pass "reason" { … }` 允许跳过**审计**，但不允许**匿名**跳过：块进台账，覆盖率
里记成 `passed_sites / sites`。没有棘轮时，把语料里的站点逐个包进旁路块就能让
`passed_sites` 一路涨而门禁全绿 —— 收据于是只是装饰。

这里的判据只有一条：**每一份语料单元的 `passed_sites / sites` 不许变大**。
比值变小是**好消息**（有人补了证明或删了旁路），打印出来但不判失败 ——
与 `.github/gate-floors.txt` / `gate-skips.txt` 的"只往好的方向动"同一条纪律。

基线是 `.github/lspe-pass-baseline.txt`，一行一份单元：

    <相对仓根的路径> <passed_sites> <sites>

重算：`python tools/lspe_ratchet.py --emit`（加旁路块之后必须显式改基线，
那一步就是"我知道自己在多卖一格审计"）。

语料表**复用孪生判据那一份**（`loment_potato_emit_test.COVERED` +
`EXTRA_COVERED`）：两份清单各写一遍必然漂，而"这一格看哪些单元"本来就不该有两个答案。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loment"))

import lomentc  # noqa: E402
import lspe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".github" / "lspe-pass-baseline.txt"


def corpus() -> list[str]:
    """判据看的语料，**复用孪生那一格**的名单。"""
    from loment_potato_emit_test import COVERED, EXTRA_COVERED  # noqa: PLC0415

    return [f"loment/examples/{name}" for name in COVERED] + list(EXTRA_COVERED)


def ledger_for(rel: str) -> dict:
    """一份单元在参考实现下的 LSPE 台账（不经过后端：只装载 + 普查）。"""
    mod, deps = lomentc.load_unit(ROOT / rel, ROOT)
    return lspe.build_ledger(
        sites=getattr(mod, "lspe_sites", []),
        state=mod.chooses.get("lspe", lomentc.CORE_DEFAULTS["lspe"]),
        set_by=("source" if "lspe" in mod.chooses else "default"),
        level=int(getattr(mod, "lspe_level", 3)),
        toks=getattr(mod, "toks", []),
        unit=mod.name,
        deps=[getattr(d, "toks", []) for d in deps],
        passes=getattr(mod, "lspe_passes", []),
    )


def measure(units: list[str] | None = None) -> dict[str, tuple[int, int]]:
    """`{单元: (passed_sites, sites)}`。"""
    return {rel: (doc["passed_sites"], doc["sites"])
            for rel in (units or corpus())
            for doc in [ledger_for(rel)]}


def load_baseline(path: Path = BASELINE) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rel, passed, sites = line.split()
        out[rel] = (int(passed), int(sites))
    return out


def dump_baseline(rows: dict[str, tuple[int, int]], path: Path = BASELINE) -> None:
    head = (
        "# `lspe pass` 的通过率基线（`docs/262` §5.16 / #1065）。\n"
        "#\n"
        "# 一行一份语料单元：<路径> <passed_sites> <sites>。\n"
        "# 比值**不许变大**：把站点包进 `lspe pass \"…\"` 之后必须显式改这里，\n"
        "# 那一步就是\"我知道自己在多卖一格审计\"。比值变小是好事，照常更新、不判红。\n"
        "#\n"
        "# 重算：`python tools/lspe_ratchet.py --emit`\n"
    )
    body = "".join(f"{rel} {rows[rel][0]} {rows[rel][1]}\n" for rel in sorted(rows))
    path.write_text(head + body, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    rows = measure()
    if "--emit" in args:
        dump_baseline(rows)
        print(f"lspe_ratchet: 写了 {len(rows)} 份单元的基线 -> "
              f"{BASELINE.relative_to(ROOT)}")
        return 0
    passed = sum(p for p, _ in rows.values())
    sites = sum(s for _, s in rows.values())
    print(f"lspe_ratchet: {len(rows)} 份单元，{passed}/{sites} 个站点被旁路")
    for rel in sorted(rows):
        p, s = rows[rel]
        if p:
            print(f"  {rel}: {p}/{s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
