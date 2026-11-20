#!/usr/bin/env python3
"""lvm_t3_test.py — T3（优化 + 去优化）的判据（`docs/249` §5，`docs/260`）。

`docs/232` §4.4 说 T3 **只有一条硬判据**：去优化之后结果与原生产物相同。其余都是"更快"，
而"更快"不是正确性陈述。四条：

* **A 语料不变** —— 每一份程序：T2 记值 → T3 按它特化，**stdout 字节 + 退出码与 T0 相同**。
  这条同时覆盖副作用：语料里的程序都在写 stdout，重跑一次就会多写一遍。
* **B 真的去优化过** —— 那份夹具**必须**在这一次跑里触发守卫，**恰好一次**，而答案仍然对。
  ⚠ 这条是判据**非空**的前提：一个"没敢特化"的实现在 A 下照样全绿（`docs/249` §5.1 第 2 条）。
* **C 副作用只发生一次** —— 同一份夹具每一轮写一个字节，stdout 必须**正好**是 1001 个 'A'
  加一个 'B'。少了它，"退回去重跑"那种错实现**照样绿**。
* **D 反例：没被推翻的那一趟，零去优化** —— 把"改成 9"那一行去掉，记录必须是空的。
  少了它，一个"每条守卫都去优化"的实现也能过 B。
* **E 特化次数 == 过门槛的站点数**（#1028）—— 确定性的计数，不看墙钟：三处调用点里
  两处占比 ≥ 阈值、一处散开，`counters.specialisations` 必须**恰好**是 2；反例
  （把阈值判断删掉）会变成 3。

用法: python tools/lvm_t3_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_t3  # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ELF.CORPUS

#: **同一个调用站点**、先 1000 次 7 再一次 9 的夹具（`docs/249` §5.1）。
#: ⚠ 写第一版时我把它写成了 `f(7)` 与 `f(9)` 两处 —— 那是**两个站点**，各自都只观察到一个值，
#: 于是永远不会去优化。同一个站点才谈得上"假设被推翻"。
#: 每轮还有一个副作用（写一个 'A'）：判据 C 靠它抓"退回去重跑"。
_FIXTURE = """module deoptfix
choose runtime
use bytes

fn f(x: u32) -> u32 {
    return x * 2;
}

fn _start() -> u32 {
    let p: ptr = alloc(2);
    store8(p, 0, 65);
    store8(p, 1, 66);
    let i: u32 = 0;
    let k: u32 = 7;
    let s: u32 = 0;
    while i < 1001 {
        syscall4(1, 1, p as u64, 1);
        s = f(k);
        if i == 999 {
            k = 9;
        }
        i = i + 1;
    }
    syscall4(1, 1, (p as u64) + 1, 1);
    syscall4(60, s as u64, 0, 0);
    return 0;
}
"""

#: 反例：把"改成 9"那一行去掉 —— 同一个站点永远只见到 7。
_NO_VIOLATION = _FIXTURE.replace("            k = 9;\n", "")


def _ir_of(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _ir_of_text(text: str, td: Path, name: str) -> str:
    src = td / f"{name}.lomt"
    with src.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def _values_for(ir: str) -> lvm_t3.Values:
    """先跑一趟 T2 把值记下来（T3 的输入）。"""
    v = lvm_t3.Values()
    lvm_t2.run_ir_t2(ir, io.BytesIO(), lvm.Profile(), ["prog"], v)
    return v


def test_corpus_unchanged() -> None:
    """判 A：语料每一份，T3（带特化）与 T0 逐字节相同。"""
    ran, spec = [], 0
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir_of(rel)
        values = _values_for(ir)
        b0, b3 = io.BytesIO(), io.BytesIO()
        r0 = lvm.run_ir(ir, b0, None, ["prog"])
        prof = lvm.Profile()
        r3 = lvm_t3.run_ir_t3(ir, b3, prof, ["prog"], values, lvm_t3.DeoptLog())
        assert (r0, b0.getvalue()) == (r3, b3.getvalue()), (
            f"[{stem}] T0 与 T3 对不上：T0 rc={r0} {b0.getvalue()[:40]!r} / "
            f"T3 rc={r3} {b3.getvalue()[:40]!r}")
        spec += len(values.sites)
        ran.append(stem)
    assert ran, "一份都没跑成 —— 判据没测到东西"
    print(f"      T0 与 T3（带特化）逐字节相同: {len(ran)} 份；观察到的调用站点 {spec} 个")


def test_deopt_happens_and_still_answers_right() -> None:
    """判 B + C：那份夹具**恰好**去优化一次、答案对、副作用只发生一次。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ir = _ir_of_text(_FIXTURE, td, "deoptfix")
        values = _values_for(ir)
        d = lvm_t3.DeoptLog()
        c = lvm.Counters()
        out3, out0 = io.BytesIO(), io.BytesIO()
        r0 = lvm.run_ir(ir, out0, None, ["prog"])
        r3 = lvm_t3.run_ir_t3(ir, out3, lvm.Profile(), ["prog"], values, d, c)

    hits = sum(d.hits.values())
    assert c.specialisations == 1, (
        f"这一趟应当特化 1 个站点，记到了 {c.specialisations}（#1028）")
    assert c.deopts == hits == 1, (
        f"去优化计数 {c.deopts} 与日志 {hits} 对不上 —— 两者量的必须是同一件事")
    assert hits == 1, (
        f"这一次跑应当**恰好**去优化 1 次，记到了 {hits}（`docs/249` §5.1）—— "
        "0 次说明优化器没敢特化（判据是空的），多次说明守卫在反复响")
    assert (r0, out0.getvalue()) == (r3, out3.getvalue()), (
        f"去优化之后答案不对：T0 rc={r0} / T3 rc={r3}；"
        f"stdout 长度 {len(out0.getvalue())} vs {len(out3.getvalue())}")
    assert out3.getvalue() == b"A" * 1001 + b"B", (
        f"副作用不是**恰好一次**：stdout 长度 {len(out3.getvalue())}，"
        f"期望 {len(b'A' * 1001 + b'B')} —— 重跑一遍会给 2002 个 A")
    print(f"      去优化 {hits} 次 · 答案 rc={r3} · 副作用 {len(out3.getvalue())} 字节（A*1001+B）")


