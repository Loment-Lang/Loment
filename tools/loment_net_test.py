#!/usr/bin/env python3
# loment_net_test.py — `lom/net.lom`（号 → 面）这条 L0 契约的判据 (docs/229)
#
# 为什么单独一条判据: 这条契约的对账面**在四个地方**, 而其中一处（`lom_audit`）
# 在门禁里是**已知红**（本机与 CI 都缺 `LinuxFUAI/` 姊妹检出，见 gate-baseline.txt）——
# 也就是说 `lom_audit` 的红**不构成信号**。要让它可判，就得有一条**能绿也能红**的判据，
# 所以这条独立出来跑同一组对账。
#
# 三件事:
#   1. 契约本身的形状（29 个号 / 21 个 net / **0 个点名不做** / 不相交）；
#   2. 四处对账（编译器、链接器、自举 checker、自举 potato）；
#   3. **这条判据自己会红** —— 四个方向各改一次，看它报不报。
#
# 运行: python tools/loment_net_test.py

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lom_audit  # noqa: E402
import lomelf  # noqa: E402
import lomc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _contract() -> tuple[dict[int, str], dict[int, str]]:
    """`(号 → 面, 点名不做的号 → 面)` —— 两份都从 `lom/net.lom` 现读，不抄。"""
    mod = lomc.load(lom_audit.LOM_NET)
    errs = lomc.check(mod)
    assert errs == [], f"lom/net.lom 自己不合法: {errs[:2]}"
    sc = next(e for e in mod.enums if e.name == "Syscall")
    # 2026-10-12 起联网面没有「点名不做」了 —— 枚举**不在**契约里就是空（留空枚举
    # 过不了检查器），所以形状判据钉的是「它不在 **或** 它是空的」。
    nd = next((e for e in mod.enums if e.name == "NotDispatched"), None)
    return ({v.value: v.meta.get("surface") for v in sc.variants},
            {v.value: v.meta.get("surface") for v in
             (nd.variants if nd is not None else [])})


def _audit() -> list[str]:
    diffs: list[str] = []
    lom_audit.audit_net(diffs)
    return diffs


@test
def test_contract_shape():
    """契约自己得先立得住：号不重复、面在词汇里、点名不做与派发面不相交。"""
    want, nd = _contract()
    mod = lomc.load(lom_audit.LOM_NET)
    surf = next(e for e in mod.enums if e.name == "Surface")
    names = {v.name for v in surf.variants}
    assert want, "Syscall 是空的"
    assert set(want.values()) <= names, f"有号落在未声明的面上: {set(want.values()) - names}"
    assert set(nd.values()) <= names, "点名不做那一族也得报得出来面"
    # **面名只有 vocabulary 里那几个**：`other` 是兜底，**不进**这张表（进了就等于把
    # "没点名的号"写成一个号，而它按定义是"本表没点名的"）
    assert "other" not in set(want.values()), "other 不该出现在 Syscall 里"
    assert not (set(want) & set(nd)), f"同一个号既派发又点名不做: {sorted(set(want) & set(nd))}"
    # 数：与 docs/224 §6 那几行实测对得上（改了这里就该改文档，反之亦然）
    net = [n for n, s in want.items() if s == "net"]
    assert len(want) == 29, f"派发面是 29 个号，得到 {len(want)}"
    assert len(net) == 21, f"net 面是 21 个号，得到 {len(net)}"
    assert len(nd) == 0, f"点名不做**已经清零**（联网面补完），得到 {len(nd)}"
    print(f"      {len(want)} 个号（net {len(net)}）+ {len(nd)} 个点名不做，面 {sorted(names)}")


@test
def test_four_counterparts_agree():
    """四处对账面逐条一致（`docs/229` §2）：编译器 / 链接器 / 自举 checker / 自举 potato。"""
    diffs = _audit()
    assert diffs == [], "对账不齐:\n  " + "\n  ".join(diffs[:6])
    print("      编译器 · 链接器 · 自举 checker · 自举 potato —— 四处逐条对上")


@test
def test_the_criterion_can_go_red():
    """**判据测过自己会红才算判据**（`docs/190` §1.4 那条纪律）—— 四个方向各改一次。

    改的是**内存里的副本**（不落盘、不碰工作树）：这四条对了，上面那条 `diffs == []`
    才说明"对得上"，而不是"根本没在看"。
    """
    orig_c = dict(lomentc.SURFACE_NUMBERS)
    orig_pe = lomelf.PE_SYSCALLS
    cases: list[tuple[str, object]] = []
    try:
        lomentc.SURFACE_NUMBERS[41] = "file"          # ① 面名不同
        cases.append(("面名不同", lambda: _audit()))
        del lomentc.SURFACE_NUMBERS[55]               # ② 计数词汇少一个号
        cases.append(("少一个号", lambda: _audit()))
        lomentc.SURFACE_NUMBERS.clear()
        lomentc.SURFACE_NUMBERS.update(orig_c)
        lomelf.PE_SYSCALLS = tuple(t for t in orig_pe if t[0] != 7)   # ③ 派发面少一个
        cases.append(("派发面少一个", lambda: _audit()))
        lomelf.PE_SYSCALLS = orig_pe
        # ④ **顺手实现了一个"点名不做"的号** —— 这一格最值得钉: 它正是"名单会腐烂"的形状
        # 「顺手多认一个号」：`NotDispatched` 已经空了，所以拿一个**谁都没有的号**当探针
        # —— 这一格钉的仍是"派发面比声明面宽"那一类漂移。
        lomelf.PE_SYSCALLS = orig_pe + ((9999, "net", "__ws_nothing"),)
        cases.append(("派发面多一个", lambda: _audit()))
        for tag, fn in cases:
            assert fn(), f"改了「{tag}」而判据没报 —— 这条判据是空转的"
    finally:
        lomentc.SURFACE_NUMBERS.clear()
        lomentc.SURFACE_NUMBERS.update(orig_c)
        lomelf.PE_SYSCALLS = orig_pe
    assert _audit() == [], "夹具没把状态还回去"
    print("      四个方向（面名 / 少号 / 派发面 / 顺手实现）各红一次，撤回后回绿")


def main() -> int:
    return loment_suite.run(TESTS, "loment_net_test")


if __name__ == "__main__":
    sys.exit(main())
