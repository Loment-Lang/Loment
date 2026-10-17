#!/usr/bin/env python3
# loment_editors_test.py — "不用 VS Code" 的编辑器支持判据 (docs/157 §3.6)
#
# 两条:
#   1. **语法表不漂移**: editors/vim/syntax/loment.vim 里的关键字/类型词必须都在 VS Code 的
#      TextMate 语法里出现 (vim 侧是手写的, 最容易和语法源脱节);
#   2. **Vim 真能高亮**: 用无头 vim 打开一个 .lomt, 断言 filetype 与几个位置的语法组
#      (关键字/类型/函数名/字符串/注释) —— 不是"文件存在"这种纸面判据。
#
# 运行: python tools/loment_editors_test.py   (无 vim 时第 2 条 SKIP, 退出码 0)

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VIM = ROOT / "editors" / "vim"
VIM_SYNTAX = VIM / "syntax" / "loment.vim"
VSCODE_GRAMMAR = ROOT / "editors" / "vscode" / "syntaxes" / "loment.tmLanguage.json"
TESTS: list[tuple[str, object]] = []

PROBE = """module probe

/// 文档注释
fn add(a: u32, b: u32) -> u32 {
    // 普通注释
    let s: str = "hi";
    return a;
}
"""


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _vim() -> str | None:
    return shutil.which("vim") or shutil.which("nvim")


@test
def test_vim_syntax_keywords_match_grammar():
    """vim 语法里的词必须都在 TextMate 语法里出现 —— 否则同一个词两边说法不一致。

    注意: 语法源把词分在**很多** scope 里 (declaration / `variable.language.self` /
    `constant.language` ...), 所以判据是"出现在语法源的任意模式里", 不是某个固定 scope。
    """
    vim_kw, vim_ty = set(), set()
    for line in VIM_SYNTAX.read_text(encoding="utf-8").splitlines():
        m = re.match(r"syn keyword (loment\w+)\s+(.*)$", line)
        if not m:
            continue
        group, words = m.group(1), m.group(2).split()
        if group in ("lomentKeyword", "lomentBool", "lomentSelf"):
            vim_kw.update(words)
        elif group in ("lomentType", "lomentBuiltin"):
            vim_ty.update(words)
    assert vim_kw and vim_ty, "没从 vim 语法里解析出词表 (文件格式变了?)"

    grammar_raw = VSCODE_GRAMMAR.read_text(encoding="utf-8")
    assert "loment" in grammar_raw, "TextMate 语法文件读不出来?"
    missing = sorted(w for w in (vim_kw | vim_ty)
                     if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])",
                                      grammar_raw))
    assert not missing, (
        f"vim 语法里的词在 TextMate 语法里找不到: {missing}\n"
        f"(要么语法源漏了它们, 要么 vim 侧多写了)")
    print(f"      vim ⊆ 语法源: 关键字 {len(vim_kw)} + 类型/内建 {len(vim_ty)} 个词都在语法里")


