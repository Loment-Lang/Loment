#!/usr/bin/env python3
"""lvm.py — LVM 的参考实现：**那份 IR 的第四个读者**（`docs/243`）。

吃 `lomelf` 的同一种文本 IR 子集，**当场执行**，不落机器码。
解析整段复用 `lomelf`（它已经产出 `Instr`/`Block`/`Func`/`Global` 这套表），
这里只把"发射"换成"求值" —— 见 `docs/243` §3。

**跳过的整段**（`docs/243` §2）：x86 编码、`Asm`、`plan_frame`（SSA→栈槽）、
重定位、ELF/PE 写出、收编面。**留下的**是**语言语义**：零/符号扩展、按宽度截断、
phi 的两趟模型。

用法: python tools/lvm.py FILE.lomt      （与 `lomelf` 同一条前门：`.lomt` -> IR -> 跑）
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402

#: 解释器自己的地址空间。全局从 `GLOBAL_BASE` 起、堆紧跟着、栈从顶上往下。
MEM_SIZE = 16 * 1024 * 1024
GLOBAL_BASE = 0x10000
STACK_TOP = MEM_SIZE
#: 每一层调用一段自己的栈区（按深度算基址 —— 见 `run` 里那段注释）。
FRAME_SIZE = 4096

#: 每个标量占 8 字节（与 `lomelf` 的栈槽模型同口径，`SCALAR_SIZE`）。
SLOT = 8

#: **步数上限**（`docs/262`，issue #353）。镜像（`loment/tools/lvm.lomt`）一直有 `STEP_CAP`，
#: 参考实现**没有** —— 于是"挂死"与"慢程序"在参考那侧根本分不开。数的是**一次函数调用里
#: 执行的指令条数**（与镜像同口径），撞上就点名报函数 / 块 / 第几条，而不是等下去。
STEP_CAP = 50_000_000

#: **调用深度上限**（`docs/262`，issue #353）。不设的话，地址空间算出来能放 ~4080 层，
#: 而**宿主 Python 的栈**在 ~200 层就先炸 —— 于是程序死在一个 `RecursionError` 上，
#: 那不是 VM 的话，也不是使用者看得懂的话。取 128：**比镜像的 `MAX_DEPTH = 32` 宽松**
#: （孪生那边不会因此更严），又远低于宿主栈。语料实测最大深度 **10**。
MAX_DEPTH = 128

#: **合成文件**的 fd 从这儿起（`docs/243` §3.4）。解释器没有真的文件描述符，
#: 所以真 fd 与合成 fd 不会撞；这个数两边实现**必须一样**（自举镜像里也写 1000）。
VFD_BASE = 1000

#: `/proc/self/cmdline` 的字段分隔符（也是内核给的那个字节）。
B_NUL = bytes([0])


class Counters:
    """一次跑的**确定性**工作量计数（issue #1028）。

    墙钟只说明"这一次机器上谁先跑完"；这一格记的是**程序与档位决定、与机器无关**的量：

    * `instructions` —— 执行的 IR 指令条数（三档同口径；融合 / 内联摊平的每一条也算）；
    * `decodes` —— **把文本解成可执行形状**的次数。T0 每执行一条就重解一次；
      T2/T3 每个函数只解一次（`decoded[name]` 是每函数的次数）；
    * `tuple_lookups` —— 读**编译好的 op 元组**的次数。T0 没有元组（恒 0）；
      T2/T3 每条执行到的指令读一次；
    * `specialisations` —— T3 真正加上守卫特化的调用点个数；
    * `deopts` —— 守卫被推翻的次数。

    关着（`counters=None`）时**一个字节都不写**，与 T1 的剖面同一条纪律。
    """

    __slots__ = ("instructions", "decodes", "tuple_lookups", "specialisations",
                 "deopts", "decoded", "decode_requests", "specialised_sites")

    def __init__(self) -> None:
        self.instructions = 0
        self.decodes = 0
        self.tuple_lookups = 0
        self.specialisations = 0
        self.deopts = 0
        #: 每个函数**真正被解了几次**（T2/T3 的"一次"判据靠它，而不是靠总数）。
        self.decoded: dict[str, int] = {}
        #: 请求解码的次数（含命中缓存）。`decode_requests >= decodes` 才说明缓存真的在挡。
        self.decode_requests = 0
        #: T3 加了守卫的站点，按 IR 解析顺序（判据拿它逐点位）。
        self.specialised_sites: list[tuple] = []

    def decode(self, name: str) -> None:
        self.decodes += 1
        self.decoded[name] = self.decoded.get(name, 0) + 1

    def text(self) -> str:
        """可读的计数报告（与剖面同一条"确定、按名字排序"的纪律）。"""
        rows = [
            f"instructions\t{self.instructions}",
            f"decodes\t{self.decodes}",
            f"decode_requests\t{self.decode_requests}",
            f"tuple_lookups\t{self.tuple_lookups}",
            f"specialisations\t{self.specialisations}",
            f"deopts\t{self.deopts}",
        ]
        per = [f"{name}\t{self.decoded[name]}" for name in sorted(self.decoded)]
        return "\n".join(["# lvm counters v1",
                          "# deterministic per run; wall clock is not a criterion",
                          "[counters]"] + rows + ["[functions]"] + per) + "\n"


def _cmdline(argv: list[str]) -> bytes:
    """内核给 `/proc/self/cmdline` 的形状：**每个字段后面跟一个 NUL**。

    抄这个形状是有意的 —— 被解释程序照着"数 NUL"来算 argc（`docs/223` 生成的入口
    就是那么干的），形状不对它数出来的就是另一个数。
    """
    return b"".join(s.encode("utf-8", "surrogateescape") + B_NUL for s in argv)


class LvmError(Exception):
    """解释器自己的错（**不是**"语言不支持" —— 那种要另说，见 `docs/243` §1）。"""


# ---------------------------------------------------------------- 入口形状（#1026）
#: `docs/243` §3.5 的那张表。编译器发**四种**入口形状，而 VM 原先只认 `_start` ——
#: 其余三种会被报成"没有 `_start`"，那是一句**真而无用**的话（程序是好的，引擎不是）。
#: 下面先**按文本认形状**，再决定支持还是点名拒。按文本而不是按 `parse_ll` 的表，理由是
#: `interrupt fn` 的 `define x86_intrcc` 会被 `lomelf` 解析成一个奇怪的返回类型 ——
#: 我们要在解析**之前**就能说出"这是中断入口"。
_DEFINE_START_RE = re.compile(r"^define\b[^\n{]*?@_start\s*\(", re.M)
_DEFINE_COMMAND_RE = re.compile(r"^define\b[^\n{]*?@command_main\s*\(", re.M)
_DEFINE_INTERRUPT_RE = re.compile(
    r"^define\b[^\n{]*?\bx86_intrcc\b[^\n{]*?@([-A-Za-z0-9_.]+)\s*\(", re.M)
_THREAD_TARGET_RE = re.compile(r'asm sideeffect "thread_spawn @([-A-Za-z0-9_.]+)"')

#: 没有入口时的统一说法。**保留"没有 `_start`"这个子串**（孪生镜像 `loment/tools/lvm.lomt`
#: 与 `lvm_twin_test` 用它认这句），但把"也没有其它入口形状"说全 —— #1026 要去掉的是
#: **有别的入口却只说没有 `_start`**，不是这句本身。
NO_ENTRY = ("这份 IR 里没有 `_start`，也没有其它入口形状（`command_main` / `interrupt` / "
            "`thread_spawn` 目标）—— 见 `docs/243` §3.5")


def entry_kind(text: str) -> tuple[str, str] | None:
    """从 IR 文本认出入口形状：`(种类, 入口名字)`；四种都不是就 `None`。

    优先级就是**能跑的优先**：`_start` > `command_main` > 中断 > 线程目标。一个普通程序
    里可以有中断处理函数；只要 `_start` 在，它就是入口，中断函数只是没被调到。
    """
    if _DEFINE_START_RE.search(text):
        return ("_start", "_start")
    if _DEFINE_COMMAND_RE.search(text):
        return ("command_main", "command_main")
    m = _DEFINE_INTERRUPT_RE.search(text)
    if m:
        return ("interrupt", m.group(1))
    m = _THREAD_TARGET_RE.search(text)
    if m:
        return ("thread_spawn", m.group(1))
    return None


def entry_refusal(kind: str, name: str) -> str:
    """不支持的入口形状 -> **点名**它、并指出定义它的那份文档（#1026 的验收）。"""
    if kind == "interrupt":
        return (f"这份 IR 的入口是中断处理函数 `{name}`（`interrupt fn`，`docs/149` §M70）"
                "—— LVM 不跑中断入口：它需要一个合成的 x86 中断帧，这一格没有"
                "（`docs/243` §3.5）")
    if kind == "thread_spawn":
        return (f"这份 IR 的入口是线程入口 `{name}`（`thread_spawn` 的目标，`docs/245`）"
                "—— LVM 不跑线程入口：并发是后续里程碑（`docs/243` §3.5）")
    raise LvmError(f"不是一种入口形状: {kind!r}")


