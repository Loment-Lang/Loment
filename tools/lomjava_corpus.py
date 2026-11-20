#!/usr/bin/env python3
"""lomjava_corpus.py — run the AOT front end over a corpus of real jars.

The jars themselves are **not** in the tree (they are third-party binaries and
tens of megabytes). `tools/fixtures/java-corpus.txt` pins their coordinates,
sizes and sha256 so a run is reproducible:

    python tools/lomjava_corpus.py --fetch --dir /d/Dev/loment-java-corpus
    python tools/lomjava_corpus.py --dir /d/Dev/loment-java-corpus

Each jar is compiled in its own subprocess (a big jar is minutes of pure-Python
work, and one bad input must not take the run down). Per jar the report says
which phase failed — `parse` (J001), `verify` (J002), `lower` (J003) — with the
first error, so the histogram points at the next thing worth fixing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tools" / "fixtures" / "java-corpus.txt"
BASE = "https://repo1.maven.org/maven2"

#: 子进程里跑的那一段：只 import 前端，编译到临时文件。
_CHILD = r"""
import sys, json, tempfile
sys.path.insert(0, %(root)r)
from pathlib import Path
import lomjava
jar = Path(%(jar)r)
try:
    classes = lomjava.load_classes(jar)
except lomjava.JavaError as e:
    print(json.dumps({"phase": "parse", "err": str(e)})); sys.exit(0)
except Exception as e:
    print(json.dumps({"phase": "parse", "err": type(e).__name__ + ": " + str(e)})); sys.exit(0)
try:
    lomjava.verify_classes(classes)
except lomjava.JavaError as e:
    print(json.dumps({"phase": "verify", "err": str(e)})); sys.exit(0)
except Exception as e:
    print(json.dumps({"phase": "verify", "err": type(e).__name__ + ": " + str(e)})); sys.exit(0)
try:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        lomjava.emit("corpus", classes, d / "c.ll", d / "c.lomt")
except lomjava.JavaError as e:
    print(json.dumps({"phase": "lower", "err": str(e)})); sys.exit(0)
except Exception as e:
    print(json.dumps({"phase": "lower", "err": type(e).__name__ + ": " + str(e)})); sys.exit(0)
print(json.dumps({"phase": "ok", "err": "", "classes": len(classes)}))
"""


def load_manifest() -> list[dict]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, group, art, ver, size, sha = line.split()
        rows.append(dict(kind=kind, group=group, art=art, ver=ver,
                         size=int(size), sha=sha))
    return rows


def jar_path(d: Path, row: dict) -> Path:
    return d / "jars" / f"{row['art']}-{row['ver']}.jar"


def fetch(d: Path, rows: list[dict]) -> int:
    (d / "jars").mkdir(parents=True, exist_ok=True)
    bad = 0
    for r in rows:
        p = jar_path(d, r)
        if p.exists() and p.stat().st_size == r["size"] and \
                hashlib.sha256(p.read_bytes()).hexdigest() == r["sha"]:
            continue
        gp = r["group"].replace(".", "/")
        url = f"{BASE}/{gp}/{r['art']}/{r['ver']}/{p.name}"
        print(f"fetch {p.name}", flush=True)
        subprocess.run(["curl", "-sSL", "--max-time", "180", "-o", str(p), url],
                       capture_output=True)
        if not p.exists() or p.stat().st_size == 0:
            p.unlink(missing_ok=True)
            print(f"  MISS {url}")
            bad += 1
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != r["sha"]:
            print(f"  SHA mismatch: {got}")
            bad += 1
    return bad


def scan_one(jar: Path, timeout: int) -> dict:
    code = _CHILD % {"root": str(ROOT / "tools"), "jar": str(jar)}
    try:
        r = subprocess.run([sys.executable, "-I", "-c", code],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"phase": "timeout", "err": f">{timeout}s"}
    out = (r.stdout or "").strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"phase": "crash",
            "err": (r.stderr or "")[-200:].replace("\n", " ")}


def signature(phase: str, err: str) -> str:
    """把第一条错误归一成一个桶 —— 直方图看的是"还差什么"，不是具体哪一行。"""
    if phase in ("ok",):
        return "ok"
    if phase in ("timeout", "crash"):
        return phase
    m = re.search(r"(J00[123]):", err)
    code = m.group(1) if m else "?"
    m2 = re.search(r"opcode `([a-z_]+)`", err)
    if m2:
        return f"{code} opcode {m2.group(1)}"
    for name in ("invokedynamic", "checkcast", "instanceof", "monitorenter",
                 "Float", "double", "float", "reflection", "ClassLoader"):
        if name in err:
            return f"{code} {name}"
    m3 = re.search(r"J003: [^:]*: ([^@]+)", err)
    if m3:
        return f"{code} {m3.group(1).strip()[:60]}"
    return f"{code} other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--json")
    ap.add_argument("--only", help="only jars whose name contains this")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    rows = load_manifest()
    if a.fetch:
        bad = fetch(d, rows)
        if bad:
            print(f"{bad} jar(s) failed to fetch", file=sys.stderr)
    if a.only:
        rows = [r for r in rows if a.only in r["art"]]

    results = []
    hooks = Counter()
    for r in rows:
        p = jar_path(d, r)
        if not p.exists():
            results.append(dict(**r, phase="missing", err="", bucket="missing"))
            hooks["missing"] += 1
            print(f"MISS {r['art']}-{r['ver']}", flush=True)
            continue
        got = scan_one(p, a.timeout)
        bucket = signature(got["phase"], got.get("err", ""))
        hooks[bucket] += 1
        results.append(dict(**r, phase=got["phase"], err=got.get("err", ""),
                            bucket=bucket))
        print(f"{'OK  ' if got['phase'] == 'ok' else 'FAIL'} "
              f"{r['art']}-{r['ver']}  [{got['phase']}] {got.get('err', '')[:110]}",
              flush=True)

    ok = sum(1 for r in results if r["phase"] == "ok")
    print(f"\n{ok}/{len(results)} jars compile through the AOT front end")
    print("failure buckets:")
    for k, n in hooks.most_common():
        if k != "ok":
            print(f"  {n:3d}  {k}")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
