#!/usr/bin/env python3
# loment_hosted_test.py — `--hosted` 的门面判据 (docs/222)
#
# 判的是一整条链, 不是一层: **一份 Loment 源, 经真的启动器编出来, 链上系统真实的
# `libz.so`, 跑出逐字节正确的结果**。这条一旦绿, "能调外部库"就是真的, 而不是
# "IR 里有个 declare"。
#
# 为什么必须比**运行结果**: 这条路上最容易错的三处都**不改 IR** ——
#   ① 启动器把 `.ll` 当成目标文件递给 gcc (实测: `collect2: ld returned 1`);
#   ② `-lz` 这种连写形式没被参数循环收下 (实测: `unknown option -lz`);
#   ③ `--hosted` 却按 sealed 链 (lomelf), 于是报"未定义的符号 compress" —— 方向全错。
# 三者都只有**跑起来看结果**才抓得到。
#
# 平台: 判据要一条链跑到底, 所以它自己选路 ——
#   * **本机是 Linux 且 PATH 上有懂 IR 的 clang** (CI 就是这种): 原生跑;
#   * **本机是 Windows** (开发机): IR 由主机的 `clang.exe` 交叉成 ELF 目标文件,
#     链接与运行在 WSL 里由 gcc 做 —— 与 `loment_ffi_test` 同一套舞步;
#   * 两样都没有: **SKIP 并指名缺什么**, 不静默绿。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_dist                                                 # noqa: E402
import lomentc                                                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 被验的那份 Loment

#: 一份**未改动的** Loment 源: `fn main` 是 hosted 的入口 (docs/222 §4.3)。
#: 它调系统 libz 的 `compress` / `uncompress`, 把 64 字节往返一遍。
#: 成功返回 0; 每一个非零都是一个**具体的**失败点, 不是笼统的"没过"。
HOSTED_SRC = """\
module hostz
choose runtime

extern fn compress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;
extern fn uncompress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;

const NSRC: u32 = 64;

fn store32(p: ptr, off: u32, v: u32) {
    store8(p, off, v as u8);
    store8(p, off + 1, ((v / 256) % 256) as u8);
    store8(p, off + 2, ((v / 65536) % 256) as u8);
    store8(p, off + 3, ((v / 16777216) % 256) as u8);
}

fn load32(p: ptr, off: u32) -> u32 {
    return load8(p, off)
        + load8(p, off + 1) * 256
        + load8(p, off + 2) * 65536
        + load8(p, off + 3) * 16777216;
}

fn main() -> u32 {
    let src: ptr = alloc(256);
    let clen: ptr = alloc(16);
    let comp: ptr = alloc(1024);
    let olen: ptr = alloc(16);
    let out: ptr = alloc(256);
    let i: u32 = 0;
    while i < NSRC {
        store8(src, i, ((i % 8) + 65) as u8);
        i = i + 1;
    }
    store32(clen, 0, 1024);
    if compress(comp, clen, src, NSRC as u64) != 0 { return 10; }
    let ncomp: u32 = load32(clen, 0);
    store32(olen, 0, 256);
    if uncompress(out, olen, comp, ncomp as u64) != 0 { return 20; }
    if load32(olen, 0) != NSRC { return 30; }
    let j: u32 = 0;
    while j < NSRC {
        if load8(out, j) != load8(src, j) { return 40; }
        j = j + 1;
    }
    if ncomp >= NSRC { return 50; }
    return 0;
}
"""

#: **证伪用的那一份**: 只把最后一处比较写错 (比成 0)。若这条判据真的在测往返,
#: 它必须返回 40; 若返回 0, 说明这条判据测的是"能跑"而不是"算对"。
FALSIFY_SRC = HOSTED_SRC.replace("if load8(out, j) != load8(src, j) {",
                                 "if load8(out, j) != 0 {")


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _resolves_with_flags(cc: str) -> bool:
    """这台机器上的 `cc` 会不会**独立解决掉**一个外部符号 (真 libc) ——

    不是"它能不能读懂 IR"(那要用一份 IR 试), 而是这一条: 原生 clang 在 Linux 上
    默认链真 libc, 而 Windows 那份 `clang.exe` 交叉到 ELF 时需要 sysroot, 给不了。
    所以这一条决定"原生跑"还是"走 WSL 两段"。
    """
    if os.name == "nt":
        return False
    return True


def _wsl() -> bool:
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    """Windows 路径 -> WSL 里能用的 `/mnt/<盘>/…`。"""
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _write_sh(path: Path, text: str, executable: bool = False) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        os.chmod(path, 0o755)


# ---------------------------------------------------------------- 假包 (真启动器)

