#!/usr/bin/env python3
"""loment_pytrans_test.py — **Python 写法 -> Loment** 的判据（`docs/187`；`docs/188` §0）。

这是 Stage A 的第二门。判据与 C 那门（`loment_ctrans_test.py`）**同一条**：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接跑那份源码的结果」

## 2026-09-18：这一组判据**整个重排了**，因为模型变了

第一版假定"翻译器要把 Python 的语义搬过来"，于是拿 CPython 当**处处**的对照组。
按 `docs/188` §0 那是反的：**用 Python 写法写的单元是一个 Loment 程序**，
**拼法是 Python 的、语义是 Loment 的**。

所以语料现在按**两边一不一致**分档，判据各不相同：

| 档 | 语料 | 判据 |
|---|---|---|
| **同意** | `policy.py`、`blockscope.py` | 翻译出来的数 **== CPython 的数**（CPython 当对照组） |
| **不同意** | `intdiv.py`、`loopend.py`、`overflow.py` | 翻译出来的数 **== 本语言算的**，而且**必须 ≠ CPython 的** |
| **拒收** | `bool_as_int.py` | **前端**报错，且说得出为什么 |

第二档那几条"必须不同"是关键：少了它，那几份语料只是在摆样子，
"我们没有迁就 Python"这句话就没被测到。

**`blockscope.py` 那份语料还带着一次教训**：第一版把"Python 是函数级作用域、
Loment 的 `let` 是块级"当成一处语义差，并为此把声明白上提到函数头。**探了一下，
那个差根本不存在** —— 块里的 `let` 块外看得见，值也对。所以那一份现在是**同意集**的
语料。**写下来是为了下次别再凭印象加语义差：先探。**

用法: python tools/loment_pytrans_test.py    （无 CPython/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402
import pytrans     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "pytrans"

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _python() -> str | None:
    for n in ("python3", "python"):
        p = shutil.which(n)
        if p:
            return p
    return None


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


def _run(exe: Path) -> int:
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


def _cpython(src: Path) -> int:
    """CPython 跑那份源码的 `main()` —— **对照组**（只在"同意集"里当判据）。"""
    out = subprocess.run(
        [_python(), "-c",
         "import runpy,sys;m=runpy.run_path(sys.argv[1]);print(m['main']())", str(src)],
        capture_output=True, text=True, timeout=120, shell=False).stdout.strip()
    return int(out or -1)


#: Loment 侧的入口：60 号系统调用 = exit。
_L_ENTRY = """
fn _start() {
    syscall4(60, main() as u64, 0, 0);
}
"""


def _emit(td: Path, name: str) -> Path:
    """`.py` -> Potato -> `lomt_from --impl` -> 一份能编的 `.lomt`。**不起任何工具链。**"""
    doc, rep = potato_from.from_python((EX / name).read_text(encoding="utf-8"),
                                       name, "strict")
    assert not potato.validate(doc), (name, potato.validate(doc)[:3])
    assert not rep.skipped, f"{name}: 转写那一步就丢了东西: {rep.skipped[:3]}"
    # **它是 Loment**，只是写法是 Python（`docs/188` §3）
    assert doc["language"] == "loment" and doc["grammar"] == "python", doc["language"]
    text, _sk = lomt_from.emit_lomt(doc, impl=True)
    p = td / (name.replace(".", "_") + ".lomt")
    p.write_text(text + _L_ENTRY, encoding="utf-8", newline="\n")
    return p


def _build(td: Path, name: str) -> tuple[Path, list[str]]:
    """返回 `(可执行文件, 检查器诊断)`。诊断非空时**没有**可执行文件。"""
    p = _emit(td, name)
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        return p, errs
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / (name.replace(".", "_") + ".elf")
    exe.write_bytes(blob)
    return exe, []


# ---------------------------------------------------------------- 期望值：独立推出来的
#
# **推，不是抄** —— 抄一个数会把"两边一起错成同一副样子"放过去。
# "不同意集"那两条按**本语言**的语义推（向零截断的 `/` `%`；`for` 的终值是 `b`）。


def _policy_expected() -> int:
    """`policy.py`（同意集）。两边一样，所以照 Python 推也对。"""
    a = 2                     # level(1000)
    b = 700 // 2              # bucket(700, 2) = 350
    acc = 0
    for i in range(4):        # 0 + 2 + 4 + 6
        acc += i * a
    return (a * 10 + (b + 20) + acc) % 200        # (20 + 370 + 12) % 200 = 2


def _blockscope_expected() -> int:
    """`blockscope.py`（同意集）。

    `pick(1)`：`c` 真 -> `x = 10`；`pick(0)`：`c` 假 -> `x = 20`。
    所以 `pick(1)*10 + pick(0) = 10*10 + 20 = **120**`。
    """
    return 10 * 10 + 20


def _trunc_div(a: int, b: int) -> int:
    """**本语言**的整除：向零截断（与 CPython 的向下取整不同）。"""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _intdiv_loment() -> int:
    """`intdiv.py` 按**本语言**算：`/` `%` 向零截断。"""
    q1, q2 = _trunc_div(-7, 2), _trunc_div(7, -2)
    m1 = -7 - _trunc_div(-7, 2) * 2
    m2 = 7 - _trunc_div(7, -2) * -2
    return (q1 + q2 + 20) * 3 + (m1 + m2 + 20)


def _intdiv_cpython() -> int:
    """同一份语料**按 CPython** 算 —— 用来断言两者**不同**（否则语料没踩在缝上）。"""
    q1, q2 = -7 // 2, 7 // -2
    m1, m2 = -7 % 2, 7 % -2
    return (q1 + q2 + 20) * 3 + (m1 + m2 + 20)


def _loopend_loment() -> int:
    """`loopend.py` 按**本语言**算：`for` 之后 `i` 是 `n`（降级式的直接结果）。"""
    def walk(n):
        total, i = 0, 0
        while i < n:                  # `for i in range(n)` 的降级式
            total += i
            i += 1
        return total + i              # i == n
    return (walk(3) + walk(4)) % 200  # (3+3) + (6+4) = 16


def _loopend_cpython() -> int:
    def walk(n):
        total = 0
        for i in range(n):
            total += i
        return total + i              # i == n-1
    return (walk(3) + walk(4)) % 200  # (3+2) + (6+3) = 14


def _i64(x: int) -> int:
    """**本语言的 `i64`**：超过 64 位就按二进制补码回绕。

    这是**语言的定义**（`tools/pytrans.py` 文件头 §决定 1：「`int` 映 `i64` …… 溢出
    变成回绕」），不是从实现里读出来的数 —— 判据这一侧按定义重算一遍。
    """
    x &= (1 << 64) - 1
    return x - (1 << 64) if x >> 63 else x


def _overflow_loment() -> int:
    """`overflow.py` 按**本语言**算：`int` 的拼法就是 `i64`，连乘溢出即回绕。

    `7 * 1000 ** 8 == 7e24` 超过 `2^63-1` ⇒ 回绕成 `-4420394637261275136`，
    于是 `sign` 给 1（CPython 那边 `7e24 > 0`，给 0）—— 这一处差别把两个数分开。
    """
    huge = _i64(7 * 1000 ** 8)        # 回绕成负数
    s = 1 if huge < 0 else 0
    w = 0 - huge                      # 已确认 huge < 0
    d = sum(int(c) for c in str(w)) % 100
    return s * 100 + d                # 100 + 75 = 175


def _overflow_cpython() -> int:
    """同一份语料**按 CPython** 算 —— 用来断言两者**不同**（否则没踩在缝上）。

    CPython 的 `int` 是任意精度：`7e24` 原样留着，`sign` 给 0，数位和是 7 ⇒ **7**。
    """
    huge = 7 * 1000 ** 8             # 任意精度，不回绕
    s = 1 if huge < 0 else 0
    w = huge if huge > 0 else 0 - huge
    d = sum(int(c) for c in str(w)) % 100
    return s * 100 + d                # 0 + 7 = 7


#: 同意集：`(文件名, 期望退出码)`。**CPython 当对照组**。
AGREE = [
    ("policy.py", _policy_expected()),
    # **块里声明、块外用**：探出来两边本来就一致（见那份语料的头注）——
    # 第一版把它当成"语义差"并为此把声明白上提到函数头，那是多余的。
    ("blockscope.py", _blockscope_expected()),
]

#: 不同意集：`(文件名, 本语言的值, CPython 的值)`。**两个数必须不同**。
DISAGREE = [
    ("intdiv.py", _intdiv_loment(), _intdiv_cpython()),
    ("loopend.py", _loopend_loment(), _loopend_cpython()),
    # **类型的宽度**那一处：`int` 的拼法是 `i64`，溢出回绕 —— 本语言 175、CPython 7。
    ("overflow.py", _overflow_loment(), _overflow_cpython()),
]


@test
def test_agreeing_source_matches_cpython():
    """**同意集**：翻译出来的 Loment 跑出的数 == 直接用 CPython 跑那份 Python 的数。

    这一档里两边算得一样，所以 CPython 是个**有意义的对照组** —— 翻译器与它
    一起错成同一副样子的可能仍在，所以再与一个**独立推出来**的期望值对一次。
    """
    if not _wsl() or not _python():
        print("      SKIP: 需要 WSL + CPython")
        return
    with tempfile.TemporaryDirectory() as t:
        for name, want in AGREE:
            td = Path(t) / name.replace(".", "_")
            td.mkdir()
            exe, errs = _build(td, name)
            assert not errs, f"{name}: 翻译出来的 Loment 检查不过: {errs[:3]}"
            py_rc, l_rc = _cpython(EX / name), _run(exe)
            assert py_rc == want, f"{name}: CPython 那边就不对: {py_rc} != {want}"
            assert l_rc == py_rc, (
                f"{name}: **翻译出来的 Loment 与 CPython 不同**: {l_rc} != {py_rc}")
            print(f"      {name}: CPython -> {py_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_disagreeing_source_follows_loment_not_python():
    """**不同意集**：两边算得不一样，而**照本语言的来**（`docs/188` §0）。

    这两份语料是这一门最要紧的**证伪面**：第一版在这里发辅助函数去保住 CPython 的
    语义（`docs/187` 初稿），那是**方向反了**。现在：

    * `intdiv.py` —— `//` 与 `%` 在负号上：本语言给 **62**，CPython 给 56；
    * `loopend.py` —— `for` 循环之后循环变量：本语言给 **16**，CPython 给 14。

    **两个数必须不同。** 一样的话，说明这份语料没踩在缝上，而"我们没有迁就 Python"
    这句话就只是一个说法。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        for name, want, py_want in DISAGREE:
            assert want != py_want, (
                f"{name}: 本语言的 {want} 与 CPython 的 {py_want} **一样** —— "
                f"这份语料没踩在缝上，它钉不住任何东西")
            td = Path(t) / name.replace(".", "_")
            td.mkdir()
            exe, errs = _build(td, name)
            assert not errs, f"{name}: 翻译出来的 Loment 检查不过: {errs[:3]}"
            l_rc = _run(exe)
            assert l_rc == want, (
                f"{name}: 翻译出来的 Loment 给 {l_rc}，按本语言推出来是 {want}；"
                f"（CPython 会给 {py_want} —— 拿到那个数就说明**还在迁就 Python**）")
            if _python():
                assert _cpython(EX / name) == py_want, f"{name}: CPython 那边也不是 {py_want}"
            print(f"      {name}: 本语言 -> {l_rc}，CPython -> {py_want}（**不等**，对的）")


