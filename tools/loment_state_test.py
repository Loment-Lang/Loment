#!/usr/bin/env python3
# loment_state_test.py — 判据 `loment_state` 读的是 **origin**、不是工作树 (CLAUDE.md 第一节)
#
# 这条判据要钉住的是**那个唯一的属性**：工具报的权威状态来自 `origin/main`，不是眼前这棵树。
# 一个"读错地方"的工具在这件事上最典型的失效形状是 —— **它照样打印得整整齐齐**，只是数字
# 来自本地。所以判据不能只看"跑得通"：它得**在权威与本树故意不一致的仓里**，看工具报的是哪一份。
#
# 三组用例：
#   1. 纯函数（`parse_release` / `parse_counts`）—— 夹具字符串，含畸形输入（**不猜**，返回 None）；
#   2. **临时夹具仓**三种形态（领先且含权威 / 落后 / 根本没有 `origin/main`），
#      验 `state()` 的每个字段与 `--check` 的退出码；
#   3. 真仓上的两条不变量：`AUTHORITY` 是**远端跟踪引用**；正则读器与模块常量给出同一个版本号。
#
# **不联网**：夹具仓是本地的 `git init`，权威引用用 `update-ref` 直接摆；`state()` 一律
# `fetch=False`。真仓那两条只读已有的引用，缺 `origin/main` 时**可见 SKIP**而不是红。
#
# 运行: python tools/loment_state_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_state  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具仓

#: 提交身份走环境变量 —— 夹具仓是全新的，没有 user.name/user.email，而**不该**为了跑判据
#: 去碰用户的全局 git config（那是仓外状态，改了就是副作用）。
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "f@x",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "f@x"}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          shell=False, encoding="utf-8", errors="replace", env=_ENV)


def _commit(cwd: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        p = cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # 显式 LF: Windows 上 write_text 会把 \n 翻成 \r\n，而夹具仓的字节要可复现
        p.write_text(text, encoding="utf-8", newline="\n")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg)
    return _git(cwd, "rev-parse", "HEAD").stdout.strip()


def _release_src(version: str) -> str:
    return f'RELEASE = "{version}"\nRELEASE_NAME = "{version}"\n'


def _fixture(td: Path) -> Path:
    """一个新仓，第一次提交里 `tools/loment_release.py` 写着 `9.9.9`。"""
    _git(td, "init", "-q")
    _commit(td, {"tools/loment_release.py": _release_src("9.9.9")}, "c1")
    return td


# ---------------------------------------------------------------- 1. 纯函数

@test
def test_parse_release_takes_the_single_source():
    """正则读器要认得真文件那两行的形状，且**只**认 `RELEASE`（不认 `RELEASE_NAME`）。"""
    src = _release_src("1.2.3")
    assert loment_state.parse_release(src) == "1.2.3", "读不出 RELEASE"
    # 注释里出现的 RELEASE 不算（真文件顶上有一段讲版本沿革的注释）
    assert loment_state.parse_release("# RELEASE = \"0.0.1\"\nRELEASE = \"2.0.0\"\n") == "2.0.0", \
        "行首锚点没生效 —— 注释里的 RELEASE 被当成了真值"
    assert loment_state.parse_release("RELEASE_NAME = \"x\"\n") is None, "不该从 RELEASE_NAME 取值"
    assert loment_state.parse_release("") is None


@test
def test_parse_counts_refuses_to_guess():
    """`--left-right --count` 的输出只有"两个整数"一种合法形状；别的**返回 None**。

    这条钉的是纪律而不是功能：畸形输入上猜一个数，会让"落后 0 / 领先 0"这种最危险的值
    （看起来完全同步）伪装成正常输出。
    """
    assert loment_state.parse_counts("3\t7\n") == (3, 7), "标准形状（制表符）没认出来"
    assert loment_state.parse_counts("3 7") == (3, 7), "空格分隔也该认"
    assert loment_state.parse_counts("0\t0") == (0, 0)
    for bad in ("", "3", "3 7 9", "a b"):
        assert loment_state.parse_counts(bad) is None, f"畸形输入 {bad!r} 被猜成了一个数"


# ---------------------------------------------------------------- 2. 夹具仓三形态

