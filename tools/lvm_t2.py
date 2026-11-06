#!/usr/bin/env python3
"""lvm_t2.py — LVM 第二格（T2）：**基线编译**（`docs/232` §4.4，issue #325）。

## 这一格要解决什么

T0（`tools/lvm.py`）每执行一条指令，就把**那条指令的文本重新解一遍**：

* 一层 `startswith` 链认出指令种类，认不出再上两三个正则；
* 每个操作数一次 `lomelf.parse_type` + `lomelf.parse_operand`；
* SSA 名当**字符串**做字典键（`fr.vals[ins.dest]`）；
* 每进一个块，线性扫一遍 `fn.blocks` 找标签；
* `getelementptr` 的类型游走、`icmp` 的谓词、`br`/`switch` 的边号——**每次**都重算。

**量出来的价钱**（`tour`，同一进程交替跑，取中位数）：只把 `parse_type`/`parse_operand`
记忆化，省 **11.2%**。⇒ **"少解几次"撑不起这一格**（`docs/232` §4.4 要求"显著快于 T0"）。
所以 T2 不是缓存，是**把"解"整体搬到执行之前**，只做一次。

## 编译成什么

每个 IR 函数编译成一个 `_CFunc`：块是**下标**、值是**槽下标**、操作数是
`(种类, 数)`（槽 / 立即数 / 零聚合）、宽度与偏移在编译期算好。执行期只剩
"读槽、算、写槽、跳块"，**没有一处再读文本**。

⚠ **编译必须在全局地址落位之后**（`Vm._place_globals` 才给 `Global.addr` 赋值）。
所以前门是"先建执行体（顺带落位），再编译"，不是反过来 —— 反过来的话每条 `@global`
操作数都会被编成地址 0。

## 它不该是另一台机器

T2 与 T0 **共用**同一个 `Mem`、同一个 `Executor.syscall`、同一个 `Profile`、同一套帧布局
（`STACK_TOP - depth * FRAME_SIZE`）。所以：

* **判据 A（`tools/lvm_t2_test.py`）**：原生产物 / T0 / T2 三者 **stdout + 退出码逐字节相同**；
* **判据 B**：T1 的剖面在 T0 与 T2 下**逐字节相同**（换引擎不许改变被记录的行为）。

⚠ **`alloca` 那条要照抄，不要"顺手修好"**：T0 里 `alloca` **每执行一次就往上 bump 一次**，
所以在循环里它每次返回的地址都不同、而且帧会一直长。那是 T0 的**可观察行为**（程序可以
比地址），T2 换了引擎但**不许改变它** —— 要改，是另一件事、另一条判据。
"""

from __future__ import annotations

import re

import lomelf
import lvm

# ---------------------------------------------------------------- 操作码
(
    _OP_ALLOCA, _OP_LOAD, _OP_STORE, _OP_GEP, _OP_EXTRACT, _OP_INSERT,
    _OP_ICMP, _OP_DIV, _OP_ARITH, _OP_CAST, _OP_CALL, _OP_ASM, _OP_RMW,
    _OP_PHI, _OP_BR, _OP_SWITCH, _OP_RET, _OP_UNREACH,
    #: 编译期就失败的指令（issue #715）。**错误延迟到执行到这一条时才发** ——
    #: T0 只在**走到**一条坏指令时报错，不可达块里的坏代码不是错。
    _OP_ERROR,
    #: 结构体下标不是常数的 `getelementptr`（issue #715）。静态走不动类型链，
    #: 退到与 T0 共用的 `lvm.gep_offset`（**运行期**算，和 T0 同义）。
    _OP_GEP_DYN,
    #: 终结指令之后的指令（或坏指令之后那一段）—— T0 永远走不到，占位而已。
    _OP_DEAD,
) = range(1, 22)

#: 一个**没被写过**的槽。T0 读到它时抛"读未定义的值"，T2 用同一个哨兵复刻那个行为
#: （**不是**默认 0 —— 默认 0 会把"逻辑漏了一条指令"变成"算出来是 0"，那是最坏的一种）。
_UNDEF = object()

_BR_UNC = re.compile(r"br label %([\w.]+)$")
_ICMP_RE = re.compile(r"icmp (\w+) (\w+) (\S+), (\S+)")
_RMW_RE = re.compile(r"atomicrmw (\w+) ptr (\S+), (\w+) (\S+)(?: \w+)*")
_CAST_RE = re.compile(r"(\w+) (\w+) (\S+) to (\w+)")
_DIV_RE = re.compile(r"(sdiv|udiv|srem|urem) (\w+) (\S+), (\S+)")
_ARITH_RE = re.compile(r"(\w+) (\w+) (\S+), (\S+)")
_CALL_RE = re.compile(r"@([-A-Za-z0-9_.]+)\((.*)\)\s*$", re.S)


# ---------------------------------------------------------------- 编译期
class _CFunc:
    """一个函数编译后的样子。`blocks[i][j]` 就是 IR 里第 i 块的第 j 条指令。"""

    __slots__ = ("name", "nslots", "params", "blocks", "labels", "plan", "rplan")

    def __init__(self, name: str) -> None:
        self.name = name
        self.nslots = 0
        self.params: list[int] = []
        self.blocks: list[list[tuple]] = []
        self.labels: list[str] = []
        #: 派生的**融合计划**（`docs/266`，issue #647）：每个块一张
        #: `[(起始下标, 条数, 形状名), ...]`。**不序列化** —— 它完全由 `blocks` 决定，
        #: 紧凑形解出来的 `_CFunc` 在第一次执行时重算，所以缓存与直编拿到同一张计划。
        self.plan: list[list[tuple[int, int, str]]] | None = None
        #: **A0 的寄存器计划**（`docs/248` §3.1，issue #648）：每块一张
        #: `_RBlock`，`None` = 这一块没生成代码（含不支持的 op，退回槽机）。
        #: 与 `plan` 一样**不序列化** —— 它完全由 `blocks` 决定。
        self.rplan = None


class Program:
    """整份 IR 编译后的样子。**只建一次**，之后随便跑多少趟。

    ⚠ **按需编译（第一次被调用时才编）**，不是一次编完整份。理由是 T0 的行为：
    它只在**执行到**一条不认识的指令时才报错。`tour` / `all_loment` 那份 IR 里就有
    `declare void @llvm.trap()` 与它的调用点 —— 在**走不到**的函数里。一次编完整份
    会让"根本没执行到的代码"把整个程序打死，那是**换引擎顺带改了语义**。
    """

    def __init__(self, globals_: list, funcs: list, externs: list,
                 fuse: bool = True, fusion: "FusionReport | None" = None,
                 hot: set[tuple[str, str]] | None = None,
                 counters: "lvm.Counters | None" = None) -> None:
        # ⚠ 调用方必须**已经**把全局摆好了（`Global.addr` 有值），否则下面的 `enc`
        # 会把每个 `@global` 编成地址 0 —— 见模块开头那段。
        self.globals = {g.name: g for g in globals_}
        self.funcs = {f.name: f for f in funcs}
        self.externs = set(externs)
        self.cfuncs: dict[str, _CFunc] = {}
        #: A4（#647）：融合的开关、报告与**热点块集**。`hot is None` = 每块都融；
        #: 给了集合就只在集合里的块融（"chosen from the T1 profile's counts"）。
        self.fuse = fuse
        self.fusion = fusion
        self.hot = hot
        #: #1028：确定性工作量计数。**每个函数最多真正解一次** —— `cfuncs` 就是那道
        #: 缓存，`decode_requests` 记"来问了几次"、`decode(name)` 记"真解了几次"。
        self.counters = counters

    def cfunc(self, name: str) -> "_CFunc":
        """取一个函数编译后的样子；没编过就现在编。错误消息**与 T0 逐字一致**。"""
        c = self.counters
        if c is not None:
            c.decode_requests += 1
        cf = self.cfuncs.get(name)
        if cf is not None:
            return cf
        if name in self.externs:
            raise lvm.LvmError(f"`{name}` 是 extern —— T2 不接 FFI（`docs/243` §5）")
        fn = self.funcs.get(name)
        if fn is None:
            raise lvm.LvmError(f"没有这个函数: {name}")
        cf = _compile(fn, self)
        self.cfuncs[name] = cf
        if c is not None:
            c.decode(name)
        return cf


