#!/usr/bin/env python3
"""lvm_dismemory_test.py — LVM 的"没有地址"档（`docs/368`）。

这一档里 guest 的指针是一个**不透明令牌**：值里没有体的编号、也没有偏移；表在运行期。
访存只在**自己的体**里；跨体、整数化、排序都是 fault。体藏在**不重叠的三维格子**里，
重定位只改运行期坐标，不改任何 guest 值。

判据：

* **A 默认不变** —— 不写 `choose lvm_dismemory_true` 的同一份源，IR 里没有 marker，
  行为与从前逐字节相同。
* **B 真前端能跑** —— 数组 + `alloc`/`free` 的源在 K=0/1/7 三次重定位下同答（rc=10）。
* **C 体表** —— 越出体 → F01（101），详细块只报**令牌相对**的事实（令牌、体类别、
  偏移、宽）：不出现体的编号或坐标（`docs/371` §可观察代数）。
* **D 令牌不是数** —— ptrtoint / inttoptr / 位运算 → F04（104）。
* **E 没有全局空间** —— 跨体相减 / 排序 → F05（105）。
* **F 身份** —— 退休令牌 F02（102）、空令牌 F03（103）。
* **G 身份预算** —— 同一 (体,偏移) 只发一个令牌；退休后令牌从表里消失；序号/4 GiB
  用尽 → F06（106）；体两两不重叠；重定位改坐标不改令牌。
* **H 原生拒绝** —— `lomelf` 读到 marker 点名拒（原生有地址）。
* **I 编译器发 marker** —— `choose lvm_dismemory_true` 才发那一行。
* **J 不与基质叠加** —— `--substrate sw_cap` + dismemory 点名拒。
* **K 详细块对重排不变** —— 同一故障程序 K=0/1/7 的整段 stderr（含详细块）逐字节相同；
  文案里没有体号/坐标/体素。
* **L 时钟被虚拟化** —— 228/201 在 dismemory 下按**已执行指令数**给虚拟钟；
  同一程序 K=0/1/7 的 stdout（含两次时间戳）逐字节相同。
* **S I/O 可用** —— `syscall4p` / `sysio` 库的指针槽不 ptrtoint：`print` 与
  `sys_write` 在 dismemory 下真的写出字节，K=0/1/7 同答（`docs/375` §3）。
* **M 表示不变性** —— 深度 3（`docs/375` §2）：体不再有连续字节数组，内容是一张
  稀疏格映射。`shuffle=0/1/7` 与 100 次确定性随机摆法下，stdout / stderr / 退出码
  逐字节相同 —— 任何"基址 + 字节数"的路径都会在换摆法时露出来。
* **N 体内没有连续字节数组** —— 分配 1 MiB、只写 2 字节，背后只有 2 个格；
  体对象上没有 `data` 那样的可按下标寻址的缓冲区。

⚠ **为什么有一半夹具是手写 IR**：ptrtoint / inttoptr / 跨体减法这些操作**在 Loment 源里
写不出来**（这正是这一档要的）；要证明 LVM 边界拒绝它们，只能直接喂 IR。合法路径
（A/B）走真前端（`.lomt` -> `lomentc` -> IR）。
"""

from __future__ import annotations

import contextlib
import io
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import lvm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

MARK = "; loment-memory: dismemory"


def _ir(src: str) -> str:
    """真前端：`.lomt` 源 -> IR（调用方给完整源码）。"""
    with tempfile.TemporaryDirectory() as tds:
        f = Path(tds) / "m.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(f)
        deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        return lomentc.emit_llvm(mod, ROOT, deps)


def _run(ir: str, rel: int = 0, substrate: str = "none",
         shuffle: int = 0) -> tuple[int, bytes, str]:
    out = io.BytesIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = lvm.run_ir(ir, out, relocate_every=rel, substrate=substrate,
                        shuffle=shuffle)
    return rc, out.getvalue(), err.getvalue()


_BODY = """module dis_basic
choose lvm_dismemory_true
choose runtime
use bytes

fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        s = s + a[i];
        i = i + 1;
    }
    let p: ptr = alloc(16);
    store32(p, 0, s);
    let v: u32 = load32(p, 0);
    free(p);
    syscall4(60, v as u64, 0, 0);
}
"""

_FLAT = _BODY.replace("choose lvm_dismemory_true\n", "")

_HAND = MARK + """

@x = global [4 x i8] zeroinitializer

define void @_start() {
entry:
  %q = getelementptr [4 x i8], ptr @x, i64 0, i64 4
  %v = load i8, ptr %q
  ret void
}
"""


def test_default_source_is_unchanged() -> None:
    ir = _ir(_FLAT)
    assert MARK not in ir, "不写 choose 却发了 dismemory marker"
    rc, _out, err = _run(ir)
    assert rc == 10, (rc, err[:300])
    print("      默认（无 marker）：rc=10，标记行不出现")


