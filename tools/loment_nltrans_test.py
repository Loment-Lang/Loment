#!/usr/bin/env python3
"""loment_nltrans_test.py — **自然语言写法 -> Loment**（`docs/197`）。

## 这一门的判据与前六门**形状不同**，要说清为什么

前六门（C / Python / Java / C# / C++ / Go）判的是

> 翻译出来的 Loment 跑出的数 == **那份源语言自己的编译器**跑出的数

这一门**没有对面那个编译器** —— 它不是别人已有的语言，是我们自己发明的
（`docs/197` §1）。所以"对照组"换成**同一个程序的 Loment 写法**，判据因此是
`docs/188` §7.1.1 那一条的**原话**：

> **两份源码翻出来的 Loment 逐行完全相同** —— 比的是**同一个字符串**，不是"差不多"。

具体到这一门，两份是：

    loment/nltrans/Sample.nl   自然语言写法
    loment/nltrans/Sample.lomt 标准 Loment 写法（同一个程序）

`front_door(Sample.nl)` 出来的字节要**一个不差**地等于 `Sample.lomt` 的字节。

**为什么这比"数相等"更硬**：数相等只说明"两边算到了同一个结果"，而它容许过程里
有一百处拼法漂移；逐字节相等说明**根本没有漂移**。反过来说，`Sample.lomt` 也必须
**自己**能编能跑 —— 否则它可能只是一串"长得像 Loment"的字节，那样逐字节相等就
退化成了"比两份文本"，第三、四条判据钉的就是这一点。

## 期望值是**推出来的，不是抄的**

`WANT_RC` 下面那三行就是推导，与 `Sample.lomt` 头注里那份一致。

用法: python tools/loment_nltrans_test.py   （无 WSL 时真跑那两条 SKIP，退出码 0）
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import nltrans     # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "nltrans"

#: **独立推出来的期望值**（推法与 `Sample.lomt` 头注里那份相同）：
#:   `sum_to(3)`   = 1+2                       = 3
#:   `gcd(48,18)`  = 6
#:   `label(8)`    = 8 是偶数 -> 1
#:   `tally(100)`  = 100 -> 50 -> 25 -> 12 -> 6 -> 3 -> 1 -> 0，共 7 步
#:   `LIMIT * 10`  = 30
#:                                            ----
#:                                            47
WANT_RC = (1 + 2) + 6 + 1 + 7 + (3 * 10)

#: `Surface.nl` / `Surface.lomt` 那一对（完整面）的期望值。**推出来的**：
#:   第一行 `total(p) + score(k) + pick(k) + summed + big`
#:     p = Point{x:4, y:5}  -> total = 9
#:     k = Kind::Big(3)     -> score = 3*2 = 6；pick = 3
#:     xs 先 [0,0,0] 再设成 [1,2,3]，然后 `fill` 把第 0 格改成 99 -> [99,2,3]
#:     summed = 99+2+3 = 104；big = max(8, 3) = 8
#:     => 9 + 6 + 3 + 104 + 8 = **130**
#:   第二行 `s + g + SECRET + take(xs)`
#:     s = p.size() = 4+5 = 9；g = guarded(2) = 2；SECRET = 7；take = 99
#:     => 9 + 2 + 7 + 99 = **117**
WANT_SURFACE_OUT = "surface 130 117\n"
#: 退出码走**库函数**那条路：`triple(big)` = 8*3 = **24**（`use` 进来的那份库真的被用到了）
WANT_SURFACE_RC = 8 * 3


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 跑那一侧

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


def _run_elf(exe: Path) -> tuple[int, str]:
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n \";$?\""],
        capture_output=True, text=True, timeout=300, shell=False)
    out, _, rc = r.stdout.rpartition(";")
    return int(rc.strip() or -1), out


def _build(src: Path) -> tuple[Path, str]:
    """参考实现那条路：`load` -> `check` -> LLVM -> ELF。**不碰安装版那个 CLI**。

    刻意绕开 `loment build` 是有理由的：安装版的自举驱动**收不了**
    `choose write grammar natural`（`docs/188` §7.2 那条"别的写法那一半阻塞在翻译器的
    Loment 孪生上"）。它拒得挺清楚（会说"请用参考实现编译"），但那是**另一条判据**的
    对象 —— 这一门现在验的是**前端翻出来的东西对不对**，所以走参考实现。
    """
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    out = Path(tempfile.gettempdir()) / "nltrans_test" / (src.stem + ".elf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return out, _no_ws(blob)


def _no_ws(blob: bytes) -> str:
    """ELF 里那一串可读的字符串（只在出错时给人看，判据不依赖它）。"""
    return "".join(chr(b) if 32 <= b < 127 else " " for b in blob[-512:])


# ---------------------------------------------------------------- 判据

@test
def test_the_natural_program_runs_to_the_derived_number():
    """**自然语言写法 -> Loment -> 真编真跑**，跑出的数 == 独立推出来的 47。

    这一条里有 `sum_to` / `gcd` / `label` / `tally` 四个函数、`for` 与 `while`
    两种循环、`when`/`otherwise`、比较、取模、整除 —— 任一处翻错都会把 47 改掉。
    另外它还真的从标准输出念了一行（`sample: 47 painted 42`），所以 `say` 那两句
    也是被验过的，不是"编过了就算"。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF")
        return
    exe, dump = _build(EX / "Sample.nl")
    rc, out = _run_elf(exe)
    assert rc == WANT_RC, (
        f"跑出来是 {rc}，推出来的期望值是 {WANT_RC}。" + dump)
    assert out == "sample: 47 painted 42\n", f"标准输出不对: {out!r}" + dump
    print(f"      自然语言写法 -> {rc}，标准输出 {out!r}（与推导一致）")


