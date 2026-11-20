"""measure_ladder.py — reproduce the composition measurement of §8 of the paper.

For every `.lomt` unit in the project's own source tree: force the configuration line to
`gc_auto_alpha` + `runtime` (so the four-rung ladder is active), compile with the project's
reference compiler, and read the emitted `gc_ladder` field. Nothing else is edited.

    python papers/loment-gc/measure_ladder.py

Requires only the repository itself; no third-party packages.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lomc  # noqa: E402
import lomentc  # noqa: E402

DIRS = ["loment/examples", "loment/lib", "loment/selfhost",
        "loment/tools", "loment/ct", "lompi"]


def parse(src: str):
    return lomentc.Parser(lomc.lex(src), src).parse()


def inject(src: str) -> str | None:
    """Force `gc_auto_alpha` + `runtime` onto a unit, changing nothing else.

    Returns None when the unit is not Loment-surface (no `module` line), i.e. one of the
    C-/Python-surface units of the multi-grammar corpus.
    """
    lines = src.splitlines()
    if not any(re.match(r"\s*module\b", ln) for ln in lines):
        return None
    drop = re.compile(r"\s*choose\s+(gc_\w+|runtime|no_runtime)\s*$")
    out, done = [], False
    for ln in lines:
        if drop.match(ln):
            continue
        out.append(ln)
        if not done and re.match(r"\s*module\b", ln):
            out.append("choose gc_auto_alpha")
            out.append("choose runtime")
            done = True
    return "\n".join(out) + "\n"


def group_of(rel: str) -> str:
    if rel.startswith("lompi/store/std"):
        return "Standard library"
    if rel.startswith("lompi/"):
        return "Package manager"
    if rel.startswith("loment/examples/"):
        return "Examples"
    if rel.startswith("loment/lib/"):
        return "Core library"
    if rel.startswith("loment/selfhost/"):
        return "Self-hosted compiler"
    if rel.startswith("loment/tools/"):
        return "Tooling"
    if rel.startswith("loment/ct/"):
        return "Surface-grammar corpus"
    return "Other"


ORDER = ["Standard library", "Package manager", "Tooling", "Examples",
         "Self-hosted compiler", "Surface-grammar corpus", "Core library"]

files = []
for d in DIRS:
    p = ROOT / d
    if p.exists():
        files += sorted(p.rglob("*.lomt"))
files = [f for f in files if "/build/dist/" not in f.as_posix()]

rows, failed, non_surface = [], [], []
for f in files:
    rel = f.relative_to(ROOT).as_posix()
    if "/neg/" in rel or rel.endswith("_neg.lomt"):
        continue
    forced = inject(f.read_text(encoding="utf-8", errors="replace"))
    if forced is None:
        non_surface.append(rel)
        continue
    try:
        pot = json.loads(lomentc.emit_potato(parse(forced), ROOT))
        gl = pot.get("gc_ladder")
        (rows.append((rel, gl)) if isinstance(gl, dict) else failed.append(rel))
    except Exception:  # noqa: BLE001
        failed.append(rel)

agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
for rel, gl in rows:
    c = agg[group_of(rel)]
    for k in ("l0", "l1", "l2", "l3", "total_sites"):
        c[k] += gl[k]
    c["files"] += 1

print("LADDER COMPOSITION — forced choose gc_auto_alpha + runtime")
print(f"scanned {len(files)} · compiled {len(rows)} · failed {len(failed)} · "
      f"non-Loment surface {len(non_surface)}")
print()
print(f"{'source area':<24}{'units':>6}{'sites':>7}{'L0':>6}{'L1':>5}{'L2':>5}{'L3':>7}{'kappa':>9}")
T = collections.Counter()
for g in ORDER:
    c = agg.get(g)
    if not c:
        continue
    n = c["total_sites"]
    k = (c["l0"] + c["l1"] + c["l2"]) / n if n else 0.0
    print(f"{g:<24}{c['files']:>6}{n:>7}{c['l0']:>6}{c['l1']:>5}{c['l2']:>5}{c['l3']:>7}{100*k:>8.2f}%")
    for kk in ("l0", "l1", "l2", "l3", "total_sites", "files"):
        T[kk] += c[kk]
n = T["total_sites"]
print(f"{'Total':<24}{T['files']:>6}{n:>7}{T['l0']:>6}{T['l1']:>5}{T['l2']:>5}{T['l3']:>7}"
      f"{100*(T['l0']+T['l1']+T['l2'])/n:>8.2f}%")
print()
nz = sum(1 for _, gl in rows if gl["l0"] + gl["l1"] + gl["l2"] > 0)
print(f"units with any compile-time placement: {nz} of {len(rows)}")
