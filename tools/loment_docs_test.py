#!/usr/bin/env python3
"""loment_docs_test.py — `docs/NNN-slug.md` 编号的判据（`tools/loment_docs.py`）。

## 为什么要有这一条

`docs/NNN` 那个号**没有任何东西看着**，而"两条线同时取号"是**常态**：2026-10-11
实测 main 上有 **7 个号被两份以上文档共用**（212 / 222 / 227 / 228 / 229 / 231 / 232）。
撞号之后，按那个号写的 ~1700 处 `docs/NNN` 引用**指哪一份都不对**。

## 判据的形状：**基线只许变短**

main 上那 7 个是既成事实，这一条**不为它们变红**（否则接上就是 7 条红的噪音，
与 `docs/213` 记的那轮"测量轮"同一个道理）。它钉两件事：

1. **不许出现新的撞号** —— 撞号集合 ⊆ `GRANDFATHERED`，多一个就红；
2. **基线只许变短** —— `GRANDFATHERED` 里哪个号**已经修好**（不再撞）却还留在表里，也红。
   与 `.github/gate-baseline.txt` 同一条纪律：留着会让 summary 一直骗人。

修一份就从这个表里删一个号 —— **修的时候要把那份文档的引用一起改**（`--fix` 只动
无歧义的全路径引用；裸 `docs/NNN` 要人自己看，见工具文件头 §2）。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_docs as D  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: **基线：只许变短。** 2026-10-11 实测出来的 7 个撞号号段。
#: 修好一个就从这里删一个 —— 留着它，`test_the_baseline_only_shrinks` 会红。
GRANDFATHERED = {212, 222, 227, 228, 229, 231, 232}

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_no_new_collisions():
    """树里不许有**基线之外**的撞号。"""
    dups = set(D.duplicates(ROOT))
    new = sorted(dups - GRANDFATHERED)
    assert not new, (
        f"docs/ 下出现了 {len(new)} 个**新的**撞号：{new}\n"
        f"  " + "\n  ".join(f"{n}: " + "、".join(p.name for p in D.duplicates(ROOT)[n])
                            for n in new) + "\n"
        f"  改法：`python tools/loment_docs.py --fix`（相对 base 新增的那一份会挪到下一个空号）")
    print(f"      撞号 {len(dups)} 个，全在基线里（{sorted(dups)}）")


@test
def test_the_baseline_only_shrinks():
    """基线里每个号**必须仍然**是撞的 —— 修好了就删掉那一行，否则表会一直骗人。"""
    dups = set(D.duplicates(ROOT))
    stale = sorted(GRANDFATHERED - dups)
    assert not stale, (
        f"`GRANDFATHERED` 里这 {len(stale)} 个号已经不撞了：{stale}\n"
        f"  说明有人把它们修好了 —— **把它们从 GRANDFATHERED 里删掉**"
        f"（基线只许变短；留着会让'还有 7 个撞号'这句话一直是假的）")
    print(f"      基线 {len(GRANDFATHERED)} 个号都还在撞，没有过期的")


@test
def test_an_english_sibling_is_not_a_collision():
    """`docs/NNN-slug.en.md` 与它的源**同号同 slug**，那是体例，不是撞号。

    体例在 `docs/i18n/glossary.md` §1（"Keep the number and the slug unchanged"）。
    **这一条必须有**：少了它，会有两种坏结果 —— 要么 check 对着 141/142/… 那批英文孪生
    一直红，要么有人"修"它们、把英文版的号和 slug 改掉，于是体例和 ~1700 处引用一起断。
    """
    with tempfile.TemporaryDirectory() as t:
        docs = Path(t) / "docs"
        docs.mkdir()
        (docs / "900-same.md").write_text("# 900 · x\n", encoding="utf-8", newline="\n")
        (docs / "900-same.en.md").write_text("# 900 · x\n", encoding="utf-8", newline="\n")
        assert D.duplicates(Path(t)) == {}, "英文孪生被当成撞号了"

        (docs / "900-other.md").write_text("# 900 · y\n", encoding="utf-8", newline="\n")
        dups = D.duplicates(Path(t))
        assert set(dups) == {900}, f"同号两份**不同的**文档该算撞号，得到 {set(dups)}"
        assert len(dups[900]) == 3, "孪生也要列出来（让人看得见）"
    print("      英文孪生不算撞号；同号异 slug 才算")


@test
def test_next_free_is_max_plus_one():
    """空号 = `max(已用) + 1`（`CONTRIBUTING.md` 的 "next free"）——
    **不去填历史上的空洞**：填洞会让号段的历史读起来是错的。"""
    with tempfile.TemporaryDirectory() as t:
        docs = Path(t) / "docs"
        docs.mkdir()
        for n in (3, 5, 9):
            (docs / f"{n:03d}-x.md").write_text(f"# {n} · x\n", encoding="utf-8", newline="\n")
        nxt = D.next_free(set(D.scan(Path(t))))
        assert nxt == 10, f"该是 10（max+1，不填 4/6/7/8 的洞），得到 {nxt}"
    print("      空号 = max+1（不填洞）")


@test
def test_fix_renumbers_only_the_newly_added_doc():
    """`--fix` 的核心：**只改这一次新增的那一份**，base 上已有的号一动不动。

    真起一个临时 git 仓 —— base 有 `docs/001-a.md`，HEAD 又加了一份 `docs/001-b.md`
    （还带一处引用）。`--fix --base <base>` 之后：b 挪到 002、它的引用跟着改、
    a 一个字没动。
    """
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        def git(*a):
            r = subprocess.run(["git", "-C", str(root)] + list(a),
                               capture_output=True, text=True, errors="replace", shell=False)
            assert r.returncode == 0, f"git {a}: {r.stderr[:200]}"
            return r.stdout

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        docs = root / "docs"
        docs.mkdir()
        (docs / "001-a.md").write_text("# 1 · a\n\nsee docs/001-a.md\n", encoding="utf-8", newline="\n")
        git("add", "-A"); git("commit", "-q", "-m", "base")
        base = git("rev-parse", "HEAD").strip()

        (docs / "001-b.md").write_text("# 1 · b\n\nsee docs/001-b.md\n", encoding="utf-8", newline="\n")
        (root / "README.md").write_text("read docs/001-b.md first\n", encoding="utf-8", newline="\n")
        git("add", "-A"); git("commit", "-q", "-m", "add b")

        rc = D.main(["--fix", "--base", base, str(root)])
        assert rc == 0, f"--fix 该是 0，得到 {rc}"
        assert (docs / "001-a.md").is_file(), "base 上已有的那份**不该**被动"
        assert not (docs / "001-b.md").exists(), "撞号的那份该已经挪走"
        new = sorted(p.name for p in docs.iterdir())
        assert new == ["001-a.md", "002-b.md"], f"改完该是 001-a + 002-b，得到 {new}"
        body = (docs / "002-b.md").read_text(encoding="utf-8")
        assert body.startswith("# 2 · b"), f"H1 里的号该跟着改，得到 {body.splitlines()[0]!r}"
        assert "docs/002-b.md" in body, "自己正文里的全路径引用该跟着改"
        assert "docs/002-b.md" in (root / "README.md").read_text(encoding="utf-8"), \
            "别处的全路径引用也该跟着改"
        assert D.duplicates(root) == {}, "改完不该还有撞号"
    print("      只动新增那份：001-b -> 002-b，H1 与全路径引用一起走，001-a 未动")


@test
def test_fix_ignores_collisions_that_are_already_on_the_base():
    """**base 上本来就有的撞号，不该让这一次改号判成失败。**

    这是第一版**真的错过的**形状（2026-10-12 拿一次真推检验出来的）：base 上已经有一对撞号
    （212 那一类，实测 main 上有 7 个），我又新加一份撞同一号的文档 —— `--fix` 把**新那份**
    改号改对了，可它随后去查**整棵树**的重号，撞上 base 那对，于是自判失败退 1。
    机器人于是**每一次推都红**，而它明明已经把该改的改好了。

    钉住两件事：① 退 0；② base 上那一对**一个都没动**。
    """
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        def git(*a):
            r = subprocess.run(["git", "-C", str(root)] + list(a),
                               capture_output=True, text=True, errors="replace", shell=False)
            assert r.returncode == 0, f"git {a}: {r.stderr[:200]}"
            return r.stdout

        git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
        docs = root / "docs"; docs.mkdir()
        # base 自己就带着一对撞号 —— 既成事实，这一趟不归它管
        (docs / "001-a.md").write_text("# 1 · a\n", encoding="utf-8", newline="\n")
        (docs / "001-b.md").write_text("# 1 · b\n", encoding="utf-8", newline="\n")
        git("add", "-A"); git("commit", "-q", "-m", "base")
        base = git("rev-parse", "HEAD").strip()

        (docs / "001-c.md").write_text("# 1 · c\n", encoding="utf-8", newline="\n")
        git("add", "-A"); git("commit", "-q", "-m", "add c")

        rc = D.main(["--fix", "--base", base, str(root)])
        assert rc == 0, f"这一趟该退 0（base 那对不归它管），得到 {rc}"
        names = sorted(p.name for p in docs.iterdir())
        assert names == ["001-a.md", "001-b.md", "002-c.md"], f"只该动 c，得到 {names}"
        assert set(D.duplicates(root)) == {1}, "base 那对撞号该**原样留着**，不该被顺手改掉"
    print("      base 上既有的撞号不影响本次改号（退 0，且那对没被动）")


@test
def test_fix_refuses_when_the_base_is_unreachable():
    """**取不到 base 就不改号** —— 不确定时改号比不改更坏（改错是静默的）。"""
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        subprocess.run(["git", "-C", str(root), "init", "-q"], shell=False,
                       capture_output=True)
        (root / "docs").mkdir()
        (root / "docs" / "001-x.md").write_text("# 1 · x\n", encoding="utf-8", newline="\n")
        rc = D.main(["--fix", "--base", "no/such/ref", str(root)])
        assert rc == 2, f"取不到 base 该退 2（不是 0，也不是 1），得到 {rc}"
        assert (root / "docs" / "001-x.md").is_file(), "不该动任何文件"
    print("      取不到 base 时退 2 且不动文件")


def main() -> int:
    return loment_suite.run(TESTS, "loment_docs_test")


if __name__ == "__main__":
    sys.exit(main())
