#!/usr/bin/env python3
# lomentlings.py — 练习册的运行器（Rustlings 的 Loment 版）
#
# 每个练习是一个目录 `exercises/NN-name/`：
#
#   main.lomt      学习者要改的文件（一开始编不过或跑错）
#   expect.txt     改对之后 stdout 应当逐字节等于它
#   solution.lomt  参考答案（运行器不读，除非 --solution）
#
# 用法:
#   python tools/lomentlings.py                # 按顺序跑一遍，报告
#   python tools/lomentlings.py 03             # 只跑 03
#   python tools/lomentlings.py --solution 03  # 打印 03 的答案
#   python tools/lomentlings.py --apply 03     # 把答案抄进 main.lomt
#
# 退出码 0 = 全部通过。

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "exercises"
IS_WIN = sys.platform == "win32"


def _exercise_dirs() -> list[Path]:
    return sorted(p for p in EX.iterdir()
                  if p.is_dir() and (p / "main.lomt").exists())


def _build(src: Path, td: Path):
    mod, deps = lomentc.load_unit(src, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        return None, errs[0]
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    if IS_WIN:
        blob, _ = lomelf.compile_pe(ir)
        exe = td / "main.exe"
    else:
        blob, _ = lomelf.compile_ll(ir)
        exe = td / "main.elf"
    exe.write_bytes(blob)
    if not IS_WIN:
        exe.chmod(0o755)
    return exe, None


def run_one(d: Path) -> tuple[bool, str]:
    want = (d / "expect.txt").read_bytes()
    with tempfile.TemporaryDirectory(prefix="lomlings_") as tds:
        td = Path(tds)
        try:
            exe, err = _build(d / "main.lomt", td)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        if exe is None:
            return False, str(err)
        try:
            r = subprocess.run([str(exe)], capture_output=True, timeout=60, shell=False)
        except subprocess.TimeoutExpired:
            return False, "运行超时"
        if r.returncode != 0:
            return False, f"退出码 {r.returncode}（stdout={r.stdout[:80]!r}）"
        if r.stdout != want:
            return False, f"stdout 不符：期望 {want!r}，实际 {r.stdout!r}"
    return True, "ok"


def _solution_ok(d: Path) -> tuple[bool, str]:
    """给判据用：编答案、跑、与 expect.txt 逐字节比。"""
    want = (d / "expect.txt").read_bytes()
    with tempfile.TemporaryDirectory(prefix="lomlings_") as tds:
        exe, err = _build(d / "solution.lomt", Path(tds))
        if exe is None:
            return False, str(err)
        r = subprocess.run([str(exe)], capture_output=True, timeout=60, shell=False)
        if r.returncode != 0:
            return False, f"退出码 {r.returncode}"
        if r.stdout != want:
            return False, f"stdout {r.stdout!r} != {want!r}"
    return True, "ok"


def check_solutions(dirs: list[Path]) -> int:
    bad = 0
    for d in dirs:
        sol = d / "solution.lomt"
        want = (d / "expect.txt").read_bytes()
        with tempfile.TemporaryDirectory(prefix="lomlings_") as tds:
            td = Path(tds)
            exe, err = _build(sol, td)
            if exe is None:
                print(f"FAIL  {d.name} solution: {err}")
                bad += 1
                continue
            r = subprocess.run([str(exe)], capture_output=True, timeout=60, shell=False)
            if r.returncode != 0 or r.stdout != want:
                print(f"FAIL  {d.name} solution: rc={r.returncode} stdout={r.stdout!r} want={want!r}")
                bad += 1
            else:
                print(f"PASS  {d.name} solution")
    print(f"\n答案: {len(dirs)-bad}/{len(dirs)} 通过")
    return 0 if bad == 0 else 1


def main(argv: list[str]) -> int:
    dirs = _exercise_dirs()
    if "--check-solutions" in argv:
        return check_solutions(dirs)
    if not dirs:
        print("lomentlings: exercises/ 下没有练习")
        return 2
    sel = None
    for a in argv:
        if a.isdigit():
            sel = a
    if "--solution" in argv or "--apply" in argv:
        if sel is None:
            print("用法: lomentlings --solution NN")
            return 2
        d = next((p for p in dirs if p.name.startswith(sel)), None)
        if d is None:
            print(f"lomentlings: 没有编号 {sel} 的练习")
            return 2
        sol = (d / "solution.lomt").read_text(encoding="utf-8")
        if "--apply" in argv:
            (d / "main.lomt").write_text(sol, encoding="utf-8", newline="\n")
            print(f"已把答案抄进 {d.name}/main.lomt")
        else:
            print(sol)
        return 0
    ok = 0
    failed = []
    for d in dirs:
        if sel is not None and not d.name.startswith(sel):
            continue
        good, why = run_one(d)
        print(f"{'PASS' if good else 'FAIL'}  {d.name}" + ("" if good else f"  — {why}"))
        if good:
            ok += 1
        else:
            failed.append(d.name)
    total = ok + len(failed)
    print(f"\n{ok}/{total} 通过" + ("" if not failed else "；先修第一个失败的，再看下一个"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
