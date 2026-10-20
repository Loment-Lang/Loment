#!/usr/bin/env python3
# loment_genesis.py — 链条的"第一个可执行文件" (docs/167 §5 ②, #776)
#
# 背景: 自 0.1.4 Alpha2 起, `loment/tools/lomelf.lomt` 能把种子 `.ll` 编成一个能用的
# Loment 编译器 (判据 loment_elf_test 的 test_lomelf_selfhost_rebuilds_the_compiler)。
# 于是 `bootstrap.sh` 里 clang 只剩下"种子 -> stage1"这一步 —— 而这一步可以换成
# **一个提交进仓库的二进制**。
#
# 这个二进制就是 genesis: `loment/build/genesis/lomelf-linux-x64.elf` —— 由种子构建出来的
# `lomelf`。有了它, `sh loment/bootstrap.sh` **不再需要 clang, 也不需要任何解释器**。
#
# ## 它现在**可证伪**（#776）
#
# 从前它只能被**信任**: `--check` 比的是 genesis 与同目录的 `SHA256SUMS`，而那份 SUM 是
# `--emit` 一起写的 —— 改源忘了重生成时，哈希与新二进制一起没动，判据照绿（docs/192 §3
# 记着这个缺口）。现在 `--emit` 同时写一份 `provenance.json`，把**产出它时用的输入与
# 工具链**钉住：
#
#   * 种子的 sha256/size；
#   * 镜像源 `loment/tools/lomelf.lomt` 的 sha256/size；
#   * **`lomelf.lomt -> IR` 那一步的 IR hash** —— 这一步走的是**种子**（确定的，与 clang
#     版本无关），所以"源改了、产物没跟上"变成一条**确定**的红（docs/192 §3 点 3）；
#   * 生成它的 clang 版本行与 target；genesis 自身的 sha256/size。
#
# `--check` 做**静态**核对（不碰 clang）：记录里的每个输入都得等于盘上的文件。
# `--mirror-ir` 重算 IR 并比（需要 clang + 能跑 Linux ELF）。
# `--verify` 完整重建一遍到**临时目录**（绝不覆盖树里的 genesis）：
#   * 同一个 clang 版本 -> 要求**逐字节相同**（这是"独立汇编器，一个答案"的形状）；
#   * 不同 clang 版本 -> 逐字节比是假红机（clang 一升级全红，docs/192 §3），改比**行为**：
#     两个 genesis 各把种子汇编成 stage1，两个 stage1 都必须复现种子、且对同一探针给出
#     同一份 IR。记录里的字节则钉在**记录的那个**工具链上。
#
# 残留的诚实点（写明，不装作没有）：链条总要有**第一个**可执行文件。记录让替换
# **可检测**（改了输入没重生成 -> 红；在钉住的工具链上重建 -> 逐字节比），但仓库内的
# 哈希不能证明"这个 clang 没骗你" —— 那一层要靠发行说明里同一哈希的多处登记与签名
# （#776 列的第三条路）。这是**已知的**信任边界，不是被当成证明的东西。
#
#   python tools/loment_genesis.py --emit       # 重建 genesis + SHA256SUMS + provenance (需要 clang/WSL)
#   python tools/loment_genesis.py --check      # 静态: 哈希 + 记录里的输入 (不需要 clang)
#   python tools/loment_genesis.py --mirror-ir  # 重算 lomelf.lomt -> IR 的哈希
#   python tools/loment_genesis.py --verify     # 完整重建并比对 (需要 clang/WSL)
#   python tools/loment_genesis.py              # 同 --check

from __future__ import annotations

import os

import argparse
import hashlib
import itertools
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"
_serial = itertools.count()

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"
MIRROR = ROOT / "loment" / "tools" / "lomelf.lomt"
GEN_DIR = ROOT / "loment" / "build" / "genesis"
GEN_NAME = "lomelf-linux-x64.elf"
GEN = GEN_DIR / GEN_NAME
SUMS = GEN_DIR / "SHA256SUMS"
PROV = GEN_DIR / "provenance.json"
TARGET = "x86_64-unknown-linux-gnu"

#: 记录格式版本。改记录结构就加一 —— 旧记录会被点名拒，而不是静默按新结构读。
PROV_FORMAT = 1


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _can_run_linux_elf() -> bool:
    """本机能不能执行一个 x86_64 Linux ELF（Linux 原生 / Windows 经 WSL）。"""
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


