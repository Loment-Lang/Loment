#!/usr/bin/env python3
# loment_hotstress_test.py — 稳定性模拟：同一个进程连着换好几次（`docs/290`）。
#
# 一个真进程从 v1 起，循环 N 次：读第 k 份候选 → 装进**不在跑**的那个槽 → 检查版本常量、
# 索引、epoch、映射数量 → 把上一个槽还掉。装完第 k 次之后跑的是 v(k+1) 的代码，
# 所以版本常量必须一路 2,3,4,…（只有返回地址真的被改写才可能）。
#
# 两个槽（0x10000000 / 0x20000000）轮流用：第一次换完之后进程就住在第一个候选槽里，
# 再往同一个地址 `MAP_FIXED` 会把它正在执行的那一页换掉。

from __future__ import annotations

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
import lomelf  # noqa: E402
import loment_hotload as hl  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


CYCLES = 6

PROBE = """module hotstress
choose runtime
use bytes
use hotswap

const CYCLES: u32 = 6;
const TAG: u32 = 1;

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
    let b: ptr = alloc(64);
    let x: u32 = v;
    let d: u32 = 0;
    if x == 0 {
        store8(b, 0, 48 as u8);
        return write_all(fd, b, 1);
    }
    while x > 0 {
        store8(b, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }
    let q: u32 = 0;
    while q < d {
        store8(b, 32 + q, load8(b, d - 1 - q) as u8);
        q = q + 1;
    }
    return write_all(fd, ptr_add(b, 32), d as u64);
}

fn put_s(fd: u64, key: str, v: u32) -> u32 {
    let _a: i64 = wstr(fd, " ");
    let _b: i64 = wstr(fd, key);
    let _c: i64 = wstr(fd, " ");
    let _d: i64 = wdec(fd, v);
    return 0;
}

fn cksum_s(p: ptr) -> u32 {
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 256 {
        s = s + load8(p, i);
        i = i + 1;
    }
    return s;
}

fn count_maps_s(scratch: ptr) -> u32 {
    let path: str = "/proc/self/maps";
    let i: u32 = 0;
    while i < str_len(path) {
        store8(scratch, i, str_byte(path, i) as u8);
        i = i + 1;
    }
    store8(scratch, i, 0 as u8);
    let fd: i64 = syscall4(2, scratch as u64, 0, 0);
    if fd < 0 {
        return 0;
    }
    let n: i64 = syscall4(0, fd as u64, ptr_add(scratch, 64) as u64, 32768);
    syscall4(3, fd as u64, 0, 0);
    let total: u32 = 0;
    let k: u32 = 0;
    while k < n as u32 {
        if load8(ptr_add(scratch, 64), k) == 10 {
            total = total + 1;
        }
        k = k + 1;
    }
    return total;
}

fn page_up_s(v: u32) -> u32 {
    return (v + 4095) / 4096 * 4096;
}

fn _start() -> u32 {
    let mp: i64 = syscall7(9, 0, 262144, 3, 34, 0, 0);
    if mp < 0 {
        return 2;
    }
    let buf: ptr = mp as ptr;
    let pathb: ptr = ptr_add(buf, 32);
    let data: ptr = ptr_add(buf, 512);
    let save: ptr = ptr_add(buf, 131072);
    let idx: ptr = ptr_add(buf, 160000);
    let maps: ptr = ptr_add(buf, 200000);
    let i: u32 = 0;
    while i < 256 {
        store8(idx, i, (i * 5 + 11) as u8);
        i = i + 1;
    }
    let k: u32 = 1;
    // 现在跑着的镜像在哪：第一次是链接基址；之后是**上一次成功装进去**的那个槽。
    // 失败的那一轮不改变它 —— 这正是"被拒的候选连一个字节都没动"。
    let where: u32 = 0;
    let live: u32 = 0;          // 上一个还值得还的槽（0 = 原始镜像，不能还）
    while k <= CYCLES {
        let tpl: str = "loment/build/h0.in";
        i = 0;
        while i < str_len(tpl) {
            store8(pathb, i, str_byte(tpl, i) as u8);
            i = i + 1;
        }
        store8(pathb, 14, (48 + k) as u8);
        store8(pathb, i, 0 as u8);
        let fd: i64 = syscall4(2, pathb as u64, 0, 0);
        if fd < 0 {
            let _e: i64 = wstr(1, "NOFILE\\n");
            return 1;
        }
        let n: i64 = syscall4(0, fd as u64, data as u64, 200000);
        syscall4(3, fd as u64, 0, 0);
        if k == 1 {
            where = load32(data, 4);      // 链接基址
        }
        let cur: u32 = load32(data, 52);
        let rc: u32 = hot_install(data, n as u32, save);
        let done: u32 = load32(save, 16384);
        let span: u32 = page_up_s(load32(data, 32));
        // epoch 的地址从**新镜像**的闸里读：换完之后跑着的代码在 `cand_slot`，
        // 上一槽马上要被还掉，再去读它的闸就是读一块已经 `munmap` 的页。
        let gate_at: u32 = load32(data, 12);
        // 被拒的那一轮**没有**在 `cur` 上映射任何东西 —— 去读它就是读一块没映射的页。
        let base_at: u32 = cur;
        if rc != 0 {
            base_at = where;
        }
        let ep: u32 = load32(base_at as ptr, gate_at + 10);
        let keep: u32 = 0;
        let ev: u32 = load32(ep as ptr, 0);
        let rel: u32 = 0;
        if rc == 0 {
            keep = live;
            live = cur;
            where = cur;
            if keep != 0 {
                rel = hot_release(keep, span);
            }
        }
        let tag: u32 = TAG;
        let _c: i64 = wstr(1, "CYCLE");
        let _1: u32 = put_s(1, "k", k);
        let _2: u32 = put_s(1, "cur", cur);
        let _3: u32 = put_s(1, "rc", rc);
        let _4: u32 = put_s(1, "tag", tag);
        let _5: u32 = put_s(1, "idx", cksum_s(idx));
        let _6: u32 = put_s(1, "done", done);
        let _7: u32 = put_s(1, "rel", rel);
        let _8: u32 = put_s(1, "epoch", ev);
        let _9: u32 = put_s(1, "maps", count_maps_s(maps));
        let _q: i64 = wstr(1, "\\n");
        k = k + 1;
    }
    return 0;
}
"""


