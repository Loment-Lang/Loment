#!/usr/bin/env python3
# lompi_sync.py — lompi 源码：开发正本 <-> 仓内副本的同步与校验 (docs/170)
#
# lompi **在外面开发，本仓只放随包发布的快照** —— 有两组配对：
#
#   1. 源码模块 + fixture:  正本 `D:\Dev\Lolment-ku\lompi`（`--from-dir` / `LOMENT_LOMPI_DEV`）
#                           -> 仓内 `lompi/`
#   2. agent 指南:          正本 `~/.claude/skills/lompi/SKILL.md`（lompi 线在那里写它）
#                           -> 仓内 `.claude/skills/lompi/SKILL.md`
#
# 打包与判据都读**仓内**这份，所以"两份是不是同一份"必须能被查出来，否则就是 CLAUDE.md
# 里那句「复制出第二份必然漂」。两组各自独立：某一组的正本不在本机，只跳过那一组。
#
#   python tools/lompi_sync.py                 # 门禁模式: 逐字节比 (正本不在本机则跳过)
#   python tools/lompi_sync.py --from-dev      # 正本 -> 仓 (把开发区的改动收进来)
#   python tools/lompi_sync.py --to-dev        # 仓 -> 正本 (在仓里改了要同步回去)
#   python tools/lompi_sync.py --status        # 只打印两边的摘要
# 退出码: 0 = 一致 (或正本不在本机，已跳过) / 1 = 有差异 / 2 = 用法错误
#
# 为什么门禁模式**跳过而不报错**：正本是某个开发者机器上的路径，别人的机器 / CI 上根本
# 不存在。跳过时打一行明说，免得读的人以为"查过了"。仓内那份自身的完整性（文件齐不齐）
# 是**无条件**查的。

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_DIR = ROOT / "lompi"

#: 正本默认位置（开发者工作区）。换机器用 `LOMENT_LOMPI_DEV` 或 `--from-dir`。
DEFAULT_DEV = Path(r"D:\Dev\Lolment-ku\lompi")
#: 指南的正本：lompi 线在**用户级 skills 目录**里写它（实测 2026-09-15 22:52 那份比仓里新）。
DEFAULT_SKILL = Path.home() / ".claude" / "skills" / "lompi"
#: 指南在仓内的落脚点（也是安装器往外拷的那份）。
REPO_SKILL = ROOT / ".claude" / "skills" / "lompi"
SKILL_FILES = ("SKILL.md",)
#: **标准库 store** 的正本（开发者工作区），仓内落在 `lompi/store/`。
#: 它是 lompi 的库（`std` 127 个模块 + `host` 7 个），随 Loment 一起装 —— 用户 2026-09-15 定。
DEFAULT_STORE = Path(r"D:\Dev\Lolment-ku\store")
REPO_STORE = ROOT / "lompi" / "store"

#: 跟随同步的源文件：**产品模块** + **自检驱动**。
#:
#: `lpi_test.lomt` 不是 `bin/lompi` 的组成部分（发行包不装它），但**要一起收** ——
#: 它是逐模块的自检汇总（`selftest_<模块>() -> u32`，退出码即结论），仓侧判据拿它当
#: "lompi 在这棵树上是真的能跑对"的证据，比只冒烟一条命令强得多。
#:
#: （2026-09-15 订正）本条原先写着"`lpi_test` 引用的 `pkg_file_exists` 在源码里不存在、
#: 编不过"。**那句是错的** —— `pkg_file_exists` 只出现在那句注释自己身上，lompi 源码一处
#: 都没有；当时编不过是另一回事（快照早于正本）。lompi 线照这句错信息排查过一轮，所以
#: 留个记号：写"实测"就要写清是**哪一版、哪条命令**，否则下一个读的人从错的前提出发。

MODULES = ("lompi.lomt", "lpi_cli.lomt", "lpi_idn.lomt", "lpi_pkg.lomt",
           "lpi_sha.lomt", "lpi_dir.lomt", "lpi_txt.lomt", "lpi_sys.lomt",
           "lpi_test.lomt")

#: 一并跟着走的目录（判据要用它当 store）。
DIRS = ("fixture",)
#: 整棵树照单全收的那种配对（store）用这个当 `names`：它的布局是
#: `<name>/<version>/*.lomt`，没有"顶层文件白名单"可言 —— 逐层列出来只会漏。
WHOLE_TREE: tuple[str, ...] | None = None


