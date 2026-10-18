#!/usr/bin/env python3
"""lvm_t3.py — LVM 第四格（T3）：**优化编译 + 去优化**（`docs/232` §4.4，issue 见 #326/#327）。

## 这一格只有一条硬判据

`docs/232` §4.4：**去优化之后，结果与原生产物相同**。别的都是"更快"，那不是正确性陈述。
（`docs/249` 整篇说的就是这件事。）

## 实装的是 B 堆的哪一条

`docs/248` §3.2 的 **B1：常量特化** —— 唯一"真赌了"的一条，也是唯一需要去优化的。
做法：某个**调用点**的剖面里，第一个实参**一直是 C**（占比 ≥ 阈值），就

1. 把被调函数（**单块、无分支、无调用**）**内联**进来，参数按 C 绑死；
2. 前面放一道**守卫**：`实参 != C ⇒ 去优化`；
3. 守卫不成立时走原来那条通用调用，**记一条去优化**，并继续用解释器的状态往下跑。

⚠ **这里"去优化"便宜得几乎不像话，而原因是写实的**：T2/T3 是**槽机** —— 每个值都已经在
它自己的槽里，没有寄存器。`docs/249` §4 那条承重不变式（"守卫之前，不可重物化的值必须已经
在栈槽里"）在这里**空成立**。真正的难点（从优化帧重建解释器状态）是**寄存器分配**的代价，
而这一格没做寄存器分配。**这不是"我们做对了"，是"这一格的地基让这个问题不存在"** ——
哪天 T3 真上了寄存器分配，`docs/249` §3 那四档重建就要真写。

## A 堆一条都没做（天花板写在纸上）

`docs/248` §3.1 的 A0（寄存器分配）/ A1（一般内联）/ A2（热块布局）/ A3（跳转表）/
A4（分派消除）**都没做**。本格只交 B1 —— 因为 B1 是唯一能证明去优化这条路的，
而 `docs/248` §3.2 也写了：没有 B1，T3 就永远不会需要去优化，那时该问的是"这还是不是 T3"。

## 值从哪儿来

剖面（`docs/247`）**不记值** —— `docs/248` §3.2 说 B1 要值，而 `docs/247` §1 说类型不记。
所以值走**另一件产物**（`--values-out` / `--values-in`）：

```
# lvm values v1
[values]
<fn>\t<block>\t<instr>\t<value>\t<count>
```

⚠ `docs/248` §2 原本写的是"给剖面加一个段（版本号 +1）"。**落地时改成独立产物**，理由是硬的：
孪生判据（`docs/247` 判据 E）逐字节比**剖面**，而镜像不记值 ⇒ 加进剖面会让孪生当场分叉。
独立的另一件产物不动那个契约。
"""

from __future__ import annotations

import io

import lomelf
import lvm
import lvm_t2

#: 一个调用点的第一个实参"一直取同一个值"的门槛。占比低于它就**不特化** ——
#: 猜得越陡，守卫响得越频繁，而守卫响一次的代价比省下来的那点大。
DOMINANT_SHARE = 0.90

#: T3 才有的三条 op（T2 不认识它们 —— 编号从 101 起，离 T2 那 1..18 远着）。
_OP_STORE_CONST = 101        # (code, slot, const)
_OP_COPY = 102               # (code, slot, operand)
_OP_GUARDED_CALL = 103       # (code, arg_operand, const, callee_name, inline_ops, site)

#: 去优化的原因词表（封闭的 —— `docs/249` §6：自由文本没法比字节）。
DEOPT_GUARD = "guard:C"


class Values:
    """一次跑里观察到的**第一个实参**的值（`docs/248` §3.2 的 B1 要的那个量）。"""

    def __init__(self) -> None:
        self.sites: dict[tuple[str, str, int], dict[int, int]] = {}
        self.funcs: list = []

    def bind(self, funcs: list) -> None:
        self.funcs = funcs

    def observe(self, site: tuple[str, str, int], value) -> None:
        d = self.sites.get(site)
        if d is None:
            d = self.sites[site] = {}
        d[value] = d.get(value, 0) + 1

    def dominant(self, site) -> tuple[int, float] | None:
        d = self.sites.get(site)
        if not d:
            return None
        total = sum(d.values())
        v, n = max(d.items(), key=lambda kv: kv[1])
        return v, n / total

    def text(self) -> str:
        """按 IR 的解析顺序输出（与剖面同一个理由：两边/两次跑要逐字节可比）。"""
        rows = []
        for fn in self.funcs:
            for blk in fn.blocks:
                for idx in range(len(blk.instrs)):
                    site = (fn.name, blk.label, idx)
                    d = self.sites.get(site)
                    if not d:
                        continue
                    for v in sorted(d):
                        rows.append(f"{fn.name}\t{blk.label}\t{idx}\t{v}\t{d[v]}")
        return "\n".join(["# lvm values v1",
                          "# first actual argument observed at each call site",
                          "[values]"] + rows) + "\n"

    @staticmethod
    def parse(text: str) -> "Values":
        v = Values()
        sec = None
        for line in text.split("\n"):
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                sec = line.strip("[]")
                continue
            if sec != "values":
                continue
            fn, blk, idx, val, n = line.split("\t")
            site = (fn, blk, int(idx))
            v.sites.setdefault(site, {})[int(val)] = int(n)
        return v


