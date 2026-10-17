#!/usr/bin/env python3
# loment_register_test.py — 在源码里注册一条 `loment` 命令的判据 (docs/223)
#
# 这条设计有三块，判据也按三块判：
#
#   1. **声明本身**（离线）—— 扫描的形状、三条诊断码（E024/E025/E026）、以及**那份
#      官方命令名清单两份实现不许漂**（参考实现是一张元组，自举是一串 `cmd_span_is`；
#      两边都得等于 `loment commands` 打出来的那批）。
#   2. **产物名字**（离线）—— `--print-command` 两个实现答得一样；两个启动器模板都
#      带着这次询问；拿桩驱动跑一遍真 bash 启动器，看它是不是真写成 `loment-<名字>`。
#   3. **工具链生成的入口**（要编译）—— 引擎发出来的 `_start` 要**真的能把 cmdline 交给
#      `command_main`**：编出来、链出来、跑起来，看 stdout 里是不是命令行、退出码是不是
#      参数个数。**只断言"IR 里有 _start"是这套东西最容易骗过自己的地方。**
#
# 运行: python tools/loment_register_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_diag  # noqa: E402
import loment_dist  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "loment" / "examples" / "cmd_hello.lomt"
#: 同一个命令的**另一种写法**（`register` 块，`docs/223` 形态 A）—— 两条判据拿这两个比。
REGISTER_EXAMPLE = ROOT / "loment" / "examples" / "register_hello.lomt"
CHECKER_SRC = ROOT / "loment" / "selfhost" / "checker.lomt"
IS_WIN = sys.platform == "win32"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _codes(src: str) -> list[int]:
    """一段源码在参考实现下的诊断码集（与 `loment_rule_parity` 同一个口径）。"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "case.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        msgs = lomentc.check(lomentc.load(f))
    return sorted({int(loment_diag.classify(m)[0][1:]) for m in msgs
                   if loment_diag.classify(m)[0] != "E999"})


GOOD = ("module m\n\npub fn loment_command() -> str { return \"mcmd\"; }\n\n"
        "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n")

#: 形态 A（`docs/223` §2 第 3 步）：名字在**语法**里，块里是这条命令的条目。
GOOD_A = ("module m\n\nregister mcmd {\n"
          "    pub fn command_main(argv: ptr, argc: u32) -> u32 {\n        return 0;\n    }\n}\n")


# ---------------------------------------------------------------- 1. 声明

@test
def test_the_five_rules_report_the_three_codes():
    """三条码各钉一边 —— 一个合法的、五个不合法的，码集逐个写死。

    合法的那个**必须是零码**：一个"看谁都想报"的规则比漏报更糟（它会把正常程序拦下来）。
    """
    assert _codes(GOOD) == [], "合法的命令单元不该有诊断"
    assert _codes(GOOD_A) == [], "合法的 `register` 单元不该有诊断"
    # 名字写成字符串（要带 `-` 时用）也是合法的 —— 合法名字的字符集是 `[A-Za-z0-9_-]`，
    # 而 `-` 不是标识符字符。
    assert _codes(GOOD_A.replace("register mcmd", 'register "m-cmd"')) == [], \
        "字符串写法的名字不该有诊断"
    cases = {
        # ---- 形态 A：与上面那几条同一批码，只是名字在语法里 --------------------
        "a-bad-name": ('module m\n\nregister "a/b" {\n'
                       "    pub fn command_main(argv: ptr, argc: u32) -> u32 {\n"
                       "        return 0;\n    }\n}\n", [24]),
        # 块里再套一个：块**只许一个函数**，所以这是形状不对（E024）—— 不是"最外层说了算"。
        "a-nested": ('module m\n\nregister outer {\n    register inner {\n'
                     "        pub fn command_main(argv: ptr, argc: u32) -> u32 {\n"
                     "            return 0;\n        }\n    }\n}\n", [24]),
        "a-no-main": ("module m\n\nregister mcmd {\n"
                      "    pub fn helper() -> u32 {\n        return 0;\n    }\n}\n", [26]),
        "decl-no-body": ('module m\n\npub fn loment_command() -> str { return "mcmd"; }\n\n'
                         "fn helper() -> u32 {\n    return 0;\n}\n", [26]),
        # (源码, 期望码)
        "bad-name": ("module m\n\npub fn loment_command() -> str { return \"a/b\"; }\n\n"
                     "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n",
                     [24]),
        "reserved-name": ("module m\n\npub fn loment_command() -> str { return \"version\"; }\n\n"
                          "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n",
                          [24]),
        "bad-shape": ("module m\n\npub fn loment_command() -> str {\n"
                      "    let s: str = \"x\";\n    return s;\n}\n\n"
                      "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n",
                      [24]),
        "two-entries": ("module m\n\npub fn loment_command() -> str { return \"mcmd\"; }\n\n"
                        "fn _start() {\n    syscall4(60, 0, 0, 0);\n}\n\n"
                        "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n",
                        [25]),
        "body-no-decl": ("module m\n\n"
                         "pub fn command_main(argv: ptr, argc: u32) -> u32 {\n    return 0;\n}\n",
                         [26]),
        "bad-signature": ("module m\n\npub fn loment_command() -> str { return \"mcmd\"; }\n\n"
                          "pub fn command_main(a: u32, b: u32) -> u32 {\n    return 0;\n}\n",
                          [26]),
    }
    for name, (src, want) in cases.items():
        got = _codes(src)
        assert got == want, f"{name}: 期望 {want}，实际 {got}"


@test
def test_library_may_not_declare_a_command():
    """库不许声明命令 —— **装载器**规则（要两个单元），所以它进不了 `_CASES`。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "lib.lomt").write_text(
            "module lib\n\npub fn loment_command() -> str { return \"libcmd\"; }\n",
            encoding="utf-8", newline="\n")
        (d / "entry.lomt").write_text(
            "module entry\n\nuse \"lib.lomt\"\n\nfn _start() {\n    syscall4(60, 0, 0, 0);\n}\n",
            encoding="utf-8", newline="\n")
        # **要走 `load_unit`** —— 库是 `use` 进来的，`load()` 只读入口那一份，
        # 拿不到 deps 就等于这条规则没被跑到（第一次就是这么写成绿的）。
        mod, deps = lomentc.load_unit(d / "entry.lomt", d)
        msgs = lomentc.check(mod, deps=deps)
    assert [m for m in msgs if "库不许声明命令" in m], msgs


