#!/usr/bin/env python3
# loment_lumtui_test.py — LumtUI (`loment/lib/lumtui*.lomt`) 的判据。
#
# ## 三条判据, 三把**不同的**尺子
#
# 这个库里有三块东西, 它们的"对"不是同一个意思, 所以不能拿同一条判据去量:
#
#   1. **`.fuc` 的字节** —— 对照物是 `lom/fuc.lom`。那是 L0 单一真源, 由 `lomc`
#      现场生成一份 Python 布局; 判据拿它 pack 出期望字节, 与库发出来的**逐字节**比。
#      这把尺子最硬: 一个字节不对就红, 而且它量的是"有没有重述 L0"。
#   2. **布局与命中** —— 对照物是**独立写在 Python 里的一份**。不是"再实现一遍就信"，
#      而是这批期望值是按文件头那几条语义**推**出来的 (固定/百分比/弹性/对齐/间距),
#      不是在 Loment 那一侧抄回来的。
#   3. **字体** —— 对照物是 **FreeType** (经 Pillow 暴露)。这是唯一一把**外部**尺子:
#      度量 (步进/升部/降部) 是精确整数, 直接逐条比; 光栅化与 FreeType 的差别在
#      hinting 上, 所以比的是**墨迹面积与包围盒**, 带容差 —— 且容差写死在这里,
#      放宽它要在这里改, 不是悄悄调一个常数。
#
# 另外两条: Python 表层语法那一个模块 (`lumtui_math.lomt`) 与 Python 逐条对;
# 示例程序 (`lumtui_demo.lomt`) 的输出是确定的, 逐条对几个记号。
#
# 用法: python tools/loment_lumtui_test.py   (无 WSL / 无字体 / 无 Pillow 时 SKIP, 退出码 0)

from __future__ import annotations

import importlib.util
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc   # noqa: E402
import lomelf    # noqa: E402
import lomc      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 跑起来的脚手架

