#!/usr/bin/env python3
"""lvm_cp_test.py — P5：checkpoint / resume（`docs/360` §7 B4，`docs/363`）。

`docs/360` §3.1 的那条性质是这里唯一的命题：

> **guest 状态就是全部状态。** 在同一台机器上跑一半、存下来、换一次进程再接着跑，
> 答案与一口气跑完逐字节相同。

显式栈引擎（`ExplicitExecutor`）把 Python 的调用栈换成 guest 自己的栈。默认 T0 仍是
递归引擎；这一档只在 `--checkpoint-at` / `--resume` 时启用，所以先钉死两件事：

* **同答** —— 整份语料在递归引擎与显式栈引擎下 `rc + stdout` 逐字节相同；
* **可存可取** —— 任意指令边界（含**调用栈里有被调者**的时刻）快照，恢复后答案不变。

七条：

* **A** 两个引擎在语料上同答。
* **B** 逐点 checkpoint/resume：一段循环里每个采样点都能接着跑。
* **C** 调用栈进快照：至少一个采样点上 `stack > 1`，且恢复后仍对。
* **D** 链式快照：K1 -> K2 -> 跑完，答案不变。
* **E** 快照确定：同一 K 两次逐字节相同；换一份 IR 恢复被**点名拒**。
* **F** 只走纯 T0：剖面 / 计数 / t2 / 紧凑形都在 CLI 上点名拒。
* **G** `sw_tag` 的对象表与 4 位标签一起进快照（`docs/364`）。
* **H** 合成文件（guest 自己的 `/proc/self/cmdline`）的**读游标**进快照：
  停在 read 中间，恢复后从同一位置接着读（W5，`docs/360` §4）。
* **I** dismemory 也能存/取：帧是体、值是格（`flags = 3`，`docs/375` §2.5）。
  显式栈引擎与递归引擎在合法程序上同答；停在任何采样点恢复后 rc/stdout 不变；
  恢复后撞上的故障（退休令牌）码 + 站点也同。

用法: python tools/lvm_cp_test.py
"""

from __future__ import annotations

import contextlib
import io
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_elf_test as ELF  # noqa: E402
import lvm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 循环 + 函数调用：`add` 在每一轮里被调一次，所以有大量「栈深 > 1」的指令边界。
_LOOP = """module cp_prog
choose runtime

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 10 {
        s = add(s, i);
        i = i + 1;
    }
    syscall4(60, s as u64, 0, 0);
}
"""

#: 另一种程序：换一份 IR 来证明「快照不认识别的字节码」。
_OTHER = """module cp_other
choose runtime

fn _start() {
    syscall4(60, 7, 0, 0);
}
"""

#: 带堆分配/释放的循环：`sw_tag` 的对象与 4 位标签必须一起进快照。
_ALLOC_LOOP = """module cp_alloc
choose runtime
use bytes

fn _start() {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 6 {
        let p: ptr = alloc(16);
        store32(p, 0, i + 1);
        s = s + load32(p, 0);
        free(p);
        i = i + 1;
    }
    syscall4(60, s as u64, 0, 0);
}
"""


#: 读 `/proc/self/cmdline`：先读 8 字节，循环一轮之后读剩下的 —— 停在中间时，
#: 恢复后必须**从同一游标**接着读（W5）。
_CMDLINE = """module cp_cmdline
choose runtime

fn _start() {
    let p: ptr = alloc(64);
    let at: i64 = (0 as i64) - (100 as i64);
    let fd: i64 = syscall4(257, at as u64, str_ptr("/proc/self/cmdline") as u64, 0 as u64);
    let n1: i64 = syscall4(0, fd as u64, p as u64, 8 as u64);
    let i: u32 = 0;
    while i < 20 {
        i = i + 1;
    }
    let n2: i64 = syscall4(0, fd as u64, (p as u64) + (8 as u64), 40 as u64);
    let n: u64 = (n1 + n2) as u64;
    syscall4(1, 1 as u64, p as u64, n);
    syscall4(3, fd as u64, 0 as u64, 0 as u64);
    syscall4(60, 0 as u64, 0 as u64, 0 as u64);
}
"""


