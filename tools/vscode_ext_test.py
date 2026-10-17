#!/usr/bin/env python3
# vscode_ext_test.py — VS Code 扩展的无头验收 (M56 编辑器宿主)
#
# 判据: 编辑器集成不必靠肉眼看 GUI 才能验证 ——
#   1) 扩展清单/语法文件结构合法, 且**每个语法正则都能编译** (否则会静默不高亮);
#   2) src/server-path.js (纯 Node) 真的能从工作区找到 tools/loment_lsp.py;
#   3) 对**真实语言服务**做一次完整 LSP 往返: initialize -> didOpen(诊断) ->
#      completion -> definition -> formatting -> shutdown;
#   4) .vsix 结构合法 (若已打包)。
#
# 用法: python tools/vscode_ext_test.py
# 退出码: 0 = 全绿 / 1 = 失败 / 2 = 环境缺失。

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import vscode_ext  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "editors" / "vscode"
VSIX = ROOT / "loment" / "build" / "loment-vscode.vsix"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _node() -> str | None:
    return shutil.which("node")


def _python() -> str:
    return shutil.which("python") or "python"


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _parse_frames(data: bytes) -> list[dict]:
    out = []
    while True:
        sep = data.find(b"\r\n\r\n")
        if sep < 0:
            return out
        header = data[:sep].decode("ascii", "replace")
        n = 0
        for line in header.splitlines():
            k, _, v = line.partition(":")
            if k.strip().lower() == "content-length":
                n = int(v.strip())
        body = data[sep + 4:sep + 4 + n]
        out.append(json.loads(body.decode("utf-8")))
        data = data[sep + 4 + n:]


# ---------------------------------------------------------------- 1. 清单与语法

@test
def test_vscode_ext_manifest_and_grammar():
    # `node_modules/` 是 gitignore 的 (vsix 打包才需要它): 干净检出/别的机器上就是没有。
    # 这属于**环境缺失**, 不是逻辑红 —— 所以只跳过那一项, 结构问题照常判。
    missing_npm = not (EXT / "node_modules" / "vscode-languageclient" / "package.json").is_file()
    bad = [b for b in vscode_ext.check() if "node_modules" not in b]
    assert not bad, f"扩展结构问题: {bad}"
    if missing_npm:
        print("      SKIP 打包前置: 没装 node_modules (npm install --omit=dev 于 editors/vscode)"
              " —— 干净检出/别的机器上如此, 不是逻辑问题")
    node = _node()
    if not node:
        print("      SKIP 正则编译: 无 node")
        return
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    # 每个 match / begin / end 都交给 node 编译一次 (Oniguruma 与 JS 正则不完全等价,
    # 但括号/转义写错这类问题两者都会报 —— 这是防止"语法文件静默失效"的一层)
    script = """
const fs = require('fs');
const files = process.argv.slice(1);
let n = 0;
for (const f of files) {
  const doc = JSON.parse(fs.readFileSync(f, 'utf8'));
  const walk = (o) => {
    if (o && typeof o === 'object') {
      for (const k of Object.keys(o)) {
        const v = o[k];
        if ((k === 'match' || k === 'begin' || k === 'end') && typeof v === 'string') {
          try { new RegExp(v); n += 1; } catch (e) { throw new Error(f + ': ' + v + ' -> ' + e.message); }
        } else { walk(v); }
      }
    }
  };
  walk(doc);
}
process.stdout.write(String(n));
"""
    grammars = [str(EXT / g["path"]) for g in pkg["contributes"]["grammars"]]
    r = subprocess.run([node, "-e", script, *grammars], capture_output=True, text=True,
                       shell=False)
    assert r.returncode == 0, f"语法正则编译失败: {r.stderr[-300:]}"
    assert int(r.stdout.strip()) >= 20, f"编译到的正则数偏少: {r.stdout!r}"


@test
def test_vscode_grammar_lints():
    """语法高亮的**两类静默失效**必须挡住 (它们不报错, 只是颜色不对):

    1. scope 名用了自造根名 —— 主题不认那段, 显示为默认前景色 (看起来"没高亮");
    2. `match` 里吃掉引号 —— 它会截胡字符串的**开引号**, 于是整份文件剩下的部分都被
       当成一个未闭合的字符串染成字符串色。我加 `excluded "` 时就踩了一次:
       在 `demo.lomt` 上从第 21 行起整片变字符串。`begin`/`end` 才是能碰引号的地方。
    """
    roots = {"comment", "string", "constant", "keyword", "storage", "entity",
             "support", "variable", "punctuation", "meta", "invalid"}
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    for g in pkg["contributes"]["grammars"]:
        doc = json.loads((EXT / g["path"]).read_text(encoding="utf-8"))
        assert doc["scopeName"] == g["scopeName"], (g["path"], doc["scopeName"])
        names, quotes = [], []

        def walk(o, key=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, k)
            elif isinstance(o, list):
                for x in o:
                    walk(x, key)
            elif isinstance(o, str):
                if key == "name":
                    names.append(o)
                if key == "match" and '"' in o:
                    quotes.append(o)

        # **文档顶层的 `name` 是显示名, 不是 scope** —— 走过去要一份豁免名单
        # ("Loment" / "L0 (.lom)"), 而名单外的显示名（如 "Loment (Python surface grammar)"）
        # 会被当成非法根名。规则本意是"scope 名要有标准根"，那就别把显示名算进来。
        walk({k: v for k, v in doc.items() if k != "name"})
        bad = [n for n in names if n.split(".")[0] not in roots]
        assert not bad, f"{g['path']}: 非标准 scope 根名 {bad[:4]}"
        assert not quotes, f"{g['path']}: match 里出现引号 (会吃掉字符串引号): {quotes[:2]}"
        print(f"      {g['path'].split('/')[-1]}: {len(set(names))} 个 scope 名合规, 无引号截胡")


# ---------------------------------------------------------------- 2. 服务路径发现

