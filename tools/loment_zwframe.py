#!/usr/bin/env python3
# loment_zwframe.py — 零宽阶段那**一帧**：`docs/218` §11 的静态规则（`docs/240`）
#
# §11 说宽度从 0 起，而"宽度 0 ≠ 拒绝"：它是**只够验证对端字节**的那一点流量 ——
# "传的只有**陈述与证明**"。§11 自己给了证伪方式：
#
# > 如果"零宽流量"最后能被用来传任意字节（比如把载荷塞进形式对象的字段里），
# > 那它就是**换了名字的清单**，这一轮作废。判据形态：零宽阶段能发什么，必须由一条
# > **静态规则**钉死（"只允许产物字节与它的证明"），而不是靠约定俗成。
#
# 这一份就是那条规则，能跑的那种：**一帧定长、没有载荷**。
#
#   LZW0 | kind(1) | reserved(3, 必须全 0) | hash32 | digest32        = 72 字节
#
# * **kind** 1 = 陈述（那份产物/它的形式对象），2 = 证明（对它的一枚摘要）；
# * **没有长度字段，也没有载荷字段** —— 所以"多塞点东西"在**形状上**就落不下去：
#   多一个字节是 `bad-length`，往保留位塞是 `reserved-nonzero`。这两条就是证伪判据。
# * 帧里的两个 32 字节都是**定长摘要**（产物哈希 / 证明摘要），不是可伸缩的不透明块 ——
#   一个能伸缩的字段就是一条载荷通道。
#
#   python tools/loment_zwframe.py cases.txt     # 每行一条用例，出一行结论
# 退出码: 0 = 好 / 1 = 用例不成形 / 2 = 用法错
#
# 用例（每行第一个词选动作）:
#   make <kind> <hash32hex> <digest32hex>   -> ok kind=<k> len=72 frame=<144 hex>
#   parse <hex>                             -> ok kind=<k> len=<n> 或 reject why=<code>
#
# 拒绝码（**封闭的一小张表**，两侧逐字相同；每一条都对应"这条路不能用来传载荷"）:
#   bad-hex          输入不是偶数长/含非十六进制字符
#   bad-length       不是 72 字节 —— **多一个字节也在这里**（证伪判据之一）
#   bad-magic        开头不是 LZW0
#   bad-kind         种类不在 {1,2}
#   reserved-nonzero 保留位不是全 0 —— **另一条塞载荷的路**（证伪判据之二）

from __future__ import annotations

import re
import sys
from pathlib import Path

#: 帧长（**一个数，只有这一处**；判据拿它与准入里 `w0` 的字节预算对账）。
FRAME_LEN = 72
MAGIC = b"LZW0"
#: 种类：1 = 陈述，2 = 证明。**只有这两个** —— 多一个种类就是多一条通道。
KINDS = (1, 2)
HASH_LEN = 32
DIGEST_LEN = 32
#: 保留位在帧里的位置与长度（必须全 0）。
RESERVED_OFF, RESERVED_LEN = 5, 3

BAD_HEX = "bad-hex"
BAD_LENGTH = "bad-length"
BAD_MAGIC = "bad-magic"
BAD_KIND = "bad-kind"
RESERVED_NONZERO = "reserved-nonzero"

#: 十进制 v. `dec_span`（Loment 侧）：可选 `+`、至少一位 ASCII 数字、前导零允许、
#: 值域 0..2^63-1。**两边用同一个接受集**，否则同一份用例会被一份收下、另一份拒掉
#: （issue #698/#699）。
MAX_DEC = (1 << 63) - 1
_DEC = re.compile(r"\+?[0-9]+\Z")
#: 字段间空白 —— Loment 侧 `is_space` 只认 space/TAB/CR/LF（不认 \x0b/\x0c）。
_WS = re.compile(r"[ \t\r]+")


