#!/usr/bin/env python3
# loment_elf_test.py — 原生 ELF 后端: 不吃 clang 也能产出可执行文件 (docs/167)
#
# 判据: 同一份 `.ll`, 两条路各产一个 ELF, **行为逐值一致** ——
#   (a) 参考路: clang --target=x86_64-unknown-linux-gnu -nostdlib -static -fno-pie -fuse-ld=lld
#   (b) 原生路: tools/lomelf.py (纯 Python, 不调 clang)
#   比 stdout 的**字节**与**退出码**。
#
# 语料只放**会终止**的 `_start` 程序。`loment/examples/native_entry.lomt` 是 `while true`
# 的裸机入口 (M32 验证集), 按设计不终止, 不进这个判据 —— 它属于"能不能编出来", 不属于
# "跑出来一样"。
#
# **例子集里每一份都要有去处** (docs/205 R6): `CORPUS` 里的是真跑的, `NOT_RUN` 里的是
# 跑不了的 —— 后者每一条都带一个**当场能核**的理由 (下面 `test_not_run_reasons_hold`
# 会真的去链一次、真的编一次), 不是一句注释。"漏掉一个"因此不是一种可能。
#
# 已知边界 (与 lomelf.py 头注同源, 逐条记账):
#   * 调用约定是我们自己的 (实参走栈), **不是 System V** —— 原生产物 v0 不给 C 调;
#   * 结构体动态下标 GEP / 间接调用 / 浮点 v0 不支持 (会报 [ERR] 而不是静默错编);
#   * 自举侧的镜像是 loment/tools/lomelf.lomt —— 自举那几条用例钉它与参考逐字节相同,
#     其中一条专门钉 FFI (`--link` 外部目标文件, docs/173)。
#
# 运行: python tools/loment_elf_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os
import re
import struct

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc           # noqa: E402
import lomelf            # noqa: E402
import loment_ffi_test as ffitest   # noqa: E402  (共用 FFI 那份 C 夹具与 Loment 源码)

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent

#: 真跑的一批: 有 `_start`、会终止、原生后端支持 —— 两条路各编一个, 比 stdout 与退出码。
CORPUS = [
    "loment/examples/user_hello.lomt",
    # 命令单元（`docs/223`）：`_start` 由**工具链生成**（读 `/proc/self/cmdline`、调
    # `command_main`），所以它们有入口、跑得起来。两种写法各一份 —— clang 与原生链接器
    # 编出来的 stdout 与退出码都比。
    "loment/examples/cmd_hello.lomt",
    "loment/examples/register_hello.lomt",
    "loment/examples/bootprobe.lomt",
    "loment/examples/selfcheck.lomt",
    "loment/examples/all_loment.lomt",
    "loment/examples/tour.lomt",
    "loment/examples/native_match_full.lomt",
    "loment/examples/switch.lomt",
    "loment/examples/lumtui_demo.lomt",
]

#: 跑不了的一批, 键 = 文件名主干, 值 = (类别, 理由)。**类别决定判据怎么核这条理由** ——
#: `test_not_run_reasons_hold` 会对每一类**真做一遍**那件事, 断言它失败得跟你说的一样。
#: 所以这三段文字不是笔记, 是每次门禁都重新成立一次的事实。
_NO_ENTRY = (
    "no-entry",
    "没有 `_start` —— 它给别的判据当**库**用（入口由 C 驱动 / QEMU / 逐字节 IR 对照提供），"
    "不是能自己跑起来的程序",
)
_IR_GAP = (
    "ir-gap",
    "IR 后端缺口: `inb` 还没实现（M20 只在 Rust 那条路上）—— 挡住它的是**后端**, 不是入口",
)
_NONTERM = (
    "nonterm",
    "有 `_start` 但**设计上不终止**（`while true` 的裸机入口, M32 验证集）: 跑它只能等到超时, "
    "而超时不是一条判据",
)

NOT_RUN: dict[str, tuple[str, str]] = {
    # 22 份: 定义函数、由调用方给入口（p7 的 C 驱动 / p9 的交叉编译 / p8 的 IR 对照）。
    **{n: _NO_ENTRY for n in (
        "ahci", "allocator", "bytes", "demo", "fuc_node", "mathutil", "native",
        "native_agg", "native_bits", "native_brk", "native_cap", "native_chain",
        "native_concat", "native_gen", "native_gen_sig", "native_mem", "native_mut",
        "native_res", "native_slice", "native_str", "native_trait", "toolchain")},
    "native_raii": _IR_GAP,
    "native_entry": _NONTERM,
}

#: 自举镜像 (`loment/tools/lomelf.lomt`) **还链不了**的那几份 —— 键是 CORPUS 里的主干名,
#: 值 = (现象, 为什么)。镜像语料 = CORPUS 减去这张表。
#:
#: 这不是"放宽判据": `test_lomelf_selfhost_matches_reference` 会拿这条表**反向核**一遍 ——
#: 表里每一份都必须**仍然链不过**。哪天它链过了, 判据红, 逼你把它挪回语料。
#: 表只许变短。
#:
#: **2026-10-16 起是空的**。原先那两份是同一件事的两张脸, 成因各在 `lomelf.lomt` 的一处:
#:   * `tour` —— `one_global` 用 u32 的 `pow256` 做除法, 元素宽度 ≥ 5 时 `256^b` 回绕成 0,
#:     `v / 0` 当场 SIGILL; 而且它根本不认聚合初值。`@__loment_caps` 正是那个形状,
#:     所以**凡是用能力域的程序都链不出来**（issue #102）。
#:   * `native_match_full` —— `do_switch` 的 case 表跨行时 `q` 停在换行上, `next_label`
#:     找不到 `label` 退回 `(0,0)`, 于是 `q` 被赋成 **0**: 从头重来, 每绕一圈发一条回填,
#:     32768 圈后报"回填表满了"。**空 case 表也复现**, 所以那不是容量问题, 是死循环
#:     （issue #2 / #10 —— 原先把这条记成"属容量, 不属缺陷", 是错的）。
#: 两个形状各有一条点名判据: `test_mirror_links_the_shapes_that_used_to_take_it_down`。
MIRROR_GAP: dict[str, tuple[str, str]] = {}

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _wsl() -> bool:
    """能不能跑 Linux ELF：**Linux 上原生跑**，Windows 上经 WSL（`#371`）。

    `CONTRIBUTING.md` 门禁那节写的是 "on Linux they run natively"，而这里原先只问
    `shutil.which("wsl")` —— Linux 上没有那个二进制（它不是 apt 包，CI 镜像也没装垫片），
    于是这一族判据在 runner 上**一条都不跑**，而门禁照样报满。
    """
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _sh(script: str, timeout: float, text: bool = False):
    """把一段 POSIX sh 交给**能跑它的那个 shell**：Linux 直接 `bash -lc`，Windows 经 WSL。

    原先调用点都写死 `["wsl", "-e", "bash", "-lc", …]` —— 只把 `_wsl()` 放宽到 Linux
    是不够的：路径对了，shell 却找不到（`#371`）。`text` 跟着调用点走：比对字符串的那几处
    要 `True`，`write_bytes(...)` 与 `.decode()` 的那几处要 `False`。
    """
    argv = (["bash", "-lc", script] if sys.platform.startswith("linux")
            else ["wsl", "-e", "bash", "-lc", script])
    return subprocess.run(argv, capture_output=True, timeout=timeout, shell=False,
                          text=text)


def _wsl_path(p: Path) -> str:
    """`_sh` 那一侧看得懂的路径：Linux 上就是本机路径，Windows 上是 WSL 的 `/mnt/<盘>/…`。"""
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _ref_ir(src: Path, td: Path) -> Path:
    """参考实现发射 IR (与 clang 路、原生路喂同一份字节)。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def _link_clang(clang: str, ll: Path, out: Path) -> None:
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(ll), "-o", str(out)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"clang 链接失败: {r.stderr[-300:]}"


def _run_bin(elf: Path, name: str, td: Path, timeout: int = 20) -> tuple[int, bytes]:
    """在 WSL 里跑 ELF, 返回 (退出码, stdout 字节)。

    stdout 落文件、退出码单独 echo —— 不能把两者混在一个流里再切尾巴 (那会把程序自己的
    输出当成退出码, 判据会假绿)。
    """
    binn = f"{_T}lomelf_{name}.bin"
    outp = td / f"{name}.out"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"timeout {timeout} {binn} > {_wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = _sh(script, timeout + 120)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


# ---------------------------------------------------------------- 自举镜像 (只建一次)

_MIRROR: Path | None = None
_MIRROR_TD: tempfile.TemporaryDirectory | None = None


def _mirror() -> Path:
    """`loment/tools/lomelf.lomt` 的产物 —— **能替用户干活的那份**链接器。

    种子 ->(clang 一次)-> stage1 ->(自举, 无 Python 无 clang)-> 镜像。clang 只在第一步
    出现, 那一步的终点是 docs/167 §5 记的 genesis。

    整条链**只跑一次**并在本进程内缓存: 三段自举编译每次一分多钟, 三条判据各建一遍是纯浪费。
    """
    global _MIRROR, _MIRROR_TD
    if _MIRROR is not None:
        return _MIRROR
    clang = _clang()
    assert clang, "缺 clang"
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    _MIRROR_TD = tempfile.TemporaryDirectory()      # 活到进程结束 (缓存产物在它里面)
    td = Path(_MIRROR_TD.name)
    s1 = td / "stage1"
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-300:]
    binn = f"{_T}lomelf_mir_s1.bin"
    script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomelf.lomt")
    rr = _sh(script, 900)
    assert rr.returncode == 0, f"stage1 编译镜像失败: {rr.stderr[-300:]}"
    mir_ll = td / "lomelf.ll"
    mir_ll.write_bytes(rr.stdout)
    assert len(rr.stdout) > 100000, f"镜像 IR 太小 ({len(rr.stdout)}B)"
    mir = td / "lomelf.bin"
    r2 = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(mir_ll), "-o", str(mir)],
        capture_output=True, text=True, shell=False)
    assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]}"
    _MIRROR = mir
    return mir


@test
def test_free_really_reclaims():
    """`free` **真的归还**（`docs/175` §3.4 的 `gc_manual`）—— 判据是**一对**程序。

    **Why**：在这一版之前 `free` 是**占位** —— `alloc` 是纯 bump（只涨不落），写 `free`
    编得过、检查得过，跑起来**什么都不发生**（`SKILL.md` 的内建表自己就写着"占位"）。
    于是"**总量超过 arena 但峰值很小**"的程序会耗尽 —— 而语言**早就允诺**了 `free`。
    这正是本仓反复认过的那种形状：**允诺却不兑现**（`docs/188` 的"宁拒勿猜"、
    `docs/175` §3.4 点名的就是它）。

    **How to apply**：两只程序**逐字同源**，只差 `free(p);` 那一行 ——
      * 带 `free`   → 跑完，退出码 = `2000 × 7 % 256 = 176`；
      * 不带 `free` → **必须崩**（arena 耗尽 → abort）。这一半是**证伪**：
        它保证上面那一半不是"反正都跑得通"。少了它，这条判据对"什么都没改"也是绿的。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcfree\nchoose runtime\n\nfn _start() {\n"
            "    let i: u32 = 0;\n"
            "    let sum: u32 = 0;\n"
            "    while i < 2000 {\n"
            "        let p: ptr = alloc(64);\n"
            "        store8(p, 0, 7);\n"
            "        let v: u32 = load8(p, 0);\n"
            "        sum = sum + v;\n"
            "        %s"
            "        i = i + 1;\n"
            "    }\n"
            "    syscall4(60, (sum %% 256) as u64, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, extra in (("free", "free(p);\n        "), ("nofree", "")):
            src = td / f"{tag}.lomt"
            src.write_text(body % extra, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, tag, td, timeout=20)[0])
    # 2000 次 × 64 B ≈ 144 KB **总量**，而**峰值**只有一个块 —— 64 KiB 的 arena 装得下
    # 的只有峰值，所以"跑得通"这件事只能是回收换来的。
    assert got[0] == 176, (
        f"带 `free` 的那只没跑通：退出码 {got[0]}（期望 176 = 2000×7%256）。"
        "要么分配器没归还，要么回收把已分配的内存还回去了 —— 两种都要查")
    assert got[1] != 176, (
        f"**不带 `free` 的那只也跑通了**（退出码 {got[1]}）—— 那说明这条判据测不出回收："
        "arena 没被耗尽，说明它比 64 KiB 大，或者 `free` 那一半根本不是回收在起作用")
    print(f"      带 free 跑通 (rc={got[0]})；不带 free 耗尽 (rc={got[1]}) —— 回收是真的")


@test
def test_gc_l2_epoch_bounds_the_heap():
    """L2 **真的发生**：同一份程序，`gc_manual` 耗尽 / `gc_auto_alpha` 跑通（`docs/210` §5）。

    **Why**：`docs/210` §4.4 说 L2 是"唯一**不扫描**的批量回收"，而它买的是栈纪律 ——
    循环体末尾把前沿退回去，**整段一次性不存在**。这件事只有跑起来才看得见：
    静态判据（`lomentc_test::test_l2_block_epoch_rule`）只能钉"编译器决定了开纪元"。

    **How to apply**：两只程序**逐字同源**，只差 `choose` 那一行 —— 循环体里每次分配
    **动态尺寸**的一块（所以 L0 不接：它只提字面量），累计 40000 × 64 B ≈ 2.56 MB，
    而 arena 只有 64 KiB。
      * 不带（默认 `gc_manual`）→ **必然耗尽**（实测 rc = 132，abort）—— 这一半是**证伪**：
        它保证上面那一半不是"反正都跑得通"；
      * `gc_auto_alpha` → **跑完**（rc = 7 = 循环真的转满了 40000 圈）。

    **峰值只有一个块**，所以"跑得通"这件事只能是回退换来的。这条也是**端到端**的：
    IR 过的是**自举镜像链接器**（`lomelf.compile_ll`），即包内那一只，不是 clang。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcl2\n%s\nfn _start() {\n"
            "    let n: u32 = 64;\n"
            "    let i: u32 = 0;\n"
            "    let s: u32 = 0;\n"
            "    while i < 40000 {\n"
            "        let p: ptr = alloc(n);\n"
            "        store8(p, 0, 1);\n"
            "        s = s + load8(p, 0);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 40000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("alpha", "choose gc_auto_alpha\nchoose runtime"),
                        ("manual", "choose gc_manual\nchoose runtime")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, tag, td, timeout=30)[0])
    assert got[0] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[0]}（期望 7）。"
        "要么前沿没回退（纪元那条 store 没发或发错地方），要么回退把还活着的内存也退了")
    assert got[1] != 7, (
        f"**默认档那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出 L2："
        "arena 没被耗尽，说明它比 64 KiB 大得多，或者那些分配根本没落到 arena 上")
    print(f"      alpha 跑通 (rc={got[0]})；manual 耗尽 (rc={got[1]}) —— 块纪元是真的")


#: 块内精确（`docs/210` §2.9）**动态那一半为什么不在这个文件里**：
#: 掩码今天只有**写**那一半（编在块头那个字里；`free` 只看它的**符号**）。而收集器的判据是
#: **值** —— "这个数看起来像不像地址"；编译器证得出"不是指针"的值本来就不会长得像地址
#: ⇒ **有没有掩码，扫描选中同一批字**。所以"掩码少一位 ⇒ 活指针被当垃圾"这条**今天造不出
#: 可红的判据**：要让那一格可证伪，得先有一个**读**掩码的消费者（搬移/压缩那一类）。
#: 这一格现在靠**静态真值表**（`lomentc_test::test_slot_table_rule`）与**逐字节孪生**
#: （`loment_p8_test::m92`）证伪。
#: **它需要的那个形状，判据已经在了**：`test_gc_alpha_keeps_an_object_behind_a_young_written_edge`
#: —— "目标只经由另一个块里按字节写的引用可达"。那条一度立不住（`__loment_sweep` 清标记清宽了
#: 4 个位图字节 ⇒ 垃圾块后面 256 字节内的活块被顺手收掉，#312），2026-10-14 修好之后才钉得住。


#: `--debug` 那一对（`docs/210` §7 的"**L0 与 L2 都没有在 `debug` 档下核过**"）。
#: 两层的形状各取一个**已经在别处立住**的：L0 那份把唯一一个 `alloc` 提到栈上
#: （产物里连分配器都不剩），L2 那份靠前沿回退跑完 40000 圈。`--debug` 只该**多加元数据**。
_DEBUG_L0_SRC = """module dbgl0
choose gc_auto_alpha
choose runtime

fn _start() {
    let a: ptr = alloc(16);
    store8(a, 0, 5);
    syscall4(60, load8(a, 0) as u64, 0, 0);
}
"""

_DEBUG_L2_SRC = """module dbgl2
choose gc_auto_alpha
choose runtime

fn _start() {
    let n: u32 = 64;
    let i: u32 = 0;
    let s: u32 = 0;
    while i < 40000 {
        let p: ptr = alloc(n);
        store8(p, 0, 1);
        s = s + load8(p, 0);
        i = i + 1;
    }
    if s == 40000 {
        syscall4(60, 7, 0, 0);
    }
    syscall4(60, 3, 0, 0);
}
"""


@test
def test_debug_flag_adds_metadata_and_nothing_else():
    """`--debug` **只加元数据** —— L0 与 L2 在 debug 档下产物**逐字节不变**（`docs/210` §7）。

    **Why**：`docs/210` §7 那句"L0 与 L2 都没有在 `debug` 档下核过"底下压着一个**真 bug**：
    `lomelf` 把 `#dbg_declare` 当成**不支持的指令**直接拒 —— 于是 `--debug` 的 IR
    **链不出来**，而 `docs/167` §7 白纸黑字写的是"元数据**被忽略**、原生产物没有行表"。
    文档与实现对不上，而链接器有**两套**（`tools/lomelf.py` 与 `loment/tools/lomelf.lomt`），
    所以"核过"要**四处**都算上（另一半在下面 `test_debug_ir_links_the_same_through_the_mirror`）。

    **How to apply**：三个断言缺一条就是"顺手报个绿"：
      * `--debug` 的 IR 与不带的那一份**必须不同**（否则这条判据什么也没测到）；
      * `#dbg_declare` 那一行**必须在**（元数据真的发了，不是"两边都没发"）；
      * 两份 IR 过 `lomelf` 之后**字节相同**，且**跑起来退出码一致** —— 静态那一半
        覆盖不了"元数据漏进机器码"这种错法（行表本来就不进 `.text`，但记录**被当成指令**
        时会，见这条判据没有它时的样子）。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for tag, src, want_rc in (("l0", _DEBUG_L0_SRC, 5), ("l2", _DEBUG_L2_SRC, 7)):
            ll = {}
            for dbg in (False, True):
                f = td / f"{tag}{int(dbg)}.lomt"
                f.write_text(src, encoding="utf-8", newline="\n")
                mod = lomentc.load(f)
                deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
                assert not lomentc.check(mod, deps=deps), f"{tag}: 自己检查不过"
                ll[dbg] = lomentc.emit_llvm(mod, ROOT, deps, debug=dbg)
            assert ll[True] != ll[False], f"{tag}: `--debug` 没往 IR 里加任何东西 —— 这条判据在空转"
            assert "#dbg_declare" in ll[True], f"{tag}: `--debug` 的 IR 里没有 `#dbg_declare`"
            assert "#dbg_declare" not in ll[False], f"{tag}: 不带 `--debug` 也发了调试记录"
            nat = {}
            for dbg in (False, True):
                nat[dbg], _ = lomelf.compile_ll(ll[dbg])
            assert nat[True] == nat[False], (
                f"{tag}: `--debug` 改了原生产物（{len(nat[True])}B vs {len(nat[False])}B）—— "
                "元数据没被忽略，漏进机器码了")
            print(f"      {tag}: `--debug` 的 IR 多 {len(ll[True]) - len(ll[False])}B 元数据，"
                  f"产物 {len(nat[True])}B 逐字节同")
            if not (_clang() and _wsl()):
                continue
            got = []
            for dbg in (False, True):
                elf = td / f"{tag}{int(dbg)}.native"
                elf.write_bytes(nat[dbg])
                got.append(_run_bin(elf, f"{tag}{int(dbg)}", td, timeout=30)[0])
            assert got == [want_rc, want_rc], (
                f"{tag}: debug 档下跑起来不一样 ({got}) —— 期望两只都是 {want_rc}"
                "（L0 那份是「提干净了、分配器都不剩」，L2 那份是「前沿真回退了」）")
            print(f"      {tag}: 两个档位都跑出 rc={want_rc}")


