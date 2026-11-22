#!/usr/bin/env python3
"""lvm_test.py — LVM 的 T0 判据（`docs/243` §4）。

**判 A：产物等价。** 同一份程序，`lomelf` 编出来的**原生产物**与 VM 各跑一遍，
比 **stdout 的字节 + 退出码**。不看时间、不看中间表示 —— 与 `loment_elf_test.py`
同一把尺子（那一条比的是 clang 路与 lomelf 路）。

**判 B：支持面相同。** 语料里每一份若 VM 说"不支持"，则 `lomelf` 也必须发不出来 ——
用一张**棘轮**表钉，只许变短。

**判 G：`gc_ladder` 在 VM 档下含义不变。** 上半："加不加 `choose lvm`，IR 逐字节相同"
（没有分配站点被搬动 ⇒ Potato 那个字段不动）。下半：一份四层各来一站的程序，原生产物与
VM 逐字节相同 —— 而且那个收集器必须是 **`define`**（它在 IR 里，不在宿主上）。

**判 F：命令单元看到的是自己。** 读 `/proc/self/cmdline` 的那两份，走**真前门**跑，
断言它们看到的名字是**自己被指到的那条路径**（`docs/243` §3.4）—— 不是解释器的。

用法: python tools/lvm_test.py
"""

from __future__ import annotations

import io
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lomentc  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: ⚠ **语料复用 `loment_elf_test.CORPUS`，不另抄一份** —— 抄了必然漂
#: （仓里对"同一份清单抄第二遍"的判词很硬）。那一批的入选条件正是本格要的：
#: 有 `_start`、会终止、原生后端支持。

#: **棘轮**：VM 跑不了、而原生产物跑得了的那些。**只许变短** ——
#: 每条都要写清"为什么现在跑不了"，哪天跑通了这条判据会红，逼着把条目删掉。
#:
#: 2026-11-22：`selfcheck` 出局了。它要系统调用 **317**（Linux 上是 `seccomp`，
#: 不是 `getrandom`）；原生产物拿到 -1 后自己报 `FAIL getrandom` 并退 1。VM 现在对
#: 表外的号回 **`-ENOSYS`**（`docs/311` §4.2）—— 与 Linux 同一个答案，于是两条路的
#: stdout + 退出码一致。**注意这不是"让 VM 支持 317"**：它仍然不实现，只是不再
#: 把"这个号没有"变成一次崩溃。
VM_KNOWN_GAPS: dict[str, str] = {}


