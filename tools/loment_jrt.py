#!/usr/bin/env python3
"""loment_jrt.py — build `loment/lib/jrt/jrt.ll` from `jrt.lomt`.

`jrt` is the minimal Java runtime (docs/281 §7): printing today, the object
model and exceptions in later slices. It is written in Loment and compiled by
the same seed the rest of the toolchain uses, so the artifact is deterministic.

Two edits are made to the compiler's output before it is written:

* the unit's `internal` helpers `__loment_memcmp/memset/memcpy` are **dropped**
  when nothing calls them — every driver-compiled unit carries a copy, and the
  build wrapper concatenates the app IR with `jrt.ll`; two `define internal`
  with the same name is a duplicate definition;
* `__loment_abort` (which jrt does call, for the out-of-memory path) is renamed
  to `__jrt_abort` for the same reason.

Usage:
    python tools/loment_jrt.py --emit    # rewrite loment/lib/jrt/jrt.ll
    python tools/loment_jrt.py --check   # exit 1 if the committed file is stale
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "lib" / "jrt" / "jrt.lomt"
OUT = ROOT / "loment" / "lib" / "jrt" / "jrt.ll"

HEADER = (
    "; 由 tools/loment_jrt.py 从 loment/lib/jrt/jrt.lomt 生成 —— 不要手改\n"
    "; jrt: the minimal Java runtime (docs/281 §7)\n\n"
)

_DROP = ("__loment_memcmp", "__loment_memset", "__loment_memcpy")


def _compile() -> str:
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402

    stage1 = loment_dist.build_stage1()
    r = subprocess.run([str(stage1), str(SRC.relative_to(ROOT))], cwd=str(ROOT),
                       capture_output=True, timeout=300)
    if r.returncode != 0 or not r.stdout:
        raise SystemExit(f"stage1 编 {SRC.name} 失败: {r.stderr[-400:]!r}")
    return r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def _drop_fn(text: str, name: str) -> str:
    """Remove one `define ... @name(...) { ... }` block (brace balanced)."""
    m = re.search(rf"^define\b[^\n]*@{re.escape(name)}\([^\n]*\{{\n", text, re.M)
    if not m:
        return text
    i = m.end()
    depth = 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    # also swallow the blank line that followed the block
    while i < len(text) and text[i] in "\r\n":
        i += 1
    return text[:m.start()] + text[i:]


def render() -> str:
    text = _compile()
    for name in _DROP:
        if not re.search(rf"call\b[^\n]*@{re.escape(name)}\(", text):
            text = _drop_fn(text, name)
    text = text.replace("@__loment_abort", "@__jrt_abort")
    return HEADER + text


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a not in (["--emit"], ["--check"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    text = render()
    if a == ["--emit"]:
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"[OK] {OUT.relative_to(ROOT)} ({len(text)} B)")
        return 0
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        print(f"[ERR] {OUT.relative_to(ROOT)} 过期 —— 跑 python tools/loment_jrt.py --emit",
              file=sys.stderr)
        return 1
    print(f"[OK] {OUT.relative_to(ROOT)} 与 jrt.lomt 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
