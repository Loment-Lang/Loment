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

import bisect
import collections
import os
import re
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402

#: 解释器自己的地址空间。**与自举镜像同一套布局**（`docs/365` 的跨实现快照）：
#: 4 MiB / 帧 32 KiB / 栈从顶上下压 / 全局从 64 KiB 起。
MEM_SIZE = 4 * 1024 * 1024
GLOBAL_BASE = 0x10000
STACK_TOP = MEM_SIZE
#: 每一层调用一段自己的栈区（按深度算基址 —— 见 `run` 里那段注释）。
FRAME_SIZE = 32768
#: 帧内**值表之后的槽区起点**（自举镜像的 `FR_SLOTS = 8 + 1024*16 + 128`）。
#: 两边必须一样：跨实现快照里的 alloca 地址是绝对地址。
FRAME_SLOTS = 16520
#: 堆的上限 = 栈底（`MEM_SIZE - MAX_DEPTH * FRAME_SIZE`），与镜像的 `STACK_LIMIT` 同。
STACK_LIMIT = MEM_SIZE - 32 * FRAME_SIZE

#: 每个标量占 8 字节（与 `lomelf` 的栈槽模型同口径，`SCALAR_SIZE`）。
SLOT = 8

#: **步数上限**（`docs/262`，issue #353）。镜像（`loment/tools/lvm.lomt`）一直有 `STEP_CAP`，
#: 参考实现**没有** —— 于是"挂死"与"慢程序"在参考那侧根本分不开。数的是**一次函数调用里
#: 执行的指令条数**（与镜像同口径），撞上就点名报函数 / 块 / 第几条，而不是等下去。
STEP_CAP = 50_000_000

#: **调用深度上限**。与镜像的 `MAX_DEPTH = 32` 取同一个数：跨实现快照的帧基址是
#: `MEM_SIZE - 深度 * FRAME_SIZE`，两边必须一致。语料实测最大深度 **10**。
MAX_DEPTH = 32

#: **合成文件**的 fd 从这儿起（`docs/243` §3.4）。解释器没有真的文件描述符，
#: 所以真 fd 与合成 fd 不会撞；这个数两边实现**必须一样**（自举镜像里也写 1000）。
VFD_BASE = 1000

#: `/proc/self/cmdline` 的字段分隔符（也是内核给的那个字节）。
B_NUL = bytes([0])

# ---------------------------------------------------------------- guest ABI（`docs/311`）
#
# **guest 看不到宿主。** 这张表说的是"guest 该看到什么"：固定是 Linux，与解释器自己
# 编成什么、跑在哪台机器上无关。四类（`docs/311` §2）：
#
#   S 合成   —— 答案由 guest 的身份/目标平台决定（`uname` 永远是 `Linux`）
#   E 模拟   —— 答案只由 guest 状态决定（`brk`、合成 fd 的 `read`/`close`）
#   F 转发   —— 答案里有一部分是"这台机器"的事实（`getpid`/`time`/`clock_gettime`）
#   R 拒绝   —— 这条 ABI 在 VM 里没有实现（网络族、`poll`/`select`、真实文件系统）
#
# 宿主能给什么**不在**这张表里 —— 那是 `HostProfile`（§3.1）。两张表分开，才有
# "同一份 IR、宿主换一份、guest 的行为不变"这句话。
GUEST_SYNTH, GUEST_EMULATE = "S", "E"
GUEST_FORWARD, GUEST_REFUSE = "F", "R"

#: 未知/未实现号的答案。Linux 对不认得的系统调用就是 `-ENOSYS`（`-38`）；guest 里
#: `if syscall(...) == -38 { … }` 是合法程序，所以这里**不 die** —— `die` 留给
#: "调用本身是坏的"（越界指针、非合成 fd），`-ENOSYS` 留给"这个号没有"（`docs/311` §4.2）。
ENOSYS = -38

GUEST_ABI = (
    (0, "read", GUEST_EMULATE),
    (1, "write", GUEST_EMULATE),
    (3, "close", GUEST_EMULATE),
    (12, "brk", GUEST_EMULATE),
    (39, "getpid", GUEST_FORWARD),
    (60, "exit", GUEST_EMULATE),
    (63, "uname", GUEST_SYNTH),
    (201, "time", GUEST_FORWARD),
    (228, "clock_gettime", GUEST_FORWARD),
    (257, "openat", GUEST_SYNTH),          # 只认 `/proc/self/cmdline`
    (41, "socket", GUEST_REFUSE),          # 网络族：VM 不做
    (7, "poll", GUEST_REFUSE),
)
GUEST_ABI_BY_NR = {nr: (name, kind) for nr, name, kind in GUEST_ABI}


class HostProfile:
    """宿主能给什么（`docs/311` §3.1）。**唯一允许随平台变的东西。**

    `elf` 无限制（内核就是 Linux ABI 的实现）；`pe` 只放行 `lomelf.PE_DISPATCH`
    里的号 —— 其余照 `__win_syscall` 的兜底回 `-ENOSYS`。于是"VM 转发了一个 PE
    没有的号"这件事，在**不用 Windows** 的前提下也能跑出来（§5 L2）。
    """

    __slots__ = ("name", "allow")

    def __init__(self, name: str, allow: frozenset | None = None) -> None:
        self.name = name
        self.allow = allow

    def can(self, nr: int) -> bool:
        return self.allow is None or nr in self.allow

    def __repr__(self) -> str:
        return f"HostProfile({self.name!r})"


def host_profile(name: str) -> HostProfile:
    if name == "elf":
        return HostProfile("elf")
    if name == "pe":
        return HostProfile("pe", frozenset(lomelf.PE_DISPATCH))
    raise LvmError(f"没有这一档宿主剖面: {name!r}（只有 elf / pe，`docs/311` §5 L2）")


class SyscallReport:
    """`--syscall-report`（`docs/311` §4.2）：把"这一条没实现"变成**可数的产物**。

    每条第一次出现的 `(号, 程序, 站点)` 一行，制表符分隔：
    `nr<TAB>类<TAB>program<TAB>fn/blk/idx`。同一站点重复命中只记一次 —— 循环里
    探测一个号不该把报告撑成程序长度的函数。
    """

    __slots__ = ("hits", "_seen")

    def __init__(self) -> None:
        self.hits: list[tuple[int, str, str, tuple[str, str, int]]] = []
        self._seen: set = set()

    def hit(self, nr: int, program: str, site, kind: str) -> None:
        key = (nr, program, tuple(site))
        if key in self._seen:
            return
        self._seen.add(key)
        self.hits.append((nr, kind, program, tuple(site)))

    def text(self) -> str:
        out = []
        for nr, kind, program, (fn, blk, idx) in self.hits:
            out.append(f"{nr}\t{kind}\t{program}\t{fn}/{blk}/{idx}\n")
        return "".join(out)


def forwarded_host_numbers(source: Path | None = None) -> frozenset[int]:
    """`loment/tools/lvm.lomt` 的 `vm_syscall` 直接转给宿主的号（`docs/311` §4.3）。

    这是**静态**判据：LVM 编成 PE 时，内层转发出去的每一个号都必须在外层的
    `PE_DISPATCH` 里 —— 缺一个，guest 拿到的是 `-ENOSYS`，与"Linux 上本来就没有
    这个号"**从 guest 侧分不出来**。等运行时才发现已经晚了，所以这里读源码。
    """
    path = source or (Path(__file__).resolve().parent.parent
                      / "loment" / "tools" / "lvm.lomt")
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        # 去掉行注释：`//` 里的花括号与 `syscall4(` 不算数。
        k = raw.find("//")
        lines.append(raw if k < 0 else raw[:k])
    text = "\n".join(lines)
    start = text.find("fn vm_syscall(")
    if start < 0:
        raise LvmError(f"{path}: 找不到 `vm_syscall`")
    brace = text.find("{", start)
    depth, i = 0, brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[brace:i]
    return frozenset(int(m) for m in re.findall(r"\bsyscall[467]\s*\(\s*(\d+)", body))


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

    __slots__ = ("instructions", "dispatches", "decodes", "tuple_lookups",
                 "specialisations", "deopts", "decoded", "decode_requests",
                 "specialised_sites")

    def __init__(self) -> None:
        self.instructions = 0
        #: **分派次数**：解释器主循环每取一次"下一步要做什么"记一。融合把两条合成一条，
        #: 于是 `dispatches` 降而 `instructions` 不变 —— 这是 A4 省下的那个量，确定性可比。
        self.dispatches = 0
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
            f"dispatches\t{self.dispatches}",
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


# ---------------------------------------------------------------- LVM-A 基质（docs/362）
#
# `docs/360` 把 LVM 的下一层写成一台**抽象机**：状态是显式的，六个原语里前四个
# （site / cap_check / gen_check / fault）是运行期指令。这一节落的是**第一档基质**
# `sw_cap` —— 本世代可交付的那一档。默认关（`--substrate none`），关着时**一个字节都不写**，
# 行为与从前逐字节相同（`docs/357` P8 / `docs/358` C10 的同一条纪律）。
#
# 对象表管三样东西：`base`/`limit`（W2 完备边界）、`gen`（W3 对象代）、`kind`（审计）。
# 能力（`Cap`）不是机器字，是解释器内部的 `(obj_id, gen)`：`docs/360` §3.1 的 `Cap`
# 在软件基质上就是**一对整数**，因为 LVM 的 `ptr` 仍是 64 位裸地址。

FAULT_BOUNDS = 1      # F01：访问越出对象
FAULT_DANGLING = 2    # F02：访问已经归还的对象
FAULT_NULL = 3        # F03：空指针解引用
FAULT_CODES = {
    FAULT_BOUNDS: "F01 bounds violation",
    FAULT_DANGLING: "F02 dangling reference",
    FAULT_NULL: "F03 null dereference",
}


class _Fault(Exception):
    """一条**语言事件**（`docs/356` §5.2）。站点与 witness 都带着，退出码 = 100 + kind。"""

    def __init__(self, kind: int, site, witness: str, detail: dict | None = None) -> None:
        self.kind, self.site, self.witness = kind, site, witness
        self.detail = detail or {}


#: `docs/366`：段错误详细化的标题与建议。首行保持不变（判据按它比），这一段跟在后面 ——
#: 形状抄 `loment/tools/lomenterr.lomt`：`error[码]: 标题 / --> 位置 / 源码行 + 插入符 / = 事实`。
_FAULT_TITLE = {
    FAULT_BOUNDS: ("F01", "访问越界（bounds）"),
    FAULT_DANGLING: ("F02", "悬垂引用（dangling）"),
    FAULT_NULL: ("F03", "空指针解引用（null）"),
}
_FAULT_TIP = {
    FAULT_BOUNDS: "检查下标与对象长度：这一次访问跨过了对象的上限。原生档在这条路径上是 UB，"
                  "LVM 把它变成这句话；上限表见 docs/263。",
    FAULT_DANGLING: "对象已经归还（代已加一），旧指针不能再解引用。归还之后再读/写是悬垂："
                    "原生档是 UB；sw_cap 按对象代抓，sw_tag 只按标签（可能漏，docs/320 §1.2 ⑧）。",
    FAULT_NULL: "指针是 0。空指针解引用在任何档都是错；先判空，或查清这个指针为什么没被赋过值。",
}


class _ObjSnap:
    """故障现场的对象**快照**。

    ⚠ 不能把 `_Obj` 本体放进 detail：异常栈解开时帧的 `finally` 会把这一帧的 alloca
    全部归还，等到顶层渲染时再看那个对象，它已经是「已归还、代加一」的另一个状态。
    第一版就是直接放了本体，于是越界被印成"已归还"。
    """

    __slots__ = ("oid", "base", "limit", "kind", "gen", "alive", "tag")

    def __init__(self, o) -> None:
        self.oid, self.base, self.limit = o.oid, o.base, o.limit
        self.kind, self.gen, self.alive, self.tag = o.kind, o.gen, o.alive, o.tag

    def size(self) -> int:
        return self.limit - self.base


def _snap(o) -> _ObjSnap | None:
    return None if o is None else _ObjSnap(o)


def _obj_line(o) -> str:
    state = "活着" if o.alive else "**已归还**"
    return (f"对象 #{o.oid}（{o.kind}）：base={o.base} limit={o.limit}"
            f"（{o.size()} 字节），第 {o.gen} 代，{state}")


def _render_dismem(kind: int, site, d: dict) -> str:
    """`docs/368` 的详细块：体的三维位形 + 令牌 + 越出体表的那一点。"""
    fn, blk, idx = site
    code, title = _FAULT_TITLE.get(kind, ("F0?", "故障"))
    out = [f"error[{code}]: {title}", f"  --> IR:{fn}:{blk}#{idx}"]
    ins = d.get("ins") or ""
    if ins:
        out += ["   |", f"   |   {ins}"]
    tok = d.get("token")
    if isinstance(tok, int):
        out.append(f"   | 令牌：0x{tok:016x}")
    kb = d.get("body_kind")
    if kb is not None:
        out.append(f"   | 体：{kb}（只报类别，不报编号与位置）")
    off, w = d.get("offset"), d.get("width") or 0
    if off is not None:
        out.append(f"   | 访问：令牌内偏移 {off}，宽 {w}")
    out.append("   |")
    reason = d.get("reason") or ""
    size = d.get("size")
    if reason == "over":
        fact = (f"偏移 {off} + 宽 {w} 越过体的上限 {size}：这一次跨出了体表（右越界）。"
                "这一档没有\"隔壁\"—— 体表之外不是内存。")
    elif reason == "walk":
        fact = (f"体内偏移 {off} 加上这个增量得到 {d.get('new_offset')}，"
                f"越出 [0,{size}]：令牌只能在**自己的体**里移动。")
    elif reason == "retired":
        fact = "这个令牌不在任何活体里：它属于已经退休的体（或从未存在）。令牌永不复用，所以它不会重新生效。"
    elif reason == "null":
        fact = f"令牌是 0（空引用），试图访问 {w} 字节。"
    elif reason in ("not-token", "code"):
        fact = ("这个值不是内存令牌（是普通整数/代码令牌）—— 这一档里指针只能是令牌；"
                "`inttoptr` 与 `ptrtoint` 都是 fault。")
    elif reason == "cast":
        fact = f"`{d.get('op')}` 把令牌与整数互转 —— 令牌不是数。"
    elif reason == "arith":
        fact = f"`{d.get('op')}` 把令牌当数用了 —— 只有体内偏移的加减是允许的。"
    elif reason == "add-bodies":
        fact = "两个令牌相加：没有地址空间可以把两颗体拼起来。"
    elif reason == "sub-bodies":
        fact = "两个不同体的令牌相减：没有地址空间可以量。"
    elif reason == "order-bodies":
        fact = "两个不同体的令牌比大小：没有地址序。"
    elif reason == "order-handle":
        fact = "令牌与整数比大小：没有地址序。"
    elif reason == "int-sub-body":
        fact = "整数减令牌：没有地址空间可以走。"
    elif reason == "brk-number":
        fact = "brk 拿到了一个整数：堆只能由**堆体的令牌 + 偏移**来长大。"
    elif reason == "brk-body":
        fact = "brk 拿到的是堆**以外**的体的令牌：堆只能由堆体长大。"
    elif reason == "size":
        fact = "申请的体超过 4 GiB：令牌的偏移字段放不下。"
    elif reason == "tokens":
        fact = "令牌序号用尽了（48 位，永不复用）—— 这是显式的身份预算，不是静默复用。"
    elif reason == "ids":
        fact = "运行期的体编号用尽了。"
    else:
        fact = f"令牌 0x{(tok or 0):016x}，访问 {w} 字节。"
    out.append(f"   = 事实：{fact}")
    depth = d.get("depth")
    where = f"   = 站点：函数 {fn} / 块 {blk} / 第 {idx} 条指令"
    if depth:
        where += f"；栈深 {depth}"
    out.append(where)
    dtip = {
        FAULT_BOUNDS: "体是实心长方体；任何跨越体表的访问都是 fault —— "
                      "它不是地址空间的下一页。检查下标，或换一个令牌。",
        FAULT_DANGLING: "令牌不在任何活体里：体已经退休。这一档没有代号可以比对，"
                        "令牌也永不复用 —— 它不会重新生效。",
        FAULT_NULL: "令牌是 0（空引用）；先判空。",
    }.get(kind, _FAULT_TIP.get(kind, ""))
    out.append(f"   = 建议：{dtip}")
    return "\n".join(out)


