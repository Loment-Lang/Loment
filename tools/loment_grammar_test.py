#!/usr/bin/env python3
"""loment_grammar_test.py — **`choose write grammar`：读法由声明决定，不由嗅探**（`docs/188`）。

用户 2026-09-18 要的那个"伟大的开关"：

> 或许，我们加入那个伟大的开关 / choose / 它回来了 / choose write grammar 语言
> 硬写法，和 std/no_std 同级，直接 lock 锁住，弱的地方终于能动了
> **grammar py 和 grammar python 都是完全可以写的**，Potato 做错了，但为 let 语句铺了路

## 它治的是什么

`docs/188` §2：源侧**可选**、对象侧**必填**。没写就是 Loment —— 99% 的文件是 Loment，
每份写一遍是噪声，而"缺 = Loment"**没有歧义**。这一下把 `detect_lang` 的**嗅探**
（"有 `#include` 之类预处理指令"）换成**声明**；兜底从"猜"变"拒绝"。

## `lock` 是**出厂锁**，不是 `docs/182` §3 那个"装进本机"

| | `docs/182` §3 的 `lock` | 这里的出厂锁 |
|---|---|---|
| 装到哪 | 用户目录（`.lomlock`） | 无 —— 编译进工具链 |
| 谁改得动 | 用户（`chooseunlock`） | **没人** |
| 阶段 | C3，**未实现** | 现在就能做 |

**所以 grammar 落地不依赖 C3** —— 出厂锁没有"本机状态"这回事，而
"本机状态"正是那条线卡住的地方。`docs/182` §3.1 那条冲突（锁与本机绑定、破坏
"同一份源 → 同一串字节"）**同样不适用**：这张表在每台机器上都是同一份。

用法: python tools/loment_grammar_test.py
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
import lomelf       # noqa: E402
import lomentc      # noqa: E402
import lomt_from    # noqa: E402
import potato       # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


#: **出厂锁的那张表**（`docs/188` §1.1）—— 在这里**钉死**一份字面量。
#:
#: 这不是"测试写死了实现"：**锁的意思就是它不该随便动**。谁要加一门语言，
#: 就得**同时**改这里 —— 那一步是"改锁"，而不是"顺手加个分支"。
#: （用户提的"将来可以用哈希把它钉死"，钉的就是这一刻；现在先用一条判据钉。）
LOCKED_ALIASES = {
    "loment": "loment",
    "c": "c",
    "py": "python", "python": "python",
    "java": "java",
    "cs": "csharp", "csharp": "csharp", "c#": "csharp",
    "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "cc": "cpp",
    "go": "go", "golang": "go",
    "rs": "rust", "rust": "rust",
    # `docs/197` 加的那一门 —— **这一笔就是"改锁"**。
    # 规范名是 `natural` 而不是 `lument`：后者与 `loment` 只差一个字母，而
    # `grammar loment` 是合法的（= 原生写法）。近邻词当规范名，代价是
    # "少打一个字母就静默换成另一种读法"，而那正是这一门最不该有的失败。
    "nl": "natural", "natural": "natural", "lument": "natural",
}


@test
def test_the_factory_lock_table_is_exactly_this():
    """**出厂锁的取值表就是这一份** —— 加一门语言要**改这里**，那是"改锁"。

    `docs/188` §1.1：这张表由官方给，**用户不能扩展、不能覆盖、不能解锁**。
    所以判据把它当成一份**锁住的值**来核对，而不是"从别处推出来"。
    """
    got = potato_from.GRAMMAR_ALIASES
    diff = [(k, LOCKED_ALIASES[k], got[k])
            for k in sorted(set(got) & set(LOCKED_ALIASES)) if got[k] != LOCKED_ALIASES[k]]
    assert got == LOCKED_ALIASES, (
        "出厂锁的取值表被改了。**如果是有意加语言**，那就同时把上面那份字面量一起改"
        "（那一步就是「改锁」，得是显式的一笔）；**如果不是**，改回去：\n"
        f"  多了: {sorted(set(got) - set(LOCKED_ALIASES))}\n"
        f"  少了: {sorted(set(LOCKED_ALIASES) - set(got))}\n"
        f"  改值: {diff}")
    print(f"      出厂锁的取值表 {len(got)} 条拼法，与钉住的那份一致")


@test
def test_canonical_names_are_exactly_the_object_side_vocabulary():
    """**规范名只许是 `potato.GRAMMARS` 那个集合** —— 别名表只有一处，规范名也只有一处。

    `docs/188` §1.2：源侧宽松（`py` / `python` 都收）、**对象侧只许一个拼法**，
    否则同一份源出两串字节，判据当场红。而那一个拼法是谁定的？是 `potato` 的
    `GRAMMARS`（v6 校验器认的那几个）。两处对不上的症状是：源里能写、对象却**非法**。
    """
    canon = set(potato_from.GRAMMAR_ALIASES.values())
    want = set(potato.GRAMMARS)
    assert canon == want, (
        f"别名表的规范名与 `potato.GRAMMARS` 不一致\n"
        f"  别名表里有而 GRAMMARS 没有: {sorted(canon - want)}\n"
        f"  GRAMMARS 里有而别名表没有: {sorted(want - canon)}")
    print(f"      规范名 {len(canon)} 个，与 `potato.GRAMMARS` 逐字一致")


@test
def test_aliases_spell_the_same_canonical_name():
    """**同一门的几种拼法进对象只许剩一个**（用户点名的那一条）。

    用户原话："**grammar py 和 grammar python 都是完全可以写的**" —— 源侧随意，
    进对象必须是规范名，否则同一份源在两台机器上会出两串字节。
    """
    for spellings, canon in ((("py", "python"), "python"),
                             (("c++", "cpp", "cxx", "cc"), "cpp"),
                             (("c#", "cs", "csharp"), "csharp"),
                             (("golang", "go"), "go"),
                             (("rs", "rust"), "rust"),
                             (("loment",), "loment"),
                             (("c",), "c"),
                             (("java",), "java")):
        for s in spellings:
            g, err, declared = potato_from.read_grammar_decl(f"choose write grammar {s}\n")
            assert (g, err, declared) == (canon, None, True), (s, g, err, declared)
    # 大小写不收人 (源侧宽松) —— 但进对象的一定是小写规范名
    g, err, _ = potato_from.read_grammar_decl("choose write grammar Python\n")
    assert (g, err) == ("python", None), (g, err)
    print("      `py`/`python` -> python；`c++`/`cpp`/`cxx`/`cc` -> cpp；大小写不收人")


@test
def test_the_declaration_decides_the_reading_not_the_sniff():
    """**读法由声明决定** —— 同名后缀、不同内容，声明说了算。

    三件事：

    1. 一份 `.lomt` 装着 Python，**不写声明**时靠嗅探（`def` 那条）认出 python；
    2. 同一份内容**写了声明**时按声明走（这里是把它指到 `c`，而它是 Python 写法 ——
       **声明就是要压过嗅探**，压不过的话这个开关等于没有）；
    3. **声明压过后缀**：一份 `.py` 写着 `grammar c`，读法就是 `c` ——
       后缀是命名习惯、会说谎，而声明是**作者对这份文件说的**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        py_src = "def f(a: int) -> int:\n    return a + 1\n"

        # ① 没声明 -> 嗅探出 python
        a = td / "a.lomt"
        a.write_text(py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(a)[0] == "python", potato_from.resolve_lang(a)

        # ② 有声明 -> 按声明（**故意指到 c，看它压不压得过嗅探**）
        b = td / "b.lomt"
        b.write_text("choose write grammar c\n" + py_src, encoding="utf-8", newline="\n")
        got = potato_from.resolve_lang(b)
        assert got[0] == "c", f"声明压不过嗅探: {got}"
        assert "声明" in got[1], got

        # ③ 声明压过后缀
        c = td / "c.py"
        c.write_text("choose write grammar c\n" + py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(c)[0] == "c", potato_from.resolve_lang(c)

        # ④ 没声明时后缀仍然说话（`.py` -> python）
        d = td / "d.py"
        d.write_text(py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(d)[0] == "python", potato_from.resolve_lang(d)
    print("      声明压过嗅探、也压过后缀；没声明时后缀与嗅探照旧")


@test
def test_bad_declarations_are_loud():
    """**写错要报出来，而且指的要对** —— 位置、次数、取值三档各有各的话。"""
    cases = [
        ("module m\nchoose write grammar python\n", "之前"),      # 在 module 之后
        ("choose write grammar py\nchoose write grammar c\n", "一次"),  # 写两次
        ("choose write grammar rustc\n", "出厂锁"),                # 拼法不在表里
        ("choose write grammar\n", "语法名"),                      # 后面没写
    ]
    for src, want in cases:
        g, err, declared = potato_from.read_grammar_decl(src)
        assert err is not None, f"{src!r} 该报错却过了"
        assert want in err, f"要点名 `{want}`: {err}"
        assert declared is False, declared
    # 取值那一档**要把可写的列出来**（不然用户只知道"不行"、不知道"写什么行"）
    _, err, _ = potato_from.read_grammar_decl("choose write grammar rustc\n")
    for alias in ("python", "cpp", "csharp", "go"):
        assert alias in err, f"报错里该列出可写拼法: {err}"
    print("      `module` 之后 / 写两次 / 拼法不在表里 / 后面没写 —— 四档都报得出")


@test
def test_declaration_is_stripped_before_the_target_parser_sees_it():
    """**声明要在交给目标语言的解析器之前抹掉，而且行号不许动。**

    `choose write grammar python` **不是合法的 Python**（也不是合法的 C / Go / …）——
    声明是**读法**，不是那份源的一部分。不抹的话，一份写得好好的 Python 会在第一行
    就被目标解析器判成语法错。

    抹法是**等长空白**（保留换行），所以目标语法报错的行号仍然指回这份文件里的那一行。

    **这一条还有下半截**：抹完真的要能**跑起来** —— 一个数，两边对得上。
    """
    src = "choose write grammar python\n" + "def f(a: int) -> int:\n    return a + 1\n"
    stripped = potato_from.strip_grammar_decl(src)
    assert len(stripped) == len(src) and stripped.count("\n") == src.count("\n"), \
        "抹声明不能改长度/行数（改了就说明行号会漂）"
    assert "choose" not in stripped, "声明没抹干净"
    assert "python" not in stripped.split("\n")[0], "别名那一截没抹掉"

    # **下半截**：端到端跑一遍（声明在 -> 认得出来 -> 翻成 Loment -> 跑出 42）
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "unit.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        doc, rep = potato_from.transcribe(p, "auto", "strict")
        assert not rep.skipped, rep.skipped
        assert doc["grammar"] == "python", doc["grammar"]
        text, skipped = lomt_from.emit_lomt(doc, impl=True)
        assert not skipped, skipped
        l = td / "l.lomt"
        l.write_text(text + "\nfn _start() {\n    syscall4(60, f(41) as u64, 0, 0);\n}\n",
                     encoding="utf-8", newline="\n")
        mod = lomentc.load(l)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=l)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
        exe = td / "l.elf"
        exe.write_bytes(blob)
        if not _wsl():
            print("      （没有 WSL，跳过跑那一步）")
            return
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
                           capture_output=True, text=True, timeout=300)
    assert r.stdout.strip() == "42", f"跑出来该是 42，得到 {r.stdout.strip()!r}"
    print("      声明抹成等长空白、行号不动；端到端跑出 42")


@test
def test_the_declaration_word_order_is_the_shared_contract():
    """**词序是与报错器共享的契约** —— 那三个词和"读到空白或 `;` 为止"。

    `loment/tools/lomenterr.lomt` 的 `decl_at` 是个**独立的 Loment 程序**：它问不到
    `potato_from`，只能逐词匹配 `choose` / `write` / `grammar`，找不到就**退回嗅探**。
    破了不会报错，只会**静默失配** —— 症状正是当初补它要治的那个：

    > 一份 `choose write grammar python` 的 `.lomt`，**工具链知道是 Python，
    > 而报错器一个字都不说**。工具链知道、渲染器沉默，是最坏的一种。

    **这个契约现在走导出管线**（loment-dev-86 的 `14cb902`）：

        常量 -> 我的正则 -> `--dump-surface` 的 `decl_word(i)` -> 渲染器逐词吃

    所以"改词序"只有**一处**可改。这条判据钉的是**这个常量的语义**（词序 + 别名边界
    + 词边界），并顺手钉住**产物里就是这三个词、按这个顺序** ——
    它与对方那条"产物必须新鲜"的判据合起来，等于"渲染器拿到的就是常量里的词序"。
    对方那条（`loment_err_test::test_a_grammar_declaration_beats_content_sniffing`）
    钉的是另一件事："渲染器读得到"。**两条都在才有意义。**
    """
    assert potato_from.GRAMMAR_DECL_WORDS == ("choose", "write", "grammar"), (
        f"词序变了：{potato_from.GRAMMAR_DECL_WORDS}。**改了它就要同时改报错器的 "
        f"`decl_lang`**（`loment/tools/lomenterr.lomt`），否则它静默退回嗅探。")
    # 别名那一格的边界**也是契约**：读到空白或 `;` 为止（报错器那处同一刀切法）。
    # 下面四条把这条边界钉死 —— 尤其 `;` 那条：它同时保证 `c#` 里的 `#` **不是**注释头。
    for src, want in (
        ("choose write grammar python;\n", "python"),     # 分号收下（C 系语言的写法）
        ("choose write grammar python \n", "python"),     # 行尾空白
        ("choose write grammar c#\n", "csharp"),          # `#` 不是注释头
        ("choose write grammar python // 说明\n", "python"),
    ):
        g, err, declared = potato_from.read_grammar_decl(src)
        assert (g, err, declared) == (want, None, True), (src, g, err, declared)
    # **词边界那条也钉上**：拼错一个字母不能被当成声明，更**不能**被抹掉半行。
    # 这条是随后的**对拍**抓出来的真 bug（见 `_GRAMMAR_LINE` 的注解）：少了 `\b`，
    # `choose write grammars python` 会被切成 `                    s python` ——
    # 而预扫那边**不报错**（它不认这是声明），于是坏处全落在用户那一行上，
    # 他拿到的是一行残缺的源 + 一句指不到点子的语法错。
    for s in ("choose write grammars python\n", "choose writes grammar c\n"):
        g, err, declared = potato_from.read_grammar_decl(s)
        # **2026-10-09（#148）：这一格从"不报错"改成了"报错"。**
        # 原先那条断言钉的是"拼错**不能**被当成声明"（对），但它顺带把**静默退回**
        # 也钉住了 —— 而那正是 `docs/198` §2 记的缺陷（"§2 那句'兜底从**猜**变**拒绝**'
        # 正是为它写的；现在它既没猜也没拒，是**换了一个猜法**"）。所以这里改判：
        # 仍然 `loment` / 仍然 `declared=False`，但**要说出来**。
        # 下面那一句才是这条用例真正要保的东西，一字未动：拼错的那行**不许被抹掉半截**。
        assert g == "loment" and declared is False and err, (s, g, err, declared)
        assert potato_from.strip_grammar_decl(s) == s, f"拼错的那行被抹了半截: {s!r}"

    # **导出那半边也钉上**：产物里就是这三个词、按这个顺序。它 + 对方那条"产物必须新鲜"
    # 的判据合起来，等于"渲染器拿到的就是常量里的词序" —— 改常量而忘了重新生成，
    # 或者有人改了导出而没改常量，这条当场红，而不是等渲染器静默退回嗅探。
    #
    # **读产物文件，不 import `loment_diag`** —— 后者模块级 `import lomentc`，而那个文件
    # 常有别的会话在改；读产物既够用，也不把这条判据绑上"编译器此刻可导入"。
    surface = (ROOT / "loment" / "tools" / "surface_data.lomt").read_text(encoding="utf-8")
    words = potato_from.GRAMMAR_DECL_WORDS
    assert f"decl_word_count() -> u32 {{ return {len(words)}; }}" in surface, (
        f"导出里的词数与常量对不上（常量 {len(words)} 个）—— 改完常量要重新生成 "
        f"`loment/tools/surface_data.lomt`（`python tools/loment_diag.py --dump-surface`）")
    for i, w in enumerate(words):
        arm = f'    if i == {i} {{ return "{w}"; }}'
        assert arm in surface, f"导出里没有这一条（顺序也要对）: {arm!r}"
    print("      词序/别名边界/词边界 == 共享契约，且产物里就是这三个词、按这个顺序")


@test
def test_front_door_handles_the_loment_half_without_the_parser():
    """**前门：读法为 `loment` 时，抹掉声明那一行 —— parser 一行不动。**

    `choose write grammar loment`（写明了读法就是 Loment）与**没写声明**是**同一条路**：
    把那一行抹成等长空白，原样交给编译器。

    **为什么是抹、不是让 parser 认这个构造**（这个选择决定了要不要付语言面的双倍工）：

    * `choose` 在 Loment 里**已经是开关关键字**（`choose <名字>` / `choose close <名字>`），
      所以 `choose write grammar python` 会被读成"开关 `write`"，然后卡在 `grammar` 上
      —— 要让它成为真语法，就得在 lexer/parser 里加**特例**；
    * 而语言面有两个实现（`tools/lomentc.py` 与 `loment/selfhost/*.lomt`），
      按 `CLAUDE.md` 要一起改、**并且重生成种子**（那笔机械提交里 46 KB 的 IR 整体位移）。
    * **抹掉**只在**前端**改一处 —— 而"声明"本来就**不是那份源的一部分**
      （它说的是"**怎么读**"），所以它属于前端、不属于语法。

    **代价记在这里**：`lomfmt` / `lomdoc` / LSP 那些**直接读源**的入口仍会看到那一行。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # ① 声明 `loment`：抹掉那一行，其余原样
        a = td / "a.lomt"
        raw_a = "choose write grammar loment\nmodule m\n\npub fn f() -> i32 {\n    return 1;\n}\n"
        a.write_text(raw_a, encoding="utf-8", newline="\n")
        fa = potato_from.front_door(a)
        assert (fa.grammar, fa.translated) == ("loment", False), (fa.grammar, fa.translated)
        assert len(fa.source) == len(raw_a), "抹声明必须等长（行号才不漂）"
        assert "choose" not in fa.source, "声明那一行没抹掉"
        assert "module m" in fa.source and "pub fn f()" in fa.source, "正文该原样留着"

        # ② **没写声明** —— 同一条路，而且**一个字都不动**
        b = td / "b.lomt"
        raw_b = "module m\n\npub fn f() -> i32 {\n    return 1;\n}\n"
        b.write_text(raw_b, encoding="utf-8", newline="\n")
        fb = potato_from.front_door(b)
        assert (fb.grammar, fb.translated) == ("loment", False), (fb.grammar, fb.translated)
        assert fb.source == raw_b, "没声明时不该动一个字节"
    print("      读法为 `loment`：抹掉声明行（等长）；没声明时一字不动 —— parser 不参与")


@test
def test_declared_rust_is_the_base_syntax_not_a_foreign_module():
    """**`choose write grammar rust` = 基础语法的一种拼法**（用户 2026-09-22 的裁定）：

    > rust 语法是 Loment 基础语法，不需要翻译

    ## 改之前的样子（这条判据治的就是它）

    `rust` 在**两处**都登记着 —— 出厂锁 `GRAMMAR_ALIASES` 的右列（== `potato.GRAMMARS`）
    与 `front_door` 的 `LANGS`。于是**源码里能写、对象也合法**，可 `lomt_from._TOOLS` 里
    **没有 rust 这一门**（`from_rust` 是 `docs/179` 抽接口那条路：只记声明、**不抓正文**）。
    结果：

    * 前门把这份源交给 `from_rust` → 对象里一个 `body` 都没有 → `emit_lomt(impl=True)`
      逐个跳过 → `NotRepresentable`，**文件被拒**；
    * 而那句跳过理由还劝人「带正文的加 `--impl` 翻出来」—— 前门**已经**用的 `impl=True`。
      一条登记在锁里的写法，**没有任何一份源编得过去，报的话还指反**。

    ## 钉三件

    1. **声明为 rust 与没写声明是同一支**：`translated=False`、产物里没有生成器包头、
       正文一字不动。这同时排除"它被 `from_rust` 读过"—— 那条路的产物是**生成**的
       （包头 `// 由 tools/lomt_from.py 从 Potato 形式对象生成`）。
    2. **命令行上真编得出 IR**（`docs/188` §7.2 那条教训：**入口不止一处**，
       库那侧全绿而命令行上抛栈）—— 而且看的是 `.ll` 里真有那个函数，不只是没报错。
    3. **`.rs` 那条路没被一起改掉**：后缀说"这是 Rust 的文件"照旧走 `from_rust` 抽接口。
       **声明与后缀分工不同**（`docs/188` §2）—— 少了这一条，下一次"顺手统一"
       就会把接口那条路悄悄换成原生读法，而 `kernel/src/*.rs` 与 `lompotc --rust`
       的孪生判据全靠它。对照面是 `resolve_lang`（**前端工具**的入口，它照旧按后缀说话）。
    """
    body = "module m\n\npub fn f(x: i32) -> i32 {\n    return x * 3;\n}\n"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        a = td / "a.lomt"
        a.write_text("choose write grammar rust\n" + body, encoding="utf-8", newline="\n")
        fa = potato_from.front_door(a)
        assert (fa.grammar, fa.translated) == ("loment", False), (fa.grammar, fa.translated)
        assert "从 Potato 形式对象生成" not in fa.source, "它被当外国模块翻过了"
        assert fa.source.endswith(body), "正文没原样留着"
        assert fa.source[:len(fa.source) - len(body)].strip() == "", "声明那一行该抹成空白"
        assert len(fa.source) == len("choose write grammar rust\n" + body), "抹声明必须等长"

        # 对照面：**另一个单元**（同内容、不写声明）。两个实现一起错时"自举 == 参考"
        # 照样绿（`docs/182` §1.9），所以比的得是这份源自己。
        b = td / "b.lomt"
        b.write_text(body, encoding="utf-8", newline="\n")
        assert potato_from.front_door(b).source == body, "没声明那份被动过了"

        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            "--print", "llvm", str(a)],
                           capture_output=True, text=True, shell=False)
        out = (r.stdout or "") + (r.stderr or "")
        assert r.returncode == 0, f"`grammar rust` 该编得过：{out[-300:]}"
        assert "Traceback" not in out, f"抛栈了 —— 话到不了用户眼前：\n{out[-400:]}"
        assert "define " in out and "@f(" in out, f"IR 里没有那个函数：{out[-300:]}"

        rs = td / "x.rs"
        rs.write_text("pub fn g(a: i32) -> i32 { a + 1 }\n", encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(rs)[0] == "rust", "`.rs` 的后缀读法被改掉了"
        assert potato_from.LANGS["rust"] is potato_from.from_rust, "`.rs` 那条路被换掉了"
        assert "rust" not in lomt_from._TOOLS, (
            "`rust` 出现在 `_TOOLS` 里了 —— 它是**基础语法**，不该有翻译器"
            "（用户 2026-09-22 的裁定）")

        # `NATIVE_GRAMMARS` 是**出厂锁里的一个子集**（原生读法的那几个）—— 它不该冒出
        # `potato.GRAMMARS` 之外的名字，否则"源里能写"与"对象里合法"就分家了。
        assert "loment" in potato_from.NATIVE_GRAMMARS
        assert set(potato_from.NATIVE_GRAMMARS) <= set(potato.GRAMMARS), (
            f"{potato_from.NATIVE_GRAMMARS} 里有不在 `potato.GRAMMARS` 里的名字 —— "
            f"那个名字写不进对象，源侧与对象侧就分家了")
    print("      `grammar rust`：原生读法、命令行上真编出 IR（`.rs` 那条接口路不受影响）")


@test
def test_front_door_translates_a_foreign_grammar_into_loment_in_process():
    """**读法是别的写法时，前门把它翻成 Loment —— 在本进程里算，不拉起那个语言。**

    这是用户那条死要求的落地：

    > Loment 在没有使用 `let py` 这行代码的情况下，**不会拉起 python 或其他任何编译器**，
    > 它会让 potato 去翻译，而 Potato 怎么翻译，建立在**算法**上。

    所以断言两件：

    * 前门吐出来的**是 Loment**（`module` + `pub fn`，**一条 `extern fn` 都没有**）；
    * 它**能编译、能跑**，数还对 —— 端到端。只断言文本的话，"翻出一份**看着像** Loment 的
      东西"也会过（`docs/186` §1 那条）。
    """
    python_src = "choose write grammar python\n\ndef entry() -> int:\n    return 42\n"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "unit.lomt"
        p.write_text(python_src, encoding="utf-8", newline="\n")
        fu = potato_from.front_door(p)
        assert (fu.grammar, fu.translated) == ("python", True), (fu.grammar, fu.translated)
        assert "module " in fu.source, fu.source[:300]
        assert "pub fn entry() -> i64 {" in fu.source, fu.source[:300]
        assert "pub extern fn " not in fu.source, fu.source[:300]

        l = td / "l.lomt"
        l.write_text(fu.source + "\nfn _start() {\n    syscall4(60, entry() as u64, 0, 0);\n}\n",
                     encoding="utf-8", newline="\n")
        mod = lomentc.load(l)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=l)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"前门翻出来的 Loment 检查不过: {errs[:3]}"
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
        exe = td / "l.elf"
        exe.write_bytes(blob)
        if not _wsl():
            print("      （没有 WSL，跳过跑那一步）")
            return
        s = str(exe.resolve()).replace("\\", "/")
        w = s if s.startswith("/") else "/mnt/" + s[0].lower() + s[2:]
        r = subprocess.run([*_WSLCMD, "bash", "-lc", f"chmod +x {w} && {w}; echo -n $?"],
                           capture_output=True, text=True, timeout=300)
    assert r.stdout.strip() == "42", f"跑出来该是 42，得到 {r.stdout.strip()!r}"
    print("      别的写法 -> 翻成 Loment（无 extern fn）-> 真编真跑出 42")


@test
def test_front_door_refuses_loudly_when_a_unit_cannot_be_translated():
    """**翻不出来的单元要拒，不能给一份"少算一步却照样能编"的。**

    那一门是**全有或全无**的（一份源里只要有一个子集外的函数，整份就翻不了）。
    **两层都会拒，而这条判据只要求"拒得响亮"**（不锁是哪一层）：

    * `lomt_from.emit_lomt(…, impl=True)` 在**翻译器**报子集外时**自己**就抛
      `NotRepresentable`（实测走的是这一路）；
    * `front_door` 自己那一句查的是 `skipped` —— **函数之外的**东西（类型/常量）发不出来时
      它可能是空的错、`skipped` 才有内容。那一支留着当第二道闸。

    **不锁层是刻意的**：锁了就变成"钉实现的内部路径"，而这条判据要钉的是**行为**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "bad.lomt"
        # `string` 是 C# 里最常见的拼法，而本语言没有字符串值
        p.write_text("choose write grammar c#\n\nnamespace D\n{\n    public class T\n    {\n"
                     "        public static int f(string s)\n        {\n            return 1;\n"
                     "        }\n    }\n}\n", encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(p)
        except (lomt_from.NotRepresentable, ValueError) as e:
            assert "string" in str(e), f"要点名那一处: {e}"
            print(f"      翻不出来时拒得响亮: {str(e)[:80]}")
        else:
            raise AssertionError("子集外的一份被前门放过去了 —— 那是一份少算一步的单元")


@test
def test_lomentc_load_goes_through_the_front_door():
    """**接线那一行也得钉住** —— 否则谁重写 `lomentc.py`，它就悄悄没了。

    前门（`potato_from.front_door`）本身在别处判过（上面两条）—— 这条钉的是
    **`lomentc.load` 真的走了它**：一份 `choose write grammar python` 的 `.lomt`
    能被 `lomentc.load` **直接读进来**，而且读出来的是**翻译后的** Loment。

    **为什么要单独一条**：接线只有一行，而 `lomentc.py` 是**共享文件**（多会话在改）。
    没有这条判据的话，那一行被人从旧副本整体覆盖掉时**没有任何东西会红** ——
    症状是"这功能昨天还好好的，今天又不行了"，而查起来要从前门一路查到 `load`。

    另外钉住 `mod.src`：翻译出来的单元**不声称**源文件是那一份 `.lomt` ——
    `mod.src` 只喂 DWARF 的 `!DIFile`，而**行号来自翻译后的正文**，
    声称了就成了一句"把调试器引向错行"的话（`docs/179` §6.5 那一类）。
    """
    python_src = "choose write grammar python\n\ndef entry() -> int:\n    return 42\n"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "unit.lomt"
        p.write_text(python_src, encoding="utf-8", newline="\n")
        # **直接 `load`** —— 不先手工 `front_door`。它读出来的就该是翻译后的 Loment
        mod = lomentc.load(p)
        assert [f.name for f in mod.funcs] == ["entry"], \
            f"`load` 没把 entry 读出来: {[f.name for f in mod.funcs]}"
        assert mod.funcs[0].ret == "i64", mod.funcs[0].ret
        assert mod.src is None, (
            f"翻译出来的单元不该声称源文件是 {mod.src} —— 行号来自翻译后的正文，"
            f"而 `mod.src` 只喂 DWARF 的 `!DIFile`（会指向错行）")

        # **另一半**：一份**本来就是 Loment** 的文件，`src` 照旧记着（DWARF 那半边不能一起丢）
        q = td / "plain.lomt"
        q.write_text("module m\n\npub fn f() -> i32 {\n    return 1;\n}\n",
                     encoding="utf-8", newline="\n")
        assert lomentc.load(q).src == q, "本来就是 Loment 的文件，`src` 该照旧记着"
    print("      `lomentc.load` 走前门：python 写法的 `.lomt` 读得进来、`src` 不谎报；"
          "而 Loment 那份照旧记账")


@test
def test_a_front_door_refusal_reaches_the_cli_as_an_error_not_a_traceback():
    """前门拒的那几档，走**命令行**要**报错**，不是抛 traceback（2026-09-18 实测）。

    洞在**接线**上，不在前门：`front_door` 抛得挺对（`FrontDoorRefused` /
    `NotRepresentable`），但 `lomentc` 的两个调用点**都没人接** —— 于是上面那些
    判据全绿（它们直接调函数），而用户在命令行上看到的是 Python 栈，
    **一句给人看的话都到不了他眼前**。这与 `docs/182` §1.9 是同一个形状：
    **判据钉了函数，没钉入口。**

    修法拦在**唯一入口** `lomentc.load_unit`（那一处把这一支翻成编译器自己的
    `LomError`，八个调用点于是都按既有方式报错），所以这条钉的是 **CLI 的输出**，
    不是某一行代码。

    三档各来一次（前门的两个 raise 点 + 翻译器那一支）：
      * 别名不在出厂锁里（`FrontDoorRefused`）；
      * 后缀不认识、内容也认不出来（同一个 raise 点，另一句话）；
      * 拼法认得出、可这门拼法**翻不出来**（`NotRepresentable`，例：`~` —— 本语言没有）。
    """
    cases = [
        ("出厂锁", "gbad.lomt",
         "choose write grammar klingon\nmodule t\n\nfn main() -> u32 {\n    return 0;\n}\n",
         "不在出厂锁"),
        ("认不出来", "gunknown.zzz", "zzz ??? !!! 这不是任何一门语言\n", "都没命中"),
        ("翻不出来", "gtilde.java",
         "public class T {\n    public static int f(int v) {\n        return ~v;\n    }\n}\n",
         "~"),
    ]
    with tempfile.TemporaryDirectory() as t:
        for label, name, text, want in cases:
            p = Path(t) / name
            p.write_text(text, encoding="utf-8", newline="\n")
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                                "--check", str(p)],
                               capture_output=True, text=True, shell=False)
            out = (r.stdout or "") + (r.stderr or "")
            assert r.returncode != 0, f"[{label}] 这份源该被拒，却退了 0：{out[-200:]}"
            assert "Traceback" not in out, f"[{label}] 抛栈了 —— 话到不了用户眼前：\n{out[-400:]}"
            assert "[ERR]" in out and name in out, f"[{label}] 没报到点上：{out[-300:]}"
            assert want in out, f"[{label}] 话里该有 {want!r}：{out[-300:]}"
    print("      前门拒的三档：命令行上都是 [ERR] + 原因（没有 traceback）")


