#!/usr/bin/env python3
"""快速对照: 同一份源码, 参考实现与**自举编译器**各发一份 IR, 逐字节比。

为什么不用 `loment_p8_test`: 那条要先把镜像建出来 (种子 → clang → stage1 → 编 lomelf.lomt
→ clang 链), 一轮好几分钟。这里直接用**种子**经 `lomelf` 编成 ELF 跑起来 —— 那就是自举
实现本身, 只是少了"镜像"那层, 迭代时够用。

**门禁里不跑, 是定位工具不是检查项** (与 `loment_ir_diff.py` 同一分工): 改语言面时先
用它把"两边判定不同 / IR 不一致"钉到具体一行, 再决定要不要惊动 p8。

**它抓的是"一边报错一边放过"** —— 第一版只比 IR, 于是"参考报 E022、自举静静接受"被
报成"IR 逐字节一致"(两边发的 IR 确实一样), 恰好漏掉要抓的东西。所以先比判定, 再比 IR。

用法: python tools/loment_probe.py FILE.lomt
"""
import pathlib
import subprocess
import os
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import lomelf     # noqa: E402
import lomentc    # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = _HERE.parent


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"


def _wsl_path(p: pathlib.Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def main() -> int:
    src = pathlib.Path(sys.argv[1]).resolve()
    # --- 参考实现 ---
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    ref_err = [e for e in errs]
    ref_ir = lomentc.emit_llvm(mod, ROOT, deps)
    # --- 自举: 种子 -> ELF -> 跑 ---
    drv, _ = lomelf.compile_ll(SEED.read_text(encoding="utf-8"))
    # 写到系统临时目录: 探针是高频反复跑的, 落在仓里就会往 `git status` 里丢垃圾。
    elf = pathlib.Path(tempfile.gettempdir()) / "loment_probe_drv.elf"
    elf.write_bytes(drv)
    r = subprocess.run(
        [*_WSLCMD, "bash", "-lc",
         f"cp {_wsl_path(elf)} {_T}p8drv.bin && chmod +x {_T}p8drv.bin && "
         f"cd {_wsl_path(ROOT)} && {_T}p8drv.bin "
         f"{src.relative_to(ROOT).as_posix()}"],
        capture_output=True, timeout=600, shell=False)
    self_err = r.stderr.decode("utf-8", "replace").strip()
    self_ir = r.stdout.decode("utf-8", "replace")

    print(f"参考: {'报错 ' + str(ref_err[:2]) if ref_err else '过'}")
    print(f"自举: {'报错 ' + self_err[:200] if r.returncode != 0 else '过'}")
    # **"一边报错一边不报"是头号信号** —— 第一版探针只比 IR, 于是"参考报 E022、自举
    # 静静放过"被报成"IR 逐字节一致" (两边发的 IR 确实一样)。那正好漏掉了要抓的东西。
    if bool(ref_err) != (r.returncode != 0):
        print("**两边判定不同** —— 一边报错一边放过:")
        print("  参考:", ref_err[0] if ref_err else "(过)")
        print("  自举:", (self_err.splitlines()[0] if self_err else "(过)"))
        return 1
    if ref_err and r.returncode != 0:
        print("两边都报错 —— 逐字比诊断:")
        print("  参考:", ref_err[0])
        print("  自举:", self_err.splitlines()[0] if self_err else "(空)")
        return 0
    if ref_ir == self_ir:
        print(f"IR 逐字节一致 ({len(self_ir)}B)")
        return 0
    print(f"**IR 不一致**: 参考 {len(ref_ir)}B / 自举 {len(self_ir)}B")
    a, b = ref_ir.splitlines(), self_ir.splitlines()
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else "<无>"
        y = b[i] if i < len(b) else "<无>"
        if x != y:
            print(f"  第一处不同 @ 行 {i+1}:\n    参考: {x[:120]}\n    自举: {y[:120]}")
            break
    return 1


if __name__ == "__main__":
    sys.exit(main())