def entry_values(vm, kind: str, name: str) -> list:
    """把一个入口变成**调用实参**。不支持的形状在这里点名拒。

    `command_main(argv: ptr, argc: u32)`（`docs/223` §3.2）是**支持**的：调用方给的
    `argv`（`run_ir(..., argv=[...])`）按内核给 `/proc/self/cmdline` 的形状铺进内存
    （每个字段后跟一个 NUL），`argc` 就是字段数 —— 这正是工具链生成的 `_start` 会做的
    那一段，只是这里由调用方直接给。
    """
    if kind == "_start":
        return []
    if kind == "command_main":
        fn = vm.funcs.get(name)
        if fn is None:
            raise LvmError(f"这份 IR 声明了 `{name}`，但解析表里没有它")
        if (len(fn.params) != 2 or fn.params[0][0] != "ptr"
                or fn.params[1][0] != "i32"):
            raise LvmError(
                "`command_main` 的签名不是 `(ptr, i32)` —— 入口是 "
                "`fn command_main(argv: ptr, argc: u32) -> u32`（`docs/223` §3.2）")
        data = _cmdline(vm.argv)
        addr = vm.mem.alloc(max(1, len(data)))
        if data:
            vm.mem.put(addr, data)
        return [addr, len(vm.argv)]
    raise LvmError(entry_refusal(kind, name))


# ---------------------------------------------------------------- 值

def _is_agg(ty: str) -> bool:
    return lomelf.is_agg(ty)


def mask(v: int, ty: str) -> int:
    """按类型的位宽截断 —— 这是**语言语义**，照抄 `lomelf` 的 `put`。"""
    n = lomelf.SCALAR_SIZE.get(ty)
    if n is None:
        return v
    return v & ((1 << (n * 8)) - 1)


def signed(v: int, ty: str) -> int:
    """同一位串按**有符号**读 —— 照抄 `lomelf.get_signed`。"""
    n = lomelf.SCALAR_SIZE.get(ty)
    if n is None:
        return v
    bits = n * 8
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def zeroext(v: int, ty: str) -> int:
    """零扩展到 64 —— 照抄 `lomelf.get` 的那一步（**必须留**，它是语义不是代码生成）。"""
    return mask(v, ty)


# ---------------------------------------------------------------- 指令表

_ARITH = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
    "and": lambda a, b: a & b,
    "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b,
    "shl": lambda a, b: a << b,
    "lshr": lambda a, b: a >> b,
    "ashr": lambda a, b: a >> b,          # 符号由下面 signed() 处理
}
_ICMP = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "ugt": lambda a, b: a > b,
    "uge": lambda a, b: a >= b,
    "ult": lambda a, b: a < b,
    "ule": lambda a, b: a <= b,
    "sgt": lambda a, b: a > b,
    "sge": lambda a, b: a >= b,
    "slt": lambda a, b: a < b,
    "sle": lambda a, b: a <= b,
}
_SIGNED_CMP = ("sgt", "sge", "slt", "sle")


def arith(op: str, a: int, b: int, ty: str) -> int:
    """一条算术/位运算 —— **T0 与 T2 共用这一份**（issue #717）。

    移位量按指令上写的宽度读成**有符号**数：负数不是一个值，是一个错。Python 的
    `<<`/`>>` 对负数抛的是 `ValueError: negative shift count`（一条 traceback，不是诊断），
    而自举镜像把位串读成 u64 后静默返回一个值 —— 三种行为里没有一种是"同一个答案"。
    这里统一成 `LvmError`；T2 的两条执行路径也走这里，镜像照同一句话拒。

    ⚠ 正的越界移位量**不动**：三种实现已经在"先移位再按宽度截断"上一致
    （`shl`/`lshr` → 0，`ashr` → 符号位），那不是这一条要改的事。
    """
    if op in ("shl", "lshr", "ashr") and signed(b, ty) < 0:
        raise LvmError("移位量是负数")
    if op == "ashr":
        return mask(signed(a, ty) >> b, ty)
    return mask(_ARITH[op](a, b), ty)


def gep_offset(ty: str, idxs: list[int], text: str = "") -> int:
    """`getelementptr` 的下标游走 —— **T0 与 T2 的同一份实现**（issue #715）。

    第 0 步是"把指针挪过整个基类型"，之后数组脱一层、结构体按字段偏移。结构体那一步
    **一直**是运行期算的（`lomelf.field_offset`）—— 所以非恒定下标在 T0 里不是错误；
    T2 静态走不动时退到这里，两边因此同义。
    """
    off, cur = 0, ty
    for k, idx in enumerate(idxs):
        if k == 0:
            off += idx * lomelf.size_of(cur)
            continue
        if cur.startswith("["):
            # `array_parts` 返回 **(个数, 元素)** —— 个数在前，别取 `[0]`。
            cur = lomelf.array_parts(cur)[1]
            off += idx * lomelf.size_of(cur)
        elif cur.startswith("{"):
            o, cur = lomelf.field_offset(cur, idx)
            off += o
        else:
            raise LvmError(f"GEP 的这一步 T0 没实现: {text!r}")
    return off

_CALL_ASM = lomelf.CALL_ASM_RE
#: `CALL_ASM_RE` 的分组: 1 = 返回类型, **2 = asm 名字, 3 = 约束串, 4 = 实参表**。
#: 2026-10-14（`docs/245`）之前只有 1/2/3 —— 名字是写死在模式里的 `"syscall"`。加
#: `thread_spawn` 那第二类内联汇编时把它改成了**捕获名字**，于是这里的组号整体挪了一格。
#: 写在这里而不是就地写 `m.group(3)`：下次再动那张模式，改这一处就够了。
_ASM_NAME, _ASM_SPEC, _ASM_ARGS = 2, 3, 4


