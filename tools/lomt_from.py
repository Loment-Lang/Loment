#!/usr/bin/env python3
# lomt_from.py — Potato 形式对象 -> L1 (.lomt) **接口单元** (docs/179, docs/175 §5)
#
# 定位 (docs/140 §9): 源语言的语法属于源语言, 表示属于 Potato, L1 是冻结核心的输入。
# 本工具补的是多语法前端那条链的**后半段**:
#
#     任意源语法  --potato_from-->  Potato 形式对象  --lomt_from-->  .lomt
#                                                                     |
#                                                        （进入冻结核心, docs/158）
#
# **为什么只出接口, 不出带体的实现**: Potato 的 schema 里**没有函数体** ——
# 表示层记的是"有什么"(结构 / 能力 / 契约 / 布局), 不是"怎么算" (docs/142 §1、
# docs/147、docs/178)。所以这一版的前端产出**声明单元**: 类型、常量、能力、函数签名。
# 函数一律发 `extern fn` —— 那道接口的实现在源语言那一侧, 调用点走平台 C ABI
# (docs/173 §2)。这不是权宜之计: 一份"把那个模块的接口抄过来"的文件, 语义上本来就是
# FFI 声明, 而且它**真的能用** —— 生成的单元可以直接 `use`, 链接外面编好的目标文件。
#
# **发不出来的东西报错, 不静默丢**: traits / impls / generics / instances / layouts
# 非空 = 这个对象超出了本工具的表示面, 报错退出。与 lomelf、自举驱动一贯的
# "把静默错编改成报错退出" 同一条纪律 (docs/167)。**少发一个函数比发错更坏**:
# 调用点会静默解析到别的东西上。
#
# 用法:
#   python tools/lomt_from.py OBJ.potato.json [--out OUT.lomt]
#   python tools/lomt_from.py SRC.rs --lang rust --out OUT.lomt      # 一步到位
#   python tools/lomt_from.py SRC.c  --lang c    --out OUT.lomt
# 退出码: 0 = 成功 / 1 = 对象里有发不出来的东西 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 顶名字必须与 L1 的标识符文法一致 (docs/143)。`potato_from` 已经过一道 `_ident`,
#: 但对象可以是手写的 —— 所以这里再挡一次, 报错而不是产出一份编不过的源码。
def _is_ident(s: object) -> bool:
    return isinstance(s, str) and bool(potato.IDENT_RE.match(s))


#: 超出本工具表示面的字段: 非空就报错 (见文件头)。
_UNSUPPORTED = ("traits", "impls", "generics", "instances")


class NotRepresentable(Exception):
    """对象里有本工具发不出来的东西。**不猜、不跳过** —— 直接说清是哪一项。"""


def _check_representable(doc: dict) -> None:
    for k in _UNSUPPORTED:
        v = doc.get(k)
        if v:
            raise NotRepresentable(
                f"{k} 非空 ({len(v)} 项) —— Potato 记了它, 但 L1 那侧的对应形状还没接上; "
                f"**不静默丢**, 报错退出")
    # layouts 是 `.lom` 的二进制版式 (Header 48B 那种), 属于 L0 单源, 不是 L1 的结构体。
    if doc.get("layouts"):
        raise NotRepresentable(
            f"layouts 非空 ({len(doc['layouts'])} 项) —— 那是 L0 (`lom/*.lom`) 的二进制版式, "
            f"应当由 `lomc`/`lomdoc` 生成, 不经过 L1")
    if doc.get("imports"):
        raise NotRepresentable(
            f"imports 非空 ({len(doc['imports'])} 项) —— Potato 的 imports 是**单元名**, "
            f"而 L1 的 `use` 要么给路径要么按层搜 (docs/143); 名字到路径的对应本工具不知道, "
            f"**不猜**")


def _ty(t: object) -> str:
    if not isinstance(t, str) or not t:
        raise NotRepresentable(f"类型 {t!r} 不是字符串")
    return t


