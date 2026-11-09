#!/usr/bin/env python3
# loment_seed.py — 无 Python 自举的**种子** (M83 延伸, docs/159)
#
# 自举链原先只能从"Python 版编译器"起步 (M83 的 stage1 由 lomentc 现编)。本工具把
# 那个起点**固化成一个已提交的工件**: 参考实现为 `selfhost/driver.lomt` 发射的 IR。
# 有了它, 整条链只需要 clang + POSIX sh (`loment/bootstrap.sh`), 不需要 Python:
#
#   clang(种子 .ll) -> stage1 --driver.lomt--> IR(== 种子) -> stage2 -> ... 定点
#
# 用法:
#   python tools/loment_seed.py --emit       写 loment/build/selfhost_driver.ll
#   python tools/loment_seed.py --check      种子与参考实现一致? (不写盘)
#   python tools/loment_seed.py --bootstrap  跑 loment/bootstrap.sh (Windows 走 WSL)
#   python tools/loment_seed.py --script-ok  纯静态: 启动脚本无第三方语言/无 CRLF
# 退出码: 0 = 一致/成功 / 1 = 有差异或失败 / 2 = 环境缺失。

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"
ENTRY = ROOT / "loment" / "selfhost" / "driver.lomt"
SCRIPT = ROOT / "loment" / "bootstrap.sh"
SH_REL = "loment/bootstrap.sh"


#: ---- 渠道注入（`docs/231` §2.3）------------------------------------------
#:
#: 发行版种子 = 把自举侧那一行渠道常量翻成 `1` 之后**重算**的种子。**只改这一行、
#: 只在这一个文件里** —— 于是"包里的树除这一行之外与仓库逐字节相同"仍是一条可判的话。
#:
#: 钩子放在这里、不放进 `lomentc.load()`：那是**编译器**，不该知道"打包"这件事。
#: `lomentc.load` 内部是**调用时才** `from potato_from import front_door`，
#: 所以换掉 `potato_from.front_door` 就够 —— 编译器一个字节都不用碰。
CHANNEL_FILE = "loment/selfhost/checker.lomt"
CHANNEL_LINE_DEV = "pub const LOMENT_CHANNEL: u32 = 0;"
CHANNEL_LINE_RELEASE = "pub const LOMENT_CHANNEL: u32 = 1;"


def _front_door_for(channel: str):
    """按渠道包一层 `front_door`；`dev` 就是原来那个。"""
    import potato_from  # noqa: PLC0415

    orig = potato_from.front_door
    if channel == "dev":
        return orig
    want = (ROOT / CHANNEL_FILE).resolve()

    def patched(path):
        fu = orig(path)
        if Path(path).resolve() == want:
            if CHANNEL_LINE_DEV not in fu.source:
                raise SystemExit(
                    f"[ERR] 渠道注入点漂了：{CHANNEL_FILE} 里找不到 `{CHANNEL_LINE_DEV}` —— "
                    "改了那一行的写法就要同时改这里（`docs/231` §2.3）")
            fu.source = fu.source.replace(CHANNEL_LINE_DEV, CHANNEL_LINE_RELEASE, 1)
        return fu

    return patched


def reference_ir(channel: str = "dev") -> str:
    """参考实现为自举驱动发射的单元 IR —— 种子应与它逐字符相同。

    `channel="release"` 时先做 §2.3 那一步注入，算出来的就是**发行版种子**。
    """
    import potato_from  # noqa: PLC0415

    orig = potato_from.front_door
    potato_from.front_door = _front_door_for(channel)
    try:
        mod = lomentc.load(ENTRY)
        deps = lomentc.resolve_deps(mod, ROOT, ENTRY.parent, entry=ENTRY)
        errs = lomentc.check(mod, deps=deps)
        if errs:
            raise SystemExit(f"[ERR] driver.lomt 自己检查不过: {errs[0]}")
        return lomentc.emit_llvm(mod, ROOT, deps)
    finally:
        potato_from.front_door = orig


