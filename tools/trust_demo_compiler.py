#!/usr/bin/env python3
# trust_demo_compiler.py — R0 玩具编译器 / 证明生产者 (docs/377)
#
# 它把玩具源编成一个链接容器，并**同时**产出 `(derivation, conclusion)`。
# 内核不 import 它；它可以撒谎，内核会按规则拒绝。
#
#   python trust_demo_compiler.py build --source a.toy --out DIR [--source b.toy ...]
#
# 产物: DIR/program.toy (artifact)、program.deriv、program.phi、trust.json。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trust_kernel as K  # noqa: E402


def parse(text: str) -> list[dict]:
    # 编译器这一侧独立写一遍；内核的 PARSE 会用**它自己**的解析器复查。
    out: list[dict] = []
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        op = parts[0].upper()
        if op not in K.OPS:
            raise SystemExit(f"line {ln}: unknown op {parts[0]!r}")
        if K.ARITY[op] == 0:
            if len(parts) != 1:
                raise SystemExit(f"line {ln}: {op} takes no argument")
            arg = 0
        else:
            if len(parts) != 2:
                raise SystemExit(f"line {ln}: {op} needs one argument")
            try:
                arg = int(parts[1], 10)
            except ValueError:
                raise SystemExit(f"line {ln}: argument is not an integer") from None
            if not 0 <= arg <= 255:
                raise SystemExit(f"line {ln}: argument out of byte range")
        out.append({"op": op, "arg": arg})
    if not out:
        raise SystemExit("empty source")
    if out[-1]["op"] != "HALT":
        raise SystemExit("last instruction must be HALT")
    return out


def encode(ast: list[dict]) -> bytes:
    return b"".join(bytes([K.OPS[x["op"]], x["arg"]]) for x in ast)


def container(unit_bytes: list[bytes]) -> bytes:
    return K._canon({
        "format": "toy/1",
        "units": [{"hex": b.hex(), "sha256": K._sha(b)} for b in unit_bytes],
    })


def build_program(sources: list[str], seed: str = "seed-0",
                  recipe: str = "toy-compiler/1",
                  allowed: list[str] | None = None,
                  l0_root: str | None = None,
                  provenance: str = "loment/build/genesis/provenance.json") -> tuple[bytes, dict, dict]:
    if allowed is None:
        allowed = ["print"]
    nodes: list[dict] = []
    judgments: dict[str, dict] = {}

    def add(nid: str, rule: str, premises: list[str], judgment: dict) -> None:
        node = {"id": nid, "rule": rule, "premises": premises, "judgment": judgment}
        nodes.append(node)
        judgments[nid] = judgment

    add("k", "AX-K", [], {"kind": "Kernel", "rules_sha256": K.RULES_SHA256})
    seed_sha = K._sha(seed.encode("utf-8"))
    add("s", "AX-S", ["k"], {"kind": "Compiler", "seed_sha256": seed_sha, "spec": "toy/1"})

    unit_bytes: list[bytes] = []
    source_hashes: list[str] = []
    unit_ids: list[str] = []
    for i, text in enumerate(sources):
        ast = parse(text)
        b = encode(ast)
        unit_bytes.append(b)
        source_sha = K._sha(text.encode("utf-8"))
        source_hashes.append(source_sha)
        p, t1, t2 = f"u{i}.p", f"u{i}.t1", f"u{i}.t2"
        add(p, "PARSE", ["s"], {
            "kind": "Source", "source_hex": text.encode("utf-8").hex(),
            "source_sha256": source_sha, "ast": ast,
        })
        add(t1, "TV-L1", [p], {
            "kind": "IR", "ast_sha256": K._sha(K._canon(ast)), "ir": ast,
            "map": [{"index": j, "op": x["op"], "arg": x["arg"], "offset": 2 * j}
                    for j, x in enumerate(ast)],
        })
        add(t2, "TV-L2", [t1], {
            "kind": "Unit", "ir_sha256": K._sha(K._canon(ast)),
            "bytes_hex": b.hex(), "unit_sha256": K._sha(b),
        })
        unit_ids.append(t2)

    art = container(unit_bytes)
    art_sha = K._sha(art)
    units = [decode_unit(x) for x in unit_bytes]
    linked = {
        "kind": "Linked", "units": [K._sha(b) for b in unit_bytes],
        "container_hex": art.hex(), "container_sha256": art_sha,
    }
    add("link", "LINK", unit_ids, linked)
    peaks = [K.static_check(u) for u in units]
    add("safe", "SAFE", ["link"], {"kind": "Safe", "ok": True, "max_depth": peaks})
    required = K._required_caps(units)
    add("cap", "CAP", ["safe"], {"kind": "Caps", "required": required,
                                 "allowed": sorted(allowed)})
    output: list[int] = []
    for u in units:
        got, code = K.run_unit(u)
        if code != 0:
            raise SystemExit("toy interpreter returned non-zero")
        output.extend(got)
    add("sem", "SEM", ["cap"], {"kind": "Sem", "output": output, "exit": 0})
    add("compose", "COMPOSE", ["sem"], {
        "kind": "Program", "output": output, "caps": required, "units": len(units),
    })
    prov = {
        "kind": "Prov", "source_sha256": sorted(source_hashes),
        "seed_sha256": seed_sha,
        "recipe_sha256": K._sha(recipe.encode("utf-8")),
        "artifact_sha256": art_sha,
    }
    if l0_root is not None:
        try:
            base = Path(l0_root).resolve()
            raw = (base / provenance).read_bytes()
            doc = json.loads(raw.decode("utf-8"))
            g = doc["genesis"]
            s = doc["inputs"]["seed"]
            m = doc["inputs"]["mirror"]
            ir = doc["mirror_ir"]
            sc = doc["source_closure"]
            gs = doc["genesis_source_closure"]
            prov["l0"] = {
                "root": str(base),
                "provenance": provenance,
                "provenance_sha256": K._sha(raw),
                "genesis_sha256": g["sha256"], "genesis_size": g["size"],
                "seed_sha256": s["sha256"], "seed_size": s["size"],
                "mirror_sha256": m["sha256"], "mirror_size": m["size"],
                "mirror_ir_sha256": ir["sha256"], "mirror_ir_size": ir["size"],
                "source_closure_sha256": sc["sha256"],
                "genesis_source_closure_sha256": gs["sha256"],
            }
        except (OSError, KeyError, TypeError, ValueError, UnicodeError) as e:
            raise SystemExit(f"bad L0 provenance: {e}") from None
    add("prov", "PROV", ["compose"], prov)
    root = {"kind": "Repro", "artifact_sha256": art_sha, "twice": art_sha}
    add("repro", "REPRO", ["prov"], root)
    deriv = {
        "format": K.FORMAT,
        "kernel": {"rules_sha256": K.RULES_SHA256},
        "nodes": nodes,
        "root": "repro",
        "artifact_sha256": art_sha,
    }
    return art, deriv, root