@test
def test_the_loment_twin_runs_to_the_same_number_by_itself():
    """**孪生那一份自己也能跑** —— 否则下一条"逐字节相等"就退化成"比两份文本"。

    这一条刻意**不经过任何翻译器**：`Sample.lomt` 就是一份普通 Loment 源。
    它跑出 47，下一条比的才是"两种拼法给出同一串字节"。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF")
        return
    exe, dump = _build(EX / "Sample.lomt")
    rc, out = _run_elf(exe)
    assert rc == WANT_RC, f"孪生那份跑出来是 {rc}，期望 {WANT_RC}。" + dump
    assert out == "sample: 47 painted 42\n", f"标准输出不对: {out!r}" + dump
    print(f"      标准 Loment 写法 -> {rc}（同一个程序，同一个数）")


@test
def test_two_spellings_one_program_translate_the_same():
    """**同一个程序、两种拼法，出来的是同一串字节**（`docs/188` §7.1.1 的原话）。

    比的是 `front_door(Sample.nl)` 的产物与 `Sample.lomt` 的**原始字节** ——
    差一个空格就说明有一条翻译规则顺手改了别的东西。

    `Sample.lomt` 头上那三行注释**是产物的一部分**（`lomt_from.emit_lomt` 生成头），
    所以它们在那份文件里；这不是"测试写死了实现"，而是这一条判据的**对象**就是
    "前端交给编译器的那串字节"。谁改了生成头，这条就红。
    """
    got = potato_from.front_door(EX / "Sample.nl").source
    want = (EX / "Sample.lomt").read_bytes().decode("utf-8")
    if got != want:
        g, w = got.splitlines(), want.splitlines()
        first = next((i for i, (a, b) in enumerate(zip(g, w)) if a != b), min(len(g), len(w)))
        raise AssertionError(
            f"两种拼法翻出来的 Loment 不同（第 {first + 1} 行起）\n"
            f"  自然语言那一侧: {g[first] if first < len(g) else '<没有这一行>'!r}\n"
            f"  Loment 那一侧  : {w[first] if first < len(w) else '<没有这一行>'!r}\n"
            f"  行数 {len(g)} vs {len(w)}")
    print(f"      两种拼法 -> 逐字节同一份 Loment（{len(want.encode())} 字节）")


@test
def test_the_front_door_reads_a_lomt_that_declares_the_grammar():
    """**`choose write grammar natural` 那条路是通的**（`docs/188` §7）。

    这一条与上面那条问的**不是同一件事**：上面是"按后缀 `.nl` 认出来"，这一条是
    "一份 `.lomt` 里声明了自己用哪种写法"。两者的入口不同（`resolve_lang` 与
    `front_door` 的声明那一支），一条判据盖不住两个入口 —— `docs/182` §1.9 的老账。
    """
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "declared.lomt"
        p.write_text("choose write grammar natural\n"
                     + (EX / "Sample.nl").read_bytes().decode("utf-8"),
                     encoding="utf-8", newline="\n")
        u = potato_from.front_door(p)
        assert u.grammar == "natural" and u.translated, (u.grammar, u.translated)
        # 声明那一行**不属于那份源**，所以产物里不许留下它的影子
        assert "choose write grammar" not in u.source, u.source[:200]
        # 而且产物与 `.nl` 那条路**是同一串字节** —— 两个入口不许给出两样东西
        assert u.source == potato_from.front_door(EX / "Sample.nl").source
    print("      声明入口：`choose write grammar natural` 与后缀入口给出同一串字节")


@test
def test_three_spellings_of_the_name_all_mean_natural():
    """`nl` / `natural` / `lument` 三种拼法都收，**进对象的只有一个规范名**。

    `docs/188` §1.2：源侧宽松、对象侧只许一个拼法，否则同一份源出两串字节。
    规范名选 `natural` 而不是 `lument` 是有理由的（`lument` 与 `loment` 只差一个字母，
    而 `grammar loment` 是合法的）—— 这条判据把"三个拼法同一个规范名"钉住。
    """
    for word in ("nl", "natural", "lument"):
        assert potato_from.GRAMMAR_ALIASES[word] == "natural", word
    assert "natural" in __import__("potato").GRAMMARS
    with tempfile.TemporaryDirectory() as t:
        for word in ("nl", "lument"):
            p = Path(t) / f"{word}.lomt"
            p.write_text(f"choose write grammar {word}\n"
                         + (EX / "Sample.nl").read_bytes().decode("utf-8"),
                         encoding="utf-8", newline="\n")
            assert potato_from.front_door(p).source == \
                potato_from.front_door(EX / "Sample.nl").source, word
    print("      `nl` / `natural` / `lument` -> 规范名 `natural`，产物一致")


@test
def test_the_verbs_land_on_the_builtins_they_name():
    """**`say` / `talk` / `paint` 各自落在哪个内建上** —— 用户点名的那三个动词。

    这一条盯的是"降级"那一层：`talk` 与 `paint` 会**补转换**（`as u64` / `as u32` /
    `as u8`），因为 `syscall4` 与 `store8` 的形参就是那些宽度 —— 作者那一句里
    没有写宽度这回事，所以补它是**翻译的一部分**，不是让作者去补。
    """
    t = nltrans.translate(
        "to f with p as a buffer and n as a whole number\n"
        "    say \"hi\"\n"
        "    say the number n\n"
        "    talk to the machine 60 with n, 0, 0\n"
        "    paint 7 at n in p\n"
        "end\n")
    assert 'syscall4(1, 1, str_ptr("hi") as u64, str_len("hi") as u64);' in t, t
    assert "nl_write_num(1, n as i64);" in t, t
    assert "syscall4(60 as u64, n as u64, 0 as u64, 0 as u64);" in t, t
    assert "store8(p, n as u32, 7 as u8);" in t, t
    # `say the number` 那个辅助函数**只在用到时才发** —— 没用到就不该进产物
    assert "fn nl_write_num" in t
    without = nltrans.translate(
        "to g with s as text\n    say s\nend\n")
    assert "nl_write_num" not in without, without
    print("      say -> write / say the number -> nl_write_num / talk -> syscall4 / "
          "paint -> store8（各补各的宽度）")


@test
def test_each_operator_has_exactly_one_spelling():
    """**一个运算符只有一个写法**（用户 2026-09-22 那条"过度复杂"的头一条）。

    原来 15 个运算符**每个都有两种写法**（`plus` 与 `+`、`is above` 与 `>` …）——
    学一遍不够、得学两遍，而两遍说的是同一件事。现在只留**词形**；
    符号那一族只剩位运算（`&` `|` `^`：它们没有自然语言说法）。

    这一条钉的比"两种写法翻出来一样"更强：**不是"也收"，是"只有一种"**。
    """
    t = nltrans.translate("""
