#!/usr/bin/env python3
# trust_kernel.py — R0–R5 玩具内核 (docs/379)
#
# 它只做一件事: 检查一份 `(artifact, derivation, conclusion)` 是否按 13 条规则成立。
# **零工具链 import** —— 不 import 编译器、LSPE、LNotary、Potato。它自带：
#
#   * 一个玩具源语言解析器（所以 PARSE 不是"编译器说什么就是什么"）；
#   * 一个玩具栈机解释器（所以 SEM 不是查表）；
#   * 一个静态栈检查器（所以 SAFE 不是自由标签）；
#   * 一条 REFINE 规则（所以热更新的「只减不增」不是自由标签）。
#
# 用法:
#   python trust_kernel.py verify --artifact A --derivation D [--conclusion C]
#
# 退出码: 0 = 接受 / 1 = 拒绝 / 2 = 读不了。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

FORMAT = 1
OPS = {"PUSH": 1, "ADD": 2, "MUL": 3, "PRINT": 4, "HALT": 5}
OP_NAMES = {v: k for k, v in OPS.items()}
ARITY = {"PUSH": 1, "ADD": 0, "MUL": 0, "PRINT": 0, "HALT": 0}
MAX_DEPTH = 64
CAPS = {"PRINT": {"print"}}

#: LNotary / Potato 的 V0 不变量（R3）。内核独立核这些，不 import LNotary。
LMS_LEVELS = ("lms_check", "lms_manage", "lms_guard", "lms_reject",
              "lms_crash", "lms_restart", "lms_seed")
BUDGETS = frozenset({"none", "instructions", "state", "outside"})
DIMS = ("mode", "gc", "runtime", "port", "vm", "lms", "lifetime", "fp", "lvm")
LAWS = frozenset({"no_runtime", "lms_full", "lifetime_auto"})
SITE_OPS = frozenset({"load", "store", "atomicrmw"})
SITE_GRADES = frozenset({"proven", "checked", "unsafe"})
SITE_RULES = frozenset({"R1", "R2", "R3", "R4"})
SITE_FAULTS = frozenset({"F01", "F02", "F03"})
SITE_EDGES = frozenset({"fault", "abort", "skip"})

#: 规则表。改一条就换哈希；推导里的 `kernel.rules_sha256` 随之失效。
RULES = ("AX-K", "AX-S", "PARSE", "TV-L1", "TV-L2", "LINK",
         "SAFE", "CAP", "SEM", "COMPOSE", "PROV", "REPRO", "REFINE")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


RULES_SHA256 = _sha(_canon({"format": FORMAT, "rules": list(RULES)}))


class Reject(Exception):
    """一条可读的拒绝理由。"""


def _hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def parse_toy(text: str) -> list[dict]:
    """玩具源语言: 一行一条 `OP [ARG]`，`#` 起注释。"""
    out: list[dict] = []
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        op = parts[0].upper()
        if op not in OPS:
            raise Reject(f"line {ln}: unknown op {parts[0]!r}")
        if len(parts) > 2:
            raise Reject(f"line {ln}: too many fields")
        if ARITY[op] == 0:
            if len(parts) != 1:
                raise Reject(f"line {ln}: {op} takes no argument")
            arg = 0
        else:
            if len(parts) != 2:
                raise Reject(f"line {ln}: {op} needs one argument")
            try:
                arg = int(parts[1], 10)
            except ValueError:
                raise Reject(f"line {ln}: argument is not an integer") from None
            if not 0 <= arg <= 255:
                raise Reject(f"line {ln}: argument out of byte range")
        out.append({"op": op, "arg": arg})
    if not out:
        raise Reject("empty source")
    if out[-1]["op"] != "HALT":
        raise Reject("last instruction must be HALT")
    return out


def _encode(ast: list[dict]) -> bytes:
    return b"".join(bytes([OPS[x["op"]], x["arg"]]) for x in ast)


def _decode_unit(b: bytes) -> list[dict]:
    if len(b) % 2 != 0:
        raise Reject("unit byte length is not a multiple of 2")
    out = []
    for i in range(0, len(b), 2):
        op, arg = b[i], b[i + 1]
        name = OP_NAMES.get(op)
        if name is None:
            raise Reject(f"unknown opcode {op}")
        if ARITY[name] == 0 and arg != 0:
            raise Reject(f"{name} carries a non-zero operand")
        out.append({"op": name, "arg": arg})
    if not out or out[-1]["op"] != "HALT":
        raise Reject("unit must end with HALT")
    return out


