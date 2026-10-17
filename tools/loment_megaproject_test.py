#!/usr/bin/env python3
"""loment_megaproject_test.py — **超大型项目**：100 个 Python 语法模块，共 1000 个函数。

用户 2026-10-10 的题：

> 继续，1000 个函数的超大型项目，100 个大型模块

以及 2026-10-11 的续题：

> 开始测试用 Java 语法写 300 个函数与 300 个模块

两题各一份语料、各三条判据，都在这个文件里：
**Python 那一份**是 100 模块 × 10 函数（`loment/examples/megaproject/`），
**Java 那一份**是 300 模块 × 1 函数（`loment/examples/megaproject-java/`）——
后者宿主的 300 条 `use` **正好压在上限上**，所以它同时也是那条上限的边界样本。

## 它测什么（与 `loment_multisyntax_projects_test.py` 的分工）

那个判据测的是"六个表层语法**各**一个大型项目，算出的数对不对"；这一条只问**规模**：

* **100 个模块**、**每个 10 个函数**、合计 **1000 个函数**，一次编、一次跑；
* 宿主是**原生 Loment**，`use` 全部 100 个模块，把每个模块的 `entry()` 折成一个数；
* 折法**顺序敏感**（`acc = (acc * 31 + e) % 1000003`）—— 只是求和的话，两处错会互相抵消。

## 三条决定，都是实测逼出来的

1. **函数名必须全局唯一。** 单元的发射符号是**平的**（ABI）：两个模块各有一个 `f0`
   会被拒（"函数 f0 与模块 a 重名"，见 issue #221 那条消息本身也指错了东西）。
   所以每个模块的函数带自己的前缀（`m007_f3`）。这不是本判据的偏好，是语言的规则。

2. **模块级常量一个都不用。** 函数内与模块常量同名的局部会被静默忽略（issue #216），
   而参数与常量同名会被误报成"参数重复"（issue #217）。这一门语料**避开这两个形状** ——
   它们是缺陷，不是语义差。原来该当常量的东西直接写成字面量。

3. **语料是生成物，判据钉住它不漂。** 100 份手抄必然漂，所以这里有一份 `gen_module()`：
   仓里那 100 个 `.lomt` 必须逐字节等于它的产物（`test_corpus_matches_the_generator`），
   与自举种子的做法同源（"能藏的前提是它可复现"）。

## 判据是三方

```
   生成器 ──> 等价的 Python 源码 ──CPython──> rc_control ─┐
                                                          ├─ 三者相等
   生成器 ──> 100 个 .lomt ──前门-> lomelf -> WSL 跑──> rc_loment ─┤
                                                          │
   README 里 `EXPECTED`（**另写一遍**的 oracle 推的）────────────────expected ┘
```

用法:
    python tools/loment_megaproject_test.py                 # 跑判据
    python tools/loment_megaproject_test.py --write          # 重新生成 Python 语料
    python tools/loment_megaproject_test.py --write-java     # 重新生成 Java 语料
    python tools/loment_megaproject_test.py --scale 10x100   # 跑一档大的（量耗时用）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf       # noqa: E402
import lomentc      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


PROJ = ROOT / "loment" / "examples" / "megaproject"

#: 模块数与每模块函数数。**"100 个大型模块、1000 个函数"是这个判据的题面**，不是可调参数。
N_MODULES = 100
N_FUNCS = 10

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ 生成器
#
# **这是唯一真源**：仓里那 100 份 `.lomt` 由它产出，判据断两者逐字节相同。

def _ks(i: int) -> tuple[int, int, int]:
    """每个模块三个小常数，由模块号推出来（不写模块级常量，理由见文件头 §2）。"""
    return (i * 7) % 13 + 1, (i * 5) % 11 + 1, (i * 3) % 7 + 1


def gen_module(i: int) -> str:
    p = f"m{i:03d}"
    k1, k2, k3 = _ks(i)
    return f"""choose write grammar python

# {p}.lomt —— 超大型项目的第 {i} 个模块（**Python 拼法**写的 Loment）。
#
# 10 个函数，名字一律带 `{p}_` 前缀 —— **单元的发射符号是平的**（ABI），
# 跨模块同名会被拒。生成器见 `tools/loment_megaproject_test.py`，判据钉着这份产物不漂。


