#!/usr/bin/env python3
# loment_thread_test.py — `thread_spawn`（M84, `docs/245`）的判据
#
# 口径与 LibL 家族一致：**对着真东西跑**。这里的"真东西"是**内核本身** —— 判据不是
# "IR 里有那条 asm"，而是**真的起 N 个线程在共享内存上累加，数出来的和必须等于 N×轮数**。
#
# 为什么非跑不可（三条都是实测，不是顾虑）：
#   * `clone` 的 flags 少一个 `CLONE_*`（或多一个 `CLONE_PIDFD`）→ 内核直接 `EINVAL`，
#     而**产物照样生成成功**。2026-10-13 就撞过这一条（当时写的是 0x51300）。
#   * `CLONE_VM` 不在时子线程是**另一个进程**，共享内存根本不共享 —— 累加结果是 0，
#     而"编译成功"这一步完全看不出来。
#   * 子线程换不换 `rbp` 是**行为**差别：不换时子线程读写落在父线程的帧里，
#     `if r == 0` 那句判断本身在读共享内存（症状：第一个子线程从不走那条路，却出现第二个）。
#
# **为什么探针是 ELF**：`thread_spawn` 直译 Linux 的 `clone`，Windows 侧没有对应物 ——
# 那边是**编译错误并点名**（`test_pe_refuses_thread_spawn` 钉着）。Windows 上探针交给
# WSL 跑，Linux 上直接跑（与 `loment_os_test.py` 同一条路）。
#
# **负例那一半不是装饰**：入口名必须在**编译期**就查得出来（是字面量、本单元里有、形参
# 恰好一个）。少了"形参恰好一个"，入口声明两个形参时子线程只压一个槽，第二个形参读到的
# 是栈上的垃圾 —— 那是**静默错编**，比报错坏得多。
#
# 运行: python tools/loment_thread_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


PROBE = ROOT / "loment" / "build" / "thread_probe.lomt"
IS_WIN = sys.platform == "win32"

N = 4
ROUNDS = 200000
STACK = 262144

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 同一个 Linux 环境

def _have_linux() -> bool:
    return (not IS_WIN) or shutil.which("wsl") is not None


def _lx(p: Path) -> str:
    """本机路径 -> 那个 Linux 环境里的路径（Windows 上是 `/mnt/<盘>/...`）。"""
    s = p.resolve().as_posix()
    if IS_WIN and len(s) > 1 and s[1] == ":":
        s = "/mnt/" + s[0].lower() + s[2:]
    return s


def _run_linux(argv: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    if IS_WIN:
        argv = [*_WSLCMD, *argv]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False, timeout=timeout)


# ---------------------------------------------------------------- 编探针

def _check(src: str, name: str = "thread_probe") -> list[str]:
    """只跑检查，返回诊断（用来测负例）。"""
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    with PROBE.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        mod = lomentc.load(PROBE)
        deps = lomentc.resolve_deps(mod, ROOT, PROBE.parent, entry=PROBE)
        return lomentc.check(mod, deps=deps, _dep_pass=True)
    finally:
        PROBE.unlink(missing_ok=True)


def _compile_elf(src: str, td: Path, name: str) -> Path:
    """编成 **ELF**（即使本机是 Windows）—— `clone` 是 Linux 的号，只能在那边的环境里跑。"""
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    with PROBE.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        mod = lomentc.load(PROBE)
        deps = lomentc.resolve_deps(mod, ROOT, PROBE.parent, entry=PROBE)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"探针检查不过: {errs[:3]}"
        ll = lomentc.emit_llvm(mod, ROOT, deps)
    finally:
        PROBE.unlink(missing_ok=True)
    exe = td / f"{name}.elf"
    exe.write_bytes(lomelf.compile_ll(ll)[0])
    exe.chmod(0o755)
    return exe


# ---------------------------------------------------------------- 探针源码

