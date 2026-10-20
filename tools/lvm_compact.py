#!/usr/bin/env python3
"""lvm_compact.py — 紧凑形：**那份 IR 的序列化**（`docs/250`，issue #328）。

## 它是什么，不是什么

`docs/232` §4.1 说 VM 是"同一份 IR 的第四个读者"。紧凑形不动摇这句话：它**不新开一门语言**，
它把**编译期已经算好的那张表**直接写成字节 —— 块是下标、SSA 名是槽下标、操作数是
`(种类, 数)`、类型与名字进字符串池。**读它就是建结构，没有任何一处再读文本。**

⚠ 它**不是压缩**（`docs/250` §4）：去重的是重复出现的名字，不是给文件瘦身用的。

## 形态必须被记下来

头四个字节是魔数，后面一个字节说明"这是哪一种形"。**喂错形点名拒**，不当成文本硬解 ——
"一个产物是什么要能读出来，不能靠它多半是"（`docs/229` 的新颖点 1）。

## 与 T2 的关系

T2（`tools/lvm_t2.py`）已经把 IR 编译成预解码的 op；紧凑形就是**那批 op 的序列化**。
所以"解码"这一步在紧凑形里根本不存在 —— 读进来就是编译好的东西。

⚠ **两个前提**（写实装时踩到的，见 `docs/250` §5）：
1. **全局地址在编译期就折进了操作数**，所以编码前必须先把全局摆好（`Vm._place_globals`）。
   解出来再摆一次得到的是同一批地址（插入序与对齐都是确定的）—— 判据 A 钉的就是这个。
2. 编码要**把每个函数都编出来**（序列化的是整份程序）。T0/T2 对"走不到的坏代码"是宽容的
   （懒编译）；紧凑形不宽容 —— 编不出来就**点名报**，不静默丢掉那个函数。
"""

from __future__ import annotations

import struct

import lomelf
import lvm
import lvm_t2

MAGIC = b"LVMC"
VERSION = 1
#: 这一版只认这一种形（`docs/250` §2.1：形态写进文件头，读的人不许猜）。
FORM_COMPACT = 1
FORM_TEXT = 0


class CompactError(lvm.LvmError):
    """紧凑形的错 —— 与"语言不支持"分开，名字也不一样（`docs/250` §3 D）。"""


