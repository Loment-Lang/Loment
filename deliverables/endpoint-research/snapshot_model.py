"""Snapshot/restore feasibility model for the Loment endpoint-execution study.

Mirrors the machine state of a `choose runtime` / `gc_manual` Loment program
compiled by tools/lomentc.py at origin/main:

  @__loment_heap  = [65536 x i8] zeroinitializer   (fixed vaddr 0x800000, non-PIE)
  @__loment_off   = i32 8                          (bump offset)
  @__loment_freehead = i32 0                       (free-list head, offsets not pointers)

The `_start` frame is a fixed set of stack slots.  At the init endpoint (after
init, before the steady loop) the live state is: heap + off + freehead + the
live frame slots + the resume label.  There is no register file to capture: the
lomelf backend keeps every SSA value in a stack slot.

This model is a *feasibility proxy*, not the compiler integration.  It checks:
  1. cold run and snapshot-restored run produce the same checksum;
  2. the blob is self-describing (magic/version/fingerprint/checksum);
  3. fingerprint mismatch and tampering are rejected (all-or-nothing);
  4. an external fd is refused (sealed-only v1);
  5. a stack-like value in the heap is detected and refused (conservative guard).
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib

HEAP_BASE = 0x800000
HEAP_SIZE = 65536
BUF = 49152
ROUNDS = 8
STACK_LO = 0x7FF000000000
STACK_HI = 0x7FFFFFFFFFFF

MAGIC = b"LMSNAP1\x00"
VERSION = 1
FRAME_SLOTS = ("ts", "buf", "t0", "x", "i", "t1", "acc", "r", "j", "t2")


class State:
    def __init__(self) -> None:
        self.heap = bytearray(HEAP_SIZE)
        self.off = 8
        self.freehead = 0
        self.frame: dict[str, int] = {}
        self.pc = "steady_head"
        self.fds: list[int] = []          # external state; must be empty in v1
        self.fingerprint = b"main-2d9215b2/gc_manual/no_runtime?/x86_64"

    # -- allocator (bump + 8-byte header; free list not exercised here) -------
    def alloc(self, n: int) -> int:
        total = (n + 8 + 7) & ~7
        if self.off + total > HEAP_SIZE:
            raise MemoryError("64 KiB arena exhausted")
        p = self.off + 8
        self.off += total
        return p

    def init(self) -> None:
        ts = self.alloc(16)
        buf = self.alloc(BUF)
        self.frame.update(ts=HEAP_BASE + ts, buf=HEAP_BASE + buf)
        for i in range(BUF):
            self.heap[buf + i] = i % 251
        # state at the init endpoint
        self.frame.update(t0=1, x=12345, i=BUF, t1=2, acc=0, r=0, j=0, t2=0)

    def steady(self) -> int:
        buf = self.frame["buf"] - HEAP_BASE
        acc = self.frame["acc"]
        for _ in range(ROUNDS):
            j = self.frame["j"]
            while j < BUF:
                acc += self.heap[buf + j]
                j += 1
        self.frame["acc"] = acc
        return acc % 1_000_000

    # -- stack-pointer guard ---------------------------------------------------
    def heap_has_stack_values(self) -> int:
        hits = 0
        for off in range(0, HEAP_SIZE - 7, 8):
            v = int.from_bytes(self.heap[off:off + 8], "little")
            if STACK_LO <= v <= STACK_HI:
                hits += 1
        return hits


def cold_run() -> int:
    s = State()
    s.init()
    return s.steady()


def encode(s: State) -> bytes:
    """Header + body.  All integers little-endian; heap stored raw."""
    if s.fds:
        raise ValueError("external fds are not snapshot-able in v1")
    if s.heap_has_stack_values():
        raise ValueError("heap contains stack-like values; refuse (conservative)")
    body = bytearray()
    body += struct.pack("<II", s.off, s.freehead)
    body += struct.pack("<I", len(FRAME_SLOTS))
    for name in FRAME_SLOTS:
        body += struct.pack("<Q", s.frame.get(name, 0))
    body += s.pc.encode("ascii").ljust(16, b"\x00")
    body += bytes(s.heap)
    checksum = hashlib.sha256(body).digest()
    head = MAGIC + struct.pack("<I", VERSION) + struct.pack("<I", len(s.fingerprint))
    head += s.fingerprint + checksum
    return head + body


def decode(blob: bytes, fingerprint: bytes) -> State | None:
    if not blob.startswith(MAGIC):
        return None
    p = len(MAGIC)
    (ver,) = struct.unpack_from("<I", blob, p); p += 4
    if ver != VERSION:
        return None
    (flen,) = struct.unpack_from("<I", blob, p); p += 4
    fp = blob[p:p + flen]; p += flen
    if fp != fingerprint:
        return None
    want = blob[p:p + 32]; p += 32
    body = blob[p:]
    if hashlib.sha256(body).digest() != want:
        return None
    s = State()
    s.fingerprint = fp
    s.off, s.freehead = struct.unpack_from("<II", body, 0)
    (nslots,) = struct.unpack_from("<I", body, 8)
    q = 12
    for name in FRAME_SLOTS[:nslots]:
        (v,) = struct.unpack_from("<Q", body, q); q += 8
        s.frame[name] = v
    s.pc = body[q:q + 16].rstrip(b"\x00").decode("ascii")
    q += 16
    s.heap[:] = body[q:q + HEAP_SIZE]
    return s


def main() -> None:
    t0 = time.perf_counter_ns()
    cold = cold_run()
    t1 = time.perf_counter_ns()
    s = State(); s.init()
    blob = encode(s)
    t2 = time.perf_counter_ns()
    r = decode(blob, s.fingerprint)
    assert r is not None
    restored = r.steady()
    t3 = time.perf_counter_ns()

    print(f"cold_checksum={cold}")
    print(f"restored_checksum={restored}")
    print(f"match={cold == restored}")
    print(f"blob_bytes={len(blob)} heap_bytes={HEAP_SIZE} frame_slots={len(FRAME_SLOTS)}")
    print(f"zlib_bytes={len(zlib.compress(blob, 9))}")
    print(f"cold_ns={t1 - t0} encode_ns={t2 - t1} decode_run_ns={t3 - t2}")

    # negative cases
    bad = bytearray(blob); bad[-1] ^= 0xFF
    print(f"tampered_rejected={decode(bytes(bad), s.fingerprint) is None}")
    print(f"fingerprint_rejected={decode(blob, b'other-build') is None}")

    fds = State(); fds.init(); fds.fds = [1]
    try:
        encode(fds)
        print("fd_refused=False")
    except ValueError:
        print("fd_refused=True")

    stack = State(); stack.init()
    stack.heap[0:8] = (0x7FFD_0000_0000).to_bytes(8, "little")
    try:
        encode(stack)
        print("stack_guard=False")
    except ValueError:
        print("stack_guard=True")


if __name__ == "__main__":
    main()
