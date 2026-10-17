#!/usr/bin/env python3
"""loment_gotrans_test.py — **Go 写法 -> Loment，跑出来的数一样**（`docs/188` §7.1）。

六门里的第六门，也是最后一门。判据与前几门**同一条**：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接跑那份源码的结果」

这一门把"直接跑那份源码"换成 `go build` + 跑那个 exe：

    Go  ──go build──> exe ──┐
                             ├─> 退出码比一比
    Go  ──potato_from──> Potato ──lomt_from --impl──> Loment ──> 退出码 ──┘

**比的是数，不是文本。**

`go run` **不能用**：它把子进程的退出码吞掉、自己报 1（实测 `exit status 30` /
`rc=1`）。所以先 `build` 出 exe 再跑 —— 与 C# 那一门同一个手法，理由也一样：
"编译失败"与"程序自己 return 1"必须是两个分得开的信号。

## 这一门**不共用** `trans_core` —— 三条形状都不一样

`docs/188` §7.1 的架构是"一份解析器 + 方言表"，而那一份是给"`<类型> <名>(…)`、
条件带括号、`;` 结句"那一族的。Go 三条都不同：**类型写在名字后面**、条件**不带括号**、
**没有 `while`**（一个 `for` 管三种形状，含 `for { }` 那种死循环）。
硬塞进方言表就得给解析器加三个"哪一门"的开关 —— 那就不是方言表了。所以自足一份。

## 它**收**两样别人拒的东西，而理由在源语言那边

`i++` / `i--` / `x += e`：C / Java / C# 里它们是**表达式**（有值），`y = i++` 映射不过去，
那几门一律拒；**Go 里它们是语句、没有值**，所以 `i++` 就是 `i = i + 1`，一字不差。
⇒ 同一处按"能表达的就转"给出**相反**的结论。

## 与 Loment 少见地对上的一格：**两边都要求显式转换**

Go **没有隐式数值转换**，所以 `int(b)` 这种转换在 Go 里到处都是 —— 而它正好对上
Loment 的 `as`，**源码里已经写好了**，翻译器不用猜。这在六门里是独一份：
C / C++ 要靠翻译器补转换，Java / C# 一个都不补，而 Go 是**源码写好了直接搬**。

## 写这一门时撞出来的两处（都在共享层，不属于 Go）

1. `potato_from.from_go` **不收 `a, b int` 这种分组形参**（Go 里极常见），
   于是一整个函数被跳过；而且它**根本不存函数正文**，`--impl` 那条路是空的。
2. `from_go` 抹注释用的是 `sub(" ", …)`（**不保长度**），而取正文要拿剥离后的下标去切
   原文 —— 长度一变切出来就是别处的字节。`from_c` 那边早就是保长度的写法。

另有一处是**跨层打架**：`gotrans.GO_TYPES` 第一版把 `int` 映成 `i32`（照着 Loment 的
默认整数顺手写的），而 `potato_from.GO_TYPES` 写的是 `i64`（Go 的 `int` 在 x86-64 上
就是 64 位）—— 同一份 Go 会产出两个**互相矛盾**的单元。现在两层一个口径。

用法: python tools/loment_gotrans_test.py   （无 go/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import ctrans      # noqa: E402
import gotrans     # noqa: E402
import jtrans      # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


EX = ROOT / "loment" / "gotrans"

#: 期望值。**推出来的，不是抄的**（见 `Sample.go` 头注那份推导）：
#: `level(1000)*5 + gcd(48,18) + score(100) + widen(byte(200)) + letterIndex() + 40`
#: = `2*5 + 6 + 73 + 100 + 1 + 40` = **230**。
WANT_RC = 2 * 5 + 6 + (6 * 3 + 27 * 1 + 14 * 2) + (200 // 2) + (ord("A") - 64) + 40

#: 对照组那一边的入口。Go 的 `main` 没有返回值，拿"数"出来得走 `os.Exit` ——
#: 而 `os.Exit` 是**成员访问**（本子集不收），所以夹具补一个 `main`。
#: 与 C 那门的 `_start`、C# 那门的 `Harness` 同一个道理：**两边各有一个入口，比的是同一个数**。
_HARNESS = """package main

import "os"