#: WSL 侧那个 `clang` 垫片。**为什么需要它**: 这台开发机的 WSL 里没有 clang,
#: 而真懂 IR 的是 Windows 那一份 `clang.exe` (WSL 经 interop 能调它)。
#: `clang.exe` 一步链完需要一个 Linux 链接器与 sysroot —— 那是 Windows 侧给不了的,
#: 所以拆成 `clang.exe -c`(交叉出 ELF 目标文件) + `gcc`(链接)。
#: **它测的是启动器怎么拼那条命令行**, 不是"clang 能不能一步链完" ——
#: CI 上的原生 clang 才是后者, 那时这份垫片不参与。
CLANG_SHIM = """\
#!/bin/sh
CLANG="/mnt/c/Program Files/LLVM/bin/clang.exe"
ir=; out=; objs=; libs=
while [ $# -gt 0 ]; do
    case "$1" in
        *.ll) ir=$1 ;;
        -o) out=$2; shift ;;
        -L*|-l*) libs="$libs $1" ;;
        *) objs="$objs $1" ;;
    esac
    shift
done
tmp=$(mktemp -d)
# `clang.exe` 是 Windows 进程, 看不见 WSL 的 `/tmp/...` —— 递过去之前要换写法。
irw=$(wslpath -w "$ir")
ow=$(wslpath -w "$tmp/a.o")
"$CLANG" --target=x86_64-unknown-linux-gnu -c "$irw" -o "$ow" || exit 1
gcc "$tmp/a.o" $objs $libs -o "$out" || exit 1
exit 0
"""


class Pkg:
    """一个**最小**的发行包布局, 里面跑的是 `loment_dist.LAUNCHER_SH` 那一份真启动器。

    驱动是个桩 (把预生成的 IR 抄到 stdout): 这一条判据要测的是**链接那一段**,
    编译器另有它的判据。桩把这件事说出来, 而不是假装它是个真驱动。
    """

    def __init__(self, td: Path, native: bool) -> None:
        self.td = td
        self.native = native
        self.bin = td / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        (td / "share" / "loment").mkdir(parents=True, exist_ok=True)
        _write_sh(self.bin / "loment", loment_dist._subst(loment_dist.LAUNCHER_SH),
                  executable=True)
        _write_sh(self.bin / "loment-lomelf",
                  '#!/bin/sh\necho "lomelf: a hosted build must not reach here" >&2\nexit 9\n',
                  executable=True)
        self.shim = td / "shim"
        if not native:
            self.shim.mkdir(exist_ok=True)
            _write_sh(self.shim / "clang", CLANG_SHIM, executable=True)
        #: 每一步的 IR 落在这里, 由 `emit()` 写、桩驱动读。
        self.ll = td / "prog.ll"
        # 桩驱动在**哪一侧**跑, 就用哪一侧的路径写法 —— WSL 里 `C:/...` 是打不开的
        # (实测: `cat: C:/Users/.../prog.ll: No such file or directory`)。
        stub_path = self.ll.as_posix() if native else _wsl_path(self.ll)
        _write_sh(self.bin / "loment-driver",
                  '#!/bin/sh\ncat "%s"\n' % stub_path,
                  executable=True)

    def emit(self, src: str) -> None:
        """用**参考编译器**把源编成 IR —— 与 `loment ir` 同一条路。"""
        d = self.td / "src"
        d.mkdir(exist_ok=True)
        f = d / "prog.lomt"
        _write_sh(f, src)
        mod = lomentc.load(f)
        errs = lomentc.check(mod)
        assert not errs, errs
        _write_sh(self.ll, lomentc.emit_llvm(mod, ROOT))

    def run(self, args: list[str], src_name: str = "src/prog.lomt") -> tuple[int, str]:
        """跑**真启动器**，再跑**它编出来的那个程序**。返回 (程序的退出码, 合并输出)。

        ⚠ **两步都要有。** 第一版只有 WSL 那一侧把程序跑起来，native 那一侧只调了
        启动器就返回 —— 于是 CI（Linux、native）拿到的永远是**构建的退出码 0**，
        十二条判据**全部空转**：zlib 那条"PASS"是假的，Python 那条因为程序根本没跑
        所以文件是空的。**抓到它的是证伪那条**（它期望 40，拿到 0）—— 这正是
        "判据要能变红"这条纪律买到的东西，不是多余的一步。

        构建失败时返回**构建的**退出码（拒绝面的判据要的就是它，那时没有程序可跑）。
        """
        if self.native:
            paths = [str(self.bin)] + ([str(self.shim)] if self.shim.exists() else [])
            r = subprocess.run([str(self.bin / "loment"), *args], capture_output=True,
                               text=True, shell=False, encoding="utf-8",
                               errors="replace", timeout=600, cwd=str(self.td),
                               env={**os.environ, "PATH": os.pathsep.join(paths + [os.environ.get("PATH", "")])})
            build_log = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0:
                return r.returncode, build_log
            # 产物路径**从参数里解析**，不写死 —— 写死就又是一处"本地与 CI 不一样"的
            # 地方，而这一条判据已经因为这类不一致空转过一次。
            exe = None
            for i, a in enumerate(args):
                if a in ("-o", "--out") and i + 1 < len(args):
                    exe = Path(args[i + 1])
                    if not exe.is_absolute():
                        exe = self.td / exe
                    break
            if exe is None or not exe.exists():
                return r.returncode, build_log + "\n(构建成功但没找到产物)"
            r2 = subprocess.run([str(exe)], capture_output=True, text=True, shell=False,
                                encoding="utf-8", errors="replace", timeout=600,
                                cwd=str(self.td))
            return r2.returncode, build_log + (r2.stdout or "") + (r2.stderr or "")
        return self._run_wsl(args, src_name)

    def _run_wsl(self, args: list[str], src_name: str) -> tuple[int, str]:
        w = _wsl_path(self.td)
        q = " ".join("'%s'" % a.replace("'", "'\\''") for a in args)
        script = (
            f"cd '{w}' || exit 99\n"
            f"chmod +x bin/* shim/* 2>/dev/null\n"
            f"PATH='{w}/shim':$PATH ./bin/loment {q}\n"
            f"b=$?\n"
            f"if [ $b -ne 0 ]; then echo \"BUILD_RC=$b\"; exit 0; fi\n"
            f"./out.bin\n"
            f"echo \"RUN_RC=$?\"\n"
        )
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script], capture_output=True,
                           text=True, shell=False, encoding="utf-8",
                           errors="replace", timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        if "RUN_RC=" in out:
            return int(out.rsplit("RUN_RC=", 1)[1].split()[0]), out
        if "BUILD_RC=" in out:
            return int(out.rsplit("BUILD_RC=", 1)[1].split()[0]), out
        return r.returncode, out