class DeoptLog:
    """去优化的记录（`docs/249` §6）—— 与剖面同形，**关掉就一个字节都不写**。"""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str, int, str], int] = {}
        self.funcs: list = []

    def bind(self, funcs: list) -> None:
        self.funcs = funcs

    def hit(self, site, reason: str) -> None:
        k = (site[0], site[1], site[2], reason)
        self.hits[k] = self.hits.get(k, 0) + 1

    def text(self) -> str:
        rows = []
        for fn in self.funcs:
            for blk in fn.blocks:
                for idx in range(len(blk.instrs)):
                    site = (fn.name, blk.label, idx)
                    for r in sorted(k[3] for k in self.hits if k[:3] == site):
                        rows.append(f"{fn.name}\t{blk.label}\t{idx}\t{r}\t"
                                    f"{self.hits[(fn.name, blk.label, idx, r)]}")
        return "\n".join(["# lvm deopt v1",
                          "# a deoptimisation is a guard that was wrong to have been assumed",
                          "[deopts]"] + rows) + "\n"


# ---------------------------------------------------------------- 特化
def _remap_op(op: tuple, smap: dict[int, int]) -> tuple:
    """把一个 op 里的槽下标按 `smap` 换掉。**逐条对齐 `lvm_t2` 的 op 形状**。"""
    code = op[0]

    def o(x):                                   # 操作数
        return (0, smap[x[1]], x[2]) if x[0] == 0 else x

    def slot(i):                                # dest 一类（-1 = 没有）
        return smap.get(i, i) if i >= 0 else i

    if code == lvm_t2._OP_ALLOCA:               # noqa: SLF001
        return (code, op[1], slot(op[2]))
    if code == lvm_t2._OP_LOAD:                 # noqa: SLF001
        return (code, op[1], o(op[2]), slot(op[3]))
    if code == lvm_t2._OP_STORE:                # noqa: SLF001
        return (code, op[1], o(op[2]), o(op[3]))
    if code == lvm_t2._OP_GEP:                  # noqa: SLF001
        return (code, o(op[1]), op[2], [(o(a), b) for (a, b) in op[3]], slot(op[4]))
    if code == lvm_t2._OP_EXTRACT:              # noqa: SLF001
        return (code, o(op[1]), op[2], op[3], slot(op[4]))
    if code == lvm_t2._OP_INSERT:               # noqa: SLF001
        return (code, op[1], o(op[2]), o(op[3]), op[4], op[5], slot(op[6]))
    if code == lvm_t2._OP_ICMP:                 # noqa: SLF001
        return (code, op[1], op[2], op[3], o(op[4]), o(op[5]), slot(op[6]))
    if code == lvm_t2._OP_DIV:                  # noqa: SLF001
        return (code, op[1], op[2], o(op[3]), o(op[4]), slot(op[5]))
    if code == lvm_t2._OP_ARITH:                # noqa: SLF001
        return (code, op[1], op[2], op[3], o(op[4]), o(op[5]), slot(op[6]))
    if code == lvm_t2._OP_CAST:                 # noqa: SLF001
        return (code, op[1], op[2], op[3], o(op[4]), slot(op[5]))
    if code == lvm_t2._OP_CALL:                 # noqa: SLF001
        return (code, op[1], [o(a) for a in op[2]], slot(op[3]))
    if code == lvm_t2._OP_ASM:                  # noqa: SLF001
        return (code, [o(a) for a in op[1]], slot(op[2]))
    if code == lvm_t2._OP_RMW:                  # noqa: SLF001
        return (code, op[1], o(op[2]), o(op[3]), slot(op[4]))
    if code == lvm_t2._OP_PHI:                  # noqa: SLF001
        return (code, slot(op[1]), {b: o(x) for b, x in op[2].items()})
    if code == lvm_t2._OP_RET:                  # noqa: SLF001
        return (code, o(op[1]) if op[1] is not None else None)
    raise lvm.LvmError(f"T3 不会重映射这个 op: {op!r}")


