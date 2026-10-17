#!/usr/bin/env python3
# loment_pkg_test.py — Loment 版 lompkg 与 Python 版 stdout 逐字节相同 (docs/159 §4b)
#
# 判据: 同一棵包树喂 `loment/tools/lompkg.lomt`(链成 ELF 后跑) 与 `tools/lompkg.py`,
# **stdout 逐字节相同 + 退出码相同**。stdout 里没有路径 (只有 名字/版本/sha256 前 16 位),
# 所以两边可以喂各自平台的路径而比较结果不受影响 —— 这也是这条判据成立的前提。
#
# stderr 只要求两边都非空、不逐字节比: "依赖 X 不存在" 这类文案里含 Python 用
# Path.resolve() 解出来的绝对路径 (含符号链接解析), 与词法规范化路径不可能逐字节相同
# (见 loment/tools/lompkg.lomt 文件头"刻意偏离" 1)。
#
# 运行: python tools/loment_pkg_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import contextlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lompkg  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lompkg.lomt"
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
    assert not errs, f"lompkg.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lompkg.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lompkg.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


# ---------------------------------------------------------------- 包树

def _mk_pkg(d: Path, name: str, version: str, deps: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "pkg.json").write_text(
        json.dumps({"name": name, "version": version, "deps": deps}), encoding="utf-8")


def _mk_src(d: Path, rel: str, body: str) -> None:
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _tree(base: Path) -> dict[str, Path]:
    """线性链 + 嵌套子目录 + 空包 + 环 + 缺依赖 + 缺 name。"""
    _mk_pkg(base / "base", "base", "0.1.0", {})
    _mk_src(base / "base", "src/m.lomt", "module base\nfn f() -> u32 { return 1; }\n")
    _mk_pkg(base / "mid", "mid", "0.1.0", {"base": {"path": "../base"}})
    _mk_src(base / "mid", "src/m.lomt", "module mid\nfn f() -> u32 { return 1; }\n")
    _mk_pkg(base / "top", "top", "0.1.0", {"mid": {"path": "../mid"}})
    _mk_src(base / "top", "src/m.lomt", "module top\nfn f() -> u32 { return 1; }\n")
    # 嵌套: rglob 必须递归到 src/deep/
    _mk_pkg(base / "nest", "nest", "0.1.0", {})
    _mk_src(base / "nest", "src/deep/d.lomt", "module deep\nfn g() -> u32 { return 2; }\n")
    # 空包: 一个 *.lomt 都没有 -> sha256(b"<empty>")
    _mk_pkg(base / "empty", "empty", "0.1.0", {})
    # **多文件包**: 2026-09-15 补 —— 原先每包只有一个 .lomt, 于是 hid 了"文件排序方向"
    # 这类 bug (lompkg.lomt 侧实际排成了降序, 两实现的哈希在多文件包上不一致却一路绿)
    _mk_pkg(base / "multi", "multi", "0.1.0", {})
    _mk_src(base / "multi", "src/a.lomt", "module a\n")
    _mk_src(base / "multi", "src/b.lomt", "module b\n")
    _mk_src(base / "multi", "src/c.lomt", "module c\n")
    # 依赖环
    _mk_pkg(base / "cyc_a", "cyc_a", "0.1.0", {"cyc_b": {"path": "../cyc_b"}})
    _mk_pkg(base / "cyc_b", "cyc_b", "0.1.0", {"cyc_a": {"path": "../cyc_a"}})
    # 依赖不存在
    _mk_pkg(base / "miss", "miss", "0.1.0", {"nope": {"path": "../nope_dir"}})
    # 缺 name
    (base / "noname").mkdir()
    (base / "noname" / "pkg.json").write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
    return {n: base / n / "pkg.json" for n in
            ("base", "mid", "top", "nest", "empty", "multi",
             "cyc_a", "cyc_b", "miss", "noname")}


# ---------------------------------------------------------------- 两侧执行

def _py(args: list[str]) -> tuple[int, str]:
    """进程内跑 Python 版并捕获 stdout。

    必须**进程内**捕获: 走子进程重定向的话 Windows 文本模式会把 `\\n` 写成 `\\r\\n`,
    而 Loment 版 (Linux 目标) 吐的是 `\\n` —— 那比的是换行翻译, 不是工具差异。
    """
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = lompkg.main(args)
    except SystemExit as e:  # argparse 的用法错误 (退出码 2)
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue()


def _py_rc(args: list[str]) -> int:
    """只要退出码 (不把 Python 版的打印漏进门禁输出)。"""
    rc, _ = _py(args)
    return rc


def _run(elf: Path, td: Path, args: list[str], name: str) -> tuple[int, str, str]:
    """在 WSL 里跑 (ELF 是 Linux 目标, 拷进 /tmp 才能执行)。args 已经是 WSL 侧字符串。"""
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lompkg_{name}.bin"
    quoted = " ".join(shlex.quote(a) for a in args)
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {quoted} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    etext = errp.read_text(encoding="utf-8", errors="replace") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, etext


