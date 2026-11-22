"""LNotary · 站点表的独立核对（`docs/363` / `docs/370` V3）。

站点表由 `tools/lms_sites.py` 从 IR 派生；这里只核它的**自洽**与**绑定的字节**，
不重新扫 IR（LNotary 的零工具链 import 是硬约束）。

V3 起多一条**对账**（`verify_fault_coverage`）：`checked` 站点的坏块出口
（`fault` / `abort` / `skip`）必须与产物自己声明的开关一致 ——
盘古开 ⇒ `fault`；否则拒绝器开 ⇒ `skip`；否则 `abort`。
"""

from __future__ import annotations

import hashlib
import json

from .verify import Error

SCHEMA = 2
OPS = frozenset({"load", "store", "atomicrmw"})
GRADES = frozenset({"proven", "checked", "unsafe"})
RULES = frozenset({"R1", "R2", "R3", "R4"})
FAULTS = frozenset({"F01", "F02", "F03"})
#: `checked` 的坏块实际做了什么（`docs/356` §5.2 / `docs/363`）：结构化 fault、
#: `__loment_abort`（SIGILL）、还是走拒绝器的 join（跳过那一条访存）。
EDGES = frozenset({"fault", "abort", "skip"})


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
    proven = checked = unsafe = 0
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
        elif s["grade"] == "proven":
            proven += 1
            if s.get("rule") not in RULES:
                _err(out, "LNT-SITES",
                     f"{key} 是 proven，但 rule 不在 {sorted(RULES)}（得到 {s.get('rule')!r}）")
            if "edge" in s or "fault" in s:
                _err(out, "LNT-SITES", f"{key} 是 proven，不该有 edge/fault（没有检查边）")
        elif s["grade"] == "checked":
            checked += 1
            edge = s.get("edge")
            if edge not in EDGES:
                _err(out, "LNT-SITES",
                     f"{key} 是 checked，但没有合法的 edge（只有 {sorted(EDGES)}）")
            elif edge == "fault":
                if s.get("fault") not in FAULTS:
                    _err(out, "LNT-SITES",
                         f"{key} 的 edge 是 fault，但没有合法的 fault")
            elif "fault" in s:
                _err(out, "LNT-SITES",
                     f"{key} 的 edge 是 {edge!r}，不该同时声称 fault")
        else:
            unsafe += 1
            if "edge" in s or "fault" in s:
                _err(out, "LNT-SITES",
                     f"{key} 是 unsafe，不该有 edge/fault")
    counts = table.get("counts")
    if not isinstance(counts, dict):
        _err(out, "LNT-SITES", "站点表缺少 counts")
    else:
        want = {"sites": len(sites), "proven": proven,
                "checked": checked, "unsafe": unsafe}
        for k, v in want.items():
            if counts.get(k) != v:
                _err(out, "LNT-SITES", f"counts.{k} 是 {counts.get(k)!r}，应为 {v}")
    body = {k: v for k, v in table.items() if k != "seal"}
    want_seal = sha256_hex(canonical_bytes(body))
    if table.get("seal") != want_seal:
        _err(out, "LNT-SEAL", f"seal 是 {table.get('seal')!r}，重算是 {want_seal!r}")
    return out


def _pangu_on(doc: dict) -> bool:
    for item in doc.get("switches") or []:
        if (isinstance(item, dict) and item.get("name") == "lms_pangu_true"
                and item.get("on") is True):
            return True
    return False


def expected_edge(doc: dict) -> str:
    """**产物自己的开关决定坏块该做什么**（`docs/363` §6.5 的 g1 出口）。

    与参考实现的 `_lms_fatal` / 拒绝器同一张表：盘古开 ⇒ fault；
    否则拒绝器开 ⇒ 跳过那一条访存；否则 `__loment_abort`（SIGILL）。
    """
    if _pangu_on(doc):
        return "fault"
    levels = doc.get("lms_levels") or {}
    if levels.get("lms_reject") is True:
        return "skip"
    return "abort"


def verify_fault_coverage(table: object, doc: dict) -> list[Error]:
    """站点表的 fault 账必须与产物自己的开关一致（`docs/370` V3 的第一半）。

    这是**两份账的对账**：表说"越界时 fault/skip/abort"，产物说"这些级开着"。
    任一边被改都会红。它不证明 IR 真的是这么编的（那是 `lms_sites` 的活），
    但能挡住"表与产物各说各的"——包括只改了 Potato 打印字段的那种篡改。
    """
    out: list[Error] = []
    if not isinstance(table, dict) or not isinstance(table.get("sites"), list):
        return out
    checked = [s for s in table["sites"]
               if isinstance(s, dict) and s.get("grade") == "checked"]
    if not checked:
        return out
    levels = doc.get("lms_levels")
    if not isinstance(levels, dict) or levels.get("lms_guard") is not True:
        _err(out, "LNT-FAULT",
             "站点表声称有 checked 站点，但产物的 lms_levels 没有把 lms_guard 打开")
        return out
    want = expected_edge(doc)
    for s in checked:
        key = f"{s.get('fn')}:{s.get('blk')}#{s.get('idx')}"
        if s.get("edge") != want:
            _err(out, "LNT-FAULT",
                 f"{key} 的坏块出口是 {s.get('edge')!r}，按产物开关应为 {want!r}")
    return out
