#!/usr/bin/env python3
# loment_choose_matrix_test.py — `choose` 的**联动矩阵**（`#940`）
#
# 一条判据管一件事：**每一维在产物里真的驱动了某个子系统**，而不只是被检查器收下、
# 记进 Potato。矩阵的格子从 `lomentc.CORE_DIMS` / `lomentc.CORE_CONFLICTS` **推出来**，
# 不另抄一张表 —— 维或冲突一变，这里的格子跟着变。
#
# 判据（每条都对着**用户入口** `tools/lomentc.py` 跑，不直接调内部函数）：
#   1. 六个合法格全部编得过，且 Potato 里记的取值 == 请求的取值；
#   2. 每一对冲突都被**点名**拒绝（两个取值都出现在诊断里），不是"非法组合"；
#   3. `gc` 三档各自驱动的东西不同：manual 不发收集器/根表，auto/alpha 发，且只有
#      alpha 的编译期梯子非零（`docs/210` §2）；
#   4. `runtime` 是**产物的性质**：`runtime` 下产物里有分配器，`no_runtime` 下没有，
#      且 `alloc` 在 `no_runtime` 里被拒（`docs/227`）；
#   5. `hosted` 把堆换成宿主的（`; loment-heap: platform` + `@malloc`），`sealed` 不换
#      （`docs/222` §4）；
#   6. `lms` 的"收一次再试"只在**真有收集器**时出现（`docs/253` §5.3）；
#   7. `lspe` 关审计只改台账、不改一行代码（`docs/262` §10.1）；
#   8. `gc` × `vm` × `lspe` × LVM 四者一致：三档 GC 在 VM 上的行为与原生相同，
#      `vm` / `lspe` 两维不改 IR，但都进 Potato（`docs/231` §1.3、`docs/243` §4.5）。

from __future__ import annotations

import io
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lvm_test as LVMTest  # noqa: E402  (复用同一份"原生 vs VM"夹具，不另抄)

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 矩阵用哪几维 —— `#940` 的三个核心维。`port` / `lms` / `lspe` 单独在下面几条里组合。
MATRIX_DIMS = ("mode", "gc", "runtime")

#: 会用堆的探针：四层梯各来一站（与 `loment/examples/gc_ladder` 同形）。
ALLOC_BODY = """fn main() -> i32 {
    let a: ptr = alloc(64);
    store8(a, 0, 9);
    let n: u32 = 8;
    let b: ptr = alloc(n);
    store8(b, 0, 3);
    let i: i32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 1);
        i = i + 1;
    }
    let d: ptr = alloc(n);
    let e: ptr = d;
    store8(e, 0, 1);
    return (load8(a, 0) as i32) + (load8(b, 0) as i32) + (load8(e, 0) as i32);
}
"""

#: 能**跑**的探针：入口是 `_start`（VM 与原生都从这里进），退出码 9+3+1 = 13。
#: 与 `ALLOC_BODY` 同一份梯子，只是入口换掉 —— 两边比的是同一个程序。
RUN_BODY = """fn _start() -> u32 {
    let a: ptr = alloc(64);
    store8(a, 0, 9);
    let n: u32 = 8;
    let b: ptr = alloc(n);
    store8(b, 0, 3);
    let i: i32 = 0;
    while i < 4 {
        let c: ptr = alloc(n);
        store8(c, 0, 1);
        i = i + 1;
    }
    let d: ptr = alloc(n);
    let e: ptr = d;
    store8(e, 0, 1);
    let s: u32 = (load8(a, 0) as u32) + (load8(b, 0) as u32) + (load8(e, 0) as u32);
    syscall4(60, s as u64, 0, 0);
    return 0;
}
"""

#: 不碰堆的探针 —— `no_runtime` 的格子必须编得过（运行期是保证，不是限制语法）。
PURE_BODY = """fn main() -> i32 {
    let i: i32 = 0;
    while i < 4 {
        i = i + 1;
    }
    return i;
}
"""


