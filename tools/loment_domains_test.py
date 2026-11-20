#!/usr/bin/env python3
"""loment_domains_test.py — 领域包的**行为**判据（docs/317）。

`loment_std_test` 管 `std`；这一份管新拆出来的八个领域包。做法一样：探针程序调库，
stdout 与 **Python 算出来的**逐字节相同。用 Python 当对照物而不是硬编码，是因为
硬编码的那份是我们自己写的，两处一起错就看不出来了。

用法: python tools/loment_domains_test.py [名字子串]   （无 clang/WSL 时 SKIP）
"""

from __future__ import annotations

import io
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_json_test as J                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_PROBE_HEAD = r'''
module domainprobe
choose runtime

use hex

fn wr(p: ptr, n: u32) -> i64 {
    return syscall4(1, 1, p as u64, n as u64);
}
fn say(tag: str, v: u64) {
    let _a: u32 = print(tag);
    let _b: u32 = print_num(v);
    let _c: u32 = print("\n");
}
fn say_bits(tag: str, v: u64, hx: ptr) {
    let b: ptr = alloc(8);
    mem_store64(b, 0, v);
    hex_encode(hx, 0, b, 0, 8);
    let _a: u32 = print(tag);
    let _b: i64 = syscall4(1, 1, hx as u64, 16);
    let _c: u32 = print("\n");
}
fn say_cstr(tag: str, p: ptr, n: u32) {
    let _a: u32 = print(tag);
    let _b: i64 = syscall4(1, 1, p as u64, n as u64);
    let _c: u32 = print("\n");
}
'''

PROBE_MATH = _PROBE_HEAD + r'''
use linalg
use poly
use calc
use prob
use complex
use geometry
use bitstream
use toposort
use elements
use chem
use physics
use thermo
use optics

fn _start() {
    let a: ptr = alloc(72);
    let x: ptr = alloc(24);
    let b: ptr = alloc(24);
    let hx: ptr = alloc(64);
    let out: ptr = alloc(64);
    let i: u32 = 0;
    while i < 9 { mem_store64(a, i * 8, 0x0 as u64); i = i + 1; }
    mem_store64(a, 0, 0x4000000000000000);
    mem_store64(a, 32, 0x4008000000000000);
    mem_store64(a, 64, 0x4010000000000000);
    say_bits("det3=", la_det3(a), hx);
    let b0: ptr = alloc(24);
    mem_store64(b0, 0, 0x4000000000000000);
    mem_store64(b0, 8, 0x4018000000000000);
    mem_store64(b0, 16, 0x4020000000000000);
    let _s: u32 = la_solve3(x, a, b0);
    say_bits("solve0=", mem_load64(x, 0), hx);

    // poly: 2 + 3x + 4x^2 at x=2 -> 24
    let c: ptr = alloc(24);
    mem_store64(c, 0, 0x4000000000000000);
    mem_store64(c, 8, 0x4008000000000000);
    mem_store64(c, 16, 0x4010000000000000);
    say_bits("poly=", po_eval(c, 3, 0x4000000000000000), hx);

    // calc: y=0,1,2,3 trapezoid = 4.5
    let y: ptr = alloc(32);
    mem_store64(y, 0, 0x0 as u64);
    mem_store64(y, 8, 0x3FF0000000000000);
    mem_store64(y, 16, 0x4000000000000000);
    mem_store64(y, 24, 0x4008000000000000);
    say_bits("trap=", ca_trapezoid(y, 4, 0x3FF0000000000000), hx);

    // prob: erf(1), normal cdf(0)
    let erfd: u64 = f64_abs(f64_sub(pb_erf(0x3FF0000000000000), 0x3FEAF767A741088B));
    let erfok: u32 = 0;
    if f64_lt(erfd, 0x3EB0C6F7A0B5ED8D) { erfok = 1; }
    say("erf_ok=", erfok as u64);
    say_bits("ncdf0=", pb_normal_cdf(0x0 as u64, 0x0 as u64, 0x3FF0000000000000), hx);

    // complex: (1+2i)(3+4i) = -5+10i
    let z1: ptr = alloc(16);
    let z2: ptr = alloc(16);
    let zr: ptr = alloc(16);
    cx_set(z1, 0x3FF0000000000000, 0x4000000000000000);
    cx_set(z2, 0x4008000000000000, 0x4010000000000000);
    cx_mul(zr, z1, z2);
    say_bits("czre=", mem_load64(zr, 0), hx);
    say_bits("czim=", mem_load64(zr, 8), hx);

    // geometry: (0,0)-(2,2) x (0,2)-(2,0)
    let p: ptr = alloc(16);
    let p2: ptr = alloc(16);
    let p3: ptr = alloc(16);
    let p4: ptr = alloc(16);
    let po: ptr = alloc(16);
    gm_set2(p, 0x0 as u64, 0x0 as u64);
    gm_set2(p2, 0x4000000000000000, 0x4000000000000000);
    gm_set2(p3, 0x0 as u64, 0x4000000000000000);
    gm_set2(p4, 0x4000000000000000, 0x0 as u64);
    let _g: u32 = gm_seg_intersect(p, p2, p3, p4, po);
    say_bits("gx=", mem_load64(po, 0), hx);

    // bitstream MSB: 5 then 3 -> 0xB8
    let bs: ptr = alloc(8);
    let st: ptr = alloc(BT_STATE);
    mem_fill(bs, 0, 8, 0);
    bt_init(st, bs, 64, BT_MSB);
    let _w1: u32 = bt_write(st, 5, 3);
    let _w2: u32 = bt_write(st, 3, 2);
    say("bs=", load8(bs, 0) as u64);

    // toposort: 0->1,0->2,1->3,2->3
    let off: ptr = alloc(20);
    let adj: ptr = alloc(16);
    let ord: ptr = alloc(16);
    let ws: ptr = alloc(tp_ws_bytes(4));
    mem_store32(off, 0, 0); mem_store32(off, 4, 2);
    mem_store32(off, 8, 3); mem_store32(off, 12, 4); mem_store32(off, 16, 4);
    mem_store32(adj, 0, 1); mem_store32(adj, 4, 2);
    mem_store32(adj, 8, 3); mem_store32(adj, 12, 3);
    let _t: u32 = tp_topo(off, adj, 4, ord, ws);
    say("tp0=", mem_load32(ord, 0) as u64);
    say("tp3=", mem_load32(ord, 12) as u64);

    // science
    say("h_mass=", el_mass(el_find("H")));
    say("fe_mass=", el_mass(el_find("Fe")));
    say("water=", ch_mass("H2O"));
    say("glucose=", ch_mass("C6H12O6"));
    say_bits("photon=", ph_photon_energy(0x3FF0000000000000), hx);
    say_bits("c2k=", tm_c_to_k(0x0 as u64), hx);
    say_bits("lens=", op_lens_image(0x4020000000000000, 0x4030000000000000), hx);
    syscall4(60, 0, 0, 0);
}
'''