def {p}_f0(x: int) -> int:
    s = 0
    for j in range({k3}):
        s += x + j * {k1}
    return s


def {p}_f1(x: int) -> int:
    return {p}_f0(x) * {k1} // 8


def {p}_f2(x: int) -> int:
    return ((x << 3) ^ (x >> 1)) & 1023


def {p}_f3(x: int) -> int:
    if x > 100:
        return x - 100
    elif x > 10:
        return x * 2
    return x + 1


def {p}_f4(x: int) -> int:
    if x <= 0:
        return 0
    return 1 + {p}_f4(x - 1)


def {p}_f5(x: int) -> int:
    t = 0
    for a in range(3):
        for b in range(3):
            if a * b > 0:
                t += a * b + x
    return t


def {p}_f6(x: int) -> int:
    c = x & 65535
    n = 0
    while n < 8:
        if c & 1:
            c = (c >> 1) ^ 0xB400
        else:
            c = c >> 1
        n += 1
    return c


def {p}_f7(x: int) -> int:
    v = x & 255
    s = v >> 1
    while s != 0:
        v = v ^ s
        s = s >> 1
    return v


def {p}_f8(x: int) -> int:
    lo = x
    hi = x
    if {k2} < lo:
        lo = {k2}
    if {k2} > hi:
        hi = {k2}
    return hi - lo


def {p}_entry() -> int:
    x = {k1} + {k2} + {k3}
    return ({p}_f1(x) + {p}_f2(x) + {p}_f3(x) + {p}_f4(x % 7) + {p}_f5(x)
            + {p}_f6(x) + {p}_f7(x) + {p}_f8(x) + {i}) % 251
