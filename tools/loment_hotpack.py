#!/usr/bin/env python3
# loment_hotpack.py — 把**投影树**打成可落盘的"工具链代际"载荷。
#
# 热更新原先只换跑着的 LSP 映像（`loment_hotart`）。链接器、编译器、CLI 这些是
# `bin/` 里的独立程序，不在那个载荷里 —— 所以构建期修复（例如 #1828/#1830）没法靠
# 热更新送达。这个文件补上那一段：
#
#   基线树 + fix 投影（`loment_projection`）
#     --(发行渠道种子, 全部 TOOLS)-->  ELF/PE 两套工具
#     --(打包 + Ed25519)-->            payload-<platform>.hot + .sig
#
# ## 载荷格式（`HOTG`，version 1）
#
#   0   4B  magic "HOTG"
#   4   4B  version = 1
#   8   4B  count
#   12  4B  reserved
#   16  count × 128B  index:
#          name[48]（ASCII，NUL 补齐；`manifest.txt` / `bin/<tool>`）
#          sha512[64]
#          off u32（相对数据区）
#          len u32
#          mode u32
#          reserved u32
#   data_start = align8(16 + count*128)
#
# 签名是**整份载荷字节**的裸 Ed25519 签名（`.sig`，64 字节），不是 OpenPGP ——
# 工具链标准库里有 `ed25519.lomt`，装好的更新器可以自己验，不需要 gpg/openssl/Python。
#
# ## 只带修复
#
# 载荷从**投影树**编，不是从 R 编；接口面（契约/语言版本/能力/surface/CLI）由
# `loment_projection` 第 3 关钉住。清单里带每条 fix 的 sha，更新器要求至少一条。
#
# ## 固定核
#
# 更新器本身**不进载荷**（它是信任根）。`fixed_core` = 钉住的发布公钥
# `loment/hotswap/release-key.ed25519` 的 sha512；候选清单必须与基线里的值相同。

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

NAME_CAP = 48
ENTRY = 128
DATA_ALIGN = 8
MAGIC = b"HOTG"
#: 与更新器的 `PAYLOAD_CAP` 同一个数 —— 超了就在打包时红，别发一份装不进去的载荷。
PAYLOAD_MAX = 32 * 1024 * 1024
VERSION = 1
KEY_REL = "loment/hotswap/release-key.ed25519"


def sha512_file(p: Path) -> str:
    h = hashlib.sha512()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fixed_core(tree: Path) -> str:
    key = tree / KEY_REL
    if not key.is_file():
        raise SystemExit(f"{tree} 里没有 {KEY_REL} —— 固定核没有锚")
    return sha512_file(key)


def _digest_map(tree: Path) -> dict[str, str]:
    import loment_hotfix as hf
    return hf.contract_digests(tree)


def baseline_text(tree: Path, key_tree: Path | None = None) -> str:
    """装好的树里那份 `baseline.txt`（准入的对照面，不含候选自己的字段）。

    `contract`/`capability`/语言版本来自**跑着的那条基线**（`tree`）；固定公钥来自
    `key_tree`（随包发的那一把）—— 热更新的基线树是旧 tag，没有新公钥。
    """
    import loment_hotfix as hf
    kt = key_tree if key_tree is not None else tree
    rel = hf.read_release(tree)
    lines = [f"language {rel}", f"baseline {rel}"]
    for name, digest in sorted(hf.contract_digests(tree).items()):
        lines.append(f"contract {name} {digest}")
    for cap in hf.capability_manifest(tree):
        lines.append(f"capability {cap}")
    lines.append(f"fixed_core {fixed_core(kt)}")
    return "\n".join(lines) + "\n"


def _load_dist(tree: Path):
    """把**这棵树自己的** `tools/loment_dist.py` 装进来（ROOT 跟着它自己走）。"""
    tools = str(tree / "tools")
    for mod in ("loment_dist", "loment_release", "loment_seed"):
        sys.modules.pop(mod, None)
    if tools in sys.path:
        sys.path.remove(tools)
    sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location(
        "loment_dist_for_hot", tree / "tools" / "loment_dist.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def build_binaries(new_tree: Path, only: set[str] | None, work: Path) -> dict[str, tuple[bytes, bytes]]:
    """用投影树自己的 TOOLS/发行渠道种子编全部工具（ELF 与 PE 各一份）。"""
    dist = _load_dist(new_tree)
    work.mkdir(parents=True, exist_ok=True)
    dist.OUT = work / "out"
    dist.STAGE = work / "stage"
    dist.OUT.mkdir(parents=True, exist_ok=True)
    dist.STAGE.mkdir(parents=True, exist_ok=True)
    seed = work / "seed.release.ll"
    seed.write_text(dist.release_seed_text(), encoding="utf-8", newline="\n")
    dist.ACTIVE_SEED = seed
    return dist.build_tools(only)


def baseline_of(tree: Path, key_tree: Path | None = None) -> str:
    return baseline_text(tree, key_tree)


def manifest_text(base_tree: Path, release: str, platform: str, projection: dict,
                  files: list[tuple[str, str, int]], *,
                  key_tree: Path | None = None) -> str:
    kt = key_tree if key_tree is not None else ROOT
    base = baseline_of(base_tree, kt)
    base_digest = hashlib.sha512(base.encode("utf-8")).hexdigest()
    lines = [
        "schema 1",
        f"platform {platform}",
        f"release {release}",
        f"base_digest {base_digest}",
        f"fixed_core {fixed_core(kt)}",
    ]
    for name, digest, mode in files:
        lines.append(f"file {name} {digest} {mode:o}")
    for step in (projection.get("line") or []):
        if step.get("kind") != "fix":
            raise SystemExit(f"投影里有非 fix 的提交：{step}")
        lines.append(f"fix {step['sha']}")
    if not (projection.get("line") or []):
        raise SystemExit("投影是空的 —— 热更新载荷必须带修复")
    return "\n".join(lines) + "\n"


def pack_payload(files: list[tuple[str, bytes, int]], manifest: bytes) -> bytes:
    entries: list[tuple[bytes, bytes, int]] = [(b"manifest.txt", manifest, 0o644)]
    entries += [(name.encode("ascii"), data, mode) for name, data, mode in files]
    entries.sort(key=lambda e: e[0])
    count = len(entries)
    data_start = 16 + count * ENTRY
    data_start = (data_start + DATA_ALIGN - 1) // DATA_ALIGN * DATA_ALIGN
    out = bytearray(struct.pack("<4sIII", MAGIC, VERSION, count, 0))
    off = 0
    blobs = bytearray()
    for name, data, mode in entries:
        if len(name) >= NAME_CAP:
            raise SystemExit(f"载荷文件名太长（>{NAME_CAP - 1}）：{name!r}")
        idx = bytearray(ENTRY)
        idx[:len(name)] = name
        idx[NAME_CAP:NAME_CAP + 64] = hashlib.sha512(data).digest()
        struct.pack_into("<III", idx, NAME_CAP + 64, off, len(data), mode)
        out += idx
        blobs += data
        off += len(data)
    out += b"\0" * (data_start - len(out))
    out += blobs
    return bytes(out)


def sign(payload: bytes, key_pem: Path, sig_out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="loment-hotpack-") as tds:
        td = Path(tds)
        p = td / "payload.bin"
        p.write_bytes(payload)
        subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key_pem),
             "-in", str(p), "-out", str(sig_out)],
            check=True, capture_output=True,
        )


