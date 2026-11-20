#!/usr/bin/env python3
"""tools/lomf.py —— `.lomf`（Loment 修改件 / modification file）的套用器。

带 Loment 一起生长的那部分代码（运行时 IR、自举侧镜像、判据）常常改的是**同一段文本的
多个副本**，而那段文本**嵌在字符串常量里** —— 行号漂得比代码还快。所以修改件不按行号，
按**逐字节的片段**定位，并且**每一处都写清期望出现几次**：

  * 少了（有人删过一份副本）→ 报错；
  * 多了（有人又抄了一份）→ 报错；
  * 不一致就**什么都不写**（先全量校验，再落盘）—— 宁可不动，不许猜。

格式（`lomf 1`）：

    lomf 1
    mod <短名>
    about <一句话：为什么改、改的是什么>
    about <可以续行，想写几行写几行>

    edit <仓库相对路径> expect <n>
    old <<<
    <逐字节的旧文本>
    >>>
    new <<<
    <逐字节的新文本>
    >>>

用法：

    python tools/lomf.py --list  <x.lomf>    # 这一笔动了哪些文件、每处现在命中几次
    python tools/lomf.py --check <x.lomf>    # 能不能套（不改盘）；已套用也报通过
    python tools/lomf.py --apply <x.lomf>    # 套用（就地改；幂等）

退出码：0 = 通过 / 已套用；1 = 对不上（报文里给出实际次数）；2 = 修改件本身不合法。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _safepath import safe_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: 每一处替换的三种状态。
CAN = "can"          # 旧文本正好出现 expect 次 —— 可以套
DONE = "done"        # 旧文本 0 次、新文本正好 expect 次 —— 已经套过了（幂等）
BAD = "bad"          # 其它一切 —— 报错，不猜


class LomfError(Exception):
    """修改件本身不合法（语法/版本/路径）。"""


class Edit:
    def __init__(self, raw: str, path: str, expect: int, old: str, new: str, line: int):
        self.raw = raw
        self.path = path
        self.expect = expect
        self.old = old
        self.new = new
        self.line = line

    def state(self) -> tuple[str, int, int]:
        p = safe_path(ROOT / self.path)
        if not Path(p).is_file():
            return BAD, -1, -1
        text = Path(p).read_bytes().decode("utf-8")
        return classify(text.count(self.old), text.count(self.new), self.expect)


def classify(old_n: int, new_n: int, expect: int) -> tuple[str, int, int]:
    if old_n == expect:
        return CAN, old_n, new_n
    if old_n == 0 and new_n == expect:
        return DONE, old_n, new_n
    return BAD, old_n, new_n


def parse(text: str) -> tuple[str, str, list[Edit]]:
    lines = text.split("\n")
    slug = ""
    about: list[str] = []
    edits: list[Edit] = []
    i = 0

    def die(msg: str, ln: int) -> None:
        raise LomfError(f"第 {ln} 行: {msg}")

    if not lines or lines[0].strip() != "lomf 1":
        raise LomfError("头一行必须是 `lomf 1`（格式版本）")
    i = 1
    while i < len(lines):
        ln = i + 1
        s = lines[i].rstrip()
        if s == "" or s.lstrip().startswith("#"):
            i += 1
            continue
        if s.startswith("mod "):
            slug = s[4:].strip()
            i += 1
            continue
        if s == "about" or s.startswith("about "):
            about.append(s[5:].strip())
            i += 1
            continue
        if s.startswith("edit "):
            head = s[5:].split()
            if len(head) != 3 or head[1] != "expect":
                die("`edit <路径> expect <次数>` —— `expect` 必写（缺省值就是猜）", ln)
            try:
                expect = int(head[2])
            except ValueError:
                die(f"`expect` 要是个整数，收到 {head[2]!r}", ln)
            if expect < 1:
                die("`expect` 至少是 1", ln)
            path = head[0]
            blocks = []
            i += 1                      # 走到 `old <<<` 那一行
            for tag in ("old", "new"):
                if i >= len(lines) or lines[i].strip() != f"{tag} <<<":
                    die(f"这里应该是 `{tag} <<<`", ln)
                j = i + 1
                body: list[str] = []
                while j < len(lines) and lines[j].strip() != ">>>":
                    body.append(lines[j])
                    j += 1
                if j >= len(lines):
                    die(f"`{tag} <<<` 没有收尾的 `>>>`", ln)
                blocks.append("\n".join(body) + "\n")
                i = j + 1
            old, new = blocks
            if old in new or new in old:
                die(f"`{path}` 那一处：旧文本与新文本互为子串 —— 套用是**幂等**的"
                    f"（靠「旧文本 0 次、新文本 expect 次」认「已经套过」），"
                    f"而互为子串时套第二次会**再改一遍**（插入式替换最典型）。"
                    f"解决办法：把邻近一行的上下文也写进两块，让两者互不为子串。", ln)
            edits.append(Edit(s, path, expect, old, new, ln))
            continue
        die(f"不认识的指令 {s!r}", ln)
    if not slug:
        raise LomfError("缺 `mod <短名>`")
    if not edits:
        raise LomfError("一个 `edit` 都没有 —— 这不是一份修改件")
    return slug, "\n".join(about), edits


def load(p: Path) -> tuple[str, str, list[Edit]]:
    return parse(Path(safe_path(p)).read_bytes().decode("utf-8"))


def cmd_list(src: Path) -> int:
    slug, about, edits = load(src)
    print(f"修改件 {slug}  ({src})")
    for a in about.split("\n"):
        print(f"  {a}")
    for e in edits:
        st, old_n, new_n = e.state()
        mark = {CAN: "待套用", DONE: "已套用", BAD: "对不上"}[st]
        print(f"  {e.path:38s} expect={e.expect}  旧={old_n} 新={new_n}   {mark}")
    return 0


def cmd_apply(src: Path, write: bool) -> int:
    slug, _about, edits = load(src)
    states = [(e, *e.state()) for e in edits]
    bad = [(e, st, o, n) for e, st, o, n in states if st == BAD]
    if bad:
        print(f"lomf: {slug} —— 有 {len(bad)} 处对不上，**一处也不动**：", file=sys.stderr)
        for e, _st, o, n in bad:
            print(f"  {e.path}: 旧文本 {o} 次（期望 {e.expect}）、新文本 {n} 次"
                  f"（第 {e.line} 行那条 edit）", file=sys.stderr)
        print("  —— 要么树变了（别人改过同一段），要么这一笔已经部分落地；"
              "先看清再动，不许猜。", file=sys.stderr)
        return 1

    todo = [(e, st) for e, st, _o, _n in states if st == CAN]
    if not todo:
        print(f"lomf: {slug} —— 已经套用过了，无事可做（{len(edits)} 处全在）")
        return 0

    # 先全量算出结果，再一起落盘 —— 中途不写半个文件。
    out: dict[str, str] = {}
    for e, _st in todo:
        p = Path(safe_path(ROOT / e.path))
        text = out.get(e.path, p.read_bytes().decode("utf-8"))
        newtext = text.replace(e.old, e.new)
        if newtext == text:
            raise LomfError(f"{e.path}: 替换没产生变化（不该发生）")
        out[e.path] = newtext
    if write:
        for rel, text in out.items():
            Path(safe_path(ROOT / rel)).write_bytes(text.encode("utf-8"))
    for e, _st in todo:
        print(f"lomf: {e.path:38s} 套用 {e.expect} 处")
    for e, st, _o, _n in states:
        if st == DONE:
            print(f"lomf: {e.path:38s} 早先已套用")
    print(f"lomf: {slug} —— {'已写盘' if write else '校验通过（未写盘）'}，"
          f"动了 {len(out)} 个文件")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description="套用 `.lomf` 修改件")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列出这一笔动了哪些文件、现在命中几次")
    g.add_argument("--check", action="store_true", help="能不能套（不改盘）")
    g.add_argument("--apply", action="store_true", help="套用（就地改，幂等）")
    ap.add_argument("lomf", help="修改件路径")
    a = ap.parse_args()
    src = Path(a.lomf)
    try:
        if a.list:
            return cmd_list(src)
        return cmd_apply(src, write=a.apply)
    except LomfError as e:
        print(f"lomf: 修改件不合法 —— {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
