#!/usr/bin/env python3
"""loment_cstrans_test.py — **C# 写法 -> Loment，跑出来的数一样**（`docs/188` §7.1）。

六门里的第四门（C / Python / Java 之后）。判据与那几门**同一条**：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接跑那份源码的结果」

这一门把"直接跑那份源码"换成 `dotnet`：

    C#  ──dotnet build/run──> Harness.Main 的退出码 ──┐
                                                       ├─> 比一比
    C#  ──potato_from──> Potato ──lomt_from --impl──> Loment ──> 退出码 ──┘

**比的是数，不是文本。**

## 这一门最要紧的一处：**同一个程序，两种写法，翻出同一份 Loment**

`loment/cstrans/Sample.cs` 与 `loment/jtrans/Sample.java` 是**同一个程序**的两种拼法
（`docs/188` §0）。所以这一门有一条别的门没有的判据：**两份源码翻出来的 Loment
逐行完全相同**。那是"Loment 可以被多种语法编写，其最终行为却无异"这句话本身，
而不是对它的一个推论 —— 两份各自 javac/dotnet 也算出同一个 99。

## 与 Java **真正**不同的一格：`byte`

| | Java | C# |
|---|---|---|
| `byte` 的取值 | -128..127（**有符号**） | **0..255（无符号）** |
| 映成 | `i8` | **`u8`** |

这是这一族的方言表里两门**唯一**没落在同一格上的类型。搞反不会报错、会**静默算错**
（C# 的 `byte b = 200;` 会变成 -56），所以它单独**两条判据、两层各一条**：

* `test_csharp_byte_is_unsigned_unlike_java` —— **对象层**（`potato_from`）：`byte` 映 `u8`、
  Java 的 `byte` 映 `i8`，同一个字段名只有符号不同；
* `test_a_byte_only_unit_runs_and_proves_it_is_unsigned` —— **端到端**：一份**全是 `byte`、
  不混宽度**的单元（`Byte.cs`）真编真跑，两边都得 **200**（`200 >= 128` 为真 —— 无符号才成立）。

**为什么要两份语料**：那条"混宽度会响亮失败"的边界只对**混**的单元有效，而端到端要验的是
"**窄类型本身能过**" —— 所以 `Byte.cs` 刻意只有一种宽度。它也不能并进 `Sample.cs`：
后者要与 `Sample.java` **逐行同源**，而 `byte` 的符号性正是两门**唯一**不同的一格
（塞进去会当场把那条判据弄红，实测撞到过）。

## 对照组怎么跑

C# 的入口是 `static int Main(string[])`，**返回 int 就是退出码** —— 比 Java 省一层
（那边得绕 `System.Exit`，而那是属性访问、本子集不收）。文件夹具仍然留着：语料是
"一份普通的 C# 代码"，不该为了被测试而长得像程序入口。

用法: python tools/loment_cstrans_test.py   （无 dotnet/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import cstrans      # noqa: E402
import jtrans       # noqa: E402
import lomelf       # noqa: E402
import lomentc      # noqa: E402
import lomt_from    # noqa: E402
import potato       # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


EX = ROOT / "loment" / "cstrans"
JEX = ROOT / "loment" / "jtrans"

#: 期望值。**推出来的，不是抄的**：`level(1000)=2`、`gcd(48,18)=6`、
#: `score(100)`（1..100 里 3 与 5 的公倍数 6 个 +3、能 3 不能 5 的 27 个 +1、
#: 能 5 不能 3 的 14 个 +2）= 18 + 27 + 28 = 73 ⇒ `2*10 + 6 + 73 = 99`。
#: 与 `loment_jtrans_test.WANT_RC` **同一个数** —— 因为是同一个程序（见文件头）。
WANT_RC = 2 * 10 + 6 + (6 * 3 + 27 * 1 + 14 * 2)

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _dotnet() -> str | None:
    return shutil.which("dotnet")


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


#: `dotnet` 的环境：关掉首跑横幅与遥测，免得它们混进 stdout。
_DOTNET_ENV = dict(os.environ, DOTNET_NOLOGO="1", DOTNET_CLI_TELEMETRY_OPTOUT="1",
                   DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1")


def _tfm(dotnet: str) -> str:
    """这个 SDK 装的是哪一代 —— `10.0.400` -> `net10.0`。

    **从 `dotnet --version` 推，不写死**：写死 `net10.0` 的话，一台只有 8.0 SDK 的
    机器上这条判据会红成"环境问题"，而它其实什么都没测。
    """
    r = subprocess.run([dotnet, "--version"], capture_output=True, text=True,
                       env=_DOTNET_ENV, timeout=180)
    major = (r.stdout or "").strip().split(".")[0]
    return f"net{major}.0" if major.isdigit() else "net8.0"


#: 项目文件。`AssemblyName=harness` 是为了**跑那条 dll**，而不是 `dotnet run` ——
#: `dotnet run` 把"编译失败"也报成退出码 1，与"程序自己 return 1"分不开。
#: 先 `build` 断言成功、再跑 dll，两个信号就干净地分开了。
_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>{tfm}</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <AssemblyName>harness</AssemblyName>
    <InvariantGlobalization>true</InvariantGlobalization>
    <GenerateDocumentationFile>false</GenerateDocumentationFile>
  </PropertyGroup>
</Project>
"""

