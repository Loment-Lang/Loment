#!/usr/bin/env python3
"""loment_ct_test.py — **编译期子集解释器**的判据（S4.0，`docs/184` §9）。

语料在 `loment/ct/*.lomt`：每支是编译期子集里的一个可求值程序，入口是 `fn main() -> u64`。
**期望值写在语料文件里**（`// expect: OK <十进制>` 或 `// expect: ERR <种类>`）——
判据**不另抄一份**，所以两边不会漂。

判据两条，一条已落地、一条是 S4.0 的核心：

1. `test_reference_interpreter_matches_corpus` —— 参考侧（`tools/loment_interp.py`）
   跑语料，逐支对上期望值。**这是规范**，自举侧要镜像的就是它。
2. `test_selfhosted_interpreter_matches_reference` —— 自举侧
   （`loment/selfhost/interp.lomt`，**用 Loment 写的 Loment 解释器**）与参考侧
   **逐字节同结果** —— S4.0 的核心判据（`docs/184` §9）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_interp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CT = ROOT / "loment" / "ct"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 语料自描述的期望值。`OK <十进制>` 或 `ERR <种类>`（种类必须两边一致 —— 见 docstring）。
_EXPECT = re.compile(r"^//\s*expect:\s*(\S.*?)\s*$", re.M)


def _corpus() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in sorted(CT.glob("*.lomt")):
        m = _EXPECT.search(p.read_text(encoding="utf-8"))
        assert m, f"{p.name}: 缺 `// expect:` 行 —— 语料必须**自描述**期望值"
        out.append((p, m.group(1)))
    assert out, f"{CT} 里一支语料都没有 —— 判据会空转"
    return out


@test
def test_reference_interpreter_matches_corpus():
    """参考侧解释器跑编译期语料，逐支对上**写在文件里**的期望值。

    这条同时是**规范**：自举侧要镜像的语义就是它跑出来的。
    """
    cases = _corpus()
    bad: list[str] = []
    for p, want in cases:
        _rc, got = loment_interp.run(p)
        if got != want:
            bad.append(f"{p.name}: 期望 {want!r}, 实得 {got!r}")
    assert not bad, "\n".join(bad)
    print(f"      参考侧: {len(cases)} 支语料全部对上期望值")


#: 自举侧解释器的 C 外壳（与 p8 的夹具同一形状：**原生 exe**，带 CRT —— 不必交叉到 WSL）。
#:
#: ⚠️ **不要用 `printf("%s", out)`** —— 实测它会崩（`0xC0000005`），而 `fwrite` + `fputc`
#: + `fflush` 没事。原因未查明，记在这里免得下次再踩。反正判据只要 stdout 上那一行。
_DRIVER = r'''
#include <stdio.h>
/* 第 4 个形参是**解释器的工作堆**（见 `interp.lomt` 的 `CT_HEAP_BYTES`）。
 * 为什么不让它自己 alloc: 自举 driver 要跑宏体, 而它的 emit_module 一次要 49152B,
 * 与解释器的 42768B 同在一进程 —— 都从语言那 64 KiB bump 堆拿就必然撞 (docs/184 §7 ①b)。 */
extern unsigned int ct_run(char *src, unsigned int n, char *out, char *heap);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    static char buf[1048576];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    static char out[4096];
    out[0] = 0;
    static char heap[__CT_HEAP_BYTES__];
    unsigned int rc = ct_run(buf, (unsigned int)n, out, heap);
    unsigned int L = 0;
    while (L < 4095 && out[L]) L++;
    fwrite(out, 1, L, stdout);
    fputc('\n', stdout);
    fflush(stdout);
    return (int)rc;
}
'''

#: 从 `interp.lomt` 里读 `CT_HEAP_BYTES` —— **不另抄一份**。
#:
#: 抄一份就会漂：那个数改了（加一块缓冲、调一个 CAP）而这边没跟上，症状是外壳的
#: `heap[]` 比解释器要的小 → 写穿栈 → 一段与本次改动无关的崩。判据要的是"两边一致"，
#: 不是"两边都写了 42768"。
_CT_HEAP_RE = re.compile(r"^pub const CT_HEAP_BYTES: u32 = (\d+);", re.M)


def _ct_heap_bytes() -> int:
    src = (ROOT / "loment" / "selfhost" / "interp.lomt").read_text(encoding="utf-8")
    m = _CT_HEAP_RE.search(src)
    assert m, "interp.lomt 里找不到 `pub const CT_HEAP_BYTES: u32 = <数>;`"
    return int(m.group(1))


@test
def test_selfhosted_interpreter_matches_reference():
    """自举侧（**用 Loment 写的 Loment 解释器**）与参考侧**逐字节同结果**。

    这是 S4.0 的**核心判据**（`docs/184` §9）：两个解释器跑同一支编译期程序，连**错误的
    种类**都要一样。它红 = `comefor` 的地基是错的。

    为什么它值得单列一条：参考侧是 Python（走 AST），自举侧是 Loment（走 token）——
    **两边连"程序长什么样"都不一样**，可结论必须一字不差。
    """
    clang = shutil.which("clang")
    if not clang:
        fb = r"C:\Program Files\LLVM\bin\clang.exe"
        clang = fb if Path(fb).exists() else None
    if not clang:
        print("      SKIP: 无 clang")
        return
    src = ROOT / "loment" / "selfhost" / "interp.lomt"
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"interp.lomt 自己检查不过: {errs[:2]}"
    cases = _corpus()
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "interp.ll").write_text(lomentc.emit_llvm(mod, ROOT, deps),
                                       encoding="utf-8", newline="\n")
        (tdp / "h.c").write_text(
            _DRIVER.replace("__CT_HEAP_BYTES__", str(_ct_heap_bytes())),
            encoding="utf-8", newline="\n")
        exe = tdp / "interp.exe"
        r = subprocess.run([clang, "-O1", "-o", str(exe), str(tdp / "h.c"),
                            str(tdp / "interp.ll")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-500:]
        bad: list[str] = []
        for p, want in cases:
            rr = subprocess.run([str(exe), str(p)], capture_output=True, text=True,
                                timeout=60, shell=False)
            got = rr.stdout.strip()
            if got != want:
                bad.append(f"{p.name}: 期望 {want!r}, 自举 {got!r} (rc={rr.returncode})")
        assert not bad, "自举侧与参考侧不一致:\n" + "\n".join(bad)
    print(f"      自举侧: {len(cases)} 支语料与参考侧逐字节同结果")


def main() -> int:
    return loment_suite.run(TESTS, "loment_ct_test")


if __name__ == "__main__":
    sys.exit(main())