@test
def test_a_dot_lomt_is_never_sniffed_into_another_language():
    """**`.lomt` 不嗅探** —— 一份忘了写 `module` 的 Loment 文件不许被"猜"成别的语言。

    这是 `docs/188` §2 的正题：

    > 这一下把 `detect_lang` 的**嗅探**换成**声明**；兜底从"猜"变"拒绝"。

    嗅探在这里**会翻错语言**，而且症状很难查：一份没有 `module` 的 Loment 文件里有
    `pub fn f() …`，而 `detect_lang` 的 `_RS_FN` 认 `fn` ⇒ 判成 **rust** ⇒ 前门按 Rust 去翻
    ⇒ 用户拿到的是一句**Rust 翻译错**，而真正的原因是"这不是（或者还不完全是）Loment"。

    **分工要说清**：`potato_from.resolve_lang`（前端工具的入口）**照旧嗅探** ——
    那里"猜一个、并把猜了什么报出来"是有用的；而**编译器要求显式**。
    """
    rust_looking = "pub fn f() -> i32 {\n    return 1;\n}\n"     # 没有 module 行
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "nomodule.lomt"
        p.write_text(rust_looking, encoding="utf-8", newline="\n")
        # 前端工具那条路**会**嗅探（这是它的分工，不是 bug）
        assert potato_from.resolve_lang(p)[0] == "rust", potato_from.resolve_lang(p)
        # **而前门不嗅探** —— 它当它是 Loment（缺声明 = Loment），原样交出去
        fu = potato_from.front_door(p)
        assert (fu.grammar, fu.translated) == ("loment", False), (fu.grammar, fu.translated)
        assert fu.source == rust_looking, "前门不该动它一个字节"
        # 于是编译器报的是"这句 Loment 读不过"，而不是"Rust 翻不过去"
        try:
            lomentc.load(p)
        except Exception as e:                                     # noqa: BLE001
            msg = str(e)
            assert "module" in msg, f"该报的是「以 module 开头」那条: {msg[:120]}"
        else:
            raise AssertionError("没有 module 的 `.lomt` 居然读得过去")
    print("      `.lomt` 不嗅探（`.py` 那种才按后缀/内容走）；报的是 Loment 自己的错")


