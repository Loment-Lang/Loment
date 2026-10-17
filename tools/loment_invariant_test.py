#!/usr/bin/env python3
"""tools/loment_invariant_test.py — `#369`：把 `#343` 那一类变成**可数**的。

`#343` 是一**类**缺陷："编译器不作声地死掉"。此前仓里没有任何东西看得见它 ——
表里那六格全是手找出来的，隔几个月一格。这条判据给它两条不变量、一份语料、一份
**只许变短**的已知缺口账，于是它第一次成为一个**数**。

两条不变量（只对**编译器的入口**问，不对 `run` —— 那个退出码是**用户程序**的）：

  1. **不无声失败**：非零退出必须伴随至少一条非空诊断。
     「诊断」**不包括 Python 回溯** —— 回溯是**编译器自己**死了（`#226` 那一格就是它），
     一个把 `Traceback` 打在 stderr 上的编译器没有告诉你哪里不对。
  2. **不被信号打死**：进程不该以信号终止 —— POSIX 启动器上就是 `rc >= 128`
     （`132` = SIGILL、`139` = SIGSEGV）。对 `ir` 这种入口，没有哪个输入该是这个结局。

**两份实现都要问。** 参考实现是 Python（发不出信号），而用户真正跑的是**自举驱动**：
`种子 ->(clang 一次)-> stage1`。`#265` 就是"参考实现那边还好、stage1 直接 SIGILL" ——
只驱动 `tools/lomentc.py` 的判据会在**产物正在死**的时候照旧绿（`#369` 里那条「陷阱」）。

语料两层：

  * **检进的最小见证**（`loment/invariants/*.lomt`）：这一类的目击者，一条一道墙；
  * **边界走查**（本文件生成、落临时目录）：把每道记过的墙**跨过去**，于是"`#44` 修好了"
    变成"墙挪到哪了、它说没说得出话"。

运行: python tools/loment_invariant_test.py   （缺 clang/WSL 时整条 SKIP）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "loment" / "invariants"
TESTS: list[tuple[str, object]] = []

PER_RUN_TIMEOUT = 45


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    return shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)


#: `#371`：能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑，Windows 上经 WSL。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                                # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _sh(script: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run([*_WSLCMD, "bash", "-lc", script],
                          capture_output=True, timeout=timeout, shell=False)


_STAGE1: Path | None = None
_STAGE1_TD: tempfile.TemporaryDirectory | None = None


def _stage1() -> Path:
    """种子 ->(clang 一次)-> **自举驱动** —— 用户真正跑的那份编译器。

    只建一次并在进程内缓存（一次 clang + 一次拷贝，约半分钟）。
    """
    global _STAGE1, _STAGE1_TD
    if _STAGE1 is not None:
        return _STAGE1
    clang = _clang()
    assert clang, "缺 clang"
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), f"缺自举种子: {seed}"
    _STAGE1_TD = tempfile.TemporaryDirectory()
    td = Path(_STAGE1_TD.name)
    s1 = td / "stage1"
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"种子 -> stage1 链接失败: {r.stderr[-300:]}"
    binn = "/tmp/loment_inv_stage1"
    rr = _sh(f"rm -f {binn} && cp {_wsl_path(s1)} {binn} && chmod +x {binn} && echo ok", 300)
    assert b"ok" in rr.stdout, f"stage1 拷进 WSL 失败: {rr.stderr[-300:]!r}"
    _STAGE1 = s1
    return s1


def _run(argv: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        return 124, "", ""
    return (r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))


def _run_stage1(src: Path) -> tuple[int, str, str]:
    """自举驱动跑一次。它在 WSL 里跑（`_wsl_path`），**cd 到仓根**（源按相对路径解析）。

    退出码**必须从里面 echo 回来**，不能拿外壳的：`wsl -e bash -lc` 那个进程的退出码不是
    里面那个二进制的（信号死掉在那一层**看不见** —— 实测它回报成一个普通的非零码，
    于是这一层会把"被 SIGILL 打死"误报成"无声失败"，而**信号正是这条判据要看的东西**）。
    两个流也一并 `cat` 回来：WSL 写 `/mnt/d` 再让 Windows 立刻读有可见性延迟
    （`/mnt/c` 那次踩过），走管道就没有这个面。
    """
    script = (f"cd {_wsl_path(ROOT)} && timeout {PER_RUN_TIMEOUT} /tmp/loment_inv_stage1 "
              f"{_wsl_path(src)} > /tmp/loment_inv.out 2> /tmp/loment_inv.err; "
              f"echo \"RC=$?\"; echo 'OUT<<'; cat /tmp/loment_inv.out; echo '>>OUT'; "
              f"echo 'ERR<<'; cat /tmp/loment_inv.err; echo '>>ERR'")
    try:
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                           capture_output=True, timeout=PER_RUN_TIMEOUT + 300, shell=False)
    except subprocess.TimeoutExpired:
        return 124, "", ""
    text = r.stdout.decode("utf-8", "replace")
    rc = -1
    for line in text.split("\n"):
        if line.startswith("RC="):
            rc = int(line[3:]) if line[3:].lstrip("-").isdigit() else -1
            break
    out = _between(text, "OUT<<", ">>OUT")
    err = _between(text, "ERR<<", ">>ERR")
    return rc, out, err


def _between(text: str, a: str, b: str) -> str:
    i = text.find(a)
    j = text.find(b, i + 1) if i >= 0 else -1
    return text[i + len(a):j] if i >= 0 and j >= 0 else ""


def verdict(rc: int, out: str, err: str) -> str | None:
    """这两条不变量：**不无声失败**、**不被信号打死**。违反就返回名字，合格返回 `None`。

    "有声"的口径里**回溯不算** —— 见文件头。判据是"err 里有 `Traceback` 且 stdout 是空的"：
    真正的诊断走 `[ERR] …`，而回溯是解释器自己在收尸。
    """
    if rc == 124:
        return "超时"
    if rc >= 128:
        return f"信号 {rc}"
    if rc != 0:
        if "Traceback (most recent call last)" in err and not out.strip():
            return "回溯（不是诊断）"
        if not out.strip() and not err.strip():
            return "无声失败"
    return None


def boundary_cases() -> dict[str, str]:
    """把 `#343` 表里每一道**记过**的墙跨过去（本文件生成，落临时目录，不进树）。

    每道墙至少给两个点：边界内一格 + 边界外一格。`#44` 那道墙今天在 64，所以给 64/65 ——
    "墙挪到哪了"这件事于是每次都由**实测**回答，而不是由某年记下的数字回答。
    """
    c: dict[str, str] = {}
    for n in (10, 11):                      # #265：10 干净、11 死
        ps = ", ".join(f"a{i}: i32" for i in range(1, n + 1))
        args = ", ".join("1 as i32" for _ in range(n))
        c[f"boundary-params-{n}"] = (
            "module w\n\nfn f(" + ps + ") -> i32 {\n    return a1;\n}\n\n"
            "fn _start() {\n    let r: i32 = f(" + args + ");\n"
            "    syscall4(60, r as u64, 0, 0);\n}\n")
    for n in (64, 65):                      # #44：今天的墙
        c[f"boundary-nested-{n}"] = (
            "module w\n\nfn _start() {\n" + "if 1 == 1 {\n" * n
            + "syscall4(60, 7, 0, 0);\n" + "}\n" * n + "}\n")
    for n in (100, 250):                    # #226：平坦项数
        c[f"boundary-terms-{n}"] = (
            "module w\n\nfn _start() {\n    let x: u64 = "
            + " + ".join("1" for _ in range(n))
            + ";\n    syscall4(60, x, 0, 0);\n}\n")
    for n in (800,):                        # 源大小（函数条数）
        body = "".join(f"fn f{i}() -> i32 {{ return {i}; }}\n\n" for i in range(n))
        c[f"boundary-{n}fn"] = (
            "module w\n\n" + body + "fn _start() {\n    syscall4(60, 7, 0, 0);\n}\n")
    return c


#: **已知缺口账（`#343` 的成员）** —— 只许变短。
#:
#: 这不是"允许它坏"，是"把它**记成一个数**"：这条判据的力气用在**别变更差**上，而每修掉
#: 一格就把这一行划掉（判据会提醒）。量于 2026-10-15，`origin/main` 上前一版（`#368` 那棵树）。
#:
#: 两格的来路（都在**自举驱动**那一侧，参考实现那边都不死 —— 这就是 `#369` 说的陷阱）：
#:   * `eleven-params` / `boundary-params-11`：`#265`。10 个参数干净、11 个 SIGILL(132)，
#:     而**参考实现照旧编得出 IR**（`rc=0`）—— 于是这一格又是"两份实现分岔，而死掉的是
#:     用户真正跑的那份"。10 个参数那一格两边都过，所以墙正好在 11 上。
#:   * `struct-literal-no-trailing-comma`：`#11`。末字段没尾逗号的结构体**当字面量用**时
#:     SIGSEGV(139)。参考实现照着编得出 IR（`rc=0`）—— 两份实现在这里**分岔**，
#:     而分岔的方向是"用户跑的那份死掉"。
#:   * `flat-250-terms` / `boundary-terms-250`：`#226`。平坦 250 项在**参考实现**那边是
#:     Python `RecursionError`：退出码非零、stderr 上 195 KB，而那是**回溯不是诊断**
#:     —— 编译器自己炸了，没说你哪里不对。自举驱动那边反而编得出来（`rc=0`），
#:     于是这一格又是两份实现**朝另一半方向**分岔。边界在 100 与 250 之间。
    # `eleven-params` / `boundary-params-11` **已划掉**：`#265` 在这个 PR 里修好了（形参类型表
    # 10 槽 → 20 槽）。一个 11 形参的程序现在编得出来，跑起来返回第 11 个形参的值。
    # `struct-literal-no-trailing-comma` **已划掉**：`#11` 修好了（字段表的步长不再假设尾逗号）。
    # `flat-250-terms` / `boundary-terms-250` **已划掉**：`#226` 在这个 PR 里修好了（递归上限
    # 抬到 8000，真撞上转成 E023），参考实现不再炸 `RecursionError`。账**只许变短**，所以
    # 修好的那一格必须在同一个 PR 里删掉 —— 否则这条判据会点名"这一格已经绿了"。
KNOWN_GAPS: set[tuple[str, str]] = set()


@test
def test_the_compiler_never_fails_silently_or_dies_by_a_signal():
    """`#369`：两条不变量过一遍语料，**两份实现都要问**；缺口账只许变短。

    **Why**：见文件头。要点是这一类的存在方式 —— 它没有判据，所以成员只能手找；
    手找就必然隔几个月一格；而"修好一个"与"墙挪了"在没有任何测量时**看起来一样**。

    **How to apply**：语料两层（检进的最小见证 + 边界走查），两份实现各跑一遍，
    把 `(用例, 实现)` 这一对当账。`KNOWN_GAPS` 之外的任何一格都判失败；
    `KNOWN_GAPS` 里已经修好的格子也判失败（**只许变短**，与门禁基线同一条规矩）。
    """
    clang, wsl = _clang(), _wsl()
    if not (clang and wsl):
        print("      SKIP: 需要 clang + WSL（stage1 要在 Linux 上跑）")
        return
    assert CORPUS.is_dir(), f"缺语料目录: {CORPUS}"

    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        cases: list[tuple[str, Path]] = [
            (p.stem, p) for p in sorted(CORPUS.glob("*.lomt"))]
        for name, src in boundary_cases().items():
            p = td / f"{name}.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            cases.append((name, p))

        _stage1()          # 缺 clang/WSL 之外的原因建不出来时，让它在这里就炸（带报文）
        found: dict[tuple[str, str], str] = {}
        for name, src in cases:
            rc, out, err = _run([sys.executable, "-X", "utf8", "-I",
                                 str(ROOT / "tools" / "loment.py"), "ir", str(src)],
                                PER_RUN_TIMEOUT)
            v = verdict(rc, out, err)
            if v:
                found[(name, "reference")] = v
            rc, out, err = _run_stage1(src)
            v = verdict(rc, out, err)
            if v:
                found[(name, "stage1")] = v

    def show(keys) -> str:
        return ", ".join(f"{n}@{impl}（{found[(n, impl)]}）" for n, impl in sorted(keys))

    new = set(found) - KNOWN_GAPS
    assert not new, (
        f"这两条不变量被违反了 {len(new)} 格（共 {len(cases) * 2} 次运行）：{show(new)} —— "
        "非零退出必须带一条非空诊断（**Python 回溯不算诊断**），且编译器不该以信号终止。"
        "若这是已知成员，请把它**写进** `KNOWN_GAPS` 并说明来路；若是新的，那正是这道门要拦的")
    gone = KNOWN_GAPS - set(found)
    assert not gone, (
        f"账上记着、今天已经不犯了：{show(gone)} —— 这一格修好了，请把 `KNOWN_GAPS` 里那行**删掉**"
        "（只许变短：这条判据的力气用在「别变更差」上，账不清就分不出新红）")
    print(f"      {len(cases)} 份语料 × 2 份实现 = {len(cases) * 2} 次运行，"
          f"违反 {len(found)} 格（都记在账上）：{show(set(found))}")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:                                       # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_invariant_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
