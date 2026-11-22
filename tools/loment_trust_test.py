#!/usr/bin/env python3
# loment_trust_test.py — 可重演链 V0 的判据 (docs/376)
#
# 这些判据钉住四件事:
#   1. `provenance.json` 把 genesis 钉在种子、镜像源与**两份 use 闭包**上;
#   2. 种子/闭包/规范变一个字节, 检查必须红;
#   3. `trust.json` 覆盖根 + 三份闭包 + 信任面 + 预算, 且是确定性的;
#   4. `bootstrap.sh` 在种子漂移时 fail closed (入口函数真的跑了).
#
# 运行: python tools/loment_trust_test.py

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_genesis as G  # noqa: E402
import loment_release as R  # noqa: E402
import loment_trust as T  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_provenance_binds_the_closures():
    """provenance 必须记录 driver.lomt / lomelf.lomt 的 use 闭包, 并与盘上一致。"""
    assert G.PROV.exists(), f"缺 {G._rel(G.PROV)}"
    prov = json.loads(G.PROV.read_text(encoding="utf-8"))
    assert prov.get("format") == G.PROV_FORMAT, prov.get("format")
    for key, entry, label in (("source_closure", G.ENTRY, "driver"),
                              ("genesis_source_closure", G.MIRROR, "lomelf")):
        rec = prov.get(key) or {}
        files = G._closure(entry)
        assert rec.get("sha256") == G._closure_sha(files), (
            f"{key} 与盘上不一致 —— 改了源就重跑 --emit")
        assert len(files) >= 2, f"{key} 只记录了 {len(files)} 个文件"
        print(f"      {label} 闭包 {len(files)} 文件, {rec['sha256'][:16]}…")


@test
def test_seed_binding_is_recorded_and_checked():
    """`seed.sha256` 必须等于当前种子的规范化哈希, 且 `--check` 全绿。"""
    assert G.SEED_SUMS.exists(), f"缺 {G._rel(G.SEED_SUMS)} (用 --emit 生成)"
    first = G.SEED_SUMS.read_text(encoding="utf-8").split()
    assert first and first[0] == G._sha256_text(G.SEED), "seed.sha256 与当前种子不符"
    assert G.check() == 0, "loment_genesis --check 未通过"
    print(f"      种子 {G._sha256_text(G.SEED)[:16]}…, seed.sha256 一致")


@test
def test_seed_drift_is_red():
    """把种子改一个字节, `loment_genesis --check` 必须红。"""
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "seed.ll"
        bad.write_bytes(G.SEED.read_bytes() + b"\n")
        old = G.SEED
        try:
            G.SEED = bad
            assert G.check() != 0, "种子漂移了而 --check 仍是绿的"
        finally:
            G.SEED = old
    print("      种子 +1 字节 -> check 红")


@test
def test_trust_manifest_hashes_the_closures():
    """trust 清单必须覆盖 driver.lomt 的全部 use 依赖与 lomelf 闭包。"""
    doc = T.build()
    paths = {x["path"] for x in doc["recipe"]["source_closure"]["files"]}
    need = {
        "loment/selfhost/driver.lomt",
        "loment/selfhost/lexer.lomt",
        "loment/selfhost/codegen.lomt",
        "loment/selfhost/checker.lomt",
        "loment/selfhost/comefor.lomt",
        "loment/selfhost/potato.lomt",
    }
    missing = need - paths
    assert not missing, f"源闭包缺 {sorted(missing)}"
    gpaths = {x["path"] for x in doc["recipe"]["genesis_source_closure"]["files"]}
    assert "loment/tools/lomelf.lomt" in gpaths, "genesis 源闭包缺 lomelf.lomt"
    assert doc["root"]["genesis"] is not None
    print(f"      源闭包 {len(paths)} 文件, genesis 源闭包 {len(gpaths)} 文件")


