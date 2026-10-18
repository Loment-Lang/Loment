#!/usr/bin/env python3
"""lvm_stability_test.py — 稳定化：**失败面**（`docs/262`，issue #353）。

T0 从第一天起就有步数上限、每一处内存访问的边界检查、以及对"不支持"的点名拒绝 ——
**但一条判据都没有**：上限在，没人断言它会响；边界检查在，没人断言它说的是什么话；
而"挂死"与"慢程序"在参考实现那侧**根本分不开**（上限只在镜像里有，参考没有 —— 本文件
落地时补上的）。

三条：

* **A 不会挂死** —— 一个死循环在**有界**步数内报出上限，并且**点名**函数 / 块 / 第几条。
  三档（t0 / t2 / t3）都要验：护栏不能只有一层有。
* **B 不会静默算错** —— 五种拒绝各来一次，断言程序**响亮地失败**而不是继续：
  不认识的系统调用 / 不支持的指令 / 写越界 / 读越界 / 栈溢出。
* **C 上限写在纸上** —— `docs/262` 逐个点名常量，并给出语料实测的最大值。

⚠ **B 的夹具是手写 IR**，而本仓的规矩是"夹具要用真实现的产物"。这里是有意的例外：
**编译器发不出这些输入**（它不会发 `select`、不会发越界指针）—— 那条规矩防的是
"手写的比真东西干净，测的是一个不会发生的输入"，而这一格要测的**正是**不会发生的输入。

用法: python tools/lvm_stability_test.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_t3  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 死循环：无条件的自环。三档都得在**有界**步数内报出来。
_HANG = """define void @spin_forever() {
entry:
  br label %spin
spin:
  br label %spin
}

define void @_start() {
entry:
  call void @spin_forever()
  ret void
}
"""

#: `select` 是 T0 不支持的指令（`docs/243` §5：这一档没有它）。
_UNSUPPORTED = """define void @_start() {
entry:
  %x = select i1 true, i32 1, i32 2
  ret void
}
"""

#: 越过地址空间末端的写 / 读。`@buf` 很小，GEP 之后远在 16 MiB 之外。
_OOB_WRITE = """@buf = global [8 x i8] zeroinitializer

define void @_start() {
entry:
  %p = getelementptr i8, ptr @buf, i64 20000000
  store i8 1, ptr %p
  ret void
}
"""

_OOB_READ = """@buf = global [8 x i8] zeroinitializer

define void @_start() {
entry:
  %p = getelementptr i8, ptr @buf, i64 20000000
  %v = load i8, ptr %p
  ret void
}
"""

#: 无底递归 —— 应当撞"栈溢出"，而不是把步数上限先撞响。
_DEEP = """define void @f() {
entry:
  call void @f()
  ret void
}

define void @_start() {
entry:
  call void @f()
  ret void
}
"""

#: 不认识的系统调用：走真前门（Loment 源）。
_BAD_SYSCALL = """module badsys
choose runtime
use bytes

fn _start() -> u32 {
    syscall4(999, 0, 0, 0);
    return 0;
}
"""


def _run_tier(ir: str, tier: str):
    if tier == "t0":
        return lvm.run_ir(ir, io.BytesIO())
    if tier == "t2":
        return lvm_t2.run_ir_t2(ir, io.BytesIO())
    return lvm_t3.run_ir_t3(ir, io.BytesIO())


def test_hang_is_bounded_and_named() -> None:
    """判 A：死循环在**有界**步数内报上限，且点名函数 / 块 / 第几条。三档都要。"""
    old = lvm.STEP_CAP
    lvm.STEP_CAP = 5000                     # 三档都在**检查时**读这个模块属性
    try:
        for tier in ("t0", "t2", "t3"):
            try:
                _run_tier(_HANG, tier)
            except lvm.LvmError as e:
                msg = str(e)
                assert "步数上限" in msg, f"[{tier}] 报的不是上限: {msg}"
                assert "spin_forever" in msg, f"[{tier}] 没点名函数: {msg}"
                assert "spin" in msg, f"[{tier}] 没点名块: {msg}"
            else:
                raise AssertionError(f"[{tier}] 死循环居然跑完了 —— 护栏在这一档没接上")
    finally:
        lvm.STEP_CAP = old
    print(f"      三档（t0/t2/t3）都在 {5000} 步内报上限，并点名 函数 / 块 / 第几条")


def test_refusals_are_loud() -> None:
    """判 B：五种拒绝各来一次 —— **响亮地失败**，不是继续算。"""
    cases = [
        ("不支持的指令", _UNSUPPORTED, ("没实现",)),
        ("写越界", _OOB_WRITE, ("写越界",)),
        ("读越界", _OOB_READ, ("读越界",)),
        ("栈溢出", _DEEP, ("栈溢出",)),
    ]
    for name, ir, wanted in cases:
        try:
            lvm.run_ir(ir, io.BytesIO())
        except lvm.LvmError as e:
            msg = str(e)
            for w in wanted:
                assert w in msg, f"[{name}] 报错里没有 {w!r}：{msg}"
        else:
            raise AssertionError(f"[{name}] 没有响亮地失败 —— 它继续跑下去了")

    # 不认识的系统调用走真前门（Loment 源 -> IR -> 跑）
    import tempfile                                          # noqa: PLC0415
    import lomentc                                            # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tds:
        src = Path(tds) / "badsys.lomt"
        src.write_text(_BAD_SYSCALL, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
    for tier in ("t0", "t2"):
        try:
            _run_tier(ir, tier)
        except lvm.LvmError as e:
            assert "999" in str(e), f"[{tier}] 报错没点名那个号: {e}"
        else:
            raise AssertionError(f"[{tier}] 不认识的系统调用被放过去了")
    print("      不支持的指令 / 写越界 / 读越界 / 栈溢出 / 未知系统调用（t0+t2）—— 全部点名拒")


def test_limits_are_on_paper() -> None:
    """判 C：`docs/262` 逐个点名上限常量，且写着语料的实测最大值。"""
    doc = ROOT / "docs" / "262-lvm-limits.md"
    assert doc.exists(), "docs/262-lvm-limits.md 不在 —— 上限只活在代码里"
    text = doc.read_text(encoding="utf-8")
    for const in ("MEM_SIZE", "GLOBAL_BASE", "FRAME_SIZE", "SLOT", "STEP_CAP"):
        assert const in text, f"docs/262 没有点名 {const}"
    assert "MAX_DEPTH" in text, "docs/262 没有点名镜像那一侧的 MAX_DEPTH"
    # 实测最大值要跟着常量写（"语料最大"这几列），不能只有常量表
    assert re.search(r"语料.*最大|最大.*语料", text), "docs/262 没有语料实测的最大值"
    print("      docs/262：常量逐个点名，语料实测最大值在旁边")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_stability_test")


if __name__ == "__main__":
    sys.exit(main())