def _key_from_env() -> Path | None:
    raw = os.environ.get("LOMENT_HOTFIX_ED25519_KEY", "").strip()
    if not raw:
        return None
    d = Path(tempfile.mkdtemp(prefix="loment-hotfix-key-"))
    pem = d / "key.pem"
    try:
        pem.write_bytes(base64.b64decode(raw))
    except Exception:
        pem.write_text(raw, encoding="utf-8")
    return pem


def package(base_tree: Path, new_tree: Path, release: str, out: Path, *,
            platforms: tuple[str, ...], key_pem: Path | None,
            projection: dict, only: set[str] | None = None,
            key_tree: Path | None = None) -> list[Path]:
    if not (projection.get("line") or []):
        raise SystemExit("投影是空的 —— 不产生载荷")
    with tempfile.TemporaryDirectory(prefix="loment-hotpack-build-") as tds:
        bins = build_binaries(new_tree, only, Path(tds))
    out.mkdir(parents=True, exist_ok=True)
    kt = key_tree if key_tree is not None else ROOT
    # 自举材料：首支热更新把它放进一个还没有更新器的旧安装
    (out / "baseline.txt").write_text(baseline_text(base_tree, kt),
                                      encoding="utf-8", newline="\n")
    shutil.copyfile(kt / KEY_REL, out / "release-key.ed25519")
    made: list[Path] = []
    for platform in platforms:
        win = platform == "windows-x64"
        files: list[tuple[str, bytes, int]] = []
        for name, (elf, pe) in bins.items():
            if name == "loment-hotupdate":
                continue                    # 更新器是信任根，不进载荷
            rel = f"bin/{name}{'.exe' if win else ''}"
            data = pe if win else elf
            files.append((rel, data, 0o755))
        manifest = manifest_text(base_tree, release, platform, projection,
                                 [(n, hashlib.sha512(d).hexdigest(), m) for n, d, m in files],
                                 key_tree=key_tree)
        payload = pack_payload(files, manifest.encode("utf-8"))
        if len(payload) > PAYLOAD_MAX:
            raise SystemExit(f"payload-{platform}.hot 超过 {PAYLOAD_MAX} 字节 —— 更新器装不下")
        name = f"payload-{platform}.hot"
        (out / name).write_bytes(payload)
        (out / "manifest.txt").write_text(manifest, encoding="utf-8", newline="\n")
        if key_pem is not None:
            sign(payload, key_pem, out / f"{name}.sig")
        made.append(out / name)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_hotpack")
    ap.add_argument("--old", required=True, help="基线树（跑着的那一份的来源）")
    ap.add_argument("--new", required=True, help="投影树")
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--projection", required=True, help="投影 JSON")
    ap.add_argument("--platform", default="both",
                    choices=("linux-x64", "windows-x64", "both"))
    ap.add_argument("--key", default=None, help="Ed25519 私钥 PEM；省略时读环境变量")
    ap.add_argument("--only", default="", help="只编这些工具（逗号分隔，测试用）")
    ap.add_argument("--key-tree", default=None,
                    help="固定公钥所在树（默认是本工具所在的树）")
    args = ap.parse_args(argv)
    platforms = (("linux-x64", "windows-x64") if args.platform == "both"
                 else (args.platform,))
    key = Path(args.key) if args.key else _key_from_env()
    proj = json.loads(Path(args.projection).read_text(encoding="utf-8"))
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    made = package(Path(args.old).resolve(), Path(args.new).resolve(), args.release,
                   Path(args.out).resolve(), platforms=platforms, key_pem=key,
                   projection=proj, only=only,
                   key_tree=Path(args.key_tree).resolve() if args.key_tree else None)
    for p in made:
        print(f"[OK] {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
