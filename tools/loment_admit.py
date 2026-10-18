#!/usr/bin/env python3
# loment_admit.py — 准入判定（`docs/238`）：**(自身, 对端, 台账序号) → 宽度**，纯函数。
#
# 这是 `docs/218` §2 第 2/3/4 条落地的第一版：宽度**是挣来的**（喂它的是对端那份台账的
# 命中数）、**收窄即时、放宽要窗口**（§2 第 3 条的不对称）、而且**判定可复现**：
#
#   * **时间不入参** —— 只有 `seq`（台账序号），所以"按时间衰减/过期"这一族做不到
#     （§9 点名不做，这里是它的**结构化**版本：判据连"多给一个字段"都不许）；
#   * **两个实现逐字节相同** —— 孪生纪律第一次用在**授权**上：同一份用例，
#     `loment/tools/lomadmit.lomt`（Loment 侧）与本文的 stdout 逐字节比
#     （`tools/loment_admit_test.py`）。
#
# 一句话：**宽度不是一张清单，是一个数的函数** —— 这个数是从台账挣来的，而这份函数
# 任何第三方拿着两份产物与台账就能**重算**（§2 第 4 条）。
#
#   python tools/loment_admit.py cases.txt        # 每行一条用例，出一行准入记录
# 退出码: 0 = 好 / 1 = 用例不成形 / 2 = 用法错
#
# 用例（**恰好 7 个字段，多一个就是错** —— 这条"输入是闭集"就是"没有时钟"那条的性质，
# 见 `--field-count` 那一族用例）:
#
#   <seq> <self> <peer> <hits> <asked> <granted_prev> <held>
#
# 记录（**字段顺序与空格都是判据的一部分**）:
#
#   seq=<n> self=<s> peer=<p> asked=<r> earned=<r> granted=<r> conns=<n> bytes=<n> life=<n> held=<n> why=<code>

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_zwframe as _zwf  # noqa: E402  （只为借一个数：帧长）

ADMIT_VERSION = 1

#: 四档，**从窄到宽** —— 与 Potato v14 的 `net_ladder` 同一套档名（`docs/237`）。
RUNGS = ("w0", "w1", "w2", "w3")

#: 档 → **三刻度**（`docs/218` §2 第 1 条：同时能持几条连接、能传多少字节、能活多久）。
#: **这些数是策略，不是推导出来的**：换一组不改变形状，只改变"挣到一档等于拿到多少"。
#: 宽度**不是**"能不能"这种布尔 —— 三刻度在下面那条记录里各占一格。
WIDTH = {
    # `w0` 的字节数**就是一个帧长**（`docs/240`）：§11 那句"只够验证对端字节"在数上
    # 就是"一帧" —— 而一帧里**装不下载荷**（`loment_zwframe` 的规则 + 两条证伪判据：
    # 多一个字节 ⇒ `bad-length`，往保留位塞 ⇒ `reserved-nonzero`）。
    "w0": (0, _zwf.FRAME_LEN, 1),          # 只够**一次验证交换**：一帧、一个序号
    "w1": (1, 1 << 16, 1 << 12),           # 一条有界连接
    "w2": (64, 1 << 20, 1 << 16),          # 多条并发
    "w3": (4096, 1 << 30, 1 << 20),        # 接受任意对端
}

#: 台账命中数 → 挣到的档（**阈值是策略**）。`hits` 是"per-duty rolling 64"里的命中数
#: （`lom/fuai.lom` 的 `qual_feed` 语义），**per-duty 的那个 duty 就是这份台账的对端** ——
#: 所以同一条记录里的 `peer` 不只是标签，它是**台账的分组键**（§2 第 5 条：对端是字节）。
HITS_TO_W1 = 1
HITS_TO_W2 = 8
HITS_TO_W3 = 32

#: **放宽要窗口**（§2 第 3 条）：候选档连续 `WIDEN_WINDOW` 个序号都更高才生效。
#: 收窄**不等** —— 立刻生效。这条不对称是既有的（`lom/fuai.lom:329` 的
#: *WIDEN_CONFIRM windows; shrink immediate*），这里只是把它落到网络上。
WIDEN_WINDOW = 3


def earned_rung(hits: int) -> str:
    """台账命中数 → 挣到的档。**没有"授予者"**：只有"你最近干得怎么样"（§2 第 2 条）。"""
    if hits >= HITS_TO_W3:
        return "w3"
    if hits >= HITS_TO_W2:
        return "w2"
    if hits >= HITS_TO_W1:
        return "w1"
    return "w0"


def asked_rung(ladder: dict) -> str:
    """产物自己的 `net_ladder` → **它要求的上限**：写过的最宽的那一档（四档都 0 就是 `w0`）。

    这是 Stage 1 与这一层的接缝（`docs/237` §1 那句"先可见可判，再谈随证据变宽"）：
    梯子给**上限**，台账给**挣到的**，判定在两者之间取小的那个 —— 而两者都是**不读源码
    可判**的数。
    """
    top = "w0"
    for r in RUNGS:
        if ladder.get(r, 0) > 0:
            top = r
    return top