#: **对照组那一边的入口**。C# 的 `Main` **返回 int 就是退出码**（比 Java 省一层）。
#: 与 C 那门的 `_start` 夹具同一个道理：**两边各有一个入口，比的是同一个数**。
_HARNESS = """using LomentDemo;

public class Harness
{
    public static int Main(string[] a)
    {
        return Sample.entry() % 256;
    }
}
"""

#: 全 `byte` 那一份（`Byte.cs`）的夹具。**`(byte)200` 这个转换写在夹具里**，不写进语料：
#: 语料是"一份普通的 C#"，夹具才是"测试用的入口"。
_HARNESS_BYTE = """using LomentDemo;

public class Harness
{
    public static int Main(string[] a)
    {
        return ByteProbe.probe((byte)200);
    }
}
"""


def _dotnet_run(td: Path, dotnet: str, src: Path, harness: str = _HARNESS) -> int:
    """C# 侧：dotnet 编「语料 + 夹具」，跑 dll，读退出码。"""
    tfm = _tfm(dotnet)
    (td / "p.csproj").write_text(_CSPROJ.format(tfm=tfm), encoding="utf-8", newline="\n")
    (td / "Harness.cs").write_text(harness, encoding="utf-8", newline="\n")
    (td / src.name).write_text(src.read_text(encoding="utf-8"),
                               encoding="utf-8", newline="\n")
    r = subprocess.run([dotnet, "build", "-c", "Release", "-v", "q", "--nologo"],
                       cwd=str(td), capture_output=True, text=True,
                       env=_DOTNET_ENV, timeout=900)
    assert r.returncode == 0, f"dotnet build 失败: {(r.stdout + r.stderr)[-500:]}"
    dll = td / "bin" / "Release" / tfm / "harness.dll"
    assert dll.exists(), f"没找到产物 {dll}"
    r = subprocess.run([dotnet, str(dll)], capture_output=True, text=True,
                       env=_DOTNET_ENV, timeout=300)
    return r.returncode


#: Loment 侧的入口：60 号系统调用 = exit。
_L_ENTRY = """
fn _start() {
    syscall4(60, entry() as u64, 0, 0);
}
"""

#: 全 `byte` 那一份的 Loment 入口。**`as u64` 那个转换写在夹具里**，与夹具同侧。
_L_ENTRY_BYTE = """
fn _start() {
    syscall4(60, probe(200) as u64, 0, 0);
}
"""


