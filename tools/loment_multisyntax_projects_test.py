#!/usr/bin/env python3
"""loment_multisyntax_projects_test.py — **六门表层语法各一个大型项目，跑出来的数相等**。

用户 2026-09-22 的题：

> 用 Loment 的 Java / Python / Rust / C / C++ / Go 的语法形式写 Loment，
> **各一个大型项目**，目标验证 Loment 多语法系统，出现问题提交 issues

## 与 `loment_multisyntax_test` 的分工（两件事，别混）

| | `loment_multisyntax_test`（`docs/179` §8） | **这一条** |
|---|---|---|
| 语料 | `examples/multisyntax/`（**抽接口**，无正文） | `examples/multisyntax-projects/`（**带正文，真编真跑**） |
| 问什么 | 认对 / 合法 / 非空 / 过 `check` | **跑出来的数 == 对照组 == 独立期望值** |
| 走哪条路 | `potato_from.transcribe` | 前门 `choose write grammar`（`docs/188`） |

## 判据是**三方**，不是两方 —— 缺哪一方都有一种错穿不过去

```
   那份源 ──真实工具链（clang / g++ / javac / go / CPython / rustc）──> rc_control ─┐
                                                                                    ├─ 三者相等
   那份源 ──前门翻成 Loment──> lomelf ──> ELF ──WSL 跑──> rc_loment ───────────────┤
                                                                                    │
   项目 README 里**独立推出来的**期望值（`EXPECTED: <数>`）────────────────────── expected ─┘
```

* 少了**对照组**：翻译器把一个数算错、而那个数谁也没验过，看不出来；
* 少了**独立期望值**：翻译器与对照组**一起**错成同一副样子会被当成通过（`docs/186` §1）。

**实测过一次三方不一致**（`c/01-algo` 的 `isqrt`）：语料里有符号溢出（C 里是 UB），
`clang -O1` 给 1000、本语言给 458753（回绕）、独立推的又是 1000 —— **不可比**。
三方里有两方相等**不能**说明什么，这就是为什么要三方。详见
`loment/examples/multisyntax-projects/README.md`。

## 对照组各自的入口（**与各门既有判据同一套夹具形状**）

每一门都要有办法把"那个数"拿出来。这里**不另发明**：`entry()` 这个名字、C/C++ 的
`_start`、Java 的 `Harness`、Go 的 `harness.go`，都与 `loment_{c,cpp,java,go,py}trans_test`
里已有的那套一致 —— 各门的工具链路径也**从那些模块里取**（`_clang` / `_have_gpp` /
`_javac` / `_go` / `_python` / `_wsl`），一处真源。

**Rust 那一门不同类**：它是**原生读法**（用户 2026-09-22：「rust 语法是 Loment 基础语法，
不需要翻译」），前门只把声明那一行抹成空白。所以它的对照侧要**多抹一行**：`module <名>`
—— 原生读法要求源里就写着它，而它在真 Rust 里是语法错。

用法: python tools/loment_multisyntax_projects_test.py
无某个工具链时那一门 SKIP（**可见地**跳过，不是静默绿）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf       # noqa: E402
import lomentc      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


EX = ROOT / "loment" / "examples" / "multisyntax-projects"

#: 每一门至少要有一个项目。**规模本身是一条判据**（`docs/179` §5 那条纪律：
#: 有人明确要过的规模，就该有东西看着它）—— 删掉一门会让这一条红。
GRAMMARS = ("c", "cpp", "java", "python", "go", "rust")

#: 模块最少要有多少个函数。**"大型项目"是个可数的要求**，不是形容词：
#: 少了它就退化成夹具，而夹具撞不出整项目才撞得到的东西（`docs/179` §8 那条教训）。
MIN_FUNCS = 10

#: **一门一张数函数的正则**（"这个项目的函数够不够多"要数得准）。
#: 一门一条而不是一条通用 —— 六门的声明形状本来就不同，通用那条正则必然是猜的。
FUNC_RE = {
    # `unsigned int f(` 也要数得到 —— C++ 那一门的语料里 `unsigned` 是常态，
    # 数不到它就会把一个"看着函数很多"的项目误判成夹具（C++ 那门自己报回来的）。
    "c": re.compile(r"(?m)^\s*(?:static\s+)?(?:unsigned\s+|signed\s+)?"
                    r"(?:int|void|bool|char|long|short)\s+\w+\s*\("),
    "cpp": re.compile(r"(?m)^\s*(?:static\s+)?(?:unsigned\s+|signed\s+)?"
                      r"(?:int|void|bool|char|long|short)\s+\w+\s*\("),
    "java": re.compile(r"(?m)^\s*(?:public|private|protected)?\s*static\s+"
                       r"(?:int|void|bool|long)\s+\w+\s*\("),
    "go": re.compile(r"(?m)^func\s+\w+\s*\("),
    "python": re.compile(r"(?m)^def\s+\w+\s*\("),
    "rust": re.compile(r"(?m)^pub\s+fn\s+\w+"),
}

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ 通用

def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _run_elf(exe: Path) -> int:
    """在 WSL 里跑那个自举出来的 ELF，读退出码。"""
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc", f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=600, shell=False)
    return int((r.stdout or "").strip() or -1)


def _projects() -> list[tuple[str, Path]]:
    """`[(语法, 项目目录)]`，按语法与目录名排序 —— 顺序稳定，报告才好比。"""
    out: list[tuple[str, Path]] = []
    for g in GRAMMARS:
        d = EX / g
        if not d.is_dir():
            continue
        for p in sorted(x for x in d.iterdir() if x.is_dir()):
            out.append((g, p))
    return out


def _module_of(proj: Path) -> Path:
    """项目里的**表层语法模块** = 除 `main.lomt` 之外的那份 `.lomt`。

    契约是**恰好一份**：多份会让"对照组读哪一份"变得要猜，而猜出来的判据是假的。
    """
    mods = sorted(p for p in proj.glob("*.lomt") if p.name != "main.lomt")
    assert len(mods) == 1, f"{proj}: 表层语法模块该恰好一份，得到 {[m.name for m in mods]}"
    return mods[0]


def _blank_line(line: str) -> str:
    """抹成**等长空白**（保留换行）—— 与 `potato_from.strip_grammar_decl` 同一手法，
    于是行号不漂、报错仍指得到那一行。"""
    return " " * len(line)


def _control_source(proj: Path, grammar: str) -> str:
    """交给**真实工具链**的那份源：把"前端指令"那些行抹掉。

    抹两样：
      * `choose write grammar <语法>` —— 它是**读法**，不是那份源的一部分（`docs/188` §1）；
      * （只有 `rust` 那一门）`module <名>` —— 原生读法要求源里写着它，
        而真 Rust 里它是语法错。**这一条不对称是实测出来的**：
            $ python tools/lomentc.py --check x.lomt   # 只有声明、没有 module
            [ERR] 3:1: 期望 module（文件必须以 module 开头），得到 'pub'
    """
    text = _module_of(proj).read_text(encoding="utf-8")
    lines = text.split("\n")
    lines[0] = _blank_line(lines[0])            # `choose write grammar <语法>`
    # **核心 `choose` 行也要抹掉**（`docs/227`）：与 `choose write grammar` 同一条理由 ——
    # 它是 **Loment 层**的声明（`docs/182` §1.3 的"核心语法硬写法"），不是那一门语法的一部分。
    # 抹的时机与前端**同一处**（`potato_from._take_core_choose`）：不抹的话这一行会被当成
    # 那门语言的语法喂给真编译器 —— 实测 clang 报 `unknown type name 'choose'`。
    _CORE = ("std", "no_std", "gc_manual", "gc_auto", "gc_auto_alpha", "runtime", "no_runtime")
    for _k, _ln in enumerate(lines):
        _head = _ln.strip().rstrip(";").split()
        if len(_head) == 2 and _head[0] == "choose" and _head[1] in _CORE:
            lines[_k] = _blank_line(_ln)
    if grammar == "rust":
        # 声明之后**第一处非空行**应当是 `module <名>` —— 原生读法要求源里写着它
        # （另五门的模块名是翻译器从文件名造的，所以那五份里**不该**有这一行）
        i = next((k for k, ln in enumerate(lines[1:], start=1) if ln.strip()), None)
        assert i is not None, f"{proj}: `grammar rust` 的模块里找不到 `module <名>`"
        assert lines[i].lstrip().startswith("module "), (
            f"{proj}: `grammar rust` 的模块第二处非空行该是 `module <名>`，"
            f"得到 {lines[i]!r}")
        lines[i] = _blank_line(lines[i])        # 它在真 Rust 里是语法错
    return "\n".join(lines)


def _expected(proj: Path) -> int:
    """从项目的 README 里读**作者独立推出来的**那个数（`EXPECTED: <n>`）。

    **为什么要从 README 读、而不是在判据里另写一份**：那个数必须由**写语料的人**
    按算法独立推一遍 —— 判据里再抄一份只会把同一个错误抄两遍。README 里还要求把
    推导过程写出来（不是只写结果），所以它**可复核**。
    """
    txt = (proj / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\s*EXPECTED:\s*(\d+)\s*$", txt)
    assert m, (f"{proj}/README.md 里没有 `EXPECTED: <数>` 那一行 —— 期望值要**写下来**，"
               f"而且要是**独立推出来的**（见本文件头那段三方判据）")
    return int(m.group(1))


def _loment_rc(main: Path) -> int:
    """Loment 侧：前门（`lomentc.load` 走它）-> IR -> lomelf 汇编 -> WSL 跑。

    **这一路真的过了前门**：`lomentc.load` 里的预扫与装载都调 `potato_from.front_door`
    （`docs/188` §7.2 那条"入口不止一处"）。所以这条判据同时验了
    "声明驱动读法"这件事本身，不只是翻译器算得对不对。
    """
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{main}: 检查不过: {errs[:3]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _info = lomelf.compile_ll(ir, [])
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "side.elf"
        exe.write_bytes(blob)
        return _run_elf(exe)


# ------------------------------------------------------------------ 各门对照组

def _control_c(proj: Path, td: Path) -> int:
    import loment_ctrans_test as T  # noqa: PLC0415
    if not T._clang() or not T._wsl():
        raise _Skip("无 clang 或 WSL")
    src = _control_source(proj, "c") + "\nint main() { return entry(); }\n" + T._C_ENTRY
    f = td / "side.c"
    f.write_text(src, encoding="utf-8", newline="\n")
    exe = td / "side.bin"
    r = subprocess.run([T._clang(), "--target=x86_64-unknown-linux-gnu", "-O1",
                        "-ffreestanding", "-nostdlib", "-fno-stack-protector",
                        "-o", str(exe), str(f)], capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"C 编译失败: {r.stderr[-400:]}"
    return T._run(exe)


def _control_cpp(proj: Path, td: Path) -> int:
    import loment_cpptrans_test as T  # noqa: PLC0415
    if not T._have_gpp():
        raise _Skip("WSL 里没有 g++")
    src = _control_source(proj, "cpp") + T._CPP_ENTRY
    f = td / "side.cpp"
    f.write_text(src, encoding="utf-8", newline="\n")
    exe = td / "side.bin"
    cmd = ("g++ " + " ".join(T._FLAGS) + f" -o {_wsl_path(exe)} {_wsl_path(f)}")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", cmd],
                       capture_output=True, text=True, timeout=900, shell=False)
    assert r.returncode == 0, f"C++ 编译失败: {(r.stdout + r.stderr)[-400:]}"
    return T._run(exe)


def _control_java(proj: Path, td: Path) -> int:
    import loment_jtrans_test as T  # noqa: PLC0415
    javac, java = T._javac(), T._java()
    if not javac or not java:
        raise _Skip("无 javac / java")
    src = _control_source(proj, "java")
    m = re.search(r"(?m)^\s*(?:public\s+)?(?:final\s+)?class\s+(\w+)", src)
    assert m, f"{proj}: 找不到 `class <名>` —— Java 那一门要一个类外壳"
    cls = m.group(1)
    (td / f"{cls}.java").write_text(src, encoding="utf-8", newline="\n")
    (td / "Harness.java").write_text(
        "\npublic class Harness {\n    public static void main(String[] a) {\n"
        f"        System.exit({cls}.entry() % 256);\n"
        "    }\n}\n", encoding="utf-8", newline="\n")
    r = subprocess.run([javac, "-d", str(td), str(td / f"{cls}.java"),
                        str(td / "Harness.java")],
                       capture_output=True, text=True, shell=False, timeout=600)
    assert r.returncode == 0, f"javac 失败: {r.stderr[-400:]}"
    r = subprocess.run([java, "-cp", str(td), "Harness"],
                       capture_output=True, text=True, shell=False, timeout=600)
    return r.returncode


def _control_go(proj: Path, td: Path) -> int:
    import loment_gotrans_test as T  # noqa: PLC0415
    go = T._go()
    if not go:
        raise _Skip("无 go")
    (td / "Sample.go").write_text(_control_source(proj, "go"),
                                  encoding="utf-8", newline="\n")
    (td / "harness.go").write_text(
        'package main\n\nimport "os"\n\nfunc main() {\n\tos.Exit(int(entry() % 256))\n}\n',
        encoding="utf-8", newline="\n")
    exe = td / "side.exe"
    r = subprocess.run([go, "build", "-o", str(exe), "Sample.go", "harness.go"],
                       cwd=str(td), capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, f"go build 失败: {(r.stdout + r.stderr)[-400:]}"
    r = subprocess.run([str(exe)], cwd=str(td), capture_output=True, text=True, timeout=300)
    return r.returncode


def _control_python(proj: Path, td: Path) -> int:
    import loment_pytrans_test as T  # noqa: PLC0415
    py = T._python()
    if not py:
        raise _Skip("无 python")
    f = td / "side.py"
    f.write_text(_control_source(proj, "python"), encoding="utf-8", newline="\n")
    r = subprocess.run([py, "-c",
                        "import runpy,sys;m=runpy.run_path(sys.argv[1]);print(m['entry']())",
                        str(f)], capture_output=True, text=True, timeout=300, shell=False)
    assert r.returncode == 0, f"CPython 跑失败: {r.stderr[-400:]}"
    return int(r.stdout.strip()) & 255


def _control_rust(proj: Path, td: Path) -> int:
    rustc = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / "rustc.exe")
    if not Path(rustc).exists() and not shutil.which("rustc"):
        raise _Skip("无 rustc")
    src = _control_source(proj, "rust") + \
        "\nfn main() { std::process::exit(entry() % 256); }\n"
    f = td / "side.rs"
    f.write_text(src, encoding="utf-8", newline="\n")
    exe = td / "side.exe"
    r = subprocess.run([rustc, "-O", "--edition", "2021", "-o", str(exe), str(f)],
                       capture_output=True, text=True, timeout=900, shell=False)
    assert r.returncode == 0, f"rustc 失败: {(r.stdout + r.stderr)[-400:]}"
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, shell=False)
    return r.returncode


class _Skip(Exception):
    """工具链不在 -> **可见地跳过**（不是静默绿）。"""


_CONTROL = {"c": _control_c, "cpp": _control_cpp, "java": _control_java,
            "go": _control_go, "python": _control_python, "rust": _control_rust}


# ------------------------------------------------------------------ 判据

@test
def test_every_grammar_has_a_project_and_they_are_big_enough():
    """**六门各至少一个项目，而且"大型"是可数的** —— 不是形容词。

    规模本身就该有东西看着它（`docs/179` §5 同一条纪律：有人明确要过的规模，
    就该有判据钉住）。少了它就退化成夹具，而夹具**撞不出**整项目才撞得到的东西
    —— 那正是 `docs/179` §8 换语料的原因。

    同时钉住**契约的形状**（三条，`loment/examples/multisyntax-projects/README.md`）：
    每份模块的**首行是 `choose write grammar <目录名>`**、暴露 `entry()`、宿主里有 `_start`。
    形状不对时下面那条三方判据**会给出假绿或假红**，所以在这里先说清。
    """
    projs = _projects()
    seen: dict[str, int] = {}
    for g, proj in projs:
        mod = _module_of(proj)
        text = mod.read_text(encoding="utf-8")
        first = text.split("\n", 1)[0].strip()
        assert first == f"choose write grammar {g}", (
            f"{mod}: 首行该是 `choose write grammar {g}`（目录名就是它声称的语法），得到 {first!r}")
        assert re.search(r"\bentry\s*\(", text), f"{mod}: 没有 `entry`"
        main = proj / "main.lomt"
        assert main.is_file(), f"{proj}: 缺 `main.lomt`（宿主）"
        assert "_start" in main.read_text(encoding="utf-8"), f"{main}: 宿主里没有 `_start`"
        n = len(FUNC_RE[g].findall(text))
        assert n >= MIN_FUNCS, (
            f"{mod}: 只数出 {n} 个函数（要求 ≥ {MIN_FUNCS}）—— 这一门要的是**大型项目**，"
            f"不是夹具；数法见本条的 docstring")
        seen[g] = seen.get(g, 0) + 1
    missing = [g for g in GRAMMARS if not seen.get(g)]
    assert not missing, f"这几门没有项目: {missing}（六门 = {list(GRAMMARS)}）"
    print(f"      {len(projs)} 个项目，六门齐（{seen}），每份模块 ≥ {MIN_FUNCS} 个函数")


@test
def test_every_project_runs_the_same_as_its_control():
    """**三方相等** —— 这条判据的正题。逐项目各跑一次。

    每一门都自己选"怎么把那个数拿出来"（C 的 `main`、Java 的 `Harness`、Go 的
    `harness.go`…），但**两边比的是同一个数**：`entry()`。工具链缺了就**可见地跳过**
    那一门（打印 SKIP），不让它变成静默绿。
    """
    projs = _projects()
    assert projs, f"{EX} 下一个项目都没有"
    lines: list[str] = []
    skipped: list[str] = []
    for g, proj in projs:
        want = _expected(proj)
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            try:
                ctl = _CONTROL[g](proj, td)
            except _Skip as e:
                skipped.append(f"{g}/{proj.name}: {e}")
                continue
        got = _loment_rc(proj / "main.lomt")
        assert ctl == want, (
            f"{g}/{proj.name}: **对照组与 README 里独立推的期望值不一致** —— "
            f"{ctl} != {want}。先查语料自己（整数溢出那类 UB 是最常见的，"
            f"`loment/examples/multisyntax-projects/README.md` 记过一次），再查期望值。")
        assert got == ctl, (
            f"{g}/{proj.name}: **翻译出来的 Loment 与对照组结果不同** —— {got} != {ctl}。"
            f"（期望值 {want} 两边都过了，所以问题在翻译/编译那一段。）")
        lines.append(f"{g}/{proj.name} -> {got}")
    for ln in lines:
        print(f"      {ln}")
    assert len(lines) + len(skipped) == len(projs)
    if skipped:
        print(f"      SKIP: {skipped}")
    assert len(lines) >= 1, "一个项目都没真跑起来 —— 全 SKIP 不算通过"


@test
def test_the_grammar_decl_is_what_selects_the_reading():
    """**同一份内容，声明不同 -> 读法不同** —— 这是多语法系统的那句话本身。

    判据不能只测"声明写对了能跑"：一个**总是按 C 读**的实现也能让那种判据全绿。
    所以这里拿**同一份文本**、只改声明，要看到**三种不同的结局**：

      * 声明 `c`       -> 收下并翻出一份 Loment（`translated=True`，产物里真有那个函数）；
      * 声明 `natural` -> **响亮地拒**（"这份源读不通"，走 `front_errors` 那一档）；
      * 声明 `rust`    -> **原生读法**（`translated=False` —— 用户 2026-09-22 的裁定：
        rust 语法是 Loment 基础语法，不需要翻译）。

    **为什么拿 `natural` 当"拒"的样本，而不是 `python`** —— `python` 今天也拒，但
    **拒得不干净**：`ast.parse` 抛的 `SyntaxError` 从 `front_door` 一路冒到命令行上，
    成了 `SyntaxError: invalid syntax (<unknown>, line 3)`，而不是
    `[ERR] … 这份源读不通`（根因：`potato_from.front_errors("python")` 返回 `()`，
    那条 `try/except` 一处都没接住；2026-09-22 实测，已上报）。
    **钉一条正在烂的行为等于给缺陷发许可证** —— 所以这里挑 `natural`：它走的正是那条
    **本该对每一门都成立**的拒收路。

    比的是 `front_door` 的**产物**，不是"跑出同一个数" —— 比数的判据在这个问题上
    **分辨不出**"读法被忽略了"（两边都按 C 读时，数还是对的）。
    """
    import potato_from  # noqa: PLC0415
    body = "int f(int a) {\n    if (a) {\n        return 1;\n    }\n    return 0;\n}\n"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        a = td / "a.lomt"
        a.write_text("choose write grammar c\n\n" + body, encoding="utf-8", newline="\n")
        fu = potato_from.front_door(a)
        assert (fu.grammar, fu.translated) == ("c", True), (fu.grammar, fu.translated)
        assert "pub fn f(" in fu.source, "C 那一支没翻出函数来"

        b = td / "b.lomt"
        b.write_text("choose write grammar natural\n\n" + body, encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(b)
        except Exception as e:                                     # noqa: BLE001
            assert "读不通" in str(e), f"报的话该说清是「读不通」这一档: {e}"
        else:
            raise AssertionError("同一段 C 声明成 natural 竟然也读得过去 —— 声明没起作用")

        # **原生拼法**：声明成 `rust` 走的是"抹掉声明那一行"那条路，产物是**生成物以外**
        # 的东西（包头 `// 由 tools/lomt_from.py …` 是翻译那一支才有的）
        c = td / "c.lomt"
        c.write_text("choose write grammar rust\nmodule m\n\npub fn g() -> i32 {\n"
                     "    return 2;\n}\n", encoding="utf-8", newline="\n")
        fu_c = potato_from.front_door(c)
        assert (fu_c.grammar, fu_c.translated) == ("loment", False), (fu_c.grammar, fu_c.translated)
        assert "pub fn g() -> i32" in fu_c.source and "从 Potato" not in fu_c.source, (
            "`grammar rust` 该走**原生读法**（用户 2026-09-22：rust 语法是 Loment 基础语法），"
            "而不是被当外国模块翻一遍")
    print("      同一段内容：声明 c 收下并翻译、声明 natural 响亮地拒、声明 rust 走原生读法")


def main() -> int:
    return loment_suite.run(TESTS, "loment_multisyntax_projects_test")


if __name__ == "__main__":
    sys.exit(main())
