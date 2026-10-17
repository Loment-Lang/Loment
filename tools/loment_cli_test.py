#!/usr/bin/env python3
# loment_cli_test.py — 自举 CLI 前端 loment-cli 的判据 (docs/169)
#
# 判的是 loment/tools/lomcli.lomt 链出来的**可执行文件**本身: 在本机原生跑, 逐条验命令的
# 输出、退出码、与"算出来的"文本 (sha256 对 hashlib, 行数对 Python 自己数)。**不比对 Python
# 实现** —— 这些命令没有 Python 版, 它自己就是实现 (与 lomfmt/lompkg 的孪生判据不同类)。
#
# 另有一条**防漂移**判据 (test_launcher_and_catalog_do_not_drift): 启动器里按名处理/转发的
# 命令, 必须与 `loment commands` 打出来的目录一致 —— 启动器有两份 (bash + batch), 命令面
# 又在第三处 (Loment 源码), 三处不同步就是"help 里没有、但确实能敲"的那种烂。
#
# 运行: python tools/loment_cli_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import hashlib
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
import lomelf  # noqa: E402
import lomc  # noqa: E402  (边界操作那条判据的**独立**尺子: 参考实现真正的词法器)
import potato  # noqa: E402  (边界内建那份清单的**单一真源**: potato.BOUNDARY_BUILTINS)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomcli.lomt"
IS_WIN = sys.platform == "win32"
#: 目录里至少要有这么多命令 (用户 2026-09-15 的要求: 至少 30 条)
CATALOG_FLOOR = 30
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 构建

_built: Path | None = None
_pkg: Path | None = None


def _build() -> Path:
    """建一次, 全测复用。在本机直接出目标格式: Windows 出 PE (能直接跑), 别的出 ELF。"""
    global _built, _pkg
    if _built is not None:
        return _built
    td = Path(tempfile.mkdtemp(prefix="lomcli-"))
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomcli.lomt 自己检查不过: {errs[:3]}"
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    raw = (lomelf.compile_pe if IS_WIN else lomelf.compile_ll)(ll)[0]
    assert raw, "链接器产出为空"
    # 放进一个**假包布局**里: CLI 靠 argv[0] 自定位, share/loment/version 要能找得到
    pkg = td / "pkg"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "share" / "loment" / "examples").mkdir(parents=True)
    exe = pkg / "bin" / ("loment-cli.exe" if IS_WIN else "loment-cli")
    exe.write_bytes(raw)
    exe.chmod(0o755)
    (pkg / "share" / "loment" / "version").write_text(
        "Loment 0.1.4 Pre2 (0.1.4-pre2), commit 0123456\nbuild 2026-09-15\n",
        encoding="utf-8", newline="\n")
    (pkg / "share" / "loment" / "examples" / "tour.lomt").write_text(
        "module tour\n\nfn _start() {\n    syscall4(60, 0, 0, 0);\n}\n",
        encoding="utf-8", newline="\n")
    _built, _pkg = exe, pkg
    return exe


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    exe = _build()
    r = subprocess.run([str(exe), *args], cwd=str(cwd or exe.parent),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    return r.returncode, r.stdout, r.stderr


def _names() -> list[str]:
    rc, out, _ = _run(["commands"])
    assert rc == 0, f"commands 退出 {rc}"
    return [x for x in out.splitlines() if x.strip()]


def _no_color() -> list[str]:
    return ["--no-color"]


# ---------------------------------------------------------------- 构建与入口

@test
def test_builds_and_has_entry():
    """链出的二进制非空, 且能跑起来 (没有 _start 的话链接器自己就会拒)。"""
    exe = _build()
    assert exe.stat().st_size > 50000, f"产物太小: {exe.stat().st_size}"
    rc, _, _ = _run(["version"])
    assert rc == 0, f"version 退出 {rc}"


@test
def test_reference_backend_agrees_with_selfhost_ir():
    """自举镜编 lomcli.lomt 的 IR, 与参考实现逐字节相同。

    这条保证 lomcli 是**自举链里的一等公民**: 装发行包时它由 stage1 编出来, 不是靠
    Python 特供。用的是发行包构建的同一条路 (loment_dist.build_stage1)。
    没有 clang 时跳过 —— stage1 要靠 clang 链一次。
    """
    clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    if not (clang and Path(clang).exists()):
        print("      SKIP: 无 clang")
        return
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    stage1 = loment_dist.build_stage1()
    want = lomentc.emit_llvm(*_load_src())
    r = subprocess.run([str(stage1), SRC.relative_to(ROOT).as_posix()],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=300)
    assert r.returncode == 0, f"stage1 编译失败: {r.stderr[-300:]}"
    # 参考经 Python stdout 出去时会被 Windows 文本模式翻成 CRLF, 镜直接 write 是 LF ——
    # 读时统一成 LF 再比 (语义上两边都是 LF)。
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, (
        f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)")


def _load_src():
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    return mod, ROOT, deps


# ---------------------------------------------------------------- 目录与帮助

@test
def test_catalog_has_at_least_thirty_commands():
    ns = _names()
    assert len(ns) >= CATALOG_FLOOR, f"命令只有 {len(ns)} 条 (要求 >= {CATALOG_FLOOR})"
    for must in ("help", "codes", "explain", "syntax", "builtins", "stat", "grep",
                 "hash", "ls", "tree", "new", "examples", "doctor", "color"):
        assert must in ns, f"目录里少了 {must}"


@test
def test_help_mentions_every_command():
    """`help` 总览里必须出现 `commands` 列的每一个名字 —— 否则就是"能敲但查不到"。"""
    _, out, _ = _run(["help"] + _no_color())
    missing = [n for n in _names() if not re.search(rf"\b{re.escape(n)}\b", out)]
    assert not missing, f"help 里没提到: {missing}"


@test
def test_every_catalog_command_is_actually_dispatchable():
    """`commands` 列出来的每一条都必须真能跑 —— 不能有"查得到、敲了说未知命令"的。

    这条是写 lib/pkg 时踩出来的: 它们当时列在目录里, 但发行包根本没有这两条
    (库系统在仓库侧, 而且是 Python), 敲下去只会得到"未知命令"。

    ⚠ **它此前是一条死判据**（2026-09-19 写 S1 第十二格时发现）：断言串一直写着**中文**
    `未知命令`，而 CLI 的文案早就改成纯 ASCII 了 —— 它自己的判据
    `test_every_command_outputs_pure_ascii` 就是那么要求的。所以这条**永远为真**，
    目录里真列一条敲不动的命令它也不会红。现在按 CLI 真会说出的那句断言。
    """
    bad = []
    for n in _names():
        rc, _, err = _run([n] + _no_color())
        if "unknown command" in err:
            bad.append(n)
    assert not bad, f"目录里列了但敲不了: {bad}"


@test
def test_help_page_for_one_command():
    rc, out, _ = _run(["help", "grep"] + _no_color())
    assert rc == 0 and "PAT" in out and "FILE" in out, out[:200]
    # 表里有一行、但没有详细页的命令, 不能崩, 也要给条出路
    rc, out, _ = _run(["help", "caps"] + _no_color())
    assert rc == 0, rc


@test
def test_every_command_outputs_pure_ascii():
    """**每一条命令的输出都必须是纯 ASCII。**

    用户 2026-09-15 实测报的乱码（八个感叹号）：Windows 上 PE 把字节直接写进控制台，
    而控制台按**当前代码页**解 —— 中文 Windows 是 936(GBK)，于是 UTF-8 的中文被按 GBK
    解成 `婧愮爜缁熻`。垫片**没有 `WriteConsoleW`**，程序这边没有任何补救手段。

    ASCII 是唯一**在任何代码页下都解码成同一个结果**的集合，所以这条不是风格问题：
    非 ASCII 就算"在我这台机器上看着是好的"，到了 936 的控制台上就是乱码。

    lompi 线已经因为同一条把它的输出全改成纯 ASCII 了 —— 这里是同一个坑的另一半。
    """
    bad = []
    for n in _names():
        rc, out, err = _run([n] + _no_color())
        # delegate 的几条（ir/build/... 由启动器转发）只打一行说明，也要守
        t = out + err
        if not t.isascii():
            where = sorted({c for c in t if ord(c) > 127})[:6]
            bad.append((n, where))
    assert not bad, f"这些命令的输出带非 ASCII（936 控制台下必乱码）: {bad}"


@test
def test_source_has_no_non_ascii_string_literals():
    """静态判据：`lomcli.lomt` 的字符串字面量里不许有非 ASCII。

    上面那条动态判据只跑 38 次调用 —— 没走到的分支（某个错误路径、某个 `help <cmd>`）
    里面藏着中文它抓不到。这条按源码扫，一个都不放过。注释里的中文无所谓：注释不进二进制。
    """
    NL = chr(10)
    Q = chr(34)
    BS = chr(92)
    src = SRC.read_text(encoding="utf-8")
    body = re.sub(r"/[*].*?[*]/", "", src, flags=re.S)
    body = re.sub("//[^" + NL + "]*", "", body)
    strlit = re.compile(Q + "(?:[^" + BS + Q + "]|" + BS + BS + ".)*" + Q)
    bad = []
    for i, line in enumerate(body.split(NL), 1):
        for m in strlit.finditer(line):
            if any(ord(c) > 127 for c in m.group(0)):
                bad.append((i, m.group(0)[:60]))
    assert not bad, f"这些字符串字面量里有非 ASCII（936 控制台下必乱码）: {bad[:5]}"

@test
def test_unknown_command_is_an_error():
    rc, out, err = _run(["frobnicate"] + _no_color())
    assert rc == 2, f"未知命令应退 2, 实得 {rc}"
    assert "unknown command" in err and "frobnicate" in err


# ---------------------------------------------------------------- 颜色

@test
def test_color_on_by_default_and_off_with_flag():
    _, on, _ = _run(["about"])
    assert "\x1b[" in on, "默认应当上色"
    _, off, _ = _run(["--no-color", "about"])
    assert "\x1b[" not in off, "--no-color 之后不该还有转义序列"


@test
def test_color_flag_anywhere_in_argv_does_not_eat_the_command():
    """`loment --no-color stat F` 里 stat 仍要被当成命令 (位置无关的全局开关)。"""
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, err = _run(["--no-color", "stat", str(f)])
    assert rc == 0, f"rc={rc} err={err[:200]}"
    assert "Source statistics" in out


# ---------------------------------------------------------------- 真算出来的东西

@test
def test_hash_rejects_directory():
    f = _pkg / "share" / "loment" / "examples"
    rc, out, err = _run(["hash", str(f)] + _no_color())
    assert rc != 0, f"hash on directory should fail, got rc={rc}, out={out}"
    assert "not a file" in err, f"expected 'not a file' in stderr, got: {err}"
    # 只查 rc 与 stderr 不够: 旧行为是**先打印 sha256 空串再退 0** —— 用一个合法输入的
    # 合法摘要回答了另一个问题 (#54)。所以这里要钉住"什么都没打印"。
    assert out.strip() == "", f"目录参数下不该打印摘要, 实得: {out!r}"


@test
def test_hash_matches_hashlib():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["hash", str(f)])
    assert rc == 0
    assert out.strip() == hashlib.sha256(f.read_bytes()).hexdigest()


