#!/usr/bin/env python3
# loment_dist.py — Loment 发行包: **命令安装** + **安装包安装** (docs/162)
#
# 产物 (loment/dist/):
#   loment-<ver>-linux-x64.tar.gz        含 install.sh      命令安装: sh install.sh
#   loment-<ver>-windows-x64.zip         含 install.ps1     命令安装: powershell -File install.ps1
#   loment-<ver>-windows-x64-setup.exe   自解压安装包       双击安装; 用 Windows 自带的 iexpress
#                                                           (前两件是确定性字节, 这件不是 —— 见 docs/162)
#   SHA256SUMS                           上面几件的 sha256
#
# 包里**没有 Python**: 八个可执行文件都是自举产物 (种子 → stage1 → IR → 链接), 构建与链接
# 都不需要 clang/WSL 才能装。构建期需要 Python 的只有这个打包工具本身 (仓库工具链, 不进包)。
#
#   python tools/loment_dist.py --emit                        # 全部（本机原生后端，无 clang/WSL）
#   python tools/loment_dist.py --emit --only driver --no-exe # 快速子集 (门禁用)
#   python tools/loment_dist.py --check                       # 现有产物与 SHA256SUMS 一致?
#   python tools/loment_dist.py --list                        # 只列会打进去的文件
#
# 退出码: 0 = 成功 / 1 = 失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import os
import gzip
import hashlib
import io
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_release  # noqa: E402  (版本名单一真源: RELEASE / RELEASE_NAME)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "dist"
STAGE = ROOT / "loment" / "build" / "dist"
SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"

#: ---- 渠道：包 = 发行版（`docs/231` §2.3）----------------------------------
#:
#: **这条流水线造出来的东西就是发行版**，所以它用的每一个二进制都由**发行版种子**建 ——
#: 种子现算：`loment_seed.reference_ir("release")`，而那个注入**只改
#: `loment/selfhost/checker.lomt` 里那一行渠道常量**。于是"发行版里 `choose vm` 是编译
#: 错误"是**这条流水线的属性**，不是"谁记得去翻某个开关"。
#:
#: 代价（明写）：包里的 `selfhost_driver.ll` **不等于**仓库里那份。判据因此从"逐字节相等"
#: 换成"**可重算的定点相等**"：`release_seed_text()` 任何时候重算都得得出同一串字节
#: （`tools/loment_channel_test.py` 钉它）。
RELEASE_SEED = STAGE / "selfhost_driver.release.ll"


def release_seed_text() -> str:
    """按发行版渠道算一遍自举驱动的 IR（注入只动 `checker.lomt` 那一行）。"""
    import loment_seed  # noqa: PLC0415

    return loment_seed.reference_ir("release")


#: 当前这次构建用的种子。**默认是仓库里那份（dev）**，`emit()` 会把它换成发行版那份。
ACTIVE_SEED: Path = SEED

DISPLAY = loment_release.RELEASE_NAME       # 人读: 0.1.4
VER = loment_release.RELEASE                # 机器: 0.1.4

#: 包里的工具 -> (入口源文件, 编译时的 CWD)。名字就是安装后的可执行名。
#: CWD 那一格是给 **路径形式的 `use "..."`** 用的 —— 它按「仓根 → 导入文件所在目录」解析,
#: 两边实现同序 (2026-09-16 之前自举镜只按 CWD, 已修; 见 docs/158 §5)。仓库其它工具全用
#: 名字形式 (`use bytes`), 那一套的搜索根是项目根/工具链, 与 CWD 无关, 所以它们一律写 "."。
#:
#: lompi 也写 ".": 它的路径形式 import 靠导入文件所在目录 (修复后的规矩), 而它现在从
#: 随包 store 里按名字取 `internet` 的模块 —— 自举镜的**开发 checkout** 那一层把 store
#: 写成 CWD 相对的 `lompi/store`, 只有从仓根编才命中 (issue #375)。
TOOLS: list[tuple[str, str, str]] = [
    ("loment-driver", "loment/selfhost/driver.lomt", "."),
    ("loment-lsp", "loment/tools/lsp.lomt", "."),
    ("loment-fmt", "loment/tools/lomfmt.lomt", "."),
    ("loment-doc", "loment/tools/lomdoc.lomt", "."),
    # `loment build/run` 的链接器 —— 自举侧的 lomelf 镜像。有它之后 **包里不再需要 clang**:
    # 存出来的产物本来就是目标平台自己的格式（Linux 出 ELF / Windows 出 PE）。
    ("loment-lomelf", "loment/tools/lomelf.lomt", "."),
    # 命令面（help/codes/stat/grep/ls/tree/...）—— 用 Loment 自己写的 CLI 前端。
    # 为什么不在启动器里写: 启动器有两份 (bash + batch), 命令写在那边就得写两遍并保持同步。
    ("loment-cli", "loment/tools/lomcli.lomt", "."),
    # 报错器 (docs/182 §6): 吃编译器 --diag-out 吐的 JSONL, 查 surface_data 补中文标题与
    # 修复建议, 渲染成 "error[E0NN]: ... / --> 文件:行 / 源行 / 插入符 / 建议"。
    # **名字没有 `loment-` 前缀**, 与 lompi 一样是个独立命令 —— 它由启动器起, 不是 `loment`
    # 的子命令 (`loment help` 里没有它)。为什么它在包里而不是并进驱动: 两个实现的消息文本
    # 本来就不同, 硬凑"两侧逐字节一致"只会造成假一致, 于是"渲染"归它, 驱动只吐码+位置
    # (docs/182 §5.3)。
    ("lomenterr", "loment/tools/lomenterr.lomt", "."),
    # lompi —— Loment 库的包管理器, **不是 Loment 官方工具**(它不编 Loment、不读源码树,
    # 是另一个命令; `loment help` 里不出现它, 见 docs/169 §2)。随包一起装, 因为它是用
    # Loment 写的、由同一条自举链编出来的。源码的**正本在开发者的工作区** (`lompi/` 这份
    # 是随包发布的快照), 两边靠 tools/lompi_sync.py 校验, 见 docs/170。
    ("lompi", "lompi/lompi.lomt", "."),
    # **VM（`choose vm` 的执行体）** —— `#355`，`docs/263`。2026-10-18 起随包发：
    # 引擎 T0–T3 与去优化都落了（`docs/259`/`docs/261`），失败面有判据（`docs/262`）。
    # ⚠ 它是**镜像**（Loment 写的那份），走同一条自举链；`docs/231` §2 的渠道闸也同日放开，
    # 所以"发行版可以调 VM"这句话现在有东西可调，不是一句声明。
    ("loment-lvm", "loment/tools/lvm.lomt", "."),
]
#: 随包发的示例。`tour.lomt` 是**一个文件过完整门语言**的导览 —— 纯包用户没有仓库里的
#: 其它示例, 所以它比 hello 更该在包里 (agent 指南 §1 讲的就是这一份, 三者同源)。
EXAMPLES = ("loment/examples/user_hello.lomt", "loment/examples/tour.lomt")
EXAMPLE = EXAMPLES[0]        # 冒烟测试与判据用的那一个
ICON = "editors/loment.ico"
#: 安装界面正中那个**圆角方形标**。不新画一张图：`editors/loment.ico` 的 7 帧全是 PNG，
#: 取最大的那帧直接当图片用 —— 于是 logo 只有**一个真源**，安装器也不必自带解码器。
LOGO_NAME = "loment-logo.png"
LICENSES = ("LICENSE-MIT", "LICENSE-APACHE")
# 随包的 agent skill —— 装完 Loment, AI agent 读它就会写 Loment。
# **自足**: 内建函数表/语法/错误码/包内命令都在里面, 不引用仓库路径。原样拷进包,
# 不做 @VERSION@ 替换 —— 这样"包里的那份 == 仓库里的那份"是可判据的。
SKILL = ".claude/skills/loment/SKILL.md"
#: lompi 的 agent 指南。**与 loment 那份同一套装法**（用户 2026-09-15 要求）：随包发、
#: 装进 ~/.claude/skills/lompi/、往 Codex 的 AGENTS.md 写一段**独立标记**的指针、卸载摘掉。
#: 标记用 `lompi:` 前缀而**不复用** loment 的 —— 两份指南是两件事，卸载一份不该动另一份。
SKILL_LOMPI = ".claude/skills/lompi/SKILL.md"
#: lompi 的**标准库 store**：`std` 128 个 .lomt（127 个模块 + `std.lomt` 门面）与 `host`
#: 7 个，各带一份 `pkg.lomp`，共 137 个文件。**随 Loment 一起装**（用户 2026-09-16 定），
#: 落在包里 `share/lompi/store/`，安装时再拷进 lompi 自己认的全局 store。
#: 正本在开发者工作区，见 tools/lompi_sync.py 的第三组配对（docs/170）。
STORE_DIR = "lompi/store"

#: 纯文本脚本一律 ASCII: Windows PowerShell 5.1 用 ANSI 读无 BOM 的 .ps1, 非 ASCII 会变乱码
#: 并连带把后续行解析坏 (docs/157 §3.4 踩过)。中文说明在 README.md 与 docs/162 里。
LAUNCHER_SH = r'''#!/usr/bin/env bash
# Loment launcher (@DISPLAY@, @VERSION@). Installed by install.sh / install.ps1.
# No Python, no clang: everything here is the self-hosted toolchain.
set -u

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
share=$(CDPATH= cd -- "$here/../share/loment" && pwd)

to_posix() {
    case "$1" in
        [A-Za-z]:[\\/]*|\\) command -v wslpath >/dev/null 2>&1 && wslpath -a "$1" || printf '%s' "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}

# Tool name -> executable path. In the Windows package every tool is `.exe`, and
# `test -x name` only appends the suffix under MSYS/Git Bash -- under WSL or another bash
# it does not, so the same `bin/loment` falsely reports a missing component (reported
# 2026-09-15: "does not include loment-driver" while loment-driver.exe sits in bin/).
# Resolve once here so both flavours of bash work.
tool() {
    if [ -x "$here/$1" ]; then printf '%s' "$here/$1"
    elif [ -x "$here/$1.exe" ]; then printf '%s' "$here/$1.exe"
    else return 1
    fi
}

find_lomelf() {
    tool loment-lomelf
}

# ---- #773: the channel that exists BEFORE the compiler has a diagnostics sink.
#
# A failure that is not written as a record before it happens is invisible to `lomenterr`
# by construction. The launcher is the one layer that always survives: a shell reports a
# signal as 128+N, so it can name what killed the tool and synthesize a record that the
# renderer *can* read. The code is E900 (internal range, separate from every user-facing
# code) - "a tool died before it could report".
signal_name() {
    case "$1" in
        129) printf '%s' SIGHUP ;;  130) printf '%s' SIGINT ;;
        131) printf '%s' SIGQUIT ;; 132) printf '%s' SIGILL ;;
        133) printf '%s' SIGTRAP ;; 134) printf '%s' SIGABRT ;;
        135) printf '%s' SIGBUS ;;  136) printf '%s' SIGFPE ;;
        137) printf '%s' SIGKILL ;; 139) printf '%s' SIGSEGV ;;
        141) printf '%s' SIGPIPE ;; 143) printf '%s' SIGTERM ;;
        *) printf '%s' "" ;;
    esac
}

# $1 = the --diag-out file, $2 = the tool's plain stderr file, $3 = tool name,
# $4 = the exit status the shell just saw. Writes ONE JSONL record (line 0 = no position,
# docs/256) and appends a plain line for the no-renderer fallback.
synthesize_diag() {
    _sf=$1; _se=$2; _st=${3:-loment-driver}; _src=$4
    _sname=
    if [ "$_src" -gt 128 ]; then
        _sname=$(signal_name "$_src")
    fi
    if [ -n "$_sname" ]; then
        _smsg="$_st was killed by signal $((_src - 128)) ($_sname)"
    else
        _smsg="$_st exited with status $_src"
    fi
    _smsg="$_smsg and wrote no diagnostic record"
    printf '{"file":"%s","line":0,"col":0,"code":"E900","message":"%s"}\n' \
        "$_st" "$_smsg" > "$_sf" 2>/dev/null
    printf 'loment: %s [E900]\n' "$_smsg" >> "$_se" 2>/dev/null
}

# Diagnostics. The driver writes its own plain summary to stderr AND, with `--diag-out`,
# machine-readable records to a file. When the renderer `lomenterr` ships in this package we
# feed it that file and let ITS output stand (it carries the title, the source line and the
# fix - docs/182 sec 6). Without it we fall back to the driver's plain lines, and SAY SO:
# silently swapping the renderer is exactly what this contract forbids.
report_diags() {
    # $3 is every renderer switch the caller collected (`--no-color` and/or
    # `--short` / `--json`), already space-joined - see the word-splitting note below.
    # $4 is the tool's exit status and $5 its name (#773): when it failed and left no
    # record, synthesize one before rendering. That is the whole point of the second
    # channel - a crash must not look like "nothing to report".
    dfile=$1; derr=$2; rflags=${3:-}; drc=${4:-0}; dwho=${5:-loment-driver}
    if [ "$drc" -ne 0 ] && [ ! -s "$dfile" ]; then
        synthesize_diag "$dfile" "$derr" "$dwho" "$drc"
    fi
    if [ -s "$dfile" ] && tool lomenterr >/dev/null 2>&1; then
        # Word-splitting on $rflags is intentional: empty -> no argument at all, and two
        # switches -> two arguments.
        "$(tool lomenterr)" $rflags "$dfile"
        rrc=$?
        if [ $rrc -le 1 ]; then
            return 0
        fi
        # The renderer itself failed - the same boundary one level down (#773). Say it,
        # and still show the compiler's plain lines: never lose the underlying error.
        if [ $rrc -gt 128 ]; then
            rsig=$(signal_name "$rrc")
            [ -n "$rsig" ] && rsig=" ($rsig)"
            echo "loment: lomenterr died (status $rrc$rsig) while rendering $dfile [E900]" >&2
        else
            echo "loment: lomenterr failed (status $rrc) while rendering $dfile [E900]" >&2
        fi
        [ -s "$derr" ] && cat "$derr" >&2
        return 0
    fi
    [ -s "$derr" ] && cat "$derr" >&2
    [ -s "$dfile" ] && echo "loment: no lomenterr in this package - showing the compiler's plain diagnostics" >&2
    return 0
}

# NOTE: this launcher is packed as ASCII (PowerShell 5.1 reads BOM-less files as ANSI) -
# keep every comment here in English.

usage() {
    cat <<EOF
Loment @DISPLAY@  (@VERSION@)
  loment version              print version
  loment ir FILE              compile to LLVM IR on stdout
  loment check FILE [--no-color] [--short|--json] [--max N]
                              check only (diagnostics on stderr, IR discarded)
                              --short: one grep-able line per diagnostic
                              --json:  one object per diagnostic (for editors and CI)
                              --max N: render at most N (default 20; 0 = all)
  loment build FILE [-o OUT] [--link OBJ...] [--opt] [--hosted [-L DIR] [-l LIB] [--cc-arg X]]
                              compile and link; --link adds a foreign object (FFI)
                              --opt: optimize with clang -O2 when it is installed (ELF)
                              --hosted: link against the host's real libraries (docs/222)
  loment run FILE [--opt] [--hosted [-L DIR] [-l LIB] [--cc-arg X]]
                              compile, link and run
  loment fmt FILE             format (prints the formatted text)
  loment doc FILE             write API docs to stdout
  loment lsp                  language server over stdio
  loment skill [--print]      print the AI-agent guide (path, or the whole text)
  loment help [COMMAND]       all commands (the full catalog lives in loment-cli)
EOF
}

need() {
    tool "$1" >/dev/null 2>&1 || { echo "loment: this package does not include $2" >&2; exit 3; }
    return 0
}

# L0 layout files (`.lom`) are a different language from L1 source. The L1 commands used to
# accept them and answer in L1 terms (check invented E002s, fmt printed nothing) - a file the
# user cannot fix from the message they got (#57). Refuse at the door, name the language.
reject_l0() {
    case "$1" in
        *.lom|*.LOM)
            echo "loment: $2 reads L1 source (.lomt); $1 is an L0 layout file (.lom)" >&2
            echo "        L0 files are consumed by the firmware build, not by the L1 toolchain." >&2
            exit 2 ;;
    esac
    return 0
}

case "${1:-help}" in
    version|-v|--version)
        cat "$share/version" ;;
    ir|check)
        mode=$1; shift
        # The colour switch is accepted here and forwarded to the RENDERER only - the
        # driver never sees it. Position is free (before or after the file), same as
        # `loment-cli`'s own --no-color (`docs/169` has a case pinning that).
        #
        # `--short` / `--json` (docs/182 sec 15) take the same route for the same reason:
        # they are RENDERER output modes, so the driver must not see them. Without this
        # forwarding the only way to get them would be to run the compiler with
        # `--diag-out` yourself and then call lomenterr on that file - a two-step shuffle
        # that makes the modes useless for CI, which is exactly who they are for.
        # `--max N` / `--max=N` (docs/191 sec 3 #5) is the same kind of switch: it caps how
        # many diagnostics the renderer PRINTS, so the driver must not see it either. Without
        # forwarding it, `--max 0` (show everything) would be unreachable from the package -
        # and that is the one spelling a user reaches for exactly when there are 300 errors.
        src=
        nc=
        om=
        dout=
        while [ $# -gt 0 ]; do
            case "$1" in
                -C|--no-color) nc=$1; shift ;;
                --short|--json) om=$1; shift ;;
                --max) om="$om --max ${2:-}"; shift 2 ;;
                --max=*) om="$om $1"; shift ;;
                # `--diag-out PATH` is NOT a renderer switch: it names the file the DRIVER
                # writes its structured records to. `lomenterr`'s own usage tells users to
                # spell exactly this, and those JSON lines carry the byte_start/byte_end
                # that editors consume - so it has to reach the driver, and the renderer
                # then reads that file instead of the temporary one (#13).
                --diag-out) dout=${2:-}; shift 2 ;;
                --diag-out=*) dout=${1#--diag-out=}; shift ;;
                -*) echo "loment: unknown option $1" >&2; exit 2 ;;
                *) [ -z "$src" ] || {
                       echo "loment: $mode accepts exactly one input file" >&2
                       exit 2
                   }
                   src=$1; shift ;;
            esac
        done
        [ -n "$src" ] || { usage >&2; exit 2; }
        reject_l0 "$src" "$mode"
        need loment-driver loment-driver
        tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
        dpath=$tmp/d.jsonl
        if [ -n "$dout" ]; then
            dpath=$dout
            # Create it even when the unit is clean: the user asked for a file, and
            # "no file appeared" is indistinguishable from "the flag did nothing" (#13).
            : > "$dpath" || { echo "loment: cannot write the diagnostics file $dout" >&2; exit 2; }
        fi
        rc=0
        if [ "$mode" = ir ]; then
            "$(tool loment-driver)" "$(to_posix "$src")" --diag-out "$(to_posix "$dpath")" 2>"$tmp/e.txt" || rc=$?
        else
            "$(tool loment-driver)" "$(to_posix "$src")" --diag-out "$(to_posix "$dpath")" >/dev/null 2>"$tmp/e.txt" || rc=$?
        fi
        if [ $rc -ne 0 ]; then
            report_diags "$dpath" "$tmp/e.txt" "$nc $om" "$rc" "loment-driver"
            exit 1
        fi
        [ -s "$tmp/e.txt" ] && cat "$tmp/e.txt" >&2
        exit 0 ;;
    fmt)
        [ $# -eq 2 ] || { usage >&2; exit 2; }
        reject_l0 "$2" fmt
        need loment-fmt loment-fmt
        exec "$(tool loment-fmt)" "$(to_posix "$2")" ;;
    doc)
        [ $# -eq 2 ] || { usage >&2; exit 2; }
        reject_l0 "$2" doc
        need loment-doc loment-doc
        exec "$(tool loment-doc)" "$(to_posix "$2")" ;;
    lsp)
        shift; need loment-lsp loment-lsp
        exec "$(tool loment-lsp)" "$@" ;;
    # The guide for AI agents, reachable WITHOUT any tool-specific directory convention:
    # an agent that meets a new language runs its CLI first, so this is the universal hook.
    # `--print` needs no file access at all.
    skill)
        shift
        case "${1:-}" in
            --print)
                [ -f "$share/skill/SKILL.md" ] ||
                    { echo "loment: this package does not include the guide" >&2; exit 3; }
                cat "$share/skill/SKILL.md" ;;
            "")
                echo "$share/skill/SKILL.md" ;;
            *)
                echo "loment: skill takes no argument except --print" >&2; exit 2 ;;
        esac ;;
    build|run)
        mode=$1; shift
        [ $# -ge 1 ] || { usage >&2; exit 2; }
        src=$1; shift
        reject_l0 "$src" "$mode"
        out=
        out_set=
        links=()
        nc=
        om=
        opt=
        hosted=
        Largs=
        largs=
        ccargs=
        while [ $# -gt 0 ]; do
            case "$1" in
                -o|--out) out=${2:-}; out_set=1; shift 2 ;;
                # A foreign object file (docs/173 FFI): loment build app.lomt --link libfoo.o
                --link) links[${#links[@]}]="${2:-}"; shift 2 ;;
                # Optimize with clang when it is installed (docs/212 sec 5A). **Opt-in on purpose**:
                # the default path stays clang-free (`lomelf`), so the hermetic / offline build
                # never grows a dependency on an optimizer being present.
                --opt|-O2) opt=1; shift ;;
                # Hosted (docs/222): the product is allowed to have open ports to the world, so
                # it is linked by the host's C toolchain against the real libc and the real
                # libraries you name. The entry becomes `main` (the C runtime starts us) and
                # `-L` / `-l` name library directories and libraries. Sealed builds - the
                # default - keep the self-hosted lomelf path, unchanged.
                --hosted) hosted=1; shift ;;
                # `-Ldir` and `-L dir` are both accepted, exactly as the C toolchains do.
                -L) Largs="$Largs -L$(to_posix "${2:-}")"; shift 2 ;;
                -L?*) Largs="$Largs -L$(to_posix "${1#-L}")"; shift ;;
                -l) largs="$largs -l${2:-}"; shift 2 ;;
                -l?*) largs="$largs -l${1#-l}"; shift ;;
                # `--cc-arg X` hands X straight to the C toolchain. A real library brings its
                # own link flags (`-Wl,-rpath,...` for libjvm, `-pthread`, ...) and this
                # launcher must not grow a flag of its own for each of them. `--cc-arg=` is
                # the one-word spelling.
                --cc-arg) ccargs="$ccargs ${2:-}"; shift 2 ;;
                --cc-arg=*) ccargs="$ccargs ${1#--cc-arg=}"; shift ;;
                -C|--no-color) nc=$1; shift ;;
                --short|--json) om=$1; shift ;;
                --max) om="$om --max ${2:-}"; shift 2 ;;
                --max=*) om="$om $1"; shift ;;
                *) echo "loment: unknown option $1" >&2; exit 2 ;;
            esac
        done
        # `-o` on `run` used to be parsed and then overwritten by the temporary path, so
        # the user got a successful exit and no artifact (#636). Accepting a flag and
        # dropping it is the silent case the launcher forbids everywhere else - refuse it
        # by name instead of inventing a second meaning for `run`.
        if [ "$mode" = run ] && [ -n "$out_set" ]; then
            echo "loment: -o is for build; run produces no artifact" >&2
            exit 2
        fi
        need loment-driver loment-driver
        need loment-lomelf loment-lomelf
        # ---- deps/*/link.txt (docs/282 sec 5.4/6.1): the only build-time discovery point.
        #
        # The source-level surface stays `use <name>`; there is no --java-lib. Every
        # `ir NAME SHA256` line is verified here - a missing file or a changed hash is a
        # named error, never a silent link of something else. `runtime jrt VER` adds
        # share/loment/jrt/VER/jrt.ll once per closure.
        #
        # The merge is plain concatenation. The linker drops a `declare` that its own
        # `define` satisfies (`ext_drop_defined` in loment/tools/lomelf.lomt), so no IR
        # text is rewritten here: two launchers parsing IR would be two chances to
        # corrupt it, and IR strings may contain shell/batch metacharacters.
        deps_ir=()
        deps_rt=()
        if [ -d deps ]; then
            if ls deps/*/link.txt >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
                echo "loment: cannot verify deps/*/link.txt: sha256sum is not on PATH" >&2
                exit 1
            fi
            for lf in deps/*/link.txt; do
                [ -f "$lf" ] || continue
                ldir=${lf%/link.txt}
                while IFS= read -r line || [ -n "$line" ]; do
                    line=${line%$'\r'}
                    line=${line#"${line%%[![:space:]]*}"}
                    case "$line" in
                        ''|'#'*) continue ;;
                    esac
                    kind=${line%%[[:space:]]*}
                    rest=${line#"$kind"}
                    rest=${rest#"${rest%%[![:space:]]*}"}
                    case "$kind" in
                        ir)
                            name=${rest%%[[:space:]]*}
                            sha=${rest#"$name"}
                            sha=${sha#"${sha%%[![:space:]]*}"}
                            extra=
                            case "$sha" in *[[:space:]]*) extra=1 ;; esac
                            if [ -z "$name" ] || [ -z "$sha" ] || [ -n "$extra" ]; then
                                echo "loment: $lf: want 'ir NAME SHA256', got '$line'" >&2
                                exit 1
                            fi
                            p="$ldir/$name"
                            if [ ! -f "$p" ]; then
                                echo "loment: $lf lists $name, but $p does not exist" >&2
                                echo "        (reinstall the dependency, or remove $ldir)" >&2
                                exit 1
                            fi
                            got=$(sha256sum "$p" 2>/dev/null | cut -d' ' -f1)
                            if [ "$got" != "$sha" ]; then
                                echo "loment: $lf lists $name with sha256 $sha" >&2
                                echo "        but the file on disk hashes to ${got:-<unreadable>}" >&2
                                exit 1
                            fi
                            deps_ir[${#deps_ir[@]}]=$(to_posix "$p")
                            ;;
                        runtime)
                            rname=${rest%%[[:space:]]*}
                            ver=${rest#"$rname"}
                            ver=${ver#"${ver%%[![:space:]]*}"}
                            if [ "$rname" != "jrt" ] || [ -z "$ver" ] \
                               || [ "${ver%%[[:space:]]*}" != "$ver" ]; then
                                echo "loment: $lf: want 'runtime jrt VERSION', got '$line'" >&2
                                exit 1
                            fi
                            seen=
                            if [ ${#deps_rt[@]} -gt 0 ]; then
                                for v in "${deps_rt[@]}"; do [ "$v" = "$ver" ] && seen=1; done
                            fi
                            [ -n "$seen" ] && continue
                            deps_rt[${#deps_rt[@]}]="$ver"
                            ;;
                        *)
                            echo "loment: $lf: unknown line '$line' (want ir / runtime / #)" >&2
                            exit 1
                            ;;
                    esac
                done < "$lf"
            done
        fi
        if [ ${#deps_rt[@]} -gt 0 ]; then
            for v in "${deps_rt[@]}"; do
                rt="$share/jrt/$v/jrt.ll"
                if [ ! -f "$rt" ]; then
                    echo "loment: a dependency asks for runtime jrt $v, but this package has no" >&2
                    echo "        $rt (the runtime ships with the toolchain, docs/282 sec 5.4)" >&2
                    exit 1
                fi
                deps_ir[${#deps_ir[@]}]=$rt
            done
        fi
        tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
        rc=0
        lrc=0
        "$(tool loment-driver)" "$(to_posix "$src")" --diag-out "$tmp/d.jsonl" > "$tmp/a.ll" 2>"$tmp/e.txt" || rc=$?
        if [ $rc -ne 0 ]; then
            report_diags "$tmp/d.jsonl" "$tmp/e.txt" "$nc $om" "$rc" "loment-driver"
            exit 1
        fi
        # ---- merge deps IR (if any). Plain concatenation; see the note where the
        # link.txt files are scanned. A symbol defined by two inputs is a named error:
        # the linker keeps one label per name, so a duplicate would silently win/lose.
        link_ir="$tmp/a.ll"
        if [ ${#deps_ir[@]} -gt 0 ]; then
            : > "$tmp/defs"
            sed -n 's/^define[^@]*@\([A-Za-z0-9_.$-]*\)(.*/\1/p' "$tmp/a.ll" >> "$tmp/defs"
            for f in "${deps_ir[@]}"; do
                sed -n 's/^define[^@]*@\([A-Za-z0-9_.$-]*\)(.*/\1/p' "$f" >> "$tmp/defs"
            done
            dups=$(sort "$tmp/defs" | uniq -d)
            if [ -n "$dups" ]; then
                echo "loment: two inputs define the same symbol:" >&2
                echo "$dups" | sed 's/^/        /' >&2
                echo "        (a library IR and the app share a name; docs/282 sec 6)" >&2
                exit 1
            fi
            cat "$tmp/a.ll" > "$tmp/merged.ll"
            printf '\n' >> "$tmp/merged.ll"
            for f in "${deps_ir[@]}"; do
                cat "$f" >> "$tmp/merged.ll"
                printf '\n' >> "$tmp/merged.ll"
            done
            link_ir="$tmp/merged.ll"
        fi
        if [ "$mode" = run ]; then out="$tmp/a.bin"; fi
        if [ -z "$out" ]; then
            # docs/223: a unit that registers a command (a `loment_command()` declaration)
            # names its own artifact -- `loment-<name>`, which is exactly what the launcher's
            # git-style lookup looks for (`loment foo` -> `loment-foo` on PATH). The name is
            # asked out of the driver, which is the side that reads the declaration; keeping
            # the rule here instead would be a second copy of it.
            #
            # One extra driver load per `build` with no `-o`. That is the price of naming the
            # artifact from the source; `-o` skips the question entirely.
            cname=$("$(tool loment-driver)" "$(to_posix "$src")" --print-command 2>/dev/null) || cname=
            case "$cname" in
                "") out="${src%.lomt}" ;;
                *)  out="loment-$cname" ;;
            esac
        fi
        # Default: link with the self-hosted lomelf - the package needs no clang.
        # `--opt`: hand the IR to clang -O2 instead (docs/212 sec 5A) - the same optimizer C and
        # Rust use. It **degrades loudly**: no clang => say so and fall back, never fail.
        # `choose hosted` in the source is the SAME claim as the `--hosted` flag - the unit says
        # so in the IR header (docs/222 sec 4.4), so the declaration alone is enough and the
        # two spellings cannot silently disagree.
        if [ -z "$hosted" ] && grep -q '^; loment-port: hosted' "$tmp/a.ll" 2>/dev/null; then
            hosted=1
        fi
        # ---- port door (@CHANNEL@, docs/222 sec 4): **open in both channels since 2026-10-17**
        # (#406). Both halves of LibC are done - static (docs/183 V1, real libz.a) and run-time
        # (V2, the system libz.so) - so a promise of "real libraries" is now keepable and there
        # is nothing left to lock. The linker still refuses a ported object by default (it does
        # not know the channel); this flag is what opens it, and it is passed in both channels
        # so that which build you run no longer decides whether a real library can be linked.
        portarg=--allow-ports
        # Naming a host library is a claim that this product HAS ports to the world. In a
        # sealed build (the default) that claim cannot be honoured - lomelf has no libc - so
        # say it instead of silently dropping the flags (docs/222 sec 4.1).
        if [ -z "$hosted" ] && { [ -n "$Largs" ] || [ -n "$largs" ] || [ -n "$ccargs" ]; }; then
            echo "loment: -L / -l / --cc-arg need --hosted: a sealed product links no host library (docs/222)" >&2
            exit 2
        fi
        # Keep every `--link` value in its own argument slot. A concatenated string is
        # expanded unquoted below, so `--link "/tmp/my libs/add.o"` used to reach the
        # linker as `--link /tmp/my` + `libs/add.o` (#635).
        linkargs=()
        objs=()
        for l in "${links[@]}"; do
            linkargs[${#linkargs[@]}]=--link
            linkargs[${#linkargs[@]}]=$(to_posix "$l")
            objs[${#objs[@]}]=$(to_posix "$l")
        done
        if [ -n "$hosted" ]; then
            # ---- hosted (docs/222 sec 4): a C-runtime product with open ports to the world.
            # The entry must be `main`, not `_start`: the host C runtime calls it, which is
            # what gives us TLS/stdio/atexit init. Keeping `_start` here would bypass that
            # init AND lose buffered output on a raw exit - a silent wrong, so refuse.
            if ! grep -qE '^define .*@main\(' "$tmp/a.ll"; then
                echo "loment: --hosted expects \`fn main() -> u32\` as the entry, but this unit defines no @main." >&2
                echo "        Hosted products start under the C runtime; sealed ones start at _start (docs/222 sec 4.3)." >&2
                exit 1
            fi
            cc=$(command -v clang 2>/dev/null || command -v cc 2>/dev/null || true)
            if [ -z "$cc" ]; then
                echo "loment: --hosted needs a C toolchain (clang or cc) to link against the host's libraries" >&2
                exit 1
            fi
            # shellcheck disable=SC2086
            "$cc" "$link_ir" "${objs[@]}" $ccargs $Largs $largs -o "$out" || exit 1
        elif [ -n "$opt" ]; then
            cc=$(command -v clang 2>/dev/null || true)
            if [ -n "$cc" ]; then
                # -O2 rewrites byte loops into memset/memcpy/memmove calls, and this runtime has
                # no libc - so the link step supplies those three (docs/212 sec 5A). Same text
                # the packaging step uses; see _FREESTANDING_LIBCALLS in tools/loment_dist.py.
                cat > "$tmp/libcalls.ll" <<'LOMENT_LIBCALLS_EOF'
@LIBCALLS@
LOMENT_LIBCALLS_EOF
                # shellcheck disable=SC2086
                "$cc" --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static \
                    -fno-pie -O2 "$link_ir" "$tmp/libcalls.ll" "${objs[@]}" -o "$out" || exit 1
            else
                echo "loment: --opt: no clang found, falling back to the unoptimized lomelf backend" >&2
                "$(tool loment-lomelf)" "$link_ir" "$out" --diag-out "$tmp/d.jsonl" "${linkargs[@]}" $portarg 2>"$tmp/e2.txt" || lrc=$?
            fi
        elif [ ${#links[@]} -gt 0 ]; then
            "$(tool loment-lomelf)" "$link_ir" "$out" --diag-out "$tmp/d.jsonl" "${linkargs[@]}" $portarg 2>"$tmp/e2.txt" || lrc=$?
        else
            "$(tool loment-lomelf)" "$link_ir" "$out" --diag-out "$tmp/d.jsonl" 2>"$tmp/e2.txt" || lrc=$?
        fi
        # The linker gets its OWN --diag-out, into the same file: when it can put a code on
        # (the `-o` directory not existing is the common one) it writes a record there and
        # report_diags renders it with the title, the why and the fixes; when it writes no
        # record its plain line is shown as is, never silently lost. Reusing the driver's
        # file is safe - on success the driver wrote nothing into it.
        if [ $lrc -ne 0 ]; then
            report_diags "$tmp/d.jsonl" "$tmp/e2.txt" "$nc $om" "$lrc" "loment-lomelf"
            exit 1
        fi
        if [ "$mode" = run ]; then
            chmod 755 "$out"
            "$out"
        else
            echo "loment: $out"
        fi ;;
    help|-h|--help)
        # The full catalog (and the per-command pages) live in loment-cli - one implementation,
        # both launchers forward. **The rest of the argv goes with it**: `loment help build`
        # must reach the per-command page, not just the catalog. Dropping it silently made
        # `loment help [COMMAND]` (advertised right in the usage text) a no-op.
        if cli=$(tool loment-cli); then shift; exec "$cli" help "$@"; fi
        usage ;;
    *)
        # A USER command: `loment foo` -> `loment-foo` on PATH, exactly `git foo` -> `git-foo`.
        # This is how software *written in Loment* registers a command: build it as
        # `loment-foo`, put it on PATH, done - nothing to declare and no rebuild of Loment.
        # The builtins above win, so a user command can NEVER shadow version/build/run/...
        ucmd="${1:-}"
        if [ -n "$ucmd" ] && command -v "loment-$ucmd" >/dev/null 2>&1; then
            shift
            exec "loment-$ucmd" "$@"
        fi
        # New OFFICIAL commands go into loment-cli (loment/tools/lomcli.lomt), NOT into this
        # shell: there are two launchers (this one and loment.cmd) and anything written here has
        # to be written twice and kept in sync. Forwarding keeps a single implementation.
        if cli=$(tool loment-cli); then exec "$cli" "$@"; fi
        usage >&2; exit 2 ;;
esac
'''

