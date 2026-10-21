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
from dataclasses import dataclass
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

ACC_STATIC = 0x0008
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
    r.u2()  # super
    for _ in range(r.u2()):
        r.u2()
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
                    cr.take(8)
                _skip_attributes(cr, pool)
        methods.append(Method(access, name, desc, code, max_stack, max_locals))

    return ClassFile(_class_name(pool, this_class), methods, fields, pool)


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
        return "ref", j
    if c == "L":
        j = s.find(";", i)
        if j < 0:
            raise JavaError("bad reference descriptor")
        return "ref", j + 1
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
            raise JavaError(f"unsupported opcode 0x{op:02x} at {off}")
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


def stack_heights(insns: list[Insn], pool: list) -> dict[int, int]:
    """Compute the operand-stack height at every reachable instruction.
    Branches that disagree are rejected; this is a small verifier, not a guess.
    """
    by_off = {x.off: x for x in insns}
    height: dict[int, int] = {}
    work = [(0, 0)]
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
        if name in ("ireturn", "lreturn", "freturn", "dreturn", "areturn", "return"):
            continue
        if name == "goto":
            work.append((x.args[0], after))
        elif name.startswith("if"):
            work.append((x.args[0], after))
            work.append((nxt, after))
        else:
            work.append((nxt, after))
    return height


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
                 module: str, own_class: str, field_layout: dict, obj_size: int,
                 static_layout: dict, globals_: list):
        self.insns = insns
        self.heights = heights
        self.max_stack = max_stack
        self.params = params
        self.param_slots = param_slots
        self.ret = ret
        self.pool = pool
        self.module = module
        self.own_class = own_class
        self.field_layout = field_layout
        self.obj_size = obj_size
        self.static_layout = static_layout
        self.globals = globals_
        self.strn = 0
        self.lines: list[str] = []
        self.tmp = 0
        self.locals: dict[tuple[int, str], str] = {}

    def t(self) -> str:
        self.tmp += 1
        return f"%t{self.tmp}"

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
            self._push("i32", str(x.args[0]), h0)
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
            if cls != self.own_class:
                raise JavaError(f"new {cls}: only the current class is supported yet")
            p = self.t()
            self.emit(f"{p} = call ptr @{_alloc_sym(self.module)}(i32 {self.obj_size})")
            self._push("ptr", p, h0)
        elif name in ("getstatic", "putstatic"):
            cls, fname, desc = fieldref(self.pool, x.args[0])
            if cls != self.own_class or fname not in self.static_layout:
                raise JavaError(f"{name} {cls}.{fname}: static field is not in this class")
            sym, ty = self.static_layout[fname]
            if name == "getstatic":
                v = self.t()
                self.emit(f"{v} = load {ty}, ptr @{sym}")
                self._push(ty, v, h0)
            else:
                v = self._pop(ty, h0 - 1)
                self.emit(f"store {ty} {v}, ptr @{sym}")
        elif name == "getfield":
            cls, fname, desc = fieldref(self.pool, x.args[0])
            if cls != self.own_class or fname not in self.field_layout:
                raise JavaError(f"getfield {cls}.{fname}: field is not in this class")
            off, ty = self.field_layout[fname]
            obj = self._pop("ptr", h0 - 1)
            q = self.t()
            self.emit(f"{q} = getelementptr i8, ptr {obj}, i32 {off}")
            v = self.t()
            self.emit(f"{v} = load {ty}, ptr {q}")
            self._push(ty, v, h0 - 1)
        elif name == "putfield":
            cls, fname, desc = fieldref(self.pool, x.args[0])
            if cls != self.own_class or fname not in self.field_layout:
                raise JavaError(f"putfield {cls}.{fname}: field is not in this class")
            off, ty = self.field_layout[fname]
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
            if cls == self.own_class:
                sym = mangle(self.module, cls, mname, desc)
                argtxt = ", ".join([f"ptr {recv}"] +
                                   [f"{t} {v}" for t, v in zip(params, vals)])
                if ret == "void":
                    self.emit(f"call void @{sym}({argtxt})")
                else:
                    r = self.t()
                    self.emit(f"{r} = call {ret} @{sym}({argtxt})")
                    self._push(ret, r, h0 - n - 1)
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
        elif name == "invokespecial":
            cls, mname, desc = methodref(self.pool, x.args[0])
            if mname == "<init>" and cls == "java/lang/Object":
                self._pop("ptr", h0 - 1)
                return
            if cls == self.own_class:
                params, ret = parse_method_descriptor(desc)
                n = len(params)
                vals = [""] * n
                for i in range(n - 1, -1, -1):
                    vals[i] = self._pop(params[i], h0 - n + i)
                this = self._pop("ptr", h0 - n - 1)
                sym = mangle(self.module, cls, mname, desc)
                argtxt = ", ".join([f"ptr {this}"] +
                                   [f"{t} {v}" for t, v in zip(params, vals)])
                if ret == "void":
                    self.emit(f"call void @{sym}({argtxt})")
                else:
                    r = self.t()
                    self.emit(f"{r} = call {ret} @{sym}({argtxt})")
                    self._push(ret, r, h0 - n - 1)
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
            if cls != self.own_class:
                raise JavaError(
                    f"invokestatic {cls}.{mname}{desc}: cross-class calls are not "
                    f"supported yet")
            params, ret = parse_method_descriptor(desc)
            n = len(params)
            vals = [""] * n
            for i in range(n - 1, -1, -1):
                vals[i] = self._pop(params[i], h0 - n + i)
            sym = mangle(self.module, cls, mname, desc)
            argtxt = ", ".join(f"{t} {v}" for t, v in zip(params, vals))
            if ret == "void":
                self.emit(f"call void @{sym}({argtxt})")
            else:
                r = self.t()
                self.emit(f"{r} = call {ret} @{sym}({argtxt})")
                self._push(ret, r, h0 - n)
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
                   field_layout: dict, obj_size: int, static_layout: dict,
                   globals_: list) -> tuple[str, str, str] | None:
    is_static = bool(m.access & ACC_STATIC)
    is_ctor = m.name == "<init>"
    if m.access & (ACC_NATIVE | ACC_ABSTRACT):
        return None
    if m.name.startswith("<") and not is_ctor:
        return None
    if m.code is None:
        return None
    desc_params, ret = parse_method_descriptor(m.desc)
    if ret not in ("i32", "i64", "void"):
        raise JavaError(f"{cls}.{m.name}: unsupported return type in {m.desc}")
    if is_ctor:
        if ret != "void":
            raise JavaError(f"{cls}.<init>{m.desc}: a constructor must return void")
    params = desc_params if is_static else ["ptr"] + desc_params
    if len(params) > 8:
        raise JavaError(f"{cls}.{m.name}: more than 8 parameters")
    insns = decode(m.code)
    heights = stack_heights(insns, pool)
    sym = mangle(module, cls, m.name, m.desc)
    slots = []
    slot = 0                         # `this` (when present) is local slot 0
    for t in params:
        slots.append(slot)
        slot += 2 if t == "i64" else 1
    em = Emitter(insns, heights, m.max_stack, params, slots, ret, pool, module, cls,
                 field_layout, obj_size, static_layout, globals_)
    body = em.run()
    def irty(t: str) -> str:
        return "void" if t == "void" else t
    argtxt = ", ".join(f"{t} %p{i}" for i, t in enumerate(params))
    rt = irty(ret)
    text = [f"define internal {rt} @{sym}({argtxt}) {{", "entry:", body, "}"]
    return sym, "\n".join(text), m.name, is_static


