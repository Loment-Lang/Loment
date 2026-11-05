#!/usr/bin/env python3
"""lomentdev — 给**改 Loment 本身**的人的开发套件（docs/302）。

它不新增能力，只把三件散在各处、每条新人都会踩一次的事收成一条命令：

  doctor     这棵检出能不能跑门禁；缺什么、去哪儿补
  gate       `python3 tools/ci.py --static-only` 的包装（默认并行、UTF-8、报最慢的几条）
  bootstrap  `sh loment/bootstrap.sh`（12–14 分钟，Windows 上走 WSL）

## 为什么不是 `loment doctor`

`loment doctor` 诊断的是**装好的工具链**（哪个组件缺失、东西在哪）。这里诊断的是
**一棵检出**：Python 版本对不对、`PYTHONUTF8` 设了没有、clang/WSL 在不在、门禁的
基线文件在不在。两个受众不同 —— 前者是语言用户，后者是贡献者。

## 为什么环境是"镜像"而不是"安装步骤"

门禁跑在 `ghcr.io/<owner>/loment-ci:<tag>` 里（`.github/ci/Dockerfile`），
`.devcontainer/devcontainer.json` 直接用**同一个 tag** —— 于是"我这里过不了"与
"CI 上过不了"之间少掉一整类环境差异。判据
`loment_dev_test::test_the_devcontainer_uses_the_same_image_tag_as_the_gate`
钉的就是这条：抬 tag 时漏改任何一处都会红。

用法::

    python3 tools/loment_dev.py doctor
    python3 tools/loment_dev.py gate [-j N] [--only SUBSTR] [--dry-run]
    python3 tools/loment_dev.py bootstrap [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 门禁的入口。`tools/ci.py --static-only` 是 `.github/workflows/gate.yml` 里那一条。
GATE = ("tools/ci.py", "--static-only")

#: 自举脚本与实际耗时（实测 12–14 分钟，见 docs/302 §3）。给超时留一倍余量。
BOOTSTRAP = "loment/bootstrap.sh"
BOOTSTRAP_SECONDS = 14 * 60
BOOTSTRAP_TIMEOUT = 30 * 60

#: 检出的根必须有的东西 —— 少一件就不是一棵能跑门禁的树。
REQUIRED_PATHS = (
    "tools/ci.py",
    "tools/loment_release.py",
    "loment/bootstrap.sh",
    ".github/gate-baseline.txt",
    ".github/gate-floors.txt",
)

#: 门禁本身只要 Python + git；下面这些是**部分判据**的依赖，缺了那些判据会 SKIP。
#: 与 `.github/ci/Dockerfile` 的包名单同源 —— 镜像里全有，本机不一定。
OPTIONAL_TOOLS = (
    ("clang", "自举与 genesis"),
    ("rustc", "loment_tools_test 的 rustc 那两条"),
    ("cargo", "同上"),
    ("go", "Go 前门判据"),
    ("javac", "Java 前门判据"),
    ("node", "VS Code 扩展判据"),
    ("gh", "loment_publish"),
    ("gdb", "diagnose_twin 探针"),
    ("wsl", "Windows 上跑自举与 ELF"),
)

#: CI 用的 Python（ubuntu 24.04 自带 3.12）。版本不同不会直接红，但**只在 CI 现形**
#: 的差异（拿 CPython 当对照物的判据）就是从这儿来的。
CI_PYTHON = (3, 12)


@dataclass
class Check:
    """一条体检结果。`required` 是"缺了就进不了门"，其余是"缺了少一块覆盖面"。"""

    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def _have(which, name: str) -> str | None:
    try:
        return which(name)
    except Exception:  # noqa: BLE001  探针坏了按"没有"算，doctor 不该自己崩
        return None


def doctor_checks(*, which=shutil.which, env=None, pyver=None,
                  platform=None, root=ROOT) -> list[Check]:
    """体检。**纯函数**（探针都可注入）—— 判据靠这一点在假环境里跑，不碰真机器。"""
    env = os.environ if env is None else env
    pyver = sys.version_info if pyver is None else pyver
    platform = sys.platform if platform is None else platform
    root = Path(root)
    out: list[Check] = []

    ver = "%d.%d.%d" % (pyver[0], pyver[1], pyver[2] if len(pyver) > 2 else 0)
    want = "%d.%d" % CI_PYTHON
    out.append(Check(
        "python", tuple(pyver[:2]) >= (3, 10),
        ver if tuple(pyver[:2]) == CI_PYTHON else f"{ver}（CI 是 {want}）",
        "" if tuple(pyver[:2]) == CI_PYTHON else f"装 Python {want}：与 CI 同版本，才不会只在 CI 现形",
    ))

    if platform == "win32":
        utf8 = env.get("PYTHONUTF8") == "1"
        out.append(Check(
            "PYTHONUTF8", utf8,
            "1" if utf8 else "没设",
            "set PYTHONUTF8=1 —— 否则子进程输出按 GBK 解码，判据会假红（docs/302 §2）",
        ))
    else:
        out.append(Check("PYTHONUTF8", True, "不适用（POSIX / 容器默认 UTF-8）"))

    git = _have(which, "git")
    out.append(Check("git", bool(git), git or "找不到", "装 git：判据要读索引与 .gitattributes"))

    missing = [p for p in REQUIRED_PATHS if not (root / p).exists()]
    out.append(Check(
        "checkout", not missing,
        "树是完整的" if not missing else "缺 " + "、".join(missing),
        "在仓库根跑：`python3 tools/loment_dev.py` 必须在检出根部",
    ))

    for name, why in OPTIONAL_TOOLS:
        if name == "wsl" and platform != "win32":
            continue
        path = _have(which, name)
        out.append(Check(name, bool(path), path or f"找不到（{why}）",
                         f"装 {name}：{why}；或直接用 .devcontainer（镜像里都有）",
                         required=False))

    in_container = env.get("LOMENT_DEV_CONTAINER") == "1" or Path("/.dockerenv").exists()
    out.append(Check(
        "environment", True,
        "在 CI 镜像里" if in_container else "本机环境（与 CI 可能不同）",
        "" if in_container else "要复现 CI：用 .devcontainer（镜像与门禁同一个 tag）",
        required=False,
    ))
    return out


def gate_argv(*, py="python3", jobs=None, only=()) -> list[str]:
    """门禁的命令行。**纯函数** —— 文档里写的就是它，别在两处各写一份。"""
    argv = [py, *GATE]
    if jobs:
        argv += ["-j", str(jobs)]
    for o in only:
        argv += ["--only-static", o]
    return argv


def bootstrap_argv(*, platform=None, root=ROOT) -> list[str]:
    """自举需要 `sh`（Linux/WSL）。Windows 上必须是 `wsl -e bash -lc`，不是 sh.exe。"""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        posix = str(root).replace("\\", "/")
        drive = posix[0].lower() + posix[1:]
        return ["wsl", "-e", "bash", "-lc", f"cd {drive} && sh {BOOTSTRAP}"]
    return ["sh", BOOTSTRAP]


def _default_jobs() -> int:
    return max(1, min(8, os.cpu_count() or 4))


def _fmt(cmd: list[str]) -> str:
    """给人看的命令行。**带空格的实参要加引号** —— `bash -lc "cd x && sh y"` 是**一个**
    实参, 平铺打印会读成四个, 于是"照着抄一遍"就错。"""
    return " ".join(shlex.quote(x) for x in cmd)


def _print(checks: list[Check]) -> None:
    for c in checks:
        mark = "ok  " if c.ok else ("MISS" if c.required else "warn")
        print(f"  {mark}  {c.name:12s} {c.detail}")
        if not c.ok and c.fix:
            print(f"          fix: {c.fix}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomentdev", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="体检这棵检出")

    g = sub.add_parser("gate", help="跑静态门禁")
    g.add_argument("-j", "--jobs", type=int, default=None)
    g.add_argument("--only", action="append", default=[], metavar="SUBSTR")
    g.add_argument("--dry-run", action="store_true", help="只打印命令")

    b = sub.add_parser("bootstrap", help="跑自举（12–14 分钟）")
    b.add_argument("--dry-run", action="store_true", help="只打印命令")

    a = ap.parse_args(argv)

    if a.cmd == "doctor":
        checks = doctor_checks(root=ROOT)
        print(f"lomentdev doctor — {ROOT}")
        _print(checks)
        bad = [c for c in checks if c.required and not c.ok]
        print(f"\n{len(checks) - len([c for c in checks if not c.ok])}/{len(checks)} 项正常")
        return 1 if bad else 0

    if a.cmd == "gate":
        jobs = a.jobs or _default_jobs()
        cmd = gate_argv(py=sys.executable, jobs=jobs, only=tuple(a.only))
        print("lomentdev gate — " + _fmt(cmd))
        if a.dry_run:
            return 0
        # 子进程的输出按宿主 locale 解码; Windows 上是 GBK, 于是判据里的非 ASCII 会炸成
        # "NoneType is not iterable" 那种与真因无关的错。
        env = dict(os.environ, PYTHONUTF8="1")
        t0 = time.perf_counter()
        rc = subprocess.call(cmd, cwd=str(ROOT), env=env)
        print(f"\nlomentdev gate — {rc and 'FAIL' or 'PASS'}  {time.perf_counter() - t0:.1f}s")
        return rc

    if a.cmd == "bootstrap":
        cmd = bootstrap_argv(root=ROOT)
        print("lomentdev bootstrap — " + _fmt(cmd))
        print(f"  预期 {BOOTSTRAP_SECONDS // 60} 分钟左右；卡住不等于死（docs/302 §3）")
        if a.dry_run:
            return 0
        t0 = time.perf_counter()
        try:
            rc = subprocess.call(cmd, cwd=str(ROOT), timeout=BOOTSTRAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"超时：{BOOTSTRAP_TIMEOUT // 60} 分钟仍未结束", file=sys.stderr)
            return 1
        print(f"\nlomentdev bootstrap — {rc and 'FAIL' or 'PASS'}  {time.perf_counter() - t0:.1f}s")
        return rc

    return 2


if __name__ == "__main__":
    sys.exit(main())