#: Windows 的原生启动器。**直接调本机的 .exe，不再往 WSL 转发** —— 包里的工具本来就是 PE。
#: 必须 ASCII（PowerShell 5.1 按 ANSI 读无 BOM 的脚本），所以注释一律英文。
LAUNCHER_CMD = r'''@echo off
rem Loment @DISPLAY@ launcher (Windows, native toolchain).
setlocal
set "here=%~dp0"
set "share=%here%..\share\loment"
set "cmd=%~1"
if "%cmd%"=="" goto usage
if "%cmd%"=="help" goto help
if "%cmd%"=="-h" goto help
if "%cmd%"=="--help" goto help
if "%cmd%"=="version" goto version
if "%cmd%"=="-v" goto version
if "%cmd%"=="--version" goto version
if "%cmd%"=="ir" goto ir
if "%cmd%"=="check" goto check
if "%cmd%"=="fmt" goto fmt
if "%cmd%"=="doc" goto doc
if "%cmd%"=="lsp" goto lsp
if "%cmd%"=="skill" goto skill
if "%cmd%"=="build" goto build
if "%cmd%"=="run" goto run
rem Everything else goes to loment-cli: the command surface lives there (loment/tools/lomcli.lomt),
rem not here - there are two launchers and anything written in both has to be kept in sync.
goto forward

:version
type "%share%\version"
exit /b 0

:ir
if "%~2"=="" goto usage
set "cmode=i"
goto compile_only

:check
if "%~2"=="" goto usage
set "cmode=c"

:compile_only
rem Shared by ir and check: compile, and on failure hand the structured diagnostics (and
rem the driver's own plain output) to :report_diags. NOTE: no parenthesised blocks around
rem anything that reads %ERRORLEVEL% -- it expands at parse time there.
rem
rem The file and the renderer switches are picked out of the whole command line, so that
rem `check FILE --no-color` and `check --no-color FILE` both work -- the bash launcher
rem accepts both too. %~2 alone would break the second spelling.
set "csrc="
set "cnc="
set "com="
set "cbad="
set "cdout="
for %%A in (%*) do call :scan_arg "%%~A"
rem :scan_arg runs inside a `for ... do call`, so it cannot end the script itself: it
rem records the bad option and this is where the run stops with the same status bash uses.
if defined cbad exit /b 2
if not defined csrc goto usage
set "dtmp=%TEMP%\loment-d%RANDOM%%RANDOM%"
mkdir "%dtmp%" >nul 2>nul
rem `--diag-out PATH`: the driver writes THERE instead of the temp file, and the file is
rem created even when the unit is clean (issue #13: "no file" looked like "flag ignored").
set "dpath=%dtmp%\d.jsonl"
if defined cdout set "dpath=%cdout%"
if defined cdout type nul > "%dpath%"
if "%cmode%"=="i" goto compile_only_ir
"%here%loment-driver.exe" "%csrc%" --diag-out "%dpath%" >nul 2>"%dtmp%\e.txt"
goto compile_only_done
:compile_only_ir
"%here%loment-driver.exe" "%csrc%" --diag-out "%dpath%" 2>"%dtmp%\e.txt"
:compile_only_done
set "crc=%ERRORLEVEL%"
if not "%crc%"=="0" call :report_diags "%dpath%" "%dtmp%\e.txt" "%cnc% %com%" "%crc%" "loment-driver"
if "%crc%"=="0" if exist "%dtmp%\e.txt" type "%dtmp%\e.txt" 1>&2
if not defined cdout del /q "%dtmp%\d.jsonl" >nul 2>nul
del /q "%dtmp%\e.txt" >nul 2>nul
rmdir "%dtmp%" >nul 2>nul
exit /b %crc%

:fmt
if "%~2"=="" goto usage
"%here%loment-fmt.exe" "%~2"
exit /b %ERRORLEVEL%

:doc
if "%~2"=="" goto usage
"%here%loment-doc.exe" "%~2"
exit /b %ERRORLEVEL%

:lsp
"%here%loment-lsp.exe" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

rem The guide for AI agents, reachable WITHOUT any tool-specific directory convention: an
rem agent that meets a new language runs its CLI first, so this is the universal hook.
rem --print needs no file access at all.
:skill
if "%~2"=="--print" goto skill_print
rem %%~fI expands to the fully-qualified path (drops the .. in %share%)
for %%I in ("%share%\skill\SKILL.md") do echo %%~fI
exit /b 0

:skill_print
if not exist "%share%\skill\SKILL.md" (
  echo loment: this package does not include the guide 1>&2
  exit /b 3
)
type "%share%\skill\SKILL.md"
exit /b %ERRORLEVEL%

:build
set "bmode=b"
goto barg_begin

:run
set "bmode=r"

:barg_begin
rem Shared argument parsing for build and run. The bash launcher does the same with a
rem `case`; keep the two in step (loment_cli_test checks the command sets match).
rem NOTE: no parenthesised blocks -- `%ERRORLEVEL%` inside one expands at parse time.
set "src=%~2"
if "%src%"=="" goto usage
set "out="
rem Port door (@CHANNEL@, docs/222 sec 4): open in both channels since 2026-10-17 (#406) -
rem LibC's static and run-time halves are both done. lomelf still refuses a ported object by
rem default; this flag is what opens it, and it is passed in both channels.
set "portarg=--allow-ports"
set "cnc="
set "com="
shift
shift
:barg_loop
if "%~1"=="" goto barg_done
if /I "%~1"=="-o" goto barg_out
if /I "%~1"=="--out" goto barg_out
if /I "%~1"=="--link" goto barg_link
if /I "%~1"=="--opt" goto barg_opt
if /I "%~1"=="-O2" goto barg_opt
if /I "%~1"=="--hosted" goto barg_hosted
if /I "%~1"=="-L" goto barg_hosted
if /I "%~1"=="-l" goto barg_hosted
set "bcs=%~1"
if "%bcs:~0,9%"=="--cc-arg=" goto barg_hosted
if /I "%~1"=="-C" goto barg_nc
if /I "%~1"=="--no-color" goto barg_nc
if /I "%~1"=="--short" goto barg_om
if /I "%~1"=="--json" goto barg_om
if /I "%~1"=="--max" goto barg_max
set "bs=%~1"
if "%bs:~0,6%"=="--max=" goto barg_max_eq
rem Unknown options are REJECTED, not ignored: silently dropping `--link` used to end in
rem "undefined label: c_add" from the linker, which points the user at the wrong thing.
echo loment: unknown option %~1 1>&2
exit /b 2
:barg_max
set "com=%com% --max %~2"
shift
shift
goto barg_loop
:barg_max_eq
set "com=%com% %~1"
shift
goto barg_loop
:barg_nc
set "cnc=%~1"
shift
goto barg_loop
:barg_om
set "com=%~1"
shift
goto barg_loop
:barg_out
rem `-o` on `run` used to be parsed and never used: the run path hardcodes its temporary
rem output, so the user got exit 0 and no artifact (#636). Refuse it by name.
if "%bmode%"=="r" goto run_out_refused
set "out=%~2"
shift
shift
goto barg_loop
:barg_link
rem The PE branch of lomelf refuses every foreign object, so `--link` can never succeed
rem here; advertising it only sends users into that refusal (#632). Refuse it here, by
rem name, with the reason the linker cannot: this launcher produces PE.
echo loment: --link needs the ELF target (foreign objects are ELF only); this launcher builds PE 1>&2
exit /b 2
:run_out_refused
echo loment: -o is for build; run produces no artifact 1>&2
exit /b 2
:barg_opt
rem The clang-optimized path (docs/212 sec 5A) is implemented for ELF only; this launcher links
rem with the self-hosted lomelf. Refused **by name** rather than silently ignored.
echo loment: --opt needs clang and is ELF-only for now; this launcher links with lomelf 1>&2
exit /b 2
:barg_hosted
rem Hosted linking (docs/222 sec 4) needs a PE-side path that does not exist yet: this launcher
rem has no import-table handling and no C toolchain wiring. Refused **by name** - silently
rem ignoring --hosted would link with lomelf and report "undefined label: compress", which
rem points the user at the wrong problem entirely.
echo loment: --hosted is not available on Windows yet (ELF only for now; docs/222 sec 7) 1>&2
exit /b 2
:barg_done
rem deps\*\link.txt (docs/282 sec 5.4/6.1): the only build-time discovery point. Each
rem `ir NAME SHA256` line is verified; a missing file or a changed hash is a named error.
rem The merge is plain concatenation - the linker drops a `declare` that its own `define`
rem satisfies, so no IR text is parsed (and possibly mangled) by a batch loop.
call :scan_deps
if defined derror goto depfail
if "%bmode%"=="r" goto run_go

rem docs/223: a unit that registers a command names its own artifact (`loment-<name>`),
rem which is what the launcher's git-style lookup looks for. Ask the driver -- it reads the
rem declaration. No answer (not a command) => the old rule, the source file's own name.
if not "%out%"=="" goto have_out
set "cname="
for /f "delims=" %%A in ('"%here%loment-driver.exe" "%src%" --print-command 2^>nul') do if not defined cname set "cname=%%A"
if not defined cname goto no_cname
set "out=loment-%cname%"
goto have_out
:no_cname
set "out=%src:.lomt=%"
:have_out
set "tmp=%TEMP%\loment-b%RANDOM%%RANDOM%"
mkdir "%tmp%" >nul 2>nul
"%here%loment-driver.exe" "%src%" --diag-out "%tmp%\d.jsonl" > "%tmp%\a.ll" 2>"%tmp%\e.txt"
set "drc=%ERRORLEVEL%"
if not "%drc%"=="0" call :report_diags "%tmp%\d.jsonl" "%tmp%\e.txt" "%cnc% %com%" "%drc%" "loment-driver"
if not "%drc%"=="0" goto fail
rem `choose hosted` (docs/222 sec 4.4): the bash launcher reads this header marker and routes
rem the unit to the host C toolchain. This launcher has no hosted path, and without the check
rem the PE linker reports "the unit has no _start" -- the wrong problem entirely (#633).
findstr /b /l /c:"; loment-port: hosted" "%tmp%\a.ll" >nul 2>nul
if "%ERRORLEVEL%"=="0" goto hosted_unit
call :make_merged
if defined derror goto fail
rem The linker gets its OWN --diag-out, into the same file (on success the driver wrote
rem nothing into it). When it can put a code on - the `-o` directory not existing is the
rem common one - :report_diags renders it with the title, the why and the fixes; when it
rem writes no record its plain line is shown as is, never silently lost.
"%here%loment-lomelf.exe" "%linkll%" "%out%.exe" --diag-out "%tmp%\d.jsonl" %portarg% 2>"%tmp%\e2.txt"
set "lrc=%ERRORLEVEL%"
if not "%lrc%"=="0" call :report_diags "%tmp%\d.jsonl" "%tmp%\e2.txt" "%cnc% %com%" "%lrc%" "loment-lomelf"
if not "%lrc%"=="0" goto fail
goto done

:run_go
set "tmp=%TEMP%\loment-r%RANDOM%%RANDOM%"
mkdir "%tmp%" >nul 2>nul
"%here%loment-driver.exe" "%src%" --diag-out "%tmp%\d.jsonl" > "%tmp%\a.ll" 2>"%tmp%\e.txt"
set "drc=%ERRORLEVEL%"
if not "%drc%"=="0" call :report_diags "%tmp%\d.jsonl" "%tmp%\e.txt" "%cnc% %com%" "%drc%" "loment-driver"
if not "%drc%"=="0" goto fail
rem Same marker check as the build path: a hosted unit is not a PE product (#633).
findstr /b /l /c:"; loment-port: hosted" "%tmp%\a.ll" >nul 2>nul
if "%ERRORLEVEL%"=="0" goto hosted_unit
call :make_merged
if defined derror goto fail
"%here%loment-lomelf.exe" "%linkll%" "%tmp%\a.exe" --diag-out "%tmp%\d.jsonl" %portarg% 2>"%tmp%\e2.txt"
set "lrc=%ERRORLEVEL%"
if not "%lrc%"=="0" call :report_diags "%tmp%\d.jsonl" "%tmp%\e2.txt" "%cnc% %com%" "%lrc%" "loment-lomelf"
if not "%lrc%"=="0" goto fail
"%tmp%\a.exe"
set "rc=%ERRORLEVEL%"
del /q "%tmp%\a.ll" "%tmp%\a.exe" "%tmp%\merged.ll" "%tmp%\defs.txt" "%tmp%\defs.sorted" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
exit /b %rc%

:done
del /q "%tmp%\a.ll" "%tmp%\merged.ll" "%tmp%\defs.txt" "%tmp%\defs.sorted" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
echo loment: %out%.exe
exit /b 0

rem ---- deps\*\link.txt scanning (docs/282 sec 5.4/6.1). Reached once per build/run, from
rem :barg_done. Numbered variables (ir0, ir1, ...) instead of a list: batch has no arrays,
rem and a space-joined list would break on a path with a space.
:scan_deps
set "dn=0"
set "rts=;"
set "derror="
if not exist deps exit /b 0
for /d %%D in (deps\*) do if exist "%%D\link.txt" call :scan_link "%%D"
exit /b 0

:scan_link
if defined derror exit /b 0
set "ldir=%~1"
set "lf=%~1\link.txt"
for /f "usebackq tokens=1,2,3,*" %%K in ("%lf%") do call :link_line "%%K" "%%L" "%%M" "%%N"
exit /b 0

:link_line
if defined derror exit /b 0
set "lk=%~1"
if "%lk%"=="" exit /b 0
if "%lk:~0,1%"=="#" exit /b 0
if /I "%lk%"=="ir" goto link_ir
if /I "%lk%"=="runtime" goto link_rt
echo loment: %lf%: unknown line "%lk% %~2 %~3" (want ir / runtime / # comment) 1>&2
set "derror=1"
exit /b 0

:link_ir
set "lname=%~2"
set "lsha=%~3"
set "lextra=%~4"
if "%lname%"=="" goto link_ir_bad
if "%lsha%"=="" goto link_ir_bad
if not "%lextra%"=="" goto link_ir_bad
set "lp=%ldir%\%lname%"
if not exist "%lp%" goto link_ir_missing
call :sha_of "%lp%"
if /I not "%sha%"=="%lsha%" goto link_ir_hash
set "ir%dn%=%lp%"
set /a dn+=1
exit /b 0
:link_ir_missing
echo loment: %lf% lists %lname%, but %lp% does not exist 1>&2
echo         (reinstall the dependency, or remove %ldir%) 1>&2
set "derror=1"
exit /b 0
:link_ir_hash
echo loment: %lf% lists %lname% with sha256 %lsha% 1>&2
echo         but the file on disk hashes to %sha% 1>&2
set "derror=1"
exit /b 0
:link_ir_bad
echo loment: %lf%: want 'ir NAME SHA256', got "%lk% %~2 %~3 %~4" 1>&2
set "derror=1"
exit /b 0

:link_rt
if /I not "%~2"=="jrt" goto link_rt_bad
set "lver=%~3"
if "%lver%"=="" goto link_rt_bad
if not "%~4"=="" goto link_rt_bad
rem Once per closure. `call set` evaluates %rts% a second time without delayed expansion.
call set "rtchk=%%rts:;%lver%;=%%"
if not "%rtchk%"=="%rts%" exit /b 0
set "lrt=%share%\jrt\%lver%\jrt.ll"
if not exist "%lrt%" goto link_rt_missing
set "rts=%rts%%lver%;"
set "ir%dn%=%lrt%"
set /a dn+=1
exit /b 0
:link_rt_missing
echo loment: a dependency asks for runtime jrt %lver%, but this package has no 1>&2
echo         %lrt% (the runtime ships with the toolchain, docs/282 sec 5.4) 1>&2
set "derror=1"
exit /b 0
:link_rt_bad
echo loment: %lf%: want 'runtime jrt VERSION', got "%~1 %~2 %~3" 1>&2
set "derror=1"
exit /b 0

:sha_of
set "sha="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do if not defined sha set "sha=%%H"
if defined sha set "sha=%sha: =%"
exit /b 0

rem Concatenate the app IR with every listed IR. A symbol defined twice is a named error
rem (the linker keeps one label per name, so a duplicate would silently win or lose).
:make_merged
set "linkll=%tmp%\a.ll"
if "%dn%"=="0" exit /b 0
type nul > "%tmp%\defs.txt"
call :collect_defs "%tmp%\a.ll"
set "i=0"
:cm_loop
if %i% geq %dn% goto cm_done
call set "cp=%%ir%i%%%"
call :collect_defs "%cp%"
set /a i+=1
goto cm_loop
:cm_done
sort "%tmp%\defs.txt" /o "%tmp%\defs.sorted" >nul 2>nul
set "prev="
for /f "usebackq delims=" %%L in ("%tmp%\defs.sorted") do call :dup_check "%%L"
if defined derror exit /b 0
copy /b /y "%tmp%\a.ll" "%tmp%\merged.ll" >nul
echo.>> "%tmp%\merged.ll"
set "i=0"
:mm_loop
if %i% geq %dn% goto mm_done
call set "cp=%%ir%i%%%"
copy /b /y "%tmp%\merged.ll"+"%cp%" "%tmp%\merged2.ll" >nul
move /y "%tmp%\merged2.ll" "%tmp%\merged.ll" >nul
echo.>> "%tmp%\merged.ll"
set /a i+=1
goto mm_loop
:mm_done
set "linkll=%tmp%\merged.ll"
exit /b 0

:collect_defs
for /f "usebackq tokens=2 delims=@" %%S in (`findstr /b /l /c:"define " "%~1"`) do call :def_sym "%%S"
exit /b 0
:def_sym
for /f "tokens=1 delims=(" %%T in ("%~1") do echo %%T>>"%tmp%\defs.txt"
exit /b 0
:dup_check
if defined derror exit /b 0
if /I "%~1"=="%prev%" goto dup_found
set "prev=%~1"
exit /b 0
:dup_found
echo loment: two inputs define the same symbol: %~1 1>&2
echo         (a library IR and the app share a name; docs/282 sec 6) 1>&2
set "derror=1"
exit /b 0

:depfail
echo loment: the deps IR is not usable -- nothing was produced 1>&2
exit /b 1

rem A unit that declared `choose hosted` in its IR header (#633). Reached from both the
rem build and the run path; the message names the limitation instead of letting the PE
rem linker talk about `_start`, which is not what the user wrote.
:hosted_unit
echo loment: this unit declares choose hosted; hosted products need the ELF target (docs/222 sec 4) 1>&2
echo         This launcher builds PE and has no hosted/import path yet. 1>&2
goto fail

rem NOTE: tool failures are tested with a STRING compare, never `if errorlevel 1`.
rem A crashed tool exits with a NEGATIVE code (0xC000001D = -1073741795) and cmd compares
rem that as signed -- so `if errorlevel 1` reads it as success, we fall through to :done
rem and print "loment: <out>.exe" while nothing was written. Proven 2026-09-15:
rem `loment build tour.lomt -o tour` exited 0 with no tour.exe on disk.
:fail
del /q "%tmp%\a.ll" "%tmp%\a.exe" "%tmp%\d.jsonl" "%tmp%\e.txt" "%tmp%\e2.txt" "%tmp%\merged.ll" "%tmp%\defs.txt" "%tmp%\defs.sorted" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
echo loment: failed -- nothing was produced 1>&2
exit /b 1

:help
rem Forward the whole command line, not just `help`: `loment help build` must reach
rem loment-cli's per-command page (the usage text advertises `loment help [COMMAND]`).
if not exist "%here%loment-cli.exe" goto usage
"%here%loment-cli.exe" %*
exit /b %ERRORLEVEL%

:forward
rem A USER command: `loment foo a b` -> `loment-foo a b` on PATH, exactly `git foo` -> `git-foo`.
rem This is how software *written in Loment* registers a command: build it as loment-foo.exe,
rem put it on PATH, done. The builtins above win, so a user command can never shadow them.
rem NOTE: the run must NOT sit inside a parenthesised block -- %ERRORLEVEL% there expands at
rem parse time and would read the PREVIOUS value (the same trap the tool-failure note below
rem describes). So: pick the path, leave the block, then run.
set "uargs="
for /f "tokens=1,*" %%A in ("%*") do set "uargs=%%B"
set "ucmd="
for /f "delims=" %%P in ('where "loment-%cmd%" 2^>nul') do if not defined ucmd set "ucmd=%%P"
if not defined ucmd goto loment_forward_cli
"%ucmd%" %uargs%
exit /b %ERRORLEVEL%

:loment_forward_cli
if not exist "%here%loment-cli.exe" goto usage
"%here%loment-cli.exe" %*
exit /b %ERRORLEVEL%

:usage
echo Loment @DISPLAY@  (@VERSION@)
echo   loment version              print version
echo   loment ir FILE              compile to LLVM IR on stdout
echo   loment check FILE [--no-color] [--short^|--json] [--max N]
echo                               check only (diagnostics on stderr, IR discarded)
echo                               --short: one grep-able line per diagnostic
echo                               --json:  one object per diagnostic (for editors and CI)
echo                               --max N: render at most N (default 20; 0 = all)
echo   loment build FILE [-o OUT] [--opt] [--hosted]
echo                               compile and link
echo                               --opt: optimize with clang -O2 (ELF only)
echo                               --hosted: link the host's real libraries (ELF only)
echo   loment run FILE [--opt] [--hosted]
echo                               compile, link and run
echo   loment fmt FILE             format (prints the formatted text)
echo   loment doc FILE             write API docs to stdout
echo   loment lsp                  language server over stdio
echo   loment skill [--print]      print the AI-agent guide (path, or the whole text)
echo   loment help [COMMAND]       all commands (the full catalog lives in loment-cli)
exit /b 2

rem ---------------------------------------------------------------- diagnostics renderer
rem Reached only via `call`; never fallen into (every label above exits).
rem %1 = the --diag-out JSONL, %2 = the driver's captured stderr, %3 = renderer flags,
rem %4 = the tool's exit status, %5 = the tool's name.
rem
rem #773: the channel that exists BEFORE the compiler has a diagnostics sink. A tool that
rem failed and left no record is synthesized here as one E900 line (line 0 = no position,
rem docs/256) so a crash cannot look like "nothing to report". Then, as before, the driver's
rem plain summary is used only when the renderer is absent -- and we SAY so.
rem
rem NTSTATUS values are negative in cmd; compare them as STRINGS (the same reason the
rem tool-failure checks below never use `if errorlevel 1`).
:report_diags
set "rf=0"
if exist "%~1" for %%A in ("%~1") do if %%~zA GTR 0 set "rf=1"
if "%rf%"=="1" goto report_have
if "%~4"=="0" goto report_have
if "%~4"=="" goto report_have
call :synthesize_diag "%~1" "%~2" "%~5" "%~4"
:report_have
set "rf=0"
if exist "%~1" for %%A in ("%~1") do if %%~zA GTR 0 set "rf=1"
if "%rf%"=="1" if exist "%here%lomenterr.exe" goto report_render
if exist "%~2" type "%~2" 1>&2
if "%rf%"=="1" echo loment: no lomenterr in this package - showing the compiler's plain diagnostics 1>&2
exit /b 0

:report_render
if "%~3"=="" goto report_render_plain
"%here%lomenterr.exe" %~3 "%~1"
goto report_render_done
:report_render_plain
"%here%lomenterr.exe" "%~1"
:report_render_done
set "rrc=%ERRORLEVEL%"
if "%rrc%"=="0" exit /b 0
if "%rrc%"=="1" exit /b 0
rem The renderer itself failed -- the same boundary one level down (#773). Say it, and
rem still show the plain lines: never lose the underlying error.
echo loment: lomenterr failed (status %rrc%) while rendering %~1 [E900] 1>&2
if exist "%~2" type "%~2" 1>&2
exit /b 0

rem %1 = the --diag-out file, %2 = the tool's plain stderr file, %3 = tool name,
rem %4 = the exit status cmd just saw. Writes one JSONL record (E900) and appends a plain
rem line for the no-renderer fallback. Every message is ASCII (docs/169 sec 3a).
:synthesize_diag
set "stool=%~3"
if "%stool%"=="" set "stool=loment-driver"
set "sstat=%~4"
set "smsg="
if "%sstat%"=="-1073741819" set "smsg=%stool% was killed by an access violation (SIGSEGV)"
if "%sstat%"=="-1073741795" set "smsg=%stool% was killed by an illegal instruction (SIGILL)"
if "%sstat%"=="-1073741676" set "smsg=%stool% was killed by an integer divide by zero (SIGFPE)"
if "%sstat%"=="-1073741571" set "smsg=%stool% was killed by a stack overflow"
if "%sstat%"=="-1073740791" set "smsg=%stool% was killed by a stack buffer overrun"
if not defined smsg set "smsg=%stool% exited with status %sstat%"
set "smsg=%smsg% and wrote no diagnostic record"
echo {"file":"%stool%","line":0,"col":0,"code":"E900","message":"%smsg%"} > "%~1"
echo loment: %smsg% [E900] >> "%~2"
exit /b 0

rem %1 = one argument from the command line; sets csrc (the only non-command, non-flag
rem word), cnc (the colour switch) and com (the renderer output mode). Reached only via
rem `call` from :compile_only.
rem
rem This one scans the WHOLE command line with a `for`, so it cannot look ahead the way
rem :barg_loop can. `--max N` is two words there -- remember "the next word is its value"
rem in cskip and drop it, otherwise `--max 0` would leave `0` to be mistaken for the file.
:scan_arg
if not defined cskip goto scan_arg_go
set "cskip="
if defined cwantmax goto scan_max_val
if defined cwantout goto scan_out_val
exit /b 0
:scan_max_val
set "cwantmax="
rem The value has to travel WITH the flag: `lomenterr --max d.jsonl` takes the diagnostics
rem file as the cap and then prints its usage instead of the user's errors (issue #13).
set "com=%com% %~1"
exit /b 0
:scan_out_val
set "cwantout="
set "cdout=%~1"
exit /b 0
:scan_arg_go
if /I "%~1"=="ir" exit /b 0
if /I "%~1"=="check" exit /b 0
if /I "%~1"=="--no-color" goto scan_nc
if /I "%~1"=="-C" goto scan_nc
if /I "%~1"=="--short" goto scan_om
if /I "%~1"=="--json" goto scan_om
if /I "%~1"=="--max" goto scan_max
if /I "%~1"=="--diag-out" goto scan_dout
set "ss=%~1"
if "%ss:~0,6%"=="--max=" goto scan_om
if "%ss:~0,11%"=="--diag-out=" goto scan_out_v
rem An option that is not one of the above is REJECTED, not taken for the source file.
rem This path scans the WHOLE command line (both `check FILE --no-color` and
rem `check --no-color FILE` have to work), so the leading dash is the only thing that
rem tells "an option I do not know" apart from "the source file" -- and an option that is
rem silently ignored is the same lesson :barg_loop records for `--link` below, and what
rem bash's two scans already do (`*) unknown option ... exit 2`).
if "%ss:~0,1%"=="-" goto scan_bad
if defined csrc goto scan_extra
set "csrc=%~1"
exit /b 0
:scan_extra
rem Say the command the user actually typed, the same way bash interpolates `$mode`
rem (`ir` shares this whole scan with `check`, so a fixed word here would be wrong for one
rem of them). cmode is set by :ir / :check before :compile_only.
if "%cmode%"=="i" (echo loment: ir accepts exactly one input file 1>&2) else (echo loment: check accepts exactly one input file 1>&2)
set "cbad=1"
exit /b 0
:scan_bad
echo loment: unknown option %~1 1>&2
set "cbad=1"
exit /b 0
:scan_nc
set "cnc=--no-color"
exit /b 0
:scan_max
set "cskip=1"
set "cwantmax=1"
:scan_om
set "com=%com% %~1"
exit /b 0
:scan_dout
set "cskip=1"
set "cwantout=1"
exit /b 0
:scan_out_v
set "cdout=%ss:~11%"
exit /b 0
'''

