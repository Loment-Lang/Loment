#!/usr/bin/env python3
# loment_lsp.py — Loment 语言服务 (M56, docs/148)
#
# 判据: 补全 / 跳转 / 诊断 三个能力可用。本文件实现最小 LSP (JSON-RPC over stdio):
#   initialize / textDocument/didOpen / didChange / definition / completion
# 用 `handle()` 直接驱动可做无编辑器自测 (见 loment_tools_test.py)。
#
# 用法:
#   python tools/loment_lsp.py            # 标准 LSP stdio 模式
#   python tools/loment_lsp.py --demo F   # 自测: 打开 F 并打印诊断/补全/跳转
# 退出码: 0 = OK / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import loment_comefor  # noqa: E402
import lomentc  # noqa: E402
import potato_from  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
KEYWORDS = ["fn", "let", "if", "else", "while", "for", "in", "match", "struct", "enum",
            "trait", "impl", "const", "return", "mut", "pub", "use", "module",
            "capability", "guard", "excluded", "interrupt", "as", "register"]
TYPE_KEYWORDS = ["u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "str",
                 "ptr", "()", "Option", "Result"]


def _parse(text: str):
    # **开关必须在 parse 之前落定**（`docs/182` §1.9）：这里绕过了 `lomentc.load`，
    # 不补这一步的话，带 `set choose` 的文件会被报**假错** —— 关着的那段照样被检查，
    # 体里定义的 `fn` 还会进补全与跳转。对不含 `set`/`choose` 的源，`_apply_switches`
    # 是一次等价拷贝，所以既有行为逐字节不变。
    #
    # 注：这里**不做**预置枚举注入（`load` 会补 `Option`/`Result`），所以语言服务至今
    # 不认识这两个枚举 —— 那是**另一处**既有缺口，不在本次范围内，记在 docs/182 §1.9.2。
    #
    # **`comefor` 的展开同理**（`docs/184` §9 S4.1）：编译器在 `_apply_switches` 之后
    # 还会展开方言，这里不补的话，一份**编得过**的方言源在编辑器里会满屏假错 ——
    # 而"编得过但编辑器报错"比"两边都报错"更糟（同一处入口缺口，`docs/182` §1.9）。
    # 顺序与 `lomentc.load` 一致：先开关，再方言。
    tbl = lomentc.SwitchTable()
    tt = lomentc._apply_switches(lomc.lex(text), tbl)
    # **命令声明也是同一处入口缺口**（`docs/223`，`docs/182` §1.9）：编译器在 `load` 里
    # 从 token 流上读它（`command_label_from_tokens`），文件因此知道"我是一个命令"。
    # 这里绕过 `load` 不补这一步的话，一份**编得过**的命令单元在编辑器里会被报成
    # 「写了 `command_main` 却没有命令声明」—— 假错，而且正是 `docs/223` 要消灭的那种。
    # **同一条流水线**：先开关、再方言 —— 扫的也是**展开之后**那份 token 流
    # （`expand` 返回的是 token 表，`Parser` 与 `command_label_from_tokens` 吃的都是它）。
    toks = loment_comefor.expand(tt, text)[0]
    mod = lomentc.Parser(toks, text).parse()
    mod.switches = tbl
    mod.command_line, mod.command, mod.command_bad = \
        lomentc.command_label_from_tokens(toks)
    return mod


def _line_span(text: str, line1: int) -> tuple[int, int]:
    """那一行**第一个 token** 的跨界 `(起列, 止列)`（都 0 起）。**不是**"猜哪个符号错了"。

    `#27`：这一层的 range 原先两端是常量 `0 → 200` —— 一条 14 字符的行与一条 300 字符的
    行回来的是同一对数字，编辑器于是把**整行**画成波浪线，指不到出问题的那一格。

    **能给到什么就给到什么**：参考实现的 `check()` 只报**行**（消息形如 `12: …`），列从来
    没有算过（`#5` 记的就是这件事）—— 所以这一层**拿不到**真正的列。但拿不到不等于编一个：
    这里退到"这一行的第一个 token"，那是词法器真算出来的位置，且**确定落在对的那一行**上。

    **为什么不按消息里的符号名去行里找**：那是在猜，而"指到假的地方去"比"指得粗"更坏
    （`lomc.LomError` 那段注释：编一个 `1:1:` 是撒谎）。猜错的代价由读者付，粗的代价只是
    少一点信息。
    """
    for t in lomc.lex(text):
        if t.line == line1:
            return max(0, t.col - 1), max(0, t.col - 1) + max(1, t.len)
    return 0, 1


