#!/usr/bin/env python3
"""loment_extblock_test.py — 外部代码块的**字节保真**（S1，`docs/185`）。

**核心那条是 `test_raw_body_is_byte_faithful`**：块里的正文是一个 raw token，
它的 `(off, len)` 圈到的源字节与**原文逐字节相同** —— 含 `"`、`\\`、`\\0`、非 ASCII。
那是 `docs/185` §2 要证明的东西：正文**不能**走字符串字面量（转义表只有 4 条、
未知转义静默退化、没有非 UTF-8 通道），所以它走新词法模式。

其余几条钉**消歧**（`docs/185` §3）：不带 `let` 的写法只在"声明过的语言名 + 顶层"下才认，
否则 `struct S {` / `fn f() -> u32 {` / `if c { }` 全会被误当成块。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lomentc  # noqa: E402
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 一段**故意的恶意正文** —— 它把字符串字面量那条路能踩的坑全踩一遍：
#: `"` 要转义、`\\0` 是未知转义（会静默退化成两个字符）、非 ASCII 没有非 UTF-8 通道。
#: 正文里还各带一个**不配对的** `{` / 引号，说明"朴素计数只数花括号"这条边界在哪。
_EVIL = ('#include <stdio.h>\n'
         'const char *s = "a\\"b\\\\c\\0d";\n'      # " 与 \ 与 \0
         'int f(void) { return 1; }\n'
         '/* 中文注释 · 不全角 */\n')


def _src(form: str) -> str:
    """把 `_EVIL` 按某种写法包成一份源。`form` 是 `let` / `bare`。"""
    head = "module m\n\ncommand c\n\n" if form == "bare" else "module m\n\n"
    return f"{head}{'c' if form == 'bare' else 'let c'} {{\n{_EVIL}}}\n\nfn main() -> u64 {{ return 0; }}\n"


@test
def test_raw_body_is_byte_faithful():
    """**正文逐字节相同** —— `docs/185` §6 给 S1.0 定的证伪判据。

    断言的是"raw token 的 `(off, len)` 圈到的源字节 == 原文"，**不是** `val` 与某个
    Python 字符串相等 —— 后者会把"两个实现都把转义解成同一个错"当成通过。
    这里比的是**字节**，而源是 UTF-8 读进来的，所以非 ASCII 也在里面。
    """
    # **正文是 `{` 与 `}` 之间的原始字节**（`docs/185` §4.1）—— 所以它**含**紧跟在
    # `{` 后面那个换行。这一条是刻意的：保真说的是"花括号之间有什么就是什么"，
    # 而不是"Loment 帮你把首尾空白修掉"（修掉就不是保真了）。
    want_c = ("\n" + _EVIL).encode("utf-8")
    for form in ("let", "bare"):
        src = _src(form)
        raws = [t for t in lomc.lex(src) if t.kind == "raw"]
        assert len(raws) == 1, f"{form}: 应当恰好一个 raw token, 实得 {len(raws)}"
        r = raws[0]
        got = src[r.off:r.off + r.len].encode("utf-8")
        want = want_c
        assert got == want, (
            f"{form}: 正文与原文不同（{len(got)}B vs {len(want)}B）\n"
            f"  原文: {want!r}\n  实得: {got!r}")
        # `val` 与跨度是**同一份**（参考侧两边都存，必须一致）
        assert r.val.encode("utf-8") == want, form
    print(f"      字节保真: 正文 {len(want_c)}B（含 \" \\ \\0 非 ASCII）两种写法都逐字节相同")


@test
def test_two_forms_are_equivalent():
    """`let c { … }` 与声明过的 `c { … }` 产出的 **token 流逐字段相同**（`docs/185` §6 S1.1）。

    `docs/183` §8.5 用户已定"两种写法都要、且等价"。这条把"等价"从一句话变成判据 ——
    比的是 kind/off/len（**不含 val**：两者 `val` 本来就相等，但 `off` 会因为
    `command c` 那一行而整体位移，所以比的是**形状**，不是绝对偏移）。
    """
    # **从块开始比**，不比声明头 —— 两种写法的头本来就不同（`let` vs `command c`），
    # 那是消歧规则的一部分，不是"等价"要覆盖的东西。
    def from_block(ts):
        k = next(i for i, t in enumerate(ts) if t.kind == "raw")
        return ts[k:]

    a = from_block(lomc.lex(_src("let")))
    b = from_block(lomc.lex(_src("bare")))
    shape = lambda ts: [(t.kind, t.len) for t in ts]  # noqa: E731
    sa, sb = shape(a), shape(b)
    assert sa == sb, f"两种写法的 token 形状不同:\n  let  {sa}\n  bare {sb}"
    ra = next(t for t in a if t.kind == "raw")
    rb = next(t for t in b if t.kind == "raw")
    assert ra.val == rb.val, "两种写法的正文不同"
    print(f"      {len(sa)} 个 token 形状相同；正文 {ra.len}B 相同")


@test
def test_bare_form_needs_a_declaration():
    """不带 `let` 的写法**只在声明过的语言名上认**（`docs/185` §3）。

    没声明就认的话，`struct S {` / `fn f() -> u32 {` 会被当成外部块 —— 那两处的 `{`
    前面也是标识符。这条同时钉住**反方向**：声明了之后才认。
    """
    undeclared = "module m\n\nc {\nint x = 1;\n}\n"
    assert not [t for t in lomc.lex(undeclared) if t.kind == "raw"], \
        "没声明过 `c`，`c {` 不该被当成外部块"
    declared = "module m\n\ncommand c\n\nc {\nint x = 1;\n}\n"
    assert len([t for t in lomc.lex(declared) if t.kind == "raw"]) == 1, \
        "声明过 `c` 之后，`c {` 应当是外部块"
    print("      消歧: 未声明不认、声明后认")


@test
def test_common_constructs_do_not_false_trigger():
    """`struct` / `fn` / `if` 的块**不该**被当成外部块（`docs/185` §3 的反面）。

    这三处都是"标识符后面跟 `{`"，正是消歧规则要挡的。第三个用例最刁：`c` **既是**
    声明过的语言名、又是个局部变量，`if c {` 在**函数体里**（非顶层）所以不认。
    """
    cases = {
        "struct": "module m\n\nstruct S {\n    a: u32,\n}\n",
        "fn": "module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
        "if-同名的语言名": ("module m\n\ncommand c\n\nfn g() -> u32 {\n"
                          "    let c: u32 = 1;\n    if c > 0 {\n        return c;\n    }\n"
                          "    return 0;\n}\n"),
        "let 绑定（`:` 与 `=`）": ("module m\n\nfn h() -> u32 {\n    let c: u32 = 1;\n"
                              "    let d = 2;\n    return c + d;\n}\n"),
    }
    for name, src in cases.items():
        got = [t.kind for t in lomc.lex(src)]
        assert "raw" not in got, f"{name}: 误判成外部块 -> {got}"
    print(f"      反例: {len(cases)} 种常见构造都不误触发")


@test
def test_body_round_trips_into_potato():
    """**正文往返逐字节相同** —— `docs/185` §6 给 S1.2/S1.3 定的证伪判据。

    源 →（词法 raw）→ AST → **Potato v5**，正文必须一个字节都不差。

    **为什么载体是 Potato 而不是 LLVM IR**：这一版**不编**那段正文（谁去编是 S2），
    所以 IR 里没有它 —— 而"产物里看得见正文"正是 `docs/179` 那条"形式对象要自解释"的
    要求。把正文塞进 IR 当字符串常量是**假动作**：LLVM IR 的字符串要转义，
    "逐字节相同"就变成了"转义再解回来相同"，那证明不了什么。

    反向那一半同样重要：**没有外部块的单元必须是空数组**，且校验器认 v5 ——
    不然这条只是在自说自话。
    """
    src = (ROOT / "loment" / "extblock" / "evil.lomt").read_text(encoding="utf-8")
    mod, deps = lomentc.load_unit(ROOT / "loment" / "extblock" / "evil.lomt", ROOT)
    assert lomentc.check(mod, deps=deps) == [], "外部块不该让 check 报错（它没什么可查的）"
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    # **不钉死版本号**（理由同 `loment_comefor_test` 那一处）：要的是"发的是当前那一版"。
    assert doc["potato"] == potato.VERSIONS[-1], doc["potato"]
    got = [(b["lang"], b["body"]) for b in doc["bodies"]]
    assert [g[0] for g in got] == ["c", "py"], f"语言名或顺序不对: {[g[0] for g in got]}"
    for lang, body in got:
        assert body.encode("utf-8") in src.encode("utf-8"), f"{lang}: 正文不是源里的那一段"
    # 正文里那三个"字符串字面量装不下"的字节，一个都不能少、不能变
    assert "\\0" in got[0][1] and '"' in got[0][1] and "中文" in got[0][1], got[0][1]
    assert potato.validate(doc) == [], potato.validate(doc)

    plain = ROOT / "loment" / "examples" / "bytes.lomt"
    m2, d2 = lomentc.load_unit(plain, ROOT)
    doc2 = json.loads(lomentc.emit_potato(m2, ROOT, d2))
    assert doc2["bodies"] == [], doc2["bodies"]
    assert potato.validate(doc2) == [], potato.validate(doc2)
    print(f"      Potato v5: 正文进产物（{[g[0] for g in got]}）; 无外部块 -> []")


# ---------------------------------------------------------------- Loment 版（S1 第六格）

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomextblock.lomt"
#: 四种**不该**误触发的常见构造（对应上面 `test_common_constructs_do_not_false_trigger`
#: 那个 dict 的四项）—— 孪生报的是这个数，不是"目录里文件数减二"。
_FALSE_CASES = ("struct.lomt", "fn.lomt", "ifc.lomt", "letbind.lomt")

#: 判据写出来、孪生读进去的那八个文件（文件名是两边的接口）。
_CASES = {
    "let.lomt": _src("let"),
    "bare.lomt": _src("bare"),
    "undeclared.lomt": "module m\n\nc {\nint x = 1;\n}\n",
    "declared.lomt": "module m\n\ncommand c\n\nc {\nint x = 1;\n}\n",
    "struct.lomt": "module m\n\nstruct S {\n    a: u32,\n}\n",
    "fn.lomt": "module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
    "ifc.lomt": ("module m\n\ncommand c\n\nfn g() -> u32 {\n"
                "    let c: u32 = 1;\n    if c > 0 {\n        return c;\n    }\n"
                "    return 0;\n}\n"),
    "letbind.lomt": ("module m\n\nfn h() -> u32 {\n    let c: u32 = 1;\n"
                     "    let d = 2;\n    return c + d;\n}\n"),
}


def _clang() -> str | None:
    import shutil
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
    import shutil
    if not shutil.which("wsl"):
        return False
    try:
        import subprocess
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build_twin(td: Path) -> Path:
    import subprocess
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomextblock.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomextblock.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomextblock.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _run_twin(elf: Path, td: Path, indir: Path) -> tuple[int, str]:
    import subprocess
    outp = td / "twin.out"
    binn = f"{_T}lomextblock.bin"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {_wsl_path(indir)} > {_wsl_path(outp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out


def _expected_report(indir: Path) -> str:
    """**用参考词法器独立算一遍**孪生该打的那五行（不复用上面任何一条检查的中间值）。

    这里同时也是"两个词法器在外部代码块这件事上给出同一个答案"的判据: 孪生那侧用的是
    自举侧 `loment/selfhost/lexer.lomt`, 这一侧是 `lomc.lex`。
    """
    line1 = ('      字节保真: 正文 {n}B（含 " \\ \\0 非 ASCII）两种写法都逐字节相同')
    bodies, shapes, n_shape = {}, {}, 0
    for form in ("let", "bare"):
        src = (indir / f"{form}.lomt").read_text(encoding="utf-8")
        ts = lomc.lex(src)
        raws = [t for t in ts if t.kind == "raw"]
        assert len(raws) == 1, f"{form}: 应当恰好一个 raw token, 实得 {len(raws)}"
        r = raws[0]
        body = src[r.off:r.off + r.len].encode("utf-8")
        # 保真的**操作定义**: 跨度正好被 `{` 与 `}` 夹住（不是"与某个 Python 串相等"）
        assert src[r.off - 1] == "{" and src[r.off + r.len] == "}", (form, r.off, r.len)
        assert any(b >= 128 for b in body), f"{form}: 正文里没有非 ASCII 字节?"
        bodies[form] = body
        k = next(i for i, t in enumerate(ts) if t.kind == "raw")
        shapes[form] = [(t.kind, t.len) for t in ts[k:]]
        n_shape = len(shapes[form])
    assert bodies["let"] == bodies["bare"], "两种写法的正文不同"
    assert shapes["let"] == shapes["bare"], "两种写法的 token 形状不同"
    n = len(bodies["let"])
    out = [line1.format(n=n), line1.format(n=n),
           f"      {n_shape} 个 token 形状相同；正文 {n}B 相同",
           "      消歧: 未声明不认、声明后认",
           f"      反例: {len(_FALSE_CASES)} 种常见构造都不误触发"]
    return "\n".join(out) + "\n"


@test
def test_extblock_check_matches_loment_twin():
    """上面四条检查（用参考词法器），**Loment 版（用自举侧词法器）给出的结论逐字节相同**。

    `docs/189` §3 的 S1 第六格。判据把八个输入文件写出来（文件名是两个实现的接口），
    两边读同一份、各打各的报告比一遍。

    **没比的一档**：`test_body_round_trips_into_potato` —— 那要自举侧的 potato 通路
    （`docs/189` §4 认过的另一根大轴），孪生不搬它，这里也不比。
    """
    import tempfile
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        indir = td / "in"
        indir.mkdir()
        for name, text in _CASES.items():
            with (indir / name).open("w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        want = _expected_report(indir)
        elf = _build_twin(td)
        rc, got = _run_twin(elf, td, indir)
    assert rc == 0, f"孪生该退 0（都命中）: rc={rc}\n{got[:300]}"
    assert got == want, f"结论与参考词法器那侧不同:\n  py     {want!r}\n  loment {got!r}"
    # 独立期望值: 正文长度必须等于 **原文那一段** 的字节数（不经过任何词法器）
    n_evil = len(("\n" + _EVIL).encode("utf-8"))
    assert f"正文 {n_evil}B" in got, f"正文长度与原文对不上（原文 {n_evil}B）: {got!r}"
    print(f"      结论与参考词法器逐字节相同；正文 {n_evil}B 另经原文独立核对")


@test
def test_extblock_twin_selfhost_compiles():
    """`lomextblock.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
    import subprocess
    import tempfile
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}extblock_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomextblock.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomextblock.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomextblock.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_extblock_test")


if __name__ == "__main__":
    sys.exit(main())