@test
def test_debug_ir_links_the_same_through_the_mirror():
    """**镜像链接器**对 `--debug` 的同一条判据（`docs/210` §7）。

    **Why**：参考实现与自举镜像**各有一套**解析器，而"元数据被忽略"这件事要**两边都成** ——
    只修参考侧的话，用户手上那份（包内那只，`loment/tools/lomelf.lomt`）照样链不出来。
    这条判据喂给镜像的正是 `--debug` 的那一份 IR，产物必须与参考**逐字节相同**。

    **How to apply**：语料取 L2 那一份 —— 它的 IR 同时含 `#dbg_declare`、带 `, !dbg !N`
    附着的 `icmp`/`store`，以及收集器整段运行期，是"元数据混进指令流"最容易出错的形状。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "dbgl2.lomt"
        src.write_text(_DEBUG_L2_SRC, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=src)
        ll_text = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
        assert "#dbg_declare" in ll_text, "语料没进 debug 档，这条判据在空转"
        want, _ = lomelf.compile_ll(ll_text)
        llrepo = ROOT / "loment" / "build" / "_mirror_dbg.ll"
        elfrepo = ROOT / "loment" / "build" / "_mirror_dbg.elf"
        llrepo.parent.mkdir(parents=True, exist_ok=True)
        try:
            llrepo.write_text(ll_text, encoding="utf-8", newline="\n")
            rc, err = _mirror_run(mir, "loment/build/_mirror_dbg.ll", "loment/build/_mirror_dbg.elf")
            assert rc == 0, f"镜像拒了 `--debug` 的 IR (退出 {rc}): {err[-300:]}"
            nat = elfrepo.read_bytes()
        finally:
            elfrepo.unlink(missing_ok=True)
            llrepo.unlink(missing_ok=True)
        assert nat == want, f"镜像的 `--debug` 产物与参考不同 ({len(nat)}B vs {len(want)}B)"
        print(f"      `--debug` 的 IR: 镜像与参考都出 {len(nat)}B，逐字节同")


#: 只测一件事的 IR：`%j = phi [.., %i]` 与 `%i = phi [.., %i2]` 是**同一前驱上的一对**，
#: `%j` 因此每次回边取 `%i` 的**旧值**。转 3 圈后 `%j` 必须是 2。
_PHI_PAIR_IR = '''define i32 @_start() {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i2, %body ]
  %j = phi i32 [ 100, %entry ], [ %i, %body ]
  %d = icmp uge i32 %i, 3
  br i1 %d, label %out, label %body
body:
  %i2 = add i32 %i, 1
  br label %loop
out:
  %c = zext i32 %j to i64
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %c, i64 0, i64 0)
  ret i32 0
}
'''


@test
def test_mirror_phi_is_simultaneous():
    """镜像的 **phi 是同时赋值的**（LLVM 语义），不是"按声明顺序读一个写一个"。

    **Why**：同一前驱上的多个 phi 必须**一起**生效。最典型的形状是链表遍历里的那一对
    —— `%cur = phi [.., %nxt]` 与 `%prev = phi [.., %cur]`。一趟顺序写时，先写 `%cur`
    再读 `%cur` 给 `%prev`，`%prev` 拿到的就成了 `%nxt`。

    实测（2026-09-25）：这一条把 `__loment_free` 的空闲链表插入变成**每次都插在头部**
    —— 链表恒为 1 个节点，`free` 过的块再也取不回来；`gc_auto` 的收集器因此回收不掉，
    arena 一路涨到 OOM。**两个实现都错**（`tools/lomelf.py` 与 `loment/tools/lomelf.lomt`），
    而 `loment_p8_test` 一直绿 —— 它比的是**文本**，phi 的语义不在文本里。

    **How to apply**：这份 IR 只测这一件事（3 圈后 `%j` 该得 **2**；顺序写会得 **3**）。
    两半都要：
      * **跑出来的值**：参考实现编出来的产物得 2；
      * **两边的字节**：同一份 IR 过自举镜像，与参考逐字节相同 —— 少了这半，
        `loment/tools/lomelf.lomt` 退回一趟写也照样绿（那正是它当时的状态）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    want_elf, _info = lomelf.compile_ll(_PHI_PAIR_IR)
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        nat = td / "phipair.native"
        nat.write_bytes(want_elf)
        rc, _out = _run_bin(nat, "phipair", td, timeout=20)
    assert rc == 2, (
        f"phi 不是同时赋值：`%j` 得 {rc}（期望 2；顺序写会得 3）。"
        "查 `_emit_phis_then` / `emit_phis` 是不是走了一趟（因果链见 docstring）")
    if not _clang():
        print("      参考侧: phi 同时赋值 (得 2)；自举侧: 无 clang, 跳过字节比对")
        return
    mir = _mirror()
    llrepo = ROOT / "loment" / "build" / "_phipair.ll"
    elfrepo = ROOT / "loment" / "build" / "_phipair.elf"
    try:
        llrepo.write_text(_PHI_PAIR_IR, encoding="utf-8", newline="\n")
        rcm, err = _mirror_run(mir, "loment/build/_phipair.ll", "loment/build/_phipair.elf")
        assert rcm == 0, f"镜像退出 {rcm}: {err[-200:]}"
        natm = elfrepo.read_bytes()
    finally:
        elfrepo.unlink(missing_ok=True)
        llrepo.unlink(missing_ok=True)
    assert natm == want_elf, (
        f"phi 的两份实现不同 ({len(natm)}B vs {len(want_elf)}B) —— "
        "自举侧多半还是有 phi 就一趟写")
    print("      phi 同时赋值: 参考得 2, 自举侧逐字节相同")


#: `#313`：GEP 的下标是**符号扩展**的（LLVM LangRef）。`@g[6] - 4` 该落在 `@g[2]`；
#: 零扩展让它落到 `@g[6] + 4294967292` —— 4 GiB 之外，读它就是 SIGSEGV（139，零输出）。
#: 程序把 42 写在 `@g[2]`，再从那个负偏移读回来当退出码：**修好得 42，没修得 139**。
_NEG_GEP_IR = """\
@g = internal global [8 x i8] zeroinitializer

define i32 @_start() {
entry:
  %g2 = getelementptr [8 x i8], ptr @g, i32 0, i32 2
  store i8 42, ptr %g2
  %p = getelementptr [8 x i8], ptr @g, i32 0, i32 6
  %q = getelementptr i8, ptr %p, i32 -4
  %v = load i8, ptr %q
  %z = zext i8 %v to i32
  %c = zext i32 %z to i64
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %c, i64 0, i64 0)
  ret i32 0
}
"""


@test
def test_a_negative_gep_index_is_sign_extended():
    """负的 `i32` 下标**不许被零扩展**（`#313`）—— 两半都要，缺哪一半都会漏一种错法。

    **Why**：`Emitter.get` 的契约是"零扩展到 64 位，符号语义在用到的地方显式 `sext`"
    （它自己的 docstring 就这么写）—— 而 GEP 的下标**恰好就是要符号的那一处**，那里却
    调了 `get`。`-4` 于是变成 `+4294967292`：地址跑到 4 GiB 之外，撞了就是 SIGSEGV。
    结构体那条路不受影响（`field_offset` + `alu_ri`，那条本来就符号扩展），所以这是
    **同一个函数里两条路打架**，不是设计如此。

    **How to apply**：
      * **行为那一半**（参考实现）：编出来跑，退出码该是 **42**；没修是 **139**。
      * **孪生那一半**（`loment/tools/lomelf.lomt`）：同一份 IR 过自举镜像，与参考
        **逐字节相同**。少了这一半，只修 Python 那一侧也照样绿 —— 而两份实现打架正是
        这个仓最不该出现的东西（比**文本**的那类判据抓不到它）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    want_elf, _info = lomelf.compile_ll(_NEG_GEP_IR)
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        nat = td / "neggep.native"
        nat.write_bytes(want_elf)
        rc, _out = _run_bin(nat, "neggep", td, timeout=20)
    assert rc == 42, (
        f"负数 GEP 下标没被符号扩展：退出码 {rc}（期望 42；139 = SIGSEGV，即地址跑到 4 GiB 外）。"
        "查 `tools/lomelf.py` 的 `_gep` 是不是还在用 `self.get` 而不是 `self.get_signed`")
    if not _clang():
        print("      参考侧: 负下标符号扩展 (得 42)；自举侧: 无 clang, 跳过字节比对")
        return
    mir = _mirror()
    llrepo = ROOT / "loment" / "build" / "_neggep.ll"
    elfrepo = ROOT / "loment" / "build" / "_neggep.elf"
    try:
        llrepo.write_text(_NEG_GEP_IR, encoding="utf-8", newline="\n")
        rcm, err = _mirror_run(mir, "loment/build/_neggep.ll", "loment/build/_neggep.elf")
        assert rcm == 0, f"镜像退出 {rcm}: {err[-200:]}"
        natm = elfrepo.read_bytes()
    finally:
        elfrepo.unlink(missing_ok=True)
        llrepo.unlink(missing_ok=True)
    assert natm == want_elf, (
        f"两份实现不同 ({len(natm)}B vs {len(want_elf)}B) —— 自举侧多半还在零扩展那个负下标")
    print("      负 GEP 下标: 参考得 42, 自举侧逐字节相同")


@test
def test_gc_manual_free_list_recycles():
    """`gc_manual` 的**空闲链表真的被复用**（不只是前沿回退）—— 判据是**一对**程序。

    **Why**：`test_free_really_reclaims` 测的是**前沿回退**（LIFO：分配—释放—再分配，
    被释放的那块正好顶着前沿）。那条在**空闲链表**整条坏掉时照样绿 —— 链表只在
    "放掉的块**不是**栈顶"时才起作用。

    **How to apply**：循环里同时握两个块 —— `cur` 是新分配的（**它就是栈顶**），
    `prev` 是上一圈那个；`free(prev)` 时 `cur` 还活着，于是**回退永远不触发**
    （回退要求被释放的块顶着前沿），回收只能来自链表复用。
      * 带 `free`   → 跑完，退出码 7（`s == 12000`：每圈读到的都是 3）；
      * 不带 `free` → **必须崩**（rc = 132，arena 耗尽）。这一半是**证伪**：
        它保证"跑通"不是"反正都跑得通"。

    实测（2026-09-25）：这条在 phi 修好之前是 **rc = 132** —— 那时的链表恒为 1 个节点。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module gclist\nchoose gc_manual\nchoose runtime\n\nfn _start() {\n"
            "    let prev: ptr = alloc(64);\n"
            "    store8(prev, 0, 3);\n"
            "    let i: u32 = 0;\n"
            "    let s: u32 = 0;\n"
            "    while i < 4000 {\n"
            "        let cur: ptr = alloc(64);\n"
            "        store8(cur, 0, 3);\n"
            "        %s"
            "        s = s + load8(prev, 0);\n"
            "        prev = cur;\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 12000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, extra in (("free", "free(prev);\n        "), ("nofree", "")):
            src = td / f"{tag}.lomt"
            src.write_text(body % extra, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"list_{tag}", td, timeout=30)[0])
    assert got[0] == 7, (
        f"带 `free` 的那只没跑通：退出码 {got[0]}（期望 7）。"
        "峰值只有两块、总量 4000×72 B ≈ 288 KB —— 跑不通就说明链表没复用"
        "（回退在这儿帮不上忙：被释放的块从来不是栈顶）")
    assert got[1] != 7, (
        f"**不带 `free` 的那只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出链表复用："
        "arena 没被耗尽，说明它比 288 KB 大得多")
    print(f"      带 free 跑通 (rc={got[0]})；不带 free 耗尽 (rc={got[1]}) —— 链表复用是真的")