def emit(module: str, cf: ClassFile, ir_path: Path, iface_path: Path) -> list[tuple]:
    layout: dict[str, tuple[int, str]] = {}
    static_layout: dict[str, tuple[str, str]] = {}
    globals_: list[str] = []
    off = 8
    for f in cf.fields:
        ty = desc_type(f.desc)
        if f.access & ACC_STATIC:
            safe = re.sub(r"[^A-Za-z0-9_.$-]", "_", f.name)
            sym = f"__jl_static_{module}_{safe}_{hashlib.sha256(f.name.encode()).hexdigest()[:8]}"
            globals_.append(f"@{sym} = internal global {ty} zeroinitializer")
            static_layout[f.name] = (sym, ty)
        else:
            layout[f.name] = (off, ty)
            off += 8
    obj_size = off
    funcs: list[tuple[str, str, str, list[str], str, bool]] = []
    for m in cf.methods:
        got = emit_method_ir(module, cf.name, m, cf.pool, layout, obj_size,
                             static_layout, globals_)
        if got is None:
            continue
        sym, text, name, is_static = got
        params, ret = parse_method_descriptor(m.desc)
        funcs.append((sym, text, name, params, ret, is_static))
    funcs.sort(key=lambda x: (x[2], x[0]))
    ir_path.write_text("\n\n".join(globals_) + "\n\n" +
                       "\n\n".join(x[1] for x in funcs) + "\n\n" + _jrt_alloc_ir(module),
                       encoding="utf-8", newline="\n")

    lines = [f"module {module}", ""]
    used: set[str] = set()
    for sym, _text, name, params, ret, is_static in funcs:
        if name.startswith("<") or not is_static:
            continue
        base = f"{module}_{name}"
        alias = base
        n = 2
        while alias in used:
            alias = f"{base}_{n}"
            n += 1
        used.add(alias)
        args = ", ".join(f"a{i}: {t}" for i, t in enumerate(params))
        call = ", ".join(f"a{i}" for i in range(len(params)))
        rt = "" if ret == "void" else f" -> {ret}"
        lines.append(f"pub fn {alias}({args}){rt} {{")
        if ret == "void":
            lines.append(f"    {sym}({call});")
        else:
            lines.append(f"    return {sym}({call});")
        lines.append("}")
        if ret == "void":
            lines.append(f"extern fn {sym}({args});")
        else:
            lines.append(f"extern fn {sym}({args}) -> {ret};")
        lines.append("")
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
        data = Path(a.classfile).read_bytes()
        cf = parse_class(data)
        out_dir = Path(a.out_dir) if a.out_dir else None
        ir = Path(a.emit_ir) if a.emit_ir else (out_dir / f"{a.module}.ll")
        iface = Path(a.emit_interface) if a.emit_interface else (out_dir / f"{a.module}.lomt")
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        funcs = emit(a.module, cf, ir, iface)
        print(f"[OK] {cf.name}: {len(funcs)} method(s) -> {ir} + {iface}")
        return 0
    except (JavaError, OSError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
