#!/usr/bin/env python3
# loment_tools_test.py — P6 工具链自检 (M55–M66, docs/148)
#
# 运行: python tools/loment_tools_test.py   (退出码 0 = 全绿)
# 说明: 需要 rustc/clang 的用例在工具缺失时打印 SKIP 并计为通过 (环境问题不算回归);
#       纯静态判定不依赖外部工具。

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"
TOOLCHAIN = EX / "toolchain.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    return shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)


def _rustc() -> str | None:
    return shutil.which("rustc")


# ---------------------------------------------------------------- 单态化金标 (docs/156)

@test
def test_mono_trace_naming_rule():
    """单态化的命名规则不漂: base + "_" + "_".join(实参), 且全语料零违规。"""
    import mono_trace
    r = mono_trace.main(["--check"])
    assert r == 0, "mono_trace --check 失败 (命名规则漂了, 见 docs/156 §1)"


@test
def test_audit_claims_match_ci():
    """审计包 (docs/160) 不许长出"第二套判据": 它列的每个工具都必须在 ci.py 的静态门禁里。

    这条盯的是 M100 的审计工具本身 —— 审计工具自己跑一套没人核的命令, 是审计里
    最典型的失效方式。
    """
    import ci
    import loment_audit
    static = set(ci.STATIC_CHECKS)
    claimed = {tool for _, _, tool, _ in loment_audit.CLAIMS}
    missing = sorted(claimed - static)
    assert not missing, f"审计包引用了不在 ci.py 静态门禁里的工具: {missing}"
    ids = [c[0] for c in loment_audit.CLAIMS]
    assert len(ids) == len(set(ids)), "主张编号重复"
    # **条数要与 `docs/160` §1 那张表逐行对齐**（2026-09-22 第三方复核发现的现状：
    # 工具 24 条 / 表 20 行 / 标题说 23 / `docs/202` 与 `docs/145` 说 18 —— 四个数各说各话。
    # 原因是这里原先只钉 `>= 10` 这个**下限**：C21–C24 加进工具时表格没跟，没有任何东西会红。
    # 现在钉**相等**：加一条主张必须同时加一行表；表里多一行而工具里没有，也红。
    kit = (ROOT / "docs" / "160-loment-audit-kit.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*(C\d+)\s*\|", kit, re.M)
    assert rows == ids, (
        "docs/160 §1 的表与 loment_audit.CLAIMS 对不上:\n"
        f"  表  : {rows}\n"
        f"  工具: {ids}\n"
        "（加/删一条主张要同时改那张表 —— 见 docs/160 §1 顶上那段与 §2 第 11 条）")
    # 每条的 argv 必须显式写出模式 (默认无参的判据要写 [], 需要模式的两条写 --check)
    for cid, _, tool, args in loment_audit.CLAIMS:
        assert isinstance(args, list), f"{cid} 的 argv 必须是 list"


@test
def test_every_test_file_is_in_the_static_gate():
    """**登记进发布清单 ≠ 接进门禁** —— 两处都要登记，这里钉住第二处。

    `ci.py` 的 `STATIC_CHECKS` 是**硬编码的一个元组**（不扫目录），所以新加一份
    `tools/*_test.py` 却漏改那里时，那份判据**随包发得出去、门禁从来不跑它** ——
    而"整轮门禁跑过、我这边全绿"那句话在当时是**不成立**的（跑的是"我自己跑那条判据"）。

    与"发布 `GLOBS` 漏了就是静默隐形"**同一个形状**，只是换了一层：
    清单管"发不发得出去"，门禁管"跑不跑"。两处都登记，而这一条钉住两者一致。

    两个方向都钉（任一侧漂了都算失败）：

      * 有 `tools/<名>_test.py` 却不在 `STATIC_CHECKS` 里 -> **门禁从来不跑它**
      * `STATIC_CHECKS` 里的名字没有对应的 `tools/<名>.py` -> 门禁跑了个不存在的

    这是 2026-09-20 另一个会话（LumtUI 那条线）加自己那份判据时实测撞到的 ——
    它登记了清单、没登记门禁，而两边都没有判据管这件事。
    """
    import ci

    tools = Path(__file__).resolve().parent
    have = {q.stem for q in tools.glob("*_test.py")}
    static = set(ci.STATIC_CHECKS)
    never_run = sorted(have - static)
    assert not never_run, (
        f"这些判据在 tools/ 里、也多半在发布清单里，但**门禁从来不跑**（`ci.py` 的 "
        f"STATIC_CHECKS 是硬编码的元组）：{never_run}")
    ghost = sorted(n for n in static if not (tools / f"{n}.py").exists())
    assert not ghost, f"门禁里这些名字没有对应的工具文件：{ghost}"
    print(f"      门禁登记处与文件一致：{len(have)} 份判据全在 STATIC_CHECKS 里")


@test
def test_every_tool_is_in_the_release_manifest():
    """**第三处登记**：发布清单（`loment_release.GLOBS`）。

    上一节钉的是"接没接进门禁"，这一条钉"进没进发布清单"—— 两处都要登记，而**两个
    方向都会静默出错**（2026-09-22 加这条之前，两边都没有判据管）：

      * 被跟踪的 `tools/*.py` 不在 `GLOBS` 里 -> 它在仓里、在门禁里**跑**，却**不在
        发布清单上**。清单是"这一版发了什么、每一份的 sha256 是什么"那本账
        （`loment/build/release-manifest.json`，不是发行包的打包清单 —— 发行包由
        `loment_dist` 按自己的 `payload()` 装，里面**根本没有 `tools/`**）。**账上没有
        它的后果不是发不出去，而是它变了没人会知道** —— 这正是 `docs/188` §7.1
        加一门表层语法那张表里标着"**没有任何判据会抓**"的那一格
        （`loment-dev-86` 那次是人工看出来的）。
      * `GLOBS` 里某一条 glob 一个文件都匹配不到 -> 陈旧条目（`docs/152` 记过一次：
        旧树的 `docs/14*.md` 一类被搬过来后成了**五个指向不存在文件**的项）。

    实测（这条判据第一次跑）：13 个 `tools/*.py` 不在清单里，其中 4 个是判据 ——
    `loment_capasserts_test` / `loment_eol_test` / `loment_ffi_test` / `loment_syscalls_test`，
    它们都在 `ci.py` 的 `STATIC_CHECKS` 里跑，却不在清单上。

    **`docs/i18n/` 故意不管**：那是译者向的工件，`docs/i18n/glossary.md` §1 明写它
    "sits outside every release GLOB … and is not shipped"。这一格的"没进清单"
    **是设计**，不是漏 —— 别照这条判据去"修"它。
    """
    import loment_release

    tracked = subprocess.run(["git", "ls-files", "tools/*.py"], cwd=str(ROOT),
                             capture_output=True, text=True, shell=False)
    assert tracked.returncode == 0, f"git ls-files 失败: {tracked.stderr[:200]}"
    have = {q.strip() for q in tracked.stdout.splitlines() if q.strip()}

    covered: set[str] = set()
    dead: list[str] = []
    for g in loment_release.GLOBS:
        hits = {p.relative_to(ROOT).as_posix() for p in ROOT.glob(g) if p.is_file()}
        if not hits:
            dead.append(g)
        covered |= hits

    missing = sorted(have - covered)
    assert not missing, (
        f"这些被跟踪的 `tools/*.py` 不在 `loment_release.GLOBS` 里 —— 发布账本上"
        f"没有它们，于是它们变了也不会有人知道（`--check` 照样绿）：{missing}。"
        f"加进 GLOBS，并**与 `loment/tools/lomrel.lomt` 的 `globs_text()` 插入同一位置**"
        f"（两份逐条同序 —— `loment_rel_test::test_lomrel_globs_match_python_globs` "
        f"纯 Python 逐条比这两份清单，缺项/失序当场红）。")
    assert not dead, (
        f"`loment_release.GLOBS` 里这些条目一个文件都匹配不到（陈旧条目）：{dead}")
    print(f"      发布清单覆盖：{len(have)} 个 tools/*.py 一件不少，{len(loment_release.GLOBS)} 条 glob 无空转")


# ------------------------------------------------- 生成物不是一个**共享寄存器**（docs/254）

#: **整树生成物** —— 内容取决于 `GLOBS` 里**每一个**文件。`docs/manual/` 也算：它的
#: `index.md` 第 3 行是 `tools/lomentc.py` 的哈希戳（`loment_manual.compiler_version`），
#: 于是**任何两个都改了编译器的 PR** 也会在同一行上对撞 —— 而编译器正是本仓的主线。
WHOLE_TREE_ARTIFACTS = ("loment/build/release-manifest.json",
                        "loment/build/SHA256SUMS",
                        "docs/manual")


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "manifest.yml").read_text(encoding="utf-8")


def _add_list(job: str) -> str:
    """那个 job 里 `git add` 收的路径（本仓纪律是点名 add，不是 `-A`）。

    **先把反斜杠续行接起来**：`git add` 的路径表在 main 那一路是两行长（五件 + `docs`），
    只看第一行会漏掉续行上的整树生成物 —— 那正是这条要抓的东西。
    """
    flat = re.sub(r"\\\n\s*", " ", job)
    adds = re.findall(r"git add (.+)", flat)
    assert adds, "这个 job 里找不到 `git add`"
    return " ".join(a.strip() for a in adds)


def _whitelist_res(job: str) -> list[str]:
    """那个 job 白名单守卫用的正则，**去掉转义**再返回（好看它有没有放行某件生成物）。"""
    pats = re.findall(r"grep -v -E '([^']+)'", job)
    assert pats, "这个 job 里找不到白名单守卫"
    return [re.sub(r"\\(.)", r"\1", p) for p in pats]


@test
def test_pr_path_carries_no_whole_tree_artifact():
    """**PR 路径不许提交整树生成物** —— 这条就是"并发 PR 不再冲突"的那个结构保证。

    **它挡的是什么**：`release-manifest.json` 与 `SHA256SUMS` 记的是 `GLOBS` 里
    **每一个文件**的 sha256。这种文件放在 PR 路径上时，两个**各改互不相干文件**的 PR
    写的是它里面**相邻的行** —— 而 git 的合并上下文只有 3 行 ⇒ 必冲突。实测
    （`docs/254` §1）：两个分支各加一份不同的 doc，谁都没碰别人的东西，试合时冲突
    就在那两件台账上，别处干净；当时唯一开着的 `CONFLICTING` PR（#436）5 个文件里
    3 个是生成物，冲突点同样是它们。

    **为什么不是"少收几条"而是"整件挪走"**：见 `docs/254` §2 —— `merge=union` 在同一行
    两改时会留下同一路径的两条哈希（**静默损坏**），GitHub 侧又不跑自定义 merge driver；
    而"入库但不核新鲜度"等于存一份**没人核**的账，比不存更坏。

    **反向也钉**：PR 侧**必须**仍然重算与"这个 PR 改了什么"一一对应的那两件 —— 种子
    （随 `loment/selfhost/*.lomt` 变）与 `surface_data.lomt`（随内建表/诊断码变）。
    它们动了就是**真**冲突，该让人看见；一起挪走会让 PR 的树上带着过期种子。
    """
    job = _job_block(_workflow(), "emit")
    added = _add_list(job)
    bad = [a for a in WHOLE_TREE_ARTIFACTS if a in added]
    assert not bad, (
        f"PR 路径（`emit` job）又在提交整树生成物了：{bad}。它们的每一行都是**别人的"
        f"文件**，两个并发 PR 必在同一片相邻行上对撞 —— 见 `docs/254`。"
        f"重算它们的是 `emit-main`（`main` 上的单一写者），不是这里。")
    for pat in _whitelist_res(job):
        for a in WHOLE_TREE_ARTIFACTS:
            assert a not in pat, f"PR 侧的白名单放行了整树生成物 `{a}`：{pat!r}"
    for cmd in ("loment_seed.py --emit", "loment_diag.py --dump-surface"):
        assert cmd in job, (
            f"PR 侧不再重算 `{cmd}` —— 它是**与这次改动一一对应**的生成物，"
            f"不该静默退出 PR 路径")
    print("      PR 路径只带种子 + surface_data（与这次改动一一对应），不带整树生成物")


