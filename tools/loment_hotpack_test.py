#!/usr/bin/env python3
# loment_hotpack_test.py — 工具链代际载荷的判据。
#
# 每一支都问同一个问题：**这份载荷能不能把链接器/编译器换掉，而且只换修复？**
#
# 夹具是真的：更新器由**自举链**（`loment_dist.build_tools`）编出来，载荷是真的
# `HOTG` 容器，签名是 openssl 的裸 Ed25519，安装目录是真的 `gen/a|b`。最后一支把
# **当前树的链接器**装进代际，再用它编一段能力域程序跑起来 —— 热更新修的是真程序。
#
# 平台：按宿主编 ELF 或 PE，在本机原生跑（Linux 原生 / Windows 原生）。

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_dist  # noqa: E402
import loment_hotpack as hp  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _host() -> str:
    return "windows-x64" if sys.platform == "win32" else "linux-x64"


def _exe_name(name: str) -> str:
    return name + (".exe" if sys.platform == "win32" else "")


_KEYDIR: Path | None = None


def _keydir() -> tuple[Path, bytes, Path]:
    """一次性 Ed25519 密钥：返回 (私钥 PEM, 公钥 32 字节, 公钥 PEM)。"""
    global _KEYDIR
    if _KEYDIR is not None:
        return _KEYDIR  # type: ignore[return-value]
    d = Path(tempfile.mkdtemp(prefix="loment-hotpack-key-"))
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519",
                    "-out", str(d / "key.pem")], check=True, capture_output=True)
    der = subprocess.run(["openssl", "pkey", "-in", str(d / "key.pem"),
                          "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    pub = der[-32:]
    subprocess.run(["openssl", "pkey", "-in", str(d / "key.pem"), "-pubout",
                    "-out", str(d / "pub.pem")], check=True, capture_output=True)
    _KEYDIR = (d / "key.pem", pub, d / "pub.pem")
    return _KEYDIR


def _baseline_for(pub: bytes) -> str:
    """基线文本，但 `fixed_core` 指向这把测试公钥。"""
    lines = [ln for ln in hp.baseline_text(ROOT).splitlines()
             if not ln.startswith("fixed_core ")]
    lines.append("fixed_core " + hashlib.sha512(pub).hexdigest())
    return "\n".join(lines) + "\n"


_BUILT: dict[str, tuple[bytes, bytes]] = {}
_STAGE: Path | None = None


def _built(tool: str) -> tuple[bytes, bytes]:
    """用自举链编一个工具，返回 (ELF, PE)；按进程缓存（一次构建 ~20s）。

    构建目录用**本进程的临时目录**：`loment_dist_test` 也用 `loment/build/dist`，
    两个判据同时跑会抢同一个 `stage1.exe`（2026-11-21 实测 PermissionError）。
    """
    global _STAGE
    if _STAGE is None:
        _STAGE = Path(tempfile.mkdtemp(prefix="loment-hotpack-dist-"))
        loment_dist.STAGE = _STAGE / "stage"
        loment_dist.OUT = _STAGE / "out"
        loment_dist.STAGE.mkdir(parents=True, exist_ok=True)
        loment_dist.OUT.mkdir(parents=True, exist_ok=True)
    if tool not in _BUILT:
        _BUILT[tool] = loment_dist.build_tools({tool})[tool]
    return _BUILT[tool]


def _setup_prefix(root: Path, pub: bytes) -> Path:
    prefix = root / "prefix"
    for d in ("bin",
              "share/loment/hot/gen/a/bin",
              "share/loment/hot/gen/b/bin"):
        (prefix / d).mkdir(parents=True, exist_ok=True)
    (prefix / "share/loment/hot/release-key.ed25519").write_bytes(pub)
    (prefix / "share/loment/hot/baseline.txt").write_text(
        _baseline_for(pub), encoding="utf-8", newline="\n")
    return prefix


def _updater(root: Path) -> Path:
    elf, pe = _built("loment-hotupdate")
    p = root / _exe_name("loment-hotupdate")
    p.write_bytes(pe if sys.platform == "win32" else elf)
    p.chmod(0o755)
    return p


def _payload(prefix: Path, rel: Path, files: list[tuple[str, bytes, int]], *,
             release: str = "v0.1.5.1-hot.2", fix: bool = True) -> Path:
    key_pem, pub, _ = _keydir()
    base = (prefix / "share/loment/hot/baseline.txt").read_text(encoding="utf-8")
    lines = [
        "schema 1",
        f"platform {_host()}",
        f"release {release}",
        "base_digest " + hashlib.sha512(base.encode()).hexdigest(),
        "fixed_core " + hashlib.sha512(pub).hexdigest(),
    ]
    for name, data, mode in files:
        lines.append(f"file {name} {hashlib.sha512(data).hexdigest()} {mode:o}")
    if fix:
        lines.append("fix " + "a" * 40)
    payload = hp.pack_payload(files, ("\n".join(lines) + "\n").encode())
    name = f"payload-{_host()}.hot"
    rel.mkdir(parents=True, exist_ok=True)
    (rel / name).write_bytes(payload)
    hp.sign(payload, key_pem, rel / (name + ".sig"))
    return rel / name


def _run_updater(exe: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(exe), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=False, timeout=120)


def _fake_files() -> list[tuple[str, bytes, int]]:
    x = ".exe" if sys.platform == "win32" else ""
    return [(f"bin/loment-lomelf{x}", b"FAKE-LOMELF\n", 0o755),
            (f"bin/loment-driver{x}", b"FAKE-DRIVER\n", 0o755)]


@test
def test_the_updater_is_built_by_the_selfhost_chain() -> None:
    elf, pe = _built("loment-hotupdate")
    assert len(elf) > 100_000 and len(pe) > 100_000, (len(elf), len(pe))
    print(f"      自举链出更新器：ELF {len(elf)} B / PE {len(pe)} B")


@test
def test_check_apply_and_rollback_over_a_signed_payload() -> None:
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _, pub, _ = _keydir()
        prefix = _setup_prefix(td, pub)
        exe = _updater(td)
        cand = _payload(prefix, td / "rel", _fake_files())
        sig = Path(str(cand) + ".sig")
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(sig), "--check"])
        assert r.returncode == 0, r.stderr
        # --check 不落盘
        for name, _data, _mode in _fake_files():
            assert not (prefix / "share/loment/hot/gen/a" / name).exists(), name
        r = _run_updater(exe, ["--prefix", str(prefix), "--status"])
        assert r.returncode == 0 and "base" in r.stdout, r.stdout
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(sig)])
        assert r.returncode == 0, r.stderr
        assert (prefix / "share/loment/hot/current").read_text(encoding="ascii") == "a"
        r = _run_updater(exe, ["--prefix", str(prefix), "--status"])
        assert r.returncode == 0 and "current a" in r.stdout, r.stdout
        for name, data, _mode in _fake_files():
            got = (prefix / "share/loment/hot/gen/a" / name).read_bytes()
            assert got == data, name
        # 第二次装进另一格，再回滚到第一格
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(sig)])
        assert r.returncode == 0, r.stderr
        assert (prefix / "share/loment/hot/current").read_text(encoding="ascii") == "b"
        r = _run_updater(exe, ["--prefix", str(prefix), "--rollback"])
        assert r.returncode == 0, r.stderr
        assert (prefix / "share/loment/hot/current").read_text(encoding="ascii") == "a"
        print("      check / apply / apply / rollback：a -> b -> a")


