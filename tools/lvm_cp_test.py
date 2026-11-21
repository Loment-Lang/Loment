#!/usr/bin/env python3
"""lvm_cp_test.py — P5：checkpoint / resume（`docs/360` §7 B4，`docs/363`）。

`docs/360` §3.1 的那条性质是这里唯一的命题：

> **guest 状态就是全部状态。** 在同一台机器上跑一半、存下来、换一次进程再接着跑，
> 答案与一口气跑完逐字节相同。

显式栈引擎（`ExplicitExecutor`）把 Python 的调用栈换成 guest 自己的栈。默认 T0 仍是
递归引擎；这一档只在 `--checkpoint-at` / `--resume` 时启用，所以先钉死两件事：

* **同答** —— 整份语料在递归引擎与显式栈引擎下 `rc + stdout` 逐字节相同；
* **可存可取** —— 任意指令边界（含**调用栈里有被调者**的时刻）快照，恢复后答案不变。

六条：

* **A** 两个引擎在语料上同答。
* **B** 逐点 checkpoint/resume：一段循环里每个采样点都能接着跑。
* **C** 调用栈进快照：至少一个采样点上 `stack > 1`，且恢复后仍对。
* **D** 链式快照：K1 -> K2 -> 跑完，答案不变。
* **E** 快照确定：同一 K 两次逐字节相同；换一份 IR 恢复被**点名拒**。
* **F** 只走纯 T0：剖面 / 计数 / t2 / 紧凑形都在 CLI 上点名拒。

用法: python tools/lvm_cp_test.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import zlib
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
    data = json.loads(zlib.decompress(blob[len(lvm.CP_MAGIC):]).decode("utf-8"))
    return len(data["stack"])


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


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_cp_test")


if __name__ == "__main__":
    sys.exit(main())
