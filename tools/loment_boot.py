#!/usr/bin/env python3
# loment_boot.py — Loment 程序在 FujoOS 用户态运行 (M67/M76/M77/M78, docs/149)
#
# 流程: .lomt --(lomentc)--> .ll --(lomelf, 仓库自己的原生后端)--> ELF
#       --(fujopack pack)--> FUJR 容器 --(QEMU -kernel + -append fujo.run=NAME)-->
#       串口输出断言。
#
# 用法:
#   python tools/loment_boot.py FILE [--needle TEXT] [--timeout 40]
# 退出码: 0 = 出现期望输出 / 1 = 未出现 / 2 = 构建失败。

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "loment" / "build"


def _tool(name: str, fallback: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    if Path(fallback).exists():
        return fallback
    raise SystemExit(f"[ERR] 找不到 {name}")


def build_elf(src: Path, out: Path) -> Path:
    mod, deps = lomentc.load_unit(src, ROOT)        # 唯一入口（docs/182 §1.10）
    errs = lomentc.check(mod, deps=deps)
    if errs:
        print(f"[ERR] {src}: {errs[0]}", file=sys.stderr)
        raise SystemExit(2)
    ll = out.with_suffix(".ll")
    text = lomentc.emit_llvm(mod, ROOT, deps)
    # `newline="\n"` 见 loment_build.py 里的说明: 默认的平台转换会与 `eol=lf` 打架
    ll.write_text(text, encoding="utf-8", newline="\n")
    # 链接交给仓库自己的原生后端（tools/lomelf.py）—— 不再经 clang
    sys.path.insert(0, str(ROOT / "tools"))
    import lomelf
    out.write_bytes(lomelf.compile_ll(text)[0])
    return out


def pack_initrd(elf: Path, name: str, out: Path) -> Path:
    # `#1786`：原先调 `tools/fujorun.py` —— 那个文件**从来不在树里**（`git log` 是空的），
    # 于是每一次 `loment_boot.py FILE` 都在这一步死掉，文档里那条"一键"的路整条不可达。
    # 树里有的是 `tools/fujopack.py`，它写的就是内核 `fujr.rs` 嗅探的 FUJR 容器。
    packer = ROOT / "tools" / "fujopack.py"
    if not packer.exists():
        print(f"[ERR] 缺 {packer.relative_to(ROOT)} —— 打不出 initrd；先把它补回来",
              file=sys.stderr)
        raise SystemExit(2)
    r = subprocess.run(
        [sys.executable, str(packer), "pack", "-e", str(elf),
         "--name", name, "-o", str(out)], capture_output=True, text=True, shell=False)
    if r.returncode:
        print(r.stdout + r.stderr, file=sys.stderr)
        raise SystemExit(2)
    return out


def run_qemu(initrd: Path, name: str, needle: str, timeout_s: float) -> bool:
    qemu = shutil.which("qemu-system-x86_64")
    if not qemu:
        print("[SKIP] 无 qemu-system-x86_64")
        return True
    kernel = ROOT / "kernel" / "fujo-kernel.bin"
    if not kernel.exists():
        print(f"[SKIP] 缺 {kernel.relative_to(ROOT)} (先构建内核)")
        return True
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "serial.log"
        p = subprocess.Popen(
            [qemu, "-m", "256M", "-kernel", str(kernel), "-initrd", str(initrd),
             "-append", f"fujo.run={name}", "-serial", f"file:{log}",
             "-display", "none", "-no-reboot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        seen = False
        while time.time() - t0 < timeout_s:
            time.sleep(0.5)
            if log.exists() and needle in log.read_text(errors="replace"):
                seen = True
                break
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        if not seen and log.exists():
            tail = log.read_text(errors="replace").splitlines()[-6:]
            print(" 串口尾部: " + " | ".join(tail))
        return seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_boot", description="Loment 程序在 FujoOS 里跑")
    ap.add_argument("file")
    ap.add_argument("--needle", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--timeout", type=float, default=40.0)
    a = ap.parse_args(argv)
    src = Path(a.file)
    name = a.name or src.stem
    needle = a.needle or f"{name.upper()} RESULT: PASS"
    elf = build_elf(src, BUILD / f"{name}.elf")
    initrd = pack_initrd(elf, name, BUILD / f"{name}.initrd")
    ok = run_qemu(initrd, name, needle, a.timeout)
    print(f"[{'PASS' if ok else 'FAIL'}] {src.name} -> {needle}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
