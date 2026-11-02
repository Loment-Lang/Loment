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
#: `lspe pass`（`docs/262` §5.16 / `#786`）—— 语言服务也走同一趟 token 拆平。不认它
#: 就会在**编得过**的源上喷 E14/E2/E27（消费方轴，与 `register` 那格同一个缺口）。
PASS_SRC = ("module t\n\nfn main() -> u32 {\n"
            "    lspe pass \"manual bound check\" {\n        let x: u32 = 3;\n    }\n"
            "    return x;\n}\n")
PASS_BAD = PASS_SRC.replace('"manual bound check"', '"   "')


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
def test_position_encoding_is_negotiated():
    """`#1171`: 编下列编码**按客户端声明协商**，缺 utf-8 时回 utf-16 并换算。

    服务端原先无条件回 `positionEncoding: "utf-8"`（`#707`）。按 LSP 3.17，服务端只能
    从客户端的 `general.positionEncodings` 里挑；`vscode-languageclient` 10.1.1 写死
    `['utf-16']` 并对别的编码**直接抛错**，于是每个 VS Code 都连不上
    （`Unsupported position encoding (utf-8) received from server`）。

    这条同时钉两件事：**声明**要对，**列**要跟着换算。列用一行带 CJK 的源 —— UTF-8 字节列
    与 UTF-16 code unit 在那行上不等，选错了这一格就对不上。
    """
    src = ('module m\n\nfn f() -> u32 {\n    return 1;\n}\n\n'
           'fn g() -> u32 {\n    let s: str = "中文"; return nope;\n}\n')
    line = src.splitlines()[7]
    byte_col = len(line.encode("utf-8")[:line.encode("utf-8").index(b"nope")])
    u16_col = len(line[:line.index("nope")].encode("utf-16-le")) // 2
    assert byte_col != u16_col, "夹具没让两种编码分开，这条判据就是空的"

    cases = ((["utf-16"], "utf-16", u16_col),
             (["utf-8", "utf-16"], "utf-8", byte_col),
             (None, "utf-16", u16_col))

    # ---- Python 那一侧（同一份规矩的第二份实现）
    # 声明要一致；**列**在这一侧用纯函数钉：参考实现的 checker 只报行，诊断的列是
    # "这一行的第一个 token"（`_line_span`），CJK 之后的位置在诊断上没有体现 ——
    # 那条路由 `test_lsp_declares_utf8_positions_and_honours_them` 的补全/跳转覆盖。
    try:
        for declared, want_enc, _ in cases:
            caps = {} if declared is None else {"general": {"positionEncodings": declared}}
            out = PY_LSP.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"capabilities": caps}}, {})
            got = out[0]["result"]["capabilities"].get("positionEncoding")
            assert got == want_enc, (declared, got)
        idx = line.index("nope")
        PY_LSP._POSITION_ENCODING = "utf-16"
        assert PY_LSP._index_to_col(line, idx) == u16_col, "utf-16 列换算"
        assert PY_LSP._col_to_index(line, u16_col) == idx, "utf-16 列反算"
        PY_LSP._POSITION_ENCODING = "utf-8"
        assert PY_LSP._index_to_col(line, idx) == byte_col, "utf-8 列换算"
        assert PY_LSP._col_to_index(line, byte_col) == idx, "utf-8 列反算"
        # 代理对：一个 emoji 在 UTF-16 里占两格
        PY_LSP._POSITION_ENCODING = "utf-16"
        assert PY_LSP._index_to_col("😀a", 1) == 2 and PY_LSP._col_to_index("😀a", 2) == 1
    finally:
        PY_LSP._POSITION_ENCODING = "utf-16"      # 别把状态漏给后面的判据

    # ---- 自举那一侧：真协议、真 ELF
    if not (_clang() and _wsl()):
        print("  SKIP 自举 LSP 的编码协商: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        b = Batch(_build(Path(td)), Path(td))
        for declared, want_enc, want_col in cases:
            caps = {} if declared is None else {"general": {"positionEncodings": declared}}
            out, rc, err = b.run([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"capabilities": caps}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": "file:///t.lomt", "text": src}}},
                {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            ])
            assert rc == 0, (rc, err[-300:])
            init = next(m for m in out if m.get("id") == 1)
            got_enc = init["result"]["capabilities"].get("positionEncoding")
            assert got_enc == want_enc, (declared, got_enc, err[-200:])
            dg = [m for m in out if m.get("method") == "textDocument/publishDiagnostics"]
            assert dg, "没有诊断（夹具里有一个未声明的符号）"
            got_col = dg[-1]["params"]["diagnostics"][0]["range"]["start"]["character"]
            assert got_col == want_col, (declared, got_col, want_col)
    print("      编码协商: 声明 utf-8 才回 utf-8；否则回 utf-16 且列按 code unit 换算")


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
        # `lspe pass`：块体照常检查（编得过 ⇒ 0 条）；理由空白 ⇒ 只有 E038。
        ("lspe-pass", PASS_SRC, None, 0, 0),
        ("lspe-pass-blank", PASS_BAD, "E038",
         PASS_BAD.splitlines().index('    lspe pass "   " {'), 1),
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


@test
def test_a_dot_nl_file_gets_one_info_and_no_fake_errors():
    """`#351`：`.nl` **没有** `choose write grammar` 那一行 —— 两个语言服务按**后缀**认
    natural，一条 Info、一条 Error 都没有。

    参考实现把 `.nl` 认成 `natural`（`potato_from.EXT`）；编辑器按同一张表把它交给
    `loment-natural`。语言服务要是拿 Loment 的规则去读它，用户会去改一份本来就对的源
    —— 这与 `#262` 是同一条，只是入口从"声明"换成了"后缀"。
    """
    src = (ROOT / "loment" / "nltrans" / "Sample.nl").read_text(encoding="utf-8")
    uri = "file:///sample.nl"
    pdg = PY_LSP.handle({"method": "textDocument/didOpen",
                         "params": {"textDocument": {"uri": uri, "text": src}}},
                        {uri: src})[0]["params"]["diagnostics"]
    assert pdg and all(d["severity"] == 3 for d in pdg), pdg
    assert "natural" in pdg[0]["message"], pdg[0]["message"]
    if not (_clang() and _wsl()):
        print("      自举侧 SKIP: 无 clang/WSL（Python 侧已判）")
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": src}}},
            {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    dg = frames[1]["params"]["diagnostics"]
    assert dg and all(d["severity"] == 3 for d in dg), dg
    assert "natural" in dg[0]["message"], dg[0]["message"]
    print("      两个语言服务：.nl 假错 0 条，各一条 Info 说明这是 natural 语法")


@test
def test_lsp_did_save_does_not_erase_the_document():
    """`#705`：didSave **不带正文**，不能当成一次空编辑把内存里的文档擦掉。

    真实客户端（vscode-languageclient）的 `didSave` 只有 `textDocument.uri`（本服务声明
    `textDocumentSync: 1`，也没有 `save.includeText`）。原先它走 `on_change`，`tn = 0`，
    文档被存成空串 —— 保存一次之后补全/跳转描述的都是一个空文件，直到下一次编辑。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///save.lomt"
    cline = GOOD.splitlines().index("    return helper();")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri, "text": GOOD}}},
        {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        # 保存通知：**没有 text / contentChanges**（就是客户端真实发的形状）
        {"method": "textDocument/didSave", "params": {"textDocument": {"uri": uri}}},
        {"jsonrpc": "2.0", "id": 3, "method": "textDocument/completion",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        {"jsonrpc": "2.0", "id": 4, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        {"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    by_id = {f["id"]: f for f in frames if "id" in f}
    for rid in (2, 3):
        labels = {it["label"] for it in by_id[rid]["result"]["items"]}
        assert {"helper", "main"} <= labels, (rid, sorted(labels)[:30])
    hline = GOOD.splitlines().index("fn helper() -> u32 {")
    d = by_id[4]["result"]
    assert d is not None and d["range"]["start"]["line"] == hline, d

    # Python 版同一件事：didSave 不改 docs。
    docs = {uri: GOOD}
    out = PY_LSP.handle({"method": "textDocument/didSave",
                         "params": {"textDocument": {"uri": uri}}}, docs)
    assert out == [] and docs[uri] == GOOD, (out, docs.get(uri))
    print("      didSave 后补全仍含 helper+main、跳转仍落在声明行（文档没被擦掉）")


#: `#706` 的探针源：每份文档定义一个**只属于自己的**函数，`definition` 按文档各查各的
#: （补全的符号表是"最后分析的那份"，不适合当"哪个文档还在"的判据）。
def _many_src(i: int) -> str:
    return f"module t{i}\n\nfn f{i}() -> u32 {{\n    return {i};\n}}\n"


def _decl_pos(src: str, name: str) -> tuple[int, int]:
    lines = src.splitlines()
    ln = lines.index(f"fn {name}() -> u32 {{")
    return ln, lines[ln].index(name) + 1


@test
def test_lsp_keeps_64_documents_and_frees_on_close():
    """`#706`：文档表原先只有 4 格，第 5 份**悄悄**顶掉第 4 份；didClose 又从不释放。

    现在：64 份各归各位；`didClose` 真的释放（关掉的那份 `definition` 回 null）；
    第 65 份**不被静默驱逐** —— 服务回一条 Info 明说"这份没被分析"，且先前那份仍然可用。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    n_doc = 64          # 与 `loment/tools/lsp.lomt` 的 `N_DOC` 对齐
    uri = lambda i: f"file:///many{i}.lomt"  # noqa: E731
    srcs = [_many_src(i) for i in range(n_doc)]
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    for i in range(n_doc):
        msgs.append({"method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": uri(i), "text": srcs[i]}}})
    ln0, ch0 = _decl_pos(srcs[0], "f0")
    ln3, ch3 = _decl_pos(srcs[3], "f3")
    msgs += [
        # 关之前：doc0 在
        {"jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri(0)},
                    "position": {"line": ln0, "character": ch0}}},
        {"method": "textDocument/didClose", "params": {"textDocument": {"uri": uri(0)}}},
        # 关之后：doc0 必须查不到（原先 didClose 没处理，这里仍是旧内容）
        {"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri(0)},
                    "position": {"line": ln0, "character": ch0}}},
        # 关掉一格后，再开一份**应当成功**（槽位被释放了）
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri(n_doc), "text": _many_src(n_doc)}}},
        {"jsonrpc": "2.0", "id": 4, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri(n_doc)},
                    "position": {"line": _decl_pos(_many_src(n_doc), f"f{n_doc}")[0],
                                 "character": _decl_pos(_many_src(n_doc), f"f{n_doc}")[1]}}},
        # 表又满了：第 65 份**不能被静默驱逐**，要报给客户端
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri(n_doc + 1),
                                     "text": _many_src(n_doc + 1)}}},
        # 而且先前那份（doc3）仍然在
        {"jsonrpc": "2.0", "id": 5, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri(3)},
                    "position": {"line": ln3, "character": ch3}}},
        {"jsonrpc": "2.0", "id": 6, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    by_id = {f["id"]: f for f in frames if "id" in f}
    assert by_id[2]["result"] is not None, by_id[2]
    assert by_id[3]["result"] is None, "didClose 没有释放槽位（关掉的文档还能跳转）"
    assert by_id[4]["result"] is not None and by_id[4]["result"]["uri"] == uri(n_doc), by_id[4]
    assert by_id[5]["result"] is not None and by_id[5]["result"]["uri"] == uri(3), \
        "第 65 份文档把先前的文档挤掉了（静默驱逐）"
    diag = {}
    for f in frames:
        if f.get("method") == "textDocument/publishDiagnostics":
            diag[f["params"]["uri"]] = f["params"]["diagnostics"]
    last = diag.get(uri(n_doc + 1), [])
    assert any("上限" in d["message"] and d["severity"] == 3 for d in last), \
        f"第 65 份文档应当回一条 Info 说明它没被分析, 实得 {last}"
    print(f"      {n_doc} 份各归各位；didClose 释放槽位；第 {n_doc + 1} 份得到"
          f"「已达上限」Info 且没有驱逐任何已打开文档")


#: `#707`：同一行里 CJK 字面量把后面的列整体推后 —— UTF-16 与 UTF-8 在这里不同。
#: 用**字符串字面量**装 CJK（词法器逐字节推进，列是字节；这正是 issue 里量的那形状）。
UTF8_MEMBER = ("module demo\n"
               "\n"
               "struct Point {\n"
               "    x: u32,\n"
               "    y: u32,\n"
               "}\n"
               "\n"
               "struct AB {\n"
               "    x: u32,\n"
               "}\n"
               "\n"
               "fn _start() {\n"
               "    let s: str = \"中文\"; let q: Point = Point { x: 1, y: 2 };\n"
               "    let s3: str = \"中文\"; let z: AB = 2;\n"
               # 编辑态 `q.` 后面紧跟 `syscall4`：解析器把它读成 `q.syscall4(...)`，
               # 所以 `_member_items` 的 `_parse` 不会失败（既有 MEMBER_SRC 用的是同一招）。
               "    let s2: str = \"中文\"; let v: u32 = q. // 成员\n"
               "    syscall4(60, 0, 0, 0);\n"
               "}\n")

#: 诊断探针（与 issue #707 量到的那一行同形）：UTF-16 说 30..33，UTF-8 是 34..37。
UTF8_BAD = ("module t\n"
            "\n"
            "fn _start() {\n"
            "    let s: str = \"中文\"; let w: f64 = 0;\n"
            "    syscall4(60, 0, 0, 0);\n"
            "}\n")


def _u8_col(line: str, ch_index: int) -> int:
    return len(line[:ch_index].encode("utf-8"))


def _u16_col(line: str, ch_index: int) -> int:
    """UTF-16 code unit 列（代理对算两格）。`#1171` 的默认编码。"""
    return len(line[:ch_index].encode("utf-16-le")) // 2


@test
def test_lsp_declares_utf8_positions_and_honours_them():
    """`#707`/`#1171`：位置编码**按客户端声明**，两个方向都要对齐。

    客户端声明 `utf-8` 时服务端回 utf-8、列按 UTF-8 字节；只声明 utf-16（默认）时回
    utf-16、列按 code unit（`#1171`）。这条判据同时扮演客户端：读 initialize 的能力，
    按**协商出来的编码**算 `character`（含非 ASCII 的行），要求补全/跳转/诊断真的落在
    那个位置 —— 声明与行为必须一致。两种编码各跑一遍，两个实现都跑。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///utf8.lomt"
    lines = UTF8_MEMBER.splitlines()
    ml = lines.index("    let s2: str = \"中文\"; let v: u32 = q. // 成员")
    # 定义跳转的**输入**与诊断的列都在 CJK 之后：选错编码就会整体错位。
    ul = lines.index("    let s3: str = \"中文\"; let z: AB = 2;")
    dln = lines.index("struct AB {")
    want_def = {"start": {"line": dln, "character": 7},
                "end": {"line": dln, "character": 9}}
    dl = UTF8_BAD.splitlines().index("    let s: str = \"中文\"; let w: f64 = 0;")
    dline = UTF8_BAD.splitlines()[dl]

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)                    # 编一次，两种声明各跑一遍
        for enc, col in (("utf-8", _u8_col), ("utf-16", _u16_col)):
            mch = col(lines[ml], lines[ml].index("q.") + 2)
            a0 = col(lines[ul], lines[ul].index("AB") + 1)
            f0 = col(dline, dline.index("f64"))
            init = {"capabilities": {"general": {"positionEncodings": [enc]}}}
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init},
                {"method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": uri, "text": UTF8_MEMBER}}},
                {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
                 "params": {"textDocument": {"uri": uri},
                            "position": {"line": ml, "character": mch}}},
                {"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
                 "params": {"textDocument": {"uri": uri},
                            "position": {"line": ul, "character": a0}}},
                {"method": "textDocument/didOpen",
                 "params": {"textDocument": {"uri": "file:///utf8bad.lomt",
                                             "text": UTF8_BAD}}},
                {"jsonrpc": "2.0", "id": 4, "method": "shutdown", "params": {}},
                {"method": "exit"},
            ]
            frames, rc, err = Batch(elf, td).run(msgs)
            assert rc == 0, f"[{enc}] LSP 退出码 {rc}: {err[-300:]}"
            caps = frames[0]["result"]["capabilities"]
            assert caps.get("positionEncoding") == enc, (enc, caps)
            by_id = {f["id"]: f for f in frames if "id" in f}
            assert {i["label"] for i in by_id[2]["result"]["items"]} == {"x", "y"}, (enc, by_id[2])
            got_def = by_id[3]["result"]
            assert got_def is not None and got_def["range"] == want_def, \
                (enc, want_def, got_def)
            dg = [f["params"]["diagnostics"] for f in frames
                  if f.get("method") == "textDocument/publishDiagnostics"][-1]
            want = {"start": {"line": dl, "character": f0},
                    "end": {"line": dl, "character": f0 + 3}}
            assert any(d["range"] == want for d in dg), (enc, want, dg)

            # Python 版：同一份协商；输入按协商的编码给。
            py_caps = PY_LSP.handle({"id": 1, "method": "initialize",
                                     "params": init}, {})[0]["result"]["capabilities"]
            assert py_caps.get("positionEncoding") == enc, (enc, py_caps)
            got = PY_LSP.handle({"jsonrpc": "2.0", "id": 2,
                                 "method": "textDocument/completion",
                                 "params": {"textDocument": {"uri": uri},
                                            "position": {"line": ml, "character": mch}}},
                                {uri: UTF8_MEMBER})[0]["result"]["items"]
            assert {i["label"] for i in got} == {"x", "y"}, (enc, got)
            pdef = PY_LSP.handle({"jsonrpc": "2.0", "id": 3,
                                  "method": "textDocument/definition",
                                  "params": {"textDocument": {"uri": uri},
                                             "position": {"line": ul, "character": a0}}},
                                 {uri: UTF8_MEMBER})[0]["result"]
            assert pdef is not None and pdef["range"] == want_def, (enc, want_def, pdef)
    PY_LSP._POSITION_ENCODING = "utf-16"
    print("      两种声明（utf-8 / utf-16）：CJK 行上 `q.` 补全、AB 跳转与诊断列"
          "都按协商出来的编码对齐（两个实现）")


@test
def test_lsp_reports_a_truncated_analysis():
    """`#710`：`on_change` 原先把 cap 写成 200000（< DOC_CAP = 262144）且**一声不吭**。

    末尾有错的长文档于是被报成"干净"的。现在：装得下的文档（250KB）要照常报错；
    装不下的（270KB）必须多一条 Info 明说"只检查了前一部分"。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return

    def long_src(n: int) -> str:
        tail = "module t\n\nfn main() -> u32 {\n    return missing_fn();\n}\n"
        pad = "// " + "x" * n + "\n"
        return pad + tail

    fits = long_src(250000)      # < 262128：完整分析，末尾 E002 必须报出来
    over = long_src(270000)      # > DOC_CAP：截断，必须报"没查全"
    assert 200000 < len(fits) < 262128 and len(over) > 262144, (len(fits), len(over))
    uri_f = "file:///fits.lomt"
    uri_o = "file:///over.lomt"
    eline = fits.splitlines().index("    return missing_fn();")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri_f, "text": fits}}},
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri_o, "text": over}}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    diag = {f["params"]["uri"]: f["params"]["diagnostics"] for f in frames
            if f.get("method") == "textDocument/publishDiagnostics"}
    df = diag.get(uri_f, [])
    assert any(d.get("code") == "E002" and d["range"]["start"]["line"] == eline for d in df), \
        f"250KB 文档在旧的 200000 上限处被截断，末尾 E002 丢了: {df}"
    assert not any("上限" in d["message"] for d in df), df
    do = diag.get(uri_o, [])
    assert any("上限" in d["message"] and d["severity"] == 3 for d in do), \
        f"截断的分析不能装成干净文件, 实得 {do}"
    print(f"      {len(fits)}B 完整分析（E002 @{eline}）；{len(over)}B 截断并报出 Info")


