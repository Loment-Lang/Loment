#!/usr/bin/env python3
# loment_os_test.py — `os` 库 (`lompi/store/os/0.1.0/`) 的判据
#
# 这是第四个随包库的判据, 口径与 LibL / internet 一致: **对着真东西跑**。这里的"真东西"
# 就是**操作系统本身**, 参考实现是 **CPython 的 `os` / `time` / `signal`** —— 它跟我们的
# 探针**在同一个环境里**跑, 于是 `uname`、`uid`、`getcwd`、`mkdir` 的**副作用**这些都能
# 逐字节比, 而不是拿一份手写的期望值。
#
# **为什么探针是 ELF 而不是本机 PE**: 这一层全是 Linux 的号 (与 `lom/net.lom` 同一套),
# PE 上除那 8 个之外一律返回 -1。所以探针编成 ELF, Windows 上交给 WSL 跑, Linux 上直接跑。
# 参考那份也一样走同一个环境 —— 两边环境不同的话, 比的就不是同一个操作系统了。
#
# **模块自检为什么只有纯逻辑**: `loment.py test` 把 Loment 译成 Rust 跑, 而那里的
# `syscall4` 是 Linux 的 `asm!("syscall")` —— 在 Windows 上它照样"返回一个数"
# (实测: 三条读身份的自检过了两条), 那个数是**巧合不是身份**。所以真实行为一律在这里判。
#
# 运行: python tools/loment_os_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


PKG = ROOT / "lompi" / "store" / "os" / "0.1.0"
STORE = ROOT / "lompi" / "store"
PROBE = ROOT / "loment" / "build" / "os_probe.lomt"
IS_WIN = sys.platform == "win32"
MODULES = ("sys", "time", "signal", "tty", "user", "thread")

#: `os/thread` 那条真跑判据的规模：4 个线程各 1 万次临界区。
#:
#: **为什么不是 5 万**：第一版写的 5 万，本机 5.6s、WSL 里 1 核也 5s —— 但在 GitHub 的
#: 共享 runner 上（门禁 `-j 4`，同一片里还有 200s 级的兄弟）**跑到 300s 超时**，被门禁
#: 自己的复跑判成 flake（`gate-log-5/flaked.txt`）。取号锁放锁是**全叫醒**（见 `docs/245`
#: §8：丢唤醒是错、惊群只是慢），4 个线程时每次放锁最多额外叫醒 3 个 —— 负载一高，
#: 系统调用数就压过了收益。1 万次仍然**远远**够用：锁一旦坏掉，4 个线程在
#: `v = load; store(v+1)` 上丢的更新是成千上万，不是"碰巧没撞上"。
N_THREADS = 4
THREAD_ROUNDS = 10000

#: 逐字节与 CPython 比的那几个键 (两边跑在同一个环境里, 所以它们必须是同一个值)。
BYTE_KEYS = ("sysname", "machine", "uid", "gid", "mkdir_ok", "access_ok",
             "chdir_ok", "cwd", "create_ok", "rename_ok", "unlink_ok", "rmdir_ok",
             "isatty")

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 同一个 Linux 环境

def _have_linux() -> bool:
    return (not IS_WIN) or shutil.which("wsl") is not None


def _lx(p: Path) -> str:
    """本机路径 -> 那个 Linux 环境里的路径 (Windows 上是 `/mnt/<盘>/...`)。"""
    s = p.resolve().as_posix()
    if IS_WIN and len(s) > 1 and s[1] == ":":
        s = "/mnt/" + s[0].lower() + s[2:]
    return s


def _run_linux(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    if IS_WIN:
        argv = [*_WSLCMD, *argv]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False, timeout=timeout)


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        if "|" in ln:
            k, v = ln.split("|", 1)
            out[k] = v
    return out


# ---------------------------------------------------------------- 编探针

def _compile_elf(src: str, td: Path, name: str) -> Path:
    """编成 **ELF** (即使本机是 Windows) —— 这一层是 Linux 的号, 只能在那边的环境里跑。"""
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


