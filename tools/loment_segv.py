#!/usr/bin/env python3
# loment_crash.py — 原生档的段错误报告器（`docs/367`）
#
# 原生产物不经过 LVM，信号来了它自己一句话不说：用户看到的是 shell 的
# `Segmentation fault`，或者 Windows 启动器下的 139。这个工具把同样的信号变成
# 一份 Lomenterr 形状的诊断 —— 信号、si_code、故障地址、读写方向、PC、RSP，
# 可选地把 PC 解析成「函数+偏移 / 源行」（需要带 `-g` 的产物 + `llvm-objdump`）。
#
# **这是 stage 0**：报告发生在**宿主**，产物本身没有变。stage 1（把报告器嵌进
# 产物、自己说）见 `docs/367` §4。
#
# 用法:
#   python tools/loment_crash.py [--map OBJ] ./prog [args...]
#   python tools/loment_crash.py --json ./prog          # 一行 JSON（给编辑器/CI）
#
# 退出码: 被信号杀死 = 128 + 信号（与 shell 一致）；正常退出 = 子进程的退出码；
#         用法/无法运行 = 2。
#
# 平台: Linux（含 WSL）。Windows 上请经 WSL 运行 —— 与 `loment_elf_test` 同一条路。

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys

# ---------------------------------------------------------------- ptrace ABI（x86-64 Linux）

PTRACE_TRACEME = 0
PTRACE_CONT = 7
PTRACE_KILL = 8
PTRACE_GETREGS = 12
PTRACE_GETSIGINFO = 0x4202

#: 我们要拦的五个信号：它们都是"进程马上要没了"，报告是最后一句能说的话。
FATAL_SIGNALS = {
    signal.SIGSEGV: ("SEGV", "段错误（segmentation fault）"),
    signal.SIGBUS: ("BUS", "总线错误（bus error）"),
    signal.SIGILL: ("ILL", "非法指令（illegal instruction）"),
    signal.SIGFPE: ("FPE", "算术异常（floating-point exception）"),
    signal.SIGABRT: ("ABRT", "进程自己 abort 了（abort）"),
}

SIG_NAMES = {
    signal.SIGSEGV: "SIGSEGV",
    signal.SIGBUS: "SIGBUS",
    signal.SIGILL: "SIGILL",
    signal.SIGFPE: "SIGFPE",
    signal.SIGABRT: "SIGABRT",
}

#: `si_code` 是**按信号分家**的：1 在 SIGSEGV 下是 `SEGV_MAPERR`，在 SIGBUS 下是
#: `BUS_ADRALN`。第一版把它做成一张扁平表，第二次写键把第一次覆盖了 —— 于是空指针
#: 写被判成"地址对齐错误"。键是 `(信号, code)`。
SI_CODES = {
    (signal.SIGSEGV, 1): ("SEGV_MAPERR", "地址没有映射到任何对象"),
    (signal.SIGSEGV, 2): ("SEGV_ACCERR", "地址已映射，但不允许这次访问"),
    (signal.SIGBUS, 1): ("BUS_ADRALN", "地址对齐错误"),
    (signal.SIGBUS, 2): ("BUS_ADRERR", "物理地址不存在"),
    (signal.SIGBUS, 3): ("BUS_OBJERR", "对象自身的硬件错误"),
    (signal.SIGILL, 2): ("ILL_ILLOPN", "非法操作码"),
    (signal.SIGFPE, 1): ("FPE_INTDIV", "整数除零"),
    (signal.SIGFPE, 2): ("FPE_INTOVF", "整数溢出"),
}


