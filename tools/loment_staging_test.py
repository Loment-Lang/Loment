#!/usr/bin/env python3
# loment_staging_test.py — GitHub 与运行进程之间的那一格（`#1154`）。
#
# 每一支都问同一件事：**换的那条路会不会碰到网络、会不会捡错东西、被拒的会不会被捡起来？**
# 夹具是一棵从本仓拷出来的树 + 一份真签名的候选。

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_release  # noqa: E402
import loment_staging as st  # noqa: E402
import loment_hotfix as hf  # noqa: E402
import loment_update as lu  # noqa: E402

BASE = loment_release.RELEASE

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
)


def _tree(dst: Path, *, release: str | None = None) -> Path:
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = dst / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    if release is not None:
        p = dst / "tools" / "loment_release.py"
        text = p.read_text(encoding="utf-8")
        text = lu._RELEASE_RE.sub(f'RELEASE = "{release}"', text, count=1)
        p.write_text(text, encoding="utf-8", newline="\n")
    return dst


def _candidate(dst: Path, tree: Path, release: str, *, sign: bool = True) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    man = hf.build(tree, release)
    payload = dst / f"loment-hotfix-{release}.tar.gz"
    payload.write_bytes(b"hotfix payload\n")
    man["artifact"] = {"name": payload.name, "sha256": lu.sha256_file(payload)}
    (dst / "hotfix.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8", newline="\n")
    sums = dst / "SHA256SUMS"
    sums.write_text(f"{man['artifact']['sha256']}  {payload.name}\n",
                    encoding="utf-8", newline="\n")
    if sign:
        kd = Path(tempfile.mkdtemp(prefix="loment-stage-key-"))
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(kd / "key.pem"), "-out", str(kd / "cert.pem"),
                        "-subj", "/CN=loment-staging-test", "-days", "1"],
                       check=True, capture_output=True)
        pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout",
                              "-in", str(kd / "cert.pem")], check=True, capture_output=True)
        (dst / "loment-signing.pub.pem").write_bytes(pub.stdout)
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
                        "-out", str(dst / "SHA256SUMS.asc"), str(sums)],
                       check=True, capture_output=True)
    return dst


def _home(t: Path) -> Path:
    h = t / "hot"
    h.mkdir(parents=True, exist_ok=True)
    os.environ["LOMENT_HOT_HOME"] = str(h)
    return h


@test
def test_a_staged_candidate_is_picked_up_in_one_poll() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        h = _home(t)
        tree = _tree(t / "tree")
        cand = _candidate(t / "cand", tree, f"v{BASE}-hot.1")
        assert st.poll(BASE) is None
        st.stage(cand, tree, f"v{BASE}-hot.1")
        got = st.poll(BASE)
        assert got is not None and (got / "hotfix.json").is_file(), got
        assert got.name == f"v{BASE}-hot.1"
        print(f"      放进去一次轮询就捡到：{got.relative_to(h)}")


@test
def test_a_bad_signature_lands_in_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        h = _home(t)
        tree = _tree(t / "tree")
        cand = _candidate(t / "cand", tree, f"v{BASE}-hot.2")
        (cand / "SHA256SUMS.asc").write_bytes(b"not a signature\n")
        out = st.stage(cand, tree, f"v{BASE}-hot.2")
        assert out.parent.name == "rejected", out
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["code"] == "signature", rec
        assert st.poll(BASE) is None
        print(f"      签名不对 → rejected/{out.name}（{rec['code']}），轮询看不见它")


@test
def test_a_stable_release_lands_in_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        tree = _tree(t / "tree")
        cand = _candidate(t / "cand", tree, f"v{BASE}")
        out = st.stage(cand, tree, f"v{BASE}")
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["code"] == "not-prerelease", rec
        assert st.poll(BASE) is None
        print("      稳定版 → rejected（not-prerelease）")


@test
def test_candidates_for_two_baselines_stay_apart() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        h = _home(t)
        a = _tree(t / "a")
        b = _tree(t / "b", release="0.1.6")
        st.stage(_candidate(t / "ca", a, f"v{BASE}-hot.1"), a, f"v{BASE}-hot.1")
        st.stage(_candidate(t / "cb", b, "v0.1.6-hot.1"), b, "v0.1.6-hot.1")
        assert st.poll(BASE).name == f"v{BASE}-hot.1"
        assert st.poll("0.1.6").name == "v0.1.6-hot.1"
        assert (h / BASE / "incoming").is_dir() and (h / "0.1.6" / "incoming").is_dir()
        print("      两条基线各看各的目录，谁也捡不到对方的候选")


@test
def test_fetching_the_same_tag_twice_leaves_one_candidate() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        h = _home(t)
        tree = _tree(t / "tree")
        src = _candidate(t / "src", tree, f"v{BASE}-hot.3")
        first = st.fetch(f"v{BASE}-hot.3", tree, src_dir=src)
        second = st.fetch(f"v{BASE}-hot.3", tree, src_dir=src, allow_rollback=True)
        assert first == second
        names = [p.name for p in (h / BASE / "incoming").iterdir()]
        assert names == [f"v{BASE}-hot.3"], names
        print("      同一个 tag 取两次：还是一条候选（替换，不是追加）")


@test
def test_the_swap_path_opens_no_socket() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        tree = _tree(t / "tree")
        cand = _candidate(t / "cand", tree, f"v{BASE}-hot.4")
        real = socket.socket

        def boom(*a, **kw):
            raise AssertionError("换的那条路开了 socket")

        socket.socket = boom  # type: ignore[assignment]
        try:
            st.stage(cand, tree, f"v{BASE}-hot.4")
            assert st.poll(BASE) is not None
        finally:
            socket.socket = real  # type: ignore[assignment]
        print("      禁掉 socket 之后 stage/poll 照常工作 —— 那条路不做网络 I/O")


def main() -> int:
    return loment_suite.run(TESTS, "loment_staging_test")


if __name__ == "__main__":
    sys.exit(main())