@test
def test_bool_as_int_is_refused_by_the_front_end():
    """**前端拒收集**：Python 的 `bool` 是 `int` 的子类，本语言的不是。

    `return a and b` 里 Python 的 `and` 返回的是**操作数**（`1 and 2` 是 `2`）——
    照翻成 `(a != 0) && (b != 0)` 两边都编得过、**只有数不一样**。
    按 `docs/188` §0 那条"**能表达的就转，表达不出来的就报错**"，这里报错。

    同时钉住**另一半**：`if x:`（x 是 int）**要转**成 `x != 0` —— 那是"整数当条件"
    的两种拼法，报错就过头了。
    """
    src = (EX / "bool_as_int.py").read_text(encoding="utf-8")
    try:
        pytrans.translate(src)
    except pytrans.Unsupported as e:
        msg = str(e)
        assert "操作数" in msg, f"要说清理由（Python 的 `and` 返回操作数）: {msg}"
        assert "布尔" in msg or "bool" in msg, f"要点出是布尔当整数用: {msg}"
        print(f"      `a and b` 当值用报得出: {msg[-90:]}")
    else:
        raise AssertionError(
            "`a and b` 当值用却一个字都没报 —— 照翻会得到 1 而 Python 给 2，"
            "两边都编得过。这正是要消灭的静默")

    # **另一半**：整数当条件要照收（转成 `!= 0`），不许一起拒了
    ok = ("def f(a: int) -> int:\n"
          "    if a:\n"
          "        return 1\n"
          "    return 0\n")
    text = pytrans.translate(ok)
    assert "a != 0" in text, f"`if a`（a 是 int）该转成 `a != 0`:\n{text}"
    print("      整数当条件照收：`if a` -> `if a != 0`")


