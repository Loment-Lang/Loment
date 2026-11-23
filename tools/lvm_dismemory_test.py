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
* **P 语义保留** —— 同一份程序，普通档与 `lvm_dismemory_true` 档（shuffle=0/1/7）
  rc 与 stdout 逐字节相同：**换表示不是换语言**。排除项写在 `_P_EXCLUDED` 里：
  那几份把 `str_ptr(...) as u64` 交给内核，按设计就是 F04 —— 它们是 S1 那条
  类型化指针通道要解决的写法，与本判据无关。

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
    for want in ("error[F01]:", "令牌：0x", "体：", "index=4", "count=4"):
        assert want in err, (want, err[:800])
    for banned in ("体 #", "位形：(", "体素 (", "offset="):
        assert banned not in err, (banned, err[:800])
    print("      越出数组（GEP 时）：rc=101；详细块只报路径事实（无体号/坐标/字节偏移）")


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
    assert rc == 105 and "error[F05]:" in err, (rc, err[:500])
    body2 = body.replace("sub i64 @x, @y", "icmp ult i64 @x, @y")
    rc2, _o2, err2 = _run(MARK + "\n" + body2)
    assert rc2 == 105 and "error[F05]:" in err2, (rc2, err2[:500])
    assert "没有全局空间" in err and "没有全局空间" in err2
    print("      引用相减 / 排序：rc=105（没有地址空间，也就没有序）")


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
    t2 = m.elem(t, 4, 8, 1)
    assert m.elem(t, 4, 8, 1) == t2, "同一个 (体,路径) 派生出了不同的令牌"
    assert m.tokens[t2][1] == ((0, 4, 8, 1),), m.tokens[t2][1]
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
    """判 N（`docs/375` §2.3 + `docs/377` §2.4）：体背后**没有连续字节数组**，
    而且**没有物理键可摆**。

    分配 1 MiB 只写 2 个字节元素时，背后只有 2 个格（键是路径）。深度 4 里
    `--dismemory-shuffle` 是空操作：没有字节号可换，内容当然一样。
    """
    m0 = lvm.Dismem()
    t0 = m0.alloc(1 << 20, kind="heap")
    b0, path0, leaf0 = m0.decode(t0, 1)
    assert path0 == () and leaf0 == (1 << 20), (path0, leaf0)
    assert not hasattr(b0, "data"), "体上仍挂着可按下标寻址的连续缓冲区"
    m0.put_run(t0, b"AB")
    assert len(b0.cells) <= 2, f"写 2 字节却落了 {len(b0.cells)} 个格"
    assert m0.get_run(t0, 4) == b"AB\0\0"
    assert all(isinstance(k, tuple) for k in b0.cells), "格的键不是路径"
    m7 = lvm.Dismem(shuffle=7)
    t7 = m7.alloc(1 << 20, kind="heap")
    b7, _p7, _l7 = m7.decode(t7, 1)
    m7.put_run(t7, b"AB")
    assert m7.get_run(t7, 4) == m0.get_run(t0, 4), "换摆法改变了逻辑内容"
    assert set(b7.cells) == set(b0.cells), (
        "深度 4 的摆法应当是空操作（没有物理键可换）")
    print("      1 MiB 只写 2 字节 ⇒ 2 个格；摆法在深度 4 是空操作")


#: P 的排除项：这几份例子把 `str_ptr(...) as u64` 递给 syscall4 —— 在 dismemory 下
#: 那就是 ptrtoint，按设计就该 F04（`docs/375` §3 的 `syscall4p` 才是那条通道）。
#: **不是**表示层改了语义；把它们排除在外比"顺手改语料"诚实。
_P_EXCLUDED = {
    "tour.lomt": "str_ptr(...) as u64 交给 syscall4（F04 是本档的正确答复）",
    "native_match_full.lomt": "同上",
    "all_loment.lomt": "同上",
}


def _inject_marker(src: str) -> str:
    """在 `module` 行之后插 `choose lvm_dismemory_true`（choose 必须在 module 之后）。"""
    out, done = [], False
    for line in src.splitlines(True):
        out.append(line)
        if not done and line.startswith("module "):
            out.append("choose lvm_dismemory_true\n")
            done = True
    assert done, "源里没有 module 行"
    return "".join(out)


