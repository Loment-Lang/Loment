#!/usr/bin/env python3
# loment_staging.py — GitHub 与运行进程之间的那一格（`#1154`）。
#
# 换的那条路**一次网络 I/O 都不做**：`fetch` 在进程外把候选放进
# `~/.loment/hot/<baseline>/incoming/<release>/`，跑着的进程只在事件循环的顶部看一眼目录。
# 被拒的候选进 `rejected/<release>.json`，带着点名理由，永远不被装载。
#
# 目录布局（按基线分开 —— 旧候选不可能在基线动了之后被捡起来）：
#
#   $LOMENT_HOT_HOME/                 (默认 ~/.loment/hot)
#     <baseline>/
#       incoming/<release>/           hotfix.json + 载荷 + SHA256SUMS + SHA256SUMS.asc + 公钥
#       rejected/<release>.json        {"release":…, "code":…, "detail":…}
#       state.json                    已接受过的 release（重放保护，`#1147`）
#       applied/<release>/            已经交给换的那一份（保留清单，`status` 读它）

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_hotfix as hf      # noqa: E402
import loment_update as lu      # noqa: E402

DEFAULT_HOME = Path.home() / ".loment" / "hot"


def home() -> Path:
    return Path(os.environ.get("LOMENT_HOT_HOME") or DEFAULT_HOME)