def _foreign_grammar(text: str) -> tuple[str, str | None] | None:
    """`#262`：文件头声明了 `choose write grammar <别名>` 时，`(规范名, 声明本身的错)`。

    没有声明、或者声明的是**原生**语法（`loment` / `rust`，见 `potato_from.NATIVE_GRAMMARS`）
    就返回 `None` —— 那种源该照常查。

    **为什么要在查之前拦下来**：一份 `choose write grammar python` 的文件**编得过**（前门会把
    它翻成 Loment），可语言服务拿 Loment 的规则去读它，报出来的是**一屏假错** —— 而"编得过
    但编辑器报错"比"两边都报错"更糟：用户会去改一份本来就对的源。
    """
    lang, err, has = potato_from.read_grammar_decl(text)
    if err:
        return lang, err
    if has and lang not in potato_from.NATIVE_GRAMMARS:
        return lang, None
    return None


def _diagnostics(text: str, path: str) -> list[dict]:
    fg = _foreign_grammar(text)
    if fg is not None:
        lang, derr = fg
        if derr:
            return [{"range": {"start": {"line": 0, "character": 0},
                               "end": {"line": 0, "character": 1}},
                     "severity": 1, "source": "loment", "message": derr}]
        # **一条 Info，不是一屏 Error**：这份源不是 Loment 写的，这一层没有它的规则。说清楚
        # "为什么这里安静"，比编一堆错更有用 —— 静默会让人以为服务挂了。
        return [{"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}},
                 "severity": 3, "source": "loment",
                 "message": f"这份源写的是 {lang} 语法（`choose write grammar {lang}`）——"
                            f" 语言服务只查 Loment，所以这里不报诊断；要真诊断就 `loment build`。"}]
    try:
        mod = _parse(text)
        errs = lomentc.check(mod)
    except lomc.LomError as e:
        # **解析期**这一支有真列（`LomError` 带 `col`），所以给得比 `_line_span` 更准；
        # `line <= 0` = 这一档指不出位置（见 `LomError` 的注释），那就别编。
        ln = max(0, e.line - 1) if e.line > 0 else 0
        c0 = max(0, e.col - 1) if e.line > 0 else 0
        return [{"range": {"start": {"line": ln, "character": c0},
                           "end": {"line": ln, "character": c0 + 1}},
                 "severity": 1, "source": "loment", "message": str(e)}]
    out = []
    for msg in errs:
        line1 = 0
        head = msg.split(":", 1)[0]
        if head.strip().isdigit():
            line1 = int(head.strip())
            msg = msg.split(":", 1)[1].strip()
        c0, c1 = _line_span(text, line1) if line1 > 0 else (0, 1)
        ln = max(0, line1 - 1)
        out.append({"range": {"start": {"line": ln, "character": c0},
                              "end": {"line": ln, "character": c1}},
                    "severity": 1, "source": "loment", "message": msg})
    return out


def _name_span(text: str, line0: int, word: str) -> tuple[int, int]:
    """声明那一行里 `word` 占哪几列。**行与名字都是已知的**，这里只是把它找出来。

    `#27`：跳转原先回 `{line, character: 0} → {line, character: 200}`，编辑器只能把光标
    扔到行首，而声明通常缩进着。这不是猜"哪个符号错了"（`_line_span` 那段说的那件事）——
    名字是调用方传进来的、行是符号表里存的，两边都确定。
    """
    lines = text.splitlines()
    if not (0 <= line0 < len(lines)):
        return 0, 0
    import re as _re
    m = _re.search(rf"\b{_re.escape(word)}\b", lines[line0])
    if not m:
        return 0, 0
    return m.start(), m.end()


def _decls(text: str) -> dict[str, dict]:
    """符号表: 名字 -> {kind, line}。"""
    mod = _parse(text)
    syms: dict[str, dict] = {}
    for f in mod.funcs:
        syms[f.name] = {"kind": "fn", "line": f.line - 1}
    for s in mod.structs:
        syms[s.name] = {"kind": "struct", "line": s.line - 1}
    for e in mod.enums:
        syms[e.name] = {"kind": "enum", "line": e.line - 1}
    for t in mod.traits:
        syms[t.name] = {"kind": "trait", "line": t.line - 1}
    for c in mod.consts:
        syms[c.name] = {"kind": "const", "line": c.line - 1}
    for c in mod.caps:
        syms[c.name] = {"kind": "capability", "line": c.line - 1}
    return syms


