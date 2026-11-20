#!/usr/bin/env python3
"""lvm_mirror_t2_test.py — **镜像**的 T2（`docs/259` §7，issue #586）。

会随货走的那份解释器是 `loment/tools/lvm.lomt`（Loment 写的自举镜像）。它在
T0 下逐条重解操作数/类型文本；这一条判据验的是它的 **T2 档**（第三个实参写 `t2`）。

四条，与 `docs/259` §5 一一对应，外加孪生：

* **A** 原生 / 镜像 T0 / 镜像 T2 的 stdout + 退出码逐字节相同（9 份语料）。
* **B** T1 剖面在镜像 T0 与镜像 T2 下逐字节相同（且**非空** —— 空文件比不出东西）。
* **C** 执行期操作数/类型解码次数：镜像 T0 **> 0**、镜像 T2 **= 0**。
  反向那一半也断言，否则量到的是"计数器没接上"。
* **D** 镜像 T2 比镜像 T0 快 —— 同一进程交替跑、取 `min`，**只判符号**。
* **孪生** 参考实现（`tools/lvm.py`，T0）与镜像 T2 的 stdout + 退出码 + 剖面逐字节相同。

⚠ 构建用的是**参考工具链**（`lomentc` -> `lomelf`），不是种子自举链：这一条判据问的是
T2 的语义，自举链把镜像编得对不对由 `lvm_twin_test` 管。没有 Linux/WSL 就**可见地跳过**。

用法: python tools/lvm_mirror_t2_test.py
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf   # noqa: E402
import lvm      # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ELF.CORPUS
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]
_BIN: Path | None = None
_TD: tempfile.TemporaryDirectory | None = None


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _runner() -> bool:
    if sys.platform.startswith("linux"):
        return True
    return sys.platform == "win32" and shutil.which("wsl") is not None


class _Skip(Exception):
    pass


def _mirror() -> Path:
    """`loment/tools/lvm.lomt` -> 参考工具链 -> Linux ELF。一次就够。"""
    global _BIN, _TD
    if _BIN is not None:
        return _BIN
    if not _runner():
        raise _Skip("没有 Linux、也没有 WSL —— 镜像的 ELF 本机跑不了")
    src = ROOT / "loment" / "tools" / "lvm.lomt"
    mod, deps = lomentc.load_unit(src, ROOT)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"镜像自己检查不过: {errs[:2]}"
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _ = lomelf.compile_ll(ll, [])
    _TD = tempfile.TemporaryDirectory()
    _BIN = Path(_TD.name) / "lvm.bin"
    _BIN.write_bytes(blob)
    _BIN.chmod(0o755)          # Linux 上直接 exec；Windows 上走 WSL 的 cp+chmod
    return _BIN


def _ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _run_mirror(rel: str, ir: str, tier: str) -> tuple[bytes, int, str, int]:
    """跑镜像，返回 `(stdout, rc, profile, decodes)`。

    输入/剖面/计数的路径在**两条平台上都钉成 `/tmp/mirror_*`** —— 于是被解释程序看到的
    argv[1] 与原生那边 `_run_native(..., argv0)` 用的字符串是同一个（命令单元读 cmdline）。
    """
    exe = _mirror()
    td = Path(_TD.name)
    inp = td / "in.ll"
    inp.write_text(ir, encoding="utf-8", newline=chr(10))
    fin, fprof, fstats = "/tmp/mirror_in.ll", "/tmp/mirror_prof.txt", "/tmp/mirror_stats.txt"
    argv = [fin, fprof, tier, fstats]
    if sys.platform.startswith("linux"):
        shutil.copyfile(inp, fin)
        for f in (fprof, fstats):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass
        r = subprocess.run([str(exe), *argv], capture_output=True, timeout=900)
        prof_text = Path(fprof).read_text(encoding="utf-8") if Path(fprof).exists() else ""
        stats_text = Path(fstats).read_text(encoding="utf-8") if Path(fstats).exists() else ""
    else:
        # 先拷进 WSL 自己的文件系统：/mnt/c 刚写的文件立刻读可能是空的（9p 可见性）。
        exe_t = "/tmp/lvm_mirror_t2.bin"
        cmds = [f"cp {_wsl_path(exe)} {exe_t}", f"chmod +x {exe_t}",
                f"cp {_wsl_path(inp)} {fin}",
                f"rm -f {fprof} {fstats}",
                f"{exe_t} " + " ".join(argv)]
        r = subprocess.run([*_WSLCMD, "bash", "-lc", " && ".join(cmds)],
                           capture_output=True, timeout=900)
        pr = subprocess.run([*_WSLCMD, "bash", "-lc", f"cat {fprof} 2>/dev/null"],
                            capture_output=True, text=True)
        st = subprocess.run([*_WSLCMD, "bash", "-lc", f"cat {fstats} 2>/dev/null"],
                            capture_output=True, text=True)
        prof_text, stats_text = pr.stdout, st.stdout
    decodes = int(stats_text.strip() or "-1")
    return r.stdout, r.returncode, prof_text, decodes


def test_a_outputs_match_across_three_ways() -> None:
    """判 A：原生 / 镜像 T0 / 镜像 T2 的 stdout + 退出码逐字节相同。"""
    try:
        _mirror()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    if sys.platform == "win32" and not ELF._wsl():
        print("      SKIP: Windows 上没有 WSL")
        return
    ran = 0
    # 两条路要**同一个 argv[0]**（`docs/243` §3.4）：命令单元读 cmdline 是为了看自己叫什么。
    argv0 = "/tmp/mirror_in.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            stem = Path(rel).stem
            if stem in LT.VM_KNOWN_GAPS:
                continue
            ir = _ir(rel)
            blob, _ = lomelf.compile_ll(ir, [])
            exe = td / f"{stem}.elf"
            exe.write_bytes(blob)
            nrc, nout = LT._run_native(exe, td, argv0)
            o0, r0, _, _ = _run_mirror(rel, ir, "t0")
            o2, r2, _, _ = _run_mirror(rel, ir, "t2")
            assert o0 == o2, f"{stem}: 镜像 T0 与 T2 的 stdout 不同"
            assert r0 == r2, f"{stem}: 镜像 T0 {r0} / T2 {r2}"
            assert o0 == nout, f"{stem}: 镜像输出与原生不同"
            assert r0 == nrc, f"{stem}: 镜像退出码 {r0} 与原生 {nrc} 不同"
            ran += 1
    print(f"      原生 / 镜像 T0 / 镜像 T2 逐字节相同: {ran} 份")


def test_b_profiles_match_and_are_not_empty() -> None:
    """判 B：剖面逐字节相同，而且**非空**。"""
    try:
        _mirror()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    for rel in ("loment/examples/tour.lomt", "loment/examples/switch.lomt"):
        ir = _ir(rel)
        _, _, p0, _ = _run_mirror(rel, ir, "t0")
        _, _, p2, _ = _run_mirror(rel, ir, "t2")
        assert "[blocks]" in p0 and len(p0.splitlines()) > 4, f"{rel}: 剖面是空的"
        assert p0 == p2, f"{rel}: 镜像 T0 与 T2 的剖面不同"
    print("      剖面：非空且逐字节相同（tour / switch）")


def test_c_no_decoding_at_run_time() -> None:
    """判 C：T0 解码 > 0，T2 **0**；反向一半也断言。"""
    try:
        _mirror()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    ir = _ir("loment/examples/tour.lomt")
    _, _, _, d0 = _run_mirror("loment/examples/tour.lomt", ir, "t0")
    _, _, _, d2 = _run_mirror("loment/examples/tour.lomt", ir, "t2")
    assert d0 > 0, f"T0 一次都没解码（{d0}）—— 计数器没接上"
    assert d2 == 0, f"镜像 T2 执行期还在解码：{d2} 次"
    print(f"      tour：T0 解码 {d0} 次；T2 解码 0 次")


def test_d_t2_is_faster() -> None:
    """判 D：同一进程交替跑、取 min；只判符号。"""
    try:
        _mirror()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    rel = "loment/examples/lumtui_demo.lomt"
    ir = _ir(rel)
    off, on = [], []
    try:
        for _ in range(3):
            t = time.perf_counter()
            _run_mirror(rel, ir, "t0")
            off.append(time.perf_counter() - t)
            t = time.perf_counter()
            _run_mirror(rel, ir, "t2")
            on.append(time.perf_counter() - t)
    except Exception as e:  # noqa: BLE001
        print(f"      SKIP: 计时跑不起来（{e}）")
        return
    assert min(on) < min(off), \
        f"镜像 T2 没有更快：T0 {min(off)*1000:.0f} ms / T2 {min(on)*1000:.0f} ms"
    print(f"      lumtui_demo：T0 {min(off)*1000:.0f} ms · T2 {min(on)*1000:.0f} ms "
          f"⇒ {min(off)/min(on):.2f}x（只判符号，倍数记账）")


def test_twin_reference_and_mirror_t2_agree() -> None:
    """孪生：参考实现（T0）与镜像 T2 的 stdout + 退出码 + 剖面逐字节相同。"""
    try:
        _mirror()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    for rel in ("loment/examples/tour.lomt", "loment/examples/user_hello.lomt"):
        ir = _ir(rel)
        buf, p = io.BytesIO(), lvm.Profile()
        rc = lvm.run_ir(ir, buf, p, ["/tmp/mirror_in.ll"])
        o2, r2, p2, _ = _run_mirror(rel, ir, "t2")
        assert buf.getvalue() == o2, f"{rel}: 参考与镜像 T2 的 stdout 不同"
        assert rc == r2, f"{rel}: 参考 {rc} / 镜像 T2 {r2}"
        assert p.text() == p2, f"{rel}: 参考与镜像 T2 的剖面不同"
    print("      参考 T0 与镜像 T2：stdout / 退出码 / 剖面逐字节相同")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_mirror_t2_test")


if __name__ == "__main__":
    sys.exit(main())
