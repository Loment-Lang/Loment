#!/usr/bin/env python3
# loment_src.py — Loment **源码包** (docs/164)
#
# 与 loment_dist.py 的区别: dist 发的是**预编译工具链** (ELF + setup.exe), 这里发的是
# **源码 + 编辑器工具**: 拿到就能读、能 build、能改, 不需要装任何 Loment 二进制。
#
# 内容只从 **git 索引**取 (git ls-files 选中的路径 + git cat-file 读 blob):
#   * 自动排除构建产物 (未跟踪的 loment/build/*.ll 之类), 且不受工作区改动影响;
#   * 字节与提交一致 (索引侧恒为 LF), 所以这个包是**提交的纯函数**。
#
#   python tools/loment_src.py --emit            # 打 loment/dist/loment-<ver>-src.zip
#   python tools/loment_src.py --list            # 只列会打进去的路径
#   python tools/loment_src.py --check           # 打进临时目录并验: 两次构建字节相同 + 内部 SHA256SUMS 自洽
#
# 退出码: 0 = 成功 / 1 = 失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_release  # noqa: E402  (版本名单一真源)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "dist"
VER = loment_release.RELEASE
DISPLAY = loment_release.RELEASE_NAME
VSIX = ROOT / "loment" / "build" / "loment-vscode.vsix"
SEED = "loment/build/selfhost_driver.ll"

#: 收哪些路径 —— 只列**已跟踪**的 (git ls-files 会给准确清单)。
#: 文档按**名字**选而不是按数字区间: 区间会把别的线的东西收进来 (docs/144-kernel-boundaries、
#: docs/145-parallel-development、docs/146-kernel-stack-arena 就是这样被误收的)。
GLOBS = [
    "README.md", "LICENSE-MIT", "LICENSE-APACHE",
    "loment/**",                       # 语言本体: 源/种子/链接脚本/语料/bootstrap
    "editors/**",                      # VS Code 扩展源码 + vim + 图标
    "docs/*loment*",                   # Loment 的规范/冻结面/手册/发行文档
    "docs/141-l0-lom-spec.md", "docs/142-potato-v0.md", "docs/147-potato-v1-spec.md",
    "docs/manual/**",
    "scripts/lomc.ps1", "scripts/install-lsp.ps1",
    "tools/lom*.py", "tools/potato*.py", "tools/mono_trace.py", "tools/vscode_ext.py",
    "tools/loment*.py",
]
#: 明确不收 (别人的线 / 与本语言无关)
EXCLUDE = ["docs/153-*"]               # 153 是 UIsport 线, 不属于语言包

README_SRC = """# Loment {DISPLAY} — 源码包

`{VERSION}`。这是 **Loment 语言本体的源码 + 编辑器工具**，不含任何预编译二进制：
解包后就能读、能构建、能改。要"装上就能跑"的预编译工具链，请用发行包（`loment-{VERSION}-linux-x64.tar.gz`
或 Windows 的 `setup.exe`）。

## 里面有什么

| 路径 | 内容 |
| --- | --- |
| `loment/selfhost/` | 自举链源码：lexer / parser / checker / codegen / driver（全部 `.lomt`） |
| `loment/tools/` | Loment 写的工具：格式化器 `lomfmt`、文档生成 `lomdoc`、语言服务 `lsp` |
| `loment/lib/json.lomt` | JSON 库（LSP 的 JSON-RPC 靠它） |
| `loment/examples/` | 示例程序（含 `user_hello.lomt`） |
| `loment/build/selfhost_driver.ll` | **自举种子**：参考实现发射的驱动 IR，只用 clang 就能从它重建整套工具链 |
| `loment/bootstrap.sh` | 无 Python 引导：种子自复现 + 三阶段定点（`sh loment/bootstrap.sh`） |
| `loment/corpus.json` | 语料清单（逐文件 sha256） |
| `tools/loment*.py` `tools/lom*.py` `tools/potato*.py` | 参考实现与工具链（Python）；自举判据就是拿它做逐字节对照 |
| `editors/vscode/` | VS Code 扩展源码（语法高亮 + LSP 客户端 + 任务） |
| `loment-vscode.vsix` | **已打好的扩展**，直接装（见下） |
| `docs/14*`–`docs/16*` `docs/manual/` | 规范、冻结面、语言手册、发行与签名说明 |

## 最快上手：不需要 Python，只要 clang

```sh
sh loment/bootstrap.sh              # 从种子重建编译器, 并证明: 种子自复现 + 三阶段定点
sh loment/bootstrap.sh FILE.lomt    # 用重建出来的编译器把某个 .lomt 编成 IR (打到 stdout)
```

Windows 上同一件事：`powershell -File scripts/lomc.ps1 FILE.lomt`（走 WSL + Windows 侧 clang）。
种子与整套工具链的细节见 `docs/159`、`docs/162`。

## 装 VS Code 扩展

```sh
code --install-extension loment-vscode.vsix        # 直接用打好的包
```

从源码重建（需要 Node 18+，`npm install` 要联网）：

```sh
cd editors/vscode && npm install --omit=dev
python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
code --install-extension loment/build/loment-vscode.vsix --force
```

装完打开任意 `.lomt`：有高亮、有补全/跳转/诊断；命令面板里有"生成 IR / 静态检查 / 格式化 / 重启语言服务"。
Vim 用户看 `editors/vim/`。

## 用参考实现（Python，可选）

自举链不需要 Python，但**对照**要用它（自举的判据就是"与参考实现逐字节相同"）：

```sh
python tools/lomentc.py loment/examples/user_hello.lomt \\
       --emit-llvm /tmp/out.ll --emit-potato /tmp/out.json
python tools/loment_p8_test.py            # 两个后端逐字节等价 + 自举定点
python tools/loment_dist_test.py          # 发行包
```

## 读语言规范

- `docs/158-loment-freeze.md` —— **冻结面**（改什么要付什么代价）+ 已知开放项；
- `docs/141-l0-lom-spec.md` / `docs/143-l1-loment-v0.md` —— L0/L1 规范；
- `docs/144-loment-native-backend.md` —— 原生后端（LLVM IR）；
- `docs/manual/` —— 自动生成的 API 手册（带编译器版本戳）；
- `docs/162` / `docs/163` —— 发行包与签名（怎么验证你下载的东西）。

## 校验

```sh
sha256sum -c SHA256SUMS
```
"""


