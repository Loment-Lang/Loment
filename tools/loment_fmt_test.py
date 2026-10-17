#!/usr/bin/env python3
# loment_fmt_test.py — Loment 版格式化器与 Python 版的行为等价 (docs/159)
#
# 判据: 同一份源码喂 `loment/tools/lomfmt.lomt` (链成 ELF 后跑) 与 `tools/lomfmt.py`,
# **stdout 逐字节相同**。语料 = loment/examples、loment/selfhost、以及格式化器自己。
#
# 这是"脱离第三方语言"里工具链那一格的第一块: 格式化器不再需要 Python 就能跑,
# 判据是它和旧实现的输出一模一样 (包括旧实现把 tok.val 当字符串比较的那些怪癖)。
#
# 运行: python tools/loment_fmt_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc     # noqa: E402
import lomentc  # noqa: E402
import lomfmt   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomfmt.lomt"
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
    assert not errs, f"lomfmt.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomfmt.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "fujofmt.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _corpus() -> list[Path]:
    files = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "selfhost").glob("*.lomt"))
    # `comefor` 的演示也进语料（`docs/184` §9 S4.3）：方言区**也**要被格式化 —— 见
    # `test_fmt_preserves_dialect_tokens` 那条（它说明为什么"不透明块"对 `comefor`
    # 是多余的）。
    files += sorted((ROOT / "loment" / "comefor").glob("*.lomt"))
    files.append(SRC)
    return files


@test
def test_fmt_preserves_dialect_tokens():
    """**格式化不改变 token 序列** —— 于是方言区被格式化了也无所谓（`docs/184` §9 S4.3）。

    `docs/184` §9 给 S4.3 定的判据里有"`lomfmt` 不碰方言区"。**对 `comefor` 来说那条
    是多余的**：方言区里的东西**仍然是 Loment 的 token**（`reg LEDS: 0x4000 { … }` 也是
    标识符/标点/数字），而宏体读的就是 token。所以只要格式化**保序**，宏体看到的输入
    一个字都没变 —— "别碰它"是在保护一件本来就不会坏的事。

    **那条判据要等到有"非 Loment 词法"的方言区**才有内容（`docs/183` §8 的 S1：
    外部代码块按原字节携带）。这里把这个区别钉下来，免得后来的人按 §9 的字面去写一个
    用不上的"不透明块"。

    判据就是保序 + 幂等，对**每一份**含有 `comefor` 的语料都跑 —— 不依赖 clang/WSL。
    """
    files = sorted((ROOT / "loment" / "comefor").glob("*.lomt"))
    assert files, "语料空了 —— 判据会空转"
    for f in files:
        src = f.read_text(encoding="utf-8")
        out = lomfmt.format_source(src)
        a = [(t.kind, t.val) for t in lomc.lex(src) if t.kind != "eof"]
        b = [(t.kind, t.val) for t in lomc.lex(out) if t.kind != "eof"]
        assert a == b, f"{f.name}: 格式化改变了 token 序列（{len(a)} -> {len(b)}）"
        assert lomfmt.format_source(out) == out, f"{f.name}: 格式化不幂等"
    print(f"      保序: {len(files)} 份方言语料格式化后 token 序列一字未变")


def _run(elf: Path, td: Path, entry: Path, name: str) -> tuple[str, str]:
    """在 WSL 里跑格式化器 (ELF 是 Linux 目标, 拷进 /tmp 才能执行)。"""
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
def test_loment_fmt_matches_python():
    """语料上逐字节相同 —— 这就是"格式化器可以脱离 Python"的判据。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        bad = []
        for f in _corpus():
            want = lomfmt.format_source(f.read_text(encoding="utf-8"))
            got, note = _run(elf, td, f, f.stem)
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                bad.append(f"{f.name} ({note}, want {len(want)}B got {len(got)}B @{k})")
        assert not bad, "与 Python 版不一致: " + "; ".join(bad[:4])
        print(f"      {len(_corpus())} 个语料逐字节相同")


@test
def test_loment_fmt_edge_cases():
    """空文件/只有注释/只有字符串/CRLF —— Python 版这些边界上的行为也得一致。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    cases = {
        "empty": "",
        "comment_only": "// 只有注释\n",
        "string_only": 'module x\n\nconst C: u32 = 1;\n\nfn f() -> str {\n    return "a\\\\b\\"c";\n}\n',
        "crlf": 'module x\r\n\r\nfn f() -> u32 {\r\n    return 1;\r\n}\r\n',
        # `#46`: 修饰词不与自己修饰的那一项分开（这一张**两边都跑**，所以孪生也被钉住）
        "pub_fn": "module x\n\npub fn f() -> u32 {\n    return 1;\n}\n",
        "pub_extern": ("module x\n\npub extern fn sys_write(fd: u32, p: ptr, n: u32) -> u32;\n"
                       "\nfn f() -> u32 {\n    return 1;\n}\n"),
        "pub_const_pair": "module x\n\npub const A: u32 = 1;\npub const B: u32 = 2;\n",
    }
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        probe = ROOT / "loment" / "build" / "fmt_edge_case.lomt"
        bad = []
        for name, text in cases.items():
            with probe.open("w", encoding="utf-8", newline="") as f:
                f.write(text)
            want = lomfmt.format_source(text)
            got, note = _run(elf, td, probe, f"edge_{name}")
            if got != want:
                bad.append(f"{name} ({note}): want {want!r} got {got!r}")
        probe.unlink(missing_ok=True)
        assert not bad, "; ".join(bad)
        print(f"      {len(cases)} 个边界用例一致")


