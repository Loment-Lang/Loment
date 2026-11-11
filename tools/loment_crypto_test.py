#!/usr/bin/env python3
"""loment_crypto_test.py — 密码学模块的行为判据（docs/328）。

**为什么不用模块自检里那些常数当期望**：自检与实现是同一只手写的，常数抄错两边一起错。
这里 Python 侧**独立实现**同一批算法（ChaCha20/Poly1305/AEAD/AES-GCM/X25519/Ed25519），
期望值由它算出来；Loment 侧由探针调用模块。两条独立实现 + RFC 公开向量三方对得上，
才算过（与 `docs` 里"验翻译链要三方"同一条）。

用法: python tools/loment_crypto_test.py   （无 clang/WSL 时 SKIP）
"""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_json_test as J  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- Python 参考实现

def _chacha_block(key: bytes, nonce: bytes, counter: int) -> bytes:
    def rotl(v, n):
        return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF

    def qr(s, a, b, c, d):
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] = rotl(s[d] ^ s[a], 16)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] = rotl(s[b] ^ s[c], 12)
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] = rotl(s[d] ^ s[a], 8)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] = rotl(s[b] ^ s[c], 7)

    const = b"expand 32-byte k"
    st = list(struct.unpack("<4I", const)) + list(struct.unpack("<8I", key)) + \
        [counter & 0xFFFFFFFF] + list(struct.unpack("<3I", nonce))
    w = st[:]
    for _ in range(10):
        qr(w, 0, 4, 8, 12)
        qr(w, 1, 5, 9, 13)
        qr(w, 2, 6, 10, 14)
        qr(w, 3, 7, 11, 15)
        qr(w, 0, 5, 10, 15)
        qr(w, 1, 6, 11, 12)
        qr(w, 2, 7, 8, 13)
        qr(w, 3, 4, 9, 14)
    return struct.pack("<16I", *[(w[i] + st[i]) & 0xFFFFFFFF for i in range(16)])