def test_dismemory_preserves_observable_semantics() -> None:
    """判 P（`docs/375` §2.4）：换表示不是换语言。

    同一份例子，普通档与 dismemory 档各跑一遍 —— rc 与 stdout 必须逐字节相同，
    而且 dismemory 侧在 shuffle=0/1/7 下也相同。这条比 M 更硬：M 只证明"摆法
    不影响"，P 证明"整个表示层(稀疏格)不影响程序看到的东西"。
    """
    for rel in ("loment/examples/user_hello.lomt", "loment/examples/switch.lomt"):
        entry = ROOT / rel
        src = entry.read_text(encoding="utf-8")
        plain = _run(_ir(src))
        dis_ir = _ir(_inject_marker(src))
        got = [_run(dis_ir, shuffle=sh) for sh in (0, 1, 7)]
        for sh, g in zip((0, 1, 7), got):
            assert (g[0], g[1]) == (plain[0], plain[1]), (
                f"{rel} shuffle={sh}: 普通档 rc={plain[0]} out={plain[1][:40]!r} / "
                f"dismemory rc={g[0]} out={g[1][:40]!r}")
    for name, why in _P_EXCLUDED.items():
        assert (ROOT / "loment" / "examples" / name).exists(), name
        assert why, name
    print("      普通档 vs dismemory（shuffle=0/1/7）：rc 与 stdout 逐字节相同")


def main() -> int:
    return loment_suite.run(TESTS, "lvm_dismemory_test")




def test_path_derivation_is_structural() -> None:
    """判 Q（`docs/377` §2.2）：`getelementptr` 产生的是**结构步骤**。

    * 数组第 k 个元素与结构第 f 个字段是两种步骤；
    * 越界在**派生时**就是 F01（不是等到 load）；
    * 同一个 `(体, 路径)` 只发一个令牌。
    """
    m = lvm.Dismem()
    t = m.alloc(16, kind="heap")
    e = m.gep(t, 0, "[4 x i32]", [3])
    _b, path, leaf = m.decode(e, 4)
    assert path == ((0, 3, 4, 4),) and leaf == 4, (path, leaf)
    assert m.gep(t, 0, "[4 x i32]", [3]) == e, "同一路径发了两个令牌"
    try:
        m.gep(t, 0, "[4 x i32]", [4])
    except lvm._Fault as x:
        assert x.kind == lvm.FAULT_BOUNDS, x.kind
    else:
        raise AssertionError("越界下标没在派生时被拒")
    s = m.gep(t, 0, "{i32, i32}", [1])
    _b2, spath, _leaf2 = m.decode(s, 4)
    assert spath == ((1, 1, 0, 4),), spath
    print("      数组/字段两类步骤；越界在派生时 F01；同一路径只发一个令牌")


def test_pointer_arithmetic_is_refused() -> None:
    """判 R（`docs/377` §2.2）：引用上没有可加的整数。

    `ptr_add` 在源里走 GEP（那是有结构的下标）；而 IR 层的 `add i64 %tok, k`
    是"把引用当数用" —— F04。两个引用相减 / 排序是 F05。两者都只能手写 IR 才到得了。
    """
    add = """; loment-memory: dismemory
@x = global [8 x i8] zeroinitializer
define void @_start() {
entry:
  %n = add i64 @x, 4
  ret void
}
"""
    rc, _o, err = _run(MARK + "\n" + add)
    assert rc == 104 and "error[F04]:" in err, (rc, err[:300])
    cmp_ = """; loment-memory: dismemory
@x = global [8 x i8] zeroinitializer
define void @_start() {
entry:
  %d = icmp ult i64 @x, @x
  ret void
}
"""
    rc2, _o2, err2 = _run(MARK + "\n" + cmp_)
    assert rc2 == 105 and "error[F05]:" in err2, (rc2, err2[:300])
    print("      add i64 %tok -> F04；icmp %tok -> F05")


