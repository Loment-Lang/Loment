#!/usr/bin/env python3
# loment_hotart_test.py — 投影树 → 热更新件（`#1155` 缺的那一步）。
#
# 夹具是两棵真的树（基线与投影），各自用 `tools/lomentc.py` + `tools/lomelf.py` 编成
# hotswap 档；装载件是两份真 `.lomhot` + 新文本拼出来的，不是手写的。

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_hotart as ha  # noqa: E402
import loment_hotfix as hf  # noqa: E402
import loment_hotload as hl  # noqa: E402
import loment_update as lu  # noqa: E402

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


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

#: 一个够小的入口：固定核自己。它有 `_start` 吗？没有 —— 用示例的 bytes 库当入口不行，
#: 所以夹具里放一个自带 `_start` 的最小程序。
PROG = """module hotprog
choose runtime
use bytes
use hotswap

fn add(a: u32, b: u32) -> u32 {
    return a + b;
}

fn _start() -> u32 {
    let x: u32 = add(1, 2);
    syscall4(60, x as u64, 0, 0);
    return 0;
}
"""


def _tree(dst: Path, *, source: str = PROG) -> Path:
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = dst / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    p = dst / "loment" / "tools" / "hotprog.lomt"
    p.write_text(source, encoding="utf-8", newline="\n")
    return dst


def _sign(out: Path) -> None:
    """发行流程里那一步（`#1153`）：一次性密钥真签 `SHA256SUMS`。"""
    kd = Path(tempfile.mkdtemp(prefix="loment-art-key-"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(kd / "key.pem"), "-out", str(kd / "cert.pem"),
                    "-subj", "/CN=loment-art-test", "-days", "1"],
                   check=True, capture_output=True)
    pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout", "-in", str(kd / "cert.pem")],
                         check=True, capture_output=True)
    (out / "loment-signing.pub.pem").write_bytes(pub.stdout)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
                    "-out", str(out / "SHA256SUMS.asc"), str(out / "SHA256SUMS")],
                   check=True, capture_output=True)


def _projection(*shas: str) -> dict:
    return {"baseline": "0.1.5", "release": "v0.1.5-hot.1", "tree": "t",
            "line": [{"sha": s, "subject": "fix", "kind": "fix"} for s in shas],
            "evicted": []}


@test
def test_the_package_is_admitted_by_the_consumer() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old = _tree(t / "old")
        new = _tree(t / "new", source=PROG.replace("return a + b;", "return a + b + 1;"))
        out = ha.package(old, new, "v0.1.5-hot.1", t / "art", entry="loment/tools/hotprog.lomt",
                         projection=_projection("a" * 40))
        assert out is not None
        assert (out / "candidate.hot").is_file() and (out / "hotfix.json").is_file()
        _sign(out)                        # 发行流程那一步；判据里用一次性密钥真签
        man = lu.check(out, old)          # 清单 + 载荷 + 分离签名
        assert man["artifact"]["name"] == "candidate.hot"
    print("      产物过准入：清单指向装载件，digest 对得上")


@test
def test_the_artifact_comes_from_the_projected_tree() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old = _tree(t / "old")
        new = _tree(t / "new", source=PROG.replace("return a + b;", "return a + b + 1;"))
        _eb, _em, old_text = ha.compile_hotswap(old, "loment/tools/hotprog.lomt")
        _nb, new_meta, new_text = ha.compile_hotswap(new, "loment/tools/hotprog.lomt")
        out = ha.package(old, new, "v0.1.5-hot.1", t / "art",
                         entry="loment/tools/hotprog.lomt", projection=_projection("b" * 40))
        blob = (out / "candidate.hot").read_bytes()
        meta = hl.parse(new_meta)
        assert blob.endswith(new_text), "装载件里的文本不是投影树编出来的那一份"
        assert meta["fixed"]["slots"] == len(meta["funcs"])
    print("      装载件的文本逐字节就是投影树的产物（不是 R 的）")


@test
def test_the_load_item_carries_both_tables() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old = _tree(t / "old")
        new = _tree(t / "new", source=PROG.replace("return a + b;", "return a + b + 1;"))
        out = ha.package(old, new, "v0.1.5-hot.1", t / "art",
                         entry="loment/tools/hotprog.lomt", projection=_projection("c" * 40))
        blob = (out / "candidate.hot").read_bytes()
        import struct
        _magic, _sb, _ss, _sg, _sgl, _cb, cs, _cg, _ctl, nreloc, nfunc, tlen, ncalls, _pad = \
            struct.unpack_from("<14I", blob, 0)
        assert nfunc > 0 and ncalls > 0 and tlen > 0
        assert cs == nfunc                     # 同一份程序两侧槽数相同
        assert len(blob) == hl.HDR + nfunc * hl.FUNC + ncalls * hl.CALL + nreloc * hl.RELOC + tlen
    print("      装载件头、两张表、文本的字节数与字段自洽")


@test
def test_a_projection_without_fixes_produces_nothing() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old = _tree(t / "old")
        new = _tree(t / "new")
        out = ha.package(old, new, "v0.1.5-hot.1", t / "art",
                         entry="loment/tools/hotprog.lomt", projection=_projection())
        assert out is None and not (t / "art").exists()
    print("      零 fix → 不产生热更新件")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotart_test")


if __name__ == "__main__":
    sys.exit(main())