@test
def test_gc_auto_collects_while_manual_exhausts():
    """`choose gc_auto` 的收集器**真的回收**（`docs/210` §2.1）—— 判据是**一对**程序。

    **Why**：静态判据能钉住"收集器发了、挂点挂了"，却对"收集器是个空函数"照样绿。
    这一条是那一半：跑起来看。

    **How to apply**：两只程序**逐字同源**，只差 `choose` 那一行。循环里每圈漏掉一块
    64 字节、只留 `a` 一块活到末尾（末尾 `load8(a, 0) == 5` 就是"活块没被误回收"的
    守门）。总量 20000×72 B ≈ 1.4 MB，而 arena 只有 64 KiB：
      * `gc_auto` → **跑完**（rc = 7）；
      * `gc_manual` → **必然耗尽**（rc = 132，abort）。这一半是**证伪**：arena 没被
        撑爆就说明它比 1.4 MB 大得多，那这条判据什么也没测。

    **端到端**：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。

    ⚠ **这条判据的边界**（别当成"收集器万能"）：触发按**分配量**（32 KiB）记，
    而 arena 是 64 KiB —— 所以**活集超过约 32 KB 的程序今天照样会耗尽**。
    这是明写的限制（`docs/210` §7），不是本判据能证的东西。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcauto\n%s\nchoose runtime\n\nfn _start() {\n"
            "    let a: ptr = alloc(8);\n"
            "    store8(a, 0, 5);\n"
            "    let i: u32 = 0;\n"
            "    while i < 20000 {\n"
            "        let t: ptr = alloc(64);\n"
            "        store8(t, 0, 1);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if load8(a, 0) == 5 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("auto", "choose gc_auto"), ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"auto_{tag}", td, timeout=60)[0])
    assert got[0] == 7, (
        f"`gc_auto` 那一只没跑完：退出码 {got[0]}（期望 7）。要么收集器没被调用，"
        "要么它没真的回收（标记/清扫/前沿回退任一处坏了），要么它把活块也收了")
    assert got[1] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出回收："
        "arena 没被耗尽，说明它比 1.4 MB 大得多")
    print(f"      auto 跑通 (rc={got[0]}，1.4 MB 请求过 64 KiB arena)；"
          f"manual 耗尽 (rc={got[1]}) —— 自动回收是真的")


#: `#367` 的语料：**一次 `alloc` 都不写**，只靠 `str_concat` 分配。6000 次拼接 × 16 字节
#: （8 的请求取整成 16）≈ 94 KB，**远远**过 64 KiB arena。
#:
#: **后缀为什么是 4 字节、而不是当年那个 40**：这一格原先是 40 字节，用来**绕开**时钟的
#: 缺陷 —— 触发按**请求字节**计，而堆按**块字节**消耗（请求 + 8 字节头、再向上取整到 8）；
#: 4 字节后缀那个比值是 2，于是计数器攒到 32768 的那一刻 arena 也正好满，`gc_auto` 会
#: **先耗尽后收集**，判据变成在测那个比值而不是在测触发。**时钟修好之后**（它算的就是分配器
#: 真正吃掉的那两行 `(sz + 15) & -8`）那个绕开不再需要，于是回到 4 字节 —— 这一版才
#: **钉得住**这个修复：把时钟改回只数载荷，`gc_auto` 那一只立刻变 132。
#: （`gc_auto_alpha` 两种后缀都一样过 —— 它另有一条**空间触发**，不靠这个计数器。）
_CONCAT_ONLY_SRC = (
    "module concatonly\n%s\nchoose runtime\n\n"
    "fn _start() {\n"
    "    let s: str = \"abcd\";\n"
    "    let i: u64 = 0;\n"
    "    while i < 6000 {\n"
    "        let t: str = str_concat(s, \"efgh\");\n"
    "        i = i + 1;\n"
    "    }\n"
    "    syscall4(60, 7, 0, 0);\n"
    "}\n")


@test
def test_a_concat_only_program_still_collects():
    """只靠 `str_concat` 分配的程序也**必须**触发收集（`#367`）—— 两档各一只，退出码 7。

    **Why**：触发原先只发在 `alloc` 的**调用点**上（`lomentc.py` 的 `builtin()` 那一支、
    `codegen.lomt` 的 `alloc` 分支），而 `str_concat` 走的是**同一个** `alloc_ir` /
    `emit_alloc_seq`，却**不经过**那两处 —— 于是它分配的字节**不计入**那个计数器。
    两档都中，而且 alpha 也救不了：它的自适应阈值与**空间触发**都住在
    `__loment_maybe_collect` 里，而那个函数**一次都不会被调用**。

    改法：把触发挪进共用的分配发射里（`alloc_ir` / `emit_alloc_seq`）—— 于是**每一次**
    分配都计数。对只含 `alloc` 的程序，发出的字节**完全不变**（顺序与实参同值）。

    **How to apply**：这只语料里**一个 `alloc` 都没有**，这正是它的证伪力所在。
    静态那一半钉"调用点存在"（修前 `call void @__loment_maybe_collect` 出现 **0** 次
    —— 函数定义在，没人调）；动态那一半钉"跑得完"（修前 rc=132 耗尽，修后 rc=7）。
    两半都要，因为"定义在"这件事会让人误以为它被调了。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        calls = {}
        for tier in ("gc_auto", "gc_auto_alpha"):
            src = td / f"{tier}.lomt"
            src.write_text(_CONCAT_ONLY_SRC % f"choose {tier}", encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            text = ll.read_text(encoding="utf-8")
            calls[tier] = text.count("call void @__loment_maybe_collect")
            blob, _info = lomelf.compile_ll(text)
            nat = td / f"{tier}.native"
            nat.write_bytes(blob)
            got[tier] = _run_bin(nat, f"concat_{tier}", td, timeout=60)[0]
    nocall = [k for k, v in calls.items() if v == 0]
    assert not nocall, (
        f"这些档位里 `__loment_maybe_collect` **一次都没被调用**（{nocall}）—— 触发只发在 "
        "`alloc` 调用点上，而这只语料里没有 `alloc`，于是收集永远不会发生")
    bad = [f"{k}={v}" for k, v in got.items() if v != 7]
    assert not bad, (
        f"只拼字符串的程序没跑完（{', '.join(bad)}；期望都是 7，132 = arena 耗尽）。"
        "成因：`str_concat` 的分配不计入触发计数")
    print(f"      gc_auto 触发调用 {calls['gc_auto']} 次 / gc_auto_alpha {calls['gc_auto_alpha']} 次，"
          "两档都跑完（无 `alloc` 的语料）")


@test
def test_collection_keeps_blocks_that_were_already_free():
    """**回收必须留住"回收之前就已经空闲"的块**（`FujoJTOP/loment#366`）—— 判据是**三只**。

    **Why**：`test_gc_auto_collects_while_manual_exhausts` 只测"垃圾能被收回来"。回收那一趟在
    收垃圾之前会**把空闲链表清空**（`__loment_collect` 与 `__loment_minor` 原先各一句
    `store i32 0, ptr @__loment_freehead`），指望清扫把每一块**重新挂回去**。而清扫走的是
    `__loment_free`，它有一条守卫：**只在块头 `next` 是负数（借出态）时才链接**（`#164`）——
    已经空闲的块头里存的正是链表后继，守卫直接返回。于是"清空 + 重挂"只重挂了这一趟
    **新死**的块，**上一趟之前就空闲的块从此谁都找不着**：既不在链表上、也不在前沿里。
    （落地时那两行是**直接删掉**的 —— 既然守卫本身已经挡住了双重插入，重置就只剩下丢块这一件事。
    另一条判据 `test_a_collection_keeps_the_free_list` 直接量那件事，本条量的是它的**后果**。）

    **How to apply**：循环里只握一块活块（`p`），每圈分配一块新的、再把**上一块** `free` 掉 ——
    被 `free` 的那一块在回收发生时**没有任何根指着它**（槽 `p` 已经改指新块），所以它正是
    "已经空闲、应当留在链上"的那一类。每圈 8008 字节，跑 300 圈 = 2.4 MB，而 arena 只有 64 KiB：
      * `gc_auto` **+ `free`** → **跑完**（rc = 7）：链表在回收之后仍在，分配一直命中它；
      * `gc_manual` + `free` → **跑完**（rc = 7）：这一只钉住"程序本身是能跑的" —— 少了它，
        第一只红了会分不清是"回收吃掉了链表"还是"这份程序本来就装不下"；
      * `gc_manual` + **不 `free`** → **必须崩**（rc = 132）：这一半是**证伪** —— 它证明 arena
        确实装不下 2.4 MB，所以上面两只的 7 不是"反正都跑得通"。

    实测（2026-10-15）：修之前第一只是 **rc = 132**（约八次回收就把 64 KiB 丢光）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module freesurvive\n%s\nchoose runtime\n\nfn _start() {\n"
            "    let p: ptr = alloc(8000);\n"
            "    store8(p, 0, 9);\n"
            "    let i: u32 = 0;\n"
            "    while i < 300 {\n"
            "        let q: ptr = alloc(8000);\n"
            "        store8(q, 0, 9);\n"
            "        %s"
            "        p = q;\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if load8(p, 0) == 9 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    cases = (("auto", "choose gc_auto", "free(p);\n        "),
             ("manual", "choose gc_manual", "free(p);\n        "),
             ("manual_nofree", "choose gc_manual", ""))
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, tier, extra in cases:
            src = td / f"{tag}.lomt"
            src.write_text(body % (tier, extra), encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"freesurvive_{tag}", td, timeout=60)[0])
    assert got[0] == 7, (
        f"`gc_auto` 那一只没跑完：退出码 {got[0]}（期望 7）。回收把**已经空闲的块**丢了 ——"
        "列表在进清扫之前被清空，而清扫只有'重新挂'那一半才补得回来")
    assert got[1] == 7, (
        f"`gc_manual` 那一只没跑完：退出码 {got[1]}（期望 7）—— 这一只不收集，它的失败"
        "说明这份程序本身就装不下，下面那条对 `gc_auto` 的判断不成立")
    assert got[2] != 7, (
        f"**不 `free` 的那一只也跑通了**（退出码 {got[2]}）—— 那这条判据测不出什么："
        "arena 装得下 2.4 MB，前两只的 7 是'反正都跑得通'")
    print(f"      auto 跑通 (rc={got[0]})；manual 跑通 (rc={got[1]})；"
          f"manual 不 free 耗尽 (rc={got[2]}) —— 回收没吃掉空闲链表")


@test
def test_every_alloc_site_arms_the_collector():
    """**`str_concat` 的分配点也要问一句"该收了没有"**（`FujoJTOP/loment#367`）—— 判据是**一对**。

    **Why**：触发（`@__loment_maybe_collect`）原先只挂在 `alloc` 这个**内建**的发射点上，
    而 `str_concat` 走的是同一个分配函数、却不经过那个点 —— 于是一份**只拼字符串**、自己
    从不 `alloc` 的程序，`@__loment_gcbytes` 永远是 0，**收集器一次都不会被叫醒**，arena 撑爆。
    要钉的就是"每个分配点都得问"。

    **How to apply**：每圈 `str_concat(a, a)` 拼出一条 32 字节的新串、结果覆盖旧值（旧串立刻成
    垃圾），活集恒为 32 字节。跑 10000 圈 = 320 KB 请求，arena 64 KiB：
      * `gc_auto` → **跑完**（rc = 7）；
      * `gc_manual` → **必须崩**（rc = 132）：这一半是**证伪**，证明 320 KB 确实装不下。

    端到端：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。实测（2026-10-15）：
    修之前 `gc_auto` 那只也是 **rc = 132** —— 一份 `alloc` 一次都不调的程序，
    收集器从头到尾没被叫醒过。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module concatgc\n%s\nchoose runtime\n\nfn _start() {\n"
            "    let a: str = \"0123456789abcdef\";\n"
            "    let s: str = str_concat(a, a);\n"
            "    let i: u32 = 0;\n"
            "    while i < 10000 {\n"
            "        s = str_concat(a, a);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if str_len(s) == 32 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, tier in (("auto", "choose gc_auto"), ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % tier, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"concatgc_{tag}", td, timeout=60)[0])
    assert got[0] == 7, (
        f"`gc_auto` 那一只没跑完：退出码 {got[0]}（期望 7）。这份程序一次 `alloc` 都不调，"
        "只有 `str_concat` 在分配 —— 它的分配点没问'该收了没有'，收集器就不会跑")
    assert got[1] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出回收："
        "arena 没被耗尽，说明它比 320 KB 大得多")
    print(f"      auto 跑通 (rc={got[0]}，320 KB 请求过 64 KiB arena)；"
          f"manual 耗尽 (rc={got[1]}) —— 拼接也在触发")


@test
def test_a_recycled_block_does_not_derail_the_walks():
    """**复用一块空闲块之后，块头的"记录尺寸"必须仍是它的实际跨度**（`FujoJTOP/loment#368`）。

    **Why**：`__loment_alloc` 的 `take` 原先在 `taken:` 里**无条件**把块头的 size 写成
    `%need`。真拆了的时候那是对的（余下那截在 `cur + need` 处另立了一块），**不拆**的时候
    就是错的：块的跨度仍是 `%bsz`，记录尺寸却小了 `%bsz - %need`（< 16 字节，所以没拆）。
    于是**每一趟按 size 往前走的遍历**（首次适配、清扫、传播、收集器清标记那两趟）都从
    `cur + need` 继续 —— 那不是下一块，是**块的内部**。载荷里那些字多半是 0 ⇒ `i += 0`
    ⇒ **死循环**（实测：2 分钟不返回；`perf` 的样本全落在收集器清标记那一趟的小循环里）。
    那几十个字节也从此谁都找不到（既不在链上、也不在前沿里）。

    **How to apply**：语料取自 `#368` —— 每圈拼一条 8 字节的串（分配后立刻 `free`），再分配
    一块 16 字节的；被 `free` 的那一块随后会被**复用**，而 `24 - 16 = 8 < 16` 正好走**不拆**
    那条路。跑 4097 圈，两次回收之后遍历就会失步。
      * `gc_auto` → **跑完**（rc = 7）；修之前它**在第二次回收上不返回**（rc = 124，被 timeout
        杀掉 —— 所以这里的超时是判据的一部分，不是保险丝）；
      * `gc_manual` → **必须崩**（rc = 132）：这一半是**证伪** —— 它证明这份程序确实有约
        98 KB 的请求要过 64 KiB 的 arena，跑通不是"反正都跑得通"。

    端到端：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module reuseextent\n%s\nchoose runtime\n\nconst SZ: u32 = 16;\n\n"
            "fn _start() {\n"
            "    let s: str = \"abcd\";\n"
            "    let esc: ptr = alloc(SZ);\n"
            "    store8(esc, 0, 1);\n"
            "    let i: u64 = 0;\n"
            "    while i < 4097 {\n"
            "        let t: str = str_concat(s, \"efgh\");\n"
            "        free(str_ptr(t));\n"
            "        let q: ptr = alloc(SZ);\n"
            "        store8(q, 0, 2);\n"
            "        esc = q;\n"
            "        i = i + 1;\n"
            "    }\n"
            "    syscall4(60, 7, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, tier in (("auto", "choose gc_auto"), ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % tier, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"reuseextent_{tag}", td, timeout=30)[0])
    assert got[0] == 7, (
        f"`gc_auto` 那一只没跑完：退出码 {got[0]}（期望 7）。124 = 被 timeout 杀掉，也就是"
        "**在第二次回收上不返回** —— 有一块被复用之后记录尺寸与实际跨度对不上，"
        "收集器清标记那一趟掉进块内部、读到 size=0 就原地打转")
    assert got[1] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出什么："
        "arena 装得下这约 98 KB 请求，上面那只的 7 不是回收换来的")
    print(f"      auto 跑通 (rc={got[0]})；manual 耗尽 (rc={got[1]}) —— 复用没有让遍历失步")


@test
def test_a_promoted_buffer_is_scanned():
    """**L0 提升到栈上的那块缓冲，里面的字也要被根扫看到**（`FujoJTOP/loment#362`）。

    **Why**：`choose gc_auto_alpha` 的 L0 把 `let NAME: ptr = alloc(<字面量>)` 提到栈上，
    于是那个局部槽里存的是**栈**地址。根扫是这么判的：`rdo = 槽里的值 − @__loment_heap`，
    `ult 65536` 才当堆内地址 —— 栈地址减出来是个巨大的无符号数，**直接跳过**。
    而 `__loment_propagate` 只在堆里走。两者合起来：**那块缓冲的内容从来没人扫**。

    而 L0 的规则**并不禁止**缓冲里装堆指针：`_ptr_only_uses` 会把"只解引用"的用户函数
    （这里就是 `wr64`）吸收进安全集，于是它能把任意 u64 写进缓冲 —— 那正是容器会做的事。
    只被缓冲里那 8 个字节指着的对象，于是被当垃圾收掉，块还被复用，程序读回的是别人的字节。

    **How to apply**：语料取自 `#362`。`hold` 是 L0 候选（`alloc(8)` 字面量、只经由 `wr64`
    解引用）；目标 `t` 唯一的引用就是写进 `hold` 的那 8 个字节；末尾那个 `alloc(32)`
    （块长 40 B，全程序只有目标用这个尺寸）是**探测块** —— 对象真被收了，它就一定落在
    目标那块上、把载荷盖成 200。三只同源程序：
      * `alpha` + 写进去 → **跑完**（rc = 7）；修之前是 **rc = 3**（读回 200）；
      * `alpha` + **不写** → **必须不是 7**：这一半是**证伪** —— 没有那 8 个字节，对象本就
        该被收，探测块就该盖掉它。少了这一半，"跑完"可能只是探测块什么也没盖到；
      * `auto` + 写进去 → **跑完**（rc = 7）：那一档没有 L0，堆块照常被扫，
        是 issue 里那一对里的另一半。

    端到端：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module l0hole\n%s\nchoose runtime\n\n"
            "fn junk() -> u64 {\n"
            "    let p: u64 = alloc(16) as u64;\n"
            "    store8(p as ptr, 0, 200);\n"
            "    return p;\n}\n\n"
            "fn wr64(p: ptr, o: u32, v: u64) -> u32 {\n"
            "    store8(p, o, (v & 255) as u8);\n"
            "    store8(p, o + 1, ((v >> 8) & 255) as u8);\n"
            "    store8(p, o + 2, ((v >> 16) & 255) as u8);\n"
            "    store8(p, o + 3, ((v >> 24) & 255) as u8);\n"
            "    store8(p, o + 4, ((v >> 32) & 255) as u8);\n"
            "    store8(p, o + 5, ((v >> 40) & 255) as u8);\n"
            "    store8(p, o + 6, ((v >> 48) & 255) as u8);\n"
            "    store8(p, o + 7, ((v >> 56) & 255) as u8);\n"
            "    return 0;\n}\n\n"
            "fn _start() {\n"
            "    let hold: ptr = alloc(8);\n"
            "    let t: u64 = alloc(32) as u64;\n"
            "    store8(t as ptr, 0, 7);\n"
            "%s"
            "    let i: u32 = 0;\n"
            "    while i < 4000 { let _a: u64 = junk(); i = i + 1; }\n"
            "    let probe: u64 = alloc(32) as u64;\n"
            "    store8(probe as ptr, 0, 200);\n"
            "    if load8(t as ptr, 0) == 7 { syscall4(60, 7, 0, 0); }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    cases = (("alpha", "choose gc_auto_alpha", "    wr64(hold, 0, t);\n"),
             ("alpha_nowrite", "choose gc_auto_alpha", ""),
             ("auto", "choose gc_auto", "    wr64(hold, 0, t);\n"))
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, tier, extra in cases:
            src = td / f"{tag}.lomt"
            src.write_text(body % (tier, extra), encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"l0hole_{tag}", td, timeout=30)[0])
    assert got[0] == 7, (
        f"`gc_auto_alpha` 那一只读到 {got[0]}（期望 7）—— 目标被收了。提升后的缓冲是一块"
        "**栈**内存，它的槽里存的是栈地址，根扫按'减堆基址、小于 arena'判，于是整块缓冲"
        "从来没人扫；只被它指着的对象就成了垃圾")
    assert got[1] != 7, (
        f"**不写那 8 个字节的那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出什么："
        "没有那 8 个字节，对象本来就该被收，探测块就该盖掉它")
    assert got[2] == 7, (
        f"`gc_auto` 那一只读到 {got[2]}（期望 7）—— 那一档没有 L0，堆块照常被扫，"
        "不该在这里出问题（issue 里的另一半正是它）")
    print(f"      alpha 跑通 (rc={got[0]})；alpha 不写就不保 (rc={got[1]})；"
          f"auto 跑通 (rc={got[2]}) —— 提升缓冲的字在根扫里")


@test
def test_l0_static_promotion_bounds_the_heap():
    """**L0（静态提升）真的发生**（`docs/210` §2.2）—— 判据是**一对**程序。

    **Why**：L0 的静态那一半（`lomentc_test::test_l0_rule_pair`）钉的是"编译器决定提走哪几个
    站点"（那对程序上 16 个站点 → 11 个）；这一条钉**整条链**：提走之后程序**跑得对**，
    而且 arena 真的**没被碰**。`docs/210` §5 末尾点名的就是这一对 —— 静态形式更强
    （产物里连 `@__loment_alloc` 都不剩），但"整条链跑得通"是另一件事，值得单独有。

    **How to apply**：一对**逐字同源**、只差 `choose` 一行的程序 —— 分配写在**辅助函数**
    `bump()` 里、循环在**调用方**，于是 L2（按循环体开纪元）接不到（循环体里没有 `alloc`）；
    尺寸是**字面量** `alloc(64)` ⇒ 落 L0 那一格。4000 次调用 × 64 B ≈ 256 KB 过 64 KiB arena：
    `gc_auto_alpha` **跑完**（rc=7）/ `gc_manual` **耗尽**（rc=132）。

    退出码还兼判**正确性**：`s` 累加 4000 次 `load8` 读回来的 5，必须正好 20000 ——
    提升到栈上的那块缓冲被**反复复用**，写进去的字节必须每次读得回来。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcl0\n%s\nchoose runtime\n\n"
            "fn bump() -> u32 {\n"
            "    let p: ptr = alloc(64);\n"
            "    store8(p, 0, 5);\n"
            "    return load8(p, 0);\n"
            "}\n\n"
            "fn _start() {\n"
            "    let s: u32 = 0;\n"
            "    let i: u32 = 0;\n"
            "    while i < 4000 {\n"
            "        s = s + bump();\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 20000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("manual", "choose gc_manual"), ("alpha", "choose gc_auto_alpha")):
            src = td / f"gcl0_{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"gcl0_{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"gcl0_{tag}", td, timeout=60)[0])
    assert got[1] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[1]}（期望 7）。要么提升没发生（256 KB 请求"
        "过不了 64 KiB arena），要么提升上去的那块缓冲被写坏了（退出码 3 表示累加和不等于 20000）")
    assert got[0] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[0]}）—— 那这条判据测不出提升："
        "256 KB 的请求本该把 64 KiB 的 arena 耗尽")
    print(f"      manual 耗尽 (rc={got[0]})；alpha 跑通 (rc={got[1]}) "
          "—— 字面量尺寸的分配提到栈上，arena 一个字节没碰")


@test
def test_l2_if_branch_epoch_bounds_the_heap():
    """**L2 也认 `if` 的两支**（`docs/210` §2.3 / §7）—— 判据是**一对**程序。

    **Why**：L2 原先只认 `while`/`for` 的体（`docs/210` §7 把"`if` 的体还是块、也能开纪元"
    列在"没做"里）。这一格把它补上：`if` 的每一支与循环体是**同一个形状的块**，规则也还是
    那一条（`_l2_ok`），"体"换成那一支的 token 跨度。

    **How to apply**：一对**逐字同源**、只差 `choose` 一行的程序。形状是**故意挑的** ——
    要让 L0、L1、以及**调用方的循环纪元**都接不到，只有 `if` 那一支自己能救：

      * `alloc(n)` 的尺寸是**变量**（`let n: u32 = 64;`）—— L0 只认字面量，接不到；
      * 分配写在**辅助函数** `step()` 里、循环在**调用方** —— 调用方的循环体里没有 `alloc`，
        循环那一格（按**循环体**开纪元）也接不到；
      * 分配在**分支里**（不在函数体顶层）—— L1 要求"声明必须在函数体顶层（不在任何 `{` 里）"，
        所以 L1 也接不到（`docs/210` §2.5 那条边界）。

    40000 次调用 × 64 B ≈ 2.56 MB 过 64 KiB arena：`gc_auto_alpha` **跑完**（rc=7，
    而且 `t == 40000` 顺带判了每次读回来的值没被写坏）/ `gc_manual` **耗尽**（rc=132）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcif\n%s\nchoose runtime\n\n"
            "fn step(x: u32) -> u32 {\n"
            "    let n: u32 = 64;\n"
            "    let s: u32 = 0;\n"
            "    if x > 0 {\n"
            "        let p: ptr = alloc(n);\n"
            "        store8(p, 0, 1);\n"
            "        s = s + load8(p, 0);\n"
            "    }\n"
            "    return s;\n"
            "}\n\n"
            "fn _start() {\n"
            "    let t: u32 = 0;\n"
            "    let i: u32 = 0;\n"
            "    while i < 40000 {\n"
            "        t = t + step(1);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if t == 40000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("manual", "choose gc_manual"), ("alpha", "choose gc_auto_alpha")):
            src = td / f"gcif_{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"gcif_{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"gcif_{tag}", td, timeout=60)[0])
    assert got[1] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[1]}（期望 7）。`if` 支那一格纪元要么没开，"
        "要么开在不该开的地方（退出码 3 表示累加和对不上，说明块被提前收回或写坏了）")
    assert got[0] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[0]}）—— 那这条判据测不出 `if` 支的纪元："
        "2.56 MB 的请求本该把 64 KiB 的 arena 耗尽")
    print(f"      manual 耗尽 (rc={got[0]})；alpha 跑通 (rc={got[1]}) "
          "—— `if` 那一支的纪元把每次分配都在支末尾还掉了")


