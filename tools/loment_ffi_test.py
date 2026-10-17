#!/usr/bin/env python3
# loment_ffi_test.py — FFI 第 1 阶段的门面判据 (docs/173)
#
# 判的是一整条链, 不是一层: **一个别的语言写的目标文件(.o), 由 Loment 声明成 `extern fn`,
# 用 `lomelf` 单独链接成可执行文件, 跑出正确答案**。这条一旦绿, "能调外部库"就是真的,
# 而不是"IR 里有个 declare"。
#
# 为什么必须比**运行结果**而不是比 IR: 这条路上最容易错的两处都**不改 IR** ——
#   ① C ABI 传参 (实参进 rdi/rsi/... 而不是我们自己的栈) —— 改错了 IR 一模一样;
#   ② 调用结果的落槽 —— 少写一句, 调用结果丢掉, 后面读到旧值 (实测退过 0 而不是 37)。
# 两者都只有**跑起来看结果**才抓得到。
#
# 证伪 (把实现弄坏, 确认它会红) —— **三条都真做过**:
#   * 把 `C_ARG_REGS` 前两个寄存器对调 -> 两条 ABI 判据红 (rc=22≠52, rc=213≠123);
#   * 把 `self.put(d, RAX)` 那句删掉 -> 红, 退出码变成 0 (调用结果丢掉, 后面读到旧值);
#   * 让 `parse_ll` 重新跳过 `declare` -> 红 (`finalize` 报未定义标签)。
#
# **第一次证伪时它没红**: 那时的 C 夹具只用了 `a+b` 与 `a*b`, 而这两条**可交换** ——
# 把 rdi/rsi 对调这种彻底的 ABI 错误, 结果**一模一样**。教训写进 `C_SOURCE` 的注释:
# 测传参次序的判据, 夹具里必须有**非交换运算**, 否则测的是"能跑"而不是"传参对"。
#
# 平台: 需要 clang (交叉编出 x86_64 ELF 目标文件) 与 WSL (跑 ELF)。缺任一则 SKIP。

from __future__ import annotations

import os

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf                                                       # noqa: E402
import lomentc                                                      # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

#: 被调的那份 C。**freestanding**: 不碰 libc —— 第 1 阶段不链 libc (docs/173 §3),
#: 目标文件里一旦出现未定义符号就会被硬拒 (下面有一条判据钉这个)。
#:
#: **全部用非交换运算** —— 这条是证伪逼出来的: 第一版只用了 `a+b` 和 `a*b`, 于是"把
#: rdi/rsi 对调"这种**彻底的 ABI 错误**照样绿 (加法交换律把它盖住了)。判据挑运算时,
#: 必须让**实参次序**影响结果, 否则它测的是"能跑"而不是"传参对"。
C_SOURCE = """\
int c_add(int a, int b) { return a + b; }
int c_mul(int a, int b) { return a * b; }
int c_sub(int a, int b) { return a - b; }
int c_mix(int a, int b, int c) { return a * 100 + b * 10 + c; }
void c_nop(void) { }
"""

#: 调 `C_SOURCE` 的那份 Loment: 三个 `extern fn`, 结果 7 + 30 + 15 = **52**。
#: 顺序敏感的是 `c_sub` (20-5=15; 寄存器对调会得到 -15) —— 与 `C_SOURCE` 的注释同一条理由。
#: 常量放在模块级是为了让 `loment_elf_test` 的自举镜像判据共用**同一份**源码, 不抄第二遍。
LOMENT_SOURCE = """\
module ffidemo

extern fn c_add(a: i32, b: i32) -> i32;
extern fn c_mul(a: i32, b: i32) -> i32;
extern fn c_sub(a: i32, b: i32) -> i32;

fn _start() {
    let r: i32 = c_add(3 as i32, 4 as i32)
        + c_mul(5 as i32, 6 as i32)
        + c_sub(20 as i32, 5 as i32);
    syscall4(60, r as u64, 0, 0);
}
"""

#: 粒度 B (docs/212 §5): 内核用 **Loment** 写 (不是 C), 经 `tools/loment_opt_obj.py` 编成
#: 预优化的 C-ABI `.o`; 再 `--link` 进调用方。`pub` 是必须的 —— 只有外部链接的函数才会
#: 出现在 `.o` 的导出表里 (`T k_add`)。
KERNEL_SOURCE = """\
module k

pub fn k_add(a: i32, b: i32) -> i32 { return a + b; }
pub fn k_mul(a: i32, b: i32) -> i32 { return a * b; }
"""

#: 调 `KERNEL_SOURCE` 的调用方: 3+4 + 5*6 = **37**。
KERNEL_CALLER_SOURCE = """\
module kapp

extern fn k_add(a: i32, b: i32) -> i32;
extern fn k_mul(a: i32, b: i32) -> i32;

fn _start() {
    let r: i32 = k_add(3 as i32, 4 as i32) + k_mul(5 as i32, 6 as i32);
    syscall4(60, r as u64, 0, 0);
}
"""


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _llvm_bin(name: str) -> str | None:
    """LLVM 自带工具 (`llvm-ar` 等) —— 与 clang 同目录, Windows 上就有。"""
    clang = _clang()
    if not clang:
        return None
    p = Path(clang).with_name(name)
    return str(p) if p.exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def compile_c(clang: str, src: Path, obj: Path, extra: tuple[str, ...] = ()) -> int:
    r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                        "-ffreestanding", "-fno-stack-protector", *extra,
                        "-o", str(obj), str(src)],
                       capture_output=True, text=True, shell=False)
    return r.returncode


def build_and_run(td: Path, lomt: Path, objs: list[Path], exit_want: int) -> tuple[int, str]:
    """Loment 源码 -> IR -> (lomelf + 外部对象) -> 跑。返回 (退出码, stderr)。"""
    mod = lomentc.load(lomt)
    errs = lomentc.check(mod)
    assert not errs, errs
    ir = lomentc.emit_llvm(mod, ROOT)
    # `allow_ports=True`：这条判据跑的**就是**开发通道那条还没完工的路（`docs/222` §4 的
    # 端口门默认拒带未定义符号的外源对象 —— 发行版里 libc 不可调用，正是靠那个默认值）。
    blob, _info = lomelf.compile_ll(ir, [lomelf.load_foreign(p) for p in objs],
                                    allow_ports=True)
    exe = td / "ffi.elf"
    exe.write_bytes(blob)
    r = subprocess.run([*_WSLCMD, "bash", "-lc",
                        f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                       capture_output=True, text=True, timeout=120, shell=False)
    return r.returncode, r.stderr


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_c_end_to_end():
    """C: `c_add(3,4) + c_mul(5,6) + c_sub(20,5)` 走完整条链, 退出码必须是 52。

    退出码 52 = 7 + 30 + 15。**任何一环错都到不了这个数**: 传参顺序错 -> `c_sub` 给出
    -15 而不是 15 (这是**故意**挑非交换运算的理由, 见 `C_SOURCE` 的注释 —— 第一版只用
    加减乘里可交换的那两个, 结果连"rdi/rsi 对调"都测不出来); 结果不落槽 -> 后面读到旧值
    (实测退 0 而不是 37); `declare` 被跳过 -> 链接期报未定义标签。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o"], 52)
        assert rc == 52, f"C 端到端结果不对: rc={rc} (期望 52) err={err[-300:]!r}"
        print("      C: c_add/c_mul/c_sub 端到端 -> 退出码 52 (含次序敏感的一项)")


@test
def test_loment_kernel_object_is_reusable_without_clang():
    """**粒度 B (docs/212 §5)** —— "端机只要那个字节"。

    内核用 **Loment** 写, 经 `tools/loment_opt_obj.py` 编成**预优化的 C-ABI `.o`**
    (那一步**要 clang**), 再 `--link` 进调用方。**链接与运行那一侧完全不碰 clang**
    (自举链接器 `lomelf` 是纯 Python) —— 这就是 Python 的 `numpy` wheel 那个形状:
    编一次、按内容寻址、端机不需要编译器。

    判据三样, 缺一不可:
      * 产物**确定**: 同一份内核两次编出的**字节相同** (那才是"按内容寻址"能成立的前提),
        且 `.o` **导出**了该导出的符号;
      * `--link` 进来**跑出正确答案** (3+4 + 5*6 = 37);
      * **把 `PATH` 掐掉** (端机上根本没有 clang) 之后, 链接+运行那一步**照跑** ——
        这一条才是这条主张本身, 前两条单独都撑不住它。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    import os

    import loment_opt_obj
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        k = td / "k.lomt"
        k.write_text(KERNEL_SOURCE, encoding="utf-8", newline="\n")
        app = td / "app.lomt"
        app.write_text(KERNEL_CALLER_SOURCE, encoding="utf-8", newline="\n")
        o1, o2 = td / "k1.o", td / "k2.o"
        assert loment_opt_obj.emit_object(k, o1) == 0, "产出内核对象失败"
        assert loment_opt_obj.emit_object(k, o2) == 0, "第二次产出失败"
        assert o1.read_bytes() == o2.read_bytes(), "同一份内核两次编出的字节不同 (内容寻址不成立)"
        # 端机那一侧: **PATH 里没有 clang**, 链接照旧 (这一段不 spawn 任何东西, 掐 PATH 安全)。
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = "/nonexistent-clang-free"
            mod = lomentc.load(app)
            assert not lomentc.check(mod), "调用方自检失败"
            ir = lomentc.emit_llvm(mod, ROOT)
            blob, _info = lomelf.compile_ll(ir, [lomelf.load_foreign(o1)], allow_ports=True)
        finally:
            os.environ["PATH"] = old
        exe = td / "k.elf"
        exe.write_bytes(blob)
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=120, shell=False)
        assert r.returncode == 37, f"无 clang 端机 `--link` 跑错: rc={r.returncode} {r.stderr[-200:]}"
        print("      Loment 内核 -> clang -O2 的 .o -> **无 clang 端机** --link -> rc=37")


