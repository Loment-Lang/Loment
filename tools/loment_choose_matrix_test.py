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
#      `vm` / `lspe` 两维不改 IR，但都进 Potato（`docs/231` §1.3、`docs/243` §4.5）；
#   9. **整张矩阵**的每个合法格，参考与自举的 IR 逐字节相同（`#940` / `#943`）；
#  10. `no_std` 的镜像**没有宿主层**（无 `.dynsym`/`.interp`/`.dynamic`、零导入）；
#  11. `std` + `hosted` 的镜像**带着宿主层**（真 libc 的导入 + 跑出 7）；
#  12. `no_std + hosted` 在**两个前端**都被拒（参考点名两个取值，自举锚在后写那行）；
#  13. `gc` 档位是**链接后镜像**的性质（函数集按档不同；`no_gc` 与 `manual` 逐字节同）；
#  14. `runtime` 的闭包按格子算（不调用的运行期层也在；堆按需；`no_runtime` 只有入口）。

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

#: 能跑的纯核探针（`_start`，不碰堆）—— `runtime` 的闭包按**格子**算，不按"用没用到"。
START_ONLY_BODY = """fn _start() {
    syscall4(60, 13, 0, 0);
}
"""

#: **宿主层**的探针（`#539`）：`abs` 是 libc 导出的名字，`alloc` 走 `hosted` 的平台堆。
#: 退出码 7 = `abs(-7)` —— 导入没解析、平台堆没接上、产物没跑起来，任一处错都到不了这个数。
HOSTED_BODY = """extern fn abs(v: i32) -> i32;

fn _start() {
    let p: ptr = alloc(8);
    store8(p, 0, 65 as u8);
    let v: i32 = abs(0 as i32 - 7);
    syscall4(60, v as u64, 0, 0);
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


def _all_legal_cells() -> list[dict[str, str]]:
    """**整张矩阵**的合法格（`#940` 第 1 行 / `#943`）：七维全部参与，冲突对现读。

    与 `_legal_cells` 的区别只在维数：那一条是 `#940` 正文点名的三个核心维（链接与运行
    的那十格），这一条是"每一维都算一格" —— 新维度一进 `CORE_DIMS`，这里自己长。
    """
    conflicts = [set(p) for p in lomentc.CORE_CONFLICTS]
    cells = []
    for combo in itertools.product(*(lomentc.CORE_DIMS[d] for d in lomentc.CORE_DIM_ORDER)):
        values = set(combo)
        if any(pair <= values for pair in conflicts):
            continue
        cells.append(dict(zip(lomentc.CORE_DIM_ORDER, combo)))
    return cells


_STAGE1: Path | None = None


def _stage1() -> Path:
    """自举驱动（**种子现链**）—— 与参考实现逐格对拍用的那一个实现。

    链接一次、全矩阵共用（缓存）：240 个格子各链一次是纯浪费，而 stage1 的字节只取决
    于种子，不取决于格子。
    """
    global _STAGE1
    if _STAGE1 is None:
        import loment_dist  # noqa: PLC0415
        _STAGE1 = loment_dist.build_stage1()
    return _STAGE1


def _selfhost_ir(td: Path, src: Path) -> tuple[int, str, str]:
    """让自举驱动编一份单元。返回 `(rc, stdout, stderr)` —— stdout 就是它的 IR。"""
    r = subprocess.run([str(_stage1()), src.name], cwd=str(td),
                       capture_output=True, text=True, shell=False, timeout=120)
    return r.returncode, r.stdout, r.stderr


@test
def test_every_legal_cell_compiles_and_the_artifact_records_it():
    """合法格全部编得过，且**产物自己记着**它选了哪一格（`#940` 第 1、2 行）。

    `#538` 给 `gc` 加了第四个值 `no_gc`：2 × 4 × 2 − 6 对冲突 = **10 个**合法格
    （#940 正文写"六个"时 `gc` 只有三档）。
    """
    cells = _legal_cells()
    assert len(cells) == 10, (
        f"合法格应为 10 个（#538 之后），实际 {len(cells)} 个 —— `CORE_DIMS` /"
        f" `CORE_CONFLICTS` 变了就要同时改这条判据与那条 issue")
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
def test_gc_tier_is_a_property_of_the_linked_artifact():
    """`#942`：档位不是 IR 里的一句话 —— **链出来的镜像**按档位不同，且与 Potato 一致。

    上一条比的是 IR 符号。这一条比**链接之后**的东西：`compile_ll` 报的 `funcs` 就是
    镜像里真实存在的函数。`no_gc` 与 `gc_manual` 必须给出**逐字节相同**的镜像 —— 那才叫
    "声明的不带收集器"落在产物上，而不是少了一行标记。
    """
    tiers = ("gc_manual", "gc_auto", "gc_auto_alpha", "no_gc")
    got: dict[str, tuple[set[str], bytes]] = {}
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for tier in tiers:
            rc, diag, ir, pot = _emit(td, f"img_{tier}",
                                      {"mode": "std", "gc": tier, "runtime": "runtime"},
                                      RUN_BODY)
            assert rc == 0, f"{tier}: 编不过\n{diag[-400:]}"
            assert pot.get("gc") == tier, f"{tier}: Potato 记的是 {pot.get('gc')!r}"
            blob, info = lomelf.compile_ll(ir, [])
            got[tier] = (set(info["funcs"]), blob)
    manual_f, manual_b = got["gc_manual"]
    auto_f, auto_b = got["gc_auto"]
    alpha_f, alpha_b = got["gc_auto_alpha"]
    nogc_f, nogc_b = got["no_gc"]
    assert not ({"__loment_collect", "__loment_maybe_collect"} & manual_f), \
        f"gc_manual 的镜像里出现了收集器: {sorted(manual_f)}"
    assert {"__loment_collect", "__loment_maybe_collect"} <= auto_f, \
        f"gc_auto 的镜像里缺收集器: {sorted(auto_f)}"
    assert {"__loment_collect", "__loment_minor", "__loment_major", "__loment_wb"} <= alpha_f, \
        f"gc_auto_alpha 的镜像里缺分代那一套: {sorted(alpha_f)}"
    assert auto_f > manual_f and alpha_f > auto_f, (
        f"档位没有让镜像变大/变小（manual={len(manual_f)} auto={len(auto_f)} "
        f"alpha={len(alpha_f)} 个函数）—— 档位没驱动链接进去的那部分")
    assert nogc_f == manual_f, (
        f"no_gc 与 gc_manual 的函数集不同：{sorted(nogc_f ^ manual_f)}")
    assert nogc_b == manual_b, "no_gc 的镜像与 gc_manual **不是逐字节相同** —— 声明没落在产物上"
    print("      manual/auto/alpha 的镜像函数集各不同；no_gc 与 manual 逐字节相同")


@test
def test_runtime_is_a_property_of_the_linked_artifact():
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
def test_runtime_is_a_property_of_the_linked_artifact():
    """`#944`：运行期的**闭包**按格子算，不按"程序用没用到"—— 链接之后看得见。

    一份**从不分配**的程序，`choose runtime` 也要带上运行期那一层（`_IR_RUNTIME`：
    mem*/abort），而**堆按需**（`docs/217`：只会除法的裸机引导程序不该凭空多 64 KiB
    的 `.bss`；`alloc` 在 `no_runtime` 下是编译期拒的，所以"有分配器"必然意味着格子对）。
    `no_runtime` 的镜像里一个运行期函数都没有、`.bss` 为零。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, ir, pot = _emit(td, "rt_img_on",
                                  {"mode": "std", "gc": "gc_manual", "runtime": "runtime"},
                                  START_ONLY_BODY)
        assert rc == 0, f"runtime 档编不过\n{diag[-400:]}"
        assert pot.get("runtime") == "runtime"
        _blob, info = lomelf.compile_ll(ir, [])
        assert {"__loment_memcpy", "__loment_memset", "__loment_memcmp",
                "__loment_abort"} <= set(info["funcs"]), (
            f"`choose runtime` 的镜像里没有运行期那一层（程序没调用它，但闭包按格子算）: "
            f"{sorted(info['funcs'])}")
        assert "__loment_alloc" not in info["funcs"], (
            "程序没 `alloc`，堆却进来了 —— 堆是按需的（`docs/217`）")
        assert info["bss"] == 0, f"没 `alloc` 的程序带了 .bss: {info}"

        rc, diag, ir, pot = _emit(td, "rt_img_off",
                                  {"mode": "std", "gc": "gc_manual", "runtime": "no_runtime"},
                                  START_ONLY_BODY)
        assert rc == 0, f"no_runtime 的纯程序编不过\n{diag[-400:]}"
        assert pot.get("runtime") == "no_runtime"
        _blob, info = lomelf.compile_ll(ir, [])
        assert set(info["funcs"]) == {"_start"}, (
            f"`choose no_runtime` 的镜像里有别的东西: {sorted(info['funcs'])}")
        assert info["data"] == 0 and info["bss"] == 0, f"no_runtime 还有数据段: {info}"

        rc, diag, ir, _pot = _emit(td, "rt_img_auto",
                                   {"mode": "std", "gc": "gc_auto", "runtime": "runtime"},
                                   RUN_BODY)
        assert rc == 0
        _blob, info = lomelf.compile_ll(ir, [])
        assert {"__loment_alloc", "__loment_collect"} <= set(info["funcs"]), (
            f"runtime + gc_auto 的镜像里缺收集器/分配器: {sorted(info['funcs'])}")

        rc, diag, _ir, _pot = _emit(td, "rt_img_bad",
                                    {"mode": "std", "gc": "gc_auto", "runtime": "no_runtime"},
                                    ALLOC_BODY)
        assert rc != 0, "`no_runtime` + `gc_auto` 被接受了（那是冲突对）"
    print("      runtime 的闭包按格子算（不分配也有分配器）；no_runtime 镜像只有 _start")


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
    """`lms` 的"收一次再试"只在真有收集器时出现 —— `gc_manual` 下它是一句空话（`docs/253` §5.3）。

    取值从 `CORE_DIMS["lms"]` 读、只比较两个取值的**行为差异**，不写死档名 ——
    这样改档名（如 `lms_*` 增档或重命名）不需要动这条判据，也不会两边各改一半。
    """
    values = lomentc.CORE_DIMS["lms"]
    # ⚠ **不假设"恰好两档"**（2026-10-19，`docs/253` §11.3 把 `lms` 改成档名之后是四档）。
    # 这一格要的是"第 3 级**开**与**关**各一档"，所以**从编译器那张表里挑**：位序与
    # `LMS_TOGGLES` 同一张，判据因此不写死任何档名。
    gi = lomentc.LMS_TOGGLES.index("lms_guard")
    off = next(v for v in values if not lomentc.LMS_TIERS[v][gi])
    on = next(v for v in values if lomentc.LMS_TIERS[v][gi])
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got: dict[tuple[str, str], str] = {}
        for gc in ("gc_manual", "gc_auto"):
            for lms in (off, on):
                cell = {"mode": "std", "gc": gc, "runtime": "runtime", "lms": lms}
                rc, diag, ir, pot = _emit(td, f"{gc}-{lms}", cell, ALLOC_BODY)
                assert rc == 0, f"{gc}+{lms}: 编不过\n{diag[-400:]}"
                assert pot.get("lms") == lms
                got[(gc, lms)] = ir
    # **判的是那条救援路径在不在**，不是产物大小：`lms_guard` 开着时两个回收档都会发
    # g1 界检查（`docs/253` §5.3 入口 B），所以"两档产物大小相同"在 `gc_manual` 下
    # 已经不成立了 —— 而入口 A 的判据从来只是那个 `%lmsretry` 标记。
    for gc in ("gc_manual", "gc_auto"):
        assert "%lmsretry" not in got[(gc, off)], f"{gc}+{off}: 关着第 3 级还有救援标记"
        want = gc == "gc_auto"
        assert ("%lmsretry" in got[(gc, on)]) == want, (
            f"{gc}+{on}: 救援路径该{'有' if want else '没有'}"
            f"（`gc_manual` 下没有东西可收，入口 A 是一句空话）")
    print(f"      {off} 无救援标记；{on} 只在 gc_auto 下有（入口 A 依赖收集器）")


