#!/usr/bin/env python3
# loment_admit_test.py — 准入判定：两个实现逐字节相同 + 那条不对称（`docs/238`）
#
# 判据（照着 `tools/loment_syscalls_test.py` 那一对的形状）：
#   1. 同一份用例文件，`loment/tools/lomadmit.lomt`（链成 ELF 后跑）与 `tools/loment_admit.py`
#      的 **stdout / stderr / 退出码逐字节相同**；
#   2. 不对称钉住：**收窄即时、放宽要窗口**（`docs/218` §2 第 3 条）；
#   3. **输入是闭集** —— 多一个字段就是错。这一条不是卫生习惯，它是"没有时钟"那条性质的
#      **结构化形式**：`docs/218` §8 的生死判据要求"掺时钟或随机数的实现必须红"，
#      而掺进去的第一个动作就是**多要一个输入**；
#   4. **可复现**：同一份用例跑两遍，字节相同（两个实现都是纯函数）；
#   5. Stage 1 × Stage 2 的接缝：`asked` 来自产物自己的 `net_ladder`（`docs/237`）。
#
# 运行: python tools/loment_admit_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_admit  # noqa: E402
import lomentc  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**（WSL 的 /tmp 共用，固定名会让并发门禁互相跑错）。
_T = f"/tmp/loment-admit-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomadmit.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 语料

#: 用例文件。**每条都写在注释里说清它在钉什么** —— 这份语料要同时喂两个实现，
#: 所以它自己就得是可以逐条读懂的。
CASES = """\
1  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  0  w3  w0  0
2  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  1  w3  w0  0
3  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  1  w3  w0  1
4  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  1  w3  w0  2

5  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  1  w3  w1  3
6  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  9  w3  w1  4
7  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  9  w3  w2  0
8  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  9  w3  w2  1
9  aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  9  w3  w2  2
10 aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  32 w3  w3  0
11 aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  1  w1  w3  3
12 aaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbb  0  w1  w1  5
13 aaaaaaaaaaaaaaaa  cccccccccccccccc  32 w3  w0  0
14 aaaaaaaaaaaaaaaa  cccccccccccccccc  32 w2  w3  0
15 aaaaaaaaaaaaaaaa  cccccccccccccccc  8  w0  w2  0
16	a	b	0	w0	w0	0
"""

#: 不成形的那些（每条**两侧都得报同一句话**）。最后一个最重要：**8 个字段** ——
#: 那是"掺进一个时钟"要做的第一件事（`docs/218` §8）。
BAD_CASES = [
    ("six-fields", "1 a b 0 w0 w0\n", 0, 6),
    ("eight-fields", "1 a b 0 w0 w0 0 42\n", 0, 8),
    ("seq-not-a-number", "x a b 0 w0 w0 0\n", 1, 0),
    ("negative-hits", "-1 a b 0 w0 w0 0\n", 1, 0),
    ("unknown-rung", "1 a b 0 w9 w0 0\n", 2, 0),
]


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


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


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomadmit.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomadmit.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomadmit.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _py(args: list[str]) -> tuple[int, str, str]:
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_admit.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}admit_{name}.bin"
    quoted = " ".join(args)
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {quoted} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, err


def _pair(elf: Path, td: Path, name: str, args: list[str],
          wsl_args: list[str] | None = None) -> tuple[int, str, str]:
    """两侧同一份用例，但**路径写法各给各的**：Python 侧是本机路径，Loment 侧跑在 WSL 里，
    只认 `/mnt/...`（给 Windows 路径的话 shell 会把反斜杠吃掉 → `cannot open`）。"""
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, args if wsl_args is None else wsl_args)
    if (wr, wo, we) == (gr, go, ge):
        return gr, go, ge
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n"
        f"  py     out={wo[-300:]!r} err={we[-300:]!r}\n"
        f"  loment out={go[-300:]!r} err={ge[-300:]!r}")


def _skip() -> bool:
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return True
    return False


# ---------------------------------------------------------------- 判据

@test
def test_the_whole_corpus_matches_python():
    """整份语料：两侧 stdout / stderr / 退出码**逐字节相同**，而且语料本身非平凡。"""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        cf = td / "cases.txt"
        cf.write_text(CASES, encoding="utf-8", newline="\n")
        rc, out, err = _pair(elf, td, "corpus", [str(cf)], [_wsl_path(cf)])
        assert rc == 0, (rc, out[:200], err[:200])
        # 语料必须真的踩到每一条路：不然"逐字节相同"是两份 `{0,0,0}` 在互相点头
        for why in ("zero", "hold", "widen-pending", "widen-confirmed", "shrink-immediate"):
            assert f"why={why}" in out, f"语料没踩到 {why}: {out[:300]!r}"
        assert "granted=w3" in out and "granted=w0" in out, out[:300]


@test
def test_malformed_cases_report_the_same_line():
    """不成形的用例：两侧**同一句话、同一个退出码**。含那条"多一个字段"。"""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        for name, text, kind, got in BAD_CASES:
            cf = td / f"{name}.txt"
            cf.write_text(text, encoding="utf-8", newline="\n")
            rc, out, err = _pair(elf, td, name, [str(cf)], [_wsl_path(cf)])
            assert rc == 1, (name, rc, out[:200], err[:200])
            # 与 Python 侧那句话**逐字**对得上（`_pair` 已经比过一遍，这里再钉住内容本身）
            want = (loment_admit.BAD_FIELDS.format(n=1, k=got) if kind == 0 else
                    loment_admit.BAD_SEQ.format(n=1) if kind == 1 else
                    loment_admit.BAD_RUNG.format(n=1))
            assert err == want + "\n", (name, err, want)
            assert out == "", (name, out[:200])


