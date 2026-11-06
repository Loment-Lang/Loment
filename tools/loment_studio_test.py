#!/usr/bin/env python3
# loment_studio_test.py — Loment Studio（源码参数台）的判据。
#
# ## 量的是什么
#
#   1. **Rust 侧**：解析、外科式拼接、新增、环境矩阵、目录覆盖 —— `cargo test --workspace`。
#   2. **CLI 真的能用**：真实自举源能列出参数；`--dry-run` 只换那一个值且**不写盘**；
#      `matrix` 把编译器拒绝的组合与理由一起报出来。
#   3. **示例文件本身合法**：`lomentc.check` 说通过。
#   4. **前端资源接上了**：`index.html` 引用的都在；`app.js` 过一遍 `node --check`。
#
# 无 `cargo` / `node` 时对应条目 SKIP（退出码 0），与仓里其它判据同一条纪律。
#
# 用法: python tools/loment_studio_test.py

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STUDIO = ROOT / "editors" / "loment-studio"
WEB = STUDIO / "web"
DEMO = "editors/loment-studio/examples/demo_params.lomt"
REAL = "loment/selfhost/codegen.lomt"

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _cargo() -> str | None:
    return shutil.which("cargo")


def _cli(args: list[str]) -> subprocess.CompletedProcess:
    cargo = _cargo()
    assert cargo, "无 cargo"
    return _run(
        [cargo, "run", "-q", "--manifest-path", str(STUDIO / "Cargo.toml"),
         "-p", "loment-studio-cli", "--", *args]
    )


@test
def test_cargo_tests_pass():
    """Rust 侧：解析 / 拼接 / 新增 / 矩阵 / 目录覆盖。"""
    cargo = _cargo()
    if cargo is None:
        print("      SKIP: 无 cargo")
        return
    r = _run([cargo, "test", "-q", "--workspace"], STUDIO)
    assert r.returncode == 0, f"cargo test 失败:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    print("      cargo test --workspace: 通过")


@test
def test_lockfile_stays_v3():
    """门禁镜像的 apt cargo 是 1.75，读不了 lockfile v4 —— 被 `cargo update` 抬上去要立刻红。"""
    head = (STUDIO / "Cargo.lock").read_text(encoding="utf-8").splitlines()[:4]
    assert "version = 3" in head, (
        "Cargo.lock 不是 v3 了：CI 镜像是 Ubuntu 24.04 的 cargo 1.75。"
        "用 `cargo +1.75.0 generate-lockfile` 重新生成，别用新版 cargo 抬版本。"
    )
    print("      Cargo.lock 仍是 v3（cargo 1.75 可读）")


@test
def test_cli_lists_a_real_selfhost_file():
    """真实自举源：参数要列全，而且目录层的说明/分组要合进去。"""
    if _cargo() is None:
        print("      SKIP: 无 cargo")
        return
    r = _cli(["list", REAL])
    assert r.returncode == 0, f"list 失败: {r.stderr[-800:]}"
    out = r.stdout
    for needle in ("语言环境", "CG_MAXFN", "代码生成表布局", "偏移", "loment-studio set"):
        assert needle in out, f"list 输出里没有 {needle!r}"
    print("      真实文件：维度 + 常量 + 目录分组都在")


@test
def test_cli_dry_run_is_surgical():
    """`set --dry-run` 只换值；注释、排版、文件本身都不动。"""
    if _cargo() is None:
        print("      SKIP: 无 cargo")
        return
    before = (ROOT / DEMO).read_text(encoding="utf-8")
    r = _cli(["set", DEMO, "const:MAX_ITEMS", "128", "--dry-run"])
    assert r.returncode == 0, f"set 失败: {r.stderr[-800:]}"
    assert "+ const MAX_ITEMS: u32 = 128;" in r.stdout, r.stdout
    assert "队列上限" in r.stdout, "注释应当留在 diff 里"
    assert (ROOT / DEMO).read_text(encoding="utf-8") == before, "--dry-run 不许写盘"
    print("      --dry-run: 只改一个值，文件未动")


@test
def test_matrix_reports_conflicts():
    """环境矩阵：12 个组合、6 个合法，冲突带理由。"""
    if _cargo() is None:
        print("      SKIP: 无 cargo")
        return
    r = _cli(["matrix", DEMO, "--json"])
    assert r.returncode == 0, f"matrix 失败: {r.stderr[-800:]}"
    d = json.loads(r.stdout)
    cells = d["cells"]
    assert len(cells) == 12, len(cells)
    valid = [c for c in cells if c["valid"]]
    assert len(valid) == 6, len(valid)
    bad = [c for c in cells if not c["valid"]]
    assert any("运行期" in (c.get("reason") or "") for c in bad), "冲突理由要说得出来"
    print("      12 个组合 / 6 合法；冲突带理由")


@test
def test_examples_pass_lomentc_check():
    """示例文件本身都要是合法 Loment（含能力域那一份）。"""
    files = sorted((STUDIO / "examples").glob("*.lomt"))
    assert files, "examples/ 里没有 .lomt"
    for p in files:
        mod, deps = lomentc.load_unit(p, ROOT)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{p.name} 有语义错: {errs}"
    print(f"      {len(files)} 份示例通过 lomentc.check")


@test
def test_frontend_assets_are_wired():
    """页面引用的每个本地资源都在；JS 过一遍语法检查。"""
    index = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js"):
        assert name in index, f"index.html 没有引用 {name}"
        assert (WEB / name).is_file(), f"缺少 web/{name}"
    node = shutil.which("node")
    if node is None:
        print("      SKIP: 无 node（只检查了文件存在）")
        return
    r = _run([node, "--check", str(WEB / "app.js")], WEB)
    assert r.returncode == 0, f"app.js 语法错: {r.stderr[-1200:]}"
    print("      app.js 语法通过 node --check")


def main() -> int:
    ok = 0
    for fn in TESTS:
        print(f"[TEST] {fn.__name__}")
        try:
            fn()
            ok += 1
        except AssertionError as e:
            print(f"      FAIL: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"      ERROR: {type(e).__name__}: {e}")
    print(f"{ok}/{len(TESTS)} passed")
    return 0 if ok == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