def _norm(b: bytes) -> bytes:
    """文本按**通用换行**归一。正本那边的行尾是编辑器写的、**并不统一**（实测
    `lpi_dir.lomt` 是 LF 而 `lpi_cli.lomt` 是 CRLF）；本仓要求 `*.lomt` 一律 LF
    （.gitattributes + `loment_eol` 门禁）。所以进仓方向归一，比也按归一比 ——
    否则"行尾不同"会被报成内容漂移，那是噪声。二进制（含 NUL）原样。"""
    if bytes([0]) in b:      # 含 NUL = 二进制, 原样
        return b
    return b.replace(bytes([13, 10]), bytes([10])).replace(bytes([13]), bytes([10]))


def _to_crlf(b: bytes) -> bytes:
    """LF -> CRLF。二进制（含 NUL）原样。推回正本时用来**保住对方编辑器的行尾**。"""
    if bytes([0]) in b:
        return b
    return b.replace(bytes([13, 10]), bytes([10])).replace(bytes([10]), bytes([13, 10]))


def _sha(p: Path) -> str:
    return hashlib.sha256(_norm(p.read_bytes())).hexdigest()


def _walk(d: Path) -> list[str]:
    return sorted(x.relative_to(d).as_posix() for x in d.rglob("*") if x.is_file())


def _files(side: Path, names=MODULES, subdirs=DIRS) -> list[str]:
    if names is None:                     # 整棵树照单全收（store）
        return _walk(side)
    out = [m for m in names if (side / m).is_file()]
    for d in subdirs:
        if (side / d).is_dir():
            out += [f"{d}/{rel}" for rel in _walk(side / d)]
    return out


def _files_pair(pair) -> list[str]:
    return _files(pair["src"], pair["names"], pair["dirs"])


def _dev_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("LOMENT_LOMPI_DEV")
    return Path(env) if env else DEFAULT_DEV


def _compare(pair) -> tuple[list[str], list[str], list[str]]:
    """返回 (内容不同, 只在正本有, 只在仓里有)。"""
    dev, repo = pair["src"], pair["dst"]
    dev_files = set(_files(dev, pair["names"], pair["dirs"]))
    repo_files = set(_files(repo, pair["names"], pair["dirs"]))
    diff = sorted(f for f in dev_files & repo_files
                  if _norm((dev / f).read_bytes()) != _norm((repo / f).read_bytes()))
    return diff, sorted(dev_files - repo_files), sorted(repo_files - dev_files)


def _copy(pair, normalize: bool) -> list[str]:
    """正本 -> 仓内副本，返回改动的相对路径。只动这一组的清单里的东西，别的一概不碰。

    `normalize=True`（进仓方向）把文本写成 LF —— 正本那边行尾不统一，仓里必须统一。
    `normalize=False`（推回正本方向）**按目标原来的行尾写**，不去动别人编辑器定的行尾。

    **两个方向都按 `_norm` 判"变没变"**（不是按原字节）。这一条是 2026-09-16 补的：
    原先推回正本时按原字节比，于是"正本是 CRLF、仓里是 LF"被当成**改动**，而判据
    （`_compare`）又按归一后比、报"一致" —— 两边口径不一，结果是 `--to-dev` 把开发区
    **全部** `.lomt` 的行尾抹平（那次只想推一份指南，三组配对一起被执行了，
    开发区 163 个文件从 CRLF 变成 LF）。
    """
    src, dst = pair["src"], pair["dst"]
    names, subdirs = pair["names"], pair["dirs"]
    changed: list[str] = []
    dev_files = set(_files(src, names, subdirs))
    for f in _files(dst, names, subdirs):
        # 只在**进仓**方向跟着删。推回正本时"仓里没有、正本有"意味着别人在正本那边
        # 加了东西还没收进来 —— 那时候删掉就是**毁掉别人的活**，只让它被门禁报出来。
        if f not in dev_files:
            if normalize:
                (dst / f).unlink()
                changed.append(f"- {f}")
            else:
                changed.append(f"! {f} (仓里没有, 正本独有 -- 没动, 先收进来)")
    for f in sorted(dev_files):
        d = dst / f
        d.parent.mkdir(parents=True, exist_ok=True)
        raw = (src / f).read_bytes()
        if normalize:
            want = _norm(raw)
            if d.is_file() and d.read_bytes() == want:
                continue
            d.write_bytes(want)
        else:
            if d.is_file() and _norm(d.read_bytes()) == _norm(raw):
                continue                        # 只有行尾不同 = 没改
            crlf = d.is_file() and bytes([13, 10]) in d.read_bytes()
            d.write_bytes(_to_crlf(raw) if crlf else raw)
        changed.append(f"+ {f}")
    return changed


