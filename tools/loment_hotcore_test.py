#!/usr/bin/env python3
# loment_hotcore_test.py — 固定核（`loment/lib/hotswap.lomt`）在真进程里换掉自己（`#1151`/`#1152`）。
#
# 探针是一个真的 Loment 程序：`nest()` 递归到第 `DEPTH` 层，在最里面把装载件交给固定核，
# 然后**每一层都接着跑**。返回地址在换之前属于旧镜像，换之后必须落到新镜像上 ——
# 于是每层读到的版本常量必须来自**新**实现。`tag` 是函数体里的字面量，不是查表、
# 不是环境变量：只有返回地址真的被改写了，它才会变。
#
# 固定核自己做 mmap / mprotect / 摘要比对 / 槽表改写 / 帧链重映射 / epoch 发布，全是 Loment。

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


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


PROBE = """module hotcore
choose runtime
use bytes
use hotswap

const DEPTH: u32 = 4;

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

//: 递归层。`tag` 是**函数体里的字面量** —— 两个构建只差这一个数。
fn nest(d: u32, ctx: ptr, blob: ptr, n: u32, save: ptr) -> u32 {
    store32(ctx, 0, load32(ctx, 0) + 1);
    let inner: u32 = 0;
    if d > 0 {
        inner = nest(d - 1, ctx, blob, n, save);
    } else {
        let rc: u32 = hot_install(blob, n, save);
        store32(ctx, 4, rc);
    }
    let tag: u32 = 1;
    return inner + tag;
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
    let ctx: ptr = ptr_add(buf, 64);
    let data: ptr = ptr_add(buf, 256);
    let save: ptr = ptr_add(buf, 131072);
    // 一块"索引"：换之前由**旧代码**写满，换之后由**新代码**读回来（`#1155`）。
    let idx: ptr = ptr_add(buf, 160000);
    let k: u32 = 0;
    while k < 256 {
        store8(idx, k, (k * 7 + 3) as u8);
        k = k + 1;
    }
    store32(ctx, 0, 0);
    store32(ctx, 4, 0);
    let n: i64 = syscall4(0, fd as u64, data as u64, 200000);
    syscall4(3, fd as u64, 0, 0);
    let sum: u32 = nest(DEPTH, ctx, data, n as u32, save);
    let count: u32 = load32(ctx, 0);
    let rc: u32 = load32(ctx, 4);
    let done: u32 = load32(save, 16384);
    let sb: u32 = load32(data, 4);
    let sg: u32 = load32(data, 12);
    let ep: u32 = load32(sb as ptr, sg + 10);
    let ev: u32 = load32(ep as ptr, 0);
    let ck: u32 = 0;
    k = 0;
    while k < 256 {
        ck = ck + load8(idx, k);
        k = k + 1;
    }
    let _a: i64 = wstr(1, "SUM ");
    let _b: i64 = wdec(1, sum);
    let _c: i64 = wstr(1, " COUNT ");
    let _d: i64 = wdec(1, count);
    let _e2: i64 = wstr(1, " RC ");
    let _f: i64 = wdec(1, rc);
    let _g: i64 = wstr(1, " DONE ");
    let _h: i64 = wdec(1, done);
    let _i: i64 = wstr(1, " EPOCH ");
    let _j: i64 = wdec(1, ev);
    let _x: i64 = wstr(1, " IDX ");
    let _y: i64 = wdec(1, ck);
    let _q: i64 = wstr(1, "\\n");
    return 0;
}
"""

#: 想换成新实现的构建：把版本常量改成 2，其余逐字相同 —— 布局与调用点 id 都不动。
Q = PROBE.replace("let tag: u32 = 1;", "let tag: u32 = 2;")

#: 帧布局变了：`nest` 多一个临时量。**在栈上**的函数布局一变就必须拒绝。
Q_LAYOUT = Q.replace(
    "    let tag: u32 = 2;\n    return inner + tag;",
    "    let tag: u32 = 2;\n    let extra: u32 = load32(ctx, 8);\n"
    "    return inner + tag + extra;",
)

NEST2 = """
fn nest2(d: u32, ctx: ptr, blob: ptr, n: u32, save: ptr) -> u32 {
    store32(ctx, 0, load32(ctx, 0) + 1);
    let inner: u32 = 0;
    if d > 0 {
        inner = nest2(d - 1, ctx, blob, n, save);
    } else {
        let rc: u32 = hot_install(blob, n, save);
        store32(ctx, 4, rc);
    }
    let tag: u32 = 1;
    return inner + tag;
}
"""

#: 调用点 id 序列变了、布局没变：`nest` 改调同形的 `nest2`（两侧都定义，槽表不变）。
CALLS_P = PROBE.replace("fn _start() -> u32 {", NEST2 + "\nfn _start() -> u32 {")
CALLS_Q = (CALLS_P.replace("let tag: u32 = 1;", "let tag: u32 = 2;")
           .replace("inner = nest(d - 1, ctx, blob, n, save);",
                    "inner = nest2(d - 1, ctx, blob, n, save);"))


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