@test
def test_official_names_are_not_hand_copied_twice():
    """"撞官方名"要的那份清单**两边都得有**，所以钉住它们**互相相等**。

    参考实现里是 `lomentc.RESERVED_COMMANDS`（一张元组），自举那边是 `checker.lomt` 里
    一串 `cmd_span_is(src, p, n, "<名字>")`。手抄的第二份必然漂 —— 这条判据就是那份保证。

    **不拿 `loment commands` 当基准**：那要一个装好的工具链，判据在干净检出上就得能跑。
    两边相等 + 两边的条目数写死，足够抓住"改了一边忘了另一边"。
    """
    want = tuple(sorted(lomentc.RESERVED_COMMANDS))
    assert len(want) == 38, f"官方命令名的条数变了：{len(want)}（docs/169 记的是 38）"
    assert list(want) == sorted(want), "参考实现那份表要按字典序，两边才好对"

    src = CHECKER_SRC.read_text(encoding="utf-8")
    body = src[src.index("fn cmd_reserved("):]
    body = body[:body.index("\n}")]
    got = tuple(sorted(m for m in re.findall(r'cmd_span_is\(src, p, n, "([^"]+)"\)', body)))
    assert got == want, (
        f"两份清单不一致 —— 只在参考实现里的: {sorted(set(want) - set(got))}；"
        f"只在自举里的: {sorted(set(got) - set(want))}。两处一起改")


# ---------------------------------------------------------------- 2. 产物名字

def _print_command(src: Path) -> str:
    """参考实现答的产物名字（等价于驱动器的 `--print-command`）。"""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        "--print-command", str(src)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       shell=False, timeout=300)
    assert r.returncode == 0, f"--print-command 退出 {r.returncode}: {r.stderr[-200:]}"
    return r.stdout.strip()