def _compile(fn, prog: Program) -> _CFunc:
    cf = _CFunc(fn.name)
    cf.labels = [b.label for b in fn.blocks]
    block_of = {b.label: i for i, b in enumerate(fn.blocks)}
    slots: dict[str, int] = {}

    def slot_of(name: str) -> int:
        got = slots.get(name)
        if got is None:
            got = len(slots)
            slots[name] = got
        return got

    def enc(s: str) -> tuple:
        """一条操作数**文本** -> `(种类, 数)`。种类：0 槽 / 1 立即数 / 2 零聚合。"""
        ty, v = lomelf.parse_operand(s)
        if v.startswith("%"):
            return (0, slot_of(v), v)
        if v.startswith("@"):
            g = prog.globals.get(v)
            if g is None:
                raise lvm.LvmError(f"未知全局 {v}")
            return (1, g.addr)
        if v == "undef" or v == "null":
            return (1, 0) if not lvm._is_agg(ty) else (2, lomelf.size_of(ty))
        if v == "true":
            return (1, 1)
        if v == "false":
            return (1, 0)
        return (1, int(v))

    for (_pty, pname) in fn.params:
        cf.params.append(slot_of(pname))

    for blk in fn.blocks:
        ops: list[tuple] = []
        #: 已经不可能再执行到的位置：坏指令之后、或终结指令之后的那一段。
        dead = False
        for ins in blk.instrs:
            dest = slot_of(ins.dest) if ins.dest else -1
            if dead:
                ops.append((_OP_DEAD,))
                continue
            try:
                ops.append(_compile_instr(ins.text, dest, block_of, enc, prog))
            except Exception as e:  # noqa: BLE001 —— 见下
                # ⚠ **不在编译期发出这个错**（issue #715）。T0 只有**执行到**一条
                # 解不了的指令时才报；而 T2 编译的是整块，包括**走不到**的部分
                # （`dead:` 块、终结指令之后）。把错误做成一条 op、到执行期走到它
                # 才抛，接受集合就与 T0 一致；在此之前 T2 会把不可达代码当硬错误。
                # 这里接 `Exception` 而不是只接 `LvmError`：T0 那条路对同样的垃圾
                # 也可能抛 `ElfError`/`ValueError`（都是一条错），接受集合才是要
                # 对齐的那个量。
                ops.append((_OP_ERROR, f"{type(e).__name__}: {e}"))
                dead = True
                continue
            if ins.text.startswith(("br ", "switch ", "ret", "unreachable")):
                dead = True                 # 终结指令之后：T0 执行不到
        cf.blocks.append(ops)

    cf.nslots = len(slots)
    return cf


def _compile_instr(text: str, dest: int, block_of: dict, enc, prog: Program) -> tuple:
    """一条指令 -> 一个 op 元组。**逐条对齐 `tools/lvm.py` 的 `_eval` 与控制流那几支**。"""
    # ---- 控制流（T0 在 `_run_body` 里就地处理的那五条）
    if text.startswith("phi "):
        ty, entries = lomelf.parse_phi(text)
        m: dict[int, tuple] = {}
        for val_s, label in entries:
            bi = block_of.get(label)
            if bi is None:
                raise lvm.LvmError(f"{label}: 没有这个块（phi）")
            # ⚠ 入边值**不带类型**（`phi i64 [ 0, %entry ]`），类型只写在 phi 头上 ——
            # 与 T0 同一条回退：先按原样解，解析不了再把 phi 的类型补上。
            try:
                m[bi] = enc(val_s)
            except lomelf.ElfError:
                m[bi] = enc(f"{ty} {val_s}")
        return (_OP_PHI, dest, m)

    if text.startswith("br "):
        mo = _BR_UNC.match(text)
        if mo is not None:
            t = block_of.get(mo.group(1))
            if t is None:
                raise lvm.LvmError(f"{mo.group(1)}: 没有这个块（br）")
            return (_OP_BR, None, t, -1)
        mc = lomelf.BR_COND_RE.search(text)
        if not mc:
            raise lvm.LvmError(f"br 解析不了: {text!r}")
        t, f = block_of.get(mc.group(2)), block_of.get(mc.group(3))
        if t is None or f is None:
            raise lvm.LvmError(f"br 的目标块不在: {text!r}")
        return (_OP_BR, enc("i1 " + mc.group(1)), t, f)

    if text.startswith("switch "):
        ms = lomelf.SWITCH_RE.match(text)
        if not ms:
            raise lvm.LvmError(f"switch 解析不了: {text!r}")
        ty, val_s, default, body = ms.groups()
        cases = []
        for (cty, cval, label) in lomelf.CASE_RE.findall(body):
            bi = block_of.get(label)
            if bi is None:
                raise lvm.LvmError(f"{label}: 没有这个块（switch）")
            cases.append((cty, int(cval), bi))
        d = block_of.get(default)
        if d is None:
            raise lvm.LvmError(f"{default}: 没有这个块（switch default）")
        return (_OP_SWITCH, enc(ty + " " + val_s), cases, d)

    if text.startswith("ret"):
        rest = text[len("ret"):].strip()
        if not rest or rest == "void":
            return (_OP_RET, None)
        return (_OP_RET, enc(rest))

    if text.startswith("unreachable"):
        return (_OP_UNREACH,)

    # ---- 值指令（T0 的 `_eval`）
    if text.startswith("alloca"):
        ty, _ = lomelf.parse_type(text[len("alloca "):])
        return (_OP_ALLOCA, max(lvm.SLOT, lomelf.size_of(ty)), dest)

    if text.startswith("load "):
        body = text[len("load "):]
        ty, i = lomelf.parse_type(body)
        return (_OP_LOAD, ty, enc(body[i:].lstrip(", ").strip()), dest)

    if text.startswith("store "):
        body = text[len("store "):]
        ty, i = lomelf.parse_type(body)
        val_s, ptr_s = lomelf.split_top(body[i:].lstrip(", "))
        return (_OP_STORE, ty, enc(ty + " " + val_s), enc(ptr_s))

    if text.startswith("getelementptr"):
        return _compile_gep(text, dest, enc)

    if text.startswith("extractvalue "):
        ty, i = lomelf.parse_type(text[len("extractvalue "):])
        parts = lomelf.split_top(text[len("extractvalue ") + i:])
        off, fty = lomelf.field_offset(ty, int(parts[-1]))
        return (_OP_EXTRACT, enc((ty + " " + parts[0]).lstrip()), off, fty, dest)

    if text.startswith("insertvalue "):
        ty, i = lomelf.parse_type(text[len("insertvalue "):])
        parts = lomelf.split_top(text[len("insertvalue ") + i:])
        off, fty = lomelf.field_offset(ty, int(parts[2]))
        return (_OP_INSERT, ty, enc((ty + " " + parts[0]).lstrip()), enc(parts[1]),
                off, fty, dest)

    if text.startswith("icmp "):
        m = _ICMP_RE.match(text)
        if not m:
            raise lvm.LvmError(f"icmp 解析不了: {text!r}")
        pred, ty, a_s, b_s = m.groups()
        fn = lvm._ICMP.get(pred)
        if fn is None:
            raise lvm.LvmError(f"icmp 谓词没实现: {pred}")
        return (_OP_ICMP, fn, pred in lvm._SIGNED_CMP, ty,
                enc(ty + " " + a_s), enc(ty + " " + b_s), dest)

    if text.startswith("atomicrmw "):
        m = _RMW_RE.match(text)
        if not m or m.group(1) != "add":
            raise lvm.LvmError(f"atomicrmw 只实现了 add: {text!r}")
        return (_OP_RMW, m.group(3), enc("ptr " + m.group(2)),
                enc(m.group(3) + " " + m.group(4)), dest)

    if text.startswith(("trunc ", "zext ", "sext ", "ptrtoint ", "inttoptr ")):
        m = _CAST_RE.match(text)
        if not m:
            raise lvm.LvmError(f"转型解析不了: {text!r}")
        op, fty, a_s, tty = m.groups()
        return (_OP_CAST, op, fty, tty, enc(fty + " " + a_s), dest)

    if text.startswith("call "):
        return _compile_call(text, dest, enc, prog)

    # ---- 除法 / 取余（T0 里单独一支：要判零、要符号、取余要减回去）
    m = _DIV_RE.match(text)
    if m:
        op, ty, a_s, b_s = m.groups()
        return (_OP_DIV, op, ty, enc(ty + " " + a_s), enc(ty + " " + b_s), dest)

    m = _ARITH_RE.match(text)
    if m and m.group(1) in lvm._ARITH:
        op, ty, a_s, b_s = m.groups()
        # 存**算子名**而不是那一格的 lambda：执行期要走 `lvm.arith`（issue #717 的
        # 负移位量检查在那里），T0 与 T2 因此是同一份算术语义。
        return (_OP_ARITH, op, ty, enc(ty + " " + a_s), enc(ty + " " + b_s), dest)

    raise lvm.LvmError(f"这个指令 T2 没实现: {text!r}")


def _compile_gep(text: str, dest: int, enc) -> tuple:
    """`getelementptr` 的类型游走是**静态的** —— 编译期走完，执行期只剩乘加。"""
    body = text[len("getelementptr"):].strip()
    if body.startswith("inbounds"):
        body = body[len("inbounds"):].strip()
    ty, i = lomelf.parse_type(body)
    parts = lomelf.split_top(body[i:].lstrip(", "))
    base = enc(parts[0])
    const = 0
    stages: list[tuple] = []
    cur = ty
    for k, idx_s in enumerate(parts[1:]):
        if k == 0:
            stages.append((enc(idx_s), lomelf.size_of(cur)))
            continue
        if cur.startswith("["):
            cur = lomelf.array_parts(cur)[1]
            stages.append((enc(idx_s), lomelf.size_of(cur)))
        elif cur.startswith("{"):
            _ty, v = lomelf.parse_operand(idx_s)
            if not v.lstrip("-").isdigit():
                # 结构体下标不是常数 ⇒ 后面每一步的"当前类型"都随值变，静态走不动。
                # ⚠ **T0 在这条路上有对应物**：`lvm.py::_gep` 一直在**运行期**用
                # `lomelf.field_offset` 走类型链（LLVM 不允许非恒定结构体下标，但
                # T0 与自举镜像都跑得动这种 IR）。所以这里不是"T0 没有对应物"，
                # 而是把整条 GEP 退到与 T0 共用的 `lvm.gep_offset`（issue #715）。
                return (_OP_GEP_DYN, base, ty, [enc(p) for p in parts[1:]], text, dest)
            off, cur = lomelf.field_offset(cur, int(v))
            const += off
        else:
            raise lvm.LvmError(f"GEP 的这一步 T2 没实现: {text!r}")
    return (_OP_GEP, base, const, stages, dest)