def _lit(s: str) -> str:
    assert all(32 <= ord(c) < 127 and c not in '"\\' for c in s), f"字面量有要转义的字符: {s!r}"
    return '"' + s + '"'


def _probe_src(base: str) -> str:
    return f"""
module os_probe
choose runtime
use os

fn w(s: str) -> u32 {{
    return syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64) as u32;
}}

fn wmem(p: ptr, o: u32, n: u32) -> u32 {{
    return syscall4(1, 1, (p as u64) + o, n as u64) as u32;
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

fn rd32(p: ptr) -> u32 {{
    return load8(p, 0) | (load8(p, 1) << 8) | (load8(p, 2) << 16) | (load8(p, 3) << 24);
}}

fn ok(v: i64) -> u32 {{
    if v >= 0 {{
        return 1;
    }}
    return 0;
}}

fn flag(b: bool) -> u32 {{
    if b {{
        return 1;
    }}
    return 0;
}}

fn _start() {{
    let u: ptr = alloc(512);
    let l: ptr = alloc(4);
    if sys_uname(u) == 0 {{
        let a: u32 = sys_uts_field(u, SYS_UTS_SYSNAME, l);
        w("sysname|"); wmem(u, a, rd32(l)); w("\\n");
        let b: u32 = sys_uts_field(u, SYS_UTS_MACHINE, l);
        w("machine|"); wmem(u, b, rd32(l)); w("\\n");
    }}
    w("uid|"); wu(user_getuid()); w("\\n");
    w("gid|"); wu(user_getgid()); w("\\n");
    w("pid|"); wu(user_getpid()); w("\\n");

    let base: ptr = alloc(4096);
    let d: ptr = alloc(4096);
    let f1: ptr = alloc(4096);
    let f2: ptr = alloc(4096);
    sys_cstr(base, 4096, {_lit(base)});
    sys_cstr(d, 4096, {_lit(base + "/d")});
    sys_cstr(f1, 4096, {_lit(base + "/f1")});
    sys_cstr(f2, 4096, {_lit(base + "/f2")});
    w("mkdir_ok|"); wu(ok(sys_mkdir(d, 493)) as u64); w("\\n");
    w("access_ok|"); wu(ok(sys_access(d, SYS_F_OK)) as u64); w("\\n");
    w("chdir_ok|"); wu(ok(sys_chdir(base)) as u64); w("\\n");
    let cwd: ptr = alloc(4096);
    let n: i64 = sys_getcwd(cwd, 4096);
    w("cwd|");
    if n > 1 {{
        wmem(cwd, 0, (n as u32) - 1);
    }}
    w("\\n");
    let fd: i64 = sys_openat(SYS_AT_FDCWD, f1, SYS_O_WRONLY_CREAT_TRUNC, SYS_MODE_RW);
    w("create_ok|"); wu(ok(fd) as u64); w("\\n");
    if fd >= 0 {{
        sys_close(fd as u64);
    }}
    w("rename_ok|"); wu(ok(sys_rename(f1, f2)) as u64); w("\\n");
    w("unlink_ok|"); wu(ok(sys_unlink(f2)) as u64); w("\\n");
    w("rmdir_ok|"); wu(ok(sys_rmdir(d)) as u64); w("\\n");

    let scratch: ptr = alloc(64);
    w("isatty|"); wu(tty_isatty(1, scratch) as u64); w("\\n");

    let ts: ptr = alloc(16);
    let req: ptr = alloc(16);
    let rem: ptr = alloc(16);
    let t0: u64 = time_mono_ns(ts);
    time_sleep_ms(50, req, rem);
    let t1: u64 = time_mono_ns(ts);
    w("mono_advance|"); wu(flag(t1 > t0 && t1 - t0 >= 40000000) as u64); w("\\n");
    w("now|"); wu(time_now_ns(ts) / 1000000000); w("\\n");

    let sa: ptr = alloc(32);
    w("sigignore_ok|"); wu(ok(signal_ignore_pipe(sa)) as u64); w("\\n");
    signal_self(SIGNAL_PIPE);
    w("sigpipe_alive|1\\n");

    syscall4(60, 0, 0, 0);
}}
"""