@test
def test_main_writer_is_not_behind_the_gate():
    """**`main` 上有一个单一写者，而且它不排在门禁后面。**

    **"不排在门禁后面"为什么要单独钉**：这条路上最容易写成的形状是
    `workflow_run: [gate]` 或 `needs: [static-*]` —— 两种都把重算**串在门禁之后**，
    于是每一次合并变成"先等门禁、再等重算"。2026-10-16 明确否掉了这个形状：重算只读树、
    只写生成物，没有任何理由等门禁，**并行**才是对的。

    **"写者存在"为什么要钉**：整树生成物从 PR 路径上撤下来之后，"台账与树一致"就
    **只**由这个写者保证（`lom_audit` 的 M93/M95 按滞后容差接着判，见 `docs/254` §3）。
    它被删掉或被改名，门禁不会有别的判据发现 —— 就是这一条。
    """
    src = _workflow()
    on_m = re.search(r"^on:\s*$", src, re.M)
    perms_m = re.search(r"^permissions:", src, re.M)
    assert on_m and perms_m, "`manifest.yml` 的 `on:` / `permissions:` 结构变了"
    assert "workflow_run" not in src[on_m.end():perms_m.start()], (
        "`manifest.yml` 挂到 `workflow_run` 上了 —— 那是「等门禁跑完再重算」的形状")

    job = _job_block(src, "emit-main")
    assert "refs/heads/main" in job, "`emit-main` 没有钉在 `main` 上"
    assert "github.event_name == 'push'" in job, "`emit-main` 不是由 `push` 触发的"
    assert not re.search(r"^ {4}needs:", job, re.M), (
        "`emit-main` 挂了 `needs:` —— 重算被排到别的 job 后面了（`docs/254` §3）")
    for cmd in ("loment_seed.py --emit",
                "loment_diag.py --dump-surface",
                "loment_manual.py --emit docs/manual",
                "loment_release.py --emit",
                "loment_release.py --checksums"):
        assert cmd in job, f"`emit-main` 少重算了一件：`{cmd}`"
    # **顺序是硬的**：清单记的是**那一刻的树**，所以它必须排在种子 / surface / 手册之后。
    order = [job.index(c) for c in ("loment_seed.py --emit",
                                    "loment_diag.py --dump-surface",
                                    "loment_manual.py --emit",
                                    "loment_release.py --emit",
                                    "loment_release.py --checksums")]
    assert order == sorted(order), (
        "`emit-main` 的重算顺序错了 —— 发布清单必须**最后**算，它记的是那一刻的树")
    added = _add_list(job)
    missing = [a for a in WHOLE_TREE_ARTIFACTS if a not in added]
    assert not missing, f"`emit-main` 没有把整树生成物收进提交：{missing}"
    # **推得动 `main` 的那个前提**：属主 PAT。`GITHUB_TOKEN` 不是 admin，推 `main` 会被分支
    # 保护拒掉（`enforce_admins` 是 false）—— 于是重算出来的东西**永远落不到 `main` 上**，
    # 而除了这一条，没有别的判据会发现（`docs/254` §3.2 与 §5 第 3 条）。
    assert "secrets.MANIFEST_TOKEN" in job, (
        "`emit-main` 推 `main` 用的不是 `secrets.MANIFEST_TOKEN` —— `GITHUB_TOKEN` 推不动"
        "（它不是 admin，会撞分支保护），而它推不动这件事本身不会报错到任何人面前")
    print("      main 侧单一写者：五件按硬顺序重算、与门禁并行、整树生成物收全、用属主 PAT 推")


# ---------------------------------------------------------------- M55 格式化

@test
def test_m55_fmt_idempotent_and_semantics():
    """格式化幂等 + 不改语义。**格式化后的副本写在本进程私有的临时目录**里。

    **为什么不能写在源同目录**：那样它就是一个 `loment/examples/*.lomt` 能匹配到的
    **仓库内临时文件**（原先叫 `._fmt_<名字>.lomt`）。原先赌的是"点开头的名字 glob
    匹配不到" —— 那是 **shell 与 `glob` 模块**的规矩，**`pathlib.Path.glob` 不遵守**
    （按 `fnmatch` 比，`*.lomt` 照样命中），于是它会被
    `loment_rel_test::test_manifest_order_is_platform_independent` 数进 GLOB 组里，
    报"这一组在清单里的次序不是字节序"（与 `loment_gc_surface._ladder_of` 那处同一个
    形状：两条判据分到同一片之后就成了偶发红）。副本**不必**挨着源：名字形式的 `use`
    按 `NAME_ROOTS` 解析，路径形式先试 `root`。
    """
    import lomfmt
    with tempfile.TemporaryDirectory(prefix="fmt-sem-") as tds:
        td = Path(tds)
        for p in sorted(EX.glob("*.lomt")):
            src = p.read_text(encoding="utf-8")
            f1 = lomfmt.format_source(src)
            assert lomfmt.format_source(f1) == f1, f"{p.name} 非幂等"
            tmp = td / p.name
            tmp.write_text(f1, encoding="utf-8")
            m1 = lomentc.load(p)
            d1 = lomentc.emit_potato(m1, ROOT, lomentc.resolve_deps(m1, ROOT, p.parent, entry=p))
            m2 = lomentc.load(tmp)
            d2 = lomentc.emit_potato(m2, ROOT, lomentc.resolve_deps(m2, ROOT, tmp.parent, entry=tmp))
            assert d1 == d2, f"{p.name} 格式化改变了语义"


# ---------------------------------------------------------------- M56 LSP

@test
def test_m56_lsp_three_capabilities():
    import loment_lsp
    uri = TOOLCHAIN.resolve().as_uri()
    docs: dict[str, str] = {}
    init = loment_lsp.handle({"id": 1, "method": "initialize", "params": {}}, docs)
    caps = init[0]["result"]["capabilities"]
    # **"在不在"而不是"真不真"**：`completionProvider` 现在是 `{}` —— 那是刻意的
    # （支持补全、但不广告触发字符，见 #28）。空 dict 在 Python 里是假值，
    # 拿它当真假判会红，而它表达的恰恰是"支持"。
    assert caps["definitionProvider"] and "completionProvider" in caps
    text = TOOLCHAIN.read_text(encoding="utf-8")
    msgs = loment_lsp.handle({"method": "textDocument/didOpen",
                              "params": {"textDocument": {"uri": uri, "text": text}}}, docs)
    assert msgs[0]["method"] == "textDocument/publishDiagnostics"
    assert msgs[0]["params"]["diagnostics"] == []
    items = loment_lsp.handle({"id": 2, "method": "textDocument/completion",
                               "params": {"textDocument": {"uri": uri}}}, docs)[0]
    labels = {i["label"] for i in items["result"]["items"]}
    assert {"fn", "u32", "fib", "gcd"} <= labels, sorted(labels)[:10]
    line = next(i for i, s in enumerate(text.splitlines()) if s.strip().startswith("fn "))
    col = text.splitlines()[line].index("fn") + 3
    res = loment_lsp.handle({"id": 3, "method": "textDocument/definition",
                             "params": {"textDocument": {"uri": uri},
                                        "position": {"line": line, "character": col}}}, docs)[0]
    assert res["result"]["range"]["start"]["line"] == line
    bad = "module m\nfn f() -> u32 { return g(1); }\n"
    d = loment_lsp.handle({"method": "textDocument/didOpen",
                           "params": {"textDocument": {"uri": "file:///bad.lomt", "text": bad}}},
                          docs)[0]
    assert d["params"]["diagnostics"], "错误文件应有诊断"


# ---------------------------------------------------------------- M57 包管理

@test
def test_m57_pkg_resolve_lock_verify_cycle():
    import lompkg
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for name, dep in (("base", None), ("mid", "base"), ("top", "mid")):
            d = base / name
            (d / "src").mkdir(parents=True)
            (d / "src" / "m.lomt").write_text(f"module {name}\nfn f() -> u32 {{ return 1; }}\n",
                                              encoding="utf-8")
            deps = {} if dep is None else {dep: {"path": f"../{dep}"}}
            (d / "pkg.json").write_text(json.dumps({"name": name, "version": "0.1.0",
                                                    "deps": deps}), encoding="utf-8")
        top = base / "top" / "pkg.json"
        pkgs = lompkg.resolve(top)
        assert [p["name"] for p in pkgs] == ["base", "mid", "top"], pkgs
        lock = base / "pkg.lock"
        assert lompkg.main(["resolve", str(top), "--lock", str(lock), "--write"]) == 0
        assert lompkg.main(["verify", str(top), "--lock", str(lock)]) == 0
        (base / "base" / "src" / "m.lomt").write_text("module base\nfn f() -> u32 { return 2; }\n",
                                                      encoding="utf-8")
        assert lompkg.main(["verify", str(top), "--lock", str(lock)]) == 1
        (base / "base" / "pkg.json").write_text(json.dumps(
            {"name": "base", "version": "0.1.0", "deps": {"top": {"path": "../top"}}}),
            encoding="utf-8")
        try:
            lompkg.resolve(top)
            assert False, "应当检测到依赖环"
        except ValueError as e:
            assert "环" in str(e)


# ---------------------------------------------------------------- M58 文档

@test
def test_m58_doc_from_lomt():
    import lomdoc
    text = lomdoc.render(lomentc.load(TOOLCHAIN), TOOLCHAIN.read_text(encoding="utf-8"),
                         str(TOOLCHAIN))
    for needle in ("# API: `toolchain`", "### `fn fib(n: u32) -> u32`",
                   "### `fn gcd(a: u32, b: u32) -> u32`", "斐波那契"):
        assert needle in text, needle
    cap_doc = lomdoc.render(lomentc.load(EX / "native_cap.lomt"),
                            (EX / "native_cap.lomt").read_text(encoding="utf-8"), "x")
    assert "## 能力域" in cap_doc and "`blk_write`" in cap_doc


# ---------------------------------------------------------------- M59 DWARF