@test
def test_free_refuses_a_double_free():
    """`free` 挡住**双重释放**（#164 / `docs/210` §2.8）—— 两次 `alloc` 不许拿到同一段内存。

    **Why**：块头的 `+4` 以前只有"空闲链下一跳"一个用途，于是 `free` 分不清"第一次"与
    "第二次"。第二次释放把同一块**同时**放在 bump 前沿与空闲链表上 ⇒ 随后两次 `alloc`
    拿到同一段 ⇒ **两段活内存互相写**。它的错法不是崩溃而是**静默串味**，所以必须在
    "程序跑起来"这一层钉住，光看 IR 看不出来。

    **How to apply**：`a` 后面还分配了 `b`，所以 `a` **不是尾块** —— 走挂链那一支（尾块那条
    路由"落在已分配区间内"那条检查挡，是另一条路，另有用例覆盖）。第二次 `free(a)` 之后再
    要两块、分别写不同字节：**没修好时它们会指向同一段**，后写的盖掉先写的 ⇒ 退出码 3。
    借出标记（`next == -1`）正是让"已经释放过"这件事在第二次 `free` 时看得见的东西。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    src_text = (
        "module freedbl\nchoose runtime\n\n"
        "fn _start() {\n"
        "    let n: u32 = 16;\n"
        "    let a: ptr = alloc(n);\n"
        "    let b: ptr = alloc(n);\n"
        "    store8(b, 0, 1);\n"
        "    free(a);\n"
        "    free(a);\n"
        "    let c: ptr = alloc(n);\n"
        "    let d: ptr = alloc(n);\n"
        "    store8(c, 0, 11);\n"
        "    store8(d, 0, 22);\n"
        "    if load8(d, 0) == 22 {\n"
        "        if load8(c, 0) == 11 {\n"
        "            syscall4(60, 7, 0, 0);\n"
        "        }\n"
        "    }\n"
        "    syscall4(60, 3, 0, 0);\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "freedbl.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        ll = _ref_ir(src, td)
        blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
        nat = td / "freedbl.native"
        nat.write_bytes(blob)
        rc, _out = _run_bin(nat, "freedbl", td)
    assert rc == 7, (
        f"双重释放之后分配出来的两块**串味了**：退出码 {rc}（期望 7）。"
        "第二次 `free(a)` 不该被接受 —— 它一接受，`a` 就同时躺在空闲链表和 bump 前沿上，"
        "`c` 与 `d` 会指向同一段内存")
    print("      双重释放之后 c/d 各写各的（rc=7）—— 第二次 free 被挡在门外")


@test
def test_free_refuses_a_pointer_it_does_not_own():
    """`free` 挡住**非自有指针**（#164 / `docs/210` §2.8）—— 越界的那个不许挂进空闲链。

    **Why**：`free` 是从指针直接算块偏移的（`sub`/`trunc` 都会回绕），以前既没验范围也没验
    对齐。一个 `ptr_add(p, 60000)` 那种指针会被当成块首，把**队外的一块偏移**挂进空闲链表
    —— 下一次 `alloc` 顺着链表走就会拿到一个 arena 之外的"块"。这是**输入侧**的洞：
    `free` 是普通内建函数，程序随时能把派生指针交给它。

    **How to apply**：抛一个远在 arena 之外的派生指针进去，然后照常做一轮
    `alloc`→写→读。判据有两条腿：**不许崩**（旧的会把垃圾挂链，随后 `alloc` 走进野块），
    而且**正常的一次分配仍然对**（惰性返回不等于把分配器弄停）。末尾空转的 `free(bad)`
    如果被接受，`b` 的写入会被挂在链上的野块带偏，退出码就不是 7。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    src_text = (
        "module freebad\nchoose runtime\n\n"
        "fn _start() {\n"
        "    let n: u32 = 32;\n"
        "    let a: ptr = alloc(n);\n"
        "    let bad: ptr = ptr_add(a, 60000);\n"
        "    free(bad);\n"
        "    free(a);\n"
        "    let b: ptr = alloc(n);\n"
        "    store8(b, 0, 4);\n"
        "    if load8(b, 0) == 4 {\n"
        "        syscall4(60, 7, 0, 0);\n"
        "    }\n"
        "    syscall4(60, 3, 0, 0);\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "freebad.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        ll = _ref_ir(src, td)
        blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
        nat = td / "freebad.native"
        nat.write_bytes(blob)
        rc, _out = _run_bin(nat, "freebad", td)
    assert rc == 7, (
        f"非自有指针被 `free` 接受了：退出码 {rc}（期望 7）。"
        "越界的指针一旦挂进空闲链表，随后的分配就会踩到 arena 之外")
    print("      越界指针被惰性丢掉，正常的那次分配照旧（rc=7）")


@test
def test_free_still_reuses_the_block_it_just_freed():
    """借出标记**不许把复用弄坏**（#164 / `docs/210` §2.8）—— 这是一条**回归**判据。

    **Why**：为了让 `free` 认出双重释放，`alloc` 开始在块头写 `next = -1`；而 `free` 的前沿
    回退靠的是"这一块正好顶到前沿"。两件事撞在一起时最容易出的错，是把**正常**那一次释放
    也挡掉 —— 那时内存只会一路涨（LIFO 不涨内存的性质没了），而且不会有任何报错。

    **How to apply**：LIFO 一轮 —— `alloc`→`free`→`alloc`，两次拿到的地址必须**相等**
    （前沿回退生效），而且写进去的字节读得回来。`a as u64` 是拿来比地址的（`ptrtoint`）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    src_text = (
        "module freereuse\nchoose runtime\n\n"
        "fn _start() {\n"
        "    let n: u32 = 64;\n"
        "    let a: ptr = alloc(n);\n"
        "    let au: u64 = a as u64;\n"
        "    store8(a, 0, 5);\n"
        "    free(a);\n"
        "    let b: ptr = alloc(n);\n"
        "    let bu: u64 = b as u64;\n"
        "    store8(b, 0, 9);\n"
        "    if bu == au {\n"
        "        if load8(b, 0) == 9 {\n"
        "            syscall4(60, 7, 0, 0);\n"
        "        }\n"
        "    }\n"
        "    syscall4(60, 3, 0, 0);\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "freereuse.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        ll = _ref_ir(src, td)
        blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
        nat = td / "freereuse.native"
        nat.write_bytes(blob)
        rc, _out = _run_bin(nat, "freereuse", td)
    assert rc == 7, (
        f"释放之后没复用回同一地址：退出码 {rc}（期望 7）。这正是「把正常释放也挡掉」的签名 —— "
        "内存会一路涨，而且没有任何报错")
    print("      LIFO 复用照旧：释放后要回来的还是同一块（rc=7）")


@test
def test_gc_heap_example_runs_under_the_collecting_tiers():
    """**真拿 `alloc` 写堆的程序**（`loment/examples/gc_heap`，`docs/210` §7）—— 三个档位各跑一遍。

    **Why**：§7 那两条（"证明面在真实代码里有多宽""收集器在真实工作量下什么样"）在**现有语料上
    问不出答案**：仓里自己的 Loment 代码一共只有 23 个 `alloc` 站点，而 `loment/lib` +
    `loment/selfhost` 那 28 份真代码里**一个都没有**（它们走 `sys_alloc`/`brk`）。这一条把那个
    前提落成一份**真在堆上建结构**的程序：一张**单向链表**（节点 16 B，下一个节点的地址用
    `as u64` 存进块里、`as ptr` 取回 —— 正是 §4.2 那句"指针进得了堆块"的形状），一个**长命**
    节点 + 每圈一个**短命**节点，6000 圈 × 16 B = 96 KB 的请求过 64 KiB arena。

    **判据**：`gc_manual` **耗尽**（rc=132 —— 没人还它）；`gc_auto` 与 `gc_auto_alpha` **跑完**
    （rc=7）。而那个 7 是**读回来的**：`walk(keep)` 只该看见一个节点、值是 7 ⇒ 长命的那个**没被
    误收**（短命的那些被收了才对）。少了这一句，一个"把什么都收掉"的收集器也能过。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    src_path = ROOT / "loment" / "examples" / "gc_heap" / "main.lomt"
    assert src_path.exists(), f"缺那份真在堆上建结构的例子: {src_path}"
    base = src_path.read_text(encoding="utf-8")
    assert "choose gc_auto_alpha" in base, "例子里那一行档位没了 —— 这条判据要拿它换档"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        for tag, ch in (("manual", "choose gc_manual"), ("auto", "choose gc_auto"),
                        ("alpha", "choose gc_auto_alpha")):
            src = td / f"gcheap_{tag}.lomt"
            src.write_text(base.replace("choose gc_auto_alpha", ch, 1),
                           encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"gcheap_{tag}.native"
            nat.write_bytes(blob)
            got[tag] = _run_bin(nat, f"gcheap_{tag}", td, timeout=180)[0]
    assert got["manual"] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got['manual']}）—— 那这份工作量压不出收集器："
        "96 KB 的请求本该把 64 KiB 的 arena 耗尽")
    for tag in ("auto", "alpha"):
        assert got[tag] == 7, (
            f"`gc_{tag}` 那一只没跑完：退出码 {got[tag]}（期望 7）。3 表示长命的那个节点被误收了 "
            f"（`walk(keep)` 读回来的不是 7），132 表示耗尽")
    print(f"      gc_heap: manual 耗尽 (rc={got['manual']})；auto rc={got['auto']}；"
          f"alpha rc={got['alpha']}（7 是 `walk` 读回来的 —— 长命的没被误收）")