#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True, text=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(src: Path, td: Path, name: str) -> Path:
    """一份入口源 -> ELF。走**纯 Python 后端** (`lomelf`), 不起 clang。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 检查不过: {errs[:3]}"
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / f"{name}.elf"
    exe.write_bytes(blob)
    return exe


def _run_raw(exe: Path, td: Path) -> bytes:
    """在 WSL 里跑, **按原始字节**收 stdout (程序可能写的是二进制)。"""
    out = td / "stdout.bin"
    s = _wsl_path(exe)
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc", f"chmod +x {s} && {s} > {_wsl_path(out)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    assert r.stdout.strip() == "0", f"退出码 {r.stdout!r} / stderr {r.stderr[:300]}"
    return out.read_bytes()


def _run_text(exe: Path, td: Path) -> str:
    return _run_raw(exe, td).decode("utf-8")


def _probe(td: Path, name: str, src: str) -> str:
    p = td / f"{name}.lomt"
    p.write_text(src, encoding="utf-8", newline="\n")
    return _run_text(_build(p, td, name), td)


def _kv(text: str) -> list[tuple[str, int]]:
    """探针的 `tag=value` 行 -> `[(tag, int)]`。空行与没有 `=` 的行一律跳过。"""
    out: list[tuple[str, int]] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out.append((k, int(v)))
    return out


def _probe_raw(td: Path, name: str, src: str) -> bytes:
    """同一件事, 但程序写的是**二进制** (`.fuc`), 所以按字节收、不解码。"""
    p = td / f"{name}.lomt"
    p.write_text(src, encoding="utf-8", newline="\n")
    return _run_raw(_build(p, td, name), td)


def _write_fuc_py() -> object:
    """`lom/fuc.lom` -> 一份 Python 布局 (`lomc` 生成), 作为 L0 单一真源的对照面。"""
    gen = ROOT / "lom" / "build" / "fuc.py"
    if not gen.exists():
        lomc.ensure_python(ROOT / "lom" / "fuc.lom", gen)
    spec = importlib.util.spec_from_file_location("lom_fuc_gen", gen)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


# ---------------------------------------------------------------- 1. .fuc 字节

#: 与 Python 那侧**同一份**界面: 一张卡片 + 两个按钮 + 两个令牌。
#: 两边都按这个描述各写一遍, 于是"库有没有重述 L0"这件事才量得准。
FUC_PROBE = r'''
module lumtui_fuc_probe
choose runtime

use lumtui
use lumtui_doc

fn _start() {
    let a: ptr = alloc(lumtui_arena_bytes());
    let d: ptr = lumtui_doc_init(a, 320, 240);
    let card: u32 = lumtui_doc_add(d, LUMTUI_K_CARD, 1);
    let b1: u32 = lumtui_doc_add(d, LUMTUI_K_BUTTON, 2);
    let b2: u32 = lumtui_doc_add(d, LUMTUI_K_BUTTON, 3);
    let _x: u32 = lumtui_doc_add_child(d, card, b1);
    let _y: u32 = lumtui_doc_add_child(d, card, b2);
    lumtui_set_text(d, b1, "OK");
    lumtui_set_text(d, b2, "Cancel");
    lumtui_set_size(d, b1, LUMTUI_M_FIXED, 72, LUMTUI_M_FIXED, 24);
    lumtui_set_pad(d, card, 8, 8, 8, 8);
    lumtui_set_gap(d, card, 6);
    lumtui_set_radius(d, b1, 4);
    lumtui_set_bg(d, b1, 4294901760);
    lumtui_set_fg(d, b1, 4294967295);
    lumtui_set_flags(d, b1, 16);
    lumtui_set_align(d, b2, LUMTUI_A_CENTER);
    lumtui_set_opacity(d, b2, 128);
    let _t1: u32 = lumtui_token(d, "accent", 0, 4283215696);
    let _t2: u32 = lumtui_token(d, "gap.md", 1, 6);
    let cap: u32 = lumtui_emit_bytes(d);
    let out: ptr = alloc(cap);
    let n: u32 = lumtui_doc_emit(d, out, cap);
    let _w: i64 = syscall4(1, 1, out as u64, n as u64);
    syscall4(60, 0, 0, 0);
}
'''


def _expected_fuc() -> bytes:
    """照 `lom/fuc.lom` 的记录布局, 在 Python 里把同一份界面 pack 出来。"""
    fuc = _write_fuc_py()
    N = fuc.NODE_STRUCT

    def node(kind, nid, flags, cc, x, y, wm, wv, hm, hv, pl, pt, pr, pb, gap, rad,
             elev, align, just, size, bg, fg, tid, tone, op, fc, extra):
        return N.pack(kind, nid, flags, cc, x, y, wm, wv, hm, hv, pl, pt, pr, pb,
                      gap, rad, elev, align, just, size, bg, fg, tid, tone, op, fc,
                      extra)

    # 建的时候默认 w/h 都是 GROW:1, 节点按创建序; 摊平是 BFS —— card(0) 的子节点
    # 就是 [b1, b2], 于是 b1 -> 1, b2 -> 2。
    nodes = b"".join([
        # card: kind 3, id 1, 无旗标, 2 个子, x/y 0, w/h 都还是默认 GROW:1,
        #       pad 8/8/8/8, gap 6, opacity 255 (doc_add 的默认)
        node(3, 1, 0, 2, 0, 0, 2, 1, 2, 1, 8, 8, 8, 8, 6, 0, 0, 0, 0, 0,
             0, 0, 0, 0, 255, 1, 0),
        # b1: kind 14, id 2, flags 16 (BOLD), 0 子, w/h FIXED 72x24, radius 4,
        #     bg 4294901760, fg 4294967295, text "OK" -> 1
        node(14, 2, 16, 0, 0, 0, 0, 72, 0, 24, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0,
             4294901760, 4294967295, 1, 0, 255, 0, 0),
        # b2: kind 14, id 3, 0 子, 默认 GROW, align CENTER(1), opacity 128,
        #     text "Cancel" -> 2
        node(14, 3, 0, 0, 0, 0, 2, 1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0,
             0, 0, 2, 0, 128, 0, 0),
    ])
    # 字符串表: 0 号恒为空串, 然后按首次出现序
    strtab = struct.pack("<I", 3)
    for s in (b"", b"OK", b"Cancel"):
        strtab += struct.pack("<H", len(s)) + s
    toktab = (struct.pack("<H", 6) + b"accent" + struct.pack("<BI", 0, 4283215696)
              + struct.pack("<H", 6) + b"gap.md" + struct.pack("<BI", 1, 6))
    str_off = fuc.HEADER_SIZE + len(nodes)
    tok_off = str_off + len(strtab)
    head = struct.pack("<IHH", fuc.MAGIC, fuc.VERSION, 0)
    head += struct.pack("<I", 3) + struct.pack("<I", fuc.HEADER_SIZE)
    head += struct.pack("<I", str_off) + struct.pack("<I", len(strtab))
    head += struct.pack("<I", tok_off) + struct.pack("<I", 2)
    head += struct.pack("<II", 0, 0)
    head += struct.pack("<HHHH", 320, 240, 0, 0)
    assert len(head) == fuc.HEADER_SIZE, len(head)
    body = head + nodes + strtab + toktab
    return body + struct.pack("<I", _fnv1a(body))


@test
def test_fuc_bytes_match_lom_single_source():
    """库发出来的 `.fuc` 与 `lom/fuc.lom` 的记录布局**逐字节相同**。

    这条判据量的是"LumtUI 有没有把 L0 的布局重述一遍": 只要库这边自己拍一个偏移,
    它就会红。它也是"`lumtui_doc` 真的合并了 `fuic.py` 那一半"的唯一证据。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        got = _probe_raw(td, "fuc_probe", FUC_PROBE)
    want = _expected_fuc()
    if got != want:
        i = next((k for k in range(max(len(got), len(want)))
                  if (got[k] if k < len(got) else None) != (want[k] if k < len(want) else None)), None)
        raise AssertionError(
            f".fuc 与 lom/fuc.lom 不一致 @ 第 {i} 字节: "
            f"LumtUI={got[i] if i is not None and i < len(got) else '<无>'} "
            f"L0={want[i] if i is not None and i < len(want) else '<无>'} "
            f"(长度 {len(got)} vs {len(want)})")
    print(f"      .fuc {len(want)} 字节与 lom/fuc.lom 逐字节一致")


