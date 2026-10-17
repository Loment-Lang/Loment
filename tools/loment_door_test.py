#!/usr/bin/env python3
# loment_door_test.py — `lom/` 那道禁区：**把 workflow 里那段脚本抠出来真跑**（`docs/239` §5）
#
# 为什么要有这条判据：那道门是 L0 契约**唯一的守卫**，而它的逻辑住在
# `.github/workflows/gate.yml` 的一段 shell 里 —— 改它等于改守卫，改错了没人看得见
# （门静默失效比门误报更坏）。所以这里不"读一眼 yaml 就算过"，而是**把那段脚本原样抠出来、
# 用假的 `gh` 喂四种事件形状、看退出码**。形状照 `loment_grammar_test` 那一类：
# 每条判据只钉一件事，钉不住就红。
#
# 被钉的四件事（外加两条已存在的规则，防它们被顺手改坏）：
#   1. **PR 上改 L0 ⇒ 拦**（这是门的本分）；
#   2. **走 PR 合进来的改动进了 main ⇒ 不判失败**（2026-10-13 修：门在 PR 上已经拦过、
#      人也做过决定（admin 合），main 那一跑再红就是误报 —— 而"门经常红"恰好教会所有人
#      忽略它）；
#   3. **直接 push 到 main 的非合并提交 ⇒ 照旧拦**（那条路绕过了 PR，没人做过决定）；
#   4. **取不到父提交数 ⇒ 按拦算**（fail closed：拿不准时不许静默放行）。
#
# 运行: python tools/loment_door_test.py   (无 bash / 无 WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