def _ref_src(base: str) -> str:
    """CPython 的参考: 在**同一个环境**里做同一串事。"""
    return f'''
import os, time
BASE = {base!r}
os.mkdir(BASE + "/rd", 0o755)
print("mkdir_ok|1")
print("access_ok|" + ("1" if os.access(BASE + "/rd", os.F_OK) else "0"))
os.chdir(BASE)
print("chdir_ok|1")
print("cwd|" + os.getcwd())
fd = os.open(BASE + "/rf1", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
print("create_ok|" + ("1" if fd >= 0 else "0"))
os.close(fd)
os.rename(BASE + "/rf1", BASE + "/rf2")
print("rename_ok|1")
os.unlink(BASE + "/rf2")
print("unlink_ok|1")
os.rmdir(BASE + "/rd")
print("rmdir_ok|1")
print("isatty|" + ("1" if os.isatty(1) else "0"))
u = os.uname()
print("sysname|" + u.sysname)
print("machine|" + u.machine)
print("uid|" + str(os.getuid()))
print("gid|" + str(os.getgid()))
print("now|" + str(int(time.time())))
'''


# ---------------------------------------------------------------- 结构

@test
def test_os_modules_are_checkable():
    """每个模块**自己**必须是合法的 L1 单元 (不编可执行, 永远跑)。

    `_dep_pass=True` 的理由与 LibL / internet 相同 (绕开 issue #246 那趟把"其余依赖整份"
    当符号表递下去的假报错)。本判据只主张"这几个模块自己是合法单元"。
    """
    for name in MODULES:
        p = PKG / f"{name}.lomt"
        assert p.exists(), f"缺模块 {p}"
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"{p.name}: {errs[:2]}"
    assert (PKG / "os.lomt").exists(), "缺包入口 os.lomt"
    assert (PKG / "pkg.lomp").exists(), "缺 pkg.lomp"
    print(f"      {len(MODULES)} 个模块 + 入口 + pkg.lomp 都过检查")


@test
def test_os_package_is_one_unit_without_name_collisions():
    """门面 (包内全部模块进同一个单元) 不许有重名 —— 顶层名字是**平的**。"""
    entry = PKG / "os.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    errs = lomentc.check(mod, deps=deps, _dep_pass=True)
    assert not errs, f"门面检查不过: {errs[:3]}"
    print(f"      门面 ({len(MODULES)} 个模块一个单元) 检查通过")


def _top_names(path: Path) -> set[str]:
    m = lomentc.load(path)
    out: set[str] = set()
    for group in (m.funcs, m.consts, m.structs, m.enums, m.traits, m.externs):
        for d in group:
            out.add(d.name)
    return out


@test
def test_os_names_do_not_collide_with_the_other_packages():
    """**os 的顶层名字与 std / host / internet 一个都不能撞。**

    钉的是那条隐性硬规则: `resolve_name` 在包内模块层 (`store/<包>/<版本>/<名>.ext`) 遇到
    **两处命中直接报错**, 而顶层名字在一个单元里是**平的**。所以两边的同名顶层声明同时被
    `use` 到时, 后果是**编译错误**, 不是"后者覆盖前者"。
    """
    mine: set[str] = set()
    for name in MODULES:
        mine |= _top_names(PKG / f"{name}.lomt")
    theirs: set[str] = set()
    for pkg in ("std", "host", "internet"):
        for f in sorted((STORE / pkg / "0.1.0").glob("*.lomt")):
            theirs |= _top_names(f)
    # `Option` / `Result` 是语言预置泛型, 每个模块的名册里都有 —— 先比掉。
    clash = sorted((mine & theirs) - {"Option", "Result"})
    assert not clash, (
        f"os 有 {len(clash)} 个顶层名字与别的包撞了: {clash[:8]} —— 换掉公开前缀或内部前缀")
    print(f"      os {len(mine)} 个顶层名字与 std+host+internet {len(theirs)} 个无一相撞")


# ---------------------------------------------------------------- 对着真 OS 跑

