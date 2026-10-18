#!/usr/bin/env python3
"""`tools/loment_ports.py` 的判据 —— `docs/219` §8 那几条里**现在就能判**的那些。

判的是三件事, 每一件都有一个**证伪对**(把实现弄坏, 它必须红):

1. **分类对**: `memcpy` 判 `close` 于是**仍算纯**(它是我们能闭合的), `time` 判 `world`
   于是**非纯**。证伪: 把 `PROVIDE` 里的 `memcpy` 删掉 -> 第一条红; 把 `time` 从
   `WORLD` 删掉 -> 第二条红。
2. **藏边抓得到**(§8 C3 的两个对):
   * `dlsym` 必须落 `refused` —— 它是按名解析, 端口闭包在它那儿失去完备性;
   * **内联 `syscall`** 必须把对象标成 `poisoned` —— 符号表里根本没有那个名字, 只有
     字节扫描抓得到。证伪: 删掉 `0f 05` 那一趟扫描 -> 第二条红。
3. **闭包不整包收**(§6): 一个归档里那个**没人用到**的成员带着 `dlsym`, 从真正的根算
   闭包时它**不许**出现在表上 —— 而"整包最大端口集"那一档里**必须**有它。两条一起
   才说明固定点真的在跑。证伪: 把 `closure` 的固定点改成"整包收" -> 第一条红。

外加一条**交叉判据**(尺子存在的理由): 在链接器读得进来的对象上, 尺子数出的端口集
必须与 `lomelf.ForeignObject` 数出的**逐条相同**。两条读法分叉的地方就是 bug 的藏身处
—— 写这份尺子的过程中它已经抓到过一次(局部节符号 `.L.str` 被当成端口)。

平台: 需要 clang (交叉编出 x86_64 ELF 目标文件)。缺则 SKIP —— 与 `loment_ffi_test` 同一条。
**不需要 WSL**: 这里全是**静态**分析, 不跑 ELF。
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomelf                                                       # noqa: E402
import loment_ports                                                 # noqa: E402
import lomentc                                                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

SOURCES = {
    "pure": "int p_add(int a, int b) { return a + b; }\n",
    "closable": "extern void *memcpy(void *, const void *, unsigned long);\n"
                "int cp(char *d, const char *s, unsigned long n) { memcpy(d, s, n); return 0; }\n",
    "world": "extern long time(long *);\nlong stamp(void) { return time(0); }\n",
    "refused": 'extern void *dlsym(void *, const char *);\n'
               'void *lk(void *h) { return dlsym(h, "time"); }\n',
    # 内联 `syscall` —— 符号表看不见它, 只有 `0f 05` 字节扫描抓得到 (docs/219 §5.2 条件一)
    "rawsyscall": 'long rw(int fd, const char *b, long n) { long r;\n'
                  '  __asm__ volatile("syscall" : "=a"(r) : "a"(1L), "D"(fd), "S"(b), "d"(n)\n'
                  '                   : "rcx", "r11", "memory");\n'
                  '  return r; }\n',
    # 函数指针表: 那条边落在 `.data.rel.ro` 的重定位里 (docs/219 §8 C7)
    "datatbl": "extern void ext(void);\n"
               "void (*const tbl[1])(void) = { ext };\n"
               "void call_it(int i) { tbl[i](); }\n",
}
ARCHIVE = {
    "a": "extern int b_sub(int, int);\nextern long time(long *);\n"
         "int a_go(int x) { return b_sub(x, 1) + (int)time(0); }\n",
    "b": "int b_sub(int a, int b) { return a - b; }\n",
    "junk": 'extern void *dlsym(void *, const char *);\n'
            'void *junk(void *h) { return dlsym(h, "x"); }\n',
}


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        if Path(c).exists():
            return c
        if shutil.which(c):
            return c
    return None


def _ar() -> str | None:
    for c in (r"C:\Program Files\LLVM\bin\llvm-ar.exe", "llvm-ar", "ar"):
        if Path(c).exists():
            return c
        if shutil.which(c):
            return c
    return None


def _cc(clang: str, src: Path, obj: Path) -> None:
    r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                        "-ffreestanding", "-fno-stack-protector", "-o", str(obj), str(src)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"C 编译失败: {r.stderr[-300:]}"


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


class _Fix:
    """一次编译好的夹具集。没有 clang 时 `skip` 为真, 判据直接印 SKIP。"""

    def __init__(self):
        self.skip = None
        self.objs: dict[str, Path] = {}
        self.ar: Path | None = None

    def build(self) -> "_Fix":
        clang, ar = _clang(), _ar()
        if not clang or not ar:
            self.skip = "需要 clang + llvm-ar/ar"
            return self
        self.skip = None
        self._td = tempfile.TemporaryDirectory()      # 夹具**不进仓库**(不与发布清单的 glob 争)
        td = Path(self._td.name)
        for name, src in SOURCES.items():
            (td / f"{name}.c").write_text(src, encoding="utf-8", newline="\n")
            obj = td / f"{name}.o"
            _cc(clang, td / f"{name}.c", obj)
            self.objs[name] = obj
        members = []
        for name, src in ARCHIVE.items():
            (td / f"{name}.c").write_text(src, encoding="utf-8", newline="\n")
            obj = td / f"{name}.o"
            _cc(clang, td / f"{name}.c", obj)
            members.append(obj)
        self.ar = td / "libdemo.a"
        r = subprocess.run([ar, "rcs", str(self.ar), *[str(m) for m in members]],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"归档失败: {r.stderr[-200:]}"
        return self


_FIX = _Fix()


def _report(name: str) -> dict:
    return loment_ports.ports(loment_ports.read_any(_FIX.objs[name]))


def _classes(report: dict) -> dict:
    return {e["name"]: e["class"] for e in report["ports"]}


@test
def test_pure_object_has_an_empty_table():
    """一个只做算术的目标文件: **零端口**, 判纯。这就是 `docs/205:44` 那条判据的原样。"""
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("pure")
    assert r["ports"] == [], r["ports"]
    assert loment_ports.pure(r)
    print("      pure.o: 端口表 0 条 -> 纯 (docs/205:44 的『无 U』)")


@test
def test_closable_symbol_is_not_a_port():
    """`memcpy` 在表上, 但它的类是 `close` —— **可闭合的符号不算端口**。

    这是整个名词的支点 (`docs/219` §6 第 2 条): 划掉的不是"未定义符号", 是"通向世界的边"。
    判据必须让两者**不同**: 端口表非空, 而 `pure()` 仍然为真。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("closable")
    assert _classes(r) == {"memcpy": "close"}, _classes(r)
    assert loment_ports.pure(r), "可闭合的符号不该让它变非纯"
    print("      closable.o: memcpy 判 close -> 表非空但**仍纯**")


