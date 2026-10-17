#!/usr/bin/env python3
# lom_audit.py — 用 .lom 单源审计既有实现 (L0 落地路径, docs/141)
#
# 定位: lomc.py 是语言编译器 (通用); 本文件是项目审计 (FujoOS 特有)。
# 审计项:
#   [fuai/spec]    lom/fuai.lom  <->  sdk/fuai-spec/spec.json   双向逐字段
#   [fuai/fujo]    lom/fuai.lom  <->  kernel/src/syscall.rs     opcode + fujo_fn
#   [fuai/linux]   lom/fuai.lom  <->  LinuxFUAI/include/fuai.h  linux_fn
#   [fuai/param]   部署参数值    <->  实现源码中的数值声明 (陈旧注释检测)
#   [fuc/layout]   lom/fuc.lom   <->  tools/fuic.py 格式串 + kernel/src/fui/fuc.rs 常量
#
# 退出码: 0 = 0 差异, 1 = 有差异, 2 = 异常。

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    """跑一条 git，返回 stdout（**失败回空串**）。只有 M93/M95 的滞后容差用它 ——
    "自这些东西上次被写出以来动过哪些路径"问的就是 git。判不了（浅克隆 / 不是 git 树）
    时回空串，调用方按"判不了"处理而**不是**当成"没动过"（见那一处的注释）。
    """
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, errors="replace", shell=False)
    return r.stdout.strip() if r.returncode == 0 else ""
LOM_FUAI = ROOT / "lom" / "fuai.lom"
LOM_FUC = ROOT / "lom" / "fuc.lom"
LOM_FUJR = ROOT / "lom" / "fujr.lom"
SPEC = ROOT / "sdk" / "fuai-spec" / "spec.json"
SYS = ROOT / "kernel" / "src" / "syscall.rs"
FUAI_H = ROOT / "LinuxFUAI" / "include" / "fuai.h"
FUIC = ROOT / "tools" / "fuic.py"
FUC_RS = ROOT / "kernel" / "src" / "fui" / "fuc.rs"
FUJR_RS = ROOT / "kernel" / "src" / "fujr.rs"
FUJPACK = ROOT / "tools" / "fujopack.py"

#: `lom/net.lom`（号 → 面）的对账面 —— **四处**：编译器的计数词汇、链接器的派发面，
#: 以及自举侧两处**只认 net 那几个号**的派生用法（`docs/229` §2）。
LOM_NET = ROOT / "lom" / "net.lom"
NET_TWIN_NET_SETS = [
    (ROOT / "loment" / "selfhost" / "checker.lomt", "chk_num_is_net"),
    (ROOT / "loment" / "selfhost" / "potato.lomt", "pt_surf_code"),
]

