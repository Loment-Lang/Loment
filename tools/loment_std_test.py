#!/usr/bin/env python3
# loment_std_test.py — std 核的**行为**判据 (docs/180)
#
# 为什么要单开一条: `loment_lib_test` 测的是**库系统** (身份/去重/物化/多版本共存),
# `loment_json_test` 测的是 json 一个模块。**没有一个判据在问"这些库里那些函数算得对不对"。**
# 库里最容易被"看起来有"蒙混过去的就是这个 —— 函数签名在、检查过、编得出, 而算错了。
#
# 做法照 `loment_json_test`: 探针程序调库, stdout 必须与 **Python 算出来的**逐字节相同。
# 用 Python 当对照物而不是硬编码期望值, 是因为硬编码的那份是我们自己写的,
# 自己写的东西两处一起错就看不出来了 (docs/176 §7.3 那条的同一种病)。
#
# 用法: python tools/loment_std_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_json_test as J                                          # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STD_PKG = ROOT / "lompi" / "store" / "std" / "0.1.0"
IS_WIN = sys.platform == "win32"


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 探针里的 `chk` 每个都打一行 `<标签> <值>`。Python 侧按同一顺序算一遍。
PROBE = r'''
module stdprobe

// `runtime` 维从 `docs/227` 起是一条**保证**：探针用了 `alloc`，根单元就得声明它。
// 仓内那个孪生 (`loment/tools/lomstdcheck.lomt:20`) 早就有一行，这个**生成的**探针
// 漏了 —— 于是 `main` 上这条判据一直是红的（不是这一笔带出来的，是 #212 漏改）。
choose runtime

use "loment/lib/mem.lomt"
use "loment/lib/num.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

// `sc` 是 say **自己的**暂存 —— 不能拿调用方的缓冲: `say(b, "x=", num_to_dec(b, v))`
// 里 say 会再写一次那个缓冲, 于是"打印某个缓冲的内容"就变成打印被覆写后的东西。
// 第一版就是这么错的 (探针自己的 bug, 与库无关) —— 记在这里免得下次再踩。
fn say(sc: ptr, tag: str, v: u32) {
    let _a: i64 = wr(1, str_ptr(tag), str_len(tag));
    let _b: u32 = num_to_dec(sc, v);
    let _c: i64 = wr(1, sc, _b);
    let _d: i64 = wr(1, str_ptr("
"), 1);
}

fn _start() {
    let sc: ptr = alloc(32);
    // ---- mem: 填 / 拷 / 比 / 找 / 重叠移动
    let a: ptr = alloc(32);
    let b: ptr = alloc(32);
    mem_fill(a, 8, 65 as u8);
    mem_copy(b, a, 8);
    say(sc, "eq=", mem_eq(a, b, 8) as u32);
    store8(b, 3, 66 as u8);
    say(sc, "ne=", mem_eq(a, b, 8) as u32);
    say(sc, "find=", mem_find(b, 8, 66 as u8) as u32);
    say(sc, "nofind=", mem_find(b, 8, 90 as u8) as u32);
    mem_fill(b, 8, 0 as u8);
    say(sc, "zero=", load8(b, 7));
    let c: ptr = alloc(16);
    store8(c, 0, 1 as u8);
    store8(c, 1, 2 as u8);
    store8(c, 2, 3 as u8);
    store8(c, 3, 4 as u8);
    mem_move(ptr_add(c, 1), c, 4);
    say(sc, "mv0=", load8(c, 1));
    say(sc, "mv3=", load8(c, 4));
    say(sc, "ne=", mem_find_ne(a, 8, 65 as u8) as u32);
    // 小端 32 位读写: 存进去再读回来, 且按字节看
    let w: ptr = alloc(8);
    mem_store32(w, 0, 305419896);
    say(sc, "l32=", mem_load32(w, 0));
    say(sc, "b0=", load8(w, 0));
    say(sc, "b3=", load8(w, 3));
    // ---- num: 十进制
    let d: ptr = alloc(32);
    say(sc, "dec0=", num_to_dec(d, 0));
    say(sc, "dec1=", num_dec_len(1234567));
    let n1: u32 = num_to_dec(d, 1234567);
    let _e: i64 = wr(1, d, n1);
    let _f: i64 = wr(1, str_ptr("
"), 1);
    let n2: u32 = num_to_dec(d, 4294967295);
    let _g: i64 = wr(1, d, n2);
    let _h: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "parse=", num_parse_dec(str_ptr("370x9"), 5));
    say(sc, "span=", num_dec_span(str_ptr("370x9"), 5));
    say(sc, "spannone=", num_dec_span(str_ptr("x9"), 2));
    // ---- num: 十六进制
    let h: ptr = alloc(16);
    say(sc, "hexn=", num_to_hex(h, 48879, 4));
    let _i: i64 = wr(1, h, 4);
    let _j: i64 = wr(1, str_ptr("
"), 1);
    let _k: u32 = num_to_hex(h, 10, 4);
    let _l: i64 = wr(1, h, 4);
    let _m: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "hexw=", num_to_hex(h, 305419896, 2));
    let _n: i64 = wr(1, h, 2);
    let _o: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "phex=", num_parse_hex(str_ptr("beef!"), 5));
    say(sc, "hspan=", num_hex_span(str_ptr("beef!"), 5));
    say(sc, "hbad=", num_hex_val(33 as u8) as u32);
    say(sc, "hup=", num_parse_hex(str_ptr("BEEF"), 4));
    syscall4(60, 0, 0, 0);
}
'''


