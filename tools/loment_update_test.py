#!/usr/bin/env python3
# loment_update_test.py — 热更新准入判据的判据（`#1147`）。
#
# 每一支都问同一个问题：**换掉一个字节，准入还过不过？**
# 夹具不是手写的清单：每份 manifest 都由 `loment_update.manifest()` 从**真的树**算出来，
# 树是从本仓拷出来再改一处的。签名也用 openssl 真签真验。

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
)

_KEYDIR: Path | None = None


def _keydir() -> Path:
    global _KEYDIR
    if _KEYDIR is not None:
        return _KEYDIR
    d = Path(tempfile.mkdtemp(prefix="loment-hot-key-"))
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem"),
         "-subj", "/CN=loment-hot-update-test", "-days", "1"],
        check=True, capture_output=True,
    )
    pub = subprocess.run(
        ["openssl", "x509", "-pubkey", "-noout", "-in", str(d / "cert.pem")],
        check=True, capture_output=True,
    )
    (d / "pub.pem").write_bytes(pub.stdout)
    _KEYDIR = d
    return d


def _tree(dst: Path, *, release: str | None = None,
          tool_append: str | None = None,
          contract_append: str | None = None,
          fixed: list[str] | None = None) -> Path:
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
    if tool_append is not None:
        with (dst / "loment" / "tools" / "lomcli.lomt").open("a", encoding="utf-8",
                                                            newline="\n") as f:
            f.write(tool_append)
    if contract_append is not None:
        with (dst / "lom" / "fuai.lom").open("a", encoding="utf-8", newline="\n") as f:
            f.write(contract_append)
    if fixed is not None:
        p = dst / lu.FIXED_CORE_LIST
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(fixed) + "\n", encoding="utf-8", newline="\n")
    return dst


def _candidate(dst: Path, tree: Path, release: str, *,
               sign: bool = True, payload: bytes = b"hotfix payload\n") -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    man = lu.manifest(tree, release)
    name = f"loment-hotfix-{release}.tar.gz"
    payload_path = dst / name
    payload_path.write_bytes(payload)
    man["artifact"] = {"name": name, "sha256": lu.sha256_file(payload_path)}
    (dst / "hotfix.json").write_text(
        json.dumps(man, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    sums = dst / "SHA256SUMS"
    sums.write_text(f"{man['artifact']['sha256']}  {name}\n", encoding="utf-8", newline="\n")
    if sign:
        kd = _keydir()
        shutil.copyfile(kd / "pub.pem", dst / "loment-signing.pub.pem")
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
             "-out", str(dst / "SHA256SUMS.asc"), str(sums)],
            check=True, capture_output=True,
        )
    return dst


def _reject(candidate: Path, tree: Path, **kw) -> str:
    try:
        lu.check(candidate, tree, **kw)
    except lu.Reject as e:
        return e.code
    raise AssertionError(f"应当拒绝，却接受了：{candidate}")


@test
def test_manifest_is_computed_from_the_tree() -> None:
    man = lu.manifest(ROOT, "v0.1.5-hot.1")
    lom = sorted(ROOT.glob("lom/*.lom"))
    assert set(man["contract"]) == {p.relative_to(ROOT).as_posix() for p in lom}
    assert man["contract"]["lom/fuai.lom"] == lu.sha256_file(ROOT / "lom" / "fuai.lom")
    assert man["language"] == lu.read_release(ROOT)
    assert man["baseline"] == "0.1.5"
    assert man["capabilities"] == []
    assert len(man["identity"]) == 64
    assert man["fixed_core"] == lu.fixed_core_digest(ROOT)
    print("      manifest 的每个字段都从树里算出来（契约 4 条 / 能力为空 / 身份 64 hex）")


@test
def test_implementation_only_candidate_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// one implementation fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        man = lu.check(cand, base)
        assert man["identity"] != lu.identity(base)
        print("      只改实现的候选被接受，且身份变了")


@test
def test_contract_change_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", contract_append="\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        assert _reject(cand, base) == "contract"
        print("      契约（lom/*.lom）动一个字节 —— 拒绝")


@test
def test_language_change_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", release="0.1.6")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        assert _reject(cand, base) == "language"
        print("      语言版本变了（树里 RELEASE 改）—— 拒绝")


@test
def test_baseline_change_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.6-hot.1")
        assert _reject(cand, base) == "baseline"
        print("      基线变了（tag 说 0.1.6，条子还说 0.1.5）—— 拒绝")


@test
def test_capability_change_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\ncapability fs : fs[0..1]\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        assert _reject(cand, base) == "capabilities"
        print("      能力清单多一条 —— 拒绝")


@test
def test_fixed_core_change_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        fixed = ["loment/tools/lomcli.lomt"]
        base = _tree(t / "base", fixed=fixed)
        cand_tree = _tree(t / "cand", fixed=fixed, tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        assert _reject(cand, base) == "fixed-core"
        print("      不可换的核动了 —— 拒绝")


@test
def test_signature_tamper_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        lu.check(cand, base)
        sums = cand / "SHA256SUMS"
        sums.write_text(sums.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        assert _reject(cand, base) == "signature"
        print("      SHA256SUMS 被改一个字节 —— 签名不过，拒绝")


@test
def test_stable_release_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5")
        assert _reject(cand, base) == "not-prerelease"
        print("      稳定版带着清单 —— 拒绝（只有 Pre-Release 是热更新）")


@test
def test_announcement_only_tag_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-pre")
        assert _reject(cand, base) == "announcement-only"
        print("      `-pre` 宣告版（今天不出包）—— 拒绝")


@test
def test_replay_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        state = t / "state.json"
        lu.check(cand, base, state=state)
        assert _reject(cand, base, state=state) == "replay"
        print("      同一个 Pre-Release 装第二次 —— 拒绝")


@test
def test_version_must_move_forward() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        assert _reject(cand, base, current="v0.1.5-hot.2") == "version"
        lu.check(cand, base, current="v0.1.5-hot.2", allow_rollback=True)
        print("      版本不前进 —— 拒绝；显式回滚 —— 放行")


@test
def test_cli_accepts_the_same_candidate() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = _tree(t / "base")
        cand_tree = _tree(t / "cand", tool_append="\n// fix\n")
        cand = _candidate(t / "cand-dir", cand_tree, "v0.1.5-hot.1")
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "loment_update.py"), "check",
             "--from", str(cand), "--at", str(base)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith("ACCEPT v0.1.5-hot.1"), r.stdout
        print("      命令行入口给出同一个答案")


def main() -> int:
    return loment_suite.run(TESTS, "loment_update_test")


if __name__ == "__main__":
    sys.exit(main())