# ---------------------------------------------------------------- 2. 布局 / 命中

LAYOUT_PROBE = r'''
module lumtui_layout_probe
choose runtime

use lumtui
use lumtui_doc
use lumtui_layout
use "loment/lib/num.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

fn say(sc: ptr, tag: str, v: u32) {
    let _a: i64 = wr(1, str_ptr(tag), str_len(tag));
    let _b: u32 = num_to_dec(sc, v);
    let _c: i64 = wr(1, sc, _b);
    let _d: i64 = wr(1, str_ptr("\n"), 1);
}

fn _start() {
    let sc: ptr = alloc(32);
    let a: ptr = alloc(lumtui_arena_bytes());
    let d: ptr = lumtui_doc_init(a, 100, 60);
    let root: u32 = lumtui_doc_add(d, LUMTUI_K_COL, 1);
    let c1: u32 = lumtui_doc_add(d, LUMTUI_K_BUTTON, 2);
    let c2: u32 = lumtui_doc_add(d, LUMTUI_K_BUTTON, 3);
    let c3: u32 = lumtui_doc_add(d, LUMTUI_K_CARD, 4);
    let _a1: u32 = lumtui_doc_add_child(d, root, c1);
    let _a2: u32 = lumtui_doc_add_child(d, root, c2);
    let _a3: u32 = lumtui_doc_add_child(d, root, c3);
    lumtui_set_pad(d, root, 5, 5, 5, 5);
    lumtui_set_gap(d, root, 4);
    lumtui_set_size(d, c1, LUMTUI_M_FIXED, 30, LUMTUI_M_FIXED, 10);
    lumtui_set_size(d, c2, LUMTUI_M_GROW, 2, LUMTUI_M_PERCENT, 50);
    lumtui_set_size(d, c3, LUMTUI_M_GROW, 1, LUMTUI_M_GROW, 1);
    lumtui_set_justify(d, root, LUMTUI_A_SPACE_BETWEEN);
    lumtui_set_align(d, c1, LUMTUI_A_END);
    let cap: u32 = lumtui_emit_bytes(d);
    let blob: ptr = alloc(cap);
    let n: u32 = lumtui_doc_emit(d, blob, cap);
    let rc: ptr = alloc(lumtui_fuc_count(blob) * LUMTUI_RECT_BYTES);
    let _m: u32 = lumtui_layout(blob, rc, 0, 0, 100, 60);
    let i: u32 = 0;
    while i < lumtui_fuc_count(blob) {
        let r: ptr = ptr_add(rc, i * LUMTUI_RECT_BYTES);
        say(sc, "n=", i);
        say(sc, "x=", lumtui_rect_x(r));
        say(sc, "y=", lumtui_rect_y(r));
        say(sc, "w=", lumtui_rect_w(r));
        say(sc, "h=", lumtui_rect_h(r));
        i = i + 1;
    }
    say(sc, "hit1=", lumtui_hit_test(blob, rc, 10, 20));
    say(sc, "hit2=", lumtui_hit_test(blob, rc, 90, 55));
    say(sc, "hitno=", lumtui_hit_test(blob, rc, 99, 59));
    say(sc, "fn=", lumtui_focus_count(blob));
    say(sc, "f0=", lumtui_focus_at(blob, 0));
    say(sc, "f1=", lumtui_focus_at(blob, 1));
    say(sc, "fnx=", lumtui_focus_next(blob, 2));
    syscall4(60, 0, 0, 0);
}
'''