def _source(cell: dict[str, str], body: str) -> str:
    lines = ["module probe"] + [f"choose {cell[d]}" for d in cell] + ["", body]
    return "\n".join(lines)


def _emit(td: Path, stem: str, cell: dict[str, str], body: str
          ) -> tuple[int, str, str, dict]:
    """跑一次用户入口。返回 (退出码, 诊断文本, IR 文本, Potato 对象)。"""
    src = td / f"{stem}.lomt"
    src.write_text(_source(cell, body), encoding="utf-8", newline="\n")
    ir = td / f"{stem}.ll"
    pot = td / f"{stem}.potato.json"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"), str(src),
                        "--emit-llvm", str(ir), "--emit-potato", str(pot)],
                       capture_output=True, text=True, shell=False)
    ir_text = ir.read_text(encoding="utf-8") if ir.exists() else ""
    pot_obj = json.loads(pot.read_text(encoding="utf-8")) if pot.exists() else {}
    return r.returncode, (r.stderr + r.stdout), ir_text, pot_obj


def _legal_cells() -> list[dict[str, str]]:
    """三个核心维的**所有合法格** —— 冲突对直接从编译器那张表读。"""
    conflicts = [set(p) for p in lomentc.CORE_CONFLICTS]
    cells = []
    for combo in itertools.product(*(lomentc.CORE_DIMS[d] for d in MATRIX_DIMS)):
        values = set(combo)
        if any(pair <= values for pair in conflicts):
            continue
        cells.append(dict(zip(MATRIX_DIMS, combo)))
    return cells


def _body(cell: dict[str, str]) -> str:
    return PURE_BODY if cell["runtime"] == "no_runtime" else ALLOC_BODY


@test
def test_every_legal_cell_compiles_and_the_artifact_records_it():
    """六个合法格全部编得过，且**产物自己记着**它选了哪一格（`#940` 第 1、2 行）。"""
    cells = _legal_cells()
    assert len(cells) == 6, (
        f"合法格应为 6 个（#940），实际 {len(cells)} 个 —— `CORE_DIMS` / `CORE_CONFLICTS`"
        f" 变了就要同时改这条判据与那条 issue")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for cell in cells:
            name = "-".join(cell[d] for d in MATRIX_DIMS)
            rc, diag, _ir, pot = _emit(td, name, cell, _body(cell))
            assert rc == 0, f"{name}: 编不过\n{diag[-500:]}"
            for dim in MATRIX_DIMS:
                got = pot.get(dim)
                assert got == cell[dim], \
                    f"{name}: Potato 记的是 {dim}={got!r}, 请求的是 {cell[dim]!r}"
    print(f"      {len(cells)} 个合法格: 编得过, 且产物的 mode/gc/runtime 与请求一致")


@test
def test_conflicting_pairs_are_refused_with_the_pair_named():
    """每一对冲突都被拒，且**两个取值都出现在诊断里** —— 不是"非法组合"（`#940` 第 4 行）。"""
    dim_of = lomentc.CORE_DIM_OF
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for pair, why in lomentc.CORE_CONFLICTS.items():
            cell = {dim_of[v]: v for v in pair}
            name = "-".join(sorted(pair))
            rc, diag, _ir, _pot = _emit(td, name, cell, PURE_BODY)
            assert rc != 0, f"{pair}: 被接受了（这一对是定义上矛盾的）"
            for v in pair:
                assert v in diag, f"{pair}: 诊断里没点名 {v!r}\n{diag[-400:]}"
            # 理由必须**针对这一对**，不能是泛泛的"取值非法"
            assert any(w in diag for w in ("不能同时选", "冲突")), \
                f"{pair}: 诊断没说清是冲突\n{diag[-400:]}"
            assert why, f"{pair}: CORE_CONFLICTS 那一项没有理由文本"
    print(f"      {len(lomentc.CORE_CONFLICTS)} 对冲突: 全部被点名拒绝")


