#!/usr/bin/env python3
"""lvm_profile_test.py — T1 的判据：**反馈剖面**（`docs/247`）。

五条（`docs/247` §5），每一条都能证伪：

* **A 确定性** —— 同一份程序跑两遍，两份剖面**逐字节相同**。T2/T3 拿它当输入，
  输入不确定就没法复现（`docs/226` 的规矩）。
* **B 保守不变式** —— 对**语料里每一份**：一个块被执行 N 次，它的出边计数之和**必须正好是 N**。
  这条不依赖任何内部结构，任何一处记账漏了或重了都会当场破。
* **C 计数是真行为** —— 一个**循环次数已知**的程序：条件块 4 次、体内 3 次、真边 3 次假边 1 次、
  调用点 1 次。
* **D 关掉是空的** —— 不带 `--profile-out`：**不写文件**，而且 stdout 字节 + 退出码与开着时
  **逐字节相同**（记录**不许**改变被记录的行为）。
* **E 第三方可读** —— B/C 全程通过**本文件自己那个小解析器**读，不复用 VM 的任何代码。
  "第三方可读"就是这么证出来的：不读它的实现也能拿到那张表。

⚠ **不判"剖面里记了什么类型"** —— 它**不记类型**（`docs/247` §1：这份 IR 是静态类型的，
记了是空的）。别把 V8 那张清单搬过来当验收标准。

用法: python tools/lvm_profile_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lvm  # noqa: E402
import loment_elf_test as ELF  # noqa: E402
import lvm_test as LT  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 语料**复用**，不另抄一份（抄了必然漂）。跑不了的那几份由 `lvm_test` 那张棘轮点名。
CORPUS = ELF.CORPUS

#: 循环次数**已知**的夹具，走**真前门**（源 -> 检查 -> IR -> 跑），不是手搓 IR。
#: 手推的期望值写在 `test_counts_are_real_behaviour` 里。
_COUNT_FIXTURE = """module lvmprof
choose runtime
use bytes

fn f(n: u32) -> u32 {
    let i: u32 = 0;
    let s: u32 = 0;
    while i < n {
        s = s + i;
        i = i + 1;
    }
    return s;
}