@test
def test_fmt_keeps_declaration_modifiers_on_one_line():
    """**修饰词不单独占一行**（`#46`）—— `pub fn f()` 要待在同一行。

    原先每个顶层关键字都 flush 一次，于是 `pub` 被留成**单独一行**、和它修饰的东西
    隔了一个空行：

        pub
        <空行>
        fn f() -> u32 {

    那不是"格式"，那是把一项声明劈成两段。判据是"**这一行到现在只有修饰词**"而不是
    "刚看见 `pub`" —— 只认 `pub` 的话 `pub extern fn …;` 仍会被劈开（`extern` 不是顶层
    关键字）。所以三条形状都钉：`pub fn` / `pub extern fn` / 两条相邻的 `pub const`。
    """
    for label, src, want in (
        ("pub fn", "module x" + chr(10) + chr(10) + "pub fn f() -> u32 {" + chr(10) + "    return 1;" + chr(10)
                   + "}" + chr(10),
         ["module x", "", "pub fn f() -> u32 {", "    return 1;", "}"]),
        ("pub extern fn",
         "module x" + chr(10) + chr(10) + "pub extern fn sys_write(fd: u32, p: ptr, n: u32) -> u32;"
         + chr(10) + chr(10) + "fn f() -> u32 {" + chr(10) + "    return 1;" + chr(10) + "}" + chr(10),
         ["module x", "", "pub extern fn sys_write(fd: u32, p: ptr, n: u32) -> u32;",
          "fn f() -> u32 {", "    return 1;", "}"]),
        ("pub const x2", "module x" + chr(10) + chr(10) + "pub const A: u32 = 1;" + chr(10)
         + "pub const B: u32 = 2;" + chr(10),
         ["module x", "", "pub const A: u32 = 1;", "pub const B: u32 = 2;"]),
    ):
        got = lomfmt.format_source(src).split(chr(10))
        while got and got[-1] == "":
            got.pop()
        assert got == want, f"{label}: 行不对 —— {got!r}"
        assert "pub" not in got, f"{label}: `pub` 被留成单独一行"
    print("      修饰词与声明同处一行：pub fn / pub extern fn / 两条 pub const")


@test
def test_loment_fmt_is_idempotent():
    """幂等: 格式化自己的输出还是它 (Python 版的判据之一, 这里靠等价性继承)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        probe = ROOT / "loment" / "build" / "fmt_idem_case.lomt"
        bad = []
        for f in _corpus()[:6]:
            once = lomfmt.format_source(f.read_text(encoding="utf-8"))
            with probe.open("w", encoding="utf-8", newline="\n") as g:
                g.write(once)
            twice, note = _run(elf, td, probe, f"idem_{f.stem}")
            if twice != once:
                bad.append(f"{f.name} ({note})")
        probe.unlink(missing_ok=True)
        assert not bad, "不幂等: " + ", ".join(bad)
        print("      6 个语料格式化两次结果相同")


#: 别的写法的样本：`choose write grammar c` 打头，第二行就是函数声明。
#: 声明那一行整个是"怎么读" —— 前门会把它抹成等长空白，于是**那一行上的函数
#: 跟着消失**，而 `--write` 是**原地写回**（`#115`）：用户的源就这么被改坏。
FOREIGN = """choose write grammar c

unsigned int add(unsigned int a, unsigned int b) {
    return a + b;
}
"""


@test
def test_loment_fmt_refuses_foreign_grammar():
    """**别的写法要拦住**（`#115` 的后半）—— 两个实现都拒，盘上那份源一个字不动。

    `lomfmt` 是**独立入口**（拿词法层直接读原文、不走前门），所以"这份源该怎么读"
    这一层得它自己判 —— 参考实现的 `potato_from.refuse_foreign_grammar` 就是这一层，
    孪生这边是新加的 `lomfmt.lomt::foreign_grammar`。不拦的后果实测过（`#115`）：
    命令**退 0**、说都不说一句，而文件已经被重排成既不是 C 也不是 Loment 的样子
    —— 比 `lomdoc` 那半边更重，因为 `--write` 是**原地写回**。

    两边都跑：只钉孪生的话，参考实现哪天松掉就没人管了。
    退出码**故意不写成同一个数**：`lomfmt.py` 把这条例外归到它文档里的"读取或词法
    错误"那一档（退 2），孪生的 `fail` 退 1 —— 判据要的是"**拒了**"，不是"退几"。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lomfmt_foreign.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        with probe.open("w", encoding="utf-8", newline="\n") as f:
            f.write(FOREIGN)
        before = probe.read_bytes()
        # 参考实现：`--write` 真跑一遍 —— 它拒了就不能碰盘上那份
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomfmt.py"),
                            "--write", str(probe)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=120)
        assert r.returncode != 0, f"参考实现没拒 (rc={r.returncode}): {r.stdout[:200]}"
        assert r.stdout == "", f"参考实现拒了却还往 stdout 写了东西: {r.stdout[:200]!r}"
        assert r.stderr.strip(), "参考实现拒了却没吭声 (stderr 空)"
        assert probe.read_bytes() == before, "参考实现拒了却把文件改坏了 (#115 就是这个症状)"
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            got, note = _run(elf, td, probe, "foreign")
            assert got == "", f"孪生拒了却还写了格式化结果: {got[:200]!r} [{note}]"
            assert "rc=1" in note, f"孪生没退 1: {note}"
            assert probe.read_bytes() == before, "孪生跑完把文件动过了"
    finally:
        probe.unlink(missing_ok=True)
    print("      别的写法: 两个实现都拒 (参考实现退非 0 / 孪生退 1)，盘上那份源未变")


def main() -> int:
    return loment_suite.run(TESTS, "loment_fmt_test")


if __name__ == "__main__":
    sys.exit(main())