@test
def test_world_symbol_makes_it_impure():
    """`time` 判 `world` -> 非纯。证伪: 把 `time` 从 `WORLD` 删掉, 这条红。"""
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("world")
    assert _classes(r) == {"time": "world"}, _classes(r)
    assert not loment_ports.pure(r)
    print("      world.o: time 判 world -> 非纯")


@test
def test_name_resolution_is_refused():
    """**证伪对 ①** (`docs/219` §8 C3): 按名解析必须落 `refused`, 不许被当成纯。

    `dlsym` 拿的是**字符串** —— 闭包在它那儿失去完备性 (§5.2 条件一)。它同时带着一个
    字符串常量, 所以这条顺带钉住"局部节符号不许被当成端口"那个已修过的 bug:
    `refused.o` 的表上**只许有 `dlsym` 一条**。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("refused")
    assert _classes(r) == {"dlsym": "refused"}, _classes(r)
    assert not loment_ports.pure(r)
    print("      refused.o: dlsym 判 refused (且 .L.str 没被误当端口)")


@test
def test_inline_syscall_has_no_name_and_must_be_caught_by_bytes():
    """**证伪对 ②** (`docs/219` §8 C3): 内联 `syscall` 在符号表里**没有名字**。

    它的端口表是**空的** —— 如果只信符号, 它会冒充一个纯对象。所以字节扫描 (`0f 05`)
    必须把它标脏。证伪: 删掉那趟扫描, 这条红。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("rawsyscall")
    assert r["ports"] == [], r["ports"]           # 符号表**确实**看不见它
    assert r["poisoned"], "0f 05 扫描没命中"
    assert not loment_ports.pure(r), "被毒化的对象不许判纯"
    print("      rawsyscall.o: 表为空但 0f 05 命中 -> 非纯 (符号表看不见它)")


@test
def test_closure_does_not_take_the_whole_archive():
    """**闭包不整包收** (`docs/219` §6; `lomelf.Archive.select` 同一条)。

    归档里那个**没人用到**的成员带着 `dlsym`。从真正的根 (`a_go`) 算, 表上**只许有
    `time`**; 而"整包最大端口集"那一档里**必须有 `dlsym`**。两条一起才说明固定点在跑。
    证伪: 把固定点换成整包收, 第一条红。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    objs = loment_ports.read_any(_FIX.ar)
    from_roots = _classes(loment_ports.ports(objs, {"a_go"}))
    whole = _classes(loment_ports.ports(objs))
    assert from_roots == {"time": "world"}, from_roots
    assert whole.get("dlsym") == "refused", whole
    print(f"      归档: 从根 a_go -> {sorted(from_roots)} (junk.o 没被拖进来)")
    print(f"      归档: 整包最大 -> {sorted(whole)}")


@test
def test_function_pointer_table_edge_is_visible():
    """**§8 C7**: 那条边落在 `.data.rel.ro` 的重定位里, 不在代码节。

    两件事都要成立, 而且**要分开报**:
      * 尺子**看得见** `ext` 这条端口(它读全部重定位节);
      * 链接器**摆不了**这个对象(`ForeignObject` 对指向非代码节的重定位硬拒)——
        所以"能不能链"与"端口表是什么"是两列, 不许混成一件。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    r = _report("datatbl")
    assert "ext" in _classes(r), _classes(r)
    assert r["linker_cannot_place"], "链接器摆不了这件事没被报出来"
    print(f"      datatbl.o: 端口 {sorted(_classes(r))} 可见; 且链接器今天摆不了")


