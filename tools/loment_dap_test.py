#!/usr/bin/env python3
"""loment_dap_test.py — **调试器真的能调试**（`docs/190`）。

## 它钉的是什么

不是"协议格式对" —— 那是没用的判据。它钉的是：

  * **断点真的停在那一行**，而且是停在该语句**执行之前**：`setBreakpoints(22)` 之后
    拿到的 `stackTrace` 里 `line` 必须是 22，且那一刻 `write_str` 还没打出一个字。
    **只断言"收到了 stopped 事件"等于什么都没测** —— 一个永远停在 `_start` 的假调试器
    也能通过；而一个把断点钉在语句**下一条**上的实现，用户看到的永远是"这行做完了"。
  * **程序真的跑了**：`_start` 里那句 `write_str` 打出来的 `M67 RESULT: PASS` 要以
    DAP 的 `output` 事件到得了客户端（不然"调试"看到的是一段没输出的死程序）。
  * **单步按行走、`next` 跨过 call**：第 22 行编译成四条指令，只单步一条行号不变；
    而 `next` 若"在别的源行上停下"，被调函数的第一行也算别的源行 —— 于是 `next` 与
    `stepIn` 变成一回事。这两条各自都是**真的会坏**的。
  * **落不上的断点要说**：行表里没有的那一行必须 `verified: false` ——
    一个"绿点但不生效"的断点比没有更坏（`docs/167` 那条"静默是敌人"）。

对照的是**真实的一次会话**：起 `wsl -e python3 tools/loment_dap.py`，按 DAP 走完
`initialize -> launch -> setBreakpoints -> configurationDone -> stopped -> stackTrace
-> continue/stepIn/next -> terminated`。

用法: python tools/loment_dap_test.py    （无 WSL / 无 clang 时 SKIP，退出码 0）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
import loment_suite

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "loment" / "examples" / "user_hello.lomt"

#: 断点设在哪一行 —— `_start` 里那句 `write_str(1, msg);`（见那个例子的源）。
#: **这个数要和源对上**，对不上判据就变成"随便哪一行都行"。
#:
#: 选它而不是下一行的 `exit(0)` 是有意的：`write_str` **还没执行**，所以"停在断点时
#: 输出尚未出现"本身又是一条判据 —— 断点落在语句**之前**，不是之后。
BP_LINE = 22

#: `_start` 里那句 `write_str` 打出来的东西（`loment/examples/user_hello.lomt`）。
MARKER = "M67 RESULT: PASS"

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


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


def _clang_on_windows() -> bool:
    return any(Path(c).is_file() for c in (
        r"C:\Program Files\LLVM\bin\clang.exe",
        r"C:\Program Files (x86)\LLVM\bin\clang.exe"))


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


class Client:
    """一个小 DAP 客户端 —— **够用来跑一次真会话**，多一行都不写。"""

    def __init__(self, argv: list[str]) -> None:
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        self.seq = 0
        self.events: list[dict] = []
        #: 已经交出去过的事件数。**没有它 `wait_event` 每次都从头找** ——
        #: 第二次等的 `stopped` 会拿到第一次那个，于是"单步之后行号变了吗"永远比的是同一个。
        self._seen = 0

    def send(self, command: str, arguments: dict | None = None) -> int:
        self.seq += 1
        body = json.dumps({"type": "request", "seq": self.seq, "command": command,
                           "arguments": arguments or {}}).encode("utf-8")
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.p.stdin.flush()
        return self.seq

    def _read(self) -> dict:
        n = None
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("适配器把管道关了: "
                                   + self.p.stderr.read().decode("utf-8", "replace")[-400:])
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1])
        return json.loads(self.p.stdout.read(n).decode("utf-8"))

    def wait_response(self, request_seq: int, timeout: float = 180.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            m = self._read()
            if m.get("type") == "event":
                self.events.append(m)
                continue
            if m.get("type") == "response" and m.get("request_seq") == request_seq:
                return m
        raise TimeoutError(f"等 response({request_seq}) 超时")

    def wait_event(self, name: str, timeout: float = 180.0) -> dict:
        """等下**一次** `name` 事件 —— 交出去过的就不再交（见 `_seen`）。"""
        def take() -> dict | None:
            for i in range(self._seen, len(self.events)):
                if self.events[i].get("event") == name:
                    self._seen = i + 1
                    return self.events[i]
            return None

        got = take()
        if got is not None:
            return got
        end = time.time() + timeout
        while time.time() < end:
            m = self._read()
            if m.get("type") == "event":
                self.events.append(m)
                got = take()
                if got is not None:
                    return got
        raise TimeoutError(f"等 event({name}) 超时；收到 {[e.get('event') for e in self.events]}")

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=30)
        except Exception:  # noqa: BLE001
            self.p.kill()


def _session(program: Path, bp_line: int, breakpoints: list[int] | None = None):
    """跑一次会话（到 `configurationDone` 应答为止），返回 `(客户端, 断点应答体)`。"""
    c = Client([*_WSLCMD, "python3", _wsl_path(ROOT / "tools" / "loment_dap.py")])
    r = c.send("initialize", {"adapterID": "loment", "linesStartAt1": True,
                              "columnsStartAt1": True})
    assert c.wait_response(r)["success"], "initialize 失败"
    c.wait_event("initialized")
    r = c.send("launch", {"program": str(program)})
    resp = c.wait_response(r)
    assert resp["success"], f"launch 失败: {resp.get('message')}"
    lines = breakpoints if breakpoints is not None else [bp_line]
    r = c.send("setBreakpoints", {"source": {"path": str(program)},
                                 "breakpoints": [{"line": l} for l in lines]})
    bps = c.wait_response(r)
    assert bps["success"], bps
    r = c.send("configurationDone")
    c.wait_response(r)
    return c, bps["body"]["breakpoints"]


@test
def test_breakpoint_stops_on_the_right_line():
    """**断点停在那一行，而且是在那条语句"执行之前"停的。**

    三件事一起钉：

      1. `stackTrace` 的 `line` 必须是设的那一行（不然是个假调试器）；
      2. 停住的那一刻，`write_str` 打的字**还没出现** —— 这条把"停在语句之前"和
         "停在语句之后"分开了。只测 1 的话，一个把断点钉在语句**下一条**上的实现
         照样绿，而那种调试器看到的永远是"这行已经做完了"；
      3. 继续之后那段字**要出现**（不然程序根本没跑）。
    """
    if not _wsl() or not _clang_on_windows():
        print("      SKIP: 需要 WSL + Windows 侧 clang")
        return
    c, bps = _session(EXAMPLE, BP_LINE)
    try:
        assert bps and bps[0]["verified"] is True, f"断点没落上: {bps}"
        stopped = c.wait_event("stopped")
        assert stopped["body"]["reason"] == "breakpoint", stopped
        r = c.send("stackTrace", {"threadId": 1})
        frames = c.wait_response(r)["body"]["stackFrames"]
        assert frames, "stackTrace 是空的"
        assert frames[0]["line"] == BP_LINE, \
            f"停在第 {frames[0]['line']} 行，设的是第 {BP_LINE} 行 —— 断点位置不对"
        assert frames[0]["source"]["path"].replace("\\", "/").endswith("user_hello.lomt"), frames
        # ② 停在这条语句**之前** —— 它要打的那段字此刻还不该出现
        before = "".join(e["body"].get("output", "") for e in c.events
                         if e.get("event") == "output")
        assert MARKER not in before, \
            f"断点设在第 {BP_LINE} 行，但那一行的语句已经执行完了（输出先到了）: {before!r}"
        # ③ 继续到结束，那段字这才该到
        c.send("continue")
        c.wait_event("terminated")
        text = "".join(e["body"].get("output", "") for e in c.events
                       if e.get("event") == "output")
        assert MARKER in text, f"程序没跑出预期的输出: {text!r}"
        print(f"      断点停在第 {BP_LINE} 行（对得上源，且停在该语句之前）；"
              f"继续后输出 {text.strip()!r}")
    finally:
        c.close()


@test
def test_step_in_moves_to_another_line():
    """**单步真的会动。** 停在断点之后 `stepIn`，拿到的行号应当与断点行**不同**。

    只断言"收到了 stopped"是不够的 —— 一个原地不动的实现也能发事件。
    """
    if not _wsl() or not _clang_on_windows():
        print("      SKIP: 需要 WSL + Windows 侧 clang")
        return
    c, _ = _session(EXAMPLE, BP_LINE)
    try:
        c.wait_event("stopped")
        c.send("stepIn")
        c.wait_event("stopped")
        got = [e for e in c.events if e.get("event") == "stopped"][-1]
        assert got["body"]["reason"] == "step", got
        r = c.send("stackTrace", {"threadId": 1})
        line = c.wait_response(r)["body"]["stackFrames"][0]["line"]
        assert line != BP_LINE, f"单步之后还停在第 {BP_LINE} 行 —— 没动"
        print(f"      单步: {BP_LINE} -> {line}")
    finally:
        c.close()


@test
def test_next_crosses_a_call():
    """**`next` 跨过 call，`stepIn` 钻进去** —— 两者必须是**不同的行**。

    第 22 行是 `write_str(1, msg);`。按 `next` 应当停在**下一句**（第 23 行 `exit(0);`，
    即 `write_str` 返回之后）；按 `stepIn` 应当钻进 `write_str` 的函数体（第 13 行）。

    **两个都给 13 就是坏的**：那说明 `next` 只做到"在别的源行上停下"，而被调函数的
    第一行也是"别的源行" —— 于是 `next` 和 `stepIn` 变成一回事，用户按 `next` 想跨过
    一句无关的调用，却每次都掉进库函数里。实测过：改之前就是这么坏的。
    """
    if not _wsl() or not _clang_on_windows():
        print("      SKIP: 需要 WSL + Windows 侧 clang")
        return
    lines = {}
    for cmd in ("stepIn", "next"):
        c, _ = _session(EXAMPLE, BP_LINE)
        try:
            c.wait_event("stopped")
            c.send(cmd)
            c.wait_event("stopped")
            r = c.send("stackTrace", {"threadId": 1})
            frames = c.wait_response(r)["body"]["stackFrames"]
            lines[cmd] = (frames[0]["line"], frames[0]["name"])
        finally:
            c.close()
    assert lines["stepIn"][0] != BP_LINE and lines["next"][0] != BP_LINE, \
        f"两个都没动: {lines}"
    assert lines["stepIn"][0] != lines["next"][0], \
        f"`next` 与 `stepIn` 停在同一行（{lines['stepIn'][0]}）—— `next` 没跨过那条 call"
    assert lines["next"][0] == BP_LINE + 1, \
        f"`next` 应当停在下一句（第 {BP_LINE + 1} 行），停在了 {lines['next'][0]}"
    print(f"      stepIn -> {lines['stepIn'][0]} ({lines['stepIn'][1]})，"
          f"next -> {lines['next'][0]} ({lines['next'][1]})")


@test
def test_unplaceable_breakpoint_is_reported():
    """**落不上的断点要说出来**（`verified: false` + 一句原因）。

    一个"绿点但不生效"的断点比没有更坏 —— 用户会以为程序没走到那里。
    """
    if not _wsl() or not _clang_on_windows():
        print("      SKIP: 需要 WSL + Windows 侧 clang")
        return
    # 第 7 行是注释（源里那段说明），行表里不可能有它
    c, bps = _session(EXAMPLE, BP_LINE, breakpoints=[BP_LINE, 7])
    try:
        assert bps[0]["verified"] is True, bps
        assert bps[1]["verified"] is False, f"第 7 行是注释，不该落上: {bps[1]}"
        assert bps[1].get("message"), f"落不上要说原因: {bps[1]}"
        print(f"      第 7 行（注释）落不上，且说了原因: {bps[1]['message'][:60]}")
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "loment_dap_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main())