def _build_loment(td: Path, doc: dict, entry: str = _L_ENTRY) -> Path:
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    assert not skipped, f"发的时候跳过了东西: {skipped[:3]}"
    # **一条 `extern fn` 都不该有** —— C# 写法写出来的是 Loment（`docs/188` §3）
    assert "pub extern fn " not in text, text[:300]
    p = td / "l_side.lomt"
    p.write_text(text + entry, encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


@test
def test_translation_runs_same_as_dotnet():
    """**翻译出来的 Loment 跑出的数 == dotnet 编那份 C# 跑出的数**。

    两边编出来的是**同一种东西**（各自一个走 exit 的入口），所以两个退出码直接可比。
    语料 `Sample.cs` 里 `using` / `namespace` / `class` 三层外壳、顶层 `const`、
    `while` 改形参、`for` + `&&`、三层 `else if` 都有 —— 任一处翻错都会把 99 改掉。
    """
    dotnet = _dotnet()
    if not dotnet or not _wsl():
        print("      SKIP: 需要 dotnet + WSL")
        return
    src = EX / "Sample.cs"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        doc, rep = potato_from.from_csharp(src.read_text(encoding="utf-8"),
                                           "Sample.cs", "strict")
        assert not potato.validate(doc), potato.validate(doc)[:3]
        # **`from_csharp` 会把没有字段的类报成一条 skip**（`无可用字段`）—— 那是既有
        # 行为、不是丢东西：这个类只有常量与方法，本来就没有字段可收。所以这里只断言
        # **函数**一条都没被跳（那才是"少算一步"的来源），类别那一档不算。
        lost = [s for s in rep.skipped if s["kind"] == "fn"]
        assert not lost, f"转写那一步丢了函数: {lost}"
        # **它是 Loment**，只是写法是 C#（`docs/188` §3）
        assert doc["language"] == "loment" and doc["grammar"] == "csharp", doc["language"]
        cs_rc = _dotnet_run(td, dotnet, src)
        l_rc = _run(_build_loment(td, doc))
    assert cs_rc == WANT_RC, f"dotnet 那边就不对: {cs_rc} != {WANT_RC}（语料或期望值错了）"
    assert l_rc == cs_rc, (
        f"**翻译出来的 Loment 与 dotnet 编的 C# 结果不同**: {l_rc} != {cs_rc}。\n"
        f"  （两个都对不上 {WANT_RC} 的话是语料问题；只有一边对不上就是翻译翻错了）")
    print(f"      dotnet -> {cs_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_two_grammars_one_program_translate_the_same():
    """**同一个程序的两种拼法，翻出来的 Loment 逐行完全相同**（`docs/188` §0）。

    这是那条规定本身，不是对它的推论：

    > Loment 可以被多种语法编写，其最终行为却无异。

    `loment/cstrans/Sample.cs` 与 `loment/jtrans/Sample.java` 是**同一个程序**
    （逐句对应，只换了拼法：`namespace` 多一层壳、`const` 换掉 `static final`）。
    所以两份翻出来该**一字不差** —— 而且两边各自的编译器也算出同一个 99。

    **不比"差不多"**：比的是同一个字符串。差一个空格就是有一条翻译规则歪了。
    """
    consts = {"WARN_LEVEL": "i32", "ALARM_LEVEL": "i32"}
    cs = cstrans.translate((EX / "Sample.cs").read_text(encoding="utf-8"), consts=consts)
    java = jtrans.translate((JEX / "Sample.java").read_text(encoding="utf-8"), consts=consts)
    assert cs == java, (
        "两种拼法翻出来不一样 —— 那就说明有一门的翻译把语义也改了（`docs/188` §0）：\n"
        + "\n".join(
            f"  {i:>3}  C# | {a!r}\n      Java | {b!r}"
            for i, (a, b) in enumerate(zip(cs.splitlines(), java.splitlines()), 1)
            if a != b)[:1200])
    print(f"      两种写法（C# / Java）翻出同一份 Loment：{len(cs.splitlines())} 行逐行相同")


@test
def test_csharp_byte_is_unsigned_unlike_java():
    """**`byte` 在 C# 里是无符号的，在 Java 里是有符号的** —— 这一族唯一不同的一格。

    搞反了**不会报错，会静默算错**：C# 的 `byte b = 200;` 映成 `i8` 就成了 -56，
    而两边都编得过。所以它单独一条判据，钉的就是那一个格子。

    **判在 `potato_from` 那一层**：本子集的翻译器只分 `int`/`bool`/`void` 三档，
    看不出宽度（`tools/cstrans.py` 文件头那条边界），而"对面那块内存里这个字段多宽、
    有没有符号"正是表示层该记的事 —— 也是桥那头交换字节时唯一的依据。
    """
    cs_src = ("using System;\n\nnamespace D\n{\n    public struct Frame\n    {\n"
              "        public byte tag;\n        public int value;\n    }\n}\n")
    java_src = ("public class Frame {\n    public byte tag;\n    public int value;\n}\n")
    cd, _ = potato_from.from_csharp(cs_src, "Frame.cs")
    jd, _ = potato_from.from_java(java_src, "Frame.java")
    cfields = {f["name"]: f["type"] for s in cd["types"] for f in s["fields"]}
    jfields = {f["name"]: f["type"] for s in jd["types"] for f in s["fields"]}
    assert cfields.get("tag") == "u8", f"C# 的 `byte` 该是无符号的 u8，得到 {cfields}"
    assert jfields.get("tag") == "i8", f"Java 的 `byte` 该是有符号的 i8，得到 {jfields}"
    # 同一个字段名、同一个宽度，**只有符号不同** —— 正是"这一族唯一不同的一格"
    assert cfields["value"] == jfields["value"] == "i32", (cfields, jfields)
    print(f"      C# `byte` -> {cfields['tag']}（0..255），Java `byte` -> {jfields['tag']}（-128..127）")


@test
def test_a_byte_only_unit_runs_and_proves_it_is_unsigned():
    """**全 `byte` 的一份能整个过去、而且跑得对** —— 那条边界的**另一半**。

    `docs/188` §7.1.1 与语言卡上写的都是"**混**宽度会响亮地失败"。
    **另一半从来没写过**：不混呢？这一条就是那个答案 —— **能过，而且数是对的**。

    它的**值**本身就是判据：`probe((byte)200)` 里 `200 >= 128` 为真 ⇒ 返回 **200**。
    `byte` 若被映成**有符号**的 `i8`（Java 那个格子），要么当场报错（`i8` 装不下 200）、
    要么变成 -56 ⇒ `-56 >= 128` 为假 ⇒ 返回 0 —— **两种都对不上 200**。

    ⇒ 这一条把这一门的**头条格子**（`byte` 无符号）从"只在对象层验过"
    补成了"**端到端验过**"。语料单独一份（`Byte.cs`）而非塞进 `Sample.cs`：
    后者要与 `Sample.java` **逐行同源**，而 `byte` 的符号性正是两门**唯一**不同的一格
    —— 塞进去会当场把那条判据弄红（实测撞到过）。
    """
    dotnet = _dotnet()
    if not dotnet or not _wsl():
        print("      SKIP: 需要 dotnet + WSL")
        return
    src = EX / "Byte.cs"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        doc, rep = potato_from.from_csharp(src.read_text(encoding="utf-8"),
                                           "Byte.cs", "strict")
        assert not potato.validate(doc), potato.validate(doc)[:3]
        lost = [s for s in rep.skipped if s["kind"] == "fn"]
        assert not lost, f"转写那一步丢了函数: {lost}"
        # **对象层先说一次**：那个形参就是 `u8`（端到端那一半在下面）
        probe = {f["name"]: f for f in doc["functions"]}["probe"]
        assert probe["params"] == [{"name": "b", "type": "u8"}], probe
        assert probe["ret"] == "u8", probe
        cs_rc = _dotnet_run(td, dotnet, src, _HARNESS_BYTE)
        l_rc = _run(_build_loment(td, doc, _L_ENTRY_BYTE))
    assert cs_rc == 200, f"dotnet 那边就不对: {cs_rc} != 200（语料或期望值错了）"
    assert l_rc == cs_rc, (
        f"**全 byte 的一份没跑对**: {l_rc} != {cs_rc}。200 才是「无符号」的答案 —— "
        f"得到 0 就是把 `byte` 当成有符号的了")
    print(f"      dotnet -> {cs_rc}，翻译成 Loment -> {l_rc}（都是 200 = 无符号）")


@test
def test_shells_are_stripped_without_moving_lines():
    """**三层外壳抹掉，而行号不许动**（`cstrans._unwrap` → `trans_core.strip_shells`）。

    C# 比 Java 多两层/一种：`using` 指令、`namespace`（花括号形**与** C# 10 的文件级
    `namespace Foo;`）、以及 `class`。抹法是**换成等长空白**，所以：

    * 成员成了顶层 ✓
    * **行号与偏移一字不变** ✓ —— 报错里的行号就是源里的行号

    不这么抹（比如把成员抽出来拼一拼）的话，**报错的行号会指到别处** ——
    而那正是 `docs/179` §6.5 记过的那种"把人引向错方向"的东西。
    """
    src = (EX / "Sample.cs").read_text(encoding="utf-8")
    u = cstrans._unwrap(src)
    assert len(u) == len(src), "抹外壳不能改长度（改了行号就会漂）"
    assert u.count("\n") == src.count("\n"), "换行数也不许变"
    assert [i for i, c in enumerate(u) if c == "\n"] == \
           [i for i, c in enumerate(src) if c == "\n"], "换行位置漂了"
    # **判据落在"行"上，不落在"子串在不在"上** —— 那份文件的**头注里**就写着
    # `using System;` 与 `class`（用来说明它们会被抹掉），子串当然还在。要钉的是
    # **声明那几行**：源里以那几个词起头的行，在产物里必须**整行空白**，且长度不变。
    s_lines, u_lines = src.split("\n"), u.split("\n")
    assert len(s_lines) == len(u_lines), "行数不许变"
    shells = [(i, l) for i, l in enumerate(s_lines)
              if l.lstrip().startswith(("using ", "namespace ", "public class "))]
    assert len(shells) == 3, f"语料里该正好 3 条外壳行（using / namespace / class），得到 {shells}"
    for i, l in shells:
        assert u_lines[i].strip() == "", f"第 {i + 1} 行没被抹掉: {l!r}"
        assert len(u_lines[i]) == len(l), f"第 {i + 1} 行长度变了（抹法必须等长）"
    assert "public static int level(" in u, "成员该留在原处"

    # **文件级 namespace**（C# 10）—— 没有体，一路抹到 `;`
    fs = ("using System;\nnamespace Foo;\n\npublic class T\n{\n"
          "    public static int f() { return 1; }\n}\n")
    u2 = cstrans._unwrap(fs)
    assert len(u2) == len(fs) and "namespace" not in u2 and "class T" not in u2, u2
    assert "pub fn f() -> i32" in cstrans.translate(fs), cstrans.translate(fs)
    print("      三层外壳抹掉、长度与换行位置一字不变（含文件级 namespace）")


@test
def test_out_of_subset_is_loud():
    """**子集外的写法要报得出，理由要对，而且带出路。**

    钉两件 C# 特有的事：

    1. **`string`** —— 这门语言最常见的类型拼法，而本语言**没有字符串值**。
       值得说清的是**两层分工**：`potato_from` **认得**它（映 `str`，数据形状记下来了），
       但**翻译器**收不了（它要翻正文，而 `str` 不是本子集的值类型）。
       所以这份文件在对象层合法、在翻译层被拒 —— 拒得响亮。
    2. **`>>>`**（C# 11 起的无符号右移）—— 本语言的 `>>` 是**算术**的，
       所以它不能被切成 `>>` `>`（那样报的是"期望 `;`"）。**是一个 token、点名拒掉**，
       理由里带出路 `(a as u32) >> n`。
    """
    src = (EX / "Unsupported.cs").read_text(encoding="utf-8")
    # ① 对象层**是合法的** —— `string` 在表示层有格子（`str`）
    doc, _rep = potato_from.from_csharp(src, "Unsupported.cs", "strict")
    assert doc["language"] == "loment" and doc["grammar"] == "csharp", doc["language"]
    assert not potato.validate(doc), potato.validate(doc)[:3]
    # ② 翻译层**拒**
    try:
        cstrans.translate(src)
    except cstrans.Unsupported as e:
        assert "string" in str(e), f"要点名 `string`: {e}"
        print(f"      `string` 报得出: {e}")
    else:
        raise AssertionError("`string` 在子集外，却一个字都没报")

    # ③ `>>>` 要点名，且给出路
    try:
        cstrans.translate("using System;\nclass T\n{\n    public static int f(int a)"
                          "\n    {\n        return a >>> 1;\n    }\n}\n")
    except cstrans.Unsupported as e:
        msg = str(e)
        assert ">>>" in msg, f"要点名 `>>>`: {msg}"
        assert "u32" in msg, f"要给出路（`(a as u32) >> n`）: {msg}"
        print(f"      `>>>` 报得出且给出路: {msg[-60:]}")
    else:
        raise AssertionError("`>>>` 在子集外，却一个字都没报")

    # ④ **另一半**：`>>`（算术，与 Loment 一致）要**照收**，不许被一起拒了
    text = cstrans.translate("using System;\nclass T\n{\n    public static int f(int a)"
                             "\n    {\n        return a >> 1;\n    }\n}\n")
    assert ">> 1" in text, text
    print("      `>>`（算术，与 Loment 一致）照收")


def main() -> int:
    return loment_suite.run(TESTS, "loment_cstrans_test")


if __name__ == "__main__":
    sys.exit(main())
