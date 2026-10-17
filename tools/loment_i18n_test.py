#!/usr/bin/env python3
# loment_i18n_test.py — 英文版的翻译戳 (docs/i18n/glossary.md §2)
#
# 判据: 每份 `docs/**/*.en.md` 的开头两行是**机器可读的戳** ——
#
#     <!-- translated-from: docs/NNN-slug.md -->
#     <!-- source-sha256: <中文源**今天**的 sha256> -->
#
# 中文源一改动, 戳就过期, 这条判据红, 直到那篇重新翻译并重盖戳。与发布清单
# (`loment_release`) 和自举种子 (`loment_seed`) 是**同一条纪律**:
# **派生物只有在"源动了会有人发现"的前提下才可信**。
#
# 这条判据**在 `docs/i18n/glossary.md` §2 里已经被点名**（"`tools/loment_i18n_test.py`
# goes red until that file is re-translated and re-stamped"），但 2026-09-22 之前
# **那个文件不存在** —— 体例写了一条没有人执行的规矩。这里把它实现出来。
#
# 运行: python tools/loment_i18n_test.py

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 戳的两行。**锚在行首行尾** —— 半行匹配会让 `<!-- translated-from: x --> 后记` 这种
#: 混进注释的行也叫通过, 而它已经不是"恰好这两行"了 (体例 §2 的措辞是 exactly these
#: two lines, before the H1)。
_FROM = re.compile(r"^<!-- translated-from: (\S+) -->$")
_SHA = re.compile(r"^<!-- source-sha256: ([0-9a-f]{64}) -->$")

#: 译者向的工件**不翻译**, 所以这个目录下不该有 `.en.md` (体例 §1: glossary
#: "gets no English sibling of its own")。它是唯一一条"该**没有**"的断言 ——
#: 别的规矩都是"有则必须正确", 有了反而错的情况只此一处。
#: **相对根算, 不用这个模块级常量** —— `scan()` 要能被夹具喂一棵**临时树**, 而对着
#: 真实仓库路径比的那一版会让夹具"通过得不对"（实测: 夹具里那份 `i18n/glossary.en.md`
#: 被当成"缺戳"报了出来, 于是"该没有的有了"这条规矩**一次都没被验过**）。
NO_SIBLING_DIR = "docs/i18n"


def sha_of(p: Path) -> str:
    """中文源的 sha256,**按通用换行归一后哈希**。

    换行归一是为了与 `loment_release.sha()` 同口径: `.gitattributes` 把 `*.md` 钉成
    `eol=lf`, 所以两种算法在仓库里**今天**给出同一个值 —— 归一买的是"换一种检出方式
    (CRLF) 时这条判据不假红"。**判据不该因为宿主而红**, 那正是 `loment_release` 里
    同一个函数存在的理由。
    """
    raw = p.read_bytes()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def scan(root: Path) -> list[str]:
    """返回树里所有**翻译戳的问题**（空 = 全绿）。纯函数, 便于用夹具证明它会红。"""
    bad: list[str] = []
    no_sibling = root / NO_SIBLING_DIR
    for en in sorted(root.glob("docs/**/*.en.md")):
        # 译者向那些目录由**另一条**规矩管（下面那个循环）, 不在这里重复计数 ——
        # 同一件事报两遍会让"共几条"这个数没法用来判断夹具。
        if en.is_relative_to(no_sibling):
            continue
        rel = en.relative_to(root).as_posix()
        lines = en.read_text(encoding="utf-8").splitlines()
        # 允许一个 BOM 落在第一行行首（编辑器留下的, 不是戳的一部分）
        if lines and lines[0].startswith("﻿"):
            lines[0] = lines[0].lstrip("﻿")
        m_from, m_sha = (_FROM.match(lines[0]), _SHA.match(lines[1])) if len(lines) >= 2 \
            else (None, None)
        if not (m_from and m_sha):
            head = " / ".join(repr(x[:45]) for x in lines[:2]) or "（空文件）"
            bad.append(f"{rel}: 开头两行不是体例要求的那两行戳: {head}")
            continue
        src_rel, want = m_from.group(1), m_sha.group(1)
        src = root / src_rel
        if not src.exists():
            bad.append(f"{rel}: 戳指的是 `{src_rel}`, 而那个文件不在树里")
            continue
        # 体例 §1: **编号与 slug 都不许改** —— 改了的话 `docs/NNN` 形式的引用与
        # `loment_src.py` 的按名 glob 会静默失配, 而内容看上去一切正常。
        if src.parent != en.parent or en.name != src.name[:-3] + ".en.md":
            bad.append(f"{rel}: 与它声明的源 `{src_rel}` 不同目录或不同名 "
                       f"(体例要求同目录、只多一个 `.en`)")
        cur = sha_of(src)
        if cur != want:
            bad.append(f"{rel}: 戳过期 —— `{src_rel}` 现在的 sha256 是 {cur[:12]}…, "
                       f"戳上写的是 {want[:12]}…（重新翻译后重盖戳）")
    for stray in sorted(no_sibling.glob("**/*.en.md")) if no_sibling.exists() else []:
        bad.append(f"{stray.relative_to(root).as_posix()}: 译者向的工件不该有英文版 "
                   f"(体例 §1)")
    return bad


TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_tree_has_no_stale_stamps():
    """仓库里每份 `.en.md` 的戳都与它声明的中文源对得上。

    **额外钉一条非空**: 树里必须**真有** `.en.md`。否则"一份都没有"也让这条判据绿,
    而那是它最该报的失败形状 —— 尺子量了个空集合还说自己没问题。
    """
    en_files = sorted(ROOT.glob("docs/**/*.en.md"))
    assert en_files, "树里一份 `docs/**/*.en.md` 都没有 —— 判据成了空转"
    bad = scan(ROOT)
    assert not bad, "翻译戳有问题:\n  " + "\n  ".join(bad)
    print(f"      {len(en_files)} 份英文版的戳都与中文源对得上")


@test
def test_scanner_catches_stale_missing_and_misnamed():
    """**验它会红** —— 用夹具喂进五种形状, 断言只有坏的那几种被点名。

    `docs/190` §1.4 那条纪律: 判据测过自己会红才算判据。这里的五格是"戳这条规矩
    会以哪些形状坏掉", 而**每一格都得被抓住**:

      * 好的那一份: 不许报（否则判据是"一律红", 没有信息量）
      * 源改了内容 -> 戳过期
      * 戳指的源不在树里 -> 缺失
      * 只有第一行、没有 sha 行 -> 缺戳
      * 同目录但改了 slug -> 命名漂移（体例 §1）
      * `docs/i18n/` 下出现 `.en.md` -> 该没有的有了
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tds:
        root = Path(tds)
        (root / "docs" / "i18n").mkdir(parents=True)

        def mk(name: str, body: str, src_body: str | None = None) -> Path:
            src = root / "docs" / name
            src.write_text(src_body if src_body is not None else "中文正文\n",
                           encoding="utf-8", newline="\n")
            en = root / "docs" / (name[:-3] + ".en.md")
            stamp = f"<!-- source-sha256: {sha_of(src)} -->\n" if body == "OK" else ""
            en.write_text(f"<!-- translated-from: docs/{name} -->\n{stamp}{body}\n",
                          encoding="utf-8", newline="\n")
            return en

        mk("101-good.md", "OK")                                   # 好的: 不许报
        stale = mk("102-stale.md", "OK")
        (root / "docs" / "102-stale.md").write_text("中文改过了\n", encoding="utf-8",
                                                    newline="\n")   # 源在盖戳之后又动了
        en_missing = root / "docs" / "103-missing.en.md"
        en_missing.write_text(
            "<!-- translated-from: docs/103-gone.md -->\n"
            "<!-- source-sha256: " + "0" * 64 + " -->\n正文\n",
            encoding="utf-8", newline="\n")
        en_nostamp = root / "docs" / "104-nostamp.en.md"
        en_nostamp.write_text("# 104 · No stamp\n正文\n", encoding="utf-8", newline="\n")
        mis = mk("105-slug.md", "OK")
        renamed = root / "docs" / "105-renamed.en.md"
        renamed.write_text(mis.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        mis.unlink()
        (root / "docs" / "i18n" / "glossary.en.md").write_text(
            "# Glossary\n", encoding="utf-8", newline="\n")

        got = scan(root)

        def hit(sub: str) -> bool:
            return any(sub in b for b in got)

        assert not hit("101-good"), f"好的那一份被报了: {got}"
        assert hit("102-stale.en.md"), f"过期那份没报: {got}"
        assert hit("103-missing.en.md"), f"源缺失那份没报: {got}"
        assert hit("104-nostamp.en.md"), f"缺戳那份没报: {got}"
        assert hit("105-renamed.en.md"), f"改名那份没报: {got}"
        # **两个条件都要**: 只断言"文件名出现过"是不够的 —— 那条文件也可能被**别的**
        # 规矩顺手报出来（第一版就是这么漏的: 它落在"缺戳"那一格, 于是"该没有的有了"
        # 这条规矩一次都没被验过）。文案也得对上。
        assert hit("i18n/glossary.en.md") and hit("不该有英文版"), \
            f"译者向工件有了英文版, 但不是被那条规矩报出来的: {got}"
        assert len(got) == 5, f"应当恰好报 5 条, 实得 {len(got)}: {got}"
    print("      五种坏形状全部被点名（好的那一份没被误报）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_i18n_test")


if __name__ == "__main__":
    sys.exit(main())
