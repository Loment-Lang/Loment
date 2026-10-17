#!/usr/bin/env python3
"""loment_cpptrans_test.py — **C++ 写法 -> Loment，跑出来的数一样**（`docs/188` §7.1）。

六门里的第五门。判据与前几门**同一条**：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接跑那份源码的结果」

这一门把"直接跑那份源码"换成 WSL 的 `g++`（与 C 那门换 `clang` 同一个道理）：

    C++  ──g++（freestanding）──> ELF ──┐
                                        ├─> 退出码比一比
    C++  ──potato_from──> Potato ──lomt_from --impl──> Loment ──> 退出码 ──┘

**比的是数，不是文本。**

## 这一门最要紧的一句：**结论与 C 一样，理由完全不同**

| | C | C++ |
|---|---|---|
| `a < b` 出什么 | `int`（0/1） | **`bool`** |
| `int x = (a < b);` | 合法（本来就是 int） | **合法**（bool 隐式转 int） |
| `if (x)`（x 是 int） | 合法（标量即真） | **合法**（int 隐式转 bool） |

⇒ 两个方向的强制转换**都要补** —— 但 C 是因为"根本没有 bool"，C++ 是因为
"**有** bool，却把两个方向的隐式转换都留着"。**Java / C# 是关的**，因为那两门的
`int x = (a < b);` 本来就编不过。

这一格是"同一族里两门看起来一样、解释完全不同"的样本 —— 判据钉住它，是因为
将来谁看到"C 与 C++ 都开了两个开关"就去关掉一个，会当场弄坏一门。

## 写这一门的语料时撞出来的两个**共享核**的 bug

都在 `trans_core.py`，都是同一族：**发射器把"右边赋给左边"的场合一律写死成整数场合**。

1. `return` —— `bool positive(int x) { return x > 0; }` 会发成
   `return (x > 0) as i32;`，而函数声明的是 `-> bool`。
2. 变量本身 —— `ty_of(Var)` 一律报 "int"，于是 `bool ok = …; if (ok) …` 里那个
   `ok` 被当成整数：Java 那条 `coerce_int_to_bool=False` 的路会对一个**布尔**变量报
   "这里要的是条件，给的是整数" —— 一句**错的**诊断，而那段 Java 完全合法。

两处都只有"翻 `bool` 的代码"才撞得到，C 那门根本没有 bool，所以一直没被逼出来。

用法: python tools/loment_cpptrans_test.py   （无 WSL/g++ 时 SKIP，退出码 0）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import cpptrans    # noqa: E402
import ctrans      # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "cpptrans"

#: 期望值。**推出来的，不是抄的**（见 `Sample.cpp` 头注那份推导）：
#: `(clamp(42,0,9)*5 + as_flag(1,0)) + count_truthy(7,0,3)*7 + 40 + score(100)`
#: = `(9*5 + 0) + 2*7 + 40 + 73` = **172**。
WANT_RC = (9 * 5 + 0) + 2 * 7 + 40 + (6 * 3 + 27 * 1 + 14 * 2)

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


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


def _have_gpp() -> bool:
    if not _wsl():
        return False
    try:
        r = subprocess.run([*_WSLCMD, "bash", "-lc", "command -v g++"],
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _run(exe: Path) -> int:
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc", f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


#: C++ 侧的入口：**自己写 `_start` 走 60 号系统调用**，不借 libc/libstdc++ ——
#: 判据比的是"这份 C++ 算出什么"，所以两边都不能被各自的运行时影响。
#: `-fno-exceptions -fno-rtti` 也是为同一个目的（不然前端会引用 `__cxa_*`）。
_CPP_ENTRY = """
static void _loment_exit(long code) {
    __asm__ volatile("syscall" : : "a"(60L), "D"(code) : "rcx", "r11", "memory");
}
extern "C" void _start(void) { _loment_exit(entry()); }
"""

#: Loment 侧的入口。与上面那个同义（60 号 = exit），只是写法换成本语言。
_L_ENTRY = """
fn _start() {
    syscall4(60, entry() as u64, 0, 0);
}
"""

_FLAGS = ["-O1", "-ffreestanding", "-nostdlib", "-fno-stack-protector",
          "-fno-exceptions", "-fno-rtti", "-static"]


def _build_cpp(td: Path, src: str) -> Path:
    """C++ -> freestanding Linux ELF，**在 WSL 里用 g++ 编**。"""
    (td / "cpp_side.cpp").write_text(src + _CPP_ENTRY, encoding="utf-8", newline="\n")
    exe = td / "cpp_side.bin"
    cmd = ("g++ " + " ".join(_FLAGS) + f" -o {_wsl_path(exe)} {_wsl_path(td / 'cpp_side.cpp')}")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", cmd],
                       capture_output=True, text=True, timeout=600, shell=False)
    assert r.returncode == 0, f"C++ 编译失败: {(r.stdout + r.stderr)[-500:]}"
    return exe


def _build_loment(td: Path, doc: dict) -> Path:
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    assert not skipped, f"发的时候跳过了东西: {skipped[:3]}"
    # **一条 `extern fn` 都不该有** —— C++ 写法写出来的是 Loment（`docs/188` §3）
    assert "pub extern fn " not in text, text[:300]
    p = td / "l_side.lomt"
    p.write_text(text + _L_ENTRY, encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


@test
def test_translation_runs_same_as_gpp():
    """**翻译出来的 Loment 跑出的数 == g++ 编那份 C++ 跑出的数**。

    `Sample.cpp` 里 `bool` 返回与局部量、`true`/`false`、`const` 形参、两个方向的
    隐式转换、`for` 降级、三层 `else if` 都有 —— 任一处翻错都会把 172 改掉。
    """
    if not _have_gpp():
        print("      SKIP: 需要 WSL + g++")
        return
    src = EX / "Sample.cpp"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        doc, rep = potato_from.from_cpp(src.read_text(encoding="utf-8"),
                                        "Sample.cpp", "strict")
        assert not potato.validate(doc), potato.validate(doc)[:3]
        lost = [s for s in rep.skipped if s["kind"] == "fn"]
        assert not lost, f"转写那一步丢了函数: {lost}"
        assert doc["language"] == "loment" and doc["grammar"] == "cpp", doc["language"]
        cpp_rc = _run(_build_cpp(td, src.read_text(encoding="utf-8")))
        l_rc = _run(_build_loment(td, doc))
    assert cpp_rc == WANT_RC, f"g++ 那边就不对: {cpp_rc} != {WANT_RC}（语料或期望值错了）"
    assert l_rc == cpp_rc, (
        f"**翻译出来的 Loment 与 g++ 编的 C++ 结果不同**: {l_rc} != {cpp_rc}。\n"
        f"  （两个都对不上 {WANT_RC} 的话是语料问题；只有一边对不上就是翻译翻错了）")
    print(f"      g++ -> {cpp_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_bool_is_a_real_type_here_unlike_c():
    """**C++ 有真正的 `bool`，C 没有** —— 而 `true` / `false` 不是 `1` / `0`。

    钉三件：

    1. `bool` 映 `bool`、`true`/`false` 原样映过去（**不是** `1`/`0`：
       `let ok: bool = 1;` 在本语言里是类型错）；
    2. 布尔**变量**要按布尔用 —— `if (ok)` 出的必须是 `if ok`，不是 `if ok != 0`
       （后者会变成一个布尔与整数相比）；
    3. **C 那一门收不了 `bool`** —— 拿它当反面对照，说明"这一格真的是两门不同"。
    """
    cpp = cpptrans.translate("bool in_range(int v, int lo, int hi) {\n"
                             "    bool ok = v < hi;\n"
                             "    if (ok) { return true; }\n"
                             "    return false;\n"
                             "}\n")
    assert "-> bool {" in cpp, cpp
    assert "let ok: bool = (v < hi);" in cpp, cpp
    assert "if ok {" in cpp, cpp
    assert "return true;" in cpp and "return false;" in cpp, cpp
    assert "as i32" not in cpp, f"`bool` 场合不该补整数转换:\n{cpp}"
    # 反面对照：C 那门不认识 `bool`
    try:
        ctrans.translate("int f(int a) { bool b = a; if (b) { return 1; } return 0; }\n")
    except ctrans.Unsupported as e:
        assert "bool" in str(e), e
    else:
        raise AssertionError("C 那门把 `bool` 收下了 —— 但 C 的类型表里没有它")
    print("      `bool`/`true`/`false` 原样过去（C 那门收不了 `bool`，对照成立）")


@test
def test_both_coercions_are_still_needed_despite_the_bool_type():
    """**两个方向都要补** —— 而这一条钉的是"为什么"（与 C 不同，见文件头那张表）。

    将来谁看到"C 与 C++ 都开了两个开关"就去关掉一个，会当场弄坏一门 ——
    所以这两条断言写在这里，不只在文档里。
    """
    # ① `bool -> int`：C++ 允许 `int f() { return a && b; }`
    t1 = cpptrans.translate("int f(int a, int b) { return a && b; }\n")
    assert "as i32" in t1, f"`bool` 当 int 用时要补 `as i32`:\n{t1}"
    # ② `int -> bool`：C++ 允许 `if (x)`（x 是 int）
    t2 = cpptrans.translate("int g(int x) { if (x) { return 1; } return 0; }\n")
    assert "if x != 0 {" in t2, f"整数当条件时要补 `!= 0`:\n{t2}"
    # 反面对照：**Java 是关的** —— 同一段 Java 该被拒，而拒的理由要说对
    import jtrans
    try:
        jtrans.translate("class T { static int f(int a, int b) { return a && b; } }")
    except jtrans.Unsupported as e:
        assert "布尔" in str(e) or "bool" in str(e), e
    else:
        raise AssertionError("Java 那门把 `int f() { return a && b; }` 收下了 —— 那在 Java 里编不过")
    print("      `bool -> int` 补 `as i32`、`int -> bool` 补 `!= 0`（Java 那条路拒，对照成立）")


@test
def test_const_and_static_are_dropped_not_rejected():
    """**`const` 与 `static` 收下并丢掉**（`docs/186` §6.3 那次更正）。

    在**只有标量、没有指针**的子集里，`const int v`（形参）与 `static int f()` 丢掉
    那两个词是**保义**的：前者源侧本来就保证不改，后者是内部链接 —— 而这里翻的是
    **整个单元的全部函数**，没有第二个翻译单元能再定义同名函数。

    `const` 原先在 C 那门是**拒**的，理由是"它们都真的改语义" —— 对 `const` 不成立。
    做 C++ 这一门时一并改成"收下并丢掉"（C++ 里 `const` 形参是常态，拒了这门没法用）。
    """
    t = cpptrans.translate("static int f(const int v, const int lo) {\n"
                           "    const int hi = 9;\n"
                           "    if (v < lo) { return lo; }\n"
                           "    if (v > hi) { return hi; }\n"
                           "    return v;\n"
                           "}\n")
    assert "pub fn f(v: i32, lo: i32) -> i32 {" in t, t
    assert "let hi: i32 = 9;" in t, t
    assert "const" not in t.replace("// ", "").replace("docs/", ""), t
    # **另一半**：`extern` / `volatile` 还是照拒（它们真的改语义）
    for bad in ("extern", "volatile", "register", "typedef"):
        try:
            cpptrans.translate(f"{bad} int v = 1;\nint f() {{ return 2; }}\n")
        except (cpptrans.Unsupported, cpptrans.CError) as e:
            assert bad in str(e), (bad, e)
        else:
            raise AssertionError(f"`{bad}` 该拒，却一个字都没报")
    print("      `const` / `static` 收下并丢掉；`extern` / `volatile` 照拒")


@test
def test_out_of_subset_is_loud():
    """**子集外的写法要报得出，而且指的要对。**

    钉三处 C++ **独有**的：
    1. **预处理指令**（`#include` / `#define`）—— `#define` 真的改语义，而这一层
       没有可分辨的作用。所以语料**不带 include**（与 C 那门同一条）。
    2. **模板** —— 连 `typename` 都表示不了。
    3. **`char`** —— 它的符号性**由实现决定**（x86-64 上 g++ 是 signed，ARM 上常常不是），
       映 `i8` / `u8` 都会在某台机器上**悄悄算错**，所以拒。C 那门映 `i8` 是个写下来的
       决定（clang 在 x86-64 Linux 上就是 signed）；C++ 没有那个默认。
    """
    for src, want in (("#include <vector>\nint f() { return 1; }\n", "预处理"),
                      ("template<typename T> T id(T a) { return a; }\n", "template"),
                      ("int f(char c) { return 1; }\n", "char")):
        try:
            cpptrans.translate(src)
        except (cpptrans.Unsupported, cpptrans.CError) as e:
            assert want in str(e), f"要点名 `{want}`: {e}"
        else:
            raise AssertionError(f"{src!r} 在子集外，却一个字都没报")
    print("      `#include` / `template` / `char` 都报得出且指出原因")

    # **另一半**：`signed char` / `unsigned char` 是**明确**的，照映
    ok = cpptrans.translate("int f(unsigned char a) { return 1; }\n")
    assert "a: u8" in ok, ok
    print("      `unsigned char`（明确无歧义）照收 -> u8")


def main() -> int:
    return loment_suite.run(TESTS, "loment_cpptrans_test")


if __name__ == "__main__":
    sys.exit(main())
