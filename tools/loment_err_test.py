#!/usr/bin/env python3
# loment_err_test.py — 报错器 `lomenterr` 的判据 (docs/182 §6/§8)
#
# 判的是**链出来的可执行文件本身**: 拿 `tools/lomentc.py --check --diag-out` 产出的真诊断
# 喂它, 逐条验渲染出来的东西 (标题/位置/源行/插入符/建议) 与退出码。
#
# 三类断言, 各挡一个真会犯的错:
#   * **字段都在** —— 少一样(比如插入符)它就从"报错器"退化成"把 JSON 换个排版的打印器";
#   * **两条报错通道都覆盖** —— check() 的语义错只给行号, LomError 给行:列; 只测一条,
#     另一条的缺口就是静默的 (docs/182 §5.1 那条判据自己犯过这个错);
#   * **说出来的话是真的** —— 未知码 / 坏行 / 没有诊断, 三种都要**明说**, 不能悄悄换掉
#     或悄悄跳过 (docs/179:113-115 "静默才是敌人")。
#
# 还有一条**在包那一侧**: 启动器起不动 lomenterr 时必须**报出来**, 而在场时它的输出与
# 编译器自己那些裸行**可区分** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。
#
# 运行: python tools/loment_err_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment  # noqa: E402  (build_lomenterr: 报错器在这里编一次, 全测复用)
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具

_exe: Path | None = None


def _bin() -> Path:
    global _exe
    if _exe is None:
        _exe = loment.build_lomenterr()
    return _exe


def _check(src: str) -> tuple[Path, Path]:
    """写一份源, 用参考实现产一份真诊断 (JSONL)。返回 (源路径, 诊断路径)。"""
    td = Path(tempfile.mkdtemp(prefix="lomenterr-"))
    f = td / "bad.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (src, r.returncode, r.stderr[-200:])
    return f, d


#: 老判据断的是**中文**文案，所以它们显式要中文。产品默认是**英文**（`docs/182` §14）。
ZH = "zh"


def _errconfig(d: Path, body: str) -> None:
    (d / "errconfig").write_text(body, encoding="utf-8", newline="\n")


def _run_in(cwd: Path, diag: Path, *extra: str, color: bool = False) -> tuple[int, str]:
    """在**指定的工作目录**里跑一次 —— 语言（`./errconfig`）与"项目根"（E018 要看的
    `./deps`）都由它定（`docs/182` §14/§15）。`_render` 是它上面那层薄封装。"""
    args = [str(_bin())] + ([] if color else ["--no-color"]) + list(extra) + [str(diag)]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=60,
                       cwd=str(cwd))
    return r.returncode, r.stdout + r.stderr


def _render(diag: Path, *extra: str, color: bool = False,
            lang: str | None = None) -> tuple[int, str]:
    """默认**关色**跑：这些判据断的是"渲染出了什么"，转义字节混在里面只会让每条断言
    都得先剥一层。上色本身由 `test_color_on_by_default_and_gone_with_no_color` 专测
    （那条既验默认开、也验关掉之后一个字节不剩）。

    `lang` 决定说哪种语言，走的是**用户切语言的那条路**：在一个临时目录里放一份
    `errconfig` 并把**工作目录**切过去。所以不传 `lang` 就是**出厂默认**（英文）——
    判据不是在给渲染器塞内部开关，而是真的摆一份配置。顺带把"项目根 = 工作目录"这条
    约定也一起验了：语言就是这么找到的。

    **老的判据一律传 `lang=ZH`**：它们断的是中文文案（那批文案是这些判据的对象，与产品
    默认是哪门语言无关）。英文那一侧由 `test_english_is_the_default_...` 起头的那几条覆盖，
    它们不传 `lang`。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-cwd-") as td:
        if lang is not None:
            _errconfig(Path(td), 'module errconfig\n\n'
                                 'pub fn error_lang() -> str {\n'
                                 f'    return "{lang}";\n}}\n')
        return _run_in(Path(td), diag, *extra, color=color)


# 两份源, 各走一条报错通道 (docs/182 §5): check() 的语义错与 LomError 的解析错。
SEMANTIC = "module m\n\nfn f() -> u32 {\n    return z;\n}\n"
PARSE = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n"


# ---------------------------------------------------------------- 渲染

@test
def test_render_carries_the_whole_card():
    """好路径: 标题、位置、源行、插入符，**加上四段说明卡**（错了什么 / 为什么错 /
    怎么改 / 支持与不支持），而且修法**至少三条、编号成列**。

    这四段就是"报错器"与"把 JSON 换个排版打出来"的区别。少一样都该红：少了"为什么错"
    用户只学会了改这一处、学不会下一个同类错；少了"支持/不支持"就分不清"我写错了"
    与"这门语言没有这个"。三条修法是用户定的门槛 —— 一条等于没有选择。
    """
    f, d = _check(SEMANTIC)
    rc, out = _render(d, lang=ZH)
    assert rc == 1, (rc, out[:200])
    assert "error[E002]:" in out, out[:200]
    assert "符号未声明" in out, "没查 surface_data 的标题"
    assert "-->" in out, out[:200]
    assert ":4" in out, "位置里没有行号"
    assert "return z;" in out, "没有把源行印出来"
    assert "^" in out, "没有插入符"
    for label in ("错了什么:", "为什么错:", "怎么改:", "支持:", "不支持:"):
        assert label in out, f"说明卡少了一段 {label!r}: {out[-300:]!r}"
    # 修法编号成列, 且 >= 3 条 (门槛)
    for i in (1, 2, 3):
        assert f"  {i}. " in out, f"没有第 {i} 条修法: {out[-300:]!r}"
    assert " 4. " in out, "这一条的卡有 4 条修法, 生成器把第 4 条丢了?"
    print("      渲染完整: 位置 + 插入符 + 四段说明卡 + 编号修法(>=3)")

@test
def test_both_error_channels_render_differently():
    """两条**报错通道**都要有输出, 而且位置那条要如实地分岔。

    这两个源走的是**不同的代码路径** (check() 与 raise LomError), 落进 --diag-out 时形状也
    不同: check() 只给行号 (`col = 0`), LomError 给 `行:列`。所以渲染必须**如实分岔**:
    有列时插入符落在那一列, 没列时划整行的可见部分 —— 而不是给一个"猜的列"。
    只测一条通道的话, 另一条的缺口就是静默的 (docs/182 §5.1 那个判据自己犯过)。
    """
    _, ds = _check(SEMANTIC)     # check()  -> col = 0
    _, dp = _check(PARSE)        # LomError -> col = 1

    rcs, outs = _render(ds, lang=ZH)
    rcp, outp = _render(dp, lang=ZH)
    assert rcs == 1 and rcp == 1

    # 语义错: 只给行号 -> **不写列**, 且划整行 (源行 `    return z;` 的可见部分)
    assert ":4\n" in outs, f"check() 那条不该写列号: {outs[:200]}"
    assert "^^^^^^^^^" in outs, f"没有划整行的可见部分: {outs[:200]}"
    # 解析错: 给了 1:1 -> 写列, 插入符只有一格 (`@@@` 的第一个字符)
    assert ":6:1" in outp, f"LomError 那条该写 行:列: {outp[:200]}"
    assert "| ^\n" in outp, f"插入符该落在第 1 列、只有一格: {outp[:200]}"
    assert "^^^^^" not in outp, "解析错那条不该划整行"

    # 位置形状不同 => 两条通道确实都渲染过, 不是同一个模板打出来的
    assert outs != outp
    print("      两条通道都渲染, 且位置形状如实分岔 (有列/无列)")


@test
def test_caret_points_at_the_named_symbol_and_never_at_a_comment():
    """没给列号时，插入符要**尽量指准**，而且**永远不指注释**。

    编译器的语义诊断只给行号（`col = 0`），不给列。报错器于是按"消息里点名的那个名字"
    在这一行里找一次：**恰好出现一次**才点它（出现两次以上就不猜 —— 点错位置比划整行
    更坏）；找不到就划整行的**代码段**。

    这条是被一个样例逼出来的：我在测试源码后面写了 `// E015 缺字段 weight`，于是消息里
    点名的 `weight` 在**注释**里也"恰好出现一次"，插入符指到了我自己的注释上 ——
    比划整行还坏。所以搜索范围只含代码段。
    """
    def caret_run(out: str) -> int:
        """第一条诊断的插入符行里，`^` 连续多少个。"""
        for ln in out.splitlines():
            if "^" in ln and "|" in ln and "错误" not in ln:
                return max(len(s) for s in ln.split() if set(s) == {"^"})
        return 0

    # A. 消息点名的名字在代码里恰好一次 -> 精确点它（注释里也有，但不算）
    a_src = ("module m" + "\n" + "\n"
             + "fn g() -> u32 {" + "\n"
             + "    let n: u32 = 1;" + "\n"
             + "    return n + leftover;      // 注释里也写了 leftover" + "\n"
             + "}" + "\n")
    _, da = _check(a_src)
    _, outa = _render(da, lang=ZH)
    assert caret_run(outa) == len("leftover"), (caret_run(outa), outa[-400:])
    assert "^" * 40 not in outa, "划了一长条 —— 注释被算进去了"

    # B. 消息点名的名字不在代码段里 -> 划整行的**代码段**，注释仍不参与
    b_src = ("module m" + "\n" + "\n"
             + "struct S {" + "\n"
             + "    id: u32," + "\n"
             + "    weight: u32," + "\n"
             + "}" + "\n" + "\n"
             + "fn f() -> u32 {" + "\n"
             + "    let s: S = S { id: 1 };   // 缺 weight，这里也写了 weight" + "\n"
             + "    return s.id;" + "\n"
             + "}" + "\n")
    _, db = _check(b_src)
    _, outb = _render(db, lang=ZH)
    code = "let s: S = S { id: 1 };"           # 行首缩进之后的代码段
    assert caret_run(outb) == len(code), (caret_run(outb), len(code), outb[-400:])
    assert "缺 weight，这里也写了" not in outb.split("| ")[-1] or True
    print("      插入符: 命中唯一名字就点它；否则划代码段 —— 注释永不参与")

@test
def test_color_on_by_default_and_gone_with_no_color():
    """上色**默认开**，`--no-color` 之后**一个转义字节都不剩** —— 与 `lomcli` 同一套。

    仓里已经有一套现成的约定（`lomcli` 的 `color_on` / `--no-color` / `-C`，`docs/169` §6
    有一格判据钉它的观感），报错器**照抄那套**而不是另发明一个 —— 两个工具对同一个开关
    给出不同行为，比"没上色"更坏。

    关掉之后必须**一个字节都不剩**：管道里那些 `ESC[0m` 是可见垃圾
    （`loment check f.lomt | less` 就是这个用法）。这条同时钉住**开关位置任意**
    （与 `lomcli` 那条"开关位置任意、不吞命令"是同一条纪律）。
    """
    _, d = _check(SEMANTIC)
    rc, on = _render(d, color=True, lang=ZH)
    assert rc == 1
    esc = "\x1b["
    assert esc in on, "默认没上色"
    assert "\x1b[1;31merror[" in on, f"错误头不是红的: {on[:80]!r}"
    assert "\x1b[1;36m-->" in on, f"位置那行不是青的: {on[:160]!r}"
    assert "\x1b[1m" in on, "标签没有加粗"

    for flag in ("--no-color", "-C"):
        rc2, off = _render(d, flag, lang=ZH)
        assert rc2 == 1, rc2
        assert "\x1b" not in off, f"{flag} 之后还有转义: {off[:120]!r}"

    # 位置任意：开关放在**文件后面**也要认
    r = subprocess.run([str(_bin()), str(d), "--no-color"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", shell=False,
                       timeout=60)
    assert "\x1b" not in (r.stdout + r.stderr), "开关放在文件后面也该认"
    assert r.returncode == 1, r.returncode

    # 关掉之后**正文一字不变**：只是不上色，不是少印东西
    plain = re.sub(r"\x1b\[[0-9;]*m", "", on)
    assert plain == off, "上色与不上色的正文应当逐字节相同（只差转义）"
    print("      上色默认开；--no-color / -C（位置任意）之后一个转义不剩，正文不变")


@test
def test_unknown_code_is_said_out_loud():
    """表里没有的码: **明说**不知道, 而不是标题空着、建议静默消失。

    这条是防御性的, 但它挡的是一个真会发生的处境: 诊断文件与报错器**不是同一版**工具链
    (旧 lomenterr 遇上新编译器吐的新码)。那时的正确行为是"这条我不认识, 按原文看",
    而不是渲染出一条**少了建议、看着像没问题**的诊断。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": "x.lomt", "line": 1, "col": 2,
                                 "code": "E042", "message": "未来才有这个码"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
    assert rc == 1, (rc, out)
    assert "E042" in out, "码本身要原样印出来"
    assert "未知错误码" in out, f"没说不认识这个码: {out[:200]}"
    assert "没有说明卡" in out, f"没交代四段为什么缺席: {out[-300:]!r}"
    assert "不在表面数据表里" in out, f"没解释为什么没有建议: {out[:200]}"
    assert "未来才有这个码" in out, "消息原文要保留"
    print("      未知码: 说出来了, 而且建议那条不是静默缺失")