INSTALL_SH = r'''#!/bin/sh
# Loment @DISPLAY@ installer (Linux / WSL). No Python, no network.
#   sh install.sh [--prefix DIR] [--no-path] [--no-skill]   default prefix: $HOME/.local
#   sh install.sh --uninstall [--prefix DIR]
# --no-skill: do not install the agent skill into ~/.claude/skills/loment
# --no-verify: install WITHOUT checking the package (deliberate, prints a warning)
set -eu

prefix=${PREFIX:-$HOME/.local}
no_path=0
no_skill=0
no_verify=0
uninstall=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=${2:-}; shift 2 ;;
        --prefix=*) prefix=${1#*=}; shift ;;
        --no-path) no_path=1; shift ;;
        --no-skill) no_skill=1; shift ;;
        --no-verify) no_verify=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "install: unknown option $1" >&2; exit 2 ;;
    esac
done
[ -n "$prefix" ] || { echo "install: empty --prefix" >&2; exit 2; }
# POSIX parameter expansion instead of `dirname`: verification must still run in a
# minimal environment where only shell builtins are guaranteed (see #425).
case "$0" in
    */*) _self_dir=${0%/*} ;;
    *) _self_dir=. ;;
esac
src=$(CDPATH= cd -- "$_self_dir" && pwd)

# A `lompi` or `loment` that is NOT ours but sits EARLIER on PATH wins, silently: you type
# `lompi version` and get an older standalone build, with nothing anywhere saying why. This
# installer never reorders a PATH it does not own, so a copy installed earlier by hand stays
# in front for good. Name it; do not remove it -- that is not this installer's call.
warn_shadowed() {
    for _n in loment lompi; do
        _hit=$(command -v "$_n" 2>/dev/null || true)
        case "$_hit" in
            ""|"$prefix/bin/$_n") ;;
            *) echo "install: WARNING: '$_n' on PATH is $_hit -- not this install" >&2
               echo "install:          that one comes first and wins; remove or rename it," >&2
               echo "install:          or put $prefix/bin ahead of it" >&2 ;;
        esac
    done
}

if [ "$uninstall" = 1 ]; then
    # Take back the libraries this package put into lompi's store. Ask lompi where that is
    # BEFORE deleting the binary (same question the install side asked, so same answer),
    # and remove only the <name>/<version> trees this package shipped -- other versions the
    # user installed under the same name are none of our business.
    store_dst=""
    if [ -x "$prefix/bin/lompi" ]; then
        store_dst=$("$prefix/bin/lompi" config 2>/dev/null | sed -n 's/^store:[[:space:]]*//p' | tr -d '\r' | head -1)
    fi
    store_base="$prefix/share/lompi/store"
    if [ -n "$store_dst" ] && [ -d "$store_base" ]; then
        for d in "$store_base"/*/*; do
            [ -d "$d" ] || continue
            rm -rf "$store_dst/${d#$store_base/}"
        done
        rmdir "$store_dst" 2>/dev/null || true
        rmdir "$(dirname "$store_dst")" 2>/dev/null || true
    fi
    rm -f "$prefix/bin/loment" "$prefix/bin/loment-driver" "$prefix/bin/loment-lsp" \
          "$prefix/bin/loment-fmt" "$prefix/bin/loment-doc" "$prefix/bin/loment-lomelf" \
          "$prefix/bin/loment-cli" "$prefix/bin/lomenterr" \
          "$prefix/bin/lompi"
    rm -rf "$prefix/share/loment" "$prefix/share/lompi"
    # only what this installer created -- never ~/.claude/skills or AGENTS.md at large
    if [ "$no_skill" != 1 ]; then
        rm -rf "$HOME/.claude/skills/loment" "$HOME/.claude/skills/lompi"
        if [ -f "$HOME/.codex/AGENTS.md" ]; then
            sed -i '/<!-- loment:begin -->/,/<!-- loment:end -->/d' "$HOME/.codex/AGENTS.md" 2>/dev/null || true
            sed -i '/<!-- lompi:begin -->/,/<!-- lompi:end -->/d' "$HOME/.codex/AGENTS.md" 2>/dev/null || true
        fi
    fi
    echo "install: removed from $prefix"
    exit 0
fi

# --- package integrity ---------------------------------------------------------------
# The list travels WITH the package, so it is not a trust anchor by itself: the digest of
# SHA256SUMS is printed below, and the release page publishes the same digest through a
# second channel. An attacker who rewrites the archive and the list together cannot also
# rewrite that page. Verification is never silently skipped: a missing verifier is fatal
# unless the caller passes --no-verify.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        _o=$(sha256sum "$1") || return 1
    elif command -v shasum >/dev/null 2>&1; then
        _o=$(shasum -a 256 "$1") || return 1
    elif command -v openssl >/dev/null 2>&1; then
        _o=$(openssl dgst -sha256 -r "$1") || return 1
    else
        return 2
    fi
    printf '%s\n' "${_o%% *}"
}

verify_package() {
    _sums="$src/SHA256SUMS"
    [ -f "$_sums" ] || {
        echo "install: SHA256SUMS missing -- package is incomplete" >&2; return 1; }
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$src" && sha256sum -c --quiet SHA256SUMS) || {
            echo "install: SHA256SUMS mismatch -- package is corrupt" >&2; return 1; }
    elif command -v shasum >/dev/null 2>&1; then
        (cd "$src" && shasum -a 256 -c --status SHA256SUMS) || {
            echo "install: SHA256SUMS mismatch -- package is corrupt" >&2; return 1; }
    elif command -v openssl >/dev/null 2>&1; then
        while IFS= read -r _line; do
            [ -n "$_line" ] || continue
            _want=${_line%% *}
            _rel=${_line#*  }
            _got=$(sha256_of "$src/$_rel") || {
                echo "install: cannot hash $_rel -- package is corrupt" >&2; return 1; }
            [ "$_got" = "$_want" ] || {
                echo "install: SHA256SUMS mismatch: $_rel" >&2; return 1; }
        done < "$_sums"
    else
        # An installer that cannot check its payload must SAY SO and STOP. Installing
        # anyway is precisely the hole (#425); an explicit --no-verify is the only way
        # past this, so an unverified install is a decision rather than a fallback.
        echo "install: no SHA-256 verifier (sha256sum / shasum / openssl) found" >&2
        echo "install: refusing to install an unverified package; pass --no-verify to override" >&2
        return 1
    fi
    return 0
}

verify_signature_if_present() {
    # Signature assets are published BESIDE the archive (release page). When a user puts
    # them next to the package we verify them; when they are absent the digest printed
    # below is the out-of-band comparison point.
    _asc="$src/SHA256SUMS.asc"
    [ -f "$_asc" ] || return 0
    _pub="$src/loment-signing.asc"
    [ -f "$_pub" ] || {
        echo "install: SHA256SUMS.asc present but loment-signing.asc missing" >&2
        return 1; }
    command -v gpg >/dev/null 2>&1 || {
        echo "install: gpg not found -- cannot verify SHA256SUMS.asc" >&2; return 1; }
    # Random when the OS can name it; the PID fallback is still created fresh, not reused.
    _gh=$(mktemp -d 2>/dev/null) || {
        _gh=${TMPDIR:-/tmp}/loment-gpg-$$
        rm -rf "$_gh"
        mkdir -p "$_gh"
    }
    chmod 700 "$_gh" 2>/dev/null || true
    if ! GNUPGHOME=$_gh gpg --batch --quiet --import "$_pub" 2>/dev/null; then
        rm -rf "$_gh"
        echo "install: cannot import loment-signing.asc" >&2
        return 1
    fi
    if ! GNUPGHOME=$_gh gpg --batch --status-fd 1 --verify "$_asc" "$_sums" 2>/dev/null \
         | grep -q '^\[GNUPG:\] GOODSIG '; then
        rm -rf "$_gh"
        echo "install: SHA256SUMS.asc does NOT verify against loment-signing.asc" >&2
        return 1
    fi
    echo "install: SHA256SUMS.asc verified against loment-signing.asc"
    rm -rf "$_gh"
    return 0
}

copy_verified_payload() {
    # Iterate the VERIFIED manifest and copy exactly those entries. An unlisted file in
    # bin/ is a hard error: it would otherwise be installed but never hashed (#424).
    _sums="$src/SHA256SUMS"
    [ -f "$_sums" ] || {
        echo "install: SHA256SUMS missing -- package is incomplete" >&2; return 1; }
    while IFS= read -r _line; do
        [ -n "$_line" ] || continue
        _rel=${_line#*  }
        case "$_rel" in
            bin/*|share/*) ;;
            *) continue ;;
        esac
        case "$_rel" in
            /*|*../*|*/..|..) echo "install: refusing suspicious path in SHA256SUMS: $_rel" >&2
                              return 1 ;;
        esac
        _dst="$prefix/$_rel"
        _dir=${_dst%/*}
        [ -d "$_dir" ] || mkdir -p "$_dir"
        cp -f "$src/$_rel" "$_dst" || return 1
    done < "$_sums"
    for _f in "$src/bin/"*; do
        [ -f "$_f" ] || continue
        if ! grep -F -q "  bin/${_f##*/}" "$_sums"; then
            echo "install: unlisted file in package: bin/${_f##*/}" >&2
            return 1
        fi
    done
    return 0
}

if [ "$no_verify" = 1 ]; then
    echo "install: WARNING: --no-verify -- installing WITHOUT checking the package" >&2
else
    verify_package || exit 1
    verify_signature_if_present || exit 1
    _digest=$(sha256_of "$src/SHA256SUMS") || {
        echo "install: cannot compute the SHA256SUMS digest" >&2; exit 1; }
    echo "install: SHA256SUMS digest: $_digest"
    echo "install: compare it with the digest published on the release page"
    echo "install:          (out of band -- do not trust a digest found inside this archive)"
fi

mkdir -p "$prefix/bin" "$prefix/share/loment" "$prefix/share/lompi"
copy_verified_payload || exit 1
chmod 755 "$prefix/bin/"*

"$prefix/bin/loment" version || {
    echo "install: installed but 'loment version' failed" >&2; exit 1; }
# lompi (Loment's package manager) ships in the same package, but it is a STANDALONE
# command -- NOT a subcommand of loment. So it gets its own smoke test: with no args it
# prints its usage and exits 2, which proves the binary is alive.
"$prefix/bin/lompi" 2>&1 | grep -q "package manager for Loment" || {
    echo "install: installed but 'lompi' did not run" >&2; exit 1; }
echo "install: Loment @DISPLAY@ -> $prefix"

# lompi's standard library (std: 127 modules, host: 7 -- 137 files) goes into the store
# lompi itself would use, so it is there the moment Loment is installed. ASK lompi for that
# path -- never re-derive it here: cfg_root() has three rules (AppData / Users / exe dir)
# and a second copy of them in this script would drift from lompi's.
store_src="$prefix/share/lompi/store"
if [ -d "$store_src" ]; then
    store_dst=$("$prefix/bin/lompi" config 2>/dev/null | sed -n 's/^store:[[:space:]]*//p' | tr -d '\r' | head -1)
    if [ -n "$store_dst" ]; then
        mkdir -p "$store_dst"
        cp -R "$store_src/." "$store_dst/"
        echo "install: lompi store -> $store_dst"
    else
        echo "install: 'lompi config' gave no store path; the libraries stay at" >&2
        echo "         $store_src -- pass that to lompi explicitly." >&2
    fi
fi

# Ship the self-contained agent skill into the user-level Claude skills dir, so a coding
# agent in ANY project can read it. Skipped when ~/.claude is absent (then it just stays
# in the package). --no-skill to skip.
skill_src="$prefix/share/loment/skill/SKILL.md"
mark_b='<!-- loment:begin -->'
mark_e='<!-- loment:end -->'
if [ "$no_skill" = 1 ]; then
    echo "install: agent skill not installed (--no-skill); kept at $skill_src"
else
    hits=""
    if [ -d "$HOME/.claude" ]; then
        mkdir -p "$HOME/.claude/skills/loment"
        cp -f "$skill_src" "$HOME/.claude/skills/loment/SKILL.md"
        hits="claude"
    fi
    # Codex CLI reads ~/.codex/AGENTS.md as global instructions. Marker-delimited so a
    # re-install is idempotent and --uninstall takes back exactly this block.
    if [ -d "$HOME/.codex" ]; then
        agents="$HOME/.codex/AGENTS.md"
        [ -f "$agents" ] || : > "$agents"
        # Drop a previous block (same markers), then append. On uninstall we delete only
        # the marked range: a leftover blank line is preferable to guessing which blank
        # lines are the user's -- never touch bytes we did not write.
        sed -i '/<!-- loment:begin -->/,/<!-- loment:end -->/d' "$agents" 2>/dev/null || true
        # If the file does not end with a newline, add one so the marker is not glued
        # onto the user's last line.
        if [ -s "$agents" ] && [ -n "$(tail -c 1 "$agents")" ]; then printf '\n' >> "$agents"; fi
        {
            echo "$mark_b"
            echo "## Loment"
            echo "Before writing or changing a Loment program (\`.lomt\`), read the guide:"
            echo "$skill_src"
            echo "It is self-contained: builtins, syntax, error codes (E1-E17), and the toolchain commands."
            echo "$mark_e"
        } >> "$agents"
        hits="${hits:+$hits, }codex"
    fi

    # lompi's guide gets the SAME treatment (user 2026-09-15), with its OWN markers so
    # uninstalling one never touches the other.
    lompi_skill="$prefix/share/lompi/skill/SKILL.md"
    if [ -f "$lompi_skill" ]; then
        if [ -d "$HOME/.claude" ]; then
            mkdir -p "$HOME/.claude/skills/lompi"
            cp -f "$lompi_skill" "$HOME/.claude/skills/lompi/SKILL.md"
            hits="${hits:+$hits, }claude:lompi"
        fi
        if [ -d "$HOME/.codex" ]; then
            agents="$HOME/.codex/AGENTS.md"
            [ -f "$agents" ] || : > "$agents"
            sed -i '/<!-- lompi:begin -->/,/<!-- lompi:end -->/d' "$agents" 2>/dev/null || true
            if [ -s "$agents" ] && [ -n "$(tail -c 1 "$agents")" ]; then printf '\n' >> "$agents"; fi
            {
                echo '<!-- lompi:begin -->'
                echo "## lompi"
                echo "To manage Loment libraries (a store, a lockfile, \`deps/\`), read:"
                echo "$lompi_skill"
                echo "lompi is a standalone command. It is NOT a subcommand of loment."
                echo '<!-- lompi:end -->'
            } >> "$agents"
            hits="${hits:+$hits, }codex:lompi"
        fi
    fi
    if [ -n "$hits" ]; then
        echo "install: agent skill -> $hits (guides under $prefix/share/{loment,lompi}/skill/)"
    else
        # No tool-specific dir to hook? The CLI is the hook.
        echo "install: no known agent dir here -- the guide is still reachable:"
        echo "install:   loment skill --print   (any agent: it runs the CLI)"
    fi
    echo "install:   a plain copy lives at $skill_src"
    echo "install:   for shells: export LOMENT_SKILL=\"$skill_src\""
fi

case ":${PATH}:" in
    *":$prefix/bin:"*) ;;
    *) [ "$no_path" = 1 ] || {
        echo "install: put $prefix/bin on PATH, e.g."
        echo "         echo 'export PATH=\"$prefix/bin:\$PATH\"' >> ~/.profile" >&2 ; } ;;
esac
warn_shadowed
'''