#: dismemory 的存/取夹具：数组 + 嵌套调用 + alloc/free + 输出。
_DIS_CP = """module cp_dis
choose lvm_dismemory_true
choose runtime
use bytes

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn bump(p: ptr, k: u32) {
    let v: u32 = load32(p, 0);
    store32(p, 0, add(v, k));
}

fn _start() {
    let xs: [u32; 8] = [1, 2, 3, 4, 5, 6, 7, 8];
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 8 {
        s = add(s, xs[i]);
        i = i + 1;
    }
    let p: ptr = alloc(16);
    store32(p, 0, s);
    bump(p, 1);
    let v: u32 = load32(p, 0);
    free(p);
    print("sum=");
    print("ok");
    print("\\n");
    syscall4(60, v as u64, 0, 0);
}
"""

#: 恢复后必须撞上**同一个故障**：被调者返回后，它的 `alloca` 令牌已退休。
#: 这种形状在 Loment 源里被静态检查器拒（`变量 p ... 已经被 free`），所以用手写 IR
#: —— 与 `lvm_dismemory_test` 的悬垂夹具同一条理由。
_DIS_UAF = """; loment-memory: dismemory

define ptr @mk() {
entry:
  %p = alloca [4 x i8]
  ret ptr %p
}

define void @_start() {
entry:
  %p = call ptr @mk()
  %v = load i8, ptr %p
  ret void
}
"""


