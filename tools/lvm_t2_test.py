#!/usr/bin/env python3
"""lvm_t2_test.py — LVM 第二格（T2：基线编译）的判据（`docs/232` §4.4，issue #325）。

四条，每一条都能证伪：

* **A 产物等价** —— 同一份程序，**原生产物 / T0 / T2** 三者 stdout 字节 + 退出码相同。
  这是"换引擎没换答案"的全部证据（`docs/232` §4.4 那行就是这么写的）。
* **B 剖面不变** —— T1 的剖面在 T0 与 T2 下**逐字节相同**。换引擎不许改变被记录的行为
  （`docs/247` 判据 D 的同一条纪律，只是这次换的是引擎不是开关）。
* **C 执行期**不再解码**（**这一格的**自证**，而且是**确定性的**：把 `lomelf.parse_type` /
  `parse_operand` 包起来数调用次数 —— 编译完的那一趟必须是 **0**，而解档那一趟必须 **> 0**
  （否则这条判据量到的是"包没接上"，不是"没解码"）。
* **D 确实更快** —— 同一进程里 **T0 / T2 交替跑**、取 `min`（`docs/226` 的记法）。
  ⚠ **判的是"谁更小"这个符号，不是某个倍数** —— 墙钟比值当门禁会随机器散布
  （那条教训：同一个量测出 3.2× / 4.13× / 5.4×）。倍数在这里**只打印、记账**，不做判据。

用法: python tools/lvm_t2_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 语料**复用**，不另抄一份（抄了必然漂）。参考实现自己跑不了的那几份由
#: `lvm_test.VM_KNOWN_GAPS` 那张棘轮点名 —— 这里跟着跳过，不另立一张表。
CORPUS = ELF.CORPUS


def _ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def test_t2_matches_t0_and_native() -> None:
    """判 A：原生产物 / T0 / T2 三者逐字节相同。"""
    if sys.platform == "win32" and not ELF._wsl():
        print("      SKIP: Windows 上没有 WSL —— 交叉编出来的 ELF 本机跑不了")
        return
    ran, skipped = [], []
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            stem = Path(rel).stem
            if stem in LT.VM_KNOWN_GAPS:
                skipped.append(stem)                 # 参考自己跑不了 —— 出局，不是缺口
                continue
            ir = _ir(rel)
            b0, b2 = io.BytesIO(), io.BytesIO()
            rc0 = lvm.run_ir(ir, b0, None, [rel])
            rc2 = lvm_t2.run_ir_t2(ir, b2, None, [rel])
            assert (rc0, b0.getvalue()) == (rc2, b2.getvalue()), (
                f"[{stem}] T0 与 T2 对不上：T0 rc={rc0} {b0.getvalue()[:40]!r} / "
                f"T2 rc={rc2} {b2.getvalue()[:40]!r}")
            # 原生产物
            try:
                blob, _ = lomelf.compile_ll(ir, [])
            except (lomelf.ElfError, lomelf.Unsupported) as e:  # noqa: BLE001
                skipped.append(stem)
                assert stem not in [Path(r).stem for r in CORPUS], e
                continue
            elf = td / f"{stem}.elf"
            elf.write_bytes(blob)
            rc_nat, out_nat = LT._run_native(elf, td, rel)       # noqa: SLF001
            assert (rc_nat, out_nat) == (rc2, b2.getvalue()), (
                f"[{stem}] 原生产物与 T2 对不上：原生 rc={rc_nat} {out_nat[:40]!r} / "
                f"T2 rc={rc2} {b2.getvalue()[:40]!r}")
            ran.append(stem)
    assert ran, "一份都没跑成 —— 判据没测到东西"
    print(f"      原生 / T0 / T2 三者逐字节相同: {len(ran)}/{len(ran)}（{', '.join(ran)}）")
    if skipped:
        print(f"      出局（参考实现自己也跑不了 / 发不出来）: {', '.join(skipped)}")


def test_profile_identical_across_tiers() -> None:
    """判 B：T1 的剖面在 T0 与 T2 下逐字节相同。"""
    ran = []
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        p0, p2 = lvm.Profile(), lvm.Profile()
        lvm.run_ir(ir, io.BytesIO(), p0, [rel])
        lvm_t2.run_ir_t2(ir, io.BytesIO(), p2, [rel])
        a, b = p0.text(), p2.text()
        if a != b:
            la, lb = a.split("\n"), b.split("\n")
            k = next((i for i in range(max(len(la), len(lb))) if la[i:i + 1] != lb[i:i + 1]), -1)
            raise AssertionError(f"[{stem}] 剖面不同 @第 {k} 行: T0 {la[k:k + 1]} / T2 {lb[k:k + 1]}")
        ran.append(stem)
    assert ran, "一份都没跑到 —— 判据没测到东西"
    print(f"      剖面逐字节相同（T0 vs T2）: {len(ran)} 份")


def test_t2_stops_decoding_at_run_time() -> None:
    """判 C：编译完之后，执行期**一次** `parse_type` / `parse_operand` 都不调。

    这条是这一格**自己的**主张（"把解码整体搬到执行之前"）的证伪面，而且是**确定性的** ——
    不靠计时。⚠ 反向那一半同样重要：**解档那一趟必须 > 0**，否则说明包没接上，
    这条判据会变成一条永远绿的**空判据**。
    """
    rel = "loment/examples/lumtui_demo.lomt"
    ir = _ir(rel)
    hits = {"n": 0}
    orig = (lomelf.parse_type, lomelf.parse_operand)

    def wrap(fn):
        def counted(*a, **k):
            hits["n"] += 1
            return fn(*a, **k)
        return counted

    lomelf.parse_type = wrap(orig[0])
    lomelf.parse_operand = wrap(orig[1])
    try:
        # 解档（T0）：每执行一条指令都要重解一遍
        hits["n"] = 0
        lvm.run_ir(ir, io.BytesIO())
        t0_hits = hits["n"]

        # 编译档（T2）：**先把每个函数都编掉**（只编译、不执行），再把计数器清零跑一趟。
        # ⚠ 不能"跑两趟、第二趟算数" —— 一个 VM 实例**只能跑一次**（`Mem` 被那次跑改过：
        # 堆游标、全局都被写过，第二趟会分配不出来而走到 `__loment_abort`）。T0 也是
        # 一回事（每次 `run_ir` 新建一个执行体），所以这里复刻同一个前提。
        g, f, e = lomelf.parse_ll(ir)
        hits["n"] = 0
        vm = lvm_t2.T2Executor(g, f, e, io.BytesIO())
        for name in list(vm.prog.funcs):
            vm.prog.cfunc(name)
        warm_hits = hits["n"]
        hits["n"] = 0
        try:
            vm.call_t2(vm.prog.cfunc("_start"), [], entry=True)
        except lvm._Exit:                                # noqa: SLF001  `_start` 里 exit(60)
            pass
        run_hits = hits["n"]
    finally:
        lomelf.parse_type, lomelf.parse_operand = orig

    assert t0_hits > 0, "T0 那趟一次解码都没有 —— 计数没接上，这条判据是空的"
    assert warm_hits > 0, "T2 编译那趟一次解码都没有 —— 计数没接上"
    assert run_hits == 0, (
        f"T2 编译完之后又解码了 {run_hits} 次 —— 说明还有指令在执行期读文本，"
        "这一格的主张不成立")
    print(f"      T0 执行期解码 {t0_hits} 次；T2 编译期 {warm_hits} 次、**执行期 0 次**")


def test_t2_is_faster() -> None:
    """判 D：同一进程里交替跑，T2 比 T0 快（**判符号，不判倍数**）。

    `docs/226` 的记法：固定机器、`min of N`、所有样本在同一次调用里。倍数只打印。
    """
    rel = "loment/examples/lumtui_demo.lomt"
    ir = _ir(rel)
    t0s, t2s = [], []
    for _ in range(3):
        t = time.perf_counter()
        lvm.run_ir(ir, io.BytesIO())
        t0s.append(time.perf_counter() - t)
        t = time.perf_counter()
        lvm_t2.run_ir_t2(ir, io.BytesIO())
        t2s.append(time.perf_counter() - t)
    a, b = min(t0s), min(t2s)
    assert b < a, f"T2 没有更快：T0 min={a * 1000:.1f} ms / T2 min={b * 1000:.1f} ms"
    print(f"      lumtui_demo：T0 min={a * 1000:.1f} ms · T2 min={b * 1000:.1f} ms "
          f"⇒ {a / b:.2f}×（倍数只记账，不做门禁）")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_t2_test")


if __name__ == "__main__":
    sys.exit(main())