def _run_native(elf: Path, td: Path, argv0: str, timeout: int = 20) -> tuple[int, bytes]:
    """跑原生产物。**Linux 上直接跑；Windows 上借 WSL**（交叉编出来的 ELF 本机跑不了）。

    ⚠ **不能只走 WSL**：门禁跑在 ubuntu 上、那儿没有 `wsl` —— 只用 WSL 的话这条判据
    在 CI 里会**整个 SKIP**，等于永远不跑（`loment_elf_test` 就是那样）。这条的意义
    全在"两条路真的都跑过、产物真的逐字节比过"。

    ⚠ `argv0` **不是可有可无的**（`docs/243` §3.4）。`cmd_hello` 那种命令单元读
    `/proc/self/cmdline` 是为了**看自己叫什么**，而两条路的进程是**两个不同的文件**
    （`prog` 与 VM），各自的名字当然不同。**把 argv[0] 钉成同一个**，比的才是
    "同一份命令行下的行为" —— 这正是判据该量的那个量。不钉的话，比的是两个文件名
    像不像，那是**另一个问题**，而且答案永远是"不像"。
    """
    if sys.platform != "win32":
        p = td / "prog"
        p.write_bytes(elf.read_bytes())
        p.chmod(0o755)
        # POSIX：`executable=` 加上 argv[0] 想写什么就写什么（这是 execve 的语义）。
        r = subprocess.run([argv0], executable=str(p), capture_output=True,
                           timeout=timeout, shell=False)
        # ⚠ **被信号打死时 Python 给的是 `-signo`，不是 shell 的 `128+signo`** ——
        # 与 `lomentc_test._run_elf` 同一条规矩：两条路（本机 Linux / Windows 经 WSL）
        # 必须报同一个数，否则 LMS 的崩溃格只在一边绿（CI 实测 `-4` vs `132`）。
        rc = r.returncode if r.returncode >= 0 else 128 - r.returncode
        return rc, r.stdout
    binn = f"/tmp/lvm_native_{os.getpid()}.bin"
    outp = td / "prog.out"
    script = (f"rm -f {binn} && cp {ELF._wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd /tmp && timeout {timeout} "
              f"env A0={shlex.quote(argv0)} bash -c 'exec -a \"$A0\" {binn}' "
              f"> {ELF._wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 120, shell=False)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


def test_vm_matches_native() -> None:
    """判 A：同名程序，两条路各跑一遍，比 stdout 字节 + 退出码。"""
    if sys.platform == "win32" and not ELF._wsl():
        print("      SKIP: Windows 上没有 WSL —— 交叉编出来的 ELF 本机跑不了")
        return
    ran, gap, out_of_reach = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for rel in ELF.CORPUS:
            src = ROOT / rel
            stem = src.stem
            mod = lomentc.load(src)
            deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
            errs = lomentc.check(mod, deps=deps)
            if errs:
                out_of_reach.append((stem, f"检查不过: {errs[0][:60]}"))
                continue
            ir = lomentc.emit_llvm(mod, ROOT, deps)
            # 原生产物：同一条 `lomelf` 后门 —— 编不出来就是"目标之外"，不是缺口。
            try:
                blob, _ = lomelf.compile_ll(ir, [])
            except (lomelf.ElfError, lomelf.Unsupported) as e:
                out_of_reach.append((stem, str(e)[:60]))
                continue
            elf = Path(td) / f"{stem}.elf"
            elf.write_bytes(blob)
            # 程序"叫什么" = 它在语料里的路径。**两边同一个字符串**（见 `_run_native`）。
            rc_native, out_native = _run_native(elf, Path(td), rel)
            buf = io.BytesIO()
            try:
                rc_vm = lvm.run_ir(ir, buf, None, [rel])
            except Exception as e:  # noqa: BLE001
                gap.append((stem, f"{type(e).__name__}: {e}"[:70]))
                continue
            out_vm = buf.getvalue()
            if (rc_native, out_native) != (rc_vm, out_vm):
                gap.append((stem, f"两条路产物不同: 原生 rc={rc_native} "
                                  f"{out_native[:40]!r} / VM rc={rc_vm} {out_vm[:40]!r}"))
                continue
            ran.append(stem)
    # **棘轮**（两个方向都判）：没登记的分歧 ⇒ 红；登记了却**跑通了** ⇒ 也红（逼着删条目）。
    unregistered = [f"{s}: {w}" for s, w in gap if s not in VM_KNOWN_GAPS]
    assert not unregistered, "这两条路对不上，而它们**没有登记**在 VM_KNOWN_GAPS 里：\n  " \
                             + "\n  ".join(unregistered)
    for stem, why in VM_KNOWN_GAPS.items():
        assert any(s == stem for s, _ in gap), (
            f"{stem}: 登记在 VM_KNOWN_GAPS 里，但它**已经对上了** —— 请删掉那条登记"
            f"（{why[:60]}…）")
    print(f"      两条路逐字节相同: {len(ran)}/{len(ran)}（{', '.join(ran)}）")
    if gap:
        print(f"      已知缺口（棘轮，只许变短）: {', '.join(s for s, _ in gap)}")
    if out_of_reach:
        print(f"      目标之外（参考实现自己也发不出来）: "
              f"{', '.join(s for s, _ in out_of_reach)}")
    assert ran, "一份都没跑成 —— 判据没测到东西"


#: `gc_ladder` 四层**各来一站**，而且**进得去**（有 `_start`）。
#: 形状抄 `loment/examples/gc_ladder/main.lomt` —— 那一份的入口是 `fn main()`，
#: 而 VM 的入口是 `_start`（`docs/243` §3.5），所以不能直接拿它跑。
#: 四层的注释跟着每一处 `alloc` 写：它们**是**那份梯子的四行（`docs/210` §2）。
_LADDER_FIXTURE = """module lvmladder
choose gc_auto_alpha
choose runtime
use bytes

fn _start() -> u32 {
    let a: ptr = alloc(64);          // L0：字面量尺寸 + 只被解引用透过
    store8(a, 0, 9);
    let n: u32 = 8;
    let b: ptr = alloc(n);           // L1：顶层声明、只解引用 -> 最后一次用处之后 free
    store8(b, 0, 3);
    let i: i32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);       // L2：循环体内、名字不出体 -> 体末尾整段退前沿
        store8(c, 0, 1);
        i = i + 1;
    }
    let d: ptr = alloc(n);           // L3：外逃（交给别的变量）—— 上面三层都接不到
    let e: ptr = d;
    store8(e, 0, 1);
    let s: u32 = (load8(a, 0) as u32) + (load8(b, 0) as u32) + (load8(e, 0) as u32);
    syscall4(60, s as u64, 0, 0);
    return 0;
}
"""


def _fixture_ir(text: str, td: Path, name: str) -> str:
    """一段源文本 -> IR（走真前门：检查 + `emit_llvm`）。"""
    src = td / f"{name}.lomt"
    with src.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def test_gc_ladder_under_vm() -> None:
    """判 G：`gc_ladder` 的四层在 VM 档下**含义不变**（issue #331，`docs/232` §4.5）。

    两半各钉一件事，都能证伪：

    * **上半：没有分配站点被搬动。** 同一份源，加不加 `choose lvm`，IR **逐字节相同**。
      这条红了就说明 `lvm` 这一维开始动梯子 —— 那时 Potato 的 `gc_ladder` 字段与它的版本号
      都要按 `docs/231` §2.2 的先例重走一遍登记处。
    * **下半：四层的落地物 VM 跑得对。** 一份四层各来一站的程序（`gc_auto_alpha`），
      原生产物与 VM 逐字节相同。"L3 交给运行期"**不是**把梯子交给 VM：那个收集器是
      **同一份 IR 里 `define` 出来的**，VM 解释的就是它 —— 所以这里顺手把"它必须是
      `define` 而不是 `declare`"也钉住：一旦变成 `declare`，就是有一半被挪到了宿主上。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        base = _fixture_ir(_LADDER_FIXTURE, td, "lvmladder")
        with_vm = _fixture_ir(_LADDER_FIXTURE.replace(
            "choose runtime", "choose runtime\nchoose lvm", 1), td, "lvmladder_vm")
    assert base == with_vm, (
        "`choose lvm` 把 IR 改了 —— 那说明它开始动 `gc_ladder`（`docs/232` §4.5）："
        "Potato 那个字段与它的版本号都要重审")
    # 收集器必须是 `define` —— 它是**这份 IR 的一部分**，不是宿主提供的运行期。
    for fn in ("__loment_alloc", "__loment_collect", "__loment_major", "__loment_wb"):
        assert re.search(rf"^define [^@]*@{re.escape(fn)}\(", base, re.M), (
            f"{fn} 不在这份 IR 的 `define` 里 —— 收集器被挪到宿主上了？"
            "那 §4.4 / §4.5 那两段的账要重算")
    if sys.platform == "win32" and not ELF._wsl():
        print("      SKIP 下半: Windows 上没有 WSL")
        return
    buf = io.BytesIO()
    rc_vm = lvm.run_ir(base, buf, None, ["lvmladder"])
    try:
        blob, _ = lomelf.compile_ll(base, [])
    except (lomelf.ElfError, lomelf.Unsupported) as e:  # noqa: BLE001
        raise AssertionError(f"四层那份原生产物发不出来: {e}") from e
    with tempfile.TemporaryDirectory() as tds2:
        td2 = Path(tds2)
        elf = td2 / "ladder.elf"
        elf.write_bytes(blob)
        rc_native, out_native = _run_native(elf, td2, "lvmladder")
    assert (rc_native, out_native) == (rc_vm, buf.getvalue()), (
        f"四层那份两条路对不上：原生 rc={rc_native} {out_native[:40]!r} / "
        f"VM rc={rc_vm} {buf.getvalue()[:40]!r}")
    assert rc_vm == 13, f"退出码应当是 9+3+1=13（三层各读回一个字节），得到 {rc_vm}"
    print("      `choose lvm` 不改 IR（逐字节同）；四层那份原生 / VM 同为 rc=13")


def test_command_unit_sees_its_own_name() -> None:
    """判 F：`/proc/self/cmdline` 给的是**被解释程序自己的**命令行（issue #330）。

    判 A 是"给两份产物**同一个** argv[0]，行为还一样不一样"；这一条问的是另一件事：
    走**真前门**（`lvm <entry>`）时，VM 挑的那个名字是**什么**。

    这里量的是"VM 把**它被指到的那条路径**当作程序的名字" —— 与它自己的名字无关。
    证伪很直接：一个**透传**实现（把宿主进程的 cmdline 递过去）在这里打印的是
    `pyth`（跑 `lvm.py` 的是 Python），而不是 `lome`；`argc` 也会是 host 的 3 而不是 1。
    所以这两条断言都不是"复述实现"，它们各自钉着一半。
    """
    entry = "loment/examples/cmd_hello.lomt"
    with tempfile.TemporaryDirectory() as td, open(td + "/sink", "wb") as sink:
        real, sys.stdout = sys.stdout, io.TextIOWrapper(sink, encoding="utf-8")
        try:
            rc = lvm.main([entry, "--root", str(ROOT)])
        finally:
            sys.stdout.flush()
            sys.stdout = real
        with open(td + "/sink", "rb") as f:
            got = f.read()
    assert got == entry[:4].encode(), (
        f"命令单元打印出来的应当是**它自己**名字的头 4 字节 {entry[:4]!r}，得到 {got!r} —— "
        "如果是 interpreter 自己的名字，说明 `cmdline` 那一处是透传不是合成")
    assert rc == 1, f"`argc` 应当是 1（只有程序名一个字段），VM 报的退出码是 {rc}"
    print(f"      真前门：命令单元看到的是自己 {entry[:4]!r}，argc=1")


def test_no_start_is_refused() -> None:
    """反例：没有 `_start` 的单元必须**点名拒**，不是静默什么都不做。"""
    ir = "define i64 @f() {\nentry:\n  ret i64 0\n}\n"
    try:
        lvm.run_ir(ir, io.BytesIO())
    except lvm.LvmError as e:
        assert "_start" in str(e), e
        return
    raise AssertionError("没有 _start 却跑起来了")


_CALLEE = """define void @f(i32 %a, i32 %b) {
entry:
  ret void
}
"""


def _refusal(ir: str) -> str:
    """跑一份 IR，返回 `LvmError` 的文本；没被拒就是这个判据红。"""
    try:
        lvm.run_ir(ir, io.BytesIO())
    except lvm.LvmError as e:
        return str(e)
    raise AssertionError(f"这份 IR 跑起来了，而它应当被拒：{ir!r}")


def test_arity_mismatch_is_refused() -> None:
    """判 H（issue #716）：实参个数与形参不符**是错**，T0 不许静默容忍。

    证伪很直接：旧的 `zip(fn.params, argtexts)` 对"少给一个实参"什么都不说（形参留 0），
    对"多给一个"直接丢掉 —— 上面两个反例都会**跑完**。多出来的那个实参故意写成
    未定义的 `%zzz`：个数这条错必须**先**报出来。
    """
    few = _CALLEE + """define i32 @_start() {
entry:
  call void @f(i32 1)
  ret i32 0
}
"""
    extra = _CALLEE + """define i32 @_start() {
entry:
  call void @f(i32 1, i32 2, i32 %zzz)
  ret i32 0
}
"""
    ok = _CALLEE + """define i32 @_start() {
entry:
  call void @f(i32 1, i32 2)
  ret i32 0
}
"""
    for name, ir in (("少一个实参", few), ("多一个实参", extra)):
        got = _refusal(ir)
        assert got == "实参个数与形参不符", f"{name}: 报的是 {got!r}"
    b = io.BytesIO()
    assert lvm.run_ir(ok, b) == 0 and b.getvalue() == b"", "个数正确的调用被误拒了"
    print("      实参个数不符（多/少）⇒ 同一句话拒；个数正确的照常跑")


_SHIFT_OK = """define i32 @_start() {
entry:
  %a = shl i32 5, 40
  %b = lshr i32 5, 40
  %c = ashr i32 -8, 40
  %d = add i32 %a, %b
  %e = add i32 %d, %c
  %z = add i32 %e, 1
  %f = icmp eq i32 %z, 0
  br i1 %f, label %ok, label %bad
ok:
  ret i32 0
bad:
  unreachable
}
"""


def test_negative_shift_is_named_error() -> None:
    """判 I（issue #717）：负移位量是**具名** `LvmError`，不是一条 Python traceback。

    两半都可证伪：负的那三份必须抛 `LvmError`（旧代码抛 `ValueError: negative shift
    count`，连类型都不对）；**正的越界**那三份必须照旧给出同一个值（那是已有一致的行为，
    不许被这一条顺手改掉）。
    """
    for op in ("shl", "lshr", "ashr"):
        ir = f"""define i32 @_start() {{
entry:
  %q = {op} i32 5, -1
  %r = trunc i32 %q to i32
  ret i32 %r
}}
"""
        got = _refusal(ir)
        assert got == "移位量是负数", f"{op}: 报的是 {got!r}"
    b = io.BytesIO()
    rc = lvm.run_ir(_SHIFT_OK, b)
    assert (rc, b.getvalue()) == (0, b""), (
        f"正的越界移位（shl/lshr→0、ashr→全 1）应当照旧成立，得到 rc={rc} {b.getvalue()!r}")
    print("      负移位量 ⇒ `移位量是负数`；正的越界照旧（shl/lshr=0、ashr=全 1）")


# ---------------------------------------------------------------- 入口形状（#1026）
#: `docs/243` §3.5 的四种形状，各一条探针。第一二种必须**跑对**，后两种必须
#: **点名拒** —— 而且不许说"没有 `_start`"（那句话对一份好程序而言真而无用）。
_ENTRY_START = """define i32 @_start() {
entry:
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 42, i64 0, i64 0)
  ret i32 0
}
"""

#: `command_main(argv, argc)`：返回 `argc*10 + argv[1][0]`。`argv = ["prog","a","b"]`
#: ⇒ `3*10 + 'a' = 127` —— 两个形参都真的来自**调用方给的**命令行。
_ENTRY_COMMAND = """define i32 @command_main(ptr %argv, i32 %argc) {
entry:
  %c = mul i32 %argc, 10
  %p = getelementptr i8, ptr %argv, i64 5
  %b = load i8, ptr %p
  %z = zext i8 %b to i32
  %s = add i32 %c, %z
  ret i32 %s
}
"""

_ENTRY_INTERRUPT = """define x86_intrcc void @isr(ptr byval([8 x i8]) %__frame) {
entry:
  ret void
}
"""

_ENTRY_THREAD = """define void @spawner() {
entry:
  %r = call i64 asm sideeffect "thread_spawn @worker", "={ax},{si},{r9},{r12},~{di},~{dx},~{r10},~{r8},~{cx},~{r11},~{memory}"(i64 0, i64 0, i64 0)
  ret void
}
define void @worker(i64 %arg) {
entry:
  ret void
}
"""


def test_entry_shape_start_runs() -> None:
    """形状 1：`_start` 照旧是入口（三档同答，rc 来自程序自己的 `exit`）。"""
    for tier in ("t0", "t2", "t3"):
        b = io.BytesIO()
        rc = lvm.run_ir(_ENTRY_START, b, None, ["p"], tier)
        assert (rc, b.getvalue()) == (42, b""), (tier, rc, b.getvalue())
    print("      `_start`：t0 / t2 / t3 都跑到 exit(42)")


def test_entry_shape_command_main_runs_with_caller_argv() -> None:
    """形状 2：`command_main(argv, argc)` **支持**，argv 由调用方给（`docs/223` §3.2）。

    证伪很直接：参数没喂进去 ⇒ `argc=0`、`argv` 指向空处，答案不是 127；
    只喂 `argc` 不铺 cmdline ⇒ 少一个 `'a'`。
    """
    for tier in ("t0", "t2", "t3"):
        b = io.BytesIO()
        rc = lvm.run_ir(_ENTRY_COMMAND, b, None, ["prog", "a", "b"], tier)
        assert (rc, b.getvalue()) == (127, b""), (tier, rc, b.getvalue())
    print("      `command_main`：argc=3、argv[1]='a' ⇒ rc=127（三档同答）")


def _entry_refusal(ir: str, tier: str) -> str:
    try:
        lvm.run_ir(ir, io.BytesIO(), None, ["p"], tier)
    except lvm.LvmError as e:
        return str(e)
    raise AssertionError(f"{tier}: 这份 IR 跑起来了，而它应当被点名拒")


def test_entry_shape_interrupt_is_refused_by_name() -> None:
    """形状 3：中断入口**点名拒**，报文里有入口名与定义它的文档（`docs/149` M70）。

    关键是否定面：**不许**说"没有 `_start`" —— 程序是好的，是引擎不跑这个形状。
    """
    for tier in ("t0", "t2", "t3"):
        msg = _entry_refusal(_ENTRY_INTERRUPT, tier)
        assert "isr" in msg and "interrupt" in msg and "docs/149" in msg, (tier, msg)
        assert "没有 `_start`" not in msg, (tier, msg)
    print("      中断入口 `isr`：三档都点名拒，报文含入口名 + docs/149，不说「没有 _start」")


def test_entry_shape_thread_spawn_target_is_refused_by_name() -> None:
    """形状 4：线程入口**点名拒**（`docs/245`；并发是后续里程碑）。"""
    for tier in ("t0", "t2", "t3"):
        msg = _entry_refusal(_ENTRY_THREAD, tier)
        assert "worker" in msg and "thread_spawn" in msg and "docs/245" in msg, (tier, msg)
        assert "没有 `_start`" not in msg, (tier, msg)
    print("      线程入口 `worker`：三档都点名拒，报文含入口名 + docs/245，不说「没有 _start」")


# ---------------------------------------------------------------- guest ABI（`docs/311`）
#: 表外的号：两个都**在 Linux 上也不存在**，所以期望值是同一个 `-ENOSYS`（-38）。
#: 进场用 `command_main`：`run_ir` 对 `_start` 一律回 0，量不到"guest 拿到什么"。
_ENOSYS_PROBE = """define i32 @command_main(ptr %argv, i32 %argc) {
entry:
  %a = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 317, i64 0, i64 0, i64 0)
  %b = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 9999, i64 0, i64 0, i64 0)
  %ca = icmp eq i64 %a, -38
  %cb = icmp eq i64 %b, -38
  %c = and i1 %ca, %cb
  %z = zext i1 %c to i32
  %bad = sub i32 1, %z
  ret i32 %bad
}
"""


def test_unknown_syscall_is_enosys() -> None:
    """判 J（`docs/311` §4.2）：表外的号回 **`-ENOSYS`**，不是 `die`。

    证伪很直接：旧实现抛 `LvmError`（工具退 2、guest 什么也没拿到）；这条探针要求
    guest 自己看见 `-38` 并把结果算进退出码 —— 0 才是"两个号都回了 -ENOSYS"。
    """
    b = io.BytesIO()
    rc = lvm.run_ir(_ENOSYS_PROBE, b, None, ["p"])
    assert (rc, b.getvalue()) == (0, b""), (
        f"表外的号没有回 -ENOSYS：rc={rc} out={b.getvalue()!r}")


def test_syscall_report_lists_number_program_site() -> None:
    """判 K（`docs/311` §4.2）：`--syscall-report` 把"哪条没实现"写成可数的产物。

    一行一条：`nr<TAB>类<TAB>程序<TAB>fn/blk/idx`。两半都证伪：缺了某一列这条就红；
    同一个站点在循环里重复命中**只记一次**（否则报告会随程序长度膨胀）。
    """
    rep = lvm.SyscallReport()
    rc = lvm.run_ir(_ENOSYS_PROBE, io.BytesIO(), None, ["prog"], report=rep)
    assert rc == 0, rc
    lines = rep.text().splitlines()
    assert len(lines) == 2, f"应当两条（317 与 9999），得到 {lines}"
    for nr in (317, 9999):
        line = next((x for x in lines if x.startswith(f"{nr}\t")), None)
        assert line is not None, f"报告里没有号 {nr}: {lines}"
        cols = line.split("\t")
        assert len(cols) == 4 and cols[1] == "?", f"列不对: {line!r}"
        assert cols[2] == "prog", f"程序名不对: {line!r}"
        assert cols[3].startswith("command_main/entry/"), f"站点不对: {line!r}"
    # 同一站点两遍只记一次（把探针跑两次，报告不长大）
    rep2 = lvm.SyscallReport()
    lvm.run_ir(_ENOSYS_PROBE, io.BytesIO(), None, ["prog"], report=rep2)
    lvm.run_ir(_ENOSYS_PROBE, io.BytesIO(), None, ["prog"], report=rep2)
    assert len(rep2.text().splitlines()) == 2, rep2.text()
    print("      表外的号 ⇒ -ENOSYS，报告两行（号/程序/站点），同一站点只记一次")


_TIME_PROBE = """define i32 @command_main(ptr %argv, i32 %argc) {
entry:
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 201, i64 0, i64 0, i64 0)
  %c = icmp eq i64 %r, -38
  %z = zext i1 %c to i32
  ret i32 %z
}
"""


def test_guest_abi_and_host_profiles() -> None:
    """判 L（`docs/311` §5 L1/L2）：`GUEST_ABI` 是单源，宿主剖面只影响 F 类。

    三条，各能单独红：

    * **L1 静态包含**：`lomt/lvm.lomt` 的 `vm_syscall` 转给宿主的号 ⊆ `PE_DISPATCH`。
      这条抓的正是本轮的病根（"VM 转发了一个 PE 没有的号"），而且不需要 Windows。
    * **表本身成形**：号不重复、四类只有 S/E/F/R。
    * **L2 宿主剖面**：同一份语料 `host=elf` 与 `host=pe` 的 stdout+rc 相同；而一个
      **拿不到 201 的剖面**上，guest 仍然只拿到 `-ENOSYS`（不是 die、不是 0）。
    """
    fwd = lvm.forwarded_host_numbers()
    missing = fwd - set(lomelf.PE_DISPATCH)
    assert not missing, (
        f"`vm_syscall` 转发了 PE 没有的号: {sorted(missing)} —— 编成 PE 时 guest 会拿到 -38，"
        "而同一份 IR 在 ELF 上正常（`docs/311` §4.3）")
    assert fwd, "一个宿主号都没扫到 —— 静态判据没测到东西"
    nrs = [nr for nr, _n, _k in lvm.GUEST_ABI]
    assert len(nrs) == len(set(nrs)), f"GUEST_ABI 有重复的号: {nrs}"
    assert {k for _n, _n2, k in lvm.GUEST_ABI} <= {"S", "E", "F", "R"}, \
        lvm.GUEST_ABI
    # L2：同一份语料，两档宿主剖面同一个答案（语料不打印 pid/时间，见 docs/311 §5 L2）
    ran = 0
    for rel in ELF.CORPUS:
        src = ROOT / rel
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        if lomentc.check(mod, deps=deps):
            continue
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        b0, b1 = io.BytesIO(), io.BytesIO()
        r0 = lvm.run_ir(ir, b0, None, [rel], host=lvm.host_profile("elf"))
        r1 = lvm.run_ir(ir, b1, None, [rel], host=lvm.host_profile("pe"))
        assert (r0, b0.getvalue()) == (r1, b1.getvalue()), (
            f"{rel}: host=elf 与 host=pe 不一致："
            f"elf rc={r0} {b0.getvalue()[:40]!r} / pe rc={r1} {b1.getvalue()[:40]!r}")
        ran += 1
    assert ran, "一份都没跑成 —— L2 判据没测到东西"
    # 拿不到 201 的剖面：guest 仍拿到 -ENOSYS，且报告记的是 F 类
    thin = lvm.HostProfile("thin", frozenset({1, 60}))
    rep = lvm.SyscallReport()
    rc = lvm.run_ir(_TIME_PROBE, io.BytesIO(), None, ["p"], host=thin, report=rep)
    assert rc == 1, f"宿主给不了 201 时 guest 应当看见 -ENOSYS，rc={rc}"
    assert rep.text().splitlines() == ["201\tF\tp\tcommand_main/entry/0"], rep.text()
    rc_elf = lvm.run_ir(_TIME_PROBE, io.BytesIO(), None, ["p"],
                        host=lvm.host_profile("elf"))
    assert rc_elf == 0, f"elf 剖面应当能拿到时间，rc={rc_elf}"
    print(f"      宿主号 ⊆ PE_DISPATCH {sorted(fwd)}；{ran} 份语料 elf/pe 同答；"
          f"缺号 ⇒ guest 拿 -ENOSYS")


def test_syscall_report_cli_writes_the_file() -> None:
    """`--syscall-report PATH` 真的落盘（CLI 接线，不是只测类）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = td / "probe.ll"
        ll.write_text(_ENOSYS_PROBE, encoding="utf-8", newline="\n")
        out = td / "report.txt"
        with open(td / "sink", "wb") as sink:
            real, sys.stdout = sys.stdout, io.TextIOWrapper(sink, encoding="utf-8")
            try:
                rc = lvm.main([str(ll), "--ir", "--syscall-report", str(out)])
            finally:
                sys.stdout.flush()
                sys.stdout = real
        assert rc == 0, rc
        got = out.read_text(encoding="utf-8")
        assert got.splitlines() and got.splitlines()[0].startswith("317\t"), got
    print("      --syscall-report 落盘：317 那条在")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_test")


if __name__ == "__main__":
    sys.exit(main())