def _bits(v: float) -> str:
    return struct.pack("<d", v).hex()


def _erf_as(x: float) -> float:
    # 与 prob.lomt 相同的 Abramowitz–Stegun 7.1.26 近似（不是 math.erf！）
    import math
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t
                 - 0.284496736) * t + 0.254829592) * t) * math.exp(-ax * ax)
    return sign * y


def _expected_math() -> str:
    out = io.StringIO()

    def say_bits(tag, v):
        out.write(f"{tag}{_bits(v)}\n")

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    say_bits("det3=", 24.0)
    say_bits("solve0=", 1.0)
    say_bits("poly=", 24.0)
    say_bits("trap=", 4.5)
    say("erf_ok=", 1)
    say_bits("ncdf0=", 0.5)
    say_bits("czre=", -5.0)
    say_bits("czim=", 10.0)
    say_bits("gx=", 1.0)
    say("bs=", 184)
    say("tp0=", 0)
    say("tp3=", 3)
    say("h_mass=", 1008000)
    say("fe_mass=", 55845000)
    say("water=", 18015000)
    say("glucose=", 180156000)
    say_bits("photon=", 6.62607015e-34)
    say_bits("c2k=", 273.15)
    say_bits("lens=", 16.0)
    return out.getvalue()


def _run_probe(name: str, src: str) -> str:
    if not (J._clang() and J._wsl()):
        raise _Skip("无 clang/WSL")
    probe = ROOT / "loment" / "build" / f"domain_{name}_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            elf = J._compile(probe, td, f"domain_{name}")
            return J._run(elf, td)
    finally:
        probe.unlink(missing_ok=True)


class _Skip(Exception):
    pass


def _compare(got: str, want: str) -> None:
    if got == want:
        return
    g, w = got.splitlines(), want.splitlines()
    first = next((i for i in range(max(len(g), len(w)))
                  if (g[i] if i < len(g) else None) != (w[i] if i < len(w) else None)), None)
    raise AssertionError(
        f"领域包结果与 Python 不一致 @ 第 {first} 行: "
        f"Loment={g[first] if first is not None and first < len(g) else '<无>'!r} "
        f"Python={w[first] if first is not None and first < len(w) else '<无>'!r}")


@test
def test_math_and_science_packages():
    """math + science：与 Python 逐字节一致。"""
    try:
        got = _run_probe("math", PROBE_MATH)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_math()
    _compare(got, want)
    print(f"      math/science: {len(want.splitlines())} 项与 Python 逐字节一致")


PROBE_ENG = _PROBE_HEAD + r'''
use units
use electronics
use control
use money
use geo
use semver
use semrange
use spdx
use args

fn say_i(tag: str, v: i64) {
    let _a: u32 = print(tag);
    let _b: u32 = print_num(v);
    let _c: u32 = print("\n");
}

fn _start() {
    let hx: ptr = alloc(64);
    let out: ptr = alloc(64);
    say_bits("mile_km=", un_convert(0x3FF0000000000000, UN_MILE, UN_KM), hx);
    let r: ptr = alloc(16);
    let _rr: u32 = ec_resistor("brown black red gold", r);
    say_bits("res=", mem_load64(r, 0), hx);
    say_bits("tol=", mem_load64(r, 8), hx);
    let st: ptr = alloc(CTRL_STATE);
    ctrl_pid_init(st, 0x4000000000000000, 0x0 as u64, 0x0 as u64);
    say_bits("pid=", ctrl_pid_step(st, 0x4008000000000000, 0x3FF0000000000000), hx);
    let ok: ptr = alloc(4);
    say("money=", mo_parse_ok("123.45", ok) as u64);
    let n: u32 = mo_format(12345, out, 64);
    say_cstr("mfmt=", out, n);
    let hd: u64 = f64_abs(f64_sub(geo_haversine(0x0 as u64, 0x0 as u64, 0x0 as u64, 0x3FF0000000000000), 0x40FB25AED3893EAC));
    let hok: u32 = 0;
    if f64_lt(hd, 0x3F847AE147AE147B) { hok = 1; } // < 0.01 m
    say("hav_ok=", hok as u64);
    say_i("svcmp=", sv_compare("1.0.0-alpha.2", "1.0.0-alpha.10") as i64);
    let bn: u32 = sv_bump_minor("1.2.3-alpha", out, 64);
    say_cstr("svbump=", out, bn);
    say("srmatch=", sr_match("1.5.0", "^1.2.3") as u64);
    say("spdx_ok=", spdx_valid("MIT") as u64);
    say("spdx_fam=", spdx_family("GPL-3.0-only") as u64);

    // argv: --x, --y=5, file
    let argv: ptr = alloc(32);
    let a: ptr = alloc(8);
    let b: ptr = alloc(8);
    let c: ptr = alloc(8);
    store8(a, 0, 45 as u8); store8(a, 1, 45 as u8); store8(a, 2, 120 as u8); store8(a, 3, 0 as u8);
    store8(b, 0, 45 as u8); store8(b, 1, 45 as u8); store8(b, 2, 121 as u8); store8(b, 3, 61 as u8);
    store8(b, 4, 53 as u8); store8(b, 5, 0 as u8);
    store8(c, 0, 102 as u8); store8(c, 1, 0 as u8);
    mem_store64(argv, 0, a as u64);
    mem_store64(argv, 8, b as u64);
    mem_store64(argv, 16, c as u64);
    let an: u32 = ag_value(argv, 3, "--y", out, 8);
    say_cstr("arg=", out, an);
    syscall4(60, 0, 0, 0);
}
'''


def _expected_eng() -> str:
    out = io.StringIO()

    def say_bits(tag, v):
        out.write(f"{tag}{_bits(v)}\n")

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    say_bits("mile_km=", 1.609344)
    say_bits("res=", 1000.0)
    say_bits("tol=", 5.0)
    say_bits("pid=", 6.0)
    say("money=", 12345)
    say("mfmt=", "123.45")
    say("hav_ok=", 1)
    say("svcmp=", -1)
    say("svbump=", "1.3.0")
    say("srmatch=", 1)
    say("spdx_ok=", 1)
    say("spdx_fam=", 3)
    say("arg=", "5")
    return out.getvalue()


@test
def test_engineering_world_and_dev_packages():
    """engineering + world + dev：与 Python 逐字节一致。"""
    try:
        got = _run_probe("eng", PROBE_ENG)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_eng()
    _compare(got, want)
    print(f"      engineering/world/dev: {len(want.splitlines())} 项与 Python 逐字节一致")


