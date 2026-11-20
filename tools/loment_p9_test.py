#!/usr/bin/env python3
# loment_p9_test.py — P9 生态与平台自检 (M89/M92, docs/151)
#
# 运行: python tools/loment_p9_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _objdump() -> str | None:
    p = shutil.which("llvm-objdump")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\llvm-objdump.exe"
    return fallback if Path(fallback).exists() else None


# ---------------------------------------------------------------- M89 SDK 示例集

@test
def test_m89_sdk_examples():
    """M89: 示例集 ≥10 且每个都能通过编译 (形式对象 + IR 可选)。"""
    srcs = sorted(EX.glob("*.lomt"))
    assert len(srcs) >= 10, len(srcs)
    ok = 0
    for p in srcs:
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, (p.name, errs[:1])
        lomentc.emit_potato(mod, ROOT, deps)
        ok += 1
    print(f"      {ok} 个示例全部通过")
    assert ok == len(srcs)


# ---------------------------------------------------------------- M92 aarch64 交叉

@test
def test_m92_aarch64_cross_compile():
    """M92: 同一份 IR 交叉编译到 aarch64 (执行需 qemu-aarch64, 本机没有 -> 部分)。"""
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    mod = lomentc.load(EX / "native.lomt")
    deps = lomentc.resolve_deps(mod, ROOT, EX, entry=EX / "native.lomt")
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "n.ll"
        f.write_text(ll, encoding="utf-8")
        obj = Path(td) / "n.o"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "--target=aarch64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-c", str(f), "-o", str(obj)], capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        dump = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-f", "-d", str(obj)], capture_output=True, text=True, shell=False).stdout
    assert "aarch64" in dump, dump[:200]
    assert "ret" in dump, "反汇编里没有 aarch64 的 ret"
    if not shutil.which("qemu-aarch64"):
        print("      SKIP: 无 qemu-aarch64, 交叉产物未执行 (M92 部分)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_p9_test")


if __name__ == "__main__":
    sys.exit(main())