@test
def test_os_matches_python():
    """探针与 CPython **在同一个 Linux 环境里**做同一串事, 结果逐字节一致。"""
    if not _have_linux():
        print("      SKIP: 没有可用的 Linux 环境 (本机是 Windows 且没有 wsl)")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = td / "osbase"          # 两边**同一个目录**, 所以 cwd 才可比
        base.mkdir()
        base_lx = _lx(base)
        exe = _compile_elf(_probe_src(base_lx), td, "os_probe")
        got_p = _run_linux([_lx(exe)], timeout=60)
        ref = td / "ref.py"
        ref.write_text(_ref_src(base_lx), encoding="utf-8", newline="\n")
        got_r = _run_linux(["python3", _lx(ref)], timeout=60)
    assert got_r.returncode == 0, f"CPython 参考自己没过: {got_r.stderr[-400:]}"
    got, want = _kv(got_p.stdout), _kv(got_r.stdout)
    assert got, f"探针没打出任何东西 (退出码 {got_p.returncode}): {got_p.stderr[-300:]}"
    for k in BYTE_KEYS:
        assert k in got, f"探针没打出 {k}"
        assert k in want, f"CPython 参考没打出 {k}"
        assert got[k] == want[k], f"{k}: 本库={got[k]!r} CPython={want[k]!r}"
    # 剩下几条是**谓词**, 不是等值 —— 进程/时间两边本来就不一样。
    assert int(got["pid"]) > 0, f"getpid 不像是真的: {got['pid']!r}"
    assert int(got["mono_advance"]) == 1, "睡了 50ms 而单调钟没前进 40ms 以上"
    skew = abs(int(got["now"]) - int(want["now"]))
    assert skew <= 120, f"墙上时钟与 CPython 差了 {skew}s (容忍 120s)"
    assert int(got["sigignore_ok"]) == 1, "rt_sigaction 忽略 SIGPIPE 失败"
    assert int(got["sigpipe_alive"]) == 1, "忽略 SIGPIPE 之后发给自己, 进程还是死了"
    print(f"      os: {len(BYTE_KEYS)} 项与 CPython 逐字节一致 + 5 项谓词 (pid/单调钟/时钟/信号)")


#: `os/thread` 的真跑探针（`docs/245` §8 阶段 2）。
#:
#: **为什么计数器是"非原子的读改写"**：`thread_lw`/`thread_sw` 故意不用 `atomic_add`
#: —— 临界区里就是一条 `v = load; store(v+1)`。于是**只有锁是对的，结果才对**：锁一旦
#: 坏了（漏放、两个人同时进去、丢了唤醒），丢更新的概率在 4×50000 次上是必然的，
#: 而不是"偶尔"。用原子加去测锁，测的是别的东西。
#:
#: `done` 那一路走的是**产物**的完成通知（`thread_spawn` 第四个实参），所以
#: `thread_join_all` 能等到 N —— 工作线程里一句"喊一声"都没有。
_THREAD_SRC = f"""
module os_thread_probe
choose runtime
use thread

const N: u32 = {N_THREADS};
const ROUNDS: u32 = {THREAD_ROUNDS};
const STACK: u32 = 262144;

fn t_rd(p: ptr, o: u32) -> u32 {{
    return load8(p, o) | (load8(p, o + 1) << 8) | (load8(p, o + 2) << 16)
        | (load8(p, o + 3) << 24);
}}

fn t_wr(p: ptr, o: u32, v: u32) {{
    store8(p, o, (v & 255) as u8);
    store8(p, o + 1, ((v >> 8) & 255) as u8);
    store8(p, o + 2, ((v >> 16) & 255) as u8);
    store8(p, o + 3, ((v >> 24) & 255) as u8);
}}

/// 记录: [next(4) | serving(4) | 计数器(4) | 空(4)] —— 前 8 字节是取号锁, 后 4 个是
/// **只用锁保护**的普通变量。
const OFF_CNT: u32 = 8;

fn worker(arg: u64) -> u32 {{
    let m: ptr = arg as ptr;
    let i: u32 = 0;
    while i < ROUNDS {{
        let _l: u32 = thread_lock(m);
        let v: u32 = t_rd(m, OFF_CNT);
        t_wr(m, OFF_CNT, v + 1);
        let _u: u32 = thread_unlock(m);
        i = i + 1;
    }}
    return 0;
}}

fn line(tag: str, v: u32) -> u32 {{
    let _a: i64 = syscall4(1, 1, str_ptr(tag) as u64, str_len(tag) as u64);
    let _e: i64 = syscall4(1, 1, str_ptr("|") as u64, 1);
    let b: ptr = alloc(24);
    let n: u32 = 0;
    let x: u32 = v;
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
    let _o: i64 = syscall4(1, 1, b as u64, n as u64);
    return syscall4(1, 1, str_ptr("\\n") as u64, 1) as u32;   // 一行一对 (`_kv` 按行解析)
}}

fn _start() {{
    let m: ptr = alloc(16);
    let k: u32 = 0;
    while k < 16 {{
        store8(m, k, 0 as u8);
        k = k + 1;
    }}
    let done: ptr = alloc(4);
    t_wr(done, 0, 0);
    let stacks: ptr = alloc((N * 8) as u32);
    let i: u32 = 0;
    while i < N {{
        let st: ptr = thread_stack(STACK);
        t_wr(stacks, i * 4, ((st as u64) / 4096) as u32);     // 按页记下来比
        let stk: u64 = st as u64;
        let _t: i64 = thread_spawn("worker", stk, m as u64, done as u64);
        i = i + 1;
    }}
    let got: u32 = thread_join_all(done, N);
    let _1: u32 = line("count", t_rd(m, OFF_CNT));
    let _2: u32 = line("joined", got);
    i = 0;
    while i < N {{
        let _3: u32 = line("stk", t_rd(stacks, i * 4));
        i = i + 1;
    }}
    let _4: i64 = syscall4(1, 1, str_ptr("\\n") as u64, 1);
    syscall4(60, 0, 0, 0);
}}
"""