@test
def test_vscode_ext_finds_language_server():
    node = _node()
    if not node:
        print("      SKIP: 无 node")
        return
    script = (
        "const {findServer} = require(process.argv[1]);"
        "const hit = findServer(process.argv[2]);"
        "const deep = findServer(process.argv[2] + '/loment/examples');"
        "process.stdout.write(JSON.stringify({hit, deep}));"
    )
    r = subprocess.run([node, "-e", script, str(EXT / "src" / "server-path.js"), str(ROOT)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"server-path.js 执行失败: {r.stderr[-300:]}"
    got = json.loads(r.stdout)
    assert got["hit"] and got["hit"].replace("\\", "/").endswith("tools/loment_lsp.py"), got
    assert got["deep"] and got["deep"] == got["hit"], f"从子目录也应当找到: {got}"


# ---------------------------------------------------------------- 3. LSP 往返

@test
def test_vscode_lsp_roundtrip():
    """对真实语言服务做一次完整会话 —— 这正是扩展依赖的契约。"""
    src = "module m\n\npub fn add(a: u32, b: u32) -> u32 {\n    return a+b;\n}\n\nfn use_it() -> u32 {\n    return add(1, 2);\n}\n"
    uri = "file:///tmp/roundtrip.lomt"
    frames = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _frame({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": {"textDocument": {"uri": uri, "text": src}}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
                "params": {"textDocument": {"uri": uri}}}),
        _frame({"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
                "params": {"textDocument": {"uri": uri},
                           "position": {"line": 7, "character": 12}}}),
        _frame({"jsonrpc": "2.0", "id": 4, "method": "textDocument/formatting",
                "params": {"textDocument": {"uri": uri}}}),
        _frame({"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}}),
    ])
    r = subprocess.run([_python(), str(ROOT / "tools" / "loment_lsp.py")], input=frames,
                       capture_output=True, shell=False, timeout=60)
    msgs = _parse_frames(r.stdout)
    by_id = {m.get("id"): m for m in msgs if m.get("id") is not None}
    assert 1 in by_id, f"没有 initialize 应答: {r.stderr[-300:]!r}"
    caps = by_id[1]["result"]["capabilities"]
    for cap in ("textDocumentSync", "definitionProvider", "completionProvider",
                "documentFormattingProvider"):
        assert cap in caps, f"能力缺失: {cap} ({caps})"
    diags = [m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"]
    assert diags, "didOpen 之后应当推送诊断"
    assert diags[-1]["params"]["uri"] == uri and diags[-1]["params"]["diagnostics"] == [], diags[-1]
    labels = {it["label"] for it in by_id[2]["result"]["items"]}
    assert "fn" in labels and "add" in labels and "u32" in labels, sorted(labels)[:10]
    loc = by_id[3]["result"]
    assert loc and loc["range"]["start"]["line"] == 2, f"定义应跳到第 3 行: {loc}"
    edits = by_id[4]["result"]
    assert edits and "a + b" in edits[0]["newText"], f"格式化应当整理运算符间距: {edits}"
    assert 5 in by_id, "shutdown 应当有应答"


@test
def test_vscode_lsp_reports_type_errors():
    """错误路径: 未声明的变量要变成诊断, 而不是静默。"""
    src = "module m\n\nfn f() -> u32 {\n    return nope;\n}\n"
    uri = "file:///tmp/bad.lomt"
    frames = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _frame({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": {"textDocument": {"uri": uri, "text": src}}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}),
    ])
    r = subprocess.run([_python(), str(ROOT / "tools" / "loment_lsp.py")], input=frames,
                       capture_output=True, shell=False, timeout=60)
    msgs = _parse_frames(r.stdout)
    diags = [m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"]
    assert diags, "应当推送诊断"
    got = diags[-1]["params"]["diagnostics"]
    assert got and got[0]["severity"] == 1, f"未解析变量应当报错: {got}"
    assert got[0]["range"]["start"]["line"] == 3, f"应当定位到第 4 行: {got[0]}"


# ---------------------------------------------------------------- 4. VSIX

@test
def test_vscode_vsix_structure():
    if not VSIX.is_file():
        print("      SKIP: 尚未打包 (python tools/vscode_ext.py --emit …)")
        return
    bad = vscode_ext.verify_vsix(VSIX)
    assert not bad, f"VSIX 结构问题: {bad}"


# ---------------------------------------------------------------- 5. 编译 / 运行

@test
def test_vscode_build_and_run_commands():
    """**「编译 / 运行」两条命令真的接得上** —— 不是只写在清单里。

    这一条钉两件事，它们各自都是**真的会坏**的：

    1. **清单声明的命令都有实现**。清单里有、`extension.js` 里没 `registerCommand` ——
       用户点下去 VS Code 只说 "command not found"，而清单看着完全正常。
    2. **`build-cmd.js` 的分支判定**（纯模块，无头跑）。选错路的失败模式是
       "点了没反应"或"编出来的是别的东西"，在编辑器里看不出来 —— 所以值得一条判据。

    分支表见 `editors/vscode/src/build-cmd.js` 头注：装了 Loment 命令走它；
    Windows 开发树走 `scripts/lomc.ps1`；两条都没有就**报错**（不静默返回空命令）。
    """
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    declared = {c["command"] for c in pkg["contributes"]["commands"]}
    js = (EXT / "src" / "extension.js").read_text(encoding="utf-8")
    for c in ("loment.build", "loment.run"):
        assert c in declared, f"清单里没有声明 {c}（用户在命令面板里找不到它）"
        assert f"registerCommand('{c}'" in js, f"清单声明了 {c}，但扩展里没有实现它"
    for k in ("loment.toolCommand", "loment.toolArgs"):
        assert k in pkg["contributes"]["configuration"]["properties"], f"缺设置 {k}"

    # **`Ctrl+Shift+B` 那条路**：它只认**注册过的任务**，不认命令。少了下面这两样，
    # 用户按 `Ctrl+Shift+B` 什么都不会发生 —— 而命令面板里那两条明明是好的。
    tds = pkg["contributes"].get("taskDefinitions") or []
    assert any(t.get("type") == "loment" for t in tds), \
        f"清单里没有 loment 的 taskDefinitions（Ctrl+Shift+B 找不到它）: {tds}"
    assert "registerTaskProvider('loment'" in js, \
        "清单声明了 loment 任务，但扩展里没有注册任务提供者"

    # **编辑器右上角那个 ▶**。第三方 Code Runner 也给 `.lomt` 挂了一个 ——
    # 它的 `when` **不带语言过滤**（`config.code-runner.showRunIconInEditorTitleMenu`），
    # 而它对 Loment 没有任何执行器，所以点下去**真的什么都不做**（2026-09-18 实测：
    # 用户说"点了运行没反应"）。所以必须有自己的那一个，且 `when` 要钉在 loment 上。
    run_menu = (pkg["contributes"].get("menus") or {}).get("editor/title/run") or []
    assert any(m.get("command") == "loment.run" and "loment" in str(m.get("when", ""))
               for m in run_menu), \
        f"`editor/title/run` 里没有钉在 loment 上的 loment.run —— 右上角的 ▶ 会是别人的" \
        f"（点了没反应的正是它）: {run_menu}"

    node = _node()
    if not node:
        print("      SKIP 分支判定: 无 node")
        return
    script = """
const {invocation} = require(process.argv[1]);
const R = process.argv[2];
const F = R + '/loment/examples/tour.lomt';
process.stdout.write(JSON.stringify({
  cli_build: invocation({root:R, file:F, action:'build', platform:'win32',
                         toolCommand:'loment', buildDir:'loment/build'}),
  cli_run:   invocation({root:R, file:F, action:'run', platform:'win32',
                         toolCommand:'wsl', toolArgs:['-e','/x/loment']}),
  dev_build: invocation({root:R, file:F, action:'build', platform:'win32'}),
  dev_run:   invocation({root:R, file:F, action:'run', platform:'win32'}),
  none:      invocation({root:R, file:F, action:'build', platform:'linux'}),
  outside:   invocation({root:R, file:'D:/elsewhere/x.lomt', action:'build', platform:'linux'}),
  // **没打开文件夹**（VS Code 空窗口）：调用方只能给"文件所在目录"，或者干脆给不出
  empty_window: invocation({root:R + '/loment/examples', file:F, action:'run',
                            platform:'win32', buildDir:'loment/build'}),
  no_root:      invocation({file:F, action:'run', platform:'win32',
                            buildDir:'loment/build'}),
  // **库模块**（没有 `fn _start`）不该被"运行" —— 编得出 ELF，但一跑就 exit 11
  lib_run:   invocation({root:R, file:R + '/loment/examples/mathutil.lomt',
                         action:'run', platform:'win32', buildDir:'loment/build'}),
  lib_build: invocation({root:R, file:R + '/loment/examples/mathutil.lomt',
                         action:'build', platform:'win32', buildDir:'loment/build'})
}));
"""
    r = subprocess.run([node, "-e", script, str(EXT / "src" / "build-cmd.js"), str(ROOT)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"build-cmd.js 执行失败: {r.stderr[-300:]}"
    got = json.loads(r.stdout)

    # ① 装了 Loment 命令 -> 走它。`build` 带 `-o`（**不带扩展名**，`lomcli.lomt:694`），
    #    `run` 不带 —— 这条差别错了会编出个名字不对的文件，而命令看着是成功的。
    b = got["cli_build"]
    assert b.get("how") == "cli" and b["cmd"] == "loment", b
    assert b["args"][0] == "build", b
    assert "-o" in b["args"] and b["args"][-1].endswith("/tour"), b
    assert b["args"][1] == "loment/examples/tour.lomt", f"要传相对路径（cwd 是工作区根）: {b}"

    # ② 经 WSL 跑同一个命令：toolArgs 在前，子命令在后
    run = got["cli_run"]
    assert run["args"] == ["-e", "/x/loment", "run", "loment/examples/tour.lomt"], run
    assert "-o" not in run["args"], f"`run` 不该带 -o: {run}"

    # ③ Windows 开发树 -> scripts/lomc.ps1；**只有 run 带 `-Run`**
    if (ROOT / "scripts" / "lomc.ps1").is_file():
        d = got["dev_build"]
        assert d.get("how") == "lomc", d
        assert d["args"][:2] == ["-NoProfile", "-File"], d
        assert "-Run" not in d["args"], f"`build` 不该带 -Run: {d}"
        assert got["dev_run"]["args"][-1] == "-Run", got["dev_run"]
    else:
        print("      SKIP Windows 开发树那一支: 本仓没有 scripts/lomc.ps1")

    # ④ 两条都没有 -> **报错**，不是静默给一个空命令（那等于"点了没反应"）
    assert "error" in got["none"], f"没有可编译的东西时必须报错: {got['none']}"
    assert "toolCommand" in got["none"]["error"], f"报错要说清该改哪个设置: {got['none']}"

    # ⑤ 文件在工作区外 -> 报错（相对路径要传给外部命令，`..` 会指到别处）
    assert "error" in got["outside"], got["outside"]

    # ⑥ **没打开文件夹时（VS Code 空窗口）也要能用**：从文件所在目录往上找
    #    `scripts/lomc.ps1`。2026-09-18 实测：双击打开 `loment/examples/mathutil.lomt`
    #    点运行 -> 「找不到能编译的东西」—— 工具链明明在，只是没往上看。
    #    `server-path.js` / `debug-cmd.js` 早就是向上找的，`build-cmd.js` 原先不是。
    for key in ("empty_window", "no_root"):
        e = got[key]
        assert e.get("how") == "lomc", f"{key}: 空窗口下没找到 scripts/lomc.ps1: {e}"
        # cwd 必须是**仓库根** —— lomc.ps1 的 `-OutDir` 默认 `loment/build` 是相对 cwd 的
        assert e["cwd"].replace("\\", "/").rstrip("/") == str(ROOT).replace("\\", "/").rstrip("/"), \
            f"{key}: cwd 应当是仓库根: {e}"
        assert e["args"][-1] == "-Run", e
        # 相对路径的基准同样是仓库根（不是文件所在目录）
        assert e["args"][3] == "loment/examples/tour.lomt", f"{key}: 相对路径基准不对: {e}"
    print("      空窗口（没打开文件夹）：从文件往上找到 lomc.ps1，cwd 回到仓库根")

    # ⑦ **库模块不能被"运行"**。`loment/examples/mathutil.lomt` 只有 `pub fn`，
    #    没有 `_start` —— 编得出 ELF 但一跑就段错误（链接器那句
    #    `cannot find entry symbol _start` 在任务输出里，而 `lomc.ps1` 自己退出码是 0）。
    #    实测症状就是用户那句"跑不动"，而且不知道为什么。
    lib = got["lib_run"]
    assert "error" in lib, f"库模块不该被放行去运行: {lib}"
    assert "_start" in lib["error"], f"报错要说清缺的是什么: {lib['error'][:80]}"
    # **编译**不受影响（出 IR / 目标文件是正当用途）
    assert got["lib_build"].get("how") == "lomc", \
        f"库模块只是不能「运行」，编译照旧: {got['lib_build']}"
    print("      库模块：`运行` 拦住并说明缺 `_start`；`编译` 照旧放行")
    print(f"      编译/运行：清单↔实现对得上；分支判定 {len(got)} 例"
          f"（CLI / WSL / 开发树 / 报错 / 空窗口）")


# ---------------------------------------------------------------- 6. 扩展真的激活

#: 一个**假的 `vscode` 模块** —— 足够让 `extension.js` 跑完 `activate`。
#: `Module._load` 把 `require('vscode')` 拦下来换成它（另外把语言服务那个包也换掉，
#: 免得为了跑一次激活去装 `node_modules`）。
#:
#: **为什么要跑真的 `activate`**：清单对、命令都注册了、纯模块单测全绿，扩展照样可能
#: 一行都执行不到。实测过一次 —— `activate()` 里引用了没定义的 `root`（`if (root)`），
#: JavaScript 到那一行抛 `ReferenceError`，于是它**后面**的任务提供者与语言服务
#: 全都没注册；而用户在编辑器里看到的只是「按了没反应 / 没有用于调试 Loment 的扩展」。
#: 那正是本仓最讨厌的静默（`docs/167`）。
_ACTIVATE_HARNESS = r"""
const Module = require('module');
const fs = require('fs');
const EXT = process.argv[2];
const ROOT = process.argv[3];

const reg = [];
const dis = () => ({ dispose() {} });
const file = ROOT + '/loment/examples/user_hello.lomt';
const SETTINGS = JSON.parse(process.argv[4] || '{}');
//: 要"打开"的文档: [{path, lang}]。语言提示那一路是**事件驱动**的（开文档 / 文件头改动），
//: 所以这里得把真的开文档事件喂进去，而不是只检查订阅建没建。
const DOCS = JSON.parse(process.argv[5] || '[]');
const langs = [];
const openers = [], closers = [];

function Task(def, scope, name, source, exec, matcher) {
  this.definition = def; this.name = name; this.exec = exec; this.matcher = matcher;
}
const vscode = {
  languages: {
    // `setTextDocumentLanguage` 在真实现里会**触发该文档的 close+open**；这里照实改
    // languageId 并记一笔，让判据能看到"最后停在哪个语言 id 上"。
    setTextDocumentLanguage: async (doc, id) => {
      doc.languageId = id;
      langs.push([doc.uri.fsPath, id]);
      return doc;
    },
  },
  workspace: {
    getConfiguration: () => ({ get: (k) => SETTINGS[k] }),
    workspaceFolders: [{ uri: { fsPath: ROOT } }],
    openTextDocument: async () => ({ uri: {} }),
    textDocuments: [],
    onDidOpenTextDocument: (f) => { openers.push(f); return dis(); },
    onDidChangeTextDocument: () => dis(),
    onDidCloseTextDocument: (f) => { closers.push(f); return dis(); },
    createFileSystemWatcher: () => ({ onDidChange: dis, onDidCreate: dis,
                                      onDidDelete: dis, dispose() {} }),
  },
  window: {
    activeTextEditor: { document: { uri: { fsPath: file }, languageId: 'loment' } },
    // **假得照实**：只有 `{log: true}` 建的才是 LogOutputChannel —— 普通通道**没有**
    // error/warn/info/debug/trace 与 onDidChangeLogLevel。vscode-languageclient v10 要的
    // 正是后者，所以这条假通道能把"建了普通通道"当场变成失败（真实现里它是一句
    // TypeError，而且是**语言服务起来之后**才炸）。
    createOutputChannel: (name, opts) => {
      const isLog = !!(opts && opts.log);
      reg.push('channel:' + name + (isLog ? ':log' : ':plain'));
      const c = { appendLine() {}, append() {}, replace() {}, clear() {},
                  show() {}, hide() {}, dispose() {} };
      if (isLog) {
        c.error = () => {}; c.warn = () => {}; c.info = () => {};
        c.debug = () => {}; c.trace = () => {};
        c.logLevel = 3; c.onDidChangeLogLevel = () => dis();
      }
      vscode.__channel = c;
      return c;
    },
    showWarningMessage: (m) => reg.push('warn:' + m),
    showErrorMessage: (m) => reg.push('err:' + m),
    showTextDocument: async () => ({}),
  },
  commands: {
    registerCommand: (id) => { reg.push('cmd:' + id); return dis(); },
    executeCommand: () => {},
  },
  tasks: {
    registerTaskProvider: (t, p) => {
      reg.push('taskProvider:' + t); vscode.__provider = p; return dis();
    },
    executeTask: async () => {},
  },
  debug: {
    registerDebugAdapterDescriptorFactory: (t, f) => {
      reg.push('debugFactory:' + t); vscode.__factory = f; return dis();
    },
  },
  ProcessExecution: function (c, a, o) { this.cmd = c; this.args = a; this.opts = o; },
  Task,
  TaskScope: { Workspace: 1 },
  TaskRevealKind: { Always: 1 },
  TaskPanelKind: { Shared: 1 },
  TaskGroup: { Build: 2 },
  Uri: { file: (p) => ({ fsPath: p }) },
  DebugAdapterExecutable: function (c, a) { this.cmd = c; this.args = a; },
};

const load = Module._load;
Module._load = function (req) {
  if (req === 'vscode') { return vscode; }
  if (req === 'vscode-languageclient/node') {
    return { LanguageClient: class { async start() { reg.push('client:start'); }
                                     async stop() {} } };
  }
  return load.apply(this, arguments);
};

(async () => {
  const out = { ok: false, reg: [], subs: 0, tasks: null, buildGroup: null, dap: null,
                docs: [], langs: [] };
  try {
    const ext = require(EXT);
    const ctx = { subscriptions: [] };
    await ext.activate(ctx);
    out.ok = true;
    out.subs = ctx.subscriptions.length;
    if (vscode.__provider) {
      const ts = await vscode.__provider.provideTasks();
      out.tasks = ts.length;
      out.buildGroup = ts.some((t) => t.group === vscode.TaskGroup.Build);
    }
    if (vscode.__factory) {
      const d = vscode.__factory.createDebugAdapterDescriptor();
      out.dap = d ? { cmd: d.cmd, args: d.args } : null;
    }
    // 打开 DOCS 里每一份文档（语言提示是事件驱动的，必须真的派事件）
    const docs = DOCS.map((c) => ({
      uri: { scheme: 'file', fsPath: c.path },
      languageId: c.lang || 'loment',
      getText: () => fs.readFileSync(c.path, 'utf8'),
    }));
    for (const d of docs) {
      for (const h of openers) h(d);
    }
    await new Promise((r) => setTimeout(r, 30));
    out.docs = docs.map((d) => ({ path: d.uri.fsPath, lang: d.languageId }));
    out.langs = langs;
  } catch (e) {
    out.error = String((e && e.stack) || e);
  }
  out.reg = reg;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _activate(settings: dict, docs: list[dict]) -> subprocess.CompletedProcess:
    """跑一遍**真的 `activate()`**（假 `vscode` 由 `_ACTIVATE_HARNESS` 注入）。

    `docs` 是"要打开的文档" `[{path, lang}]` —— 语言提示那一路是事件驱动的，所以判据
    必须把开文档事件派进去，而不是只检查订阅建没建。
    """
    node = _node()
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "activate-harness.js"
        h.write_text(_ACTIVATE_HARNESS, encoding="utf-8")
        return subprocess.run(
            [node, str(h), str(EXT / "src" / "extension.js"), str(ROOT),
             json.dumps(settings), json.dumps(docs)],
            capture_output=True, text=True, shell=False)


@test
def test_vscode_extension_activates():
    """**扩展真的激活得起来，而且该注册的都注册了。**

    `activate()` 抛异常时 VS Code 只会把扩展标红，功能**静默全丢** ——
    而清单、纯模块单测、语法文件全都还是绿的。所以这一条要真的跑一遍它。
    """
    if not _node():
        print("      SKIP: 无 node")
        return
    # **语言服务开着**（`enableLsp: true`）：关掉它 `startClient` 根本不跑，而这一格里
    # 藏着真 bug —— v10 的客户端要求输出通道是 `LogOutputChannel`。
    r = _activate({"enableLsp": True, "toolCommand": "loment",
                   "buildDir": "loment/build"}, [])

    def fail(msg):
        return AssertionError(f"{msg}\n  node stderr: {r.stderr.strip()[-400:]}")

    assert r.returncode == 0, fail(f"激活脚手架本身就挂了 (rc={r.returncode})")
    got = json.loads(r.stdout)
    assert got.get("ok"), fail(f"`activate()` 抛了: {got.get('error', '')[:600]}")

    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    reg = set(got["reg"])

    # ① 清单里声明的**每一条命令**都要真的注册上 —— 少一条，用户点下去只说
    #    "command not found"，而清单看着完全正常。
    missing = sorted(c["command"] for c in pkg["contributes"]["commands"]
                     if f"cmd:{c['command']}" not in reg)
    assert not missing, fail(f"这些命令在清单里，但 activate 时没注册: {missing}")

    # ② 任务提供者 / 调试适配器工厂：`Ctrl+Shift+B` 与 F5 各自只认这两样
    assert "taskProvider:loment" in reg, fail(f"没有注册 loment 任务提供者: {sorted(reg)}")
    assert "debugFactory:loment" in reg, fail(f"没有注册 loment 调试适配器工厂: {sorted(reg)}")

    # ④ **语言服务的输出通道必须是 LogOutputChannel**（`createOutputChannel(name, {log:true})`）。
    #    `vscode-languageclient` v10 会给它挂 `onDidChangeLogLevel(...)` 并调 `.error(...)`，
    #    普通通道这两样都没有 —— 实测症状是语言服务一起就弹两条
    #    `TypeError: this.outputChannel.error is not a function`。判据用**假得照实**的通道
    #    （只有 `{log:true}` 才带那几个方法），所以这一格坏了会当场红。
    assert "channel:Loment:log" in reg, fail(
        "输出通道不是 LogOutputChannel —— vscode-languageclient v10 要求 "
        "`createOutputChannel('Loment', {log: true})`；注册记录里是 "
        f"{[x for x in reg if x.startswith('channel:')] or '（压根没建）'}")
    assert "client:start" in reg, fail(f"语言服务没起来: {sorted(reg)}")

    # ③ 任务提供者真给出两条任务，且「编译」是**默认生成任务**（`Ctrl+Shift+B` 靠它）
    assert got["tasks"] == 2, fail(f"应当给「编译 / 编译并运行」两条任务，给了 {got['tasks']}")
    assert got["buildGroup"] is True, fail("「编译」没有挂 TaskGroup.Build —— Ctrl+Shift+B 不会挑它")

    # ④ 调试工厂给得出**真命令**（不是 undefined）。清单里 `debuggers[].type` 必须与
    #    注册时用的那个名字一致 —— 不一致时 F5 还是会弹"没有用于调试的扩展"。
    types = [d.get("type") for d in pkg["contributes"].get("debuggers") or []]
    assert "loment" in types, fail(f"清单里的调试器类型对不上: {types}")
    dap = got["dap"]
    assert dap, fail("调试适配器工厂返回了 null/undefined（F5 会是一个没反应的会话）")
    assert dap["args"] and any("loment_dap.py" in a for a in dap["args"]), fail(f"适配器命令不对: {dap}")
    print(f"      activate() 跑通：{len(reg)} 项注册、{got['tasks']} 条任务（含默认生成任务）、"
          f"调试适配器 `{dap['cmd']} …{Path(dap['args'][-1]).name}`")


# ------------------------------------------------- 5.5 表层语法与未受信工作区

#: 与参考实现对拍的**边角**语料。主体是仓库里**全部** `.lomt` / `.lom`（枚举，不是手挑）——
#: 手挑的那几条往往正好避开分叉（`docs/188` §1 那条教训）。
_DECL_EDGE = [
    "module m\n\nfn f() -> u32 { return 1; }\n",
    "choose write grammar python\nmodule m\n",
    "choose write grammar rust\nmodule m\n",
    "choose write grammar c#\nmodule m\n",
    "﻿choose write grammar go\nmodule m\n",
    "   choose write grammar java\nmodule m\n",
    "module m\nchoose write grammar python\n",
    "choose write grammar python\nchoose write grammar c\nmodule m\n",
    "choose write grammar\nmodule m\n",
    "choose write grammars python\nmodule m\n",
    "grammar python\nmodule m\n",
    "module m\n\nchoose gc_auto_alpha\n",
    "choose write grammar python;\nmodule m\n",
    # 声明**不在第 1 行**（前面一段块注释）—— 实测过：漏了这个形状时
    # `loment/examples/cproject/bitkit.lomt` 两边说法不一致（2026-10-12），
    # 而手挑的边角全在第 1 行、全绿。
    "/*\n * 说明\n */\n\nchoose write grammar c\n\nunsigned int f(unsigned int x) {\n",
]

_CONF_EDGE = [
    'pub fn source_ext() -> str { return ".foo"; }\n',
    '// source_ext ".bar"\nmodule conf\n\npub fn source_ext() -> str { return ".foo"; }\n',
    '/* source_ext ".bar" */\npub fn source_ext() -> str { return ".baz"; }\n',
    'pub fn other() -> str { return ".foo"; }\n',
    'pub fn source_ext() -> str { return x; }\n',
    'pub fn source_ext() -> str { return "foo"; }\n',
    'pub fn source_ext() -> str { return ""; }\n',
    'pub fn source_ext() -> str {\n  let a: u32 = 1;\n  return ".foo";\n}\n',
    '',
]

_NODE_DECL = r"""
const g = require(process.argv[1]);
const texts = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify(
  texts.map((t) => { const r = g.readGrammarDecl(t); return [r.grammar, r.declared]; })));
"""

_NODE_CONF = r"""
const c = require(process.argv[1]);
const texts = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify(texts.map((t) => c.readSourceExt(t))));
"""


def _run_node_module(script: str, module: Path, corpus: list[str]) -> list:
    """把一批输入喂给一个**纯 Node 模块**，拿回它的判断。"""
    node = _node()
    assert node, "无 node"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "corpus.json"
        f.write_text(json.dumps(corpus), encoding="utf-8")
        r = subprocess.run([node, "-e", script, str(module), str(f)],
                           capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"node 侧挂了: {r.stderr[-400:]}"
    return json.loads(r.stdout)


def _l1_sources() -> list[tuple[str, str]]:
    """仓库里全部 `.lomt` / `.lom` 的 `(相对路径, 原文)`（跳过 `dist/` 下那几份拷贝）。"""
    out = []
    for p in sorted(ROOT.rglob("*")):
        if p.suffix not in (".lomt", ".lom") or not p.is_file():
            continue
        if "dist" in p.relative_to(ROOT).parts:
            continue
        try:
            out.append((p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return out


@test
def test_vscode_grammar_decl_matches_reference():
    """`grammar-detect.js` 与参考实现（`potato_from.read_grammar_decl`）**逐条对拍**。

    只比 `(规范名, 声明成立吗)` —— 报错文案归编译器，编辑器这一侧不重复它。
    语料是**仓库里每一份 L1/L0 源**加上那批边角：判据的价值全在"枚举"，手挑几条会
    正好避开分叉（`docs/188` §1 那条教训）。
    """
    if not _node():
        print("      SKIP: 无 node")
        return
    import potato_from  # noqa: PLC0415  （对拍的另一边）
    pairs = _l1_sources()
    texts = [t for _rel, t in pairs] + _DECL_EDGE
    js = _run_node_module(_NODE_DECL, EXT / "src" / "grammar-detect.js", texts)
    bad = []
    for i, t in enumerate(texts):
        grammar, _err, declared = potato_from.read_grammar_decl(t)
        if js[i] != [grammar, declared]:
            name = pairs[i][0] if i < len(pairs) else f"<边角 #{i - len(pairs)}>"
            bad.append(f"{name}: js={js[i]} py={[grammar, declared]}")
    assert not bad, "声明检测两边说法不一致:\n  " + "\n  ".join(bad[:8])
    # 别名表本身也要同表 —— 右列是规范名，错一格就会"源里能写、对象非法"。
    js_aliases = _run_node_module(
        "const g = require(process.argv[1]);\nprocess.stdout.write(JSON.stringify(g.GRAMMAR_ALIASES));",
        EXT / "src" / "grammar-detect.js",
        [""])
    assert js_aliases == potato_from.GRAMMAR_ALIASES, (
        f"别名表两边不同:\n  js={js_aliases}\n  py={potato_from.GRAMMAR_ALIASES}")
    print(f"      声明检测对拍: {len(texts)} 份源（含 {len(_DECL_EDGE)} 条边角）两边一致；"
          f"别名表 {len(js_aliases)} 条同表")


@test
def test_vscode_source_ext_matches_reference():
    """`conf-ext.js` 与参考实现（`lomentc._conf_ext_from_tokens`）逐条对拍。"""
    if not _node():
        print("      SKIP: 无 node")
        return
    import lomc  # noqa: PLC0415
    import lomentc  # noqa: PLC0415
    js = _run_node_module(_NODE_CONF, EXT / "src" / "conf-ext.js", _CONF_EDGE)
    bad = []
    for i, t in enumerate(_CONF_EDGE):
        want = lomentc._conf_ext_from_tokens(lomc.lex(t))
        if js[i] != want:
            bad.append(f"#{i}: js={js[i]!r} py={want!r}")
    assert not bad, "`source_ext` 读法两边不一致:\n  " + "\n  ".join(bad)
    print(f"      source_ext 对拍: {len(_CONF_EDGE)} 条一致（含注释里的同名标签、带标签无字符串）")


@test
def test_vscode_surface_grammar_switches_language():
    """**真的打开文档**：声明了外门表层语法的源切到 `loment-<规范名>`，原生的不动。

    这一格跑的是 `activate()` 里注册的那个**入口函数**（开文档 -> 读声明 -> 换语言 id），
    而不是纯模块 —— 纯模块全绿而入口没接上，用户在编辑器里看到的还是"一片不上色"。
    """
    if not _node():
        print("      SKIP: 无 node")
        return
    cases = [
        # (仓库相对路径, 打开时的语言 id, 期望停在哪个语言 id)
        ("loment/examples/multisyntax-projects/python/01-numtheory/numtheory.lomt",
         "loment", "loment-python"),
        ("loment/examples/multisyntax-projects/c/01-algo/algo.lomt", "loment", "loment-c"),
        ("loment/examples/multisyntax-projects/go/01-digest/digest.lomt", "loment", "loment-go"),
        ("loment/lib/lumtui_math.lomt", "loment", "loment-python"),
        # `rust` 是**原生**（docs/188 §7.3）：声明了也不该换语言 id
        ("loment/selfhost/grammar_decl/rust.lomt", "loment", "loment"),
        # 没声明 = 原生
        ("loment/examples/tour.lomt", "loment", "loment"),
        # 用户显式指定过的语言不去抢（`lom` 是 L0，家族之外）
        ("lom/fuai.lom", "lom", "lom"),
    ]
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "loment.conf").write_text(
            'pub fn source_ext() -> str { return ".foo"; }\n', encoding="utf-8")
        (proj / "a.foo").write_text("module a\n", encoding="utf-8")
        (proj / "b.txt").write_text("hello\n", encoding="utf-8")
        cases += [(str(proj / "a.foo"), "plaintext", "loment"),   # 项目自定义后缀 -> L1
                  (str(proj / "b.txt"), "plaintext", "plaintext")]  # 别的后缀，不认领
        docs = [{"path": (c[0] if Path(c[0]).is_absolute() else str(ROOT / c[0])),
                 "lang": c[1]} for c in cases]
        r = _activate({"enableLsp": False, "buildDir": "loment/build"}, docs)

    def fail(msg):
        return AssertionError(f"{msg}\n  node stderr: {r.stderr.strip()[-400:]}")

    assert r.returncode == 0, fail(f"脚手架挂了 (rc={r.returncode})")
    got = json.loads(r.stdout)
    assert got.get("ok"), fail(f"`activate()` 抛了: {got.get('error', '')[:600]}")
    after = {d["path"]: d["lang"] for d in got["docs"]}
    bad = []
    for rel, _before, want in cases:
        path = rel if Path(rel).is_absolute() else str(ROOT / rel)
        if after[path] != want:
            bad.append(f"{Path(rel).name}（打开时 {_before}）: 期望 {want}，实得 {after[path]}")
    assert not bad, "表层语法没有换到该在的语言 id:\n  " + "\n  ".join(bad)
    print(f"      开文档换语言: {len(cases)} 例（外门七门换、原生不换、L0 不抢、"
          f"项目自定义后缀认成 loment）")


@test
def test_vscode_multisyntax_languages_are_declared():
    """七门外门表层语法**在清单里各有语言 + 语法**，而且借的宿主 scope 写对。

    三处必须说同一件事：出厂锁那张别名表（`potato_from.GRAMMAR_ALIASES`）、
    `grammar-detect.js` 的 `languageIdFor`、以及 `package.json` 的 languages/grammars。
    少一处就是"声明写得出、编辑器里没那门"。
    """
    import potato_from  # noqa: PLC0415
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    langs = {lang["id"] for lang in pkg["contributes"]["languages"]}
    canonical = sorted(set(potato_from.GRAMMAR_ALIASES.values()))
    foreign = [g for g in canonical if g not in potato_from.NATIVE_GRAMMARS]
    want = {"loment", "lom"} | {f"loment-{g}" for g in foreign}
    assert langs == want, f"清单里的语言 id 对不上出厂锁那张表:\n  多: {sorted(langs - want)}\n  少: {sorted(want - langs)}"
    if _node():
        ids = _run_node_module(
            "const g = require(process.argv[1]);\n"
            "const names = [...new Set(Object.values(g.GRAMMAR_ALIASES))];\n"
            "process.stdout.write(JSON.stringify(names.map((c) => g.languageIdFor(c))));",
            EXT / "src" / "grammar-detect.js", [""])
        assert set(ids) == want - {"lom"}, (
            f"`languageIdFor` 给出的 id 与清单不同: {sorted(set(ids))}")
    # 每一门外门语法的语法文件都必须**只做两件事**：认那行声明 + include 宿主的语法。
    guests = {"c": "source.c", "cpp": "source.cpp", "java": "source.java",
              "csharp": "source.cs", "go": "source.go", "python": "source.python"}
    for g in foreign:
        path = EXT / "syntaxes" / f"loment-{g}.tmLanguage.json"
        assert path.is_file(), f"缺语法文件: {path.name}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        pats = [json.dumps(p, ensure_ascii=False) for p in doc["patterns"]]
        assert any("source.loment#grammar-decl" in p for p in pats), (
            f"{path.name} 没 include 那行声明的规则（它就该只做这件事 + 借宿主）")
        if g in guests:
            assert any(guests[g] in p for p in pats), (
                f"{path.name} 没 include 宿主语法 {guests[g]}")
    # 声明那一格必须真的在主语法里（否则 include 的是个空名字，静默不上色）
    main = json.loads((EXT / "syntaxes" / "loment.tmLanguage.json").read_text(encoding="utf-8"))
    assert "grammar-decl" in main["repository"], "主语法里没有 `grammar-decl` 这一格"
    print(f"      多语法登记: {len(foreign)} 门外门语法各有语言 id 与语法文件，"
          f"宿主 scope 逐门对得上")


@test
def test_vscode_hardens_untrusted_workspaces():
    """#166：未受信工作区要有**声明**，且"能变成跑什么"的设置要被挡住。

    这一条与 `vscode_ext.check()` 里那条是同一个判断的两处落点 —— 这里断言的是
    **内容**（哪几个设置、为什么是它们），`--check` 那条保证**每次打包都会看**。
    """
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    cap = pkg.get("capabilities", {})
    untrusted = cap.get("untrustedWorkspaces")
    assert untrusted, "package.json 没有 capabilities.untrustedWorkspaces（#166）"
    assert untrusted.get("supported") in (True, "limited"), (
        f"untrustedWorkspaces.supported 取值不对: {untrusted.get('supported')!r}")
    restricted = set(untrusted.get("restrictedConfigurations") or [])
    props = set(pkg["contributes"]["configuration"]["properties"])
    need = set(vscode_ext.MUST_RESTRICT)
    assert need <= restricted, f"这些设置没被挡住: {sorted(need - restricted)}"
    assert restricted <= props, f"restrictedConfigurations 里有不存在的设置: {sorted(restricted - props)}"
    # 挡住的那几个必须**确实**是"能变成跑什么"的那类（命令 / 参数 / 会被执行的脚本路径）
    for s in sorted(need):
        assert s.split(".")[-1].endswith(("Command", "Args", "Path", "Script")), \
            f"{s} 看着不像是能变成命令的设置 —— MUST_RESTRICT 该改就一起改"
    assert all(b is None or "untrustedWorkspaces" not in b for b in vscode_ext.check()), \
        "vscode_ext.check() 也该钉着这件事（--check 与 CI 都要看得到）"
    print(f"      未受信工作区: 声明了 supported={untrusted.get('supported')!r}，"
          f"挡住 {len(restricted)} 个设置（命令/参数/脚本路径）")


@test
def test_vscode_publish_ready():
    """商店发布那几件 —— `vsce publish` 的硬要求，加"两份打包清单不许漏同一类文件"。

    2026-10-13 第一次把它发到 Marketplace 之前补的。四件事：

    1. `repository` 要在（否则每次都得带 `--allow-missing-repository`）；
    2. `icon` 指到的文件要**真的在包里**，而且按商店规范是 128×128 的 PNG；
    3. `LICENSE` 要在**扩展目录**里，且与仓根那份**逐字节相同** —— vsce 只找扩展目录这一份，
       而"同一份东西存在第二份就会漂"在本仓对别的派生物一律上判据，许可正文不该例外；
    4. `node_modules` 必须在**两份**清单里：`files`（vsce 认的那份）与
       `vscode_ext.INCLUDE_DIRS`（本机侧载那份）。漏了的话商店装下来的扩展起不来 ——
       VS Code **不会**替用户跑 `npm install`。
    """
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    # ① repository
    repo = pkg.get("repository") or {}
    url = repo.get("url") if isinstance(repo, dict) else repo
    assert url and "github.com" in str(url), f"repository 不对: {repo!r}"
    # ② icon：存在、是 PNG、128×128、且在两份清单里
    icon = pkg.get("icon")
    assert icon, "package.json 缺 icon（商店卡片会是灰的）"
    ip = EXT / str(icon)
    assert ip.is_file(), f"icon 指的文件不存在: {icon}"
    raw = ip.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{icon} 不是 PNG"
    w, h = struct.unpack(">II", raw[16:24])
    assert (w, h) == (128, 128), f"{icon} 是 {w}x{h}，商店要 128x128"
    files = pkg.get("files") or []
    assert str(icon) in files, f"{icon} 不在 package.json 的 files 里"
    assert str(icon) in vscode_ext.INCLUDE_FILES, \
        f"{icon} 不在 vscode_ext.INCLUDE_FILES 里（本机侧载那份会缺它）"
    # ③ LICENSE：扩展目录里那份 == 仓根那份
    lic_ext, lic_root = EXT / "LICENSE", ROOT / "LICENSE"
    assert lic_ext.is_file(), "扩展目录里没有 LICENSE（vsce 只找这一份）"
    assert lic_root.is_file(), "仓根没有 LICENSE？"
    assert lic_ext.read_bytes().replace(b"\r\n", b"\n") == \
        lic_root.read_bytes().replace(b"\r\n", b"\n"), \
        "editors/vscode/LICENSE 与仓根 LICENSE 不一致（两份许可正文已经开始漂了）"
    assert "LICENSE" in files and "LICENSE" in vscode_ext.INCLUDE_FILES, \
        "LICENSE 没进打包清单"
    # ④ node_modules 两份清单都要有
    assert "node_modules" in files, "files 里没有 node_modules —— 商店装下来的跑不起来"
    assert "node_modules" in vscode_ext.INCLUDE_DIRS, "INCLUDE_DIRS 里没有 node_modules"
    # 附带：语言引用的 grammar / configuration 要被 vsce 那份清单罩住
    for g in pkg["contributes"]["grammars"]:
        assert Path(str(g["path"]).lstrip("./")).parts[0] in files, \
            f"files 没罩住语法目录: {g['path']}"
    for lang in pkg["contributes"]["languages"]:
        conf = lang.get("configuration")
        if conf:
            assert Path(str(conf).lstrip("./")).name in files, \
                f"files 没罩住 {lang['id']} 的 configuration: {conf}"
    print(f"      发布就绪：repository / icon 128x128 / LICENSE 与仓根逐字节相同 / "
          f"node_modules 在两份清单里（{len(files)} 条允许清单）")


@test
def test_vscode_registration_points_at_the_real_dir():
    """**登记必须指向真目录** —— 它指向别处时高亮/命令/F5 整个消失，而磁盘上一切"看着在"。

    2026-09-18 实测踩到的坑：侧载时把旧副本挪成 `<ident>-<ver>.old` **留在了
    `extensions/` 里**，VS Code 扫到那个文件夹（它也有 `package.json`），把扩展登记到了
    这个**马上要被删掉**的名字上。当时 `--doctor` 只查"有没有登记"，所以照常报 OK ——
    这一条把那个盲点钉住。
    """
    ident = "hooyan2022.loment"
    dest = vscode_ext.ext_dir() / f"{ident}-0.1.0"
    stale = {"identifier": {"id": ident}, "version": "0.1.0",
             "location": {"$mid": 1, "path": f"/c:/x/{ident}-0.1.0.old", "scheme": "file"},
             "relativeLocation": f"{ident}-0.1.0.old"}
    other = {"identifier": {"id": "someone.else"},
             "relativeLocation": "someone.else-1.0.0"}
    entries, n = vscode_ext.fix_registration([dict(stale), dict(other)], ident, dest)
    assert n == 1, f"应当只改 1 条，改了 {n}"
    got = entries[0]
    assert got["relativeLocation"] == dest.name, got
    # URI 要**照抄 VS Code 自己写的那种**：只有 $mid/path/scheme，正斜杠（Windows 上
    # 盘符小写并顶一个 `/`，即 `/c:/...`）。自己发明 `fsPath`/`external` 就不是它认的
    # 那一份了。
    assert got["location"] == vscode_ext.vs_uri(dest), got["location"]
    assert set(got["location"]) == {"$mid", "path", "scheme"}, got["location"]
    if sys.platform == "win32":
        assert re.match(r"^/[a-z]:/", got["location"]["path"]), got["location"]
    else:
        # 别的平台上**就是这个绝对路径本身，不许再顶一个斜杠** —— `//home/...`
        # 写进 `extensions.json` 就是一条坏登记（2026-09-22 在 CI 的 Linux runner 上
        # 露出来的那个 bug，修它是 `vs_uri` 那一笔，这条断言把结果钉住）。
        assert got["location"]["path"] == dest.as_posix(), got["location"]
    assert entries[1] == other, f"别人的条目不许动: {entries[1]}"

    # 盘符小写、正斜杠、顶一个 `/` —— 与 VS Code 写出来的一致
    assert vscode_ext.vs_uri(Path("D:/Dev/Loment-DEV/x")) == {
        "$mid": 1, "path": "/d:/Dev/Loment-DEV/x", "scheme": "file"}, \
        vscode_ext.vs_uri(Path("D:/Dev/Loment-DEV/x"))
    print(f"      登记修复：{ident}-0.1.0.old -> {dest.name}；别人的条目未动")


@test
def test_vscode_install_sweeps_stale_versions():
    """升级时**旧的版本目录必须被挪走** —— `extensions/` 下每个带 `package.json` 的目录
    都会被扫成一个扩展，同 ident 两份就各认一份 `.lomt`。

    2026-10-12 从 0.1.0 升到 0.2.0 时实测撞上的：`--install` 只挪 `dest`，
    旧目录名（版本号变了）原地不动，于是两份一起被扫进去 —— 而 `--doctor` 的
    "抢语言 id" 那一格**看不见它**（那循环跳过同 ident 的目录）。判据是纯函数那一层，
    不碰本机真的 `~/.vscode`。
    """
    with tempfile.TemporaryDirectory() as tds:
        extdir = Path(tds)
        ident = "hooyan2022.loment"
        dest = extdir / f"{ident}-0.2.0"
        for name in (f"{ident}-0.1.0", f"{ident}-0.2.0", f"{ident}-0.1.0.old",
                     "someoneelse.thing-1.0.0"):
            (extdir / name).mkdir()
        (extdir / f"{ident}-0.1.0" / "package.json").write_text("{}", encoding="utf-8")
        got = [p.name for p in vscode_ext.stale_dirs(extdir, ident, dest)]
        assert got == [f"{ident}-0.1.0", f"{ident}-0.1.0.old"], got
    print("      升级扫旧版：只认同 ident 的旧目录（不含 dest 自己、不含别人的扩展）")


@test
def test_vscode_doctor_names_same_name_other_publisher():
    """**同名、不同发布者**的目录：认出来点名，但**绝不自动删**。

    `stale_dirs` 按**当前 ident**（`<发布者>.<名>`）glob，而换发布者之后 ident 整个变了
    （`fujojtop.loment` → `hooyan2022.loment`），旧目录不在它的匹配范围里。
    2026-10-13 实测撞到：磁盘上两份都声明 `.lomt`/`.lom`，而 `--doctor` 只看得见症状
    （"别人也认 .lomt"），看不见那是**自己**的旧目录。

    **这一条最初写反过**：第一版让 `--install` 把它们一并挪走（判据第一版的期望值就是
    "两个都挪"），而它把**别人的同名扩展**也一起挪了 —— 判据当场抓出来。清单里没有任何
    字段能分开那两件事，所以现在的规矩是：**认出来给 doctor 点名，删不删由人**。
    这里同时钉住那两件事：① 该认的认得出；② 它**不在** `--install` 该挪的那一批里。
    """
    with tempfile.TemporaryDirectory() as tds:
        extdir = Path(tds)
        dest = extdir / "hooyan2022.loment-0.2.0"
        dest.mkdir()
        pkg = {"name": "loment", "publisher": "hooyan2022"}
        # 自己换发布者留下的（要**认出来**，但不自动删）
        (extdir / "fujojtop.loment-0.2.0").mkdir()
        (extdir / "fujojtop.loment-0.2.0" / "package.json").write_text(
            json.dumps({"name": "loment", "publisher": "FujoJTOP"}), encoding="utf-8")
        # 别人的同名扩展 —— 和上面那个**在清单里长得一模一样**，这正是不能自动删的理由
        (extdir / "someoneelse.loment-1.0.0").mkdir()
        (extdir / "someoneelse.loment-1.0.0" / "package.json").write_text(
            json.dumps({"name": "loment", "publisher": "someoneelse"}), encoding="utf-8")
        # 名字不同的（不该被认出来）
        (extdir / "fujojtop.other-0.1.0").mkdir()
        (extdir / "fujojtop.other-0.1.0" / "package.json").write_text(
            json.dumps({"name": "other", "publisher": "FujoJTOP"}), encoding="utf-8")
        got = sorted(p.name for p in vscode_ext.renamed_publisher_dirs(extdir, pkg, dest))
        # ① 两个同名不同发布者的都被认出来（doctor 会各自点名）
        assert got == ["fujojtop.loment-0.2.0", "someoneelse.loment-1.0.0"], got
        # ② 但它们**不在** `--install` 该挪的那一批里（那一批按当前 ident 走）
        ident = "hooyan2022.loment"
        swept = [p.name for p in vscode_ext.stale_dirs(extdir, ident, dest)]
        assert swept == [], f"`--install` 的自动清理不该碰这两个: {swept}"
    print("      同名不同发布者：doctor 逐个点名，但**不自动删**（分不开 _自己的旧目录_ 与"
          " _别人的同名扩展_）")


def main(argv: list[str] | None = None) -> int:
    return loment_suite.run(TESTS, "vscode_ext_test", only=(argv[0] if argv else None))


if __name__ == "__main__":
    sys.exit(main())