@test
def test_each_gc_tier_drives_the_collector_and_the_ladder():
    """`gc` 三档驱动的东西**不同**：收集器/根表 vs 编译期梯子（`#942`、`docs/210` §2）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        out = {}
        for tier in ("gc_manual", "gc_auto", "gc_auto_alpha"):
            cell = {"mode": "std", "gc": tier, "runtime": "runtime"}
            rc, diag, ir, pot = _emit(td, tier, cell, ALLOC_BODY)
            assert rc == 0, f"{tier}: 编不过\n{diag[-400:]}"
            ladder = pot.get("gc_ladder") or {}
            out[tier] = {
                "collector": "__loment_collect" in ir,
                "roots": "__loment_roots" in ir,
                "compile_time_rungs": sum(int(ladder.get(k, 0)) for k in ("l0", "l1", "l2")),
            }
    manual, auto, alpha = (out[k] for k in ("gc_manual", "gc_auto", "gc_auto_alpha"))
    assert not manual["collector"] and not manual["roots"], \
        f"gc_manual 的产物里出现了收集器/根表: {manual}"
    assert auto["collector"] and auto["roots"], f"gc_auto 缺收集器/根表: {auto}"
    assert alpha["collector"] and alpha["roots"], f"gc_auto_alpha 缺收集器/根表: {alpha}"
    assert auto["compile_time_rungs"] == 0, \
        f"gc_auto 不该有编译期梯子（那是 alpha 的）: {auto}"
    assert alpha["compile_time_rungs"] > 0, \
        f"gc_auto_alpha 的编译期梯子全零 —— L0/L1/L2 没有驱动: {alpha}"
    print(f"      manual={manual} auto={auto} alpha={alpha}")


@test
def test_runtime_is_a_property_of_the_artifact():
    """`runtime` ⇒ 产物里有分配器；`no_runtime` ⇒ 一个符号都没有，且 `alloc` 被拒（`#944`）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, ir, pot = _emit(td, "rt_on", {"mode": "std", "gc": "gc_manual",
                                                "runtime": "runtime"}, ALLOC_BODY)
        assert rc == 0, f"runtime 档编不过\n{diag[-400:]}"
        assert pot.get("runtime") == "runtime"
        assert "__loment_alloc" in ir, "`choose runtime` 的产物里没有分配器"

        rc, diag, ir, pot = _emit(td, "rt_off", {"mode": "std", "gc": "gc_manual",
                                                 "runtime": "no_runtime"}, PURE_BODY)
        assert rc == 0, f"no_runtime 的纯程序编不过\n{diag[-400:]}"
        assert pot.get("runtime") == "no_runtime"
        for sym in ("__loment_alloc", "__loment_free", "__loment_collect",
                    "__loment_roots", "@__loment_heap"):
            assert sym not in ir, f"`choose no_runtime` 的产物里出现了 {sym}"

        rc, diag, _ir, _pot = _emit(td, "rt_off_alloc",
                                    {"mode": "std", "gc": "gc_manual",
                                     "runtime": "no_runtime"}, ALLOC_BODY)
        assert rc != 0, "`no_runtime` 下用 `alloc` 被接受了（那是保证，不是声明）"
        assert "no_runtime" in diag, f"拒绝时没点名 no_runtime\n{diag[-400:]}"
    print("      runtime: 有分配器；no_runtime: 零运行期符号，且 alloc 被点名拒绝")


