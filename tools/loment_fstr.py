"""字符串插值的**前端改写**（`docs/341`）。

`f"a{x}b"` 不是语法构造，而是**读法**：前端把它改写成

    str_concat(str_concat("a", str_show(x)), "b")

于是 lexer / parser / checker / codegen **一行都不动**（与 `potato_from._tur2_sugar`
同一条理由）。自举侧的孪生实现在 `loment/selfhost/driver.lomt` 的 `interp_rewrite`，
两边产出的文本必须逐字节相同 —— 那是 IR 逐字节相同的前提。

## 表面语法（v1）

* `f"…"` —— 插值字符串；`\\` 转义与普通字符串同一张表（`lomc._ESCAPES`）；
* `rf"…"` —— **不处理转义**的插值字符串（`\\` 就是 `\\`），花括号规则相同；
* `{{` / `}}` —— 字面量花括号；
* `{expr}` —— 孔；`expr` 是**任意表达式**；
* `{expr=}` —— 调试图：先印 `expr` 的原文再加 `=`，然后印值；
* `{expr!s}` / `{expr!r}` —— 转换：`!s` 走 `str_show`，`!r` 走 `str_repr`。

## 刻意不做（v1）

* 格式说明 `{x:>5}` / `{x:05}` —— 下一阶段（列号映射表同一批）；
* 跨行的插值字符串与跨行的孔 —— 拒绝。**行号因此一字不动**，只有列号要映射；
* 孔里嵌套插值字符串 —— 拒绝（外层孔扫描按普通字符串读内层，通常报"未闭合"）。

## 列号映射表

改写只在一行之内增删字符。`ColMap` 记下每一行里"新列区间 ← 原列区间"的对应：

* 逐字节照抄的片段（文本段、孔里的表达式原文）**线性**映射；
* 合成出来的片段（`str_concat(` / `str_show(` / 前缀 `f` 的位置）**塌到**它所属孔或
  字符串的原始起点（原长记 0）；
* 片段之后的列，按累计位移校正。

解析器的报错带 `行:列`（`lomc.LomError`）；`lomentc.load` 把它过一遍这张表。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lomc


#: 允许的插值前缀。两个都是完整标识符，按词的边界判，不会互相遮。
_PREFIXES = ("rf", "f")


@dataclass
class ColMap:
    """新列号 → 原列号。**行号不变**（改写从不插入换行）。"""

    #: 行号 → [(新起, 新止, 原起, 原长)]，按新起点升序；列号 **1 起**。
    lines: dict[int, list[tuple[int, int, int, int]]] = field(default_factory=dict)

    def map(self, line: int, col: int) -> int:
        ents = self.lines.get(line)
        if not ents:
            return col
        adj = 0
        for na, nb, oa, ob in ents:
            if col < na:
                return col + adj
            if col < nb:
                if ob == oa:
                    return oa
                return oa + (col - na)
            adj += (ob - oa) - (nb - na)
        return col + adj

    def empty(self) -> bool:
        return not self.lines


def _is_ident(c: str) -> bool:
    return c.isalnum() or c == "_"


def _line_col(src: str, i: int) -> tuple[int, int]:
    line = src.count("\n", 0, i) + 1
    return line, i - src.rfind("\n", 0, i)


def _lit_body(s: str) -> str:
    """把一段**用户原文**安全地嵌进普通字符串字面量。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class _Scanner:
    def __init__(self, src: str):
        self.src = src
        self.n = len(src)
        self.i = 0
        self.out: list[str] = []
        self.out_len = 0
        self.src_line_start = 0        # `src` 里当前行的起点
        self.line = 1
        self.out_line_start = 0        # `out` 里当前行的起点
        self.colmap = ColMap()
        self.depth = 0                 # 顶层花括号深度（判外部代码块）
        self.declared: set[str] = set()
        self.last_word = ""
        self.word_before_last = ""

    # -- 写 -------------------------------------------------------------

    def _record(self, new_a: int, new_b: int, orig_start: int, orig_len: int,
                text: str, anchor: int) -> None:
        orig_a = orig_start - self.src_line_start + 1
        if orig_len == len(text) and self.src[orig_start:orig_start + orig_len] == text:
            ent = (new_a, new_b, orig_a, orig_a + orig_len)
        else:
            ac = anchor - self.src_line_start + 1
            ent = (new_a, new_b, ac, ac)
        self.colmap.lines.setdefault(self.line, []).append(ent)

    def emit(self, text: str, orig_start: int, orig_len: int, anchor: int) -> None:
        if not text:
            return
        new_a = self.out_len - self.out_line_start + 1
        self._record(new_a, new_a + len(text), orig_start, orig_len, text, anchor)
        self.out.append(text)
        self.out_len += len(text)

    def copy(self, upto: int) -> None:
        self.emit(self.src[self.i:upto], self.i, upto - self.i, self.i)
        self.i = upto

    # -- 扫描 -----------------------------------------------------------

    def run(self) -> str:
        src = self.src
        while self.i < self.n:
            c = src[self.i]
            if c == "\n":
                self.out.append("\n")
                self.out_len += 1
                self.i += 1
                self.line += 1
                self.src_line_start = self.i
                self.out_line_start = self.out_len
                self.last_word = ""
                self.word_before_last = ""
                continue
            if src.startswith("//", self.i):
                j = src.find("\n", self.i)
                self.copy(self.n if j < 0 else j)
                continue
            if src.startswith("/*", self.i):
                j = src.find("*/", self.i + 2)
                if j < 0:
                    line, col = _line_col(src, self.i)
                    raise lomc.LomError(line, col, "未闭合的块注释")
                self.copy(j + 2)
                continue
            if c == '"':
                self.copy(self._skip_string(self.i))
                continue
            if _is_ident(c):
                j = self.i
                while j < self.n and _is_ident(src[j]):
                    j += 1
                word = src[self.i:j]
                if word in _PREFIXES and j < self.n and src[j] == '"':
                    self._fstring(self.i, j, raw=(word == "rf"))
                    self.last_word, self.word_before_last = "", word
                    continue
                if word in ("command", "foruse"):
                    k = j
                    while k < self.n and src[k] in " \t":
                        k += 1
                    e = k
                    while e < self.n and _is_ident(src[e]):
                        e += 1
                    if e > k:
                        self.declared.add(src[k:e])
                self.word_before_last, self.last_word = self.last_word, word
                self.copy(j)
                continue
            if c == "{":
                if self._starts_raw_block():
                    self.copy(self._raw_block_end(self.i))
                    continue
                self.depth += 1
                self.copy(self.i + 1)
                continue
            if c == "}":
                if self.depth > 0:
                    self.depth -= 1
                self.copy(self.i + 1)
                continue
            self.copy(self.i + 1)
        return "".join(self.out)

    def _starts_raw_block(self) -> bool:
        """与 `lomc._starts_ext_block` 同一条规则（`docs/185` §3）。"""
        if not self.last_word:
            return False
        if self.word_before_last == "let":
            return True
        return self.depth == 0 and self.last_word in self.declared

    def _raw_block_end(self, i: int) -> int:
        src, n = self.src, self.n
        j, d = i + 1, 1
        while j < n and d > 0:
            if src[j] == "{":
                d += 1
            elif src[j] == "}":
                d -= 1
            j += 1
        if d != 0:
            line, col = _line_col(src, i)
            raise lomc.LomError(line, col, "外部代码块没闭合（少一个 `}`）")
        return j

    def _skip_string(self, i: int) -> int:
        src, n = self.src, self.n
        j = i + 1
        while j < n:
            if src[j] == "\\" and j + 1 < n:
                j += 2
                continue
            if src[j] == '"':
                return j + 1
            j += 1
        line, col = _line_col(src, i)
        raise lomc.LomError(line, col, "未闭合的字符串")

    # -- 插值字符串 -----------------------------------------------------

    def _fstring(self, start: int, q: int, raw: bool) -> None:
        """`start` 是前缀起点、`q` 是开引号；整段（前缀 + 字符串）改写后写出去。"""
        src, n = self.src, self.n
        line, col = _line_col(src, start)
        #: 片段：("lit"|"hole", 文本, 原起点, 原长, 转换, 调试前缀, 孔起, 孔止)
        #: 孔起/孔止 = `{` 与 `}` 的位置（合成括号的列号塌到它们上面）。
        pieces: list[tuple[str, str, int, int, str, str, int, int, object]] = []
        buf: list[str] = []
        buf_start = q + 1
        j = q + 1
        while True:
            if j >= n or src[j] == "\n":
                raise lomc.LomError(line, col, "未闭合的插值字符串（v1 只收单行）")
            c = src[j]
            if c == '"':
                break
            if c == "\\" and not raw:
                if j + 1 >= n:
                    raise lomc.LomError(line, col, "未闭合的插值字符串（v1 只收单行）")
                nxt = src[j + 1]
                if nxt not in lomc._ESCAPES:
                    raise lomc.LomError(line, col + (j - start),
                                        f"未知转义 \\{nxt} —— 只认 \\n \\t \\r \\0 \\\\ \\\"")
                buf.append(src[j:j + 2])
                j += 2
                continue
            if c == "\\" and raw:
                buf.append("\\\\")
                j += 1
                continue
            if c == "{":
                if j + 1 < n and src[j + 1] == "{":
                    buf.append("{")
                    j += 2
                    continue
                if buf:
                    pieces.append(("lit", "".join(buf), buf_start, j - buf_start,
                                   "", "", 0, 0, None))
                    buf = []
                end, body_start, body = self._hole(j, line, start)
                expr, expr_off, conv, debug, spec = self._hole_tail(
                    body, body_start, line, j)
                pieces.append(("hole", expr, expr_off, len(expr), conv,
                               expr + "=" if debug else "", j, end, spec))
                j = end + 1
                buf_start = j
                continue
            if c == "}":
                if j + 1 < n and src[j + 1] == "}":
                    buf.append("}")
                    j += 2
                    continue
                raise lomc.LomError(line, col + (j - start),
                                    "单个 `}` 不是合法的插值 —— 字面量花括号写 `}}`")
            buf.append(c)
            j += 1
        if buf:
            pieces.append(("lit", "".join(buf), buf_start, j - buf_start,
                           "", "", 0, 0, None))
        self._emit_fstring(pieces, start, j + 1)

    def _hole(self, j: int, line: int, sstart: int) -> tuple[int, int, str]:
        """`j` 在孔的 `{`；返回 `(闭 } 下标, 体起点, 体)`。"""
        src, n = self.src, self.n
        body_start = j + 1
        k = body_start
        depth = 0
        while k < n:
            c = src[k]
            if c == "\n":
                raise lomc.LomError(line, _line_col(src, j)[1],
                                    "孔不能跨行（v1 只收单行的插值字符串）")
            if c == '"':
                k = self._skip_string(k)
                continue
            if src.startswith("//", k):
                e = src.find("\n", k)
                if e < 0:
                    break
                k = e
                continue
            if src.startswith("/*", k):
                e = src.find("*/", k + 2)
                if e < 0:
                    raise lomc.LomError(line, _line_col(src, k)[1], "未闭合的块注释")
                k = e + 2
                continue
            if c in "{([":
                depth += 1
            elif c in "})]":
                if c == "}" and depth == 0:
                    return k, body_start, src[body_start:k]
                depth -= 1
            k += 1
        raise lomc.LomError(line, _line_col(src, j)[1],
                            "未闭合的插值孔（少一个 `}`）")

    def _top_colon(self, t: str) -> int:
        """孔体里**顶层**的 `:`（格式说明的起点）；`a[1:2]` 那种在括号里，不算。"""
        depth = 0
        k = 0
        while k < len(t):
            c = t[k]
            if c == '"':
                k += 1
                while k < len(t):
                    if t[k] == "\\":
                        k += 2
                        continue
                    if t[k] == '"':
                        break
                    k += 1
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ":" and depth == 0:
                return k
            k += 1
        return -1

    def _parse_spec(self, txt: str, line: int, col: int):
        """格式说明 v1：`[0]?[<>=^]?W`。返回 `(fill, align, width)`。"""
        s = txt.strip()
        if not s:
            raise lomc.LomError(line, col, "格式说明是空的")
        fill = " "
        align = 1                      # 缺省右对齐（数字的默认；文档写明）
        if s[0] == "0":
            fill = "0"
            s = s[1:]
        if s[:1] in ("<", ">", "^"):
            align = {"<": 0, ">": 1, "^": 2}[s[0]]
            s = s[1:]
        if not s.isdigit():
            raise lomc.LomError(
                line, col,
                "格式说明只认 `>W` / `<W` / `^W` / `0W`（可选前导 `0` 表示用 0 填充）")
        w = int(s)
        if w < 1 or w > 65535:
            raise lomc.LomError(line, col, "格式说明的宽度要在 1..65535 之间")
        return (fill, align, w)

    def _hole_tail(self, body: str, body_start: int, line: int, at: int
                   ) -> tuple[str, int, str, bool, object]:
        """剥出格式说明、`!s` / `!r` 与 `=`；返回 `(表达式, 原起点, 转换, 调试, 说明)`。"""
        col = _line_col(self.src, at)[1]
        lead = len(body) - len(body.lstrip())
        t = body.strip()
        off = body_start + lead
        spec = None
        ci = self._top_colon(t)
        if ci >= 0:
            spec = self._parse_spec(t[ci + 1:], line, col)
            t = t[:ci].rstrip()
        conv = ""
        if len(t) >= 2 and t[-2] == "!" and t[-1] in "sr":
            conv = t[-1]
            t = t[:-2].rstrip()
        debug = False
        if t.endswith("=") and len(t) >= 2 and t[-2] not in "=!<>+-*/%&|^":
            debug = True
            t = t[:-1].rstrip()
        if not t:
            raise lomc.LomError(line, col, "插值孔里没有表达式")
        return t, off, conv, debug, spec

    def _emit_fstring(self, pieces, start: int, end: int) -> None:
        #: 操作数 = 一组列映射片段 (文本, 原起点, 原长, 锚点)；片段拼起来就是该操作数。
        ops: list[list[tuple[str, int, int, int]]] = []

        def lit_op(o_start: int, o_len: int, body: str) -> list[tuple[str, int, int, int]]:
            return [('"', o_start, 0, o_start),
                    (body, o_start, o_len, o_start),
                    ('"', o_start, 0, o_start)]

        for kind, text, o_start, o_len, conv, dbg, h_start, h_end, spec in pieces:
            if kind == "lit":
                if dbg:
                    ops.append(lit_op(o_start, 0, _lit_body(dbg) + text))
                elif text:
                    ops.append(lit_op(o_start, o_len, text))
                continue
            if dbg:
                ops.append(lit_op(o_start, 0, _lit_body(dbg)))
            fn = "str_repr" if conv == "r" else "str_show"
            # 合成的括号塌到孔的边界上：`(` ← `{`，`)` ← `}` —— 孔里少写东西时
            # 解析器报在合成的 `)` 上，用户该看到的是自己写的那个 `}`。
            op = [(fn + "(", h_start, 0, h_start),
                  (text, o_start, o_len, o_start),
                  (")", h_end, 0, h_end)]
            if spec is not None:
                fill, align, width = spec
                # 填充字符写成整数字面量（`fill` 是字符，语言里 `u8` 参数收 32 / 48）
                op = ([("str_pad(", h_start, 0, h_start)] + op +
                      [(f", {width}, {ord(fill)}, {align})", h_end, 0, h_end)])
            ops.append(op)
        if not ops:
            ops = [[('""', start, 0, start)]]
        op_texts = ["".join(c[0] for c in op) for op in ops]
        if len(ops) == 1:
            text = op_texts[0]
            chunks = list(ops[0])
        else:
            text = _fold(op_texts)
            chunks = [("str_concat(", start, 0, start)] * (len(ops) - 1)
            chunks.extend(ops[0])
            for k in range(1, len(ops)):
                chunks.append((", ", start, 0, start))
                chunks.extend(ops[k])
                chunks.append((")", start, 0, start))
        base = self.out_len - self.out_line_start + 1
        pos = 0
        for txt, o_start, o_len, anchor in chunks:
            self._record(base + pos, base + pos + len(txt), o_start, o_len, txt, anchor)
            pos += len(txt)
        self.out.append(text)
        self.out_len += len(text)
        self.i = end


def _fold(op_texts: list[str]) -> str:
    """左结合：`str_concat(str_concat(p0, p1), …)`。"""
    acc = op_texts[0]
    for p in op_texts[1:]:
        acc = "str_concat(" + acc + ", " + p + ")"
    return acc


def rewrite(src: str) -> tuple[str, ColMap]:
    """把源里的插值字符串改写成 `str_concat` / `str_show` 链。

    返回 `(新源码, 列号映射)`。没有插值字符串时原样返回、映射为空。
    """
    sc = _Scanner(src)
    out = sc.run()
    return out, sc.colmap
