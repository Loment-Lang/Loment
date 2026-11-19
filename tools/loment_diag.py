#!/usr/bin/env python3
# loment_diag.py — 诊断分类 + 修复建议 (M64, docs/148)
#
# 判据: 10 类错误各有错误码与修复建议。
# 实现: 对 lomentc.check() 的原始消息做模式分类 (不改编译器消息本身),
#       每类给出稳定错误码 (E0xx) 与可执行建议。
#
# 用法:
#   python tools/loment_diag.py FILE [--json]
# 退出码: 0 = 无错误 / 1 = 有错误 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

@dataclass(frozen=True)
class Card:
    """一个码的说明卡。字段顺序 = 渲染顺序。"""

    what: str
    why: str
    fixes: tuple[str, ...]
    yes: str
    no: str



@dataclass(frozen=True)
class LangCard:
    """一门源语言的卡。`key` 与 `potato_from.LANGS` 的键一致。"""

    key: str
    display: str
    exts: tuple[str, ...]        # 后缀（`potato_from.EXT` 的真源，这里只做镜像说明）
    tokens: tuple[str, ...]      # 内容特征（粗提示）
    edge: str                    # 这门语言的边界：什么翻不过去
    abi: str                     # 调用约定那一档的**事实**（**不许**是"这条路通不通"的结论）



# ---------------------------------------------------------------- errdata（真源）
#
# 2026-11-18 起，码表 / 说明卡 / 语言卡 / 分类规则的**真源是 `loment/tools/errdata.lomt`**
# （自举侧）。这里**反过来解析它**：参考实现只留「正则 → 码号」这一层解释器。
#
# 方向是有意的：文字与规则的唯一副本住在**会被装进工具链的那一侧**。Python 不再拥有它 ——
# 它只是这份数据的第一个读者。`surface_data.lomt` 里剩下的东西全是从**别的真源**推导的
# （语法别名与词序来自 `potato_from`，版本来自 `loment_release`）。

#: 内部码的号段（`#773`）。与用户码分开：`n_codes()` 只给用户码的上界，
#: 而 900..999 留给"工具链自己崩了"这一类，不参与"从 1 起连续"那条不变式。
INTERNAL_LO = 900
INTERNAL_HI = 999


def is_internal_code(c: int) -> bool:
    """这个号是不是内部码（E9xx）。"""
    return INTERNAL_LO <= c <= INTERNAL_HI


def code_num(code: str) -> int:
    """`"E023"` -> `23`。取不出来就是 0（调用方据此报"未知码"）。"""
    return int(code[1:]) if len(code) > 1 and code[0] == "E" and code[1:].isdigit() else 0


def _code_str(n: int) -> str:
    """码的显示形状：< 100 是 `E0xx`，>= 100 是 `E0xxx`（用户 2026-11-10 定）。"""
    return f"E{n:03d}" if n < 100 else f"E{n:04d}"


_ERRDATA = ROOT / "loment" / "tools" / "errdata.lomt"


