#!/usr/bin/env python3
# lomfmt.py — Loment 格式化器 (M55, docs/148)
#
# 判据: 幂等 (格式化两次结果相同) + 语义保持 (格式化前后形式对象一致)。
# 实现: 复用 lomc 词法器 -> 多字符运算符合并 -> 缩进/空格规则重排。
# 不改变 token 序列的语义, 只改空白与换行。
#
# 用法:
#   python tools/lomfmt.py FILE [FILE...]          # 输出到 stdout
#   python tools/lomfmt.py --check FILE [FILE...]  # 只检查是否已格式化 (退出码 1 = 需格式化)
#   python tools/lomfmt.py --write FILE [FILE...]  # 原地写回
# 退出码: 0 = OK / 1 = --check 有差异 / 2 = 读取或词法错误。

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import potato_from  # noqa: E402

# 需要合并的多字符运算符 (词法器逐字符产出 punct)
MERGE = {"->", "::", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "..", "=>"}
# 这些运算符两侧各留一个空格 (二元)
BINARY = {"+", "-", "*", "/", "%", "=", "==", "!=", "<", "<=", ">", ">=",
          "&&", "||", "&", "|", "^", "<<", ">>", "->", "=>"}
# 这些符号前不留空格
NO_SPACE_BEFORE = {",", ";", ")", "]", "}", ":", "::", ".", ".."}
# 这些符号后不留空格
NO_SPACE_AFTER = {"(", "[", ".", "::", "!", ".."}
# 顶层关键字 (用于顶层之间插空行)
# **`choose` 原先就不在这张表里** —— 既有缺口, 直到 docs/182 加开关才暴露:
# 没有它, `choose std` 会被揉进上一行, 而 `set choose X { … }` 更是整块贴上去。
TOP_KW = {"module", "use", "capability", "const", "struct", "enum", "trait", "impl",
          "fn", "excluded", "pub", "interrupt", "choose", "set", "addin",
          # `comefor` / `byuse` 是**编译期的块级构造**，与 `set choose` 同形：
          # 不进这张表的话，`comefor let "x" to {` 会被揉进上一行、
          # `byuse "x" done` 也一样 —— 与当初 `choose` 漏掉时同一个症状。
          "comefor", "byuse",
          # `register`（docs/223 形态 A）是**顶层**声明，同理。
          "register"}
#: **声明行首的修饰词**（`#46`）：它们不单独占一行，要和后面那一项连在一起。
#: `pub` 在 `TOP_KW` 里（它确实起一条声明），`extern` 不在（它不触发换行）。
_HEAD_MODS = {"pub", "extern"}
INDENT = "    "


def merge_ops(toks: list[lomc.Tok]) -> list[lomc.Tok]:
    out: list[lomc.Tok] = []
    i = 0
    while i < len(toks):
        a = toks[i]
        if a.kind == "punct" and i + 1 < len(toks) and toks[i + 1].kind == "punct":
            two = a.val + toks[i + 1].val
            if two in MERGE:
                out.append(lomc.Tok("punct", two, a.line, a.col))
                i += 2
                continue
        out.append(a)
        i += 1
    return out


def _is_value_end(tok: lomc.Tok | None) -> bool:
    """上一个 token 是否结束一个值 (用于判断 & - * + 是一元还是二元)。"""
    if tok is None:
        return False
    if tok.kind in ("ident", "number", "string"):
        return tok.val not in ("let", "return", "if", "else", "while", "for", "in",
                               "match", "mut", "fn", "const", "guard", "as", "pub")
    return tok.val in (")", "]", "}")


_CONTROL = {"if", "while", "match", "for", "in", "return", "guard", "as"}


def _needs_space(pp: lomc.Tok | None, prev: lomc.Tok | None, cur: lomc.Tok) -> bool:
    if prev is None:
        return False
    pv, cv = prev.val, cur.val
    # 值表只对**标点**有意义：字符串的 `val` 是内容（`#640`），`"}"` 的 `val` 就是 `}`，
    # 拿它去查 `NO_SPACE_BEFORE` 会把 `f(a, "}")` 排成 `f(a,"}")`。
    if cur.kind == "string":
        return True
    if prev.kind == "string":
        return cv not in NO_SPACE_BEFORE
    if cv in NO_SPACE_BEFORE or pv in NO_SPACE_AFTER:
        return False
    if cv in ("(", "["):
        if prev.kind in ("ident", "number", "string") and pv not in _CONTROL:
            return False  # 调用/下标/类型参数
        if pv in (")", "]"):
            return False
    if cv == "!" or pv == "!":
        return False
    if cv in BINARY or pv in BINARY:
        if cv in ("+", "-", "&") and not _is_value_end(prev):
            return False  # 一元
        if pv in ("+", "-", "&") and not _is_value_end(pp):
            return False  # 一元 (看更早的 token)
        return True
    return True


_TYPE_BODY_KW = {"struct", "enum", "trait", "match"}
_BRACE_KW = {"struct", "enum", "trait", "impl", "fn", "if", "while", "for", "match",
             "else", "capability"}