def _build(src: str):
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "stress.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs[:3]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, info = lomelf.compile_ll(ir, hotswap=True)
    meta = hl.parse(info["hot"])
    return blob, meta, hl.text_of(blob, meta)


def _fields(out: str) -> list[dict[str, int]]:
    rows = []
    for line in out.splitlines():
        w = line.split()
        if not w or w[0] != "CYCLE":
            continue
        row = {}
        for i in range(1, len(w) - 1, 2):
            row[w[i]] = int(w[i + 1])
        rows.append(row)
    return rows


def _source(tag: int, cycles: int) -> str:
    return (PROBE.replace("const TAG: u32 = 1;", f"const TAG: u32 = {tag};")
                  .replace("const CYCLES: u32 = 6;", f"const CYCLES: u32 = {cycles};"))


def _case(versions: list[str], plan: list[tuple], *, tamper: tuple[int, ...] = ()):
    """`plan[k] = (旧版下标, 新版下标, 跑着的基址, 候选槽)`；`tamper` 是要改坏的第几轮。"""
    built = [_build(v) for v in versions]
    target = ROOT / "loment" / "build"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for k, (oi, ni, live, slot) in enumerate(plan, start=1):
        _ob, old_meta, _ot = built[oi]
        _nb, new_meta, new_text = built[ni]
        blob = bytearray(hl.build_blob(old_meta, new_meta, new_text,
                                       self_base=live, cand_slot=slot))
        if k in tamper:
            # 文本段在装载件的**末尾**：位置 = 总长 - text_len（别按表长手算）。
            import struct as _s
            text_len = _s.unpack_from("<I", blob, 44)[0]
            at = len(blob) - text_len + new_meta["fixed"]["gate"] + 3
            blob[at] ^= 0xFF
        f = target / f"h{k}.in"
        f.write_bytes(bytes(blob))
        written.append(f)
    with tempfile.TemporaryDirectory() as t:
        probe = Path(t) / "stress.elf"
        probe.write_bytes(built[0][0])
        os.chmod(probe, 0o755)
        try:
            out = _run(probe, len(plan))
        finally:
            for f in written:
                f.unlink(missing_ok=True)
    return out