def check() -> int:
    want = reference_ir()
    if not SEED.exists():
        print(f"[ERR] 缺种子 {SEED.relative_to(ROOT)} (运行 --emit)")
        return 1
    got = SEED.read_text(encoding="utf-8")
    if got != want:
        bad = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]), None)
        print(f"[DIFF] 种子过期: 盘上 {len(got)}B vs 参考 {len(want)}B @{bad} "
              f"(运行 --emit 重新固化; 自举产物变了就必须跟上)")
        return 1
    print(f"loment_seed: 种子与参考逐字符一致 ({len(want)}B)")
    return 0


def emit() -> int:
    want = reference_ir()
    SEED.parent.mkdir(parents=True, exist_ok=True)
    with SEED.open("w", encoding="utf-8", newline="\n") as f:
        f.write(want)
    print(f"[OK] {SEED.relative_to(ROOT)} ({len(want)}B)")
    return 0


#: 启动脚本: **只允许 clang + 宿主 shell**, 不许出现任何解释器。这两个文件是"没有 Python
#: 也能构建/使用 Loment"的全部入口, 所以它们自己不能偷偷调解释器。
LAUNCH_SCRIPTS = ("loment/bootstrap.sh", "scripts/lomc.ps1")

#: 命令位置不许出现的解释器。
FORBIDDEN = ("python", "python3", "perl", "ruby", "node", "cargo", "rustc", "gcc")


def script_ok() -> int:
    """静态判据: 启动脚本只用 clang (无第三方语言调用), 且是 LF 换行。

    注释里的提法不算 (`# python ...` 只是说明); 只看**命令位置** —— 行首或 `| & ; (` 之后。
    """
    bad: list[str] = []
    for rel in LAUNCH_SCRIPTS:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"缺 {rel}")
            continue
        raw = p.read_bytes()
        if b"\r\n" in raw:
            bad.append(f"{rel} 含 CRLF (WSL/Linux 的 sh 会把 \\r 当命令字符; "
                       f"见 .gitattributes 的 *.sh/*.ps1 eol=lf)")
        text = raw.decode("utf-8", errors="replace")
        for ln, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            for name in FORBIDDEN:
                if re.search(rf"(?:^|[|&;(])\s*{re.escape(name)}\b", code):
                    bad.append(f"{rel}:{ln} 调用了 {name}: {line.strip()[:70]}")
    if bad:
        for b in bad:
            print(f"[FAIL] {b}")
        return 1
    print(f"loment_seed: 启动脚本 {len(LAUNCH_SCRIPTS)} 个只用 clang "
          f"(无第三方语言调用, LF 换行)")
    return 0


def bootstrap() -> int:
    """跑无 Python 自举 (本机不能执行 Linux ELF 时走 WSL)。

    参数表调用, 不拼 shell 字符串: WSL 把进程的当前目录翻成 /mnt/... 传进去,
    脚本自己再 `cd` 到仓库根 (它按 $0 定位), 所以不依赖调用方的工作目录。
    """
    if not SCRIPT.exists():
        print(f"[ERR] 缺 {SCRIPT.relative_to(ROOT)}")
        return 2
    if shutil.which("wsl"):
        cmd = [*_WSLCMD, "sh", SH_REL]
    elif shutil.which("sh"):
        cmd = ["sh", SH_REL]
    else:
        print("[ERR] 既无 wsl 也无 sh")
        return 2
    # 900 -> 1800（2026-11-09）：种子到 5.63 MB 之后，三阶段定点在 CI 的
    # ubuntu runner 上撞了 15 分钟。这里有**两个**慢步骤（stage1 与 stage2 各编一遍
    # driver.lomt），而它们是定点判据本身，不能抽掉。1800 是**存活护栏**，不是性能
    # 指标：真挂死仍然会在半小时内报出来。
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                       shell=False, cwd=str(ROOT))
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr[-800:])
        print(f"[FAIL] 无 Python 自举失败 (退出 {r.returncode})")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_seed", description="无 Python 自举的种子")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    ap.add_argument("--script-ok", action="store_true")
    a = ap.parse_args(argv)
    if a.emit:
        return emit()
    if a.check:
        return check()
    if a.bootstrap:
        return bootstrap()
    if a.script_ok:
        return script_ok()
    print("[ERR] 需要 --emit / --check / --bootstrap / --script-ok", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
