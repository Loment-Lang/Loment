#!/usr/bin/env python3
# loment_seed_test.py — 无 Python 自举的闸门 (docs/159)
#
# 判据三条 (前两条不需要 clang, 第三条要 clang + 能跑 Linux ELF 的环境):
#   1. 种子 == 参考实现为 selfhost/driver.lomt 发射的 IR (种子不许过期);
#   2. 启动脚本静态检查: 命令位置不许出现任何第三方语言解释器, 且 LF 换行;
#   3. `sh loment/bootstrap.sh` 全绿 —— 即"只有 clang"能复现编译器:
#      stage1(种子) 编译自己 == 种子, stage2/stage3 逐字节相同, 跨阶段对非自身入口一致。
#
# 运行: python tools/loment_seed_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_seed_matches_reference():
    """种子必须等于参考实现的产物 —— 自举产物一变, 种子就得重新固化。"""
    assert loment_seed.SEED.exists(), \
        f"缺 {loment_seed.SEED.relative_to(ROOT)} (python tools/loment_seed.py --emit)"
    rc = loment_seed.check()
    assert rc == 0, "种子过期 (见上面 [DIFF]; 运行 python tools/loment_seed.py --emit)"


@test
def test_seed_is_marked_generated():
    """种子在 `.gitattributes` 里必须被标成**生成物**, 否则它会重新淹掉评审面。

    这条钉的是 `docs/176` §4 A 的那一半收益: 种子 46 KB 宽、纯机器发射, 一次语言面改动
    能让它整体位移 —— 实测 `extern fn` 两次提交里它以 3858 行占 diff 的 **93%**,
    把真正要看的 153 行手写淹掉。标成生成物后 GitHub 折叠、`git diff` 不出内容。

    **能这么做的前提是上面那条判据**: 种子被机器保证"等于参考实现的产物", 所以
    正确用法是**重新生成**而不是读 diff。两条判据是一对 —— 少了上面那条, 这条
    就变成"把可能过期的东西藏起来"。
    """
    import subprocess
    # **必须用 POSIX 斜杠**: `git check-attr` 拿反斜杠路径匹配不上 `.gitattributes` 里的
    # 模式, 会回 "unspecified" —— 于是这条判据在 Windows 上假红 (第一版就是这么错的)。
    rel = loment_seed.SEED.relative_to(ROOT).as_posix()
    # **必须有 `--`**: 没有它 git 分不清属性名与路径, 会把第二个属性名当成**路径** ——
    # 于是回一行 `diff: linguist-generated: unspecified`, 这条判据假红。
    r = subprocess.run(["git", "check-attr", "linguist-generated", "diff", "--", rel],
                       cwd=str(ROOT), capture_output=True, text=True, shell=False)
    out = r.stdout
    assert "linguist-generated: true" in out, f"种子没标成生成物:\n{out}"
    assert "diff: unset" in out, f"种子仍会进 diff:\n{out}"


@test
def test_bootstrap_script_is_python_free():
    """启动脚本不许调用解释器 (clang/sh 以外), 且必须是 LF —— WSL 的 sh 认不了 CRLF。"""
    rc = loment_seed.script_ok()
    assert rc == 0, "启动脚本违反静态判据 (见上面 [FAIL])"


@test
def test_seed_bootstrap_fixed_point():
    """只有 clang: 种子 -> stage1 -> (自复现) -> stage2 -> stage3 定点。

    这条是"脱离 Python"的判据本体。没有 clang (或没有 sh/WSL) 时打印 SKIP 而不是失败 ——
    但在 CI 机器上它必须真的跑过 (与其它自举测试同一个约定)。
    """
    import shutil
    if not (shutil.which("wsl") or shutil.which("sh")):
        print("      SKIP: 无 sh/WSL")
        return
    rc = loment_seed.bootstrap()
    assert rc == 0, "无 Python 自举失败"


def main() -> int:
    return loment_suite.run(TESTS, "loment_seed_test")


if __name__ == "__main__":
    sys.exit(main())