@test
def test_stat_counts_match_what_python_counts():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["stat", str(f)] + _no_color())
    assert rc == 0
    raw = f.read_bytes()
    lines = raw.decode().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    nums = [int(m) for m in re.findall(r"\b(\d+)\s*$", out, re.M)]
    assert str(len(raw)) in out, f"字节数不对: {out}"
    assert str(len(lines)) in out, f"行数不对 (Python 数出 {len(lines)}): {out}"


def _boundary_counts(text: str) -> dict[str, int]:
    """**独立**数一遍边界操作 —— 走参考实现真正的词法器 (`lomc.lex`), 不用字节扫描。

    被验的那份 (`lomcli.lomt` 里的 `cnt_calls`) 是它自己手写的字节扫描器; 这里走的是
    编译器那条路。两边的算法不同, 数出来的东西必须一样 —— 这条判据的意义就在这里。
    """
    toks = [t for t in lomc.lex(text) if t.kind != "eof"]

    def calls(name: str) -> int:
        # `fn NAME(` 是**声明**, 不是调用 —— 少了这一条, 谁定义了一个叫 `ptr_add`
        # 的函数, 就会凭空多出一次"越界"。
        return sum(1 for i, t in enumerate(toks)
                   if t.kind == "ident" and t.val == name
                   and i + 1 < len(toks) and toks[i + 1].kind == "punct"
                   and toks[i + 1].val == "("
                   and not (i > 0 and toks[i - 1].kind == "ident"
                            and toks[i - 1].val == "fn"))

    externs: list[str] = []
    i = 0
    while i + 2 < len(toks):
        if (toks[i].kind == "ident" and toks[i].val == "extern"
                and toks[i + 1].kind == "ident" and toks[i + 1].val == "fn"
                and toks[i + 2].kind == "ident"):
            externs.append(toks[i + 2].val)
            i += 3
            continue
        i += 1
    syscalls = sum(calls(b) for b in potato.BOUNDARY_BUILTINS if b.startswith("syscall"))
    ptrs = sum(calls(b) for b in potato.BOUNDARY_BUILTINS if not b.startswith("syscall"))
    extc = sum(calls(nm) for nm in externs)
    return {"extern fn declared": len(externs), "extern call sites": extc,
            "syscalls": syscalls, "raw ptr transforms": ptrs,
            "total sites": extc + syscalls + ptrs}


@test
def test_stat_boundary_counts_match_the_lexer():
    """`stat` 的「边界操作」这几行, 必须与**词法器**数出来的**逐项**相等。

    **Why** (`docs/205` R5): `unsafe` 在 Loment 里**不是关键字**, 所以这条边界不是语法
    划的, 是由**名字**划的 —— "越过语言保证的那几个内建"加上"本单元自己声明的 `extern fn`"。
    它的全部价值在"可 grep、可计数、可审计"上, 所以**数错了就等于没有**。
    **How to apply**: 语料是仓库里**全部**示例, 不是一份。一份只能证明那条路走得通,
    证明不了边界: 注释里出现的名字、字符串里的名字、`extern fn` 声明处自己那个 `(`,
    都是要**不数**或**减掉**的。
    """
    srcs = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    assert len(srcs) >= 30, f"示例集变小了: {len(srcs)}"
    bad: list[str] = []
    for f in srcs:
        rc, out, err = _run(["stat", str(f)] + _no_color())
        if rc != 0:
            bad.append(f"{f.name}: stat rc={rc} {err.strip()[:60]}")
            continue
        for key, exp in _boundary_counts(f.read_text(encoding="utf-8")).items():
            m = re.search(rf"{re.escape(key)}\s*(\d+)\s*$", out, re.M)
            if not m:
                bad.append(f"{f.name}: 输出里没有 `{key}`")
            elif int(m.group(1)) != exp:
                bad.append(f"{f.name}: {key} 报 {m.group(1)}, 词法器数出 {exp}")
    assert not bad, "边界操作数与词法器不一致: " + " / ".join(bad[:6])

    # 夹具: 五个数**写死**。语料只证明"跑得通", 夹具才证明"数得对" ——
    # 边缘 (注释里的名字、字符串里的名字、以它开头的长名字、声明处那个 `(`)
    # 全在 `loment/lex/boundary.lomt` 里, 而它在语料里一次都碰不到。
    fix = ROOT / "loment" / "lex" / "boundary.lomt"
    pinned = {"extern fn declared": 2, "extern call sites": 3, "syscalls": 4,
              "raw ptr transforms": 4, "total sites": 11}
    got_o = _boundary_counts(fix.read_text(encoding="utf-8"))
    assert got_o == pinned, f"夹具自己的期望值漂了: 词法器数出 {got_o}, 写的是 {pinned}"
    rc, out, err = _run(["stat", str(fix)] + _no_color())
    assert rc == 0, f"词法夹具 stat rc={rc}: {err.strip()[:80]}"
    for key, exp in pinned.items():
        m = re.search(rf"{re.escape(key)}\s*(\d+)\s*$", out, re.M)
        assert m and int(m.group(1)) == exp, (
            f"夹具 ({fix.name}): {key} 报 {m.group(1) if m else '缺'}, 期望 {exp}")


@test
def test_cat_prints_numbered_lines():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["cat", str(f)] + _no_color())
    assert rc == 0
    assert out.splitlines()[0].strip().startswith("1"), out[:80]
    assert len(out.splitlines()) == 5, f"行数不对: {out!r}"