@test
def test_three_args_and_void_call():
    """三个实参 + 一个 void 调用: 走完寄存器路径的更多位置, 且 void 调用不留返回值。

    `c_mix(a,b,c) = a*100 + b*10 + c` —— 三个位置**各有各的量级**, 于是"第 3 个实参走到
    第 1 个寄存器"这类错位会被看出来 (求和同样是交换的, 测不出位置)。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(
            "module ffi3\n\n"
            "extern fn c_mix(a: i32, b: i32, c: i32) -> i32;\n"
            "extern fn c_nop();\n\n"
            "fn _start() {\n"
            "    c_nop();\n"
            "    let r: i32 = c_mix(1 as i32, 2 as i32, 3 as i32);\n"
            "    syscall4(60, r as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o"], 123)
        assert rc == 123, f"三实参/void 结果不对: rc={rc} (期望 123) err={err[-300:]!r}"
        print("      三实参 + void 调用 -> 退出码 123 (位置敏感)")


#: 第二个 `.o` 的内容 —— 只为"**两个**外部对象一起链"那条判据存在。
C2_SOURCE = """\
int d_scale(int a, int b) { return a * 10 - b; }
"""

#: **跨对象引用**的两半 (docs/173 阶段 2)。`c_add` 调 `c_sub`, 而 `c_sub` 只在另一个
#: 目标文件里 —— 于是 `a.o` 里会有一条指向**未定义**符号的 `R_X86_64_PLT32`, 它只能靠
#: "先摆好所有对象再建符号表"才解得出。`noinline` 是必须的: 不然 -O1 会把它内联掉,
#: 重定位就没了, 判据会退化成"什么都没测"。
C_SUB_SOURCE = """\
int c_sub(int a, int b) { return a - b; }
"""
C_CALLER_SOURCE = """\
extern int c_sub(int, int);
__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }
"""
#: 调上面那一对的 Loment: 退出码 22 = `c_add(3,4)` = 7 + `c_sub(20,5)` = 15。
LOMENT_MULTI_SOURCE = """\
module multi

extern fn c_add(a: i32, b: i32) -> i32;

fn _start() {
    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);
}
"""

#: 调**两个** `.o` 的那份 Loment。它存在的理由是**名字池的基**:
#: 自举链接器把外部符号名拷进**输入缓冲的尾部** —— 标签表存的是偏移, 而 `lbl_find` 拿
#: 输入缓冲当基。若把名字留在**对象缓冲**里, 第二个 `.o` 一读进来就会把第一个的符号名
#: 盖掉, 然后在回填时报"未定义的标签: c_add"。**单对象测不出这个**: 只有一个对象时那块
#: 缓冲始终是完整的。所以这条判据必须有两个对象才有意义。
#: 退出码 75 = c_add(3,4)=7 + d_scale(7,2)=68。
LOMENT_TWO_OBJECTS_SOURCE = """\
module ffitwo

extern fn c_add(a: i32, b: i32) -> i32;
extern fn d_scale(a: i32, b: i32) -> i32;

fn _start() {
    let r: i32 = c_add(3 as i32, 4 as i32) + d_scale(7 as i32, 2 as i32);
    syscall4(60, r as u64, 0, 0);
}
"""


@test
def test_two_foreign_objects():
    """两个 `.o` 一起 `--link`: 第一个对象导出的符号不能被第二个对象的读入盖掉。

    退出码 75 = 7 + (70 - 2)。`d_scale` 也是**非交换**的 (`a*10 - b`), 所以传参次序在
    这条里同样被测到。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        (td / "d.c").write_text(C2_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        assert compile_c(clang, td / "d.c", td / "d.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_TWO_OBJECTS_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o", td / "d.o"], 75)
        assert rc == 75, f"两个外部对象结果不对: rc={rc} (期望 75) err={err[-300:]!r}"
        print("      两个外部对象一起链 -> 退出码 75")


#: Rust 那份。**`#![no_std]` + `#[no_mangle] extern "C"`** —— 这就是 Rust 库对外暴露 C ABI 的
#: 标准做法 (`#[no_mangle]` 保住符号名, `extern "C"` 保住调用约定)。目标用
#: `x86_64-unknown-none`(裸机, 对象格式是 ELF), 因为这台机器上只装了 msvc 与 none 两个 target。
#: 同样挑**非交换**的减法, 理由见 `C_SOURCE` 的注释。
RUST_SOURCE = """\
#![no_std]
#[no_mangle]
pub extern "C" fn r_sub(a: i32, b: i32) -> i32 { a - b }
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
"""


def _rustc() -> str | None:
    return shutil.which("rustc")


def _rust_has_target() -> bool:
    """**这份工具链装没装**这个目标的 `core` —— 不是"rustc 认不认识这个名字"。

    原先是 `rustc --print target-list`, 而那个名单对**所有**内置目标都为真: 它回答的是
    "名字在不在内置表里", 与有没有这个目标的 `core` 无关。本机没装 rustc 时这一族判据
    一律 SKIP, 所以错在哪看不出来; `#371` 让它们在 runner 上**原生跑**之后现形 ——
    容器里 apt 装的 rustc **认识** `x86_64-unknown-none`, 却没装它的 core, 于是判据红在
    "工具链不全"上 (`error[E0463]: can't find crate for core … may not be installed`),
    而那不是被测行为。
    """
    rustc = _rustc()
    if not rustc:
        return False
    r = subprocess.run([rustc, "--print", "sysroot"], capture_output=True,
                       text=True, shell=False)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    d = Path(r.stdout.strip()) / "lib" / "rustlib" / "x86_64-unknown-none" / "lib"
    return d.is_dir() and any(d.glob("libcore*.rlib"))