@test
def test_load_unit_takes_the_front_door_too_not_just_load():
    """**唯一入口 `load_unit` 也要过前门** —— 因为**开关预扫**自己读一遍源。

    2026-09-18 **命令行上撞到的**（`lomentc --check`），而编译链那一侧的判据全绿：

        未定义的开关 `write` —— 先写 `set choose write { … }` 定义它

    原因是 `choose` 在 Loment 里**已经是开关关键字**，`choose write` 被读成**取开关 `write`**
    —— 而预扫（`prescan_switches` 里那个 `scan`）**自己** `read_text` 了一遍，
    没经过前门。只接 `load` 是不够的。

    ⇒ **这正是 `docs/182` §1.9 那张"读 L1 源的入口"清单的形状：入口不止一处。**
    所以这条判据钉的是 `load_unit`（那条路**含预扫**），而不是 `load` ——
    上一条钉的是 `load`，两条加起来才是"整条装载链都过前门"。
    """
    python_src = "choose write grammar python\n\ndef entry() -> int:\n    return 42\n"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "unit.lomt"
        p.write_text(python_src, encoding="utf-8", newline="\n")
        mod, deps = lomentc.load_unit(p, ROOT)      # 含预扫那一趟
        assert [f.name for f in mod.funcs] == ["entry"], \
            f"预扫那趟没过前门（多半是「未定义的开关 write」）: {[f.name for f in mod.funcs]}"
    print("      `load_unit`（含**预扫**那趟）也过前门 —— 入口不止一处，两条判据各钉一个")


