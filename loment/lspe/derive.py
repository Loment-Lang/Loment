"""派生树与证明哈希（`docs/262` §5.9）。

每个 `PROVED` 站点都要能回答"**什么保护了这次访问**"。一个布尔不是审计：
这里把站点变成一张 `Claim -> Obligation -> Evidence(kind, source, span)` 的 DAG，
按**规范化序列化**取哈希。哈希进产物（`lspe.proof_root`），整棵树进 `.lspe` 旁挂。

三条硬纪律：

* **同一棵树两次序列化成同一串字节** —— 键排序、无空白、不依赖 dict 迭代序；
* **证据节点必须带 `source` 与 `span`**（源行/列）；零证据的 `PROVED` 被拒；
* **任一证据节点变化 ⇒ 哈希变化**（否则旁挂被改过也验不出来）。

哈希用 FNV-1a 32 位：自举侧已经有同一支实现（`lspe_fnv_byte`），所以 `proof_hash`
可以进两侧逐字节比较的台账；32 位在这里够用 —— 它防的是**无意的篡改**，
不是密码学对手（README 里不声称抗碰撞）。
"""

from __future__ import annotations

import json

SIDECAR_FORMAT = "lspe-sidecar"
SIDECAR_VERSION = 1


def fnv1a32(s: str) -> int:
    """与 `lomentc._fnv1a32` 逐字节相同（这里不 import 编译器：它有循环依赖）。"""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def canonical(obj: object) -> str:
    """规范序列化：键排序、无空格、保留非 ASCII（内容是站点文本，不是传输格式）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def span_of(site: dict) -> list[int]:
    """站点的 `[line, col, end_line, end_col]`。缺列时退回行、行。"""
    span = site.get("span")
    if span and len(span) == 4:
        return [int(x) for x in span]
    ln = int(site.get("line", 0) or 0)
    return [ln, 0, ln, 0]


def evidence_node(kind: str, source: str, site: dict) -> dict:
    """一个证据节点。`source` 为空 = 没有来源，调用方不该造出这种节点。"""
    if not source:
        raise ValueError("证据节点必须有 source（其余可以是空串，来源不行）")
    return {"kind": kind, "source": source, "span": span_of(site)}


def derivation(site: dict, evidence: list[dict] | None = None) -> dict:
    """把一个站点变成派生树。

    `evidence` 是求解器给出的证据节点；不传就按站点的 `evidence`/`source` 造一个。
    `PROVED` 且零证据是**非法状态**（`#748` 验收第 3 条）—— 这里直接拒，
    因为放它过去就等于在产物里写一个没有出处的布尔。
    """
    if evidence is None:
        ev = site.get("evidence")
        src = site.get("source")
        evidence = ([evidence_node(str(ev), str(src), site)]
                    if ev not in (None, "none") and src else [])
    proved = bool(site.get("proved"))
    if proved and not evidence:
        raise ValueError(f"PROVED 站点 {site.get('id')!r} 没有任何证据节点")
    return {
        "claim": site["id"],
        "kind": site["kind"],
        "obligation": site["obligation"],
        "proved": proved,
        "reason": site.get("reason"),
        "evidence": evidence,
    }


def proof_hash(tree: dict) -> str:
    return f"{fnv1a32(canonical(tree)):08x}"


def build_trees(sites: list[dict], solver=None) -> dict[str, dict]:
    """`{site_id: tree}`。站点顺序不影响结果（逐站点算）。"""
    out: dict[str, dict] = {}
    for site in sites:
        ev = solver.evidence_nodes(site) if solver is not None else None
        tree = derivation(site, ev)
        out[site["id"]] = {"hash": proof_hash(tree), "tree": tree}
    return out


def proof_root(trees: dict[str, dict]) -> str:
    """有序站点哈希的根。站点集合少一个、多一个、改一个，根都会变。"""
    lines = [f"{sid} {trees[sid]['hash']}" for sid in sorted(trees)]
    return f"{fnv1a32(chr(10).join(lines)):08x}"


def sidecar(unit: str, level: int, trees: dict[str, dict]) -> dict:
    return {
        "format": SIDECAR_FORMAT,
        "version": SIDECAR_VERSION,
        "unit": unit,
        "level": level,
        "sites": {sid: trees[sid]["tree"] | {"hash": trees[sid]["hash"]}
                  for sid in sorted(trees)},
        "root": proof_root(trees),
    }


def verify(artifact: dict, side: dict) -> list[str]:
    """离线核对 `.lspe` 旁挂（`docs/262` §5.9 / `#749`）。**不重编译。**

    返回问题列表；空 = 通过。检查四件事：

    1. 旁挂的单元与产物是同一份；
    2. 每个站点的树重新序列化后的哈希等于旁挂里写的；
    3. 旁挂的根等于产物 `lspe.proof_root`（产物没有这个字段 ⇒ 这 artifacts 不是
       带旁挂构建出来的，拒绝）；
    4. 站点数等于产物台账的 `sites`（旁挂漏站点会被第 3 条抓到，这一条是二重保险）。
    """
    bad: list[str] = []
    if side.get("format") != SIDECAR_FORMAT:
        return [f"不是 LSPE 旁挂：format={side.get('format')!r}"]
    if int(side.get("version", 0)) != SIDECAR_VERSION:
        bad.append(f"旁挂版本不认识：{side.get('version')!r}")
    unit = artifact.get("unit") or artifact.get("module") or artifact.get("name")
    if unit and side.get("unit") != unit:
        bad.append(f"旁挂属于 {side.get('unit')!r}，产物是 {unit!r}")
    lp = artifact.get("lspe") or {}
    root = lp.get("proof_root")
    if not root:
        bad.append("产物没有 lspe.proof_root —— 它不是带旁挂构建出来的")
    sites = side.get("sites") or {}
    for sid in sorted(sites):
        ent = sites[sid]
        tree = {k: ent[k] for k in ("claim", "kind", "obligation", "proved",
                                    "reason", "evidence") if k in ent}
        h = proof_hash(tree)
        if h != ent.get("hash"):
            bad.append(f"站点 {sid} 的哈希对不上：旁挂写 {ent.get('hash')}，重算是 {h}")
    if root and proof_root({sid: {"hash": sites[sid].get("hash", "")}
                            for sid in sites}) != root:
        bad.append("旁挂的根与产物的 lspe.proof_root 对不上（少站点或被改过）")
    if "sites" in lp and int(lp.get("sites", -1)) != len(sites):
        bad.append(f"旁挂有 {len(sites)} 个站点，台账说 {lp.get('sites')} 个")
    return bad


def why_lines(tree: dict, indent: str = "") -> list[str]:
    """`loment lspe why` 的打印体：claim、义务、证据、源跨度。"""
    out = [f"{indent}claim        {tree['claim']}",
           f"{indent}obligation   {tree['obligation']} ({tree['kind']})",
           f"{indent}verdict      {'PROVED' if tree['proved'] else 'UNDECIDED'}"
           + (f" ({tree['reason']})" if tree.get("reason") else "")]
    if not tree.get("evidence"):
        out.append(f"{indent}evidence     (none)")
    for node in tree.get("evidence", []):
        ln, col, eln, ecol = node["span"]
        out.append(f"{indent}evidence     {node['kind']} <- {node['source']} "
                   f"@ {ln}:{col}-{eln}:{ecol}")
    return out
