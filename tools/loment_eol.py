#!/usr/bin/env python3
# loment_eol.py — 检出行尾门禁 (LF 契约, docs/161)
#
# 为什么需要这道门: git 的 clean filter 按 `.gitattributes` 把 CRLF 归一成 LF 再哈希,
# 所以一条 CRLF 的工作区文件与 LF blob 哈希相同 —— `git status` / `git diff` **永远**报干净,
# 两个工作树字节不同也看不出来。而 Loment 的一批判据按**原始字节**读源码 (自举 lexer/parser/
# codegen 与参考实现逐字节比), 于是一条 CRLF 检出会以"逻辑红"的样子出现, 让人去修不存在的 bug。
# 本门禁把它变成指名道姓的检出红, 并给出两条命令的修法。
#
# 判据: .gitattributes 里 attr 带 `eol=lf` 的已跟踪文件, 工作区字节必须是 LF。
#   python tools/loment_eol.py          # 门禁: 0 = 一致 / 1 = 有 CRLF / 2 = 无法判定
#   python tools/loment_eol.py --fix    # 就地归一 (对该文件 git rm --cached + checkout HEAD)
#
# 注意 `--fix` 只改写"git 认为干净"的文件: 有实际内容改动的文件会被跳过, 绝不吞改动。

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOWN = 10
FIX = ("git rm --cached <文件> && git checkout HEAD -- <文件>\n"
       "    整目录: git rm -r --cached <目录> && git checkout HEAD -- <目录>")


def _git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def offenders() -> list[str] | None:
    """返回 attr 声明 eol=lf 但工作区不是 LF 的文件; None = 无法判定 (不在 git 工作树)。

    带 --others: 刚写出来还没进索引的文件也要看 —— 否则门禁对"新文件"是瞎的,
    等它进了索引才发现 CRLF, 那时红已经追不上"谁写的"了。
    """
    rc, out = _git("ls-files", "--eol", "--cached", "--others", "--exclude-standard", "-z")
    if rc != 0:
        return None
    bad = []
    for rec in out.split("\x00"):
        if not rec.strip():
            continue
        head, _, path = rec.partition("\t")
        parts = head.split()
        if len(parts) < 2:
            continue
        worktree, attr = parts[1], " ".join(parts[2:])
        # 只认"确实带 CR"的两类: w/crlf (全 CRLF) 与 w/mixed (部分行 CRLF)。
        # 其余 (w/lf, w/none, w/-, 或文件不在盘上) 都不是行尾问题 —— 缺文件 git status 会喊。
        if "eol=lf" in attr and worktree in ("w/crlf", "w/mixed"):
            bad.append(path)
    return bad


def _index_blob(path: str) -> bytes | None:
    """索引里那一份的字节 (索引侧恒为 LF); 未跟踪 -> None。"""
    r = subprocess.run(["git", "cat-file", "blob", f":{path}"], cwd=ROOT,
                       capture_output=True, shell=False)
    return r.stdout if r.returncode == 0 else None


def fix(bad: list[str]) -> int:
    """就地归一。判据是**索引里的字节**: 只有"工作区去掉 CR 之后与索引完全一致"才写盘,
    所以绝不会吞掉真实内容改动, 也不需要先 git status 判定谁是脏的。"""
    done = skipped = 0
    for p in bad:
        f = ROOT / p
        try:
            wb = f.read_bytes()
        except OSError as e:  # noqa: BLE001
            print(f"[SKIP] {p} —— 读不到 ({e})")
            skipped += 1
            continue
        blob = _index_blob(p)
        if blob is None:  # 未跟踪: 索引里没有可比对象, 按契约只做行尾归一
            f.write_bytes(wb.replace(b"\r\n", b"\n"))
            done += 1
            continue
        if wb.replace(b"\r\n", b"\n") != blob:
            print(f"[SKIP] {p} —— 工作区与索引内容不一致 (有真实改动), 不覆盖")
            skipped += 1
            continue
        f.write_bytes(blob)
        done += 1
    print(f"loment_eol --fix: 归一 {done} 件, 跳过 {skipped} 件")
    return 0 if skipped == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_eol")
    ap.add_argument("--fix", action="store_true", help="就地归一 CRLF 文件")
    a = ap.parse_args(argv)

    bad = offenders()
    if bad is None:
        # 行首写成 `SKIP`，**不带名字**：这是**整条检查**没跑（不是某一条判据跳过），
        # 门禁按"整条套件级跳过"认它，于是要在 `gate-skips.txt` 里登记一行 `<套件> *`。
        # 原先这行是 `loment_eol: SKIP —— …`，行首不是 SKIP，机器一条都数不到。
        print("SKIP  不是 git 工作树 (行尾契约由 .gitattributes 与检出共同保证)")
        return 2
    if a.fix:
        return fix(bad)
    if not bad:
        print("loment_eol: 行尾一致 (eol=lf 声明的已跟踪文件全部 LF)")
        return 0
    for p in bad[:SHOWN]:
        print(f"[CRLF] {p}")
    more = len(bad) - SHOWN
    tail = f"，另有 {more} 件" if more > 0 else ""
    print(f"loment_eol: {len(bad)} 件工作区不是 LF{tail}")
    print("  这是**检出/包装**问题, 不是逻辑: 换行不进 git 的内容比较"
          " (git diff 对纯 CRLF 改动可以给出空 diff), 也没有任何命令会去改写这些字节。")
    print("  修: python tools/loment_eol.py --fix   (未跟踪的新文件按行尾直接归一)")
    print("  等价手工 (已跟踪文件):")
    print(f"    {FIX}")
    print("  根因若在生成器: 写仓库内工件要用 newline='\\n' (见 lomfmt/lomdoc/loment_status)。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
