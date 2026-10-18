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
KNOWN_GAPS: dict[str, str] = {}

#: `std` 的**门面**（`module std` + 127 条 `use`，**零个函数**）。它不单独比：
#: 它自己的贡献是空的，而它的 127 个模块**每一个**都在本判据里单独比过。
#: 门面本身要钉的是"平名字空间不撞名"，由
#: `lomentc_test::test_std_package_is_one_unit_without_name_collisions` 管。
#:
#: ⚠ 自举编译器编 127 模块的单元**超线性**慢（`std.lomt` 头注实测 n=64 已 187 秒）——
#: 那是**速度**缺口，不是接受集/IR 分叉，写在这里是为了别把它误当孪生缺口。
FACADE = "std/0.1.0/std.lomt"


def test_store_packages_are_twins() -> None:
    """`lompi/store/**` 每一份：接受集相同，IR 逐字节相同（已知缺口是一张棘轮）。"""
    stage1 = _stage1()
    same, both_refused, gaps, bad = [], [], [], []
    for target in sorted(STORE.glob("**/*.lomt")):
        rel = target.relative_to(ROOT).as_posix()
        key = target.relative_to(STORE).as_posix()
        if _declared_grammar(target) != "loment":
            continue                      # 别的读法不在自举侧的目标内（docs/188 §4.1）
        if key == FACADE:
            continue                      # 零函数的门面；见 `FACADE` 的说明
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


def test_the_facade_is_still_only_a_facade() -> None:
    """被跳过的那一份**必须继续是门面**（零函数）—— 谁往里面加代码，这条就红。

    跳过 `std.lomt` 的理由是"它自己的贡献是空的"；一旦它有了函数体，那个理由就不
    成立，必须把它放回逐字节比对（并且接受那份超线性编译时间）。
    """
    import re  # noqa: PLC0415
    text = (STORE / FACADE).read_text(encoding="utf-8")
    hits = re.findall(r"(?m)^\s*(?:pub )?fn\s+\w+", text)
    assert not hits, (
        f"{FACADE} 里出现了函数（{hits[:3]}）—— 它不再是纯门面："
        f"把它从跳过名单里拿掉，按真模块逐字节比")
    print("      门面：`std.lomt` 仍然零函数（127 条 use，逐个模块单独比过）")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_store_twin_test")


if __name__ == "__main__":
    sys.exit(main())
