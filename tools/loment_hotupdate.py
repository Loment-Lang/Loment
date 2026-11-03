#!/usr/bin/env python3
# loment_hotupdate.py — `loment hotupdate`：一次热更新的全部动作，输出只有四行。
#
#   checking hotupdate
#   prerelease : v0.1.5-hot.1
#   downloading...  100%
#   checking...  100%
#   update....  100%
#
# `checking`/`downloading`/`update` 的百分比是真的：下载按资源文件数，检查按准入的六关
# （`loment_update.check` 的 `progress` 回调），更新按进程退出前后。不是动画。
#
# 传输在进程外（这里从 `--from` 给的目录复制，等价于把 GitHub 的资产下到本地），
# 换的那条路只在本地读一个文件 —— 与 `docs/288` 的分工一致。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_hotfix as hf      # noqa: E402
import loment_staging as st     # noqa: E402
import loment_update as lu      # noqa: E402

_TTY = sys.stdout.isatty()


def _pct(label: str, i: int, n: int, *, end: bool = False) -> None:
    """每一段只打**一行**，数字是真的（这一段走完了几分之几）。

    不画动画、不刷中间态：这条命令的输出是给人看结论的。
    """
    if i < n and not end:
        return
    pct = 100 if n <= 0 else int(i * 100 / n)
    sys.stdout.write(f"{label} {pct:3d}%\n")
    sys.stdout.flush()


def _wsl(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="loment hotupdate")
    ap.add_argument("--work", required=True, help="模拟用的工作目录（仓库外）")
    ap.add_argument("--tag", default=None, help="目标 Pre-Release（默认取 --from 里那份清单）")
    ap.add_argument("--from", dest="src", default=None, help="资产的来源目录（默认 <work>/release）")
    args = ap.parse_args(argv)

    work = Path(args.work).resolve()
    src = Path(args.src).resolve() if args.src else work / "release"
    tree = work / "tree-old"
    os.environ["LOMENT_HOT_HOME"] = str(work / "hot")

    print("checking hotupdate")
    if not (src / "hotfix.json").is_file():
        print("no release found", file=sys.stderr)
        return 2
    man = __import__("json").loads((src / "hotfix.json").read_text(encoding="utf-8"))
    tag = args.tag or man.get("release", "")
    print(f"prerelease : {tag}")
    if man.get("release") != tag:
        print(f"tag mismatch: manifest says {man.get('release')!r}", file=sys.stderr)
        return 2

    # ---- downloading（按资产个数计）
    assets = sorted(p for p in src.iterdir() if p.is_file())
    tmp = Path(tempfile.mkdtemp(prefix="loment-hotupdate-"))
    for i, p in enumerate(assets, start=1):
        shutil.copyfile(p, tmp / p.name)
        _pct("downloading...", i, len(assets), end=(i == len(assets)))

    # ---- checking + 放进暂存目录（准入只有一份实现：`loment_update.check`）
    seen = {"i": 0, "n": 6}

    def _prog(i: int, n: int) -> None:
        seen["i"], seen["n"] = i, n
        _pct("checking...", i, n, end=(i == n))

    got = st.stage(tmp, tree, tag, progress=_prog)
    shutil.rmtree(tmp, ignore_errors=True)
    if got.parent.name == "rejected":
        if seen["i"] < seen["n"]:
            _pct("checking...", seen["i"], seen["n"], end=True)   # 停在**拒绝的那一关**
        rec = __import__("json").loads(got.read_text(encoding="utf-8"))
        first = (rec.get("detail") or "").splitlines()
        print(f"update refused: {rec['code']}" + (f" — {first[0]}" if first else ""))
        return 1
    _pct("update....", 0, 1, end=False)
    probe = work / "demotool.elf"
    script = (f"rm -rf /tmp/loment-hotupdate && mkdir -p /tmp/loment-hotupdate && "
              f"cp {_wsl(probe)} /tmp/loment-hotupdate/t && chmod +x /tmp/loment-hotupdate/t && "
              f"cd /tmp/loment-hotupdate && ./t")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True, text=True)
    fields = {}
    for line in r.stdout.splitlines():
        w = line.split()
        for i in range(0, len(w) - 1, 2):
            fields[w[i]] = w[i + 1]
    if r.returncode != 0 or fields.get("RC") != "0":
        print(f"update failed: rc={r.returncode} rc-hot={fields.get('RC', '?')}")
        return 1
    # 换完之后重新跑一次：它应当**不再**看到候选（已经装过），而版本已经是新的。
    _pct("update....", 1, 1, end=True)
    print(f"updated {fields.get('VERSION', '?')} -> {fields.get('AFTER', '?')}"
          f"  (index {fields.get('INDEX', '?')})")
    st.take(got)
    return 0


if __name__ == "__main__":
    sys.exit(main())