GATE = ROOT / ".github" / "workflows" / "gate.yml"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _door_script() -> str:
    """从 workflow 里抠出那一段 —— **不另抄一份**（抄一份两边就会漂）。

    **不用 `yaml`**：全仓就这一处会用到它，而 CI 镜像里**没有 pyyaml**（`grep -rl 'import yaml'
    tools/` 在写这条时只有本文件）—— 为一条判据往镜像里加依赖是拿构建链换方便。
    所以按**行扫描**取 `lom-door` 那个 job 里第一个 `run: |` 的块标量：块里的行缩进都比
    那个 key 深，遇到缩进不深的就是块尾。抠错的话下面六条判据会立刻红（而不是静默换一段脚本）。
    """
    lines = GATE.read_text(encoding="utf-8").splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.strip() == "lom-door:")
    while i < len(lines) and lines[i].strip() != "run: |":
        i += 1
    if i >= len(lines):
        raise AssertionError("gate.yml 里 `lom-door` job 没有 `run: |` 那一段")
    indent = len(lines[i]) - len(lines[i].lstrip())
    body: list[str] = []
    for ln in lines[i + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        body.append(ln[indent + 2:] if len(ln) > indent + 2 else "")
    return "\n".join(body) + "\n"


#: 假 `gh`：把每条 API 调用喂回固定答案。`FAKE_PARENTS` 控"父提交数"。
STUB = """#!/usr/bin/env bash
case "$*" in
  *"pulls/7/files"*) echo "$FAKE_FILES"; exit 0 ;;
  *"compare/"*)      echo "$FAKE_FILES"; exit 0 ;;
  *"commits/"*)      echo "$FAKE_PARENTS"; exit 0 ;;
esac
exit 0
"""


def _run(env_extra: dict, files: str) -> tuple[int, str, str]:
    """跑一遍那段脚本，返回 `(退出码, stdout, summary)`。"""
    td = Path(tempfile.mkdtemp(prefix="door-"))
    (td / "door.sh").write_text(_door_script(), encoding="utf-8", newline="\n")
    (td / "gh").write_text(STUB, encoding="utf-8", newline="\n")
    os.chmod(td / "gh", 0o755)          # POSIX 那条路要靠它（CI 上就是这条）
    (td / "sum.md").write_text("", encoding="utf-8")
    # **summary 写在 WSL 原生的 /tmp 里，不写在 /mnt/c 上。** 实测：走 9p 时 WSL 那边刚写完、
    # 这边立刻读会读到**空的** —— 同一格时对时错（`assert` 的失败信息里那份 summary 明明好好的）。
    # 那是跨边界的可见性延迟，不是门的行为；判据得绕开它，否则这条判据自己会 flaky。
    wsl_sum = f"/tmp/door-sum-{os.getpid()}.md"
    use_wsl = os.name == "nt"
    env = {"EVENT": "push", "PR": "", "REPO": "x/y", "BEFORE": "a" * 40, "AFTER": "b" * 40,
           "FAKE_FILES": files,
           "GITHUB_STEP_SUMMARY": wsl_sum if use_wsl else str(td / "sum.md")}
    env.update(env_extra)
    if os.name == "nt":
        if not use_wsl or not _wsl():
            raise RuntimeError("windows 上要 WSL 才能跑 bash")
        sets = " ".join(f"{k}={json.dumps(v)}" for k, v in env.items())
        inner = (f"chmod +x {_wsl_path(td / 'gh')} && rm -f {wsl_sum} && cd {_wsl_path(ROOT)} && "
                 f"env -i PATH={_wsl_path(td)}:/usr/local/sbin:/usr/local/bin:/usr/sbin:"
                 f"/usr/bin:/sbin:/bin {sets} bash {_wsl_path(td / 'door.sh')}; "
                 # 门自己的 stdout 会先出来，所以用标记把"退出码"与"summary"隔开
                 f'echo "<<<RC=$?"; echo "<<<SUM"; cat {wsl_sum} 2>/dev/null')
        r = subprocess.run([*_WSLCMD, "bash", "-lc", inner],
                           capture_output=True, text=True, timeout=120, shell=False)
        raw = r.stdout or ""
        rc = -1
        for ln in raw.splitlines():
            if ln.startswith("<<<RC="):
                rc = int(ln[6:].strip() or -1)
        summ = raw.partition("<<<SUM\n")[2]
        shutil.rmtree(td, ignore_errors=True)
        return rc, raw, summ
    e = dict(os.environ)
    e.update(env)
    e["PATH"] = f"{td}:{e['PATH']}"
    r = subprocess.run(["bash", str(td / "door.sh")], capture_output=True, text=True,
                       env=e, timeout=120, shell=False)
    summ = (td / "sum.md").read_text(encoding="utf-8")
    shutil.rmtree(td, ignore_errors=True)
    return r.returncode, (r.stdout or "") + (r.stderr or ""), summ


def _ready() -> bool:
    if os.name == "nt" and not _wsl():
        print("      SKIP: windows 上要 WSL 才能跑 bash")
        return False
    return True


# ---------------------------------------------------------------- 判据

@test
def test_a_pull_request_that_modifies_l0_is_blocked():
    """**门的本分**：PR 上改了既有的 L0 契约 ⇒ 拦（退出码 1 + 名单里那一行）。"""
    if not _ready():
        return
    rc, out, summ = _run({"EVENT": "pull_request", "PR": "7"}, "modified|lom/fuai.lom")
    assert rc == 1, (rc, out[-300:])
    assert "lom/fuai.lom" in summ and "L0 接口层" in summ, summ[:200]


@test
def test_a_merged_change_does_not_red_main():
    """**2026-10-13 修的那一格**：走 PR 合进来的改动，在 main 那一跑**不判失败**。

    这个提交（`AFTER`）是**合并提交**（父提交 2 个）⇒ 门在 PR 上已经拦过、人也做过决定
    （admin 合就是那个决定的形状）。再红就是误报，而"门经常红"会教会所有人忽略它。
    仍然**记进 summary**（不许静默）。
    """
    if not _ready():
        return
    rc, out, summ = _run({"FAKE_PARENTS": "2"}, "modified|lom/fuai.lom")
    assert rc == 0, (rc, out[-300:])
    assert "不判失败" in summ and "lom/fuai.lom" in summ, summ[:200]


@test
def test_a_direct_push_is_still_blocked():
    """**直接 push 到 main 的非合并提交照旧拦** —— 那条路绕过了 PR，没人做过决定。"""
    if not _ready():
        return
    rc, out, summ = _run({"FAKE_PARENTS": "1"}, "modified|lom/fuai.lom")
    assert rc == 1, (rc, out[-300:])
    assert "不判失败" not in summ, summ[:200]


@test
def test_unreadable_parents_fail_closed():
    """拿不准时**不许静默放行**：父提交数取不到（API 抽风 / 形状变了）就按 1 算 ⇒ 拦。"""
    if not _ready():
        return
    for junk in ("x", "", "0"):
        rc, out, _summ = _run({"FAKE_PARENTS": junk}, "modified|lom/fuai.lom")
        assert rc == 1, (junk, rc, out[-200:])


@test
def test_an_added_l0_file_is_not_blocked():
    """既有的那条规则**没被顺手改坏**：**新增**一份 L0 文件不在这道门下面。"""
    if not _ready():
        return
    rc, out, summ = _run({"EVENT": "pull_request", "PR": "7"}, "added|lom/net2.lom")
    assert rc == 0, (rc, out[-300:])
    assert "新增" in summ, summ[:200]


@test
def test_a_change_outside_l0_passes():
    """不碰 `lom/` 的改动，门一律放行（别把门做成"什么改动都拦"）。"""
    if not _ready():
        return
    rc, out, summ = _run({"EVENT": "pull_request", "PR": "7"}, "modified|tools/ci.py")
    assert rc == 0, (rc, out[-300:])
    # 放行那一支的话写在 **stdout**（summary 只在"有新增 lom/ 文件"时才写）
    assert "无**改动**" in out, out[-300:]


def main() -> int:
    return loment_suite.run(TESTS, "loment_door_test")


if __name__ == "__main__":
    raise SystemExit(main())
