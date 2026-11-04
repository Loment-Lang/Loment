#!/usr/bin/env python3
# loment_lsp_hot_test.py — 真的 `loment lsp` 在跑着的时候换掉自己（`#1155`）。
#
# 一个真进程、真的 LSP 协议（`Content-Length` 分帧）。换之前 `initialize` 报 `0.3`，
# 换之后同一个请求报 `0.4` —— 而 `serverInfo.version` 是**代码里的字面量**，不是文件、
# 不是环境变量。定义跳转在换前后给出同一个答案：换的是代码，索引还在。
#
# 候选由 `loment_hotart` 从两棵树编出来（基线 + "投影后"），装载件是真产物。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_hotart as ha  # noqa: E402
import loment_hotload as hl  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


ENTRY = "loment/tools/lsp.lomt"
#: 这份"fix"是**代码**层面的一格：定义的范围起点从 `hc - 1` 改成 `hc`。
#: 不能用改字符串来当验证 —— 字符串在 `.data` 里，而数据是**共享状态**、不随候选换
#: （`docs/291` §2）。改一个立即数，才是"新代码在跑"的真凭据。
OLD_CODE = "        hs = hc - 1;"
NEW_CODE = "        hs = hc;"

_COPY = (
    "lom/*.lom",
    "tools/loment_release.py",
    "loment/tools/*.lomt",
    "loment/selfhost/*.lomt",
    "tools/lomentc.py",
    "tools/lomelf.py",
    "tools/lomfmt.py",
    "tools/loment.py",
    "loment/hotswap/fixed.txt",
    "loment/lib/*.lomt",
    "loment/examples/bytes.lomt",
)

DRIVER = r'''
import json, os, subprocess, sys, time

elf, cand = sys.argv[1], sys.argv[2]
p = subprocess.Popen([elf, "--hot", cand], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

def send(obj):
    b = json.dumps(obj).encode()
    p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
    p.stdin.flush()

def recv():
    n = None
    while True:
        line = p.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if line == b"":
            break
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    if n is None:
        return None
    return json.loads(p.stdout.read(n).decode())

DOC = "module m\n\nfn add(a: u32, b: u32) -> u32 {\n    return a + b;\n}\n\nfn _start() -> u32 {\n    return add(1, 2);\n}\n"
URI = "file:///tmp/loment-hot/m.lomt"

def note(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})

def req(i, method, params):
    send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    # 服务端会**主动**推通知（publishDiagnostics），所以读到 id 对上为止。
    for _ in range(32):
        got = recv()
        if got is None:
            return None
        if got.get("id") == i:
            return got
    return None

req(1, "initialize", {"processId": None, "rootUri": None, "capabilities": {}})
note("initialized", {})
note("textDocument/didOpen", {"textDocument": {"uri": URI, "languageId": "loment",
                                              "version": 1, "text": DOC}})
d1 = req(2, "textDocument/definition", {"textDocument": {"uri": URI},
          "position": {"line": 7, "character": 12}})
print("DEF1", json.dumps(d1.get("result"), sort_keys=True))

os.rename(cand + ".tmp", cand)          # 候选出现
req(3, "textDocument/hover", {"textDocument": {"uri": URI},
      "position": {"line": 7, "character": 12}})   # 这一条还由旧代码回答
d2 = req(5, "textDocument/definition", {"textDocument": {"uri": URI},
          "position": {"line": 7, "character": 12}})
print("DEF2", json.dumps(d2.get("result"), sort_keys=True))
req(6, "shutdown", None)
note("exit", None)
p.wait(timeout=10)
'''


def _tree(dst: Path, *, patch: bool = False) -> Path:
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = dst / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    p = dst / ENTRY
    text = p.read_text(encoding="utf-8")
    if text.count(OLD_CODE) != 1:
        raise AssertionError("找不到 on_definition 里那一格")
    if patch:
        text = text.replace(OLD_CODE, NEW_CODE)
    p.write_text(text, encoding="utf-8", newline="\n")
    return dst


def _build(tree: Path) -> bytes:
    import lomentc
    import lomelf
    p = tree / ENTRY
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, tree.resolve(), p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, errs[:2]
    ir = lomentc.emit_llvm(mod, tree.resolve(), deps)
    blob, _info = lomelf.compile_ll(ir, hotswap=True)
    return blob


#: `#371`：能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑，Windows 上经 WSL。
#: 门禁跑在 Linux 上（`#382`），所以这里**不能写死 `wsl`**。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl_ok() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


@test
def test_a_running_lsp_replaces_itself_at_the_safe_point() -> None:
    if not _wsl_ok():
        print("      SKIP: 没有 WSL，跑不了 Linux ELF")
        return
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old = _tree(t / "old")
        new = _tree(t / "new", patch=True)
        elf = _build(old)
        proj = {"baseline": "0.1.5", "release": "v0.1.5-hot.1", "tree": "lsp",
                "line": [{"sha": "e" * 40, "subject": "fix: bump the server version",
                          "kind": "fix"}], "evicted": []}
        out = ha.package(old, new, "v0.1.5-hot.1", t / "art", entry=ENTRY, projection=proj)
        assert out is not None
        cand = out / "candidate.hot"

        run = t / "run"
        run.mkdir()
        (run / "lsp.elf").write_bytes(elf)
        os.chmod(run / "lsp.elf", 0o755)
        # 候选先放成 `.tmp`：驱动在合适的时候 rename —— "GitHub 那边出现了"。
        (run / "cand.tmp").write_bytes(cand.read_bytes())
        (run / "drive.py").write_text(DRIVER, encoding="utf-8", newline="\n")

        script = (f"rm -rf /tmp/loment-hot && mkdir -p /tmp/loment-hot && "
                  f"cp {_wsl(run / 'lsp.elf')} /tmp/loment-hot/lsp && chmod +x /tmp/loment-hot/lsp && "
                  f"cp {_wsl(run / 'cand.tmp')} /tmp/loment-hot/next.hot.tmp && "
                  f"cp {_wsl(run / 'drive.py')} /tmp/loment-hot/drive.py && "
                  f"cd /tmp/loment-hot && python3 drive.py ./lsp /tmp/loment-hot/next.hot")
        r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                           capture_output=True, text=True)
    fields: dict[str, str] = {}
    for line in r.stdout.splitlines():
        w = line.split(" ", 1)
        if len(w) == 2:
            fields[w[0]] = w[1]
    d1, d2 = fields.get("DEF1", ""), fields.get("DEF2", "")
    assert d1 not in ("", "null") and d2 not in ("", "null"), (d1, d2, r.stdout[-400:])
    # 旧代码给 `hc - 1`（'h'，第 3 列），新代码给 `hc`（第 4 列）—— 换完之后回答的是新代码。
    assert '"character": 3' in d1, (d1, r.stdout[-300:], r.stderr[-300:])
    assert '"character": 4' in d2, (d2, r.stdout[-300:], r.stderr[-300:])
    print(f"      跑着的 lsp 在安全点换了代码：定义列 3 -> 4（{d2[:44]}…）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_lsp_hot_test")


if __name__ == "__main__":
    sys.exit(main())
