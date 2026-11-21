"""LNotary · 法域封印（`docs/358` §4.1 / `docs/361` §6 的 V1）。

旁挂的形状：`schema / potato / artifact / dims / dims_hash / laws / seal`。
`seal` 是对**除它自己以外**全部字段的规范序列化取 SHA-256；`dims_hash` 是
`dims` 的同一套哈希。两者都是内容寻址，所以同一份 Potato 派生两次逐字节相同。

这一层只记录**产物声称了什么、公证人核了什么**。它不重新证明站点的合法性。
"""

from __future__ import annotations

import hashlib
import json

from .verify import Error, LEVELS

SCHEMA = 1

#: 九个核心维。`lspe` 的取值住在 `lspe.state`，其余是顶层字段。
DIMS = ("mode", "gc", "runtime", "port", "vm", "lms", "lifetime", "fp")

#: 法域里收录的三条规律（V1）。每条只在产物**真的声称**它时出现。
LAW_NO_RUNTIME = "no_runtime"
LAW_LMS_FULL = "lms_full"
LAW_LIFETIME_AUTO = "lifetime_auto"
LAWS = (LAW_NO_RUNTIME, LAW_LMS_FULL, LAW_LIFETIME_AUTO)

#: **创世预算**（`docs/358` §3.2 / `docs/361` V5）：递归有界，三个数进封印。
#: 一份产物派生出的子宇宙总数、深度、每层子数都不许超过它。
GENESIS_DEFAULTS = {"mode": "same_artifact", "max_depth": 4,
                    "max_children": 8, "max_total": 32}