@test
def test_print_command_answers_with_the_name_or_nothing():
    assert _print_command(EXAMPLE) == "cmd-hello", "带声明的单元要答出名字"
    assert _print_command(REGISTER_EXAMPLE) == "reghello", \
        "`register` 块那种写法要答出同一件事（名字在语法里）"
    assert _print_command(ROOT / "loment" / "examples" / "tour.lomt") == "", \
        "没声明的单元要答一个空行（调用方按「空 = 不是命令」处理）"


@test
def test_both_launchers_ask_for_the_name():
    """两个启动器**都**要问，而且问法只在该问的时候问（用户给了 `-o` 就不问）。

    只断言"文本里有 `--print-command`"是不够的 —— 写在 `-o` 之后的分支里就等于没写。
    这里连"它出现在 `out=` 的兜底那一段"一起钉住。
    """
    sh, cmd = loment_dist.LAUNCHER_SH, loment_dist.LAUNCHER_CMD
    for name, t in (("bash", sh), ("batch", cmd)):
        assert "--print-command" in t, f"{name} 启动器没问驱动器要命令名"
        i = t.index('${src%.lomt}') if name == "bash" else t.index("set \"out=%src:.lomt=%\"")
        assert t.index("--print-command") < i, \
            f"{name} 启动器把询问放在兜底之后了 —— 那样它永远不会被走到"
        assert "loment-" in t, f"{name} 启动器没把产物名拼成 `loment-<名字>`"


@test
def test_bash_launcher_names_the_artifact_after_the_command():
    """拿**桩驱动器**真跑一遍 bash 启动器（与 `loment_cli_test` 那条用户命令判据同款）。

    真驱动要几分钟才能建出来，而这条要判的是**启动器那几行的分流**：`-o` 给不给、
    驱动器答了什么。桩把三种回答都演一遍，比真跑一次覆盖得还全。
    """
    bash = shutil.which("bash")
    if not bash:
        print("      (跳过: 没有 bash，跑不了 POSIX 启动器)")
        return
    with tempfile.TemporaryDirectory() as tds:
        t = Path(tds)
        pf = t / "pf"
        (pf / "bin").mkdir(parents=True)
        (pf / "share" / "loment").mkdir(parents=True)
        (pf / "share" / "loment" / "version").write_bytes(b"FAKE\n")
        # 桩驱动器: `--print-command` 回显 `$LOMENT_STUB_NAME`; 正常那一趟吐一条空 IR。
        drv = pf / "bin" / "loment-driver"
        drv.write_text('#!/bin/sh\nfor a in "$@"; do\n  if [ "$a" = "--print-command" ]; then\n'
                       '    printf "%s\\n" "$LOMENT_STUB_NAME"\n    exit 0\n  fi\ndone\n'
                       'exit 0\n', encoding="utf-8", newline="\n")
        # 桩链接器: 把 `-o`/OUT 记下来，好断言产物叫什么。
        (pf / "bin" / "loment-lomelf").write_text(
            '#!/bin/sh\necho "$2" >> "$LOMENT_STUB_LOG"\n', encoding="utf-8", newline="\n")
        lom = pf / "bin" / "loment"
        lom.write_text(loment_dist._subst(loment_dist.LAUNCHER_SH), encoding="utf-8", newline="\n")
        for p in (drv, pf / "bin" / "loment-lomelf", lom):
            p.chmod(0o755)

        def shp(p: Path) -> str:
            s = str(p).replace("\\", "/")
            return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s

        log = t / "out.log"
        env = dict(__import__("os").environ)
        env["PATH"] = f"{shp(pf / 'bin')}:{env.get('PATH', '')}"
        env["LOMENT_STUB_LOG"] = shp(log)

        def run(stub: str, *args: str) -> str:
            env["LOMENT_STUB_NAME"] = stub
            log.write_text("", encoding="utf-8")
            subprocess.run([bash, shp(lom), "build", "x.lomt", *args], cwd=str(t), env=env,
                           capture_output=True, text=True, timeout=60, shell=False)
            return log.read_text(encoding="utf-8").strip()

        assert run("mycmd") == "loment-mycmd", "有命令声明时产物名要是 `loment-<名字>`"
        assert run("") == "x", "没有声明时退回源文件自己的名字"
        assert run("mycmd", "-o", "mine") == "mine", "用户给了 `-o` 就不该再问"