def test_real_frontend_runs_under_every_relocation_schedule() -> None:
    ir = _ir(_BODY)
    assert MARK in ir, "choose 了却没发 marker"
    got = []
    for rel in (0, 1, 7):
        rc, out, err = _run(ir, rel)
        assert rc == 10, (rel, rc, err[:400])
        got.append((rc, out))
    assert got[0] == got[1] == got[2], "重定位改变了被解释程序的结果"
    print("      真前端（数组 + alloc/free）：K=0/1/7 同答 rc=10")


def test_out_of_body_is_f01_with_3d_detail() -> None:
    rc, _out, err = _run(_HAND)
    assert rc == 101, (rc, err[:400])
    for want in ("error[F01]:", "令牌：0x", "体：", "访问：令牌内偏移 4", "这一档没有"):
        assert want in err, (want, err[:800])
    for banned in ("体 #", "位形：(", "体素 ("):
        assert banned not in err, (banned, err[:800])
    print("      越出体表：rc=101；详细块只报令牌相对事实（无体号/坐标/体素）")


def test_tokens_are_not_numbers() -> None:
    cases = {
        "ptrtoint": """@x = global [4 x i8] zeroinitializer
define void @_start() {
entry:
  %n = ptrtoint ptr @x to i64
  ret void
}
""",
        "inttoptr": """define void @_start() {
entry:
  %p = inttoptr i64 123 to ptr
  ret void
}
""",
        "and": """@x = global [4 x i8] zeroinitializer
define void @_start() {
entry:
  %n = and i64 @x, 1
  ret void
}
""",
    }
    for op, body in cases.items():
        rc, _out, err = _run(MARK + "\n" + body)
        assert rc == 104, (op, rc, err[:300])
        assert "error[F04]:" in err and f"`{op}`" in err, (op, err[:500])
    print("      ptrtoint / inttoptr / and：rc=104，点名是哪一个")


def test_no_global_address_space() -> None:
    body = """@x = global [4 x i8] zeroinitializer
@y = global [4 x i8] zeroinitializer
define void @_start() {
entry:
  %d = sub i64 @x, @y
  ret void
}
"""
    rc, _out, err = _run(MARK + "\n" + body)
    assert rc == 105 and "error[F05]:" in err and "相减" in err, (rc, err[:500])
    body2 = body.replace("sub i64 @x, @y", "icmp ult i64 @x, @y")
    rc2, _o2, err2 = _run(MARK + "\n" + body2)
    assert rc2 == 105 and "比大小" in err2, (rc2, err2[:500])
    print("      跨体相减 / 排序：rc=105（没有地址空间）")


def test_retired_and_null_tokens() -> None:
    dangling = """define ptr @mk() {
entry:
  %p = alloca [4 x i8]
  ret ptr %p
}
define void @_start() {
entry:
  %p = call ptr @mk()
  store i8 1, ptr %p
  ret void
}
"""
    rc, _out, err = _run(MARK + "\n" + dangling)
    assert rc == 102 and "error[F02]:" in err and "永不复用" in err, (rc, err[:500])
    null = """define void @_start() {
entry:
  %v = load i8, ptr null
  ret void
}
"""
    rc2, _o2, err2 = _run(MARK + "\n" + null)
    assert rc2 == 103 and "error[F03]:" in err2, (rc2, err2[:400])
    print("      退休令牌 F02、空令牌 F03；F02 说明里写着令牌不复用")


def test_identity_budget_and_placement() -> None:
    m = lvm.Dismem()
    t = m.alloc(8, kind="global")
    t2 = m.add_off(t, 4)
    assert m.add_off(t, 4) == t2, "同一个 (体,偏移) 派生出了不同的令牌"
    assert m.tokens[t2][1] == 4
    m.retire(t)
    assert t not in m.tokens and t2 not in m.tokens, "退休后令牌还在表里"
    try:
        m.decode(t2, 1)
    except lvm._Fault as e:
        assert e.kind == lvm.FAULT_DANGLING
    else:
        raise AssertionError("退休令牌还能解码")
    # 4 GiB 与序号用尽都是显式的 F06
    try:
        m.alloc(1 << 32)
    except lvm._Fault as e:
        assert e.kind == lvm.FAULT_LIMIT
    else:
        raise AssertionError("4 GiB 的体没被拒")
    m.next_serial = lvm.TOKEN_MAX
    try:
        m.alloc(8)
    except lvm._Fault as e:
        assert e.kind == lvm.FAULT_LIMIT
    else:
        raise AssertionError("序号用尽没被拒")
    # 两两不重叠 + 重定位只改坐标
    m2 = lvm.Dismem()
    toks = [m2.alloc(64, kind="heap") for _ in range(40)]
    before = {b.id: (b.x, b.y, b.z) for b in m2.bodies.values()}
    bs = [b for b in m2.bodies.values() if b.alive]
    for i, a in enumerate(bs):
        for b in bs[i + 1:]:
            assert not (a.x < b.x + b.dx and b.x < a.x + a.dx
                        and a.y < b.y + b.dy and b.y < a.y + a.dy
                        and a.z < b.z + b.dz and b.z < a.z + a.dz), \
                f"体 #{a.id} 与 #{b.id} 重叠"
    m2.relocate()
    after = {b.id: (b.x, b.y, b.z) for b in m2.bodies.values()}
    assert any(before[k] != after[k] for k in before), "重定位没改任何坐标"
    assert all(t in m2.tokens for t in toks), "重定位动了令牌"
    print("      身份：派生去重、退休即失效、4 GiB/序号用尽 F06、体不重叠、重定位不动令牌")


