"""LNotary · 同产物派生链（`docs/358` §3.2 / `docs/361` V5）。

一个**宇宙**不是进程，是四元组 `(产物, caps, 预算, 封印)` 加一条派生记录。
派生只允许**收缩**：子 caps ⊆ 父 caps，子预算 ≤ 父预算，深度 +1。
`sum` 是内容寻址的：根绑 `(封印, 根授权)`，子绑 `(父 sum, 差量)`。

这一层是**静态**的：它证明"子能碰的东西不多于父"，不证明哪些 `unsafe` 站点在子
权能下真的到得了（可达性，`docs/361` §7）。
"""

from __future__ import annotations

import hashlib
import json

from .verify import Error

SCHEMA = 1

#: 根的 `sum` 把**初始授权**也绑进来（`docs/358` 的"根 = seal"是它的简化读法）。
_ROOT_TAG = "lnotary-root/1"


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _err(out: list[Error], code: str, msg: str) -> None:
    out.append(Error(code, msg))


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _nonneg(x: object) -> bool:
    return _is_int(x) and x >= 0


def _is_hex64(x: object) -> bool:
    return (isinstance(x, str) and len(x) == 64
            and all(c in "0123456789abcdef" for c in x))


def _caps_of(u: dict, out: list[Error], where: str) -> set[str] | None:
    caps = u.get("caps")
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        _err(out, "LNT-GEN", f"{where}.caps 必须是字符串数组")
        return None
    if len(set(caps)) != len(caps):
        _err(out, "LNT-CAP", f"{where}.caps 有重复")
        return None
    return set(caps)


def _budget_of(u: dict, out: list[Error], where: str) -> dict[str, int] | None:
    b = u.get("budget")
    if not isinstance(b, dict):
        _err(out, "LNT-GEN", f"{where}.budget 必须是对象")
        return None
    for k, v in b.items():
        if not isinstance(k, str) or not _nonneg(v):
            _err(out, "LNT-BUD", f"{where}.budget.{k} 必须是非负整数")
            return None
    return dict(b)


def _norm_caps(caps: list[str]) -> list[str]:
    return sorted(set(caps))


def _norm_budget(budget: dict[str, int]) -> dict[str, int]:
    return {k: budget[k] for k in sorted(budget)}


def root_sum(seal: str, caps: list[str], budget: dict[str, int]) -> str:
    grant = {"caps": _norm_caps(caps), "budget": _norm_budget(budget)}
    return sha256_hex(_ROOT_TAG.encode("ascii") + b"\0"
                      + seal.encode("ascii") + b"\0"
                      + canonical_bytes(grant))


def child_sum(parent_sum: str, caps: list[str], budget: dict[str, int]) -> str:
    delta = {"caps": _norm_caps(caps), "budget": _norm_budget(budget)}
    return sha256_hex(parent_sum.encode("ascii") + b"\0" + canonical_bytes(delta))


def root_universe(seal: str, caps: list[str], budget: dict[str, int],
                  uid: str = "root") -> dict:
    return {
        "schema": SCHEMA,
        "id": uid,
        "parent": None,
        "seal": seal,
        "caps": _norm_caps(caps),
        "budget": _norm_budget(budget),
        "depth": 0,
        "sum": root_sum(seal, caps, budget),
    }


def derive_universe(parent: dict, caps: list[str], budget: dict[str, int],
                    genesis: dict, uid: str | None = None) -> dict:
    """派生一个子宇宙。**只收缩**；违反就抛 `ValueError`（带可读理由）。

    调用方负责先确认 `parent` 自身是合法的（`verify_forest` 或 `root_universe` 产出）。
    """
    pc = set(parent.get("caps") or [])
    extra = set(caps) - pc
    if extra:
        raise ValueError(f"cap_escalation: {sorted(extra)} 不在父的 caps 里")
    pb = parent.get("budget") or {}
    for k, v in budget.items():
        if k not in pb:
            raise ValueError(f"budget_escalation: 父没有资源 {k!r}")
        if v > pb[k]:
            raise ValueError(f"budget_escalation: {k}={v} > 父的 {pb[k]}")
    depth = int(parent.get("depth", 0)) + 1
    if depth > int(genesis.get("max_depth", 0)):
        raise ValueError(f"depth_exceeded: {depth} > max_depth {genesis.get('max_depth')}")
    s = child_sum(str(parent.get("sum")), caps, budget)
    return {
        "schema": SCHEMA,
        "id": uid or ("u" + s[:12]),
        "parent": parent.get("id"),
        "seal": parent.get("seal"),
        "caps": _norm_caps(caps),
        "budget": _norm_budget(budget),
        "depth": depth,
        "sum": s,
    }


