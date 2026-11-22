#!/usr/bin/env python3
"""lvm_cap_test.py — LVM-A 的 `sw_cap` 基质（`docs/362`，`docs/360` §7 的 B2/B3）。

`docs/360` 把 LVM 的下一层写成一**台抽象机**：对象表（`base`/`limit`/`gen`）、
能力检查、对象代、语言级 `fault(kind, site)`。这一格落的是**本世代可交付的第一档**
`sw_cap`。判据只有一条主轴：

> **同一份 IR，`sw_none` 与 `sw_cap` 在合法程序上逐字节同答；在不合法程序上
> `sw_cap` 给一个定义好的 fault（码 + 站点），`sw_none` 给不出。**

九条：

* **A 默认关** —— 不加 `--substrate` 时与从前逐字节相同（`docs/357` P8）。
* **B 动态下标越界** —— `a[10]` 在 `[u32; 4]` 上：`none` 静默，`sw_cap` 是 F01 + 站点。
* **C 悬垂** —— `free` 之后经内存洗白再读：`none` 读到 0，`sw_cap` 是 F02 + 站点。
* **D 堆对象越界** —— `alloc(16)` 之后写 `+16`：`none` 静默，`sw_cap` 是 F01。
* **E 语料零误报** —— `loment_elf_test.CORPUS` 每一份在 `none / sw_cap / sw_tag` 三档下同答。
* **F 站点确定** —— 同一输入两次，故障行逐字节同。
* **G 只走 T0** —— t2/t3 点名拒。
* **H `sw_tag` 抓未复用的悬垂** —— 地址上没有活对象时也是 F02。
* **I `sw_tag` 的复用是概率检测** —— 复用长度扫一遍，`sw_cap` 全抓、`sw_tag` 至少漏一次
  （`docs/364`；声明里 `no_dangling` 只能是 `floored`）。
* **J 段错误详细化** —— F01/F02/F03 都按 Lomenterr 的形状渲染：`error[码]` / `--> 站点` /
  指令 + 插入符 / `= 对象` / `= 事实` / `= 建议`（`docs/366`；首行保持稳定）。
* **K `llvm.trap` 也详细化** —— 132 的那一行之后跟同一形状的块（站点 + 建议）。

⚠ **夹具走真前端**（`.lomt` -> `lomentc` -> IR -> 跑）：不手写 IR。这一格要证明的是
"用户能写出来的错误会被抓住"，手写的 IR 会绕过前端、测到一个不会发生的输入。

用法: python tools/lvm_cap_test.py
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_elf_test as ELF  # noqa: E402
import lvm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 合法程序：栈数组的规范循环（下标从 0 到长度减一）。退出码 = 10。
_SAFE = """module cap_safe
choose runtime

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        s = s + a[i];
        i = i + 1;
    }
    syscall4(60, s as u64, 0, 0);
}
"""

#: 动态下标越界：下标是变量（前端不会拒），越过 `[u32; 4]` 的界。
_OOB = """module cap_oob
choose runtime

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let i: u32 = 10;
    a[i] = 99;
    syscall4(60, a[0] as u64, 0, 0);
}
"""

#: 悬垂：指针经 8 条 `store8` / `load8` 洗成整数再变回来 —— 静态检查器看不见，
#: 只有运行期对象代能说「这块已经归还」。
_UAF = """module cap_uaf
choose runtime
use bytes

fn w64(p: ptr, off: u32, v: u64) {
    let i: u32 = 0;
    while i < 8 {
        store8(p, off + i, ((v >> (i * 8)) & 255) as u8);
        i = i + 1;
    }
}

fn r64(p: ptr, off: u32) -> u64 {
    let v: u64 = 0;
    let i: u32 = 0;
    while i < 8 {
        let b: u64 = load8(p, off + i) as u64;
        v = v + (b << (i * 8));
        i = i + 1;
    }
    return v;
}