def test_byte_reinterpretation_of_an_element_is_refused() -> None:
    """判 S（`docs/377` §2.2）：把一个**更宽的元素**用更窄的类型去指 = 字节重解释。

    体根仍是字节序列（`alloc` 的产物就是字节），所以根上的 `i8` 指针合法；
    但已经落在 `i32` 元素上的引用再用 `i8` 去指它，这一档不主张 —— F04。
    """
    m = lvm.Dismem()
    t = m.alloc(16, kind="heap")
    e = m.gep(t, 0, "[4 x i32]", [2])
    try:
        m.gep(e, 0, "i8", [])
    except lvm._Fault as x:
        assert x.kind == lvm.FAULT_NOADDR, x.kind
    else:
        raise AssertionError("把 i32 元素当字节串重新解释了")
    print("      i32 元素上用 i8 去指：F04；根上的字节指针仍合法")


#: V（`docs/377` §4）：深度 4 的**形状语料**。只放语言面**接受**的形状；
#: 嵌套聚合在编译期就被拒，另有一条判据把那条边界钉住。
_SHAPE_BASE = """module shape_{name}
choose runtime
use bytes

{body}
"""

_SHAPES = {
    "struct_fields": r'''
struct P { a: u32, b: u32 }
fn _start() {
    let p: P = P { a: 3, b: 5 };
    p.a = p.a + 1;
    syscall4(60, (p.a * 10 + p.b) as u64, 0, 0);
}
''',
    "agg_param_ret": r'''
struct P { a: u32, b: u32 }
fn mk(a: u32, b: u32) -> P { return P { a: a, b: b }; }
fn score(p: P) -> u32 { return p.a * 10 + p.b; }
fn _start() {
    let p: P = mk(3, 4);
    syscall4(60, score(p) as u64, 0, 0);
}
''',
    "slice_arg": r'''
fn sum(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}
fn _start() {
    let a: [u32; 4] = [1, 2, 3, 4];
    syscall4(60, sum(&a) as u64, 0, 0);
}
''',
    "ptr_field": r'''
struct Node { v: u32, next: ptr }
fn _start() {
    let q: ptr = alloc(4);
    store32(q, 0, 5);
    let n: Node = Node { v: 2, next: q };
    let x: u32 = load32(n.next, 0);
    free(q);
    syscall4(60, (n.v * 10 + x) as u64, 0, 0);
}
''',
    "print_num": r'''
fn _start() {
    print_num(4321 as i64);
    print("\n");
    syscall4(60, 7, 0, 0);
}
''',
    "str_num": r'''
fn _start() {
    let s: str = str_num(1234);
    print(s);
    print("\n");
    syscall4(60, 5, 0, 0);
}
''',
    "struct_print": r'''
struct Msg { n: u32 }
fn _start() {
    let m: Msg = Msg { n: 7 };
    print("msg=");
    print_num(m.n);
    print("\n");
    syscall4(60, m.n as u64, 0, 0);
}
''',
}


def test_deep_shapes_match_plain() -> None:
    """判 V（`docs/377` §4）：形状语料在普通档与深度 4 档下同答。"""
    n = 0
    for name, body in _SHAPES.items():
        plain = _ir(_SHAPE_BASE.format(name=name, body=body))
        dis = _ir(_SHAPE_BASE.format(name=name, body=body)
                  .replace("choose runtime", "choose runtime\nchoose lvm_dismemory_true"))
        a = _run(plain)
        b = _run(dis)
        assert (a[0], a[1]) == (b[0], b[1]), (name, a, b)
        n = n + 1
    print(f"      {n} 份形状语料：普通档与深度 4 档 rc/stdout 逐字节同")


def test_nested_aggregates_are_a_language_boundary() -> None:
    """嵌套聚合目前**编不过**（语言面），这一档不为它背锅。"""
    bad = r'''
struct In { x: u32 }
struct Out { i: In, z: u32 }
fn _start() {
    let o: Out = Out { i: In { x: 1 }, z: 2 };
    syscall4(60, 0, 0, 0);
}
'''
    try:
        _ir(_SHAPE_BASE.format(name="nested", body=bad))
    except Exception as e:                       # noqa: BLE001
        assert "只支持标量" in str(e), str(e)[:200]
    else:
        raise AssertionError("嵌套聚合已被支持 —— 把它挪进 `_SHAPES` 并删掉这条")
    print("      嵌套聚合：编译器点名拒（只支持标量）—— 语言面边界，不是深度 4 的")

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    sys.exit(main())