@test
def test_six_back_to_back_swaps() -> None:
    versions = [_source(k, CYCLES) for k in range(1, CYCLES + 2)]
    plan = []
    for k in range(1, CYCLES + 1):
        slot = hl.SLOT_A if k % 2 == 1 else hl.SLOT_B
        live = None if k == 1 else (hl.SLOT_A if k % 2 == 0 else hl.SLOT_B)
        plan.append((k - 1, k, live, slot))
    out = _case(versions, plan)
    if out is None:
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return

    rows = _fields(out)
    assert len(rows) == CYCLES, (rows, out[:400])
    for i, row in enumerate(rows, start=1):
        assert row["rc"] == 0, (i, row)
        # 装完第 i 次，跑的是第 i+1 个构建 —— 版本常量来自**函数体**。
        assert row["tag"] == i + 1, (i, row)
        assert row["epoch"] == 1, (i, row)
        assert row["done"] > 0, (i, row)
        assert row["idx"] == sum((k * 5 + 11) % 256 for k in range(256)), (i, row)
    for row in rows:
        assert row["rel"] == 0, row                  # 上一个槽每次都还得掉（第一个没有）
    maps = [r["maps"] for r in rows]
    assert max(maps) - min(maps) <= 2, maps          # 没有无界增长
    print(f"      {CYCLES} 次连换：版本 2..{CYCLES + 1}、索引不变、每次还掉上一个槽、"
          f"映射数 {min(maps)}..{max(maps)}")


@test
def test_a_refused_candidate_does_not_disturb_the_process() -> None:
    """中间插一份被改坏的候选：进程照跑、版本不动，下一份好的照样装得上。"""
    versions = [_source(k, 3) for k in range(1, 4)]
    plan = [
        (0, 1, None, hl.SLOT_A),                 # v1 -> v2 进 A
        (1, 2, hl.SLOT_A, hl.SLOT_B),            # 这一份被改坏
        (1, 2, hl.SLOT_A, hl.SLOT_B),            # 同一份，好的
    ]
    out = _case(versions, plan, tamper=(2,))
    if out is None:
        print("SKIP: 没有 WSL/Linux，跑不了 ELF")
        return
    rows = _fields(out)
    assert len(rows) == 3, (rows, out[:400])
    assert rows[0]["rc"] == 0 and rows[0]["tag"] == 2, rows[0]
    assert rows[1]["rc"] != 0, rows[1]                # 固定核摘要不对 -> 拒绝
    assert rows[1]["tag"] == 2, rows[1]               # 还是上一版
    assert rows[2]["rc"] == 0 and rows[2]["tag"] == 3, rows[2]
    assert rows[2]["done"] > 0, rows[2]
    print(f"      中间一份被改坏（RC {rows[1]['rc']}）：版本停在 2，下一份照样装成 3")


def _run(probe: Path, cycles: int) -> str | None:
    if sys.platform.startswith("linux"):
        r = subprocess.run([str(probe)], cwd=ROOT, capture_output=True, text=True)
        return r.stdout
    if not shutil.which("wsl"):
        return None

    def w(p: Path) -> str:
        s = str(p.resolve()).replace("\\", "/")
        return "/mnt/" + s[0].lower() + s[2:]

    script = (f"rm -rf /tmp/hotstress && mkdir -p /tmp/hotstress/loment/build && "
              f"cp {w(probe)} /tmp/hotstress_probe && chmod +x /tmp/hotstress_probe")
    for k in range(1, cycles + 1):
        script += f" && cp {w(ROOT / 'loment' / 'build' / f'h{k}.in')} " \
                  f"/tmp/hotstress/loment/build/h{k}.in"
    script += " && cd /tmp/hotstress && /tmp/hotstress_probe"
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True, text=True)
    return r.stdout


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotstress_test")


if __name__ == "__main__":
    sys.exit(main())