def _ref_find(src: str) -> tuple[bool, str | None, int]:
    """**独立写的参照实现**：按字符走一遍，**一行正则都不用**。

    它对着的是 `potato_from.find_decl` —— **只管"找"**那一层（有没有声明头、别名是什么、
    第几行），**不管**"必须在 `module` 之前 / 只许写一次 / 别名在不在出厂锁里"。
    分层是必要的：不分层的话，拿"融合了三条规矩的结果"去比"只管找的结果"，
    对拍会满屏**假分歧**（实测：`module m` 在前那一档，融合层报"必须在 module 之前"、
    找层说 python —— 两边都对，只是**答的不是同一个问题**）。

    loment-dev-86 把它压成了一句可操作的：**"分层让对拍有对象"** ——
    `find_decl` 是"找"、渲染器的 `decl_at` 也是"找"，**两边都是"找"，所以能对拍**。
    我原来的说法（"先问两边答的是不是同一个问题"）只说了一半：它告诉你要**检查**，
    这一句告诉你要**先分层**才能检查。

    规则（与报错器 `decl_at` 同一刀）：行首可跳空白 → `choose` ␣ `write` ␣ `grammar`
    （三个**空白分隔**的词）→ 第三个词后面只能是 **空白 / `;` / 行尾** → 别名是
    **跳过空白之后**到空白/`;`/行尾为止的一串；跳完空白就撞上 `;` 或行尾 ⇒ **有头没别名**。
    """
    line = 1
    for raw in src.split("\n"):
        i, n = 0, len(raw)
        while i < n and raw[i] in " \t":
            i += 1
        ok = True
        for word in ("choose", "write", "grammar"):
            if raw[i:i + len(word)] != word:
                ok = False
                break
            i += len(word)
            if i < n and raw[i] not in " \t;":
                ok = False                       # 词后面贴了别的字符 ⇒ 不是这个词
                break
            j = i
            while j < n and raw[j] in " \t":
                j += 1
            if word != "grammar" and j == i:
                ok = False                       # 词与词之间必须有空白
                break
            i = j
        if ok:
            if i >= n or raw[i] == ";":
                return True, None, line           # 有头、没写别名
            j = i
            while j < n and raw[j] not in " \t;":
                j += 1
            return True, raw[i:j], line
        line += 1
    return False, None, 0


