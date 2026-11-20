#!/usr/bin/env python3
# loment_audit.py — 审计包 (M100, docs/160)
#
# 给**第三方**复核对 Loment 0.1.4 Alpha 的主张: 一条命令跑完全部判据, 打印/落盘一份
# 可附在审计报告后的证据 (含版本、提交、工件哈希、每条主张的结果)。
#
#   python tools/loment_audit.py            # 跑全部, 人类可读
#   python tools/loment_audit.py --json     # 落盘 loment/build/audit-report.json
#   python tools/loment_audit.py --list     # 只列主张与对应命令 (不跑)
#
# 设计原则: 这里**只调用已有的门禁**, 不新写判据 —— 审计工具自己长成第二套判据是
# 审计里最常见的坑。退出码: 0 = 全部通过 / 1 = 有红 / 2 = 用法错误。

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_release  # noqa: E402  (版本名取它的 RELEASE: 单一真源)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "build" / "audit-report.json"

#: 主张 -> (跑它的工具模块, 传给 main 的参数)。工具名必须出现在 tools/ci.py 的
#: STATIC_CHECKS 里 (loment_tools_test::test_audit_claims_match_ci 会核这条,
#: 防止审计工具偷偷跑别的东西)。
CLAIMS: list[tuple[str, str, str, list[str]]] = [
    ("C1", "自举 checker 与参考实现规则等价 (棘轮门禁, 假阳性/漂移必须为 0)",
     "loment_rule_parity", []),
    ("C2", "两个后端逐字节等价 + 三阶段自举定点 + 40/40 语料零诊断 + driver 负例被拒",
     "loment_p8_test", []),
    ("C3", "参考实现自身的 91 条判据 (含 Rust/IR/原生后端形状)",
     "lomentc_test", []),
    ("C4", "工具侧: 诊断分类完整 / 内建表一致 / 增量缓存 / DWARF 变量信息等 18 条",
     "loment_tools_test", []),
    ("C5", "无 Python 自举: 种子与参考逐字符一致 + 启动脚本无解释器 + 定点",
     "loment_seed_test", []),
    ("C6", "Loment 版格式化器与 Python 版逐字节相同 (42 语料 + 边界 + 幂等)",
     "loment_fmt_test", []),
    ("C7", "示例集/交叉编译 (25 个示例 + aarch64 目标发射)",
     "loment_p9_test", []),
    ("C8", "VS Code 宿主与 LSP 往返 (无头验收)",
     "vscode_ext_test", []),
    # **C9 的量尺 2026-10-16 换过**：原来指向 `loment_release`（无参 = "入库台账 == 当前树"），
    # 而那条**只在唯一权威的那棵树上为真**。全树台账不再由 PR 提交之后（原因见
    # `.github/workflows/manifest.yml` 文件头那节），它从门禁里撤了下来 —— 一条在交付物上
    # 恒假的判据不能挂在门禁上。换成的量尺是"**两个独立实现给出同一份清单**"；
    # 台账**与树**的一致性由 `lom_audit` 的 M95 按**滞后容差**接着判（`docs/254`）。
    ("C9", "发布清单**可复现**：Loment 版生成器与 Python 版 stdout / 落盘字节 / 退出码"
           "**逐字节相同**（两个独立实现给出同一份清单）",
     "loment_rel_test", []),
    ("C10", "状态矩阵与 docs/145 一致 (账本不是手改的)",
     "loment_status", []),
    ("C11", "LSP 去 Python: Loment 版语言服务 (补全/跳转/诊断/--check) 与工具等价性",
     "loment_lsp_test", []),
    ("C12", "工具链等价性: 格式化器与文档生成器与 Python 版逐字节相同",
     "loment_doc_test", []),
    ("C13", "JSON 库与 Python json 逐字节一致 (LSP 的 JSON-RPC 靠它)",
     "loment_json_test", []),
    ("C14", "Windows 文件类型注册 (.lomt/.lom 常驻打开方式) 与启动脚本无解释器",
     "loment_filetype_test", []),
    ("C15", "发行包: 命令安装 (sh/ps1) 与自解压安装包, 装出来的编译器产物与参考逐字节相同",
     "loment_dist_test", []),
    ("C16", "发行包签名: Authenticode (发布者可读/篡改可验) + SHA256SUMS 分离签名",
     "loment_sign_test", []),
    ("C17", "包管理器去 Python: Loment 版 lompkg 与 Python 版 stdout 逐字节相同 "
            "(拓扑序 + sha256 + 环检测 + 锁往返)",
     "loment_pkg_test", []),
    ("C18", "L0 生成器去 Python: Loment 版 lomc 的四个后端与 Python 版**逐字节相同** "
            "(12 份发射 + --emit 落盘 + --check 对账/漂移 + 错误码)",
     "loment_lomc_test", []),
    ("C19", "原生 ELF 后端: tools/lomelf.py 把 IR 直接编成 x86-64 ELF (不经 clang), "
            "与 clang 链的产物**行为逐值一致** (stdout 字节 + 退出码); 种子 -> stage1 -> IR "
            "-> 原生产物全程无 clang",
     "loment_elf_test", []),
    ("C20", "原生 ELF 后端**自举侧镜像**: loment/tools/lomelf.lomt (走种子自举链编成二进制) "
            "对语料产出的 ELF 与参考实现**逐字节相同** —— 编译一个 Loment 程序全程不需要 clang",
     "loment_elf_test", []),
    ("C21", "链条的起点 (genesis): 提交进仓库的 lomelf 二进制起头, 在**没有 clang** 的环境里"
            "跑通 bootstrap.sh 四条证明 —— 重建工具链不需要 C 编译器, 也不需要解释器",
     "loment_genesis_test", []),
    ("C22", "构建路径去 Python 第一格: Loment 版里程碑状态矩阵生成器, 与 Python 版 "
            "stdout/stderr/落盘字节/退出码**逐字节相同** (含 --check 漂移检出)",
     "loment_status_test", []),
    ("C23", "构建路径去 Python 第二格: Loment 版发布清单生成器 (glob 展开 + sha256 + 定形 JSON), "
            "与 Python 版 stdout / 落盘字节 / 退出码**逐字节相同**",
     "loment_rel_test", []),
    ("C24", "原生 PE 后端: 同一份 IR 产出的 Windows PE 控制台程序**在本机原生跑**, "
            "与 clang/Linux 路的 stdout 字节 + 退出码一致; 编译与运行都不碰 clang/wsl。"
            "syscall 面补齐到 read/write/close/brk/exit/getdents64/openat/newfstatat, "
            "argv 由 shim 合成 —— **构建工具 lomstatus/lomrel 本身**编成 PE 原生跑, "
            "输出与 Python 版逐字节相同",
     "loment_pe_test", []),
]


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, shell=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _clang_path() -> str | None:
    import shutil
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