#: `#53` 的探针：同一份文档在 `didOpen` 与 `didChange` 里各报一次版本号。
VER_CLEAN = GOOD
VER_BAD = BAD


@test
def test_lsp_publish_diagnostics_carries_the_document_version():
    """`#53`：`publishDiagnostics` 要把 `didOpen` / `didChange` 里收到的 `version` 原样带回。

    服务声明了 `textDocumentSync: 1`（客户端**会**在每次编辑时发版本号），却把版本丢了 ——
    编辑器于是没法判断一批诊断描述的是哪一版缓冲区，"改完还留着的旧波浪线"就是这么来的。
    `version` 在协议里是可选字段，所以**缺席时不要编一个**。

    两个实现各判一次：Python 版直接调 `handle()`（永远跑）；自举版走真协议（无 clang/WSL 时跳过）。
    """
    uri = "file:///version.lomt"
    docs: dict[str, str] = {}

    def publish(msgs: list[dict]) -> dict:
        pub = [m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"]
        assert pub, f"应当回一条 publishDiagnostics: {msgs}"
        return pub[0]

    p1 = publish(PY_LSP.handle(
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri, "version": 1, "text": VER_BAD}}}, docs))
    assert p1["params"].get("version") == 1, p1
    assert any("missing_fn" in d["message"] for d in p1["params"]["diagnostics"]), p1
    p2 = publish(PY_LSP.handle(
        {"method": "textDocument/didChange",
         "params": {"textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": VER_CLEAN}]}}, docs))
    assert p2["params"].get("version") == 2, p2
    assert p2["params"]["diagnostics"] == [], p2
    # **缺席不编**：没带版本的通知，回包里也不该凭空出现 `version`。
    p3 = publish(PY_LSP.handle(
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": "file:///noversion.lomt", "text": VER_CLEAN}}}, {}))

    assert "version" not in p3["params"], p3

    if not (_clang() and _wsl()):
        print("      自举侧 SKIP: 无 clang/WSL（Python 侧已判）")
        return
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": uri, "version": 1, "text": VER_BAD}}},
        {"method": "textDocument/didChange",
         "params": {"textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": VER_CLEAN}]}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    pubs = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
    assert [p["params"].get("version") for p in pubs] == [1, 2], pubs
    assert any(d.get("code") == "E002" for d in pubs[0]["params"]["diagnostics"]), pubs[0]
    assert pubs[1]["params"]["diagnostics"] == [], pubs[1]
    print("      两个实现：didOpen v1 / didChange v2 原样回显；缺席时不编 version")


#: `#709` 量到的长度：613 字节的 URI > 旧的 `URI_CAP - 16` = 496。
LONG_URI = "file:///" + "x" * 600 + ".lomt"


@test
def test_lsp_keeps_a_long_uri_whole():
    """`#709`：URI 超过 496 字节时被**静默截断**，客户端按完整 URI 匹配就对不上。

    这条要求**原样保留**：诊断里 `params.uri` 与 `definition` 结果里的 `uri` 都必须与
    客户端发来的一字不差。Python 版从来没有上限（它用的是字符串），所以它这一半是**回归**
    判据；自举版是本次修的正题。
    """
    assert len(LONG_URI.encode("utf-8")) == 613, len(LONG_URI.encode("utf-8"))
    uri = LONG_URI
    cline = GOOD.splitlines().index("    return helper();")
    docs = {uri: GOOD}
    p = PY_LSP.handle({"method": "textDocument/didOpen",
                       "params": {"textDocument": {"uri": uri, "text": GOOD}}}, docs)
    assert p[0]["params"]["uri"] == uri, p[0]
    d = PY_LSP.handle({"jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
                       "params": {"textDocument": {"uri": uri},
                                  "position": {"line": cline, "character": 12}}}, docs)[0]
    assert d["result"] is not None and d["result"]["uri"] == uri, d

    if not (_clang() and _wsl()):
        print("      自举侧 SKIP: 无 clang/WSL（Python 侧已判）")
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": GOOD}}},
            {"jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
             "params": {"textDocument": {"uri": uri},
                        "position": {"line": cline, "character": 12}}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    pubs = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
    assert pubs and pubs[0]["params"]["uri"] == uri, (len(uri), pubs[:1])
    got = [f for f in frames if f.get("id") == 2][0]["result"]
    assert got is not None and got["uri"] == uri, (len(uri), got)
    print(f"      {len(uri)}B 的 URI：两个实现的诊断与跳转都原样保留")


@test
def test_lsp_names_the_limit_for_a_uri_that_does_not_fit():
    """`#709` 的另一半：装不下的 URI 至少要**说出上限**，不能静默截断。

    修法允许"保留完整 URI"或"拒绝并说出上限"；前者有容量上限，所以后者必须真的存在。
    这条用一个 9000 字节的 URI：它超过服务声明的 8176 字节上限，发布包里必须多一条
    severity 3 的 Info，消息里带**那个数字**（`URI_CAP - 16`）。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///" + "y" * 9000 + ".lomt"
    assert len(uri.encode("utf-8")) > 8176
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"method": "textDocument/didOpen",
             "params": {"textDocument": {"uri": uri, "text": GOOD}}},
            {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
            {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        frames, rc, err = Batch(_build(td), td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    pubs = [f for f in frames if f.get("method") == "textDocument/publishDiagnostics"]
    assert len(pubs) == 1, pubs
    dg = pubs[0]["params"]["diagnostics"]
    notes = [d for d in dg if d.get("severity") == 3 and "上限" in d["message"]]
    assert notes, f"超上限的 URI 不能静默截断, 实得 {dg}"
    assert "8176" in notes[0]["message"], notes[0]
    print("      超上限 URI：多一条点名 8176 字节上限的 Info（不再静默）")


if __name__ == "__main__":
    sys.exit(main())
