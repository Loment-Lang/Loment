#!/usr/bin/env python3
# loment_hotbuild_test.py — `loment build --profile hotswap` 的启动器管线（`#1151`）。
#
# 不建整包：把 `loment_dist.LAUNCHER_SH` 原样取出来，配一对**假的** driver / lomelf，
# 真跑启动器，看它把什么交给链接器。这样"转发"与"拒绝"都是执行出来的，不是文本断言。
# Windows 上在 WSL 的原生 /tmp 里跑（/mnt/d 的可执行位不可靠）。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_dist  # noqa: E402

TESTS: list = []

DRIVER = ("#!/bin/sh\n"
          "if [ \"$2\" = \"--print-command\" ]; then exit 0; fi\n"
          "printf 'define i32 @_start() {\\nentry:\\n  ret i32 0\\n}\\n'\n")
LINKER = '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n: > "$2"\n'


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _prepare(td: Path) -> None:
    (td / "loment").write_text(loment_dist._subst(loment_dist.LAUNCHER_SH, "release"),
                               encoding="utf-8", newline="\n")
    (td / "loment-driver").write_text(DRIVER, encoding="utf-8", newline="\n")
    (td / "loment-lomelf").write_text(LINKER, encoding="utf-8", newline="\n")
    (td / "x.lomt").write_text("module x\nfn _start() -> u32 { return 0; }\n",
                               encoding="utf-8", newline="\n")
    for n in ("loment", "loment-driver", "loment-lomelf"):
        os.chmod(td / n, 0o755)


def _run(args: list[str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        _prepare(td)
        q = " ".join(args)
        if sys.platform.startswith("linux"):
            r = subprocess.run(f"bash ./loment {q} 2>&1; echo rc=$?; cat args.txt 2>/dev/null",
                               shell=True, cwd=td, capture_output=True, text=True)
            out = r.stdout
        else:
            if not shutil.which("wsl"):
                return -1, ""
            probe = subprocess.run(["wsl", "-e", "true"], capture_output=True)
            if probe.returncode != 0:
                return -1, ""

            def w(p: Path) -> str:
                s = str(p.resolve()).replace("\\", "/")
                return "/mnt/" + s[0].lower() + s[2:]

            script = (f"rm -rf /tmp/loment-hotbuild && mkdir -p /tmp/loment-hotbuild && "
                      f"cp -r {w(td)}/. /tmp/loment-hotbuild/ && "
                      f"cd /tmp/loment-hotbuild && chmod +x loment loment-driver loment-lomelf && "
                      f"bash ./loment {q} 2>&1; echo rc=$?; cat args.txt 2>/dev/null")
            r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                               capture_output=True, text=True)
            out = r.stdout
    rc = -1
    for line in out.splitlines():
        if line.startswith("rc="):
            rc = int(line[3:])
    return rc, out


@test
def test_bash_launcher_forwards_the_profile() -> None:
    rc, out = _run(["build", "x.lomt", "--profile", "hotswap"])
    if rc == -1:
        print("SKIP: 没有 bash/WSL")
        return
    assert rc == 0, out[-300:]
    lines = [ln for ln in out.splitlines() if ln]
    assert "--profile" in lines and "hotswap" in lines, out[-300:]
    print("      启动器把 --profile hotswap 原样交给了链接器")


@test
def test_bash_launcher_refuses_an_unknown_profile() -> None:
    rc, out = _run(["build", "x.lomt", "--profile", "nope"])
    if rc == -1:
        print("SKIP: 没有 bash/WSL")
        return
    assert rc == 2, out[-300:]
    assert "unknown --profile" in out, out[-300:]
    print("      不认识的 profile —— 退出 2 并点名")


@test
def test_bash_launcher_refuses_opt_with_profile() -> None:
    rc, out = _run(["build", "x.lomt", "--opt", "--profile", "hotswap"])
    if rc == -1:
        print("SKIP: 没有 bash/WSL")
        return
    assert rc == 2, out[-300:]
    assert "cannot be combined with --opt" in out, out[-300:]
    print("      --opt 与 --profile 同时给 —— 退出 2 并点名（clang 不发槽表）")


@test
def test_batch_and_help_mention_the_profile() -> None:
    assert "--profile" in loment_dist.LAUNCHER_CMD, "Windows 启动器没有 --profile"
    assert "%profarg%" in loment_dist.LAUNCHER_CMD, "Windows 启动器没有把它交给链接器"
    help_text = (ROOT / "loment" / "tools" / "lomcli.lomt").read_text(encoding="utf-8")
    assert "--profile hotswap" in help_text, "loment help 里没有 --profile hotswap"
    print("      Windows 启动器与 loment help 都点了名")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotbuild_test")


if __name__ == "__main__":
    sys.exit(main())