def _compile(text: str, name: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{name}.lomt"
        src.write_text(text, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{name} 检查不过：{errs[:2]}"
        return lomentc.emit_llvm(mod, ROOT, deps)


def _run(ir: str, at=None, resume=None, substrate="none", argv=None):
    out = io.BytesIO()
    rc, blob = lvm.run_checkpointed(ir, out, substrate, at, resume,
                                    argv if argv is not None else ["prog"])
    return rc, out.getvalue(), blob


def _stack_depth(blob: bytes) -> int:
    return lvm.cp_frame_count(blob)


def test_dismemory_snapshot_roundtrip() -> None:
    """判 I（`docs/375` §2.5）：没有地址那一档也能存/取。

    两件事一起钉：**显式栈引擎在 dismemory 下与递归引擎同答**（它是快照那一档的
    运行器），以及停在任何采样点恢复后的 `rc + stdout` 与一口气跑完相同。
    """
    ir = _compile(_DIS_CP, "cp_dis")
    out = io.BytesIO()
    base = (lvm.run_ir(ir, out), out.getvalue())
    out = io.BytesIO()
    got = _run(ir)
    assert (got[0], got[1]) == base, ("显式栈引擎与递归引擎在 dismemory 下不同答",
                                      got[:2], base)
    for at in (0, 5, 17, 60):
        rc1, out1, blob = _run(ir, at=at)
        assert blob, (at, "没有快照")
        rc2, out2, _ = _run(ir, resume=blob)
        assert (rc2, out1 + out2) == base, (at, rc2, (out1 + out2)[:40], base[1][:40])
    print(f"      dismemory：递归/显式同答 {base[0]}；at=0/5/17/60 恢复后 rc/stdout 同")


def test_dismemory_snapshot_keeps_the_fault() -> None:
    """判 I（故障面）：恢复后的执行撞上**同一处**退休令牌（F02），码与站点同。"""
    ir = _DIS_UAF
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = io.BytesIO()
        rc = lvm.run_ir(ir, out)
    one = (rc, _first_line(err.getvalue()))
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        rc1, _o1, blob = _run(ir, at=1)
    err3 = io.StringIO()
    with contextlib.redirect_stderr(err3):
        rc2, _o2, _b2 = _run(ir, resume=blob)
    resumed = (rc2, _first_line(err3.getvalue()))
    assert one == resumed, (one, resumed, err2.getvalue()[-200:])
    print("      恢复后仍是同一处 F02：码 + 站点逐字同")


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text.splitlines() else ""


def test_engines_agree_on_corpus() -> None:
    """判 A：递归引擎与显式栈引擎在整份语料上 `rc + stdout` 逐字节同。"""
    compared = 0
    for rel in ELF.CORPUS:
        src = ROOT / rel
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        if lomentc.check(mod, deps=deps):
            continue
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        out = io.BytesIO()
        try:
            rc = lvm.run_ir(ir, out, None, [str(src)], "t0")
            rec = (rc, out.getvalue())
        except Exception as e:                        # noqa: BLE001
            rec = ("EXC", f"{type(e).__name__}: {e}")
        out = io.BytesIO()
        try:
            rc, _ = lvm.run_checkpointed(ir, out, "none", None, None, [str(src)])
            exp = (rc, out.getvalue())
        except Exception as e:                        # noqa: BLE001
            exp = ("EXC", f"{type(e).__name__}: {e}")
        assert rec == exp, f"{rel}: 递归={rec!r} 显式={exp!r}"
        compared += 1
    assert compared >= len(ELF.CORPUS) - 1, f"只比了 {compared} 份语料"
    print(f"      {compared} 份语料：递归引擎与显式栈引擎逐字节同答")


def test_checkpoint_resume_at_every_sample() -> None:
    """判 B：循环里逐个采样点快照，恢复后答案不变（rc=45）。"""
    ir = _compile(_LOOP, "cp_prog")
    base_rc, base_out, _ = _run(ir)
    assert base_rc == 45, f"夹具的答案变了：{base_rc}"
    points = 0
    for at in list(range(0, 240, 7)) + [1, 2, 3, 239, 240]:
        rc, _out, blob = _run(ir, at=at)
        if blob is None:
            continue
        rc2, out2, blob2 = _run(ir, resume=blob)
        assert blob2 is None, f"at={at}: 恢复之后又停了"
        assert (rc2, out2) == (base_rc, base_out), f"at={at}: 恢复后答案变了（{rc2}）"
        points += 1
    assert points >= 20, f"只验了 {points} 个点 —— 采样太稀"
    print(f"      {points} 个指令边界：存 -> 取 -> 跑完，rc 与 stdout 逐字节同")


def test_call_stack_is_in_the_snapshot() -> None:
    """判 C：至少一个采样点上调用栈深 > 1，并且从那个快照恢复仍然对。"""
    ir = _compile(_LOOP, "cp_prog")
    base = _run(ir)[:2]
    deep = None
    for at in range(0, 240, 1):
        rc, _out, blob = _run(ir, at=at)
        if blob is None:
            continue
        if _stack_depth(blob) > 1:
            deep = (at, blob)
            break
    assert deep is not None, "没有任何采样点落在被调者里面 —— 夹具或引擎有问题"
    at, blob = deep
    rc, out, _ = _run(ir, resume=blob)
    assert (rc, out) == base, f"栈深 > 1 的快照恢复后答案变了（at={at}）"
    print(f"      at={at} 的栈深为 {_stack_depth(blob)}：被调者也在快照里，恢复后 rc 不变")


def test_chained_checkpoints() -> None:
    """判 D：K1 -> K2 -> 跑完。中间状态全部经序列化，答案不变。"""
    ir = _compile(_LOOP, "cp_prog")
    base = _run(ir)[:2]
    rc1, _o1, b1 = _run(ir, at=30)
    assert b1 is not None
    rc2, _o2, b2 = _run(ir, at=60, resume=b1)
    assert b2 is not None, f"第二段没有停住（rc={rc2}）"
    rc3, out3, b3 = _run(ir, resume=b2)
    assert b3 is None and (rc3, out3) == base, f"第三段答案变了：{(rc3, out3)!r}"
    print("      30 -> 60 -> 跑完：两次快照、两次恢复，rc 与 stdout 同")


def test_snapshot_deterministic_and_ir_bound() -> None:
    """判 E：同一 K 两次快照逐字节同；换一份 IR 恢复被点名拒。"""
    ir = _compile(_LOOP, "cp_prog")
    _rc, _o, b1 = _run(ir, at=40)
    _rc, _o, b2 = _run(ir, at=40)
    assert b1 == b2, "同一份输入、同一个点，快照不一样"
    other = _compile(_OTHER, "cp_other")
    try:
        _run(other, resume=b1)
    except lvm.LvmError as e:
        assert "IR" in str(e), f"拒文没有点名 IR：{e}"
    else:
        raise AssertionError("换了一份 IR 还能恢复 —— 快照没有绑定字节码")
    print("      同一 K 两次逐字节同；换 IR 恢复被点名拒")


def test_checkpoint_carries_substrate_state() -> None:
    """判 G：`sw_tag` 的对象表与标签一起进快照；中途存、恢复，答案不变。"""
    ir = _compile(_ALLOC_LOOP, "cp_alloc")
    rc0, out0, _ = _run(ir, substrate="sw_tag")
    for at in (20, 40, 60, 80, 100, 140, 180):
        rc1, _o1, blob = _run(ir, at=at, substrate="sw_tag")
        if blob is None:
            continue
        rc2, out2, blob2 = _run(ir, resume=blob, substrate="sw_tag")
        assert blob2 is None, f"at={at}: 恢复之后又停了"
        assert (rc2, out2) == (rc0, out0), f"at={at}: 恢复后答案变了（{rc2}）"
        print(f"      sw_tag + checkpoint at={at}：对象与标签都过了快照，rc 不变")
        return
    raise AssertionError("没有找到停点 —— 夹具太短")


def test_checkpoint_only_on_plain_t0() -> None:
    """判 F：剖面 / 计数 / t2 / 紧凑形在 CLI 上点名拒。"""
    src = ROOT / "loment/examples/user_hello.lomt"
    with tempfile.TemporaryDirectory() as td:
        seal = Path(td) / "cp.bin"
        cases = [
            ["--tier", "t2", "--checkpoint-at", "1", "--checkpoint-out", str(seal), str(src)],
            ["--checkpoint-at", "1", "--checkpoint-out", str(seal),
             "--profile-out", str(Path(td) / "p.txt"), str(src)],
            ["--checkpoint-at", "1", "--checkpoint-out", str(seal),
             "--counters-out", str(Path(td) / "c.txt"), str(src)],
            ["--checkpoint-at", "1", str(src)],
        ]
        for args in cases:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = lvm.main(args)
            assert rc == 2, f"{args}: 没有拒（rc={rc}）"
    print("      t2 / 剖面 / 计数 / 缺 --checkpoint-out：四项都点名拒")


def test_checkpoint_carries_the_command_line_cursor() -> None:
    """判 H（W5）：停在 read 中间，恢复后从同一游标接着读（`docs/360` §4）。"""
    ir = _compile(_CMDLINE, "cp_cmdline")
    argv = ["checkpoint-cmdline-prog"]
    want_rc, want_out, _ = _run(ir, argv=argv)
    assert want_rc == 0 and want_out.endswith(b"\0"), (want_rc, want_out)
    open_pts: list[int] = []
    mid_pts: list[int] = []
    for at in (0, 10, 20, 30, 40, 45, 50):
        rc1, out1, blob = _run(ir, at, argv=argv)
        assert blob is not None, f"{at} 处没有停"
        if struct.unpack_from("<I", blob, 16 + lvm.CTX_VFD_OPEN)[0]:
            open_pts.append(at)
            if struct.unpack_from("<I", blob, 16 + lvm.CTX_VFD_POS)[0] == 8:
                mid_pts.append(at)
        rc2, out2, blob2 = _run(ir, None, blob, argv=argv)
        assert blob2 is None
        assert (rc2, out1 + out2) == (want_rc, want_out), (
            f"at={at}: rc={rc2} out={out1 + out2!r} vs {want_out!r}")
    assert open_pts, "没有采样点落在 open 之后 —— 这条判据没量到合成文件"
    assert mid_pts, "没有采样点停在第一次 read 之后 —— 这条判据没量到游标"
    print(f"      停点 {mid_pts} 停在读到 8 字节处：游标与字节都恢复")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_cp_test")


if __name__ == "__main__":
    sys.exit(main())
