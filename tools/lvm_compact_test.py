#!/usr/bin/env python3
"""lvm_compact_test.py — 紧凑形的判据（`docs/250`，issue #328）。

四条：

* **A 恒等** —— 语料每一份：**文本**跑一遍、**紧凑形**跑一遍，stdout 字节 + 退出码相同；
  而且 **T1 的剖面也逐字节相同**（同一个引擎、同一份程序，换的只是编码 —— 剖面要是变了，
  说明解码把什么弄丢了）。
* **B 体积** —— 点名一份，**前后两个数**。⚠ 还钉一条反向的：**每一份都必须比文本小**。
  第一版就是这么红的：`@__loment_heap` 是 64 KiB 的 `zeroinitializer`，按字节原样写进去，
  `tour` 的紧凑形是文本的 **330%** —— 比原文件还大。
* **C 加载更快** —— 同一进程交替跑：**文本路**（解析 + 编译 + 跑）对 **紧凑路**（解码 + 跑），
  `min of N`。⚠ 这条量的是**加载**，不是执行：长程序上它会被执行时间摊薄，所以倍数只记账
  （`docs/250` §1 已经把"文件级解析只占 0.6%"量出来了）。
* **D 形态可读、错形点名拒** —— 读文件头就能说出是哪种形；把文本喂给紧凑形加载器要
  **点名拒**，不许当成文本硬解（`docs/250` §2.1）。

用法: python tools/lvm_compact_test.py
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
import lvm_compact  # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 语料**复用**，不另抄一份。参考跑不了的几份由 `lvm_test` 那张棘轮点名，这里跟着跳过。
CORPUS = ELF.CORPUS


def _ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def test_compact_is_identity_on_the_corpus() -> None:
    """判 A：文本与紧凑形跑出来**逐字节相同**，剖面也一样。"""
    ran = []
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        blob = lvm_compact.encode_ir(ir)
        b0, bc = io.BytesIO(), io.BytesIO()
        p0, pc = lvm.Profile(), lvm.Profile()
        r0 = lvm.run_ir(ir, b0, p0, [rel])
        rc = lvm_compact.run_compact(blob, bc, pc, [rel])
        assert (r0, b0.getvalue()) == (rc, bc.getvalue()), (
            f"[{stem}] 文本与紧凑形对不上：文本 rc={r0} {b0.getvalue()[:40]!r} / "
            f"紧凑 rc={rc} {bc.getvalue()[:40]!r}")
        a, b = p0.text(), pc.text()
        if a != b:
            la, lb = a.split("\n"), b.split("\n")
            k = next((i for i in range(max(len(la), len(lb))) if la[i:i + 1] != lb[i:i + 1]), -1)
            raise AssertionError(f"[{stem}] 剖面不同 @第 {k} 行: 文本 {la[k:k + 1]} / 紧凑 {lb[k:k + 1]}")
        ran.append(stem)
    assert ran, "一份都没跑成 —— 判据没测到东西"
    print(f"      文本 / 紧凑形逐字节相同（含剖面）: {len(ran)} 份（{', '.join(ran)}）")


def test_compact_is_smaller() -> None:
    """判 B：体积**前后两个数**，且**每一份都比文本小**。

    反向那条是承重的：算错一处分组（例如把 64 KiB 的零全局按字节原样写）就会得到
    "紧凑形比文本大三倍"，而 stdout 照样对 —— 只有这条会红。
    """
    worst = (0.0, "")
    biggest = (0, "", 0, 0)
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        blob = lvm_compact.encode_ir(ir)
        ratio = len(blob) / len(ir)
        assert ratio < 1.0, (
            f"[{stem}] 紧凑形比文本还大：{len(blob)}B vs {len(ir)}B（{ratio * 100:.0f}%）"
            " —— 编码把什么东西按字节展开了")
        if ratio > worst[0]:
            worst = (ratio, stem)
        if len(ir) > biggest[2]:
            biggest = (ratio, stem, len(ir), len(blob))
    ratio, stem, t, c = biggest
    print(f"      {stem}: {t / 1024:.1f} KB -> {c / 1024:.1f} KB（{ratio * 100:.1f}%）"
          f" · 语料里最差的一份 {worst[1]} {worst[0] * 100:.1f}%")


def test_compact_loads_faster() -> None:
    """判 C：**加载**更快（文本路：解析+编译；紧凑路：解码）。同一进程交替，`min of N`。

    ⚠ 只说加载。执行时间两边一样（同一个引擎），所以程序越大这条越不显眼 ——
    `docs/250` §1 那条测量（文件级解析只占 0.6%）已经把这个方向说清楚了。
    """
    rel = "loment/examples/lumtui_demo.lomt"
    ir = _ir(rel)
    blob = lvm_compact.encode_ir(ir)                 # 编码不算在"加载"里
    text_ms, comp_ms = [], []
    for _ in range(5):
        t = time.perf_counter()
        lvm_compact.encode_ir(ir)                    # 文本路：解析 + 编译（编码当它不存在）
        text_ms.append(time.perf_counter() - t)
        t = time.perf_counter()
        lvm_compact.decode(blob)                     # 紧凑路：只解码
        comp_ms.append(time.perf_counter() - t)
    a, b = min(text_ms), min(comp_ms)
    assert b < a, f"紧凑形加载没有更快：文本 {a * 1000:.1f} ms / 紧凑 {b * 1000:.1f} ms"
    print(f"      加载 lumtui_demo：文本路 {a * 1000:.1f} ms · 紧凑路 {b * 1000:.1f} ms "
          f"⇒ {a / b:.2f}×（只记账，不做门禁）")


def test_form_is_recorded_and_a_wrong_form_is_refused() -> None:
    """判 D：文件头说得清是哪种形；喂错形**点名拒**。"""
    ir = _ir("loment/examples/user_hello.lomt")
    blob = lvm_compact.encode_ir(ir)
    assert blob[:4] == lvm_compact.MAGIC, blob[:4]
    assert blob[4] == lvm_compact.VERSION, blob[4]
    assert blob[5] == lvm_compact.FORM_COMPACT, blob[5]

    # 喂文本原文 -> 点名拒（不是"当成文本硬解"）
    try:
        lvm_compact.decode(ir.encode("utf-8"))
    except lvm_compact.CompactError as e:
        assert "紧凑形" in str(e), e
    else:
        raise AssertionError("把文本喂给紧凑形加载器，它居然收了")

    # 头对、版本不认识 -> 也点名拒
    bad = bytearray(blob)
    bad[4] = 99
    try:
        lvm_compact.decode(bytes(bad))
    except lvm_compact.CompactError as e:
        assert "99" in str(e), e
    else:
        raise AssertionError("版本号不认识却收了")
    print("      文件头：魔数 / 版本 / 形态；文本与错版本都点名拒")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_compact_test")


if __name__ == "__main__":
    sys.exit(main())
