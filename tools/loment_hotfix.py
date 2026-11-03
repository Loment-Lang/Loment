#!/usr/bin/env python3
# loment_hotfix.py — 每个 Pre-Release 都带一份 `hotfix.json`（`#1153`）。
#
# 清单**从树里算出来，从不手写**：契约摘要、语言版本、能力清单、接口面、CLI 表、
# 基线、固定核，再加投影（`#1156`）与预发布标志。它是 `loment_update.check`（`#1147`）
# 的另一半 —— 两个实现必须逐字段同名同编码，所以字段名只在这个文件里定义一次。
#
# 它描述的是**投影出来的构建**（`#1156`），不是发行二进制；发行二进制是完整安装，
# 永远不会被热装。没有 fix 提交的发布不产生热更新件，`artifact` 就留空。

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = 1

CONTRACT_GLOB = "lom/*.lom"
CAPABILITY_GLOBS = ("loment/tools/*.lomt", "loment/selfhost/*.lomt")
FIXED_CORE_LIST = "loment/hotswap/fixed.txt"
CLI_TABLE = "loment/tools/lomcli.lomt"

_CAP_RE = re.compile(r"^\s*capability\s+(.+?)\s*$")
_RELEASE_RE = re.compile(r'^RELEASE\s*=\s*"([^"]+)"', re.M)
_ROW_RE = re.compile(r'row\(mem,\s*fd,\s*"([^"]*)",\s*"([^"]*)"')
_KW_RE = re.compile(r'eq_cstr\(s,\s*n,\s*"([^"]*)"\)')
_DIAG_RE = re.compile(r'\bE(\d{3})\b')


def digest_file(path: Path) -> str:
    """**sha256**，不是 `#1153` 正文写的 blake2b。

    准入判据要由**工具链自己**在进程里算（`loment/tools/update.lomt`），而工具链里只有
    `sha256.lomt`。选一个它算不出来的算法，等于把准入判据锁在 Python 里 —— 那正好是
    `docs/278` §2 拒绝的形状。差异记在 `docs/283` §2。
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _rel_files(tree: Path, pattern: str) -> list[Path]:
    return sorted(p for p in tree.glob(pattern) if p.is_file())


def read_release(tree: Path) -> str:
    text = (tree / "tools" / "loment_release.py").read_text(encoding="utf-8")
    m = _RELEASE_RE.search(text)
    if not m:
        raise SystemExit(f"{tree}/tools/loment_release.py 里没有 RELEASE")
    return m.group(1)


def contract_digests(tree: Path) -> dict[str, str]:
    """`lom/*.lom` 逐文件的 sha256 —— 跨线契约的字节真身。"""
    return {p.relative_to(tree).as_posix(): digest_file(p)
            for p in _rel_files(tree, CONTRACT_GLOB)}


def capability_manifest(tree: Path) -> list[str]:
    caps: set[str] = set()
    for pattern in CAPABILITY_GLOBS:
        for p in _rel_files(tree, pattern):
            for line in p.read_text(encoding="utf-8").splitlines():
                m = _CAP_RE.match(line)
                if m:
                    caps.add(m.group(1))
    return sorted(caps)


def fixed_core_files(tree: Path) -> list[str]:
    listing = tree / FIXED_CORE_LIST
    if not listing.is_file():
        return []
    out: list[str] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def fixed_core_digest(tree: Path) -> str:
    h = hashlib.sha256()
    for rel in fixed_core_files(tree):
        p = tree / rel
        if not p.is_file():
            raise SystemExit(f"固定核清单里的 {rel} 不在树里")
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii"))
    return h.hexdigest()


def surface(tree: Path) -> dict[str, str]:
    """接口面的三个摘要：关键词、语法形态、诊断码。

    来源都是**工具链自己发出去的那一份**：关键词与语法形态在 `lomcli.lomt`（`loment
    keywords` / `loment syntax` 打的就是它），诊断码在 `lomentc.py`。改一个词、加一条
    语法、加一个码，这里都会动 —— 一个被标成 `bug` 的功能因此过不了这关。
    """
    cli_text = (tree / CLI_TABLE).read_text(encoding="utf-8")
    keywords = sorted(set(_KW_RE.findall(cli_text)))
    diagnostics = sorted({"E" + m for m in _DIAG_RE.findall(
        (tree / "tools" / "lomentc.py").read_text(encoding="utf-8"))})
    syntax = sorted(_ROW_RE.findall(cli_text))          # 命令的用法串就是语法形态表
    return {
        "keywords": _digest(keywords),
        "syntax": _digest([f"{a}|{b}" for a, b in syntax]),
        "diagnostics": _digest(diagnostics),
    }


def cli_digest(tree: Path) -> str:
    """CLI 命令表：命令名 + 用法串，按名字排序。"""
    cli_text = (tree / CLI_TABLE).read_text(encoding="utf-8")
    rows = sorted(_ROW_RE.findall(cli_text))
    return _digest([f"{name}|{args}" for name, args in rows])


def build(tree: Path, release: str, *, projection: dict | None = None) -> dict:
    #: 基线是 `<major>.<minor>.<patch>`，**从 `RELEASE` 读**，不从 tag 猜 —— tag 上
    #: 还挂着 `-hot.N` 那一截，猜出来的东西与 `loment_update` 那边的基线不是同一个量。
    base = read_release(tree)
    m = re.match(r"^v(\d+\.\d+\.\d+)", release)
    if not m:
        raise SystemExit(f"release tag 不是 v<major>.<minor>.<patch>[-...]：{release}")
    if m.group(1) != base:
        raise SystemExit(
            f"tag 说基线 {m.group(1)}，树里 RELEASE 是 {base} —— 一份清单不能同时属于两条基线")
    return {
        "schema": SCHEMA,
        "release": release,
        "prerelease": "-" in release,
        "baseline": base,
        "language": read_release(tree),
        "contract": contract_digests(tree),
        "capabilities": capability_manifest(tree),
        "surface": surface(tree),
        "cli": cli_digest(tree),
        "fixed_core": fixed_core_digest(tree),
        "functions": [],
        "projection": projection or {"baseline": base, "line": [], "evicted": [], "tree": None},
        "artifact": {},
    }


def write(tree: Path, release: str, out: Path, *, projection: dict | None = None) -> Path:
    man = build(tree, release, projection=projection)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="loment_hotfix")
    ap.add_argument("--tree", default=".", help="读哪棵树（默认当前目录）")
    ap.add_argument("--release", default=None, help="发布 tag（默认 v<RELEASE>）")
    ap.add_argument("--out", default=None, help="写到哪（默认 loment/dist/hotfix.json）")
    args = ap.parse_args(argv)
    tree = Path(args.tree).resolve()
    release = args.release or f"v{read_release(tree)}"
    out = Path(args.out) if args.out else tree / "loment" / "dist" / "hotfix.json"
    write(tree, release, out)
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
