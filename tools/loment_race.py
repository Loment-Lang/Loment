#!/usr/bin/env python3
"""loment_race — Loment vs C vs Rust, same machine, same algorithms, same clock.

Three axes per kernel, all ELF, all executed inside **one** WSL invocation
(`docs/226` §1: one `wsl -e` costs ~130 ms of startup, so timing must happen
in a single call):

    loment      tools/lomelf.py            (the unoptimized stack machine)
    loment-opt  the same IR -> clang -O2   (docs/212 §5A, the offload path)
    c           gcc -O2 -static
    rust        rustc -O (static crt)

Every program computes a checksum and exits with it; all four must agree, so a
backend cannot "win" by being wrong.

Usage:
    python tools/loment_race.py [--runs 5] [--kernel NAME] [--json] [--keep]

The corpus and every binary live in a temp dir and are removed unless --keep.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import lomentc  # noqa: E402
import lomelf  # noqa: E402
import loment_dist  # noqa: E402


# ---------------------------------------------------------------- kernels
#: Each entry returns (loment, c, rust).  The three spellings are the same
#: algorithm with the same 8-bit checksum on exit.

def _kernels() -> dict[str, tuple[str, str, str]]:
    sum_l = """module sum
fn _start() {
    let acc: u64 = 0;
    let i: u64 = 0;
    while i < 20000000 {
        acc = acc + (i * 7) % 1000;
        i = i + 1;
    }
    syscall4(60, acc & 255, 0, 0);
}
"""
    sum_c = """#include <stdint.h>
int main(void) {
    uint64_t acc = 0, i;
    for (i = 0; i < 20000000ULL; i++) acc += (i * 7ULL) % 1000ULL;
    return (int)(acc & 255);
}
"""
    sum_r = """fn main() {
    let mut acc: u64 = 0;
    let mut i: u64 = 0;
    while i < 20_000_000 { acc += (i * 7) % 1000; i += 1; }
    std::process::exit((acc & 255) as i32);
}
"""

    mem_l = """module mem
choose runtime
fn _start() {
    let p: ptr = alloc(32768);
    let i: u32 = 0;
    while i < 32768 {
        store8(p, i, (i & 255) as u8);
        i = i + 1;
    }
    let k: u32 = 0;
    while k < 5000000 {
        let q: u32 = k & 32767;
        store8(p, q, (((load8(p, q) as u32) + 3) & 255) as u8);
        k = k + 1;
    }
    let s: u32 = 0;
    let j: u32 = 0;
    while j < 32768 {
        s = s + (load8(p, j) as u32);
        j = j + 1;
    }
    syscall4(60, (s & 255) as u64, 0, 0);
}
"""
    mem_c = """#include <stdint.h>
#include <stdlib.h>
int main(void) {
    unsigned char *p = malloc(32768);
    for (uint32_t i = 0; i < 32768; i++) p[i] = (unsigned char)(i & 255);
    for (uint32_t k = 0; k < 5000000; k++) {
        uint32_t q = k & 32767;
        p[q] = (unsigned char)(p[q] + 3);
    }
    uint32_t s = 0;
    for (uint32_t j = 0; j < 32768; j++) s += p[j];
    free(p);
    return (int)(s & 255);
}
"""
    mem_r = """fn main() {
    let mut p = vec![0u8; 32768];
    for i in 0..32768usize { p[i] = (i & 255) as u8; }
    for k in 0..5_000_000usize {
        let q = k & 32767;
        p[q] = p[q].wrapping_add(3);
    }
    let mut s: u32 = 0;
    for j in 0..32768usize { s += p[j] as u32; }
    std::process::exit((s & 255) as i32);
}
"""

    fib_l = """module fib