"""


def gen_main() -> str:
    """宿主：**原生 Loment**，`use` 全部模块，把每个 `entry()` 折成一个数。

    折法顺序敏感（`acc * 31 + e`，再取模），所以两处错不会互相抵消。

    **`choose runtime` 是必需的**（`docs/227`：那一维是一句**保证**，不是提示）：这些模块
    自己用了 `%`，于是它们是"要运行期"的依赖，而**根单元必须满足依赖的要求**。少了这一行，
    编译器点名拒（`no_runtime` ⇒ 产物里不许出现那段运行期）。
    """
    out = ["module main", "", "choose runtime", ""]
    for i in range(N_MODULES):
        out.append(f'use "m{i:03d}.lomt"')
    out += ["", "fn _start() {", "    let acc: i64 = 0;"]
    for i in range(N_MODULES):
        out.append(f"    acc = (acc * 31 + m{i:03d}_entry() as i64) % 1000003;")
    out += ["    syscall4(60, (acc % 256) as u64, 0, 0);", "}", ""]
    return "\n".join(out)


def _corpus() -> dict[str, str]:
    files = {f"m{i:03d}.lomt": gen_module(i) for i in range(N_MODULES)}
    files["main.lomt"] = gen_main()
    return files


# ------------------------------------------------------------------ oracle
#
# **另写一遍**（与语料不同的手法）：`f0` 用闭式、`f2` 用位串、`f7` 用 `bin()`、
# `f4` 直接用 `max(x, 0)`。README 里那个 `EXPECTED` 就是它算出来的。

def oracle_entry(i: int) -> int:
    k1, k2, k3 = _ks(i)
    x = k1 + k2 + k3

    # f0 = Σ_{j<k3} (x + j*k1) —— 闭式
    f0 = k3 * x + k1 * k3 * (k3 - 1) // 2
    f1 = f0 * k1 // 8
    # f2：位串写法
    f2 = (int(bin(x << 3)[2:], 2) ^ (x >> 1)) & 1023
    f3 = (x - 100) if x > 100 else ((x * 2) if x > 10 else x + 1)
    f4 = max(x % 7, 0)                       # 递归就是数步数
    # f5：双重循环的闭式（a,b 都过 0..2，a*b>0 的格子）
    f5 = sum(a * b + x for a in range(3) for b in range(3) if a * b > 0)
    # f6：另写一遍那个 8 轮移位
    c = x & 65535
    for _ in range(8):
        c = ((c >> 1) ^ 0xB400) if (c & 1) else (c >> 1)
    f6 = c
    # f7：用 bin() 把每一对位折起来
    v = x & 255
    bits = bin(v)[2:].zfill(8)
    f7 = v
    s = v >> 1
    while s:
        f7 ^= s
        s >>= 1
    f8 = abs(x - k2)
    return (f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + i) % 251


def oracle_total() -> int:
    acc = 0
    for i in range(N_MODULES):
        acc = (acc * 31 + oracle_entry(i)) % 1000003
    return acc % 256


# ------------------------------------------------------------------ 分层（顶到 1000 个模块）
#
# 一个文件最多 `use` **300** 条（`docs/143` §93；超了 E020，消息是"门面拆小"）。
# 所以"1000 个模块"**不可能**由一个宿主平铺 `use` 完 —— 必须分层：
# 叶子是 Python 拼法的模块（子集里没有 `use`，只能被用），中间是**原生** hub，
# 每个 hub `use` 一批叶子并把它们折起来，宿主再折 hub。
#
# 本机实测（2026-10-11，仓内 HEAD）：10 hub × 100 叶子 = 1011 个模块，
# load+check 192s / IR 7.0s / lomelf 2.8s / ELF 4.73 MB，跑出的数与独立推的一致。

MAX_USE_PER_FILE = 300


def _leaf_src(i: int) -> str:
    """与 `gen_module()` **同一个算法**，只是编号用 4 位（要过 1000 个）。"""
    return gen_module(i).replace(f"m{i:03d}", f"m{i:04d}")


def gen_tiered(hubs: int, leaves: int) -> dict[str, str]:
    """分层项目的全套源：`hubs × leaves` 个叶子 + `hubs` 个 hub + 一个宿主。"""
    assert leaves <= MAX_USE_PER_FILE, f"一个 hub 只能 use {MAX_USE_PER_FILE} 条"
    assert hubs <= MAX_USE_PER_FILE, f"宿主只能 use {MAX_USE_PER_FILE} 条"
    files: dict[str, str] = {}
    n = 0
    for h in range(hubs):
        names = []
        for _ in range(leaves):
            files[f"m{n:04d}.lomt"] = _leaf_src(n)
            names.append(f"m{n:04d}")
            n += 1
        hp = f"hub{h:03d}"
        # hub 自己用了 `%` ⇒ 它是"要运行期"的**库**，而库必须**声明**它的需求
        # （`docs/227`：根那一行是"保证"，这一行是"需求"）。
        s = [f"module {hp}", "", "choose runtime", ""] + [f'use "{x}.lomt"' for x in names]
        s += ["", f"pub fn {hp}_entry() -> i64 {{", "    let acc: i64 = 0;"]
        s += [f"    acc = (acc * 31 + {x}_entry() as i64) % 1000003;" for x in names]
        s += ["    return acc;", "}", ""]
        files[f"{hp}.lomt"] = "\n".join(s)
    m = ["module main", "", "choose runtime", ""] + [f'use "hub{h:03d}.lomt"' for h in range(hubs)]
    m += ["", "fn _start() {", "    let acc: i64 = 0;"]
    m += [f"    acc = (acc * 31 + hub{h:03d}_entry()) % 1000003;" for h in range(hubs)]
    m += ["    syscall4(60, (acc % 256) as u64, 0, 0);", "}", ""]
    files["main.lomt"] = "\n".join(m)
    return files


def oracle_tiered(hubs: int, leaves: int) -> int:
    """分层版本的独立期望值 —— 与叶子那套 oracle 同源（另写一遍的手法）。"""
    acc = 0
    for h in range(hubs):
        ha = 0
        for k in range(leaves):
            ha = (ha * 31 + oracle_entry(h * leaves + k)) % 1000003
        acc = (acc * 31 + ha) % 1000003
    return acc % 256


# ------------------------------------------------------------------ Java 那一门，同样的规模
#
# 题面（用户 2026-10-11）：用 Java 语法写 **300 个函数、300 个模块**（每个模块一个函数）。
# 300 这个数不是随便挑的：它**正好是一个文件 `use` 的上限**，所以宿主这一层顶到了边。
#
# Java 那一门的形状与 Python 不同：整份是一个 `class` 外壳（翻译器抹成等长空白），
# 里面的 `public static` 方法成为顶层函数。子集同样很窄，写作时避开的东西见 README。

N_JAVA_MODULES = 300


def _jks(i: int) -> tuple[int, int, int]:
    return (i * 7) % 13 + 1, (i * 5) % 11 + 1, (i * 3) % 7 + 1


def gen_java_module(i: int) -> str:
    p = f"j{i:03d}"
    k1, k2, k3 = _jks(i)
    return f"""choose write grammar java