@test
def test_vim_highlights_lomt():
    """无头 vim: filetype 认成 loment, 且各类 token 落到对的语法组。

    命令里**只有字面量**: 把 editors/vim 拷成临时目录里的 `rtp/`, 以 cwd = 临时目录运行,
    于是是 `set rtp+=rtp` / `edit probe.lomt` / `writefile(g:o, 'out.txt')` —— 不拼任何路径。
    (顺带避开 `-S script`: Windows 的 vim 会等输入而挂住; 用一串 `-c` 最稳。)
    """
    vim = _vim()
    if not vim:
        print("      SKIP: 无 vim/nvim")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        shutil.copytree(VIM, td / "rtp")
        with (td / "probe.lomt").open("w", encoding="utf-8", newline="\n") as f:
            f.write(PROBE)
        # vim 对 `-c` 有**数量上限** (Too many "+command" arguments), 所以把命令用 `|` 串成
        # 两条; 命令里只有字面量 (全部相对 cwd = 临时目录)。
        setup = ("set rtp+=rtp | filetype plugin on | syntax on | edit probe.lomt | "
                 "let g:o = [] | call add(g:o, 'filetype=' . &filetype)")
        checks = (" | ".join([
            # (line, col) 都是 1-based, 按 PROBE 数出来
            "call add(g:o, 'kw=' . synIDattr(synID(4, 1, 1), 'name'))",       # fn
            "call add(g:o, 'ty=' . synIDattr(synID(4, 11, 1), 'name'))",      # u32
            "call add(g:o, 'fnname=' . synIDattr(synID(4, 4, 1), 'name'))",   # add
            "call add(g:o, 'cmt=' . synIDattr(synID(5, 5, 1), 'name'))",      # //
            "call add(g:o, 'doc=' . synIDattr(synID(3, 1, 1), 'name'))",      # ///
            "call add(g:o, 'str=' . synIDattr(synID(6, 18, 1), 'name'))",     # "hi"
            "call add(g:o, 'ret=' . synIDattr(synID(7, 5, 1), 'name'))",      # return
            "call writefile(g:o, 'out.txt')",
            "qa!",
        ]))
        args = [vim, "-N", "-u", "NONE", "-es", "--not-a-term",
                "-c", setup, "-c", checks]
        r = subprocess.run(args, capture_output=True, text=True, timeout=180,
                           shell=False, cwd=str(td))
        out = td / "out.txt"
        assert out.exists(), (f"vim 没写出结果 (rc={r.returncode}): "
                              f"{r.stdout[-200:]}{r.stderr[-200:]}")
        got = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines()
                   if "=" in ln)
    assert got.get("filetype") == "loment", got
    assert got["kw"] == "lomentKeyword", got
    assert got["ty"] == "lomentType", got
    assert got["ret"] == "lomentKeyword", got
    assert got["fnname"] == "lomentFuncName", got
    assert got["cmt"] == "lomentComment", got
    assert got["doc"] == "lomentDocComment", got
    assert got["str"] == "lomentString", got
    print("      无头 vim: filetype=loment; fn/u32/函数名/注释/文档注释/字符串/return 各就各位")


# ---------------------------------------------------------------- Loment 版（S1 第三格）

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 是所有 `wsl -e` 调用共用的，
#: 固定文件名会让并发跑门禁的两个进程互相跑对方的二进制 —— 那是错结果，不是慢）。
_T = f"/tmp/loment-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomvimgrammar.lomt"


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


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


def _build_twin(td: Path) -> Path:
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomvimgrammar.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomvimgrammar.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomvimgrammar.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _run_twin(elf: Path, td: Path) -> tuple[int, str]:
    outp = td / "twin.out"
    binn = f"{_T}lomvim.bin"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} > {_wsl_path(outp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out


@test
def test_vim_grammar_check_matches_loment_twin():
    """上面那条检查，**Loment 版给出的结论与 Python 版逐字节相同**。

    这是 `docs/189` §3 的 S1 第三格（**检查型** —— 判据自己就是检查逻辑，没有对应工具）。
    Python 那一侧**成功时是一行 stdout**，失败时是 `assert` 抛异常（不是 stdout）—— 所以
    这里比的是**成功那一趟**；失败那一档两边各写各的语气（孪生自己会说缺哪些词并退 1）。
    另加一条**独立断言**：孪生报的两个数必须等于我从 vim 文件里**自己数**出来的词数 ——
    只比两边相同的话，两边一起数错也会"一致"。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    # 参考侧：直接跑上面那条检查，捕获它打印的那一行
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        test_vim_syntax_keywords_match_grammar()
    want = buf.getvalue()

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build_twin(td)
        rc, got = _run_twin(elf, td)
    assert rc == 0, f"孪生该退 0（都命中）: rc={rc}\n{got[:200]}"
    assert got == want, f"结论与 Python 版不同:\n  py    {want!r}\n  loment {got!r}"

    # 独立数一遍（不复用上面那条检查的集合）
    kw, ty = set(), set()
    for line in VIM_SYNTAX.read_text(encoding="utf-8").splitlines():
        m = re.match(r"syn keyword loment(\w+)\s+(.*)$", line)
        if not m:
            continue
        g, words = m.group(1), m.group(2).split()
        if g in ("Keyword", "Bool", "Self"):
            kw.update(words)
        elif g in ("Type", "Builtin"):
            ty.update(words)
    assert f"关键字 {len(kw)} + 类型/内建 {len(ty)} 个词" in got, (
        f"报的个数与我独立数出来的对不上（{len(kw)} / {len(ty)}）: {got!r}")
    print("      结论与 Python 版逐字节相同；两个数另经独立清点")


def main() -> int:
    return loment_suite.run(TESTS, "loment_editors_test")


if __name__ == "__main__":
    sys.exit(main())
