#!/usr/bin/env python3
# loment_rel_test.py — Loment 版发布清单与 Python 版逐字节相同 (docs/167 Stage 3)
#
# 判据: `loment/tools/lomrel.lomt` (链成 ELF 后跑) 与 `tools/loment_release.py`:
#   1. 无参数 (门禁模式) 与 `--check`: stdout / stderr / 退出码逐字节相同;
#   2. `--emit PATH`: 写出的清单逐字节相同, stdout 也相同;
#   3. `--checksums PATH`: 写出的清单逐字节相同 (路径不同则比内容), stdout 除路径外相同;
#   4. (不给路径的) `--emit` 写的是仓库那条默认清单 —— 两侧点名同一条;
#   5. `lomrel.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# **2/3 两条都喂进程私有路径** (`_rel_*-<pid>.json` / `.sum`, 见各自用例): 它们原先改写的是
# 仓库里那条**共享**的 `loment/build/release-manifest.json`（写完再还原）。并发时那是个
# 窗口：同片任何一条读它的判据都可能读到**半截文件**。两侧现在都认 `--emit PATH`
# (与本来就认路径的 `--checksums` 同一形状), 所以"真落盘 + 比字节"一条都没少,
# 而共享路径一个字节都不碰 —— 与 `docs/181` §2 那条纪律同源。
#
# 平台口径 (重要): 清单次序**必须与平台无关** —— 两边都按 **posix 路径的字节序**排。
# `sorted(Path)` 不能直接用: 它在 Windows 上按 normcase 小写比、在 Linux 上按字节比,
# 同一个 glob 会排出两种次序 (实测: `loment/build/genesis/` 下 `SHA256SUMS` 与
# `lomelf-linux-x64.elf` 谁在前, 两种平台正好相反)。次序不一致不是"好不好看":
# 本机重算的清单与 CI 重算的永远对不上 ⇒ CI 每次推一笔 `[生成物]` ⇒ 那笔机器人推送把
# 必需检查挂成 `action_required` ⇒ PR 永远 BLOCKED。参考侧见 `loment_release.build`
# 的注释, 自举侧见 `lomrel.lomt` 的 `cmp_bytes`。
#
# 运行: python tools/loment_rel_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os
import re

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_release  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomrel.lomt"
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


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomrel.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomrel.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomrel.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _norm(v: str) -> str:
    """只归一路径分隔符 (Windows 的 Python 打印 docs\\x, Linux 侧是 docs/x)。"""
    return v.replace(chr(92), "/")