def _member_receiver(text: str, line: int, ch: int) -> str | None:
    """光标前是 `recv.` 的话返回 `recv`（`#28`）。认 `p.` 与 `p.x` 的前半截。

    `p[i].` / `a.b.` 暂不认 —— 返回 None，走通用补全。**宁可少给，不给错的**：补错一项
    比少补一项更打扰人（`#27` 那条"指到假的地方去比指得粗更坏"是同一个判断）。
    """
    lines = text.splitlines()
    if not (0 <= line < len(lines)):
        return None
    prefix = lines[line][:max(0, ch)]
    m = re.search(r"([A-Za-z_]\w*)\.[A-Za-z0-9_]*$", prefix)
    if not m:
        return None
    start = m.start(1)
    if start > 0 and (prefix[start - 1].isalnum() or prefix[start - 1] in "_."):
        return None
    return m.group(1)


def _lets_of(node, out: list) -> None:
    """把 `node` 底下所有 `Let` 收进 `out`（含块内的）。"""
    if isinstance(node, lomentc.Let):
        out.append(node)
    elif isinstance(node, list):
        for x in node:
            _lets_of(x, out)
    elif hasattr(node, "__dataclass_fields__"):
        for k in node.__dataclass_fields__:
            _lets_of(getattr(node, k, None), out)


def _member_items(text: str, recv: str, line1: int) -> list[dict] | None:
    """`recv` 在 `line1`（1 起）那一处是什么类型；是 `struct` 就回它的字段，否则 None。"""
    try:
        mod = _parse(text)
    except Exception:  # noqa: BLE001  编辑中的语法错不影响补全
        return None
    f = None
    for g in mod.funcs:
        if g.line <= line1 and (f is None or g.line > f.line):
            f = g
    if f is None:
        return None
    ty = None
    for p in f.params:
        if p.name == recv:
            ty = p.type
    lets: list = []
    _lets_of(f.body, lets)
    for s in lets:
        if s.name == recv and s.line < line1:
            ty = s.type
    if not ty:
        return None
    st = next((s for s in mod.structs if s.name == ty.strip()), None)
    if st is None:
        return None
    return [{"label": fn, "kind": 5, "detail": ft} for fn, ft in st.fields]