def decode_unit(b: bytes) -> list[dict]:
    return K._decode_unit(b)


def write_program(out: Path, sources: list[str], seed: str = "seed-0",
                  recipe: str = "toy-compiler/1", allowed: list[str] | None = None,
                  l0_root: str | None = None,
                  provenance: str = "loment/build/genesis/provenance.json") -> dict:
    art, deriv, conc = build_program(sources, seed=seed, recipe=recipe, allowed=allowed,
                                     l0_root=l0_root, provenance=provenance)
    out.mkdir(parents=True, exist_ok=True)
    (out / "program.toy").write_bytes(art)
    (out / "program.deriv").write_text(
        json.dumps(deriv, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (out / "program.phi").write_text(
        json.dumps(conc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    spec = Path(__file__).resolve().parent.parent / "docs" / "377-trust-quorum.md"
    budget = {
        "format": 1,
        "rules": len(K.RULES),
        "rule_lines": len(Path(K.__file__).read_text(encoding="utf-8").splitlines()),
        "spec_lines": len(spec.read_text(encoding="utf-8").splitlines()) if spec.exists() else None,
        "proof_bytes": len(K._canon(deriv)),
        "check_ms": None,
        "n": 1, "k": 1, "diversity_axes": 0,
        "assumptions": 7,
    }
    (out / "trust.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    return budget


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trust_demo_compiler",
                                 description="R0 toy compiler/prover (docs/377)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--source", action="append", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--seed", default="seed-0")
    b.add_argument("--recipe", default="toy-compiler/1")
    b.add_argument("--allow", action="append", default=None)
    b.add_argument("--l0-root", default=None,
                   help="把该根下的 loment/build/genesis/provenance.json 接成 PROV 前提")
    b.add_argument("--provenance", default="loment/build/genesis/provenance.json")
    a = ap.parse_args(argv)
    sources = [Path(p).read_text(encoding="utf-8") for p in a.source]
    budget = write_program(Path(a.out), sources, seed=a.seed, recipe=a.recipe,
                           allowed=a.allow, l0_root=a.l0_root,
                           provenance=a.provenance)
    print(f"[OK] {a.out}/program.toy  proof {budget['proof_bytes']} B  "
          f"rules {budget['rules']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