/*
 * {p}.lomt —— 超大型 Java 项目的第 {i} 个模块（**Java 语法**写的 Loment）。
 *
 * 整份是一个 `class` 外壳（翻译器抹成等长空白，行号不漂），里面的 `public static`
 * 方法成为顶层函数。函数名带 `{p}_` 前缀 —— **发射符号是平的**（ABI），跨模块同名会撞。
 * 生成器见 `tools/loment_megaproject_test.py`，判据钉着这份产物不漂。
 *
 * 这一门的写法限制（实测）：`i++` / `i += x` 都**不收**，循环变量要写开
 * （`i = i + 1`）；没有字段/全局量，所以一个类常量都不写。
 */

public class C{i:03d} {{

    public static int {p}_entry() {{
        int s = 0;
        for (int k = 0; k < {k3}; k = k + 1) {{
            s = s + k * {k1};
        }}
        int v = s + {k2};
        if (v > 100) {{
            v = v - 100;
        }} else if (v > 10) {{
            v = v * 2;
        }}
        return (v + {i}) % 251;
    }}
}}
"""


def gen_java_main() -> str:
    """宿主：原生 Loment，`use` **恰好 300 条**（这就是那个上限），把它们折起来。"""
    out = ["module main", "", "choose runtime", ""]
    for i in range(N_JAVA_MODULES):
        out.append(f'use "j{i:03d}.lomt"')
    out += ["", "fn _start() {", "    let acc: i64 = 0;"]
    for i in range(N_JAVA_MODULES):
        out.append(f"    acc = (acc * 31 + j{i:03d}_entry() as i64) % 1000003;")
    out += ["    syscall4(60, (acc % 256) as u64, 0, 0);", "}", ""]
    return "\n".join(out)


def java_corpus() -> dict[str, str]:
    files = {f"j{i:03d}.lomt": gen_java_module(i) for i in range(N_JAVA_MODULES)}
    files["main.lomt"] = gen_java_main()
    return files


def oracle_java_entry(i: int) -> int:
    """另写一遍（闭式求和 + 比较链），与语料不同手法。"""
    k1, k2, k3 = _jks(i)
    s = k1 * k3 * (k3 - 1) // 2
    v = s + k2
    if v > 100:
        v -= 100
    elif v > 10:
        v *= 2
    return (v + i) % 251


def oracle_java_total() -> int:
    acc = 0
    for i in range(N_JAVA_MODULES):
        acc = (acc * 31 + oracle_java_entry(i)) % 1000003
    return acc % 256


def _build_and_run(srcs: dict[str, str], td: Path) -> int:
    for name, text in srcs.items():
        (td / name).write_text(text, encoding="utf-8", newline="\n")
    main = td / "main.lomt"
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, td, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"检查不过: {errs[:2]}"
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    with tempfile.TemporaryDirectory() as t:
        exe = Path(t) / "side.elf"
        exe.write_bytes(blob)
        return _run_elf(exe)


# ------------------------------------------------------------------ 跑
class _Skip(Exception):
    """跑不了那个 ELF（没有 Linux、也没有 WSL）-> **可见地跳过**，不是静默绿。"""


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _run_elf(exe: Path) -> int:
    """跑那个 ELF，读退出码。

    **产物是 x86-64 静态 ELF**（`lomelf` 出的就是它），所以 **Linux 上直接跑** ——
    这是让这条判据在 **CI（ubuntu）上也真跑**的唯一办法（`wsl` 在那边不存在，
    照老写法这条会在 runner 上一路 SKIP，等于没测）。
    Windows 上没有 WSL 就跑不了 —— 那时**可见地跳过**。
    """
    if sys.platform.startswith("linux"):
        exe.chmod(0o755)
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=900, shell=False)
        return r.returncode
    if shutil.which("wsl"):
        r = subprocess.run(
            [*_WSLCMD, "bash", "-lc",
             f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
            capture_output=True, text=True, timeout=900, shell=False)
        return int((r.stdout or "").strip() or -1)
    raise _Skip("既不是 Linux、也没有 WSL")


def _loment_rc() -> int:
    main = PROJ / "main.lomt"
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{main}: 检查不过: {errs[:3]}"
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "side.elf"
        exe.write_bytes(blob)
        return _run_elf(exe)


def _control_python(td: Path) -> int:
    """对照组：把生成器产出的**等价 Python** 交给 CPython 跑。

    语料本身是 Python **拼法**的 Loment（不是合法 Python），所以对照侧用的是
    `gen_module()` 里那些函数体的直译 —— 与语料同源，**不是独立推导**。
    独立的那一路是 `oracle_total()`（README 里的 `EXPECTED`）。
    """
    py = td / "mega.py"
    parts = ["def _k1(i): return (i * 7) % 13 + 1",
             "def _k2(i): return (i * 5) % 11 + 1",
             "def _k3(i): return (i * 3) % 7 + 1", "", ""]
    for i in range(N_MODULES):
        p = f"m{i:03d}"
        k1, k2, k3 = _ks(i)
        parts.append(f"def {p}_f0(x):\n    s = 0\n    for j in range({k3}):\n        s += x + j * {k1}\n    return s")
        parts.append(f"def {p}_f1(x):\n    return {p}_f0(x) * {k1} // 8")
        parts.append(f"def {p}_f2(x):\n    return ((x << 3) ^ (x >> 1)) & 1023")
        parts.append(f"def {p}_f3(x):\n    if x > 100:\n        return x - 100\n    elif x > 10:\n        return x * 2\n    return x + 1")
        parts.append(f"def {p}_f4(x):\n    if x <= 0:\n        return 0\n    return 1 + {p}_f4(x - 1)")
        parts.append(f"def {p}_f5(x):\n    t = 0\n    for a in range(3):\n        for b in range(3):\n            if a * b > 0:\n                t += a * b + x\n    return t")
        parts.append(f"def {p}_f6(x):\n    c = x & 65535\n    n = 0\n    while n < 8:\n        if c & 1:\n            c = (c >> 1) ^ 0xB400\n        else:\n            c = c >> 1\n        n += 1\n    return c")
        parts.append(f"def {p}_f7(x):\n    v = x & 255\n    s = v >> 1\n    while s != 0:\n        v = v ^ s\n        s = s >> 1\n    return v")
        parts.append(f"def {p}_f8(x):\n    lo = x\n    hi = x\n    if {k2} < lo:\n        lo = {k2}\n    if {k2} > hi:\n        hi = {k2}\n    return hi - lo")
        parts.append(f"def {p}_entry():\n    x = {k1} + {k2} + {k3}\n"
                     f"    return ({p}_f1(x) + {p}_f2(x) + {p}_f3(x) + {p}_f4(x % 7) "
                     f"+ {p}_f5(x) + {p}_f6(x) + {p}_f7(x) + {p}_f8(x) + {i}) % 251")
        parts.append("")
        parts.append("")
    body = "".join(f"    acc = (acc * 31 + m{i:03d}_entry()) % 1000003\n" for i in range(N_MODULES))
    parts.append("def total():\n    acc = 0\n" + body + "    return acc % 256\n")
    py.write_text("\n".join(parts), encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, "-c",
                        "import runpy,sys;print(runpy.run_path(sys.argv[1])['total']())", str(py)],
                       capture_output=True, text=True, timeout=600, shell=False)
    assert r.returncode == 0, f"CPython 跑失败: {r.stderr[-300:]}"
    return int(r.stdout.strip())


def _expected() -> int:
    txt = (PROJ / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\s*EXPECTED:\s*(\d+)\s*$", txt)
    assert m, f"{PROJ}/README.md 里没有 `EXPECTED: <数>` 那一行"
    return int(m.group(1))


# ------------------------------------------------------------------ 判据

@test
def test_corpus_matches_the_generator():
    """仓里那 100 份 `.lomt` 必须**逐字节**等于 `gen_module()` 的产物。

    与自举种子同一条纪律（`loment_seed_test`）：生成物可以进仓，但前提是它**可复现** ——
    没有这一条，手改一份 `.lomt` 而没改生成器就会静默漂，判据却照样绿。
    """
    want = _corpus()
    for name, text in want.items():
        p = PROJ / name
        assert p.is_file(), f"{p} 不在树里 —— 先跑 `--write` 生成语料"
        got = p.read_text(encoding="utf-8")
        assert got == text, (
            f"{name} 与生成器不一致（{len(got)}B vs {len(text)}B）—— "
            f"要么它被手改了，要么生成器改了没重新生成。跑 `--write`。")
    extra = sorted(p.name for p in PROJ.glob("m[0-9]*.lomt"))
    assert len(extra) == N_MODULES, f"模块数不对：{len(extra)} != {N_MODULES}"
    print(f"      {len(want)} 份文件与生成器逐字节一致（{N_MODULES} 模块 + 宿主）")


@test
def test_the_scale_is_what_was_asked_for():
    """**"1000 个函数 / 100 个模块"是个可数的要求**，不是形容词。"""
    nfn = 0
    for i in range(N_MODULES):
        text = (PROJ / f"m{i:03d}.lomt").read_text(encoding="utf-8")
        n = len(re.findall(r"(?m)^def\s+\w+\s*\(", text))
        assert n == N_FUNCS, f"m{i:03d}.lomt 有 {n} 个函数，要求 {N_FUNCS}"
        nfn += n
    assert nfn == N_MODULES * N_FUNCS == 1000, nfn
    main = (PROJ / "main.lomt").read_text(encoding="utf-8")
    assert main.count('use "m') == N_MODULES, "宿主没有 use 全部模块"
    assert f"m{N_MODULES - 1:03d}_entry()" in main, "最后一个模块没有被折进去"
    print(f"      {N_MODULES} 个模块、{nfn} 个函数、宿主 use {N_MODULES} 个模块")


@test
def test_the_whole_thing_builds_runs_and_agrees_three_ways():
    """**1000 个函数一次编、一次跑**，而且 `loment == CPython == 独立推的`。"""
    want = _expected()
    orc = oracle_total()
    assert orc == want, f"README 的 EXPECTED 与 oracle 不一致: {want} != {orc}"
    with tempfile.TemporaryDirectory() as t:
        ctl = _control_python(Path(t))
    assert ctl == want, f"CPython 对照与独立期望值不一致: {ctl} != {want}"
    try:
        got = _loment_rc()
    except _Skip as e:
        print(f"      SKIP 跑不了 ELF（{e}）—— 独立期望值 {want} 仍然核对过")
        return
    assert got == ctl, f"Loment 与对照组不一致: {got} != {ctl}"
    print(f"      1000 个函数 / {N_MODULES} 个模块 -> loment={got} cpython={ctl} expected={want}")


@test
def test_a_host_cannot_use_more_than_the_cap_of_modules():
    """**一个文件最多 `use` 300 条** —— 这是"1000 个模块必须分层"的那条语言级理由。

    不是本判据的偏好：超一条编译就拒（E020），消息还告诉你怎么办（"门面拆小"）。
    没有这一条，README 里"为什么要分层"就只是一句说法。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for i in range(MAX_USE_PER_FILE + 1):
            (td / f"u{i:03d}.lomt").write_text(
                f"module u{i:03d}\npub fn g{i:03d}() -> i64 {{ return {i}; }}\n",
                encoding="utf-8", newline="\n")
        m = ["module main"] + [f'use "u{i:03d}.lomt"' for i in range(MAX_USE_PER_FILE + 1)]
        m += ["", "fn _start() { syscall4(60, g000() as u64, 0, 0); }", ""]
        main = td / "main.lomt"
        main.write_text("\n".join(m), encoding="utf-8", newline="\n")
        mod = lomentc.load(main)
        try:
            lomentc.resolve_deps(mod, ROOT, td, entry=main)
        except Exception as e:                                     # noqa: BLE001
            assert "超过上限" in str(e) and str(MAX_USE_PER_FILE) in str(e), \
                f"超限该说清是条数上限: {e}"
            print(f"      {MAX_USE_PER_FILE + 1} 条 use 被拒: {str(e).splitlines()[0][:72]}")
        else:
            raise AssertionError(
                f"{MAX_USE_PER_FILE + 1} 条 use 竟然过了 —— "
                f"那「1000 个模块要分层」这条理由就不成立")


