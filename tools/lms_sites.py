#!/usr/bin/env python3
"""lms_sites.py — LMS 站点普查（`docs/363`）。

把一份 IR 里的访存站点逐条点名。站点 ID 与 LVM 的 fault 报站点**同一套坐标**：

    (函数名, 块标签, 块内指令序号)   →   `fn:blk#idx`

等级只有两个（V1）：

* `checked`：同函数里有 g1 形状的检查（`br i1 …, label %*lmsok, label %*lmsbad`），
  且这条访存在 `*lmsok` 块里 —— 原生侧真的会拦它；
* `unsafe`：其余。LVM 的 `sw_cap` 会检查全部访存，但那是**基质**的账，
  不是这张表要冒充的。

用法: python tools/lms_sites.py IN.ll [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402

SCHEMA = 1
INTERNAL = "__loment_"
_BR_OK_BAD = re.compile(r"br i1 .*label %([-\w.$]+), label %([-\w.$]+)")


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_blocks(fn) -> set[str]:
    """`br i1 …, label %*lmsok, label %*lmsbad` 里的 ok 块。"""
    out: set[str] = set()
    for blk in fn.blocks:
        for ins in blk.instrs:
            m = _BR_OK_BAD.search(ins.text)
            if m and "lmsok" in m.group(1) and "lmsbad" in m.group(2):
                out.add(m.group(1))
    return out


def _checked_sites(fn) -> set[tuple[str, int]]:
    """每个 `*lmsok` 块里**第一条**访存才是被检查的那一条。

    检查保护的是紧跟 `getelementptr` 的那条 `load`/`store`；块里后面的访存
    （比如把结果写进局部变量的 `store i32 %t6, ptr %x.addr`）不在检查范围内。
    """
    ok = _checked_blocks(fn)
    out: set[tuple[str, int]] = set()
    for blk in fn.blocks:
        if blk.label not in ok:
            continue
        for idx, ins in enumerate(blk.instrs):
            if ins.text.startswith(("load ", "store ", "atomicrmw ")):
                out.add((blk.label, idx))
                break
    return out


def census(ir_text: str) -> dict:
    _globals, funcs, _externs = lomelf.parse_ll(ir_text)
    sites: list[dict] = []
    for fn in funcs:
        if fn.name.startswith(INTERNAL):
            continue
        checked_set = _checked_sites(fn)
        for blk in fn.blocks:
            for idx, ins in enumerate(blk.instrs):
                text = ins.text
                op = ty = None
                if text.startswith("load "):
                    op = "load"
                    ty, _ = lomelf.parse_type(text[len("load "):])
                elif text.startswith("store "):
                    op = "store"
                    ty, _ = lomelf.parse_type(text[len("store "):])
                elif text.startswith("atomicrmw "):
                    op = "atomicrmw"
                    ty = None
                if op is None:
                    continue
                checked = (blk.label, idx) in checked_set
                site = {
                    "fn": fn.name, "blk": blk.label, "idx": idx,
                    "op": op, "ty": ty,
                    "grade": "checked" if checked else "unsafe",
                }
                if checked:
                    site["fault"] = "F01"
                sites.append(site)
    raw = ir_text.encode("utf-8")
    body = {
        "schema": SCHEMA,
        "source": {"sha256": sha256_hex(raw), "bytes": len(raw)},
        "sites": sites,
        "counts": {
            "sites": len(sites),
            "checked": sum(1 for s in sites if s["grade"] == "checked"),
            "unsafe": sum(1 for s in sites if s["grade"] == "unsafe"),
        },
    }
    body["seal"] = sha256_hex(canonical_bytes(body))
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        text = Path(a.ir).read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 读不了：{e}", file=sys.stderr)
        return 2
    table = census(text)
    out = json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
        c = table["counts"]
        print(f"[LMS] sites -> {a.out} ({c['sites']} site(s), "
              f"{c['checked']} checked, {c['unsafe']} unsafe)")
    else:
        print(out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