INSTALL_PS1 = r'''# install.ps1 - Loment @DISPLAY@ installer for Windows (native toolchain).
#
# ASCII only (docs/157 3.4: PS 5.1 reads a BOM-less .ps1 as ANSI).
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -File install.ps1 -Prefix D:\Loment
#   powershell -File install.ps1 -DryRun          # print the plan, change nothing
#   powershell -File install.ps1 -Uninstall
#
# The package ships native PE binaries - no WSL, no clang. Everything lands under
# $Prefix, and bin/loment.cmd calls those .exe files directly.

[CmdletBinding()]
param(
    [string]$Prefix = '',
    [string]$PayloadDir = '',
    [string]$PayloadZip = '',
    [switch]$NoPath,
    [switch]$NoFileType,
    [switch]$NoSkill,
    [switch]$NoVerify,
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Prefix -eq '') { $Prefix = Join-Path $env:LOCALAPPDATA 'Loment' }
$BinDir = Join-Path $Prefix 'bin'

function Say([string]$m) { Write-Host $m }

function Resolve-Payload {
    if ($PayloadDir -ne '') { return $PayloadDir }
    if ($PayloadZip -ne '') {
        # Extract BY HAND: Windows PowerShell 5.1's Expand-Archive does not reject `..`
        # or absolute entries (the rejection arrived in .NET Core 3.0), so a crafted zip
        # can write outside the target before any check runs. We also verify every entry
        # against SHA256SUMS BEFORE writing a single byte, and only into a directory the
        # OS names for us (no predictable loment-setup-<PID> to pre-create or guess).
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $root = Join-Path $env:TEMP ('loment-setup-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        $rootFull = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
        $zip = [IO.Compression.ZipFile]::OpenRead($PayloadZip)
        try {
            # Payloads come in two shapes. Zipping the distribution's **contents** puts
            # SHA256SUMS at the archive root. Zipping the distribution **directory** gives
            # every entry one shared leading folder (loment-<ver>-windows-x64/), and then
            # there is no root SHA256SUMS -- which is how the released 0.1.5 setup.exe was
            # built, so it refused a complete package with "SHA256SUMS missing". Accept
            # exactly that shape: one shared folder, with SHA256SUMS at its root. Anything
            # else is still reported as incomplete.
            $top = ''
            $sumsEntry = $zip.GetEntry('SHA256SUMS')
            if (-not $sumsEntry) {
                $tops = @{}
                $shared = $true
                foreach ($e in $zip.Entries) {
                    if ($e.FullName -eq '' -or $e.FullName.EndsWith('/')) { continue }
                    $cut = $e.FullName.IndexOf('/')
                    if ($cut -le 0) { $shared = $false; break }
                    $tops[$e.FullName.Substring(0, $cut)] = $true
                }
                if ($shared -and $tops.Count -eq 1) {
                    $seg = [string](@($tops.Keys)[0])
                    $sumsEntry = $zip.GetEntry($seg + '/SHA256SUMS')
                    if ($sumsEntry) { $top = $seg + '/' }
                }
            }
            if (-not $sumsEntry) { throw "SHA256SUMS missing from payload.zip -- package is incomplete" }
            $want = @{}
            $reader = New-Object System.IO.StreamReader($sumsEntry.Open())
            try {
                while (($line = $reader.ReadLine()) -ne $null) {
                    if ($line -match '^([0-9a-f]{64})  (.+)$') { $want[$Matches[2]] = $Matches[1] }
                }
            } finally { $reader.Dispose() }
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                foreach ($e in $zip.Entries) {
                    if ($e.FullName -eq '' -or $e.FullName.EndsWith('/')) { continue }
                    $name = $e.FullName.Substring($top.Length)
                    $rel = $name.Replace('/', '\')
                    if ([IO.Path]::IsPathRooted($rel) -or $rel -match '(^|\\)\.\.(\\|$)') {
                        throw "payload.zip entry escapes the extraction root: $($e.FullName)"
                    }
                    $dest = [IO.Path]::GetFullPath((Join-Path $rootFull $rel))
                    if (-not $dest.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
                        throw "payload.zip entry escapes the extraction root: $($e.FullName)"
                    }
                    if ($name -eq 'SHA256SUMS') { continue }
                    if (-not $want.ContainsKey($name)) {
                        throw "unlisted file in package: $($e.FullName)"
                    }
                    $s = $e.Open()
                    try { $hash = $sha.ComputeHash($s) } finally { $s.Dispose() }
                    $got = ([BitConverter]::ToString($hash)).Replace('-', '').ToLower()
                    if ($got -ne $want[$name]) {
                        throw "SHA256SUMS mismatch: $($e.FullName)"
                    }
                }
            } finally { $sha.Dispose() }
            # Verification passed -- now, and only now, write the files.
            foreach ($e in $zip.Entries) {
                if ($e.FullName -eq '' -or $e.FullName.EndsWith('/')) { continue }
                $name = $e.FullName.Substring($top.Length)
                $rel = $name.Replace('/', '\')
                $dest = [IO.Path]::GetFullPath((Join-Path $rootFull $rel))
                New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
            }
        } finally { $zip.Dispose() }
        # `$top` is stripped on the way out, so the extraction root itself is the
        # distribution root in both shapes -- the rest of the script needs no other change.
        return $root
    }
    return $ScriptDir
}

function Verify-Sums([string]$payload) {
    $sums = Join-Path $payload 'SHA256SUMS'
    if (-not (Test-Path -LiteralPath $sums)) { throw "SHA256SUMS missing -- package is incomplete" }
    $bad = 0
    $verified = @()
    foreach ($line in Get-Content -LiteralPath $sums) {
        if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { continue }
        $want = $Matches[1]; $rel = $Matches[2]
        $f = Join-Path $payload $rel
        if (-not (Test-Path -LiteralPath $f)) { Say "[sums] missing: $rel"; $bad++; continue }
        $got = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) { Say "[sums] MISMATCH: $rel"; $bad++ } else { $verified += $rel }
    }
    if ($bad -gt 0) { throw "$bad file(s) failed SHA256SUMS verification" }
    Say "[1/6] SHA256SUMS verified"
    return $verified
}

function Verify-SignatureIfPresent([string]$payload) {
    # The signature assets are published BESIDE the archive (release page). When present
    # next to the payload we verify them; otherwise the SHA256SUMS digest printed later is
    # the out-of-band comparison point.
    $asc = Join-Path $payload 'SHA256SUMS.asc'
    if (-not (Test-Path -LiteralPath $asc)) { return }
    $pub = Join-Path $payload 'loment-signing.asc'
    if (-not (Test-Path -LiteralPath $pub)) {
        throw "SHA256SUMS.asc present but loment-signing.asc missing"
    }
    if (-not (Get-Command gpg -ErrorAction SilentlyContinue)) {
        throw "gpg not found -- cannot verify SHA256SUMS.asc"
    }
    $home2 = Join-Path $env:TEMP ('loment-gpg-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $home2 -Force | Out-Null
    $old = $env:GNUPGHOME
    try {
        $env:GNUPGHOME = $home2
        & gpg --batch --quiet --import $pub
        if ($LASTEXITCODE -ne 0) { throw "cannot import loment-signing.asc" }
        & gpg --batch --verify $asc (Join-Path $payload 'SHA256SUMS')
        if ($LASTEXITCODE -ne 0) { throw "SHA256SUMS.asc does NOT verify against loment-signing.asc" }
        Say "[1b/6] detached signature verified"
    } finally {
        $env:GNUPGHOME = $old
        Remove-Item -LiteralPath $home2 -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Copy-VerifiedTree([string]$From, [string]$To, [string[]]$Verified, [string]$Prefix) {
    # Copy exactly what the verified manifest names. An unlisted file is a hard error:
    # iterating the directory instead of the list installs files nobody hashed (#424).
    $files = @(Get-ChildItem -LiteralPath $From -Recurse -File -ErrorAction SilentlyContinue)
    foreach ($f in $files) {
        $rel = $Prefix + '/' + $f.FullName.Substring($From.Length + 1).Replace('\', '/')
        if ($Verified -notcontains $rel) {
            throw "unlisted file in package: $rel -- refusing to install"
        }
        $tail = $f.FullName.Substring($From.Length + 1)
        $dst = Join-Path $To $tail
        New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
        Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
    }
}

function Remove-PathEntry([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $cur) { return }
    $parts = $cur.Split(';') | Where-Object { $_ -ne '' -and $_ -ne $dir }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

function Add-PathEntry([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($cur -and ($cur.Split(';') -contains $dir)) { return }
    $new = if ($cur) { "$cur;$dir" } else { $dir }
    [Environment]::SetEnvironmentVariable('Path', $new, 'User')
}

# A `lompi` or `loment` that is NOT ours but sits EARLIER on PATH wins, silently: you type
# `lompi version` and get an older standalone build, with nothing anywhere saying why --
# "lompi is still 0.1.0" is then unattributable. This installer APPENDS to PATH on purpose
# (it must not reorder a machine it does not own), so a copy installed earlier by hand stays
# in front for good. Name it, once, and never touch it -- deleting someone else's command is
# not this installer's call. 2026-10-12: a user reported exactly this.
function Show-Shadowed {
    $mine = @('loment', 'lompi')
    $found = 0
    foreach ($n in $mine) {
        $c = @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue)[0]
        if (-not $c -or -not $c.Source) { continue }
        if ($c.Source.ToLower().StartsWith($BinDir.ToLower())) { continue }
        Say "[3a/6] WARNING: '$n' on PATH resolves to $($c.Source) -- not this install."
        Say "        That one comes first and wins; remove or rename it,"
        Say "        or put $BinDir ahead of it."
        $found++
    }
    if ($found -eq 0) { Say "[3a/6] no other loment/lompi earlier on PATH" }
}

function Find-Editor {
    foreach ($scope in @('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')) {
        $base = [Environment]::GetEnvironmentVariable($scope)
        if (-not $base) { continue }
        foreach ($rel in @('Programs\Microsoft VS Code\Code.exe', 'Microsoft VS Code\Code.exe')) {
            $p = Join-Path $base $rel
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    return $null
}

# Mirrors tools/loment_filetype.py: same ProgIDs, same keys, HKCU only (no admin).
function Register-FileType {
    param([string]$Editor, [string]$Icon)
    $map = @{ '.lomt' = @('Loment.Source', 'Loment source file', 'text/x-loment');
              '.lom'  = @('Loment.L0', 'Loment L0 declaration', 'text/x-lom');
              '.lomp' = @('Loment.Package', 'Loment package manifest', 'text/x-loment') }
    $cmd = '"' + $Editor + '" "%1"'
    foreach ($ext in $map.Keys) {
        $progid = $map[$ext][0]; $friendly = $map[$ext][1]; $mime = $map[$ext][2]
        New-Item -Path "HKCU:\Software\Classes\$ext" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$ext" -Name '(default)' -Value $progid
        New-Item -Path "HKCU:\Software\Classes\$ext\OpenWithProgids" -Force | Out-Null
        New-ItemProperty -Path "HKCU:\Software\Classes\$ext\OpenWithProgids" -Name $progid `
            -Value '' -PropertyType String -Force | Out-Null
        New-Item -Path "HKCU:\Software\Classes\$progid" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name '(default)' -Value $friendly
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'FriendlyTypeName' -Value $friendly
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'Content Type' -Value $mime
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'PerceivedType' -Value 'text'
        New-Item -Path "HKCU:\Software\Classes\$progid\DefaultIcon" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid\DefaultIcon" -Name '(default)' `
            -Value ('"' + $Icon + '",0')
        New-Item -Path "HKCU:\Software\Classes\$progid\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid\shell\open\command" `
            -Name '(default)' -Value $cmd
        $fk = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\OpenWithProgids"
        New-Item -Path $fk -Force | Out-Null
        New-ItemProperty -Path $fk -Name $progid -Value '' -PropertyType String -Force | Out-Null
    }
    Say "[6/6] .lomt/.lom/.lomp registered (open with: $Editor)"
}

function Unregister-FileType {
    foreach ($ext in @('.lomt', '.lom', '.lomp')) {
        $progid = switch ($ext) {
            '.lomt' { 'Loment.Source' }
            '.lom'  { 'Loment.L0' }
            default { 'Loment.Package' }
        }
        foreach ($k in @("HKCU:\Software\Classes\$progid\shell\open\command",
                         "HKCU:\Software\Classes\$progid\shell\open",
                         "HKCU:\Software\Classes\$progid\shell",
                         "HKCU:\Software\Classes\$progid\DefaultIcon",
                         "HKCU:\Software\Classes\$progid",
                         "HKCU:\Software\Classes\$ext\OpenWithProgids",
                         "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\OpenWithProgids")) {
            Remove-Item -Path $k -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Say "[--] .lomt/.lom registration removed"
}

function Get-LompiStore([string]$Exe) {
    # Ask lompi itself where its store is. cfg_root() has three rules (AppData / Users /
    # exe dir); re-deriving them here would be a second copy that drifts -- so we read the
    # line `lompi config` prints instead.
    if (-not (Test-Path -LiteralPath $Exe)) { return '' }
    foreach ($line in (& $Exe config 2>$null)) {
        if ($line -match '^store:\s*(.+?)\s*$') { return $Matches[1] }
    }
    return ''
}

if ($Uninstall) {
    if ($DryRun) { Say "[dry-run] would remove $Prefix, the file-type keys and the agent skill"; exit 0 }
    # Take back the libraries this package put into lompi's store. Ask lompi where that is
    # BEFORE removing anything (the binary and the pristine copy both live under $Prefix),
    # and drop only the <name>/<version> trees this package shipped -- other versions the
    # user installed under the same name are none of our business.
    $storeSrc = Join-Path $Prefix 'share\lompi\store'
    if (Test-Path -LiteralPath $storeSrc) {
        $dst = Get-LompiStore (Join-Path $BinDir 'lompi.exe')
        if ($dst) {
            foreach ($pkg in Get-ChildItem -LiteralPath $storeSrc -Directory -ErrorAction SilentlyContinue) {
                foreach ($ver in Get-ChildItem -LiteralPath $pkg.FullName -Directory -ErrorAction SilentlyContinue) {
                    Remove-Item -LiteralPath (Join-Path $dst "$($pkg.Name)\$($ver.Name)") `
                                -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
            if ((Test-Path -LiteralPath $dst) -and -not (Get-ChildItem -LiteralPath $dst -Force)) {
                Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue
                # the store's parent (e.g. %LOCALAPPDATA%\lompi) too, but only if we left it
                # empty -- a user's own cache dir stays
                $up = Split-Path -Parent $dst
                if ((Test-Path -LiteralPath $up) -and -not (Get-ChildItem -LiteralPath $up -Force)) {
                    Remove-Item -LiteralPath $up -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    Remove-Item -LiteralPath $Prefix -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $NoPath) { Remove-PathEntry $BinDir }
    if (-not $NoFileType) { Unregister-FileType }
    if (-not $NoSkill) {
        # Only what this installer created -- never a skills dir / AGENTS.md at large.
        Remove-Item -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\loment') `
                    -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\lompi') `
                    -Recurse -Force -ErrorAction SilentlyContinue
        $agents = Join-Path $env:USERPROFILE '.codex\AGENTS.md'
        if (Test-Path -LiteralPath $agents) {
            $t = Get-Content -LiteralPath $agents -Raw
            if ($null -eq $t) { $t = '' }
            $t = [regex]::Replace($t, '(?s)\r?\n?<!-- loment:begin -->.*?<!-- loment:end -->\r?\n?', '')
            $t = [regex]::Replace($t, '(?s)\r?\n?<!-- lompi:begin -->.*?<!-- lompi:end -->\r?\n?', '')
            [System.IO.File]::WriteAllText($agents, $t, (New-Object System.Text.UTF8Encoding($false)))
        }
        [Environment]::SetEnvironmentVariable('LOMENT_SKILL', $null, 'User')
    }
    Say "uninstalled"
    exit 0
}

$Payload = Resolve-Payload
Say "Loment @DISPLAY@ (@VERSION@)"
Say "  prefix:  $Prefix"
Say "  payload: $Payload"

if ($DryRun) {
    Say "[dry-run] would: verify SHA256SUMS; copy bin/ + share/ under $Prefix;"
    Say "[dry-run]       add $BinDir to user PATH; register .lomt/.lom"
    exit 0
}

$verified = @()
if ($NoVerify) {
    Say "WARNING: -NoVerify -- installing WITHOUT checking the package"
    $sumsPath = Join-Path $Payload 'SHA256SUMS'
    if (-not (Test-Path -LiteralPath $sumsPath)) { throw "SHA256SUMS missing -- package is incomplete" }
    foreach ($line in Get-Content -LiteralPath $sumsPath) {
        if ($line -match '^[0-9a-f]{64}  (.+)$') { $verified += $Matches[1] }
    }
} else {
    $verified = @(Verify-Sums $Payload)
    Verify-SignatureIfPresent $Payload
    $digest = (Get-FileHash -LiteralPath (Join-Path $Payload 'SHA256SUMS') -Algorithm SHA256).Hash.ToLower()
    Say "[1a/6] SHA256SUMS digest: $digest"
    Say "       compare it with the digest published on the release page"
    Say "       (out of band -- do not trust a digest found inside this archive)"
}

New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
Copy-VerifiedTree (Join-Path $Payload 'bin') $BinDir $verified 'bin'
$shareDir = Join-Path $Prefix 'share\loment'
New-Item -ItemType Directory -Path (Join-Path $shareDir 'examples') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $shareDir 'skill') -Force | Out-Null
# lompi's guide lives under its OWN share tree -- and the destination directory must
# EXIST before Copy-Item writes into it (the same bug bit share\loment\skill in v1).
$lompiShareDir = Join-Path $Prefix 'share\lompi'
New-Item -ItemType Directory -Path (Join-Path $lompiShareDir 'skill') -Force | Out-Null
foreach ($rel in @('share/loment/version', 'share/loment/seed.ll',
                   'share/loment/examples/user_hello.lomt',
                   'share/loment/examples/tour.lomt',
                   'share/loment/skill/SKILL.md',
                   'share/lompi/skill/SKILL.md')) {
    if ($verified -notcontains $rel) { throw "unlisted file in package: $rel -- refusing to install" }
    $f = Join-Path $Payload $rel.Replace('/', '\')
    Copy-Item -LiteralPath $f -Destination (Join-Path $Prefix $rel.Replace('/', '\')) -Force
}
# loment/lib: the modules you can `use` by name (proc is one). Copy from the VERIFIED
# list, not from a wildcard: a stray file in the directory has no hash and must not
# reach the installed prefix.
$libSrc = Join-Path $Payload 'share\loment\lib'
if (Test-Path -LiteralPath $libSrc) {
    $libDst = Join-Path $Prefix 'share\loment\lib'
    New-Item -ItemType Directory -Path $libDst -Force | Out-Null
    Copy-VerifiedTree $libSrc $libDst $verified 'share/loment/lib'
}
foreach ($rel in @('README.md', 'LICENSE-MIT', 'LICENSE-APACHE')) {
    if ($verified -notcontains $rel) { throw "unlisted file in package: $rel -- refusing to install" }
    $f = Join-Path $Payload $rel
    Copy-Item -LiteralPath $f -Destination (Join-Path $Prefix $rel) -Force
}
Say "[2/6] toolchain -> $BinDir"

if ($NoPath) { Say "[3/6] PATH unchanged (-NoPath)" }
else { Add-PathEntry $BinDir; Say "[3/6] user PATH += $BinDir" }
Show-Shadowed

& (Join-Path $BinDir 'loment.cmd') version
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: 'loment version' exit $LASTEXITCODE" }
& (Join-Path $BinDir 'loment.cmd') ir (Join-Path $shareDir 'examples\user_hello.lomt') > $null
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: compiling user_hello.lomt" }
Say "[5/6] smoke test ok (version + compile)"

# lompi's standard library (std: 127 modules, host: 7 -- 137 files): keep the pristine copy
# under the prefix, then put it where lompi will actually look. ASK lompi for that path --
# cfg_root() has three rules, and a second copy of them in this script would drift.
$storeSrc = Join-Path $lompiShareDir 'store'
$storeFrom = Join-Path $Payload 'share\lompi\store'
if (Test-Path -LiteralPath $storeFrom) {
    New-Item -ItemType Directory -Path $storeSrc -Force | Out-Null
    Copy-VerifiedTree $storeFrom $storeSrc $verified 'share/lompi/store'
    $dst = Get-LompiStore (Join-Path $BinDir 'lompi.exe')
    if ($dst) {
        New-Item -ItemType Directory -Path $dst -Force | Out-Null
        Copy-Item -Path (Join-Path $storeSrc '*') -Destination $dst -Recurse -Force
        Say "[5a/6] lompi store (std + host, 137 files) -> $dst"
    } else {
        Say "[5a/6] 'lompi config' gave no store path; the libraries stay at $storeSrc"
    }
}

# --- agent skill: hand it to whatever agent is here, NOT just Claude --------------------
# There is no OS-wide way to push a skill into an arbitrary LLM: every tool reads its own
# path. So we write a POINTER into the user-level file that each tool already uses, and
# only when that tool's directory already exists -- we never create another tool's config
# dir. The guide itself stays in ONE place (the package). -NoSkill skips all of this.
$skillSrc = Join-Path $shareDir 'skill\SKILL.md'
$markB = '<!-- loment:begin -->'
$markE = '<!-- loment:end -->'
$skillHits = @()
if ($NoSkill) {
    Say "[5b/6] agent skill not installed (-NoSkill); kept at $skillSrc"
} else {
    # Claude Code: user-level skills dir, auto-discovered.
    $claudeDir = Join-Path $env:USERPROFILE '.claude'
    if (Test-Path -LiteralPath $claudeDir) {
        $skillDir = Join-Path $claudeDir 'skills\loment'
        New-Item -ItemType Directory -Path $skillDir -Force | Out-Null
        Copy-Item -LiteralPath $skillSrc -Destination (Join-Path $skillDir 'SKILL.md') -Force
        $skillHits += "claude"
    }
    # Codex CLI: global instructions at ~/.codex/AGENTS.md. Marker-delimited so re-install
    # is idempotent and uninstall removes exactly this block, leaving the rest untouched.
    $codexDir = Join-Path $env:USERPROFILE '.codex'
    if (Test-Path -LiteralPath $codexDir) {
        $agents = Join-Path $codexDir 'AGENTS.md'
        $prev = ''
        if (Test-Path -LiteralPath $agents) { $prev = Get-Content -LiteralPath $agents -Raw }
        if ($null -eq $prev) { $prev = '' }
        $prev = [regex]::Replace($prev, '(?s)<!-- loment:begin -->.*?<!-- loment:end -->\r?\n?', '')
        $block = $markB + "`r`n" +
                 "## Loment`r`n" +
                 "Before writing or changing a Loment program (``.lomt``), read the guide:`r`n" +
                 "$skillSrc`r`n" +
                 "It is self-contained: builtins, syntax, error codes (E1-E17), and the toolchain commands.`r`n" +
                 $markE + "`r`n"
        # Append only: the file's original bytes are left untouched, so removing this
        # block again restores it byte for byte. (v1 trimmed -- the gate caught it.)
        $body = $prev
        if ($body -ne '') { $body += "`r`n" }
        [System.IO.File]::WriteAllText($agents, $body + $block, (New-Object System.Text.UTF8Encoding($false)))
        $skillHits += "codex"
    }
    # lompi's guide gets the SAME treatment (user 2026-09-15), with its OWN markers so
    # uninstalling one never touches the other. lompi is a standalone command.
    $lompiSkillSrc = Join-Path $Prefix 'share\lompi\skill\SKILL.md'
    if (Test-Path -LiteralPath $lompiSkillSrc) {
        if (Test-Path -LiteralPath $claudeDir) {
            $lompiSkillDir = Join-Path $claudeDir 'skills\lompi'
            New-Item -ItemType Directory -Path $lompiSkillDir -Force | Out-Null
            Copy-Item -LiteralPath $lompiSkillSrc -Destination (Join-Path $lompiSkillDir 'SKILL.md') -Force
            $skillHits += "claude:lompi"
        }
        if (Test-Path -LiteralPath $codexDir) {
            $agents2 = Join-Path $codexDir 'AGENTS.md'
            $prev2 = ''
            if (Test-Path -LiteralPath $agents2) { $prev2 = Get-Content -LiteralPath $agents2 -Raw }
            if ($null -eq $prev2) { $prev2 = '' }
            $prev2 = [regex]::Replace($prev2, '(?s)<!-- lompi:begin -->.*?<!-- lompi:end -->\r?\n?', '')
            $block2 = '<!-- lompi:begin -->' + "`r`n" +
                      "## lompi`r`n" +
                      "To manage Loment libraries (a store, a lockfile, ``deps/``), read:`r`n" +
                      "$lompiSkillSrc`r`n" +
                      "lompi is a standalone command. It is NOT a subcommand of loment.`r`n" +
                      '<!-- lompi:end -->' + "`r`n"
            $body2 = $prev2
            if ($body2 -ne '') { $body2 += "`r`n" }
            [System.IO.File]::WriteAllText($agents2, $body2 + $block2, (New-Object System.Text.UTF8Encoding($false)))
            $skillHits += "codex:lompi"
        }
    }

    # A user-level variable so anything can find the guide without knowing the prefix.
    # .NET writes HKCU\Environment and broadcasts WM_SETTINGCHANGE (new processes see it).
    [Environment]::SetEnvironmentVariable('LOMENT_SKILL', $skillSrc, 'User')
    if ($skillHits.Count -eq 0) {
        # No tool-specific dir to hook? The CLI is the hook: any agent runs it, so
        # "loment skill --print" needs no convention and no file access.
        Say "[5b/6] no known agent dir here -- the guide is still reachable:"
        Say "      loment skill --print     (any agent: run the CLI)"
        Say "      or paste into that agent's rules: read $skillSrc before writing Loment"
    } else {
        Say "[5b/6] agent skill -> $($skillHits -join ', ') (+ LOMENT_SKILL)"
    }
}

if ($NoFileType) { Say "[6/6] file type registration skipped (-NoFileType)"; exit 0 }
$editor = Find-Editor
if (-not $editor) {
    Say "[6/6] no editor found (VS Code) -- file type registration skipped."
    Say "      re-run with VS Code installed, or use tools/loment_filetype.py --register"
    exit 0
}
Register-FileType -Editor $editor -Icon $icon
Say ""
Say "installed. Try:  loment version"
'''

