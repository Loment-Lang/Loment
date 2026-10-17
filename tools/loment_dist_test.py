#!/usr/bin/env python3
# loment_dist_test.py — 发行包判据 (docs/162)
#
# 判据 (每条都是"打出来的包真能用", 不是"文件在"):
#   1. payload 布局与脚本卫生: 该有的都在; .sh/.ps1/.cmd 纯 ASCII; .ps1/.cmd 是 CRLF
#      (PS 5.1 用 ANSI 读无 BOM 的非 ASCII 会把后续行解析坏 —— docs/157 §3.4)
#   2. 归档内容 == payload (逐个 sha256 对得上, 不是"大概在")
#   3. 归档**确定性**: 同样输入两次写出字节相同 (zip 固定时间戳 + tar.gz mtime=0)
#      + **宿主无关**: 换掉 `sys.platform` 再压一遍, zip 字节必须一模一样
#      (容器里 `create_system` 那一个字段; 见 test_archives 里那条注解)。
#   4. `--check` 对产物与 SHA256SUMS 一致
#   5. install.sh 端到端 (WSL 里): tar → 装进临时前缀 → `loment version` →
#      `loment ir` 的产物与参考实现**逐字节相同** → `loment run` 打出东西 →
#      --uninstall 摘掉 → 再装一次仍成功 (幂等)
#   6. Windows 安装脚本能在 PowerShell 5.1 下解析并 -DryRun 跑通 (本机实测)
#
# 退出码: 0 = 全过 / 1 = 有红。

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_dist  # noqa: E402
import loment_seed  # noqa: E402
import lomentc  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = loment_dist.ROOT


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