to f with a as a whole number and b as a whole number giving a whole number
    when a is above b and a is not 0
        give back a times 2 plus b minus 1
    end
    give back a modulo b over 2
end
""")
    assert "((a > b) && (a != 0))" in t, t
    assert "(((a * 2) + b) - 1)" in t, t
    # 位运算那一族**只有符号**（没有词形），所以它们照旧收
    bits = nltrans.translate("""
to g with a as a whole number and b as a whole number giving a whole number
    give back a & b | (a ^ b)
end
""")
    assert "((a & b) | (a ^ b))" in bits, bits
    # **符号形的算术/比较不再收** —— 每一格都要报，不能悄悄收下
    for bad in ("a + b", "a > b", "a == b", "a && b", "a << b", "a - b", "a * b"):
        try:
            nltrans.translate(f"""
to h with a as a whole number and b as a whole number giving a whole number
    give back {bad}
end
""")
        except (nltrans.Unsupported, nltrans.NaturalError):
            pass
        else:
            raise AssertionError(f"`{bad}` 被收下了 —— 一个运算符只许一个写法")
    print("      15 个运算符各只留词形；符号形点名拒；位运算仍只有符号")



@test
def test_line_number_of_a_defect_is_the_file_line_not_the_body_line():
    """**正文拼起来之后，行号仍然指回原文件**（`docs/188` §7.2 那条老账）。

    `lomt_from` 是把各函数**正文拼成一份临时源**再交给翻译器的（`_join_bodies`），
    所以翻译器数出来的行号天然是"按正文"算的。修法是拿 `body_line` 垫空行 ——
    这条判据钉的就是那一垫。

    **夹具自证能分辨两种读法**：缺陷在文件第 6 行，而按"只算这一个函数体"是第 4 行。
    两个数**必须不同**，否则这条判据测的是一个"两种读法都对"的输入。
    """
    src = ("program p\n"                            # 1
           "\n"                                     # 2
           "to f with n as a whole number\n"        # 3  <- 函数体从这里开始
           "    let x be n\n"                       # 4  (= 体内第 2 行)
           "    let z be n\n"                       # 5
           "    set y to n\n"                       # 6  <- 缺陷在这里 (= 体内第 4 行)
           "    give back x plus z\n"               # 7
           "end\n")                                 # 8
    want_line = 6
    body_line = 4
    assert want_line != body_line, "夹具分辨不了两种读法"
    assert src.splitlines()[want_line - 1].strip().startswith("set y"), "夹具自己错了"

    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "defect.nl"
        p.write_text(src, encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(p)
        except Exception as e:  # noqa: BLE001 - 具体类型下面再断
            msg = str(e)
        else:
            raise AssertionError("这一份本该被拒")
    assert "set y" in msg or "没有声明过" in msg, f"拒得不是那一处: {msg}"
    assert f"第 {want_line} 行" in msg, (
        f"报的是**正文里**的行号，不是文件里的（`body_line` 那一步漏了？）: {msg}")
    assert f"第 {body_line} 行" not in msg, f"报成了按函数体算的那一行: {msg}"
    print(f"      缺陷在文件第 {want_line} 行，报出来的也是 {want_line} "
          f"（按函数体算是 {body_line}，两个数不同）")


@test
def test_content_detection_needs_both_signals():
    """**内容识别要两条一起**（`program <名字>` 整行 + `give back`）—— 认不出比认错好。

    这一门的句子全是日常英文词，单看某一个词会与别的语言的注释/标识符撞上。
    所以判据是"两条齐了才认"，代价是"只有一条时不认"，而那条代价是**故意的**：
    认错的症状是"拿一份 C 按自然语言去读"，比认不出坏得多（`docs/188` §2 那条兜底
    从"猜"变"拒绝"）。
    """
    both = "program demo\n\nto f\n    give back 1\nend\n"
    lang, why = potato_from.detect_lang(both)
    assert lang == "natural", (lang, why)
    # 只有 `program` 那一行：**不认**（这多半是别的什么语言里一个叫 program 的东西）
    only_one = "program demo\n\nint f(void) { return 1; }\n"
    lang2, _ = potato_from.detect_lang(only_one)
    assert lang2 != "natural", f"只有一条信号就认了: {lang2}"
    print("      两条齐了才认自然语言写法；只有一条不认")


@test
def test_a_dot_nl_file_is_read_by_extension():
    """后缀那一格也是**登记的**（`EXT[".nl"] == "natural"`），与 `LANGS` 对得上。

    `docs/188` §7.1 那张登记表里"不加会怎样"最隐晦的一格就是这张表 ——
    它是 `lomt_from._TOOLS` 与报错器那几张卡共同的对齐点。
    """
    assert potato_from.EXT[".nl"] == "natural"
    assert "natural" in potato_from.LANGS
    import lomt_from
    mod, errs, tool, hint = lomt_from._TOOLS["natural"]
    assert mod == "nltrans" and tool == "tools/nltrans.py"
    assert "NaturalError" in errs
    import loment_diag
    card = loment_diag.LANG_CARDS["natural"]
    assert card.exts == (".nl",) and card.key == "natural"
    assert "natural" in loment_diag.LANG_EDGE_EN
    assert "natural" in loment_diag.LANG_ABI_EN
    print("      后缀表 / LANGS / _TOOLS / 语言卡 四处对齐")


@test
def test_the_surface_program_runs_to_the_derived_numbers():
    """**完整面的那一份**（`Surface.nl`）：真编真跑，两个数都对上。

    它就是"这一门到底能写多少东西"的答卷 —— 一篇里同时用到了结构体、枚举、
    trait/impl（`ask p for size`）、泛型、`match` 与 `if let`、能力域与守卫、
    数组与切片、`use` 引进来的库函数、`only here` 的私有常量、`choose no_std`。

    期望值是**推出来的**（见 `WANT_SURFACE_OUT` / `WANT_SURFACE_RC`）。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF")
        return
    exe, dump = _build(EX / "Surface.nl")
    rc, out = _run_elf(exe)
    assert out == WANT_SURFACE_OUT, f"标准输出不对: {out!r}" + dump
    assert rc == WANT_SURFACE_RC, f"退出码 {rc} != 推出的 {WANT_SURFACE_RC}" + dump
    print(f"      Surface.nl -> {rc}，标准输出 {out!r}（与推导一致）")