INSTALL_CMD = r'''@echo off
rem Loment @DISPLAY@ installer entry point. Works in BOTH layouts:
rem   * self-extracting setup.exe  -> payload.zip sits next to this file: unpack it first
rem   * plain .zip                 -> this directory IS the payload
rem Double-clicking this file installs with defaults; see README.md for the switches
rem (-Prefix, -NoPath, -NoFileType, -NoSkill, -DryRun, -Uninstall).
setlocal
rem #422: cmd.exe resolves a bare program name from the CURRENT DIRECTORY before PATH
rem unless this is set -- and the current directory is the unpack directory, which is
rem exactly what the archive's supplier controls. Belt and braces: invoke PowerShell by
rem absolute System32 path below; this closes the same search for anything else.
set "NoDefaultCurrentDirectoryInExePath=1"
set HERE=%~dp0
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" (
  echo install: Windows PowerShell not found at "%PS%"
  exit /b 1
)
set PSARGS=
if exist "%HERE%payload.zip" set PSARGS=-PayloadZip "%HERE%payload.zip"

rem Two faces, one installer. **No arguments = a double-click** -> the window (welcome.ps1),
rem which drives install.ps1 and shows what it says. **Any argument = a script** -> the plain
rem console path this file always had: CI, -DryRun, -Prefix, -Uninstall all land there, so
rem nothing that parses this output (or checks a Windows-only criterion) changes behaviour.
rem 2026-10-12: a double-click used to do *exactly* the right thing and show none of it -- the
rem console is not visible in that launch, so "it worked" and "it did nothing" looked the same.
if not "%~1"=="" goto console
if not exist "%HERE%welcome.ps1" goto console
"%PS%" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%HERE%welcome.ps1" %PSARGS%
exit /b %ERRORLEVEL%

:console
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1" %PSARGS% %*
rem String compare, not `if errorlevel 1` -- a crashed interpreter exits NEGATIVE and
rem cmd compares that as signed, so the batch test would read it as success.
if not "%ERRORLEVEL%"=="0" (
  echo.
  echo install failed. See README.md, or run it manually:
  echo   "%PS%" -ExecutionPolicy Bypass -File "%HERE%install.ps1" %PSARGS%
  pause
  exit /b 1
)
rem A double-click passes no arguments -- keep the window open so the result stays visible.
if "%~1"=="" (
  echo.
  pause
)
'''

