#!/usr/bin/env python3
"""loment_comefor_test.py — `comefor`/`byuse` 展开的判据（S4.1，`docs/184` §9）。

**核心那条是 `test_minimal_dialect_expands_to_handwritten`**：一段用自定义语法的源，
编出的 IR 与"手写展开后"的源**逐字节相同**。那是 `docs/184` §9 给 S4.1 定的证伪判据
—— 不是"能跑通"，是**逐字节**。

其余几条钉的是**边界**：没有 `comefor` 的文件必须逐 token 原样通过（否则 S4.1 就悄悄
改了所有既有程序的产物）、`comefor` 不是保留字、体里的 `use` 有明确的拒绝、`byuse`
收错名字要报错、宏体多吃要报错。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import loment_comefor  # noqa: E402
import lomentc  # noqa: E402
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CF = ROOT / "loment" / "comefor"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ir(p: Path) -> str:
    mod, deps = lomentc.load_unit(p, ROOT)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{p.name} 检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


#: 没有 `comefor` 的文件 —— 逐个都要**逐 token 原样通过**。
_IDENTITY_FILES = ("loment/examples/bytes.lomt", "loment/selfhost/switches.lomt",
                   "loment/ct/strs.lomt", "loment/ct/offsets.lomt")


@test
def test_no_comefor_is_identity():
    """没有 `comefor` 的文件，展开前后**逐 token 同一批对象**。

    这条是"S4.1 不改变任何既有程序产物"的保证。展开器要是顺手 normalize 了什么
    （重建 token、丢字段、动顺序），这里当场红 —— 而 IR 判据未必看得出来
    （`docs/182` 的"消费方轴"：判据只跑它跑的那些）。
    """
    for rel in _IDENTITY_FILES:
        p = ROOT / rel
        txt = p.read_text(encoding="utf-8")
        a = lomc.lex(txt)
        b = loment_comefor.expand(list(a), txt)[0]
        assert len(a) == len(b), f"{rel}: token 数变了 {len(a)} -> {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            assert x is y, (f"{rel}: 第 {i} 个 token 不是同一个对象"
                            f"（{x.kind} {x.val!r} -> {y.kind} {y.val!r}）")
    print(f"      恒等: {len(_IDENTITY_FILES)} 支无 comefor 的文件逐 token 原样通过")


@test
def test_minimal_dialect_expands_to_handwritten():
    """**S4.1 的核心判据**：方言源与手写展开源编出的 IR 逐字节相同（`docs/184` §9）。

    这是"展开器真的对"的唯一硬证据 —— 别的判据都只说明"没崩"。它同时覆盖 §3.2 的
    四个内建（`ct_tok`/`ct_out`/`ct_syn`/`ct_n` 里前三个都用到了）与 §4 的位置构造。
    """
    got = _ir(CF / "def_dialect.lomt")
    want = _ir(CF / "def_hand.lomt")
    if got != want:
        gl, wl = got.splitlines(), want.splitlines()
        diff = []
        for i in range(max(len(gl), len(wl))):
            x = gl[i] if i < len(gl) else "<无>"
            y = wl[i] if i < len(wl) else "<无>"
            if x != y and len(diff) < 8:
                diff.append(f"  L{i + 1}\n    方言: {x.strip()}\n    手写: {y.strip()}")
        raise AssertionError(
            f"方言源与手写展开源产出的 IR 不同（want {len(want)}B got {len(got)}B）:\n"
            + "\n".join(diff))
    print(f"      逐字节: 方言源 == 手写展开源（{len(got)}B）")


@test
def test_comefor_is_shape_not_reserved_word():
    """`comefor`/`byuse`/`done`/`to` **都不是保留字**（`docs/158` §4 第 12 条）。

    判形状而不是判词 —— `let comefor: u32 = 1;` 与 `fn byuse() -> u32` 都该照编不误。
    按词判会把它们误伤，而这类误伤只有真写了才看得见。
    """
    txt = ("module shape_not_word\n"
           "fn byuse() -> u32 { return 7; }\n"
           "fn to() -> u32 { return 1; }\n"
           "fn done() -> u32 { return 2; }\n"
           "fn comefor(a: u32) -> u32 { return a; }\n"
           "fn main() -> u64 {\n"
           "    let comefor: u32 = 3;\n"
           "    return (byuse() + to() + done() + comefor(4) + comefor) as u64;\n"
           "}\n")
    toks = loment_comefor.expand(lomc.lex(txt), txt)[0]
    mod = lomentc.Parser(toks, txt).parse()
    assert [f.name for f in mod.funcs] == ["byuse", "to", "done", "comefor", "main"], \
        [f.name for f in mod.funcs]
    print("      形状判定: 四个词都能当标识符用")


@test
def test_body_rejects_use():
    """体里写 `use` 要**明确报错**，不是静默忽略（`docs/184` §11）。

    `use` 要的是文件级的依赖解析（相对路径 + 开关表），嵌在一段体里没有显然语义。
    静默忽略才是最坏的 —— 帮手找不到会变成 `undef`，指到一个看不出原因的地方。
    """
    txt = ("module body_use\n"
           "comefor let \"x\" to {\n"
           "    use bytes\n"
           "    fn main() -> u64 { return 0; }\n"
           "}\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)[0]
    except lomc.LomError as e:
        assert "use" in str(e), e
        print("      体里的 use: 明确拒绝")
        return
    raise AssertionError("体里写 `use` 应当报错，实际过了")


@test
def test_byuse_unknown_name_is_rejected():
    """`byuse "没定义过的" done` 要报错 —— 收一个不在用的方言是笔误，静默收下会掩盖它。"""
    txt = ("module byuse_unknown\n"
           "fn main() -> u64 { return 0; }\n"
           "byuse \"nope\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)[0]
    except lomc.LomError as e:
        assert "nope" in str(e), e
        print("      byuse 收错名字: 明确拒绝")
        return
    raise AssertionError("`byuse` 收一个没定义的名字应当报错，实际过了")


@test
def test_overconsumption_is_rejected():
    """宏体说它吃的比游标后面剩的还多 —— 要报错，不是让 `i` 跑过头。

    这条挡的是**宏体的笔误**（返回值写错）。放过去的话，游标会跳过文件尾，
    症状是"编译完了但产物是空的"，比一条指得出位置的错难查得多。
    """
    txt = ("module overconsume\n"
           "comefor let \"x\" to {\n"
           "    fn main() -> u64 { return 99; }\n"
           "}\n"
           "x 1;\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)[0]
    except lomc.LomError as e:
        assert "99" in str(e), e
        print("      宏体多吃: 明确拒绝")
        return
    raise AssertionError("宏体报的消费数超过剩余 token 应当报错，实际过了")


@test
def test_definition_must_be_top_level():
    """`comefor` 写在块里要报错（`docs/184` §1："这套块不能嵌套"）。

    写在函数体里定义出来的方言，作用域会从**函数的中间**开始，而收它的 `byuse` 在文件
    别处 —— 读的人看不出这两件事有关系。**定义**限顶层；**使用**不限（方言构造用在函数
    体里正是它的用处）。
    """
    txt = ("module nested_def\n"
           "fn helper() -> u64 {\n"
           "    comefor let \"x\" to {\n"
           "        fn main() -> u64 { return 0; }\n"
           "    }\n"
           "    return 1;\n"
           "}\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)[0]
    except lomc.LomError as e:
        assert "顶层" in str(e), e
        print("      块里的定义: 明确拒绝")
        return
    raise AssertionError("`comefor` 写在块里应当报错，实际过了")


@test
def test_entry_takes_no_params():
    """`fn main` 带形参要报错 —— 它要吃的东西全在游标上（`docs/184` §3.1）。

    不挡的话症状是跑起来报 `undef`（形参没有实参可绑），指在**体里面**；而真正的原因
    在签名上。**错要指在错的地方。**
    """
    # **必须真的用一次** —— 这条守卫在 `_run` 里（要跑才谈得上形参），
    # 只定义不使用的话它根本不经过。第一版就是这么写的，判据红了才知道。
    txt = ("module param_entry\n"
           "comefor let \"x\" to {\n"
           "    fn main(n: u64) -> u64 { return n; }\n"
           "}\n"
           "x 1 ;\n"
           "fn main() -> u64 { return 0; }\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)[0]
    except lomc.LomError as e:
        assert "形参" in str(e), e
        print("      main 带形参: 明确拒绝")
        return
    raise AssertionError("`fn main` 带形参应当报错，实际过了")


@test
def test_domain_is_the_builtin_table():
    """宏体里**表外的名字调不到** —— 这就是"编译期域"（`docs/184` §5.1，S4.2 的证伪判据）。

    `docs/184` §9 给 S4.2 定的判据是"`syscall4` 写在宏体里 -> **被拒**"。这条钉它。

    **域不是新加的一层**：两个解释器都只有**一张固定内建表**，表外的名字不是"被检查
    出来"，是**根本不存在**。所以 §5 原来担心的"宏体能不能 syscall / 能不能开文件"
    在这一版是**调不到**。

    这条的价值在于**它会拦住"内建表被顺手加宽"**：哪天真让 `syscall4` 可用了，
    这里当场红 —— 而那是**换了一个安全模型**，得有人明确决定，不能顺手带进来。
    """
    # 同一个体，换掉那一个名字：`alloc` 在内建表里（过），`syscall4` 不在（拒）。
    def body(call: str) -> str:
        return ("module dom\n"
                "comefor let \"d\" to {\n"
                "    fn main() -> u64 {\n"
                f"        let x: u64 = {call};\n"
                "        return x;\n"
                "    }\n"
                "}\n"
                "d 1 ;\n"
                "fn main() -> u64 { return 0; }\n"
                "byuse \"d\" done\n")

    ok = loment_comefor.expand(lomc.lex(body("alloc(16) as u64")), body("alloc(16) as u64"))[0]
    assert ok, "在表里的内建应当跑得通"
    for call, why in (("syscall4(60, 0 as u64, 0 as u64, 0 as u64)", "起进程/退出"),
                      ("open(1) as u64", "开文件")):
        txt = body(call)
        try:
            loment_comefor.expand(lomc.lex(txt), txt)[0]
        except lomc.LomError as e:
            assert "nobuiltin" in str(e), (call, e)
            continue
        raise AssertionError(f"{call}（{why}）不该在宏体里可用 —— 它不在内建表里，"
                             f"而这张表**就是**编译期域（`docs/184` §5.1）")
    print("      域 = 内建表: 表外的名字调不到（syscall / 开文件）")


@test
def test_potato_v4_carries_dialects():
    """产物里**看得出用了哪些方言** —— `docs/184` §9 给 S4.3 定的证伪判据。

    方言定义那一段在展开时被**抹掉**了，所以不带上产物的话，读产物的人（"不读源码的那
    一侧"，`docs/182` §1 里 Potato 存在的理由）**看不见**这份单元用了自定义语法。

    `body` 一起带上（`docs/184` §7 ②）：只记名字的话，读的人知道"用了方言 `def`"
    却不知道 `def` 是什么意思 —— 那正是"产物不自解释"。带上源文本，这一条就可重建。

    反向那一半同样重要：**没有方言的单元必须是空数组**，且校验器认 v4
    （`potato.validate`）—— 不然这条只是在自说自话。
    """
    mod, deps = lomentc.load_unit(CF / "def_dialect.lomt", ROOT)
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    # **不钉死版本号**：钉了就是每升一版红一次（v5 → v6 → v7 → v8 → v9 已经红过好几轮）。
    # 这条要的是"发的是**当前**那一版"，由 `potato.VERSIONS[-1]` 说了算 ——
    # 与 `lomentc_test` 那四条同一个写法。
    assert doc["potato"] == potato.VERSIONS[-1], doc["potato"]
    names = [d["name"] for d in doc["dialects"]]
    assert names == ["def"], f"方言清单应当是 ['def'], 实得 {names}"
    body = doc["dialects"][0]["body"]
    # `body` 是**定义处那段程序的源文本** —— 认它而不是认长度: 长度是巧合, 内容是契约。
    assert "ct_syn" in body and "fn main" in body, body[:200]
    assert potato.validate(doc) == [], potato.validate(doc)

    # 反向: 没有 `comefor` 的单元 -> 空数组（不是"缺这项"）
    plain = ROOT / "loment" / "examples" / "bytes.lomt"
    m2, d2 = lomentc.load_unit(plain, ROOT)
    doc2 = json.loads(lomentc.emit_potato(m2, ROOT, d2))
    assert doc2["dialects"] == [], doc2["dialects"]
    assert potato.validate(doc2) == [], potato.validate(doc2)
    print(f"      Potato v4: 方言进产物（{names[0]}, body {len(body)}B）; 无方言 -> []")


# ---------------------------------------------------------------- Loment 版（S1 第十格）

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-{os.getpid()}-"
TWIN = ROOT / "loment" / "tools" / "lomcomefor.lomt"
DRIVER = ROOT / "loment" / "selfhost" / "driver.lomt"

#: 判据写出来、孪生按**相对字面路径**读的那些输入（文件名是两个实现的接口）。
#: `deps/myhelper/` 是**给体里那句 `use` 制造一个能解析的目标** —— 早前用内建的
#: `use bytes` 时，导入解析会先失败（临时目录里到不了内建根），于是真正要测的那条
#: 守卫（体里不许 `use`）**根本轮不到**，报出来的是"名字导入找不到或有歧义"。
_CASES = {
    "shape_not_word.lomt": (
        "module shape_not_word\n"
        "fn byuse() -> u32 { return 7; }\n"
        "fn to() -> u32 { return 1; }\n"
        "fn done() -> u32 { return 2; }\n"
        "fn comefor(a: u32) -> u32 { return a; }\n"
        "fn main() -> u64 {\n"
        "    let comefor: u32 = 3;\n"
        "    return (byuse() + to() + done() + comefor(4) + comefor) as u64;\n"
        "}\n"),
    "bad_body_use.lomt": (
        "module body_use\n"
        "comefor let \"x\" to {\n"
        "    use myhelper\n"
        "    fn main() -> u64 { return 0; }\n"
        "}\n"
        "byuse \"x\" done\n"),
    "bad_unknown_byuse.lomt": (
        "module byuse_unknown\nfn main() -> u64 { return 0; }\nbyuse \"nope\" done\n"),
    "bad_overconsume.lomt": (
        "module overconsume\n"
        "comefor let \"x\" to {\n    fn main() -> u64 { return 99; }\n}\n"
        "x 1;\nbyuse \"x\" done\n"),
    "bad_not_top.lomt": (
        "module nested_def\n"
        "fn helper() -> u64 {\n"
        "    comefor let \"x\" to {\n        fn main() -> u64 { return 0; }\n    }\n"
        "    return 1;\n}\n"
        "byuse \"x\" done\n"),
    "bad_entry_params.lomt": (
        "module param_entry\n"
        "comefor let \"x\" to {\n    fn main(n: u64) -> u64 { return n; }\n}\n"
        "x 1 ;\nfn main() -> u64 { return 0; }\nbyuse \"x\" done\n"),
}


def _py_report() -> str:
    """用 **Python 的 `loment_comefor`** 跑与孪生**同名的那 7 项**检查, 打同一份报告。

    孪生只能走驱动的命令面（退出码 + 诊断），所以这一侧也按"看产物"的规则算。
    **负例大多只判"被拒 + 有一句诊断"**（`use` / `byuse` / `top level` 三条另收关键词）：
    两侧的措辞本来就不同（参考: "`byuse 'nope'` 收的不是任何在用的方言" / 自举:
    "byuse 收的不是任何在用的方言"）—— 把措辞变成判据是错的, 两个方向都错。
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

    ok1 = False
    try:
        ok1 = _ir(CF / "def_dialect.lomt") == _ir(CF / "def_hand.lomt")
    except Exception:  # noqa: BLE001
        ok1 = False
    rep("test_minimal_dialect_expands_to_handwritten", ok1,
        "方言源与手写源的 IR 不同（或至少一份没编出来）")

    ok2 = False
    try:
        m2, d2 = lomentc.load_unit(_WORK / "shape_not_word.lomt", ROOT)
        ok2 = lomentc.check(m2, deps=d2) == []
    except Exception:  # noqa: BLE001
        ok2 = False
    rep("test_comefor_is_shape_not_reserved_word", ok2, "四个词当标识符用就编不过")

    def rejected(rel: str, kw: str) -> bool:
        txt = (_WORK / rel).read_text(encoding="utf-8")
        try:
            loment_comefor.expand(lomc.lex(txt), txt)
        except lomc.LomError as e:
            return (kw in str(e)) if kw else True
        except Exception:  # noqa: BLE001
            return True          # 别的异常也算"被拒"（只是形状不同）
        return False

    rep("test_body_rejects_use", rejected("bad_body_use.lomt", "use"), "体里的 use 没被拒")
    rep("test_byuse_unknown_name_is_rejected",
        rejected("bad_unknown_byuse.lomt", "byuse"), "byuse 收错名字没被拒")
    rep("test_overconsumption_is_rejected",
        rejected("bad_overconsume.lomt", ""), "宏体多吃没被拒")
    # 关键词**各侧查各自实现的原话**：这一侧查的是**参考实现**（`loment_comefor.expand`，
    # 消息保持中文），孪生那一侧查的是**自举驱动**（`FujoJTOP/loment#8` 之后是纯 ASCII
    # 英文的 `comefor must be written at the top level`）。关键词不进报告（报告只有
    # PASS/FAIL 与失败原因），所以两边仍然逐字节相同 —— 实测把这个词写死成一种语言，
    # 就是"报告与 Python 版不同：py 6/7 / loment 7/7"。
    rep("test_definition_must_be_top_level",
        rejected("bad_not_top.lomt", "顶层"), "块里的定义没被拒")
    rep("test_entry_takes_no_params",
        rejected("bad_entry_params.lomt", ""), "main 带形参没被拒")

    out.append(f"lomcomefor: {ct['p']}/{ct['p'] + ct['f']} 通过")
    return "\n".join(out) + "\n"