@test
def test_hosted_port_moves_the_heap_to_the_host():
    """`hosted` 把堆换成宿主的（平台 arena + `@malloc`），`sealed` 留在产物里（`docs/222` §4）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        sealed_ir = hosted_ir = ""
        for port in ("sealed", "hosted"):
            cell = {"mode": "std", "gc": "gc_manual", "runtime": "runtime", "port": port}
            rc, diag, ir, pot = _emit(td, port, cell, ALLOC_BODY)
            assert rc == 0, f"{port}: 编不过\n{diag[-400:]}"
            assert pot.get("port") == port
            if port == "sealed":
                sealed_ir = ir
            else:
                hosted_ir = ir
    assert "loment-heap: platform" in hosted_ir, "hosted 没把堆换成平台的"
    assert "@malloc" in hosted_ir, "hosted 的产物里没有 malloc"
    assert "loment-heap: platform" not in sealed_ir and "@malloc" not in sealed_ir, \
        "sealed 的产物里出现了宿主的堆"
    assert sealed_ir != hosted_ir, "port 这一维没有改变产物"
    print("      sealed: 封在产物里；hosted: platform arena + @malloc")


@test
def test_hosted_and_gc_auto_compose():
    """`port` 与 `gc` 是两条正交轴：`hosted` + `gc_auto` 是合法格，堆从宿主来，
    收集器与根表照样在（`docs/222` §4；`_IR_GC_HOSTED`）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        cell = {"mode": "std", "gc": "gc_auto", "runtime": "runtime", "port": "hosted"}
        rc, diag, ir, pot = _emit(td, "hosted-auto", cell, ALLOC_BODY)
        assert rc == 0, f"hosted + gc_auto 编不过\n{diag[-400:]}"
        assert pot.get("port") == "hosted" and pot.get("gc") == "gc_auto"
        assert "loment-heap: platform" in ir and "@malloc" in ir, \
            "hosted + gc_auto 没把堆换成平台的"
        assert "__loment_collect" in ir and "__loment_roots" in ir, \
            "hosted + gc_auto 的产物里缺收集器/根表"
    print("      hosted + gc_auto: 平台堆 + 收集器 + 根表")


@test
def test_lms_rescue_only_exists_when_a_collector_does():
    """`lms` 的"收一次再试"只在真有收集器时出现 —— `gc_manual` 下它是一句空话（`docs/253` §5.3）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        sizes = {}
        for gc in ("gc_manual", "gc_auto"):
            for lms in ("lms_close", "lms_open"):
                cell = {"mode": "std", "gc": gc, "runtime": "runtime", "lms": lms}
                rc, diag, ir, pot = _emit(td, f"{gc}-{lms}", cell, ALLOC_BODY)
                assert rc == 0, f"{gc}+{lms}: 编不过\n{diag[-400:]}"
                assert pot.get("lms") == lms
                sizes[(gc, lms)] = len(ir)
    assert sizes[("gc_manual", "lms_open")] == sizes[("gc_manual", "lms_close")], \
        "gc_manual 下 lms_open 改变了产物 —— 没有收集器可'拉回', 这一级不该有代码"
    assert sizes[("gc_auto", "lms_open")] != sizes[("gc_auto", "lms_close")], \
        "gc_auto 下 lms_open 没有改变产物 —— 第 3 级入口 A 没接上"
    print(f"      manual 下无差别; auto 下 {sizes[('gc_auto', 'lms_close')]} -> "
          f"{sizes[('gc_auto', 'lms_open')]} B")


@test
def test_lspe_audit_switch_changes_the_ledger_not_the_code():
    """关审计只改台账、不改一行代码（`docs/262` §10.1；`#943`）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        for lspe in ("lspe_open_admin", "lspe_close_admin"):
            cell = {"mode": "std", "gc": "gc_manual", "runtime": "runtime", "lspe": lspe}
            rc, diag, ir, pot = _emit(td, lspe, cell, ALLOC_BODY)
            assert rc == 0, f"{lspe}: 编不过\n{diag[-400:]}"
            got[lspe] = (ir, (pot.get("lspe") or {}).get("state"))
    (ir_open, state_open), (ir_close, state_close) = (
        got["lspe_open_admin"], got["lspe_close_admin"])
    assert state_open == "lspe_open_admin" and state_close == "lspe_close_admin", \
        f"Potato 没记对审计开关: {state_open!r} / {state_close!r}"
    assert ir_open == ir_close, "关审计改变了产物 —— 它只该改变台账"
    print("      台账状态跟着开关走，IR 逐字节相同")