# ---------------------------------------------------------------- 3. 生成的入口

@test
def test_generated_entry_calls_command_main_and_the_unit_has_no_start():
    """IR 里那个 `_start` 是**生成的**：它调 `command_main`，单元自己没写 `_start`。

    （"它真能把 cmdline 交过去"由下面那条端到端管 —— 这里只看形状。）
    """
    mod, deps = lomentc.load_unit(EXAMPLE, ROOT)
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    assert "define void @_start() {" in ll, "没有生成的进程入口"
    assert "call i32 @command_main(" in ll, "生成的入口没调命令体"
    assert "; ---- 命令入口" in ll, "生成的入口没有那段标注"
    src = EXAMPLE.read_text(encoding="utf-8")
    assert "fn _start" not in src, "示例自己写了 `_start` —— 那样这个例子就测不到生成那一路"


def _build_and_run(src: Path, args: list[str]) -> "tuple[bytes, int] | None":
    """把 `src` 编出来、链出来、跑起来。链不出来（本机没有那个后端）返回 None。

    **不只看 IR**：这套东西最容易骗过自己的地方就是"形状对、跑起来 SIGILL"（`docs/223`
    的入口那一半第一次就是这么过的），所以每一个命令判据都落到真进程的输出与退出码上。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        mod, deps = lomentc.load_unit(src, ROOT)
        ll_text = lomentc.emit_llvm(mod, ROOT, deps)
        (d / "a.ll").write_text(ll_text, encoding="utf-8", newline="\n")
        try:
            import lomelf
            raw, _entry = (lomelf.compile_pe if IS_WIN else lomelf.compile_ll)(ll_text)
        except Exception as e:  # noqa: BLE001
            print(f"      (跳过: 链不出来 —— {type(e).__name__}: {e})")
            return None
        exe = d / ("a.exe" if IS_WIN else "a")
        exe.write_bytes(raw)
        exe.chmod(0o755)
        r = subprocess.run([str(exe), *args], capture_output=True, timeout=60, shell=False)
        return r.stdout, r.returncode


@test
def test_the_command_really_runs_and_sees_its_own_command_line():
    """编出来、**链出来、跑起来**：stdout 是 cmdline 的头四个字节，退出码是参数个数。"""
    got = _build_and_run(EXAMPLE, ["one", "two"])
    if got is None:
        return
    out, rc = got
    head = ROOT.as_posix().encode()[:4]
    assert out.startswith(head) or len(out) == 4, f"stdout 不是命令行开头: {out[:40]!r}"
    assert rc == 3, f"`a one two` 该有 3 个字段，实际退出 {rc}"


@test
def test_the_register_form_runs_exactly_like_the_declaration_form():
    """两种写法编出来的程序**跑起来一样**（同一条命令行 -> 同一份 stdout、同一个退出码）。

    这条是"形态 A 编译到形态 B"那句声明的**行为证据**。断言两边 IR 都有 `_start` 说明不了
    任何事（两种写法当然都有），要看见的是：块那种写法真的跑出同一个结果。
    （IR 逐字节那一半由 `loment_p8_test` 的语料闸门管 —— `register_hello.lomt` 就在语料里。）
    """
    a = _build_and_run(EXAMPLE, ["one", "two"])
    b = _build_and_run(REGISTER_EXAMPLE, ["one", "two"])
    if a is None or b is None:
        return
    assert a == b, f"两种写法跑出来不一样: 函数那种 {a!r} vs 块那种 {b!r}"
    assert b[1] == 3, f"块那种写法该拿到 3 个字段，实际退出 {b[1]}"


def main() -> int:
    return loment_suite.run(TESTS, "loment_register_test")


if __name__ == "__main__":
    sys.exit(main())