@test
def test_lspe_audit_switch_changes_the_ledger_not_the_code():
    """关审计只改台账、不改一行代码（`docs/262` §10.1；`#943`）。

    取值从 `CORE_DIMS["lspe"]` 读，不写死档名 —— 与上一条同一条纪律。
    """
    values = lomentc.CORE_DIMS["lspe"]
    assert len(values) == 2, f"lspe 期望两档，实际 {values}"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = {}
        for lspe in values:
            cell = {"mode": "std", "gc": "gc_manual", "runtime": "runtime", "lspe": lspe}
            rc, diag, ir, pot = _emit(td, lspe, cell, ALLOC_BODY)
            assert rc == 0, f"{lspe}: 编不过\n{diag[-400:]}"
            got[lspe] = (ir, (pot.get("lspe") or {}).get("state"))
    (ir_a, state_a), (ir_b, state_b) = (got[v] for v in values)
    assert (state_a, state_b) == tuple(values), \
        f"Potato 没记对审计开关: {state_a!r} / {state_b!r}（期望 {values}）"
    assert ir_a == ir_b, "审计开关改变了产物 —— 它只该改变台账"
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
    lspe_alt = lomentc.CORE_DIMS["lspe"][1]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for gc in ("gc_manual", "gc_auto", "gc_auto_alpha"):
            variants = {
                "plain": {},
                "vm": {"vm": "vm"},
                "lspe_alt": {"lspe": lspe_alt},
                "vm+lspe_alt": {"vm": "vm", "lspe": lspe_alt},
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
            assert irs["lspe_alt"] == base, f"{gc}: 另一档审计改变了 IR"
            assert irs["vm+lspe_alt"] == base, f"{gc}: vm + 另一档审计组合改变了 IR"

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


@test
def test_no_std_is_a_claim_the_linker_checks():
    """`mode` 行（`#941` / #539）：`no_std` 的产物**自己声明**只用核那一层，链接器核这句话。

    与 `docs/227` 判据 B 同形：诚实的 `no_std` 编得过、IR 里有 `; loment-mode: no_std`
    且没有 `; loment-port: hosted`；一份**伪造**的（宿主层产物 + 那行声明）在链接期
    被点名拒。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, ir, pot = _emit(td, "nostd", {"mode": "no_std", "gc": "gc_manual",
                                                "runtime": "no_runtime"}, PURE_BODY)
        assert rc == 0, f"`choose no_std` 编不过\n{diag[-400:]}"
        assert pot.get("mode") == "no_std", f"Potato 记错了运行模式: {pot.get('mode')!r}"
        assert "; loment-mode: no_std" in ir, "产物没有声明它只用核那一层"
        assert "; loment-port: hosted" not in ir, "no_std 的产物带了宿主层"
        rc, diag, hosted_ir, _pot = _emit(td, "hosted_forge", {"mode": "std", "gc": "gc_manual",
                                                               "runtime": "no_runtime",
                                                               "port": "hosted"}, PURE_BODY)
        assert rc == 0 and "; loment-port: hosted" in hosted_ir, "夹具不成形：宿主层没有标记"
        try:
            lomelf.compile_ll("; loment-mode: no_std\n" + hosted_ir, [])
        except lomelf.Unsupported as e:
            assert "no_std" in str(e), f"拒绝时没点名 no_std: {e}"
        else:
            raise AssertionError("声明 no_std 却带着宿主层的 IR 被链接器收下了")
    print("      no_std: 编得过、产物无宿主层、伪造的声明在链接期点名拒")


@test
def test_both_implementations_emit_the_same_ir_on_every_legal_cell():
    """`#940` 第 1 行 / `#943`：**整张矩阵**的每个合法格，两份实现的 IR 逐字节相同。

    `#940` 正文写的"六格"是 `mode × gc × runtime`；现在 `CORE_DIMS` 是七维，合法格
    由编译器自己的表**现算**（当前 240 个）。格子**不写死** —— 加一维、改一对冲突，
    这条判据自己跟着长。要的是"每一格都有人答"，所以**不抽样**：抽样的绿灯证明不了
    没被抽到的那一格。自举驱动是种子现链的那一只，与用户拿到的 dev 通道同一份实现。
    """
    cells = _all_legal_cells()
    assert len(cells) >= 100, (
        f"整张矩阵只剩 {len(cells)} 个合法格 —— 维或冲突表被改窄了，先解释再改这条")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for cell in cells:
            name = "-".join(cell[d] for d in lomentc.CORE_DIM_ORDER)
            rc, diag, ref, pot = _emit(td, name, cell, _body(cell))
            assert rc == 0, f"{name}: 参考实现编不过\n{diag[-400:]}"
            for dim in lomentc.CORE_DIM_ORDER:
                assert pot.get(dim) == cell[dim] or dim in ("lms", "lspe"), (
                    f"{name}: Potato 记的是 {dim}={pot.get(dim)!r}, 请求的是 {cell[dim]!r}")
            src = td / f"{name}.lomt"
            rc2, got, err = _selfhost_ir(td, src)
            assert rc2 == 0, f"{name}: 自举驱动拒绝（rc={rc2}）{err[-300:]}"
            assert got == ref, (
                f"{name}: 两份实现的 IR 不同（参考 {len(ref)}B / 自举 {len(got)}B）")
    print(f"      {len(cells)} 个合法格：参考与自举的 IR 逐字节相同")


@test
def test_no_std_image_carries_no_hosted_layer():
    """`#941` / `#539`：`no_std` 的产物是**自足的镜像** —— 没有动态导入、没有宿主层。

    `std` 解锁宿主层（`docs/222` §4），`no_std` 的定义是"只用核那一层"；这句话要能
    从**产物**上核：镜像里没有 `.dynsym` / `.interp` / `.dynamic`，链接器也报零导入。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, ir, pot = _emit(td, "nostd_img",
                                  {"mode": "no_std", "gc": "gc_manual", "runtime": "runtime"},
                                  RUN_BODY)
        assert rc == 0, f"`choose no_std` 编不过\n{diag[-400:]}"
        assert pot.get("mode") == "no_std"
        assert "; loment-mode: no_std" in ir, "产物没有声明它只用核那一层"
        blob, info = lomelf.compile_ll(ir, [])
        assert info["shared"] == [] and info["imports"] == [], (
            f"no_std 的镜像里有导入: {info['shared']} / {info['imports']}")
        for sec in (b".dynsym", b".interp", b".dynamic"):
            assert sec not in blob, f"no_std 的镜像里有 {sec.decode()} —— 宿主层被拉进来了"
    print("      no_std: 静态镜像，无 .dynsym/.interp/.dynamic，零导入")


@test
def test_std_hosted_image_carries_the_hosted_layer():
    """`#941` / `#539`：`std` + `hosted` 的产物**带着宿主层** —— 真 libc 的导入与运行。

    与上一条是**同一个判据的两个方向**：`no_std` 必须没有宿主层，`std`+`hosted` 必须
    有。`abs(-7)` 跑出 7 这件事把"导入解析"也从链接期带到了运行期。
    """
    import loment_ffi_test as ffitest  # noqa: PLC0415
    if not LVMTest.ELF._wsl():
        print("      SKIP: 需要 WSL（或 Linux）跑 ELF")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        libc = ffitest._system_so(td / "libc.so.6", ffitest._LIB6_CANDIDATES)
        if libc is None:
            print("      SKIP: 这台机器上没有 libc.so.6")
            return
        src = td / "std_hosted.lomt"
        src.write_text(_source({"mode": "std", "gc": "gc_manual",
                                "runtime": "runtime", "port": "hosted"}, HOSTED_BODY),
                       encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"`std` + `hosted` 编不过：{errs[:1]}"
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        blob, info = lomelf.compile_ll(ir, [lomelf.load_foreign(libc)], allow_ports=True)
        assert info["shared"] == ["libc.so.6"], f"没有链上真 libc: {info}"
        assert "abs" in info["imports"], f"宿主层的名字不在导入表里: {info['imports']}"
        assert b".dynsym" in blob and b".interp" in blob, "std+hosted 的镜像不是动态的"
        exe = td / "std_hosted.elf"
        exe.write_bytes(blob)
        r = LVMTest.ELF._sh(f"chmod +x {LVMTest.ELF._wsl_path(exe)} && "
                            f"{LVMTest.ELF._wsl_path(exe)}", 120)
        assert r.returncode == 7, (
            f"std+hosted 的产物跑出 {r.returncode}（期望 7）：{r.stderr[-300:]!r}")
    print("      std+hosted: 链真 libc.so.6、abs 进导入表、跑出 7")


@test
def test_no_std_with_hosted_is_refused_by_both_front_ends():
    """`#539` 第 3、4 条：`no_std` + `hosted` 的拒绝要发生在**用户入口**，两个前端都要。

    参考实现点名两个取值与那条需求链（`CORE_CONFLICTS` 的原文）；自举驱动拒绝并把
    锚点放在**后写的那一行**。两边报文的措辞本来就分叉（`docs/158` §4），这里核的是
    "都拒绝 + 参考点名 + 自举指对行"，不核文本逐字节。
    """
    cell = {"mode": "no_std", "port": "hosted"}
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, _ir, _pot = _emit(td, "nostd_hosted", cell, START_ONLY_BODY)
        assert rc != 0, "参考实现收下了 `no_std` + `hosted`"
        for v in ("no_std", "hosted"):
            assert v in diag, f"参考实现的诊断没点名 {v!r}\n{diag[-400:]}"
        assert "不能同时选" in diag or "冲突" in diag, f"没说清是冲突\n{diag[-400:]}"
        src = td / "nostd_hosted.lomt"     # `_emit` 已经把同一份源写在那里
        rc2, _out, err = _selfhost_ir(td, src)
        assert rc2 != 0, "自举驱动收下了 `no_std` + `hosted`"
        assert "E22" in err, f"自举驱动拒绝时没给 E22：{err[-300:]!r}"
        assert "line 3" in err, (
            f"自举驱动的锚点不在后写的那一行（`hosted` 是第 3 行）：{err[-300:]!r}")
    print("      no_std+hosted: 参考点名两个取值与需求链；自举拒绝并锚在第 3 行")


@test
def test_no_gc_is_accepted_and_the_linker_checks_the_claim():
    """`no_gc`（`#538`）：值是核心取值，产物**自己声明**不带收集器，链接器核这句话。

    三件事：`choose no_gc` 编得过；IR 里有 `; loment-gc: no_gc` 且**没有**收集器/根表；
    一份**伪造**的 IR（同一份 gc_auto 产物 + 那行声明）在**链接期**被点名拒 ——
    这是 `docs/227` 判据 B 的镜像，也是这个值"有牙"的地方。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, diag, ir, pot = _emit(td, "nogc", {"mode": "std", "gc": "no_gc",
                                              "runtime": "runtime"}, ALLOC_BODY)
        assert rc == 0, f"`choose no_gc` 编不过\n{diag[-400:]}"
        assert pot.get("gc") == "no_gc", f"Potato 记错了回收档: {pot.get('gc')!r}"
        assert "; loment-gc: no_gc" in ir, "产物没有声明它不带收集器"
        for sym in ("__loment_collect", "__loment_roots"):
            assert sym not in ir, f"`choose no_gc` 的产物里出现了 {sym}"
        # 同形状的一份自动档产物（确实带着收集器）—— 拿来伪造声明。
        rc, diag, auto_ir, _pot = _emit(td, "auto_forge", {"mode": "std", "gc": "gc_auto",
                                                           "runtime": "runtime"}, ALLOC_BODY)
        assert rc == 0 and "__loment_collect" in auto_ir, "夹具不成形：auto 产物没有收集器"
        try:
            lomelf.compile_ll("; loment-gc: no_gc\n" + auto_ir, [])
        except lomelf.Unsupported as e:
            assert "no_gc" in str(e), f"拒绝时没点名 no_gc: {e}"
        else:
            raise AssertionError("声明 no_gc 却带着收集器的 IR 被链接器收下了")
    print("      no_gc: 编得过、产物无收集器、伪造的声明在链接期点名拒")


def main() -> int:
    return loment_suite.run(TESTS, "loment_choose_matrix_test")


if __name__ == "__main__":
    sys.exit(main())