@test
def test_grep_line_numbers_and_exit_codes():
    d = Path(tempfile.mkdtemp(prefix="lomcli-grep-"))
    f = d / "x.lomt"
    f.write_text("one\nalpha\ntwo\nalpha\nthree\n", encoding="utf-8", newline="\n")
    rc, out, _ = _run(["grep", "alpha", str(f)] + _no_color())
    assert rc == 0, rc
    assert [int(x.split(":")[0]) for x in out.splitlines()] == [2, 4], out
    rc, _, _ = _run(["grep", "nope", str(f)] + _no_color())
    assert rc == 1, f"没命中应退 1, 实得 {rc}"
    rc, _, err = _run(["grep", "onlypat"] + _no_color())
    assert rc == 2, f"缺 FILE 应退 2, 实得 {rc}"


@test
def test_count_and_fns_on_a_known_file():
    d = Path(tempfile.mkdtemp(prefix="lomcli-cnt-"))
    f = d / "x.lomt"
    f.write_text(
        "module x\nchoose runtime\n\nstruct S {\n    a: u32,\n}\n\n"
        "enum E {\n    A,\n}\n\n"
        "fn one() -> u32 {\n    return 1;\n}\n\n"
        "pub fn two(a: u32) -> u32 {\n    return a;\n}\n",
        encoding="utf-8", newline="\n")
    rc, out, _ = _run(["count", str(f)] + _no_color())
    assert rc == 0
    got = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", out, re.M))
    assert got.get("fn") == "2" and got.get("struct") == "1" and got.get("enum") == "1", out
    rc, out, _ = _run(["fns", str(f)] + _no_color())
    assert rc == 0
    assert "fn one() -> u32" in out and "fn two(a: u32) -> u32" in out, out


@test
def test_tokens_picks_up_strings_and_comments():
    d = Path(tempfile.mkdtemp(prefix="lomcli-tok-"))
    f = d / "x.lomt"
    f.write_text('module x\nchoose runtime\n\n// 注释\nfn f() -> u32 {\n    let s: str = "ab";\n    return 1;\n}\n',
                 encoding="utf-8", newline="\n")
    rc, out, _ = _run(["tokens", str(f)] + _no_color())
    assert rc == 0
    got = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", out, re.M))
    assert got.get("strings") == "1", out
    assert got.get("comments") == "1", out
    assert got.get("keywords") and int(got["keywords"]) >= 4, out


# ---------------------------------------------------------------- 文件与目录

@test
def test_missing_file_is_a_clean_error_not_a_crash():
    rc, out, err = _run(["cat", "/definitely/not/here.lomt"] + _no_color())
    assert rc == 1, f"rc={rc}"
    assert "cannot open" in err, err[:200]


@test
def test_ls_and_tree_mark_directories():
    d = Path(tempfile.mkdtemp(prefix="lomcli-ls-"))
    (d / "sub").mkdir()
    (d / "sub" / "deep").mkdir()
    (d / "sub" / "deep" / "f.lomt").write_text("module f\n", encoding="utf-8")
    (d / "a.lomt").write_text("module a\n", encoding="utf-8")
    rc, out, _ = _run(["ls", str(d)] + _no_color())
    assert rc == 0
    assert "sub/" in out and "a.lomt" in out and "sub\n" not in out, out
    rc, out, _ = _run(["tree", str(d)] + _no_color())
    assert rc == 0
    assert "deep/" in out and "f.lomt" in out, out
    assert re.search(r"^\s+f\.lomt", out, re.M), "tree 的缩进没体现层级"


@test
def test_new_writes_a_skeleton_that_compiles():
    """`new` 出来的东西必须**真能编** —— 生成的骨架过了参考实现的 check + emit。"""
    d = Path(tempfile.mkdtemp(prefix="lomcli-new-"))
    rc, out, _ = _run(["new", "hello"], cwd=d)
    assert rc == 0, out
    f = d / "hello.lomt"
    assert f.exists(), "没写出 hello.lomt"
    mod = lomentc.load(f)
    deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
    assert not lomentc.check(mod, deps=deps), "生成的骨架检查不过"
    assert lomentc.emit_llvm(mod, ROOT, deps), "生成的骨架发射不出 IR"
    assert "_start" in f.read_text(encoding="utf-8")


@test
def test_new_refuses_to_overwrite():
    d = Path(tempfile.mkdtemp(prefix="lomcli-new2-"))
    (d / "hi.lomt").write_text("module hi\n", encoding="utf-8")
    rc, _, err = _run(["new", "hi"], cwd=d)
    assert rc == 1, f"已存在应当退 1, 实得 {rc}"
    assert (d / "hi.lomt").read_text(encoding="utf-8") == "module hi\n", "把原文件覆盖了"


# ---------------------------------------------------------------- 包内自定位

@test
def test_version_reads_share_version():
    rc, out, _ = _run(["version"])
    assert rc == 0
    assert out.startswith("Loment 0.1.4 Pre2"), out
    assert "commit 0123456" in out


@test
def test_doctor_reports_missing_tools_then_green_when_present():
    """体检必须有**分辨力**: 缺组件时红且退 1, 组件齐了就绿且退 0。"""
    rc, out, _ = _run(["doctor"] + _no_color())
    assert rc == 1, f"缺驱动时应当退 1, 实得 {rc}"
    assert "MISSING" in out
    # 把七个名字都补上 (内容无所谓, 只要有这个文件)
    for n in ("loment-driver", "loment-lsp", "loment-fmt", "loment-doc",
              "loment-lomelf", "loment-cli", "lomenterr"):
        p = _pkg / "bin" / (n + ".exe" if IS_WIN else n)
        if not p.exists():
            p.write_bytes(b"stub")
    rc, out, _ = _run(["doctor"] + _no_color())
    assert rc == 0, f"组件齐了还退 {rc}: {out}"
    assert "all green" in out


@test
def test_tools_counts_and_threshold_agree():
    """`loment tools` 的分母与门槛必须是和工具清单**同一个数**。

    原先分母印的是写死的 `" / 6"`、门槛写的是 `ok < 6`，而 `tool_name` 有 7 项 —— 于是
    缺一个组件时 `ok` 为 6，`6 < 6` 是假: **报绿且退 0**。这条检查因此没有分辨力，而当时
    没有任何判据会红。这里两头都测: 齐了要 `n / n` 且退 0，缺一个要报出 `n-1 / n` 且退 1。
    """
    names = ["loment-driver", "loment-lsp", "loment-fmt", "loment-doc",
             "loment-lomelf", "loment-cli", "lomenterr"]
    bindir = _pkg / "bin"
    added: list[Path] = []
    for n in names:
        p = bindir / (n + ".exe" if IS_WIN else n)
        if not p.exists():
            p.write_bytes(b"stub")
            added.append(p)
    victim = bindir / ("lomenterr.exe" if IS_WIN else "lomenterr")
    saved = victim.read_bytes()
    try:
        rc, out, _ = _run(["tools"] + _no_color())
        assert rc == 0, f"组件齐了应当退 0, 实得 {rc}: {out[:200]}"
        assert f"present: {len(names)} / {len(names)}" in out, out[:200]
        victim.unlink()
        rc, out, _ = _run(["tools"] + _no_color())
        assert f"present: {len(names) - 1} / {len(names)}" in out, out[:200]
        assert rc == 1, f"缺一个组件必须退 1 (分母与门槛要一致), 实得 {rc}: {out[:200]}"
    finally:
        # 这个假包全测共用: 动过就放回去, 别让下一条判据看到我的痕迹
        victim.write_bytes(saved)
        for p in added:
            p.unlink(missing_ok=True)


@test
def test_where_resolves_and_reports_the_expected_path():
    rc, out, _ = _run(["where", "driver"])
    assert rc == 0, rc
    assert out.strip().endswith("loment-driver" + (".exe" if IS_WIN else "")), out
    rc, _, err = _run(["where", "nosuchtool"] + _no_color())
    assert rc == 2, rc
    assert "no such tool" in err


@test
def test_examples_and_example_read_the_package():
    rc, out, _ = _run(["examples"] + _no_color())
    assert rc == 0, rc
    assert "tour.lomt" in out
    rc, out, _ = _run(["example", "tour"])
    assert rc == 0 and "module tour" in out, out[:200]
    rc, _, err = _run(["example", "nope"] + _no_color())
    assert rc == 1 and "no such example" in err, err[:200]


