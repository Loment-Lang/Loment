#!/usr/bin/env python3
"""loment_spec_oracle_test.py — 原生面（IR 后端 + lomelf）的**独立**判据（#772）。

## 它补的是哪一格

仓里现有的每一条"自举侧对不对"的判据，期望值都来自参考实现：

* `loment_p8_test::test_m83_m84_self_compile_and_fixed_point` 先断言
  `stage1(driver.lomt) == lomentc.emit_llvm(driver.lomt)`，再断言定点；
* `loment_store_twin_test` 把 `lompi/store/**` 逐个当根单元，要求两边 IR 逐字节相同。

这两条证明的是**两个实现一致**，不是**两个实现都对**。#694（整数字面量 `as i64` 在
两个实现里一起按 32 位截断）就是这类共享错误：孪生判据全绿，错的是**两边共同的那个答案**。

## 这个 oracle 是什么

一张**由语言规范推出来**的用例表（`standard/LLS.md` §2 的规范引用：`docs/144` §3 定整数
语义）。每个用例只有一份数据：

* `expr` —— 一棵小的表达式树；
* `typ` —— 表达式的类型；
* `cite` —— 规则出处。

**同一棵树上长出两样东西**：

1. `_render()` 生成喂给两个编译器的 Loment 源码；
2. `_model()` 按规范算出期望的 64 位值（回绕、有/无符号选 `sdiv`/`udiv`、
   `ashr`/`lshr`、`icmp slt/ult`、`as` 的加宽保值 / 变窄回绕）。

`_model()` **不调用** `emit_llvm`、`loment_interp` 或任何编译器代码 —— 它是这三种路径之外的
第四条路径，写成一张可执行表。两边手改一边不会漂：源码和期望值同源。

判据跑真程序：两个编译器各产出一个本机可执行文件（Windows 上是 PE，Linux 上是 ELF，
都不需要 clang / WSL），**同一个程序**把每个用例的结果按 8 字节一条写到 stdout。
期望的字节串由 `_model()` 给出。任一侧与规范不同就红；**两侧一致但与规范不同**也红，
而且会点名这是 #772 的共享错误类。

## "它真的能红吗"

`test_agreement_alone_is_not_accepted` 把 #694 的**历史错值**（`253402300800` 被截成
32 位）同时当作"参考的答案"和"自举侧的答案"喂给判定函数：两边一致、但规范不同意 ——
判定必须红，并且必须点名那个用例。这条判据是 oracle 没有退化成"比较两个实现"的证明。

运行: python tools/loment_spec_oracle_test.py
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_dist  # noqa: E402
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
M64 = (1 << 64) - 1
WIDTH = {"u8": 8, "i8": 8, "u16": 16, "i16": 16,
         "u32": 32, "i32": 32, "u64": 64, "i64": 64}

#: 整数语义的规范出处。`docs/144` §3（M0 的取舍）：
#:   * 有/无符号**按声明类型选指令** —— `icmp slt/ult`、`sdiv/udiv`、`ashr/lshr`、`srem/urem`；
#:   * **溢出按模回绕**，是定义好的行为（发射的 `add`/`sub`/`mul` 从不带 `nsw`/`nuw`）。
#: `as` 的取值规则另见 #694 的修复记录：**整数字面量 `as iN` 的值就是字面量本身**
#: （加宽不截断），窄目标按位宽回绕。
CITE = "docs/144 §3 + #694"


# ---------------------------------------------------------------- 规范模型
#
# 一个值 = (位模式, 宽度, 是否有符号)。宽度 `None` 表示**未定宽的字面量** —— 它的值是
# 精确整数，`as T` 时才会落到某个宽度上（#694 的那条规则）。

class _V:
    __slots__ = ("bits", "w", "signed")

    def __init__(self, bits: int, w: int | None, signed: bool):
        self.w = w
        self.signed = signed
        self.bits = bits if w is None else bits & ((1 << w) - 1)

    def num(self) -> int:
        """按声明符号性读出来的**数学整数**。"""
        if self.w is None:
            return self.bits
        if self.signed and self.bits >= (1 << (self.w - 1)):
            return self.bits - (1 << self.w)
        return self.bits


def _type(t: str) -> tuple[int, bool]:
    return WIDTH[t], t.startswith("i")


def _coerce(v: _V, typ: str) -> _V:
    """把未定宽的字面量落到 `typ` 上；已有类型的操作数必须本来就是这个类型。"""
    w, s = _type(typ)
    if v.w is None:
        return _V(v.bits, w, s)
    assert (v.w, v.signed) == (w, s), f"模型用例混了宽度: {v.w}/{v.signed} vs {typ}"
    return v


def _bin(op: str, a: _V, b: _V, typ: str) -> _V:
    w, s = _type(typ)
    x, y = _coerce(a, typ).num(), _coerce(b, typ).num()
    if op == "+":
        v = x + y
    elif op == "-":
        v = x - y
    elif op == "*":
        v = x * y
    elif op == "/":
        if y == 0:
            raise ZeroDivisionError("模型用例不许碰除零（陷阱语义另算）")
        q = abs(x) // abs(y)
        v = -q if (x < 0) != (y < 0) else q      # 向零截断（sdiv/udiv）
    elif op == "%":
        if y == 0:
            raise ZeroDivisionError("模型用例不许碰取模零")
        q = abs(x) // abs(y)
        q = -q if (x < 0) != (y < 0) else q
        v = x - q * y                            # 余数与被除数同号（srem/urem）
    elif op == "<<":
        v = x << y
    elif op == ">>":
        v = x >> y                               # Python 的负数右移就是算术右移
    else:
        raise ValueError(op)
    return _V(v, w, s)


def _cast(a: _V, typ: str) -> _V:
    """`as T`：未定宽字面量**保住原值**；变窄截位模式；加宽按源符号性扩展。"""
    w2, s2 = _type(typ)
    if a.w is None:
        return _V(a.bits, w2, s2)
    if w2 < a.w:
        return _V(a.bits, w2, s2)                # trunc
    return _V(a.num(), w2, s2)                   # sext / zext


def _cmp(op: str, a: _V, b: _V, typ: str) -> bool:
    x, y = _coerce(a, typ).num(), _coerce(b, typ).num()
    if op == "<":
        return x < y
    if op == ">":
        return x > y
    if op == "<=":
        return x <= y
    if op == ">=":
        return x >= y
    if op == "==":
        return x == y
    if op == "!=":
        return x != y
    raise ValueError(op)


def _model(node, typ: str):
    k = node[0]
    if k == "lit":
        return _V(node[1], None, False)
    if k == "bin":
        return _bin(node[1], _model(node[2], typ), _model(node[3], typ), typ)
    if k == "cast":
        assert node[2][0] == "lit", "本表的 cast 只包字面量（#694 的那一类）"
        return _cast(_model(node[2], node[1]), node[1])   # 字面量不读上下文类型
    if k == "cmp":
        return _cmp(node[1], _model(node[2], typ), _model(node[3], typ), typ)
    raise ValueError(k)


def _render(node, typ: str | None) -> str:
    k = node[0]
    if k == "lit":
        # **不加括号**：`((-1) as u32)` 会被参考实现拒（括号里的负字面量类型是 None）；
        # `-1 as u32` 按优先级读成 `-(1 as u32)`，位模式与"字面量 -1 落到 u32"相同。
        return str(node[1])
    if k == "bin":
        assert typ is not None, "二元表达式的类型由外层给出"
        return f"({_render(node[2], typ)} {node[1]} {_render(node[3], typ)})"
    if k == "cast":
        return f"({_render(node[2], None)} as {node[1]})"
    if k == "cmp":
        assert typ is not None
        return f"({_render(node[2], typ)} {node[1]} {_render(node[3], typ)})"
    raise ValueError(k)


# ---------------------------------------------------------------- 用例表
#
# 每条都是"规范说得出答案、而两个实现可能一起错"的一格。名字里的类别是棘轮：
# `test_the_table_covers_the_shared_error_classes` 要求这几类**在表里**。

def L(v: int):
    return ("lit", v)


def B(op: str, a, b):
    return ("bin", op, a, b)


def C(t: str, a):
    return ("cast", t, a)


def Q(op: str, a, b):
    return ("cmp", op, a, b)


@dataclass(frozen=True)
class Case:
    name: str
    typ: str
    node: object
    kind: str = "value"      # value = 表达式；bool = 条件（真 1 / 假 2）
    cite: str = CITE


CASES: list[Case] = [
    # 回绕（docs/144 §3：溢出按模回绕，不是 UB）
    Case("wrap-add-u32", "u32", B("+", L(4294967295), L(1))),
    Case("wrap-sub-u32", "u32", B("-", L(0), L(1))),
    Case("wrap-add-u8", "u8", B("+", L(255), L(1))),
    Case("wrap-mul-i32", "i32", B("*", L(2147483647), L(2))),
    Case("shl-wrap-u32", "u32", B("<<", L(3), L(31))),
    # 有/无符号选指令（sdiv/udiv、srem/urem、ashr/lshr、icmp slt/ult）
    Case("div-trunc-i32", "i32", B("/", L(-7), L(2))),
    Case("div-trunc-i32-b", "i32", B("/", L(7), L(-2))),
    Case("rem-sign-i32", "i32", B("%", L(-7), L(2))),
    Case("rem-sign-i32-b", "i32", B("%", L(7), L(-2))),
    Case("udiv-u32", "u32", B("/", L(4294967295), L(2))),
    Case("ashr-i32", "i32", B(">>", L(-8), L(1))),
    Case("lshr-u32", "u32", B(">>", L(4294967288), L(1))),
    Case("i64-sub", "i64", B("-", L(0), L(5))),
    # `as`：加宽保值（#694 的共享错误就在这一格）、变窄回绕
    Case("literal-widen-u64", "u64", C("u64", L(253402300800))),
    Case("literal-widen-i64", "i64", C("i64", L(253402300800))),
    Case("literal-narrow-u8", "u8", C("u8", L(300))),
    Case("literal-trunc-i32", "i32", C("i32", L(4294967296))),
    Case("negative-to-u32", "u32", C("u32", L(-1))),
    # 比较：符号性决定 slt / ult
    Case("signed-lt", "i32", Q("<", L(-1), L(0)), kind="bool"),
    Case("unsigned-gt", "u32", Q(">", L(4294967295), L(0)), kind="bool"),
    Case("signed-le", "i64", Q("<=", L(-9223372036854775807), L(0)), kind="bool"),
]

#: 这些类别必须在表里（只许加、不许静默删）。
REQUIRED_CLASSES = (
    "wrap-add-u32", "wrap-sub-u32", "wrap-mul-i32", "wrap-add-u8",
    "div-trunc-i32", "rem-sign-i32", "udiv-u32",
    "ashr-i32", "lshr-u32",
    "literal-widen-u64", "literal-narrow-u8",
    "signed-lt", "unsigned-gt",
)

#: #694 的历史错值：整数字面量在 `as i64` 前先按 32 位截断。
HISTORIC_WRONG_694 = 253402300800 & 0xFFFFFFFF


def _expected_value(case: Case) -> int:
    """规范给出的 64 位值（程序最后写出去的就是这 8 字节）。"""
    v = _model(case.node, case.typ)
    if case.kind == "bool":
        return 1 if v else 2
    return v.num() & M64          # `t as u64`：有符号负数按补码落到 64 位


def _expected_stream() -> bytes:
    return b"".join(struct.pack("<Q", _expected_value(c)) for c in CASES)


def _source() -> str:
    """从**同一张表**生成两个编译器共用的源码（源码与期望值不会手改漂）。"""
    n = len(CASES)
    out = ["module spec_oracle", "choose runtime", "", "fn _start() {",
           f"    let p: ptr = alloc({8 * n});"]
    for i, c in enumerate(CASES):
        if c.kind == "bool":
            # 比较的**两边都先落到声明类型上**：裸字面量比较的默认宽度不在规范里
            # （docs/143：整数字面量由上下文定宽），拿它来考符号性会考错东西。
            _op, left, right = c.node[1], c.node[2], c.node[3]
            out += [f"    let c{i}a: {c.typ} = {_render(left, c.typ)};",
                    f"    let c{i}b: {c.typ} = {_render(right, c.typ)};",
                    f"    let v{i}: u64 = 0;",
                    f"    if c{i}a {_op} c{i}b {{ v{i} = 1; }} else {{ v{i} = 2; }}"]
        else:
            out += [f"    let t{i}: {c.typ} = {_render(c.node, c.typ)};",
                    f"    let v{i}: u64 = t{i} as u64;"]
        for k in range(8):
            off = 8 * i + k
            src = f"v{i} as u8" if k == 0 else f"(v{i} >> {8 * k}) as u8"
            out.append(f"    store8(p, {off}, {src});")
    out += [f"    syscall4(1, 1, p as u64, {8 * n});",
            "    syscall4(60, 0, 0, 0);", "}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- 两条例行路径

def _host_target() -> str:
    return loment_dist._host_target()                      # noqa: SLF001


def _link(ir: str, td: Path, name: str) -> Path:
    out = td / (name + (".exe" if _host_target() == "pe" else ".elf"))
    out.write_bytes(loment_dist._lomelf_link(ir, _host_target()))  # noqa: SLF001
    if _host_target() == "elf":
        out.chmod(0o755)
    return out


def _run_reference(src: Path, td: Path) -> tuple[bytes, str]:
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"参考实现拒了 oracle 源码: {errs[:2]}"
    return _run(_link(lomentc.emit_llvm(mod, ROOT, deps), td, "ref"))


def _stage1(td: Path) -> Path:
    """种子 -> 宿主格式的自举驱动（本机直接跑，不经 clang / WSL）。"""
    tgt = _host_target()
    out = td / ("stage1.exe" if tgt == "pe" else "stage1.elf")
    out.write_bytes(loment_dist._lomelf_link(                    # noqa: SLF001
        loment_seed.reference_ir("dev"), tgt))
    if tgt == "elf":
        out.chmod(0o755)
    return out


def _run_mirror(stage1: Path, src: Path, td: Path) -> tuple[bytes, str]:
    r = subprocess.run([str(stage1), str(src)], capture_output=True, cwd=str(ROOT),
                       shell=False, timeout=300)
    assert r.returncode == 0, f"自举侧拒了 oracle 源码: {r.stderr[-300:]!r}"
    return _run(_link(r.stdout.decode("utf-8", "replace"), td, "mirror"))


def _run(exe: Path) -> tuple[bytes, str]:
    r = subprocess.run([str(exe)], capture_output=True, shell=False, timeout=120)
    return r.stdout, f"rc={r.returncode} stderr={r.stderr[-200:]!r}"


# ---------------------------------------------------------------- 判定

def judge(expected: bytes, ref: bytes | None, mirror: bytes | None) -> list[str]:
    """把两侧的实际输出与**规范期望**比；返回问题清单（空 = 绿）。

    这里是 oracle 与孪生判据的分界：孪生问 `ref == mirror`，这里问
    `ref == spec and mirror == spec`。两侧一致但与规范不同 —— 正是 #772 要抓的那一类。
    """
    n = 8 * len(CASES)
    problems: list[str] = []
    if ref is None:
        problems.append("参考实现没有产出可执行文件")
    if mirror is None:
        problems.append("自举侧没有产出可执行文件")
    if problems:
        return problems
    if len(ref) != n or len(mirror) != n:
        return [f"输出长度不是 {n} 字节（参考 {len(ref)}，自举 {len(mirror)}）—— 程序没按表写满"]
    for i, c in enumerate(CASES):
        e = expected[8 * i:8 * i + 8]
        r = ref[8 * i:8 * i + 8]
        m = mirror[8 * i:8 * i + 8]
        if r == e and m == e:
            continue
        ev = struct.unpack("<Q", e)[0]
        rv = struct.unpack("<Q", r)[0]
        mv = struct.unpack("<Q", m)[0]
        if r == m:
            problems.append(
                f"{c.name}: **两个实现一致地给出 {rv}，但规范是 {ev}**（{c.cite}）"
                f" —— #772 的共享错误类：孪生判据看不见这一格")
        elif r == e:
            problems.append(f"{c.name}: 自举侧给出 {mv}，规范是 {ev}（{c.cite}）")
        elif m == e:
            problems.append(f"{c.name}: 参考实现给出 {rv}，规范是 {ev}（{c.cite}）")
        else:
            problems.append(
                f"{c.name}: 两侧互不相同（参考 {rv} / 自举 {mv}），规范是 {ev}（{c.cite}）")
    return problems


# ---------------------------------------------------------------- 判据

def test_both_implementations_match_the_spec_model() -> None:
    """两个编译器各自跑同一个程序，输出必须**逐字节等于规范模型**（不是互相等于）。

    三条路径：规范模型（`_model`，纯 Python，不碰编译器）、参考实现
    （`lomentc.emit_llvm` → `lomelf`）、自举侧（种子 → stage1 → `lomelf`）。
    后两条只比行为，不比 IR —— IR 逐字节是 `loment_store_twin_test` 的事。
    """
    expected = _expected_stream()
    src_text = _source()
    with tempfile.TemporaryDirectory(prefix="spec-oracle-") as tds:
        td = Path(tds)
        src = td / "spec_oracle.lomt"
        with src.open("w", encoding="utf-8", newline="\n") as f:
            f.write(src_text)
        ref, ref_note = _run_reference(src, td)
        mir, mir_note = _run_mirror(_stage1(td), src, td)
    problems = judge(expected, ref, mir)
    assert not problems, (
        f"{len(problems)} 个用例与规范不符:\n  " + "\n  ".join(problems[:8])
        + f"\n  （参考 {ref_note}；自举 {mir_note}）")
    print(f"      {len(CASES)} 个用例：参考实现与自举侧都等于规范模型（{len(expected)} 字节）")


def test_agreement_alone_is_not_accepted() -> None:
    """**两侧一致 ≠ 对**：把 #694 的历史错值喂给判定，必须红并点名那一格。

    这条是 oracle 的**证伪判据**。它模拟的正是 #772 说的场景：参考与自举**都**把
    `253402300800 as i64` 截成 32 位（历史值 `0xFFF44180`）。孪生判据对这种情形报绿；
    这里必须报"两个实现一致地给出 N，但规范是 M"。
    """
    expected = _expected_stream()
    idx = next(i for i, c in enumerate(CASES) if c.name == "literal-widen-u64")
    wrong = bytearray(expected)
    wrong[8 * idx:8 * idx + 8] = struct.pack("<Q", HISTORIC_WRONG_694)

    problems = judge(expected, bytes(wrong), bytes(wrong))
    assert problems, "两侧一致但都错时判定竟然通过了 —— oracle 退化成了孪生"
    head = problems[0]
    assert "两个实现一致地给出" in head and "literal-widen-u64" in head, head
    # 反向对照：真值本身必须通过（判定不是恒红）。
    assert judge(expected, expected, expected) == [], "规范自己的答案竟然被判红"
    print(f"      证伪：两侧同为 #694 的错值 {HISTORIC_WRONG_694} 时判定红，"
          f"且点名 literal-widen-u64；两侧同为规范值时判定绿")


def test_the_table_covers_the_shared_error_classes() -> None:
    """用例表是一张**只许变长**的覆盖面账：类别点名在，出处不能空。

    没有这条，删掉"恰好抓到某个共享错误"的那一格不会有人发现 —— 与
    `KNOWN_GAPS` 棘轮同一个理由，只是方向相反（这是**下限**，那是**上限**）。
    """
    names = {c.name for c in CASES}
    missing = [n for n in REQUIRED_CLASSES if n not in names]
    assert not missing, f"oracle 表缺了这些共享错误类别: {missing}"
    assert len(CASES) >= 20, f"用例数 {len(CASES)} 少于下限 20 —— 删格子要连下限一起改？"
    for c in CASES:
        assert c.cite.strip(), f"{c.name}: 没有规范出处"
        assert "docs/144" in c.cite, f"{c.name}: 出处没有指向整数语义的规范 (docs/144 §3)"
    # 源码必须真的能从表长出来（渲染器与表同源）。
    src = _source()
    assert src.count("store8(") == 8 * len(CASES), "渲染出来的存储数不对"
    print(f"      {len(CASES)} 格，{len(REQUIRED_CLASSES)} 个类别点名在，出处全非空")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_spec_oracle_test")


if __name__ == "__main__":
    sys.exit(main())