@test
def test_gc_vm_lspe_and_lvm_agree_on_every_tier():
    """GC × VM × LSPE × LVM：三档 GC 在 **VM 上**跑出的行为与原生相同，而 `vm` / `lspe`
    两维**不改 IR**、只进 Potato（`docs/231` §1.3、`docs/243` §4.5、`docs/262` §10.1）。

    这一条把四个子系统放在同一张格里：

    * `gc`：三档各一份（同一份梯子源）；
    * `vm`：`choose vm` 与不写——IR 必须逐字节相同（`vm` 今天只声明"要由 VM 执行"，
      产物不变；哪天它开始动 IR，这条会红，逼着重走 `docs/231` §2.2 的登记处）；
    * `lspe`：开/关审计——IR 同样必须相同，只有 Potato 的 `state` 变；
    * `lvm`：VM 跑出的退出码/输出必须与 `lomelf` 原生产物**逐字节相同**。
    """
    expected_rc = 13
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for gc in ("gc_manual", "gc_auto", "gc_auto_alpha"):
            variants = {
                "plain": {},
                "vm": {"vm": "vm"},
                "lspe_close": {"lspe": "lspe_close_admin"},
                "vm+lspe_close": {"vm": "vm", "lspe": "lspe_close_admin"},
            }
            irs = {}
            for name, extra in variants.items():
                cell = {"mode": "std", "gc": gc, "runtime": "runtime", **extra}
                rc, diag, ir, pot = _emit(td, f"{gc}-{name}", cell, RUN_BODY)
                assert rc == 0, f"{gc}+{name}: 编不过\n{diag[-400:]}"
                assert pot.get("gc") == gc, f"{gc}+{name}: Potato 的 gc 记错了"
                if "vm" in extra:
                    assert pot.get("vm") == "vm", f"{gc}+{name}: Potato 没记 vm"
                if "lspe" in extra:
                    assert (pot.get("lspe") or {}).get("state") == extra["lspe"], \
                        f"{gc}+{name}: Potato 没记 lspe"
                irs[name] = ir
            base = irs["plain"]
            assert irs["vm"] == base, \
                f"{gc}: `choose vm` 改变了 IR —— vm 维开始驱动产物，登记处要重走（docs/231 §2.2）"
            assert irs["lspe_close"] == base, f"{gc}: 关审计改变了 IR"
            assert irs["vm+lspe_close"] == base, f"{gc}: vm + 关审计组合改变了 IR"

            buf = io.BytesIO()
            rc_vm = lvm.run_ir(base, buf, None, ["probe"])
            out_vm = buf.getvalue()
            assert (rc_vm, out_vm) == (expected_rc, b""), \
                f"{gc}: VM 跑出 rc={rc_vm} out={out_vm!r}，应为 {expected_rc} 且无输出"

            if sys.platform == "win32" and not LVMTest.ELF._wsl():
                continue
            blob, _ = lomelf.compile_ll(base, [])
            elf = td / f"{gc}.elf"
            elf.write_bytes(blob)
            rc_native, out_native = LVMTest._run_native(elf, td, "probe")
            assert (rc_native, out_native) == (rc_vm, out_vm), (
                f"{gc}: 原生 rc={rc_native} {out_native!r} / VM rc={rc_vm} {out_vm!r}")
    native = "" if sys.platform != "win32" or LVMTest.ELF._wsl() else "（本机无 WSL，原生对照跳过）"
    print(f"      3 档 GC × vm × lspe: IR 逐字节同；VM 均 rc={expected_rc}{native}")


def main() -> int:
    return loment_suite.run(TESTS, "loment_choose_matrix_test")


if __name__ == "__main__":
    sys.exit(main())