class UserRegs(ctypes.Structure):
    """`struct user_regs_struct`（x86-64）：27 个 u64。索引见 `REG_*`。"""

    _fields_ = [("r15", ctypes.c_ulonglong), ("r14", ctypes.c_ulonglong),
                ("r13", ctypes.c_ulonglong), ("r12", ctypes.c_ulonglong),
                ("rbp", ctypes.c_ulonglong), ("rbx", ctypes.c_ulonglong),
                ("r11", ctypes.c_ulonglong), ("r10", ctypes.c_ulonglong),
                ("r9", ctypes.c_ulonglong), ("r8", ctypes.c_ulonglong),
                ("rax", ctypes.c_ulonglong), ("rcx", ctypes.c_ulonglong),
                ("rdx", ctypes.c_ulonglong), ("rsi", ctypes.c_ulonglong),
                ("rdi", ctypes.c_ulonglong), ("orig_rax", ctypes.c_ulonglong),
                ("rip", ctypes.c_ulonglong), ("cs", ctypes.c_ulonglong),
                ("eflags", ctypes.c_ulonglong), ("rsp", ctypes.c_ulonglong),
                ("ss", ctypes.c_ulonglong), ("fs_base", ctypes.c_ulonglong),
                ("gs_base", ctypes.c_ulonglong), ("ds", ctypes.c_ulonglong),
                ("es", ctypes.c_ulonglong), ("fs", ctypes.c_ulonglong),
                ("gs", ctypes.c_ulonglong)]


_libc = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None


def _ptrace(req: int, pid: int, addr: int = 0, data: int = 0) -> int:
    assert _libc is not None
    res = _libc.ptrace(ctypes.c_long(req), ctypes.c_long(pid), ctypes.c_void_p(addr),
                       ctypes.c_void_p(data))
    if res == -1:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return res


def _siginfo(pid: int) -> tuple[int, int, int]:
    """读 `siginfo_t`：`(signo, code, addr)`。

    x86-64 Linux 上 `si_signo / si_errno / si_code` 是前三个 `int`，`si_addr`
    在偏移 16 的联合里（`SIGSEGV/SIGBUS/SIGILL/SIGFPE` 都是这个成员）。
    """
    buf = ctypes.create_string_buffer(128)
    _ptrace(PTRACE_GETSIGINFO, pid, 0, ctypes.addressof(buf))
    signo, _errno, code = struct.unpack_from("<iii", buf.raw, 0)
    addr = struct.unpack_from("<Q", buf.raw, 16)[0]
    return signo, code, addr


def _regs(pid: int) -> UserRegs:
    r = UserRegs()
    _ptrace(PTRACE_GETREGS, pid, 0, ctypes.addressof(r))
    return r


# ---------------------------------------------------------------- 地址 -> 函数/源行

def _objdump_rows(obj: str) -> list[tuple[int, str, str]]:
    """`llvm-objdump -d -l` 的行表：`(addr, "file:line", fn)`；拿不到就空表。"""
    exe = shutil.which("llvm-objdump") or shutil.which("llvm-objdump-19") \
        or shutil.which("llvm-objdump-18") or shutil.which("llvm-objdump-17")
    if not exe:
        return []
    try:
        dump = subprocess.run([exe, "-d", "-l", obj], capture_output=True,
                              text=True, timeout=120, shell=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, str, str]] = []
    cur_fn, cur_src = "?", ""
    for ln in dump.splitlines():
        s = ln.strip()
        m = re.match(r"^([0-9a-f]+) <([^>]+)>:", s)
        if m:
            cur_fn = m.group(2)
            continue
        if s.startswith(";"):
            cur_src = s.lstrip("; ").strip()
            continue
        m = re.match(r"^([0-9a-f]+):", s)
        if m and cur_src:
            rows.append((int(m.group(1), 16), cur_src, cur_fn))
    return rows


def _symbolize(rip: int, obj: str | None) -> str:
    """`函数+偏移（源行）`；没有行表就给空串。"""
    if not obj:
        return ""
    rows = _objdump_rows(obj)
    hit = [r for r in rows if r[0] <= rip]
    if not hit:
        return ""
    best = max(hit, key=lambda r: r[0])
    return f"函数 {best[2]}+0x{rip - best[0]:x}（源行 {best[1]}）"


# ---------------------------------------------------------------- 报告