def _pairs(arg: str | None) -> list[dict]:
    """两组配对。`--from-dir` 只改源码那组的正本（指南那组的正本不在那儿）。"""
    return [
        {"label": "源码", "src": _dev_dir(arg), "dst": REPO_DIR,
         "names": MODULES, "dirs": DIRS},
        {"label": "指南", "src": Path(os.environ.get("LOMENT_LOMPI_SKILL") or DEFAULT_SKILL),
         "dst": REPO_SKILL, "names": SKILL_FILES, "dirs": ()},
        # 标准库 store：整棵树照收（`<name>/<version>/*.lomt`），所以 names=None。
        {"label": "标准库", "src": Path(os.environ.get("LOMENT_LOMPI_STORE") or DEFAULT_STORE),
         "dst": REPO_STORE, "names": WHOLE_TREE, "dirs": ()},
    ]


def _selfcheck(prs) -> list[str]:
    """仓内那两份**自己**的完整性 —— 与正本在不在无关，无条件查。"""
    bad = []
    for pair in prs:
        if not pair["dst"].is_dir():
            bad.append(str(pair["dst"]))
            continue
        if pair["names"] is None:          # 整棵树：只要求非空
            if not _walk(pair["dst"]):
                bad.append(f'{pair["label"]}: {pair["dst"]} 是空的')
            continue
        for m in pair["names"]:
            if not (pair["dst"] / m).is_file():
                bad.append(f'{pair["label"]}: {pair["dst"].name}/{m}')
        for d in pair["dirs"]:
            if not (pair["dst"] / d).is_dir():
                bad.append(f'{pair["label"]}: {pair["dst"].name}/{d}/')
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lompi_sync")
    ap.add_argument("--check", action="store_true", help="门禁模式 (默认)")
    ap.add_argument("--from-dev", action="store_true", help="正本 -> 仓 (把外面的改动收进来)")
    ap.add_argument("--to-dev", action="store_true", help="仓 -> 正本 (在仓里改了要同步回去)")
    ap.add_argument("--status", action="store_true", help="只打印摘要")
    ap.add_argument("--from-dir", metavar="DIR", help="源码正本目录 (默认见文件头)")
    a = ap.parse_args(argv)
    prs = _pairs(a.from_dir)

    # `--from-dev` 就是来**补全**仓内那份的，所以"仓里还缺文件"不能拦它 —— 那个自检是给
    # 读模式用的。拷完再自查一遍（见下），比拦在前面有用。
    if a.from_dev or a.to_dev:
        rc = 0
        for pair in prs:
            src, dst = (pair["src"], pair["dst"]) if a.from_dev else (pair["dst"], pair["src"])
            if not src.is_dir():
                print(f"[ERR] {pair['label']}的源目录不存在: {src}")
                rc = 2
                continue
            changed = _copy({"src": src, "dst": dst,
                             "names": pair["names"], "dirs": pair["dirs"]},
                            normalize=a.from_dev)
            if not changed:
                print(f"lompi_sync[{pair['label']}]: 两边本来就一致，没动")
                continue
            print(f"lompi_sync[{pair['label']}]: {src} -> {dst}，改了 {len(changed)} 个:")
            for c in changed:
                print(f"  {c}")
        bad = _selfcheck(prs)
        if bad:
            print(f"[ERR] 拷完之后仓内仍不完整，缺: {bad}")
            return 1
        if a.from_dev and rc == 0:
            print("  记得跑 `python tools/loment_dist.py --emit` 重出包")
        return rc

    bad = _selfcheck(prs)
    if bad:
        print(f"[ERR] 仓内不完整，缺: {bad}")
        return 1

    rc = 0
    for pair in prs:
        if not pair["src"].is_dir():
            # 正本不在本机（CI / 别人的机器）。**说清楚**，别让人以为查过了。
            # 行首写成 `SKIP` 是给**门禁**看的（`#371`）：这一行原先虽然明说，但机器认不出来
            # —— `count_skips` 只数行首是 SKIP 的，于是"这一组没比"在预算里是一笔糊涂账。
            # 带名字（`<组名>: <原因>`）之后它进得了 `gate-skips.txt`，谁在哪台机器上跳过
            # 得掉就一清二楚。
            print(f"SKIP  lompi_sync[{pair['label']}]: 正本不在本机 ({pair['src']}) —— 跳过比对")
            continue
        diff, only_dev, only_repo = _compare(pair)
        if not (diff or only_dev or only_repo):
            print(f"lompi_sync[{pair['label']}]: 与正本逐字节一致")
            continue
        rc = 1
        for f in diff:
            print(f"[DIFF] {pair['label']}/{f}  正本 {_sha(pair['src'] / f)[:12]}"
                  f"  仓 {_sha(pair['dst'] / f)[:12]}")
        for f in only_dev:
            print(f"[正本独有] {pair['label']}/{f}")
        for f in only_repo:
            print(f"[仓内独有] {pair['label']}/{f}")
    if rc:
        print("lompi_sync: 有配对与正本不一致 —— 跑 --from-dev 收进来，或 --to-dev 推出去")
    return rc


if __name__ == "__main__":
    sys.exit(main())