def parse_dec(tok: str) -> int:
    """与 `loment/tools/lomzwframe.lomt` 的 `dec_span` 逐条对应。

    先剥前导零再 `int()`：CPython 对超长十进制串有自己的 4300 位上限，而那会
    让"一千个 0 加一个 1"在 Python 侧变成错误、在 Loment 侧还能读 —— 两边又漂了。
    """
    if not _DEC.fullmatch(tok):
        raise ValueError(tok)
    digits = (tok[1:] if tok[0] == "+" else tok).lstrip("0") or "0"
    if len(digits) > 19:                       # 2^63-1 是 19 位
        raise ValueError(tok)
    v = int(digits, 10)
    if v > MAX_DEC:
        raise ValueError(tok)
    return v


def make_frame(kind: int, artifact_hash: bytes, proof: bytes) -> bytes:
    """把（陈述 | 证明）编成一帧 —— **定长**，没有别的地方放东西。"""
    if kind not in KINDS:
        raise ValueError(BAD_KIND)
    if len(artifact_hash) != HASH_LEN or len(proof) != DIGEST_LEN:
        raise ValueError(BAD_LENGTH)
    return MAGIC + bytes([kind]) + bytes(RESERVED_LEN) + artifact_hash + proof


def parse_frame(buf: bytes) -> tuple[str, dict]:
    """解一帧；`(结论, 细节)`。**顺序即判据**：长度先看 —— 多一个字节也是 `bad-length`。"""
    if len(buf) != FRAME_LEN:
        return "reject", {"why": BAD_LENGTH}
    if buf[:4] != MAGIC:
        return "reject", {"why": BAD_MAGIC}
    kind = buf[4]
    if kind not in KINDS:
        return "reject", {"why": BAD_KIND}
    if any(buf[RESERVED_OFF:RESERVED_OFF + RESERVED_LEN]):
        return "reject", {"why": RESERVED_NONZERO}
    return "ok", {"kind": kind, "len": len(buf)}


def run_cases(text: str) -> tuple[int, str, str]:
    """整份用例进，`(退出码, stdout, stderr)` 出。**两侧共用这一套结论与这一套措辞。**"""
    out: list[str] = []
    # 行切分与空白集合同 Loment 侧: 只按 `\n` 切, 只把 space/TAB/CR 当空白
    # (`str.splitlines()` 还会在 \x0b/\x0c/ 等处切, 那会让两侧"同一份输入"不同形)。
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip(" \t\r")
        if not line:
            continue
        f = _WS.split(line)
        if f[0] == "make":
            if len(f) != 4:
                return 1, "", f"line {n}: make needs <kind> <hash32hex> <digest32hex>\n"
            try:
                kind = parse_dec(f[1])
            except ValueError:
                return 1, "", f"line {n}: {BAD_KIND}\n"
            try:
                h = bytes.fromhex(f[2])
                d = bytes.fromhex(f[3])
            except ValueError:
                return 1, "", f"line {n}: {BAD_HEX}\n"
            try:
                fr = make_frame(kind, h, d)
            except ValueError as e:
                return 1, "", f"line {n}: {e}\n"
            out.append(f"ok kind={kind} len={len(fr)} frame={fr.hex()}")
        elif f[0] == "parse":
            if len(f) != 2:
                return 1, "", f"line {n}: parse needs <hex>\n"
            try:
                buf = bytes.fromhex(f[1])
            except ValueError:
                out.append(f"reject why={BAD_HEX}")
                continue
            verdict, det = parse_frame(buf)
            out.append(f"ok kind={det['kind']} len={det['len']}" if verdict == "ok"
                       else f"reject why={det['why']}")
        else:
            # 不回 repr: Loment 侧回显的是原样的 token (issue #699)
            return 1, "", f"line {n}: unknown action {f[0]}\n"
    return 0, "".join(x + "\n" for x in out), ""


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 1:
        print("usage: loment_zwframe.py <cases-file>", file=sys.stderr)
        return 2
    p = Path(a[0])
    if not p.is_file():
        print(f"loment_zwframe: cannot open {a[0]}", file=sys.stderr)
        return 2
    rc, out, err = run_cases(p.read_text(encoding="utf-8"))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
