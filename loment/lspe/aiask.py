"""AI 检查器的**通道层**（`docs/262` §10.7）。

`aicheck.py` 只定义请求与回答的形状；这里负责把请求送出去、把回答收回来。
三条通道，全部在**编译期之外**（CI 仍然确定性）：

* `--ask-cmd CMD`：外部程序。bundle JSON 进 stdin，replies JSON 出 stdout。
  不经过 shell（`shlex.split` + 参数表），可离线、可换模型、可被任何语言实现。
* `--ask`：内置 Anthropic Messages API。密钥只从环境读
  （`ANTHROPIC_API_KEY`，或 `LOMENT_AI_KEY`），模型用 `--model` /
  `LOMENT_AI_MODEL` 覆盖。
* `--replies PATH`：人手写或版本库里的回答文件（不需要任何网络）。

三条通道的产物**只有一种用途**：交给 `aicheck.ingest` 变成 Q014。
这里不产生证据、不碰任何判决 —— AI 是问题源，不是证据源。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request

from .aicheck import FORMAT, REPLY_FORMAT, VERSION, dumps
from .questions import LspeError

#: 内置通道的默认模型（可用 `--model` / `LOMENT_AI_MODEL` 覆盖）。
DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

SYSTEM = (
    "You are a skeptical code reviewer for the Loment systems language. "
    "You are given a JSON bundle of open safety questions, one per site. "
    "Your job is to say, for each site, whether the surrounding code looks "
    "suspicious (`suspicious`), fine (`clean`), or you cannot tell (`unsure`). "
    "You do NOT prove anything and your answer never discharges an obligation. "
    "Quote only the site ids from the bundle; never invent ids. "
    "Reply with ONE JSON object and nothing else, in this shape:\n"
    '{"format": "lspe-aicheck-replies", "version": 1, "unit": "<unit>", '
    '"model": "<your model name>", "findings": [{"site": "<id>", '
    '"verdict": "suspicious|clean|unsure", "note": "<one line>"}]}\n'
    "Keep each note under 200 characters and give a concrete reason."
)


def prompt(bundle: dict) -> str:
    """把 bundle 变成一段**确定性**的提示词（同一份 bundle 两次生成相同字节）。"""
    return ("Review the following LSPE sites. Answer with the JSON object only.\n\n"
            + dumps(bundle))


def _first_json(text: str) -> dict:
    """从模型输出里取出第一个 JSON 对象（容忍 ```json 围栏与前后闲话）。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首尾围栏行
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    lo = t.find("{")
    hi = t.rfind("}")
    if lo < 0 or hi <= lo:
        raise LspeError("E036", "模型的回答里没有 JSON 对象")
    try:
        obj = json.loads(t[lo:hi + 1])
    except json.JSONDecodeError as e:
        raise LspeError("E036", f"模型的回答不是 JSON：{e}") from None
    return obj if isinstance(obj, dict) else {}


def normalize(replies: dict, bundle: dict) -> dict:
    """补齐模型常漏的三个固定字段；**不**替它编 findings。

    `format` / `version` / `unit` 由通道补，是因为它们不是判决内容；
    `findings` 缺了就是 E036 —— 一份"什么都没看出来"的回答也必须显式写空表，
    否则"模型没回"与"模型说没问题"就分不开了。
    """
    if not isinstance(replies, dict):
        raise LspeError("E036", "模型的回答不是 JSON 对象")
    if "findings" not in replies:
        raise LspeError("E036", "模型的回答缺 `findings`（空表也要显式写）")
    out = dict(replies)
    out.setdefault("format", REPLY_FORMAT)
    out.setdefault("version", VERSION)
    out.setdefault("unit", bundle.get("unit"))
    if not out.get("model"):
        out["model"] = bundle.get("model") or "unknown"
    if not isinstance(out.get("findings"), list):
        raise LspeError("E036", "`findings` 必须是数组")
    return out


def _run(cmd: list[str], payload: str, timeout: float) -> str:
    try:
        r = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           shell=False, timeout=timeout)
    except FileNotFoundError:
        raise LspeError("E036", f"找不到外部命令：{cmd[0]!r}") from None
    except subprocess.TimeoutExpired:
        raise LspeError("E036", f"外部命令超过 {timeout:g}s 没回答") from None
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        why = tail[-1][:200] if tail else "（无输出）"
        raise LspeError("E036", f"外部命令退出 {r.returncode}：{why}")
    return r.stdout


def _argv(command) -> list[str]:
    """把 `--ask-cmd` 拆成参数表。**不经 shell**，也不让 Windows 的反斜杠被吃掉。

    `shlex.split` 默认是 POSIX 规则：`C:\Python\python.exe` 会被拆成
    `C:Pythonpython.exe`（实测）。所以 Windows 上用 `posix=False` 再剥掉成对引号。
    """
    if isinstance(command, (list, tuple)):
        return [str(x) for x in command]
    if os.name == "nt":
        parts = shlex.split(command, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'"
                else p for p in parts]
    return shlex.split(command)


def run_raw(command, payload: str, timeout: float = 120.0) -> str:
    """跑一条外部命令、拿回 stdout —— 通用通道（`loment slop` 也用它）。

    与 `run_command` 的区别只有一层：这里**不认** LSPE 的回答形状。调用方自己
    校验自己的 JSON（slop 的提示格式与 Q014 回答不是一回事）。
    """
    argv = _argv(command)
    if not argv:
        raise LspeError("E036", "命令是空的")
    return _run(argv, payload, timeout)


def parse_json_text(text: str) -> dict:
    """一段输出里的第一个 JSON 对象（围栏与闲话都容忍）。"""
    return _first_json(text)


def run_command(command, bundle: dict, timeout: float = 120.0) -> dict:
    """`--ask-cmd`：外部程序读 bundle、写 replies。不经 shell。"""
    argv = _argv(command)
    if not argv:
        raise LspeError("E036", "`--ask-cmd` 是空的")
    out = _run(argv, dumps(bundle), timeout)
    return normalize(_first_json(out), bundle)


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LOMENT_AI_KEY")


def ask_anthropic(bundle: dict, model: str | None = None,
                  key: str | None = None, timeout: float = 120.0) -> dict:
    """`--ask`：内置通道。密钥只从环境/参数来，绝不写进 artifact 或日志。"""
    key = key or _api_key()
    if not key:
        raise LspeError("E036", "`--ask` 需要 ANTHROPIC_API_KEY（或 LOMENT_AI_KEY）")
    model = model or os.environ.get("LOMENT_AI_MODEL") or DEFAULT_MODEL
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "temperature": 0,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt(bundle)}],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"content-type": "application/json",
                 "anthropic-version": API_VERSION,
                 "x-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LspeError("E036", f"模型 API 返回 {e.code}：{detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise LspeError("E036", f"连不上模型 API：{e}") from None
    try:
        data = json.loads(raw)
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        raise LspeError("E036", f"模型 API 的回答读不了：{e}") from None
    replies = normalize(_first_json(text), bundle)
    replies.setdefault("model", model)
    return replies


def ask(bundle: dict, *, cmd: str | None = None, model: str | None = None,
        timeout: float = 120.0) -> dict:
    """选一条通道；`cmd` 优先。两条都没有是调用方的错。"""
    if cmd:
        return run_command(cmd, bundle, timeout)
    return ask_anthropic(bundle, model=model, timeout=timeout)


__all__ = ["DEFAULT_MODEL", "FORMAT", "SYSTEM", "ask", "ask_anthropic",
           "normalize", "parse_json_text", "prompt", "run_command", "run_raw"]
