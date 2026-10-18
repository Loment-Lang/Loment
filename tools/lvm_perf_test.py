#!/usr/bin/env python3
"""lvm_perf_test.py — VM 的性能护栏（`docs/226` §2.2，issue #354）。

`docs/226` 是这一仓的性能账，它的规矩是"数字要带口径、A/B 背靠背"。VM 在此之前**一个数
都没有** —— T0/T2/T3 都在声称"更快"，而账上一个字也没有。这一格做两件事：

* **把四档的数记进账**（`docs/226` §2.2，含**与原生比**那一列 —— 那是诚实列）；
* **给一个上限**：一条**绝对**天花板，形状照自举编译那条（`loment_seed_test` 的 60s 护栏）。

⚠ **不是比值门禁**。墙钟比值当门禁会随机器散布（同一个量测出过 3.2× / 4.13× / 5.4×，
界 5.0 正落在散布里）。所以这里：

* **天花板**是**绝对**的、给得很宽（`lumtui_demo` 实测 T0 ≈ 6.4s，天花板 120s）——
  它抓的是"挂死或掉了一个数量级"，不是"慢了 20%"；
* **快慢**只判**符号**（T2 比 T0 小、T3 比 T0 小），倍数只打印。

用法: python tools/lvm_perf_test.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_t3  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 语料里最重的一份 —— 所有档都在它上面量（小的那份量出来的是启动成本）。
HEAVY = "loment/examples/lumtui_demo.lomt"

#: **绝对**天花板（秒）。实测 T0 ≈ 6.4s；120s 是"挂死/掉一个数量级"的网，不是性能门禁。
CEILING_S = 120.0

_CACHE: dict[str, float] = {}


def _ir() -> str:
    src = ROOT / HEAVY
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _time(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def _tiers() -> dict[str, float]:
    """四档各跑一趟（同一进程、背靠背，`docs/226` §1 的口径）。结果缓存，几条判据共用。"""
    if _CACHE:
        return _CACHE
    ir = _ir()
    _CACHE["T0"] = _time(lambda: lvm.run_ir(ir, io.BytesIO(), None, ["p"]))
    _CACHE["T1"] = _time(lambda: lvm.run_ir(ir, io.BytesIO(), lvm.Profile(), ["p"]))
    _CACHE["T2"] = _time(lambda: lvm_t2.run_ir_t2(ir, io.BytesIO(), None, ["p"]))
    vals = lvm_t3.Values()
    lvm_t2.run_ir_t2(ir, io.BytesIO(), lvm.Profile(), ["p"], vals)   # 值观察（一次性）
    _CACHE["T3"] = _time(lambda: lvm_t3.run_ir_t3(ir, io.BytesIO(), None, ["p"], vals,
                                                  lvm_t3.DeoptLog()))
    return _CACHE


def test_every_tier_under_the_ceiling() -> None:
    """判 A：每一档都在**绝对**天花板内跑完（挂死 / 掉一个数量级的网）。"""
    t = _tiers()
    for name, sec in t.items():
        assert sec < CEILING_S, (
            f"{name} 跑 {HEAVY} 用了 {sec:.1f}s，超过天花板 {CEILING_S:.0f}s —— "
            "先问是不是挂死或掉了一个数量级，别先信这个数")
    print("      " + " · ".join(f"{k}={v * 1000:.0f}ms" for k, v in t.items())
          + f"（天花板 {CEILING_S:.0f}s）")


def test_faster_tiers_are_faster() -> None:
    """判 B：**只判符号** —— T2 比 T0 小、T3 比 T0 小。倍数只打印。"""
    t = _tiers()
    assert t["T2"] < t["T0"], f"T2 没有比 T0 快：T0={t['T0']:.2f}s T2={t['T2']:.2f}s"
    assert t["T3"] < t["T0"], f"T3 没有比 T0 快：T0={t['T0']:.2f}s T3={t['T3']:.2f}s"
    print(f"      T2/T0 = {t['T0'] / t['T2']:.2f}× · T3/T0 = {t['T0'] / t['T3']:.2f}×"
          f"（倍数只记账，不做门禁）")


def test_the_ledger_has_the_rows() -> None:
    """判 C：`docs/226` 里**真有**这张表，而且**有与原生比的那一列**。

    `docs/226` 是"要动性能先读的那一份"；VM 的数不在里面，就等于没记过。
    """
    doc = ROOT / "docs" / "226-loment-performance-ledger.md"
    assert doc.exists(), "docs/226 不在"
    text = doc.read_text(encoding="utf-8")
    assert "LVM" in text and "§2.2" in text.replace("## 2.2", "§2.2") or "2.2 LVM" in text, \
        "docs/226 里没有 LVM 那一节"
    for tier in ("T0", "T1", "T2", "T3"):
        assert tier in text, f"docs/226 的 LVM 那一节没有 {tier} 这一档"
    assert "原生" in text, "docs/226 的 LVM 那一节没有『与原生比』那一列 —— 那是诚实列"
    print("      docs/226 §2.2：四档 + 与原生比的那一列都在")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_perf_test")


if __name__ == "__main__":
    sys.exit(main())
