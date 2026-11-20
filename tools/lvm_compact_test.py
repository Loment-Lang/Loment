#!/usr/bin/env python3
"""lvm_compact_test.py — 紧凑形的判据（`docs/250`，issue #328）。

四条：

* **A 恒等** —— 语料每一份：**文本**跑一遍、**紧凑形**跑一遍，stdout 字节 + 退出码相同；
  而且 **T1 的剖面也逐字节相同**（同一个引擎、同一份程序，换的只是编码 —— 剖面要是变了，
  说明解码把什么弄丢了）。
* **B 体积** —— 点名一份，**前后两个数**。⚠ 还钉一条反向的：**每一份都必须比文本小**。
  第一版就是这么红的：`@__loment_heap` 是 64 KiB 的 `zeroinitializer`，按字节原样写进去，
  `tour` 的紧凑形是文本的 **330%** —— 比原文件还大。
* **C 加载更快** —— 同一进程交替跑：**文本路**（解析 + 编译 + 跑）对 **紧凑路**（解码 + 跑），
  `min of N`。⚠ 这条量的是**加载**，不是执行：长程序上它会被执行时间摊薄，所以倍数只记账
  （`docs/250` §1 已经把"文件级解析只占 0.6%"量出来了）。
* **D 形态可读、错形点名拒** —— 读文件头就能说出是哪种形；把文本喂给紧凑形加载器要
  **点名拒**，不许当成文本硬解（`docs/250` §2.1）。

用法: python tools/lvm_compact_test.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_compact  # noqa: E402
import lvm_test as LT  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 语料**复用**，不另抄一份。参考跑不了的几份由 `lvm_test` 那张棘轮点名，这里跟着跳过。
CORPUS = ELF.CORPUS


def _ir(rel: str) -> str:
    src = ROOT / rel
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


def test_compact_is_identity_on_the_corpus() -> None:
    """判 A：文本与紧凑形跑出来**逐字节相同**，剖面也一样。"""
    ran = []
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        blob = lvm_compact.encode_ir(ir)
        b0, bc = io.BytesIO(), io.BytesIO()
        p0, pc = lvm.Profile(), lvm.Profile()
        r0 = lvm.run_ir(ir, b0, p0, [rel])
        rc = lvm_compact.run_compact(blob, bc, pc, [rel])
        assert (r0, b0.getvalue()) == (rc, bc.getvalue()), (
            f"[{stem}] 文本与紧凑形对不上：文本 rc={r0} {b0.getvalue()[:40]!r} / "
            f"紧凑 rc={rc} {bc.getvalue()[:40]!r}")
        a, b = p0.text(), pc.text()
        if a != b:
            la, lb = a.split("\n"), b.split("\n")
            k = next((i for i in range(max(len(la), len(lb))) if la[i:i + 1] != lb[i:i + 1]), -1)
            raise AssertionError(f"[{stem}] 剖面不同 @第 {k} 行: 文本 {la[k:k + 1]} / 紧凑 {lb[k:k + 1]}")
        ran.append(stem)
    assert ran, "一份都没跑成 —— 判据没测到东西"
    print(f"      文本 / 紧凑形逐字节相同（含剖面）: {len(ran)} 份（{', '.join(ran)}）")


def test_compact_is_smaller() -> None:
    """判 B：体积**前后两个数**，且**每一份都比文本小**。

    反向那条是承重的：算错一处分组（例如把 64 KiB 的零全局按字节原样写）就会得到
    "紧凑形比文本大三倍"，而 stdout 照样对 —— 只有这条会红。
    """
    worst = (0.0, "")
    biggest = (0, "", 0, 0)
    for rel in CORPUS:
        stem = Path(rel).stem
        if stem in LT.VM_KNOWN_GAPS:
            continue
        ir = _ir(rel)
        blob = lvm_compact.encode_ir(ir)
        ratio = len(blob) / len(ir)
        assert ratio < 1.0, (
            f"[{stem}] 紧凑形比文本还大：{len(blob)}B vs {len(ir)}B（{ratio * 100:.0f}%）"
            " —— 编码把什么东西按字节展开了")
        if ratio > worst[0]:
            worst = (ratio, stem)
        if len(ir) > biggest[2]:
            biggest = (ratio, stem, len(ir), len(blob))
    ratio, stem, t, c = biggest
    print(f"      {stem}: {t / 1024:.1f} KB -> {c / 1024:.1f} KB（{ratio * 100:.1f}%）"
          f" · 语料里最差的一份 {worst[1]} {worst[0] * 100:.1f}%")


def test_compact_loads_faster() -> None:
    """判 C：**加载**更快（文本路：解析+编译；紧凑路：解码）。同一进程交替，`min of N`。

    ⚠ 只说加载。执行时间两边一样（同一个引擎），所以程序越大这条越不显眼 ——
    `docs/250` §1 那条测量（文件级解析只占 0.6%）已经把这个方向说清楚了。
    """
    rel = "loment/examples/lumtui_demo.lomt"
    ir = _ir(rel)
    blob = lvm_compact.encode_ir(ir)                 # 编码不算在"加载"里
    text_ms, comp_ms = [], []
    for _ in range(5):
        t = time.perf_counter()
        lvm_compact.encode_ir(ir)                    # 文本路：解析 + 编译（编码当它不存在）
        text_ms.append(time.perf_counter() - t)
        t = time.perf_counter()
        lvm_compact.decode(blob)                     # 紧凑路：只解码
        comp_ms.append(time.perf_counter() - t)
    a, b = min(text_ms), min(comp_ms)
    assert b < a, f"紧凑形加载没有更快：文本 {a * 1000:.1f} ms / 紧凑 {b * 1000:.1f} ms"
    print(f"      加载 lumtui_demo：文本路 {a * 1000:.1f} ms · 紧凑路 {b * 1000:.1f} ms "
          f"⇒ {a / b:.2f}×（只记账，不做门禁）")


def test_form_is_recorded_and_a_wrong_form_is_refused() -> None:
    """判 D：文件头说得清是哪种形；喂错形**点名拒**。"""
    ir = _ir("loment/examples/user_hello.lomt")
    blob = lvm_compact.encode_ir(ir)
    assert blob[:4] == lvm_compact.MAGIC, blob[:4]
    assert blob[4] == lvm_compact.VERSION, blob[4]
    assert blob[5] == lvm_compact.FORM_COMPACT, blob[5]

    # 喂文本原文 -> 点名拒（不是"当成文本硬解"）
    try:
        lvm_compact.decode(ir.encode("utf-8"))
    except lvm_compact.CompactError as e:
        assert "紧凑形" in str(e), e
    else:
        raise AssertionError("把文本喂给紧凑形加载器，它居然收了")

    # 头对、版本不认识 -> 也点名拒
    bad = bytearray(blob)
    bad[4] = 99
    try:
        lvm_compact.decode(bytes(bad))
    except lvm_compact.CompactError as e:
        assert "99" in str(e), e
    else:
        raise AssertionError("版本号不认识却收了")
    print("      文件头：魔数 / 版本 / 形态；文本与错版本都点名拒")


def test_the_cli_writes_and_reads_a_compact_file() -> None:
    """判（#723）：`tools/lvm.py` 自己会写、会读紧凑形 —— 它不再只是"自己判据的玩具"。

    走的全是**真入口**（子进程跑 `lvm.py`）：写出来的文件与文本路跑出的 stdout/退出码相同，
    文本文件喂给 `--compact` 要点名拒，两个开关不能同时给。
    """
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    rel = "loment/examples/user_hello.lomt"
    cli = [sys.executable, str(ROOT / "tools" / "lvm.py")]
    env = {**os.environ, "PYTHONUTF8": "1"}
    with tempfile.TemporaryDirectory() as td:
        blob = Path(td) / "user_hello.lvmb"
        w = subprocess.run(cli + [rel, "--compact-out", str(blob)],
                           capture_output=True, cwd=str(ROOT), env=env)
        assert w.returncode == 0, w.stderr.decode("utf-8", "replace")
        assert blob.read_bytes()[:4] == lvm_compact.MAGIC, blob.read_bytes()[:4]
        assert w.stdout == b"", "--compact-out 只编不跑，不该有程序输出"

        a = subprocess.run(cli + [rel], capture_output=True, cwd=str(ROOT), env=env)
        b = subprocess.run(cli + ["--compact", str(blob)], capture_output=True,
                           cwd=str(ROOT), env=env)
        assert a.returncode == 0, a.stderr.decode("utf-8", "replace")
        assert (a.stdout, a.returncode) == (b.stdout, b.returncode), (
            f"文本路与紧凑路不同：{a.stdout!r}/{a.returncode} vs "
            f"{b.stdout!r}/{b.returncode}")

        c = subprocess.run(cli + ["--compact", rel], capture_output=True, cwd=str(ROOT),
                           env=env)
        assert c.returncode != 0 and b"LVMC" in c.stderr, (
            f"把文本喂给 --compact 没点名拒：rc={c.returncode} {c.stderr!r}")

        d = subprocess.run(cli + ["--compact", rel, "--compact-out", str(blob)],
                           capture_output=True, cwd=str(ROOT), env=env)
        assert d.returncode == 2, f"两个开关同时给居然过了：rc={d.returncode}"
    print("      CLI：--compact-out 写出 LVMC 文件，--compact 跑出逐字节相同的结果；"
          "错形与互斥开关都拒")


def test_the_mirror_is_still_deferred() -> None:
    """判（#724）：镜像那一半**还没落** —— 这条是棘轮，落地时它会红。

    见 `docs/158` 已知口子第 13 条：`loment/tools/lvm.lomt` 里没有紧凑形，所以
    `docs/250` §3 E 的孪生判据对它**不适用**。谁把紧凑形写进镜像，就得
    按 §5 四条一起走，并删掉这条判据。
    """
    src = (ROOT / "loment" / "tools" / "lvm.lomt").read_text(encoding="utf-8")
    hits = [w for w in ("compact", "COMPACT", "LVMC", "FORM_") if w in src]
    assert not hits, (
        f"`loment/tools/lvm.lomt` 里出现了紧凑形（{hits}）—— 镜像开工了："
        f"按 docs/158 §5 四条把两个实现一起改完，删掉这条判据，"
        f"再给 docs/250 §3 E 补上逐字节对拍")
    doc = (ROOT / "docs" / "158-loment-freeze.md").read_text(encoding="utf-8")
    assert "紧凑形 / T2 没有自举镜像" in doc, (
        "docs/158 里那条推迟记录不见了 —— 没有它，读者会把紧凑形的全绿当成孪生过了")
    print("      镜像：`lvm.lomt` 里没有紧凑形（推迟记录在 docs/158 第 13 条）")


def _reencode(blob: bytes) -> bytes:
    """decode 之后再 encode 一遍（`run_compact` 内部的那条路，不跑程序）。"""
    import lvm_t2  # noqa: PLC0415

    g, f, e, c = lvm_compact.decode(blob)
    vm = lvm_t2.T2Executor(g, f, e, io.BytesIO())
    vm.prog.cfuncs.update(c)
    return lvm_compact.encode_program(vm.prog, g, e)


def test_a_zero_filled_global_keeps_its_length_under_round_trip() -> None:
    """#689：`encode(decode(encode(x)))` 与 `encode(x)` 逐字节相同。

    `zeroinitializer` 记的是 `tag=0, len`；解码成 `None` 之后若再编码写 0，
    64 字节的堆就悄悄变成 0 字节 —— 而 stdout 全对，只有这条看得见。
    """
    src = ("@__loment_heap = global [64 x i8] zeroinitializer\n"
           "define i32 @_start() {\nentry:\n  ret i32 0\n}\n")
    one = lvm_compact.encode_ir(src)
    two = _reencode(one)
    if one != two:
        k = next(i for i in range(min(len(one), len(two))) if one[i] != two[i])
        raise AssertionError(
            f"零全局往返不稳定：第 {k} 字节 {one[k]:#04x} -> {two[k]:#04x}"
            f"（{len(one)}B vs {len(two)}B）")
    print(f"      零全局 [64 x i8] 往返稳定（{len(one)}B）")


def test_counts_taken_from_the_stream_do_not_drive_allocations() -> None:
    """#690：流里的 `ln` 与 `nslots` 都**不许**直接拿去分配。

    34 字节的产物曾让 `ln=10^8` 吃掉 100 MB、`nslots=10^8` 吃掉 816 MB。
    """
    def blob_with(body) -> bytes:
        out = bytearray(lvm_compact.MAGIC)
        out.append(lvm_compact.VERSION)
        out.append(lvm_compact.FORM_COMPACT)
        lvm_compact._put_u(out, 2)              # 池：两条
        for s in ("g", "i8"):
            raw = s.encode()
            lvm_compact._put_u(out, len(raw))
            out += raw
        body(out)
        return bytes(out)

    def huge_zero_run(out):
        lvm_compact._put_u(out, 1)              # 一个全局
        lvm_compact._put_u(out, 0)              # 名字 = 池[0]
        lvm_compact._put_u(out, 1)              # 类型 = 池[1]（i8）
        lvm_compact._put_u(out, 0)              # tag 0 = 全零
        lvm_compact._put_u(out, 10 ** 8)        # 长度 100 MB（而 i8 只该是 1）
        lvm_compact._put_u(out, 0)              # externs
        lvm_compact._put_u(out, 0)              # funcs

    def huge_nslots(out):
        lvm_compact._put_u(out, 0)              # 无全局
        lvm_compact._put_u(out, 0)              # externs
        lvm_compact._put_u(out, 1)              # 一个函数
        lvm_compact._put_u(out, 0)              # 名字 = 池[0]
        lvm_compact._put_u(out, 10 ** 8)        # nslots
        lvm_compact._put_u(out, 0)              # nparams
        lvm_compact._put_u(out, 0)              # nblocks

    for tag, fn in (("全零长度", huge_zero_run), ("nslots", huge_nslots)):
        try:
            lvm_compact.decode(blob_with(fn))
        except lvm_compact.CompactError as e:
            assert tag in str(e) or "不可信" in str(e) or "全零" in str(e), e
        else:
            raise AssertionError(f"流里声称的 {tag} 被直接信了（会按声称值分配）")
    print("      流里的 `ln` / `nslots` 都被拒，没有按声称值分配")


def test_trailing_bytes_are_refused() -> None:
    """#691：这一形没有校验和，长度是它唯一的身份 —— 多一个字节就是另一个产物。"""
    one = lvm_compact.encode_ir(_ir("loment/examples/user_hello.lomt"))
    for extra in (b"x", b"\x00" * 100):
        blob = one + extra
        try:
            lvm_compact.decode(blob)
        except lvm_compact.CompactError as e:
            assert str(len(extra)) in str(e), e
        else:
            raise AssertionError(f"尾部多 {len(extra)} 字节却收下了")
    # 两份完整产物连在一起也不行
    try:
        lvm_compact.decode(one + one)
    except lvm_compact.CompactError:
        pass
    else:
        raise AssertionError("两份产物连在一起却收下了")
    print("      尾部垃圾 / 两份拼接：都点名拒")


