#!/usr/bin/env python3
# loment_state.py — 权威状态在 `origin`，不在你面前这棵本地树（CLAUDE.md 第一节）
#
# **本仓的检出静默落后是常态**：2026-09-25 实测本地 `main` 比 `origin/main` 落后 181 个
# 提交，树里连 `.github/` 都不存在。落后**不会报错** —— 拿旧树下结论，看起来和读对了
# 一样，而且代价很贵（0.1.4 早已发布、公告都发了，还有人读着本地树在说"等 0.1.4"）。
# 这个工具把"该读 `origin` 的那几件"一次打出来，让那条约定**可执行**，不必靠人记得。
#
#   python tools/loment_state.py             # 先 fetch，再报权威状态
#   python tools/loment_state.py --no-fetch  # 不碰网络，读已有的远端引用
#   python tools/loment_state.py --check     # 这棵树建在权威基底上吗？（0 是 / 1 否 / 2 无法判定）
#   python tools/loment_state.py --ci        # 另查 GitHub Actions 最近几轮（要 gh，缺就 SKIP）
#
# 退出码: 0 = 正常 / 1 = --check 判否 / 2 = 用法错误或无法判定。
#
# **两个"为什么这么写"**：
#   * 版本号**正则读源码**而不 import `loment_release` —— 要读的是 **`origin` 上那一份**，
#     而 import 只能读到工作树里这一份，那正是本工具要避开的东西。
#   * 权威引用写成**常量**而不做成参数 —— 这工具存在的全部理由就是"默认读错了地方"，
#     再留个开关让人读别处，等于把唯一的价值变成可选项。

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: 权威引用。见头上那段"为什么是常量"。
AUTHORITY = "origin/main"
#: 版本名的单一真源在 `tools/loment_release.py`（`RELEASE` / `RELEASE_NAME`）。
RELEASE_PATH = "tools/loment_release.py"
RELEASE_RE = re.compile(r'^RELEASE\s*=\s*"([^"]+)"', re.M)


def git(*args: str, root: Path = ROOT) -> tuple[int, str]:
    """跑一条 git，返回 (rc, stdout.strip())。**不走 shell** —— `origin/main:<路径>` 里那个
    冒号在 MSYS 的 Git Bash 下会被改写成 `;`，而 argv 直传不经那层转换。"""
    r = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip()


def parse_release(src: str) -> str | None:
    """从 `loment_release.py` 的源码里取 `RELEASE`；取不到返回 None。"""
    m = RELEASE_RE.search(src)
    return m.group(1) if m else None


def parse_counts(out: str) -> tuple[int, int] | None:
    """解析 `git rev-list --left-right --count A...B` 的输出 -> **(领先, 落后)**。

    计数行左=第一个引用独有、右=第二个引用独有。这里第一个引用是 `HEAD`、第二个是
    权威，于是左 = **HEAD 独有 = 领先**、右 = **权威独有 = 落后**。行不成形时返回 None
    （**不猜** —— "落后 0" 是最危险的那个值，看着像完全同步）。
    """
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def have_authority(root: Path = ROOT) -> bool:
    """`origin/main` 这个引用在不在（fetch 过 / 是完整克隆 / 不是 tarball 检出）。"""
    rc, _ = git("rev-parse", "--verify", "--quiet", f"{AUTHORITY}^{{commit}}", root=root)
    return rc == 0


def on_authority(root: Path = ROOT) -> bool:
    """`HEAD` 含 `origin/main` 吗 —— 即这棵树是不是建在**权威基底**上。

    含 = 我的提交是叠在权威之上的（PR 分支的常态）。不含 = 我落后或分叉了，
    这时"拿这棵树下的结论"就该被怀疑。
    """
    rc, _ = git("merge-base", "--is-ancestor", AUTHORITY, "HEAD", root=root)
    return rc == 0


def show_authority_file(rel: str, root: Path = ROOT) -> str | None:
    """读 **`origin/main` 上**那份文件的内容；引用或路径不存在时返回 None。"""
    rc, out = git("show", f"{AUTHORITY}:{rel}", root=root)
    return out if rc == 0 else None