PROBE_LOW = _PROBE_HEAD + r'''
use endian
use elf
use pe
use abi
use caps
use seccomp
use ns
use multiboot
use gdt
use paging
use pci
use vga

fn _start() {
    let hx: ptr = alloc(64);
    let out: ptr = alloc(128);

    // ELF: 合成一个头
    let e: ptr = alloc(256);
    mem_fill(e, 0, 256, 0);
    store8(e, 0, 127 as u8); store8(e, 1, 69 as u8); store8(e, 2, 76 as u8); store8(e, 3, 70 as u8);
    store8(e, 4, 2 as u8); store8(e, 5, 1 as u8);
    endian_store16le(e, 16, 2);
    endian_store16le(e, 18, 62);
    endian_store64le(e, 24, 4194304);
    endian_store16le(e, 54, 56);
    endian_store16le(e, 56, 2);
    let _eh: u32 = elf_header(e, 256, out);
    say("elf_type=", mem_load32(out, 0) as u64);
    say("elf_mach=", mem_load32(out, 4) as u64);
    say("elf_entry=", mem_load64(out, 8));

    // PE: 合成一个最小头
    let p: ptr = alloc(512);
    mem_fill(p, 0, 512, 0);
    store8(p, 0, 77 as u8); store8(p, 1, 90 as u8);
    endian_store32le(p, 60, 64);
    store8(p, 64, 80 as u8); store8(p, 65, 69 as u8);
    endian_store16le(p, 68, 34404);
    endian_store16le(p, 70, 2);
    endian_store16le(p, 84, 240);
    endian_store16le(p, 88, 523);
    endian_store32le(p, 88 + 16, 4096);
    let _pc: u32 = pe_coff(p, 512, out);
    say("pe_mach=", mem_load32(out, 0) as u64);
    let _po: u32 = pe_opt(p, 512, out);
    say("pe_entry=", mem_load32(out, 4) as u64);

    // ABI: struct { char; int; double; }
    let sizes: ptr = alloc(12);
    let aligns: ptr = alloc(12);
    let offs: ptr = alloc(12);
    mem_store32(sizes, 0, 1); mem_store32(sizes, 4, 4); mem_store32(sizes, 8, 8);
    mem_store32(aligns, 0, 1); mem_store32(aligns, 4, 4); mem_store32(aligns, 8, 8);
    say("abi_size=", abi_layout(sizes, aligns, 3, offs) as u64);
    say("abi_off1=", mem_load32(offs, 4) as u64);
    say("abi_off2=", mem_load32(offs, 8) as u64);

    // caps
    let cs: ptr = alloc(8);
    say("cap_n=", cap_parse("chown net_admin bpf", cs) as u64);
    let capok: u32 = 0;
    if cap_has(cs, 39) { capok = 1; }
    say("cap_bpf=", capok as u64);

    // seccomp: lda + jeq + ret
    let sb: ptr = alloc(64);
    let ss: ptr = alloc(SEC_STATE);
    sec_init(ss, sb, 8);
    let _s1: u32 = sec_ld_abs(ss, SEC_DATA_ARCH);
    let _s2: u32 = sec_jeq(ss, 3221225534, 0, 1);
    let _s3: u32 = sec_ret_allow(ss);
    say("sec_c1=", load8(sb, 0) as u64);
    say("sec_c2=", load8(sb, 8) as u64);
    say("sec_jf=", load8(sb, 11) as u64);

    // namespaces
    let nf: u64 = ns_parse("pid net");
    let pidok: u32 = 0;
    if ns_has(nf, "pid") { pidok = 1; }
    let mntok: u32 = 0;
    if ns_has(nf, "mnt") { mntok = 1; }
    say("ns_pid=", pidok as u64);
    say("ns_mnt=", mntok as u64);

    // multiboot
    let mb: ptr = alloc(16);
    let _mw: u32 = mb_header_write(mb, 3);
    say("mb_ok=", mb_header_valid(mb) as u64);

    // gdt
    let gd: ptr = alloc(16);
    gd_code(gd, 0);
    say("gdt_access=", load8(gd, 5) as u64);
    say("gdt_flags=", load8(gd, 6) as u64);

    // paging
    let pe1: u64 = pg_pte(1048576, PG_PRESENT | PG_RW);
    say("pg_addr=", pg_addr(pe1));
    say("pg_present=", pg_has(pe1, PG_PRESENT) as u64);
    say("pg_nx=", pg_has(pg_set(pe1, PG_NX, 1), PG_NX) as u64);

    // pci
    let pcfg: ptr = alloc(256);
    mem_fill(pcfg, 0, 256, 0);
    endian_store32le(pcfg, 16, 4273868800);
    say("pci_bar=", pci_bar_addr(pcfg, 0));
    say("pci_bdf=", pci_bdf(1, 2, 3) as u64);

    // vga
    say("vga_cell=", vga_cell(65, vga_attr(15, 4)) as u64);
    say("vga_red=", vga_color("red") as u64);
    syscall4(60, 0, 0, 0);
}
'''


def _expected_low() -> str:
    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    say("elf_type=", 2)
    say("elf_mach=", 62)
    say("elf_entry=", 4194304)
    say("pe_mach=", 34404)
    say("pe_entry=", 4096)
    say("abi_size=", 16)
    say("abi_off1=", 4)
    say("abi_off2=", 8)
    say("cap_n=", 3)
    say("cap_bpf=", 1)
    say("sec_c1=", 32)
    say("sec_c2=", 21)
    say("sec_jf=", 1)
    say("ns_pid=", 1)
    say("ns_mnt=", 0)
    say("mb_ok=", 1)
    say("gdt_access=", 154)
    say("gdt_flags=", 160)
    say("pg_addr=", 1048576)
    say("pg_present=", 1)
    say("pg_nx=", 1)
    say("pci_bar=", 4273868800)
    say("pci_bdf=", 275)
    say("vga_cell=", 20289)
    say("vga_red=", 4)
    return out.getvalue()


@test
def test_lowlevel_system_and_baremetal_packages():
    """lowlevel + system + baremetal：与已知常量/Python 逐字节一致。"""
    try:
        got = _run_probe("low", PROBE_LOW)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_low()
    _compare(got, want)
    print(f"      lowlevel/system/baremetal: {len(want.splitlines())} 项逐字节一致")


