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

    __slots__ = ("name", "nslots", "params", "blocks", "labels")

    def __init__(self, name: str) -> None:
        self.name = name
        self.nslots = 0
        self.params: list[int] = []
        self.blocks: list[list[tuple]] = []
        self.labels: list[str] = []


class Program:
    """整份 IR 编译后的样子。**只建一次**，之后随便跑多少趟。

    ⚠ **按需编译（第一次被调用时才编）**，不是一次编完整份。理由是 T0 的行为：
    它只在**执行到**一条不认识的指令时才报错。`tour` / `all_loment` 那份 IR 里就有
    `declare void @llvm.trap()` 与它的调用点 —— 在**走不到**的函数里。一次编完整份
    会让"根本没执行到的代码"把整个程序打死，那是**换引擎顺带改了语义**。
    """

    def __init__(self, globals_: list, funcs: list, externs: list) -> None:
        # ⚠ 调用方必须**已经**把全局摆好了（`Global.addr` 有值），否则下面的 `enc`
        # 会把每个 `@global` 编成地址 0 —— 见模块开头那段。
        self.globals = {g.name: g for g in globals_}
        self.funcs = {f.name: f for f in funcs}
        self.externs = set(externs)
        self.cfuncs: dict[str, _CFunc] = {}

    def cfunc(self, name: str) -> "_CFunc":
        """取一个函数编译后的样子；没编过就现在编。错误消息**与 T0 逐字一致**。"""
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
                 profile=None, argv: list[str] | None = None, values=None) -> None:
        super().__init__(globals_, funcs, externs, out, profile, argv)
        #: T3 的**值观察**（`docs/248` §3.2 的 B1 要"值"，而剖面只记次数）。
        #: 只在开着剖面时才记（那一条路本来就有指令下标 `j`）—— 见 `_OP_CALL` 那一支。
        self.values = values
        # ⚠ **必须在 `super().__init__` 之后**：那里才调 `_place_globals` 给
        # `Global.addr` 赋值，而编译期要拿那个地址去编每个 `@global` 操作数。
        self.prog = Program(globals_, funcs, externs)

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
            return self._body(cf, vals, base)
        finally:
            self.depth -= 1

    def _body(self, cf: _CFunc, vals: list, base: int):       # noqa: C901
        prof = self.profile
        blocks, labels, name = cf.blocks, cf.labels, cf.name
        nxt = base
        bi, prev = 0, -1
        steps = 0                       # **整个调用**共用一个计数（跨块也算）—— 与镜像同口径
        while True:
            blk = blocks[bi]
            if prof is not None:
                prof.block(name, labels[bi])
                for j, op in enumerate(blk):
                    steps += 1
                    if steps > lvm.STEP_CAP:
                        raise lvm.LvmError(
                            f"步数上限 {lvm.STEP_CAP}：{name} 的块 {labels[bi]}，第 {j} 条")
                    code = op[0]

                    if code == _OP_CALL:
                        _, cname, a_ops, d = op
                        cf = self.prog.cfunc(cname)
                        # issue #716：**先判个数、再求值实参** —— 多出来的那个实参若是
                        # 未定义的值，T0 报的是"实参个数与形参不符"，不是"读未定义的值"。
                        if len(a_ops) != len(cf.params):
                            raise lvm.LvmError("实参个数与形参不符")
                        args = [_rd(vals, o) for o in a_ops]
                        prof.call_site((name, labels[bi], j), cname)
                        if self.values is not None and args:
                            self.values.observe((name, labels[bi], j), args[0])
                        v = self.call_t2(cf, args)
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
                        _, d, mapping = op
                        src = mapping.get(prev)
                        if src is None:
                            raise lvm.LvmError(f"{name}: phi 没有来自块 {prev} 的入边")
                        if d >= 0:
                            vals[d] = _rd(vals, src)
                        continue
                    if code == _OP_ALLOCA:
                        _, size, d = op
                        vals[d] = nxt
                        nxt += size
                        continue
                    _run_value(self, vals, op)
            else:
                # **热路径**：没有剖面时，整条链上每一步都只碰整数。
                for j, op in enumerate(blk):
                    steps += 1
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
                        cf = self.prog.cfunc(cname)
                        if len(a_ops) != len(cf.params):
                            raise lvm.LvmError("实参个数与形参不符")
                        v = self.call_t2(cf, [_rd(vals, o) for o in a_ops])
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
                        _, d, mapping = op
                        src = mapping.get(prev)
                        if src is None:
                            raise lvm.LvmError(f"{name}: phi 没有来自块 {prev} 的入边")
                        if d >= 0:
                            vals[d] = _rd(vals, src)
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


# ---------------------------------------------------------------- 前门
def run_ir_t2(text: str, out, profile=None, argv: list[str] | None = None,
              values=None) -> int:
    """IR 文本 -> **编译一次** -> 跑完 -> 退出码。与 `lvm.run_ir` 同一个契约。"""
    globals_, funcs, externs = lomelf.parse_ll(text)
    if profile is not None:
        profile.bind(funcs)
    if values is not None:
        values.bind(funcs)                  # 没有它 `text()` 不知道按什么顺序输出
    vm = T2Executor(globals_, funcs, externs, out, profile, argv, values)
    if "_start" not in vm.prog.funcs:
        raise lvm.LvmError("这份 IR 里没有 `_start`")
    start = vm.prog.cfunc("_start")
    try:
        vm.call_t2(start, [], entry=True)
    except lvm._Exit as e:                                  # noqa: SLF001
        return e.code
    return 0
