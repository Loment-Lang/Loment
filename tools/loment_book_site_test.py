#!/usr/bin/env python3
# loment_book_site_test.py — Pages 站点的判据
#
# 站点是 `tools/loment_book_site.py` 现场渲染的，没有构建缓存；这里钉住四件事：
# 每章都有一页、站内相对链接都落得到实文件、代码块被转义且语言类还在、目录标出当前章。
# 渲染器与判据读的是同一份实现 —— 改了渲染器，这里会先红。

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_book_site  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _build(td: Path) -> list[Path]:
    return loment_book_site.build(td / "site")


@test
def test_every_chapter_has_a_page():
    """两本书的每一条、by-example 的每个文件，都渲染出一页。"""
    total = 0
    for book in loment_book_site.BOOKS:
        entries = loment_book_site._book_entries(book)
        total += len(entries)
        with tempfile.TemporaryDirectory(prefix="lomsite_") as tds:
            out = Path(tds) / "site"
            written = _build(Path(tds))
            have = {p.relative_to(out).as_posix() for p in written}
        missing = [book["slug"] + "/" + rel[:-3] + ".html" for _, _, rel in entries
                   if book["slug"] + "/" + rel[:-3] + ".html" not in have]
        assert not missing, f"{book['slug']}: 这些条目没有页面: " + ", ".join(missing[:8])
    assert total == 46, f"条目数变了: {total}（两本书 + by-example）"


@test
def test_links_resolve():
    """站内每个相对链接都落得到实文件（指向 book/ 之外的改指 GitHub）。"""
    with tempfile.TemporaryDirectory(prefix="lomsite_") as tds:
        out = Path(tds) / "site"
        written = _build(Path(tds))
        bad = loment_book_site.check(out, written)
    assert not bad, "链接落空:\n" + "\n".join(bad[:12])


@test
def test_code_fences_are_preserved_and_escaped():
    """围栏代码块留着语言类，内容按字面转义（`Result<u32, u32>` 不能变成标签）。"""
    with tempfile.TemporaryDirectory(prefix="lomsite_") as tds:
        out = Path(tds) / "site"
        _build(Path(tds))
        page = (out / "usage" / "src" / "07-option-result.html").read_text(encoding="utf-8")
    assert '<pre><code class="language-rust">' in page, "围栏块的语言类丢了"
    assert "Result&lt;u32, u32&gt;" in page, "尖括号没有转义"
    assert not re.search(r"Result<u32", page), "代码块里有未转义的小于号"


@test
def test_toc_marks_the_current_chapter():
    """目录标出当前这本书与当前这一章，各一条。"""
    with tempfile.TemporaryDirectory(prefix="lomsite_") as tds:
        out = Path(tds) / "site"
        _build(Path(tds))
        page = (out / "usage" / "src" / "07-option-result.html").read_text(encoding="utf-8")
        other = (out / "contributing" / "src" / "04-the-gate.html").read_text(encoding="utf-8")
    assert page.count('class="current"') == 2, "当前书 + 当前章应当各有一条标记"
    assert 'class="current">Option, Result and <code>?</code></a>' in page, \
        "当前章的标题没被选中"
    assert 'class="current">Loment Development Contribution Plan</a>' in other, \
        "贡献书里没标出当前书"
    assert "Using Loment" in other and "Loment Development Contribution Plan" in page, \
        "两本书没有互相出现在目录里"


def main() -> int:
    return loment_suite.run(TESTS, "loment_book_site_test")


if __name__ == "__main__":
    sys.exit(main())