def _grammar_case_set() -> set[str]:
    """候选行的**枚举** —— 抽出来给两条判据共用。

    两侧各枚举一遍必然漂：加一档只会落在其中一边，于是"覆盖不同、各自绿着却互不覆盖"。
    同一条纪律在 `_ref_find` 的字母表那段也写着（那是与 loment-dev-86 那份对齐）。
    """
    base = "choose write grammar python"
    lines: set[str] = {base, "  " + base, "\t" + base, "x" + base, "#" + base,
                       "// " + base, "/* " + base, "choose write grammars python",
                       "choosewrite grammar python", "choose writes grammar c",
                       "choose write grammar", "choose write grammar ",
                       "choose write grammar;", "choose write grammar python;",
                       "choose write grammar python // 说明",
                       "choose write grammar\tpython",
                       "choose write grammar python2", "choose write grammar Python",
                       base + "\n" + base,
                       "module m\n" + base, base + "\nmodule m"}
    # **分隔符 × 别名**：三处间隔换成空白/制表，别名换成每一种。
    for sep in (" ", "  ", "\t"):
        for alias in ("py", "python", "c#", "c++", "go", "python2", "", "Python"):
            lines.add(f"choose{sep}write{sep}grammar{sep}{alias}".rstrip())
    # **每处单字符替换**：这是"覆盖想不到的写法"的那一半 —— 手挑挑不出这些。
    #
    # 字母表是**并集**：loment-dev-86 在第三词后面单挑了 12 种分隔符
    # （`#` `.` `:` `,` `-` `_` `(` `!` `=` `@` `'` `"`，判据 `d763ffa`），
    # 我这份是"每个位置 × 这些字符" —— 所以**我这份严格覆盖他那份**。
    # 两边各钉一条时最怕的就是"覆盖不同、于是各自绿着却互不覆盖"；把字母表对齐就没有那一档。
    # （`_` 值得点出来：它是**词字符**，`grammar_python` 两边都判"没有声明头" ✓ 一致。）
    sep_alphabet = ("#", ".", ":", ",", "-", "_", "(", "!", "=", "@", "'", '"')
    for i in range(len(base)):
        for ch in ("x", " ", "\t", ";", *sep_alphabet):
            lines.add(base[:i] + ch + base[i + 1:])
    return lines