@test
def test_the_surface_twin_is_byte_identical_and_runs_by_itself():
    """`Surface.lomt` 是 `Surface.nl` 的**同源 Loment 写法**，两条一起钉。

    ① 逐字节相同（`docs/188` §7.1.1 那条原话）；② 它**自己**也跑到同一对数字 ——
    没有 ② 的话，① 退化成"比两份文本"。
    """
    got = potato_from.front_door(EX / "Surface.nl").source
    want = (EX / "Surface.lomt").read_bytes().decode("utf-8")
    assert got == want, "两种拼法翻出来的 Loment 不同（`docs/188` §7.1.1）"
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF（逐字节那半已过）")
        return
    exe, dump = _build(EX / "Surface.lomt")
    rc, out = _run_elf(exe)
    assert (rc, out) == (WANT_SURFACE_RC, WANT_SURFACE_OUT), (rc, out) + dump
    print(f"      逐字节相同（{len(want.encode())} 字节），且孪生自己跑到 {rc}")


@test
def test_every_declaration_shape_lowers_onto_the_right_loment():
    """**声明的自然说法 -> Loment 构造**：一条一格，逐字比对。

    这一条钉的是"**拼法与形状**"那一层 —— 每一句自然语言落到哪个 Loment 写法上。
    它比"跑出正确的数"更细：数对得上容许很多拼法漂移，而这里比的是**同一串字节**。
    """
    t = nltrans.translate(
        "program p\n"
        "\n"
        "the standard library is not available\n"
        "\n"
        "use json\n"
        "\n"
        "remember K as 3\n"
        "remember HIDDEN as 9, only here\n"
        "\n"
        "a Pair for any T has left as a T and right as a T\n"
        "a Kind is either Small or Big carrying a count\n"
        "a Sizer can size giving a count\n"
        "a Pair can be a Sizer\n"
        "    to size giving a count\n"
        "        give back 1\n"
        "    end\n"
        "end\n"
        "\n"
        "a disk space called slots covers 0 to 4, and it can be taken back\n"
        "leave out \"the network\"\n"
        "someone else wrote read_at with fd as a count giving a whole number\n")
    for want in ("choose no_std",
                 "use json",
                 "const HIDDEN: i64 = 9;",
                 "pub struct Pair<T> {\n    left: T,\n    right: T,\n}",
                 "pub enum Kind {\n    Small,\n    Big(u32),\n}",
                 "pub trait Sizer {\n    fn size(self) -> u32;\n}",
                 "impl Sizer for Pair {\n    fn size(self) -> u32 {\n"
                 "        return 1;\n    }\n}"):
        assert want in t, f"少了这一段：{want!r}\n{t}"
    print("      choose / use / 结构体 / 枚举 / trait / impl 逐字对上")