def _clang() -> str | None:
    import shutil
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
    import shutil
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


def _build(src: Path, td: Path, name: str) -> Path:
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / f"{name}.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / f"{name}.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


@test
def test_comefor_check_matches_loment_twin():
    """**Loment 版**（`lomcomefor.lomt` 驱自举驱动）与 Python 版**同名检查的报告逐字节相同**。

    `docs/189` §3 的 S1 第十格。被测的是**另一件 Loment 程序**（自举侧的展开器），
    所以这一格同时是"两个展开器在这些性质上给出同一个答案"的判据 —— 而它**当场抓到
    两处真差距**（`byuse` 收错名字静默通过、体里的 `use` 没被拒），已在同一笔改动里
    补上（`loment/selfhost/comefor.lomt` + `driver.lomt` 的新错码 10）。
    """
    global _WORK
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    import shutil
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _WORK = td / "work"
        _WORK.mkdir()
        for f in ("def_dialect.lomt", "def_hand.lomt"):
            shutil.copy(CF / f, _WORK / f)
        for name, text in _CASES.items():
            with (_WORK / name).open("w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        (_WORK / "deps" / "myhelper").mkdir(parents=True)
        with (_WORK / "deps" / "myhelper" / "myhelper.lomt").open(
                "w", encoding="utf-8", newline="\n") as f:
            f.write("module myhelper\npub fn forty() -> u32 { return 40; }\n")
        want = _py_report()
        drv = _build(DRIVER, td, "comefor_driver")
        chk = _build(TWIN, td, "lomcomefor")
        shutil.copy(drv, _WORK / "driver")
        (_WORK / "driver").chmod(0o755)
        outp = td / "check.out"
        binn = f"{_T}lomcomefor.bin"
        script = (f"cp {_wsl_path(chk)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(_WORK)} && {binn} > {_wsl_path(outp)} 2>&1; echo -n $?")
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                           capture_output=True, text=True, timeout=300, shell=False)
        got = outp.read_bytes().decode("utf-8") if outp.exists() else ""
    assert r.stdout.strip() == "0", f"孪生该退 0（7 项全过）: rc={r.stdout!r}\n{got[:300]}"
    assert got == want, f"报告与 Python 版不同:\n  py     {want!r}\n  loment {got!r}"
    n = len(got.strip().splitlines()) - 1
    assert got.count("  PASS  ") == n == 7, (n, got[-120:])
    assert "lomcomefor: 7/7 通过" in got, got[-120:]
    print(f"      {n} 项检查: 与 Python 版逐字节相同（含刚补上的两处守卫）")


@test
def test_comefor_twin_selfhost_compiles():
    """`lomcomefor.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
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
        binn = f"{_T}lomcomefor_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomcomefor.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomcomefor.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomcomefor.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_comefor_test")


if __name__ == "__main__":
    sys.exit(main())