def _compile_call(text: str, dest: int, enc, prog: Program) -> tuple:
    m = lvm._CALL_ASM.match(text)
    if m:
        name = m.group(lvm._ASM_NAME)
        if name != "syscall":
            raise lvm.LvmError(f"这一档不支持的 asm: {name!r}（`docs/245`）")
        argtext = m.group(lvm._ASM_ARGS)
        return (_OP_ASM, [enc(s) for s in lomelf.split_top(argtext)], dest)
    body = text[len("call "):].strip()
    if body.startswith("void"):
        body = body[4:].strip()
    else:
        _retty, i = lomelf.parse_type(body)
        body = body[i:].strip()
    m2 = _CALL_RE.match(body)
    if not m2:
        raise lvm.LvmError(f"解析不了调用: {text!r}")
    name = m2.group(1)
    args = [enc(s) for s in lomelf.split_top(m2.group(2)) if s]
    # 被调方**不在编译期解析**：T0 是执行到那一条才报"extern / 没有这个函数"，
    # T2 要在同一个时刻报同一句话（见 `Program.cfunc`）。
    return (_OP_CALL, name, args, dest)


# ---------------------------------------------------------------- A4：超级指令
_OPNAME = {
    _OP_ALLOCA: "alloca", _OP_LOAD: "load", _OP_STORE: "store", _OP_GEP: "gep",
    _OP_GEP_DYN: "gep.dyn", _OP_EXTRACT: "extractvalue", _OP_INSERT: "insertvalue",
    _OP_ICMP: "icmp", _OP_DIV: "div", _OP_ARITH: "arith", _OP_CAST: "cast",
    _OP_CALL: "call", _OP_ASM: "asm", _OP_RMW: "atomicrmw", _OP_PHI: "phi",
    _OP_BR: "br", _OP_SWITCH: "switch", _OP_RET: "ret", _OP_UNREACH: "unreachable",
    _OP_ERROR: "error", _OP_DEAD: "dead",
}


def _shape(op: tuple) -> str:
    """报告里那一格的名字。算术 / 除法 / 转型带上算子名（`add`、`sdiv`、`zext`）——
    只写 `arith` 的话，报告说不清"哪一串"被合了。"""
    k = op[0]
    if k in (_OP_ARITH, _OP_DIV, _OP_CAST) and len(op) > 1 and isinstance(op[1], str):
        return op[1]
    return _OPNAME.get(k, f"op{k}")


#: 有**专门处理器**的两条形状（`_exec_pair`）。只合这两种 —— 把三条以上或没专
#: 门处理器的形状硬塞进来，会让执行退回 `_run_value` 的通用分派，**比不合并还慢**
#: （2026-10-19 实测：第一版就是那么写的，T2 从 5.5x 掉到 4.1x）。
_PAIR_OK = frozenset({
    (_OP_ARITH, _OP_ARITH),
    (_OP_ARITH, _OP_STORE),
    (_OP_LOAD, _OP_ARITH),
    (_OP_ICMP, _OP_BR),
})


def _plan_block(ops: list, fuse: bool = True) -> list[tuple[int, int, str]]:
    """一块的融合计划：`[(起始下标, 条数, 形状名), ...]`。**下标是 IR 里的原下标** ——
    剖面按它记边与调用点，融合不许把站点挪走。"""
    if not fuse:
        return [(i, 1, _shape(op)) for i, op in enumerate(ops)]
    plan: list[tuple[int, int, str]] = []
    i, n = 0, len(ops)
    while i < n:
        if i + 1 < n and (ops[i][0], ops[i + 1][0]) in _PAIR_OK:
            plan.append((i, 2, _shape(ops[i]) + "+" + _shape(ops[i + 1])))
            i += 2
            continue
        plan.append((i, 1, _shape(ops[i])))
        i += 1
    return plan


class FusionReport:
    """融合报告（`docs/266`）：一个**关着就一个字节都不写**的产物。

    格式与 T1 剖面同一套纪律（`docs/247`）：`[sites]` 按 IR 解析顺序，`[patterns]`
    给出每种形状的总数。**没有这份报告，判据只能量"变快了"，说不出"合了什么"。**
    """

    def __init__(self) -> None:
        self.sites: list[tuple[str, str, int, str]] = []
        self.counts: dict[str, int] = {}

    def add(self, fn: str, label: str, start: int, shape: str) -> None:
        self.sites.append((fn, label, start, shape))
        self.counts[shape] = self.counts.get(shape, 0) + 1

    def text(self) -> str:
        out = ["# lvm fusion v1",
               "# site = (function, block, first-instruction-index); shape = the fused op sequence",
               "# order = IR parse order; [patterns] is sorted by shape",
               "[sites]"]
        out += [f"{fn}	{blk}	{i}	{shape}" for (fn, blk, i, shape) in self.sites]
        out.append("[patterns]")
        out += [f"{shape}	{self.counts[shape]}" for shape in sorted(self.counts)]
        return chr(10).join(out) + chr(10)


def parse_hot_blocks(text: str) -> set[tuple[str, str]]:
    """从一份 T1 剖面里取出**被执行过的块**（`[blocks]` 段）。融合只在这些块里做 ——
    "chosen from the T1 profile's counts"（issue #647）。剖面的格式见 `docs/247`。"""
    hot: set[tuple[str, str]] = set()
    section = ""
    for ln in text.splitlines():
        if ln.startswith("[") and ln.endswith("]"):
            section = ln[1:-1]
            continue
        if section != "blocks" or not ln or ln.startswith("#"):
            continue
        parts = ln.split("	")
        if len(parts) >= 2:
            hot.add((parts[0], parts[1]))
    return hot


# ---------------------------------------------------------------- 执行期
def _rd(vals: list, op: tuple):
    k = op[0]
    if k == 0:
        v = vals[op[1]]
        if v is _UNDEF:
            raise lvm.LvmError(f"读未定义的值 {op[2]}")
        return v
    if k == 1:
        return op[1]
    return b"\0" * op[1]


def _do_div(kind: str, ty: str, a: int, b: int) -> int:
    """**逐字对齐** T0 那一支（`tools/lvm.py` 的 `sdiv/udiv/srem/urem`）。"""
    if kind in ("sdiv", "srem"):
        a, b = lvm.signed(a, ty), lvm.signed(b, ty)
    if b == 0:
        raise lvm.LvmError("除以零")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    if kind in ("srem", "urem"):
        return lvm.mask(a - b * q, ty)
    return lvm.mask(q, ty)