@test
def test_share_paths_are_normalized():
    """`loment env` / `loment examples` 打的是**给用户读的路径** —— 里面不该有 `..`。

    `share_dir` 原先拼的是 `<bindir>/../share/loment/`，同一个目录，但屏幕上出现
    `.../bin/../share/loment/examples/`：能打开，却是让读者自己去归一化。三个命令
    (`env` / `examples` / `example` 的错误路径) 共用这个字符串，所以在这里一起钉住。
    """
    for cmd in (["env"], ["examples"]):
        rc, out, _ = _run(cmd + _no_color())
        assert rc == 0, (cmd, rc)
        bad = [ln for ln in out.splitlines() if ".." in ln]
        assert not bad, f"{cmd} 打出了未归一化的路径: {bad}"
        assert "share/loment/" in out.replace("\\", "/"), (cmd, out[:200])


# ---------------------------------------------------------------- 参考页

@test
def test_codes_lists_every_code_in_the_table():
    """`loment codes` 的每一行都必须来自 `loment_diag` 那张表 —— **一个码都不能少**。

    这一条原先只扫到 E19 (写下它时表就到那儿), 于是 E20-E23 加进来时它照样绿 —— 而它的名字
    ("all_nineteen") 正是那种会悄悄过期的硬编码。现在迭代**真源本身**: 表里有的码,
    `loment codes` 里必须都印出来。旧实现手抄 23 条 `codrow`, 加一个码忘了改那边就是
    "新码凭空消失", 没有任何判据会红 (docs/182 5.2)。
    """
    import loment_diag
    rc, out, _ = _run(["codes"] + _no_color())
    assert rc == 0
    missing = [c for c in sorted(loment_diag.ASCII_ONE_LINER) if f"E{c} " not in out]
    assert not missing, f"错误码表里没有 E{missing}"
    # 说明文字也要是表里那一份, 不是另写的
    for c, desc in loment_diag.ASCII_ONE_LINER.items():
        assert desc in out, f"E{c} 的说明不是 surface_data 里那一份: {desc!r}"


@test
def test_explain_accepts_three_spellings_and_rejects_junk():
    for spelling in ("E4", "e4", "4"):
        rc, out, _ = _run(["explain", spelling] + _no_color())
        assert rc == 0 and "Capability domain" in out, (spelling, out[:120])
    # **上界从真源推导, 不写死**。原先这里写 "E1..E23", 于是每加一个码就得手改这个测试 ——
    # 漏改时的症状是"判据红了但源码没错"; 而更糟的一种改法是把断言放宽, 从此再也不测边界。
    # 真实的契约是"explain 接受到表里最后一个码为止", 那就照它测。
    import loment_diag
    top = max(loment_diag.ASCII_ONE_LINER)
    rc, _, err = _run(["explain", f"E{top + 1}"] + _no_color())
    assert rc == 2, err[:120]
    for c in (top, top - 1, top - 2):
        rc, out, _ = _run(["explain", f"E{c}"] + _no_color())
        assert rc == 0 and f"E{c}" in out, (c, rc, out[:120])
    rc, _, _ = _run(["explain"] + _no_color())
    assert rc == 2


@test
def test_every_code_explain_speaks():
    """`loment explain E<n>` 对**表里每一个码**都要说出一句真话。

    原先的长文只覆盖 8/23 个码，其余落进一个通用兜底 —— 用户敲 `loment explain E7`
    拿到的是"去 `loment codes` 那张表里找"，而这个码到底什么意思一个字都没有。
    修法不是把那 15 条长文补上（那是翻译项目），而是**每个码先给一行来自真源的话**
    （`surface_data.code_ascii`）。所以这条判据是"表里有的码，explain 里必须都有"——
    加一个码而 explain 说不出话，它会红。
    """
    import loment_diag
    for c, desc in sorted(loment_diag.ASCII_ONE_LINER.items()):
        rc, out, _ = _run(["explain", f"E{c}"] + _no_color())
        assert rc == 0, (c, rc, out[:120])
        assert desc in out, f"explain E{c} 里没有表里那一行: {out[:200]!r}"
    print(f"      {len(loment_diag.ASCII_ONE_LINER)} 个码 explain 都说得出一句真话")

@test
def test_reference_pages_are_nonempty():
    for c in ("syntax", "builtins", "types", "keywords", "caps", "cheat", "about", "env"):
        rc, out, _ = _run([c] + _no_color())
        assert rc == 0, f"{c} 退出 {rc}"
        assert len(out.strip()) > 60, f"{c} 输出太短: {out!r}"


# ---------------------------------------------------------------- 防漂移

@test
def test_launcher_and_catalog_do_not_drift():
    """启动器按名处理/转发的命令, 必须在 `loment commands` 的目录里。

    三处会各自漂: bash 启动器 (bin/loment)、batch 启动器 (bin/loment.cmd)、命令目录
    (lomcli.lomt 的 names_dump)。前两处是**生成**的 (loment_dist 里的常量), 所以直接
    读那两份常量即可 —— 不用真去打一个包。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    catalog = set(_names())

    sh = loment_dist.LAUNCHER_SH
    case = re.search(r"case \"\$\{1:-help\}\" in(.*?)\nesac", sh, re.S)
    assert case, "解析不出 bash 启动器的 case"
    sh_names: set[str] = set()
    for line in case.group(1).splitlines():
        m = re.match(r"\s{4}([A-Za-z0-9|_-]+)\)", line)
        if m:
            for alt in m.group(1).split("|"):
                if not alt.startswith("-") and alt != "*":
                    sh_names.add(alt)
    assert sh_names, "bash 启动器里一个命令都没解析出来"
    missing = sorted(sh_names - catalog)
    assert not missing, f"bash 启动器处理了但目录里没有: {missing}"

    cmd = loment_dist.LAUNCHER_CMD
    cmd_names = set(re.findall(r'if "%cmd%"=="([A-Za-z0-9_-]+)" goto', cmd))
    cmd_names |= set(re.findall(r'if "%cmd%"=="(-[A-Za-z-]+)" goto', cmd))
    cmd_names = {n for n in cmd_names if not n.startswith("-")}
    assert cmd_names, "batch 启动器里一个命令都没解析出来"
    missing = sorted(cmd_names - catalog)
    assert not missing, f"cmd 启动器处理了但目录里没有: {missing}"

    # 两个启动器认得的命令集必须一致 —— 不然 Windows 与 Linux 行为分叉
    assert sh_names == cmd_names, (
        f"两个启动器不一致: 只有 bash 有 {sorted(sh_names - cmd_names)}, "
        f"只有 cmd 有 {sorted(cmd_names - sh_names)}")


@test
def test_launcher_forwards_unknown_to_cli():
    """启动器的兜底必须是**转发给 loment-cli**, 不是自己再写一份 usage。"""
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    assert 'exec "$cli" "$@"' in loment_dist.LAUNCHER_SH
    assert '"%here%loment-cli.exe" %*' in loment_dist.LAUNCHER_CMD
    # 两个启动器都必须是纯 ASCII (PowerShell 5.1 按 ANSI 读无 BOM 脚本, docs/157 §3.4)
    for name, txt in (("launcher.sh", loment_dist.LAUNCHER_SH),
                      ("launcher.cmd", loment_dist.LAUNCHER_CMD)):
        bad = [(i, c) for i, c in enumerate(txt) if ord(c) > 127]
        assert not bad, f"{name} 里有非 ASCII 字符: {bad[:3]}"


# ---------------------------------------------------------------- 用户自定义命令 (git 模型)

@test
def test_user_command_on_path_is_run():
    """`loment foo` -> PATH 上的 `loment-foo`（就是 `git foo` -> `git-foo`）。

    这是"**用 Loment 写的软件注册一条命令**"的唯一机制: 把程序编成 `loment-foo`
    放上 PATH 就完了 —— 不需要声明、不需要重建 Loment。所以它必须**真的**能跑, 而且
    **不能把自己的名字当参数传下去**（`loment foo a b` 要变成 `loment-foo a b`）。
    这里跑真的 bash 启动器 + 桩 loment-cli, 断言三件事: 命中用户命令、没命中仍转发、
    内置命令不被顶掉。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        pf = t / "pf"
        (pf / "bin").mkdir(parents=True)
        (pf / "share" / "loment").mkdir(parents=True)
        (pf / "share" / "loment" / "version").write_bytes(b"FAKE VERSION\n")
        cli = pf / "bin" / "loment-cli"
        cli.write_bytes(b'#!/bin/sh\necho "OFFICIAL $@"\n')
        lom = pf / "bin" / "loment"
        lom.write_bytes(loment_dist._subst(loment_dist.LAUNCHER_SH).encode("utf-8"))
        up = t / "userbin"
        up.mkdir()
        user = up / "loment-foo"
        user.write_bytes(b'#!/bin/sh\necho "USER $@"\n')
        for p in (cli, lom, user):
            p.chmod(0o755)

        bash = shutil.which("bash")
        if not bash:
            print("         (跳过: 没有 bash, 跑不了 POSIX 启动器)")
            return
        def shp(p: Path) -> str:
            """bash 认的路径: Windows 盘符转成 /c/...（Linux 上原样）。"""
            s = str(p).replace("\\", "/")
            return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s

        env = dict(os.environ)
        # **前置**到原 PATH 上: 换成只有这两个目录, bash 自己就找不到 dirname/cat 了
        env["PATH"] = f"{shp(pf / 'bin')}:{shp(up)}:{env.get('PATH', '')}"

        def run(*args: str) -> str:
            r = subprocess.run([bash, shp(lom), *args], cwd=str(t), env=env,
                               capture_output=True, text=True, timeout=60)
            return ((r.stdout or "") + (r.stderr or "")).strip()

        got = run("foo", "a", "b")
        assert "USER a b" in got, f"`loment foo a b` 没跑到用户在 PATH 上放的那个: {got!r}"
        assert "foo" not in got.split("USER")[1][:4], \
            f"用户命令不该收到自己的名字 (`git foo` -> `git-foo`, 不是 `git-foo foo`): {got!r}"
        got = run("nosuchthing")
        assert "OFFICIAL" in got, f"没有对应的用户命令时应当转发给 loment-cli: {got!r}"
        got = run("version")
        assert "FAKE VERSION" in got, f"内置命令被 PATH 上的同名文件顶掉了: {got!r}"