def _inlinable(cf) -> bool:
    """单块、最后一条是 `ret`、里面**没有调用** —— 满足这三条才能直接摊平进来。"""
    if len(cf.blocks) != 1:
        return False
    blk = cf.blocks[0]
    if not blk or blk[-1][0] != lvm_t2._OP_RET:               # noqa: SLF001
        return False
    # ⚠ 已经特化过的被调**不许再内联**：它里面已经有 T3 那三条 op，而 `_remap_op`
    # 不认识它们 —— 第一版就是这么炸的（`lumtui_off_nodes` 先被特化，又被摊进
    # `lumtui_nrec`）。**顺序**在这里会决定成败，所以直接按"形状"拒，不靠顺序。
    bad = (lvm_t2._OP_CALL, lvm_t2._OP_BR, lvm_t2._OP_SWITCH,   # noqa: SLF001
           lvm_t2._OP_PHI, _OP_GUARDED_CALL, _OP_STORE_CONST,   # noqa: SLF001
           _OP_COPY)
    return all(op[0] not in bad for op in blk)


def specialise(prog, values: Values) -> list[tuple]:
    """按值观察给调用点加守卫特化。返回被特化的站点列表（判据要它）。"""
    done = []
    for fname in list(prog.funcs):          # `prog.funcs` 是 name -> Func，迭代拿到的是名字
        cf = prog.cfuncs.get(fname)
        if cf is None:
            continue                            # 还没被调用过 ⇒ 还没编译 ⇒ 没有站点可特化
        for bi, blk in enumerate(cf.blocks):
            for j, op in enumerate(blk):
                if op[0] != lvm_t2._OP_CALL:                  # noqa: SLF001
                    continue
                _, cname, a_ops, dest = op
                if dest < 0 or not a_ops:
                    continue
                site = (fname, cf.labels[bi], j)
                dom = values.dominant(site)
                if dom is None or dom[1] < DOMINANT_SHARE:
                    continue
                const = dom[0]
                callee = prog.cfunc(cname)
                if not _inlinable(callee):
                    continue
                base = cf.nslots
                smap = {i: base + i for i in range(callee.nslots)}
                cf.nslots += callee.nslots
                body = [_remap_op(o2, smap) for o2 in callee.blocks[0][:-1]]
                ret_op = callee.blocks[0][-1]
                # 参数绑成常数：写进**第一个**形参槽，其余照抄实参。
                prelude = [(_OP_STORE_CONST, smap[callee.params[0]], const)]  # noqa: SLF001
                for k, p in enumerate(callee.params[1:], start=1):
                    if k < len(a_ops):
                        prelude.append((_OP_COPY, smap[p], a_ops[k]))  # noqa: SLF001
                fin = [(_OP_COPY, dest,                          # noqa: SLF001
                        _remap_op((lvm_t2._OP_RET, ret_op[1]), smap)[1])    # noqa: SLF001
                       if ret_op[1] is not None else (_OP_STORE_CONST, dest, 0)]  # noqa: SLF001
                blk[j] = (_OP_GUARDED_CALL, a_ops[0], const, cname,  # noqa: SLF001
                          prelude + body + fin, site)
                done.append(site)
    return done