def _pkg(td: Path, src: str) -> Pkg:
    native = _resolves_with_flags(_clang() or "cc")
    p = Pkg(td, native)
    p.emit(src)
    return p


# ---------------------------------------------------------------- 环境: 两边各一套

#: 外源工件 (`.o` / `.so` / `.class`) 必须在**能产出 ELF 的那一侧**编出来。
#: 本机是 Windows 时那就是 WSL, 是 Linux 时就是本机。**判据不假装这两边一样** ——
#: 它把"在哪一侧"写成一个函数, 而不是散在每一条判据里。
class Env:
    def __init__(self, td: Path, native: bool) -> None:
        self.td = td
        self.native = native

    def path(self, p: Path) -> str:
        """这份工件在**跑工件的那一侧**怎么写。"""
        return str(p) if self.native else _wsl_path(p)

    def sh(self, script: str) -> tuple[int, str]:
        # `LOMENT_PATH_EXTRA`: 开发机上工具链不在 PATH 里时 (装在 `~/.lompi020` 那种),
        # 由**调用者显式**指出来。**判据不猜路径** —— CI 上它为空, 走的还是系统 PATH。
        extra = os.environ.get("LOMENT_PATH_EXTRA", "")
        if extra:
            script = f'export PATH="{extra}:$PATH"\n' + script
        cmd = ["bash", "-lc", script] if self.native else [*_WSLCMD, "bash", "-lc", script]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False,
                           encoding="utf-8", errors="replace", timeout=1800)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def which(self, name: str) -> str | None:
        rc, out = self.sh(f"command -v {name} || true")
        s = out.strip().splitlines()
        return s[-1].strip() if rc == 0 and s and s[-1].strip() else None


def _env(td: Path) -> Env:
    return Env(td, _resolves_with_flags(_clang() or "cc"))


# ---------------------------------------------------------------- 判据