def render_fault(kind: int, site, detail: dict) -> str:
    """把一次故障渲染成 Lomenterr 形状的多行诊断（`docs/366`）。"""
    if detail.get("dismem"):
        return _render_dismem(kind, site, detail)
    fn, blk, idx = site
    code, title = _FAULT_TITLE.get(kind, ("F0?", "故障"))
    out = [f"error[{code}]: {title}",
           f"  --> IR:{fn}:{blk}#{idx}"]
    ins = detail.get("ins") or ""
    op = detail.get("op") or ""
    width = detail.get("width", 0)
    if ins:
        out.append("   |")
        out.append(f"   |   {ins}")
        if op and op in ins:
            col = ins.rindex(op)
            out.append(f"   |   {' ' * col}{'^' * max(4, len(op))}  这里访问 {width} 字节")
    out.append("   |")
    reason = detail.get("reason")
    obj = detail.get("obj")
    dead = detail.get("dead")
    live = detail.get("live")
    if obj is not None:
        out.append(f"   = {_obj_line(obj)}")
    if dead is not None and dead is not obj:
        out.append(f"   = 已归还的那一个：{_obj_line(dead)}")
    addr = detail.get("addr")
    if reason == "null":
        fact = f"指针是 0（空指针），试图访问 {width} 字节"
    elif reason == "unmapped":
        fact = f"没有任何对象覆盖地址 {addr}（访问 {width} 字节）—— 地址不在对象表里"
    elif reason == "retired":
        asked = detail.get("asked") or (None, None)
        fact = (f"能力指向 #{asked[0]} 第 {asked[1]} 代；那个对象已经归还，"
                f"现在是第 {obj.gen} 代" if obj is not None
                else f"能力指向的对象 #{asked[0]} 已经不在对象表里（快照/放置不一致）")
        if live is not None:
            fact += f"。地址 {addr} 现在住着 #{live.oid}（{live.kind}，第 {live.gen} 代）"
    elif reason == "retired-addr":
        fact = (f"地址 {addr} 落在已归还的对象 #{dead.oid}（第 {dead.gen} 代）里；"
                f"这次访问 {width} 字节")
    elif reason == "over":
        off = detail.get("offset", 0)
        over = off + width - obj.size() if obj is not None else 0
        fact = (f"地址 {addr} 在对象内，偏移 {off}；访问 {width} 字节越过上限 {over} 字节"
                f"（右越界）")
    elif reason == "under":
        off = detail.get("offset", 0)
        fact = f"地址 {addr} 在对象起点**之前**（偏移 {off}）；访问 {width} 字节"
    else:
        fact = f"地址 {addr}，访问 {width} 字节"
    out.append(f"   = 事实：{fact}")
    depth = detail.get("depth")
    where = f"   = 站点：函数 {fn} / 块 {blk} / 第 {idx} 条指令"
    if depth:
        where += f"；栈深 {depth}"
    out.append(where)
    out.append(f"   = 建议：{_FAULT_TIP.get(kind, '')}")
    return "\n".join(out)


def fault_exit(kind: int, site, witness: str, detail: dict | None = None) -> int:
    """`fault()` 的共同出口：**100 + kind** + 一行站点（与 `llvm.trap` 的 132 同形）。

    首行保持稳定（孪生/故障判据按它比）；`docs/366` 的详细块跟在后面。
    """
    fn, blk, idx = site
    print(f"lvm: {FAULT_CODES[kind]} at {fn}:{blk}#{idx} ({witness})", file=sys.stderr)
    if detail:
        print(render_fault(kind, site, detail), file=sys.stderr)
    return 100 + kind


class _Obj:
    __slots__ = ("oid", "base", "limit", "gen", "alive", "kind", "tag")

    def __init__(self, oid: int, base: int, size: int, kind: str) -> None:
        self.oid, self.base, self.limit, self.kind = oid, base, base + size, kind
        self.gen = 0
        self.alive = True
        #: `sw_tag` 的 4 位标签（1..15；0 留给"无能力"）。`sw_cap` 不用它。
        self.tag = 0

    def size(self) -> int:
        return self.limit - self.base


class SwCap:
    """`sw_cap` 基质：对象表 + 能力检查 + 对象代。

    对象从五处来：全局（启动时一次）、`command_main` 的 argv、`brk` 授出的区、
    `alloca`（每执行一次一个）、`__loment_alloc`（分配器返回之后登记）。
    归还发生在 `__loment_free` 返回之后与栈帧返回时 ——
    代加一，对象离开「活的」索引，但**留在表里**，好让旧能力按 `(obj_id, gen)` 对出 F02。

    ⚠ **边界（诚实写在这里，不留给读者猜）**：`ptr` 在 IR 里是 64 位裸地址。能力只活在
    解释器的 SSA 侧（`%name -> (obj_id, gen)`）与指针存储的影子表里；一旦地址被洗成整数
    再变回指针（`ptrtoint` → 算术 → `inttoptr`，语言里 `as u64` 就是这条），能力就丢了，
    只能按**地址**找对象。地址被下一次 `alloc` 复用之后，旧指针与一个新指针**长得一模一样**
    —— 这一档的对象代抓不住它。非概率的对象代要求指针本身携带代（胖指针 / 槽表），
    那是 `docs/360` §3.3 的 `hw_cap` 与 `docs/320` S1 的事，不在本档。
    """

    #: 对象表上限。越过它就是**基质自己的失败**，不是程序的行为 —— 响亮地拒。
    MAX_OBJECTS = 1 << 20
    #: 按地址回查已归还对象时最多看最近的多少条。更早的不是"对"，是"没查"。
    RETIRED_SCAN = 4096

    #: `sw_cap`：对象代（精确）。`sw_tag`：4 位标签（概率，MTE 形状）。
    def __init__(self, mode: str = "cap") -> None:
        self.mode = mode
        self.objs: dict[int, _Obj] = {}
        self.next_oid = 1
        #: `sw_tag` 的标签序列：1..15 轮转。第 16 次分配回到 1 —— 这正是
        #: 「4 位只有 15 个非零值」的后果，判据拿它造一次**碰撞**。
        self.tag_seq = 0
        #: 活对象按 `base` 索引；`bases` 保持升序，查地址用二分。
        self.live: dict[int, int] = {}
        self.bases: list[int] = []
        #: 已归还对象，最近的在末尾。只为「裸地址回查」保留一个有限窗口。
        self.retired: list[_Obj] = []
        #: 指针存储的影子表：地址 -> 存进去的那个指针的能力。
        self.shadow: dict[int, tuple[int, int, int]] = {}

    # ------------------------------------------------------------ 对象
    def new_obj(self, base: int, size: int, kind: str) -> int:
        if len(self.objs) >= self.MAX_OBJECTS:
            raise LvmError(f"sw_cap 对象表满了（上限 {self.MAX_OBJECTS}）")
        prev = self.live.get(base)
        if prev is not None:
            if self.objs[prev].kind == "global":
                # 基座的不变量：全局在启动时一次摆好，之后任何分配都不该压在它头上。
                raise LvmError(f"sw_cap：新对象与全局重叠（base={base:#x}）")
            self.retire_oid(prev)
        oid = self.next_oid
        self.next_oid += 1
        o = _Obj(oid, base, size, kind)
        if self.mode == "tag":
            o.tag = 1 + (self.tag_seq % 15)
            self.tag_seq += 1
        self.objs[oid] = o
        self.live[base] = oid
        bisect.insort(self.bases, base)
        return oid

    def cap_of(self, oid: int) -> tuple[int, int, int]:
        o = self.objs[oid]
        return (o.oid, o.gen, o.tag)

    def retire_oid(self, oid: int) -> None:
        o = self.objs.get(oid)
        if o is None or not o.alive:
            return
        o.alive = False
        o.gen += 1
        self.retired.append(o)
        if len(self.retired) > self.RETIRED_SCAN:
            self.retired.pop(0)
        if self.live.get(o.base) == oid:
            del self.live[o.base]
            i = bisect.bisect_left(self.bases, o.base)
            if i < len(self.bases) and self.bases[i] == o.base:
                self.bases.pop(i)
        self._drop_shadow(o.base, o.limit)

    def grant_brk(self, base: int, want: int) -> None:
        """`brk(want)` 授出的那段内存 —— 命令单元的 `_start` 就是这么拿到 cmdline 缓冲的。"""
        oid = self.live.get(base)
        o = self.objs.get(oid) if oid is not None else None
        if o is not None and o.alive and o.kind == "brk":
            o.limit = max(o.limit, want)
            return
        self.new_obj(base, max(0, want - base), "brk")

    def retire_addr(self, addr: int) -> None:
        """`__loment_free(addr)` 之后归还**堆对象**。

        ⚠ 只认 `kind == "heap"` 且 `base == addr`：`free` 的实参就是载荷首地址，而分配器
        自己会挡双重释放 / 非载荷指针。这里若退而按地址找，第二次 `free` 会找到外层
        `@__loment_heap` 全局，把整块 arena 当成"已归还" —— 一个安静的灾难。
        """
        oid = self.live.get(addr)
        if oid is None:
            return
        if self.objs[oid].kind != "heap":
            return
        self.retire_oid(oid)

    def _drop_shadow(self, base: int, limit: int) -> None:
        stale = [a for a in self.shadow if base <= a < limit]
        for a in stale:
            del self.shadow[a]

    # ------------------------------------------------------------ 查
    def lookup_live(self, addr: int) -> _Obj | None:
        """**最内层**活对象（全局里嵌着堆对象，堆对象优先）。"""
        i = bisect.bisect_right(self.bases, addr) - 1
        while i >= 0:
            o = self.objs[self.live[self.bases[i]]]
            if o.base <= addr < o.limit:
                return o
            i -= 1
        return None

    def lookup_retired(self, addr: int) -> _Obj | None:
        for o in reversed(self.retired):
            if o.base <= addr < o.limit:
                return o
        return None

    def innermost_dead(self, addr: int) -> _Obj | None:
        """地址上**最内层**的已归还对象。

        ⚠ 这一条是必须的：堆对象住在 `@__loment_heap` 这个全局里面，全局是**活的**、
        永远覆盖整块 arena。只按「有没有活对象盖住」判，归还之后每一个地址都还能
        落进那个外层全局 —— 悬垂永远抓不住。所以「最内层」的归属比存活更重要。
        """
        live = self.lookup_live(addr)
        # 热路径：地址落在**内层**活对象里时，已归还表不必看 —— 悬垂只会发生在
        # 内层对象没了、外层容器（全局 / `brk`）还在的时候。
        if live is not None and live.kind not in ("global", "brk"):
            return None
        dead = self.lookup_retired(addr)
        if dead is not None and (live is None or dead.base > live.base):
            return dead
        return None

    def cap_for_addr(self, addr: int) -> tuple[int, int, int] | None:
        o = self.lookup_live(addr)
        if o is None:
            return None
        # 最内层的归属是已归还对象时，这个地址"没有主人" —— 外层 arena 全局不算数。
        if self.innermost_dead(addr) is not None:
            return None
        return (o.oid, o.gen, o.tag)

    # ------------------------------------------------------------ 检查
    def check(self, addr: int, width: int, cap: tuple[int, int, int] | None,
              site, ctx: dict | None = None) -> None:
        """`P2 cap_check` + `P3 gen_check` 的合一：一次访问前问一句「凭什么碰」。

        `ctx` 是渲染用的现场（指令文本、指针操作数、栈深）—— 不影响判决，只影响
        `docs/366` 那段详细诊断。判据只比首行，所以缺了它也不改变行为。
        """
        detail = dict(ctx or {})
        detail.update(addr=addr, width=width)
        if addr == 0 and width > 0:
            detail["reason"] = "null"
            raise _Fault(FAULT_NULL, site, f"size={width}", detail)
        if self.mode == "tag":
            self._check_tag(addr, width, cap, site, detail)
            return
        if cap is not None:
            o = self.objs.get(cap[0])
            if o is None or not o.alive or o.gen != cap[1]:
                detail.update(obj=_snap(o), asked=(cap[0], cap[1]), reason="retired",
                              live=_snap(self.lookup_live(addr)))
                raise _Fault(FAULT_DANGLING, site,
                             f"object {cap[0]} gen {cap[1]} -> retired", detail)
            off = addr - o.base
            if off < 0 or off + width > o.size():
                detail.update(obj=_snap(o), offset=off,
                              reason="under" if off < 0 else "over")
                raise _Fault(FAULT_BOUNDS, site,
                             f"offset={off} size={width} limit={o.size()}", detail)
            return
        dead = self.innermost_dead(addr)
        if dead is not None:
            detail.update(dead=_snap(dead), reason="retired-addr",
                          live=_snap(self.lookup_live(addr)))
            raise _Fault(FAULT_DANGLING, site,
                         f"address={addr} was freed (object {dead.oid})", detail)
        o = self.lookup_live(addr)
        if o is not None:
            if addr + width > o.limit:
                detail.update(obj=_snap(o), offset=addr - o.base, reason="over")
                raise _Fault(FAULT_BOUNDS, site,
                             f"address={addr} size={width} limit={o.size()}", detail)
            return
        detail["reason"] = "unmapped"
        raise _Fault(FAULT_BOUNDS, site,
                     f"no object covers address={addr} size={width}", detail)

    def _check_tag(self, addr: int, width: int, cap, site, detail: dict) -> None:
        """`sw_tag`：比较**4 位标签**，不是对象代。同标签的重用会漏 —— 那是声明的降级。

        `docs/320` §1.2 ⑧：MTE 是检测，不是保证。这里的 15 个非零标签就是那个概率。
        """
        live = self.lookup_live(addr)
        if cap is not None and live is not None and cap[2] == live.tag:
            if addr + width > live.limit:
                detail.update(obj=_snap(live), offset=addr - live.base, reason="over")
                raise _Fault(FAULT_BOUNDS, site,
                             f"address={addr} size={width} limit={live.size()}", detail)
            return
        if cap is not None:
            o = self.objs.get(cap[0])
            if o is not None and o.alive:
                off = addr - o.base
                if off < 0 or off + width > o.size():
                    detail.update(obj=_snap(o), offset=off,
                                  reason="under" if off < 0 else "over")
                    raise _Fault(FAULT_BOUNDS, site,
                                 f"offset={off} size={width} limit={o.size()}", detail)
                return
            detail.update(obj=_snap(o), asked=(cap[0], None), reason="retired",
                          live=_snap(live))
            if live is not None:
                raise _Fault(FAULT_DANGLING, site,
                             f"tag {cap[2]} != live tag {live.tag}", detail)
            raise _Fault(FAULT_DANGLING, site, f"address={addr} was freed", detail)
        if live is not None:
            dead = self.innermost_dead(addr)
            if dead is not None:
                detail.update(dead=_snap(dead), reason="retired-addr", live=_snap(live))
                raise _Fault(FAULT_DANGLING, site, f"address={addr} was freed", detail)
            if addr + width > live.limit:
                detail.update(obj=_snap(live), offset=addr - live.base, reason="over")
                raise _Fault(FAULT_BOUNDS, site,
                             f"address={addr} size={width} limit={live.size()}", detail)
            return
        dead = self.lookup_retired(addr)
        if dead is not None:
            detail.update(dead=_snap(dead), reason="retired-addr")
            raise _Fault(FAULT_DANGLING, site, f"address={addr} was freed", detail)
        detail["reason"] = "unmapped"
        raise _Fault(FAULT_BOUNDS, site,
                     f"no object covers address={addr} size={width}", detail)


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
        size = max(1, len(data))
        addr = vm.mem.alloc(size)
        if data:
            vm.mem.put(addr, data)
        if vm.sub is not None:
            vm.sub.new_obj(addr, size, "argv")
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
        if self.heap >= STACK_LIMIT:
            raise LvmError("堆撞上栈 —— 地址空间用完了")
        return a


