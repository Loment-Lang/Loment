#!/usr/bin/env python3
"""lvm_seal.py — 只信产物：封印、独立验证器、同产物创世衰减（`docs/358` / `docs/363`）。

三件东西，一个文件，**零 import 工具链**（只用标准库）：

* **封印**（`emit`）：一份 LVM 字节码（文本 IR）的静态事实 —— 产物哈希、逐站点台账、
  基质剖面（guarantees / assumptions）、规律与排除、创世预算、验证器标价、总哈希。
* **验证器**（`verify`）：只读产物与封印，**独立解码** IR 找访存站点，重算台账与总哈希。
  它不 import 编译器、链接器、LSPE 包，也不读源码。
* **创世衰减**（`derive`）：同产物派生 —— capset ⊆ 父、预算 ≤ 父、深度/子数/总数不超封印；
  子的 `sum = H(parent.sum ‖ canonical(delta))`。

`docs/358` §1 的 A 链在这里的落法：完备性从「源码里的每个访存」换成「**产物里的每条访存**」。
验证器自己扫 IR，不信编译器标的站点；删一条 / 多一条都红。

用法:
  python tools/lvm_seal.py emit   --ir FILE.ll --profile sw_cap [--out seal.json]
  python tools/lvm_seal.py verify --ir FILE.ll --seal seal.json [--host sw_cap,sw_tag]
  python tools/lvm_seal.py derive --parent universe.json --caps a,b --heap N --steps N
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

#: 2 = 1 + 解释器标价（`docs/360` §7 B8，`docs/363` §6）：字段变了就升版，旧封印拒收。
SCHEMA = 2
MODE_SAME_ARTIFACT = "same_artifact"

#: 四个基质档（`docs/360` §3.3）。`guarantees` 是这一档**真的兑现**的原语；
#: `assumptions` 是它只当公理的、或在别的宿主上才成立的东西。
SUBSTRATES = {
    "sw_cap": {
        "guarantees": ["P1.site", "P2.bounds", "P3.gen", "P4.fault"],
        "assumptions": ["P5.checkpoint:opt_in", "W6.host_cpu"],
    },
    "sw_tag": {
        "guarantees": ["P1.site", "P2.bounds", "P4.fault"],
        "assumptions": ["P3.probabilistic", "P5.checkpoint:opt_in", "W6.host_cpu"],
    },
    "hw_cap": {
        "guarantees": ["P1.site", "P2.bounds", "P3.gen", "P4.fault", "P5.hardware"],
        "assumptions": ["W6.cheri_silicon"],
    },
    "hw_tag": {
        "guarantees": ["P1.site", "P2.bounds", "P4.fault", "P5.hardware"],
        "assumptions": ["P3.probabilistic", "W6.mte_silicon"],
    },
}

#: 这台机器上**真的能跑**的基质。`hw_*` 要 CHERI / MTE，本机没有。
DEFAULT_HOST = ("sw_cap", "sw_tag")

#: 规律 -> 它需要的原语。`checked` 的规律缺了任何一条，验证器就红。
LAW_NEEDS = {
    "site_stable": ["P1.site"],
    "no_segv": ["P2.bounds", "P4.fault"],
    "no_dangling": ["P3.gen", "P4.fault"],
}

#: 一个站点台账条目的 grade：本档兑现了边界/对象代就是 `checked`，否则 `unsafe`。
MEMORY_KINDS = ("load", "store", "getelementptr", "atomicrmw")
UNSAFE_KINDS = ("syscall", "foreign")

MAX_VERIFIER_BYTES = 128 * 1024

#: **解释器标价**（`docs/360` §7 B8）：时间桥梁把信任从硅片移到解释器，解释器就必须
#: **小到能审**。预算是**决定**，不是测出来的数 —— 越过它，这座桥只是把信任搬进一个
#: 更大的东西。两份实现各自记，总数也记；封印里同时带价与预算（`docs/363` §6）。
#: 解释器（参考 + 自举两份源码）的声明价格。**这是标价，不是目标**：越过就拒绝封印，
#: 因为"信任只是搬家"（`docs/360` B8）。2026-11-21 的实测是 9,450 行 / 372,293 字节
#: —— 段错误详细化、dismemory 与自举 `sw_tag` 都落进了这两份源码，原来的 8,000/320 KiB
#: 已经装不下；抬到 10,000 行 / 400 KiB，并在 `docs/363` §2 记下这次抬价。
#: 2026-11-22（LVM-A 二期 S3/S4：镜像的格表与快照段）再抬到 11,000 行 /
#: 432 KiB —— 记账在 `docs/375` §2.5。
INTERPRETER_BUDGET = {"lines": 11000, "bytes": 432 * 1024}
INTERPRETER_SOURCES = {
    "reference": Path(__file__).resolve().parent / "lvm.py",
    "selfhost": Path(__file__).resolve().parent.parent / "loment" / "tools" / "lvm.lomt",
}


# ---------------------------------------------------------------- 规范化与哈希

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sum_of(obj: dict) -> str:
    """对**除 `sum` 以外**的全部字段取哈希。字段顺序由规范序列化钉住。"""
    body = {k: v for k, v in obj.items() if k != "sum"}
    return sha256_hex(canonical(body))


# ---------------------------------------------------------------- 独立解码：站点

_DEFINE_RE = re.compile(r"^define\b[^\n]*?@([-A-Za-z0-9_.]+)\s*\(")
_DECLARE_RE = re.compile(r"^declare\b[^\n]*?@([-A-Za-z0-9_.]+)\s*\(")
_LABEL_RE = re.compile(r"^([-A-Za-z0-9_.]+):\s*$")


def scan_sites(ir_text: str) -> list[dict]:
    """独立扫一遍 IR：每条访存 / 每条 unsafe 调用一个站点。**不 import 编译器。**

    站点编号按产物里的出现顺序分配；`offset` 是指令行的字节偏移。函数 / 块 / 块内序号
    与 `llvm.trap` 的站点同形，第三方可以把判决指回产物里的那一行。
    """
    lines = ir_text.splitlines(keepends=True)
    sites: list[dict] = []
    fn = None
    blk = None
    idx = 0
    declared: set[str] = set()
    offset = 0
    for raw in lines:
        line = raw.split(";", 1)[0]
        stripped = line.strip()
        m = _DEFINE_RE.match(stripped)
        if m:
            fn, blk, idx = m.group(1), None, 0
            offset += len(raw.encode("utf-8"))
            continue
        m = _DECLARE_RE.match(stripped)
        if m:
            declared.add(m.group(1))
            offset += len(raw.encode("utf-8"))
            continue
        m = _LABEL_RE.match(stripped)
        if m and fn is not None:
            blk, idx = m.group(1), 0
            offset += len(raw.encode("utf-8"))
            continue
        if not stripped or stripped == "} " or stripped == "}":
            offset += len(raw.encode("utf-8"))
            continue
        if fn is not None and blk is not None:
            body = stripped.split("=", 1)[1].strip() if "=" in stripped else stripped
            word = body.split()[0] if body else ""
            kind = None
            if word in MEMORY_KINDS:
                kind = word
            elif word == "call":
                if 'asm sideeffect "syscall"' in body:
                    kind = "syscall"
                else:
                    callee = re.search(r"@([-A-Za-z0-9_.]+)\s*\(", body)
                    if callee is not None and callee.group(1) in declared:
                        kind = "foreign"
            if kind is not None:
                sites.append({
                    "id": len(sites),
                    "fn": fn,
                    "blk": blk,
                    "idx": idx,
                    "offset": offset,
                    "kind": kind,
                })
            idx += 1
        offset += len(raw.encode("utf-8"))
    return sites


def grade_sites(sites: list[dict], effective: list[str]) -> list[dict]:
    """本档兑现了什么，就给站点什么判决；兑现不了的一律 `unsafe`。"""
    has_bounds = "P2.bounds" in effective
    out = []
    for s in sites:
        if s["kind"] in UNSAFE_KINDS:
            g = "unsafe"
        elif has_bounds:
            g = "checked"
        else:
            g = "unsafe"
        out.append(dict(s, grade=g))
    return out


def build_laws(sites: list[dict], effective: list[str]) -> list[dict]:
    unsafe = [s["id"] for s in sites if s["grade"] == "unsafe"]
    laws = []
    for law in sorted(LAW_NEEDS):
        needs = LAW_NEEDS[law]
        ok = all(n in effective for n in needs)
        laws.append({
            "id": law,
            "verdict": "checked" if ok else "floored",
            "needs": needs,
            "exclusions": unsafe,
        })
    return laws


# ---------------------------------------------------------------- 封印

def _verifier_meta(path: Path | None = None) -> dict:
    p = path or Path(__file__)
    data = p.read_bytes()
    return {
        "id": "lvm_seal",
        "version": 1,
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "lines": data.count(b"\n"),
        "max_bytes": MAX_VERIFIER_BYTES,
    }


def _interpreter_meta(sources: dict[str, Path] | None = None) -> dict:
    srcs = sources or INTERPRETER_SOURCES
    per: dict[str, dict] = {}
    tot_l = tot_b = 0
    for name, p in srcs.items():
        data = Path(p).read_bytes()
        lines = data.count(b"\n")
        per[name] = {"lines": lines, "bytes": len(data), "sha256": sha256_hex(data)}
        tot_l += lines
        tot_b += len(data)
    return {"sources": per, "total": {"lines": tot_l, "bytes": tot_b},
            "budget": dict(INTERPRETER_BUDGET)}


def build_seal(ir_text: str, profile: str, host=DEFAULT_HOST,
               genesis: dict | None = None, floor: dict | None = None,
               verifier_path: Path | None = None,
               interpreter_sources: dict[str, Path] | None = None) -> dict:
    if profile not in SUBSTRATES:
        raise ValueError(f"没有这一档基质: {profile!r}")
    spec = SUBSTRATES[profile]
    available = profile in host
    effective = list(spec["guarantees"]) if available else []
    assumptions = list(spec["assumptions"])
    if not available:
        assumptions = assumptions + [f"substrate:{profile}_unavailable"] + list(spec["guarantees"])
    raw_sites = scan_sites(ir_text)
    sites = grade_sites(raw_sites, effective)
    genesis = dict(genesis or {"mode": MODE_SAME_ARTIFACT, "max_depth": 4,
                               "max_children": 4, "max_total": 16})
    data = ir_text.encode("utf-8")
    seal = {
        "schema": SCHEMA,
        "artifact": {
            "format": 1,
            "bytes": len(data),
            "sha256": sha256_hex(data),
            "site_count": len(sites),
            "sites_sha256": sha256_hex(canonical(sites)),
        },
        "sites": sites,
        "laws": build_laws(sites, effective),
        "substrate": {
            "profile": profile,
            "guarantees": sorted(effective),
            "assumptions": sorted(set(assumptions)),
        },
        "verifier": _verifier_meta(verifier_path),
        "interpreter": _interpreter_meta(interpreter_sources),
        "genesis": genesis,
        "floor": dict(floor or {"seed_sha256": None}),
    }
    seal["sum"] = sum_of(seal)
    return seal


def verify(ir_text: str, seal: dict, host=DEFAULT_HOST,
           verifier_bytes: bytes | None = None) -> dict:
    """只读产物 + 封印的独立判决。返回 `{ok, reasons, checked}`。"""
    reasons: list[str] = []
    checked: list[str] = []

    if seal.get("schema") != SCHEMA:
        return {"ok": False, "reasons": [f"unknown schema: {seal.get('schema')!r}"],
                "checked": checked}
    if sum_of(seal) != seal.get("sum"):
        reasons.append("seal sum does not match its fields (tampered)")
    else:
        checked.append("seal sum")

    data = ir_text.encode("utf-8")
    art = seal.get("artifact") or {}
    if art.get("bytes") != len(data) or art.get("sha256") != sha256_hex(data):
        reasons.append("artifact hash/size does not match the seal")
    else:
        checked.append("artifact hash")

    sub = seal.get("substrate") or {}
    profile = sub.get("profile")
    if profile not in SUBSTRATES:
        reasons.append(f"unknown substrate profile: {profile!r}")
        return {"ok": False, "reasons": reasons, "checked": checked}
    spec = SUBSTRATES[profile]
    available = profile in host
    effective = list(spec["guarantees"]) if available else []
    assumptions = list(spec["assumptions"])
    if not available:
        assumptions = assumptions + [f"substrate:{profile}_unavailable"] + list(spec["guarantees"])
    if sorted(effective) != list(sub.get("guarantees") or []):
        reasons.append(
            "substrate guarantees are not what this host can honour "
            f"(profile={profile}, host={sorted(host)})")
    else:
        checked.append("substrate guarantees")
    if sorted(set(assumptions)) != list(sub.get("assumptions") or []):
        reasons.append("substrate assumptions do not match the profile/host")
    else:
        checked.append("substrate assumptions")

    raw_sites = scan_sites(ir_text)
    sites = grade_sites(raw_sites, effective)
    if sites != seal.get("sites"):
        reasons.append("site ledger does not match the artifact (missing/extra/tampered access)")
    else:
        checked.append("site ledger")
    if art.get("site_count") != len(sites) or art.get("sites_sha256") != sha256_hex(canonical(sites)):
        reasons.append("site count/hash does not match the artifact")
    else:
        checked.append("site count/hash")

    laws = build_laws(sites, effective)
    if laws != seal.get("laws"):
        reasons.append("laws do not match the substrate and the site ledger")
    else:
        checked.append("laws")

    g = seal.get("genesis") or {}
    if g.get("mode") != MODE_SAME_ARTIFACT:
        reasons.append(f"genesis mode unsupported: {g.get('mode')!r}")
    elif not all(isinstance(g.get(k), int) and g[k] >= 1
                 for k in ("max_depth", "max_children", "max_total")):
        reasons.append("genesis bounds must be positive integers")
    else:
        checked.append("genesis")

    vm = seal.get("verifier") or {}
    if verifier_bytes is not None:
        if vm.get("bytes") != len(verifier_bytes) or vm.get("sha256") != sha256_hex(verifier_bytes):
            reasons.append("verifier bytes/hash do not match the seal")
        elif vm.get("bytes", 0) > vm.get("max_bytes", 0):
            reasons.append("verifier exceeds its declared byte budget")
        else:
            checked.append("verifier")
    if vm.get("bytes", 0) > vm.get("max_bytes", MAX_VERIFIER_BYTES):
        reasons.append("verifier exceeds its declared byte budget")

    # B8（`docs/360` §7）：解释器标价 —— 两条实现各自的价、总数、以及预算。
    # 验证器不读解释器源码（那是构建侧的事），它判**封印自己**：来源与总数对得上、
    # 总数不越预算；`lvm_seal_test` 的判 H 再把封印里的哈希与盘上文件对一次。
    ip = seal.get("interpreter")
    srcs = (ip or {}).get("sources") or {}
    tot = (ip or {}).get("total") or {}
    bud = (ip or {}).get("budget") or {}
    if not isinstance(ip, dict) or not srcs:
        reasons.append("interpreter price is missing from the seal")
    elif not all(isinstance(s, dict) and isinstance(s.get("lines"), int)
                 and isinstance(s.get("bytes"), int)
                 and s["lines"] >= 0 and s["bytes"] >= 0
                 for s in srcs.values()):
        reasons.append("interpreter sources must carry non-negative lines/bytes")
    else:
        want_l = sum(s["lines"] for s in srcs.values())
        want_b = sum(s["bytes"] for s in srcs.values())
        if tot.get("lines") != want_l or tot.get("bytes") != want_b:
            reasons.append("interpreter total does not match its sources")
        elif not (isinstance(bud.get("lines"), int) and isinstance(bud.get("bytes"), int)
                  and bud["lines"] > 0 and bud["bytes"] > 0):
            reasons.append("interpreter budget must be positive integers")
        elif want_l > bud["lines"] or want_b > bud["bytes"]:
            reasons.append(
                f"interpreter exceeds its declared budget ({want_l} lines / "
                f"{want_b} bytes > {bud['lines']} / {bud['bytes']})")
        else:
            checked.append("interpreter price")

    return {"ok": not reasons, "reasons": reasons, "checked": checked}


# ---------------------------------------------------------------- 同产物创世衰减

def universe_sum(u: dict) -> str:
    """根的 `sum` = 对全部字段取哈希；派生的 `sum` = `H(parent.sum ‖ canonical(delta))`。

    派生节点自带 `parent` 与 `delta`，所以整条链可以离线逐节复核。
    """
    if int(u.get("depth", 0)) == 0:
        return sum_of(u)
    return sha256_hex(u["parent"].encode("utf-8") + canonical(u["delta"]))


def root_universe(seal: dict, caps: list[str], heap: int, steps: int) -> dict:
    u = {
        "artifact": seal["artifact"]["sha256"],
        "seal_sum": seal["sum"],
        "caps": sorted(caps),
        "budget": {"heap": int(heap), "steps": int(steps)},
        "depth": 0,
        "children": 0,
        "total": 1,
        "genesis": dict(seal["genesis"]),
    }
    u["sum"] = sum_of(u)
    return u


def derive(parent: dict, delta: dict) -> tuple[dict | None, list[str]]:
    """`docs/358` §3.2 的 G1–G5。失败返回 `(None, [原因])`。"""
    why: list[str] = []
    if universe_sum(parent) != parent.get("sum"):
        why.append("parent_sum_mismatch")
    caps = set(parent.get("caps") or [])
    want = set(delta.get("caps") or [])
    if not want <= caps:
        why.append("cap_escalation")
    pb = parent.get("budget") or {}
    db = delta.get("budget") or {}
    for k in ("heap", "steps"):
        if int(db.get(k, 0)) > int(pb.get(k, 0)):
            why.append(f"budget_escalation:{k}")
    gen = parent.get("genesis") or {}
    if int(parent.get("depth", 0)) + 1 > int(gen.get("max_depth", 0)):
        why.append("depth_exceeded")
    if int(parent.get("children", 0)) + 1 > int(gen.get("max_children", 0)):
        why.append("children_exceeded")
    if int(parent.get("total", 0)) + 1 > int(gen.get("max_total", 0)):
        why.append("tree_exceeded")
    if why:
        return None, why
    budget = {"heap": int(db.get("heap", 0)), "steps": int(db.get("steps", 0))}
    child = {
        "artifact": parent["artifact"],
        "seal_sum": parent["seal_sum"],
        "caps": sorted(want),
        "budget": budget,
        "depth": int(parent["depth"]) + 1,
        "children": 0,
        "total": int(parent["total"]) + 1,
        "genesis": dict(gen),
        "parent": parent["sum"],
        "delta": {"caps": sorted(want), "budget": budget},
    }
    child["sum"] = universe_sum(child)
    return child, []


# ---------------------------------------------------------------- 前门

def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_emit(a) -> int:
    seal = build_seal(_read(a.ir), a.profile, tuple(a.host.split(",")),
                      genesis=json.loads(a.genesis) if a.genesis else None)
    text = json.dumps(seal, sort_keys=True, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


def cmd_verify(a) -> int:
    seal = json.loads(_read(a.seal))
    vb = Path(a.verifier).read_bytes() if a.verifier else None
    got = verify(_read(a.ir), seal, tuple(a.host.split(",")), vb)
    for line in got["checked"]:
        print(f"  OK   {line}")
    for line in got["reasons"]:
        print(f"  FAIL {line}", file=sys.stderr)
    print("verify:", "PASS" if got["ok"] else "FAIL")
    return 0 if got["ok"] else 1


def cmd_derive(a) -> int:
    parent = json.loads(_read(a.parent))
    delta = {"caps": [c for c in (a.caps or "").split(",") if c],
             "budget": {"heap": a.heap, "steps": a.steps}}
    child, why = derive(parent, delta)
    if child is None:
        for w in why:
            print(f"  REFUSED {w}", file=sys.stderr)
        return 1
    text = json.dumps(child, sort_keys=True, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lvm_seal",
                                 description="LVM 封印 / 独立验证器 / 同产物创世（docs/363）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit", help="为一份 LVM 字节码（文本 IR）生成封印")
    e.add_argument("--ir", required=True)
    e.add_argument("--profile", choices=tuple(SUBSTRATES), default="sw_cap")
    e.add_argument("--host", default=",".join(DEFAULT_HOST),
                   help="这台机器真的能跑的基质（默认 sw_cap,sw_tag）")
    e.add_argument("--genesis", default=None, help='JSON，例如 {"mode":"same_artifact",...}')
    e.add_argument("--out", default=None)
    v = sub.add_parser("verify", help="只读产物与封印，重算判决")
    v.add_argument("--ir", required=True)
    v.add_argument("--seal", required=True)
    v.add_argument("--host", default=",".join(DEFAULT_HOST))
    v.add_argument("--verifier", default=None, help="可选的验证器文件，核对它的字节与哈希")
    d = sub.add_parser("derive", help="同产物派生：左闭的 capset / 预算 / 深度")
    d.add_argument("--parent", required=True)
    d.add_argument("--caps", default="")
    d.add_argument("--heap", type=int, default=0)
    d.add_argument("--steps", type=int, default=0)
    d.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    return {"emit": cmd_emit, "verify": cmd_verify, "derive": cmd_derive}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
