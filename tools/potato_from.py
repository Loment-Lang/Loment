#!/usr/bin/env python3
# potato_from.py — Python/C/Rust 源码 -> Potato 形式对象 转写器 (M49, docs/147)
#
# 定位 (docs/140 §9): 源语言的语法属于源语言, 表示属于 Potato。
# 本工具是"低收益工具链"臂: 只做结构识别, 不做语义等价证明。strict 臂不猜测 ——
# 映射不出类型的实体记入 report.skipped; lenient 臂按固定规则放宽 (未知->ptr, 未标注->i64),
# 因此转换率更高但保真度更低。两臂都确定性可复现, 绝不随机。
#
# 用法:
#   python tools/potato_from.py FILE [--lang auto|python|c|rust] [--mode strict|lenient]
#                                  [--json OUT.json] [--report OUT.report.json]
#   python tools/potato_from.py --corpus MANIFEST.json --out-dir DIR
#
# 退出码: 0 = 全部转写成功 / 1 = 有文件转写失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

# ---- 源语言类型 -> Potato 类型
PY_TYPES = {"int": "i64", "bool": "bool", "str": "str", "bytes": "ptr", "None": "()"}
C_TYPES = {
    "uint8_t": "u8", "uint16_t": "u16", "uint32_t": "u32", "uint64_t": "u64",
    "int8_t": "i8", "int16_t": "i16", "int32_t": "i32", "int64_t": "i64",
    "unsigned char": "u8", "unsigned short": "u16", "unsigned int": "u32",
    "unsigned long": "u64", "signed char": "i8", "short": "i16", "int": "i32",
    # **`unsigned` / `signed` 单独写就是 `unsigned int` / `signed int`**（C 标准这么定，
    # 不是缩写习惯）。原先这里没有它们，于是 `unsigned f(...)` 的返回类型"无映射"被跳过 ——
    # 而那是最常见的 C 写法之一。2026-09-17 用一份 C 装 `.lomt` 实测撞到的：
    # 5 个函数里 4 个因此被丢，而**报告还被工具扔了**，看起来像"只认出 1 个"。
    # `char` 的符号性在 C 里是实现定义的；x86-64 Linux 上是 i8，照这个来。
    "unsigned": "u32", "signed": "i32", "char": "i8",
    "long": "i64", "size_t": "u64", "ssize_t": "i64", "bool": "bool",
    "_Bool": "bool", "void": "()", "char *": "str", "const char *": "str",
    "void *": "ptr", "uintptr_t": "u64", "intptr_t": "i64",
    # 内核常见 typedef 简写 (结构识别: 与标准名同义)
    "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64",
}
RS_TYPES = {
    "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64",
    "bool": "bool", "()": "()", "usize": "u64", "isize": "i64",
}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

GO_TYPES = {
    # `int`/`uint` 在 Go 里是**平台相关**的 (至少 32 位; amd64 上是 64 位)。
    # 这里按**本仓唯一支持的目标** (x86-64) 读 —— 不假装它是精确的:
    # 要精确就别用 `int`, 用 `int32`/`int64`。这条与 C 那边的 `int -> i32` 不同,
    # 是**因为两种语言的规定不同**, 不是为了整齐。
    "int": "i64", "int8": "i8", "int16": "i16", "int32": "i32", "int64": "i64",
    "uint": "u64", "uint8": "u8", "uint16": "u16", "uint32": "u32", "uint64": "u64",
    "byte": "u8", "rune": "i32", "bool": "bool", "uintptr": "u64",
    # Go 的 `string` 就是「指针 + 长度」—— 与 Loment 的 `str` 同形, 所以映 `str`,
    # **不**映 `ptr`。(它不是 C 字符串, 于是过不了 FFI 第 1 阶段那道闸门 —— 那是对的,
    # 见 docs/179 §3。)
    "string": "str",
    # float 不在 Loment 的类型里 —— 显式写出来, 让报错是"无映射"而不是"未知类型"。
    "float32": None, "float64": None, "complex64": None, "complex128": None,
}
#: `func name(...) T {` —— Go 的函数定义。`func` 这个关键字在 C/Rust/Python 里都不出现,
#: 所以它是一条**很干净**的判据。
_GO_FN = re.compile(r"^[ \t]*func[ \t]+([A-Za-z_]\w*)[ \t]*\(([^)]*)\)[ \t]*([^{;\n]*?)[ \t]*\{",
                    re.M)
_GO_STRUCT = re.compile(r"^[ \t]*type[ \t]+([A-Za-z_]\w*)[ \t]+struct[ \t]*\{(.*?)^[ \t]*\}",
                        re.M | re.S)
#: `//export name` —— 只有这样的 Go 函数才是 **C ABI** (cgo 的约定)。
#: 普通 Go 函数走 Go 自己那套调用约定 (与 Rust 的普通 `pub fn` 同理), 照 C ABI 调就是错编。
_GO_EXPORT = re.compile(r"^[ \t]*//[ \t]*export[ \t]+([A-Za-z_]\w*)", re.M)


def _go_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", t.strip())
    if not t:
        return "()"
    if t.startswith("[]"):
        # 切片是 (ptr, len, cap) 三个字 —— Loment 没有对应形态。**不猜成 ptr**:
        # 猜成 ptr 会丢长度, 调用方按什么切? 报"无映射"让上游决定。
        return None
    if t.endswith("...T") or t.startswith("..."):
        return None                          # 变参
    n = 0
    while t.endswith("*"):
        t = t[:-1].strip()
        n += 1
    if n:
        return "ptr"                         # `*T` 一律 ptr (Go 没有别的指针形态好用)
    if t in known:
        return t
    return GO_TYPES.get(t) if t in GO_TYPES else None