#: 安装器的**脸**：`welcome.ps1`。它自己**不装任何东西** —— 它驱动 `install.ps1`，把那边
#: 说的话显示出来。一份安装逻辑、两张脸：命令行那张留给脚本与 CI（`install.cmd` 带参数时
#: 走的就是它，判据也全在那边），窗口这张留给双击。
#:
#: **为什么要有它**：2026-10-12 用户报「双击 setup.exe 毫无反应」。实测下来包解开了、
#: 脚本真跑了、东西真装上了 —— 只是那条路上**没有一个看得见的窗口**，于是「装好了」与
#: 「什么都没发生」长得一模一样，还留下卡在 `pause` 的僵尸 cmd。窗口不是装饰，是那句回执。
#:
#: `-Screenshot` / `-ShotWizard` 让脚本把某一页画进 PNG 再退出：判据靠它在这台机器上量
#: 「两页真的画得出来」，不必去截桌面（也就不会拍下不属于我们的东西）。
#: `-AutoRun` 自动按下 Install、装完自己关 —— 判据靠它把「窗口这条路」真跑一遍，
#: 不然窗口就只是一张图。
#: `-AutoRun` 自动按下 Install、装完自己关 —— 判据靠它把「窗口这条路」真跑一遍，
#: 不然窗口就只是一张图。
#: `-AutoRun` 自动按下 Install、装完自己关 —— 判据靠它把「窗口这条路」真跑一遍，
#: 不然窗口就只是一张图。
#: `-AutoRun` 自动按下 Install、装完自己关 —— 判据靠它把「窗口这条路」真跑一遍，
#: 不然窗口就只是一张图。
WELCOME_PS1 = r'''# welcome.ps1 - Loment setup, the visible half.
#
# ASCII only (PS 5.1 reads a BOM-less .ps1 as ANSI -- same rule as install.ps1).
# It installs nothing itself: it drives install.ps1 and shows what install.ps1 says.
# One implementation of the install, two faces -- the console one stays for scripts
# and CI (`install.cmd -DryRun` never reaches this file).
#
#   powershell -File welcome.ps1                        # splash -> wizard -> install
#   powershell -File welcome.ps1 -Screenshot OUT.png    # draw the splash, save, exit
#   powershell -File welcome.ps1 -ShotWizard OUT.png    # draw the wizard page, save, exit

[CmdletBinding()]
param(
    [string]$InstallPs1 = '',
    [string]$LogoPath = '',
    [string]$PayloadZip = '',
    [string]$Prefix = '',
    [string]$Screenshot = '',
    [string]$ShotWizard = '',
    [string]$ShotDone = '',
    [string]$Display = 'Loment @DISPLAY@',
    [switch]$DryRun,
    [switch]$AutoRun,
    [switch]$AutoClose,
    [switch]$NoPath,
    [switch]$NoFileType,
    [switch]$NoSkill,
    [switch]$Uninstall,
    [int]$HoldMs = 0
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
# Must come before the first control exists. Without it the controls render in the classic
# theme and -- the one that bit on 2026-10-12 -- a Marquee ProgressBar draws as an EMPTY
# bar: the install was running while the window looked like nothing was happening.
[System.Windows.Forms.Application]::EnableVisualStyles()

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($InstallPs1 -eq '') { $InstallPs1 = Join-Path $ScriptDir 'install.ps1' }
if ($LogoPath -eq '') { $LogoPath = Join-Path $ScriptDir 'loment-logo.png' }
if ($Prefix -eq '') { $Prefix = Join-Path $env:LOCALAPPDATA 'Loment' }

$VERSION = $Display

$PAPER = [System.Drawing.Color]::FromArgb(246, 247, 249)
$CARD = [System.Drawing.Color]::White
$INK = [System.Drawing.Color]::FromArgb(28, 32, 36)
$MUTE = [System.Drawing.Color]::FromArgb(122, 130, 140)
$EDGE = [System.Drawing.Color]::FromArgb(226, 229, 234)
$BLUE = [System.Drawing.Color]::FromArgb(79, 107, 224)
$BLUE_DK = [System.Drawing.Color]::FromArgb(64, 88, 196)

# ---- rounded-corner helpers: every box in this program goes through these
function New-RoundPath([int]$w, [int]$h, [int]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    if ($r -lt 1) { $r = 1 }
    $d = $r * 2
    $p.AddArc(0, 0, $d, $d, 180, 90)
    $p.AddArc($w - $d, 0, $d, $d, 270, 90)
    $p.AddArc($w - $d, $h - $d, $d, $d, 0, 90)
    $p.AddArc(0, $h - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    return $p
}
function Set-Round($ctl, [int]$r) {
    $w = $ctl.Width; $h = $ctl.Height
    if ($w -lt 2 -or $h -lt 2) { return }
    $path = New-RoundPath $w $h $r
    $ctl.Region = New-Object System.Drawing.Region($path)
}
function New-FlatButton([string]$text, [int]$w, [int]$h, $back, $fore, [int]$r) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.Size = New-Object System.Drawing.Size($w, $h)
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 0
    $b.BackColor = $back
    $b.ForeColor = $fore
    $b.Font = New-Object System.Drawing.Font('Segoe UI', 9.75)
    $b.Cursor = 'Hand'
    Set-Round $b $r
    return $b
}
function New-Badge([bool]$ok) {
    # Drawn, not a character: the file is ASCII, so a check mark cannot be a literal here.
    $s = 54
    $bmp = New-Object System.Drawing.Bitmap($s, $s)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $col = if ($ok) { [System.Drawing.Color]::FromArgb(38, 150, 88) } else { [System.Drawing.Color]::FromArgb(198, 62, 44) }
    $br = New-Object System.Drawing.SolidBrush($col)
    $g.FillEllipse($br, 0, 0, ($s - 2), ($s - 2))
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 5)
    $pen.StartCap = 'Round'
    $pen.EndCap = 'Round'
    if ($ok) {
        $pts = @(
            (New-Object System.Drawing.Point -ArgumentList @(14, 28)),
            (New-Object System.Drawing.Point -ArgumentList @(23, 37)),
            (New-Object System.Drawing.Point -ArgumentList @(40, 17))
        )
        $g.DrawLines($pen, $pts)
    } else {
        $g.DrawLine($pen, 17, 17, 37, 37)
        $g.DrawLine($pen, 37, 17, 17, 37)
    }
    $g.Dispose()
    $pb = New-Object System.Windows.Forms.PictureBox
    $pb.Image = $bmp
    $pb.Size = New-Object System.Drawing.Size($s, $s)
    $pb.BackColor = [System.Drawing.Color]::Transparent
    return $pb
}

$logo = $null
if (Test-Path -LiteralPath $LogoPath) {

    try {
        $logo = [System.Drawing.Image]::FromFile($LogoPath)
    } catch { $logo = $null }
}

# ---------------------------------------------------------------- splash

$SW = 520; $SH = 340; $SR = 22
$splash = New-Object System.Windows.Forms.Form
$splash.FormBorderStyle = 'None'
$splash.StartPosition = 'CenterScreen'
$splash.ClientSize = New-Object System.Drawing.Size($SW, $SH)
$splash.BackColor = $CARD
$splash.Text = $VERSION
$splash.Opacity = 0.0
$splash.Region = New-Object System.Drawing.Region((New-RoundPath $SW $SH $SR))

if ($logo -ne $null) {
    $pic = New-Object System.Windows.Forms.PictureBox
    $pic.Image = $logo
    $pic.SizeMode = 'Zoom'
    $pic.BackColor = [System.Drawing.Color]::Transparent
    $pic.Size = New-Object System.Drawing.Size(132, 132)
    $pic.Location = New-Object System.Drawing.Point -ArgumentList @(([int](($SW - 132) / 2)), 56)
    $splash.Controls.Add($pic)
    $y = 206
} else {
    $y = 130
}

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Welcome to Loment'
$title.Font = New-Object System.Drawing.Font('Segoe UI', 20)
$title.ForeColor = $INK
$title.BackColor = [System.Drawing.Color]::Transparent
$title.TextAlign = 'MiddleCenter'
$title.Size = New-Object System.Drawing.Size($SW, 40)
$title.Location = New-Object System.Drawing.Point -ArgumentList @(0, $y)
$splash.Controls.Add($title)

$sub = New-Object System.Windows.Forms.Label
$sub.Text = $VERSION + '  -  a systems programming language'
$sub.Font = New-Object System.Drawing.Font('Segoe UI', 9.5)
$sub.ForeColor = $MUTE
$sub.BackColor = [System.Drawing.Color]::Transparent
$sub.TextAlign = 'MiddleCenter'
$sub.Size = New-Object System.Drawing.Size($SW, 22)
$sub.Location = New-Object System.Drawing.Point -ArgumentList @(0, ($y + 44))
$splash.Controls.Add($sub)

if ($Screenshot -ne '') {
    $splash.Opacity = 1.0
    $splash.Show()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 200
    $bmp = New-Object System.Drawing.Bitmap($SW, $SH)
    $splash.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $SW, $SH)))
    $bmp.Save($Screenshot, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $splash.Close()
    Write-Host "wrote $Screenshot"
    exit 0
}

$state = @{ step = 0; hold = 0 }
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 16
$timer.Add_Tick({
        $op = $splash.Opacity
        if ($state.step -eq 0) {
            $op += 0.06
            if ($op -ge 1.0) { $op = 1.0; $state.step = 1 }
            $splash.Opacity = $op
        } elseif ($state.step -eq 1) {
            $state.step = 2
        } elseif ($state.step -eq 2) {
            $state.hold++
            if ($state.hold -gt (14 + [int]($HoldMs / 16))) { $state.step = 3 }
        } else {
            $op -= 0.08
            if ($op -le 0.0) { $splash.Opacity = 0.0; $timer.Stop(); $splash.Close() }
            $splash.Opacity = $op
        }
    })
$splash.Show()
$timer.Start()
while ($splash.Visible) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 8 }
$timer.Dispose()

# ---------------------------------------------------------------- wizard

$WW = 640; $WH = 452; $WR = 16
$win = New-Object System.Windows.Forms.Form
$win.Text = $VERSION + ' Setup'
$win.FormBorderStyle = 'None'
$win.StartPosition = 'CenterScreen'
$win.ClientSize = New-Object System.Drawing.Size($WW, $WH)
$win.BackColor = $PAPER
$win.Opacity = 0.0
$win.Region = New-Object System.Drawing.Region((New-RoundPath $WW $WH $WR))
try {
    if (Test-Path -LiteralPath (Join-Path $ScriptDir 'bin\loment.ico')) {
        $win.Icon = New-Object System.Drawing.Icon((Join-Path $ScriptDir 'bin\loment.ico'))
    }
} catch { }

# a borderless window has to be draggable by hand: the header is the handle
Add-Type -Namespace Native -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ReleaseCapture();
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wp, IntPtr lp);
'@
function Enable-Drag($ctl) {
    $ctl.Add_MouseDown({
            param($s, $e)
            if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
                [Native.Win]::ReleaseCapture() | Out-Null
                [Native.Win]::SendMessage($win.Handle, 0xA1, [IntPtr]2, [IntPtr]::Zero) | Out-Null
            }
        })
}

$head = New-Object System.Windows.Forms.Panel
$head.Size = New-Object System.Drawing.Size($WW, 62)
$head.Location = New-Object System.Drawing.Point(0, 0)
$head.BackColor = $CARD
$head.Region = New-Object System.Drawing.Region((New-RoundPath $WW 62 16))
Enable-Drag $head
$win.Controls.Add($head)

if ($logo -ne $null) {
    $hp = New-Object System.Windows.Forms.PictureBox
    $hp.Image = $logo
    $hp.SizeMode = 'Zoom'
    $hp.BackColor = [System.Drawing.Color]::Transparent
    $hp.Size = New-Object System.Drawing.Size(30, 30)
    $hp.Location = New-Object System.Drawing.Point(22, 16)
    $head.Controls.Add($hp)
    Enable-Drag $hp
}

$ht = New-Object System.Windows.Forms.Label
$ht.Text = $VERSION + ' Setup'
$ht.Font = New-Object System.Drawing.Font('Segoe UI', 12)
$ht.ForeColor = $INK
$ht.BackColor = [System.Drawing.Color]::Transparent
$ht.Size = New-Object System.Drawing.Size(420, 30)
$ht.Location = New-Object System.Drawing.Point(62, 16)
$head.Controls.Add($ht)
Enable-Drag $ht

$btnX = New-FlatButton 'x' 34 30 $CARD $MUTE 10
$btnX.Location = New-Object System.Drawing.Point -ArgumentList @(($WW - 48), 16)
$btnX.Add_Click({ $win.Close() })
$head.Controls.Add($btnX)

$body = New-Object System.Windows.Forms.Panel
$body.Size = New-Object System.Drawing.Size(($WW - 48), ($WH - 62 - 92))
$body.Location = New-Object System.Drawing.Point(24, 74)
$body.BackColor = $CARD
$body.Region = New-Object System.Drawing.Region((New-RoundPath ($WW - 48) ($WH - 62 - 92) 12))
$win.Controls.Add($body)

$foot = New-Object System.Windows.Forms.Panel
$foot.Size = New-Object System.Drawing.Size($WW, 68)
$foot.Location = New-Object System.Drawing.Point(0, ($WH - 68))
$foot.BackColor = [System.Drawing.Color]::Transparent
$win.Controls.Add($foot)

$btnInstall = New-FlatButton 'Install' 118 34 $BLUE ([System.Drawing.Color]::White) 10
$btnInstall.Location = New-Object System.Drawing.Point -ArgumentList @(($WW - 24 - 118), 18)
$foot.Controls.Add($btnInstall)

$btnBack = New-FlatButton 'Back' 92 34 $CARD $INK 10
$btnBack.Location = New-Object System.Drawing.Point -ArgumentList @(($WW - 24 - 118 - 10 - 92), 18)
$btnBack.Enabled = $false
$foot.Controls.Add($btnBack)

$btnCancel = New-FlatButton 'Cancel' 92 34 $CARD $MUTE 10
$btnCancel.Location = New-Object System.Drawing.Point -ArgumentList @(($WW - 24 - 118 - 10 - 92 - 10 - 92), 18)
$foot.Controls.Add($btnCancel)

# ---- page 1: what and where
$lblTo = New-Object System.Windows.Forms.Label
$lblTo.Text = 'Install Loment to'
$lblTo.Font = New-Object System.Drawing.Font('Segoe UI', 10.5)
$lblTo.ForeColor = $INK
$lblTo.BackColor = [System.Drawing.Color]::Transparent
$lblTo.Size = New-Object System.Drawing.Size(240, 24)
$lblTo.Location = New-Object System.Drawing.Point(28, 26)
$body.Controls.Add($lblTo)

$txtPrefix = New-Object System.Windows.Forms.TextBox
$txtPrefix.Text = $Prefix
$txtPrefix.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$txtPrefix.BorderStyle = 'FixedSingle'
$txtPrefix.Size = New-Object System.Drawing.Size(430, 26)
$txtPrefix.Location = New-Object System.Drawing.Point(28, 54)
$body.Controls.Add($txtPrefix)

$btnBrowse = New-FlatButton 'Change...' 106 28 ([System.Drawing.Color]::FromArgb(237, 240, 245)) $INK 8
$btnBrowse.Location = New-Object System.Drawing.Point(468, 53)
$body.Controls.Add($btnBrowse)

$chkPath = New-Object System.Windows.Forms.CheckBox
$chkPath.Text = 'Add Loment to PATH'
$chkPath.Checked = -not $NoPath
$chkPath.Font = New-Object System.Drawing.Font('Segoe UI', 9.75)
$chkPath.ForeColor = $INK
$chkPath.BackColor = [System.Drawing.Color]::Transparent
$chkPath.Size = New-Object System.Drawing.Size(320, 24)
$chkPath.Location = New-Object System.Drawing.Point(28, 104)
$body.Controls.Add($chkPath)

$chkType = New-Object System.Windows.Forms.CheckBox
$chkType.Text = 'Open .lomt and .lom files with Loment'
$chkType.Checked = -not $NoFileType
$chkType.Font = New-Object System.Drawing.Font('Segoe UI', 9.75)
$chkType.ForeColor = $INK
$chkType.BackColor = [System.Drawing.Color]::Transparent
$chkType.Size = New-Object System.Drawing.Size(400, 24)
$chkType.Location = New-Object System.Drawing.Point(28, 132)
$body.Controls.Add($chkType)

$chkSkill = New-Object System.Windows.Forms.CheckBox
$chkSkill.Text = 'Install the agent guide (SKILL.md)'
$chkSkill.Checked = -not $NoSkill
$chkSkill.Font = New-Object System.Drawing.Font('Segoe UI', 9.75)
$chkSkill.ForeColor = $INK
$chkSkill.BackColor = [System.Drawing.Color]::Transparent
$chkSkill.Size = New-Object System.Drawing.Size(400, 24)
$chkSkill.Location = New-Object System.Drawing.Point(28, 160)
$body.Controls.Add($chkSkill)

$note = New-Object System.Windows.Forms.Label
$note.Text = 'No Python, no clang, no WSL - native binaries and the standard library.'
$note.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$note.ForeColor = $MUTE
$note.BackColor = [System.Drawing.Color]::Transparent
$note.Size = New-Object System.Drawing.Size(540, 22)
$note.Location = New-Object System.Drawing.Point(28, 200)
$body.Controls.Add($note)

$script:page = 1
$script:ui = $null

function Show-ProgressPage {
    $body.Controls.Clear()
    $l = New-Object System.Windows.Forms.Label
    $l.Text = 'Installing'
    $l.Font = New-Object System.Drawing.Font('Segoe UI', 10.5)
    $l.ForeColor = $INK
    $l.BackColor = [System.Drawing.Color]::Transparent
    $l.Size = New-Object System.Drawing.Size(200, 24)
    $l.Location = New-Object System.Drawing.Point(28, 24)
    $body.Controls.Add($l)

    $bar = New-Object System.Windows.Forms.ProgressBar
    $bar.Style = 'Marquee'
    $bar.MarqueeAnimationSpeed = 24
    $bar.Size = New-Object System.Drawing.Size(($WW - 48 - 56), 12)
    $bar.Location = New-Object System.Drawing.Point(28, 54)
    $body.Controls.Add($bar)

    $log = New-Object System.Windows.Forms.TextBox
    $log.Multiline = $true
    $log.ReadOnly = $true
    $log.ScrollBars = 'Vertical'
    $log.BorderStyle = 'None'
    $log.Font = New-Object System.Drawing.Font('Consolas', 8.5)
    $log.BackColor = $PAPER
    $log.ForeColor = [System.Drawing.Color]::FromArgb(70, 76, 84)
    $log.Size = New-Object System.Drawing.Size(($WW - 48 - 56), 178)
    $log.Location = New-Object System.Drawing.Point(28, 78)
    $body.Controls.Add($log)
    return @{ bar = $bar; log = $log; title = $l }
}

function Fade-In {
    for ($i = 0; $i -le 14; $i++) {
        $win.Opacity = $i / 14.0
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 11
    }
    $win.Opacity = 1.0
}

$btnBrowse.Add_Click({
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = 'Where should Loment be installed?'
        $dlg.SelectedPath = $txtPrefix.Text
        if ($dlg.ShowDialog() -eq 'OK') { $txtPrefix.Text = $dlg.SelectedPath }
    })
$btnCancel.Add_Click({ $win.Close() })

$btnInstall.Add_Click({
        if ($script:page -eq 1) {
            $script:page = 2
            $ui = Show-ProgressPage
            $script:ui = $ui
            $btnBack.Enabled = $false
            $btnInstall.Enabled = $false
            $btnCancel.Text = 'Hide'

            $argv = @('-Prefix', $txtPrefix.Text)
            if (-not $chkPath.Checked) { $argv += '-NoPath' }
            if (-not $chkType.Checked) { $argv += '-NoFileType' }
            if (-not $chkSkill.Checked) { $argv += '-NoSkill' }
            # In the self-extracting layout the payload sits inside payload.zip, not next to
            # this file, so this has to be forwarded: without it the installer finds no
            # checksum list beside the payload and refuses the package as incomplete.
            if ($PayloadZip -ne '') { $argv += @('-PayloadZip', $PayloadZip) }
            if ($DryRun) { $argv += '-DryRun' }
            if ($Uninstall) { $argv += '-Uninstall' }

            # The child writes its own log; the window reads the file. Deliberately NOT
            # BeginOutputReadLine + a PowerShell event handler: that handler runs on a
            # threadpool thread, and a scriptblock invoked there is not safe while the UI
            # thread sits inside ShowDialog -- measured 2026-10-12: the window died the
            # instant the child exited (exit 2, no message, no tick ever ran). A file has no
            # threads in it.
            $logFile = Join-Path $env:TEMP ('loment-setup-' + $PID + '.log')
            Remove-Item -LiteralPath $logFile -ErrorAction SilentlyContinue
            $q = { param($t) "'" + $t.Replace("'", "''") + "'" }
            # Quote the VALUES, never the switches: `'-Prefix'` is not a parameter name, and
            # PowerShell answers with PositionalParameterNotFound before install.ps1 runs a
            # single line -- which is exactly how "it did not install and the log is empty"
            # looked on 2026-10-12.
            $parts = @()
            foreach ($a in $argv) {
                if ($a.StartsWith('-')) { $parts += $a } else { $parts += (& $q $a) }
            }
            # A terminating error (throw / $ErrorActionPreference='Stop') does NOT go into a
            # `*>` redirection -- the engine reports it at the host instead. That is how the
            # 0.1.5 setup.exe showed "Install failed (exit 1) -- see the log above" over an
            # EMPTY log while install.ps1 had a very clear message for the console nobody
            # could see. The catch writes that reason into the same file.
            $inner = 'try { & ' + (& $q $InstallPs1) + ' ' + ($parts -join ' ') + ' *> ' + (& $q $logFile)
            $inner += ' } catch { ($_ | Out-String) | Out-File -LiteralPath ' + (& $q $logFile) + ' -Append; exit 1 }'
            $ps = New-Object System.Diagnostics.Process
            # #422: never a bare name -- CreateProcess searches the current directory,
            # which is the unpack directory an archive supplier controls.
            $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            if (-not (Test-Path -LiteralPath $psExe)) { throw "Windows PowerShell not found at $psExe" }
            $ps.StartInfo.FileName = $psExe
            $ps.StartInfo.Arguments = '-NoProfile -ExecutionPolicy Bypass -Command "' + $inner + '"'
            $ps.StartInfo.UseShellExecute = $false
            $ps.StartInfo.CreateNoWindow = $true
            $ps.Start() | Out-Null
            # Every handler local the timer needs has to live in script scope: the timer's
            # scriptblock runs later, from the message loop, and does NOT see the click
            # handler's locals. Miss one and it reads $null -- that is how the window sat on
            # "Installing" forever on 2026-10-12 while the install itself had already
            # finished.
            $script:proc = $ps
            $script:logFile = $logFile
            $script:lastTxt = ''

            $tick = New-Object System.Windows.Forms.Timer
            $tick.Interval = 120
            $tick.Add_Tick({
                    $txt = ''
                    if (Test-Path -LiteralPath $logFile) {
                        try { $txt = [string](Get-Content -LiteralPath $logFile -Raw -ErrorAction Stop) } catch { $txt = $script:lastTxt }
                    }
                    if ($txt -ne $script:lastTxt) {
                        $script:lastTxt = $txt
                        $ui.log.Text = $txt
                        $ui.log.SelectionStart = $ui.log.Text.Length
                        $ui.log.ScrollToCaret()
                    }
                    if ($script:proc.HasExited) {
                        $tick.Stop()
                        $code = $script:proc.ExitCode
                        $script:rc = $code
                        $ok = ($code -eq 0)

                        # The receipt. This is the whole point of the window: say what happened
                        # and then WAIT -- the window closes when the person closes it, never on
                        # its own. (-AutoClose exists only so a criterion can run this file.)
                        $ui.title.Text = 'Finished'
                        $ui.bar.Style = 'Continuous'
                        $ui.bar.Value = 100
                        $ui.log.Size = New-Object System.Drawing.Size(($WW - 48 - 56), 116)

                        $badge = New-Badge $ok
                        $badge.Location = New-Object System.Drawing.Point(30, 206)
                        $body.Controls.Add($badge)

                        $res = New-Object System.Windows.Forms.Label
                        if ($ok) { $res.Text = $VERSION + ' is installed.' }
                        else { $res.Text = 'Install failed (exit ' + $code + ').' }
                        $res.Font = New-Object System.Drawing.Font('Segoe UI', 12.5)
                        $res.ForeColor = if ($ok) { [System.Drawing.Color]::FromArgb(38, 132, 78) } else { [System.Drawing.Color]::FromArgb(190, 60, 40) }
                        $res.BackColor = [System.Drawing.Color]::Transparent
                        $res.Size = New-Object System.Drawing.Size(470, 28)
                        $res.Location = New-Object System.Drawing.Point(94, 206)
                        $body.Controls.Add($res)

                        $hint = New-Object System.Windows.Forms.Label
                        if ($ok) { $hint.Text = 'Open a new terminal and try:  loment version' }
                        else { $hint.Text = 'Nothing was left half-written -- see the log above.' }
                        $hint.Font = New-Object System.Drawing.Font('Segoe UI', 9.5)
                        $hint.ForeColor = $MUTE
                        $hint.BackColor = [System.Drawing.Color]::Transparent
                        $hint.Size = New-Object System.Drawing.Size(470, 24)
                        $hint.Location = New-Object System.Drawing.Point(94, 234)
                        $body.Controls.Add($hint)

                        $btnInstall.Enabled = $true
                        $btnInstall.Text = 'Close'
                        $btnCancel.Visible = $false
                        $script:page = 3
                        if ($ShotDone -ne '') {
                            [System.Windows.Forms.Application]::DoEvents()
                            Start-Sleep -Milliseconds 250
                            $bmp = New-Object System.Drawing.Bitmap($WW, $WH)
                            $win.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $WW, $WH)))
                            $bmp.Save($ShotDone, [System.Drawing.Imaging.ImageFormat]::Png)
                            $bmp.Dispose()
                            Write-Host "wrote $ShotDone"
                            $win.Close()
                        }
                        if ($AutoClose) { $win.Close() }
                    }
                })
            $tick.Start()
            $script:tick = $tick
        } else {
            $win.Close()
        }
    })

if ($ShotWizard -ne '') {
    $win.Opacity = 1.0
    $win.Show()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 250
    $bmp = New-Object System.Drawing.Bitmap($WW, $WH)
    $win.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $WW, $WH)))
    $bmp.Save($ShotWizard, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $win.Close()
    Write-Host "wrote $ShotWizard"
    exit 0
}

$script:rc = 0
$win.Add_Shown({
        Fade-In
        # -AutoRun: press Install for the user and close when the install ends. It is how a
        # criterion exercises this file at all -- a window nobody clicks is not a test.
        if ($AutoRun) { $btnInstall.PerformClick() }
    })
[void]$win.ShowDialog()
# Carry the install's own exit code out, so a script that launched the window can still
# tell whether the install worked. Nothing installed -> 0.
exit $script:rc
'''