@test
def test_widen_needs_a_window_and_shrink_is_immediate():
    """那条不对称（`docs/218` §2 第 3 条）：**收窄即时，放宽要窗口**。

    同一份台账、同一条对端，只改历史 —— 逐号走一遍：`widen-pending` 连着
    `WIDEN_WINDOW - 1` 号，第 `WIDEN_WINDOW` 号才 `widen-confirmed`；而掉下来**当号**
    就生效（`shrink-immediate`，`held` 清零）。
    """
    s, p = "a" * 16, "b" * 16
    # 从 w0 起、台账一直够 w1：头两号挂账，第三号生效
    r1 = loment_admit.admit(1, s, p, 1, "w3", "w0", 0)
    assert (r1["granted"], r1["held"], r1["why"]) == ("w0", 1, "widen-pending"), r1
    r2 = loment_admit.admit(2, s, p, 1, "w3", "w0", 1)
    assert (r2["granted"], r2["held"], r2["why"]) == ("w0", 2, "widen-pending"), r2
    r3 = loment_admit.admit(3, s, p, 1, "w3", "w0", 2)
    assert (r3["granted"], r3["held"], r3["why"]) == ("w1", 3, "widen-confirmed"), r3
    # 台账掉了：当号就收窄，不等窗口
    r4 = loment_admit.admit(4, s, p, 1, "w3", "w2", 9)
    assert (r4["granted"], r4["held"], r4["why"]) == ("w1", 0, "shrink-immediate"), r4
    # 台账空了：回到 w0 —— 而 `w0` 不是拒绝，是"只够一次验证交换"（`docs/218` §11）
    r5 = loment_admit.admit(5, s, p, 0, "w3", "w2", 4)
    assert (r5["granted"], r5["conns"], r5["why"]) == ("w0", 0, "zero"), r5
    # **产物自己要求的上限**封顶：台账挣到 w3、但这份产物只写到 w1
    r6 = loment_admit.admit(6, s, p, 32, "w1", "w1", 0)
    assert (r6["earned"], r6["granted"]) == ("w3", "w1"), r6


@test
def test_the_judgement_is_reproducible_and_its_inputs_are_closed():
    """**可复现**（`docs/218` §2 第 4 条）：同一份用例跑两遍字节相同；**输入是闭集**。

    第二半才是要害：判定只有那七个数，**多一个字段就是错** —— 想掺时钟或随机数，
    第一个动作就是多要一个输入，而这一条当场红（§8 那条生死判据的结构化形式）。
    """
    assert loment_admit.run_cases(CASES) == loment_admit.run_cases(CASES)
    rc, out, _e = loment_admit.run_cases(CASES)
    # 语料里**非空行**的条数 == 记录条数（空行按约定跳过 —— 那是"跳过"不是"记录"）
    assert rc == 0 and out.count("\n") == len([x for x in CASES.splitlines() if x.strip()]), \
        (rc, out.count("\n"))
    # 八字段：**不是"忽略多余的那个"，是错**
    rc8, _o8, e8 = loment_admit.run_cases("1 a b 0 w0 w0 0 42\n")
    assert rc8 == 1 and "expected 7 fields, got 8" in e8, (rc8, e8)
    # 时间在判定里**没有位置**：`admit` 的形参表就是它的输入集 —— 多一个都得改签名，
    # 而判据这一侧比的是**两个实现**，改签名两边都得改，骗不过去。
    import inspect
    params = list(inspect.signature(loment_admit.admit).parameters)
    assert params == ["seq", "self_h", "peer_h", "hits", "asked", "granted_prev", "held"], params


@test
def test_asked_comes_from_the_artifact_ladder():
    """Stage 1 × Stage 2 的接缝（`docs/237`）：`asked` 就是产物自己 `net_ladder` 的最宽档。

    这条把"梯子"与"判定"接上：梯子给**上限**（这份程序自己要求多少），台账给**挣到的**，
    判定在两者之间取小的那个 —— 而**两者都是不读源码可判的数**。
    """
    # 单元测试：四档全 0 → w0；只有 w2 → w2；最宽的那档说了算
    assert loment_admit.asked_rung({"w0": 0, "w1": 0, "w2": 0, "w3": 0}) == "w0"
    assert loment_admit.asked_rung({"w0": 0, "w1": 3, "w2": 0, "w3": 0}) == "w1"
    assert loment_admit.asked_rung({"w0": 1, "w1": 0, "w2": 2, "w3": 0}) == "w2"
    assert loment_admit.asked_rung({"w0": 1, "w1": 2, "w2": 0, "w3": 1}) == "w3"
    # 真产物：那份钉子（四档都非零）要求的上限是 `w3`
    import json
    ex = ROOT / "loment" / "examples" / "net_ladder" / "main.lomt"
    mod, deps = lomentc.load_unit(ex, ROOT)
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    assert loment_admit.asked_rung(doc["net_ladder"]) == "w3", doc["net_ladder"]
    assert doc["net_ladder"]["total_net"] == doc["surfaces"]["net"], doc


@test
def test_lomadmit_selfhost_compiles():
    """`lomadmit.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。

    这条**非测不可**：上面那几条跑的是**参考实现**编出来的 ELF，而这份源里有两处已知的
    自举差异会咬人 —— **形参上限**（`FujoJTOP/loment#265`：11 个以上自举侧无声 SIGILL，
    所以解析结果摆进缓冲、判定函数只收 4 个形参）与**入口必须显式 `die(0)`**
    （`_start` 返回就是跳到垃圾地址，实测收尾段错误）。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}admit_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomadmit.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomadmit.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 2000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomadmit.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_admit_test")


if __name__ == "__main__":
    raise SystemExit(main())