# ---------------------------------------------------------------- 变长整数
def _put_u(buf: bytearray, n: int) -> None:
    """无符号 LEB128。"""
    if n < 0:
        raise CompactError(f"内部错误：负数进了无符号编码 {n}")
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def _get_u(blob: bytes, i: int, where: str = "") -> tuple[int, int]:
    """读 LEB128。**到头就是 `CompactError`，不是 `IndexError`**（#692）。

    `where` 是调用处给的那一段的名字（字符串池 / 全局 / 指令……），错误里点名它，
    否则一个截断的产物只能说"越界了"，说不出是**哪儿**被截断的。
    """
    n, shift = 0, 0
    while True:
        if i >= len(blob):
            raise CompactError(f"紧凑形在{where or '这里'}处被截断（变长整数读到末尾）")
        b = blob[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7
        if shift > 63:
            raise CompactError(f"紧凑形在{where or '这里'}处有一个过长的变长整数")


def _put_i(buf: bytearray, n: int) -> None:
    """有符号（zigzag）—— IR 里的立即数可能是负的，也可能是 2**64-1 那种无符号大数。"""
    _put_u(buf, (n << 1) if n >= 0 else ((-n) << 1) - 1)


def _get_i(blob: bytes, i: int, where: str = "") -> tuple[int, int]:
    zz, i = _get_u(blob, i, where)
    return ((zz >> 1) if not zz & 1 else -((zz + 1) >> 1)), i


# ---------------------------------------------------------------- 字符串池
class _Pool:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.idx: dict[str, int] = {}

    def add(self, s: str) -> int:
        got = self.idx.get(s)
        if got is None:
            got = len(self.rows)
            self.idx[s] = got
            self.rows.append(s)
        return got


#: 谓词 / 算术算子按**名字**存，不按函数对象 —— 函数对象不能进文件，
#: 而且两个实现要能对上（判据 E 的那一半将来靠它）。
def _icmp_name(fn) -> str:
    for k, v in lvm._ICMP.items():                          # noqa: SLF001
        if v is fn:
            return k
    raise CompactError("不认识的 icmp 谓词")


def _arith_name(fn) -> str:
    for k, v in lvm._ARITH.items():                         # noqa: SLF001
        if v is fn:
            return k
    raise CompactError("不认识的算术算子")


# ---------------------------------------------------------------- 编码
def encode_ops(ops_bytes: bytearray, op: tuple, pool: _Pool) -> None:
    code = op[0]
    _put_u(ops_bytes, code)

    def operand(o: tuple) -> None:
        kind = o[0]
        _put_u(ops_bytes, kind)
        if kind == 0:
            _put_u(ops_bytes, o[1])
            _put_u(ops_bytes, pool.add(o[2]))
        elif kind == 1:
            _put_i(ops_bytes, o[1])
        else:
            _put_u(ops_bytes, o[1])

    def stridx(s: str) -> None:
        _put_u(ops_bytes, pool.add(s))

    if code == lvm_t2._OP_ALLOCA:                           # noqa: SLF001
        _put_u(ops_bytes, op[1])
        _put_i(ops_bytes, op[2])
    elif code == lvm_t2._OP_LOAD:                           # noqa: SLF001
        stridx(op[1])
        operand(op[2])
        _put_i(ops_bytes, op[3])
    elif code == lvm_t2._OP_STORE:                          # noqa: SLF001
        stridx(op[1])
        operand(op[2])
        operand(op[3])
    elif code == lvm_t2._OP_GEP:                            # noqa: SLF001
        operand(op[1])
        _put_i(ops_bytes, op[2])
        _put_u(ops_bytes, len(op[3]))
        for (o, size) in op[3]:
            operand(o)
            _put_u(ops_bytes, size)
        _put_i(ops_bytes, op[4])
    elif code == lvm_t2._OP_EXTRACT:                        # noqa: SLF001
        operand(op[1])
        _put_u(ops_bytes, op[2])
        stridx(op[3])
        _put_i(ops_bytes, op[4])
    elif code == lvm_t2._OP_INSERT:                         # noqa: SLF001
        stridx(op[1])
        operand(op[2])
        operand(op[3])
        _put_u(ops_bytes, op[4])
        stridx(op[5])
        _put_i(ops_bytes, op[6])
    elif code == lvm_t2._OP_ICMP:                           # noqa: SLF001
        stridx(_icmp_name(op[1]))
        _put_u(ops_bytes, 1 if op[2] else 0)
        stridx(op[3])
        operand(op[4])
        operand(op[5])
        _put_i(ops_bytes, op[6])
    elif code == lvm_t2._OP_DIV:                            # noqa: SLF001
        stridx(op[1])
        stridx(op[2])
        operand(op[3])
        operand(op[4])
        _put_i(ops_bytes, op[5])
    elif code == lvm_t2._OP_ARITH:                          # noqa: SLF001
        # op 形状（#717 之后）：`(code, 算子名, 类型, 左, 右, dest)` —— 算子名本身就是
        # "ashr"/"lshr"/"shl"，所以线上那一格 ashr 位由名字推出来，**字节格式不变**。
        stridx(op[1])
        _put_u(ops_bytes, 1 if op[1] == "ashr" else 0)
        stridx(op[2])
        operand(op[3])
        operand(op[4])
        _put_i(ops_bytes, op[5])
    elif code == lvm_t2._OP_CAST:                           # noqa: SLF001
        stridx(op[1])
        stridx(op[2])
        stridx(op[3])
        operand(op[4])
        _put_i(ops_bytes, op[5])
    elif code == lvm_t2._OP_CALL:                           # noqa: SLF001
        stridx(op[1])
        _put_u(ops_bytes, len(op[2]))
        for o in op[2]:
            operand(o)
        _put_i(ops_bytes, op[3])
    elif code == lvm_t2._OP_ASM:                            # noqa: SLF001
        _put_u(ops_bytes, len(op[1]))
        for o in op[1]:
            operand(o)
        _put_i(ops_bytes, op[2])
    elif code == lvm_t2._OP_RMW:                            # noqa: SLF001
        stridx(op[1])
        operand(op[2])
        operand(op[3])
        _put_i(ops_bytes, op[4])
    elif code == lvm_t2._OP_PHI:                            # noqa: SLF001
        _put_i(ops_bytes, op[1])
        _put_u(ops_bytes, len(op[2]))
        for (bi, o) in sorted(op[2].items()):
            _put_u(ops_bytes, bi)
            operand(o)
    elif code == lvm_t2._OP_BR:                             # noqa: SLF001
        cond = op[1]
        _put_u(ops_bytes, 0 if cond is None else 1)
        if cond is not None:
            operand(cond)
        _put_u(ops_bytes, op[2])
        _put_i(ops_bytes, op[3])
    elif code == lvm_t2._OP_SWITCH:                         # noqa: SLF001
        operand(op[1])
        _put_u(ops_bytes, len(op[2]))
        for (cty, cval, bi) in op[2]:
            stridx(cty)
            _put_i(ops_bytes, cval)
            _put_u(ops_bytes, bi)
        _put_u(ops_bytes, op[3])
    elif code == lvm_t2._OP_RET:                            # noqa: SLF001
        v = op[1]
        _put_u(ops_bytes, 0 if v is None else 1)
        if v is not None:
            operand(v)
    elif code == lvm_t2._OP_UNREACH:                        # noqa: SLF001
        pass
    else:
        raise CompactError(f"不认识的 op: {op!r}")


def encode_program(prog, globals_: list, externs: list) -> bytes:
    """`Program`（已编译）-> 字节。`globals_` 必须**已经摆好**（见模块开头那条前提）。"""
    pool = _Pool()
    body = bytearray()

    _put_u(body, len(globals_))
    for g in globals_:
        _put_u(body, pool.add(g.name))
        _put_u(body, pool.add(g.ty))
        # ⚠ **全零的全局按"零"记，不按字节记**。`@__loment_heap` 是 64 KiB 的
        # `zeroinitializer` —— 文本里几个字符，照字节写就把整个紧凑形撑大 64 KB
        # （第一版实测：`tour` 21.5KB 文本 -> 70.9KB 紧凑形，比原文件还大三倍）。
        #
        # ⚠ **`data is None` 的长度是 `size_of(ty)`，不是 0**：解码器把全零恢复成 `None`
        # （不物化那段零），再编码时若写 0，`encode(decode(encode(x)))` 就丢了长度 ——
        # 这正是 #689：一个 64 字节的 `zeroinitializer` 读写成 0 字节。
        if g.data is None:
            _put_u(body, 0)                  # 0 = 全零
            _put_u(body, lomelf.size_of(g.ty))
            continue
        data = g.data
        if not data.strip(b"\x00"):
            _put_u(body, 0)                  # 0 = 全零
            _put_u(body, len(data))
        else:
            _put_u(body, 1)                  # 1 = 原样字节
            _put_u(body, len(data))
            body += data

    ext = sorted(externs)
    _put_u(body, len(ext))
    for n in ext:
        _put_u(body, pool.add(n))

    funcs = [prog.funcs[n] for n in prog.funcs]
    _put_u(body, len(funcs))
    for fn in funcs:
        cf = prog.cfuncs[fn.name]
        _put_u(body, pool.add(fn.name))
        _put_u(body, cf.nslots)
        _put_u(body, len(cf.params))
        for s in cf.params:
            _put_u(body, s)
        _put_u(body, len(cf.blocks))
        for bi, blk in enumerate(cf.blocks):
            _put_u(body, pool.add(cf.labels[bi]))
            _put_u(body, len(blk))
            for op in blk:
                encode_ops(body, op, pool)

    out = bytearray(MAGIC)
    out.append(VERSION)
    out.append(FORM_COMPACT)
    _put_u(out, len(pool.rows))
    for s in pool.rows:
        raw = s.encode("utf-8")
        _put_u(out, len(raw))
        out += raw
    out += body
    return bytes(out)


def encode_ir(text: str) -> bytes:
    """IR 文本 -> 紧凑形。走**真前门**：解析 -> 落全局 -> 编译 -> 序列化。"""
    globals_, funcs, externs = lomelf.parse_ll(text)
    # 先建执行体：它会把全局摆好（`_place_globals`），操作数里的地址就是从这里来的。
    vm = lvm_t2.T2Executor(globals_, funcs, externs, _NullOut())
    for name in list(vm.prog.funcs):
        vm.prog.cfunc(name)                 # 编不出来就点名报，不静默丢函数
    return encode_program(vm.prog, globals_, externs)


class _NullOut:
    def write(self, b):                     # encode 不跑程序，写口只是占位
        return len(b)

    def flush(self):
        pass


# ---------------------------------------------------------------- 解码
class _Reader:
    def __init__(self, blob: bytes) -> None:
        self.b = blob
        self.i = 0
        #: 正在读的**那一段**的名字，只进错误消息（#692：截断要说清在哪儿）。
        self.where = "文件头"

    def u8(self) -> int:
        if self.i >= len(self.b):
            raise CompactError(f"紧凑形在{self.where}处被截断（读单字节时到头）")
        v = self.b[self.i]
        self.i += 1
        return v

    def u(self) -> int:
        v, self.i = _get_u(self.b, self.i, self.where)
        return v

    def i_(self) -> int:
        v, self.i = _get_i(self.b, self.i, self.where)
        return v

    def raw(self, n: int) -> bytes:
        end = self.i + n
        if n < 0 or end > len(self.b):
            raise CompactError(
                f"紧凑形在{self.where}处被截断（要 {n} 字节，只剩 {len(self.b) - self.i}）")
        v = self.b[self.i:end]
        self.i = end
        return v


def decode(blob: bytes):
    """字节 -> `(globals, funcs, externs, cfuncs)`。**不读任何文本**。"""
    if len(blob) < 4 or blob[:4] != MAGIC:
        head = blob[:4]
        raise CompactError(
            f"这不是紧凑形（头四个字节是 {head!r}，不是 {MAGIC!r}）—— "
            "别把它当文本硬解（`docs/250` §2.1）")
    if len(blob) < 6:
        raise CompactError(
            f"紧凑形的文件头被截断（有魔数，但只有 {len(blob)} 字节，"
            f"连版本与形两字节都放不下）")
    r = _Reader(blob)
    r.i = 4
    ver = r.u8()
    if ver != VERSION:
        raise CompactError(f"紧凑形版本 {ver} 不认识（这一版只认 {VERSION}）")
    form = r.u8()
    if form != FORM_COMPACT:
        raise CompactError(f"文件头说这是形 {form}，不是紧凑形（{FORM_COMPACT}）")

    r.where = "字符串池"
    n = r.u()
    pool = []
    for _ in range(n):
        ln = r.u()
        try:
            pool.append(r.raw(ln).decode("utf-8"))
        except UnicodeDecodeError as e:
            raise CompactError(f"紧凑形的字符串池里有一段不是 UTF-8：{e}") from None

    def s() -> str:
        raw = r.u()
        if raw >= len(pool):
            raise CompactError(
                f"紧凑形在{r.where}处引用字符串池的第 {raw} 条，池里只有 {len(pool)} 条")
        return pool[raw]

    r.where = "全局"
    globals_ = []
    for _ in range(r.u()):
        name, ty = s(), s()
        tag = r.u()
        ln = r.u()
        if tag == 0:
            # 全零只按声明的大小合法（#690）：不信流里那个长度就分配 ——
            # 34 字节的输入曾让 `ln=10^8` 直接吃掉 100 MB。
            want = lomelf.size_of(ty)
            if ln != want:
                raise CompactError(
                    f"紧凑形里全局 {name} 声明为 {ty}（{want} 字节），"
                    f"却写了一段 {ln} 字节的全零")
            data = None            # `_place_globals` 按 `size_of(ty)` 补零
        elif tag == 1:
            data = r.raw(ln)
        else:
            raise CompactError(f"全局 {name} 的数据标记 {tag} 不认识")
        globals_.append(lomelf.Global(name, ty, data))

    r.where = "外部符号"
    externs = [s() for _ in range(r.u())]

    r.where = "函数表"
    cfuncs = {}
    funcs = []
    for _ in range(r.u()):
        fname = s()
        nslots = r.u()
        nparams = r.u()
        params = [r.u() for _ in range(nparams)]
        nblocks = r.u()
        cf = lvm_t2._CFunc(fname)                        # noqa: SLF001
        cf.nslots, cf.params = nslots, params
        for _ in range(nblocks):
            r.where = f"函数 {fname} 的块表"
            cf.labels.append(s())
            r.where = f"函数 {fname} 的指令"
            cf.blocks.append([_decode_op(r, s) for _ in range(r.u())])
        # **不信 `nslots`**（#690）：编译器的槽只来自形参与各指令的目的，
        # 所以合法程序里 `nslots <= 形参数 + 指令条数`。不查的话，
        # 一个 34 字节的产物就能让 `call_t2` 去分配 816 MB。
        total_ops = sum(len(b) for b in cf.blocks)
        if nslots > nparams + total_ops:
            raise CompactError(
                f"函数 {fname} 声称有 {nslots} 个槽，而它只有 {nparams} 个形参、"
                f"{total_ops} 条指令 —— 这个数不可信")
        for p in params:
            if p >= nslots:
                raise CompactError(
                    f"函数 {fname} 的形参槽 {p} 越界（只有 {nslots} 个槽）")
        cfuncs[fname] = cf
        fn = lomelf.Func(fname, None, [], False)
        # ⚠ 块里的**指令条数**要让 `Profile.text()` 能按 (块, 下标) 遍历 —— 它不看指令内容，
        # 只看 `range(len(blk.instrs))`。少填了，剖面的 [edges]/[calls] 段会整段消失，
        # 而 stdout 照样对（判据 B 专门钉这一半）。
        fn.blocks = [lomelf.Block(lbl) for lbl in cf.labels]
        for blk, ops in zip(fn.blocks, cf.blocks):
            blk.instrs = [lomelf.Instr(None, "") for _ in ops]
        funcs.append(fn)
    # 这一形**没有校验和**（`docs/250` §4.3），长度是它唯一的身份：
    # 多出来的字节不许默默丢掉（#691）—— 那是"这份产物是什么"读错的第一步。
    if r.i != len(blob):
        raise CompactError(
            f"紧凑形读完了还有 {len(blob) - r.i} 字节没用到 —— "
            "要么粘了垃圾，要么是两份产物连在了一起")
    return globals_, funcs, externs, cfuncs


def _decode_op(r: _Reader, s) -> tuple:
    code = r.u()

    def operand() -> tuple:
        kind = r.u()
        if kind == 0:
            return (0, r.u(), s())
        if kind == 1:
            return (1, r.i_())
        return (2, r.u())

    if code == lvm_t2._OP_ALLOCA:                        # noqa: SLF001
        return (code, r.u(), r.i_())
    if code == lvm_t2._OP_LOAD:                          # noqa: SLF001
        return (code, s(), operand(), r.i_())
    if code == lvm_t2._OP_STORE:                         # noqa: SLF001
        return (code, s(), operand(), operand())
    if code == lvm_t2._OP_GEP:                           # noqa: SLF001
        base = operand()
        const = r.i_()
        stages = [(operand(), r.u()) for _ in range(r.u())]
        return (code, base, const, stages, r.i_())
    if code == lvm_t2._OP_EXTRACT:                       # noqa: SLF001
        return (code, operand(), r.u(), s(), r.i_())
    if code == lvm_t2._OP_INSERT:                        # noqa: SLF001
        return (code, s(), operand(), operand(), r.u(), s(), r.i_())
    if code == lvm_t2._OP_ICMP:                          # noqa: SLF001
        pred = s()
        fn = lvm._ICMP.get(pred)                         # noqa: SLF001
        if fn is None:
            raise CompactError(f"不认识的 icmp 谓词: {pred}")
        sg = r.u() != 0
        ty = s()
        return (code, fn, sg, ty, operand(), operand(), r.i_())
    if code == lvm_t2._OP_DIV:                           # noqa: SLF001
        return (code, s(), s(), operand(), operand(), r.i_())
    if code == lvm_t2._OP_ARITH:                         # noqa: SLF001
        op = s()
        r.u()                                            # 旧格式里的 ashr 位（名字已带）
        ty = s()
        return (code, op, ty, operand(), operand(), r.i_())
    if code == lvm_t2._OP_CAST:                          # noqa: SLF001
        return (code, s(), s(), s(), operand(), r.i_())
    if code == lvm_t2._OP_CALL:                          # noqa: SLF001
        name = s()
        args = [operand() for _ in range(r.u())]
        return (code, name, args, r.i_())
    if code == lvm_t2._OP_ASM:                           # noqa: SLF001
        args = [operand() for _ in range(r.u())]
        return (code, args, r.i_())
    if code == lvm_t2._OP_RMW:                           # noqa: SLF001
        return (code, s(), operand(), operand(), r.i_())
    if code == lvm_t2._OP_PHI:                           # noqa: SLF001
        d = r.i_()
        m = {r.u(): operand() for _ in range(r.u())}
        return (code, d, m)
    if code == lvm_t2._OP_BR:                            # noqa: SLF001
        cond = operand() if r.u() else None
        return (code, cond, r.u(), r.i_())
    if code == lvm_t2._OP_SWITCH:                        # noqa: SLF001
        v = operand()
        cases = []
        for _ in range(r.u()):
            cty = s()
            cases.append((cty, r.i_(), r.u()))
        return (code, v, cases, r.u())
    if code == lvm_t2._OP_RET:                           # noqa: SLF001
        return (code, operand() if r.u() else None)
    if code == lvm_t2._OP_UNREACH:                       # noqa: SLF001
        return (code,)
    raise CompactError(f"紧凑形里有个不认识的 op {code}")


# ---------------------------------------------------------------- 前门
def run_compact(blob: bytes, out, profile=None, argv: list[str] | None = None,
                fusion=None, hot: set[tuple[str, str]] | None = None) -> int:
    """紧凑形 -> 跑完 -> 退出码。与 `lvm.run_ir` / `lvm_t2.run_ir_t2` 同一个契约。

    `fusion` / `hot` 是 A4（`docs/266`，issue #647）：融合计划**不序列化**，解码之后
    按 op 重算 —— 所以紧凑形、缓存与直编拿到同一张计划。给 `fusion` 时在这里先算一遍
    （执行可能只走到一部分函数，报告要覆盖整份程序）。
    """
    globals_, funcs, externs, cfuncs = decode(blob)
    if profile is not None:
        profile.bind(funcs)
    vm = lvm_t2.T2Executor(globals_, funcs, externs, out, profile, argv,
                           fusion=fusion, hot=hot)
    vm.prog.cfuncs.update(cfuncs)
    if fusion is not None:
        for cf in cfuncs.values():
            if cf.blocks:
                vm._plan(cf, 0, cf.blocks[0])
    start = cfuncs.get("_start")
    if start is None:
        raise lvm.LvmError("这份 IR 里没有 `_start`")
    try:
        vm.call_t2(start, [], entry=True)
    except lvm._Exit as e:                              # noqa: SLF001
        return e.code
    return 0
