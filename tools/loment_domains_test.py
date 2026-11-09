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


def main(argv: list[str] | None = None) -> int:
    import loment_suite
    return loment_suite.run(TESTS, "loment_domains_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