def decode_artifact(artifact: bytes) -> list[list[dict]]:
    """玩具产物是一个链接容器: `{"format":"toy/1","units":[...]}`。"""
    try:
        doc = json.loads(artifact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Reject(f"artifact is not JSON: {e}") from None
    if doc.get("format") != "toy/1":
        raise Reject("artifact format is not toy/1")
    units = doc.get("units")
    if not isinstance(units, list) or not units:
        raise Reject("artifact has no units")
    out = []
    for i, u in enumerate(units):
        if not isinstance(u, dict) or not isinstance(u.get("hex"), str):
            raise Reject(f"unit {i} is malformed")
        try:
            b = bytes.fromhex(u["hex"])
        except ValueError:
            raise Reject(f"unit {i} is not hex") from None
        if u.get("sha256") != _sha(b):
            raise Reject(f"unit {i} sha256 mismatch")
        out.append(_decode_unit(b))
    return out


def static_check(unit: list[dict]) -> int:
    depth = 0
    peak = 0
    for ins in unit:
        op = ins["op"]
        if op == "PUSH":
            depth += 1
        elif op in ("ADD", "MUL"):
            if depth < 2:
                raise Reject(f"{op} underflows the stack")
            depth -= 1
        elif op == "PRINT":
            if depth < 1:
                raise Reject("PRINT underflows the stack")
            depth -= 1
        elif op == "HALT":
            if depth != 0:
                raise Reject("HALT with a non-empty stack")
        peak = max(peak, depth)
        if peak > MAX_DEPTH:
            raise Reject(f"stack depth exceeds {MAX_DEPTH}")
    return peak


def run_unit(unit: list[dict]) -> tuple[list[int], int]:
    stack: list[int] = []
    out: list[int] = []
    for ins in unit:
        op = ins["op"]
        if op == "PUSH":
            stack.append(ins["arg"])
        elif op in ("ADD", "MUL"):
            if len(stack) < 2:
                raise Reject(f"{op} underflows at run time")
            b = stack.pop()
            a = stack.pop()
            stack.append(a + b if op == "ADD" else a * b)
        elif op == "PRINT":
            if not stack:
                raise Reject("PRINT underflows at run time")
            out.append(stack.pop())
        elif op == "HALT":
            break
    return out, 0


def _required_caps(units: list[list[dict]]) -> list[str]:
    caps: set[str] = set()
    for u in units:
        for ins in u:
            caps |= CAPS.get(ins["op"], set())
    return sorted(caps)


def _premises(node: dict, judgments: dict[str, dict]) -> list[dict]:
    out = []
    for p in node.get("premises", []):
        if p not in judgments:
            raise Reject(f"premise {p!r} is missing or comes later")
        out.append(judgments[p])
    return out


# ---------------------------------------------------------------- 13 条规则


def _r_ax_k(node, pre, ctx):
    j = node.get("judgment") or {}
    if j.get("kind") != "Kernel":
        raise Reject("AX-K: judgment is not Kernel")
    if j.get("rules_sha256") != RULES_SHA256:
        raise Reject("AX-K: rule-table hash mismatch")
    return j


def _r_ax_s(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Kernel":
        raise Reject("AX-S: needs one Kernel premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Compiler":
        raise Reject("AX-S: judgment is not Compiler")
    if not _hex64(j.get("seed_sha256")):
        raise Reject("AX-S: seed_sha256 is not a sha256")
    return j


def _r_parse(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Compiler":
        raise Reject("PARSE: needs one Compiler premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Source":
        raise Reject("PARSE: judgment is not Source")
    try:
        src = bytes.fromhex(j.get("source_hex", ""))
    except ValueError:
        raise Reject("PARSE: source_hex is not hex") from None
    if _sha(src) != j.get("source_sha256"):
        raise Reject("PARSE: source hash mismatch")
    try:
        ast = parse_toy(src.decode("utf-8"))
    except UnicodeDecodeError:
        raise Reject("PARSE: source is not UTF-8") from None
    if _canon(ast) != _canon(j.get("ast")):
        raise Reject("PARSE: AST does not match the source")
    return j


def _r_tv_l1(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Source":
        raise Reject("TV-L1: needs one Source premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "IR":
        raise Reject("TV-L1: judgment is not IR")
    ast = pre[0]["ast"]
    if j.get("ast_sha256") != _sha(_canon(ast)):
        raise Reject("TV-L1: AST hash mismatch")
    if _canon(j.get("ir")) != _canon(ast):
        raise Reject("TV-L1: IR is not the declared translation of the AST")
    m = j.get("map")
    if not isinstance(m, list) or len(m) != len(ast):
        raise Reject("TV-L1: map length mismatch")
    for i, e in enumerate(m):
        if not isinstance(e, dict):
            raise Reject("TV-L1: map entry is not an object")
        if e.get("index") != i or e.get("op") != ast[i]["op"] or \
                e.get("arg") != ast[i]["arg"] or e.get("offset") != 2 * i:
            raise Reject(f"TV-L1: map entry {i} does not match the AST")
    return j


def _r_tv_l2(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "IR":
        raise Reject("TV-L2: needs one IR premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Unit":
        raise Reject("TV-L2: judgment is not Unit")
    ir = pre[0]["ir"]
    if j.get("ir_sha256") != _sha(_canon(ir)):
        raise Reject("TV-L2: IR hash mismatch")
    try:
        b = bytes.fromhex(j.get("bytes_hex", ""))
    except ValueError:
        raise Reject("TV-L2: bytes_hex is not hex") from None
    if _canon(_decode_unit(b)) != _canon(ir):
        raise Reject("TV-L2: bytes do not encode the IR")
    if j.get("unit_sha256") != _sha(b):
        raise Reject("TV-L2: unit hash mismatch")
    return j


def _r_link(node, pre, ctx):
    if not pre or any(p.get("kind") != "Unit" for p in pre):
        raise Reject("LINK: needs Unit premises")
    j = node.get("judgment") or {}
    if j.get("kind") != "Linked":
        raise Reject("LINK: judgment is not Linked")
    try:
        container = bytes.fromhex(j.get("container_hex", ""))
    except ValueError:
        raise Reject("LINK: container_hex is not hex") from None
    if _sha(container) != j.get("container_sha256"):
        raise Reject("LINK: container hash mismatch")
    units = decode_artifact(container)
    want = [p["unit_sha256"] for p in pre]
    if j.get("units") != want:
        raise Reject("LINK: unit list does not match the premises")
    if len(units) != len(want):
        raise Reject("LINK: container unit count mismatch")
    return j


def _r_safe(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Linked":
        raise Reject("SAFE: needs one Linked premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Safe" or j.get("ok") is not True:
        raise Reject("SAFE: judgment is not Safe/ok")
    units = decode_artifact(bytes.fromhex(pre[0]["container_hex"]))
    peaks = [static_check(u) for u in units]
    if j.get("max_depth") != peaks:
        raise Reject("SAFE: max_depth does not match the static check")
    l0 = j.get("l0_safe")
    if l0 is not None:
        _check_l0_safe(l0)
    return j


def _r_cap(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Safe":
        raise Reject("CAP: needs one Safe premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Caps":
        raise Reject("CAP: judgment is not Caps")
    # Safe 的父是 LINK；从 derivation 的节点图里取容器。
    container = bytes.fromhex(lookup_linked(node, ctx)["container_hex"])
    required = _required_caps(decode_artifact(container))
    if j.get("required") != required:
        raise Reject("CAP: required set does not match the artifact")
    allowed = j.get("allowed")
    if not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
        raise Reject("CAP: allowed is not a string list")
    if not set(required) <= set(allowed):
        raise Reject(f"CAP: required {required} not subset of allowed {sorted(set(allowed))}")
    return j


def lookup_linked(node: dict, ctx: dict) -> dict:
    """从当前节点往上找最近一条 Linked 判断（供 CAP 用）。"""
    seen: set[str] = set()

    def walk(nid: str) -> dict | None:
        if nid in seen:
            return None
        seen.add(nid)
        n = ctx["nodes"][nid]
        if n["judgment"].get("kind") == "Linked":
            return n["judgment"]
        for p in n.get("premises", []):
            got = walk(p)
            if got is not None:
                return got
        return None

    for p in node.get("premises", []):
        got = walk(p)
        if got is not None:
            return got
    raise Reject("CAP: no Linked judgment in the premise chain")


def _r_sem(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Caps":
        raise Reject("SEM: needs one Caps premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Sem":
        raise Reject("SEM: judgment is not Sem")
    linked = lookup_linked(node, ctx)
    units = decode_artifact(bytes.fromhex(linked["container_hex"]))
    out: list[int] = []
    for u in units:
        got, code = run_unit(u)
        if code != 0:
            raise Reject("SEM: non-zero exit")
        out.extend(got)
    if j.get("output") != out or j.get("exit") != 0:
        raise Reject("SEM: output/exit do not match the interpreter")
    return j


def _r_compose(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Sem":
        raise Reject("COMPOSE: needs one Sem premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Program":
        raise Reject("COMPOSE: judgment is not Program")
    if j.get("output") != pre[0]["output"]:
        raise Reject("COMPOSE: output does not match Sem")
    linked = lookup_linked(node, ctx)
    units = decode_artifact(bytes.fromhex(linked["container_hex"]))
    if j.get("caps") != _required_caps(units):
        raise Reject("COMPOSE: caps do not match the artifact")
    if j.get("units") != len(units):
        raise Reject("COMPOSE: unit count mismatch")
    return j


def _collect(node: dict, kind: str, ctx: dict) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []

    def walk(nid: str) -> None:
        if nid in seen:
            return
        seen.add(nid)
        n = ctx["nodes"][nid]
        if n["judgment"].get("kind") == kind:
            out.append(n["judgment"])
        for p in n.get("premises", []):
            walk(p)

    for p in node.get("premises", []):
        walk(p)
    return out


def _safe_join(root: str, rel) -> Path:
    if not isinstance(rel, str) or not rel:
        raise Reject("L0: empty path")
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise Reject(f"L0: path is not relative: {rel!r}")
    base = Path(root).resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise Reject(f"L0: path escapes the root: {rel!r}") from None
    return p


def _file_meta(p: Path) -> tuple[str, int]:
    try:
        b = p.read_bytes()
    except OSError as e:
        raise Reject(f"L0: cannot read {p}: {e}") from None
    return _sha(b), len(b)


def _check_l0(j) -> None:
    """R2: 把 docs/376 的 provenance 接成 PROV 的可选前提。

    它只读文件与哈希，不 import 工具链。记录里的每个文件都要与盘上一致；
    `provenance_sha256` 把这条记录钉在具体那份 provenance 上。
    """
    if not isinstance(j, dict):
        raise Reject("L0: record is not an object")
    root = j.get("root", ".")
    prov_rel = j.get("provenance", "loment/build/genesis/provenance.json")
    prov_path = _safe_join(root, prov_rel)
    prov_sha, _size = _file_meta(prov_path)
    if j.get("provenance_sha256") != prov_sha:
        raise Reject("L0: provenance hash mismatch")
    try:
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise Reject(f"L0: provenance is not readable JSON: {e}") from None
    if prov.get("format") != 2:
        raise Reject("L0: provenance format is not 2")
    g = prov.get("genesis") or {}
    if j.get("genesis_sha256") != g.get("sha256") or j.get("genesis_size") != g.get("size"):
        raise Reject("L0: genesis record mismatch")
    gname = g.get("name")
    if not isinstance(gname, str):
        raise Reject("L0: genesis name is missing")
    gsha, gsize = _file_meta(_safe_join(root, f"loment/build/genesis/{gname}"))
    if gsha != g.get("sha256") or gsize != g.get("size"):
        raise Reject("L0: genesis file mismatch")
    for key in ("seed", "mirror"):
        rec = (prov.get("inputs") or {}).get(key) or {}
        if j.get(f"{key}_sha256") != rec.get("sha256") or \
                j.get(f"{key}_size") != rec.get("size"):
            raise Reject(f"L0: {key} record mismatch")
        sha, size = _file_meta(_safe_join(root, rec.get("path", "")))
        if sha != rec.get("sha256") or size != rec.get("size"):
            raise Reject(f"L0: {key} file mismatch")
    ir = prov.get("mirror_ir") or {}
    if not _hex64(ir.get("sha256")) or not isinstance(ir.get("size"), int):
        raise Reject("L0: mirror_ir record is malformed")
    if j.get("mirror_ir_sha256") != ir.get("sha256") or \
            j.get("mirror_ir_size") != ir.get("size"):
        raise Reject("L0: mirror_ir record mismatch")
    for key in ("source_closure", "genesis_source_closure"):
        rec = prov.get(key) or {}
        if j.get(f"{key}_sha256") != rec.get("sha256"):
            raise Reject(f"L0: {key} record mismatch")
        files = rec.get("files")
        if not isinstance(files, list) or not files:
            raise Reject(f"L0: {key} has no files")
        for e in files:
            if not isinstance(e, dict):
                raise Reject(f"L0: {key} entry is not an object")
            sha, size = _file_meta(_safe_join(root, e.get("path", "")))
            if sha != e.get("sha256") or size != e.get("size"):
                raise Reject(f"L0: closure file mismatch: {e.get('path')!r}")


def _read_json_rel(root: str, rel) -> tuple[bytes, dict]:
    p = _safe_join(root, rel)
    raw = p.read_bytes()
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as e:
        raise Reject(f"L0Safe: {rel!r} is not readable JSON: {e}") from None


def _check_potato(doc: dict) -> None:
    ver = doc.get("potato")
    if not isinstance(ver, str) or not ver.startswith("v") or not ver[1:].isdigit():
        raise Reject("L0Safe: potato version is not vN")
    n = int(ver[1:])
    if n < 20:
        raise Reject("L0Safe: lawspace requires Potato v20+")
    if not (doc.get("lvm") == "lvm" or doc.get("vm") in ("vm", "lvm")):
        raise Reject("L0Safe: artifact does not declare choose lvm/vm")
    ladder = doc.get("gc_ladder")
    if not isinstance(ladder, dict):
        raise Reject("L0Safe: gc_ladder is missing")
    keys = ("l0", "l0_5", "l1", "l2", "l3") if n >= 19 else ("l0", "l1", "l2", "l3")
    try:
        total = sum(int(ladder[k]) for k in keys)
    except (KeyError, TypeError, ValueError):
        raise Reject("L0Safe: gc_ladder has a non-integer cell") from None
    if total != ladder.get("total_sites"):
        raise Reject("L0Safe: gc_ladder sum identity is false")
    levels = doc.get("lms_levels")
    if not isinstance(levels, dict) or set(levels) != set(LMS_LEVELS) or \
            any(not isinstance(levels[k], bool) for k in LMS_LEVELS):
        raise Reject("L0Safe: lms_levels is not exactly seven booleans")
    budgets = doc.get("lms_budgets")
    if not isinstance(budgets, dict) or any(v not in BUDGETS for v in budgets.values()):
        raise Reject("L0Safe: lms_budgets has an unknown value")
    if doc.get("runtime") == "no_runtime" and any(v == "state" for v in budgets.values()):
        raise Reject("L0Safe: no_runtime artifact carries a stateful level")
    lspe = doc.get("lspe")
    if not isinstance(lspe, dict) or not isinstance(lspe.get("state"), str) or \
            not isinstance(lspe.get("sites"), int):
        raise Reject("L0Safe: lspe ledger is malformed")
    sw = doc.get("switches")
    if not isinstance(sw, list) or any(
            not isinstance(x, dict) or not isinstance(x.get("name"), str)
            or not isinstance(x.get("on"), bool) for x in sw):
        raise Reject("L0Safe: switches are malformed")


def _dims_of(doc: dict) -> dict:
    out = {k: doc[k] for k in DIMS if isinstance(doc.get(k), str)}
    lspe = doc.get("lspe")
    if isinstance(lspe, dict) and isinstance(lspe.get("state"), str):
        out["lspe"] = lspe["state"]
    return out


def _check_lawspace(doc: dict, side: dict, potato_sha: str, potato_size: int,
                    sites: dict | None) -> None:
    if side.get("schema") != 1:
        raise Reject("L0Safe: lawspace schema is not 1")
    if side.get("potato") != doc.get("potato"):
        raise Reject("L0Safe: lawspace potato version mismatch")
    art = side.get("artifact") or {}
    if art.get("sha256") != potato_sha or art.get("bytes") != potato_size:
        raise Reject("L0Safe: lawspace is not bound to the Potato bytes")
    dims = _dims_of(doc)
    if side.get("dims") != dims or side.get("dims_hash") != _sha(_canon(dims)):
        raise Reject("L0Safe: lawspace dims/dims_hash mismatch")
    laws = side.get("laws")
    if not isinstance(laws, list):
        raise Reject("L0Safe: lawspace laws is not a list")
    seen: set[str] = set()
    for law in laws:
        if not isinstance(law, dict) or law.get("id") not in LAWS:
            raise Reject("L0Safe: unknown law")
        if law["id"] in seen:
            raise Reject("L0Safe: duplicate law")
        seen.add(law["id"])
        if law.get("declared") != law["id"]:
            raise Reject("L0Safe: law declared does not match its id")
        if law.get("checked") is not True:
            raise Reject("L0Safe: law was not checked by the notary")
    g = side.get("genesis")
    if not isinstance(g, dict) or g.get("mode") != "same_artifact":
        raise Reject("L0Safe: genesis mode is not same_artifact")
    for k in ("max_depth", "max_children", "max_total"):
        v = g.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise Reject(f"L0Safe: genesis.{k} is not a non-negative integer")
    rec = side.get("sites")
    if sites is not None:
        if not isinstance(rec, dict):
            raise Reject("L0Safe: sites were provided but the lawspace does not bind them")
        if rec.get("sha256") != sites.get("seal") or \
                rec.get("count") != (sites.get("counts") or {}).get("sites"):
            raise Reject("L0Safe: lawspace does not match the site table")
    elif rec is not None:
        raise Reject("L0Safe: lawspace binds sites but none were provided")
    body = {k: v for k, v in side.items() if k != "seal"}
    if side.get("seal") != _sha(_canon(body)):
        raise Reject("L0Safe: lawspace seal mismatch")


def _check_sites(table: dict) -> None:
    schema = table.get("schema")
    if schema not in (1, 2):
        raise Reject("L0Safe: site table schema is not 1/2")
    sites = table.get("sites")
    if not isinstance(sites, list):
        raise Reject("L0Safe: site table has no sites list")
    seen: set[tuple[str, str, int]] = set()
    proven = checked = unsafe = 0
    for s in sites:
        if not isinstance(s, dict) or not isinstance(s.get("fn"), str) or \
                not isinstance(s.get("blk"), str) or \
                not isinstance(s.get("idx"), int) or isinstance(s.get("idx"), bool):
            raise Reject("L0Safe: malformed site")
        key = (s["fn"], s["blk"], s["idx"])
        if key in seen:
            raise Reject("L0Safe: duplicate site")
        seen.add(key)
        if s.get("op") not in SITE_OPS:
            raise Reject("L0Safe: unknown site op")
        if s.get("grade") not in SITE_GRADES:
            raise Reject("L0Safe: unknown site grade")
        if s["grade"] == "proven":
            proven += 1
            if s.get("rule") not in SITE_RULES:
                raise Reject("L0Safe: proven site has no rule")
            if schema == 2 and ("edge" in s or "fault" in s):
                raise Reject("L0Safe: proven site has an edge/fault")
        elif s["grade"] == "checked":
            checked += 1
            if schema == 2:
                edge = s.get("edge")
                if edge not in SITE_EDGES:
                    raise Reject("L0Safe: checked site has no valid edge")
                if edge == "fault":
                    if s.get("fault") not in SITE_FAULTS:
                        raise Reject("L0Safe: fault edge has no valid fault")
                elif "fault" in s:
                    raise Reject("L0Safe: non-fault edge carries a fault")
            elif s.get("fault") not in SITE_FAULTS:
                raise Reject("L0Safe: checked site has no fault")
        else:
            unsafe += 1
            if schema == 2 and ("edge" in s or "fault" in s):
                raise Reject("L0Safe: unsafe site has an edge/fault")
    counts = table.get("counts")
    want = {"sites": len(sites), "proven": proven,
            "checked": checked, "unsafe": unsafe}
    if not isinstance(counts, dict) or any(counts.get(k) != v for k, v in want.items()):
        raise Reject("L0Safe: site counts do not match")
    body = {k: v for k, v in table.items() if k != "seal"}
    if table.get("seal") != _sha(_canon(body)):
        raise Reject("L0Safe: site table seal mismatch")


def _check_fault_coverage(doc: dict, table: dict) -> None:
    """V3: checked 站点的坏块出口必须与产物自己的开关一致。"""
    if table.get("schema") != 2:
        return
    checked = [s for s in table.get("sites", [])
               if isinstance(s, dict) and s.get("grade") == "checked"]
    if not checked:
        return
    levels = doc.get("lms_levels") or {}
    if levels.get("lms_guard") is not True:
        raise Reject("L0Safe: checked sites but lms_guard is off")
    pangu = any(isinstance(x, dict) and x.get("name") == "lms_pangu_true"
                and x.get("on") is True for x in (doc.get("switches") or []))
    want = "fault" if pangu else ("skip" if levels.get("lms_reject") is True else "abort")
    for s in checked:
        if s.get("edge") != want:
            raise Reject(f"L0Safe: fault edge is {s.get('edge')!r}, artifact implies {want!r}")


def _check_l0_safe(j) -> None:
    """R3: LNotary lawspace + 站点表 + Potato V0 作为 SAFE 的前提。

    内核不 import LNotary：它独立重算 lawspace/站点表的封印，并核对 Potato 的
    `gc_ladder` / `lms_levels` / `lms_budgets` / `lspe` / `switches` 不变量。
    """
    if not isinstance(j, dict):
        raise Reject("L0Safe: record is not an object")
    root = j.get("root", ".")
    potato_raw, potato = _read_json_rel(root, j.get("potato", ""))
    if j.get("potato_sha256") != _sha(potato_raw):
        raise Reject("L0Safe: Potato file hash mismatch")
    sites_raw = sites = None
    if j.get("sites"):
        sites_raw, sites = _read_json_rel(root, j["sites"])
        if j.get("sites_sha256") != _sha(sites_raw):
            raise Reject("L0Safe: site table file hash mismatch")
    law_raw, law = _read_json_rel(root, j.get("lawspace", ""))
    if j.get("lawspace_sha256") != _sha(law_raw):
        raise Reject("L0Safe: lawspace file hash mismatch")
    _check_potato(potato)
    _check_lawspace(potato, law, _sha(potato_raw), len(potato_raw), sites)
    if sites is not None:
        _check_sites(sites)
        _check_fault_coverage(potato, sites)


def _deviations(units: list[list[dict]]) -> list[dict]:
    """R5: 一代产物里的偏差站点 —— 玩具里是每一个 PRINT（可观察副作用）。"""
    return [{"unit": u, "index": i, "op": ins["op"]}
            for u, unit in enumerate(units)
            for i, ins in enumerate(unit) if ins["op"] == "PRINT"]


def _dev_key(d: dict) -> tuple[int, int]:
    return (d["unit"], d["index"])


def _r_refine(node, pre, ctx):
    """R5/T6: `T'` refines `T` 当且仅当偏差站点只减不增。"""
    if len(pre) != 1 or pre[0].get("kind") != "Repro":
        raise Reject("REFINE: needs one Repro premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Refines":
        raise Reject("REFINE: judgment is not Refines")
    if j.get("artifact_sha256") != ctx["artifact_sha"]:
        raise Reject("REFINE: the new artifact hash does not match")
    if not _hex64(j.get("from_artifact_sha256")):
        raise Reject("REFINE: from_artifact_sha256 is not a sha256")
    try:
        old = bytes.fromhex(j.get("from_container_hex", ""))
    except ValueError:
        raise Reject("REFINE: from_container_hex is not hex") from None
    if _sha(old) != j["from_artifact_sha256"]:
        raise Reject("REFINE: the predecessor container hash does not match")
    linked = lookup_linked(node, ctx)
    d_to = _deviations(decode_artifact(bytes.fromhex(linked["container_hex"])))
    d_from = _deviations(decode_artifact(old))
    if j.get("to_deviations") != d_to:
        raise Reject("REFINE: to_deviations do not match the new artifact")
    if j.get("from_deviations") != d_from:
        raise Reject("REFINE: from_deviations do not match the predecessor")
    to_keys = {_dev_key(x) for x in d_to}
    from_keys = {_dev_key(x) for x in d_from}
    added = [x for x in d_to if _dev_key(x) not in from_keys]
    removed = [x for x in d_from if _dev_key(x) not in to_keys]
    delta = j.get("delta")
    if not isinstance(delta, dict):
        raise Reject("REFINE: delta is not an object")
    if delta.get("added") != added or delta.get("removed") != removed:
        raise Reject("REFINE: delta does not match the two deviation sets")
    if added:
        raise Reject("REFINE: the update adds deviations")
    return j


def _r_prov(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Program":
        raise Reject("PROV: needs one Program premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Prov":
        raise Reject("PROV: judgment is not Prov")
    sources = sorted(x["source_sha256"] for x in _collect(node, "Source", ctx))
    compilers = _collect(node, "Compiler", ctx)
    if len(compilers) != 1:
        raise Reject("PROV: need exactly one Compiler judgment")
    if j.get("source_sha256") != sources:
        raise Reject("PROV: source hash list mismatch")
    if j.get("seed_sha256") != compilers[0]["seed_sha256"]:
        raise Reject("PROV: seed hash mismatch")
    if not _hex64(j.get("recipe_sha256")):
        raise Reject("PROV: recipe_sha256 is not a sha256")
    if j.get("artifact_sha256") != ctx["artifact_sha"]:
        raise Reject("PROV: artifact hash mismatch")
    if j.get("l0") is not None:
        _check_l0(j["l0"])
    return j


def _r_repro(node, pre, ctx):
    if len(pre) != 1 or pre[0].get("kind") != "Prov":
        raise Reject("REPRO: needs one Prov premise")
    j = node.get("judgment") or {}
    if j.get("kind") != "Repro":
        raise Reject("REPRO: judgment is not Repro")
    if j.get("artifact_sha256") != ctx["artifact_sha"]:
        raise Reject("REPRO: artifact hash mismatch")
    if j.get("twice") != j.get("artifact_sha256"):
        raise Reject("REPRO: the two builds differ")
    return j


RULES_IMPL = {
    "AX-K": _r_ax_k, "AX-S": _r_ax_s, "PARSE": _r_parse,
    "TV-L1": _r_tv_l1, "TV-L2": _r_tv_l2, "LINK": _r_link,
    "SAFE": _r_safe, "CAP": _r_cap, "SEM": _r_sem,
    "COMPOSE": _r_compose, "PROV": _r_prov, "REPRO": _r_repro,
    "REFINE": _r_refine,
}


def _check(artifact: bytes, derivation: dict, conclusion: dict | None) -> dict:
    if not isinstance(derivation, dict):
        raise Reject("derivation is not an object")
    if derivation.get("format") != FORMAT:
        raise Reject("derivation format mismatch")
    kern = derivation.get("kernel") or {}
    if kern.get("rules_sha256") != RULES_SHA256:
        raise Reject("derivation was checked against another rule table")
    nodes_raw = derivation.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise Reject("derivation has no nodes")
    nodes: dict[str, dict] = {}
    judgments: dict[str, dict] = {}
    ctx = {"artifact": artifact, "artifact_sha": _sha(artifact),
           "nodes": nodes, "judgments": judgments}
    for n in nodes_raw:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            raise Reject("malformed node")
        nid = n["id"]
        if nid in nodes:
            raise Reject(f"duplicate node id {nid!r}")
        if n.get("rule") not in RULES_IMPL:
            raise Reject(f"unknown rule {n.get('rule')!r}")
        pre = _premises(n, judgments)
        nodes[nid] = n
        judgments[nid] = RULES_IMPL[n["rule"]](n, pre, ctx)
    root = derivation.get("root")
    if root not in judgments:
        raise Reject("root is missing")
    if conclusion is not None and _canon(conclusion) != _canon(judgments[root]):
        raise Reject("conclusion does not match the root judgment")
    if judgments[root].get("artifact_sha256") != ctx["artifact_sha"]:
        raise Reject("root does not bind the artifact")
    return judgments[root]


def verify(artifact: bytes, derivation: dict, conclusion: dict | None = None) -> tuple[bool, list[str]]:
    try:
        _check(artifact, derivation, conclusion)
        return True, []
    except (Reject, KeyError, TypeError, ValueError, AttributeError) as e:
        return False, [str(e)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trust_kernel", description="R0 toy kernel (docs/379)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--artifact", required=True)
    v.add_argument("--derivation", required=True)
    v.add_argument("--conclusion")
    v.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        artifact = Path(a.artifact).read_bytes()
        deriv = json.loads(Path(a.derivation).read_text(encoding="utf-8"))
        conc = json.loads(Path(a.conclusion).read_text(encoding="utf-8")) if a.conclusion else None
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 2
    ok, why = verify(artifact, deriv, conc)
    if a.json:
        print(json.dumps({"ok": ok, "why": why}, ensure_ascii=False))
    else:
        print("ACCEPT" if ok else "REJECT")
        for w in why:
            print(f"  {w}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