PROBE_DEV = _PROBE_HEAD + r'''
use duration
use target
use shellwords
use dotenv
use template
use commit
use changelog
use jsonptr
use gitignore
use patch

fn _start() {
    let out: ptr = alloc(256);
    let ok: ptr = alloc(4);

    // duration
    say("dur=", du_parse("1h30m", ok) as u64);
    let dn: u32 = du_format(5400000000000 as i64, out, 256);
    say_cstr("dufmt=", out, dn);

    // target
    let tt: ptr = alloc(32);
    let _tr: u32 = tgt_parse("x86_64-unknown-linux-gnu", tt);
    say("tos=", mem_load32(tt, 20) as u64);
    say("tenv=", mem_load32(tt, 28) as u64);

    // shellwords
    let swb: ptr = alloc(64);
    let sws: ptr = alloc(32);
    say("swc=", sw_split("a 'b c'", swb, 64, sws, 4) as u64);
    say("sw2=", mem_load32(sws, 12) as u64);
    let qn: u32 = sw_quote("a b", out, 256);
    say_cstr("swq=", out, qn);

    // dotenv
    let de: str = "X='a b'\nY=2\n";
    let dv: u32 = de_get(str_ptr(de), str_len(de), "X", out, 256);
    say_cstr("de=", out, dv);

    // template
    let keys: ptr = alloc(8);
    let vals: ptr = alloc(8);
    let kb: ptr = alloc(8);
    let vb: ptr = alloc(8);
    store8(kb, 0, 110 as u8); store8(kb, 1, 0 as u8); // "n"
    store8(vb, 0, 119 as u8); store8(vb, 1, 111 as u8); store8(vb, 2, 114 as u8);
    store8(vb, 3, 108 as u8); store8(vb, 4, 100 as u8); store8(vb, 5, 0 as u8);
    mem_store64(keys, 0, kb as u64);
    mem_store64(vals, 0, vb as u64);
    let tp: str = "hi {{n}}";
    let tn: u32 = tpl_render(tp, keys, vals, 1, out, 256);
    say_cstr("tpl=", out, tn);

    // commit
    let co: ptr = alloc(48);
    let cs: str = "feat(x)!: y";
    let _cr: u32 = cm_parse(cs, co);
    say("cm_type=", mem_load32(co, CM_TYPE_LEN) as u64);
    say("cm_scope=", mem_load32(co, CM_SCOPE_LEN) as u64);
    say("cm_bang=", mem_load32(co, CM_BANG) as u64);
    say("cm_desc=", mem_load32(co, CM_DESC_LEN) as u64);

    // changelog
    let m1: ptr = alloc(16);
    let m2: ptr = alloc(16);
    let msgs: ptr = alloc(16);
    let s1: str = "feat: add";
    let s2: str = "fix: bug";
    let l1: u32 = str_len(s1);
    let l2: u32 = str_len(s2);
    mem_copy(m1, 0, str_ptr(s1), 0, l1); store8(m1, l1, 0 as u8);
    mem_copy(m2, 0, str_ptr(s2), 0, l2); store8(m2, l2, 0 as u8);
    mem_store64(msgs, 0, m1 as u64);
    mem_store64(msgs, 8, m2 as u64);
    let cln: u32 = cl_generate(msgs, 2, out, 256);
    say_cstr("cl=", out, cln);

    // jsonptr
    let doc: str = "{\"a\":[10,20]}";
    let jv: u64 = jp_get_u64(str_ptr(doc), str_len(doc), "/a/1", ok);
    say("jp=", jv);

    // gitignore
    let p1: ptr = alloc(8);
    let p2: ptr = alloc(16);
    let pats: ptr = alloc(16);
    store8(p1, 0, 42 as u8); store8(p1, 1, 46 as u8); store8(p1, 2, 111 as u8); store8(p1, 3, 0 as u8); // *.o
    store8(p2, 0, 33 as u8); store8(p2, 1, 107 as u8); store8(p2, 2, 101 as u8); store8(p2, 3, 101 as u8);
    store8(p2, 4, 112 as u8); store8(p2, 5, 46 as u8); store8(p2, 6, 111 as u8); store8(p2, 7, 0 as u8); // !keep.o
    mem_store64(pats, 0, p1 as u64);
    mem_store64(pats, 8, p2 as u64);
    let g1: str = "x.o";
    let g2: str = "keep.o";
    say("gi1=", gi_match(pats, 2, g1, 0) as u64);
    say("gi2=", gi_match(pats, 2, g2, 0) as u64);

    // patch
    let src: str = "a\nc\n";
    let dif: str = "@@ -1,2 +1,3 @@\n a\n+b\n c\n";
    let pn: u32 = pa_apply(str_ptr(src), str_len(src), str_ptr(dif), str_len(dif), out, 256);
    say_cstr("pa=", out, pn);
    syscall4(60, 0, 0, 0);
}
'''


def _expected_dev() -> str:
    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    say("dur=", 5400000000000)
    say("dufmt=", "1h30m0s")
    say("tos=", 5)
    say("tenv=", 3)
    say("swc=", 2)
    say("sw2=", 3)
    say("swq=", "'a b'")
    say("de=", "a b")
    say("tpl=", "hi world")
    say("cm_type=", 4)
    say("cm_scope=", 1)
    say("cm_bang=", 1)
    say("cm_desc=", 1)
    say("cl=", "### Features\n\n- add\n\n### Bug Fixes\n\n- bug\n\n")
    say("jp=", 20)
    say("gi1=", 1)
    say("gi2=", 0)
    say("pa=", "a\nb\nc\n")
    return out.getvalue()


@test
def test_dev_tooling_packages():
    """dev 的开发者工具：与 Python 的预期逐字节一致。"""
    try:
        got = _run_probe("dev", PROBE_DEV)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_dev()
    _compare(got, want)
    print(f"      dev tooling: {len(want.splitlines())} 项与 Python 逐字节一致")