def _py(args: list[str]) -> tuple[int, str, str]:
    """进程内跑 Python 版并捕获两路输出 (见 loment_pkg_test 的同一理由: 避免换行翻译)。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_release.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str], timeout: int = 600) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomrel_{name}.bin"
    quoted = " ".join(args)
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {quoted} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=timeout, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, err


def _pair(elf: Path, td: Path, name: str, args: list[str]) -> None:
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, args)
    if (wr, _norm(wo), _norm(we)) == (gr, go, ge):
        return
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n"
        f"  py     out={_norm(wo)[-200:]!r} err={_norm(we)[-200:]!r}\n"
        f"  loment out={go[-200:]!r} err={ge[-200:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_manifest_order_is_platform_independent():
    """清单的次序**每个 glob 组内按 posix 路径的字节序** —— 不是"哪个平台顺手就哪个"。

    **Why**：次序不一致的代价不是好看：**本机重算的清单与 CI 重算的永远对不上**，
    CI 就会每次都推一笔 `[生成物]`，而机器人推送会把必需检查挂成 `action_required`
    ⇒ PR 永远 BLOCKED（`docs/` 里记着这条坑，2026-09-25 又踩了一次）。

    **How to apply**：按 `GLOBS` 把清单**分组**，逐组核"组内是字节序"（组与组之间是
    `GLOBS` 的手写序，那是刻意的，不核）。期望值在这儿**独立算**（`sorted(key=str)`）。
    旧写法（`sorted(Path)` 在 Windows 上按 normcase 折叠）会让
    `loment/build/genesis/lomelf-linux-x64.elf` 排在 `.../SHA256SUMS` 前面，
    这条当场红（实测两种次序确实相反）。

    **量的是生成器的输出，不是仓库里那份台账**（2026-10-16 改）。台账从这一天起由
    `main` 上的单一写者刷新（`.github/workflows/manifest.yml` 的 `emit-main`），
    所以**在一棵 PR 的树上它是落后的** —— 拿它判，一个新增了文件的 PR 会看到
    `got` 与 `want` 错位，报出来的却是"次序错了"，那是**假红**。
    """
    got = [x["path"] for x in loment_release.build()["files"]]
    pos = 0
    for g in loment_release.GLOBS:
        want = sorted(p.relative_to(ROOT).as_posix()
                      for p in ROOT.glob(g) if p.is_file())
        chunk = got[pos:pos + len(want)]
        assert chunk == want, (
            f"`{g}` 这一组在清单里的次序不是字节序（多半是 `sorted(Path)` 的平台语义"
            f"漏了回来）：\n  清单 {chunk[:6]}\n  字节序 {want[:6]}")
        pos += len(want)
    assert pos == len(got), f"清单条目数 {len(got)} 与 GLOBS 展开的 {pos} 对不上"


_GL = re.compile(r'fn globs_text\(\) -> str \{\s*return "(.*?)";', re.S)


def _lomrel_globs() -> list[str]:
    """从 `lomrel.lomt` 的 `globs_text()` 里读那份 glob 清单（一条一行）。

    **从源码里读，不另抄一份** —— 抄一份就是又一处会漂的清单，正是下面那条判据要防的东西。
    提取口径：字符串字面量里是**真的换行**（Loment 里没写 `\\n` 转义），收尾是 `";`。
    """
    m = _GL.search(SRC.read_text(encoding="utf-8"))
    assert m, f"在 {SRC.name} 里找不到 `fn globs_text()` 的字符串字面量"
    lines = m.group(1).split("\n")
    if lines and lines[-1] == "":       # 字面量末尾那个换行
        lines.pop()
    return lines


@test
def test_lomrel_globs_match_python_globs():
    """两处清单**逐条同序同名**：`loment_release.GLOBS` 与自举那份 `globs_text()`。

    **Why**：这是 `--emit` / `--checksums` / `--check` 三条"比两侧字节"的前提，而
    **此前没有一条判据在核它** —— `loment_tools_test::test_every_tool_is_in_the_release_manifest`
    只在**报错信息**里写着"要同步"，它比的是"工具在不在 `GLOBS` 里"，比不到自举那一份。
    2026-10-10 实测：`f262a67`（逃生舱·粒度 B）把 `tools/loment_opt_obj.py` 加进 `GLOBS`
    却漏了 `lomrel.lomt`，一路漂到 `main`：两侧工件数 539 / 538。

    **CI 上看不见，所以这条必须是纯 Python**：会抓它的那三条判据全要 WSL（编 lomrel 再跑），
    ubuntu 的 runner 上 SKIP —— 漏项因此漂过去了。本判据不编、不跑 WSL，在 CI 上真跑。

    **How to apply**：改 `GLOBS` 就改 `lomrel.lomt`，**同一位置**（顺序也是清单口径的一部分；
    两处插在不同位置会给出"同集合不同顺序"的两份清单，`--emit` 报字节不同而工件数一样）。
    """
    py, sh = list(loment_release.GLOBS), _lomrel_globs()
    if py == sh:
        print(f"      两处清单一致 ({len(py)} 条)")
        return
    ipy, ish = set(py), set(sh)
    first = next((i for i, (a, b) in enumerate(zip(py, sh)) if a != b), min(len(py), len(sh)))
    a = py[first] if first < len(py) else "<无>"
    b = sh[first] if first < len(sh) else "<无>"
    raise AssertionError(
        f"两侧发布清单不一致: GLOBS {len(py)} 条 / lomrel {len(sh)} 条。\n"
        f"  首个不同在第 {first + 1} 条: GLOBS={a!r} lomrel={b!r}\n"
        f"  只在 GLOBS: {[g for g in py if g not in ish][:8]}\n"
        f"  只在 lomrel: {[g for g in sh if g not in ipy][:8]}\n"
        f"  改法: 两处在**同一位置**增删同一行 (`tools/loment_release.py` 的 `GLOBS` 与 "
        f"`{SRC.name}` 的 `globs_text()`)。")


@test
def test_lomrel_matches_python():
    """无参数 (门禁模式) 与 `--check`: stdout / stderr / 退出码逐字节相同。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        _pair(elf, td, "noargs", [])
        _pair(elf, td, "check", ["--check"])
    print("      无参数 / --check: stdout + stderr + 退出码一致")


