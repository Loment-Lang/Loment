#!/usr/bin/env python3
# loment_pe_test.py — 原生 PE 后端: Loment 程序在 Windows 上原生跑, 不经 WSL (docs/167)
#
# 判据:
#   1. 平台中性的语料的 PE 产物, 与 clang/Linux 参考路 **stdout 字节 + 退出码**一致;
#   2. 产物在本机**原生**跑 (判据 1 的参考路要 WSL, 而产物本身不需要) —— 这一条把
#      PATH 收成只剩 python, 证明整条"编译 + 运行"都不碰 clang 与 wsl;
#   3. 不支持的输入必须报错退出, 不许静默错编;
#   4. CLI: `--target pe --check` 不落盘 rc=0; 非法 `--target` rc=2。
#
# **语料只取平台中性的两个。** `bootprobe`(M76) 与 `selfcheck`(M77) 是 OS 探针 —— 一个打印
# uname 的 sysname, 一个断言 seccomp 沙箱里 getrandom 会失败。它们在 Windows 上**本就该不同**
# (shim 还没有 uname/getpid/seccomp), 拿它们当判据只会把平台差异误判成回归。
#
# 已知边界 (与 lomelf.py 头注同源): shim 只实现了 `write`(1) 与 `exit`(60), 其余 syscall 号
# 返回 -1。所以**还不能跑需要 argv 或文件 I/O 的程序** (openat/read/getdents64/newfstatat/brk
# 未做, Windows 也没有 procfs 可读)。
#
# 运行: python tools/loment_pe_test.py   (非 Windows 或无 clang/WSL 时 SKIP 相应用例)

from __future__ import annotations

import contextlib
import importlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


LOMELF = ROOT / "tools" / "lomelf.py"
PORTABLE = [
    "loment/examples/user_hello.lomt",
    "loment/examples/all_loment.lomt",
]
# 构建路径上的 Loment 工具 + 它们对应的 Python 参照物。这些是**真正要去 WSL 的**那一批 ——
# 它们要 argv、要开文件、要遍历目录，所以这条用例同时钉住 shim 的整个 syscall 面。
TOOLS = [
    ("lomstatus", "loment/tools/lomstatus.lomt", ["--check"], "loment_status"),
    ("lomrel", "loment/tools/lomrel.lomt", ["--check"], "loment_release"),
]
# 程序自己的规格输出（与 clang 路对过；这里是给"不碰 clang/WSL"那条用例用的定值）
GOLDEN = {
    "user_hello": (0, b"M67 RESULT: PASS loment-user\n"),
    "all_loment": (0, b"M78 sum=42 double=84 max=84 fib=55\nM78 RESULT: PASS all-loment\n"),
}
# 能力域探针（见 test_pe_links_capability_programs）: 域内两个槽位相加, 顺带过一次 syscall。
CAP_DEMO = """module capdemo
choose runtime

capability blk_write : disk[0..4] revocable

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);
    return slot;
}

fn _start() {
    let a: u32 = write_slot(3);
    let b: u32 = write_slot(1);
    if a + b == 4 {
        write_str(1, "CAP OK\\n");
        syscall4(60, 0, 0, 0);
    }
    write_str(1, "CAP BAD\\n");
    syscall4(60, 1, 0, 0);
}
"""
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _on_windows() -> bool:
    return sys.platform == "win32"


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _ir(src: Path, td: Path) -> Path:
    """参考实现发射 IR（PE 路与 clang 路喂同一份字节）。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def _run_native(exe: Path, timeout: int = 20) -> tuple[int, bytes]:
    """在本机直接跑 PE（不经 WSL）。退出码与 stdout 分开拿，不混在一个流里。"""
    r = subprocess.run([str(exe)], capture_output=True, timeout=timeout, shell=False)
    return r.returncode, r.stdout


def _run_in_wsl(elf: Path, name: str, td: Path, timeout: int = 20) -> tuple[int, bytes]:
    binn = f"{_T}lompe_{name}.bin"
    outp = td / f"{name}.out"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"timeout {timeout} {binn} > {_wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 120, shell=False)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


def _sections(blob: bytes) -> tuple[dict, list[tuple]]:
    """解出 PE 的关键头部与节表。字段偏移按 PE32+ 的规格（这里是判据，故意不复用 lomelf）。"""
    assert blob[:2] == b"MZ", "缺 MZ"
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    assert blob[pe:pe + 4] == b"PE\x00\x00", "缺 PE 签名"
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    opt = pe + 24
    machine = struct.unpack_from("<H", blob, pe + 4)[0]
    size_img = struct.unpack_from("<I", blob, opt + 56)[0]
    size_hdr = struct.unpack_from("<I", blob, opt + 60)[0]
    entry = struct.unpack_from("<I", blob, opt + 16)[0]
    sec_align, file_align = struct.unpack_from("<II", blob, opt + 32)
    head = {"machine": machine, "nsec": nsec, "size_image": size_img,
            "size_headers": size_hdr, "entry": entry,
            "sec_align": sec_align, "file_align": file_align}
    st = opt + struct.unpack_from("<H", blob, pe + 20)[0]
    secs = []
    for i in range(nsec):
        h = st + 40 * i
        name = blob[h:h + 8].rstrip(b"\0").decode()
        vs, va, rs, rp, ch = (struct.unpack_from("<I", blob, h + 8)[0],
                              struct.unpack_from("<I", blob, h + 12)[0],
                              struct.unpack_from("<I", blob, h + 16)[0],
                              struct.unpack_from("<I", blob, h + 20)[0],
                              struct.unpack_from("<I", blob, h + 36)[0])
        secs.append((name, vs, va, rs, rp, ch))
    return head, secs


# ---------------------------------------------------------------- 用例

@test
def test_pe_image_is_structurally_sane():
    """PE 头部与节表必须自洽 —— 这几条正是当初把加载器搞拒收的地方。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in PORTABLE:
            src = ROOT / rel
            blob, _info = lomelf.compile_pe(_ir(src, td).read_text(encoding="utf-8"))
            head, secs = _sections(blob)
            assert head["machine"] == 0x8664, f"{src.name}: 不是 x64"
            assert head["size_headers"] and head["size_headers"] % head["file_align"] == 0, \
                f"{src.name}: SizeOfHeaders 未按 FileAlignment 对齐 ({head['size_headers']})"
            assert head["size_image"] % head["sec_align"] == 0, f"{src.name}: SizeOfImage 未对齐"
            # 节区按 RVA 升序且互不重叠（早先 .text 固定 0x1000 / .data 固定 0x2000，
            # 文本一过一页两节就重叠）
            end = 0
            for name, vs, va, _rs, _rp, _ch in secs:
                assert va >= end, f"{src.name}: {name} 与前节重叠 (va={va:#x} < {end:#x})"
                end = va + max(vs, 1)
            assert end <= head["size_image"], f"{src.name}: SizeOfImage 盖不住最后一节"
            # 入口必须落在某个可执行节里
            hit = [s for s in secs if s[2] <= head["entry"] < s[2] + max(s[1], 1) and s[5] & 0x20000000]
            assert hit, f"{src.name}: 入口 {head['entry']:#x} 不在可执行节里"
            # 文件必须盖住所有节的原始数据，否则加载器读到 EOF 之外直接拒收
            need = max(rp + rs for _n, _v, _a, rs, rp, _c in secs)
            assert len(blob) >= need, f"{src.name}: 文件 {len(blob)} < 节数据需要 {need}"
    print("      PE 头部/节表自洽: 对齐、不重叠、入口可执行、文件盖得住节数据")


@test
def test_pe_runs_natively_matches_linux_behavior():
    """同一份 IR: PE 产物(本机原生) 与 clang/Linux 产物(WSL) 的 stdout 字节 + 退出码一致。"""
    clang = _clang()
    if not (_on_windows() and clang and _wsl()):
        print("      SKIP: 非 Windows 或无 clang/WSL")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in PORTABLE:
            src = ROOT / rel
            name = src.stem
            ll = _ir(src, td)
            ref = td / f"{name}.elf"
            r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                                "-ffreestanding", "-static", "-fno-pie", "-fuse-ld=lld",
                                "-Wl,-e,_start", str(ll), "-o", str(ref)],
                               capture_output=True, text=True, shell=False)
            assert r.returncode == 0, f"clang 链接失败: {r.stderr[-300:]}"
            exe = td / f"{name}.exe"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            exe.write_bytes(blob)
            want = _run_in_wsl(ref, f"{name}_c", td)
            got = _run_native(exe)
            if want != got:
                raise AssertionError(
                    f"[{name}] 行为不一致: rc clang/wsl={want[0]} pe/native={got[0]}\n"
                    f"  linux ({len(want[1])}B): {want[1][:200]!r}\n"
                    f"  pe    ({len(got[1])}B): {got[1][:200]!r}")
            ok += 1
    print(f"      {ok} 个程序的 PE 产物与 clang/Linux 产物 stdout 字节 + 退出码一致")


