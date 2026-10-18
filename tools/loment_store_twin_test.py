#!/usr/bin/env python3
"""loment_store_twin_test.py — `lompi/store/**` 的孪生判据（#679 / #719）。

## 它钉的是什么

`docs/158` §5 那条老规矩：**同一份源，两个编译器发出的 IR 逐字节相同**。这条判据把
`lompi/store/**`（随包发出的全部模块）逐个**当根单元**编一遍，两边比：

* 两边都编得出来 ⇒ **IR 必须逐字节相同**；
* **只**有一边编得出来 ⇒ 就是分叉（收下的那个实现漏了规矩，或拒的那个多拒了）；
* 两边都拒 ⇒ 不算分叉（拒的理由不在 IR 这一层，另有判据）。

`#679` 是这条判据的由来：0.1.4.6 的 `internet`/`os`/`host` 二十四个模块没有一个在
孪生判据里，第一次跑就抓到九处分叉（六份是**非法 IR**）。`#719` 补的是 `std`。

## 已知缺口是**棘轮**

`KNOWN_GAPS` 里的每一条都必须**仍然分叉** —— 修好之后这条判据会红，逼着删条目
（与 `loment_p8_test.M85_KNOWN_GAPS` 同一个形状：只许变短，不会过期变松）。

## 怎么跑

构建 seed 的 stage1（宿主格式：Windows 出 PE、Linux 出 ELF，都**直接在本机跑**，
不需要 clang / WSL），再把每个模块的相对路径喂给它 `subprocess.run(cwd=仓库根)`。

用法: python tools/loment_store_twin_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_dist  # noqa: E402
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "lompi" / "store"


def _stage1() -> Path:
    """把种子链成**宿主格式**的 stage1（不落 `loment/build/dist/`，避免并行分片互踩）。"""
    td = tempfile.mkdtemp(prefix="loment-store-twin-")
    tgt = loment_dist._host_target()                      # noqa: SLF001
    out = Path(td) / ("stage1.exe" if tgt == "pe" else "stage1.elf")
    out.write_bytes(loment_dist._lomelf_link(                 # noqa: SLF001
        loment_seed.reference_ir("dev"), tgt))
    if tgt == "elf":
        out.chmod(0o755)
    return out


def _ref_ir(path: Path) -> str | None:
    """参考实现给这份源的 IR; 它自己拒了就给 `None`（那不是孪生分叉）。"""
    try:
        mod = lomentc.load(path)
        deps = lomentc.resolve_deps(mod, ROOT, path.parent, entry=path)
        errs = lomentc.check(mod, deps=deps)
        if errs:
            return None
        return lomentc.emit_llvm(mod, ROOT, deps)
    except Exception:                                      # noqa: BLE001
        return None


def _declared_grammar(path: Path) -> str:
    import potato_from  # noqa: PLC0415

    g, _err, declared = potato_from.read_grammar_decl(
        path.read_text(encoding="utf-8", errors="replace"))
    return g if declared else "loment"


#: **已知缺口**（棘轮：只许变短）。键是 `lompi/store` 下的相对路径。
KNOWN_GAPS: dict[str, str] = {
    "std/0.1.0/base32.lomt":
        "#719：移位右操作数里 `5 * j` 的类型没有折进来（自举落 i64、参考 i32）",
    "std/0.1.0/matrix.lomt":
        "#719：`0 as u64 as ptr` 这种**链式**转型在自举侧只做第一层，少了 `inttoptr`",
    "std/0.1.0/rational.lomt":
        "#719：64 位字面量的环绕（自举侧只做到 32 位；`9223372036854775808` 没折成负数）",
    "std/0.1.0/std.lomt":
        "#719：门面太大，自举侧检查器的符号表装不下（参考实现无上限）",
    "std/0.1.0/f64fmt.lomt":
        "#719：链式转型只做第一层 —— `x as i32 as i64` 缺了第二条 `sext`（自举发 "
        "`store i64 %i32值`，是非法 IR）",
    "std/0.1.0/libl.lomt":
        "#719：L0 的安全被调集合比参考**大**：自举把 `bsearch` 的首参判成不外逃、"
        "参考判成外逃 —— 于是 `bsearch_u32` 的 `alloc(4)` 自举提到栈上（`slot.buf`）、"
        "参考留在堆上。测试面里 `bsearch` 不接收它，但这一格两边必须同集合",
    "std/0.1.0/stdio.lomt":
        "#719：同 `libl`（经 `stdlib` 的 `bsearch_u32` 进来：`alloc(4)` 提栈 / 留堆）",
    "std/0.1.0/stdlib.lomt":
        "#719：同 `libl`（`bsearch_u32` 的 `alloc(4)`：自举 L0 提栈、参考留堆）",
}


def test_store_packages_are_twins() -> None:
    """`lompi/store/**` 每一份：接受集相同，IR 逐字节相同（已知缺口是一张棘轮）。"""
    stage1 = _stage1()
    same, both_refused, gaps, bad = [], [], [], []
    for target in sorted(STORE.glob("**/*.lomt")):
        rel = target.relative_to(ROOT).as_posix()
        key = target.relative_to(STORE).as_posix()
        if _declared_grammar(target) != "loment":
            continue                      # 别的读法不在自举侧的目标内（docs/188 §4.1）
        want = _ref_ir(target)
        r = subprocess.run([str(stage1), rel], capture_output=True, cwd=str(ROOT))
        got = r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        if want is None and got is None:
            both_refused.append(key)
            continue
        if want is not None and got is not None and want == got:
            same.append(key)
            continue
        if key in KNOWN_GAPS:
            gaps.append(key)
            continue
        detail = ("参考拒 / 自举收" if want is None else
                  "自举拒 / 参考收" if got is None else
                  "两边都发得出但 IR 不同")
        bad.append(f"{key}（{detail}）")
    # 棘轮：登记过的缺口必须**仍然**分叉；修好了就删条目。
    for key in KNOWN_GAPS:
        assert key not in same, (
            f"{key}: 登记在 KNOWN_GAPS 里，但两边**已经一致** —— 请删掉那条登记"
            f"（{KNOWN_GAPS[key][:40]}…）")
    assert not bad, (
        f"这些模块两边的接受集或 IR 不同、且没有登记：{bad} —— 要么修，要么在 "
        f"KNOWN_GAPS 里点名（#679 / #719）")
    assert same, "一份都没比成 —— 判据自己失效了"
    print(f"      lompi/store：{len(same)}/{len(same) + len(gaps) + len(both_refused)} 逐字节一致"
          f"；两边都拒 {len(both_refused)}；已知缺口 {len(gaps)}（棘轮，只许变短）")
    for key in gaps:
        print(f"        已知缺口: {key}（{KNOWN_GAPS[key][:60]}…）")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_store_twin_test")


if __name__ == "__main__":
    sys.exit(main())
