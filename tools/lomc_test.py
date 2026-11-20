#!/usr/bin/env python3
# lomc_test.py — L0 编译器自检 (docs/141 §6 验收)
#
# 覆盖: 正例解析 / 六类语义错误拦截 / 生成确定性 / --check 漂移检出 /
#       生成物与既有实现逐字面一致 (fuc 布局的真值锚定)。
#
# 运行: python tools/lomc_test.py    (退出码 0 = 全绿)

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOM_FUC = ROOT / "lom" / "fuc.lom"
LOM_FUAI = ROOT / "lom" / "fuai.lom"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def parse_text(src: str) -> lomc.Module:
    return lomc.Parser(lomc.lex(src), src).parse()


def errs_of(src: str) -> list[str]:
    return lomc.check(parse_text(src))


# ---------------------------------------------------------------- 正例

@test
def test_real_schemas_parse_clean():
    for p in (LOM_FUC, LOM_FUAI):
        mod = lomc.load(p)
        assert lomc.check(mod) == [], f"{p.name} 语义错误: {lomc.check(mod)}"


@test
def test_fuai_covers_all_primitives():
    mod = lomc.load(LOM_FUAI)
    assert len(mod.enums) == 1 and mod.enums[0].name == "Opcode"
    assert len(mod.enums[0].variants) == 46, len(mod.enums[0].variants)
    assert len(mod.params) == 2


@test
def test_fuc_layout_is_64_and_48():
    mod = lomc.load(LOM_FUC)
    node = next(r for r in mod.records if r.name == "Node")
    header = next(r for r in mod.records if r.name == "Header")
    assert node.size == 64 and header.size == 48
    # 无空洞: 字段连续铺满 64B
    cur = 0
    for f in sorted(node.fields, key=lambda x: x.offset):
        assert f.offset == cur, f"{f.name} 期望 @{cur} 实得 @{f.offset}"
        cur += lomc.TYPES[f.type][1]
    assert cur == 64, cur


# ---------------------------------------------------------------- 负例: 语义检查必须拦截

@test
def test_duplicate_enum_value_rejected():
    e = errs_of('module m\nenum E : u16 { a = 1 b = 1 }\n')
    assert any("同值" in x for x in e), e


@test
def test_enum_value_out_of_range_rejected():
    e = errs_of('module m\nenum E : u8 { a = 256 }\n')
    assert any("超出" in x for x in e), e


@test
def test_overlapping_fields_rejected():
    e = errs_of('module m\nrecord R layout(packed, size=8) { a : u32 @0 b : u32 @2 }\n')
    assert any("重叠" in x for x in e), e


@test
def test_field_overrun_rejected():
    e = errs_of('module m\nrecord R layout(packed, size=4) { a : u32 @2 }\n')
    assert any("越过" in x for x in e), e


@test
def test_duplicate_top_level_name_rejected():
    e = errs_of('module m\nconst X : u16 = 1\nrecord X layout(packed, size=4) { a : u32 @0 }\n')
    assert any("重复" in x for x in e), e


@test
def test_unknown_type_rejected_at_parse():
    try:
        parse_text('module m\nrecord R layout(packed, size=4) { a : u24 @0 }\n')
    except lomc.LomError as ex:
        assert "未知字段类型" in ex.msg, ex.msg
    else:
        raise AssertionError("u24 应被拒绝")


@test
def test_missing_module_rejected():
    try:
        parse_text('enum E : u16 { a = 1 }\n')
    except lomc.LomError as ex:
        assert "module" in ex.msg, ex.msg
    else:
        raise AssertionError("缺 module 应被拒绝")


# ---------------------------------------------------------------- 生成确定性 + 真值锚定

@test
def test_generation_is_deterministic():
    mod = lomc.load(LOM_FUAI)
    for kind, fn in lomc.EMITTERS.items():
        assert fn(mod) == fn(mod), f"{kind} 生成不确定"


@test
def test_generated_fuc_fmt_matches_fuic():
    """生成格式串必须与 tools/fuic.py 手写字面串逐字符一致 (真值锚定)。"""
    py = lomc.emit_python(lomc.load(LOM_FUC))
    fuic = (ROOT / "tools" / "fuic.py").read_text(encoding="utf-8")
    for name in ("NODE_FMT", "HEADER_FMT"):
        m = re.search(rf"^{name} = '(.+)'$", py, re.MULTILINE)
        assert m, f"{name} 未生成"
        assert f'"{m.group(1)}"' in fuic, f"{name} {m.group(1)!r} 不在 fuic.py"