@test
def test_match_and_if_let_are_told_apart_by_the_number_of_arms():
    """**看形状那几句 → `match` 还是 `if let`**，由**臂的条数**决定（`docs/197` §2）。

    一条臂、没有兜底 ⇒ `if let`；两条以上（或带 `when anything else`）⇒ `match`。
    合并的判据是**主语那串记号逐字相同** —— 所以两条主语不同的形状句**不该**并起来。
    """
    # 两条臂 -> match（且臂之间**不夹**别的东西，顺序照写）
    m = nltrans.translate(
        "to f with k as a Kind giving a whole number\n"
        "    when k looks like a Kind that is Big carrying w\n"
        "        give back w\n"
        "    end\n"
        "    when k looks like a Kind that is Small\n"
        "        give back 0\n"
        "    end\n"
        "end\n"
        "a Kind is either Small or Big carrying a whole number\n")
    assert "match k {" in m and "Kind::Big(w) => {" in m and "Kind::Small => {" in m, m
    assert "if let" not in m, m
    # 一条臂 + 兜底 -> match，且兜底是 `_`
    m2 = nltrans.translate(
        "to f with k as a Kind giving a whole number\n"
        "    when k looks like a Kind that is Big carrying w\n"
        "        give back w\n"
        "    end\n"
        "    when anything else\n"
        "        give back 0\n"
        "    end\n"
        "end\n"
        "a Kind is either Small or Big carrying a whole number\n")
    assert "match k {" in m2 and "_ => {" in m2, m2
    # 一条臂、没有兜底 -> if let
    m3 = nltrans.translate(
        "to f with k as a Kind giving a whole number\n"
        "    when k looks like a Kind that is Big carrying w\n"
        "        give back w\n"
        "    end\n"
        "    give back 0\n"
        "end\n"
        "a Kind is either Small or Big carrying a whole number\n")
    assert "if let Kind::Big(w) = k {" in m3, m3
    assert "match" not in m3, m3
    print("      一条臂 -> if let；两条/带兜底 -> match")


