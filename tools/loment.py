#!/usr/bin/env python3
# loment.py — Loment 工具链统一入口 (P6, docs/148)
#
#   loment fmt    FILE...          # M55 格式化
#   loment doc    FILE             # M58 API 文档
#   loment diag   FILE             # M64 诊断分类 + 修复建议
#   loment err    诊断.jsonl        # 报错器: 渲染编译器 --diag-out 的结构化诊断 (docs/182 §6)
#   loment ir     FILE [--objdump] # M60 IR / 机器码
#   loment test   FILE             # M61 内建测试框架 (test_* 函数)
#   loment bench  FILE [--n N]     # M62 基准 (Rust 路径 vs IR 路径)
#   loment cov    FILE [--call F]  # M63 IR 级块覆盖
#   loment build  DIR [--out DIR]  # M65/M66 增量构建 + 缓存 (见 loment_build.py)
#   loment pkg    ...              # M57 包管理 (见 lompkg.py)
#   loment lsp                     # M56 语言服务 (见 loment_lsp.py)
#
# 退出码: 子命令语义, 0 = 成功。
# 外部工具一律用参数列表调用 (shell=False), 可执行文件经 shutil.which 解析。

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402  (LomError: 把解析/装载期的诊断与内部故障分开)
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
C_TYPES = {"u8": "unsigned char", "u16": "unsigned short", "u32": "unsigned int",
           "u64": "unsigned long long", "i8": "signed char", "i16": "short",
           "i32": "int", "i64": "long long", "bool": "_Bool", "()": "void"}


def _load(file: str):
    p = Path(file)
    # **唯一入口**（`docs/182` §1.10）：预扫 → 装载 → 解析依赖。
    # 这一处原先自己拼 `load` + `resolve_deps` —— 少了预扫那一趟 `addin` 就白写，
    # 实测加 `addin` 之后当场红（"未定义的开关" + `addin` 目标根本没进单元）。
    try:
        mod, deps = lomentc.load_unit(p, ROOT)
    except OSError as e:
        # 与 `lomentc.main` 那条同源（`issue #8`）：文件这一层的失败不再甩 Python 栈。
        raise SystemExit(f"loment: 读不了输入文件 {p}: {e.strerror or e}")
    errs = lomentc.check(mod, deps=deps)
    if errs:
        for e in errs:
            print("  " + e, file=sys.stderr)
        raise SystemExit(1)
    return p, mod, deps


def _picked(mod: lomentc.Module, prefix: str) -> list[lomentc.Func]:
    return [f for f in mod.funcs
            if f.name.startswith(prefix) and not f.params and f.ret in ("bool", "u32")]


def cmd_fmt(a) -> int:
    import lomfmt
    return lomfmt.main(a.files)


def cmd_doc(a) -> int:
    import lomdoc
    return lomdoc.main([a.file] + (["--out", a.out] if a.out else []))


def cmd_diag(a) -> int:
    import loment_diag
    return loment_diag.main([a.file] + (["--json"] if a.json else []))


#: 报错器的源码与**已编产物**。产物落在 `loment/build/`（与 stage1 同处）：仓库侧是开发入口，
#: 包里那一份由 `loment_dist` 从同一条自举链出。
ERR_SRC = ROOT / "loment" / "tools" / "lomenterr.lomt"
ERR_EXE = ROOT / "loment" / "build" / ("lomenterr.exe" if sys.platform == "win32"
                                       else "lomenterr.elf")