def _is_comment_line(s: str) -> bool:
    """这一行输出是不是注释（顶层空行要插在整块注释**之前**）。"""
    t = s.strip()
    return t.startswith("//") or t.startswith("/*") or t.startswith("*")


def _scan_comments(src: str) -> tuple[dict[int, list[str]], dict[int, str]]:
    """注释从源码里**单独扫一遍**（`#45`）。

    词法器把注释跳过了，token 流里根本没有它们 —— 格式化器只吃 token，于是 `///`
    文档注释连同所有 `//` / `/* */` 一起被丢掉，`loment doc` 的文档源也就没了。

    返回两张表：`行号 -> 独占该行的注释（可能多条）`、`行号 -> 行尾注释`。
    字符串里的 `//` 不算注释（扫描时跳过字符串与转义）。
    """
    own: dict[int, list[str]] = {}
    trail: dict[int, str] = {}
    i, n, line = 0, len(src), 1
    line_has_code = False
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            line_has_code = False
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == '"':
            i += 1
            line_has_code = True
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                # **字符串里的换行不计数** —— 词法器就是这么做的（`lomc.lex` 的 `"` 分支
                # 只扫到配对的引号，不碰 `line`）。这里若数了，行号会与 token 的行号整体
                # 错开，注释就会插到别的语句中间（实测 codegen.lomt 差 6 行）。
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            if j < 0:
                j = n
            text = src[i:j].rstrip()
            if line_has_code:
                trail[line] = text
            else:
                own.setdefault(line, []).append(text)
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            text = src[i:j]
            if line_has_code:
                trail[line] = text
            else:
                own.setdefault(line, []).append(text)
            line += text.count("\n")
            i = j
            continue
        line_has_code = True
        i += 1
    return own, trail


def _brace_kind(line_toks: list[lomc.Tok]) -> str:
    """当前 { 属于哪种块: type (逗号分隔的声明体) / match (臂列表) / code。"""
    for t in reversed(line_toks):
        if t.kind == "ident" and t.val in _TYPE_BODY_KW:
            return "match" if t.val == "match" else "type"
        if t.kind == "ident" and t.val in _BRACE_KW:
            return "code"
    return "code"