@test
def test_reads_authority_not_the_working_tree():
    """**这条就是整个工具的理由**：权威与本树版本故意不同，看它报哪一份。

    夹具：`origin/main` 停在 `9.9.9` 那次提交，工作树往前走了一次、版本改成 `0.1.4`。
    工具必须报 **权威 9.9.9**。要是它去读了工作树（反过来报 0.1.4），这条红 —— 而那正是
    真实事故的形状（0.1.4 已发布、读着旧树的人还在说"等 0.1.4"）。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _fixture(td)
        _git(td, "update-ref", "refs/remotes/origin/main", "HEAD")
        _commit(td, {"tools/loment_release.py": _release_src("0.1.4")}, "c2")

        s = loment_state.state(root=td, fetch=False)
        assert s["authority_release"] == "9.9.9", f"权威版本应来自 origin/main，实得 {s['authority_release']}"
        assert s["local_release"] == "0.1.4", f"本树版本应来自工作树，实得 {s['local_release']}"
        assert s["on_authority"] is True, "HEAD 含 origin/main，应判为在权威基底之上"
        assert s["counts"] == (1, 0), f"应当是领先 1 / 落后 0，实得 {s['counts']}"
        assert loment_state.verdict(s)[0] == 0, f"在权威基底上应判 OK：{loment_state.verdict(s)[1]}"


@test
def test_behind_the_authority_is_judged_no():
    """落后形态：`origin/main` 走到 HEAD 前面去了，`--check` 必须**判否**（rc=1）。

    夹具造法：从 c1 另起一条提交 c2 并把 `origin/main` 指过去，再把工作树**退回到** c1 ——
    于是 HEAD 既不含 c2、也没被谁含，正是"本地树静默落后"那一刻的形状。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _fixture(td)
        c1 = _git(td, "rev-parse", "HEAD").stdout.strip()
        c2 = _commit(td, {"tools/loment_release.py": _release_src("9.9.10")}, "c2")
        _git(td, "update-ref", "refs/remotes/origin/main", c2)
        _git(td, "checkout", "-q", "--detach", c1)

        s = loment_state.state(root=td, fetch=False)
        assert s["on_authority"] is False, "HEAD 不含 origin/main，不该判为在权威基底之上"
        assert s["authority_release"] == "9.9.10", f"权威版本实得 {s['authority_release']}"
        rc, why = loment_state.verdict(s)
        assert rc == 1, f"落后应判否（rc=1），实得 {rc}: {why}"
        assert "落后" in why, f"判词该说清是落后：{why}"


@test
def test_no_authority_ref_is_visible_not_silent():
    """没有 `origin/main` 时判 **rc=2（无法判定）**，不是 0、也不是 1。

    这条钉的是"可见的空缺"：把"算不出来"当成"同步"（0）是最坏的那种假绿 —— 而仓里
    已经有过同形状的教训（门禁登记处那一格，`docs/201`）。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        _fixture(td)  # 故意不摆 refs/remotes/origin/main
        s = loment_state.state(root=td, fetch=False)
        assert s["have_authority"] is False, "夹具里不该有 origin/main"
        assert s["authority_release"] is None and s["on_authority"] is None, "没有权威时该是 None，不是猜的值"
        rc, why = loment_state.verdict(s)
        assert rc == 2, f"无法判定应是 2，实得 {rc}: {why}"
        assert "无法判定" in why, why


# ---------------------------------------------------------------- 3. 真仓上的不变量

@test
def test_authority_is_a_remote_tracking_ref():
    """`AUTHORITY` 必须是**远端跟踪引用**，不是本地分支。

    这是工具的整个立身之本：读本地分支等于换个地方犯同一个错。写成断言而不是注释，
    是因为"顺手把它做成参数"是一个看起来无害、实际上把唯一价值删掉的改动。
    """
    assert loment_state.AUTHORITY.startswith("origin/"), \
        f"AUTHORITY 该是远端跟踪引用，实得 {loment_state.AUTHORITY!r}"
    assert not loment_state.AUTHORITY.startswith("refs/heads/"), "不能是本地分支"


@test
def test_regex_reader_agrees_with_the_module_on_the_real_file():
    """在**真文件**上：正则读器与模块常量给出同一个版本号。

    两边对不上的意思是"读法漂了"——而这条读法是 `loment_state` 读 **origin 上那一份**时
    用的唯一手段，漂了就会对着任何远端文件报一个错的版本。两方独立（一边读源码文本、
    一边是 module 常量），对上才算数。
    """
    import loment_release
    src = (ROOT / loment_state.RELEASE_PATH).read_text(encoding="utf-8")
    got = loment_state.parse_release(src)
    assert got == loment_release.RELEASE, \
        f"正则读器 {got!r} != loment_release.RELEASE {loment_release.RELEASE!r}"


@test
def test_real_repo_state_is_coherent():
    """真仓上跑一次 `state()`（不 fetch），字段之间必须自洽；没有 `origin/main` 就可见 SKIP。"""
    if not loment_state.have_authority(ROOT):
        print("      SKIP: 这个检出没有 origin/main（tarball 检出？）")
        return
    s = loment_state.state(root=ROOT, fetch=False)
    assert s["have_authority"] is True
    assert s["on_authority"] is not None, "有权威时不该是 None"
    assert s["authority_head"] and s["authority_date"], "权威的短 sha / 日期不该空"
    assert s["counts"] is not None, "有权威时必须算出领先/落后"
    assert s["behind_commits"] == str(s["counts"][1]), \
        f"两条算法给出不同的落后数：{s['behind_commits']} vs {s['counts']}"
    print(f"      真仓: {s['branch']} @ {s['head']}，权威 {s['authority']} @ {s['authority_head']}"
          f"，落后 {s['counts'][1]} / 领先 {s['counts'][0]}")


# ---------------------------------------------------------------- 跑

def main() -> int:
    if not shutil.which("git"):
        print("loment_state_test: SKIP（没有 git）")
        return 0
    return loment_suite.run(TESTS, "loment_state_test")


if __name__ == "__main__":
    sys.exit(main())