@test
def test_pe_builds_and_runs_without_clang_or_wsl():
    """**去 WSL 的判据**: PATH 里只剩 python 也编得出、跑得起来。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent)      # 没有 clang、没有 wsl
        for rel in PORTABLE:
            src = ROOT / rel
            name = src.stem
            ll = _ir(src, td)
            exe = td / f"{name}.exe"
            r = subprocess.run([sys.executable, str(LOMELF), str(ll),
                                "--target", "pe", "-o", str(exe)],
                               capture_output=True, text=True, shell=False, env=env)
            assert r.returncode == 0, f"[{name}] 编译失败: {r.stderr[-300:]}"
            assert exe.exists(), f"[{name}] 没落盘"
            assert _run_native(exe) == GOLDEN[name], f"[{name}] 输出与定值不符"
    print(f"      PATH 里没有 clang/wsl 也能编出并跑起来 ({len(PORTABLE)} 个程序)")


@test
def test_pe_links_capability_programs():
    """能力域程序必须能走原生后端 —— 它的域描述表是**结构体全局初值**。

    回归护栏：`capability` 会发一张 `[N x { i64, i64, i64, i64 }]` 的表, 而 `_parse_init`
    原先只认标量/字节串/数组, 于是**任何带 capability 的程序**在链接期直接
    `v0 不支持的全局初值` 失败 —— 连仓库自己的 `loment/examples/native_cap.lomt` 都编不出来。
    这个用例把"能编 + 能跑 + 越界真的 trap"三件事一起钉住。
    """
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "capdemo.lomt"
        src.write_text(CAP_DEMO, encoding="utf-8", newline="\n")
        ll = _ir(src, td)
        assert "[1 x { i64, i64, i64, i64 }]" in ll.read_text(encoding="utf-8"), \
            "域描述表形态变了 —— 这个用例盯的就是它的初值解析"
        exe = td / "capdemo.exe"
        blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
        exe.write_bytes(blob)
        assert _run_native(exe) == (0, b"CAP OK\n"), "域内 guard 应当放行"
        # 非字面量越界 -> 运行期 trap (IR 后端是 ud2, 退出码 132/SIGILL)
        trap = td / "captrap.lomt"
        trap.write_text(CAP_DEMO.replace("write_slot(1)", "write_slot(9)"),
                        encoding="utf-8", newline="\n")
        tll = _ir(trap, td)
        texe = td / "captrap.exe"
        texe.write_bytes(lomelf.compile_pe(tll.read_text(encoding="utf-8"))[0])
        assert _run_native(texe)[0] != 0, "运行期越界应当 trap, 不该正常退出"
        # 字面量越界 -> 编译期就拒 (参考实现的 check)
        lit = td / "caplit.lomt"
        lit.write_text(CAP_DEMO.replace("guard blk_write(slot);", "guard blk_write(9);"),
                       encoding="utf-8", newline="\n")
        mod = lomentc.load(lit)
        deps = lomentc.resolve_deps(mod, ROOT, lit.parent, entry=lit)
        assert any("越界" in e for e in lomentc.check(mod, deps=deps)), \
            "字面量越界必须在检查期报错"
    print("      能力域程序: 域内放行 / 越界 trap / 字面量越界拒绝, 且走的是原生 PE")


@test
def test_pe_runs_the_loment_toolchain_natively():
    """**去 WSL 的判据（构建路径）**：Loment 工具本身编成 PE 后在本机原生跑，
    stdout 字节 + 退出码与 Python 参照版一致 —— 也就是说这台机器上已经不需要
    "把 ELF 丢进 WSL" 才能跑构建工具。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for name, rel, argv, pymod in TOOLS:
            ll = _ir(ROOT / rel, td)
            exe = td / f"{name}.exe"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            exe.write_bytes(blob)
            # 工具按相对路径解析输入，必须在仓库根跑
            got = subprocess.run([str(exe)] + argv, capture_output=True,
                                 cwd=str(ROOT), shell=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    rc = importlib.import_module(pymod).main(list(argv))
                except SystemExit as e:
                    rc = e.code
            want = buf.getvalue().encode("utf-8")
            assert (rc, want) == (got.returncode, got.stdout), (
                f"[{name}] 不一致: rc {rc} vs {got.returncode}\n"
                f"  python ({len(want)}B): {want[:160]!r}\n"
                f"  pe     ({len(got.stdout)}B): {got.stdout[:160]!r}")
            ok += 1
    print(f"      {ok} 个构建工具（argv + 目录遍历 + 文件 I/O）"
          f"在 Windows 上原生跑，与 Python 版逐字节相同")


@test
def test_pe_runs_the_selfhost_compiler_natively():
    """**去 WSL 的判据（编译器侧）**：自举种子编成 PE 后，在 Windows 上**原生当编译器用** ——
    对语料产出的 IR 与参考实现逐字节相同；那份 IR 再喂回 `lomelf --target pe`，
    产物跑出来的行为也对得上。整条链在这台机器上不碰 clang、不碰 WSL。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    seed = ROOT / "loment/build/selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        drv = td / "driver.exe"
        blob, _info = lomelf.compile_pe(seed.read_text(encoding="utf-8"))
        drv.write_bytes(blob)
        ok = 0
        for rel in PORTABLE:
            src = ROOT / rel
            ref = _ir(src, td)                          # 参考实现发的 IR
            r = subprocess.run([str(drv), rel], capture_output=True,
                               cwd=str(ROOT), shell=False)   # 入口按仓库相对路径给
            assert r.returncode == 0, f"[{src.stem}] 编译器 rc={r.returncode}: {r.stderr[-200:]!r}"
            assert r.stdout == ref.read_bytes(), f"[{src.stem}] IR 与参考不一致"
            # 全链：那份 IR -> PE -> 跑，行为与定值一致
            prog = td / f"{src.stem}.exe"
            prog.write_bytes(lomelf.compile_pe(r.stdout.decode("utf-8"))[0])
            assert _run_native(prog) == GOLDEN[src.stem], f"[{src.stem}] 全链产物行为不符"
            ok += 1
    print(f"      {ok} 个语料: 自举种子编成的 PE 原生当编译器用, IR 逐字节相同且全链可跑")


@test
def test_pe_selfhost_mirror_matches_reference():
    """**去 WSL 的最后一道坎**：自举侧的镜像 `loment/tools/lomelf.lomt` 也能出 PE，
    且与参考实现的 PE **逐字节相同**。也就是说 Windows 包里那个"把 .ll 变成 .exe"的
    链接器可以是自举产物，不必是 Python 也不必是 clang。

    镜像本体是 Linux ELF（走种子自举链编出来），所以它在 WSL 里跑 —— 但它**产出的**是 PE。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        script = (f"cp {_wsl_path(s1)} {_T}lompe_s1.bin && chmod +x {_T}lompe_s1.bin && "
                  f"cd {_wsl_path(ROOT)} && {_T}lompe_s1.bin loment/tools/lomelf.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编镜像失败: {rr.stderr[-300:]}"
        mir_ll = td / "lomelf.ll"
        mir_ll.write_bytes(rr.stdout)
        mir = td / "lomelf.bin"
        r2 = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(mir_ll), "-o", str(mir)],
            capture_output=True, text=True, shell=False)
        assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]}"
        ok = 0
        for rel in PORTABLE:
            src = ROOT / rel
            ll = _ir(src, td)
            got = td / f"{src.stem}.mir.exe"
            script = (f"cd {_wsl_path(ROOT)} && {_wsl_path(mir)} "
                      f"{_wsl_path(ll)} {_wsl_path(got)}")
            r3 = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                                capture_output=True, timeout=300, shell=False)
            assert r3.returncode == 0, f"镜像出 PE 失败: {r3.stderr[-300:]!r}"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            assert got.read_bytes() == blob, f"[{src.stem}] 镜像的 PE 与参考不一致"
            ok += 1
    print(f"      {ok} 个语料: 自举镜像产出的 PE 与参考逐字节相同（去 WSL 的最后一道坎）")


@test
def test_pe_reports_unsupported_instead_of_miscompiling():
    """不支持的东西必须**报错退出**, 不许静默编出一个错的 PE。"""
    bad = [
        ("结构体动态下标 GEP",
         "define void @_start() {\nentry:\n  %p = alloca { i32, i32 }\n  %i = load i32, ptr %p\n"
         "  %q = getelementptr { i32, i32 }, ptr %p, i32 %i\n  ret void\n}\n"),
        ("间接调用",
         "define void @_start() {\nentry:\n  %f = alloca ptr\n  %g = load ptr, ptr %f\n"
         "  call void %g()\n  ret void\n}\n"),
        ("没有 _start",
         "define void @main() {\nentry:\n  ret void\n}\n"),
    ]
    for label, text in bad:
        try:
            lomelf.compile_pe(text)
        except lomelf.Unsupported as e:
            assert str(e), f"{label}: 报了 Unsupported 但没有消息"
            continue
        raise AssertionError(f"[{label}] 本该报 Unsupported, 却编过去了")
    print(f"      {len(bad)} 类不支持的输入都报了错")


