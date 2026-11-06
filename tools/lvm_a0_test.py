#!/usr/bin/env python3
"""lvm_a0_test.py — A0 寄存器分配与**取帧重建**的判据（`docs/248` §3.1，issue #648 / #649）。

A0 把"每个 SSA 值一个栈槽"换成"值住在寄存器里，槽只在**块边界**与**守卫之前**写"。
这一换把 `docs/249` §4 那条承重不变式从**空成立**变成**真的要求**：槽机之下值本来就在
槽里，守卫之前"已经落槽"这句话没有内容；寄存器之下它是去优化能不能答对的分界线。

五条，缺一条这套就退化成"看起来像 A0"：

* **A 逐字节相同** —— 语料每一份，A0 与槽机的 stdout + 退出码相同（`docs/248` §3.1：
  A 堆只改"值住在哪儿"，不改算出来的东西）。
* **B 真的更快** —— 同一进程里交替跑，A0 必须**快于**槽机。只判符号，倍数只记账
  （`docs/226` §1：墙钟比值不当门禁）。
* **C 守卫之前落槽** —— 读**编译出来的计划**：每个守卫站点，守卫读的那个槽必须在
  "落槽表"里，而且生成的源码里那几行**逐条**出现在守卫调用之前。`docs/249` §5.2
  要的就是"不是读代码相信"。
* **D 用寄存器里的值去优化仍然答对** —— 那份夹具的值在守卫之前**只住在寄存器里**；
  守卫不成立时走通用调用，答案与原生产物一致，且去优化**恰好一次**。
* **E 落槽是承重的（证伪）** —— 把生成源码里那几行落槽**删掉**再跑：答案必须变错
  **或**直接报"读未定义的值"。少了这一条，C 只是"那几行存在"，证明不了它们有用。

用法: python tools/lvm_a0_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_t3  # noqa: E402
import loment_elf_test as ELF  # noqa: E402
import lvm_test as LT  # noqa: E402
import lvm_t3_test as T3T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ELF.CORPUS
ANSWER = 18          # T3 夹具在原生 / T0 / T2 / T3 上的答案（`lvm_t3_test` 判 A/C）


def _ir_of(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _run_both(ir: str, argv: list[str]):
    """同一个产物：槽机一趟、A0 一趟。返回两边的 (退出码, stdout)。"""
    def once(a0: bool):
        out = io.BytesIO()
        old = lvm_t2.T2Executor._rplan
        if not a0:
            lvm_t2.T2Executor._rplan = lambda self, cf: None       # 强制槽机
        try:
            rc = lvm_t2.run_ir_t2(ir, out, None, argv)
        finally:
            lvm_t2.T2Executor._rplan = old
        return rc, out.getvalue()
    return once(False), once(True)


def test_a0_is_byte_identical() -> None:
    """判 A：A0 与槽机**逐字节相同**（stdout + 退出码），语料每一份。"""
    ran = 0
    for rel in CORPUS:
        if Path(rel).stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir_of(rel)
        slot, a0 = _run_both(ir, [Path(rel).stem])
        assert slot == a0, (rel, "槽机", slot[0], "A0", a0[0])
        ran += 1
    print(f"      {ran} 份语料：A0 与槽机 stdout + 退出码逐字节相同")


def test_a0_is_faster_than_the_slot_machine() -> None:
    """判 B：**只判符号** —— A0 比槽机快。倍数只打印（`docs/226` §1）。"""
    ir = _ir_of("loment/examples/lumtui_demo.lomt")

    def once(a0: bool) -> float:
        out = io.BytesIO()
        old = lvm_t2.T2Executor._rplan
        if not a0:
            lvm_t2.T2Executor._rplan = lambda self, cf: None
        try:
            t = time.perf_counter()
            lvm_t2.run_ir_t2(ir, out, None, ["p"])
            return time.perf_counter() - t
        finally:
            lvm_t2.T2Executor._rplan = old

    slot = min(once(False) for _ in range(2))
    a0 = min(once(True) for _ in range(2))
    assert a0 < slot, f"A0 没有比槽机快：槽机 {slot:.2f}s，A0 {a0:.2f}s"
    print(f"      槽机 {slot:.2f}s · A0 {a0:.2f}s ⇒ {slot / a0:.2f}×（倍数只记账）")


def _spec_run(rewrite=None):
    """跑一遍 T3 夹具。`rewrite(cf, bi, rb) -> (source, fn)` 可以换掉某个块的生成代码。

    返回 `(rc 或 None, 异常文本, [(函数, 块, _RBlock)], 去优化次数)`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ir = T3T._ir_of_text(T3T._FIXTURE, td, "a0_probe")
        vals = lvm_t3.Values()
        lvm_t2.run_ir_t2(ir, io.BytesIO(), lvm.Profile(), ["prog"], vals)
        seen = []
        orig = lvm_t2._plan_regs

        def spy(cf, inline_mem=True):
            r = orig(cf, inline_mem)
            for bi, rb in enumerate(r or []):
                if rb is None:
                    continue
                if rb.guard_spills:
                    seen.append((cf.name, cf.labels[bi], rb))
                if rewrite is not None and rb.guard_spills:
                    rb.source, rb.fn = rewrite(cf, bi, rb)
            return r

        lvm_t2._plan_regs = spy
        log = lvm_t3.DeoptLog()
        rc, err = None, ""
        try:
            rc = lvm_t3.run_ir_t3(ir, io.BytesIO(), None, ["prog"], vals, log)
        except lvm.LvmError as e:
            err = str(e)
        finally:
            lvm_t2._plan_regs = orig
        return rc, err, seen, log.text().count("guard:C")