@test
def test_ruler_agrees_with_the_linker_on_every_object_the_linker_can_read():
    """**交叉判据** —— 尺子存在的理由。

    在 `ForeignObject` 读得进来的对象上, 尺子的端口集必须与它**逐条相同**。
    两条读法的分歧就是 bug 的藏身处: 写这份尺子时它抓到过一次(重定位那条路把
    clang 给字符串常量的局部节符号 `.L.str` 当成了端口)。
    """
    if _FIX.skip:
        print(f"      SKIP: {_FIX.skip}")
        return
    checked = 0
    for name, path in _FIX.objs.items():
        try:
            fo = lomelf.load_foreign(path)
        except lomelf.Unsupported:
            continue                       # 链接器读不进来 -> 这条判据管不着(另一列在报)
        mine = set(_classes(_report(name)))
        assert mine == set(fo.undefined), f"{name}: 尺子 {sorted(mine)} != 链接器 {fo.undefined}"
        checked += 1
    assert checked >= 4, f"只对上了 {checked} 份, 太少"
    print(f"      尺子与 lomelf 在 {checked} 份对象上逐条相同")


@test
def test_the_linkers_conscript_surface_is_inside_the_rulers_provide_set():
    """**两半表不许分叉**（`docs/219` §5.2 条件二："名字的另一端在我们手里"）。

    表有两半：**尺子**判一个名字 `close`（`loment_ports.PROVIDE`），**链接器**真的把它
    发出来（`lomelf.CONSCRIPT`）。链接器那一半必须是尺子那一半的**子集** —— 否则尺子会说
    "这条端口可闭合"，而链接器提供不出来，产物在链接期报"未定义的符号"，
    **与表上的话正好相反**。

    反过来**不要求**：表里还有一批尚未实现的（`malloc` / `strlen` / `__udivdi3` …），
    那是 S1c 的活。这条判据**不需要 clang**，所以它不会 SKIP。
    """
    have = set(lomelf.CONSCRIPT)
    missing = sorted(have - loment_ports.PROVIDE)
    assert not missing, (
        f"链接器发出了尺子不认识的名字：{missing} —— 把它们加进 `loment_ports.PROVIDE`，"
        f"否则表会说'可闭合'而链接器在那条上发不出来")
    print(f"      收编面两半一致：链接器发出 {len(have)} 个名字，"
          f"都在尺子的 PROVIDE（{len(loment_ports.PROVIDE)} 个）里")
    print(f"      表上尚未实现（S1c 的活）："
          f"{sorted(loment_ports.PROVIDE - have)[:6]} …")


# ---------------------------------------------------------------- 手搭 ELF / ar
#
# 下面几条判据要的形状 clang 摆不出来（`SHN_ABS` / `SHN_COMMON` / `SHN_XINDEX` /
# 局部函数符号 / 同一个符号的强弱两份），所以这里只搭 `ForeignObject` 读的那几节。
# **不是**一份通用 ELF 工具：只覆盖判据用到的节类型与 `.rela*`。

_SHT_PROGBITS, _SHT_SYMTAB, _SHT_RELA, _SHT_NOBITS, _SHT_SYMTAB_SHNDX = 1, 2, 4, 8, 18
_SHF_ALLOC, _SHF_WRITE, _SHF_EXEC = 0x2, 0x1, 0x4
_SHN_ABS, _SHN_COMMON = 0xFFF1, 0xFFF2