# ---------------------------------------------------------------- 执行期
class T3Executor(lvm_t2.T2Executor):
    """T2 的执行体 + 两件新 op：守卫特化的调用、常数/拷贝搬运。"""

    def __init__(self, *a, deopts: DeoptLog | None = None, **kw) -> None:
        super().__init__(*a, **kw)
        self.deopts = deopts

    def _body(self, cf, vals, base):                             # noqa: C901
        prof = self.profile
        blocks, labels, name = cf.blocks, cf.labels, cf.name
        frame = [base]                       # 帧的 bump 游标（`alloca` 每执行一次就往上走）
        bi, prev = 0, -1
        steps = 0
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

                if code == _OP_GUARDED_CALL:              # noqa: SLF001
                    _, arg_op, const, cname, inline, site = op
                    if prof is not None:
                        prof.call_site(site, cname)
                    if _rd(vals, arg_op) == const:
                        # 假设成立：摊平的那段直接跑（**没有**一次真调用）。
                        # ⚠ 帧基址要**照抄真调用会给它的那一个**（`STACK_TOP - (depth+1)*FRAME_SIZE`）：
                        # 被调里的 `alloca` 每执行一次就往上走，而地址是**可观察的**。第一版直接
                        # 用调用者的 bump 游标，于是 1000 轮循环把帧撑过栈顶、写越界 ——
                        # 内联把"每次调用一块新帧"这件事弄丢了（`docs/249` §2）。
                        cframe = [lvm.STACK_TOP - (self.depth + 1) * lvm.FRAME_SIZE]
                        for o2 in inline:
                            self._one(vals, o2, cframe)
                    else:
                        # 假设不成立：记一条，走通用调用（**副作用只发生一次** ——
                        # 摊平的那段还没开始跑，谈不上"重跑"）
                        if self.deopts is not None:
                            self.deopts.hit(site, DEOPT_GUARD)
                        self._one(vals, (lvm_t2._OP_CALL, cname,   # noqa: SLF001
                                         [arg_op], inline_dest(inline)), frame)
                    continue
                if code == _OP_STORE_CONST:
                    vals[op[1]] = op[2]
                    continue
                if code == _OP_COPY:
                    vals[op[1]] = _rd(vals, op[2])
                    continue
                if code == lvm_t2._OP_ALLOCA:             # noqa: SLF001
                    _, size, d = op
                    vals[d] = frame[0]
                    frame[0] += size
                    continue
                self._one(vals, op, frame)
                if code == lvm_t2._OP_BR:                        # noqa: SLF001
                    prev, bi = bi, (op[2] if op[1] is None else
                                    (op[2] if _rd(vals, op[1]) else op[3]))
                    break
                if code == lvm_t2._OP_RET:                       # noqa: SLF001
                    return 0 if op[1] is None else _rd(vals, op[1])
                if code == lvm_t2._OP_SWITCH:                    # noqa: SLF001
                    v = _rd(vals, op[1])
                    tgt = op[3]
                    for (cty, cval, cbi) in op[2]:
                        if lvm.mask(v, cty) == cval:
                            tgt = cbi
                            break
                    prev, bi = bi, tgt
                    break
                if code == lvm_t2._OP_PHI:                       # noqa: SLF001
                    src = op[2].get(prev)
                    if src is None:
                        raise lvm.LvmError(f"{name}: phi 没有来自块 {prev} 的入边")
                    if op[1] >= 0:
                        vals[op[1]] = _rd(vals, src)
                    continue

    def _one(self, vals, op, frame) -> None:
        """一条**普通** op（调用 / 算术 / 内存 / 系统调用…）—— 与 T2 同一套语义。"""
        code = op[0]
        if code == _OP_STORE_CONST:          # 摊平进来的那段里也有这两条
            vals[op[1]] = op[2]
        elif code == _OP_COPY:
            vals[op[1]] = _rd(vals, op[2])
        elif code == lvm_t2._OP_ALLOCA:                          # noqa: SLF001
            _, size, d = op
            vals[d] = frame[0]
            frame[0] += size
        elif code == lvm_t2._OP_CALL:                            # noqa: SLF001
            _, cname, a_ops, d = op
            args = [_rd(vals, o) for o in a_ops]
            v = self.call_t2(self.prog.cfunc(cname), args)
            if d >= 0:
                vals[d] = v
        elif code == lvm_t2._OP_RET:                             # noqa: SLF001
            pass
        elif code in (lvm_t2._OP_BR, lvm_t2._OP_SWITCH,          # noqa: SLF001
                      lvm_t2._OP_PHI):                           # noqa: SLF001
            pass
        else:
            lvm_t2._run_value(self, vals, op)                    # noqa: SLF001


def inline_dest(op) -> int:
    """摊平那段最后一条 `_OP_COPY` 的目标槽 —— 去优化时结果也要落到同一个地方。"""
    last = op[-1]
    return last[1] if last[0] in (_OP_COPY, _OP_STORE_CONST) else -1  # noqa: SLF001


def _rd(vals, op):
    return lvm_t2._rd(vals, op)                                  # noqa: SLF001


# ---------------------------------------------------------------- 前门
def run_ir_t3(text: str, out, profile=None, argv: list[str] | None = None,
              values: Values | None = None, deopts: DeoptLog | None = None) -> int:
    globals_, funcs, externs = lomelf.parse_ll(text)
    if profile is not None:
        profile.bind(funcs)
    if deopts is not None:
        deopts.bind(funcs)
    vm = T3Executor(globals_, funcs, externs, out, profile, argv, deopts=deopts)
    if values is not None:
        # ⚠ 编译是**懒**的（T2 §3）：此刻 `prog.cfuncs` 还是空的。要把**有观察的那些函数**
        # 先编出来，否则 `specialise` 一个站点都扫不到 —— 而它不会报错，只会静默不特化
        # （第一版就是这样：答案全对、去优化记录空着，判据 B 的"必须真去优化过"当场红）。
        for (_fname, _b, _i) in list(values.sites):
            vm.prog.cfunc(_fname)
        specialise(vm.prog, values)
    if "_start" not in vm.prog.funcs:
        raise lvm.LvmError("这份 IR 里没有 `_start`")
    try:
        vm.call_t2(vm.prog.cfunc("_start"), [], entry=True)
    except lvm._Exit as e:                                      # noqa: SLF001
        return e.code
    return 0
