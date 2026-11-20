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

import re
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
def test_seed_fits_the_linkers_input_buffer():
    """种子必须装进 lomelf 的输入缓冲, 并且**留出名义余量** (`#777`)。

    这条上限就是**种子的上限**: `bootstrap.sh` 的 1/4 步把种子喂给 lomelf —— 提交进仓库的
    genesis 与包内镜像都是同一份源码建的。越过去之后 `read_all` 会收满即停、`run()` 里那条
    守卫会**响亮退出**（不再是静默截断), 但自举链就停在这里; `#441` 的悬崖就是这个形状。

    余量写成一个看得见的数 (`RESERVE`): 低于它就算失败, 逼下一笔改动先抬布局, 而不是让
    预算悄悄归零。上限与种子大小都**读**出来, 不另抄一份数（与 `IN_CAP` 那条纪律同源）。
    """
    import lomelf  # noqa: F401  (只为确保 tools/ 在 sys.path 上这条路径可导入)

    src = (ROOT / "loment" / "tools" / "lomelf.lomt").read_text(encoding="utf-8")
    m = re.search(r"const IN_CAP: u32 = (\d+);", src)
    assert m, "在 loment/tools/lomelf.lomt 里找不到 `const IN_CAP: u32 = <数>;`"
    cap = int(m.group(1))
    seed = loment_seed.SEED.stat().st_size
    reserve = 256 * 1024
    assert seed + reserve <= cap, (
        f"种子 {seed} B + 名义余量 {reserve} B > lomelf 输入上限 {cap} B"
        f"（实际余量 {cap - seed} B）。越限时 lomelf **响亮退出**, 自举链断在这里。"
        f"抬法是 `docs/192` / `docs/200` 那条式子: M_TXT/M_OUT/M_GB/M_TAB/M_OBJ/M_AR/ARENA"
        f" 与 IN_CAP 一起后移, 表区按同一比例抬（2026-10-19 那次: 4 MiB -> 6 MiB, `#777`）")
    print(f"      种子 {seed} B / 输入上限 {cap} B, 余量 {cap - seed} B (要求 >= {reserve})")


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