def _elf_obj(*, code=((".text", b"\xC3"),), data=b"", rodata=b"", bss=0,
             syms=(), relocs=(), xindex=False) -> bytes:
    """`syms` 项 = (名字, bind, type, shndx, value, size)。

    `shndx` 是 int / 节名 / `("xindex", 节名)`（后者写 0xffff 并填伴随表）。
    `relocs` 项 = (目标节名, 节内偏移, 类型, 符号下标, 加数)，下标从 0 起。
    """
    secs: list[dict] = []
    byname: dict[str, int] = {}

    def add(nm, typ, flags, blob=b"", *, link=0, info=0, align=8, entsize=0, size=None):
        idx = len(secs)
        secs.append({"name": nm, "type": typ, "flags": flags, "blob": blob,
                     "link": link, "info": info, "align": align, "entsize": entsize,
                     "size": len(blob) if size is None else size, "nameoff": 0})
        byname[nm] = idx
        return idx

    add("", 0, 0, align=1)
    for nm, blob in code:
        add(nm, _SHT_PROGBITS, _SHF_ALLOC | _SHF_EXEC, blob, align=16)
    if data:
        add(".data", _SHT_PROGBITS, _SHF_ALLOC | _SHF_WRITE, data)
    if rodata:
        add(".rodata", _SHT_PROGBITS, _SHF_ALLOC, rodata)
    if bss:
        add(".bss", _SHT_NOBITS, _SHF_ALLOC | _SHF_WRITE, size=bss)
    strtab = add(".strtab", _SHT_PROGBITS, 0, align=1)
    symtab = add(".symtab", _SHT_SYMTAB, 0, align=8, entsize=24,
                 link=strtab, info=1)
    xi = add(".symtab_shndx", _SHT_SYMTAB_SHNDX, 0, align=4, entsize=4) \
        if xindex else None
    for target in sorted({r[0] for r in relocs}):
        add(".rela" + target, _SHT_RELA, 0, align=8, entsize=24,
            link=symtab, info=byname[target])
    shstr = add(".shstrtab", _SHT_PROGBITS, 0, align=1)

    st = bytearray(b"\x00")
    noff: dict[str, int] = {}
    for s in syms:
        noff[s[0]] = len(st)
        st += s[0].encode() + b"\x00"
    secs[strtab]["blob"] = bytes(st)
    secs[strtab]["size"] = len(st)
    sd = bytearray(24)                                  # 0 号是保留的空符号
    xd = bytearray(struct.pack("<I", 0))
    for nm, bind, typ, shndx, value, size in syms:
        if isinstance(shndx, tuple):
            real, emit = byname[shndx[1]], 0xFFFF
        else:
            real = byname[shndx] if isinstance(shndx, str) else shndx
            emit = real
        sd += struct.pack("<IBBHQQ", noff[nm], (bind << 4) | typ, 0, emit, value, size)
        xd += struct.pack("<I", real)
    secs[symtab]["blob"] = bytes(sd)
    secs[symtab]["size"] = len(sd)
    if xi is not None:
        secs[xi]["blob"] = bytes(xd)
        secs[xi]["size"] = len(xd)
        secs[xi]["link"] = symtab
    groups: dict[str, list] = {}
    for target, off, ty, si, addend in relocs:
        groups.setdefault(target, []).append((off, ((si + 1) << 32) | ty, addend))
    for target, items in groups.items():
        blob = b"".join(struct.pack("<QQq", o, inf, a) for o, inf, a in items)
        secs[byname[".rela" + target]]["blob"] = blob
        secs[byname[".rela" + target]]["size"] = len(blob)
    sh = bytearray(b"\x00")
    for s in secs:
        s["nameoff"] = len(sh)
        sh += s["name"].encode() + b"\x00"
    secs[shstr]["blob"] = bytes(sh)
    secs[shstr]["size"] = len(sh)

    out = bytearray(64)
    for s in secs:
        if s["type"] == _SHT_NOBITS:
            s["off"] = len(out)
            continue
        while len(out) % s["align"]:
            out.append(0)
        s["off"] = len(out)
        out += s["blob"]
    while len(out) % 8:
        out.append(0)
    shoff = len(out)
    for s in secs:
        out += struct.pack("<IIQQQQIIQQ", s["nameoff"], s["type"], s["flags"], 0,
                           s["off"], s["size"], s["link"], s["info"],
                           s["align"], s["entsize"])
    out[0:16] = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    struct.pack_into("<HHIQQQIHHHHHH", out, 16, 1, 0x3E, 1, 0, 0, shoff, 0,
                     64, 0, 0, 64, len(secs), shstr)
    return bytes(out)


def _make_ar(members) -> bytes:
    """`members` = [(名字, 字节)]。经典 `ar` 头，够 `Archive` 读。"""
    out = bytearray(b"!<arch>\n")
    for nm, data in members:
        out += f"{nm:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        out += data
        if len(data) % 2:
            out.append(0)
    return bytes(out)


def _load_obj(td: Path, name: str, blob: bytes):
    p = td / name
    p.write_bytes(blob)
    return lomelf.load_foreign(p)


def _load_ar(td: Path, name: str, members):
    p = td / name
    p.write_bytes(_make_ar(members))
    return lomelf.load_foreign(p)


def _fake_so(*exports: str, soname: str = "libfake.so"):
    """只带**导出表**的共享对象 —— `compile_ll` 从 `.so` 那侧只读这些。

    判据不依赖系统里有没有某个真库（那是 `loment_ffi_test` 的活），也不要 clang
    去出一个跨平台链接得起来的 `.so`。属性与 `SharedObject.__init__` 的产物同名。
    """
    so = object.__new__(lomelf.SharedObject)
    so.path, so.name, so.raw = Path(soname), soname, b""
    so.soname = soname
    so.exports = {nm: ("func", True) for nm in exports}
    so.versions = {}
    return so