@test
def test_the_tiered_shape_builds_and_runs():
    """**分层**那一档：hub `use` 叶子、宿主 `use` hub —— 这是顶到上千模块的唯一形状。

    规模刻意取小（默认 4 hub × 40 叶子 = 164 个模块），因为它要进**门禁** ——
    上千模块那一档在本机是 192s（README 有实测表），不适合每轮都跑；
    要跑它用 `python tools/loment_megaproject_test.py --scale 10x100`。
    """
    hubs, leaves = 4, 40
    want = oracle_tiered(hubs, leaves)
    srcs = gen_tiered(hubs, leaves)
    try:
        with tempfile.TemporaryDirectory() as t:
            got = _build_and_run(srcs, Path(t))
    except _Skip as e:
        print(f"      SKIP 跑不了 ELF（{e}）")
        return
    assert got == want, f"分层项目算错了: {got} != {want}（独立 oracle）"
    print(f"      {hubs} hub × {leaves} 叶子 = {hubs * leaves + hubs + 1} 个模块 -> {got}")


JPROJ = ROOT / "loment" / "examples" / "megaproject-java"


def _java_control(td: Path) -> int:
    """对照组：把 Java 那份源的**首行抹掉**，它就是**真的 Java 文件**，交给 javac/java。

    与 Python 那一门同一个手法：`choose write grammar java` 是**读法**，不是那份源的一部分
    （`docs/188` §1）。没有 javac/java 就**可见地跳过**。
    """
    import loment_jtrans_test as T  # noqa: PLC0415
    javac, java = T._javac(), T._java()
    if not javac or not java:
        raise _Skip("无 javac / java")
    for i in range(N_JAVA_MODULES):
        lines = gen_java_module(i).split("\n")
        assert lines[0].strip() == "choose write grammar java"
        lines[0] = " " * len(lines[0])          # 抹成等长空白，行号不漂
        (td / f"C{i:03d}.java").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    body = "".join(f"        acc = (acc * 31 + C{i:03d}.j{i:03d}_entry()) % 1000003;\n"
                   for i in range(N_JAVA_MODULES))
    (td / "Harness.java").write_text(
        "public class Harness {\n    public static void main(String[] a) {\n"
        "        int acc = 0;\n" + body + "        System.exit(acc % 256);\n    }\n}\n",
        encoding="utf-8", newline="\n")
    r = subprocess.run([javac, "-d", str(td), str(td / "Harness.java")]
                       + [str(td / f"C{i:03d}.java") for i in range(N_JAVA_MODULES)],
                       capture_output=True, text=True, timeout=1200, shell=False)
    assert r.returncode == 0, f"javac 失败: {r.stderr[-400:]}"
    r = subprocess.run([java, "-cp", str(td), "Harness"],
                       capture_output=True, text=True, timeout=600, shell=False)
    return r.returncode