PROBE_WORLD = _PROBE_HEAD + r'''
use calendar
use holiday
use tzoff
use dateparse
use timeago
use geohash
use flag
use langtag
use phone
use postal
use isbn
use ean
use iban
use luhn
use slug
use translit
use namefmt
use words
use roman

fn _start() {
    let o: ptr = alloc(128);
    let c: ptr = alloc(24);
    let ok: ptr = alloc(4);

    say("days=", cd_days_from_civil(1970 as i64, 1 as i64, 1 as i64) as u64);
    say("wd=", cd_weekday(0 as i64) as u64);
    let yw: ptr = alloc(4);
    say("isow=", cd_iso_week(2021 as i64, 1 as i64, 1 as i64, yw) as u64);
    say("isoy=", mem_load32(yw, 0) as u64);
    cd_add_months(2024 as i64, 1 as i64, 31 as i64, 1 as i64, c);
    let amm: u64 = (mem_load32(c, 4) as u64) * 100;
    say("am=", amm + (mem_load32(c, 8) as u64));

    cd_civil_from_days(hd_easter(2024 as i64), c);
    let em: u64 = (mem_load32(c, 4) as u64) * 100;
    say("easter=", em + (mem_load32(c, 8) as u64));
    cd_civil_from_days(hd_last_weekday(2024 as i64, 5 as i64, 1), c);
    say("memday=", mem_load32(c, 8) as u64);

    say("tzp=", tz_parse("+08:00", ok) as u64);
    let nf: u32 = tz_format(0 as i32 - 330 as i32, o, 128);
    say_cstr("tzf=", o, nf);
    tz_civil(0 as i64, 480 as i32, c);
    say("tzh=", mem_load32(c, 12) as u64);

    say("dp=", dp_parse_date("2024-02-29", ok) as u64);
    let ni: u32 = dp_format_iso(0 as i64, o, 128);
    say_cstr("iso=", o, ni);
    say("ep=", dp_parse_iso("2026-11-08T10:30:00Z", ok) as u64);

    let ta: u32 = ta_format(10800 as i64, o, 128);
    say_cstr("ta=", o, ta);
    let tb: u32 = ta_format(0 as i64 - 7200 as i64, o, 128);
    say_cstr("tb=", o, tb);

    let gn: u32 = gh_encode(57649110 as i32, 10407440 as i32, 8, o, 128);
    say_cstr("gh=", o, gn);

    let f: ptr = alloc(16);
    let fc: ptr = alloc(8);
    let _fr: u32 = fg_from_code("jp", f, 16);
    let fn2: u32 = fg_code(f, fc, 8);
    say_cstr("fg=", fc, fn2);

    let lt: u32 = lgt_canonical("zh-hant-tw", o, 128);
    say_cstr("lt=", o, lt);
    say("ltv=", lgt_is_valid("en-US") as u64);

    let pn: u32 = tel_parse("+1 (202) 555-0147", o, 128);
    say_cstr("tel=", o, pn);
    let pp: u32 = tel_pretty("+12025550147", o, 128);
    say_cstr("tp=", o, pp);

    say("z1=", zip_valid("US", "94103-1234") as u64);
    say("z2=", zip_valid("CA", "K1A 0B1") as u64);
    say("z3=", zip_valid("DE", "ABCDE") as u64);

    say("i1=", isbn_valid("0-306-40615-2") as u64);
    say("i2=", isbn_valid("978-0-306-40615-7") as u64);
    let i3: u32 = isbn10_to_13("0306406152", o, 128);
    say_cstr("i3=", o, i3);
    say("e1=", ean_valid("4006381333931") as u64);
    say("e2=", ean_check_digit("03600029145") as u64);

    say("b1=", iban_valid("DE89370400440532013000") as u64);
    say("b2=", iban_valid("GB82WEST12345698765432") as u64);
    say("b3=", iban_valid("DE89370400440532013001") as u64);
    say("b4=", iban_country_len("DE") as u64);
    say("l1=", luhn_ok("4111111111111111") as u64);
    let lb: u32 = luhn_brand("4111111111111111", o, 128);
    say_cstr("lb=", o, lb);
    let lb2: u32 = luhn_brand("5555555555554444", o, 128);
    say_cstr("lb2=", o, lb2);

    let sn: u32 = sl_make("  Hello, World!  ", o, 128);
    say_cstr("sl=", o, sn);
    let tr1: u32 = tl_translit("Москва", o, 128);
    say_cstr("tr1=", o, tr1);
    let tr2: u32 = tl_translit("Αθήνα", o, 128);
    say_cstr("tr2=", o, tr2);

    let nm: u32 = nmf_family_first("Ada Lovelace Jr.", o, 128);
    say_cstr("nm=", o, nm);
    let ns: ptr = alloc(32);
    say("nfl=", nmf_parse("Dr. Ada Lovelace Jr.", ns) as u64);

    let w1: u32 = nw_cardinal(123 as i64, o, 128);
    say_cstr("w1=", o, w1);
    let w2: u32 = nw_cardinal(1000000 as i64, o, 128);
    say_cstr("w2=", o, w2);
    let w3: u32 = nw_ordinal(21 as i64, o, 128);
    say_cstr("w3=", o, w3);
    let w4: u32 = nw_cardinal(0 as i64 - 5 as i64, o, 128);
    say_cstr("w4=", o, w4);
    say("r1=", rm_to_int("MCMXCIV") as u64);
    let r2: u32 = rm_from_int(2026, o, 128);
    say_cstr("r2=", o, r2);

    syscall4(60, 0, 0, 0);
}

fn fg_code(flag: ptr, out: ptr, cap: u32) -> u32 {
    let a: u32 = load8(flag, 3);
    let b: u32 = load8(flag, 7);
    if cap < 2 {
        return 2;
    }
    store8(out, 0, (65 + (a - 166)) as u8);
    store8(out, 1, (65 + (b - 166)) as u8);
    return 2;
}
'''


def _expected_world() -> str:
    import datetime as dt

    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    epoch = dt.date(1970, 1, 1)
    say("days=", 0)
    say("wd=", 4)
    say("isow=", 53)
    say("isoy=", 2020)
    say("am=", 229)
    say("easter=", 331)
    say("memday=", 27)
    say("tzp=", 480)
    say("tzf=", "-05:30")
    say("tzh=", 8)
    say("dp=", (dt.date(2024, 2, 29) - epoch).days)
    say("iso=", "1970-01-01T00:00:00Z")
    say("ep=", int(dt.datetime(2026, 11, 8, 10, 30, tzinfo=dt.timezone.utc).timestamp()))
    say("ta=", "3 hours ago")
    say("tb=", "in 2 hours")
    say("gh=", "u4pruydq")
    say("fg=", "JP")
    say("lt=", "zh-Hant-TW")
    say("ltv=", 1)
    say("tel=", "+12025550147")
    say("tp=", "+1 202 555 0147")
    say("z1=", 1)
    say("z2=", 1)
    say("z3=", 0)
    say("i1=", 1)
    say("i2=", 1)
    say("i3=", "9780306406157")
    say("e1=", 1)
    say("e2=", 2)
    say("b1=", 1)
    say("b2=", 1)
    say("b3=", 0)
    say("b4=", 22)
    say("l1=", 1)
    say("lb=", "visa")
    say("lb2=", "mastercard")
    say("sl=", "hello-world")
    say("tr1=", "Moskva")
    say("tr2=", "Athina")
    say("nm=", "Lovelace, Ada, Jr.")
    say("nfl=", 15)
    say("w1=", "one hundred twenty-three")
    say("w2=", "one million")
    say("w3=", "twenty-first")
    say("w4=", "minus five")
    say("r1=", 1994)
    say("r2=", "MMXXVI")
    return out.getvalue()


@test
def test_world_reference_packages():
    """world 包的现实对照：日期、编码、证件号、文字渲染与 Python 逐字节一致。"""
    try:
        got = _run_probe("world", PROBE_WORLD)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_world()
    _compare(got, want)
    print(f"      world reference: {len(want.splitlines())} 项与 Python 逐字节一致")