def test_no_violation_means_no_deopt() -> None:
    """判 D：没有假设被推翻的那一趟，去优化记录必须是空的。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ir = _ir_of_text(_NO_VIOLATION, td, "nodeoptfix")
        values = _values_for(ir)
        d = lvm_t3.DeoptLog()
        out = io.BytesIO()
        rc = lvm_t3.run_ir_t3(ir, out, lvm.Profile(), ["prog"], values, d)
    assert sum(d.hits.values()) == 0, (
        f"没有一次假设被推翻，却记到了 {sum(d.hits.values())} 次去优化 —— "
        "那条守卫根本没在按观察走")
    assert rc == 14, f"反例的退出码应当是 7*2=14，得到 {rc}"
    print("      没被推翻的那一趟：零去优化，rc=14")


#: 判 E 的期望值用**导入时快照**的门槛算 —— 这样把实现里的 `DOMINANT_SHARE` 调成 0
#: 只会移动实现、不会移动期望（证伪才真能红）。
_THRESHOLD = lvm_t3.DOMINANT_SHARE

#: 三处调用点，同一个**可内联**的被调（单块 `ret`）：
#: `%a` 永远是 7、`%b` 永远是 5（两处占比 1.0），`%d` 每轮一个新值（占比 0.001）。
#: 所以"过门槛的站点数"**独立地**是 2，T3 的特化计数必须与它相等。
_HOT_SITES = """define i32 @f(i32 %x) {
entry:
  %m = mul i32 %x, 2
  ret i32 %m
}
define i32 @_start() {
entry:
  br label %loop
loop:
  %k = phi i32 [ 0, %entry ], [ %k2, %loop ]
  %a = call i32 @f(i32 7)
  %b = call i32 @f(i32 5)
  %d = call i32 @f(i32 %k)
  %k2 = add i32 %k, 1
  %c = icmp ult i32 %k2, 1000
  br i1 %c, label %loop, label %done
done:
  ret i32 0
}
"""


def test_specialisation_count_equals_sites_over_threshold() -> None:
    """判 E（#1028）：T3 的特化次数 == 剖面里**过门槛**的站点数，逐点位。

    这是确定性的：同一份 IR + 同一份剖面，计数与机器无关。期望值从 `Values`
    **独立算出来**（占比 ≥ `DOMINANT_SHARE`），不是复述 `specialise()` 的返回值。

    证伪：把 `specialise()` 里的阈值判断删掉 ⇒ 那个散开的站点也被特化，计数变成 3，
    这条红；把计数写死成 0 ⇒ 也红。
    """
    values = _values_for(_HOT_SITES)
    assert len(values.sites) == 3, values.sites
    over = [s for s, seen in values.sites.items()
            if max(seen.values()) / sum(seen.values()) >= _THRESHOLD]
    assert len(over) == 2, f"夹具自己不对：过门槛的站点应当是 2 个，得到 {over}"
    under = [s for s in values.sites if s not in over]
    assert len(under) == 1, under

    c = lvm.Counters()
    d = lvm_t3.DeoptLog()
    out = io.BytesIO()
    rc = lvm_t3.run_ir_t3(_HOT_SITES, out, lvm.Profile(), ["hot"], values, d, c)

    assert rc == 0 and out.getvalue() == b"", (rc, out.getvalue())
    assert c.specialisations == len(over) == 2, (
        f"过门槛的站点 {len(over)} 个，T3 却特化了 {c.specialisations} 个")
    assert sorted(c.specialised_sites) == sorted(over), (
        f"特化站点对不上：计数 {c.specialised_sites} / 期望 {over}")
    assert c.deopts == 0, f"这一趟没有假设被推翻，却记了 {c.deopts} 次去优化"
    print(f"      3 处调用点：2 处过门槛 ⇒ specialisations={c.specialisations}，"
          f"散开的那处没被特化；去优化 0 次")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_t3_test")


if __name__ == "__main__":
    sys.exit(main())