class T2Executor(lvm.Executor):
    """T0 的执行体 + 一个**编译档**的 `run`。系统调用 / 内存 / 剖面**全用 T0 那一份**。"""

    def __init__(self, globals_: list, funcs: list, externs: list, out,
                 profile=None, argv: list[str] | None = None, values=None,
                 fuse: bool = True, fusion: "FusionReport | None" = None,
                 hot: set[tuple[str, str]] | None = None,
                 counters: "lvm.Counters | None" = None) -> None:
        super().__init__(globals_, funcs, externs, out, profile, argv, counters)
        #: T3 的**值观察**（`docs/248` §3.2 的 B1 要"值"，而剖面只记次数）。
        #: 只在开着剖面时才记（那一条路本来就有指令下标 `j`）—— 见 `_OP_CALL` 那一支。
        self.values = values
        # ⚠ **必须在 `super().__init__` 之后**：那里才调 `_place_globals` 给
        # `Global.addr` 赋值，而编译期要拿那个地址去编每个 `@global` 操作数。
        self.prog = Program(globals_, funcs, externs, fuse, fusion, hot, counters)

    # ------------------------------------------------------------ 融合计划
    # ------------------------------------------------------------ A0
    def _rplan(self, cf: _CFunc):
        """取（并第一次算）这个函数的寄存器计划。`False`/`None` = 退回槽机。"""
        if cf.rplan is None:
            # 执行体**没有覆盖** `load`/`store` 才把内存访问摊开 —— 覆盖了还摊，
            # 就是绕过了那个覆盖（故障注入那一类子类会踩这条）。
            plain = (type(self).load is lvm.Executor.load
                     and type(self).store is lvm.Executor.store)
            cf.rplan = _plan_regs(cf, plain)
        return cf.rplan or None

    def _body_regs(self, cf: _CFunc, vals: list, base: int, rp: list):
        """**A0 的执行环**：值住在 `R` 里，`vals` 只在块边界被碰。

        `st` = `[bi, prev, nxt, steps, ret]` —— 用列表而不是对象：这是每次调用都要建的
        唯一一件东西，属性访问在热路径上比下标贵。
        """
        nregs = max((rb.nregs for rb in rp), default=0)
        R: list = [None] * nregs
        st = [0, -1, base, 0, 0]
        bi, prev = 0, -1
        while True:
            rb = rp[bi]
            for (s, r) in rb.lin:
                R[r] = vals[s]
            st[0], st[1] = bi, prev
            nbi = rb.fn(self, R, st, vals)
            if nbi < 0:
                return st[4]
            for (r, s) in rb.lout:
                vals[s] = R[r]
            prev, bi = bi, nbi

    def _plan(self, cf: _CFunc, bi: int, blk: list) -> list:
        """取（并第一次算）这个函数的融合计划。**按块决定**：热点集里没有的块不融。"""
        if cf.plan is None:
            cf.plan = []
            for k, ops in enumerate(cf.blocks):
                allow = self.prog.fuse and (
                    self.prog.hot is None or (cf.name, cf.labels[k]) in self.prog.hot)
                cf.plan.append(_plan_block(ops, allow))
            if self.prog.fusion is not None:
                for k, plan in enumerate(cf.plan):
                    for start, count, shape in plan:
                        if count > 1:
                            self.prog.fusion.add(cf.name, cf.labels[k], start, shape)
        return cf.plan[bi]

    # ------------------------------------------------------------ 调用
    def call_t2(self, cf: _CFunc, args: list, entry: bool = False):
        # 兜底（T3 也会从这里过）：`_body` 那两处已经**在求值之前**判过个数，
        # 这里再判一次只是为了任何新调用点都不会绕过 issue #716 的那个答案。
        if not entry and len(cf.params) != len(args):
            raise lvm.LvmError("实参个数与形参不符")
        if self.depth + 1 > lvm.MAX_DEPTH:
            raise lvm.LvmError(f"栈溢出（调用深度上限 {lvm.MAX_DEPTH}）")
        self.depth += 1
        base = lvm.STACK_TOP - self.depth * lvm.FRAME_SIZE
        if base <= self.mem.heap:
            self.depth -= 1
            raise lvm.LvmError("栈溢出")
        try:
            vals = [_UNDEF] * cf.nslots
            for i, s in enumerate(cf.params):
                if i < len(args):
                    vals[s] = args[i]
                elif entry:
                    vals[s] = 0                 # T0 对 `_start` 的形参一律给 0
            # **A0**（`docs/248` §3.1）：没有剖面、没有计数器时走寄存器那条路。
            # 有剖面时**必须**走槽机 —— 判据 B 比的是剖面逐字节，而记剖面那条路
            # 是"每条指令一个下标"的形状，A0 会把它换成另一台机器。
            # 有计数器时同理（#1028 的确定性计数按槽机口径定义）。
            if self.profile is None and self.counters is None:
                rp = self._rplan(cf)
                if rp:
                    return self._body_regs(cf, vals, base, rp)
            return self._body(cf, vals, base)
        finally:
            self.depth -= 1

    def _body(self, cf: _CFunc, vals: list, base: int):       # noqa: C901
        prof = self.profile
        blocks, labels, name = cf.blocks, cf.labels, cf.name
        nxt = base
        bi, prev = 0, -1
        steps = 0                       # **整个调用**共用一个计数（跨块也算）—— 与镜像同口径
        cnt = self.counters             # #1028：关着时下面每一处都是 `if cnt`
        while True:
            blk = blocks[bi]
            # phi **并行**求值（与 `lvm._run_body` 同一条注释）：先把整组结果算出来，
            # 再统一写回；否则后面的 phi 读到前面 phi 本轮刚写的值（`free` 的空闲链
            # 就靠 `%prev = [%cur, ...]` 读旧值，见 #1027）。
            if blk and blk[0][0] == _OP_PHI:
                pending: list[tuple[int, object]] = []
                for op in blk:
                    if op[0] != _OP_PHI:
                        break
                    _, d, mapping = op
                    src = mapping.get(prev)
                    if src is None:
                        raise lvm.LvmError(f"{name}: phi 没有来自块 {prev} 的入边")
                    if d >= 0:
                        pending.append((d, _rd(vals, src)))
                for d, v in pending:
                    vals[d] = v
            if prof is not None:
                prof.block(name, labels[bi])
                # 记剖面时不融合：那份产物要比逐字节（判据 B）。融合是执行期的事，
                # 开关在不在都不改变被记录的行为。
                for j, op in enumerate(blk):
                    steps += 1
                    if cnt is not None:
                        cnt.instructions += 1
                        cnt.tuple_lookups += 1
                    if steps > lvm.STEP_CAP:
                        raise lvm.LvmError(
                            f"步数上限 {lvm.STEP_CAP}：{name} 的块 {labels[bi]}，第 {j} 条")
                    code = op[0]

                    if code == _OP_CALL:
                        _, cname, a_ops, d = op
                        if cname == "llvm.trap":
                            raise lvm._Trap((name, labels[bi], j))  # noqa: SLF001
                        # ⚠ 别覆盖 `cf` —— 它还是**当前函数**，融合计划按它取
                        # （A4 之后这里踩过一次：覆盖后下一轮拿的是被调方的计划）。
                        callee = self.prog.cfunc(cname)
                        # issue #716：**先判个数、再求值实参** —— 多出来的那个实参若是
                        # 未定义的值，T0 报的是"实参个数与形参不符"，不是"读未定义的值"。
                        if len(a_ops) != len(callee.params):
                            raise lvm.LvmError("实参个数与形参不符")
                        args = [_rd(vals, o) for o in a_ops]
                        prof.call_site((name, labels[bi], j), cname)
                        if self.values is not None and args:
                            self.values.observe((name, labels[bi], j), args[0])
                        v = self.call_t2(callee, args)
                        if d >= 0:
                            vals[d] = v
                        continue
                    if code == _OP_BR:
                        _, cond_op, t, f = op
                        if cond_op is None:
                            prof.edge(name, labels[bi], j, 0)
                            prev, bi = bi, t
                            break
                        c = _rd(vals, cond_op)
                        prof.edge(name, labels[bi], j, 0 if c else 1)
                        prev, bi = bi, (t if c else f)
                        break
                    if code == _OP_SWITCH:
                        _, v_op, cases, dft = op
                        v = _rd(vals, v_op)
                        tgt, e = dft, len(cases)
                        for k, (cty, cval, cbi) in enumerate(cases):
                            if lvm.mask(v, cty) == cval:
                                tgt, e = cbi, k
                                break
                        prof.edge(name, labels[bi], j, e)
                        prev, bi = bi, tgt
                        break
                    if code == _OP_RET:
                        return 0 if op[1] is None else _rd(vals, op[1])
                    if code == _OP_UNREACH:
                        raise lvm.LvmError("执行到 unreachable")
                    if code == _OP_PHI:
                        # 已经在块入口**并行**算完（见 `_body` 开头那一段）。
                        continue
                    if code == _OP_ALLOCA:
                        _, size, d = op
                        vals[d] = nxt
                        nxt += size
                        continue
                    _run_value(self, vals, op)
            else:
                # **热路径**：没有剖面时，整条链上每一步都只碰整数。
                for (j, count, _sh) in self._plan(cf, bi, blk):
                    op = blk[j]
                    if count == 2:
                        steps += 1
                        if cnt is not None:
                            cnt.instructions += 1
                            cnt.tuple_lookups += 1
                        if steps > lvm.STEP_CAP:
                            raise lvm.LvmError(
                                f"步数上限 {lvm.STEP_CAP}：{name} 的块 {labels[bi]}，"
                                f"第 {j} 条")
                        steps += 1
                        if cnt is not None:
                            cnt.instructions += 1
                            cnt.tuple_lookups += 1
                        if steps > lvm.STEP_CAP:
                            raise lvm.LvmError(
                                f"步数上限 {lvm.STEP_CAP}：{name} 的块 {labels[bi]}，"
                                f"第 {j + 1} 条")
                        # 就地展开（**不要**调 `_exec_pair`）：一个 Python 函数调用的
                        # 开销几乎等于它替掉的那条分派链，实测只剩 1.04x。见 docs/266。
                        q2 = blk[j + 1]
                        ka, kb = op[0], q2[0]
                        if ka == _OP_ARITH and kb == _OP_ARITH:
                            _, o1, t1, x1, y1, d1 = op
                            vals[d1] = lvm.arith(o1, _rd(vals, x1), _rd(vals, y1), t1)
                            _, o2, t2, x2, y2, d2 = q2
                            vals[d2] = lvm.arith(o2, _rd(vals, x2), _rd(vals, y2), t2)
                        elif ka == _OP_LOAD and kb == _OP_ARITH:
                            _, t1, x1, d1 = op
                            vals[d1] = self.load(t1, _rd(vals, x1))
                            _, o2, t2, x2, y2, d2 = q2
                            vals[d2] = lvm.arith(o2, _rd(vals, x2), _rd(vals, y2), t2)
                        elif ka == _OP_ARITH and kb == _OP_STORE:
                            _, o1, t1, x1, y1, d1 = op
                            vals[d1] = lvm.arith(o1, _rd(vals, x1), _rd(vals, y1), t1)
                            _, t2, v2, p2 = q2
                            self.store(t2, _rd(vals, p2), _rd(vals, v2))
                        else:                                  # icmp + br
                            _, fn, sg, ty, x1, y1, d1 = op
                            x, y = _rd(vals, x1), _rd(vals, y1)
                            if sg:
                                x, y = lvm.signed(x, ty), lvm.signed(y, ty)
                            vals[d1] = 1 if fn(x, y) else 0
                            _, cond, tgt, alt = q2
                            prev, bi = bi, (tgt if cond is None or _rd(vals, cond) else alt)
                            break
                        continue
                    steps += 1
                    if cnt is not None:
                        cnt.instructions += 1
                        cnt.tuple_lookups += 1
                    if steps > lvm.STEP_CAP:
                        raise lvm.LvmError(
                            f"步数上限 {lvm.STEP_CAP}：{name} 的块 {labels[bi]}，第 {j} 条")
                    code = op[0]

                    if code == _OP_ARITH:
                        _, opn, ty, a_op, b_op, d = op
                        vals[d] = lvm.arith(opn, _rd(vals, a_op), _rd(vals, b_op), ty)
                        continue
                    if code == _OP_LOAD:
                        _, ty, a_op, d = op
                        vals[d] = self.load(ty, _rd(vals, a_op))
                        continue
                    if code == _OP_STORE:
                        _, ty, v_op, a_op = op
                        self.store(ty, _rd(vals, a_op), _rd(vals, v_op))
                        continue
                    if code == _OP_ALLOCA:
                        _, size, d = op
                        vals[d] = nxt
                        nxt += size
                        continue
                    if code == _OP_GEP:
                        _, base_op, const, stages, d = op
                        off = const
                        for (i_op, size) in stages:
                            off += _rd(vals, i_op) * size
                        vals[d] = _rd(vals, base_op) + off
                        continue
                    if code == _OP_BR:
                        _, cond_op, t, f = op
                        if cond_op is None:
                            prev, bi = bi, t
                            break
                        prev, bi = bi, (t if _rd(vals, cond_op) else f)
                        break
                    if code == _OP_CALL:
                        _, cname, a_ops, d = op
                        if cname == "llvm.trap":
                            raise lvm._Trap((name, labels[bi], j))  # noqa: SLF001
                        callee = self.prog.cfunc(cname)
                        if len(a_ops) != len(callee.params):
                            raise lvm.LvmError("实参个数与形参不符")
                        v = self.call_t2(callee, [_rd(vals, o) for o in a_ops])
                        if d >= 0:
                            vals[d] = v
                        continue
                    if code == _OP_RET:
                        return 0 if op[1] is None else _rd(vals, op[1])
                    if code == _OP_SWITCH:
                        _, v_op, cases, dft = op
                        v = _rd(vals, v_op)
                        tgt = dft
                        for (cty, cval, cbi) in cases:
                            if lvm.mask(v, cty) == cval:
                                tgt = cbi
                                break
                        prev, bi = bi, tgt
                        break
                    if code == _OP_PHI:
                        # 同上：块入口已经并行算完。
                        continue
                    if code == _OP_UNREACH:
                        raise lvm.LvmError("执行到 unreachable")
                    _run_value(self, vals, op)


