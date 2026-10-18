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


# ---------------------------------------------------------------- 导入器的拒绝面
#
# `#628` / `#629` / `#641` / `#642` / `#643` 五条是同一个形状：**构建报成功，产物跑起来
# 才死**。它们都住在 `lomelf` 的导入路径上，而症状却指向别处（"undefined symbol"、
# 段错误、加载器拒绑）。所以每条判据都要求链接器**当场点名拒绝**，且理由在消息里。
#
# 夹具**全部手搭**（一个最小 `.so`、一个带坏重定位的 `.o`、一个 ar 归档），不经 clang：
# 量的是"坏输入会不会被拒"，而真工具链根本不肯产出这种坏输入。这也让这五条**永不 SKIP**。


def _minimal_shared_object() -> tuple[bytearray, dict[str, int]]:
    """最小可用的 ELF64 `.so`：一个 R|X 的 `PT_LOAD` + `PT_DYNAMIC` + `.dynsym`。

    导出 `add_two` 与 `keepalive` 两个函数（`st_value` 落在 `PT_LOAD` 里）。返回的偏移表
    指向**要改坏的那几个字段**：符号项、`DT_SONAME` 的值、字符串表里的空串。
    """
    phoff, phnum = 64, 2
    dyn_off = phoff + phnum * 56
    dynsz = 6 * 16
    dynsym_off = dyn_off + dynsz
    syms = ("add_two", "keepalive")
    dynsym_size = 24 * (1 + len(syms))
    dstr_off = dynsym_off + dynsym_size
    dstr = b"\x00"
    offs: dict[str, int] = {}
    for nm in (*syms, "libfixture.so.1"):
        offs[nm] = len(dstr)
        dstr += nm.encode() + b"\x00"
    offs["empty"] = len(dstr)                  # `#641`：指向一个空串
    dstr += b"\x00"
    shstr_off = dstr_off + len(dstr)
    # `.text` 是真的（`#643` 要求 FUNC 的 `st_value` 落在**可执行**节/段里）：
    # 每个导出给一个字节，符号按 `st_shndx=4` 指过来。
    shstr = b"\x00.dynsym\x00.dynstr\x00.shstrtab\x00.text\x00"
    text_off = shstr_off + len(shstr)
    total_text = len(syms)
    shoff = (text_off + total_text + 7) // 8 * 8
    total = shoff + 5 * 64
    b = bytearray(total)
    b[0:8] = b"\x7fELF\x02\x01\x01\x00"
    struct.pack_into("<H", b, 16, 3)           # ET_DYN
    struct.pack_into("<H", b, 18, 62)          # EM_X86_64
    struct.pack_into("<I", b, 20, 1)
    struct.pack_into("<Q", b, 32, phoff)
    struct.pack_into("<Q", b, 40, shoff)
    struct.pack_into("<H", b, 52, 64)
    struct.pack_into("<H", b, 54, 56)
    struct.pack_into("<H", b, 56, phnum)
    struct.pack_into("<H", b, 58, 64)
    struct.pack_into("<H", b, 60, 5)           # e_shnum
    struct.pack_into("<H", b, 62, 3)           # e_shstrndx = .shstrtab
    # PT_LOAD：vaddr 0 恒等映射整个文件，R|X。PT_DYNAMIC 指进中间那张表。
    struct.pack_into("<IIQQQQQQ", b, phoff, 1, 5, 0, 0, 0, total, total, 0x1000)
    struct.pack_into("<IIQQQQQQ", b, phoff + 56, 2, 6, dyn_off, dyn_off,
                     dyn_off, dynsz, dynsz, 8)
    for i, (tag, val) in enumerate(((5, dstr_off), (10, len(dstr)),
                                    (6, dynsym_off), (11, 24),
                                    (14, offs["libfixture.so.1"]), (0, 0))):
        struct.pack_into("<QQ", b, dyn_off + i * 16, tag, val)
    b[dstr_off:dstr_off + len(dstr)] = dstr
    b[shstr_off:shstr_off + len(shstr)] = shstr
    for i, nm in enumerate(syms, start=1):
        struct.pack_into("<IBBHQQ", b, dynsym_off + i * 24, offs[nm],
                         (1 << 4) | 2, 0, 4, text_off + i - 1, 1)

    def sec(idx, nameoff, typ, flags, off, size, link, info, align, ent):
        struct.pack_into("<IIQQQQIIQQ", b, shoff + idx * 64, nameoff, typ,
                         flags, 0, off, size, link, info, align, ent)

    sec(1, 1, 11, 2, dynsym_off, dynsym_size, 2, 1, 8, 24)   # .dynsym
    sec(2, 9, 3, 2, dstr_off, len(dstr), 0, 0, 1, 0)         # .dynstr
    sec(3, 17, 3, 0, shstr_off, len(shstr), 0, 0, 1, 0)      # .shstrtab
    sec(4, 27, 1, 6, text_off, total_text, 0, 0, 16, 0)      # .text ALLOC|EXEC
    return b, {"sym": dynsym_off + 24, "soname": dyn_off + 4 * 16,
               "empty": offs["empty"]}