#: FFI 第 1 阶段收的签名面 (docs/173 §3): 标量 + `ptr`。聚合类型与 `str` **按值**传参
#: 会改变调用点代码形状, 一律留到后面 —— 这里照同一条线卡住, 与 `lomentc` 的
#: `_extern_ty_ok` 对齐 (那边是编译器侧的闸门, 这里是前端侧的**同一道**闸门)。
_FFI_SCALARS = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "ptr", "()")


def _ffi_ok(t: object) -> bool:
    return isinstance(t, str) and t in _FFI_SCALARS


#: **一门一张表** —— 加一门只加一行，别在 `emit_lomt` 里长成一条 if 链。
#: 每项: 模块名 · 它的 "子集外" 异常类 · 工具路径 · 给用户的子集提示。
#: 放在模块级（不是 `emit_lomt` 里）是为了让判据也能读到它 —— 判据要拿同一张表去调
#: 那一门的翻译器（`loment_multisyntax_test`），表抄第二遍必然漂。
_TOOLS = {
    "c": ("ctrans", "Unsupported CError", "tools/ctrans.py",
          "只收整数标量、if/while/for、四则与位运算"),
    "python": ("pytrans", "Unsupported PyError", "tools/pytrans.py",
               "只收整数标量、if/while/for、四则与位运算；参数与返回都要写类型注解"),
    "java": ("jtrans", "Unsupported CError", "tools/jtrans.py",
             "只收整数标量、if/while/for、四则与位运算；`class` 外壳会被抹掉"),
    "csharp": ("cstrans", "Unsupported CError", "tools/cstrans.py",
               "只收整数标量、if/while/for、四则与位运算；"
               "`using` / `namespace` / `class` 三层外壳都会被抹掉"),
    "cpp": ("cpptrans", "Unsupported CError", "tools/cpptrans.py",
            "只收整数标量与 `bool`、if/while/for、四则与位运算；"
            "预处理指令、`std::` 与成员访问都不收"),
    "go": ("gotrans", "Unsupported GoError", "tools/gotrans.py",
           "只收整数标量与 `bool`、`if`/`for`、四则与位运算；"
           "`string` / 切片 / map / 指针 / 多返回值都不收"),
    # **这一门与前六门不同类**（`docs/197`）：前六门是别人已有的语言，子集线是**碰上的**；
    # 这一门是我们自己发明的，子集线是**画出来的** —— 所以下面的"不收"是设计，不是缺口。
    "natural": ("nltrans", "Unsupported NaturalError", "tools/nltrans.py",
                "动词起头的句子，声明也如是：`say` / `talk to the machine` / `paint` / "
                "`let be` / `set to` / `when`（含 `looks like` 看形状）/ `while` / "
                "`for from to` / `give back` / `guard the … space at`；声明是 "
                "`a … has`（结构体）/ `a … is either`（枚举）/ `a … can`（trait）/ "
                "`a … can be a …`（impl）/ `a <空间> space called`（能力域）/ `use` / "
                "`someone else wrote`（C ABI）/ `leave out`"),
}