def _run(probe: Path) -> str | None:
    if sys.platform.startswith("linux"):
        r = subprocess.run([str(probe)], cwd=ROOT, capture_output=True, text=True)
        return r.stdout
    if not shutil.which("wsl"):
        return None

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


def _case(psrc: str, qsrc: str, mutate=None) -> dict[str, int]:
    pb, pm, _pt = _build(psrc)
    qb, qm, qt = _build(qsrc)
    blob = bytearray(hl.build_blob(pm, qm, qt))
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


def _skip(got) -> bool:
    if got.get("SKIP"):
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return True
    return False


@test
def test_every_live_frame_continues_in_the_new_image() -> None:
    for depth in (1, 2, 16):
        src = PROBE.replace("const DEPTH: u32 = 4;", f"const DEPTH: u32 = {depth};")
        qsrc = Q.replace("const DEPTH: u32 = 4;", f"const DEPTH: u32 = {depth};")
        got = _case(src, qsrc)
        if _skip(got):
            return
        # 每层都读到新常量 2：只有返回地址被逐帧改写才可能。
        assert got.get("SUM") == (depth + 1) * 2, (depth, got)
        # 每层的入口副作用只发生一次。
        assert got.get("COUNT") == depth + 1, (depth, got)
        assert got.get("RC") == 0, (depth, got)
        # 改写过几帧：hot_install 自己 + DEPTH+1 层 nest（`_start` 那帧没有调用点）。
        assert got.get("DONE") == depth + 2, (depth, got)
        assert got.get("EPOCH") == 1, (depth, got)
    print("      N = 1/2/16：每一帧都接着在新镜像里跑（版本常量逐层读到 2），副作用一次")


@test
def test_the_index_survives_the_swap() -> None:
    """状态不是代码：换之前写满的一块"索引"，换之后由**新代码**原样读回来（`#1155`）。"""
    got = _case(PROBE, Q)
    if _skip(got):
        return
    want = sum((k * 7 + 3) % 256 for k in range(256))
    assert got.get("IDX") == want, (got, want)
    assert got.get("SUM") == 10, got       # 5 帧 × 版本常量 2
    print(f"      索引校验和 {want} 换前换后相同 —— 换的是代码，不是状态")


@test
def test_the_swap_reports_one_epoch() -> None:
    got = _case(PROBE, Q)
    if _skip(got):
        return
    assert got.get("RC") == 0, got
    assert got.get("EPOCH") == 1, got
    print("      装完 epoch 是 READY（1）")


@test
def test_tampered_digest_is_refused() -> None:
    def mutate(blob, pm, qm) -> None:
        at = (hl.HDR + len(qm["funcs"]) * hl.FUNC
              + sum(len(f["calls"]) for f in qm["funcs"].values()) * hl.CALL
              + len(qm["relocs"]) * hl.RELOC + qm["fixed"]["gate"] + 3)
        blob[at] ^= 0xFF

    got = _case(PROBE, Q, mutate)
    if _skip(got):
        return
    assert got.get("RC") == 7, got
    assert got.get("SUM") == 5, got
    assert got.get("EPOCH") == 0, got
    print("      候选的闸被改了一个字节 —— RC 7，槽表与帧都没动")


@test
def test_slot_shape_mismatch_is_refused() -> None:
    def mutate(blob, pm, qm) -> None:
        struct.pack_into("<I", blob, 24, qm["fixed"]["slots"] + 1)

    got = _case(PROBE, Q, mutate)
    if _skip(got):
        return
    assert got.get("RC") == 4, got
    assert got.get("SUM") == 5, got
    print("      槽表条数不同 —— RC 4，没装")


@test
def test_frame_layout_change_is_refused_by_name() -> None:
    _pb, pm, _pt = _build(PROBE)
    slot = pm["funcs"]["nest"]["tramp"]
    got = _case(PROBE, Q_LAYOUT)
    if _skip(got):
        return
    assert got.get("RC") == (hl.FLAG_LAYOUT << 12) + slot, (slot, got)
    assert got.get("SUM") == 5, got          # 还是旧实现：每层 1
    assert got.get("EPOCH") == 0, got
    print(f"      nest 布局变了 —— 拒绝并点名槽 {slot}（RC 段 {hl.FLAG_LAYOUT}），旧代码照跑")


@test
def test_call_id_change_is_refused_by_name() -> None:
    _pb, pm, _pt = _build(CALLS_P)
    slot = pm["funcs"]["nest"]["tramp"]
    got = _case(CALLS_P, CALLS_Q)
    if _skip(got):
        return
    assert got.get("RC") == (hl.FLAG_CALLS << 12) + slot, (slot, got)
    assert got.get("SUM") == 5, got
    print(f"      调用点 id 序列变了 —— 拒绝并点名槽 {slot}（RC 段 {hl.FLAG_CALLS}）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotcore_test")


if __name__ == "__main__":
    sys.exit(main())