@test
def test_user_command_lookup_in_both_launchers():
    """两个启动器都要有这条查找, 且在**内置判断之后、转发之前**。

    cmd 侧只做静态断言: 它的端到端由 `loment_dist_test` 装完包真跑 `loment.cmd` 覆盖
    （这里没法凭空造一个 `loment-cli.exe` 桩）。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    sh, cmd = loment_dist.LAUNCHER_SH, loment_dist.LAUNCHER_CMD

    assert 'command -v "loment-$ucmd"' in sh, "bash 启动器没有查 PATH 上的 loment-<名>"
    assert sh.index("loment-$ucmd") < sh.index('exec "$cli" "$@"'), \
        "用户命令的查找必须在转发给 loment-cli **之前**"
    assert 'exec "loment-$ucmd" "$@"' in sh, \
        "bash 启动器没把命令名从参数里摘掉（应先 shift 再 exec）"

    assert 'where "loment-%cmd%"' in cmd, "cmd 启动器没有查 PATH 上的 loment-<名>"
    assert cmd.index('where "loment-%cmd%"') < cmd.index(":loment_forward_cli"), \
        "用户命令的查找必须在转发给 loment-cli **之前**"
    # 运行时**不能**被括号块包住: 块里的 %ERRORLEVEL% 在解析期就展开了, 读到的是上一个
    # 值 —— 仓库里那条"工具失败别用 if errorlevel"的注释记的就是同一个坑。
    assert re.search(r'\n"%ucmd%" %uargs%\n', cmd), \
        "cmd 启动器把用户命令的调用写进了括号块/缩进了 —— 退出码会读错"


@test
def test_both_launchers_forward_the_renderer_output_modes():
    """两个启动器都要把 `--short` / `--json` 转交给**渲染器**（`docs/182` §15）。

    它们是渲染器的输出模式，驱动不该看见 —— 与 `--no-color` 同一条路（也都是"位置任意"
    的那个开关集合）。少了这条转发，"给 CI 与编辑器用的那两个模式"在包里只能两步土办法
    拿到：先自己带 `--diag-out` 编一次、再手动起 `lomenterr` —— 而绕开这两步正是它们存在
    的理由。

    bash 侧的**行为**由 `loment_err_test` 的启动器判据端到端验（桩渲染器把自己的 argv
    打出来，所以开关有没有到手直接看得到）。cmd 侧这里只做**静态**断言，与
    `test_user_command_lookup_in_both_launchers` 同一条纪律 —— 这边造不出一个能跑
    `loment-driver.exe` 的桩包（`loment_dist_test` 也只验它存在、不含 wsl，不跑它）。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    sh, cmd = loment_dist.LAUNCHER_SH, loment_dist.LAUNCHER_CMD

    # bash: 两处参数扫描（check/ir 一处、build/run 一处）都要认，且都要转交
    assert sh.count("--short|--json) om=$1; shift ;;") == 2, \
        "bash 启动器不是两处参数扫描都认 --short/--json"
    assert sh.count('"$nc $om"') == 3, "bash 启动器没把输出模式转交给 report_diags"
    assert '"$(tool lomenterr)" $rflags "$dfile"' in sh, \
        "renderer 的开关没有一起(word-split)传给 lomenterr"
    # `--max N` / `--max=N`：两个拼法都要转交，而且**带上它的值**（这个词的两个拼法各一条）
    assert sh.count('--max) om="$om --max ${2:-}"; shift 2 ;;') == 2, \
        "bash: --max <N> 没被认/没带上值"
    assert sh.count('--max=*) om="$om $1"; shift ;;') == 2, "bash: --max=N 那个拼法没被认"

    # cmd: check 走 :scan_arg、build/run 走 :barg_loop —— 两条路都要认，且都要转交
    assert cmd.count('if /I "%~1"=="--short" goto scan_om') == 1, "cmd: check 不认 --short"
    assert cmd.count('if /I "%~1"=="--json" goto scan_om') == 1, "cmd: check 不认 --json"
    assert cmd.count('if /I "%~1"=="--short" goto barg_om') == 1, "cmd: build/run 不认 --short"
    assert cmd.count('if /I "%~1"=="--json" goto barg_om') == 1, "cmd: build/run 不认 --json"
    assert ':barg_om' in cmd and ':scan_om' in cmd, "cmd: 两个分支缺一个落点"
    # 累加式的写法出现在两处：check 那路的 `:scan_om`，与 build/run 那路的 `--max=N` 落点
    assert cmd.count('set "com=%com% %~1"') == 2, "cmd: 没把这个开关累加进 com"
    assert cmd.count('set "com=%~1"') == 1, "cmd: build/run 那路没记下这个开关"
    # 五处：check/ir 一处、build 的驱动与链接器各一处、run 的驱动与链接器各一处。
    # 链接器那两处是 `issue #8` 加上的 —— lomelf 现在也有自己的 `--diag-out`，
    # 写不了产物时它吐一条 E029，由同一个 report_diags 渲染。
    assert cmd.count('"%cnc% %com%"') == 5, \
        "cmd: 每处 report_diags 调用都要带上输出模式 (driver ×2 + lomelf ×2 + check ×1)"
    # `--max` 在 cmd 里是**两**个词，而 check 那路是 `for` 扫全命令行 —— 必须记住
    # "下一个词是它的值"并跳过，否则 `--max 0` 会把 `0` 当成源文件名。
    assert cmd.count('if /I "%~1"=="--max" goto scan_max') == 1, "cmd: check 不认 --max"
    assert ':scan_max' in cmd and 'set "cskip=1"' in cmd, "cmd: 没记住 --max 的值那一个词"
    assert cmd.count('if not defined cskip goto scan_arg_go') == 1, \
        "cmd: 跳过一个词的机制不在（--max 的值会被当成源文件）"
    assert cmd.count('set "com=%com% --max %~2"') == 1, "cmd: build/run 没带上 --max 的值"