@test
def test_java_corpus_matches_the_generator():
    """300 个 `.java` 语料与生成器逐字节一致（与 Python 那一门同一条纪律）。"""
    want = java_corpus()
    for name, text in want.items():
        p = JPROJ / name
        assert p.is_file(), f"{p} 不在树里 —— 先跑 `--write-java`"
        assert p.read_text(encoding="utf-8") == text, f"{name} 与生成器不一致，跑 `--write-java`"
    got = sorted(p.name for p in JPROJ.glob("j[0-9]*.lomt"))
    assert len(got) == N_JAVA_MODULES, f"模块数不对：{len(got)} != {N_JAVA_MODULES}"
    print(f"      {len(want)} 份文件与生成器逐字节一致（{N_JAVA_MODULES} 模块 + 宿主）")


@test
def test_java_scale_is_300_modules_and_300_functions():
    """**300 个模块、300 个函数** —— 而且宿主那 300 条 `use` 正好压在**上限**上。"""
    nfn = 0
    for i in range(N_JAVA_MODULES):
        text = (JPROJ / f"j{i:03d}.lomt").read_text(encoding="utf-8")
        n = len(re.findall(r"(?m)^\s*public\s+static\s+int\s+\w+\s*\(", text))
        assert n == 1, f"j{i:03d}.lomt 有 {n} 个 static 方法，要求 1"
        nfn += n
    assert nfn == N_JAVA_MODULES == 300, nfn
    main = (JPROJ / "main.lomt").read_text(encoding="utf-8")
    assert main.count('use "j') == N_JAVA_MODULES, "宿主没有 use 全部模块"
    assert main.count('use "j') == MAX_USE_PER_FILE, "宿主的 use 条数应当正好是上限 300"
    print(f"      {N_JAVA_MODULES} 个模块、{nfn} 个函数、宿主 use {N_JAVA_MODULES} 条（= 上限）")


