#!/usr/bin/env python3
"""`docs/210` §7 那个**最该先量的数**：证明面在真实代码里有多宽。

「证明面有多宽」= 真代码里的 `alloc` 站点，有多少落进 L0/L1/L2（编译器证得住、不进收集器），
有多少落进 L3（残差）。**每一格都是一个不读源码可判的数**（v10 的 `gc_ladder`），所以这把尺子
只做一件事：把语料跑成 `gc_auto_alpha`，把 `gc_ladder` 加起来。

## 为什么要「改成 alpha」

语料（`loment/examples`、`loment/lib`、`loment/selfhost`）默认是 `gc_manual` —— 那就**没有
分层可言**（`gc_ladder` 恒为 `{0,0,0,total}`）。要问「这层证得住多少」，就得让它走 alpha 那一趟，
所以这里**注入** `choose gc_auto_alpha` + `choose runtime`（写在 `module` 行之后）。

**已经在源里写了 `choose gc` 的**（今天只有 `loment/examples/gc_ladder`）**原样跳过**，不注入 ——
它自己已经表态了。

## 口径与边界（明写）

* 数的是 **`alloc` 调用点**（`gc_ladder` 的定义）。`str_concat` 也落堆，但它不是 `alloc` 站点，
  **不计入** —— 这一层是 `docs/210` 设计里就写明的口径，不是这里的省略。
* 注入只加 `choose`，**不改函数体**，所以「站点数」是这份源本来就有的。
* 注入后的副本写在**本进程私有的临时目录**里 —— 那段源**不进仓库**（见 `_ladder_of`）。
  它**不必**是源的同级文件：名字形式的 `use` 按 `lomentc.NAME_ROOTS`（仓根的
  `loment/lib` / `loment/examples` / `loment/selfhost` / `loment/tools`）解析，**没有一层是
  「入口文件旁边」**；路径形式的 `use` 先试 `root` 再试文件自己的目录，语料里唯一一条
  （`loment/examples/demo.lomt` 的 `use "lom/fujr.lom"`）正是 `root` 那一条。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import lomentc  # noqa: E402

#: 语料 = 仓里**自己的 Loment 代码**。三个目录各有分工：示例（小、面宽）、
#: std 库、以及自举编译器本体（最大的一份真代码）。
CORPUS = ("loment/examples", "loment/lib", "loment/selfhost")

_INJECT = ("choose gc_auto_alpha", "choose runtime")


def _inject(text: str) -> str | None:
    """把 alpha 那两个 `choose` 插到 `module` 行之后；源里已表态的回 `None`（跳过）。

    **也跳过声明 `no_std` 的源**（`choose mode no_std` 与简写 `choose no_std` 两种拼法）：
    `mode=no_std` 与 `gc=gc_auto` 是冲突表里的一对（自动回收要一个运行期），注入 alpha 会让
    这份源自检就失败 —— 量不到东西而整条判据红，红的却不是尺子坏了。这类源**本来就不在**
    这一维的可测面上，跳过是对的。

    **为什么必须 `module` 行之后**：`module` 是文件的第一件事，`choose` 排在它后面（语言规定）。
    没有 `module` 行（不是一份 Loment 单元）也回 `None`。
    """
    if "choose gc" in text or any(
            ln.split()[:1] == ["choose"] and "no_std" in ln.split()
            for ln in text.split("\n")):
        return None
    out, done = [], False
    for ln in text.split("\n"):
        out.append(ln)
        if not done and ln.strip().startswith("module "):
            out += list(_INJECT)
            done = True
    return "\n".join(out) if done else None


def _ladder_of(path: Path) -> dict:
    """一份源在 alpha 档下的 `gc_ladder`。注入后的副本**写在本进程私有的临时目录**里。

    **为什么不能写在源同目录**（2026-10-10 实测的那条并行假红）：那样它就是一个
    `loment/selfhost/*.lomt` 能匹配到的**仓库内固定路径**。原先赌的是"点开头的名字
    glob 匹配不到" —— 那是 **shell / `glob` 模块**的规矩，`pathlib.Path.glob` **不遵守**：
    它按 `fnmatch` 比，`*.lomt` 照样命中 `.tmp-gcsurface-driver.lomt`。于是分片把
    `loment_gc_surface_test` 与 `loment_rel_test` 放进同一片之后，
    `loment_rel_test::test_manifest_order_is_platform_independent` 会在窗口里数到多出来的
    那一份，报"这一组在清单里的次序不是字节序" —— 5/6，单跑 6/6（`docs/213` §7 记的那次）。
    """
    text = path.read_text(encoding="utf-8")
    src = _inject(text)
    if src is None:
        return {"skipped": True}
    with tempfile.TemporaryDirectory(prefix="gcsurface-") as tds:
        tmp = Path(tds) / path.name
        tmp.write_text(src, encoding="utf-8", newline="\n")
        mod, deps = lomentc.load_unit(tmp, ROOT)
        doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
        return doc["gc_ladder"]


def measure(dirs=CORPUS) -> list[tuple[str, dict]]:
    """逐文件量：`[(相对路径, gc_ladder), …]`，**按路径排序**（确定性）。跳过的也留着（带 `skipped`）。"""
    rows: list[tuple[str, dict]] = []
    for d in dirs:
        for p in sorted((ROOT / d).glob("*.lomt")):
            rows.append((p.relative_to(ROOT).as_posix(), _ladder_of(p)))
    return rows


def total(rows) -> dict:
    t = {"l0": 0, "l1": 0, "l2": 0, "l3": 0, "total_sites": 0}
    for _rel, gl in rows:
        if gl.get("skipped"):
            continue
        for k in t:
            t[k] += gl[k]
    return t


def _pct(part: int, whole: int) -> str:
    return "0.0%" if whole == 0 else f"{100.0 * part / whole:.1f}%"


def report(rows) -> str:
    """人看的表：逐文件只列**有站点**的，再加汇总与「证明面占比」。"""
    t = total(rows)
    lines = ["每份源（只列有 alloc 站点的；跳过的不列，见 --all）："]
    n_skip = 0
    for rel, gl in rows:
        if gl.get("skipped"):
            n_skip += 1
            continue
        if gl["total_sites"]:
            lines.append(f"  {rel:44} l0={gl['l0']} l1={gl['l1']} l2={gl['l2']} "
                         f"l3={gl['l3']} total={gl['total_sites']}")
    proved = t["l0"] + t["l1"] + t["l2"]
    lines.append(f"\n汇总（{len(rows) - n_skip} 份，跳过 {n_skip} 份已表态的）：")
    lines.append(f"  L0={t['l0']}  L1={t['l1']}  L2={t['l2']}  L3={t['l3']}  "
                 f"total={t['total_sites']}")
    lines.append(f"  证明面（L0+L1+L2 / total）= {proved}/{t['total_sites']} "
                 f"= {_pct(proved, t['total_sites'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_gc_surface",
                                 description="GC 证明面的量尺（docs/210 §7）")
    ap.add_argument("--dirs", nargs="*", default=list(CORPUS),
                    help="语料目录（默认 examples / lib / selfhost）")
    ap.add_argument("--json", action="store_true", help="逐文件明细 + 汇总，JSON")
    a = ap.parse_args(argv)
    rows = measure(a.dirs)
    if a.json:
        print(json.dumps({"rows": [{"path": r, **g} for r, g in rows],
                          "total": total(rows)}, ensure_ascii=False, indent=1))
    else:
        print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