def _pair(elf: Path, td: Path, name: str,
          py_args: list[str], el_args: list[str]) -> None:
    """跑两侧并比 stdout + 退出码; 不一致就抛断言 (带首个差异的上下文)。"""
    want_rc, want = _py(py_args)
    got_rc, got, etext = _run(elf, td, el_args, name)
    if got_rc == want_rc and got == want:
        return
    k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
             min(len(got), len(want)))
    raise AssertionError(
        f"[{name}] 不一致: rc py={want_rc} el={got_rc}\n"
        f"  want ({len(want)}B): {want[max(0, k - 60):k + 40]!r}\n"
        f"  got  ({len(got)}B): {got[max(0, k - 60):k + 40]!r}\n"
        f"  首个字节差异 @{k}\n"
        f"  el stderr: {etext[-200:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_loment_lompkg_matches_python():
    """链式依赖 / 嵌套子目录 / 空包: resolve 的 stdout 逐字节相同。

    哈希前 16 位一致就证明了 SHA-256、喂入顺序 (相对路径 + NUL + 原始字节 + NUL) 与
    目录递归都对。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "tree"
        m = _tree(base)
        elf = _build(td)
        n = 0
        for name in ("top", "nest", "empty", "multi"):
            _pair(elf, td, f"res_{name}",
                  ["resolve", str(m[name])],
                  ["resolve", _wsl_path(m[name])])
            n += 1
        print(f"      {n} 棵包树 resolve stdout 逐字节相同")


@test
def test_loment_lompkg_edge_cases():
    """环 / 缺依赖 / 缺 name / 用法错误: 退出码相同且 stdout 相同 (都是空)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "tree"
        m = _tree(base)
        elf = _build(td)
        for name in ("cyc_a", "miss", "noname", "cyc_b"):
            _pair(elf, td, f"err_{name}",
                  ["resolve", str(m[name])],
                  ["resolve", _wsl_path(m[name])])
        # 用法错误 (argparse 退出码 2)
        _pair(elf, td, "usage_none", ["resolve"], ["resolve"])
        _pair(elf, td, "usage_bad", ["bogus", str(m["top"])],
              ["bogus", _wsl_path(m["top"])])
        _pair(elf, td, "usage_verify_nolock", ["verify", str(m["top"])],
              ["verify", _wsl_path(m["top"])])
        print("      4 个错误场景 + 3 个用法错误: 退出码与 stdout 一致")


@test
def test_loment_lompkg_verify_and_lock_roundtrip():
    """verify: 一致 / 改一字节后 DIFF / 锁里缺包 MISS; 且两个方向的锁都能被对方读。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "tree"
        m = _tree(base)
        elf = _build(td)
        top = m["top"]
        py_lock = td / "py.lock"
        el_lock = td / "el.lock"

        # Python 写锁 -> 两边 verify (含 Loment 读 Python 的锁)
        assert _py_rc(["resolve", str(top), "--lock", str(py_lock), "--write"]) == 0
        _pair(elf, td, "verify_pylock",
              ["verify", str(top), "--lock", str(py_lock)],
              ["verify", _wsl_path(top), "--lock", _wsl_path(py_lock)])

        # Loment 写锁。`--write` 成功消息里**带锁文件路径** (`[OK] 锁文件 -> ...`), 两边
        # 写法必然不同 (Windows 路径 vs /mnt 路径), 所以只比到 "[OK] n 个包" 那行为止;
        # 侧效应 (锁本身) 用"Python 能读它"来验 —— 这就是跨实现往返。
        want_rc, want = _py(["resolve", str(top), "--lock", str(el_lock), "--write"])
        got_rc, got, etext = _run(elf, td, ["resolve", _wsl_path(top),
                                            "--lock", _wsl_path(el_lock), "--write"],
                                  "write_ellock")
        assert (want_rc, got_rc) == (0, 0), \
            f"rc py={want_rc} el={got_rc} el_stderr={etext[-200:]!r}"
        want_head = want.rsplit("[OK] 锁文件", 1)[0]
        got_head = got.rsplit("[OK] 锁文件", 1)[0]
        assert got_head == want_head, \
            f"resolve 段不一致:\n  want {want_head!r}\n  got  {got_head!r}"
        assert "[OK] 锁文件 ->" in got, f"缺写锁消息: {got!r}"
        assert _py_rc(["verify", str(top), "--lock", str(el_lock)]) == 0, \
            "Python 读 Loment 写的锁应当通过"

        # 改一个字节 -> 两边都 [DIFF] 且 rc=1
        _mk_src(base / "base", "src/m.lomt", "module base\nfn f() -> u32 { return 9; }\n")
        _pair(elf, td, "verify_diff",
              ["verify", str(top), "--lock", str(py_lock)],
              ["verify", _wsl_path(top), "--lock", _wsl_path(py_lock)])

        # 锁里只有 top -> 两边都报 [MISS] base/mid
        partial = td / "partial.lock"
        partial.write_text(json.dumps({"lockfile": 1, "packages": [
            {"name": "top", "version": "0.1.0", "dir": "x", "hash": "0" * 64}]},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _pair(elf, td, "verify_miss",
              ["verify", str(top), "--lock", str(partial)],
              ["verify", _wsl_path(top), "--lock", _wsl_path(partial)])
        print("      verify 一致/DIFF/MISS + 双向锁往返: stdout 与退出码都一致")


def main() -> int:
    return loment_suite.run(TESTS, "loment_pkg_test")


if __name__ == "__main__":
    sys.exit(main())
