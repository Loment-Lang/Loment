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
import potato_from  # noqa: E402  (DEFAULT_EDITION: 版次表的单一真源)

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
    # **编译期告知版次**（`docs/296` §1.1）：用户写的拼法 -> 规范名。
    _sp = getattr(mod, "spelling", "") or "default"
    _prof = getattr(mod, "profile", potato_from.DEFAULT_EDITION)
    # 标签与自举驱动的措辞对齐（2026.3 说全名：`Tur 3` 会被读成另一个版次）。
    _label = {"tur2026.1": "1", "tur2026.2": "2", "tur2026.3": "2026.3"}.get(
        _prof, _prof.rsplit(".", 1)[-1])
    print(f"loment: grammar {_sp} -> {_prof} (Tur {_label})", file=sys.stderr)
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
    # 负的迭代次数没有意义，而它会被原样拼进 `let n = -5u32;` —— 于是用户看到的是
    # **rustc** 的报错（`#507`），不是我们的用法错误。在入口挡住。
    if a.n < 0:
        print("[ERR] --n 必须是非负整数", file=sys.stderr)
        return 2
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


def _lspe_answers(a) -> dict:
    """`--answers FILE` 与 `--answer SITE=B`（可重复）合成一张表（`#755`）。"""
    import lspe
    out: dict = {}
    if getattr(a, "answers", None):
        out.update(lspe.load_answers(a.answers))
    for raw in (getattr(a, "answer", None) or []):
        site, _, ans = raw.partition("=")
        if not site or not ans:
            raise SystemExit(f"loment: --answer 要写成 SITE=B，得到 {raw!r}")
        out[site.strip()] = ans.strip()
    return out


def _lspe_input(a, *, check_it: bool = True):
    """装载一份单元并**把前门的拒绝变成一行话**（`#1087` / `#1181`）。

    目录、缺文件、前门拒（`FrontDoorRefused` / `NotRepresentable`）都不该甩 Python
    栈 —— 用户看到的是 `[ERR] …` 而不是 traceback。
    """
    p = Path(a.file)
    if not p.exists():
        raise SystemExit(f"loment: 文件不存在：{p}")
    if p.is_dir():
        raise SystemExit(f"loment: 这是目录，不是一份单元：{p}")
    try:
        mod, deps = lomentc.load_unit(
            p, ROOT, lspe_level=getattr(a, "lspe_level", 3),
            lspe_mode=getattr(a, "lspe_mode", "full"),
            answers=_lspe_answers(a))
    except OSError as e:
        raise SystemExit(f"loment: 读不了 {p}: {e.strerror or e}")
    except lomentc.LomError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        raise SystemExit(1)
    if check_it:
        errs = lomentc.check(mod, deps=deps)
        if errs:
            for e in errs:
                print(f"[ERR] {e}", file=sys.stderr)
            raise SystemExit(1)
    return p, mod, deps


def cmd_lspe_state(a) -> int:
    """`loment lspe state FILE` —— 打印这份单元的 LSPE 台账（`docs/262` §8）。"""
    import json
    p, mod, deps = _lspe_input(a)
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    lp = doc["lspe"]
    print(json.dumps(lp, ensure_ascii=False, indent=2))
    # LSPC 的贴条（`docs/262` §5.10 / §5.16 / `#786`）：**永不沉默** —— 即使
    # 一个旁路块都没有，"0 个未审计块"也是一条可核的声明。走 stderr：
    # stdout 是台账 JSON（上面那一块），机器读它。
    import lspe
    for _ln in lspe.lspc_lines(lp):
        print(_ln, file=sys.stderr)
    # AI 检查器的怀疑（Q014）：只在显式给 `--replies` 时出现，永不改判决。
    _rep = getattr(a, "replies", None)
    if _rep:
        if not lp.get("aicheck"):
            print("[LSPE] 这份产物没开 `lspe_aicheck_true` —— 不读回答",
                  file=sys.stderr)
        else:
            _res = _ai_ingest(_rep, getattr(mod, "lspe_sites", []), mod.name)
            if _res is None:
                return 1
            for _ln in lspe.aicheck_render(_res):
                print(_ln, file=sys.stderr)
    if lp["state"] == "lspe_close_admin":
        print("[LSPE] the audit is closed (lspe_close_admin): the checks still ran; "
              "only the ledger is off.", file=sys.stderr)
    return 0