def _parse_asm_constraints(spec: str) -> tuple[str | None, list[str]]:
    """`"={ax},{ax},{di},…,~{cx}"` -> (输出寄存器, [各输入的寄存器])。"""
    out = None
    ins: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part or part.startswith("~"):
            continue
        m = re.match(r"([=+*&]?)\{([^}]+)\}", part)
        if not m:
            continue
        kind, reg = m.group(1), m.group(2)
        if "=" in kind or "+" in kind:
            out = reg
        else:
            ins.append(reg)
    return out, ins


class Mem:
    """一块字节数组 + 一个地址就是下标。全局 / 堆 / 栈都住在里面。"""

    def __init__(self) -> None:
        self.buf = bytearray(MEM_SIZE)
        self.heap = GLOBAL_BASE
        self.sp = STACK_TOP

    def put(self, addr: int, data: bytes) -> None:
        if addr < 0 or addr + len(data) > MEM_SIZE:
            raise LvmError(f"写越界: {addr} + {len(data)}")
        self.buf[addr:addr + len(data)] = data

    def get(self, addr: int, n: int) -> bytes:
        if addr < 0 or addr + n > MEM_SIZE:
            raise LvmError(f"读越界: {addr} + {n}")
        return bytes(self.buf[addr:addr + n])

    def cstr(self, addr: int, limit: int = 4096) -> bytes:
        """读一个 NUL 结尾的字符串（被解释程序里的字面量路径）。上限是"不许挂死"。"""
        if addr < 0 or addr >= MEM_SIZE:
            raise LvmError(f"读越界: {addr}")
        n = 0
        while n < limit and addr + n < MEM_SIZE and self.buf[addr + n] != 0:
            n += 1
        return bytes(self.buf[addr:addr + n])

    def alloc(self, n: int, align: int = 8) -> int:
        self.heap = (self.heap + align - 1) // align * align
        a = self.heap
        self.heap += n
        if self.heap >= self.sp:
            raise LvmError("堆撞上栈 —— 地址空间用完了")
        return a


class Frame:
    """一次调用的栈帧：SSA 名 -> 值；`alloca` 的槽也在这一层。"""

    def __init__(self, mem: Mem, base: int) -> None:
        self.mem = mem
        self.base = base          # 帧底（往上是本帧的槽）
        self.slots: dict[str, int] = {}
        self.vals: dict[str, object] = {}
        self.next = base

    def slot(self) -> int:
        a = self.next
        self.next += SLOT
        return a


class Profile:
    """T1 的剖面（`docs/247`）—— 三样**静态读不出来**的东西：块的次数、边的次数、调用点。

    ⚠ **不记类型**。这份 IR 是静态类型的：`add i32 %a, %b` 里的 `i32` 写在指令上，
    同一条加法每一次执行只能是 i32 —— "观察到 i32"不是新信息。V8 反馈向量最重要那一项
    是给它吃的 **JavaScript**（没有类型）准备的，照抄过来是空的（`docs/247` §1）。

    站点标识 = (函数名, 块标签, 块内指令下标)。**输出按 IR 的解析顺序**，不是按名字排序 ——
    自举那一侧没有排序（固定表、按解析顺序摆），而两边要逐字节比同一份剖面。
    解析顺序对同一份 IR 是确定的，所以"确定性"这条判据照样成立（`docs/247` §4）。
    """

    def __init__(self) -> None:
        self.blocks: dict[tuple[str, str], int] = {}
        self.edges: dict[tuple[str, str, int, int], int] = {}
        self.calls: dict[tuple[str, str, int, str], int] = {}
        #: 解析出来的函数表（`lomelf.parse_ll` 的那一份，**顺序就是 IR 里的顺序**）。
        #: `text()` 靠它遍历 —— 见上面那段"为什么按解析顺序"。
        self.funcs: list = []

    @staticmethod
    def _bump(d: dict, k) -> None:
        d[k] = d.get(k, 0) + 1

    def block(self, fn: str, blk: str) -> None:
        self._bump(self.blocks, (fn, blk))

    def edge(self, fn: str, blk: str, idx: int, e: int) -> None:
        self._bump(self.edges, (fn, blk, idx, e))

    def call_site(self, site: tuple[str, str, int], callee: str) -> None:
        fn, blk, idx = site
        self._bump(self.calls, (fn, blk, idx, callee))

    def bind(self, funcs: list) -> None:
        """接上解析出来的函数表 —— `run_ir` 解析完就调它。"""
        self.funcs = funcs

    def text(self) -> str:
        """剖面文本。**第三方可读是构造出来的**：一张按 TAB 分列的表。

        ⚠ **遍历顺序 = IR 的解析顺序**（函数 → 块 → 指令），不是按名字排序：自举那一侧的
        固定表就是按解析顺序摆的，两边要比逐字节（`docs/247` §4 记了这条取舍）。
        """
        blocks: list[str] = []
        edges: list[str] = []
        calls: list[str] = []
        for fn in self.funcs:
            for blk in fn.blocks:
                n = self.blocks.get((fn.name, blk.label))
                if n:
                    blocks.append(f"{fn.name}\t{blk.label}\t{n}")
                for idx in range(len(blk.instrs)):
                    site = (fn.name, blk.label, idx)
                    # ⚠ **按边号升序**，不是"第一次被走到的次序"：自举那侧沿边表走，天然升序；
                    # 这里跟着字典插入顺序走，就会在"第一次取假边"的站点上和它分叉。
                    for kk in sorted(k for k in self.edges if k[:3] == site):
                        edges.append(f"{kk[0]}\t{kk[1]}\t{kk[2]}\t{kk[3]}\t{self.edges[kk]}")
                    for kk in sorted(k for k in self.calls if k[:3] == site):
                        calls.append(f"{kk[0]}\t{kk[1]}\t{kk[2]}\t{kk[3]}\t{self.calls[kk]}")
        out = ["# lvm profile v1",
               "# site = (function, block, instruction-index-in-block); a block is (function, block)",
               "# order = IR parse order (function -> block -> instruction), not sorted by name",
               "[blocks]"] + blocks + ["[edges]"] + edges + ["[calls]"] + calls
        return "\n".join(out) + "\n"


