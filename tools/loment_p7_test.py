#!/usr/bin/env python3
# loment_p7_test.py — P7 内核集成自检 (M67–M78, docs/149)
#
# 运行: python tools/loment_p7_test.py   (退出码 0 = 全绿)
# 说明: 需要 QEMU/内核的用例在环境缺失时打印 SKIP 并计通过 (环境问题不算回归)。

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _build_ir_c(src: Path, driver: str, td: str, name: str):
    """编译 Loment 模块 + C 驱动 (IR 路径), 返回可执行路径。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, errs
    ll = Path(td) / f"{name}.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = Path(td) / f"{name}.c"
    c.write_text(driver, encoding="utf-8")
    exe = Path(td) / f"{name}.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr
    return exe


def _run(exe: Path) -> str:
    r = subprocess.run([shutil.which(str(exe)) or str(exe)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ---------------------------------------------------------------- M68 AHCI

@test
def test_m68_ahci_core():
    if not _clang():
        print("      SKIP: 无 clang")
        return
    drv = """#include <stdio.h>
#include <string.h>
extern unsigned int ahci_port_count(void *base);
extern unsigned int ahci_ncs(void *base);
extern _Bool ahci_supports_64bit(void *base);
extern unsigned int ahci_enable(void *base);
int main(void) {
    unsigned char regs[256];
    memset(regs, 0, sizeof regs);
    unsigned int cap = 0x1Fu | 0x80000000u;   /* NCS=31 + 64bit */
    memcpy(regs + 0, &cap, 4);
    unsigned int pi = 0x3Fu;                   /* 6 个端口 */
    memcpy(regs + 12, &pi, 4);
    printf("%u %u %d %u\\n", ahci_port_count(regs), ahci_ncs(regs),
           ahci_supports_64bit(regs), ahci_enable(regs));
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as td:
        out = _run(_build_ir_c(EX / "ahci.lomt", drv, td, "ahci"))
    assert out == "6 31 1 1", out


# ---------------------------------------------------------------- M69 FUI 节点

@test
def test_m69_fuc_node_matches_lom_single_source():
    if not _clang():
        print("      SKIP: 无 clang")
        return
    drv = """#include <stdio.h>
extern unsigned int pack_node(unsigned char *p, unsigned int kind, unsigned int id,
                              unsigned int x, unsigned int y, unsigned int bg);
int main(void) {
    unsigned char buf[64];
    unsigned int n = pack_node(buf, 7, 3, 100, 200, 0x11223344u);
    printf("%u ", n);
    for (int i = 0; i < 64; i++) printf("%02x", buf[i]);
    printf("\\n");
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as td:
        out = _run(_build_ir_c(EX / "fuc_node.lomt", drv, td, "fucnode"))
    n, hexs = out.split()
    assert n == "64"
    # S3 之后**产物不在索引里**（`docs/189` §3.0）：缺了就现场生成一份再用。
    lomc.ensure_python(ROOT / "lom" / "fuc.lom", ROOT / "lom" / "build" / "fuc.py")
    sys.path.insert(0, str(ROOT / "lom" / "build"))
    import fuc  # 由 lomc 从 lom/fuc.lom 按需生成
    want = fuc.NODE_STRUCT.pack(7, 3, 0, 0, 100, 200, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                0, 0, 0, 0x11223344, 0, 0, 0, 0, 0, 0).hex()
    assert hexs == want, f"Loment {hexs[:32]}... != lom/fuc.lom {want[:32]}..."


# ---------------------------------------------------------------- M72 系统调用层

@test
def test_m72_syscall_layer_from_single_source():
    import loment_syscalls
    want = loment_syscalls.emit()
    dest = ROOT / "loment" / "build" / "fuai_syscalls.lomt"
    assert dest.exists() and dest.read_text(encoding="utf-8") == want
    ops = loment_syscalls.opcodes()
    assert len(ops) == 46, len(ops)
    assert all(loment_syscalls.kernel_has(c) for _, c in ops)
    mod = lomentc.load(dest)
    assert not lomentc.check(mod), lomentc.check(mod)[:2]
    assert len(mod.funcs) == 46


# ---------------------------------------------------------------- M73 分配器

@test
def test_m73_allocator_first_fit():
    if not _clang():
        print("      SKIP: 无 clang")
        return
    drv = """#include <stdio.h>
extern void pool_init(unsigned char *p, unsigned int bytes, unsigned int block);
extern unsigned char *pool_alloc(unsigned char *p, unsigned int need);
extern unsigned int pool_free(unsigned char *p, unsigned char *blk);
extern unsigned int pool_free_count(unsigned char *p, unsigned int blocks);
int main(void) {
    unsigned char pool[1024];
    pool_init(pool, 1024, 100);          /* 9 块 x 100B */
    unsigned char *a = pool_alloc(pool, 100);
    unsigned char *b = pool_alloc(pool, 100);
    pool_free(pool, a);
    unsigned char *c = pool_alloc(pool, 100);   /* 复用 a */
    printf("%d %d %d %u\\n", a != 0, b != 0 && b != a, c == a, pool_free_count(pool, 3));
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as td:
        out = _run(_build_ir_c(EX / "allocator.lomt", drv, td, "alloc"))
    assert out == "1 1 1 1", out  # 前 3 块: 0/1 占用, 2 空闲


# ---------------------------------------------------------------- M75 调试器

@test
def test_m75_debugger_symbolizer():
    import loment
    if not _clang():
        print("      SKIP: 无 clang")
        return
    assert loment.main(["dbg", str(EX / "toolchain.lomt"), "--fn", "fib"]) == 0


# ---------------------------------------------------------------- M70 中断处理

@test
def test_m70_interrupt_handler_ir_shape():
    """M70: 中断处理函数的 IR 形状 (x86_intrcc + byval 帧) —— IDT 安装归内核侧。"""
    src = ("module m\nchoose runtime\ninterrupt fn timer_isr() {\n    let t: u32 = 1;\n"
           "    store8(alloc(1), 0, t as u8);\n}\n")
    mod = lomentc.Parser(__import__("lomc").lex(src), src).parse()
    ll = lomentc.emit_llvm(mod, ROOT)
    assert "define x86_intrcc void @timer_isr(ptr byval([8 x i8]) %__frame)" in ll, ll
    assert not lomentc.check(mod)


# ---------------------------------------------------------------- M71 模块 ABI

@test
def test_m71_module_abi_entry_shape():
    """M71: 模块 ABI —— 入口 `_start()` 零参 + 栈上 argv, 与 m30_linux.elf 同形。

    判据**现编现验**。原先它读 `loment/build/user_hello.elf` —— 一个没进 git、也没有任何
    东西重新生成的残留文件: 干净检出时它不存在, 整条判据**静默跳过**; 机器上留着旧产物时
    才跑, 验的却是那份旧产物的布局。2026-09-15 实测: 残留那份入口在 `0x201260`, 而工具链
    现编出的是 `0x400000` —— 也就是说这条判据**从来没有验过当前工具链**。
    """
    import lomelf
    entry = EX / "user_hello.lomt"
    mod = lomentc.load(entry)
    start = next(f for f in mod.funcs if f.name == "_start")
    assert not start.params and start.ret == "()"
    ir = lomentc.emit_llvm(mod, ROOT, lomentc.resolve_deps(mod, ROOT, EX, entry=entry))
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "user_hello.elf"
        p.write_bytes(lomelf.compile_ll(ir)[0])
        out = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-f", str(p)], capture_output=True, text=True, shell=False).stdout
    assert "start address: 0x000000000040" in out, out


# ---------------------------------------------------------------- M67/M76/M77/M78

@test
def test_m67_m78_boot_programs():
    qemu = shutil.which("qemu-system-x86_64")
    kernel = ROOT / "kernel" / "fujo-kernel.bin"
    if not qemu or not kernel.exists():
        print("      SKIP: 无 QEMU 或内核镜像")
        return
    import loment_boot
    for name, needle in (("user_hello", "M67 RESULT: PASS"),
                         ("bootprobe", "M76 RESULT: PASS"),
                         ("selfcheck", "M77 RESULT: PASS"),
                         ("all_loment", "M78 RESULT: PASS")):
        src = EX / f"{name}.lomt"
        elf = loment_boot.build_elf(src, ROOT / "loment" / "build" / f"{name}.elf")
        initrd = loment_boot.pack_initrd(
            elf, name, ROOT / "loment" / "build" / f"{name}.initrd")
        assert loment_boot.run_qemu(initrd, name, needle, 40.0), f"{name}: 未出现 {needle}"


def main() -> int:
    return loment_suite.run(TESTS, "loment_p7_test")


if __name__ == "__main__":
    sys.exit(main())
