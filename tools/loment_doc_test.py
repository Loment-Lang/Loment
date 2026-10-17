#!/usr/bin/env python3
# loment_doc_test.py — Loment 版 lomdoc 与 Python 版逐字节相同 (docs/159 §4b)
#
# 判据: 同一份 .lomt 喂 `loment/tools/lomdoc.lomt`(链成 ELF 后跑) 与 `tools/lomdoc.py`,
# stdout 必须一模一样。语料 = loment/examples + loment/selfhost + loment/tools。
#
# 运行: python tools/loment_doc_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

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
import lomdoc   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomdoc.lomt"
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
    assert not errs, f"lomdoc.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomdoc.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomdoc.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _corpus() -> list[Path]:
    files = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "selfhost").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "tools").glob("*.lomt"))
    return files


def _want(p: Path, name: str) -> str | None:
    """Python 版输出; 语义错误 (lomdoc 会拒绝) 返回 None。

    `name` 必须是**命令行给的那个路径** —— 参考实现把参数原样写进文档头, Loment 版也一样,
    所以两边要比同一个字符串 (否则比的是"相对 vs 绝对", 不是工具差异)。
    """
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    if lomentc.check(mod, deps=deps):
        return None
    return lomdoc.render(mod, p.read_text(encoding="utf-8"), name)


def _run(elf: Path, td: Path, entry: Path, name: str) -> tuple[str, str]:
    got = td / f"{name}.out"
    err = td / f"{name}.err"
    script = (f"rm -f {_T}{name}.bin && cp {_wsl_path(elf)} {_T}{name}.bin && "
              f"chmod +x {_T}{name}.bin && "
              f"cd {_wsl_path(ROOT)} && {_T}{name}.bin {entry.relative_to(ROOT).as_posix()} "
              f"> {_wsl_path(got)} 2> {_wsl_path(err)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = got.read_text(encoding="utf-8") if got.exists() else ""
    etext = err.read_text(encoding="utf-8", errors="replace") if err.exists() else ""
    return out, f"rc={r.stdout.strip()} {etext[-200:]}"


@test
def test_loment_lomdoc_matches_python():
    """全语料逐字节相同 —— "文档生成器可以脱离 Python" 的判据。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    files = _corpus()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        bad, n = [], 0
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            want = _want(f, rel)
            if want is None:
                continue
            n += 1
            got, note = _run(elf, td, f, f.stem)
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                bad.append(f"{f.name} ({note}, want {len(want)}B got {len(got)}B @{k})\n"
                           f"      want: {want[max(0,k-60):k+40]!r}\n"
                           f"      got : {got[max(0,k-60):k+40]!r}")
        assert not bad, "\n".join(bad[:3])
        print(f"      {n} 个语料逐字节相同")


EDGE_CASE = '''module lomdoc_edge

/// 能力域文档: 可撤销的写权限
capability w : disk[0..4] revocable

/// 不可撤销的那个
capability r : disk[8..12]

excluded "net: 本单元不申请 net 能力"

/// 常量文档
/// 第二行也要拼上
const HDR: u32 = 8;

/// 十六进制字面量在文档里要打印成十进制
const MASK: u32 = 0x10;

/// 类型文档
struct Pair {
    a: u32,
    b: u32,
}

/// 枚举文档
enum Color {
    Red,
    Rgb(u32),
}

/// trait 文档
trait Show {
    fn show(self) -> u32;
    fn tag(self, k: u32) -> u32;
}

impl Show for Pair {
    fn show(self) -> u32 {
        return self.a;
    }

    fn tag(self, k: u32) -> u32 {
        return k;
    }
}

/// 泛型函数的形参不进文档头
fn max_of<T>(a: T, b: T) -> T {
    return a;
}

fn no_doc() {
    let x: u32 = 1;
}

/// 命令 (形态 A): 块那层壳对按深度走的读者是可见的, 块里的函数仍是顶层函数 (#399)
register edgecmd {
    /// 块里的那个函数
    pub fn command_main(argv: ptr, argc: u32) -> u32 {
        return argc;
    }
}
'''


@test
def test_loment_lomdoc_edge_cases():
    """语料里没出现的段: excluded / 十六进制常量 / 多行 doc / 双方法 trait / 空 doc /
    register 块里的函数 (含它自己的 doc 行 —— 语料那份块里没有 `///`)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lomdoc_edge.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        with probe.open("w", encoding="utf-8", newline="\n") as f:
            f.write(EDGE_CASE)
        rel = probe.relative_to(ROOT).as_posix()
        want = _want(probe, rel)
        assert want is not None, "边界用例自己没过语义检查 (改用例本身)"
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            got, note = _run(elf, td, probe, "edge")
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                gl = want[:k].count("\n") + 1
                raise AssertionError(
                    f"边界用例不一致 ({note}) 首个差异在第 {gl} 行 @{k}:\n"
                    f"  want: {want[max(0,k-70):k+50]!r}\n"
                    f"  got : {got[max(0,k-70):k+50]!r}")
    finally:
        probe.unlink(missing_ok=True)
    print("      边界用例 (excluded/hex/多行 doc/双方法 trait/泛型/空 doc/register 块) 一致")


#: 别的写法的样本：`choose write grammar c` 打头，第二行就是函数声明。
#: 声明那一行整个是"怎么读" —— 前门会把它抹成等长空白，于是**那一行上的函数
#: 跟着消失**，单元变空，工具却退 0（`#144`）。
FOREIGN = """choose write grammar c

unsigned int add(unsigned int a, unsigned int b) {
    return a + b;
}
"""


@test
def test_loment_lomdoc_refuses_foreign_grammar():
    """**别的写法要拦住**（`#144`）—— 两个实现都拒，都是退 1、stdout 为空。

    `lomdoc` 是**独立入口**（拿词法层直接读原文、不走前门），所以"这份源该怎么读"
    这一层得它自己判 —— 参考实现的 `potato_from.refuse_foreign_grammar` 就是这一层，
    孪生这边是新加的 `lomdoc.lomt::foreign_grammar`。不拦的后果实测过：文档里
    **一个函数都没有**、模块名成了 `grammar`、退 0。

    两边都跑：只钉孪生的话，参考实现哪天松掉就没人管了。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lomdoc_foreign.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        with probe.open("w", encoding="utf-8", newline="\n") as f:
            f.write(FOREIGN)
        # 参考实现：命令行真跑一遍（`refuse_foreign_grammar` 在 main 里）
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomdoc.py"),
                            str(probe)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=120)
        assert r.returncode == 1, f"参考实现没拒 (rc={r.returncode}): {r.stdout[:200]}"
        assert r.stdout == "", f"参考实现拒了却还往 stdout 写了东西: {r.stdout[:200]!r}"
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            got, note = _run(elf, td, probe, "foreign")
            assert got == "", f"孪生拒了却还写了文档: {got[:200]!r} [{note}]"
            assert "rc=1" in note, f"孪生没退 1: {note}"
    finally:
        probe.unlink(missing_ok=True)
    print("      别的写法: 两个实现都拒 (rc=1, stdout 空)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_doc_test")


if __name__ == "__main__":
    sys.exit(main())