class Frame:
    """一次调用的栈帧：SSA 名 -> 值；`alloca` 的槽也在这一层。"""

    def __init__(self, mem: Mem, base: int) -> None:
        self.mem = mem
        self.base = base          # 帧底（往上是本帧的槽）
        self.slots: dict[str, int] = {}
        self.vals: dict[str, object] = {}
        #: `sw_cap` 基质用的两张旁表（关着时恒为空，没有代价）：
        #: SSA 名 -> 能力 `(obj_id, gen, tag)`；本帧登记过、返回时要归还的对象。
        self.caps: dict[str, tuple[int, int, int]] = {}
        self.objs: list[int] = []
        #: 槽从**值表之后**起（`FRAME_SLOTS`），与自举镜像的 `FR_SLOTS` 同址。
        #: dismemory 下 `next` 是**体内偏移**，槽由令牌表派生（`base + n` 对不透明令牌没有意义）。
        self.next = (FRAME_SLOTS if isinstance(mem, Dismem) else base + FRAME_SLOTS)

    def slot(self) -> int:
        a = (self.next + SLOT - 1) // SLOT * SLOT
        self.next = a + SLOT
        if isinstance(self.mem, Dismem):
            # 帧体是 `[u8; FRAME_SIZE]`；槽 = 这个数组的元素（`docs/377` §2.3）。
            return self.mem.elem(self.base, a, FRAME_SIZE, 1)
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
                 counters: "Counters | None" = None, sub: "SwCap | None" = None,
                 dismemory: bool = False, relocate_every: int = 0,
                 shuffle: int = 0,
                 host: HostProfile | None = None,
                 report: "SyscallReport | None" = None) -> None:
        #: 宿主剖面（`docs/311` §3.1）：`elf` 什么都给，`pe` 只给 `PE_DISPATCH` 里的号。
        #: 只影响 F 类（转发）—— S/E 类按定义不碰宿主，R 类永远是同一个答案。
        self.host = host if host is not None else host_profile("elf")
        #: `--syscall-report`：`None` = 不记（关掉时一个字节都不写，与剖面同一条纪律）。
        self.syscall_report = report
        #: `docs/368`：没有地址的那一档。关着时 `mem` 仍是 `Mem`，一个字节都不多走。
        self.dismemory = dismemory
        self.relocate_every = relocate_every
        self.mem = Dismem(shuffle) if dismemory else Mem()
        if dismemory:
            self.mem.site = ("?", "?", 0)
        #: 基质（`docs/362`）。`None` = 今天的 LVM，**一个字节都不写**。
        self.sub = sub
        #: 处在运行期内部函数（`__loment_*`）里时 > 0 —— 它们是**基座自己的代码**：
        #: 分配器要写已归还的块、要碰块头，按定义不受能力检查约束（`docs/360` W2 的
        #: "对象表必须覆盖所有可达内存"说的是 guest，不是基座）。
        self.priv = 0
        #: 全局名 -> 能力。启动时登记，永不归还。
        self.global_caps: dict[str, tuple[int, int]] = {}
        self.globals = {g.name: g for g in globals_}
        self.funcs = {f.name: f for f in funcs}
        #: 代码地址（C2 虚表 / 函数值）：`ptrtoint @fn` 与 `call %fp` 要在同一张表上。
        #: 取内存区之外的高地址，避免与堆/栈槽撞车。
        self.fn_addr = {f.name: (1 << 40) + i * 16
                        for i, f in enumerate(funcs)}
        self.addr_fn = {a: n for n, a in self.fn_addr.items()}
        self.externs = set(externs)
        self.out = out
        self.depth = 0
        #: 全局已执行指令数（两个引擎共用）：dismemory 的虚拟钟按它走（`docs/371`）。
        self.executed = 0
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
            g.addr = (self.mem.alloc(len(data), align, kind="global")
                      if self.dismemory else self.mem.alloc(len(data), align))
            if self.dismemory:
                self.mem.put(g.addr, data, g.ty)
            else:
                self.mem.put(g.addr, data)
            if self.sub is not None:
                oid = self.sub.new_obj(g.addr, len(data), "global")
                self.global_caps[g.name] = self.sub.cap_of(oid)

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
            if v[1:] in self.fn_addr:
                return ty, self.fn_addr[v[1:]]
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
        if self.dismemory:
            self.mem.site = self.site
            raw = self.mem.get(addr, n, ty)
        else:
            raw = self.mem.get(addr, n)
        if _is_agg(ty):
            return raw
        return int.from_bytes(raw, "little")

    def store(self, ty: str, addr: int, val) -> None:
        if self.dismemory:
            self.mem.site = self.site
        if _is_agg(ty):
            blob = val if isinstance(val, bytes) else bytes(val)
            if self.dismemory:
                self.mem.put(addr, blob, ty)
            else:
                self.mem.put(addr, blob)
            return
        n = lomelf.size_of(ty)
        blob = (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little")
        if self.dismemory:
            self.mem.put(addr, blob, ty)
        else:
            self.mem.put(addr, blob)



# ---------------------------------------------------------------- Dismemory（docs/368）
#
# **这一档没有地址。** guest 的"指针"是**体的句柄**：`[tag=D1][gen][body][offset]`。
# 访存只在**这颗体**的 `offset < size` 里；句柄不是数（ptrtoint/inttoptr/位运算都是
# fault），也没有全局序（跨体相减/排序是 fault）。体被排进一个**隐藏的、不重叠的
# 三维格子**里；重定位只改运行期的坐标，不改任何 guest 值 —— 所以没有悬空指针。
#
# 关着时（默认）这条路一个字节都不走：`Vm.mem` 仍是 `Mem`，所有分支都由
# `self.dismemory` 把着。

DISM_TAG = 0xD1          # 内存令牌（不透明）
CODE_TAG = 0xC0          # 代码令牌（`@fn`）—— 另一域，不是内存
TOKEN_MAX = 1 << 48      # 令牌序号 48 位；**永不复用**

FAULT_NOADDR = 4         # F04：令牌不是数
FAULT_NOORDER = 5        # F05：没有跨体的地址空间
FAULT_LIMIT = 6          # F06：令牌序号/表容量用尽
FAULT_CODES[FAULT_NOADDR] = "F04 no addresses"
FAULT_CODES[FAULT_NOORDER] = "F05 no address space"
FAULT_CODES[FAULT_LIMIT] = "F06 identity exhausted"
_FAULT_TITLE[FAULT_NOADDR] = ("F04", "没有地址（no addresses）")
_FAULT_TITLE[FAULT_NOORDER] = ("F05", "没有全局空间（no address space）")
_FAULT_TITLE[FAULT_LIMIT] = ("F06", "身份预算用尽（identity exhausted）")
_FAULT_TIP[FAULT_NOADDR] = (
    "这一档里指针是一个**不透明令牌**，不是数：ptrtoint / inttoptr / 把令牌拿去做"
    "位运算是 fault。要跨内存就用多个令牌或显式链接，不要把它算成整数。")
_FAULT_TIP[FAULT_NOORDER] = (
    "令牌只在它自己的体里有意义；跨体相减、排序、brk 都是 fault —— 没有地址空间可以走。")
_FAULT_TIP[FAULT_LIMIT] = (
    "64 位令牌的序号 48 位、**永不复用**；用尽或令牌表满就走这里。它是显式失败，"
    "不是静默复用（docs/368 §1.7）。")


def d_is_handle(v) -> bool:
    return isinstance(v, int) and 0 <= v < (1 << 64) and (v >> 56) == DISM_TAG


def d_is_code(v) -> bool:
    return isinstance(v, int) and 0 <= v < (1 << 64) and (v >> 56) == CODE_TAG


class _DismBody:
    """运行期内部的**体**。编号与坐标**不进 guest 值** —— 只活在令牌表后面。"""

    __slots__ = ("id", "kind", "size", "dx", "dy", "dz", "x", "y", "z",
                 "cells", "shift", "alive", "tokens")

    def __init__(self, bid: int, kind: str, n: int, shift: int = 0) -> None:
        self.id, self.kind = bid, kind
        self.size = max(0, int(n))
        d = 1
        while d * d * d < self.size:
            d += 1
        self.dx = d
        self.dy = d
        self.dz = max(1, (self.size + d * d - 1) // (d * d))
        self.x = self.y = self.z = 0
        #: `docs/377` §2：体是一张 `路径 -> 叶字节` 的映射。路径是**结构位置**
        #: （数组第 i 个元素 / 结构第 f 个字段），不是体内字节偏移。
        self.cells: dict[tuple, bytes] = {}
        #: 深度 3 的摆法在深度 4 没有物理键可换；保留字段只为快照字段对齐。
        self.shift = 0
        self.alive = True
        self.tokens: set[int] = set()


class Dismem:
    """**一张令牌表 + 一组体。** guest 拿到的 64 位值是一个**不透明令牌**：里面既没有
    体的编号，也没有偏移 —— 唯一的含义是"表里的那一行"。访存=查表，算术=在表里派生
    一个新令牌（到体外的派生直接 fault）。"""

    HEAP_VOLUME = STACK_LIMIT - GLOBAL_BASE

    def __init__(self, shuffle: int = 0) -> None:
        #: 表示层摆法（`docs/375` §2.3）：0 = 规范；非 0 给每颗体一个确定性的移位。
        self.shuffle = int(shuffle)
        self.bodies: dict[int, _DismBody] = {}
        #: **活体**（退休的不在这里）。摆放只跟活体比 —— 否则每次 `alloca` 都要扫
        #: 一遍"这辈子造过的所有体"，循环里调函数的程序是 O(n²)（2026-11-22 实测
        #: 2000 次 store32 要 57s，`docs/375` §6）。
        self.live: dict[int, _DismBody] = {}
        self.next_bid = 1
        #: 令牌 -> (体编号, 路径, 叶宽)。**永不外露**；表里没有 offset。
        self.tokens: dict[int, tuple[int, tuple, int]] = {}
        #: (体编号, 路径) -> 令牌。同一个引用点只有**一个**令牌（`docs/377` §2）。
        self.rev: dict[tuple[int, tuple], int] = {}
        self.next_serial = 1
        #: 退休令牌的墓碑（只为诊断；令牌永不复用，所以它只增不减，按容量截断）。
        self.tomb: dict[int, tuple[int, str]] = {}
        self.round = 0
        self.site = None
        #: 当前指令的现场（指令文本/栈深），由 VM 在访存前放进；
        #: `map_detail` 把它并进故障现场。
        self.hint: dict = {}
        self.sp = 0
        self.heap = self.alloc(self.HEAP_VOLUME, kind="heapspan")

    # ------------------------------------------------------------ 排版
    def _overlap(self, x, y, z, dx, dy, dz, me=None) -> bool:
        for b in self.live.values():
            if b is me:
                continue
            if (x < b.x + b.dx and b.x < x + dx
                    and y < b.y + b.dy and b.y < y + dy
                    and z < b.z + b.dz and b.z < z + dz):
                return True
        return False

    def _place(self, b: "_DismBody") -> None:
        x = self.round
        y = 0
        z = 0
        while True:
            if not self._overlap(x, y, z, b.dx, b.dy, b.dz, b):
                b.x, b.y, b.z = x, y, z
                return
            x += max(1, b.dx)
            if x > 1 << 16:
                x = self.round
                y += max(1, b.dy)
                if y > 1 << 16:
                    y = 0
                    z += max(1, b.dz)

    def relocate(self) -> None:
        """重排全部活体 —— 改的是**运行期坐标**，令牌与 guest 值一个都不动。"""
        self.round += 1
        for b in sorted(self.live.values(), key=lambda q: q.id):
            self._place(b)

    # ------------------------------------------------------------ 令牌
    def _new_token(self, b: "_DismBody", path: tuple, leaf: int) -> int:
        key = (b.id, path)
        got = self.rev.get(key)
        if got is not None:
            return got
        serial = self.next_serial
        if serial >= TOKEN_MAX:
            self._fault(FAULT_LIMIT, "token serials exhausted (%d)" % serial,
                        {"dismem": True, "reason": "tokens", "width": 8})
        self.next_serial = serial + 1
        t = (DISM_TAG << 56) | serial
        self.tokens[t] = (b.id, path, leaf)
        self.rev[key] = t
        b.tokens.add(t)
        return t

    def map_detail(self, h, width: int, reason: str) -> dict:
        key = self.tokens.get(h) if isinstance(h, int) else None
        d: dict = {"dismem": True, "reason": reason, "token": h, "width": width}
        if self.hint:
            d.update(self.hint)
        if key is None:
            dead = self.tomb.get(h) if isinstance(h, int) else None
            if dead is not None:
                d.update({"body_kind": dead[1], "dead": True})
            return d
        _bid, path, leaf = key
        b = self.bodies.get(_bid)
        d.update({"path_len": len(path), "leaf": leaf})
        if path:
            d.update({"step": path[-1][1], "steps": [s[1] for s in path]})
        if b is not None:
            d.update({"body_kind": b.kind, "size": b.size})
        return d

    def _fault(self, kind: int, witness: str, detail: dict):
        raise _Fault(kind, self.site or ("?", "?", 0), witness, detail)

    def decode(self, h, width: int):
        """令牌 -> (体, 路径, 叶宽)。**表里没有 = 身份不存在**（F02）。"""
        if not isinstance(h, int) or h == 0:
            self._fault(FAULT_NULL, "size=%d" % width,
                        self.map_detail(0, width, "null"))
        if not d_is_handle(h):
            if d_is_code(h):
                self._fault(FAULT_NOADDR, "code token used as memory",
                            {"dismem": True, "reason": "code", "token": h,
                             "width": width})
            self._fault(FAULT_NOADDR, "not a reference token",
                        {"dismem": True, "reason": "not-token", "token": h,
                         "width": width})
        key = self.tokens.get(h)
        if key is None:
            self._fault(FAULT_DANGLING, "token=%#x not live" % h,
                        self.map_detail(h, width, "retired"))
        bid, path, leaf = key
        b = self.bodies.get(bid)
        if b is None or not b.alive:
            self._fault(FAULT_DANGLING, "token=%#x retired" % h,
                        self.map_detail(h, width, "retired"))
        if width > leaf:
            self._fault(FAULT_BOUNDS,
                        "width=%d leaf=%d path_steps=%d" % (width, leaf, len(path)),
                        self.map_detail(h, width, "over"))
        return b, path, leaf

    # ------------------------------------------------------------ Mem 接口
    def alloc(self, n: int, align: int = 8, kind: str = "heap") -> int:
        size = max(0, int(n))
        if size > 0xFFFFFFFF:
            self._fault(FAULT_LIMIT, "body size=%d > 4 GiB" % size,
                        {"dismem": True, "reason": "size", "width": 0})
        bid = self.next_bid
        self.next_bid += 1
        # 摆法（`docs/375` §2.3）：只改物理键的写法，逻辑格号与令牌不动。
        shift = 0 if self.shuffle == 0 else (
            (self.shuffle * 2654435761 + bid * 40503) & 0x7FFFFFFF)
        b = _DismBody(bid, kind, size, 0)
        self.bodies[bid] = b
        self._place(b)
        self.live[bid] = b
        return self._new_token(b, (), size)

    def retire(self, h: int) -> None:
        key = self.tokens.get(h) if isinstance(h, int) else None
        if key is None:
            return
        bid, _path, _leaf = key
        b = self.bodies.get(bid)
        if b is None or not b.alive:
            return
        # **这一颗体的所有令牌一起作废**：它们从此不在表里 —— 不需要代号，也没有 ABA。
        for t in list(b.tokens):
            key2 = self.tokens.pop(t, None)
            if key2 is not None:
                self.rev.pop(key2, None)
            self.tomb[t] = (bid, b.kind)
        if len(self.tomb) > 4096:
            for t in list(self.tomb)[:1024]:
                self.tomb.pop(t, None)
        b.tokens.clear()
        b.alive = False
        b.cells.clear()
        self.live.pop(bid, None)

    # ------------------------------------------------------------ 叶访问
    def put(self, h, data, ty: str | None = None) -> None:
        b, path, leaf = self.decode(h, len(data))
        if ty is not None and _is_agg(ty):
            self._put_agg(b, path, ty, bytes(data))
            return
        if any(data):
            b.cells[path] = bytes(data)
        else:
            b.cells.pop(path, None)

    def get(self, h, n: int, ty: str | None = None) -> bytes:
        b, path, leaf = self.decode(h, n if ty is None else 0)
        if ty is not None and _is_agg(ty):
            return self._get_agg(b, path, ty)
        return bytes(b.cells.get(path, b"\0" * n)[:n]).ljust(n, b"\0")

    # ------------------------------------------------------------ 路径派生
    def gep(self, h: int, delta: int, ty: str, rest: list[int]) -> int:
        """`getelementptr ty, ptr %p, delta, rest...` -> 新令牌（`docs/377` §2.2）。

        第 0 个下标是"沿指针的元素推进"：若当前路径末尾就是一个数组元素，
        就在**同一根数组轴**上前进；否则从体根起算（体尺寸 / sizeof(ty) 个元素）。
        越界在**派生时**就是 F01 —— 不是等到 load。"""
        b, path, _leaf = self.decode(h, 0)
        width = lomelf.size_of(ty)
        scalar = not (ty.startswith("[") or ty.startswith("{"))
        if delta == 0 and scalar and not (
                path and path[-1][3] == width):
            # `gep T, p, 0`：指针停在同一根数组的**第 0 个元素**上；但若 p 已经指向
            # 一个**更宽**的元素（字段/数组元素），用更窄的 T 去指它是**字节重解释**
            # —— 深度 4 不主张这件事（`docs/377` §2.2，F04）。
            if path:
                d = self.map_detail(h, width, "gep")
                d["want"] = width
                self._fault(FAULT_NOADDR, "reinterpret a wider element",
                            d)
            cnt = (b.size // width) if width > 0 else 0
            if cnt <= 0:
                d = self.map_detail(h, width, "gep")
                d["index"] = 0
                d["count"] = cnt
                self._fault(FAULT_BOUNDS, "index=0 count=0", d)
            path = ((0, 0, cnt, width),)
        elif delta != 0:
            if path and path[-1][0] == 0 and path[-1][3] == width:
                kind, idx, cnt, w = path[-1]
                nidx = idx + delta
                if nidx < 0 or nidx >= cnt:
                    d = self.map_detail(h, width, "gep")
                    d["index"] = nidx
                    d["count"] = cnt
                    self._fault(FAULT_BOUNDS, "index=%d count=%d" % (nidx, cnt), d)
                path = path[:-1] + ((0, nidx, cnt, w),)
            elif path:
                # 从一个字段 / 不同宽度的元素上"往前挪"不是结构操作（`docs/377` §2.2）。
                d = self.map_detail(h, width, "gep")
                self._fault(FAULT_NOADDR, "pointer step from a non-element reference", d)
            else:
                cnt = (b.size // width) if width > 0 else 0
                if delta < 0 or delta >= cnt:
                    d = self.map_detail(h, width, "gep")
                    d["index"] = delta
                    d["count"] = cnt
                    self._fault(FAULT_BOUNDS, "index=%d count=%d" % (delta, cnt), d)
                path = path + ((0, delta, cnt, width),)
        cur, leaf = ty, width
        for idx in rest:
            if cur.startswith("["):
                n, elem = lomelf.array_parts(cur)
                w = lomelf.size_of(elem)
                if idx < 0 or idx >= n:
                    d = self.map_detail(h, w, "gep")
                    d["index"] = idx
                    d["count"] = n
                    self._fault(FAULT_BOUNDS, "index=%d count=%d" % (idx, n), d)
                path = path + ((0, idx, n, w),)
                cur, leaf = elem, w
            elif cur.startswith("{"):
                _off, fty = lomelf.field_offset(cur, idx)
                w = lomelf.size_of(fty)
                path = path + ((1, idx, 0, w),)
                cur, leaf = fty, w
            else:
                raise LvmError(f"GEP 的这一步 T0 没实现: {cur!r}")
        return self._new_token(b, path, leaf)

    def elem(self, h: int, index: int, count: int, width: int) -> int:
        """帧里的字节槽：`ELEM(index, count, width)`（`docs/377` §2.3）。"""
        b, path, _leaf = self.decode(h, 0)
        if index < 0 or index >= count:
            d = self.map_detail(h, width, "slot")
            d["index"] = index
            d["count"] = count
            self._fault(FAULT_BOUNDS, "index=%d count=%d" % (index, count), d)
        return self._new_token(b, path + ((0, index, count, width),), width)

    # ------------------------------------------------------------ 聚合（逐叶，没有 blob）
    def _get_agg(self, b, path: tuple, ty: str) -> bytes:
        size = lomelf.size_of(ty)
        out = bytearray(size)
        if ty.startswith("["):
            n, elem = lomelf.array_parts(ty)
            w = lomelf.size_of(elem)
            for i in range(n):
                out[i * w:(i + 1) * w] = self._get_agg(
                    b, path + ((0, i, n, w),), elem)
            return bytes(out)
        if ty.startswith("{"):
            k = _struct_arity(ty)
            for i in range(k):
                off, fty = lomelf.field_offset(ty, i)
                w = lomelf.size_of(fty)
                out[off:off + w] = self._get_agg(b, path + ((1, i, 0, w),), fty)
            return bytes(out)
        return bytes(b.cells.get(path, b"\0" * size))[:size].ljust(size, b"\0")

    def _put_agg(self, b, path: tuple, ty: str, data: bytes) -> None:
        if ty.startswith("["):
            n, elem = lomelf.array_parts(ty)
            w = lomelf.size_of(elem)
            for i in range(n):
                self._put_agg(b, path + ((0, i, n, w),), elem,
                              data[i * w:(i + 1) * w])
            return
        if ty.startswith("{"):
            k = _struct_arity(ty)
            for i in range(k):
                off, fty = lomelf.field_offset(ty, i)
                w = lomelf.size_of(fty)
                self._put_agg(b, path + ((1, i, 0, w),), fty,
                              data[off:off + w])
            return
        if any(data):
            b.cells[path] = bytes(data)
        else:
            b.cells.pop(path, None)

    # ------------------------------------------------------------ 字节串：沿 u8 数组轴
    def _run_base(self, h: int, n: int):
        b, path, leaf = self.decode(h, 0)
        if path and path[-1][0] == 0 and path[-1][3] == 1:
            _k, start, cnt, _w = path[-1]
            if start + n > cnt:
                d = self.map_detail(h, 1, "run")
                d["index"] = start + n - 1
                d["count"] = cnt
                self._fault(FAULT_BOUNDS, "run index=%d count=%d" % (start + n, cnt), d)
            return b, path[:-1], start, cnt
        if not path and b.size >= n:
            return b, (), 0, b.size
        self._fault(FAULT_NOADDR, "byte run over a non-u8-element reference",
                    {"dismem": True, "reason": "run", "token": h, "width": n})
        return None, (), 0, 0

    def get_run(self, h, n: int) -> bytes:
        b, base, start, cnt = self._run_base(h, n)
        return bytes(_cell_byte(b.cells.get(base + ((0, start + k, cnt, 1),)))
                     for k in range(n))

    def put_run(self, h, data) -> None:
        b, base, start, cnt = self._run_base(h, len(data))
        for k, byte in enumerate(data):
            key = base + ((0, start + k, cnt, 1),)
            if byte:
                b.cells[key] = bytes([byte])
            else:
                b.cells.pop(key, None)

    def cstr(self, h, limit: int = 4096) -> bytes:
        b, path, _leaf = self.decode(h, 0)
        if not (path and path[-1][0] == 0 and path[-1][3] == 1):
            if path:
                self._fault(FAULT_NOADDR, "cstr over a non-u8-element reference",
                            {"dismem": True, "reason": "cstr", "token": h, "width": 1})
            base, start, cnt = (), 0, b.size
        else:
            base, start, cnt = path[:-1], path[-1][1], path[-1][2]
        out = bytearray()
        i = start
        while i < cnt and len(out) < limit:
            c = _cell_byte(b.cells.get(base + ((0, i, cnt, 1),)))
            if c == 0:
                break
            out.append(c)
            i += 1
        return bytes(out)


def _cell_byte(cell) -> int:
    return cell[0] if cell else 0


def _struct_arity(ty: str) -> int:
    return len(lomelf._fields(ty))


def _d_num(v) -> bool:
    return isinstance(v, int) and not d_is_handle(v) and not d_is_code(v)


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

    def __init__(self, site, detail: dict | None = None) -> None:
        self.site = site
        self.code = 132
        self.detail = detail or {}


def trap_exit(site, detail: dict | None = None) -> int:
    """`llvm.trap` 的共同出口：**132**（与原生 `SIGILL` 同一个数）+ 一行站点。

    首行保持稳定；`docs/366` 的详细块跟在后面（同 Lomenterr 的形状）。
    """
    fn, blk, idx = site
    print(f"lvm: llvm.trap at {fn}:{blk}#{idx}", file=sys.stderr)
    if detail:
        ins = detail.get("ins") or ""
        out = ["error[trap]: 程序主动崩溃（llvm.trap → 退出码 132）",
               f"  --> IR:{fn}:{blk}#{idx}"]
        if ins:
            out += ["   |", f"   |   {ins}"]
        out.append("   |")
        depth = detail.get("depth")
        where = f"   = 站点：函数 {fn} / 块 {blk} / 第 {idx} 条指令"
        if depth:
            where += f"；栈深 {depth}"
        out.append(where)
        out.append("   = 建议：这是程序自己的崩溃器（LMS 第 5 级「拒不了就崩」）；"
                   "要看为什么走到这里，打开剖面（--profile）或在该站点前打印状态。"
                   "原生档走同一条路径时是 SIGILL（132）。")
        print("\n".join(out), file=sys.stderr)
    return 132


class Executor(Vm):
    # ------------------------------------------------------------ 系统调用
    def refuse(self, nr: int, kind: str = "") -> int:
        """guest 拿到 `-ENOSYS`；"这一条没实现"记进覆盖报告（`docs/311` §4.2）。

        `die` 留给"调用本身是坏的"（越界指针、非合成 fd）；"这个号没有"是 Linux
        也会给的答案，不是一次崩溃 —— 两者要分开。
        """
        if self.syscall_report is not None:
            name = GUEST_ABI_BY_NR.get(nr, ("", ""))[1]
            self.syscall_report.hit(nr, self.argv[0] if self.argv else "?",
                                    self.site or ("?", "?", 0), kind or name or "?")
        return ENOSYS

    def syscall(self, nr: int, a: list[int]) -> int:
        """**唯一**的对外通道（`docs/243` §3.4）。表里没有的回 `-ENOSYS`。

        `docs/311` §4.2 之前这里是 `raise LvmError`（= 工具的响亮）。那是把
        "工具该说话"与"guest 该像 Linux"写成了同一件事。现在分开：**guest 拿到
        `-ENOSYS`**，响亮度挪到 `--syscall-report` 那份可数的产物上。
        """
        if nr == 0:                                    # read(fd, buf, count)
            # 解释器没有真的文件描述符 —— 只认识 257 开的那个合成文件。
            v = self.vfds.get(a[0])
            if v is None:
                raise LvmError(f"read: fd {a[0]} 不是 VM 合成的那一个"
                               "（`docs/243` §3.4）")
            data = v[0][v[1]:v[1] + a[2]]
            v[1] += len(data)
            if self.dismemory:
                self.mem.put_run(a[1], data)
            else:
                self.mem.put(a[1], data)
            return len(data)
        if nr == 1:                                    # write(fd, buf, count)
            fd, buf, n = a[0], a[1], a[2]
            data = self.mem.get_run(buf, n) if self.dismemory else self.mem.get(buf, n)
            if fd in (1, 2):
                self.out.write(data)
                self.out.flush()
            return n
        if nr == 3:                                    # close(fd)
            if self.vfds.pop(a[0], None) is None:
                raise LvmError(f"close: fd {a[0]} 不是 VM 合成的那一个"
                               "（`docs/243` §3.4）")
            return 0
        if nr == 12 and self.dismemory:                 # brk —— 只认堆体的**根**
            want = a[0]
            heap_body, _hp, _hl = self.mem.decode(self.mem.heap, 0)
            if want == 0:
                return self.mem.heap
            if not d_is_handle(want):
                raise _Fault(FAULT_NOORDER, self.site, "brk on a number",
                             {"dismem": True, "reason": "brk-number", "width": 8})
            b, path, _leaf = self.mem.decode(want, 0)
            if b is not heap_body or path:
                raise _Fault(FAULT_NOORDER, self.site,
                             "brk on a non-root reference",
                             {"dismem": True, "reason": "brk-body", "width": 8})
            return want
        if nr == 12:                                   # brk(addr)
            # `_IR_HEAP` 的分配器用它向 OS 要页。解释器的堆本来就在地址空间里，
            # 所以这里只需把"当前可信的堆底"如实回一个数：给了就认，没给就报现值。
            want = a[0]
            if want == 0 or (self.mem.heap <= want < self.mem.sp):
                if want:
                    if self.sub is not None:
                        self.sub.grant_brk(self.mem.heap, want)
                    self.mem.heap = want
                return self.mem.heap
            return self.mem.heap
        if nr == 39:                                   # getpid
            # 如实转出去：这是**这台机器的事实**，被解释程序问的就是"我"是谁。
            # F 类：宿主给不了（例如 PE 上那条 shim 没有这个号）⇒ guest 仍然拿 -ENOSYS。
            if not self.host.can(nr):
                return self.refuse(nr, GUEST_FORWARD)
            return os.getpid()
        if nr == 63:                                   # uname(struct utsname *p)
            # ⚠ **合成，不转宿主** —— 它描述的是**目标平台**，不是解释器跑在哪台机器上。
            # 判据 A 比的是"这份产物在目标平台上会打印什么"，所以这里必须给 Linux：
            # 宿主是 Windows 也照样给 Linux（`docs/243` §3.4 那条规矩）。
            fields = [b"Linux", b"loment", b"0.1.5", b"#1 LVM T0", b"x86_64", b"(none)"]
            blob = b"".join(f[:64].ljust(65, b"\0") for f in fields)
            if self.dismemory:
                self.mem.put_run(a[0], blob)
            else:
                self.mem.put(a[0], blob)
            return 0
        if nr == 201:                                  # time(NULL)
            if not self.dismemory and not self.host.can(nr):
                return self.refuse(nr, GUEST_FORWARD)
            if self.dismemory:
                # 时间也在观察代数里（`docs/371`）：dismemory 下宿主墙钟会被 guest 用来
                # 量重排效应，所以按**已执行指令数**给一台确定性虚拟钟（1 条 = 1 ms）。
                return self.executed // 1000
            return int(time.time())
        if nr == 228:                                  # clock_gettime(id, struct timespec *)
            # `lgc` 的预算读它（`docs/309`）。**按 clockid 分流**：0 是墙钟（与 201 同源），
            # 其余是单调钟 —— 实测 ELF 侧两者差一个数量级，拿单调钟回答 realtime 会让
            # "同一份源在 PE 与 ELF 上"的形状不同（`docs/311` §2 的 F 类）。
            # 按 Linux x86-64 的 `timespec`（两个 i64：秒、纳秒）写进 guest 内存。
            if not self.dismemory and not self.host.can(nr):
                return self.refuse(nr, GUEST_FORWARD)
            if self.dismemory:
                ns = self.executed * 1_000_000
            else:
                ns = time.time_ns() if a[0] == 0 else time.monotonic_ns()
            blob = ((ns // 1_000_000_000).to_bytes(8, "little", signed=True)
                    + (ns % 1_000_000_000).to_bytes(8, "little", signed=True))
            if self.dismemory:
                self.mem.put_run(a[1], blob)
            else:
                self.mem.put(a[1], blob)
            return 0
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
        # 表外的号：Linux 给 `-ENOSYS`，不是"进程死掉"。`docs/311` §4.2。
        return self.refuse(nr)

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
        m2 = re.match(r"([@%][-A-Za-z0-9_.]+)\((.*)\)\s*$", body, re.S)
        if not m2:
            raise LvmError(f"解析不了调用: {text!r}")
        callee = m2.group(1)
        if callee.startswith("%"):
            # 间接调用（C2 虚表）：值是 `ptrtoint @fn` 得到的代码地址。
            _, addr = self.operand(fr, "ptr " + callee)
            name = self.addr_fn.get(addr)
            if name is None:
                raise LvmError(f"间接调用的代码地址不在函数表里: {addr}")
        else:
            name = callee[1:]
        args = [s for s in lomelf.split_top(m2.group(2)) if s]
        if name == "llvm.trap":
            raise _Trap(self.site, self._diag(fr, text, "", 0))
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
        # **dismemory 的分配器是模型原生的**（`docs/368` §1.5）：地址式的空闲链在
        # 这一档里根本不存在（指针不是数），所以 `__loment_alloc` / `__loment_free`
        # 在**调用边界**换成"造一颗体 / 退休一颗体"，不进去跑那段运行期代码。
        if self.dismemory and self.sub is None and name in ("__loment_alloc",
                                                            "__loment_free"):
            if name == "__loment_alloc":
                _t, sz = self.operand(fr, args[0])
                return self.mem.alloc(max(1, int(sz)), kind="heap")
            _t, pp = self.operand(fr, args[0])
            self.mem.retire(int(pp))
            return 0
        # `sw_cap` 的两个对象生命周期事件。**在返回之后**登记 / 归还：分配器自己
        # 要写块头、写已归还的块（那是它的活），进去之前它还不知道这块归谁。
        hook_alloc = self.sub is not None and name == "__loment_alloc"
        hook_free = self.sub is not None and name == "__loment_free"
        alloc_size = self.operand(fr, args[0])[1] if hook_alloc else 0
        free_ptr = self.operand(fr, args[0])[1] if hook_free else 0
        ret = self.run(fn, args, fr)
        if hook_alloc:
            self.sub.new_obj(int(ret), max(0, int(alloc_size)), "heap")
        elif hook_free:
            self.sub.retire_addr(int(free_ptr))
        return ret

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
        if self.dismemory:
            # 帧 = 一颗体；`base` 是它的句柄（偏移 0）。返回时退休，旧句柄 → F02。
            base = self.mem.alloc(FRAME_SIZE, kind="frame")
        else:
            base = STACK_TOP - self.depth * FRAME_SIZE
            if base <= self.mem.heap:
                self.depth -= 1
                raise LvmError("栈溢出")
        # 运行期内部函数（分配器 / 收集器）是**基座自己的代码**：它们写块头、
        # 扫已归还的块。能力检查在它们里面没有意义（`docs/362` §2）。
        internal = self.sub is not None and fn.name.startswith("__loment_")
        if internal:
            self.priv += 1
        try:
            return self._run_body(fn, argtexts, caller, base, entry_vals)
        finally:
            if internal:
                self.priv -= 1
            if self.dismemory:
                self.mem.retire(base)
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
            if self.sub is not None and not self.priv:
                # 能力随实参进形参（`docs/362` §2.2）：不然一个指针一过函数边界就
                # 只剩地址，堆对象的界与代都问不出来。
                cap = self._cap_of(caller, argtexts[k])
                if cap is not None:
                    fr.caps[pname] = cap
        try:
            return self._run_loop(fn, fr)
        finally:
            # 栈帧返回 = 这一帧的 `alloca` 对象全部归还（`docs/362` §2）。
            if self.sub is not None:
                for oid in fr.objs:
                    self.sub.retire_oid(oid)
            if self.dismemory:
                for h in fr.objs:
                    self.mem.retire(h)

    def _run_loop(self, fn, fr: Frame):
        label = fn.blocks[0].label
        prev = None
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
            _pending_phi: list[tuple[str, object, tuple[int, int] | None]] = []
            for idx, ins in enumerate(blk.instrs):
                if self.profile is not None or self.sub is not None or self.dismemory:
                    # 剖面 / `sw_cap` / dismemory 都需要**每条指令**的站点；都关着时，
                    # 只有 `call` 记（`llvm.trap` 用），不给每条指令付一次元组的代价。
                    self.site = (fn.name, blk.label, idx)
                    if self.dismemory:
                        self.mem.hint = {}
                elif ins.text.startswith("call "):
                    self.site = (fn.name, blk.label, idx)
                steps += 1
                self.executed += 1
                if (self.dismemory and self.relocate_every
                        and steps % self.relocate_every == 0):
                    self.mem.relocate()
                if self.counters is not None:
                    # T0 的"解码"就是这一条指令本身：每执行一次就把文本重解一次。
                    self.counters.instructions += 1
                    self.counters.dispatches += 1
                    self.counters.decodes += 1
                if steps > STEP_CAP:
                    raise LvmError(
                        f"步数上限 {STEP_CAP}：{fn.name} 的块 {blk.label}，"
                        f"第 {idx} 条 —— {ins.text[:60]}")
                if ins.text.startswith("phi "):
                    if ins.dest:
                        val, cap = self._phi(fr, ins.text, prev)
                        _pending_phi.append((ins.dest, val, cap))
                    continue
                if _pending_phi:
                    for _d, _v, _c in _pending_phi:
                        fr.vals[_d] = _v
                        if _c is None:
                            fr.caps.pop(_d, None)
                        else:
                            fr.caps[_d] = _c
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
                    return self._ret(fr, ins.text)
                if ins.text.startswith("unreachable"):
                    raise LvmError("执行到 unreachable")
                # ⚠ **没有目的值的指令也要执行**（`store` 就是：它 `dest is None`）。
                # 第一版写成 `if ins.dest:` 才求值 —— 于是所有 `store` 被**静默跳过**，
                # 症状是实参全是 0、程序照样"跑完"（连错都不报，最坏的一种）。
                v = self._eval(fr, ins.text)
                if ins.dest:
                    fr.vals[ins.dest] = v
                    if self.sub is not None:
                        self._track_cap(fr, ins, v)
            else:
                raise LvmError(f"{fn.name}: 块 {label} 没有终结指令")

    # ------------------------------------------------------------ 单条指令
    def _diag(self, fr: Frame, text: str, op: str, width: int) -> dict:
        """故障渲染的现场（`docs/366`）：指令文本、指针操作数、宽度、栈深。"""
        depth = self.depth if self.dismemory else (STACK_TOP - fr.base) // FRAME_SIZE
        return {"ins": text, "op": op, "width": width, "depth": depth}

    def _d_arith(self, op: str, a, b, ty: str):
        """句柄参与的算术（`docs/368` §1.4）。**只有 `add`/`sub` 的体内偏移是允许的。**
        体内偏移有顺序（数组下标），所以同体相减/比较照常；跨体一律 F05。"""
        ha, hb = d_is_handle(a), d_is_handle(b)
        if ha or hb:
            # 深度 4：引用没有可加减的整数（F04）；两个引用的差是"序"（F05）。
            if op == "sub" and ha and hb:
                raise _Fault(FAULT_NOORDER, self.site, op,
                             {"dismem": True, "reason": "order", "op": op,
                              "width": 8})
            raise _Fault(FAULT_NOADDR, self.site, op,
                         {"dismem": True, "reason": "arith", "op": op,
                          "width": 8})
        return None

    def _eval(self, fr: Frame, text: str):
        if text.startswith("alloca"):
            ty, _ = lomelf.parse_type(text[len("alloca "):])
            if self.dismemory:
                # `alloca` 是一颗自己的体（`docs/368` §1.5）：返回句柄；帧返回时退休。
                h = self.mem.alloc(max(1, lomelf.size_of(ty)), kind="alloca")
                fr.objs.append(h)
                return h
            a = fr.slot()
            fr.next = a + max(SLOT, lomelf.size_of(ty))
            fr.slots[text] = a
            return a
        if text.startswith("load "):
            body = text[len("load "):]
            ty, i = lomelf.parse_type(body)
            ptr_s = body[i:].lstrip(", ").strip()
            _bty, addr = self.operand(fr, ptr_s)
            if self.dismemory:
                self.mem.hint = self._diag(fr, text, ptr_s, lomelf.size_of(ty))
            if self.sub is not None and not self.priv:
                self.sub.check(addr, lomelf.size_of(ty), self._cap_of(fr, ptr_s),
                               self.site, self._diag(fr, text, ptr_s, lomelf.size_of(ty)))
            return self.load(ty, addr)
        if text.startswith("store "):
            body = text[len("store "):]
            ty, i = lomelf.parse_type(body)
            # `store <ty> <val>, ptr <addr>` —— 值那一格**没带类型**，得把类型的给它。
            val_s, ptr_s = lomelf.split_top(body[i:].lstrip(", "))
            _vty, val = self.operand(fr, ty + " " + val_s)
            _pty, addr = self.operand(fr, ptr_s)
            if self.dismemory:
                self.mem.hint = self._diag(fr, text, ptr_s, lomelf.size_of(ty))
            if self.sub is not None and not self.priv:
                self.sub.check(addr, lomelf.size_of(ty), self._cap_of(fr, ptr_s),
                               self.site, self._diag(fr, text, ptr_s, lomelf.size_of(ty)))
                if ty == "ptr":
                    # 指针进内存，能力跟着进影子表（`docs/362` §2.3）。
                    cap = self._cap_of(fr, val_s)
                    if cap is None:
                        self.sub.shadow.pop(addr, None)
                    else:
                        self.sub.shadow[addr] = cap
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
            if self.dismemory:
                bad = self._d_arith(op, a, b, ty)
                if bad is not None:
                    return bad
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
            if self.dismemory:
                got = self._d_arith(op, a, b, ty)
                if got is not None:
                    return got
            return arith(op, a, b, ty)
        raise LvmError(f"这个指令 T0 没实现: {text!r}")

    # ------------------------------------------------------------ sw_cap 的能力传播
    def _cap_of(self, fr: Frame, text: str) -> tuple[int, int] | None:
        """一个操作数文本 -> 它的能力。**没有就只能按地址找**（返回 `None`）。"""
        if self.sub is None or self.priv:
            return None
        try:
            _ty, v = lomelf.parse_operand(text)
        except lomelf.ElfError:
            v = text.strip()                  # `store` 的值那一格不带类型
        if v.startswith("%"):
            return fr.caps.get(v)
        if v.startswith("@"):
            return self.global_caps.get(v[1:])
        return None

    def _gep_base(self, text: str) -> str:
        body = text[len("getelementptr"):].strip()
        if body.startswith("inbounds"):
            body = body[len("inbounds"):].strip()
        _ty, i = lomelf.parse_type(body)
        return lomelf.split_top(body[i:].lstrip(", "))[0]

    def _track_cap(self, fr: Frame, ins, value) -> None:
        """一条指令写完结果之后，把能力记到 SSA 名上（`docs/362` §2.2 的传播表）。"""
        if self.sub is None or self.priv:
            return
        text = ins.text
        if text.startswith("alloca"):
            ty, _ = lomelf.parse_type(text[len("alloca "):])
            oid = self.sub.new_obj(int(value), lomelf.size_of(ty), "alloca")
            fr.objs.append(oid)
            fr.caps[ins.dest] = self.sub.cap_of(oid)
            return
        if text.startswith("getelementptr"):
            cap = self._cap_of(fr, self._gep_base(text))
            if cap is not None:
                fr.caps[ins.dest] = cap
            return
        if text.startswith("load "):
            ty, i = lomelf.parse_type(text[len("load "):])
            if ty == "ptr":
                # ⚠ 影子的**键是槽地址**（指针从哪儿读出来），不是被读出的值。
                # 2026-11-21 才发现：原先拿 `int(value)` 当键，于是能力过不了栈槽 ——
                # 一个 `let p: ptr` 用一次就丢；UAF 只有"洗成整数"那条路还会被抓。
                slot_s = text[len("load ") + i:].lstrip(", ").strip()
                _sty, slot = self.operand(fr, slot_s)
                cap = self.sub.shadow.get(int(slot))
                if cap is None:
                    cap = self.sub.cap_for_addr(int(value))
                if cap is not None:
                    fr.caps[ins.dest] = cap
            return
        if text.startswith("call "):
            body = text[len("call "):]
            retty, _ = lomelf.parse_type(body)
            if retty == "ptr":
                cap = self.sub.cap_for_addr(int(value))
                if cap is not None:
                    fr.caps[ins.dest] = cap
            return
        if text.startswith("inttoptr "):
            cap = self.sub.cap_for_addr(int(value))
            if cap is not None:
                fr.caps[ins.dest] = cap

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
        if self.dismemory and d_is_handle(base):
            # 深度 4：下标变**结构步骤**，不是字节偏移（`docs/377` §2.2）。
            self.mem.site = self.site
            return self.mem.gep(base, int(idxs[0]), ty, [int(i) for i in idxs[1:]])
        off = gep_offset(ty, idxs, text)
        return base + off

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
        if self.dismemory and (d_is_handle(a) or d_is_handle(b)):
            if pred in ("eq", "ne"):
                same = (a == b) if (d_is_handle(a) and d_is_handle(b)) else False
                return 1 if (same == (pred == "eq")) else 0
            raise _Fault(FAULT_NOORDER, self.site, "compare references",
                         {"dismem": True, "reason": "order", "width": 8})
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
        if self.sub is not None and not self.priv:
            self.sub.check(addr, lomelf.size_of(ty),
                           self._cap_of(fr, "ptr " + m.group(2)), self.site,
                           self._diag(fr, text, "ptr " + m.group(2),
                                      lomelf.size_of(ty)))
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
        if self.dismemory:
            raise _Fault(FAULT_NOADDR, self.site, op,
                         {"dismem": True, "reason": "cast", "op": op,
                          "handle": a, "width": 8})
        return a                                       # ptrtoint / inttoptr：地址就是整数

    # ------------------------------------------------------------ 控制流
    def _phi(self, fr: Frame, text: str, prev: str | None):
        """返回 `(值, 能力)`。能力只有 `ty == "ptr"` 时才可能有（`docs/362` §2.2）。"""
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
                if self.sub is None or self.priv or ty != "ptr":
                    return v, None
                cap = self._cap_of(fr, val_s)
                if cap is None:
                    cap = self.sub.cap_for_addr(int(v))
                return v, cap
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


# ---------------------------------------------------------------- 显式栈引擎（docs/363 P5）
#
# `docs/360` §3.1：**guest 状态就是全部状态。** 递归 T0 的 guest 状态有一半分住在
# Python 的调用栈里 —— 那不是 guest 的，取不到也存不下来。这一档把调用栈搬到**显式**的
# `stack` 列表里：PC 是 `(帧, 块, 指令序号)`，值是帧里的字典，对象在基质表里。
# 于是 checkpoint = 序列化这台机器，resume = 反序列化后接着跑。
#
# 默认仍是递归引擎（零风险）；只有 `--checkpoint-at` / `--resume` 走这一档。
# 判据 `tools/lvm_cp_test.py` 拿整份语料钉「两个引擎同答」。

#: 共同快照格式（`docs/365`）：与自举镜像**同一套布局**。
#: 头 16 B + ctx 256 B + 全局表 + 对象表 + 续延栈 2112 B + 4 MiB 内存 + 参考侧扩展。
CP_MAGIC = b"LVMC"
CP_VERSION = 2
CP_HEADER = 16
CTX_SIZE = 256
CONT_REC = 16          # u32 / 帧
MAX_CONT = 33          # MAX_DEPTH + 1
FRAME_VALS = 8         # 帧内值表起点（自举的 FR_VALS）
VAL_REC = 16           # 值表一条：name_off/name_len/vlo/vhi
EXT_MAGIC = b"LVEX"
EXT_VERSION = 1

#: 自举镜像 ctx 的格号（跨实现快照要写它的布局）。
CTX_NSRC, CTX_NG, CTX_NF, CTX_NP, CTX_NB, CTX_NI = 0, 4, 8, 12, 16, 20
CTX_HEAP, CTX_DEPTH, CTX_STEPS = 24, 28, 96
CTX_PROF_FD, CTX_NEDGE = 104, 108
CTX_ARG0, CTX_ARG0N, CTX_VFD_POS, CTX_VFD_OPEN = 112, 116, 120, 124
CTX_SUB, CTX_PRIV = 172, 188
CTX_NOBJ, CTX_NFOBJ, CTX_NRET = 192, 196, 200
CTX_MODE, CTX_CKPT_AT, CTX_IFHASH = 220, 248, 252

#: 对象 kind 的跨实现编码（与自举的 `OBJ_*` 同数；argv 是参考侧多出来的一档）。
KIND_CODE = {"global": 1, "brk": 2, "alloca": 3, "heap": 4, "argv": 5}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}


def fnv32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def _u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)


def cp_frame_count(blob: bytes) -> int:
    """快照里的调用深度（测试用；格式是共同格式的 ctx）。"""
    return struct.unpack_from("<I", blob, CP_HEADER + CTX_DEPTH)[0]


def cp_substrate(blob: bytes) -> int:
    return struct.unpack_from("<I", blob, 12)[0]


def ir_layout(text: str, funcs: list) -> tuple[dict, dict]:
    """IR 文本 -> `(SSA 名 -> 跨度, SSA 名 -> 类型)`。

    跨度必须与自举镜像解析出来的**逐字节一致**：它拿 `name_off/name_len` 当值表的键。
    """
    data = text.encode("utf-8")
    spans: dict[tuple[str, str], tuple[int, int]] = {}
    types: dict[tuple[str, str], str] = {}
    define_re = re.compile(rb"^define\b[^\n]*?@([-A-Za-z0-9_.]+)\s*\(([^)]*)\)", re.M)
    name_re = re.compile(rb"%[-A-Za-z0-9_.]+")
    dest_re = re.compile(rb"^[ \t]*(%[-A-Za-z0-9_.]+)[ \t]*=")
    for m in define_re.finditer(data):
        fn = m.group(1).decode()
        params = m.group(2)
        pbase = m.start(2)
        for nm in name_re.finditer(params):
            spans[(fn, nm.group(0).decode())] = (pbase + nm.start(), len(nm.group(0)))
        # 函数体：从 `{` 之后逐行扫 dest
        body_start = data.find(b"{", m.end())
        if body_start < 0:
            continue
        pos = body_start + 1
        while pos < len(data):
            nl = data.find(b"\n", pos)
            if nl < 0:
                nl = len(data)
            line = data[pos:nl]
            if line.strip() == b"}":
                break
            dm = dest_re.match(line)
            if dm:
                spans[(fn, dm.group(1).decode())] = (pos + dm.start(1), len(dm.group(1)))
            pos = nl + 1
    for fn in funcs:
        for ty, name in fn.params:
            types[(fn.name, name)] = ty
        for blk in fn.blocks:
            for ins in blk.instrs:
                if not ins.dest:
                    continue
                ty = lomelf.result_type(ins.text)
                if ty is not None:
                    types[(fn.name, ins.dest)] = ty
    return spans, types


class _Suspend(Exception):
    """跑到 checkpoint 点：把快照带出来，交给调用方落盘。"""

    def __init__(self, blob: bytes) -> None:
        self.blob = blob


#: 自举镜像的 `NONE`（块/函数下标的空值）。
NONE = 0xFFFFFFFF


class _Reader:
    """共同快照的游标（截断一律点名拒）。"""

    __slots__ = ("b", "o")

    def __init__(self, b: bytes) -> None:
        self.b = b
        self.o = 0

    def take(self, n: int) -> bytes:
        if self.o + n > len(self.b):
            raise LvmError("checkpoint：文件截断了")
        d = self.b[self.o:self.o + n]
        self.o += n
        return d

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def done(self) -> bool:
        return self.o >= len(self.b)


class _IterFrame(Frame):
    """显式栈里的一帧：递归引擎里那组 Python 局部变量搬到这里。"""

    def __init__(self, mem: Mem, base: int, fn) -> None:
        super().__init__(mem, base)
        self.fn = fn
        self.label = fn.blocks[0].label
        self.idx = 0
        self.prev: str | None = None
        self.steps = 0
        self.pending: list[tuple[str, object, tuple[int, int] | None]] = []
        self.wait_dest: str | None = None
        self.wait_retty: str = "void"
        self.wait_hook: tuple[str, int] | None = None
        self.internal = fn.name.startswith("__loment_")


class ExplicitExecutor(Executor):
    """T0 的显式栈引擎：把 Python 的调用栈换成 guest 自己的栈。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stack: list[_IterFrame] = []
        self.executed = 0
        self.cp_at: int | None = None
        #: 共同快照要的 IR 侧信息（由 `run_checkpointed` 装）。
        self.ir_text = ""
        self.ir_bytes = b""
        self.ir_fnv = 0
        self.func_list: list = []
        self.fn_index: dict[str, int] = {}
        self.block_index: dict[str, dict[str, int]] = {}
        self.global_blocks: list = []
        self.name_spans: dict = {}
        self.value_types: dict = {}
        self._block_cache: dict[str, dict] = {}

    # ------------------------------------------------------------ 入口
    def run(self, fn, argtexts: list[str], caller: Frame | None,
            entry_vals: list | None = None):
        if caller is not None:
            raise LvmError("显式栈引擎不接受递归调用（内部错）")
        self._push(fn, None, entry_vals, None, [])
        return self._loop()

    def run_resumed(self):
        return self._loop()

    def _blocks(self, fn) -> dict:
        got = self._block_cache.get(fn.name)
        if got is None:
            got = {b.label: b for b in fn.blocks}
            self._block_cache[fn.name] = got
        return got

    def _push(self, fn, argtexts, entry_vals, caller, argvals, argcaps=None):
        depth = len(self.stack) + 1
        if depth > MAX_DEPTH:
            raise LvmError(f"栈溢出（调用深度上限 {MAX_DEPTH}）")
        if self.dismemory:
            # 与递归引擎同一条（`docs/368` §1.5）：帧是一颗体，`base` 是它的令牌。
            base = self.mem.alloc(FRAME_SIZE, kind="frame")
        else:
            base = STACK_TOP - depth * FRAME_SIZE
            if base <= self.mem.heap:
                raise LvmError("栈溢出")
        fr = _IterFrame(self.mem, base, fn)
        for k, (pty, pname) in enumerate(fn.params):
            if argvals is not None:
                v = argvals[k][1]
            elif entry_vals is not None:
                v = entry_vals[k] if k < len(entry_vals) else 0
            else:
                v = 0
            fr.vals[pname] = v
            if self.sub is not None and not fr.internal:
                cap = None
                if argcaps is not None:
                    cap = argcaps[k]
                elif pty == "ptr" and entry_vals is not None:
                    cap = self.sub.cap_for_addr(int(v))
                if cap is not None:
                    fr.caps[pname] = cap
        if fr.internal:
            self.priv += 1
        self.stack.append(fr)
        return fr

    # ------------------------------------------------------------ 主循环
    def _loop(self):
        while self.stack:
            fr = self.stack[-1]
            if self.cp_at is not None and self.executed >= self.cp_at:
                raise _Suspend(self.snapshot())
            block = self._blocks(fr.fn).get(fr.label)
            if block is None:
                raise LvmError(f"{fr.fn.name}: 没有块 {fr.label}")
            if fr.idx >= len(block.instrs):
                raise LvmError(f"{fr.fn.name}: 块 {fr.label} 没有终结指令")
            ins = block.instrs[fr.idx]
            if fr.idx == 0 and self.profile is not None:
                self.profile.block(fr.fn.name, fr.label)
            if (self.profile is not None or self.sub is not None
                    or self.dismemory):
                self.site = (fr.fn.name, fr.label, fr.idx)
            elif ins.text.startswith("call "):
                self.site = (fr.fn.name, fr.label, fr.idx)
            self.executed += 1
            fr.steps += 1
            if self.counters is not None:
                self.counters.instructions += 1
                self.counters.dispatches += 1
                self.counters.decodes += 1
            if fr.steps > STEP_CAP:
                raise LvmError(
                    f"步数上限 {STEP_CAP}：{fr.fn.name} 的块 {fr.label}，"
                    f"第 {fr.idx} 条 —— {ins.text[:60]}")
            text = ins.text
            if text.startswith("phi "):
                if ins.dest:
                    val, cap = self._phi(fr, text, fr.prev)
                    fr.pending.append((ins.dest, val, cap))
                fr.idx += 1
                continue
            if fr.pending:
                for d, v, c in fr.pending:
                    fr.vals[d] = v
                    if c is None:
                        fr.caps.pop(d, None)
                    else:
                        fr.caps[d] = c
                fr.pending = []
            if text.startswith("br "):
                label, _pred, edge = self._br(fr, text, fr.label)
                if self.profile is not None:
                    self.profile.edge(fr.fn.name, fr.label, fr.idx, edge)
                fr.prev = fr.label
                fr.label = label
                fr.idx = 0
                continue
            if text.startswith("switch "):
                label, edge = self._switch(fr, text)
                if self.profile is not None:
                    self.profile.edge(fr.fn.name, fr.label, fr.idx, edge)
                fr.prev = fr.label
                fr.label = label
                fr.idx = 0
                continue
            if text.startswith("ret"):
                value = self._ret(fr, text)
                if self._return(fr, value):
                    return value
                continue
            if text.startswith("unreachable"):
                raise LvmError("执行到 unreachable")
            if text.startswith("call "):
                self._do_call(fr, ins)
                continue
            v = self._eval(fr, text)
            if ins.dest:
                fr.vals[ins.dest] = v
                if self.sub is not None:
                    self._track_cap(fr, ins, v)
            fr.idx += 1
        return 0

    # ------------------------------------------------------------ 调用
    def _do_call(self, fr: _IterFrame, ins) -> None:
        text = ins.text
        m = _CALL_ASM.match(text)
        if m:
            name = m.group(_ASM_NAME)
            if name != "syscall":
                raise LvmError(f"这一档不支持的 asm: {name!r}（`docs/245`）")
            argtext = m.group(_ASM_ARGS)
            a = [self.operand(fr, s)[1] for s in lomelf.split_top(argtext)]
            v = self.syscall(a[0], a[1:])
            if ins.dest:
                fr.vals[ins.dest] = v
            fr.idx += 1
            return
        body = text[len("call "):].strip()
        if body.startswith("void"):
            retty, body = None, body[4:].strip()
        else:
            retty, i = lomelf.parse_type(body)
            body = body[i:].strip()
        m2 = re.match(r"([@%][-A-Za-z0-9_.]+)\((.*)\)\s*$", body, re.S)
        if not m2:
            raise LvmError(f"解析不了调用: {text!r}")
        callee = m2.group(1)
        if callee.startswith("%"):
            _, addr = self.operand(fr, "ptr " + callee)
            name = self.addr_fn.get(addr)
            if name is None:
                raise LvmError(f"间接调用的代码地址不在函数表里: {addr}")
        else:
            name = callee[1:]
        args = [s for s in lomelf.split_top(m2.group(2)) if s]
        if name == "llvm.trap":
            raise _Trap(self.site, self._diag(fr, text, "", 0))
        if name in self.externs:
            raise LvmError(f"`{name}` 是 extern —— T0 不接 FFI（`docs/243` §5）")
        fn = self.funcs.get(name)
        if fn is None:
            raise LvmError(f"没有这个函数: {name}")
        if len(args) != len(fn.params):
            raise LvmError("实参个数与形参不符")
        if self.profile is not None and self.site is not None:
            self.profile.call_site(self.site, name)
        argvals = [self.operand(fr, s) for s in args]
        argcaps = ([self._cap_of(fr, s) for s in args]
                   if self.sub is not None else [None] * len(args))
        # 与递归引擎同一条（`docs/368` §1.5）：dismemory 的分配器是模型原生的 ——
        # `__loment_alloc` / `__loment_free` 在这里换成"造一颗体 / 退休一颗体"，
        # 不进去跑那段按地址写的运行期代码（那段会 ptrtoint → F04）。
        if self.dismemory and self.sub is None and name in ("__loment_alloc",
                                                            "__loment_free"):
            if name == "__loment_alloc":
                v = self.mem.alloc(max(1, int(argvals[0][1])), kind="heap")
            else:
                self.mem.retire(int(argvals[0][1]))
                v = 0
            if ins.dest:
                fr.vals[ins.dest] = v
            fr.idx += 1
            return
        fr.wait_dest = ins.dest
        fr.wait_retty = "void" if retty is None else retty
        fr.wait_hook = None
        if self.sub is not None and name == "__loment_alloc":
            fr.wait_hook = ("alloc", int(argvals[0][1]))
        elif self.sub is not None and name == "__loment_free":
            fr.wait_hook = ("free", int(argvals[0][1]))
        fr.idx += 1
        self._push(fn, None, None, fr, argvals, argcaps)

    def _return(self, fr: _IterFrame, value) -> bool:
        if self.sub is not None and not fr.internal:
            for oid in fr.objs:
                self.sub.retire_oid(oid)
        if self.dismemory:
            # 帧返回 = 这一帧的 alloca 与帧体一起退休（旧令牌 → F02）。
            for h in fr.objs:
                self.mem.retire(h)
            self.mem.retire(fr.base)
        if fr.internal:
            self.priv -= 1
        self.stack.pop()
        if not self.stack:
            return True
        caller = self.stack[-1]
        hook = caller.wait_hook
        if hook is not None:
            kind, arg = hook
            if kind == "alloc":
                self.sub.new_obj(int(value), max(0, int(arg)), "heap")
            else:
                self.sub.retire_addr(int(arg))
        if caller.wait_dest is not None:
            caller.vals[caller.wait_dest] = value
            if (self.sub is not None and not self.priv
                    and caller.wait_retty == "ptr"):
                cap = self.sub.cap_for_addr(int(value))
                if cap is not None:
                    caller.caps[caller.wait_dest] = cap
        caller.wait_dest = None
        caller.wait_retty = "void"
        caller.wait_hook = None
        return False

    # ------------------------------------------------------------ 共同快照（docs/365）
    def _align8(self, v: int) -> int:
        return (v + 7) // 8 * 8

    def _place_value(self, fr: _IterFrame, val) -> tuple[int, int]:
        """宿主值 -> `(lo, hi)`。聚合先落到本帧槽区，记成 `(addr, size)`。"""
        if isinstance(val, bytes):
            fr.next = self._align8(fr.next)
            addr = fr.next
            self.mem.put(addr, bytes(val))
            fr.next = addr + len(val)
            return addr, len(val)
        v = int(val) & 0xFFFFFFFFFFFFFFFF
        return v & 0xFFFFFFFF, v >> 32

    def _span_name(self, off: int, ln: int) -> str | None:
        if not ln:
            return None
        return self.ir_bytes[off:off + ln].decode("utf-8", "replace")

    def _wait_span(self, fr: _IterFrame) -> tuple[int, int]:
        if not fr.wait_dest:
            return 0, 0
        sp = self.name_spans.get((fr.fn.name, fr.wait_dest))
        if sp is None:
            raise LvmError(f"checkpoint：找不到 {fr.wait_dest} 的 IR 跨度")
        return sp

    def _prepare_tables(self) -> list:
        """把每帧的值表与聚合暂存写进 guest 内存；返回待写回的 phi 条目。

        dismemory 下"guest 内存"是**帧体里的格**（`docs/375` §2.5）：值表与聚合都
        写进那一颗体，偏移口径与平坦帧相同（`FRAME_VALS`）。
        """
        pending = []
        for d, fr in enumerate(self.stack, 1):
            n = 0
            for name, val in sorted(fr.vals.items()):
                sp = self.name_spans.get((fr.fn.name, name))
                if sp is None:
                    raise LvmError(f"checkpoint：找不到 SSA 名 {name} 的 IR 跨度（{fr.fn.name}）")
                if n >= 1024:
                    raise LvmError("checkpoint：一帧里的值超过自举镜像的 1024 格")
                if self.dismemory:
                    lo, hi = self._place_value_d(fr, val)
                    self._frame_put(fr, FRAME_VALS + n * VAL_REC,
                                    struct.pack("<IIII", sp[0], sp[1], lo, hi))
                else:
                    lo, hi = self._place_value(fr, val)
                    struct.pack_into("<IIII", self.mem.buf,
                                     fr.base + FRAME_VALS + n * VAL_REC,
                                     sp[0], sp[1], lo, hi)
                n += 1
            for name, val, cap in fr.pending:
                lo, hi = (self._place_value_d(fr, val) if self.dismemory
                          else self._place_value(fr, val))
                pending.append((d, name, lo, hi, cap))
            if self.dismemory:
                self._frame_put(fr, 0, struct.pack("<II", n, fr.next))
            else:
                struct.pack_into("<II", self.mem.buf, fr.base, n, fr.next - fr.base)
        return pending

    def _place_value_d(self, fr, val) -> tuple[int, int]:
        """dismemory 的宿主值 -> `(lo, hi)`；聚合按**帧体里的字节 run** 记 `(off, size)`。"""
        if isinstance(val, bytes):
            fr.next = self._align8(fr.next)
            off = fr.next
            self._frame_put(fr, off, bytes(val))
            fr.next = off + len(val)
            return off, len(val)
        v = int(val) & 0xFFFFFFFFFFFFFFFF
        return v & 0xFFFFFFFF, v >> 32

    def _frame_elem(self, fr, off: int) -> int:
        return self.mem.elem(fr.base, off, FRAME_SIZE, 1)

    def _frame_put(self, fr, off: int, data: bytes) -> None:
        self.mem.put_run(self._frame_elem(fr, off), data)

    def _frame_get(self, fr, off: int, n: int) -> bytes:
        return self.mem.get_run(self._frame_elem(fr, off), n)

    def _sub_code(self) -> int:
        if self.sub is None:
            return 0
        return 2 if self.sub.mode == "tag" else 1

    def _extension(self, pending: list) -> bytes:
        out = bytearray(EXT_MAGIC + _u32(EXT_VERSION))
        out += _u32(len(pending))
        for d, name, lo, hi, cap in pending:
            nb = name.encode("utf-8")
            out += _u32(d) + _u32(len(nb)) + nb + _u64(lo) + _u64(hi)
            if cap is None:
                out += _u32(0)
            else:
                out += _u32(1) + _u32(cap[0]) + _u32(cap[1]) + _u32(cap[2])
        out += _u32(len(self.stack))
        for fr in self.stack:
            for txt in (fr.fn.name, fr.label, fr.prev or ""):
                nb = txt.encode("utf-8")
                out += _u32(len(nb)) + nb
        caps = []
        for d, fr in enumerate(self.stack, 1):
            for name, c in sorted(fr.caps.items()):
                caps.append((d, name, c))
        out += _u32(len(caps))
        for d, name, c in caps:
            nb = name.encode("utf-8")
            out += _u32(d) + _u32(len(nb)) + nb + _u32(c[0]) + _u32(c[1]) + _u32(c[2])
        sh = sorted(self.sub.shadow.items()) if self.sub is not None else []
        out += _u32(len(sh))
        for a, c in sh:
            out += _u32(a) + _u32(c[0]) + _u32(c[1]) + _u32(c[2])
        out += _u32(len(self.vfds))
        for fd, (fdta, pos) in sorted(self.vfds.items()):
            out += _u32(fd) + _u32(pos) + _u32(len(fdta)) + bytes(fdta)
        out += _u32(len(self.argv))
        for s in self.argv:
            nb = s.encode("utf-8")
            out += _u32(len(nb)) + nb
        return bytes(out)

    def snapshot(self) -> bytes:
        """共同快照：与自举镜像同一套布局（`docs/365`）。"""
        if not self.ir_text:
            raise LvmError("checkpoint：这台引擎没有绑定 IR 文本")
        pending = self._prepare_tables()
        ctx = bytearray(CTX_SIZE)
        cset = lambda off, v: struct.pack_into("<I", ctx, off, int(v) & 0xFFFFFFFF)  # noqa: E731
        cset(CTX_NSRC, len(self.ir_bytes))
        cset(CTX_NG, len(self.globals))
        cset(CTX_NF, len(self.func_list))
        cset(CTX_NP, sum(len(f.params) for f in self.func_list))
        cset(CTX_NB, sum(len(f.blocks) for f in self.func_list))
        cset(CTX_NI, sum(len(b.instrs) for f in self.func_list for b in f.blocks))
        cset(CTX_HEAP, self.mem.heap)
        cset(CTX_DEPTH, len(self.stack))
        cset(CTX_STEPS, self.executed)
        cset(CTX_PROF_FD, 0)
        cset(CTX_NEDGE, 0)
        cset(CTX_ARG0, 0)
        cset(CTX_ARG0N, 0)
        if self.vfds:
            _fd, (fdta, pos) = next(iter(sorted(self.vfds.items())))
            cset(CTX_VFD_POS, pos)
            cset(CTX_VFD_OPEN, 1)
        cset(CTX_SUB, 1 if self.sub is not None else 0)
        cset(CTX_PRIV, self.priv)
        live = [o for o in (self.sub.objs.values() if self.sub else []) if o.alive]
        cset(CTX_NOBJ, len(live))
        cset(CTX_NFOBJ, sum(1 for o in live if o.kind == "alloca"))
        cset(CTX_NRET, len(self.sub.retired) if self.sub else 0)
        cset(CTX_MODE, 1)
        cset(CTX_CKPT_AT, 0)
        cset(CTX_IFHASH, self.ir_fnv)

        flags = 3 if self.dismemory else self._sub_code()
        out = bytearray(CP_MAGIC + _u32(CP_VERSION) + _u32(self.ir_fnv)
                        + _u32(flags) + ctx)
        if self.dismemory:
            out += self._snapshot_dis()
            out += self._extension(pending)
            return bytes(out)
        out += _u32(len(self.globals))
        for name, g in self.globals.items():
            nb = name.encode("utf-8")
            out += _u32(len(nb)) + nb + _u32(g.addr)
        objs = []
        if self.sub is not None:
            objs = [o for o in self.sub.objs.values() if o.alive]
            seen = set()
            for o in self.sub.retired:
                if o.oid not in seen:
                    seen.add(o.oid)
                    objs.append(o)
        out += _u32(len(objs))
        for o in objs:
            out += (_u32(o.oid) + _u32(o.base) + _u32(o.limit) + _u32(o.gen)
                    + _u32(1 if o.alive else 0) + _u32(KIND_CODE.get(o.kind, 4))
                    + _u32(o.tag))
        out += _u32(self.sub.next_oid if self.sub else 1)
        out += _u32(self.sub.tag_seq if self.sub else 0)
        cont = bytearray(MAX_CONT * CONT_REC * 4)
        for d, fr in enumerate(self.stack, 1):
            base = STACK_TOP - d * FRAME_SIZE
            fi = self.fn_index[fr.fn.name]
            bi = self.block_index[fr.fn.name][fr.label]
            prev = (NONE if fr.prev is None
                    else self.block_index[fr.fn.name][fr.prev])
            cfr = (STACK_TOP - (d - 1) * FRAME_SIZE) if d > 1 else NONE
            ro, rl = self._wait_span(self.stack[d - 2]) if d > 1 else (0, 0)
            do_, dl = self._wait_span(fr)
            hook = 0
            a0 = 0
            if fr.wait_hook is not None:
                hook = 1 if fr.wait_hook[0] == "alloc" else 2
                a0 = int(fr.wait_hook[1]) & 0xFFFFFFFFFFFFFFFF
            rec = [fi, base, cfr, ro, rl, bi, fr.idx, do_, dl,
                   1 if fr.internal else 0, hook,
                   a0 & 0xFFFFFFFF, a0 >> 32, prev, fr.steps, 0]
            for k, v in enumerate(rec):
                struct.pack_into("<I", cont, (d * CONT_REC + k) * 4, v)
        out += cont
        out += bytes(self.mem.buf)
        out += self._extension(pending)
        return bytes(out)

    def _restore_dis(self, frames, bodies, cells, toks, next_bid, next_serial) -> None:
        """把 flags=3 的段装回 `Dismem`（`docs/375` §2.5）。"""
        m = self.mem
        m.bodies.clear()
        m.live.clear()
        m.tokens.clear()
        m.rev.clear()
        m.tomb.clear()
        m.next_bid = 1
        m.next_serial = 1
        for bid, kind, size, shift in bodies:
            b = _DismBody(bid, kind, size, shift)
            b.alive = True
            m.bodies[bid] = b
            m._place(b)
            m.live[bid] = b
        for bid, path, blob in cells:
            b = m.bodies.get(bid)
            if b is not None:
                b.cells[path] = blob
        for tok, bid, path, leaf in toks:
            m.tokens[tok] = (bid, path, leaf)
            m.rev[(bid, path)] = tok
            b = m.bodies.get(bid)
            if b is not None:
                b.tokens.add(tok)
        m.next_bid = next_bid
        m.next_serial = next_serial
        for tok, (bid, path, _leaf) in m.tokens.items():
            if path == () and m.bodies.get(bid) is not None \
                    and m.bodies[bid].kind == "heapspan":
                m.heap = tok
                break

    def _snapshot_dis(self) -> bytes:
        """`flags = 3` 的段：令牌、帧体、格表、令牌表、体表、计数器（`docs/375` §2.5）。"""
        out = bytearray()
        out += _u32(len(self.globals))
        for name, g in self.globals.items():
            nb = name.encode("utf-8")
            out += _u32(len(nb)) + nb + _u64(g.addr)
        out += _u32(0)                       # 对象表：dismemory 与基质不叠加
        out += _u32(1) + _u32(0)             # next_oid / tag_seq（占位，同共同布局）
        cont = bytearray(MAX_CONT * CONT_REC * 4)
        for d, fr in enumerate(self.stack, 1):
            fi = self.fn_index[fr.fn.name]
            bi = self.block_index[fr.fn.name][fr.label]
            prev = (NONE if fr.prev is None
                    else self.block_index[fr.fn.name][fr.prev])
            ro, rl = self._wait_span(self.stack[d - 2]) if d > 1 else (0, 0)
            do_, dl = self._wait_span(fr)
            hook = 0
            a0 = 0
            if fr.wait_hook is not None:
                hook = 1 if fr.wait_hook[0] == "alloc" else 2
                a0 = int(fr.wait_hook[1]) & 0xFFFFFFFFFFFFFFFF
            rec = [fi, 0, 0, ro, rl, bi, fr.idx, do_, dl,
                   1 if fr.internal else 0, hook,
                   a0 & 0xFFFFFFFF, a0 >> 32, prev, fr.steps, 0]
            for k, v in enumerate(rec):
                struct.pack_into("<I", cont, (d * CONT_REC + k) * 4, v)
        out += cont
        out += _u32(len(self.stack))
        for fr in self.stack:
            out += _u64(fr.base) + _u32(fr.next) + _u32(len(fr.objs))
            for h in fr.objs:
                out += _u64(h)
        bodies = sorted((b for b in self.mem.bodies.values() if b.alive),
                        key=lambda q: q.id)
        cells = [(b.id, path, blob) for b in bodies
                 for path, blob in sorted(b.cells.items())]
        out += _u32(len(cells))
        for bid, path, blob in cells:
            out += _u32(bid) + _u32(len(path))
            for kind, idx, cnt, w in path:
                out += _u32(kind) + _u32(idx) + _u32(cnt) + _u32(w)
            out += _u32(len(blob)) + blob
        toks = sorted(self.mem.tokens.items())
        out += _u32(len(toks))
        for tok, (bid, path, leaf) in toks:
            out += _u64(tok) + _u32(bid) + _u32(leaf) + _u32(len(path))
            for kind, idx, cnt, w in path:
                out += _u32(kind) + _u32(idx) + _u32(cnt) + _u32(w)
        out += _u32(len(bodies))
        for b in bodies:
            nb = b.kind.encode("utf-8")
            out += (_u32(b.id) + _u32(b.size) + _u32(b.shift)
                    + _u32(len(nb)) + nb)
        out += _u32(self.mem.next_bid) + _u32(self.mem.next_serial)
        return bytes(out)

    def restore(self, blob: bytes) -> None:
        r = _Reader(blob)
        if r.take(4) != CP_MAGIC:
            raise LvmError("checkpoint：魔数不对")
        ver = r.u32()
        if ver != CP_VERSION:
            raise LvmError(f"checkpoint：不认识的版本 {ver}")
        if r.u32() != self.ir_fnv:
            raise LvmError("checkpoint：IR 与快照不匹配（字节码换了）")
        flags = r.u32()
        ctx = r.take(CTX_SIZE)
        self.mem.heap = struct.unpack_from("<I", ctx, CTX_HEAP)[0]
        depth = struct.unpack_from("<I", ctx, CTX_DEPTH)[0]
        self.executed = struct.unpack_from("<I", ctx, CTX_STEPS)[0]
        if depth > MAX_DEPTH:
            raise LvmError("checkpoint：深度超过上限")
        nglob = r.u32()
        gaddr = {}
        for _ in range(nglob):
            name = r.take(r.u32()).decode("utf-8", "replace")
            gaddr[name] = r.u64() if flags == 3 else r.u32()
        for name, g in self.globals.items():
            if name in gaddr:
                g.addr = gaddr[name]
        nobj = r.u32()
        objs = []
        for _ in range(nobj):
            oid = r.u32()
            base = r.u32()
            limit = r.u32()
            gen = r.u32()
            alive = r.u32()
            kind = CODE_KIND.get(r.u32(), "heap")
            tag = r.u32()
            objs.append((oid, base, limit, gen, alive, kind, tag))
        next_oid = r.u32()
        tag_seq = r.u32()
        cont = r.take(MAX_CONT * CONT_REC * 4)
        dis_state = None
        if flags == 3:
            nfr = r.u32()
            dis_frames = []
            for _ in range(nfr):
                base_tok = r.u64()
                fr_next = r.u32()
                na = r.u32()
                objs_h = [r.u64() for _ in range(na)]
                dis_frames.append((base_tok, fr_next, objs_h))
            nc = r.u32()
            dis_cells = []
            for _ in range(nc):
                bid = r.u32()
                plen = r.u32()
                path = tuple((r.u32(), r.u32(), r.u32(), r.u32())
                             for _ in range(plen))
                blen = r.u32()
                blob = r.take(blen)
                dis_cells.append((bid, path, blob))
            nt = r.u32()
            dis_toks = []
            for _ in range(nt):
                tok = r.u64()
                bid = r.u32()
                leaf = r.u32()
                plen = r.u32()
                path = tuple((r.u32(), r.u32(), r.u32(), r.u32())
                             for _ in range(plen))
                dis_toks.append((tok, bid, path, leaf))
            nb = r.u32()
            dis_bodies = []
            for _ in range(nb):
                bid = r.u32()
                size = r.u32()
                shift = r.u32()
                kind = r.take(r.u32()).decode("utf-8", "replace")
                dis_bodies.append((bid, kind, size, shift))
            dis_next_bid = r.u32()
            dis_next_serial = r.u32()
            dis_state = (dis_frames, dis_bodies, dis_cells, dis_toks,
                         dis_next_bid, dis_next_serial)
        else:
            mem = r.take(MEM_SIZE)
            if len(mem) != MEM_SIZE:
                raise LvmError("checkpoint：内存长度不对")
            self.mem.buf = bytearray(mem)
        if flags == 0 or flags == 3:
            self.sub = None
        else:
            s = SwCap("tag" if flags == 2 else "cap")
            s.next_oid = next_oid
            s.tag_seq = tag_seq
            for oid, base, limit, gen, alive, kind, tag in objs:
                o = _Obj(oid, base, limit - base, kind)
                o.gen = gen
                o.alive = bool(alive)
                o.tag = tag
                s.objs[oid] = o
                if o.alive:
                    s.live[o.base] = oid
            s.bases = sorted(s.live)
            s.retired = [s.objs[oid] for oid, *_ in objs if not alive]
            self.sub = s
            self.global_caps = {}
            for name, g in self.globals.items():
                cap = s.cap_for_addr(g.addr)
                if cap is not None:
                    self.global_caps[name] = cap
        pending, caps, shadow, vfds, argv = [], {}, [], {}, []
        frame_names: list = []
        if not r.done():
            if r.take(4) != EXT_MAGIC:
                raise LvmError("checkpoint：扩展魔数不对")
            if r.u32() != EXT_VERSION:
                raise LvmError("checkpoint：扩展版本不对")
            for _ in range(r.u32()):
                d = r.u32()
                name = r.take(r.u32()).decode("utf-8", "replace")
                lo = r.u64()
                hi = r.u64()
                cap = None
                if r.u32():
                    cap = (r.u32(), r.u32(), r.u32())
                pending.append((d, name, lo, hi, cap))
            frame_names = []
            for _ in range(r.u32()):
                fn_name = r.take(r.u32()).decode("utf-8", "replace")
                blk_name = r.take(r.u32()).decode("utf-8", "replace")
                prev_name = r.take(r.u32()).decode("utf-8", "replace")
                frame_names.append((fn_name, blk_name, prev_name))
            for _ in range(r.u32()):
                d = r.u32()
                name = r.take(r.u32()).decode("utf-8", "replace")
                caps.setdefault(d, {})[name] = (r.u32(), r.u32(), r.u32())
            for _ in range(r.u32()):
                shadow.append((r.u32(), (r.u32(), r.u32(), r.u32())))
            for _ in range(r.u32()):
                fd = r.u32()
                pos = r.u32()
                vfds[fd] = [r.take(r.u32()), pos]
            for _ in range(r.u32()):
                argv.append(r.take(r.u32()).decode("utf-8", "replace"))
        if self.sub is not None:
            self.sub.shadow = dict(shadow)
        self.vfds = vfds
        self.next_vfd = (max(vfds) + 1) if vfds else VFD_BASE
        if argv:
            self.argv = argv
        if dis_state is not None:
            self._restore_dis(*dis_state)
        self.stack = []
        for d in range(1, depth + 1):
            rec = struct.unpack_from("<16I", cont, d * CONT_REC * 4)
            (fi, _frb, _cfr, _ro, _rl, bi, j, do_, dl, _priv, hook,
             a0lo, a0hi, prev, steps, _sp) = rec
            if d <= len(frame_names):
                fn_name, blk_name, prev_name = frame_names[d - 1]
                fn = self.funcs.get(fn_name)
                if fn is None:
                    raise LvmError(f"checkpoint：没有函数 {fn_name!r}")
                blk = next((b for b in fn.blocks if b.label == blk_name), None)
                if blk is None:
                    raise LvmError(f"checkpoint：{fn_name} 里没有块 {blk_name!r}")
                prev_label = prev_name or None
            else:
                if bi >= len(self.global_blocks):
                    raise LvmError("checkpoint：块下标越界")
                fn, blk = self.global_blocks[bi]
                prev_label = (None if prev == NONE
                              else self.global_blocks[prev][1].label)
            if flags == 3:
                base_tok, fr_next, fr_objs = dis_frames[d - 1]
                fr = _IterFrame(self.mem, base_tok, fn)
            else:
                fr = _IterFrame(self.mem, STACK_TOP - d * FRAME_SIZE, fn)
            fr.label = blk.label
            fr.idx = j
            fr.prev = prev_label
            fr.steps = steps
            if flags == 3:
                headers = self.mem.get_run(self.mem.elem(fr.base, 0, FRAME_SIZE, 1), 8)
                n, fr.next = struct.unpack("<II", headers)
                if n > 1024:
                    raise LvmError("checkpoint：一帧里的值超过 1024 格")
                fr.vals = {}
                for k in range(n):
                    blob16 = self.mem.get_run(self.mem.elem(
                        fr.base, FRAME_VALS + k * VAL_REC, FRAME_SIZE, 1), VAL_REC)
                    no, nl, lo, hi = struct.unpack("<IIII", blob16)
                    name = self._span_name(no, nl)
                    ty = self.value_types.get((fn.name, name))
                    if ty is not None and lomelf.is_agg(ty):
                        fr.vals[name] = self.mem.get_run(
                            self.mem.elem(fr.base, lo, FRAME_SIZE, 1), hi)
                    else:
                        fr.vals[name] = lo | (hi << 32)
            else:
                fr.next = fr.base + struct.unpack_from("<I", self.mem.buf, fr.base + 4)[0]
                n = struct.unpack_from("<I", self.mem.buf, fr.base)[0]
                if n > 1024:
                    raise LvmError("checkpoint：一帧里的值超过 1024 格")
                fr.vals = {}
                for k in range(n):
                    no, nl, lo, hi = struct.unpack_from(
                        "<IIII", self.mem.buf, fr.base + FRAME_VALS + k * VAL_REC)
                    name = self._span_name(no, nl)
                    ty = self.value_types.get((fn.name, name))
                    if ty is not None and lomelf.is_agg(ty):
                        fr.vals[name] = bytes(self.mem.buf[lo:lo + hi])
                    else:
                        fr.vals[name] = lo | (hi << 32)
            fr.caps = dict(caps.get(d, {}))
            if flags == 3:
                fr.objs = list(fr_objs)
            else:
                fr.objs = [o.oid for o in (self.sub.objs.values() if self.sub else [])
                           if o.kind == "alloca" and fr.base <= o.base < fr.base + FRAME_SIZE]
            fr.wait_dest = self._span_name(do_, dl) if dl else None
            fr.wait_retty = "void"
            if j > 0 and j <= len(blk.instrs):
                ins = blk.instrs[j - 1]
                if ins.text.startswith("call "):
                    ty = lomelf.result_type(ins.text)
                    fr.wait_retty = "void" if ty is None else ty
            a0 = (a0hi << 32) | a0lo
            fr.wait_hook = (("alloc", a0) if hook == 1
                            else ("free", a0) if hook == 2 else None)
            fr.internal = fn.name.startswith("__loment_")
            fr.pending = []
            for pd, name, lo, hi, cap in pending:
                if pd != d:
                    continue
                ty = self.value_types.get((fn.name, name))
                if ty is not None and lomelf.is_agg(ty):
                    val = (self.mem.get_run(
                               self.mem.elem(fr.base, lo, FRAME_SIZE, 1), hi)
                           if flags == 3 else bytes(self.mem.buf[lo:lo + hi]))
                else:
                    val = lo | (hi << 32)
                fr.pending.append((name, val, cap))
            self.stack.append(fr)
        self.priv = sum(1 for f in self.stack if f.internal)
        self.cp_at = None


def run_checkpointed(text: str, out, substrate: str = "none",
                     at: int | None = None, resume: bytes | None = None,
                     argv: list[str] | None = None
                     ) -> tuple[int, bytes | None]:
    """跑一份 IR，可选在**第 at 条指令之前**停住并交出快照；或从快照继续。

    `docs/360` §7 B4 的落点。返回 `(退出码, 快照或 None)`。这一档**不记剖面/计数**：
    它们记的是解释器自己的账，不是 guest 状态；要记它们就别用 checkpoint。
    """
    if substrate not in ("none", "sw_cap", "sw_tag"):
        raise LvmError(f"没有这一档基质: {substrate!r}（只有 none / sw_cap / sw_tag）")
    dis = "; loment-memory: dismemory" in text
    if dis and substrate != "none":
        raise LvmError("dismemory 与基质不叠加（docs/368 §1.6）")
    shape = entry_kind(text)
    if shape is None:
        raise LvmError(NO_ENTRY)
    kind, name = shape
    if kind in ("interrupt", "thread_spawn"):
        raise LvmError(entry_refusal(kind, name))
    globals_, funcs, externs = lomelf.parse_ll(text)
    sub = (SwCap("cap") if substrate == "sw_cap"
           else SwCap("tag") if substrate == "sw_tag" else None)
    vm = ExplicitExecutor(globals_, funcs, externs, out, None, argv, None, sub,
                          dismemory=dis)
    vm.ir_text = text
    vm.ir_bytes = text.encode("utf-8")
    vm.ir_fnv = fnv32(vm.ir_bytes)
    vm.func_list = funcs
    vm.fn_index = {f.name: i for i, f in enumerate(funcs)}
    vm.block_index = {}
    vm.global_blocks = []
    gidx = 0
    for f in funcs:
        vm.block_index[f.name] = {}
        for b in f.blocks:
            vm.block_index[f.name][b.label] = gidx
            vm.global_blocks.append((f, b))
            gidx += 1
    vm.name_spans, vm.value_types = ir_layout(text, funcs)
    if resume is not None:
        vm.restore(resume)
        want = {"none": None, "sw_cap": "cap", "sw_tag": "tag"}[substrate]
        got = vm.sub.mode if vm.sub is not None else None
        if got != want:
            raise LvmError("checkpoint：基质与快照不匹配（resume 要带同一档）")
    # `at` 是**本次**命令行的：恢复之后才设，避免被快照里的旧停点覆盖。
    vm.cp_at = at
    try:
        if resume is not None:
            value = vm.run_resumed()
            return ((value & 0xFF) if kind != "_start" else 0), None
        start = next((f for f in funcs if f.name == name), None)
        if start is None:
            raise LvmError(f"这份 IR 声明了入口 `{name}`，但解析表里没有它")
        args = entry_values(vm, kind, name)
        if kind == "_start":
            vm.run(start, [], None)
        else:
            return (vm.run_entry(start, args) & 0xFF), None
    except _Exit as e:
        return e.code, None
    except _Trap as e:
        return trap_exit(e.site, e.detail), None
    except _Fault as e:
        return fault_exit(e.kind, e.site, e.witness, e.detail), None
    except _Suspend as e:
        return 0, e.blob
    return 0, None


# ---------------------------------------------------------------- 前门

def run_ir(text: str, out, profile: Profile | None = None,
           argv: list[str] | None = None, tier: str = "t0",
           values=None, deopts=None, counters: "Counters | None" = None,
           substrate: str = "none", relocate_every: int = 0, shuffle: int = 0,
           host: HostProfile | None = None,
           report: "SyscallReport | None" = None) -> int:
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
    if host is not None or report is not None:
        if tier != "t0":
            raise LvmError("宿主剖面与覆盖报告只在 t0 落地（`docs/311` §5）")
        host = host or host_profile("elf")
    if tier == "t2":
        if substrate != "none":
            raise LvmError("基质（sw_cap / sw_tag）只在 t0 落地（`docs/362`/`docs/364`）；t2/t3 还没有它")
        import lvm_t2                                       # noqa: PLC0415

        return lvm_t2.run_ir_t2(text, out, profile, argv, values, counters=counters)
    if tier == "t3":
        if substrate != "none":
            raise LvmError("基质（sw_cap / sw_tag）只在 t0 落地（`docs/362`/`docs/364`）；t2/t3 还没有它")
        import lvm_t3                                       # noqa: PLC0415

        return lvm_t3.run_ir_t3(text, out, profile, argv, values, deopts, counters)
    if tier != "t0":
        raise LvmError(f"没有这一档: {tier!r}（只有 t0 / t2 / t3）")
    if substrate not in ("none", "sw_cap", "sw_tag"):
        raise LvmError(f"没有这一档基质: {substrate!r}（只有 none / sw_cap / sw_tag）")
    dismemory = "; loment-memory: dismemory" in text
    if dismemory and substrate != "none":
        raise LvmError("dismemory 与基质（sw_cap / sw_tag）不叠加："
                       "这一档自己的规则已经覆盖了谁能碰（docs/368 §1.6）")
    shape = entry_kind(text)
    if shape is None:
        raise LvmError(NO_ENTRY)
    kind, name = shape
    if kind in ("interrupt", "thread_spawn"):
        raise LvmError(entry_refusal(kind, name))
    globals_, funcs, externs = lomelf.parse_ll(text)
    if profile is not None:
        profile.bind(funcs)
    sub = (SwCap("cap") if substrate == "sw_cap"
           else SwCap("tag") if substrate == "sw_tag" else None)
    vm = Executor(globals_, funcs, externs, out, profile, argv, counters, sub,
                  dismemory=dismemory,
                  relocate_every=relocate_every, shuffle=shuffle,
                  host=host, report=report)
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
        return trap_exit(e.site, e.detail)
    except _Fault as e:
        return fault_exit(e.kind, e.site, e.witness, e.detail)
    return 0


def run_source(src: Path, root: Path, out=None, profile: Profile | None = None,
               argv: list[str] | None = None, tier: str = "t0",
               values=None, deopts=None, counters: "Counters | None" = None,
               substrate: str = "none", relocate_every: int = 0, shuffle: int = 0,
               host: HostProfile | None = None,
               report: "SyscallReport | None" = None) -> int:
    """与 `lomelf` 同一条前门：`.lomt` -> IR -> 跑。"""
    if argv is None:
        argv = [str(src)]
    return run_ir(compile_source(src, root),
                  out if out is not None else sys.stdout, profile, argv, tier,
                  values, deopts, counters, substrate, relocate_every, shuffle,
                  host=host, report=report)


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
    ap.add_argument("--dismemory-shuffle", type=int, default=0, metavar="N",
                    help="dismemory（docs/375 §2.3）：表示层摆法。0 = 规范；非 0 给每颗体"
                         "一个确定性的移位，逻辑下标不变、物理桶不同 —— stdout/stderr 必须一样")
    ap.add_argument("--dismemory-relocate-every", type=int, default=0, metavar="K",
                    help="dismemory（docs/368）：每 K 条指令把全部活体重排一次（0 = 关；"
                         "句柄不变，所以两次的 stdout/rc 必须一样）")
    ap.add_argument("--substrate", choices=("none", "sw_cap", "sw_tag"), default="none",
                    help="LVM-A 的执行基质（docs/362/364）：none（默认，今天的行为）、"
                         "sw_cap（对象表 + 边界 + 对象代）、sw_tag（4 位标签，概率检测）；"
                         "只在 --tier t0")
    ap.add_argument("--host-profile", choices=("elf", "pe"), default="elf",
                    help="宿主能给什么（docs/311 §5 L2）：elf（默认，内核全给）、"
                         "pe（只放行 PE_DISPATCH 里的号，其余回 -ENOSYS）；只在 --tier t0")
    ap.add_argument("--syscall-report", metavar="PATH", default=None,
                    help="把表外的号写成一份可数的报告（docs/311 §4.2）："
                         "每行 `nr<TAB>类<TAB>程序<TAB>fn/blk/idx`；只在 --tier t0")
    ap.add_argument("--checkpoint-at", type=int, default=None, metavar="N",
                    help="P5（docs/363）：在第 N 条指令之前停住，把 guest 状态写进 "
                         "--checkpoint-out（只走显式栈引擎）")
    ap.add_argument("--checkpoint-out", metavar="PATH", default=None,
                    help="P5：快照写到 PATH；resume 就是再跑一次并给 --resume")
    ap.add_argument("--resume", metavar="PATH", default=None,
                    help="P5：从 PATH 的快照继续；入口仍要给（用来重新解析同一份 IR）")
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
    if (a.host_profile != "elf" or a.syscall_report is not None):
        if a.tier != "t0":
            print("lvm: --host-profile / --syscall-report 只在 --tier t0 下有效"
                  "（docs/311 §5）", file=sys.stderr)
            return 2
        if a.compact is not None or a.compact_out is not None or a.cache is not None:
            print("lvm: --host-profile / --syscall-report 与紧凑形/缓存不能同时给"
                  "（它们是另一个引擎）", file=sys.stderr)
            return 2
        if a.checkpoint_at is not None or a.resume is not None:
            print("lvm: --host-profile / --syscall-report 不进快照（它们是解释器的账，"
                  "不是 guest 状态）", file=sys.stderr)
            return 2
    if a.substrate != "none":
        if a.tier != "t0":
            print("lvm: --substrate sw_cap 只在 --tier t0 下有效（docs/362）",
                  file=sys.stderr)
            return 2
        if a.compact is not None or a.compact_out is not None or a.cache is not None:
            print("lvm: --substrate 与紧凑形/缓存不能同时给（它们是另一个引擎）",
                  file=sys.stderr)
            return 2
    checkpointing = a.resume is not None or a.checkpoint_at is not None
    if checkpointing:
        if a.checkpoint_at is not None and a.checkpoint_out is None:
            print("lvm: --checkpoint-at 需要 --checkpoint-out", file=sys.stderr)
            return 2
        if a.checkpoint_at is None and a.checkpoint_out is not None:
            print("lvm: --checkpoint-out 只在 --checkpoint-at 下有意义", file=sys.stderr)
            return 2
        if a.tier != "t0" or a.compact is not None or a.compact_out is not None \
                or a.cache is not None:
            print("lvm: checkpoint 只在纯 T0 下有效（docs/363）", file=sys.stderr)
            return 2
        if (a.profile_out is not None or a.counters_out is not None
                or a.values_out is not None or a.values_in is not None
                or a.deopt_out is not None):
            print("lvm: checkpoint 不记剖面/计数/值观察 —— 那些是解释器的账，不是 guest 状态",
                  file=sys.stderr)
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
    host = host_profile(a.host_profile)
    sreport = SyscallReport() if a.syscall_report is not None else None

    def _write_syscall_report() -> None:
        if sreport is not None:
            with Path(a.syscall_report).open("w", encoding="utf-8", newline="\n") as f:
                f.write(sreport.text())
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
        if checkpointing:
            ir = (Path(a.entry).read_text(encoding="utf-8") if a.ir
                  else compile_source(Path(a.entry).resolve(), root))
            blob = Path(a.resume).read_bytes() if a.resume is not None else None
            rc, cp = run_checkpointed(ir, buf, a.substrate, a.checkpoint_at, blob,
                                      [a.entry])
            if cp is not None:
                Path(a.checkpoint_out).write_bytes(cp)
        elif a.compact_out is not None:
            # 只编不跑：入口 -> IR -> 紧凑形。这是 docs/250 那条"写进文件"的路。
            ir = (Path(a.entry).read_text(encoding="utf-8") if a.ir
                  else compile_source(Path(a.entry).resolve(), root))
            Path(a.compact_out).write_bytes(lvm_compact.encode_ir(ir))
            return 0
        elif a.compact is not None:
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
                rc = run_ir(ir, buf, prof, [a.entry], a.tier, values, deopts, counters,
                            a.substrate, a.dismemory_relocate_every,
                            a.dismemory_shuffle, host=host, report=sreport)
        else:
            if a.tier == "t2":
                import lvm_t2                                # noqa: PLC0415
                ir = compile_source(Path(a.entry).resolve(), root)
                rc = lvm_t2.run_ir_t2(ir, buf, prof, [a.entry], values,
                                      a.fusion_out is not None or a.fusion_profile is not None,
                                      fusion, hot, counters=counters)
            else:
                rc = run_source(Path(a.entry).resolve(), root, buf, prof, [a.entry],
                                a.tier, values, deopts, counters, a.substrate,
                                a.dismemory_relocate_every, a.dismemory_shuffle,
                                host=host, report=sreport)
    except (LvmError, lomelf.ElfError, lomelf.Unsupported,
            lvm_compact.CompactError) as e:
        sys.stdout.buffer.write(buf.getvalue())
        _write_syscall_report()
        print(f"lvm: {e}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(buf.getvalue())
    _write_syscall_report()
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