# 生成物登记: (lom 源, 生成器, 磁盘路径) — 逐字节对账, 漂移即报错
GENERATED = [
    (LOM_FUC, "rust", ROOT / "lom" / "build" / "fuc.rs"),
    (LOM_FUC, "c", ROOT / "lom" / "build" / "fuc.h"),
    (LOM_FUC, "python", ROOT / "lom" / "build" / "fuc.py"),
    (LOM_FUC, "json", ROOT / "lom" / "build" / "fuc.json"),
    (LOM_FUC, "rust", ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs"),
    (LOM_FUAI, "rust", ROOT / "lom" / "build" / "fuai.rs"),
    (LOM_FUAI, "c", ROOT / "lom" / "build" / "fuai.h"),
    (LOM_FUAI, "python", ROOT / "lom" / "build" / "fuai.py"),
    (LOM_FUAI, "json", ROOT / "lom" / "build" / "fuai.json"),
    (LOM_FUJR, "rust", ROOT / "lom" / "build" / "fujr.rs"),
    (LOM_FUJR, "c", ROOT / "lom" / "build" / "fujr.h"),
    (LOM_FUJR, "python", ROOT / "lom" / "build" / "fujr.py"),
    (LOM_FUJR, "json", ROOT / "lom" / "build" / "fujr.json"),
    (LOM_NET, "json", ROOT / "lom" / "build" / "net.json"),
]

# 部署参数 -> 富士侧实现文件与别名模式 (项目特有知识, 故留在审计脚本而非语言里)
PARAM_BINDINGS = {
    "tau_high_default": {
        "fujo_files": [ROOT / "kernel" / "src" / "capability.rs"],
        "alias": r"(?:τ_high|tau_high|cfg7|cfg\s*7)",
    },
    "tau_low_default": {
        "fujo_files": [ROOT / "kernel" / "src" / "capability.rs"],
        "alias": r"(?:τ_low|tau_low|cfg8|cfg\s*8)",
    },
}

# 取整数时排除标识符内数字 (W46) 与小数 (0.45)
INT_RE = re.compile(r"(?<![A-Za-z_0-9.])(\d+)(?![0-9])")
DISPATCH_RE = re.compile(r"^\s*0x([0-9A-Fa-f]{4})\s*=>", re.MULTILINE)


def parse_dispatch(text: str) -> dict[int, str]:
    """syscall.rs: opcode -> 该分支的多行表达式文本。"""
    out: dict[int, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*0x([0-9A-Fa-f]{4})\s*=>", lines[i])
        if not m:
            i += 1
            continue
        op = int(m.group(1), 16)
        block = [lines[i][m.end():].strip()]
        j = i + 1
        while j < len(lines) and not re.match(r"^\s*0x[0-9A-Fa-f]{4}\s*=>", lines[j]):
            block.append(lines[j].strip())
            j += 1
        out[op] = " ".join(b for b in block if b)
        i = j
    return out


def audit_fuai(diffs: list[str]) -> tuple[int, int]:
    mod = lomc.load(LOM_FUAI)
    enum = mod.enums[0]
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    prims = {p["opcode"]: p for p in spec["primitives"]}
    lom_by_op = {v.value: v for v in enum.variants}

    # --- (a) .lom <-> spec.json 双向
    for v in enum.variants:
        p = prims.get(v.value)
        if p is None:
            diffs.append(f"[fuai/spec ] {v.name}: opcode 0x{v.value:04X} 在 .lom 但 spec.json 未登记")
            continue
        if p["name"] != v.name:
            diffs.append(f"[fuai/spec ] 0x{v.value:04X}: .lom 名 {v.name} != spec 名 {p['name']}")
        for key in ("layer", "sig", "fujo", "semantics"):
            want = p.get(key)
            got = v.meta.get(key)
            if (want or None) != (got or None):
                diffs.append(f"[fuai/spec ] {v.name}.{key}: .lom={got!r} spec={want!r}")
        if (p.get("linux") or None) != (v.meta.get("linux") or None):
            diffs.append(
                f"[fuai/spec ] {v.name}.linux: .lom={v.meta.get('linux')!r} spec={p.get('linux')!r}"
            )
    for op, p in prims.items():
        if op not in lom_by_op:
            diffs.append(f"[fuai/spec ] {p['name']}: spec 有 opcode 0x{op:04X} 但 .lom 缺登记")

    # --- (a2) 权威翻转 (docs/141 v1): spec.json 必须是 .lom 的生成物
    import lom_spec_emit as _lse

    want_spec = _lse.emit(mod)
    for t in _lse.TARGETS:
        if not _on_disk(t):
            continue
        if t.read_text(encoding="utf-8") != want_spec:
            diffs.append(f"[fuai/gen  ] {t.relative_to(ROOT)} 与 .lom 生成结果不一致 (重新生成)")

    # --- (b) .lom <-> syscall.rs dispatch
    text = SYS.read_text(encoding="utf-8")
    dispatch = parse_dispatch(text)
    assert dispatch, "syscall.rs dispatch 解析失败"
    for v in enum.variants:
        if v.value not in dispatch:
            diffs.append(f"[fuai/fujo ] {v.name}: opcode 0x{v.value:04X} 不在 dispatch")
            continue
        fn = v.meta.get("fujo", "")
        if fn and not re.search(rf"\b{re.escape(fn)}\s*\(", dispatch[v.value]):
            diffs.append(f"[fuai/fujo ] {v.name}: dispatch 分支不含 {fn}")

    # --- (c) .lom <-> fuai.h
    htxt = FUAI_H.read_text(encoding="utf-8")
    funcs = set(re.findall(r"\b(fuai_[a-z0-9_]+)\s*\(", htxt))
    for v in enum.variants:
        linux = v.meta.get("linux")
        if linux:
            if linux not in funcs:
                diffs.append(f"[fuai/linux] {v.name}: {linux} 不在 fuai.h")
        else:
            if v.meta.get("layer") == "core" and f"fuai_{v.name}" in funcs:
                diffs.append(f"[fuai/linux] {v.name}: .lom 未登记 linux 但 fuai.h 出现 fuai_{v.name}")

    # --- (d) 部署参数: 富士侧源码不得出现 Linux 侧取值 (陈旧注释检测)
    for p in mod.params:
        bind = PARAM_BINDINGS.get(p.name)
        if not bind:
            continue
        fujo_v, linux_v = p.values.get("fujo"), p.values.get("linux")
        if fujo_v is None or linux_v is None or fujo_v == linux_v:
            continue
        for f in bind["fujo_files"]:
            if not f.exists():
                continue
            for ln, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if not re.search(bind["alias"], line):
                    continue
                for m in INT_RE.finditer(line):
                    n = int(m.group(1))
                    if n == linux_v:
                        diffs.append(
                            f"[fuai/param] {f.name}:{ln}: 提到 {p.name}={n} 但富士侧应为 "
                            f"{fujo_v}（{n} 是 LinuxFUAI 部署值）— 陈旧数值"
                        )
    return len(enum.variants), len(prims)


def audit_fuc(diffs: list[str]) -> None:
    mod = lomc.load(LOM_FUC)
    node = next(r for r in mod.records if r.name == "Node")
    header = next(r for r in mod.records if r.name == "Header")
    py = lomc.emit_python(mod)

    def grab(name: str) -> str | None:
        m = re.search(rf"^{name} = (.+)$", py, re.MULTILINE)
        if not m:
            return None
        v = m.group(1).strip()
        if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
            v = v[1:-1]  # 去引号: 与源码里的字面串直接比对
        return v

    # tools/fuic.py 手写格式串
    fuic_txt = FUIC.read_text(encoding="utf-8")
    node_fmt = grab("NODE_FMT")
    header_fmt = grab("HEADER_FMT")
    if node_fmt and node_fmt not in fuic_txt:
        diffs.append(f"[fuc/layout] Node 格式串 {node_fmt} 未出现在 tools/fuic.py")
    if header_fmt and header_fmt not in fuic_txt:
        diffs.append(f"[fuc/layout] Header 格式串 {header_fmt} 未出现在 tools/fuic.py")

    # kernel/src/fui/fuc.rs 必须从生成模块取常量, 不能退回手写
    rs = FUC_RS.read_text(encoding="utf-8")
    if "pub use super::fuc_gen::*;" not in rs:
        diffs.append("[fuc/layout] fuc.rs 未 re-export fuc_gen (常量可能又变回手写)")
    # tools/fuic.py 的打包器必须引用生成模块, 不能退回手写 struct.pack
    if "spec_from_file_location" not in fuic_txt or "fuc.py" not in fuic_txt:
        diffs.append("[fuc/layout] fuic.py 未引用 lom/build/fuc.py (打包器可能退回手写)")
    if header.size != 48:
        diffs.append(f"[fuc/layout] Header.size={header.size} != 48")

    # kernel 侧生成物必须与 lomc 输出逐字节一致 (接管真源后, 漂移不可能)
    gen_rs = ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs"
    want_rs = lomc.emit_rust(mod)
    if not gen_rs.exists():
        diffs.append("[fuc/kernel] kernel/src/fui/fuc_gen.rs 缺失")
    elif gen_rs.read_text(encoding="utf-8") != want_rs:
        diffs.append("[fuc/kernel] fuc_gen.rs 与 .lom 生成结果不一致 (重新生成即可)")


def generated_wants() -> list[tuple[Path, str]]:
    """`GENERATED` 表里 `.lom` -> 各语言产物的 (路径, 应该是的字节)。"""
    cache: dict[Path, lomc.Module] = {}
    out = []
    for lom, kind, path in GENERATED:
        if lom not in cache:
            cache[lom] = lomc.load(lom)
        out.append((path, lomc.EMITTERS[kind](cache[lom])))
    return out


def _on_disk(path: Path) -> bool:
    """S3（`docs/189` §3.0）之后**产物不在索引里**：「文件不在」是**仓库的默认状态**
    （新克隆就是这样），**不是**缺陷 —— 审计里只剩"在盘上但与生成结果不一致"才算漂移。
    `lom_audit --emit` 能把这些产物一次补齐。

    这一句是**整片改动**的那个判断：以前"提交的副本 == 生成器此刻会生成的"，
    现在"**生成是确定的** + **漂移检得出来**"。
    """
    return path.exists()


def audit_generated(diffs: list[str]) -> int:
    """所有登记生成物必须与 .lom 生成结果逐字节一致。"""
    names = {path: lom.name for lom, _k, path in GENERATED}
    for path, want in generated_wants():
        if not _on_disk(path):
            continue
        if path.read_text(encoding="utf-8") != want:
            diffs.append(f"[gen        ] {path.relative_to(ROOT)} 与 {names[path]} 生成结果不一致")
    return len(GENERATED)


def _fn_body(text: str, name: str) -> str:
    """源码里那个顶层函数的正文（从 `fn NAME(` 到下一行的 `}`）。

    审计要看的是"**这个函数认哪些号**"，所以只取正文 —— 取整个文件会把别处的数字也算进来。
    """
    m = re.search(rf"(?m)^fn {name}\(.*$", text)
    if not m:
        return ""
    end = text.find("\n}", m.end())
    return text[m.start():end if end > 0 else len(text)]


def audit_net(diffs: list[str]) -> None:
    """`lom/net.lom` 是"号 → 面"的**单源**（`docs/229` §2）。

    四处实现/用法各自抄过一份，这里按**两个方向**对账：多一个号、少一个号、面不同，
    三种都算差异（"方向反了也一样" —— 早期那些对账只查一个方向，于是"实现多了个号"
    一直没人报）。
    """
    import lomentc  # noqa: PLC0415  (审计读实现, 与上面 `import potato` 同一个形状)
    import lomelf

    mod = lomc.load(LOM_NET)
    sc = next(e for e in mod.enums if e.name == "Syscall")
    # 联网面补完后（`docs/234`）这一族整个不在契约里了 —— 缺席 = 空集
    nd = next((e for e in mod.enums if e.name == "NotDispatched"), None)
    nd_variants = nd.variants if nd is not None else []
    want = {v.value: v.meta.get("surface") for v in sc.variants}

    # (a) 编译器那一份 —— 计数词汇（`surfaces` 按面分类用的就是它）
    got_c = dict(lomentc.SURFACE_NUMBERS)
    for n in sorted(set(want) | set(got_c)):
        if want.get(n) != got_c.get(n):
            diffs.append(f"[net/lomentc] 号 {n}: .lom={want.get(n)!r} lomentc={got_c.get(n)!r}")

    # (b) 链接器那一份 —— PE 真正派发哪些号 + 各属哪一面
    pe = {n: s for n, s, _label in lomelf.PE_SYSCALLS}
    for n in sorted(set(want) | set(pe)):
        if want.get(n) != pe.get(n):
            diffs.append(f"[net/lomelf ] 号 {n}: .lom={want.get(n)!r} PE_SYSCALLS={pe.get(n)!r}")

    # (c) "点名不做"那一族必须**不在**派发面上 —— 哪天谁顺手实现了其中一个，这条红
    for v in nd_variants:
        if v.value in pe:
            diffs.append(f"[net/lomelf ] {v.name}({v.value}) 在 NotDispatched 里, 却被 "
                         f"PE_SYSCALLS 派发了 —— 两条路选一条: 实现它并挪进 Syscall, "
                         f"或者把这条从 NotDispatched 删掉")

    # (d) 自举侧那两处派生用法。它们**形状不同**，所以分开对：
    #     `chk_num_is_net` 只认 net 那一族；`pt_surf_code` 是**整张表**（用代号 1..4
    #     表示 file/mem/net/proc）—— 后者按**分块**比，免得把那四个代号抄进审计里。
    path, fn = NET_TWIN_NET_SETS[0]
    body = _fn_body(path.read_text(encoding="utf-8"), fn)
    if not body:
        diffs.append(f"[net/twin   ] {path.name} 里找不到 {fn}()")
    else:
        net_want = sorted(n for n, s in want.items() if s == "net")
        got = sorted(int(x) for x in re.findall(r"\b(?:n|num)\s*==\s*(\d+)", body))
        if got != net_want:
            diffs.append(f"[net/twin   ] {path.name} 的 {fn}(): {got} != .lom 的 net 表 {net_want}")

    path, fn = NET_TWIN_NET_SETS[1]
    body = _fn_body(path.read_text(encoding="utf-8"), fn)
    if not body:
        diffs.append(f"[net/twin   ] {path.name} 里找不到 {fn}()")
    else:
        by_code: dict[int, list[int]] = {}
        for m in re.finditer(r"if ([^{]+?)\{\s*return (\d+);", body):
            for num in re.findall(r"\bnum\s*==\s*(\d+)", m.group(1)):
                by_code.setdefault(int(m.group(2)), []).append(int(num))
        by_surface: dict[str, list[int]] = {}
        for n, s in want.items():
            by_surface.setdefault(s, []).append(n)
        got_groups = sorted(sorted(v) for v in by_code.values())
        want_groups = sorted(sorted(v) for v in by_surface.values())
        if got_groups != want_groups:
            diffs.append(f"[net/twin   ] {path.name} 的 {fn}(): 分块 {got_groups} "
                         f"!= .lom 的面 {want_groups}")


def audit_fujr(diffs: list[str]) -> None:
    mod = lomc.load(LOM_FUJR)
    hdr = next(r for r in mod.records if r.name == "Header")
    sec = next(r for r in mod.records if r.name == "Section")
    if (hdr.size, sec.size) != (64, 32):
        diffs.append(f"[fujr       ] Header/Section = {hdr.size}/{sec.size}, 期望 64/32")
    # 内核侧仍认 FUJR 魔数
    rs = FUJR_RS.read_text(encoding="utf-8")
    if "FUJR" not in rs:
        diffs.append("[fujr       ] kernel/src/fujr.rs 未见 FUJR 魔数")
    # 宿主打包器必须引用生成模块
    pk = FUJPACK.read_text(encoding="utf-8")
    if "spec_from_file_location" not in pk or "fujr.py" not in pk:
        diffs.append("[fujr       ] fujopack.py 未引用 lom/build/fujr.py")


# --------------------------------------------------------------------------- 生成物清单
#
# 这一族是"**有判据、也必须能重新生成**"的产物 (docs/158 §5 第 3 条要的那一步)。
# 审计与 `--emit` **共用同一次计算** —— 另外写一份"产物该长什么样"就是第二份真相,
# 两份必然漂, 而漂法是静默的 (判据绿、产物错)。
#
# 为什么这个入口现在才补: 之前的流程是"到旧树跑一遍生成、把文件拷过来", 搬到开发口
# 之后旧树不在了, 才发现有几件产物**只有判据、没有生成器** (自举那 4 个
# `selfhost_*.potato.json` 就是) —— 2026-09-17 要给形式对象升 v2 时撞上的。

def transpile_wants() -> list[tuple[Path, str]]:
    """`loment/examples/` 的 .rs / .ll 产物。"""
    import lomentc
    ex = ROOT / "loment" / "examples"
    b = ROOT / "loment" / "build"
    src = ex / "demo.lomt"
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    out = [(b / "demo.rs", lomentc.emit_rust(mod, ROOT, deps))]
    for n in ("native", "native_agg", "native_str", "native_slice", "native_mut",
              "native_gen", "native_trait", "native_res", "native_mem", "native_bits",
              "native_entry"):
        out.append((b / f"{n}.ll", lomentc.emit_llvm(lomentc.load(ex / f"{n}.lomt"), ROOT)))
    return out


def example_potato_wants() -> list[tuple[Path, str]]:
    """M45/M46: 每个示例一份形式对象 (编译器的强制导出面)。"""
    import lomentc
    ex = ROOT / "loment" / "examples"
    b = ROOT / "loment" / "build"
    out = []
    for s in sorted(ex.glob("*.lomt")):
        m = lomentc.load(s)
        out.append((b / f"{s.stem}.potato.json",
                    lomentc.emit_potato(m, ROOT,
                                        lomentc.resolve_deps(m, ROOT, s.parent, entry=s))))
    return out


def selfhost_potato_wants() -> list[tuple[Path, str]]:
    """M79/M80/M81/M82: 自举各模块的形式对象。"""
    import lomentc
    b = ROOT / "loment" / "build"
    out = []
    for stem in ("lexer", "parser", "checker", "codegen"):
        sh = ROOT / "loment" / "selfhost" / f"{stem}.lomt"
        if not sh.exists():
            continue
        m = lomentc.load(sh)
        out.append((b / f"selfhost_{stem}.potato.json",
                    lomentc.emit_potato(m, ROOT,
                                        lomentc.resolve_deps(m, ROOT, sh.parent, entry=sh))))
    return out


def cap_asserts_want() -> tuple[Path, str]:
    """M50: A1–A4 断言表。"""
    import potato_assert
    return (ROOT / "loment" / "build" / "cap_asserts.rs",
            potato_assert.emit_rust(potato_assert.load_objects()))


def syscalls_want() -> tuple[Path, str]:
    """M72: 系统调用层由 `lom/fuai.lom` 单源生成。"""
    import loment_syscalls
    return (ROOT / "loment" / "build" / "fuai_syscalls.lomt", loment_syscalls.emit())


def audit_l1(diffs: list[str]) -> None:
    """L1 产物必须与 .lomt 转译结果一致 (docs/143)。"""
    for path, want in transpile_wants():
        if not _on_disk(path):
            continue
        if path.read_text(encoding="utf-8") != want:
            diffs.append(f"[l1         ] {path.relative_to(ROOT)} 与 .lomt 转译结果不一致")

    # M45/M46: 每个示例必须有一份通过独立校验器的形式对象 (缺失/过期 = 门禁失败)
    import potato
    for obj, want in example_potato_wants():
        if not obj.exists():
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 缺失 (M46 强制导出)")
            continue
        got = obj.read_text(encoding="utf-8")
        if got != want:
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 与 .lomt 形式对象不一致")
            continue
        errs = potato.validate(json.loads(got))
        if errs:
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 形式对象非法: {errs[0]}")

    # M50: A1–A4 断言表必须与形式对象一致
    cap, want_cap = cap_asserts_want()
    if _on_disk(cap) and cap.read_text(encoding="utf-8") != want_cap:
        diffs.append(f"[l1         ] {cap.relative_to(ROOT)} 与形式对象不一致 (M50)")

    # M72: 系统调用层必须由 lom/fuai.lom 单源生成
    syscalls, want_sys = syscalls_want()
    if _on_disk(syscalls) and syscalls.read_text(encoding="utf-8") != want_sys:
        diffs.append(f"[l1         ] {syscalls.relative_to(ROOT)} 与 lom/fuai.lom 不一致 (M72)")

    # M79/M80/M81/M82: 自举 lexer/parser/checker/codegen 的形式对象必须与源码一致
    for sh_obj, want_sh in selfhost_potato_wants():
        if not sh_obj.exists():
            diffs.append(f"[l1         ] {sh_obj.relative_to(ROOT)} 缺失 (自举形式对象)")
        elif sh_obj.read_text(encoding="utf-8") != want_sh:
            diffs.append(f"[l1         ] {sh_obj.relative_to(ROOT)} 与 selfhost 源码不一致")

    # M93/M95: 手册站点与发布清单必须与当前工件一致 —— **按滞后容差判** (2026-10-16)。
    #
    # 这两条原来是"逐字节等于**当前树**"。那时全树生成物由**每个 PR** 提交，所以那句话在
    # 每个 PR 上都为真；代价是那两个文件是**全树台账**（每一行都是别人的文件），两个并发 PR
    # 写相邻行 ⇒ **必冲突**（实测见 `.github/workflows/manifest.yml` 文件头那节）。
    #
    # 现在全树生成物由 `main` 上的单一写者刷新（同一个 workflow 的 `emit-main`），
    # 于是**在一棵还没被刷新的树上，落后是预期的** —— 拿它判红就是拿排队现象当缺陷。
    #
    # 所以改成：对**自这些东西上次被写出以来没动过的路径**要求相等。
    #   * 动过 = 还没轮到写者刷新 ⇒ 容忍（那是设计，不是漂）
    #   * 没动 = 它必须对得上 ⇒ 抓**真正错的**台账（手工改过的、生成器写错的）
    # 这比原来那条**更强**：原写法在"仓库里的和生成的一起过期"时照样绿
    # （`docs/162` 记过那个形状：四次都是这么漏掉的）。
    #
    # **判不了就不判"绿"，判"红"**：浅克隆（`actions/checkout` 默认 `fetch-depth: 1`）
    # 里 `git log` 只看得到 HEAD，"自上次写出以来动过什么"问不出来 —— 那时退回严格比较
    # 会把每个 PR 都判红，而**静默放行**会让这条判据在 CI 上形同不存在。两种都不行，
    # 所以这里直接报一条能照着修的账（门禁的分片 checkout 要 `fetch-depth: 0`）。
    def _changed_since(rel: str) -> set[str] | None:
        """自 `rel` 上次被写出以来动过的路径；拿不到历史就 None。"""
        if _git("rev-parse", "--is-shallow-repository") == "true":
            return None
        last = _git("log", "-1", "--format=%H", "--", rel)
        if not last:
            return None
        # **与工作树比，不与 `HEAD` 比**（`git diff <sha>` 省掉第二个参数就是这个意思）：
        # `last..HEAD` 看不见**未提交**的改动，于是本机带着改动跑一次，就会把刚改的那几个
        # 文件报成"台账对不上"（实测：6 处）。CI 上树是干净的，两种写法等价。
        out = _git("diff", "--name-only", last)
        return {ln.strip() for ln in out.splitlines() if ln.strip()}

    man_stale = _changed_since("docs/manual")
    rel_stale = _changed_since("loment/build/release-manifest.json")

    import loment_manual
    import loment_release
    if man_stale is None or rel_stale is None:
        diffs.append("[l1         ] M93/M95 判不了：拿不到 git 历史"
                     "（浅克隆？门禁的分片 checkout 要 fetch-depth: 0）")
    else:
        for rel, text in loment_manual.build().items():
            p = ROOT / "docs" / "manual" / rel
            if f"docs/manual/{rel}" in man_stale:
                continue
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                diffs.append(f"[l1         ] docs/manual/{rel} 与生成的页面不一致 (M93)")
                break
        man = ROOT / "loment" / "build" / "release-manifest.json"
        want_rel = {x["path"]: x["sha256"] for x in loment_release.build()["files"]}
        if not man.exists():
            diffs.append(f"[l1         ] {man.relative_to(ROOT)} 缺失 (M95)")
        else:
            got_rel = {x["path"]: x["sha256"]
                       for x in json.loads(man.read_text(encoding="utf-8")).get("files", [])}
            bad = [k for k in want_rel if got_rel.get(k) != want_rel[k] and k not in rel_stale]
            extra = [k for k in got_rel if k not in want_rel and k not in rel_stale]
            if bad or extra:
                diffs.append(f"[l1         ] release-manifest.json 与工件不一致 (M95): "
                             f"{(bad + extra)[:2]}")


def emit() -> int:
    """把上面那一族的生成物**写出来** (docs/158 §5 第 3 条要的那一步)。

    `.lom` -> 各语言那一族 (`GENERATED` + `lomc.EMITTERS`) 也一起写 —— 它们与 L1 那一族
    是同一类东西 (有判据的生成物), `--emit` 只覆盖一半就是个陷阱。

    **`newline="\\n"` 不能省**: 与 `loment_build.py` 同一处坑 —— 默认的平台转换会写出
    CRLF, 而 `.gitattributes` 定的是 `eol=lf`, 于是"重生成 == 仓库里的"这条判据
    在 Windows 上必然假红。

    **注意它会写 `kernel/src/fui/fuc_gen.rs`** —— 那一份虽然落在内核目录里, 但它是
    **这份仓的生成物** (由 `lom/fuc.lom` 生成, 内核只是消费方), 所以该跟着单源重生成。
    `CLAUDE.md` 那条"kernel/ 是只读副本"管的是**他们的手写源码** (`syscall.rs` 之类),
    不覆盖这一份。
    """
    items: list[tuple[Path, str]] = []
    items += generated_wants()
    items += transpile_wants()
    items += example_potato_wants()
    items += selfhost_potato_wants()
    items.append(cap_asserts_want())
    items.append(syscalls_want())
    for path, text in items:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"[wrote] {path.relative_to(ROOT)}")
    print(f"lom_audit --emit: {len(items)} 个生成物")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--emit":
        return emit()
    diffs: list[str] = []
    try:
        n_prim, n_spec = audit_fuai(diffs)
        audit_fuc(diffs)
        audit_fujr(diffs)
        audit_net(diffs)
        n_gen = audit_generated(diffs)
        audit_l1(diffs)
    except lomc.LomError as e:
        print(f"[ERR] .lom 解析失败: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] 审计异常: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print("LOM AUDIT v0  (.lom 单源 -> 实现)")
    print(f"  fuai: {n_prim} 原语 (spec.json 登记 {n_spec})")
    print("  fuc : Header 48B / Node 64B")
    print("  fujr: Header 64B / Section 32B")
    print(f"  gen : {n_gen} 个生成物逐字节对账")
    if diffs:
        print(f"\n[DIFF] {len(diffs)} 项:")
        for d in diffs:
            print("  " + d)
        return 1
    print("\n[OK] 0 差异 — 实现与 .lom 单源一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
