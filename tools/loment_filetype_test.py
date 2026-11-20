#!/usr/bin/env python3
# loment_filetype_test.py — Windows 文件类型注册的门禁 (docs/157 §3.3)
#
# 判据 (前两条不碰注册表, 任何平台都能跑):
#   1. 注册**计划**覆盖两个扩展名的全部必要项 (默认值/OpenWithProgids/类型名/图标/打开命令);
#   2. 图标是多尺寸合法 .ico (含 16px 小尺寸 —— 资源管理器列表要用);
#   3. --status 只读 (跑完注册表状态不变);
#   4. 若本机**已经注册过**, 实际值必须与计划一致 (只读核对; 没注册就 SKIP)。
#
# 运行: python tools/loment_filetype_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_filetype as FT  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _dry_run() -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment_filetype.py"),
                        "--register", "--dry-run", "--json"],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout)


@test
def test_plan_covers_both_extensions():
    """计划里两个扩展名都要有: 默认值/两处 OpenWithProgids/类型名/图标/带 "%1" 的打开命令。"""
    if sys.platform != "win32":
        print("      SKIP: 非 Windows —— 登记的是 HKCU 注册表，别的平台没有对应物")
        return
    data = _dry_run()
    ops = data["ops"]
    for ext, (progid, friendly, mime) in FT.EXT_MAP.items():
        def has(key_suffix: str, name: str, value: str | None = None) -> bool:
            for op in ops:
                if not op["key"].endswith(key_suffix) or op["name"] != name:
                    continue
                return value is None or op["value"] == value
            return False
        assert has(f"Classes\\{ext}", "", progid), f"{ext}: 默认值没指向 {progid}"
        assert has(f"Classes\\{ext}\\OpenWithProgids", progid), f"{ext}: 缺 Classes 侧 OpenWithProgids"
        assert has(f"FileExts\\{ext}\\OpenWithProgids", progid), f"{ext}: 缺 FileExts 侧 OpenWithProgids"
        assert has(f"Classes\\{progid}", "FriendlyTypeName", friendly), f"{progid}: 缺类型名"
        assert has(f"Classes\\{progid}", "Content Type", mime), f"{progid}: 缺 Content Type"
        icon_ops = [op for op in ops if op["key"].endswith(f"{progid}\\DefaultIcon")]
        assert icon_ops and icon_ops[0]["value"].startswith('"'), f"{progid}: 缺图标"
        cmd = [op for op in ops if op["key"].endswith(f"{progid}\\shell\\open\\command")]
        assert cmd, f"{progid}: 缺打开命令"
        assert cmd[0]["value"].endswith('"%1"'), \
            f"{progid}: 打开命令必须以 \"%1\" 结尾 (否则打不开具体文件): {cmd[0]['value']}"
    # 编辑器: 工具找不到时用占位符 (只可能出现在 dry-run, 见 loment_filetype.main) ——
    # 本机没装编辑器不是这条判据的事 (干净检出/别的机器上很常见), 提示即可, 不算红。
    ed = data["editor"]
    if ed.startswith("<"):
        print(f"      SKIP 编辑器存在性: 本机没找到编辑器, 计划用占位符 {ed}; "
              f"真注册请加 --editor")
    else:
        assert Path(ed).is_file(), ed


@test
def test_icon_is_valid_multisize_ico():
    """图标: 合法 ICO 头 + 至少 5 个尺寸 + 含 16px (资源管理器小图标)。"""
    assert FT.ICON.is_file(), f"缺 {FT.ICON.relative_to(ROOT)} (python tools/loment_filetype.py --emit-icon)"
    raw = FT.ICON.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert reserved == 0 and kind == 1, f"不是 ICO (reserved={reserved} type={kind})"
    assert count >= 5, f"尺寸太少: {count}"
    sizes = []
    for i in range(count):
        off = 6 + i * 16
        w, h = raw[off], raw[off + 1]
        sizes.append((w or 256, h or 256))
    assert (16, 16) in sizes, f"缺 16x16 (列表/详细信息视图要靠它): {sizes}"
    assert (256, 256) in sizes, f"缺 256x256: {sizes}"
    for i in range(count):
        off = 6 + i * 16
        bytes_in_res, img_off = struct.unpack("<II", raw[off + 8:off + 16])
        assert img_off + bytes_in_res <= len(raw), f"第 {i} 个图像的偏移越界"


@test
def test_status_is_read_only():
    """--status 不许改系统: 跑前后注册表读取结果必须一致 (本机不是 Windows 时它只打印 SKIP)。"""
    before = _snapshot()
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment_filetype.py"), "--status"],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-300:]
    after = _snapshot()
    assert before == after, "--status 改了注册表 (它必须是只读的)"
    if sys.platform != "win32":
        assert "Windows" in r.stdout, "非 Windows 上应提示这是 Windows 专有"


def _snapshot() -> dict:
    """当前注册表状态的只读快照 (非 Windows 返回空)。"""
    if sys.platform != "win32":
        return {}
    import winreg
    out: dict = {}
    for ext, (progid, _f, _m) in FT.EXT_MAP.items():
        for key, name in ((f"Software\\Classes\\{ext}", ""),
                          (f"Software\\Classes\\{ext}\\OpenWithProgids", progid),
                          (f"Software\\Classes\\{progid}\\shell\\open\\command", "")):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                    out[f"{key}|{name}"] = str(winreg.QueryValueEx(k, name)[0])
            except OSError:
                out[f"{key}|{name}"] = None
    return out


@test
def test_registered_state_matches_plan():
    """如果本机注册过, 实际值必须与计划一致 (只读; 没注册过就 SKIP)。"""
    if sys.platform != "win32":
        print("      SKIP: 非 Windows")
        return
    snap = _snapshot()
    if not any(v is not None for v in snap.values()):
        print("      SKIP: 本机还没注册 (跑 python tools/loment_filetype.py --register)")
        return
    data = _dry_run()
    want = {f"{op['key']}|{op['name']}": op["value"] for op in data["ops"]
            if op["value"]}  # 空值 (OpenWithProgids 的标记) 不比内容, 只比存在性
    for k, v in want.items():
        if k in snap:
            assert snap[k] == v, f"{k}: 实际 {snap[k]!r} != 计划 {v!r} (重新跑 --register)"


def main() -> int:
    return loment_suite.run(TESTS, "loment_filetype_test")


if __name__ == "__main__":
    sys.exit(main())