@test
def test_the_values_and_the_containers_lower_onto_their_forms():
    """**值那一半**：结构体字面量 / 枚举构造 / 取东西 / 切片 / 长度 / 方法。

    "**取一个东西只有一种形状**"（`the <什么> of <东西>`）在这一条里逐格钉住：
    字段、方法、下标、长度、切片**五格同一个壳**；数组那一族（字面量、切片、定长）
    照 Loment 写（`[1, 2, 3]` / `[i64]` / `[i64; 3]`），所以那条规则没有例外。
    """
    t = nltrans.translate("""
a Point has x as a whole number and y as a whole number
a Kind is either Small or Big carrying a whole number
a Sizer can size giving a whole number
a Point can be a Sizer
    to size giving a whole number
        give back the x of self
    end
end

to f with xs as [i64] and p as a Point giving a whole number
    let a be a Point with x as 1 and y as 2
    let b be a Kind that is Big carrying 3
    let c be a Kind that is Small
    let d be [1, 2, 3]
    let e be the item 1 of xs
    let g be the length of xs
    let h be the size of p
    let i be the run of xs
    let j be the changeable run of xs
    give back the x of a plus e plus g plus h
end
""")
    for want in ("let a: Point = Point { x: 1, y: 2 };",
                 "let b: Kind = Kind::Big(3);",
                 "let c: Kind = Kind::Small;",
                 "let d: [i64; 3] = [1, 2, 3];",
                 "let e: i64 = (xs[1]);",
                 "let g: u32 = slice_len(xs);",
                 "let h: i64 = p.size();",
                 "let i: [i64] = (&xs);",
                 "let j: [i64] = (&mut xs);",
                 "return ((((a.x) + e) + g) + h);"):
        assert want in t, f"少了这一段：{want!r} —— 全文：{t}"
    print("      结构体 / 枚举 / 数组 / 取东西五格同壳 / 切片 逐字对上")