@test
def test_m59_dwarf_line_table():
    mod = lomentc.load(TOOLCHAIN)
    deps = lomentc.resolve_deps(mod, ROOT, EX, entry=TOOLCHAIN)
    ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    assert "!llvm.dbg.cu" in ll and "!DISubprogram" in ll and "!DILocation" in ll
    assert any(l.startswith("define ") and "!dbg !" in l for l in ll.splitlines())
    assert ll.count("!dbg !") > 20, ll.count("!dbg !")
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang, 跳过 DWARF 落盘验证")
        return
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.ll"
        f.write_text(ll, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                            "-g", "-c", str(f), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr
        dump = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", "-l", str(obj)], capture_output=True, text=True, shell=False).stdout
        assert "toolchain.lomt:" in dump, "行表里没有源文件行号"


@test
def test_m59_dwarf_local_variables():
    """M59 (收口): 行表之外还要有**变量信息**。

    IR 里每个形参/局部都有 `DILocalVariable` 元数据 + 一条 `#dbg_declare` 调试记录;
    落盘后用 `llvm-objdump -d -l --debug-vars` (LLVM 自己的"调试器变量视图") 能在反汇编旁
    按源码行列出源码变量名。

    位置表达式在本工具链里显示成 `<unknown op DW_OP_fbreg>` —— **不是缺陷**: clang 22 自己
    发射的 C 源码产物在同一条命令下显示完全相同, 这是 llvm-objdump 的变量位置求值器在
    freestanding 目标上的限制 (要解析位置得有完整调试器), 详见 docs/145 的 M59 节。

    本用例同时钉住"非 debug 路径不长出调试元数据" —— 与"两后端逐字节等价"的判据配对。
    """
    mod = lomentc.load(TOOLCHAIN)
    deps = lomentc.resolve_deps(mod, ROOT, EX, entry=TOOLCHAIN)
    ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    assert "!DILocalVariable(" in ll, "缺变量元数据"
    assert "#dbg_declare(" in ll, "缺变量声明记录"
    assert "!DIBasicType(" in ll, "缺变量类型"
    assert '!DILocalVariable(name: "n", arg: 1, scope:' in ll, "形参没标 arg"
    for nm in ("a", "b", "i", "t"):   # 局部不带 arg
        assert f'!DILocalVariable(name: "{nm}", scope:' in ll, nm
    plain = lomentc.emit_llvm(mod, ROOT, deps)
    assert "!DILocalVariable" not in plain and "#dbg_declare" not in plain, \
        "非 debug 路径不该有调试元数据"
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang, 跳过 --debug-vars 落盘验证")
        return
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.ll"
        f.write_text(ll, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                            "-g", "-c", str(f), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr
        objdump = shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe"
        dump = subprocess.run([objdump, "-d", "-l", "--debug-vars=ascii", str(obj)],
                              capture_output=True, text=True, shell=False).stdout
        assert "toolchain.lomt:" in dump, "行表里没有源文件行号"
        fib = dump.split("<fib>:")[1].split("\n000")[0] if "<fib>:" in dump else ""
        assert fib, "反汇编里没有 fib"
        for nm in ("n", "a", "b", "i", "t"):   # 变量视图必须逐个列出
            assert f"- {nm} = " in fib, f"变量视图里没有 {nm}"


@test
def test_m59_debug_ir_compiles_across_corpus():
    """M59 回归: 带 `--debug` 的 IR 必须**仍是合法 IR** —— 对含 match/枚举/泛型/数组的语料
    逐个 `clang -g` 编译。

    这条盯的是一个**既有 bug**(M59 收口时抓到): 多行 `switch` 被 `!dbg` 后缀逐行污染
    (`switch i32 %x, label %L [, !dbg !7`), 只有 `--debug` + `match` 才触发, 旧用例只喂
    没有 match 的 `toolchain.lomt`, 从未覆盖。修法是让续行走 `w_raw`(不加后缀),
    `!dbg` 只挂整条指令末尾的 `]`。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    import subprocess
    files = [TOOLCHAIN] + [EX / n for n in
                           ("native_res.lomt", "demo.lomt", "native_agg.lomt", "native_gen.lomt")]
    bad = []
    with tempfile.TemporaryDirectory() as td:
        for src in files:
            mod = lomentc.load(src)
            deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
            ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
            assert "!DILocalVariable(" in ll, f"{src.name}: 没发变量信息"
            f = Path(td) / (src.stem + ".ll")
            f.write_text(ll, encoding="utf-8")
            r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                                "-g", "-c", str(f), "-o", str(Path(td) / (src.stem + ".o"))],
                               capture_output=True, text=True, shell=False)
            if r.returncode != 0:
                tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "?"
                bad.append(f"{src.name}: {tail}")
    assert not bad, "带 --debug 的 IR 编译不过: " + "; ".join(bad)
    print(f"      {len(files)} 个语料的 --debug IR 都能被 clang -g 编译")


# ---------------------------------------------------------------- M30/M31/M32 裸机

@test
def test_m30_bare_metal_object_and_link():
    """M30/M31/M32: `x86_64-unknown-none` 目标 → 无 libc 依赖的对象 → 按脚本链成映像。

    这条链在此之前只有文档里的手工命令 (docs/145 的 P3 证据), 没有测试看着,
    会随编译器漂移而静默失效 —— 这里把它钉成判据。
    """
    import subprocess
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    lld = r"C:\Program Files\LLVM\bin\ld.lld.exe"
    if not Path(lld).exists():
        print("      SKIP: 无 ld.lld")
        return
    nm = shutil.which("llvm-nm") or r"C:\Program Files\LLVM\bin\llvm-nm.exe"
    objdump = shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe"
    ld_script = ROOT / "loment" / "build" / "loment.ld"
    assert ld_script.exists(), "缺链接脚本 loment/build/loment.ld"
    entry = EX / "native_entry.lomt"
    with tempfile.TemporaryDirectory() as td:
        mod = lomentc.load(entry)
        deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
        ll = Path(td) / "entry.ll"
        ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
        obj = Path(td) / "entry.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                            "-nostdlib", "-c", str(ll), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        # M31: 对象里不能有未定义符号 (运行时是内联的 __loment_memcmp/memset/abort)
        undef = subprocess.run([nm, "-u", str(obj)], capture_output=True,
                               text=True, shell=False).stdout.strip()
        assert undef == "", f"裸机对象有未定义符号 (M31 回归): {undef[:200]}"
        # M32: 按脚本链接
        elf = Path(td) / "entry.elf"
        r = subprocess.run([lld, "-T", str(ld_script), str(obj), "-o", str(elf)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        syms = subprocess.run([nm, str(elf)], capture_output=True, text=True,
                              shell=False).stdout
        assert "_start" in syms and "timer_isr" in syms, syms[:200]
        # 脚本布局: 最低的 text 符号必须正好落在 1 MiB (ENTRY(_start) 生效则入口 == _start)
        text_addrs = [int(l.split()[0], 16) for l in syms.splitlines()
                      if len(l.split()) == 3 and l.split()[1] in ("t", "T")]
        assert min(text_addrs) == 0x100000, f"text 没落在脚本的 1 MiB: {min(text_addrs):#x}"
        addr = int(next(l.split()[0] for l in syms.splitlines()
                        if l.split()[-1] == "_start"), 16)
        assert 0x100000 <= addr < 0x101000, f"_start 不在第一页 text 里: {addr:#x}"
        head = subprocess.run([objdump, "-f", str(elf)], capture_output=True,
                              text=True, shell=False).stdout
        assert f"start address: 0x{addr:016x}" in head, head[:300]
        # 链接产物同样不能有未定义符号 (整套 = 一个能独立跑的映像)
        undef2 = subprocess.run([nm, "-u", str(elf)], capture_output=True,
                                text=True, shell=False).stdout.strip()
        assert undef2 == "", f"链接产物有未定义符号: {undef2[:200]}"
        print(f"      裸机链: {obj.stat().st_size}B 对象 (0 未定义) -> "
              f"{elf.stat().st_size}B 映像, _start @{addr:#x}")


# ---------------------------------------------------------------- M60–M63 CLI

@test
def test_m60_ir_viewer():
    import loment
    assert loment.main(["ir", str(TOOLCHAIN)]) == 0
    if _clang():
        assert loment.main(["ir", str(TOOLCHAIN), "--objdump"]) == 0


@test
def test_m61_test_framework():
    import loment
    assert loment.main(["test", str(TOOLCHAIN)]) == 0


@test
def test_m62_bench_table():
    import loment
    assert loment.main(["bench", str(TOOLCHAIN), "--n", "20000"]) == 0


@test
def test_m63_coverage():
    import loment
    assert loment.main(["cov", str(TOOLCHAIN), "--call", "cov_main"]) == 0


# ---------------------------------------------------------------- M64 诊断

SNIPPETS = [
    ("类型不匹配", "module m\nfn f() -> u32 { let x: u32 = true; return x; }\n"),
    ("未声明符号", "module m\nfn f() -> u32 { return g(1); }\n"),
    ("实参数量", "module m\nfn g(a: u32) -> u32 { return a; }\n"
                 "fn f() -> u32 { return g(1, 2); }\n"),
    ("能力域", "module m\nfn f() -> u32 { guard c(0); return 0; }\n"),
    ("出界冲突", 'module m\ncapability c : disk[0..1]\nexcluded "disk: none"\n'
                 "fn f() -> u32 { guard c(0); return 0; }\n"),
    ("移动后使用", "module m\nstruct S { a: u32 }\n"
                   "fn f() -> u32 { let s: S = S { a: 1 }; let t: S = s; return s.a; }\n"),
    ("借用冲突", "module m\nfn g(a: mut [u32], b: [u32]) -> u32 { return 0; }\n"
                 "fn f() -> u32 { let a: [u32; 2] = [1, 2]; return g(&mut a, &a); }\n"),
    ("match 非法", "module m\nenum E { A, B }\nfn f(e: E) -> u32 { match e { E::A => { return 1; } } "
                   "return 0; }\n"),
    ("结构体字段", "module m\nstruct S { a: u32, b: u32 }\n"
                   "fn f() -> u32 { let s: S = S { a: 1 }; return s.a; }\n"),
    ("? 误用", "module m\nfn h() -> u32 { return 1; }\n"
               "fn f() -> u32 { return h()?; }\n"),
    ("只读切片写入", "module m\nfn f(xs: [u32]) -> u32 { xs[0] = 1; return 0; }\n"),
    ("悬垂借用", "module m\nfn f() -> [u32] { let a: [u32; 2] = [1, 2]; return &a; }\n"),
    ("重名", "module m\nfn f() -> u32 { return 1; }\nfn f() -> u32 { return 2; }\n"),
]


@test
def test_m64_diagnostics_have_codes_and_hints():
    import loment_diag
    codes = set()
    for name, src in SNIPPETS:
        errs = lomentc.check(lomentc.Parser(lomc.lex(src), src).parse())
        assert errs, f"{name}: 应当报错"
        diags = loment_diag.diagnose(errs)
        for d in diags:
            assert d["code"] != "E999", f"{name}: 未分类 {d['message']}"
            assert d["hint"], f"{name}: 缺建议"
            codes.add(d["code"])
    assert len(codes) >= 10, sorted(codes)


@test
def test_m64_all_reference_messages_are_classified():
    """分类表**完整覆盖**参考实现能发出的每一条消息模板。

    为什么要这条: 自举 checker 与参考实现的对照判据是"码集相等", 而码是从消息**分类**
    来的 —— 分类表漏一条, 那条规则在对照里就变成"两边都看不见"(都成了 E999 被丢掉),
    缺口会**静默消失**。这里用 ast 把 lomentc.py 里的消息模板抽出来, 逐个渲染成样例
    消息再分类, 任何一条落到 E999 就算回归。

    **2026-09-17 补第二个通道**: 原先只抽 `errs.append(...)`（`check()` 的语义错误）,
    于是 `raise LomError(...)`（词法/解析/装载期）**一条都没被覆盖过** —— 实测 50 条里
    39 条从来没进过分类表, 而它们恰恰是新手最先撞上的那批（`顶层只允许 …`、
    `未知顶层关键字 …`、`pub 之后需要一项声明`、`数组长度必须为正`）。
    判据只盯一个通道, 另一个通道的缺口就是**静默**的 —— 这正是这条判据自己要防的那种事。
    """
    import ast
    import loment_diag
    tree = ast.parse((ROOT / "tools" / "lomentc.py").read_text(encoding="utf-8"))
    numeric = ("line", "col", "len", "lo", "hi", "value")

    def sample(arg) -> str | None:
        """模板表达式 -> 样例消息（数值字段给 1，其余给 Foo）。"""
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            parts: list[str] = []
            for v in arg.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    src = ast.unparse(v.value)
                    parts.append("1" if any(w in src for w in numeric) else "Foo")
            return "".join(parts)
        return None

    tpls: list[str] = []
    for node in ast.walk(tree):
        # 通道一: `errs.append(消息)` —— `check()` 的语义错误
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "errs" and node.args):
            s = sample(node.args[0])
            if s is not None:
                tpls.append(s)
        # 通道二: `raise LomError(行, 列, 消息)` —— 词法/解析/装载期
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", "") == "LomError"
                and len(node.exc.args) >= 3):
            s = sample(node.exc.args[2])
            if s is not None:
                tpls.append(s)
    assert len(tpls) >= 100, f"抽取到的模板太少, 抽取逻辑可能坏了: {len(tpls)}"
    bad = []
    for t in tpls:
        for line in t.split("\n"):                     # 多行 f-string: 逐行判
            s = line.strip()
            if not s:
                continue
            if loment_diag.classify(s)[0] == "E999":
                bad.append(s)
    assert not bad, "未分类的参考消息模板:\n  " + "\n  ".join(sorted(set(bad)))
    print(f"      参考消息模板 {len(tpls)} 条全部有错误码")


@test
def test_foreign_note_carries_the_language_card():
    """`foreign_note` 那条路（外源文件 + 有错）必须把**语言卡的边界与调用约定**也带上。

    **这条是被实测逼出来的**：加卡的那一版在 f-string 里插了一个从没定义过的 `NL` ——
    而那段代码只有"外源文件 + 有错"才走到，我自己那几条判据一条都没覆盖到它，
    是另一条门的 `loment_multisyntax_test` 先报的 `NameError`。判据的覆盖面缺口和代码的
    缺口是同一件事的两面（docs/182 1.9 那条"消费者清单要和判据一起长"）。

    顺带钉住"卡上的话真的被送出去了" —— 只断言"没崩"会漏掉"卡是拿到了但没打印"。
    """
    import loment_diag
    try:                                    # 见上一条的说明：翻译线模块编不过时**可见跳过**
        import potato_from                                          # noqa: F401
    except Exception as e:                                     # noqa: BLE001
        print(f"        [SKIP] import potato_from 失败，这条查不了：{e}")
        return
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "cflow.lomt"          # 名字是 .lomt、内容是 C —— 那个已知处境
        f.write_text("#include <stdio.h>" + "\n"
                     + "int main(void) { return 0; }" + "\n",
                     encoding="utf-8", newline="\n")
        note = loment_diag.foreign_note(f, ["1:1: 非法字符 '#'"])
    assert note, "外源文件没给出提示"
    assert "不是 Loment" in note, note[:200]
    card = loment_diag.LANG_CARDS["c"]
    assert card.edge in note, "语言卡上的**边界**没送到"
    assert card.abi in note, "语言卡上的**调用约定**没送到"
    print("      foreign_note 带上了语言卡的边界与调用约定")

@test
def test_code_tables_cover_the_same_codes():
    """`RULES`（分类）与 `ASCII_ONE_LINER`（CLI 一行式）**键集必须相等**。

    这两张表是同一件事的**两个受众**（中文给人看诊断，ASCII 给 CLI —— CLI 输出必须纯 ASCII，
    `docs/169`），所以是两份文字、**不是两份清单**。但"加一个码忘了加另一种文字"正是本仓
    反复撞的那类静默缺口（`docs/179` §7.3），所以键集相等要有判据钉着。
    """
    import loment_diag
    ruled = {loment_diag.code_num(c) for c, _p, _t, _h in loment_diag.RULES}
    ascii_ = set(loment_diag.ASCII_ONE_LINER)
    assert ruled == ascii_, (
        f"只在 RULES 里: {sorted(ruled - ascii_)}; 只在 ASCII 表里: {sorted(ascii_ - ruled)}")
    assert 0 not in ruled, "有码解不出数字"
    # **编码必须从 1 起连续**: `surface_data.n_codes()` 给的是**条数**, 而两个消费者
    # (`lomcli` 的 `codes`/`explain`, 循环 1..n_codes()) 拿它当**上界**用 —— 那只有在
    # 连续编号时才等价。留个空号(比如只有 E24 没有 E25)会让两边静默错位。
    assert ruled == set(range(1, max(ruled) + 1)), (
        f"码不是连续编号, 缺: {sorted(set(range(1, max(ruled) + 1)) - ruled)}")
    print(f"      两张码表键集相等 ({len(ruled)} 条: E{min(ruled)}–E{max(ruled)})")


@test
def test_every_code_has_a_card_with_at_least_three_fixes():
    """每个码都要有一张说明卡，且**修法至少三条** —— 这是用户定的门槛。

    「错了什么 / 为什么错 / 怎么改(≥3) / 支持与不支持」是报错器渲染的东西（docs/182 §11）。
    少了卡就是渲染出一条"只有标题"的诊断，而用户会把它读成"这条没有更多可说的了"；
    只有一条修法等于没有选择 —— 门槛定在三条，正是"你自己按情况挑"的前提。

    `FIX_STRIDE` 是 8：平键 `c*8+i` 决定了**一条码最多 8 条修法**，超了就静默被挤掉，
    所以上界也要钉（不然第 9 条会消失在生成器里，而生成器不会吭声）。
    """
    import loment_diag
    ruled = {loment_diag.code_num(c) for c, _p, _t, _h in loment_diag.RULES}
    assert set(loment_diag.CARDS) == ruled, (
        f"只在 RULES 里(没卡): {sorted(ruled - set(loment_diag.CARDS))}; "
        f"只在卡里: {sorted(set(loment_diag.CARDS) - ruled)}")
    bad_n = {c: len(k.fixes) for c, k in loment_diag.CARDS.items() if len(k.fixes) < 3}
    assert not bad_n, f"修法少于三条: {bad_n}"
    too_many = {c: len(k.fixes) for c, k in loment_diag.CARDS.items() if len(k.fixes) > 8}
    assert not too_many, f"修法超过 FIX_STRIDE(8), 会被静默挤掉: {too_many}"
    empty = [c for c, k in loment_diag.CARDS.items()
             if not (k.what and k.why and k.yes and k.no)]
    assert not empty, f"四段里有空的: {empty}"
    n = sum(len(k.fixes) for k in loment_diag.CARDS.values())
    print(f"      {len(ruled)} 个码各有一张卡, 修法共 {n} 条 (每条码 >= 3)")


@test
def test_the_english_tables_are_complete_and_pure_ascii():
    """英文那几张表（**默认语言**）与中文那份一一对应，且**全 ASCII**。

    英文是默认（`docs/182` §14），所以"只写了中文"的症状发生在**默认路径**上：
    输出里没有标题（`error[E002]: ` 后面空着）或四段一起不见 —— 而"只有码号"的诊断
    看起来像"这条没有更多可说的了"，比不报还坏。

    四样一起钉：
      * **键集相等** —— 加一个码只补了中文，英文默认下那条就残；
      * **段不空** —— `what` / `why` / `yes` / `no` 都不许空串；
      * **修法条数相等** —— `code_nfix` 只报一个数（语言不进它的键），少一条会被
        **静默挤掉**；上界 8 也是（`FIX_STRIDE`）；
      * **纯 ASCII** —— 这是"默认输出不再是乱码"那一半的**前提**（`docs/169` §3a：
        936 代码页），夹一个 `→` 或 `“”` 进来，换默认语言这个改动就白做了。
    """
    import loment_diag
    ruled = {loment_diag.code_num(c) for c, _p, _t, _h in loment_diag.RULES}
    assert set(loment_diag.TITLE_EN) == ruled, (
        f"英文标题缺: {sorted(ruled - set(loment_diag.TITLE_EN))}; "
        f"多出来: {sorted(set(loment_diag.TITLE_EN) - ruled)}")
    assert set(loment_diag.CARDS_EN) == ruled, (
        f"英文卡缺: {sorted(ruled - set(loment_diag.CARDS_EN))}; "
        f"多出来: {sorted(set(loment_diag.CARDS_EN) - ruled)}")
    bad_n, empty, nonascii = {}, [], []
    for c, k in loment_diag.CARDS_EN.items():
        if len(k.fixes) != len(loment_diag.CARDS[c].fixes):
            bad_n[c] = (len(k.fixes), len(loment_diag.CARDS[c].fixes))
        if len(k.fixes) < 3 or len(k.fixes) > 8:
            bad_n[c] = len(k.fixes)
        for i, fx in enumerate(k.fixes):
            if not fx:
                empty.append((c, f"fix{i}"))
            if any(ord(ch) > 127 for ch in fx):
                nonascii.append((c, f"fix{i}"))
        for f in ("what", "why", "yes", "no"):
            v = getattr(k, f)
            if not v:
                empty.append((c, f))
            if any(ord(ch) > 127 for ch in v):
                nonascii.append((c, f))
    for c, t in loment_diag.TITLE_EN.items():
        if not t:
            empty.append((c, "title"))
        if any(ord(ch) > 127 for ch in t):
            nonascii.append((c, "title"))
    assert not bad_n, f"英文修法条数与中文不等 / 越界 (期望与中文一致且在 3..8): {bad_n}"
    assert not empty, f"英文表里有空段: {empty}"
    assert not nonascii, (
        f"英文表里有非 ASCII (936 控制台上就是乱码, 默认语言换过去就白换了): {nonascii}")
    n = sum(len(k.fixes) for k in loment_diag.CARDS_EN.values())
    print(f"      {len(ruled)} 条英文卡与中文一一对应, 修法 {n} 条, 全 ASCII")


@test
def test_language_cards_cover_every_language_the_frontends_know():
    """`LANG_CARDS` 的键集必须 == `potato_from.LANGS` —— **翻译线加语言, 这里要跟上**。

    报错器在"这个文件不是 Loment"时给的那一段，内容全来自 `LANG_CARDS`（走
    `--dump-surface` 生成给自举侧）。翻译线加一门语言而这里没跟上，症状是**新语言的文件
    报错时只字不提怎么翻** —— 静默缺口，没有任何东西会红。

    这就是 `docs/158` §5 那条"**消费者清单要和判据一起长**"：语言集是同一份清单，
    `potato_from.LANGS` 与推送它的 `LANG_CARDS` 必须一起动。红了就照现有那张卡补一张。
    """
    import loment_diag
    try:
        import potato_from
    except Exception as e:                                     # noqa: BLE001
        # **跳过而不是红**：这不是"环境缺了东西"，而是**翻译线那个模块此刻编不过** ——
        # 那是它自己的判据该报的（`loment_multisyntax_test` 一进来就会崩），这里再报一遍
        # 只是把同一个故障数两遍，还会让"翻译线在制品"看起来像"报错器的卡漏了"。
        # 但它**必须是看得见的**（下面这行会进日志），不能一声不吭地绿。
        print(f"        [SKIP] import potato_from 失败，这条查不了语言覆盖：{e}")
        return
    want = set(potato_from.LANGS)
    got = set(loment_diag.LANG_CARDS)
    assert got == want, (
        f"LANGS 里有而 LANG_CARDS 没有: {sorted(want - got)} —— 照现有那张卡补一张; "
        f"LANG_CARDS 里多出来的: {sorted(got - want)}")
    print(f"      {len(want)} 门语言都有卡 ({sorted(want)})")
    # 英文那一半（`LANG_EDGE_EN` / `LANG_ABI_EN`）**同一把尺子**：键集相等、不空、全 ASCII。
    # 这两段是"这个文件不是 Loment"时唯一的可执行建议，缺一门就是"新语言的文件报错时
    # 只字不提怎么翻"—— 与中文侧缺一门是同一个缺口，只是它落在**默认语言**上。
    for name, tbl in (("LANG_EDGE_EN", loment_diag.LANG_EDGE_EN),
                      ("LANG_ABI_EN", loment_diag.LANG_ABI_EN)):
        assert set(tbl) == want, (
            f"{name} 缺: {sorted(want - set(tbl))}; 多出来: {sorted(set(tbl) - want)}")
        empty = [k for k, v in tbl.items() if not v]
        assert not empty, f"{name} 里有空段: {empty}"
        bad = [k for k, v in tbl.items() if any(ord(ch) > 127 for ch in v)]
        assert not bad, f"{name} 里有非 ASCII: {bad}"


@test
def test_ui_strings_have_both_languages():
    """报错器的**界面字串**两门语言都有，键集相同且从 1 起连续。

    这些是渲染器自己的骨架字串（标签、说明句、用法、汇总），**不在** `surface_data` 里 ——
    lomcli 看不见它们，没必要让 CLI 的码表背这份负担。所以上面那几条表判据盖不到它们。
    漏一条的症状是某种语言下渲染出一个**空标签**（`": 值"`），看着像渲染坏了。

    直接读 `lomenterr.lomt`：`ui_en` 与 `ui_zh` 的 `if k == N { return ... }` 键集必须相等、
    且从 1 起连续（跳号 = 加了键忘了写，或者写了一个谁都读不到的键）。
    在源码层面就判得动，不必编一次再跑 —— 也避免了"某个键在两种语言下恰好是同一个 ASCII 串"
    这种把判据骗过去的巧合。
    """
    import re
    src = (ROOT / "loment" / "tools" / "lomenterr.lomt").read_text(encoding="utf-8")

    def keys(fn: str) -> set[int]:
        m = re.search(r"fn " + fn + r"\(k: u32\) -> str \{(.*?)\n\}", src, re.S)
        assert m, f"找不到 {fn}"
        return {int(x) for x in re.findall(r"if k == (\d+) \{ return ", m.group(1))}

    en, zh = keys("ui_en"), keys("ui_zh")
    assert en == zh, f"只有英文: {sorted(en - zh)}; 只有中文: {sorted(zh - en)}"
    assert en == set(range(1, max(en) + 1)), (
        f"界面字串键不从 1 起连续, 缺: {sorted(set(range(1, max(en) + 1)) - en)}")
    print(f"      {len(en)} 个界面字串两门语言齐全 (1..{max(en)})")


@test
def test_surface_data_is_up_to_date():
    """`loment/tools/surface_data.lomt` 是**生成物**，必须与重新生成的结果逐字节相同。

    它是 `docs/176` B 那条管线（数据从逻辑里拆出来，自举侧 `use` 它）的第一片，
    也是 `docs/182` §5.2 消掉"同一份码表抄第二份"的落点。**能藏的前提是可复现** ——
    判据就是那个前提（同 `loment_seed_test` 那条"种子 == 参考实现的产物"）。
    """
    import loment_diag
    p = ROOT / "loment" / "tools" / "surface_data.lomt"
    assert p.exists(), f"缺 {p} (python tools/loment_diag.py --dump-surface {p})"
    want = loment_diag.surface_lomt()
    got = p.read_text(encoding="utf-8")
    assert got == want, "surface_data.lomt 过期 (重跑 --dump-surface)"
    # 生成的**是 Loment 源码**, 所以要能编 —— 转义写错会在这里暴露
    errs = lomentc.check(lomentc.load(p), deps=[])
    assert not errs, errs[:3]
    print(f"      surface_data.lomt 最新且可编译 ({len(want)}B)")


@test
def test_m64_structured_diagnostics_are_jsonl():
    """`--diag-out` 吐的是**一行一条 JSON**，字段齐全，且**两条报错通道都走同一条路**。

    报错器 `lomenterr` 的输入就是它（`docs/182` §5）。这条钉三件：

      * **一行一条**（不是一个大数组）—— 边报边写，崩在半路也已经落盘；
      * **字段集与 `DIAG_FIELDS` 一致** —— 两个实现与下游报错器共用这一份形状；
      * **`check()` 的语义错与 `LomError` 的解析错都要有** —— 只测一条通道，另一条的缺口
        就是静默的。实测：加上解析那条通道之后，`LomError` 里 39/50 条从没有过错误码。

    **`title`/`hint` 不在这份输出里**（`docs/182` §5.3）：编译器只报"是什么、在哪"，
    标题与修复建议由报错器查 `surface_data` 补 —— 两个实现的消息文本本来就不同，
    硬凑"逐字节一致"只会造出假一致。
    """
    cases = [
        # (源码, 期望的码) —— 一条走 check(), 一条走 LomError
        ("module m\n\nchoose std\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n", "E022"),
        ("module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n", "E019"),
    ]
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for i, (src, want) in enumerate(cases):
            f = td / f"c{i}.lomt"
            f.write_text(src, encoding="utf-8", newline="\n")
            out = td / f"d{i}.jsonl"
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                                str(f), "--check", "--diag-out", str(out)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=120)
            assert r.returncode == 1, (src, r.returncode, r.stderr[-200:])
            lines = [x for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
            assert lines, f"没写出诊断: {r.stderr[-200:]}"
            recs = [json.loads(x) for x in lines]   # 一行一条，每行都是完整 JSON
            for d in recs:
                assert set(d) == set(lomentc.DIAG_FIELDS), (set(d), lomentc.DIAG_FIELDS)
                assert d["code"] != "E999", d
            assert recs[0]["code"] == want, (recs[0]["code"], want)
    print("      结构化诊断: 一行一条 JSON, 字段齐全, check/LomError 两条通道都覆盖")


@test
def test_e019_parse_errors_are_classified():
    """解析期消息有自己的码 (E019), 而且**不抢** E001 的活。

    原先解析期消息一条都不在分类表里 —— `loment diag` 把它们显示成 E999「未分类, 请报告」,
    可它恰恰是新手最常撞上的一类 (`match` 臂写成表达式、无值 `return`、漏分号)。
    这条同时钉住**顺序**: E001 的模式里有「期望」二字, E019 若排在它前面就会把类型错也吞掉。
    """
    import loment_diag
    assert loment_diag.classify("5:1: 期望 ;，得到 'capability'")[0] == "E019"
    assert loment_diag.classify("7:17: 期望 {，得到 '0'")[0] == "E019"
    assert loment_diag.classify("4:11: 期望表达式，得到 ';'")[0] == "E019"
    assert loment_diag.classify("实参类型 u32，期望 bool")[0] == "E001"
    assert loment_diag.classify("调用未定义的函数 pick")[0] == "E002"


@test
def test_cli_reports_parse_errors_without_traceback():
    """`loment ir <语法错的文件>` 给一行 `[ERR] 行:列: ...`、退出码 1 —— 不是 Python 回溯。

    2026-09-15 用户实测: 指南里最容易踩的两种写法 (漏分号 / 无值 `return`) 当时全都只看到
    traceback。对"照指南写第一个程序"的人, 这是最坏的第一印象, 而且它看起来像编译器崩了,
    而不是"你写错了"。
    """
    cases = {"漏了分号": "module a\n\nconst X: u32 = 3\n",
             "无值 return": "module b\n\nfn f() {\n    return;\n}\n"}
    with tempfile.TemporaryDirectory() as td:
        for name, src in cases.items():
            p = Path(td) / "bad.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment.py"), "ir", str(p)],
                               capture_output=True, text=True, shell=False)
            assert r.returncode == 1, f"{name}: 期望退出码 1, 得到 {r.returncode}"
            assert r.stderr.startswith("[ERR] "), f"{name}: stderr={r.stderr[:120]!r}"
            assert "Traceback" not in r.stderr, f"{name}: 甩了 Python 回溯\n{r.stderr}"


@test
def test_m81_builtin_tables_match():
    """自举 checker 的内建名清单必须与参考实现 `lomentc.BUILTINS` 同集合。

    checker.lomt 里的 `is_builtin` 是一张**手写的空格分隔字符串**, 参考实现是一张 dict ——
    两边漂移的后果很具体: 新加一个内建而没同步, 调用点会被自举 checker 报成
    "未定义的函数"(假阳性), 而字节一致判据看不出来 (那是编译器后端的事)。
    """
    src = (ROOT / "loment" / "selfhost" / "checker.lomt").read_text(encoding="utf-8")
    m = re.search(r'let names: str = "([^"]+)"', src)
    assert m, "checker.lomt 里没有内建名表"
    mine = set(m.group(1).split())
    want = set(lomentc.BUILTINS) | {"slice_len"}
    assert mine == want, f"只在 checker: {sorted(mine - want)}; 只在参考: {sorted(want - mine)}"
    print(f"      内建名表 {len(mine)} 个一致 (含单列的 slice_len)")


@test
def test_tool_memory_layout_stays_inside_cap():
    """**每个 `loment/tools/` 工具的"内存布局"要落在它自己声明的 `M_CAP` 之内。**

    为什么需要一条判据盯着它: 那套布局是**手工维持**的 —— `const` 只吃整数字面量，
    所以 `TB_*` 这些表偏移只能一个个写死（见 `lompotato.lomt` 布局块里那句注释）。
    "加一张表忘了抬 `M_CAP`"、"改基址时算错一格"都会**静默**越界，而越界的写
    **在 Windows 上看不出来**: 堆有余量、把它吞了；Linux 上 `brk` 区域之外没有映射，
    直接 SIGSEGV。

    这不是假设 —— 2026-09-22 把门禁搬上 Linux runner 时，`lompotato` 的 `TB_DL`
    正是这么崩的: 那一组表偏移整体偏高 65536（从 475136 起而不是从 409600 起），
    最后一张落在 **847024 > M_CAP 832512**。于是 `enums` 里只要有一个非空的
    `variants`（那是**唯一**会用到 `TB_DL` 的路径）就段错误 —— 而同一份判据在
    Windows 上一直是绿的。

    查两件事:

    1. 每个 `M_X` 落在 `M_CAP` 内；有配对的 `X_CAP` 时，`M_X + X_CAP` 也要在界内；
    2. 名字表 `TB_*` **等距**（间距 == `TA`）、起点是表区起点 `M_TB`、且**最后一张
       表的末尾**也在界内。
    """
    pat = re.compile(r"^const\s+([A-Z][A-Z0-9_]*)\s*:\s*u32\s*=\s*(\d+)\s*;", re.M)
    bad: list[str] = []
    checked = 0
    for p in sorted((ROOT / "loment" / "tools").glob("*.lomt")):
        c = {m.group(1): int(m.group(2))
             for m in pat.finditer(p.read_text(encoding="utf-8"))}
        cap = c.get("M_CAP")
        if cap is None:
            continue
        checked += 1
        for name, off in sorted(c.items()):
            if not name.startswith("M_") or name == "M_CAP":
                continue
            if off >= cap:
                bad.append(f"{p.name}: {name} = {off} 已在 M_CAP = {cap} 之外")
            size = c.get(name[2:] + "_CAP")
            if size is not None and off + size > cap:
                bad.append(f"{p.name}: {name} + {name[2:]}_CAP = "
                           f"{off}+{size} = {off + size} 超出 M_CAP = {cap}")
        tbs = sorted(v for k, v in c.items() if k.startswith("TB_"))
        if tbs:
            ta = c.get("TA")
            if not ta:
                bad.append(f"{p.name}: 有 TB_* 却没有 TA（表尺寸）")
            else:
                gaps = sorted({b - a for a, b in zip(tbs, tbs[1:])})
                if gaps != [ta]:
                    bad.append(f"{p.name}: TB_* 不等距: 间距 {gaps} != TA = {ta}")
                if tbs[-1] + ta > cap:
                    bad.append(f"{p.name}: 最后一张表 {tbs[-1]} + TA = "
                               f"{tbs[-1] + ta} 超出 M_CAP = {cap}")
                m_tb = c.get("M_TB")
                if m_tb is not None and tbs[0] != m_tb:
                    bad.append(f"{p.name}: 第一张表 {tbs[0]} 不在表区起点 "
                               f"M_TB = {m_tb}")
    assert not bad, ("工具的内存布局越界（Linux 上段错误、Windows 上看不出来）:\n  "
                     + "\n  ".join(bad))
    print(f"      {checked} 个工具的内存布局都在各自的 M_CAP 之内")


@test
def test_tool_reserves_its_memory():
    """**问过堆顶的工具，必须把堆扩到够再用它。**

    `lomtfrom` / `lomtrans` 原来是 `brk(0)` 拿当前堆顶当基址、却**没有**让内核把堆扩到
    `M_CAP`，于是 `[mem, mem + M_CAP)` 里多数页根本没被映射 —— 第一次写到上面那一段就是
    SIGSEGV（`lomtfrom` 连读 argv 都够远）。Windows 上看不出来: PE 的 .bss/堆区 committed
    得大；而判据在本机跑的就是 PE（2026-09-22 把门禁搬上 Linux runner 才现形）。

    家族里其余 24 个工具都扩了（`sys_alloc` 或第二个 `brk`），只有这两个忘了。所以这一条查的
    是"**凡是问过堆顶的，都要有扩堆**"。
    """
    bad = []
    for p in sorted((ROOT / "loment" / "tools").glob("*.lomt")):
        t = p.read_text(encoding="utf-8")
        if "syscall4(12, 0, 0, 0)" not in t:
            continue
        if "fn sys_alloc" in t or "syscall4(12, (cur" in t:
            continue
        bad.append(p.name)
    assert not bad, ("这些工具问了堆顶却没把堆扩到够（Linux 上段错误、Windows 上看不出来）: "
                     + ", ".join(bad))
    print("      问过堆顶的工具都把堆扩到够了")


@test
def test_arg_helpers_do_not_rebase_cb():
    """**`cb` 已经是绝对指针，不许再当基址加偏移。**

    `lomtrans` 的 `arg_is` / `arg_val` 里写着 `ptr_add(cb, M_TMP + 96)` —— 而
    `cb = ptr_add(mem, M_ARG)`，于是那个地址落到 `mem + 2441312`，越过 `M_CAP`
    （1525248）约 90 万字节。`arg_is` 每轮参数循环都调 ⇒ **有参数就 SIGSEGV、无参数不崩**。

    正确写法要从 `cb` 往下减，而那个相对偏移是负数、`u32` 写不出来 —— 这正是它当初被写反的
    原因。按 `arg_at` 自己的约定，暂存格应当由调用方从 `mem` 算好传进来。
    """
    bad = []
    for p in sorted((ROOT / "loment" / "tools").glob("*.lomt")):
        t = p.read_text(encoding="utf-8")
        # **先剥注释、但保留换行** —— 这一条解释的就是那个写成错的写法，注释里必然带着它，
        # 不剥就自己报自己（第一版就踩了）；用换行顶替是为了行号仍然指向源码。
        # 与 `loment_cli_test` 剥注释同一写法。
        t = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)
        t = re.sub(r"//[^\n]*", "", t)
        for m in re.finditer(r"ptr_add\(cb,\s*[A-Z][A-Z0-9_]*\b", t):
            # 注意**不能**写成 `[^()]*` 通配: `ptr_add(cb, o1)` 是合法的 ——
            # `o1` 是 cmdline 内的相对偏移，本来就该从 `cb` 加。要抓的是
            # "把 `M_*` 这类**以 mem 为基准**的常量又加到 `cb` 上"。
            bad.append(f"{p.name}:{t[:m.start()].count(chr(10)) + 1}  {m.group(0)}")
    assert not bad, ("把 cb（已经是绝对指针）又当基址用了:\n  " + "\n  ".join(bad))
    print("      没有把 cb 再当基址的写法")


# ---------------------------------------------------------------- M65/M66 构建

@test
def test_m65_m66_incremental_and_cache():
    import time
    import loment_build
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"
        t0 = time.perf_counter()
        assert loment_build.main([str(EX), "--out", str(out)]) == 0
        cold = time.perf_counter() - t0
        t1 = time.perf_counter()
        assert loment_build.main([str(EX), "--out", str(out)]) == 0
        hot = time.perf_counter() - t1
        recs = json.loads((out / ".loment-cache.json").read_text(encoding="utf-8"))["records"]
        assert len(recs) >= 15, len(recs)
        assert hot < cold, (cold, hot)
        # 改一个文件: 只有它需要重编
        victim = out.parent / "victim.lomt"
        victim.write_text((EX / "mathutil.lomt").read_text(encoding="utf-8"), encoding="utf-8")
        assert loment_build.main([str(out.parent), "--out", str(out)]) == 0


@test
def test_gate_reports_how_many_criteria_did_not_run():
    """门禁必须把"这条检查里有多少判据没跑"数出来报进那一行（`#371`）。

    **Why**：每个判据套件的运行器都是 `try: fn(); print("  PASS  {name}")` —— 一条判据
    打印 `SKIP: 无 clang/WSL` 之后 `return`，在它眼里**正常返回就是通过**。于是那一行报的
    是"没抛异常的总数"。实测（把 `wsl` 从 `shutil.which` 里藏掉，也就是 runner 的条件）：
    `loment_elf_test` 打印 26 行 SKIP、汇总照报 `31/31 通过`、退出码 0。

    而 CI 是 ubuntu 容器，里面**没有 `wsl`**（它不是 apt 包，镜像也没装垫片）—— 所以那道
    必需检查在 runner 上对**整条运行时护栏**失明，而且它报满。被判据门着的那些条
    （sweep 精度、L0 提升、L1 定活、L2 纪元、`free` 所有权那一对、自适应与分代）正是
    "跑一个产物"才有意义的那一类。
    **门静默失效比门误报更坏** —— 这是 `loment_door_test` 立论的原话，这里是它的同一件事。

    **How to apply**：`ci.py` 把每条检查输出里"行首是 SKIP"的行数出来，放进
    `:: [static] …` 那一行；0 的时候不喧哗（大多数检查一条都不跳）。

    **证伪**：把 `ci.py` 里 `count_skips` 那几处去掉，下面第 (2)(3) 两段立刻红
    （`format_line` 与 5 元组都不存在）—— 这条判据证伪的是**报数**，不是"跳过该不该跳过"。
    """
    import ci

    # (1) 计数规则: 只认**行首**是 SKIP 的那种行（各套件都写成 `print("      SKIP: …")`）。
    #     放在中间的不算 —— 否则某条判据的说明文字里带一个 SKIP 就会被误数。
    assert ci.count_skips("  PASS  a\n      SKIP: 无 clang/WSL\n  PASS  b\n") == 1
    assert ci.count_skips("  PASS  a\n") == 0
    assert ci.count_skips("") == 0
    assert ci.count_skips("  PASS  a  # 这一条没跳过, 只是提到了 SKIP\n") == 0

    # (2) 那一行必须带上这个数; 0 的时候不出现
    line = ci.format_line("loment_elf_test", True, "31/31 通过", 1.2, 26)
    assert "26" in line and "SKIP" in line, line
    quiet = ci.format_line("loment_tools_test", True, "12/12 通过", 1.2, 0)
    assert "SKIP" not in quiet, quiet

    # (3) **端到端**：造一个真模块喂给 `_run_one_check`（走的就是门禁那条路），
    #     它打印两条行首 SKIP、一条只是提到 SKIP —— 数出来的必须正好是 2。
    #
    #     **为什么不拿某个真实套件来钉**：那个数**随平台走**。第一版钉的是
    #     "藏掉 `wsl` 之后 `loment_elf_test` 跳过 >= 20 条"，而同一个 PR 的下半
    #     （`#371` 第二半）恰恰让这族判据在 Linux 上**原生跑**、于是那个数掉到 0 ——
    #     判据和它的修法在同一个 PR 里互相打架，CI 当场报 `loment_tools_test` 新红。
    #     这一条要钉的是**报数这件事**，不是某个套件在某个平台上跳几条。
    import shutil
    import tempfile

    probe = Path(tempfile.mkdtemp(prefix="loment-skipcount-"))
    try:
        (probe / "loment_skipcount_probe.py").write_text(
            "def main() -> int:\n"
            "    print('  PASS  a')\n"
            "    print('      SKIP: 无 clang/WSL')\n"
            "    print('  PASS  b')\n"
            "    print('      SKIP: 需要 WSL')\n"
            "    print('  PASS  c  # 这一行只是提到 SKIP')\n"
            "    return 0\n", encoding="utf-8", newline="\n")
        # 另一份**走共享运行器**的探针：它把 `SKIP` 连**判据名**打出来，逐条预算才对得了账。
        (probe / "loment_namedskip_probe.py").write_text(
            "import loment_suite\n"
            "\n"
            "TESTS = []\n"
            "\n"
            "def test(fn):\n"
            "    TESTS.append((fn.__name__, fn))\n"
            "    return fn\n"
            "\n"
            "@test\n"
            "def test_needs_wsl():\n"
            "    print('      SKIP: 需要 WSL')\n"
            "\n"
            "@test\n"
            "def test_runs():\n"
            "    print('      跑过了')\n"
            "\n"
            "def main() -> int:\n"
            "    return loment_suite.run(TESTS, 'loment_namedskip_probe')\n",
            encoding="utf-8", newline="\n")
        sys.path.insert(0, str(probe))
        try:
            r0 = ci._run_one_check("loment_skipcount_probe")
            r1 = ci._run_one_check("loment_namedskip_probe")
        finally:
            sys.path.remove(str(probe))
            sys.modules.pop("loment_skipcount_probe", None)
            sys.modules.pop("loment_namedskip_probe", None)
        assert r0.ok, "探针模块自己没跑通"
        assert r0.skips == 2, f"跳过数该是 2（两条行首的算、提到的不算），得到 {r0.skips}"

        # (4) `#371` 的**判定面**（2026-10-15 补）：跳过必须**认得出是哪一条**，否则
        #     `ci.check_budget` 没法逐条对账签入的 `.github/gate-skips.txt`。这一条与上面
        #     钉"条数"的那条是一对：数得出条数却认不出名字，等于预算对那个套件失明。
        assert r1.ok, "走共享运行器的探针自己没跑通"
        assert r1.skips == 1 and r1.skipped == ("test_needs_wsl",), \
            f"跳过要连名字认出来，得到 {r1.skipped!r}"
        assert r1.passed == ("test_runs",), f"通过的那条也要认得出来，得到 {r1.passed!r}"
        assert r1.declared == 2, f"len(TESTS) 要读出来，得到 {r1.declared}"
    finally:
        shutil.rmtree(probe, ignore_errors=True)


@test
def test_no_shipped_tool_prints_an_internal_name():
    """包里那些工具**写给人看的消息不许以内部组件名开头**（`issue #8`）。

    用户敲的是 `loment`（或 `lompi`）。`fujoc-s`（自举驱动的旧内部名）、`lomelf`、
    `fujofmt` / `lomfmt`、`lomdoc` 这些名字对他不指向任何他手上的东西 —— 而
    `fujoc-s: 打不开 x.lomt` 正是他实际看到的那一行。`issue #8` 报的三条里两条是这个。

    **为什么扫源码而不是跑二进制**：跑一遍要构建整包（分钟级），而这一条要守的是
    "新加的消息别再带内部名" —— 那是纯静态的，扫一遍就够。跑真东西那半由
    `loment_p8_test` 的入口装载那条与 `loment_err_test` 的渲染那条承担。

    **`lompi:` 与 `lomenterr:` 不在此列**：它们是独立命令，用户真的会敲（包里
    `bin/lompi`、`bin/lomenterr` 就在那儿）。这里禁的是**用户没敲过**的名字。
    """
    pat = re.compile(r'"((?:fujoc-s|fujofmt|lomelf|lomfmt|lomdoc)\s*:)')
    bad = []
    for d in (ROOT / "loment" / "selfhost", ROOT / "loment" / "tools"):
        for src in sorted(d.glob("*.lomt")):
            for n, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
                # **整行注释跳过** —— 源码里本来就有几处在注解里引用旧消息（那些正是
                # 这一条要记的事），扫它们会把"记录"当成"违规"。
                if line.lstrip().startswith("//"):
                    continue
                for m in pat.finditer(line):
                    bad.append(f"{src.relative_to(ROOT)}:{n}: {m.group(1)}")
    assert not bad, ("有人把内部组件名写进了面向用户的消息 (用户敲的是 `loment`):\n  "
                     + "\n  ".join(bad))
    print("      包里没有工具以内部组件名开头报消息")


@test
def test_gate_budget_matches_the_suites():
    """`#371` 两道棘轮的**签入文件本身**说得通吗。

    `.github/gate-floors.txt` 说每个套件该有多少条判据，`.github/gate-skips.txt` 说哪些判据
    允许在这台机器上不跑。`ci.check_budget` 拿它们判成败 —— 所以文件本身错了就是整个机制错了。
    这条钉四件事：

    1. 两份都能被 `ci.read_budget()` 读出来（格式对）；
    2. **每个有 `TESTS` 的套件都在地板里，且数字 == `len(TESTS)`** —— 漏一个套件就等于它
       从此不受地板约束（「把判据删光」那条路又通了）；
    3. 两份里点到的套件名都在 `STATIC_CHECKS` 里（写错名字 = 那一行永远不生效）；
    4. 跳过表里点到的**判据名**在那个套件里真的存在（判据改名或删掉之后，那条豁免会变成
       一句永远不生效的谎话）。
    """
    import ci

    floors, skips = ci.read_budget()

    declared: dict[str, int] = {}
    for name in ci.STATIC_CHECKS:
        tests = getattr(__import__(name), "TESTS", None)
        if tests:
            declared[name] = len(tests)

    missing = sorted(set(declared) - set(floors))
    assert not missing, f"这些套件有判据却不在 gate-floors.txt 里: {missing}"
    wrong = {n: (floors[n], c) for n, c in declared.items() if floors.get(n) != c}
    assert not wrong, f"地板与实测不符（文件, 实测）: {wrong}"

    unknown = sorted({s for s, _ in skips if s not in ci.STATIC_CHECKS})
    assert not unknown, f"预算里点到了 STATIC_CHECKS 里没有的套件: {unknown}"

    for suite, crit in sorted(skips):
        if crit == "*":
            continue
        tests = getattr(__import__(suite), "TESTS", None)
        if tests:
            # 归一走 `loment_suite.entries` —— `TESTS` 有 `(名字, 函数)` 与裸函数两种写法
            names = {n for n, _ in loment_suite.entries(tests)}
        else:
            # 没搬上共享运行器的套件（`check()` 那一族）没有 `TESTS` —— 名字从**源码**里认：
            # 那里必须有 `SKIP  <判据名>:` 这么一行。少了这一路，那几家的豁免写错名字
            # 也没人管（一条永远不生效的谎话）。
            src = (Path(__file__).parent / f"{suite}.py").read_text(
                encoding="utf-8", errors="replace")
            # f-string 里的 `{pair['label']}` 在**源码**里是字面量，而量出来的名字是渲染后的
            # （`lompi_sync[指南]`）—— 所以占位符当通配符比。
            pats = [re.compile("^" + ".*".join(
                        re.escape(p) for p in re.split(r"\{[^}]*\}", n)) + "$")
                    for n in re.findall(r"SKIP\s+([^:\n]+?):", src)]
            assert any(p.match(crit) for p in pats), (
                f"gate-skips.txt 里 {suite} 的 {crit} 在那个套件里找不到（改名了？）")
            continue
        assert crit in names, f"gate-skips.txt 里 {suite} 的 {crit} 不在那个套件里（改名了？）"
    print(f"      gate-floors {len(floors)} 个套件 / gate-skips {len(skips)} 条豁免，都对得上")


@test
def test_every_criterion_is_registered():
    """套件里每个 `def test_*` 都必须**有人跑它**：进 `TESTS`，或者在别处被点名过。

    没人跑的后果是**它从不运行，而套件照样绿** —— 与 `#371` 同一个形状（有一层安全检查
    看不见洞）。新写一条判据、忘了装饰器，是最容易发生也最难发现的一种。

    **为什么不去认"必须挂 `@test`"**：仓里有四种注册惯用法 —— `@test` 装饰器（多数套件）、
    `check()` 闭包往 `RESULTS` 里塞（`loment_dist_test` / `loment_sign_test`）、手写 `main`
    里直接调、以及按名字约定收（`lvm_test` / `lvm_twin_test` 的
    `sorted(globals().items())`）。所以**不去猜惯用法，直接问套件自己**：把模块 import
    进来，看 `def test_*` 的名字在不在那份 `TESTS` 里。对没有 `TESTS` 的套件
    （`check()` 闭包那几个）退回到**可达性**：名字在模块里被读到过就算有人跑。

    这条与 `test_gate_budget_matches_the_suites` 一样要 import 全部套件 —— 那份代价那边
    已经付过了（`len(TESTS)` 只能从 import 拿），这里是同一笔账。
    """
    import ast

    import ci

    def registered(mod) -> set[str] | None:
        """套件自己说"我要跑哪些"；没有 `TESTS` 就返回 `None`（退回可达性）。"""
        tests = getattr(mod, "TESTS", None)
        if not tests:
            return None
        out: set[str] = set()
        for e in tests:
            if isinstance(e, tuple) and len(e) >= 2:
                out.add(str(e[0]))          # `(名字, 函数)`；名字可能就是显示名
                if callable(e[1]):
                    out.add(getattr(e[1], "__name__", ""))
            elif callable(e):
                out.add(e.__name__)         # 裸函数
        return out

    offenders = []
    for name in ci.STATIC_CHECKS:
        p = Path(__file__).resolve().parent / f"{name}.py"
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        loaded = {n.id for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        loaded |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        reg = registered(__import__(name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            decs = [ast.unparse(d) for d in node.decorator_list]
            if any(d == "test" or d.endswith(".test") for d in decs):
                continue
            if node.name in loaded:
                continue          # 被别处点名（`check()` 闭包 / 手写 main）—— 有人跑它
            if reg is not None and node.name in reg:
                continue          # 进了 `TESTS` —— 有人跑它（按名字收的那种套件走这条）
            offenders.append(f"{name}.py:{node.lineno} {node.name}")
    assert not offenders, ("这些 def test_* 既没挂 @test 也没进 TESTS，因而从不运行: "
                           + ", ".join(offenders))
    print("      每个 def test_* 都有人跑（进了 TESTS 或挂 @test）")


# ------------------------------------------------- 门禁 workflow 自己的形状（`#371`）

def _gate_workflow() -> str:
    return (ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8")


def _job_block(wf: str, job: str) -> str:
    """取 `jobs:` 下面那个 job 的整段（到下一个同缩进的 job 名为止）。

    **为什么不用 YAML 解析**：PyYAML 不在 CI 镜像的包名单里 —— 引一个依赖只为读
    两个 workflow 文件不值当（`ci-image.yml` 的包名单有"不加"的纪律）。这里的形状是
    **固定**的（job 名两格缩进，键四格），正则足够且看得见自己在断言什么。

    两处调用：`gate.yml`（下面那一族）与 `manifest.yml`（生成物那条线）。
    """
    m = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  \S|\Z)", wf, re.M | re.S)
    assert m, f"workflow 文件里找不到 job `{job}`"
    return m.group(0)


@test
def test_gate_workflow_shard_shape_matches_ci():
    """分片那个数**写在三处**：`GATE_SHARDS`、矩阵每条的 `total`、`ci.py` 现算的片数。

    文件头写着"改分片形状要同时改 `ci.py` 和这里" —— 而此前**没有任何东西**钉住它。
    这个数一偏，分片划分就变、并集不再是 `STATIC_CHECKS`，`static` 那步的条数校验
    会红 —— 但那是**跑完之后**才发现。三处对齐让它当场红。

    片 1 归 `static-exclusive`（独占那一片串在其余片前面），所以矩阵该是 `2..N`；
    这里连"少一片"也一起钉 —— 少的那一片就是**从此不跑**的那几条判据。
    """
    import ci

    wf = _gate_workflow()
    m = re.search(r'^  GATE_SHARDS: "(\d+)"', wf, re.M)
    assert m, "gate.yml 里找不到 `GATE_SHARDS`"
    n = int(m.group(1))

    trips = re.findall(r"-\s*\{\s*shard:\s*(\d+),\s*total:\s*(\d+)\s*\}", wf)
    assert trips, "矩阵 `include:` 一条都没有 —— 分片那几条判据从此不跑"
    totals = {int(t) for _, t in trips}
    assert totals == {n}, f"矩阵里的 total {sorted(totals)} 与 GATE_SHARDS={n} 不一致"
    ids = sorted(int(s) for s, _ in trips)
    assert ids == list(range(2, n + 1)), (
        f"矩阵该是 2..{n}（片 1 归 static-exclusive），实际 {ids}")

    excl = _job_block(wf, "static-exclusive")
    assert '--shard "1/$GATE_SHARDS"' in excl, "`static-exclusive` 没在跑片 1"

    plan = ci.shards(n)
    assert len(plan) == n, f"`ci.shards({n})` 切出 {len(plan)} 片，与 GATE_SHARDS={n} 不符"
    flat = [x for s in plan for x in s]
    assert sorted(flat) == sorted(ci.STATIC_CHECKS), "分片不是 STATIC_CHECKS 的一个划分"
    print(f"      分片形状三处一致: GATE_SHARDS={n}, 矩阵 2..{n}, ci.shards({n}) 切出 {n} 片")


@test
def test_gate_workflow_keeps_the_required_check_name():
    """`static` 那个 job 的 `name:` **就是**保护规则里挂着的必需检查名。

    文件头第三条边界：名字一没，保护规则就永远等不到它，PR 会**全部**卡住
    （不是松开）。这条钉两件事：它还是那个名字，而且**只有一个** job 叫这个名字
    —— 两个同名 job 会让"必需检查"的归属变成看谁先报。

    `static-manual` 特意起了别的名字（它是手工诊断，不该顶替 PR 上那道门），
    这里把那条也钉住。
    """
    wf = _gate_workflow()
    req = "ci.py --static-only"
    names = re.findall(r"^    name: (.+)$", wf, re.M)
    assert names.count(req) == 1, (
        f"`{req}` 在 job 名里出现 {names.count(req)} 次（应为 1 次）—— {names}")
    assert f"    name: {req}" in _job_block(wf, "static"), (
        f"job `static` 的 name 不再是 `{req}`：保护规则等的就是这个字符串")
    print(f"      必需检查名 `{req}` 仍由唯一一个 job 扛着")


@test
def test_gate_workflow_aggregation_fails_closed():
    """聚合那两步里各有一条"不许静默变绿"，以及两处"基线不见了就判失败"。

    堵的是两个**静默通过**的形状：

    * 少一片就少几条判据，而门禁照样绿 —— 所以聚合要**数**条数并对不上就红；
    * `needs` 的默认行为是"依赖失败就**跳过**"，而跳过不算失败 —— 保护规则会一直
      等下去。所以那里必须显式看 `needs.*.result`。

    以及基线文件**不见了**时不能当成"没有基线"放行：两个跑片的 job 各有一处守卫。
    """
    wf = _gate_workflow()
    agg = _job_block(wf, "static")
    assert "len(ci.STATIC_CHECKS)" in agg, "聚合那步没拿 STATIC_CHECKS 的条数当基准"
    assert 'if [ "$got" != "$want" ]' in agg, "聚合那步没在条数对不上时判失败"
    assert "needs.static-exclusive.result != 'success'" in agg, (
        "少了分片红就是红那一步 —— 依赖失败会跳过，而跳过不算失败，"
        "保护规则会一直等")
    assert "needs.static-shard.result != 'success'" in agg, "同上（另一片）"

    guard = wf.count("基线文件 .github/gate-baseline.txt 不见了")
    assert guard == 2, (
        f"基线缺失的守卫有 {guard} 处，应为 2（独占片 / 分片各一处）—— "
        f"少了就是那一类 job 会把'基线没了'当成'没有基线'放行")

    # 基线棘轮（**只许变短**）本身也要有人钉 —— 否则它自己会被悄悄删掉。
    # **钉步骤名，不钉那几个字**：段落里那句 `echo "基线只许变短: OK…"` 与注释都含着
    # 同样几个字，按字面子串判会在步骤被删掉之后**照样绿**（第一版就是这样，
    # 变异验证时两条棘轮改动一条都没抓住）。
    assert re.search(r"^\s*- name: 基线只许变短$", agg, re.M), (
        "少了基线棘轮那一步 —— 往基线里**加**一条就没人拦了，"
        "而那条路等于把一条新红登记成合法")
    # 判失败要**带 `::error::`**：变异成一句普通 echo 之后 GitHub 不会红那一步
    assert 'echo "::error::取对照面失败' in agg, (
        "棘轮取不到对照面时没判失败（fail closed）—— 静默跳过的棘轮会变成"
        "一句永远不生效的谎话")
    assert 'echo "::error::对照面（$base）里读不到' in agg, "同上（读不到基线那一处）"
    assert "comm -13 old.txt new.txt" in agg, "棘轮没在算'新增'（comm -13 那一列）"
    assert "norm .github/gate-baseline.txt > new.txt" in agg, "棘轮比的不是树里那份基线"
    print("      聚合的两条防静默 + 两处基线缺失守卫 + 基线棘轮都在")


@test
def test_gate_container_image_matches_the_recipe():
    """镜像的**属主与 tag** 都写在两处：`gate.yml` 各 job 的 `container.image`，
    与 `ci-image.yml` 的 `TAG` + 它按 `github.repository_owner` **算出来**的那个属主。

    文件头第四条边界："改镜像要同时抬这两处 —— 抬 tag 是让各 job 用上新镜像的
    **唯一**动作"。两处不一致的后果是**静默**的：构建推的是新 tag，各 job 拉的还是
    旧镜像 —— 看起来改了，其实没改（而"看起来配上了、其实没配上"正是最费时间的一类）。

    **属主那一半是 2026-10-17 补的**，因为当天搬家时差点让每一片都拉不到镜像：
    `gate.yml` 里属主是**写死的**，`ci-image.yml` 里是**算的**，库一换组织两边就分叉，
    而**没有任何东西会提醒**。这条判据在当时是**红的**（库已经住进 `Loment-Lang`，
    `gate.yml` 还指着 `fujojtop`）—— 它就是为那一刻写的。

    期望的属主取自 `loment_publish.ORG` —— "这个项目住在哪个组织"在本仓的单一真源。
    转小写是因为 **ghcr 的仓库名必须全小写**（Docker 的硬要求，`ci-image.yml` 那儿也这么做）。

    没拉镜像的那个 job（`static-exclusive`）刻意留在 runner 上，见它自己那段注释；
    所以这里只比**出现过的**那些。
    """
    wf = _gate_workflow()
    tags = re.findall(r"image:\s*ghcr\.io/[^:\s]+:(\S+)", wf)
    assert tags, "gate.yml 里一个 `container.image` 都没有"
    recipe = (ROOT / ".github" / "workflows" / "ci-image.yml").read_text(encoding="utf-8")
    m = re.search(r'^  TAG: "([^"]+)"', recipe, re.M)
    assert m, "ci-image.yml 里找不到 `TAG`"
    assert set(tags) == {m.group(1)}, (
        f"gate.yml 用 {sorted(set(tags))}，ci-image.yml 构建 {m.group(1)} —— "
        f"镜像抬了 tag 而各 job 没跟，是静默的")

    import loment_publish
    owners = set(re.findall(r"image:\s*ghcr\.io/([^/:\s]+)/", wf))
    want = loment_publish.ORG.lower()
    assert owners == {want}, (
        f"gate.yml 拉的是 {sorted(owners)} 名下的镜像，而库住在 `{loment_publish.ORG}`"
        f"（ghcr 里该是 `{want}`）—— 库一换组织，`ci-image.yml` **算出来**的属主就变了、"
        f"`gate.yml` 这一行**不会**跟着变，而两处谁都不提醒谁。"
        f"换的顺序是硬的：先在新属主下把 tag 建出来、**再把它设成 public**"
        f"（包默认私有，fork 的 PR 拉不到）、**最后**才改 gate.yml —— `docs/255` §2")
    print(f"      镜像属主 `{want}` + tag `{m.group(1)}` 两处一致")


@test
def test_gate_workflow_fails_when_the_budget_disagrees():
    """判据预算对不上时，门禁必须**真的红** —— 而这一步得在 workflow 里**单独**认出来。

    `ci.py` 在**每条判据都 PASS** 的情况下也可能判失败：有人漏登记一条跳过、或者动了
    地板。那种失败**不在** `:: [static] … FAIL` 里（没有任何一条套件失败），而各分片
    "对照基线（新红，单跑确认）"那一步只读那些 FAIL 行 —— 于是**预算不符的片会报绿**，
    每条套件都写着 PASS。这正是这一整版要堵的形状，落在它自己身上就太讽刺了。

    （落这一版时实测：`gate-skips.txt` 空着，若少了这一步，那几片确实会报绿。）

    两个跑片的 job（`static-exclusive` / `static-shard`）各要一处，且判失败要带
    `::error::` —— 少一个前缀，GitHub 不会红那一步。
    """
    wf = _gate_workflow()
    steps = re.findall(r"^\s*- name: .*$", wf, re.M)
    n = sum(1 for s in steps if s.strip() == "- name: 判据预算（#371）")
    assert n == 2, (
        f"认预算那一步有 {n} 处，应为 2（独占片 / 分片各一处）—— "
        f"少了就是预算不符时门禁**报绿**")
    assert wf.count("grep -q 'fujoci: 判据预算对不上' gate.out") == 2, (
        "两处都要认 `ci.py` 那句原话（只看退出码分不清预算不对与某条套件红）")
    assert wf.count('echo "::error::判据预算对不上') == 2, (
        "两处判失败都要带 `::error::`，否则那一步不会红")
    print("      判据预算对不上时，两个跑片的 job 都会红")


@test
def test_gate_checks_the_budget_even_when_a_suite_is_red():
    """有套件红的时候，判据预算**照样要查** —— 否则最需要它的时候它不跑。

    形状（2026-10-15 在 CI 上实测到）：`main()` 原先在"有红就中止"那一步就 `return 1`，
    而预算那一段写在它**后面** —— 于是任何一条套件红（哪怕那条红早就登记在基线里、
    根本不算新红）都会把整段跳过。那天的第 5 片有 109 条判据跳过，**一条都没量到**，
    就因为它有一条基线内的红。

    这里把 `run_static` 换掉，喂它一条红的、一条带**未登记**跳过的，直接跑 `main()`：
    要求输出里**同时**出现预算那句。
    """
    import contextlib
    import io

    import ci

    red = ci.Result("probe_red_suite", False, "probe: 1/1 通过", 0.1,
                    0, ("p",), (), 1, False)
    skip = ci.Result("probe_skip_suite", True, "probe: 1/2 通过, 1 SKIP", 0.1,
                     1, ("p",), ("test_needs_the_moon",), 2, False)
    orig_run, orig_argv = ci.run_static, sys.argv
    out = io.StringIO()
    try:
        ci.run_static = lambda *a, **k: [red, skip]
        sys.argv = ["ci.py", "--static-only"]
        with contextlib.redirect_stdout(out):
            rc = ci.main()
    finally:
        ci.run_static, sys.argv = orig_run, orig_argv
    text = out.getvalue()
    assert "判据预算对不上" in text, (
        "有套件红的时候预算那一段没跑 —— 跳过表与地板在那一次跑里完全没有约束力。"
        f"输出尾部：{text[-300:]!r}")
    assert rc == 1, "有红又有预算问题时，退出码必须是 1"
    print("      有红的套件时预算照样查（两条一起报出来）")


@test
def test_gate_makes_an_unnamed_skip_loud():
    """跳过**没有名字**时必须判失败 —— 没名字的跳过对预算完全不可见。

    仓里当时有三个套件没搬上 `loment_suite.run`（`check()` 那一族：`loment_dist_test` /
    `loment_sign_test` / `loment_eol`），它们打的是 `SKIP <原因>` 这种没名字的写法：
    `count_skips` 数得到（所以 `N SKIP` 那一栏会报），而**名字**认不出来 —— 于是
    `gate-skips.txt` 对它们毫无约束力，`CONTRIBUTING.md` 禁的那条路
    （"给一条既有判据加个环境门让它不跑"）在这三个套件里一直开着。

    这条直接跑 `check_budget`：一条"打了 2 条 SKIP、0 条带名字"的结果必须出问题；
    反过来"1 条带名字且登记过"不能出问题（否则判据本身在滥杀）。
    """
    import ci

    r = ci.Result("probe_unnamed", True, "probe: 1/1 通过", 0.1, 2, ("p",), (), 1, False)
    bad = ci.check_budget([r], {"probe_unnamed": 1}, set(), in_ci=False)
    assert any("没有名字" in b for b in bad), f"没名字的跳过没被判失败：{bad}"

    r2 = ci.Result("probe_named", True, "probe: 1/2 通过, 1 SKIP", 0.1,
                   1, ("p",), ("test_x",), 2, False)
    assert not ci.check_budget([r2], {"probe_named": 2}, {("probe_named", "test_x")},
                               in_ci=False), "登记好的跳过不该被判失败"
    print("      跳过没名字 = 判失败；有名字且登记过 = 放过")


@test
def test_gate_only_reports_a_stale_skip_for_a_suite_it_ran():
    """「登记了却没跳」的 notice **只能对"这一趟跑过的套件"打**。

    分片跑的时候 `results` 只是这一片的套件。若直接拿 `skips - seen` 去滤，表里所有
    **不在这一片**的条目都会被点成"这次没跳" —— 于是那句"照 notice 把表削短"会变成
    **把整张表删光**。实测过：合并 `#382` 之后那一轮（run 38817007229），六个分片的
    notice 合起来正好覆盖整张 140 条的表；按它削，表会被清空而不是变短。

    这里直接跑 `check_budget`：一个"这一趟没跑的套件"的条目**不许**出现在 notice 里。
    """
    import contextlib
    import io

    import ci

    ran = ci.Result("probe_ran", True, "probe: 1/1 通过", 0.1, 0, ("p",), (), 1, False)
    skips = {("probe_ran", "test_never_runs"),        # 这一趟跑了 → 该点它
             ("probe_absent", "test_other")}          # 这一趟没跑 → 不许点它
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ci.check_budget([ran], {"probe_ran": 1}, skips, in_ci=True)
    text = out.getvalue()
    assert "probe_ran test_never_runs" in text, (
        f"这一趟跑过、而它没跳的条目没被点名（削表就少了依据）：{text!r}")
    assert "probe_absent" not in text, (
        "把**这一趟没跑**的套件的条目也点成没跳 —— 照它削表会把整张表删光")
    print("      只点「跑了却没跳」的条目（没跑的套件不点）")


@test
def test_lomf_patches_in_the_tree_are_applied():
    """树里的每一份 `.lomf` 都**已经套用**了 —— 补丁是这一笔的**记录**，源是**事实**。

    **Why**：`.lomf` 不参与编译（编译器不认这个后缀），所以它唯一的价值就是"这一笔改了什么"
    的那份**可核对**记录。若允许"补丁放着、源另说"，它就会立刻腐烂成一份没人对得上的说明书
    —— 那比没有更坏。这条判据把两者钉在一起：**每一处 `old` 都必须 0 次、`new` 都必须
    正好 `expect` 次**（也就是 `lomf.DONE`）。

    **How to apply**：套用器分得清"待套用"与"已套用"，所以这一条能问得很准。新增补丁要
    在**同一个 PR 里**套用；套用器报"待套用"就是这条红。
    """
    import lomf
    pats = sorted((ROOT / "loment" / "patches").glob("*.lomf"))
    assert pats, ("`loment/patches/` 下一份修改件都没有 —— 这条判据自己先失效了"
                  "（那时它无法证明任何事）")
    bad = []
    for p in pats:
        _slug, _about, edits = lomf.load(p)
        for e in edits:
            st, old_n, new_n = e.state()
            if st != lomf.DONE:
                bad.append(f"{p.name}: {e.path}（旧={old_n} 新={new_n} 期望={e.expect}）")
    assert not bad, (
        f"这些修改件在树里**没有套用**（或只套了一半）：{bad} —— `.lomf` 与源必须一致，"
        "否则它就成了一份对不上的说明书")
    print(f"      {len(pats)} 份修改件都已在树里套用")


@test
def test_lomf_never_applies_on_a_guess():
    """对不上就**一处也不改**，互为子串就**拒收**（`.lomf` 的两条硬规矩）。

    **Why**：修改件不按行号、按逐字节片段定位，所以"对不上"是**必然会发生**的事（别人改过
    同一段、或者这一笔只落了一半）。那一时刻只有两种选择：猜，或者停。猜的后果是**静默改错
    文件**，而这类改动住在运行时 IR 里 —— 猜错的地方不会报错，只会在很久以后表现为一个
    莫名的回收行为。所以：**对不上报错、一处不动**。

    "互为子串"那条更隐蔽：套用是**幂等**的（靠「旧 0 次、新 expect 次」认"已经套过"），
    而插入式替换天然让 `old` 成为 `new` 的子串 —— 那种补丁套第二次会**再插一遍**，
    且套用器无从分辨。所以那条形状在**解析期**就被拒。
    """
    import lomf
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        target = tmp / "x.txt"
        pat = tmp / "p.lomf"

        def write_patch(body: str) -> None:
            pat.write_text("lomf 1\nmod t\nabout 试\n\n" + body,
                           encoding="utf-8", newline="\n")

        old_root = lomf.ROOT
        lomf.ROOT = tmp
        try:
            # ① 次数对不上（期望 2、实际 1）—— 必须报错，且**不动盘**
            target.write_bytes(b"aaa\n")
            write_patch("edit x.txt expect 2\nold <<<\naaa\n>>>\nnew <<<\nbbb\n>>>\n")
            rc = lomf.cmd_apply(pat, write=True)
            assert rc == 1, f"次数对不上却返回 {rc}（期望 1）"
            assert target.read_bytes() == b"aaa\n", "次数对不上却动了盘"

            # ② 次数多出来也一样（期望 1、实际 2）
            target.write_bytes(b"aaa\naaa\n")
            write_patch("edit x.txt expect 1\nold <<<\naaa\n>>>\nnew <<<\nbbb\n>>>\n")
            rc = lomf.cmd_apply(pat, write=True)
            assert rc == 1, f"次数多出来却返回 {rc}（期望 1）"
            assert target.read_bytes() == b"aaa\naaa\n", "次数多出来却动了盘"

            # ③ 插入式（old 是 new 的子串）—— 解析期就拒
            write_patch("edit x.txt expect 1\nold <<<\naaa\n>>>\nnew <<<\naaa\nccc\n>>>\n")
            try:
                lomf.load(pat)
            except lomf.LomfError as e:
                assert "互为子串" in str(e), f"拒了，但报文不对：{e}"
            else:
                raise AssertionError("插入式替换（old 是 new 的子串）应该被拒 —— "
                                     "它套第二次会再插一遍")

            # ④ 好的一笔：逐字节替换 + 幂等（第二次说"已经套用"，一个字节都不改）
            target.write_bytes(b"aaa\n")
            write_patch("edit x.txt expect 1\nold <<<\naaa\n>>>\nnew <<<\nbbb\n>>>\n")
            assert lomf.cmd_apply(pat, write=True) == 0, "该套用的没套上"
            assert target.read_bytes() == b"bbb\n", f"落盘字节不对：{target.read_bytes()!r}"
            assert lomf.cmd_apply(pat, write=True) == 0, "重复套用应当无害"
            assert target.read_bytes() == b"bbb\n", "重复套用改了盘（该是幂等）"
            _slug, _about, edits = lomf.load(pat)
            assert edits[0].state()[0] == lomf.DONE, "套过之后状态应当是「已套用」"
        finally:
            lomf.ROOT = old_root
    print("      对不上就报错、一处不动；插入式在解析期被拒；套用与重复套用都幂等")


def main() -> int:
    return loment_suite.run(TESTS, "loment_tools_test")


if __name__ == "__main__":
    sys.exit(main())