#: 工作线程：**先睡一会儿**（让主线程一定已经睡在 futex 上，这样"被唤醒"才是可证的
#: 而不是"撞运气"），再把自己的 tid 写进自己的槽（证明它真是一个独立的线程），然后在
#: **共享**计数器上原子累加 `ROUNDS` 次，**然后什么都不写就 return**。
#:
#: 最后那句"什么都不写"是**刻意的**：让 `done` 加一、`futex_wake` 那件事是**产物做的**
#: （`thread_spawn` 第四个实参那段尾声），不是这个函数做的。工作线程想赖也赖不掉。
#: 每条实参指向自己那条 16 字节记录 `[tid 槽(8) | 共享计数器指针(8)]`。
_WORKER = f"""
module th_probe
choose runtime

const N: u32 = {N};
const ROUNDS: u32 = {ROUNDS};
const STACK: u64 = {STACK};

fn rd(p: ptr, o: u32) -> u64 {{
    let v: u64 = 0;
    v = (load8(p, o) as u64) + ((load8(p, o + 1) as u64) << 8)
        + ((load8(p, o + 2) as u64) << 16) + ((load8(p, o + 3) as u64) << 24)
        + ((load8(p, o + 4) as u64) << 32) + ((load8(p, o + 5) as u64) << 40)
        + ((load8(p, o + 6) as u64) << 48) + ((load8(p, o + 7) as u64) << 56);
    return v;
}}

fn wr(p: ptr, o: u32, v: u64) {{
    let i: u32 = 0;
    while i < 8 {{
        store8(p, o + i, ((v >> ((i * 8) as u32)) & 255) as u8);
        i = i + 1;
    }}
}}

fn worker(arg: u64) -> u32 {{
    let rec: ptr = arg as ptr;
    let ts: ptr = alloc(16);
    wr(ts, 0, 0);
    wr(ts, 8, 5000000);                              // 5ms —— 让主线程先睡下去
    let _s: i64 = syscall4(35, ts as u64, 0, 0);     // nanosleep
    wr(rec, 0, syscall4(186, 0, 0, 0) as u64);       // gettid() -> 自己那一格
    let cnt: ptr = rd(rec, 8) as ptr;
    let i: u32 = 0;
    while i < ROUNDS {{
        atomic_add(cnt, 1);
        i = i + 1;
    }}
    return 0;
}}

fn wu(v: u64) -> u32 {{
    let b: ptr = alloc(24);
    let n: u32 = 0;
    let x: u64 = v;
    if x == 0 {{
        store8(b, 0, 48 as u8);
        n = 1;
    }}
    while x > 0 {{
        store8(b, n, (48 + (x % 10) as u32) as u8);
        x = x / 10;
        n = n + 1;
    }}
    let i: u32 = 0;
    while i < n / 2 {{
        let lo: u8 = load8(b, i) as u8;
        store8(b, i, load8(b, n - 1 - i) as u8);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }}
    return syscall4(1, 1, b as u64, n as u64) as u32;
}}

fn line(tag: str, v: u64) -> u32 {{
    let _a: i64 = syscall4(1, 1, str_ptr(tag) as u64, str_len(tag) as u64);
    let _e: i64 = syscall4(1, 1, str_ptr("=") as u64, 1);
    let w: u32 = wu(v);
    return syscall4(1, 1, str_ptr(" ") as u64, 1) as u32;
}}

fn rd32(p: ptr) -> u32 {{
    return load8(p, 0) | (load8(p, 1) << 8) | (load8(p, 2) << 16) | (load8(p, 3) << 24);
}}

fn wr32(p: ptr, v: u32) {{
    store8(p, 0, (v & 255) as u8);
    store8(p, 1, ((v >> 8) & 255) as u8);
    store8(p, 2, ((v >> 16) & 255) as u8);
    store8(p, 3, ((v >> 24) & 255) as u8);
}}

fn _start() {{
    let cnt: ptr = alloc(8);
    wr32(cnt, 0);
    let done: ptr = alloc(8);
    wr32(done, 0);
    let recs: ptr = alloc((N * 16) as u32);
    let tids: ptr = alloc((N * 8) as u32);
    let main_tid: u64 = syscall4(186, 0, 0, 0) as u64;
    let i: u32 = 0;
    while i < N {{
        let rec: ptr = ((recs as u64) + ((i * 16) as u64)) as ptr;
        wr(rec, 0, 0);
        wr(rec, 8, cnt as u64);
        let stk: i64 = syscall7(9, 0, STACK, 3, 34, (0 as u64) - 1, 0);
        if stk < 0 {{
            let _e: u32 = wu(9999);
        }} else {{
            // 第四个实参就是"完成通知地址" —— 子线程跑完由**产物**在那里加一并唤醒。
            let t: i64 = thread_spawn("worker", (stk as u64) + STACK, rec as u64, done as u64);
            let _s: u32 = 0;
        }}
        i = i + 1;
    }}
    // 睡在 futex 上等全部完成。**这是这一条要验的东西**：工作线程的 `worker` 里一句
    // "喊一声"都没有，`done` 能到 N、futex 真把主线程叫醒，全是产物那段尾声做的。
    // `wakes` 数的是**真被叫醒**的次数（返回 0），`EAGAIN`（值已经变了）不算。
    let ts: ptr = alloc(16);
    let wakes: u32 = 0;
    let spins: u32 = 0;
    while rd32(done) < N && spins < 200 {{
        let cur: u32 = rd32(done);
        wr(ts, 0, 2);
        wr(ts, 8, 0);
        let r: i64 = syscall7(202, done as u64, 128, cur as u64, ts as u64, 0, 0);
        if r == 0 {{
            wakes = wakes + 1;
        }}
        spins = spins + 1;
    }}
    // 把每条记录的 tid 抄到一处，顺序打出来。
    let j: u32 = 0;
    while j < N {{
        let rec2: ptr = ((recs as u64) + ((j * 16) as u64)) as ptr;
        wr(tids, j * 8, rd(rec2, 0));
        j = j + 1;
    }}
    let _1: u32 = line("sum", rd32(cnt) as u64);
    let _2: u32 = line("done", rd32(done) as u64);
    let _3: u32 = line("wakes", wakes as u64);
    let _4: u32 = line("main", main_tid);
    j = 0;
    while j < N {{
        let _5: u32 = line("tid", rd(tids, j * 8));
        j = j + 1;
    }}
    let _6: i64 = syscall4(1, 1, str_ptr("\\n") as u64, 1);
    syscall4(60, 0, 0, 0);
}}
"""