#: 调共享对象里的 `add_two` 的最小 IR —— 走 `resolve()` 那一步（`#642` 在那里才拒）。
_IR_CALLS_ADD_TWO = """declare i32 @add_two(i32)
define i32 @_start() {
entry:
  %r = call i32 @add_two(i32 1)
  ret i32 %r
}
"""


def _mutated_so(td: Path, mutate) -> Path:
    """先确认**未改的**夹具能被读进来，再落一份改坏的 —— 免得判据测的是"夹具本身就坏"。"""
    raw, off = _minimal_shared_object()
    good = td / "libfixture.so.1"
    good.write_bytes(bytes(raw))
    so = lomelf.load_foreign(good)
    assert "add_two" in so.exports, so.exports
    mutate(raw, off)
    bad = td / "libbroken.so.1"
    bad.write_bytes(bytes(raw))
    return bad


@test
def test_so_export_outside_every_load_is_refused():
    """`#643`：导出表的 `st_value` 落在所有 `PT_LOAD` 之外。

    旧代码收下它，第一条 call 跳到未映射地址（实测 SIGSEGV，rc=139）。**"名字在导出表里"
    只证明名字存在，不证明它指向一段被映射的内存** —— 这一条把后半句补上。
    """
    with tempfile.TemporaryDirectory() as t:
        bad = _mutated_so(Path(t), lambda raw, off:
                          struct.pack_into("<Q", raw, off["sym"] + 8, 0x7F0000000))
        try:
            lomelf.load_foreign(bad)
        except lomelf.Unsupported as e:
            msg = str(e)
        else:
            raise AssertionError("st_value 在 PT_LOAD 之外的导出被收下了 —— 调它会跳到未映射地址")
        assert "add_two" in msg and "PT_LOAD" in msg and "st_value" in msg, msg
        print(f"      {msg}")


@test
def test_hidden_export_is_not_bound_from_outside():
    """`#642`：`STV_HIDDEN` 的符号在 `.dynsym` 里，但 loader 不会跨对象绑定它。

    旧代码只看绑定/类型/`st_shndx`，于是链接期说"库里有这个名字"，加载期说
    `undefined symbol`。判据走 `compile_ll`（`resolve()` 在那一步被调用）——
    只 `load_foreign` 的话新旧代码都绿，测不到东西。
    """
    with tempfile.TemporaryDirectory() as t:
        bad = _mutated_so(Path(t), lambda raw, off: raw.__setitem__(off["sym"] + 5, 2))
        try:
            lomelf.compile_ll(_IR_CALLS_ADD_TWO, [lomelf.load_foreign(bad)],
                              allow_ports=True)
        except lomelf.Unsupported as e:
            msg = str(e)
        else:
            raise AssertionError("hidden 导出被当成可绑定 —— 加载期会报 undefined symbol")
        assert "add_two" in msg and "hidden" in msg and "visibility" in msg, msg
        print(f"      {msg}")


@test
def test_so_with_empty_soname_is_refused():
    """`#641`：`DT_SONAME` 存在但为空。

    旧代码把"有 tag"与"tag 有内容"混为一谈，写出 `DT_NEEDED=""`；loader 匹配不上任何库，
    构建却报成功。空名字不是"没有名字" —— 没有名字时回落到文件名，空名字只能拒。
    """
    with tempfile.TemporaryDirectory() as t:
        bad = _mutated_so(Path(t), lambda raw, off:
                          struct.pack_into("<Q", raw, off["soname"] + 8, off["empty"]))
        try:
            lomelf.load_foreign(bad)
        except lomelf.Unsupported as e:
            msg = str(e)
        else:
            raise AssertionError("空 DT_SONAME 被收下了 —— 产物会带着 DT_NEEDED=\"\"")
        assert "DT_SONAME" in msg and "empty" in msg, msg
        print(f"      {msg}")


@test
def test_foreign_architecture_shared_object_is_refused():
    """`#629`：`e_machine` 不是 x86-64 的 `ET_DYN`。

    `EI_CLASS`/`EI_DATA` 每个 64 位小端交叉目标都满足；只有 `e_machine` 分得开架构。
    放过去的两种结局都很糟：loader 拒掉，或者机器上恰有同名原生库 —— 产物悄悄绑到
    **另一份库**上，V2 的"导出表证明名字存在"当场失效。
    """
    with tempfile.TemporaryDirectory() as t:
        bad = _mutated_so(Path(t), lambda raw, off:
                          struct.pack_into("<H", raw, 18, 183))    # AArch64
        try:
            lomelf.load_foreign(bad)
        except lomelf.Unsupported as e:
            msg = str(e)
        else:
            raise AssertionError("别的架构的 ET_DYN 被当成可用的共享对象")
        assert "e_machine=183" in msg and "EM_X86_64" in msg, msg
        print(f"      {msg}")