def _grammar_cases() -> list[str]:
    """排好序的候选行 —— **判据写 `cases.txt` 的顺序也就是报告的顺序**，两侧同此一份。"""
    return sorted(_grammar_case_set())


@test
def test_the_scanner_agrees_with_an_independently_written_reference():
    """**扫一批输入，与一个独立写的参照实现逐条对拍** —— 这是"重组是保义的吗"的答法。

    loment-dev-86 那句话说到点子上：**"改实现"的证据标准比"写实现"高** ——
    写的时候你只要证明新东西对，改的时候你要证明新旧**一样**，而"原有判据全过"
    恰恰是最容易骗人的那种证据（它们覆盖的是**行为**，不是**改动**）。

    这条就是那个标准的落地：不动手挑几条，而是**枚举**一批（原样 / 分隔符 / **每处
    单字符替换** / 别名各种拼法 / 前缀杂质 / 两次声明），拿**按字符走**写的参照实现与
    正则实现逐条比。手挑的表只能覆盖"想得到的写法"；枚举能覆盖到想不到的那些。

    **它当场抓到了两件真东西**：

    1. 参照自己写错过一处（`w[3:]` 应为 `w[len("grammar"):]`）—— 对拍**不保证谁对**，
       它保证**分歧会浮出来**；
    2. `choose write grammar#python` 这种"头后面贴着非空白非 `;`"的写法，
       **我的扫描器与报错器 `decl_at` 说不同的话**（我认成"有头没别名"→报错，
       它认成"没有声明"→退回嗅探）。于是把边界从 `\\b` 收紧成 **空白/`;`/行尾**
       —— 两边现在同一条规矩。

    **同时钉住最要命的那半**：参照说"根本没有声明"的行，`strip_grammar_decl` **一个字
    都不许动** —— 漏边界那次正是这里出的错（把 `choose write grammars python` 切成了
    `                    s python`，而预扫**不报错**，坏处全落在用户那行上）。
    """
    lines = _grammar_case_set()

    bad = []
    for ln in sorted(lines):
        src = ln + "\n"
        mine, ref = potato_from.find_decl(src), _ref_find(src)
        if mine != ref:
            bad.append((ln, mine, ref))
            continue
        # **参照说"根本没有声明头"时，那一行一个字都不许被抹掉。**
        # 只对这一档断言 —— "**有**头但写错了"（比如没写别名）那一档，`read_grammar_decl`
        # 会当场报错、走不到抹除，抹不抹都无害；而"根本没有头"那一档**抹了就是把用户的
        # 行切坏**，漏边界那次正是这里出的错。**这一档才要命。**
        if not ref[0]:
            after = potato_from.strip_grammar_decl(src)
            if after != src:
                bad.append((ln, "strip 动了不该动的行", repr(after)))
    assert not bad, (
        f"{len(lines)} 条里 {len(bad)} 条与参照对不上（前 5 条）：\n"
        + "\n".join(f"  {ln!r}\n    find_decl={m}\n    参照={r}" for ln, m, r in bad[:5]))
    print(f"      枚举 {len(lines)} 条，与独立写的参照逐条一致（含行号）；且无声明头时一个字未动")


