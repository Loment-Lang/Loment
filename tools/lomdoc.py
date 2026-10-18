#!/usr/bin/env python3
# lomdoc.py — 从 .lomt 生成 API 文档 (M58, docs/148)
#
# 判据: 从 .lomt 生成 API 文档 (签名 + 能力域 + 类型 + trait), 文档注释取 `///` 行。
# 用法:
#   python tools/lomdoc.py FILE [--out OUT.md] [--lom-root DIR]
# 退出码: 0 = OK / 1 = 编译错误 / 2 = 用法错误。

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def doc_of(lines: list[str], line: int) -> str:
    """取声明前连续的 /// 行 (line 是 1-based 声明行)。"""
    out: list[str] = []
    i = line - 2
    while i >= 0 and lines[i].strip().startswith("///"):
        out.append(lines[i].strip()[3:].strip())
        i -= 1
    return " ".join(reversed(out))


def _sig(f: lomentc.Func) -> str:
    args = ", ".join(f"{p.name}: {p.type}" for p in f.params)
    return f"fn {f.name}({args}) -> {f.ret}"


def render(mod: lomentc.Module, src: str, name: str) -> str:
    lines = src.splitlines()
    # 措辞与实现解耦: Loment 版 lomdoc 输出必须逐字节相同, 所以这里不写工具文件名/路径分隔符
    out = [f"# API: `{mod.name}`", "", f"> 源: `{name}` · 由 lomdoc 生成", ""]
    if mod.caps:
        out += ["## 能力域", "", "| 能力 | 空间 | 区间 | 可撤销 | 说明 |", "|---|---|---|---|---|"]
        for c in mod.caps:
            out.append(f"| `{c.name}` | `{c.space}` | [{c.lo}..{c.hi}] | "
                       f"{'是' if c.revocable else '否'} | {doc_of(lines, c.line)} |")
        out.append("")
    if mod.excluded:
        out += ["## 出界声明", ""]
        out += [f"- {x}" for x in mod.excluded]
        out.append("")
    if mod.consts:
        out += ["## 常量", "", "| 常量 | 类型 | 值 | 说明 |", "|---|---|---|---|"]
        for c in mod.consts:
            out.append(f"| `{c.name}` | `{c.type}` | {c.value} | {doc_of(lines, c.line)} |")
        out.append("")
    if mod.structs:
        out += ["## 类型", ""]
        for s in mod.structs:
            out += [f"### `struct {s.name}`", "", doc_of(lines, s.line), "",
                    "| 字段 | 类型 |", "|---|---|"]
            out += [f"| `{fn}` | `{ft}` |" for fn, ft in s.fields]
            out.append("")
    if mod.enums:
        out += ["## 枚举", ""]
        for e in mod.enums:
            out += [f"### `enum {e.name}`", "", doc_of(lines, e.line), ""]
            for v in e.variants:
                payload = f" ({e.payloads[v]})" if v in e.payloads else ""
                out.append(f"- `{e.name}::{v}`{payload}")
            out.append("")
    if mod.traits:
        out += ["## trait", ""]
        for t in mod.traits:
            ms = ", ".join(f"`{m[0]}() -> {m[1]}`" for m in t.methods)
            out += [f"### `trait {t.name}`", "", doc_of(lines, t.line), "", f"方法: {ms}", ""]
    if mod.impls:
        out += ["## impl", ""]
        for i in mod.impls:
            out.append(f"- `impl {i.trait} for {i.type}`")
        out.append("")
    if mod.funcs:
        out += ["## 函数", ""]
        for f in mod.funcs:
            out += [f"### `{_sig(f)}`", "", doc_of(lines, f.line), ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomdoc", description=".lomt -> Markdown API 文档")
    ap.add_argument("file")
    ap.add_argument("--out", metavar="PATH")
    ap.add_argument("--lom-root", default=None)
    a = ap.parse_args(argv)
    root = Path(a.lom_root) if a.lom_root else ROOT
    p = Path(a.file)
    # **别的写法要在这里拦住**（`docs/182` §1.9）。`load_unit` 会**过前门**，
    # 于是拿到的是**译文**的模块（函数带的是**译文行号**），而下面 `render` 拿
    # **原文**去按那些行号索引 —— 短文件越界、`IndexError` 甩栈；长文件不越界，
    # 于是**不崩、给出张冠李戴的文档**（`fn alpha` 拿到 `beta` 的注释），退 0。
    # 两种都是静默错，所以**认出非原生写法就拒**。
    try:
        import potato_from                                            # noqa: PLC0415
        potato_from.refuse_foreign_grammar(p.read_text(encoding="utf-8"), "lomdoc")
    except (ValueError, OSError) as e:
        print(f"[ERR] {p}: {e}", file=sys.stderr)
        return 1
    try:
        mod, deps = lomentc.load_unit(p, root)      # 唯一入口（docs/182 §1.10）
        errs = lomentc.check(mod, deps=deps)
    except lomentc.LomError as e:
        print(f"[ERR] {p}: {e}", file=sys.stderr)
        return 1
    if errs:
        print(f"[ERR] {p}: {len(errs)} 项语义错误", file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        return 1
    # 路径一律用 POSIX 分隔符: 文档要跨平台可比, Loment 版不可能知道宿主是 Windows 还是 WSL
    text = render(mod, p.read_text(encoding="utf-8"), p.as_posix())
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"[OK] {p} -> {a.out}")
    else:
        # **两个出口必须逐字节相同**（`#506`）：Windows 的文本模式会把 `\n` 翻成 `\r\n`，
        # 于是同一份输入 `>` 出去是 CRLF、`--out` 出去是 LF —— 而"文档要跨平台可比"
        # 正是这个模块存在的理由。`newline=""` 关掉翻译，原样写出。
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(newline="")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