@test
def test_a_call_with_no_arguments_is_just_its_name():
    """**没有实参的调用就写名字本身**（`say the number run`）。

    分辨靠**声明表**（两遍读出来的）：名字是个函数、又不是局部量 ⇒ 它是调用。
    这一条同时钉住"局部量优先"，否则一个与函数同名的变量会被悄悄改成调用。
    """
    t = nltrans.translate(
        "to called giving a whole number\n"
        "    give back 42\n"
        "end\n"
        "to f giving a whole number\n"
        "    let other be called\n"
        "    give back other plus called\n"
        "end\n"
        "to g giving a whole number\n"
        "    let called be 7\n"
        "    give back called\n"
        "end\n")
    # `called` 是函数 ⇒ 两处都是**调用**（`let` 的类型也是从声明表查出来的）
    assert "let other: i64 = called();" in t, t
    assert "return (other + called());" in t, t
    # 而在 `g` 里 `called` 被一个局部量遮住 ⇒ 那两句是**变量**（局部量优先）
    assert "let called: i64 = 7;" in t, t
    assert "return called;" in t, t
    print("      无参调用写名字；与局部量同名时以局部量为准")

@test
def test_out_of_subset_is_loud_and_points_at_the_line():
    """**读得通但这一版不翻**的，一条都不许沉默过去，而且**行号要指回原文件**。

    最后两例是 `docs/188` §7.2 那条老账：正文是**拼起来**再交给翻译器的，拼接不带
    行偏移的话，报的行号是"按函数体"算的 —— 用户在源码里按它**找不到东西**
    （实测那边报的是第 2 行，而那一句在文件第 3 行）。
    """
    cases = [
        # `use` 这一版收了；顶层不认识的句子照旧要报
        ("program p\n\nlet x be 1\n", "只能写在函数体里"),
        ("program p\n\nthe switches come from extra\n", "顶层不认识的句子"),
        ("program p\n\nto f with a as a myst\n    give back a\nend\n", "类型短语"),
        # **"调用了不存在的函数"这一格故意不查**（见 `docs/197` §5）：
        # `use "别的.lomt"` 引进来的函数在这段正文里看不见，查了就是**假红**。
        # 真判据在编译器那一侧（E2「未定义的函数」），所以这一份**一个字都不报**。
        ("program p\n\nto f giving a whole number\n"
         "    give back nope of 1\nend\n", None),
        ("program p\n\nto f\n    set x to 1\nend\n", "没有声明过"),
        ("program p\n\nto f\n    talk to the machine 60 with 1, 2\nend\n", "三个"),
        ("program p\n\nto f\n    let x be a plus b\nend\n", "说不出"),
        # 调用一个 `giving` 没写的函数：**读得通、却不是一个值** —— 拦在这里，
        # 不让它变成"编译到一半报类型 `()`"那种隔着一层的错
        ("program p\n\nto g\nend\n\nto f\n    let x be g\nend\n", "不返回值"),
        ("program p\n\nto f\n    when 1\nend\n", "end"),
        ("program p\n\nto f\n    give back 1\n", "没有 `end`"),
        ("program p\n\nto f\n    match x\nend\n", "不认识的句子"),
        ("program p\n\nto f\n    to g\n    end\nend\n", "顶层"),
    ]
    for src, want in cases:
        try:
            nltrans.translate(src)
        except (nltrans.Unsupported, nltrans.NaturalError) as e:
            assert want is not None, f"这一份本该**收下**，却被拒了：{e}"
            assert want in str(e), f"要点名 `{want}`：{e}"
        else:
            assert want is None, (
                f"{src!r} 在子集外，却一个字都没报（该点 `{want}`）")
    print(f"      {len(cases)} 档子集外各报各的，都点到了点子上")