class Vm:
    def __init__(self, globals_: list, funcs: list, externs: list, out,
                 profile: Profile | None = None, argv: list[str] | None = None,
                 counters: "Counters | None" = None) -> None:
        self.mem = Mem()
        self.globals = {g.name: g for g in globals_}
        self.funcs = {f.name: f for f in funcs}
        self.externs = set(externs)
        self.out = out
        self.depth = 0
        #: T1（`docs/247`）。`None` = 这一趟不记账 —— **关掉时一个字节都不写**，
        #: 而且下面每一处记账都写成 `if self.profile is not None:`，所以关掉时**没有代价**
        #: （判据 D 要的正是"记录不许改变被记录的行为"）。
        self.profile = profile
        self.site: tuple[str, str, int] | None = None
        #: **被解释程序自己的**命令行（`docs/243` §3.4）。它读 `/proc/self/cmdline`
        #: 想知道"我叫什么" —— 那个文件属于**进程**，而进程是解释器。所以这一处必须
        #: **合成**，否则程序会以为自己是 `lvm`。给的就是"VM 被指到的那个输入路径"。
        self.argv = list(argv) if argv is not None else ["<ir>"]
        #: 确定性工作量计数（#1028）。`None` = 这一趟不记 —— 关掉时每一处记账都是
        #: `if c is not None`，与剖面同一条纪律。
        self.counters = counters
        #: 合成文件的 fd —— 解释器**没有真的文件描述符**，只认识这一张表里的。
        self.vfds: dict[int, list] = {}
        self.next_vfd = VFD_BASE
        self._place_globals()

    def _place_globals(self) -> None:
        for g in self.globals.values():
            data = g.data or b"\0" * lomelf.size_of(g.ty)
            align = max(1, min(8, lomelf.size_of(g.ty) or 1))
            g.addr = self.mem.alloc(len(data), align)
            self.mem.put(g.addr, data)

    # ------------------------------------------------------------ 值

    def operand(self, fr: Frame, s: str):
        """一个操作数文本 -> 宿主值。标量是 int，聚合是**内存映像的 bytes**。"""
        ty, v = lomelf.parse_operand(s)
        if v.startswith("%"):
            got = fr.vals.get(v)
            if got is None:
                raise LvmError(f"读未定义的值 {v}")
            return ty, got
        if v.startswith("@"):
            g = self.globals.get(v)
            if g is None:
                raise LvmError(f"未知全局 {v}")
            return ty, g.addr
        if v == "undef" or v == "null":
            return ty, 0 if not _is_agg(ty) else b"\0" * lomelf.size_of(ty)
        if v == "true":
            return ty, 1
        if v == "false":
            return ty, 0
        return ty, int(v)

    def load(self, ty: str, addr: int):
        n = lomelf.size_of(ty)
        raw = self.mem.get(addr, n)
        if _is_agg(ty):
            return raw
        return int.from_bytes(raw, "little")

    def store(self, ty: str, addr: int, val) -> None:
        if _is_agg(ty):
            self.mem.put(addr, val if isinstance(val, bytes) else bytes(val))
            return
        n = lomelf.size_of(ty)
        self.mem.put(addr, (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little"))


class _Exit(Exception):
    """`exit` 走系统调用 —— 它不返回，用异常把栈弹干净。"""

    def __init__(self, code: int) -> None:
        self.code = code


class _Trap(Exception):
    """`llvm.trap` —— 第 5 级崩溃器的落地（`docs/253` §5.5）。

    原生那边它是 `SIGILL`（shell 报 132）；VM 这边把它定成**同一个退出码**，
    并把站点（函数 / 块 / 指令序号）写出来 —— 两个实现"都死了"不算证据，
    要说得出**死在哪一条**（#1027）。
    """

    def __init__(self, site) -> None:
        self.site = site
        self.code = 132


def trap_exit(site) -> int:
    """`llvm.trap` 的共同出口：**132**（与原生 `SIGILL` 同一个数）+ 一行站点。"""
    fn, blk, idx = site
    print(f"lvm: llvm.trap at {fn}:{blk}#{idx}", file=sys.stderr)
    return 132


class Executor(Vm):
    # ------------------------------------------------------------ 系统调用
    def syscall(self, nr: int, a: list[int]) -> int:
        """**唯一**的对外通道（`docs/243` §3.4）。表里没有的就点名拒。"""
        if nr == 0:                                    # read(fd, buf, count)
            # 解释器没有真的文件描述符 —— 只认识 257 开的那个合成文件。
            v = self.vfds.get(a[0])
            if v is None:
                raise LvmError(f"read: fd {a[0]} 不是 VM 合成的那一个"
                               "（`docs/243` §3.4）")
            data = v[0][v[1]:v[1] + a[2]]
            v[1] += len(data)
            self.mem.put(a[1], data)
            return len(data)
        if nr == 1:                                    # write(fd, buf, count)
            fd, buf, n = a[0], a[1], a[2]
            data = self.mem.get(buf, n)
            if fd in (1, 2):
                self.out.write(data)
                self.out.flush()
            return n
        if nr == 3:                                    # close(fd)
            if self.vfds.pop(a[0], None) is None:
                raise LvmError(f"close: fd {a[0]} 不是 VM 合成的那一个"
                               "（`docs/243` §3.4）")
            return 0
        if nr == 12:                                   # brk(addr)
            # `_IR_HEAP` 的分配器用它向 OS 要页。解释器的堆本来就在地址空间里，
            # 所以这里只需把"当前可信的堆底"如实回一个数：给了就认，没给就报现值。
            want = a[0]
            if want == 0 or (self.mem.heap <= want < self.mem.sp):
                if want:
                    self.mem.heap = want
                return self.mem.heap
            return self.mem.heap
        if nr == 39:                                   # getpid
            # 如实转出去：这是**这台机器的事实**，被解释程序问的就是"我"是谁。
            return os.getpid()
        if nr == 63:                                   # uname(struct utsname *p)
            # ⚠ **合成，不转宿主** —— 它描述的是**目标平台**，不是解释器跑在哪台机器上。
            # 判据 A 比的是"这份产物在目标平台上会打印什么"，所以这里必须给 Linux：
            # 宿主是 Windows 也照样给 Linux（`docs/243` §3.4 那条规矩）。
            fields = [b"Linux", b"loment", b"0.1.5", b"#1 LVM T0", b"x86_64", b"(none)"]
            self.mem.put(a[0], b"".join(f[:64].ljust(65, b"\0") for f in fields))
            return 0
        if nr == 201:                                  # time(NULL)
            return int(time.time())
        # ⚠ **317 不是 getrandom** —— 我一开始照 `selfcheck` 的名字猜它是，写了个
        # `os.urandom` 分支。**判据 A 当场把这个臆造纠了出来**：原生产物在 Linux 上拿到
        # 317 = `seccomp`，返回 -1，于是 `selfcheck` 自己报 FAIL；而我的 VM "通过"了。
        # 两边一致才是对的 —— 所以这个分支**删掉**，VM 点名拒（`docs/243` §3.4 的规矩：
        # 表里没有的不装作有）。`selfcheck` 因此留在棘轮里，理由换成了真的那个。
        if nr == 257:                                  # openat(dirfd, path, flags)
            # **只认识 `/proc/self/cmdline`**，而且给它的是**被解释程序自己的**命令行，
            # 不是解释器的（下一段那个 `argv`）。别的路径点名拒 —— VM 里没有文件系统，
            # 装作有就是给错答案（`docs/243` §3.4）。
            path = self.mem.cstr(a[1])
            if path != b"/proc/self/cmdline":
                raise LvmError(f"openat: 只认识 /proc/self/cmdline（被解释程序自己的"
                               f"命令行），不认识 {path!r}（`docs/243` §3.4）")
            fd = self.next_vfd
            self.next_vfd += 1
            self.vfds[fd] = [_cmdline(self.argv), 0]
            return fd
        if nr == 60:                                   # exit(code)
            raise _Exit(a[0] & 0xFF)
        raise LvmError(f"系统调用表里没有 {nr}（`docs/243` §3.4）")

    # ------------------------------------------------------------ 调用
    def call(self, fr: Frame, text: str):
        m = _CALL_ASM.match(text)
        if m:
            name = m.group(_ASM_NAME)
            spec, argtext = m.group(_ASM_SPEC), m.group(_ASM_ARGS)
            if name != "syscall":
                # `thread_spawn` 那种**定制序列**（`docs/245`）不是一条 syscall ——
                # 这一档（T0 解释器）不实现它。**响亮地拒**，不假装跑过去。
                raise LvmError(f"这一档不支持的 asm: {name!r}（`docs/245`）")
            out_reg, ins = _parse_asm_constraints(spec)
            a = [self.operand(fr, s)[1] for s in lomelf.split_top(argtext)]
            nr = a[0]
            return self.syscall(nr, a[1:])
        body = text[len("call "):].strip()
        if body.startswith("void"):
            retty, body = None, body[4:].strip()
        else:
            retty, i = lomelf.parse_type(body)
            body = body[i:].strip()
        m2 = re.match(r"@([-A-Za-z0-9_.]+)\((.*)\)\s*$", body, re.S)
        if not m2:
            raise LvmError(f"解析不了调用: {text!r}")
        name = m2.group(1)
        args = [s for s in lomelf.split_top(m2.group(2)) if s]
        if name == "llvm.trap":
            raise _Trap(self.site)
        if name in self.externs:
            raise LvmError(f"`{name}` 是 extern —— T0 不接 FFI（`docs/243` §5）")
        fn = self.funcs.get(name)
        if fn is None:
            raise LvmError(f"没有这个函数: {name}")
        # issue #716：实参个数与形参不符**是错**，三种实现用同一句话拒。以前这里是
        # `zip(fn.params, argtexts)` —— 多出来的实参被**静默丢掉**、少给的形参留 0，
        # 于是"少一个实参"的程序照样跑完。
        if len(args) != len(fn.params):
            raise LvmError("实参个数与形参不符")
        if self.profile is not None and self.site is not None:
            self.profile.call_site(self.site, name)
        return self.run(fn, args, fr)

    # ------------------------------------------------------------ 执行
    def run_entry(self, fn, values: list):
        """入口调用：形参拿**宿主值**（`command_main` 的 argv/argc，`docs/243` §3.5）。"""
        return self.run(fn, [], None, values)

    def run(self, fn, argtexts: list[str], caller: Frame | None,
            entry_vals: list | None = None):
        # ⚠ **每一层调用各占一段自己的栈区**（按深度算基址）。
        # 第一版每层都取 `mem.sp - 4096`，而 `mem.sp` 从不下降 ⇒ **被调用者和调用者
        # 用同一段地址**，被调者的 `alloca` 覆盖了调用者的槽，返回后读回来全是 0 ——
        # 症状是"凡是算数再打印的程序全错"，而 `user_hello`（值在调用**前**就按值传走）
        # 照样对，所以一开始没露。
        if self.depth + 1 > MAX_DEPTH:
            raise LvmError(f"栈溢出（调用深度上限 {MAX_DEPTH}）")
        self.depth += 1
        base = STACK_TOP - self.depth * FRAME_SIZE
        if base <= self.mem.heap:
            self.depth -= 1
            raise LvmError("栈溢出")
        try:
            return self._run_body(fn, argtexts, caller, base, entry_vals)
        finally:
            self.depth -= 1

    def _run_body(self, fn, argtexts: list[str], caller: Frame | None, base: int,
                  entry_vals: list | None = None):
        fr = Frame(self.mem, base)
        # 形参与实参：实参在**调用者的**上下文里求值（`docs/243` §3.4）
        for k, (pty, pname) in enumerate(fn.params):
            if caller is None:
                # 入口：`_start` 的形参一律 0（旧行为）；`command_main` 的两个形参
                # 由 `entry_values` 直接给（`docs/223` §3.2）。
                fr.vals[pname] = (entry_vals[k] if entry_vals is not None
                                  and k < len(entry_vals) else 0)
                continue
            _ty, v = self.operand(caller, argtexts[k])
            fr.vals[pname] = v
        label = fn.blocks[0].label
        prev = None
        ret = 0
        steps = 0
        while True:
            blk = next((b for b in fn.blocks if b.label == label), None)
            if blk is None:
                raise LvmError(f"{fn.name}: 没有块 {label}")
            if self.profile is not None:
                self.profile.block(fn.name, blk.label)
            #: LLVM 的 `phi` 是**并行**的：同一块里后面的 phi 读前面的 phi 时，
            #: 读到的是**入边**的旧值，不是这一轮刚写进去的新值。顺序求值会让
            #: `%prev = phi [0, %link], [%cur, %lcont]` 读到**新的** `%cur`（它刚被
            #: 前一条 phi 改成 0），`free` 的空闲链因此每轮都被重置成单块 —— 实测
            #: `gc_auto` 的空闲块永远不复用，arena 提前耗尽（#1027）。
            _pending_phi: list[tuple[str, object]] = []
            for idx, ins in enumerate(blk.instrs):
                if self.profile is not None:
                    self.site = (fn.name, blk.label, idx)
                elif ins.text.startswith("call "):
                    # 只有 `llvm.trap` 需要站点；它是**调用**，所以只在调用点记 ——
                    # 不给每条指令付一次元组的代价（T1 关掉时"一个字节都不写"）。
                    self.site = (fn.name, blk.label, idx)
                steps += 1
                if self.counters is not None:
                    # T0 的"解码"就是这一条指令本身：每执行一次就把文本重解一次。
                    self.counters.instructions += 1
                    self.counters.decodes += 1
                if steps > STEP_CAP:
                    raise LvmError(
                        f"步数上限 {STEP_CAP}：{fn.name} 的块 {blk.label}，"
                        f"第 {idx} 条 —— {ins.text[:60]}")
                if ins.text.startswith("phi "):
                    if ins.dest:
                        _pending_phi.append((ins.dest, self._phi(fr, ins.text, prev)))
                    continue
                if _pending_phi:
                    for _d, _v in _pending_phi:
                        fr.vals[_d] = _v
                    _pending_phi = []
                if ins.text.startswith("br "):
                    label, _pred, edge = self._br(fr, ins.text, blk.label)
                    if self.profile is not None:
                        self.profile.edge(fn.name, blk.label, idx, edge)
                    prev = blk.label
                    break
                if ins.text.startswith("switch "):
                    label, edge = self._switch(fr, ins.text)
                    if self.profile is not None:
                        self.profile.edge(fn.name, blk.label, idx, edge)
                    prev = blk.label
                    break
                if ins.text.startswith("ret"):
                    ret = self._ret(fr, ins.text)
                    return ret
                if ins.text.startswith("unreachable"):
                    raise LvmError("执行到 unreachable")
                # ⚠ **没有目的值的指令也要执行**（`store` 就是：它 `dest is None`）。
                # 第一版写成 `if ins.dest:` 才求值 —— 于是所有 `store` 被**静默跳过**，
                # 症状是实参全是 0、程序照样"跑完"（连错都不报，最坏的一种）。
                v = self._eval(fr, ins.text)
                if ins.dest:
                    fr.vals[ins.dest] = v
            else:
                raise LvmError(f"{fn.name}: 块 {label} 没有终结指令")

    # ------------------------------------------------------------ 单条指令
    def _eval(self, fr: Frame, text: str):
        if text.startswith("alloca"):
            ty, _ = lomelf.parse_type(text[len("alloca "):])
            a = fr.slot()
            fr.next = a + max(SLOT, lomelf.size_of(ty))
            fr.slots[text] = a
            return a
        if text.startswith("load "):
            body = text[len("load "):]
            ty, i = lomelf.parse_type(body)
            _bty, addr = self.operand(fr, body[i:].lstrip(", ").strip())
            return self.load(ty, addr)
        if text.startswith("store "):
            body = text[len("store "):]
            ty, i = lomelf.parse_type(body)
            # `store <ty> <val>, ptr <addr>` —— 值那一格**没带类型**，得把类型的给它。
            val_s, ptr_s = lomelf.split_top(body[i:].lstrip(", "))
            _vty, val = self.operand(fr, ty + " " + val_s)
            _pty, addr = self.operand(fr, ptr_s)
            self.store(ty, addr, val)
            return None
        if text.startswith("getelementptr"):
            return self._gep(fr, text)
        if text.startswith("extractvalue "):
            return self._extractvalue(fr, text)
        if text.startswith("insertvalue "):
            return self._insertvalue(fr, text)
        if text.startswith("icmp "):
            return self._icmp(fr, text)
        if text.startswith("atomicrmw "):
            return self._atomicrmw(fr, text)
        if text.startswith(("trunc ", "zext ", "sext ", "ptrtoint ", "inttoptr ")):
            return self._cast(fr, text)
        if text.startswith("call "):
            return self.call(fr, text)
        m = re.match(r"(sdiv|udiv|srem|urem) (\w+) (\S+), (\S+)", text)
        if m:
            op, ty, a_s, b_s = m.groups()
            _t, a = self.operand(fr, ty + " " + a_s)
            _t2, b = self.operand(fr, ty + " " + b_s)
            if op in ("sdiv", "srem"):
                a, b = signed(a, ty), signed(b, ty)
            if b == 0:
                raise LvmError("除以零")
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            # ⚠ **取余要减回去** —— 第一版只给 `srem` 减了，`urem` 直接返回**商**。
            # 症状极难认：程序跑得完、退出码也对，只是**每一位数都是商**（`42 % 10` 得 4），
            # 于是十进制打印全是错位。`user_hello` 不做算术，所以一开始没露。
            if op in ("srem", "urem"):
                return mask(a - b * q, ty)
            return mask(q, ty)
        m = re.match(r"(\w+) (\w+) (\S+), (\S+)", text)
        if m and m.group(1) in _ARITH:
            op, ty, a_s, b_s = m.groups()
            _t, a = self.operand(fr, ty + " " + a_s)
            _t2, b = self.operand(fr, ty + " " + b_s)
            return arith(op, a, b, ty)
        raise LvmError(f"这个指令 T0 没实现: {text!r}")

    def _gep(self, fr: Frame, text: str) -> int:
        body = text[len("getelementptr"):].strip()
        if body.startswith("inbounds"):
            body = body[len("inbounds"):].strip()
        ty, i = lomelf.parse_type(body)
        # ⚠ 类型后面紧跟一个逗号，而 `split_top` 在开头那个逗号处会吐一个**空元素**
        # （它按逗号切、不跳过空段）—— 所以先把这个逗号吃掉。
        parts = lomelf.split_top(body[i:].lstrip(", "))
        _bty, base = self.operand(fr, parts[0])
        idxs = [self.operand(fr, s)[1] for s in parts[1:]]
        return base + gep_offset(ty, idxs, text)

    def _extractvalue(self, fr: Frame, text: str):
        ty, i = lomelf.parse_type(text[len("extractvalue "):])
        parts = lomelf.split_top(text[len("extractvalue ") + i:])
        _vty, val = self.operand(fr, (ty + " " + parts[0]).lstrip())
        off, fty = lomelf.field_offset(ty, int(parts[-1]))
        return self._from_bytes(fty, val[off:off + lomelf.size_of(fty)])

    def _insertvalue(self, fr: Frame, text: str):
        ty, i = lomelf.parse_type(text[len("insertvalue "):])
        parts = lomelf.split_top(text[len("insertvalue ") + i:])
        _bty, agg = self.operand(fr, (ty + " " + parts[0]).lstrip())
        _vty, val = self.operand(fr, parts[1])
        off, fty = lomelf.field_offset(ty, int(parts[2]))
        buf = bytearray(agg if isinstance(agg, bytes) else b"\0" * lomelf.size_of(ty))
        buf[off:off + lomelf.size_of(fty)] = self._to_bytes(fty, val)
        return bytes(buf)

    def _from_bytes(self, ty: str, raw: bytes):
        return raw if _is_agg(ty) else int.from_bytes(raw, "little")

    def _to_bytes(self, ty: str, val) -> bytes:
        if _is_agg(ty):
            return val if isinstance(val, bytes) else bytes(val)
        n = lomelf.size_of(ty)
        return (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little")

    def _icmp(self, fr: Frame, text: str) -> int:
        m = re.match(r"icmp (\w+) (\w+) (\S+), (\S+)", text)
        if not m:
            raise LvmError(f"icmp 解析不了: {text!r}")
        pred, ty, a_s, b_s = m.groups()
        _t, a = self.operand(fr, ty + " " + a_s)
        _t2, b = self.operand(fr, ty + " " + b_s)
        if pred in _SIGNED_CMP:
            a, b = signed(a, ty), signed(b, ty)
        return 1 if _ICMP[pred](a, b) else 0

    def _atomicrmw(self, fr: Frame, text: str) -> int:
        m = re.match(r"atomicrmw (\w+) ptr (\S+), (\w+) (\S+)(?: \w+)*", text)
        if not m or m.group(1) != "add":
            raise LvmError(f"atomicrmw 只实现了 add: {text!r}")
        _t, addr = self.operand(fr, "ptr " + m.group(2))
        ty, rhs = m.group(3), m.group(4)
        _t2, val = self.operand(fr, ty + " " + rhs)
        old = self.load(ty, addr)
        self.store(ty, addr, mask(old + val, ty))
        return old

    def _cast(self, fr: Frame, text: str):
        m = re.match(r"(\w+) (\w+) (\S+) to (\w+)", text)
        if not m:
            raise LvmError(f"转型解析不了: {text!r}")
        op, fty, a_s, tty = m.groups()
        _t, a = self.operand(fr, fty + " " + a_s)
        if op == "trunc":
            return mask(a, tty)
        if op == "zext":
            return zeroext(a, fty)
        if op == "sext":
            return signed(a, fty) & ((1 << (lomelf.size_of(tty) * 8)) - 1)
        return a                                       # ptrtoint / inttoptr：地址就是整数

    # ------------------------------------------------------------ 控制流
    def _phi(self, fr: Frame, text: str, prev: str | None):
        ty, entries = lomelf.parse_phi(text)
        for val_s, block in entries:
            if block == prev:
                # ⚠ **入边值不带类型**（`lomelf` 发的是 `phi i64 [ 0, %entry ]`）——
                # 类型只写在 phi 头上。所以先按原样试，不行再把 phi 的类型补上。
                # （第一版直接按原样解，撞到 `0` 就抛"解析不了的值"。）
                try:
                    _t, v = self.operand(fr, val_s)
                except lomelf.ElfError:
                    _t, v = self.operand(fr, f"{ty} {val_s}")
                return v
        raise LvmError(f"phi 没有来自 {prev} 的入边: {text!r}")

    def _br(self, fr: Frame, text: str, cur: str) -> tuple[str, str, int]:
        """返回 `(目标块, 前驱, 边的编号)`。**边号**是 T1 记账要的（`docs/247` §4）：
        无条件分支只有一条边（0）；条件分支 0 = 真、1 = 假。"""
        m = re.match(r"br label %([\w.]+)$", text)
        if m:
            return m.group(1), cur, 0
        m = lomelf.BR_COND_RE.search(text)
        if not m:
            raise LvmError(f"br 解析不了: {text!r}")
        _t, cond = self.operand(fr, "i1 " + m.group(1))
        return (m.group(2) if cond else m.group(3)), cur, (0 if cond else 1)

    def _switch(self, fr: Frame, text: str) -> tuple[str, int]:
        """返回 `(目标块, 边的编号)`：case 按出现次序 0..n-1，default 是 n。"""
        m = lomelf.SWITCH_RE.match(text)
        if not m:
            raise LvmError(f"switch 解析不了: {text!r}")
        ty, val_s, default, body = m.groups()
        _t, v = self.operand(fr, ty + " " + val_s)
        for k, (cty, cval, label) in enumerate(lomelf.CASE_RE.findall(body)):
            if mask(v, cty) == int(cval):
                return label, k
        return default, len(lomelf.CASE_RE.findall(body))

    def _ret(self, fr: Frame, text: str):
        rest = text[len("ret"):].strip()
        if not rest or rest == "void":
            return 0
        ty, _i = lomelf.parse_type(rest)
        _t, v = self.operand(fr, rest)
        return v


# ---------------------------------------------------------------- 前门

def run_ir(text: str, out, profile: Profile | None = None,
           argv: list[str] | None = None, tier: str = "t0",
           values=None, deopts=None, counters: "Counters | None" = None) -> int:
    """IR 文本 -> 跑完 -> 进程该退的码。**入口按 `docs/243` §3.5 那张表认**。

    `profile` 传一个 `Profile` 就记账（T1，`docs/247`）；传 `None`（默认）不记 ——
    **关掉时不写任何字节**，而且被记录程序的行为逐字节不变（判据 D）。

    `tier` 选**哪一格**跑（`docs/232` §4.4）：`"t0"`（默认）是逐条解释，`"t2"` 先用
    `tools/lvm_t2.py` 把整份 IR 编译一次再跑。**两档给同一个答案**（判据 A/B 各钉一半），
    默认仍是 t0 —— 换引擎不许悄悄改掉既有判据量到的东西。

    `argv` 是**被解释程序自己的**命令行（`docs/243` §3.4）：它读 `/proc/self/cmdline`
    时看到的就是这个。默认 `["<ir>"]` —— 进程内调用手里没有"那个程序叫什么"，
    就不编一个像路径的东西出来；要真名字的调用方自己传（`lvm_test` 传的就是它
    喂给原生产物的同一个名字，好让两边逐字节可比）。
    """
    if tier == "t2":
        import lvm_t2                                       # noqa: PLC0415

        return lvm_t2.run_ir_t2(text, out, profile, argv, values, counters=counters)
    if tier == "t3":
        import lvm_t3                                       # noqa: PLC0415

        return lvm_t3.run_ir_t3(text, out, profile, argv, values, deopts, counters)
    if tier != "t0":
        raise LvmError(f"没有这一档: {tier!r}（只有 t0 / t2 / t3）")
    shape = entry_kind(text)
    if shape is None:
        raise LvmError(NO_ENTRY)
    kind, name = shape
    if kind in ("interrupt", "thread_spawn"):
        raise LvmError(entry_refusal(kind, name))
    globals_, funcs, externs = lomelf.parse_ll(text)
    if profile is not None:
        profile.bind(funcs)
    vm = Executor(globals_, funcs, externs, out, profile, argv, counters)
    start = next((f for f in funcs if f.name == name), None)
    if start is None:
        raise LvmError(f"这份 IR 声明了入口 `{name}`，但解析表里没有它")
    args = entry_values(vm, kind, name)
    try:
        if kind == "_start":
            vm.run(start, [], None)
        else:
            # `command_main` 的返回值就是**进程退出码** —— 工具链生成的 `_start` 正是
            # 把它交给 `exit(60)`（`docs/223` §3.2）。这里直接照同一个语义收尾。
            return vm.run_entry(start, args) & 0xFF
    except _Exit as e:
        return e.code
    except _Trap as e:
        return trap_exit(e.site)
    return 0


def run_source(src: Path, root: Path, out=None, profile: Profile | None = None,
               argv: list[str] | None = None, tier: str = "t0",
               values=None, deopts=None, counters: "Counters | None" = None) -> int:
    """与 `lomelf` 同一条前门：`.lomt` -> IR -> 跑。"""
    if argv is None:
        argv = [str(src)]
    return run_ir(compile_source(src, root),
                  out if out is not None else sys.stdout, profile, argv, tier,
                  values, deopts, counters)


def compile_source(src: Path, root: Path) -> str:
    """源 -> IR 文本（前端那三步）。`run_source` 与 `--compact-out` 共用。"""
    import lomentc  # noqa: PLC0415

    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, root, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise LvmError(f"检查不过: {errs[:2]}")
    return lomentc.emit_llvm(mod, root, deps)


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    import io  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="lvm", description="LVM T0：那份 IR 的第四个读者（docs/243）")
    ap.add_argument("entry", nargs="?", help=".lomt 入口，或 --ir 时是 .ll；--compact 时省略")
    ap.add_argument("--ir", action="store_true", help="直接吃 IR 文本（跳过前端）")
    ap.add_argument("--root", default=".", help="解析 use 的根（默认当前目录）")
    ap.add_argument("--tier", choices=("t0", "t2", "t3"), default="t0",
                    help="跑哪一格（docs/232 §4.4）：t0 逐条解释（默认）、t2 编译一次再跑、"
                         "t3 再按值观察做守卫特化（docs/261）")
    ap.add_argument("--values-out", metavar="PATH", default=None,
                    help="T3 的**值观察**：把每个调用点第一个实参的观察写成 PATH（需要 --tier t2）")
    ap.add_argument("--values-in", metavar="PATH", default=None,
                    help="T3：读进值观察并按它特化（需要 --tier t3）")
    ap.add_argument("--deopt-out", metavar="PATH", default=None,
                    help="T3：把去优化记录写到 PATH（不写就不记）")
    ap.add_argument("--compact-out", metavar="PATH", default=None,
                    help="把入口编成**紧凑形**写进 PATH，不运行（docs/250）")
    ap.add_argument("--compact", metavar="PATH", default=None,
                    help="直接跑一份紧凑形产物（与 entry 二选一）")
    ap.add_argument("--profile-out", metavar="PATH", default=None,
                    help="T1：把反馈剖面写到 PATH（`docs/247`）。不写就不记，一个字节都不写")
    ap.add_argument("--counters-out", metavar="PATH", default=None,
                    help="把**确定性**工作量计数写到 PATH（issue #1028）：指令 / 解码 / "
                         "元组读取 / 特化 / 去优化。不写就不记")
    ap.add_argument("--fusion-out", metavar="PATH", default=None,
                    help="A4（docs/266）：把融合报告写到 PATH。不写就不记")
    ap.add_argument("--fusion-profile", metavar="PATH", default=None,
                    help="A4：只在这份 T1 剖面点过名的块里融合（chosen from the profile）")
    ap.add_argument("--cache", nargs="?", const="", metavar="PATH", default=None,
                    help="编译产物缓存（docs/265，#646）：缺省放在入口旁边（<entry>.lvmc），"
                         "给 PATH 就放那儿。只在 --tier t2 下有效；过期会**点名拒绝**并重编")
    a = ap.parse_args(argv)
    # `lvm_compact` 会经 `lvm_t2` 反过来 import 本模块 —— 只能在 `main()` 里、模块装完之后拿。
    import lvm_compact  # noqa: PLC0415

    if a.compact is not None and a.compact_out is not None:
        print("lvm: --compact 与 --compact-out 不能同时给", file=sys.stderr)
        return 2
    fusion_wanted = a.fusion_out is not None or a.fusion_profile is not None
    if fusion_wanted and a.compact_out is not None:
        print("lvm: --fusion-* 要跑程序，--compact-out 只编不跑", file=sys.stderr)
        return 2
    if fusion_wanted and a.compact is None and a.tier != "t2":
        print("lvm: --fusion-* 只在 --tier t2（或 --compact）下有效", file=sys.stderr)
        return 2
    if a.cache is not None:
        if a.tier != "t2":
            print("lvm: --cache 只在 --tier t2 下有效（缓存的是那一格的编译产物）",
                  file=sys.stderr)
            return 2
        if a.compact_out is not None or a.compact is not None:
            print("lvm: --cache 与 --compact/--compact-out 不能同时给", file=sys.stderr)
            return 2
    if a.entry is None and a.compact is None:
        print("lvm: 需要一个入口（.lomt / --ir 的 .ll），或 --compact PATH",
              file=sys.stderr)
        return 2
    if a.counters_out is not None and (a.compact is not None or a.compact_out is not None):
        print("lvm: --counters-out 只对 T0/T2/T3 有意义（紧凑形是另一格）",
              file=sys.stderr)
        return 2
    root = Path(a.root).resolve()
    buf = io.BytesIO()
    prof = Profile() if a.profile_out else None
    counters = Counters() if a.counters_out else None
    fusion = None
    hot = None
    if a.fusion_out is not None:
        import lvm_t2                                       # noqa: PLC0415
        fusion = lvm_t2.FusionReport()
    if a.fusion_profile is not None:
        import lvm_t2                                       # noqa: PLC0415
        hot = lvm_t2.parse_hot_blocks(
            Path(a.fusion_profile).read_text(encoding="utf-8"))
    values = None
    deopts = None
    if a.values_out is not None:
        if a.tier != "t2":
            print("lvm: --values-out 只在 --tier t2 下记（那条路才有调用点的下标）",
                  file=sys.stderr)
            return 2
        import lvm_t3                                       # noqa: PLC0415
        values = lvm_t3.Values()
        prof = prof if prof is not None else Profile()       # 记值要那条带下标的路径
    if a.tier == "t3":
        import lvm_t3                                       # noqa: PLC0415
        if a.values_in is not None:
            values = lvm_t3.Values.parse(Path(a.values_in).read_text(encoding="utf-8"))
        if a.deopt_out is not None:
            deopts = lvm_t3.DeoptLog()
    try:
        # `--entry` 那段文本**原样**当被解释程序的 argv[0] —— 与自举镜像同一处规矩：
        # "VM 被指到的那个输入路径"（`docs/243` §3.4）。
        if a.compact_out is not None:
            # 只编不跑：入口 -> IR -> 紧凑形。这是 docs/250 那条"写进文件"的路。
            ir = (Path(a.entry).read_text(encoding="utf-8") if a.ir
                  else compile_source(Path(a.entry).resolve(), root))
            Path(a.compact_out).write_bytes(lvm_compact.encode_ir(ir))
            return 0
        if a.compact is not None:
            rc = lvm_compact.run_compact(Path(a.compact).read_bytes(), buf, prof,
                                         [a.compact], fusion, hot)
        elif a.cache is not None:
            # 缓存那一趟：入口 -> IR -> （缓存命中就跳过编译）-> 紧凑形 -> 跑。
            # **IR 还是照常生成**（源输入要过前端）—— 缓存省的是 IR 之后那一段，
            # 也正是 `docs/259` §6 记的 62 ms / 24.7 ms 那一笔（`docs/265` §2）。
            import lvm_cache                                  # noqa: PLC0415

            entry = Path(a.entry).resolve()
            ir = (entry.read_text(encoding="utf-8") if a.ir
                  else compile_source(entry, root))
            cpath = Path(a.cache) if a.cache else lvm_cache.default_path(entry)
            payload = lvm_cache.load_or_build(cpath, Path(a.entry), ir)
            rc = lvm_compact.run_compact(payload, buf, prof, [a.entry], fusion, hot)
        elif a.ir:
            ir = Path(a.entry).read_text(encoding="utf-8")
            if a.tier == "t2":
                import lvm_t2                                # noqa: PLC0415
                rc = lvm_t2.run_ir_t2(ir, buf, prof, [a.entry], values,
                                      a.fusion_out is not None or a.fusion_profile is not None,
                                      fusion, hot, counters=counters)
            else:
                rc = run_ir(ir, buf, prof, [a.entry], a.tier, values, deopts, counters)
        else:
            if a.tier == "t2":
                import lvm_t2                                # noqa: PLC0415
                ir = compile_source(Path(a.entry).resolve(), root)
                rc = lvm_t2.run_ir_t2(ir, buf, prof, [a.entry], values,
                                      a.fusion_out is not None or a.fusion_profile is not None,
                                      fusion, hot, counters=counters)
            else:
                rc = run_source(Path(a.entry).resolve(), root, buf, prof, [a.entry],
                                a.tier, values, deopts, counters)
    except (LvmError, lomelf.ElfError, lomelf.Unsupported,
            lvm_compact.CompactError) as e:
        sys.stdout.buffer.write(buf.getvalue())
        print(f"lvm: {e}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(buf.getvalue())
    if values is not None and a.values_out is not None:
        with Path(a.values_out).open("w", encoding="utf-8", newline="\n") as f:
            f.write(values.text())
    if deopts is not None and a.deopt_out is not None:
        with Path(a.deopt_out).open("w", encoding="utf-8", newline="\n") as f:
            f.write(deopts.text())
    if fusion is not None and a.fusion_out is not None:
        with Path(a.fusion_out).open("w", encoding="utf-8", newline="\n") as f:
            f.write(fusion.text())
    if prof is not None and a.profile_out is not None:
        # `newline="\n"`：本机 `write_text` 会写成 CRLF，而剖面要跨平台逐字节可比。
        with Path(a.profile_out).open("w", encoding="utf-8", newline="\n") as f:
            f.write(prof.text())
    if counters is not None and a.counters_out is not None:
        with Path(a.counters_out).open("w", encoding="utf-8", newline="\n") as f:
            f.write(counters.text())
    return rc


if __name__ == "__main__":
    sys.exit(main())