_IR_START0 = "define i32 @_start() {\nentry:\n  ret i32 0\n}\n"


def _ir_calls(name: str, *, ret: bool = True) -> str:
    body = f"  %r = call i32 @{name}()\n  ret i32 %r\n" if ret else \
           f"  call void @{name}()\n  ret i32 0\n"
    ty = "i32" if ret else "void"
    return f"declare {ty} @{name}()\n\ndefine i32 @_start() {{\nentry:\n{body}}}\n"


_TEXT_OFF = 0x1000                    # `build_elf` 里文本段的文件偏移


def _text(blob: bytes, n: int) -> bytes:
    return blob[_TEXT_OFF:_TEXT_OFF + n]


def _call_targets(blob: bytes, n: int) -> list[int]:
    """文本段里每条 `E8 rel32` 的**绝对**目标地址。"""
    t = _text(blob, n)
    out = []
    for i in range(len(t) - 4):
        if t[i] == 0xE8:
            rel = struct.unpack_from("<i", t, i + 1)[0]
            out.append(lomelf.ELF_TEXT_VADDR + i + 5 + rel)
    return out


@test
def test_reserved_section_indices_define_their_symbols():
    """**#619**：`SHN_ABS` / `SHN_COMMON` / `SHN_XINDEX` 都是**定义**，不是"没找到"。

    三条各一个对象：绝对值符号（`.set foo, 0x1234` 的形状）、`-fcommon` 的 COMMON
    符号（`st_value` 是**对齐**、`st_size` 是长度）、以及要走 `.symtab_shndx` 的
    XINDEX 符号。旧实现把它们全跳过去，于是重定位以"未定义的符号"报出来 —— 而对象
    自己明明定义了它。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # ① SHN_ABS：值就是绝对地址。
        fo = _load_obj(td, "abs.o", _elf_obj(
            code=((".text", bytes(8) + b"\xC3"),),
            syms=(("absval", 1, 1, _SHN_ABS, 0x1234, 8),),
            relocs=((".text", 0, lomelf.REL_64, 0, 0),)))
        assert fo.absyms == {"absval": 0x1234}, fo.absyms
        blob, _ = lomelf.compile_ll(_IR_START0, [fo])
        assert struct.pack("<Q", 0x1234) in blob
        # ② SHN_COMMON：链接器给它划存储（这里落在 DATA 段）。
        fo = _load_obj(td, "common.o", _elf_obj(
            code=((".text", bytes(8) + b"\xC3"),),
            syms=(("tbl", 1, 1, _SHN_COMMON, 8, 8),),
            relocs=((".text", 0, lomelf.REL_64, 0, 0),)))
        assert fo.dsyms.get("tbl") == 0, fo.dsyms
        assert len(fo.data) == 8, len(fo.data)
        blob, _ = lomelf.compile_ll(_IR_START0, [fo])
        assert struct.pack("<Q", lomelf.ELF_DATA_VADDR) in blob
        # ③ SHN_XINDEX：真实节号在伴随表里。
        fo = _load_obj(td, "xindex.o", _elf_obj(
            code=((".text", b"\xE8\x00\x00\x00\x00\xC3"),),
            syms=(("wide", 1, 2, ("xindex", ".text"), 0, 0),
                  ("f", 1, 2, ".text", 0, 0)),
            relocs=((".text", 1, lomelf.REL_PLT32, 0, -4),),
            xindex=True))
        assert fo.syms.get("wide") == 0, fo.syms
        lomelf.compile_ll(_ir_calls("f"), [fo])
        print("      ABS=0x1234 / COMMON 划了 8 字节 / XINDEX 经伴随表定到 .text")


@test
def test_archive_keeps_a_later_strong_definition_and_it_wins():
    """**#618** 上半：弱定义**不把名字闭合**，后面的强定义仍被收进来，且**强盖弱**。

    存档是 `w.o`（`f` 弱，返回 7）在前、`s.o`（`f` 强，返回 42）在后。旧实现两边都收
    但标签"先来的为准" ⇒ 调用落到弱实现。判据直接量**调用目标地址**：必须是 `s.o`
    的函数（`s.o` 是最后一个对象，基址 = 文本长度 − 它的长度）。
    """
    w_text = b"\xB8\x07\x00\x00\x00\xC3"        # mov eax, 7; ret
    s_text = b"\xB8\x2A\x00\x00\x00\xC3"        # mov eax, 42; ret
    w = _elf_obj(code=((".text", w_text),), syms=(("f", 2, 2, ".text", 0, 0),))
    s = _elf_obj(code=((".text", s_text),), syms=(("f", 1, 2, ".text", 0, 0),))
    with tempfile.TemporaryDirectory() as t:
        ark = _load_ar(Path(t), "libws.a", [("w.o", w), ("s.o", s)])
        blob, info = lomelf.compile_ll(_ir_calls("f"), [ark])
    assert info["objects"] == ["w.o", "s.o"], info["objects"]
    strong_at = lomelf.ELF_TEXT_VADDR + info["text"] - len(s_text)
    targets = _call_targets(blob, info["text"])
    assert strong_at in targets, (
        f"`f` 没指到强定义（期望 {strong_at:#x}，实际 {[hex(x) for x in targets]}）")
    print(f"      弱+强两份都收了; `f` 指向强定义 @ {strong_at:#x}")


@test
def test_two_strong_definitions_are_a_diagnostic():
    """**#618** 下半：两个**强**定义不再静默取第一个，而是链接期点名拒。"""
    one = _elf_obj(syms=(("f", 1, 2, ".text", 0, 0),))
    two = _elf_obj(syms=(("f", 1, 2, ".text", 0, 0),))
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        a = _load_obj(td, "one.o", one)
        b = _load_obj(td, "two.o", two)
        try:
            lomelf.compile_ll(_ir_calls("f"), [a, b])
        except lomelf.Unsupported as e:
            assert "重复定义" in str(e) and "f" in str(e), str(e)
        else:
            raise AssertionError("两个强定义被静默接受了")
    print("      两个强定义: 链接期点名拒")


