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
* **快慢不判秒表**（#1028）：档位差由确定性计数钉（T0 每条重解、T2/T3 每条读一次元组、
  每个函数最多解一次），秒数与倍数只打印。

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

#: 每档 `(墙钟秒, 确定性计数)`。**墙钟只报告**；判据在计数上（#1028）。
_CACHE: dict[str, tuple[float, lvm.Counters]] = {}


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


def _tiers() -> dict[str, tuple[float, lvm.Counters]]:
    """四档各跑一趟（同一进程、背靠背，`docs/226` §1 的口径）。结果缓存，几条判据共用。"""
    if _CACHE:
        return _CACHE
    ir = _ir()
    c0, c1, c2, c3 = (lvm.Counters() for _ in range(4))
    _CACHE["T0"] = (_time(lambda: lvm.run_ir(ir, io.BytesIO(), None, ["p"],
                                             counters=c0)), c0)
    _CACHE["T1"] = (_time(lambda: lvm.run_ir(ir, io.BytesIO(), lvm.Profile(), ["p"],
                                             counters=c1)), c1)
    _CACHE["T2"] = (_time(lambda: lvm_t2.run_ir_t2(ir, io.BytesIO(), None, ["p"],
                                                   counters=c2)), c2)
    vals = lvm_t3.Values()
    lvm_t2.run_ir_t2(ir, io.BytesIO(), lvm.Profile(), ["p"], vals)   # 值观察（一次性）
    _CACHE["T3"] = (_time(lambda: lvm_t3.run_ir_t3(ir, io.BytesIO(), None, ["p"], vals,
                                                   lvm_t3.DeoptLog(), c3)), c3)
    return _CACHE


def test_every_tier_under_the_ceiling() -> None:
    """判 A：每一档都在**绝对**天花板内跑完（挂死 / 掉一个数量级的网）。

    ⚠ 这是墙钟在这一格唯一的**判据**用途 —— 绝对值、给得宽，抓的是挂死，
    不是"慢了 20%"。快慢本身由判 B 的确定性计数说，不由秒表说。
    """
    t = _tiers()
    for name, (sec, _c) in t.items():
        assert sec < CEILING_S, (
            f"{name} 跑 {HEAVY} 用了 {sec:.1f}s，超过天花板 {CEILING_S:.0f}s —— "
            "先问是不是挂死或掉了一个数量级，别先信这个数")
    print("      " + " · ".join(f"{k}={sec * 1000:.0f}ms" for k, (sec, _c) in t.items())
          + f"（天花板 {CEILING_S:.0f}s；秒数只报告）")


def test_work_counters_are_deterministic() -> None:
    """判 B（#1028）：档位差用**确定性计数**钉，秒表只打印。

    两条关系在任何机器上都成立，删掉对应实现就会红：

    * T0 每执行一条指令重解一次 ⇒ `decodes == instructions`（`tuple_lookups == 0`）；
    * T2/T3 每条执行到的指令读一次编译好的元组 ⇒ `tuple_lookups == instructions`，
      且每个函数最多真解一次（`decoded[*] == 1`）。

    ⚠ 这里**不再判** `T2 < T0` / `T3 < T0`：那两条在负载机上可以无缘无故翻转。
    倍数照旧打印，供 `docs/226` §2.2.3 记账。
    """
    t = _tiers()
    t0, t2, t3 = t["T0"][1], t["T2"][1], t["T3"][1]
    assert t0.instructions > 0, "T0 一条指令都没数到 —— 计数没接上"
    assert t0.decodes == t0.instructions, (
        f"T0 应当每条重解：instructions={t0.instructions} decodes={t0.decodes}")
    assert t0.tuple_lookups == 0, f"T0 没有编译好的元组，却读了 {t0.tuple_lookups} 次"
    for name, c in (("T2", t2), ("T3", t3)):
        assert c.instructions > 0, f"{name} 一条指令都没数到 —— 计数没接上"
        assert c.tuple_lookups == c.instructions, (
            f"{name} 每条执行到的指令应当读一次元组：instructions={c.instructions} "
            f"tuple_lookups={c.tuple_lookups}")
        assert c.decoded and all(n == 1 for n in c.decoded.values()), (
            f"{name} 把某个函数解了不止一次：{c.decoded}")
    sec0, sec2, sec3 = t["T0"][0], t["T2"][0], t["T3"][0]
    print(f"      {HEAVY}: T0 指令 {t0.instructions} / 解码 {t0.decodes}；"
          f"T2 解码 {t2.decodes}（函数 {len(t2.decoded)} 个，各 1 次）/ 元组 {t2.tuple_lookups}；"
          f"T3 特化 {t3.specialisations} / 去优化 {t3.deopts}")
    print(f"      秒表（只报告）：T0={sec0 * 1000:.0f}ms T2={sec2 * 1000:.0f}ms "
          f"T3={sec3 * 1000:.0f}ms ⇒ T2/T0={sec0 / sec2:.2f}× · "
          f"T3/T0={sec0 / sec3:.2f}×")


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
    # #1028：计数必须进账 —— "3.2×" 不能是唯一能引的数。
    for word in ("工作量", "解码", "元组", "特化", "去优化"):
        assert word in text, f"docs/226 的 LVM 那一节没有工作量计数的「{word}」那一行"
    print("      docs/226 §2.2：四档 + 与原生比 + 确定性工作量计数都在")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_perf_test")


if __name__ == "__main__":
    sys.exit(main())
