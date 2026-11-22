#!/usr/bin/env python3
# loment_trust.py — 可重演链的清单 (docs/376)
#
# #776 的 `provenance.json` 把 genesis 钉在种子、镜像源与镜像 IR 上。这份清单在它上面
# 再补三件事:
#
#   1. **源闭包**: `driver.lomt` 与 `lomelf.lomt` 的 `use` 闭包, 不是只有入口文件;
#   2. **参考闭包**: 发射种子的 `lomentc.py` + 局部 import + `loment_seed.py`;
#   3. **信任面与预算**: 锚的形状、规范的内容哈希、假设条数、可数预算。
#
# 它不重算 genesis (那是 `loment_genesis.py --verify`), 它记录"这份产物是从哪粒种子、
# 按哪份配方来的", 并让"规范改了而清单没跟"变成一条红。
#
#   python tools/loment_trust.py --emit   写 loment/build/trust.json
#   python tools/loment_trust.py --check  与盘上清单对账 (并核 genesis 记录)
#   python tools/loment_trust.py --json   只打印, 不落盘
#   python tools/loment_trust.py --root   只打印根与预算
#
# 退出码: 0 = 一致 / 1 = 有差异或记录未通过 / 2 = 用法错误。

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import platform
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_genesis as G  # noqa: E402

ROOT = G.ROOT
TRUST = ROOT / "loment" / "build" / "trust.json"
SPEC = ROOT / "docs" / "376-replay-trust.md"

#: 预算的棘轮上限。**只许朝安全方向改**:
#:   * `seed_bytes` / `reference_lines` / `verifier_lines` / `assumption_count` 是上限;
#:   * `independent_lineages` 是下限 (今天 0, 目标是 >= 1)。
BUDGET_LIMITS = {
    "reference_lines": 20000,
    "verifier_lines": 1500,
    "assumption_count": 8,
    "independent_lineages": 0,
}

#: 现有谱系。`independent` 是这条谱系**是否不共享代码**; 今天全部 false
#: (`docs/160` §2.1 / `docs/203` §7.3: 同作者、同前端或派生)。
LINEAGES = [
    {"name": "reference-python", "entry": "tools/lomentc.py", "independent": False},
    {"name": "selfhost-loment", "entry": "loment/selfhost/driver.lomt", "independent": False},
    {"name": "native-lomelf", "entry": "loment/tools/lomelf.lomt", "independent": False},
    {"name": "ct-interp", "entry": "tools/loment_interp.py", "independent": False},
]

#: 信任锚的形状。可读性不是必要条件; 但总要有一个有限可述、可独立检查的锚。
#: `docs/376` §2.5 把三种锚写开; V0 用的是"provenance + 闭包"的弱化版。
TRUST_SURFACE = {
    "anchor": "provenance+closures",
    "spec": "docs/376-replay-trust.md",
    "verifier": ["tools/loment_genesis.py", "tools/loment_trust.py"],
    "assumptions": [
        "genesis 由 provenance 记录的种子/镜像构建 (format 2)",
        "sha256 无碰撞",
        "宿主与 CPU 按 ELF 语义执行",
        "参考实现与自举侧不是独立谱系",
        "随机差分尚未接入 (differential.cases=0)",
    ],
}


