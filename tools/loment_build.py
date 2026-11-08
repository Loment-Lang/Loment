#!/usr/bin/env python3
# loment_build.py — 增量构建 + 构建缓存 (M65/M66, docs/148)
#
# 判据:
#   M65 增量编译: 改动单文件只重编该文件及其下游, 其余命中缓存;
#   M66 构建缓存: 冷/热构建时间对照 (内容哈希命中率)。
# 缓存键 = sha256(编译器身份 + 源文件 + 递归 use 的 .lomt 内容); 键不变则跳过生成。
# **编译器身份**（`#900`）：参考实现那几份源的字节。只哈希源文件时，工具链升级后同一份
# 源仍会命中**上一个编译器**的产物 —— 缓存在这里必须失效。
# **产物完整性**（`#901`）：命中的前提不只是"路径存在"，还要**内容哈希与记录一致**——
# 否则任何能往 `build/` 写字的东西（提交进来的构建目录、另一个工具）都能占据那个名字，
# 而每次构建都报 `[HOT]`。
#
# 用法:
#   python tools/loment_build.py DIR [--out OUT] [--report] [--clean]
# 退出码: 0 = 全部成功 / 1 = 有文件编译失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
#: 两种写法都要认: `use "path.lomt"` 与 `use 名字` (名字形式的解析规则**只有一份**,
#: 就是 lomentc.resolve_name —— 这里绝不自己再抄一遍, 抄了就会与编译器的规则漂移)。
USE_RE = re.compile(r'use\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))')


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p.resolve())


def dep_files(p: Path, root: Path, seen: set[Path]) -> list[Path]:
    """递归收集 L1 依赖 (只跟 .lomt, 不跟 .lom 布局)。两种 `use` 写法都认。"""
    p = p.resolve()
    if p in seen or not p.exists():
        return []
    seen.add(p)
    out = [p]
    for m in USE_RE.finditer(p.read_text(encoding="utf-8")):
        rel, name = m.group(1), m.group(2)
        if name is not None:
            # 名字形式: 规则只有 lomentc 那一份, 这里只调用
            cand = lomentc.resolve_name(name, root)
        else:
            if not rel.endswith(".lomt"):
                continue
            cand = next((c for c in ((root / rel), (p.parent / rel)) if c.exists()), None)
            if cand is None:
                continue
        out += dep_files(cand, root, seen)
    return out


#: 编译器身份由**这几份源**决定（`#900`）。参考实现的发射面就在这三个文件里；
#: `loment/lspe/` 是检查阶段会读的语义面，跟着算进去。清单变短（少算一份）就是
#: "工具链换了而缓存仍然命中" —— 那正是这条要堵的洞。
COMPILER_SOURCES = ("tools/lomentc.py", "tools/lomc.py", "tools/potato_from.py")
COMPILER_DIRS = ("loment/lspe",)


def compiler_id(root: Path | None = None) -> str:
    """工具链身份：编译器那几份源的 sha256（路径名 + 字节，次序固定）。"""
    root = root or ROOT
    h = hashlib.sha256()
    paths: list[Path] = [root / rel for rel in COMPILER_SOURCES]
    for rel in COMPILER_DIRS:
        paths += sorted((root / rel).rglob("*.py"))
    for p in paths:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cache_key(files: list[Path], toolchain: str = "") -> str:
    h = hashlib.sha256()
    h.update(b"loment-build-2\0")
    h.update(toolchain.encode())
    h.update(b"\0")
    for p in sorted(files):
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _hit(prev: dict, key: str, toolchain: str, out: Path) -> bool:
    """缓存命中 = 键相同 **且** 每份产物还在、内容没被换过（`#901`）。"""
    if not prev or prev.get("key") != key or prev.get("toolchain") != toolchain:
        return False
    arts = prev.get("artifacts") or []
    hashes = prev.get("hashes") or {}
    if not arts or set(hashes) != set(arts):
        return False
    for name in arts:
        p = out / name
        if not p.is_file() or _sha256_file(p) != hashes[name]:
            return False
    return True