OUT = loment_dist.OUT
IT_OUT = loment_dist.STAGE / "it-out"   # 测试专用产物目录 (不碰 loment/dist)
VER = loment_dist.VER
PREFIX_IT = f"{_T}loment_dist_it"          # WSL 侧的安装前缀
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def wsl(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """在**这套命令自己的平台**上跑它们。

    `rm` / `sh` / `test` / 装出来的 `loment` 本来就是 Linux 的东西：Windows 上得借 WSL，
    而**本机就是 Linux 时借 WSL 是假借** —— 那些命令本来就在手边，CI 上更是直接
    `FileNotFoundError: 'wsl'`（2026-09-22 把门禁搬上 Linux runner 时撞到，露出来的是
    `install.sh 端到端` 那条）。所以这里按平台分流，**不是跳过** —— 换到 Linux 之后这些
    判据反而跑的是原生那条路（在 Linux 上验 `install.sh` 正是该做的事）。
    """
    argv = list(args) if sys.platform != "win32" else [*_WSLCMD, *args]
    return subprocess.run(argv, capture_output=True, text=True,
                          shell=False, encoding="utf-8", errors="replace", timeout=timeout)


def wsl_path(p: Path) -> str:
    """给上面那些命令用的路径。本机就是 Linux 时**原样返回** —— 没有盘符要翻。"""
    if sys.platform != "win32":
        return str(p.resolve())
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


#: `--opt` 的**试金石**：一段有"按字节铺"与"按字节搬"两种循环的程序。
#:
#: 为什么需要它：`-O2` 会把这两种循环改写成 `memset` / `memcpy` / `memmove` 的调用，而
#: Loment 是无 libc 的 —— 少补一个符号，clang 就在**链接期**报 `undefined symbol: memset`
#: （实测）。`user_hello` 只有一次 `write`，没有这种循环，所以光靠它那一条 `--opt` 判据
#: 是看不见这一格的。这段程序**故意**两样都有：铺（`store8(p, i, 常量)`）与搬
#: （`store8(d, i, load8(s, i))`），且长度是运行期参数，优化器不会把它折没了。
#: `_start` 读到最后一个字节再退出，于是"跑起来"这件事本身就是"搬对了"。
OPT_LIBCALL_PROBE = """\
module optlibcall_probe
choose runtime

fn fill(p: ptr, n: u32, v: u8) {
    let i: u32 = 0;
    while i < n {
        store8(p, i, v);
        i = i + 1;
    }
}

fn copy(d: ptr, s: ptr, n: u32) {
    let i: u32 = 0;
    while i < n {
        store8(d, i, load8(s, i) as u8);
        i = i + 1;
    }
}

fn _start() {
    let p: ptr = alloc(4096);
    let q: ptr = alloc(4096);
    fill(p, 2048, 7);
    copy(q, p, 2048);
    exit(load8(q, 2047) as u64);
}

fn exit(code: u64) -> i64 {
    return syscall4(60, code, 0, 0);
}
"""


# ------------------------------------------------------------------ 1. 布局

def test_layout() -> None:
    lin = loment_dist.payload("linux", {"loment-driver": (b"\x7fELF-fake", b"MZ-fake")})
    win = loment_dist.payload("windows", {"loment-driver": (b"\x7fELF-fake", b"MZ-fake")})
    want_common = {"bin/loment", "share/loment/version", "share/loment/seed.ll",
                   "share/loment/examples/user_hello.lomt", "README.md", "LICENSE"}
    check("payload 公共布局齐全", want_common <= set(lin))
    check("linux 有 install.sh 且可执行",
          lin.get("install.sh", (b"", 0))[1] == 0o755)
    check("windows 有 install.ps1/install.cmd/welcome.ps1/logo/loment.ico",
          {"install.ps1", "install.cmd", "welcome.ps1", "loment-logo.png",
           "bin/loment.ico"} <= set(win))
    check("windows zip 里没有 install.sh (不混淆两种装法)",
          "install.sh" not in win and "install.ps1" not in lin)
    check("loment-logo.png 真的是 PNG (从 editors/loment.ico 最大的那帧抽的)",
          win["loment-logo.png"][0][:8] == b"\x89PNG\r\n\x1a\n")
    check("ELF 权限位是 755", all(m == 0o755 for (rel, (b, m)) in lin.items()
                                  if rel.startswith("bin/loment-")))

    # 脚本卫生: ASCII + 行尾
    bad_ascii = [rel for rel in ("bin/loment", "install.sh")
                 if any(c > 0x7E for c in lin[rel][0])]
    check("linux 脚本纯 ASCII", not bad_ascii, ",".join(bad_ascii))
    ps1 = win["install.ps1"][0]
    check("install.ps1 纯 ASCII", all(c <= 0x7E for c in ps1))
    # welcome.ps1 是同一类东西（PS 5.1 按 ANSI 读无 BOM 的 .ps1），卫生规则也同一套。
    for rel in ("install.ps1", "install.cmd", "welcome.ps1"):
        blob = win[rel][0]
        check(f"{rel} 纯 ASCII", all(c <= 0x7E for c in blob))
        check(f"{rel} 是 CRLF", b"\r\n" in blob and b"\n" not in blob.replace(b"\r\n", b""))
    check("bin/loment 是 LF (WSL/Linux 侧)",
          b"\r" not in lin["bin/loment"][0])

    # 安装器的两张脸。**无参数（= 双击）走窗口，带参数走控制台** —— 而 2026-10-12 用户报的
    # 「双击 setup.exe 毫无反应」，实测下来是包解开了、脚本真跑了、东西也真装上了，只是
    # 那条路上**没有一个看得见的窗口**。这一条钉**静态形状**（跨平台跑得到）：下面那条
    # 真开窗口的判据只在 Windows 上做，而门禁只有 ubuntu runner。
    cmd = win["install.cmd"][0].decode("ascii")
    check("install.cmd 两张脸: 无参数 -> 窗口(welcome.ps1), 有参数 -> 控制台",
          "welcome.ps1" in cmd and ":console" in cmd and "goto console" in cmd
          and cmd.index("welcome.ps1") < cmd.index(":console"),
          "两条分支都要在, 且窗口那条排在前")
    wps = win["welcome.ps1"][0].decode("ascii")
    # 断言抓的是**重写的特征**（校验原语 / 复制载荷），不是某个词的出现 —— 第一版抓
    # `SHA256SUMS` 这个词，结果被窗口自己的一句注释绊倒（2026-10-12 实测），那是假红。
    check("welcome.ps1 只驱动 install.ps1, 不重写安装逻辑 (一份逻辑两张脸)",
          "install.ps1" in wps and "Get-FileHash" not in wps and "Copy-Item" not in wps,
          "窗口不该自己校验/复制载荷 —— 那是 install.ps1 的活")
    # Windows 包里工具都带 .exe, 而 `test -x name` 只在 MSYS 下自动补后缀 —— WSL 不会。
    # 所以启动器必须经 tool() 解析路径; 裸 `$here/loment-` 一旦回归就是「假报缺组件」。
    launcher = lin["bin/loment"][0].decode("ascii")
    check("sh 启动器经 tool() 解析工具路径 (Windows 包的工具带 .exe)",
          "$here/loment-" not in launcher and "tool loment-driver" in launcher,
          "仍有裸路径" if "$here/loment-" in launcher else "")
    ver = lin["share/loment/version"][0].decode()
    check("version 文件带显示名与标识符",
          loment_dist.DISPLAY in ver and VER in ver, ver.splitlines()[0] if ver else "")

    # issue #32: loment.cmd 的 usage 块每行都必须带 echo
    cmd_body = loment_dist._subst(loment_dist.LAUNCHER_CMD)
    usage_part = cmd_body.split(":usage", 1)[1].split("exit /b", 1)[0]
    usage_lines = [ln.strip() for ln in usage_part.splitlines() if ln.strip()]
    check("loment.cmd usage 块所有非空行均以 echo 开头",
          all(ln.startswith("echo") for ln in usage_lines),
          str([ln for ln in usage_lines if not ln.startswith("echo")]))


# ------------------------------------------------------------------ 2/3. 归档

def _safe_extract(arc, dest: Path) -> None:
    """只往 dest 之下解: 拒绝绝对路径与 `..`。

    归档是**我们自己打的**, 但解包代码不该假设这一点 —— 这条校验让"换成别人的包"也安全。
    """
    base = dest.resolve()
    names = arc.namelist() if isinstance(arc, zipfile.ZipFile) else arc.getnames()
    for name in names:
        if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
            raise ValueError(f"unsafe archive member: {name}")
        if not str((base / name).resolve()).startswith(str(base)):
            raise ValueError(f"archive member escapes the target dir: {name}")
    arc.extractall(dest)


def read_zip(p: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(p) as zf:
        return {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}


def read_tar(p: Path) -> dict[str, bytes]:
    with tarfile.open(p) as tf:
        return {m.name.split("/", 1)[1]: tf.extractfile(m).read()
                for m in tf.getmembers() if m.isfile()}


def test_archives() -> None:
    lin = loment_dist.payload("linux", {"loment-driver": (b"ELF-A", b"PE-A")})
    win = loment_dist.payload("windows", {"loment-driver": (b"ELF-A", b"PE-A")})
    z = read_zip_bytes(loment_dist._zip("loment-x", win))
    t = read_tar_bytes(loment_dist._tar_gz("loment-x", lin))
    check("zip 内容与 payload 逐文件相同",
          z == {k: v[0] for k, v in win.items()})
    check("tar.gz 内容与 payload 逐文件相同",
          t == {k: v[0] for k, v in lin.items()})
    # 确定性: 同一输入两次 → 相同字节 (与仓库其余"确定性工件"同一纪律)
    check("zip 两次写出字节相同",
          loment_dist._zip("loment-x", win) == loment_dist._zip("loment-x", win))
    check("tar.gz 两次写出字节相同",
          loment_dist._tar_gz("loment-x", lin) == loment_dist._tar_gz("loment-x", lin))

    # 3b. **宿主无关**: zip 容器里 `create_system` 那一个字段由 `zipfile` 按
    # `sys.platform` 填 (win32 → 0, 其余 → 3)。内容一个字节不差, 但"同一 tag 在两个
    # runner 上出两个哈希"。2026-10-10 咬到过一次: `skill_zip()` 自己建的那份 ZipFile
    # 漏了 `create_system = 3` (`_zip` 一直有), 而发布流水线正好要从 ubuntu 挪到
    # windows —— 第五件 setup.exe 要 Windows 自带的 iexpress。
    #
    # **为什么不能只断言"等于 3"**: 默认值本来就是"除 win32 外都是 3", 于是在 ubuntu
    # 门禁上两种写法都过 —— 那条断言是空的。真正的性质是**不随 `sys.platform` 变**,
    # 所以这里在同一个进程里把它换成另一个值再压一遍、比字节。
    # **两个平台都判得动**: 在 Windows 上它盯 `skill_zip` 那一半, 在 ubuntu 上盯 `_zip`
    # 那一半 —— 而"只在 Windows 上现形"正是这条缺陷当初能溜过去的理由。
    here = {"_zip": loment_dist._zip("loment-x", win),
            "skill_zip": loment_dist.skill_zip()}
    real_plat = sys.platform
    try:
        sys.platform = "linux" if real_plat == "win32" else "win32"
        other = {"_zip": loment_dist._zip("loment-x", win),
                 "skill_zip": loment_dist.skill_zip()}
    finally:
        sys.platform = real_plat
    drifted = [k for k in here if here[k] != other[k]]
    check("zip 字节不随 sys.platform 变 (容器里 create_system 那一个字段)",
          not drifted, ",".join(drifted))


def read_zip_bytes(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        return {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}


def read_tar_bytes(blob: bytes) -> dict[str, bytes]:
    import io
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        return {m.name.split("/", 1)[1]: tf.extractfile(m).read()
                for m in tf.getmembers() if m.isfile()}


# ------------------------------------------------------------------ 4. 构建 + check

def build() -> tuple[Path, Path]:
    # 要整包（不是 --only driver）：`loment build/run` 靠包内的 loment-lomelf 链接
    rc = loment_dist.main(["--emit", "--no-exe", "--out", str(IT_OUT)])
    assert rc == 0, f"loment_dist --emit rc={rc}"
    tar = IT_OUT / f"loment-{VER}-linux-x64.tar.gz"
    zipf = IT_OUT / f"loment-{VER}-windows-x64.zip"
    check("产物存在 (tar.gz + zip)", tar.exists() and zipf.exists())
    check("--check 与 SHA256SUMS 一致",
          loment_dist.main(["--check", "--out", str(IT_OUT)]) == 0)
    # 归档里的 driver == **本包实际用的那个种子**现链出来的（中间没被动过）。
    #
    # ⚠ **包是发行版**（`docs/231` §2.3）：`emit()` 会把 `ACTIVE_SEED` 换成
    # **发行版种子**（只改 `loment/selfhost/checker.lomt` 那一行渠道常量，再重算）。
    # 这里原先写死的是 `loment_dist.SEED` —— **仓库里那份 dev 种子** —— 所以渠道闸一进来
    # 就当场红（2026-10-12 那次 CI 里 50 条判据中唯一的新红）。
    # 这不是"放宽判据"：比的是**同一个东西**（归档里的 driver 与种子链产物逐字节相同），
    # 只是那份种子现在**按渠道**取；下面那条断言把"有没有走发行版"单独钉住。
    assert loment_dist.ACTIVE_SEED == loment_dist.RELEASE_SEED, (
        "打包没有走发行版种子 —— 包里会是 dev 工具链，于是发行版照样收 `choose vm`"
        "（`docs/231` §2.3）")
    driver = loment_dist._lomelf_link(
        loment_dist.ACTIVE_SEED.read_text(encoding="utf-8"), "elf")
    check("归档里的 loment-driver == 种子链出来的",
          read_tar(tar)["bin/loment-driver"] == driver)
    check("归档里有 loment-lomelf（`loment build/run` 的链接器）",
          "bin/loment-lomelf" in read_tar(tar))
    # 报错器也要在包里: 启动器在 check/build/run 失败时起它 (docs/182 §6)。它缺席**不报错**
    # (退回编译器的裸诊断并说明), 所以少了它不会有任何东西红 —— 这条就是补那个缺口的。
    check("归档里有 lomenterr（报错器）", "bin/lomenterr" in read_tar(tar))
    return tar, zipf


# ------------------------------------------------------------------ 5. install.sh 端到端

def test_install_sh(tar: Path) -> None:
    work = loment_dist.STAGE / "it"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with tarfile.open(tar) as tf:
        _safe_extract(tf, work)
    root = next(p for p in work.iterdir() if p.is_dir())
    install = root / "install.sh"

    wsl("rm", "-rf", PREFIX_IT)
    r = wsl("sh", wsl_path(install), "--prefix", PREFIX_IT, "--no-path")
    check("install.sh 装进临时前缀 (含随包校验和)", r.returncode == 0,
          (r.stderr or r.stdout)[-200:])

    r = wsl(f"{PREFIX_IT}/bin/loment", "version")
    check("装出来的 loment version 打出版本行",
          r.returncode == 0 and loment_dist.DISPLAY in r.stdout, r.stdout[:120])

    # 装的编译器产出的 IR == 参考实现 (这才是"能用"的判据)
    mod = lomentc.load(ROOT / loment_dist.EXAMPLE)
    deps = lomentc.resolve_deps(mod, ROOT, (ROOT / loment_dist.EXAMPLE).parent,
                                entry=ROOT / loment_dist.EXAMPLE)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = wsl(f"{PREFIX_IT}/bin/loment", "ir",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("包的编译器产物与参考实现逐字节相同",
          r.returncode == 0 and r.stdout == want,
          f"rc={r.returncode} len={len(r.stdout)}/{len(want)}")

    # check 子命令: 正例退出 0 且不吐 IR
    r = wsl(f"{PREFIX_IT}/bin/loment", "check",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("loment check 正例退出 0 且不打印 IR",
          r.returncode == 0 and r.stdout.strip() == "", r.stdout[:80])

    # run: 整链都在包里（驱动 + 自举链接器），不再需要外部 clang
    r = wsl(f"{PREFIX_IT}/bin/loment", "run",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("loment run 编译+链接+运行并打出东西",
          r.returncode == 0 and r.stdout.strip() != "", (r.stderr or "")[-200:])

    # `--opt` (docs/212 §5 A): hand the IR to clang -O2 when clang is on PATH, else **say so**
    # and fall back to the self-hosted lomelf linker. Either way the binary must run; the
    # two branches are pinned separately so a silent no-op `--opt` cannot pass.
    ex = f"{PREFIX_IT}/share/loment/examples/user_hello.lomt"
    d_bin, o_bin = "/tmp/loment_def_bin", "/tmp/loment_opt_bin"
    wsl(f"{PREFIX_IT}/bin/loment", "build", ex, "-o", d_bin)
    r = wsl(f"{PREFIX_IT}/bin/loment", "build", ex, "--opt", "-o", o_bin)
    check("loment build --opt 产出可运行二进制",
          r.returncode == 0, (r.stderr or "")[-200:])
    # `build` does not set the exec bit (only `run` chmods -- pre-existing, both backends),
    # so set it before running.
    wsl("chmod", "755", o_bin)
    rr = wsl(o_bin)
    check("build --opt 的产物跑得起来",
          rr.returncode == 0 and rr.stdout.strip() != "", (rr.stderr or "")[-160:])
    has_clang = wsl("sh", "-c", "command -v clang >/dev/null && echo y || echo n").stdout.strip() == "y"
    if has_clang:
        check("有 clang 时 --opt 走优化路径（不说 fallback）",
              "falling back" not in (r.stderr or ""), (r.stderr or "")[-160:])
        sz = lambda p: int(wsl("sh", "-c", f"wc -c < {p}").stdout.strip() or "0")
        check("有 clang 时 --opt 是**另一个后端**的产物（与默认不同）",
              sz(o_bin) != sz(d_bin), f"default={sz(d_bin)} opt={sz(o_bin)}")
    else:
        check("无 clang 时 --opt **点名**退回 lomelf（不静默）",
              "no clang" in (r.stderr or ""), (r.stderr or "")[-160:])

    # 上面那条 `--opt` 判据只说明"编译得动 user_hello"。**链得起来**是另一件事：`-O2`
    # 会把字节循环改写成 memset/memcpy/memmove，而这个运行期没有 libc。见 OPT_LIBCALL_PROBE。
    IT_OUT.mkdir(parents=True, exist_ok=True)
    probe = IT_OUT / "optlibcall_probe.lomt"
    probe.write_text(OPT_LIBCALL_PROBE, encoding="utf-8", newline="\n")
    p_src = wsl_path(probe)
    p_def, p_opt = f"{_T}probe_def", f"{_T}probe_opt"
    r = wsl(f"{PREFIX_IT}/bin/loment", "build", p_src, "-o", p_def)
    check("有字节循环的程序 build 得动（默认后端）",
          r.returncode == 0, (r.stderr or "")[-200:])
    r = wsl(f"{PREFIX_IT}/bin/loment", "build", p_src, "--opt", "-o", p_opt)
    check("有字节循环的程序 --opt 也**链得起来**（合成的 libcall 有人补）",
          r.returncode == 0 and "undefined symbol" not in (r.stderr or ""),
          (r.stderr or "")[-240:])
    if has_clang and r.returncode == 0:
        wsl("chmod", "755", p_opt)
        rr = wsl(p_opt)
        check("补齐 libcall 的产物跑出正确结果（搬对了）",
              rr.returncode == 7, f"rc={rr.returncode} {(rr.stderr or '')[-120:]}")

    # `loment help [COMMAND]` 必须**走得到那一页**。启动器只转发 `help` 而不带后面的参数时,
    # 详细页永远看不到 —— 而目录页里印的正是 `loment help [COMMAND]`。`loment-cli help build`
    # 直呼是好的, 所以这条**只能由装好的启动器**来测 (源码级判据看不见这一层)。
    r = wsl(f"{PREFIX_IT}/bin/loment", "help", "build")
    check("装完后 `loment help build` 打到详细页 (sh 启动器转发了参数)",
          r.returncode == 0 and "Compile and link to an executable" in r.stdout,
          (r.stdout or r.stderr)[:140])

    # 缺件时的报错要指名：临时把 fmt 拿掉，`loment fmt` 应该明确说"这个包没包含"
    wsl("rm", "-f", f"{PREFIX_IT}/bin/loment-fmt")
    r = wsl(f"{PREFIX_IT}/bin/loment", "fmt",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("缺组件时报错指名 (不静默)",
          r.returncode == 3 and "loment-fmt" in (r.stdout + r.stderr),
          f"rc={r.returncode} out={(r.stdout + r.stderr)[:80]}")

    # 幂等: 再装一次仍成功
    r = wsl("sh", wsl_path(install), "--prefix", PREFIX_IT, "--no-path")
    check("重复安装幂等", r.returncode == 0, (r.stderr or "")[-160:])

    # 卸载: 清干净
    r = wsl("sh", wsl_path(install), "--uninstall", "--prefix", PREFIX_IT)
    r2 = wsl("test", "-e", f"{PREFIX_IT}/bin/loment")
    check("--uninstall 摘掉可执行文件", r.returncode == 0 and r2.returncode != 0)


# ------------------------------------------------------------------ 6. Windows 安装脚本

def test_installers_name_an_earlier_lompi() -> None:
    """更靠前的 `lompi`/`loment` 会**静默**盖过我们装的那份 —— 两份安装脚本都要点名它。

    **Why**：2026-10-12 用户报"敲 `lompi` 显示 0.1.0"。查下来是 `~/.local/bin/lompi.exe`
    （一个月前单独装的那份）在 PATH 上排得更前：安装器是**追加** PATH 的（刻意不打乱别人的
    机器），所以更早的那份永远赢，而装完**没有任何一句话**提到这件事 —— 版本号看着不对，
    却无从归因。

    **为什么不能只靠真跑**：真跑那半在 `test_windows_installer` 里（Windows-only），而门禁
    只有 ubuntu runner —— 光靠它的话，这个缺陷在 CI 里谁都拦不住。这一条钉**静态形状**，
    哪台机都跑得到：两份脚本里都要有这段检查，且**真的被调用**（定义在而没人调 = 没有）。
    """
    for label, src, fn in (("install.ps1", loment_dist.INSTALL_PS1, "Show-Shadowed"),
                           ("install.sh", loment_dist.INSTALL_SH, "warn_shadowed")):
        n = src.count(fn)
        check(f"{label} 有'更靠前的同名命令'检查且被真的调用 (定义+调用 >= 2 处)",
              n >= 2, f"{fn} 出现 {n} 次")
        body = src.split(fn, 1)[1] if n else ""
        check(f"{label} 的检查同时管 loment 与 lompi (只管一个就漏一半)",
              "loment" in body and "lompi" in body, f"检查体里少了名字: {body[:90]!r}")


def test_windows_installer(zipf: Path) -> None:
    if sys.platform != "win32":
        # 这一条验的是 install.ps1 / install.cmd 那套 Windows 安装脚本。runner 上**有
        # PowerShell**（所以原先那道 `if not ps` 守卫拦不住），但没有 `cmd` —— 会走到
        # 一半才炸 `FileNotFoundError: 'cmd'`。按本文件对 Windows 专属判据的既有写法
        # 整条跳过，并写明这一半在 CI 上**不覆盖**。
        print("  SKIP  Windows安装脚本: 非 Windows —— 这一半在 CI 上不覆盖"
              "（要覆盖得用 Windows runner）")
        return
    work = loment_dist.STAGE / "it-win"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with zipfile.ZipFile(zipf) as zf:
        _safe_extract(zf, work)
    root = next(p for p in work.iterdir() if p.is_dir())
    ps1 = root / "install.ps1"
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        print("  SKIP  install.ps1 -DryRun: 没有 powershell")
        return
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-DryRun"], capture_output=True, text=True, shell=False,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    check("install.ps1 在 PowerShell 下解析并 -DryRun 通过",
          r.returncode == 0 and "dry-run" in out, out[-220:])

    # 自解压包那条路: install.cmd 用 -PayloadZip 把 payload.zip 解开再装 —— 单独验这段接线
    pz = work / "payload.zip"
    pz.write_bytes(loment_dist._zip("", loment_dist.payload("windows", {})))
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-DryRun", "-PayloadZip", str(pz)], capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    check("install.ps1 -PayloadZip (自解压包路径) 也能跑",
          r.returncode == 0 and "payload" in out, out[-220:])

    exe = OUT / f"loment-{VER}-windows-x64-setup.exe"  # 正式产物目录里的 (测试不重建 exe)
    if exe.exists():
        head = exe.read_bytes()[:2]
        check("setup.exe 是 PE 且非空", head == b"MZ" and exe.stat().st_size > 100000,
              f"head={head!r} size={exe.stat().st_size}")
    else:
        print("  SKIP  setup.exe 存在性: 本次 --emit 用了 --no-exe")

    # 先把**静态形状**钉住 —— 这条跨平台, 门禁 (ubuntu) 跑得到。
    # 下面那条真跑只在 Windows 上做, 而 CI 里没有 Windows runner, 所以只靠它的话
    # 这个缺陷在 CI 里谁都拦不住。不变量: 拉脚本的那条命令**必须给路径**,
    # 不能是裸文件名 (裸名会让 cmd 去搜 PATH/当前目录, 而不是包自己解开的地方)。
    launched = next(l for l in loment_dist.iexpress_sed(Path("t.exe"), Path("."),
                                                       ["install.cmd"]).splitlines()
                    if l.startswith("AppLaunched=")).split("=", 1)[1]
    toks = [t.strip('"') for t in launched.split() if t.strip('"').endswith("install.cmd")]
    check("AppLaunched 给脚本带路径, 不是裸文件名 (裸名会去搜 PATH, 解包目录搜不到)",
          bool(toks) and all(("\\" in t or "/" in t) for t in toks),
          f"AppLaunched={launched!r}")

    # ★ 自解压包要**真跑一遍**, 不是"是 PE 就过"。
    #   2026-09-25 用户报"双击 setup.exe 什么都没发生": wextract 解完包按 `AppLaunched`
    #   拉起安装脚本, 而当时那条命令用**裸文件名**调 `install.cmd` —— cmd 的"搜索当前目录"
    #   被环境变量 `NoDefaultCurrentDirectoryInExePath` 关掉时 (开发工具/加固环境常设,
    #   本仓 agent 进程里就是 1), 脚本找不到, 包解完就静默退出。旧判据只查"是 PE 且非空",
    #   所以它能一路发到用户手上。
    #
    #   用 `iexpress_sed` (就是正式件用的那份 SED, 含同一条 `AppLaunched`) 打一个**极小
    #   载荷**的包再跑它: 载荷写什么与这个缺陷无关 (缺陷在"怎么拉起脚本"), 而拿真载荷跑
    #   会写用户的 PATH 与注册表 —— 判据不能那么干。
    #   `NoDefaultCurrentDirectoryInExePath` **必须显式设上**: 不设的话正常机器上怎么都绿,
    #   这条判据就永远测不到东西。
    ie = shutil.which("iexpress") or r"C:\Windows\System32\iexpress.exe"
    if os.name != "nt" or not Path(ie).exists():
        print("  SKIP  wextract 真跑一遍: 非 Windows / 没有 iexpress")
    else:
        run_dir = loment_dist.STAGE / "exe-run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        landed = run_dir / "landed.txt"
        if not str(landed).isascii():
            # install.cmd 是 ASCII 脚本 (docs/157 §3.4), 塞不进非 ASCII 路径
            print("  SKIP  wextract 真跑一遍: 临时目录路径含非 ASCII")
        else:
            (run_dir / "install.cmd").write_bytes(
                b"@echo off\r\necho landed > \"" + str(landed).encode("ascii") + b"\"\r\n")
            files = sorted(p.name for p in run_dir.iterdir())
            sed = run_dir / "run.sed"
            built = run_dir / "run-setup.exe"
            sed.write_text(loment_dist.iexpress_sed(built, run_dir, files),
                           encoding="ascii", newline="\r\n")
            subprocess.run([ie, "/N", str(sed)], capture_output=True, cwd=str(run_dir),
                           timeout=180)
            if not built.exists():
                check("自解压包能打出 (iexpress)", False, "iexpress 没产出 exe")
            else:
                # wextract 拉起 cmd 是异步的 —— 等哨兵, 不等进程退出
                subprocess.run([str(built)], capture_output=True, timeout=180,
                               env=dict(os.environ, NoDefaultCurrentDirectoryInExePath="1"))
                for _ in range(40):
                    if landed.exists():
                        break
                    time.sleep(0.25)
                check("自解压包解完包后真把安装脚本跑起来 (裸名调用会静默丢失)",
                      landed.exists(), "wextract 没执行 install.cmd (landed.txt 没出现)")

    # ★ 用户最可能走的那一步: 解压 zip -> 双击 / 运行 install.cmd。
    #   它曾只认自解压布局 (去找 payload.zip), 在 zip 布局里必然失败 —— 2026-09-12 用户报障。
    cmd_file = root / "install.cmd"
    r = subprocess.run(["cmd", "/c", str(cmd_file), "-DryRun"], capture_output=True,
                       text=True, shell=False, encoding="utf-8", errors="replace",
                       timeout=180, cwd=str(root), stdin=subprocess.DEVNULL)
    out = (r.stdout or "") + (r.stderr or "")
    check("zip 布局下 install.cmd -DryRun 通过 (用户路径)",
          r.returncode == 0 and "dry-run" in out, out[-220:])

    # ★ 真装一遍 (临时前缀, -NoPath -NoFileType: 不动用户 PATH 与注册表)。
    #   包里就是本机 PE —— 不需要 WSL, 也不需要 clang。
    pfx = loment_dist.STAGE / "it-win-pfx"
    if pfx.exists():
        shutil.rmtree(pfx)
    # 沙箱用户目录: 安装器会把 agent skill 放进 ~/.claude/skills/loment —— 不能碰真机器上的那个
    home = loment_dist.STAGE / "it-win-home"
    if home.exists():
        shutil.rmtree(home)
    (home / ".claude").mkdir(parents=True)
    # 别家 agent 的落点: 造一个**已经有内容**的 Codex 全局指令文件, 用来验证
    # "只加带标记的一段、不碰原有内容、重装不重复、卸载精确摘掉"。
    (home / ".codex").mkdir(parents=True)
    codex = home / ".codex" / "AGENTS.md"
    codex.write_text("MY OWN RULES\nsecond line\n", encoding="utf-8", newline="\n")
    # ★ 更靠前的 `lompi`/`loment` 会**静默**盖过我们装的那份。2026-10-12 用户报障：
    #   敲 `lompi` 拿到的是一个月前单独装的那份（0.1.0）。安装器是**追加** PATH 的（刻意
    #   不打乱别人的机器），所以更早的那份永远赢，而装完没有一句话提到它 —— 版本号看着不
    #   对，却无从归因。就地造一个假 `lompi` 放在 PATH 最前面，装一遍，要求它被**点名**。
    decoy = work / "decoy"
    if decoy.exists():
        shutil.rmtree(decoy)
    decoy.mkdir(parents=True)
    (decoy / "lompi.cmd").write_bytes(b"@echo off\r\necho decoy-lompi\r\n")
    env = dict(os.environ, USERPROFILE=str(home))
    env["PATH"] = str(decoy) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-Prefix", str(pfx), "-PayloadDir", str(root),
                        "-NoPath", "-NoFileType"], capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace", timeout=300, env=env)
    inst_out = (r.stdout or "") + (r.stderr or "")
    check("install.ps1 真装 (临时前缀, 原生 PE, 无 WSL) 退出 0", r.returncode == 0,
          inst_out[-260:])
    check("更靠前的 lompi 被点名 (给出路径, 而不是只说一句注意 PATH)",
          "WARNING" in inst_out and str(decoy).lower() in inst_out.lower(), inst_out[-300:])

    # ★ 窗口那条路真跑一遍。静态那两条只证明"两个分支在"；这两条证明它**开得起来、
    #   装得完、拿得回退出码**。-AutoRun 自动按下 Install，-ShotDone 让窗口把完成页画成
    #   PNG（窗口自己画自己 —— 判据不去截桌面，不拍不属于它的东西），装到沙箱前缀，
    #   不动用户的 PATH 与文件类型。
    wsplash = work / "shot-splash.png"
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(root / "welcome.ps1"),
                        "-InstallPs1", str(root / "install.ps1"),
                        "-LogoPath", str(root / "loment-logo.png"),
                        "-Screenshot", str(wsplash)],
                       capture_output=True, text=True, shell=False, encoding="utf-8",
                       errors="replace", timeout=180, env=env)
    check("welcome.ps1 能画出 splash（自画自截，不碰桌面）",
          r.returncode == 0 and wsplash.exists() and wsplash.stat().st_size > 3000,
          ((r.stdout or "") + (r.stderr or ""))[-200:])

    gp = work / "gui-prefix"
    gdone = work / "shot-done.png"
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(root / "welcome.ps1"),
                        "-InstallPs1", str(root / "install.ps1"),
                        "-LogoPath", str(root / "loment-logo.png"),
                        "-Prefix", str(gp), "-NoPath", "-NoFileType", "-NoSkill",
                        "-AutoRun", "-AutoClose", "-ShotDone", str(gdone)],
                       capture_output=True, text=True, shell=False, encoding="utf-8",
                       errors="replace", timeout=900, env=env)
    check("窗口那条路真装一遍（AutoRun 到沙箱前缀），退出 0 且真装出东西",
          r.returncode == 0 and (gp / "bin/loment.cmd").exists(),
          ((r.stdout or "") + (r.stderr or ""))[-260:])
    check("完成页画得出来（绿勾那页）", gdone.exists() and gdone.stat().st_size > 3000, "")
    cmd = pfx / "bin/loment.cmd"
    body = cmd.read_text(encoding="utf-8", errors="replace") if cmd.exists() else ""
    check("装出 loment.cmd, 它调本机 exe, 且**不再出现 wsl**",
          cmd.exists() and "loment-driver.exe" in body and "wsl" not in body.lower(),
          "" if cmd.exists() else "缺 loment.cmd")
    r2 = subprocess.run(["cmd", "/c", str(cmd), "version"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("装完后 `loment version` 能跑",
          r2.returncode == 0 and loment_dist.DISPLAY in (r2.stdout or ""), (r2.stdout or "")[:120])

    # issue #32: loment.cmd 无参数打出用法, 退出 2, 且 stderr 无批处理执行错误
    r_usage = subprocess.run(["cmd", "/c", str(cmd)], capture_output=True, text=True,
                             shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("装完后 `loment` (无参数) 打出用法且 stderr 为空 (退出 2)",
          r_usage.returncode == 2 and not (r_usage.stderr or "").strip() and
          "check only" in (r_usage.stdout or "") and "--short:" in (r_usage.stdout or ""),
          (r_usage.stderr or "")[:120])

    # 与 sh 启动器那条对称: `loment help build` 的详细页必须走得到 (两份启动器都要转参数)
    r2b = subprocess.run(["cmd", "/c", str(cmd), "help", "build"], capture_output=True, text=True,
                         shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("装完后 `loment help build` 打到详细页 (cmd 启动器转了参数)",
          r2b.returncode == 0 and "Compile and link to an executable" in (r2b.stdout or ""),
          (r2b.stdout or r2b.stderr or "")[:140])

    # issue #13: 未知开关要被**拒绝**，不能当成源文件、也不能静默忽略。
    # `--diag-out` 是**驱动**的开关（docs/182 §5），`loment check` 上并没有这一条 ——
    # 所以这里要的不是"支持它"，而是"别装作支持"：原先它会落到 :scan_arg 里
    # "第一个非开关词就是源文件"那一支，于是被无声吞掉（bash 侧同形状退 2）。
    stray = work / "should_not_exist.jsonl"
    ex_src = pfx / "share/loment/examples/user_hello.lomt"
    r_unknown = subprocess.run(
        ["cmd", "/c", str(cmd), "check", str(ex_src), "--diag-out", str(stray)],
        capture_output=True, text=True, shell=False, encoding="utf-8",
        errors="replace", timeout=180)
    check("装完后 `loment check FILE --diag-out P` 被拒 (未知开关, 退 2, 不写文件)",
          r_unknown.returncode == 2 and "unknown option" in (r_unknown.stderr or "")
          and not stray.exists(),
          f"rc={r_unknown.returncode} err={(r_unknown.stderr or '')[:90]}")

    # ★ 全链判据: `loment run` 在本机编出 PE 并跑起来 —— 这才是"去 WSL"的意义
    ex = pfx / "share/loment/examples/user_hello.lomt"
    r3 = subprocess.run(["cmd", "/c", str(cmd), "run", str(ex)], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=300)
    check("装完后 `loment run` 原生跑通 (无 WSL / 无 clang)",
          r3.returncode == 0 and "PASS loment-user" in (r3.stdout or ""),
          ((r3.stdout or "") + (r3.stderr or ""))[-200:])

    # ★ agent skill: 装完 agent 读得到, 且与仓库里的那份**逐字节相同**
    #   (这条是补出来的 —— 第一版漏了给 share\loment\skill 建目录, 真装才炸出来)
    skill = home / ".claude" / "skills" / "loment" / "SKILL.md"
    want = (loment_dist.ROOT / loment_dist.SKILL).read_bytes()
    check("装完把 agent skill 放进 ~/.claude/skills/loment (沙箱 HOME)",
          skill.exists() and skill.read_bytes() == want, f"缺或不符: {skill}")
    check("随包的 skill 也留在前缀里 (share/loment/skill)",
          (pfx / "share/loment/skill/SKILL.md").exists(), "缺 share/loment/skill/SKILL.md")

    # ★ 跨 agent 广播: 别家 agent 读不到 .claude/skills/, 所以往它自己认的文件里写**指针**。
    #   这条同时钉住"不碰原有内容"与"带标记所以可精确摘除"。
    txt = codex.read_text(encoding="utf-8")
    check("广播到 Codex 的 ~/.codex/AGENTS.md (带标记的指针, 原有内容保留)",
          "<!-- loment:begin -->" in txt and "MY OWN RULES" in txt
          and txt.count("<!-- loment:begin -->") == 1, txt[:140])
    check("指针指向包里那份指南, 不是拷贝",
          str(pfx / "share/loment/skill/SKILL.md") in txt, txt[:200])

    # ★ lompi 的指南走**同一套装法**（用户 2026-09-15），但用**它自己的标记**。
    #   这一条只有在**真装**里才验得到 —— 源码级判据（loment_lompi_test）只能说脚本里
    #   提到了它，不能说明装完真的落到了该落的地方。
    lompi_skill = home / ".claude" / "skills" / "lompi" / "SKILL.md"
    lompi_want = (loment_dist.ROOT / loment_dist.SKILL_LOMPI).read_bytes()
    check("装完把 lompi 的指南放进 ~/.claude/skills/lompi (沙箱 HOME)",
          lompi_skill.exists() and lompi_skill.read_bytes() == lompi_want,
          f"缺或不符: {lompi_skill}")
    check("随包的 lompi 指南也留在前缀里 (share/lompi/skill)",
          (pfx / "share/lompi/skill/SKILL.md").exists(), "缺 share/lompi/skill/SKILL.md")
    txt2 = codex.read_text(encoding="utf-8")
    check("lompi 的 Codex 指针带**自己**的标记 (与 loment 那段分得开)",
          "<!-- lompi:begin -->" in txt2 and "<!-- lompi:end -->" in txt2
          and txt2.count("<!-- lompi:begin -->") == 1
          and txt2.count("<!-- loment:begin -->") == 1, txt2[:220])
    check("lompi 二进制装进了 bin/",
          (pfx / "bin" / "lompi.exe").exists() or (pfx / "bin" / "lompi").exists(),
          "bin/ 里没有 lompi")

    # ★ 通用兜底：payload 里 `share/` 下的**每一个**文件都要真落到前缀里。
    #   写 lompi 时踩过 —— 拷贝清单加了 `share/lompi/skill/SKILL.md`，却忘了先建
    #   `share\lompi\skill` 这个目录，`Copy-Item` 直接抛，**整个安装在第 2 步就死了**
    #   （后面的 agent skill 段根本没跑到）。逐个文件点名只能守住点过名的那些；
    #   这条按 payload 自己列，以后往包里加什么都会自动被守到。
    want_share = [k for k in loment_dist.payload("windows", {}) if k.startswith("share/")]
    missing_share = [k for k in want_share if not (pfx / k.replace("/", "\\")).exists()]
    check(f"payload 里 share/ 的 {len(want_share)} 个文件全落到了前缀",
          not missing_share, f"缺: {missing_share}")

    # ★ 与任何目录约定无关的兜底: 跑 CLI 就能拿到指南。这条是给"机器上既没有 Claude
    #   也没有 Codex"的情形准备的 —— agent 上手陌生语言的第一动作就是跑 CLI 看用法。
    r5 = subprocess.run(["cmd", "/c", str(cmd), "skill"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=120)
    got_path = (r5.stdout or "").strip()
    check("`loment skill` 打出指南路径且该路径存在",
          r5.returncode == 0 and got_path.endswith("SKILL.md") and Path(got_path).exists(),
          got_path[:160])
    r6 = subprocess.run(["cmd", "/c", str(cmd), "skill", "--print"], capture_output=True,
                        text=True, shell=False, encoding="utf-8", errors="replace", timeout=120)
    got = (r6.stdout or "").replace("\r\n", "\n")
    check("`loment skill --print` 的输出 == 指南全文 (逐字节)",
          r6.returncode == 0 and got == want.decode("utf-8").replace("\r\n", "\n"),
          f"rc={r6.returncode} len={len(got)}")
    r7 = subprocess.run(["cmd", "/c", str(cmd), "help"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=120)
    check("`loment help` 的用法里能看到 skill (agent 的第一动作)",
          "loment skill" in (r7.stdout or ""), (r7.stdout or "")[:160])

    # ★ 构建失败必须是**非零退出**, 且 "rc=0" 与 "产物真的在磁盘上" 必须同时成立。
    #   原先 cmd 启动器用 `if errorlevel 1` 判工具失败 —— 可崩溃的工具退出码是**负数**
    #   (0xC000001D = -1073741795), cmd 按有符号比较判定为**假**, 于是落到成功分支: 打出
    #   "loment: x.exe"、rc=0, 磁盘上却什么都没有 (2026-09-15 用户实测)。
    #   这条判据与链接器能力**无关**: 就算以后聚合能编了, 不变式 rc=0 <=> 有产物 仍成立。
    agg = pfx / "share/loment/examples/tour.lomt"      # 按值聚合, 现链接器编不了
    probe = pfx / "rc_probe"
    r8 = subprocess.run(["cmd", "/c", str(cmd), "build", str(agg), "-o", str(probe)],
                        capture_output=True, text=True, shell=False, encoding="utf-8",
                        errors="replace", timeout=180)
    produced = probe.with_suffix(".exe").exists()
    check("build 的退出码与产物一致 (rc=0 <=> 产物存在)", (r8.returncode == 0) == produced,
          f"rc={r8.returncode} 产物={produced} out={(r8.stdout or '').strip()[:80]}")

    # 同一类洞的另一半: 编译**本身**失败时, `loment run` 也绝不能回 0 (否则 CI 被静默骗过)。
    bad = pfx / "bad_probe.lomt"
    bad.write_text("module bad\n\nfn f() -> u32 {\n    return nope;\n}\n",
                   encoding="utf-8", newline="\n")
    r9 = subprocess.run(["cmd", "/c", str(cmd), "run", str(bad)], capture_output=True,
                        text=True, shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("编译失败时 `loment run` 非零退出", r9.returncode != 0,
          f"rc={r9.returncode} out={(r9.stdout or '')[:80]}")

    r4 = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                         "-Uninstall", "-Prefix", str(pfx), "-NoPath", "-NoFileType"],
                        capture_output=True, text=True, shell=False, encoding="utf-8",
                        errors="replace", timeout=180, env=env)
    check("--uninstall 摘掉前缀", r4.returncode == 0 and not pfx.exists(),
          ((r4.stdout or "") + (r4.stderr or ""))[-200:])
    check("--uninstall 也摘掉 agent skill (只摘它自己建的那个目录)", not skill.exists(),
          "skill 还在")
    check("--uninstall 也摘掉 lompi 的指南", not lompi_skill.exists(), "lompi skill 还在")
    check("--uninstall 还原别家 agent 的文件 (只摘标记段, 原有内容不动)",
          codex.read_text(encoding="utf-8") == "MY OWN RULES\nsecond line\n",
          codex.read_text(encoding="utf-8")[:80])
    shutil.rmtree(pfx, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------------------ main

def test_driver_seed_matches_reference() -> None:
    """`SEED` 必须**就是**参考实现为 `loment/selfhost/driver.lomt` 发射的 IR。

    **为什么这条在这个文件里也要有**（`loment_seed_test` 已经有一条一样的）：包里的
    `bin/loment-driver` 现在**取自种子**，不再现场重编（见 `loment_dist.SEED_TOOL`）——
    于是"包里那个 driver 确实等于从 driver.lomt 编出来的东西"就全押在种子上，而种子
    一旦悄悄过期，**发行包判据自己看不出来**。这条把那个前提钉在**同一个判据**里。

    代价是参考实现发射一次 driver（本机 2.5s），换掉的是原来那 273s 的重编 —— 同一件事
    的**第一遍**，由本地跑的这条钉住；`loment_seed_test` 的 `bootstrap` 另有一条
    "stage1 自编 == 种子"的定点判据。
    """
    with contextlib.redirect_stdout(io.StringIO()):
        rc = loment_seed.check()
    check("种子 == 参考实现为 driver.lomt 发射的 IR（包里 driver 的来源）", rc == 0)


def test_opt_driver_runs() -> None:
    """`--opt` 编出来的**驱动自己**必须跑得起来，而且与 `-O0` 产物**逐字节同输出**。

    **为什么单起一条**：包里 `bin/loment-driver` 的来源就是 `SEED`（3.2 MB IR，全仓最大的
    一个入口），而 `--opt` 会把这份 IR 直接交给 clang `-O2`。2026-10-10 实测过它的下场：
    `-O0` 编出来一切正常，`-O1/-O2` 编出来**一跑就 SIGSEGV**（`docs/212` §5.2）。
    `user_hello` 那条 `--opt` 判据**看不见它** —— 那条编的是**用户程序**，不是驱动。

    根因是入口 `_start` 缺 `stackrealign`（进程入口的 `rsp` 比 SysV 函数入口约定少 8），
    修法见 `docs/212` §5.2。这条判据钉的就是"修好了"这件事：同一个程序，优化过的驱动与
    没优化的驱动必须**行为相同**——这不是"跑得动"能替代的（编错的值照样跑得动）。

    **Why `-O0` 当对照而不是跟参考实现比**：不需要第二份真源，而且它直接测的正是那条通用
    主张本身（同一份 IR 换个优化档不该改行为）。代价是 3.2 MB IR 上两趟 clang。
    """
    cc = loment_dist.clang_path()
    if cc is None:
        print("  SKIP  驱动 --opt: 没有 clang —— 这条要真编一次才作数")
        return
    # Windows 上交叉编出的是 Linux ELF, 本机跑不了, 得借 WSL (与 `wsl()` 同一条分流)。
    if sys.platform == "win32" and not shutil.which("wsl"):
        print("  SKIP  驱动 --opt: 没有 WSL —— 交叉编出来的 ELF 在本机跑不了")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        (td / "seed.ll").write_text(loment_dist.SEED.read_text(encoding="utf-8"),
                                    encoding="utf-8", newline="\n")
        # 与打包那一步**同一份文本**：`-O2` 会合成 memset/memcpy/memmove（docs/212 §5.1）
        (td / "libcalls.ll").write_text(loment_dist._FREESTANDING_LIBCALLS,
                                        encoding="utf-8", newline="\n")
        src = ROOT / "loment" / "examples" / "user_hello.lomt"
        got: dict[str, tuple[int, bytes]] = {}
        for lvl in ("0", "2"):
            exe = td / f"driver_O{lvl}"
            b = subprocess.run(
                [cc, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
                 "-static", "-fno-pie", "-Wl,-e,_start", f"-O{lvl}", str(td / "seed.ll"),
                 str(td / "libcalls.ll"), "-o", str(exe)],
                capture_output=True, text=True, shell=False)
            if b.returncode != 0:
                check(f"驱动 IR 能被 clang -O{lvl} 编出来", False, b.stderr[-200:])
                return
            # **拷到 WSL 自己的盘上再跑** —— DrvFs (/mnt/c...) 上执行不了 ELF。
            tag = f"{_T}optdrv_O{lvl}.bin"
            outp = td / f"out_O{lvl}.txt"
            r = wsl("sh", "-c",
                    f"cd {wsl_path(ROOT)} && rm -f {tag} && cp {wsl_path(exe)} {tag} && "
                    f"chmod +x {tag} && timeout 300 {tag} {wsl_path(src)} "
                    f"> {wsl_path(outp)} 2>/dev/null; echo -n $?")
            try:
                rc = int(r.stdout.strip())
            except ValueError:
                rc = -1
            got[lvl] = (rc, outp.read_bytes() if outp.exists() else b"")
        check("驱动自己在 -O2 下编出来跑得起来 (rc=0)", got["2"][0] == 0, f"rc={got['2'][0]}")
        check("驱动 -O2 与 -O0 的产物逐字节同输出",
              got["0"][1] == got["2"][1] and got["0"][1] != b"",
              f"{len(got['0'][1])}B vs {len(got['2'][1])}B")


def test_check_detects_staleness() -> None:
    """`--check` 必须能发现"归档里那份来源文件不是当前源码"。

    它原先只拿归档跟**它自己的** SHA256SUMS 比 —— 两边一起过期就永远报"一致"。
    2026-09-15 用户问"安装包更新了吗"才发现的: `use` 改动之后 `loment/dist/` 里的
    skill 与 seed 全是旧的, 而 `--check` 照样绿。这条判据就是钉那个洞。
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        lin = loment_dist.payload("linux", {"loment-driver": (b"ELF", b"PE")})
        lin["share/loment/skill/SKILL.md"] = (b"stale\n", 0o644)   # 冒充「过期的归档」
        (out / f"loment-{VER}-linux-x64.tar.gz").write_bytes(
            loment_dist._tar_gz(f"loment-{VER}-linux-x64", lin))
        loment_dist.write_sums(out)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = loment_dist.check(out)
        check("--check 能发现归档里的来源文件过期 (原先是个洞)", rc != 0, f"rc={rc}")
    # 版本文件也带提交号, 所以它同属"打完包又提交了一次"那一类 —— 2026-09-16 之前它
    # **不在** _fresh_sources 里, 于是 0.1.4-pre1 的包里印的是上一个提交号的 commit 行,
    # 而 `--check` 照样绿 (真实事故, 不是假想)。
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        lin = loment_dist.payload("linux", {"loment-driver": (b"ELF", b"PE")})
        lin["share/loment/version"] = (b"Loment 9.9.9 Stale (9.9.9-stale)\ncommit 0000000\n",
                                       0o644)
        (out / f"loment-{VER}-linux-x64.tar.gz").write_bytes(
            loment_dist._tar_gz(f"loment-{VER}-linux-x64", lin))
        loment_dist.write_sums(out)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = loment_dist.check(out)
        check("--check 能发现归档里的 version 文件过期 (提交号印的是上一代)", rc != 0,
              f"rc={rc}")
    ver = loment_dist.version_text()
    check("version 文件的提交号就是当前 HEAD",
          loment_dist._git("rev-parse", "--short", "HEAD") in ver, ver.strip())


def test_skill_example_sync() -> None:
    """skill §1 贴的那份 `tour` 必须**逐字节**等于 `loment/examples/tour.lomt`, 而且能过前端。

    **Why**: 这一块是给"只装了包、没有仓库"的 agent 当**唯一**参考的 —— 它错了, 用户
    照着敲就得到一个编不过的程序, 且没有任何别的东西能纠正他。
    2026-09-15 真的发生了: 复制进 SKILL.md 时漏掉 `const` 后的 `;`, 而 skill 正文
    自称"和仓库里那份是同一份" —— 没有任何判据守着这句话。
    **How to apply**: 改 `loment/examples/tour.lomt` 之后必须同步改 skill (以及
    `~/.claude/skills/`), 否则 `loment_dist_test` 红。
    """
    sk = (ROOT / loment_dist.SKILL).read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```rust\n(.*?)```", sk, re.S) if "module tour" in b]
    check("skill 里有且只有一份 tour 全文", len(blocks) == 1, f"命中 {len(blocks)} 块")
    if len(blocks) != 1:
        return
    src_text = blocks[0].strip()
    ex = (ROOT / "loment/examples/tour.lomt").read_text(encoding="utf-8")
    body = ex[ex.index("module tour"):].strip()
    check("skill §1 == loment/examples/tour.lomt (逐字节)", src_text == body)

    src = Path(tempfile.mkdtemp(prefix="lom_skill_")) / "tour.lomt"
    src.write_text(src_text + "\n", encoding="utf-8")
    try:
        mod = lomentc.load(src)
        errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, src.parent, entry=src))
    except lomentc.LomError as e:
        errs = [str(e)]
    check("skill §1 能过 check (不是一段装饰性代码)", not errs, errs[0] if errs else "")


#: 哪些文档里的 ```rust 块进判据 —— **给读者抄的那些**, 不是设计记录。
#: `SKILL.md` 是"只装了包、没有仓库"的 agent 唯一的参考; README 与 QUICKSTART 是
#: 新读者照着敲的两份。
#:
#: **`docs/*.md` 故意不在里面**: 它们是设计/测量记录, 里面多的是**片段**
#: (半句语法、故意写错的对照), 不是能独立编译的单元 —— 把它们全拉进来, 只会得到
#: 一屏 `<!-- no-compile -->` 标记, 那种"跳过"是没有信息的。
#:
#: **`docs/manual/` 也不在**: 它的示例页**就是从 `loment/examples/*.lomt` 生成出来的**
#: (`tools/loment_manual.py:44-52` 逐个示例 `lomdoc.render`), 所以那些"代码块"由
#: `loment_p9_test` 与 `loment_elf_test` 覆盖 —— 在这里再扫一遍是重复, 不是覆盖。
DOC_SAMPLES = [loment_dist.SKILL, "README.md", "QUICKSTART.md"]


def test_docs_samples_compile() -> None:
    """给读者抄的那几份文档里, **每个** ```rust 样例都必须是能过前端的真代码。

    **Why**: 这些样例是读者照着敲的东西 —— 错一个字符, 他就卡在那里, 而且没有任何东西
    能告诉他哪边错了。2026-09-15 实测过一次 (`tour` 里漏了个 `;`, 指南还自称"和仓库里
    那份是同一份")。**原先这条只扫 SKILL.md 一份**, 于是 README 与 QUICKSTART 里那几块
    **没有任何东西看着** (docs/205 R6) —— 2026-09-23 把它一般化成 `DOC_SAMPLES`。
    **How to apply**: 有两种片段要跳过, 都在围栏前一行加 `<!-- no-compile -->`:

    1. **故意写错**的片段 (展示"这样会报 E22"之类);
    2. **半块**片段 —— 需要**伴生文件**才编得过的那种 (实测: `addin` 的入口那一半,
       它的 `set choose` 定义在旁边的 `chooseset.lomt` 里)。这类**必须写清为什么**,
       并指到仓库里真能跑的那一对 (`loment/examples/addin/`) —— 否则"跳过"就成了
       悄悄放宽。

    只对 `rust` 围栏生效; 别的语言围栏不在扫描范围内。每份文档**各自**要有非零命中:
    抽不到样例的那一份是"判据对它在空转", 而不是"它恰好没有代码"。
    """
    td = Path(tempfile.mkdtemp(prefix="lom_doc_sample_"))
    total = 0
    for doc in DOC_SAMPLES:
        text = (ROOT / doc).read_text(encoding="utf-8")
        n = 0
        for i, m in enumerate(re.finditer(r"```rust\n(.*?)```", text, re.S)):
            if "no-compile" in text[max(0, m.start() - 80):m.start()]:
                continue
            src = td / f"{Path(doc).stem}_{i}.lomt"
            src.write_text(m.group(1), encoding="utf-8", newline="\n")
            try:
                mod = lomentc.load(src)
                errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, td, entry=src))
            except lomentc.LomError as e:
                errs = [str(e)]
            n += 1
            check(f"{doc} 的 rust 样例 #{i} 过前端", not errs, errs[0] if errs else "")
        check(f"{doc} 里扫到了 rust 样例 (判据没对这份空转)", n > 0, "一块都没扫到")
        total += n
    print(f"      {len(DOC_SAMPLES)} 份文档共 {total} 块 rust 样例全过前端")


def main() -> int:
    print("loment_dist_test —— 发行包判据 (docs/162)")
    for name, fn in (("driver 的来源：种子 == 参考实现", test_driver_seed_matches_reference),
                     ("--opt 编出来的驱动自己跑得起来", test_opt_driver_runs),
                     ("布局与脚本卫生", test_layout), ("归档内容与确定性", test_archives),
                     ("安装脚本点名更靠前的同名命令", test_installers_name_an_earlier_lompi),
                     ("--check 的新鲜度", test_check_detects_staleness),
                     ("skill 与示例同步", test_skill_example_sync),
                     ("读者文档里的样例都能编", test_docs_samples_compile)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(name, False, f"{type(e).__name__}: {e}")
    tar, zipf = build()
    try:
        test_install_sh(tar)
    except Exception as e:  # noqa: BLE001
        check("install.sh 端到端", False, f"{type(e).__name__}: {e}")
    try:
        test_windows_installer(zipf)
    except Exception as e:  # noqa: BLE001
        check("Windows 安装脚本", False, f"{type(e).__name__}: {e}")
    bad = [r for r in RESULTS if not r[1]]
    print(f"\nloment_dist_test: {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for name, _ok, detail in bad:
        print(f"  FAIL  {name}  [{detail}]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