@test
def test_a_tampered_payload_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _, pub, _ = _keydir()
        prefix = _setup_prefix(td, pub)
        exe = _updater(td)
        cand = _payload(prefix, td / "rel", _fake_files())
        data = bytearray(cand.read_bytes())
        data[-1] ^= 0xFF
        cand.write_bytes(bytes(data))
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(cand) + ".sig", "--check"])
        assert r.returncode != 0 and "signature" in r.stderr, r.stderr
        assert not (prefix / "share/loment/hot/current").exists()
        print("      改一个字节 -> 签名不过，什么也没写")


@test
def test_a_payload_without_fixes_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _, pub, _ = _keydir()
        prefix = _setup_prefix(td, pub)
        exe = _updater(td)
        cand = _payload(prefix, td / "rel", _fake_files(), fix=False)
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(cand) + ".sig", "--check"])
        assert r.returncode != 0 and "fix" in r.stderr, r.stderr
        print("      清单里没有 fix -> 拒绝")


@test
def test_a_wrong_pinned_key_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _, pub, _ = _keydir()
        prefix = _setup_prefix(td, pub)
        exe = _updater(td)
        cand = _payload(prefix, td / "rel", _fake_files())
        (prefix / "share/loment/hot/release-key.ed25519").write_bytes(os.urandom(32))
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(cand) + ".sig", "--check"])
        assert r.returncode != 0 and "fixed_core" in r.stderr, r.stderr
        print("      运行树里的公钥被换 -> fixed_core 对不上，拒绝")