# ---------------------------------------------------------------- Loment 版（S1 第十三格）

_TW = f"/tmp/loment-grammar-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomgrammar.lomt"


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


def _build_elf(src: Path, out: Path) -> Path:
    """仓库自己的前端发射 + clang 链成静态 ELF（走的是判据那一路, 不是自举链）。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = out.with_suffix(".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(out), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return out


def _escape_case(ln: str) -> str:
    """一条候选行 -> `cases.txt` 里的一行（`\\` / 换行 / 制表转义, 反斜杠先转）。

    多行 case（`base + "\\n" + base`）因此只占**一行** —— 两侧看的是同一份输入。
    """
    return ln.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def _write_cases(d: Path) -> None:
    (d / "cases.txt").write_text("".join(_escape_case(c) + "\n" for c in _grammar_cases()),
                                 encoding="utf-8", newline="\n")


def _grammar_report() -> str:
    """判据这一侧的报告：每个 case 拿**独立写的参照实现** `_ref_find` 算一遍。

    格式与孪生逐字相同：`序号 TAB 找到没有 TAB 行号 TAB 别名`（别名可以为空）。
    """
    out: list[str] = []
    for i, ln in enumerate(_grammar_cases()):
        found, lang, line = _ref_find(ln + "\n")
        out.append(f"{i}\t{1 if found else 0}\t{line}\t{lang or ''}")
    out.append(f"lomgrammar: {len(_grammar_cases())} 条")
    return "\n".join(out) + "\n"


def _grammar_divergence(got: str, want: str) -> str:
    """第一处分歧 —— 连带把**参考实现**的答案打出来, 好判"是 Loment 错还是参照错"。

    对拍**不保证谁对**, 它保证分歧会浮出来（`_ref_find` 头注里那句）；所以诊断里
    三方的答案要一起给, 不然看的人只能在两个数之间猜。
    """
    g, w = got.split("\n"), want.split("\n")
    n = max(len(g), len(w))
    for i in range(n):
        a = g[i] if i < len(g) else "<缺>"
        b = w[i] if i < len(w) else "<缺>"
        if a != b:
            cases = _grammar_cases()
            if i < len(cases):
                ln = cases[i]
                return (f"第 {i} 条 ({ln!r}):\n"
                        f"    loment = {a!r}\n"
                        f"    参照   = {b!r}\n"
                        f"    find_decl(参考实现) = {potato_from.find_decl(ln + chr(10))!r}")
            return f"第 {i} 行: loment={a!r} 参照={b!r}"
    return "(逐字节相同?)"


@test
def test_grammar_scanner_twin_matches_the_independent_reference():
    """**Loment 版**（`lomgrammar.lomt`）与判据那一侧的独立参照 `_ref_find` **逐字节相同**。

    `docs/189` §3 的 S1 第十三格（丙类）。同一批枚举 → 判据写成 `cases.txt`（转义后每条一行）
    → 孪生在 WSL 里读它、扫一遍、打出 `序号/找到没有/行号/别名`。

    这一格钉的是**声明扫描**那一层（"找"），不是"必须在 module 之前 / 只许写一次 /
    别名在不在出厂锁里"那一层 —— 分层之后才有对拍的**对象**（`_ref_find` 头注）。

    它是 `docs/189` §4.1 那根轴（六门翻译器的 Loment 孪生）要用的第一块砖:
    自举侧现在只收 `choose write grammar loment`（`driver.lomt` 的守卫）, 而扫描这一层
    可以先独立对拍到位。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    want = _grammar_report()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _write_cases(td)
        chk = _build_elf(TWIN, td / "lomgrammar.elf")
        outp = td / "check.out"
        binn = f"{_TW}chk.bin"
        script = (f"cp {_wsl_path(chk)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(td)} && {binn} > {_wsl_path(outp)} 2>&1; echo -n $?")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, text=True, timeout=300, shell=False)
        got = outp.read_bytes().decode("utf-8") if outp.exists() else ""
    assert rr.stdout.strip() == "0", f"孪生该退 0: rc={rr.stdout!r}\n{got[:400]}"
    assert got == want, "报告与独立参照不同 —— " + _grammar_divergence(got, want)
    n = len(_grammar_cases())
    assert got.count("\n") == n + 1, (got.count("\n"), n)
    assert f"lomgrammar: {n} 条" in got, got[-120:]
    print(f"      {n} 条候选行: Loment 扫描器与独立写的参照逐条一致（含行号与别名）")


