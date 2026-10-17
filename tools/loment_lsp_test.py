#!/usr/bin/env python3
# loment_lsp_test.py — Loment 版语言服务 (loment/tools/lsp.lomt) 的协议判据
#
# 为什么: 开发者不该为了写 Loment 装 Python。这条测试用**真协议**驱动 Loment 版 LSP
# (经 WSL 跑 Linux ELF): 批量喂入带 Content-Length 分帧的 JSON-RPC, 解析它的输出帧,
# 覆盖 initialize / didOpen 诊断 / didChange / completion / definition / shutdown / exit。
#
# 用**文件重定向**而不是管道: WSL 互操作下管道会卡住 (`wsl -e` 的 stdin 要等 EOF), 喂文件
# 既确定又快。消息序列是预先定好的, 所以批量喂没有损失。
#
# 运行: python tools/loment_lsp_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402
import loment_lsp as PY_LSP  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lsp.lomt"
TESTS: list[tuple[str, object]] = []

GOOD = ("module t\n\n/// 一个辅助函数\nfn helper() -> u32 {\n    return 1;\n}\n\n"
        "fn main() -> u32 {\n    return helper();\n}\n")
BAD = ("module t\n\nfn helper() -> u32 {\n    return 1;\n}\n\n"
       "fn main() -> u32 {\n    return missing_fn();\n}\n")
DUP = ("module t\n\nfn a() -> u32 {\n    return 1;\n}\n\n"
       "fn a() -> u32 {\n    return 2;\n}\n")
#: `register` 块（`docs/223` 形态 A）—— **新语法**，语言服务不认它就会在编得过的源上
#: 报假错（消费方轴 `docs/182` §1.9 那条，与开关/方言是同一个入口缺口）。
REG_SRC = ("module t\n\nregister tcmd {\n"
           "    fn command_main(argv: ptr, argc: u32) -> u32 {\n        return argc;\n    }\n}\n")


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True, text=True,
                              timeout=60, shell=False).returncode == 0
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
    assert not errs, f"lsp.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lsp.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lsp.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    return elf


def frame(msg: dict) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def parse_frames(raw: bytes) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(raw):
        j = raw.find(b"\r\n\r\n", i)
        if j < 0:
            break
        hdr = raw[i:j].decode("ascii", "replace")
        n = 0
        for line in hdr.splitlines():
            if line.lower().startswith("content-length:"):
                n = int(line.split(":", 1)[1].strip())
        body = raw[j + 4:j + 4 + n]
        if len(body) < n:
            break
        out.append(json.loads(body.decode("utf-8")))
        i = j + 4 + n
    return out


class Batch:
    """把一串消息喂给 LSP 二进制, 拿回按序的输出帧。"""

    def __init__(self, elf: Path, td: Path):
        self.elf = elf
        self.td = td

    def run(self, msgs: list[dict]) -> tuple[list[dict], int, str]:
        inp = self.td / "in.bin"
        inp.write_bytes(b"".join(frame(m) for m in msgs))
        for a in (["rm", "-f", f"{_T}loment_lsp.bin"],
                  ["cp", _wsl_path(self.elf), f"{_T}loment_lsp.bin"],
                  ["chmod", "+x", f"{_T}loment_lsp.bin"],
                  ["rm", "-f", f"{_T}loment_lsp_in.bin"],
                  ["cp", _wsl_path(inp), f"{_T}loment_lsp_in.bin"]):
            r = subprocess.run([*_WSLCMD, *a], capture_output=True, shell=False)
            assert r.returncode == 0, (a, r)
        r = subprocess.run(
            [*_WSLCMD, "bash", "-lc",
             f"{_T}loment_lsp.bin < {_T}loment_lsp_in.bin > {_T}loment_lsp_out.bin "
             f"2>{_T}loment_lsp_err.bin; echo -n $?"],
            capture_output=True, text=True, timeout=300, shell=False)
        rc = int(r.stdout.strip() or -1)
        raw = subprocess.run([*_WSLCMD, "cat", f"{_T}loment_lsp_out.bin"],
                             capture_output=True, shell=False).stdout
        err = subprocess.run([*_WSLCMD, "cat", f"{_T}loment_lsp_err.bin"],
                             capture_output=True, shell=False).stdout.decode("utf-8", "replace")
        return parse_frames(raw), rc, err