@test
def test_generated_fuc_consts_match_kernel():
    """生成常量必须与内核采用的常量一致 (fuc.rs 已改为 re-export fuc_gen)。"""
    mod = lomc.load(LOM_FUC)
    gen = (ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs").read_text(encoding="utf-8")
    fuc_rs = (ROOT / "kernel" / "src" / "fui" / "fuc.rs").read_text(encoding="utf-8")
    want = {
        "MAGIC": next(c.value for c in mod.consts if c.name == "MAGIC"),
        "VERSION": next(c.value for c in mod.consts if c.name == "VERSION"),
        "NODE_SIZE": next(r.size for r in mod.records if r.name == "Node"),
    }
    for name, val in want.items():
        m = re.search(rf"pub const {name}: \w+ = ([0-9x_A-Fa-f]+)", gen)
        assert m, f"fuc_gen.rs 缺 {name}"
        got = int(m.group(1).replace("_", ""), 0)
        assert got == val, f"{name}: fuc_gen.rs={got} .lom={val}"
    assert "pub use super::fuc_gen::*;" in fuc_rs, "fuc.rs 未 re-export fuc_gen"


@test
def test_check_detects_drift_and_tolerates_missing():
    """`--check` 的两半：**漂移要检出**，而**产物不在**不算漂移。

    这条原先钉的是旧不变式（"提交的副本 == 生成器此刻会生成的"），最后一句断言
    **缺失 ⇒ 退 1**。`docs/189` §3.0 的 S3 决定把交付物**移出索引**之后，「文件不在」
    是**仓库的默认状态**（新克隆就是这样）—— **对默认状态报错是错的**，所以那一句
    的期望跟着变（改成 0）。不变式的新说法是：**生成是确定的 + 漂移检得出来**。
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "gen.rs"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out)]) == 0
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 0
            out.write_text("// corrupted\n", encoding="utf-8")
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 1
            out.unlink()
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 0


@test
def test_semantic_error_exit_code():
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.lom"
        bad.write_text("module m\nenum E : u8 { a = 1 b = 1 }\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomc.main([str(bad), "--print", "json"]) == 1


@test
def test_meta_parses_and_nests():
    src = 'module m\nmeta {\n  title = "t"\n  layers {\n    core = "c"\n    "host-ext" = "h"\n  }\n}\nconst X : u8 = 1\n'
    mod = parse_text(src)
    assert mod.meta is not None
    assert mod.meta.entries["title"] == "t"
    assert mod.meta.entries["layers"]["host-ext"] == "h"


@test
def test_meta_duplicate_key_rejected():
    try:
        parse_text('module m\nmeta { a = "1" a = "2" }\nconst X : u8 = 1\n')
    except lomc.LomError as ex:
        assert "重复" in ex.msg, ex.msg
    else:
        raise AssertionError("meta 重复键应被拒绝")


@test
def test_param_note_parsed():
    mod = lomc.load(LOM_FUAI)
    notes = {p.name: p.note for p in mod.params}
    assert notes["tau_low_default"] == "一致"
    assert "非接口差异" in notes["tau_high_default"]


@test
def test_spec_json_is_generated():
    """权威翻转: spec.json 必须等于 lom/fuai.lom 的生成结果 (docs/141 v1)。"""
    import lom_spec_emit as lse

    want = lse.emit(lomc.load(LOM_FUAI))
    for t in lse.TARGETS:
        assert t.exists(), f"{t} 缺失"
        assert t.read_text(encoding="utf-8") == want, f"{t} 与生成结果不一致"


@test
def test_fujr_schema_layout():
    mod = lomc.load(ROOT / "lom" / "fujr.lom")
    hdr = next(r for r in mod.records if r.name == "Header")
    sec = next(r for r in mod.records if r.name == "Section")
    assert (hdr.size, sec.size) == (64, 32)
    assert lomc._py_struct_format(hdr) == "<III52x"
    assert lomc._py_struct_format(sec) == "<I4xQQI4x"


@test
def test_fujr_byte_roundtrip():
    """用生成结构重打包既有 .run, 必须与原文件逐字节一致。"""
    import importlib.util as ilu

    fujr_py = ROOT / "lom" / "build" / "fujr.py"
    # S3 之后**产物不在索引里**（docs/189 §3.0）：缺了就现场生成一份再用。
    lomc.ensure_python(ROOT / "lom" / "fujr.lom", fujr_py)
    spec = ilu.spec_from_file_location("t_fujr", fujr_py)
    F = ilu.module_from_spec(spec)
    spec.loader.exec_module(F)
    src = ROOT / "sdk" / "build" / "m31_res.run"
    assert src.exists(), "缺少 m31_res.run 基准"
    b = src.read_bytes()
    _magic, ver, count = F.HEADER_STRUCT.unpack_from(b, 0)
    secs = [F.SECTION_STRUCT.unpack_from(b, F.HEADER_SIZE + i * F.SECTION_SIZE) for i in range(count)]
    out = bytearray(F.HEADER_STRUCT.pack(F.MAGIC, F.VERSION, count))
    off = F.HEADER_SIZE + F.SECTION_SIZE * count
    payload = bytearray()
    for tag, o, sz, fv in secs:
        al = (off + F.SECTION_ALIGN - 1) & ~(F.SECTION_ALIGN - 1)
        payload += b"\x00" * (al - off)
        off = al
        payload += b[o:o + sz]
        off += sz
        out += F.SECTION_STRUCT.pack(tag, al, sz, fv)
    out += payload
    assert bytes(out) == b, f"重打包不一致 ({len(out)} / {len(b)} B)"


@test
def test_fujopack_uses_generated():
    src = (ROOT / "tools" / "fujopack.py").read_text(encoding="utf-8")
    assert "spec_from_file_location" in src and "fujr.py" in src
    assert '"FUJR"' not in src, "打包器仍写死 FUJR 魔数"


# ---------------------------------------------------------------- Loment 版（S1 第九格）

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-{os.getpid()}-"
#: 被测的是**另一件 Loment 程序**（L0 编译器的 Loment 版）+ 一个驱它的检查器。
TWIN_LOMC = ROOT / "loment" / "tools" / "lomc.lomt"
TWIN_CHK = ROOT / "loment" / "tools" / "lomccheck.lomt"

#: 判据写出来、孪生按**相对字面路径**读的那些输入（文件名是两个实现的接口）。
_BAD_INPUTS = {
    "bad_dup_enum.lom": "module m\nenum E : u8 { a = 1 b = 1 }\n",
    "bad_oor.lom": "module m\nenum E : u8 { a = 256 }\n",
    "bad_overlap.lom": "module m\nrecord R layout(packed, size=8) { a : u32 @0 b : u32 @2 }\n",
    "bad_overrun.lom": "module m\nrecord R layout(packed, size=4) { a : u32 @2 }\n",
    "bad_dupname.lom": ("module m\nconst X : u16 = 1\n"
                        "record X layout(packed, size=4) { a : u32 @0 }\n"),
    "bad_unknown.lom": "module m\nrecord R layout(packed, size=4) { a : u24 @0 }\n",
    "bad_nomodule.lom": "enum E : u16 { a = 1 }\n",
    "bad_metadup.lom": 'module m\nmeta { a = "1" a = "2" }\nconst X : u8 = 1\n',
    "meta.lom": ('module m\nmeta {\n  title = "t"\n  layers {\n    core = "c"\n'
                 '    "host-ext" = "h"\n  }\n}\nconst X : u8 = 1\n'),
}
_GOOD = ("fuai.lom", "fuc.lom", "fujr.lom")

#: L0 标量宽度（与孪生那张表同一份）。
_TW = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8}


def _py_json(args: list[str]) -> tuple[int, str, str]:
    """进程内跑 Python 版 lomc, 把 (rc, stdout, stderr) 都收回来。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = lomc.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _py_report() -> str:
    """用 **Python 的 lomc** 跑与孪生**同名的那 14 项**检查, 打同一份报告。

    孪生只能走 `lomc.lomt` 的命令面（`--print json` + 退出码 + 诊断走 stderr），所以
    这一侧也按"看产物"的规则算 —— 不复用上面那些进程内摸对象的检查: 两边必须是同一条
    判据, 否则比的是两种不同的断言。
    """
    out: list[str] = []
    ct = {"p": 0, "f": 0}

    def rep(name: str, ok: bool, why: str) -> None:
        if ok:
            out.append(f"  PASS  {name}")
            ct["p"] += 1
        else:
            out.append(f"  FAIL  {name}: {why}")
            ct["f"] += 1

    def art(rel: str):
        """跑一次 `lomc <rel> --print json`, 返回 (rc, stdout, stderr)。"""
        return _py_json([str(_WORK / rel), "--print", "json"])

    def doc(rel: str):
        rc, so, _se = art(rel)
        return rc, (json.loads(so) if rc == 0 and so.strip() else None)

    rc1, d1 = doc("fuai.lom")
    rc2, d2 = doc("fuc.lom")
    rep("test_real_schemas_parse_clean",
        d1 is not None and d1.get("module") == "fuai" and d2 is not None
        and d2.get("module") == "fuc", "fuai / fuc 没干净解析")

    rep("test_fuai_covers_all_primitives",
        d1 is not None and len(d1["enums"]) == 1 and d1["enums"][0]["name"] == "Opcode"
        and len(d1["enums"][0]["variants"]) == 46 and len(d1["params"]) == 2,
        "Opcode 变体数或参数数不对")

    ok4 = False
    if d2 is not None:
        recs = {r["name"]: r for r in d2["records"]}
        if "Header" in recs and "Node" in recs:
            node, cur = recs["Node"], 0
            contig = True
            for f in sorted(node["fields"], key=lambda x: x["offset"]):
                if f["offset"] != cur or f["type"] not in _TW:
                    contig = False
                    break
                cur += _TW[f["type"]]
            ok4 = (recs["Header"]["size"] == 48 and node["size"] == 64
                   and contig and cur == 64)
    rep("test_fuc_layout_is_64_and_48", ok4, "Header/Node 大小或字段有空洞")

    def rejected(rel: str, *keywords: str) -> bool:
        rc, _so, se = art(rel)
        return rc != 0 and bool(se.strip()) and any(k in se for k in keywords)

    # 重复枚举值: 参考说"同值", Loment 版说"重复" —— 两边都算这一类（措辞不算判据）
    rep("test_duplicate_enum_value_rejected", rejected("bad_dup_enum.lom", "重复", "同值"),
        "重复枚举值没被拒（或没说明）")
    rep("test_enum_value_out_of_range_rejected", rejected("bad_oor.lom", "超出"),
        "越界枚举值没被拒")
    rep("test_overlapping_fields_rejected", rejected("bad_overlap.lom", "重叠"),
        "字段重叠没被拒")
    rep("test_field_overrun_rejected", rejected("bad_overrun.lom", "越过"),
        "字段越过 size 没被拒")
    rep("test_duplicate_top_level_name_rejected", rejected("bad_dupname.lom", "重复"),
        "顶层名字重复没被拒")
    rep("test_unknown_type_rejected_at_parse", rejected("bad_unknown.lom", "未知字段类型"),
        "u24 没被拒")
    rep("test_missing_module_rejected", rejected("bad_nomodule.lom", "module"),
        "缺 module 没被拒")

    rc5, d5 = doc("meta.lom")
    rep("test_meta_parses_and_nests",
        d5 is not None and d5.get("meta", {}).get("title") == "t"
        and d5["meta"]["layers"].get("host-ext") == "h", "meta 没解析出来或没嵌层")

    rep("test_meta_duplicate_key_rejected", rejected("bad_metadup.lom", "重复"),
        "meta 重复键没被拒")

    ok6 = False
    if d1 is not None:
        notes = {p["name"]: p["note"] for p in d1["params"]}
        ok6 = (notes.get("tau_low_default") == "一致"
               and "非接口差异" in notes.get("tau_high_default", ""))
    rep("test_param_note_parsed", ok6, "两个部署参数的 note 不对")

    rc7, d7 = doc("fujr.lom")
    ok7 = False
    if d7 is not None:
        sizes = {r["name"]: r["size"] for r in d7["records"]}
        ok7 = sizes.get("Header") == 64 and sizes.get("Section") == 32
    rep("test_fujr_schema_layout", ok7, "fujr 的 Header/Section 大小不对")

    out.append(f"lomccheck: {ct['p']}/{ct['p'] + ct['f']} 通过")
    return "\n".join(out) + "\n"


def _clang() -> str | None:
    import shutil
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    import shutil
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(src: Path, td: Path, name: str) -> Path:
    import lomentc
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / f"{name}.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / f"{name}.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


@test
def test_lomc_check_matches_loment_twin():
    """**Loment 版**（`lomccheck.lomt` 驱 `lomc.lomt`）与 Python 版**同名检查的报告逐字节相同**。

    `docs/189` §3 的 S1 第九格。被测的是**另一件 Loment 程序**（L0 编译器的 Loment 版），
    所以这一格同时是"两个 L0 编译器在这些性质上给出同一个答案"的判据 —— 它**当场抓到**了
    一处真缺陷: `lomc.lomt` 原先**放行**重复的 meta 键（参考实现在 `parse_meta_body` 里拒），
    已在同一笔改动里补上（`meta 键重复`），并顺手补了顶层 `meta 块重复`。
    """
    global _WORK
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    import shutil
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 工作目录: 孪生按**相对字面路径**跑（proc_sh 只吃编译期字面量命令）
        _WORK = td / "work"
        _WORK.mkdir()
        for name in _GOOD:
            shutil.copy(ROOT / "lom" / name, _WORK / name)
        for name, text in _BAD_INPUTS.items():
            with (_WORK / name).open("w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        want = _py_report()
        lomc_elf = _build(TWIN_LOMC, td, "lomc_twin")
        chk_elf = _build(TWIN_CHK, td, "lomccheck")
        shutil.copy(lomc_elf, _WORK / "lomc")
        (_WORK / "lomc").chmod(0o755)
        outp = td / "check.out"
        binn = f"{_T}lomccheck.bin"
        script = (f"cp {_wsl_path(chk_elf)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(_WORK)} && {binn} > {_wsl_path(outp)} 2>&1; echo -n $?")
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                           capture_output=True, text=True, timeout=300, shell=False)
        got = outp.read_bytes().decode("utf-8") if outp.exists() else ""
    assert r.stdout.strip() == "0", f"孪生该退 0（14 项全过）: rc={r.stdout!r}\n{got[:300]}"
    assert got == want, f"报告与 Python 版不同:\n  py     {want!r}\n  loment {got!r}"
    # 独立期望值: 项数必须等于我这边**自己数**出来的检查数, 且全过
    n = len(got.strip().splitlines()) - 1
    assert got.count("  PASS  ") == n == 14, (n, got[-120:])
    assert f"lomccheck: 14/14 通过" in got, got[-120:]
    print(f"      {n} 项检查: 与 Python 版逐字节相同（含刚补上的 meta 重复键）")


@test
def test_lomc_twin_selfhost_compiles():
    """`lomccheck.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}lomccheck_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomccheck.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomccheck.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomccheck.lomt 成功 ({len(rr.stdout)}B IR)")


@test
def test_string_escapes_are_six_and_the_rest_is_an_error():
    """`#33` / `#36`：字符串转义认 `n t r 0 \\\\ "` 这六个，**表外是 E019**。

    原先表里没有 `r` 与 `0`，两者都走"未知转义取原字符"那条兜底 —— 于是 `"\\r"` 静默变成
    **字母 r**（用户写回车得到一个 r），`"\\x41"` 静默变成三个字符 `x41`。而仓库自己在用
    `"\\0"`（`loment/lib/proc.lomt` 的 `"/bin/sh\\0"`），一直靠同一条静默兜底在跑。

    判据两头都量：**认的那几个值对不对**（不是"不报错"，是**字节**），以及**表外真的拒**。
    """
    def val(raw: str) -> str:
        return [t for t in lomc.lex(raw) if t.kind == "string"][0].val

    assert val('"a\\rb"') == "a\rb", repr(val('"a\\rb"'))
    assert val('"a\\0b"') == "a\x00b", repr(val('"a\\0b"'))
    assert val('"a\\tb"') == "a\tb" and val('"a\\nb"') == "a\nb"
    assert val('"a\\\\b"') == "a\\b" and val('"a\\"b"') == 'a"b'
    for bad in ('"\\q"', '"\\x41"', '"\\u0041"'):
        try:
            lomc.lex(bad)
        except lomc.LomError as e:
            assert "未知转义" in str(e), (bad, e)
        else:
            raise AssertionError(f"{bad} 没有被拒 —— 又是静默取原字符")


def main() -> int:
    return loment_suite.run(TESTS, "lomc_test")


if __name__ == "__main__":
    sys.exit(main())