def baseline_dir(baseline: str, root: Path | None = None) -> Path:
    d = (root or home()) / baseline
    for sub in ("incoming", "rejected", "applied"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _release_key(release: str) -> tuple:
    """`v0.1.5-hot.10` 要排在 `v0.1.5-hot.9` 后面 —— 按数字比，不按字典序。"""
    import re
    m = re.match(r"^v(\d+(?:\.\d+)*)(?:-(.*))?$", release)
    if not m:
        return ((), ())
    nums = tuple(int(x) for x in m.group(1).split("."))
    tail = tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", m.group(2) or ""))
    return (nums, tail)


def _reject(bdir: Path, release: str, code: str, detail: str) -> None:
    (bdir / "rejected" / f"{release}.json").write_text(
        json.dumps({"release": release, "code": code, "detail": detail},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    incoming = bdir / "incoming" / release
    if incoming.exists():
        shutil.rmtree(incoming)


def stage(src: Path, tree: Path, release: str, *, current: str | None = None,
          allow_rollback: bool = False) -> Path:
    """检查一份已下载好的候选，然后（且只在通过时）放进 `incoming/`。

    检查就是 `#1147` 的那一套：签名、五个等式、预发布、版本单调、重放。任何一条不过
    → 进 `rejected/`，运行进程碰都不会碰它。
    """
    src = Path(src)
    base = hf.read_release(tree)
    bdir = baseline_dir(base)
    # 幂等：同一个 tag 再来一次就是**同一份**候选 —— 还在等着的、或已经换过的。
    for sub in ("incoming", "applied"):
        seen = bdir / sub / release
        if (seen / "hotfix.json").is_file():
            return seen
    try:
        lu.check(src, tree, state=bdir / "state.json",
                 current=current, allow_rollback=allow_rollback)
    except lu.Reject as e:
        _reject(bdir, release, e.code, e.detail)
        return bdir / "rejected" / f"{release}.json"

    dest = bdir / "incoming" / release
    # 幂等：同一个 tag 再来一次就**替换**那一份，不是多出一份。
    if dest.exists():
        shutil.rmtree(dest)
    tmp = Path(tempfile.mkdtemp(prefix=f".{release}-", dir=bdir / "incoming"))
    try:
        for p in src.iterdir():
            if p.is_file():
                shutil.copyfile(p, tmp / p.name)
        tmp.rename(dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def poll(baseline: str, root: Path | None = None) -> Path | None:
    """跑着的进程在事件循环顶部看一眼：有没有**属于本基线**的、最新的候选。

    只看 `incoming/` —— 别的基线的目录压根不在这一棵下面。
    """
    bdir = (root or home()) / baseline
    inc = bdir / "incoming"
    if not inc.is_dir():
        return None
    cands = sorted((p for p in inc.iterdir() if p.is_dir() and (p / "hotfix.json").is_file()),
                   key=lambda p: _release_key(p.name))
    return cands[-1] if cands else None


def take(candidate: Path, root: Path | None = None) -> Path:
    """把一份候选从 `incoming/` 挪进 `applied/`（换完，或者换被拒绝之后）。

    **应用顺序要记下来**：`status` 判断"这次是不是回滚"靠的是先后，不是版本号 ——
    按版本号排序的话，回滚永远看不见（实测：hot.2 之后回到 hot.1 会排成 hot.1, hot.2）。
    """
    bdir = Path(candidate).parent.parent
    name = Path(candidate).name
    dest = bdir / "applied" / name
    if dest.exists():
        shutil.rmtree(dest)
    Path(candidate).rename(dest)
    ledger = bdir / "applied.json"
    seq = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() else []
    if not seq or seq[-1] != name:
        seq.append(name)
    ledger.write_text(json.dumps(seq, indent=2) + "\n", encoding="utf-8", newline="\n")
    return dest


def fetch(tag: str, tree: Path, *, downloader: list[str] | None = None,
          src_dir: Path | None = None, current: str | None = None,
          allow_rollback: bool = False) -> Path:
    """把一份 Pre-Release 拿进 `incoming/`。**传输在进程外**。

    - `src_dir` 给了就直接从目录取（用户自己下载的、或判据里预置的）；
    - 否则用主机上的 `gh release download`；
    - 都没有就点名拒绝（工具链自己不开 socket）。
    """
    if src_dir is not None:
        return stage(Path(src_dir), tree, tag, current=current,
                     allow_rollback=allow_rollback)
    tmp = Path(tempfile.mkdtemp(prefix="loment-fetch-"))
    cmd = downloader or ["gh", "release", "download", tag, "--dir", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise lu.Reject("transport", "没有 gh —— 用 --from 给一份已经下好的目录")
    if r.returncode != 0:
        raise lu.Reject("transport", (r.stderr or r.stdout).strip()[:200])
    out = stage(tmp, tree, tag, current=current, allow_rollback=allow_rollback)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def applied(baseline: str, root: Path | None = None) -> list[dict]:
    """已经交给换的那些清单，**按应用顺序**（`loment update status` 读它）。"""
    d = (root or home()) / baseline / "applied"
    if not d.is_dir():
        return []
    ledger = d.parent / "applied.json"
    if ledger.is_file():
        names = json.loads(ledger.read_text(encoding="utf-8"))
    else:
        names = sorted((p.name for p in d.iterdir()), key=_release_key)
    out = []
    for name in names:
        man = d / name / "hotfix.json"
        if man.is_file():
            out.append(json.loads(man.read_text(encoding="utf-8")))
    return out


def status(baseline: str, root: Path | None = None) -> str:
    """`loment update status` 的那段文本（`#1157`）：线、已应用的 fix、要重启的 fix。

    回滚在线上是**往左走**：后一次接受的 release 比前一次小。它被标成 `rollback`，
    不是 `upgrade` —— 自动接受保持单调，往回走只能是显式的操作。
    """
    rows = applied(baseline, root)
    out = [f"line    {baseline}"]
    if not rows:
        out.append("  (nothing applied)")
        return "\n".join(out) + "\n"
    prev = None
    for man in rows:
        rel = man.get("release", "?")
        move = ""
        if prev is not None:
            move = "  (rollback)" if _release_key(rel) < _release_key(prev) else ""
        out.append(f"  {rel}{move}")
        for step in (man.get("projection") or {}).get("line") or []:
            out.append(f"    + {step.get('sha', '')[:12]}  {step.get('subject', '')}")
        prev = rel
    last = rows[-1]
    evicted = (last.get("projection") or {}).get("evicted") or []
    if evicted:
        out.append("restart required")
        for step in evicted:
            out.append(f"    ! {step.get('sha', '')[:12]}  {step.get('subject', '')}"
                       f"  ({step.get('reason', '?')})")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="loment_update_fetch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="stage a Pre-Release for the running build")
    f.add_argument("--tag", required=True)
    f.add_argument("--at", default=".")
    f.add_argument("--from", dest="src", default=None, help="an already-downloaded directory")
    f.add_argument("--current", default=None)
    f.add_argument("--allow-rollback", action="store_true")
    p = sub.add_parser("poll", help="what the running process would pick up now")
    p.add_argument("--baseline", required=True)
    s = sub.add_parser("status", help="the hot line, the fixes applied, the ones to restart for")
    s.add_argument("--baseline", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        sys.stdout.write(status(args.baseline))
        return 0
    if args.cmd == "fetch":
        try:
            out = fetch(args.tag, Path(args.at).resolve(),
                        src_dir=Path(args.src) if args.src else None,
                        current=args.current, allow_rollback=args.allow_rollback)
        except lu.Reject as e:
            print(f"REJECT {e.code}: {e.detail}", file=sys.stderr)
            return 1
        print(f"STAGED {out}")
        return 0
    got = poll(args.baseline)
    print(f"PICK {got}" if got else "PICK none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