#: 旧名字（`#776` 之前只有 WSL 这一条路，所以名字叫 `_wsl`）。
_wsl = _can_run_linux_elf


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _clang_version() -> str:
    cc = _clang()
    if not cc:
        return ""
    try:
        r = subprocess.run([cc, "--version"], capture_output=True, text=True,
                           timeout=60, shell=False)
    except Exception:  # noqa: BLE001
        return ""
    return (r.stdout.splitlines() or [""])[0].strip()


def _run_elf(elf: Path, args: list[str], stdout_file: Path | None = None,
             timeout: int = 900) -> tuple[int, str]:
    """跑一个 **Linux ELF**（Linux 原生；Windows 拷进 WSL 的 /tmp 再跑）。

    stdout 要落盘时重定向到 `stdout_file`；退出码单独 `echo -n $?` 取，不和输出混在一起。
    """
    if sys.platform.startswith("linux"):
        elf.chmod(0o755)
        r = subprocess.run([str(elf), *args], cwd=str(ROOT), capture_output=True,
                           timeout=timeout, shell=False)
        if stdout_file is not None:
            stdout_file.write_bytes(r.stdout)
        return r.returncode, r.stderr.decode("utf-8", "replace")
    tag = f"{_T}gen{next(_serial)}"
    script = (f"cp {shlex.quote(_wsl_path(elf))} {tag}.bin && chmod +x {tag}.bin && "
              f"cd {shlex.quote(_wsl_path(ROOT))} && {tag}.bin")
    script += "".join(" " + shlex.quote(a) for a in args)
    if stdout_file is not None:
        script += f" > {shlex.quote(_wsl_path(stdout_file))}"
    script += "; echo -n $?"
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script], capture_output=True,
                       timeout=timeout, shell=False)
    try:
        rc = int(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        rc = -1
    # WSL 经 /mnt 写出来的文件，Windows 侧偶有可见性延迟（判据会时对时错）——
    # 这是**等的理由**，不是"偶尔读不到就重跑"。
    if stdout_file is not None:
        for _ in range(100):
            if stdout_file.exists() and stdout_file.stat().st_size > 0:
                break
            time.sleep(0.02)
    return rc, r.stderr.decode("utf-8", "replace")


def _assemble(genesis: Path, in_path: Path, out: Path, timeout: int = 900) -> tuple[int, str]:
    """`genesis <in.ll> <out.bin>`：把一个 `.ll` 汇编成可执行文件。"""
    # 两个路径都要翻成 WSL 看得懂的形式（Windows 上是 `/mnt/d/...`）；Linux 上是原路径。
    return _run_elf(genesis, [_wsl_path(in_path), _wsl_path(out)], timeout=timeout)


def _wait_written(p: Path) -> bytes:
    """读一个**另一个环境**刚写出来的文件 —— 9p 可见性会晚一拍，等它出现。"""
    for _ in range(100):
        try:
            if p.exists() and p.stat().st_size > 0:
                return p.read_bytes()
        except OSError:
            pass
        time.sleep(0.02)
    raise AssertionError(f"等不到文件写出: {p}")


def _compile(stage1: Path, entry_rel: str, out: Path, timeout: int = 900) -> tuple[int, str]:
    """`stage1 <entry.lomt>`：编译器入口，IR 写 stdout（重定向进 `out`）。"""
    return _run_elf(stage1, [entry_rel], stdout_file=out, timeout=timeout)


@dataclass(frozen=True)
class Rebuild:
    genesis: bytes
    mirror_ir: bytes
    clang_version: str


def rebuild(work: Path) -> Rebuild:
    """从**盘上的种子与镜像源**重建一份 genesis（写进 `work`，不碰树里的任何文件）。"""
    cc = _clang()
    if not (cc and _can_run_linux_elf()):
        raise RuntimeError("重建需要 clang 与能执行 Linux ELF 的环境 (WSL)")
    stage1 = work / "stage1"
    r = subprocess.run(
        [cc, f"--target={TARGET}", "-nostdlib", "-ffreestanding", "-static",
         "-fuse-ld=lld", "-o", str(stage1), str(SEED)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-300:]
    if sys.platform.startswith("linux"):
        stage1.chmod(0o755)
    ir = work / "lomelf.ll"
    rc, err = _compile(stage1, MIRROR.relative_to(ROOT).as_posix(), ir)
    assert rc == 0, f"stage1 编镜像失败: {err[-300:]}"
    gen = work / (GEN_NAME + ".new")
    r2 = subprocess.run(
        [cc, f"--target={TARGET}", "-nostdlib", "-ffreestanding", "-static", "-fno-pie",
         "-fuse-ld=lld", "-Wl,-e,_start", str(ir), "-o", str(gen)],
        capture_output=True, text=True, shell=False)
    assert r2.returncode == 0, r2.stderr[-300:]
    return Rebuild(gen.read_bytes(), ir.read_bytes(), _clang_version())


def _provenance(rb: Rebuild) -> dict:
    return {
        "format": PROV_FORMAT,
        "genesis": {"name": GEN_NAME, "sha256": _sha256_bytes(rb.genesis),
                    "size": len(rb.genesis), "mode": "100755"},
        "inputs": {
            "seed": {"path": SEED.relative_to(ROOT).as_posix(),
                     "sha256": _sha256(SEED), "size": SEED.stat().st_size},
            "mirror": {"path": MIRROR.relative_to(ROOT).as_posix(),
                       "sha256": _sha256(MIRROR), "size": MIRROR.stat().st_size},
        },
        "mirror_ir": {"sha256": _sha256_bytes(rb.mirror_ir), "size": len(rb.mirror_ir)},
        "toolchain": {"clang": rb.clang_version, "target": TARGET},
        "chain": ["clang(seed) -> stage1",
                  "stage1(mirror) -> mirror.ll",
                  "clang(mirror.ll) -> genesis"],
    }


def _load_provenance() -> dict | None:
    if not PROV.exists():
        return None
    try:
        return json.loads(PROV.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def emit() -> int:
    cc = _clang()
    if not (cc and _can_run_linux_elf()):
        print("[ERR] --emit 需要 clang 与能执行 Linux ELF 的环境 (WSL)", file=sys.stderr)
        return 1
    if not SEED.exists():
        print(f"[ERR] 缺种子 {SEED.relative_to(ROOT)}", file=sys.stderr)
        return 1
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loment-genesis-") as tds:
        rb = rebuild(Path(tds))
    GEN.write_bytes(rb.genesis)
    if not sys.platform.startswith("win"):
        GEN.chmod(0o755)
    SUMS.write_text(f"{_sha256_bytes(rb.genesis)}  {GEN_NAME}\n", encoding="utf-8", newline="\n")
    prov = _provenance(rb)
    PROV.write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"[OK] {GEN.relative_to(ROOT)} ({GEN.stat().st_size} B)  "
          f"sha256 {_sha256_bytes(rb.genesis)[:16]}…")
    print(f"[OK] {SUMS.relative_to(ROOT)}")
    print(f"[OK] {PROV.relative_to(ROOT)}  (种子 {prov['inputs']['seed']['sha256'][:16]}…, "
          f"镜像 {prov['inputs']['mirror']['sha256'][:16]}…, "
          f"镜像 IR {prov['mirror_ir']['sha256'][:16]}…)")
    print(f"     工具链: {rb.clang_version}")
    return 0


def check() -> int:
    """静态核对：哈希、记录里的输入、启动脚本引用。**不需要 clang。**"""
    if not GEN.exists():
        print(f"[ERR] 缺 genesis {GEN.relative_to(ROOT)} (用 --emit 重建)", file=sys.stderr)
        return 1
    ok = True
    notes: list[tuple[bool, str]] = []

    def note(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        notes.append((good, text))

    got = _sha256(GEN)
    if SUMS.exists():
        want = SUMS.read_text(encoding="utf-8").split()[0]
        note(want == got, f"哈希一致 ({got[:16]}…)" if want == got else
             f"[ERR] 哈希不符: 记录 {want[:16]}… 实际 {got[:16]}…")
    else:
        note(False, "[ERR] 缺 SHA256SUMS")

    boot = (ROOT / "loment" / "bootstrap.sh").read_text(encoding="utf-8")
    marker = f"genesis={GEN.relative_to(ROOT).as_posix()}"
    note(marker in boot, "bootstrap.sh 引用了 genesis" if marker in boot else
         f"[ERR] bootstrap.sh 没有把 genesis 定义为 {GEN.relative_to(ROOT).as_posix()}")

    prov = _load_provenance()
    if prov is None:
        note(False, f"[ERR] 缺 {PROV.relative_to(ROOT)} 或它读不出来 (用 --emit 重建)")
    else:
        note(prov.get("format") == PROV_FORMAT,
             f"记录格式 {PROV_FORMAT}" if prov.get("format") == PROV_FORMAT else
             f"[ERR] 记录格式 {prov.get('format')!r} != {PROV_FORMAT}")
        g = prov.get("genesis") or {}
        note(g.get("name") == GEN_NAME, f"记录的是 {GEN_NAME}" if g.get("name") == GEN_NAME
             else f"[ERR] 记录的名字是 {g.get('name')!r}")
        note(g.get("sha256") == got, "记录的 genesis 哈希与盘上一致" if g.get("sha256") == got
             else "[ERR] 记录的 genesis 哈希与盘上不符")
        note(g.get("size") == GEN.stat().st_size,
             "记录的 genesis 大小与盘上一致" if g.get("size") == GEN.stat().st_size
             else "[ERR] 记录的 genesis 大小与盘上不符")
        for key, path in (("seed", SEED), ("mirror", MIRROR)):
            rec = (prov.get("inputs") or {}).get(key) or {}
            cur = _sha256(path)
            ok_here = rec.get("sha256") == cur and rec.get("size") == path.stat().st_size
            note(ok_here, f"输入 {key} 与记录一致 ({cur[:16]}…)" if ok_here else
                 f"[ERR] 输入 {key} 与记录不符：盘上 {cur[:16]}…/{path.stat().st_size}B，"
                 f"记录 {(rec.get('sha256') or '?')[:16]}…/{rec.get('size')}B —— "
                 f"改了源就重跑 --emit")
        ir = prov.get("mirror_ir") or {}
        note(bool(ir.get("sha256")), "镜像 IR 哈希已记录" if ir.get("sha256") else
             "[ERR] 记录里没有 镜像 IR 哈希")
        tc = prov.get("toolchain") or {}
        note(bool(tc.get("clang")), "工具链版本已记录" if tc.get("clang") else
             "[ERR] 记录里没有 clang 版本")

    for good, text in notes:
        print(f"  {'[OK]' if good else ''} {text}".rstrip())
    return 0 if ok else 1


def check_mirror_ir() -> int:
    """重算 `lomelf.lomt -> IR` 并与记录里的哈希比。

    这一步**与 clang 版本无关**（IR 由种子编出来的 stage1 发射），所以它能把
    "源改了、genesis 没跟上"变成一条确定的红 —— docs/192 §3 点 3 的那条建议。
    返回 0 一致 / 1 不一致 / 2 环境不足（调用方按 SKIP 处理）。
    """
    prov = _load_provenance()
    if prov is None:
        print("[ERR] 缺 provenance.json（先 --check 看是哪一格）", file=sys.stderr)
        return 1
    want = (prov.get("mirror_ir") or {}).get("sha256")
    if not want:
        print("[ERR] 记录里没有镜像 IR 哈希", file=sys.stderr)
        return 1
    cc = _clang()
    if not (cc and _can_run_linux_elf()):
        print("SKIP: 重算镜像 IR 需要 clang 与能执行 Linux ELF 的环境 (WSL)")
        return 2
    with tempfile.TemporaryDirectory(prefix="loment-mirror-ir-") as tds:
        work = Path(tds)
        stage1 = work / "stage1"
        r = subprocess.run(
            [cc, f"--target={TARGET}", "-nostdlib", "-ffreestanding", "-static",
             "-fuse-ld=lld", "-o", str(stage1), str(SEED)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        ir = work / "lomelf.ll"
        rc, err = _compile(stage1, MIRROR.relative_to(ROOT).as_posix(), ir)
        assert rc == 0, f"stage1 编镜像失败: {err[-300:]}"
        got = _sha256(ir)
        size = ir.stat().st_size
    if got == want:
        print(f"  镜像 IR 与记录一致 ({got[:16]}…)")
        return 0
    print(f"[ERR] 镜像 IR 与记录不符: 盘上 {got[:16]}… ({size}B), "
          f"记录 {want[:16]}… —— `lomelf.lomt` 改了而 genesis 没重生成 (docs/192 §3)")
    return 1


#: 工具链不同时用来比行为的**固定输入**（一小段带全局、调用、分支、`_start` 的 IR）。
#: 汇编一个 `.ll` 是确定性的：同一份输入、同一套语义 ⇒ 逐字节同样的产物。
PROBE_IR_TEXT = """@msg = global [4 x i8] c"abc\\00"

define i32 @add2(i32 %a, i32 %b) {
entry:
  %r = add i32 %a, %b
  ret i32 %r
}

define void @_start() {
entry:
  %x = call i32 @add2(i32 40, i32 2)
  %c = icmp eq i32 %x, 42
  br i1 %c, label %ok, label %bad
ok:
  ret void
bad:
  ret void
}
"""


def _verify_behaviour(rebuilt: Path, work: Path) -> bool:
    """工具链不同时的比对：两个 genesis 对同一份 `.ll` 必须给出**逐字节相同**的产物。

    这比"把种子汇编成 stage1"便宜得多（那个在 CI 上要几分钟），而它问的是同一件事：
    提交的那个 genesis 与从记录输入重建的那个，**行为上是不是同一个汇编器**。
    它做不到的（写明）：不能证明"这个 clang 生成的字节就是这个 genesis 的字节" ——
    那一层只在同版本工具链上由逐字节比给出。
    """
    probe = work / "probe.ll"
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(PROBE_IR_TEXT)
    produced: dict[str, bytes] = {}
    for label, gen in (("committed", GEN), ("rebuilt", rebuilt)):
        out = work / f"probe-{label}.bin"
        rc, err = _assemble(gen, probe, out)
        if rc != 0:
            print(f"[ERR] {label} genesis 汇编探针 IR 失败: {err[-300:]}")
            return False
        produced[label] = _wait_written(out)
    if produced["committed"] != produced["rebuilt"]:
        print("[ERR] 两个 genesis 对同一份探针 IR 给出不同产物 —— "
              "提交的那个不是从记录的种子/镜像重建出来的")
        return False
    return True


def verify() -> int:
    """完整核对：静态记录 + 重算镜像 IR + 重建并比对。返回 0/1/2（2 = 环境不足）。"""
    if check() != 0:
        return 1
    rc = check_mirror_ir()
    if rc == 2:
        return 2
    if rc != 0:
        return 1
    prov = _load_provenance() or {}
    if not (_clang() and _can_run_linux_elf()):
        print("SKIP: 重建需要 clang 与能执行 Linux ELF 的环境 (WSL)")
        return 2
    with tempfile.TemporaryDirectory(prefix="loment-genesis-verify-") as tds:
        work = Path(tds)
        rb = rebuild(work)
        pinned = ((prov.get("toolchain") or {}).get("clang") or "").strip()
        if rb.clang_version == pinned:
            if rb.genesis == GEN.read_bytes():
                print(f"  重建逐字节等于提交的 genesis ({len(rb.genesis)} B, "
                      f"clang {rb.clang_version.split(' ')[0]} 与记录相同)")
                return 0
            print(f"[ERR] 同一工具链下重建与提交件不同: 重建 {len(rb.genesis)}B "
                  f"{_sha256_bytes(rb.genesis)[:16]}…, 提交 {GEN.stat().st_size}B "
                  f"{_sha256(GEN)[:16]}… —— 提交的那个不是这份种子/镜像的产物")
            return 1
        print(f"  注意: 本机 clang 与记录不同（记录 {pinned[:60]!r}，本机 "
              f"{rb.clang_version[:60]!r}）—— 逐字节比是假红机（docs/192 §3），改比行为。")
        rebuilt = work / (GEN_NAME + ".new")
        if not _verify_behaviour(rebuilt, work):
            return 1
        print("  行为比对通过：两个 genesis 对同一份探针 IR 给出逐字节相同的产物")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_genesis",
                                 description="链条的第一个可执行文件 (docs/167, #776)")
    ap.add_argument("--emit", action="store_true", help="重建 genesis + SHA256SUMS + provenance")
    ap.add_argument("--check", action="store_true", help="静态核对哈希、记录与启动脚本")
    ap.add_argument("--mirror-ir", action="store_true", help="重算镜像 IR 的哈希")
    ap.add_argument("--verify", action="store_true", help="完整重建并比对")
    a = ap.parse_args(argv)
    if a.emit:
        return emit()
    if a.mirror_ir:
        return check_mirror_ir()
    if a.verify:
        return verify()
    return check()


if __name__ == "__main__":
    sys.exit(main())