@test
def test_a_weak_undefined_reference_binds_to_zero():
    """**#617**：弱未定义引用不是硬需求 —— 不进端口门、不拉归档，重定位写 0。

    对象里一个 `STB_WEAK` + `SHN_UNDEF` 的 `opt`，外加一条指向它的 `R_X86_64_64`。
    默认档（不开 `--allow-ports`）必须能链；被写进去的 8 字节必须是 0。
    """
    obj = _elf_obj(
        code=((".text", bytes(8) + b"\xC3"),),
        syms=(("opt", 2, 2, 0, 0, 0),),
        relocs=((".text", 0, lomelf.REL_64, 0, 0),))
    with tempfile.TemporaryDirectory() as t:
        fo = _load_obj(Path(t), "weak.o", obj)
    assert fo.undefined == [], fo.undefined
    assert fo.weak_undefined == ["opt"], fo.weak_undefined
    blob, info = lomelf.compile_ll(_IR_START0, [fo])      # 不开 allow_ports
    base = info["text"] - len(fo.text)
    assert _text(blob, info["text"])[base:base + 8] == b"\x00" * 8
    print("      弱未定义: 默认档可链, 重定位写 0")


@test
def test_archive_plus_shared_object_is_a_diagnostic_not_a_traceback():
    """**#616**：`.a` 与 `.so` 同时给时，先分流再展开 —— 不碰 `SharedObject.undefined`。

    旧实现把 `SharedObject` 留在 `direct` 里，展开归档时读 `o.undefined` ⇒
    `AttributeError`（用户看到 Python 栈）。现在归档成员照常挑、`.so` 只提供导出。
    """
    h = _elf_obj(syms=(("helper", 1, 2, ".text", 0, 0),))
    with tempfile.TemporaryDirectory() as t:
        ark = _load_ar(Path(t), "libh.a", [("h.o", h)])
        so = _fake_so("unused")
        blob, info = lomelf.compile_ll(_ir_calls("helper"), [ark, so],
                                       allow_ports=True)
        assert info["objects"] == ["h.o"], info
        assert info["shared"] == ["libfake.so"], info
        assert info["imports"] == [], info
        # 默认档仍然拒（共享对象那一条），而且是诊断不是炸栈。
        try:
            lomelf.compile_ll(_ir_calls("helper"), [ark, so])
        except lomelf.Unsupported as e:
            assert "端口" in str(e), str(e)
        else:
            raise AssertionError("带共享对象居然默认收下了")
    print("      归档 + 共享对象: 分流后正常；默认档仍是命名拒绝")


@test
def test_the_port_gate_only_counts_names_left_unresolved():
    """**#615**：门判"解析之后还剩什么"，不是每个对象自己的未定义清单。

    两只互相闭合的对象（`a.o` 定义 `f` 调 `g`、`b.o` 定义 `g`）必须默认可链；
    对象调收编面提供的 `memcpy` 也一样 —— 那个名字链接器自己会发出来。
    """
    a = _elf_obj(
        code=((".text", b"\xE8\x00\x00\x00\x00\xC3"),),
        syms=(("f", 1, 2, ".text", 0, 0), ("g", 1, 2, 0, 0, 0)),
        relocs=((".text", 1, lomelf.REL_PLT32, 1, -4),))
    b = _elf_obj(syms=(("g", 1, 2, ".text", 0, 0),))
    c = _elf_obj(
        code=((".text", b"\xE8\x00\x00\x00\x00\xC3"),),
        syms=(("c_fn", 1, 2, ".text", 0, 0), ("memcpy", 1, 2, 0, 0, 0)),
        relocs=((".text", 1, lomelf.REL_PLT32, 1, -4),))
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        fa = _load_obj(td, "a.o", a)
        fb = _load_obj(td, "b.o", b)
        fc = _load_obj(td, "c.o", c)
        lomelf.compile_ll(_ir_calls("f"), [fa, fb])          # 不开 allow_ports
        lomelf.compile_ll(_IR_START0, [fc])                   # 收编面提供 memcpy
    print("      闭合的多对象 + 收编面 memcpy: 默认档都不再误报端口")