def _run_ir(ir: str, td: Path, name: str) -> int:
    """一段 IR -> 跑起来的**退出码**（被信号打断也归一成 128+sig）。

    `hosted` 的产物由 **C 工具链**链（真 libc，`docs/222` §4.4）—— 所以这里就是
    "clang 出目标文件、系统链接器链上"，与启动器那条路上的**同一件事**，只是省掉启动器。
    本机是 Linux 时 clang 一步链完；Windows 开发机上 clang 交叉出 ELF、gcc 在 WSL 里链 + 跑
    （与上面那个 `CLANG_SHIM` 同一套舞步）。
    """
    ll = td / f"{name}.ll"
    _write_sh(ll, ir)
    cc = _clang()
    assert cc, "无 clang"
    if _resolves_with_flags(cc):                      # 本机是 Linux
        exe = td / name
        r = subprocess.run([cc, "-o", str(exe), str(ll)], capture_output=True,
                           text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        rc = subprocess.run([str(exe)], capture_output=True, shell=False).returncode
        return rc if rc >= 0 else 128 - rc            # -4 (SIGILL) -> 132
    obj = td / f"{name}.o"
    r = subprocess.run([cc, "--target=x86_64-unknown-linux-gnu", "-c", str(ll), "-o", str(obj)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    script = (f"cd '{_wsl_path(td)}' && gcc {name}.o -o {name}.bin && ./{name}.bin; echo \"RC=$?\"")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300, shell=False)
    out = (r.stdout or "") + (r.stderr or "")
    assert "RC=" in out, out[-400:]
    return int(out.rsplit("RC=", 1)[1].split()[0])


@test
def test_hosted_heap_asks_the_os_and_sealed_cannot():
    """`docs/228` §5 的 **A3**（= `docs/219` §8b 的 S1e）：**同一条程序**，
    `hosted` 真的拿到了 > 64 KiB 的 arena，`sealed` 真的拿不到。

    那条程序（`lomentc_test.HEAP_SRC`，**源只有一份**）分配 **200000** 字节（> 64 KiB）
    逐字节写 7、读回最后一字节 —— 成功返回 **7**。

    **两个方向都是判据**：只判"`hosted` 能跑"会被"把 `65536` 改大"骗过 ——
    而那一改 `sealed` 那条保证就没了（`docs/219` §7 那条"测量强于声明"）。
    `sealed` 这一侧要的是**死在 `lomentc.py:218` 那条 `boom` 上**（SIGILL）。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    if os.name == "nt" and not _wsl():
        print("      SKIP: 无 WSL (Windows 上把 IR 交叉成 ELF 要它)")
        return
    import lomentc_test as CT                                        # noqa: PLC0415
    # 两对：**分配器**那一对（`docs/228`）与 **分配器+收集器**那一对（`docs/230`）。
    # 后一对里 `hosted` 那一份还额外要求"**活着的数据在收集之后仍然在**" ——
    # 收集器真的在这块大 arena 上跑过、而且认得同一块内存，才算过。
    pairs = (("heap", CT.HEAP_HOSTED_SRC, CT.HEAP_SEALED_SRC),
             ("gc", CT.HEAP_HOSTED_GC_AUTO_SRC, CT.HEAP_SEALED_GC_AUTO_SRC))
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for label, hsrc, ssrc in pairs:
            got: dict[str, int] = {}
            for tag, src in (("hosted", hsrc), ("sealed", ssrc)):
                f = td / f"{tag}_{label}.lomt"
                _write_sh(f, src)
                mod = lomentc.load(f)
                errs = lomentc.check(mod)
                assert not errs, errs
                got[tag] = _run_ir(lomentc.emit_llvm(mod, ROOT), td, f"{tag}_{label}")
            assert got["hosted"] == 7, (
                f"[{label}] hosted 没拿到 > 64 KiB 的 arena (rc={got['hosted']})")
            assert got["sealed"] == 132, (
                f"[{label}] sealed 居然拿到了 > 64 KiB (rc={got['sealed']})"
                " —— `sealed` 那条保证被改掉了")
            print(f"      [{label}] hosted rc=7 / sealed rc=132 (SIGILL)")


@test
def test_the_static_arena_collector_still_reclaims():
    """`docs/231`（B3）：位图换掉之后，**静态那一档的收集器**仍然收得对。

    这条是 B3 的骨架判据：上面那条两方向的判据里 `sealed` 那一份在第一次
    `alloc(200000)` 就落 `boom` —— **根本没走到收集器**。而 B3 改的是 `_IR_GC_SHARED`
    本体，**每一档**都变了。所以另起一条：只在 **64 KiB** 里跑，累计要 **200 KB**
    （收不回来必然 OOM），同时守着一块只被栈上 `keep` 指着的活数据 ——
    **收集之后必须原样**（返回 9）。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    if os.name == "nt" and not _wsl():
        print("      SKIP: 无 WSL (Windows 上把 IR 交叉成 ELF 要它)")
        return
    import lomentc_test as CT                                        # noqa: PLC0415
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        f = td / "gc_sealed_churn.lomt"
        _write_sh(f, CT.GC_SEALED_CHURN_SRC)
        mod = lomentc.load(f)
        errs = lomentc.check(mod)
        assert not errs, errs
        rc = _run_ir(lomentc.emit_llvm(mod, ROOT), td, "gc_sealed_churn")
    assert rc == 9, f"静态档的收集器没收对 (rc={rc}) —— 位图那一改有问题"
    print("      静态 64 KiB：累计 200 KB 收得回来，活数据原样 (rc=9)")


@test
def test_hosted_links_a_real_library_and_returns_the_right_answer():
    """**那一条判据**: 一份 Loment 源调系统 libz, compress→uncompress 逐字节回原文。

    退出码 0 才算过; 非 0 的数字指出具体是哪一步 (10 压缩 / 20 解压 / 30 长度 /
    40 逐字节 / 50 没压小)。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        out = pkg.td / "out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o",
                           str(out) if pkg.native else "./out.bin"])
        assert rc == 0, f"hosted 程序返回 {rc} (期望 0)\n{log[-1200:]}"
        print("      Loment -> libz compress/uncompress -> 逐字节回原文 (rc=0)")


@test
def test_the_criterion_can_go_red():
    """**证伪**: 只把最后一处比较写错, 必须返回 40。

    没有这一条, 上一条测的是"能跑"而不是"算对" —— 一个把结果丢掉的实现也会返回 0。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), FALSIFY_SRC)
        out = pkg.td / "out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o",
                           str(out) if pkg.native else "./out.bin"])
        assert rc == 40, f"改造过的程序应该返回 40, 实际 {rc}\n{log[-1200:]}"
        print("      证伪: 比较写错 -> rc=40 (判据不是空跑)")