def render(exe: str, signo: int, code: int, addr: int, regs: UserRegs,
           obj: str | None) -> str:
    tag, title = FATAL_SIGNALS.get(signo, ("SIG", "进程被信号杀死"))
    signame = SIG_NAMES.get(signo, f"信号 {signo}")
    code_name, code_why = SI_CODES.get((signo, code), (f"si_code={code}", "未分类"))
    where = _symbolize(regs.rip, obj)
    lines = [
        f"loment: 进程死于信号 {signo}（{signame}）",
        f"error[{tag}]: {title}",
        f"  --> {exe}:0x{regs.rip:x}",
        "   |",
        f"   | 信号：{signo}（{signame}）；{code_name}：{code_why}",
        f"   | 访问：0x{addr:x}",
        f"   | PC：0x{regs.rip:x}；RSP：0x{regs.rsp:x}",
    ]
    if where:
        lines.append(f"   | 位置：{where}")
    lines += [
        "   |",
        f"   = 事实：进程执行到 0x{regs.rip:x} 时访问 0x{addr:x}，收到 {signame}"
        f"（{code_name}）。原生档没有运行期站点表，这一句是宿主替它说的。",
        "   = 建议：用 `--debug` 构建、再给本工具 `--map <产物>`，位置就能解析成"
        "函数与 `.lomt` 源行；要逐站点的诊断（对象表 / 对象代 / 对象现场），"
        "用 LVM 档运行（`--substrate sw_cap`，见 docs/366）。stage 1 把这段报告嵌进"
        "产物本身（docs/367 §4），那时直接跑也看得到。",
    ]
    return "\n".join(lines)


def _json_row(exe: str, signo: int, code: int, addr: int, regs: UserRegs,
              obj: str | None) -> str:
    return json.dumps({
        "exe": exe,
        "signal": signo,
        "signal_name": SIG_NAMES.get(signo, str(signo)),
        "si_code": code,
        "addr": addr,
        "pc": regs.rip,
        "rsp": regs.rsp,
        "where": _symbolize(regs.rip, obj),
    }, ensure_ascii=False)


def run(exe: str, args: list[str], obj: str | None, as_json: bool) -> int:
    pid = os.fork()
    if pid == 0:
        try:
            _ptrace(PTRACE_TRACEME, 0)
            os.execv(exe, [exe] + args)
        except BaseException:
            os._exit(127)
    while True:
        try:
            _wpid, status = os.waitpid(pid, 0)
        except ChildProcessError:
            return 127
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            signo = os.WTERMSIG(status)
            print(f"loment: 进程死于信号 {signo}（报告器没能截住现场）", file=sys.stderr)
            return 128 + signo
        if os.WIFSTOPPED(status):
            signo = os.WSTOPSIG(status)
            if signo not in FATAL_SIGNALS:
                # 非致命信号（含 exec 后的 SIGTRAP）原样放行。
                _ptrace(PTRACE_CONT, pid, 0, 0 if signo == signal.SIGTRAP else signo)
                continue
            try:
                got_signo, code, addr = _siginfo(pid)
            except OSError:
                got_signo, code, addr = signo, 0, 0
            try:
                regs = _regs(pid)
            except OSError:
                regs = UserRegs()
            if as_json:
                print(_json_row(exe, got_signo, code, addr, regs, obj), file=sys.stderr)
            else:
                print(render(exe, got_signo, code, addr, regs, obj), file=sys.stderr)
            _ptrace(PTRACE_KILL, pid)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            return 128 + got_signo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_crash",
                                 description="原生档的段错误报告器（docs/367，stage 0）")
    ap.add_argument("--map", default=None, metavar="OBJ",
                    help="带 -g 的产物/对象：用 llvm-objdump 把 PC 解析成函数与源行")
    ap.add_argument("--json", action="store_true", help="一行 JSON（给编辑器/CI）")
    ap.add_argument("exe")
    ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("loment_crash: 只在 Linux（含 WSL）上工作 —— Windows 请经 WSL 运行",
              file=sys.stderr)
        return 2
    exe = os.path.abspath(a.exe)
    if not os.path.exists(exe):
        print(f"loment_crash: 找不到 {a.exe}", file=sys.stderr)
        return 2
    return run(exe, list(a.args), a.map, a.json)


if __name__ == "__main__":
    sys.exit(main())
