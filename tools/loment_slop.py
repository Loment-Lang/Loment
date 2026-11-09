#!/usr/bin/env python3
"""loment_slop.py — **AI slop 的机械代理判据**（`docs/318`）。

## 它修的是什么

"AI slop" 不是一种语气，是**一组可观察的变更形状**：范围失控、测试空转、
复制粘贴、注释复述代码、新增的公开 API 没有调用点、把依赖/生成物混进手写改动。
这些形状都**不需要模型**就能看出来 —— 模型只在"这些点值不值得人看一眼"上做提示。

## 三条纪律（与 LSPE 的 AI 检查器同一条）

1. **机械判据是判决，模型是提示。** `--ask-cmd` 的产出只打印成 `HINT`，
   不改 findings、不改退出码。
2. **确定性。** 同一棵树、同一个 base 跑两遍，JSON 逐字节相同。
3. **默认不阻断。** `--strict` 才让 medium/high 变退出码 1；CI 先只报告。

## 用法

    python tools/loment_slop.py check                    # 默认 diff origin/main...HEAD
    python tools/loment_slop.py check --worktree         # 连未提交的改动一起看
    python tools/loment_slop.py check --area tools --area loment
    python tools/loment_slop.py check --strict --json
    python tools/loment_slop.py check --ask-cmd 'my-model --json'

退出码：0 = 没有（或没开 strict）；1 = strict 下有 medium/high；2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 默认阈值。数字是**政策**，不是自然常数 —— 调它们要改这一行并说明理由。
MAX_LINES = 400
MAX_FILES = 12
DUP_MIN_LINES = 5

#: 只看"我们写的代码"。`docs/` 是散文，`deps/` 是包，`kernel/`/`sdk/` 是 vendored，
#: `neg/` 是**故意写坏的夹具**，`corpus` 是语料 —— 拿它们做重复检测只会制造假阳性。
SKIP_DIRS = {".git", "build", "dist", "docs", "deps", "kernel", "sdk",
             "videos", "book", "papers", "deliverables", "node_modules"}
SKIP_PARTS = ("loment/selfhost/neg/", "loment/examples/", "loment/corpus")

CODE_SUFFIXES = (".py", ".lomt", ".lom", ".c", ".rs", ".sh", ".yml", ".yaml")

SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}
STRICT_AT = {"medium", "high"}


class SlopError(Exception):
    """用法/环境错误（退出码 2）—— 不是发现。"""


def _git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       shell=False)
    if check and r.returncode != 0:
        raise SlopError((r.stderr or r.stdout).strip().splitlines()[-1]
                        if (r.stderr or r.stdout).strip() else f"git {args[0]} 失败")
    return r.stdout


def _skipped(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    return any(path.replace("\\", "/").startswith(p) for p in SKIP_PARTS)


def _untracked(root: Path) -> list[str]:
    """git diff 看不见的新文件（未 `git add` 的）。PR 上没有它们，本地看改动时必须有。"""
    raw = _git(root, "ls-files", "--others", "--exclude-standard")
    return [p for p in raw.splitlines() if p and not _skipped(p)]


def changed_files(root: Path, base: str, worktree: bool = False) -> dict[str, dict]:
    """`{path: {add, del, status}}`。`worktree=True` 时连未提交的改动一起看。"""
    spec = [base] if worktree else [f"{base}...HEAD"]
    num = _git(root, "diff", "--numstat", *spec)
    stat = _git(root, "diff", "--name-status", *spec)
    status = {}
    for line in stat.splitlines():
        bits = line.split("\t")
        if len(bits) >= 2:
            status[bits[-1]] = bits[0][0]
    out: dict[str, dict] = {}
    for line in num.splitlines():
        bits = line.split("\t")
        if len(bits) != 3:
            continue
        a, d, path = bits
        if _skipped(path):
            continue
        out[path] = {"add": int(a) if a.isdigit() else 0,
                     "del": int(d) if d.isdigit() else 0,
                     "status": status.get(path, "M")}
    if worktree:
        for path in _untracked(root):
            try:
                n = len((root / path).read_text(encoding="utf-8",
                                                errors="replace").splitlines())
            except OSError:
                n = 0
            out.setdefault(path, {"add": n, "del": 0, "status": "A"})
    return out


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(root: Path, base: str, worktree: bool = False) -> dict[str, list[tuple[int, str]]]:
    """`{path: [(line_no, text)]}`，只收新加的行（`-U0`）。"""
    spec = [base] if worktree else [f"{base}...HEAD"]
    raw = _git(root, "diff", "-U0", "--no-color", *spec)
    out: dict[str, list[tuple[int, str]]] = {}
    path = None
    ln = 0
    for line in raw.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("--- "):
            continue
        m = _HUNK.match(line)
        if m:
            ln = int(m.group(1))
            continue
        if path is None or _skipped(path):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, []).append((ln, line[1:]))
            ln += 1
    if worktree:
        for path in _untracked(root):
            if path in out:
                continue
            try:
                text = (root / path).read_text(encoding="utf-8",
                                               errors="replace")
            except OSError:
                continue
            out[path] = [(i + 1, t) for i, t in enumerate(text.splitlines())]
    return out


def finding(kind: str, severity: str, message: str, *, file: str | None = None,
            line: int | None = None, fix: str | None = None,
            data: dict | None = None) -> dict:
    fid = f"{kind}:{file or '-'}:{line or 0}"
    return {"id": fid, "kind": kind, "severity": severity, "message": message,
            "file": file, "line": line, "fix": fix, "data": data or {}}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).rstrip(";,")


def rule_scope(files: dict[str, dict], areas: list[str], *,
               max_lines: int = MAX_LINES, max_files: int = MAX_FILES) -> list[dict]:
    out = []
    total = sum(v["add"] + v["del"] for v in files.values())
    if total > max_lines or len(files) > max_files:
        out.append(finding(
            "scope.size", "medium",
            f"改动 {total} 行 / {len(files)} 个文件（上限 {max_lines} 行 / {max_files} 个）"
            " —— 拆成能单独 review 的几笔",
            fix="按主题拆 PR；机械产物单独一条提交"))
    if areas:
        outside = sorted(p for p in files
                         if not any(p == a or p.startswith(a.rstrip("/") + "/")
                                    for a in areas))
        for p in outside:
            out.append(finding(
                "scope.outside_area", "low",
                f"`{p}` 不在声明的范围里（--area {' '.join(areas)}）",
                file=p, fix="移出本 PR，或把 --area 写全"))
    return out


_TEST_FILE = re.compile(r"(^|/)[^/]*_test\.py$")
_TRIVIAL = (
    (re.compile(r"^\s*assert\s+True\s*$"), "assert True"),
    (re.compile(r"^\s*assert\s+False\s*$"), "assert False"),
    (re.compile(r"^\s*assert\s+(\w+)\s*==\s*\1\s*$"), "assert x == x"),
    (re.compile(r"^\s*assert\s+(\d+)\s*==\s*\1\s*$"), "assert N == N"),
)


def rule_trivial_asserts(added: dict[str, list[tuple[int, str]]]) -> list[dict]:
    out = []
    for path, lines in added.items():
        if not _TEST_FILE.search(path):
            continue
        for ln, text in lines:
            for rx, label in _TRIVIAL:
                if rx.match(text):
                    out.append(finding(
                        "tests.trivial", "medium",
                        f"恒真的断言（{label}）不测任何东西",
                        file=path, line=ln,
                        fix="断言被测函数的具体结果，或删掉这条测试"))
                    break
    return out


def rule_comment_restate(added: dict[str, list[tuple[int, str]]]) -> list[dict]:
    """注释与紧跟着的代码行**逐字相同**（去掉标记与标点）—— 复述不是解释。"""
    out = []
    for path, lines in added.items():
        for i in range(len(lines) - 1):
            ln, text = lines[i]
            s = text.strip()
            if s.startswith("#"):
                body = s.lstrip("#").strip()
            elif s.startswith("//"):
                body = s[2:].strip()
            else:
                continue
            nxt = lines[i + 1][1].strip()
            if not body or not nxt:
                continue
            if _norm(body) == _norm(nxt):
                out.append(finding(
                    "comments.restate", "low",
                    "注释与下一行代码逐字相同 —— 删掉注释，或写**为什么**",
                    file=path, line=ln,
                    fix="把注释改成约束/理由；没有理由就删"))
    return out


def _base_lines(root: Path, base: str, path: str) -> list[str] | None:
    """base 版本里那份文件的行（没有就 None）—— 重复检测对着**旧树**看。"""
    r = subprocess.run(["git", "-C", str(root), "show", f"{base}:{path}"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False)
    if r.returncode != 0:
        return None
    return [_norm(x) for x in r.stdout.splitlines()]


def rule_duplicate_blocks(root: Path, added: dict[str, list[tuple[int, str]]],
                          base: str = "HEAD", min_lines: int = DUP_MIN_LINES) -> list[dict]:
    """新加的连续代码块在**base 树**里已经有一份逐字相同的 —— 复制粘贴。

    对着 base 看，不对着工作树看：工作树里新加的那一块会和自己匹配，规则就永远沉默。
    """
    if min_lines < 2:
        return []
    out = []
    cache: dict[str, list[str] | None] = {}
    for path, lines in added.items():
        seq = [(_norm(t), ln) for ln, t in lines]
        keys: set[str] = set()
        for i in range(len(seq) - min_lines + 1):
            win = [x[0] for x in seq[i:i + min_lines]]
            if sum(1 for x in win if len(x) >= 8) < min_lines:
                continue
            key = max(win, key=len)
            # 每个文件只看有限个窗口：一条 `git grep` 一次进程，语料大时要封顶。
            if len(key) < 16 or key in keys or len(keys) >= 48:
                continue
            keys.add(key)
            try:
                hits = _git(root, "grep", "-n", "-F", "--untracked", "--",
                            key, "*.py", "*.lomt", "*.lom", "*.c", "*.rs")
            except SlopError:
                continue
            found = None
            for h in hits.splitlines()[:32]:
                hp = h.split(":", 1)[0]
                if hp == path or _skipped(hp):
                    continue
                if hp not in cache:
                    cache[hp] = _base_lines(root, base, hp)
                old = cache[hp]
                if not old:
                    continue
                for j in range(len(old) - min_lines + 1):
                    if old[j:j + min_lines] == win:
                        found = (hp, j + 1)
                        break
                if found:
                    break
            if found:
                out.append(finding(
                    "duplicates.block", "medium",
                    f"{min_lines} 行与 `{found[0]}:{found[1]}` 逐字相同 —— 复制粘贴",
                    file=path, line=seq[i][1],
                    fix="抽成一处共享函数，或删掉多余的一份"))
                break
    return out


_PUB_FN = re.compile(r"^\s*pub\s+fn\s+([A-Za-z_]\w*)")


def rule_unused_public_api(root: Path, added: dict[str, list[tuple[int, str]]]) -> list[dict]:
    """新加的 `pub fn` 在**代码**里一次都没被调用（只在定义处出现）。"""
    out = []
    for path, lines in added.items():
        if not path.endswith((".lomt", ".lom")):
            continue
        for ln, text in lines:
            m = _PUB_FN.match(text)
            if not m:
                continue
            name = m.group(1)
            try:
                hits = _git(root, "grep", "-n", "-w", "--untracked", "--",
                            name, "*.py", "*.lomt", "*.lom", "*.c", "*.rs")
            except SlopError:
                hits = ""
            places = [h for h in hits.splitlines() if h.strip()]
            callable_hits = [h for h in places
                             if not (h.startswith(f"{path}:{ln}:") or
                                     re.search(rf"\bpub\s+fn\s+{re.escape(name)}\b", h))]
            if not callable_hits:
                out.append(finding(
                    "api.unused", "medium",
                    f"新加的 `pub fn {name}` 在代码里没有调用点",
                    file=path, line=ln,
                    fix="接上一个真实调用点，或收回成私有（去掉 `pub`）"))
    return out


def rule_tests_missing(files: dict[str, dict]) -> list[dict]:
    out = []
    tests_changed = any(_TEST_FILE.search(p) for p in files)
    for path, meta in files.items():
        if _TEST_FILE.search(path) or meta["status"] == "D":
            continue
        src = (path.startswith(("tools/", "loment/")) and
               path.endswith((".py", ".lomt")))
        if not src or path.endswith(("__init__.py",)):
            continue
        if meta["status"] == "A" and not tests_changed:
            out.append(finding(
                "tests.missing", "medium",
                "新增的源文件没有同时新增/改动判据",
                file=path,
                fix="加一条判据（先红），或说明为什么这一份不需要"))
        elif not tests_changed and meta["add"] + meta["del"] > 30:
            out.append(finding(
                "tests.missing", "low",
                "改了 30 行以上，但本 PR 没有任何 `*_test.py` 改动",
                file=path,
                fix="补判据，或在 PR 里说明验证方式"))
    return out


def rule_new_deps(files: dict[str, dict]) -> list[dict]:
    out = []
    for path in sorted(files):
        if path.startswith("deps/") or path in ("lompi.lock", "loment.conf"):
            out.append(finding(
                "deps.new", "info",
                "依赖 / 锁文件被改动 —— 确认是必要的，并且没有把整棵树带进来",
                file=path, fix="在 PR 里写明新增依赖的用途"))
    return out


def check(root: Path = ROOT, base: str = "origin/main", *,
          worktree: bool = False, areas: list[str] | None = None,
          max_lines: int = MAX_LINES, max_files: int = MAX_FILES,
          dup_min_lines: int = DUP_MIN_LINES) -> dict:
    """跑全部机械判据。返回 `{"base", "files", "findings", "summary"}`。"""
    root = Path(root)
    files = changed_files(root, base, worktree)
    added = added_lines(root, base, worktree)
    findings: list[dict] = []
    findings += rule_scope(files, list(areas or []),
                           max_lines=max_lines, max_files=max_files)
    findings += rule_trivial_asserts(added)
    findings += rule_comment_restate(added)
    findings += rule_duplicate_blocks(root, added, base, dup_min_lines)
    findings += rule_unused_public_api(root, added)
    findings += rule_tests_missing(files)
    findings += rule_new_deps(files)
    findings.sort(key=lambda f: (-SEV_ORDER[f["severity"]], f["id"]))
    by_sev = {s: sum(1 for f in findings if f["severity"] == s)
              for s in ("high", "medium", "low", "info")}
    return {"base": base, "worktree": worktree,
            "files": dict(sorted(files.items())),
            "findings": findings,
            "summary": {"findings": len(findings), **by_sev}}


def text_lines(report: dict) -> list[str]:
    out = [f"[slop] base {report['base']}"
           f"{' + worktree' if report.get('worktree') else ''}: "
           f"{len(report['files'])} file(s), "
           f"{report['summary']['findings']} finding(s)"]
    for f in report["findings"]:
        where = f["file"] or "-"
        if f["line"]:
            where += f":{f['line']}"
        out.append(f"SLOP[{f['severity']}] {f['id']} {where} — {f['message']}")
        if f.get("fix"):
            out.append(f"          fix: {f['fix']}")
    if not report["findings"]:
        out.append("SLOP[none] 没有命中任何代理判据")
    return out


# ---------------------------------------------------------------- AI 提示面

AI_HINTS_FORMAT = "loment-slop-hints"


def ai_bundle(report: dict, added: dict[str, list[tuple[int, str]]],
              max_lines: int = 80) -> dict:
    """给模型的输入：发现 + 一小段 diff。**确定性**（排序固定、截断固定）。"""
    excerpt = []
    budget = max_lines
    for path in sorted(added):
        for ln, text in added[path]:
            if budget <= 0:
                break
            excerpt.append(f"{path}:{ln}: {text}")
            budget -= 1
    return {
        "format": "loment-slop-bundle",
        "version": 1,
        "base": report.get("base"),
        "findings": [{"id": f["id"], "kind": f["kind"],
                      "severity": f["severity"], "file": f["file"],
                      "line": f["line"], "message": f["message"]}
                     for f in report.get("findings", [])],
        "diff_excerpt": excerpt,
    }


AI_SYSTEM = (
    "You review code changes for low-quality AI-generated work ('slop'). "
    "You are given mechanical findings and a truncated diff. For each finding, "
    "say whether you agree it looks like slop (`agree`), it is a false positive "
    "(`disagree`), or you cannot tell (`unsure`), with a one-line note. "
    "You may also add `extra` hints, but they never become findings. "
    "Reply with one JSON object only: "
    '{"format": "loment-slop-hints", "version": 1, "model": "<name>", '
    '"hints": [{"finding": "<id>", "verdict": "agree|disagree|unsure", '
    '"note": "<one line>"}], "extra": ["<optional>"]}'
)


def ai_ingest(replies: dict, report: dict) -> dict:
    """吃一份提示：只校验形状与引用，**绝不**改 findings。"""
    if replies.get("format") != AI_HINTS_FORMAT:
        raise SlopError(f"不是 slop 提示：format={replies.get('format')!r}")
    known = {f["id"] for f in report.get("findings", [])}
    hints, unknown = [], []
    for i, h in enumerate(replies.get("hints") or []):
        fid = str((h or {}).get("finding") or "")
        verdict = str((h or {}).get("verdict") or "")
        if verdict not in ("agree", "disagree", "unsure"):
            raise SlopError(f"hints[{i}] 的 verdict 不认识：{verdict!r}")
        if fid not in known:
            unknown.append(fid)
            continue
        hints.append({"finding": fid, "verdict": verdict,
                      "note": str(h.get("note") or ""),
                      "source": f"ai:{replies.get('model') or 'unknown'}"})
    extra = [str(x) for x in (replies.get("extra") or [])]
    return {"hints": hints, "unknown": unknown, "extra": extra}


def _argv(command) -> list[str]:
    """参数表，不经 shell。Windows 上用 `posix=False`，否则 `C:\...` 的反斜杠会被吃掉。"""
    if isinstance(command, (list, tuple)):
        return [str(x) for x in command]
    if os.name == "nt":
        parts = shlex.split(command, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'"
                else p for p in parts]
    return shlex.split(command)


def _first_json(text: str) -> dict:
    """外部程序输出里的第一个 JSON 对象（容忍 ```json 围栏与前后闲话）。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    lo, hi = t.find("{"), t.rfind("}")
    if lo < 0 or hi <= lo:
        raise SlopError("提示里没有 JSON 对象")
    obj = json.loads(t[lo:hi + 1])
    if not isinstance(obj, dict):
        raise SlopError("提示必须是 JSON 对象")
    return obj


def _ai_ask(command, bundle: dict, timeout: float) -> dict:
    """外部模型提示：bundle 进 stdin、hints 出 stdout。**这一层永不阻断**（见 main）。"""
    payload = json.dumps({"system": AI_SYSTEM, "bundle": bundle},
                         ensure_ascii=False, sort_keys=True)
    argv = _argv(command)
    if not argv:
        raise SlopError("`--ask-cmd` 是空的")
    r = subprocess.run(argv, input=payload, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False,
                       timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise SlopError(f"提示命令退出 {r.returncode}："
                        f"{(tail[-1][:200] if tail else '（无输出）')}")
    return _first_json(r.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_slop",
                                 description="AI slop 的机械代理判据")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--root", default=str(ROOT),
                   help="仓库根（默认：本工具所在的那棵树）")
    c.add_argument("--base", default="origin/main")
    c.add_argument("--worktree", action="store_true",
                   help="连未提交的改动一起看")
    c.add_argument("--area", action="append", default=None, metavar="PATH",
                   help="本 PR 声明改动的范围（可重复）")
    c.add_argument("--max-lines", type=int, default=MAX_LINES)
    c.add_argument("--max-files", type=int, default=MAX_FILES)
    c.add_argument("--dup-min-lines", type=int, default=DUP_MIN_LINES)
    c.add_argument("--json", action="store_true")
    c.add_argument("--strict", action="store_true",
                   help="medium/high 命中时退出码 1")
    c.add_argument("--ask-cmd", default=None, metavar="CMD",
                   help="外部模型：bundle 进 stdin、hints 出 stdout（永不阻断）")
    c.add_argument("--hints-out", default=None, metavar="PATH")
    c.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args(argv)

    try:
        report = check(Path(a.root), a.base,
                       worktree=a.worktree, areas=a.area,
                       max_lines=a.max_lines, max_files=a.max_files,
                       dup_min_lines=a.dup_min_lines)
    except SlopError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 2

    hints = None
    if a.ask_cmd:
        try:
            added = added_lines(Path(a.root), a.base, a.worktree)
            replies = _ai_ask(a.ask_cmd, ai_bundle(report, added), a.timeout)
            hints = ai_ingest(replies, report)
            if a.hints_out:
                Path(a.hints_out).write_text(
                    json.dumps(replies, ensure_ascii=False, indent=1,
                               sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")
        except Exception as e:   # noqa: BLE001 — 提示层永不阻断
            # 提示层的失败**不动判决**：报告照出，提示缺席（含模型/JSON/进程错误）。
            print(f"[slop] 提示层跳过：{e}", file=sys.stderr)

    if a.json:
        out = dict(report)
        if hints is not None:
            out["hints"] = hints
        print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        for line in text_lines(report):
            print(line)
        if hints is not None:
            for h in hints["hints"]:
                print(f"HINT {h['finding']} ({h['verdict']}): {h['note']}")
            for fid in hints["unknown"]:
                print(f"HINT unknown finding {fid}")
            for x in hints["extra"]:
                print(f"HINT extra: {x}")
    if a.strict and any(f["severity"] in STRICT_AT for f in report["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