def build_lomenterr(force: bool = False) -> Path:
    """编 `loment/tools/lomenterr.lomt` 成本机能跑的可执行文件。**按源码内容哈希判新鲜**
    —— 不用 mtime: Windows 上时间戳粒度粗, "改完立刻跑"会拿到旧产物, 而那正是最需要对的
    一刻 (与 lompi「身份是内容哈希」同一条口径)。
    """
    import hashlib
    import lomelf
    key = hashlib.sha256(ERR_SRC.read_bytes()).hexdigest()
    # 新鲜度戳**平台无关的名字** —— 从可执行名派生的话, Windows 上是 lomenterr.exe.sha、
    # Linux 上是 lomenterr.elf.sha, `.gitignore` 就得跟着平台写两遍。
    stamp = ERR_EXE.parent / "lomenterr.src-sha"
    if not force and ERR_EXE.exists() and stamp.exists() \
            and stamp.read_text(encoding="utf-8").strip() == key:
        return ERR_EXE
    mod, deps = lomentc.load_unit(ERR_SRC, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise SystemExit(f"lomenterr.lomt 自己检查不过: {errs[:3]}")
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    raw = (lomelf.compile_pe if sys.platform == "win32" else lomelf.compile_ll)(ll)[0]
    ERR_EXE.parent.mkdir(parents=True, exist_ok=True)
    # 临时名 + os.replace: 并行门禁里可能有另一个进程正**执行**着这份产物, 直接覆写会撞
    # PermissionError (loment_dist 的 `_write_shared` 记着同一件事)。
    tmp = ERR_EXE.with_name(ERR_EXE.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, ERR_EXE)
    ERR_EXE.chmod(0o755)
    stamp.write_text(key, encoding="utf-8")
    return ERR_EXE


def cmd_err(a) -> int:
    """`loment err 诊断.jsonl [--no-color] [--short|--json]` —— 渲染结构化诊断 (docs/182 §6)。

    **包里的自动渲染在启动器那边** (`bin/loment` / `loment.cmd` 在 check/build/run 失败时
    自己起 `lomenterr`), 仓库侧这个子命令是同一个程序的开发入口。`--no-color` 与
    `lomcli` 那套同名同义（默认上色）；管道里要干净输出就带上它。`--short` / `--json`
    是另外两个出口 (**顺带关色**, docs/182 §15)。三个开关**原样转给渲染器**，
    这里不复制它们的语义 —— 一处解释，另一处照抄，正是本仓最不想要的。
    """
    exe = build_lomenterr(force=a.rebuild)
    flags = []
    if a.no_color:
        flags.append("--no-color")
    if a.short:
        flags.append("--short")
    if a.json:
        flags.append("--json")
    return subprocess.run([str(exe)] + flags + [a.file], shell=False).returncode


def cmd_ir(a) -> int:
    p, mod, deps = _load(a.file)
    text = lomentc.emit_llvm(mod, ROOT, deps)
    if not a.objdump:
        sys.stdout.write(text)
        return 0
    with tempfile.TemporaryDirectory() as td:
        binp = Path(td) / "m.bin"
        # 机器码由仓库自己的原生后端出（不再经 clang），再用 llvm-objdump 反汇编看。
        # 原生后端要用户态入口，纯函数片段没有 —— 只为这个**调试视图**补一个空 `_start`。
        src = text
        if "_start" not in text:
            src = text + "\ndefine void @_start() {\nentry:\n  ret void\n}\n"
        import lomelf
        binp.write_bytes(lomelf.compile_ll(src)[0])
        d = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", str(binp)],
            capture_output=True, text=True, shell=False)
        sys.stdout.write(text + "\n; ==== 机器码 (lomelf 出的 ELF, llvm-objdump -d) ====\n" + d.stdout)
    return 0