def cmd_lspe_why(a) -> int:
    """`loment lspe why <site> FILE` —— 重建并打印那个站点的派生链（`#749`）。"""
    import lspe
    file = getattr(a, "file", None)
    if not file:
        print("用法: loment lspe why <site> <file>", file=sys.stderr)
        return 2
    a.file = file
    p, mod, deps = _lspe_input(a, check_it=False)
    ledger = lspe.build_ledger(
        sites=getattr(mod, "lspe_sites", []),
        state=mod.chooses.get("lspe", lomentc.CORE_DEFAULTS["lspe"]),
        set_by=("source" if "lspe" in mod.chooses else "default"),
        level=int(getattr(mod, "lspe_level", 3)),
        toks=getattr(mod, "toks", []), unit=mod.name,
        deps=[getattr(d, "toks", []) for d in deps],
        passes=getattr(mod, "lspe_passes", []),
        mode=str(getattr(mod, "lspe_mode", "full")),
        answers=getattr(mod, "lspe_answers", None),
        sidecar=True)
    trees = ledger.get("_trees") or {}
    want = a.site.strip()
    hit = want if want in trees else None
    if hit is None:
        cand = sorted(sid for sid in trees if sid.startswith(want) or want in sid)
        if len(cand) == 1:
            hit = cand[0]
        elif not cand:
            print(f"[LSPE] 没有站点 {want!r}（这份单元有 {len(trees)} 个站点）",
                  file=sys.stderr)
            return 1
        else:
            print(f"[LSPE] {want!r} 命中 {len(cand)} 个站点，写全一点：",
                  file=sys.stderr)
            for sid in cand[:8]:
                print("  " + sid, file=sys.stderr)
            return 1
    for line in lspe.why_lines(trees[hit]["tree"]):
        print(line)
    print(f"proof_hash   {trees[hit]['hash']}")
    return 0


def cmd_lspe_verify(a) -> int:
    """`loment lspe verify <artifact> <sidecar>` —— **不重编译**地核对旁挂（`#749`）。"""
    import json
    import lspe
    if not getattr(a, "sidecar", None):
        print("用法: loment lspe verify <artifact> <sidecar>", file=sys.stderr)
        return 2
    try:
        artifact = json.loads(Path(a.artifact).read_text(encoding="utf-8"))
        side = json.loads(Path(a.sidecar).read_text(encoding="utf-8"))
    except OSError as e:
        print(f"[ERR] 读不了：{e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"[ERR] 不是 JSON：{e}", file=sys.stderr)
        return 2
    bad = lspe.verify(artifact, side)
    if bad:
        for b in bad:
            print(f"[LSPE] {b}", file=sys.stderr)
        return 1
    print(f"[LSPE] ok: {len(side.get('sites') or {})} site(s), "
          f"root {side.get('root')}")
    return 0


def _ai_ingest(path_or_obj, sites, unit):
    """把一份回答（路径或已解析的 dict）变成 Q014；出错返回 `None` 并打印原因。"""
    import json
    import lspe
    try:
        replies = (json.loads(Path(path_or_obj).read_text(encoding="utf-8"))
                   if isinstance(path_or_obj, (str, Path)) else path_or_obj)
        return lspe.aicheck_ingest(replies, sites, unit)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERR] 读不了回答：{e}", file=sys.stderr)
    except lspe.LspeError as e:
        print(f"[ERR] {e}", file=sys.stderr)
    return None