@test
def test_naming_a_library_without_hosted_is_refused():
    """`-l` / `-L` 是在声明"这份产物有对外端口"。sealed 档兑现不了这个声明, 要说出来。"""
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        rc, log = pkg.run(["build", "src/prog.lomt", "-lz", "-o",
                           str(pkg.td / "o.bin") if pkg.native else "./out.bin"])
        assert rc == 2, f"应该退 2 (用法错), 实际 {rc}\n{log[-600:]}"
        assert "--hosted" in log, log[-600:]
        print("      没有 --hosted 时 -lz 被点名拒 (退 2)")


@test
def test_hosted_needs_a_main_entry():
    """hosted 产物由 C 运行期调用 `main`; 拿一份只有 `_start` 的单元来, 必须点名拒。

    静默按 sealed 链的后果是"未定义的符号 compress" —— 把病因指到符号上, 方向全错。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        # 把 IR 里的入口改成 _start: 模拟一份 sealed 形状的单元
        _write_sh(pkg.ll, pkg.ll.read_text(encoding="utf-8").replace("@main(", "@_start("))
        out = str(pkg.td / "o.bin") if pkg.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o", out])
        assert rc == 1, f"应该退 1, 实际 {rc}\n{log[-600:]}"
        assert "main" in log, log[-600:]
        print("      hosted 却只有 _start: 点名拒 (退 1)")


@test
def test_windows_launcher_refuses_hosted_by_name():
    """PE 侧今天没有 hosted 这条路 (docs/222 §7)。**点名拒**, 不许静默按 lomelf 链。

    这台机器上 Windows 启动器跑不了 hosted, 所以这一条只能静态判 —— 它判的是
    "那份批处理里有这一支, 而且它会退 2", 不是"PE 上能跑"。
    """
    body = loment_dist.LAUNCHER_CMD
    assert 'if /I "%~1"=="--hosted" goto barg_hosted' in body, "Windows 启动器没收下 --hosted"
    assert ":barg_hosted" in body, "Windows 启动器没有那一支"
    tail = body.split(":barg_hosted", 1)[1].split(":barg_done", 1)[0]
    assert "exit /b 2" in tail, "那一支没有拒绝 (退 2)"
    assert "ELF" in tail, "拒绝理由没说清是平台的事"
    print("      Windows 启动器对 --hosted 点名拒 (退 2, 理由说清是平台)")


@test
def test_choose_hosted_alone_is_enough():
    """**声明与开关是同一件事的两种说法**（`docs/222` §4.4）。

    一份 `choose hosted` 的源，命令行上**一个 `--hosted` 都不打**，也必须走 hosted 那条路
    并算出正确答案 —— 否则用户写了声明却静默按 sealed 链，报"未定义的符号 compress"。
    判据同时钉住 IR 里那一行标记真的发出来了。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        pkg = Pkg(td, _resolves_with_flags(_clang() or "cc"))
        # `choose` 只能出现在根单元的顶层，所以放在 `module` 之后。
        pkg.emit(HOSTED_SRC.replace("module hostz\n", "module hostz\n\nchoose hosted\n", 1))
        ir = pkg.ll.read_text(encoding="utf-8")
        assert "; loment-port: hosted" in ir, "IR 里没有那行标记（声明到不了构建那一侧）"
        out = str(td / "out.bin") if pkg.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "-lz", "-o", out])   # **没有 --hosted**
        assert rc == 0, f"只写声明的那份返回 {rc}\n{log[-900:]}"
        print("      `choose hosted` 单独就够: 不打 --hosted 也走 hosted 并算对 (rc=0)")