def _kv(text: str) -> dict[str, list[str]]:
    """探针的 stdout 是一串 `<标签>=<十进制数>`（没有 printf，逐个写十进制）。"""
    out: dict[str, list[str]] = {}
    for tok in text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


def _run_probe(td: Path) -> dict[str, list[str]]:
    assert _have_linux(), "没有 WSL/Linux, 跑不了 ELF 探针"
    exe = _compile_elf(_WORKER, td, "th_probe")
    got = _run_linux([_lx(exe)], timeout=300)
    assert got.returncode == 0, f"探针没跑完 (rc={got.returncode}): {got.stderr[-300:]}"
    return _kv(got.stdout)


# ---------------------------------------------------------------- 判据

@test
def test_threads_accumulate_on_shared_memory():
    """N 个线程各在**共享**计数器上原子加 `ROUNDS` 次 —— 和必须正好是 `N × ROUNDS`。

    这一条同时钉住三件在"编译成功"里看不出来的事：
      * `clone` 的 flags **对**（错一个内核就 `EINVAL`，产物照样生成）；
      * **`CLONE_VM` 在**（不在的话子线程是另一个进程，内存不共享，和为 0）；
      * 子线程**真的从入口开始跑**（RIP 继承 / 栈没接上都会让它跑飞或落到父线程的代码里）。
    """
    with tempfile.TemporaryDirectory() as tds:
        got = _run_probe(Path(tds))
    assert "sum" in got, f"探针没打出 sum: {got}"
    want = N * ROUNDS
    assert int(got["sum"][0]) == want, f"共享计数 {got['sum'][0]}，期望 {want}"


@test
def test_each_thread_is_a_distinct_thread():
    """每个工作线程写下自己的 `gettid()` —— 它们必须是**互不相同**、且**都不等于主线程**。

    这是"真线程"那一条的**证伪面**：把 `thread_spawn` 换成一个普通的顺序调用，累加和
    照样等于 `N × ROUNDS`（上面那条**测不出来**），但 tid 会全部相同。少了这一条，
    "起线程"这件事就只是"跑了一段代码"。
    """
    with tempfile.TemporaryDirectory() as tds:
        got = _run_probe(Path(tds))
    assert "main" in got and len(got.get("tid", [])) == N, f"探针输出不全: {got}"
    main_tid = int(got["main"][0])
    tids = [int(t) for t in got["tid"]]
    assert all(t > 0 for t in tids), f"有线程没写 tid: {tids}"
    assert len(set(tids)) == N, f"{N} 个线程的 tid 有重复: {tids}"
    assert main_tid not in tids, f"子线程的 tid 与主线程相同 ({main_tid}): {tids}"