def verify_forest(seal: str, genesis: dict, universes: object) -> list[Error]:
    """核对一棵派生树（或链）。只读；空列表 = 接受。"""
    out: list[Error] = []
    if not _is_hex64(seal):
        _err(out, "LNT-GEN", "封印必须是 64 位十六进制")
    if not isinstance(genesis, dict):
        _err(out, "LNT-GEN", "genesis 必须是对象")
        return out
    if genesis.get("mode") != "same_artifact":
        _err(out, "LNT-GEN", f"genesis.mode 必须是 same_artifact，得到 {genesis.get('mode')!r}")
    for k in ("max_depth", "max_children", "max_total"):
        if not _nonneg(genesis.get(k)):
            _err(out, "LNT-GEN", f"genesis.{k} 必须是非负整数")
    if out:
        return out
    if not isinstance(universes, list):
        _err(out, "LNT-GEN", "universes 必须是数组")
        return out
    nodes: dict[str, dict] = {}
    caps: dict[str, set[str]] = {}
    budgets: dict[str, dict[str, int]] = {}
    for u in universes:
        if not isinstance(u, dict) or not isinstance(u.get("id"), str):
            _err(out, "LNT-GEN", f"宇宙必须是带 id 的对象，得到 {u!r}")
            continue
        uid = u["id"]
        if uid in nodes:
            _err(out, "LNT-GEN", f"宇宙 {uid!r} 出现了两次")
            continue
        nodes[uid] = u
        if u.get("seal") != seal:
            _err(out, "LNT-SEAL", f"{uid}: seal 是 {u.get('seal')!r}，应为 {seal!r}")
        c = _caps_of(u, out, uid)
        b = _budget_of(u, out, uid)
        if c is None or b is None:
            continue
        caps[uid], budgets[uid] = c, b
        if not _is_hex64(u.get("sum")):
            _err(out, "LNT-SUM", f"{uid}: sum 必须是 64 位十六进制")
    if out:
        return out
    roots = [uid for uid, u in nodes.items() if u.get("parent") is None]
    if len(roots) != 1:
        _err(out, "LNT-TREE", f"必须恰好一个根，得到 {len(roots)} 个")
        return out
    total = len(nodes)
    if total > genesis["max_total"]:
        _err(out, "LNT-TREE", f"总数 {total} > max_total {genesis['max_total']}")
    children: dict[str, int] = {uid: 0 for uid in nodes}
    for uid, u in nodes.items():
        parent = u.get("parent")
        depth = u.get("depth")
        if not _is_int(depth) or depth < 0:
            _err(out, "LNT-GEN", f"{uid}: depth 必须是非负整数")
            continue
        if parent is None:
            if depth != 0:
                _err(out, "LNT-DEPTH", f"{uid}: 根 depth 必须是 0")
            want = root_sum(seal, list(caps[uid]), budgets[uid])
            if u.get("sum") != want:
                _err(out, "LNT-SUM", f"{uid}: 根的 sum 与 (封印, 授权) 不一致")
            continue
        if not isinstance(parent, str) or parent not in nodes:
            _err(out, "LNT-TREE", f"{uid}: 父 {parent!r} 不存在")
            continue
        children[parent] += 1
        p = nodes[parent]
        pdepth = p.get("depth")
        if _is_int(pdepth) and depth != pdepth + 1:
            _err(out, "LNT-DEPTH", f"{uid}: depth {depth} != 父 {pdepth} + 1")
        if depth > genesis["max_depth"]:
            _err(out, "LNT-DEPTH", f"{uid}: depth {depth} > max_depth {genesis['max_depth']}")
        extra = caps[uid] - caps[parent]
        if extra:
            _err(out, "LNT-CAP", f"{uid}: 提权 {sorted(extra)}（父没有）")
        for k, v in budgets[uid].items():
            if k not in budgets[parent]:
                _err(out, "LNT-BUD", f"{uid}: 父没有资源 {k!r}")
            elif v > budgets[parent][k]:
                _err(out, "LNT-BUD", f"{uid}: {k}={v} > 父的 {budgets[parent][k]}")
        want = child_sum(str(p.get("sum")), list(caps[uid]), budgets[uid])
        if u.get("sum") != want:
            _err(out, "LNT-SUM", f"{uid}: sum 与 (父 sum, 差量) 不一致")
    for pid, n in children.items():
        if n > genesis["max_children"]:
            _err(out, "LNT-TREE", f"{pid}: 子数 {n} > max_children {genesis['max_children']}")
    # 环：每个节点沿 parent 走，走不到根就是环。
    for uid in nodes:
        seen: set[str] = set()
        cur = uid
        while cur is not None and cur in nodes:
            if cur in seen:
                _err(out, "LNT-CYCLE", f"{uid}: 派生图里有环（{cur}）")
                break
            seen.add(cur)
            cur = nodes[cur].get("parent")
    return out
