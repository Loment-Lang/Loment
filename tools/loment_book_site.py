#!/usr/bin/env python3
# loment_book_site.py — 把 book/ 渲染成静态站点（GitHub Pages 用）
#
# 为什么自己写渲染器：书的 Markdown 只用到很窄的一组语法（标题、段落、围栏代码、
# 行内代码、粗体/斜体、链接、列表、引用、表格），而门禁要能在**没有 mdBook 的
# runner** 上跑同一条渲染。引一个第三方渲染器等于把发布面交给它；这里只依赖标准库，
# 判据 `loment_book_site_test.py` 和 Pages 的 workflow 跑的是同一份实现。
#
# 用法:
#   python tools/loment_book_site.py --out _site        # 渲染整站
#   python tools/loment_book_site.py --out _site --check  # 渲染后自检链接

from __future__ import annotations

import argparse
import html
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "book"
SRC = BOOK / "src"
BY_EXAMPLE = BOOK / "by-example"
SUMMARY = BOOK / "SUMMARY.md"

_ENTRY = re.compile(r"^\s*(?:\d+\.\s*)?\[(.+?)\]\((.+?)\)\s*$")
_FENCE = re.compile(r"^```([A-Za-z0-9_,+-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_UL = re.compile(r"^\s*[-*]\s+(.*)$")
_OL = re.compile(r"^\s*\d+\.\s+(.*)$")
_CODE = re.compile(r"`([^`]+)`")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "section"


def _inline(text: str) -> str:
    """行内：代码 > 链接 > 粗体 > 斜体；文本先转义。"""
    out: list[str] = []
    pos = 0
    for m in _CODE.finditer(text):
        out.append(_inline_text(text[pos:m.start()]))
        out.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        pos = m.end()
    out.append(_inline_text(text[pos:]))
    return "".join(out)


def _inline_text(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: '<a href="%s">%s</a>' % (_href(m.group(2)), m.group(1)),
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", text)
    return text


#: 正在渲染的源文件（repo 相对）。相对链接要靠它算落点：book/ 内的链接改成站内页，
#: 指向 book/ 之外的链接（`../../exercises/README.md`）改成 GitHub 上的原文。
CURRENT_SRC = "book/src/00-introduction.md"


def _href(url: str) -> str:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) or url.startswith("#"):
        return html.escape(url, quote=True)
    path, _, frag = url.partition("#")
    target = posixpath.normpath(
        posixpath.join(posixpath.dirname(CURRENT_SRC), path))
    if target.startswith("book/") and target.endswith(".md"):
        site = target[len("book/"): -3] + ".html"
        here = posixpath.dirname(CURRENT_SRC)[len("book/"):] or "."
        out = posixpath.relpath(site, here)
    else:
        out = ("https://github.com/Loment-Lang/Loment/blob/main/" + target)
    if frag:
        out += "#" + frag
    return html.escape(out, quote=True)


def _table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def render_markdown(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        m = _FENCE.match(stripped)
        if m:
            lang = m.group(1).split(",")[0]
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append("<pre><code%s>%s</code></pre>"
                       % (cls, html.escape("\n".join(body), quote=False)))

        elif stripped.startswith("<!--") and stripped.endswith("-->"):
            out.append(stripped)
            i += 1

        elif _HEADING.match(stripped):
            hm = _HEADING.match(stripped)
            level = len(hm.group(1))
            title = hm.group(2)
            out.append('<h%d id="%s">%s</h%d>'
                       % (level, _slug(re.sub(r"[`*]", "", title)), _inline(title), level))
            i += 1

        elif stripped.startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            head = _table_row(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_table_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>"
                       + "".join("<th>%s</th>" % _inline(c) for c in head)
                       + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in r) + "</tr>")
            out.append("</tbody></table>")

        elif stripped.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % _inline(" ".join(quote)))

        elif _UL.match(line) or _OL.match(line):
            ordered = bool(_OL.match(line))
            items: list[str] = []
            while i < len(lines):
                cur = lines[i]
                um = _UL.match(cur)
                om = _OL.match(cur)
                if um or om:
                    items.append((om or um).group(1))
                elif cur.startswith("  ") and cur.strip() and items:
                    items[-1] += " " + cur.strip()   # 续行
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>"
                       % (tag, "".join("<li>%s</li>" % _inline(x) for x in items), tag))

        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() \
                    and not _FENCE.match(lines[i].strip()) \
                    and not _HEADING.match(lines[i].strip()) \
                    and not lines[i].strip().startswith(("|", ">", "- ", "* ")) \
                    and not _OL.match(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>%s</p>" % _inline(" ".join(para)))

    return "\n".join(out)


def summary_entries() -> list[tuple[str, str, str]]:
    """(part, title, relative source path) —— 只读 SUMMARY.md。"""
    part = ""
    out: list[tuple[str, str, str]] = []
    for line in SUMMARY.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            part = line[3:].strip()
            continue
        m = _ENTRY.match(line)
        if m:
            out.append((part, m.group(1), m.group(2)))
    return out


def by_example_entries() -> list[tuple[str, str, str]]:
    out = []
    files = sorted(BY_EXAMPLE.glob("*.md"))
    files.sort(key=lambda p: (p.name != "README.md", p.name))
    for p in files:
        first = next((ln for ln in p.read_text(encoding="utf-8").split("\n")
                      if ln.startswith("# ")), "")
        title = first[2:].strip() or ("By Example" if p.name == "README.md" else p.stem)
        out.append(("By example", title, "by-example/" + p.name))
    return out


def _title_of(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


CSS = """
:root { --fg:#1b1b1f; --muted:#6b6b76; --line:#e3e3e8; --bg:#fff; --code:#f4f4f7; }
* { box-sizing:border-box; }
body { margin:0; color:var(--fg); background:var(--bg);
  font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif; }
a { color:#0b5cad; text-decoration:none; }
a:hover { text-decoration:underline; }
header.top { border-bottom:1px solid var(--line); padding:14px 24px; display:flex;
  gap:16px; align-items:baseline; }
header.top strong { font-size:17px; }
header.top span { color:var(--muted); font-size:13px; }
.layout { display:flex; max-width:1180px; margin:0 auto; }
nav.toc { width:290px; flex:none; padding:24px; border-right:1px solid var(--line);
  font-size:14px; position:sticky; top:0; align-self:flex-start; max-height:100vh;
  overflow:auto; }
nav.toc h4 { margin:18px 0 6px; font-size:12px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--muted); }
nav.toc ul { list-style:none; margin:0; padding:0; }
nav.toc li { margin:2px 0; }
nav.toc a.current { font-weight:600; color:var(--fg); }
main { padding:32px 40px 64px; min-width:0; flex:1; }
h1 { font-size:30px; line-height:1.25; margin:0 0 18px; }
h2 { font-size:22px; margin:34px 0 10px; }
h3 { font-size:18px; margin:26px 0 8px; }
p { margin:12px 0; }
pre { background:var(--code); padding:14px 16px; border-radius:6px; overflow:auto;
  font-size:13.5px; line-height:1.5; }
code { background:var(--code); padding:1px 4px; border-radius:4px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.92em; }
pre code { background:none; padding:0; font-size:inherit; }
blockquote { margin:16px 0; padding:2px 16px; border-left:3px solid var(--line);
  color:var(--muted); }
table { border-collapse:collapse; margin:16px 0; font-size:14px; }
th, td { border:1px solid var(--line); padding:6px 10px; text-align:left; }
th { background:var(--code); }
.pager { display:flex; justify-content:space-between; gap:16px; margin-top:44px;
  padding-top:16px; border-top:1px solid var(--line); font-size:14px; }
@media (max-width:820px) { .layout { display:block; } nav.toc { position:static;
  width:auto; max-height:none; border-right:0; border-bottom:1px solid var(--line); }
  main { padding:24px 20px 48px; } }
"""


def _page(title: str, body: str, toc: str, root: str, prev, nxt) -> str:
    pager = []
    if prev:
        pager.append('<a href="%s%s">← %s</a>' % (root, prev[1], html.escape(prev[0])))
    else:
        pager.append("<span></span>")
    if nxt:
        pager.append('<a href="%s%s">%s →</a>' % (root, nxt[1], html.escape(nxt[0])))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Loment</title>
<link rel="stylesheet" href="{root}assets/style.css">
</head>
<body>
<header class="top"><strong><a href="{root}index.html">Loment</a></strong>
<span>The Loment Book</span></header>
<div class="layout">
<nav class="toc">{toc}</nav>
<main>
{body}
<div class="pager">{''.join(pager)}</div>
</main>
</div>
</body>
</html>
"""


def _toc(entries, current: str, root: str) -> str:
    parts: list[str] = []
    seen = None
    for part, title, rel in entries:
        if part != seen:
            if seen is not None:
                parts.append("</ul>")
            parts.append('<h4>%s</h4><ul>' % html.escape(part or "Contents"))
            seen = part
        target = rel[: -3] + ".html"
        cls = ' class="current"' if rel == current else ""
        parts.append('<li><a href="%s%s"%s>%s</a></li>'
                     % (root, target, cls, _inline(title)))
    parts.append("</ul>")
    return "".join(parts)


def build(out: Path) -> list[Path]:
    book_entries = summary_entries()
    ex_entries = by_example_entries()
    entries = book_entries + ex_entries

    sources: list[tuple[str, Path, str]] = []
    for part, title, rel in entries:
        sources.append((title, BOOK / rel, rel))

    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8", newline="\n")

    written: list[Path] = []
    global CURRENT_SRC
    for idx, (title, path, rel) in enumerate(sources):
        CURRENT_SRC = "book/" + rel
        body = render_markdown(path.read_text(encoding="utf-8"))
        prev = (sources[idx - 1][0], sources[idx - 1][2][:-3] + ".html") if idx else None
        nxt = (sources[idx + 1][0], sources[idx + 1][2][:-3] + ".html") \
            if idx + 1 < len(sources) else None
        page = _page(title, body, _toc(entries, rel, "../"), "../", prev, nxt)
        dest = out / (rel[:-3] + ".html")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8", newline="\n")
        written.append(dest)

    index = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loment — learn</title><link rel="stylesheet" href="assets/style.css"></head>
<body>
<header class="top"><strong>Loment</strong><span>The Loment Book</span></header>
<div class="layout"><nav class="toc">{_toc(entries, "", "")}</nav>
<main><h1>The Loment Book</h1>
<p>Read the book from the beginning, or jump to <a href="by-example/01-hello.html">By Example</a>.
Every program in here is compiled and run by the build; if a chapter stops working, the gate
turns red.</p>
<p><a href="{entries[0][2][:-3]}.html">Start with {html.escape(entries[0][1])} →</a></p>
</main></div></body></html>
"""
    (out / "index.html").write_text(index, encoding="utf-8", newline="\n")
    written.append(out / "index.html")
    return written


def check(out: Path, written: list[Path]) -> list[str]:
    bad: list[str] = []
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    for p in written:
        text = p.read_text(encoding="utf-8")
        base = p.parent.relative_to(out)
        for href in re.findall(r'href="([^"]+)"', text):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("#"):
                continue
            target = posixpath.normpath((base / href.split("#")[0]).as_posix())
            if target and target not in files:
                bad.append(f"{p.name}: 链接落空 {href}")
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    written = build(out)
    print(f"loment_book_site: {len(written)} 页 -> {out}")
    if args.check:
        bad = check(out, written)
        if bad:
            print("\n".join(bad[:20]))
            return 1
        print("链接: 全部落得到实文件")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