def state(root: Path = ROOT, fetch: bool = True) -> dict:
    """把该看的一次收齐。**任何一步失败都不抛** —— 这是个报告工具，不是判据。"""
    s: dict = {"root": str(root), "authority": AUTHORITY, "fetched": None}
    if fetch:
        s["fetched"] = git("fetch", "--quiet", "origin", root=root)[0] == 0
    s["have_authority"] = have_authority(root)
    s["branch"] = git("rev-parse", "--abbrev-ref", "HEAD", root=root)[1] or "(detached)"
    s["head"] = git("rev-parse", "--short", "HEAD", root=root)[1]
    s["authority_head"] = git("rev-parse", "--short", AUTHORITY, root=root)[1]
    s["authority_date"] = git("log", "-1", "--format=%cs", AUTHORITY, root=root)[1]
    s["behind_commits"] = git("rev-list", "--count", f"HEAD..{AUTHORITY}", root=root)[1]
    rc, out = git("rev-list", "--left-right", "--count", f"HEAD...{AUTHORITY}", root=root)
    s["counts"] = parse_counts(out) if rc == 0 else None
    s["dirty"] = bool(git("status", "--porcelain", root=root)[1])
    if s["have_authority"]:
        s["on_authority"] = on_authority(root)
        s["authority_release"] = parse_release(show_authority_file(RELEASE_PATH, root=root) or "")
        rc, tags = git("tag", "--merged", AUTHORITY, "--sort=-v:refname", root=root)
        s["recent_tags"] = tags.splitlines()[:3] if rc == 0 else []
    else:
        s["on_authority"] = None
        s["authority_release"] = None
        s["recent_tags"] = []
    local_src = (root / RELEASE_PATH)
    s["local_release"] = parse_release(local_src.read_text(encoding="utf-8")) if local_src.exists() else None
    return s


def verdict(s: dict) -> tuple[int, str]:
    """`--check` 的判据：**这棵树建在权威基底上吗**。

    刻意**不**判"本地 RELEASE == 权威 RELEASE"：升版本的那个 PR 里两者本来就该不同
    （工作树比 `origin/main` 新），拿它当红会对着**正确的状态**报错。这里判的只有一件
    —— 我的提交有没有叠在权威之上；版本差异只**打印**，不判。
    """
    if not s["have_authority"]:
        return 2, f"无法判定：本地没有 `{AUTHORITY}` 这个引用（没 fetch 过？这是个 tarball 检出？）"
    if s["on_authority"]:
        return 0, f"OK：这棵树含 `{AUTHORITY}`（权威基底之上）"
    if s["counts"]:
        ahead, behind = s["counts"]
        return 1, (f"这棵树**不含** `{AUTHORITY}`：落后 {behind} 个提交、领先 {ahead} 个"
                   f" —— 拿它下的结论要先核 `git show {AUTHORITY}:<路径>`")
    return 1, f"这棵树**不含** `{AUTHORITY}`（分叉或落后）"


def _ci_lines() -> list[str]:
    """可选的 CI 一栏。缺 `gh` 或没登录时**可见地 SKIP**，不装作查过了。"""
    gh = shutil.which("gh")
    if not gh:
        return ["  CI      : SKIP（没有 gh）"]
    r = subprocess.run([gh, "run", "list", "--limit", "5",
                        "--json", "name,conclusion,headBranch,createdAt",
                        "--jq", '.[] | "  CI      : \\(.conclusion // "running")  \\(.name)  \\(.headBranch)"'],
                       capture_output=True, text=True, shell=False,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return [f"  CI      : SKIP（gh 查不动：{r.stderr.strip().splitlines()[:1]}）"]
    return r.stdout.strip().splitlines() or ["  CI      : （这个仓还没有 run）"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_state")
    ap.add_argument("--no-fetch", action="store_true", help="不碰网络，读已有的远端引用")
    ap.add_argument("--check", action="store_true", help="判「这棵树建在权威基底上吗」")
    ap.add_argument("--ci", action="store_true", help="另查 GitHub Actions 最近几轮（要 gh）")
    a = ap.parse_args(argv)

    s = state(fetch=not a.no_fetch)
    print(f"loment_state —— 权威是 `{s['authority']}`，不是这棵工作树")
    if s["fetched"] is False:
        print("  fetch   : 失败（离线？）—— 下面读的是**上一次**取到的远端引用")
    print(f"  本树    : {s['branch']} @ {s['head']}"
          f"{'  [工作区脏]' if s['dirty'] else ''}")
    print(f"  权威    : {s['authority']} @ {s['authority_head']}  ({s['authority_date']})")
    if s["counts"]:
        ahead, behind = s["counts"]
        if not behind and not ahead:
            print("  相对权威: 同步（一个提交都不差）")
        else:
            print(f"  相对权威: 落后 {behind} / 领先 {ahead} 个提交")
    else:
        print("  相对权威: （算不出 —— 没有可比引用）")
    print(f"  版本    : 权威 {s['authority_release']} / 本树 {s['local_release']}"
          f"{'   <-- 本树过期，别拿它判断版本' if s['authority_release'] and s['local_release'] and s['authority_release'] != s['local_release'] else ''}")
    print(f"  最近 tag: {' '.join(s['recent_tags']) or '(权威上没有 tag)'}")
    if a.ci:
        for ln in _ci_lines():
            print(ln)

    rc, why = verdict(s)
    if a.check:
        print(f"\n[{'OK' if rc == 0 else 'NO'}] {why}")
        return rc
    return 0 if rc != 2 else 2


if __name__ == "__main__":
    sys.exit(main())