def test_the_frame_is_materialised_before_every_guard() -> None:
    """判 C（#648 第三格 / #649 第一条）：守卫之前，该写的槽**真的写了**。

    读的是**编译出来的计划**，不是源码里的注释：
      * 每个守卫站点在 `guard_spills` 里有一张落槽表；
      * 守卫读的那个操作数槽在表里；
      * 生成的源码里，`vals[槽] = R[寄存器]` 那几行**逐条**出现在守卫调用之前。
    """
    rc, err, seen, n_deopt = _spec_run()
    assert rc == ANSWER, (rc, err)
    assert seen, "T3 夹具里没有守卫 —— 这条判据在空转（先看 specialise 有没有真的特化）"
    assert n_deopt == 1, n_deopt
    for (_fname, _lab, rb) in seen:
        for site, slots in rb.guard_spills.items():
            assert slots, (site, "守卫站点没有落槽表")
            head, sep, _tail = rb.source.partition("_a0_guarded")
            assert sep, (site, "生成源码里没有守卫调用")
            for s in slots:
                assert f"vals[{s}] = R[" in head, (
                    site, f"守卫之前没有把槽 {s} 落下去 —— `docs/249` §4 那条不变式破了")
    print(f"      {len(seen)} 个守卫站点：落槽逐条在守卫之前（{n_deopt} 次去优化）")



def test_the_guarded_value_lives_only_in_a_register() -> None:
    """#649 的第一条：守卫那一条值，在守卫之前**只住在寄存器里**。

    这是"A0 让去优化不再是免费的"的字面证据：如果是槽机，这个值从头到尾都在槽里，
    "守卫之前重建帧"这句话没有内容。判据读生成源码：守卫调用之前，那个槽被写
    **恰好一次**，而且就是落槽那一行 —— 在此之前它没被写过。
    """
    rc, err, seen, _n = _spec_run()
    assert rc == ANSWER, (rc, err)
    assert seen, "没有守卫站点 —— 这条判据在空转"
    for (_fname, cf_lab, rb) in seen:
        for site, slots in rb.guard_spills.items():
            head = rb.source.partition("_a0_guarded")[0]
            for s in slots:
                writes = [ln for ln in head.splitlines() if ln.lstrip().startswith(f"vals[{s}] =")]
                assert len(writes) == 1, (
                    site, f"槽 {s} 在守卫之前被写了 {len(writes)} 次 —— "
                    "它不止住在寄存器里，这条判据证明不了 A0 的模型")
    print(f"      {len(seen)} 个守卫：被守卫的值在落槽之前一次都没进过槽")


def test_removing_the_spill_breaks_the_answer() -> None:
    """判 E（#649 的证伪）：把落槽那几行**删掉**，答案必须不再是 {ANSWER}。

    这是 C 的另一半：C 证明"那几行在"，E 证明"那几行有用"。少了 E，一个把落槽写成
    `pass` 的实现照样能过 C —— 而它真去优化时读到的是上一次的残留值，
    那**是一个数**，不是一个错（`docs/249` §4）。
    """
    def drop(cf, bi, rb):
        keep = [ln for ln in rb.source.splitlines()
                if not ln.lstrip().startswith("vals[")]
        src = "\n".join(keep)
        gns = {"lvm": lvm, "lomelf": lvm_t2.lomelf, "_UNDEF": lvm_t2._UNDEF,
               "_F": rb._fns if hasattr(rb, "_fns") else [],
               "_div": lvm_t2._do_div, "_extract": lvm_t2._extract,
               "_insert": lvm_t2._insert, "_rmw": lvm_t2._rmw, "_CF": cf}
        exec(compile(src, "<a0-nospill>", "exec"), gns)   # noqa: S102
        return src, gns["_b"]

    rc, err, seen, _n = _spec_run(rewrite=drop)
    assert seen, "没有守卫站点 —— 这条判据在空转"
    assert rc != ANSWER, (
        f"把落槽删掉之后答案**仍然**是 {rc} —— 那几行不是承重的，"
        "或者这条夹具根本没让值只住在寄存器里")
    print(f"      删掉落槽：{('答案 ' + str(rc)) if rc is not None else '报错 ' + err[:40]}"
          f"（不再是 {ANSWER}）")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_a0_test")


if __name__ == "__main__":
    sys.exit(main())