@test
def test_unary_invert_is_refused_by_the_front_end():
    """**前端拒收集**：`~` 在本语言里没有对应的一元运算符。

    原先 `raw()` 把 `ast.Invert` 直接映成 `"~"`，而 Loment 的一元运算符只有 `-` 与 `!`
    —— **产出一份编不过的源**，而报出来的那个错指向**生成出来的那一份**（`docs/198` §1）。
    这一条钉住"点名拒、且说得出为什么"。

    **另一半也要钉**：`-a` 与 `+a` 照旧收（那两处本语言有一元运算符）——
    否则"把 `~` 拒了"很容易顺手把一元那一族一起拒掉。
    """
    src = (EX / "unary_invert.py").read_text(encoding="utf-8")
    try:
        pytrans.translate(src)
    except pytrans.Unsupported as e:
        msg = str(e)
        assert "宽度" in msg or "i32" in msg, f"要说清：{msg}"
        print(f"      `~a` 报得出: {msg[-80:]}")
    else:
        raise AssertionError(
            "`~a` 却一个字都没报 —— 发出来的 `( ~a )` 过不了本语言的词法器。"
            "这正是要消灭的「能过前端、后面必炸」")

    ok = ("def f(a: int) -> int:\n"
          "    return -a + +a\n")
    text = pytrans.translate(ok)
    assert "(-a)" in text and "(a)" in text, f"`-a` / `+a` 该照收:\n{text}"
    print("      一元 `-` / `+` 照收")


def main() -> int:
    return loment_suite.run(TESTS, "loment_pytrans_test")


if __name__ == "__main__":
    sys.exit(main())