def _entry(p: Path) -> dict:
    """文本按通用换行归一后哈希; 与 `loment_genesis`/`loment_release` 同一条口径。"""
    b = G._text_bytes(p)
    return {"path": G._rel(p), "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}


def _local_imports(p: Path) -> set[str]:
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def reference_closure() -> list[dict]:
    """`tools/lomentc.py` + 它的**局部** import 闭包 + `loment_seed.py`。

    只跟进 `tools/<名字>.py` 这一种落点; 标准库与第三方包由 `seed_env.python` 代表。
    """
    queue = [ROOT / "tools" / "lomentc.py", ROOT / "tools" / "loment_seed.py"]
    seen: set[Path] = set()
    while queue:
        p = queue.pop()
        rp = p.resolve()
        if rp in seen or not p.exists():
            continue
        seen.add(rp)
        for name in _local_imports(p):
            q = ROOT / "tools" / f"{name}.py"
            if q.exists():
                queue.append(q)
    return [_entry(p) for p in sorted(seen, key=lambda q: q.as_posix())]


def _line_count(p: Path) -> int:
    return len(p.read_text(encoding="utf-8").splitlines())


def _in_cap() -> int | None:
    src = G.MIRROR.read_text(encoding="utf-8")
    m = re.search(r"const IN_CAP: u32 = (\d+);", src)
    return int(m.group(1)) if m else None


def _surface() -> dict:
    out = dict(TRUST_SURFACE)
    if SPEC.exists():
        out["spec_sha256"] = _entry(SPEC)["sha256"]
    return out


def budget() -> dict:
    verifier = [ROOT / "tools" / "loment_genesis.py", ROOT / "tools" / "loment_trust.py"]
    return {
        "seed_bytes": G._text_size(G.SEED),
        "seed_input_cap": _in_cap(),
        "reference_lines": _line_count(ROOT / "tools" / "lomentc.py"),
        "spec_lines": _line_count(SPEC) if SPEC.exists() else None,
        "verifier_lines": sum(_line_count(p) for p in verifier if p.exists()),
        "assumption_count": len(TRUST_SURFACE["assumptions"]),
        "oracle_lines": None,
        "replay_minutes": None,
        "independent_lineages": sum(1 for x in LINEAGES if x["independent"]),
    }


def _lineages() -> list[dict]:
    out = []
    for x in LINEAGES:
        p = ROOT / x["entry"]
        row = dict(x)
        row["sha256"] = _entry(p)["sha256"] if p.exists() else None
        out.append(row)
    return out


def build() -> dict:
    prov = G._load_provenance()
    src = G._closure(G.ENTRY)
    mirror = G._closure(G.MIRROR)
    ref = reference_closure()
    toolchain = (prov or {}).get("toolchain") or {
        "clang": G._clang_version(), "target": G.TARGET}
    seed_env = (prov or {}).get("seed_env") or {
        "host": platform.system().lower(),
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
    }
    return {
        "schema": 1,
        "root": {
            "seed": (prov or {}).get("inputs", {}).get("seed"),
            "genesis": (prov or {}).get("genesis"),
            "mirror_ir": (prov or {}).get("mirror_ir"),
            "toolchain": toolchain,
            "provenance_format": (prov or {}).get("format"),
        },
        "recipe": {
            "source_closure": {"sha256": G._closure_sha(src), "files": src},
            "genesis_source_closure": {"sha256": G._closure_sha(mirror), "files": mirror},
            "reference_closure": {"sha256": G._closure_sha(ref), "files": ref},
            "toolchain": toolchain,
            "seed_env": seed_env,
            "commands": [
                "python tools/loment_seed.py --emit",
                "python tools/loment_genesis.py --emit",
                "python tools/loment_genesis.py --verify",
                "sh loment/bootstrap.sh",
            ],
        },
        "trust_surface": _surface(),
        "diversity": {
            "lineages": _lineages(),
            "independent_lineages": sum(1 for x in LINEAGES if x["independent"]),
        },
        "differential": {"corpus_sha256": None, "cases": 0, "divergences": 0},
        "budget": budget(),
    }


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def emit() -> int:
    doc = build()
    if doc["root"]["genesis"] is None:
        print(f"[ERR] 缺 {G._rel(G.PROV)}; 先跑 python tools/loment_genesis.py --emit",
              file=sys.stderr)
        return 1
    TRUST.parent.mkdir(parents=True, exist_ok=True)
    TRUST.write_text(dumps(doc), encoding="utf-8", newline="\n")
    print(f"[OK] {G._rel(TRUST)} ({len(doc['recipe']['source_closure']['files'])} 源文件, "
          f"{len(doc['recipe']['reference_closure']['files'])} 参考文件)")
    return 0


def check(trust_path: Path = TRUST) -> int:
    want = build()
    if want["root"]["genesis"] is None:
        print(f"[ERR] 缺 {G._rel(G.PROV)}; 先跑 python tools/loment_genesis.py --emit",
              file=sys.stderr)
        return 1
    if not trust_path.exists():
        print(f"[ERR] 缺 {G._rel(trust_path)} (运行 --emit)", file=sys.stderr)
        return 1
    got = json.loads(trust_path.read_text(encoding="utf-8"))
    if dumps(got) != dumps(want):
        for k in want:
            if json.dumps(got.get(k), ensure_ascii=False, sort_keys=True) != \
                    json.dumps(want[k], ensure_ascii=False, sort_keys=True):
                print(f"[DIFF] {k}")
        print("loment_trust: 盘上清单与当前树不一致 (重跑 --emit)")
        return 1
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = G.check()
    if rc != 0:
        sys.stdout.write(buf.getvalue())
        print("loment_trust: genesis 记录/种子绑定未通过 (先跑 loment_genesis --check)",
              file=sys.stderr)
        return 1
    b = want["budget"]
    cap = b.get("seed_input_cap")
    if cap is not None and b["seed_bytes"] >= cap:
        print(f"[ERR] 种子 {b['seed_bytes']} B 超过 lomelf IN_CAP {cap} B", file=sys.stderr)
        return 1
    for key, limit in (("reference_lines", BUDGET_LIMITS["reference_lines"]),
                       ("verifier_lines", BUDGET_LIMITS["verifier_lines"]),
                       ("assumption_count", BUDGET_LIMITS["assumption_count"])):
        if b[key] > limit:
            print(f"[ERR] {key}={b[key]} 超过预算 {limit} (docs/376)", file=sys.stderr)
            return 1
    if b["independent_lineages"] < BUDGET_LIMITS["independent_lineages"]:
        print("[ERR] 独立谱系数低于预算 (docs/376)", file=sys.stderr)
        return 1
    print(f"loment_trust: 根 {want['root']['genesis']['sha256'][:16]}…, "
          f"种子 {(want['root']['seed'] or {}).get('sha256', '')[:16]}…, "
          f"源 {len(want['recipe']['source_closure']['files'])} 文件, "
          f"参考 {len(want['recipe']['reference_closure']['files'])} 文件")
    return 0


def show_root() -> int:
    doc = build()
    r = doc["root"]
    g = r["genesis"] or {}
    print(f"seed       {(r['seed'] or {}).get('sha256')}  {(r['seed'] or {}).get('size')} B")
    print(f"genesis    {g.get('sha256')}  {g.get('size')} B  "
          f"provenance_format={r['provenance_format']}")
    print(f"source     {doc['recipe']['source_closure']['sha256']}  "
          f"{len(doc['recipe']['source_closure']['files'])} files")
    print(f"reference  {doc['recipe']['reference_closure']['sha256']}  "
          f"{len(doc['recipe']['reference_closure']['files'])} files")
    b = doc["budget"]
    print(f"budget     seed={b['seed_bytes']}B/{b['seed_input_cap']}B  "
          f"reference={b['reference_lines']} lines  "
          f"verifier={b['verifier_lines']} lines  "
          f"independent_lineages={b['independent_lineages']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_trust", description="可重演链清单 (docs/376)")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", action="store_true")
    a = ap.parse_args(argv)
    if a.emit:
        return emit()
    if a.json:
        sys.stdout.write(dumps(build()))
        return 0
    if a.root:
        return show_root()
    return check()


if __name__ == "__main__":
    sys.exit(main())
