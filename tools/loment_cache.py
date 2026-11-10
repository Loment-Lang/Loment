#!/usr/bin/env python3
"""loment_cache.py — 编译缓存（IR 层，按内容哈希寻址）。

**为什么按内容哈希而不是 mtime**：这条工具链已经有两处同口径的地方 —— lompi 的包身份
是内容哈希，`build_lomenterr` 的新鲜度戳也是。mtime 在 Windows 上粒度粗，"改完立刻跑"
会拿到旧产物，而那正是最需要对的时刻。

**键里放什么**（少一样就是静默错编）：

  * 根单元 + **全部**依赖的源文件字节（按规范化路径排序）；
  * 生效的核心维（`effective_dims`）、版次、LSPE 档与回答；
  * 发射开关（debug / coverage / lspe 相关）；
  * **编译器自身**的摘要（`lomentc.py` 与它依赖的发射器模块）。

**键里不放什么**：绝对路径本身不单列 —— 内容相同就该命中，换目录不该失效；但路径会
以"规范化后的相对/绝对字符串"参与排序与拼接，避免两个同名不同内容的文件撞键。

**边界**：只缓存**文件都在盘上**的单元。解析自字符串（`translated`）或没有 `src` 的
单元一律不缓存 —— 那种调用的输入不在键的控制范围内。

用法：
    python tools/loment_cache.py --stats
    python tools/loment_cache.py --clear
    python tools/loment_cache.py --verify     # 重算一条样本并比对缓存
环境变量：
    LOMENT_CACHE_DIR   缓存目录（默认见 `cache_root`）
    LOMENT_CACHE=off   整个进程不读不写缓存
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 参与"编译器摘要"的模块。只列**真的影响 IR 字节**的那几个 —— 列多了会让无关改动
#: 把整个缓存作废，列少了会让编译器改动命中旧产物（后者更严重，所以宁可多一点）。
_TOOL_FILES = (
    "tools/lomentc.py",
    "tools/lomc.py",
    "tools/lspe.py",
    "tools/potato_from.py",
    "tools/lomt_from.py",
    "tools/loment_comefor.py",
)


def enabled() -> bool:
    return os.environ.get("LOMENT_CACHE", "").strip().lower() not in ("off", "0", "no")


def cache_root() -> Path:
    """缓存目录。`LOMENT_CACHE_DIR` 优先；否则按平台选一个用户级目录。"""
    env = os.environ.get("LOMENT_CACHE_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
        return Path(base) / "loment" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "loment"
    return Path.home() / ".cache" / "loment"


def tool_digest() -> str:
    h = hashlib.sha256()
    for rel in _TOOL_FILES:
        p = ROOT / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def _unit_digests(units) -> list[tuple[str, bytes]] | None:
    """所有单元的 `(路径, 实际参与编译的 token 流摘要)`；没有 src 就返回 None。

    **不能只哈希盘上的文件字节**：前门会把别的表层语法翻成 Loment（`choose write
    grammar …`）、`comefor` 会在 token 层展开、发行版注入会把 `LOMENT_CHANNEL` 那一行
    换掉 —— 这三件事都**不改文件**，却改编译输入。`loment_channel_test` 就是这么抓到
    第一版的：dev 与 release 两个种子命中了同一条缓存，输出一模一样。
    """
    out: list[tuple[str, bytes]] = []
    for m in units:
        src = getattr(m, "src", None)
        if src is None:
            return None
        try:
            key = str(Path(src).resolve())
        except OSError:
            return None
        h = hashlib.sha256()
        toks = getattr(m, "toks", None)
        if toks:
            for t in toks:
                h.update(t.kind.encode("utf-8"))
                h.update(b"\x00")
                h.update(str(t.val).encode("utf-8"))
                h.update(b"\x00")
        else:
            try:
                h.update(Path(src).read_bytes())
            except OSError:
                return None
        out.append((key, h.digest()))
    out.sort(key=lambda kv: kv[0])
    return out


def key_for(mod, deps, *, flags: dict | None = None) -> str | None:
    """这一份单元 + 这一组开关的缓存键；不可缓存时返回 None。"""
    units = [mod, *(deps or [])]
    srcs = _unit_digests(units)
    if srcs is None:
        return None
    import lomentc  # 延迟导入：本模块会在 lomentc 内部被调用

    dims = lomentc.effective_dims(mod)
    h = hashlib.sha256()
    h.update(b"loment-ir-cache-v1\0")
    h.update(tool_digest().encode("ascii") + b"\0")
    for dim in lomentc.CORE_DIM_ORDER:
        h.update(f"{dim}={dims.get(dim, '')}".encode("utf-8") + b"\0")
    h.update(f"profile={getattr(mod, 'profile', '')}".encode("utf-8") + b"\0")
    for k, v in sorted((flags or {}).items()):
        h.update(f"{k}={v}".encode("utf-8") + b"\0")
    for path, data in srcs:
        h.update(path.encode("utf-8") + b"\0")
        h.update(str(len(data)).encode("ascii") + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def _path(key: str) -> Path:
    return cache_root() / "ir" / key[:2] / f"{key}.ll"


def get(key: str) -> str | None:
    if not enabled():
        return None
    p = _path(key)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    # 空文件是**写了一半**的形状（掉电/并发截断）。当成未命中并删掉 —— 把空串当 IR
    # 返回，编译会"成功"地发出一个空产物，那是缓存能犯的最坏的错。
    if not text:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return text


def put(key: str, text: str) -> None:
    if not enabled():
        return
    p = _path(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        # 缓存坏了不该让编译失败 —— 删掉半截，下次重算。
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def stats() -> dict[str, object]:
    root = cache_root()
    files = list((root / "ir").rglob("*.ll")) if (root / "ir").is_dir() else []
    total = sum(p.stat().st_size for p in files) if files else 0
    return {"root": str(root), "entries": len(files), "bytes": total}


def clear() -> int:
    root = cache_root() / "ir"
    n = len(list(root.rglob("*.ll"))) if root.is_dir() else 0
    if root.is_dir():
        shutil.rmtree(root)
    return n


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--clear" in argv:
        n = clear()
        print(f"清掉 {n} 条 IR 缓存（{cache_root()}）")
        return 0
    s = stats()
    print(f"缓存目录: {s['root']}")
    print(f"条目: {s['entries']}  占用: {s['bytes']} 字节")
    print(f"状态: {'开' if enabled() else '关（LOMENT_CACHE=off）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
