#!/usr/bin/env python3
# loment_hotfix_test.py — 发布侧的 `hotfix.json`（`#1153`）。
#
# 每一支都在问同一个问题：**清单是"算出来的"还是"抄下来的"？** 夹具是一棵从本仓拷出来
# 的树，改一处源码，看对应的字段动不动；签名那几条用 openssl 真签真验。

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_suite  # noqa: E402
import loment_update as lu  # noqa: E402
import loment_hotfix as hf  # noqa: E402

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


def _tree(dst: Path, *, append: tuple[str, str] | None = None) -> Path:
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = dst / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    if append is not None:
        rel, text = append
        with (dst / rel).open("a", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return dst


def _build(**kw) -> dict:
    with tempfile.TemporaryDirectory() as t:
        return hf.build(_tree(Path(t) / "tree", **kw), "v0.1.5-hot.1")


def _candidate(dst: Path, tree: Path, release: str) -> Path:
    """候选目录：清单 + 载荷 + SHA256SUMS + 分离签名（用一次性的 RSA 密钥真签）。"""
    import subprocess

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
    kd = Path(tempfile.mkdtemp(prefix="loment-hotfix-key-"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(kd / "key.pem"), "-out", str(kd / "cert.pem"),
                    "-subj", "/CN=loment-hotfix-test", "-days", "1"],
                   check=True, capture_output=True)
    pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout",
                          "-in", str(kd / "cert.pem")], check=True, capture_output=True)
    (dst / "loment-signing.pub.pem").write_bytes(pub.stdout)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
                    "-out", str(dst / "SHA256SUMS.asc"), str(sums)],
                   check=True, capture_output=True)
    return dst


@test
def test_every_field_is_computed_from_the_tree() -> None:
    man = _build()
    want = {"schema", "release", "prerelease", "baseline", "language", "contract",
            "capabilities", "surface", "cli", "fixed_core", "functions",
            "projection", "artifact"}
    assert set(man) == want, sorted(set(man) ^ want)
    assert man["schema"] == hf.SCHEMA
    assert man["prerelease"] is True
    assert man["baseline"] == "0.1.5"
    assert man["language"] == hf.read_release(ROOT)
    assert man["contract"] and all(len(v) == 64 for v in man["contract"].values())
    assert set(man["surface"]) == {"keywords", "syntax", "diagnostics"}
    assert len(man["cli"]) == 64 and len(man["fixed_core"]) == 64
    assert man["functions"] == [] and man["artifact"] == {}
    print(f"      13 个字段全从树里算出来（契约 {len(man['contract'])} 条，固定核 64 hex）")


@test
def test_the_contract_digest_follows_lom_fuai() -> None:
    before = _build()
    after = _build(append=("lom/fuai.lom", "\n// one contract byte\n"))
    assert before["contract"]["lom/fuai.lom"] != after["contract"]["lom/fuai.lom"]
    assert before["fixed_core"] == after["fixed_core"]
    print("      改一行契约 → contract 里那一条变，固定核不动")


@test
def test_the_cli_digest_follows_the_command_table() -> None:
    before = _build()
    after = _build(append=("loment/tools/lomcli.lomt",
                           '\nfn _probe_row(mem: ptr, fd: u64) -> u32 {\n'
                           '    return row(mem, fd, "frobnicate", "", "a new command");\n}\n'))
    assert before["cli"] != after["cli"], "加了一条命令，CLI 摘要没动"
    print("      加一个 CLI 命令 → cli 摘要变")


@test
def test_the_surface_digest_follows_a_new_keyword() -> None:
    before = _build()
    after = _build(append=("loment/tools/lomcli.lomt",
                           '\nfn _probe_kw(s: ptr, n: u32) -> bool {\n'
                           '    return eq_cstr(s, n, "hotnotakw");\n}\n'))
    assert before["surface"]["keywords"] != after["surface"]["keywords"], \
        "加了一个词，接口面摘要没动"
    assert before["surface"]["syntax"] == after["surface"]["syntax"]
    print("      加一个关键词 → surface.keywords 变，语法形态不动")


@test
def test_a_signed_prerelease_manifest_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        tree = _tree(t / "tree")
        cand = _candidate(t / "cand", tree, "v0.1.5-hot.1")
        man = lu.check(cand, tree)
        assert man["release"] == "v0.1.5-hot.1"
        payload = json.loads((cand / "hotfix.json").read_text(encoding="utf-8"))
        assert payload["surface"] == hf.surface(tree)
    print("      真签真验：清单 + SHA256SUMS + .asc 一起过准入")


@test
def test_a_stable_release_manifest_is_refused() -> None:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        tree = _tree(t / "tree")
        man = hf.build(tree, "v0.1.5")
        assert man["prerelease"] is False
        dst = t / "cand"
        dst.mkdir()
        (dst / "hotfix.json").write_text(json.dumps(man) + "\n", encoding="utf-8",
                                         newline="\n")
        try:
            lu.check(dst, tree)
        except lu.Reject as e:
            assert e.code == "not-prerelease", e.code
        else:
            raise AssertionError("稳定版清单被当成了热更新")
    print("      稳定版也带清单，但准入按 not-prerelease 拒绝")


@test
def test_the_release_workflow_builds_it_before_signing() -> None:
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    dist = (ROOT / "tools" / "loment_dist.py").read_text(encoding="utf-8")
    assert "LOMENT_RELEASE_TAG: ${{ steps.ver.outputs.tag }}" in wf, \
        "workflow 没把真 tag 传给构建"
    i_hf = dist.index("loment_hotfix.write")
    i_sums = dist.index("sums = write_sums(out)")
    assert i_hf < i_sums, "清单必须在算 SHA256SUMS **之前**落盘，否则签名锚不覆盖它"
    assert "SHA256SUMS.asc" in wf and "loment/dist/*" in wf
    # 热更新件（`#1155`）：只在给了 `hot_base` 时做，且落在 dist **之外**。
    assert "loment_projection.py" in wf and "loment_hotart.py" in wf,         "workflow 没有热更新投影/打包那一步"
    assert "hot_base" in wf and "loment/build/hot" in wf,         "热更新件应该由 hot_base 触发、落在 loment/build/hot（不碰发行资产）"
    print("      workflow 传 tag；清单先落盘、后算校验和；签名覆盖整个 dist;"
          " 热更新件由 hot_base 触发、不碰发行资产")


def main() -> int:
    return loment_suite.run(TESTS, "loment_hotfix_test")


if __name__ == "__main__":
    sys.exit(main())