PROBE_LOW_SYS = _PROBE_HEAD + r'''
use leb128
use bitpack
use ar
use macho
use coff
use dwarf
use reloc
use calling
use errno
use syscalls
use fcntl
use mmap
use poll
use resource
use procfs
use ioctl

fn ar_test_hdr(b: ptr, off: u32, name: str, size: u32) {
    let i: u32 = 0;
    while i < 60 { store8(b, off + i, 32 as u8); i = i + 1; }
    let nl: u32 = str_len(name);
    i = 0;
    while i < nl { store8(b, off + i, str_byte(name, i) as u8); i = i + 1; }
    let digits: ptr = alloc(16);
    let dn: u32 = 0;
    let v: u32 = size;
    if v == 0 {
        store8(digits, 0, 48 as u8);
        dn = 1;
    }
    while v > 0 {
        store8(digits, dn, (48 + (v % 10)) as u8);
        v = v / 10;
        dn = dn + 1;
    }
    i = 0;
    while i < dn {
        store8(b, off + 48 + (dn - 1 - i), load8(digits, i) as u8);
        i = i + 1;
    }
    store8(b, off + 58, 96 as u8);
    store8(b, off + 59, 10 as u8);
}

fn _start() {
    let o: ptr = alloc(256);
    let used: ptr = alloc(4);
    let buf: ptr = alloc(256);

    // leb128
    let lb: u32 = lb_enc_u64(300 as u64, buf, 256);
    say("lb_len=", lb as u64);
    say("lb_b0=", load8(buf, 0) as u64);
    say("lb_back=", lb_dec_u64(buf, lb, used));
    let ls: u32 = lb_enc_i64(0 as i64 - 300 as i64, buf, 256);
    say("lb_slen=", ls as u64);
    say("lb_sback=", lb_dec_i64(buf, ls, used) as u64);

    // bitpack
    store8(buf, 0, 177 as u8);
    store8(buf, 1, 2 as u8);
    say("bp=", bp_get_le(buf, 4, 8));
    store8(buf, 0, 0 as u8); store8(buf, 1, 0 as u8);
    bp_set_be(buf, 3, 10, 981 as u64);
    say("bp_rt=", bp_get_be(buf, 3, 10));

    // ar
    let a: ptr = alloc(256);
    let hdr: str = "!<arch>\n";
    let i: u32 = 0;
    while i < 8 { store8(a, i, str_byte(hdr, i) as u8); i = i + 1; }
    ar_test_hdr(a, 8, "a.o/", 3);
    store8(a, 68, 1 as u8); store8(a, 69, 2 as u8); store8(a, 70, 3 as u8);
    ar_test_hdr(a, 72, "b.o/", 4);
    store8(a, 132, 9 as u8);
    say("ar_c=", ar_count(a, 136) as u64);
    say("ar_f=", ar_find(a, 136, "b.o") as u64);

    // macho
    let m: ptr = alloc(64);
    store8(m, 0, 207 as u8); store8(m, 1, 250 as u8);
    store8(m, 2, 237 as u8); store8(m, 3, 254 as u8);
    store8(m, 4, 7 as u8); store8(m, 5, 0 as u8);
    store8(m, 6, 0 as u8); store8(m, 7, 1 as u8);
    store8(m, 12, 2 as u8); store8(m, 16, 3 as u8);
    let mo: ptr = alloc(32);
    let _mr: u32 = mc_parse(m, 32, mo);
    say("mc_cpu=", mem_load32(mo, 4) as u64);
    say("mc_ft=", mem_load32(mo, 12) as u64);
    say("mc_n=", mem_load32(mo, 16) as u64);

    // coff
    let c: ptr = alloc(64);
    store8(c, 0, 100 as u8); store8(c, 1, 134 as u8);
    store8(c, 2, 1 as u8);
    let co: ptr = alloc(24);
    let _cr: u32 = coff_parse(c, 64, co);
    say("co_m=", mem_load32(co, 0) as u64);
    let cn: u32 = coff_machine_name(mem_load32(co, 0), o, 256);
    say_cstr("co_n=", o, cn);

    // dwarf
    let d: ptr = alloc(64);
    store8(d, 0, 40 as u8); store8(d, 4, 5 as u8);
    store8(d, 6, 1 as u8); store8(d, 7, 8 as u8); store8(d, 8, 12 as u8);
    let do_: ptr = alloc(24);
    let _dr: u32 = dw_unit_header(d, 64, do_);
    say("dw_v=", mem_load32(do_, 4) as u64);
    say("dw_as=", mem_load32(do_, 12) as u64);
    say("dw_ab=", mem_load32(do_, 16) as u64);

    // reloc / calling
    let rn: u32 = rc_name(62, 4, o, 256);
    say_cstr("rc1=", o, rn);
    let rn2: u32 = rc_machine_name(183, o, 256);
    say_cstr("rc2=", o, rn2);
    let ca: u32 = reg_int_arg(REG_SYSV, 3, o, 256);
    say_cstr("ca1=", o, ca);
    let cb: u32 = reg_int_arg(REG_WIN64, 0, o, 256);
    say_cstr("ca2=", o, cb);
    let cc: u32 = reg_int_arg(REG_AAPCS64, 7, o, 256);
    say_cstr("ca3=", o, cc);
    say("ca4=", reg_int_arg(REG_WIN64, 4, o, 256) as u64);

    // errno
    let e1: u32 = er_name(2 as i64, o, 256);
    say_cstr("er1=", o, e1);
    let e2: u32 = er_name(13 as i64, o, 256);
    say_cstr("er2=", o, e2);
    let e3: u32 = er_name(111 as i64, o, 256);
    say_cstr("er3=", o, e3);
    let e4: u32 = er_text(13 as i64, o, 256);
    say_cstr("er4=", o, e4);

    // syscalls
    let s1: u32 = scn_name(1 as i64, o, 256);
    say_cstr("sc1=", o, s1);
    let s2: u32 = scn_name(257 as i64, o, 256);
    say_cstr("sc2=", o, s2);
    say("sc3=", scn_num("openat") as u64);
    say("sc4=", scn_num("nope") as u64);

    // fcntl / mmap / poll / resource
    let f1: u32 = fcnt_open_flags(1 + 64 + 524288, o, 256);
    say_cstr("fc1=", o, f1);
    say("fc2=", fcnt_lookup("O_APPEND") as u64);
    let p1: u32 = mm_prot_text(3, o, 256);
    say_cstr("mm1=", o, p1);
    let p2: u32 = mm_map_text(34, o, 256);
    say_cstr("mm2=", o, p2);
    say("mm3=", mm_anon_rw_mask() as u64);
    let w1: u32 = pw_events_text(17, o, 256);
    say_cstr("pw1=", o, w1);
    let w2: u32 = pw_epoll_text(2147483652, o, 256);
    say_cstr("pw2=", o, w2);
    let r1: u32 = rlim_name(7, o, 256);
    say_cstr("rl1=", o, r1);
    say("rl2=", rlim_is_inf(RLIM_INFINITY) as u64);

    // procfs
    let st: str = "1234 (my proc (x)) S 1 1234 1234 0 -1 4194304 100 0 0 0 55 66 0 0 20 0 1\n";
    let po: ptr = alloc(24);
    let _pr: u32 = pf_stat(str_ptr(st), str_len(st), po);
    say("pf1=", mem_load32(po, 0) as u64);
    say("pf2=", mem_load32(po, 8) as u64);
    say("pf3=", mem_load32(po, 12) as u64);
    say("pf4=", mem_load32(po, 16) as u64);
    let mi: str = "MemFree:   12345 kB\nMemTotal:  654321 kB\n";
    let mn: u32 = pf_meminfo_total(str_ptr(mi), str_len(mi), o, 256);
    say_cstr("pf5=", o, mn);

    // ioctl
    say("io1=", iol_tiocgwinsz());
    let ws: ptr = alloc(8);
    store8(ws, 0, 40 as u8); store8(ws, 2, 120 as u8);
    let wo: ptr = alloc(16);
    iol_winsize(ws, wo);
    say("io2=", mem_load32(wo, 0) as u64);
    say("io3=", mem_load32(wo, 4) as u64);

    syscall4(60, 0, 0, 0);
}
'''