@test
def test_lsp_protocol_roundtrip():
    """一整套: initialize -> didOpen(好) -> didChange(坏) -> completion -> definition
    -> shutdown -> exit, 逐帧核对。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///t.lomt"
    bline = BAD.splitlines().index("    return missing_fn();")
    hline = GOOD.splitlines().index("fn helper() -> u32 {")   # 引用在 GOOD 文档里查
    cline = GOOD.splitlines().index("    return helper();")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"method": "textDocument/didOpen", "params": {"textDocument":
                                                      {"uri": uri, "text": GOOD}}},
        {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        # 引用处 -> 声明处 (GOOD 文档: 第 8 行调用 helper, 声明在第 2 行)
        {"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri}, "contentChanges": [{"text": BAD}]}},
        # 未声明的词 -> null
        {"jsonrpc": "2.0", "id": 4, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": bline, "character": 12}}},
        {"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        frames, rc, err = Batch(elf, td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    assert len(frames) == 7, f"应有 7 帧, 实得 {len(frames)}"

    init = frames[0]
    caps = init["result"]["capabilities"]
    assert init["id"] == 1 and caps["textDocumentSync"] == 1, init
    assert caps["definitionProvider"] is True, caps
    assert init["result"]["serverInfo"]["name"] == "loment-lsp", init

    d1 = frames[1]
    assert d1["method"] == "textDocument/publishDiagnostics", d1
    assert d1["params"]["uri"] == uri and d1["params"]["diagnostics"] == [], d1

    comp = frames[2]
    items = comp["result"]["items"]
    labels = {it["label"] for it in items}
    kinds = {it["label"]: it["kind"] for it in items}
    assert {"fn", "let", "u32", "helper", "main", "register"} <= labels, sorted(labels)[:24]
    assert kinds["fn"] == 14 and kinds["u32"] == 6, kinds
    assert kinds["helper"] == 3 and kinds["main"] == 3, kinds
    py_items = PY_LSP.handle({"id": 9, "method": "textDocument/completion",
                              "params": {"textDocument": {"uri": uri}}},
                             {uri: GOOD})[0]["result"]["items"]
    py_words = {i["label"] for i in py_items if i["kind"] in (14, 6)}
    assert py_words <= labels, f"与 Python 版的关键字/类型词不一致: {sorted(py_words - labels)}"

    dfn = frames[3]["result"]
    assert dfn is not None and dfn["uri"] == uri, dfn
    assert dfn["range"]["start"]["line"] == hline, (dfn, hline)

    d2 = frames[4]
    dg = d2["params"]["diagnostics"]
    assert any(d["code"] == "E002" and d["range"]["start"]["line"] == bline for d in dg),         (bline, dg)
    assert all(d["severity"] == 1 and d["source"] == "loment" for d in dg), dg
    assert all(d["message"].startswith(d["code"]) for d in dg), dg

    assert frames[5]["result"] is None, frames[5]
    assert frames[6]["result"] is None, frames[6]
    print(f"      8 条消息 -> 7 帧全对 (干净 0 诊断 / 补全含 helper+main / 跳转引用->行 "
          f"{hline} / didChange 后 E002 @{bline} / 未声明词->null / shutdown null)")


@test
def test_lsp_diagnostics_match_selfhosted_checker():
    """诊断的**码与行号**来自自举 checker (与参考实现规则等价 63/63)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    cases = [
        ("dup", DUP, "E013", [i for i, l in enumerate(DUP.splitlines())
                                 if l.startswith("fn a")][-1], 1),   # 报的是**重复**的那个
        ("undeclared", BAD, "E002", BAD.splitlines().index("    return missing_fn();"), 1),
        ("clean", GOOD, None, 0, 0),
        # `register` 块是新语法：语言服务认不出它就会在这份**编得过**的源上报一条
        # "未知顶层关键字"，而那正是消费方轴要抓的那种假错。
        ("register", REG_SRC, None, 0, 0),
    ]
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    for name, text, _c, _l, _n in cases:
        msgs.append({"method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": f"file:///{name}.lomt",
                                                  "text": text}}})
    msgs += [{"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}},
             {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        frames, rc, err = Batch(elf, td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    diags = [f["params"]["diagnostics"] for f in frames[1:1 + len(cases)]]
    assert len(diags) == len(cases), frames
    for (name, _t, want_code, want_line, want_n), dg in zip(cases, diags):
        if want_code is None:
            assert not dg, (name, dg)
        else:
            assert any(d["code"] == want_code and d["range"]["start"]["line"] == want_line
                       for d in dg), (name, want_code, want_line, dg)
            assert len(dg) >= want_n, (name, want_n, dg)
        for d in dg:
            assert d["code"].startswith("E") and len(d["code"]) == 4, d
    print(f"      {len(cases)} 个用例的码/行号正确 (重名 E013 / 未声明 E002 / 干净 0 条)")


@test
def test_lsp_check_mode_is_python_free():
    """`lsp --check FILE` -> `路径:行:列: E0NN 标题`, 退出码 0/1 —— 编辑器任务与 CI 可用,
    且**不需要 Python** (与诊断共用同一套 checker 与文案表)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lsp_check_case.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(DUP + "\nfn m() -> u32 {\n    return nope();\n}\n",
                     encoding="utf-8", newline="\n")
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            for a in (["rm", "-f", f"{_T}loment_lsp.bin"],
                      ["cp", _wsl_path(elf), f"{_T}loment_lsp.bin"],
                      ["chmod", "+x", f"{_T}loment_lsp.bin"]):
                subprocess.run([*_WSLCMD, *a], capture_output=True, shell=False)
            # 把**绝对 WSL 路径**当数据传给 --check (不经 shell, 也不猜仓库在哪) ——
            # 早先这里写死了 `cd /mnt/d/Dev/FujoOS-FujoLang`, 换个检出位置就 rc=2
            # (服务打不开文件), 会被误读成"服务没构建"。
            abs_probe = _wsl_path(probe)
            r = subprocess.run([*_WSLCMD, f"{_T}loment_lsp.bin", "--check", abs_probe],
                               capture_output=True, text=True, timeout=180, shell=False)
            out = r.stdout
            assert r.returncode == 1, f"有错文件应当 rc=1, 实得 {r.returncode}: {out!r} {r.stderr[-200:]!r}"
            lines = [ln for ln in out.splitlines() if ": E" in ln]
            assert any("E013" in ln and ln.startswith(abs_probe) for ln in lines), out
            assert any("E002" in ln for ln in lines), out
            for ln in lines:
                head = ln.split(": E")[0]
                assert head.startswith(abs_probe + ":"), ln
                # 路径之后正好是 `:行:列` (两个冒号)
                assert head[len(abs_probe):].count(":") == 2, ln
            clean = _wsl_path(ROOT / "loment" / "examples" / "mathutil.lomt")
            r2 = subprocess.run([*_WSLCMD, f"{_T}loment_lsp.bin", "--check", clean],
                                capture_output=True, text=True, timeout=180, shell=False)
            assert r2.returncode == 0 and r2.stdout.strip() == "", (r2.returncode, r2.stdout)
    finally:
        probe.unlink(missing_ok=True)
    print("      --check: 有错 -> rc=1 且逐行 `路径:行:列: E0NN 标题`; 干净 -> rc=0 无输出")


#: 开关体里放一个**必然报错**的调用 (E002 未定义函数), 再加上一句 `choose` 就能打开它。
SW_OFF_SRC = ("module t\n\n"
              "set choose verbose {\n    fn banner() -> u32 {\n"
              "        return missing_fn();\n    }\n}\n")
SW_ON_SRC = SW_OFF_SRC + "\nchoose verbose\n"

#: 一份**编得过**的方言源（`docs/184` §9 S4.1）。
#:
#: 宏体吃 4 个 token（`名字` `=` `数` `;`）**什么都不吐** —— `def ANSWER = 1;` 整条消失。
#: 之所以不吐东西：这条判据要的是"**方言区被消费掉了**"，而不是"吐得对不对"
#: （吐得对不对是 `loment_comefor_test` 那条逐字节判据的事）。吐东西要 `ct_syn` + 字符串，
#: 会把这条判据变成一个**第二份展开器测试**，而它想测的是**语言服务这个消费者**。
#:
#: 于是形状是：**不展开 = `def`/`byuse` 被报成未知顶层关键字**（假错）；展开 = 干净。
CF_SRC = ('module t\n\n'
          'comefor let "def" to {\n'
          '    fn main() -> u64 {\n'
          '        return 4;\n'
          '    }\n'
          '}\n\n'
          'def ANSWER = 1;\n\n'
          'fn main() -> u64 { return 1; }\n\n'
          'byuse "def" done\n')


@test
def test_lsp_check_honours_switches():
    """语言服务是"读 L1 源的东西"之一 —— **开关没落定就等于关着的那段照样被检查**。

    本判据钉的是 `docs/182` §1.8 那条"裁减真生效", 但**换一个消费者测**: 同一份源,
    开关关着 -> `--check` 必须**一声不吭** (rc=0); 加一句 `choose verbose` 打开 ->
    必须报出体里那个未定义函数 (rc=1 且含 E002)。

    **这条自带对照, 所以它是可证伪的**: 两个断言跑的是**同一个体**(只差一句 `choose`),
    而"打开 -> E002"证明检查器**确实会**报那个调用。于是"关着 -> rc=0"只可能来自
    **那段真被裁掉了** —— 裁减一旦失效, 两个断言里的第一个立刻红。

    为什么单列一条: `lsp.lomt` 原先拿 `lex` 的原始 token 直接喂 `run_check`/`scan_syms`,
    编译链的判据全绿**也测不到它** —— 语言服务是独立进程、独立入口。这正是
    `docs/182` §1.9 记的那一类"被上游盖住"的缺口。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lsp_switch_case.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)

    def run(src: str) -> subprocess.CompletedProcess:
        probe.write_text(src, encoding="utf-8", newline="\n")
        return subprocess.run([*_WSLCMD, f"{_T}loment_lsp.bin", "--check",
                               _wsl_path(probe)],
                              capture_output=True, text=True, timeout=180, shell=False)

    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            for a in (["rm", "-f", f"{_T}loment_lsp.bin"],
                      ["cp", _wsl_path(elf), f"{_T}loment_lsp.bin"],
                      ["chmod", "+x", f"{_T}loment_lsp.bin"]):
                subprocess.run([*_WSLCMD, *a], capture_output=True, shell=False)
            off = run(SW_OFF_SRC)
            assert off.returncode == 0 and off.stdout.strip() == "", \
                f"关着的开关体不该被检查, 实得 rc={off.returncode}: {off.stdout!r}"
            on = run(SW_ON_SRC)
            assert on.returncode == 1, f"打开后应当报错, 实得 rc={on.returncode}: {on.stdout!r}"
            assert "E002" in on.stdout, f"打开后应当报未定义函数 (E002): {on.stdout!r}"
    finally:
        probe.unlink(missing_ok=True)
    print("      --check: 关着的开关体不报错; `choose` 打开后 E002 照报 (LSP 侧裁减真生效)")


@test
def test_python_lsp_also_honours_switches():
    """**Python 版语言服务是独立的一份实现，同样漏过开关** —— 这条钉它。

    为什么两边都要有：`loment/tools/lsp.lomt` 与 `tools/loment_lsp.py` 是**两个**服务端
    （前者装好后不依赖 Python，后者是 VS Code 扩展的默认后端与 `loment lsp` 的实现）。
    修了自举那份不等于修了这份 —— 实测就是如此。

    **断言按消息内容而不是 `code`**：Python 版**从来不吐 `code` 字段**
    （`_diagnostics` 只给 `range`/`severity`/`source`/`message`），自举版才吐
    `E0NN`。这是**两实现的既有不对齐**，从没被判据碰过 —— 本条不假装它有，
    也不顺手掩盖：记在 `docs/182` §1.9.2 的未修清单里。

    不依赖 clang/WSL：直接调 Python 版的处理函数，所以这条**永远会跑**。
    """
    def diags(text: str) -> list[dict]:
        out = PY_LSP.handle({"id": 1, "method": "textDocument/didOpen",
                             "params": {"textDocument": {"uri": "file:///sw.lomt",
                                                         "text": text}}},
                            {"file:///sw.lomt": text})
        pub = [f for f in out if f.get("method") == "textDocument/publishDiagnostics"]
        assert pub, f"didOpen 应当回一条 publishDiagnostics: {out}"
        return pub[0]["params"]["diagnostics"]

    off = diags(SW_OFF_SRC)
    assert off == [], f"关着的开关体不该被检查, 实得 {off}"
    on = diags(SW_ON_SRC)
    assert any("missing_fn" in d["message"] for d in on), f"打开后应报未定义函数: {on}"
    print("      Python 版语言服务: 关着不报错 / 打开报未定义函数 (与自举版同行为)")


@test
def test_python_lsp_honours_comefor():
    """语言服务也要展开方言 —— 否则**编得过的源在编辑器里报假错**（`docs/184` §9 S4.1）。

    与 `test_python_lsp_also_honours_switches` 是**同一个入口缺口的两半**
    （`docs/182` §1.9 的消费方轴）：`tools/loment_lsp.py` 绕过了 `lomentc.load`，
    只补开关不补方言的话，`comefor`/`byuse`/`def` 会被逐条报成未知顶层关键字
    —— 而 `lomentc` 编同一份文件是**过**的。**"编得过但编辑器报错"比两边都报错更糟。**

    **用判据自己带的那份源**（`CF_SRC`），不读 `loment/comefor/*.lomt` —— 那样判据
    才自足：它同时**断言这份源真的编得过**，否则"没假错"可能只是因为本来就该报错。
    """
    def diags(text: str) -> list[dict]:
        out = PY_LSP.handle({"id": 1, "method": "textDocument/didOpen",
                             "params": {"textDocument": {"uri": "file:///cf.lomt",
                                                         "text": text}}},
                            {"file:///cf.lomt": text})
        pub = [f for f in out if f.get("method") == "textDocument/publishDiagnostics"]
        assert pub, f"didOpen 应当回一条 publishDiagnostics: {out}"
        return pub[0]["params"]["diagnostics"]

    # **前提**：这份源真的编得过（拿编译器自己验，不是拿语言服务验）。另外断言
    # `def ANSWER = 1;` 那一段**真的被消费掉了** —— 不然"没报假错"可能只是因为
    # 语言服务**根本没看**(比如把整份源吞了)。
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cf.lomt"
        p.write_text(CF_SRC, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)          # 不抛 = 编译器认这份方言源
        assert [f.name for f in mod.funcs] == ["main"], \
            f"前提不成立：`def …` 那一段应当被消费掉, 实得函数表 {[f.name for f in mod.funcs]}"
        assert not mod.consts, f"前提不成立：宏体什么都不吐, 实得常量 {mod.consts}"
    got = diags(CF_SRC)
    assert got == [], f"编得过的方言源不该在编辑器里报假错, 实得 {got}"
    print("      语言服务: 方言源**编得过**且不报假错")


@test
def test_python_lsp_accepts_the_register_form():
    """`register` 块是**新语法**（`docs/223` 形态 A）：语言服务不认它，就会在一份
    **编得过**的源上报一条假错 —— "编得过但编辑器报错"比两边都报错更糟
    （消费方轴，`docs/182` §1.9；与开关/方言那两条同一个入口缺口）。

    **前提先拿编译器验**（这份源真的零诊断），否则"没报错"可能只是因为它本来就该报错。
    """
    def diags(text: str) -> list[dict]:
        out = PY_LSP.handle({"id": 1, "method": "textDocument/didOpen",
                             "params": {"textDocument": {"uri": "file:///reg.lomt",
                                                         "text": text}}},
                            {"file:///reg.lomt": text})
        pub = [f for f in out if f.get("method") == "textDocument/publishDiagnostics"]
        assert pub, f"didOpen 应当回一条 publishDiagnostics: {out}"
        return pub[0]["params"]["diagnostics"]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "reg.lomt"
        p.write_text(REG_SRC, encoding="utf-8", newline="\n")
        errs = lomentc.check(lomentc.load(p))
        assert errs == [], f"前提不成立：这份源应当零诊断, 实得 {errs}"
    got = diags(REG_SRC)
    assert got == [], f"编得过的 `register` 源不该在编辑器里报假错, 实得 {got}"
    print("      Python 版语言服务: `register` 块不报假错")


#: 一份带 `choose write grammar` 声明的方言源（issue #142）—— 内容并不重要，
#: 光是这行声明就够让 `_parse` 在 `lomentc.Parser` 里炸开（它只认原生语法）。
GRAMMAR_SRC = "choose write grammar c\n\nint main() {\n    return 1;\n}\n"


@test
def test_python_lsp_definition_survives_grammar_declaration():
    """`textDocument/definition` 撞上带 `choose write grammar` 声明的文件时不该让
    语言服务整个炸掉（issue #142）—— `didOpen` 会把同一个 `LomError` 接住变成诊断,
    但 `handle()` 里 `textDocument/definition` 分支直接调 `_decls` -> `_parse`,
    没有 `try`, 于是 `LomError` 会一路抛出 handler, 在真实 serve 循环里就是服务器退出。
    """
    uri = "file:///grammar.lomt"
    docs = {uri: GRAMMAR_SRC}
    out = PY_LSP.handle({"jsonrpc": "2.0", "id": 1, "method": "textDocument/definition",
                         "params": {"textDocument": {"uri": uri},
                                    "position": {"line": 2, "character": 4}}},
                        docs)
    assert out and out[0].get("result") is None, out
    print("      带 grammar 声明的文件: definition 请求返回 null 而不是抛出 LomError")


#: `#27` 的探针源: 一个未知类型（E030）在**已知的一列**上, 一个名字在**已知的一列**上。
RANGE_SRC = ("module t\n"
             "\n"
             "struct Point {\n"
             "    x: u32,\n"
             "    y: u32,\n"
             "}\n"
             "\n"
             "fn area(p: Point) -> u32 {\n"
             "    return p.x + p.y;\n"
             "}\n"
             "\n"
             "fn _start() {\n"
             "    let w: f64 = 0;\n"
             "    syscall4(60, 0, 0, 0);\n"
             "}\n")


def _want_span(word: str, line0: int) -> dict:
    """从源文本里算出 `word` 在 `line0` 上的跨度 —— 判据**不写死列号**, 免得改源就过期。"""
    col = RANGE_SRC.splitlines()[line0].index(word)
    return {"start": {"line": line0, "character": col},
            "end": {"line": line0, "character": col + len(word)}}


@test
def test_lsp_ranges_name_the_token_and_not_the_line():
    """`#27`：range 要指在**那个符号**上，不是常量 `0 → 200`。

    原先每一条 range 的两端都是常量列 `0` 与 `200`：一条 14 字符的行与一条 300 字符的行
    回来的是同一对数字，编辑器于是把**整行**画成波浪线，指不到出问题的那一格。`definition`
    同理，回的是 `{line, 0} → {line, 200}`，光标只能落到行首。

    判据不满足于"不再等于 200"：它把**期望的跨度**从源文本里算出来（那个 token 在哪一列、
    多长），要求逐字相等。顺手也钉住了 `#323` 的新码在语言服务这一侧是通的。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///range.lomt"
    dl = RANGE_SRC.splitlines().index("    let w: f64 = 0;")
    fl = RANGE_SRC.splitlines().index("fn area(p: Point) -> u32 {")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": RANGE_SRC}}},
            {"jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
             "params": {"textDocument": {"uri": uri},
                        "position": {"line": fl,
                                     "character": RANGE_SRC.splitlines()[fl].index("Point") + 1}}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    dg = frames[1]["params"]["diagnostics"]
    want = _want_span("f64", dl)
    assert any(d["range"] == want for d in dg), (want, dg)
    got = frames[2]["result"]
    assert got is not None, frames[2]
    assert got["range"] == _want_span("Point", 2), (got, _want_span("Point", 2))
    print(f"      诊断 range == `f64` 的跨度 {want['start']['character']}.."
          f"{want['end']['character']}；definition == 声明里那个名字的跨度")


@test
def test_python_lsp_ranges_are_not_placeholders_either():
    """同一件事的 **Python 参考实现**那一侧（`tools/loment_lsp.py`）—— 与上面那条成对。

    两边能给的精度**不一样**，判据也据此分开，不假装它们一样：

    * **解析期**那一支有真列（`lomc.LomError` 带 `col`），所以要求它给**精确**跨度；
    * **检查期**的消息只带**行**（`12: …`，`#5` 记的就是"列从来没算过"），这一层拿不到列 ——
      于是要求它给**那一行第一个 token** 的跨度：落在对的那一行上，且**不再是**常量。

    `definition` 两边都能给准：行与名字都是已知的，只是要把它找出来。
    """
    uri = "file:///range.lomt"
    docs = {uri: RANGE_SRC}
    dg = PY_LSP.handle({"method": "textDocument/didOpen",
                        "params": {"textDocument": {"uri": uri, "text": RANGE_SRC}}},
                       docs)[0]["params"]["diagnostics"]
    dl = RANGE_SRC.splitlines().index("    let w: f64 = 0;")
    first = RANGE_SRC.splitlines()[dl].index("let")
    assert any(d["range"] == {"start": {"line": dl, "character": first},
                              "end": {"line": dl, "character": first + 3}} for d in dg), dg
    # 解析期那一支：列要**精确**到词法器的 `col`，不是 0。
    bad = "module t\n\nfn f() -> u32 {\n    let a: u32 = 1 @ 2;\n    return a;\n}\n"
    perr = PY_LSP.handle({"method": "textDocument/didOpen",
                          "params": {"textDocument": {"uri": "file:///bad.lomt", "text": bad}}},
                         {"file:///bad.lomt": bad})[0]["params"]["diagnostics"]
    assert perr and perr[0]["range"]["start"]["character"] > 0, perr
    fl = RANGE_SRC.splitlines().index("fn area(p: Point) -> u32 {")
    got = PY_LSP.handle({"jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
                         "params": {"textDocument": {"uri": uri},
                                    "position": {"line": fl,
                                                 "character":
                                                     RANGE_SRC.splitlines()[fl].index("Point")
                                                     + 1}}},
                        docs)[0]["result"]
    assert got is not None and got["range"] == _want_span("Point", 2), got
    print("      检查期落在对的**那一行**上；解析期给到**真列**；definition 给到名字的跨度")


def main() -> int:
    return loment_suite.run(TESTS, "loment_lsp_test")


#: `#262` 的探针源：一份**编得过**的 python 语法文件（前门会把它翻成 Loment）。
FOREIGN_SRC = ("choose write grammar python\n"
               "\n"
               "module t\n"
               "\n"
               "def f(v):\n"
               "    return v + 1\n")


#: `#28` 的探针源：光标停在 `q.` 后面（`Point` 的两个字段应该回来）。
MEMBER_SRC = ("module demo\n"
              "\n"
              "struct Point {\n"
              "    x: u32,\n"
              "    y: u32,\n"
              "}\n"
              "\n"
              "fn area(p: Point) -> u32 {\n"
              "    return p.x + p.y;\n"
              "}\n"
              "\n"
              "fn _start() {\n"
              "    let q: Point = Point { x: 1, y: 2 };\n"
              "    let v: u32 = q.\n"
              "    syscall4(60, 0, 0, 0);\n"
              "}\n")


def _member_pos() -> tuple[int, int]:
    """`q.` 之后那个位置（0 起的行列）。"""
    lines = MEMBER_SRC.splitlines()
    ln = lines.index("    let v: u32 = q.")
    return ln, lines[ln].index("q.") + 2


@test
def test_completion_after_a_dot_lists_the_struct_fields():
    """`#28`：`q.` 之后回来的是 `Point` 的**字段**，不是一屏关键字。

    这条 issue 的另一半（"广告了 `.` / `:` 触发字符却什么都不给"）在更早的一次改动里已经
    修掉 —— 触发字符不再广告。剩下的正题是**补全本身不看位置**，所以这一条量的是"给了什么"：
    两个语言服务各判一次，字段集合必须**正好**是 `{x, y}`。
    """
    uri = "file:///member.lomt"
    ln, ch = _member_pos()
    req = {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
           "params": {"textDocument": {"uri": uri},
                      "position": {"line": ln, "character": ch}}}
    got = PY_LSP.handle(req, {uri: MEMBER_SRC})[0]["result"]["items"]
    assert {i["label"] for i in got} == {"x", "y"}, got
    if not (_clang() and _wsl()):
        print("      自举侧 SKIP: 无 clang/WSL（Python 侧已判）")
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": MEMBER_SRC}}},
            dict(req),
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    items = frames[2]["result"]["items"]
    assert {i["label"] for i in items} == {"x", "y"}, items
    print("      两个语言服务：`q.` 之后正好回来 Point 的 {x, y}")


@test
def test_a_foreign_grammar_file_gets_one_info_and_no_fake_errors():
    """`#262`：`choose write grammar python` 的源**编得过**，所以拿 Loment 的规则去查它，
    报出来的全是**假错** —— 用户会去改一份本来就对的源。

    两个语言服务各判一条，要求一样：**一条 Info（severity 3），一条 Error（severity 1）都没有**。
    判"没有 Error"比判"有几条"更要紧 —— 假错正是这条 issue 说的东西。
    """
    uri = "file:///foreign.lomt"
    pdg = PY_LSP.handle({"method": "textDocument/didOpen",
                         "params": {"textDocument": {"uri": uri, "text": FOREIGN_SRC}}},
                        {uri: FOREIGN_SRC})[0]["params"]["diagnostics"]
    assert pdg and all(d["severity"] == 3 for d in pdg), pdg
    if not (_clang() and _wsl()):
        print("      自举侧 SKIP: 无 clang/WSL（Python 侧已判）")
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": FOREIGN_SRC}}},
            {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    dg = frames[1]["params"]["diagnostics"]
    assert dg and all(d["severity"] == 3 for d in dg), dg
    print("      两个语言服务：假错 0 条，各一条 Info 说明这份源是 python 语法")


if __name__ == "__main__":
    sys.exit(main())