fn fib(n: u32) -> u32 {
    if n < 2 {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}
fn _start() {
    syscall4(60, (fib(32) & 255) as u64, 0, 0);
}
"""
    fib_c = """#include <stdint.h>
static uint32_t fib(uint32_t n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}
int main(void) { return (int)(fib(32) & 255); }
"""
    fib_r = """fn fib(n: u32) -> u32 {
    if n < 2 { return n; }
    fib(n - 1) + fib(n - 2)
}
fn main() { std::process::exit((fib(32) & 255) as i32); }
"""

    bits_l = """module bits
fn _start() {
    let acc: u64 = 0;
    let i: u64 = 0;
    while i < 2000000 {
        let x: u64 = i;
        let c: u64 = 0;
        while x > 0 {
            c = c + 1;
            x = x & (x - 1);
        }
        acc = acc + c;
        i = i + 1;
    }
    syscall4(60, acc & 255, 0, 0);
}
"""
    bits_c = """#include <stdint.h>
int main(void) {
    uint64_t acc = 0;
    for (uint64_t i = 0; i < 2000000ULL; i++) {
        uint64_t x = i, c = 0;
        while (x > 0) { c++; x &= x - 1; }
        acc += c;
    }
    return (int)(acc & 255);
}
"""
    bits_r = """fn main() {
    let mut acc: u64 = 0;
    for i in 0..2_000_000u64 {
        let mut x = i;
        let mut c = 0u64;
        while x > 0 { c += 1; x &= x - 1; }
        acc += c;
    }
    std::process::exit((acc & 255) as i32);
}
"""

    arr_l = """module arr
choose runtime
fn _start() {
    let p: ptr = alloc(16384);
    let i: u32 = 0;
    while i < 16384 {
        store8(p, i, ((i * 3) & 255) as u8);
        i = i + 1;
    }
    let pass: u32 = 0;
    while pass < 200 {
        let j: u32 = 1;
        while j < 16384 {
            let v: u32 = (load8(p, j) as u32) + (load8(p, j - 1) as u32);
            store8(p, j, (v & 255) as u8);
            j = j + 1;
        }
        pass = pass + 1;
    }
    syscall4(60, (load8(p, 16383) as u64) & 255, 0, 0);
}
"""
    arr_c = """#include <stdint.h>
#include <stdlib.h>
int main(void) {
    uint8_t *p = malloc(16384);
    for (uint32_t i = 0; i < 16384; i++) p[i] = (uint8_t)((i * 3) & 255);
    for (uint32_t pass = 0; pass < 200; pass++)
        for (uint32_t j = 1; j < 16384; j++)
            p[j] = (uint8_t)(p[j] + p[j - 1]);
    uint8_t r = p[16383];
    free(p);
    return (int)(r & 255);
}
"""
    arr_r = """fn main() {
    let mut p = vec![0u8; 16384];
    for i in 0..16384usize { p[i] = ((i as u32) * 3 & 255) as u8; }
    for _ in 0..200 {
        for j in 1..16384usize { p[j] = p[j].wrapping_add(p[j - 1]); }
    }
    std::process::exit((p[16383] & 255) as i32);
}
"""
    return {
        "sum": (sum_l, sum_c, sum_r),
        "mem": (mem_l, mem_c, mem_r),
        "fib": (fib_l, fib_c, fib_r),
        "bits": (bits_l, bits_c, bits_r),
        "arr": (arr_l, arr_c, arr_r),
    }


# ---------------------------------------------------------------- build
def _wsl() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = p.resolve().as_posix()
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _loment_elf(src: Path) -> bytes:
    mod, deps = lomentc.load_unit(src, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise SystemExit(f"{src.name}: {errs[0]}")
    return lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps))[0]


def _clang_elf(src: Path, out: Path, cc: str) -> bytes | None:
    ll = lomentc.emit_llvm(*_load(src), )
    ir = out.with_suffix(".ll")
    ir.write_text(ll, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [cc, "--target=x86_64-unknown-linux-gnu", "-O2", "-static",
         "-nostdlib", "-ffreestanding", "-o", str(out), str(ir)],
        capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        print(f"  [{src.stem}] clang 失败: {r.stderr[-300:]}")
        return None
    return out.read_bytes()


def _load(src: Path):
    mod, deps = lomentc.load_unit(src, ROOT)
    return mod, ROOT, deps


def _compile_in_wsl(td: Path, jobs: list[tuple[str, str]], lang: str) -> None:
    """Compile C / Rust inside WSL.  Not timed, so one call per language is fine."""
    if not jobs:
        return
    lines = ["set -e", f"cd {_wsl_path(td)}"]
    for name, src in jobs:
        if lang == "c":
            lines.append(f"gcc -O2 -static -o {name}.c.bin {src}")
        else:
            lines.append(
                f"rustc -O -C target-feature=+crt-static -o {name}.rs.bin {src}")
    (td / f"build_{lang}.sh").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8", newline="\n")
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"sh {_wsl_path(td / f'build_{lang}.sh')}"],
                       capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        raise SystemExit(f"{lang} 编译失败:\n{r.stderr[-800:]}")


# ---------------------------------------------------------------- time
_TIME_PY = """import json, subprocess, sys, time
out = {}
for name in sys.argv[1:]:
    best = None
    rc = None
    for _ in range(REPS):
        t = time.perf_counter()
        r = subprocess.run(["/tmp/loment-race/" + name], capture_output=True)
        dt = (time.perf_counter() - t) * 1000.0
        rc = r.returncode
        best = dt if best is None else min(best, dt)
    out[name] = (round(best, 3), rc)
print(json.dumps(out))
"""


def _time_all(td: Path, names: list[str], reps: int) -> dict[str, tuple[float, int]]:
    td.mkdir(exist_ok=True)
    (td / "timeall.py").write_text(
        _TIME_PY.replace("REPS", str(reps)), encoding="utf-8", newline="\n")
    lines = ["set -e", "rm -rf /tmp/loment-race", "mkdir -p /tmp/loment-race",
             f"cp {_wsl_path(td)}/*.bin {_wsl_path(td)}/timeall.py /tmp/loment-race/",
             "cd /tmp/loment-race",
             "python3 timeall.py " + " ".join(names)]
    (td / "run.sh").write_text("\n".join(lines) + "\n",
                               encoding="utf-8", newline="\n")
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"sh {_wsl_path(td / 'run.sh')}"],
                       capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        raise SystemExit(f"计时失败:\n{r.stderr[-800:]}")
    return {k: (v[0], v[1]) for k, v in json.loads(r.stdout).items()}


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Loment vs C vs Rust (WSL, same clock)")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--kernel", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    if not _wsl():
        raise SystemExit("需要 WSL（C / Rust / 计时都在 WSL 里跑）")

    kernels = _kernels()
    if a.kernel:
        if a.kernel not in kernels:
            raise SystemExit(f"未知 kernel: {a.kernel}（有 {', '.join(kernels)}）")
        kernels = {a.kernel: kernels[a.kernel]}

    td = Path(tempfile.mkdtemp(prefix="loment-race-"))
    cc = loment_dist.clang_path()
    c_jobs: list[tuple[str, str]] = []
    r_jobs: list[tuple[str, str]] = []
    names: list[str] = []
    try:
        for name, (l_src, c_src, r_src) in kernels.items():
            lom = td / f"{name}.lomt"
            lom.write_text(l_src, encoding="utf-8", newline="\n")
            (td / f"{name}.elf.bin").write_bytes(_loment_elf(lom))
            (td / f"{name}.c").write_text(c_src, encoding="utf-8", newline="\n")
            (td / f"{name}.rs").write_text(r_src, encoding="utf-8", newline="\n")
            c_jobs.append((name, f"{name}.c"))
            r_jobs.append((name, f"{name}.rs"))
            names += [f"{name}.elf.bin", f"{name}.c.bin", f"{name}.rs.bin"]
            if cc:
                blob = _clang_elf(lom, td / f"{name}.clang.bin", cc)
                if blob is not None:
                    names.append(f"{name}.clang.bin")
        _compile_in_wsl(td, c_jobs, "c")
        _compile_in_wsl(td, r_jobs, "rust")
        timed = _time_all(td, [n for n in names if (td / n).exists()], a.runs)
    finally:
        if not a.keep:
            shutil.rmtree(td, ignore_errors=True)

    cols = ["elf.bin", "clang.bin", "c.bin", "rs.bin"]
    label = {"elf.bin": "loment", "clang.bin": "loment-opt", "c.bin": "c -O2",
             "rs.bin": "rust -O"}
    rows = []
    bad = []
    for name in kernels:
        row = {"kernel": name}
        rcs = {}
        for suf in cols:
            key = f"{name}.{suf}"
            if key not in timed:
                continue
            ms, rc = timed[key]
            row[label[suf]] = ms
            rcs[label[suf]] = rc
        unique = set(rcs.values())
        if len(unique) > 1:
            bad.append((name, rcs))
        row["rc"] = unique.pop() if len(unique) == 1 else rcs
        rows.append(row)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"Loment vs C vs Rust  (WSL, min of {a.runs}; ms; lower is better)")
    head = f"{'kernel':8} {'loment':>9} {'loment-opt':>11} {'c -O2':>7} {'rust -O':>8} {'vs C':>6}"
    print(head)
    for row in rows:
        c = row.get("c -O2")
        rel = f"{row.get('loment', 0) / c:.2f}x" if c else "-"
        print(f"{row['kernel']:8} {row.get('loment', 0):9.1f} "
              f"{row.get('loment-opt', 0):11.1f} {c or 0:7.1f} "
              f"{row.get('rust -O', 0):8.1f} {rel:>6}")
    rcs = {row["kernel"]: row["rc"] for row in rows}
    print(f"checksums: {rcs}")
    if bad:
        print(f"[ERR] 同一 kernel 的退出码不一致: {bad}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