def cmd_lspe_aicheck(a) -> int:
    """`loment lspe aicheck FILE` —— AI 检查器（`docs/262` §10.7）。

    三条通道，全部在编译期之外：

      --emit PATH        出请求（确定性；默认打到 stdout）
      --ask-cmd CMD      外部程序：bundle 进 stdin、replies 出 stdout
      --ask              内置 Anthropic 通道（密钥只从环境读）
      --replies PATH     吃一份已存在的回答（版本库/手写）

    **AI 是问题源，不是证据源**：回答只产 Q014，永不改变台账判决。
    """
    import json
    import lspe
    p, mod, deps = _lspe_input(a, check_it=False)
    ledger = lspe.build_ledger(
        sites=getattr(mod, "lspe_sites", []),
        state=mod.chooses.get("lspe", lomentc.CORE_DEFAULTS["lspe"]),
        set_by=("source" if "lspe" in mod.chooses else "default"),
        level=int(getattr(mod, "lspe_level", 3)),
        toks=getattr(mod, "toks", []), unit=mod.name,
        deps=[getattr(d, "toks", []) for d in deps],
        passes=getattr(mod, "lspe_passes", []),
        mode=str(getattr(mod, "lspe_mode", "full")),
        answers=getattr(mod, "lspe_answers", None),
        aicheck=(getattr(mod, "slots", {}) or {}).get("aicheck") == "true")
    if not ledger.get("aicheck"):
        print("[LSPE] 这个单元没有 `choose lspe_aicheck_true` —— AI 检查器没开",
              file=sys.stderr)
        return 2
    sites = getattr(mod, "lspe_sites", [])
    by_id = {s["id"]: s for s in sites}
    lines = Path(p).read_text(encoding="utf-8").splitlines()
    bundle = lspe.aicheck_bundle(mod.name, ledger.get("_questions"), by_id, lines,
                                 model=getattr(a, "model", None))

    ask_cmd = getattr(a, "ask_cmd", None)
    do_ask = bool(getattr(a, "ask", False))
    replies_path = getattr(a, "replies", None)
    chosen = int(bool(ask_cmd)) + int(do_ask) + int(bool(replies_path))
    if chosen > 1:
        print("[ERR] `--ask-cmd` / `--ask` / `--replies` 三选一",
              file=sys.stderr)
        return 2

    if getattr(a, "emit", None):
        Path(a.emit).write_text(lspe.aicheck_dumps(bundle), encoding="utf-8",
                                newline="\n")
        print(f"[LSPE] aicheck: {len(bundle['requests'])} 个请求 -> {a.emit}")

    if ask_cmd or do_ask:
        try:
            replies = lspe.aicheck_ask(
                bundle, cmd=ask_cmd, model=getattr(a, "model", None),
                timeout=float(getattr(a, "timeout", 120)))
        except lspe.LspeError as e:
            print(f"[ERR] {e}", file=sys.stderr)
            return 2
        out = getattr(a, "replies_out", None)
        if out:
            Path(out).write_text(lspe.aicheck_dumps(replies), encoding="utf-8",
                                 newline="\n")
            print(f"[LSPE] aicheck: 回答 -> {out}")
    elif replies_path:
        replies = replies_path
    else:
        if not getattr(a, "emit", None):
            print(lspe.aicheck_dumps(bundle), end="")
        return 0

    result = _ai_ingest(replies, sites, mod.name)
    if result is None:
        return 1
    if getattr(a, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for line in lspe.aicheck_render(result):
            print(line)
    if getattr(a, "strict", False) and any(
            f["verdict"] == "suspicious" for f in result["findings"]):
        return 1
    return 0


def cmd_lspe_loans(a) -> int:
    """`loment lspe loans FILE` —— E10 借用检查器的 loan 图（`docs/317`）。

    这一层是**报告**：不改编译判决、不写台账。它把 `&x` / `&mut x` 变成 loan，
    按 A1 的口径（kill = 最后一次使用）算存活，报冲突与悬垂。
    """
    import json
    import lspe
    p, mod, deps = _lspe_input(a, check_it=False)
    dims = lomentc.effective_dims(mod)
    if dims.get("lifetime", "lifetime_no") == "lifetime_no":
        print("[LSPE] 这份单元关了生命周期（`lifetime_no`）—— 没有 loan 可查",
              file=sys.stderr)
        return 0
    rep = lspe.loans_analyze(getattr(mod, "toks", []), mod.name)
    if getattr(a, "json", False):
        print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for line in lspe.loans_text(rep):
            print(line)
        for q in rep["questions"]:
            print(f"[LSPE] {q['code']} {q['site']}: {q['claim']}",
                  file=sys.stderr)
    if getattr(a, "strict", False) and (rep["conflicts"] or rep["findings"]):
        return 1
    return 0


def cmd_lspe_effects(a) -> int:
    """`loment lspe effects FILE` —— E11 效应检查器（`docs/341`）。

    报告面：读源码的 token 流，**不经过解析器** —— 效应语法还没有进语言，
    但检查器要先能对形状说话。不改编译判决、不写台账、不改源码。
    """
    import json
    import lspe
    path = Path(a.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"loment lspe effects: 读不了 {a.file}: {e}", file=sys.stderr)
        return 1
    toks = lomc.lex(text)
    rep = lspe.effects_analyze(toks, path.stem)
    if getattr(a, "json", False):
        print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for line in lspe.effects_text(rep):
            print(line)
        for q in rep["questions"]:
            print(f"[LSPE] {q['code']} {q['site']}: {q['claim']}",
                  file=sys.stderr)
    if getattr(a, "strict", False):
        hard = [f for f in rep["findings"]
                if f["kind"] not in ("capture_unproved", "undeclared_row")]
        if hard:
            return 1
    return 0


def _lspc_mode(a, mod) -> str:
    import lspe
    if getattr(a, "lspc", None):
        return a.lspc
    return lspe.lspc_default(mod.chooses.get("lspe", lomentc.CORE_DEFAULTS["lspe"]),
                             str(getattr(mod, "lspe_mode", "full")))


def cmd_lspc_check(a) -> int:
    """`loment lspc check FILE` —— 只跑前端，出预检单，**不写文件**（`#758`）。

    默认退出码 0；`--lspc=strict` 只对**未 ack 的高风险族**阻断。
    这也是"编译前"的操作形态：类型检查跑过、代码生成没跑。
    """
    import json
    import lspe
    p, mod, deps = _lspe_input(a, check_it=False)
    # **检查错误不妨碍出单**（`#758` 验收第 1 条）：LSPC 是告知，不是闸门。
    errs = lomentc.check(mod, deps=deps)
    ledger = lspe.build_ledger(
        sites=getattr(mod, "lspe_sites", []),
        state=mod.chooses.get("lspe", lomentc.CORE_DEFAULTS["lspe"]),
        set_by=("source" if "lspe" in mod.chooses else "default"),
        level=int(getattr(mod, "lspe_level", 3)),
        toks=getattr(mod, "toks", []), unit=mod.name,
        deps=[getattr(d, "toks", []) for d in deps],
        passes=getattr(mod, "lspe_passes", []),
        mode=str(getattr(mod, "lspe_mode", "full")),
        answers=getattr(mod, "lspe_answers", None),
        dims=lomentc.effective_dims(mod),
        facts=lomentc._lspe_facts(mod, deps),
        sidecar=bool(getattr(a, "previous_sidecar", None)),
        aicheck=(getattr(mod, "slots", {}) or {}).get("aicheck") == "true")
    # 跨构建记忆（`#760`）：上一轮的 deferred / 过期假设 / close_admin。
    prev_art = prev_side = None
    for attr, fname in (("previous", "artifact"), ("previous_sidecar", "sidecar")):
        path = getattr(a, attr, None)
        if not path:
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERR] 读不了 {fname} {path}: {e}", file=sys.stderr)
            raise SystemExit(2)
        if attr == "previous":
            prev_art = data
        else:
            prev_side = data
    previous = lspe.previous_items(prev_art, prev_side, ledger.get("_trees"))
    mode = _lspc_mode(a, mod)
    sheet = lspe.lspc_sheet(
        getattr(mod, "lspe_sites", []), ledger,
        dims=lomentc.effective_dims(mod),
        facts=lomentc._lspe_facts(mod, deps),
        previous=previous,
        mode=mode,
        acks=lspe.parse_acks(getattr(mod, "lspe_answers", {}) or {}))
    if getattr(a, "json", False):
        print(json.dumps(sheet, ensure_ascii=False, indent=2))
    else:
        for line in lspe.lspc_text(sheet):
            print(line)
        # AI 检查器的怀疑（Q014）贴在预检单后面：它是**告知**，不阻断。
        _rep = getattr(a, "replies", None)
        if _rep:
            if not ledger.get("aicheck"):
                print("[LSPE] 这份单元没开 `lspe_aicheck_true` —— 不读回答",
                      file=sys.stderr)
            else:
                _res = _ai_ingest(_rep, getattr(mod, "lspe_sites", []), mod.name)
                if _res is None:
                    return 1
                for _f in _res["findings"]:
                    print(f"[LSPC] Q014 {_f['site']} ({_f['verdict']}): "
                          f"{_f['note']}")
                for _sid in _res["unknown"]:
                    print(f"[LSPC] Q014 未知站点 {_sid}")
        for e in errs:
            print(f"[ERR] {e}", file=sys.stderr)
    if mode == "strict" and sheet["blocking"]:
        print("[LSPC] strict: 未 ack 的高风险族阻断 —— " + ", ".join(sheet["blocking"]),
              file=sys.stderr)
        return 1
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
    lp = sub.add_parser("lspe")
    lsub = lp.add_subparsers(dest="lspe_cmd", required=True)
    lps = lsub.add_parser("state")
    lps.add_argument("file")
    lps.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4))
    lps.add_argument("--lspe-mode", default="full", choices=("full", "quiet", "strict"))
    lps.add_argument("--answer", action="append", metavar="SITE=B")
    lps.add_argument("--answers", default=None, metavar="FILE")
    lps.add_argument("--replies", default=None, metavar="PATH",
                     help="把 AI 检查器的回答（Q014）一并列出")
    lps.set_defaults(fn=cmd_lspe_state)
    lpw = lsub.add_parser("why")
    lpw.add_argument("site")
    lpw.add_argument("file", nargs="?")
    lpw.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4))
    lpw.add_argument("--lspe-mode", default="full", choices=("full", "quiet", "strict"))
    lpw.add_argument("--answer", action="append", metavar="SITE=B")
    lpw.add_argument("--answers", default=None, metavar="FILE")
    lpw.set_defaults(fn=cmd_lspe_why)
    lpv = lsub.add_parser("verify")
    lpv.add_argument("artifact")
    lpv.add_argument("sidecar", nargs="?")
    lpv.set_defaults(fn=cmd_lspe_verify)
    lpa = lsub.add_parser("aicheck")
    lpa.add_argument("file")
    lpa.add_argument("--emit", default=None, metavar="PATH",
                     help="写一份审查请求（默认打到 stdout）")
    lpa.add_argument("--replies", default=None, metavar="PATH",
                     help="吃一份模型回答（只产 Q014，不改进判决）")
    lpa.add_argument("--ask-cmd", default=None, metavar="CMD",
                     help="外部程序：bundle 进 stdin、replies 出 stdout（不经 shell）")
    lpa.add_argument("--ask", action="store_true",
                     help="用内置 Anthropic 通道（ANTHROPIC_API_KEY / LOMENT_AI_KEY）")
    lpa.add_argument("--model", default=None, metavar="NAME",
                     help="模型名（也会记进请求与回答的 model 字段）")
    lpa.add_argument("--replies-out", default=None, metavar="PATH",
                     help="把（外部/内置）回答写到这里，便于进版本库")
    lpa.add_argument("--timeout", type=float, default=120.0, metavar="SECONDS",
                     help="模型通道的超时（默认 120s）")
    lpa.add_argument("--json", action="store_true")
    lpa.add_argument("--strict", action="store_true",
                     help="有 suspicious 时退出码 1")
    lpa.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4))
    lpa.add_argument("--lspe-mode", default="full", choices=("full", "quiet", "strict"))
    lpa.add_argument("--answer", action="append", metavar="SITE=B")
    lpa.add_argument("--answers", default=None, metavar="FILE")
    lpa.set_defaults(fn=cmd_lspe_aicheck)
    lpl = lsub.add_parser("loans")
    lpl.add_argument("file")
    lpl.add_argument("--json", action="store_true")
    lpl.add_argument("--strict", action="store_true",
                     help="有冲突/悬垂时退出码 1（报告面，不影响编译）")
    lpl.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4))
    lpl.add_argument("--lspe-mode", default="full", choices=("full", "quiet", "strict"))
    lpl.add_argument("--answer", action="append", metavar="SITE=B")
    lpl.add_argument("--answers", default=None, metavar="FILE")
    lpl.set_defaults(fn=cmd_lspe_loans)
    lpe = lsub.add_parser("effects")
    lpe.add_argument("file")
    lpe.add_argument("--json", action="store_true")
    lpe.add_argument("--strict", action="store_true",
                     help="有硬发现时退出码 1（报告面，不影响编译）")
    lpe.set_defaults(fn=cmd_lspe_effects)
    lc = sub.add_parser("lspc")
    lcsub = lc.add_subparsers(dest="lspc_cmd", required=True)
    lcc = lcsub.add_parser("check")
    lcc.add_argument("file")
    lcc.add_argument("--json", action="store_true")
    lcc.add_argument("--lspc", default=None, choices=("full", "quiet", "strict", "off"))
    lcc.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4))
    lcc.add_argument("--lspe-mode", default="full", choices=("full", "quiet", "strict"))
    lcc.add_argument("--answer", action="append", metavar="SITE=B")
    lcc.add_argument("--answers", default=None, metavar="FILE")
    lcc.add_argument("--replies", default=None, metavar="PATH",
                     help="把 AI 检查器的回答（Q014）贴到预检单后面")
    lcc.add_argument("--previous", default=None, metavar="ARTIFACT",
                     help="上一轮的 potato.json（跨构建记忆，`#760`）")
    lcc.add_argument("--previous-sidecar", default=None, metavar="FILE",
                     help="上一轮的 .lspe 旁挂（判『过期假设』用）")
    lcc.set_defaults(fn=cmd_lspc_check)
    for name in ("build", "pkg", "lsp", "lib", "slop"):
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
        if a.delegate == "slop":
            import loment_slop
            return loment_slop.main(a.rest)
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