@test
def test_sealed_units_get_no_marker():
    """反向: **sealed 是默认档**，所以现存每一份单元的 IR 一个字节都不该变。

    没有这一条，那行标记可能被无条件发出去 —— 而孪生判据逐字节比的那 46 份会一起红，
    病因却指不到这里。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)          # 没有 `choose hosted`
        ir = pkg.ll.read_text(encoding="utf-8")
        assert "loment-port" not in ir, "sealed 的单元不该有 port 标记"
        print("      sealed 单元不带 port 标记（现存 IR 逐字节不变）")


@test
def test_the_shipped_usage_mentions_hosted():
    """两份启动器的用法文本都要提到 `--hosted` —— 否则它是个"藏在代码里"的开关。"""
    sh = loment_dist._subst(loment_dist.LAUNCHER_SH)
    assert "--hosted" in sh, "POSIX 启动器的用法没提 --hosted"
    assert "--hosted" in loment_dist.LAUNCHER_CMD, "Windows 启动器的用法没提 --hosted"
    print("      两份启动器的用法文本都提到 --hosted")


# ---------------------------------------------------------------- 五个生态，各一条

#: C++ 那份库: 一个**真类** (构造函数 / 成员函数), 没有任何 `extern "C"` 包装。
#: 所以 Loment 那一侧要按**改名字符**声明 —— 这正是"用 C++ 的库"与"用 C 的库"的区别:
#: 名字被编过、`this` 是第一个实参、底下要链 libstdc++ 的运行期。
CPP_LIB = """\
class Counter {
public:
    int v;
    Counter(int x) : v(x) {}
    int add(int d) { v += d; return v; }
};
Counter* counter_new(int x) { return new Counter(x); }
int counter_add(Counter* c, int d) { return c->add(d); }
"""

#: 改名字符是 `nm` 量出来的, 不是猜的 (实测 `_Z11counter_newi` / `_Z11counter_addP7Counteri`)。
CPP_CALLER = """\
module cppcaller
choose runtime

extern fn _Z11counter_newi(x: i32) -> ptr;
extern fn _Z11counter_addP7Counteri(c: ptr, d: i32) -> i32;

fn main() -> u32 {
    let c: ptr = _Z11counter_newi(5 as i32);
    if (c as u64) == 0 { return 10; }
    if _Z11counter_addP7Counteri(c, 37 as i32) != 42 { return 30; }
    return 0;
}
"""

#: 嵌入 CPython 的那一段: `PyRun_SimpleString` 让 CPython 自己算 `6*7` 并写出来。
#: 判据比的是**那个文件的内容** —— 也就是"CPython 真的在这个进程里跑并算对了",
#: 不是"链接没报错"。
PY_SCRIPT = "open('%s','w').write(str(6*7))\\n"
PY_CALLER = """\
module pycaller
choose runtime

extern fn Py_Initialize() -> u32;
extern fn PyRun_SimpleString(code: ptr) -> i32;

fn main() -> u32 {
    let buf: ptr = alloc(512);
    let code: str = "%s";
    let n: u32 = str_len(code);
    let i: u32 = 0;
    while i < n {
        store8(buf, i, str_byte(code, i) as u8);
        i = i + 1;
    }
    store8(buf, n, 0);
    Py_Initialize();
    if PyRun_SimpleString(buf) != 0 { return 10; }
    return 0;
}
"""

#: JNI 垫片 (与 `docs/222` §8 里那份同源): 把 JNI 的**间接调用**摊平成具名的直接调用,
#: 因为 Loment 的 codegen 至今不支持间接调用。
JNI_SHIM = """\
#include <jni.h>
#include <stdio.h>
#include <string.h>
static JavaVM *g_vm = NULL;
static JNIEnv *g_env = NULL;
long jvm_start(const char *classpath) {
    if (g_vm != NULL) { return 0; }
    static char cp[2048];
    snprintf(cp, sizeof(cp), "-Djava.class.path=%s", classpath);
    JavaVMOption opts[2];
    opts[0].optionString = cp;
    opts[1].optionString = "-Xrs";
    JavaVMInitArgs args;
    memset(&args, 0, sizeof(args));
    args.version = JNI_VERSION_1_8;
    args.nOptions = 2;
    args.options = opts;
    args.ignoreUnrecognized = JNI_FALSE;
    if (JNI_CreateJavaVM(&g_vm, (void **)&g_env, &args) != JNI_OK) { g_vm = NULL; return -1000; }
    return 0;
}
long jvm_static_int(const char *cls, const char *method, long a) {
    if (g_env == NULL) { return -1000; }
    jclass c = (*g_env)->FindClass(g_env, cls);
    if (c == NULL) { return -1001; }
    jmethodID mid = (*g_env)->GetStaticMethodID(g_env, c, method, "(I)I");
    if (mid == NULL) { return -1002; }
    return (long)(*g_env)->CallStaticIntMethod(g_env, c, mid, (jint)a);
}
void jvm_stop(void) { if (g_vm != NULL) { (*g_vm)->DestroyJavaVM(g_vm); g_vm = NULL; g_env = NULL; } }
"""

JAVA_LIB = """\
public class Hello {
    public static int compute(int seed) {
        int acc = seed;
        for (int i = 0; i < 10; i++) { acc = acc * 2 + i; }
        return acc;
    }
}
"""

#: `Hello.compute(1)` 推出来是 2037 (10 圈 acc = acc*2 + i), 不是抄的。
JAVA_CALLER = """\
module javacaller
choose runtime

extern fn jvm_start(classpath: ptr) -> i64;
extern fn jvm_static_int(cls: ptr, method: ptr, a: i64) -> i64;
extern fn jvm_stop();