def format_source(src: str) -> str:
    potato_from.refuse_foreign_grammar(src, "lomfmt")
    toks = [t for t in merge_ops(lomc.lex(src)) if t.kind != "eof"]
    out: list[str] = []
    line: list[str] = []
    line_toks: list[lomc.Tok] = []
    stack: list[str] = []
    pp: lomc.Tok | None = None
    prev: lomc.Tok | None = None
    depth = 0
    after_close = False  # 上一个 token 是 } : 下一条语句另起一行
    last_top = ""
    #: 缓冲行**已经带了行尾注释**（源码行号，0 = 没有）。注释一去，这一行就到此为止：
    #: 再往上追加一个 token 就会被 `//` 吞掉。source 里两行各带一条行尾注释、而格式化
    #: 把它们并成一行的情形不是假想的 —— `codegen.lomt` 那个 `while` 条件就是
    #: （实测吞掉了 `|| tok_is(… "free")) {`）。
    line_cmt = 0

    # ---- 注释（`#45`）：token 流里没有它们，所以单独扫、按源码行号插回去 ----
    own_comments, trail_comments = _scan_comments(src)
    emitted_own: set[int] = set()
    #: 已经轮到、但还没落盘的独占行注释。**惰性**是必须的：顶层声明之间的空行由下面
    #: 那段逻辑直接 `out.append("")`，若注释先落盘，空行就会插在 `///` 与它注释的
    #: 那条声明**之间**（`#45` 的第一版就是这样）。等下一次 flush 再吐出来即可。
    pending_own: list[tuple[int, int, str]] = []

    def flush() -> None:
        nonlocal line, line_toks, line_cmt
        # 缓冲行与待吐注释的**先后按源码行号定**，不能写死：
        #   * `module`（第 2 行）还缓冲着，`///`（第 3 行）已经到了 → 行先出；
        #   * `syscall4(...)`（第 6 行）缓冲着，注释在第 4-5 行 → 注释先出。
        # 写死任何一边都会把注释挪到它注释的那一行后面（`#45` 两次踩到）。
        if not line:
            for d, _ln, text in pending_own:
                out.append(INDENT * d + text)
            pending_own.clear()
            line_cmt = 0
            return
        start = line_toks[0].line if line_toks else 1 << 30
        first_cmt = pending_own[0][1] if pending_own else 1 << 30
        line_text = INDENT * depth + "".join(line)
        if start < first_cmt:
            out.append(line_text)
            line, line_toks = [], []
            for d, _ln, text in pending_own:
                out.append(INDENT * d + text)
        else:
            for d, _ln, text in pending_own:
                out.append(INDENT * d + text)
            out.append(line_text)
            line, line_toks = [], []
        line_cmt = 0
        pending_own.clear()

    def emit_own_before(srcline: int) -> None:
        for ln in sorted(k for k in own_comments if k < srcline and k not in emitted_own):
            for text in own_comments[ln]:
                pending_own.append((depth, ln, text))
            emitted_own.add(ln)

    def emit_trailing(ln: int) -> None:
        nonlocal line_cmt
        text = trail_comments.pop(ln, None)
        if text is None:
            return
        if line:
            line.append(" " + text)
            line_cmt = ln
        elif out:
            out[-1] = out[-1] + " " + text

    for t in toks:
        emit_own_before(t.line)
        if prev is not None and t.line > prev.line:
            emit_trailing(prev.line)
        # 行尾注释把这一行封住了 —— 再追加 token 就会被 `//` 吞掉。
        if line_cmt:
            flush()
        v = t.val
        if after_close:
            after_close = False
            if v not in (";", ",", "else", ")", "]", ".", "}"):
                flush()
        # 顶层声明之间空行 (同类单行声明不插空行; 多行声明之间插空行)
        if depth == 0 and t.kind == "ident" and v in TOP_KW:
            # **修饰词不是声明本身的首词**（`#46`）：`pub fn f()` / `pub extern fn f()`
            # 要待在**同一行**，起头的是 `fn` / `struct` …（空行与分组也按那一个算）。
            # 原先每个顶层关键字都 flush 一次，于是 `pub` 被留成**单独一行**、和它修饰的
            # 东西隔了一个空行 —— 那不是"格式"，那是把一项声明劈成两段。
            #
            # 判据是"**这一行到现在只有修饰词**"，而不是"刚看见 `pub`"：`extern` 不是
            # 顶层关键字（它跟在 `pub` 后面、不会触发这一支），只认 `pub` 的话
            # `pub extern fn` 仍会被劈开。
            mods_only = bool(line_toks) and all(
                x.kind == "ident" and x.val in _HEAD_MODS for x in line_toks)
            if not mods_only:
                flush()
            if v != "pub":
                if last_top and (last_top != v or out[-1] == "}"):
                    # 空行插在**注释块之前**（`#45`）：`///` 注释的是它**下面**那条声明，
                    # 插在中间会把文档注释和它注释的东西拆开。
                    i = len(out)
                    while i > 0 and _is_comment_line(out[i - 1]):
                        i -= 1
                    if i > 0 and out[i - 1] != "":
                        out.insert(i, "")
                last_top = v
        # **先看 kind，再看值**（`#640`）：字符串字面量的 `val` 是不带引号的内容，
        # 所以 `"{"` 的 `val` 就是 `{`。只比值得话，下面四支会把字面量当标点铺开 ——
        # 引号一去，`lex_tok_is(src, toks, j, "{")` 就成了 `lex_tok_is(src, toks, j, {)`，
        # 括号失衡、解析不了，而退出码是 0。语料里 8 份中了（含 `lomfmt.lomt` 自己）。
        if t.kind != "string" and v == "{":
            kind = _brace_kind(line_toks)
            if _needs_space(pp, prev, t):
                line.append(" ")
            line.append("{")
            flush()
            stack.append(kind)
            depth += 1
            pp, prev = prev, t
            continue
        if t.kind != "string" and v == "}":
            flush()
            depth = max(0, depth - 1)
            if stack:
                stack.pop()
            line.append("}")
            line_toks.append(t)
            pp, prev, after_close = prev, t, True
            continue
        if t.kind != "string" and v == ";":
            line.append(";")
            flush()
            pp, prev = prev, t
            continue
        if t.kind != "string" and v == ",":
            line.append(",")
            if stack and stack[-1] in ("type", "match"):
                flush()
            pp, prev = prev, t
            continue
        if line and _needs_space(pp, prev, t):
            line.append(" ")
        line.append(_render(t))
        line_toks.append(t)
        pp, prev = prev, t
    if prev is not None:
        emit_trailing(prev.line)
    # token 流之后还留着的独占行注释（文件末尾的注释）
    for ln in sorted(k for k in own_comments if k not in emitted_own):
        for text in own_comments[ln]:
            pending_own.append((depth, ln, text))
        emitted_own.add(ln)
    flush()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _render(t: lomc.Tok) -> str:
    if t.kind == "string":
        esc = t.val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    return t.val


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = "print"
    files: list[str] = []
    for a in argv:
        if a == "--check":
            mode = "check"
        elif a == "--write":
            mode = "write"
        else:
            files.append(a)
    if not files:
        print("用法: lomfmt.py [--check|--write] FILE...", file=sys.stderr)
        return 2
    bad = 0
    for f in files:
        p = Path(f)
        try:
            src = p.read_text(encoding="utf-8")
            want = format_source(src)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {f}: {e}", file=sys.stderr)
            return 2
        if mode == "print":
            sys.stdout.write(want)
        elif mode == "check":
            if src != want:
                bad += 1
                print(f"[DIFF] {f}: 需要格式化")
            else:
                print(f"[OK] {f}")
        else:
            if src != want:
                p.write_text(want, encoding="utf-8", newline="\n")
                print(f"[FMT] {f}")
            else:
                print(f"[OK] {f}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
