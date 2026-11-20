#!/usr/bin/env python3
# loment_lomc_test.py — Loment 版 L0 编译器与 Python 版逐字节相同 (docs/159 §4b)
#
# 判据: 同一份 `.lom` 喂 `loment/tools/lomc.lomt`(链成 ELF 后跑) 与 `tools/lomc.py`:
#   1. `--print {rust,c,python,json}` 的 **stdout 逐字节相同** (纯内容, 无路径);
#   2. `--emit-*` **写出的文件逐字节相同** (这才是内核线消费的东西);
#   3. `--check` / 语义错误 / 用法错误的**退出码相同**, stdout 相同。
#
# 路径归一: `--emit-*` / `--check` 的 stdout 里含路径, Python 在 Windows 上把它渲染成
# `lom\fuai.lom`、本程序 (Linux 目标) 是 `lom/fuai.lom`。所以 (a) 目标路径一律用**仓库相对
# 且带 `/`** 的写法 (两边打印同一个字符串), (b) 对 Python 的 stdout 做 `\`->`/` 归一后比较。
# 归一之外没有再放宽 —— 路径以外的每个字节都要对上。
#
# 已知边界 (与 lomc.lomt 头注一致): 本版不替 `--emit-*` 建父目录 (Python 会 mkdir -p) ——
# 判据用的目录由门禁先建好。
#
# 运行: python tools/loment_lomc_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

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
import lomc     # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomc.lomt"
# `lom/net.lom` 从它在的那天起就得进这份清单 —— 它是**同一种东西**（L0 单一真源）。
# 少一份的症状很安静: 自举 lomc 读不了它, 而 Python 那份读得了。
LOMS = ["lom/fuai.lom", "lom/fuc.lom", "lom/fujr.lom", "lom/net.lom"]
TARGETS = ["rust", "c", "python", "json"]
# 目标后缀 (与 lomc.py 的调用约定一致, 供 --check 的默认对账路径用)
EXT = {"rust": "rs", "c": "h", "python": "py", "json": "json"}
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
    assert not errs, f"lomc.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomc.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomc.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _py(args: list[str]) -> tuple[int, str]:
    """进程内跑 Python 版并捕获 stdout (见 loment_pkg_test 的同一理由: 避免 Windows 换行翻译)。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = lomc.main(args)
    except SystemExit as e:  # argparse 也走这里
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue()


def _norm(s: str) -> str:
    """只归一路径分隔符 (Windows Python 打印 `lom\\x.lom`, Linux 侧是 `lom/x.lom`)。"""
    return s.replace("\\", "/")


def _run(elf: Path, td: Path, args: list[str], name: str) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomc_{name}.bin"
    quoted = " ".join(args)  # 参数都是仓库相对路径, 无空格
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


def _pair(elf: Path, td: Path, name: str, py_args: list[str], el_args: list[str]) -> None:
    want_rc, want = _py(py_args)
    got_rc, got, etext = _run(elf, td, el_args, name)
    if got_rc == want_rc and got == _norm(want):
        return
    k = next((i for i in range(min(len(got), len(_norm(want)))) if got[i] != _norm(want)[i]),
             min(len(got), len(_norm(want))))
    wn = _norm(want)
    raise AssertionError(
        f"[{name}] 不一致: rc py={want_rc} el={got_rc}\n"
        f"  want ({len(wn)}B): {wn[max(0, k - 60):k + 40]!r}\n"
        f"  got  ({len(got)}B): {got[max(0, k - 60):k + 40]!r}\n"
        f"  首个差异 @{k}  el stderr={etext[-160:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_loment_lomc_print_matches_python():
    """四个发射器的 `--print` stdout 逐字节相同 —— 内容侧的判据 (无语义差异)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        n = 0
        for f in LOMS:
            for t in TARGETS:
                _pair(elf, td, f"p_{Path(f).stem}_{t}", [f, "--print", t], [f, "--print", t])
                n += 1
        print(f"      {n} 份发射 (3 个 .lom × 4 个后端) stdout 逐字节相同")