README_MD = """# Loment {DISPLAY}

版本 `{VERSION}`。这是一份**自包含**的 Loment 工具链发行包：包里**没有 Python**，也
**不需要 clang、不需要 WSL** —— 八个可执行文件都是自举产物，`build`/`run` 用包内的
`loment-lomelf` 在本机直接出 ELF/PE。构建与安装的全部细节见仓库 `docs/162`。

## 包内容

| 文件 | 作用 |
| --- | --- |
| `bin/loment-driver` | 编译器（装载 → 检查 → 发射 LLVM IR）；自举链的 stage1 |
| `bin/loment-lsp` | 语言服务（补全/跳转/诊断/`--check`），stdio 上的 LSP |
| `bin/loment-fmt` | 格式化器（与 Python 版逐字节相同，docs/159） |
| `bin/loment-doc` | API 文档生成器 |
| `bin/loment` | 启动器（下面那些子命令） |
| `bin/loment-lomelf` | 链接器：把 `.ll` 变成可执行文件（`build`/`run` 用它） |
| `bin/loment-cli` | 命令前端：`help` / `codes` / `stat` / `grep` / `ls` / `tree` / …（Loment 自己写的，`loment/tools/lomcli.lomt`） |
| `bin/lomenterr` | 报错器：吃编译器 `--diag-out` 的 JSONL，补上标题、源行与修复建议（`loment/tools/lomenterr.lomt`，`docs/182`）。**由启动器自动调用**，不是 `loment` 的子命令。它有**自己的版本号**（与语言版本是两条线）：`lomenterr version` |
| `bin/lompi` | **Loment 库的包管理器**（Loment 自己写的，`lompi/`）。**独立命令，不是 `loment` 的子命令** —— `loment help` 里没有它，直接敲 `lompi` |
| `share/loment/seed.ll` | 自举种子：只用 clang 就能从它重建整套工具链 |
| `share/loment/examples/user_hello.lomt` | 示例程序（用 syscall 打印） |
| `share/loment/skill/SKILL.md` | **给 AI agent 的 Loment 说明书**（见下） |
| `share/lompi/skill/SKILL.md` | **lompi 的说明书**（同一种装法：进 `~/.claude/skills/lompi/`，并往 Codex 的 AGENTS.md 写指针） |
| `share/lompi/store/` | **lompi 的标准库**：`std` / `host` / `internet` / `os` 四个包，共 **160** 个文件（这个数**钉在** `loment_lompi_test.STORE_FILES`，别在这行手抄第二份）。装的时候会一并拷进 **lompi 自己认的全局 store**（问 `lompi config`），装完就能直接 `lompi index` / `use std` |

## 安装（三种方式，装出来一样）

**① 命令安装 · Linux / WSL**

```sh
tar xzf loment-{VERSION}-linux-x64.tar.gz
cd loment-{VERSION}-linux-x64
sh install.sh                        # 默认装到 ~/.local
sh install.sh --prefix /opt/loment   # 换前缀
sh install.sh --uninstall
```

**② 命令安装 · Windows / PowerShell**

```powershell
Expand-Archive loment-{VERSION}-windows-x64.zip -DestinationPath .
cd loment-{VERSION}-windows-x64
powershell -ExecutionPolicy Bypass -File install.ps1
# 可选: -Prefix D:\\Loment
#       -NoPath  -NoFileType  -NoSkill  -DryRun  -Uninstall
```

也可以直接**双击 `install.cmd`**（按默认参数装；`install.cmd` 在 zip 布局与自解压布局里都能用）。
**不带参数的双击会开一个窗口**（`welcome.ps1`：渐入 logo → 选装到哪 → 装完给回执），
带参数时仍是下面那条命令行、输出照旧 —— 脚本与 CI 走的那条一点没变。
遇到"被策略阻止"时用上面那条 `-ExecutionPolicy Bypass` 的命令。

**③ 安装包安装 · Windows 双击**

```text
loment-{VERSION}-windows-x64-setup.exe
```

自解压安装包（用 Windows 自带的 `iexpress` 做，不引第三方工具）：双击即装 ——
校验 SHA256SUMS → 把工具链拷进安装前缀 → 写 `loment.cmd` → 加用户 PATH →
装 `.lomt`/`.lom` 文件类型 → 冒烟测试。**未签名**，SmartScreen 会提示"未知发布者"，
选"更多信息 → 仍要运行"。

## 让 AI agent 写 Loment

包里带一份**自足**的 skill（`share/loment/skill/SKILL.md`）：内建函数表、语法、
错误码表、以及这个包自己的命令，都在里面，不依赖源码仓库。

安装时它会同时被写进 **`~/.claude/skills/loment/`**（用户级），于是**任何工程**里的
Claude Code 都能读到它 —— 你只要说"用 Loment 写个程序"就行。不想装用 `-NoSkill`
（Linux: `--no-skill`）；没装 Claude 的话它会留在包里，把那个文件拷到
`<你的工程>/.claude/skills/loment/SKILL.md` 也一样。卸载时一并摘掉。

**既没有 Claude 也没有 Codex？** 那就不靠目录约定 —— **跑 CLI 就行**：

```sh
loment skill            # 打印指南路径
loment skill --print    # 直接把指南全文打到 stdout
```

`loment help` 的用法里也印了这一行，所以任何 agent 上手这门语言的第一条命令
（`loment --help`）就能看到入口 —— 与它是什么工具无关。

## 用法

```sh
loment version                     # 版本
loment ir demo.lomt                # 编译到 LLVM IR（stdout）
loment check demo.lomt             # 只做检查（不打印 IR）
loment run demo.lomt               # 编译 + 链接 + 运行
loment build demo.lomt -o demo     # 只出可执行文件
loment fmt demo.lomt               # 格式化
loment doc demo.lomt               # 生成 API 文档
loment lsp                         # 语言服务（编辑器用）
```

**前置条件**：包里就是目标平台自己的可执行文件，**没有任何外部依赖** —— 不需要 clang，
Windows 包也不需要 WSL。`run`/`build` 由包内的自举链接器 `loment-lomelf` 出产物：
Linux 出 ELF、Windows 出 PE。

**Windows 说明**：装到 `$Prefix`（默认 `%LOCALAPPDATA%\Loment`），`bin\loment.cmd`
直接调那些 `.exe`。

## 撤销

```sh
sh install.sh --uninstall                  # Linux / WSL
powershell -File install.ps1 -Uninstall    # Windows（同时清 PATH、文件类型与 agent skill）
```
"""


# ------------------------------------------------------------------ 自举构建

def _lomelf_link(ir_text: str, target: str) -> bytes:
    """IR -> 可执行文件。用仓库自己的原生后端（`tools/lomelf.py`），**不再经 clang**。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lomelf
    fn = lomelf.compile_pe if target == "pe" else lomelf.compile_ll
    return fn(ir_text)[0]


def _host_target() -> str:
    return "pe" if sys.platform == "win32" else "elf"


#: clang 的位置 —— 与 `loment_opt_obj.py` 是**同一张表**（换机器只改一处口径）。
CLANG_CANDIDATES = ("clang", r"C:\Program Files\LLVM\bin\clang.exe")


def clang_path() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


#: 优化器可能合成出来的三个 libcall（`docs/212` §5A）。
#:
#: `-O2` 会把"按字节铺/搬"的循环改写成对 `memset` / `memcpy` / `memmove` 的调用 —— 它既
#: 认得出 Loment 运行期里那两段循环，也认得出**用户代码**里同样的循环。Loment 是无 libc
#: 的，于是链接期就是 `undefined symbol: memset`。实测：`lomelf` / `lomcli` / `driver`
#: 三个入口全栽在这一条上（另外四个入口没有这种循环，所以一直是绿的）。
#:
#: 三条都是**公开定义**，不能是 `internal` —— `internal` 满足不了 LLVM 合成到**外部**
#: 名字上的调用（实测仍然 `undefined symbol`）。签名与 libcall 逐字对齐（返回 `ptr`、
#: 长度 `i64`、填充值 `i32`）；`memcpy` 只按正方向搬（它的契约就是"不重叠"），`memmove`
#: 按方向 —— 两者**故意不写成同一个函数体**，否则 mergefunc 会把它们并成一个，再在其中
#: 一条上长出调用来。
#:
#: **为什么补在链接这一步，而不是补进 IR 运行期**：那份运行期在**两个实现**里
#: （`tools/lomentc.py` 与 `loment/selfhost/codegen.lomt`，须逐字节一致），改它就要连带
#: 重出种子、**重出提交进仓库的 genesis 二进制**、再重算两份 SHA256SUMS。而这条路
#: （clang 链）的产物根本不经过 lomelf，前端一行都不用动 —— 那就别动。
#:
#: 也不进 `.o`（粒度 B）：那个 `.o` 是给**没有 clang**的端机用的，符号在这里补齐正是
#: "端机不必有编译器"的意思。两边都补反而会重复定义。
_FREESTANDING_LIBCALLS = """\
define ptr @memset(ptr %p, i32 %v, i64 %n) {
entry:
  %vb = trunc i32 %v to i8
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %q = getelementptr i8, ptr %p, i64 %i
  store i8 %vb, ptr %q
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret ptr %p
}

define ptr @memcpy(ptr %d, ptr %s, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %sp = getelementptr i8, ptr %s, i64 %i
  %b = load i8, ptr %sp
  %dp = getelementptr i8, ptr %d, i64 %i
  store i8 %b, ptr %dp
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret ptr %d
}