@test
def test_os_thread_mutex_serializes_writers():
    """`os/thread` 的取号锁**真的把临界区串起来了** —— 这一条的对照物是**没有锁的结果**。

    临界区里是**非原子**的 `v = load; store(v+1)`，所以锁一旦坏了（漏放、两人同时进、
    `serving` 写错顺序），4×50000 次里丢更新是必然的。换言之：这条判据红的时候，坏的**一定
    是锁**，不是"这个测试碰巧没撞上"。

    它同时钉住 `thread_stack`（栈是 mmap 出来的，`thread_spawn` 要的正是那个栈顶）与
    完成通知（工作线程里没有一句"喊一声"，`thread_join_all` 却等到了 N）。
    """
    if not _have_linux():
        print("      SKIP: 无 WSL/Linux")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _compile_elf(_THREAD_SRC, td, "os_thread_probe")
        got_p = _run_linux([_lx(exe)], timeout=300)
    assert got_p.returncode == 0, f"线程探针没跑完: {got_p.stderr[-300:]}"
    got = _kv(got_p.stdout)
    assert "count" in got and "joined" in got, f"探针输出不全: {got_p.stdout!r}"
    want = N_THREADS * THREAD_ROUNDS
    assert int(got["count"]) == want, \
        f"锁保护的计数器是 {got['count']}，期望 {want} —— 锁漏了更新"
    assert int(got["joined"]) == N_THREADS, \
        f"join 等到 {got['joined']}，期望 {N_THREADS} —— 完成通知没到齐"
    # `stk` 是**重复的键**, `_kv` 是 dict (后者覆盖) —— 所以这里从原始 stdout 数。
    stk = [v for k, v in (ln.split("|", 1) for ln in got_p.stdout.splitlines()
                          if ln.startswith("stk|"))]
    assert len(stk) == N_THREADS, f"栈地址没打印全 ({len(stk)} 条): {got_p.stdout!r}"
    assert len(set(stk)) == N_THREADS, f"两条线程共用了同一块栈: {stk}"
    print(f"      os/thread: {N_THREADS} 个线程 {want} 次临界区 + join 到齐 + 栈各不同")


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_os_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
