#!/usr/bin/env python3
"""lvm_fusion_test.py — A4 超级指令（`docs/266`，issue #647）。

四条，逐条对应 issue 里"done 意味着什么"：

* **A 逐字节同** —— 语料上 fused T2 与 T0 的 stdout + 退出码相同；剖面也逐字节相同
  （融合是执行期的事，开关不许改变被记录的行为）。
* **B 报告说得出来** —— 哪些序列、各几处，按 IR 解析顺序；关掉时一个字节都不写。
  这一条防的是"只量到变快了，说不出合了什么"。
* **C 从剖面里选** —— 给了 T1 剖面就只在点过名的块里融合（`hot`），报告里每一处都
  落在那些块里。
* **D 确实更快** —— 分派密集的夹具上，同一进程 **fused / unfused 交替跑**、取 `min`；
  只判符号，倍数记账（`docs/226`）。不拿墙钟比值当门禁。

外加两条"不许变"：融合对里的错误与步数上限仍然报**原下标**、原消息。

用法: python tools/lvm_fusion_test.py
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
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ELF.CORPUS

#: 分派密集的夹具：循环体全是纯算术，没有内存访问。A4 省的是分派，这份把它放大。
DISPATCH_LOOP = """define i32 @_start() {
entry:
  br label %loop
loop:
  %a = phi i32 [ 0, %entry ], [ %a2, %loop ]
  %b = phi i32 [ 0, %entry ], [ %b2, %loop ]
  %a2 = add i32 %a, 1
  %b2 = add i32 %b, 2
  %c = add i32 %a2, %b2
  %d = sub i32 %c, 3
  %e = icmp slt i32 %d, 3000000
  br i1 %e, label %loop, label %done
done:
  %r = and i32 %d, 255
  %z = zext i32 %r to i64
  %x = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %z, i64 0, i64 0)
  ret i32 0
}
"""


def _ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _run(ir: str, fuse: bool, prof=None, fusion=None, hot=None, argv=None):
    buf = io.BytesIO()
    rc = lvm_t2.run_ir_t2(ir, buf, prof, argv or ["<fusion-test>"], None, fuse, fusion, hot)
    return buf.getvalue(), rc


def test_fused_matches_t0_on_the_corpus() -> None:
    """判 A：fused T2 与 T0 的 stdout + 退出码逐字节相同。"""
    ran = 0
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        out0, rc0 = io.BytesIO(), 0
        rc0 = lvm.run_ir(ir, out0, argv=[rel])
        out2, rc2 = _run(ir, True, argv=[rel])
        assert out0.getvalue() == out2 and rc0 == rc2, f"{stem}: T0 与 fused T2 不一致"
        ran += 1
    print(f"      T0 与 fused T2 逐字节相同: {ran} 份")


def test_profile_is_identical_with_fusion_on() -> None:
    """判 A（另一半）：T1 剖面在 T0 与 fused T2 下逐字节相同。"""
    for rel in ("loment/examples/tour.lomt", "loment/examples/switch.lomt"):
        ir = _ir(rel)
        p0, p2 = lvm.Profile(), lvm.Profile()
        lvm.run_ir(ir, io.BytesIO(), p0, [rel])
        _run(ir, True, p2, lvm_t2.FusionReport())
        assert p0.text() == p2.text(), f"{Path(rel).stem}: 剖面被融合改变了"
    print("      剖面：T0 与 fused T2 逐字节相同（tour / switch）")


def test_report_lists_sequences_in_parse_order() -> None:
    """判 B：报告按解析顺序列出站点，`[patterns]` 的计数与站点数一致。"""
    ir = _ir("loment/examples/tour.lomt")
    rep = lvm_t2.FusionReport()
    _run(ir, True, None, rep)
    assert rep.sites, "报告是空的 —— 融合一处都没发生"
    # 解析顺序：函数表顺序 -> 块顺序 -> 块内下标升序。
    order = [(fn.name, b.label) for fn in lomelf.parse_ll(ir)[1] for b in fn.blocks]
    pos = {k: i for i, k in enumerate(order)}
    last_pos, last_idx = -1, {}
    for (fn, blk, idx, shape) in rep.sites:
        key = (fn, blk)
        assert key in pos, f"站点 {key} 不在解析顺序里"
        assert pos[key] >= last_pos, "站点没有按解析顺序排列"
        if pos[key] == last_pos:
            assert idx > last_idx[key], f"{key} 的块内下标不是升序"
        last_pos, last_idx[key] = pos[key], idx
        assert shape.count("+") >= 1
    per_shape = {}
    for (_, _, _, shape) in rep.sites:
        per_shape[shape] = per_shape.get(shape, 0) + 1
    assert per_shape == rep.counts, (per_shape, rep.counts)
    print(f"      报告：{len(rep.sites)} 处、{len(rep.counts)} 种形状，按解析顺序")


def test_hot_blocks_restrict_fusion() -> None:
    """判 C：给了剖面就只在点过名的块里融合。"""
    ir = _ir("loment/examples/tour.lomt")
    hot_one = {("__loment_memcmp", "loop")}
    rep = lvm_t2.FusionReport()
    _run(ir, True, None, rep, hot_one)
    assert rep.sites, "热点块里一处都没融合"
    bad = {(fn, blk) for (fn, blk, _i, _s) in rep.sites} - hot_one
    assert not bad, f"报告里有热点块之外的站点：{sorted(bad)[:3]}"
    # 反向一半：空的剖面 = 一处都不融合（选不出来就不优化）。
    rep2 = lvm_t2.FusionReport()
    _run(ir, True, None, rep2, set())
    assert not rep2.sites, "空剖面仍然融合了"
    print(f"      热点选择：1 个块 -> {len(rep.sites)} 处；空剖面 -> 0 处")


def test_fused_is_faster_on_a_dispatch_bound_loop() -> None:
    """判 D：同一进程交替跑、取 min；**只判符号**。"""
    off, on = [], []
    for _ in range(3):
        t = time.perf_counter()
        _run(DISPATCH_LOOP, False)
        off.append(time.perf_counter() - t)
        t = time.perf_counter()
        _run(DISPATCH_LOOP, True)
        on.append(time.perf_counter() - t)
    assert min(on) < min(off), \
        f"融合没有更快：unfused {min(off)*1000:.0f} ms / fused {min(on)*1000:.0f} ms"
    print(f"      分派密集夹具：unfused {min(off)*1000:.0f} ms · fused {min(on)*1000:.0f} ms "
          f"⇒ {min(off)/min(on):.2f}x（只判符号，倍数记账）")


def test_errors_and_step_cap_keep_the_original_index() -> None:
    """融合对里的错误与步数上限仍然报**原下标、原消息**。"""
    # `load` + `add`，而地址槽从未被写过 -> `读未定义的值`。
    ir = """define i32 @_start() {
entry:
  %v = add i32 1, 2
  %x = load i32, ptr %missing
  ret i32 %x
}
"""
    def msg(fuse: bool) -> str:
        try:
            _run(ir, fuse)
        except lvm.LvmError as e:
            return str(e)
        return "<none>"

    assert msg(False) == msg(True) == "读未定义的值 %missing", (msg(False), msg(True))

    # 步数上限：把上限调小，fused/unfused 的报错下标必须相同。
    def cap_msg(fuse: bool) -> str:
        try:
            _run(DISPATCH_LOOP, fuse)
        except lvm.LvmError as e:
            return str(e)
        return "<none>"

    old = lvm.STEP_CAP
    try:
        lvm.STEP_CAP = 7
        m_off, m_on = cap_msg(False), cap_msg(True)
        assert m_off == m_on, (m_off, m_on)
        assert "步数上限" in m_on, m_on
    finally:
        lvm.STEP_CAP = old
    print("      错误 / 步数上限：fused 与 unfused 报同一句话、同一个下标")


def test_cli_report_is_optional_and_stable() -> None:
    """报告关着不写；开着时写出的站点在两次运行之间逐字节相同。"""
    import subprocess

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        a, b = td / "f1.txt", td / "f2.txt"
        base = [sys.executable, str(ROOT / "tools" / "lvm.py"),
                str(ROOT / "loment" / "examples" / "tour.lomt"), "--tier", "t2"]
        r = subprocess.run(base, capture_output=True)
        assert r.returncode == 0 and not a.exists()
        r1 = subprocess.run(base + ["--fusion-out", str(a)], capture_output=True)
        r2 = subprocess.run(base + ["--fusion-out", str(b)], capture_output=True)
        assert r1.returncode == r2.returncode == 0
        assert r.stdout == r1.stdout == r2.stdout
        assert a.read_bytes() == b.read_bytes()
        assert a.read_text(encoding="utf-8").startswith("# lvm fusion v1")
    print("      CLI：不写报告时无产物；两次写出的报告逐字节相同")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_fusion_test")


if __name__ == "__main__":
    sys.exit(main())
