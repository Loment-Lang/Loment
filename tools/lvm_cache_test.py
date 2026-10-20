#!/usr/bin/env python3
"""lvm_cache_test.py — 编译产物缓存（`docs/265`，issue #646）。

四条，逐条对应 issue 里"done 意味着什么"：

* **A 第二趟逐字节同第一趟** —— stdout + 退出码，外加 T1 剖面逐字节相同。
* **B 旧缓存点名拒绝** —— 拿 A 的缓存去配 B（内容 / IR 任一不同），必须是
  `CacheStale` 且消息里带着缓存路径与**是哪一样**变了；随后重编出来的答案是 B 的。
  这一条**不是**"缓存坏了就重编"那么弱：喂一份"另一份程序的缓存"时，
  静默用下去会给出**错的答案**，所以拒绝本身要被看见。
* **C 第二趟不编译** —— `lomelf.parse_type` / `parse_operand` 的确定性计数器：
  冷的那一趟 > 0，热的那一趟 **0**；反向一半也断言（否则量到的是"包没接上"）。
* **D 形态被记下来** —— 文件头写的是紧凑形；改成别的形/魔数/截断都必须点名拒。

用法: python tools/lvm_cache_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lvm_cache  # noqa: E402
import lvm_compact  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

IR_A = """define i32 @_start() {
entry:
  %a = add i32 20, 22
  %b = mul i32 %a, 2
  %z = zext i32 %b to i64
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %z, i64 0, i64 0)
  ret i32 0
}
"""

IR_B = """define i32 @_start() {
entry:
  %a = add i32 1, 2
  %b = mul i32 %a, 7
  %z = zext i32 %b to i64
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %z, i64 0, i64 0)
  ret i32 0
}
"""


class _Counters:
    """把两个解码函数包起来数次数。**两半都要数**（冷 > 0、热 == 0）。"""

    def __init__(self) -> None:
        self.pt = 0
        self.po = 0

    def __enter__(self):
        self._pt, self._po = lomelf.parse_type, lomelf.parse_operand

        def pt(*a, **k):
            self.pt += 1
            return self._pt(*a, **k)

        def po(*a, **k):
            self.po += 1
            return self._po(*a, **k)

        lomelf.parse_type, lomelf.parse_operand = pt, po
        return self

    def __exit__(self, *exc):
        lomelf.parse_type, lomelf.parse_operand = self._pt, self._po
        return False


def _run(payload: bytes, prof=None) -> tuple[bytes, int]:
    buf = io.BytesIO()
    rc = lvm_compact.run_compact(payload, buf, prof)
    return buf.getvalue(), rc


def test_cold_and_warm_are_byte_identical() -> None:
    """判 A：第二趟（缓存）与第一趟（冷）的 stdout + 退出码 + 剖面逐字节相同。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        entry = td / "a.ll"
        entry.write_text(IR_A, encoding="utf-8", newline="\n")
        cpath = lvm_cache.default_path(entry)

        p_cold = lvm.Profile()
        cold_payload = lvm_cache.load_or_build(cpath, entry, IR_A)
        out_cold, rc_cold = _run(cold_payload, p_cold)

        stale = cpath.read_bytes()
        p_warm = lvm.Profile()
        warm_payload = lvm_cache.load_or_build(cpath, entry, IR_A)
        out_warm, rc_warm = _run(warm_payload, p_warm)

        assert cpath.exists(), "第一次跑没有写缓存"
        assert out_cold == out_warm, (out_cold, out_warm)
        assert rc_cold == rc_warm == 84, (rc_cold, rc_warm)
        assert p_cold.text() == p_warm.text(), "剖面在冷/热两趟之间变了"
        assert stale == cpath.read_bytes(), "第二趟把缓存重写了（说明它没命中）"
    print("      冷/热两趟：stdout + rc + 剖面逐字节相同（rc=84）")