def _reloc_object(reloc_type: int) -> bytes:
    """一个 `ET_REL` 目标文件：`.text` 里一个 `ret`，`.symtab` 定义 `f`，
    `.rela.text` 里一条 `reloc_type` 的重定位。用来让成员**符号读得出来、重定位读不了**。"""
    text_off, sym_off, sym_size = 64, 72, 48
    str_off = sym_off + sym_size
    strtab = b"\x00f\x00"
    rela_off = (str_off + len(strtab) + 7) // 8 * 8
    shstr_off = rela_off + 24
    shstr = b"\x00.text\x00.symtab\x00.strtab\x00.rela.text\x00.shstrtab\x00"
    shoff = (shstr_off + len(shstr) + 7) // 8 * 8
    b = bytearray(shoff + 6 * 64)
    b[0:8] = b"\x7fELF\x02\x01\x01\x00"
    struct.pack_into("<H", b, 16, 1)           # ET_REL
    struct.pack_into("<H", b, 18, 62)
    struct.pack_into("<I", b, 20, 1)
    struct.pack_into("<Q", b, 40, shoff)
    struct.pack_into("<H", b, 52, 64)
    struct.pack_into("<H", b, 54, 64)
    struct.pack_into("<H", b, 58, 64)
    struct.pack_into("<H", b, 60, 6)
    struct.pack_into("<H", b, 62, 5)
    b[text_off] = 0xC3
    struct.pack_into("<IBBHQQ", b, sym_off + 24, 1, 0x12, 0, 1, 0, 1)   # f
    b[str_off:str_off + len(strtab)] = strtab
    struct.pack_into("<QQq", b, rela_off, 0, (1 << 32) | reloc_type, 0)
    b[shstr_off:shstr_off + len(shstr)] = shstr

    def sec(idx, nameoff, typ, flags, off, size, link, info, align, ent):
        struct.pack_into("<IIQQQQIIQQ", b, shoff + idx * 64, nameoff, typ,
                         flags, 0, off, size, link, info, align, ent)

    sec(1, 1, 1, 6, text_off, 1, 0, 0, 16, 0)              # .text  ALLOC|EXEC
    sec(2, 7, 2, 0, sym_off, sym_size, 3, 1, 8, 24)        # .symtab
    sec(3, 15, 3, 0, str_off, len(strtab), 0, 0, 1, 0)     # .strtab
    sec(4, 23, 4, 0, rela_off, 24, 2, 1, 8, 24)            # .rela.text
    sec(5, 34, 3, 0, shstr_off, len(shstr), 0, 0, 1, 0)    # .shstrtab
    return bytes(b)


def _archive(member: str, data: bytes) -> bytes:
    hdr = (member.encode().ljust(16) + b"0".ljust(12) + b"0".ljust(6)
           + b"0".ljust(6) + b"644".ljust(8)
           + str(len(data)).encode().ljust(10) + b"`\n")
    return b"!<arch>\n" + hdr + data + (b"\n" if len(data) & 1 else b"")


_IR_CALLS_F = """declare i32 @f(i32)
define i32 @_start() {
entry:
  %r = call i32 @f(i32 1)
  ret i32 %r
}
"""


@test
def test_unusable_archive_member_is_named_when_its_symbol_is_needed():
    """`#628`：归档成员解析失败被**静默丢掉**，失败最后以"未定义的符号 f"报给调用方。

    旧注释写着"真需要它的符号时后面会报到"—— 它从来没报过。判据要求消息里出现
    **成员名**、它**定义的名字**、以及**用不了的原因**（这里是重定位类型 3），
    否则用户永远不知道符号其实在库里。
    """
    with tempfile.TemporaryDirectory() as t:
        ar = Path(t) / "libbroken.a"
        ar.write_bytes(_archive("broken.o", _reloc_object(3)))
        try:
            lomelf.compile_ll(_IR_CALLS_F, [lomelf.load_foreign(ar)],
                              allow_ports=True)
        except lomelf.Unsupported as e:
            msg = str(e)
        else:
            raise AssertionError("坏成员被静默丢掉，符号却解析成功？（夹具或链接器有问题）")
        assert "broken.o" in msg, msg
        assert "defines f" in msg, msg
        assert "3" in msg, msg
        print(f"      {msg}")


def main() -> int:
    _FIX.build()
    return loment_suite.run(TESTS, "loment_ports_test")


if __name__ == "__main__":
    sys.exit(main())