fn put(p: ptr, s: str) -> u32 {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    while i < n {
        store8(p, i, str_byte(s, i) as u8);
        i = i + 1;
    }
    store8(p, n, 0);
    return n;
}

fn main() -> u32 {
    let cp: ptr = alloc(256);
    let cls: ptr = alloc(64);
    let mtd: ptr = alloc(64);
    put(cp, "%s");
    put(cls, "Hello");
    put(mtd, "compute");
    if jvm_start(cp) != 0 { return 10; }
    let v: i64 = jvm_static_int(cls, mtd, 1 as i64);
    jvm_stop();
    if v == 2037 { return 0; }
    if v < 0 { return (200 as u32) + ((0 - v) as u32 %% 100); }
    return 30;
}
"""

CS_LIB = """\
using System.Runtime.InteropServices;
public static class Lib
{
    [UnmanagedCallersOnly(EntryPoint = "cs_compute")]
    public static int Compute(int seed)
    {
        int acc = seed;
        for (int i = 0; i < 10; i++) { acc = acc * 3 - i; }
        return acc;
    }
}
"""

CS_PROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <PublishAot>true</PublishAot>
    <NativeLib>Shared</NativeLib>
    <InvariantGlobalization>true</InvariantGlobalization>
    <AssemblyName>CsLib</AssemblyName>
  </PropertyGroup>
</Project>
"""

#: `Lib.Compute(1)` 推出来是 44292 (10 圈 acc = acc*3 - i)。
CS_CALLER = """\
module cscaller
choose runtime

extern fn cs_compute(seed: i32) -> i32;

fn main() -> u32 {
    if cs_compute(1 as i32) == 44292 { return 0; }
    return 30;
}
"""


def _libpython_args(env: Env) -> list[str]:
    """系统那份 libpython 的 `-L` / `-l` —— 由 `sysconfig` 报出来, 不猜。"""
    rc, out = env.sh(
        "python3 -c \"import sysconfig as s;"
        "print(s.get_config_var('LIBDIR'), s.get_config_var('LDLIBRARY'))\"")
    assert rc == 0 and out.strip(), f"问不出 libpython 的位置: {out[-300:]}"
    libdir, soname = out.split()[-2], out.split()[-1]
    stem = soname.split(".so")[0]
    name = stem[3:] if stem.startswith("lib") else stem          # libpython3.12 -> python3.12
    return [f"-L{libdir}", f"-l{name}"]


def _java_home(env: Env) -> str | None:
    """JDK 根 —— 判据要的是 **JDK**(有 `include/jni.h`), 不是 JRE。"""
    rc, out = env.sh(
        "jh=$(readlink -f \"$(command -v javac)\" 2>/dev/null || true); "
        "jh=${jh%/bin/javac}; "
        "if [ -n \"$jh\" ] && [ -f \"$jh/include/jni.h\" ]; then echo \"$jh\"; fi")
    home = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return home or None


def _java_link_args(home: str) -> list[str]:
    """链 `libjvm.so` 要捎的那几件。rpath 走 `--cc-arg` 直通 —— 它不在标准库目录里。"""
    return [f"--cc-arg=-Wl,-rpath,{home}/lib/server",
            f"-L{home}/lib/server", "-ljvm", "-lstdc++", "-lpthread", "-ldl"]


def _dotnet(env: Env) -> str | None:
    rc, out = env.sh("command -v dotnet || true")
    s = [x.strip() for x in out.strip().splitlines() if x.strip()]
    return s[-1] if s else None


@test
def test_cpp_a_real_class_through_mangled_names():
    """C++: 按**改名字符**调一个真类 —— 没有 `extern "C"`, `this` 是第一个实参。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        env = Env(td, _resolves_with_flags(_clang() or "cc"))
        if not env.which("g++") and not env.which("clang++"):
            print("      SKIP: 这一侧没有 C++ 编译器")
            return
        d = td / "cpp"
        d.mkdir()
        (d / "foo.cpp").write_text(CPP_LIB, encoding="utf-8", newline="\n")
        cxx = env.which("g++") or env.which("clang++")
        rc, log = env.sh(f"cd '{env.path(d)}' && {cxx} -O1 -c foo.cpp -o foo.o")
        assert rc == 0, log[-600:]
        pkg = Pkg(td, env.native)
        pkg.emit(CPP_CALLER)
        out = str(td / "out.bin") if env.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted",
                           "--link", env.path(d / "foo.o"), "-lstdc++", "-o", out])
        assert rc == 0, f"C++ 那条返回 {rc}\n{log[-900:]}"
        print("      C++: 改名字符 + this 指针 + libstdc++ -> 42 (rc=0)")


@test
def test_python_embeds_cpython_and_evaluates():
    """Python: 进程里嵌 CPython, 让它**自己算** `6*7`, 判据比的是它写出来的数。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        env = Env(td, _resolves_with_flags(_clang() or "cc"))
        flags = _libpython_args(env)
        py_out = td / "py.txt"
        src = PY_CALLER % (PY_SCRIPT % env.path(py_out))
        pkg = Pkg(td, env.native)
        try:
            pkg.emit(src)
        except Exception as e:                                      # noqa: BLE001
            raise AssertionError(f"这份 Loment 源没编过: {e}") from e
        out = str(td / "out.bin") if env.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted",
                           *flags, "-o", out])
        assert rc == 0, f"Python 那条返回 {rc}\n{log[-900:]}"
        got = py_out.read_text(encoding="utf-8").strip() if py_out.exists() else ""
        assert got == "42", f"CPython 写出来的是 {got!r}, 期望 '42'"
        print("      Python: 进程里嵌 CPython, 它算出 6*7=42 (rc=0)")