def test_warm_run_does_no_parsing() -> None:
    """判 C：冷的一趟有解码，热的一趟 0 次 —— 两半都断言。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        entry = td / "a.ll"
        entry.write_text(IR_A, encoding="utf-8", newline="\n")
        cpath = lvm_cache.default_path(entry)

        with _Counters() as c_cold:
            payload = lvm_cache.load_or_build(cpath, entry, IR_A)
            _run(payload)
        assert c_cold.pt > 0 and c_cold.po > 0, \
            f"冷的一趟一次都没解码（{c_cold.pt}/{c_cold.po}）—— 计数器没接上"

        with _Counters() as c_warm:
            payload2 = lvm_cache.load_or_build(cpath, entry, IR_A)
            _run(payload2)
        assert c_warm.pt == 0 and c_warm.po == 0, \
            f"热的一趟还在解码：parse_type={c_warm.pt} parse_operand={c_warm.po}"
    print(f"      冷：parse_type={c_cold.pt} / parse_operand={c_cold.po}；热：0 / 0")


def test_stale_cache_is_refused_by_name() -> None:
    """判 B：另一份程序的缓存必须被点名拒绝，重编出来的答案是新的那份。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        entry_a = td / "a.ll"
        entry_a.write_text(IR_A, encoding="utf-8", newline="\n")
        cpath = lvm_cache.default_path(entry_a)
        lvm_cache.load_or_build(cpath, entry_a, IR_A)          # A 的缓存

        # ① 只换 IR（入口文件一个字没动）也必须被拒 —— 依赖变了是同一类过期。
        key_same_file_new_ir = lvm_cache.key_for(entry_a, IR_B)
        try:
            lvm_cache.decode(cpath.read_bytes(), key_same_file_new_ir, cpath)
        except lvm_cache.CacheStale as e:
            assert "IR" in str(e), f"IR 变了却没说是 IR：{e}"
        else:
            raise AssertionError("IR 变了，缓存仍被接受")

        # ② 同一份缓存，配另一份输入（大小/mtime/内容都可能不同）。
        entry_b = td / "b.ll"
        entry_b.write_text(IR_B, encoding="utf-8", newline=chr(10))
        key_b = lvm_cache.key_for(entry_b, IR_B)
        try:
            lvm_cache.decode(cpath.read_bytes(), key_b, cpath)
        except lvm_cache.CacheStale as e:
            msg = str(e)
            assert str(cpath) in msg, f"拒绝消息没点名缓存文件：{msg}"
            assert "过期" in msg, f"拒绝消息没说清原因：{msg}"
        else:
            raise AssertionError("另一份程序的缓存被静默接受了")

        # 走正常入口：拒绝 -> 重编 -> 给出 B 的答案（21），不是 A 的（84）。
        payload = lvm_cache.load_or_build(cpath, entry_b, IR_B)
        _, rc = _run(payload)
        assert rc == 21, f"重编后的答案不对：{rc}（B 是 21）"
    print("      另一份输入/另一份 IR：点名拒绝，随后重编出正确的数")


def test_form_is_recorded_and_checked() -> None:
    """判 D：文件头写着形；改形 / 改魔数 / 截断都必须点名拒。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        entry = td / "a.ll"
        entry.write_text(IR_A, encoding="utf-8", newline="\n")
        cpath = lvm_cache.default_path(entry)
        payload = lvm_cache.load_or_build(cpath, entry, IR_A)
        key = lvm_cache.key_for(entry, IR_A)
        blob = cpath.read_bytes()
        assert blob.startswith(lvm_cache.MAGIC)
        assert blob[9] == lvm_cache.FORM_COMPACT, "文件头没写形"

        bad_form = bytearray(blob)
        bad_form[9] = 99
        bad_magic = bytearray(blob)
        bad_magic[0:8] = b"NOTCAChE"
        for name, data, want in (
                ("改了形", bad_form, "形"),
                ("改了魔数", bad_magic, "魔数"),
                ("截断", blob[:12], "截断")):
            try:
                lvm_cache.decode(bytes(data), key, cpath)
            except lvm_cache.CacheStale as e:
                assert want in str(e) or "不是" in str(e), f"[{name}] 消息没说清：{e}"
            else:
                raise AssertionError(f"[{name}] 被静默接受了")
        # 载荷本身还得是紧凑形（两层的形必须一致）。
        assert payload[:4] == lvm_compact.MAGIC
    print("      形/魔数/截断：三种都点名拒绝")


def test_cli_second_run_matches_first() -> None:
    """整条 CLI：两次 `--tier t2 --cache` 的 stdout、退出码与缓存文件都稳定。"""
    import subprocess

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        entry = td / "a.ll"
        entry.write_text(IR_A, encoding="utf-8", newline="\n")
        cpath = td / "out.lvmc"
        cmd = [sys.executable, str(ROOT / "tools" / "lvm.py"), str(entry), "--ir",
               "--tier", "t2", "--cache", str(cpath)]
        r1 = subprocess.run(cmd, capture_output=True)
        blob1 = cpath.read_bytes()
        r2 = subprocess.run(cmd, capture_output=True)
        assert r1.returncode == r2.returncode == 84, (r1.returncode, r2.returncode)
        assert r1.stdout == r2.stdout == b"", (r1.stdout, r2.stdout)
        assert blob1 == cpath.read_bytes(), "第二趟重写了缓存"
    print("      CLI 两趟：stdout/退出码/缓存文件都稳定（rc=84）")


def test_corpus_cold_and_warm_agree() -> None:
    """语料上再核一遍 A/C：`tour` 的冷/热两趟逐字节同，热的一趟不再解码。"""
    import lvm

    entry = ROOT / "loment" / "examples" / "tour.lomt"
    ir = lvm.compile_source(entry, ROOT)
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        cpath = td / "tour.lvmc"
        with _Counters() as c_cold:
            payload = lvm_cache.load_or_build(cpath, entry, ir)
            p_cold = lvm.Profile()
            out_cold, rc_cold = _run(payload, p_cold)
        assert c_cold.pt > 0, "语料冷的那一趟一次都没解码"
        with _Counters() as c_warm:
            payload2 = lvm_cache.load_or_build(cpath, entry, ir)
            p_warm = lvm.Profile()
            out_warm, rc_warm = _run(payload2, p_warm)
        assert c_warm.pt == c_warm.po == 0,             f"语料热的那一趟还在解码：{c_warm.pt}/{c_warm.po}"
        assert out_cold == out_warm and rc_cold == rc_warm
        assert p_cold.text() == p_warm.text()
    print(f"      tour：冷 {len(out_cold)} 字节 / rc={rc_cold}；热 0 次解码，输出与剖面全同")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "lvm_cache_test")


if __name__ == "__main__":
    sys.exit(main())
