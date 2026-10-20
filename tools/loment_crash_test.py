#!/usr/bin/env python3
# loment_crash_test.py — 崩溃批次的判据（`#805` / `#868`–`#874`）
#
# 这一套只钉一件事：**深度/容量越界要出诊断，不许出信号**。
#
# 每个复现都跑**真入口** —— 用仓库里那份已提交的种子
# (`loment/build/selfhost_driver.ll`) 链出来的自举驱动。不是"拿 Python 实现试一下"：
# 这些 bug 本身就是自举侧独有的（参考实现抛的是 `LomError`）。
#
# 判据形状：
#   * 退出码必须是 1（诊断）或 0（合法）；**大于 128 一律算信号**（128+n）；
#   * stderr 必须点名（`E23` 嵌套 / `E30` 未知名 / `the unit is too large`）；
#   * 同一族的尺寸阶梯要么**全部被拒**，要么"被接受的尺寸是一个前缀"—— 后者才是
#     "单调"：一旦越界，再大的输入不能换一条路崩掉。
#
# 运行: python tools/loment_crash_test.py   (没有 clang / 跑不了 Linux ELF 时逐条 SKIP)

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"
ADMIT_SRC = ROOT / "loment" / "tools" / "lomadmit.lomt"
TESTS: list[tuple[str, object]] = []

#: 诊断里必须出现的三种名字（与自举侧同一套码）。
DEEP = "E23"                     # 嵌套超过 64 层
UTYPE = "E30"                    # 类型位置上的未知名（嵌套泛型在自举侧就是这个）
TOO_LARGE = "the unit is too large"

_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]

#: 驱动 ELF 只建一次：同一份种子字节 -> 同一个产物（与 `loment_p8_test` 同一条约定）。
_BUILT: dict[str, Path] = {}


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _skip() -> bool:
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return True
    return False


_MEM_STUB = """\
void *memmove(void *d, const void *s, unsigned long n) {
    unsigned char *dd = d; const unsigned char *ss = s;
    if (dd < ss) { for (unsigned long i = 0; i < n; i++) dd[i] = ss[i]; }
    else { for (unsigned long i = n; i > 0; i--) dd[i - 1] = ss[i - 1]; }
    return d;
}
"""