func main() {
\tos.Exit(entry() % 256)
}
"""

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _go() -> str | None:
    return shutil.which("go")


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _run(exe: Path) -> int:
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


def _go_run(td: Path, go: str, src: Path) -> int:
    """Go 侧：`go build` 出 exe 再跑，读退出码。**不用 `go run`** —— 它吞退出码。"""
    (td / "Sample.go").write_text(src.read_text(encoding="utf-8"),
                                  encoding="utf-8", newline="\n")
    (td / "harness.go").write_text(_HARNESS, encoding="utf-8", newline="\n")
    exe = td / "go_side.exe"
    r = subprocess.run([go, "build", "-o", str(exe), "Sample.go", "harness.go"],
                       cwd=str(td), capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, f"go build 失败: {(r.stdout + r.stderr)[-500:]}"
    r = subprocess.run([str(exe)], cwd=str(td), capture_output=True, text=True,
                       timeout=300)
    return r.returncode


def _build_loment(td: Path, doc: dict) -> Path:
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    assert not skipped, f"发的时候跳过了东西: {skipped[:3]}"
    assert "pub extern fn " not in text, text[:300]
    p = td / "l_side.lomt"
    p.write_text(text + "\nfn _start() {\n    syscall4(60, entry() as u64, 0, 0);\n}\n",
                 encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


@test
def test_translation_runs_same_as_go():
    """**翻译出来的 Loment 跑出的数 == go build 那份 Go 跑出的数**。

    `Sample.go` 里不带括号的条件、三截 `for`、`for cond`、`for {}`、
    `i++` / `+=`、分组形参 `a, b int`、`byte`、`int(b)`、rune 字面量、
    `bool` 返回与布尔变量都有 —— 任一处翻错都会把 230 改掉。
    """
    go = _go()
    if not go or not _wsl():
        print("      SKIP: 需要 go + WSL")
        return
    src = EX / "Sample.go"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        doc, rep = potato_from.transcribe(src, "auto", "strict")
        assert not potato.validate(doc), potato.validate(doc)[:3]
        lost = [s for s in rep.skipped if s["kind"] == "fn"]
        assert not lost, f"转写那一步丢了函数: {lost}"
        assert doc["language"] == "loment" and doc["grammar"] == "go", doc["language"]
        go_rc = _go_run(td, go, src)
        l_rc = _run(_build_loment(td, doc))
    assert go_rc == WANT_RC, f"go 那边就不对: {go_rc} != {WANT_RC}（语料或期望值错了）"
    assert l_rc == go_rc, (
        f"**翻译出来的 Loment 与 go 编的 Go 结果不同**: {l_rc} != {go_rc}。\n"
        f"  （两个都对不上 {WANT_RC} 的话是语料问题；只有一边对不上就是翻译翻错了）")
    print(f"      go -> {go_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_go_shape_three_differences_from_the_brace_family():
    """**这一门是自足一份，因为三条形状都不一样**（`docs/188` §7.1）。

    钉住三条，每条都拿"花括号族"那一边做反面对照，说明为什么它不能塞进同一张方言表：

    1. **类型写在名字后面** —— `func f(a int) int`；
    2. **条件不带括号**；
    3. **没有 `while`**，一个 `for` 管三种形状（`for {}` / `for cond {}` /
       `for init; cond; post {}`）—— 第三条尤其不能共用：那边 `for` **必须**是三截。
    """
    # ① 类型在名字后面（`(a int)` 而不是 `(int a)`）
    t1 = gotrans.translate("package main\n\nfunc f(a int, b int) int {\n\treturn a + b\n}\n")
    assert "pub fn f(a: i64, b: i64) -> i64 {" in t1, t1
    # ② 条件不带括号（源码里就没有）
    t2 = gotrans.translate("package main\n\nfunc g(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n")
    assert "if (x > 0) {" in t2, t2
    # ③ 三种 `for`
    t3 = gotrans.translate("package main\n\nfunc h(n int) int {\n"
                           "\ts := 0\n\tfor i := 0; i < n; i++ {\n\t\ts++\n\t}\n"
                           "\tfor s > n {\n\t\ts--\n\t}\n"
                           "\tfor {\n\t\ts++\n\t\tif s > 100 {\n\t\t\treturn s\n\t\t}\n\t}\n}\n")
    assert "while (i_go__1 < n) {" in t3, t3        # 三截那种降级
    assert "while (s > n) {" in t3, t3             # 只有条件那种
    assert "while true {" in t3, t3                # 死循环那种
    # 反面：花括号族那边 `for` 必须是三截，`for x` 那种要报错
    try:
        ctrans.translate("int f(int n) { for n > 0 { return 1; } return 0; }\n")
    except (ctrans.Unsupported, ctrans.CError):
        pass
    else:
        raise AssertionError("C 那边把 `for n > 0` 收下了 —— 那边必须是三截")
    print("      类型在名字后 / 条件不带括号 / 三种 for —— 三条与花括号族都不同")


@test
def test_go_conversions_map_straight_onto_as():
    """**Go 的显式转换就是 Loment 的 `as`** —— 而且 Go **没有**隐式数值转换。

    这是六门里独一份：C / C++ 要靠翻译器**猜**哪里补转换（两边都隐式收），
    Java / C# 一个都不补，而 Go 是**源码里已经写好了**、直接搬。
    """
    t = gotrans.translate("package main\n\nfunc w(b byte) int {\n\treturn int(b) / 2\n}\n")
    assert "return ((b as i64) / 2);" in t, t
    # **另一半**：Go 也不许 `bool` 当整数用 —— 翻译器拒，理由要说对
    try:
        gotrans.translate("package main\n\nfunc f(v bool) int {\n\treturn v + 1\n}\n")
    except (gotrans.Unsupported, gotrans.GoError) as e:
        assert "布尔" in str(e) or "bool" in str(e), e
    print("      `int(b)` -> `(b as i64)`；`bool` 当整数用被拒（Go 自己也不许）")


@test
def test_statement_forms_others_reject_are_taken_here():
    """**同一处按"能表达的就转"给出相反结论 —— 而差别在源语言那边。**

    `i++` / `x += e`：C / Java / C# 里是**表达式**（有值），映射不过去 ⇒ 拒；
    Go 里是**语句、没有值** ⇒ 一字不差地展开成 `x = x + 1`。
    """
    t = gotrans.translate("package main\n\nfunc f(n int) int {\n\ts := 0\n"
                          "\tfor i := 0; i < n; i++ {\n\t\ts += 2\n\t}\n\treturn s\n}\n")
    assert "i_go__1 = (i_go__1 + 1);" in t, t     # `i++`
    assert "s = (s + 2);" in t, t                 # `s += 2`
    # 反面：**同一段写法在 C 那边是拒的**，拒的理由要指到点子上
    for tr, mod in ((ctrans.translate, ctrans), (jtrans.translate, jtrans)):
        try:
            tr("int f() { int i = 0; i++; return i; }\n")
        except (mod.Unsupported, mod.CError) as e:
            assert "自增/自减" in str(e) or "++" in str(e), e
        else:
            raise AssertionError("花括号族把 `i++` 收下了 —— 那边 `++` 是有值的表达式")
    print("      `i++` / `+=` 这一门收（Go 里是语句）；C / Java 那两门拒（那边是表达式）")


@test
def test_out_of_subset_is_loud():
    """**子集外的写法要报得出，而且指的对。**"""
    cases = [
        ("package main\n\nfunc f(s string) int {\n\treturn 0\n}\n", "string"),
        ("package main\n\nfunc f(n int) int {\n\tswitch n {\n\tcase 1:\n\t\treturn 1\n\t}\n\treturn 0\n}\n",
         "switch"),
        ('package main\n\nimport "fmt"\n\nfunc f() int {\n\treturn 0\n}\n', "import"),
        ("package main\n\nfunc f() (int, int) {\n\treturn 1, 2\n}\n", "多返回"),
        ("package main\n\nfunc f(m map[string]int) int {\n\treturn 0\n}\n", "map"),
        ("package main\n\nfunc f(a, b int) int {\n\ta, b = b, a\n\treturn a\n}\n", "多重"),
    ]
    for src, want in cases:
        try:
            gotrans.translate(src)
        except (gotrans.Unsupported, gotrans.GoError) as e:
            assert want in str(e), f"要点名 `{want}`: {e}"
        else:
            raise AssertionError(f"{src!r} 在子集外，却一个字都没报（该点 `{want}`）")
    print("      `string` / `switch` / `import` / 多返回值 / `map` / 多重赋值 都报得出")


def main() -> int:
    return loment_suite.run(TESTS, "loment_gotrans_test")


if __name__ == "__main__":
    sys.exit(main())