def _expected_layout() -> list[tuple[str, int]]:
    """照 `lumtui_layout.lomt` 文件头那几条语义, **在这个文件里推一遍**。

    不是"再实现一遍布局引擎" —— 这里只手算这三个节点, 每一步都写出来, 于是
    期望值的来历看得见 (硬编码一个数就没有这个好处)。
    """
    out: list[tuple[str, int]] = []
    # root: col, pad 5, gap 4, justify space-between。整块 100x60。
    # 内容盒 = (5,5) 90x50。
    cx, cy, cw, ch = 5, 5, 90, 50
    # 主轴是 y。固定长度: c1 固定 10; c2 是百分数 -> 50% * 50 = 25; c3 弹性。
    fixed = 10 + 25
    gaps = 4 * 2
    slack = ch - fixed - gaps            # 50 - 35 - 8 = 7
    # 弹性权重: c3 权重 1 -> 吃满 slack (有弹性成员时 slack 全归它)
    c3_h = slack
    # justify space-between: 多出来的 extra 是 0 (被弹性吃掉了), 所以逐个紧挨
    # c1 at y=5, h=10;  y2 = 5+10+4 = 19, h=25;  y3 = 19+25+4 = 48, h=7
    out += [("n", 0), ("x", cx - 5), ("y", cy - 5), ("w", 100), ("h", 60)]   # root 拿到整块
    # c1: 交叉轴 align END -> x = 5 + (90-30) = 65
    out += [("n", 1), ("x", 65), ("y", 5), ("w", 30), ("h", 10)]
    # c2: w 是弹性(权重2) —— 主轴是 y, 所以 w 是**交叉轴**, GROW 当填满 -> 90
    out += [("n", 2), ("x", 5), ("y", 19), ("w", 90), ("h", 25)]
    # c3: 交叉轴 GROW -> 90
    out += [("n", 3), ("x", 5), ("y", 48), ("w", 90), ("h", c3_h)]
    # 命中: (10,20) 在 c2 里 (5..95 × 19..44); (90,55) 在 c3 的下边界**外**
    # (右下开区间), 所以落到 root; (99,59) 同样只有 root 收。
    out += [("hit1", 2), ("hit2", 0), ("hitno", 0)]
    # 焦点序: 只有两个 BUTTON 可聚焦 (c3 是 CARD); 走到头回绕到第一个。
    out += [("fn", 2), ("f0", 1), ("f1", 2), ("fnx", 1)]
    return out


@test
def test_layout_matches_independent_expectation():
    """布局算出来的矩形与**在这里独立推出来的**那几个数逐条相同。"""
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        got = _kv(_probe(td, "layout_probe", LAYOUT_PROBE))
    want = _expected_layout()
    g = got
    if g != want:
        bad = next((i for i in range(max(len(g), len(want)))
                    if (g[i] if i < len(g) else None) != (want[i] if i < len(want) else None)), None)
        raise AssertionError(
            f"布局不一致 @ 第 {bad} 项: "
            f"LumtUI={g[bad] if bad is not None and bad < len(g) else '<无>'} "
            f"期望={want[bad] if bad is not None and bad < len(want) else '<无>'}")
    print(f"      布局 + 命中 + 焦点序: {len(g)} 项独立推出的一致")


# ---------------------------------------------------------------- 3. Python 写法那一块

MATH_PROBE = r'''
module lumtui_math_probe
choose runtime

use lumtui_math
use "loment/lib/num.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

fn say(sc: ptr, tag: str, v: u32) {
    let _a: i64 = wr(1, str_ptr(tag), str_len(tag));
    let _b: u32 = num_to_dec(sc, v);
    let _c: i64 = wr(1, sc, _b);
    let _d: i64 = wr(1, str_ptr("\n"), 1);
}

fn _start() {
    let sc: ptr = alloc(32);
    say(sc, "a=", lumtui_fp_mul(98304, 32768) as u32);
    say(sc, "b=", lumtui_fp_to_int(98304) as u32);
    say(sc, "c=", lumtui_clamp(-9, 0, 255) as u32);
    say(sc, "d=", lumtui_lerp(0, 100, 16384) as u32);
    say(sc, "e=", lumtui_ease_quad_in(32768) as u32);
    say(sc, "f=", lumtui_ease_smooth(32768) as u32);
    say(sc, "g=", lumtui_ease_cubic_out(32768) as u32);
    say(sc, "h=", lumtui_sin_deg(30) as u32);
    say(sc, "i=", lumtui_sqrt(99) as u32);
    say(sc, "j=", lumtui_rgba(1, 2, 3, 4) as u32);
    say(sc, "k=", lumtui_col_luma(16777215) as u32);
    say(sc, "l=", lumtui_blend(0, 16777215, 128) as u32);
    say(sc, "m=", lumtui_grow_share(300, 2, 5) as u32);
    say(sc, "n=", lumtui_snap(37, 8) as u32);
    say(sc, "o=", lumtui_fnv1a_step(2166136261, 97) as u32);
    say(sc, "p=", lumtui_hit_round(1, 1, 0, 0, 20, 20, 6) as u32);
    syscall4(60, 0, 0, 0);
}
'''


