#!/usr/bin/env python3
# loment_opt_obj.py — 产出**预优化的 C-ABI 目标文件**（docs/212 §5 B：逃生舱的"带"）。
#
# 把一份 Loment 源编成 IR，再交给 **clang -O2** 编成一个 `.o`。那个 `.o` 是**自足的**
# （`--link` 的既有要求：no relocations / no undefined symbols），于是**另一台没装 clang 的
# 机器**只要拿到它的**字节**，就能 `loment build app.lomt --link k.o` 把它链进去。
#
# 这就是 Python 的 `numpy` wheel 那个形状：**编一次、按内容寻址分发、端机不需要编译器**。
# 与 `loment build --opt`（粒度 A：整程序走 clang）的分工：**A 每次都要 clang，B 让端机
# 不必** —— 代价是内核必须自足（不带 libc、不带未定义的 Loment 运行期符号）。
#
# 用法:
#     python tools/loment_opt_obj.py KERNEL.lomt -o KERNEL.o
#
# 退出码: 0 成功 / 2 用法 / 1 编译失败（**含"没有 clang"——点名，不静默**）

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
#: 与仓里其它地方同一张表（`loment_audit._clang_path` / `loment.py` 的 fallback）。
CLANG_CANDIDATES = ("clang", r"C:\Program Files\LLVM\bin\clang.exe")


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def emit_object(src: Path, out: Path) -> int:
    """`src` (Loment 源) -> `out` (预优化的 ELF `.o`)。失败点名。"""
    cc = _clang()
    if not cc:
        print("loment_opt_obj: no clang found -- producing an optimized object needs a C "
              "compiler; this is the *producer* side (consumers only need the .o bytes)",
              file=sys.stderr)
        return 1
    mod = lomentc.load(src)
    errs = lomentc.check(mod)
    if errs:
        print("; ".join(errs[:5]), file=sys.stderr)
        return 1
    ir = lomentc.emit_llvm(mod, ROOT)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        ll = Path(td) / "k.ll"
        ll.write_text(ir, encoding="utf-8", newline="\n")
        # 与 `loment build --opt` 同一目标/同一组 flag（docs/212 §5）—— 产物是 ELF，
        # 能被自举链接器 `lomelf` 的 `--link` 读进去。
        r = subprocess.run([cc, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                            "-ffreestanding", "-fno-pie", "-fno-stack-protector", "-O2",
                            "-c", str(ll), "-o", str(out)],
                           capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        print(r.stderr[:400], file=sys.stderr)
        return 1
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{out}  ({out.stat().st_size} B)  sha256={digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_opt_obj")
    ap.add_argument("src", help="Loment 内核源码 (.lomt)")
    ap.add_argument("-o", "--out", required=True, help="产出的目标文件 (.o)")
    a = ap.parse_args(argv)
    return emit_object(Path(a.src), Path(a.out))


if __name__ == "__main__":
    raise SystemExit(main())