@test
def test_pe_cli_check_and_usage():
    """CLI: `--target pe --check` 不落盘且 rc=0; 非法 `--target` rc=2。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ir(ROOT / PORTABLE[0], td)
        r = subprocess.run([sys.executable, str(LOMELF), str(ll), "--target", "pe", "--check"],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"--check rc={r.returncode}: {r.stderr[-200:]}"
        assert "[OK]" in r.stdout, r.stdout[-200:]
        assert not (td / "user_hello.exe").exists(), "--check 不该落盘"
        r2 = subprocess.run([sys.executable, str(LOMELF), str(ll), "--target", "macho"],
                            capture_output=True, text=True, shell=False)
        assert r2.returncode == 2, f"非法 --target 应当 rc=2, 得到 {r2.returncode}"
    print("      --target pe --check 不落盘 rc=0 · 非法 --target rc=2")


#: 联网语料：**一份源、两个平台**。它自己起监听（端口填 0 ⇒ 由内核挑临时端口）、
#: 自己连自己，一次把 shim 的联网面走完：socket / setsockopt(SO_REUSEADDR) / bind /
#: listen / getsockname / connect / accept / read / write（socket 型 fd 上分流到 recv/send）/
#: setsockopt(SO_RCVTIMEO)（timeval → 毫秒） / sendto(addr=NULL, MSG_NOSIGNAL)。
#: 三行输出各钉一件事：回显对得上 / 读超时返回 -EAGAIN / 对端挂断之后进程还活着。
#: 走 127.0.0.1 + 临时端口，所以并发跑门禁也不会撞端口。
SOCK_DEMO = """module sockdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa_init(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn _start() {
    let sa: ptr = alloc(64);
    let _z: u32 = sa_init(sa, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket(listener)\\n");
    }
    let one: ptr = alloc(8);
    store8(one, 0, 1);
    let so: i64 = syscall6(54, ls as u64, 1, 2, one as u64, 4);
    if so < 0 {
        die(2, "FAIL setsockopt(SO_REUSEADDR)\\n");
    }
    let bi: i64 = syscall6(49, ls as u64, sa as u64, 16, 0, 0);
    if bi < 0 {
        die(3, "FAIL bind\\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(4, "FAIL listen\\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let gn: i64 = syscall6(51, ls as u64, sa as u64, ln as u64, 0, 0);
    if gn < 0 {
        die(5, "FAIL getsockname\\n");
    }
    if load8(sa, 3) == 0 {
        die(6, "FAIL ephemeral port is 0\\n");
    }
    let cs: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if cs < 0 {
        die(7, "FAIL socket(client)\\n");
    }
    let cn: i64 = syscall6(42, cs as u64, sa as u64, 16, 0, 0);
    if cn < 0 {
        die(8, "FAIL connect\\n");
    }
    let ac: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if ac < 0 {
        die(9, "FAIL accept\\n");
    }
    let pw: i64 = syscall6(1, cs as u64, str_ptr("ping") as u64, 4, 0, 0);
    if pw != 4 {
        die(10, "FAIL client write\\n");
    }
    let rb: ptr = alloc(64);
    let r1: i64 = syscall6(0, ac as u64, rb as u64, 4, 0, 0);
    if r1 != 4 {
        die(11, "FAIL server read\\n");
    }
    if load8(rb, 0) != 112 {
        die(12, "FAIL echo byte\\n");
    }
    let pw2: i64 = syscall6(1, ac as u64, str_ptr("pong") as u64, 4, 0, 0);
    if pw2 != 4 {
        die(13, "FAIL server write\\n");
    }
    let r2: i64 = syscall6(0, cs as u64, rb as u64, 4, 0, 0);
    if r2 != 4 {
        die(14, "FAIL client read\\n");
    }
    if load8(rb, 1) != 111 {
        die(15, "FAIL echo byte 2\\n");
    }
    // getpeername：出口方向也写回 sockaddr（AF_INET 两边同值，只验它填了东西）
    let pa: ptr = alloc(64);
    let pn: ptr = alloc(8);
    store8(pn, 0, 16);
    let gp: i64 = syscall6(52, ac as u64, pa as u64, pn as u64, 0, 0);
    if gp < 0 {
        die(16, "FAIL getpeername\\n");
    }
    if load8(pa, 1) != 0 {
        die(17, "FAIL getpeername family\\n");
    }
    if load8(pa, 3) == 0 {
        die(18, "FAIL getpeername port\\n");
    }
    w(1, "ECHO OK\\n");

    let tv: ptr = alloc(16);
    store8(tv, 8, 224);
    store8(tv, 9, 147);
    store8(tv, 10, 4);
    let st1: i64 = syscall6(54, ac as u64, 1, 20, tv as u64, 16);
    if st1 < 0 {
        die(19, "FAIL setsockopt(SO_RCVTIMEO)\\n");
    }
    let r3: i64 = syscall6(0, ac as u64, rb as u64, 4, 0, 0);
    if r3 != -11 {
        die(20, "FAIL read-timeout is not -EAGAIN\\n");
    }
    // getsockopt 反向那一次换算：毫秒摊回 timeval，并把 *optlen 写回 16
    let gb: ptr = alloc(16);
    let gl: ptr = alloc(8);
    store8(gl, 0, 16);
    let gs: i64 = syscall6(55, ac as u64, 1, 20, gb as u64, gl as u64);
    if gs < 0 {
        die(21, "FAIL getsockopt(SO_RCVTIMEO)\\n");
    }
    if load8(gl, 0) != 16 {
        die(22, "FAIL getsockopt optlen\\n");
    }
    if load8(gb, 8) != 224 {
        die(23, "FAIL getsockopt usec low\\n");
    }
    if load8(gb, 9) != 147 {
        die(24, "FAIL getsockopt usec high\\n");
    }
    w(1, "TIMEOUT OK\\n");

    // accept4 + SOCK_NONBLOCK：新 socket 要真的建成非阻塞（read 立刻 -EAGAIN）
    let cs2: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if cs2 < 0 {
        die(25, "FAIL socket(client2)\\n");
    }
    let cn2: i64 = syscall6(42, cs2 as u64, sa as u64, 16, 0, 0);
    if cn2 < 0 {
        die(26, "FAIL connect 2\\n");
    }
    let ac2: i64 = syscall6(288, ls as u64, 0, 0, 2048, 0);
    if ac2 < 0 {
        die(27, "FAIL accept4\\n");
    }
    let r4: i64 = syscall6(0, ac2 as u64, rb as u64, 4, 0, 0);
    if r4 != -11 {
        die(28, "FAIL accept4 socket is not non-blocking\\n");
    }
    syscall6(3, ac2 as u64, 0, 0, 0, 0);
    syscall6(3, cs2 as u64, 0, 0, 0, 0);
    w(1, "NONBLOCK OK\\n");

    // AF_INET6 的两侧翻译（Linux=10 / Windows=23）。**允许这台机器没有 IPv6**：
    // 那就两边一起打印 IPV6 NO —— 判据是"两个平台看到同一件事"，不是"必须有 IPv6"。
    let s6: i64 = syscall6(41, 10, 1, 0, 0, 0);
    if s6 < 0 {
        w(1, "IPV6 NO\\n");
    } else {
        let a6: ptr = alloc(64);
        store8(a6, 0, 10);
        store8(a6, 1, 0);
        store8(a6, 23, 1);
        let b6: i64 = syscall6(49, s6 as u64, a6 as u64, 28, 0, 0);
        if b6 < 0 {
            w(1, "IPV6 NO\\n");
        } else {
            let n6: ptr = alloc(8);
            store8(n6, 0, 28);
            let g6: i64 = syscall6(51, s6 as u64, a6 as u64, n6 as u64, 0, 0);
            if g6 < 0 {
                die(29, "FAIL v6 getsockname\\n");
            }
            if load8(a6, 0) != 10 {
                die(30, "FAIL v6 family not translated back\\n");
            }
            let l6: i64 = syscall6(50, s6 as u64, 4, 0, 0, 0);
            if l6 < 0 {
                die(31, "FAIL v6 listen\\n");
            }
            let v6c: i64 = syscall6(41, 10, 1, 0, 0, 0);
            if v6c < 0 {
                die(32, "FAIL v6 client socket\\n");
            }
            let nc: i64 = syscall6(42, v6c as u64, a6 as u64, 28, 0, 0);
            if nc < 0 {
                die(33, "FAIL v6 connect\\n");
            }
            let ac6: i64 = syscall6(43, s6 as u64, 0, 0, 0, 0);
            if ac6 < 0 {
                die(34, "FAIL v6 accept\\n");
            }
            let w6: i64 = syscall6(1, v6c as u64, str_ptr("v") as u64, 1, 0, 0);
            if w6 != 1 {
                die(35, "FAIL v6 write\\n");
            }
            let r6: i64 = syscall6(0, ac6 as u64, rb as u64, 1, 0, 0);
            if r6 != 1 {
                die(36, "FAIL v6 read\\n");
            }
            if load8(rb, 0) != 118 {
                die(37, "FAIL v6 payload\\n");
            }
            syscall6(3, ac6 as u64, 0, 0, 0, 0);
            syscall6(3, v6c as u64, 0, 0, 0, 0);
            w(1, "IPV6 OK\\n");
        }
        syscall6(3, s6 as u64, 0, 0, 0, 0);
    }

    let extra: i64 = syscall6(1, cs as u64, str_ptr("xxxx") as u64, 4, 0, 0);
    if extra != 4 {
        die(31, "FAIL client write 2\\n");
    }
    syscall6(3, cs as u64, 0, 0, 0, 0);
    let big: ptr = alloc(2048);
    let _x1: i64 = syscall6(44, ac as u64, big as u64, 2048, 16384, 0);
    let _x2: i64 = syscall6(44, ac as u64, big as u64, 2048, 16384, 0);
    w(1, "SURVIVED\\n");
    let _sd: i64 = syscall6(48, ac as u64, 2, 0, 0, 0);
    syscall6(3, ac as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""

#: **UDP 语料**：它证明的是"七个值的系统调用"—— `sendto`(44)/`recvfrom`(45) 的第 6 个
#: 实参（`socklen_t`）原先**没有地方放**，所以"带地址的 UDP"从 Loment 源码里写不出来
#: （`docs/217` §5 点名的缺口）。它两个方向都走：客户端 `sendto` 到一个已知 `sockaddr`、
#: 服务端 `recvfrom` 拿到**发送方的地址**、再用那个地址回一发、客户端 `recvfrom` 收回来。
#: 地址是 `127.0.0.1` + **内核挑的临时端口**（`getsockname` 问出来），所以并发跑门禁不撞端口。
UDP_DEMO = """module udpdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn _start() {
    let a: ptr = alloc(64);
    let b: ptr = alloc(64);
    let _z1: u32 = sa(a, 0, 0);
    let _z2: u32 = sa(b, 0, 0);
    let s1: i64 = syscall6(41, 2, 2, 0, 0, 0);
    if s1 < 0 {
        die(1, "FAIL socket(1)\n");
    }
    let s2: i64 = syscall6(41, 2, 2, 0, 0, 0);
    if s2 < 0 {
        die(2, "FAIL socket(2)\n");
    }
    let l1: ptr = alloc(8);
    store8(l1, 0, 16);
    let b1: i64 = syscall6(49, s1 as u64, a as u64, 16, 0, 0);
    if b1 < 0 {
        die(3, "FAIL bind(1)\n");
    }
    let l2: ptr = alloc(8);
    store8(l2, 0, 16);
    let b2: i64 = syscall6(49, s2 as u64, b as u64, 16, 0, 0);
    if b2 < 0 {
        die(4, "FAIL bind(2)\n");
    }
    let g1: i64 = syscall6(51, s1 as u64, a as u64, l1 as u64, 0, 0);
    if g1 < 0 {
        die(5, "FAIL getsockname\n");
    }
    let sb: ptr = alloc(64);
    store8(sb, 0, 104);
    store8(sb, 1, 105);
    let t1: i64 = syscall7(44, s2 as u64, sb as u64, 2, 0, a as u64, 16);
    if t1 != 2 {
        die(6, "FAIL sendto\n");
    }
    let rb: ptr = alloc(64);
    let src: ptr = alloc(64);
    let sl: ptr = alloc(8);
    store8(sl, 0, 16);
    let r1: i64 = syscall7(45, s1 as u64, rb as u64, 8, 0, src as u64, sl as u64);
    if r1 != 2 {
        die(7, "FAIL recvfrom\n");
    }
    if load8(rb, 0) != 104 {
        die(8, "FAIL payload\n");
    }
    if load8(src, 1) != 0 {
        die(9, "FAIL src family\n");
    }
    if load8(src, 3) == 0 {
        die(10, "FAIL src port\n");
    }
    w(1, "UDP RECV OK\n");
    store8(rb, 0, 121);
    store8(rb, 1, 111);
    let n2: i64 = load8(sl, 0) as i64;
    let t2: i64 = syscall7(44, s1 as u64, rb as u64, 2, 0, src as u64, n2 as u64);
    if t2 != 2 {
        die(11, "FAIL reply\n");
    }
    let r2: i64 = syscall7(45, s2 as u64, rb as u64, 8, 0, src as u64, sl as u64);
    if r2 != 2 {
        die(12, "FAIL client recvfrom\n");
    }
    if load8(rb, 1) != 111 {
        die(13, "FAIL reply payload\n");
    }
    w(1, "UDP OK\n");
    syscall6(3, s1 as u64, 0, 0, 0, 0);
    syscall6(3, s2 as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
UDP_GOLDEN = b"UDP RECV OK\nUDP OK\n"


def _udp_ok(out: bytes) -> bool:
    return out == UDP_GOLDEN


#: **多路复用语料**：两个连接，一个有数据、一个没有，`poll`(7) 必须**只**报有数据的那个；
#: 把数据读掉之后再 `poll` 一次，两个都不就绪 ⇒ 超时返回 **0**（不是永远阻塞）。
#: 这条钉的是 PE 侧那一格：`poll` 在 Windows 上没得直接转发 —— WinSock 只有 `select`，
#: 两边的事件位还**不同值**（Linux `POLLIN`=1 / Windows `POLLRDNORM`=0x100），
#: 所以 shim 得把 `pollfd` 数组翻成三个 `fd_set`、再把就绪翻回去（`docs/221`）。
POLL_DEMO = """module polldemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn pfd(p: ptr, fd: i64, ev: u8) -> u32 {
    store8(p, 0, fd as u8);
    store8(p, 1, 0);
    store8(p, 2, 0);
    store8(p, 3, 0);
    store8(p, 4, ev);
    store8(p, 5, 0);
    store8(p, 6, 0);
    store8(p, 7, 0);
    return 0;
}

fn _start() {
    let sa0: ptr = alloc(64);
    let _z: u32 = sa(sa0, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket(ls)\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let bi: i64 = syscall6(49, ls as u64, sa0 as u64, 16, 0, 0);
    if bi < 0 {
        die(2, "FAIL bind\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(3, "FAIL listen\n");
    }
    let g1: i64 = syscall6(51, ls as u64, sa0 as u64, ln as u64, 0, 0);
    if g1 < 0 {
        die(4, "FAIL getsockname\n");
    }
    let c1: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if c1 < 0 {
        die(5, "FAIL socket(c1)\n");
    }
    let n1: i64 = syscall6(42, c1 as u64, sa0 as u64, 16, 0, 0);
    if n1 < 0 {
        die(6, "FAIL connect 1\n");
    }
    let a1: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a1 < 0 {
        die(7, "FAIL accept 1\n");
    }
    let d1: i64 = syscall6(1, c1 as u64, str_ptr("hi") as u64, 2, 0, 0);
    if d1 != 2 {
        die(8, "FAIL write 1\n");
    }
    let c2: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if c2 < 0 {
        die(9, "FAIL socket(c2)\n");
    }
    let n2: i64 = syscall6(42, c2 as u64, sa0 as u64, 16, 0, 0);
    if n2 < 0 {
        die(10, "FAIL connect 2\n");
    }
    let a2: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a2 < 0 {
        die(11, "FAIL accept 2\n");
    }
    let pf: ptr = alloc(64);
    let pf2: ptr = ptr_add(pf, 8);
    let _p1: u32 = pfd(pf, a1, 1);
    let _p2: u32 = pfd(pf2, a2, 1);
    let r1: i64 = syscall6(7, pf as u64, 2, 3000, 0, 0);
    if r1 != 1 {
        die(12, "FAIL poll count\n");
    }
    if load8(pf, 6) != 1 {
        die(13, "FAIL poll revents[0]\n");
    }
    if load8(pf2, 6) != 0 {
        die(14, "FAIL poll revents[1]\n");
    }
    w(1, "POLL READY OK\n");
    let rb: ptr = alloc(64);
    let rd: i64 = syscall6(0, a1 as u64, rb as u64, 8, 0, 0);
    if rd != 2 {
        die(15, "FAIL read a1\n");
    }
    let r2: i64 = syscall6(7, pf as u64, 2, 200, 0, 0);
    if r2 != 0 {
        die(16, "FAIL poll timeout\n");
    }
    if load8(pf, 6) != 0 {
        die(17, "FAIL timeout revents\n");
    }
    w(1, "POLL TIMEOUT OK\n");
    syscall6(3, a1 as u64, 0, 0, 0, 0);
    syscall6(3, a2 as u64, 0, 0, 0, 0);
    syscall6(3, c1 as u64, 0, 0, 0, 0);
    syscall6(3, c2 as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
SEL_DEMO = """module seldemo
choose runtime              // 夹具里用 `%` 与 `/`（位图按字节定位）—— 按 main 的
                            // `no_runtime` 保证，用到运行期就得在**根单元**声明

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

// 128 字节的 fd_set 位图清零（x86-64 是 16 个 u64）
fn fs_clr(p: ptr) -> u32 {
    let i: u32 = 0;
    while i < 128 {
        store8(p, i, 0);
        i = i + 1;
    }
    return 0;
}

// 置第 fd 位：`fd_set` 是**位图**，第 fd 位落在第 `fd / 8` 个字节、字节内第 `fd % 8` 位。
// ⚠ 别只写第一个字节 —— 这个夹具里第 4 个 socket 就是 fd 8，只写 byte 0 会把那一位
// **截断成 0**（`1 << 8` 塞进 u8 是 0），于是 writefds 是空的、`select` 干等到超时：
// 症状长得像"shim 没归一化"，其实是语料自己没置上位。
fn fs_set(p: ptr, fd: i64) -> u32 {
    let bi: i64 = fd / 8;
    let b: u32 = 1;
    let k: i64 = 0;
    while k < fd % 8 {
        b = b * 2;
        k = k + 1;
    }
    let old: u32 = load8(p, bi as u32);
    store8(p, bi as u32, (old | b) as u8);
    return 0;
}

// 第 fd 位是一吗（多位置着也判得对：先除再模）
fn fs_has(p: ptr, fd: i64) -> u32 {
    let bi: i64 = fd / 8;
    let b: u32 = 1;
    let k: i64 = 0;
    while k < fd % 8 {
        b = b * 2;
        k = k + 1;
    }
    let v: u32 = load8(p, bi as u32);
    if (v / b) % 2 == 1 {
        return 1;
    }
    return 0;
}

// Linux 的 struct timeval 是**两个 i64**（16 字节）—— Windows 那边是两个 32 位 long，
// 换算在 shim 里做。这里只写微秒那一格（秒保持 0）：都是亚秒的超时。
fn put64(p: ptr, off: u32, v: i64) -> u32 {
    let k: u32 = 0;
    let x: i64 = v;
    while k < 8 {
        store8(p, off + k, (x % 256) as u8);
        x = x / 256;
        k = k + 1;
    }
    return 0;
}

fn _start() {
    let sa0: ptr = alloc(64);
    let _z: u32 = sa(sa0, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket(ls)\\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let bi: i64 = syscall6(49, ls as u64, sa0 as u64, 16, 0, 0);
    if bi < 0 {
        die(2, "FAIL bind\\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(3, "FAIL listen\\n");
    }
    let g1: i64 = syscall6(51, ls as u64, sa0 as u64, ln as u64, 0, 0);
    if g1 < 0 {
        die(4, "FAIL getsockname\\n");
    }
    let c1: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if c1 < 0 {
        die(5, "FAIL socket(c1)\\n");
    }
    let n1: i64 = syscall6(42, c1 as u64, sa0 as u64, 16, 0, 0);
    if n1 < 0 {
        die(6, "FAIL connect 1\\n");
    }
    let a1: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a1 < 0 {
        die(7, "FAIL accept 1\\n");
    }
    let d1: i64 = syscall6(1, c1 as u64, str_ptr("hi") as u64, 2, 0, 0);
    if d1 != 2 {
        die(8, "FAIL write 1\\n");
    }
    let c2: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if c2 < 0 {
        die(9, "FAIL socket(c2)\\n");
    }
    let n2: i64 = syscall6(42, c2 as u64, sa0 as u64, 16, 0, 0);
    if n2 < 0 {
        die(10, "FAIL connect 2\\n");
    }
    let a2: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a2 < 0 {
        die(11, "FAIL accept 2\\n");
    }
    // ---- ① 可读：a1 有数据、a2 没有 ----
    let rf: ptr = alloc(128);
    let wf: ptr = alloc(128);
    let tv: ptr = alloc(16);
    let _q0: u32 = fs_clr(rf);
    let _q1: u32 = fs_clr(wf);
    let _s1: u32 = fs_set(rf, a1);
    let _s2: u32 = fs_set(rf, a2);
    let _t1: u32 = put64(tv, 0, 0);
    let _t2: u32 = put64(tv, 8, 300000);
    let r1: i64 = syscall6(23, (a2 + 1) as u64, rf as u64, 0, 0, tv as u64);
    if r1 != 1 {
        die(12, "FAIL select(read) count\\n");
    }
    if fs_has(rf, a1) != 1 {
        die(13, "FAIL select(read) a1 ready\\n");
    }
    if fs_has(rf, a2) != 0 {
        die(14, "FAIL select(read) a2 not ready\\n");
    }
    w(1, "SELECT READY OK\\n");
    let rb: ptr = alloc(64);
    let rd: i64 = syscall6(0, a1 as u64, rb as u64, 8, 0, 0);
    if rd != 2 {
        die(15, "FAIL read a1\\n");
    }
    // ---- ② 超时：两个都没数据 ----
    let _q2: u32 = fs_clr(rf);
    let _s3: u32 = fs_set(rf, a1);
    let _s4: u32 = fs_set(rf, a2);
    let _t3: u32 = put64(tv, 8, 200000);
    let r2: i64 = syscall6(23, (a2 + 1) as u64, rf as u64, 0, 0, tv as u64);
    if r2 != 0 {
        die(16, "FAIL select timeout count\\n");
    }
    if fs_has(rf, a1) != 0 {
        die(17, "FAIL timeout a1 not ready\\n");
    }
    w(1, "SELECT TIMEOUT OK\\n");
    // ---- ③ **连接失败**：非阻塞 connect 到一个没人听的端口 ----
    //    Linux 把它报在 **writefds**，WinSock 报在 **exceptfds** ——
    //    shim 的出参归一化就是为这一格写的（docs/231）。
    let c3: i64 = syscall6(41, 2, 2049, 0, 0, 0);
    if c3 < 0 {
        die(18, "FAIL socket(c3 nonblock)\\n");
    }
    let sa3: ptr = alloc(64);
    let _z3: u32 = sa(sa3, 0, 1);
    let n3: i64 = syscall6(42, c3 as u64, sa3 as u64, 16, 0, 0);
    if n3 == 0 {
        die(19, "FAIL connect to a closed port should not succeed\\n");
    }
    let _q3: u32 = fs_clr(wf);
    let _s5: u32 = fs_set(wf, c3);
    // 超时给 **3 秒**：Windows 把"本地端口被拒"这件事升上来要 ~2.8 s（实测，见 docs/230），
    // Linux 是瞬间。300 ms 在两个平台上都是"没等到"，但那说明不了归一化对错。
    let _t4: u32 = put64(tv, 8, 3000000);
    let r3: i64 = syscall6(23, (c3 + 1) as u64, 0, wf as u64, 0, tv as u64);
    if r3 != 1 {
        die(20, "FAIL select(connfail) count\\n");
    }
    if fs_has(wf, c3) != 1 {
        die(21, "FAIL connfail not in writefds\\n");
    }
    let errb: ptr = alloc(8);
    let errl: ptr = alloc(8);
    store8(errl, 0, 4);
    let eg: i64 = syscall6(55, c3 as u64, 1, 4, errb as u64, errl as u64);
    if eg < 0 {
        die(22, "FAIL getsockopt(SO_ERROR)\\n");
    }
    if load8(errb, 0) == 0 {
        die(23, "FAIL SO_ERROR should be non-zero\\n");
    }
    w(1, "SELECT CONNFAIL OK\\n");
    // ---- ④ 三个集合全空 = 睡 timeout（Linux 的 `select(0,NULL,NULL,NULL,&tv)`）----
    let _t5: u32 = put64(tv, 8, 100000);
    let r4: i64 = syscall6(23, 0, 0, 0, 0, tv as u64);
    if r4 != 0 {
        die(24, "FAIL select sleep\\n");
    }
    w(1, "SELECT SLEEP OK\\n");
    syscall6(3, a1 as u64, 0, 0, 0, 0);
    syscall6(3, a2 as u64, 0, 0, 0, 0);
    syscall6(3, c1 as u64, 0, 0, 0, 0);
    syscall6(3, c2 as u64, 0, 0, 0, 0);
    syscall6(3, c3 as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
SEL_GOLDEN = b"SELECT READY OK\nSELECT TIMEOUT OK\nSELECT CONNFAIL OK\nSELECT SLEEP OK\n"


def _sel_ok(out: bytes) -> bool:
    return out == SEL_GOLDEN

POLL_GOLDEN = b"POLL READY OK\nPOLL TIMEOUT OK\n"


def _poll_ok(out: bytes) -> bool:
    return out == POLL_GOLDEN


SOCK_HEAD = b"ECHO OK\nTIMEOUT OK\nNONBLOCK OK\n"
SOCK_TAIL = (b"IPV6 OK\nSURVIVED\n", b"IPV6 NO\nSURVIVED\n")


#: **兜底那条路的语料**（2026-10-11 收口）：一个 Linux 自己也不认得的号。
#: 两边的期望值是**同一个数** —— 这就把"表外的号返回什么"从散文变成判据。
#: 挑 `9999`/`9998` 是因为它们**在 Linux 上也没实现**，所以不存在"一边能做一边不能"
#: 那种不可比的形态：真的判据是"同一个语义两边都成立"。
ENOSYS_DEMO = """module enosysdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn _start() -> u32 {
    let want: i64 = 0 - 38;
    let a: i64 = syscall4(9999, 0, 0, 0);
    let b: i64 = syscall6(9998, 0, 0, 0, 0, 0);
    let _c: i64 = syscall7(9997, 0, 0, 0, 0, 0, 0);
    if a != want {
        w(1, "ENOSYS BAD a\\n");
        return 1;
    }
    if b != want {
        w(1, "ENOSYS BAD b\\n");
        return 2;
    }
    w(1, "ENOSYS OK\\n");
    return 0;
}
"""
ENOSYS_GOLDEN = b"ENOSYS OK\n"


def _enosys_ok(out: bytes) -> bool:
    return out == ENOSYS_GOLDEN


def _sock_ok(out: bytes) -> bool:
    """IPv6 那一行**允许两种情况**（机器没有 IPv6 时两边都打 NO）——
    判据是"两个平台看到同一件事"，不是"必须有 IPv6"。"""
    return out.startswith(SOCK_HEAD) and out[len(SOCK_HEAD):] in SOCK_TAIL


#: 四份联网语料：`(文件名, 源码, 输出怎么算对)`。两条动态判据都跑这一份清单 ——
#: 加一份语料就两边一起覆盖，别只在一边加。
MSG_DEMO = """module msgdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn put64(p: ptr, off: u32, v: i64) -> u32 {
    let k: u32 = 0;
    let x: i64 = v;
    while k < 8 {
        store8(p, off + k, (x % 256) as u8);
        x = x / 256;
        k = k + 1;
    }
    return 0;
}

// Linux 的 struct msghdr（x86-64）：name@0 namelen@8 iov@16 iovlen@24
//                                  control@32 controllen@40 flags@48
fn mkhdr(m: ptr, name: ptr, namelen: i64, iov: ptr, iovlen: i64) -> u32 {
    let _a: u32 = put64(m, 0, name as i64);
    let _b: u32 = put64(m, 8, namelen);
    let _c: u32 = put64(m, 16, iov as i64);
    let _d: u32 = put64(m, 24, iovlen);
    let _e: u32 = put64(m, 32, 0);
    let _f: u32 = put64(m, 40, 0);
    let _g: u32 = put64(m, 48, 0);
    return 0;
}

// struct iovec 是 {base, len}，每条 16 字节
fn mkiov(v: ptr, k: u32, base: ptr, len: i64) -> u32 {
    let _a: u32 = put64(v, k * 16, base as i64);
    let _b: u32 = put64(v, k * 16 + 8, len);
    return 0;
}

fn _start() {
    // ---- TCP：`sendmsg` 把三段散出去，`recvmsg` 再按三条 iovec 收回来 ----
    let sa0: ptr = alloc(64);
    let _z0: u32 = sa(sa0, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket\\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let bi: i64 = syscall6(49, ls as u64, sa0 as u64, 16, 0, 0);
    if bi < 0 {
        die(2, "FAIL bind\\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(3, "FAIL listen\\n");
    }
    let g1: i64 = syscall6(51, ls as u64, sa0 as u64, ln as u64, 0, 0);
    if g1 < 0 {
        die(4, "FAIL getsockname\\n");
    }
    let c1: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if c1 < 0 {
        die(5, "FAIL socket(c)\\n");
    }
    let n1: i64 = syscall6(42, c1 as u64, sa0 as u64, 16, 0, 0);
    if n1 < 0 {
        die(6, "FAIL connect\\n");
    }
    let a1: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a1 < 0 {
        die(7, "FAIL accept\\n");
    }
    let b0: ptr = alloc(8);
    store8(b0, 0, 65);
    store8(b0, 1, 66);
    let b1: ptr = alloc(8);
    store8(b1, 0, 67);
    store8(b1, 1, 68);
    store8(b1, 2, 69);
    let b2: ptr = alloc(8);
    store8(b2, 0, 70);
    let iov: ptr = alloc(64);
    let _i0: u32 = mkiov(iov, 0, b0, 2);
    let _i1: u32 = mkiov(iov, 1, b1, 3);
    let _i2: u32 = mkiov(iov, 2, b2, 1);
    let m: ptr = alloc(64);
    let nil: ptr = 0 as ptr;
    let _h0: u32 = mkhdr(m, nil, 0, iov, 3);
    let sn: i64 = syscall6(46, c1 as u64, m as u64, 0, 0, 0);
    if sn != 6 {
        die(8, "FAIL sendmsg count\\n");
    }
    w(1, "SENDMSG OK\\n");
    let r0: ptr = alloc(8);
    let r1: ptr = alloc(8);
    let r2: ptr = alloc(8);
    let riov: ptr = alloc(64);
    let _j0: u32 = mkiov(riov, 0, r0, 2);
    let _j1: u32 = mkiov(riov, 1, r1, 2);
    let _j2: u32 = mkiov(riov, 2, r2, 2);
    let rm: ptr = alloc(64);
    let _h1: u32 = mkhdr(rm, nil, 0, riov, 3);
    let rn: i64 = syscall6(47, a1 as u64, rm as u64, 0, 0, 0);
    if rn != 6 {
        die(9, "FAIL recvmsg count\\n");
    }
    if load8(r0, 0) != 65 {
        die(10, "FAIL r0a\\n");
    }
    if load8(r0, 1) != 66 {
        die(11, "FAIL r0b\\n");
    }
    if load8(r1, 0) != 67 {
        die(12, "FAIL r1a\\n");
    }
    if load8(r1, 1) != 68 {
        die(13, "FAIL r1b\\n");
    }
    if load8(r2, 0) != 69 {
        die(14, "FAIL r2a\\n");
    }
    if load8(r2, 1) != 70 {
        die(15, "FAIL r2b\\n");
    }
    w(1, "RECVMSG SCATTER OK\\n");
    // ---- UDP：`sendmsg` 带 msg_name（没 connect 过也能发），`recvmsg` 收地址 ----
    let ua: ptr = alloc(64);
    let ub: ptr = alloc(64);
    let _z1: u32 = sa(ua, 0, 0);
    let _z2: u32 = sa(ub, 0, 0);
    let us: i64 = syscall6(41, 2, 2, 0, 0, 0);
    let ur: i64 = syscall6(41, 2, 2, 0, 0, 0);
    if us < 0 {
        die(16, "FAIL udp socket(1)\\n");
    }
    if ur < 0 {
        die(17, "FAIL udp socket(2)\\n");
    }
    let ul: ptr = alloc(8);
    store8(ul, 0, 16);
    let bu1: i64 = syscall6(49, us as u64, ua as u64, 16, 0, 0);
    if bu1 < 0 {
        die(18, "FAIL udp bind(1)\\n");
    }
    let bu2: i64 = syscall6(49, ur as u64, ub as u64, 16, 0, 0);
    if bu2 < 0 {
        die(19, "FAIL udp bind(2)\\n");
    }
    let gu: i64 = syscall6(51, us as u64, ua as u64, ul as u64, 0, 0);
    if gu < 0 {
        die(20, "FAIL udp getsockname\\n");
    }
    let h0: ptr = alloc(8);
    store8(h0, 0, 104);
    store8(h0, 1, 101);
    let h1: ptr = alloc(8);
    store8(h1, 0, 121);
    let uiov: ptr = alloc(64);
    let _u0: u32 = mkiov(uiov, 0, h0, 2);
    let _u1: u32 = mkiov(uiov, 1, h1, 1);
    let um: ptr = alloc(64);
    let _h2: u32 = mkhdr(um, ua, 16, uiov, 2);
    let un: i64 = syscall6(46, ur as u64, um as u64, 0, 0, 0);
    if un != 3 {
        die(21, "FAIL udp sendmsg count\\n");
    }
    w(1, "UDP SENDMSG OK\\n");
    let pl: ptr = alloc(64);
    let giov: ptr = alloc(64);
    let _g0: u32 = mkiov(giov, 0, pl, 16);
    let src: ptr = alloc(64);
    let gm: ptr = alloc(64);
    let _h3: u32 = mkhdr(gm, src, 128, giov, 1);
    // 收在 `us` 上：数据报是 `ur` 发往 `ua` 的，而 `ua` 是 `us` 的名字
    let gn: i64 = syscall6(47, us as u64, gm as u64, 0, 0, 0);
    if gn != 3 {
        die(22, "FAIL udp recvmsg count\\n");
    }
    if load8(pl, 0) != 104 {
        die(23, "FAIL udp payload a\\n");
    }
    if load8(pl, 1) != 101 {
        die(24, "FAIL udp payload b\\n");
    }
    if load8(pl, 2) != 121 {
        die(25, "FAIL udp payload c\\n");
    }
    // 来源地址被写进 msg_name，且 msg_namelen 被回写成真长度
    let nl: i64 = load8(src, 1) as i64;
    if nl != 0 {
        die(26, "FAIL udp src family\\n");
    }
    if load8(src, 3) == 0 {
        die(27, "FAIL udp src port\\n");
    }
    if load8(gm, 8) == 0 {
        die(28, "FAIL udp msg_namelen\\n");
    }
    w(1, "UDP RECVMSG OK\\n");
    syscall6(3, a1 as u64, 0, 0, 0, 0);
    syscall6(3, c1 as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall6(3, us as u64, 0, 0, 0, 0);
    syscall6(3, ur as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
MSG_GOLDEN = (b"SENDMSG OK\nRECVMSG SCATTER OK\nUDP SENDMSG OK\nUDP RECVMSG OK\n")


def _msg_ok(out: bytes) -> bool:
    return out == MSG_GOLDEN


SP_DEMO = """module spdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

// 小整数（含负数）打到 stdout —— 错误码要**看得见**，否则两边不一致也只会"都过了"
fn num(tag: str, v: i64) -> u32 {
    w(1, tag);
    let x: i64 = v;
    if x < 0 {
        w(1, "-");
        x = 0 - x;
    }
    let buf: ptr = alloc(32);
    let n: u32 = 0;
    if x == 0 {
        store8(buf, 0, 48);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + (x % 10)) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = n;
    while i > 0 {
        i = i - 1;
        syscall4(1, 1, buf as u64 + i as u64, 1);
    }
    w(1, "\n");
    return 0;
}

fn _start() {
    // ---- STREAM：一对流式 socket，来回各一次 ----
    let sv: ptr = alloc(16);
    let r1: i64 = syscall6(53, 1, 1, 0, sv as u64, 0);
    if r1 < 0 {
        die(1, "FAIL socketpair(stream)\n");
    }
    let f0: i64 = load8(sv, 0) as i64;
    let f1: i64 = load8(sv, 4) as i64;
    let p1: i64 = syscall6(1, f0 as u64, str_ptr("ping") as u64, 4, 0, 0);
    if p1 != 4 {
        die(2, "FAIL write a\n");
    }
    let rb: ptr = alloc(16);
    let q1: i64 = syscall6(0, f1 as u64, rb as u64, 4, 0, 0);
    if q1 != 4 {
        die(3, "FAIL read a\n");
    }
    if load8(rb, 0) != 112 {
        die(4, "FAIL payload a\n");
    }
    let p2: i64 = syscall6(1, f1 as u64, str_ptr("pong") as u64, 4, 0, 0);
    if p2 != 4 {
        die(5, "FAIL write b\n");
    }
    let q2: i64 = syscall6(0, f0 as u64, rb as u64, 4, 0, 0);
    if q2 != 4 {
        die(6, "FAIL read b\n");
    }
    if load8(rb, 3) != 103 {
        die(7, "FAIL payload b\n");
    }
    w(1, "SP STREAM OK\n");
    // ---- DGRAM：一对数据报 socket ----
    let sv2: ptr = alloc(16);
    let r2: i64 = syscall6(53, 1, 2, 0, sv2 as u64, 0);
    if r2 < 0 {
        die(8, "FAIL socketpair(dgram)\n");
    }
    let g0: i64 = load8(sv2, 0) as i64;
    let g1: i64 = load8(sv2, 4) as i64;
    let d1: i64 = syscall6(1, g0 as u64, str_ptr("dg") as u64, 2, 0, 0);
    if d1 != 2 {
        die(9, "FAIL dgram write\n");
    }
    let d2: i64 = syscall6(0, g1 as u64, rb as u64, 8, 0, 0);
    if d2 != 2 {
        die(10, "FAIL dgram read\n");
    }
    if load8(rb, 0) != 100 {
        die(11, "FAIL dgram payload\n");
    }
    w(1, "SP DGRAM OK\n");
    // ---- 错误路：错误码要与 Linux **逐字相同**（不是"都是负数"就算过）----
    let e1: i64 = syscall6(53, 2, 1, 0, sv as u64, 0);
    let _n1: u32 = num("sp(af_inet) = ", e1);
    let e2: i64 = syscall6(53, 1, 99, 0, sv as u64, 0);
    let _n2: u32 = num("sp(type99)  = ", e2);
    syscall6(3, f0 as u64, 0, 0, 0, 0);
    syscall6(3, f1 as u64, 0, 0, 0, 0);
    syscall6(3, g0 as u64, 0, 0, 0, 0);
    syscall6(3, g1 as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
SP_HEAD = b"SP STREAM OK\nSP DGRAM OK\n"


def _sp_ok(out: bytes) -> bool:
    """两行正常路必须过；后两行是**错误码**，值本身钉在这里（Linux 实测）。"""
    if not out.startswith(SP_HEAD):
        return False
    return out[len(SP_HEAD):] == b"sp(af_inet) = -95\nsp(type99)  = -22\n"


EP_DEMO = """module epdemo
choose runtime

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn num(tag: str, v: i64) -> u32 {
    w(1, tag);
    let x: i64 = v;
    if x < 0 {
        w(1, "-");
        x = 0 - x;
    }
    let buf: ptr = alloc(32);
    let n: u32 = 0;
    if x == 0 {
        store8(buf, 0, 48);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + (x % 10)) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = n;
    while i > 0 {
        i = i - 1;
        syscall4(1, 1, buf as u64 + i as u64, 1);
    }
    w(1, "\n");
    return 0;
}

fn sa(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn put32(p: ptr, off: u32, v: i64) -> u32 {
    let k: u32 = 0;
    let x: i64 = v;
    while k < 4 {
        store8(p, off + k, (x % 256) as u8);
        x = x / 256;
        k = k + 1;
    }
    return 0;
}

fn put64(p: ptr, off: u32, v: i64) -> u32 {
    let k: u32 = 0;
    let x: i64 = v;
    while k < 8 {
        store8(p, off + k, (x % 256) as u8);
        x = x / 256;
        k = k + 1;
    }
    return 0;
}

// struct epoll_event 在 x86-64 Linux 上是 **packed**：events(u32)@0、data(u64)@4（共 12 字节）
fn mkev(ev: ptr, events: i64, data: i64) -> u32 {
    let _a: u32 = put32(ev, 0, events);
    let _b: u32 = put64(ev, 4, data);
    return 0;
}

fn ev_of(o: ptr) -> i64 {
    return load8(o, 0) as i64;
}

fn data_of(o: ptr) -> i64 {
    return load8(o, 4) as i64;
}

fn _start() {
    // 一对连着的 TCP socket：c1 ↔ a1
    let sa0: ptr = alloc(64);
    let _z: u32 = sa(sa0, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket(ls)\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let bi: i64 = syscall6(49, ls as u64, sa0 as u64, 16, 0, 0);
    if bi < 0 {
        die(2, "FAIL bind\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(3, "FAIL listen\n");
    }
    let g1: i64 = syscall6(51, ls as u64, sa0 as u64, ln as u64, 0, 0);
    if g1 < 0 {
        die(4, "FAIL getsockname\n");
    }
    let c1: i64 = syscall6(41, 2, 1, 0, 0, 0);
    let n1: i64 = syscall6(42, c1 as u64, sa0 as u64, 16, 0, 0);
    if n1 < 0 {
        die(5, "FAIL connect\n");
    }
    let a1: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if a1 < 0 {
        die(6, "FAIL accept\n");
    }
    // ---- epoll 实例 ----
    let ep: i64 = syscall6(291, 0, 0, 0, 0, 0);
    if ep < 0 {
        die(7, "FAIL epoll_create1\n");
    }
    let _n0: u32 = num("epoll_create1 = ", ep);
    let ev: ptr = alloc(16);
    let out: ptr = alloc(128);
    let _e1: u32 = mkev(ev, 1, 42);                   // EPOLLIN，data=42
    let r1: i64 = syscall6(233, ep as u64, 1, c1 as u64, ev as u64, 0);
    if r1 != 0 {
        die(8, "FAIL ctl add c1\n");
    }
    let _e2: u32 = mkev(ev, 1, 7);                    // EPOLLIN，data=7
    let r2: i64 = syscall6(233, ep as u64, 1, a1 as u64, ev as u64, 0);
    if r2 != 0 {
        die(9, "FAIL ctl add a1\n");
    }
    // ① 谁都还没有数据 ⇒ 等到超时，返回 0
    let w1: i64 = syscall6(232, ep as u64, out as u64, 8, 200, 0);
    let _n1: u32 = num("wait(empty) = ", w1);
    // ② 往 c1 写两个字节 ⇒ a1 可读
    let _p: i64 = syscall6(1, c1 as u64, str_ptr("hi") as u64, 2, 0, 0);
    let w2: i64 = syscall6(232, ep as u64, out as u64, 8, 1000, 0);
    let _n2: u32 = num("wait(read) n = ", w2);
    let _n3: u32 = num("wait(read) ev = ", ev_of(out));
    let _n4: u32 = num("wait(read) data = ", data_of(out));
    let rb: ptr = alloc(16);
    let _r: i64 = syscall6(0, a1 as u64, rb as u64, 2, 0, 0);
    // ③ c1 改成等 EPOLLOUT（连着的就是可写的）
    let _e3: u32 = mkev(ev, 4, 9);
    let r3: i64 = syscall6(233, ep as u64, 3, c1 as u64, ev as u64, 0);
    if r3 != 0 {
        die(10, "FAIL ctl mod\n");
    }
    let w3: i64 = syscall6(232, ep as u64, out as u64, 8, 500, 0);
    let _n5: u32 = num("wait(write) n = ", w3);
    let _n6: u32 = num("wait(write) ev = ", ev_of(out));
    let _n7: u32 = num("wait(write) data = ", data_of(out));
    // ---- 错误路：码要与 Linux 逐字相同 ----
    let x1: i64 = syscall6(233, ep as u64, 1, c1 as u64, ev as u64, 0);   // ADD 已登记
    let _m1: u32 = num("ctl(add dup) = ", x1);
    let x2: i64 = syscall6(233, ep as u64, 3, ls as u64, ev as u64, 0);   // MOD 没登记
    let _m2: u32 = num("ctl(mod none) = ", x2);
    // ⚠ 这里**不能**拿 fd 1 当"不是 socket 的 fd"：Linux 那边"能不能进 epoll"取决于它
    // 到底是什么 —— **管道可以**（CI runner 上 stdout 就是管道，这条当场给出 0）、
    // 常规文件与字符设备**不行**（EPERM）。那是**环境相关**的，逐字节判据不能钉它。
    // 改用"op 不认识"：两边都稳定地给 EINVAL。
    let x3: i64 = syscall6(233, ep as u64, 99, c1 as u64, ev as u64, 0);  // op 不认识
    let _m3: u32 = num("ctl(bad op) = ", x3);
    let x4: i64 = syscall6(291, 3, 0, 0, 0, 0);                           // flags 不认识
    let _m4: u32 = num("create1(bad) = ", x4);
    // ---- 关掉实例之后还能再建一个（实例要回收，不然四次就 EMFILE）----
    let _c: i64 = syscall6(3, ep as u64, 0, 0, 0, 0);
    let ep2: i64 = syscall6(291, 0, 0, 0, 0, 0);
    if ep2 < 0 {
        die(11, "FAIL epoll_create1 reuse\n");
    }
    w(1, "EPOLL REUSE OK\n");
    syscall6(3, c1 as u64, 0, 0, 0, 0);
    syscall6(3, a1 as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall6(3, ep2 as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""
EP_GOLDEN = (b"epoll_create1 = @G@\nwait(empty) = 0\nwait(read) n = 1\nwait(read) ev = 1\n"
             b"wait(read) data = 7\nwait(write) n = 1\nwait(write) ev = 4\n"
             b"wait(write) data = 9\nctl(add dup) = -17\nctl(mod none) = -2\n"
             b"ctl(bad op) = -22\ncreate1(bad) = -22\nEPOLL REUSE OK\n")


def _ep_ok(out: bytes) -> bool:
    """实例号（fd）两边不保证同值，所以只钉"它是个正整数"，其余逐字节比。"""
    for i in range(3, 64):
        if out == EP_GOLDEN.replace(b"@G@", str(i).encode(), 1):
            return True
    return False


#: 联网语料：`(文件名, 源码, 输出怎么算对)`。两条动态判据都跑这一份清单 ——
#: 加一份语料就两边一起覆盖，别只在一边加。
NET_CASES = (("sockdemo", SOCK_DEMO, _sock_ok), ("udpdemo", UDP_DEMO, _udp_ok),
             ("polldemo", POLL_DEMO, _poll_ok), ("seldemo", SEL_DEMO, _sel_ok),
             ("msgdemo", MSG_DEMO, _msg_ok), ("spdemo", SP_DEMO, _sp_ok),
             ("epdemo", EP_DEMO, _ep_ok),
             ("enosysdemo", ENOSYS_DEMO, _enosys_ok))


def _shim_blob():
    """现算一遍 shim 机器码 + 导入表（`--dump-win-shim` 冻的就是这两段）。"""
    a = lomelf.Asm(0)
    em = lomelf.PeEmitter([])
    em.asm = a
    idata, slots = lomelf.build_pe_idata()
    lomelf.emit_win_shim(em, slots)
    return a.finalize(), idata, slots, a.labels


@test
def test_pe_shim_dispatches_the_documented_numbers():
    """派发面 == `PE_DISPATCH` 那张登记表：注释、文档、机器码三者不能各自漂。

    跨平台（纯数据），所以**门禁的 ubuntu runner 也跑得到** —— 它盯的是
    "文档说支持哪些号"这件事本身，而不是那些号在 Windows 上跑得对不对。
    """
    blob, _idata, _slots, labels = _shim_blob()
    i = labels["__win_syscall"]
    while not (blob[i] == 0x3D and blob[i + 5] == 0x0F and 0x80 <= blob[i + 6] <= 0x8F):
        i += 1                                   # 跳过序言（push rbp / mov rbx,…）
    seen = []
    while blob[i] == 0x3D and blob[i + 5] == 0x0F and 0x80 <= blob[i + 6] <= 0x8F:
        seen.append(struct.unpack_from("<i", blob, i + 1)[0])
        i += 11                                  # cmp eax,imm32 (5) + jcc rel32 (6)
    assert seen == lomelf.PE_DISPATCH, f"派发面与登记表不符:\n  {seen}\n  {lomelf.PE_DISPATCH}"
    # 兜底**照常量比**，不抄字节：`mov rax, imm32`（符号扩展）的前缀 + `PE_ENOSYS`。
    want = b"\x48\xC7\xC0" + struct.pack("<i", lomelf.PE_ENOSYS)
    assert blob[i:i + 7] == want, f"兜底不是 `mov rax,{lomelf.PE_ENOSYS}`: {blob[i:i+7]!r}"
    print(f"      {len(seen)} 个号逐个对上 `PE_DISPATCH`，兜底 {lomelf.PE_ENOSYS}（-ENOSYS）")


@test
def test_pe_dispatch_table_is_the_single_source():
    """派发面由**一张表**生成：表决定字节，**双向**可证。

    这是"万物可改"落到运行时联网面上的那一格（`docs/218` §12）：加号、改面名、划自己的面，
    改的是**数据**（`lomelf.PE_SYSCALLS`），不是手写的机器码发射序列；而"改了什么"由
    **产物字节的哈希**说话 —— 冻出来的 `win_shim_data.lomt` 被发布清单
    （`loment/build/SHA256SUMS`）钉着，于是"两端跑的是不是同一张表"不靠版本号、靠字节。

    判据要**两边都证**：同一张表 ⇒ 逐字节相同（表真的决定字节）；改一个号 ⇒ 字节不同
    且更短（表真的在被读，不是摆设）。
    """
    nums = [n for n, _s, _l in lomelf.PE_SYSCALLS]
    assert len(set(nums)) == len(nums), f"派发表里号重复: {sorted(nums)}"
    assert nums == lomelf.PE_DISPATCH, "`PE_DISPATCH` 必须从表派生，不能是手写的第二份"
    for _n, surface, label in lomelf.PE_SYSCALLS:
        assert surface and " " not in surface, f"面名不合形: {surface!r}"
        assert label.startswith("__ws_"), f"处理块名不合形: {label}"
    blob, _idata, _slots, labels = _shim_blob()
    for _n, _s, label in lomelf.PE_SYSCALLS:
        assert label in labels, f"派发表指向一个不存在的处理块: {label}"
    assert _shim_blob()[0] == blob, "同一张表生成两次应当逐字节相同"
    counts = lomelf.pe_surface_sites()
    assert sum(counts.values()) == len(lomelf.PE_SYSCALLS), "面计数之和应当等于表长"
    orig = lomelf.PE_SYSCALLS
    try:
        lomelf.PE_SYSCALLS = tuple(t for t in orig if t[0] != lomelf.SYS_SOCKET)
        shrunk = _shim_blob()[0]
    finally:
        lomelf.PE_SYSCALLS = orig
    assert shrunk != blob, "把 socket 从表里拿掉，产物字节没变 —— 表没在决定字节"
    assert len(shrunk) < len(blob), "拿掉一条应当让派发面变短"
    print(f"      {len(nums)} 条表 ⇒ {len(blob)} B，面计数 {counts}；"
          f"拿掉 socket ⇒ {len(shrunk)} B（表确实在决定字节）")


@test
def test_pe_surface_table_matches_the_artifact_counter():
    """运行时的**面表**与产物里那张**号 → 面**表必须是同一张（`docs/222`）。

    声明侧（`lomentc.SURFACE_NUMBERS`）决定 `.potato.json` 的 `surfaces` 怎么分，运行时侧
    （`lomelf.PE_SURFACE_BY_NUMBER`）决定 PE 上哪个号真的被派发、算哪个面。两份各自漂会
    产出最难看的那一格：产物说"这个单元有 3 个 `net` 站点"，而 Windows 上那个号拿到 -1 ——
    **数得出来、跑不起来**。那正是 `docs/217` 要堵的缺口，所以这里两个方向都报。

    这张表**不是**"语言规定的"：`lomelf.PE_SYSCALLS` 是用户可以改的数据（`docs/218` §12）。
    改它没问题 —— 改完这一条就红，于是"面名换了 / 号挪了"必须**两边一起显式改**，不能悄悄漂。
    """
    decl = lomentc.SURFACE_NUMBERS
    run = lomelf.PE_SURFACE_BY_NUMBER
    miss = {n: run[n] for n in sorted(run) if n not in decl}
    extra = {n: decl[n] for n in sorted(decl) if n not in run}
    diff = {n: (run[n], decl[n]) for n in sorted(run) if n in decl and run[n] != decl[n]}
    assert not miss, f"运行时派发了、产物计数却不认识这些号（会被算进 other）: {miss}"
    assert not extra, f"产物计数认得、PE 运行时不派发这些号（数得出来、跑不起来）: {extra}"
    assert not diff, f"同一个号两边算的面不同: {diff}"
    # 面名两边都得是"一个词" —— 空串/带空格会让"各面之和"看着对、其实什么都没分
    for m in (decl, run):
        assert all(isinstance(v, str) and v and " " not in v for v in m.values()), m
    print(f"      {len(decl)} 个号、面名逐条相同（声明侧 == 运行时侧）")


@test
def test_pe_imports_two_dlls_with_the_socket_surface():
    """导入表：两条描述符 + 终止项、两个 DLL 名、整个 ws2_32 面，且塞得进孪生那块落点。"""
    idata, slots = lomelf.build_pe_idata()
    d = lomelf.PE_IDATA_RVA
    got, k = [], 0
    while True:
        ilt, _ts, _fc, name_rva, _iat = struct.unpack_from("<IIIII", idata, k * 20)
        if ilt == 0 and name_rva == 0:
            break                                # 全零终止项
        got.append(idata[name_rva - d:idata.index(b"\x00", name_rva - d)].decode())
        k += 1
    assert got == [n for n, _f in lomelf.PE_DLLS], f"描述符指向的 DLL 不对: {got}"
    for nm in lomelf.PE_IMPORTS_WS2:
        assert nm in slots, f"ws2_32 少了 `{nm}`"
    assert len(slots) == len(lomelf.PE_IMPORTS) + len(lomelf.PE_IMPORTS_WS2), \
        "函数名有重名（`slots` 是按名字索引的平表，重名会静默覆盖）"
    # Import Directory 的 size 必须跟着描述符条数走（原先写死 40 = 一条 + 终止项）
    assert lomelf.PE_IMPORT_DESC_BYTES == 20 * (len(lomelf.PE_DLLS) + 1)
    tiny = "define void @_start() {\nentry:\n  ret void\n}\n"
    blob, _info = lomelf.compile_pe(tiny)
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    opt = pe + 24
    dir_rva, dir_sz = struct.unpack_from("<II", blob, opt + 112 + 8)
    assert (dir_rva, dir_sz) == (lomelf.PE_IDATA_RVA, lomelf.PE_IMPORT_DESC_BYTES), \
        f"可选头里的 Import Directory = {hex(dir_rva)}/{dir_sz}，与描述符条数不符"
    # 孪生侧给导入表留的落点只有 TB_EXT-TB_IDATA 宽，**撑破是静默截断**
    twin = (ROOT / "loment" / "tools" / "lomelf.lomt").read_text(encoding="utf-8")
    tb = int(re.search(r"const TB_IDATA: u32 = (\d+);", twin).group(1))
    ext = int(re.search(r"const TB_EXT: u32 = (\d+);", twin).group(1))
    assert len(idata) <= ext - tb, f"导入表 {len(idata)} B 撑破了孪生落点的 {ext - tb} B"
    print(f"      {len(got)} 个 DLL、{len(slots)} 个导入；导入表 {len(idata)} B ≤ 孪生落点 {ext - tb} B")


@test
def test_frozen_shim_blob_matches_the_reference():
    """树里冻的那份（`win_shim_data.lomt`）== 参考实现现算的那份 —— 孪生照抄的就是它。

    这条盯的是"改了 shim 却忘了重新冻"：没有它，自举链接器会照抄一份**旧的**机器码，
    而参考实现编出的 PE 是新的 —— 两边在 `test_pe_selfhost_mirror_matches_reference`
    之前都不会有人喊。
    """
    src = (ROOT / "loment" / "tools" / "win_shim_data.lomt").read_text(encoding="utf-8")
    blob, idata, _slots, _labels = _shim_blob()

    def decode(block: str, n: int) -> bytes:
        got = {int(a): b for a, b in
               re.findall(r'if i == (\d+) \{ return "([0-9a-f]*)"; \}', block)}
        assert sorted(got) == list(range(n)), f"分片编号不连续: {sorted(got)[:4]}…"
        return bytes.fromhex("".join(got[i] for i in range(n)))

    n_shim = int(re.search(r"pub fn n_shim\(\) -> u32 \{ return (\d+); \}", src).group(1))
    n_idata = int(re.search(r"pub fn n_idata\(\) -> u32 \{ return (\d+); \}", src).group(1))
    sblock = re.search(r"pub fn shim_part.*?(?=pub fn n_idata)", src, re.S).group(0)
    iblock = re.search(r"pub fn idata_part.*", src, re.S).group(0)
    assert decode(sblock, n_shim) == blob, "shim 机器码与冻结的那份不符 —— 跑 `--dump-win-shim`"
    assert decode(iblock, n_idata) == idata, "导入表与冻结的那份不符 —— 跑 `--dump-win-shim`"
    print(f"      冻结的 shim {len(blob)} B / 导入表 {len(idata)} B 与现算的逐字节相同")


@test
def test_elf_net_programs_run_on_linux():
    """Linux 上**真跑**一遍两份联网语料（TCP 与 UDP）—— CI 的 ubuntu runner 跑得到这一条。

    PE 那一侧要靠 Windows 机器（见下一条），所以这条是门禁里**唯一**会真的收发字节的联网判据。
    """
    if not sys.platform.startswith("linux"):
        print("      SKIP: 非 Linux（PE 那半由 test_pe_and_elf_agree_on_net_programs 钉）")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for name, source, ok in NET_CASES:
            src = td / f"{name}.lomt"
            src.write_text(source, encoding="utf-8", newline="\n")
            ll = _ir(src, td)
            elf = td / f"{name}.elf"
            elf.write_bytes(lomelf.compile_ll(ll.read_text(encoding="utf-8"))[0])
            elf.chmod(0o755)
            r = subprocess.run([str(elf)], capture_output=True, timeout=60, shell=False)
            assert r.returncode == 0 and ok(r.stdout),                 f"[{name}] Linux 上没跑对: rc={r.returncode} out={r.stdout!r} err={r.stderr[:200]!r}"
    print("      TCP（回显/超时/非阻塞/IPv6/挂断）与 UDP（带地址的 sendto/recvfrom）都在 Linux 上真跑通过")


@test
def test_pe_and_elf_agree_on_net_programs():
    """同一份 `.lomt`：PE（本机原生）与 ELF（WSL）**输出逐字节相同、退出码相同**。

    这就是"孪生联网"的正题 —— 不是"两个平台各自能跑"，而是**同一份源在两边行为一致**。
    两份语料（TCP 与 UDP）走同一条路，所以"加了语料只钉了一边"这种漏法在这里报不出来。
    """
    if not (_on_windows() and _wsl()):
        print("      SKIP: 非 Windows 或无 WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        outs = []
        for name, source, ok in NET_CASES:
            src = td / f"{name}.lomt"
            src.write_text(source, encoding="utf-8", newline="\n")
            ll = _ir(src, td)
            text = ll.read_text(encoding="utf-8")
            exe = td / f"{name}.exe"
            exe.write_bytes(lomelf.compile_pe(text)[0])
            elf = td / f"{name}.elf"
            elf.write_bytes(lomelf.compile_ll(text)[0])
            got = _run_native(exe, timeout=60)
            want = _run_in_wsl(elf, name, td, timeout=60)
            assert got == want, f"[{name}] 两个平台不一致:\n  PE   {got!r}\n  ELF  {want!r}"
            assert got[0] == 0 and ok(got[1]), f"[{name}] 联网语料没跑对: {got!r}"
            outs.append((name, got[1]))
        shown = " · ".join(f"{n}={o!r}" for n, o in outs)
    print(f"      PE 与 ELF 逐字节相同：{shown}")


def main() -> int:
    return loment_suite.run(TESTS, "loment_pe_test")


if __name__ == "__main__":
    sys.exit(main())
