#!/usr/bin/env python3
"""Flomtc V0: a reproducible compile-time harness for docs/350.

This does not change the compiler.  It measures the reference pipeline
in-process, on a deterministic ~5000-line medium corpus:

    front   = lomentc.load_unit
    check   = lomentc.check
    potato  = lomentc.emit_potato
    ir      = lomentc.emit_llvm
    lomelf  = lomelf.compile_ll

`--flomtc on` adds `choose flomtc_true` to the corpus (the gated fast path);
`--flomtc both` runs both and checks that every artifact is byte-identical.

Usage:
    python tools/flomtc.py bench [--file PATH] [--functions N] [--runs N]
                                 [--target-ms N] [--flomtc off|on|both]
                                 [--json] [--keep]
    python tools/flomtc.py gen --out PATH [--functions N] [--flomtc]

The corpus is generated in a temporary directory (never in a GLOB dir) and
removed unless --keep is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import lomentc  # noqa: E402
import lomelf  # noqa: E402


def gen_corpus(path: Path, functions: int, flomtc: bool = False) -> int:
    lines = ["module bench5000", ""]
    if flomtc:
        lines.insert(1, "choose flomtc_true")
    for i in range(functions):
        lines += [
            f"fn f{i}(x: u32) -> u32 {{",
            f"    let y: u32 = x + {i % 7};",
            "    if y > 10 {",
            "        y = y - 1;",
            "    }",
            "    let k: u32 = 0;",
            "    while k < 3 {",
            "        y = y + k;",
            "        k = k + 1;",
            "    }",
            "    return y;",
            "}",
            "",
        ]
    lines += ["fn _start() {"]
    for i in range(functions):
        lines.append(f"    let a{i}: u32 = f{i}({i % 13});")
    lines += ["    syscall4(60, 0, 0, 0);", "}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


def run_once(src: Path) -> dict:
    """Same order as the real driver: Potato is emitted before LLVM."""
    t0 = time.perf_counter()
    mod, deps = lomentc.load_unit(src, ROOT)
    t1 = time.perf_counter()
    errs = lomentc.check(mod, deps=deps)
    t2 = time.perf_counter()
    potato = lomentc.emit_potato(mod, ROOT, deps)
    t3 = time.perf_counter()
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    t4 = time.perf_counter()
    blob, _ = lomelf.compile_ll(ll, [])
    t5 = time.perf_counter()

    def sha(s: str | bytes) -> str:
        data = s if isinstance(s, bytes) else s.encode()
        return hashlib.sha256(data).hexdigest()

    return {
        "front_ms": (t1 - t0) * 1000.0,
        "check_ms": (t2 - t1) * 1000.0,
        "potato_ms": (t3 - t2) * 1000.0,
        "ir_ms": (t4 - t3) * 1000.0,
        "lomelf_ms": (t5 - t4) * 1000.0,
        "total_ms": (t5 - t0) * 1000.0,
        "errors": len(errs),
        "ir_bytes": len(ll),
        "elf_bytes": len(blob),
        "potato_bytes": len(potato),
        "potato_sha": sha(potato),
        "ir_sha": sha(ll),
        "elf_sha": sha(blob),
    }


def bench(src: Path, runs: int, target_ms: float) -> dict:
    rows = [run_once(src) for _ in range(max(1, runs))]
    best = min(rows, key=lambda r: r["total_ms"])
    if best["errors"]:
        raise SystemExit(f"corpus failed check: {best['errors']} errors")
    return {
        "corpus": str(src),
        "lines": len(src.read_text(encoding="utf-8").splitlines()),
        "runs": rows,
        "best": best,
        "target_ms": target_ms,
        "gap": best["total_ms"] / target_ms if target_ms else 0.0,
    }


def print_table(result: dict) -> None:
    b = result["best"]
    total = b["total_ms"] or 1.0
    print("Flomtc V0 (reference pipeline, in-process)")
    print(f"corpus: {result['corpus']} ({result['lines']} lines)")
    print(f"{'stage':10} {'best ms':>9} {'share':>7}")
    for key, label in (("front_ms", "front"), ("check_ms", "check"),
                       ("potato_ms", "potato"), ("ir_ms", "IR"),
                       ("lomelf_ms", "lomelf")):
        print(f"{label:10} {b[key]:9.0f} {b[key] / total:7.0%}")
    print(f"{'total':10} {b['total_ms']:9.0f} {1.0:7.0%}")
    print(f"target {result['target_ms']:.0f} ms  gap {result['gap']:.2f}x")
    print(f"IR {b['ir_bytes']} B  ELF {b['elf_bytes']} B  "
          f"Potato {b['potato_bytes']} B  errors {b['errors']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Flomtc V0 compile-time harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="time the reference pipeline")
    b.add_argument("--file", type=Path, default=None)
    b.add_argument("--functions", type=int, default=360)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--target-ms", type=float, default=1000.0)
    b.add_argument("--flomtc", choices=("off", "on", "both"), default="both")
    b.add_argument("--json", action="store_true")
    b.add_argument("--keep", action="store_true")

    g = sub.add_parser("gen", help="write the deterministic corpus")
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--functions", type=int, default=360)
    g.add_argument("--flomtc", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "gen":
        lines = gen_corpus(args.out, args.functions, args.flomtc)
        print(f"wrote {args.out}: {lines} lines, {args.functions} functions")
        return 0

    tmp: Path | None = None
    srcs: dict[str, Path] = {}
    if args.file is not None:
        srcs["off"] = args.file
        if args.flomtc in ("on", "both"):
            srcs["on"] = args.file
    else:
        tmp = Path(tempfile.mkdtemp(prefix="flomtc-"))
        srcs["off"] = tmp / "bench5000.lomt"
        gen_corpus(srcs["off"], args.functions, False)
        if args.flomtc in ("on", "both"):
            srcs["on"] = tmp / "bench5000-flomtc.lomt"
            gen_corpus(srcs["on"], args.functions, True)

    try:
        results = {mode: bench(src, args.runs, args.target_ms)
                   for mode, src in srcs.items()}
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for mode, result in results.items():
                print(f"--- flomtc {mode} ---")
                print_table(result)
            if len(results) == 2:
                off, onb = results["off"]["best"], results["on"]["best"]
                same = all(off[k] == onb[k]
                           for k in ("potato_sha", "ir_sha", "elf_sha"))
                print(f"identical artifacts: {same}  "
                      f"speedup {off['total_ms'] / onb['total_ms']:.2f}x")
                if not same:
                    raise SystemExit("flomtc_true changed an artifact")
    finally:
        if tmp is not None and not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