def _join_bodies(bodies: dict[str, str], at: dict[str, int]) -> str:
    """把各函数正文拼成**一份源**，让每一段都落在它**在原文里的那一行**。

    **为什么不能直接 `"\\n".join`**：翻译器报的 `第 N 行` 数的是**它拿到的那串文本**的
    行号。裸拼的话 N 是"按函数体"算的 —— 十几个函数拼起来之后与文件完全对不上，
    用户在源码里按那个行号**找不到东西**（`docs/179` §6.5：错要指在错的地方）。

    **做法是垫空行**（`at` 就是 `functions[i].body_line`），与 `trans_core.strip_shells`
    抹外壳用的是同一条纪律 —— 那一处是"换成**等长**空白"，这里没有"抹掉"可言，
    所以是"补上**缺的那些换行**"。两条都是**让行号与原文一致**，而不是让报错改写行号：
    诊断那一侧（`trans_core` 里几十处 `第 {…} 行`）一行都不用动。

    **正文本身一字不动**：`body` 是 `potato_from` 按原文切下来的，`test_body_is_byte_faithful`
    钉着它逐字节保真；垫空行只发生在这份**拼出来的**源里。而翻译器的产物是走语法树的
    （`Emitter` 从 AST 出行），空行不进产物 —— 所以 `--impl` 发出来的 `.lomt` **一个字节都不变**。

    没带 `body_line` 的对象（手写的、或 v≤6 的旧对象）退回**裸拼**：宁可还是老样子，
    也不能凭空编一个位置出来。
    """
    parts: list[str] = []
    cur = 1                                   # 下一个字符会落在第几行
    for n, b in bodies.items():
        want = at.get(n)
        if want is None:
            if parts:
                parts.append("\n")
                cur += 1
        elif want > cur:
            parts.append("\n" * (want - cur))
            cur = want
        else:
            # 位置撞了/倒回去了（写坏的对象）。**至少隔开一行** —— 两段正文并到同一行
            # 会让它们粘成一个语法上不成立的东西，那比行号不准更坏。
            parts.append("\n")
            cur += 1
        parts.append(b)
        cur += b.count("\n")
    return "".join(parts)


def _sig_params(fn: dict) -> str:
    out = []
    for p in fn.get("params") or []:
        n = p.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"函数 {fn.get('name')!r} 的形参名 {n!r} 不是标识符")
        out.append(f"{n}: {_ty(p.get('type'))}")
    return ", ".join(out)


def _whose_line(e: object, body_at: dict[str, int]) -> str | None:
    """从翻译器抛出的消息里取出 `第 N 行`，回查那是**哪个函数**的正文（`docs/186` §4）。

    没有行号、或没有函数落在它之前，就返回 `None`（调用方照旧走原来那句话）。
    """
    m = re.search(r"第 (\d+) 行", str(e))
    if m is None:
        return None
    ln = int(m.group(1))
    best: str | None = None
    for name, start in body_at.items():
        if start <= ln and (best is None or start > body_at[best]):
            best = name
    return best


