#!/usr/bin/env python3
"""lomjava.py — Java .class -> Loment IR + interface (P0 reference implementation).

This is the reference side of the AOT Java front end described in docs/274 and
docs/275.  The first slice is deliberately small:

  * one .class file (a .jar reader comes later);
  * public static methods whose parameters and return are 32-bit integers or
    longs (boolean/byte/char/short are represented as i32 on the JVM stack);
  * arithmetic, locals, conditional branches and goto;
  * no objects, arrays, strings, exceptions, virtual calls, invokedynamic.

The output is a portable Loment IR module plus a generated Loment interface
unit.  The interface declares the mangled symbol as `extern fn`; the IR merge
step removes that declaration once the definition is present, so the call is a
direct internal call on both ELF and PE.

Usage:
    python tools/lomjava.py CLASS --module mathlib --out-dir DIR
    python tools/lomjava.py CLASS --module mathlib --emit-ir OUT.ll \
                                  --emit-interface OUT.lomt

Exit codes: 0 success, 1 unsupported/malformed input, 2 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- class file

CP_UTF8 = 1
CP_INTEGER = 3
CP_FLOAT = 4
CP_LONG = 5
CP_DOUBLE = 6
CP_CLASS = 7
CP_STRING = 8
CP_FIELDREF = 9
CP_METHODREF = 10
CP_INTERFACE_METHODREF = 11
CP_NAME_AND_TYPE = 12
CP_METHOD_HANDLE = 15
CP_METHOD_TYPE = 16
CP_DYNAMIC = 17
CP_INVOKE_DYNAMIC = 18
CP_MODULE = 19
CP_PACKAGE = 20

ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_NATIVE = 0x0100
ACC_ABSTRACT = 0x0400


class JavaError(Exception):
    """A class-file or bytecode feature this slice does not support."""


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.i = 0

    def u1(self) -> int:
        if self.i + 1 > len(self.data):
            raise JavaError("truncated class file")
        v = self.data[self.i]
        self.i += 1
        return v

    def u2(self) -> int:
        if self.i + 2 > len(self.data):
            raise JavaError("truncated class file")
        v = struct.unpack_from(">H", self.data, self.i)[0]
        self.i += 2
        return v

    def u4(self) -> int:
        if self.i + 4 > len(self.data):
            raise JavaError("truncated class file")
        v = struct.unpack_from(">I", self.data, self.i)[0]
        self.i += 4
        return v

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.data):
            raise JavaError("truncated class file")
        v = self.data[self.i:self.i + n]
        self.i += n
        return v


@dataclass
class Method:
    access: int
    name: str
    desc: str
    code: bytes | None
    max_stack: int
    max_locals: int
    exc: list
    #: `StackMapTable` 的帧：[(bytecode offset, 声明的操作数栈类型列表)]。
    #: 空表示类文件没带这张表（v49 及以前）。局部变量的声明帧直接从表里读。
    frames: list = field(default_factory=list)


@dataclass
class Field:
    access: int
    name: str
    desc: str


@dataclass
class ClassFile:
    name: str
    methods: list[Method]
    fields: list[Field]
    pool: list
    #: 超类名（`java/lang/Object` 为 None）与直接接口名 —— 验证器的可见性要用。
    super_name: str | None = None
    interfaces: list = field(default_factory=list)


@dataclass
class ClassMeta:
    layout: dict          # instance field -> (byte offset, ir type)
    static_layout: dict   # static field -> (global symbol, ir type)
    size: int             # bytes allocated by `new`


def parse_class(data: bytes) -> ClassFile:
    r = Reader(data)
    if r.u4() != 0xCAFEBABE:
        raise JavaError("bad magic (not a class file)")
    r.u2()  # minor
    major = r.u2()
    if major < 45 or major > 69:
        raise JavaError(f"unsupported class-file major version {major}")

    cp_count = r.u2()
    pool: list = [None] * cp_count
    i = 1
    while i < cp_count:
        tag = r.u1()
        if tag == CP_UTF8:
            n = r.u2()
            raw = r.take(n)
            try:
                # Class-file UTF-8 is "modified UTF-8": a NUL is encoded as
                # C0 80. Replace that before decoding; ordinary UTF-8 otherwise.
                s = raw.replace(b"\xc0\x80", b"\x00").decode("utf-8")
            except UnicodeDecodeError as e:
                raise JavaError(f"constant pool #{i}: invalid modified UTF-8: {e}") from None
            pool[i] = (tag, s)
        elif tag == CP_INTEGER:
            pool[i] = (tag, struct.unpack(">i", r.take(4))[0])
        elif tag == CP_FLOAT:
            pool[i] = (tag, struct.unpack(">f", r.take(4))[0])
        elif tag == CP_LONG:
            pool[i] = (tag, struct.unpack(">q", r.take(8))[0])
            i += 1
            if i < cp_count:
                pool[i] = None
        elif tag == CP_DOUBLE:
            pool[i] = (tag, struct.unpack(">d", r.take(8))[0])
            i += 1
            if i < cp_count:
                pool[i] = None
        elif tag in (CP_CLASS, CP_STRING, CP_METHOD_TYPE, CP_MODULE, CP_PACKAGE):
            pool[i] = (tag, r.u2())
        elif tag in (CP_FIELDREF, CP_METHODREF, CP_INTERFACE_METHODREF,
                     CP_NAME_AND_TYPE, CP_DYNAMIC, CP_INVOKE_DYNAMIC):
            pool[i] = (tag, r.u2(), r.u2())
        elif tag == CP_METHOD_HANDLE:
            pool[i] = (tag, r.u1(), r.u2())
        else:
            raise JavaError(f"constant pool #{i}: unsupported tag {tag}")
        i += 1

    r.u2()  # access
    this_class = r.u2()
    super_idx = r.u2()
    ifaces = [r.u2() for _ in range(r.u2())]
    fields: list[Field] = []
    for _ in range(r.u2()):
        access = r.u2()
        fname = _utf8(pool, r.u2())
        fdesc = _utf8(pool, r.u2())
        _skip_attributes(r, pool)
        fields.append(Field(access, fname, fdesc))
    methods: list[Method] = []
    for _ in range(r.u2()):
        access = r.u2()
        name = _utf8(pool, r.u2())
        desc = _utf8(pool, r.u2())
        code = None
        max_stack = max_locals = 0
        exc: list = []
        frames: list = []
        for _ in range(r.u2()):
            aname = _utf8(pool, r.u2())
            length = r.u4()
            body = r.take(length)
            if aname == "Code":
                cr = Reader(body)
                max_stack = cr.u2()
                max_locals = cr.u2()
                n = cr.u4()
                code = cr.take(n)
                for _ in range(cr.u2()):
                    exc.append((cr.u2(), cr.u2(), cr.u2(), cr.u2()))
                for _ in range(cr.u2()):
                    cname = _utf8(pool, cr.u2())
                    clen = cr.u4()
                    cbody = cr.take(clen)
                    if cname == "StackMapTable":
                        frames = _parse_stack_map(cbody)
        methods.append(Method(access, name, desc, code, max_stack, max_locals, exc,
                              frames))

    return ClassFile(_class_name(pool, this_class), methods, fields, pool,
                     _class_name(pool, super_idx) if super_idx else None,
                     [_class_name(pool, i) for i in ifaces])


#: `StackMapTable` 的 verification_type_info 标签（JVMS §4.7.4）。
SM_TOP, SM_INT, SM_FLOAT, SM_DOUBLE, SM_LONG, SM_NULL, SM_UTHIS, SM_OBJ, SM_UNINIT = range(9)


def _parse_vti(r: Reader) -> tuple:
    tag = r.u1()
    if tag == 7:
        return (SM_OBJ, r.u2())
    if tag == 8:
        return (SM_UNINIT, r.u1())
    if 0 <= tag <= 6:
        return (tag, 0)
    raise JavaError(f"StackMapTable: bad verification type {tag}")


def _parse_stack_map(body: bytes) -> list:
    """把 `StackMapTable` 读成**增量帧**：[(offset, kind, payload)]。

    增量而不是展开：`chop`/`append` 相对的是上一帧，而上一帧相对的是方法的入口帧
    （形参）—— 入口帧只有调用方（验证器）知道。这里只负责把字节读对。
    """
    r = Reader(body)
    out: list = []
    off = -1
    for _ in range(r.u2()):
        ft = r.u1()
        if ft <= 63:                       # same_frame
            off += ft + 1
            out.append((off, "same", ()))
        elif ft <= 127:                    # same_locals_1_stack_item
            off += (ft - 64) + 1
            out.append((off, "same1", (_parse_vti(r),)))
        elif ft == 247:                    # same_locals_1_stack_item_extended
            off += r.u2() + 1
            out.append((off, "same1", (_parse_vti(r),)))
        elif 248 <= ft <= 250:             # chop_frame
            off += r.u2() + 1
            out.append((off, "chop", (251 - ft,)))
        elif ft == 251:                    # same_frame_extended
            off += r.u2() + 1
            out.append((off, "same", ()))
        elif 252 <= ft <= 254:             # append_frame
            off += r.u2() + 1
            out.append((off, "append", tuple(_parse_vti(r) for _ in range(ft - 251))))
        elif ft == 255:                    # full_frame
            off += r.u2() + 1
            loc = tuple(_parse_vti(r) for _ in range(r.u2()))
            st = tuple(_parse_vti(r) for _ in range(r.u2()))
            out.append((off, "full", (loc, st)))
        else:
            raise JavaError(f"StackMapTable: bad frame type {ft}")
    return out


def _slots(vtis: tuple) -> list:
    """一个 verification type 占几个槽：long/double 占两个（第二个是 Top）。"""
    out: list = []
    for tag, info in vtis:
        out.append((tag, info))
        if tag in (SM_LONG, SM_DOUBLE):
            out.append((SM_TOP, 0))
    return out


def load_classes(path: Path) -> list[ClassFile]:
    """Read a `.class`, or every class entry in a jar (sorted by entry name)."""
    data = path.read_bytes()
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist()
                           if n.endswith(".class") and not n.startswith("META-INF/"))
            if not names:
                raise JavaError("jar has no .class entries")
            if len(names) > MAX_CLASSES:
                raise JavaError(f"J002: jar has {len(names)} classes (cap {MAX_CLASSES})")
            return [parse_class(z.read(n)) for n in names]
    return [parse_class(data)]


def load_class(path: Path) -> ClassFile:
    return load_classes(path)[0]


def _skip_attributes(r: Reader, pool) -> None:
    for _ in range(r.u2()):
        r.u2()
        n = r.u4()
        r.take(n)


def _utf8(pool, idx: int) -> str:
    ent = pool[idx]
    if not ent or ent[0] != CP_UTF8:
        raise JavaError(f"constant pool #{idx}: expected Utf8")
    return ent[1]


def _class_name(pool, idx: int) -> str:
    ent = pool[idx]
    if not ent or ent[0] != CP_CLASS:
        raise JavaError(f"constant pool #{idx}: expected Class")
    return _utf8(pool, ent[1])


# ---------------------------------------------------------------- descriptors

def parse_method_descriptor(desc: str) -> tuple[list[str], str]:
    if not desc.startswith("("):
        raise JavaError(f"bad method descriptor {desc!r}")
    end = desc.find(")")
    if end < 0:
        raise JavaError(f"bad method descriptor {desc!r}")
    params: list[str] = []
    i = 1
    while i < end:
        t, i = _one_type(desc, i)
        params.append(t)
    ret, i = _one_type(desc, end + 1)
    if i != len(desc):
        raise JavaError(f"trailing text in method descriptor {desc!r}")
    return params, ret


def _one_type(s: str, i: int) -> tuple[str, int]:
    if i >= len(s):
        raise JavaError("truncated descriptor")
    c = s[i]
    if c == "[":
        j = i + 1
        while j < len(s) and s[j] == "[":
            j += 1
        _, j = _one_type(s, j)
        return "ptr", j
    if c == "L":
        j = s.find(";", i)
        if j < 0:
            raise JavaError("bad reference descriptor")
        return "ptr", j + 1
    if c == "Z" or c == "B" or c == "C" or c == "S" or c == "I":
        return "i32", i + 1
    if c == "J":
        return "i64", i + 1
    if c == "F" or c == "D":
        raise JavaError("float/double are not supported in the first slice")
    if c == "V":
        return "void", i + 1
    raise JavaError(f"unknown descriptor character {c!r}")


# ---------------------------------------------------------------- bytecode

@dataclass
class Insn:
    off: int
    op: int
    args: tuple
    size: int
    stack_delta: int


#: 不在支持子集里的有名 opcode —— 错误信息里点名，别只给一个十六进制。
_UNSUPPORTED_OPCODE = {
    0xA8: "jsr", 0xA9: "ret", 0xAA: "tableswitch", 0xAB: "lookupswitch",
    0xBA: "invokedynamic", 0xC2: "monitorenter", 0xC3: "monitorexit",
    0xC0: "checkcast", 0xC1: "instanceof", 0xC4: "wide", 0xC5: "multianewarray",
}

_OPS: dict[int, tuple[str, str, int]] = {}


def _op(code: int, name: str, fmt: str = "", delta: int = 0) -> None:
    _OPS[code] = (name, fmt, delta)


def _init_ops() -> None:
    for i, name in enumerate(("nop", "aconst_null", "iconst_m1", "iconst_0", "iconst_1",
                              "iconst_2", "iconst_3", "iconst_4", "iconst_5")):
        _op(i, name, "", 1 if 2 <= i <= 8 else 0)
    _op(9, "lconst_0", "", 1)
    _op(10, "lconst_1", "", 1)
    _op(11, "fconst_0", "", 1)
    _op(12, "fconst_1", "", 1)
    _op(13, "fconst_2", "", 1)
    _op(14, "dconst_0", "", 2)
    _op(15, "dconst_1", "", 2)
    _op(16, "bipush", "b", 1)
    _op(17, "sipush", "h", 1)
    _op(18, "ldc", "b", 1)
    _op(19, "ldc_w", "h", 1)
    _op(20, "ldc2_w", "h", 1)
    _op(21, "iload", "b", 1); _op(22, "lload", "b", 1)
    _op(23, "fload", "b", 1); _op(24, "dload", "b", 2)
    _op(25, "aload", "b", 1)
    for k in range(4):
        _op(26 + k, f"iload_{k}", "", 1)
        _op(30 + k, f"lload_{k}", "", 1)
        _op(42 + k, f"aload_{k}", "", 1)
    _op(54, "istore", "b", -1); _op(55, "lstore", "b", -1)
    _op(56, "fstore", "b", -1); _op(57, "dstore", "b", -2)
    _op(58, "astore", "b", -1)
    for k in range(4):
        _op(59 + k, f"istore_{k}", "", -1)
        _op(63 + k, f"lstore_{k}", "", -1)
        _op(75 + k, f"astore_{k}", "", -1)
    for code, name, delta in (
            (96, "iadd", -1), (97, "ladd", -1), (98, "fadd", -1), (99, "dadd", -2),
            (100, "isub", -1), (101, "lsub", -1), (102, "fsub", -1), (103, "dsub", -2),
            (104, "imul", -1), (105, "lmul", -1), (106, "fmul", -1), (107, "dmul", -2),
            (108, "idiv", -1), (109, "ldiv", -1), (110, "fdiv", -1), (111, "ddiv", -2),
            (112, "irem", -1), (113, "lrem", -1), (114, "frem", -1), (115, "drem", -2),
            (116, "ineg", 0), (117, "lneg", 0),
            (120, "ishl", -1), (121, "lshl", -1), (122, "ishr", -1), (123, "lshr", -1),
            (124, "iushr", -1), (125, "lushr", -1),
            (126, "iand", -1), (127, "land", -1), (128, "ior", -1), (129, "lor", -1),
            (130, "ixor", -1), (131, "lxor", -1),
            (132, "iinc", 0)):
        _op(code, name, "bb" if code == 132 else "", delta)
    _op(148, "lcmp", "", -1)
    _op(133, "i2l", "", 0); _op(134, "i2f", "", 0); _op(135, "i2d", "", 0)
    _op(136, "l2i", "", 0); _op(137, "l2f", "", 0); _op(138, "l2d", "", 0)
    _op(145, "i2b", "", 0); _op(146, "i2c", "", 0); _op(147, "i2s", "", 0)
    _op(153, "ifeq", "h", -1); _op(154, "ifne", "h", -1)
    _op(155, "iflt", "h", -1); _op(156, "ifge", "h", -1)
    _op(157, "ifgt", "h", -1); _op(158, "ifle", "h", -1)
    for code, name in ((159, "if_icmpeq"), (160, "if_icmpne"), (161, "if_icmplt"),
                       (162, "if_icmpge"), (163, "if_icmpgt"), (164, "if_icmple"),
                       (165, "if_acmpeq"), (166, "if_acmpne")):
        _op(code, name, "h", -2)
    _op(167, "goto", "h", 0)
    _op(184, "invokestatic", "h", 0)
    _op(46, "iaload", "", -1); _op(47, "laload", "", -1)
    _op(79, "iastore", "", -3); _op(80, "lastore", "", -3)
    _op(188, "newarray", "b", 0); _op(190, "arraylength", "", 0)
    _op(187, "new", "h", 1); _op(89, "dup", "", 1)
    _op(87, "pop", "", -1); _op(88, "pop2", "", -2)
    _op(180, "getfield", "h", 0); _op(181, "putfield", "h", -2)
    _op(178, "getstatic", "h", 1); _op(179, "putstatic", "h", -1)
    _op(183, "invokespecial", "h", 0); _op(182, "invokevirtual", "h", 0)
    _op(170, "tableswitch", "sw", -1); _op(171, "lookupswitch", "sw", -1)
    _op(191, "athrow", "", -1)
    _op(172, "ireturn", "", -1); _op(173, "lreturn", "", -1)
    _op(174, "freturn", "", -1); _op(175, "dreturn", "", -2)
    _op(176, "areturn", "", -1); _op(177, "return", "", 0)


_init_ops()


def decode(code: bytes) -> list[Insn]:
    out: list[Insn] = []
    i = 0
    while i < len(code):
        off = i
        op = code[i]
        i += 1
        if op not in _OPS:
            # 点名（J003 的意义就在于"拒的是哪一个构造"，不是"有个字你不认识"）。
            nm = _UNSUPPORTED_OPCODE.get(op)
            raise JavaError(f"unsupported opcode 0x{op:02x}"
                            + (f" ({nm})" if nm else "") + f" at {off}")
        name, fmt, delta = _OPS[op]
        args: tuple = ()
        if fmt == "b":
            if i >= len(code):
                raise JavaError(f"truncated {name} at {off}")
            args = (code[i],); i += 1
        elif fmt == "h":
            if i + 2 > len(code):
                raise JavaError(f"truncated {name} at {off}")
            rel = struct.unpack_from(">h", code, i)[0]
            i += 2
            # JVM branch offsets are relative to the opcode's address, not the
            # next instruction and not absolute.
            args = (off + rel,) if (name.startswith("if") or name == "goto") else (rel,)
        elif fmt == "bb":
            if i + 2 > len(code):
                raise JavaError(f"truncated {name} at {off}")
            args = (code[i], struct.unpack_from(">b", code, i + 1)[0]); i += 2
        elif fmt == "sw":
            # 跳转表按 **4 字节对齐**（相对方法起始），padding 是 0..3 个 0 字节。
            while i % 4 != 0:
                i += 1
            if name == "tableswitch":
                if i + 12 > len(code):
                    raise JavaError(f"truncated tableswitch at {off}")
                dflt, low, high = struct.unpack_from(">iii", code, i)
                i += 12
                if high < low or (high - low) > 65535:
                    raise JavaError(f"bad tableswitch bounds at {off}: {low}..{high}")
                n = high - low + 1
                if i + 4 * n > len(code):
                    raise JavaError(f"truncated tableswitch at {off}")
                table = []
                for k in range(n):
                    rel = struct.unpack_from(">i", code, i + 4 * k)[0]
                    table.append((low + k, off + rel))
                i += 4 * n
                args = (off + dflt, tuple(table))
            else:
                if i + 8 > len(code):
                    raise JavaError(f"truncated lookupswitch at {off}")
                dflt, n = struct.unpack_from(">ii", code, i)
                i += 8
                if n < 0 or n > 65535 or i + 8 * n > len(code):
                    raise JavaError(f"bad lookupswitch pair count at {off}: {n}")
                table = []
                for k in range(n):
                    val, rel = struct.unpack_from(">ii", code, i + 8 * k)
                    table.append((val, off + rel))
                i += 8 * n
                args = (off + dflt, tuple(table))
        out.append(Insn(off, op, args, i - off, delta))
    return out


def desc_type(t: str) -> str:
    if not t:
        raise JavaError("empty field descriptor")
    c = t[0]
    if c in "IJZBCS":
        return "i64" if c == "J" else "i32"
    if c in "FD":
        raise JavaError("float/double fields are not supported")
    if c in "L[":
        return "ptr"
    raise JavaError(f"bad field descriptor {t!r}")


def fieldref(pool: list, idx: int) -> tuple[str, str, str]:
    ent = pool[idx] if idx < len(pool) else None
    if not ent or ent[0] != CP_FIELDREF:
        raise JavaError(f"constant pool #{idx}: expected Fieldref")
    cls = _class_name(pool, ent[1])
    nat = pool[ent[2]]
    if not nat or nat[0] != CP_NAME_AND_TYPE:
        raise JavaError(f"constant pool #{ent[2]}: expected NameAndType")
    return cls, _utf8(pool, nat[1]), _utf8(pool, nat[2])


def methodref(pool: list, idx: int) -> tuple[str, str, str]:
    ent = pool[idx] if idx < len(pool) else None
    if not ent or ent[0] not in (CP_METHODREF, CP_INTERFACE_METHODREF):
        raise JavaError(f"constant pool #{idx}: expected Methodref")
    cls = _class_name(pool, ent[1])
    nat = pool[ent[2]]
    if not nat or nat[0] != CP_NAME_AND_TYPE:
        raise JavaError(f"constant pool #{ent[2]}: expected NameAndType")
    return cls, _utf8(pool, nat[1]), _utf8(pool, nat[2])


def _delta(x: Insn, pool: list) -> int:
    name = _OPS[x.op][0]
    if name in ("invokestatic", "invokevirtual", "invokespecial", "invokeinterface"):
        _cls, _n, desc = methodref(pool, x.args[0])
        params, ret = parse_method_descriptor(desc)
        delta = (0 if ret == "void" else 1) - len(params)
        if name != "invokestatic":
            delta -= 1          # `this` is an extra stack value
        return delta
    return x.stack_delta


def stack_heights(insns: list[Insn], pool: list, exc: list | None = None) -> dict[int, int]:
    """Compute the operand-stack height at every reachable instruction.
    Branches that disagree are rejected; this is a small verifier, not a guess.
    Exception handlers start with the thrown object on the stack (height 1).
    """
    by_off = {x.off: x for x in insns}
    height: dict[int, int] = {}
    work = [(0, 0)] + [(e[2], 1) for e in (exc or [])]
    while work:
        off, h = work.pop()
        if off not in by_off:
            raise JavaError(f"branch target {off} is not an instruction")
        if off in height:
            if height[off] != h:
                raise JavaError(f"stack height mismatch at {off}: {height[off]} vs {h}")
            continue
        height[off] = h
        x = by_off[off]
        name = _OPS[x.op][0]
        after = h + _delta(x, pool)
        if after < 0:
            raise JavaError(f"operand stack underflow at {off}")
        nxt = off + x.size
        if name in ("ireturn", "lreturn", "freturn", "dreturn", "areturn",
                    "return", "athrow"):
            continue
        if name == "goto":
            work.append((x.args[0], after))
        elif name in ("tableswitch", "lookupswitch"):
            work.append((x.args[0], after))
            for _val, tgt in x.args[1]:
                work.append((tgt, after))
        elif name.startswith("if"):
            work.append((x.args[0], after))
            work.append((nxt, after))
        else:
            work.append((nxt, after))
    return height


# ---------------------------------------------------------------- verifier (docs/282 §8)
#
# 降级之后内存访问是裸的：操作数栈的一个槽就是 i64，`getfield` 的偏移来自类元数据，
# 但**基址来自操作数栈**。所以"`astore` 存进去的到底是不是引用"必须在降级之前判死
# —— 否则一段手写字节码就能让降级器把任意整数当指针用（`inttoptr` 之后就是任意读）。
#
# 这里判到"引用 / 整数 / 长整数 / 未赋值"这一层。类身份不进类型格：字段偏移与数组
# 元素宽度都来自元数据（§8.2），内存安全只需要"这个槽是引用"。完整 JVM 类型格
# （精确类、`uninitializedThis`、`StackMapTable` 逐帧比对）仍未做，见 docs 的状态行。

T_TOP = "top"      # 局部变量槽未赋值 —— 读它就是错
T_I32 = "i32"
T_I64 = "i64"
T_REF = "ref"
T_NULL = "null"
#: 构造器里的 `this`，在 `invokespecial <init>` 之前不可当引用用。
T_UTHIS = "uninitialized-this"

#: 一个 jar 最多带这么多个类（编译期资源上限的一部分）。
MAX_CLASSES = 1024
#: 单方法代码长度 —— 与 JVM 自己的上限一致，也挡住"一个方法撑爆工具"。
MAX_CODE = 65535
#: 一份 IR 的字节上限 —— 防止一个 jar 在编译期把产物撑到不可用。
MAX_IR_BYTES = 32 * 1024 * 1024


def _vtype(ir_ty: str, ctx: str) -> str:
    if ir_ty == "i32":
        return T_I32
    if ir_ty == "i64":
        return T_I64
    if ir_ty == "ptr":
        return T_REF
    raise JavaError(f"J003: {ctx}: {ir_ty} values are not supported in the first slice")


def _vmerge(a: str, b: str, ctx: str) -> str:
    if a == b:
        return a
    if a == T_TOP or b == T_TOP:
        return T_TOP
    if a == T_NULL and b == T_REF:
        return T_REF
    if a == T_REF and b == T_NULL:
        return T_REF
    raise JavaError(f"J002: {ctx}: incompatible types {a} / {b} at a merge point")


def _vpop(st: list, want: str, ctx: str, off: int) -> None:
    if not st:
        raise JavaError(f"J002: {ctx} @{off}: operand stack underflow")
    t = st.pop()
    if want == T_REF:
        if t not in (T_REF, T_NULL):
            raise JavaError(f"J002: {ctx} @{off}: expected a reference, found {t}")
    elif t != want:
        raise JavaError(f"J002: {ctx} @{off}: expected {want}, found {t}")


def _vpush(st: list, t: str, m: "Method", ctx: str, off: int) -> None:
    st.append(t)
    if len(st) > m.max_stack:
        raise JavaError(
            f"J002: {ctx} @{off}: operand stack exceeds max_stack ({m.max_stack})")


def _vlocal(lc: list, idx: int, ctx: str, off: int) -> str:
    if idx >= len(lc):
        raise JavaError(f"J002: {ctx} @{off}: local {idx} is past max_locals")
    t = lc[idx]
    if t == T_TOP:
        raise JavaError(f"J002: {ctx} @{off}: read of unassigned local {idx}")
    return t


def _vstore(lc: list, idx: int, t: str, ctx: str, off: int) -> None:
    need = 2 if t == T_I64 else 1
    if idx + need > len(lc):
        raise JavaError(f"J002: {ctx} @{off}: local {idx} is past max_locals")
    lc[idx] = t
    if need == 2:
        lc[idx + 1] = T_TOP


def _v_uninit(t: str) -> bool:
    return t == T_UTHIS or t.startswith("uninit@")


def _v_initialized(st: list, lc: list, tag: str) -> None:
    """`invokespecial <init>` 之后，同一个未初始化对象的**所有副本**都成了引用。"""
    for i, t in enumerate(st):
        if t == tag:
            st[i] = T_REF
    for i, t in enumerate(lc):
        if t == tag:
            lc[i] = T_REF


def _vstep(cf: ClassFile, m: Method, x: Insn, st: list, lc: list) -> list[int]:
    """Apply one instruction to (stack, locals); return successor offsets."""
    name = _OPS[x.op][0]
    ctx = f"{cf.name}.{m.name}{m.desc}"
    nxt = x.off + x.size
    ends = name in ("goto", "ireturn", "lreturn", "freturn", "dreturn", "areturn",
                    "return", "athrow", "ret", "tableswitch", "lookupswitch")
    branch = None

    if name == "nop":
        pass
    elif name == "aconst_null":
        _vpush(st, T_NULL, m, ctx, x.off)
    elif name.startswith("iconst_"):
        _vpush(st, T_I32, m, ctx, x.off)
    elif name.startswith("lconst_"):
        _vpush(st, T_I64, m, ctx, x.off)
    elif name.startswith(("fconst_", "dconst_")):
        raise JavaError(f"J003: {ctx} @{x.off}: float/double are not supported")
    elif name in ("bipush", "sipush"):
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("ldc", "ldc_w"):
        idx = x.args[0]
        ent = cf.pool[idx] if idx < len(cf.pool) else None
        if ent and ent[0] == CP_INTEGER:
            _vpush(st, T_I32, m, ctx, x.off)
        elif ent and ent[0] == CP_STRING:
            _vpush(st, T_REF, m, ctx, x.off)
        elif ent and ent[0] in (CP_FLOAT, CP_METHOD_HANDLE, CP_METHOD_TYPE):
            raise JavaError(f"J003: {ctx} @{x.off}: ldc of a constant this slice does not support")
        else:
            raise JavaError(f"J002: {ctx} @{x.off}: bad ldc constant #{idx}")
    elif name == "ldc2_w":
        idx = x.args[0]
        ent = cf.pool[idx] if idx < len(cf.pool) else None
        if ent and ent[0] == CP_LONG:
            _vpush(st, T_I64, m, ctx, x.off)
        elif ent and ent[0] == CP_DOUBLE:
            raise JavaError(f"J003: {ctx} @{x.off}: double is not supported")
        else:
            raise JavaError(f"J002: {ctx} @{x.off}: bad ldc2_w constant #{idx}")
    elif name.startswith(("iload", "lload", "aload")):
        idx = x.args[0] if x.args else int(name[-1])
        got = _vlocal(lc, idx, ctx, x.off)
        if name.startswith("iload"):
            if got != T_I32:
                raise JavaError(f"J002: {ctx} @{x.off}: iload of a {got} local")
            _vpush(st, T_I32, m, ctx, x.off)
        elif name.startswith("lload"):
            if got != T_I64:
                raise JavaError(f"J002: {ctx} @{x.off}: lload of a {got} local")
            _vpush(st, T_I64, m, ctx, x.off)
        else:
            if got not in (T_REF, T_NULL) and not _v_uninit(got):
                raise JavaError(f"J002: {ctx} @{x.off}: aload of a {got} local")
            _vpush(st, got, m, ctx, x.off)
    elif name.startswith("istore"):
        idx = x.args[0] if x.args else int(name[-1])
        _vpop(st, T_I32, ctx, x.off)
        _vstore(lc, idx, T_I32, ctx, x.off)
    elif name.startswith("lstore"):
        idx = x.args[0] if x.args else int(name[-1])
        _vpop(st, T_I64, ctx, x.off)
        _vstore(lc, idx, T_I64, ctx, x.off)
    elif name.startswith("astore"):
        idx = x.args[0] if x.args else int(name[-1])
        if not st:
            raise JavaError(f"J002: {ctx} @{x.off}: operand stack underflow")
        t = st.pop()
        if t not in (T_REF, T_NULL) and not _v_uninit(t):
            raise JavaError(f"J002: {ctx} @{x.off}: astore of a {t}")
        _vstore(lc, idx, t, ctx, x.off)
    elif name in ("fstore", "dstore", "fload", "dload"):
        raise JavaError(f"J003: {ctx} @{x.off}: float/double are not supported")
    elif name == "iinc":
        idx = x.args[0]
        if _vlocal(lc, idx, ctx, x.off) != T_I32:
            raise JavaError(f"J002: {ctx} @{x.off}: iinc on a non-int local")
    elif name in ("iadd", "isub", "imul", "idiv", "irem", "iand", "ior", "ixor"):
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("ladd", "lsub", "lmul", "ldiv", "lrem", "land", "lor", "lxor"):
        _vpop(st, T_I64, ctx, x.off)
        _vpop(st, T_I64, ctx, x.off)
        _vpush(st, T_I64, m, ctx, x.off)
    elif name in ("ineg",):
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("lneg",):
        _vpop(st, T_I64, ctx, x.off)
        _vpush(st, T_I64, m, ctx, x.off)
    elif name in ("ishl", "ishr", "iushr"):
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("lshl", "lshr", "lushr"):
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_I64, ctx, x.off)
        _vpush(st, T_I64, m, ctx, x.off)
    elif name == "lcmp":
        _vpop(st, T_I64, ctx, x.off)
        _vpop(st, T_I64, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name == "i2l":
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_I64, m, ctx, x.off)
    elif name == "l2i":
        _vpop(st, T_I64, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("i2b", "i2c", "i2s"):
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name in ("i2f", "i2d", "l2f", "l2d", "f2i", "f2l", "f2d", "d2i", "d2l",
                  "d2f", "fadd", "dadd", "fsub", "dsub", "fmul", "dmul", "fdiv",
                  "ddiv", "frem", "drem", "fneg", "dneg", "fcmpg", "fcmpl", "dcmpg",
                  "dcmpl"):
        raise JavaError(f"J003: {ctx} @{x.off}: float/double are not supported")
    elif name in ("ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle"):
        _vpop(st, T_I32, ctx, x.off)
        branch = x.args[0]
    elif name in ("if_icmpeq", "if_icmpne", "if_icmplt", "if_icmpge",
                  "if_icmpgt", "if_icmple"):
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_I32, ctx, x.off)
        branch = x.args[0]
    elif name in ("if_acmpeq", "if_acmpne"):
        _vpop(st, T_REF, ctx, x.off)
        _vpop(st, T_REF, ctx, x.off)
        branch = x.args[0]
    elif name == "goto":
        return [x.args[0]]
    elif name in ("tableswitch", "lookupswitch"):
        _vpop(st, T_I32, ctx, x.off)
        dflt, table = x.args
        return [dflt] + [tgt for _val, tgt in table]
    elif name == "dup":
        if not st:
            raise JavaError(f"J002: {ctx} @{x.off}: dup on an empty stack")
        if st[-1] == T_I64:
            raise JavaError(f"J002: {ctx} @{x.off}: dup of a long")
        _vpush(st, st[-1], m, ctx, x.off)
    elif name == "pop":
        _vpop(st, st[-1] if st else T_I32, ctx, x.off)
    elif name == "pop2":
        if not st:
            raise JavaError(f"J002: {ctx} @{x.off}: pop2 on an empty stack")
        if st[-1] == T_I64:
            st.pop()
        else:
            _vpop(st, st[-1], ctx, x.off)
            if not st:
                raise JavaError(f"J002: {ctx} @{x.off}: pop2 on a one-slot stack")
            _vpop(st, st[-1], ctx, x.off)
    elif name == "new":
        # 未初始化的对象带**分配点**：`dup` 出来的两份是同一个对象，一次 `<init>`
        # 之后一起变成引用（JVMS §4.10.1.9）。
        _vpush(st, f"uninit@{x.off}", m, ctx, x.off)
    elif name in ("getstatic", "putstatic"):
        cls, fname, desc = fieldref(cf.pool, x.args[0])
        if (name == "getstatic" and cls == "java/lang/System"
                and fname in ("out", "err")
                and desc == "Ljava/io/PrintStream;"):
            _vpush(st, T_REF, m, ctx, x.off)
        else:
            ty = _vtype(desc_type(desc), ctx)
            if name == "getstatic":
                _vpush(st, ty, m, ctx, x.off)
            else:
                _vpop(st, ty, ctx, x.off)
    elif name in ("getfield", "putfield"):
        cls, fname, desc = fieldref(cf.pool, x.args[0])
        ty = _vtype(desc_type(desc), ctx)
        if name == "getfield":
            _vpop(st, T_REF, ctx, x.off)
            _vpush(st, ty, m, ctx, x.off)
        else:
            _vpop(st, ty, ctx, x.off)
            _vpop(st, T_REF, ctx, x.off)
    elif name in ("invokestatic", "invokevirtual", "invokespecial"):
        cls, mname, desc = methodref(cf.pool, x.args[0])
        params, ret = parse_method_descriptor(desc)
        vtypes = [_vtype(t, ctx) for t in params]
        for t in reversed(vtypes):
            _vpop(st, t, ctx, x.off)
        if name != "invokestatic":
            if not st:
                raise JavaError(f"J002: {ctx} @{x.off}: operand stack underflow")
            recv = st[-1]
            if _v_uninit(recv):
                if mname != "<init>":
                    raise JavaError(
                        f"J002: {ctx} @{x.off}: {cls}.{mname} called on an "
                        f"uninitialized object")
                st.pop()
                _v_initialized(st, lc, recv)
            else:
                _vpop(st, T_REF, ctx, x.off)
        if ret != "void":
            _vpush(st, _vtype(ret, ctx), m, ctx, x.off)
    elif name in ("iaload", "laload"):
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_REF, ctx, x.off)
        _vpush(st, T_I64 if name == "laload" else T_I32, m, ctx, x.off)
    elif name in ("iastore", "lastore"):
        _vpop(st, T_I64 if name == "lastore" else T_I32, ctx, x.off)
        _vpop(st, T_I32, ctx, x.off)
        _vpop(st, T_REF, ctx, x.off)
    elif name in ("faload", "daload", "fastore", "dastore"):
        raise JavaError(f"J003: {ctx} @{x.off}: float/double arrays are not supported")
    elif name == "newarray":
        _vpop(st, T_I32, ctx, x.off)
        _vpush(st, T_REF, m, ctx, x.off)
    elif name == "arraylength":
        _vpop(st, T_REF, ctx, x.off)
        _vpush(st, T_I32, m, ctx, x.off)
    elif name == "athrow":
        _vpop(st, T_REF, ctx, x.off)
        return []
    elif name in ("ireturn", "lreturn", "areturn", "return"):
        want = {"ireturn": T_I32, "lreturn": T_I64, "areturn": T_REF,
                "return": None}[name]
        if want is not None:
            _vpop(st, want, ctx, x.off)
        if st:
            raise JavaError(f"J002: {ctx} @{x.off}: {len(st)} value(s) left on the stack at return")
        if m.name == "<init>" and any(t == T_UTHIS for t in lc):
            raise JavaError(
                f"J002: {ctx} @{x.off}: the constructor returns with `this` uninitialized")
        return []
    else:
        raise JavaError(f"J003: {ctx} @{x.off}: opcode `{name}` is not supported")

    if nxt >= len(m.code):
        if not ends:
            raise JavaError(f"J002: {ctx} @{x.off}: control falls off the end of the method")
        return []
    if branch is not None:
        return [branch, nxt]
    return [nxt]


def _verify_method(cf: ClassFile, m: Method) -> None:
    ctx = f"{cf.name}.{m.name}{m.desc}"
    if m.code is None:
        return
    if len(m.code) > MAX_CODE:
        raise JavaError(f"J002: {ctx}: code is {len(m.code)} bytes (cap {MAX_CODE})")
    try:
        insns = decode(m.code)
    except JavaError as e:
        raise JavaError(f"J003: {ctx}: {e}") from None
    at = {x.off: x for x in insns}
    starts = set(at)
    params, ret = parse_method_descriptor(m.desc)
    static = bool(m.access & ACC_STATIC)

    lc0 = [T_TOP] * m.max_locals
    slot = 0
    if not static:
        if m.max_locals < 1:
            raise JavaError(f"J002: {ctx}: max_locals is 0 but the method takes `this`")
        lc0[0] = T_UTHIS if m.name == "<init>" else T_REF
        slot = 1
    for t in params:
        vt = _vtype(t, ctx)
        need = 2 if vt == T_I64 else 1
        if slot + need > m.max_locals:
            raise JavaError(f"J002: {ctx}: parameters do not fit in max_locals")
        lc0[slot] = vt
        if need == 2:
            lc0[slot + 1] = T_TOP
        slot += need

    # 异常处理器入口的状态：栈上是抛出的对象，局部变量是**覆盖范围内所有可达点**
    # 的合并。覆盖范围随数据流才知道，所以整体做不动点。
    exc = []
    for start, end, target, ctype in m.exc:
        if not (0 <= start < end <= len(m.code)):
            raise JavaError(f"J002: {ctx}: exception range {start}..{end} is outside the code")
        if target not in starts:
            raise JavaError(f"J002: {ctx}: exception handler {target} is not an instruction")
        if ctype != 0 and (ctype >= len(cf.pool) or not cf.pool[ctype]
                           or cf.pool[ctype][0] != CP_CLASS):
            raise JavaError(f"J002: {ctx}: bad catch_type #{ctype}")
        exc.append((start, end, target))

    state: dict[int, tuple] = {}
    hstate: dict[int, tuple] = {}
    for _round in range(64):
        changed = False
        state = {}
        work = [(0, ((), tuple(lc0)))]
        work += [(t, s) for t, s in hstate.items()]
        while work:
            off, (st, lc) = work.pop()
            if off not in starts:
                raise JavaError(f"J002: {ctx}: branch target {off} is not an instruction")
            if off in state:
                ost, olc = state[off]
                if len(ost) != len(st):
                    raise JavaError(
                        f"J002: {ctx} @{off}: stack depths differ at a merge point")
                st = [_vmerge(a, b, ctx) for a, b in zip(ost, st)]
                lc = [_vmerge(a, b, ctx) for a, b in zip(olc, lc)]
                if state[off] == (tuple(st), tuple(lc)):
                    continue
            state[off] = (tuple(st), tuple(lc))
            s = list(st)
            l = list(lc)
            for succ in _vstep(cf, m, at[off], s, l):
                if succ not in starts:
                    raise JavaError(
                        f"J002: {ctx} @{off}: branch target {succ} is not an instruction")
                work.append((succ, (tuple(s), tuple(l))))
        # 处理器入口 = 覆盖范围内所有可达点的局部变量合并
        for start, end, target in exc:
            loc = None
            for off, (st, lc) in state.items():
                if start <= off < end:
                    loc = list(lc) if loc is None else [
                        _vmerge(a, b, ctx) for a, b in zip(loc, lc)]
            if loc is None:
                continue
            new = ((T_REF,), tuple(loc))
            if hstate.get(target) != new:
                hstate[target] = new
                changed = True
        if not changed:
            break
    else:
        raise JavaError(f"J002: {ctx}: the verifier did not reach a fixed point")

    if m.frames:
        _verify_frames(cf, m, lc0, slot, state, starts)


def _find_member(owner: ClassFile, name: str, desc: str, kind: str):
    src = owner.fields if kind == "field" else owner.methods
    for f in src:
        if f.name == name and f.desc == desc:
            return f
    return None


_SM_TO_T = {SM_TOP: T_TOP, SM_INT: T_I32, SM_LONG: T_I64,
            SM_NULL: T_NULL, SM_UTHIS: T_UTHIS, SM_OBJ: T_REF}


_T_TO_SM = {T_TOP: (SM_TOP, 0), T_I32: (SM_INT, 0), T_I64: (SM_LONG, 0),
            T_REF: (SM_OBJ, 0), T_NULL: (SM_NULL, 0), T_UTHIS: (SM_UTHIS, 0)}


def _t_to_sm(t: str) -> tuple:
    if t in _T_TO_SM:
        return _T_TO_SM[t]
    if t.startswith("uninit@"):
        return (SM_UNINIT, int(t[7:]))
    raise JavaError(f"J002: internal: no stack-map form for {t}")


def _sm_type(v: tuple, ctx: str) -> str:
    tag, info = v
    if tag in (SM_FLOAT, SM_DOUBLE):
        raise JavaError(f"J003: {ctx}: floating-point stack maps are not supported")
    if tag == SM_UNINIT:
        return f"uninit@{info}"
    return _SM_TO_T[tag]


def _compat(a: str, b: str) -> bool:
    # 帧里声明的是"这个槽可赋给什么"。`Top` 在 JVM 的类型格里是顶 —— 任何值都
    # 可以赋给它（赋过去之后这个槽就不可用了），所以帧说 top、推导有值，是合法的。
    if a == b or a == T_TOP:
        return True
    # 引用格点在这里被抹平（`ref` / `null`），所以"可赋给"只要两边都是引用类。
    return a in (T_REF, T_NULL) and b in (T_REF, T_NULL)


def _verify_frames(cf: ClassFile, m: Method, lc0: list, plen: int, state: dict,
                   starts: set) -> None:
    """`StackMapTable` 与推导结果逐槽比对（docs/282 §8.1）。

    只判**声明了的**帧：JVM 还要求每个基本块起点都有帧，那条要额外记前驱 —— 这里
    先不比"缺帧"，比"声明了的帧与推导一致"（写进状态行的仍是缺口）。
    """
    ctx = f"{cf.name}.{m.name}{m.desc}"
    # 帧里的局部变量只列到"用到的最后一个"，入口帧也只是形参那一段 —— 不能拿
    # 补到 max_locals 的整张表去接 append/chop。
    locals_ = [_t_to_sm(t) for t in lc0[:plen]]
    for off, kind, payload in m.frames:
        if kind == "same":
            stack = []
        elif kind == "same1":
            stack = _slots(payload)
        elif kind == "chop":
            k = payload[0]
            if k > len(locals_):
                raise JavaError(
                    f"J002: {ctx} @{off}: StackMapTable chop underflows the locals")
            locals_ = locals_[:len(locals_) - k]
            stack = []
        elif kind == "append":
            locals_ = locals_ + _slots(payload)
            stack = []
        else:
            locals_ = _slots(payload[0])
            stack = _slots(payload[1])
        if len(locals_) > m.max_locals:
            raise JavaError(
                f"J002: {ctx} @{off}: StackMapTable declares more locals than max_locals")
        if off not in starts:
            raise JavaError(
                f"J002: {ctx} @{off}: StackMapTable frame is not on an instruction")
        if off not in state:
            continue                      # 不可达：没有推导值可比
        inferred_stack, inferred_locals = state[off]
        if len(stack) != len(inferred_stack):
            raise JavaError(
                f"J002: {ctx} @{off}: StackMapTable stack depth {len(stack)} "
                f"disagrees with the inferred {len(inferred_stack)}")
        for i, (v, got) in enumerate(zip(stack, inferred_stack)):
            want = _sm_type(v, ctx)
            if not _compat(want, got):
                raise JavaError(
                    f"J002: {ctx} @{off}: StackMapTable stack slot {i} is {want}, "
                    f"inferred {got}")
        for i in range(max(len(locals_), len(inferred_locals))):
            v = locals_[i] if i < len(locals_) else (SM_TOP, 0)
            want = _sm_type(v, ctx)
            got = inferred_locals[i] if i < len(inferred_locals) else T_TOP
            if not _compat(want, got):
                raise JavaError(
                    f"J002: {ctx} @{off}: StackMapTable local {i} is {want}, "
                    f"inferred {got}")


def _resolve_member(cls: str, name: str, desc: str, kind: str, registry: dict):
    """沿超类链找成员。返回 (声明它的类名, 成员)；链走出闭包就返回 (None, None)。"""
    cur = registry.get(cls)
    steps = 0
    while cur is not None and steps < 64:
        f = _find_member(cur, name, desc, kind)
        if f is not None:
            return cur.name, f
        cur = registry.get(cur.super_name) if cur.super_name else None
        steps += 1
    return None, None


def _package(name: str) -> str:
    return name.rsplit("/", 1)[0] if "/" in name else ""


def _is_subclass(name: str, owner: str, registry: dict) -> bool:
    cur = registry.get(name)
    steps = 0
    while cur is not None and cur.super_name is not None and steps < 64:
        if cur.super_name == owner:
            return True
        cur = registry.get(cur.super_name)
        steps += 1
    return False


def _access_word(access: int) -> str:
    if access & ACC_PRIVATE:
        return "private"
    if access & ACC_PROTECTED:
        return "protected"
    if access & ACC_PUBLIC:
        return "public"
    return "package-private"


def _visible(accessor: str, owner: str, access: int, registry: dict) -> bool:
    if access & ACC_PUBLIC:
        return True
    if access & ACC_PRIVATE:
        return accessor == owner
    same_pkg = _package(accessor) == _package(owner)
    if access & ACC_PROTECTED:
        return same_pkg or accessor == owner or _is_subclass(accessor, owner, registry)
    # 包内可见
    return same_pkg


def _verify_access(cf: ClassFile, m: Method, registry: dict) -> None:
    """访问控制（docs/282 §8.1）：闭包内能解析到的成员才判；链走出闭包就跳过。"""
    ctx = f"{cf.name}.{m.name}{m.desc}"
    for x in decode(m.code):
        name = _OPS[x.op][0]
        if name in ("getfield", "putfield", "getstatic", "putstatic"):
            cls, fname, desc = fieldref(cf.pool, x.args[0])
            owner, f = _resolve_member(cls, fname, desc, "field", registry)
            if owner is None:
                continue
            if not _visible(cf.name, owner, f.access, registry):
                raise JavaError(
                    f"J002: {ctx} @{x.off}: {owner}.{fname} is "
                    f"{_access_word(f.access)} and not visible here")
            if f.access & ACC_FINAL and name in ("putfield", "putstatic"):
                ok = (owner == cf.name and
                      ((name == "putfield" and m.name == "<init>") or
                       (name == "putstatic" and m.name == "<clinit>")))
                if not ok:
                    raise JavaError(
                        f"J002: {ctx} @{x.off}: {owner}.{fname} is final and assigned "
                        f"outside its initializer")
        elif name in ("invokestatic", "invokevirtual", "invokespecial"):
            cls, mname, desc = methodref(cf.pool, x.args[0])
            owner, mm = _resolve_member(cls, mname, desc, "method", registry)
            if owner is None:
                continue
            if not _visible(cf.name, owner, mm.access, registry):
                raise JavaError(
                    f"J002: {ctx} @{x.off}: {owner}.{mname}{desc} is "
                    f"{_access_word(mm.access)} and not visible here")


def verify_classes(classes: list[ClassFile]) -> None:
    """拒绝降级不了的类：结构、控制流、操作数类型、访问控制、资源上限（docs/282 §8）。"""
    if len(classes) > MAX_CLASSES:
        raise JavaError(f"J002: {len(classes)} classes (cap {MAX_CLASSES})")
    names = set()
    for cf in classes:
        if cf.name in names:
            raise JavaError(f"J002: class {cf.name} appears twice")
        names.add(cf.name)
    registry = {cf.name: cf for cf in classes}
    for cf in classes:
        for m in cf.methods:
            if m.code is None:
                continue
            if len(m.code) > MAX_CODE:
                raise JavaError(
                    f"J002: {cf.name}.{m.name}{m.desc}: code is {len(m.code)} bytes "
                    f"(cap {MAX_CODE})")
            _verify_method(cf, m)
            _verify_access(cf, m, registry)


# ---------------------------------------------------------------- IR emitter

def _alloc_sym(module: str) -> str:
    return f"__jrt_alloc_{module}"


def _jrt_alloc_ir(module: str) -> str:
    """A minimal per-module heap: one MiB of zeroed .bss and a bump pointer.

    Per-module symbols avoid a duplicate definition when two libraries are
    linked before a shared `jrt.ll` exists. There is no collection yet.
    """
    sym = _alloc_sym(module)
    return f'''@__jrt_heap_{module} = internal global [1048576 x i8] zeroinitializer
@__jrt_off_{module} = internal global i32 0
@__jrt_exc_{module} = internal global ptr zeroinitializer

define internal ptr @{sym}(i32 %n) {{
entry:
  %o = load i32, ptr @__jrt_off_{module}
  %p = add i32 %o, 8
  %e = add i32 %p, %n
  store i32 %e, ptr @__jrt_off_{module}
  %q = getelementptr [1048576 x i8], ptr @__jrt_heap_{module}, i32 0, i32 %p
  ret ptr %q
}}
'''


_JRT_DECLS = """declare void @jrt_print_i32(i64, i32)
declare void @jrt_println_i32(i64, i32)
declare void @jrt_print_i64(i64, i64)
declare void @jrt_println_i64(i64, i64)
declare void @jrt_print_s(i64, ptr)
declare void @jrt_println_s(i64, ptr)
declare void @jrt_println(i64)"""


def mangle(module: str, cls: str, name: str, desc: str) -> str:
    h = hashlib.sha256(f"{cls}.{name}{desc}".encode()).hexdigest()[:8]
    # `<init>` is not a legal LLVM symbol character; keep the digest (which is
    # computed over the real name) and sanitize only the readable prefix.
    safe = re.sub(r"[^A-Za-z0-9_.$-]", "_", name)
    return f"__jl_{module}_{safe}_{h}"


def _slot_local(name: str, ty: str) -> str:
    # A JVM local slot may be reused with a different type (e.g. `int i` twice,
    # then the same slot holds a `long`). Give each (slot, type) pair its own
    # storage; the verifier guarantees a slot is written before it is read, so
    # this preserves semantics without a full local-type lattice.
    return f"%loc{name}_{ty}"


class Emitter:
    def __init__(self, insns: list[Insn], heights: dict[int, int], max_stack: int,
                 params: list[str], param_slots: list[int], ret: str, pool: list,
                 module: str, own_class: str, registry: dict, metas: dict,
                 class_ids: dict, globals_: list, exc: list | None = None):
        self.insns = insns
        self.heights = heights
        self.max_stack = max_stack
        self.params = params
        self.param_slots = param_slots
        self.ret = ret
        self.pool = pool
        self.module = module
        self.own_class = own_class
        self.registry = registry
        self.metas = metas
        #: 对象的头 4 字节是类号（0 = 非 `new` 出来的东西：String、数组、异常垫片）。
        self.class_ids = class_ids
        self.globals = globals_
        self.exc = exc or []
        self.strn = 0
        self.lines: list[str] = []
        self.tmp = 0
        self.lbl = 0
        self.locals: dict[tuple[int, str], str] = {}

    def t(self) -> str:
        self.tmp += 1
        return f"%t{self.tmp}"

    # ---- String.equals / hashCode, inline over the UTF-16 bytes ----------
    #
    # Bytes rather than `i16` loads: both linkers already handle `load i8`
    # (every string path uses it), while a 16-bit load is one more instruction
    # shape to keep in agreement.

    def _str_units(self, s: str, i: str) -> tuple[str, str]:
        """Return (lo, hi) bytes of code unit `i` of String value `s`."""
        d = self.t()
        q = self.t()
        self.emit(f"{d} = getelementptr i8, ptr {s}, i32 8")
        self.emit(f"{d} = load ptr, ptr {d}")
        self.emit(f"{q} = mul i32 {i}, 2")
        p = self.t()
        self.emit(f"{p} = getelementptr i8, ptr {d}, i32 {q}")
        lo = self.t()
        self.emit(f"{lo} = load i8, ptr {p}")
        q2 = self.t()
        self.emit(f"{q2} = add i32 {q}, 1")
        p2 = self.t()
        self.emit(f"{p2} = getelementptr i8, ptr {d}, i32 {q2}")
        hi = self.t()
        self.emit(f"{hi} = load i8, ptr {p2}")
        return lo, hi

    def _str_len(self, s: str) -> str:
        q = self.t()
        v = self.t()
        self.emit(f"{q} = getelementptr i8, ptr {s}, i32 16")
        self.emit(f"{v} = load i32, ptr {q}")
        return v

    def _string_equals(self, recv: str, other: str) -> str:
        n = self.lbl
        self.lbl += 1
        r = self.t()
        self.emit(f"{r} = alloca i32")
        self.emit(f"store i32 0, ptr {r}")
        oi = self.t()
        self.emit(f"{oi} = ptrtoint ptr {other} to i64")
        iz = self.t()
        self.emit(f"{iz} = icmp eq i64 {oi}, 0")
        self.emit(f"br i1 {iz}, label %SE{n}D, label %SE{n}L")
        self.emit(f"SE{n}L:")
        l1 = self._str_len(recv)
        l2 = self._str_len(other)
        le = self.t()
        self.emit(f"{le} = icmp eq i32 {l1}, {l2}")
        self.emit(f"br i1 {le}, label %SE{n}I, label %SE{n}D")
        self.emit(f"SE{n}I:")
        i = self.t()
        self.emit(f"{i} = alloca i32")
        self.emit(f"store i32 0, ptr {i}")
        self.emit(f"br label %SE{n}P")
        self.emit(f"SE{n}P:")
        iv = self.t()
        self.emit(f"{iv} = load i32, ptr {i}")
        go = self.t()
        self.emit(f"{go} = icmp slt i32 {iv}, {l1}")
        self.emit(f"br i1 {go}, label %SE{n}B, label %SE{n}S")
        self.emit(f"SE{n}B:")
        a1, b1 = self._str_units(recv, iv)
        a2, b2 = self._str_units(other, iv)
        c1 = self.t()
        c2 = self.t()
        ce = self.t()
        self.emit(f"{c1} = icmp eq i8 {a1}, {a2}")
        self.emit(f"{c2} = icmp eq i8 {b1}, {b2}")
        self.emit(f"{ce} = and i1 {c1}, {c2}")
        self.emit(f"br i1 {ce}, label %SE{n}N, label %SE{n}D")
        self.emit(f"SE{n}N:")
        nv = self.t()
        self.emit(f"{nv} = add i32 {iv}, 1")
        self.emit(f"store i32 {nv}, ptr {i}")
        self.emit(f"br label %SE{n}P")
        self.emit(f"SE{n}S:")
        self.emit(f"store i32 1, ptr {r}")
        self.emit(f"br label %SE{n}D")
        self.emit(f"SE{n}D:")
        rv = self.t()
        self.emit(f"{rv} = load i32, ptr {r}")
        self._push("i32", rv, 0)
        return rv

    def _string_hash(self, recv: str) -> str:
        n = self.lbl
        self.lbl += 1
        h = self.t()
        self.emit(f"{h} = alloca i32")
        self.emit(f"store i32 0, ptr {h}")
        l1 = self._str_len(recv)
        i = self.t()
        self.emit(f"{i} = alloca i32")
        self.emit(f"store i32 0, ptr {i}")
        self.emit(f"br label %SH{n}P")
        self.emit(f"SH{n}P:")
        iv = self.t()
        self.emit(f"{iv} = load i32, ptr {i}")
        go = self.t()
        self.emit(f"{go} = icmp slt i32 {iv}, {l1}")
        self.emit(f"br i1 {go}, label %SH{n}B, label %SH{n}D")
        self.emit(f"SH{n}B:")
        lo, hi = self._str_units(recv, iv)
        h0 = self.t()
        hv = self.t()
        cu = self.t()
        hiw = self.t()
        h1 = self.t()
        h2 = self.t()
        nv = self.t()
        self.emit(f"{h0} = load i32, ptr {h}")
        self.emit(f"{hv} = mul i32 {h0}, 31")
        self.emit(f"{cu} = zext i8 {lo} to i32")
        self.emit(f"{hiw} = zext i8 {hi} to i32")
        self.emit(f"{hiw} = shl i32 {hiw}, 8")
        self.emit(f"{h1} = add i32 {hv}, {cu}")
        self.emit(f"{h2} = add i32 {h1}, {hiw}")
        self.emit(f"store i32 {h2}, ptr {h}")
        self.emit(f"{nv} = add i32 {iv}, 1")
        self.emit(f"store i32 {nv}, ptr {i}")
        self.emit(f"br label %SH{n}P")
        self.emit(f"SH{n}D:")
        rv = self.t()
        self.emit(f"{rv} = load i32, ptr {h}")
        self._push("i32", rv, 0)
        return rv

    def emit(self, s: str) -> None:
        self.lines.append("  " + s)

    def local(self, idx: int, ty: str) -> str:
        self.locals[(idx, ty)] = ty
        return ty

    def run(self) -> str:
        # entry: allocate locals and operand-stack slots, copy parameters.
        # JVM local slots are not value indices: a long/double occupies two
        # slots. `param_slots` maps each descriptor parameter to its slot.
        for slot, ty in zip(self.param_slots, self.params):
            self.local(slot, ty)
        # Discover local types from the bytecode before emitting allocas.
        self._scan_locals()
        for idx, ty in sorted(self.locals):
            self.emit(f"{_slot_local(str(idx), ty)} = alloca {ty}")
        for i, (slot, ty) in enumerate(zip(self.param_slots, self.params)):
            self.emit(f"store {ty} %p{i}, ptr {_slot_local(str(slot), ty)}")
        for k in range(self.max_stack):
            # The stack slot type is discovered from the instruction that writes
            # it.  For the first slice every value is i32/i64; allocate i64 and
            # use trunc/sext around narrow values would be wasteful, so use i32
            # unless a long is present.
            self.emit(f"%stk{k} = alloca i64")
        self.emit(f"br label %L{self.insns[0].off}" if self.insns else "ret void")
        for x in self.insns:
            self._insn(x)
        return "\n".join(self.lines)

    # ---- 继承：字段与实现都沿超类链找 -------------------------------------

    def _field(self, cls: str, name: str, static: bool):
        """沿超类链找字段，返回它**声明类**给出的 (偏移/符号, IR 类型)。"""
        cur = cls
        steps = 0
        while cur in self.registry and steps < 64:
            meta = self.metas.get(cur)
            if meta is not None:
                got = (meta.static_layout if static else meta.layout).get(name)
                if got is not None:
                    return got
            cur = self.registry[cur].super_name
            steps += 1
        return None

    def _impl(self, cls: str, name: str, desc: str):
        """沿超类链找方法实现，返回 (声明类名, Method)。"""
        cur = cls
        steps = 0
        while cur in self.registry and steps < 64:
            cf = self.registry[cur]
            for mm in cf.methods:
                if mm.name == name and mm.desc == desc:
                    return cur, mm
            cur = cf.super_name
            steps += 1
        return None, None

    def _is_sub(self, name: str, base: str) -> bool:
        cur = name
        steps = 0
        while cur in self.registry and steps < 64:
            if cur == base:
                return True
            cur = self.registry[cur].super_name
            steps += 1
        return False

    def _virtual_call(self, x, cls: str, mname: str, desc: str, recv: str,
                      params: list, ret: str, vals: list, h0: int) -> None:
        """闭世界去虚化：按对象的类号分派到**直调**（docs/282 §6.3）。

        候选 = 闭包里所有 `cls` 的子类（含自己），取各自沿超类链解析出来的实现。
        只有一个实现时就是一次直调；多个时按类号比较，最后落到引用类自己的实现
        （闭世界里不会出现别的类号，那条分支是给"头是 0"的垫片留的）。
        """
        default_sym = None
        entries: list[tuple[int, str]] = []
        for name in sorted(self.registry):
            if not self._is_sub(name, cls):
                continue
            owner, mm = self._impl(name, mname, desc)
            if owner is None or mm.code is None:
                continue
            sym = mangle(self.module, owner, mname, desc)
            if name == cls:
                default_sym = sym
            entries.append((self.class_ids[name], sym))
        if not entries:
            raise JavaError(
                f"invokevirtual {cls}.{mname}{desc}: no implementation in the linked set")
        if default_sym is None:
            default_sym = entries[0][1]
        argtxt = ", ".join([f"ptr {recv}"] +
                           [f"{t} {v}" for t, v in zip(params, vals)])
        switch = [(cid, sym) for cid, sym in entries if sym != default_sym]
        if not switch:
            if ret == "void":
                self.emit(f"call void @{default_sym}({argtxt})")
            else:
                r = self.t()
                self.emit(f"{r} = call {ret} @{default_sym}({argtxt})")
                self._push(ret, r, h0 - len(params) - 1)
            return
        lbl = self.lbl
        self.lbl += 1
        cidv = self.t()
        self.emit(f"{cidv} = load i32, ptr {recv}")
        self.emit(f"br label %V{lbl}_c0")
        for i, (want, sym) in enumerate(switch):
            self.lines.append(f"V{lbl}_c{i}:")
            c = self.t()
            self.emit(f"{c} = icmp eq i32 {cidv}, {want}")
            self.emit(f"br i1 {c}, label %V{lbl}_t{i}, label %V{lbl}_c{i + 1}")
            self.lines.append(f"V{lbl}_t{i}:")
            self._emit_call(sym, argtxt, ret, h0 - len(params) - 1)
            self.emit(f"br label %V{lbl}_end")
        self.lines.append(f"V{lbl}_c{len(switch)}:")
        self._emit_call(default_sym, argtxt, ret, h0 - len(params) - 1)
        self.emit(f"br label %V{lbl}_end")
        self.lines.append(f"V{lbl}_end:")

    def _emit_call(self, sym: str, argtxt: str, ret: str, slot: int) -> None:
        if ret == "void":
            self.emit(f"call void @{sym}({argtxt})")
        else:
            r = self.t()
            self.emit(f"{r} = call {ret} @{sym}({argtxt})")
            self._push(ret, r, slot)

    def _scan_locals(self) -> None:
        for x in self.insns:
            name = _OPS[x.op][0]
            if name in ("lload", "lstore"):
                self.local(x.args[0], "i64")
            elif name in ("iload", "istore"):
                self.local(x.args[0], "i32")
            elif name.startswith("lload_") or name.startswith("lstore_"):
                self.local(int(name[-1]), "i64")
            elif name.startswith("iload_") or name.startswith("istore_"):
                self.local(int(name[-1]), "i32")
            elif name in ("aload", "astore"):
                self.local(x.args[0], "ptr")
            elif name.startswith("aload_") or name.startswith("astore_"):
                self.local(int(name[-1]), "ptr")
            elif name.startswith("fload") or name.startswith("fstore"):
                raise JavaError("float locals are not supported in the first slice")
            elif name.startswith("dload") or name.startswith("dstore"):
                raise JavaError("double locals are not supported in the first slice")

    def _push(self, ty: str, val: str, h: int) -> None:
        # Every operand-stack slot is i64; i32 and ptr values are converted at
        # the boundary. One slot width is enough for i32/i64/ptr and keeps the
        # emitter independent of a full JVM type lattice for now.
        if ty == "i32":
            x = self.t()
            self.emit(f"{x} = sext i32 {val} to i64")
            self.emit(f"store i64 {x}, ptr %stk{h}")
        elif ty == "i64":
            self.emit(f"store i64 {val}, ptr %stk{h}")
        elif ty == "ptr":
            x = self.t()
            self.emit(f"{x} = ptrtoint ptr {val} to i64")
            self.emit(f"store i64 {x}, ptr %stk{h}")
        else:
            raise JavaError(f"operand-stack type {ty} is not supported")

    def _pop(self, ty: str, h: int) -> str:
        v = self.t()
        self.emit(f"{v} = load i64, ptr %stk{h}")
        if ty == "i32":
            x = self.t()
            self.emit(f"{x} = trunc i64 {v} to i32")
            return x
        if ty == "i64":
            return v
        if ty == "ptr":
            x = self.t()
            self.emit(f"{x} = inttoptr i64 {v} to ptr")
            return x
        raise JavaError(f"operand-stack type {ty} is not supported")

    def _check_exc(self, x: Insn) -> None:
        """After a call (or `athrow`): if the module exception slot is set,
        jump to the innermost handler covering this bytecode offset, or return
        a default value so the caller can propagate it."""
        e = self.t()
        self.emit(f"{e} = load ptr, ptr @__jrt_exc_{self.module}")
        is_ = self.t()
        self.emit(f"{is_} = icmp ne ptr {e}, null")
        handler = None
        for start, end, target, _typ in self.exc:
            if start <= x.off < end:
                handler = target
                break
        self.emit(f"br i1 {is_}, label %E{x.off}, label %N{x.off}")
        self.lines.append(f"E{x.off}:")
        if handler is not None:
            self.emit(f"store ptr null, ptr @__jrt_exc_{self.module}")
            self._push("ptr", e, 0)
            self.emit(f"br label %L{handler}")
        elif self.ret == "void":
            self.emit("ret void")
        elif self.ret in ("i32", "i64"):
            self.emit(f"ret {self.ret} 0")
        else:
            raise JavaError(f"exception propagation does not support a {self.ret} return")
        self.lines.append(f"N{x.off}:")

    def _insn(self, x: Insn) -> None:
        name = _OPS[x.op][0]
        h0 = self.heights.get(x.off)
        if h0 is None:
            return  # unreachable
        self.lines.append(f"L{x.off}:")
        if name in ("iconst_m1", "iconst_0", "iconst_1", "iconst_2", "iconst_3",
                    "iconst_4", "iconst_5"):
            v = {"iconst_m1": -1, "iconst_0": 0, "iconst_1": 1, "iconst_2": 2,
                 "iconst_3": 3, "iconst_4": 4, "iconst_5": 5}[name]
            self._push("i32", str(v), h0)
        elif name == "bipush":
            # The byte operand is signed; the decoder keeps it unsigned so that
            # `ldc`/`iload` indices (the other users of "b") stay indices.
            v = x.args[0]
            if v >= 128:
                v -= 256
            self._push("i32", str(v), h0)
        elif name == "sipush":
            self._push("i32", str(x.args[0]), h0)
        elif name in ("ldc", "ldc_w"):
            ent = self.pool[x.args[0]] if x.args[0] < len(self.pool) else None
            if ent and ent[0] == CP_INTEGER:
                self._push("i32", str(ent[1]), h0)
            elif ent and ent[0] == CP_STRING:
                s = _utf8(self.pool, ent[1])
                data = s.encode("utf-16-le")
                nbytes = len(data) if data else 1
                if not data:
                    data = b"\x00"
                sym = f"__jl_str_{self.module}_{hashlib.sha256(data).hexdigest()[:10]}"
                esc = "".join(f"\\{b:02x}" for b in data)
                if not any(g.startswith(f"@{sym} ") for g in self.globals):
                    self.globals.append(
                        f'@{sym} = internal constant [{nbytes} x i8] c"{esc}"')
                d = self.t()
                self.emit(f"{d} = getelementptr [{nbytes} x i8], ptr @{sym}, i32 0, i32 0")
                p = self.t()
                self.emit(f"{p} = call ptr @{_alloc_sym(self.module)}(i32 {32 + nbytes})")
                q = self.t()
                self.emit(f"{q} = getelementptr i8, ptr {p}, i32 8")
                self.emit(f"store ptr {d}, ptr {q}")
                r = self.t()
                self.emit(f"{r} = getelementptr i8, ptr {p}, i32 16")
                self.emit(f"store i32 {len(data) // 2}, ptr {r}")
                self._push("ptr", p, h0)
            else:
                raise JavaError(f"{name} #{x.args[0]} is not an int/string constant")
        elif name == "ldc2_w":
            ent = self.pool[x.args[0]] if x.args[0] < len(self.pool) else None
            if not ent or ent[0] != CP_LONG:
                raise JavaError(f"ldc2_w #{x.args[0]} is not a long constant")
            self._push("i64", str(ent[1]), h0)
        elif name in ("lconst_0", "lconst_1"):
            self._push("i64", "0" if name == "lconst_0" else "1", h0)
        elif name in ("iload", "lload", "iload_0", "iload_1", "iload_2", "iload_3",
                      "lload_0", "lload_1", "lload_2", "lload_3"):
            idx = x.args[0] if x.args else int(name[-1])
            ty = "i64" if name.startswith("l") else "i32"
            v = self.t()
            self.emit(f"{v} = load {ty}, ptr {_slot_local(str(idx), ty)}")
            self._push(ty, v, h0)
        elif name in ("aload", "aload_0", "aload_1", "aload_2", "aload_3"):
            idx = x.args[0] if x.args else int(name[-1])
            v = self.t()
            self.emit(f"{v} = load ptr, ptr {_slot_local(str(idx), 'ptr')}")
            self._push("ptr", v, h0)
        elif name in ("istore", "istore_0", "istore_1", "istore_2", "istore_3"):
            idx = x.args[0] if x.args else int(name[-1])
            v = self._pop("i32", h0 - 1)
            self.emit(f"store i32 {v}, ptr {_slot_local(str(idx), 'i32')}")
        elif name in ("astore", "astore_0", "astore_1", "astore_2", "astore_3"):
            idx = x.args[0] if x.args else int(name[-1])
            v = self._pop("ptr", h0 - 1)
            self.emit(f"store ptr {v}, ptr {_slot_local(str(idx), 'ptr')}")
        elif name in ("lstore", "lstore_0", "lstore_1", "lstore_2", "lstore_3"):
            idx = x.args[0] if x.args else int(name[-1])
            v = self._pop("i64", h0 - 1)
            self.emit(f"store i64 {v}, ptr {_slot_local(str(idx), 'i64')}")
        elif name == "iinc":
            idx, delta = x.args
            v = self.t()
            self.emit(f"{v} = load i32, ptr {_slot_local(str(idx), 'i32')}")
            n = self.t()
            self.emit(f"{n} = add i32 {v}, {delta}")
            self.emit(f"store i32 {n}, ptr {_slot_local(str(idx), 'i32')}")
        elif name in ("iadd", "isub", "imul", "iand", "ior", "ixor"):
            b = self._pop("i32", h0 - 1)
            a = self._pop("i32", h0 - 2)
            op = {"iadd": "add", "isub": "sub", "imul": "mul",
                  "iand": "and", "ior": "or", "ixor": "xor"}[name]
            r = self.t()
            self.emit(f"{r} = {op} i32 {a}, {b}")
            self._push("i32", r, h0 - 2)
        elif name in ("ladd", "lsub", "lmul", "land", "lor", "lxor"):
            b = self._pop("i64", h0 - 1)
            a = self._pop("i64", h0 - 2)
            op = {"ladd": "add", "lsub": "sub", "lmul": "mul",
                  "land": "and", "lor": "or", "lxor": "xor"}[name]
            r = self.t()
            self.emit(f"{r} = {op} i64 {a}, {b}")
            self._push("i64", r, h0 - 2)
        elif name in ("idiv", "irem", "ldiv", "lrem"):
            ty = "i64" if name.startswith("l") else "i32"
            b = self._pop(ty, h0 - 1)
            a = self._pop(ty, h0 - 2)
            op = "sdiv" if name.endswith("div") else "srem"
            r = self.t()
            self.emit(f"{r} = {op} {ty} {a}, {b}")
            self._push(ty, r, h0 - 2)
        elif name in ("ineg", "lneg"):
            ty = "i64" if name == "lneg" else "i32"
            a = self._pop(ty, h0 - 1)
            r = self.t()
            self.emit(f"{r} = sub {ty} 0, {a}")
            self._push(ty, r, h0 - 1)
        elif name in ("ishl", "ishr", "iushr", "lshl", "lshr", "lushr"):
            ty = "i64" if name.startswith("l") else "i32"
            b = self._pop("i32", h0 - 1)   # the shift distance is always int
            a = self._pop(ty, h0 - 2)
            m = self.t()
            if ty == "i64":
                bb = self.t()
                self.emit(f"{bb} = sext i32 {b} to i64")
                self.emit(f"{m} = and i64 {bb}, 63")
            else:
                self.emit(f"{m} = and i32 {b}, 31")
            r = self.t()
            op = {"ishl": "shl", "ishr": "ashr", "iushr": "lshr",
                  "lshl": "shl", "lshr": "ashr", "lushr": "lshr"}[name]
            self.emit(f"{r} = {op} {ty} {a}, {m}")
            self._push(ty, r, h0 - 2)
        elif name == "lcmp":
            b = self._pop("i64", h0 - 1)
            a = self._pop("i64", h0 - 2)
            lt = self.t(); gt = self.t(); r1 = self.t(); r2 = self.t(); r = self.t()
            self.emit(f"{lt} = icmp slt i64 {a}, {b}")
            self.emit(f"{gt} = icmp sgt i64 {a}, {b}")
            self.emit(f"{r1} = zext i1 {lt} to i32")
            self.emit(f"{r2} = zext i1 {gt} to i32")
            self.emit(f"{r} = sub i32 {r2}, {r1}")
            self._push("i32", r, h0 - 2)
        elif name == "i2l":
            a = self._pop("i32", h0 - 1)
            r = self.t()
            self.emit(f"{r} = sext i32 {a} to i64")
            self._push("i64", r, h0 - 1)
        elif name == "l2i":
            a = self._pop("i64", h0 - 1)
            r = self.t()
            self.emit(f"{r} = trunc i64 {a} to i32")
            self._push("i32", r, h0 - 1)
        elif name in ("i2b", "i2c", "i2s"):
            a = self._pop("i32", h0 - 1)
            narrow = {"i2b": ("i8", "sext"), "i2c": ("i16", "zext"),
                      "i2s": ("i16", "sext")}[name]
            t0 = self.t(); r = self.t()
            self.emit(f"{t0} = trunc i32 {a} to {narrow[0]}")
            self.emit(f"{r} = {narrow[1]} {narrow[0]} {t0} to i32")
            self._push("i32", r, h0 - 1)
        elif name in ("ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle"):
            v = self._pop("i32", h0 - 1)
            cond = {"ifeq": "eq", "ifne": "ne", "iflt": "slt",
                    "ifge": "sge", "ifgt": "sgt", "ifle": "sle"}[name]
            c = self.t()
            self.emit(f"{c} = icmp {cond} i32 {v}, 0")
            self.emit(f"br i1 {c}, label %L{x.args[0]}, label %L{x.off + x.size}")
        elif name in ("if_icmpeq", "if_icmpne", "if_icmplt", "if_icmpge",
                      "if_icmpgt", "if_icmple"):
            b = self._pop("i32", h0 - 1)
            a = self._pop("i32", h0 - 2)
            cond = {"if_icmpeq": "eq", "if_icmpne": "ne", "if_icmplt": "slt",
                    "if_icmpge": "sge", "if_icmpgt": "sgt",
                    "if_icmple": "sle"}[name]
            c = self.t()
            self.emit(f"{c} = icmp {cond} i32 {a}, {b}")
            self.emit(f"br i1 {c}, label %L{x.args[0]}, label %L{x.off + x.size}")
        elif name == "dup":
            v = self.t()
            self.emit(f"{v} = load i64, ptr %stk{h0 - 1}")
            self.emit(f"store i64 {v}, ptr %stk{h0}")
        elif name in ("pop", "pop2"):
            pass
        elif name == "new":
            cls = _class_name(self.pool, x.args[0])
            meta = self.metas.get(cls)
            if meta is not None:
                size = meta.size
            elif cls in ("java/lang/RuntimeException", "java/lang/Exception",
                         "java/lang/Throwable"):
                size = 8
            else:
                raise JavaError(f"new {cls}: class is not in the linked set")
            p = self.t()
            self.emit(f"{p} = call ptr @{_alloc_sym(self.module)}(i32 {size})")
            # 对象头的前 4 字节是类号 —— 虚分派就靠它（`String`/数组/异常垫片
            # 不是 `new` 出来的，头是 0，不走分派）。
            self.emit(f"store i32 {self.class_ids.get(cls, 0)}, ptr {p}")
            self._push("ptr", p, h0)
        elif name in ("getstatic", "putstatic"):
            cls, fname, desc = fieldref(self.pool, x.args[0])
            # `System.out` / `System.err` are PrintStream objects in Java; here
            # they are just the file descriptor, carried as a pointer constant.
            if (name == "getstatic" and cls == "java/lang/System"
                    and fname in ("out", "err") and desc == "Ljava/io/PrintStream;"):
                fd = 1 if fname == "out" else 2
                v = self.t()
                self.emit(f"{v} = inttoptr i64 {fd} to ptr")
                self._push("ptr", v, h0)
            else:
                gotf = self._field(cls, fname, True)
                if gotf is None:
                    raise JavaError(
                        f"{name} {cls}.{fname}: static field is not in the linked set")
                sym, ty = gotf
                if name == "getstatic":
                    v = self.t()
                    self.emit(f"{v} = load {ty}, ptr @{sym}")
                    self._push(ty, v, h0)
                else:
                    v = self._pop(ty, h0 - 1)
                    self.emit(f"store {ty} {v}, ptr @{sym}")
        elif name == "getfield":
            cls, fname, desc = fieldref(self.pool, x.args[0])
            gotf = self._field(cls, fname, False)
            if gotf is None:
                raise JavaError(f"getfield {cls}.{fname}: field is not in the linked set")
            off, ty = gotf
            obj = self._pop("ptr", h0 - 1)
            q = self.t()
            self.emit(f"{q} = getelementptr i8, ptr {obj}, i32 {off}")
            v = self.t()
            self.emit(f"{v} = load {ty}, ptr {q}")
            self._push(ty, v, h0 - 1)
        elif name == "putfield":
            cls, fname, desc = fieldref(self.pool, x.args[0])
            gotf = self._field(cls, fname, False)
            if gotf is None:
                raise JavaError(f"putfield {cls}.{fname}: field is not in the linked set")
            off, ty = gotf
            val = self._pop(ty, h0 - 1)
            obj = self._pop("ptr", h0 - 2)
            q = self.t()
            self.emit(f"{q} = getelementptr i8, ptr {obj}, i32 {off}")
            self.emit(f"store {ty} {val}, ptr {q}")
        elif name == "invokevirtual":
            cls, mname, desc = methodref(self.pool, x.args[0])
            params, ret = parse_method_descriptor(desc)
            n = len(params)
            vals = [""] * n
            for i in range(n - 1, -1, -1):
                vals[i] = self._pop(params[i], h0 - n + i)
            recv = self._pop("ptr", h0 - n - 1)
            if cls == "java/io/PrintStream" and mname in ("print", "println"):
                fd = self.t()
                self.emit(f"{fd} = ptrtoint ptr {recv} to i64")
                nl = "ln" if mname == "println" else ""
                if n == 0 and mname == "println":
                    self.emit(f"call void @jrt_println(i64 {fd})")
                elif n == 1 and params[0] == "i32":
                    self.emit(f"call void @jrt_print{nl}_i32(i64 {fd}, i32 {vals[0]})")
                elif n == 1 and params[0] == "i64":
                    self.emit(f"call void @jrt_print{nl}_i64(i64 {fd}, i64 {vals[0]})")
                elif n == 1 and params[0] == "ptr":
                    self.emit(f"call void @jrt_print{nl}_s(i64 {fd}, ptr {vals[0]})")
                else:
                    raise JavaError(
                        f"PrintStream.{mname}{desc} is not supported yet "
                        f"(only int, long, String, and println())")
            elif cls in self.registry:
                self._virtual_call(x, cls, mname, desc, recv, params, ret, vals, h0)
            elif cls == "java/lang/String" and mname == "equals" and desc == "(Ljava/lang/Object;)Z":
                self._string_equals(recv, vals[0])
            elif cls == "java/lang/String" and mname == "hashCode" and desc == "()I":
                self._string_hash(recv)
            elif cls == "java/lang/String" and mname == "length" and desc == "()I":
                q = self.t()
                self.emit(f"{q} = getelementptr i8, ptr {recv}, i32 16")
                v = self.t()
                self.emit(f"{v} = load i32, ptr {q}")
                self._push("i32", v, h0 - n - 1)
            elif cls == "java/lang/String" and mname == "charAt" and desc == "(I)C":
                q = self.t(); off = self.t(); c = self.t(); z = self.t()
                self.emit(f"{q} = getelementptr i8, ptr {recv}, i32 8")
                self.emit(f"{q} = load ptr, ptr {q}")
                self.emit(f"{off} = mul i32 {vals[0]}, 2")
                self.emit(f"{c} = getelementptr i8, ptr {q}, i32 {off}")
                self.emit(f"{c} = load i16, ptr {c}")
                self.emit(f"{z} = zext i16 {c} to i32")
                self._push("i32", z, h0 - n - 1)
            else:
                raise JavaError(
                    f"invokevirtual {cls}.{mname}{desc} is not supported yet")
            self._check_exc(x)
        elif name == "invokespecial":
            cls, mname, desc = methodref(self.pool, x.args[0])
            if mname == "<init>" and cls == "java/lang/Object":
                self._pop("ptr", h0 - 1)
                return
            if mname == "<init>" and cls in (
                    "java/lang/RuntimeException", "java/lang/Exception",
                    "java/lang/Throwable"):
                params, _ret = parse_method_descriptor(desc)
                n = len(params)
                for i in range(n - 1, -1, -1):
                    self._pop(params[i], h0 - n + i)
                self._pop("ptr", h0 - n - 1)
                return
            if cls in self.registry:
                owner, mm2 = self._impl(cls, mname, desc)
                if owner is None or mm2.code is None:
                    raise JavaError(
                        f"invokespecial {cls}.{mname}{desc}: no definition in the linked set")
                params, ret = parse_method_descriptor(desc)
                n = len(params)
                vals = [""] * n
                for i in range(n - 1, -1, -1):
                    vals[i] = self._pop(params[i], h0 - n + i)
                this = self._pop("ptr", h0 - n - 1)
                sym = mangle(self.module, owner, mname, desc)
                argtxt = ", ".join([f"ptr {this}"] +
                                   [f"{t} {v}" for t, v in zip(params, vals)])
                if ret == "void":
                    self.emit(f"call void @{sym}({argtxt})")
                else:
                    r = self.t()
                    self.emit(f"{r} = call {ret} @{sym}({argtxt})")
                    self._push(ret, r, h0 - n - 1)
                self._check_exc(x)
                return
            raise JavaError(f"invokespecial {cls}.{mname}{desc} is not supported")
        elif name == "newarray":
            atype = x.args[0]
            elem = {4: 1, 5: 2, 8: 1, 9: 2, 10: 4, 11: 8}.get(atype)
            if elem is None:
                raise JavaError(f"newarray atype {atype} is not supported")
            count = self._pop("i32", h0 - 1)
            size = self.t()
            total = self.t()
            self.emit(f"{size} = mul i32 {count}, {elem}")
            self.emit(f"{total} = add i32 {size}, 8")
            p = self.t()
            self.emit(f"{p} = call ptr @{_alloc_sym(self.module)}(i32 {total})")
            self.emit(f"store i32 {count}, ptr {p}")
            self._push("ptr", p, h0 - 1)
        elif name == "arraylength":
            p = self._pop("ptr", h0 - 1)
            n = self.t()
            self.emit(f"{n} = load i32, ptr {p}")
            self._push("i32", n, h0 - 1)
        elif name in ("iaload", "laload", "iastore", "lastore"):
            ty = "i64" if name.startswith("l") else "i32"
            width = 8 if ty == "i64" else 4
            if name.endswith("store"):
                val = self._pop(ty, h0 - 1)
                idx = self._pop("i32", h0 - 2)
                arr = self._pop("ptr", h0 - 3)
            else:
                idx = self._pop("i32", h0 - 1)
                arr = self._pop("ptr", h0 - 2)
            base = self.t(); off = self.t(); q = self.t()
            self.emit(f"{base} = getelementptr i8, ptr {arr}, i32 8")
            self.emit(f"{off} = mul i32 {idx}, {width}")
            self.emit(f"{q} = getelementptr i8, ptr {base}, i32 {off}")
            if name.endswith("store"):
                self.emit(f"store {ty} {val}, ptr {q}")
            else:
                v = self.t()
                self.emit(f"{v} = load {ty}, ptr {q}")
                self._push(ty, v, h0 - 2)
        elif name == "invokestatic":
            cls, mname, desc = methodref(self.pool, x.args[0])
            # `java.lang.Math`'s integer subset: lowered inline, no runtime call.
            if cls == "java/lang/Math" and mname in ("abs", "min", "max"):
                if desc in ("(I)I", "(J)J") and mname == "abs":
                    ty = "i32" if desc == "(I)I" else "i64"
                    a = self._pop(ty, h0 - 1)
                    # Same reason as min/max: a branch + store is the shape the
                    # self-hosted linker already agrees on (a mask/`ashr`
                    # shortcut was miscompiled there).
                    n = self.lbl
                    self.lbl += 1
                    p = self.t(); c = self.t()
                    self.emit(f"{p} = alloca {ty}")
                    self.emit(f"{c} = icmp slt {ty} {a}, 0")
                    self.emit(f"br i1 {c}, label %AB{n}N, label %AB{n}P")
                    self.emit(f"AB{n}N:")
                    nm = self.t()
                    self.emit(f"  {nm} = sub {ty} 0, {a}")
                    self.emit(f"  store {ty} {nm}, ptr {p}")
                    self.emit(f"  br label %AB{n}E")
                    self.emit(f"AB{n}P:")
                    self.emit(f"  store {ty} {a}, ptr {p}")
                    self.emit(f"  br label %AB{n}E")
                    self.emit(f"AB{n}E:")
                    r = self.t()
                    self.emit(f"  {r} = load {ty}, ptr {p}")
                    self._push(ty, r, h0 - 1)
                elif desc in ("(II)I", "(JJ)J") and mname in ("min", "max"):
                    ty = "i32" if desc == "(II)I" else "i64"
                    b = self._pop(ty, h0 - 1)
                    a = self._pop(ty, h0 - 2)
                    # A mask (`sext i1`) shortcut is correct in LLVM but the
                    # self-hosted linker's 1-bit handling has bitten before
                    # (#224); a store + branch is the boring shape both
                    # backends already agree on.
                    n = self.lbl
                    self.lbl += 1
                    p = self.t()
                    c = self.t()
                    self.emit(f"{p} = alloca {ty}")
                    self.emit(f"{c} = icmp {'slt' if mname == 'min' else 'sgt'} {ty} {a}, {b}")
                    self.emit(f"br i1 {c}, label %MM{n}A, label %MM{n}B")
                    self.emit(f"MM{n}A:")
                    self.emit(f"  store {ty} {a}, ptr {p}")
                    self.emit(f"  br label %MM{n}E")
                    self.emit(f"MM{n}B:")
                    self.emit(f"  store {ty} {b}, ptr {p}")
                    self.emit(f"  br label %MM{n}E")
                    self.emit(f"MM{n}E:")
                    r = self.t()
                    self.emit(f"  {r} = load {ty}, ptr {p}")
                    self._push(ty, r, h0 - 2)
                else:
                    raise JavaError(f"Math.{mname}{desc} is not supported yet")
                self._check_exc(x)
                return
            if cls not in self.registry:
                raise JavaError(
                    f"invokestatic {cls}.{mname}{desc}: class is not in the linked set")
            # 静态方法可以继承：沿超类链找到**声明它的**那个类再取名。
            owner, mm = self._impl(cls, mname, desc)
            if owner is None or mm.code is None:
                raise JavaError(
                    f"invokestatic {cls}.{mname}{desc}: no definition in the linked set")
            params, ret = parse_method_descriptor(desc)
            n = len(params)
            vals = [""] * n
            for i in range(n - 1, -1, -1):
                vals[i] = self._pop(params[i], h0 - n + i)
            sym = mangle(self.module, owner, mname, desc)
            argtxt = ", ".join(f"{t} {v}" for t, v in zip(params, vals))
            if ret == "void":
                self.emit(f"call void @{sym}({argtxt})")
            else:
                r = self.t()
                self.emit(f"{r} = call {ret} @{sym}({argtxt})")
                self._push(ret, r, h0 - n)
            self._check_exc(x)
        elif name == "athrow":
            e = self._pop("ptr", h0 - 1)
            self.emit(f"store ptr {e}, ptr @__jrt_exc_{self.module}")
            self._check_exc(x)
        elif name in ("tableswitch", "lookupswitch"):
            key = self._pop("i32", h0 - 1)
            dflt, table = x.args
            lbl = self.lbl
            self.lbl += 1
            self.emit(f"br label %S{lbl}_0")
            for i, (val, target) in enumerate(table):
                self.lines.append(f"S{lbl}_{i}:")
                c = self.t()
                self.emit(f"{c} = icmp eq i32 {key}, {val}")
                self.emit(f"br i1 {c}, label %L{target}, label %S{lbl}_{i + 1}")
            self.lines.append(f"S{lbl}_{len(table)}:")
            self.emit(f"br label %L{dflt}")
        elif name == "goto":
            self.emit(f"br label %L{x.args[0]}")
        elif name == "ireturn":
            v = self._pop("i32", h0 - 1)
            self.emit(f"ret i32 {v}")
        elif name == "lreturn":
            v = self._pop("i64", h0 - 1)
            self.emit(f"ret i64 {v}")
        elif name == "return":
            self.emit("ret void")
        elif name in ("freturn", "dreturn", "areturn"):
            raise JavaError(f"{name} is not supported in the first slice")
        else:
            raise JavaError(f"opcode {name} at {x.off} is not supported in the first slice")


def emit_method_ir(module: str, cls: str, m: Method, pool: list,
                   registry: dict, metas: dict, class_ids: dict,
                   globals_: list) -> tuple[str, str, str] | None:
    is_static = bool(m.access & ACC_STATIC)
    is_ctor = m.name == "<init>"
    is_clinit = m.name == "<clinit>"
    if m.access & (ACC_NATIVE | ACC_ABSTRACT):
        return None
    if m.name.startswith("<") and not (is_ctor or is_clinit):
        return None
    if m.code is None:
        return None
    desc_params, ret = parse_method_descriptor(m.desc)
    if ret not in ("i32", "i64", "void"):
        raise JavaError(f"{cls}.{m.name}: unsupported return type in {m.desc}")
    if is_ctor:
        if ret != "void":
            raise JavaError(f"{cls}.<init>{m.desc}: a constructor must return void")
    if is_clinit and (not is_static or ret != "void" or desc_params):
        raise JavaError(f"{cls}.<clinit>: must be static ()V")
    params = desc_params if is_static else ["ptr"] + desc_params
    if len(params) > 8:
        raise JavaError(f"{cls}.{m.name}: more than 8 parameters")
    insns = decode(m.code)
    heights = stack_heights(insns, pool, m.exc)
    sym = mangle(module, cls, m.name, m.desc)
    slots = []
    slot = 0                         # `this` (when present) is local slot 0
    for t in params:
        slots.append(slot)
        slot += 2 if t == "i64" else 1
    em = Emitter(insns, heights, m.max_stack, params, slots, ret, pool, module, cls,
                 registry, metas, class_ids, globals_, m.exc)
    body = em.run()
    def irty(t: str) -> str:
        return "void" if t == "void" else t
    argtxt = ", ".join(f"{t} %p{i}" for i, t in enumerate(params))
    rt = irty(ret)
    text = [f"define internal {rt} @{sym}({argtxt}) {{", "entry:", body, "}"]
    return sym, "\n".join(text), m.name, is_static


def emit(module: str, classes: list[ClassFile], ir_path: Path,
         iface_path: Path) -> list[tuple]:
    # 验证先于降级（docs/282 §8）：降级产物里一个槽就是 i64，进了 IR 就再没有类型
    # 可查。这一步只看字节码，不看类元数据。
    verify_classes(classes)
    registry = {cf.name: cf for cf in classes}
    metas: dict[str, ClassMeta] = {}
    globals_: list[str] = []
    # 类号按名字排序 —— 同一个输入集必须得到同一套号（可复现）。0 留给
    # "不是 `new` 出来的对象"（String、数组），所以从 1 开始。
    class_ids = {name: i + 1 for i, name in enumerate(sorted(registry))}
    # 子类先算完超类：字段偏移从超类对象的末尾接着排，虚分派的"谁是子类"也靠它。
    order: list[ClassFile] = []
    placed: set[str] = set()

    def _place(cf: ClassFile, depth: int = 0) -> None:
        if cf.name in placed or depth > 64:
            return
        sup = registry.get(cf.super_name) if cf.super_name else None
        if sup is not None:
            _place(sup, depth + 1)
        if cf.name in placed:
            return
        placed.add(cf.name)
        order.append(cf)

    for cf in classes:
        _place(cf)
    for cf in order:
        sup_meta = metas.get(cf.super_name) if cf.super_name else None
        layout: dict[str, tuple[int, str]] = {}
        static_layout: dict[str, tuple[str, str]] = {}
        off = sup_meta.size if sup_meta is not None else 8
        for f in cf.fields:
            ty = desc_type(f.desc)
            if f.access & ACC_STATIC:
                safe = re.sub(r"[^A-Za-z0-9_.$-]", "_", f.name)
                sym = (f"__jl_static_{module}_{safe}_"
                       f"{hashlib.sha256(f.name.encode()).hexdigest()[:8]}")
                globals_.append(f"@{sym} = internal global {ty} zeroinitializer")
                static_layout[f.name] = (sym, ty)
            else:
                layout[f.name] = (off, ty)
                off += 8
        metas[cf.name] = ClassMeta(layout, static_layout, off)

    funcs: list[tuple[str, str, str, list[str], str, bool, str]] = []
    for cf in classes:
        for m in cf.methods:
            got = emit_method_ir(module, cf.name, m, cf.pool, registry, metas,
                                 class_ids, globals_)
            if got is None:
                continue
            sym, text, name, is_static = got
            params, ret = parse_method_descriptor(m.desc)
            funcs.append((sym, text, name, params, ret, is_static, cf.name))
    funcs.sort(key=lambda x: (x[6], x[2], x[0]))
    init_sym = f"__jl_init_{module}"
    inited = f"@__jl_inited_{module}"
    globals_.append(f"{inited} = internal global i32 0")
    clinits = [x[0] for x in funcs if x[2] == "<clinit>"]
    init_lines = [
        f"define internal void @{init_sym}() {{",
        "entry:",
        f"  %s = load i32, ptr {inited}",
        "  %n = icmp ne i32 %s, 0",
        "  br i1 %n, label %done, label %run",
        "run:",
        f"  store i32 1, ptr {inited}",
    ]
    init_lines += [f"  call void @{s}()" for s in clinits]
    init_lines += ["  br label %done", "done:", "  ret void", "}"]
    ir_text = ("\n\n".join(globals_) + "\n\n" +
               "\n\n".join(x[1] for x in funcs) + "\n\n" +
               "\n".join(init_lines) + "\n\n" +
               _JRT_DECLS + "\n\n" + _jrt_alloc_ir(module))
    if len(ir_text.encode("utf-8")) > MAX_IR_BYTES:
        raise JavaError(
            f"J002: the generated IR is over {MAX_IR_BYTES} bytes (resource cap)")
    ir_path.write_text(ir_text, encoding="utf-8", newline="\n")

    decls = [f"extern fn {init_sym}();"]
    bodies: list[str] = []
    used: set[str] = set()
    multi = len(classes) > 1
    for sym, _text, name, params, ret, is_static, cls in funcs:
        if name.startswith("<") or not is_static:
            continue
        simple = re.sub(r"[^A-Za-z0-9_]", "_", cls.rsplit("/", 1)[-1])
        base = f"{module}_{simple}_{name}" if multi else f"{module}_{name}"
        alias = base
        n = 2
        while alias in used:
            alias = f"{base}_{n}"
            n += 1
        used.add(alias)
        args = ", ".join(f"a{i}: {t}" for i, t in enumerate(params))
        call = ", ".join(f"a{i}" for i in range(len(params)))
        rt = "" if ret == "void" else f" -> {ret}"
        # Declarations go **after every body**. The self-hosted checker's
        # per-function body walk starts at each `fn` token and takes the next
        # `{` as that function's body; a bodiless `extern fn` therefore adopts
        # the body of the function below it and checks it against the extern's
        # (empty) parameter list, so every parameter there reads as undefined
        # (E2). Trailing declarations have no following body and are skipped.
        # Filed as an issue; when the checker is fixed this can move back.
        if ret == "void":
            decls.append(f"extern fn {sym}({args});")
        else:
            decls.append(f"extern fn {sym}({args}) -> {ret};")
        bodies.append(f"pub fn {alias}({args}){rt} {{")
        bodies.append(f"    {init_sym}();")
        if ret == "void":
            bodies.append(f"    {sym}({call});")
        else:
            bodies.append(f"    return {sym}({call});")
        bodies.append("}")
        bodies.append("")
    lines = [f"module {module}", ""] + bodies + decls
    iface_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return funcs


# ---------------------------------------------------------------- IR merge

_DEF_RE = re.compile(r"^define\b[^@]*@([A-Za-z0-9_.$-]+)\s*\(", re.M)
_DECL_RE = re.compile(r"^declare\b[^@]*@([A-Za-z0-9_.$-]+)\s*\(", re.M)


def merge_ir(paths: list[Path], out: Path) -> None:
    texts = [p.read_text(encoding="utf-8") for p in paths]
    defined: dict[str, str] = {}
    for p, text in zip(paths, texts):
        for name in _DEF_RE.findall(text):
            if name in defined:
                raise JavaError(f"duplicate definition @{name} in {defined[name]} and {p}")
            defined[name] = str(p)
    merged: list[str] = []
    for p, text in zip(paths, texts):
        kept: list[str] = []
        for line in text.splitlines():
            m = _DECL_RE.match(line)
            if m and m.group(1) in defined:
                continue
            kept.append(line)
        merged.append("\n".join(kept))
    joined = "\n\n".join(merged)
    if joined.count("define void @_start") > 1:
        raise JavaError("more than one _start")
    out.write_text(joined + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("classfile")
    ap.add_argument("--module", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--emit-ir")
    ap.add_argument("--emit-interface")
    a = ap.parse_args(argv)
    try:
        classes = load_classes(Path(a.classfile))
        out_dir = Path(a.out_dir) if a.out_dir else None
        ir = Path(a.emit_ir) if a.emit_ir else (out_dir / f"{a.module}.ll")
        iface = Path(a.emit_interface) if a.emit_interface else (out_dir / f"{a.module}.lomt")
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        funcs = emit(a.module, classes, ir, iface)
        print(f"[OK] {len(classes)} class(es), {len(funcs)} method(s) -> {ir} + {iface}")
        return 0
    except (JavaError, OSError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