def _run_value(vm: T2Executor, vals: list, op: tuple) -> None:
    """**所有**值指令的兜底实现。

    记着剖面时走这一条（`prof is not None` 的那一支）：慢一点没关系，那时候要比的是
    **剖面逐字节相同**，不是墙钟。不记剖面时热路径在 `_body` 里就地展开 —— 两处必须
    语义相同，判据 B 钉的就是这件事。
    """
    code = op[0]
    if code == _OP_ERROR:
        # 编译期就解不了的一条指令（issue #715）—— 只有**执行到**这里才是一个错。
        raise lvm.LvmError(op[1])
    if code == _OP_DEAD:
        # 逻辑上走不到（`_OP_ERROR` 在它前面，或块已经 `br`/`ret` 走了）。
        raise lvm.LvmError("执行到不可达的位置")
    if code == _OP_ARITH:
        _, opn, ty, a_op, b_op, d = op
        vals[d] = lvm.arith(opn, _rd(vals, a_op), _rd(vals, b_op), ty)
    elif code == _OP_LOAD:
        _, ty, a_op, d = op
        vals[d] = vm.load(ty, _rd(vals, a_op))
    elif code == _OP_STORE:
        _, ty, v_op, a_op = op
        vm.store(ty, _rd(vals, a_op), _rd(vals, v_op))
    elif code == _OP_GEP:
        _, base_op, const, stages, d = op
        off = const
        for (i_op, size) in stages:
            off += _rd(vals, i_op) * size
        vals[d] = _rd(vals, base_op) + off
    elif code == _OP_GEP_DYN:
        # 非恒定结构体下标：静态类型链走不动，退到与 T0 共用的那一份（issue #715）。
        _, base_op, ty, i_ops, text, d = op
        vals[d] = _rd(vals, base_op) + lvm.gep_offset(
            ty, [_rd(vals, o) for o in i_ops], text)
    elif code == _OP_ICMP:
        _, fn, sg, ty, a_op, b_op, d = op
        a, b = _rd(vals, a_op), _rd(vals, b_op)
        if sg:
            a, b = lvm.signed(a, ty), lvm.signed(b, ty)
        vals[d] = 1 if fn(a, b) else 0
    elif code == _OP_DIV:
        _, kind, ty, a_op, b_op, d = op
        vals[d] = _do_div(kind, ty, _rd(vals, a_op), _rd(vals, b_op))
    elif code == _OP_CAST:
        _, kind, fty, tty, a_op, d = op
        a = _rd(vals, a_op)
        if kind == "trunc":
            vals[d] = lvm.mask(a, tty)
        elif kind == "zext":
            vals[d] = lvm.zeroext(a, fty)
        elif kind == "sext":
            vals[d] = lvm.signed(a, fty) & ((1 << (lomelf.size_of(tty) * 8)) - 1)
        else:
            vals[d] = a                             # ptrtoint / inttoptr
    elif code == _OP_EXTRACT:
        _, a_op, off, fty, d = op
        agg = _rd(vals, a_op)
        raw = agg[off:off + lomelf.size_of(fty)]
        vals[d] = raw if lvm._is_agg(fty) else int.from_bytes(raw, "little")
    elif code == _OP_INSERT:
        _, ty, a_op, b_op, off, fty, d = op
        agg = _rd(vals, a_op)
        buf = bytearray(agg if isinstance(agg, bytes) else b"\0" * lomelf.size_of(ty))
        val = _rd(vals, b_op)
        n = lomelf.size_of(fty)
        buf[off:off + n] = (val if isinstance(val, bytes)
                            else (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little"))
        vals[d] = bytes(buf)
    elif code == _OP_RMW:
        _, ty, a_op, v_op, d = op
        addr = _rd(vals, a_op)
        old = vm.load(ty, addr)
        vm.store(ty, addr, lvm.mask(old + _rd(vals, v_op), ty))
        vals[d] = old
    elif code == _OP_ASM:
        _, a_ops, d = op
        a = [_rd(vals, o) for o in a_ops]
        vals[d] = vm.syscall(a[0], a[1:])
    else:
        raise lvm.LvmError(f"T2 不认识这个 op: {op!r}")



# ---------------------------------------------------------------- A0：寄存器分配
#
# `docs/248` §3.1 的 **A0**（issue #648）：把"每个 SSA 值一个栈槽"搬进**寄存器**，
# 栈槽只在**块边界**与**守卫之前**写。这是 T3 的地基缺口 —— 槽机之下 `docs/249` §4
# 那条承重不变式（"守卫之前，不可重物化的一律落槽"）是**空成立**的，A0 一落地它才
# 真的要求写（#649）。
#
# 做法（Python 宿主的诚实版）：
#   1. 每个函数一次**活跃性分析**（块级 use/def + 不动点）⇒ 每块 live-in / live-out；
#   2. 每块一次**线性扫描**，把活跃的槽分到寄存器号；
#   3. 每块**生成一段 Python**（一次 `exec`）：值住在 `R` 里，块首从 `vals` 读
#      live-in、块尾把 live-out 写回 `vals`。于是 `vals` 只在边界被碰。
#
# ⚠ **整函数一起上或一起不上**：只要有一块含生成器不认识的 op，这个函数就整份退回
# 槽机（`rplan = False`）。混着跑会让"块边界落槽"从一条不变式变成一堆特例。

class _RBlock:
    __slots__ = ("nregs", "lin", "lout", "fn", "source", "guard_spills")

    def __init__(self, nregs, lin, lout, fn, source, guard_spills=None) -> None:
        self.nregs = nregs
        #: `[(槽, 寄存器)]`：块首从 `vals` 读进来
        self.lin = lin
        #: `[(寄存器, 槽)]`：块尾写回 `vals`
        self.lout = lout
        self.fn = fn
        #: 生成的 Python 源码 —— 判据直接读它（"守卫之前有没有落槽"要**看得见**）
        self.source = source
        #: 守卫站点 -> 守卫之前落槽的槽号表（#649 的判据读它）
        self.guard_spills = guard_spills or {}


def _slots_of(op) -> list[int]:
    """这条指令**读**了哪些槽（kind 0 的操作数）。"""
    c = op[0]
    if c == _OP_ARITH:
        return [x[1] for x in (op[3], op[4]) if x[0] == 0]
    if c == _OP_LOAD:
        return [op[2][1]] if op[2][0] == 0 else []
    if c == _OP_STORE:
        return [x[1] for x in (op[2], op[3]) if x[0] == 0]
    if c == _OP_ALLOCA:
        return []
    if c == _OP_GEP:
        out = [op[1][1]] if op[1][0] == 0 else []
        out += [io_[1] for (io_, _sz) in op[3] if io_[0] == 0]
        return out
    if c == _OP_GEP_DYN:
        out = [op[1][1]] if op[1][0] == 0 else []
        out += [o[1] for o in op[3] if o[0] == 0]
        return out
    if c == _OP_ICMP:
        return [x[1] for x in (op[4], op[5]) if x[0] == 0]
    if c == _OP_DIV:
        return [x[1] for x in (op[3], op[4]) if x[0] == 0]
    if c == _OP_CAST:
        return [op[4][1]] if op[4][0] == 0 else []
    if c == _OP_EXTRACT:
        return [op[1][1]] if op[1][0] == 0 else []
    if c == _OP_INSERT:
        return [x[1] for x in (op[2], op[3]) if x[0] == 0]
    if c == _OP_RMW:
        return [x[1] for x in (op[2], op[3]) if x[0] == 0]
    if c == _OP_CALL:
        return [o[1] for o in op[2] if o[0] == 0]
    if c == _OP_ASM:
        return [o[1] for o in op[1] if o[0] == 0]
    if c == _OP_PHI:
        return [v[1] for v in op[2].values() if v[0] == 0]
    if c == _OP_BR:
        return [op[1][1]] if op[1] is not None and op[1][0] == 0 else []
    if c == _OP_SWITCH:
        return [op[1][1]] if op[1][0] == 0 else []
    if c == _OP_RET:
        return [op[1][1]] if op[1] is not None and op[1][0] == 0 else []
    # ---- T3 的三条（`tools/lvm_t3.py`）。编号是那边写死的 101/102/103，这里只认形状：
    # 摊平段里的 op 也算**读**，否则它们的来源槽会被分配漏掉。
    if c == 102:                                    # _OP_COPY
        return [op[2][1]] if op[2][0] == 0 else []
    if c == 103:                                    # _OP_GUARDED_CALL
        out = [op[1][1]] if op[1][0] == 0 else []
        for o in op[4]:
            out += _slots_of(o)
        return out
    return []


def _dest_of(op) -> int:
    """这条指令**写**哪个槽（-1 = 不写）。"""
    c = op[0]
    if c in (_OP_ARITH, _OP_LOAD, _OP_GEP, _OP_GEP_DYN, _OP_ICMP, _OP_DIV,
             _OP_CAST, _OP_EXTRACT, _OP_INSERT, _OP_RMW, _OP_ASM, _OP_ALLOCA):
        return op[-1]
    if c == _OP_CALL:
        return op[3]
    if c == _OP_PHI:
        return op[1]
    if c == 101:                                    # _OP_STORE_CONST
        return op[1]
    if c == 102:                                    # _OP_COPY
        return op[1]
    if c == 103:                                    # _OP_GUARDED_CALL
        last = op[4][-1] if op[4] else None
        return last[1] if last is not None and last[0] in (101, 102) else -1
    return -1


def _succs(blk) -> list[int]:
    if not blk:
        return []
    op = blk[-1]
    if op[0] == _OP_BR:
        return [op[2]] if op[1] is None else [op[2], op[3]]
    if op[0] == _OP_SWITCH:
        return [b for (_t, _v, b) in op[2]] + [op[3]]
    return []


def _live(cf: "_CFunc") -> tuple[list[set], list[set]]:
    """块级活跃性（use/def + 不动点）。**只算到终结指令** —— 之后的 `_OP_DEAD` 不算。"""
    nb = len(cf.blocks)
    use = [set() for _ in range(nb)]
    defs = [set() for _ in range(nb)]
    for bi, blk in enumerate(cf.blocks):
        seen_def: set[int] = set()
        for op in blk:
            for s in _slots_of(op):
                if s not in seen_def:
                    use[bi].add(s)
            d = _dest_of(op)
            if d >= 0:
                seen_def.add(d)
                defs[bi].add(d)
            if op[0] in (_OP_BR, _OP_RET, _OP_SWITCH, _OP_UNREACH):
                break
    #: **入边上的 phi 操作数**：块 B 的 phi 从 P 进来时读的那个值，在 P 的出口是活的 ——
    #: 但它不是 B 里"被读"（B 一开始就把它定义了），所以 `use` 抓不到它。少了这一条，
    #: 前驱就不会把它写回 `vals`，目标块去优化/重入时读到的是上一轮残留的值
    #: （`free` 的空闲链就是这么崩的：`%prev = phi [0,%link],[%cur,%lcont]`）。
    edge = [set() for _ in range(nb)]
    for bi, blk in enumerate(cf.blocks):
        for op in blk:
            if op[0] != _OP_PHI:
                break
            for (pred, val) in op[2].items():
                if val[0] == 0 and 0 <= pred < nb:
                    edge[pred].add(val[1])
    lin = [set() for _ in range(nb)]
    lout = [set() for _ in range(nb)]
    changed = True
    while changed:
        changed = False
        for bi in range(nb - 1, -1, -1):
            out: set[int] = set(edge[bi])
            for s in _succs(cf.blocks[bi]):
                out |= lin[s]
            new = use[bi] | (out - defs[bi])
            if new != lin[bi] or out != lout[bi]:
                lin[bi], lout[bi] = new, out
                changed = True
    return lin, lout


def _must(cf) -> list[set]:
    """每块入口**一定已经写过**的槽（正向 must 分析，∩ 过前驱）。

    只为一件事服务：**省掉"读未定义的值"那两行**。可能没写的槽仍然逐次检查 —— 那是
    T0 的可见行为（读到才报），不能因为"大多数情况下写过"就把它变成"块首统一报"。
    """
    nb = len(cf.blocks)
    preds: list[list[int]] = [[] for _ in range(nb)]
    for bi, blk in enumerate(cf.blocks):
        for s in _succs(blk):
            if bi not in preds[s]:
                preds[s].append(bi)
    defs = []
    for blk in cf.blocks:
        d = set()
        for op in blk:
            x = _dest_of(op)
            if x >= 0:
                d.add(x)
        defs.append(d)
    top = set(range(cf.nslots))
    must_in = [set(top) for _ in range(nb)]
    must_in[0] = set(cf.params)
    changed = True
    while changed:
        changed = False
        for bi in range(nb):
            if bi == 0:
                new = set(cf.params)
            elif not preds[bi]:
                new = set(top)
            else:
                new = set(top)
                for p in preds[bi]:
                    new &= must_in[p] | defs[p]
            if new != must_in[bi]:
                must_in[bi] = new
                changed = True
    return must_in


def _assign(lin: set, defs_use: list[tuple[int, int, int]], nops: int,
            live_out: set) -> dict[int, int]:
    """线性扫描：槽 -> 寄存器号。`defs_use` = `[(槽, 首, 尾)]`。"""
    starts = []
    for s in lin:
        starts.append((0, s))
    for (s, a, _b) in defs_use:
        if s not in lin:
            starts.append((a, s))
    end_of = {s: b for (s, _a, b) in defs_use}
    for s in live_out:
        end_of[s] = nops + 1
    intervals = []
    for s in set(list(lin) + [s for (s, _a, _b) in defs_use] + list(live_out)):
        a = 0 if s in lin else min([x for (t, x, _y) in defs_use if t == s] or [0])
        b = end_of.get(s, nops)
        intervals.append((a, b, s))
    intervals.sort()
    active: list[tuple[int, int]] = []          # (尾, 寄存器)
    free: list[int] = []
    rmap: dict[int, int] = {}
    nxt = 0
    for (a, b, s) in intervals:
        active = [(e, r) for (e, r) in active if e >= a]
        used = {r for (_e, r) in active}
        free = [r for r in range(nxt) if r not in used]
        r = free[0] if free else nxt
        if r == nxt:
            nxt += 1
        rmap[s] = r
        active.append((b, r))
    return rmap

#: 生成器**不认识**的 op ⇒ 整个函数退回槽机。`ERROR`/`DEAD` 只出现在坏 IR 或死代码里，
#: 它们的行为（走到才报错）由槽机那条路原样保留。
_GEN_UNSUPPORTED = frozenset((_OP_ERROR, _OP_DEAD))


class _Gen:
    """一个块的源码生成器。**只拼字符串**，编译交给 `_plan_regs` 一次 `exec`。"""

    def __init__(self, cf, bi: int, rmap: dict, inline_mem: bool = True) -> None:
        self.cf, self.bi, self.rmap = cf, bi, rmap
        #: 把 `load`/`store` 摊进生成代码（省掉一次方法查找 + 一次 `_is_agg`）。
        #: 只在执行体**没有**覆盖它们时开 —— 覆盖了还摊，就是绕过了覆盖。
        self.inline_mem = inline_mem
        self.lines: list[str] = []
        self.tmp = 0
        #: `read()` 生成的行按这个缩进走 —— phi 的嵌套分支里比块体深一层。
        self.indent = 4
        #: 这一刻**一定已经写过**的槽（`_must` 的块入口集合 + 本块已发的定义）。
        self.assigned: set = set()
        #: 本块**全部**被分配了寄存器的槽（守卫之前全部落槽 —— 保守，见 #649）。
        self.all_slots: list = []
        self.fns: list = []          # ICMP 的谓词（不能写进源码）
        self.guard_spills: dict = {}
        #: 块**入口**时活着的槽（`lin`）。phi 的操作数是**入边上的值**，只有它们才在
        #: 块首的寄存器里 —— 本块自己定义的槽（同块的 phi 目标）那时还在 `vals` 里。
        self.in_slots: set = set()

    def t(self) -> str:
        self.tmp += 1
        return f"v{self.tmp}"

    def read(self, op) -> str:
        """一个操作数 -> 一个 Python 表达式（槽则先落一个临时，带未定义检查）。"""
        if op[0] == 0:
            r = self.rmap[op[1]]
            if op[1] in self.assigned:
                return f"R[{r}]"
            name = op[2] if len(op) > 2 else f"%{op[1]}"
            n = self.t()
            pad = " " * self.indent
            self.lines.append(f"{pad}{n} = R[{r}]")
            self.lines.append(f"{pad}if {n} is _UNDEF: raise lvm.LvmError('读未定义的值 {name}')")
            return n
        if op[0] == 1:
            return repr(op[1])
        n = self.t()
        self.lines.append(" " * self.indent + f"{n} = bytes({op[1]})")
        return n

    def read_entry(self, op) -> str:
        """phi 的操作数：**入边上的值**。只有 `lin` 里的槽才在块首寄存器里，其余读 `vals`。"""
        if op[0] != 0:
            return self.read(op)
        s = op[1]
        name = op[2] if len(op) > 2 else f"%{s}"
        pad = " " * self.indent
        n = self.t()
        if s in self.in_slots and s in self.rmap:
            self.lines.append(f"{pad}{n} = R[{self.rmap[s]}]")
        else:
            self.lines.append(f"{pad}{n} = vals[{s}]")
        if s not in self.assigned:
            self.lines.append(
                f"{pad}if {n} is _UNDEF: raise lvm.LvmError('读未定义的值 {name}')")
        return n

    def emit(self, op, j: int):
        """发一条指令；返回终结指令的 `return` 表达式（None = 继续下一条）。"""
        cf, name = self.cf, self.cf.name
        c = op[0]
        lab = cf.labels[self.bi]
        _d0 = _dest_of(op)
        if _d0 >= 0:
            self.assigned.add(_d0)
        self.lines.append("    steps += 1")
        self.lines.append("    if steps > cap: raise lvm.LvmError("
                          f"'步数上限 %d：{name} 的块 {lab}，第 {j} 条' % cap)")
        if c == _OP_ARITH:
            _c, opn, ty, a, b, d = op
            x, y = self.read(a), self.read(b)
            # 非移位的六条**摊开**：`arith` 那条路唯一多做的是"负移位量"检查，而它只对
            # 移位有意义。摊开省一次 Python 调用（热路径上每条算术一次）。
            if opn in ("add", "sub", "mul", "and", "or", "xor"):
                sym = {"add": "+", "sub": "-", "mul": "*", "and": "&",
                       "or": "|", "xor": "^"}[opn]
                self.lines.append(f"    R[{self.rmap[d]}] = ({x} {sym} {y}) & {_mask_int(ty)}")
            else:
                self.lines.append(f"    R[{self.rmap[d]}] = lvm.arith({opn!r}, "
                                  f"{x}, {y}, {ty!r})")
            return None
        if c == _OP_LOAD:
            _c, ty, a, d = op
            addr = self.read(a)
            n = lomelf.size_of(ty)
            if self.inline_mem:
                if lvm._is_agg(ty):
                    self.lines.append(f"    R[{self.rmap[d]}] = mem.get({addr}, {n})")
                else:
                    self.lines.append(f"    R[{self.rmap[d]}] = int.from_bytes("
                                      f"mem.get({addr}, {n}), 'little')")
            else:
                self.lines.append(f"    R[{self.rmap[d]}] = vm.load({ty!r}, {addr})")
            return None
        if c == _OP_STORE:
            _c, ty, v, a = op
            addr, val = self.read(a), self.read(v)
            n = lomelf.size_of(ty)
            if self.inline_mem:
                if lvm._is_agg(ty):
                    self.lines.append(f"    mem.put({addr}, {val} if isinstance({val}, bytes) "
                                      f"else bytes({val}))")
                else:
                    self.lines.append(f"    mem.put({addr}, (int({val}) & {_mask_int(ty)})."
                                      f"to_bytes({n}, 'little'))")
            else:
                self.lines.append(f"    vm.store({ty!r}, {addr}, {val})")
            return None
        if c == _OP_ALLOCA:
            _c, size, d = op
            self.lines.append(f"    R[{self.rmap[d]}] = nxt")
            self.lines.append(f"    nxt += {size}")
            return None
        if c == _OP_GEP:
            _c, base, const, stages, d = op
            expr = self.read(base)
            parts = [str(const)]
            for (io_, size) in stages:
                parts.append(f"({self.read(io_)} * {size})")
            self.lines.append(f"    R[{self.rmap[d]}] = {expr} + " + " + ".join(parts))
            return None
        if c == _OP_GEP_DYN:
            _c, base, ty, i_ops, text, d = op
            outs = ", ".join(self.read(o) for o in i_ops)
            self.lines.append(f"    R[{self.rmap[d]}] = {self.read(base)} + "
                              f"lvm.gep_offset({ty!r}, [{outs}], {text!r})")
            return None
        if c == _OP_ICMP:
            _c, fn, sg, ty, a, b, d = op
            self.fns.append(fn)
            k = len(self.fns) - 1
            x, y = self.read(a), self.read(b)
            if sg:
                x, y = f"lvm.signed({x}, {ty!r})", f"lvm.signed({y}, {ty!r})"
            self.lines.append(f"    R[{self.rmap[d]}] = 1 if _F[{k}]({x}, {y}) else 0")
            return None
        if c == _OP_DIV:
            _c, kind, ty, a, b, d = op
            self.lines.append(f"    R[{self.rmap[d]}] = _div({kind!r}, {ty!r}, "
                              f"{self.read(a)}, {self.read(b)})")
            return None
        if c == _OP_CAST:
            _c, kind, fty, tty, a, d = op
            v = self.read(a)
            if kind == "trunc":
                e = f"lvm.mask({v}, {tty!r})"
            elif kind == "zext":
                e = f"lvm.zeroext({v}, {fty!r})"
            elif kind == "sext":
                e = f"(lvm.signed({v}, {fty!r}) & {_mask_int(tty)})"
            else:
                e = v
            self.lines.append(f"    R[{self.rmap[d]}] = {e}")
            return None
        if c == _OP_EXTRACT:
            _c, a, off, fty, d = op
            self.lines.append(f"    R[{self.rmap[d]}] = _extract({self.read(a)}, {off}, {fty!r})")
            return None
        if c == _OP_INSERT:
            _c, ty, a, b, off, fty, d = op
            self.lines.append(f"    R[{self.rmap[d]}] = _insert({ty!r}, {self.read(a)}, "
                              f"{self.read(b)}, {off}, {fty!r})")
            return None
        if c == _OP_RMW:
            _c, ty, a, v, d = op
            self.lines.append(f"    R[{self.rmap[d]}] = _rmw(vm, {ty!r}, "
                              f"{self.read(a)}, {self.read(v)})")
            return None
        if c == _OP_CALL:
            _c, cname, a_ops, d = op
            if cname == "llvm.trap":
                self.lines.append(f"    raise lvm._Trap(({name!r}, {lab!r}, {j}))")
                return None
            n = self.t()
            self.lines.append(f"    {n} = vm.prog.cfunc({cname!r})")
            self.lines.append(f"    if len({n}.params) != {len(a_ops)}: "
                              "raise lvm.LvmError('实参个数与形参不符')")
            args = ", ".join(self.read(o) for o in a_ops)
            if d >= 0:
                self.lines.append(f"    R[{self.rmap[d]}] = vm.call_t2({n}, [{args}])")
            else:
                self.lines.append(f"    vm.call_t2({n}, [{args}])")
            return None
        if c == _OP_ASM:
            _c, a_ops, d = op
            vals = [self.read(o) for o in a_ops]
            head = vals[0]
            rest = ", ".join(vals[1:])
            self.lines.append(f"    R[{self.rmap[d]}] = vm.syscall({head}, [{rest}])")
            return None
        if c == 101:                                     # T3：_OP_STORE_CONST
            self.lines.append(f"    R[{self.rmap[op[1]]}] = {op[2]!r}")
            return None
        if c == 102:                                     # T3：_OP_COPY
            self.lines.append(f"    R[{self.rmap[op[1]]}] = {self.read(op[2])}")
            return None
        if c == 103:                                     # T3：_OP_GUARDED_CALL
            _c, arg_op, const, cname, inline, site = op
            # **#649 的不变式，落地在这里**：守卫之前，本块所有活着的值都写回槽。
            # 去优化那条路（`_one` 通用调用 / 摊平段）只看 `vals`，所以这一串是承重的。
            # ⚠ **只落这一刻已经写过的**（`assigned`）：把本块后面才定义的槽也落下去，
            # 写进 `vals` 的是 `None`/未初始化 —— 那不是"保守"，那是**写坏帧**。
            live_now = [(sl, self.rmap[sl]) for sl in sorted(self.assigned) if sl in self.rmap]
            spill = []
            for (slot, reg) in live_now:
                self.lines.append(f"    vals[{slot}] = R[{reg}]")
                spill.append(slot)
            self.guard_spills[site] = spill
            n = self.t()
            self.lines.append(f"    {n} = vm._a0_guarded(_CF, {self.bi}, {j}, vals, nxt)")
            self.lines.append(f"    nxt = {n}")
            # 守卫那条路会把结果写进 `vals` —— 寄存器**整个重装**（块内静态可知）。
            for (slot, reg) in live_now:
                self.lines.append(f"    R[{reg}] = vals[{slot}]")
            return None
        if c == _OP_PHI:
            return None                      # 块首统一做（并行）
        if c == _OP_BR:
            _c, cond, t, f = op
            if cond is None:
                return str(t)
            return f"({t} if {self.read(cond)} else {f})"
        if c == _OP_SWITCH:
            _c, v, cases, dft = op
            n = self.read(v)
            for k, (cty, cval, cbi) in enumerate(cases):
                kw = "if" if k == 0 else "elif"
                self.lines.append(f"    {kw} lvm.mask({n}, {cty!r}) == {cval}: return {cbi}")
            return str(dft)
        if c == _OP_RET:
            v = "0" if op[1] is None else self.read(op[1])
            self.lines.append(f"    st[4] = {v}")
            return "-1"
        if c == _OP_UNREACH:
            # 非返回的终结：`raise` 之后那一行 `return -9` 是死代码，只为让生成函数
            # 在语法上有一个返回表达式（harness 只认 >= 0 的块号与 -1 的"已返回"）。
            self.lines.append("    raise lvm.LvmError('执行到 unreachable')")
            return "-9"
        return "UNSUPPORTED"

    def phi(self) -> None:
        blk = self.cf.blocks[self.bi]
        ops = []
        for op in blk:
            if op[0] != _OP_PHI:
                break
            ops.append(op)
        if not ops:
            return
        pend = []
        for (c, d, mapping) in ops:
            n = self.t()
            ks = sorted(mapping)
            self.lines.append(f"    if st[1] == " + " or st[1] == ".join(str(k) for k in ks) + ":")
            for i, k in enumerate(ks):
                kw = "if" if i == 0 else "elif"
                self.lines.append(f"        {kw} st[1] == {k}:")
                self.indent = 12
                self.lines.append(f"            {n} = {self.read_entry(mapping[k])}")
                self.indent = 4
            self.lines.append("    else:")
            self.lines.append(
                f"        raise lvm.LvmError('{self.cf.name}: phi 没有来自块 %d 的入边' % st[1])")
            pend.append((self.rmap[d], n))
        for (r, n) in pend:
            self.lines.append(f"    R[{r}] = {n}")


def _mask_int(ty: str) -> int:
    n = lomelf.size_of(ty)
    return (1 << (n * 8)) - 1


def _extract(agg, off: int, fty: str):
    raw = agg[off:off + lomelf.size_of(fty)]
    return raw if lvm._is_agg(fty) else int.from_bytes(raw, "little")


def _insert(ty: str, agg, val, off: int, fty: str) -> bytes:
    buf = bytearray(agg if isinstance(agg, bytes) else b"\0" * lomelf.size_of(ty))
    n = lomelf.size_of(fty)
    buf[off:off + n] = (val if isinstance(val, bytes)
                        else (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little"))
    return bytes(buf)


def _rmw(vm, ty: str, addr: int, val):
    old = vm.load(ty, addr)
    vm.store(ty, addr, lvm.mask(old + val, ty))
    return old


def _plan_regs(cf, inline_mem: bool = True):
    """给一个函数生成寄存器计划。返回 `[_RBlock|None, ...]` 或 `False`（整份退回槽机）。"""
    for blk in cf.blocks:
        for op in blk:
            if op[0] in _GEN_UNSUPPORTED:
                return False
    lin, lout = _live(cf)
    must = _must(cf)
    plans = []
    for bi, blk in enumerate(cf.blocks):
        first: dict = {}
        last: dict = {}
        for j, op in enumerate(blk):
            for s in _slots_of(op):
                last[s] = j
            d = _dest_of(op)
            if d >= 0 and d not in first:
                first[d] = j
        du = [(s, a, last.get(s, len(blk))) for (s, a) in first.items()]
        rmap = _assign(lin[bi], du, len(blk), lout[bi])
        nregs = (max(rmap.values()) + 1) if rmap else 0
        g = _Gen(cf, bi, rmap, inline_mem)
        g.assigned = set(must[bi])
        g.in_slots = set(lin[bi])
        # 守卫之前要落槽的**全部**寄存器（保守：不区分可重物化与否 —— 见 #649）
        g.all_slots = sorted((sl, rg) for (sl, rg) in rmap.items())
        g.phi()
        nxt_ret = None
        for j, op in enumerate(blk):
            if op[0] == _OP_PHI:
                continue
            r = g.emit(op, j)
            if r == "UNSUPPORTED":
                return False
            if r is not None:
                nxt_ret = r
                break
        if nxt_ret is None:
            return False
        src = ["def _b(vm, R, st, vals):",
               "    cap = lvm.STEP_CAP",
               "    steps = st[3]",
               "    nxt = st[2]",
               "    mem = vm.mem"] + g.lines + [
               "    st[2] = nxt",
               "    st[3] = steps",
               f"    return {nxt_ret}"]
        source = "\n".join(src)
        gns = {"lvm": lvm, "lomelf": lomelf, "_UNDEF": _UNDEF, "_F": g.fns,
               "_div": _do_div, "_extract": _extract, "_insert": _insert, "_rmw": _rmw,
               "_CF": cf}
        exec(compile(source, f"<lvm-a0 {cf.name}#{bi}>", "exec"), gns)   # noqa: S102
        plans.append(_RBlock(nregs, [(s, rmap[s]) for s in lin[bi]],
                             [(rmap[s], s) for s in lout[bi]],
                             gns["_b"], source, g.guard_spills))
    return plans


# ---------------------------------------------------------------- 前门
def run_ir_t2(text: str, out, profile=None, argv: list[str] | None = None,
              values=None, fuse: bool = True,
              fusion: "FusionReport | None" = None,
              hot: set[tuple[str, str]] | None = None,
              counters: "lvm.Counters | None" = None) -> int:
    """IR 文本 -> **编译一次** -> 跑完 -> 退出码。与 `lvm.run_ir` 同一个契约。

    `fuse` 是 A4（`docs/266`，issue #647）：默认开 —— 它只合并纯值指令，没有假设、
    没有守卫，判据是"语义逐字节相同"。`fusion` 给一份报告对象时才记站点（关着不写）；
    `hot` 限制只在剖面点过名的块里融合。

    入口形状按 `docs/243` §3.5 认（#1026）：`_start` / `command_main` 支持，
    中断 / 线程入口点名拒 —— 与 T0 同一套判断。
    """
    shape = lvm.entry_kind(text)
    if shape is None:
        raise lvm.LvmError(lvm.NO_ENTRY)
    kind, name = shape
    if kind in ("interrupt", "thread_spawn"):
        raise lvm.LvmError(lvm.entry_refusal(kind, name))
    globals_, funcs, externs = lomelf.parse_ll(text)
    if profile is not None:
        profile.bind(funcs)
    if values is not None:
        values.bind(funcs)                  # 没有它 `text()` 不知道按什么顺序输出
    vm = T2Executor(globals_, funcs, externs, out, profile, argv, values,
                    fuse, fusion, hot, counters)
    if fusion is not None:
        # 报告要覆盖**整份程序**：懒编译只编走到的那几个，报告会缺块。编译全部是安全的
        # —— 编不出来的指令变成 `_OP_ERROR`，只有执行到才抛（与 T0 的接受集合一致）。
        # 计划也要在这里**先算一遍**：记剖面时的执行路径不融合（判据 B），不会顺手
        # 算计划，报告就会是空的。
        # ⚠ 循环变量**不许叫 `name`** —— 它是入口名，会被这里覆盖掉，于是下一行
        # `cfunc(name)` 调用的是"最后一个被定义的函数"（#1026 落地时踩过：`tour`
        # 的剖面只剩 `max_of_u32` 一个块）。
        for fname in list(vm.prog.funcs):
            cf = vm.prog.cfunc(fname)
            if cf.blocks:
                vm._plan(cf, 0, cf.blocks[0])
    args = lvm.entry_values(vm, kind, name)
    start = vm.prog.cfunc(name)
    try:
        ret = vm.call_t2(start, args, entry=True)
    except lvm._Exit as e:                                  # noqa: SLF001
        return e.code
    except lvm._Trap as e:                                  # noqa: SLF001
        return lvm.trap_exit(e.site)
    return 0 if kind == "_start" else ret & 0xFF