@test
def test_both_launchers_reject_unknown_options():
    """未知开关必须**报错并退 2** —— 不能当成源文件，也不能静默忽略。

    这一课仓库已经学过一次：`:barg_loop` 那儿留着注释说，静默丢掉 `--link` 曾让链接器报出
    `undefined label: c_add`，把用户指到错的地方。但那条纪律只落在 **cmd 的 build/run 路**
    上：cmd 的 `:scan_arg`（check/ir 路）与两条扫描都**没有判据钉着**，于是
    `loment check x.lomt --diag-out p` 在 Windows 上被无声吞掉（issue #13 报的就是它；
    bash 侧同一形状会 `unknown option` + 退 2）。四条路这里一起钉。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    sh, cmd = loment_dist.LAUNCHER_SH, loment_dist.LAUNCHER_CMD

    # bash: 两条参数扫描各有一处拒绝 (check/ir 一处、build/run 一处)
    assert sh.count('*) echo "loment: unknown option $1" >&2; exit 2 ;;') == 2,         "bash 启动器不是两条扫描都拒绝未知开关"
    # cmd: build/run 的 :barg_loop 与 check/ir 的 :scan_arg 各有一处
    assert cmd.count('echo loment: unknown option %~1 1>&2') == 2,         "cmd 启动器不是两条扫描都拒绝未知开关"
    assert ':scan_bad' in cmd, "cmd: check/ir 路缺拒绝的落点"
    # 限定"以 - 开头"：check/ir 那路扫的是**整条命令行**，源文件名也在里面，
    # 不限定的话要么漏掉 --diag-out，要么把 x.lomt 当成未知开关。
    assert 'if "%ss:~0,1%"=="-" goto scan_bad' in cmd, "cmd: 拒绝没有限定在 - 开头的词"
    # `for ... do call` 里 `exit /b` 出不了脚本，所以要为它留一个停下的地方
    assert 'set "cbad="' in cmd and "if defined cbad exit /b 2" in cmd,         "cmd: :scan_arg 的拒绝没有从 :compile_only 传出去"


@test
def test_both_launchers_reject_extra_check_files():
    """`check` must not silently compile only the first of multiple input files.

    **静态**那一半 —— bash 侧的**行为**由 `loment_err_test` 的
    `test_launcher_refuses_a_second_input_file` 端到端验（真启动器 + 桩驱动，所以
    "驱动有没有被起"直接看得到）。两边分着写是这仓的老规矩：cmd 一个包都造不出来
    （`loment_dist_test` 只验它存在、不含 wsl、不跑它），所以它只有静态那半。

    拒绝里说的是**用户敲的那个命令名**（`ir` 与 `check` 共用这段扫描）：bash 走
    `$mode`，cmd 走 `cmode`。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    sh, cmd = loment_dist.LAUNCHER_SH, loment_dist.LAUNCHER_CMD

    # Bash parses the file and renderer switches in one pass, so both file positions remain valid.
    assert 'src=' in sh
    assert '$mode accepts exactly one input file' in sh
    assert '*) [ -z "$src" ] || {' in sh
    assert '-*) echo "loment: unknown option $1" >&2; exit 2 ;;' in sh
    assert '[ -n "$src" ] || { usage >&2; exit 2; }' in sh

    # The batch launcher scans the complete command line and must reject a second positional word.
    assert 'if defined csrc goto scan_extra' in cmd
    assert ':scan_extra' in cmd
    assert 'echo loment: ir accepts exactly one input file 1>&2' in cmd
    assert 'echo loment: check accepts exactly one input file 1>&2' in cmd
    assert 'if "%cmode%"=="i"' in cmd, "cmd: 拒绝里没按 cmode 说出用户敲的那个命令名"
    assert 'if defined cbad exit /b 2' in cmd


# ---------------------------------------------------------------- Loment 版（S1 第十二格）

_TW = f"/tmp/loment-cli-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomclicheck.lomt"

#: 摆出来的包布局里那几份可以逐字节复用的东西（两侧各写一份，内容同）。
_VER_TXT = ("Loment 0.1.4 Pre2 (0.1.4-pre2), commit 0123456\nbuild 2026-09-15\n")
_TOUR = "module tour\n\nfn _start() {\n    syscall4(60, 0, 0, 0);\n}\n"
_Y_SRC = "one\nalpha\ntwo\nalpha\nthree\n"
_C_SRC = ("module x\nchoose runtime\n\nstruct S {\n    a: u32,\n}\n\nenum E {\n    A,\n}\n\n"
          "fn one() -> u32 {\n    return 1;\n}\n\n"
          "pub fn two(a: u32) -> u32 {\n    return a;\n}\n")
_T_SRC = ('module x\nchoose runtime\n\n// 注释\nfn f() -> u32 {\n    let s: str = "ab";\n'
          '    return 1;\n}\n')
_HI_SRC = "module hi\n"
_STUBS = ("loment-driver", "loment-lsp", "loment-fmt", "loment-doc",
          "loment-lomelf", "loment-cli", "lomenterr")


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


#: 孪生要跑的那一整批命令 —— **一个脚本一次跑完**, 每条前后打 `@@名字 退出码` 标记。
#:
#: 为什么不一条一条给孪生调: `proc_sh` 每条 `alloc(PROC_WS)`=4 KiB, 而语言的 `alloc` 是
#: **只有 64 KiB、不回收**的 bump 堆 —— 40 来条命令当场把堆顶穿（先读到被踩坏的缓冲、
#: 再 `Illegal instruction`）。夹具与第 13 格的 `cases.txt` 同路数, 断言仍然全在 Loment 侧。
#:
#: `rc=$?` 必须**紧挨着**取（中间夹一句裸 `echo` 会冲成 0）。
_RUN_ALL = """\
C=./bin/loment-cli
r() { nm="$1"; shift; out=$("$@" 2>&1); rc=$?; printf '\\n@@%s %s\\n' "$nm" "$rc"; printf '%s' "$out"; }
r commands $C commands
r help $C --no-color help
r allcmds sh allcmds.sh
r hgrep $C --no-color help grep
r frob $C frobnicate --no-color
r about $C about
r aboutn $C --no-color about
r stat $C --no-color stat fx/y.lomt
r hash $C hash fx/y.lomt
r cat $C --no-color cat fx/y.lomt
r grep $C --no-color grep alpha fx/x.lomt
r grepn $C --no-color grep nope fx/x.lomt
r grepm $C --no-color grep onlypat
r count $C --no-color count fx/c.lomt
r fns $C --no-color fns fx/c.lomt
r tokens $C --no-color tokens fx/t.lomt
r catm $C --no-color cat /definitely/not/here.lomt
r ls $C --no-color ls d
r tree $C --no-color tree d
r ver $C version
r codes $C --no-color codes
r ex4 $C --no-color explain E4
r exe4 $C --no-color explain e4
r ex4b $C --no-color explain 4
r ex99 $C --no-color explain E99
r exno $C --no-color explain
r pgsyntax $C --no-color syntax
r pgbuiltins $C --no-color builtins
r pgtypes $C --no-color types
r pgkeywords $C --no-color keywords
r pgcaps $C --no-color caps
r pgcheat $C --no-color cheat
r pgabout $C --no-color about
r pgenv $C --no-color env
r whered $C where driver
r wheren $C --no-color where nosuchtool
r exam $C --no-color examples
r examt $C example tour
r examn $C --no-color example nope
r docg $C --no-color doctor
( cd newdir && r newh ../bin/loment-cli --no-color new hello )
r lsnew $C --no-color ls newdir
( cd newdir && r newhi ../bin/loment-cli --no-color new hi )
r hashhi $C hash newdir/hi.lomt
cd ../p_red
r docr ./bin/loment-cli --no-color doctor
exit 0
"""


def _cli_pkg(root: Path, exe: Path, name: str, stubs: bool, linux: bool = False) -> Path:
    """一个包布局: `bin/loment-cli`(CLI 靠 argv[0] 自定位) + `share/loment/version`。

    `stubs=False` 的那个（`p_red`）用来验体检**有分辨力** —— 缺组件时它必须红。
    `linux=True` 是给孪生那棵树用的: 拷进去的是 **ELF**, 所以文件名的后缀按**目标平台**
    定（不是按跑判据的这个平台）—— CLI 找 `loment-<名>` 时带不带 `.exe` 由**它自己**
    编成哪个后端决定。
    """
    pkg = root / name
    win = not linux
    dst = pkg / "bin" / ("loment-cli.exe" if win else "loment-cli")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy(exe, dst)
    dst.chmod(0o755)
    _w(pkg / "share" / "loment" / "version", _VER_TXT)
    _w(pkg / "share" / "loment" / "examples" / "tour.lomt", _TOUR)
    if stubs:
        for n in _STUBS:
            p = pkg / "bin" / (n + ".exe" if win else n)
            if not p.exists():
                p.write_bytes(b"stub")
    return pkg