_CAP_ROWS = """module caprows
choose runtime
capability c0 : sp[0..3] consumed(1)
capability c1 : sp[0..3] consumed(1)
fn _start() {
    guard c0(0);
    guard c0(1);
    guard c1(0);
    syscall4(60, 0, 0, 0);
}
"""


@test
def test_a_linker_from_the_payload_links_a_program() -> None:
    """把**当前树的链接器**装进代际，用代际里的那份编一段能力域程序并跑起来。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _, pub, _ = _keydir()
        prefix = _setup_prefix(td, pub)
        exe = _updater(td)
        elf, pe = _built("loment-lomelf")
        rel = f"bin/loment-lomelf{'.exe' if sys.platform == 'win32' else ''}"
        cand = _payload(prefix, td / "rel", [(rel, pe if sys.platform == "win32" else elf, 0o755)])
        r = _run_updater(exe, ["--prefix", str(prefix), "--payload", str(cand),
                               "--sig", str(cand) + ".sig"])
        assert r.returncode == 0, r.stderr
        assert (prefix / "share/loment/hot/current").read_text(encoding="ascii") == "a"
        link = prefix / "share/loment/hot/gen/a" / rel
        assert link.is_file(), link
        # 源 -> IR（参考编译器；与链接器无关）
        src = td / "caprows.lomt"
        src.write_text(_CAP_ROWS, encoding="utf-8", newline="\n")
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:2]
        ir = td / "caprows.ll"
        ir.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8", newline="\n")
        out = td / ("caprows.exe" if sys.platform == "win32" else "caprows.bin")
        r = subprocess.run([str(link), str(ir), str(out)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        assert r.returncode == 0, (r.returncode, r.stderr[-300:])
        assert out.is_file()
        run = subprocess.run([str(out)], capture_output=True, timeout=30)
        assert run.returncode == 0, (
            f"代际里的链接器编出来的程序 rc={run.returncode} —— GEP 行仍在重叠")
        print("      代际里的链接器编出 caprows：rc=0（#1830 的修复随载荷到达）")


@test
def test_the_launcher_prefers_the_active_generation() -> None:
    """装好的启动器必须读 `current` —— 否则载荷落了盘没人用（首支自举的真缺口）。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        prefix = td / "p"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "share/loment/hot/gen/a/bin").mkdir(parents=True)
        (prefix / "share/loment/version").write_text("0.1.5.1\n", encoding="ascii")
        launcher = prefix / "bin/loment"
        launcher.write_text(loment_dist._subst(loment_dist.LAUNCHER_SH, "release"),
                            encoding="ascii", newline="\n")
        launcher.chmod(0o755)
        (prefix / "bin/loment-fmt").write_text(
            "#!/bin/sh\necho BASE\nexit 1\n", encoding="ascii", newline="\n")
        (prefix / "bin/loment-fmt").chmod(0o755)
        (prefix / "share/loment/hot/gen/a/bin/loment-fmt").write_text(
            "#!/bin/sh\necho GEN\nexit 0\n", encoding="ascii", newline="\n")
        (prefix / "share/loment/hot/gen/a/bin/loment-fmt").chmod(0o755)
        src = td / "x.lomt"
        src.write_text("fn main() {}\n", encoding="utf-8", newline="\n")
        # `"bash"` 会被 CreateProcess 解析成 System32 的 WSL bash（它看不见 C:/…）；
        # 与 `loment_cli_test` 同一姿势：which 出来的那份 + MSYS 路径。
        bash = shutil.which("bash") or "bash"

        def shp(p: Path) -> str:
            s = str(p).replace("\\", "/")
            return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s

        run = lambda: subprocess.run([  # noqa: E731
            bash, shp(launcher), "fmt", shp(src)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        r = run()   # current 不存在 -> 基线 bin/
        assert r.returncode == 1 and "BASE" in r.stdout, (r.returncode, r.stdout, r.stderr)
        (prefix / "share/loment/hot/current").write_text("a", encoding="ascii")
        r = run()   # current = a -> 代际
        assert r.returncode == 0 and "GEN" in r.stdout, (r.returncode, r.stdout, r.stderr)
        # 首支自举：存量安装的旧启动器不认代际，自举脚本必须把新启动器换上去。
        for name in ("bootstrap-hotfix.sh", "bootstrap-hotfix.cmd"):
            body = (ROOT / ".github" / "hotfix" / name).read_text(encoding="utf-8")
            assert "loment-launcher" in body, name
        print("      启动器按 current 走代际；自举脚本换上新启动器")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotpack_test")


if __name__ == "__main__":
    sys.exit(main())