@test
def test_archive_selection_sees_definitions_in_data_sections():
    """**#614**：成员把需要的符号定义在 `.rodata` 里也算"它提供了"。

    `b.o` 引用外部 `tbl`；`c.o` 把 `tbl` 定义在 `.rodata`（4 字节 `9`）。旧实现的
    挑选只看 `syms`（代码节），`c.o` 永远选不中。判据同时核数据段里真的是那 4 字节。
    """
    b = _elf_obj(
        code=((".text", bytes(8) + b"\xC3"),),
        syms=(("get", 1, 2, ".text", 0, 0), ("tbl", 1, 1, 0, 0, 0)),
        relocs=((".text", 0, lomelf.REL_64, 1, 0),))
    c = _elf_obj(code=((".text", b""),), rodata=b"\x09\x00\x00\x00",
                 syms=(("tbl", 1, 1, ".rodata", 0, 4),))
    with tempfile.TemporaryDirectory() as t:
        ark = _load_ar(Path(t), "libd.a", [("b.o", b), ("c.o", c)])
        blob, info = lomelf.compile_ll(_ir_calls("get"), [ark])
    assert info["objects"] == ["b.o", "c.o"], info["objects"]
    data_off = lomelf.align_up(_TEXT_OFF + info["text"], 0x1000)
    assert blob[data_off:data_off + 4] == b"\x09\x00\x00\x00"
    assert struct.pack("<Q", lomelf.ELF_DATA_VADDR) in _text(blob, info["text"])
    print("      归档: `.rodata` 里的 tbl 提供了符号 -> c.o 被选中，数据也摆对了")


@test
def test_local_function_symbols_are_registered():
    """**#613**：局部代码符号（`-ffunction-sections` 的 `static helper`）要能解析。

    `helper` 是 `STB_LOCAL`、住在 `.text.helper`；`f`（GLOBAL）在 `.text.f` 里调它。
    旧实现把局部符号改了名却**没登记**，于是解析失败、报 `$a.o$helper` 未定义；
    自举镜像则能链同一份对象。
    """
    obj = _elf_obj(
        code=((".text.helper", b"\xC3"),
              (".text.f", b"\xE8\x00\x00\x00\x00\xC3")),
        syms=(("helper", 0, 2, ".text.helper", 0, 0),
              ("f", 1, 2, ".text.f", 0, 0)),
        relocs=((".text.f", 1, lomelf.REL_PLT32, 0, -4),))
    with tempfile.TemporaryDirectory() as t:
        fo = _load_obj(Path(t), "a.o", obj)
    assert "$a.o$helper" in fo.syms, fo.syms
    lomelf.compile_ll(_ir_calls("f"), [fo])
    print("      局部函数符号: `$a.o$helper` 已登记, 对象可链")


@test
def test_an_object_can_call_into_a_linked_shared_object():
    """**#627**：`.o` 的重定位可以指向 `.so` 的导出 —— PLT 标签必须在应用重定位**之前**。

    `helper.o` 有一条未定义的 `libfunc`；`libfake.so` 导出它。旧实现先应用对象重定位、
    之后才建导入 ⇒ "引用了未定义的符号 libfunc"。判据核两件事：链得过去，且那条
    `call` 的落点是 16 对齐的 PLT 桩（不是 0，也不是对象自己的代码）。
    """
    obj = _elf_obj(
        code=((".text", b"\xE8\x00\x00\x00\x00\xC3"),),
        syms=(("f", 1, 2, ".text", 0, 0), ("libfunc", 1, 2, 0, 0, 0)),
        relocs=((".text", 1, lomelf.REL_PLT32, 1, -4),))
    with tempfile.TemporaryDirectory() as t:
        fo = _load_obj(Path(t), "helper.o", obj)
    blob, info = lomelf.compile_ll(_IR_START0, [fo, _fake_so("libfunc")],
                                   allow_ports=True)
    assert info["imports"] == ["libfunc"], info
    targets = _call_targets(blob, info["text"])
    assert any(x % 16 == 0 and x > lomelf.ELF_TEXT_VADDR for x in targets), targets
    print(f"      对象 -> .so: import={info['imports']}, 调用落到 PLT 桩")