def cmd_test(a) -> int:
    p, mod, deps = _load(a.file)
    tests = [f for f in mod.funcs if f.name.startswith("test_") and not f.params
             and f.ret == "bool"]
    if not tests:
        print("[ERR] 没有 test_*() -> bool 函数", file=sys.stderr)
        return 2
    rust = lomentc.emit_rust(mod, ROOT, deps)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(rust, encoding="utf-8")
        harness = ["include!(\"m.rs\");", "fn main() {",
                   "    let mut pass = 0u32; let mut total = 0u32;"]
        for f in tests:
            harness += ["    total += 1;",
                        f"    if {f.name}() {{ pass += 1; println!(\"PASS {f.name}\"); }}",
                        f"    else {{ println!(\"FAIL {f.name}\"); }}"]
        harness += ["    println!(\"RESULT: {}/{} PASS\", pass, total);",
                    "    if pass != total { std::process::exit(1); }", "}"]
        (Path(td) / "main.rs").write_text("\n".join(harness) + "\n", encoding="utf-8")
        exe = Path(td) / "t.exe"
        r = subprocess.run(
            [shutil.which("rustc") or "rustc", "-O", "-o", str(exe),
             str(Path(td) / "main.rs")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        run = subprocess.run([shutil.which(str(exe)) or str(exe)],
                             capture_output=True, text=True, shell=False)
        print(run.stdout.strip())
        return run.returncode


def cmd_bench(a) -> int:
    p, mod, deps = _load(a.file)
    benches = _picked(mod, "bench_")
    if not benches:
        print("[ERR] 没有 bench_*() 函数", file=sys.stderr)
        return 2
    rust = lomentc.emit_rust(mod, ROOT, deps)
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    rows = []
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(rust, encoding="utf-8")
        h = ["include!(\"m.rs\");", "fn main() {",
             f"    let n = {a.n}u32;", "    let mut acc = 0u32;"]
        for f in benches:
            h += ["    let t = std::time::Instant::now();",
                  f"    for _ in 0..n {{ acc = acc.wrapping_add({f.name}()); "
                  "std::hint::black_box(acc); }",
                  f"    println!(\"RUST {f.name} {{}} {{}}\", t.elapsed().as_nanos(), acc);"]
        h += ["}"]
        (Path(td) / "main.rs").write_text("\n".join(h) + "\n", encoding="utf-8")
        exe = Path(td) / "b.exe"
        r = subprocess.run(
            [shutil.which("rustc") or "rustc", "-O", "-o", str(exe),
             str(Path(td) / "main.rs")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        rust_out = subprocess.run([shutil.which(str(exe)) or str(exe)],
                                  capture_output=True, text=True, shell=False).stdout
        for line in rust_out.splitlines():
            _, name, ns, _acc = line.split()
            rows.append([name, int(ns), None])
        (Path(td) / "m.ll").write_text(ir, encoding="utf-8")
        c = ["#include <stdio.h>", "#include <time.h>"]
        for f in benches:
            c.append(f"extern {C_TYPES.get(f.ret, 'unsigned int')} {f.name}(void);")
        c += ["int main(void) {", f"    unsigned long n = {a.n}UL;",
              "    volatile unsigned long acc = 0;"]
        for f in benches:
            c += ["    {",
                  "    struct timespec ts0, ts1;",
                  "    timespec_get(&ts0, TIME_UTC);",
                  f"    for (unsigned long i = 0; i < n; i++) acc += {f.name}();",
                  "    timespec_get(&ts1, TIME_UTC);",
                  "    double ns = (double)(ts1.tv_sec - ts0.tv_sec) * 1e9",
                  "              + (double)(ts1.tv_nsec - ts0.tv_nsec);",
                  f"    printf(\"IR {f.name} %.0f %lu\\n\", ns, acc);",
                  "    }"]
        c += ["    return 0;", "}"]
        (Path(td) / "drv.c").write_text("\n".join(c) + "\n", encoding="utf-8")
        exe2 = Path(td) / "b2.exe"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "-O1", "-o", str(exe2), str(Path(td) / "drv.c"), str(Path(td) / "m.ll")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        ir_out = subprocess.run([shutil.which(str(exe2)) or str(exe2)],
                                capture_output=True, text=True, shell=False).stdout
        for line in ir_out.splitlines():
            _, name, ns, _acc = line.split()
            for row in rows:
                if row[0] == name:
                    row[2] = int(float(ns))
    print("| 函数 | Rust 路径 (ns) | IR 路径 (ns) | 比值 |")
    print("|---|---|---|---|")
    for name, rns, irns in rows:
        ratio = f"{irns / rns:.2f}x" if rns and irns else "-"
        print(f"| `{name}` | {rns} | {irns} | {ratio} |")
    return 0


def cmd_cov(a) -> int:
    p, mod, deps = _load(a.file)
    fn = next((f for f in mod.funcs if f.name == a.call), None)
    if fn is None:
        print(f"[ERR] 找不到入口函数 {a.call}", file=sys.stderr)
        return 2
    if fn.params or fn.ret not in C_TYPES:
        print(f"[ERR] 覆盖入口必须是零参标量函数 ({fn.ret} {fn.name})", file=sys.stderr)
        return 2
    ir = lomentc.emit_llvm(mod, ROOT, deps, coverage=True)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.ll").write_text(ir, encoding="utf-8")
        drv = f"""#include <stdio.h>
extern unsigned long long __loment_cov[256];
extern const unsigned long long __loment_cov_n;
extern {C_TYPES[fn.ret]} {fn.name}(void);
int main(void) {{
    (void){fn.name}();
    unsigned long long n = __loment_cov_n, hit = 0;
    for (unsigned long long i = 0; i < n; i++) if (__loment_cov[i]) hit++;
    printf("COV %llu/%llu %.1f%%\\n", hit, n, n ? 100.0 * hit / n : 0.0);
    return 0;
}}
"""
        (Path(td) / "drv.c").write_text(drv, encoding="utf-8")
        exe = Path(td) / "cov.exe"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "-O0", "-o", str(exe), str(Path(td) / "drv.c"), str(Path(td) / "m.ll")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        out = subprocess.run([shutil.which(str(exe)) or str(exe)],
                             capture_output=True, text=True, shell=False).stdout.strip()
    print(f"{p.name}: {out}")
    return 0


def cmd_dbg(a) -> int:
    """M75: 源码级符号化 —— 地址 <-> 源行 (基于 DWARF 行表)。"""
    import re
    # **别的写法不支持**（`docs/182` §1.9 那条"读 L1 源的入口"轴）：`_load` **过前门**，
    # 拿到的是**译文**的模块，而 DWARF 行表里写的是**译文**的行号，`mod.src` 又是 `None`
    # （`lomentc.load`：翻译出来的单元**不声称**源文件是那一份）。于是报出来的 `文件:行`
    # **两头都不对**：路径丢了一截、分隔符混用，行号在用户那份文件里根本不存在
    # （实测：一份 **9 行**的文件报 `…\t.lomt:10`）—— 一个**指向不存在的地方**的成功输出。
    try:
        import potato_from
        potato_from.refuse_foreign_grammar(Path(a.file).read_text(encoding="utf-8"),
                                           "loment dbg")
    except (ValueError, OSError) as e:
        print(f"[ERR] {a.file}: {e}", file=sys.stderr)
        return 1
    p, mod, deps = _load(a.file)
    ir = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    with tempfile.TemporaryDirectory() as td:
        ll = Path(td) / "m.ll"
        ll.write_text(ir, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "--target=x86_64-unknown-none", "-ffreestanding", "-g", "-c",
             str(ll), "-o", str(obj)], capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        dump = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", "-l", str(obj)], capture_output=True, text=True, shell=False).stdout
    rows: list[tuple[int, str, str]] = []   # (addr, src, fn)
    cur_fn, cur_src = "?", ""
    for ln in dump.splitlines():
        s = ln.strip()
        m = re.match(r"^([0-9a-f]+) <([^>]+)>:", s)
        if m:
            cur_fn = m.group(2)
            continue
        if s.startswith(";"):
            cur_src = s.lstrip("; ").strip()
            continue
        m = re.match(r"^([0-9a-f]+):", s)
        if m and cur_src:
            rows.append((int(m.group(1), 16), cur_src, cur_fn))
    if a.addr is not None:
        addr = int(a.addr, 0)
        hit = [r for r in rows if r[0] <= addr]
        if not hit:
            print(f"[ERR] 地址 {addr:#x} 无行表条目", file=sys.stderr)
            return 1
        best = max(hit, key=lambda r: r[0])
        print(f"{addr:#x} -> {best[1]} ({best[2]}+{addr - best[0]:#x})")
        return 0
    fn = a.func
    sel = [r for r in rows if r[2] == fn]
    if not sel:
        fns = sorted({r[2] for r in rows if r[2] != "?"})
        print(f"[ERR] 无函数 {fn}; 可用: {', '.join(fns[:8])}", file=sys.stderr)
        return 1
    srcs = []
    for _, s, _ in sel:
        if s not in srcs:
            srcs.append(s)
    lines = [int(x.rsplit(":", 1)[1]) for x in srcs if x.rsplit(":", 1)[-1].isdigit()]
    print(f"fn {fn}: {len(sel)} 条指令, 源行 {min(lines)}..{max(lines)} ({len(srcs)} 个)")
    for s in srcs[:12]:
        print(f"  {s}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment", description="Loment 工具链")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fmt"); f.add_argument("files", nargs="+"); f.set_defaults(fn=cmd_fmt)
    d = sub.add_parser("doc"); d.add_argument("file"); d.add_argument("--out")
    d.set_defaults(fn=cmd_doc)
    g = sub.add_parser("diag"); g.add_argument("file"); g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cmd_diag)
    e = sub.add_parser("err"); e.add_argument("file")
    e.add_argument("--rebuild", action="store_true", help="忽略缓存, 重编报错器")
    e.add_argument("--no-color", action="store_true",
                   help="不上色（与 lomcli 同一个开关；默认上色）")
    e.add_argument("--short", action="store_true",
                   help="一行一条（可 grep；顺带关色）")
    e.add_argument("--json", action="store_true",
                   help="一行一个对象，含说明卡字段（给编辑器与 CI；顺带关色）")
    e.set_defaults(fn=cmd_err)
    i = sub.add_parser("ir"); i.add_argument("file"); i.add_argument("--objdump", action="store_true")
    i.set_defaults(fn=cmd_ir)
    t = sub.add_parser("test"); t.add_argument("file"); t.set_defaults(fn=cmd_test)
    b = sub.add_parser("bench"); b.add_argument("file"); b.add_argument("--n", type=int, default=200000)
    b.set_defaults(fn=cmd_bench)
    c = sub.add_parser("cov"); c.add_argument("file"); c.add_argument("--call", default="cov_main")
    c.set_defaults(fn=cmd_cov)
    g = sub.add_parser("dbg"); g.add_argument("file")
    g.add_argument("--fn", dest="func", default=None, help="按函数列出源行")
    g.add_argument("--addr", default=None, help="地址 -> 源行")
    g.set_defaults(fn=cmd_dbg)
    for name in ("build", "pkg", "lsp", "lib"):
        s = sub.add_parser(name)
        s.add_argument("rest", nargs=argparse.REMAINDER)
        s.set_defaults(fn=None, delegate=name)
    a = ap.parse_args(argv)
    if getattr(a, "delegate", None):
        if a.delegate == "pkg":
            import lompkg
            return lompkg.main(a.rest)
        if a.delegate == "lib":
            import lomlib
            return lomlib.main(a.rest)
        if a.delegate == "lsp":
            import loment_lsp
            return loment_lsp.main(a.rest)
        import loment_build
        return loment_build.main(a.rest)
    try:
        return a.fn(a)
    except lomc.LomError as e:
        # 解析期/装载期错误是**正常诊断**, 不是内部故障 —— 不该甩 Python 回溯给用户
        # (2026-09-15 用户实测: match 臂写成表达式、无值 return, 两条都只看到 traceback)。
        # 这类消息目前**没有错误码** (E001–E018 只覆盖 checker 的语义诊断, 见 docs/158 §1)。
        print(f"[ERR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
