#!/usr/bin/env python3
# loment_hotart.py — 把**投影树**编成热更新件（`#1155` 缺的那一步）。
#
# 投影（`#1156`）给的是一棵树与一份清单；固定核（`#1151`/`#1152`）吃的是一个扁平的
# 装载件。这个文件补上中间那一段：
#
#   旧树  --(hotswap 档)-->  旧 ELF + 旧 .lomhot
#   投影树 --(hotswap 档)-->  新 ELF + 新 .lomhot + 新文本
#   两份 .lomhot + 新文本 --> 装载件（`loment_hotload.build_blob`）
#
# 产物目录里放 `candidate.hot`（装载件本身）、`hotfix.json`（`#1153` 的清单，artifact
# 指向装载件）与 `SHA256SUMS`。签名仍由发行流程做（`#1153`）。
#
# **从投影树编，不从 R 编** —— 否则功能又跟着进来了。

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_hotfix as hf          # noqa: E402
import loment_hotload as hl         # noqa: E402
import loment_update as lu          # noqa: E402


def compile_hotswap(tree: Path, entry: str) -> tuple[bytes, str, bytes]:
    """在一棵树里把 `entry` 编成 hotswap 档：返回 (ELF, `.lomhot` 文本, 文本段)。"""
    import lomentc
    import lomelf

    root = tree.resolve()
    p = tree / entry
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, root, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise SystemExit(f"{entry}: {errs[0]}")
    ir = lomentc.emit_llvm(mod, root, deps)
    blob, info = lomelf.compile_ll(ir, hotswap=True)
    meta = hl.parse(info["hot"])
    return blob, info["hot"], hl.text_of(blob, meta)


def package(old_tree: Path, new_tree: Path, release: str, out: Path, *,
            entry: str, projection: dict | None = None) -> Path | None:
    """打一份热更新件。投影为空（没有 fix）时返回 `None` —— 不产生产物。"""
    if projection is not None and not projection.get("line"):
        return None
    _ob, old_meta_text, _ot = compile_hotswap(old_tree, entry)
    _nb, new_meta_text, new_text = compile_hotswap(new_tree, entry)
    old_meta = hl.parse(old_meta_text)
    new_meta = hl.parse(new_meta_text)
    blob = hl.build_blob(old_meta, new_meta, new_text)

    out.mkdir(parents=True, exist_ok=True)
    cand = out / "candidate.hot"
    cand.write_bytes(blob)
    man = hf.build(new_tree, release, projection=projection)
    man["artifact"] = {"name": cand.name, "sha256": lu.sha256_file(cand)}
    (out / "hotfix.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8", newline="\n")
    (out / "SHA256SUMS").write_text(f"{man['artifact']['sha256']}  {cand.name}\n",
                                    encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="loment_hotart")
    ap.add_argument("--old", required=True, help="跑着的那棵树（基线）")
    ap.add_argument("--new", required=True, help="投影出来的树")
    ap.add_argument("--entry", required=True, help="程序入口（相对树的 .lomt 路径）")
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    got = package(Path(args.old).resolve(), Path(args.new).resolve(), args.release,
                  Path(args.out), entry=args.entry)
    if got is None:
        print("[SKIP] 投影为空 —— 不产生热更新件")
        return 0
    print(f"[OK] {got}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
