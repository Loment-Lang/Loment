"""站点级增量缓存（`docs/262` §5.13 / `#752`）。

键是 `(site_id, obligation_kind, unit_hash, dims, lspe_level)` —— **内容寻址**，
不是行号。三条纪律：

* 命中重建的 `proof_hash` 必须相同；不同就是**硬错误**，不是静默 miss；
* L2 的 `UNDECIDED(depth)` 不能在 L3 复用（级别变了，结论不能继承）；
* 缓存目录**不在任何 GLOB 目录里**（`#752` 验收最后一条）：`loment/**`、`tools/**`、
  `docs/**`、`editors/**` 是发布清单扫的面，缓存落进去会让"树干净"变成假话。

缓存只影响**重算成本**，不影响台账：冷/热构建写出的 `lspe` 字段必须逐字节相同。
"""

from __future__ import annotations

import json
from pathlib import Path

CACHE_FORMAT = "lspe-cache"
CACHE_VERSION = 1
DEFAULT_DIR = ".lspe-cache"

#: 发布清单/判据会扫的目录 —— 缓存绝不能落在里面。
GLOB_ROOTS = ("loment", "tools", "docs", "editors", "scripts", "standard", "book")


def _safe_dir(root: Path, path: Path | None) -> Path:
    root = Path(root).resolve()
    out = Path(path).resolve() if path else (root / DEFAULT_DIR)
    for g in GLOB_ROOTS:
        d = (root / g).resolve()
        if out == d or d in out.parents:
            raise ValueError(f"LSPE 缓存目录不能落在 GLOB 目录 {g}/ 里：{out}")
    return out


def site_key(site_id: str, obligation: str, unit_hash: str,
             dims: dict, level: int) -> str:
    dims_s = ",".join(f"{k}={dims[k]}" for k in sorted(dims))
    return f"{site_id}|{obligation}|{unit_hash}|{dims_s}|{level}"


class Cache:
    def __init__(self, root, unit: str, unit_hash: str, dims: dict,
                 level: int, directory=None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.unit = unit
        self.unit_hash = unit_hash
        self.dims = dict(dims or {})
        self.level = int(level)
        self.path: Path | None = None
        self.data: dict = {}
        if not enabled:
            return
        self.path = _safe_dir(Path(root), directory) / f"{unit}-{unit_hash}.json"
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if raw.get("format") == CACHE_FORMAT and raw.get("version") == CACHE_VERSION:
                self.data = raw.get("entries") or {}

    def key(self, site: dict) -> str:
        return site_key(str(site["id"]), str(site["obligation"]),
                        self.unit_hash, self.dims, self.level)

    def get(self, site: dict) -> dict | None:
        if not self.enabled:
            return None
        ent = self.data.get(self.key(site))
        if not ent:
            return None
        # 低一级的"深度到顶"不能在高一级复用（`#752` 验收第 3 条）。
        if ent.get("reason") == "depth" and int(ent.get("level", 0)) < self.level:
            return None
        return ent

    def put(self, site: dict, result: dict) -> None:
        if not self.enabled:
            return
        ent = dict(result)
        ent["level"] = self.level
        self.data[self.key(site)] = ent

    def check(self, site: dict, proof_hash: str) -> None:
        """命中后核对哈希：**不同就是硬错误**（`#752` 验收第 2 条）。"""
        ent = self.get(site)
        if ent and ent.get("hash") and ent["hash"] != proof_hash:
            raise ValueError(
                f"LSPE 缓存哈希不一致：{site['id']} 缓存 {ent['hash']}，"
                f"重算 {proof_hash} —— 缓存不能改变产物")
        if ent is not None:
            ent["hash"] = proof_hash

    def save(self) -> None:
        if not self.enabled or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": CACHE_FORMAT, "version": CACHE_VERSION,
                   "unit": self.unit, "unit_hash": self.unit_hash,
                   "entries": self.data}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                        indent=0) + "\n",
                             encoding="utf-8", newline="\n")