def _expected() -> str:
    """**用 Python 算**同一串结果 —— 不硬编码。顺序与探针里的 `say` 一一对应。

    硬编码期望值的毛病: 那份期望也是我们自己写的, 两处一起错就看不出来。
    """
    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    a = bytes(b"A" * 8)
    b = bytearray(a)
    say("eq=", 1)
    b[3] = 66
    say("ne=", 0)
    say("find=", 3)
    say("nofind=", 0xFFFFFFFF)          # -1 as u32
    b = bytearray(8)
    say("zero=", 0)
    c = bytearray([1, 2, 3, 4])
    c[1:5] = c[0:4]                     # mem_move(ptr_add(c,1), c, 4) —— 从尾到头
    say("mv0=", c[1])
    say("mv3=", c[4])
    say("ne=", 0xFFFFFFFF)              # 全是 'A' -> -1 as u32
    w = (305419896).to_bytes(4, "little")
    say("l32=", 305419896)
    say("b0=", w[0])
    say("b3=", w[3])
    d = bytearray(32)
    d[0:1] = b"0"
    say("dec0=", 1)
    say("dec1=", 7)
    out.write("1234567\n")
    out.write("4294967295\n")
    say("parse=", 370)
    say("span=", 3)
    say("spannone=", 0)
    say("hexn=", 4)
    out.write("beef\n")
    out.write("000a\n")
    say("hexw=", 2)
    out.write(f"{305419896 % 256:02x}\n")
    say("phex=", 0xBEEF)
    say("hspan=", 4)
    say("hbad=", 0xFFFFFFFF)
    say("hup=", 0xBEEF)
    return out.getvalue()


# ---------------------------------------------------------------- store/std 的边界 (parse / bigint)

#: `parse_i64` / `parse_i64_ok` 的用例（issue #720）。最后三个是"前缀"与"空"的语义。
_PARSE_I64_CASES = [
    "9223372036854775807",
    "9223372036854775808",
    "18446744073709551615",
    "-9223372036854775808",
    "-9223372036854775809",
    "-42",
    "+7",
    "12ab",
    "",
]


def _py_parse_i64(s: str) -> tuple[bool, int]:
    """Python 侧独立算的期望：**有符号**范围 + 饱和；前缀语义与库一致。"""
    import re as _re
    neg = False
    body = s
    if body[:1] in ("+", "-"):
        neg = body[0] == "-"
        body = body[1:]
    digits = _re.match(r"[0-9]*", body).group(0)
    v = int(digits) if digits else 0
    if neg:
        v = -v
    ok = bool(digits) and len(digits) == len(body) and -(2 ** 63) <= v <= 2 ** 63 - 1
    sat = max(-(2 ** 63), min(2 ** 63 - 1, v))
    return ok, sat