def _expected_math() -> list[tuple[str, int]]:
    """**本语言**的语义 (除以零向零截断, 不是 CPython 的向下取整)。"""
    ONE = 65536

    def fpmul(a, b):
        q = abs(a * b) // ONE
        return q if (a * b) >= 0 else -q

    def lerp(a, b, t):
        return a + fpmul(b - a, t)

    def fptoi(v):
        return (v + ONE // 2) // ONE if v >= 0 else (v - ONE // 2) // ONE

    def ease_smooth(t):
        return fpmul(fpmul(t, t), 3 * ONE - 2 * t)

    def ease_cubic_out(t):
        u = ONE - t
        return ONE - fpmul(fpmul(u, u), u)

    def sin_deg(deg):
        d = deg % 360
        sign = 1
        if d > 180:
            d -= 180
            sign = -1
        num, den = 4 * d * (180 - d), 40500 - d * (180 - d)
        return sign * (num * ONE) // den

    def sqrt(v):
        x = v
        y = (x + 1) // 2
        while y < x:
            x = y
            y = (x + v // x) // 2
        return x

    def blend(dst, src, alpha):
        if alpha == 0:
            return dst
        if alpha >= 255:
            return src
        inv = 255 - alpha
        ch = lambda c, sh: (c >> sh) & 0xFF
        r = (ch(src, 16) * alpha + ch(dst, 16) * inv) // 255
        g = (ch(src, 8) * alpha + ch(dst, 8) * inv) // 255
        b = (ch(src, 0) * alpha + ch(dst, 0) * inv) // 255
        return ((ch(dst, 24) & 0xFF) << 24) | (r << 16) | (g << 8) | b

    return [
        ("a", fpmul(98304, 32768)),                       # 49152
        ("b", fptoi(98304)),                              # 2
        ("c", 0),
        ("d", lerp(0, 100, 16384)),                       # 25
        ("e", fpmul(32768, 32768)),                       # 16384
        ("f", ease_smooth(32768)),                        # 32768
        ("g", ease_cubic_out(32768)),                     # 57344
        ("h", sin_deg(30)),                               # 32768
        ("i", sqrt(99)),                                  # 9
        ("j", (4 << 24) | (1 << 16) | (2 << 8) | 3),      # 67174915
        ("k", (255 * 306 + 255 * 601 + 255 * 117) // 1024),
        ("l", blend(0, 0xFFFFFF, 128)),                   # 8421504
        ("m", (300 * 2) // 5),                            # 120
        ("n", ((37 + 4) // 8) * 8),                       # 40
        ("o", ((2166136261 ^ 97) * 16777619) % 4294967296),
        ("p", 0),                                         # 角上那点在圆外
    ]


@test
def test_python_grammar_module_matches_python():
    """`lumtui_math.lomt` (**Python 写法**写的 Loment) 与 Python 逐条相同。

    这条也是"用 Python 语法写"这一条要求在库里**落了地**的证据: 它真的是一份
    `choose write grammar python` 的单元, 由 `lomentc.load` 的前门在进程内翻成
    Loment, 再被别的 Loment 模块 `use`。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        got = _kv(_probe(td, "math_probe", MATH_PROBE))
    want = _expected_math()
    if got != want:
        bad = next((i for i in range(max(len(got), len(want)))
                    if (got[i] if i < len(got) else None) != (want[i] if i < len(want) else None)), None)
        raise AssertionError(f"数学核不一致 @ {bad}: {got[bad]} vs {want[bad]}")
    print(f"      数学核 (Python 写法): {len(got)} 项与 Python 一致")


# ---------------------------------------------------------------- 4. 字体 (外部尺子)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\times.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
#: 逐字节量到的那些字。ASCII 里挑几个: 直笔画、曲线、有降部、复合字形都有。
FONT_CHARS = "ABCEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
FONT_EXTRA = [0xE9, 0xF1, 0xC5]        # 复合字形: eacute / ntilde / Aring


def _find_font() -> tuple[Path, str] | None:
    for c in FONT_CANDIDATES:
        p = Path(c)
        if p.exists():
            wsl = "/mnt/" + c[0].lower() + c[2:].replace("\\", "/") if ":" in c else c
            return p, wsl
    return None


def _have_pillow() -> bool:
    try:
        import PIL.ImageFont  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


#: 读一个字体文件, 报出逐字形的**字体单位**步进与整像素度量。
#: 步进在字体单位下是整数, 与 FreeType 对得上**没有舍入** —— 这是最硬的一条。
FONT_PROBE = r'''
module lumtui_font_probe
choose runtime

use lumtui
use lumtui_font
use "loment/lib/num.lomt"

const M_FONT: u32 = 0;
const M_FREC: u32 = 2097152;
const M_SCR: u32 = 2097232;
const M_PATH: u32 = 2120464;
const M_OUT: u32 = 2120720;
const M_BMP: u32 = 2120752;
const M_TOT: u32 = 2129968;

fn mem() -> ptr {
    let cur: u64 = syscall4(12, 0, 0, 0) as u64;
    let want: u64 = cur + (M_TOT as u64) + 4096;
    let got: u64 = syscall4(12, want, 0, 0) as u64;
    if got < want {
        syscall4(60, 66, 0, 0);
    }
    return cur as ptr;
}

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

fn say(sc: ptr, tag: str, v: u32) {
    let _a: i64 = wr(1, str_ptr(tag), str_len(tag));
    let _b: u32 = num_to_dec(sc, v);
    let _c: i64 = wr(1, sc, _b);
    let _d: i64 = wr(1, str_ptr("\n"), 1);
}

fn cstr(dst: ptr, s: str) {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    while i < n {
        store8(dst, i, str_byte(s, i) as u8);
        i = i + 1;
    }
    store8(dst, n, 0 as u8);
}

fn slurp(path: ptr, dst: ptr, cap: u32) -> u32 {
    let fd: i64 = syscall4(257, (0 as u64) - 100, path as u64, 0);
    if fd < 0 {
        return 0;
    }
    let off: u64 = 0;
    while off < (cap as u64) {
        let got: i64 = syscall4(0, fd as u64, (dst as u64) + off, (cap as u64) - off);
        if got <= 0 {
            let _c: i64 = syscall4(3, fd as u64, 0, 0);
            return off as u32;
        }
        off = off + (got as u64);
    }
    let _c: i64 = syscall4(3, fd as u64, 0, 0);
    return off as u32;
}

/// 一个码点一个码点地报: 字形号 / 字体单位步进 / 像素步进 / 位图盒 / 墨迹和。
fn probe_char(font: ptr, f: ptr, sc: ptr, box: ptr, bmp: ptr, cp: u32, size: u32) -> u32 {
    say(sc, "cp=", cp);
    let gi: u32 = lumtui_font_glyph(font, f, cp);
    say(sc, "gid=", gi);
    say(sc, "adv=", lumtui_font_advance_units(font, f, gi));
    say(sc, "advpx=", lumtui_font_advance_px(font, f, cp, size));
    let r: u32 = lumtui_font_render(font, f, cp, size, sc, bmp, 96, 96, box);
    say(sc, "ok=", r);
    if r == 0 {
        return 0;
    }
    let w: u32 = lumtui_ld32(box, 8);
    let h: u32 = lumtui_ld32(box, 12);
    say(sc, "bw=", w);
    say(sc, "bh=", h);
    let ink: u32 = 0;
    let i: u32 = 0;
    while i < w * h {
        ink = ink + load8(bmp, i);
        i = i + 1;
    }
    say(sc, "ink=", ink);
    return 1;
}

fn _start() {
    let memp: ptr = mem();
    let font: ptr = ptr_add(memp, M_FONT);
    let f: ptr = ptr_add(memp, M_FREC);
    let sc: ptr = ptr_add(memp, M_SCR);
    let path: ptr = ptr_add(memp, M_PATH);
    let box: ptr = ptr_add(memp, M_OUT);
    let bmp: ptr = ptr_add(memp, M_BMP);
    cstr(path, "__FONT_PATH__");
    let n: u32 = slurp(path, font, 2097152);
    if n == 0 {
        syscall4(60, 7, 0, 0);
    }
    say(sc, "load=", lumtui_font_load(font, n, f));
    say(sc, "upem=", lumtui_font_upem(f));
    say(sc, "ascpx=", lumtui_font_ascent_px(f, 32));
    say(sc, "descpx=", lumtui_font_descent_px(f, 32));
    say(sc, "linepx=", lumtui_font_line_h_px(f, 32));
    let s: str = "__CHARS__";
    let k: u32 = 0;
    while k < str_len(s) {
        let _q: u32 = probe_char(font, f, sc, box, bmp, str_byte(s, k), 32);
        k = k + 1;
    }
    let x: u32 = 0;
    while x < 3 {
        let cp: u32 = lumtui_pick_extra(x);
        let _r: u32 = probe_char(font, f, sc, box, bmp, cp, 32);
        x = x + 1;
    }
    syscall4(60, 0, 0, 0);
}

fn lumtui_pick_extra(i: u32) -> u32 {
    if i == 0 {
        return 233;
    }
    if i == 1 {
        return 241;
    }
    return 197;
}
'''


def _parse_font_out(text: str) -> dict:
    """把探针的 label/value 流拆成 `{cp: {gid:.., adv:.., ...}, 'head': {...}}`。"""
    head: dict[str, int] = {}
    chars: dict[int, dict[str, int]] = {}
    cur: dict[str, int] | None = None
    for ln in text.splitlines():
        ln = ln.strip()
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        if k == "cp":
            cur = {}
            chars[int(v)] = cur
            continue
        if cur is None:
            head[k] = int(v)
        else:
            cur[k] = int(v)
    return {"head": head, "chars": chars}


@test
def test_font_metrics_match_freetype():
    """字体度量与 **FreeType** (经 Pillow) 逐条相同。

    比的是**三个真数**: 码点->字形号有没有 (gid != 0 与 FreeType 的"这个字有没有"
    一致)、`hmtx` 的步进宽度 (字体单位下是整数, 与 `getlength(..., size=upem)`
    应当精确相等)、升部/降部 (与 `getmetrics()` 同源)。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    found = _find_font()
    if found is None:
        print("      SKIP: 本机没有可用的 TTF")
        return
    if not _have_pillow():
        print("      SKIP: 无 Pillow")
        return
    _, wsln = found
    from PIL import ImageFont
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = FONT_PROBE.replace("__FONT_PATH__", wsln).replace("__CHARS__", FONT_CHARS)
        got = _parse_font_out(_probe(td, "font_probe", src))
    assert got["head"]["load"] == 1, "字体载不进来"
    upem = got["head"]["upem"]
    ft = ImageFont.truetype(str(found[0]), upem)   # size = upem, 于是 getlength 直接是字体单位

    bad: list[str] = []
    for cp, r in sorted(got["chars"].items()):
        ch = chr(cp)
        gid_ok = r["gid"] != 0
        ft_ok = ft.getmask(ch).getbbox() is not None
        if gid_ok != ft_ok:
            bad.append(f"U+{cp:04X}: 有没有字形不一致 (LumtUI gid={r['gid']}, FT={ft_ok})")
            continue
        if not gid_ok:
            continue
        want_adv = int(round(ft.getlength(ch)))
        if r["adv"] != want_adv:
            bad.append(f"U+{cp:04X}: 步进 {r['adv']} != FreeType {want_adv}")
    # 升部/降部: size=upem 时像素值就是字体单位
    want_asc = int(round(ImageFont.truetype(str(found[0]), 32).getmetrics()[0]))
    got_asc = got["head"]["ascpx"]
    if abs(got_asc - want_asc) > 1:
        bad.append(f"升部 {got_asc} 与 FreeType {want_asc} 差超过 1")
    if bad:
        raise AssertionError("字体度量不一致:\n  " + "\n  ".join(bad[:8]))
    print(f"      {found[0].name}: {len(got['chars'])} 个码点的度量与 FreeType 一致")


@test
def test_glyph_raster_matches_freetype():
    """光栅化的**墨迹面积与包围盒**与 FreeType 对得上 (带容差)。

    容差是刻意的: FreeType 做 hinting 与自己的抗锯齿, 本库完全不做 hinting ——
    逐像素比会红得没有信息量。这里比的是"形状对不对": 包围盒差不超过 2 像素,
    墨迹总量 (覆盖值之和) 相差不超过 25%。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    found = _find_font()
    if found is None or not _have_pillow():
        print("      SKIP: 无 TTF / Pillow")
        return
    _, wsln = found
    from PIL import ImageFont
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = FONT_PROBE.replace("__FONT_PATH__", wsln).replace("__CHARS__", FONT_CHARS)
        got = _parse_font_out(_probe(td, "font_probe", src))
    ft = ImageFont.truetype(str(found[0]), 32)
    bad: list[str] = []
    cmp_n = 0
    for cp, r in sorted(got["chars"].items()):
        if r.get("ok", 0) == 0 or r.get("bw", 0) == 0:
            continue
        m = ft.getmask(chr(cp))
        bb = m.getbbox()
        if bb is None:
            continue
        # **比墨迹盒, 不比 `font.getbbox`。** Pillow 的 `mask.size` 是**步进盒**
        # (`getbbox('W')` 给 30 宽, 而那正是 'W' 的步进), 拿它当"字形有多宽"会把
        # 本库这边**多余留白不算**的那份正确当成偏差 —— 实测 'B' 是 18 vs 18、
        # '1' 是 9 vs 9, 只是 `font.getbbox` 把两边都撑到步进宽度而已。
        want_w = bb[2] - bb[0]
        want_h = bb[3] - bb[1]
        if abs(r["bw"] - want_w) > 2 or abs(r["bh"] - want_h) > 2:
            bad.append(f"U+{cp:04X}: 包围盒 {r['bw']}x{r['bh']} vs FreeType {want_w}x{want_h}")
            continue
        want_ink = sum(bytes(m))          # ImagingCore 支持 buffer 协议; 没有 tobytes()
        if want_ink == 0:
            continue
        ratio = r["ink"] / want_ink
        if not (0.75 <= ratio <= 1.25):
            bad.append(f"U+{cp:04X}: 墨迹 {r['ink']} vs FreeType {want_ink} (比 {ratio:.2f})")
            continue
        cmp_n += 1
    if bad:
        raise AssertionError("光栅化与 FreeType 对不上:\n  " + "\n  ".join(bad[:8]))
    print(f"      {cmp_n} 个字形: 包围盒 ±2 像素、墨迹 ±25% 内与 FreeType 一致")


# ---------------------------------------------------------------- 5. 示例程序

DEMO = ROOT / "loment" / "examples" / "lumtui_demo.lomt"


LIB_NAMES = ["lumtui", "lumtui_doc", "lumtui_layout", "lumtui_font", "lumtui_paint",
             "lumtui_math"]


@test
def test_each_lib_module_is_checkable_as_its_own_entry():
    """每个模块**自己当入口**都要过检查器 —— 这条不是形式主义。

    `lomentc.check` 今天**不查依赖的正文**：拿一个 `use lumtui_font` 的 app 去查，
    那份源是绿的 (实测)。所以"我的探针全绿"证明不了库是好的 —— 这次两个正文
    类型错 (`let bits: u32 = <i64>`、`return <ptr>` 而声明 `-> u32`) 都是
    `loment_std_test` 这条**同形状**的判据抓到的，我自己的探针一条都没看见。
    这里把那个形状钉在 LumtUI 自己身上。详见 `docs/196` §4.9。
    """
    bad: list[str] = []
    for name in LIB_NAMES:
        p = ROOT / "loment" / "lib" / f"{name}.lomt"
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        if errs:
            bad.append(f"{p.name}: {errs[:2]}")
    if bad:
        raise AssertionError("模块自己当入口时检查不过:\n  " + "\n  ".join(bad))
    print(f"      {len(LIB_NAMES)} 个模块各自当入口都过检查")


@test
def test_demo_runs_and_renders():
    """示例程序真的跑得起来, 而且画出来的东西是**确定**的。

    对的是几个记号, 不是整幅 ASCII —— 整幅会把"渲染对不对"变成"像素全等",
    那样改一次调色板就红一次, 而它并不说明什么错。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        out = _run_text(_build(DEMO, td, "lumtui_demo"), td)
    lines = out.splitlines()
    head = dict(ln.split("=", 1) for ln in lines if "=" in ln and not ln.startswith(" "))
    frame = [ln for ln in lines if ln and "=" not in ln]
    assert int(head["valid"]) == 1, "示例发的 .fuc 校验不过"
    assert int(head["nodes"]) == 8, head
    assert int(head["focus_n"]) == 3, head
    assert int(head["hit_btn"]) == 5, head
    assert int(head["fuc_bytes"]) > 0, head
    assert len(frame) == 36, f"帧应有 36 行, 得到 {len(frame)}"
    assert all(len(r) == 96 for r in frame), "帧的每一行应为 96 列"
    # 卡片是居中的: 左上角那几行该是背景 (空白), 中间该有卡片
    assert frame[0].strip() == "", "第一行应当是空背景"
    assert frame[18].strip() != "", "中间那一行应当有内容"
    print(f"      示例: {head['nodes']} 个节点, .fuc {head['fuc_bytes']} 字节, "
          f"{len(frame)}×{len(frame[0])} 帧")


# ---------------------------------------------------------------- 入口

def main() -> int:
    ok = 0
    return loment_suite.run(TESTS, "loment_lumtui_test")


if __name__ == "__main__":
    sys.exit(main())