fn _start() {
    let p: ptr = alloc(16);
    store32(p, 0, 7);
    let slot: ptr = alloc(8);
    w64(slot, 0, p as u64);
    free(p);
    let q: ptr = r64(slot, 0) as ptr;
    let v: u32 = load32(q, 0);
    syscall4(60, v as u64, 0, 0);
}
"""

#: 空指针：把 0 洗成 `ptr` 再写 —— 只有运行期知道它是 0。
_NULL = """module cap_null
choose runtime
use bytes

fn _start() {
    let z: u64 = 0;
    let p: ptr = z as ptr;
    store32(p, 0, 1);
    syscall4(60, 0, 0, 0);
}
"""

#: 堆对象越界：`alloc(16)` 给 16 字节，写 `+16`（块内还有对齐余量，但对象只有 16 字节）。
_HEAP_OOB = """module cap_heap_oob
choose runtime
use bytes

fn _start() {
    let p: ptr = alloc(16);
    store32(p, 16, 99);
    syscall4(60, 0, 0, 0);
}
"""


def _compile(text: str, name: str) -> str:
    """真前端：`.lomt` -> IR。与 `lvm_stability_test` 同一条纪律。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{name}.lomt"
        src.write_text(text, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{name} 检查不过：{errs[:2]}"
        return lomentc.emit_llvm(mod, ROOT, deps)


def _run(ir: str, substrate: str, tier: str = "t0"):
    out, err = io.BytesIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            rc = lvm.run_ir(ir, out, None, ["prog"], tier, substrate=substrate)
        except lvm.LvmError as e:
            return ("LvmError", str(e)), out.getvalue(), err.getvalue()
    return rc, out.getvalue(), err.getvalue()


def _site_in(err: str) -> bool:
    """故障行必须点名**站点**：`<函数>:<块>#<指令序号>`（`docs/356` §5.3）。"""
    return re.search(r" at \S+:\S+#\d+ \(", err) is not None


def test_default_is_unchanged() -> None:
    """判 A：不加基质时逐字节同答；加上它，合法程序也逐字节同答。"""
    ir = _compile(_SAFE, "cap_safe")
    a = _run(ir, "none")
    b = _run(ir, "none")
    assert a == b, "同一份输入两次跑出了不同答案 —— 默认路径不确定"
    c = _run(ir, "sw_cap")
    assert a == c, f"合法程序在两种基质下不同答：none={a!r} sw_cap={c!r}"
    assert a[0] == 10, f"合法程序的退出码不是 10：{a!r}"
    print("      合法程序：none 两次逐字节同；sw_cap 与 none 逐字节同（rc=10）")


def test_dynamic_index_is_a_fault() -> None:
    """判 B：`a[10]` 在 `[u32; 4]` 上。先红：`none` 静默跑完。"""
    ir = _compile(_OOB, "cap_oob")
    silent = _run(ir, "none")
    assert silent[0] == 1, f"`none` 没有静默跑完 —— 夹具变了：{silent!r}"
    caught = _run(ir, "sw_cap")
    rc, _out, err = caught
    assert rc == 100 + lvm.FAULT_BOUNDS, f"退出码不是 100+F01：{rc}"
    assert "F01" in err and _site_in(err), f"F01 没有带站点：{err!r}"
    print(f"      none rc=1（静默）；sw_cap rc={rc} F01 + 站点")


def test_use_after_free_is_a_fault() -> None:
    """判 C：`free` 之后经内存洗白再读。先红：`none` 静默读到 0。"""
    ir = _compile(_UAF, "cap_uaf")
    silent = _run(ir, "none")
    assert silent[0] == 0, f"`none` 没有静默跑完 —— 夹具变了：{silent!r}"
    rc, _out, err = _run(ir, "sw_cap")
    assert rc == 100 + lvm.FAULT_DANGLING, f"退出码不是 100+F02：{rc}"
    assert "F02" in err and _site_in(err), f"F02 没有带站点：{err!r}"
    print(f"      none rc=0（读到 0）；sw_cap rc={rc} F02 + 站点")


def test_heap_write_past_object_is_a_fault() -> None:
    """判 D：`alloc(16)` 之后写 `+16`。先红：`none` 静默（块内对齐余量吞掉它）。"""
    ir = _compile(_HEAP_OOB, "cap_heap_oob")
    silent = _run(ir, "none")
    assert silent[0] == 0, f"`none` 没有静默跑完 —— 夹具变了：{silent!r}"
    rc, _out, err = _run(ir, "sw_cap")
    assert rc == 100 + lvm.FAULT_BOUNDS, f"退出码不是 100+F01：{rc}"
    assert "F01" in err and _site_in(err), f"F01 没有带站点：{err!r}"
    print(f"      none rc=0（静默）；sw_cap rc={rc} F01 + 站点")


def test_fault_is_deterministic() -> None:
    """站点与 witness 是确定的：同一份输入跑两次，stderr 逐字节同。"""
    ir = _compile(_OOB, "cap_oob")
    a = _run(ir, "sw_cap")
    b = _run(ir, "sw_cap")
    assert a == b, f"同一份输入两次给了不同的故障：{a!r} vs {b!r}"
    print(f"      故障行两次逐字节同：{a[2].strip()}")


def test_substrate_is_t0_only() -> None:
    """基质只在 T0 落地；t2/t3 要是**点名拒**，不是静默忽略。"""
    ir = _compile(_SAFE, "cap_safe")
    for tier in ("t2", "t3"):
        got, _out, _err = _run(ir, "sw_cap", tier)
        assert got[0] == "LvmError", f"[{tier}] 没有拒：{got!r}"
        assert "t0" in got[1], f"[{tier}] 拒文没点名 t0：{got[1]!r}"
    print("      t2 / t3：点名拒（只认 t0）")


def test_corpus_has_no_false_positives() -> None:
    """判 E：整套语料在两种基质下 `rc + stdout` 逐字节同。

    ⚠ `selfcheck` 在两边都**跑不了**（要系统调用 317，`lvm_test` 的棘轮记着这条）；
    这一格比的是"两边**同样**跑不了"，不是"它能跑"。
    """
    checked = 0
    for rel in ELF.CORPUS:
        src = ROOT / rel
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        if errs:
            continue
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        a = _run(ir, "none")
        b = _run(ir, "sw_cap")
        c = _run(ir, "sw_tag")
        assert a == b == c, f"{rel}: none={a!r} sw_cap={b!r} sw_tag={c!r}"
        checked += 1
    assert checked >= len(ELF.CORPUS) - 1, f"只比了 {checked} 份语料"
    print(f"      {checked} 份语料：三档基质逐字节同答（含同样跑不了的那几份）")


#: 标签碰撞：`p` 的地址被复用 N 次，最后一次留在手上。`sw_tag` 只比 4 位标签，
#: 总有一个 N 让新对象拿到与 `p` 相同的标签 —— 那时悬垂就漏了。
_TAG_TMPL = """module tag_coll
choose runtime
use bytes

fn w64(p: ptr, off: u32, v: u64) {
    let i: u32 = 0;
    while i < 8 {
        store8(p, off + i, ((v >> (i * 8)) & 255) as u8);
        i = i + 1;
    }
}

fn r64(p: ptr, off: u32) -> u64 {
    let v: u64 = 0;
    let i: u32 = 0;
    while i < 8 {
        let b: u64 = load8(p, off + i) as u64;
        v = v + (b << (i * 8));
        i = i + 1;
    }
    return v;
}

fn _start() {
    let p: ptr = alloc(16);
    store32(p, 0, 7);
    let slot: ptr = alloc(8);
    w64(slot, 0, p as u64);
    let alias: ptr = r64(slot, 0) as ptr;
    free(alias);
    let k: u32 = 0;
    while k < @N@ {
        let q: ptr = alloc(16);
        free(q);
        k = k + 1;
    }
    let last: ptr = alloc(16);
    store32(last, 0, 9);
    let v: u32 = load32(p, 0);
    syscall4(60, v as u64, 0, 0);
}
"""


def test_sw_tag_catches_unreused_dangling() -> None:
    """判 H：`sw_tag` 抓得住**没有复用**的悬垂（地址上没有任何活对象）。"""
    ir = _compile(_UAF, "cap_uaf")
    rc, _out, err = _run(ir, "sw_tag")
    assert rc == 100 + lvm.FAULT_DANGLING, f"退出码不是 100+F02：{rc}"
    assert "F02" in err and _site_in(err), err
    print("      未复用的悬垂：sw_tag 也是 F02 + 站点")


def test_sw_tag_reuse_is_probabilistic() -> None:
    """判 I：同一个地址被复用之后 —— `sw_cap` 全抓，`sw_tag` 会漏（4 位标签）。

    这是**声明**的判据：`sw_tag` 的 `no_dangling` 只能是 `floored`（`docs/360` §3.3）。
    """
    hits = []
    for n in range(0, 17):
        ir = _compile(_TAG_TMPL.replace("@N@", str(n)), f"tag_coll_{n}")
        cap = _run(ir, "sw_cap")
        tag = _run(ir, "sw_tag")
        assert cap[0] == 100 + lvm.FAULT_DANGLING, (
            f"N={n}: sw_cap 没抓到复用过的悬垂（rc={cap[0]}）")
        if tag[0] != 100 + lvm.FAULT_DANGLING:
            hits.append((n, tag[0]))
    assert hits, "17 个复用长度里没有一次标签碰撞 —— 4 位标签没被模拟"
    print(f"      sw_cap 全抓；sw_tag 在 {hits} 漏掉（4 位标签，概率检测）")


def test_faults_render_a_lomenterr_shaped_diagnostic() -> None:
    """判 J（`docs/366`）：三种故障都按 Lomenterr 的形状渲染，首行保持稳定。"""
    cases = (("oob", _OOB, "F01", "右越界", True),
             ("uaf", _UAF, "F02", "悬垂", True),
             ("null", _NULL, "F03", "空指针", False))
    for name, src, code, word, has_obj in cases:
        ir = _compile(src, name)
        rc, _out, err = _run(ir, "sw_cap")
        assert isinstance(rc, int) and rc >= 101, (name, rc, err[:200])
        first = err.splitlines()[0] if err else ""
        assert re.search(r" at \S+:\S+#\d+ \(", first), (name, first)
        assert f"error[{code}]:" in err, (name, err)
        assert "  --> IR:" in err, (name, err)
        assert "   |" in err and "^" in err and "这里访问" in err, (name, err)
        assert "= 事实：" in err, (name, err)
        assert "= 站点：函数 " in err, (name, err)
        assert "= 建议：" in err, (name, err)
        assert word in err, (name, word)
        if has_obj:
            assert "对象 #" in err and "base=" in err and "limit=" in err, (name, err)
        else:
            assert "指针是 0" in err, (name, err)
    print("      F01/F02/F03：error[码] / --> / 指令+插入符 / = 对象 / = 事实 / = 建议")


def test_trap_renders_a_diagnostic() -> None:
    """判 K：`llvm.trap` 的 132 之后跟同一形状的块（`docs/366`）。"""
    ir = """declare void @llvm.trap()

define void @_start() {
entry:
  call void @llvm.trap()
  ret void
}
"""
    out, err = io.BytesIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = lvm.run_ir(ir, out, None, ["prog"], "t0")
    text = err.getvalue()
    assert rc == 132, (rc, text)
    assert text.splitlines()[0].startswith("lvm: llvm.trap at _start:entry#0"), text
    assert "error[trap]:" in text and "  --> IR:_start:entry#0" in text, text
    assert "call void @llvm.trap()" in text, text
    assert "= 站点：函数 _start" in text and "= 建议：" in text, text
    print("      llvm.trap：首行不变，后面是同一形状的详细块")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_cap_test")


if __name__ == "__main__":
    sys.exit(main())