def canonical_bytes(obj: object) -> bytes:
    """规范序列化：排序键、无空白、UTF-8。哈希只认这一种写法。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dims_of(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in DIMS:
        v = doc.get(k)
        if isinstance(v, str):
            out[k] = v
    lspe = doc.get("lspe")
    if isinstance(lspe, dict) and isinstance(lspe.get("state"), str):
        out["lspe"] = lspe["state"]
    return out


def _laws_of(doc: dict) -> list[dict]:
    laws: list[dict] = []
    if doc.get("runtime") == "no_runtime":
        budgets = doc.get("lms_budgets")
        ok = (not isinstance(budgets, dict)
              or not any(budgets.get(k) == "state" for k in LEVELS))
        laws.append({"id": LAW_NO_RUNTIME, "declared": "no_runtime",
                     "checked": ok, "evidence": ["runtime", "lms_budgets"]})
    if doc.get("lms") == "lms_full":
        levels = doc.get("lms_levels")
        ok = (not isinstance(levels, dict)
              or all(levels.get(k) is True for k in LEVELS))
        laws.append({"id": LAW_LMS_FULL, "declared": "lms_full",
                     "checked": ok, "evidence": ["lms", "lms_levels"]})
    if doc.get("lifetime") == "lifetime_auto":
        laws.append({"id": LAW_LIFETIME_AUTO, "declared": "lifetime_auto",
                     "checked": True, "evidence": ["lifetime"]})
    return laws


def derive_lawspace(doc: dict, artifact_bytes: bytes,
                    sites: dict | None = None,
                    genesis: dict | None = None) -> dict:
    """从一份**已通过 V0** 的 Potato 派生旁挂。调用方负责先跑 V0。

    `sites` 是 `tools/lms_sites.py` 派生的站点表；给了就把它的封印与站点数
    绑进旁挂（`docs/363`）。`genesis` 是创世预算（`docs/361` V5），缺省用
    `GENESIS_DEFAULTS`。不修改 `doc`；输出不含时间戳、路径等不稳定字段。
    """
    if doc.get("vm") != "vm":
        raise ValueError("LNT-VM: LNotary 只在 choose vm 的产物上启用"
                         f"（这份是 {doc.get('vm')!r}）")
    dims = dims_of(doc)
    g = dict(GENESIS_DEFAULTS)
    if genesis:
        g.update(genesis)
    body = {
        "schema": SCHEMA,
        "potato": doc.get("potato"),
        "artifact": {"sha256": sha256_hex(artifact_bytes),
                     "bytes": len(artifact_bytes)},
        "dims": dims,
        "dims_hash": sha256_hex(canonical_bytes(dims)),
        "laws": _laws_of(doc),
        "genesis": g,
    }
    if sites is not None:
        body["sites"] = {
            "sha256": sites.get("seal"),
            "count": (sites.get("counts") or {}).get("sites"),
        }
    body["seal"] = sha256_hex(canonical_bytes(body))
    return body


def _err(out: list[Error], code: str, message: str) -> None:
    out.append(Error(code, message))


def verify_lawspace(doc: dict, side: object,
                    artifact_bytes: bytes | None = None,
                    sites: dict | None = None) -> list[Error]:
    """核对旁挂与产物。只读；返回错误列表（空 = 接受）。

    如果旁挂绑了站点表（`sites` 字段），调用方必须同时给出那张表；
    反过来给了表却没绑，也红 —— 两边的说法必须一致。
    """
    out: list[Error] = []
    if not isinstance(side, dict):
        _err(out, "LNT-LS", f"旁挂必须是对象，得到 {type(side).__name__}")
        return out
    if side.get("schema") != SCHEMA:
        _err(out, "LNT-LS", f"旁挂 schema 必须是 {SCHEMA}，得到 {side.get('schema')!r}")
    if side.get("potato") != doc.get("potato"):
        _err(out, "LNT-LS",
             f"旁挂的 potato 是 {side.get('potato')!r}，产物是 {doc.get('potato')!r}")
    if doc.get("vm") != "vm":
        _err(out, "LNT-VM",
             f"LNotary 只在 choose vm 的产物上启用；这份是 {doc.get('vm')!r}")

    g = side.get("genesis")
    if not isinstance(g, dict):
        _err(out, "LNT-GEN", "旁挂缺少 genesis（创世预算）")
    else:
        if g.get("mode") != "same_artifact":
            _err(out, "LNT-GEN",
                 f"genesis.mode 必须是 same_artifact，得到 {g.get('mode')!r}")
        for k in ("max_depth", "max_children", "max_total"):
            v = g.get(k)
            if not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
                _err(out, "LNT-GEN", f"genesis.{k} 必须是非负整数")
        if isinstance(g.get("max_depth"), int) and g.get("max_depth") < 1:
            _err(out, "LNT-GEN", "genesis.max_depth 至少为 1")

    art = side.get("artifact")
    if not isinstance(art, dict):
        _err(out, "LNT-ART", "旁挂缺少 artifact {sha256, bytes}")
    else:
        if artifact_bytes is not None:
            want = sha256_hex(artifact_bytes)
            if art.get("sha256") != want:
                _err(out, "LNT-ART",
                     f"artifact.sha256 是 {art.get('sha256')!r}，实际是 {want!r}")
            if art.get("bytes") != len(artifact_bytes):
                _err(out, "LNT-ART",
                     f"artifact.bytes 是 {art.get('bytes')!r}，实际是 {len(artifact_bytes)}")
        if not isinstance(art.get("sha256"), str) or not isinstance(art.get("bytes"), int):
            _err(out, "LNT-ART", "artifact 必须同时有 sha256（字符串）与 bytes（整数）")

    dims = side.get("dims")
    want_dims = dims_of(doc)
    if dims != want_dims:
        _err(out, "LNT-DIMS", f"旁挂的 dims 是 {dims!r}，产物是 {want_dims!r}")
    elif side.get("dims_hash") != sha256_hex(canonical_bytes(want_dims)):
        _err(out, "LNT-DIMS", "旁挂的 dims_hash 与 dims 不一致")

    laws = side.get("laws")
    if not isinstance(laws, list):
        _err(out, "LNT-LAW", "旁挂的 laws 必须是数组")
    else:
        seen: set[str] = set()
        for law in laws:
            if not isinstance(law, dict):
                _err(out, "LNT-LAW", f"law 必须是对象，得到 {law!r}")
                continue
            lid = law.get("id")
            if lid not in LAWS:
                _err(out, "LNT-LAW", f"不认识的规律 {lid!r}（只有 {LAWS}）")
                continue
            if lid in seen:
                _err(out, "LNT-LAW", f"规律 {lid!r} 出现了两次")
            seen.add(lid)
            want = {"no_runtime": "no_runtime", "lms_full": "lms_full",
                    "lifetime_auto": "lifetime_auto"}[lid]
            if law.get("declared") != want:
                _err(out, "LNT-LAW",
                     f"{lid} 的 declared 是 {law.get('declared')!r}，应为 {want!r}")
            if law.get("checked") is not True:
                _err(out, "LNT-LAW", f"{lid} 没有被公证人核过（checked != true）")

    want_sites = side.get("sites")
    if want_sites is not None:
        if not isinstance(want_sites, dict):
            _err(out, "LNT-LS", "旁挂的 sites 必须是 {sha256, count}")
        elif sites is None:
            _err(out, "LNT-LS", "旁挂绑了站点表，但没有给 --sites")
        else:
            if want_sites.get("sha256") != sites.get("seal"):
                _err(out, "LNT-LS",
                     f"旁挂绑的站点表封印是 {want_sites.get('sha256')!r}，"
                     f"实际是 {sites.get('seal')!r}")
            want_n = (sites.get("counts") or {}).get("sites")
            if want_sites.get("count") != want_n:
                _err(out, "LNT-LS",
                     f"旁挂绑的站点数是 {want_sites.get('count')!r}，实际是 {want_n!r}")
    elif sites is not None:
        _err(out, "LNT-LS", "给了 --sites，但旁挂没绑站点表")

    body = {k: v for k, v in side.items() if k != "seal"}
    want_seal = sha256_hex(canonical_bytes(body))
    if side.get("seal") != want_seal:
        _err(out, "LNT-SEAL",
             f"seal 是 {side.get('seal')!r}，重算是 {want_seal!r}")
    return out