def _cli_fixtures(root: Path) -> None:
    """输入文件与**期望值**, 全写进 CLI 的 cwd 里（命令用的是相对路径）。

    期望值全是拿 **CPython 当神谕**算出来的（hashlib / 自己数行 / 从真源抄错误码表）。
    """
    _w(root / "fx" / "y.lomt", _Y_SRC)
    _w(root / "fx" / "x.lomt", _Y_SRC)
    _w(root / "fx" / "c.lomt", _C_SRC)
    _w(root / "fx" / "t.lomt", _T_SRC)
    _w(root / "d" / "sub" / "deep" / "f.lomt", "module f\n")
    _w(root / "d" / "a.lomt", "module a\n")
    _w(root / "newdir" / "hi.lomt", _HI_SRC)
    # 目录: 每条命令都要能敲得动 —— 用 shell 脚本扫一遍（WSL 里有 sh）
    _w(root / "allcmds.sh",
       "for n in $(./bin/loment-cli commands); do ./bin/loment-cli \"$n\" --no-color 2>&1; "
       "echo \"== $n\"; done\nexit 0\n")
    _w(root / "run_all.sh", _RUN_ALL)
    yraw = (root / "fx" / "y.lomt").read_bytes()
    _w(root / "want_hash.txt", hashlib.sha256(yraw).hexdigest() + "\n")
    _w(root / "want_hi_hash.txt", hashlib.sha256(_HI_SRC.encode()).hexdigest() + "\n")
    lines = yraw.decode().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    _w(root / "want_stat.txt", f"{len(yraw)}\n{len(lines)}\n")
    import loment_diag  # noqa: E402
    # `explain E99` 那条要一个**表外**的码 —— 表长到 99 就该改这里, 所以先钉住
    assert max(loment_diag.ASCII_ONE_LINER) < 99, "错误码表到 99 了, 换一个表外的码"
    _w(root / "codes.txt", "".join(f"E{c} {d}\n"
                                   for c, d in sorted(loment_diag.ASCII_ONE_LINER.items())))


