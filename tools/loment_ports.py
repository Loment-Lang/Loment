#!/usr/bin/env python3
"""`docs/219` §4 那把尺子: 一份目标文件 / 归档的**世界端口表**。

> **端口表 = 链接结束后仍然未闭合的符号集合, 逐条带一个类别。**

这份工具**只量, 不改**: 它不动编译器、不动链接器, 只把 `lomelf` 已经在算的那个"需求集"
读出来、分类、计数 —— 也就是 `docs/219` 的 S0(记账面)。它**不**回答"这个库语义上纯不纯"
(那是 `docs/219` §5.1 明写会崩的地方); 它回答的是**名字级**的那个问题。

## 为什么它比链接器的读法**宽**

`ForeignObject` (`tools/lomelf.py:2821`) 是**链接器**的读口: 它不摆 `.data`/`.rodata`,
于是对指向那些节的任何重定位**硬拒** —— 原话是"丢掉它们不是无害的, 代码里的字符串常量
就住在 `.rodata`"。**而"这份产物引用到哪些外部名字"不需要摆节就能读出来。**
所以尺子自己读一遍**全部重定位节**, 并把"链接器今天摆不摆得了它"作为**单独一列**报出来
(`linker_cannot_place`) —— 两件事都会发生, 而且**必须看得见是两件事**。

⚠ 两条读法分叉的地方就是 bug 藏身处, 所以 `loment_ports_test.py` 里有一条**交叉判据**:
在链接器读得进来的那些对象上, 尺子数出的端口集必须与 `lomelf` 数出的**逐条相同**。

## 类别 (docs/219 §4)

| 类 | 含义 | 处置 |
|---|---|---|
| `close` | **可闭合**: 收编面提供(我们写的实现) | 从表上划掉 |
| `world` | **世界**: 值取决于 Loment 之外 | 留在表上 —— 它就是这份产物的世界面 |
| `refused` | **点名拒**: glibc 内部结构 / 按名解析 | 拒, 带理由 |
| `unknown` | **没人分类过** | **一律拒**(宁拒勿猜, `docs/188`) —— 默认档不是"放行" |

默认落在 `unknown` 是**故意的**: 一张"没写就放行"的表是**声明**, 而本文要的是**测量**。

## 名字看不见的那一层 (`docs/219` §5.2 条件一)

符号表看不见**内联 `syscall`**(x86-64 的 `0f 05`)与**裸 asm**。所以除了符号闭包,
这里还扫一遍 `.text` 字节, 命中就把该对象标成 `poisoned`。**过近似是故意的**:
立即数里恰好出现这两个字节也会命中 —— 安全方向上的假阳性只是把一个对象判脏。

用法:
    python tools/loment_ports.py FILE...            # 人读的表
    python tools/loment_ports.py --json FILE...     # 机器读
    python tools/loment_ports.py --roots a,b FILE.a # 只从这些根符号算闭包
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lomelf                                                       # noqa: E402

# ---------------------------------------------------------------------------
# 收编面: 这张表是**数据**, 不是逻辑。`docs/219` §6 说它最终要进产物(Potato);
# 今天它在这儿, 因为 S0 只是"记账面", 还没到进产物那一格。
# ---------------------------------------------------------------------------

#: **可闭合** —— 我们会提供这些符号的实现, 于是它们**不从表上通向世界**。
#:
#: 来源是两份权威表, 照抄不靠记性:
#:   * **C 标准 freestanding 也要求的**: `memcpy` `memmove` `memset` `memcmp`
#:     (C11 §4.6.2.1; C23 又扩了一组 `<string.h>`)。
#:   * **编译器隐式会发的 libcall**: LLVM `RuntimeLibcalls.def` 那一族
#:     (整数除法/取模辅助、移位辅助、栈保护)。
#: 再加上**我们自己已有的分配器** (`@__loment_alloc`, `tools/lomentc.py:154`)。
PROVIDE = frozenset({
    # 分配器 (docs/219 §6 第 1 条)
    "malloc", "free", "realloc", "calloc",
    "__loment_alloc", "__loment_free",
    # freestanding 要求的字符串/内存族
    "memcpy", "memmove", "memset", "memcmp",
    "strlen", "strcpy", "strncpy", "strcmp", "strncmp", "strchr", "strrchr",
    "strcat", "strncat", "memchr",
    # 编译器 libcall (RuntimeLibcalls.def 那一族)
    "__udivdi3", "__umoddi3", "__divdi3", "__moddi3", "__udivmoddi4",
    "__muldi3", "__muloti4", "__ashldi3", "__ashrdi3", "__lshrdi3",
    "__clzdi2", "__ctzdi2", "__popcountdi2",
    "__stack_chk_fail", "__stack_chk_guard",
})

#: **世界** —— 值取决于 Loment 之外。它们**留在表上**, 而且**它们的实现也要在收编面上**
#: (`docs/219` §6 第 3 条), 否则"世界"是被别人定义的。
WORLD = frozenset({
    # 时间
    "time", "clock", "clock_gettime", "clock_getres", "gettimeofday", "nanosleep",
    "times", "setitimer", "localtime", "gmtime", "mktime", "strftime",
    # 随机
    "getrandom", "rand", "random", "srand", "srandom", "drand48", "lrand48",
    # 环境
    "getenv", "setenv", "unsetenv", "putenv", "environ", "sysconf", "getauxval",
    # I/O 与文件
    "read", "write", "open", "openat", "close", "lseek", "pread", "pwrite",
    "fstat", "stat", "lstat", "fopen", "fclose", "fread", "fwrite", "fflush",
    "fseek", "ftell", "printf", "fprintf", "sprintf", "snprintf", "puts", "fputs",
    "perror", "isatty", "unlink", "rename", "mkdir", "access", "getcwd", "chdir",
    # 进程
    "fork", "execve", "execvp", "waitpid", "system", "getpid", "getppid", "_exit",
    "abort", "exit", "atexit", "kill", "raise", "signal", "sigaction",
    # 线程 (docs/219 §10.6: 不进"纯面")
    "pthread_create", "pthread_join", "pthread_mutex_lock", "pthread_mutex_unlock",
    "pthread_self", "pthread_exit", "pthread_key_create", "pthread_setspecific",
    # 区域设置
    "setlocale", "localeconv", "nl_langinfo",
})

#: **点名拒** —— 每一类都有理由, 不是"以后再说" (`docs/219` §4 第三类)。
#:   * glibc 的**内部结构**: 它们不是接口, 是不可能用别的方式满足的实现细节。
#:   * **按名解析**: 把"到哪"变成运行期才知道的字符串 ⇒ 闭包失去完备性 (§5.2 条件一)。
REFUSED = {
    "dlsym": "按名解析: 端口闭包失去完备性 (docs/219 §5.2 条件一)",
    "dlopen": "按名解析: 同上, 且它把边的另一端拿出去 (§5.3)",
    "dlclose": "按名解析",
    "dlerror": "按名解析",
    "__libc_start_main": "glibc 内部结构: 启动胶水由我们的 _start 承担 (§6 第 3 条)",
    "__errno_location": "glibc 内部结构: errno 的 TLS 槽 (单线程下可退化成全局, 尚未收编)",
    "__cxa_atexit": "glibc 内部结构: 析构登记",
    "__dso_handle": "glibc 内部结构: 析构登记",
    "__gmon_start__": "glibc 内部结构: profiling 桩",
}

CLASSES = ("close", "world", "refused", "unknown")


def classify(name: str) -> str:
    """一个符号属于哪一类。**没写过的落在 `unknown`** —— 默认拒。"""
    if name in PROVIDE:
        return "close"
    if name in WORLD:
        return "world"
    if name in REFUSED:
        return "refused"
    return "unknown"


# ---------------------------------------------------------------------------
# 读: 一份 ELF64 小端可重定位目标文件里**符号与重定位**那一层。
# 刻意**不复用** `ForeignObject`: 它带着链接器的约束(不摆 `.data`/`.rodata` 就对指向
# 那些节的重定位硬拒), 而端口表不需要摆节。两条读法**都必须在**, 差别也要报出来。
# ---------------------------------------------------------------------------

SHN_UNDEF = 0
#: 与 `lomelf.py:2818` 同一份名单 —— 代码引用不到的元数据节, 丢掉无害。
_META = (".eh_frame", ".debug", ".comment", ".note")


def _is_code(s: dict) -> bool:
    """`ForeignObject` 的口径: `.text` **与** `.text.*` (rustc/LLVM 默认按函数分节)。"""
    return s["type"] == 1 and (s["name"] == ".text" or s["name"].startswith(".text."))


class Obj:
    """一份目标文件里我们真正要的那点东西。"""

    def __init__(self, name: str, raw: bytes):
        self.name = name
        self.defined: set[str] = set()
        self.undefined: set[str] = set()
        self.text = b""
        self.reloc_to_data = False      #: 有重定位指向非代码节 ⇒ 链接器今天会拒
        self.reloc_unknown = False      #: 有不认识的重定位类型 ⇒ 链接器今天会拒
        self._load(raw)

    def _load(self, raw: bytes) -> None:
        if raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
            raise ValueError(f"{self.name}: 不是 ELF64 小端目标文件")
        e_shoff, = struct.unpack_from("<Q", raw, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 58)
        secs = []
        for k in range(e_shnum):
            o = e_shoff + k * e_shentsize
            nameoff, typ, _fl, _ad, off, size, link, info, _al, _es = \
                struct.unpack_from("<IIQQQQIIQQ", raw, o)
            secs.append({"nameoff": nameoff, "type": typ, "off": off, "size": size,
                         "link": link, "info": info, "name": ""})
        shstr = secs[e_shstrndx]
        for s in secs:
            end = raw.index(b"\x00", shstr["off"] + s["nameoff"])
            s["name"] = raw[shstr["off"] + s["nameoff"]:end].decode("utf-8", "replace")

        self.text = b"".join(raw[s["off"]:s["off"] + s["size"]] for s in secs if _is_code(s))

        # 符号表: `st_info` 高 4 位是绑定、低 4 位是类型 (`ELF64_ST_INFO`)。取错半字节
        # 会让归档的固定点整个失效 —— `lomelf.py:2256` 那处注释记着这次实测。
        #
        # **端口只从符号表收, 不从重定位收**: 每个被重定位引用的 GLOBAL/WEAK 外部符号
        # 本来就在 `.symtab` 里(shndx=0)。反过来收重定位会把**局部节符号**(clang 给字符串
        # 常量的 `.L.str`)当成端口 —— 实测过, 它会让 `dlsym` 那个夹具多出一条假端口。
        for si, s in enumerate(secs):
            if s["type"] != 2:                                  # SHT_SYMTAB
                continue
            strt = secs[s["link"]]
            for k in range(s["size"] // 24):
                nameoff, info, _other, shndx, _val, _sz = \
                    struct.unpack_from("<IBBHQQ", raw, s["off"] + k * 24)
                if nameoff == 0 or (info >> 4) not in (1, 2):    # 无名 / 非 GLOBAL,WEAK
                    continue
                end = raw.index(b"\x00", strt["off"] + nameoff)
                nm = raw[strt["off"] + nameoff:end].decode("utf-8", "replace")
                if shndx == SHN_UNDEF:
                    self.undefined.add(nm)
                elif shndx < len(secs):
                    self.defined.add(nm)

        for s in secs:
            if s["type"] != 4 or s["size"] == 0:                # SHT_RELA
                continue
            tgt = s["info"]
            if 0 < tgt < len(secs) and not _is_code(secs[tgt]) \
                    and not secs[tgt]["name"].startswith(_META):
                self.reloc_to_data = True
            for k in range(s["size"] // 24):
                r_info, = struct.unpack_from("<Q", raw, s["off"] + k * 24 + 8)
                ty, sym_i = r_info & 0xFFFFFFFF, r_info >> 32
                if ty not in lomelf.SUPPORTED_RELOCS:
                    self.reloc_unknown = True                   # 复用的是链接器那份常量
        self.undefined -= self.defined


def read_any(path: Path) -> list["Obj"]:
    """按**内容**分流 (`!<arch>\\n` / `\\x7fELF`), 与 `lomelf.load_foreign` 同一条口径。

    看内容不看后缀: `.a`/`.o`/`.lib`/无后缀在各家工具链里并不统一, 而两种格式的魔数
    都极短且不可能互串。
    """
    raw = path.read_bytes()
    if raw[:8] != b"!<arch>\n":
        return [Obj(path.name, raw)]
    out: list[Obj] = []
    pos = 8
    while pos + 60 <= len(raw):
        hdr = raw[pos:pos + 60]
        size = int(hdr[48:58].decode("ascii", "replace").strip())
        name = hdr[0:16].decode("ascii", "replace").strip()
        data = raw[pos + 60:pos + 60 + size]
        if data[:4] == b"\x7fELF":          # 元数据成员(符号索引/长名表)不是 ELF, 滤掉
            out.append(Obj(f"{path.name}({name})", data))
        pos += 60 + size + (size & 1)      # 成员数据按偶数对齐, 奇数补一个填充字节
    return out


def closure(objs: list["Obj"], roots: set[str] | None = None) -> set[str]:
    """固定点, 与 `lomelf.Archive.select` 同形: 收一个成员 → 它引用的名字进需求 →
    重复到不再变化。**不整包收** —— 整包收会把"某个成员用了 printf"这种用不到的东西
    也拖进来, 而真实库里几乎总有一两个成员带着无关依赖。

    `roots` 为 `None` = **整个归档的最大端口集**(不考虑谁被用到)。
    """
    defined = {n for o in objs for n in o.defined}
    if roots is None:
        return {n for o in objs for n in o.undefined} - defined
    demand, picked = set(roots), []
    changed = True
    while changed:
        changed = False
        for o in objs:
            if o not in picked and (o.defined & demand):
                picked.append(o)
                demand |= o.undefined
                changed = True
    return demand - {n for o in picked for n in o.defined}


def ports(objs: list["Obj"], roots: set[str] | None = None) -> dict:
    table = sorted(({"name": n, "class": classify(n), "why": REFUSED.get(n, "")}
                    for n in closure(objs, roots)),
                   key=lambda e: (CLASSES.index(e["class"]), e["name"]))
    return {
        "objects": [o.name for o in objs],
        "ports": table,
        "counts": {c: sum(1 for e in table if e["class"] == c) for c in CLASSES},
        "poisoned": sorted(o.name for o in objs if b"\x0f\x05" in o.text),
        "linker_cannot_place": sorted({o.name for o in objs
                                       if o.reloc_to_data or o.reloc_unknown}),
    }


def pure(report: dict) -> bool:
    """`docs/205:44` 那条判据在这个名词下的样子: **表为空**(零端口), 且没被毒化。

    "可闭合"那类**不算端口** —— 它们已经从表上划掉了(§6 第 2 条)。
    零端口 ⟺ `llvm-nm` 里除"我们提供的那几个"外没有别的 `U`。
    """
    c = report["counts"]
    return not (c["world"] or c["refused"] or c["unknown"]) and not report["poisoned"] \
        and not report["linker_cannot_place"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_ports")
    ap.add_argument("paths", nargs="+", metavar="FILE")
    ap.add_argument("--roots", default="", help="逗号分隔的根符号; 给了就只算它们的闭包")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    objs: list[Obj] = []
    for p in a.paths:
        objs += read_any(Path(p))
    if not objs:
        print("[ERR] 没有可读的目标文件", file=sys.stderr)
        return 2
    r = ports(objs, {x for x in a.roots.split(",") if x} or None)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        c = r["counts"]
        print(f"端口表: {len(r['ports'])} 条  (close={c['close']} world={c['world']}"
              f" refused={c['refused']} unknown={c['unknown']})")
        for e in r["ports"]:
            why = f"   -- {e['why']}" if e["why"] else ""
            print(f"  [{e['class']:>7}] {e['name']}{why}")
        if r["poisoned"]:
            print(f"  !! 内联 syscall 命中 (0f 05): {', '.join(r['poisoned'])}")
        if r["linker_cannot_place"]:
            print(f"  !! 链接器今天摆不了 (指向非代码节/未知重定位): "
                  f"{', '.join(r['linker_cannot_place'])}")
        print("纯" if pure(r) else "非纯")
    return 0 if pure(r) else 1


if __name__ == "__main__":
    sys.exit(main())