def test_native_linker_refuses() -> None:
    try:
        lomelf.compile_ll(_HAND)
    except lomelf.Unsupported as e:
        assert "dismemory" in str(e), str(e)
    else:
        raise AssertionError("原生链接器收下了 dismemory 的 IR")
    print("      原生链接器点名拒（原生有地址）")


def test_compiler_emits_the_marker() -> None:
    assert MARK in _ir(_BODY)
    assert MARK not in _ir(_FLAT)
    print("      marker 只由 choose lvm_dismemory_true 产生")


def test_substrate_cannot_combine() -> None:
    try:
        _run(_HAND, substrate="sw_cap")
    except lvm.LvmError as e:
        assert "dismemory" in str(e), str(e)
    else:
        raise AssertionError("dismemory 与 sw_cap 叠加没被拒")
    print("      dismemory 与基质不叠加")



def test_fault_detail_is_invariant_under_relocation() -> None:
    """判 K（`docs/371`）：故障详细块不能泄漏隐藏格子的全序。

    同一份 IR、同一个故障，K=0/1/7 只是运行期重排体；整段 stderr 必须逐字节相同。
    之前的文案带 `体 #n` / `(x,y,z)` / 体素 —— 那等于把房间号念给 guest。
    """
    errs: list[str] = []
    for rel in (0, 1, 7):
        rc, _out, err = _run(_HAND, rel)
        assert rc == 101, (rel, rc, err[:300])
        errs.append(err)
    assert errs[0] == errs[1] == errs[2], (
        "重排改变了详细块：\n--- K=0\n" + errs[0][:400]
        + "\n--- K=7\n" + errs[2][:400])
    for banned in ("体 #", "位形：(", "体素 ("):
        assert banned not in errs[0], (banned, errs[0][:400])
    print("      同一故障 K=0/1/7：stderr 逐字节相同，且没有体号/坐标/体素")


_TIME = """module dis_time
choose lvm_dismemory_true
choose runtime

fn _start() {
    let t1: i64 = syscall4(201, 0, 0, 0);
    let k: u32 = 0;
    while k < 500000 {
        k = k + 1;
    }
    let t2: i64 = syscall4(201, 0, 0, 0);
    let d: u64 = (t2 - t1) as u64;
    syscall4(60, (d % 251) + 1, 0, 0);
}
"""


def test_clock_is_virtual_under_dismemory() -> None:
    """判 L（`docs/371`）：dismemory 下时间也在观察代数里。

    228 / 201 不再读宿主墙钟；按**已执行指令数**给确定性虚拟钟（1 条 = 1 ms）。
    于是同一程序 K=0/1/7 的 stdout（含两次时间戳）逐字节相同 —— guest 不能拿钟
    去量重排的缓存效应。虚拟钟仍要**前进**（否则"时间"成了常量）。
    """
    ir = _ir(_TIME)
    rcs: list[int] = []
    for rel in (0, 1, 7):
        rc, _out, err = _run(ir, rel)
        assert rc == rcs[0] if rcs else True, (rel, rc, rcs, err[:300])
        rcs.append(rc)
    assert len(set(rcs)) == 1, f"重排改变了虚拟钟（rc 各是 {rcs}）"
    assert rcs[0] > 1, (
        "虚拟秒没有前进（rc={}）：50 万次循环在墙钟上不到 1 秒，说明还在读宿主钟"
        .format(rcs[0]))
    rc2, _o2, _e2 = _run(ir, 0)
    assert rc2 == rcs[0], f"同一程序两次跑出不同的虚拟钟：{rcs[0]} vs {rc2}"
    print(f"      虚拟钟 K=0/1/7 同答 rc={rcs[0]}；重跑同值（墙钟版会是 1）")


