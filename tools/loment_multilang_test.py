#!/usr/bin/env python3
"""loment_multilang_test.py — **一条真跑起来的多语言程序**（`docs/183` §8.2 的 S2 判据）。

`loment/examples/multilang/` 里有四份源码，**三段各用一门语法写**：

    01-c/pack.lomt          C 语法（.lomt 装着 C）      装帧/拆帧
    02-python/policy.lomt   Python 语法                 判策略等级
    03-java/Machine.lomt    Java 语法                   状态机
    main.lomt               Loment                      串起来

判据就是 `docs/183` §8.2 给 S2 定的那条：**走通一个真实的多语言程序**。这里比它更进一步
—— 三段都要**真的算到结果里**：

    frame=459752 level=2 state=2    （stdout，逐字节比）
    退出码 200                       （712 的低 8 位）

## 为什么需要这个夹具，而不是"`loment run` 一下就完了"

脚本**替 S2 做了调节器该做的事**：把 `.lomt` 里的外源语法**物化成目标语言要的形状**
（`pack.lomt` → `pack.c`、`policy.lomt` → `policy.py`、`Machine.lomt` → `Machine.java`），
再调各自的编译器。**单一真源仍然是那三份 `.lomt`** —— 物化出来的文件全在临时目录里，
谁都不会去手改它们（手改必然与 `.lomt` 漂）。

S2 落地时，这一步搬进编译器（`foruse` / `command`），**本判据一行都不用改** ——
它比的是"这个程序跑出什么"，不是"谁去编的"。

## 两条腿都在这条链上（`docs/173` §2）

* **C** 走**翻译**（`docs/188` §3）：一份 C 写法写的单元**就是 Loment**，
  `lomt_from --impl` 把 `pack.lomt` 翻成真 `pub fn` —— **不起 clang、不链 `.o`**；
* **Python / Java** 走**运行期腿**：`proc_sh` 起进程，读 stdout。

两条腿之间**传不了指针**，所以帧落成 4 字节小端的 `frame.bin`。**那个边界是这一节最该
被看见的东西**，不是实现偷懒。

用法: python tools/loment_multilang_test.py   （无 clang/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


EX = ROOT / "loment" / "examples" / "multilang"

#: 期望值。**推出来的，不是抄的**（见 `_expected`）：
#:   w = pack_frame(7, 1000) = (7 << 16) | 1000 = 459752
#:   Python: 459752 & 0xFFFF = 1000 >= ALARM_LEVEL(900) -> level 2
#:   Java:   step(IDLE, 2) -> ALARM -> 2
WANT_STDOUT = "frame=459752 level=2 state=2\n"
#: `sensor * 100 + value / 100 + state` = 700 + 10 + 2 = 712 -> 低 8 位
WANT_RC = (7 * 100 + 1000 // 100 + 2) & 0xFF

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _wsl_home() -> str:
    return subprocess.run([*_WSLCMD, "bash", "-lc", "echo -n $HOME"],
                          capture_output=True, text=True, timeout=60,
                          shell=False).stdout.strip()


def _wsl_ok(script: str) -> bool:
    try:
        return subprocess.run([*_WSLCMD, "bash", "-lc", script],
                              capture_output=True, text=True, timeout=180,
                              shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _materialize(td: Path) -> Path:
    """把 `.lomt` 里的外源语法**物化成目标语言要的形状**，并生成 C 的接口单元。

    这一步是**脚本替 S2 做调节器**（文件头那条说明）。**单一真源是那三份 `.lomt`** ——
    谁都不该去手改这里写出来的副本。
    """
    (td / "pack.c").write_text((EX / "01-c" / "pack.lomt").read_text(encoding="utf-8"),
                               encoding="utf-8", newline="\n")
    (td / "policy.py").write_text((EX / "02-python" / "policy.lomt").read_text(encoding="utf-8"),
                                  encoding="utf-8", newline="\n")
    (td / "Machine.java").write_text((EX / "03-java" / "Machine.lomt").read_text(encoding="utf-8"),
                                     encoding="utf-8", newline="\n")
    (td / "main.lomt").write_text((EX / "main.lomt").read_text(encoding="utf-8"),
                                  encoding="utf-8", newline="\n")
    # C -> Potato -> **翻译成 Loment**（`docs/188` §3）
    #
    # 2026-09-18 改：原来是"发接口单元 + clang 编 `.o` + 链进来"——那是**旧模型**
    # （把 C 当外源代码）。按新模型，一份 C 写法写的单元**就是 Loment**，所以这里
    # 走 `--impl` 翻成真 `pub fn`，**不用 clang、不用链**。
    doc, _rep = potato_from.LANGS["c"]((td / "pack.c").read_text(encoding="utf-8"), "pack.c",
                                       "strict")
    text, _skipped = lomt_from.emit_lomt(doc, impl=True)
    (td / "pack_impl.lomt").write_text(text, encoding="utf-8", newline="\n")
    return td


def _write_java_shim(td: Path, jdk: str) -> None:
    """夹具生成 `run-java.sh` —— **它在替 S2 当调节器**。

    `loment/lib/proc.lomt:120` 的 exec 是 `execve(path, argv, NULL)`：**不传环境**，
    所以子进程的 `PATH` 只有 `/bin/sh` 自带的默认值，自建 JDK 不在里面。
    "找到目标语言的运行时、把它摆到进程能看见的地方"本来就是 `foruse` 的活儿 ——
    S2 落地前由夹具顶上，**判据比的是这个程序跑出什么，不是谁去找到 java**。
    """
    (td / "run-java.sh").write_text(f'#!/bin/sh\nexec "{jdk}/java" "$@"\n',
                                    encoding="utf-8", newline="\n")


@test
def test_three_syntaxes_feed_one_program():
    """**三段各用一门语法写，合成一个真能跑的程序**（`docs/183` §8.2 的 S2 判据）。

    stdout 与退出码**都要对**，而且两者都由**三段共同决定**：

    * C 没被真调到（`--link` 没接上 / ABI 传参错）-> `w` 与 `frame_sensor(w)` 都不对
      -> 打印与退出码同时变；
    * Python 那一段没跑成 -> `level` 会是 `ask` 的失败值 9；
    * Java 那一段没跑成 -> `state` 是 9；
    * 帧没落成字节 -> 那两段读到的是空的（或读不到 `frame.bin`）。

    **没有一行是"只测一侧"的**：这就是"多语言程序真的跑通了"该有的形状。
    """
    clang, wsl = _clang(), _wsl()
    if not clang or not wsl:
        print("      SKIP: 需要 clang + WSL")
        return
    jdk = f"{_wsl_home()}/fujotc/jdk/bin"
    if not _wsl_ok(f"test -x '{jdk}/java'"):
        print("      SKIP: 没有自建 jdk（跑 wsl -e bash tools/loment_toolchain.sh jdk）")
        return
    with tempfile.TemporaryDirectory() as t:
        td = _materialize(Path(t))
        # ---- C 那段**不需要编译器**了（它是表层语法，翻成 Loment 就完了）；
        #      只剩 Java 那一段要 javac（它是**显式拉起**的运行期腿）。
        assert _wsl_ok(f"cd {_wsl_path(td)} && '{jdk}/javac' Machine.java"), "javac 失败"
        _write_java_shim(td, jdk)
        # ---- Loment 侧：接口单元 + 主程序 -> IR -> 链上 C 的目标文件
        mod, deps = lomentc.load_unit(td / "main.lomt", ROOT)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"main.lomt 检查不过: {errs[:3]}"
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        # **没有 `load_foreign`** —— C 那段已经翻成 Loment 了，没有目标文件要链
        blob, _info = lomelf.compile_ll(ir, [])
        exe = td / "multilang.elf"
        exe.write_bytes(blob)
        # ---- 跑：**cwd 设成这个目录**，那两段的命令行是相对路径（帧文件也落在这儿）
        out = td / "out.txt"
        err = td / "err.txt"
        r = subprocess.run(
            [*_WSLCMD, "bash", "-lc",
             f"chmod +x {_wsl_path(exe)} && cd {_wsl_path(td)} && "
             f"{_wsl_path(exe)} > {_wsl_path(out)} 2> {_wsl_path(err)}; echo -n $?"],
            capture_output=True, text=True, timeout=300, shell=False)
        rc = int(r.stdout.strip() or -1)
        stdout = out.read_text(encoding="utf-8") if out.exists() else ""
        stderr = err.read_text(encoding="utf-8", errors="replace") if err.exists() else ""
        assert stdout == WANT_STDOUT, (
            f"stdout 不对:\n  期望 {WANT_STDOUT!r}\n  实得 {stdout!r}\n"
            f"  rc={rc} stderr={stderr[-300:]!r}")
        assert rc == WANT_RC, f"退出码不对: {rc} != {WANT_RC}（三段都参与这个数）"
        print(f"      C(翻译) + Python(进程) + Java(进程) -> {WANT_STDOUT.strip()!r}, rc={rc}")


@test
def test_source_is_single_truth():
    """**真源只有那三份 `.lomt`** —— 夹具物化出来的副本必须与它逐字节相同。

    防的是"顺手改了一下临时目录里的 `pack.c`"：那样判据会绿，而 `.lomt` 已经不是
    程序真正的样子了 —— 这份多语言程序就退化成了三份各自为政的源码。
    """
    with tempfile.TemporaryDirectory() as t:
        td = _materialize(Path(t))
        for lomt, native in (("01-c/pack.lomt", "pack.c"),
                             ("02-python/policy.lomt", "policy.py"),
                             ("03-java/Machine.lomt", "Machine.java")):
            a = (EX / lomt).read_text(encoding="utf-8")
            b = (td / native).read_text(encoding="utf-8")
            assert a == b, f"{lomt} 与物化出来的 {native} 不同"
        # 接口单元是**生成物**（手改会被下次生成覆盖）—— 顶上那行注释就是标记
        impl = (td / "pack_impl.lomt").read_text(encoding="utf-8")
        assert impl.startswith("// 由 tools/lomt_from.py"), impl[:80]
        # **是 `pub fn`，不是 `pub extern fn`** —— C 写法写出来的是 Loment（docs/188 §3）
        assert "pub fn pack_frame" in impl, impl[:400]
        assert "pub extern fn " not in impl, impl[:400]
    print("      单一真源: 三份 .lomt 物化后逐字节相同；C 那段翻成 pub fn 且无 extern")


def main() -> int:
    return loment_suite.run(TESTS, "loment_multilang_test")


if __name__ == "__main__":
    sys.exit(main())