def _git_tracked(out: Path) -> list[str]:
    """`out` 里被 git 跟踪的文件。非空 = 这是个**提交进仓库**的构建目录（`#901`）。"""
    try:
        r = subprocess.run(["git", "-C", str(out), "ls-files", "--", "."],
                           capture_output=True, text=True, shell=False)
    except OSError:
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def build_one(p: Path, out: Path, root: Path, toolchain: str = "") -> dict:
    key = cache_key(dep_files(p, root, set()), toolchain)
    rec = {"file": _rel(p), "key": key, "toolchain": toolchain,
           "artifacts": [], "hashes": {}, "ok": True, "error": ""}
    try:
        mod, deps = lomentc.load_unit(p, root)      # 唯一入口（docs/182 §1.10）
        errs = lomentc.check(mod, deps=deps)
        if errs:
            rec["ok"] = False
            rec["error"] = errs[0]
            return rec
        rust = lomentc.emit_rust(mod, root, deps)
        potato = lomentc.emit_potato(mod, root, deps)
        # **`newline="\n"` 不能省**: 默认会按平台转换, 于是在 Windows 上写出的产物是 CRLF,
        # 而 `.gitattributes` 对 `*.rs`/`*.json`/`*.ll` 定的是 `eol=lf` —— 检出回来是 LF。
        # 两者一比就"不一致", 于是**"重生成的产物 == 仓库里的"这条判据在 Windows 上必然假红**。
        # 同族的坑 `.gitattributes` 自己记过一条 ("IR 逐字节比较会假红")。
        # 2026-09-17 归因: 重生成 potato 产物时发现 28 个 `.rs` 全"变了", 逐行看却一模一样。
        (out / f"{p.stem}.rs").write_text(rust, encoding="utf-8", newline="\n")
        (out / f"{p.stem}.potato.json").write_text(potato, encoding="utf-8", newline="\n")
        rec["artifacts"] = [f"{p.stem}.rs", f"{p.stem}.potato.json"]
        try:
            ll = lomentc.emit_llvm(mod, root, deps)
            (out / f"{p.stem}.ll").write_text(ll, encoding="utf-8", newline="\n")
            rec["artifacts"].append(f"{p.stem}.ll")
        except lomentc.LomError as e:  # 原生后端不支持的部分: 记录但不失败
            rec["error"] = f"IR: {e}"
        rec["hashes"] = {name: _sha256_file(out / name) for name in rec["artifacts"]}
    except Exception as e:  # noqa: BLE001
        rec["ok"] = False
        rec["error"] = str(e)
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_build", description="增量构建 + 缓存")
    ap.add_argument("dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    src_dir = Path(a.dir).resolve()
    out = Path(a.out).resolve() if a.out else src_dir / "build"
    if not src_dir.is_dir():
        print(f"[ERR] 目录不存在: {src_dir}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    tracked = _git_tracked(out)
    if tracked:
        # `#901`: 提交进仓库的构建目录里，索引和产物**可以一起**被伪造 —— 那份缓存
        # 不是"上次构建留下的"，而是别人写好的。拒绝，而不是"小心地命中"。
        print(f"[ERR] 构建目录被 git 跟踪（{len(tracked)} 个文件）: {out}\n"
              f"      提交进仓库的 build/ 不能当缓存用 —— 换 --out，或先把它移出版本控制。",
              file=sys.stderr)
        if tracked[:3]:
            print("      " + "\n      ".join(tracked[:3]), file=sys.stderr)
        return 2
    toolchain = compiler_id(ROOT)
    index_path = out / ".loment-cache.json"
    old: dict[str, dict] = {}
    if index_path.exists() and not a.clean:
        try:
            old = {r["file"]: r for r in json.loads(
                index_path.read_text(encoding="utf-8")).get("records", [])}
        except (OSError, json.JSONDecodeError):
            old = {}

    files = sorted(p for p in src_dir.glob("*.lomt"))
    t0 = time.perf_counter()
    records, hit, miss, bad = [], 0, 0, 0
    for p in files:
        rel = _rel(p)
        prev = old.get(rel)
        key = cache_key(dep_files(p, ROOT, set()), toolchain)
        if _hit(prev, key, toolchain, out):
            hit += 1
            records.append(prev)
            continue
        miss += 1
        rec = build_one(p, out, ROOT, toolchain)
        rec["file"] = rel
        records.append(rec)
        if not rec["ok"]:
            bad += 1
    dt = time.perf_counter() - t0
    index_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8", newline="\n")
    print(f"[{'COLD' if hit == 0 else 'HOT '}] {len(files)} 个单元: "
          f"{hit} 命中 / {miss} 重编 / {bad} 失败 — {dt * 1000:.1f} ms")
    if a.report:
        print("| 文件 | 缓存 | 产物 | 备注 |")
        print("|---|---|---|---|")
        for r in records:
            state = "命中" if r in records[:hit] and r.get("key") in {x.get("key") for x in records[:hit]} else "重编"
            print(f"| `{r['file']}` | {state} | {', '.join(r['artifacts'])} | "
                  f"{r.get('error', '')} |")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