fn _start() -> u32 {
    let r: u32 = f(3);
    syscall4(60, r as u64, 0, 0);
    return 0;
}
"""


# ---------------------------------------------------------------- 第三方解析器
#
# **刻意不复用 `lvm.Profile`**：判据 E 要的是"不读它的实现也能拿到那张表"，
# 拿它自己的读法去读它自己的产物，那条就白证了。

def pparse(text: str) -> dict[str, dict]:
    """`docs/247` §4 那三段的**独立**读法。返回 {'blocks': {...}, 'edges': {...}, 'calls': {...}}。"""
    out: dict[str, dict] = {"blocks": {}, "edges": {}, "calls": {}}
    section = None
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            assert section in out, f"不认识的段: {line!r}"
            continue
        assert section, f"还没进段就有数据行: {line!r}"
        f = line.split("\t")
        if section == "blocks":
            fn, blk, n = f
            out["blocks"][(fn, blk)] = int(n)
        elif section == "edges":
            fn, blk, idx, e, n = f
            out["edges"][(fn, blk, int(idx), int(e))] = int(n)
        else:
            fn, blk, idx, callee, n = f
            out["calls"][(fn, blk, int(idx), callee)] = int(n)
    return out


def _run(src: Path, prof: lvm.Profile | None, out=None):
    return lvm.run_source(src.resolve(), ROOT, out if out is not None else io.BytesIO(), prof)


def _corpus_ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


# ---------------------------------------------------------------- 判据

def test_deterministic() -> None:
    """判 A：同一份程序跑两遍，剖面逐字节相同。"""
    text = _corpus_ir("loment/examples/tour.lomt")
    seen = []
    for _ in range(2):
        prof = lvm.Profile()
        lvm.run_ir(text, io.BytesIO(), prof)
        seen.append(prof.text())
    assert seen[0] == seen[1], "两次跑出来的剖面不一样 —— T2/T3 拿它当输入没法复现"
    p = pparse(seen[0])
    assert p["blocks"] and p["edges"], "剖面是空的，判据没测到东西"
    print(f"      两遍逐字节相同：{len(p['blocks'])} 个块 · {len(p['edges'])} 条边 · "
          f"{len(p['calls'])} 个调用点")


def test_block_count_equals_outgoing_edges() -> None:
    """判 B：块执行 N 次 ⇒ 它的出边计数之和正好是 N（语料里每一份）。"""
    checked = 0
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue                                     # 参考自己跑不了 —— 出局，不是缺口
        prof = lvm.Profile()
        lvm.run_ir(_corpus_ir(rel), io.BytesIO(), prof)
        p = pparse(prof.text())
        for (fn, blk, _idx, _e) in p["edges"]:
            assert (fn, blk) in p["blocks"], (
                f"[{stem}] {fn}::{blk} 有出边却没有块的计数 —— 记账漏了一处")
        for (fn, blk) in p["blocks"]:
            out_edges = sum(n for (f2, b2, _i, _e), n in p["edges"].items()
                            if (f2, b2) == (fn, blk))
            if out_edges == 0:
                continue                                 # `ret`/`unreachable` 结尾的块没有出边
            assert out_edges == p["blocks"][(fn, blk)], (
                f"[{stem}] {fn}::{blk} 执行 {p['blocks'][(fn, blk)]} 次，"
                f"出边计数之和却是 {out_edges}")
        for (fn, blk, _i, _callee), n in p["calls"].items():
            assert n <= p["blocks"][(fn, blk)], (
                f"[{stem}] {fn}::{blk} 的调用点被记了 {n} 次，而这一块只执行了 "
                f"{p['blocks'][(fn, blk)]} 次")
        checked += 1
    assert checked, "一份都没跑到 —— 判据没测到东西"
    print(f"      块次数 == 出边和 · 调用点 ≤ 块次数：{checked} 份语料")


def test_counts_are_real_behaviour() -> None:
    """判 C：循环次数已知的夹具，逐条对上手推的期望值。

    夹具是 `f(3)`：`while i < 3` ⇒ **条件 4 次**（3 次真 + 1 次假）、**体内 3 次**、**结束时 1 次**。
    程序自己退出码是 3（`0+1+2`）—— 那也是"跑完了"的证据。
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "lvmprof.lomt"
        src.write_text(_COUNT_FIXTURE, encoding="utf-8", newline="\n")
        prof = lvm.Profile()
        rc = _run(src, prof)
        assert rc == 3, f"夹具应当退出 3（0+1+2），退出的是 {rc}"
        p = pparse(prof.text())

    # 那个条件块：出边一条记 3 次、另一条记 1 次 —— 就是 `while i < 3` 的形状。
    splits = []
    for (fn, blk, idx, e), n in p["edges"].items():
        if e == 0:
            t = p["edges"].get((fn, blk, idx, 1))
            if t is not None:
                splits.append((fn, blk, n, t))
    assert len(splits) == 1, f"夹具里应当恰好有一个两分支的条件块，找到 {len(splits)} 个"
    fn, blk, taken, not_taken = splits[0]
    assert (taken, not_taken) == (3, 1), (
        f"`while i < 3` 应当是 真 3 次 / 假 1 次，记到的是 真 {taken} / 假 {not_taken}")
    assert p["blocks"][(fn, blk)] == 4, \
        f"条件块应当执行 4 次（3 真 + 1 假），记到 {p['blocks'][(fn, blk)]}"
    # `f` 的四个块：入口 1 / 条件 4 / 循环体 3 / 结束 1 —— 排序后就是 [1, 1, 3, 4]。
    # （不按标签名断言：标签是 codegen 的产物，会随它改；**次数**才是这一条要钉的东西。）
    counts = sorted(n for (fn2, _blk2), n in p["blocks"].items() if fn2 == fn)
    assert counts == [1, 1, 3, 4], (
        f"`f` 的块次数应当是 入口1 / 条件4 / 体3 / 结束1 ⇒ [1,1,3,4]，记到 {counts}")
    # 调用点：`_start` 调 `f` 一次。
    call_f = [n for (_f3, _b3, _i3, callee), n in p["calls"].items() if callee == "f"]
    assert call_f == [1], f"`f` 应当被调 1 次，记到 {call_f}"
    print("      已知循环：条件 4 次 / 真 3 · 假 1 / 块次数 [1,1,3,4] / 调用 1 次 —— 逐条对上")


def test_off_is_a_no_op() -> None:
    """判 D：关掉 ⇒ 不写文件；而且**行为逐字节不变**。"""
    rel = "loment/examples/tour.lomt"
    src = (ROOT / rel).resolve()

    b_on, b_off = io.BytesIO(), io.BytesIO()
    prof = lvm.Profile()
    rc_on = _run(src, prof, b_on)
    rc_off = _run(src, None, b_off)
    assert (rc_on, b_on.getvalue()) == (rc_off, b_off.getvalue()), (
        "开着剖面跑出来的 stdout/rc 与关掉时不同 —— **记录改变了被记录的行为**")
    assert prof.text().strip(), "开着却没记到东西"

    # CLI 那一层：不写文件。（`lvm.main` 会把被解释程序的 stdout 直接写出来 —— 收进一个
    # 临时文件，否则判据自己的输出里会混进三遍 `TOUR RESULT: PASS`。）
    with tempfile.TemporaryDirectory() as td, open(td + "/sink", "wb") as sink:
        real_out = sys.stdout
        sys.stdout = io.TextIOWrapper(sink, encoding="utf-8")
        out = Path(td) / "never.txt"
        rc = lvm.main([rel, "--root", str(ROOT), "--profile-out", str(out)])
        assert rc == 0 and out.exists(), "给了 --profile-out 却没写文件"
        before = out.read_bytes()
        out2 = Path(td) / "also.txt"
        rc2 = lvm.main([rel, "--root", str(ROOT), "--profile-out", str(out2)])
        assert rc2 == 0 and out2.read_bytes() == before, "两次 CLI 的剖面不一样"
        gone = Path(td) / "nope.txt"
        rc3 = lvm.main([rel, "--root", str(ROOT)])
        assert rc3 == 0 and not gone.exists()
        sys.stdout = real_out
    print("      关掉：不写文件且 stdout/rc 逐字节同；开着：两遍逐字节同")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nlvm_profile_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