@test
def test_rust_end_to_end():
    """Rust: 一个 `#[no_mangle] extern "C"` 的静态库目标文件, 由 Loment 调。

    **同一个机制, 不同的语言** —— C ABI 是那条共同接口: Rust 侧 `#[no_mangle]` 保符号名、
    `extern "C"` 保调用约定, 我们这边 `extern fn` 声明 + 寄存器传参。所以"支持一个语言"
    在阶段 1/2 里就是"给它写一条构建配方 + 一条判据", 不是给每个语言写一套 FFI。
    """
    rustc, clang = _rustc(), _clang()
    if not rustc or not _rust_has_target() or not clang or not _wsl():
        print("      SKIP: 需要 rustc + x86_64-unknown-none + clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "r.rs").write_text(RUST_SOURCE, encoding="utf-8", newline="\n")
        r = subprocess.run([rustc, "--target", "x86_64-unknown-none", "--crate-type",
                            "staticlib", "--emit=obj", "-O",
                            "-o", str(td / "r.o"), str(td / "r.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"rustc 失败: {r.stderr[-300:]}"
        (td / "m.lomt").write_text(
            "module ffirust\n\n"
            "extern fn r_sub(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, r_sub(50 as i32, 8 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "r.o"], 42)
        assert rc == 42, f"Rust 端到端结果不对: rc={rc} (期望 42) err={err[-300:]!r}"
        print("      Rust: #[no_mangle] extern \"C\" 目标文件 -> 退出码 42")


@test
def test_cpp_end_to_end():
    """C++: `extern "C"` 包一层 —— 与 C 同一个机制 (C 系列的语言都走这条)。

    直接调 C++ 的重载/名字修饰符号需要 Itanium 还原, 那是另一件事; 库对外暴露的那一面
    照例是 `extern "C"`。
    """
    clang = _clang()
    cxx = shutil.which("clang++") or str(Path(clang).with_name("clang++.exe")) \
        if clang else None
    if not clang or not cxx or not Path(cxx).exists() or not _wsl():
        print("      SKIP: 需要 clang++ + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "cxx.cc").write_text(
            "namespace demo { int div(int a, int b) { return a / b; } }\n"
            "extern \"C\" int cxx_div(int a, int b) { return demo::div(a, b); }\n",
            encoding="utf-8", newline="\n")
        r = subprocess.run([cxx, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                            "-ffreestanding", "-fno-stack-protector", "-fno-exceptions",
                            "-fno-rtti", "-o", str(td / "cxx.o"), str(td / "cxx.cc")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"clang++ 失败: {r.stderr[-300:]}"
        (td / "m.lomt").write_text(
            "module fficxx\n\n"
            "extern fn cxx_div(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, cxx_div(84 as i32, 2 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "cxx.o"], 42)
        assert rc == 42, f"C++ 端到端结果不对: rc={rc} (期望 42) err={err[-300:]!r}"
        print("      C++: extern \"C\" 包装 -> 退出码 42")


@test
def test_python_via_process_bridge():
    """Python: 起一个 `python3`, 让它 import 它自己的库, 把结果读回来。

    **这是第二条腿, 不是 C ABI 那条** (docs/173 §2 的说明): Python 不是"导出 C ABI 的库",
    是解释器。所以这里**不链接** —— 起一个进程, 把代码交给它, 把 stdout 读回来。判据里用的
    是 Python 自带的 `json` 库 (不是我们写死的一个数), 所以它证明的是"**真用上了 Python 的库**",
    而不是"能起个进程"。

    退出码 = `len(json.dumps([1,2,3]))` = 9 (即 `[1, 2, 3]` 的长度)。数字是 Python 算的,
    Loment 只把读回来的第一个字节转成退出码 —— 中间没有任何一处是我们自己算的。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    code = r"""import json
print(len(json.dumps([1,2,3])))"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "p.lomt").write_text(
            "module pybridge\nchoose runtime\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            "    let n: i64 = proc_sh(\"python3 -c 'import json; print(len(json.dumps([1,2,3])))'\",\n"
            "                         buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    let d: u32 = load8(buf, 0) - 48;\n"       # '9' -> 9
            "    syscall4(60, d as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        # 先把桥自己的模块装进单元 (use proc)
        mod = lomentc.load(td / "p.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "p.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        blob, _info = lomelf.compile_ll(ir)
        exe = td / "p.elf"
        exe.write_bytes(blob)
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        assert r.returncode == 9, (
            f"Python 桥结果不对: rc={r.returncode} (期望 9 = len(json.dumps([1,2,3]))) "
            f"err={r.stderr[-300:]!r}")
        print("      Python: python3 + json 库, 输出读回 -> 退出码 9")


@test
def test_java_via_process_bridge():
    """Java: 腿 2 上的第六个语言。JDK 是**免 sudo 下载**的 (Adoptium tarball, 见 `_tc.sh`)。

    走的是 Java **自己的生态**: `javac` 编一个 `.java`, 再 `java` 跑它 —— 与 Python / Perl /
    Lua 是**同一个函数** (`proc_sh`), 区别只在命令。这就是腿 2 的意思: 不装绑定、不做接口层。

    退出码 7 = `System.out.println(7)` 打出来的那个 7。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    jdk = f"{_wsl_home()}/{TC_SUBDIR}/jdk/bin"
    if not _wsl_ok(f"test -x '{jdk}/java'"):
        print("      SKIP: 没有自建 jdk (跑 wsl -e bash tools/loment_toolchain.sh jdk)")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "Hello.java").write_text(
            "public class Hello { public static void main(String[] a) { "
            "System.out.println(7); } }\n", encoding="utf-8", newline="\n")
        # `javac` 得在**它自己的目录**里跑, 而那一侧要看得见这个 `.java` —— 所以先拷进 WSL。
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"rm -rf {_T}ffijava && mkdir -p {_T}ffijava && "
                            f"cp {_wsl_path(td)}/Hello.java {_T}ffijava/"],
                           capture_output=True, text=True, timeout=120, shell=False)
        assert r.returncode == 0, f"拷 .java 失败: {r.stderr[-200:]}"
        _bridge_case(f"cd {_T}ffijava && {jdk}/javac Hello.java && {jdk}/java Hello",
                     7, "Java 桥")
        print("      Java: javac + java (自建 JDK, 免 sudo) -> 退出码 7")


@test
def test_javascript_via_process_bridge():
    """JavaScript: 同一条腿上的第三个语言 —— 换个命令就行, **代码一行不用改**。

    这条顺带钉住一件事: `proc_sh` 是**通用**的 (起进程 + 读 stdout), 不是"Python 专用"。
    语言数量在第二条腿上**不是**工作量 —— 有解释器就能用; 真正的工作量在第一条腿
    (C ABI 那一族) 的链接能力上。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    has = subprocess.run([*_WSLCMD, "bash", "-lc", "command -v node"],
                         capture_output=True, text=True, timeout=60, shell=False)
    if has.returncode != 0:
        print("      SKIP: WSL 里没有 node")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "j.lomt").write_text(
            "module jsbridge\nchoose runtime\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            "    let n: i64 = proc_sh(\"node -e 'console.log(JSON.stringify([1,2,3]).length)'\",\n"
            "                         buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    syscall4(60, (load8(buf, 0) - 48) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "j.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "j.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps))
        exe = td / "j.elf"
        exe.write_bytes(blob)
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        # JSON.stringify([1,2,3]) = "[1,2,3]" -> 长度 7
        assert r.returncode == 7, (
            f"JS 桥结果不对: rc={r.returncode} (期望 7 = len(JSON.stringify([1,2,3]))) "
            f"err={r.stderr[-300:]!r}")
        print("      JavaScript: node + JSON 库, 输出读回 -> 退出码 7")


def _wsl_ok(shell_cmd: str) -> bool:
    """在 WSL 里跑一句 shell, 返回是否退出 0。探测解释器/工具链在不在用它。"""
    return subprocess.run([*_WSLCMD, "bash", "-lc", shell_cmd],
                          capture_output=True, text=True, timeout=120,
                          shell=False).returncode == 0


def _wsl_home() -> str:
    r = subprocess.run([*_WSLCMD, "bash", "-lc", "printf %s \"$HOME\""],
                       capture_output=True, text=True, timeout=60, shell=False)
    return r.stdout.strip()


def _bridge_case(cmd: str, want_rc: int, label: str) -> None:
    """走一遍进程桥: 起 `cmd`, 把它 stdout 的第一个字节减 48 当退出码 (所以输出要是**一位数字**)。

    两条腿的差别在这里最清楚: 腿 2 **换语言只是换命令**, Loment 这边一行不改。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "p.lomt").write_text(
            "module bridge\nchoose runtime\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            f"    let n: i64 = proc_sh(\"{cmd}\", buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    syscall4(60, (load8(buf, 0) - 48) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "p.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "p.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps))
        exe = td / "p.elf"
        exe.write_bytes(blob)
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        assert r.returncode == want_rc, (
            f"{label} 结果不对: rc={r.returncode} (期望 {want_rc}) err={r.stderr[-300:]!r}")


#: 免 sudo 拿的工具链放这儿 (docs/177 §2)。**必须在 `$HOME` 而不是 `/tmp`** —— WSL 闲置
#: 会关停, tmpfs 一关就清空 (2026-09-17 实测: 下好的 zig 就这么没了)。
#: 取法: `wsl -e bash tools/loment_toolchain.sh lua`
TC_SUBDIR = "fujotc"


@test
def test_perl_via_process_bridge():
    """Perl: 腿 2 上的第四个语言。**WSL 里本来就有 perl**, 所以这条真的跑。

    用的是 Perl **自己的库** (`JSON::PP` 是核心模块) —— 这正是"支持一个语言的库"在腿 2 上
    的意思: 不装绑定、不做接口层, 对方照旧用它自己的生态。

    退出码 7 = `encode_json([1,2,3])` = `"[1,2,3]"` 的长度。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    if not _wsl_ok("command -v perl"):
        print("      SKIP: WSL 里没有 perl")
        return
    # **用 `-MJSON::PP` 显式加载, 不写 `use JSON::PP`** —— 后者在 `-e` 里要分号与换行,
    # 而我们的命令是塞进 Loment 字符串字面量的一行。
    _bridge_case("perl -MJSON::PP -e 'print length(encode_json([1,2,3]))'", 7, "Perl 桥")
    print("      Perl: JSON::PP 库, 输出读回 -> 退出码 7")


@test
def test_lua_via_process_bridge():
    """Lua: 腿 2 上的第五个语言。解释器是**免 sudo 自建**的 (见 `tools/loment_toolchain.sh`)。

    命令里避开了**双引号** (`string.char(44)` 代替 `","`) —— 它整条要塞进 Loment 的字符串
    字面量, 而 Loment 的字符串用双引号括。这不是技巧, 是这条腿的真实约束: 命令是一行字符串。

    退出码 5 = `table.concat({1,2,3}, ",")` = `"1,2,3"` 的长度。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    lua = f"{_wsl_home()}/{TC_SUBDIR}/lua-5.4.7/src/lua"
    if not _wsl_ok(f"test -x '{lua}'"):
        print(f"      SKIP: 没有自建 lua (跑 wsl -e bash tools/loment_toolchain.sh lua)")
        return
    _bridge_case(f"{lua} -e 'print(#table.concat({{1,2,3}},string.char(44)))'", 5, "Lua 桥")
    print("      Lua: table.concat/string.char, 输出读回 -> 退出码 5")


@test
def test_zig_end_to_end():
    """Zig: `export fn` 导出 C ABI 符号 —— **腿 1 上的第四个语言** (docs/177 P2)。

    Zig 是**免 sudo 自建**的 (ziglang.org 的 tarball, 见 `tools/loment_toolchain.sh`)。

    **`-OReleaseSmall` 不是随手挑的**: 默认档会把 Zig 自己的运行期一起编进来 —— 实测对象
    **9.7 MB**、还带一整张 `.rela.data` 数据重定位表, 而这两种我们都不支持。`-OReleaseSmall`
    把它压到 **880 字节**、只剩一条 `.rela.eh_frame`(那张节我们不摆, 按元数据跳过)。
    所以"能不能用一个语言的库"在这里**不是**"有没有工具链", 而是"**那份对象是不是自包含的**"
    —— 这正是 `docs/173` §3 那把尺子在量的事。

    退出码 42 = `z_sub(50, 8)`。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    zig = f"{_wsl_home()}/{TC_SUBDIR}/zig/zig"
    if not _wsl_ok(f"test -x '{zig}'"):
        print(f"      SKIP: 没有自建 zig (跑 wsl -e bash tools/loment_toolchain.sh zig)")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "z.zig").write_text(
            "export fn z_sub(a: i32, b: i32) i32 { return a - b; }\n",
            encoding="utf-8", newline="\n")
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"cd {_wsl_path(td)} && {zig} build-obj -OReleaseSmall "
                            f"-femit-bin=z.o z.zig"],
                           capture_output=True, text=True, timeout=300, shell=False)
        assert r.returncode == 0, f"zig build-obj 失败: {r.stderr[-300:]}"
        (td / "m.lomt").write_text(
            "module zig\n\n"
            "extern fn z_sub(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, z_sub(50 as i32, 8 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "z.o"], 42)
        assert rc == 42, f"Zig 端到端结果不对: rc={rc} (期望 42) err={err[-300:]!r}"
        print("      Zig: export fn (自建工具链, -OReleaseSmall) -> 退出码 42")


@test
def test_go_via_process_bridge():
    """Go: 腿 2 上的第七个语言。

    Go 的编译器在**主机**上 (`C:\\Program Files\\Go\\bin\\go.exe`), 而我们的 ELF 跑在 WSL 里 ——
    靠 **WSL interop** 直接把那个 `.exe` 起来 (WSL 的 binfmt 注册了 Windows 二进制)。这条路
    成立恰恰说明腿 2 的定义是对的: "**起一个进程、读它的 stdout**"与对方是什么二进制无关。

    命令里**只用单引号** —— 整条要塞进 Loment 的双引号字符串字面量, 而 `go.exe` 的路径带空格
    必须引起来。这不是技巧, 是这条腿的真实约束 (Python / Perl / Lua / Java 的都一样)。

    退出码 5 = `len(strings.Join([]string{"a","b","c"}, ","))` = `"a,b,c"` 的长度。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    go = "/mnt/c/Program Files/Go/bin/go.exe"
    if not _wsl_ok(f"test -x '{go}'"):
        print("      SKIP: 主机上没有 Go")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "m.go").write_text(
            'package main\n'
            'import ("fmt"; "strings")\n'
            'func main() {\n'
            '    fmt.Println(len(strings.Join([]string{"a", "b", "c"}, ",")))\n'
            '}\n', encoding="utf-8", newline="\n")
        r = subprocess.run([*_WSLCMD, "bash", "-lc",
                            f"rm -rf {_T}ffigo && mkdir -p {_T}ffigo && "
                            f"cp {_wsl_path(td)}/m.go {_T}ffigo/"],
                           capture_output=True, text=True, timeout=120, shell=False)
        assert r.returncode == 0, f"拷 .go 失败: {r.stderr[-200:]}"
        _bridge_case(f"cd {_T}ffigo && '{go}' run m.go", 5, "Go 桥")
        print("      Go: go run + strings 包 (WSL interop) -> 退出码 5")


@test
def test_multi_object_cross_reference():
    """**多目标文件**: `a.o` 调 `b.o` —— 跨对象重定位必须解开。

    这是 `docs/173` 阶段 2 的正题, 也是"真实 C 库"与"手工挑出来的单函数玩具"的分界线:
    真实库就是多目标文件、成员互相引用、每个成员都带重定位。

    退出码 22 = `c_add(3,4)` = 7 + `c_sub(20,5)` = 15。`c_sub` 只存在于 `b.o`,
    所以 `a.o` 里那条 `R_X86_64_PLT32` 只能靠"**先摆好所有对象再建符号表**"才解得出 ——
    先解后摆的话 `c_sub` 还是 UND。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "b.c").write_text(C_SUB_SOURCE, encoding="utf-8", newline="\n")
        (td / "a.c").write_text(C_CALLER_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "a.c", td / "a.o") == 0, "C 编译失败"
        assert compile_c(clang, td / "b.c", td / "b.o") == 0, "C 编译失败"
        # 先确认这条判据真的踩在重定位上 —— 否则它测的是别的东西
        fo = lomelf.ForeignObject(td / "a.o")
        assert fo.relocs, "a.o 里没有重定位, 这条判据没测到该测的东西"
        assert "c_sub" in fo.undefined, f"a.o 没把 c_sub 记成未定义: {fo.undefined}"
        (td / "m.lomt").write_text(LOMENT_MULTI_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "a.o", td / "b.o"], 22)
        assert rc == 22, f"跨对象引用结果不对: rc={rc} (期望 22) err={err[-300:]!r}"
        print("      多目标文件 (a.o 调 b.o, 跨对象重定位) -> 退出码 22")


#: **两个成员各有同名局部符号**。`static` 的东西在目标文件里是 `bind=0` 的**局部**符号，
#: 名字就是程序员写的那个（`slot`）—— 两个文件里都叫 `slot`。zlib 那边是编译器生成的
#: `.LC0` / `.my_version` 一类，同一回事。
C_LOCAL_A = """
static int slot = 11;
int get_a(void) { slot = slot + 1; return slot; }
"""
C_LOCAL_B = """
static int slot = 21;
int get_b(void) { slot = slot + 1; return slot; }
"""

LOMENT_LOCAL_COLLIDE = """module collide
choose runtime

extern fn get_a() -> i32;
extern fn get_b() -> i32;

fn _start() {
    // 各自 ++ 自己的 slot：12 与 22。**两个都对**才退 7 —— 串了的话第二个会读到
    // 第一个的槽（老病"先来的为准"），于是要么 3、要么 4。
    if get_a() != 12 { syscall4(60, 3, 0, 0); }
    if get_b() != 22 { syscall4(60, 4, 0, 0); }
    syscall4(60, 7, 0, 0);
}
"""


@test
def test_local_symbols_do_not_collide_across_members():
    """**局部符号必须按对象区分**（2026-10-15，`FujoJTOP/loment#359`）。

    标签表是**按名字**查的、而且"先来的为准" —— 而 `.LC0` / `.L.str` 这种**局部**名字
    每个成员里都有。不分对象的话，第二个成员的 `.LC0` 会解析到**第一个成员的** `.LC0` 上：
    **静默指错地址**，不报任何错。修法是给局部符号加对象前缀（`docs/219` §8b 记了很久的
    "某个成员的数据/重定位还有残留缺陷"，真凶就是这个 —— 与 S1f 修过的**节符号**同一类，
    只是当时没顺手修普通局部符号）。

    `b.o` **放在前面**：那正是触发顺序（先来的定义赢）。夹具刻意让两个字符串长度相同、
    内容不同 —— 撞上时读到的是**另一个成员**的字节，于是能分辨"指错"与"没读"。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "a.c").write_text(C_LOCAL_A, encoding="utf-8", newline="\n")
        (td / "b.c").write_text(C_LOCAL_B, encoding="utf-8", newline="\n")
        # `-O0`：`-O1` 会把那个 `static` 直接折成常量，于是**根本不留具名局部符号** ——
        # 那样这条判据就是空的（实测：第一版夹具正是这样，关掉修也绿）。
        assert compile_c(clang, td / "a.c", td / "a.o", ("-O0",)) == 0, "C 编译失败"
        assert compile_c(clang, td / "b.c", td / "b.o", ("-O0",)) == 0, "C 编译失败"
        # **先确认这两个成员真的有同名局部符号** —— 否则这条判据测的是别的东西。
        # 比的是**去掉按对象前缀之后**的名字：修好之后登记的名字长成 `$<对象>$<原名>`，
        # 取最后一个 `$` 之后那段就是原名的样子（没修时整串就是原名，所以这条检查两边都成立）。
        fa = lomelf.ForeignObject(td / "a.o")
        fb = lomelf.ForeignObject(td / "b.o")
        raw = lambda s: {n.rsplit("$", 1)[-1] for n in s}      # noqa: E731
        shared = raw(fa.dsyms) & raw(fb.dsyms)
        assert shared, f"两个成员没有同名局部符号，这条判据空跑了: {sorted(fa.dsyms)[:4]}"
        # 名字级：**标签表里不许再同名**（同名 ⇒ 后登记的那个会被"先来的为准"挡掉）。
        assert not (set(fa.dsyms) & set(fb.dsyms)), (
            f"两个成员的局部符号在标签表里还是同名: {sorted(set(fa.dsyms) & set(fb.dsyms))[:4]}")
        (td / "m.lomt").write_text(LOMENT_LOCAL_COLLIDE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "b.o", td / "a.o"], 7)
        assert rc == 7, (f"两个成员的局部常量串了（**先来的为准**那条老病）：rc={rc}，"
                         f"期望 7（3 = 第一个成员读错，4 = 第二个读错）{err[-200:]!r}")
        print(f"      两个成员的同名局部符号（{sorted(shared)[:2]}…）各自解对 -> 退出码 7")


#: **进入 C 函数时 `rsp % 16` 必须是 8**（SysV：`call` 那一刻 rsp ≡ 0，`call` 压入返回地址
#: 之后就是 8）。我们自己的调用约定实参走栈、帧不对齐，所以调用点必须**显式对齐**。
C_ALIGN = """
unsigned long entry_align(void) {
    unsigned long sp;
    __asm__ volatile("mov %%rsp, %0" : "=r"(sp));
    return sp % 16;
}
"""

LOMENT_ALIGN = """module align
choose runtime

extern fn entry_align() -> u64;

fn _start() {
    syscall4(60, entry_align(), 0, 0);
}
"""


@test
def test_extern_call_sites_are_sixteen_byte_aligned():
    """**调 C 之前 `rsp` 要按 SysV 对齐**（2026-10-15，`FujoJTOP/loment#359`）。

    不修的话，**任何用到 16 字节 SSE 存的 C 函数当场 `#GP`**（`si_code=SI_KERNEL`、
    `si_addr=NULL` —— 那不是段越界，是**对不齐**）。实测：zlib 的 `compress` 就死在
    `movaps %xmm0,-0x70(%rbp)` 上；`deflateInit` 那条路偏偏又是好的，所以症状看起来
    "时好时坏"。**判据直接量那个不变量**：让 C 侧把进入时的 `rsp % 16` 报出来，
    它必须是 **8**（= 退出码 8）。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "al.c").write_text(C_ALIGN, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "al.c", td / "al.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_ALIGN, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "al.o"], 8)
        assert rc == 8, (f"C 侧看到的栈对齐是 {rc}（mod 16），SysV 要求 **8** —— "
                         f"调用点没对齐，任何 `movaps` 到栈的库函数都会 #GP "
                         f"{err[-200:]!r}")
        print("      C 侧报进入时 rsp % 16 == 8（SysV 要的那个数）-> 退出码 8")


@test
def test_archive_selects_only_needed_members():
    """**静态库 `.a`**: 只挑定义"当前需要的符号"的成员, **不整包收**。

    退出码 22, 与上一条同一个程序, 只是把 `a.o`/`b.o` 打成了归档。

    判据的关键是那个 **`junk.o`**: 它定义 `unused_junk`（谁也不需要）、却**引用了 `printf`**。
    - 整包收的实现会把它拖进来, 于是"引用了未定义符号" → **整个库被拒**;
    - 固定点选择只收 `a.o`(定义 c_add) → 它需要 c_sub → 再收 `b.o` → 停。`junk.o` 从不进。
    真实库里几乎总有那么一两个成员带无关的 libc 依赖, 所以这条差别就是"能用/不能用"。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    # 打包器用 **llvm-ar** (与 clang 同一套, Windows 上就有) —— 不用 `ar`, 免得判据
    # 在 Windows 上静默 SKIP (第一版就是那么写的, 结果它在最该跑的那台机器上没跑)。
    ar = shutil.which("llvm-ar") or _llvm_bin("llvm-ar.exe") or shutil.which("ar")
    if not ar:
        print("      SKIP: 没有 llvm-ar/ar")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "a.c").write_text(
            "extern int c_sub(int, int);\n"
            "__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }\n",
            encoding="utf-8", newline="\n")
        (td / "b.c").write_text("int c_sub(int a, int b) { return a - b; }\n",
                                encoding="utf-8", newline="\n")
        (td / "junk.c").write_text(
            "extern int printf(const char *, ...);\n"
            "int unused_junk(void) { return printf(\"never called\"); }\n",
            encoding="utf-8", newline="\n")
        for stem in ("a", "b", "junk"):
            assert compile_c(clang, td / f"{stem}.c", td / f"{stem}.o") == 0, "C 编译失败"
        lib = td / "libdemo.a"
        r = subprocess.run([ar, "rcs", str(lib), str(td / "a.o"), str(td / "b.o"),
                            str(td / "junk.o")], capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"ar 失败: {r.stderr[-200:]}"
        # 先钉住"固定点只挑该挑的" —— 不比这条, 整包收也能跑出 22, 判据就没意义了
        picked = {o.name for o in lomelf.Archive(lib).select({"c_add"})}
        assert picked == {"a.o/", "b.o/"}, f"归档挑错了成员: {picked}"
        (td / "m.lomt").write_text(
            "module arch\n\n"
            "extern fn c_add(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [lib], 22)
        assert rc == 22, f"归档链接结果不对: rc={rc} (期望 22) err={err[-300:]!r}"
        print("      归档 .a: 固定点只挑 a.o+b.o, 含 printf 的 junk.o 没被拖进来 -> 22")


@test
def test_object_with_relocations_is_rejected():
    """**仍然硬的边界**: 不认识的重定位类型 -> 硬拒。

    上面两条把重定位从"一律拒"放开到了"认识的那几种", 但**不认识的一律拒**这条没变 ——
    猜一个偏移的后果是"跳到错地址", 那是运行期崩溃而不是编译期报错。

    另外仍然拒的还有: 重定位指向**非代码节** (`.data`/`.rodata`) —— 本档不摆那些节。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # 造一个假的重定位类型: 改掉 .rela.text 里 r_info 的低 32 位
        (td / "c.c").write_text(
            "extern int c_sub(int, int);\n"
            "__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }\n",
            encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0
        raw = bytearray((td / "c.o").read_bytes())
        fo = lomelf.ForeignObject(td / "c.o")
        assert fo.relocs, "夹具没生成重定位"
        # 找 .rela.text 并把它第一条的 type 改成 0x7f (不认识的)
        import struct as _s
        e_shoff, = _s.unpack_from("<Q", raw, 40)
        sent, shnum = _s.unpack_from("<HH", raw, 58)
        for k in range(shnum):
            o = e_shoff + k * sent
            typ, = _s.unpack_from("<I", raw, o + 4)
            if typ != 4:
                continue
            off, = _s.unpack_from("<Q", raw, o + 24)
            _s.pack_into("<I", raw, off + 8, 0x7F)      # r_info 低 32 位 = 类型
            break
        (td / "bad.o").write_bytes(bytes(raw))
        try:
            lomelf.ForeignObject(td / "bad.o")
        except lomelf.Unsupported as e:
            assert "重定位" in str(e), e
        else:
            raise AssertionError("不认识的重定位类型应当被拒")
        print("      不认识的重定位类型: 硬拒")


#: **收编面**（`docs/219` §6）的夹具：这份 C 要 `memset` / `memcpy` / `memcmp` 三个
#: **未定义符号** —— 在此之前这是"链接期硬拒"（就是下面那条判据），现在是**由 Loment
#: 自己写的那三个函数**满足。三个都用上，因为"三条各自都能收"才是那条主张。
C_CONSCRIPT_SOURCE = """\
extern void *memcpy(void *, const void *, unsigned long);
extern void *memset(void *, int, unsigned long);
extern int memcmp(const void *, const void *, unsigned long);

int c_fill_compare(char *d, char *s, unsigned long n) {
    memset(s, 65, n);                 /* s = "AAAA" */
    memcpy(d, s, n);                  /* d = "AAAA" */
    if (memcmp(d, s, n) != 0) return 7;
    return (int)(unsigned char)d[0] + (int)(unsigned char)d[3];   /* 65 + 65 */
}
"""

#: 调上面那份 C 的 Loment 程序。退出码 = `c_fill_compare` 的返回值 = **130**。
LOMENT_CONSCRIPT_SOURCE = """\
module conscript
choose runtime

extern fn c_fill_compare(d: ptr, s: ptr, n: u64) -> i32;

fn _start() {
    let d: ptr = alloc(16 as u32);
    let s: ptr = alloc(16 as u32);
    let r: i32 = c_fill_compare(d, s, 4 as u64);
    syscall4(60, r as u64, 0, 0);
}
"""


@test
def test_object_using_memcpy_is_conscripted_and_runs():
    """**收编面第一刀**（`docs/219` §6）：外部 C 对象要的 `memcpy`/`memset`/`memcmp`，
    由 **Loment 自己写的那三个函数**提供 —— 它们于是从端口表上被**划掉**，而不是
    "链一个系统 libc 进来"。

    判据是**跑出来的数**，不是"IR 里有个定义"：`memset` 把 `s` 填成 `'A'`(65)、
    `memcpy` 拷到 `d`、`memcmp` 判等，返回 `d[0] + d[3]` = 65 + 65 = **130**。
    任何一环错都到不了这个数：`memcpy` 不发则 `d` 是垃圾、`memset` 错则 `d[0]` 不是 65、
    `memcmp` 误报不等则退 7。

    **它同时是证伪对**：把 `Emitter.emit_conscript` 里 `memcpy` 那段删掉，链接期就会
    重新报"引用了未定义的符号 memcpy"（就是下面那条判据的形状）。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "cons.c").write_text(C_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "cons.c", td / "cons.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "cons.o"], 130)
        assert rc == 130, f"收编面结果不对: rc={rc} (期望 130) err={err[-300:]!r}"
        print("      收编面: memset/memcpy/memcmp 由 Loment 提供 -> 退出码 130")


#: **`docs/219` §8 的 C2 夹具**：C 侧的 `free` 要还的是 **Loment `alloc` 出来的块** ——
#: 同一条空闲链。Loment 先 `alloc` 一块、交给 C；C 写一个字节，然后**用 C 的 `free`**
#: 把它还掉；Loment 再要一次同尺寸 —— 只有拿回**同一个地址**才算"同一个堆"。
C_MALLOC_SOURCE = """\
extern void free(void *);

int c_put(char *p) { p[0] = 42; free(p); return p[0]; }
int c_same(char *a, char *b) { return a == b; }
"""

#: 退出码 = `(r - 42) * 1000 + 7 + c_same(b, b2) * 35` = **42**（两项都对时）。
LOMENT_MALLOC_SOURCE = """\
module consp3
choose runtime

extern fn c_put(p: ptr) -> i32;
extern fn c_same(a: ptr, b: ptr) -> i32;

fn _start() {
    let b: ptr = alloc(8 as u32);
    let r: i32 = c_put(b);
    let b2: ptr = alloc(8 as u32);
    let code: i32 = (r - 42) * 1000 + 7 + c_same(b, b2) * 35;
    syscall4(60, code as u64, 0, 0);
}
"""


@test
def test_malloc_and_free_are_loments_own_heap():
    """**`docs/219` §8 C2**（"堆**真是**我们的"，含反面）：C 世界的 `malloc`/`free`
    不是另一份分配器，是 `@__loment_alloc` / `@__loment_free` 的**换 ABI 薄壳**。

    判据要能分辨"同一个堆"与"另一个堆"，所以**不是**"C 能 malloc 出东西"（那太弱：
    另起一份分配器也成立），而是**地址同一**：Loment `alloc` 一块 → C 写一个字节并用
    **C 的 `free`** 还掉 → Loment 再要一次同尺寸，**必须拿回同一个地址**。
    第一块在 bump 前沿上，前沿回退把它退回去 —— 只有 `free` 真接到了我们的空闲链，
    第二次才会命中同一个地址。

    **反面（这条判据的证伪）**：把 `free` 那一格从收编面里去掉（或让壳不接 `@__loment_free`），
    地址就对不上 —— 退出码从 42 变成 7。C2 在文档里就是带这个反面的。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "m.c").write_text(C_MALLOC_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "m.c", td / "m.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_MALLOC_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "m.o"], 42)
        assert rc == 42, f"C2 不对: rc={rc} (期望 42; 7 = free 没接到我们的堆) err={err[-300:]!r}"
        print("      C2: C 的 free 还回 Loment 的空闲链 -> 再 alloc 命中同一地址 (rc=42)")


#: **数据节**的夹具（`docs/219` §8b 第 1 条）：一份**真实 C 库的形状** —— 字符串常量
#: 住在 `.rodata.str1.1`，一张 `const char *const` 表住在 `.data.rel.ro`。
#: 两条都要：前者考"数据节摆不摆"，后者考"**落在数据节上的重定位**算不算"。
C_DATA_SOURCE = """\
int c_pick(int i) {
    static const char *const names[] = {"ab", "cd"};
    return names[i][1];
}
"""

LOMENT_DATA_SOURCE = """\
module consp4
choose runtime

extern fn c_pick(i: i32) -> i32;

fn _start() {
    syscall4(60, c_pick(1 as i32) as u64, 0, 0);
}
"""


@test
def test_object_with_rodata_and_data_relocations_runs():
    """**真实 C 库的门槛**（`docs/219` §8b 第 1 条，S2 的前置）：数据节。

    在此之前这一步**连读都读不进来** —— `.rodata` 从来没被摆过，于是代码里那条
    `lea rdi, [rip + .L.str]` 以"引用了未定义的符号 .L.str"的面目报出来（一句"不支持的节"
    都看不到）。而**真实 C 库的字符串常量、常量表都在这里**，所以这是 zlib 那一类库的门槛。

    判据取 `names[1][1]` = `"cd"` 的第 2 个字节 = `'d'` = **100**：它同时要求
    **数据节被摆对**（`"cd"` 那 3 个字节）**而且** `.data.rel.ro` 里那条**指向它的重定位**
    算对（`names[1]` 才是 `"cd"` 的地址）。任一条错都到不了 100。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "d.c").write_text(C_DATA_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "d.c", td / "d.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_DATA_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "d.o"], 100)
        assert rc == 100, f"数据节结果不对: rc={rc} (期望 100) err={err[-300:]!r}"
        print("      数据节: .rodata 字符串 + .data.rel.ro 表 -> rc=100")


#: **栈金丝雀**的夹具（`docs/219` S2）：`-fstack-protector-all` 会插 `fs:0x28` 的读写。
#: `volatile` 是为了**别被优化掉** —— 没有它，`-O1` 会把整个数组消掉、金丝雀也就不插了
#: （实测：不写 `volatile` 时对象里一次 `fs` 读取都没有）。
C_CANARY_SOURCE = """\
int c_canary(int x) { volatile char b[64]; b[0] = (char)x; b[63] = 0; return b[0]; }
"""

#: **`memcpy`/`memset` 的返回值**：C 里它们返回 `void*`（就是目标指针），调用方**会用它**。
C_RETVAL_SOURCE = """\
extern void *memset(void *, int, unsigned long);
extern void *memcpy(void *, const void *, unsigned long);

int c_retval(void) {
    char a[8], b[8];
    char *p = (char *)memset(a, 7, 8);   /* p 必须是 a */
    char *q = (char *)memcpy(b, p, 8);   /* q 必须是 b */
    return q[0] + p[7];                  /* 7 + 7 */
}
"""


@test
def test_stack_protector_object_runs_because_we_give_it_tls():
    """**TLS**（`docs/219` S2）：`-fstack-protector` 编出来的库会读 **`fs:0x28`** 取金丝雀，
    而我们没有 TLS 时 `fs` 是 0 —— 那是去读地址 `0x28`，**一进去就段错误**。

    所以带外部对象时，产物里放一小块 TLS，入口用 `arch_prctl(ARCH_SET_FS)` 把 `fs` 指过来。
    金丝雀不需要是随机数：入口存一次、出口比一次，中间没人改，永远相等。

    这条夹具**特意用 `-fstack-protector-all` 强制插金丝雀**（普通 `-strong` 在小函数上
    根本不插，实测过）—— 没有 TLS 时这条会以**段错误**收场，而不是算错。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "can.c").write_text(C_CANARY_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "can.c", td / "can.o",
                         ("-fstack-protector-all",)) == 0, "C 编译失败"
        fo = lomelf.load_foreign(td / "can.o")
        assert "__stack_chk_fail" in fo.undefined, "夹具没插金丝雀 (判据自己坏了)"
        (td / "m.lomt").write_text(
            "module canary\n\nextern fn c_canary(x: i32) -> i32;\n\n"
            "fn _start() {\n    syscall4(60, c_canary(6 as i32) as u64, 0, 0);\n}\n",
            encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "can.o"], 6)
        assert rc == 6, f"带金丝雀的对象: rc={rc} (期望 6; 139 = 没有 TLS) err={err[-300:]!r}"
        print("      栈金丝雀: -fstack-protector-all 的对象链上就跑通 (rc=6, 有 TLS)")


@test
def test_memcpy_and_memset_return_the_destination():
    """C 的 `memcpy`/`memset` **返回 `void*`**（目标指针），调用方会拿它。第一版收编面
    没把原起点放回 `rax`（循环把 `rdi` 推到了末尾），于是 `p = memset(...)` 拿到垃圾。

    判据取 `q[0] + p[7]` = 7 + 7 = **14**：两个返回值都得对。返回垃圾时要么算错、要么崩。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "r.c").write_text(C_RETVAL_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "r.c", td / "r.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(
            "module retval\n\nextern fn c_retval() -> i32;\n\n"
            "fn _start() {\n    syscall4(60, c_retval() as u64, 0, 0);\n}\n",
            encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "r.o"], 14)
        assert rc == 14, f"返回值不对: rc={rc} (期望 14) err={err[-300:]!r}"
        print("      memcpy/memset 的返回值: p/q 都是目标指针 -> rc=14")


@test
def test_object_with_undefined_symbol_is_rejected():
    """一个符号**到链接结束都没人提供** (典型是 libc) -> **硬拒**, 不猜。

    **判定点从"读对象时"挪到了"链接结束时"**（阶段 2）：未定义符号本身**不再是错误** ——
    它可能由**另一个对象**提供, 那正是多目标文件 C 库的形状。所以这条给一个没人提供的符号,
    断言它在**链接期**被拒。留住这条是为了钉住边界仍然硬: 拒掉, 而不是猜一个地址。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text("extern int not_defined_anywhere(int);\n"
                                "int c_wrap(int x) { return not_defined_anywhere(x); }\n",
                                encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0
        (td / "m.lomt").write_text(
            "module undef\n\n"
            "extern fn c_wrap(x: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, c_wrap(1 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "m.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "m.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        try:
            # `allow_ports=True`：**必须**过这一关才测得到本判据要测的东西。不传的话先撞上
            # 端口门（`docs/222` §4），这条就变成了"测那扇门"、而不是"测没人提供的符号被拒"
            # —— 那种"因为别的原因而绿"正是最该避免的。
            lomelf.compile_ll(ir, [lomelf.ForeignObject(td / "c.o")], allow_ports=True)
        except lomelf.Unsupported as e:
            assert "未定义" in str(e), e
        else:
            raise AssertionError("引用没人提供的符号应当被拒")
        print("      没人提供的符号: 链接期硬拒 (第 2 阶段仍不链 libc)")


@test
def test_the_port_gate_refuses_by_default_and_the_dev_channel_opens_it():
    """**端口门**（`docs/222` §4，2026-10-12 用户定）：带**未定义符号**的外源对象默认被拒 ——
    这就是"0.1.4.5 里 libc 不可调用"在**链接器**这一侧的落点（另一侧是编译器那条渠道闸：
    发行版不收 `choose hosted`）。

    **两端都要测**：只测"拒"的话，一个"什么外源对象都拒"的链接器也能绿；只测"收"的话，
    门什么时候被悄悄拆掉谁也不知道。夹具用**收编面**那一份 —— 它的未定义符号就是
    `memcpy`/`memset`/`memcmp` 三个，正是真 C 库要吃的名字。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "cons.c").write_text(C_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "cons.c", td / "cons.o") == 0
        (td / "m.lomt").write_text(LOMENT_CONSCRIPT_SOURCE, encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "m.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "m.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        fo = lomelf.load_foreign(td / "cons.o")
        assert set(fo.undefined) >= {"memcpy"}, fo.undefined
        try:
            lomelf.compile_ll(ir, [fo])
        except lomelf.Unsupported as e:
            assert "端口" in str(e), str(e)
        else:
            raise AssertionError(
                "带端口的对象**默认竟然收下了** —— 发行版里 libc 就是可调用的了"
                "（`docs/222` §4 那扇门被拆了）")
        blob, _info = lomelf.compile_ll(ir, [fo], allow_ports=True)
        assert len(blob) > 0
        print("      默认拒（发行版）/ `allow_ports` 收（开发通道）—— 两端都核过")


@test
def test_a_selfclosed_object_is_untouched_by_the_port_gate():
    """那扇门只关 libc：**自闭合**的外源对象（一条未定义符号都没有）两档都收。

    这条是"锁过头"的守卫 —— `docs/173` 一直在发的 FFI 形状、`docs/177` 那 10 个语言
    全建立在这上面。门若把自闭合对象也拒掉，"锁 libc"就变成了"砍掉 FFI"。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0
        (td / "m.lomt").write_text(LOMENT_SOURCE, encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "m.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "m.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        fo = lomelf.load_foreign(td / "c.o")
        assert fo.undefined == [], fo.undefined
        blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [fo])
        assert len(blob) > 0
        print("      自闭合对象不带开关也照样链 —— 门没有锁过头")


@test
def test_link_is_elf_only():
    """`--link` 只支持 ELF 目标 —— PE 侧的 FFI 还没做, 要**明说**而不是忽略参数。"""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"),
                        "in.ll", "--target", "pe", "--link", "x.o"],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 2 and "只支持 ELF" in r.stderr, (r.returncode, r.stderr[-200:])
    print("      --link 配 PE: 明确拒绝")


@test
def test_program_without_extern_is_unchanged():
    """**反向的一条**: 没有 `extern` 的程序一个字都不该变。

    新增 `parse_ll` 的第三个返回值 / 新增 `Emitter.externs` 都可能顺手改变老程序的行为,
    这条钉住"老路径不动": 同一份 IR, 带不带 `--link` 之外的一切照旧。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        ir = ("define void @_start() {\n"
              "entry:\n"
              "  %t1 = call i64 asm sideeffect \"syscall\", "
              "\"={ax},{ax},{di},{si},{dx}\"(i64 60, i64 7, i64 0, i64 0)\n"
              "  ret void\n"
              "}\n")
        (td / "a.ll").write_text(ir, encoding="utf-8", newline="\n")
        blob, info = lomelf.compile_ll(ir)
        assert info["linked"] == [] and info["objects"] == [], info
        assert len(blob) > 0
        print("      无 extern 的程序: 路径照旧")


# ---------------------------------------------------------------- 真静态库 (V1)

#: `docs/183` §4 **V1**（静态 libc）的验收格：真 **`libz.a`** 经**我们自己的链接器**进来，
#: `compress → uncompress → 原文` 逐字节对。
#:
#: 逻辑与 `loment_hosted_test.HOSTED_SRC` 是**同一份**（那一份走 `choose hosted` + 系统
#: `libz.so`、由 C 工具链链；这一份走 `sealed` + `libz.a`、由 `lomelf` 链），失败码也对齐：
#: 10 compress / 20 uncompress / 30 解压后的长度 / 40 内容 / 50 "压缩居然没变小"。
#: 唯一的结构差别是入口（那份是 hosted 的 `main`，这份是我们自己链接器的 `_start`）——
#: **两条判据合起来才是那句话**："能调真库"在**两个方向**上都成立。
ZLIB_STATIC_SRC = """\
module zlibstatic
choose runtime

extern fn compress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;
extern fn uncompress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;

const NSRC: u32 = 64;

fn store32(p: ptr, off: u32, v: u32) {
    store8(p, off, v as u8);
    store8(p, off + 1, ((v / 256) % 256) as u8);
    store8(p, off + 2, ((v / 65536) % 256) as u8);
    store8(p, off + 3, ((v / 16777216) % 256) as u8);
}

fn load32(p: ptr, off: u32) -> u32 {
    return load8(p, off)
        + load8(p, off + 1) * 256
        + load8(p, off + 2) * 65536
        + load8(p, off + 3) * 16777216;
}

fn _start() {
    let src: ptr = alloc(256);
    let clen: ptr = alloc(16);
    let comp: ptr = alloc(1024);
    let olen: ptr = alloc(16);
    let out: ptr = alloc(256);
    let i: u32 = 0;
    while i < NSRC {
        store8(src, i, ((i % 8) + 65) as u8);
        i = i + 1;
    }
    store32(clen, 0, 1024);
    if compress(comp, clen, src, NSRC as u64) != 0 { syscall4(60, 10, 0, 0); }
    let ncomp: u32 = load32(clen, 0);
    store32(olen, 0, 256);
    if uncompress(out, olen, comp, ncomp as u64) != 0 { syscall4(60, 20, 0, 0); }
    if load32(olen, 0) != NSRC { syscall4(60, 30, 0, 0); }
    let j: u32 = 0;
    while j < NSRC {
        if load8(out, j) != load8(src, j) { syscall4(60, 40, 0, 0); }
        j = j + 1;
    }
    if ncomp >= NSRC { syscall4(60, 50, 0, 0); }
    syscall4(60, 0, 0, 0);
}
"""

#: **证伪用的那一份**：只把逐字节比对的方向写错（拿 `out[j]` 与 `src[j] + 1` 比）。
#: 若上面那条判据真的在测"算对了"，这一份就**必须**报 40；它要是也 rc=0，说明那条判据测的
#: 是"能跑"而不是"算对"（`loment_hosted_test` 那一份的证伪是同一条道理）。
ZLIB_STATIC_FALSIFIER_SRC = ZLIB_STATIC_SRC.replace(
    "if load8(out, j) != load8(src, j) { syscall4(60, 40, 0, 0); }",
    "if load8(out, j) != ((load8(src, j) + 1) % 256) { syscall4(60, 40, 0, 0); }")
assert ZLIB_STATIC_FALSIFIER_SRC != ZLIB_STATIC_SRC, "证伪件没改到东西"

#: 系统静态 zlib 的几个常见落点（发行版布局不同）。**没有就 SKIP** —— 这不是"判据放宽"：
#: 缺 `libz.a` 的机器上这一格**测不了**，而"测不了"与"过了"必须分开说。
LIBZ_CANDIDATES = ("/usr/lib/x86_64-linux-gnu/libz.a", "/usr/lib64/libz.a",
                   "/usr/lib/libz.a", "/usr/lib/x86_64-linux-gnu/libz.a.orig")


def _system_libz(dst: Path) -> Path | None:
    """把系统那份 `libz.a` 取到**仓库里**一份（两侧按**同一条相对路径**读它），取不到返回 None。

    **取法分两条腿** —— `#371` 起这一族判据在 Linux 上是**原生**跑的（`_WSLCMD` 为空、
    `_wsl_path` 不加 `/mnt/` 前缀），而 runner 上**没有 `wsl` 这个命令**：

      * **这一侧读得到**（Linux：`/usr/lib/...` 就在本机）⇒ **直接读**。对这一侧来说"拷贝"
        是多余的一步，而多余的那一步正是会坏的那一步 —— 实测：写死 `subprocess.run(["wsl", …])`
        在 runner 上直接 `FileNotFoundError`，判据红得**像回归**，其实只是没走平台那两条腿；
      * **读不到**（Windows：它在 WSL 里）⇒ 从另一侧 `cat` 回来，**不是 `cp`** ——
        WSL 往 `/mnt/*` 写、这一侧立刻读会读到**空的**（9p 的可见性延迟，`docs/` 里记过）。

    取回来**校验魔数**（`!<arch>\\n`），不是只看长度：半个归档会让判据在错的东西上跑，
    而它红起来长得和"真缺陷"一模一样。
    """
    data: bytes | None = None
    for cand in LIBZ_CANDIDATES:
        try:
            p = Path(cand)
            if p.is_file():
                b = p.read_bytes()
                if b[:8] == b"!<arch>\n":
                    data = b
                    break
        except OSError:
            pass
    if data is None:
        for cand in LIBZ_CANDIDATES:
            try:
                r = subprocess.run([*_WSLCMD, "bash", "-lc", f"cat {cand}"],
                                   capture_output=True, timeout=300, shell=False)
            except OSError:            # 另一条腿不存在（没装 WSL）：**取不到**，不是挂掉
                return None
            if r.returncode == 0 and r.stdout[:8] == b"!<arch>\n":
                data = r.stdout
                break
    if data is None:
        return None
    dst.write_bytes(data)
    return dst if dst.read_bytes()[:8] == b"!<arch>\n" else None


@test
def test_a_real_static_library_round_trips():
    """真 `libz.a` 经**我们自己的链接器**：`compress → uncompress → 原文`。

    **Why**：这是 `docs/183` §4 的 **V1** 验收格，也是 `docs/219` §8b 那条线一直盯着的
    "第一个真库"。它与 `loment_hosted_test` 那条**看着像**，其实是**另一个方向**：那条走
    `choose hosted`（产物由 C 工具链链、库是 `.so`、堆问宿主要），这条走 `sealed`（产物由
    `lomelf` 自己链、库是 `.a`、堆**封在产物里**）。两条都绿才叫"能调真库在两个方向上都成立"。

    **它测的是插件面之外的东西**：真库是个 **`ar` 归档**（16 个成员，成员之间互相引用），
    所以这条判据同时钉住**固定点成员选择**（不整包收 —— 整包收会把 `gz*` 那些成员的
    `fopen`/`printf` 一起拖进来，整个库就被端口门拒了）与 arena 够不够大（`deflateInit`
    要 256–512 KiB，见 `lomentc.FFI_ARENA_BYTES`）。

    **证伪**：见 `ZLIB_STATIC_FALSIFIER_SRC` —— 比对方向写反的那一份必须报 40。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        ark = _system_libz(ROOT / "loment" / "build" / "_libz_probe.a")
        if ark is None:
            print("      SKIP: 这台机器上没有 libz.a（要 zlib1g-dev）—— 这一格测不了")
            return
        try:
            # 先量"这条判据真的踩在归档上" —— 不然它测的是别的什么
            fo = lomelf.load_foreign(ark)
            n_mem = len(fo.members)
            assert n_mem >= 8, f"libz.a 只有 {n_mem} 个成员？这不是真库"
            for tag, src, want in (("roundtrip", ZLIB_STATIC_SRC, 0),
                                   ("falsifier", ZLIB_STATIC_FALSIFIER_SRC, 40)):
                (td / f"{tag}.lomt").write_text(src, encoding="utf-8", newline="\n")
                rc, err = build_and_run(td, td / f"{tag}.lomt", [ark], want)
                assert rc == want, (
                    f"[{tag}] rc={rc}（期望 {want}）err={err[-300:]!r}")
            # 选出来的是**该要的那几个成员**，不是整包（整包会被端口门拒 —— 那正是
            # "不整包收"存在的理由）
            picked = lomelf.Archive(ark).select({"compress", "uncompress"})
            names = [o.name for o in picked]
            assert len(picked) < n_mem, f"把整个归档都收进来了: {n_mem} 个成员"
            assert any("compress" in n or "deflate" in n for n in names), names
            print(f"      真 libz.a（{n_mem} 个成员）→ 挑出 {len(picked)} 个 → "
                  f"compress/uncompress 逐字节回原文 (rc=0)；证伪件报 40")
        finally:
            ark.unlink(missing_ok=True)


# ---------------------------------------------------------------- V2：运行期绑定的共享对象

#: 系统**共享** zlib 的几个常见落点（V2 读 `.so`，V1 读同名的 `.a`）。
#: `/usr/lib/.../libz.so` 在 Debian 系是链接脚本（ASCII），下面按 **ET_DYN** 校验会把它滤掉。
LIBZ_SO_CANDIDATES = ("/usr/lib/x86_64-linux-gnu/libz.so.1", "/usr/lib64/libz.so.1",
                      "/usr/lib/libz.so.1", "/usr/lib/x86_64-linux-gnu/libz.so")


def _system_so(dst: Path, candidates: tuple[str, ...]) -> Path | None:
    """把系统的一份 `.so` 取到仓库里，取不到返回 None。两条腿与 `_system_libz` 同源。

    **校验 `\\x7fELF` + `e_type == ET_DYN`**（不是只看前缀）：`libz.so` 这种名字在
    Debian 系可能是链接脚本，半个 `.so` 会让判据在错的东西上跑，而它红起来与"真缺陷"
    长得一样。
    """
    def ok(b: bytes) -> bool:
        return (len(b) > 20 and b[:4] == b"\x7fELF"
                and struct.unpack_from("<H", b, 16)[0] == 3)

    data: bytes | None = None
    for cand in candidates:
        try:
            p = Path(cand)
            if p.is_file():
                b = p.read_bytes()
                if ok(b):
                    data = b
                    break
        except OSError:
            pass
    if data is None:
        for cand in candidates:
            try:
                r = subprocess.run([*_WSLCMD, "bash", "-lc", f"cat {cand}"],
                                   capture_output=True, timeout=300, shell=False)
            except OSError:            # 另一条腿不存在（没装 WSL）：**取不到**，不是挂掉
                return None
            if r.returncode == 0 and ok(r.stdout):
                data = r.stdout
                break
    if data is None:
        return None
    dst.write_bytes(data)
    return dst if ok(dst.read_bytes()) else None


def _dynamic_facts(blob: bytes) -> dict:
    """从**我们自己产出**的 ELF 里读动态链接那几样 —— 没有节头，只能走程序头 + `.dynamic`。"""
    e_phoff, = struct.unpack_from("<Q", blob, 32)
    e_phnum, = struct.unpack_from("<H", blob, 56)
    loads: list[tuple[int, int, int]] = []
    dyn: tuple[int, int] | None = None
    interp_at = None
    for k in range(e_phnum):
        o = e_phoff + k * 56
        pt, _fl, poff, pva, _pa, pfsz, _ms, _al = struct.unpack_from("<IIQQQQQQ", blob, o)
        if pt == 1:
            loads.append((pva, poff, pfsz))
        elif pt == 2:
            dyn = (poff, pfsz)
        elif pt == 3:
            interp_at = (poff, pfsz)
    assert dyn is not None, "产物没有 PT_DYNAMIC —— 这不是动态链接的产物"

    def foff(va: int) -> int:
        for v, o, sz in loads:
            if v <= va < v + sz:
                return o + (va - v)
        raise AssertionError(f"动态段里的地址 0x{va:x} 不在任何 PT_LOAD 里")

    entries: dict[int, int] = {}
    o, end = dyn[0], dyn[0] + dyn[1]
    while o + 16 <= end:
        tag, val = struct.unpack_from("<QQ", blob, o)
        o += 16
        if tag == 0:
            break
        entries.setdefault(tag, val)
    strtab = foff(entries[5])                      # DT_STRTAB
    needed: list[str] = []
    o = dyn[0]
    while o + 16 <= end:
        tag, val = struct.unpack_from("<QQ", blob, o)
        o += 16
        if tag == 0:
            break
        if tag == 1:                               # DT_NEEDED
            e = blob.index(b"\x00", strtab + val)
            needed.append(blob[strtab + val:e].decode())
    interp = None
    if interp_at is not None:
        e = blob.index(b"\x00", interp_at[0])
        interp = blob[interp_at[0]:e].decode()
    return {"interp": interp, "needed": needed, "dyn": entries}


def _build_dynamic(td: Path, src: str, so: Path) -> tuple[bytes, dict]:
    """Loment 源码 -> IR -> (**带一个 `.so` 的**) ELF。开发通道那条路，所以 `allow_ports`。"""
    p = td / "v2.lomt"
    p.write_text(src, encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    errs = lomentc.check(mod)
    assert not errs, errs
    ir = lomentc.emit_llvm(mod, ROOT)
    return lomelf.compile_ll(ir, [lomelf.load_foreign(so)], allow_ports=True)


def _run_elf(td: Path, blob: bytes) -> tuple[int, str]:
    exe = td / "v2.elf"
    exe.write_bytes(blob)
    r = subprocess.run([*_WSLCMD, "bash", "-lc",
                        f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                       capture_output=True, text=True, timeout=120, shell=False)
    return r.returncode, r.stderr


@test
def test_a_real_shared_library_is_bound_at_run_time():
    """`docs/183` §4 的 **V2**（#360）：链系统 `libz.so`（**不链** `libz.a`），跑回原文。

    **"库真的被用上了"必须单独证明** —— 静态链也能让行为判据变绿，那样这条就什么都没说。
    所以这里同时量三件静态链做不到的事：

      * 产物里有 `PT_INTERP`，`DT_NEEDED` 里写着 `libz.so.1`（静态产物两样都没有）；
      * 产物里**没有 zlib 的报错串**（`invalid distance too far back` / `incorrect header
        check`）—— 那些字节只在库里；搬进产物才会有（V1 那份链 `libz.a` 的产物有，正是
        这两条判据合起来的意思）；
      * 体积：动态产物 < 64 KiB（V1 那份是 200 KiB 量级）。

    **三条失败模式都必须是命名错误，不是运行期崩**：缺 `.so`、库里没有这个名字、名字只以
    **非默认版本**导出 / 是**数据**符号。前两条对着 `libz.so` 量；后两条对着 `libc.so.6`
    量 —— libz 没有非默认版本导出、也没有真的数据导出（这一点本身也是量出来的，不是假设）。

    **端口门**：发行通道（`allow_ports=False`）必须拒收动态依赖 —— 这是 `docs/219` S3。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL（或 Linux）跑 ELF")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        so = _system_so(ROOT / "loment" / "build" / "_libz_probe.so.1", LIBZ_SO_CANDIDATES)
        if so is None:
            print("      SKIP: 这台机器上没有 libz.so（要 zlib1g）—— 这一格测不了")
            return
        try:
            blob = b""
            for tag, src, want in (("roundtrip", ZLIB_STATIC_SRC, 0),
                                   ("falsifier", ZLIB_STATIC_FALSIFIER_SRC, 40)):
                blob, info = _build_dynamic(td, src, so)
                assert info["shared"] == ["libz.so.1"], info
                assert info["imports"] == ["compress", "uncompress"], info
                rc, err = _run_elf(td, blob)
                assert rc == want, f"[{tag}] rc={rc}（期望 {want}）err={err[-300:]!r}"

            facts = _dynamic_facts(blob)
            assert facts["interp"] == "/lib64/ld-linux-x86-64.so.2", facts["interp"]
            assert facts["needed"] == ["libz.so.1"], facts["needed"]
            # 没有把库的代码搬进来：zlib 的报错串在库里，不在我们的产物里。
            lib = so.read_bytes()
            for needle in (b"invalid distance too far back", b"incorrect header check"):
                assert needle in lib, f"取回来的 libz.so 里没有 {needle!r}？取错文件了"
                assert needle not in blob, (
                    f"产物里出现了 zlib 的 {needle!r} —— 库的 .rodata 被搬进来了，"
                    f"那就不是动态链接")
            assert len(blob) < 64 * 1024, f"动态产物 {len(blob)} 字节，太大 —— 像是静态链的"

            # ---- 失败模式一：缺 `.so`（打不开 = 命名错误，不是崩）
            try:
                lomelf.load_foreign(td / "no-such-library.so.1")
                raise AssertionError("缺共享对象居然没报错")
            except lomelf.Unsupported as e:
                assert "打不开" in str(e), str(e)

            # ---- 失败模式二：库里没有这个名字（**走完整条链**，不是只调 resolve）
            missing = ZLIB_STATIC_SRC.replace(
                "extern fn compress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;",
                "extern fn loment_not_exported(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;",
                1).replace(
                "if compress(comp, clen, src, NSRC as u64) != 0",
                "if loment_not_exported(comp, clen, src, NSRC as u64) != 0", 1)
            assert missing != ZLIB_STATIC_SRC and "loment_not_exported" in missing
            try:
                _build_dynamic(td, missing, so)
                raise AssertionError("库里没有的符号居然链过去了")
            except lomelf.Unsupported as e:
                assert "没有导出" in str(e), str(e)

            # ---- 失败模式三/四：非默认版本、数据符号（对着 libc.so.6 量）
            libc = _system_so(td / "libc.so.6", _LIB6_CANDIDATES)
            if libc is None:
                print("      （这台上没有 libc.so.6：版本/数据符号那两条失败模式测不了）")
            else:
                probe = lomelf.load_foreign(libc)
                try:
                    probe.resolve("xdecrypt")          # 只以 GLIBC_2.2.5（非默认）导出
                    raise AssertionError("非默认版本的导出居然能绑")
                except lomelf.Unsupported as e:
                    assert "非默认版本" in str(e) and "GLIBC_" in str(e), str(e)
                try:
                    probe.resolve("stdin")             # 数据符号：要 GOT 间接，这一版不做
                    raise AssertionError("数据符号居然能绑")
                except lomelf.Unsupported as e:
                    assert "数据" in str(e), str(e)

            # ---- 端口门：发行通道（默认）必须拒收
            p = td / "v2.lomt"
            mod = lomentc.load(p)
            ir = lomentc.emit_llvm(mod, ROOT)
            try:
                lomelf.compile_ll(ir, [lomelf.load_foreign(so)], allow_ports=False)
                raise AssertionError("发行通道收下了一个通向世界的端口")
            except lomelf.Unsupported as e:
                assert "--allow-ports" in str(e), str(e)

            print(f"      真 libz.so（{len(lib)} B）→ DT_NEEDED=libz.so.1、产物 {len(blob)} B、"
                  f"compress/uncompress 逐字节回原文 (rc=0)；证伪件 40；"
                  f"四条失败模式都是命名错误；发行通道拒收")
        finally:
            so.unlink(missing_ok=True)


#: `libc.so.6` 的几个常见落点（只有"失败模式"那两条用它 —— 那是**符号表**的样本）。
_LIB6_CANDIDATES = ("/usr/lib/x86_64-linux-gnu/libc.so.6", "/usr/lib64/libc.so.6",
                    "/lib/x86_64-linux-gnu/libc.so.6", "/usr/lib/libc.so.6")


def main() -> int:
    failed = []
    for fn in TESTS:

        try:
            p = Path(cand)
            if p.is_file():
                b = p.read_bytes()
                if b[:8] == b"!<arch>\n":
                    data = b
                    break
        except OSError:
            pass
    if data is None:
        for cand in LIBZ_CANDIDATES:
            try:
                r = subprocess.run([*_WSLCMD, "bash", "-lc", f"cat {cand}"],
                                   capture_output=True, timeout=300, shell=False)
            except OSError:            # 另一条腿不存在（没装 WSL）：**取不到**，不是挂掉
                return None
            if r.returncode == 0 and r.stdout[:8] == b"!<arch>\n":
                data = r.stdout
                break
    if data is None:
        return None
    dst.write_bytes(data)
    return dst if dst.read_bytes()[:8] == b"!<arch>\n" else None


@test
def test_a_real_static_library_round_trips():
    """真 `libz.a` 经**我们自己的链接器**：`compress → uncompress → 原文`。

    **Why**：这是 `docs/183` §4 的 **V1** 验收格，也是 `docs/219` §8b 那条线一直盯着的
    "第一个真库"。它与 `loment_hosted_test` 那条**看着像**，其实是**另一个方向**：那条走
    `choose hosted`（产物由 C 工具链链、库是 `.so`、堆问宿主要），这条走 `sealed`（产物由
    `lomelf` 自己链、库是 `.a`、堆**封在产物里**）。两条都绿才叫"能调真库在两个方向上都成立"。

    **它测的是插件面之外的东西**：真库是个 **`ar` 归档**（16 个成员，成员之间互相引用），
    所以这条判据同时钉住**固定点成员选择**（不整包收 —— 整包收会把 `gz*` 那些成员的
    `fopen`/`printf` 一起拖进来，整个库就被端口门拒了）与 arena 够不够大（`deflateInit`
    要 256–512 KiB，见 `lomentc.FFI_ARENA_BYTES`）。

    **证伪**：见 `ZLIB_STATIC_FALSIFIER_SRC` —— 比对方向写反的那一份必须报 40。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        ark = _system_libz(ROOT / "loment" / "build" / "_libz_probe.a")
        if ark is None:
            print("      SKIP: 这台机器上没有 libz.a（要 zlib1g-dev）—— 这一格测不了")
            return
        try:
            # 先量"这条判据真的踩在归档上" —— 不然它测的是别的什么
            fo = lomelf.load_foreign(ark)
            n_mem = len(fo.members)
            assert n_mem >= 8, f"libz.a 只有 {n_mem} 个成员？这不是真库"
            for tag, src, want in (("roundtrip", ZLIB_STATIC_SRC, 0),
                                   ("falsifier", ZLIB_STATIC_FALSIFIER_SRC, 40)):
                (td / f"{tag}.lomt").write_text(src, encoding="utf-8", newline="\n")
                rc, err = build_and_run(td, td / f"{tag}.lomt", [ark], want)
                assert rc == want, (
                    f"[{tag}] rc={rc}（期望 {want}）err={err[-300:]!r}")
            # 选出来的是**该要的那几个成员**，不是整包（整包会被端口门拒 —— 那正是
            # "不整包收"存在的理由）
            picked = lomelf.Archive(ark).select({"compress", "uncompress"})
            names = [o.name for o in picked]
            assert len(picked) < n_mem, f"把整个归档都收进来了: {n_mem} 个成员"
            assert any("compress" in n or "deflate" in n for n in names), names
            print(f"      真 libz.a（{n_mem} 个成员）→ 挑出 {len(picked)} 个 → "
                  f"compress/uncompress 逐字节回原文 (rc=0)；证伪件报 40")
        finally:
            ark.unlink(missing_ok=True)


def main() -> int:
    return loment_suite.run(TESTS, "loment_ffi_test")


if __name__ == "__main__":
    sys.exit(main())