def test_every_truncation_is_a_compact_error_that_names_the_section() -> None:
    """#692：任意前缀都是 `CompactError`，且说得出被截断的是哪一段。

    改之前：0–5 字节是 `CompactError`，**6 字节往上全是 `IndexError`**；
    5 字节时那句拒绝还拿同一个值跟自己比（“不是 b'LVMC'”）。
    """
    one = lvm_compact.encode_ir(_ir("loment/examples/user_hello.lomt"))
    for cut in range(len(one)):
        try:
            lvm_compact.decode(one[:cut])
        except lvm_compact.CompactError as e:
            msg = str(e)
            assert "IndexError" not in msg
            if cut == 5:
                assert "b'LVMC'" not in msg, msg
        except Exception as e:                  # noqa: BLE001
            raise AssertionError(
                f"截到 {cut} 字节时抛的是 {type(e).__name__}：{e}") from None
        else:
            if cut < len(one):
                raise AssertionError(f"截到 {cut} 字节却解出来了")

    # 池里有一段不是 UTF-8 -> CompactError，不是 UnicodeDecodeError
    bad = bytearray(lvm_compact.MAGIC)
    bad.append(lvm_compact.VERSION)
    bad.append(lvm_compact.FORM_COMPACT)
    lvm_compact._put_u(bad, 1)                  # 一条池
    lvm_compact._put_u(bad, 1)                  # 长 1
    bad += b"\xff"
    lvm_compact._put_u(bad, 0)                  # 无全局
    lvm_compact._put_u(bad, 0)                  # externs
    lvm_compact._put_u(bad, 0)                  # funcs
    try:
        lvm_compact.decode(bytes(bad))
    except lvm_compact.CompactError as e:
        assert "UTF-8" in str(e), e
    else:
        raise AssertionError("池里不是 UTF-8 却收下了")
    print(f"      全部 {len(one)} 个前缀 + 非 UTF-8 池：都是 CompactError")


def test_the_encodable_set_is_pinned() -> None:
    """#693：紧凑形**序列化整份程序**，所以它比解释器窄一档 —— 这一档必须被钉住。

    T0/T2 是**懒编译**：走不到的坏代码不编译。`encode_ir` 不懒（不静默丢函数），
    于是"有个函数用了 T2 没实现的指令"在解释器里能跑、在编码时点名拒。
    这条判据把那条边界写成可证伪的两半：解释器收，编码器拒且**点出那条指令**。
    """
    src = ("define void @dead() {\nentry:\n  %x = fadd double 1.0, 2.0\n  ret void\n}\n"
           "define i32 @_start() {\nentry:\n  ret i32 0\n}\n")
    out = io.StringIO()
    assert lvm.run_ir(src, out) == 0, "解释器本来应当懒编译、跑得到 _start"
    try:
        lvm_compact.encode_ir(src)
    except lvm.LvmError as e:
        assert "fadd" in str(e), f"拒绝了，但没点出那条指令：{e}"
    else:
        raise AssertionError("T2 没实现的指令被静默编进了紧凑形")
    print("      懒编译边界：解释器收 / encode_ir 点名拒（fadd）")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_compact_test")


if __name__ == "__main__":
    sys.exit(main())