def provenance() -> dict:
    cc = _clang_path()
    ver = ""
    if cc:
        try:
            ver = (subprocess.run([cc, "--version"], capture_output=True, text=True,
                                  shell=False).stdout.splitlines() or [""])[0]
        except Exception:  # noqa: BLE001
            ver = ""
    return {
        "commit": _git("rev-parse", "HEAD"),
        "describe": _git("describe", "--tags", "--always"),
        "tag": _git("tag", "--points-at", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "clang": ver or "(没找到 clang —— 需要它的判据会 SKIP)",
        "python": sys.version.split()[0],
    }


def run_claim(module_name: str, argv: list[str]) -> tuple[bool, str]:
    """进程内调用工具 main() —— 与 ci.py 同一套调用方式 (不经 shell)。"""
    tail = ""
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:  # noqa: BLE001
        return False, f"import 失败: {type(e).__name__}: {e}"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            fn = getattr(mod, "main", None)
            rc = int(fn(argv)) if fn is not None and _accepts_argv(fn) else _main_noargs(mod)
    except SystemExit as e:  # 工具用 sys.exit(main())
        rc = int(e.code or 0)
    except Exception as e:  # noqa: BLE001
        rc, tail = 1, f"{type(e).__name__}: {e}"
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if lines:
        # 红的时候别只留最后一行: 把失败/差异/错误行挑出来, 报告里才有得归因
        bad = [ln for ln in lines
               if "FAIL" in ln or "DIFF" in ln or "STALE" in ln or ln.lstrip().startswith("[ERR]")]
        tail = (bad[-1] if bad else lines[-1])[:200]
    return rc == 0, tail


def _accepts_argv(fn) -> bool:
    import inspect
    try:
        return len(inspect.signature(fn).parameters) >= 1
    except Exception:  # noqa: BLE001
        return True


def _main_noargs(mod) -> int:
    return int(mod.main())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_audit", description="审计包 (docs/160)")
    ap.add_argument("--json", action="store_true", help="落盘 loment/build/audit-report.json")
    ap.add_argument("--list", action="store_true", help="只列主张")
    a = ap.parse_args(argv)

    if a.list:
        for cid, what, tool, args in CLAIMS:
            print(f"{cid}  {what}\n      -> python tools/{tool}.py {' '.join(args)}")
        return 0

    prov = provenance()
    print("Loment 审计包 (docs/160) —— 只调用已有门禁, 不新增判据")
    print(f"  提交 {prov['commit'][:12]}  {prov['describe']}"
          f"{' [工作区脏]' if prov['dirty'] else ''}")
    print(f"  {prov['clang']}  ·  python {prov['python']}")
    print(f"  tag: {prov['tag'] or '(HEAD 上没有 tag)'}")
    print()

    results = []
    t0 = time.time()
    for cid, what, tool, args in CLAIMS:
        t = time.time()
        ok, tail = run_claim(tool, args)
        results.append({"id": cid, "claim": what, "tool": f"tools/{tool}.py",
                        "argv": args, "ok": ok, "detail": tail,
                        "seconds": round(time.time() - t, 1)})
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid} {what}")
        if tail:
            print(f"         {tail}")

    bad = [r for r in results if not r["ok"]]
    report = {
        "release": loment_release.RELEASE,  # 单一真源: 别再抄一遍字面量
        "provenance": prov,
        "claims": results,
        "passed": len(results) - len(bad),
        "total": len(results),
        "seconds": round(time.time() - t0, 1),
        "non_claims": [
            "冻结面内判据都是**自证**的: 两个实现由同一作者编写, 本报告不等于外部确认",
            "三处刻意保守偏离: 类型未知时 checker 可能少报 (方向保守, 不假阳性) (docs/158 §4)",
            "同名 let 双 alloca: 同一函数里重复声明同名变量时两个后端语义不同 (docs/158 §4)",
            "自举驱动整条链没有 parser: 解析期错误只有参考实现能报 (docs/158 §4)",
            "aarch64 只验证到发射 (无 qemu-user/无真机执行) (docs/158 §4)",
            "自举性能 12.8s (参考 1.2s); DWARF 变量信息的位置求值要完整调试器 (docs/150 M82)",
            "L0 版 lomc 不替 --emit-* 建父目录; 不复刻词法层非法字符/未闭合注释报错 (docs/160 §2.7)",
            "自举 checker 对**关键字当标识符**有一处假阳性 (仍开放) (docs/150 M81 后置修订)",
            "自举 codegen 的 as 取型缺口: `(a + b as T) as U` 发非法 IR (仍开放) (docs/150 M82 后置修订)",
            "Mimosa 扫描器未给出完整结论 (scanner_enobufs) —— 不宣称项目安全",
        ],
    }
    if a.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"\n[OK] {OUT.relative_to(ROOT)}")
    print(f"\nloment_audit: {report['passed']}/{report['total']} 通过 ({report['seconds']}s)"
          + ("" if not bad else " —— 有红: " + ", ".join(r["id"] for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