define ptr @memmove(ptr %d, ptr %s, i64 %n) {
entry:
  %fwdok = icmp ult ptr %d, %s
  br i1 %fwdok, label %fwd, label %chk
chk:
  %se = getelementptr i8, ptr %s, i64 %n
  %bwdok = icmp uge ptr %d, %se
  br i1 %bwdok, label %fwd, label %bwd
fwd:
  br label %floop
floop:
  %i = phi i64 [ 0, %fwd ], [ %i1, %fbody ]
  %fdone = icmp uge i64 %i, %n
  br i1 %fdone, label %end, label %fbody
fbody:
  %fsp = getelementptr i8, ptr %s, i64 %i
  %fb = load i8, ptr %fsp
  %fdp = getelementptr i8, ptr %d, i64 %i
  store i8 %fb, ptr %fdp
  %i1 = add i64 %i, 1
  br label %floop
bwd:
  br label %bloop
bloop:
  %j = phi i64 [ %n, %bwd ], [ %j1, %bbody ]
  %jz = icmp eq i64 %j, 0
  br i1 %jz, label %end, label %bbody
bbody:
  %j1 = sub i64 %j, 1
  %bsp = getelementptr i8, ptr %s, i64 %j1
  %bb = load i8, ptr %bsp
  %bdp = getelementptr i8, ptr %d, i64 %j1
  store i8 %bb, ptr %bdp
  br label %bloop
end:
  ret ptr %d
}
"""


def _link_opt(ir_text: str, cc: str) -> tuple[bytes | None, str]:
    """IR -> **clang -O2** 出来的 ELF 可执行文件（docs/212 §5A）。

    返回 `(字节, "")` 或 `(None, 第一行诊断)`。**降级必须吵** —— clang 拒一份 IR 是
    "我们这个代码生成器发了非法 IR"的信号（实测就是这么抓到的），静默退回 lomelf 会让
    它永远看不见。诊断只取第一行：那是 `文件:行: 错误`，够定位了。

    这是**打包机上的那一次**：整程序交给 clang，与 C / Rust 用的是同一个优化器。命令行与
    包内启动器的 `--opt` 逐字相同 —— 否则"包里那个 lompi"和"你本地 `loment build --opt`
    出来的"会是两种不同的东西，而两边都自称同一条逃生舱。

    PE 那一半**不走这里**：这个包的 PE 形状由自举链接器决定，不是 clang 出的。所以
    `--opt` 只让 ELF 侧变快，Windows 侧维持 lomelf —— 这是如实的不对称，不是遗漏。
    """
    with tempfile.TemporaryDirectory(prefix="optdist-") as td:
        ll = Path(td) / "u.ll"
        ll.write_text(ir_text, encoding="utf-8", newline="\n")
        lc = Path(td) / "libcalls.ll"
        lc.write_text(_FREESTANDING_LIBCALLS, encoding="utf-8", newline="\n")
        out = Path(td) / "u.bin"
        r = subprocess.run([cc, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                            "-ffreestanding", "-static", "-fno-pie", "-O2",
                            str(ll), str(lc), "-o", str(out)],
                           capture_output=True, text=True, shell=False,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not out.exists():
            err = next((l.strip() for l in (r.stderr or "").splitlines() if l.strip()), "")
            return None, err or f"clang 退出码 {r.returncode}"
        return out.read_bytes(), ""


def _write_shared(path: Path, data: bytes) -> Path:
    """把共享产物写进 `STAGE`：**内容一样就一个字节都不写**, 否则原子换入。

    **为什么必须有这一条** (2026-09-17, 并行门禁撞出来的): `STAGE` 是**跨检查共享**的
    (`loment_cli_test` / `loment_lompi_test` / `loment_dist_test` 都用 stage1), 而原先
    这里**无条件重写** —— 一条判据正**执行**着 `stage1.exe`, 另一条把它重写掉, Windows 上
    后面那次调用就 `PermissionError: [Errno 13]`。实测 `loment_lompi_test` 从 20/20 掉到
    **10/20**, 就是这么来的。

    稳态下内容不变 ⇒ 零写入 ⇒ 竞态根本不存在 (只有第一次构建会写, 而那时还没有人能
    执行它)。写的时候用 `.pid` 临时名 + `os.replace`, 于是读方**永远看不到写了一半的文件**。
    """
    if path.exists():
        try:
            if path.read_bytes() == data:
                return path
        except OSError:                     # 别人正在换入: 当"内容不确定", 走完整路径
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def build_stage1() -> Path:
    """种子 -> stage1（种子是 driver 的定点, 所以 stage1 就是驱动自身）。

    stage1 按**本机**格式出：在 Windows 上出 PE，这样它直接就能跑，不必再去借 WSL。
    """
    if not ACTIVE_SEED.exists():
        raise SystemExit(f"missing seed {ACTIVE_SEED.relative_to(ROOT)} (docs/159)")
    STAGE.mkdir(parents=True, exist_ok=True)
    tgt = _host_target()
    stage1 = STAGE / ("stage1.exe" if tgt == "pe" else "stage1.elf")
    p = _write_shared(stage1, _lomelf_link(ACTIVE_SEED.read_text(encoding="utf-8"), tgt))
    # Linux 那支出来的是 ELF, 而 `emit_ir` **马上要执行它** —— 没有可执行位就是
    # `PermissionError: [Errno 13]`。Windows 那支是 PE, 而 PE 的执行不看这一位, 所以
    # 本机一直没暴露 (2026-09-22 把门禁搬上 CI, 第一条红就是它)。
    if tgt == "elf":
        p.chmod(0o755)
    return p


def emit_ir(stage1: Path, entry: str, cwd: str = ".") -> Path:
    """用 stage1 编译 entry → IR 落盘（**在本机直接跑**，不经 WSL）。

    cwd 是**编译时的工作目录**，也是传给 stage1 的入口路径的基准 —— 自举镜按 CWD 解析
    路径形式的 `use "..."`，所以入口要用相对 cwd 的名字（见 TOOLS 的注解）。
    """
    STAGE.mkdir(parents=True, exist_ok=True)
    out = STAGE / (Path(entry).stem + ".ll")
    base = (ROOT / cwd).resolve()
    # 入口路径要**相对 cwd** 给 stage1 —— 只取 basename 只对"入口就在 cwd 下"成立,
    # 而 `.` 那组的入口在 `loment/selfhost/`、`loment/tools/` 下 (2026-09-15 踩过:
    # `--only lompi` 没走到 `.` 那组所以没暴露, 全量 --emit 才炸)。
    rel = os.path.relpath((ROOT / entry).resolve(), base)
    r = subprocess.run([str(stage1), rel], capture_output=True,
                       cwd=str(base), shell=False)
    if r.returncode != 0 or not r.stdout:
        raise SystemExit(f"stage1 failed on {entry}: {r.stderr[-400:]!r}")
    return _write_shared(out, r.stdout)     # 共享产物: 见 _write_shared 的说明


#: 包里唯一一个**不现场编译**的工具。它的入口就是自举驱动 `loment/selfhost/driver.lomt`，
#: 而它的 IR **已经作为 `SEED` 提交在仓里** —— 种子是这个入口的**定点**（docs/159）：
#: `loment_seed_test` 钉住"种子 == 参考实现为 driver.lomt 发射的 IR"，`loment/bootstrap.sh`
#: 钉住"stage1 编译 driver.lomt == 种子"。所以在这里再用 stage1 重编一遍，买到的是
#: **同一件事的第二遍**，而它是整条 `--emit` 里最贵的一步：本机 273s（`build_tools` 的
#: 76%），CI 上约 700s —— 占 `loment_dist_test` 那 954s 的四分之三（实测见 docs/213）。
#: 定点本身仍被上面两条判据守着，只是不再由**发行包判据**重复付账。
SEED_TOOL = "loment-driver"


def build_tools(only: set[str] | None, opt: bool = False) -> dict[str, tuple[bytes, bytes]]:
    """名字 -> (Linux ELF 字节, Windows PE 字节)。

    IR 是**目标无关**的，所以每个入口的 IR 只算一次，同一份再各链一遍 —— 两个平台的包
    都能从本机构建出来，不需要另一个平台、也不需要 WSL。

    `SEED_TOOL` 的 IR 直接取 `SEED`（见上）；其余入口由 stage1 现场编译，且**并发**跑
    —— 它们彼此独立（各写各的 `STAGE/<name>.ll`）。并发的收益全在 `emit_ir` 那些
    **子进程**上；`_lomelf_link` 是纯 Python，GIL 下并不并行，但它总共才几秒
    （最大的 3.2MB IR 也只要 0.8s），不值得为它上进程池。

    `opt=True` 时**ELF 那一半**改走 clang -O2（docs/212 §5A）；PE 那一半不动（见
    `_link_opt` 的注解）。没有 clang 就**降级并说清楚**，不失败 —— 与启动器的 `--opt`
    同一条口径（"装上 clang 才更快，没有它照样发得出去"）。
    """
    todo = [(n, e, c) for n, e, c in TOOLS if not only or n in only]
    if not todo:
        return {}
    if any(n == SEED_TOOL for n, _e, _c in todo) and not ACTIVE_SEED.exists():
        raise SystemExit(f"missing seed {ACTIVE_SEED.relative_to(ROOT)} (docs/159)")
    # stage1 只在真需要现场编译时才造 —— 于是 `--only driver` 是秒级
    stage1 = None if all(n == SEED_TOOL for n, _e, _c in todo) else build_stage1()
    cc = clang_path() if opt else None
    if opt and cc is None:
        print("  [--opt] 没找到 clang —— 退回未优化的 lomelf 后端（包照样出得来）")
    optimized: list[str] = []

    def one(name: str, entry: str, cwd: str) -> tuple[str, bytes, bytes]:
        text = (ACTIVE_SEED if name == SEED_TOOL else emit_ir(stage1, entry, cwd)
                ).read_text(encoding="utf-8")
        elf, why = _link_opt(text, cc) if cc else (None, "")
        if elf is None:
            elf = _lomelf_link(text, "elf")
            if cc:
                print(f"  [{name}] --opt 降级：clang 没收下这份 IR —— {why}")
        else:
            optimized.append(name)
        return name, elf, _lomelf_link(text, "pe")

    jobs = max(1, min(4, (os.cpu_count() or 1)))
    if jobs == 1 or len(todo) == 1:
        made = [one(*t) for t in todo]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            made = list(ex.map(lambda t: one(*t), todo))

    out: dict[str, tuple[bytes, bytes]] = {}
    for name, elf, pe in made:      # **按 TOOLS 顺序落盘 —— 发布清单的顺序是判据**
        out[name] = (elf, pe)
        print(f"  [{name}] elf {len(elf)} 字节 / pe {len(pe)} 字节")
    if opt:
        print(f"  [--opt] clang -O2 了 {len(optimized)}/{len(out)} 个入口"
              + (f"（{' '.join(optimized)}）" if optimized else ""))
    return out


# ------------------------------------------------------------------ payload

def _read(p: str) -> bytes:
    return (ROOT / p).read_bytes()


def logo_png() -> bytes:
    """`editors/loment.ico` 里最大的那一帧 —— 纯 stdlib 解 ICO 目录，帧本身就是 PNG。

    **不做缩放**：ico 里已经有 16/24/32/48/64/128/256 七档，安装界面要 30 与 132 两个尺寸，
    让 GDI+ 自己缩比在 Python 里插值更准（也少一处会漂的东西）。
    """
    data = _read(ICON)
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError(f"{ICON} 不是 ICO")
    n = struct.unpack_from("<H", data, 4)[0]
    best = (0, 0, 0)  # (边长, 偏移, 长度)
    for i in range(n):
        w, _h, _c, _r, _p, _bpp, size, off = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
        w = w or 256  # 0 表示 256
        if w > best[0]:
            best = (w, off, size)
    _w, off, size = best
    if data[off:off + 4] != b"\x89PNG":
        raise ValueError(f"{ICON} 的最大帧不是 PNG（{size} 字节）")
    return data[off:off + size]


def _store_files() -> dict[str, bytes]:
    """随包的 lompi 标准库 -> {`<name>/<version>/<file>`: 字节}。

    **整棵树照收** —— store 的布局是 `<name>/<version>/*`，没有"顶层文件白名单"可言
    （加一个模块就多一个文件）。所以这里不列名字，走目录；`_fresh_sources` 也用它，
    于是"库里加了新模块却忘了重打包"会被 `--check` 抓住。
    """
    base = ROOT / STORE_DIR
    return {p.relative_to(base).as_posix(): p.read_bytes()
            for p in sorted(base.rglob("*")) if p.is_file()}


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def version_text() -> str:
    sha = _git("rev-parse", "--short", "HEAD") or "unknown"
    date = _git("show", "-s", "--format=%cs", "HEAD") or "unknown"
    return f"Loment {DISPLAY} ({VER})\ncommit {sha} ({date})\n"


def payload(kind: str, bins: dict[str, tuple[bytes, bytes]]) -> dict[str, tuple[bytes, int]]:
    """kind = linux | windows。归档内相对路径 -> (字节, 权限)。

    同一批工具按平台取**各自的产物**：Linux 包放 ELF，Windows 包放 PE（带 `.exe` 后缀）。
    Windows 包因此不再需要 WSL —— 里面全是本机可执行文件。
    """
    files: dict[str, tuple[bytes, int]] = {}
    idx = 1 if kind == "windows" else 0
    for name, pair in bins.items():
        files[f"bin/{name}{'.exe' if kind == 'windows' else ''}"] = (pair[idx], 0o755)
    # 包里那份启动器由 `release` 渠道**生成**（模板里只有注释上的 `@CHANNEL@`）。端口门
    # 自 2026-10-17（`#406`）起在**两个渠道都开着**：启动器无条件给链接器传
    # `--allow-ports`，链真 `.so` 不再是开发通道的特权（`docs/219` §8c）。
    files["bin/loment"] = (_subst(LAUNCHER_SH, "release").encode("ascii"), 0o755)
    if kind == "windows":
        # Windows 用原生 .cmd 启动器：包里全是 PE，直接调本机 exe，不再往 WSL 转发
        files["bin/loment.cmd"] = (_crlf(_subst(LAUNCHER_CMD, "release")).encode("ascii"), 0o755)
    files["share/loment/version"] = (version_text().encode(), 0o644)
    files["share/loment/seed.ll"] = (_read("loment/build/selfhost_driver.ll"), 0o644)
    for ex in EXAMPLES:
        files[f"share/loment/examples/{Path(ex).name}"] = (_read(ex), 0o644)
    # `loment/lib/` 里的模块 (proc 就是其中一个)。**为什么必须随包发**: 名字形式的搜索根
    # 第三层是**相对当前目录**的四个内置根 (`loment/lib` 等, docs/143 §2.3), 装好的工具链
    # 里根本没有仓库 —— 不带上这一份, 包用户 `use proc` 只会得到"名字导入找不到".
    # 2026-09-16 真装了一遍才看见 (判据里跑的都是仓库内的路径, 照不出来)。
    for lib in sorted((ROOT / "loment" / "lib").glob("*.lomt")):
        files[f"share/loment/lib/{lib.name}"] = (lib.read_bytes(), 0o644)
    files["share/loment/skill/SKILL.md"] = (_read(SKILL), 0o644)
    # lompi 是独立命令，它那份指南也放**自己**的 share 树下，不塞进 share/loment/
    files["share/lompi/skill/SKILL.md"] = (_read(SKILL_LOMPI), 0o644)
    # lompi 的标准库 (std/host/internet/os 四个包, 共 160 个文件 —— 数见 `loment_lompi_test.
    # STORE_FILES`, 别在这儿手抄第二份)。放在包里是**纯净的那一份**,
    # 安装器再照 lompi 自己认的全局 store 拷过去 (见 install.sh / install.ps1) —— 于是
    # 装完就能直接用, 同时"这一版 Loment 到底随包发了哪一版库"有据可查。
    for rel, body in _store_files().items():
        files[f"share/lompi/store/{rel}"] = (body, 0o644)
    files["README.md"] = (_subst(README_MD).encode(), 0o644)
    for lic in LICENSES:
        files[lic] = (_read(lic), 0o644)
    if kind == "linux":
        files["install.sh"] = (_subst(INSTALL_SH).encode("ascii"), 0o755)
    else:
        files["bin/loment.ico"] = (_read(ICON), 0o644)
        files["install.ps1"] = (_crlf(_subst(INSTALL_PS1)).encode("ascii"), 0o644)
        files["install.cmd"] = (_crlf(_subst(INSTALL_CMD)).encode("ascii"), 0o644)
        # 安装器的脸，与它正中那张标。`install.cmd` 无参数（= 双击）时走这两个文件。
        files["welcome.ps1"] = (_crlf(_subst(WELCOME_PS1)).encode("ascii"), 0o644)
        files[LOGO_NAME] = (logo_png(), 0o644)
    # 随包校验和 (安装脚本第一步就校它; 覆盖上面所有文件, 不含自己)
    files["SHA256SUMS"] = (sums_text(files).encode(), 0o644)
    return files


def _subst(text: str, channel: str = "dev") -> str:
    """把模板里的占位符换掉。**默认 `dev`** —— 于是老调用点（判据里的桩包）行为不变。

    `channel` 只喂 `@CHANNEL@`。**它今天只出现在注释里** —— 端口门自 2026-10-17
    （`#406`）起在两个渠道都开着：`--hosted` 与带未定义符号的外源对象都由启动器传
    `--allow-ports` 给链接器，发行版与开发版在这件事上没有行为差别（`docs/219` §8c）。
    链接器自己的默认仍是"拒"（直接调它时要显式写 `--allow-ports`）。

    发行包由 `emit()` 走 `release`；判据里的桩包走默认的 `dev`。两者生成的脚本在
    `@CHANNEL@` 之外**逐字节相同**。
    """
    return (text.replace("@DISPLAY@", DISPLAY).replace("@VERSION@", VER)
            .replace("@LIBCALLS@", _FREESTANDING_LIBCALLS)
            .replace("@CHANNEL@", channel))


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def sums_text(files: dict[str, tuple[bytes, int]]) -> str:
    return "".join(f"{hashlib.sha256(files[k][0]).hexdigest()}  {k}\n" for k in sorted(files))


def _zip(prefix: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    """确定性 zip: 固定时间戳 + 排序 + unix 权限位 (同输入两次构建字节相同)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            data, mode = files[rel]
            name = f"{prefix}/{rel}" if prefix else rel
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def _tar_gz(prefix: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    """确定性 tar.gz: mtime=0 + 稳定 uid/gid/uname (gzip 头也不带时间)。"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for rel in sorted(files):
            data, mode = files[rel]
            ti = tarfile.TarInfo(f"{prefix}/{rel}")
            ti.size, ti.mtime, ti.mode = len(data), 0, mode
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.type = tarfile.REGTYPE
            tf.addfile(ti, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


#: wextract 解完包后在**解包目录**里拉起的命令。脚本必须给**绝对路径**。
#:
#: 裸名 `install.cmd` 会失败: cmd 的"搜索当前目录"被环境变量
#: `NoDefaultCurrentDirectoryInExePath` 关掉时 (开发工具/加固环境常设这个 —— 本仓
#: 的 agent 进程里就是 1), `cmd /c install.cmd` 找不到刚解出来的脚本, 报一句就走,
#: 自解压包于是"解了包、什么都没发生"。2026-09-25 用户报障即此。
#: `%CD%` 是 cmd 的运行时变量 (打包时 iexpress **不会**替换它), 展开后就是解包目录。
APP_LAUNCHED = 'cmd.exe /c "%CD%\\install.cmd"'


def iexpress_sed(target: Path, src: Path, files: list[str]) -> str:
    """IExpress 的 SED 文本。

    `[Options]` 里那九条字符串 (InstallPrompt / DisplayLicense / FinishMessage /
    TargetName / FriendlyName / AppLaunched / PostInstallCmd / AdminQuietInstCmd /
    UserQuietInstCmd) 在生成的 exe 里是**一张按顺序排的字符串表** —— 中间插一条或
    调换位置, wextract 就会按错的索引读表, 包整个不工作 (且**没有任何报错**)。
    所以这段的**行序不要动**。

    单独抽出来是为了让 `loment_dist_test` 能用**同一份 SED** 打一个极小载荷的包
    来真跑 —— 见那里的 `wextract 真跑一遍` 判据。
    """
    strings = "".join(f'FILE{i}="{n}"\n' for i, n in enumerate(files))
    refs = "".join(f"%FILE{i}%=\n" for i in range(len(files)))
    return (
        "[Version]\nClass=IEXPRESS\nSEDVersion=3\n[Options]\nPackagePurpose=InstallApp\n"
        "ShowInstallProgramWindow=1\nHideExtractAnimation=1\nUseLongFileName=1\n"
        "InsideCompressed=0\nCAB_FixedSize=0\nCAB_ResvCodeSigning=0\nRebootMode=N\n"
        "InstallPrompt=\nDisplayLicense=\nFinishMessage=\n"
        f"TargetName={target.resolve()}\nFriendlyName=Loment {DISPLAY} Setup\n"
        f"AppLaunched={APP_LAUNCHED}\nPostInstallCmd=<None>\n"
        "AdminQuietInstCmd=\nUserQuietInstCmd=\nSourceFiles=SourceFiles\n"
        "[Strings]\n" + strings +
        f"[SourceFiles]\nSourceFiles0={src.resolve()}\\\n[SourceFiles0]\n" + refs)


def emit_exe(zip_bytes: bytes, target: Path) -> bool:
    """iexpress (Windows 自带) 打自解压安装包: payload.zip + install.ps1 + install.cmd。

    载荷**必须**是"发行目录的内容"压在 zip 根上。0.1.5 发布出去的那个 exe 是从目录本身
    压的（每条目多一层 `loment-<ver>-.../`），安装器当场把完整包判成不完整。install.ps1
    现在两种形状都认（`$top`），但**发行侧只发根上那种**：一种形状，少一条可能漂的路。
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        if "SHA256SUMS" not in zf.namelist():
            raise SystemExit("  [setup.exe] 载荷没有根上的 SHA256SUMS —— 拒绝打一个装不上的包"
                             "（把发行目录的**内容**压进去，不要压目录本身）")
    src = STAGE / "exe-payload"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    (src / "payload.zip").write_bytes(zip_bytes)
    (src / "install.ps1").write_bytes(_crlf(_subst(INSTALL_PS1)).encode("ascii"))
    (src / "install.cmd").write_bytes(_crlf(_subst(INSTALL_CMD)).encode("ascii"))
    (src / "welcome.ps1").write_bytes(_crlf(_subst(WELCOME_PS1)).encode("ascii"))
    (src / LOGO_NAME).write_bytes(logo_png())
    names = sorted(p.name for p in src.iterdir())   # 注意: 在写 loment.sed **之前**
    sed = src / "loment.sed"
    sed.write_text(iexpress_sed(target, src, names),
                   encoding="ascii", newline="\r\n")
    ie = shutil.which("iexpress") or r"C:\Windows\System32\iexpress.exe"
    if not Path(ie).exists():
        print("  [setup.exe] SKIP: iexpress 不存在")
        return False
    # `/Q` **不是可选的**（2026-10-20 实测）：不带它时，IExpress 在没有可交互控制台的
    # 进程里不去读 SED，而是弹出 "IExpress Wizard" 等输入 —— 进程 0% CPU 永久挂着，
    # 外面看起来就是"构建卡住"。发行作业因此撑满 60 分钟上限（#1077）。
    # 超时是第二道保险：GUI 建包正常只要几秒，300 秒还没回来就判失败，别让整条链陪葬。
    try:
        r = subprocess.run([ie, "/N", "/Q", str(sed)], capture_output=True, text=True,
                           shell=False, cwd=str(src), timeout=300)
    except subprocess.TimeoutExpired:
        print("  [setup.exe] iexpress 300 秒没返回 —— 已终止（向导在等输入？）")
        return False
    if not (target.exists() and target.stat().st_size > 0):
        print(f"  [setup.exe] iexpress 失败 rc={r.returncode}: "
              f"{(r.stdout or r.stderr)[-300:]}")
        return False
    return True


# ------------------------------------------------------------------ 入口

def skill_zip() -> bytes:
    """把 agent 指南单独打成一个确定性 zip (顶层目录 `loment/`), 供别的工具直接装。

    它是**第 4 个发行件**, 由 --emit 一起产出 —— 既不手工打(手工打的哈希必然与
    随后的 SHA256SUMS 对不上, 2026-09-15 踩过), 也能被 --check 的新鲜度检查覆盖。
    结构与 loment_dist_test 里的确定性约定一致: 固定时间戳 1980-01-01 + 权限 0644。

    `create_system = 3` 与 `_zip` 同一条口径, 但**这里漏过一次** (2026-10-10):
    `ZipInfo.__init__` 按 `sys.platform` 填默认值 (`'win32'` → 0, 其余 → 3), 于是同一份
    内容在 Windows 宿主上压出的字节与 Linux 宿主不同 —— 内容一个字节不差, 只是容器里那
    一个字段。它咬到的是"同一 tag 在两个 runner 上出两个哈希", 而发布流水线正好要从
    ubuntu 挪到 windows (`release.yml` 的 setup.exe 要 iexpress)。钉死它, 换宿主才**不改产物**。
    """
    skill_dir = Path(ROOT / SKILL).parent         # 例如 .claude/skills/loment
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(skill_dir.rglob("*")):
            if not p.is_file():
                continue
            zi = zipfile.ZipInfo(f"loment/{p.relative_to(skill_dir).as_posix()}",
                                 (1980, 1, 1, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            z.writestr(zi, p.read_bytes())
    return buf.getvalue()


def _use_release_seed() -> None:
    """把 `ACTIVE_SEED` 换成**发行版种子**（现算，落盘到 `STAGE`）。

    **这是"包 = 发行版"的落点**（`docs/231` §2.3）：只要走这条流水线，包里每一个工具都是
    发行版渠道建的 —— `loment-driver` 在其中，于是它会对 `choose vm` 报编译错误。

    种子每次现算（不读盘上那份缓存）：缓存只省本机重复构建的时间，**判据不在缓存上** ——
    `tools/loment_channel_test.py` 每次都重算一遍再对。
    """
    global ACTIVE_SEED
    STAGE.mkdir(parents=True, exist_ok=True)
    text = release_seed_text()
    RELEASE_SEED.write_text(text, encoding="utf-8", newline="\n")
    ACTIVE_SEED = RELEASE_SEED
    print(f"  [channel] 发行版种子 {len(text)}B —— 注入点："
          f"loment/selfhost/checker.lomt 那一行渠道常量（`docs/231` §2.3）")


def emit(only: set[str] | None, want_exe: bool, out_dir: Path | None = None,
         opt: bool = False) -> int:
    out = out_dir or OUT
    _use_release_seed()
    bins = build_tools(only, opt)
    lin = payload("linux", bins)
    win = payload("windows", bins)
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    tar = out / f"loment-{VER}-linux-x64.tar.gz"
    tar.write_bytes(_tar_gz(f"loment-{VER}-linux-x64", lin))
    made.append(tar)
    print(f"  [{tar.name}] {tar.stat().st_size} 字节, {len(lin)} 个文件")

    zbytes = _zip(f"loment-{VER}-windows-x64", win)
    zipf = out / f"loment-{VER}-windows-x64.zip"
    zipf.write_bytes(zbytes)
    made.append(zipf)
    print(f"  [{zipf.name}] {len(zbytes)} 字节, {len(win)} 个文件")

    if want_exe:
        exe = out / f"loment-{VER}-windows-x64-setup.exe"
        if exe.exists():
            _unlink_retry(exe)
        if emit_exe(_zip("", win), exe):
            made.append(exe)
            print(f"  [{exe.name}] {exe.stat().st_size} 字节 (自解压, 未签名)")

    # 第 4 件: agent 指南的独立 zip (顶层目录 loment/), 供别的工具直接装。
    # 由 --emit 产出而不是手工打 —— 手工打的字节会漂, 而 write_sums 是扫目录的,
    # 于是清单里留下一条对不上的哈希 (2026-09-15 踩过)。
    szip = out / "loment-skill.zip"
    szip.write_bytes(skill_zip())
    made.append(szip)
    print(f"  [{szip.name}] {szip.stat().st_size} 字节 (agent 指南)")

    # 清掉**上一版**留下的发行件: 版本号进文件名, 所以新版不会覆盖旧版 —— 留着的话
    # 产物目录里会同时躺着两个版本, 分不清哪个是当前件 (2026-09-15 踩过两次)。
    # 只动本目录里名字像发行件的文件, 且只删**不在本次产物清单里**的。
    keep = {p.name for p in made} | {"SHA256SUMS"}
    # 注意 keep 里没有 --no-exe 时本该有的那份: 所以"名字含当前版本号"的一律保留,
    # 免得 `--emit --no-exe` 把**当前**版本的 setup.exe 当陈旧件删掉。
    stale = [p for p in sorted(out.iterdir())
             if p.is_file() and p.name not in keep and VER not in p.name
             and (p.name.startswith("loment-") or p.name.endswith("-setup.exe"))]
    for p in stale:
        _unlink_retry(p)
        print(f"  [陈旧] 删掉 {p.name} (不是本次产物)")

    sums = write_sums(out)
    print(f"  [{sums.name}] {len(made)} 行")
    return 0


def _unlink_retry(p: Path, tries: int = 5) -> None:
    """删文件带重试, 删不掉就改名挪走 —— **不让整个 --emit 死在中途**。

    第一层是老的: 刚签过名的 exe 常被 Defender 扫一下, 那几百毫秒里删会 WinError 5
    (2026-09-12 撞到过一次, 手工再删就没了)。

    第二层是 2026-09-15 补的: 又撞到, 而且**重试 5 次仍拒、紧接着手工改名却一次成功**
    —— 拒绝只落在 delete 这一条路径上(安全软件挂钩的典型样子)。所以退一步改名挪开,
    调用方照常写新文件。**必须挪出产物目录**: `write_sums` 是扫目录的, 留个 `*.old`
    会被算进清单。腾不掉才让 --emit 失败 —— 那会留下"归档是新的、清单是旧的"混杂目录。
    """
    import time
    for i in range(tries):
        try:
            p.unlink()
            return
        except PermissionError:
            if i == tries - 1:
                break
            time.sleep(1.0)
    stale = STAGE / "stale"
    stale.mkdir(parents=True, exist_ok=True)
    dst = stale / f"{p.name}.{int(time.time())}"
    p.rename(dst)
    print(f"  [{p.name}] 删不掉(拒绝访问), 已挪到 {dst.name} —— 有空手工清一下")


def write_sums(out_dir: Path | None = None) -> Path:
    """重算产物目录的 SHA256SUMS (排除清单自身、分离签名 .sig 与公钥证书 .pem)。

    单独抽出来是因为**签名会改 PE 的字节**: 签名之后必须重算清单, 否则清单对不上产物。
    """
    out = out_dir or OUT
    keep_out = ("SHA256SUMS", "verify.sh", "verify.ps1", "FINGERPRINT")
    arts = [p for p in sorted(out.iterdir())
            if p.is_file() and p.name not in keep_out
            and not p.name.endswith((".sig", ".pem", ".asc"))]
    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                            for p in arts), encoding="utf-8", newline="\n")
    return sums


def _read_archive(p: Path) -> dict[str, bytes]:
    """归档 -> 去掉顶层目录名的 相对路径: 字节。"""
    if p.suffix == ".zip":
        with zipfile.ZipFile(p) as z:
            return {n.split("/", 1)[1]: z.read(n) for n in z.namelist()}
    with tarfile.open(p) as tf:
        return {m.name.split("/", 1)[1]: tf.extractfile(m).read()
                for m in tf.getmembers() if m.isfile()}


def _fresh_sources(kind: str) -> dict[str, bytes]:
    """归档里**直接从仓库文件来**的那些条目 -> 当前应有的字节。

    编译产物 (`bin/loment-*`) 不在此列 —— 它们要重新编译才能比, 由 `--emit` 保证;
    这里盯的是"改了源码却忘了重打包"这一类: 它们不需要编译就能比, 又是最容易忘的
    (2026-09-15 踩过: `use` 改动之后 `loment/dist/` 里的 skill/seed 全是旧的, 而
    那时 `--check` 照样报"一致" —— 它只拿归档跟**它自己的** SHA256SUMS 比)。
    """
    out = {
        "share/loment/skill/SKILL.md": _read(SKILL),
        **{f"share/loment/lib/{p.name}": p.read_bytes()
           for p in sorted((ROOT / "loment" / "lib").glob("*.lomt"))},
        "share/lompi/skill/SKILL.md": _read(SKILL_LOMPI),
        **{f"share/lompi/store/{k}": v for k, v in _store_files().items()},
        "share/loment/seed.ll": _read("loment/build/selfhost_driver.ll"),
        # **版本文件也在这一列** (2026-09-16 补): 它带**提交号**, 所以"打完包又提交了一次"
        # 就会让它过期。原先 `--check` 根本不看它 —— 于是 0.1.4-pre1 的包里印的是**上一个**
        # 提交号的 commit 行, 而 `--check` 照样绿 (和上面 skill/store 那两个洞是同一种:
        # 只跟自己的 SHA256SUMS 比, 两边一起过期就永远看不出来)。
        "share/loment/version": version_text().encode(),
        f"share/loment/examples/{Path(EXAMPLE).name}": _read(EXAMPLE),
        "README.md": _subst(README_MD).encode(),
        "LICENSE-MIT": _read("LICENSE-MIT"),
        "LICENSE-APACHE": _read("LICENSE-APACHE"),
    }
    if kind == "linux":
        out["bin/loment"] = _subst(LAUNCHER_SH, "release").encode("ascii")
        out["install.sh"] = _subst(INSTALL_SH).encode("ascii")
    else:
        out["bin/loment.cmd"] = _crlf(_subst(LAUNCHER_CMD, "release")).encode("ascii")
        # 安装脚本也是**模板直出**的 —— 改了模板不重打包, 归档里就是旧的, 而
        # `--check` 原先照样绿 (2026-09-16 加 store 时踩到: 改完 INSTALL_SH 才发现)。
        out["install.ps1"] = _crlf(_subst(INSTALL_PS1)).encode("ascii")
        out["install.cmd"] = _crlf(_subst(INSTALL_CMD)).encode("ascii")
        out["welcome.ps1"] = _crlf(_subst(WELCOME_PS1)).encode("ascii")
        out[LOGO_NAME] = logo_png()
    return out


def check(out_dir: Path | None = None) -> int:
    out = out_dir or OUT
    sums = out / "SHA256SUMS"
    if not sums.exists():
        print(f"[ERR] {sums.relative_to(ROOT)} 缺失 (先跑 --emit)")
        return 1
    bad = []
    n = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        want, rel = line.split("  ", 1)
        p = out / rel
        n += 1
        got = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(缺失)"
        if got != want:
            bad.append(f"{rel}: {got} != {want}")
    for b in bad:
        print(f"[DIFF] {b}")
    # 新鲜度: 归档里那些源码直出的条目, 必须等于**当前**仓库里的那份
    stale = []
    for arc, kind in ((f"loment-{VER}-linux-x64.tar.gz", "linux"),
                      (f"loment-{VER}-windows-x64.zip", "windows")):
        p = out / arc
        if not p.exists():
            continue
        got = _read_archive(p)
        for rel, want in _fresh_sources(kind).items():
            # 归档里**缺**这条也算陈旧: 新加一个 store 模块却忘了重打包时, 它不在
            # SHA256SUMS 里, 光比内容永远看不出来 (2026-09-16 加 store 时发现的洞)。
            if rel not in got:
                stale.append(f"{arc}:{rel} (归档里没有)")
            elif got[rel] != want:
                stale.append(f"{arc}:{rel}")
    sz = out / "loment-skill.zip"
    if sz.exists() and _read_archive(sz).get("SKILL.md") != _read(SKILL):
        stale.append("loment-skill.zip:SKILL.md")
    for s in stale:
        print(f"[STALE] {s} —— 归档里那份不是当前源码, 重跑 --emit")
    if bad or stale:
        return 1
    print(f"loment_dist: {n}/{n} 产物与 SHA256SUMS 一致, 且源码直出的条目都是最新的")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_dist")
    ap.add_argument("--emit", action="store_true", help="构建并打包")
    ap.add_argument("--check", action="store_true", help="校验现有产物与 SHA256SUMS")
    ap.add_argument("--list", action="store_true", help="只列会打进去的文件")
    ap.add_argument("--only", metavar="NAME[,NAME]",
                    help="只构建这些工具 (driver,lsp,fmt,doc,lomelf,cli,lompi)")
    ap.add_argument("--no-exe", action="store_true", help="跳过 Windows 自解压安装包")
    ap.add_argument("--opt", action="store_true",
                    help="ELF 侧改用 clang -O2 构建（docs/212 §5A；没有 clang 就降级）")
    ap.add_argument("--out", metavar="DIR", help="产物目录 (默认 loment/dist)")
    a = ap.parse_args(argv)

    if a.list:
        for rel in sorted(payload("linux", {})):
            print(f"  {rel}")
        return 0
    out_dir = Path(a.out) if a.out else None
    if a.check:
        return check(out_dir)
    if a.emit:
        # --only 允许短名 (driver/lsp/fmt/doc) —— 名字对齐工具名 loment-<x>
        only = None
        if a.only:
            # 两种写法都收: 工具名 (loment-fmt) 与短名 (fmt)。**lompi 没有 `loment-`
            # 前缀**, 只按短名规则拼会得到 `loment-lompi` 而永远匹配不上, 所以逐个试。
            known = {n for n, _e, _c in TOOLS}
            only: set[str] = set()
            unknown: set[str] = set()
            for t in (x for x in a.only.split(",") if x):
                if t in known:
                    only.add(t)
                elif f"loment-{t}" in known:
                    only.add(f"loment-{t}")
                else:
                    unknown.add(t)
            if unknown:
                print(f"[ERR] 未知工具: {sorted(unknown)}", file=sys.stderr)
                return 2
        return emit(only, not a.no_exe, out_dir, a.opt)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
