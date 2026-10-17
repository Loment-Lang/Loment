#!/usr/bin/env python3
"""loment_suite.py — 各判据套件共用的**运行器**（`#371`）。

为什么要有它
------------
84 个套件里 71 个各自抄了一份一模一样的循环：

```python
for name, fn in TESTS:
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as e:
        failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
print(f"\\n{suite}: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
return 1 if failed else 0
```

这份循环有**两个洞**，都是"绿得看不出来"那一类：

1. **跳过被记成通过。** 判据里的环境门写成
   `print("      SKIP: 无 clang/WSL"); return` —— 在 `try` 眼里那是**正常返回**，
   于是计入 PASS，摘要行照报 `31/31 通过`。实测：门禁 807 条判据里 **219 条**会走这条路，
   而 `loment_elf_test` 被门着的**正好是整条运行时护栏**（`#371`）。
2. **套件是空的也过。** `TESTS` 为空时 `0/0 通过`，`return 0`。把判据删光 = 绿。

这个运行器把两件都堵上：**跳过的判据要认领到名字**（`  SKIP  <name>: 原因`，供 `ci.py`
对账签入的预算），**空套件直接判失败**。

判据本体**一行都不用改**
------------------------
运行器靠"这条判据打没打 `SKIP` 行"来认，不要求它们换写法 —— 那 219 处保持原样。
各套件只是把 `main()` 换成一行 `return loment_suite.run(TESTS, "<套件名>")`；
循环之前的前置 setup（例如 `loment_ports_test` 的 `_FIX.build()`）留在 `main()` 里。
"""

from __future__ import annotations

import contextlib
import io
import sys

__all__ = ["run", "entries"]


def entries(tests) -> list[tuple[str, object]]:
    """归一化 `TESTS` —— 各套件两种写法都有：`(name, fn)` 与裸 `fn`。"""
    out: list[tuple[str, object]] = []
    for t in tests:
        if isinstance(t, tuple):
            out.append((t[0], t[1]))
        else:
            out.append((t.__name__, t))
    return out


def _skips(out: str) -> list[str]:
    """这条判据打的 `SKIP` 行里的原因（判据只认行首是 `SKIP` 的，理由同 `ci.count_skips`）。"""
    got = []
    for ln in out.splitlines():
        s = ln.lstrip()
        if s.startswith("SKIP"):
            got.append(s[len("SKIP"):].lstrip(": ").strip())
    return got


def run(tests, suite: str, *, only: str | None = None) -> int:
    """跑一个套件。返回退出码（0 = 没有 FAIL）。`only` = 只跑名字含这个子串的判据。

    输出契约（`ci.py` 与 `gate.yml` 按这个读，改这里要两边一起看）：

    * `  PASS  <name>` / `  FAIL  <name>: <类型>: <信息>`
    * `  SKIP  <name>: <原因>` —— **跳过必须有名字**，否则预算对不了账
    * 判据自己打的那几行说明照旧原样输出（它们是诊断）
    * 最后一行：`<套件>: <通过>/<总> 通过[, <跳过> SKIP][, <失败> 失败]`，
      失败时再跟一段 `<红: 判据名: 类型: 信息；…>` —— `ci.py` 只把**最后一行**贴给
      CI 看（`_run_one_check`），所以"哪条红的、为什么"必须在那一行里。这条原本只有
      `loment_pe_test` 自己记得（它那行注释写着"别的套件也该这么写"），现在每个套件都有。
    """
    items = entries(tests)
    if not items:
        # 老循环在这里给 `0/0 通过` + `return 0` —— 把判据删光就绿了。空套件**永远**是错。
        print("  FAIL  <suite>: 这个套件一条判据都没有 (#371)")
        print(f"\n{suite}: 0/0 通过, 0 SKIP, 1 失败")
        return 1
    if only:
        items = [(n, f) for n, f in items if only in n]
        if not items:
            print(f"  (没有判据名含 {only!r})")
            return 0

    npass = nskip = 0
    failed: list[tuple[str, BaseException]] = []
    for name, fn in items:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn()
        except BaseException as e:                                   # noqa: BLE001
            # SystemExit 之类也算失败 —— 与 `ci.py` 读 `main()` 退出码那一路同一条纪律。
            _emit_diagnostics(buf.getvalue(), drop_skips=True)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed.append((name, e))
            continue
        out = buf.getvalue()
        reasons = _skips(out)
        _emit_diagnostics(out, drop_skips=bool(reasons))
        if reasons:
            print(f"  SKIP  {name}: {reasons[0]}")
            nskip += 1
        else:
            print(f"  PASS  {name}")
            npass += 1

    tail = f"\n{suite}: {npass}/{len(items)} 通过"
    if nskip:
        tail += f", {nskip} SKIP"
    if failed:
        tail += f", {len(failed)} 失败"
        why = "；".join(f"{n}: {type(e).__name__}: {str(e)[:400]}".replace("\n", " ")
                        for n, e in failed)
        tail += f"\n  红: {why}"
    print(tail)
    return 1 if failed else 0


def _emit_diagnostics(out: str, *, drop_skips: bool) -> None:
    """把判据自己打的说明行原样露出来；`drop_skips` 时掐掉它的 `SKIP` 行（运行器会重打一条带名字的）。"""
    if not out:
        return
    for ln in out.splitlines():
        if drop_skips and ln.lstrip().startswith("SKIP"):
            continue
        print(ln)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