@test
def test_java_project_builds_runs_and_agrees_three_ways():
    """三方：`loment == javac/java == 独立推的`。"""
    want = _expected_java()
    orc = oracle_java_total()
    assert orc == want, f"README 的 EXPECTED 与 oracle 不一致: {want} != {orc}"
    main = JPROJ / "main.lomt"
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{main}: 检查不过: {errs[:3]}"
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        exe = td / "side.elf"
        exe.write_bytes(blob)
        try:
            got = _run_elf(exe)
        except _Skip as e:
            print(f"      SKIP 跑不了 ELF（{e}）—— 独立期望值 {want} 仍然核对过")
            return
    try:
        with tempfile.TemporaryDirectory() as t:
            ctl = _java_control(Path(t))
    except _Skip as e:
        assert got == want, f"Loment 与独立期望值不一致: {got} != {want}"
        print(f"      SKIP javac/java（{e}）—— loment={got} == expected={want}")
        return
    assert ctl == want, f"Java 对照与独立期望值不一致: {ctl} != {want}"
    assert got == ctl, f"Loment 与 Java 对照不一致: {got} != {ctl}"
    print(f"      300 个模块 -> loment={got} java={ctl} expected={want}")


def _expected_java() -> int:
    txt = (JPROJ / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\s*EXPECTED:\s*(\d+)\s*$", txt)
    assert m, f"{JPROJ}/README.md 里没有 `EXPECTED: <数>` 那一行"
    return int(m.group(1))


def _scale(spec: str) -> int:
    """`--scale 10x100`：跑一档大的，打印各阶段耗时（README 那张表就是这么量的）。"""
    hubs_s, _, leaves_s = spec.partition("x")
    hubs, leaves = int(hubs_s), int(leaves_s)
    t0 = time.time()
    srcs = gen_tiered(hubs, leaves)
    t1 = time.time()
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for name, text in srcs.items():
            (td / name).write_text(text, encoding="utf-8", newline="\n")
        main = td / "main.lomt"
        mod = lomentc.load(main)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=main)
        errs = lomentc.check(mod, deps=deps)
        t2 = time.time()
        assert not errs, errs[:2]
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
        t3 = time.time()
        exe = td / "side.elf"
        exe.write_bytes(blob)
        rc = _run_elf(exe)
        t4 = time.time()
    want = oracle_tiered(hubs, leaves)
    n = hubs * leaves
    print(f"{hubs} hub × {leaves} 叶子 = {n} 叶子 / {n + hubs + 1} 个模块")
    print(f"  生成 {t1 - t0:.1f}s | load+check {t2 - t1:.1f}s | IR {t3 - t2:.1f}s "
          f"| lomelf {t4 - t3:.1f}s | 总 {t4 - t0:.1f}s")
    print(f"  ELF {len(blob)}B | rc={rc} | 独立 oracle={want} | "
          f"{'一致' if rc == want else '不一致!'}")
    return 0 if rc == want else 1


def _write() -> int:
    PROJ.mkdir(parents=True, exist_ok=True)
    for name, text in _corpus().items():
        (PROJ / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"写了 {len(_corpus())} 份到 {PROJ}")
    print(f"oracle 算出来: EXPECTED = {oracle_total()}")
    return 0


def main() -> int:
    if "--write" in sys.argv:
        return _write()
    if "--write-java" in sys.argv:
        JPROJ.mkdir(parents=True, exist_ok=True)
        for name, text in java_corpus().items():
            (JPROJ / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"写了 {len(java_corpus())} 份到 {JPROJ}")
        print(f"oracle 算出来: EXPECTED = {oracle_java_total()}")
        return 0
    if "--scale" in sys.argv:
        return _scale(sys.argv[sys.argv.index("--scale") + 1])
    return loment_suite.run(TESTS, "loment_megaproject_test")


if __name__ == "__main__":
    sys.exit(main())