def _edge_probe() -> str:
    body = []
    for i, s in enumerate(_PARSE_I64_CASES):
        body.append(f'    let s{i}: str = "{s}";')
        body.append(f'    ep_ok("p{i}_ok", parse_i64_ok(str_ptr(s{i}), 0, str_len(s{i})));')
        body.append(f'    ep_line_i("p{i}_v", parse_i64(str_ptr(s{i}), 0, str_len(s{i})));')
    return """
module std_edge_probe
choose runtime
use parse
use bigint
use bigdec

fn ep_w(s: str) -> u32 {
    return syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64) as u32;
}

fn ep_dec(v: u64) -> u32 {
    let b: ptr = alloc(32);
    let n: u32 = 0;
    let x: u64 = v;
    if x == 0 {
        store8(b, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(b, n, (48 + (x % 10) as u32) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(b, i) as u8;
        store8(b, i, load8(b, n - 1 - i) as u8);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }
    return syscall4(1, 1, b as u64, n as u64) as u32;
}

fn ep_line(tag: str, v: u64) -> u32 {
    let _a: u32 = ep_w(tag);
    let _b: u32 = ep_w("|");
    let _c: u32 = ep_dec(v);
    return ep_w("\\n");
}

fn ep_flag(b: bool) -> u64 {
    if b {
        return 1;
    }
    return 0;
}

fn ep_ok(tag: str, b: bool) -> u32 {
    return ep_line(tag, ep_flag(b));
}

fn ep_line_i(tag: str, v: i64) -> u32 {
    let _a: u32 = ep_w(tag);
    let _b: u32 = ep_w("|");
    let m: u64 = v as u64;
    if v < 0 {
        let _s: u32 = ep_w("-");
        m = (0 as u64) - (v as u64);
    }
    let _c: u32 = ep_dec(m);
    return ep_w("\\n");
}

fn _start() {
""" + "\n".join(body) + """
    let a: ptr = alloc(64);
    let q: ptr = alloc(64);
    bigint_from_u64(a, 8, 1000000007);
    bigint_from_u64(q, 8, 777);
    let r: u32 = bigint_divmod_small(q, 8, a, 0);
    let _d0: u32 = ep_line("div0_ret", r as u64);
    let _d1: u32 = ep_line("div0_qlen", bigint_len(q) as u64);
    let _d2: u32 = ep_line("div0_q", bigint_to_u64(q));
    let r2: u32 = bigint_divmod_small(q, 8, a, 10);
    let _d3: u32 = ep_line("div10_ret", r2 as u64);
    let _d4: u32 = ep_line("div10_q", bigint_to_u64(q));
    let b: ptr = alloc(64);
    let q2: ptr = alloc(64);
    bigint_from_u64(b, 8, 18446744073709551615);
    let r3: u32 = bigint_divmod_small(q2, 8, b, 7);
    let _d5: u32 = ep_line("big_ret", r3 as u64);
    let _d6: u32 = ep_line("big_q", bigint_to_u64(q2));
    // #722: bigdec 的零除数 —— 返回哨兵、结果成 0，不是 SIGILL。
    let bd: ptr = alloc(bigdec_bytes(16));
    let bdr: ptr = alloc(bigdec_bytes(16));
    let bdt: ptr = alloc(bigint_dec_tmp_bytes(16));
    bigdec_from_u64(bd, 16, 100, 2);
    let rb: u32 = bigdec_div_small(bdr, 16, bd, 0, bdt, 16);
    let _e0: u32 = ep_line("bd_div0_ret", rb as u64);
    let bz: u32 = 0;
    if bigdec_is_zero(bdr) {
        bz = 1;
    }
    let _e1: u32 = ep_line("bd_div0_zero", bz as u64);
    syscall4(60, 0, 0, 0);
}
"""


def _have_linux() -> bool:
    return (not IS_WIN) or shutil.which("wsl") is not None


def _lx(p: Path) -> str:
    s = p.resolve().as_posix()
    if IS_WIN and len(s) > 1 and s[1] == ":":
        s = "/mnt/" + s[0].lower() + s[2:]
    return s


def _compile_elf(src: str, td: Path, name: str) -> Path:
    """用**仓内**参考编译器 + `lomelf` 出 ELF —— 这条路不需要 clang。"""
    probe = ROOT / "loment" / "build" / "std_edge_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        mod = lomentc.load(probe)
        deps = lomentc.resolve_deps(mod, ROOT, probe.parent, entry=probe)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"std 边界探针检查不过: {errs[:3]}"
        ll = lomentc.emit_llvm(mod, ROOT, deps)
    finally:
        probe.unlink(missing_ok=True)
    exe = td / f"{name}.elf"
    exe.write_bytes(lomelf.compile_ll(ll)[0])
    exe.chmod(0o755)
    return exe


def _run_linux(exe: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    argv = [str(exe)] if not IS_WIN else [*_WSLCMD, _lx(exe)]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False, timeout=timeout)


def _edge_expected() -> str:
    out: list[str] = []
    for i, s in enumerate(_PARSE_I64_CASES):
        ok, v = _py_parse_i64(s)
        out.append(f"p{i}_ok|{1 if ok else 0}")
        out.append(f"p{i}_v|{v}")
    out += [
        f"div0_ret|{2 ** 32 - 1}",
        "div0_qlen|1",
        "div0_q|777",
        "div10_ret|7",
        "div10_q|100000000",
        f"big_ret|{(2 ** 64 - 1) % 7}",
        f"big_q|{(2 ** 64 - 1) // 7}",
        "bd_div0_ret|4294967295",
        "bd_div0_zero|1",
    ]
    return "\n".join(out) + "\n"


