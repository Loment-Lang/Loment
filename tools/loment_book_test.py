#!/usr/bin/env python3
# loment_book_test.py — 书里的每个 `rust,run` 块都必须真编真跑（docs/ 里的书是教材，也会腐烂）
#
# 约定（书正文里有说明，这里必须是它的**唯一实现**）：
#
#   ```rust,run
#   module …
#   ```
#   <!-- stdin: …\n… -->      （可选：喂给程序的 stdin，`\n` 是转义）
#   <!-- expect: … -->         （可选：stdout 必须逐字节等于它；没有就只要求退出码 0）
#
# 紧跟在围栏之后的两条 HTML 注释是本判据读的全部元数据。块里若写了
# `use "helpers.lomt"`，判据把 `book/by-example/helpers.lomt` 一起放进临时目录 —— 书里
# 那些短例子靠它打印。
#
# 运行: python tools/loment_book_test.py       (退出码 0 = 全绿)

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "book"
SRC = BOOK / "src"
BY_EXAMPLE = BOOK / "by-example"
CONTRIBUTING_SRC = ROOT / "contributing" / "src"
HELPERS = BY_EXAMPLE / "helpers.lomt"
IS_WIN = sys.platform == "win32"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_BLOCK = re.compile(r"```rust,run\n(.*?)```", re.S)
_STDIN = re.compile(r"<!--\s*stdin:\s*(.*?)\s*-->")
_EXPECT = re.compile(r"<!--\s*expect:\s*(.*?)\s*-->", re.S)


def _unescape(s: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "n":
                out.append(10)
                i += 2
                continue
            if n == "t":
                out.append(9)
                i += 2
                continue
            if n == "\\":
                out.append(92)
                i += 2
                continue
            out.extend(c.encode())
            i += 1
            continue
        out.extend(c.encode())
        i += 1
    return bytes(out)


def _blocks(text: str):
    """(source, stdin_bytes|None, expect_bytes|None, line_no)"""
    out = []
    for m in _BLOCK.finditer(text):
        src = m.group(1)
        tail = text[m.end():m.end() + 400]
        # 只认紧跟块之后（最多隔一个空行）的注释
        head = tail.split("\n\n", 1)[0]
        sin = _STDIN.search(head)
        exp = _EXPECT.search(head)
        line = text[:m.start()].count("\n") + 1
        out.append((src, _unescape(sin.group(1)) if sin else None,
                    _unescape(exp.group(1)) if exp else None, line))
    return out


def _build(src: str, td: Path, stem: str):
    f = td / f"{stem}.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    mod, deps = lomentc.load_unit(f, ROOT)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        return None, errs[0]
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    if IS_WIN:
        blob, _ = lomelf.compile_pe(ir)
        exe = td / f"{stem}.exe"
    else:
        blob, _ = lomelf.compile_ll(ir)
        exe = td / f"{stem}.elf"
    exe.write_bytes(blob)
    if not IS_WIN:
        exe.chmod(0o755)
    return exe, None


def _run_one(path: Path, src: str, sin, exp, line: int) -> list[str]:
    bad: list[str] = []
    with tempfile.TemporaryDirectory(prefix="lombook_") as tds:
        td = Path(tds)
        if 'use "helpers.lomt"' in src:
            (td / "helpers.lomt").write_text(HELPERS.read_text(encoding="utf-8"),
                                             encoding="utf-8", newline="\n")
        try:
            exe, err = _build(src, td, path.stem)
        except Exception as e:  # noqa: BLE001
            return [f"{path.name}:{line}: 编译抛异常 {type(e).__name__}: {e}"]
        if exe is None:
            return [f"{path.name}:{line}: 编译失败: {err}"]
        try:
            r = subprocess.run([str(exe)], input=(sin or b""),
                               capture_output=True, timeout=60, shell=False)
        except subprocess.TimeoutExpired:
            return [f"{path.name}:{line}: 运行超时"]
        if r.returncode != 0:
            bad.append(f"{path.name}:{line}: 退出码 {r.returncode}"
                       f" (stdout={r.stdout[:120]!r} stderr={r.stderr[:120]!r})")
        if exp is not None and r.stdout.rstrip(b"\n") != exp.rstrip(b"\n"):
            bad.append(f"{path.name}:{line}: stdout 不符\n  期望 {exp!r}\n  实际 {r.stdout!r}")
    return bad


def _run_dir(files: list[Path], label: str, allow_zero: bool = False) -> None:
    bad: list[str] = []
    blocks = 0
    for p in files:
        text = p.read_text(encoding="utf-8")
        for src, sin, exp, line in _blocks(text):
            blocks += 1
            bad.extend(_run_one(p, src, sin, exp, line))
    assert blocks > 0 or allow_zero, f"{label}: 一个 rust,run 块都没找到"
    if bad:
        raise AssertionError(f"{label}: {len(bad)} 个块不过\n" + "\n".join(bad[:12]))
    if blocks:
        print(f"      {label}: {blocks} 个可运行块全部编译并运行通过")
    else:
        print(f"      {label}: {len(files)} 章，没有 rust,run 块（贡献书只有命令与指针）")


@test
def test_the_book_runs():
    """书正文（book/src/*.md）里每个 rust,run 块都编得过、跑得出、输出对。"""
    files = sorted(SRC.glob("*.md"))
    assert files, "book/src 不存在或为空"
    _run_dir(files, "book/src")


@test
def test_by_example_runs():
    """by-example 每个 rust,run 块同上。"""
    files = sorted(BY_EXAMPLE.glob("*.md"))
    assert files, "book/by-example 不存在或为空"
    _run_dir(files, "book/by-example")


@test
def test_contributing_book_renders():
    """贡献书没有可运行块，但每一章都要在（它讲的是仓库与流程，不是程序）。"""
    files = sorted(CONTRIBUTING_SRC.glob("*.md"))
    assert files, "contributing/src 不存在或为空"
    _run_dir(files, "contributing/src", allow_zero=True)


@test
def test_exercise_solutions_pass():
    """练习册的参考答案必须真的编得过、跑得出、输出逐字节等于 expect.txt。"""
    import lomentlings  # noqa: PLC0415
    dirs = lomentlings._exercise_dirs()
    assert dirs, "exercises/ 下没有练习"
    bad = []
    for d in dirs:
        good, why = lomentlings._solution_ok(d)
        if not good:
            bad.append(f"{d.name}: {why}")
    assert not bad, "答案不过:\n" + "\n".join(bad[:8])
    print(f"      exercises: {len(dirs)} 份答案全部对上")


def main() -> int:
    return loment_suite.run(TESTS, "loment_book_test")


if __name__ == "__main__":
    sys.exit(main())
