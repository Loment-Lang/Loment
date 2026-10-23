#!/usr/bin/env python3
# loment_hotcore_test.py — 固定核（`loment/lib/hotswap.lomt`）在真进程里换掉自己（`#1151`）。
#
# 探针是一个真的 Loment 程序：它先调自己的 `add(1,2)`（走槽表），把装载件交给固定核，
# 再调一次 —— 第二次必须落到候选镜像的实现上。固定核自己做 mmap / mprotect / 摘要比对 /
# 槽表改写 / epoch 发布，全部是 Loment。

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import loment_hotload as hl  # noqa: E402

TESTS: list = []
MAGIC = 0x4C544F48


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


PROBE = """module hotcore
choose runtime
use bytes
use hotswap

fn write_all(fd: u64, p: ptr, n: u64) -> i64 {
    let off: u64 = 0;
    while off < n {
        let w: i64 = syscall4(1, fd, ptr_add(p, off as u32) as u64, n - off);
        if w <= 0 {
            return w;
        }
        off = off + w as u64;
    }
    return off as i64;
}

fn wstr(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn wdec(fd: u64, v: u32) -> i64 {
    let buf: ptr = alloc(64);
    let x: u32 = v;
    let d: u32 = 0;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        return write_all(fd, buf, 1);
    }
    while x > 0 {
        store8(buf, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }
    let q: u32 = 0;
    while q < d {
        store8(buf, 32 + q, load8(buf, d - 1 - q) as u8);
        q = q + 1;
    }
    return write_all(fd, ptr_add(buf, 32), d as u64);
}

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() -> u32 {
    let mp: i64 = syscall7(9, 0, 262144, 3, 34, 0, 0);
    if mp < 0 {
        return 2;
    }
    let buf: ptr = mp as ptr;
    let path: str = "loment/build/hot.in";
    let i: u32 = 0;
    while i < str_len(path) {
        store8(buf, i, str_byte(path, i) as u8);
        i = i + 1;
    }
    store8(buf, i, 0 as u8);
    let fd: i64 = syscall4(2, buf as u64, 0, 0);
    if fd < 0 {
        let _e: i64 = wstr(1, "NOFILE\\n");
        return 1;
    }
    let data: ptr = ptr_add(buf, 256);
    let save: ptr = ptr_add(buf, 131072);
    let n: i64 = syscall4(0, fd as u64, data as u64, 200000);
    syscall4(3, fd as u64, 0, 0);
    let before: u32 = add(1, 2);
    let rc: u32 = hot_install(data, n as u32, save);
    let after: u32 = add(1, 2);
    let sb: u32 = load32(data, 4);
    let sg: u32 = load32(data, 12);
    let tl: u32 = load32(data, 32);
    let span: u32 = (tl + 4095) / 4096 * 4096;
    let ep: u32 = load32(sb as ptr, sg + 6);
    let ev: u32 = load32(ep as ptr, 0);
    let after2: u32 = 0;
    let rr: u32 = 0;
    let rel: u32 = 0;
    if rc == 0 {
        rr = hot_restore(data, n as u32, save, sb);
        after2 = add(1, 2);
        rel = hot_release(268435456, span);
    }
    let ev2: u32 = load32(ep as ptr, 0);
    let _a: i64 = wstr(1, "BEFORE ");
    let _b: i64 = wdec(1, before);
    let _c: i64 = wstr(1, " AFTER ");
    let _d: i64 = wdec(1, after);
    let _e2: i64 = wstr(1, " RC ");
    let _f: i64 = wdec(1, rc);
    let _g: i64 = wstr(1, " RESTORED ");
    let _h: i64 = wdec(1, after2);
    let _i: i64 = wstr(1, " RR ");
    let _j: i64 = wdec(1, rr);
    let _k: i64 = wstr(1, " RELEASE ");
    let _l: i64 = wdec(1, rel);
    let _m: i64 = wstr(1, " EPOCH ");
    let _n: i64 = wdec(1, ev);
    let _o: i64 = wstr(1, " EPOCH2 ");
    let _p: i64 = wdec(1, ev2);
    let _q: i64 = wstr(1, "\\n");
    return 0;
}
"""


def _build(src: str):
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "probe.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:3]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, info = lomelf.compile_ll(ir, hotswap=True)
    meta = hl.parse(info["hot"])
    return blob, meta, hl.text_of(blob, meta)