def _driver() -> Path:
    """种子 -> 自举驱动 ELF（`-O1`，与 `loment_p8_test` 的构建参数同一条）。"""
    hit = _BUILT.get("driver")
    if hit is not None and hit.exists():
        return hit
    assert SEED.exists(), f"缺种子 {SEED.relative_to(ROOT)} (python tools/loment_seed.py --emit)"
    td = Path(tempfile.mkdtemp(prefix="loment-crash-"))
    atexit.register(shutil.rmtree, td, ignore_errors=True)
    stub = td / "memstub.c"
    stub.write_text(_MEM_STUB, encoding="utf-8", newline="\n")
    stub_o = td / "memstub.o"
    r = subprocess.run([_clang(), "--target=x86_64-unknown-linux-gnu", "-ffreestanding",
                        "-fno-builtin", "-O0", "-c", "-o", str(stub_o), str(stub)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    elf = td / "driver.elf"
    r = subprocess.run([_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                        "-ffreestanding", "-static", "-fuse-ld=lld", "-O1",
                        "-o", str(elf), str(SEED), str(stub_o)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    _BUILT["driver"] = elf
    return elf


def _run(elf: Path, entry: Path, td: Path, name: str) -> tuple[int, str, str]:
    """跑一个 ELF，返回 (退出码, stdout 文本, stderr 文本)。

    `rc` 从 shell 的 `$?` 显式抄出来：被信号打断的命令是 `128+n`（`ls` 的退出码是 2），
    所以判据能用 `rc >= 128` 认出"信号"，而不是把它当成某条诊断。
    """
    outp, errp = td / f"{name}.out", td / f"{name}.err"
    # **按进程一份**：WSL 的 /tmp 是共享的, 并发的两个门禁会互相跑对方的二进制。
    binn = f"/tmp/loment-crash-{os.getpid()}-{name}-{abs(hash(str(elf))) % 100000}"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {_wsl_path(entry)} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8", errors="replace") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8", errors="replace") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip()[-3:])
    except ValueError:
        rc = -1
    return rc, out, err


# ---------------------------------------------------------------- 复现程序

def _paren(n: int) -> str:
    return ("module t\n\nfn f() -> u32 { return " + "(" * n + "1" + ")" * n
            + "; }\n\nfn main() -> i32 { return 0; }\n")


def _call(n: int) -> str:
    return ("module t\n\nfn f(x: u32) -> u32 { return x; }\n\nfn g() -> u32 { return "
            + "f(" * n + "1" + ")" * n + "; }\n\nfn main() -> i32 { return 0; }\n")


def _unary(n: int) -> str:
    return ("module t\n\nfn f() -> bool { return " + "!" * n
            + "true; }\n\nfn main() -> i32 { return 0; }\n")


def _generic(n: int) -> str:
    return ("module t\n\npub struct G<T> { a: T }\n\nfn f(x: " + "G<" * n + "u32"
            + ">" * n + ") -> u32 { return 0; }\n\nfn main() -> i32 { return 0; }\n")


def _array_type(n: int) -> str:
    return ("module t\n\nfn f(a: " + "[" * n + "u32" + "]" * n
            + ") -> u32 { return 0; }\n\nfn main() -> i32 { return 0; }\n")


def _index(n: int) -> str:
    return ("module t\n\nfn f(a: [u32; 4]) -> u32 { return " + "a[" * n + "0"
            + "]" * n + "; }\n\nfn main() -> i32 { return 0; }\n")


def _array_literal(n: int) -> str:
    return ("module t\n\nfn f() -> u32 { let a: [u32; " + str(n) + "] = ["
            + ", ".join("0" for _ in range(n)) + "]; return a[0]; }\n\n"
            "fn main() -> i32 { return 0; }\n")


def _ladder(kind: str, n: int, td: Path) -> tuple[int, str, str]:
    p = td / f"{kind}{n}.lomt"
    p.write_text(globals()[f"_{kind}"](n), encoding="utf-8", newline="\n")
    return _run(_driver(), p, td, f"{kind}{n}")


def _assert_diagnosed(rc: int, err: str, what: str, marks: tuple[str, ...]) -> None:
    assert rc < 128, f"{what}: 进程被信号打断（rc={rc}, signal={rc - 128}）: {err[-200:]!r}"
    assert rc == 1, f"{what}: 退出码 {rc}（期望 1 = 诊断）: {err[-300:]!r}"
    assert any(m in err for m in marks), f"{what}: stderr 里没有 {marks}: {err[-300:]!r}"


# ---------------------------------------------------------------- 判据

@test
def test_deep_parentheses_are_diagnosed_not_signalled():
    """`#868`：200 层括号原先在自举驱动上 SIGSEGV，而 300 层反而报得出。

    两侧都钉：**上限以内照旧接受**（63 层），**到上限就报 E023**，之后每一档都只有
    诊断 —— 不存在"更大的输入换一条路崩掉"。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, _out, err = _ladder("paren", 63, td)
        assert rc == 0, f"63 层括号被误拒: rc={rc} {err[-200:]!r}"
        for n in (64, 100, 200, 300, 400, 1000, 5000):
            rc, _out, err = _ladder("paren", n, td)
            _assert_diagnosed(rc, err, f"paren {n}", (DEEP, TOO_LARGE))
    print("      括号：63 接受；64..5000 一律 E023 / 容量诊断")


@test
def test_deep_calls_are_diagnosed_not_signalled():
    """`#869`：300 层 `f(f(...))` 原先 SIGSEGV（200 层反而报得出）。"""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for n in (64, 100, 200, 300, 400, 1000):
            rc, _out, err = _ladder("call", n, td)
            _assert_diagnosed(rc, err, f"call {n}", (DEEP, TOO_LARGE))
    print("      调用：64..1000 一律 E023 / 容量诊断")


@test
def test_deep_unary_chain_is_diagnosed_not_signalled():
    """`#870`：1000 个 `!` 原先 SIGSEGV（500 个反而报得出）。

    一元链没有括号，块/括号的深度计数看不见它 —— 所以这一条钉的是**连续一元运算符**
    那一路。64 个仍接受，65 个起报。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, _out, err = _ladder("unary", 64, td)
        assert rc == 0, f"64 个 `!` 被误拒: rc={rc} {err[-200:]!r}"
        for n in (65, 100, 500, 1000, 5000):
            rc, _out, err = _ladder("unary", n, td)
            _assert_diagnosed(rc, err, f"unary {n}", (DEEP, TOO_LARGE))
    print("      一元链：64 接受；65..5000 一律 E023 / 容量诊断")


@test
def test_deep_generic_arguments_are_diagnosed_not_signalled():
    """`#871`：256 层 `G<G<...>>` 原先 SIGSEGV（128 层反而报得出）。

    自举侧的检查器不认识嵌套泛型实参，报的是 E030「类型位置上的未知名」（参考实现
    也报"未知类型 G<G<…>"）。这条只要求**每一档都有诊断、没有信号** —— 码可以是
    E030，也可以是容量诊断。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for n in (16, 64, 128, 256, 1000, 5000):
            rc, _out, err = _ladder("generic", n, td)
            _assert_diagnosed(rc, err, f"generic {n}", (UTYPE, DEEP, TOO_LARGE))
    print("      泛型实参：16..5000 一律 E030 / E023 / 容量诊断")


@test
def test_deep_array_types_are_diagnosed_not_signalled():
    """`#873`：256 层 `[[[...u32...]]]` 原先 SIGSEGV。

    自举侧的类型检查 `check_type` 对 `[` 是递归的 —— 括号深度那一趟必须先把它挡住。
    **注意**：自举侧本来就不认识嵌套数组类型（`[[u32]]` 起就报 E030），所以这里不断言
    "上限以内接受"：它要钉的只是"每一档都有诊断、没有信号"。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for n in (2, 4, 63, 64, 128, 256, 512, 1000):
            rc, _out, err = _ladder("array_type", n, td)
            _assert_diagnosed(rc, err, f"array_type {n}", (UTYPE, DEEP, TOO_LARGE))
    print("      数组类型：2..1000 一律 E030 / E023 / 容量诊断")


@test
def test_deep_index_expressions_are_diagnosed_not_signalled():
    """`#874`：256 层 `a[a[a[...]]]` 原先 SIGSEGV（128 层反而接受）。"""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        rc, _out, err = _ladder("index", 63, td)
        assert rc == 0, f"63 层下标被误拒: rc={rc} {err[-200:]!r}"
        for n in (64, 128, 256, 512, 1000):
            rc, _out, err = _ladder("index", n, td)
            _assert_diagnosed(rc, err, f"index {n}", (DEEP, TOO_LARGE))
    print("      下标：63 接受；64..1000 一律 E023 / 容量诊断")


@test
def test_array_literal_capacity_guard_is_monotonic():
    """`#805`：700 个元素的数组字面量原先 SIGSEGV，660 个反而报"token 太多"。

    根因不是"密度算错了"，是**词法器在写满缓冲之后还去读表**（`lx_ext_starts` 读
    `out[(n-1)*20]`）。修法是不读；于是容量守卫变成"计数 vs 容量"，每一档都报同一句话。
    这条要求**同一句话**（不是"都有诊断就行"）：非单调的根源就是不同尺寸走不同分支。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        msgs = []
        for n in (500, 660, 700, 1000, 2000):
            rc, _out, err = _ladder("array_literal", n, td)
            _assert_diagnosed(rc, err, f"array_literal {n}", (TOO_LARGE,))
            msgs.append(err.strip())
        assert len(set(msgs)) == 1, f"同一份超限输入报出了不同的诊断: {sorted(set(msgs))}"
    print(f"      数组字面量：500..2000 全部同一句容量诊断（{msgs[0][:48]}…）")


@test
def test_lomadmit_720_cases_still_succeeds():
    """`#872` 的回归面：720 条合法用例本来就能跑完（`#697` 修掉的是 64 KiB 堆）。

    把它留在这里是因为 `#872` 的复现就是这一档 —— 修容量错**不许**顺手把这一档
    变成"太大"。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _lomadmit(td)
        cf = td / "cases720.txt"
        cf.write_text("".join(f"{i} aa bb 0 w0 w0 0\n" for i in range(720)),
                      encoding="utf-8", newline="\n")
        rc, out, err = _run(elf, cf, td, "admit720")
        assert rc == 0, f"720 条合法用例被拒: rc={rc} {err[-200:]!r}"
        assert out.count("\n") == 720, f"输出行数 {out.count(chr(10))} != 720"
    print("      lomadmit：720 条合法用例仍然 rc=0、720 行输出")


@test
def test_lomadmit_oversized_input_is_a_capacity_error():
    """`#872`：用例文件超过 `CASES_CAP`（256 KiB）原先**静默截断**。

    截断之后最后一行只剩半行，报的是"line 1816: expected 7 fields, got 3" —— 一条
    指向**没写错的行**的假诊断。这条要求点名容量、退出码 2，且**不是信号**。
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _lomadmit(td)
        self_h, peer = "a" * 64, "b" * 64
        cf = td / "big.txt"
        cf.write_text("".join(f"{i} {self_h} {peer} 0 w0 w0 0\n" for i in range(2000)),
                      encoding="utf-8", newline="\n")
        assert cf.stat().st_size > 262144, cf.stat().st_size
        rc, _out, err = _run(elf, cf, td, "admitbig")
        assert rc < 128, f"lomadmit 被信号打断: rc={rc} {err[-200:]!r}"
        assert rc == 2, f"容量错的退出码应为 2: rc={rc} {err[-200:]!r}"
        assert "cases file is too large" in err, f"没有点名容量: {err[-300:]!r}"
    print("      lomadmit：>256 KiB 的用例文件报容量错（rc=2），不再静默截断")


def _lomadmit(td: Path) -> Path:
    """用**种子驱动**把 `loment/tools/lomadmit.lomt` 编成 IR，再交给 clang 链成 ELF。"""
    hit = _BUILT.get("admit")
    if hit is not None and hit.exists():
        return hit
    ll = td / "lomadmit.ll"
    rc, out, err = _run(_driver(), ADMIT_SRC, td, "emit_admit")
    assert rc == 0, f"驱动编 lomadmit 失败: rc={rc} {err[-300:]!r}"
    ll.write_text(out, encoding="utf-8", newline="\n")
    elf = td / "lomadmit.elf"
    r = subprocess.run([_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                        "-ffreestanding", "-static", "-fuse-ld=lld",
                        "-o", str(elf), str(ll)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    _BUILT["admit"] = elf
    return elf


def main() -> int:
    return loment_suite.run(TESTS, "loment_crash_test")


if __name__ == "__main__":
    sys.exit(main())