def _tracked() -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z", *GLOBS], cwd=str(ROOT), shell=False,
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit("git ls-files 失败 (源码包只从 git 索引取内容)")
    names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
    for pat in EXCLUDE:
        import fnmatch
        names = [n for n in names if not fnmatch.fnmatch(n, pat)]
    return sorted(names)


def _blobs(names: list[str]) -> dict[str, bytes]:
    """一次 git cat-file --batch 读全部 blob (比逐个调用快得多; 索引侧恒为 LF)。"""
    spec = "\n".join(f":{n}" for n in names)
    r = subprocess.run(["git", "cat-file", "--batch"], cwd=str(ROOT), shell=False,
                       input=spec.encode(), capture_output=True)
    out = r.stdout
    blobs: dict[str, bytes] = {}
    pos = 0
    for i in range(len(names)):
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode("utf-8", "replace").split()
        size = int(header[2])
        start = nl + 1
        blobs[names[i]] = out[start:start + size]
        pos = start + size + 1        # 跳过 blob 后面的换行
    return blobs


def build(src_dir: Path | None) -> tuple[bytes, dict[str, bytes]]:
    names = _tracked()
    blobs = _blobs(names)
    files: dict[str, bytes] = dict(blobs)
    files["README-SRC.md"] = README_SRC.replace("{DISPLAY}", DISPLAY).replace("{VERSION}", VER).encode()
    vsix = src_dir / "loment-vscode.vsix" if src_dir else VSIX
    if vsix.exists():
        files["loment-vscode.vsix"] = vsix.read_bytes()
    # 随包校验和 (不含自己)
    files["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(files[k]).hexdigest()}  {k}\n" for k in sorted(files)).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            zi = zipfile.ZipInfo(f"loment-{VER}-src/{rel}", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = (0o755 if rel.endswith((".sh", ".ps1")) else 0o644) << 16
            zf.writestr(zi, files[rel])
    return buf.getvalue(), files


def emit() -> int:
    blob, files = build(None)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"loment-{VER}-src.zip"
    out.write_bytes(blob)
    has_seed = SEED in files
    print(f"  [{out.name}] {len(blob)} 字节, {len(files)} 个文件 "
          f"(种子 {'有' if has_seed else '缺!'}, vsix "
          f"{'有' if 'loment-vscode.vsix' in files else '无'})")
    return 0 if has_seed else 1


def check() -> int:
    """两次构建字节相同 (确定性) + 包内 SHA256SUMS 自洽 + 关键内容齐。"""
    bad = 0
    b1, files = build(None)
    b2, _ = build(None)
    if b1 != b2:
        print("[DIFF] 两次构建字节不同")
        bad += 1
    else:
        print("[OK] 两次构建字节相同 (确定性)")
    sums = files.get("SHA256SUMS", b"").decode()
    for line in sums.splitlines():
        want, rel = line.split("  ", 1)
        got = hashlib.sha256(files[rel]).hexdigest()
        if got != want:
            print(f"[DIFF] 包内校验和: {rel}")
            bad += 1
    print(f"[OK] 包内 SHA256SUMS 自洽 ({len(sums.splitlines())} 条)" if not bad else "")
    must = ["loment/bootstrap.sh", SEED, "loment/selfhost/driver.lomt",
            "loment/selfhost/codegen.lomt", "loment/tools/lsp.lomt",
            "editors/vscode/package.json", "README-SRC.md",
            "LICENSE-MIT", "LICENSE-APACHE"]
    missing = [m for m in must if m not in files]
    if missing:
        print(f"[DIFF] 缺关键文件: {missing}")
        bad += 1
    else:
        print(f"[OK] 关键文件齐 ({len(must)} 项)")
    print(f"loment_src: {'全绿' if not bad else str(bad) + ' 处红'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_src")
    ap.add_argument("--emit", action="store_true", help="打源码包")
    ap.add_argument("--list", action="store_true", help="只列会打进去的路径")
    ap.add_argument("--check", action="store_true", help="确定性 + 包内校验和 + 关键文件")
    a = ap.parse_args(argv)
    if a.list:
        for n in _tracked():
            print(f"  {n}")
        print(f"  （外加生成的 README-SRC.md / SHA256SUMS / loment-vscode.vsix）")
        return 0
    if a.emit:
        return emit()
    # 无参数 = 门禁模式 (与仓库其它工具同一约定: ci.py 按 main() 调用)
    return check()


if __name__ == "__main__":
    sys.exit(main())