def _cli_py_report(pkg: Path, red: Path) -> str:
    """用 **PE 版 CLI**（Windows 原生）做与孪生**同名的那 23 项**断言, 打同一份报告。

    `pkg` 是组件齐的那一棵（孪生的 cwd），`red` 是缺组件的那一棵（体检那一条要用）。
    **断言全部与平台无关**（退出码 / 相对路径回显 / 行号 / 计数 / 子串）—— 两侧跑的
    是两个后端产物, 绝对路径必然不同。
    """
    out: list[str] = []
    ct = {"p": 0, "f": 0}
    exe_def = pkg / "bin" / ("loment-cli.exe" if IS_WIN else "loment-cli")

    def rep(name: str, ok: bool, why: str) -> None:
        if ok:
            out.append(f"  PASS  {name}")
            ct["p"] += 1
        else:
            out.append(f"  FAIL  {name}: {why}")
            ct["f"] += 1

    def run(args: list[str], cwd: Path | None = None, merge: bool = False,
            exe: Path | None = None) -> tuple[int, str]:
        # `exe` 要跟 `cwd` 同属一棵树: CLI 靠 **argv[0]** 自定位（`doctor` / `where` 就是
        # 按它那个目录找工具的）—— 拿另一棵树的二进制去跑会读到错的组件表。
        r = subprocess.run([str(exe or exe_def), *args], cwd=str(cwd or pkg),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=180)
        return r.returncode, r.stdout + (r.stderr if merge else "")

    def names() -> list[str]:
        return [x for x in run(["commands"])[1].splitlines() if x.strip()]

    # 1
    ns = names()
    must = ("help", "codes", "explain", "syntax", "builtins", "stat", "grep", "hash",
            "ls", "tree", "new", "examples", "doctor", "color")
    rep("test_catalog_has_at_least_thirty_commands",
        len(ns) >= CATALOG_FLOOR and all(m in ns for m in must),
        "命令少于 30 条, 或该有的名字不在目录里")
    # 2
    rc, helptxt = run(["--no-color", "help"])
    miss = [n for n in ns if not re.search(rf"\b{re.escape(n)}\b", helptxt)]
    rep("test_help_mentions_every_command", rc == 0 and not miss,
        "help 里没提到目录里的某些名字")
    # 3 —— 与孪生同一句断言（原先那条断言的是**中文** `未知命令`, 而 CLI 早就改成纯 ASCII 了,
    #      于是它**永远为真**、是一条死判据; 2026-09-19 写本格时发现, 连同孪生一起收紧）
    blob = ""
    for n in ns:
        _rc, t = run([n, "--no-color"], merge=True)
        blob += t + f"== {n}\n"
    rep("test_every_catalog_command_is_actually_dispatchable",
        "unknown command" not in blob and "== " in blob,
        "目录里列了但敲不了 (敲出 unknown command)")
    # 4
    rc4, o4 = run(["--no-color", "help", "grep"])
    rep("test_help_page_for_one_command", rc4 == 0 and "PAT" in o4 and "FILE" in o4,
        "help grep 没给出 PAT/FILE 的形状")
    # 5
    rc5, o5 = run(["frobnicate", "--no-color"], merge=True)
    rep("test_unknown_command_is_an_error",
        rc5 == 2 and "unknown command" in o5 and "frobnicate" in o5,
        "未知命令没退 2, 或没说出命令名")
    # 6
    _, on = run(["about"])
    _, off = run(["--no-color", "about"])
    rep("test_color_on_by_default_and_off_with_flag",
        "\x1b" in on and "\x1b" not in off, "默认没上色, 或 --no-color 仍留转义字节")
    # 7
    rc7, o7 = run(["--no-color", "stat", "fx/y.lomt"])
    rep("test_color_flag_anywhere_in_argv_does_not_eat_the_command",
        rc7 == 0 and "Source statistics" in o7 and "fx/y.lomt" in o7,
        "`--no-color stat F` 里的 stat 没被当成命令")
    # 8
    want_hash = (pkg / "want_hash.txt").read_text(encoding="utf-8").strip()
    _rc, oh = run(["hash", "fx/y.lomt"])
    rep("test_hash_matches_hashlib", oh.strip() == want_hash,
        "sha256 与 hashlib 算出来的不一样")
    # 9
    want_stat = (pkg / "want_stat.txt").read_text(encoding="utf-8").split()
    _rc, o9 = run(["--no-color", "stat", "fx/y.lomt"])
    rep("test_stat_counts_match_what_python_counts",
        want_stat[0] in o9 and want_stat[1] in o9, "字节数或行数与 Python 数的不一致")
    # 10
    rc10, o10 = run(["--no-color", "cat", "fx/y.lomt"])
    rep("test_cat_prints_numbered_lines",
        rc10 == 0 and len(o10.splitlines()) == 5 and o10.splitlines()[0].strip().startswith("1"),
        "cat 没带行号, 或行数不是 5")
    # 11
    rc11, o11 = run(["--no-color", "grep", "alpha", "fx/x.lomt"])
    g1 = rc11 == 0 and [int(x.split(":")[0]) for x in o11.splitlines()] == [2, 4]
    g2 = run(["--no-color", "grep", "nope", "fx/x.lomt"])[0] == 1
    g3 = run(["--no-color", "grep", "onlypat"])[0] == 2
    rep("test_grep_line_numbers_and_exit_codes", g1 and g2 and g3,
        "grep 的行号 / 三种退出码 (0/1/2) 不对")
    # 12
    rc12, o12 = run(["--no-color", "count", "fx/c.lomt"])
    got12 = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", o12, re.M))
    c_ok = rc12 == 0 and got12.get("fn") == "2" and got12.get("struct") == "1" \
        and got12.get("enum") == "1"
    rc13, o13 = run(["--no-color", "fns", "fx/c.lomt"])
    f_ok = rc13 == 0 and "fn one() -> u32" in o13 and "fn two(a: u32) -> u32" in o13
    rep("test_count_and_fns_on_a_known_file", c_ok and f_ok,
        "count 的 fn/struct/enum 计数, 或 fns 的签名不对")
    # 13
    rc14, o14 = run(["--no-color", "tokens", "fx/t.lomt"])
    got14 = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", o14, re.M))
    rep("test_tokens_picks_up_strings_and_comments",
        rc14 == 0 and got14.get("strings") == "1" and got14.get("comments") == "1"
        and int(got14.get("keywords", 0)) >= 4,
        "tokens 的 strings/comments/keywords 不对")
    # 14
    rc15, o15 = run(["--no-color", "cat", "/definitely/not/here.lomt"], merge=True)
    rep("test_missing_file_is_a_clean_error_not_a_crash",
        rc15 == 1 and "cannot open" in o15, "缺文件没退 1, 或没说 cannot open")
    # 15
    rc16, o16 = run(["--no-color", "ls", "d"])
    l_ok = rc16 == 0 and "sub/" in o16 and "a.lomt" in o16 and "sub\n" not in o16
    rc17, o17 = run(["--no-color", "tree", "d"])
    t_ok = rc17 == 0 and "deep/" in o17 and "f.lomt" in o17 and "\n      f.lomt" in o17
    rep("test_ls_and_tree_mark_directories", l_ok and t_ok,
        "ls 没给目录加 / , 或 tree 没体现层级")
    # 16
    nw = pkg
    rc18, o18 = run(["--no-color", "new", "hello"], cwd=nw / "newdir")
    nw1 = rc18 == 0 and "wrote hello.lomt" in o18
    nw2 = "hello.lomt" in run(["--no-color", "ls", "newdir"])[1]
    rc19, _o19 = run(["--no-color", "new", "hi"], cwd=nw / "newdir", merge=True)
    nw3 = rc19 == 1
    want_hi = (nw / "want_hi_hash.txt").read_text(encoding="utf-8").strip()
    nw4 = run(["hash", "newdir/hi.lomt"])[1].strip() == want_hi
    rep("test_new_writes_a_skeleton_and_refuses_to_overwrite", nw1 and nw2 and nw3 and nw4,
        "new 没写出骨架, 或已存在时被覆盖/没退 1")
    # 17
    rc20, o20 = run(["version"])
    rep("test_version_reads_share_version",
        rc20 == 0 and o20.startswith("Loment 0.1.4 Pre2") and "commit 0123456" in o20,
        "version 没读到包里的那一份")
    # 18
    rc21, o21 = run(["--no-color", "codes"])
    rows = [x for x in (nw / "codes.txt").read_text(encoding="utf-8").splitlines() if x]
    bad = [r for r in rows if r.split(" ", 1)[0] not in o21 or r.split(" ", 1)[1] not in o21]
    rep("test_codes_lists_every_code_in_the_table", rc21 == 0 and not bad,
        "错误码表里有码没被 codes 印出来, 或说明不是真源那一份")
    # 19
    e_ok = all(run(["--no-color", "explain", s])[1].find("Capability domain") >= 0
               and run(["--no-color", "explain", s])[0] == 0
               for s in ("E4", "e4", "4"))
    e_hi = run(["--no-color", "explain", "E99"])[0] == 2
    e_no = run(["--no-color", "explain"])[0] == 2
    rep("test_explain_accepts_three_spellings_and_rejects_junk", e_ok and e_hi and e_no,
        "explain 的三种拼法 / 越界码 / 缺参 有一处不对")
    # 20
    pg = all(run(["--no-color", c])[0] == 0 and len(run(["--no-color", c])[1].strip()) > 60
             for c in ("syntax", "builtins", "types", "keywords", "caps", "cheat",
                       "about", "env"))
    rep("test_reference_pages_are_nonempty", pg, "有参考页空着或退码不为 0")
    # 21
    rc22, o22 = run(["where", "driver"])
    w_ok = rc22 == 0 and "loment-driver" in o22
    rc23, o23 = run(["--no-color", "where", "nosuchtool"], merge=True)
    w_no = rc23 == 2 and "no such tool" in o23
    rep("test_where_resolves_and_reports_the_expected_path", w_ok and w_no,
        "where 没解出 loment-driver, 或不存在时没退 2")
    # 22
    rc24, o24 = run(["--no-color", "examples"])
    ex1 = rc24 == 0 and "tour.lomt" in o24
    rc25, o25 = run(["example", "tour"])
    ex2 = rc25 == 0 and "module tour" in o25
    rc26, o26 = run(["--no-color", "example", "nope"], merge=True)
    ex3 = rc26 == 1 and "no such example" in o26
    rep("test_examples_and_example_read_the_package", ex1 and ex2 and ex3,
        "examples/example 没读到包里那一份, 或不存在时没退 1")
    # 23
    red_exe = red / "bin" / ("loment-cli.exe" if IS_WIN else "loment-cli")
    rc27, o27 = run(["--no-color", "doctor"], cwd=red, exe=red_exe)
    d1 = rc27 == 1 and "MISSING" in o27
    rc28, o28 = run(["--no-color", "doctor"])
    d2 = rc28 == 0 and "all green" in o28
    rep("test_doctor_reports_missing_tools_then_green_when_present", d1 and d2,
        "缺组件时没红/组件齐了没绿")

    out.append(f"lomclicheck: {ct['p']}/{ct['p'] + ct['f']} 通过")
    return "\n".join(out) + "\n"


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
    s = str(Path(p).resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build_elf_for(src: Path, dst: Path) -> Path:
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = dst.with_suffix(".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(dst), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return dst


@test
def test_cli_check_matches_loment_twin():
    """**Loment 版**（`lomclicheck.lomt` 驱 ELF 版 CLI）与 Python 版**同名断言的报告逐字节相同**。

    `docs/189` §3 的 S1 第十二格（丁类）。被测的是**另一件 Loment 程序**（`lomcli.lomt`）——
    Python 那侧跑它的 **PE**（Windows 原生）, 孪生那侧跑 **ELF**（WSL）。

    两侧断言的全是**与平台无关**的东西（退出码 / 相对路径回显 / 行号 / 计数 / 子串）——
    `example nope` 的报错里带着绝对路径、`examples`/`env` 打的是从 `argv[0]` 推出来的目录,
    两平台必然不同, 所以一处都不比。期望值（sha256 / 字节数 / 行数 / 错误码表）由判据拿
    **CPython 当神谕**算出来写成夹具。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # **两侧各一棵树**: `new hello` 会往 `newdir/` 里落一个文件, 共用一棵的话先跑的那侧
        # 就把夹具改掉了, 后跑的那侧 `new` 变成"已存在"（2026-09-19 实测踩到）。
        root_py = td / "py"
        root_py.mkdir()
        exe = _build()
        green_py = _cli_pkg(root_py, exe, "p_green", stubs=True)
        red_py = _cli_pkg(root_py, exe, "p_red", stubs=False)
        _cli_fixtures(green_py)
        want = _cli_py_report(green_py, red_py)
        # ELF 版: 同一个源, 另一个后端
        root_w = td / "wsl"
        root_w.mkdir()
        _build_elf_for(SRC, td / "loment-cli.elf")
        green = _cli_pkg(root_w, td / "loment-cli.elf", "p_green", stubs=True, linux=True)
        red = _cli_pkg(root_w, td / "loment-cli.elf", "p_red", stubs=False, linux=True)
        _cli_fixtures(green)
        # 交错的那一步: 孪生自己也要能编 + 跑
        chk = _build_elf_for(TWIN, td / "lomclicheck.elf")
        outp = td / "check.out"
        binn = f"{_TW}chk.bin"
        script = (f"cp {_wsl_path(chk)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(green)} && {binn} > {_wsl_path(outp)} 2>&1; echo -n $?")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, text=True, timeout=600, shell=False)
        got = outp.read_bytes().decode("utf-8") if outp.exists() else ""
    assert rr.stdout.strip() == "0", f"孪生该退 0: rc={rr.stdout!r}\n{got[:400]}"
    assert got == want, f"报告与 Python 版不同:\n  py     {want!r}\n  loment {got!r}"
    n = len(got.strip().splitlines()) - 1
    assert got.count("  PASS  ") == n == 23, (n, got[-200:])
    print(f"      {n} 项断言: PE 版与 ELF 版 CLI 同一份报告（与 Python 版逐字节相同）")


@test
def test_cli_twin_selfhost_compiles():
    """`lomclicheck.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
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
        binn = f"{_TW}s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomclicheck.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomclicheck.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomclicheck.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_cli_test")


if __name__ == "__main__":
    sys.exit(main())