@test
def test_gc_alpha_adaptive_bounds_a_large_live_set():
    """`gc_auto_alpha` 的**自适应**收集器收得下更大的活集（`docs/210` §2.4 的"策略池"）。

    **Why**：`gc_auto` 那条明写的上限是"**活集 ≳ 32 KB 会耗尽**"—— 它按**固定** 32 KiB 的
    分配量触发，于是"活集 + 至多 32 KiB 垃圾 ≤ 64 KiB"，活集一过 32 KB 就装不下。alpha
    的自适应触发多一条**空间触发**（前沿快满就收），把这一条抬到"活集接近整个 arena"。

    **How to apply**：一对逐字同源、只差 `choose` 一行的程序 —— 活集 **44 KB**（`a`+`b`，
    尺寸是变量所以不是 L0、末尾还在用所以活到最后），随后循环里每圈漏一块**收得掉的垃圾**
    （`chew` 里 `p` 是 L3：`let q: ptr = p` 外逃，出了 `chew` 就没人指得到）。总量
    400×136 ≈ 53 KB 请求过 64 KiB arena：
      * `gc_auto` → **耗尽**（rc=132）—— 固定触发还没到，arena 先满了；
      * `gc_auto_alpha` → **跑完**（rc=7）—— 空间触发在快满时收掉垃圾。
    两只是**同一份源**，所以差别只来自那一档的触发策略。端到端过**包内镜像链接器**。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcadapt\n%s\nchoose runtime\n\n"
            "fn chew(n: u32) -> u32 {\n"
            "    let p: ptr = alloc(n);\n"
            "    let q: ptr = p;\n"
            "    store8(q, 0, 1);\n"
            "    return load8(q, 0);\n"
            "}\n\n"
            "fn _start() {\n"
            "    let sz: u32 = 22000;\n"
            "    let a: ptr = alloc(sz);\n"
            "    store8(a, 0, 5);\n"
            "    let b: ptr = alloc(sz);\n"
            "    store8(b, 0, 6);\n"
            "    let s: u32 = 0;\n"
            "    let i: u32 = 0;\n"
            "    while i < 400 {\n"
            "        s = s + chew(128);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if load8(a, 0) == 5 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("auto", "choose gc_auto"), ("alpha", "choose gc_auto_alpha")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"adapt_{tag}", td, timeout=60)[0])
    assert got[1] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[1]}（期望 7）。自适应收集器要么没生效，"
        "要么空间触发 / 活集量法 / 阈值任一处坏了")
    assert got[0] != 7, (
        f"**`gc_auto` 那一只也跑通了**（退出码 {got[0]}）—— 那这条判据测不出自适应的好处："
        "固定 32 KiB 触发本该在活集 44 KB 时装不下 arena")
    print(f"      gc_auto 耗尽 (rc={got[0]}，活集 44 KB)；gc_auto_alpha 跑通 (rc={got[1]}) "
          "—— 自适应触发收得下更大的活集")


@test
def test_gc_alpha_minor_keeps_a_young_block_reachable_from_an_old_one():
    """分代那一档的**写屏障是承重的**（`docs/210` §2.7）—— 老块里存着指向年轻块的指针。

    **Why**：小回收**不扫老年代**（那是它省下来的工），而老块里可能存着**指向年轻代的真
    指针** —— `p as u64`（`ptrtoint`）+ `store64` 就写得出来（`docs/210` §4.2 那条
    「根表是一个可审计的产物」正是想说清这件事）。没有写屏障把那张卡标脏，小回收就会把
    **还活着的**年轻块当垃圾扫掉。它扫得悄无声息：**释放不改有效载荷**，所以要判"它还是
    不是活的"，不能只看那几个字节，要看它**有没有被复用**。

    **How to apply**：三段 churn，每一段都在排除一个"会让目标块不年轻"的机关 ——

      * 目标块**尺寸独一无二**（20000 B，垃圾是 3000）—— 空闲链表接不住它，于是它
        **只能从前沿分配**，而前沿 ≥ `oldtop` ⇒ 它是**真年轻代**。（实测：尺寸撞上垃圾
        尺寸时，`alloc` 会从空闲链表给一个**低地址**的块，那一出生就算"老" —— 判据空转。）
      * 目标块在**第一轮之后**分配 —— 那时 `oldtop` 已经立起来（`big` 已经算老的了）；
      * 之后**换一个尺寸**继续 churn —— 空闲链表接不住新尺寸，年轻代才**真的长起来**，
        小回收才有得扫。（实测：同尺寸会被链表复用、年轻代不长，判据同样空转。）

    末尾读回老块里那个指针指向的字节：必须还是当初写进去的标签。
    **把 `__loment_minor` 里那趟脏卡扫描拆掉，这一条立刻红**（实测 `rc=110`：块被回收、
    字节被后一轮垃圾改写）—— 这就是它证伪得了的理由。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    src_text = ("module gcminor\nchoose gc_auto_alpha\nchoose runtime\n\n"
                "fn w64(p: ptr, off: u32, v: u64) {\n"
                "    let i: u32 = 0;\n"
                "    while i < 8 {\n"
                "        store8(p, off + i, ((v >> (i * 8)) & 255) as u8);\n"
                "        i = i + 1;\n"
                "    }\n"
                "}\n\n"
                "fn r64(p: ptr, off: u32) -> u64 {\n"
                "    let v: u64 = 0;\n"
                "    let i: u32 = 0;\n"
                "    while i < 8 {\n"
                "        let b: u64 = load8(p, off + i) as u64;\n"
                "        v = v + (b << (i * 8));\n"
                "        i = i + 1;\n"
                "    }\n"
                "    return v;\n"
                "}\n\n"
                "fn stash_at(big: ptr, off: u32, sz: u32, tag: u32) {\n"
                "    let g: ptr = alloc(sz);\n"
                "    store8(g, 0, (tag & 255) as u8);\n"
                "    w64(g, 8, g as u64);\n"
                "    w64(big, off, g as u64);\n"
                "}\n\n"
                "fn _start() {\n"
                "    let bs: u32 = 2000;\n"
                "    let big: ptr = alloc(bs);\n"
                "    store8(big, 0, 1);\n"
                "    let a: u32 = 0;\n"
                "    while a < 20 {\n"
                "        stash_at(big, 128, 3000, a + 10);\n"
                "        a = a + 1;\n"
                "    }\n"
                "    stash_at(big, 8, 20000, 201);\n"
                "    let b: u32 = 0;\n"
                "    while b < 40 {\n"
                "        stash_at(big, 192, 3000, b + 80);\n"
                "        b = b + 1;\n"
                "    }\n"
                "    let back: ptr = r64(big, 8) as ptr;\n"
                "    if load8(back, 0) == (201 & 255) {\n        syscall4(60, 7, 0, 0);\n    }\n"
                "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "gcminor.lomt"
        src.write_text(src_text, encoding="utf-8", newline="\n")
        ll = _ref_ir(src, td)
        blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
        nat = td / "gcminor.native"
        nat.write_bytes(blob)
        rc = _run_bin(nat, "gcminor", td, timeout=60)[0]
    assert rc == 7, (
        f"老块里那个指针指向的年轻块**没了**：退出码 {rc}（期望 7）。分代那一档的写屏障 / "
        "脏卡扫描 / 晋升（`oldtop` 推进）/ 年轻代传播，四处任一处坏了都会走到这儿")


#: 判据（一）与（二）共用的语料骨架 —— **只差 `choose` 一行**。
#:
#: 形状：一块**垃圾**（200）在前，一块**活的**（7）紧跟在它 256 字节之内，之后是长 churn。
#: 活块由**局部 `ptr`** 持有（就是根表里的那一格），所以"它该不该活"没有歧义。
#:
#: **两处刻意的写法**（都是为了不让判据落到别的东西上）：
#:   * 尺寸走 `const` **名字**（`alloc(NODE)`）而不是字面量 —— `alloc(<数字字面量>)` 是
#:     **L0（静态提升）的候选形状**，一旦被提走，这块就不在堆上，本判据测的就不是清扫了；
#:   * 再补一句 `t as u64` —— L0 的资格要求名字"只被 `load8`/`store8` 当首参透过"，
#:     一次 `as` 就把它取消掉。两道锁是故意的：这一条的靶子是**清扫**。
_SWEEP_PRECISION_SRC = (
    "module sweepprec\n%s\nchoose runtime\n\n"
    "const NODE: u32 = 16;\n\n"
    "fn bigjunk() -> u64 {\n"
    "    let p: u64 = alloc(64) as u64;\n"
    "    store8(p as ptr, 0, 200);\n"
    "    return p;\n"
    "}\n\n"
    "fn junk() -> u64 {\n"
    "    let p: u64 = alloc(NODE) as u64;\n"
    "    store8(p as ptr, 0, 200);\n"
    "    return p;\n"
    "}\n\n"
    "fn _start() {\n"
    "    let _g: u64 = bigjunk();\n"
    "    let t: ptr = alloc(NODE);\n"
    "    store8(t, 0, 7);\n"
    "    let _esc: u64 = t as u64;\n"
    "    let i: u32 = 0;\n"
    "    while i < 40000 {\n"
    "        let _j: u64 = junk();\n"
    "        i = i + 1;\n"
    "    }\n"
    "    if load8(t, 0) == 7 {\n        syscall4(60, 7, 0, 0);\n    }\n"
    "    syscall4(60, 3, 0, 0);\n}\n")


@test
def test_sweep_keeps_a_live_block_next_to_garbage():
    """清扫**只清自己那一格**（`docs/210` §2.4）—— 判据是**一对**程序，两档各一只。

    **Why**：`@__loment_mark` 是**每 8 字节堆一位**的位图，而释放那一格曾经写
    `store i32 0, ptr %smp` —— 那是**越界清**：`%smp` 指向的是一格**字节**，`store i32`
    一次抹掉 **4 格 = 256 字节堆里的全部块**。被顺带抹掉的块可能**是活的**，而清扫
    **在同一条路径上继续往前走**，于是它走到那些块时看到的位已经是 0 ⇒ **当垃圾收掉**。
    这不是分代那一档特有的：`gc_auto` 与 `gc_auto_alpha` 共用这一个 `__loment_sweep`，
    两档都中（实测：两档各退 3）。

    **How to apply**：垃圾块 64 B（`bigjunk`）先落在偏移 8，活块 16 B 紧跟其后 ——
    两者都在同一个 256 字节窗口里。之后 40000 次 churn 逼出收集，再用**同尺寸**的垃圾
    把活块那块**复用**掉（`junk` 请求 16 B ⇒ 接得住活块那 24 B 的空闲块）—— 于是
    "它还活着吗"可以从**有效载荷**读出来（7 = 活着，200 = 被复用）。判据不猜"释放有没有
    改数据"：它只看复用（`test_gc_alpha_minor_...` 那条注释里点过这个坑）。

    **证伪**：把 `kill:` 里那行加回 `store i32 0, ptr %smp`，两档各退 3（实测）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        for tier in ("gc_auto", "gc_auto_alpha"):
            src = td / f"{tier}.lomt"
            src.write_text(_SWEEP_PRECISION_SRC % f"choose {tier}",
                           encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tier}.native"
            nat.write_bytes(blob)
            got[tier] = _run_bin(nat, f"sweepprec_{tier}", td, timeout=60)[0]
    bad = [f"{k}={v}" for k, v in got.items() if v != 7]
    assert not bad, (
        f"活块被清扫带走了（{', '.join(bad)}；期望都是 7）。成因：清扫清标记时清**宽了** —— "
        "位图上一格字节管 8 个块，`store i32` 一次管 32 个（256 字节堆）；要紧的是清扫"
        "**同一趟**就会走到那些被误清的块，把它们交回分配器")
    print(f"      gc_auto={got['gc_auto']} gc_auto_alpha={got['gc_auto_alpha']}"
          " —— 两档都没把垃圾后面的活块一起收掉")


#: 判据（二）的语料 = **issue #312 那段原样**（`docs/210` §2.9 收的洞）。
#:
#: 目标块的**唯一**引用是"另一个块里按字节写着的那 8 个字节"，而持有者是个真的**堆块**
#: （尺寸走 `const NODE` ⇒ 不走 L0），写那 8 个字节时**两者都还年轻** ⇒ 没有卡被标脏。
#: 于是目标块靠的是**标记的延续**（上一轮标上、下一轮没人清它）活着 —— 这正是清扫
#: 越界清标记时会死掉的东西。
_STORED_POINTER_SRC = (
    "module holdedge\nchoose gc_auto_alpha\nchoose runtime\n\n"
    "const NODE: u32 = 16;\n\n"
    "fn mk() -> u64 {\n"
    "    let p: ptr = alloc(NODE);\n"
    "    store8(p, 0, 7);\n"
    "    return p as u64;\n"
    "}\n\n"
    "fn junk() -> u64 {\n"
    "    let p: ptr = alloc(NODE);\n"
    "    store8(p, 0, 200);\n"
    "    return p as u64;\n"
    "}\n\n"
    "fn _start() {\n"
    "    let tgt: u64 = mk();\n"
    "    let hold: ptr = alloc(NODE);\n"
    "    store8(hold, 0, 1);\n"
    "    store8(hold, 8, (tgt & 255) as u8);\n"
    "    store8(hold, 9, ((tgt >> 8) & 255) as u8);\n"
    "    store8(hold, 10, ((tgt >> 16) & 255) as u8);\n"
    "    store8(hold, 11, ((tgt >> 24) & 255) as u8);\n"
    "    store8(hold, 12, ((tgt >> 32) & 255) as u8);\n"
    "    store8(hold, 13, ((tgt >> 40) & 255) as u8);\n"
    "    store8(hold, 14, ((tgt >> 48) & 255) as u8);\n"
    "    store8(hold, 15, ((tgt >> 56) & 255) as u8);\n"
    "    let i: u32 = 0;\n"
    "    while i < 40000 {\n"
    "        let _j: u64 = junk();\n"
    "        i = i + 1;\n"
    "    }\n"
    "    let t: ptr = tgt as ptr;\n"
    "    if load8(t, 0) == 7 {\n        syscall4(60, 7, 0, 0);\n    }\n"
    "    syscall4(60, 3, 0, 0);\n}\n")


@test
def test_gc_alpha_keeps_an_object_behind_a_young_written_edge():
    """**写时双方都还年轻的那条边**，靠标记延续活着（`docs/210` §2.7）—— issue #312。

    **Why**：老→年轻的边靠**写屏障**记卡，可这条边**写的时候两边都还年轻** —— 那一刻没有
    卡被标脏。它还能活，靠的是**标记的延续**：小回收只清**年轻代**的标记，旧块的位一旦
    标上就一直留着，直到下一次全量。所以"清标记"这件事只要清**宽了**，这条边就断。

    这一条与 `test_gc_alpha_minor_keeps_...old_one` 是**两回事**：那条测**脏卡**（写的时候
    持有者已经老了 ⇒ 卡被标脏 ⇒ 小回收扫卡把它找回来）；这条测**没有任何卡**的那一半。
    两半都在，§2.7 那句话才成立。

    **How to apply**：issue #312 的那段原样 —— 目标块只经由"另一个堆块里按字节写的 8 个
    字节"可达（不是根：它在 `u64` 里）。40000 次 churn 逼出收集，末尾读回那个字节。

    **证伪**：`kill:` 里那行 `store i32 0, ptr %smp` 一加回来，这一条退 3（实测）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "holdedge.lomt"
        src.write_text(_STORED_POINTER_SRC, encoding="utf-8", newline="\n")
        ll = _ref_ir(src, td)
        blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
        nat = td / "holdedge.native"
        nat.write_bytes(blob)
        rc = _run_bin(nat, "holdedge", td, timeout=60)[0]
    assert rc == 7, (
        f"只有「另一个块里按字节写着」的那条引用指着它，而它没了：退出码 {rc}（期望 7）。"
        "多半是清扫（或清年轻代标记那一趟）**清宽了**：位图一格字节管 8 个块，"
        "清一格 = 顺手把邻居的位也清了")


@test
def test_l1_definite_lifetime_bounds_the_heap():
    """**L1（定活）真的发生**（`docs/210` §2.5）—— 判据是**一对**程序。

    **Why**：静态判据（`lomentc_test::test_l1_definite_lifetime_rule`）钉的是"编译器决定了
    要还哪些"；这一条钉"还了之后内存真的不涨"。

    **How to apply**：两只程序**逐字同源**，只差 `choose` 那一行。形状是**故意挑的** ——
    要让 L0 与 L2 都接不到，只有 L1 能救：

      * `alloc(n)` 的尺寸是**变量**（`let n: u32 = 64;`）—— L0 只认字面量，接不到；
      * 分配写在**辅助函数**里、循环在**调用方** —— 调用方的循环体里只有一次调用、没有
        `alloc`，所以 L2 那一格（按**循环体**开纪元）也接不到；
      * `p` 只被 `store8`/`load8` 透过、从不外逃 —— L1 的资格。

    4000 次调用、每次 64 B 请求（≈288 KB）过 64 KiB arena：
      * `gc_auto_alpha` → **跑完**（rc = 7）：每次调用在返回前把它那块还掉了，峰值是一块；
      * `gc_manual` → **耗尽**（rc = 132）。这一半是**证伪**：arena 没被撑爆就说明它比
        288 KB 大得多，那这条判据什么也没测。

    **端到端**：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcl1\n%s\nchoose runtime\n\n"
            "fn work() -> u32 {\n"
            "    let n: u32 = 64;\n"
            "    let p: ptr = alloc(n);\n"
            "    store8(p, 0, 1);\n"
            "    return load8(p, 0);\n"
            "}\n\n"
            "fn _start() {\n"
            "    let i: u32 = 0;\n"
            "    let s: u32 = 0;\n"
            "    while i < 4000 {\n"
            "        s = s + work();\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 4000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("alpha", "choose gc_auto_alpha"), ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"l1_{tag}", td, timeout=60)[0])
    assert got[0] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[0]}（期望 7）。"
        "要么 L1 没插 `free`，要么插的位置不对（那一格该由静态判据先抓住）")
    assert got[1] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出 L1："
        "arena 没被耗尽，说明它比 288 KB 大得多")
    print(f"      alpha 跑通 (rc={got[0]}，4000 次调用每次一块过 64 KiB arena)；"
          f"manual 耗尽 (rc={got[1]}) —— 定活是真的")


def _mirror_run(mir: Path, in_rel: str, out_rel: str, links: tuple[str, ...] = (),
                ports: bool = True) -> tuple[int, str]:
    """在 WSL 里用镜像编一个**仓库内相对路径**的 `.ll`。返回 (退出码, stderr)。

    stdout 与退出码不混在一个流里: 先让它跑, 再单独 `echo -n $?` (与 `_run_bin` 同一条纪律)。

    `ports=True`（默认）时带对象就补 `--allow-ports`（`docs/222` §4 的端口门）：链接器
    **默认拒**带未定义符号的外源对象，判据要在开发通道上跑那条还没完工的路，得显式把门
    放开。没有对象就不传 —— 这样"不带对象"的那些调用逐字节仍是老样子。`ports=False`
    是给**门本身**那条判据用的（同一份对象、不给开关 ⇒ 必须拒）。
    """
    tag = f"{_T}lomelf_m.bin"
    extra = "".join(f" --link {x}" for x in links)
    if links and ports:
        extra += " --allow-ports"
    script = (f"cp {_wsl_path(mir)} {tag} && chmod +x {tag} && cd {_wsl_path(ROOT)} && "
              f"{tag} {in_rel} {out_rel}{extra}; echo -n $?")
    r = _sh(script, 900)
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, r.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------- 用例

@test
def test_lomelf_matches_clang_behavior():
    """同一份 IR: 原生产物与 clang 产物的 stdout 字节 + 退出码完全一致。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            src = ROOT / rel
            name = src.stem
            ll = _ref_ir(src, td)
            ref = td / f"{name}.clang"
            nat = td / f"{name}.native"
            _link_clang(clang, ll, ref)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat.write_bytes(blob)
            want_rc, want = _run_bin(ref, f"{name}_c", td)
            got_rc, got = _run_bin(nat, f"{name}_n", td)
            if (want_rc, want) != (got_rc, got):
                raise AssertionError(
                    f"[{name}] 行为不一致: rc clang={want_rc} native={got_rc}\n"
                    f"  clang  ({len(want)}B): {want[:200]!r}\n"
                    f"  native ({len(got)}B): {got[:200]!r}")
            ok += 1
    print(f"      {ok} 个程序的原生产物与 clang 产物 stdout 字节 + 退出码一致")


@test
def test_every_example_is_decided():
    """`loment/examples/*.lomt` 里**每一份**都得有去处: 要么在 CORPUS 里真跑, 要么在
    NOT_RUN 里带一个理由 —— 没有第三种状态 (docs/205 R6)。

    **Why**: R6 的判据是"包里的示例每一个都进判据"。在这条之前只有 4 份被跑过,
    另外的**没有任何东西看着** —— 新加一份示例, 谁也不会发现它没被覆盖。
    文档里那个"N example programs"的数字更是从来没人核过: 2026-09-23 实测,
    README 写 30、QUICKSTART 写 28、盘上是 32, 三个数字互不相同。

    **How to apply**: 加一份示例就**必须**在这里挑一边。挑 NOT_RUN 要写清理由,
    而理由会被 `test_not_run_reasons_hold` 当场核实。
    """
    names = sorted(p.stem for p in (ROOT / "loment" / "examples").glob("*.lomt"))
    ran = sorted(Path(r).stem for r in CORPUS)
    both = sorted(set(ran) & set(NOT_RUN))
    assert not both, f"CORPUS 与 NOT_RUN 两边都有: {both}"
    undecided = sorted(set(names) - set(ran) - set(NOT_RUN))
    assert not undecided, f"没人管的示例 (编排漏了): {undecided}"
    stale = sorted((set(ran) | set(NOT_RUN)) - set(names))
    assert not stale, f"台账里多了已经不存在的文件: {stale}"
    empty = [k for k, (_kind, why) in NOT_RUN.items() if not why.strip()]
    assert not empty, f"理由为空: {empty}"

    # 文档里的数字要**说得对**, 不是"看起来合理" —— 这就是 R6 说的"不能静默腐烂"。
    for doc in ("README.md", "QUICKSTART.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        hits = re.findall(r"(\d+) example programs", text)
        assert hits, f"{doc} 里找不到 'N example programs' 这句话"
        wrong = [h for h in hits if int(h) != len(names)]
        assert not wrong, (f"{doc} 写的是 {wrong} 份, 盘上是 {len(names)} 份"
                           " —— 数字不会自己跟上, 它得有人改")
    print(f"      {len(names)} 份示例: {len(ran)} 份真跑 + {len(NOT_RUN)} 份带理由")


@test
def test_not_run_reasons_hold():
    """`NOT_RUN` 里的理由**当场成立** —— 真去链一次、真去编一次, 不是信那句话。

    **Why**: 一份"跑不了"的台账最容易退化成一句没人核过的注释: 后端补齐了、入口加上了、
    `while true` 改成会返回了 —— 台账自己不会知道, 而它照样读起来是对的。
    这里按**类别**把理由跑一遍: `no-entry` 要真被链接器以"没有 _start"拒掉,
    `ir-gap` 要真编不出来, `nonterm` 要源码里真是 `while true`。理由不成立 = 判据红。
    """
    td = Path(tempfile.mkdtemp())
    for name, (kind, _why) in sorted(NOT_RUN.items()):
        src = ROOT / "loment" / "examples" / f"{name}.lomt"
        if kind == "nonterm":
            text = src.read_text(encoding="utf-8")
            assert "while true" in text, f"{name}: 说是设计上不终止, 源码里却没有 while true"
            continue
        try:
            ll, got = _ref_ir(src, td), None
        except Exception as e:  # noqa: BLE001
            ll, got = None, f"{type(e).__name__}: {e}"
        if kind == "ir-gap":
            assert got is not None and "IR 后端" in got, (
                f"{name}: 记的是后端缺口, 实际却编过去了 ({got})")
            continue
        assert got is None, f"{name}: 记的是没有入口, 实际连 IR 都编不出来 ({got})"
        try:
            lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            refused = None
        except Exception as e:  # noqa: BLE001
            refused = f"{type(e).__name__}: {e}"
        assert refused and "没有 _start" in refused, (
            f"{name}: 记的是没有 _start 入口, 链接器给的却是: {refused}")
    print(f"      {len(NOT_RUN)} 条理由都当场核过")


@test
def test_lomelf_reports_unsupported_instead_of_miscompiling():
    """不支持的东西必须**报错退出**, 不许静默编出一个错的 ELF。"""
    bad = [
        ("结构体动态下标 GEP",
         "define void @_start() {\nentry:\n  %p = alloca { i32, i32 }\n  %i = load i32, ptr %p\n  %q = getelementptr { i32, i32 }, ptr %p, i32 %i\n  ret void\n}\n"),
        ("间接调用",
         "define void @_start() {\nentry:\n  %f = alloca ptr\n  %g = load ptr, ptr %f\n  call void %g()\n  ret void\n}\n"),
        ("没有 _start",
         "define void @main() {\nentry:\n  ret void\n}\n"),
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for label, text in bad:
            f = td / "bad.ll"
            f.write_text(text, encoding="utf-8", newline="\n")
            try:
                lomelf.compile_ll(text)
            except lomelf.Unsupported as e:
                assert str(e), f"{label}: 报了 Unsupported 但没有消息"
                continue
            raise AssertionError(f"[{label}] 本该报 Unsupported, 却编过去了")
    print(f"      {len(bad)} 类不支持的输入都报了错")


@test
def test_lomelf_selfhost_reports_oversized_input():
    """自举镜像: 输入超过输入缓冲上限要**报错退出**, 不是截断成一个跑起来就崩的二进制 (docs/192)。

    与上面那条"不支持的要报错"同一个家族 —— **不是"能不能编", 是"编不出来时说不说"**。

    自举镜像的输入缓冲是**固定大小**（`lomelf.lomt` 的 `IN_CAP`，布局算出来的：
    `M_IN` 起、`M_TXT` 接，两者之差就是它 —— 所以**上限从源码里读**：2026-09-19 它从
    2 MiB 抬到 4 MiB（`docs/192` §2），把旧数钉死的话这条会跟着红），而 `read_all`
    **读满就返回**。不加守卫的话超限的 `.ll` 被**悄悄截断** —— `.ll` 是逐行语法，截断点
    之后的函数定义凭空消失，汇编出来的是一个缺胳膊少腿、跑起来就段错误的可执行文件。
    2026-09-18 实测：同一份 `.ll`，这边段错误、clang 汇编完全正常。

    **参考侧没有这个上限**（`lomelf.py` 整个读进来）—— 所以这条只钉自举镜像那一格。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        m = re.search(r"const IN_CAP: u32 = (\d+);",
                      (ROOT / "loment" / "tools" / "lomelf.lomt").read_text(encoding="utf-8"))
        assert m, "在 lomelf.lomt 里找不到 `const IN_CAP: u32 = <数>;`"
        cap = int(m.group(1))
        # 仓库里最大的那一份 `.ll` + 注释填充撑过上限（注释不动语义, 本判据只看"拒不拒"）
        big = td / "big.ll"
        pad = "; " + "x" * 78 + "\n"
        need = cap - seed.stat().st_size
        big.write_text(pad * (need // len(pad) + 2) + seed.read_text(encoding="utf-8"),
                       encoding="utf-8", newline="\n")
        assert big.stat().st_size > cap, f"填充没撑过 {cap} (判据自己坏了)"
        rc, err = _mirror_run(mir, _wsl_path(big), "/tmp/lomelf_oversized.probe")
        assert rc != 0, (f"{big.stat().st_size} B 的输入被接受了 (rc={rc}, 上限 {cap}) —— "
                         f"截断是静默的, 产出的二进制跑起来才会崩")
        assert "does not fit" in err, f"拒了, 但没说清是上限的事: {err[:200]!r}"
        print(f"      超限 {big.stat().st_size} B 的 .ll 被拒（上限 {cap} B, 从源码读的）")


@test
def test_lomelf_cli_check_and_usage():
    """CLI: `--check` 不落盘且 rc=0; 用法错误 rc=2。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ref_ir(ROOT / CORPUS[0], td)
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"), str(ll), "--check"],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"--check rc={r.returncode}: {r.stderr[-200:]}"
        assert "[OK]" in r.stdout, r.stdout[-200:]
        assert not (td / "user_hello").exists(), "--check 不该落盘"
        r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py")],
                            capture_output=True, text=True, shell=False)
        assert r2.returncode == 2, f"无参数应当 rc=2, 得到 {r2.returncode}"
    print("      --check 不落盘 rc=0 · 无参数 rc=2")


@test
def test_lomelf_compiles_selfhost_ir_without_clang():
    """**主线判据**: 种子 →(clang 一次)→ stage1 → 发 IR → lomelf 编成 ELF 并跑起来。

    这正是"用户编译一个 Loment 程序不再需要 clang"的形状: clang 只在**从种子重建 stage1**
    那一步出现 (那是 genesis, docs/167 §4 记账), 之后编译用户程序全程没有 clang。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        # stage1 发 IR (自举路, 无 Python 无 clang)
        entry = ROOT / "loment" / "examples" / "user_hello.lomt"
        binn = f"{_T}lomelf_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/examples/user_hello.lomt")
        rr = _sh(script, 300)
        assert rr.returncode == 0, rr.stderr[-300:]
        ll = td / "from_stage1.ll"
        ll.write_bytes(rr.stdout)
        assert b"_start" in rr.stdout, "stage1 的产物不像 IR"
        # lomelf 编它并跑
        blob, _info = lomelf.compile_ll(rr.stdout.decode("utf-8"))
        elf = td / "from_stage1.elf"
        elf.write_bytes(blob)
        rc, out = _run_bin(elf, "from_stage1", td)
        assert rc == 0, f"stage1 发的 IR 编出来的 ELF 跑挂了 rc={rc}"
        assert out == b"M67 RESULT: PASS loment-user\n", f"输出不符: {out!r}"
        assert entry.name == "user_hello.lomt"
    print("      种子 -> stage1 -> IR -> 原生产物 -> 跑出 M67 PASS (编译用户程序全程无 clang)")


@test
def test_lomelf_selfhost_matches_reference():
    """自举侧镜像: `loment/tools/lomelf.lomt` 编出的 ELF 与参考实现**逐字节相同**。

    这条是"编译一个 Loment 程序不需要 clang"真正落脚的地方 —— 参考实现 (Python) 只是
    这格的规格书, 能替用户干活的是自举侧那份。链条里 clang 只出现在"种子 -> stage1"
    一步 (genesis, docs/167 §5 记账)。

    语料是 `CORPUS` 减去 `MIRROR_GAP`（镜像还链不了的那几份, 各带理由; 2026-10-16 起为空）。
    把语料从 4 份放宽到"能跑的每一份"时, 这条**立刻抓到两个缺口** —— 一个是崩
    （issue #102）, 一个是回填表满（当时记成容量, 实为 `do_switch` 的死循环）。
    `MIRROR_GAP` 的执行方式是**只许变短**: 表里每一份每轮都要**真的再链一次**, 链过了就红。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 语料: 参考 IR -> 镜像编 -> 与参考的字节比。`MIRROR_GAP` 里的先跳过,
        # 下面那段会**反向**核它们 —— 说链不过的, 必须真的还链不过。
        total = 0
        for rel in [r for r in CORPUS if Path(r).stem not in MIRROR_GAP]:
            srcl = ROOT / rel
            ll = _ref_ir(srcl, td)
            want, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            llrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.ll"
            elfrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.elf"
            llrepo.parent.mkdir(parents=True, exist_ok=True)
            try:
                llrepo.write_bytes(ll.read_bytes())
                rc, err = _mirror_run(mir, f"loment/build/_mirror_{srcl.stem}.ll",
                                      f"loment/build/_mirror_{srcl.stem}.elf")
                assert rc == 0, f"[{srcl.stem}] 镜像退出 {rc}: {err[-200:]}"
                nat = elfrepo.read_bytes()
            finally:
                elfrepo.unlink(missing_ok=True)
                llrepo.unlink(missing_ok=True)
            assert nat == want, f"[{srcl.stem}] 镜像产物与参考不同 ({len(nat)}B vs {len(want)}B)"
            total += 1

        # 反向核: MIRROR_GAP 里说"链不过"的, **必须真的还链不过**。哪天链过了, 判据红,
        # 逼你把它挪回语料 —— 这就是这张表"只许变短"的执行方式。
        gone = []
        for stem, (seen, why) in sorted(MIRROR_GAP.items()):
            src = ROOT / "loment" / "examples" / f"{stem}.lomt"
            if not src.exists():
                gone.append(f"{stem}: 文件不在了")
                continue
            ll = _ref_ir(src, td)
            llrepo = ROOT / "loment" / "build" / f"_gap_{stem}.ll"
            elfrepo = ROOT / "loment" / "build" / f"_gap_{stem}.elf"
            try:
                llrepo.write_bytes(ll.read_bytes())
                rc, _err = _mirror_run(mir, f"loment/build/_gap_{stem}.ll",
                                       f"loment/build/_gap_{stem}.elf")
            finally:
                elfrepo.unlink(missing_ok=True)
                llrepo.unlink(missing_ok=True)
            if rc == 0:
                gone.append(f"{stem}: 镜像**已经链得过**了 (记的是 {seen}, {why[:40]}…)")
        assert not gone, ("MIRROR_GAP 该变短了 —— 这几份现在能链, 把它们挪回语料: "
                          + " / ".join(gone))
    print(f"      {total} 个程序: 自举镜像与参考逐字节相同; "
          f"另有 {len(MIRROR_GAP)} 份记在 MIRROR_GAP 里, 已反向核实仍然链不过")


#: 手写 IR 夹具: **两个各自独立把镜像打下来过**的形状（issue #1 的两个真成因）。
#: 这是**形状**不是程序 —— 哪一条回退, 判据直接点名, 不用再把 `tour` 二分一遍。
#: 参考实现两条都编得过, 所以期望值是 `lomelf.compile_ll` 现算的。
_MIRROR_SHAPE_IR: dict[str, tuple[str, str]] = {
    "i64_global": (
        "64 位全局 + 非零初值（含聚合）。旧 `one_global` 用 u32 的 `pow256` 做除法: "
        "b=4 时 `256^b` 回绕成 0, 除零当场 SIGILL。`@__loment_caps` 就是这形状, "
        "所以能力域程序全都链不出来（issue #102）。",
        """@scalar = internal global i64 7
@big = internal global i64 4294967296
@neg = internal global i64 -1
@arr = internal global [2 x i64] [i64 1, i64 2]
@i32s = internal global i32 8
@caps = internal constant [1 x { i64, i64, i64, i64 }] [{ i64, i64, i64, i64 } { i64 1163935908, i64 0, i64 4, i64 1 }]
@blob = private unnamed_addr constant [4 x i8] c"ab\\00c"

define void @_start() {
  ret void
}
"""),
    "multiline_switch": (
        "case 表跨行的 `switch`（自举 codegen 发出来的就是这种）。旧 `do_switch` 的 `q` 停在"
        "换行上, `next_label` 找不到 `label` 退回 `(0,0)`, 于是 `q` 被赋成 0 —— 从头重来, "
        "每绕一圈发一条回填, 32768 圈后报\"回填表满了\"。第二个函数是**空 case 表**（只把 `]` "
        "写到下一行）, 一条回填都不需要, 照样打满 —— 这就是它不是容量问题的证据"
        "（issue #2 / #10）。",
        """define i32 @f(i32 %x) {
entry:
  switch i32 %x, label %e [
      i32 0, label %a
      i32 1, label %b
    ]
a:
  ret i32 1
b:
  ret i32 2
e:
  ret i32 3
}
define i32 @g(i32 %x) {
entry:
  switch i32 %x, label %z [
    ]
z:
  ret i32 0
}
define void @_start() {
  ret void
}
"""),
}


@test
def test_mirror_links_the_shapes_that_used_to_take_it_down():
    """镜像对这两个形状的产物与参考**逐字节相同**。

    **Why**: issue #1 说的是"自带的 `tour` 示例编不出来, 而且一声不吭"。它有两个**互相
    独立**的成因, 都在 `loment/tools/lomelf.lomt` 里, 而**语料一份都没覆盖它们** ——
    `loment/examples/switch.lomt` 名字叫 switch, 里面**一条 `switch` 都没有**。
    修完把形状单独钉住: 回退时这条点名, 而不是只剩 `tour` 那条泛泛地红。

    **未修时的实测**（2026-10-16）: `i64_global` → SIGILL (rc=132);
    `multiline_switch` → `lomelf: 回填表满了 … (TB_FIX_MAX)` (rc=1)。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    done = []
    for name, (why, text) in sorted(_MIRROR_SHAPE_IR.items()):
        want, _info = lomelf.compile_ll(text)
        llrepo = ROOT / "loment" / "build" / f"_shape_{name}.ll"
        elfrepo = ROOT / "loment" / "build" / f"_shape_{name}.elf"
        try:
            llrepo.write_text(text, encoding="utf-8", newline="\n")
            rc, err = _mirror_run(mir, f"loment/build/_shape_{name}.ll",
                                  f"loment/build/_shape_{name}.elf")
            assert rc == 0, f"[{name}] 镜像退出 {rc}: {err[-200:]} —— {why}"
            got = elfrepo.read_bytes()
        finally:
            llrepo.unlink(missing_ok=True)
            elfrepo.unlink(missing_ok=True)
        assert got == want, (f"[{name}] 镜像与参考不同 ({len(got)}B vs {len(want)}B) —— {why}")
        done.append(name)
    print(f"      {len(done)} 个形状: 镜像与参考逐字节相同 ({', '.join(done)})")


@test
def test_selfhost_mirror_matches_reference_on_thread_spawn():
    """自举镜像的 `thread_spawn` 定制序列与参考 `lomelf.py` **逐字节相同**。

    **Why**：那条序列是**字面机器码**（不是一串 `mov_*` 助手拼出来的），两侧各写一遍 ——
    参考侧是 `Emitter._SPAWN_HEAD` / `_SPAWN_TAIL`，镜像侧是 `do_call_spawn` 里那一串
    `e1`。**它们漂一个字节就是另一份产物**：`jne 0x13` 那个位移是按后面 19 字节算的
    （3+2+5+2+5+2），少一个字节就跳过 `.parent` 落到子线程那条路上；`call` 的相对地址
    错开就是跑飞。而**别的判据一条都看不见** —— `loment_thread_test` 跑的是参考侧发的
    产物，镜像那条路它碰不到（那边要用 clang 建镜像，代价太大，这里已经建过了）。

    **探针源码从 `loment_thread_test` 借**（同一个常量，不抄第二份）：那份是这条构造的
    主人，这里只做"同一份 IR 过两个链接器"的字节比对。
    """
    import loment_thread_test as th
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    src = ROOT / "loment" / "build" / "_thread_probe.lomt"
    llrepo = ROOT / "loment" / "build" / "_thread_twin.ll"
    elfrepo = ROOT / "loment" / "build" / "_thread_twin.elf"
    src.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.write_text(th._WORKER, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"线程探针自己检查不过: {errs[:2]}"
        ll = lomentc.emit_llvm(mod, ROOT, deps)
        llrepo.write_text(ll, encoding="utf-8", newline="\n")
        want = lomelf.compile_ll(ll)[0]
        rc, err = _mirror_run(mir, "loment/build/_thread_twin.ll",
                              "loment/build/_thread_twin.elf")
        assert rc == 0, f"镜像编线程探针退出 {rc}: {err[-200:]}"
        got = elfrepo.read_bytes()
    finally:
        for p in (src, llrepo, elfrepo):
            p.unlink(missing_ok=True)
    assert got == want, (f"镜像的 thread_spawn 序列与参考不同 "
                         f"({len(got)}B vs {len(want)}B)")
    print(f"      thread_spawn: 镜像与参考逐字节相同 ({len(got)}B)")


@test
def test_both_linkers_agree_on_a_negative_immediate():
    """**负立即数**: 操作数的跨度要含 `-`。

    LLVM IR 的立即数允许负号（`gc_manual` 的分配器里那句 `and i32 %need0, -8` 是第一处），
    而镜像里的操作数是拿"词"量出来的 —— 那一刻它不认 `-`，于是 `-8` 被量成**长度 0**：
    常量物化读到空串，**镜像发 `0`、参考发 `-8`**。

    这条**不依赖分配器**（它哪天不再用负立即数，这条照样在）：把一份语料里的某个正常量
    **取负**，两份链接器仍必须逐字节相同。替换点**找不到就红** —— 免得例句改过之后
    这条静默地什么也没测。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ref_ir(ROOT / "loment" / "examples" / "user_hello.lomt", td)
        text = ll.read_text(encoding="utf-8")
        old = "(i64 1, i64 %t1, i64 %t4, i64 %t8)"
        assert text.count(old) == 1, f"夹具漂了: user_hello 的 IR 里那个实参串出现 {text.count(old)} 次"
        neg = td / "_neg.ll"
        neg.write_text(text.replace(old, "(i64 -1, i64 %t1, i64 %t4, i64 %t8)"),
                       encoding="utf-8")
        want, _info = lomelf.compile_ll(neg.read_text(encoding="utf-8"))
        llrepo = ROOT / "loment" / "build" / "_neg_imm.ll"
        elfrepo = ROOT / "loment" / "build" / "_neg_imm.elf"
        try:
            llrepo.write_bytes(neg.read_bytes())
            rc, err = _mirror_run(mir, "loment/build/_neg_imm.ll", "loment/build/_neg_imm.elf")
            assert rc == 0, f"镜像退出 {rc}: {err[-200:]}"
            nat = elfrepo.read_bytes()
        finally:
            elfrepo.unlink(missing_ok=True)
            llrepo.unlink(missing_ok=True)
        assert nat == want, (f"负立即数: 镜像与参考不同 ({len(nat)}B vs {len(want)}B) —— "
                             f"量操作数跨度时把 `-` 丢了?")
    print("      负立即数: 两份链接器逐字节相同")


@test
def test_lomelf_rebuilds_the_compiler_without_clang():
    """**重建不需要 clang**: lomelf(种子) -> 一个能用的 Loment 编译器, 且它自编译
    `driver.lomt` 的产物**与种子逐字节相同**（定点成立）。整条链没有 clang 参与。

    这是 0.1.4 Alpha2 那条最严口径（"连发布/重建都不需要 C 编译器"）在**参考侧**的落地；
    真正消掉"第一个二进制"的是 docs/167 §5 第 4 条的 genesis。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    drv, _info = lomelf.compile_ll(seed.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = td / "drv.elf"
        elf.write_bytes(drv)
        binn = f"{_T}lomelf_drv.bin"
        outp = td / "drv2.ll"
        errp = td / "drv2.err"
        # **stderr 落文件而不是 /dev/null**: 这条曾经偶发红过一次, 而当时错误被丢掉了,
        # 只留下"退出码 1" —— 一份说不出理由的红对本仓库没有用 (旁边那条重建用例一直是这么做的)。
        script = (
            f"cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
            f"cd {_wsl_path(ROOT)} && {binn} loment/selfhost/driver.lomt "
            f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?"
        )
        r = _sh(script, 900)
        etxt = errp.read_text(encoding="utf-8", errors="replace")[-400:] if errp.exists() else ""
        assert r.stdout.decode().strip() == "0", \
            f"原生编译器退出非零: {r.stdout[:40]!r} stderr={etxt!r}"
        got = outp.read_bytes()
    want = seed.read_bytes()
    assert got == want, f"[定点不成立] 自编译产物 {len(got)}B != 种子 {len(want)}B"
    print(f"      lomelf(种子) -> 编译器 -> 自编译产物 == 种子 ({len(got)}B), 全程无 clang")


@test
def test_lomelf_selfhost_rebuilds_the_compiler():
    """**自举侧也能重建编译器**: 种子 ->(clang 一次)-> stage1 -> 编出镜像 `lomelf.lomt`
    -> 镜像把**种子**编成一个编译器 -> 那个编译器自编译 `driver.lomt` 的产物 == 种子。

    也就是说"重建这套工具链不需要 C 编译器、也不需要解释器"在**自举侧**成立 ——
    clang 只在第一步（种子 -> stage1）出现，那一步的终点是 docs/167 §5 记的 genesis。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 镜像编种子 -> 新的编译器
        seed_c = td / "seed.elf"
        r3 = _sh(
            f"cp {_wsl_path(mir)} {_T}lomelf_rb2.bin && chmod +x {_T}lomelf_rb2.bin && "
            f"cd {_wsl_path(ROOT)} && {_T}lomelf_rb2.bin loment/build/selfhost_driver.ll "
            f"{_wsl_path(seed_c)}", 900, text=True)
        assert r3.returncode == 0, f"镜像编种子失败: {r3.stderr[-300:]}"
        # 用它自编译 driver.lomt, 与种子比
        outp = td / "again.ll"
        r4 = _sh(
            f"cp {_wsl_path(seed_c)} {_T}lomelf_rb3.bin && chmod +x {_T}lomelf_rb3.bin && "
            f"cd {_wsl_path(ROOT)} && {_T}lomelf_rb3.bin loment/selfhost/driver.lomt "
            f"> {_wsl_path(outp)} 2> {_wsl_path(td / 'err.txt')}; echo -n $?", 900,
            text=True)
        errf = td / "err.txt"
        etxt = errf.read_text(encoding="utf-8", errors="replace")[-300:] if errf.exists() else ""
        assert r4.stdout.strip() == "0", f"重建出的编译器退出非零: {r4.stdout[:40]!r} {etxt!r}"
        got = outp.read_bytes()
    want = seed.read_bytes()
    assert got == want, f"[自举侧定点不成立] {len(got)}B != 种子 {len(want)}B"
    print(f"      镜像 -> 编译器 -> 自编译产物 == 种子 ({len(got)}B), 自举侧全程无 clang 无解释器")


@test
def test_lomelf_selfhost_links_foreign_object():
    """**自举链接器也读外部 `.o`** (docs/173 FFI): 镜像 + `--link` 的产物与参考**逐字节相同**,
    而且跑出正确答案。

    这是 `docs/174 §4.1` 那条缺口的判据 —— 在此之前自举侧对 `--link` 是**硬拒**, 也就是说
    **装好的工具链根本链不了 `extern`** (仓库里能跑、包里的不能)。判据落在"镜像能链 +
    产物与参考一致 + 跑对", 不是"IR 里有 declare"。

    两个场景: **单对象** (C ABI 传参) 与 **双对象** (再加"第一个对象的符号名不能被第二个
    对象的读入盖掉")。双对象那条是**必须**的: 名字池的基若放错, 单对象照样绿。

    夹具与 `loment_ffi_test` 共用 (同一份 C 与同一份 Loment 源码), 不抄第二遍 —— 抄一份
    就会漂一份, 而"非交换运算"那条教训正是夹具本身的问题。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    bld = ROOT / "loment" / "build"
    # (标签, Loment 源码, C 源文件, --link 的顺序, 期望退出码)
    cases = [
        ("ffi", ffitest.LOMENT_SOURCE,
         [(ffitest.C_SOURCE, "c")], ["c"], 52),
        ("ffi2", ffitest.LOMENT_TWO_OBJECTS_SOURCE,
         [(ffitest.C_SOURCE, "c"), (ffitest.C2_SOURCE, "d")], ["c", "d"], 75),
        # 跨对象重定位 (docs/173 阶段 2): a.o 里的 PLT32 指向只有 b.o 知道的 c_sub。
        # 这条必须逐字节比 —— 自举侧重定位算错的话, 参考侧照样绿。
        ("ffi3", ffitest.LOMENT_MULTI_SOURCE,
         [(ffitest.C_CALLER_SOURCE, "a"), (ffitest.C_SUB_SOURCE, "b")], ["a", "b"], 22),
        # **收编面**（`docs/219` §6）：这份 C 要 `memcpy`/`memset`/`memcmp`，两个链接器
        # 各自**发出同一段收编面**（参考侧字面字节、自举侧同一串的 hex）—— 这一格逐字节
        # 比的就是那 102 字节。参考侧发了、自举侧忘了发，这里必红。
        ("ffi4", ffitest.LOMENT_CONSCRIPT_SOURCE,
         [(ffitest.C_CONSCRIPT_SOURCE, "cons")], ["cons"], 130),
        # **C2**（`docs/219` §8）：`malloc`/`free` 是**换 ABI 的薄壳**，底下调我们的堆 ——
        # 壳里的 `call` 是**回填**出来的（两侧都算 rel32），所以这一格逐字节比的就是
        # 那两条薄壳有没有算成同一个地址。
        ("ffi5", ffitest.LOMENT_MALLOC_SOURCE,
         [(ffitest.C_MALLOC_SOURCE, "mallocc")], ["mallocc"], 42),
        # **数据节**（`docs/219` §8b 第 1 条）：`.rodata` 里的字符串 + `.data.rel.ro`
        # 里指向它们的表。这一格逐字节比的是**两边把数据节摆在同一个地址上** ——
        # 摆错顺序（bss 与对象数据重叠）、或数据重定位算错，都会当场红。
        ("ffi6", ffitest.LOMENT_DATA_SOURCE,
         [(ffitest.C_DATA_SOURCE, "datac")], ["datac"], 100),
        # **局部符号按对象改名**（2026-10-15，`FujoJTOP/loment#359`）：两个成员各有同名的
        # 局部常量（`.LC0`），`b.o` 排在前面 —— 老病是"先来的为准"，第二个成员的常量会
        # 解析到第一个上（静默指错）。两侧都要各自解对，产物还要逐字节相同。
        ("ffi7", ffitest.LOMENT_LOCAL_COLLIDE,
         [(ffitest.C_LOCAL_A, "a"), (ffitest.C_LOCAL_B, "b")], ["b", "a"], 7),
        # **C 调用点的栈对齐**（同一天同一笔）：C 侧报进入时 `rsp % 16`，SysV 要 **8**。
        # 自举侧也要有自己的对齐序列，而且与参考**逐字节相同**。
        ("ffi8", ffitest.LOMENT_ALIGN, [(ffitest.C_ALIGN, "al")], ["al"], 8),
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        bld.mkdir(parents=True, exist_ok=True)
        for tag, lomt_src, csrcs, order, want_rc in cases:
            objs, clones = {}, []
            for text, stem in csrcs:
                (td / f"{stem}.c").write_text(text, encoding="utf-8", newline="\n")
                assert ffitest.compile_c(_clang(), td / f"{stem}.c", td / f"{stem}.o") == 0, \
                    "C 编译失败"
                objs[stem] = td / f"{stem}.o"
            src = td / f"{tag}.lomt"
            src.write_text(lomt_src, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            want, _info = lomelf.compile_ll(
                ll.read_text(encoding="utf-8"),
                [lomelf.ForeignObject(objs[s]) for s in order],
                allow_ports=True)     # 开发通道：这几份 C 都带端口（docs/222 §4 的门）
            # 外部对象与 IR 都要放成**仓库内相对路径** —— 镜像在 WSL 里按相对路径找。
            llrepo = bld / f"_mirror_{tag}.ll"
            elfrepo = bld / f"_mirror_{tag}.elf"
            rel_objs = [f"loment/build/_mirror_{tag}_{s}.o" for s in order]
            try:
                llrepo.write_bytes(ll.read_bytes())
                for stem, rel in zip(order, rel_objs):
                    p = ROOT / rel
                    p.write_bytes(objs[stem].read_bytes())
                    clones.append(p)
                nat_rc, err = _mirror_run(mir, f"loment/build/_mirror_{tag}.ll",
                                          f"loment/build/_mirror_{tag}.elf", tuple(rel_objs))
                assert nat_rc == 0, f"[{tag}] 镜像 + --link 退出 {nat_rc}: {err[-300:]}"
                nat = elfrepo.read_bytes()
                assert nat == want, f"[{tag}] 镜像的 FFI 产物与参考不同 ({len(nat)}B vs {len(want)}B)"
                rc, out = _run_bin(elfrepo, f"ffi_{tag}", td)
                assert rc == want_rc, f"[{tag}] 跑出的结果不对: rc={rc} (期望 {want_rc}) {out[:120]!r}"
            finally:
                for p in [elfrepo, llrepo, *clones]:
                    p.unlink(missing_ok=True)
    print(f"      镜像 + --link {len(cases)} 例 (单对象/双对象/跨对象重定位/收编面/堆的薄壳/数据节): "
          f"与参考逐字节相同, 退出码 " + " / ".join(str(c[4]) for c in cases))


@test
def test_lomelf_selfhost_links_a_real_archive():
    """**自举链接器读真 `ar` 归档**（`docs/173` 阶段 2，`FujoJTOP/loment#405`）。

    在这之前它一次只驻留**一个** `.o`，对 `.a` 是硬拒 —— 于是"整个 zlib 归档过不过自举
    链接器"这一问答**否**，而包里装的正是这一只。判据沿用 `#359` 那条的**同一份 Loment 源**
    （`loment_ffi_test.ZLIB_STATIC_SRC`，参考那一半在那边过）：真 `libz.a` → 镜像 + `--link`
    → 产物与参考**逐字节相同** → 跑出 `compress → uncompress → 原文`。

    **定点这一半是判据的另一半**：整包收会把 `gz*` 那些成员的 `fopen`/`printf` 一并拖进来，
    端口门于是把整个库拒掉 —— 所以这一格还要求"挑出来的成员**少于**归档里的成员数"
    （见 `test_a_real_static_library_round_trips` 里参考侧的同一条断言）。
    **证伪**：比对方向写反的那一份必须报 40。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    bld = ROOT / "loment" / "build"
    bld.mkdir(parents=True, exist_ok=True)
    ark = ffitest._system_libz(bld / "_mirror_libz.a")
    if ark is None:
        print("      SKIP: 这台机器上没有 libz.a（要 zlib1g-dev）—— 这一格测不了")
        return
    rel_ark = "loment/build/_mirror_libz.a"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        try:
            for tag, src_text, want_rc in (("zlib", ffitest.ZLIB_STATIC_SRC, 0),
                                           ("zlibr", ffitest.ZLIB_STATIC_FALSIFIER_SRC, 40)):
                src = td / f"{tag}.lomt"
                src.write_text(src_text, encoding="utf-8", newline="\n")
                ll = _ref_ir(src, td)
                want, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"),
                                                [lomelf.load_foreign(ark)], allow_ports=True)
                llrepo = bld / f"_mirror_{tag}.ll"
                elfrepo = bld / f"_mirror_{tag}.elf"
                try:
                    llrepo.write_bytes(ll.read_bytes())
                    rc, err = _mirror_run(mir, f"loment/build/_mirror_{tag}.ll",
                                          f"loment/build/_mirror_{tag}.elf", (rel_ark,))
                    assert rc == 0, f"[{tag}] 镜像 + --link 退出 {rc}: {err[-300:]}"
                    nat = elfrepo.read_bytes()
                    assert nat == want, \
                        f"[{tag}] 镜像的归档产物与参考不同 ({len(nat)}B vs {len(want)}B)"
                    got, out = _run_bin(elfrepo, f"zlib_{tag}", td)
                    assert got == want_rc, \
                        f"[{tag}] 跑出的结果不对: rc={got} (期望 {want_rc}) {out[:120]!r}"
                finally:
                    elfrepo.unlink(missing_ok=True)
                    llrepo.unlink(missing_ok=True)
        finally:
            ark.unlink(missing_ok=True)
    print("      镜像 + 真 libz.a: 产物与参考逐字节相同, compress/uncompress 回原文 (rc=0)"
          "；证伪件报 40")


@test
def test_a_corrupt_shared_object_is_a_diagnostic_not_a_traceback():
    """改坏**一个字段**的 `.so` 要报 `Unsupported`，不是 Python 栈，也不是转圈（`#592`、`#603`）。

    四个探针与 issue 里那批同一形状：`e_shoff` 越界、`e_shnum` 过大、文件截断一半，
    外加一个 `.gnu.version_d` 的 `vd_next` **自环** —— 最后那一刀在没有界的那一版里是
    死循环（100% CPU、一句话都不说），所以这条判据同时钉住"不会转圈"。

    `Unsupported` 是链接器对"这个输入我处理不了"的唯一口径（`ForeignObject` 那边一直
    如此，见 `#167`）。改成它意味着：**用户拿到的是文件名 + 哪张表越界**，而不是
    `struct.error: unpack_from requires a buffer of at least …`。
    """
    src = next((Path(c) for c in ffitest.LIBZ_SO_CANDIDATES if Path(c).is_file()), None)
    if src is None:
        print("      SKIP: 这台机器上没有 libz.so（要 zlib1g）")
        return
    good = src.read_bytes()
    if len(good) < 64 or good[:4] != b"\x7fELF":
        print("      SKIP: libz.so 不是 ELF64")
        return

    def with_u(off: int, fmt: str, val: int) -> bytes:
        b = bytearray(good)
        struct.pack_into(fmt, b, off, val)
        return bytes(b)

    cases = {
        "e_shoff 越界": with_u(40, "<Q", 0xFFFFFF0),
        "e_shnum 过大": with_u(60, "<H", 60000),
        "文件截断一半": good[: len(good) // 2],
    }

    # 版本表自环：自己读一遍节头找到 `.gnu.version_d`，把第一条的 `vd_next` 写成 0
    # （`vo += 0` ⇒ 原地打转）。这一段用独立的最小读法，不借被测代码 —— 否则一个读错
    # 会把夹具也一起读错，两边一致地错。
    e_shoff, = struct.unpack_from("<Q", good, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", good, 58)
    secs = []
    for k in range(e_shnum):
        _no, _ty, _fl, _ad, off, size, _li, _in, _a2, _en = \
            struct.unpack_from("<IIQQQQIIQQ", good, e_shoff + k * e_shentsize)
        secs.append((_no, off, size))
    sbase = secs[e_shstrndx][1]
    verd = None
    for nameoff, off, size in secs:
        end = good.index(b"\x00", sbase + nameoff)
        if good[sbase + nameoff:end] == b".gnu.version_d":
            verd = (off, size)
            break
    # ⚠ `vd_next == 0` **不是**自环：那条链以 0 结尾，它是"到此为止"。真正的危险是
    # 两种走法：一步跳出这一节（大 nxt），或者一步只走 1 字节、永远踩不到 0。
    if verd is not None:
        b = bytearray(good)
        struct.pack_into("<I", b, verd[0] + 16, 0x00FFFFFF)   # vd_next 跳出这一节
        cases["版本定义链跳出这一节"] = bytes(b)
        b2 = bytearray(good)
        struct.pack_into("<I", b2, verd[0] + 16, 1)           # 每一步只走 1 字节
        cases["版本定义链走不完"] = bytes(b2)

    with tempfile.TemporaryDirectory(prefix="so-mut-") as td:
        for name, data in cases.items():
            p = Path(td) / "m.so"
            p.write_bytes(data)
            try:
                lomelf.SharedObject(p)
            except lomelf.Unsupported as e:
                assert "m.so" in str(e), f"{name}: 报错里没有文件名: {e}"
            except Exception as e:                       # noqa: BLE001
                raise AssertionError(
                    f"{name}: 抛了 {type(e).__name__}，不是 Unsupported: {e}")
            else:
                raise AssertionError(f"{name}: 改坏的 .so 被当成好的收了")
    print(f"      {len(cases)} 种改坏的 .so 都报 Unsupported"
          f"（含版本表跳出/走不完，不再转圈）")

    # **镜像那一侧**（`#593` / `#594`）：包里跑的就是它。它的 `so_load` 把文件长度 `n`
    # 收进形参、一次也没用过 —— 176 字节的坏文件能读到 32 MiB 外（SIGSEGV），或者读到
    # 上一个 `--link` 文件留下的字节（诊断指错方向）；DT_STRTAB 缺了还会把 ELF 头那几个
    # 字节当成 SONAME 写进 `DT_NEEDED`。两半都要**退出非零并说一句话**。
    if not (_clang() and _wsl()):
        print("      SKIP 镜像侧: 无 clang/WSL")
        return
    mir = _mirror()
    bld = ROOT / "loment" / "build"
    bld.mkdir(parents=True, exist_ok=True)
    llrepo = bld / "_mirror_so_mut.ll"
    outrepo = bld / "_mirror_so_mut.elf"
    sorepo = bld / "_mirror_so_mut.so"
    with tempfile.TemporaryDirectory(prefix="so-mut-mir-") as tdm:
        src = Path(tdm) / "m.lomt"
        src.write_text("module m\n\nextern fn compress(a: ptr, b: u32) -> i32;\n\n"
                       "fn _start() -> u32 {\n    return 0;\n}\n",
                       encoding="utf-8", newline="\n")
        llrepo.write_bytes(_ref_ir(src, Path(tdm)).read_bytes())
    # 版本定义链那两刀是**参考侧独有**的：镜像是按**节类型**找 `.dynsym` / `.gnu.version`，
    # 从来不读 `.gnu.version_d`（它不需要"版本名的说法"，那份只用于参考的错误消息）。
    # 它压根不碰那一段，所以改那一段它没反应——**留着会是一条测不到东西的断言**。
    mir_cases = {k: v for k, v in cases.items() if "版本定义链" not in k}
    try:
        for name, data in mir_cases.items():
            sorepo.write_bytes(data)
            rc, err = _mirror_run(mir, "loment/build/_mirror_so_mut.ll",
                                  "loment/build/_mirror_so_mut.elf",
                                  ("loment/build/_mirror_so_mut.so",))
            assert rc != 0, f"[镜像] {name}: 改坏的 .so 被收下了 (rc=0)"
            assert "共享对象" in err, (
                f"[镜像] {name}: 没说出是共享对象（信号？）rc={rc} {err[-200:]!r}")
    finally:
        for f in (llrepo, outrepo, sorepo):
            f.unlink(missing_ok=True)
    print(f"      镜像侧：{len(mir_cases)} 种改坏的 .so 都退出非零并点名")


@test
def test_lomelf_selfhost_links_a_real_shared_object():
    """**自举链接器链真 `.so`**（`docs/183` §4 的 V2，`#360`）。

    与参考那一半（`loment_ffi_test.test_a_real_shared_library_is_bound_at_run_time`）
    **同一份 Loment 源**：真 `libz.so` → 镜像 + `--link` → 产物与参考**逐字节相同** →
    跑出 `compress → uncompress → 原文`。`.so` 的字节一个也不进产物 —— 这条量的是
    "同一个答案，两种实现"。

    **端口门**也在这一格里量**否定**的那一半：同一个 `.so`、不给 `--allow-ports`，
    镜像必须拒（发行通道里这条路是关着的）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    bld = ROOT / "loment" / "build"
    bld.mkdir(parents=True, exist_ok=True)
    so = ffitest._system_so(bld / "_mirror_libz.so.1", ffitest.LIBZ_SO_CANDIDATES)
    if so is None:
        print("      SKIP: 这台机器上没有 libz.so（要 zlib1g）—— 这一格测不了")
        return
    rel_so = "loment/build/_mirror_libz.so.1"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        try:
            for tag, src_text, want_rc in (("zso", ffitest.ZLIB_STATIC_SRC, 0),
                                           ("zsor", ffitest.ZLIB_STATIC_FALSIFIER_SRC, 40)):
                src = td / f"{tag}.lomt"
                src.write_text(src_text, encoding="utf-8", newline="\n")
                ll = _ref_ir(src, td)
                want, info = lomelf.compile_ll(ll.read_text(encoding="utf-8"),
                                               [lomelf.load_foreign(so)], allow_ports=True)
                assert info["shared"] == ["libz.so.1"], info
                llrepo = bld / f"_mirror_{tag}.ll"
                elfrepo = bld / f"_mirror_{tag}.elf"
                try:
                    llrepo.write_bytes(ll.read_bytes())
                    rc, err = _mirror_run(mir, f"loment/build/_mirror_{tag}.ll",
                                          f"loment/build/_mirror_{tag}.elf", (rel_so,))
                    assert rc == 0, f"[{tag}] 镜像 + --link .so 退出 {rc}: {err[-300:]}"
                    nat = elfrepo.read_bytes()
                    assert nat == want, (
                        f"[{tag}] 镜像的动态产物与参考不同 ({len(nat)}B vs {len(want)}B)")
                    got, out = _run_bin(elfrepo, f"zso_{tag}", td)
                    assert got == want_rc, (
                        f"[{tag}] 跑出的结果不对: rc={got}（期望 {want_rc}）{out[:120]!r}")
                finally:
                    elfrepo.unlink(missing_ok=True)
                    if tag == "zsor":
                        llrepo.unlink(missing_ok=True)
            # 端口门（否定那一半）：同一份 `.ll` + 同一个 `.so`，不给开关必须拒。
            rc2, err2 = _mirror_run(mir, "loment/build/_mirror_zso.ll",
                                    "loment/build/_mirror_zso_gate.elf", (rel_so,),
                                    ports=False)
            assert rc2 != 0, "发行通道居然收下了共享对象"
            assert "--allow-ports" in err2, f"拒的理由没说到开关: {err2[-300:]!r}"
        finally:
            (bld / "_mirror_zso.ll").unlink(missing_ok=True)
            (bld / "_mirror_zso_gate.elf").unlink(missing_ok=True)
            so.unlink(missing_ok=True)
    print("      镜像 + 真 libz.so: 产物与参考逐字节相同, compress/uncompress 回原文 (rc=0)"
          "；证伪件报 40；不给 --allow-ports 就拒")


@test
def test_the_mirror_linker_refuses_ports_without_the_dev_flag():
    """自举链接器的**端口门**（`docs/222` §4）：同一份带端口的外源对象，不给
    `--allow-ports` 就**必须拒**。

    参考侧那条在 `loment_ffi_test::test_the_port_gate_refuses_by_default_...`；这一条是它
    的孪生。**为什么非要有**：包里装的是**自举**那一只链接器 —— 只有它也拒，
    "0.1.4.5 里 libc 不可调用"才是真的。参考拒、自举收，是最坏的那种分叉。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    td = Path(tempfile.mkdtemp(prefix="mf_"))
    bld = ROOT / "loment" / "build"
    bld.mkdir(parents=True, exist_ok=True)
    tag = "portgate"
    src = td / f"{tag}.lomt"
    src.write_text(ffitest.LOMENT_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
    ll = _ref_ir(src, td)
    (td / "cons.c").write_text(ffitest.C_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
    assert ffitest.compile_c(_clang(), td / "cons.c", td / "cons.o") == 0
    llrepo = bld / f"_mirror_{tag}.ll"
    elfrepo = bld / f"_mirror_{tag}.elf"
    objrepo = ROOT / f"loment/build/_mirror_{tag}_cons.o"
    try:
        llrepo.write_bytes(ll.read_bytes())
        objrepo.write_bytes((td / "cons.o").read_bytes())
        rc, err = _mirror_run(mir, f"loment/build/_mirror_{tag}.ll",
                              f"loment/build/_mirror_{tag}.elf",
                              (f"loment/build/_mirror_{tag}_cons.o",), ports=False)
        assert rc != 0, (
            "自举链接器**没有**端口门 —— 带未定义符号的对象照收，于是包里的 "
            "`loment build --link` 又把 libc 放进来了（`docs/222` §4）")
        print("      自举链接器：不给 --allow-ports 就拒带端口的对象")
    finally:
        for p in (llrepo, elfrepo, objrepo):
            p.unlink(missing_ok=True)


#: `#366` 的语料：刻 N 个块（**一个 `free` 都不写**）→ 用大块把累计请求推过触发阈值、逼出
#: 两次收集 → 再分配 N 个**同尺寸**的块，数有多少落回原来那片区域。
#:
#: 两处刻意的写法（都与 `_SWEEP_PRECISION_SRC` 同一条道理）：
#:   * 尺寸走 `const` **名字**（`alloc(SZ)`）而不是字面量、且每次分配都 `esc = q` 逃逸一次
#:     —— 否则 L0 会把 `alloc(<字面量>)` 提到栈上、L2 会把循环体里那份回卷，测到的就
#:     不是清扫了；
#:   * 逼出**两次**收集：`#366` 丢块发生在**第二次**（第一次时链表是空的，丢了也看不出来）。
#: 触发用的请求是 `BIG`（208 字节），**装不进**刻出来的 80 字节块 —— 于是那段空闲区
#: 在两次收集之间不会被顺手动到，"它还在不在"这个问题才是干净的。
_FREE_LIST_SRC = (
    "module freelist\n%s\nchoose runtime\n\n"
    "const N: u64 = 200;\n"
    "const SZ: u32 = 64;\n"
    "const STRIDE: u64 = 80;\n"
    "const BIG: u32 = 200;\n\n"
    "fn carve_k(k: u64) -> u64 {\n"
    "    let first: u64 = 0;\n"
    "    let last: ptr = alloc(SZ);\n"
    "    store8(last, 0, 42);\n"
    "    let i: u64 = 0;\n"
    "    while i < k {\n"
    "        let p: ptr = alloc(SZ);\n"
    "        store8(p, 0, 42);\n"
    "        if i == 0 {\n"
    "            first = p as u64;\n"
    "        }\n"
    "        last = p;\n"
    "        i = i + 1;\n"
    "    }\n"
    "    return first;\n"
    "}\n\n"
    "fn churn(m: u64) -> u64 {\n"
    "    let esc: ptr = alloc(BIG);\n"
    "    store8(esc, 0, 7);\n"
    "    let g: u64 = 0;\n"
    "    while g < m {\n"
    "        let t: ptr = alloc(BIG);\n"
    "        store8(t, 0, 7);\n"
    "        esc = t;\n"
    "        g = g + 1;\n"
    "    }\n"
    "    return esc as u64;\n"
    "}\n\n"
    "fn _start() {\n"
    "    let first: u64 = carve_k(N);\n"
    "    let _k1: u64 = churn(200);\n"
    "    let _k2: u64 = churn(200);\n"
    "    let hits: u64 = 0;\n"
    "    let esc: ptr = alloc(SZ);\n"
    "    store8(esc, 0, 7);\n"
    "    let j: u64 = 0;\n"
    "    while j < N {\n"
    "        let p: ptr = alloc(SZ);\n"
    "        let a: u64 = p as u64;\n"
    "        if a >= first && a < first + N * STRIDE {\n"
    "            hits = hits + 1;\n"
    "        }\n"
    "        store8(p, 0, 7);\n"
    "        esc = p;\n"
    "        j = j + 1;\n"
    "    }\n"
    "    if hits >= N - 4 {\n        syscall4(60, 7, 0, 0);\n    }\n"
    "    syscall4(60, 3, 0, 0);\n}\n")


@test
def test_a_collection_keeps_the_free_list():
    """一次收集**不许**把当前空闲块丢掉（`#366`）—— 两档各一只，退出码 7 = 拿回了那片区域。

    **Why**：`__loment_collect` / `__loment_minor` 原先在清扫前 `store i32 0, ptr
    @__loment_freehead`，理由是「本来就在链上的块会被再 free 一次（双重插入，链就毁了）」。
    那条理由站在 `#164` **之前** —— 当时 `free` 不验所有权，谁调都往里挂。`#164` 之后
    `__loment_free` 只认**借出中**的块（`next == -1`），已在链上的块**惰性返回**：于是
    重置不再防住什么，却把**当前所有空闲块**从分配器手里**永久**丢掉 —— 清扫撞上它们时
    那道守卫拒绝重挂，而清扫**不会**把它们放回去。字节还在 arena 里、还在前沿之下，
    `alloc` 再也摸不到，直到 `__loment_abort`。

    **How to apply**：`_FREE_LIST_SRC` 两只同源，只差 `choose`；两边都要 `rc == 7`。
    实测：**未修**的树上一档拿回 0/200、另一档也是 0/200（语料里的 `hits >= N - 4` 直接
    不成立）；修完 199/200。**这条判据的证伪方向是"少拿回"**，所以阈值卡在 `N - 4`
    而不是"恰好 N"（那个 1 的差是前沿回卷那一下，与丢块无关）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        for tier in ("gc_auto", "gc_auto_alpha"):
            src = td / f"{tier}.lomt"
            src.write_text(_FREE_LIST_SRC % f"choose {tier}", encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tier}.native"
            nat.write_bytes(blob)
            got[tier] = _run_bin(nat, f"freelist_{tier}", td, timeout=60)[0]
    bad = [f"{k}={v}" for k, v in got.items() if v != 7]
    assert not bad, (
        f"一次收集把空闲块丢掉了（{', '.join(bad)}；期望都是 7）。成因：清扫前清空了 "
        "`@__loment_freehead`，而清扫**没法**把已在链上的块重新挂回去 —— `__loment_free` "
        "只认借出中的块（`#164` 的守卫），会在那道守卫上惰性返回")
    print(f"      gc_auto={got['gc_auto']} gc_auto_alpha={got['gc_auto_alpha']}"
          " —— 两档收集之后都还拿得回那片空闲区")


def _time_bin(elf: Path, name: str, td: Path, runs: int = 5, timeout: int = 120) -> int:
    """**一次 shell 里跑 `runs` 遍、取最小**，回微秒。

    **为什么不是每遍起一次**（`docs/210` §7 实录过、`docs/226` 也记着）：一次 `wsl` 启动就够
    把被测的那点时间淹掉 —— 第一版每遍起一次，量出来 303/284 ms，而程序本身只有 15 ms；
    同一支二进制两次量到 275/481 ms 也是这么来的。最小值的意义是"这一遍没有被打断"。
    """
    binn = f"{_T}lomelf_{name}.bin"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"best=999999999999; for i in $(seq {runs}); do "
              f"s=$(date +%s%N); {binn} >/dev/null 2>&1; e=$(date +%s%N); "
              f"d=$(( (e - s) / 1000 )); "
              f"if [ $d -lt $best ]; then best=$d; fi; done; echo -n $best")
    r = _sh(script, timeout, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


@test
def test_the_generational_barrier_has_a_measured_cost():
    """**分代写屏障的墙钟代价**（`FujoJTOP/loment#365`，`docs/210` §2.7/§7）—— 有上界的测量。

    **Why**：`docs/210` §2.7 说写屏障是分代那一档的价钱（**每个 `store8` 站点一次调用**），
    §7 一直写着"墙钟没量"：唯一一次尝试被机器噪声淹掉（同一支 `gc_auto` 二进制在同一时段量到
    275 ms 与 481 ms —— 1.75 倍，而要看的东西比它小）。确定性的那一半已经有了：写重的负载上
    alpha 档的 **IR 是 auto 档的 1.37 倍**，而这个比值由站点数钉住、不会漂。缺的是墙钟。

    **How to apply**：同一份**写重、几乎不分配**的工作量（每圈 8 个 `store8` 打在同一块上，
    循环里一次 `alloc` 都没有 ⇒ 两个档的差别**只有**屏障那一次调用，没有收集器掺进来），
    编两遍 —— `choose gc_auto`（**不发**屏障）与 `choose gc_auto_alpha`（每站发一次）——
    各自**一次 shell 里跑 5 遍取最小**（见 `_time_bin`：一次 `wsl` 启动就能淹掉被测时间）。
    三个断言，而**确定性的那个才是这条判据的意图**：

      * 两只**都跑完**（rc = 7）—— 否则"更快"可能只是崩得早；
      * `gc_auto` 的 IR 里**一条屏障都没有**（那一档不发）；
      * `gc_auto_alpha` 的 IR 里屏障函数定义 **1** 次、**调用次数 == 源里 `store8` 站点数**
        （每站一次）。这三条与机器快慢无关，抓的正是"形状变了"：每字一次会让调用数变成
        8 倍，跟着堆大小走则会随负载漂。

    **墙钟只报不判**（`FujoJTOP/loment#442`）：同一个比值在判据作者那台是 3.2×、本机 4.13×、
    CI runner 5.4×（两次相差 0.5%，不是抖动），而界原先定在 5.0 —— **分开的是机器，不是形状**：
    它在 CI 上恒红、在别的机器上恒绿，与那棵树对不对无关。要让它重新变成门禁，得让它跟
    **同一次运行里、紧接着的对照**比，而不是跟一个绝对比值比。比值仍打印出来，供 `docs/210` §7 记数。
    """
    if not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module barriercost\n%s\nchoose runtime\n\n"
            "fn mk(n: u32) -> ptr {\n"          # 指针**逃逸**：不这么写它会落到 L0/L1，
            "    return alloc(n);\n}\n\n"        # 那块内存根本不上堆，屏障也就不会发
            "fn _start() {\n"
            "    let p: ptr = mk(4096);\n"
            "    let i: u32 = 0;\n"
            "    while i < 1000000 {\n"
            "        store8(p, 0, 1);\n"
            "        store8(p, 1, 2);\n"
            "        store8(p, 2, 3);\n"
            "        store8(p, 3, 4);\n"
            "        store8(p, 4, 5);\n"
            "        store8(p, 5, 6);\n"
            "        store8(p, 6, 7);\n"
            "        store8(p, 7, 8);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    syscall4(60, 7, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        irs: dict[str, str] = {}
        for tag, tier in (("auto", "choose gc_auto"), ("alpha", "choose gc_auto_alpha")):
            src = td / f"{tag}.lomt"
            src.write_text(body % tier, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            irs[tag] = ll.read_text(encoding="utf-8")
            blob, _info = lomelf.compile_ll(irs[tag])
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            rc, _out = _run_bin(nat, f"barrier_rc_{tag}", td, timeout=60)
            assert rc == 7, f"`{tier}` 那一只没跑完：rc={rc}（比时间之前先确认它真的跑完了）"
            got[tag] = _time_bin(nat, f"barrier_{tag}", td, runs=5)

    # **形状**：数 IR 就够了，与机器快慢无关（`#442` —— 原先唯一的断言是个绝对墙钟比值，
    # 而它在本机 4.13×、CI runner 5.4×、作者那台 3.2×，界 5.0 落在三者的散布里）。
    sites = body.count("store8(")
    assert "__loment_wb" not in irs["auto"], (
        "`choose gc_auto` 的 IR 里出现了写屏障 —— 那一档**不发**屏障，这是形状变了")
    n_def = irs["alpha"].count("define internal void @__loment_wb")
    assert n_def == 1, f"`choose gc_auto_alpha` 里屏障函数定义了 {n_def} 次（应为 1 次）"
    n_call = irs["alpha"].count("call void @__loment_wb")
    assert n_call == sites, (
        f"屏障调用 {n_call} 处，而源里 `store8` 站点 {sites} 个 —— 屏障应当**每站一次**"
        "（每字一次会让它变成 8 倍，跟着堆大小走则会随负载漂）")

    a, b = got["auto"], got["alpha"]
    assert a > 0 and b > 0, f"计时没拿到数：auto={a} alpha={b}（微秒）"
    ratio = b / a
    print(f"      写屏障：auto {a / 1000:.1f} ms / alpha {b / 1000:.1f} ms = {ratio:.2f}×"
          f"（auto 的 IR {len(irs['auto'].splitlines())} 行、无屏障；"
          f"alpha {len(irs['alpha'].splitlines())} 行、{n_call} 处调用 / {sites} 个站点）"
          "（各一次 shell 里 5 遍取最小；比值只报不判，见 docstring）")


@test
def test_the_runtime_guard_does_not_need_a_windows_binary():
    """**这一族的闸门是"能不能跑 Linux ELF"，不是"有没有 `wsl` 这个二进制"**（`FujoJTOP/loment#371`）。

    **Why**：`_wsl()` 原先只是 `shutil.which("wsl")`。必需检查跑在 ubuntu runner 上，而那儿
    **没有**这个二进制（它不是 apt 包，镜像里也没有垫片）—— 于是本文件里**每一条要跑产物的
    判据**都在 `SKIP: 无 clang/WSL` 那条早退路上返回，而早退在运行器里是一次**正常返回**
    ⇒ 记成 PASS ⇒ 门禁报 `31/31 通过`。也就是说：**改收集器、改分配器、改两个链接器的产物形状，
    都能在一条运行期判据都没跑过的情况下过掉必需检查**。跳过的那一族不是抽样，它就是这一族。

    （`#374` 让每一行带上 `N SKIP`、`#382` 让 Linux 上原生跑 —— 这一条钉的是**别再把闸门写回
    "某个二进制在不在"**：机制改回去了，上面那两件事的账就又会静默。）

    **How to apply**：把 `wsl` 从 `shutil.which` 里藏掉（正是 issue 里那份复现），再看 `_wsl()`：
      * Linux 上**必须仍然为真** —— 产物本来就是 Linux ELF，该原生跑；
      * 其它平台上**必须为假** —— 藏掉之后本机确实跑不了，那是实话。
    两个方向都钉住：只钉 Linux 那一半的话，一个恒真的 `_wsl()` 也能过。
    这一条**自己不 SKIP** —— 它测的是"闸门怎么判"，两个平台上都判得了。
    """
    real = shutil.which
    try:
        shutil.which = lambda n: None if n == "wsl" else real(n)
        got = _wsl()
    finally:
        shutil.which = real
    if sys.platform.startswith("linux"):
        assert got is True, (
            "Linux 上把 `wsl` 藏起来之后 `_wsl()` 应当仍为真 —— 否则 runner 上这一族全被"
            "跳过、而门禁照样报满（`FujoJTOP/loment#371`）")
    else:
        assert got is False, (
            f"非 Linux 上把 `wsl` 藏起来之后 `_wsl()` 应当是假，得到 {got!r} —— 一个恒真的"
            "闸门会把'跑不了'变成'假装跑过了'，而那正是 `FujoJTOP/loment#371` 的形状")


def main() -> int:
    return loment_suite.run(TESTS, "loment_elf_test")


if __name__ == "__main__":
    sys.exit(main())