def _check_store_std_edges() -> None:
    """`lompi/store/std` 的 parse / bigint 边界：与 Python 独立算的期望逐字节比。

    * issue #720：`parse_i64_ok` 的界必须是**有符号**的；`parse_i64` 饱和、绝不反号。
    * issue #721：`bigint_divmod_small(..., 0)` 返回 `BIG_DIV0`、商不动，不是 `ud2`。
    """
    if not _have_linux():
        print("      SKIP: 无 WSL/Linux (store/std 边界)")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        exe = _compile_elf(_edge_probe(), td, "std_edge_probe")
        r = _run_linux(exe, timeout=120)
    assert r.returncode == 0, f"std 边界探针没跑完 (退出码 {r.returncode}): {r.stderr[-300:]}"
    want = _edge_expected()
    if r.stdout != want:
        g, w = r.stdout.splitlines(), want.splitlines()
        first = next((i for i in range(max(len(g), len(w)))
                      if (g[i] if i < len(g) else None) != (w[i] if i < len(w) else None)), None)
        raise AssertionError(
            f"store/std 边界与 Python 不一致 @ 第 {first} 行: "
            f"Loment={g[first] if first is not None and first < len(g) else '<无>'!r} "
            f"Python={w[first] if first is not None and first < len(w) else '<无>'!r}")
    print(f"      parse/bigint: {len(want.splitlines())} 项与 Python 一致 (issue #720/#721)")


@test
def test_std_modules_match_python():
    """std 核的 mem / num 两模块: Loment 算的和 Python 算的**逐字节相同**。

    同时量 `lompi/store/std` 的 parse / bigint 边界（issue #720 / #721）—— 那一条走
    仓内 `lomelf`，不需要 clang。
    """
    _check_store_std_edges()
    if not (J._clang() and J._wsl()):
        print("      SKIP: mem/num 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "std_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(PROBE)
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            elf = J._compile(probe, td, "std_probe")
            got = J._run(elf, td)
    finally:
        probe.unlink(missing_ok=True)
    want = _expected()
    if got != want:
        g, w = got.splitlines(), want.splitlines()
        first = next((i for i in range(max(len(g), len(w)))
                      if (g[i] if i < len(g) else None) != (w[i] if i < len(w) else None)), None)
        raise AssertionError(
            f"std 核结果与 Python 不一致 @ 第 {first} 行: "
            f"Loment={g[first] if first is not None and first < len(g) else '<无>'!r} "
            f"Python={w[first] if first is not None and first < len(w) else '<无>'!r}")
    print(f"      mem + num: {len(want.splitlines())} 项与 Python 逐字节一致")


# ---------------------------------------------------------------- Loment 版（S1 第八格）

#: Loment 版的同一件事 —— 探针已经是**仓库里的一件工具**, 不再由判据生成。
TWIN = ROOT / "loment" / "tools" / "lomstdcheck.lomt"


@test
def test_std_check_matches_loment_twin():
    """`loment/tools/lomstdcheck.lomt` 报的每一项, 与 Python 算的**逐字节相同**。

    `docs/189` §3 的 S1 第八格。与上一条的区别不是"换了实现", 而是**探针那一层没了**:
    参考那侧必须"生成探针源 -> 编出来跑"（Python 调不了 Loment 的库）, Loment 侧自己
    就是 Loment 程序, 直接 `use mem` / `use num`。
    """
    if not (J._clang() and J._wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        elf = J._compile(TWIN, td, "lomstdcheck")
        got = J._run(elf, td)
    want = _expected()
    assert got == want, f"std 核结果与 Python 不一致:\n  got : {got!r}\n  want: {want!r}"
    print(f"      mem + num: {len(want.splitlines())} 项与 Python 逐字节一致（Loment 版）")


@test
def test_std_twin_selfhost_compiles():
    """`lomstdcheck.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
    if not (J._clang() and J._wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        s1 = td / "stage1"
        r = subprocess.run(
            [J._clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{J._T}stdcheck_s1.bin"
        script = (f"cp {J._wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {J._wsl_path(ROOT)} && {binn} loment/tools/lomstdcheck.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomstdcheck.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomstdcheck.lomt 成功 ({len(rr.stdout)}B IR)")


@test
def test_std_modules_are_checkable():
    """每个 std 模块**自己**必须是合法的 L1 单元 (这条不靠 clang, 永远跑)。"""
    import lomentc
    libs = sorted((ROOT / "loment" / "lib").glob("*.lomt"))
    assert len(libs) >= 5, [p.name for p in libs]
    for p in libs:
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{p.name}: {errs[:2]}"
    print(f"      {len(libs)} 个 std 模块都过检查: {[p.stem for p in libs]}")


def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_std_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