@test
def test_loment_lomc_emit_writes_same_bytes():
    """`--emit-*` 落盘的文件逐字节相同, 且 `[OK] src -> dest (NB)` 这行也对得上。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    outdir = ROOT / "loment" / "build" / "lomc_emit_tmp"
    outdir.mkdir(parents=True, exist_ok=True)
    rel = outdir.relative_to(ROOT).as_posix()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        try:
            for f in LOMS:
                stem = Path(f).stem
                dests = [f"{rel}/{stem}.{EXT[t]}" for t in TARGETS]
                args = [f]
                for t, d in zip(TARGETS, dests):
                    args += [f"--emit-{t}", d]
                # 两边跑**同一组参数** (dest 相同 -> [OK] 行可比); 落盘内容是 Loment 写的,
                # 再拿 Python 的 --print 逐字节核它。
                _pair(elf, td, f"e_{stem}", args, args)
                for t, d in zip(TARGETS, dests):
                    _, want = _py([f, "--print", t])
                    got = (ROOT / d).read_bytes()
                    assert got == want.encode("utf-8"), \
                        f"{stem}.{t}: 落盘 {len(got)}B 与 --print {len(want)} 字不符"
            print(f"      {len(LOMS)} 个 .lom 的四路落盘字节 + 输出行一致")
        finally:
            shutil.rmtree(outdir, ignore_errors=True)


@test
def test_loment_lomc_check_and_errors():
    """`--check` 对账 / 漂移检出 / 语义错误 / 用法错误: 退出码与 stdout 一致。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        # 1) **生成是确定的**: 同一份 `.lom` 生成两遍 -> 逐字节相同。
        #    这一档原先比的是"盘上那份**提交好的**副本 == 生成器此刻会生成的"；
        #    交付物**移出索引**之后（`docs/189` §3.0）盘上不再有副本 —— 于是不变式
        #    换成文档里写的那两半：**生成是确定的** + **漂移检得出来**（第 2 条）。
        gen1 = ROOT / "loment" / "build" / "lomc_gen_tmp1"
        gen2 = ROOT / "loment" / "build" / "lomc_gen_tmp2"
        rel1, rel2 = (d.relative_to(ROOT).as_posix() for d in (gen1, gen2))
        gen1.mkdir(parents=True, exist_ok=True)
        gen2.mkdir(parents=True, exist_ok=True)
        try:
            for f in LOMS:
                stem = Path(f).stem
                for t in TARGETS:
                    ext = EXT[t]
                    for tag, rel in (("1", rel1), ("2", rel2)):
                        args = [f, f"--emit-{t}", f"{rel}/{stem}.{ext}"]
                        _pair(elf, td, f"g{tag}_{stem}_{t}", args, args)
                    a = (gen1 / f"{stem}.{ext}").read_bytes()
                    b = (gen2 / f"{stem}.{ext}").read_bytes()
                    assert a == b, (f"`{f}` 的 {t} 产物两次生成不一致 "
                                    f"({len(a)}B / {len(b)}B) —— 生成必须是确定的")
        finally:
            shutil.rmtree(gen1, ignore_errors=True)
            shutil.rmtree(gen2, ignore_errors=True)
        # 2) **漂移检得出来**: 先现场生成一份, 再改一个字节, `--check` 必须红
        bad_dir = ROOT / "loment" / "build" / "lomc_bad_tmp"
        bad_dir.mkdir(parents=True, exist_ok=True)
        relbad = bad_dir.relative_to(ROOT).as_posix()
        try:
            n = 0
            for f in LOMS:
                stem = Path(f).stem
                for t in TARGETS:
                    dest = bad_dir / f"{stem}.{EXT[t]}"
                    emit = [f, f"--emit-{t}", f"{relbad}/{stem}.{EXT[t]}"]
                    _pair(elf, td, f"p_{stem}_{t}", emit, emit)   # 先把它生成出来
                    data = dest.read_text(encoding="utf-8")
                    if t == "json":
                        data = data.replace("v0", "v1", 1)        # 改一个字节级差异
                    else:
                        data = data + "// drift\n"
                    dest.write_text(data, encoding="utf-8", newline="\n")
                args = [f, "--check"]
                for t in TARGETS:
                    args += [f"--emit-{t}", f"{relbad}/{stem}.{EXT[t]}"]
                _pair(elf, td, f"d_{stem}", args, args)
                n += 1
            print(f"      {len(LOMS)} 个 .lom: 生成两遍逐字节相同 + {n} 份漂移都被检出")
        finally:
            shutil.rmtree(bad_dir, ignore_errors=True)
        # 3) 语义错误 (枚举值重复 + const 越界)
        errfile = ROOT / "loment" / "build" / "lomc_err_tmp.lom"
        errfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            errfile.write_text(
                "module errcase\n\n"
                "enum E : u8 {\n  A = 1\n  B = 1\n}\n\n"
                "const C: u8 = 300;\n",
                encoding="utf-8", newline="\n")
            relerr = errfile.relative_to(ROOT).as_posix()
            _pair(elf, td, "err_sem", [relerr, "--emit-json", f"{relbad}/x.json"],
                  [relerr, "--emit-json", f"{relbad}/x.json"])
        finally:
            errfile.unlink(missing_ok=True)
        # 4) 用法错误 (没给输出) / 未知选项
        _pair(elf, td, "err_none", ["lom/fuc.lom"], ["lom/fuc.lom"])
        _pair(elf, td, "err_opt", ["lom/fuc.lom", "--nope"], ["lom/fuc.lom", "--nope"])
        print("      语义错误 / 无输出 / 未知选项: 退出码与 stdout 一致")


@test
def test_lomc_selfhost_compiles():
    """lomc.lomt 必须能走**种子自举链**编译 (无 Python): clang(种子) -> stage1 -> 本文件。

    这条盯的是移植时抓到的两个真 bug (见 docs/150 的账):
      1. 自举 checker 的**复合类型表**只在主遍历开头清一次, 而注释与 impl 路径都写明
         "每函数清零" —— 大单元 (lomc.lomt 有 357 个 let) 写穿 448 条上限, 踩进借用名表,
         整条扫描游标错位, 表现是一堆 E002 (把 `let` 的变量名当类型);
      2. 把局部变量命名成关键字 (`let fn`) —— 参考实现接受, 自举 checker 却按 token
         字面量把它当函数声明, 走岔 (这是一处**假阳性**, 比"保守少报"更糟)。
    两者都修了; 这条用例把"大单元 + 关键字"钉住, 不许回退。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        rc, out, etext = _run(s1, td, ["loment/tools/lomc.lomt"], "selfhost")
        assert rc == 0, f"stage1 编译 lomc.lomt 失败 rc={rc}: {etext[-300:]!r}"
        assert len(out) > 100000, f"产物太小 ({len(out)}B) —— 不像一个完整单元"
        print(f"      种子自举链编译 lomc.lomt 成功 ({len(out)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_lomc_test")


if __name__ == "__main__":
    sys.exit(main())