def handle(msg: dict, docs: dict[str, str]) -> list[dict]:
    """纯函数式 LSP 处理: 输入一条消息, 输出若干条待发送消息。"""
    method = msg.get("method", "")
    params = msg.get("params") or {}
    rid = msg.get("id")
    if method == "initialize":
        # `completionProvider: {}` = "支持补全，但**没有触发字符**"。
        #
        # #28：原先登的是 `{"triggerCharacters": [".", ":"]}`，而补全**不看位置**
        # （`p.` 后面回来的还是一屏关键字）——于是每敲一个点就弹一屏没用的东西，
        # 读起来就是坏的。成员补全没实现之前，**不广告触发字符**才是诚实的那一半
        # （issue 自己指的那条路）。手动 Ctrl+Space 照旧可用。
        return [{"jsonrpc": "2.0", "id": rid, "result": {
            "capabilities": {"textDocumentSync": 1,
                             "definitionProvider": True,
                             "documentFormattingProvider": True,
                             "completionProvider": {}},
            "serverInfo": {"name": "loment-lsp", "version": "0.2"}}}]
    if method in ("textDocument/didOpen", "textDocument/didChange"):
        td = params.get("textDocument") or {}
        uri = td.get("uri", "")
        text = (params.get("text") or
                (td.get("text") if "text" in td else None) or
                ((params.get("contentChanges") or [{}])[-1].get("text")))
        if text is not None:
            docs[uri] = text
        return [{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                 "params": {"uri": uri, "diagnostics": _diagnostics(docs.get(uri, ""), uri)}}]
    if method == "textDocument/definition":
        uri = (params.get("textDocument") or {}).get("uri", "")
        pos = params.get("position") or {}
        text = docs.get(uri, "")
        lines = text.splitlines()
        ln = pos.get("line", 0)
        word = ""
        if 0 <= ln < len(lines):
            import re
            # 光标可能落在词中间 (编辑器就是这样报的): 先向左扩到词首, 再取整词
            line = lines[ln]
            ch = pos.get("character", 0)
            start = min(max(ch, 0), len(line))
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                start -= 1
            m = re.match(r"[A-Za-z_]\w*", line[start:])
            if m:
                word = m.group(0)
        try:
            sym = _decls(text).get(word)
        except lomc.LomError:  # 解析不了的源 (如 `choose write grammar`) -> null, 不炸服务
            sym = None
        if not sym:
            return [{"jsonrpc": "2.0", "id": rid, "result": None}]
        s0, s1 = _name_span(text, sym["line"], word)
        return [{"jsonrpc": "2.0", "id": rid, "result": {
            "uri": uri, "range": {"start": {"line": sym["line"], "character": s0},
                                  "end": {"line": sym["line"], "character": s1}}}}]
    if method == "textDocument/completion":
        uri = (params.get("textDocument") or {}).get("uri", "")
        # `#28`：`p.` 之后要给 `Point` 的字段，不是一屏关键字。
        pos = params.get("position") or {}
        recv = _member_receiver(docs.get(uri, ""), pos.get("line", 0),
                                pos.get("character", 0))
        if recv is not None:
            got = _member_items(docs.get(uri, ""), recv, pos.get("line", 0) + 1)
            if got is not None:
                return [{"jsonrpc": "2.0", "id": rid,
                         "result": {"isIncomplete": False, "items": got}}]
        items = [{"label": k, "kind": 14} for k in KEYWORDS]
        items += [{"label": t, "kind": 6} for t in TYPE_KEYWORDS]
        try:
            for name, sym in _decls(docs.get(uri, "")).items():
                items.append({"label": name,
                              "kind": {"fn": 3, "struct": 22, "enum": 13,
                                       "trait": 8, "const": 21, "capability": 6}
                                      .get(sym["kind"], 6)})
        except Exception:  # noqa: BLE001  编辑中的语法错误不影响补全
            pass
        return [{"jsonrpc": "2.0", "id": rid, "result": {"isIncomplete": False,
                                                         "items": items}}]
    if method == "textDocument/formatting":
        # M55/M56: 格式化由语言服务提供 (编辑器侧因此不需要自己起进程)
        uri = (params.get("textDocument") or {}).get("uri", "")
        src = docs.get(uri, "")
        try:
            import lomfmt  # noqa: PLC0415
            new = lomfmt.format_source(src)
        except Exception as e:  # noqa: BLE001  语法错误时不动文档
            return [{"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32603, "message": f"格式化失败: {e}"}}]
        if new == src:
            return [{"jsonrpc": "2.0", "id": rid, "result": []}]
        lines = src.split("\n")
        return [{"jsonrpc": "2.0", "id": rid, "result": [{
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": len(lines), "character": 0}},
            "newText": new}]}]
    if method == "shutdown":
        return [{"jsonrpc": "2.0", "id": rid, "result": None}]
    if rid is not None:
        return [{"jsonrpc": "2.0", "id": rid, "result": None}]
    return []


def _serve() -> int:
    docs: dict[str, str] = {}
    while True:
        headers: dict[str, str] = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return 0
            line = line.strip()
            if not line:
                break
            k, _, v = line.decode("ascii", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        if n <= 0:
            continue
        msg = json.loads(sys.stdin.buffer.read(n).decode("utf-8"))
        for out in handle(msg, docs):
            body = json.dumps(out, ensure_ascii=False).encode("utf-8")
            sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
            sys.stdout.buffer.write(body)
            sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_lsp", description="Loment 语言服务")
    ap.add_argument("--demo", metavar="FILE", help="无编辑器自测")
    a = ap.parse_args(argv)
    if not a.demo:
        return _serve()
    p = Path(a.demo).resolve()
    text = p.read_text(encoding="utf-8")
    uri = p.as_uri()
    docs: dict[str, str] = {}
    print("== initialize ==")
    print(json.dumps(handle({"id": 1, "method": "initialize", "params": {}}, docs)[0]["result"],
                     ensure_ascii=False))
    print("== didOpen -> diagnostics ==")
    for m in handle({"method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": uri, "text": text}}}, docs):
        print(f"{len(m['params']['diagnostics'])} 条诊断")
    print("== completion ==")
    items = handle({"id": 2, "method": "textDocument/completion",
                    "params": {"textDocument": {"uri": uri}}}, docs)[0]["result"]["items"]
    print(f"{len(items)} 项: " + ", ".join(i["label"] for i in items[:10]) + " ...")
    print("== definition (第一处 fn 名) ==")
    ln = next(i for i, s in enumerate(text.splitlines()) if s.strip().startswith("fn "))
    col = text.splitlines()[ln].index("fn") + 3
    res = handle({"id": 3, "method": "textDocument/definition",
                  "params": {"textDocument": {"uri": uri},
                             "position": {"line": ln, "character": col}}}, docs)[0]["result"]
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