_IO = """module dis_io
choose lvm_dismemory_true
choose runtime
use bytes

fn _start() {
    print("io ok\n");
    let p: ptr = alloc(4);
    store8(p, 0, 66);
    let w: i64 = syscall4p(1, 1, p, 1);
    if w != 1 { syscall4(60, 2, 0, 0); }
    syscall4(60, 0, 0, 0);
}
"""


def test_typed_pointer_syscalls_write_bytes() -> None:
    """判 S（`docs/375` §3）：dismemory 下的 I/O 不经过 ptrtoint。

    `print` 与 `syscall4p` 的第三个实参是**类型化指针槽**：lowering 发 `ptr %t` 而不是
    `ptrtoint`，于是令牌原样进寄存器、运行期按号解码。K=0/1/7 的 stdout 必须逐字节同。
    """
    ir = _ir(_IO)
    assert "ptrtoint ptr %t" not in ir, "用户代码里仍有 ptrtoint —— 类型化槽没生效"
    outs: list[bytes] = []
    for rel in (0, 1, 7):
        rc, out, err = _run(ir, rel)
        assert rc == 0, (rel, rc, err[:300])
        outs.append(out)
    assert outs[0] == outs[1] == outs[2] == b"io ok\nB", outs
    print("      syscall4p/print 在 dismemory 下真的写出字节；K=0/1/7 同答")


def test_representation_is_invariant_under_shuffle() -> None:
    """判 M（`docs/375` §2.4）：摆法只改物理键，改不了任何可观察量。

    深度 3 之后体是`逻辑格号 -> 字节`的稀疏映射。`--dismemory-shuffle N` 给每颗体一个
    确定性的移位 —— 同一个逻辑下标落到不同的桶。**任何"基址 + 字节数"的访问路径**
    在这种换摆法下都会读到别的格，于是 stdout / stderr / 退出码会变。这里用
    `shuffle=0/1/7` 加 100 次固定种子的随机摆法把这件事钉住；故障详细块也必须逐字节同。
    """
    import random  # noqa: PLC0415

    body = _ir(_BODY)
    base = _run(body)
    assert base[0] == 10, base
    rng = random.Random(20261122)  # 固定种子：判据可复现
    plans = [0, 1, 7] + [rng.randrange(1, 1 << 30) for _ in range(100)]
    for sh in plans:
        rc, out, err = _run(body, shuffle=sh)
        assert (rc, out) == (base[0], base[1]), (
            f"shuffle={sh} 改变了被解释程序的结果: rc={rc} out={out!r}\n{err[:300]}")
    io_ir = _ir(_IO)
    for sh in (0, 1, 7):
        rc, out, _err = _run(io_ir, shuffle=sh)
        assert (rc, out) == (0, b"io ok\nB"), (sh, rc, out)
    errs = [_run(_HAND, shuffle=sh)[2] for sh in (0, 1, 7)]
    assert errs[0] == errs[1] == errs[2], (
        "换摆法改变了故障详细块：\n" + errs[0][:300] + "\n---\n" + errs[2][:300])
    print(f"      shuffle=0/1/7 + 100 次随机摆法：rc/stdout/stderr 逐字节相同")


def test_body_is_sparse_not_a_byte_array() -> None:
    """判 N（`docs/375` §2.3）：体背后**没有连续字节数组**。

    分配 1 MiB 只写 2 字节时，背后应当只有 2 个格 —— 一块可按下标寻址的缓冲区会
    立刻铺满 1 MiB，而且访问路径一定写成 `base + k`。这条判据在换表示之前是红的：
    那时的 `_DismBody` 有 `data = bytearray(size)`。
    """
    m0 = lvm.Dismem()
    t0 = m0.alloc(1 << 20, kind="heap")
    b0, off0 = m0.decode(t0, 1)
    assert off0 == 0, off0
    assert not hasattr(b0, "data"), "体上仍挂着可按下标寻址的连续缓冲区"
    m0.put(t0, b"AB")
    assert len(b0.cells) <= 2, f"写 2 字节却落了 {len(b0.cells)} 个格"
    assert m0.get(t0, 4) == b"AB\0\0"
    assert all(0 <= k < b0.size for k in b0.cells), "格号越出了体的逻辑尺寸"
    m7 = lvm.Dismem(shuffle=7)
    t7 = m7.alloc(1 << 20, kind="heap")
    b7, _o7 = m7.decode(t7, 1)
    m7.put(t7, b"AB")
    assert m7.get(t7, 4) == m0.get(t0, 4), "换摆法改变了逻辑内容"
    assert set(b7.cells) != set(b0.cells), "换摆法后物理键没变 —— 摆法没生效"
    print("      1 MiB 只写 2 字节 ⇒ 2 个格；换摆法改物理键、不改逻辑内容")

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_dismemory_test")


if __name__ == "__main__":
    sys.exit(main())