def admit(seq: int, self_h: str, peer_h: str, hits: int, asked: str,
          granted_prev: str, held: int) -> dict:
    """**判定本身** —— 上面那七个数进，一条记录出。没有别的输入（这就是"可复现"）。"""
    earned = earned_rung(hits)
    want = earned if RUNGS.index(earned) <= RUNGS.index(asked) else asked
    if want == granted_prev:
        granted, held2, why = want, held + 1, "hold"
    elif RUNGS.index(want) > RUNGS.index(granted_prev):
        held2 = held + 1
        if held2 >= WIDEN_WINDOW:
            granted, why = want, "widen-confirmed"
        else:
            granted, why = granted_prev, "widen-pending"
    else:
        granted, held2, why = want, 0, "shrink-immediate"
    if hits == 0 and granted == "w0":
        why = "zero"                       # `docs/218` §11：宽度从 0 起，而 0 不是拒绝
    conns, nbytes, life = WIDTH[granted]
    return {"seq": seq, "self": self_h, "peer": peer_h, "asked": asked, "earned": earned,
            "granted": granted, "conns": conns, "bytes": nbytes, "life": life,
            "held": held2, "why": why}


def record(rec: dict) -> str:
    """一条记录的**规范形式** —— 字段顺序与分隔符都是判据的一部分。"""
    return (f"seq={rec['seq']} self={rec['self']} peer={rec['peer']} asked={rec['asked']}"
            f" earned={rec['earned']} granted={rec['granted']} conns={rec['conns']}"
            f" bytes={rec['bytes']} life={rec['life']} held={rec['held']} why={rec['why']}")


#: 形状错时的报错行 —— **两侧逐字相同**（判据比它）。`<几>` 是 1 开始的行号。
BAD_FIELDS = "line {n}: expected 7 fields, got {k}"
BAD_SEQ = "line {n}: seq/hits/held must be non-negative integers below 2^63"
BAD_RUNG = "line {n}: asked/granted must be one of w0..w3"

#: `seq/hits/held` 的值域：0..2^63-1。**两边同一个界** —— Loment 侧用 u64 累加
#: （`dec_span`），Python 的 `int` 本来无界，不设界两边就永远对不上（issue #698）。
MAX_COUNT = (1 << 63) - 1
_DEC = re.compile(r"\+?[0-9]+\Z")
#: 字段间空白 —— Loment 侧 `is_space` 只认 space/TAB/CR/LF（不认 \x0b/\x0c）。
_WS = re.compile(r"[ \t\r]+")


def parse_count(tok: str) -> int:
    """与 `loment/tools/lomadmit.lomt` 的 `dec_span` 逐条对应：可选 `+`、至少一位
    ASCII 数字、前导零允许、值域 0..2^63-1。

    先剥前导零再 `int()` —— CPython 的 4300 位字符串上限会让超长前导零只在 Python
    侧报错，而 Loment 侧照读（两侧又漂）。
    """
    if not _DEC.fullmatch(tok):
        raise ValueError(tok)
    digits = (tok[1:] if tok[0] == "+" else tok).lstrip("0") or "0"
    if len(digits) > 19:                       # 2^63-1 是 19 位
        raise ValueError(tok)
    v = int(digits, 10)
    if v > MAX_COUNT:
        raise ValueError(tok)
    return v


def run_cases(text: str) -> tuple[int, str, str]:
    """整份用例进，`(退出码, stdout, stderr)` 出。**两侧共用这一套判定与这一套报错。**"""
    out: list[str] = []
    # 行切分与空白集合同 Loment 侧: 只按 `\n` 切, 只把 space/TAB/CR 当空白。
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip(" \t\r")
        if not line:
            continue
        f = _WS.split(line)
        if len(f) != 7:
            return 1, "", BAD_FIELDS.format(n=n, k=len(f)) + "\n"
        seq, selfh, peer, hits, asked, prev, held = f
        try:
            seq_i, hits_i, held_i = parse_count(seq), parse_count(hits), parse_count(held)
        except ValueError:
            return 1, "", BAD_SEQ.format(n=n) + "\n"
        if asked not in RUNGS or prev not in RUNGS:
            return 1, "", BAD_RUNG.format(n=n) + "\n"
        out.append(record(admit(seq_i, selfh, peer, hits_i, asked, prev, held_i)))
    return 0, "".join(x + "\n" for x in out), ""


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 1:
        print("usage: loment_admit.py <cases-file>", file=sys.stderr)
        return 2
    p = Path(a[0])
    if not p.is_file():
        print(f"loment_admit: cannot open {a[0]}", file=sys.stderr)
        return 2
    rc, out, err = run_cases(p.read_text(encoding="utf-8"))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