def _unlom(s: str) -> str:
    """把 `.lomt` 字符串字面量里那几种转义还原（与 `_lom_str` 是一对）。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = s[i + 1] if i + 1 < len(s) else ""
        out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(e, e))
        i += 2
    return "".join(out)


def _errdata_fns(text: str) -> dict[str, list[tuple[int, str]]]:
    """`函数名 -> [(键, 值)]`（键是 `if` 里的那个数字，值已反转义）。"""
    fns: dict[str, list[tuple[int, str]]] = {}
    cur: str | None = None
    for line in text.splitlines():
        st = line.strip()
        m = re.match(r"pub fn (\w+)\(", st)
        if m:
            cur = m.group(1)
            fns[cur] = []
            continue
        if cur is None:
            continue
        m = re.match(r'if \w+ == (\d+) \{ return (?:(\d+)|"(.*)"); \}$', st)
        if m:
            k = int(m.group(1))
            v = int(m.group(2)) if m.group(2) is not None else _unlom(m.group(3))
            fns[cur].append((k, v))
            continue
        if st.startswith("}"):
            cur = None
    return fns


def _errdata_load() -> dict:
    text = _ERRDATA.read_text(encoding="utf-8")
    fns = _errdata_fns(text)

    def scal(name: str) -> int:
        m = re.search(rf"pub fn {name}\(\) -> u32 \{{ return (\d+); \}}", text)
        if not m:
            raise SystemExit(f"loment_diag: errdata.lomt 里没有 {name}()")
        return int(m.group(1))

    def table(name: str) -> dict[int, str]:
        return {k: v for k, v in fns.get(name, [])}

    ed = {
        "rule_count": scal("rule_count"),
        "n_codes": scal("n_codes"),
        "internal_lo": scal("internal_lo"),
        "internal_hi": scal("internal_hi"),
        "rule_code": table("rule_code"),
        "rule_pat": table("rule_pat"),
        "code_str": table("code_str"),
        "code_family": table("code_family"),
        "nfix": table("code_nfix"),
        "ascii": table("code_ascii"),
        "title": table("code_title"),
        "hint": table("code_hint"),
        "what": table("code_what"),
        "why": table("code_why"),
        "yes": table("code_yes"),
        "no": table("code_no"),
        "fix": table("code_fix"),
        "lang_count": scal("lang_count"),
        "lang_key": table("lang_key"),
        "lang_name": table("lang_name"),
        "lang_exts": table("lang_exts"),
        "lang_tokens": table("lang_tokens"),
        "lang_edge": table("lang_edge"),
        "lang_abi": table("lang_abi"),
    }
    ed["rules"] = [
        (_code_str(int(ed["rule_code"][i])), ed["rule_pat"][i])
        for i in range(ed["rule_count"])
    ]
    if len(ed["rules"]) != ed["rule_count"]:
        raise SystemExit("loment_diag: errdata 的规则表不完整")
    return ed


_ED = _errdata_load()

#: 规则表：`(码, 模式, 中文标题, 中文建议)`，**顺序就是命中优先级**。
RULES: list[tuple[str, str, str, str]] = [
    (code, pat,
     _ED["title"].get(code_num(code) * 2 + 1, ""),
     _ED["hint"].get(code_num(code) * 2 + 1, ""))
    for code, pat in _ED["rules"]
]

_CODES: list[int] = sorted({code_num(c) for c, _p, _t, _h in RULES})


def _card_of(c: int, lang: int) -> Card:
    n = int(_ED["nfix"].get(c, 0))
    return Card(
        what=_ED["what"].get(c * 2 + lang, ""),
        why=_ED["why"].get(c * 2 + lang, ""),
        fixes=tuple(_ED["fix"].get((c * 8 + i) * 2 + lang, "") for i in range(n)),
        yes=_ED["yes"].get(c * 2 + lang, ""),
        no=_ED["no"].get(c * 2 + lang, ""),
    )


CARDS: dict[int, Card] = {c: _card_of(c, 1) for c in _CODES}
CARDS_EN: dict[int, Card] = {c: _card_of(c, 0) for c in _CODES}
TITLE_EN: dict[int, str] = {c: _ED["title"].get(c * 2, "") for c in _CODES}
ASCII_ONE_LINER: dict[int, str] = {c: v for c, v in _ED["ascii"].items()}

LANG_ORDER: tuple[str, ...] = tuple(_ED["lang_key"][i] for i in range(_ED["lang_count"]))
LANG_CARDS: dict[str, LangCard] = {}
LANG_EDGE_EN: dict[str, str] = {}
LANG_ABI_EN: dict[str, str] = {}
for _i in range(_ED["lang_count"]):
    _k = _ED["lang_key"][_i]
    LANG_CARDS[_k] = LangCard(
        key=_k,
        display=_ED["lang_name"][_i],
        exts=tuple(_ED["lang_exts"][_i].split()),
        tokens=tuple(_ED["lang_tokens"][_i].split("|")),
        edge=_ED["lang_edge"].get(_i * 2 + 1, ""),
        abi=_ED["lang_abi"].get(_i * 2 + 1, ""),
    )
    LANG_EDGE_EN[_k] = _ED["lang_edge"].get(_i * 2, "")
    LANG_ABI_EN[_k] = _ED["lang_abi"].get(_i * 2, "")

_FAMILY: dict[int, int] = {c: f for c, f in _ED["code_family"].items()}


def family(code: str) -> str:
    """细分码 -> 它所属的族码（`E0186` -> `E030`）；不是细分码就原样返回。"""
    n = int(code[1:])
    return _code_str(_FAMILY.get(n, n))


def _validate() -> None:
    """errdata 的静态不变式 —— 坏掉时**当场炸**，不要等渲染器静默地少印一条。

    这些检查原来住在 `surface_lomt()` 里（生成器硬失败）；真源搬过来之后，落点跟着搬。
    """
    user = [c for c in _CODES if not is_internal_code(c)]
    internal = [c for c in _CODES if is_internal_code(c) and c != 999]
    if 999 not in _CODES:
        raise SystemExit("loment_diag: 兜底码 E0999 不在 errdata 的码表里")
    if not user or user != list(range(1, user[-1] + 1)):
        raise SystemExit(f"loment_diag: 用户码不是从 1 起连续: {user[:5]}…")
    if not internal or internal != list(range(internal[0], internal[-1] + 1)):
        raise SystemExit(f"loment_diag: 内部码不是连续一段: {internal}")
    if internal[0] < INTERNAL_LO or internal[-1] > INTERNAL_HI:
        raise SystemExit(f"loment_diag: 内部码越出 {INTERNAL_LO}..{INTERNAL_HI}")
    if _ED["n_codes"] != user[-1]:
        raise SystemExit(f"loment_diag: n_codes()={_ED['n_codes']} 与实测 {user[-1]} 不一致")
    if _ED["internal_lo"] != internal[0] or _ED["internal_hi"] != INTERNAL_HI:
        raise SystemExit("loment_diag: errdata 的内部码段与实测不一致")
    for c in _CODES:
        n_zh = len(CARDS[c].fixes) if c in CARDS else 0
        n_en = len(CARDS_EN[c].fixes) if c in CARDS_EN else 0
        if n_zh != n_en:
            raise SystemExit(f"loment_diag: 码 {c} 的中英修法条数不等 ({n_zh} vs {n_en})")
        if c in CARDS and (not CARDS[c].what or not CARDS[c].why):
            raise SystemExit(f"loment_diag: 码 {c} 的说明卡缺 what/why")
        if c in CARDS and len(CARDS[c].fixes) > 8:
            # 平键是 `c * 8 + i`（`FIX_STRIDE = 8`）—— 第 9 条会被静默挤掉。
            raise SystemExit(f"loment_diag: 码 {c} 有 {len(CARDS[c].fixes)} 条修法（上限 8）")
    # 英文那一份必须**全 ASCII**（默认语言在 936 代码台下不乱码的前提，docs/169 §3a）。
    for c in _CODES:
        for name, v in (("title", TITLE_EN.get(c, "")),
                        ("what", CARDS_EN[c].what), ("why", CARDS_EN[c].why),
                        ("yes", CARDS_EN[c].yes), ("no", CARDS_EN[c].no),
                        *[("fix", f) for f in CARDS_EN[c].fixes]):
            if any(ord(ch) > 127 for ch in v):
                raise SystemExit(f"loment_diag: 英文 {name} 里混进了非 ASCII（码 {c}）")


_validate()


def _lomenterr_version() -> str:
    """报错器**自己的**版本号 —— 与语言版本是两条线（`docs/182` §17）。

    真源是 `loment_release.LOMENTERR`；这里只是把它取过来，好让 `--dump-surface`
    导进派生单元。**取不到就炸，不给默认值**（编出来的版本号看着完全正常）。
    """
    import loment_release
    v = loment_release.LOMENTERR
    if not v:
        raise SystemExit("loment_diag: loment_release.LOMENTERR 是空的")
    return v

RULES_BY_CODE: dict[int, tuple[str, str, str]] = {
    code_num(c): (c, t, h) for c, _pat, t, h in RULES
}


def classify(msg: str) -> tuple[str, str, str]:
    for code, pat, title, hint in RULES:
        if re.search(pat, msg):
            return code, title, hint
    return "E0999", "未分类", "请报告此消息以便补充分类规则。"


def foreign_note(path: Path, errs: list[str]) -> str | None:
    """文件报错、而**看内容是别的语言**时, 给一条对得上的建议。

    **为什么非要有这一条**: 一份 C 源码叫 `.lomt` 时, `loment diag` 会照着 E019 的通用
    建议说"按消息给的行:列**改那一行的写法**" —— 那条在这里是**错的**: 那份 C 的语法本来
    就对, 它只是不是 Loment。照建议改会把一份好 C 改坏。

    **触发条件是"有错就闻一下", 不是某一条特定消息** —— 这条是实测改过来的:
    第一版只认 `期望 module`, 而那份 C 在第 63 行有个 `'0'` 字符字面量, **词法**先于解析
    就把它拒了 (`63:25: 非法字符 "'"`), 于是根本没走到"期望 module", 提示不出现。
    改文件的人会以为"这条判据没生效", 而真相是触发面太窄。
    """
    if not errs:
        return None
    try:
        import potato_from
        # 注意用 `resolve_lang` 而不是 `detect_lang`: 后缀优先 (一份 `.c` 有没有错都该
        # 按 C 说), 内容兜底 —— 判语法的规则只有一处, 见 potato_from.resolve_lang。
        lang, why = potato_from.resolve_lang(path, "auto")
    except Exception:                                                # noqa: BLE001
        return None
    # **从 `potato_from` 推导, 不写死** —— 原先这里硬编码了 `("c","rust","python")`,
    # 于是加了 Go/Java 之后, 一份 Go 源码的诊断提示**静默消失**(`resolve_lang` 明明
    # 认出来了)。硬编码一份"支持哪些语言"的清单, 必然在加语言时漏掉。
    # **声明里写的"原生"语法不是"外源"。** `tur` 也是 `LANGS` 里的一门，
    # 但它同时是**原生**语法（`NATIVE_GRAMMARS`，`docs/188` §2、2026-11-03 的裁定）：
    # 一份 `choose write grammar tur` 的文件**就是** Loment，前门当原生读。
    # 不排掉的话，报错器会对它说"**这个文件不是 Loment 语法**、别照上面那条改"，
    # 而上面那条（补 `module`）**恰恰是对的** —— 一条该听的话被一句"别听"撤销。
    # 反过来，`rust` 已退回**表层语法**（2026-11-03）：声明 rust 的文件拿外源提示是**对的**。
    #
    # **但只排"声明"那一路**（靠 `why` 分）：一份**真 Rust 源码**叫 `.rs`、
    # 没写声明 —— 那时 `resolve_lang` 按**后缀**认，而这句提示**是对的**
    # （那份内容确实不是 Loment，前门会当原生读、然后读不通）。
    # 一刀切把 `rust` 整个排掉，会把那条真该说的话也一起消掉
    # （实测：`loment_multisyntax_test::test_diag_does_not_tell_you_to_fix_valid_foreign_source`
    # 的 `SYNTAXES` 里那条 `.rs` 用的是**真 Rust 源码**，它就该被点名）。
    _by_decl = why.startswith("文件头声明")
    if lang in potato_from.LANGS and not (_by_decl and lang in potato_from.NATIVE_GRAMMARS):
        card = LANG_CARDS.get(lang)
        head = (f"这个文件**不是 Loment 语法**, 看内容是 "
                f"**{card.display if card else lang.upper()}**（{why}）。"
                f"别照上面那条改 —— 它的语法本来就是对的。")
        # 语言的**边界**与**调用约定**与报错器**共用同一段文字**（`LANG_CARDS`）——
        # 抄第二份必然漂, 而这两句正是用户最需要对齐的部分。
        if card:
            # 用普通拼接而不是 f-string 插值换行：f-string 的表达式段里放不了 `\n` 转义，
            # 而插一个模块级常量就要多一处"只有这里用"的名字（上一版就是那么写的，
            # `NL` 从没定义过，而这条路只有"外源文件 + 有错"才走到，判据没覆盖到）。
            head += ("\n      这门语言的边界: " + card.edge
                     + "\n      调用约定: " + card.abi)
        # **该建议 `--impl` 还是接口单元，要看接口那条路走不走得通** —— 这不是锦上添花：
        # Python 那类的 `abi` 不是 C，接口单元**一条函数都发不出来**，照默认那条命令敲会
        # 得到一个**空 module**（stderr 上有 `[skip] f: 调用约定不是 C ABI`，但产物看着
        # 像"编过了"）。一份"编过了但什么都没有"的东西，比报错还难查。
        #
        # **判据是问出来、不是按语言写死**：直接拿 `lomt_from.emit_lomt(impl=False)` 数
        # 它到底发了几条 `pub extern fn`。写死一张"哪些语言有翻译器/哪些 ABI 是 C"的表，
        # 必然在加语言时漏掉（上面那段注释就是为同一个毛病写的）。
        try:
            _doc, _rep = potato_from.transcribe(path, "auto", "strict")
            n_body = sum(1 for f in (_doc.get("functions") or [])
                         if isinstance(f.get("body"), str) and f["body"].strip())
            import lomt_from
            iface, _sk = lomt_from.emit_lomt(_doc, impl=False)
            n_iface = iface.count("pub extern fn ")
        except Exception:                                            # noqa: BLE001
            return None
        #: 命令行前缀。**`--impl` 由每个分支自己拼** —— 拼进这里会让"不带 --impl"那行
        #: 也带上它（第一版就是这么错的：两条命令都变成了 `--impl`，还重了一遍）。
        cmd = f"       python tools/lomt_from.py \"{path}\" --lang auto "
        if n_body and n_iface == 0:
            # 接口那条路一条都发不出来（ABI 不是 C，或者签名过不了 FFI 那道闸门）
            return (head + f"**它带着函数的正文**，而这个语言走不了接口那条路"
                    f"（ABI 不是 C，或签名超出 FFI 第 1 阶段）—— 只发接口会得到一个"
                    f"**空 module**。翻成 Loment 实现：\n"
                    f"{cmd}--impl --out {path.stem}.impl.lomt\n"
                    f"      （`--impl` 把正文翻成真的 `pub fn`。见 docs/186 / docs/187）")
        if n_body:
            # 两条路都走得通：接口单元（实现在外面，链目标文件）或翻成实现
            return (head + f"它**既可以**只出接口、也**可以**把正文翻成实现：\n"
                    f"{cmd}--out {path.with_suffix('.iface.lomt').name}\n"
                    f"           （不带 `--impl`：接口单元 —— 实现在源语言那一侧，"
                    f"配外面编好的目标文件用。见 docs/179 §2）\n"
                    f"{cmd}--impl --out {path.stem}.impl.lomt\n"
                    f"           （带 `--impl`：把正文翻成真的 `pub fn`。"
                    f"见 docs/186 / docs/187）")
        return (head + f"走多语法前端:\n"
                f"{cmd}--out {path.with_suffix('.iface.lomt').name}\n"
                f"      （它把外源语法转成 L1 接口单元, 再由 Loment 编译器编。"
                f"见 docs/179）")
    return None


def diagnose(errs: list[str]) -> list[dict]:
    """裸消息 -> 一条带说明卡的诊断。

    **说明书那一半也从这里出**（`what/why/fixes/yes/no`）—— 报错器渲染的就是它们，而
    `--json` 的消费者拿到的与报错器看到的是**同一份**内容。码不在卡表里时那五项缺席，
    由调用方走"未知码"那条（**不静默**：什么都不给，看起来像"这条没有更多可说的"）。
    """
    out = []
    for e in errs:
        code, title, hint = classify(e)
        card = CARDS.get(code_num(code))
        d = {"code": code, "title": title, "message": e, "hint": hint}
        if card:
            d.update({"what": card.what, "why": card.why, "fixes": list(card.fixes),
                      "yes": card.yes, "no": card.no})
        out.append(d)
    return out


def _lom_str(s: str) -> str:
    """把一段文字放进 Loment 字符串字面量。**必须转义** —— 标题/建议里既有 `"`（E005 的
    `excluded "<space>: ..."`）也有 `\\`，不转义就会把生成的文件切坏。"""
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + out + '"'


def _lomenterr_version() -> str:
    """报错器**自己的**版本号 —— 与语言版本是两条线（`docs/182` §17）。

    **真源只有一个**：`loment_release.LOMENTERR`。这里只是把它取过来，好让
    `--dump-surface` 把它导进 `surface_data`（报错器拿它打 `lomenterr version`）。
    在别处再写一遍字面量就是"同一份清单抄第二遍" —— 本仓那条老账（`docs/179` §7.3），
    而这一处的第二份会特别难抓：两个号都是 `0.1.x`，对不上时看不出谁对。

    **取不到就炸，不给默认值**：给了默认值，生成物会带着一个**编出来**的版本号出门，
    而它看着完全正常（`0.1.0` 这种真像回事的号），没人会发现 —— 宁可它红。
    """
    import loment_release
    v = loment_release.LOMENTERR
    if not v:
        raise SystemExit("loment_diag: loment_release.LOMENTERR 是空的")
    return v


def surface_lomt() -> str:
    """导出**派生**数据（语法别名 / 声明词序 / 报错器版本）。

    2026-11-18 起，码表、说明卡、语言卡、分类规则、号段边界、细分码的族映射，真源**全是
    `loment/tools/errdata.lomt`**（自举侧）。`surface_data` 只剩「从别的真源推导」的这几样，
    并且 `use errdata` —— 消费方照旧 `use surface_data` 也能拿到 errdata 的那部分。

    为什么这几样仍然生成而不是搬进 errdata：别名表与词序是**前端**的事实（`potato_from`
    是出厂锁），版本号是**发行**的事实（`loment_release`）。把它们手抄一份进 errdata
    就是本仓最反对的「同一份清单抄第二遍」。
    """
    import potato_from

    aliases = sorted(potato_from.GRAMMAR_ALIASES.items())
    decl_words = tuple(potato_from.GRAMMAR_DECL_WORDS)

    src = [
        "// surface_data.lomt — 由 `tools/loment_diag.py --dump-surface` 生成，别手改。",
        "//",
        "// **它只剩派生数据**（2026-11-18 起）：码表 / 说明卡 / 语言卡 / 分类规则的真源是",
        "// `loment/tools/errdata.lomt`（自举侧，下面 `use errdata`）。这里生成的是：",
        "//   * `potato_from.GRAMMAR_ALIASES` / `GRAMMAR_DECL_WORDS`（语法声明的别名与词序）",
        "//   * `loment_release.LOMENTERR`（报错器版本）",
        "//",
        "// 这几样仍然生成，是因为它们的真源在别处（前端 / 发行）；手抄进 errdata 就是",
        "// 「同一份清单抄第二遍」。",
        "",
        "module surface_data",
        "",
        "use errdata",
        "",
        f"pub fn decl_word_count() -> u32 {{ return {len(decl_words)}; }}",
        "",
        "/// 声明的第 `i` 个词（`choose` / `write` / `grammar`）—— 词序是两边共享的契约。",
        "pub fn decl_word(i: u32) -> str {",
    ]
    for i, w in enumerate(decl_words):
        src.append(f"    if i == {i} {{ return {_lom_str(w)}; }}")
    src += ['    return "";', "}", ""]
    src += [f"pub fn alias_count() -> u32 {{ return {len(aliases)}; }}", "",
            "/// 第 `i` 个语法别名（报错器按**小写**比 —— 作者写 `C#` 还是 `c#` 都该认）。",
            "pub fn alias_word(i: u32) -> str {"]
    for i, (word, _canon) in enumerate(aliases):
        src.append(f"    if i == {i} {{ return {_lom_str(word.lower())}; }}")
    src += ['    return "";', "}", "",
            "/// 该别名对应的语言（= `LANG_CARDS` 的键 / `potato_from.LANGS` 的键）。",
            "pub fn alias_lang(i: u32) -> str {"]
    for i, (_word, canon) in enumerate(aliases):
        src.append(f"    if i == {i} {{ return {_lom_str(canon)}; }}")
    src += ['    return "";', "}", ""]
    src += [
        "/// 报错器自己的版本（`lomenterr version` 打的就是它）。真源：`tools/loment_release.py`。",
        f"pub fn lomenterr_version() -> str {{ return {_lom_str(_lomenterr_version())}; }}",
        "",
    ]
    return chr(10).join(src)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_diag", description="诊断分类 + 修复建议")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lom-root", default=None)
    ap.add_argument("--dump-surface", metavar="PATH",
                    help="把码表导出成 Loment 数据单元（给自举侧与报错器 use，docs/182 §5.2）")
    a = ap.parse_args(argv)
    if a.dump_surface:
        Path(a.dump_surface).parent.mkdir(parents=True, exist_ok=True)
        Path(a.dump_surface).write_text(surface_lomt(), encoding="utf-8", newline="\n")
        print(f"[OK] {a.dump_surface} ({len(ASCII_ONE_LINER)} 条码)")
        return 0
    if not a.file:
        ap.error("需要 FILE，或 --dump-surface PATH")
    root = Path(a.lom_root) if a.lom_root else ROOT
    p = Path(a.file)
    try:
        mod, deps = lomentc.load_unit(p, root)      # 唯一入口（docs/182 §1.10）
        errs = lomentc.check(mod, deps=deps)
    except lomentc.LomError as e:
        errs = [str(e)]
    except OSError as e:
        # 文件这一层的失败（`#483`）：路径不存在 / 是目录 / 没有读权限。原先它一路漏到
        # Python 栈 —— `lomentc` 自己的 `main()` 早就把这一句接住了（见 `E031` 那条规则），
        # `diag` 这个入口忘了。措辞与那边**同一句**：分类器认的是"读不了输入文件"。
        errs = [f"读不了输入文件 {p}: {e.strerror or e}"]
    diags = diagnose(errs)
    note = foreign_note(p, errs)
    if a.json:
        print(json.dumps({"diagnostics": diags, "foreign_note": note},
                         ensure_ascii=False, indent=2))
    else:
        # **外源文件：语言那段是整条报告的起点**（`#29`）。它原先排在最后，前面先给
        # `E0999`/`E019` 与一屏 **Loment 的**修法 —— 而这份文件不是 Loment，那些修法
        # 一条都不适用。现在先说是哪门语言、三条进来路；Loment 解析器看到的东西只作为
        # "在它上面读到的东西"附在后面，卡片整块不印（与报错器 `h_foreign` 同一条口径）。
        if note:
            print("  ⚠ " + note)
            if diags:
                print()
                print("  Loment 解析器在它上面读到的是（只作参考，别照它改）：")
            for d in diags:
                print(f"    {d['code']} [{d['title']}] {d['message']}")
        else:
            for d in diags:
                print(f"{d['code']} [{d['title']}] {d['message']}")
                if "what" not in d:
                    print(f"      建议: {d['hint']}")
                    continue
                print(f"      错了什么: {d['what']}")
                print(f"      为什么错: {d['why']}")
                print("      怎么改:")
                for i, fx in enumerate(d["fixes"], 1):
                    print(f"        {i}. {fx}")
                print(f"      支持:   {d['yes']}")
                print(f"      不支持: {d['no']}")
        if not diags and not note:
            print("[OK] 无错误")
    return 1 if diags else 0


if __name__ == "__main__":
    sys.exit(main())