@test
def test_completion_wakes_the_joiner():
    """第四个实参那条**完成通知**真的发生 —— 而且**不是工作线程自觉做的**。

    为什么非得有它：子线程的尾声是**编译器发的**（入口一 `return` 就 `exit`），所以工作
    线程在返回之后**没有机会跑任何代码**。没有这段尾声，`join` 就只能靠"工作线程自己记得
    在最后喊一声"，忘了就是**挂死** —— 一个系统语言不该有的地雷。

    **怎么证**：主线程**睡在 futex 上**（工作线程先 `nanosleep` 5ms，保证主线程一定已经
    睡下去），`wakes` 数的是**真被叫醒**的次数（`futex` 返回 0；值已经变了返回 `EAGAIN`,
    不算）。这个探针里的 `worker` **一句通知都没写**，所以 `done` 到 N、主线程被叫醒，
    只能来自产物。`done` 加一用的是 `lock incl` —— 它同时是那个唤醒的**释放屏障**。
    """
    with tempfile.TemporaryDirectory() as tds:
        got = _run_probe(Path(tds))
    assert "done" in got and "wakes" in got, f"探针输出不全: {got}"
    assert int(got["done"][0]) == N, \
        f"完成计数 {got['done'][0]}，期望 {N} —— 产物里那段尾声没跑到"
    assert int(got["wakes"][0]) > 0, \
        ("主线程一次都没被 futex 叫醒 —— 通知那一段没发出来 (或没发对)")


@test
def test_entry_must_be_a_string_literal():
    """入口必须是**字面量**：产物里那是一条写死的调用边，不是运行期取得到的值。"""
    src = _WORKER.replace('thread_spawn("worker"', 'thread_spawn(who')
    src = src.replace("fn _start() {", "fn _start() {\n    let who: str = \"worker\";")
    errs = _check(src)
    assert any("thread_spawn" in e and "字面量" in e for e in errs), \
        f"把入口换成变量应当报出来, 实际: {errs[:3]}"


@test
def test_unknown_entry_is_a_compile_error():
    """入口名**在本单元里查不到**时在编译期点名 —— 不留到链接期变成"未定义的标签"。"""
    src = _WORKER.replace('thread_spawn("worker"', 'thread_spawn("nope"')
    errs = _check(src)
    assert any("未定义的函数" in e and "nope" in e for e in errs), \
        f"未定义的入口应当报出来, 实际: {errs[:3]}"


@test
def test_entry_must_take_exactly_one_parameter():
    """入口**形参恰好一个** —— 两个形参时子线程只压一个槽，第二个读到栈上的垃圾。

    那是**静默错编**（编译成功、跑起来结果错），所以这条不是洁癖。
    """
    src = _WORKER.replace("fn worker(arg: u64) -> u32 {",
                          "fn worker(arg: u64, extra: u64) -> u32 {")
    src = src.replace("    let rec: ptr = arg as ptr;",
                      "    let rec: ptr = arg as ptr;\n    let _x: u64 = extra;")
    errs = _check(src)
    assert any("thread_spawn" in e and "1 个实参" in e for e in errs), \
        f"两形参的入口应当报出来, 实际: {errs[:3]}"


@test
def test_pe_refuses_thread_spawn():
    """PE 目标**响亮地拒绝** —— Windows 没有 `clone`，静默错编一个"假线程"更坏。"""
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    with PROBE.open("w", encoding="utf-8", newline="\n") as f:
        f.write(_WORKER)
    try:
        mod = lomentc.load(PROBE)
        deps = lomentc.resolve_deps(mod, ROOT, PROBE.parent, entry=PROBE)
        ll = lomentc.emit_llvm(mod, ROOT, deps)
    finally:
        PROBE.unlink(missing_ok=True)
    try:
        lomelf.compile_pe(ll)
    except lomelf.Unsupported as e:
        assert "thread_spawn" in str(e), f"拒绝得没点名: {e}"
        print(f"      PE 侧拒绝并点名: {e}")
        return
    raise AssertionError("PE 目标居然接受了 thread_spawn —— 它没有 clone")


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_thread_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
