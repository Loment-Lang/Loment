#!/usr/bin/env python3
"""loment_cache_test.py — IR 缓存的行为判据（docs/324）。

四条，各钉一个会**静默错编**的方向：

  1. 同一份输入第二次命中，且**字节相同**（缓存返回的就是生成物）；
  2. 改依赖的一个字节 ⇒ 键变、重算（不能拿旧 IR）；
  3. `LOMENT_CACHE=off` ⇒ 不读不写；
  4. 缓存文件被截断/写坏 ⇒ 退回重算，不把半截 IR 当结果。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_DEP = "module dep\n\npub fn twice(x: u32) -> u32 {\n    return x + x;\n}\n"
_ROOT = ("module main\n\nuse \"dep.lomt\"\n\nfn main() -> u32 {\n"
         "    return twice(21);\n}\n")


def _emit(d: Path):
    m, deps = lomentc.load_unit(d / "main.lomt", d)
    return lomentc.emit_llvm(m, d, deps)


@test
def test_hit_returns_identical_bytes():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cd:
        os.environ["LOMENT_CACHE_DIR"] = cd
        d = Path(td)
        (d / "dep.lomt").write_text(_DEP, encoding="utf-8")
        (d / "main.lomt").write_text(_ROOT, encoding="utf-8")
        a = _emit(d)
        import loment_cache
        n1 = loment_cache.stats()["entries"]
        b = _emit(d)
        n2 = loment_cache.stats()["entries"]
        assert a == b, "命中与重算的字节不同"
        assert n1 == 1 and n2 == 1, (n1, n2)
        print("      命中：第二次字节相同，条目数不涨")


@test
def test_dep_change_invalidates():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cd:
        os.environ["LOMENT_CACHE_DIR"] = cd
        d = Path(td)
        dep = d / "dep.lomt"
        dep.write_text(_DEP, encoding="utf-8")
        (d / "main.lomt").write_text(_ROOT, encoding="utf-8")
        a = _emit(d)
        dep.write_text(_DEP.replace("x + x", "x + x + 1"), encoding="utf-8")
        b = _emit(d)
        assert a != b, "依赖改了，产物却没变"
        import loment_cache
        assert loment_cache.stats()["entries"] == 2, loment_cache.stats()
        print("      依赖改一个字节 ⇒ 新键、新产物")


@test
def test_off_switch_writes_nothing():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cd:
        os.environ["LOMENT_CACHE_DIR"] = cd
        os.environ["LOMENT_CACHE"] = "off"
        try:
            d = Path(td)
            (d / "dep.lomt").write_text(_DEP, encoding="utf-8")
            (d / "main.lomt").write_text(_ROOT, encoding="utf-8")
            _emit(d)
            import loment_cache
            assert loment_cache.stats()["entries"] == 0, loment_cache.stats()
        finally:
            os.environ.pop("LOMENT_CACHE", None)
        print("      LOMENT_CACHE=off：不写")


@test
def test_corrupt_entry_falls_back():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cd:
        os.environ["LOMENT_CACHE_DIR"] = cd
        d = Path(td)
        (d / "dep.lomt").write_text(_DEP, encoding="utf-8")
        (d / "main.lomt").write_text(_ROOT, encoding="utf-8")
        good = _emit(d)
        import loment_cache
        # 把那条缓存文件清空：下一次必须重算，不能返回空串
        files = list((Path(cd) / "ir").rglob("*.ll"))
        assert len(files) == 1, files
        files[0].write_text("", encoding="utf-8")
        bad = _emit(d)
        assert bad == good and bad != "", "坏缓存没有被重算覆盖"
        print("      坏缓存：重算后与正确产物一致")


def main() -> int:
    return loment_suite.run(TESTS, "loment_cache_test")


if __name__ == "__main__":
    sys.exit(main())