@test
def test_trust_check_detects_recipe_drift():
    """trust.json 的闭包哈希被改一个字符, --check 必须红。"""
    doc = T.build()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trust.json"
        p.write_text(T.dumps(doc), encoding="utf-8", newline="\n")
        assert T.check(trust_path=p) == 0, "刚写出的 trust.json 应当与当前树一致"
        bad = json.loads(p.read_text(encoding="utf-8"))
        bad["recipe"]["source_closure"]["sha256"] = "0" * 64
        p.write_text(T.dumps(bad), encoding="utf-8", newline="\n")
        assert T.check(trust_path=p) != 0, "闭包哈希被改而 --check 仍是绿的"
    print("      recipe.source_closure 改动 -> check 红")


@test
def test_trust_budget_within_ceiling():
    """种子必须装得进 lomelf 输入缓冲; 参考/验证器/假设不许悄悄涨过预算。"""
    doc = T.build()
    b = doc["budget"]
    cap = b["seed_input_cap"]
    assert cap is not None, "在 lomelf.lomt 里找不到 IN_CAP"
    assert b["seed_bytes"] < cap, f"种子 {b['seed_bytes']} B >= IN_CAP {cap} B"
    for key, limit in (("reference_lines", T.BUDGET_LIMITS["reference_lines"]),
                       ("verifier_lines", T.BUDGET_LIMITS["verifier_lines"]),
                       ("assumption_count", T.BUDGET_LIMITS["assumption_count"])):
        assert b[key] <= limit, f"{key}={b[key]} > {limit} (docs/376)"
    assert doc["trust_surface"]["anchor"] == "provenance+closures"
    assert doc["trust_surface"]["spec"] == "docs/376-replay-trust.md"
    assert doc["trust_surface"].get("spec_sha256")
    print(f"      种子 {b['seed_bytes']}/{cap} B, 参考 {b['reference_lines']} 行, "
          f"验证器 {b['verifier_lines']} 行, 假设 {b['assumption_count']} 条")


@test
def test_trust_json_is_deterministic():
    """同一棵树两次 build 必须逐字节相同 (否则清单不可复现)。"""
    a = T.dumps(T.build())
    b = T.dumps(T.build())
    assert a == b, "两次 build 的 trust.json 不同"
    print(f"      trust.json 确定性 ({len(a)} B)")


@test
def test_bootstrap_fails_closed_on_seed_drift():
    """入口函数: 用漂移的种子跑 bootstrap, 必须在执行 genesis 前失败。"""
    boot = G.BOOT.read_text(encoding="utf-8") if hasattr(G, "BOOT") else \
        (G.ROOT / "loment" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "seed.sha256" in boot and "种子漂移" in boot, "bootstrap.sh 没有种子哈希检查"
    if not G._wsl():
        print("      SKIP: 无 WSL (只做了静态检查)")
        return
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "seed.ll"
        bad.write_bytes(G.SEED.read_bytes() + b"\n")
        script = (f"cd {G._wsl_path(G.ROOT)} && "
                  f"LOMENT_ALLOW_DRIFT=0 LOMENT_SEED={G._wsl_path(bad)} sh loment/bootstrap.sh")
        r = subprocess.run([*G._WSLCMD, "bash", "-lc", script],
                           capture_output=True, text=True, timeout=180, shell=False)
        out = (r.stdout or "") + (r.stderr or "")
        assert r.returncode != 0, f"漂移种子仍跑通 bootstrap: {out[-300:]!r}"
        assert "种子漂移" in out, f"失败原因不是种子漂移: {out[-300:]!r}"
    print("      漂移种子 -> bootstrap 在编译前失败")


@test
def test_trust_is_in_release_manifest():
    """工具与清单必须在发布口径里, 否则第三方拿不到重演配方。"""
    for p in ("tools/loment_trust.py", "tools/loment_trust_test.py",
              "loment/build/trust.json"):
        assert p in R.GLOBS, f"{p} 不在 loment_release.GLOBS"
    files = {x["path"] for x in R.build()["files"]}
    assert "tools/loment_trust.py" in files
    assert "tools/loment_trust_test.py" in files
    if T.TRUST.exists():
        assert "loment/build/trust.json" in files
    print("      trust 工具/清单在发布口径里")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_trust_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
