"""Build-time pre-initialisation prototype (post-link patch).

The probe `initheavy.lomt` spends ~6.2 ms filling a 49 KiB buffer; `preinit.lomt`
is the same program with that init loop removed.  This script computes the heap
image the init would have produced and writes it into the ELF's data segment at
the address the allocator hands to `buf`.  The program then starts at the steady
loop with the heap already initialised: no program-level cold start.

This is a research prototype, not a compiler feature.  The real feature would
run the init in a build-time harness and emit the data segment directly; the
bytes are the same.

Usage:
  python patch_preinit.py --src build/preinit --dst build/preinit-patched --buf-vaddr 0x800080

`--buf-vaddr` is the pointer value the probe prints (or derives from the
allocator: heap_base + 0x30, where heap_base = DATA_VADDR + len(.data)).
"""

from __future__ import annotations

import argparse
import struct


def heap_image(warm: int, buf: int) -> bytearray:
    x = 1
    img = bytearray(buf)
    for i in range(warm):
        x = (x * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        if i % 4096 == 0:
            img[(i // 4096) % buf] = x % 251
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--buf-vaddr", required=True, type=lambda s: int(s, 0))
    ap.add_argument("--warm", type=int, default=2_000_000)
    ap.add_argument("--buf", type=int, default=49_152)
    args = ap.parse_args()

    elf = bytearray(open(args.src, "rb").read())
    e_phoff = struct.unpack_from("<Q", elf, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", elf, 0x36)[0]
    e_phnum = struct.unpack_from("<H", elf, 0x38)[0]
    ph = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_flags = struct.unpack_from("<II", elf, off)
        p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(
            "<QQQQQQ", elf, off + 8)
        ph.append(dict(off=off, type=p_type, flags=p_flags, offset=p_offset,
                       vaddr=p_vaddr, paddr=p_paddr, filesz=p_filesz,
                       memsz=p_memsz, align=p_align))

    data_p = next(p for p in ph if p["type"] == 1 and p["vaddr"] >= 0x800000)
    img = heap_image(args.warm, args.buf)
    new_data = bytearray(data_p["memsz"])
    new_data[:data_p["filesz"]] = elf[data_p["offset"]:data_p["offset"] + data_p["filesz"]]
    at = args.buf_vaddr - data_p["vaddr"]
    assert 0 <= at and at + args.buf <= len(new_data), (at, len(new_data))
    new_data[at:at + args.buf] = img

    new_off = (len(elf) + 0xFFF) & ~0xFFF
    out = bytearray(elf)
    out += b"\x00" * (new_off - len(out))
    out += new_data
    struct.pack_into("<Q", out, data_p["off"] + 8, new_off)          # p_offset
    struct.pack_into("<Q", out, data_p["off"] + 32, data_p["memsz"])  # p_filesz
    open(args.dst, "wb").write(out)
    print(f"wrote {args.dst}: {len(out)} B, data at 0x{new_off:x}, "
          f"image at vaddr 0x{args.buf_vaddr:x} (data+0x{at:x}), "
          f"nonzero={sum(1 for b in img if b)}")


if __name__ == "__main__":
    main()
