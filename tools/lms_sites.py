#!/usr/bin/env python3
"""lms_sites.py — LMS 站点普查（`docs/363` / `docs/370` V4）。

把一份 IR 里的访存站点逐条点名。站点 ID 与 LVM 的 fault 报站点**同一套坐标**：

    (函数名, 块标签, 块内指令序号)   →   `fn:blk#idx`

等级三种：

* `proven`：证明落在站点上（`rule`）。今天落三条：
  * **R1**：常量下标 + 常量长度，字节偏移 + 宽度 ≤ 对象大小；
  * **R2**：规范循环 —— 真后继块里有 `icmp ult 槽, N`、GEP 用同一个槽、
    块末尾 `+1` 写回同一个槽并跳回条件块，且数组长度 == N；
  * **R3**：支配分支 —— GEP 在条件块的**唯一**真后继里，条件 `icmp ult 槽, N`、
    GEP 用同一个槽，且数组长度 == N；
* `checked`：同函数里有 g1 形状的检查（`br i1 …, label %*lmsok, label %*lmsbad`），
  且这条访存在 `*lmsok` 块里；
* `unsafe`：其余。

R4（生命周期 + `gc_ladder` 交叉）要另一份产物，尚未落（`docs/370` §7 第 8 条）。

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
_BR_COND = re.compile(r"br i1 (%[-\w.$]+), label %([-\w.$]+), label %([-\w.$]+)")
_BR_ONE = re.compile(r"br label %([-\w.$]+)")
_ICMP_ULT = re.compile(r"icmp ult i32 (%[-\w.$]+), (\d+)$")


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_blocks(fn) -> set[str]:
    out: set[str] = set()
    for blk in fn.blocks:
        for ins in blk.instrs:
            m = _BR_OK_BAD.search(ins.text)
            if m and "lmsok" in m.group(1) and "lmsbad" in m.group(2):
                out.add(m.group(1))
    return out


def _checked_sites(fn) -> set[tuple[str, int]]:
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


def _const_int(s: str) -> int | None:
    s = s.strip()
    if s.startswith("-"):
        return -int(s[1:]) if s[1:].isdigit() else None
    return int(s) if s.isdigit() else None


def _ptr_name(op: str) -> str | None:
    s = op.strip()
    if s.startswith("ptr "):
        s = s[4:].strip()
    return s if s.startswith("%") else None


def _gep_parts(text: str) -> tuple[str, str | None, list[object]] | None:
    body = text[len("getelementptr"):].strip()
    if body.startswith("inbounds"):
        body = body[len("inbounds"):].strip()
    ty, i = lomelf.parse_type(body)
    ops = lomelf.split_top(body[i:].lstrip(", ").strip())
    if not ops:
        return None
    base = _ptr_name(ops[0])
    vals: list[object] = []
    for op in ops[1:]:
        _t, j = lomelf.parse_type(op)
        s = op[j:].strip()
        v = _const_int(s)
        vals.append(v if v is not None else s)
    return ty, base, vals


def _gep_info(text: str, offsets: dict[str, tuple[str, int]]) -> tuple[str, int] | None:
    parts = _gep_parts(text)
    if parts is None:
        return None
    ty, base, vals = parts
    if base is None or base not in offsets or not vals:
        return None
    if any(not isinstance(v, int) for v in vals):
        return None
    root, boff = offsets[base]
    idxs = [int(v) for v in vals]
    if ty.startswith("["):
        n, elem = lomelf.array_parts(ty)
        esz = lomelf.size_of(elem)
        if len(idxs) >= 2:
            return root, boff + idxs[0] * n * esz + idxs[1] * esz
        return root, boff + idxs[0] * esz
    if ty.startswith("%"):
        if len(idxs) >= 2:
            off, _fty = lomelf.field_offset(ty, idxs[1])
            return root, boff + off
        return None
    return root, boff + idxs[0] * lomelf.size_of(ty)


def _access_ptr(text: str, op: str) -> str | None:
    if op == "load":
        rest = text[len("load "):].lstrip()
        _t, j = lomelf.parse_type(rest)
        return rest[j:].lstrip(", ").strip()
    if op == "store":
        rest = text[len("store "):].lstrip()
        _t, j = lomelf.parse_type(rest)
        for part in reversed(lomelf.split_top(rest[j:].lstrip(", ").strip())):
            if part.strip().startswith("ptr "):
                return part.strip()
    return None


def _facts(fn):
    """`loads`、`cmp`、`brs`、`preds` —— 一次扫，后面按块查。"""
    loads: dict[str, str] = {}
    cmp: dict[str, tuple[str, int]] = {}
    brs: dict[str, tuple[str, str, str]] = {}
    preds: dict[str, list[str]] = {b.label: [] for b in fn.blocks}
    for blk in fn.blocks:
        for ins in blk.instrs:
            t = ins.text
            if t.startswith("load ") and ins.dest:
                p = _access_ptr(t, "load")
                name = _ptr_name(p) if p else None
                if name is not None:
                    loads[ins.dest] = name
            if ins.dest:
                m = _ICMP_ULT.match(t)
                if m:
                    cmp[ins.dest] = (m.group(1), int(m.group(2)))
            m = _BR_COND.match(t)
            if m:
                brs[blk.label] = (m.group(1), m.group(2), m.group(3))
                preds[m.group(2)].append(blk.label)
                preds[m.group(3)].append(blk.label)
                continue
            m = _BR_ONE.match(t)
            if m:
                preds[m.group(1)].append(blk.label)
    return loads, cmp, brs, preds


def _origin(v: object, loads: dict[str, str]) -> tuple[str, str]:
    s = str(v)
    if s in loads:
        return "slot", loads[s]
    return "ssa", s


def _store_before(blk, slot: str, upto: int) -> bool:
    for ins in blk.instrs[:upto]:
        if ins.text.startswith("store ") and f"ptr {slot}" in ins.text:
            return True
    return False


def _has_incr(blk, slot: str, loads: dict[str, str]) -> bool:
    for ins in blk.instrs:
        if ins.text.startswith("add i32 ") and ins.text.endswith(", 1"):
            lhs = lomelf.split_top(ins.text[len("add i32 "):])[0].strip()
            if loads.get(lhs) == slot:
                return True
    return False


def _dyn_gep_rule(blk, idx: int, ty: str, base: str | None, vals: list[object],
                  loads, cmp, brs, preds) -> str | None:
    """R2 / R3：GEP 的动态下标有支配它的 `icmp ult`。"""
    if not ty.startswith("[") or base is None:
        return None
    dyn = [v for v in vals if not isinstance(v, int)]
    if len(dyn) != 1:
        return None
    n, _elem = lomelf.array_parts(ty)
    mypred = preds.get(blk.label, [])
    if len(mypred) != 1:
        return None
    p = mypred[0]
    br = brs.get(p)
    if br is None or br[1] != blk.label:
        return None
    cname = br[0]
    if cname not in cmp:
        return None
    lhs, k = cmp[cname]
    if k != n:
        return None
    if _origin(dyn[0], loads) != _origin(lhs, loads):
        return None
    kind, name = _origin(dyn[0], loads)
    if kind == "slot" and _store_before(blk, name, idx):
        return None
    # R2：块末尾跳回条件块，且同一个槽 +1 写回。
    term = blk.instrs[-1].text if blk.instrs else ""
    if term == f"br label %{p}" and kind == "slot" and _has_incr(blk, name, loads):
        return "R2"
    return "R3"


def census(ir_text: str) -> dict:
    _globals, funcs, _externs = lomelf.parse_ll(ir_text)
    sites: list[dict] = []
    for fn in funcs:
        if fn.name.startswith(INTERNAL):
            continue
        checked_set = _checked_sites(fn)
        loads, cmp, brs, preds = _facts(fn)
        sizes: dict[str, int] = {}
        offsets: dict[str, tuple[str, int]] = {}
        gep_rule: dict[str, str] = {}
        for blk in fn.blocks:
            for idx, ins in enumerate(blk.instrs):
                text = ins.text
                if text.startswith("alloca ") and ins.dest:
                    ty, _ = lomelf.parse_type(text[len("alloca "):])
                    sizes[ins.dest] = lomelf.size_of(ty)
                    offsets[ins.dest] = (ins.dest, 0)
                    continue
                if text.startswith("getelementptr") and ins.dest:
                    info = _gep_info(text, offsets)
                    if info is not None:
                        offsets[ins.dest] = info
                    else:
                        parts = _gep_parts(text)
                        if parts is not None:
                            ty, base, vals = parts
                            rule = _dyn_gep_rule(blk, idx, ty, base, vals,
                                                 loads, cmp, brs, preds)
                            if rule is not None:
                                gep_rule[ins.dest] = rule
                    continue
                op = ty = None
                if text.startswith("load "):
                    op = "load"
                    ty, _ = lomelf.parse_type(text[len("load "):])
                elif text.startswith("store "):
                    op = "store"
                    ty, _ = lomelf.parse_type(text[len("store "):])
                elif text.startswith("atomicrmw "):
                    op = "atomicrmw"
                if op is None:
                    continue
                proven_rule = None
                ptr_text = _access_ptr(text, op)
                if ptr_text is not None:
                    pname = _ptr_name(ptr_text)
                    if pname in gep_rule:
                        proven_rule = gep_rule[pname]
                if proven_rule is None and ptr_text is not None and ty is not None:
                    pname = _ptr_name(ptr_text)
                    if pname is not None and pname in offsets:
                        root, off = offsets[pname]
                        width = lomelf.size_of(ty)
                        if root in sizes and off + width <= sizes[root]:
                            proven_rule = "R1"
                checked = (blk.label, idx) in checked_set
                site = {
                    "fn": fn.name, "blk": blk.label, "idx": idx,
                    "op": op, "ty": ty,
                    "grade": ("proven" if proven_rule
                              else "checked" if checked else "unsafe"),
                }
                if proven_rule:
                    site["rule"] = proven_rule
                elif checked:
                    site["fault"] = "F01"
                sites.append(site)
    raw = ir_text.encode("utf-8")
    body = {
        "schema": SCHEMA,
        "source": {"sha256": sha256_hex(raw), "bytes": len(raw)},
        "sites": sites,
        "counts": {
            "sites": len(sites),
            "proven": sum(1 for s in sites if s["grade"] == "proven"),
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
              f"{c['proven']} proven, {c['checked']} checked, {c['unsafe']} unsafe)")
    else:
        print(out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
