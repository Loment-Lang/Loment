#!/usr/bin/env python3
"""判据：开发套件（`tools/loment_dev.py`, docs/302）真的在替人挡事，而不是一张说明书。

三件事各有一条：

1. **体检点名**：缺东西时必须说**缺的是哪一件**、**怎么补**，而不是一句"环境有问题"。
   补上之后必须转绿 —— 只测"红"的探针，修好了也看不出来。
2. **命令形状**：`gate` / `bootstrap` 拼出来的命令行就是文档里那条；Windows 上自举
   必须走 `wsl -e bash -lc`（`sh.exe` 跑不了那个脚本）。
3. **镜像对齐**：`.devcontainer` 必须是门禁**同一枚**镜像摘要。抬 tag 时漏改一处，
   贡献者的环境就与 CI 悄悄分叉 —— 这条钉住它（与 `gate.yml` 的
   `CI_IMAGE_DIGEST` / `ci-image.yml` 的 `TAG` 三方一致）。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import loment_dev  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _fake_root(tmp: Path) -> Path:
    for rel in loment_dev.REQUIRED_PATHS:
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return tmp


def _ok_which(names):
    return lambda n: (f"/usr/bin/{n}" if n in names else None)


@test
def test_doctor_is_green_on_a_complete_machine():
    names = {n for n, _ in loment_dev.OPTIONAL_TOOLS} | {"git"}
    with tempfile.TemporaryDirectory() as d:
        got = loment_dev.doctor_checks(
            which=_ok_which(names), env={"PYTHONUTF8": "1"},
            pyver=(3, 12, 3), platform="linux", root=_fake_root(Path(d)))
    bad = [c for c in got if c.required and not c.ok]
    assert not bad, "完整环境下不该有必检项是红的: " + "; ".join(c.name for c in bad)
    assert {"python", "git", "checkout"} <= {c.name for c in got}


@test
def test_doctor_names_the_missing_tool_and_the_fix():
    with tempfile.TemporaryDirectory() as d:
        got = loment_dev.doctor_checks(
            which=_ok_which({"git"}), env={}, pyver=(3, 12, 3),
            platform="linux", root=_fake_root(Path(d)))
    by = {c.name: c for c in got}
    assert by["git"].ok, "这条只缺 clang，git 该是绿的（否则测的就是『全都缺』）"
    assert not by["clang"].ok
    assert "clang" in by["clang"].fix, f"fix 里没点名要装什么: {by['clang'].fix!r}"
    assert "自举" in by["clang"].detail, "detail 要说清为什么需要它"


@test
def test_windows_without_pythonutf8_is_a_failure_and_setting_it_clears_it():
    names = {n for n, _ in loment_dev.OPTIONAL_TOOLS} | {"git"}
    with tempfile.TemporaryDirectory() as d:
        root = _fake_root(Path(d))
        red = loment_dev.doctor_checks(which=_ok_which(names), env={},
                                       pyver=(3, 12, 3), platform="win32", root=root)
        green = loment_dev.doctor_checks(which=_ok_which(names),
                                         env={"PYTHONUTF8": "1"},
                                         pyver=(3, 12, 3), platform="win32", root=root)
    red_utf8 = next(c for c in red if c.name == "PYTHONUTF8")
    assert not red_utf8.ok and "PYTHONUTF8=1" in red_utf8.fix
    assert next(c for c in green if c.name == "PYTHONUTF8").ok


@test
def test_gate_command_is_the_one_in_the_docs():
    assert loment_dev.gate_argv() == ["python3", "tools/ci.py", "--static-only"]
    got = loment_dev.gate_argv(jobs=4, only=("loment_c", "elf"))
    assert got == ["python3", "tools/ci.py", "--static-only", "-j", "4",
                   "--only-static", "loment_c", "--only-static", "elf"], got


@test
def test_bootstrap_goes_through_wsl_on_windows():
    posix = loment_dev.bootstrap_argv(platform="linux")
    assert posix == ["sh", "loment/bootstrap.sh"], posix
    win = loment_dev.bootstrap_argv(platform="win32", root=Path("D:/Dev/Loment-DEV"))
    assert win[:4] == ["wsl", "-e", "bash", "-lc"], win
    assert "loment/bootstrap.sh" in win[-1] and "cd d:/Dev/Loment-DEV" in win[-1], win[-1]
    shown = loment_dev._fmt(win)
    assert "'cd d:/Dev/Loment-DEV" in shown or '"cd d:/Dev/Loment-DEV' in shown, (
        f"打给人看的命令行把带空格的实参平铺了，照着抄就会错：{shown}")


@test
def test_the_devcontainer_uses_the_same_image_as_the_gate():
    gate = (ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8")
    digest = re.search(r'CI_IMAGE_DIGEST:\s*"([^"]+)"', gate).group(1)
    tag = re.search(r'CI_IMAGE_TAG:\s*"([^"]+)"', gate).group(1)
    used = set(re.findall(r"^\s*image:\s*(\S+)", gate, re.M))
    assert used, "gate.yml 里没找到 container 的 image 行 —— 这条判据已经空了"

    dc = json.loads((ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))
    assert dc["image"] in used, (
        f"devcontainer 用的镜像 {dc['image']} 不是门禁那枚（{sorted(used)}）")
    assert dc["image"].endswith(digest), (
        "devcontainer 的摘要与 CI_IMAGE_DIGEST 不一致 —— 抬 tag 时漏改了这一处")
    assert dc["containerEnv"].get("PYTHONUTF8") == "1"
    assert "loment_dev.py doctor" in dc["postCreateCommand"], (
        "进容器后要自述一遍环境，否则缺包要等到跑门禁才发现")

    ci = (ROOT / ".github" / "workflows" / "ci-image.yml").read_text(encoding="utf-8")
    assert re.search(r'^\s*TAG:\s*"' + re.escape(tag) + r'"', ci, re.M), (
        f"ci-image.yml 的 TAG 与 gate.yml 的 CI_IMAGE_TAG({tag}) 不一致")


@test
def test_the_contributor_guide_exists_and_is_linked():
    doc = ROOT / "docs" / "302-lomentwork.md"
    assert doc.exists(), "docs/302-lomentwork.md 不在树里"
    body = doc.read_text(encoding="utf-8")
    assert ".devcontainer" in body and "loment_dev.py" in body, "文档要点名两个入口"

    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert ".devcontainer" in contributing, "CONTRIBUTING 里没有指向开发套件的路"
    assert "tools/loment_dev.py" in contributing, "CONTRIBUTING 里没写那条命令"


def main() -> int:
    return loment_suite.run(TESTS, "loment_dev_test")


if __name__ == "__main__":
    sys.exit(main())