@test
def test_java_calls_a_real_class_through_jni():
    """Java: 起 JVM, 调 `Hello.compute(1)` —— 2037 是推出来的, 不是抄的。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        env = Env(td, _resolves_with_flags(_clang() or "cc"))
        if not env.which("javac"):
            print("      SKIP: 这一侧没有 javac (CI 镜像里由 default-jdk 提供)")
            return
        home = _java_home(env)
        if home is None:
            print("      SKIP: 有 javac 但没有 JNI 头 (要 JDK, 不是 JRE)")
            return
        d = td / "java"
        (d / "classes").mkdir(parents=True)
        (d / "Hello.java").write_text(JAVA_LIB, encoding="utf-8", newline="\n")
        (d / "jni_shim.c").write_text(JNI_SHIM, encoding="utf-8", newline="\n")
        rc, log = env.sh(f"cd '{env.path(d)}' && javac -d classes Hello.java")
        assert rc == 0, f"javac 没编过:\n{log[-900:]}"
        rc, log = env.sh(f"cd '{env.path(d)}' && gcc -O1 -fPIC -c jni_shim.c -o jni_shim.o "
                         f"-I{home}/include -I{home}/include/linux")
        assert rc == 0, f"JNI 垫片没编过:\n{log[-900:]}"
        pkg = Pkg(td, env.native)
        pkg.emit(JAVA_CALLER % env.path(d / "classes"))
        out = str(td / "out.bin") if env.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted",
                           "--link", env.path(d / "jni_shim.o"),
                           *_java_link_args(home), "-o", out])
        assert rc == 0, f"Java 那条返回 {rc}\n{log[-900:]}"
        print("      Java: JNI 垫片 + libjvm, Hello.compute(1)=2037 (rc=0)")


@test
def test_csharp_nativeaot_library_is_callable():
    """C#: NativeAOT 出的 `.so` 上 `[UnmanagedCallersOnly]` 导出的那个符号。

    **这一条在 CI 上会 SKIP** —— 门禁镜像**故意**不含 `dotnet`
    (`.github/ci/Dockerfile` 的边界段写明理由: 那几条判据的 SKIP 已写进基线,
    加进来是改覆盖面)。所以"五个都跑通"这句话在 CI 上目前是**四个**;
    这一点写在 `docs/222` §8, 不在判据里假装。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        env = Env(td, _resolves_with_flags(_clang() or "cc"))
        dotnet = _dotnet(env)
        if not dotnet:
            print("      SKIP: 这一侧没有 dotnet (CI 镜像故意不含; 见 docs/222 §8)")
            return
        d = td / "cs"
        d.mkdir()
        (d / "CsLib.csproj").write_text(CS_PROJ, encoding="utf-8", newline="\n")
        (d / "Lib.cs").write_text(CS_LIB, encoding="utf-8", newline="\n")
        rc, log = env.sh(
            f"cd '{env.path(d)}' && DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 "
            f"{dotnet} publish -c Release -r linux-x64 --self-contained true -o out > /dev/null 2>&1")
        assert rc == 0, f"NativeAOT publish 没成功:\n{log[-900:]}"
        pkg = Pkg(td, env.native)
        pkg.emit(CS_CALLER)
        out = str(td / "out.bin") if env.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted",
                           "--link", env.path(d / "out" / "CsLib.so"), "-o", out])
        assert rc == 0, f"C# 那条返回 {rc}\n{log[-900:]}"
        print("      C#: NativeAOT 的 .so, Lib.Compute(1)=44292 (rc=0)")


def main() -> int:
    if not _clang():
        print("loment_hosted_test: SKIP —— 这台机器上没有 clang, "
              "IR 编不成目标文件 (docs/222)")
        return 0
    if not _resolves_with_flags(_clang()) and not _wsl():
        print("loment_hosted_test: SKIP —— 本机是 Windows 且没有 WSL: "
              "ELF 产物跑不起来 (docs/222)")
        return 0
    return loment_suite.run(TESTS, "loment_hosted_test")


if __name__ == "__main__":
    sys.exit(main())