@test
def test_lomrel_emit_writes_same_bytes():
    """`--emit PATH`: 写出的清单逐字节相同, stdout 也相同 —— **落在进程私有路径上**。

    **Why 私有路径**：这一段原先喂的是仓库那条 `loment/build/release-manifest.json` ——
    两侧轮流**改写共享文件**, 完了在 `finally` 里还原。写出来的内容虽然与原来一致,
    但写的那一刻是**truncate + write**：同片任何一条读它的判据都可能读到半截/空文件
    （`docs/181` §2 那条纪律治的就是这个形状）。两侧都认 `--emit PATH` 之后,
    这里照样比**真落盘的字节**, 却一个字节都不碰共享路径。

    **默认路径那件事没有因此漏掉**：它由
    `test_emit_default_path_is_the_same_on_both_sides` 就地钉住（那一条不需要 WSL,
    在 CI 上真跑）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    out = ROOT / f"loment/build/_rel_manifest-{os.getpid()}.json"
    out_rel = out.relative_to(ROOT).as_posix()
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            rc, po, pe = _py(["--emit", out_rel])
            assert rc == 0, f"python --emit 失败 rc={rc}"
            py_bytes = out.read_bytes()
            gr, go, ge = _run(elf, td, "emit", ["--emit", out_rel])
            sh_bytes = out.read_bytes()
            assert (rc, _norm(po), _norm(pe)) == (gr, go, ge), (
                f"stdout 不一致:\n  py {po!r} {pe!r}\n  sh {go!r} {ge!r}")
            assert py_bytes == sh_bytes, f"落盘不同: {len(py_bytes)}B vs {len(sh_bytes)}B"
            # **不再与仓库里那份比**（2026-10-16 撤掉）：台账由 `main` 上的单一写者刷新，
            # PR 的树上它就是落后的，拿它判等于在每一个 PR 上判红。这条判据要证的是
            # **两个独立实现给出同一份清单** —— 上面那一行就是它。
    finally:
        out.unlink(missing_ok=True)
    print("      --emit: 清单字节 + 输出行一致 (两个实现逐字节相同)")


@test
def test_emit_default_path_is_the_same_on_both_sides():
    """不给路径时 `--emit` 写哪儿 —— 两侧必须点名**同一条**。

    **Why 要有这一条**：`--emit PATH` 那条判据现在各写一份私有路径, "默认写仓库那条"
    就不再由它覆盖, 而自举侧的默认值是个**写死的字面量**, 且在两个分支各写一遍
    (`--emit` 一处写、一处回显; `--check` 两处)。少一处或多一处都没人会发现:
    `lomrel --emit` 会写到一条没人读的路径上, 门禁照样绿。这里就地把它钉成棘轮。

    不需要 WSL/clang, 所以在 ubuntu 的 runner 上**真跑** —— 这一份文件里那四条要 WSL 的
    判据在那上面全 SKIP, 那边实际只跑本条与另外两条纯 Python 的。
    """
    want = loment_release.OUT.relative_to(ROOT).as_posix()
    src = SRC.read_text(encoding="utf-8")
    got = src.count(f'"{want}"')
    assert got == 4, (
        f"`{SRC.relative_to(ROOT)}` 里 `{want}` 应正好 4 处"
        f"（`--emit` 与 `--check` 两个分支各两处: 一处 cstr 写、一处 wstr 回显）, 实际 {got} 处"
        f" —— 改了默认路径就两边一起改")
    print(f"      默认清单路径两侧同为 {want} (共 4 处)")


@test
def test_lomrel_checksums_same_bytes():
    """`--checksums`: 写出的清单逐字节相同 (喂同一个输出路径)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    #: **每进程一份**：固定文件名在并发时会让两条判据互相覆盖/读到半截 (`docs/181` §2)。
    out_rel = f"loment/build/_rel_cks-{os.getpid()}.sum"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        try:
            _pair(elf, td, "cks", ["--checksums", out_rel])
            # 落盘内容比一次 (两边都写了同一个路径, 后写的必须与前次的 Python 字节相同)
            r1, p1, _e1 = _py(["--checksums", out_rel])
            a = (ROOT / out_rel).read_bytes()
            r2, g2, _e2 = _run(elf, td, "cks2", ["--checksums", out_rel])
            b = (ROOT / out_rel).read_bytes()
            assert (r1, _norm(p1)) == (r2, g2), "stdout 不一致"
            assert a == b, "校验清单字节不同"
        finally:
            (ROOT / out_rel).unlink(missing_ok=True)
    print("      --checksums: 清单字节 + 输出行一致")


