#!/usr/bin/env python3
"""loment_java_pkg.py — publish a jar as a lompi foreign package (`kind java`).

The AOT compile happens **here**, on the publisher's side. The package ships the
compiled IR, the Loment interface and `link.txt`, so `lompi install` only has to
copy and verify them: the consumer needs neither a JDK nor the compiler
(docs/283 §4–§6).

    python tools/loment_java_pkg.py --jar shapelib.jar --name shapelib \\
        --version 1.0.0 --out store

produces `store/shapelib/1.0.0/` with:

    shapelib.lomt      interface (the `pub fn` surface the app calls)
    shapelib.ll        the IR the build wrapper links
    lib.jar            the original archive (kept: it is part of the identity)
    link.txt           `ir shapelib.ll <sha>` + `runtime jrt 0.1.0`
    foreign.lomp       the manifest
    foreign.txt        the artifact list with hashes
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomjava  # noqa: E402

#: 运行期版本 —— 与 `tools/loment_dist.py` 发在 `share/loment/jrt/` 下的那个一致。
JRT_VERSION = "0.1.0"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package(jar: Path, name: str, version: str, out: Path,
                  jrt_version: str = JRT_VERSION) -> Path:
    pkg = out / name / version
    pkg.mkdir(parents=True, exist_ok=True)
    classes = lomjava.load_classes(jar)
    lomjava.emit(name, classes, pkg / f"{name}.ll", pkg / f"{name}.lomt")
    shutil.copyfile(jar, pkg / "lib.jar")

    (pkg / "foreign.lomp").write_text(
        "module foreign\n\n"
        f'pub fn name() -> str {{ return "{name}"; }}\n'
        f'pub fn version() -> str {{ return "{version}"; }}\n'
        'pub fn kind() -> str { return "java"; }\n',
        encoding="utf-8", newline="\n")

    (pkg / "foreign.txt").write_text(
        "# lompi foreign v1\nkind java\n"
        f"artifact jar lib.jar {_sha(pkg / 'lib.jar')}\n"
        f"artifact interface {name}.lomt {_sha(pkg / f'{name}.lomt')}\n"
        f"artifact ir {name}.ll {_sha(pkg / f'{name}.ll')}\n",
        encoding="utf-8", newline="\n")

    (pkg / "link.txt").write_text(
        f"# build-time discovery point (docs/283 sec 6.1)\n"
        f"ir {name}.ll {_sha(pkg / f'{name}.ll')}\n"
        f"runtime jrt {jrt_version}\n",
        encoding="utf-8", newline="\n")
    return pkg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jar", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jrt", default=JRT_VERSION)
    a = ap.parse_args(argv)
    try:
        pkg = build_package(Path(a.jar), a.name, a.version, Path(a.out), a.jrt)
    except (lomjava.JavaError, OSError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    print(f"[OK] {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