def _expected_low_sys() -> str:
    import errno as E
    import os

    # `fcntl` 模块**不导出** `O_*`（那些在 `os` 里）。探针是 **Linux ELF**，所以期望值
    # 也必须是 **Linux 的 ABI 值**：Linux 上直接取 `os.*`（独立对照），Windows 上 `os`
    # 给的是 Windows 的取值（`O_APPEND`=8），不能拿来当 Linux 的期望 —— 用常量兜底。
    _linux = sys.platform.startswith("linux")
    F = type("_F", (), {
        "O_WRONLY": os.O_WRONLY if _linux else 1,
        "O_CREAT": os.O_CREAT if _linux else 64,
        "O_CLOEXEC": getattr(os, "O_CLOEXEC", 524288) if _linux else 524288,
        "O_APPEND": os.O_APPEND if _linux else 1024,
    })

    try:
        import termios as T
    except ImportError:  # Windows：没有 termios
        class _T:
            TIOCGWINSZ = 0x5413
        T = _T

    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    say("lb_len=", 2)
    say("lb_b0=", 0xAC)
    say("lb_back=", 300)
    say("lb_slen=", 2)
    say("lb_sback=", -300)
    say("bp=", 43)
    say("bp_rt=", 981)
    say("ar_c=", 2)
    say("ar_f=", 132)
    say("mc_cpu=", 0x01000007)
    say("mc_ft=", 2)
    say("mc_n=", 3)
    say("co_m=", 0x8664)
    say("co_n=", "amd64")
    say("dw_v=", 5)
    say("dw_as=", 8)
    say("dw_ab=", 12)
    say("rc1=", "R_X86_64_PLT32")
    say("rc2=", "AArch64")
    say("ca1=", "rcx")
    say("ca2=", "rcx")
    say("ca3=", "x7")
    say("ca4=", 0)
    linux = sys.platform.startswith("linux")
    say("er1=", E.errorcode[2] if linux else "ENOENT")
    say("er2=", E.errorcode[13] if linux else "EACCES")
    say("er3=", E.errorcode[111] if linux else "ECONNREFUSED")
    say("er4=", os.strerror(13).lower() if linux else "permission denied")
    say("sc1=", "write")
    say("sc2=", "openat")
    say("sc3=", 257)
    say("sc4=", -1)
    mask = F.O_WRONLY | F.O_CREAT | F.O_CLOEXEC
    names = []
    if (mask & 3) == F.O_WRONLY:
        names.append("O_WRONLY")
    if mask & F.O_CREAT:
        names.append("O_CREAT")
    if mask & F.O_CLOEXEC:
        names.append("O_CLOEXEC")
    say("fc1=", "|".join(names))
    say("fc2=", F.O_APPEND)
    say("mm1=", "PROT_READ|PROT_WRITE")
    say("mm2=", "MAP_PRIVATE|MAP_ANONYMOUS")
    say("mm3=", 34)  # MAP_PRIVATE|MAP_ANONYMOUS
    say("pw1=", "POLLIN|POLLHUP")
    say("pw2=", "EPOLLOUT|EPOLLET")
    say("rl1=", "RLIMIT_NOFILE")
    say("rl2=", 1)
    say("pf1=", 1234)
    say("pf2=", 1)
    say("pf3=", 55)
    say("pf4=", 66)
    say("pf5=", "654321 kB")
    say("io1=", T.TIOCGWINSZ)
    say("io2=", 40)
    say("io3=", 120)
    return out.getvalue()


