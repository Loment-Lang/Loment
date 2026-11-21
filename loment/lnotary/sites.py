"""LNotary · 站点表的独立核对（`docs/363`）。

站点表由 `tools/lms_sites.py` 从 IR 派生；这里只核它的**自洽**与**绑定的字节**，
不重新扫 IR（LNotary 的零工具链 import 是硬约束）。
"""

from __future__ import annotations

import hashlib
import json

from .verify import Error

SCHEMA = 1
OPS = frozenset({"load", "store", "atomicrmw"})
GRADES = frozenset({"checked", "unsafe"})
FAULTS = frozenset({"F01", "F02", "F03"})


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _err(out: list[Error], code: str, msg: str) -> None:
    out.append(Error(code, msg))


def verify_sites(table: object, ir_bytes: bytes | None = None) -> list[Error]:
    out: list[Error] = []
    if not isinstance(table, dict):
        _err(out, "LNT-SITES", f"站点表必须是对象，得到 {type(table).__name__}")
        return out
    if table.get("schema") != SCHEMA:
        _err(out, "LNT-SITES", f"站点表 schema 必须是 {SCHEMA}，得到 {table.get('schema')!r}")
    src = table.get("source")
    if not isinstance(src, dict):
        _err(out, "LNT-SITES", "站点表缺少 source {sha256, bytes}")
    else:
        if not isinstance(src.get("sha256"), str) or not isinstance(src.get("bytes"), int):
            _err(out, "LNT-SITES", "source 必须同时有 sha256（字符串）与 bytes（整数）")
        elif ir_bytes is not None:
            want = sha256_hex(ir_bytes)
            if src["sha256"] != want:
                _err(out, "LNT-SITES", f"source.sha256 是 {src['sha256']!r}，实际是 {want!r}")
            if src["bytes"] != len(ir_bytes):
                _err(out, "LNT-SITES",
                     f"source.bytes 是 {src['bytes']!r}，实际是 {len(ir_bytes)}")
    sites = table.get("sites")
    if not isinstance(sites, list):
        _err(out, "LNT-SITES", "站点表的 sites 必须是数组")
        return out
    seen: set[tuple[str, str, int]] = set()
    checked = unsafe = 0
    for s in sites:
        if (not isinstance(s, dict)
                or not isinstance(s.get("fn"), str)
                or not isinstance(s.get("blk"), str)
                or not isinstance(s.get("idx"), int)
                or isinstance(s.get("idx"), bool)):
            _err(out, "LNT-SITES", f"站点必须是 {{fn, blk, idx, op, ty, grade}}，得到 {s!r}")
            continue
        key = (s["fn"], s["blk"], s["idx"])
        if key in seen:
            _err(out, "LNT-SITES", f"站点 {key} 出现了两次")
        seen.add(key)
        if s.get("op") not in OPS:
            _err(out, "LNT-SITES", f"{key} 的 op 是 {s.get('op')!r}（只有 {sorted(OPS)}）")
        if s.get("grade") not in GRADES:
            _err(out, "LNT-SITES", f"{key} 的 grade 是 {s.get('grade')!r}（只有 {sorted(GRADES)}）")
        elif s["grade"] == "checked":
            checked += 1
            if s.get("fault") not in FAULTS:
                _err(out, "LNT-SITES", f"{key} 是 checked，但没有合法的 fault")
        else:
            unsafe += 1
    counts = table.get("counts")
    if not isinstance(counts, dict):
        _err(out, "LNT-SITES", "站点表缺少 counts")
    else:
        want = {"sites": len(sites), "checked": checked, "unsafe": unsafe}
        for k, v in want.items():
            if counts.get(k) != v:
                _err(out, "LNT-SITES", f"counts.{k} 是 {counts.get(k)!r}，应为 {v}")
    body = {k: v for k, v in table.items() if k != "seal"}
    want_seal = sha256_hex(canonical_bytes(body))
    if table.get("seal") != want_seal:
        _err(out, "LNT-SEAL", f"seal 是 {table.get('seal')!r}，重算是 {want_seal!r}")
    return out
