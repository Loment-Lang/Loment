"""LNotary · 产物公证人：独立验证器（`docs/358` §4.2 / `docs/370`）。

**零工具链 import**：只用标准库。不 import `lomentc`、`potato`、`loment.lspe`。
它核对产物里已经写下的账，不重新计算证据，也不读取源码。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 认识的 Potato 版本上限。加一版要在这里加一 —— 这是公证人的规格，不是编译器的。
#: v24（2026-11-21）：第五维改名 `vm` → `lvm`；本文件的 `LVM_ON_VALUES` 两边都认。
MAX_VERSION = 24

#: 声明"这份产物要求公证"的用户开关。编译器把它记进 `switches`。
CLAIM_SWITCH = "lnotary_true"

#: **LVM 的激活声明**。今天的维度值是 `vm`；LVM 线正在把拼写改成 `lvm`
#: （2026-11-21 进行中）。两条都认 —— 改名落地那一刻，门控不需要再改一次。
LVM_ON_VALUES = ("vm", "lvm")


def lvm_enabled(doc: dict) -> bool:
    return doc.get("vm") in LVM_ON_VALUES or doc.get("lvm") == "lvm"

SYSTEMS = ("gc", "runtime", "port", "vm", "lms", "lspe")
#: v24 起第五维改名：`systems` 里的键也跟着叫 `lvm`（旧产物仍按 `vm` 读）。
SYSTEMS_V24 = ("gc", "runtime", "port", "lvm", "lms", "lspe")
SYSTEM_DETAIL = {"gc": "gc_ladder", "lms": "lms_levels", "lspe": "lspe"}
LEVELS = ("lms_check", "lms_manage", "lms_guard", "lms_reject",
          "lms_crash", "lms_restart", "lms_seed")
BUDGETS = frozenset({"none", "instructions", "state", "outside"})

_UNKNOWN = object()


@dataclass(frozen=True)
class Error:
    code: str
    message: str


@dataclass
class Verdict:
    version: int = -1
    errors: list[Error] = field(default_factory=list)
    claimed_switches: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.errors


def _err(v: Verdict, code: str, message: str) -> None:
    v.errors.append(Error(code, message))


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _nonneg(x: object) -> bool:
    return _is_int(x) and x >= 0


def _keys_check(v: Verdict, code: str, obj: dict, want: tuple[str, ...], where: str) -> bool:
    got = set(obj)
    want_s = set(want)
    ok = True
    for k in sorted(got - want_s):
        _err(v, code, f"{where} 有不认识的键 {k!r}")
        ok = False
    for k in sorted(want_s - got):
        _err(v, code, f"{where} 少了 {k!r}")
        ok = False
    return ok


def _parse_version(v: Verdict, doc: dict) -> int | None:
    raw = doc.get("potato")
    if not isinstance(raw, str) or len(raw) < 2 or raw[0] != "v" or not raw[1:].isdigit():
        _err(v, "LNT-VER", f"potato 版本必须是 vN，得到 {raw!r}")
        return None
    n = int(raw[1:])
    v.version = n
    if n > MAX_VERSION:
        _err(v, "LNT-VER", f"potato 版本 {raw} 超过公证人认识的 v{MAX_VERSION}")
        return None
    return n


def _count_true(levels: object) -> int | None:
    if not isinstance(levels, dict) or set(levels) != set(LEVELS):
        return None
    if not all(isinstance(levels[k], bool) for k in LEVELS):
        return None
    return sum(1 for k in LEVELS if levels[k])


def _expected_cost(k: str, ladder: object, levels: object, lspe: object):
    if k == "gc":
        if isinstance(ladder, dict) and _nonneg(ladder.get("total_sites")):
            return {"sites": ladder["total_sites"]}
        return _UNKNOWN
    if k == "lms":
        c = _count_true(levels)
        return _UNKNOWN if c is None else {"levels": c}
    if k == "lspe":
        if isinstance(lspe, dict) and _nonneg(lspe.get("sites")):
            return {"sites": lspe["sites"]}
        return _UNKNOWN
    return None


def _check_systems(v: Verdict, doc: dict) -> None:
    sy = doc.get("systems")
    if not isinstance(sy, dict):
        _err(v, "LNT-SYS", f"systems 必须是对象（六个系统各一条），得到 {type(sy).__name__}")
        return
    keys = SYSTEMS_V24 if v.version >= 24 else SYSTEMS
    _keys_check(v, "LNT-SYS", sy, keys, "systems")
    ladder = doc.get("gc_ladder")
    levels = doc.get("lms_levels")
    lspe = doc.get("lspe")
    for k in keys:
        if k not in sy:
            continue
        e = sy[k]
        if not isinstance(e, dict):
            _err(v, "LNT-SYS", f"systems.{k} 必须是对象")
            continue
        unknown = set(e) - {"state", "detail", "cost"}
        if unknown:
            _err(v, "LNT-SYS", f"systems.{k} 有不认识的键: {sorted(unknown)}")
        if not {"state", "detail", "cost"} <= set(e):
            _err(v, "LNT-SYS", f"systems.{k} 必须同时有 state/detail/cost")
            continue
        det = SYSTEM_DETAIL.get(k)
        if e["detail"] != det:
            _err(v, "LNT-SYS", f"systems.{k}.detail 是 {e['detail']!r}，应为 {det!r}")
        want_state = (lspe.get("state") if isinstance(lspe, dict) else None) \
            if k == "lspe" else doc.get(k)
        if e["state"] != want_state:
            _err(v, "LNT-SYS",
                 f"systems.{k}.state 是 {e['state']!r}，但字段是 {want_state!r}")
        want_cost = _expected_cost(k, ladder, levels, lspe)
        if want_cost is not _UNKNOWN and e["cost"] != want_cost:
            _err(v, "LNT-SYS",
                 f"systems.{k}.cost 是 {e['cost']!r}，应为 {want_cost!r}")


def _check_gc(v: Verdict, n: int, ladder: object) -> None:
    if not isinstance(ladder, dict):
        _err(v, "LNT-GC", "gc_ladder 必须是对象")
        return
    comps = ["l0", "l0_5", "l1", "l2", "l3"] if n >= 19 else ["l0", "l1", "l2", "l3"]
    want = tuple(comps + ["total_sites"])
    ok = _keys_check(v, "LNT-GC", ladder, want, "gc_ladder")
    good = True
    for k in want:
        if not _nonneg(ladder.get(k)):
            _err(v, "LNT-GC", f"gc_ladder.{k} 必须是非负整数，得到 {ladder.get(k)!r}")
            good = False
    if ok and good:
        total = sum(ladder[k] for k in comps)
        if total != ladder["total_sites"]:
            _err(v, "LNT-GC",
                 f"gc_ladder 自洽失败：{' + '.join(comps)} = {total}，"
                 f"total_sites = {ladder['total_sites']}")


def _check_levels(v: Verdict, levels: object) -> None:
    if not isinstance(levels, dict):
        _err(v, "LNT-LMS", "lms_levels 必须是对象（七个布尔）")
        return
    if not _keys_check(v, "LNT-LMS", levels, LEVELS, "lms_levels"):
        return
    for k in LEVELS:
        if not isinstance(levels[k], bool):
            _err(v, "LNT-LMS", f"lms_levels.{k} 必须是布尔，得到 {levels[k]!r}")


def _check_budgets(v: Verdict, doc: dict, budgets: object) -> None:
    if not isinstance(budgets, dict):
        _err(v, "LNT-BUD", "lms_budgets 必须是对象（七个取值）")
        return
    if not _keys_check(v, "LNT-BUD", budgets, LEVELS, "lms_budgets"):
        return
    for k in LEVELS:
        if budgets[k] not in BUDGETS:
            _err(v, "LNT-BUD",
                 f"lms_budgets.{k} 是 {budgets[k]!r}，只允许 {sorted(BUDGETS)}")
    if doc.get("runtime") == "no_runtime":
        bad = [k for k in LEVELS if budgets.get(k) == "state"]
        if bad:
            _err(v, "LNT-RT",
                 f"runtime = no_runtime，但 {bad} 声明了 state 预算")


def _check_lspe(v: Verdict, lspe: object) -> None:
    if not isinstance(lspe, dict):
        _err(v, "LNT-LSPE", "lspe 必须是对象")
        return
    if not isinstance(lspe.get("state"), str):
        _err(v, "LNT-LSPE", "lspe.state 必须是字符串")
    if not _nonneg(lspe.get("sites")):
        _err(v, "LNT-LSPE", f"lspe.sites 必须是非负整数，得到 {lspe.get('sites')!r}")


def _check_switches(v: Verdict, n: int, doc: dict) -> None:
    sw = doc.get("switches", [])
    if not isinstance(sw, list):
        _err(v, "LNT-SW", "switches 必须是数组")
        return
    seen: set[str] = set()
    claimed: set[str] = set()
    for item in sw:
        if (not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("on"), bool)):
            _err(v, "LNT-SW", f"switches 的条目必须是 {{name, on}}，得到 {item!r}")
            continue
        name = item["name"]
        if name in seen:
            _err(v, "LNT-SW", f"开关 {name!r} 出现了两次")
        seen.add(name)
        if item["on"]:
            claimed.add(name)
    v.claimed_switches = claimed
    if CLAIM_SWITCH in claimed:
        if n < 20 or not isinstance(doc.get("systems"), dict):
            _err(v, "LNT-CLAIM",
                 f"声明了 {CLAIM_SWITCH}，但产物缺少 v20+ 的 systems 台账")


def verify_potato(doc: object) -> Verdict:
    """核对一份 Potato。只读；不修改 `doc`。"""
    v = Verdict()
    if not isinstance(doc, dict):
        _err(v, "LNT-TOP", f"产物必须是对象，得到 {type(doc).__name__}")
        return v
    n = _parse_version(v, doc)
    if n is None:
        return v
    #: **启用条件**（用户 2026-11-21）：LNotary 只在 LVM 被 choose 启用的产物上生效。
    #: 没有 LVM 就没有 `sw_cap` 基质，公证人的站点/fault 声明会变成另一件更弱的事。
    if not lvm_enabled(doc):
        _err(v, "LNT-VM",
             f"LNotary 只在 choose lvm（旧拼写 choose vm）的产物上启用；"
             f"这份是 vm={doc.get('vm')!r} lvm={doc.get('lvm')!r}")
    if n >= 20:
        _check_systems(v, doc)
    if n >= 10:
        _check_gc(v, n, doc.get("gc_ladder"))
    if n >= 17:
        _check_levels(v, doc.get("lms_levels"))
    if n >= 18:
        _check_budgets(v, doc, doc.get("lms_budgets"))
    if n >= 16:
        _check_lspe(v, doc.get("lspe"))
    _check_switches(v, n, doc)
    return v


def main(argv: list[str] | None = None) -> int:
    import json
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: lnotary <potato.json>", file=sys.stderr)
        return 2
    path = args[0]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        print(f"lnotary: {path}: {e}", file=sys.stderr)
        return 2
    v = verify_potato(doc)
    if v.ok:
        print(f"lnotary: ok ({path}, potato v{v.version})")
        return 0
    for e in v.errors:
        print(f"lnotary: {e.code}: {e.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
