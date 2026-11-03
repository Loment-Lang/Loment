#!/usr/bin/env python3
# loment_hotline_test.py — 投影清单：把"装了什么、剔除了什么"说清楚（`#1157`）。
#
# 每一支都问：**清单能不能证明线上每一条都是 fix，被剔除的有没有名字，回滚有没有被
# 当成升级？** 夹具是一棵真 git 仓库 + 一份真签名的候选。

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
import loment_hotfix as hf  # noqa: E402
import loment_staging as st  # noqa: E402
import loment_update as lu  # noqa: E402
import loment_projection as pr  # noqa: E402

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

_GIT = ["git", "-c", "user.email=line@example.invalid", "-c", "user.name=line"]


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run([*_GIT, "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-300:])
    return r.stdout


def _repo(t: Path) -> Path:
    repo = t / "repo"
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = repo / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    _git(repo, "init", "--quiet")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "baseline")
    _git(repo, "tag", "B0")
    return repo


def _commit(repo: Path, msg: str, edit) -> str:
    edit(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


def _fix(repo: Path) -> None:
    with (repo / "tools" / "loment.py").open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n# hotfix here\n")


def _keyword(word: str):
    def edit(repo: Path) -> None:
        with (repo / "loment" / "tools" / "lomcli.lomt").open("a", encoding="utf-8",
                                                              newline="\n") as f:
            f.write(f'\nfn _p_{word}(s: ptr, n: u32) -> bool {{\n'
                    f'    return eq_cstr(s, n, "{word}");\n}}\n')
    return edit


def _project(repo: Path, t: Path, release="v0.1.5-hot.1") -> dict:
    with tempfile.TemporaryDirectory() as w:
        proj = pr.project(repo, "B0", "HEAD", Path(w) / "work", release=release)
    return proj.as_dict()


def _candidate(dst: Path, tree: Path, release: str, projection: dict) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    man = hf.build(tree, release, projection=projection)
    payload = dst / f"loment-hotfix-{release}.tar.gz"
    payload.write_bytes(b"hotfix payload\n")
    man["artifact"] = {"name": payload.name, "sha256": lu.sha256_file(payload)}
    (dst / "hotfix.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8", newline="\n")
    sums = dst / "SHA256SUMS"
    sums.write_text(f"{man['artifact']['sha256']}  {payload.name}\n",
                    encoding="utf-8", newline="\n")
    kd = Path(tempfile.mkdtemp(prefix="loment-line-key-"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(kd / "key.pem"), "-out", str(kd / "cert.pem"),
                    "-subj", "/CN=loment-line-test", "-days", "1"],
                   check=True, capture_output=True)
    pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout",
                          "-in", str(kd / "cert.pem")], check=True, capture_output=True)
    (dst / "loment-signing.pub.pem").write_bytes(pub.stdout)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
                    "-out", str(dst / "SHA256SUMS.asc"), str(sums)],
                   check=True, capture_output=True)
    return dst


def _home(t: Path) -> Path:
    import os
    h = t / "hot"
    h.mkdir(parents=True, exist_ok=True)
    os.environ["LOMENT_HOT_HOME"] = str(h)
    return h


@test
def test_the_manifest_names_applied_and_evicted() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        good = _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        bad = _commit(repo, "fix: adds a keyword\n\nHotfix: yes", _keyword("hotline"))
        proj = _project(repo, t)
        assert [s["sha"] for s in proj["line"]] == [good], proj
        assert [(s["sha"], s["reason"]) for s in proj["evicted"]] == [(bad, "digest")], proj
        assert all(s["kind"] == "fix" for s in proj["line"])
    print("      清单点名：线里 1 条 fix，剔除 1 条（digest）")


@test
def test_a_manifest_claiming_a_feature_is_refused() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        repo = _repo(t)
        _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        proj = _project(repo, t)
        proj["line"][0]["kind"] = "feature"
        cand = _candidate(t / "cand", repo, "v0.1.5-hot.1", proj)
        try:
            lu.check(cand, repo)
        except lu.Reject as e:
            assert e.code == "projection", e.code
        else:
            raise AssertionError("声称线上有一条 feature 的清单被接受了")
    print("      清单说线上有一条 feature —— 准入按 projection 拒绝")


@test
def test_status_lists_the_line_and_the_restart_required() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        repo = _repo(t)
        good = _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        bad = _commit(repo, "fix: adds a keyword\n\nHotfix: yes", _keyword("hotline"))
        proj = _project(repo, t)
        cand = _candidate(t / "cand", repo, "v0.1.5-hot.1", proj)
        staged = st.stage(cand, repo, "v0.1.5-hot.1")
        st.take(staged)
        text = st.status("0.1.5")
        assert good[:12] in text and bad[:12] in text, text
        assert "restart required" in text and "(digest)" in text, text
    print("      status：线上那一条 + 要重启的那一条（带理由）")


@test
def test_the_tree_hash_reproduces_the_artifact() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        repo = _repo(t)
        _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        one = _project(repo, t / "a")
        two = _project(repo, t / "b")
        assert one["tree"] and one["tree"] == two["tree"]
        man = hf.build(repo, "v0.1.5-hot.1", projection=one)
        assert man["projection"]["tree"] == one["tree"]
    print("      清单里的 tree 哈希重放得出来（两次投影同一棵树）")


@test
def test_an_earlier_point_is_a_rollback() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        repo = _repo(t)
        _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        proj2 = _project(repo, t / "p2", release="v0.1.5-hot.2")
        proj1 = _project(repo, t / "p1", release="v0.1.5-hot.1")
        st.stage(_candidate(t / "c2", repo, "v0.1.5-hot.2", proj2), repo, "v0.1.5-hot.2")
        st.stage(_candidate(t / "c1", repo, "v0.1.5-hot.1", proj1), repo,
                 "v0.1.5-hot.1", current="v0.1.5-hot.2", allow_rollback=True)
        for rel in ("v0.1.5-hot.2", "v0.1.5-hot.1"):
            got = st.poll("0.1.5")
            assert got is not None and got.name == rel, (rel, got)
            st.take(got)
        text = st.status("0.1.5")
        assert "(rollback)" in text, text
    print("      回到线上的前一个点：status 标成 rollback，不是 upgrade")


@test
def test_a_manifest_from_another_baseline_is_refused() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        _home(t)
        repo = _repo(t)
        _commit(repo, "fix: real fix\n\nHotfix: yes", _fix)
        proj = _project(repo, t)
        proj["baseline"] = "0.1.6"
        cand = _candidate(t / "cand", repo, "v0.1.5-hot.1", proj)
        try:
            lu.check(cand, repo)
        except lu.Reject as e:
            assert e.code == "projection-baseline", e.code
        else:
            raise AssertionError("另一条基线（0.1.6）的清单被接受了")
    print("      清单说自己是 0.1.6 的 —— 准入按 projection-baseline 拒绝")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotline_test")


if __name__ == "__main__":
    sys.exit(main())