@test
def test_optional_and_result_are_types_values_and_question_mark_only():
    """**`Option` / `Result` 收三样，不收第四样** —— 第四样是**语言层面**的边界。

    收：类型（`Option<i64>` / `Result<i64, i64>`）、构造、`?`（`unless it failed`）。
    不收：**形状**。判据拿写出来的名字与**单态化名**比（`Option::Some` 对
    `Option_u32::Some`），而后者是编译器的内部拼法 —— 源里根本写不出来（实测）。
    """
    t = nltrans.translate(
        "to maybe_one with x as a whole number giving Option<i64>\n"
        "    when x is above 10\n"
        "        give back nothing to carry\n"
        "    end\n"
        "    give back something carrying x plus 1\n"
        "end\n"
        "\n"
        "to find with x as a whole number "
        "giving Result<i64, i64>\n"
        "    when x is above 10\n"
        "        give back a failure carrying x\n"
        "    end\n"
        "    give back a success carrying x plus 1\n"
        "end\n"
        "\n"
        "to prop with x as a whole number "
        "giving Result<i64, i64>\n"
        "    let y be find of x unless it failed\n"
        "    give back a success carrying y\n"
        "end\n")
    assert "pub fn maybe_one(x: i64) -> Option<i64> {" in t, t
    assert "return Option::None;" in t, t
    assert "return Option::Some((x + 1));" in t, t
    assert "pub fn find(x: i64) -> Result<i64, i64> {" in t, t
    assert "return Result::Err(x);" in t, t
    assert "pub fn prop(x: i64) -> Result<i64, i64> {" in t, t
    # `?` 拆开之后 `y` 是 **i64**（不是 `Result<…>`），而 `?` 只写在 let 的右半边
    assert "let y: i64 = find(x)?;" in t, t
    assert "return Result::Ok(y);" in t, t
    # 形状那一格**响亮地拒**，并且说清是语言层面的
    try:
        nltrans.translate("to f with x as Option<i64> giving a whole number\n"
                          "    when x looks like a Option that is Some carrying v\n"
                          "        give back v\n"
                          "    end\n"
                          "    give back 0\n"
                          "end\n")
    except (nltrans.Unsupported, nltrans.NaturalError) as e:
        assert "单态化" in str(e) or "Option" in str(e), e
    else:
        raise AssertionError("`Option` 的形状这一版收不了，却一个字都没报")
    print("      Option/Result 的类型、构造、`?` 都收；形状响亮地拒（语言层面）")


@test
def test_the_generic_return_type_is_not_looked_up():
    """**泛型函数的返回类型查不出来** —— 所以 `let m be largest of a, b` 要写 `as`。

    `largest<T>(a: T, b: T) -> T` 的 `T` 不是一个类型，它是"调用点当场定的那个"。
    放进声明表的话会发出 `let m: T = …;`，那是**一份编不过的源**；所以它**不进表**，
    查不到就让作者写 `as`（与"查不到就报错"同一条纪律）。
    """
    try:
        nltrans.translate(
            "to largest for any T with a as a T and b as a T giving a T\n"
            "    give back a\n"
            "end\n"
            "to f giving a whole number\n"
            "    let m be largest of 1, 2\n"
            "    give back m\n"
            "end\n")
    except (nltrans.Unsupported, nltrans.NaturalError) as e:
        assert "说不出" in str(e), e
    else:
        raise AssertionError("泛型返回类型被当成具体类型查出来了 —— 那会发出一份编不过的源")
    # 补一句 `as`（并且给调用点一份**声明过类型**的实参）就成立
    ok = nltrans.translate(
        "to largest for any T with a as a T and b as a T giving a T\n"
        "    give back a\n"
        "end\n"
        "to f giving a whole number\n"
        "    let x be 1\n"
        "    let y be 2\n"
        "    let m be (largest of x, y) as a whole number\n"
        "    give back m\n"
        "end\n")
    assert "pub fn largest<T>(a: T, b: T) -> T {" in ok, ok
    assert "let m: i64 = largest(x, y);" in ok, ok
    print("      泛型返回不进声明表；补 `as` 之后照常翻出来")


def main() -> int:
    return loment_suite.run(TESTS, "loment_nltrans_test")


if __name__ == "__main__":
    sys.exit(main())