@test
def test_grammar_twin_selfhost_compiles():
    """`lomgrammar.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
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
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomgrammar.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomgrammar.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomgrammar.lomt 成功 ({len(rr.stdout)}B IR)")


@test
def test_a_bom_does_not_hide_the_declaration():
    """文件开头的 **UTF-8 BOM** 不能让声明失效。

    Windows 的编辑器默认会写 BOM（记事本、"另存为 UTF-8"、PowerShell 的 `>`），
    而 `docs/188` §2 把"这份源怎么读"**整个压在那一行**上（`.lomt` 刻意**不嗅探**内容）。
    原先四条第 `_GRAMMAR_*` 正则都是 `^[ \\t]*`：BOM 不是空白 ⇒ `choose` 不在"行首"⇒
    **声明整个找不到** ⇒ 文件落到"缺声明 = 原生 Loment"，整份 C 被当 Loment 编，
    报出来是 `1:1 非法字符 '\\ufeff'` —— 用户完全看不出"是你那行声明没生效"。

    两边都钉：带 BOM 与不带 BOM **读出来的语法与译文必须一样**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        body = "choose write grammar c\nint main() { return 44; }\n"
        outs = []
        for tag, prefix in (("bom", "﻿"), ("nobom", "")):
            # **文件名要一样** —— 产物里的 `module <主干名>` 取自路径，
            # 换个名字比出来的差就不是 BOM 那一处了。
            d = td / tag
            d.mkdir()
            p = d / "t.lomt"
            p.write_bytes((prefix + body).encode("utf-8"))
            fu = potato_from.front_door(p)
            assert fu.grammar == "c" and fu.translated, (tag, fu.grammar, fu.translated)
            assert "choose write grammar" not in fu.source, (tag, fu.source[:80])
            outs.append(fu.source)
        assert outs[0] == outs[1], "带 BOM 与不带的产物不同"
    print("      带 BOM 的声明照常认到，产物与不带 BOM 的逐字节相同")


@test
def test_a_misspelled_declaration_is_refused_not_guessed_past():
    """**关键词写错 / 词序不对**要报，不能"当成没有声明"悄悄读成 Loment。

    `docs/188` §1 列了四种"声明写错"的报错（名字不在表里 / 写在 `module` 之后 /
    写两次 / 后面没写名字）—— **漏了"关键词本身写错"这一种**（`docs/198` §2 报过，
    当时未修）。这一种原先**一声不响**：文件落到"缺 = Loment"那一条，用户拿到的是
    正文里某处的语法错（`未知顶层关键字 'def'` 之类）—— **指的不是那一行**。

    §2 那句"兜底从**猜**变**拒绝**"正是为它写的；原先它既没猜也没拒，是**换了一个猜法**。

    两面都钉：四种写错**必须报**（且指出那一行的原文），而**真没写声明**的照旧回退
    到 Loment（那是 `docs/188` §2 的规矩：99% 的文件是 Loment，缺 = Loment 没有歧义）。
    """
    for bad in ("choose grammar c", "grammar c", "choose write grammars c", "choose language c"):
        lang, err, declared = potato_from.read_grammar_decl(bad + "\n\nint main() { return 7; }\n")
        assert err and not declared, (bad, err, declared)
        assert "第 1 行" in err and bad in err, (bad, err)
    # 真没声明的照旧回退
    assert potato_from.read_grammar_decl("module m\nfn f() { }\n") == ("loment", None, False)
    # 写对的照旧
    assert potato_from.read_grammar_decl("choose write grammar c\nint main(){}")[:1] == ("c",)
    # **合法的开关不算"写错的声明"**（它在 `module` 之后；写在之前本来就另有错）
    assert potato_from.read_grammar_decl("module m\nchoose gc_auto\nfn f() { }\n") == \
        ("loment", None, False)
    print("      四种写错的声明都报得出（并给出那一行原文）；缺声明照旧回退")


def main() -> int:
    return loment_suite.run(TESTS, "loment_grammar_test")


if __name__ == "__main__":
    sys.exit(main())