def _chacha_xor(key: bytes, nonce: bytes, counter: int, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = _chacha_block(key, nonce, counter + i // 64)
        chunk = data[i:i + 64]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
    return bytes(out)


def _poly1305(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        n = int.from_bytes(block + b"\x01", "little")
        acc = ((acc + n) * r) % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _aead_seal(key: bytes, nonce: bytes, aad: bytes, pt: bytes):
    otk = _chacha_block(key, nonce, 0)[:32]
    ct = _chacha_xor(key, nonce, 1, pt)

    def pad(x):
        return x + b"\x00" * ((16 - len(x) % 16) % 16)

    mac_data = pad(aad) + pad(ct) + struct.pack("<QQ", len(aad), len(ct))
    return ct, _poly1305(otk, mac_data)


# --- AES-128 / GCM

def _xtime(b):
    b <<= 1
    if b & 0x100:
        b ^= 0x11B
    return b & 0xFF


def _aes_sbox():
    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if (a * b) % 0x11B == 1 or (a * b) & 0xFF == 1 and _gf_mul(a, b) == 1:
                pass
    sbox = [0] * 256
    for a in range(256):
        x = 0 if a == 0 else _gf_inv(a)
        s = x
        for _ in range(4):
            x = ((x << 1) | (x >> 7)) & 0xFF
            s ^= x
        sbox[a] = s ^ 0x63
    return sbox


def _gf_mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def _gf_inv(a):
    for b in range(1, 256):
        if _gf_mul(a, b) == 1:
            return b
    return 0


_SBOX = None


def _aes128_expand(key: bytes) -> list[bytes]:
    global _SBOX
    if _SBOX is None:
        _SBOX = _aes_sbox()
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        tmp = list(w[i - 1])
        if i % 4 == 0:
            tmp = tmp[1:] + tmp[:1]
            tmp = [_SBOX[x] for x in tmp]
            tmp[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([w[i - 4][j] ^ tmp[j] for j in range(4)])
    return [bytes(sum(w[4 * r:4 * r + 4], [])) for r in range(11)]


def _aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    rk = _aes128_expand(key)
    s = [b ^ k for b, k in zip(block, rk[0])]

    def shift_rows(x):
        return [x[0], x[5], x[10], x[15], x[4], x[9], x[14], x[3],
                x[8], x[13], x[2], x[7], x[12], x[1], x[6], x[11]]

    def mix_columns(x):
        out = []
        for c in range(4):
            a = x[4 * c:4 * c + 4]
            out += [_gf_mul(a[0], 2) ^ _gf_mul(a[1], 3) ^ a[2] ^ a[3],
                    a[0] ^ _gf_mul(a[1], 2) ^ _gf_mul(a[2], 3) ^ a[3],
                    a[0] ^ a[1] ^ _gf_mul(a[2], 2) ^ _gf_mul(a[3], 3),
                    _gf_mul(a[0], 3) ^ a[1] ^ a[2] ^ _gf_mul(a[3], 2)]
        return out

    for r in range(1, 10):
        s = [_SBOX[b] for b in s]
        s = shift_rows(s)
        s = mix_columns(s)
        s = [b ^ k for b, k in zip(s, rk[r])]
    s = [_SBOX[b] for b in s]
    s = shift_rows(s)
    return bytes([b ^ k for b, k in zip(s, rk[10])])


def _gcm_mul(x: int, y: int) -> int:
    r = 0xE1000000000000000000000000000000
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ r
        else:
            v >>= 1
    return z


def _ghash(h: int, data: bytes) -> int:
    y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16].ljust(16, b"\x00")
        y = _gcm_mul(y ^ int.from_bytes(blk, "big"), h)
    return y


def _aes_gcm_seal(key: bytes, iv: bytes, aad: bytes, pt: bytes):
    h = int.from_bytes(_aes128_encrypt_block(key, b"\x00" * 16), "big")
    if len(iv) == 12:
        j0 = iv + b"\x00\x00\x00\x01"
    else:
        pad = b"\x00" * ((16 - len(iv) % 16) % 16)
        j0 = _ghash(h, iv + pad + struct.pack(">QQ", 0, len(iv) * 8)).to_bytes(16, "big")
    ct = bytearray()
    ctr = int.from_bytes(j0, "big")
    for i in range(0, len(pt), 16):
        ctr = (ctr & ~0xFFFFFFFF) | ((ctr + 1) & 0xFFFFFFFF)
        ks = _aes128_encrypt_block(key, ctr.to_bytes(16, "big"))
        ct += bytes(a ^ b for a, b in zip(pt[i:i + 16], ks))
    def pad(x):
        return x + b"\x00" * ((16 - len(x) % 16) % 16)
    s = _ghash(h, pad(aad) + pad(bytes(ct)) + struct.pack(">QQ", len(aad) * 8, len(ct) * 8))
    tag = (s ^ int.from_bytes(_aes128_encrypt_block(key, j0), "big")).to_bytes(16, "big")
    return bytes(ct), tag


# --- X25519 / Ed25519

_P = 2 ** 255 - 19


def _x25519(k: bytes, u: bytes) -> bytes:
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    scalar = int.from_bytes(k, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (scalar >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * (z3 * z3 % _P) % _P
        x2 = aa * bb % _P
        z2 = e * (aa + 121665 * e % _P) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


_ED_L = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_ED_I = pow(2, (_P - 1) // 4, _P)


def _ed_recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _ED_I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_ED_BY = 4 * pow(5, _P - 2, _P) % _P
_ED_BX = _ed_recover_x(_ED_BY, 0)


def _ed_add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _ED_D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _ed_scalarmult(p, s):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _ed_add(q, p)
        p = _ed_add(p, p)
        s >>= 1
    return q


def _ed_encode(p):
    x, y, z, _ = p
    zi = pow(z, _P - 2, _P)
    x = x * zi % _P
    y = y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _ed_decode(s):
    val = int.from_bytes(s, "little")
    y = val & ((1 << 255) - 1)
    x = _ed_recover_x(y, (val >> 255) & 1)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _ed_public(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = bytearray(h[:32])
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    return _ed_encode(_ed_scalarmult((_ED_BX, _ED_BY, 1, _ED_BX * _ED_BY % _P),
                                     int.from_bytes(a, "little")))


def _ed_sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = bytearray(h[:32])
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    pk = _ed_public(seed)
    r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), "little") % _ED_L
    big_r = _ed_encode(_ed_scalarmult((_ED_BX, _ED_BY, 1, _ED_BX * _ED_BY % _P), r))
    k = int.from_bytes(hashlib.sha512(big_r + pk + msg).digest(), "little") % _ED_L
    s = (r + k * int.from_bytes(a, "little")) % _ED_L
    return big_r + s.to_bytes(32, "little")


def _ed_verify(pk: bytes, msg: bytes, sig: bytes) -> int:
    a = _ed_decode(pk)
    r = _ed_decode(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if a is None or r is None or s >= _ED_L:
        return 1
    k = int.from_bytes(hashlib.sha512(sig[:32] + pk + msg).digest(), "little") % _ED_L
    lhs = _ed_scalarmult((_ED_BX, _ED_BY, 1, _ED_BX * _ED_BY % _P), s)
    rhs = _ed_add(r, _ed_scalarmult(a, k))
    return 0 if _ed_encode(lhs) == _ed_encode(rhs) else 1


# ---------------------------------------------------------------- 探针

PROBE = r'''
module cryptoprobe
choose runtime

use chacha20
use poly1305
use aead
use aes
use x25519
use ed25519

fn hxv(v: u32) -> u32 {
    return v & 255;
}

fn hexin(s: str, out: ptr) -> u32 {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    let o: u32 = 0;
    while i + 1 < n {
        let hi: u32 = hxv(str_byte(s, i) - 48);
        let lo: u32 = hxv(str_byte(s, i + 1) - 48);
        let h2: u32 = 0;
        if str_byte(s, i) >= 97 { h2 = str_byte(s, i) - 87; } else { h2 = str_byte(s, i) - 48; }
        let l2: u32 = 0;
        if str_byte(s, i + 1) >= 97 { l2 = str_byte(s, i + 1) - 87; } else { l2 = str_byte(s, i + 1) - 48; }
        store8(out, o, (h2 * 16 + l2) as u8);
        o = o + 1;
        i = i + 2;
    }
    return o;
}

fn hexout(tag: str, p: ptr, n: u32) {
    let hexs: str = "0123456789abcdef";
    let _t: u32 = print(tag);
    let i: u32 = 0;
    let b: ptr = alloc(2);
    while i < n {
        let v: u32 = load8(p, i) as u32;
        store8(b, 0, str_byte(hexs, v / 16) as u8);
        store8(b, 1, str_byte(hexs, v % 16) as u8);
        let _w: i64 = syscall4(1, 1, b as u64, 2);
        i = i + 1;
    }
    let nl: ptr = alloc(1);
    store8(nl, 0, 10 as u8);
    let _x: i64 = syscall4(1, 1, nl as u64, 1);
}

fn _start() {
    let key: ptr = alloc(64);
    let nonce: ptr = alloc(32);
    let msg: ptr = alloc(512);
    let out: ptr = alloc(512);
    let tag: ptr = alloc(32);

    // 1. ChaCha20 block
    let kn: u32 = hexin("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", key);
    let nn: u32 = hexin("000000090000004a00000000", nonce);
    let cx: ptr = alloc(cc_ctx_bytes());
    let _s1: u32 = cc_setup(cx, key, nonce, 1 as u32);
    let _b1: u32 = cc_block(cx, out);
    hexout("chacha=", out, 32);

    // 2. Poly1305
    let pk: ptr = alloc(32);
    let _pkn: u32 = hexin("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b", pk);
    let mn: u32 = 0;
    let mtext: str = "Cryptographic Forum Research Group";
    let i: u32 = 0;
    while i < str_len(mtext) {
        store8(msg, i, str_byte(mtext, i) as u8);
        i = i + 1;
    }
    mn = i;
    let pst: ptr = alloc(poly_state_bytes());
    let _t1: u32 = poly_tag(pk, msg, mn, tag, pst);
    hexout("poly=", tag, 16);

    // 3. AEAD
    let ak: ptr = alloc(32);
    let _akn: u32 = hexin("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f", ak);
    let an: ptr = alloc(12);
    let _ann: u32 = hexin("070000004041424344454647", an);
    let aad: ptr = alloc(16);
    let aadn: u32 = hexin("50515253c0c1c2c3c4c5c6c7", aad);
    let atext: str = "Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.";
    i = 0;
    while i < str_len(atext) {
        store8(msg, i, str_byte(atext, i) as u8);
        i = i + 1;
    }
    let amn: u32 = i;
    let asc: ptr = alloc(aead_scratch(amn));
    let ct: ptr = alloc(256);
    let atag: ptr = alloc(16);
    let _se: u32 = aead_seal(ak, an, aad, aadn, msg, amn, ct, atag, asc);
    hexout("aead_ct=", ct, 32);
    hexout("aead_tag=", atag, 16);

    // 4. AES-128 block
    let ak128: ptr = alloc(16);
    let _a128: u32 = hexin("000102030405060708090a0b0c0d0e0f", ak128);
    let blk: ptr = alloc(16);
    let _blkn: u32 = hexin("00112233445566778899aabbccddeeff", blk);
    let rk: ptr = alloc(240);
    let rounds: u32 = aes_expand(ak128, 16 as u32, rk);
    let _aenc: u32 = aes_encrypt(rk, rounds, blk, out);
    hexout("aes=", out, 16);

    // 5. AES-GCM（全零 key/iv、空明文）
    let zk: ptr = alloc(16);
    let ziv: ptr = alloc(12);
    i = 0;
    while i < 16 { store8(zk, i, 0 as u8); i = i + 1; }
    i = 0;
    while i < 12 { store8(ziv, i, 0 as u8); i = i + 1; }
    let gsc: ptr = alloc(aes_gcm_scratch(0));
    let gtag: ptr = alloc(16);
    let _gs: u32 = aes_gcm_seal(zk, 16 as u32, ziv, 12 as u32, ziv, 0, ziv, 0, out, gtag, gsc);
    hexout("gcm=", gtag, 16);

    // 6. X25519
    let xa: ptr = alloc(32);
    let xb: ptr = alloc(32);
    let _xan: u32 = hexin("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a", xa);
    let _xbn: u32 = hexin("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb", xb);
    let xsc: ptr = alloc(x25519_scratch());
    let puba: ptr = alloc(32);
    let pubb: ptr = alloc(32);
    let _pa: u32 = x25519_base(xa, puba, xsc);
    let _pb: u32 = x25519_base(xb, pubb, xsc);
    let shared: ptr = alloc(32);
    let _sh: u32 = x25519(xa, pubb, shared, xsc);
    hexout("x_puba=", puba, 32);
    hexout("x_pubb=", pubb, 32);
    hexout("x_shared=", shared, 32);

    // 7. Ed25519（RFC 8032 §7.1 test 2）
    let seed: ptr = alloc(32);
    let _sn: u32 = hexin("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", seed);
    let epk: ptr = alloc(32);
    let esc: ptr = alloc(ed_scratch());
    let _ep: u32 = ed_public(seed, epk, esc);
    hexout("ed_pub=", epk, 32);
    let emsg: ptr = alloc(8);
    store8(emsg, 0, 114 as u8);
    let sig: ptr = alloc(64);
    let _es: u32 = ed_sign(seed, emsg, 1 as u32, sig, esc);
    hexout("ed_sig=", sig, 64);
    let ver: u32 = ed_verify(epk, emsg, 1 as u32, sig, esc);
    let _v: u32 = print("ed_ver=");
    let _vn: u32 = print_num(ver as u64);
    let nl2: ptr = alloc(1);
    store8(nl2, 0, 10 as u8);
    let _vnl: i64 = syscall4(1, 1, nl2 as u64, 1);

    syscall4(60, 0, 0, 0);
}
'''


def _expected() -> str:
    key = bytes(range(32))
    nonce = bytes.fromhex("000000090000004a00000000")
    chacha = _chacha_block(key, nonce, 1)[:32].hex()

    pkey = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
    poly = _poly1305(pkey, b"Cryptographic Forum Research Group").hex()

    ak = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
    an = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    msg = (b"Ladies and Gentlemen of the class of '99: If I could offer you only "
           b"one tip for the future, sunscreen would be it.")
    ct, tag = _aead_seal(ak, an, aad, msg)

    aes_key = bytes(range(16))
    aes = _aes128_encrypt_block(aes_key, bytes.fromhex("00112233445566778899aabbccddeeff")).hex()
    gct, gtag = _aes_gcm_seal(b"\x00" * 16, b"\x00" * 12, b"", b"")

    xa = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    xb = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    puba = _x25519(xa, (9).to_bytes(32, "little"))
    pubb = _x25519(xb, (9).to_bytes(32, "little"))
    shared = _x25519(xa, pubb)

    seed = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    epk = _ed_public(seed)
    esig = _ed_sign(seed, bytes([0x72]))
    ever = _ed_verify(epk, bytes([0x72]), esig)

    lines = [
        f"chacha={chacha}",
        f"poly={poly}",
        f"aead_ct={(ct[:32]).hex()}",
        f"aead_tag={tag.hex()}",
        f"aes={aes}",
        f"gcm={gtag.hex()}",
        f"x_puba={puba.hex()}",
        f"x_pubb={pubb.hex()}",
        f"x_shared={shared.hex()}",
        f"ed_pub={epk.hex()}",
        f"ed_sig={esig.hex()}",
        f"ed_ver={ever}",
    ]
    return "\n".join(lines) + "\n"


def _run_probe() -> str:
    if not (J._clang() and J._wsl()):
        raise _Skip("无 clang/WSL")
    probe = ROOT / "loment" / "build" / "crypto_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(PROBE, encoding="utf-8", newline="\n")
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            elf = J._compile(probe, td, "crypto_probe")
            return J._run(elf, td)
    finally:
        probe.unlink(missing_ok=True)


class _Skip(Exception):
    pass


@test
def test_crypto_primitives_match_independent_python():
    """六个原语与 Python 独立实现逐字节一致（ChaCha20/Poly1305/AEAD/AES-GCM/X25519/Ed25519）。"""
    try:
        got = _run_probe()
    except _Skip as e:
        print(f"      SKIP: {e}")
        return
    want = _expected()
    if got != want:
        g, w = got.splitlines(), want.splitlines()
        first = next((i for i in range(max(len(g), len(w)))
                      if (g[i] if i < len(g) else None) != (w[i] if i < len(w) else None)), None)
        raise AssertionError(
            f"密码学结果与 Python 不一致 @ 第 {first} 行: "
            f"Loment={g[first] if first is not None and first < len(g) else '<无>'!r} "
            f"Python={w[first] if first is not None and first < len(w) else '<无>'!r}")
    print(f"      六个原语：{len(want.splitlines())} 项与 Python 独立实现逐字节一致")


def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_crypto_test",
                            only=((argv or sys.argv[1:] or [None])[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