@test
def test_seed_fits_lomrel_normalization_buffer():
    """清单里最大的工件要装得进 `lomrel` 的**归一化副本** —— 越界是**静默**的，所以要有判据。

    2026-09-20 实测（`docs/200` §6）：`lomrel` 的 `M_NORM` 是固定的 2 MiB，而清单里有
    **比它大的工件**（自举种子）。越界写进紧挨着的 `M_CUR`（已装载的清单）——
    症状不是"这个文件的哈希错"，是**它之后每一条都对不上**：486 条里从种子那一条起
    226 条全红，而两边的**写**（`--emit`）还是逐字节相同的，所以看上去像"清单过期"。

    与 `loment_genesis_test::test_seed_fits_lomelf_input_buffer` 同一个形状：
    从 `lomrel.lomt` 里**读**上限，不另抄一份，并把余量打出来。
    """
    src = SRC.read_text(encoding="utf-8")
    m = re.search(r"const NORM_CAP: u32 = (\d+);", src)
    assert m, f"在 {SRC.name} 里找不到 `const NORM_CAP: u32 = <数>;`"
    cap = int(m.group(1))
    worst, worst_sz = "", 0
    for p in _manifest_paths():
        f = ROOT / p
        if f.is_file() and f.stat().st_size > worst_sz:
            worst, worst_sz = p, f.stat().st_size
    assert worst_sz < cap, (
        f"清单里最大的工件 {worst}（{worst_sz} B）超过 lomrel 的归一化上限 {cap} B。"
        f"**越界不报错**，会写坏已装载的清单（症状是「后面每一条都对不上」）—— "
        f"抬 `M_NORM` / `NORM_CAP` / `M_CUR` / `ARENA`，见 docs/200 §6")
    print(f"      最大工件 {worst} {worst_sz} B / 归一化上限 {cap} B, 余量 {cap - worst_sz}")


def _manifest_paths() -> list[str]:
    """清单里点名的相对路径 —— **问生成器要**，不读仓库里那份台账。

    2026-10-16 起台账由 `main` 上的单一写者刷新，PR 的树上它是落后的；容量那一条
    （下面 `test_..._fits_...`）量的正是"最大工件有没有超 `lomrel` 的归一化上限"，
    读一份落后的台账会让**新加的大文件**漏过这一条，直到 `main` 刷过一轮。
    """
    return [x["path"] for x in loment_release.build()["files"]]


@test
def test_lomrel_selfhost_compiles():
    """lomrel.lomt 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}lomrel_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomrel.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomrel.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 80000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomrel.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_rel_test")


if __name__ == "__main__":
    sys.exit(main())