@test
def test_a_call_without_declare_is_not_an_import():
    """**#630**：没有 `declare` 的调用**不许**变成 PLT 导入。

    它走的是 Loment 的栈约定（实参压栈），而 PLT 对面是 C ABI（实参在寄存器）——
    绑上去就是把"链接期错误"换成"运行期读到垃圾"。判据：带 `.so` 也必须报
    `未定义的标签`；同一份 IR 加上 `declare` 才正常导入。
    """
    no_decl = ("define i32 @_start() {\nentry:\n"
               "  %r = call i32 @bar()\n  ret i32 %r\n}\n")
    so = _fake_so("bar")
    try:
        lomelf.compile_ll(no_decl, [so], allow_ports=True)
    except (lomelf.ElfError, lomelf.Unsupported) as e:
        assert "bar" in str(e) and "未定义" in str(e), str(e)
    else:
        raise AssertionError("没 declare 的调用被绑成了 PLT 导入")
    blob, info = lomelf.compile_ll(_ir_calls("bar"), [so], allow_ports=True)
    assert info["imports"] == ["bar"], info
    print("      无 declare: 链接期报未定义标签；有 declare 才成为导入")


@test
def test_extern_call_sites_save_the_callers_rbx():
    """**#626**：extern 调用点的对齐序列要保存调用方的 `RBX`（SysV 被调方保存）。

    序列形如 `push rbx; mov rbx,rsp; and rsp,-16; call; mov rsp,rbx; pop rbx`。
    旧实现少了 push/pop：C 的帧里 `RBX` 被换成我们的栈指针 —— C → Loment 回调
    （zlib 的 `zalloc` / `qsort` 比较函数）再走一次 extern 调用时就会读到它。
    """
    blob, info = lomelf.compile_ll(_ir_calls("g", ret=False),
                                   [_fake_so("g")], allow_ports=True)
    t = _text(blob, info["text"])
    a = t.find(b"\x53\x48\x89\xE3")             # push rbx; mov rbx, rsp
    b = t.find(b"\x48\x89\xDC\x5B")             # mov rsp, rbx; pop rbx
    assert a >= 0 and b > a, (a, b)
    print("      extern 调用点: push rbx … pop rbx 包住了对齐序列")


def _heap_rounding(ir: str) -> tuple[int, int, int]:
    """从分配器 IR 里取出 (起始偏移, 加数, 掩码) —— 判据按这两条算载荷地址。"""
    off = int(re.search(r"@__loment_off = internal global i32 (\d+)", ir).group(1))
    add = int(re.search(r"%need0 = add i32 %size, (\d+)", ir).group(1))
    mask = int(re.search(r"%need = and i32 %need0, (-?\d+)", ir).group(1))
    return off, add, mask


@test
def test_malloc_payloads_are_sixteen_aligned():
    """**#620**：交给 C 的 `malloc` 必须满足 `max_align_t`（x86-64 SysV = 16）。

    判据不只看"链得过"：它从**真正发出的分配器 IR** 里取起始偏移与取整公式，
    按分配器的步长算一串载荷地址（相对 16 对齐的 arena 基址），要求全部 `% 16 == 0`。
    旧公式 `(size + 15) & -8` 在 `size = 9` 时就把下一个载荷推到 `8 (mod 16)`。
    另外核 arena 基址的取整确实在代码里（hosted 与封着版各一条）。
    """
    for ir in (lomentc._IR_HEAP, lomentc._IR_HEAP_HOSTED, lomentc._IR_HEAP_FFI):
        assert "%need0 = add i32 %size, 23" in ir
        assert "and i32 %need0, -16" in ir
    assert "%ra = add i64 %ri, 15" in lomentc._IR_HEAP_HOSTED
    assert "%rm = and i64 %ra, -16" in lomentc._IR_HEAP_HOSTED
    assert "%ia = add i64 %i, 15" in lomentc._IR_HEAP_FFI
    assert "%im = and i64 %ia, -16" in lomentc._IR_HEAP_FFI
    off, add, mask = _heap_rounding(lomentc._IR_HEAP_FFI)
    bad = []
    for size in range(1, 200):
        need = (size + add) & (mask & 0xFFFFFFFF)
        assert (off + 8) % 16 == 0
        if (off + 8) % 16:
            bad.append((size, off + 8))
        off += need
    assert not bad, f"载荷没有 16 对齐: {bad[:4]}"
    # 封着版（`_IR_HEAP`）走同一条算术 —— 两份分配器不该在这里分叉。
    _off2, add2, mask2 = _heap_rounding(lomentc._IR_HEAP)
    assert (add2, mask2) == (add, mask), (add2, mask2)
    print(f"      分配器: 起始 {off} + 8, need=(size+{add})&{mask} -> 载荷恒 16 对齐")


def main() -> int:
    _FIX.build()
    return loment_suite.run(TESTS, "loment_ports_test")


if __name__ == "__main__":
    sys.exit(main())