def emit_lomt(doc: dict, impl: bool = False) -> tuple[str, list[tuple[str, str]]]:
    """Potato 形式对象 -> (L1 单元源码, 跳过的项)。

    **确定性**: 同一份对象永远出同一串字节 (所以生成物能进判据)。
    第二项是 `(名字, 原因)` —— 调用方**必须报出来** (见 `main`)。

    `impl=True` (`docs/186`) 时，**带 `body` 的函数发成真实现**（`pub fn … { … }`，
    正文由 `ctrans` 从原文翻成 Loment），没带正文的照旧发 `pub extern fn`。默认 `False`
    —— 那才是本工具一贯的"接口单元"，而且**必须是默认**：`loment_multilang_test`
    那类用法靠 `extern fn` 把实现在外部（编好的 `.o`）里的符号接进来，
    发了体就变成"同一个符号定义两遍"，而那是**链接期的错**，不是这里的。
    """
    _check_representable(doc)
    if not _is_ident(doc.get("unit")):
        raise NotRepresentable(f"unit {doc.get('unit')!r} 不是标识符 (L1 的 module 名要求它)")

    skipped: list[tuple[str, str]] = []
    ver = doc.get("potato")
    lines: list[str] = [
        "// 由 tools/lomt_from.py 从 Potato 形式对象生成 —— 请勿手改。",
        "// 它是**接口单元**: 函数是 `extern fn` (实现在源语言那一侧,",
        "// 调用点走平台 C ABI, docs/173 §2)。",
        f"// 源对象: unit={doc.get('unit')} language={doc.get('language')} potato={ver}",
        "",
        f"module {doc['unit']}",
    ]
    if impl:
        lines[1] = "// 它是**带实现的单元** (`--impl`, docs/186): 带正文的函数是真 `pub fn`，"
        lines[2] = "// 没带正文的仍是 `extern fn` (实现在源语言那一侧, docs/173 §2)。"
        # **实体单元带运行期声明**（`docs/227`）。翻译过来的程序按 C / Python / Java / Go
        # 的语义算 —— 那几门语言**都有运行期**（除法要 trap、内存要分配），而 Loment 从
        # 2026-10-10 起默认档是一句**保证**：`no_runtime` 的产物里不许出现那段运行期。
        # 不带这一行，**每一份**翻出来的程序都会在检查器那里被拒。
        #
        # **只加在 `impl=True` 这一支**，两个理由：接口单元（全是 `extern fn`，没有正文）
        # 一个构造都不产生，本来就不需要它；而自举侧的孪生 `loment/tools/lomtfrom.lomt`
        # **只走 `impl=False` 那一支**（见它的头注），所以这一改**不用动孪生**。
        lines.insert(6, "choose runtime")

    # ---- 能力域
    caps = doc.get("capabilities") or []
    if caps:
        lines.append("")
        lines.append("// ---- 能力域 (docs/146)")
        for c in caps:
            n = c.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"能力名 {n!r} 不是标识符")
            d = c.get("domain") or {}
            lo, hi = d.get("lo"), d.get("hi")
            if not isinstance(lo, int) or not isinstance(hi, int) or isinstance(lo, bool):
                raise NotRepresentable(f"能力 {n} 的域 lo/hi 必须是整数, 得到 {lo!r}/{hi!r}")
            space = d.get("space")
            if not _is_ident(space):
                raise NotRepresentable(f"能力 {n} 的空间名 {space!r} 不是标识符")
            tail = " revocable" if c.get("revocable") else ""
            lines.append(f"capability {n} : {space}[{lo}..{hi}]{tail}")

    # ---- 常量
    consts = doc.get("consts") or []
    if consts:
        lines.append("")
        lines.append("// ---- 常量")
        for c in consts:
            n = c.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"常量名 {n!r} 不是标识符")
            v = c.get("value")
            if not isinstance(v, int) or isinstance(v, bool):
                raise NotRepresentable(f"常量 {n} 的值 {v!r} 不是整数 (L1 常量只收整型)")
            lines.append(f"pub const {n}: {_ty(c.get('type'))} = {v};")

    # ---- 结构体
    for s in doc.get("types") or []:
        n = s.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"结构体名 {n!r} 不是标识符")
        fields = s.get("fields") or []
        if not fields:
            raise NotRepresentable(f"结构体 {n} 没有字段 (L1 不收空结构体)")
        lines.append("")
        lines.append(f"pub struct {n} {{")
        seen: set[str] = set()
        for f in fields:
            fn = f.get("name")
            if not _is_ident(fn):
                raise NotRepresentable(f"结构体 {n} 的字段名 {fn!r} 不是标识符")
            if fn in seen:
                raise NotRepresentable(f"结构体 {n} 的字段 {fn} 重复")
            seen.add(fn)
            lines.append(f"    {fn}: {_ty(f.get('type'))},")
        lines.append("}")

    # ---- 枚举
    for e in doc.get("enums") or []:
        n = e.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"枚举名 {n!r} 不是标识符")
        vs = e.get("variants") or []
        if not vs:
            raise NotRepresentable(f"枚举 {n} 没有变体 (L1 不收空枚举)")
        payloads = e.get("payloads") or {}
        lines.append("")
        lines.append(f"pub enum {n} {{")
        seen = set()
        for v in vs:
            if not _is_ident(v):
                raise NotRepresentable(f"枚举 {n} 的变体名 {v!r} 不是标识符")
            if v in seen:
                raise NotRepresentable(f"枚举 {n} 的变体 {v} 重复")
            seen.add(v)
            pt = payloads.get(v)
            lines.append(f"    {v}({_ty(pt)})," if pt else f"    {v},")
        lines.append("}")

    # ---- 函数 (一律 extern: 实现在源语言那一侧)
    # 先攒进 fn_lines: **一条都没发出来时不留空的分节标题** —— 一个只有标题的
    # "// ---- 外部函数" 会让读者以为下面本该有东西 (而原因是被跳过了, 那个报在 stderr)。
    fns = doc.get("functions") or []
    #: `pub extern fn` 那一批（实现在源语言那一侧）
    fn_lines: list[str] = []
    #: 翻了正文得到的那一批（`--impl`）。两块**各有各的标题**，空的那块不留标题。
    impl_lines: list[str] = []
    if fns:
        names: set[str] = set()
        #: 有正文的那批（`--impl` 要翻的）。键是名字，值是原文。
        bodies: dict[str, str] = {}
        #: 各正文**在原文里的起始行**（`functions[i].body_line`，可选）。拼源时按它垫空行，
        #: 于是翻译器报的行号就是源里的行号 —— 见 `_join_bodies`。
        body_at: dict[str, int] = {}
        for f in fns:
            n = f.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"函数名 {n!r} 不是标识符")
            if n in names:
                raise NotRepresentable(f"函数 {n} 重复")
            names.add(n)
            # **两条闸门, 缺一不可**。少发一个函数在这里是**安全的** —— Loment 没有重载,
            # 名字是精确的, 所以调用点会得到"未声明的函数"(E002) 而不是静默绑到别人身上。
            # 但**必须报出来** (见 `main`), 不能沉默。
            # 正文只在 `--impl` 那条路上用。**没开 `--impl` 时它不是"被跳过的东西"** ——
            # 接口单元本来就是"声明在此、实现在那边"，`pack_iface.lomt` 那类用法
            # （`docs/179` §2）要的正是这个。报成 skip 会让 `loment_multilang_test` 红，
            # 而且报的是假消息（没有任何东西被丢掉）。
            bd = f.get("body")
            if impl and isinstance(bd, str) and bd.strip():
                # **有正文时 ABI 那道闸门让开**：它不是"声明一条外部函数"，而是
                # **定义**这个函数 —— 实现就在这儿（由 `ctrans` / `pytrans` 翻出来）。
                bodies[n] = bd
                ln = f.get("body_line")
                if isinstance(ln, int) and not isinstance(ln, bool) and ln >= 1:
                    body_at[n] = ln
                continue
            abi = f.get("abi")
            # **`abi` 缺席 = 这是个 Loment 函数**（`docs/188` §3）。
            #
            # 旧读法是反过来的：`abi` 缺席被当成"没记 ABI 的外国函数"而跳过。
            # 那套读法把 `potato_from` **推断**出来的 ABI（"因为它是个 Java 文件"）
            # 当成了"实现在外面"的证据 —— 于是一份 Python 写法的单元整批被跳过，
            # 转出一个**空 module**。现在 `potato_from` 只记**源码显式宣称**的 ABI
            # （`extern "C"` / `//export`），所以缺席有确定的意思：它是 Loment。
            if abi is None:
                # **这句话要分两种情形说** —— 旧版只有一句"带正文的加 `--impl` 翻出来"，
                # 而 `front_door` **就是**用 `impl=True` 调的（`potato_from.front_door`）：
                # 已经把 `--impl` 加上的人，看到的却是"你再把它加上"。一句**指反了的**话。
                # 真的原因是前者：这条路（`docs/179` 抽接口）**根本没抓正文**，
                # 而这一门也未必有翻译器（`_TOOLS` 里没有就得先有孪生，`docs/189` §4.1）。
                why = ("这是个 Loment 函数但对象里没带正文 —— 接口单元里没有它的实现。"
                       if not impl else
                       # **`impl=True` 这条**：`front_door` 就是用它调的。所以走到这里
                       # 最常见的情形是"**源码里这条只有声明、没有定义**"——C 里就是
                       # **原型**（`int g(int x);`）。原先把原因写成"这份对象的 grammar 是
                       # **抽接口那条路**读出来的，它只记声明、不抓正文" —— 那句话对
                       # 前门那条路**是反的**（前门就是抓正文的那条），用户按它去"换
                       # `choose write grammar` 前门"只会原地打转。
                       "这份对象里没有这个函数的正文 —— 走 `choose write grammar` 前门时，"
                       "那意味着**源码里这条只有声明、没有定义**（C 里就是**原型** "
                       "`int g(int x);`，而 Stage A **不支持原型**）。给它补一个定义；"
                       "若它本来就是**外部函数**，那走 `pub extern fn` 那条路（`docs/186` §9），"
                       "不是这一条。")
                skipped.append((n, why))
                continue
            if abi != "c":
                skipped.append((n, f"调用约定不是 C ABI (abi={abi})"))
                continue
            bad = [p.get("name") for p in (f.get("params") or [])
                   if not _ffi_ok(p.get("type"))]
            if not _ffi_ok(f.get("ret") or "()") or bad:
                skipped.append((n, "签名超出 FFI 第 1 阶段 (只收标量与 ptr, docs/173 §3)"
                                   + (f"; 参数 {bad}" if bad else "")))
                continue
            r = f.get("ret")
            head = f"pub extern fn {n}({_sig_params(f)})"
            if r and r != "()":
                head += f" -> {_ty(r)}"
            fn_lines.append(head + ";")

        # ---- 带正文的函数：把原文拼成一份源文件再翻（`docs/186` / `docs/187`）
        if bodies:
            # **按 `grammar` 分派，不按 `language`**（`docs/188` §3）：`language` 现在
            # 一律是 `"loment"`（用别的写法写的**也是** Loment），"用哪种写法写的"
            # 记在 `grammar` 上。旧对象没有 `grammar`（v≤5），兜底当 Loment。
            lang = doc.get("grammar") or "loment"
            if lang == "loment" and doc.get("language") in ("c", "python", "java", "go", "rust"):
                lang = doc["language"]      # 旧对象（v≤5）还是按 language 记的
            # **一门一张表**（模块级的 `_TOOLS`）—— 加一门只加一行，别在这里长成 if 链。
            if lang not in _TOOLS:
                raise NotRepresentable(
                    f"这份对象的 grammar 是 {lang!r} —— 带正文的函数还没有这一门的翻译器"
                    f"（现在有 {sorted(_TOOLS)}，见 docs/186 / docs/187 / docs/188 §7.1）")
            _mn, _en, tool, hint = _TOOLS[lang]
            mod = __import__(_mn)       # 只在 `--impl` 这条路上要它 —— 默认那条不该被拖进来
            errs = tuple(getattr(mod, e) for e in _en.split())
            # `externs` 只有 C 那门用得上：它的 `pub extern fn` 与翻译出来的实现能共处
            # 一个单元。Python 那门不导出 C ABI（`abi="python"` 的进不了 `extern fn`），
            # 所以 Python 单元里的跨函数调用只认带正文的那些 —— 缺了会**响亮报错**。
            kw: dict = {}
            if lang == "c":
                # 键**必须**是 `f.get("name")`，不能写成 `n` —— 上面那个 `for f in fns`
                # 的循环变量叫 `n`，循环结束后它留着**最后一个函数**的名字。曾经写的就是
                # `n`，于是每个条目的键都塌成同一个名字、字典只剩一条，跨单元那条路
                # 整个失效（而报出来的话是"本单元没有这个函数"，指向别处）。
                kw["externs"] = {f.get("name"): _ty(f.get("ret") or "()")
                                 for f in fns
                                 if _is_ident(f.get("name")) and f.get("name") not in bodies
                                 and _ffi_ok(f.get("ret") or "()")}
            else:
                # Python 那门要知道**模块常量**的名字：正文里只有函数，常量在对象的
                # `consts` 里（`pub const` 由本函数上面那段发），不给这张表的话
                # 函数体里一引用常量就报"没赋过值"。
                kw["consts"] = {c["name"]: _ty(c.get("type") or "i64")
                                for c in (doc.get("consts") or [])
                                if _is_ident(c.get("name"))}
            try:
                # **拼出来的源的行号 == 原文的行号**（见 `_join_bodies`）—— 翻译器
                # 报的 `第 N 行` 因此直接就是用户能在源码里找到的那一行。
                text = mod.translate(_join_bodies(bodies, body_at),
                                     keep=set(bodies), **kw)
            except errs as e:  # type: ignore[misc]
                # **报得出是哪个函数** —— `docs/186` §4 的承诺（"一份文件里往往十几个
                # 函数，只说'子集外'用户不知道该去改哪一个"）。
                #
                # 异常里带的是**原文行号**（`_join_bodies` 按 `body_line` 垫空行保证了
                # 这一点），而 `body_at` 记着每个函数的**起始行** —— 拿行号回查就能点名。
                #
                # 原先只给"`{len(bodies)}` 个带正文的函数里有子集外的写法"：那个数是
                # **所有**带正文的函数的个数，不是"有几个出问题"，而且**函数名一个都
                # 没有**。实测：一份 4 个函数的文件、第 3 个非法，报的是
                # "**4 个**带正文的函数里有子集外的写法: 第 4 行: 不支持 `switch`"。
                who = _whose_line(e, body_at)
                if who is not None:
                    raise NotRepresentable(
                        f"函数 `{who}` 里有子集外的写法（{tool} 的 Stage A {hint}）: {e}")
                raise NotRepresentable(
                    f"{len(bodies)} 个带正文的函数里有子集外的写法（{tool} 的 Stage A "
                    f"{hint}）: {e}")
            impl_lines.append(f"// ---- 由正文翻译出来的实现 "
                              f"(docs/186: {lang} -> Loment, {tool})")
            impl_lines.append(text.rstrip("\n"))
        if fn_lines:
            lines.append("")
            lines.append("// ---- 外部函数 (docs/173: 声明在此, 实现在源语言那一侧, C ABI)")
            lines += fn_lines
        if impl_lines:
            lines.append("")
            lines += impl_lines

    # ---- 出界声明
    for x in doc.get("excluded") or []:
        if not isinstance(x, str) or not x:
            raise NotRepresentable(f"excluded 项 {x!r} 不是非空字符串")
        lines.append("")
        lines.append(f'excluded "{x}"')

    return "\n".join(lines) + "\n", skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomt_from",
                                 description="Potato 形式对象 -> L1 接口单元")
    ap.add_argument("path", help=".potato.json, 或源文件 (配 --lang)")
    ap.add_argument("--lang", choices=("auto", "python", "c", "rust"), default=None,
                    help="给了就先把源文件转成形式对象再发 L1")
    ap.add_argument("--mode", choices=("strict", "lenient"), default="strict")
    ap.add_argument("--impl", action="store_true",
                    help="对象里带 `body` 的函数发成真实现 (docs/186)；默认只发接口")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    #: 实际**认成**的那个语言 (不是 `a.lang` —— 用户多半给的是 `auto`)。
    #: 初值给 `a.lang` 只是为了下面那个 `except SyntaxError` 里引用得到它。
    got = a.lang

    # **不给 `--lang` 时按输入的性质分**，不是一律当 JSON 读。
    #
    # 这个工具有两种输入：`.potato.json`（形式对象）与**源文件**（配 `--lang`）。
    # 原先不给 `--lang` 就一律走 JSON 那一支，于是 `lomt_from.py g.c` 把一份 C 源码
    # 喂给 `json.loads`，端到用户面前的是 **Python 的 json 异常文本**：
    #
    #     [ERR] 读取失败: Expecting value: line 1 column 1 (char 0)
    #
    # 那句话对一份 C 文件没有任何意义；而同一个文件经**上游那一段**
    # （`potato_from.py g.c`）却好好出来（rc=0）。`.c` 后缀本来就有确定答案
    # （`potato_from.EXT[".c"] == "c"`），而 `resolve_lang` 这台工具**本来就有**
    # （给了 `--lang auto` 时走的就是它）。
    #
    # 判据取**后缀**而不是内容：`.json` 是形式对象，别的都当源文件交给 `auto`。
    if a.lang is None and Path(a.path).suffix.lower() != ".json":
        a.lang = "auto"

    try:
        if a.lang is not None:
            import potato_from
            # **先说清认成了什么** —— 这就是"检测"这一步: 用户拿一份 `.lomt` 装着 C
            # 过来, 他要看到的是"认出来了", 而不是默默转出一个东西。
            got, why = potato_from.resolve_lang(Path(a.path), a.lang)
            print(f"[detect] {a.path} -> {got or '认不出'}（{why}）", file=sys.stderr)
            if got == "loment":
                print(f"[ERR] 这本来就是 Loment 语法, 不该走前端 —— "
                      f"直接交给编译器: loment check {a.path}", file=sys.stderr)
                return 2
            doc, rep = potato_from.transcribe(Path(a.path), a.lang, a.mode)
            # **转写那一步的跳过项也要报** —— 只报发射那一步等于把"前一步丢的"藏起来。
            # 2026-09-17 实测: 一份 5 函数的 C 只发出 1 个, 而这里一条 `[skip]` 都没有,
            # 看起来像"它只认出 1 个函数"(真相是另 4 个在转写那步因类型没映射被丢)。
            for s in rep.skipped:
                print(f"[skip] {s['name']}: {s['why']}", file=sys.stderr)
            # **字段级跳过也要出声** (2026-09-17 实测): `float` / `list` / 无注解字段,
            # 以及枚举类型的字段, 原先只躺在 `Report.field_notes` 里 —— 命令行**一个字都
            # 不打**。一份 struct 少两个字段, `[OK]` 那行干干净净。字段不计进"实体分母"
            # (见 `Report.skip_field`: 分子分母都是实体, 塞字段进去会让转化率失真) 是对的,
            # 但**不计数不等于不报告** —— 五个前端都在调 `skip_field`, 这一处修的是全体。
            for f in rep.field_notes:
                print(f"[skip] {f['owner']}.{f['field']}: {f['why']}", file=sys.stderr)
            n_pre = len(rep.skipped) + len(rep.field_notes)
        else:
            doc = json.loads(Path(a.path).read_text(encoding="utf-8"))
            n_pre = 0
        # 对象自身先要合法 —— 拿一份非法对象去发 L1 等于把错误往后传。
        errs = potato.validate(doc)
        if errs:
            print(f"[ERR] 形式对象不合法: {errs[0]}", file=sys.stderr)
            return 2
        text, skipped = emit_lomt(doc, impl=a.impl)
    except NotRepresentable as e:
        print(f"[ERR] 发不出来: {e}", file=sys.stderr)
        return 1
    # **按错的语法去解析要报得出来** (2026-09-17 实测): 一份带 `import` 的 Java 原文
    # 被内容兜底判成 python 之后, `ast.parse` 抛的是**裸 traceback** —— 用户看到的是一屏
    # Python 内部栈, 一个字都没说"你这份东西不是 Python"。识别错了不丢人, 认错还甩栈才丢人。
    except SyntaxError as e:
        print(f"[ERR] 按 {got} 解析失败: 第 {e.lineno} 行 {e.msg}\n"
              f"      这份源码不像是 {got} —— 用 `--lang <名字>` 指明, "
              f"或检查它是否本来就是 Loment (`loment check`)。", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERR] 读取失败: {e}", file=sys.stderr)
        return 2

    # **跳过必须出声**。它们不进产物 (那是对的: 宁缺勿错), 但沉默就等于让用户以为
    # "那个函数本来就不在那儿"。写到 stderr, 不污染 stdout 的源码。
    for n, why in skipped:
        print(f"[skip] {n}: {why}", file=sys.stderr)

    if a.out:
        # `newline="\n"`: 与 loment_build.py 同一处坑 (见那里的注释)
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        n_fn = text.count("pub extern fn ")
        n_skip = len(skipped) + n_pre
        print(f"[OK] {a.path} -> {a.out}"
              + (f" (函数 {n_fn} 条, 跳过 {n_skip} 条)" if n_skip else ""))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
