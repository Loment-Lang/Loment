#!/usr/bin/env python3
"""lvm_twin_test.py — LVM 自举镜像的**孪生判据**（`docs/243` §5）。

**同一份 `.ll` 喂两个实现** —— `tools/lvm.py`（参考，进程内跑）与
`loment/tools/lvm.lomt`（自举镜像，**走种子自举链**编成 ELF 后跑）—— 比
**stdout 的字节 + 退出码**。

与 `loment_elf_test` 里那对 `lomelf`/`lomelf.lomt` 是**两种活**：那一格比产出的 ELF
**逐字节**，这一格比**跑出来的行为**。它同时也是"自举链把 `lvm.lomt` 编得对不对"的
判据 —— 镜像的输入是**另一个程序**，任何一处编错都会从行为上露出来。

⚠ **孪生判据只证"两边一致"，不证"对"**（`docs/158`、以及那条记忆）。"这一份产物本身
对不对"由 `tools/lvm_test.py` 管 —— 它比的是 **原生产物**。

链条里 clang 只出现在"种子 → stage1"一步（genesis，`docs/167` §5 记账）。没有 clang
就 SKIP：这条判据在**没有 clang 的机器上不成立**，但它也**不假装成立**。

用法: python tools/lvm_twin_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]



#: 语料**复用 `loment_elf_test.CORPUS`，不另抄一份** —— 抄了必然漂。
#: 其中 `tools/lvm.py` 自己跑不了的那几份（`lvm.LVM_KNOWN_GAPS`：要 257/63/39 号
#: 系统调用）在这里**自动出局** —— 参考都跑不了，就没有"两边一致"可谈。
CORPUS = ELF.CORPUS

#: **棘轮**：参考跑得了、而镜像跑不了或对不上的那些。**只许变短** ——
#: 每条都要写清"为什么"，哪天它跑通了这条判据会红，逼着把条目删掉。今天是**空的**。
TWIN_GAPS: dict[str, str] = {}

_MIRROR: Path | None = None
_MIRROR_TD: tempfile.TemporaryDirectory | None = None


def _clang() -> str | None:
    return ELF._clang()


def _wsl() -> bool:
    if sys.platform != "win32":
        return False
    return shutil.which("wsl") is not None


def _build(seq: list[list[str]], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(seq, capture_output=True, timeout=timeout, shell=False)


def _mirror() -> Path:
    """`loment/tools/lvm.lomt` 的产物 —— **能替用户干活的那份**解释器。

    种子 ->(clang 一次)-> stage1 ->(自举, 无 Python 无 clang)-> 镜像。
    整条链**只跑一次**并在本进程内缓存（三段自举编译每次一分多钟, 几条判据各建一遍是纯浪费）。

    ⚠ **链接那一步也在宿主上用同一个 clang**（`--target` 出 Linux ELF）—— 别指望 WSL 里
    也有 clang：本机就是这样（宿主有 `C:\Program Files\LLVM`, WSL 里 `clang: command not found`）。
    """
    global _MIRROR, _MIRROR_TD
    if _MIRROR is not None:
        return _MIRROR
    clang = _clang()
    assert clang, "缺 clang"
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    _MIRROR_TD = tempfile.TemporaryDirectory()
    td = Path(_MIRROR_TD.name)
    s1 = td / "stage1"
    r = _build([clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
                "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)])
    assert r.returncode == 0, f"种子编不出来: {r.stderr[-300:]!r}"

    if sys.platform == "win32":
        binn = f"/tmp/lvm_s1_{os.getpid()}.bin"
        # 二进制先拷进 WSL 自己的文件系统：`/mnt/d` 上就地覆盖后 WSL 会**拿旧的**，
        # 症状是"改了源码但跑的是上一版"，而且不报任何错。
        rr = _build([*_WSLCMD, "bash", "-lc",
                     f"rm -f {binn}; cp {ELF._wsl_path(s1)} {binn}; chmod +x {binn}; "
                     f"cd {ELF._wsl_path(ROOT)} && timeout 900 {binn} loment/tools/lvm.lomt"])
    else:
        rr = _build([str(s1), "loment/tools/lvm.lomt"])
    assert rr.returncode == 0, f"stage1 编译镜像失败: {rr.stderr[-300:]!r}"
    mir_ll = td / "lvm.ll"
    mir_ll.write_bytes(rr.stdout)
    assert len(rr.stdout) > 100000, f"镜像 IR 太小 ({len(rr.stdout)}B)"

    mir = td / "lvm.bin"
    r2 = _build([clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
                 "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start",
                 str(mir_ll), "-o", str(mir)])
    assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]!r}"
    _MIRROR = mir
    return mir


def _mirror_input_path(ll: Path) -> str:
    """镜像**看到的**输入路径 —— 与 `_run_mirror` 给它的那个实参**必须是同一个字符串**。

    这不是照抄一遍：`docs/243` §3.4 之下，这个字符串就是**被解释程序的 argv[0]**
    （镜像把它当"程序自己的名字"合成进 `/proc/self/cmdline`）。所以参考那侧也得**用
    这个名字**跑 —— 否则两边量到的根本不是同一个输入。
    """
    if sys.platform != "win32":
        return str(ll)
    return f"/tmp/lvm_twin_{os.getpid()}.ll"


def _run_mirror(mir: Path, ll: Path, prof: Path | None = None,
                timeout: int = 180) -> tuple[int, bytes, bytes]:
    """跑镜像 -> `(退出码, stdout 字节, 剖面字节)`。**Linux 上直接跑；Windows 上借 WSL**。

    给了 `prof` 就把它作为第二个实参传进去（T1，`docs/247`），并把**剖面**也要回来 ——
    判据 E 比的就是那一段字节。

    ⚠ 两边都先把**二进制与输入**拷进自己的临时区：`/mnt/d` 上就地覆盖后 WSL 会**拿旧的**
    （drvfs 有缓存），症状是"改了源码但跑的是上一版"，而且**不报任何错**。

    ⚠ 递给镜像的那个输入路径**就是被解释程序的 argv[0]**（`docs/243` §3.4），所以它与
    `_mirror_input_path` **必须一致** —— 改了这里那句 `{tag}.ll` 就要改那边。
    """
    if sys.platform != "win32":
        args = [str(mir), str(ll)] + ([str(prof)] if prof is not None else [])
        r = subprocess.run(args, capture_output=True, timeout=timeout, shell=False)
        p = prof.read_bytes() if prof is not None and prof.exists() else b""
        return r.returncode, r.stdout, p
    tag = f"/tmp/lvm_twin_{os.getpid()}"
    outp = Path(tempfile.gettempdir()) / "lvm_twin_out.bin"
    pout = Path(tempfile.gettempdir()) / "lvm_twin_prof.bin"
    arg2 = f"{tag}.prof" if prof is not None else ""
    script = (f"rm -f {tag}.bin {tag}.ll {tag}.out {tag}.prof; "
              f"cp {ELF._wsl_path(mir)} {tag}.bin && cp {ELF._wsl_path(ll)} {tag}.ll && "
              f"chmod +x {tag}.bin && cd /tmp && "
              f"timeout {timeout} {tag}.bin {tag}.ll {arg2} > {tag}.out 2>/dev/null; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 180, shell=False)
    got = _build([*_WSLCMD, "bash", "-lc", f"base64 -w0 {tag}.out"])
    import base64  # noqa: PLC0415
    outp.write_bytes(base64.b64decode(got.stdout))
    p = b""
    if prof is not None:
        g2 = _build([*_WSLCMD, "bash", "-lc", f"base64 -w0 {tag}.prof 2>/dev/null || true"])
        if g2.stdout.strip():
            p = base64.b64decode(g2.stdout)
            pout.write_bytes(p)
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, outp.read_bytes(), p


def _ref_ir(src: Path, td: Path) -> Path:
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    # ⚠ `newline="\n"`：本机 `write_text` 会写成 CRLF，而镜像的行解析按**行尾**判块边界。
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def test_twin_matches_reference() -> None:
    """判 A：同一份 IR，参考与自举镜像各跑一遍，比 stdout 字节 + 退出码。"""
    if not (_clang() and (sys.platform != "win32" or _wsl())):
        print("      SKIP: 没有 clang（这条判据要它把种子编成 stage1）")
        return
    import io  # noqa: PLC0415
    mir = _mirror()
    ran, gap, out_of_reach = [], [], []
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            src = ROOT / rel
            ll = _ref_ir(src, td)
            buf = io.BytesIO()
            prof = lvm.Profile()
            try:
                ref = (lvm.run_ir(ll.read_text(encoding="utf-8"), buf, prof,
                                  [_mirror_input_path(ll)]), buf.getvalue())
            except Exception as e:  # noqa: BLE001
                # 参考自己跑不了 —— 出局，不是缺口（`lvm_test.py` 那张棘轮管它）。
                out_of_reach.append((src.stem, str(e)[:56]))
                continue
            pfile = td / f"{src.stem}.prof"
            rc, out, pmir = _run_mirror(mir, ll, pfile)
            if (ref[0], ref[1]) != (rc, out):
                gap.append((src.stem, f"两边对不上: 参考 rc={ref[0]} {ref[1][:40]!r} / "
                                      f"镜像 rc={rc} {out[:40]!r}"))
                continue
            # **判 E（T1）**：剖面逐字节。这是 VM 第一件**两边能直接比字节**的产物 ——
            # 在此之前，孪生只能比行为（两边跑的是同一份 IR，中间产物没有可比性）。
            pref = prof.text().encode("utf-8")
            if pref != pmir:
                a = pref.decode().split("\n")
                b = pmir.decode("utf-8", "replace").split("\n")
                k = next((i for i in range(max(len(a), len(b))) if a[i:i + 1] != b[i:i + 1]), -1)
                gap.append((src.stem, f"剖面不同 @第 {k} 行: 参考 {a[k:k + 1]} / 镜像 {b[k:k + 1]}"))
                continue
            ran.append(src.stem)
    # **棘轮**（两个方向都判）：没登记的分歧 ⇒ 红；登记了却**跑通了** ⇒ 也红。
    unregistered = [f"{s}: {w}" for s, w in gap if s not in TWIN_GAPS]
    assert not unregistered, ("这两条路对不上，而它们**没有登记**在 TWIN_GAPS 里：\n  "
                              + "\n  ".join(unregistered))
    for stem, why in TWIN_GAPS.items():
        assert any(s == stem for s, _ in gap), (
            f"{stem}: 登记在 TWIN_GAPS 里，但它**已经对上了** —— 请删掉那条登记（{why[:60]}…）")
    print(f"      两边逐字节相同（stdout + rc + **剖面**）: {len(ran)}/{len(ran)}"
          f"（{', '.join(ran)}）")
    if gap:
        print(f"      已知缺口（棘轮，只许变短）: {', '.join(s for s, _ in gap)}")
    if out_of_reach:
        print(f"      目标之外（参考实现自己也跑不了）: {', '.join(s for s, _ in out_of_reach)}")
    assert ran, "一份都没跑成 —— 判据没测到东西"


def test_corpus_is_the_elf_corpus() -> None:
    """语料是**同一份对象**，不是抄的第二份清单（抄了必然漂）。"""
    assert CORPUS is ELF.CORPUS, "语料被抄成第二份了"
    assert any(r.endswith("tour.lomt") for r in CORPUS), CORPUS


def test_usage_is_refused() -> None:
    """CLI：没有实参 ⇒ rc=2 且点名用法；读不了输入 ⇒ rc=2。"""
    if not (_clang() and (sys.platform != "win32" or _wsl())):
        print("      SKIP: 没有 clang")
        return
    mir = _mirror()
    if sys.platform != "win32":
        r = subprocess.run([str(mir)], capture_output=True, timeout=60, shell=False)
        assert r.returncode == 2, r.returncode
    else:
        tag = f"/tmp/lvm_use_{os.getpid()}"
        script = (f"rm -f {tag}.bin; cp {ELF._wsl_path(mir)} {tag}.bin && chmod +x {tag}.bin && "
                  f"cd /tmp && ./{os.path.basename(tag)}.bin > /dev/null 2>&1; echo -n $?")
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                           capture_output=True, timeout=180, shell=False)
        assert r.stdout.decode().strip() == "2", r.stdout
    with tempfile.TemporaryDirectory() as tds:
        missing = Path(tds) / "nope.ll"
        missing.write_text("", encoding="utf-8")
        rc, _o, _p = _run_mirror(mir, missing, timeout=60)
        assert rc == 2, f"空输入应当 rc=2，得到 {rc}"
    print("      没有实参 / 读不了输入 ⇒ 都是 rc=2")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_twin_test")


if __name__ == "__main__":
    sys.exit(main())