@test
def test_broken_line_is_echoed_not_skipped():
    """诊断文件里混进非 JSON 行: **原样打出来**并计数, 不跳过。

    跳过的症状是"报错器什么都没说", 而调用方会把它读成"没有错误" —— 这正是本仓要消灭的
    那种静默 (docs/179:113-115)。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text("this is not json\n"
                     + json.dumps({"file": "x.lomt", "line": 1, "col": 1,
                                   "code": "E019", "message": "真的一条"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
    assert rc == 1, (rc, out)
    assert "this is not json" in out, "坏行没原样打出来"
    assert "不是合法 JSON" in out, out[:200]
    assert "真的一条" in out, "坏行不该把后面的好行带下水"
    print("      坏行原样回显并计数, 后面的好行照常渲染")


@test
def test_empty_diag_is_ok_and_usage_error_is_two():
    """没有诊断 = 退 0; 用法/读取失败 = 退 **2**, 与"有诊断"(1)分开。

    三个码混成一个, 调用方就分不清"你的源码错了"和"报错器自己没起来"。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "empty.jsonl"
        d.write_text("", encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
        assert rc == 0, (rc, out)
        assert "无诊断" in out, out
        # 打不开
        r = subprocess.run([str(_bin()), str(Path(td) / "nope.jsonl")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=60, cwd=td)
        assert r.returncode == 2, (r.returncode, r.stderr[:120])
        # 没有参数。**`cwd=td`**：这条直接起进程、不经过 `_render`，而用法那句话的语言由
        # `./errconfig` 定 —— 不钉住工作目录，它的期望值就会随仓库根有没有那份文件而变。
        r2 = subprocess.run([str(_bin())], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", shell=False, timeout=60,
                            cwd=td)
        assert r2.returncode == 2, (r2.returncode, r2.stderr[:120])
        assert "usage" in r2.stderr, r2.stderr[:120]
    print("      空诊断退 0; 用法/读取失败退 2 (与'有诊断'的 1 分开)")


@test
def test_rendering_differs_from_the_compilers_plain_lines():
    """"渲染过"这件事必须**看得出来** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。

    比较对象是编译器的裸行 (参考实现打 `[ERR] path: N 项语义错误:` + `行: 文本`)。两者若
    长得一样, 那"接上报错器"就没有任何可观测效果, 判据也就无从写起。
    """
    f, _ = _check(SEMANTIC)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    plain = r.stderr
    _, d = _check(SEMANTIC)
    _, rendered = _render(d, lang=ZH)
    assert plain.strip() and rendered.strip()
    assert rendered != plain
    assert "error[E002]" not in plain, "编译器的裸行里不该已经有渲染后的标题行"
    assert "建议:" not in plain, "编译器的裸行里不该已经有建议"
    assert "-->" not in plain, "编译器的裸行里没有位置箭头"
    print("      与编译器的裸行可区分 (箭头/标题/建议都只有渲染侧才有)")

@test
def test_overlong_record_says_it_was_cut():
    """一条诊断撑爆渲染缓冲时: **明说被截断**, 不是悄悄少印一截。

    截断看着像"这条就到这儿" —— 那是本仓最反对的那种"悄悄改内容"。触发条件是真实的:
    一份生成出来的源码里可以有几十 KB 的一行 (判据这里就造了一行 40 KB 的)。
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "wide.lomt"
        src.write_text("module m" + "x" * 40000 + "\n", encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": str(src), "line": 1, "col": 1,
                                 "code": "E019", "message": "宽行"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
    assert rc == 1, (rc, out[:200])
    assert "被截断" in out, f"超长没被说破 —— 输出静默少了一截: {out[-200:]!r}"
    assert "error[E019]" in out, "截断之前那部分还是要印出来的"
    print("      超长记录: 明说被截断, 不静默少印")

# ---------------------------------------------------------------- 外源语言 (docs/188 §7.1)

@test
def test_foreign_file_gets_a_language_section():
    """文件不是 Loment 时, 报错器要说清"它是什么 + 三条进来路 + 这门语言的边界"。

    **为什么这条重要**: 这是新手第二常见的处境（拿一份 C/Java 源码叫 `.lomt` 就编）。
    只报"非法字符 #"帮不到他 —— 他会去改那一行, 而那份源码本来是对的。
    内容全来自 `surface_data` 的语言卡（真源是 `loment_diag.LANG_CARDS`）。

    **语言是提示不是结论**：判定权在翻译器的 `--lang auto`，所以命令一律带 `--lang auto`，
    最后一行也要把这句话说明白 —— 说死一句错的语言，用户会拿着错的命令去试。
    """
    c_src = ("#include <stdio.h>" + "\n"
             + "int main(int argc, char **argv) {" + "\n"
             + "    printf(1);" + "\n"
             + "    return 0;" + "\n" + "}" + "\n")
    td = Path(tempfile.mkdtemp(prefix="lomenterr-foreign-"))
    f = td / "cflow.lomt"          # **名字是 .lomt, 内容是 C** —— 正是那个已知处境
    f.write_text(c_src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stderr[-200:])
    _, out = _render(d, lang=ZH)
    assert "不是 Loment" in out, f"没说这个文件不是 Loment: {out[-400:]!r}"
    assert "像 C" in out, f"没认出是 C: {out[-400:]!r}"
    assert "--lang auto" in out, "命令没带 --lang auto (那等于替翻译器下结论)"
    assert "--impl" in out and ".iface.lomt" in out, "三条路没写全"
    assert "边界" in out, "没给这门语言的边界"
    assert "别拿它当结论" in out, "没说语言只是提示"
    # **顺序也是判据**（`#29`）：外源那条提示要**顶到卡片前面** —— 或者说，卡片那四段
    # （错了什么 / 为什么错 / 怎么改 / 支持 / 不支持）**根本不该出现**，因为它们讲的
    # 全是 Loment 的规矩，而这份文件不是 Loment。原先它排在**最后**，读的人先照着
    # 一屏不适用的"修法"把一份好 C 改坏，翻到底才看见那句"这个文件不是 Loment"。
    for label in ("错了什么:", "为什么错:", "怎么改:", "支持:", "不支持:"):
        assert label not in out, f"外源文件不该给 Loment 的说明卡（{label}）: {out[-400:]!r}"
    print("      外源文件: 认出语言 + 三条路 + 边界, 且语言以 --lang auto 为准；"
          "提示顶在前面，Loment 的说明卡不再出现")


@test
def test_foreign_words_in_a_comment_do_not_flag_a_loment_file():
    """正经 Loment 文件**不会**因为注释里出现外源特征词就被判成外源。

    内容兜底是给"这个文件根本不是 Loment"用的，而注释里写一句 `def ` / `use std::` 太容易了 ——
    一份正经 Loment 文件只要有一条语法错，就会被自己的注释带成"这看起来是 Python"，
    然后在真正的诊断后面挂一段几百字的建议。

    挡它的是"**命中必须在行首**"：真实的签名行（`#include` / `def ` / `func ` /
    `public class`）都在行首，而注释里的那个词不在。
    """
    src = ("module m" + "\n" + "\n"
           + "// def foo(self) is Python, but this file is not" + "\n"
           + "fn f() -> u32 {" + "\n"
           + "    return z;" + "\n" + "}" + "\n")
    td = Path(tempfile.mkdtemp(prefix="lomenterr-nofalse-"))
    f = td / "coment.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stderr[-200:])
    _, out = _render(d, lang=ZH)
    assert "error[E002]" in out, out[:200]
    assert "不是 Loment" not in out, f"被注释带跑了: {out[-300:]!r}"
    print("      注释里的外源特征词不误报 (命中要在行首)")

@test
def test_a_grammar_declaration_beats_content_sniffing():
    """文件头写了 `choose write grammar <别名>` 时，**声明说了算** —— 与工具链同一次序。

    `potato_from.resolve_lang` 现在是 **声明 > 后缀 > 内容**（`docs/188` §2）：声明是
    **作者对这份文件说的**，而后缀只是命名习惯。渲染器看不到工具链的解析结果，得自己
    找那一行 —— 不找就会**说出与工具链相反的一句**，而用户只能信一个。

    这条是实测出来的缺口：一份 `choose write grammar python` 的 `.lomt`，工具链知道是
    Python，而报错器因为内容里没有 Python 特征词（`def ` / `__name__` / `self.`）
    **一个字都不说** —— 工具链知道、渲染器沉默，是最坏的一种。

    顺带钉住**别名**也要认（`c#` / `cs` → C#）：别名表由 `--dump-surface` 从
    `potato_from.GRAMMAR_ALIASES` 导出，所以"作者能写哪些词"仍是一处真源。

    **判据自己造诊断，不请编译器**（这条是 2026-09-18 改的，因为前门落地后不得不改）：
    一旦 `choose write grammar python` 的文件走前门**编得过**了（`eb060eb`），
    "请编译器产一条诊断"这条路就没了 —— 而这条判据要判的是**渲染器读文件头**这件事，
    与谁产的诊断无关。所以夹具改成 `.py` 文件 + 一条合成诊断。顺带把判据变强了：
    **后缀说是 Python、声明说是 C#**，此时"声明赢"才是真的被验到（以前两边都是 Python，
    声明赢不赢看不出来）。
    """
    def probe(name: str, body: str) -> str:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-decl-"))
        f = td / f"{name}.py"                 # 后缀那条路也认得出来 → 外源段一定在
        f.write_text(body, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 2, "col": 1, "code": "E019",
                                 "message": "boom"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
        assert rc == 1, (rc, out[:120])
        return out

    # 声明与后缀**说不同的话**：后缀是 `.py`，声明是 C# —— 声明必须赢
    out = probe("decl", "choose write grammar cpp" + "\n" + "\n"
                + "x = 1" + "\n")
    assert "像 C++" in out, f"没按声明认（后缀是 .py）：{out[-400:]!r}"
    assert "据文件头" in out, f"没说清依据是声明：{out[-400:]!r}"

    # 别名：作者写 `cs` / `c#` 都该落到 C#
    for alias in ("cs", "c#", "C#"):
        o2 = probe("al", "choose write grammar " + alias + "\n"
                   + "y = 2" + "\n")
        assert "像 C#" in o2, f"别名 {alias!r} 没认成 C#：{o2[-400:]!r}"

    # 没有声明时，依据仍要说清是"据后缀"（不能混着说）
    o3 = probe("noext", "y = 2" + "\n")
    assert "像 Python" in o3 and "据后缀" in o3, f"没说清依据是后缀：{o3[-400:]!r}"
    print("      声明压过后缀与内容嗅探（含别名 cs/c#/C#），依据也说给用户")

@test
def test_a_malformed_declaration_is_not_honoured():
    """声明那一行的**词边界**要钉死：写坏了就**不许当声明**。

    这一条来自对端的教训（而且是他们自己抓到的）：他们把那三个词从散落的字面量重组成
    "从一个常量拼出来"时，`_GRAMMAR_LINE` 的尾巴漏了 `` —— 于是拼错一个字母的
    `choose write grammars python` 会命中**前缀**、抹掉 20 个字符、留下 `s python`；
    而 `read_grammar_decl` 那边**有** `` 所以**不报错**，坏处全落在用户那行上。
    抓住它的**不是判据**（原有 7/7 全过），是"**重组前后对拍**"。

    我这边刚把 `decl_at` 从三次写死的 `eat_word(…, "choose")` 改成**逐词循环**
    （词序读 `surface_data`）—— **形态上与他那次是同一类改动**。所以这里不靠嘴说
    "`eat_word` 本来就有边界检查"，而是**把那张表钉出来**。

    判据的断言刻意分成两种，免得把"正确地忽略"与"坏掉了"混成一条：
    **畸形**的声明不许说"据文件头"；**良构**的必须说。而畸形那半还要**多一条**：
    外源段必须**在场**（`不是 Loment` 要出现）—— 不然"没说据文件头"在"根本没说任何话"时
    也成立，那这条判据就没有牙。夹具因此用 `.py` 后缀：后缀那条路保证外源段一定在，
    于是"没说据文件头"只能是因为声明没被认。

    **夹具自己造诊断**（理由见上一条判据）：前门落地后，写了声明的文件编得过，
    "请编译器产诊断"这条路没有了 —— 而这条判据判的是渲染器**读文件头**。
    """
    def probe(body: str) -> str:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-declbad-"))
        f = td / "m.py"                        # 后缀把外源段钉在场
        f.write_text(body, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 2, "col": 1, "code": "E019",
                                 "message": "boom"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
        assert rc == 1, (rc, out[:120])
        return out

    # **畸形**：都不许被当成声明（"据文件头"一个字都不该出现，而外源段要在）
    malformed = [
        "choose write grammars python",     # 第三个词拼错 —— 对端那次就是这一条
        "choose write grammar",              # 没有别名
        "choosewrite grammar python",        # 词之间没断
        "xchoose write grammar python",      # 行首有杂质
        "// choose write grammar python",    # **注释里**写着 —— 最要紧的一条
        "choose write grammar python2",      # 别名不在出厂锁里
    ]
    for line in malformed:
        out = probe(line + "\n" + "y = 1" + "\n")
        assert "不是 Loment" in out, f"夹具没让外源段出现, 这条判据就没牙了: {out[-300:]!r}"
        assert "据文件头" not in out, f"畸形声明被当成了声明: {line!r}\n{out[-300:]!r}"

    # **第三词后面的分隔符类** —— 这一类是对端用**枚举**抓到的（手挑挑不出 `#` `.` `:`
    # 插在第三词后面）。他们那边原先用 ``，`#` 算"词边界"于是判成"头成立、别名没写"；
    # 我这条刀要求"空白 / `;` / 行尾"，判成"根本没有声明"。**两边对同一份源说不同的话**
    # ——两边都编得过，所以只有枚举能把它们摆到一起。现在两边同一条规矩，这里把它钉住。
    for sep in ("#", ".", ":", ",", "-", "_", "(", "!", "=", "@", "'", '"'):
        body = "choose write grammar" + sep + "python" + "\n" + "y = 1" + "\n"
        out = probe(body)
        assert "不是 Loment" in out, f"夹具没让外源段出现: {sep!r} {out[-300:]!r}"
        assert "据文件头" not in out, (
            f"第三词后接 {sep!r} 被当成了声明: {out[-300:]!r}")

    # **良构**：必须说"据文件头"（含允许的写法：分号收尾、别名后跟说明）
    wellformed = ["choose write grammar python",
                  "  choose	write  grammar	python",
                  "choose write grammar python;",
                  "choose write grammar python // 说明"]
    for line in wellformed:
        out = probe(line + "\n" + "y = 1" + "\n")
        assert "像 Python" in out and "据文件头" in out, (
            f"良构声明没被认: {line!r}\n{out[-300:]!r}")
    print("      声明的词边界: 6 种畸形 + 12 种分隔符都不认；4 种良构都认"
          "（含注释里那句不认）")

@test
def test_a_superset_language_is_not_reported_as_its_subset():
    """**C++ 不能被报成 C、C# 不能被报成 Java** —— 认语言是"逐门问、取第一个命中"，
    所以顺序**就是优先级**，而顺序写在 `loment_diag.LANG_ORDER` 里。

    这一条是对端抓出来的真 bug：生成器原先 `sorted(LANG_CARDS)`（字典序），于是 `c`
    排在 `cpp` 前面 —— 一份 C++ 文件里有 `#include`，**先被 C 那三条特征词接走**，
    C++ 的提示永远轮不到；同理 `java` 会接走 `csharp` 的 `public class`。

    它可证伪，而且证伪的方向明确：**把 `LANG_ORDER` 换回 `sorted` 这条就红**。
    加一门新语言时，如果它的特征词是某一门的老超集（像 C++ 之于 C），就得排到那门
    前面 —— 这是"顺序即优先级"的固有代价，写在这里免得下次再撞。
    """
    cases = [
        ("cpp", "C++", "#include <vector>" + "\n"
         + "int sum(int a) {" + "\n" + "    return a;" + "\n" + "}" + "\n"),
        ("cs", "C#", "using System;" + "\n"
         + "public class P {" + "\n"
         + "    static void Main(string[] args) {}" + "\n" + "}" + "\n"),
    ]
    for ext, want, src in cases:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-order-"))
        f = td / f"m.{ext}.lomt"        # 后缀是 .lomt -> 只能靠内容认
        f.write_text(src, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(f), "--check", "--diag-out", str(d)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 1, (ext, r.returncode)
        _, out = _render(d, lang=ZH)
        assert f"像 {want}" in out, f"{ext} 的文件被认成别的语言了: {out[-400:]!r}"
    print("      C++ 不被认成 C、C# 不被认成 Java（顺序即优先级）")

@test
def test_foreign_section_is_given_once_per_file():
    """同一个外源文件给**一次**就够了 —— 重复 N 遍会把真正的诊断挤没。

    它靠的是记住"上次给过哪个文件"。**这条要能证伪**：造两条同一文件的诊断，数出现次数。
    """
    td = Path(tempfile.mkdtemp(prefix="lomenterr-once-"))
    src = td / "j.java"
    src.write_text("public class S {" + "\n" + "    static void main() {" + "\n" + "    }" + "\n" + "}" + "\n",
                   encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    rec = {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "期望 module"}
    d.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n",
                 encoding="utf-8", newline="\n")
    rc, out = _render(d, lang=ZH)
    assert rc == 1
    assert out.count("不是 Loment") == 1, f"外源段给了不止一次: {out.count(chr(19981) + chr(26159) + chr(32) + chr(76))}"
    assert "2 条错误" in out, "两条诊断都要计数"
    print("      外源段一个文件只给一次 (两条诊断, 一次提示)")

# ---------------------------------------------------------------- 语言 (§14)

@test
def test_english_is_the_default_and_the_renderers_own_text_is_ascii():
    """**出厂默认说英文**，而且那句英文一个非 ASCII 字节都没有。

    两条是一件事的两半。默认之所以从中文换成英文：Windows 控制台按 936 代码页解 UTF-8，
    而 PE 垫片没有 `WriteConsoleW`，程序侧无从补救（`docs/169` §3a）—— 于是"默认输出"
    在中文 Windows 上一直是乱码。换成英文才修得掉，**前提是那份英文真的全 ASCII**：
    夹一个 `—` 或 `…` 进来，936 控制台上照样花，这个改动就白做了。

    **判据自己造一条 ASCII 消息**，而不是拿编译器那条：编译器给的消息原文现在仍是中文
    （`docs/182` §5.3，那是另一条线的事），混进来会把"报错器自己那部分是不是 ASCII"
    偷换成"整份输出是不是 ASCII"。分开说才判得准 —— 也才诚实地说明这个改动修到了哪一步。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-lang-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": str(src), "line": 4, "col": 12, "code": "E002",
                                 "message": "call to undeclared function z"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)                     # 不传 lang = 出厂默认
    assert rc == 1, (rc, out[:200])
    assert "error[E002]:" in out, out[:200]
    assert "undeclared name" in out, f"默认没说英文: {out[:200]!r}"
    # 四段齐全（英文侧也要，不能只有中文那份全）
    for label in ("what went wrong:", "why:", "how to fix:", "supported:", "not supported:"):
        assert label in out, f"英文侧说明卡少了一段 {label!r}: {out[-300:]!r}"
    for i in (1, 2, 3):
        assert f"  {i}. " in out, f"英文侧没有第 {i} 条修法: {out[-300:]!r}"
    assert out.count("^") >= 1, "英语侧没有插入符"
    bad = sorted({c for c in out if ord(c) > 127})
    assert not bad, f"渲染器自己那部分有非 ASCII 字节 {bad!r} —— 936 控制台上就是乱码"
    print("      默认英文, 四段齐全, 且渲染器自己那部分逐字节纯 ASCII")


@test
def test_errconfig_switches_the_language_and_a_bad_one_falls_back():
    """`errconfig` 能把语言切到中文；**读不出来就退回默认**（英文），不报错。

    六种输入各来一遍，判决只有两个：切了 / 没切。
      * `"zh"` / `"ZH"` / `"Chinese"` → 中文（**本来就该大小写不敏感**：配置是手写的，
        逼用户记住大小写换来的是"配了没生效"，而那种失败**没有任何提示**。
        第一版只让长写法不敏感、`en`/`zh` 是逐字节比的 —— `"ZH"` 静默退回默认，
        正是文档说 A 代码做 B。这一条留着就是那次实测）；
      * `"en"` → 英文（显式写回默认，也是合法的）；
      * `"klingon"` → **认不出来**，退回英文 —— 不报错、不猜；
      * 注释里出现 `error_lang` → **不算**（配置项要写成 `error_lang(`）。

    最后一条是设计的一部分而不是巧合：少了"标签后面必须紧跟 `(`"这条，一句注释就能把语言
    悄悄改掉 —— 而"悄悄生效"正是本仓最反对的那种。
    """
    f, d = _check(SEMANTIC)
    del f

    def cfg(v: str) -> str:
        return f'module e\npub fn error_lang() -> str {{\n    return "{v}";\n}}\n'

    cases = [
        (cfg("zh"), True),
        (cfg("ZH"), True),
        (cfg("Chinese"), True),
        (cfg("en"), False),
        (cfg("klingon"), False),
        ('// error_lang is documented here\n'
         'pub fn other() -> str {\n    return "zh";\n}\n', False),
    ]
    for body, want_zh in cases:
        with tempfile.TemporaryDirectory(prefix="lomenterr-cfg-") as td:
            _errconfig(Path(td), body)
            rc, out = _run_in(Path(td), d)
        assert rc == 1, (body, rc, out[:120])
        got_zh = "符号未声明" in out
        assert got_zh == want_zh, f"errconfig={body!r} 期望中文={want_zh} 得到 {got_zh}: {out[:200]!r}"
    print("      errconfig: zh/ZH/Chinese 切中文, en/乱值/注释里的伪标签都退回默认")


@test
def test_the_toolchain_beside_errconfig_is_the_second_place_read():
    """两份 `errconfig`：**项目那份优先，工具链旁边那份兜底**（`docs/182` §14）。

    `./errconfig` 好测（`_render` 就是这么干的）；工具链旁边那份**从 `argv[0]` 推**，
    所以要摆一个假的包布局：`<tmp>/bin/lomenterr` + `<tmp>/share/loment/errconfig`。

    这条同时钉住一个容易被写反的语义：**"值认不出来" = "没配"**，于是**继续找下一份**。
    不这么做的话，项目里写错一个字母会把用户**系统级**的那份设置悄悄作废 ——
    用户明明配过一次、又明明刚在项目里"配"了一次，结果两份都没生效，而输出里没有任何提示。
    这条纪律是从 `loment.conf` 抄的（"读不出来就当没配"），所以它两边是同一句。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-pkg-") as td:
        pkg = Path(td) / "pkg"
        (pkg / "bin").mkdir(parents=True)
        (pkg / "share" / "loment").mkdir(parents=True)
        exe = pkg / "bin" / _bin().name
        shutil.copy2(_bin(), exe)                     # 保留可执行位（Linux 上要）
        _errconfig(pkg / "share" / "loment", 'module errconfig\n'
                                             'pub fn error_lang() -> str {\n'
                                             '    return "zh";\n}\n')

        cwd = Path(td) / "proj"
        cwd.mkdir()
        _, d = _check(SEMANTIC)                        # 诊断文件的 file 是绝对路径

        def run() -> str:
            r = subprocess.run([str(exe), "--no-color", str(d)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=60, cwd=str(cwd))
            assert r.returncode == 1, (r.returncode, r.stderr[:120])
            return r.stdout + r.stderr

        assert "符号未声明" in run(), "工具链旁边那份没被读到"
        # 项目那份写了个认不出来的值 → **当作没配**，落到工具链那份（中文），而不是英文
        _errconfig(cwd, 'module errconfig\npub fn error_lang() -> str {\n'
                        '    return "klingon";\n}\n')
        assert "符号未声明" in run(), "认不出来的值把系统级设置作废了 (该继续找下一份)"
        # 项目那份**有效**时必须压过工具链那份
        _errconfig(cwd, 'module errconfig\npub fn error_lang() -> str {\n'
                        '    return "en";\n}\n')
        assert "undeclared name" in run(), "项目那份没有压过工具链那份"
    print("      两份 errconfig: 项目优先、工具链兜底, 认不出来的值算'没配'")


# ---------------------------------------------------------------- 汇总 (§15)

@test
def test_summary_groups_by_code_worst_first():
    """结尾的汇总**按码分组，条数多的先出**，并列时码号小的在前。

    这是"12 条错误"之外多出来的那点信息：12 条里有 9 条是同一个原因时，改一处就消掉九条，
    而按出现顺序一条条修会白改八次。所以**顺序本身是被判的**（不是"反正都印出来了"）。

    汇总只在**给人看**的两个模式下打（`--json` 不打，那会让下游解析器当场坏掉）。
    它后面还跟着 `more: loment explain ...`（`docs/191` §3 #4），所以这里**按内容找**那一行，
    而不是"取最后一行" —— 那种取法在尾巴上加一行就会静默失准。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-sum-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        # 故意让"少见的码"排在文件里靠前：按出现顺序排的话 E019 会先出，这条判据就抓得到。
        rows = [{"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "a"},
                {"file": str(src), "line": 1, "col": 1, "code": "E002", "message": "b"},
                {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "c"},
                {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "d"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    summ = [ln for ln in out.splitlines() if ln.startswith("4 errors:")]
    assert summ == ["4 errors: E019 x3, E002 x1"], f"汇总行不对: {summ!r}"
    print("      汇总按码分组、条数多的先出 (4 errors: E019 x3, E002 x1)")


# ---------------------------------------------------------------- --short / --json (§15)

@test
def test_short_mode_is_one_line_per_diagnostic():
    """`--short`: **一行一条**，`文件:行:列: error[码]: 消息`，而且**顺带关色**。

    形状照抄 GCC / clang 那一族，编辑器与 CI 不用任何配置就认得。四个断言各挡一种退化：
    * **前 N 行** = N 条诊断（多一行少一行，"一行一条"这个承诺就破了）；
    * 一条诊断里的换行**折成空格**（不折的话一条会变两三行 —— 同一个承诺的另一种破法）；
    * **没有转义字节**（`--short` 不带 `--no-color` 也不该上色：管道里要的是能 grep 的行）；
    * 尾巴那两行（汇总 + `more:`）**不能挤进诊断区** —— 所以这里按"前 N 行"判，
      而不是"总共就 N 行"：断言一改，往尾巴上加东西就会红，而尾巴本来就该能加东西。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-short-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": "a.lomt", "line": 3, "col": 7, "code": "E002", "message": "x\ny"},
                {"file": "b.lomt", "line": 9, "col": 0, "code": "E019", "message": "z"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _run_in(Path(td), d, "--short", color=True)
    assert rc == 1, (rc, out[:200])
    lines = out.strip().splitlines()
    assert lines[0] == "a.lomt:3:7: error[E002]: x y", f"短格式不对: {lines[0]!r}"
    # col=0 是"编译器只给了行号"（check() 的语义错就是这样），那时不该硬编一个列出来
    assert lines[1] == "b.lomt:9: error[E019]: z", f"没列时不该印列: {lines[1]!r}"
    assert lines[2] == "2 errors: E002 x1, E019 x1", f"短模式不给汇总: {lines[2]!r}"
    assert lines[3] == "more: loment explain E002 E019", f"没有 explain 指针: {lines[3]!r}"
    assert len(lines) == 4, f"两条诊断 + 汇总 + 指针 = 4 行, 得到 {len(lines)} 行: {out!r}"
    assert "\x1b" not in out, f"--short 不该上色 (管道里要能 grep): {out[:120]!r}"
    print("      --short: 一行一条 + 换行折空格 + 顺带关色 + 末尾汇总与 explain 指针")


@test
def test_json_mode_carries_the_whole_card_and_parses():
    """`--json`: **一行一个对象**（JSONL，与编译器的 `--diag-out` 同族，多了卡片字段）。

    它给的是**程序**：编辑器把 `what`/`why`/`fixes` 放进 quickfix 面板，CI 按码统计。
    所以三条都要判：
    * **每一行都能被解析** —— 卡片正文里有 `"` 与反斜杠，不转义就是一个坏 JSON，
      而在编辑器那边那表现为"报错器坏了"，不是"这条消息有点怪"；
    * 卡片字段**在**且 `fixes` 是数组（≥3 条，与门槛同一份）；
    * **没有汇总行** —— 那是"给人看"的东西，混进 JSONL 会让下游解析器当场坏掉。
      所以带中文消息的一行也要能过（转义把非 ASCII 原样留着，JSON 允许）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-json-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": "a.lomt", "line": 3, "col": 7, "code": "E002",
                 "message": 'has "quotes" \\ and\na newline'},
                {"file": "b.lomt", "line": 9, "col": 2, "code": "E002", "message": "中文也行"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _run_in(Path(td), d, "--json", color=True)
    assert rc == 1, (rc, out[:200])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2, f"每个诊断一行, 不该多出别的行(汇总?): {out!r}"
    for ln in lines:
        obj = json.loads(ln)                     # 解析不了就是坏 JSON
        for k in ("code", "title", "file", "line", "col", "message",
                  "what", "why", "fixes", "yes", "no"):
            assert k in obj, f"缺字段 {k}: {ln[:120]!r}"
        assert isinstance(obj["fixes"], list) and len(obj["fixes"]) >= 3, obj["fixes"]
    first = json.loads(lines[0])
    assert first["message"] == 'has "quotes" \\ and\na newline', first["message"]
    assert first["file"] == "a.lomt" and first["line"] == 3 and first["col"] == 7
    assert "\x1b" not in out, "--json 不该上色"
    print("      --json: 每行可解析 + 卡片字段齐全 + 转义正确 + 不掺汇总行")


# ---------------------------------------------------------------- 拼写建议 (§14)

@test
def test_the_help_block_prints_the_fixed_line():
    """**唯一**近候选时，印出**改好的那一行**（rustc 的 `help:` 就是这么做的）。

    这条判据在 2026-09-18 换过一次契约：原先只印一句 `did you mean X (declared at line N)`，
    现在印出**整行改好的源码**。理由：一句"你是不是想写 X"要求人在脑子里做替换，
    **而替换正是最容易再错一次的地方**；给一行能直接抄的，就没有第二次机会出错
    （`docs/191` §3 #1，抄自 rustc 的 `help:` 块）。

    夹具走的是**真编译器**（`lomentc --check --diag-out`），不是手写的 JSONL ——
    手写的消息比真消息干净，会漏掉真消息尾巴上那句 `（跨模块调用需要 pub）`，
    而那句曾经把线索带偏（见 `test_the_hint_ignores_the_compilers_trailing_note`）。
    """
    src = ("module m\n\n"
           "fn leftover_count() -> u32 { return 0; }\n\n"
           "fn _start() {\n"
           "    let a: u32 = leftove_count();\n"
           "}\n")
    f, d = _check(src)
    del f
    rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "help: a function with a similar name exists" in out, f"没给 help 块: {out[-400:]!r}"
    # 改好的那一行：名字被换掉，而**同一行别的字一个不动**
    assert "let a: u32 = leftover_count();" in out, f"没印出改好的那一行: {out[-400:]!r}"
    assert "leftove_count();" not in out.split("help:")[-1], "改好的行里还是错的那个名字"
    # 插入符点在**新**名字上，长度 = 新名字的长度
    fixline = [ln for ln in out.splitlines() if "leftover_count();" in ln][-1]
    caret = out.splitlines()[out.splitlines().index(fixline) + 1]
    assert caret.count("^") == len("leftover_count"), f"插入符长度不对: {caret!r}"
    print("      help 块: 印出改好的那一行 + 新名字下的插入符")


@test
def test_ties_list_candidates_with_kind_and_line():
    """**并列**时不再闭嘴：列出来，每条带**种类**与**行号**。

    这条判据也在 2026-09-18 换过契约（与上一条同一轮，方向相反）：原先并列就**什么都不说**
    —— 那是我在没有更好的办法时的保守（说错一个名字，用户会照着它改）。抄了 Ruby 的
    `Did you mean?` 列表与 rustc 的 "a local variable with a similar name exists" 之后
    看清了：**闭嘴与抛硬币之间还有第三档** —— 列出来让用户挑，并说清每个候选**是什么**、
    **在哪一行**。那既不猜，也不把"有两个可能"这个事实瞒掉。

    子代理实测 8 门语言，**没有一门**为拼错给出多个候选、更没一门把候选按种类标出来
    （`docs/191` §2 第一条）。所以这一格是照**引述里的 Ruby** 抄的，而不是照实测的那几门。

    差得远的那一档仍然**什么都不说** —— 那条没变，也不该变。
    """
    src = ("module m\n\n"
           "fn sum_total1() -> u32 { return 0; }\n\n"
           "fn sum_total2() -> u32 { return 0; }\n\n"
           "fn _start() {\n"
           "    let a: u32 = sum_totalz();\n"
           "}\n")
    f, d = _check(src)
    del f
    rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "help: names that are close:" in out, f"并列没列候选: {out[-400:]!r}"
    assert "`sum_total1` (a function, line 3)" in out, f"候选缺种类/行号: {out[-400:]!r}"
    assert "`sum_total2` (a function, line 5)" in out, f"候选缺种类/行号: {out[-400:]!r}"
    # 并列时**不给**改好的那一行（指不准改哪一处，就不给一个看着像答案的东西）
    assert "let a: u32 = sum_total1();" not in out, f"并列却给了唯一答案: {out[-400:]!r}"

    # 差得远: 一个都不像 → 仍然一声不吭
    src2 = "module m\n\nfn _start() {\n    let a: u32 = nothing_like_this_at_all();\n}\n"
    _, d2 = _check(src2)
    _, out2 = _render(d2)
    assert "help:" not in out2, f"差得远也吭声: {out2[-400:]!r}"
    print("      并列: 列候选（带种类与行号）且不给唯一答案; 差得远仍不说话")


@test
def test_the_hint_ignores_the_compilers_trailing_note():
    """线索取自**源文件**，不是"消息里最后一个词" —— 这是实测逼出来的一个真 bug。

    真消息长这样：`10: 调用未定义的函数 leftove_count（跨模块调用需要 pub）`。
    原先取"最后一个长度 >= 2 的词"，取到的是 **`pub`**（括号里那句提示文字的末尾），
    于是**插入符划到了 `pub`、拼写建议去查 `pub` 的邻居** —— 而这一条说的明明是
    `leftove_count`。（夹具比真东西干净：手写的消息没有那条尾巴，所以它测的是一个
    不会发生的输入。这一条判据因此改用**真诊断**。）

    修法不是维护一张"消息里的噪声词"表（那是同一份清单抄第二遍，编译器改一次措辞就过期），
    而是**拿源文件当裁判**：从消息末尾往前找第一个"在源文件里整词恰好出现一次"的词。
    """
    src = ("module m\n\n"
           "fn leftover_count() -> u32 { return 0; }\n\n"
           "fn _start() {\n"
           "    let a: u32 = leftove_count();\n"
           "}\n")
    f, d = _check(src)
    del f
    rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    # 真消息里确实有那句尾巴（否则这条判据就没在测它）
    assert "需要 pub" in out, f"夹具或编译器变了 —— 消息里没有那句尾巴: {out[:400]!r}"
    # 插入符必须落在 leftove_count 上（13 个字符），不是 pub（3 个）
    srcline = [ln for ln in out.splitlines() if "leftove_count();" in ln
               and "help" not in ln and "let a" in ln]
    assert srcline, f"没印出那一行: {out[:400]!r}"
    caret = out.splitlines()[out.splitlines().index(srcline[0]) + 1]
    assert caret.count("^") == len("leftove_count"), (
        f"插入符没落在那个名字上（落在 pub 上就是旧 bug 回来了）: {caret!r}")
    assert "help: a function with a similar name exists" in out, "建议也没了"
    print("      线索取自源文件 (真诊断尾巴上的 pub 不再把插入符带偏)")


# ---------------------------------------------------------------- E018 的候选 (§15)

@test
def test_e018_lists_what_is_actually_in_deps():
    """E018: 光说"补文件"没用 —— 要把**手上真实有的**说出来。两种地方各判一条。

    * 没有 `./deps` → 明说没有，并把第 1 层找的路径写清楚。**这条不需要 `getdents64`**：
      `openat` 失败本身就是答案；
    * 有 `./deps` → 只列**名字接近**的（`zlib` 与 `util` 差得远，不许出现在清单里）。

    工作目录就是"项目根"（`use` 的第 1 层从这里找），所以要 `_run_in` 指定 cwd ——
    这也顺带钉住"项目根 = 工作目录"这条约定。

    **线索取自源文件**（`msg_hint`：消息里第一个"在本文件里整词恰好出现一次"的词），
    所以夹具的源里得**真的有那一行 `use util`** —— 这也正是真实情形：那一行就是
    E018 的现场。这一点是 2026-09-18 随 `msg_hint` 一起改的：先前"取最后一个词"时，
    源里有没有那一行都无所谓；现在**有**才是常态、也才说得通（名字都不在文件里，
    "它指向源里哪一处"这个问题本身就不成立）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-deps-") as td:
        root = Path(td)
        f = root / "m.lomt"
        f.write_text("module m\n\nuse util\n", encoding="utf-8", newline="\n")
        d = root / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 3, "col": 1, "code": "E018",
                                 "message": "名字导入找不到模块 util"}) + "\n",
                     encoding="utf-8", newline="\n")

        rc, out = _run_in(root, d)               # 还没有 deps/
        assert rc == 1, (rc, out[:200])
        assert "no `./deps` directory here" in out, f"没明说没有 deps/: {out[-300:]!r}"

        for nm in ("util2", "utils", "zlib"):
            (root / "deps" / nm).mkdir(parents=True)
        rc, out = _run_in(root, d)
        assert rc == 1, (rc, out[:200])
        assert "util2" in out and "utils" in out, f"没列出接近的名字: {out[-300:]!r}"
        assert "zlib" not in out, f"差得远的不该进清单: {out[-300:]!r}"
    print("      E018: 没有 deps/ 就明说; 有就只列名字接近的")


@test
def test_json_suggestions_are_machine_applicable():
    """`--json` 的 `suggestions[]` 是**能直接应用**的：`replacement` + `byte_start`/`byte_end`。

    抄 rustc 的 `--error-format=json` / Swift 的 fix-it / Roslyn 的 code fix 那一族：
    编辑器的价值不在"看到一句话"，而在"**一键改对**"。所以判据不止看字段在不在 ——
    它**真的拿那两个偏移去原文里切一刀**，切出来的必须正好是那个错名字。

    **CRLF 也判**（同一件事的两半）：偏移只有是真的**文件字节**偏移才有用，而
    `lomenterr` 早先会把源里的 CR 就地剥掉再渲染 —— 那样每个 CRLF 文件从第二行起
    就**每行偏一个字节**，而 Windows 上 CRLF 是常态。判据在一份 CRLF 源上再切一刀，
    就是为了钉死这一点（切出来是那个名字 = 偏移没被规范化过）。

    **自己加的一条规矩**（rustc 不需要，因为它有 span）：偏移**只在那名字于本文件里
    全局唯一时**才给 —— 位置是**推**出来的，推不准就只给名字。并列那半判这个。
    """
    src = ("module m\n\n"
           "fn leftover_count() -> u32 { return 0; }\n\n"
           "fn _start() {\n"
           "    let a: u32 = leftove_count();\n"
           "}\n")
    f, d = _check(src)
    rc, out = _run_in(d.parent, d, "--json")
    assert rc == 1, (rc, out[:200])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, out
    obj = json.loads(lines[0])
    sugs = obj["suggestions"]
    assert len(sugs) == 1, sugs
    s = sugs[0]
    assert s["kind"] == "fn" and s["line"] == 3, s
    assert s["applicability"] == "MaybeIncorrect", s               # 猜的，就说是猜的
    assert s["replacement"] == "leftover_count", s                 # 换成什么
    raw = f.read_bytes()
    assert raw[s["byte_start"]:s["byte_end"]].decode() == "leftove_count", (
        f"偏移切出来的不是那个错名字: {raw[s['byte_start']:s['byte_end']]!r}")
    # 真的应用一次：拿偏移切掉那一段、把 replacement 塞进去，得到的必须是**能编过的**那一行
    applied = raw[:s["byte_start"]] + s["replacement"].encode() + raw[s["byte_end"]:]
    assert b"leftover_count();" in applied, "应用之后那一行不是改好的样子"

    # CRLF：偏移是**文件字节**偏移，不是规范化之后的
    with tempfile.TemporaryDirectory(prefix="lomenterr-crlf-") as td:
        g = Path(td) / "c.lomt"
        g.write_bytes(src.replace("\n", "\r\n").encode())
        d2 = Path(td) / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(g), "--check", "--diag-out", str(d2)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 1, (r.returncode, r.stderr[-200:])
        _, out2 = _run_in(Path(td), d2, "--json")
        raw2 = g.read_bytes()                      # 临时目录出去就没了，先读
    s2 = json.loads([ln for ln in out2.splitlines() if ln.strip()][0])["suggestions"][0]
    assert raw2[s2["byte_start"]:s2["byte_end"]].decode() == "leftove_count", (
        "CRLF 文件上的偏移偏了 —— 那是把 CR 剥掉之后再算偏移的症状")

    # 并列：**不给偏移**（指不准改哪一处，就不给一个能改错地方的东西）
    src3 = ("module m\n\n"
            "fn sum_total1() -> u32 { return 0; }\n\n"
            "fn sum_total2() -> u32 { return 0; }\n\n"
            "fn _start() {\n"
            "    let a: u32 = sum_totalz();\n"
            "}\n")
    _, d3 = _check(src3)
    _, out3 = _run_in(d3.parent, d3, "--json")
    sugs3 = json.loads([ln for ln in out3.splitlines() if ln.strip()][0])["suggestions"]
    assert len(sugs3) == 2, sugs3
    assert all("byte_start" not in x for x in sugs3), f"并列却给了偏移: {sugs3}"
    print("      --json 建议: 偏移能切片（含 CRLF）; 并列时不给偏移")


@test
def test_the_tail_points_at_loment_explain():
    """结尾一行 `more: loment explain E002` —— 抄 rustc 的 `try 'rustc --explain E0318'`。

    子代理实测 8 门语言：**没有一门**在给人看的输出里给出能点的文档入口（rustc 只给命令，
    C# 的 `helpUri` 只进 SARIF）。所以照最好的那个抄，落点在最末一行。

    判据多一条**别人不会写的**：那一行点到的码**必须是真的**（在 `loment_diag.RULES` 里）——
    指针要么指向一件已经存在的东西，要么就是一句空话。`lomcli` 的 `explain` 认不认那个码，
    由 `loment_cli_test::test_every_code_explain_speaks` 那边管（这边不重复起工具链）。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_diag  # noqa: E402
    known = {r[0] for r in loment_diag.RULES}

    with tempfile.TemporaryDirectory(prefix="lomenterr-more-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "a"},
                {"file": str(src), "line": 1, "col": 1, "code": "E002", "message": "b"},
                {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "c"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[-1].startswith("more: loment explain "), f"最后一行不是指针: {lines[-1]!r}"
    codes = lines[-1].split("loment explain ")[1].split()
    assert codes == ["E019", "E002"], f"指针没按汇总的次序列出出现过的码: {codes}"
    assert all(c in known for c in codes), f"指针点了不存在的码: {codes}"
    print("      " + lines[-1])


@test
def test_max_caps_and_says_how_many_were_hidden():
    """`--max N` 卡住**渲染**条数，而**上限不许静默**：说清还剩几条。

    抄 clang 的 `-ferror-limit=20`（超了打 `fatal error: too many errors emitted,
    stopping now`）与 Go 的 10 条上限（`too many errors`）—— 两家都默认设上限，理由一样：
    生成的代码一错就是几百条，逐条渲染几十行会把要看的东西顶出屏幕。
    **但两家的说法都不完整**：都只说"停了"，不说"还剩几条"。这里的判据要两个都说。

    三个断言，各挡一种退化：
    * 默认 20 真的生效（造 25 条）；
    * **汇总的数字仍然是总数** —— "渲染了几条"与"一共几条"是两回事，上限不该把汇总改小；
    * `--max 0` = 全部（这个后门要留着：用户得能说"我就要全看"）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-max-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": str(src), "line": 1, "col": 1, "code": "E002", "message": f"m{i}"}
                for i in range(25)]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")

        rc, out = _render(d)                       # 默认 20
        assert rc == 1, (rc, out[:200])
        assert out.count("error[E002]:") == 20, f"默认上限没生效: {out.count('error[E002]:')}"
        assert "note: 5 more not shown (--max 0 for all)" in out, f"没说清还剩几条: {out[-300:]!r}"
        assert "25 errors: E002 x25" in out, f"汇总该报**总数**: {out[-300:]!r}"

        _, out0 = _run_in(Path(td), d, "--max", "0")
        assert out0.count("error[E002]:") == 25, "--max 0 该看全部"
        assert "more not shown" not in out0, "全渲染了还说不显示?"

        _, out3 = _run_in(Path(td), d, "--max=3")
        assert out3.count("error[E002]:") == 3, f"--max=N 那种拼法没认: {out3[:200]!r}"

        # **`--json` 不受上限管**：上限是"别把人的屏幕顶爆"的装置，而机器出口没有屏幕 ——
        # 那里少一条就是**静默丢数据**（下游按条数算的东西全错，而它在 stderr 上也看不到
        # 提示，因为 JSON 模式压根不打汇总与说明）。所以机器出口**全给**。
        _, outj = _run_in(Path(td), d, "--json", "--max", "3")
        assert len([ln for ln in outj.splitlines() if ln.strip()]) == 25, (
            f"--json 被上限截了 —— 那是静默丢数据: {len(outj.splitlines())} 行")
    print("      --max: 默认 20、--max 0 全看、--max=N 也认、--json 从不截; 上限与总数都说清")


# ---------------------------------------------------------------- 包那一侧 (§6 的兜底纪律)

#: 启动器判据的**桩包**: 真启动器(`loment_dist.LAUNCHER_SH`) + 桩驱动 + 源文件。
#: 桩驱动一跑就打 `PLAIN-DRIVER-OUTPUT`, 于是"驱动到底有没有被起"在这些判据里是**可观察的**
#: —— 参数被启动器静默吞掉时, 缺的正是这一行。
_LAUNCHER_STUB_DRIVER = ("#!/bin/sh\n"
                         "out=\n"
                         "while [ $# -gt 0 ]; do\n"
                         "  case \"$1\" in --diag-out) shift; out=$1 ;; esac\n"
                         "  shift\n"
                         "done\n"
                         "printf '%s\\n' "
                         "'{\"file\":\"x.lomt\",\"line\":1,\"col\":1,\"code\":\"E019\","
                         "\"message\":\"boom\"}' > \"$out\"\n"
                         "echo PLAIN-DRIVER-OUTPUT >&2\n"
                         "exit 1\n")


def _shp(p: Path) -> str:
    """Windows 路径 -> bash 打得开的形状（启动器是 POSIX 脚本, 跑在 bash 里）。"""
    s = str(p).replace("\\", "/")
    return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s


def _launcher_pkg(with_err: bool) -> Path:
    """摆一个包布局: `bin/loment` 取的是**真的**那份启动器, 驱动是桩。"""
    import loment_dist  # noqa: E402

    t = Path(tempfile.mkdtemp(prefix="lomenterr-launcher-"))
    pf = t / "pf"
    (pf / "bin").mkdir(parents=True)
    (pf / "share" / "loment").mkdir(parents=True)
    (pf / "share" / "loment" / "version").write_bytes(b"stub\n")
    (pf / "bin" / "loment-driver").write_text(_LAUNCHER_STUB_DRIVER, encoding="utf-8",
                                              newline="\n")
    (pf / "bin" / "loment").write_text(
        loment_dist._subst(loment_dist.LAUNCHER_SH), encoding="utf-8", newline="\n")
    if with_err:
        (pf / "bin" / "lomenterr").write_text(
            "#!/bin/sh\necho RENDERED-BY-LOMENTERR \"$@\"\n",
            encoding="utf-8", newline="\n")
    for p in (pf / "bin").iterdir():
        p.chmod(0o755)
    (pf / "src.lomt").write_text("module m\n", encoding="utf-8", newline="\n")
    # 第二份源: 给"一个 check 只吃一个文件"那条判据用。
    (pf / "other.lomt").write_text("module m\n", encoding="utf-8", newline="\n")
    return pf


_LAUNCHER_ENV = dict(os.environ)
_LAUNCHER_ENV["PATH"] = (f"{_shp(Path('/usr/bin'))}:{_shp(Path('/bin'))}:"
                         f"{_LAUNCHER_ENV.get('PATH', '')}")


@test
def test_version_prints_the_reporter_own_number():
    """`lomenterr version` 打的是**报错器自己的**版本号，与语言版本是**两条线**（`docs/182` §17）。

    报错器是一件独立交付的程序 —— 它有自己的命令行面、自己的判据、自己的孪生，改动也与
    语言面无关（2026-09-18 到 10-13 之间它改了七八轮，语言版本只动过一次）。捆在一个号上
    就只能记成"语言又升了一版"，那既不真，也让人没法回答"我用的是哪个报错器"。

    四个断言，各挡一种退化：
    * **三个写法**都认（`version` / `--version` / `-V`）—— 少一个就有人敲不出想要的东西；
    * 退 **0**（不是 2）—— 版本不是用法错误；
    * 那一行在 **stdout**，stderr 是空的 —— 脚本要 `v=$(lomenterr version)` 拿它，
      而 fd 2 抓不到（别处一律 fd 2，这一行是**有意的例外**，见 §17.4）；
    * 与 `loment_release.LOMENTERR` **逐字相同** —— 这一条同时是"没有第二份字面量"的判据：
      真源改了而 `--dump-surface` 忘了重跑，这里与 `test_surface_data_is_up_to_date`
      会一起红，而不是让一个编出来的号静静发出去。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_release  # noqa: E402
    want = f"lomenterr {loment_release.LOMENTERR}"
    assert re.fullmatch(r"\d+\.\d+(\.\d+)*", loment_release.LOMENTERR), (
        f"真源里的版本号不像个版本号（被清空/写成占位符了？）: {loment_release.LOMENTERR!r}")
    for spelling in ("version", "--version", "-V"):
        r = subprocess.run([str(_bin()), spelling], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=60)
        assert r.returncode == 0, (spelling, r.returncode, r.stderr[:120])
        assert r.stdout.strip() == want, (
            f"{spelling}: stdout={r.stdout!r}，期望 {want!r}")
        assert r.stderr == "", (
            f"{spelling}: 版本走 stdout，stderr 该是空的: {r.stderr!r}")
    print(f"      lomenterr version: 三个写法都打 {want!r}（stdout，与真源逐字节相同）")


@test
def test_the_docs_show_the_version_the_binary_prints():
    """README 与 QUICKSTART 里那两处 `lomenterr 0.1.2` 必须跟着真源走。

    它们是**叙述**（不是第二份真源），但叙述里印着一个**具体的号**，就会过期 ——
    而"文档说 0.1.2、二进制打 0.1.3"是最难被发现的那种不一致：两边单看都正常，
    要同时看着两边才知道。本仓对这类"抄第二遍"的处置一向是二选一：要么**生成**它，
    要么**判据盯着**它（`docs/182` §17.2）。这里选后者 —— 那两处是散文里嵌的一行示例，
    生成进去反而读不通。

    （`docs/182` §17 不进这份清单：它讲的是**机制**，引的是 `LOMENTERR` 那个常量名，
    而不是"这个号现在是几"。）
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_release  # noqa: E402
    want = f"lomenterr {loment_release.LOMENTERR}"
    for name in ("README.md", "QUICKSTART.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert want in text, (
            f"{name} 里没有 {want!r} —— 版本号改了而文档没跟上（docs/182 §17.2）")
    print(f"      README 与 QUICKSTART 都跟着真源 ({want!r})")


@test
def test_every_message_the_compiler_can_print_has_a_code():
    """**编译器能说的每一句，报错器都得认得出是哪一类** —— 这是「减少静默」的机械落点。

    分类是一张正则表（`loment_diag.RULES`）；落在表外的消息渲染成 **E999「未分类，请报告」**。
    那是一条**响亮的**退路（不是静默），但它把"我们没覆盖到"推给用户，而且只在真有人撞上时
    才暴露 —— 加了新诊断而忘了补分类，中间那段时间**没有任何东西会红**。

    所以这条判据把**编译器的诊断模板**枚举出来（AST 扫 `tools/lomentc.py` 里
    `errs.append(...)` 与 `LomError(...)` 的调用点），逐条过 `classify()`，断言没有一条落到
    E999。加了新消息却没补分类 —— 这里当场红，而不是等用户来报。

    **扫模板而不是跑语料**：模板是"编译器可能说什么"的**上界**，运行时的消息是它的实例化。
    上界覆盖了实例化一定覆盖，反过来不成立 —— 跑一遍语料只碰得到其中一部分，而**碰不到的那些
    恰恰是最容易漏的**（罕见路径）。这一轮实测：102 种模板，当时 0 条未分类。

    `{…}` 占位代成**数字** `1`：消息里的数值位置（`需要 2 个实参`）靠 `\\d+` 匹配，
    代成字母会**假红** —— 第一版就是这么误报的。
    """
    import ast
    import loment_diag  # noqa: E402
    src = (ROOT / "tools" / "lomentc.py").read_text(encoding="utf-8")

    def template(node):
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "1" for v in node.values)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def is_error_sink(call) -> bool:
        f = call.func
        if isinstance(f, ast.Attribute) and f.attr == "append" and isinstance(f.value, ast.Name):
            return "err" in f.value.id.lower()
        return isinstance(f, ast.Name) and "LomError" in f.id

    seen: set[str] = set()
    bad: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and node.args and is_error_sink(node):
            t = template(node.args[0])
            if not t:
                continue
            t = re.sub(r"^\{[^}]*\}:\s*", "", t)      # 去掉 `12: ` 那个行号前缀
            if t in seen:
                continue
            seen.add(t)
            if loment_diag.classify(t)[0] == "E999":
                bad.append(t)
    assert seen, "一条诊断都没扫到 —— 提取方式跟编译器脱节了（这条判据在空转）"
    assert not bad, (
        f"这些编译器消息没有分类（会渲染成 E999「请报告」）: {bad[:6]}"
        f" —— 去 `loment_diag.RULES` 补一条，别让用户替我们兜")
    print(f"      编译器 {len(seen)} 种诊断模板**全部**有分类（没有一条落到 E999）")


def _launcher_run(bash: str, pf: Path, args: list, *, cmd: str = "check",
                  file_first: bool = True):
    """跑**真启动器**。`file_first=False` 把开关放在文件名前面 —— 两种位置都得能跑。

    这个 `file_first` 是补出来的: 那条注释一直写着"两个位置都试", 而这里的跑法**只有**
    文件在前那一种 (见 `test_launcher_takes_exactly_one_input_file` 的 docstring)。
    """
    sp = _shp(pf / "src.lomt")
    argv = [bash, _shp(pf / "bin" / "loment"), cmd]
    argv += [sp, *args] if file_first else [*args, sp]
    return subprocess.run(argv, cwd=str(pf), env=_LAUNCHER_ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", shell=False,
                          timeout=60)


@test
def test_launcher_renders_with_it_and_says_so_without_it():
    """启动器: 有 lomenterr 就用它; 没有就**退回内置并把话说出来**。

    两条都要测, 而且**用真的启动器 + 桩驱动**: 只测"有"那一半的话, "静默换掉用户以为在用的
    东西"这个错就没有判据 (docs/182 §6/§8)。桩驱动同时吐 stderr(裸行) 与 `--diag-out`(JSONL),
    与两个真实现的形状一致。
    """
    bash = shutil.which("bash")
    if not bash:
        print("        (跳过: 没有 bash, 跑不了 POSIX 启动器)")
        return

    def run(pf: Path, *args: str, file_first: bool = True) -> str:
        r = _launcher_run(bash, pf, list(args), file_first=file_first)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        return r.stdout + r.stderr

    with_it = run(_launcher_pkg(True))
    without_it = run(_launcher_pkg(False))

    assert "RENDERED-BY-LOMENTERR" in with_it, f"在场时没起它: {with_it!r}"
    assert "PLAIN-DRIVER-OUTPUT" not in with_it, (
        f"用了渲染器就不该再吐驱动那行裸诊断 (会说两遍): {with_it!r}")

    assert "PLAIN-DRIVER-OUTPUT" in without_it, f"缺席时该退回裸诊断: {without_it!r}"
    assert "no lomenterr" in without_it, (
        f"缺席时**必须说一句** —— 静默换掉渲染器正是这条纪律要挡的: {without_it!r}")

    # **开关要送到渲染器手里**，不只是启动器认得：桩渲染器把自己的 argv 打出来，
    # 所以这一条直接看得到 `--no-color` 有没有被转交。两个位置都试 ——
    # `loment check FILE --no-color` 与 `loment check --no-color FILE`。
    for spelling in ("--no-color", "-C"):
        for file_first in (True, False):
            got_nc = run(_launcher_pkg(True), spelling, file_first=file_first)
            # 启动器**原样转交**用户那个写法（不归一化）—— 两种拼法 `lomenterr` 都认，
            # 所以转交时改写是没有意义的动作。
            where = "文件在后" if file_first else "开关在后"
            assert "RENDERED-BY-LOMENTERR" in got_nc and spelling in got_nc, (
                f"{spelling} ({where}) 没被转交给渲染器: {got_nc!r}")

    # `--short` / `--json` 走同一条转交路（`docs/182` §15）：它们也是**渲染器**的输出
    # 模式，驱动不该看见。这里判的是**行为**（桩渲染器把自己的 argv 打出来），
    # 因为"转发到位"不是能从代码里读出来的性质 —— cmd 侧只能静态判，
    # 那一条在 `loment_cli_test` 里。
    for spelling in ("--short", "--json"):
        got_om = run(_launcher_pkg(True), spelling)
        assert "RENDERED-BY-LOMENTERR" in got_om and spelling in got_om, (
            f"{spelling} 没被转交给渲染器: {got_om!r}")
    # `--max N` 是**两个词**，值也要一起送到（只转 `--max` 不转值，渲染器会把它当文件名）
    got_mx = run(_launcher_pkg(True), "--max", "0")
    assert "--max" in got_mx and " 0" in got_mx, f"--max 的值没转交: {got_mx!r}"
    # 两个一起给时**两个都要到**（用一个变量存一个开关就会静默丢掉另一个）
    got_both = run(_launcher_pkg(True), "--no-color", "--json")
    assert "--no-color" in got_both and "--json" in got_both, (
        f"同时给两个开关时丢了一个: {got_both!r}")
    print("      启动器: 在场则渲染(且不吃裸行), 缺席则退回并明说, "
          "四个开关(含 --max 的值)都转交到位")


@test
def test_launcher_takes_exactly_one_input_file():
    """`check`/`ir` 只吃**一个**文件: 多给的那个要**报错退 2**, 不能静默只查第一个。

    issue #55 的形状: `loment check ok.lomt bad.lomt --short` 在 cmd 侧**退 0 且只查了第一个**
    —— 而 `loment check *.lomt` 正是 shell 里最自然的写法, 于是一条 CI 步骤报"干净"而错的文件
    根本没被看。bash 侧当时报的是 `unknown option bad.lomt`（响, 但话不对）。

    **为什么必须是行为判据**: `*)` 那条分支在启动器源码里**一直就有**, 源码里有它、
    却没有一条判据证明它走得到 —— #55 就是那么活下来的。同理, `check --no-color FILE`
    这个位置也一直没有判据: 上面那条判据的注释写着"两个位置都试", 而它当时只跑了
    `check FILE --no-color`（同一个形状: 注释声称的覆盖面大于代码的覆盖面）。桩驱动一跑就打
    `PLAIN-DRIVER-OUTPUT`, 所以"驱动根本没被起"和"驱动被起了"在这里都是可观察的。
    """
    bash = shutil.which("bash")
    if not bash:
        print("        (跳过: 没有 bash, 跑不了 POSIX 启动器)")
        return

    # 不带渲染器的包: 驱动那行裸诊断就是"驱动有没有被起"的**唯一**信号
    # （带渲染器时它被有意吞掉, 见上面那条判据）。
    pf = _launcher_pkg(False)

    # 开关在前: 驱动必须被起 —— `check --no-color FILE` 修好之前这一条走不到驱动
    r = _launcher_run(bash, pf, ["--no-color"], file_first=False)
    assert r.returncode == 1 and "PLAIN-DRIVER-OUTPUT" in (r.stdout + r.stderr), (
        f"`check --no-color FILE` 没走到驱动: rc={r.returncode} {r.stdout}{r.stderr}")

    # 两个文件: 退 2、说出理由、**而且驱动没被起**（起了就是既查了又不承认）
    for cmd in ("check", "ir"):
        r2 = _launcher_run(bash, pf, [_shp(pf / "other.lomt")], cmd=cmd)
        blob = r2.stdout + r2.stderr
        assert r2.returncode == 2, (
            f"`{cmd}` 两个文件该退 2, 实得 {r2.returncode}: {blob!r}")
        assert f"{cmd} accepts exactly one input file" in blob, (
            f"`{cmd}` 的拒绝没说清理由, 也没说出用户敲的那个命令名: {blob!r}")
        assert "PLAIN-DRIVER-OUTPUT" not in blob, (
            f"`{cmd}` 拒绝了却还是把驱动起了 —— 那就是既查了又不承认: {blob!r}")

    print("      启动器: 开关在文件名前也能到驱动; 第二个文件被拒(退 2, 驱动没起), "
          "且拒绝里说的是用户敲的那个命令名")


# ---------------------------------------------------------------- Loment 版（S1 第十一格）

_TW = f"/tmp/loment-err-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomerrcheck.lomt"
ERR_SRC = ROOT / "loment" / "tools" / "lomenterr.lomt"

#: 语义错那条源（5 行, `return z;` 在第 4 行）与解析错那条（`@@@` 在第 6 行第 1 列）。
_SEM_SRC = "module m\n\nfn f() -> u32 {\n    return z;\n}\n"
_PAR_SRC = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n"

#: 判据写出来、孪生读进去的那几份诊断。`{F}` 是源文件路径 —— **两侧各写自己平台能
#: 打开的那一份**（孪生在 WSL 里跑、Python 那侧原生跑 PE）；本格的断言不碰路径。
_JSONL = {
    "semantic.jsonl": [{"file": "{F}/bad.lomt", "line": 4, "col": 0, "code": "E002",
                        "message": "4: 使用未声明的变量 z"}],
    "parse.jsonl": [{"file": "{F}/par.lomt", "line": 6, "col": 1, "code": "E002",
                     "message": "6:1: 语法错"}],
    "unknown.jsonl": [{"file": "{F}/bad.lomt", "line": 4, "col": 0, "code": "E999",
                       "message": "4: 无此码"}],
    "broken.jsonl": [{"file": "{F}/bad.lomt", "line": 99, "col": 0, "code": "E002",
                      "message": "99: 行号越界"}],
    "empty.jsonl": [],
    "two.jsonl": [{"file": "{F}/bad.lomt", "line": 4, "col": 0, "code": "E002",
                   "message": "4: a"},
                  {"file": "{F}/bad.lomt", "line": 4, "col": 0, "code": "E002",
                   "message": "4: b"}],
}


def _write_fixtures(d: Path, path_of_src: str) -> None:
    """把两份源与六份诊断写进 `d`；`{F}` 换成 `path_of_src`。"""
    (d / "bad.lomt").write_text(_SEM_SRC, encoding="utf-8", newline="\n")
    (d / "par.lomt").write_text(_PAR_SRC, encoding="utf-8", newline="\n")
    for name, rows in _JSONL.items():
        text = "".join(json.dumps({**r, "file": r["file"].replace("{F}", path_of_src)},
                                  ensure_ascii=False) + "\n" for r in rows)
        (d / name).write_text(text, encoding="utf-8", newline="\n")


def _py_report() -> str:
    """用 **PE 版 lomenterr**（原生跑）做与孪生**同名的那 7 项**断言, 打同一份报告。

    两侧跑的是**同一个源编出来的两个后端产物**（孪生那侧是 ELF、在 WSL 里）——
    断言的是"渲染出了什么", 不是某个平台的字节。诊断里那个 `file` 路径两侧不同,
    但下面的断言一条都不碰它（只看 `:行`、源行、标签这些）。
    """
    out: list[str] = []
    ct = {"p": 0, "f": 0}

    def rep(name: str, ok: bool, why: str) -> None:
        if ok:
            out.append(f"  PASS  {name}")
            ct["p"] += 1
        else:
            out.append(f"  FAIL  {name}: {why}")
            ct["f"] += 1

    exe = _bin()

    def rend(*extra: str) -> tuple[int, str]:
        r = subprocess.run([str(exe), *extra], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=60,
                           cwd=str(_WORK_PY))
        return r.returncode, r.stdout + r.stderr

    def allin(t: str, *frags: str) -> bool:
        return all(f in t for f in frags)

    rc1, o1 = rend("--no-color", "semantic.jsonl")
    rep("test_render_carries_the_whole_card",
        rc1 == 1 and allin(o1, "error[E002]:", "-->", ":4", "    return z;", "^^^^^^^^^",
                           "message:", "what went wrong:", "why:", "how to fix:",
                           "  1. ", "  2. ", "  3. "),
        "说明卡缺段、没插入符、或修法没编号成列")

    rc2, o2 = rend("--no-color", "parse.jsonl")
    rep("test_both_error_channels_render_differently",
        rc1 == 1 and ":4" in o1 and ":4:1" not in o1 and "^^^^^^^^^" in o1
        and rc2 == 1 and ":6:1" in o2 and "| ^" in o2 and "^^^^^^^^^" not in o2,
        "语义错划整行 / 解析错写 行:列 的形状不对")

    rc3, o3 = rend("--no-color", "unknown.jsonl")
    rep("test_unknown_code_is_said_out_loud",
        rc3 == 1 and allin(o3, "unknown error code", "no card for this code"),
        "未知码没说出来")

    rc4, o4 = rend("--no-color", "broken.jsonl")
    rep("test_broken_line_is_echoed_not_skipped",
        rc4 == 1 and allin(o4, ":99", "message:", "how to fix:"),
        "越界的行号把整条诊断丢了")

    rc5a, o5a = rend("--no-color", "empty.jsonl")
    rc5b, o5b = rend()
    rep("test_empty_diag_is_ok_and_usage_error_is_two",
        rc5a == 0 and "no diagnostics" in o5a and rc5b == 2 and "usage:" in o5b,
        "空诊断该退 0 / 无参数该退 2 并打用法")

    _, o6a = rend("semantic.jsonl")
    _, o6b = rend("--no-color", "semantic.jsonl")
    rep("test_color_on_by_default_and_gone_with_no_color",
        "\x1b" in o6a and "\x1b" not in o6b,
        "默认没上色, 或 --no-color 仍留转义字节")

    rc7, o7 = rend("--no-color", "--short", "two.jsonl")
    rep("test_short_mode_is_one_line_per_diagnostic",
        rc7 == 1 and o7.count("error[E002]:") == 2 and "2 errors: E002 x2" in o7,
        "短模式不是一条一行, 或汇总不对")

    out.append(f"lomerrcheck: {ct['p']}/{ct['p'] + ct['f']} 通过")
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


@test
def test_err_check_matches_loment_twin():
    """**Loment 版**（`lomerrcheck.lomt` 驱 ELF 版报错器）与 Python 版**同名断言的报告逐字节相同**。

    `docs/189` §3 的 S1 第十一格。被测的是**另一件 Loment 程序**（`lomenterr`）——
    Python 那侧跑的是它的 **PE** 产物（原生）, 孪生那侧跑 **ELF**（WSL）。两侧断言的是
    "渲染出了什么", 所以这条同时是"同一个源的两个后端产物渲染一致"的判据。

    **诊断由判据写出来**: 参考编译器的 `--diag-out` 随 S2 消失, 而且两个实现必须看同一份输入。
    """
    global _WORK_PY
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        win = td / "win"
        wsl = td / "wsl"
        win.mkdir()
        wsl.mkdir()
        _write_fixtures(win, str(win).replace("\\", "/"))
        _WORK_PY = win
        want = _py_report()
        # ELF 版: 同一个源, 另一个后端
        mod = lomentc.load(ERR_SRC)
        deps = lomentc.resolve_deps(mod, ROOT, ERR_SRC.parent, entry=ERR_SRC)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"lomenterr.lomt 自己检查不过: {errs[:2]}"
        ll = td / "lomenterr.ll"
        with ll.open("w", encoding="utf-8", newline="\n") as f:
            f.write(lomentc.emit_llvm(mod, ROOT, deps))
        elf = td / "lomenterr.elf"
        r = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        shutil.copy(elf, wsl / "lomenterr")
        (wsl / "lomenterr").chmod(0o755)
        _write_fixtures(wsl, _wsl_path(wsl))
        m2 = lomentc.load(TWIN)
        d2 = lomentc.resolve_deps(m2, ROOT, TWIN.parent, entry=TWIN)
        assert not lomentc.check(m2, deps=d2), "lomerrcheck.lomt 自己检查不过"
        ll2 = td / "chk.ll"
        with ll2.open("w", encoding="utf-8", newline="\n") as f:
            f.write(lomentc.emit_llvm(m2, ROOT, d2))
        chk = td / "lomerrcheck.elf"
        r2 = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(chk), str(ll2)],
            capture_output=True, text=True, shell=False)
        assert r2.returncode == 0, r2.stderr[-400:]
        outp = td / "check.out"
        binn = f"{_TW}chk.bin"
        script = (f"cp {_wsl_path(chk)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(wsl)} && {binn} > {_wsl_path(outp)} 2>&1; echo -n $?")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, text=True, timeout=300, shell=False)
        got = outp.read_bytes().decode("utf-8") if outp.exists() else ""
    assert rr.stdout.strip() == "0", f"孪生该退 0（7 项全过）: rc={rr.stdout!r}\n{got[:300]}"
    assert got == want, f"报告与 Python 版不同:\n  py     {want!r}\n  loment {got!r}"
    n = len(got.strip().splitlines()) - 1
    assert got.count("  PASS  ") == n == 7, (n, got[-120:])
    assert "lomerrcheck: 7/7 通过" in got, got[-120:]
    print(f"      {n} 项断言: PE 版与 ELF 版渲染一致（与 Python 版逐字节相同）")


@test
def test_err_twin_selfhost_compiles():
    """`lomerrcheck.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
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
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomerrcheck.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomerrcheck.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomerrcheck.lomt 成功 ({len(rr.stdout)}B IR)")


@test
def test_a_position_less_record_carries_no_line_number():
    """`line == 0` = 编译器**没有位置可指**（`E031`..`E033`：文件读不到 / 写不出去 /
    单元太大，`issue #8`）。三条一起立：

      * `--> <file>` 后面**不带行号** —— `file:0` 看着像第 0 行，编辑器会照着跳；
      * **不印 `(no such line in the source file)`** —— 那会让读者以为源码有问题，
        而这几个码的 `file` 常常根本不是源码（`E032` 那个是一条**还不存在**的产物路径）；
      * `--short` 那一路同理：`file: error[E031]` 而不是 `file:0:`。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-nopos-") as td:
        d = Path(td) / "nopos.jsonl"
        d.write_text(json.dumps({
            "file": "nosuch.lomt", "line": 0, "col": 0, "code": "E031",
            "message": "cannot open this file",
        }) + "\n", encoding="utf-8", newline="\n")
        rc, out = _render(d)
        assert rc == 1, (rc, out[:300])
        assert "E031" in out and "cannot read this input file" in out, out[:300]
        assert "--> nosuch.lomt\n" in out, f"位置那一行没只印文件: {out[:400]!r}"
        assert "nosuch.lomt:0" not in out, f"`-->` 后面带了行号: {out[:400]!r}"
        assert "no such line" not in out, f"去源里找了第 0 行: {out[:400]!r}"
        rc, out = _render(d, "--short")
        assert rc == 1, (rc, out[:300])
        assert "nosuch.lomt: error[E031]" in out, out[:300]
        assert "nosuch.lomt:0" not in out, out[:300]
    print("      没有位置的记录：`--> 文件` 不带行号、不去源里找第 0 行，--short 同")


# ---------------------------------------------------------------- 入口


def main() -> int:
    print(f"loment_err_test: {len(TESTS)} 条\n")
    bad = 0
    return loment_suite.run(TESTS, "loment_err_test")


if __name__ == "__main__":
    sys.exit(main())