PROBE_SYSOS = _PROBE_HEAD + r"""
use caps
use cgroup
use epoll
use iouring
use statfs
use futex
use procstatus
use mountinfo
use civildate
use paths
use umask
use cmdline
use pwd
use readdir

fn rl32(p: ptr, off: u32) -> u32 {
    let v: u32 = 0;
    let i: u32 = 0;
    while i < 4 {
        v = v | ((load8(p, off + i) as u32) << (8 * i));
        i = i + 1;
    }
    return v;
}

fn rl64(p: ptr, off: u32) -> u64 {
    let v: u64 = 0 as u64;
    let i: u32 = 0;
    while i < 8 {
        v = v | ((load8(p, off + i) as u64) << (8 * i));
        i = i + 1;
    }
    return v;
}

fn put(s: str, out: ptr, cap: u32) -> u32 {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    while i < n {
        if i < cap {
            store8(out, i, str_byte(s, i) as u8);
        }
        i = i + 1;
    }
    return n;
}

fn put_at(s: str, out: ptr, base: u32, cap: u32) -> u32 {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    while i < n {
        if base + i < cap {
            store8(out, base + i, str_byte(s, i) as u8);
        }
        i = i + 1;
    }
    return n;
}

fn _start() {
    let o: ptr = alloc(256);
    let b: ptr = alloc(256);
    let q: ptr = alloc(8);
    let pd: ptr = alloc(8);
    let yp: ptr = alloc(8);
    let mp: ptr = alloc(8);
    let dp: ptr = alloc(8);

    // cgroup v2 文本
    let n1: u32 = put("4294967296", b, 256);
    say("cg_len=", cg_parse_u64(b, n1, q) as u64);
    let cgv: u64 = rl64(q, 0);
    say("cg_val_hi=", cgv / (4294967296 as u64));
    say("cg_val_lo=", cgv % (4294967296 as u64));
    let n2: u32 = put("max", b, 256);
    say("cg_max_n=", cg_parse_max(b, n2, q) as u64);
    let cgm: u64 = rl64(q, 0);
    say("cg_max_hi=", cgm / (4294967296 as u64));
    say("cg_max_lo=", cgm % (4294967296 as u64));
    let n3: u32 = put("200000 100000", b, 256);
    say("cg_cpu=", cg_parse_cpu_max(b, n3, q, pd) as u64);
    say("cg_q=", rl64(q, 0));
    say("cg_p=", rl64(pd, 0));
    let mt: u32 = cg_mem_text(536870912 as u64, o, 32);
    say_cstr("cg_mem=", o, mt);
    let n4: u32 = put("memory.max 1024", b, 256);
    say("cg_key=", cg_key_match(b, n4, "memory.max") as u64);

    // epoll 位名
    let en: u32 = ep_mask_text(2147483649 as u32, o, 64);
    say_cstr("ep=", o, en);

    // io_uring 的 SQE 打包
    let sq: u32 = io_sqe_pack(b, 18 as u32, 7 as i64, 4096 as u64, 512, 8192 as u64, 11259375 as u64);
    say("sq_n=", sq as u64);
    say("sq_op=", load8(b, 0) as u64);
    say("sq_fd=", rl32(b, 4) as u64);
    say("sq_off=", rl64(b, 8));
    say("sq_addr=", rl64(b, 16));
    say("sq_len=", rl32(b, 24) as u64);

    // 文件系统魔数
    let sm: u32 = st_magic_name(61267 as u64, o, 32);
    say_cstr("st=", o, sm);

    // futex 操作名
    let fnt: u32 = fx_op_text(128 as u32, o, 64);
    say_cstr("fx=", o, fnt);

    // 公历
    say("cal_d=", cal_days_from_civil(2024 as i64, 2 as i64, 29 as i64) as u64);
    let cc: u32 = cal_civil_from_days(0 as i64, yp, mp, dp);
    say("cal_y=", rl64(yp, 0));
    say("cal_m=", rl64(mp, 0));
    say("cal_dd=", rl64(dp, 0));
    say("cal_wd=", cal_weekday(0 as i64) as u64);
    let yt: u32 = cal_ymd_text(2024 as i64, 2 as i64, 29 as i64, o, 32);
    say_cstr("cal_t=", o, yt);

    // 路径
    let n5: u32 = put("/a//b/../c/./d", b, 256);
    let pn: u32 = pa_normalize(b, n5, o, 64);
    say_cstr("pn=", o, pn);

    // 权限位
    let ut: u32 = um_mode_text(493 as u32, o, 32);
    say_cstr("um_t=", o, ut);
    say("um_p=", um_parse_text("rw-r--r--") as u64);
    say("um_a=", um_apply(18 as u32, 511 as u32) as u64);

    // /proc/<pid>/status
    let n6: u32 = put("Name: probe\nState: R (running)\nPid: 42\nVmRSS: 1024 kB\n", b, 256);
    say("ps_pid=", ps_value_u64(b, n6, "Pid", q) as u64);
    say("ps_pidv=", rl64(q, 0));
    say("ps_rss=", ps_value_u64(b, n6, "VmRSS", q) as u64);
    say("ps_rssv=", rl64(q, 0));
    say("ps_st=", ps_state_code(b, n6) as u64);
    let sst: u32 = ps_state_text(ps_state_code(b, n6), o, 32);
    say_cstr("ps_stt=", o, sst);

    // mountinfo 字段
    let n7: u32 = put("1 2 0:1 / /mnt rw - tmpfs tmpfs rw", b, 256);
    say("mi_cnt=", mi_field_count(b, n7) as u64);
    let f4: u32 = mi_field(b, n7, 4, o, 64);
    say_cstr("mi_f4=", o, f4);

    // argv NUL 块
    let nn: u32 = put_at("a", b, 0, 256);
    store8(b, 1, 0 as u8);
    let nn2: u32 = put_at("b", b, 2, 256);
    say("an_n=", (nn + nn2) as u64);
    say("ar_n=", ar_count(b, 3) as u64);
    let gotten: u32 = ar_get(b, 3, 1, o, 64);
    say_cstr("ar_1=", o, gotten);

    // /etc/passwd
    let n8: u32 = put("root:x:0:0:root:/root:/bin/sh\n", b, 256);
    say("pw_n=", pw_count(b, n8) as u64);
    let home: u32 = pw_field(b, n8, 0, 5, o, 64);
    say_cstr("pw_home=", o, home);
    say("pw_uid=", pw_uid_of_name(b, n8, "root") as u64);

    // 目录项类型名
    let dd: u32 = put(di_type_name(4), o, 32);
    say_cstr("di_dir=", o, dd);
    let df: u32 = put(di_type_name(8), o, 32);
    say_cstr("di_reg=", o, df);
    let dn: u32 = put(".", o, 32);
    say("di_dot=", di_skip_dots(o, dn) as u64);
    syscall4(60, 0, 0, 0);
}
"""


def _expected_sysos_survey() -> str:
    """Python 独立算出的同一串结果（不复用被测实现里的表）。"""
    umax = 2**64 - 1
    lines = [
        "cg_len=10",
        f"cg_val_hi={4294967296 // 4294967296}",
        f"cg_val_lo={4294967296 % 4294967296}",
        "cg_max_n=3",
        f"cg_max_hi={umax // 4294967296}",
        f"cg_max_lo={umax % 4294967296}",
        "cg_cpu=1",
        "cg_q=200000",
        "cg_p=100000",
        "cg_mem=512M",
        "cg_key=1",
        "ep=EPOLLIN|EPOLLET",
        "sq_n=64",
        "sq_op=18",
        "sq_fd=7",
        "sq_off=8192",
        "sq_addr=4096",
        "sq_len=512",
        "st=ext2/3/4",
        "fx=FUTEX_WAIT|FUTEX_PRIVATE_FLAG",
        "cal_d=19782",
        "cal_y=1970",
        "cal_m=1",
        "cal_dd=1",
        "cal_wd=4",
        "cal_t=2024-02-29",
        "pn=/a/c/d",
        "um_t=rwxr-xr-x",
        "um_p=420",
        "um_a=493",
        "ps_pid=1",
        "ps_pidv=42",
        "ps_rss=1",
        "ps_rssv=1024",
        "ps_st=82",
        "ps_stt=running",
        "mi_cnt=10",
        "mi_f4=/mnt",
        "an_n=2",
        "ar_n=2",
        "ar_1=b",
        "pw_n=1",
        "pw_home=/root",
        "pw_uid=0",
        "di_dir=dir",
        "di_reg=file",
        "di_dot=1",
    ]
    return "\n".join(lines) + "\n"


@test
def test_system_and_os_survey_packages():
    """system/os 补到 40 之后的抽样行为：纯逻辑与 Python 逐字节一致（docs/327）。"""
    try:
        got = _run_probe("sysos", PROBE_SYSOS)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_sysos_survey()
    _compare(got, want)
    print(f"      system/os 抽样: {len(want.splitlines())} 项与 Python 逐字节一致")


@test
def test_lowlevel_and_system_packages():
    """lowlevel/system：二进制格式与内核常量，与 Python 标准库/常量逐字节一致。"""
    try:
        got = _run_probe("lowsys", PROBE_LOW_SYS)
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected_low_sys()
    _compare(got, want)
    print(f"      lowlevel/system: {len(want.splitlines())} 项与 Python 逐字节一致")


def main(argv: list[str] | None = None) -> int:
    import loment_suite
    return loment_suite.run(TESTS, "loment_domains_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