def _blob(pmeta, qmeta, qtext) -> bytes:
    funcs = sorted((fn["tramp"], fn["entry"]) for fn in qmeta["funcs"].values())
    out = struct.pack("<12I", MAGIC, pmeta["base"], pmeta["fixed"]["slots"],
                      pmeta["fixed"]["gate"], 25, qmeta["base"],
                      qmeta["fixed"]["slots"], qmeta["fixed"]["gate"],
                      len(qtext), len(qmeta["relocs"]), len(funcs), len(qtext))
    for off, tgt in qmeta["relocs"]:
        out += struct.pack("<III", off, tgt & 0xFFFFFFFF, tgt >> 32)
    for slot, entry in funcs:
        out += struct.pack("<II", slot, entry)
    return out + qtext


def _run(probe: Path) -> str | None:
    if sys.platform.startswith("linux"):
        r = subprocess.run([str(probe)], cwd=ROOT, capture_output=True, text=True)
        return r.stdout
    if not shutil.which("wsl"):
        return None
    probe_wsl = subprocess.run(["wsl", "-e", "true"], capture_output=True)

    def w(p: Path) -> str:
        s = str(p.resolve()).replace("\\", "/")
        return "/mnt/" + s[0].lower() + s[2:]

    script = (f"rm -rf /tmp/hotcore && mkdir -p /tmp/hotcore/loment/build && "
              f"cp {w(probe)} /tmp/hotcore_probe && chmod +x /tmp/hotcore_probe && "
              f"cp {w(ROOT / 'loment' / 'build' / 'hot.in')} /tmp/hotcore/loment/build/hot.in && "
              f"cd /tmp/hotcore && /tmp/hotcore_probe")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True, text=True)
    return r.stdout


def _fields(out: str) -> dict[str, int]:
    vals = {}
    for line in out.splitlines():
        w = line.split()
        for i in range(0, len(w) - 1, 2):
            try:
                vals[w[i]] = int(w[i + 1])
            except ValueError:
                pass
    return vals


def _case(mutate=None) -> dict[str, int]:
    pb, pm, _pt = _build(PROBE)
    qb, qm, qt = _build(PROBE.replace("return a + b;", "return a + b + 1;"))
    blob = bytearray(_blob(pm, qm, qt))
    if mutate is not None:
        mutate(blob, pm, qm)
    with tempfile.TemporaryDirectory() as t:
        probe = Path(t) / "probe.elf"
        probe.write_bytes(pb)
        os.chmod(probe, 0o755)
        (ROOT / "loment" / "build").mkdir(parents=True, exist_ok=True)
        target = ROOT / "loment" / "build" / "hot.in"
        target.write_bytes(bytes(blob))
        try:
            out = _run(probe)
        finally:
            target.unlink(missing_ok=True)
    if out is None:
        return {"SKIP": 1}
    return _fields(out)


@test
def test_fixed_core_flips_its_own_table() -> None:
    got = _case()
    if got.get("SKIP"):
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return
    assert got.get("BEFORE") == 3, got
    assert got.get("AFTER") == 4, got
    assert got.get("RC") == 0, got
    assert got.get("EPOCH") == 1, got
    assert got.get("RESTORED") == 3, got
    assert got.get("RR") == 0, got
    assert got.get("RELEASE") == 0, got
    assert got.get("EPOCH2") == 0, got
    print("      固定核：mmap/翻转 3 -> 4（epoch=1），改回 3，旧槽 munmap（epoch=0）")


@test
def test_tampered_digest_is_refused() -> None:
    def mutate(blob, pm, qm) -> None:
        at = 48 + len(qm["relocs"]) * 12 + len(qm["funcs"]) * 8 + qm["fixed"]["gate"] + 3
        blob[at] ^= 0xFF

    got = _case(mutate)
    if got.get("SKIP"):
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return
    assert got.get("RC") == 7, got
    assert got.get("AFTER") == 3, got
    assert got.get("EPOCH") == 0, got
    assert got.get("RESTORED") == 0, got
    print("      候选的闸被改了一个字节 —— RC 7，槽表没动，epoch 仍是 0")


@test
def test_slot_shape_mismatch_is_refused() -> None:
    def mutate(blob, pm, qm) -> None:
        struct.pack_into("<I", blob, 24, qm["fixed"]["slots"] + 1)

    got = _case(mutate)
    if got.get("SKIP"):
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return
    assert got.get("RC") == 4, got
    assert got.get("AFTER") == 3, got
    assert got.get("RESTORED") == 0, got
    print("      槽表条数不同 —— RC 4，没装")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotcore_test")


if __name__ == "__main__":
    sys.exit(main())