def from_go(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "go", mode)
    doc = _blank(_ident(Path(name).stem), "go")
    # **注释要在取完 `//export` 之后再剥** —— 它本身就是一条注释。
    exported = set(_GO_EXPORT.findall(src))
    # **抹注释要保长度**（`_blank_keep_off`，与 `from_c` 同一个手法）：下面取函数正文
    # 是拿**剥离后的下标**去切**原文**，长度一变切出来的就是别处的字节。
    # 2026-09-18 做 Go 那一门时撞到 —— 原先是 `sub(" ", src)`，整份文件的下标全错。
    body = _C_COMMENT.sub(_blank_keep_off, src)
    known: set[str] = set()
    for m in _GO_STRUCT.finditer(body):
        sname, inner = m.group(1), m.group(2)
        fields = []
        for line in inner.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            parts = line.split()
            if len(parts) < 2 or parts[0].startswith("//"):
                continue
            fname = parts[0]
            if not IDENT_RE.match(fname):
                continue
            t = _go_type(" ".join(parts[1:]), mode, known)
            if t is None:
                rep.skip_field(sname, fname, f"字段类型 {' '.join(parts[1:])!r} 无映射")
                continue
            fields.append({"name": fname, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    for m in _GO_FN.finditer(body):
        fn, params, ret = m.group(1), m.group(2), m.group(3).strip()
        # 只有 `//export` 过的才是 C ABI —— 与 Rust 那侧的 `extern "C"` 同一个道理。
        # **`//export` 是源码显式宣称的 C ABI，留**；没写的那种原来记 `"go"`，
        # 那是"因为这是个 Go 文件"的**推断** —— 按 `docs/188` §3 删掉（详见 `from_java`）。
        abi = "c" if fn in exported else None
        if ret.startswith("("):
            rep.skip("fn", fn, "多返回值 —— Potato 是单返回")
            continue
        rt = _go_type(ret, mode, known)
        if rt is None:
            rep.skip("fn", fn, f"返回类型 {ret!r} 无映射")
            continue
        ps, bad = [], None
        #: **同类型可以共享名字**：`a, b int` 与 `a int, b int` 都是这一门收的写法，
        #: 而且前者在真实 Go 里**非常常见**（原先只收后者，于是 `gcd(a, b int)` 整个函数被
        #: 跳过 —— 2026-09-18 做 Go 那一门时撞到）。分辨办法与 `gotrans.params()` 一样：
        #: 只有**名字**的那一段先攒着，等后面那一段 `名字 类型` 把类型带过来。
        pending: list[str] = []
        for raw in [p.strip() for p in params.split(",") if p.strip()]:
            parts = raw.split()
            if len(parts) == 1 and IDENT_RE.match(parts[0]):
                pending.append(parts[0])
                continue
            if len(parts) != 2:
                bad = f"形参 {raw!r} 不是 `名字 类型` 两段"
                break
            pn, pt = parts
            if not IDENT_RE.match(pn):
                bad = f"形参名 {pn!r} 非法"
                break
            t = _go_type(pt, mode, known)
            if t is None:
                bad = f"形参类型 {pt!r} 无映射"
                break
            for n in pending + [pn]:
                ps.append({"name": n, "type": t})
            pending = []
        if bad is None and pending:
            bad = f"形参 {', '.join(pending)!r} 只有名字没有类型"
        if bad:
            rep.skip("fn", fn, bad)
            continue
        ent = {"name": fn, "params": ps, "ret": rt}
        if abi is not None:                 # 只有 `//export` 过的那种才记（见上）
            ent["abi"] = abi
        # ---- 正文（`docs/188` §3 的 `functions[i].body`）：存**整段函数原文**，
        # 给 `lomt_from --impl` 翻成实现用。**没有它这一门就只剩接口单元** ——
        # 而 Go 写法的单元按 §3 是 Loment，该能翻出实现来。
        # `_GO_FN` 的尾巴就是那个 `{`，所以 `close` 由它往下配平；切开的是**原文**
        # （注释与格式都在），靠的是上面那句"抹注释保长度"。
        if abi is None:
            close = _block_end(body, m.end() - 1)
            if close < 0:
                rep.skip("fn", fn, "花括号不配平（原文到这里就断了）")
                continue
            raw = src[m.start():close + 1]
            ent["body"] = raw.strip()
            ent["body_line"] = _body_at(src, m.start(), raw)
        doc["functions"].append(ent)
        rep.ok += 1
    return _finish(doc, rep)


JAVA_TYPES = {
    "byte": "i8", "short": "i16", "int": "i32", "long": "i64",
    # `void` 映 `"()"` —— 与 C 那边**同一个口径** (`C_TYPES["void"]`), 发射时就没有 `-> T`。
    # 2026-09-17 补: 原先 JAVA_TYPES 没有 `void`, 于是**每一个 setter**(真实 Java 里占
    # 相当比例)都报"返回类型 'void' 无映射" —— 那句提示是**错的**: void 完全表示得了,
    # 它只是没被加进来。注意这些方法仍然是 `abi: "java"`, 照样不会发 `extern fn`。
    "void": "()",
    # Java 的 `char` 是 **16 位无符号** (不是 C 的 8 位) —— 映 `u16`, 别照 C 的习惯映 i8。
    "char": "u16", "boolean": "bool",
    "String": "str",
    # float/double 不在 Loment 的类型里。
    "float": None, "double": None,
    # **装箱类型不映** (Integer/Long/Boolean…): 它们**可以为 null**, 而 Loment 的整型
    # 不能。映成 i32 就把"可能没有值"这件事丢了 —— 那正是这套表示层最不该丢的东西。
    # 报"无映射"让人显式处理, 而不是给一个看起来能用的 i32。
    "Integer": None, "Long": None, "Short": None, "Byte": None, "Boolean": None,
    "Character": None, "Float": None, "Double": None,
    "Object": None, "List": None, "Map": None,
}
_JAVA_CLASS = re.compile(r"(?:^|\n)[ \t]*(?:public[ \t]+)?(?:final[ \t]+|abstract[ \t]+)*"
                         r"class[ \t]+([A-Za-z_]\w*)[^{;]*\{")
#: 字段 / 方法 / 常量。三者的区别只在修饰符与尾部形状, 所以分开写更好读。
_JAVA_FIELD = re.compile(r"^[ \t]*(?:(?:public|private|protected|static|final|transient"
                         r"|volatile)[ \t]+)*([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)[ \t]+"
                         r"([A-Za-z_]\w*)[ \t]*(?:=[^;]*)?;[ \t]*$")
_JAVA_METHOD = re.compile(r"^[ \t]*(?:(?:public|private|protected|static|final|abstract"
                          r"|native|synchronized|default)[ \t]+)*"
                          r"(?:<[^>]+>[ \t]+)?([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)[ \t]+"
                          r"([A-Za-z_]\w*)[ \t]*\(([^)]*)\)")
_JAVA_CONST = re.compile(r"^[ \t]*(?:(?:public|private|protected)[ \t]+)?static[ \t]+final[ \t]+"
                         r"([A-Za-z_][\w.]*)[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*(-?\d+)[ \t]*;")
_JAVA_ENUM = re.compile(r"(?:^|\n)[ \t]*(?:public[ \t]+)?(?:final[ \t]+|static[ \t]+)*"
                        r"enum[ \t]+([A-Za-z_]\w*)[^{;]*\{")
_STR_LIT = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def _block_end(text: str, open_idx: int) -> int:
    """`{` 的下标 -> 配对 `}` 的下标; 找不到返回 -1。

    调用方**必须先把注释与字符串字面量剥掉** —— 否则 `"{"` 会把配对算错。
    """
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _class_members(body: str) -> list[tuple[str, str, int]]:
    """类体里**顶层**的成员声明 (跳过方法体、内部类那些嵌套块)。

    **"类体"这个形状 Java 与 C# 是一样的** —— `class X { 字段; 方法() {} }` ——
    所以这一份两门共用（`docs/188` §7.1 的"一份解析器 + 方言表"），不各自抄。

    做法是走一遍花括号配平: 深度 0 上遇到 `;` 或遇到一个完整的 `{...}` 就收一个成员。
    不这么做的话, 方法体里的局部变量会被当成字段 —— 那是**静默把声明抽错**。

    返回 `(归一化文本, 原文, 原文在入参里的起始下标)`。**原文那一份是给
    `functions[i].body` 用的**（`docs/188` §3）—— 归一化把空白压掉了, 拿它当源喂给
    前端会丢格式；更要紧的是 `potato_from` 里那份 `body` 是**剥过注释**的, 直接当源用
    也失真。**下标那一个**是给 `functions[i].body_line` 用的（见 `_body_at`）：正文的
    行号只能从"它在原文里从哪开始"倒推，而成员是**顺序切**出来的（一个成员的原文
    恰好是 `body[起始下标:下一个成员的起始下标]`），所以记下起点就够了。
    """
    out: list[tuple[str, int]] = []
    cur, depth, start, begin = [], 0, None, 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == "{":
            depth += 1
            if depth == 1:
                start = i
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                cur.append(body[start:i + 1])
                out.append(("".join(cur), begin))
                cur, start, begin = [], None, i + 1
        elif c == ";" and depth == 0:
            cur.append(c)
            out.append(("".join(cur), begin))
            cur, begin = [], i + 1
        elif depth == 0:
            cur.append(c)
        i += 1
    return [(" ".join(m.split()), m, off) for m, off in out]


def _lang_type(t: str, mode: str, known: set[str], types: dict) -> str | None:
    """一门花括号语言的类型拼法 -> Loment 类型。**表从外面传进来** ——
    Java 与 C# 的类型集不同（`byte` 一个有符号一个无符号），形状却一样。"""
    t = re.sub(r"\s+", " ", t.strip())
    if not t:
        return None
    if t.endswith("[]"):
        base = _lang_type(t[:-2], mode, known, types)
        # Java/C# 数组是**动态长度**的 —— 与 Loment 的切片 `[T]` 同义 (不是定长 `[T; N]`)。
        return f"[{base}]" if base else None
    if t in known:
        return t
    if "<" in t:                       # 泛型: 元素类型被擦除, 表示层记不住
        return None
    return types.get(t) if t in types else None


class ClassLang:
    """**"函数住在 `class X { … }` 里"这一族**的方言表（`_from_class_lang` 的入参）。

    形状是一样的（字段 / 方法 / 常量 / 枚举四种成员，同一种花括号配平），
    真正不同的只有下面这几张表 —— 所以它们进表，不进 if（`docs/188` §7.1）。
    """

    __slots__ = ("grammar", "types", "cls", "enum", "const", "field", "method")

    def __init__(self, grammar: str, types: dict, cls, enum, const, field, method):
        self.grammar = grammar
        self.types = types
        self.cls = cls
        self.enum = enum
        self.const = const
        self.field = field
        self.method = method


#: 这一族的**壳行**：`namespace X` / `using …;` / `package …;` / `import …;`，
#: 以及只剩一对花括号的行。它们都是**结构**，不是"谁也不认识的内容" ——
#: 类体已经由 `class` / `enum` 两条正则认下来了，没人认那几行壳是正常的。
_SHELL_LINE = re.compile(r"^\s*(?:namespace|using|package|import)(?:[^A-Za-z0-9_]|$)"
                           r"|^\s*[{}]\s*$")


def _from_class_lang(src: str, name: str, mode: str,
                     lang: ClassLang) -> tuple[dict, Report]:
    """**"函数住在 `class X { … }` 里"这一族**的共用引擎：Java 与 C#。

    两门各自的调用约定**都不记进对象**（`docs/188` §3）：`abi` 不再由"这是什么文件"
    推出来，只有源码**显式宣称**外部 ABI 时才记（见 Go 的 `//export`、Rust 的
    `extern "C"`）。所以用 Java/C# 写法写的单元发出来的是 `pub fn`，就是 Loment。

    那这一层还有什么用: **把数据结构带过来**。类 -> `types`、常量 -> `consts`，
    于是 Loment 侧知道对面那块内存长什么样。链接型的语言 (C/Rust/Go) 给的是
    **能直接调的函数**，运行型的给的是**数据形状 + 走桥的签名**。

    **`field` / `method` 两张表也要传进来，不能共用** —— 修饰词集不同
    （C# 的 `internal` / `sealed` / `partial`，Java 的 `synchronized` / `default`）。
    共用的话一条 `internal static int F()` 匹配不上方法那条正则，于是掉进"以 `;`
    结尾 = 字段"那一支然后被丢掉 —— **静默丢一个函数**，正是本项目最不能接受的那种。
    """
    rep = Report(name, lang.grammar, mode)
    doc = _blank(_ident(Path(name).stem), lang.grammar)
    # **抹注释要保长度**（`_blank_keep_off`，与 `from_c` / `from_go` 同一个手法）：
    # 方法正文的**行号**要拿剥离后的下标回原文里数（`functions[i].body_line`），
    # 而一段多行注释被 `sub(" ", …)` 压成一个空格时行号会**整体上移** ——
    # 报出来的行号就又指到别处了，正是这条修复本身要消灭的那种错。
    # （`_STR_LIT` 那一层改的是行**内**的长度，不动换行，所以行号不受它影响。）
    body = _C_COMMENT.sub(_blank_keep_off, _STR_LIT.sub('""', src))
    known: set[str] = set()
    #: 两条正则**认下来**的字符范围。扫完之后剩下的非空白内容就是"谁也不认识"的残渣，
    #: 末尾由 `_c_leftover_lines` 报出来（`#121` / `#75` —— C 那一门在 2026-10-20 的
    #: 批次 1 就补上了，Java / C# 这一族当时漏了）。
    consumed: list[tuple[int, int]] = []
    # ---- Java 的 `enum Name { A, B, C }` —— **不是 class**, 所以上面那圈抓不到它。
    # 原先 Java 的枚举**静默消失** (与 C 那边同一个口子)。
    # 变体可以带构造实参 (`B(1)`) 与体 (`C { … }`), 这里只取**名字**: Potato 的 enums
    # 只有变体名。带参/带体的那些在下面按"保真度损失"记一笔, 不假装它们与裸变体一样。
    for m in lang.enum.finditer(body):
        ename = m.group(1)
        open_idx = body.rindex("{", m.start(), m.end())
        end = _block_end(body, open_idx)
        if end < 0:
            rep.skip("type", ename, "枚举体配平不了 (花括号不配对)")
            continue
        consumed.append((m.start(), end + 1))
        inner = body[open_idx + 1:end]
        vs, lossy = [], False
        for raw in inner.split(","):
            raw = raw.strip().split()[0] if raw.strip() else ""
            raw = raw.split("(")[0].split("{")[0].strip()
            if not raw:
                continue
            if not IDENT_RE.match(raw):
                continue
            vs.append(raw)
        if "(" in inner or "{" in inner:
            lossy = True
        if not vs:
            rep.skip("type", ename, "枚举无变体")
            continue
        doc["enums"].append({"name": ename, "variants": vs})
        # **枚举名要进 `known`** —— 否则类里一个 `private State s;` 字段查不到这个名字,
        # 被当成"无映射"丢掉。2026-09-17 实测: 产物里明明有 `pub enum State`, 而同一个
        # 文件里引用它的字段却没了 (三个 agent 里 Java 那位报的第三条)。
        # 这一圈**排在类那一圈前面**, 所以同文件内"先声明枚举、后引用它"是通的。
        # **类与类之间的先后依赖仍然不通** (A 引用后声明的 B): 那要预扫一遍名字, 而预扫
        # 会把"字段没抽出来、整类被丢掉"的名字也放进去, 于是引用它的字段过不了校验器的
        # "未声明"。宁可维持现状: 少映射要**出声** (`skip_field`), 不是静默。
        known.add(ename)
        if lossy:
            rep.skip_field(ename, "<变体实参/枚举体>", "只取了变体名 (Potato 的 enums 没有值)")
        rep.ok += 1
    for m in lang.cls.finditer(body):
        cname = m.group(1)
        open_idx = body.rindex("{", m.start(), m.end())
        end = _block_end(body, open_idx)
        if end < 0:
            rep.skip("type", cname, "类体配平不了 (花括号不配对)")
            continue
        consumed.append((m.start(), end + 1))
        inner = body[open_idx + 1:end]
        fields, consts, methods = [], [], []
        for mem, mem_raw, mem_off in _class_members(inner):
            mc = lang.const.match(mem)
            if mc:
                consts.append((mc.group(1), mc.group(2), int(mc.group(3))))
                continue
            # **方法要在字段之前判**: 抽象/接口/native 方法**以 `;` 结尾**却带参数表
            # (`public native int j_native(int a);`)。按"以分号结尾 = 字段"先判的话,
            # 它们会掉进字段那一支然后被丢掉 —— 静默丢, 而且丢的恰好是**唯一那类
            # 与外部实现对接的方法**(2026-09-17 实测: Java 的 j_native 一直抽不出来)。
            mm = lang.method.match(mem)
            if mm and "(" in mem and IDENT_RE.match(mm.group(2)):
                methods.append((mm.group(1), mm.group(2), mm.group(3), mem_raw,
                                mem_off))
                continue
            if mem.rstrip().endswith(";"):
                mf = lang.field.match(mem)
                if mf and IDENT_RE.match(mf.group(2)):
                    fields.append((mf.group(1), mf.group(2)))
                    continue
            # **类里这一条谁也不认识**（`#75`）：不是常量、不是字段、也不是方法 ——
            # 原先它**静默消失**（方法头写坏成 `static int bad( { … }`、嵌套类、
            # 静态初始化块、带注解的方法…）。整份单元少一块内容，而 `check` 判 OK。
            rep.skip("member", (mem.split() or ["?"])[0][:24],
                     f"类 {cname} 里这一条不是常量 / 字段 / 方法 —— 这一门只认那三种；"
                     f"原文：{mem[:60]!r}")
        # ---- 常量 (类里先取, 因为它们的类型也会进 known 的判断)
        for ty, cn, val in consts:
            if cn in {c["name"] for c in doc["consts"]}:
                continue
            t = _lang_type(ty, mode, known, lang.types)
            if t is None or t not in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"):
                rep.skip("const", cn, f"常量类型 {ty!r} 不是整型 (Loment 常量只收整型)")
                continue
            doc["consts"].append({"name": cn, "type": t, "value": val})
            rep.ok += 1
        # ---- 类 -> struct
        flds = []
        for fty, fname in fields:
            t = _lang_type(fty, mode, known, lang.types)
            if t is None:
                rep.skip_field(cname, fname, f"字段类型 {fty!r} 无映射")
                continue
            flds.append({"name": fname, "type": t})
        if flds:
            doc["types"].append({"name": cname, "fields": flds})
            known.add(cname)
            rep.ok += 1
        elif fields:
            # **只有"本来有字段、一个都没映上"才算丢。** 一个**没有字段**的类不是丢东西 ——
            # 它只是方法的命名空间，Java / C# 里到处都是这种壳。原先无条件报，
            # 于是每一份 Java / C# 语料都带一条假的 `skip`（`无可用字段`），
            # 而那正好挡住了"转写期的 skip 要报给用户"那一道（见 `front_door`）。
            rep.skip("type", cname, "无可用字段")
        # ---- 方法 -> 函数 (abi=java)
        for rty, mname, params, mem_raw, mem_off in methods:
            if mname == cname:
                rep.skip("fn", mname, "构造器")
                continue
            rt = _lang_type(rty, mode, known, lang.types)
            if rt is None:
                rep.skip("fn", mname, f"返回类型 {rty!r} 无映射")
                continue
            ps, bad = [], None
            for raw in [p.strip() for p in params.split(",") if p.strip()]:
                parts = raw.split()
                if len(parts) < 2:
                    bad = f"形参 {raw!r} 不是 `类型 名字`"
                    break
                pn = parts[-1]
                pt = " ".join(parts[:-1])
                if not IDENT_RE.match(pn):
                    bad = f"形参名 {pn!r} 非法"
                    break
                t = _lang_type(pt, mode, known, lang.types)
                if t is None:
                    bad = f"形参类型 {pt!r} 无映射"
                    break
                ps.append({"name": pn, "type": t})
            if bad:
                rep.skip("fn", mname, bad)
                continue
            # `abi` **不再由"这是什么文件"推**（`docs/188` §3）。那份推断会把
            # "用 Java 写法写的 Loment"当成外国货，于是函数被 ABI 闸门整批挡掉 ——
            # 而它本来就该被翻成 `pub fn`。只有**源码显式宣称**外部 ABI 时才记
            # （见 Go 的 `//export`、Rust 的 `extern "C"`）。
            # 正文（`docs/188` §3 的 `functions[i].body`）：Java 那一侧同一形状。
            # 存**整段方法原文** —— 见 `_java_members` 的注解。
            ent: dict = {"name": mname, "params": ps, "ret": rt}
            if mem_raw.strip():
                ent["body"] = mem_raw.strip()
                # `mem_off` 是**在 `inner` 里**的下标，`inner` 从 `body` 的
                # `open_idx + 1` 开始；而 `body` 是等长抹出来的 ⇒ 它的下标就是
                # 原文的下标（见上面那句"抹注释要保长度"）。
                ent["body_line"] = _body_at(src, open_idx + 1 + mem_off, mem_raw)
            doc["functions"].append(ent)
            rep.ok += 1
    # ---- **谁也不认识的顶层行**（`#121` / `#75`）：类 / 枚举两条正则认下来的范围之外
    # 的非空白内容 —— `use "x.lomt"` / `use bytes`（那是 Loment 语法，这一门没有对应
    # 构造）、散在类外的声明、写坏的顶层形状。原先它们**既不进产物也不进 `skipped`**：
    # 整份单元少一块内容，而 `check` 判 OK。
    for ln, txt in _c_leftover_lines(body, consumed):
        # **壳行不算"谁也不认识的内容"**：`namespace D` / `using System;` / `package …;`
        # / 只剩花括号的行 —— 它们都是**结构**，`class` / `enum` 那两条正则已经把类体认
        # 下来了（见 `_class_members`），壳没有人认是**正常**的。
        # 不排掉的话，一份平常的 C# 文件（`using` + `namespace` + 两层壳的花括号）会因为
        # 这几行被**整份拒掉** —— 而 `loment/cstrans/*.cs` 三份语料全是这个形状。
        # `using` 那一条与翻译器的 `_CS_SHELLS` 同一口径（那里也把它当壳）。
        if _SHELL_LINE.match(txt):
            continue
        rep.skip("decl", (txt.split() or ["?"])[0][:24],
                 f"第 {ln} 行: 顶层这一条没有对应的规则 —— {lang.grammar} 这一门只认 "
                 f"`class` / `enum`（以及类里的常量 / 字段 / 方法）；`use`、散在类外的"
                 f"声明、顶层函数都在子集外（`docs/188` §4）。原文：{txt[:60]}")
    return _finish(doc, rep)


# ---- 两门的方言表（`docs/188` §7.1）。**只列真正不同的东西。**

JAVA_LANG = ClassLang(
    grammar="java", types=JAVA_TYPES,
    cls=_JAVA_CLASS, enum=_JAVA_ENUM, const=_JAVA_CONST,
    field=_JAVA_FIELD, method=_JAVA_METHOD,
)


def from_java(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """Java 源码 -> 形式对象。引擎在 `_from_class_lang`，这里只是把 Java 那张表递过去。"""
    return _from_class_lang(src, name, mode, JAVA_LANG)


# ---------------------------------------------------------------- C#（同一族）
#
# 与 Java **共用** `_from_class_lang`：`class X { 字段; 方法() {} }` 这个形状一样。
# **两处真不同**：
#
#   * 常量是 `const`（Java 是 `static final`）—— 见 `_CS_CONST`，C# 的 `const`
#     **隐含 static**，所以没有 `static` 那一截；
#   * **`byte` 是无符号的**（Java 有符号）—— 见 `CS_TYPES` 里那一行。
#
# `field` / `method` 两张表**必须另给**：修饰词集不同（C# 的 `internal` / `sealed` /
# `partial`，Java 的 `synchronized` / `default`）。共用一张的话，一条
# `internal static int F()` 匹配不上方法那条正则，就掉进"以 `;` 结尾 = 字段"那支被丢掉
# —— **静默丢一个函数**。

CS_TYPES = {
    "sbyte": "i8",
    # **C# 的 `byte` 是 0..255（无符号）**，Java 的 `byte` 是 -128..127（有符号）。
    # 这是这一族里两门**唯一**没落在同一格上的类型（`JAVA_TYPES["byte"] == "i8"`）。
    # 搞反的后果**不是报错而是静默算错**：C# 里 `byte b = 200;` 会变成 -56。
    "byte": "u8",
    "short": "i16", "ushort": "u16",
    "int": "i32", "uint": "u32",
    "long": "i64", "ulong": "u64",
    # C# 的 `char` 是一个 UTF-16 码元 —— 16 位**无符号**整数，不是一个字符串类型
    "char": "u16",
    "bool": "bool",
    # `void` 映 `"()"` —— 与 C/Java 那边**同一个口径**（发射时就没有 `-> T`）
    "void": "()",
    "string": "str",
    # 浮点与十进制不在 Loment 的类型里
    "float": None, "double": None, "decimal": None,
    # `nint`/`nuint` 的宽度随目标变 —— 表示层记不住"多宽"，所以不映
    "nint": None, "nuint": None,
    "object": None, "dynamic": None, "var": None,
}

#: C# 成员声明上的修饰词。**要认得全**：认不出来的成员会被当成"既不是字段也不是方法"
#: 而丢掉，那是静默的。收下并丢掉的那批（`public`…`sealed`）与出现就拒的那批
#: （`abstract`…`async`）都列进来 —— 拒的那批由**翻译器**报错，这一层只负责认出形状。
_CS_MODS = (r"(?:(?:public|private|protected|internal|static|sealed|abstract|virtual"
            r"|override|partial|new|extern|unsafe|readonly|volatile|async)[ \t]+)*")

#: **`class` 与 `struct` 都要收** —— C# 里"一坨有字段的值类型"最自然的写法是
#: `struct`（`class` 是引用类型，`struct` 才是按值传的那个）。这一层管的是**数据形状**
#: （对面那块内存长什么样），两种都是形状；只收 `class` 的话，一份
#: `public struct Frame { public byte tag; }` 会**静默丢掉整个类型**。
#:
#: 与翻译器那一层不冲突：那边的方言表里 `struct` 在 `agg`（拒），而翻译器只看**函数**，
#: 类型根本不经过它。两层各管各的。
_CS_CLASS = re.compile(r"(?:^|\n)[ \t]*(?:(?:public|internal|private|protected|sealed"
                       r"|abstract|static|partial|readonly|ref)[ \t]+)*"
                       r"(?:class|struct)[ \t]+([A-Za-z_]\w*)[^{;]*\{")
#: `enum` 那一圈与 Java 同形（`enum X { A, B }` 在 C/Java/C# 里长得一样）。
_CS_ENUM = re.compile(r"(?:^|\n)[ \t]*(?:(?:public|internal|private|protected)[ \t]+)*"
                      r"enum[ \t]+([A-Za-z_]\w*)[^{;]*\{")
#: `const <类型> NAME = <整数>;`
_CS_CONST = re.compile(r"^[ \t]*(?:(?:public|private|protected|internal)[ \t]+)?"
                       r"const[ \t]+([A-Za-z_][\w.]*)[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*"
                       r"(-?\d+)[ \t]*;")
_CS_FIELD = re.compile(r"^[ \t]*" + _CS_MODS
                       + r"([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)[ \t]+"
                       r"([A-Za-z_]\w*)[ \t]*(?:=[^;]*)?;[ \t]*$")
_CS_METHOD = re.compile(r"^[ \t]*" + _CS_MODS
                        + r"(?:<[^>]+>[ \t]+)?([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)"
                        r"[ \t]+([A-Za-z_]\w*)[ \t]*\(([^)]*)\)")

CSHARP_LANG = ClassLang(
    grammar="csharp", types=CS_TYPES,
    cls=_CS_CLASS, enum=_CS_ENUM, const=_CS_CONST,
    field=_CS_FIELD, method=_CS_METHOD,
)


def from_csharp(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """C# 源码 -> 形式对象。引擎在 `_from_class_lang`，这里只是把 C# 那张表递过去。"""
    return _from_class_lang(src, name, mode, CSHARP_LANG)


# ---------------------------------------------------------------- 语法识别 (按内容)
#
# docs/175 §5 那条: "**非 Loment 源语法的 `.lomt` 文件**" —— 后缀说的是"这是 Loment 的
# 源文件", 而那种文件里装的东西可以是**别的语法**。只按后缀判的话, 一份写着 C 的 `.lomt`
# 会走进 Loment 解析器, 得到 `1:1: 期望 module（文件必须以 module 开头），得到 'int'`
# —— 一条**把人引向错方向**的建议: 它会让你去改那一行的写法, 而那份 C 的语法本来就对,
# 只是它不是 Loment (2026-09-17 用一份 C 装 `.lomt` 实测到的)。
#
# **判据顺序就是优先级**, 先认最专有的特征:
#   ① `module <名字>` / `capability`·`guard`·`excluded` —— Loment 独有
#   ② `def` / `import` / `class`                        —— Python
#   ③ `fn` / `#[…]`                                     —— Rust
#   ④ `#include` 之类预处理指令                          —— C
#   ⑤ 函数定义的**形状** `<类型> <名字>(…)`              —— 放在最后, 因为它是启发式
#
# **它不假装完备**: 认不出就返回空串, 调用方据此报"请用 `--lang` 指明",
# 而不是猜一个再去编 (猜错比认不出更坏 —— 见 lomelf 头注那条"把静默错编改成报错退出")。
_LOMENT_MODULE = re.compile(r"^[ \t]*module[ \t]+[A-Za-z_]\w*", re.M)
_LOMENT_OWN = re.compile(r"^[ \t]*(?:pub[ \t]+)?(?:capability|guard)\b|^[ \t]*excluded[ \t]+\"",
                         re.M)
#: **`class` 在 Python 与 Java 里都出现**, 所以两条判据按**结尾字符**分开:
#: Python 的类是 `class X:` / `class X(Base):`(冒号), Java 的类是 `class X {`(花括号)。
#: 只认"class"这个词会把两种语言混成一种 —— 那是最容易犯、也最难发现的一类错。
#: **`import` 这条要长成 Python 的样子**: `import java.util.List;` 也是 import ——
#: 与 Python 撞车, 而 Python 排在 Java 前面, 于是一份**带 import 的真 Java 文件在内容
#: 兜底时被判成 python**, 接着 `ast.parse` 在 `package a.b;` 上**抛一坨 traceback**。
#: 判别点是**行尾分号**: Python 的 import 语句不以 `;` 结尾 (写了也是罕见病),
#: Java 的必然以 `;` 结尾。所以这里用 `[^\n;]*$` 把带分号的那行排除掉。
#: 2026-09-17 实测撞到 (三个 agent 写语料, Java 那一份一个 `import` 都不敢写)。
_PY_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+\w+"
                     r"|^[ \t]*class[ \t]+\w+[^\n{]*:[ \t]*$"
                     r"|^[ \t]*(?:import|from)[ \t]+\w[^\n;]*$", re.M)
_RS_FN = re.compile(r"^[ \t]*(?:pub[ \t]+)?(?:unsafe[ \t]+)?(?:async[ \t]+)?"
                    r"(?:extern[ \t]+\"[^\"]*\"[ \t]+)?fn[ \t]+\w+", re.M)
#: Go: `func` 这个关键字在 C/Rust/Python/Java 里都不出现, 是一条很干净的判据。
_GO_DEF = re.compile(r"^[ \t]*func[ \t]+\w+", re.M)
#: **`package` 在 Go 与 Java 里都有** —— 按**有没有分号**分开: Go 是 `package main`,
#: Java 是 `package com.example;`。不分开的话一份 Java 源码会被认成 Go
#: (2026-09-17 实测: 加 Java 之后 detect_lang 对 Java 文件返回 'go')。
_GO_PKG = re.compile(r"^[ \t]*package[ \t]+[A-Za-z_]\w*[ \t]*$", re.M)
#: Java: `class` 前面带可见性/修饰符, 或者 `package a.b.c;` 开头。
#: **`import java.util.List;` 这种也是 Java, 但它与 Python 的 `import x` 撞车** ——
#: 所以 Java 只认 `package ...;` 与 `class`/`interface` 声明, 不认裸 `import`。
#:
#: **`enum` 不算 Java 判据**: `enum X { A, B }` 在 **C、Java、Rust 里长得一模一样**
#: (C 只是多一个行尾分号, 而那一行常常换个写法)。把它当 Java 的特征, 一份带枚举的 C
#: 源码就会被判成 java —— 2026-09-17 实测撞到 (给 C 夹具加了个 `enum Mode` 之后
#: `c_8_detected` 立刻红)。判据只认**专有**的东西, 这个不专有。
#: 代价: 只有枚举、没有 class 的 Java 文件认不出 (用户用 `--lang java` 指明)。
_JAVA_DEF = re.compile(r"^[ \t]*(?:public[ \t]+|final[ \t]+|abstract[ \t]+)*(?:class|interface)"
                       r"[ \t]+\w+[^\n{]*\{"
                       r"|^[ \t]*package[ \t]+[\w.]+[ \t]*;", re.M)
#: C#：**只有两条，都挑"专有"的形状**（上面那条纪律：判据只认专有的东西）。
#:
#:   ① `using <大写开头>;` —— C# 的 using **指令**。Java 没有 `using`；
#:      C++ 的 `using namespace std;`（有空格）与 `using std::cout;`（有 `::`）都不匹配；
#:      C++ 的别名 `using Foo = …;` 有个 `=`。⇒ 专有。
#:   ② `static … Main(string[] …)` —— C# 的入口。Java 的入口是小写 `main(String[]`，
#:      而小写 `string` 在 Java 里根本不是类型。⇒ 专有。
#:
#: **`namespace` 故意不当判据** —— C++ 也有 `namespace X {`，拿它认 C# 会把一份 C++
#: 源码判成 csharp，然后**按 C# 去翻**（静默翻错语言，比认不出坏得多）。
#: 代价：一份**没写 using、也没有入口**的 C# 文件认不出，得用 `--lang csharp` 指明 ——
#: `detect_lang` 本来就不假装完备，这条是"宁可认不出，不肯认错"那一侧的。
#: C++：**只认 C 里根本不会出现的东西**（同一条"判据只认专有的"纪律）。
#:
#:   ① `std::`            —— 作用域解析，C 里没有 `::`；
#:   ② `template<`        —— 模板；
#:   ③ `using namespace`  —— C 没有 `using`；
#:   ④ C++ 标准头（`<vector>` / `<iostream>` / …）—— `#include` 在 C 里也有，所以
#:      **只看头名**，而且**要求紧跟 `>`**：`#include <string.h>` 是 **C 头**，
#:      不写那个 `>` 的话 `string` 会把一份 C 源码判成 C++。
#:
#: **必须排在 `_C_PRE` 前面**：C++ 文件一样有 `#include`，而 `_C_PRE` 会把任何带预处理
#: 指令的文件判成 C —— 排在后面的话 C++ 永远轮不到。
#: **`namespace` / `class` 故意不当判据**：C# 也有它们，而那两门有**专有**判据
#: （`_CS_DEF` 排在前面先把 C# 接走），拿它们认 C++ 会让别的语言被按 C++ 翻。
_CPP_DEF = re.compile(
    r"\bstd::"
    r"|^[ \t]*template[ \t]*<"
    r"|^[ \t]*using[ \t]+namespace[ \t]+"
    r"|^[ \t]*#include[ \t]*<(?:iostream|vector|string|map|set|algorithm|memory"
    r"|utility|functional|array|tuple|optional|variant|sstream|fstream|iomanip"
    r"|numeric|queue|stack|deque|list|bitset|unordered_\w+|cstd\w+)>",
    re.M)
_CS_DEF = re.compile(r"^[ \t]*using[ \t]+[A-Z][\w.]*[ \t]*;"
                     r"|^[ \t]*(?:public[ \t]+|private[ \t]+|protected[ \t]+|internal[ \t]+)*"
                     r"static[ \t]+(?:void|int)[ \t]+Main[ \t]*\([ \t]*string[ \t]*\[",
                     re.M)
_C_PRE = re.compile(r"^[ \t]*#[ \t]*(?:include|define|ifdef|ifndef|pragma|endif|elif)\b", re.M)
#: 一行只有"类型 + 名字 + 参数表"(行尾可选 `{`) —— C 的函数定义 (K&R 之后)。
#: 排除 `return`/`if`/`while`/`for`/`switch`/`else` 开头, 免得把语句或调用当定义。
_C_FNDEF = re.compile(r"^[ \t]*(?!(?:return|if|while|for|switch|else)\b)"
                      r"[A-Za-z_][\w \t\*]*\*?[A-Za-z_]\w*[ \t]*\([^;{}\"()]*\)[ \t]*\{?[ \t]*$",
                      re.M)
#: 整份 C **一行写完** (`int add(int a, int b) { return a + b; }`) —— 上面那条要求行尾就是
#: `)`, 所以它漏这一种, 而那是最常见的写法之一。
#:
#: **只认 C 的类型关键字开头**, 这是刻意的: Loment 的一行函数长成 `fn f() { return 1; }`,
#: 而 `fn`/`struct`/`enum`/`const`/`capability` 都不在下面这张表里 —— 于是两种语言不会在这里
#: 撞车。(带 `struct` 的 C 定义走 `_C_STRUCT`, 不靠这一条。)
_C_TYPES_LEAD = ("void", "int", "char", "short", "long", "float", "double",
                 "unsigned", "signed", "static", "inline", "size_t")
_C_ONELINE = re.compile(r"^[ \t]*(?:" + "|".join(_C_TYPES_LEAD) + r")\b"
                        r"[^;{}]*\([^;{}]*\)[ \t]*\{.*\}[ \t]*;?[ \t]*$", re.M)


#: 判语法前先把注释去掉 —— 注释里出现 `fn`/`def`/`module` 会把人骗过去。
#: (C 的 `/* */`、`//`; Python 的 `#` 不是 `_C_PRE` 那种 `#include`, 留着无所谓)
_ANY_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
#: **只有结构体、没有函数**的文件也要认得出 —— 而 `struct X {` 在 Rust 与 C 里都有。
#: 按**字段写法**分: Rust 是 `名字: 类型`, C 是 `类型 名字;`。这是唯一可靠的区分点
#: (`pub` 可有可无, 所以不能靠它)。2026-09-17 加多语法判据时撞到: 一份只有
#: `struct T { x: i32 }` 的 Rust 源四种特征全不命中。
_RS_STRUCT_MARK = re.compile(r"^[ \t]*(?:pub[ \t]+)?struct[ \t]+\w+[^{]*\{[^}]*?"
                             r"^\s*\w+[ \t]*:[ \t]*\w", re.M | re.S)
_C_STRUCT_MARK = re.compile(r"^[ \t]*(?:typedef[ \t]+)?struct[ \t]+\w*[^{]*\{[^}]*?"
                            r"\b[A-Za-z_]\w*[ \t]+[A-Za-z_]\w*[ \t]*;", re.M | re.S)


#: 自然语言写法（`docs/197`）的**内容判据**：**两条一起要**。
#:
#: 一条就够吗？不够，而且方向是**认不出比认错好**：这一门的句子全是日常英文词
#: （`set` / `and` / `is` / `say`），单看某一个词会与别的语言的注释/标识符撞上。
#: `program <名字>` **整行**加 `give back` 一起出，才是这一门独有的签名 ——
#: 与 C# 那两条（`using <大写>;` / `static … Main(string[] …)`）同一条纪律。
_NL_PROGRAM = re.compile(r"^[ \t]*program[ \t]+[A-Za-z_]\w*[ \t]*$", re.M)
_NL_GIVE = re.compile(r"\bgive back\b")


def detect_lang(src: str) -> tuple[str, str]:
    """按**内容**判断源语法。返回 `(语言, 依据)`; 认不出返回 `("", 原因)`。

    语言取值与 `LANGS` 的键一致 (另加 `"loment"` —— 那表示"这本来就是 Loment 源,
    不该走前端")。
    """
    body = _ANY_COMMENT.sub(" ", src)
    if _LOMENT_MODULE.search(body):
        return "loment", "以 `module <名字>` 开头"
    if _LOMENT_OWN.search(body):
        return "loment", "有 `capability` / `guard` / `excluded` —— Loment 独有"
    # **自然语言写法排在很前面**：它的两条判据是**整行 `program <名字>` + `give back`**,
    # 一起出才算命中（见上面的注解）。排前面是因为它下面那几门的兜底判据偏松
    # （`_C_FNDEF` / `_C_ONELINE` 那种形状匹配），而这一门的两条是**专有**的。
    if _NL_PROGRAM.search(body) and _NL_GIVE.search(body):
        return "natural", "有整行 `program <名字>` 与 `give back` —— 自然语言写法独有"
    if _PY_DEF.search(body):
        return "python", "有 `def` / `class` / `import`"
    if _RS_FN.search(body):
        return "rust", "有 `fn`"
    if _GO_DEF.search(body) or _GO_PKG.search(body):
        return "go", "有 `func` / `package`"
    # **C++ 排在 C# / Java 前面**：它的判据（`std::` / `template<` / `using namespace` /
    # C++ 标准头）是**专有**的，而它必须赶在 `_C_PRE` 前面（C++ 一样有 `#include`）。
    if _CPP_DEF.search(body):
        return "cpp", "有 `std::` / `template<` / `using namespace` / C++ 标准头"
    # **C# 要排在 Java 前面**：两门都写 `class X {`，一份 C# 源码对 `_JAVA_DEF` 也是
    # 命中的。而 C# 那两条判据是**专有**的（见 `_CS_DEF` 的注解），所以先问它。
    if _CS_DEF.search(body):
        return "csharp", "有 `using <命名空间>;` 或 `static … Main(string[] …)`"
    if _JAVA_DEF.search(body):
        return "java", "有 `class` / `interface` / `package …;`"
    if _C_PRE.search(body):
        return "c", "有 `#include` 之类预处理指令"
    # 到这里剩下的多半是"只有声明没有函数"的文件 —— 按结构体字段的写法分。
    if _RS_STRUCT_MARK.search(body):
        return "rust", "有 `struct X { 名字: 类型 }` 形状的字段 (Rust 写法)"
    if _C_STRUCT_MARK.search(body):
        return "c", "有 `struct X { 类型 名字; }` 形状的字段 (C 写法)"
    if _C_FNDEF.search(body):
        return "c", "有 `<类型> <名字>(…)` 形状的函数定义, 且没有 `fn`/`def`"
    if _C_ONELINE.search(body):
        return "c", "有整行写完的 C 函数定义 (`<类型关键字> …(…) { … }`)"
    return "", "已知特征都没命中"


def _ident(name: str, fallback: str = "unit") -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not name or not name[0].isalpha() and name[0] != "_":
        name = "_" + name
    return name if IDENT_RE.match(name) else fallback


class Report:
    """转写报告: 实体 = 函数/类型/常量 (字段不计实体, 单独记保真度损失)。"""

    def __init__(self, path: str, language: str, mode: str):
        self.path, self.language, self.mode = path, language, mode
        self.ok = 0
        self.skipped: list[dict] = []
        self.field_notes: list[dict] = []
        self.validate_errors: list[str] = []

    def skip(self, kind: str, name: str, why: str) -> None:
        self.skipped.append({"kind": kind, "name": name, "why": why})

    def skip_field(self, owner: str, field: str, why: str) -> None:
        """类型内字段映射失败: 不计入实体分母, 只记保真度损失。"""
        self.field_notes.append({"owner": owner, "field": field, "why": why})

    @property
    def seen(self) -> int:
        return self.ok + len(self.skipped)

    def as_dict(self, obj_ok: bool) -> dict:
        return {
            "path": self.path, "language": self.language, "mode": self.mode,
            "entities_seen": self.seen, "entities_ok": self.ok,
            "conversion_rate": round(self.ok / self.seen, 4) if self.seen else 0.0,
            "skipped": self.skipped, "fields_skipped": len(self.field_notes),
            "field_notes": self.field_notes, "object_valid": obj_ok,
            "validate_errors": self.validate_errors,
        }


def _finish(doc: dict, rep: Report) -> tuple[dict, Report]:
    errs = potato.validate(doc)
    rep.validate_errors = errs
    return doc, rep


def _blank(unit: str, grammar: str) -> dict:
    """一份空的**合法**对象。

    ## 2026-09-18: 这里原来是**错的**，改掉了

    原文（保留作为记录）：

    > 停在 **v1** 而不是跟着编译器升到 v2：v2 的 `mode` 是"这个 Loment 程序用哪个运行模式"，
    > 而这里的对象描述的是**外源模块**的结构 —— 它**不是一个 Loment 程序**，给它填 `std`
    > 就是**替它声称**一件源里没有的事。

    那条理由建立在"外源代码"那个理解上。用户 2026-09-18 把它更正为**表层语法**
    （`docs/188` §0）：一份用 C / Python / Java 写法写的单元**就是**一个 Loment 程序，
    只是拼法不同。**所以它该有 `mode`、该是 `language: "loment"`**，而"用哪种写法写的"
    是另一件事，记在新字段 `grammar` 上。

    这个 bug **有实际后果**，不是洁癖：`language: "python"` 会被下游当成"外国货"，
    而 `abi: "python"` 会被 `lomt_from` 的 ABI 闸门挡掉 —— 于是用 Python 写法写的单元
    转出来是一份**空 module**（`loment_diag` 里那条"照建议敲会得到空 module"的提示，
    根因就在这儿）。

    **`guards: 0` 不能漏**（2026-09-17 修）：v1 要求 `guards` 是非负整数，而原先这里
    没有这个键 —— 于是每一份对象都是**非法 v1**，`validate_errors` 里一直挂着错误。
    这是"没有判据盯着"的典型：字段被报到报告里，但没有任何测试断言报告是干净的。
    """
    return {
        "potato": "v6", "unit": unit,
        # `language` 说的是"这份东西**是**什么" —— 用别的写法写的，**它仍然是 Loment**。
        "language": "loment",
        # `grammar` 说的是"用什么**写法**写的"（`docs/188`）。两个字面不同，别混。
        "grammar": grammar,
        "imports": [],
        "capabilities": [], "functions": [], "layouts": [], "consts": [], "enums": [],
        "types": [], "traits": [], "impls": [], "generics": [], "instances": [],
        "guards": 0, "excluded": [],
        "mode": "std", "switches": [], "dialects": [], "bodies": [],
    }


# ---------------------------------------------------------------- Python (ast)

def _py_type(node, mode: str) -> str | None:
    if node is None:
        return "i64" if mode == "lenient" else None
    txt = ast.unparse(node) if hasattr(ast, "unparse") else ""
    if txt in PY_TYPES:
        return PY_TYPES[txt]
    return "ptr" if mode == "lenient" else None  # lenient: 未知类型降级为指针


#: 前端**自己**折叠整数表达式, 与 C enum 那处同一口径 (`_C_ENUM` 也要算出值来)。
#: 只收"所有叶子都是整数字面量"的表达式 —— 有一个叶子是名字 (如 `1 << SHIFT`)
#: 就**报出来**, 不猜它的值。
#: `ast.Div` **不在**表里: 它产生 float, 折出来就不是整数常量了。
_PY_FOLD_OPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod,
                ast.LShift, ast.RShift, ast.BitOr, ast.BitAnd, ast.BitXor)
_PY_INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}


def _py_int_literal(node) -> int | None:
    """整数字面量, **含** `-5` / `1 << 4` 这类显然可折叠的形式。折不出来返回 None。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _py_int_literal(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, _PY_FOLD_OPS):
        a, b = _py_int_literal(node.left), _py_int_literal(node.right)
        if a is None or b is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.FloorDiv):
                return a // b
            if isinstance(node.op, ast.Mod):
                return a % b
            if isinstance(node.op, ast.LShift):
                return a << b
            if isinstance(node.op, ast.RShift):
                return a >> b
            if isinstance(node.op, ast.BitOr):
                return a | b
            if isinstance(node.op, ast.BitAnd):
                return a & b
            return a ^ b
        except (ZeroDivisionError, ValueError, OverflowError):
            return None                       # 除零 / 负位移 / 位移过大 -> 当折不出来
    return None


def _py_const(name: str, value, ann, mode: str) -> tuple[dict | None, str]:
    """模块级 `NAME = <整数>` 或 `NAME: int = <整数>` -> 一条常量。

    第二个返回值非空时**调用方一定要出声** —— 这条函数的存在理由就是原先那三种写法
    **一声不响地消失** (2026-09-17 加多语法语料时实测):

      * `LOW = -5`      —— `-5` 是 `UnaryOp`, 不是 `Constant`
      * `MAX: int = 8`  —— `AnnAssign`, 而这是**最 Pythonic 的写法**
      * `SHIFT = 1 << 4` —— `BinOp`

    产物里少三个常量、汇总行一个数都不变、`[skip]` 一行都没有 —— 不看产物根本发现不了。
    **注解写了非整型就报出来**, 不能照值发 `i64`: 那会把作者声明的类型改掉。
    """
    v = _py_int_literal(value)
    if v is None:
        return None, "模块级常量的值不是整数字面量"
    if ann is not None:
        t = _py_type(ann, mode)
        if t not in _PY_INT_TYPES:
            return None, f"常量注解 {t or '无映射'} 不是整型"
    return {"name": name, "type": "i64", "value": v}, ""


def from_python(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "python", mode)
    doc = _blank(_ident(Path(name).stem), "python")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        # **这一门自己就有"读不通"这一档**（`pytrans.PyError`），可这条路原先让它
        # **裸着冒出去** —— 命令行上收到的是一段 Python 的 traceback，不是编译器的话
        # （`#76`；另外五门在这件事上都是干净的拒：`[ERR] <文件>: … 第 N 行: …`）。
        # 换一个只改了**消息**的 `SyntaxError` 重抛，由 `front_errors("python")` 接住、
        # 翻成 `NotRepresentable` —— 与"翻不出来"走同一条出口。
        where = f"第 {e.lineno} 行" if e.lineno else "文件头附近"
        raise SyntaxError(f"{where}: 这份 Python 解析不过 —— {e.msg}") from None
    known: set[str] = set()

    def _put_const(cname: str, cval, cann) -> None:
        c, why = _py_const(cname, cval, cann, mode)
        if c:
            doc["consts"].append(c)
            rep.ok += 1
        else:
            rep.skip("const", cname, why)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields = []
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    t = _py_type(st.annotation, mode)
                    if t is None:
                        rep.skip_field(node.name, st.target.id, "无可用类型映射")
                        continue
                    fields.append({"name": st.target.id, "type": t})
            if not fields:
                rep.skip("type", node.name, "无注解字段")
                continue
            doc["types"].append({"name": node.name, "fields": fields})
            known.add(node.name)
            rep.ok += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
                rep.skip("fn", node.name, "变参/位置参数")
                continue
            params, bad = [], None
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                t = _py_type(a.annotation, mode)
                if t is None:
                    bad = f"参数 {a.arg} 无类型映射"
                    break
                params.append({"name": a.arg, "type": t})
            if bad:
                rep.skip("fn", node.name, bad)
                continue
            ret = _py_type(node.returns, mode)
            if ret is None:
                rep.skip("fn", node.name, "返回类型无映射")
                continue
            #: Python 的调用约定是自己那套 (CPython C-API / 解释器), 不是平台 C ABI ——
            #: 记下来, 让下游知道它**不能**直接发 `extern fn`。要调 Python 走进程桥
            #: (`loment/lib/proc.lomt`, docs/173 §4)。
            # `abi` 按 `docs/188` §3 **不再由"这是什么文件"推** —— 见 `from_java` 那条注释。
            ent: dict = {"name": node.name, "params": params, "ret": ret}
            # ---- 正文（`docs/186` §3 的 `functions[i].body`，Python 这一侧同一形状）。
            # **整段函数原文**，用 `ast.get_source_segment` 取 —— 它是按源码位置切的，
            # 与 C 那侧按下标切同一个道理：**保真**（`int` 注解与 `bool` 注解在 Potato
            # 里可能是同一个宽度的整数，回推必然丢掉用户写的那个）。
            seg = ast.get_source_segment(src, node)
            if seg:
                ent["body"] = seg
                # `get_source_segment` 是从节点的**起始位置**切的（`def` 那一行的
                # `col_offset` 处），所以这一段正文的首行就是 `node.lineno`。
                ent["body_line"] = node.lineno
            doc["functions"].append(ent)
            rep.ok += 1
        # 模块级常量两种写法都收。**`isupper()` 是"这是不是常量"的判据**, 放在这里
        # 而不是 `_py_const` 里 —— 模块级小写赋值是变量, 不是"没转成功的常量", 报它
        # 只会淹掉真正的丢失。
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper():
            _put_const(node.targets[0].id, node.value, None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id.isupper():
            _put_const(node.target.id, node.value, node.annotation)
    return _finish(doc, rep)


# ---------------------------------------------------------------- C (轻量解析)

_C_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_C_STRUCT = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
#: C 的 `enum Name { A, B = 3, C };`。C **没有 enum 类型名以外的身份** ——
#: 变体名是模块级常量, 所以两处都要看:
#:   * 不带值的 -> `enums`(Potato 的 enums 只有变体名)
#:   * **带值的 -> `consts`** —— 值没法塞进 enums 的 schema, 而它常常是**协议常量**,
#:     丢值比丢名严重得多 (2026-09-17 补: 原先 C 的 enum 谁都不收, **静默消失**)。
_C_ENUM = re.compile(r"\benum\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
#: 字段 = `类型 名字;`。**不锚行首** —— 原先锚了 `^\s*`, 于是 `struct P { int a; int b; };`
#: 这种**一行写完的结构体**只抽得到第一个字段 (整个 `int a; int b;` 是一行, `.+?` 只吃一段),
#: 而第二个字段**静默消失**。一行写 struct 在 C 里很常见 (2026-09-17 加多语法判据时撞到)。
_C_FIELD = re.compile(r"([^;{}]+?)\s+([A-Za-z_]\w*)\s*(\[\s*\d+\s*\])?\s*;")
#: 函数**定义或声明**（`类型 名字(...)` 后面跟 `{` 或 `;`）。
#:
#: **不锚行首** —— 原先锚了 `^[ \t]*`（配 `re.M`），于是**同一行上的第二个函数永远匹配不到**：
#: `int f(){ … } int g(){ … }` 里 `g` 整个消失，而且 `skipped` 也是空的（不是"跳过了"，
#: 是**压根没看见**）。C 完全允许这么写。与 `_C_FIELD` 是同一处毛病（那一处已经改过）。
#:
#: 行首锚**本来是为了别匹配到函数体里的调用** —— 那一层改由 `_from_c` 按"已认下的函数体
#: 范围"挡（那里的 `claimed`）：那条更准，因为 `Foo bar(1);` 这类写法在 C++ 里就是**变量
#: 声明**，与函数定义同形，只靠关键字表挡不住。
_C_FN = re.compile(r"(?:static\s+|inline\s+|const\s+)*"
                   r"([A-Za-z_][\w \t\*]*?)\s+([A-Za-z_]\w*)\s*\(([^;{)]*)\)\s*[;{]")
_C_KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else"}


def _c_type(t: str, mode: str, known: set[str], types: dict) -> str | None:
    """一门 C 系语言的类型拼法 -> Loment 类型。**表从外面传** —— C 与 C++ 不同
    （C++ 的 `char` 与 C 一样是**实现定义**的符号性，所以那里要显式映成 `None`）。"""
    t = re.sub(r"\s+", " ", t.strip())
    # 前置限定词先剥掉（`const int v` -> `int v`）。
    # **这一层管的是数据形状，而限定词不改布局** —— `const` / `volatile` 都不动一个字节。
    # 收还是拒是**翻译器**那一层的决定（`docs/186` §6.3 记了 `const` 从"拒"改到"收下并丢掉"），
    # 不该在这里把一份**结构完全画得出来**的头文件整段判成"无映射"。
    # 2026-09-18 做 C++ 那一门时撞到：`in_range(const int v, …)` 一个函数都没进来。
    t = re.sub(r"^(?:(?:const|volatile|static|register)\s+)+", "", t)
    if t.endswith("[]"):
        t = t[:-2] + " *"
    # `enum X` **在 C 里就是一个整型** (标准这么定) —— 映 i32。不认它的话, 凡是收
    # `enum X` 形参的函数**整个被跳过**, 而那在真实 C 里到处都是 (2026-09-17 补)。
    if t.startswith("enum "):
        return "i32"
    if t in types:
        return types[t]
    if t.endswith("*"):
        base = re.sub(r"\s+", " ", t[:-1].strip())
        return "str" if base == "char" else "ptr"
    if t in known:
        return t
    return "ptr" if mode == "lenient" else None


def _blank_keep_off(m: "re.Match[str]") -> str:
    """把一段注释/预处理指令换成**等长**的空白，且**保留其中的换行**。

    这是为 `functions[i].body` 服务的（`docs/186`）：抓正文要按**下标**回原文里切，
    而下标只有在 `body` 与 `src` 逐字节等长时才有意义。原先的 `sub(" ", …)` 把一整段
    多行注释压成一个空格 —— 长度与行号**双双失真**，那样切出来的"正文"是别处的字节。
    """
    return "".join("\n" if ch == "\n" else " " for ch in m.group())


def _body_line(src: str, off: int) -> int:
    """`src` 里下标 `off` 落在第几行（1 起）。**这是 `functions[i].body_line`**
    （见 `_body_at`）。

    只在"**下标 == 原文下标**"时才准 —— 所以取正文那几处的 `src` 都是**等长**抹出来的
    （`_blank_keep_off`）。抹长度一变，这里算出来的行号就指到别处去了，而那正是要治的病。
    """
    return src.count("\n", 0, off) + 1


def _body_at(src: str, start: int, raw: str) -> int:
    """一段正文（`raw`，取自 `src[start:]` 的一个切片）**去空白后**的首行。

    正文存的是 `raw.strip()` —— 领头那些空白**不在这段正文里**，所以行号要往前
    挪过它们（多行注释被抹成空行时，这一截里可能真的夹着换行）。
    """
    return _body_line(src, start + (len(raw) - len(raw.lstrip())))


#: **C++ 的类型表**。与 C 那张**只差 `char` 那一格**（其余同名同义，所以从 C 复制）：
#: **`char` 的符号性在 C++ 里也是实现定义的**（x86-64 上 g++ 是 signed，ARM 上常常不是），
#: 表示层不该猜 —— 猜错的话"对面那块内存里这个字段是 0..255 还是 -128..127"就记反了。
#: C 那边映 `i8` 是**写下来的决定**（x86-64 Linux 上 clang 就是 signed）；C++ 这边
#: 不给映射，让它**出声**（`无映射`），因为 C++ 没有"C 就是 x86-64 Linux"那个默认。
#: `signed char` / `unsigned char` 是**明确**的，照映。
CPP_TYPES = dict(C_TYPES, **{
    "char": None,
    "char *": None, "const char *": None,
    "signed char": "i8", "unsigned char": "u8",
    "long long": "i64", "unsigned long long": "u64",
    "bool": "bool", "void *": "ptr",
})


def _past_semi(body: str, i: int) -> int:
    """从 `i` 起吃掉同一行上的空白与一个可选的 `;`，返回新末尾（给"认下这一条"用）。

    `struct P { … }` / `enum E { … }` 那两条正则到 `}` 就停了，**尾随的 `;` 不在匹配里**
    —— 不补这一下，那个分号会被当成"谁也不认识的顶层内容"报出来。
    """
    j = i
    while j < len(body) and body[j] in " \t":
        j += 1
    return j + 1 if j < len(body) and body[j] == ";" else i


def _c_leftover_lines(body: str, consumed: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """顶层**没有任何规则认下来**的非空白内容 -> `[(行号, 那一行的原文), …]`（一行一条）。

    `docs/186` §4 把"全局变量 / 聚合类型 / 预处理指令"逐条列进**点名拒绝**那一栏，
    而 `_from_c` 只有三条正则（`_C_STRUCT` / `_C_ENUM` / `_C_FN`）扫顶层 ——
    扫不到的既不进产物、也不进 `skipped`。单元于是带着**半份内容**被发出去，
    而 `check` 判 `[OK]`。这一条把残渣找出来（`docs/167`："不静默丢"）。

    输入是**只抹了注释**的那一份（`_from_c` 里的 `nocomment`），不是扫描用的 `body` ——
    后者连 `#` 行也抹掉了，而 `#include` / `#define` **正是要报的东西**。
    两份逐字节等长（抹的是等长空白），所以 `consumed` 的下标在两边通用。
    """
    used = bytearray(len(body))
    for a, b in consumed:
        for i in range(max(a, 0), min(b, len(used))):
            used[i] = 1
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for m in re.finditer(r"[^\s]", body):
        if used[m.start()]:
            continue
        ln = body.count("\n", 0, m.start()) + 1
        if ln in seen:
            continue
        seen.add(ln)
        out.append((ln, body.splitlines()[ln - 1].strip()))
    return out


def _from_c(src: str, name: str, mode: str, grammar: str,
            types: dict) -> tuple[dict, Report]:
    """**C 系那一门**（C / C++）的共用引擎：函数、结构体、枚举、常量四种声明。

    形状一样，差异只在**类型表** —— 所以它当参数传进来（`docs/188` §7.1）。
    这两门都不收顶层常量（`const` 全局量），所以没有 `const_words` 那一档。
    """
    rep = Report(name, grammar, mode)
    doc = _blank(_ident(Path(name).stem), grammar)
    body = _C_COMMENT.sub(_blank_keep_off, src)
    #: **只抹了注释**的那一份。残渣检测要它：扫描用的 `body` 连 `#` 行也抹掉了
    #: （下一行），而 `#include` / `#define` 正是 `docs/186` §4 列进"点名拒绝"那一栏的
    #: 东西 —— 抹掉之后谁也看不见它们。
    nocomment = body
    body = re.sub(r"^[ \t]*#.*$", _blank_keep_off, body, flags=re.M)
    known: set[str] = set()
    #: 三条正则**认下来**的字符范围。扫完之后剩下的非空白内容就是"谁也不认识"的残渣，
    #: 由 `_c_leftover_lines` 报出来（见那一处的注解）。
    consumed: list[tuple[int, int]] = []
    for m in _C_STRUCT.finditer(body):
        consumed.append((m.start(), _past_semi(body, m.end())))
        sname, inner = m.group(1), m.group(2)
        fields = []
        for fm in _C_FIELD.finditer(inner):
            ft, fn, arr = fm.group(1), fm.group(2), fm.group(3)
            if fn in ("if", "while", "for", "return"):
                continue
            t = _c_type(ft, mode, known, types)
            if arr and t:
                t = f"[{t}; {arr[1:-1].strip()}]"
            if t is None:
                rep.skip_field(sname, fn, f"字段类型 {ft!r} 无映射")
                continue
            fields.append({"name": fn, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    # ---- C 的 enum。**整个枚举要么进 `enums`、要么全进 `consts`**, 不拆开 ——
    # 拆开(把带值的那个单拎出来当 const)会造出一个**看着少了一个变体**的枚举, 那是误导。
    # 判据是"有没有任何一个变体带显式值": 有 -> 全按 C 语义算出值、发芽成 consts;
    # 没有 -> 就是一个普通枚举, 进 `enums`。
    for m in _C_ENUM.finditer(body):
        consumed.append((m.start(), _past_semi(body, m.end())))
        ename, inner = m.group(1), m.group(2)
        members, cur, next_v = [], None, 0
        for raw in inner.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if "=" in raw:
                vn, vv = raw.split("=", 1)
                vn, vv = vn.strip(), vv.strip()
                if not IDENT_RE.match(vn) or not re.fullmatch(r"-?\d+", vv):
                    cur = None
                    rep.skip("const", raw, "枚举变体不是 `名字` 或 `名字 = 整数`")
                    continue
                next_v = int(vv)
            else:
                vn = raw
                if not IDENT_RE.match(vn):
                    cur = None
                    rep.skip("const", raw, "枚举变体名非法")
                    continue
            members.append((vn, next_v))
            next_v += 1
        if not members:
            rep.skip("type", ename, "枚举无变体")
            continue
        if all(v == i for i, (_n, v) in enumerate(members)):
            doc["enums"].append({"name": ename, "variants": [n for n, _v in members]})
        else:
            for vn, vv in members:
                if vn in {c["name"] for c in doc["consts"]}:
                    continue
                doc["consts"].append({"name": vn, "type": "i32", "value": vv})
        rep.ok += 1
    #: 已认下的函数体延伸到哪（右花括号之后的**第一个位置**）。`_C_FN` 不锚行首了，
    #: 所以体里那些与定义同形的写法要靠它挡 —— 见那条正则上面的注解。
    claimed = -1
    for m in _C_FN.finditer(body):
        if m.start() < claimed:
            continue                # 落在上一个函数的**体内**：那是调用/声明，不是定义
        rt, fn, params = m.group(1), m.group(2), m.group(3)
        if fn in _C_KEYWORDS or rt.strip().split()[-1] in _C_KEYWORDS:
            continue
        # 是**定义**就把整段范围先认下来 —— 哪怕它随后因为返回类型无映射被跳过：
        # 不然它**体内**的写法会被接着当成函数。
        is_def = m.end() > 0 and body[m.end() - 1] == "{"
        close = _block_end(body, m.end() - 1) if is_def else -1
        if is_def and close < 0:
            rep.skip("fn", fn, "花括号不配平（原文到这里就断了）")
            consumed.append((m.start(), m.end()))
            continue
        if is_def:
            claimed = close + 1
            consumed.append((m.start(), close + 1))
        else:
            consumed.append((m.start(), m.end()))
        if "..." in params:
            rep.skip("fn", fn, "变参")
            continue
        ret = _c_type(rt, mode, known, types)
        if ret is None:
            rep.skip("fn", fn, f"返回类型 {rt!r} 无映射")
            continue
        ps, bad = [], None
        for j, raw in enumerate([p.strip() for p in params.split(",") if p.strip()]):
            if raw == "void":
                continue
            parts = raw.replace("*", " * ").split()
            if len(parts) < 2:
                bad = f"参数 {raw!r} 解析失败"
                break
            pname = parts[-1]
            t = _c_type(" ".join(parts[:-1]), mode, known, types)
            if t is None:
                bad = f"参数类型 {raw!r} 无映射"
                break
            ps.append({"name": _ident(pname, f"a{j}"), "type": t})
        if bad:
            rep.skip("fn", fn, bad)
            continue
        #: C 的函数**按定义**就是 C ABI (`_c_bare` 只收裸函数, 不含 `static` 之类),
        #: 所以这里不是猜。带结构体参数的那些会在 `lomt_from` 那侧被拒 (第 1 阶段只收标量)。
        # `abi` 按 `docs/188` §3 **不再由"这是什么文件"推** —— 见 `from_java` 那条注释。
        # 【真外国的 C】那条路是**源码里显式写的** `extern fn`，由 `lomentc.emit_potato`
        # 记 `abi`；不是从"这份文件后缀是 .c"推出来的。
        ent: dict = {"name": fn, "params": ps, "ret": ret}
        # ---- 正文（`docs/186`）：有体的函数把**原文**带上，没有的（声明）不带这个字段。
        #
        # **可选子字段**，与 `abi` 同一条先例（`docs/179` §3.1）：省略合法、给了必须认。
        # 不认识它的消费者忽略它就是对的 —— 它们本来也只按签名用这份对象（接口单元那条路）。
        #
        # 存的是**整段函数原文**（签名 + 体），不是只存 `{…}`：签名那边 Potato 记的是
        # **映射后的** Loment 类型名（`i32`），从 `i32` 反推回 C 的拼法是另一张表，
        # 而原文本来就在手边。另一个理由更要紧 —— **原文是保真的**：`unsigned` 与
        # `unsigned int` 在 Potato 里都是 `u32`，回推必然丢掉用户写的那个拼法。
        if is_def:
            raw = src[m.start():close + 1]
            ent["body"] = raw.strip()
            ent["body_line"] = _body_at(src, m.start(), raw)
        doc["functions"].append(ent)
        rep.ok += 1
    # ---- **前向声明并进它的定义**。
    #
    # C 里 `int g(int);` 后面跟 `int g(int x) { … }` 是**最常规**的写法（互相递归的两
    # 个函数只能这么写），对象里于是一个名字有**两条**记录：一条有正文、一条没有。
    # 不并的话 `lomt_from` 报"**函数 g 重复**" —— 一个**假的重复**：用户只写了一份定义。
    # 两条**都有正文**的才是真重复，那种照旧留给下游报。
    _by_name: dict[str, list[dict]] = {}
    for ent in doc["functions"]:
        _by_name.setdefault(ent["name"], []).append(ent)
    _merged: list[dict] = []
    for _ents in _by_name.values():
        _with = [x for x in _ents if "body" in x]
        # **至多一条有正文时才并**（有正文的那条留下；一条都没有就留第一条声明）。
        # 这样三种形状各得其所：
        #   声明 + 定义     -> 并成一条（真定义）
        #   两条都只是声明  -> 并成一条（下游说"只有声明没有定义"，那是对的）
        #   **两条都有正文** -> 不并（那是**真重复**，一并就把一份写重复了的源
        #                        悄悄收下了 —— 那比报错坏）
        if len(_ents) > 1 and len(_with) <= 1:
            _merged.append(_with[0] if _with else _ents[0])
            continue
        _merged.extend(_ents)
    doc["functions"] = _merged
    # ---- 残渣：顶层还有"谁也不认识"的东西 -> **报出来**，别让半份单元悄悄发出去
    for ln, txt in _c_leftover_lines(nocomment, consumed):
        rep.skip("decl", (txt.split() or ["?"])[0][:24],
                 f"第 {ln} 行: 顶层这一条没有对应的规则 —— Stage A 只认函数 / `struct` / "
                 f"`enum`；`typedef`、全局量、`union`、预处理指令都在子集外"
                 f"（`docs/186` §4）。原文：{txt[:60]}")
    return _finish(doc, rep)


def from_c(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """C 源码 -> 形式对象。引擎在 `_from_c`，这里只是把 C 那张表递过去。"""
    return _from_c(src, name, mode, "c", C_TYPES)


def from_cpp(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """C++ 源码 -> 形式对象（`docs/188` §7.1）。引擎同上，表是 C++ 那张
    —— 与 C 只差 `char` 那一格，见 `CPP_TYPES`。"""
    return _from_c(src, name, mode, "cpp", CPP_TYPES)


# ---------------------------------------------------------------- Rust (轻量解析)

_RS_STRUCT = re.compile(r"\b(?:pub\s+)?struct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
_RS_ENUM = re.compile(r"\b(?:pub\s+)?enum\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
# `extern "C" fn` 里 `extern "C"` 夹在修饰符和 `fn` 之间 —— 原先的模式要求 `fn` 紧跟修饰符,
# 于是**恰恰是那些真有 C ABI 的函数被漏掉**(2026-09-17 撞出来的)。捕获那个 ABI 串 (§5)。
_RS_FN = re.compile(r"^[ \t]*(?:pub\s+)?(?:const\s+)?(?:unsafe\s+)?(?:async\s+)?"
                    r"(?:extern\s+\"([^\"]*)\"\s+)?fn\s+"
                    r"([A-Za-z_]\w*)\s*(<[^>]*>)?\s*\(([^{)]*)\)\s*(?:->\s*([^{]+?))?\s*\{",
                    re.M)
#: Rust 的 `pub const NAME: TYPE = VALUE;`。**必须要求名字后面有冒号** ——
#: `const fn f()` 也是 `const` 开头, 靠那个冒号把它挡在外面 (它由 `_RS_FN` 接走)。
#: `static` **不算常量** (可变状态), 不收。
_RS_CONST = re.compile(r"^[ \t]*(?:pub(?:[ \t]*\([^)]*\))?[ \t]+)?const[ \t]+"
                       r"([A-Za-z_]\w*)[ \t]*:[ \t]*([^=;]+?)[ \t]*=[ \t]*([^;]+);", re.M)
_RS_INT_RANGE = {
    "i8": (-128, 127), "i16": (-32768, 32767),
    "i32": (-(2 ** 31), 2 ** 31 - 1), "i64": (-(2 ** 63), 2 ** 63 - 1),
    "u8": (0, 255), "u16": (0, 65535), "u32": (0, 2 ** 32 - 1), "u64": (0, 2 ** 64 - 1),
}


def _rs_int_literal(txt: str) -> int | None:
    """Rust 整数字面量: `8` / `0xFF` / `0b1010` / `1_000` / `8u32`。

    **只认字面量, 不认表达式** —— `_RS_CONST` 那处实测的 14 个常量全是字面量。折不出来
    就返回 None, 由调用方**报出来**: 这条函数不假装能算 Rust 的常量表达式。
    """
    t = txt.strip().replace("_", "")
    t = re.sub(r"(?:u|i)(?:8|16|32|64|128|size)$", "", t)   # 后缀 8u32 / 8usize
    try:
        return int(t, 0)
    except ValueError:
        return None


def _rs_const(cname: str, ctype: str, cval: str, mode: str,
              known: set[str]) -> tuple[dict | None, str]:
    """一条 Rust 常量。第二个返回值非空时**调用方一定要出声**。

    2026-09-17 补: 原先 `from_rust` 只有 struct/enum/fn 三条循环, **常量一条都不抽** ——
    语料里 5 个文件共 14 个 `pub const` 既不进产物也**不报 `[skip]`**, 汇总行一个数都不变。
    """
    v = _rs_int_literal(cval)
    if v is None:
        return None, f"常量值 {cval.strip()!r} 不是整数字面量"
    t = _rs_type(ctype, mode, known)
    if t not in _RS_INT_RANGE:
        return None, f"常量类型 {ctype.strip()!r} 不是整型"
    lo, hi = _RS_INT_RANGE[t]
    if not lo <= v <= hi:
        return None, f"常量值 {v} 超出 {t} 的范围"
    return {"name": cname, "type": t, "value": v}, ""


def _rs_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"&'\w+\s*", "&", t.strip())  # 去生命周期
    if t.startswith("&mut [") and t.endswith("]"):
        inner = _rs_type(t[6:-1], mode, known)
        return f"mut [{inner}]" if inner else None
    if t.startswith("&[") and t.endswith("]"):
        inner = _rs_type(t[2:-1], mode, known)
        return f"[{inner}]" if inner else None
    if t in ("&str", "str"):
        return "str"
    if t.startswith("&mut ") or t.startswith("&"):
        return "ptr" if mode == "lenient" else None
    if t.startswith("*const ") or t.startswith("*mut "):
        return "ptr"
    if t.startswith("[") and t.endswith("]"):
        if ";" in t:
            elem, n = t[1:-1].rsplit(";", 1)
            elem, n = elem.strip(), n.strip()
            if not n.isdigit():
                return "ptr" if mode == "lenient" else None
            et = _rs_type(elem, mode, known)
            return f"[{et}; {n}]" if et else None
        et = _rs_type(t[1:-1], mode, known)
        return f"[{et}]" if et else None
    if t in RS_TYPES:
        return RS_TYPES[t]
    if t in known:
        return t
    return "ptr" if mode == "lenient" else None


def from_rust(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "rust", mode)
    doc = _blank(_ident(Path(name).stem), "rust")
    body = re.sub(r"//[^\n]*", "", src)
    known: set[str] = set()
    for m in _RS_STRUCT.finditer(body):
        sname, inner = m.group(1), m.group(2)
        fields = []
        for line in inner.split(","):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            fn, ft = line.split(":", 1)
            fn, ft = fn.strip(), ft.strip()
            # **`pub x: i32` 里的 `pub` 要剥掉** —— Rust 的结构体字段大多数是 `pub`,
            # 不剥的话 `IDENT_RE.match("pub x")` 失败, 整个字段**静默消失**
            # (2026-09-17 加多语法判据时撞到: `pub struct Pt { pub x: i32, pub y: i32 }`
            # 抽出来 0 个字段)。`pub(crate)` 那种也一起剥。
            fn = re.sub(r"^(?:pub(?:\s*\([^)]*\))?\s+)+", "", fn).strip()
            if not IDENT_RE.match(fn):
                continue
            t = _rs_type(ft, mode, known)
            if t is None:
                rep.skip_field(sname, fn, f"字段类型 {ft!r} 无映射")
                continue
            fields.append({"name": fn, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    for m in _RS_ENUM.finditer(body):
        ename, inner = m.group(1), m.group(2)
        if "(" in inner or "{" in inner:
            rep.skip("type", ename, "带载荷变体")
            continue
        vs = [v.strip() for v in inner.split(",") if IDENT_RE.match(v.strip())]
        if not vs:
            rep.skip("type", ename, "无变体")
            continue
        doc["enums"].append({"name": ename, "variants": vs})
        known.add(ename)
        rep.ok += 1
    for m in _RS_CONST.finditer(body):
        c, why = _rs_const(m.group(1), m.group(2), m.group(3), mode, known)
        if c:
            doc["consts"].append(c)
            rep.ok += 1
        else:
            rep.skip("const", m.group(1), why)
    for m in _RS_FN.finditer(body):
        abi_g, fn, generics, params, ret = (m.group(1), m.group(2), m.group(3),
                                            m.group(4), m.group(5))
        #: 只有 `extern "C"` 才是 C ABI —— **不能拿普通 `pub fn` 当 FFI 声明**:
        #: 它的 ABI 是 Rust 自己的, 照 C ABI 调就是错编。这个判断只有源语言这侧做得了,
        #: 所以 ABI 记进对象 (§5), 由 `lomt_from` 决定能不能发 `extern fn`。
        # `extern "C"` 是**源码显式宣称**的，留；没有它的原来记 `"rust"`，那是推断 ——
        # 按 `docs/188` §3 删掉（详见 `from_java`）。
        abi = "c" if (abi_g or "").strip() == "C" else None
        if generics:
            rep.skip("fn", fn, "泛型函数")
            continue
        if "..." in params or "self" in params.split(",")[0].strip():
            rep.skip("fn", fn, "方法/变参")
            continue
        ps, bad = [], None
        for j, raw in enumerate([p.strip() for p in params.split(",") if p.strip()]):
            if ":" not in raw:
                bad = f"参数 {raw!r} 无类型"
                break
            pn, pt = raw.split(":", 1)
            pn, pt = pn.strip(), pt.strip()
            if pn.startswith("mut "):
                pn = pn[4:].strip()
            if not IDENT_RE.match(pn):
                bad = f"参数名 {pn!r} 非法"
                break
            t = _rs_type(pt, mode, known)
            if t is None:
                bad = f"参数类型 {pt!r} 无映射"
                break
            ps.append({"name": pn, "type": t})
        if bad:
            rep.skip("fn", fn, bad)
            continue
        r = _rs_type(ret, mode, known) if ret else "()"
        if r is None:
            rep.skip("fn", fn, f"返回类型 {ret!r} 无映射")
            continue
        ent = {"name": fn, "params": ps, "ret": r}
        if abi is not None:                 # 只有 `extern "C"` 的那种才记（见上）
            ent["abi"] = abi
        doc["functions"].append(ent)
        rep.ok += 1
    return _finish(doc, rep)


# ------------------------------------------------- 自然语言写法（docs/197）

def from_natural(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """**自然语言写法 -> Potato 形式对象**（`docs/197`）。

    这一门与另外六门有一处**结构上的不同**，值得写下来：那六门是"别人的语言"，
    所以它们先被拆成"声明 + 正文"，正文再交给各自的翻译器；这一门**自己就是 Loment**，
    所以它的正文是**原样存下来的整段函数**（`to … end`），由 `nltrans.translate` 回头
    再读一遍。这一层看着多余，其实不能省 —— 它是"**正文逐字节保真**"那条判据的落点：
    `--impl` 走的是 `body`，而 `body` 就是原文的切片，一个字节都不动。

    **读不通就抛**（`nltrans.NaturalError`），不往 `rep.skipped` 里塞 —— 那种塞法会让
    `emit_lomt` 发出一份"没有函数的单元"而**照样绿**，正是本仓最不能接受的那类失败。
    翻成响亮的话由 `front_door` 那一层做（与 `docs/188` §7.2 那条"前门拒的三档不许是
    traceback"同一个位置）。
    """
    import nltrans  # 懒加载：`potato_from` 被很多地方 import，翻译器不是每条路都要
    prog = nltrans.parse_program(src)
    unit = _ident(prog.unit or Path(name).stem)
    doc = _blank(unit, "natural")
    rep = Report(name, "natural", mode)

    #: **对象里只放 `lomt_from` 真正会发的东西**（`docs/197` §5）：
    #: `module` / 能力域 / 常量 / `excluded` / 函数。其余（`choose` / `use` / 结构体 /
    #: 枚举 / trait / impl）**不能进对象** —— 不是漏了，是 `lomt_from._check_representable`
    #: 明确拒收（`imports` / `types` / `enums` / `traits` / `impls` 非空就报"L1 那侧的
    #: 对应形状还没接上"）。它们**由 `translate` 自己发**，走的也是同一条正文管道。
    if prog.mode_decl is not None:
        doc["mode"] = "no_std" if prog.mode_decl.text.endswith("no_std") else "std"
    for c in prog.consts:
        if not c.pub:
            continue                      # 私有常量不进对象，由翻译器自己发（见下）
        doc["consts"].append({"name": c.name, "type": c.ty, "value": c.value})
        rep.ok += 1
    for cname, space, lo, hi, rev, _line in prog.caps:
        doc["capabilities"].append({
            "name": cname, "domain": {"space": space, "lo": lo, "hi": hi},
            "revocable": rev})
        rep.ok += 1
    for what, _line in prog.excluded:
        doc["excluded"].append(what)

    #: **声明也走 `functions` 这条管道** —— 它是一节里唯一能把"行号对齐"做对的地方
    #: （`lomt_from._join_bodies` 按 `body_line` 垫空行）。名字是合成的，正文是那段
    #: 声明的**原文**；`translate` 读到它就知道那是什么（它按第一个词分派）。
    #: 不这么做的话，两条路会把行号算成两个数 —— 而"错要指在错的地方"是本仓的纪律。
    carriers: list[tuple[int, dict]] = []
    for i, d in enumerate(prog.uses):
        carriers.append((d.line, _carrier(f"use_{i}", d.text, d.line)))
    if prog.mode_decl is not None:
        carriers.append((prog.mode_decl.line,
                         _carrier("choose", prog.mode_decl.text, prog.mode_decl.line)))
    for st in prog.structs:
        carriers.append((st.line, _carrier(f"struct_{st.name}", st.text, st.line)))
    for en in prog.enums:
        carriers.append((en.line, _carrier(f"enum_{en.name}", en.text, en.line)))
    for tr in prog.traits:
        carriers.append((tr.line, _carrier(f"trait_{tr.name}", tr.text, tr.line)))
    for im in prog.impls:
        carriers.append((im.line,
                         _carrier(f"impl_{im.trait}_{im.target}", im.text, im.line)))
    for c in prog.consts:
        if c.pub:
            continue
        # 私有常量：原文那句 `remember … , only here`（翻译器读到它才发 `const`）
        carriers.append((c.line, _carrier(f"const_{c.name}",
                                          _remember_text(src, c.name), c.line)))

    entries: list[tuple[int, dict]] = list(carriers)
    for f in prog.fns:
        entries.append((f.line, {
            "name": f.name,
            "params": [{"name": n, "type": t} for n, t in f.params],
            "ret": f.ret,
            # 正文 = **整段函数原文**（`to …` 一路到 `end`）。与 `from_go` 那条一样，
            # 它自带函数头 —— `lomt_from` 是拿它重翻一遍，不是拿它当"块"。
            "body": f.body,
            "body_line": f.line,
        }))
        rep.ok += 1
    for f in prog.externs:
        # **无正文 + `abi: "c"`** —— `emit_lomt` 那条路正是为这个形状写的（`docs/173`）：
        # 有正文的走 `--impl`，没有正文而 `abi` 是 C ABI 的发 `pub extern fn`。
        # 签名超出 FFI 第 1 阶段时 `emit_lomt` 会把它记进 `skipped`，`front_door`
        # 随即**响亮地拒**（不会发出一份少了声明的单元）。
        entries.append((f.line, {
            "name": f.name,
            "params": [{"name": n, "type": t} for n, t in f.params],
            "ret": f.ret,
            "abi": "c",
        }))
        rep.ok += 1
    # **按行号排序**：`_join_bodies` 是"按给定的顺序垫空行"的，顺序错了后面的
    # 每一段行号都会漂（而它垫的是**往前**，所以逆序会让后来的段挤到同一行上）。
    for _line, ent in sorted(entries, key=lambda kv: kv[0]):
        doc["functions"].append(ent)
    return _finish(doc, rep)


def _carrier(name: str, text: str, line: int) -> dict:
    """一条**只带原文**的"函数"条目 —— 声明借它走正文那条管道（见上面的注解）。"""
    return {"name": f"__nl_{name}", "params": [], "ret": "()",
            "body": text, "body_line": line}


def _remember_text(src: str, name: str) -> str:
    """把一条 `remember …` 的原文从句柄里取回来（私有常量那条路要用它）。

    只在**整行**匹配时返回那一行；匹配不上就退回一个**读得出来的**最小句子 ——
    宁可退化成"能重读到类型"的那一句，也不要凭空编一段原文。
    """
    m = re.search(rf"^[ \t]*remember[ \t]+{re.escape(name)}[ \t]+as[ \t]+[0-9]+[^\n]*$",
                  src, re.M)
    return m.group(0).strip() if m else f"remember {name} as 0, only here"


def front_errors(lang: str) -> tuple:
    """**前门该替哪一门接住它自己的异常。**

    别的几门读不通时会抛各家的异常，而 `front_door` 那一条路会一路冒到命令行上变成
    traceback（`docs/188` §7.2 治过一次同样的病）。这一门自己就声明清楚了：
    "写法不对"（`NaturalError`）与"子集外"（`Unsupported`）都该被翻成
    `lomt_from.NotRepresentable` —— 那句"这份单元表示不出来"正是它们的语义。
    """
    if lang == "natural":
        import nltrans  # noqa: PLC0415
        return (nltrans.NaturalError, nltrans.Unsupported)
    if lang == "python":
        # `ast.parse` 的语法错。**必须在这里接住**：这条路上 Python 的解析器是我们
        # 自己调的，它抛的异常与另外五门那几种（`Unsupported` / `CError` …）不同族 ——
        # 不接就是"用户看到一段 Python traceback"（`#76`）。
        return (SyntaxError,)
    return ()


# ---------------------------------------------------------------- CLI

LANGS = {"python": from_python, "c": from_c, "rust": from_rust,
         "go": from_go, "java": from_java, "csharp": from_csharp, "cpp": from_cpp,
         "natural": from_natural}
#: 后缀 -> 语言。**一个语言可以有好几个后缀**（`.cc`/`.cxx` 都是 C++ 的常见写法）。
#: `.nl` 只给 `natural` 一个人（`docs/197 §2`）—— 那一门没有"现成的后缀"可借，
#: 所以按它自己的名字定一个。
EXT = {".py": "python", ".c": "c", ".h": "c", ".rs": "rust", ".go": "go",
       ".java": "java", ".cs": "csharp",
       ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hxx": "cpp",
       ".nl": "natural"}
#: `abi` 的取值域 (与 `potato.ABIS` 对齐): `c` = 平台 C ABI, 能发 `extern fn`;
#: 其余都是**运行时那一族**, 走进程桥 (docs/173 §4)。


# ------------------------------------------------- `choose write grammar` 声明
#
# `docs/188` §1：源里可以声明"这份是用哪种**写法**写的"，于是**读法由声明决定**，
# 而不是靠嗅探（`detect_lang` 那套 `#include` / `def ` 的启发式）。兜底从"猜"变"拒绝"。
#
# **出厂锁**（`docs/188` §1.1）：取值表由官方给出 —— **用户不能扩展、不能覆盖、
# 不能解锁**。所以它是一张**写死的**表，不是可配置项。
# （与 `docs/182` §3 那个"装进本机、用户能 `chooseunlock`"的锁**不是一回事**，别混：
# 那个至今未实现，而这个**现在就能做**，正因为它没有"本机状态"。将来可以**哈希**把它
# 钉死 —— 那一步留给"发行版钉住工具链"时做。）
#
# **别名表只有一处**（与"判语法的规则只有一处"同一条纪律）：右列就是**规范名**，
# 必须恰好是 `potato.GRAMMARS` 那个集合 —— 判据钉着（`loment_grammar_test`）。
# 源侧宽松（`py` / `python` 都收）、对象侧只许一个拼法，否则同一份源出两串字节。
GRAMMAR_ALIASES: dict[str, str] = {
    "loment": "loment",
    "c": "c",
    "py": "python", "python": "python",
    "java": "java",
    "cs": "csharp", "csharp": "csharp", "c#": "csharp",
    "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "cc": "cpp",
    "go": "go", "golang": "go",
    "rs": "rust", "rust": "rust",
    # 自然语言写法（`docs/197`）。**规范名是 `natural`，不是 `lument`** ——
    # 那是刻意选的：`loment` 与 `lument` 只差一个字母，而 `grammar loment` 是**合法**
    # 的（= 原生写法）。把近邻词当规范名，代价是"少打一个字母就静默换成另一种读法"，
    # 而这正是这一门最不该有的失败。`lument` 仍收（它是这次设计的委托名），
    # 但不进对象 —— 对象侧只许 `potato.GRAMMARS` 里那几个。
    "nl": "natural", "natural": "natural", "lument": "natural",
}

#: **读法就是原生语法的那几个规范名。**
#:
#: `loment` 是显然的那一个。**`rust` 也在这里** —— 用户 2026-09-22 的裁定：
#: **「rust 语法是 Loment 基础语法，不需要翻译」**。本语言的原生拼法本来就是 Rust 风味
#: （`CLAUDE.md` 那条"不发明语法、规避 LLM 零语料"买到的就是它），所以
#: `choose write grammar rust` 说的是"**按基础语法读这份 Loment**"，
#: 而不是"把一份外国 Rust 模块抽成接口"。
#:
#: **一处刻意的不对称，要说清**（`docs/188` §2 那条分工）：**后缀** `.rs` 与
#: **`--lang rust`** 照旧走 `from_rust`（`docs/179` 抽接口那条路 —— `kernel/src/*.rs`
#: 与 `lompotc --rust` 那条孪生判据都靠它）。两者不冲突：**后缀说"这是哪个语言的文件"，
#: 声明说"这份 Loment 用哪种写法"**。于是 `.lomt` 里写 `grammar rust` 是原生读法，
#: 而一份 `.rs` 仍然是"Rust 的文件，抽它的接口"。
NATIVE_GRAMMARS = ("loment", "rust")

#: **声明的词序**：`choose write grammar <别名>`。
#:
#: **这是与报错器共享的一份契约，不是随便三个词。** 报错器是个**独立的 Loment 程序**
#: （`loment/tools/lomenterr.lomt` 的 `decl_at`），它问不到 `potato_from`。
#:
#: **它已经走导出管线了 —— 一处改、下游全跟**：
#:
#:     这个常量 -> 下面三条正则 -> `--dump-surface` 的 `decl_word(i)` -> 渲染器逐词吃
#:
#: 所以"改词序"只有**这一处**可改（改完要重新生成 `surface_data.lomt`，那边有判据钉新鲜度）。
#: 这条管线是 loment-dev-86 拉直的（`14cb902`）；我原先主张"两边各钉一条判据就够"，
#: **那个主张是错的** —— 判据只能**事后抓**，共享常量是**防**；而且别名表早就走了这条
#: 管线，词序不走才是**不一致**。
#:
#: 两边各有一条判据仍然留着，但它们钉的不是同一件事：这边钉**这个常量的语义**
#: （`test_the_declaration_word_order_is_the_shared_contract`，含词边界与别名边界），
#: 那边钉**渲染器读得到**。
GRAMMAR_DECL_WORDS = ("choose", "write", "grammar")

#: `choose write grammar <别名>`。别名那一格**不收 `;`**（`grammar python;` 也收），
#: 但**收 `#`** —— `c#` 是个别名，把它当注释头切掉的话那个拼法就用不了了。
#: （"读到空白或 `;` 为止"这条也是契约的一部分 —— 报错器那边同一处也是这么切的。）
_GRAMMAR_HEAD = r"[ \t]+".join(GRAMMAR_DECL_WORDS)
#: 三个词之后**只认空白 / `;` / 行尾** —— 与报错器 `decl_at` 那处**同一刀**
#: （它读完三个词也要求"后面是空白或 `;` 或行尾"）。
#:
#: **这一格从 `\b` 改成这个显式集合，是"与独立写的参照对拍"逼出来的**（见
#: `loment_grammar_test` 那条枚举判据）：`\b` 会把 `choose write grammar#python`
#: 也当成"声明头成立"，于是报"后面要写语法名"；而 `decl_at` 那边判**没有声明**、
#: 退回嗅探。**两边对同一份源说不同的话** —— 正是这条线要防的那类分歧。
#: 现在两边同一条规矩：非空白非 `;` 的字符接在 `grammar` 后面 ⇒ **根本没有声明头**。
_GRAMMAR_TAIL = r"(?=[ \t;]|$)"
#: 行首那一格 —— **允许一个 UTF-8 BOM**。
#:
#: Windows 的编辑器（记事本、"另存为 UTF-8"、PowerShell 的 `>`）默认会写 BOM，
#: 而原先那四条正则都是 `^[ \t]*`：BOM 不是空白，`choose` 就不在"行首"了 ⇒
#: **声明整个找不到**。于是文件落到"缺声明 = 原生 Loment"那一条，整份 C 被当 Loment 编，
#: 报出来的是 `1:1 非法字符 '﻿'` —— 用户完全看不出"是你那行声明没生效"。
#: 而在本仓的平台上带 BOM 是常态（`docs/188` §2 又把"读法"整个压在那一行上）。
#: 抹白是**等长**的，所以 BOM 会被换成空白、行号与偏移都不动。
_GRAMMAR_LEAD = r"^(?:\ufeff)?[ \t]*"
_GRAMMAR_ANY = re.compile(_GRAMMAR_LEAD + _GRAMMAR_HEAD + _GRAMMAR_TAIL, re.M)
#: 别名**必须空白分隔**：`choose write grammar;python` 里的 `;python` **不是**别名
#: （那是"头写了、别名没写"⇒ 报"后面要写语法名"）。别名本体到空白或 `;` 为止 ——
#: 所以 `c#` 里的 `#` 是别名的一部分，不是注释头。
_GRAMMAR_DECL = re.compile(_GRAMMAR_LEAD + _GRAMMAR_HEAD + _GRAMMAR_TAIL + r"[ \t]+([^\s;]+)",
                           re.M)
#: **整行**（含别名，到行尾）—— 抹的时候要抹干净，只抹前三个词会留下 `python` 那一截。
#:
#: **尾巴那条边界不能省**（`af6ee7b` 漏了它，是随后的对拍抓出来的）：`.join(...)` 拼出来的
#: 头是个**纯字面**，而尾巴又是**可选**的 —— 于是 `choose write grammars python`
#: （拼错一个字母）会匹配到 `choose write grammar` 这个**前缀**、把前 20 个字符抹成空白、
#: 留下 `s python`。而 `read_grammar_decl` 那边有边界检查、**不认为**这是声明、**不报错**，
#: 一路走到这里把第一行切坏 —— 用户拿到的是一行残缺的源和一句指不到点子的语法错。
_GRAMMAR_LINE = re.compile(_GRAMMAR_LEAD + _GRAMMAR_HEAD + _GRAMMAR_TAIL + r"[^\n]*", re.M)
_MODULE_LINE = re.compile(r"^[ \t]*module[ \t]+[A-Za-z_]\w*", re.M)


def strip_grammar_decl(src: str) -> str:
    """把声明那一行**抹成等长空白**再交给目标语言的解析器。

    **不抹的话它根本解析不了**：`choose write grammar python` 不是合法的 Python
    （也不是合法的 C / Go / …）—— 声明是**读法**，不是那份源的一部分。

    抹法是等长空白（保留换行），所以**行号一字不动** —— 目标语法报的错，
    行号仍然指回这份文件里的那一行。
    """
    return _GRAMMAR_LINE.sub(_blank_keep_off, src)


def find_decl(src: str) -> tuple[bool, str | None, int]:
    """**只"找"**那一行 —— 与报错器的 `decl_at` **同一个职责**（`docs/188` §1）。

    返回 `(有没有声明头, 别名原文或 None, 行号)`。**不管**"必须在 `module` 之前"、
    "只许写一次"、别名在不在出厂锁里 —— 那三条是 `read_grammar_decl` **在这之上**加的。

    **为什么把这一层单独露出来**：报错器只要"找"（它是个独立的 Loment 程序，判不了别的），
    而"找"是**两边共享的那一层**。分层之后，判据才能**逐条对拍这一层**；
    不分层的话，拿"融合了三条规矩的结果"去比"只管找的结果"，对拍会满屏**假分歧**
    （实测：`module m` 在前那一档，融合层报"必须在 module 之前"、找层说 python ——
    两边都对，只是**答的不是同一个问题**）。
    """
    hits = list(_GRAMMAR_ANY.finditer(src))
    if not hits:
        return False, None, 0
    first = hits[0]
    line = src[:first.start()].count("\n") + 1
    m = _GRAMMAR_DECL.match(src, first.start())
    return True, (m.group(1) if m else None), line


def _grammar_attempt(src: str) -> tuple[int, str] | None:
    """找一行"**像是想写声明、但没写对**"的 -> `(行号, 那一行的原文)`；没有就是 `None`。

    判据：**`module` 之前**有一行，头一个词是 `choose` / `write` / `grammar` 里的任意一个，
    但**后面还有别的词**（`choose write grammar` 那条严格头没匹配上，`find_decl` 已经确认了）。

    为什么限定在 `module` 之前：声明必须在那儿（`docs/188` §1）。这一条让规则**很窄** ——
    那三个词在合法源里只出现在声明本身里。实测（本仓 171 份 `.lomt` / `.lom`）：
    `module` 之前以它们开头、又不是严格声明的行 **一条都没有**。

    为什么不用更宽的"行首是那三个词的任意子集"（`docs/198` §2 的初稿）：那样
    `choose gc_auto` 这类**开关**会中招，而它是合法的 Loment。
    """
    mod = _MODULE_LINE.search(src)
    head = src[:mod.start()] if mod is not None else src
    for i, l in enumerate(head.splitlines(), 1):
        t = l.split()
        if len(t) >= 2 and t[0] in GRAMMAR_DECL_WORDS and not _GRAMMAR_ANY.match(l.lstrip()):
            return i, l.strip()
    return None


def refuse_foreign_grammar(src: str, tool: str) -> None:
    """**"读 L1 源的入口"不许去动用别的表层语法写的源** —— `docs/182` §1.9 那条轴。

    `lomfmt` / `lomdoc` 是**独立入口**：它们拿 `lomc.lex` 直接读原文，**不走前门**。
    而一份 `.lomt` 里可以装六种写法的任何一种。对 C 那门后果很重：

        choose write grammar c unsigned int f(unsigned int x) {   ← 声明与函数头揉成一行

    而**声明那一行整个是"怎么读"**：前门按它定读法、再把它抹成等长空白 ⇒
    **那一行上的函数跟着一起消失**，单元变成空的，而全程**退 0**；
    `lomfmt --write` 是**原地写回**，用户那份源就这么被改坏了。

    所以这里**认出非原生写法就拒** —— 与 `loment check` 对同一份文件的处置一致。
    `rust` 是**原生**（`NATIVE_GRAMMARS`，2026-09-22 的裁定），照旧放行。

    **放在这里、不是各入口各写一份**：这是一条**规则**，抄两份必然漂。
    """
    lang, _err, declared = read_grammar_decl(src)
    if declared and lang not in NATIVE_GRAMMARS:
        raise ValueError(
            f"这是用 {lang} 写法写的 Loment（文件头有 `choose write grammar`）—— "
            f"{tool} 只处理**原生**写法。这份源要交给编译器：`loment check <文件>`"
            f"（`docs/211` / `docs/182` §1.9）。")


def read_grammar_decl(src: str) -> tuple[str, str | None, bool]:
    """**文件头预扫**：`choose write grammar <别名>` -> `(规范名, 报错, 有没有声明)`。

    必须在 `module` **之前**：它决定后面怎么读，读到了 `module` 才说就晚了。
    只许写一次。没写就是 `("loment", None, False)` —— 源侧**可选**（`docs/188` §2）：
    99% 的文件是 Loment，每份写一遍是噪声；而"缺 = Loment"**没有歧义**。

    **"找"在 `find_decl` 里**（那一层与报错器共享），这里只在它之上加三条规矩。
    """
    found, alias, line = find_decl(src)
    if not found:
        # **"想写、但没写对"也要报**（`docs/188` §1 那四种之外的一种，`docs/198` §2 报过）。
        # 关键词写错 / 词序不对时原先**一声不响**：文件落到"缺 = Loment"那一条，
        # 用户拿到的是正文里某处的语法错（`未知顶层关键字 'def'` 之类）—— **指的不是那一行**。
        # §2 那句"兜底从**猜**变**拒绝**"正是为它写的；现在它既没猜也没拒，
        # 是**换了一个猜法**（当成 Loment）。
        guess = _grammar_attempt(src)
        if guess is not None:
            ln, txt = guess
            return "loment", (
                f"第 {ln} 行: 这一行像是想写 `choose write grammar <语法名>`，"
                f"但词序或拼写对不上（实得：{txt!r}）。正确写法是 "
                f"`choose write grammar <名字>` —— 写在 `module` 之前、只写一次，"
                f"名字取出厂锁的那张表（`docs/188` §1）。"
                f"（若你本意是**开关** `choose <名字>`，那不是这一条：开关要写在 "
                f"`module` **之后**。）"), False
        return "loment", None, False
    first = _GRAMMAR_ANY.search(src)
    assert first is not None
    mod = _MODULE_LINE.search(src)
    if mod is not None and mod.start() < first.start():
        return "loment", (f"第 {line} 行: `choose write grammar` 必须在 "
                          f"`module` **之前** —— 它决定后面怎么读，"
                          f"读到 `module` 才说就晚了"), False
    later = list(_GRAMMAR_ANY.finditer(src))
    if len(later) > 1:
        return "loment", (f"第 {src[:later[1].start()].count(chr(10)) + 1} 行: "
                          f"`choose write grammar` 只许写一次"), False
    if alias is None:
        return "loment", (f"第 {line} 行: `choose write grammar` "
                          f"后面要写语法名"), False
    word = alias.strip().lower()
    if word not in GRAMMAR_ALIASES:
        return "loment", (
            f"第 {line} 行: `grammar {alias}` 不在出厂锁的取值表里"
            f"—— 这张表由官方给，**不可扩展、不可覆盖**（`docs/188` §1.1）。"
            f"可写的是：{'、'.join(sorted(GRAMMAR_ALIASES))}"), False
    return GRAMMAR_ALIASES[word], None, True


class FrontUnit:
    """**前门的产物**：这份源该以哪种读法被读、以及读出来的 **Loment 源码**。

    `translated` 说的是"这份 Loment 是**翻出来的**"（源里写的是别的写法）。
    它有用，因为**行号**：翻译器自己的错（子集外 / 语法错）带的是**原文**行号 ✓，
    而编译器在**翻出来的那份**上报的错（类型不符之类）带的是**翻译后**的行号 ——
    调用方据此可以说清"这些行号指的是哪一份"（`docs/179` §6.5 那类"把人引向错方向"）。
    """

    __slots__ = ("grammar", "source", "translated", "path")

    def __init__(self, grammar: str, source: str, translated: bool, path: Path):
        self.grammar = grammar
        self.source = source
        self.translated = translated
        self.path = path


#: **Loment 自己的源文件后缀**。这些后缀说的是"这是一份 **Loment 的源文件**"，
#: 而声明说的是"它用哪种**写法**写的" —— 两件事，别混（`docs/188` §1、§2）。
LOMENT_EXT = (".lomt", ".lom", ".lomp")

#: `(路径, 语言, 模式) -> FrontUnit` 的**一趟式备忘，只存"翻出来的那一支"**。
#:
#: **为什么要有**：读一个源的地方**不止一处** —— `lomentc.load` 读一次，开关预扫
#: （`prescan_switches` 里那个 `scan`）**又读一次**。2026-09-18 实测：只接了 `load`
#: 的话，一份 `choose write grammar python` 的 `.lomt` 在**预扫**那趟被读成
#: "未定义的开关 `write`" —— 而那是**命令行**上先撞到的（`lomentc --check`）。
#: 两处都过前门才对，但那意味着**翻一遍的活被干两遍**；文件在一次编译里不会中途变，
#: 所以按路径备忘是安全的。**只备忘翻译这一支** —— `loment` 那一支只是抹一行，便宜。
_FRONT_MEMO: dict = {}


class FrontDoorRefused(ValueError):
    """**前门拒了这份源**：声明非法，或这门拼法根本没认出来（`docs/188` §1）。

    消息是**给人看的话**（不是栈），也**不带路径** —— 路径由调用方补（它才知道自己
    是从哪进来的）。它是 `ValueError` 的子类，所以既有的 `except ValueError` 照收；
    而 `lomentc.load_unit`（读单元的**唯一入口**）把这一支翻成编译器自己的 `LomError`，
    于是八个调用点都按既有方式报错，而不是让用户看 traceback。
    """


#: 一行**核心模式声明**：`choose <核心词>`（可选分号）。`choose write grammar …` 不匹配
#: —— 它的第二个词是 `write`，不是核心词。
_CORE_CHOOSE_RE = re.compile(r"^[ \t]*choose[ \t]+([A-Za-z_]\w*)[ \t]*;?[ \t]*$")


def _take_core_choose(src: str) -> tuple[str, list[str]]:
    """把**核心模式声明**（`choose runtime` / `choose gc_auto` / `choose no_std`）**摘出来**。

    返回 `(抹掉它们之后的源, 摘出来的那些行)`。

    **为什么是前门的活**（2026-10-09）：按 `docs/188` §0，一份用 Python/C 写法写的单元
    **仍然是一个 Loment 程序** —— 拼法是那一门的，**语义是 Loment 的**。核心 `choose`
    说的是"这个程序要什么"（`docs/182` §1.3 的"核心语法硬写法"），它是**源的一部分**，
    而各家翻译器不认识它：C 那一门把它算成"子集外的一条"直接拒（实测），
    别的门当自己那门语法吃掉（实测：`m.chooses` 空着）。

    **抹成等长空白**（与 `strip_grammar_decl` 同一手法）：行号不动，翻译器报的错还指在
    原来的行上。

    与 `strip_grammar_decl` 方向相反、理由同一条：`choose write grammar` 说的是
    "**怎么读这份文件**"（属于前端，所以抹掉不留），核心 `choose` 说的是
    "**这份程序要什么**"（属于源，所以抹掉之后要**带回来**）。
    """
    kept: list[str] = []
    core: list[str] = []
    for line in src.split("\n"):
        m = _CORE_CHOOSE_RE.match(line.rstrip("\r"))
        if m and m.group(1) in potato.CORE_WORDS:
            core.append(f"choose {m.group(1)}")
            kept.append(" " * len(line))
        else:
            kept.append(line)
    return "\n".join(kept), core


def _inject_core_choose(text: str, core: list[str]) -> str:
    """把摘出来的核心声明**接在译文第一行 `module` 之后**。

    **不能接在最前面**：`parse()` 的第一步是 `expect("module")`（文件必须以 `module`
    开头），`choose` 行只能出现在它之后。

    **译文自己已经写过的那几条要跳过**（2026-10-10）：`lomt_from.emit_lomt` 在 `impl=True`
    时会**自己**给实体单元发一行 `choose runtime`（`docs/227`）。重复注入的后果不是"多一行
    废话"，是**这一维写了两次** —— 那是 E022，而 `loment/lib/lumtui_math.lomt` 那份
    python 写法的模块当场就红（实测）。
    """
    if not core:
        return text
    core = [c for c in core
            if not re.search(rf"^[ \t]*{re.escape(c)}[ \t]*$", text, re.M)]
    if not core:
        return text
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for i, l in enumerate(lines):
        if l.startswith("module "):
            return nl.join(lines[:i + 1] + core + lines[i + 1:])
    return text


def front_door(path: Path, lang: str = "auto", mode: str = "strict") -> FrontUnit:
    """**Loment 的前门**：一份源 -> 该交给编译器的 **Loment 源码**（`docs/188` §1、§7.2）。

    按 `resolve_lang` 的次序（**声明 > 后缀 > 内容**）定读法，然后分两条路：

    * **原生拼法（`loment` / `rust`，含没写声明）** —— 它就是 Loment。把声明那一行
      **抹成等长空白**再交出去。`rust` 在这一支里是因为**它是基础语法的一种拼法**，
      不需要翻译器 —— 见 `NATIVE_GRAMMARS` 的注解（用户 2026-09-22 的裁定）。
    * **别的写法** —— 那份源按 `docs/188` §0 **仍然是 Loment**，只是拼法不同。所以走
      `potato_from` + `lomt_from --impl` **翻成 Loment 源码**再交出去。
      **全程在本进程里算，不拉起 python / java / …** —— 那是用户定的死要求
      （"Loment 在没有使用 `let py` 这行代码的情况下，不会拉起 python 或其他任何编译器"）。

    ## 为什么是"**抹掉那一行**"而不是"让 parser 认这个构造"

    这一条决定了整件事**要不要付语言面的双倍工**，所以写清楚：

    * 让 parser 认 `choose write grammar` —— 要动 lexer/parser，而语言面有**两个实现**
      （`tools/lomentc.py` 与 `loment/selfhost/*.lomt`），按 `CLAUDE.md` 要一起改、
      **并且重生成种子**（那笔机械提交里 46 KB 的 IR 会整体位移）。
      而这一门**本来就有坑**：`choose` 在 Loment 里**已经是开关关键字**
      （`choose <名字>` / `choose close <名字>`），所以 `choose write grammar python`
      会被读成"开关 `write`"，然后卡在 `grammar` 上 —— 要加一条**特例**才分得开。
    * **抹掉** —— 只在**前端**改一处，**parser 一行不动、种子不变**。

    而"声明"本来就**不是那份源的一部分**（它说的是"**怎么读**"），所以它属于**前端**、
    不属于语法。⇒ 这样选不是图省事，是把它放对了层。

    **代价要说清**：`lomfmt` / `lomdoc` / LSP 这些**直接读源**的入口仍会看到那一行。
    它们要不要认，是**另一件事**（`docs/182` §1.9 那张"读 L1 源的入口"清单），
    这一版先只把**编译器**这条路走通，并在那张清单上记一笔。

    ## 一处**刻意的不对称**：`.lomt` **不嗅探**（但 `resolve_lang` 会）

    这条决定了这个函数与 `resolve_lang` 的分工，得写清楚：

    * **`.lomt` / `.lom` / `.lomp`** —— 后缀已经说了"这是一份 Loment 的源文件"，
      所以读法**只看声明**：写了别的写法就按它翻；**没写就是 Loment**（`docs/188` §2
      "缺 = Loment，没有歧义"）。**不走内容嗅探** —— 那正是 §2 要治的：
      > 这一下把 `detect_lang` 的**嗅探**换成**声明**；兜底从"猜"变"拒绝"。
      嗅探在这里还会**翻错语言**：一份忘了写 `module` 的 Loment 文件会被嗅成 `rust`
      （`_RS_FN` 认 `fn`），于是用户拿到的是一句 Rust 翻译错，而不是"这不是 Loment"。
    * **别的后缀**（`.py` / `.c` / …）—— 按后缀说话，这就是 `resolve_lang` 那条路。

    **而 `potato_from.resolve_lang` 照旧嗅探** —— 它是**前端工具**的入口（`transcribe`），
    那里"猜一个再报出猜了什么"是有用的。两条路的差别不是不一致，是**分工**：
    **编译器要求显式，前端工具可以先猜**。
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    # **UTF-8 BOM 不是源的一部分**（`#482`）：Windows 的记事本、PowerShell 的 `>`
    # 都会写它。留着它，词法器在 1:1 报一句"非法字符 '﻿'"，把真正的错盖掉。
    # 只去**一个**、只在**文件头** —— 这正是 BOM 的定义；行号不受影响，第 1 行的列
    # 号少一格，而那一格本来就没显示。
    if src.startswith("﻿"):
        src = src[1:]
    if lang == "auto" and path.suffix in LOMENT_EXT:
        # Loment 的源文件：**只信声明**（缺 = Loment），**不嗅探** —— 见上面那一段
        g, err, declared = read_grammar_decl(src)
        if err:
            raise FrontDoorRefused(err)
        if not declared or g in NATIVE_GRAMMARS:
            # `rust` 与 `loment` 都落这一支：**它就是基础语法**（见 `NATIVE_GRAMMARS`
            # 的注解）—— 不经过任何翻译器，抹掉声明那一行原样交出去。
            return FrontUnit("loment", strip_grammar_decl(src), False, path)
        lang, why = g, "文件头声明 `choose write grammar`"
    else:
        lang, why = resolve_lang(path, lang)
        if not lang:
            raise FrontDoorRefused(why)
        if lang == "loment":
            # 读法就是 Loment：**抹掉声明那一行**（等长空白，行号不动）后原样交出去
            return FrontUnit("loment", strip_grammar_decl(src), False, path)
    if lang not in LANGS:
        raise ValueError(
            f"{path}: 定不出源语法（后缀 {path.suffix!r} 不在 {sorted(EXT)}，内容：{why}）"
            f"—— 用 --lang 指明，或在文件头写 `choose write grammar <语法名>`")
    # **别的写法**：翻成 Loment。`lomt_from` 按需 import —— 它会把各门翻译器拉进来，
    # 而这条路不是每个调用方都走得到（与 `lomt_from` 自己那条注解同一个道理）。
    import lomt_from  # noqa: PLC0415
    _key = (str(path), lang, mode)
    _hit = _FRONT_MEMO.get(_key)
    if _hit is not None:
        return _hit
    # **核心声明先摘出来**（`_take_core_choose`）：翻译器不认识那几行，交给它就是
    # "子集外的一条"（C 门直接拒）或者被当成本门语法吃掉（别的门）。摘出来之后接回译文。
    _body, _core = _take_core_choose(strip_grammar_decl(src))
    try:
        doc, rep = LANGS[lang](_body, path.name, mode)
    except front_errors(lang) as e:
        # 写法读不通 —— 与"翻不出来"一样响亮地拒，**不给一份少算一步的单元**
        # （见 `front_errors` 的注解）。
        raise lomt_from.NotRepresentable(f"{path}: 这份源读不通 —— {e}")
    # **转写那一步丢的东西也要报**。`emit_lomt` 的 `skipped` 只管"发不出来"；
    # 转写期丢的是**根本没进对象**的东西（顶层 `typedef` / 全局量 / `union` /
    # 预处理指令 / K&R 函数 …）。这一条原先把报告整个丢掉（`_rep`），于是这些东西
    # **在编译器这条路上一个字都不说** —— 而 `lomt_from.py` 那条路会打 `[skip]`。
    # 同一条纪律：全有或全无，丢了就拒（`docs/167`"不静默丢"）。
    if rep.skipped:
        raise lomt_from.NotRepresentable(
            f"{path}: 用 {lang} 写法写的单元里有 {len(rep.skipped)} 处转写不了"
            f"（前 3 处：{[(x['name'], x['why']) for x in rep.skipped[:3]]}）—— "
            f"那一门整份是全有或全无，丢了的部分不会悄悄跳过，这里直接拒")
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    if skipped:
        # **子集外的东西发不出来** —— 必须响亮，不能给一份"少算一步却照样能编"的单元。
        # 抛 `NotRepresentable`（发射器自己的那个类型，`lomt_from` 顶上定义的）——
        # 这一条的语义正是"这份单元**表示不出来**"，比 `ValueError` 说得准。
        raise lomt_from.NotRepresentable(
            f"{path}: 用 {lang} 写法写的单元里有 {len(skipped)} 处发不出来"
            f"（前 3 处：{skipped[:3]}）—— 那一门整份是全有或全无，"
            f"翻不出来的部分不会悄悄丢掉，这里直接拒")
    # **核心声明跟着译文一起交出去**（见 `_core_choose_lines`）。放在最后一步、只对
    # "翻了的那一支"做 —— 原生拼法那一支原样交出去，那些行本来就在源里。
    text = _inject_core_choose(text, _core)
    _out = FrontUnit(lang, text, True, path)
    _FRONT_MEMO[_key] = _out
    return _out


def resolve_lang(path: Path, lang: str = "auto") -> tuple[str, str]:
    """`(语言, 依据)` —— **声明 > 后缀 > 内容**。`transcribe` 与各工具共用这一处,
    免得"判语法"这事在两处各写一遍(那种必然漂)。

    **声明压过后缀**：后缀是命名习惯、会说谎（一份装着 Python 的 `.lomt` 正是这条要治的），
    而声明是**作者对这份文件说的**。`--lang` 仍然最大（那是调用方当场指定）。
    定不出来时返回 `("", 原因)`，由调用方决定怎么报。
    """
    if lang != "auto":
        return lang, "调用方指定的"
    src = path.read_text(encoding="utf-8", errors="replace")
    g, err, declared = read_grammar_decl(src)
    if err:
        return "", err
    if declared:
        return g, "文件头声明 `choose write grammar`"
    by_ext = EXT.get(path.suffix, "")
    if by_ext:
        return by_ext, f"后缀 {path.suffix}"
    return detect_lang(src)


def transcribe(path: Path, lang: str = "auto", mode: str = "strict"):
    """源文件 -> (形式对象, 报告)。

    `lang="auto"` 时按 `resolve_lang` 的规则定语言 —— 它也就是 docs/175 §5 那条
    "非 Loment 源语法的 `.lomt` 文件"落地的地方。
    """
    lang, why = resolve_lang(path, lang)
    if not lang:
        # `choose write grammar` 那一声明的毛病（位置不对 / 写两次 / 拼法不在出厂锁里）
        raise ValueError(f"{path}: {why}")
    if lang == "loment":
        raise ValueError(
            f"{path} 是 **Loment 语法**（{why}）—— 它不该走多语法前端, "
            f"直接交给编译器: loment check {path}")
    if lang not in LANGS:
        raise ValueError(
            f"定不出源语法（后缀 {path.suffix!r} 不在 {sorted(EXT)}，内容：{why}）"
            f"—— 用 --lang c|rust|python 指明")
    src = path.read_text(encoding="utf-8", errors="replace")
    # **声明要先抹掉**：`choose write grammar python` 不是合法的 Python，
    # 交给目标解析器之前必须清掉（等长空白，行号不动）。
    return LANGS[lang](strip_grammar_decl(src), path.name, mode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_from", description="源码 -> Potato 形式对象")
    ap.add_argument("file", nargs="?", help="源文件")
    ap.add_argument("--lang", choices=("auto",) + tuple(sorted(LANGS)), default="auto")
    ap.add_argument("--mode", choices=("strict", "lenient"), default="strict")
    ap.add_argument("--json", metavar="OUT")
    ap.add_argument("--report", metavar="OUT")
    ap.add_argument("--corpus", metavar="MANIFEST", help="JSON 清单: [{path, lang?}]")
    ap.add_argument("--out-dir", metavar="DIR", help="--corpus 的输出目录")
    a = ap.parse_args(argv)

    if a.corpus:
        if not a.out_dir:
            print("[ERR] --corpus 需要 --out-dir", file=sys.stderr)
            return 2
        man = json.loads(Path(a.corpus).read_text(encoding="utf-8"))
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        reports = []
        for ent in man:
            p = Path(ent["path"])
            try:
                doc, rep = transcribe(p, ent.get("lang", a.lang), a.mode)
            except Exception as e:  # noqa: BLE001
                reports.append({"path": str(p), "language": ent.get("lang", a.lang),
                                "entities_seen": 0, "entities_ok": 0,
                                "conversion_rate": 0.0, "skipped": [],
                                "object_valid": False, "validate_errors": [str(e)]})
                continue
            (out / f"{_ident(p.stem)}.potato.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reports.append(rep.as_dict(not rep.validate_errors))
        (out / "reports.json").write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ok = sum(1 for r in reports if r["object_valid"])
        print(f"[OK] 转写 {ok}/{len(reports)} 个文件 -> {out}")
        return 0 if ok == len(reports) else 1

    if not a.file:
        print("[ERR] 需要文件或 --corpus", file=sys.stderr)
        return 2
    p = Path(a.file)
    doc, rep = transcribe(p, a.lang, a.mode)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    if a.report:
        Path(a.report).write_text(
            json.dumps(rep.as_dict(not rep.validate_errors), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    if not a.json and not a.report:
        sys.stdout.write(text)
    else:
        print(f"[OK] {p} -> {rep.ok}/{rep.seen} 实体 "
              f"(校验错误 {len(rep.validate_errors)})")
    return 0 if not rep.validate_errors else 1


if __name__ == "__main__":
    sys.exit(main())
