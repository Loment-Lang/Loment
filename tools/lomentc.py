#!/usr/bin/env python3
# lomentc.py — L1 Loment 编译器 v0 (docs/143)
#
# Loment = L0 接口层 (.lom 布局/契约) + 行为 (函数) + 能力声明。
# v0 策略 (docs/140 §4): 语法是 Rust 的严格子集 —— 不发明语法, 规避 LLM 零语料;
# 先转译到 Rust, 不写后端 —— 可行性判据 = 不写后端就能跑。
#
# 用法:
#   python tools/lomentc.py foo.lomt --emit-rust out.rs
#   python tools/lomentc.py foo.lomt --emit-potato out.json
#   python tools/lomentc.py foo.lomt --check          # 生成物与磁盘对账
#
# 退出码: 0 = 成功 / 1 = 语法或语义错误 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

Tok = lomc.Tok
LomError = lomc.LomError

INT_TYPES = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
TYPES = INT_TYPES + ("bool", "str", "ptr", "()")

# 内建函数: 名字 -> (参数类型, 返回类型) —— 由两后端各自降级 (M1/M2)
BUILTINS = {
    "str_len": (("str",), "u32"),
    "str_eq": (("str", "str"), "bool"),
    "str_concat": (("str", "str"), "str"),   # M2
    "str_byte": (("str", "u32"), "u32"),
    "panic": (("u32",), "u32"),  # M19: 不可返回 (类型仅占位)
    "alloc": (("u32",), "ptr"),  # M15
    "free": (("ptr",), "u32"),
    "load8": (("ptr", "u32"), "u32"),
    "store8": (("ptr", "u32", "u8"), "u32"),
    "atomic_add": (("ptr", "u32"), "u32"),  # M21
    "inb": (("u16",), "u32"),               # M20 (仅 Rust 路径)
    "outb": (("u16", "u8"), "u32"),
    "get_bits": (("u8", "u32", "u32"), "u8"),        # M22: 位域读
    "set_bits": (("u8", "u32", "u32", "u8"), "u8"),  # M22: 位域写
    "str_ptr": (("str",), "ptr"),                    # M67: str 数据指针 (syscall 用)
    "ptr_add": (("ptr", "u32"), "ptr"),              # M73: 指针字节偏移
    "ptr_sub": (("ptr", "u32"), "ptr"),
    "syscall4": (("u64", "u64", "u64", "u64"), "i64"),  # M67: nr, a0..a2 (rax/rdi/rsi/rdx)
    "syscall6": (("u64", "u64", "u64", "u64", "u64", "u64"), "i64"),  # M72: nr, a0..a4
    # M72+: nr + a0..a5 —— 名字里的数就是**值**的个数（与 syscall4 / syscall6 同一约定），
    # 所以 7 = nr + 6 个实参，正好把 Linux 的六个实参寄存器（rdi/rsi/rdx/r10/r8/**r9**）用满。
    # 为什么非要有它：`sendto`(44) 与 `recvfrom`(45) 的第 6 个实参是 `socklen_t`，
    # 在 syscall6 上**没有地方放**（递过去的是寄存器里的残留值）—— 于是"带地址的 UDP"
    # 从 Loment 源码里写不出来（`docs/217` §5 点名的那个缺口，`docs/220` 关掉它）。
    "syscall7": (("u64", "u64", "u64", "u64", "u64", "u64", "u64"), "i64"),
    # M84: 真线程 (docs/245)。**入口是编译期的名字**（字符串字面量）—— 子线程落地后
    # **直接** `call @名字`，不是间接调用，所以静态调用图一个字不动（`docs/208` 那两条
    # 承重门禁的前提不破）。这也是它与"函数指针"（线 B）分家的那条线：这里没有地址，
    # 只有一个由产物写死的调用边。
    # 第四个实参是**完成通知地址**（0 = 不要通知）：子线程在入口返回**之后**原子加一
    # 并 `futex_wake` 那一个地址，然后才 exit。为什么非要有它：子线程的尾声是**编译器
    # 发的**（入口一 return 就 exit），所以**工作线程没有机会在返回之后跑任何代码** ——
    # 没有一个自动的通知点，`join` 就只能靠"工作线程自己记得喊一声"，忘了就是挂死。
    # 只在 ELF/lomelf 目标可用（Windows 没有 `clone`）；PE 目标报错并点名，不静默错编。
    "thread_spawn": (("str", "u64", "u64", "u64"), "i64"),
}
BUILTIN_DIVERGES = ("panic",)  # 求值后不可继续 (M19)

#: `thread_spawn` 的入口名（M84）。**链接器把名字原样当符号用**，所以这里就是能安全落进
#: `.ll` 的 asm 串、又能被符号表接住的那一类 —— 比"合法标识符"再收一点（不许点，免得与
#: `函数$块` 那种内部标签撞面）。不用正则：`lomentc` 顶层不 import `re`。
def _is_fn_name(s: str) -> bool:
    if not s or not (s[0].isascii() and (s[0].isalpha() or s[0] == "_")):
        return False
    return all(c.isascii() and (c.isalnum() or c == "_") for c in s[1:])

_IR_RUNTIME = '''
; ---- Loment freestanding 运行时 (M31: 无 libc) ----
define internal i32 @__loment_memcmp(ptr %a, ptr %b, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %cont ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %eq, label %body
body:
  %pa = getelementptr i8, ptr %a, i64 %i
  %pb = getelementptr i8, ptr %b, i64 %i
  %ca = load i8, ptr %pa
  %cb = load i8, ptr %pb
  %ne = icmp ne i8 %ca, %cb
  br i1 %ne, label %diff, label %cont
cont:
  %i1 = add i64 %i, 1
  br label %loop
diff:
  %da = zext i8 %ca to i32
  %db = zext i8 %cb to i32
  %r = sub i32 %da, %db
  ret i32 %r
eq:
  ret i32 0
}

define internal void @__loment_memset(ptr %p, i8 %v, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %q = getelementptr i8, ptr %p, i64 %i
  store i8 %v, ptr %q
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_memcpy(ptr %d, ptr %s, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %sp = getelementptr i8, ptr %s, i64 %i
  %b = load i8, ptr %sp
  %dp = getelementptr i8, ptr %d, i64 %i
  store i8 %b, ptr %dp
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_abort() {
  call void @llvm.trap()
  unreachable
}

declare void @llvm.trap()
'''

#: **分配器**（`docs/175` §3.4 的 `gc_manual`）。
#:
#: 它**单独一个常量**，因为它**有自己的全局**（`@__loment_heap` / `@__loment_off` /
#: `@__loment_freehead`）。而「发运行期」与「发全局」若是两个独立条件，就会有一个
#: 程序发出 `__loment_alloc` 却没有任何全局可引用 —— **实测**：一份只用整数除法
#: （于是 `@__loment_` 出现、运行期被发出）而不用 `alloc` 的单元，编译期直接报
#: `use of undefined value '@__loment_freehead'`。分配器与它的全局**同进同出**。
_IR_HEAP = '''; ---- 分配器（docs/175 §3.4 的 `gc_manual`）------------------------------------
;
; 这里以前是**纯 bump**（`@__loment_off` 只涨不落），而 `free` 是个**占位** ——
; 写它编得过、跑起来什么都不发生。`docs/175` §3.4 把那种形状点名叫「**允诺却不兑现**」，
; 所以这一版把它兑现：**bump + 地址序空闲链表**。
;
; 块的形状（8 字节头，**载荷 16 字节对齐**）：
;   +0  i32  size   —— 这一块的**总长**（含头），永远是 16 的倍数
;   +4  i32  next   —— 下一块**空闲**块的偏移；0 = 链尾（所以偏移 0 永远不是块）
;                    **负数 = 这块正借在外面**（`alloc` 发的）—— `free` 看**符号**挡双重释放。
;                    值是 `-(mask + 2)`，`mask` 是**块内槽掩码**（`docs/210` §2.9）：
;                    `-1`（恰好是原先那个数）⇒ 块内**整块保守扫**；`≤ -2` ⇒ 块内精确。
;                    掩码**不进任何表** —— 它就是这个字本身。
; 载荷从 +8 开始。整个 arena 是 `@__loment_heap`，偏移从 0 起算。
; **头仍是 8 字节，但 `%need` 按 16 向上取整**：块起点恒为 8 (mod 16)，载荷（+8）因此
; 落在 16 的倍数上。C 的 `malloc` 契约要求 `max_align_t` (16) 对齐，`-mavx2` 的
; `vmovdqa` 直接依赖它（`#620`）。
;
; 三条规则，各有它要挡的东西：
;   * **第一次适配 + 拆分**：空闲块大到有余（余量 ≥ 16）就切一块出来，余下的还回链上。
;     不拆的话一次大分配会把整个链表吃光。**记录尺寸永远等于这块的实际跨度** ——
;     不拆的时候**不把 size 改小**（余量不足 16 字节，改小它换不来可用空间，
;     只让按 size 走的遍历掉进块内部，`FujoJTOP/loment#368`）。
;   * **地址序**：链表按地址升序，`free` 线性找插入点。顺序让「前沿回退」可判。
;   * **前沿回退**：释放的那一块**正好顶到 bump 前沿**时，直接把前沿退回去。
;     这是 LIFO（分配—释放—再分配）不涨内存的原因；没有它，`free` 只是把块挂起来，
;     bump 前沿照样一路涨到 OOM。
;
;   * **`free` 先验所有权**（2026-10-13）：越界 / 未对齐 / 头不合常理 / **已经释放过**的，
;     一律**惰性返回**（不 abort，也绝不挂链）。双重释放会把同一块**同时**放在前沿与空闲
;     链表上，于是两次 `alloc` 拿到同一段内存 —— 那是这个分配器唯一能真正毁掉程序的形状。
;     残余边界（对齐得正好、后面那个字又恰好是**负数**的派生指针）写在 `docs/210` §2.8。
;
; **还没做的**（下一层）：物理相邻的两块不合并（coalesce）。非 LIFO 的碎片化因此还在，
; 而且它是收集器那一层的活（`docs/175` §3.4 的 `gc_auto`）。
define internal ptr @__loment_alloc(i32 %size) {
entry:
  %need0 = add i32 %size, 23
  %need = and i32 %need0, -16
  %head = load i32, ptr @__loment_freehead
  br label %floop
floop:
  %cur = phi i32 [ %head, %entry ], [ %nxt, %fnext ]
  %prev = phi i32 [ 0, %entry ], [ %cur, %fnext ]
  %fend = icmp eq i32 %cur, 0
  br i1 %fend, label %bump, label %fit
fit:
  %hp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %cur
  %bsz = load i32, ptr %hp
  %big = icmp uge i32 %bsz, %need
  br i1 %big, label %take, label %fnext
fnext:
  %n4 = add i32 %cur, 4
  %npp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %n4
  %nxt = load i32, ptr %npp
  br label %floop
take:
  %slack = sub i32 %bsz, %need
  %cansplit = icmp uge i32 %slack, 16
  %t4 = add i32 %cur, 4
  %tpp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %t4
  %rest = load i32, ptr %tpp
  br i1 %cansplit, label %split, label %nosplit
split:
  ; **块的记录尺寸只在「真拆了」的时候才改小**（`FujoJTOP/loment#368`）——
  ; 原来这句在 `taken:` 里无条件执行，于是**不拆分**那条路（余量 < 16 字节，
  ; 块还是那么大，只是没切）也会把 size 改成 `need`。后果有两层：
  ;   * 按 `size` 往前走的每一趟（`__loment_alloc` 的首次适配、`__loment_sweep`、
  ;     `__loment_propagate`、以及收集器的两步）都会从 `cur + need` 继续，
  ;     而那已经不是下一块了 —— 它掉进**块的内部**，把载荷当尺寸读；
  ;   * 载荷里的字节多半是 0 ⇒ `i += 0` ⇒ **死循环**（实测：2 分钟不返回）。
  ; 判据：`loment_elf_test::test_a_reused_block_keeps_its_recorded_extent`。
  %so = add i32 %cur, %need
  %spp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %so
  store i32 %slack, ptr %spp
  %so4 = add i32 %so, 4
  %spp4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %so4
  store i32 %rest, ptr %spp4
  store i32 %need, ptr %hp
  br label %relink
nosplit:
  ; 不拆：**尺寸原样留着**（就是 `%bsz`）—— 那几十字节的余量仍然在块里，
  ; 只是不足以另立一块。改小它不会让那点余量变得可用，只会让遍历失步。
  br label %relink
relink:
  %newhead = phi i32 [ %so, %split ], [ %rest, %nosplit ]
  %ishead = icmp eq i32 %prev, 0
  br i1 %ishead, label %sethead, label %setprev
sethead:
  store i32 %newhead, ptr @__loment_freehead
  br label %taken
setprev:
  %p4 = add i32 %prev, 4
  %ppp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %p4
  store i32 %newhead, ptr %ppp
  br label %taken
taken:
  %t4b = add i32 %cur, 4
  %tppb = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %t4b
  store i32 -1, ptr %tppb
  %ko = add i32 %cur, 8
  %kp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %ko
  ret ptr %kp
bump:
  %off = load i32, ptr @__loment_off
  %nxtoff = add i32 %off, %need
  %fits = icmp ule i32 %nxtoff, 65536
  br i1 %fits, label %bok, label %boom
boom:
  call void @__loment_abort()
  unreachable
bok:
  store i32 %nxtoff, ptr @__loment_off
  %wp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %off
  store i32 %need, ptr %wp
  %w4 = add i32 %off, 4
  %wp4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %w4
  store i32 -1, ptr %wp4
  %bo = add i32 %off, 8
  %bp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %bo
  ret ptr %bp
}

define internal void @__loment_free(ptr %p) {
entry:
  %base = ptrtoint ptr @__loment_heap to i64
  %pi = ptrtoint ptr %p to i64
  %d64 = sub i64 %pi, %base
  %d32 = trunc i64 %d64 to i32
  %alo = icmp uge i32 %d32, 16
  br i1 %alo, label %inrange, label %out
inrange:
  %offa = load i32, ptr @__loment_off
  %ahi = icmp ule i32 %d32, %offa
  br i1 %ahi, label %align, label %out
align:
  %m7 = and i32 %d32, 7
  %aok = icmp eq i32 %m7, 0
  br i1 %aok, label %hdr, label %out
hdr:
  %hi = sub i32 %d32, 8
  %hp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %hi
  %sz = load i32, ptr %hp
  %h4 = add i32 %hi, 4
  %hpp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %h4
  %nx = load i32, ptr %hpp
  %onloan = icmp slt i32 %nx, 0
  br i1 %onloan, label %sane, label %out
sane:
  %szok = icmp uge i32 %sz, 8
  %hend = add i32 %hi, %sz
  %endok = icmp ule i32 %hend, %offa
  %hok = and i1 %szok, %endok
  br i1 %hok, label %check, label %out
check:
  %off = load i32, ptr @__loment_off
  %istail = icmp eq i32 %hend, %off
  br i1 %istail, label %rewind, label %link
out:
  ret void
rewind:
  store i32 %hi, ptr @__loment_off
  ret void
link:
  %head = load i32, ptr @__loment_freehead
  br label %lloop
lloop:
  %cur = phi i32 [ %head, %link ], [ %nxt, %lcont ]
  %prev = phi i32 [ 0, %link ], [ %cur, %lcont ]
  %lend = icmp eq i32 %cur, 0
  %after = icmp ugt i32 %cur, %hi
  %stop = or i1 %lend, %after
  br i1 %stop, label %ins, label %lcont
lcont:
  %l4 = add i32 %cur, 4
  %lpp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %l4
  %nxt = load i32, ptr %lpp
  br label %lloop
ins:
  %i4 = add i32 %hi, 4
  %ipp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %i4
  store i32 %cur, ptr %ipp
  %ishead2 = icmp eq i32 %prev, 0
  br i1 %ishead2, label %sethead, label %setprev
sethead:
  store i32 %hi, ptr @__loment_freehead
  ret void
setprev:
  %q4 = add i32 %prev, 4
  %qpp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %q4
  store i32 %hi, ptr %qpp
  ret void
}

@__loment_heap = internal global [65536 x i8] zeroinitializer
@__loment_off = internal global i32 8
@__loment_freehead = internal global i32 0
'''

#: `hosted` 那一份 arena 的字节数（`docs/228` §3.3）。**编译期常量**，并且**写进产物**
#: （`; loment-heap: platform <N>`）—— 与上面那个 `; loment-port: hosted` 同一个道理：
#: 构建那一侧要知道这件事，而它不该去读源码（读源码要它懂 Loment 的词法，而 IR 本来就是
#: 构建的输入）。写进字节之后，"这一份到底吃没吃到 A"是**量**出来的，不是**声明**出来的。
HOSTED_ARENA_BYTES = 1 << 20

#: **`hosted` 那一份分配器**（`docs/228` 的 A；`docs/219` §8b 的 **S1e**）。
#:
#: 与 `_IR_HEAP` **同序、同一条空闲链表**，只换两件事：
#:   ① arena 不是 `.bss` 里的 `[65536 x i8]`，而是 `@__loment_arena()` 向**宿主**要的一块；
#:   ② 于是寻址全走 `%hb`（基址），而不是数组符号。
#: 块的形状、地址序、首次适配 + 拆分、前沿回退**一个字节没动** —— 因为 arena 仍是
#: **一段连续内存**：`free` 靠 `ptrtoint - 基址` 反算偏移（`:237`），多段才是重写分配器
#: （`docs/228` §1 第三行、§4 的 B2）。
#:
#: **要谁**：宿主的 `malloc`（`docs/228` §3.1）。`hosted` 的产物本来就由 C 工具链链出
#: （`loment_hosted_test` 那条判据链的是真的 `libz.so`），所以宿主分配器就是"OS 那一层"
#: 在这条路上的正确接口；raw syscall 反而要**多一张平台表**，与 `docs/219` §5 的记账面重复。
#: **惰性**：第一次 `alloc` 才要（不在启动路径上挂钩子）；要不到就落 `__loment_abort`。
#:
#: **`sealed` 走不到这一段** —— 它走 `_IR_HEAP`，逐字节不变（`docs/228` §2）。
_IR_HEAP_HOSTED_HEAD = '''; ---- 分配器（`docs/228`：arena 从**外面**来的那一份；`docs/175` §3.4）--------------------
;
; 与 `_IR_HEAP` 同序、同一条空闲链表，只换两件事：arena 从**外面**来
; （**谁给**写在下面那段 `@__loment_arena()` 里：宿主给的、或产物里那块静态的）、
; 寻址走 `%hb`（基址）。
;
; **`free` 的所有权校验与 `_IR_HEAP` 是同一套**（借出标记是负数 + 四条廉价检查；2026-10-13 起
; 标记由原先那个 `-1` 放宽成 `-(掩码 + 2)`，好让块头带上**块内槽掩码**，见 `docs/210` §2.9），
; 残余边界同样写在 `docs/210` §2.8。
; 块的形状、地址序、首次适配 + 拆分、前沿回退一个字节没动 —— 因为 arena 仍是**一段
; 连续内存**（偏移是 i32；多段才是重写分配器，见 `docs/228` §4 的 B2）。
;
; 一次要 `@ARENA@` 字节，惰性（第一次 `alloc` 才要）；要不到就 `__loment_abort`。

'''

#: `@__loment_arena()` 的**宿主版**：向宿主要一块（`malloc`）。
#:
#: 返回值**按 16 向上取整**（`#620`）：载荷从 `基址 + 8` 开始，基址必须 16 对齐才让
#: `malloc` 满足 `max_align_t`。多要 16 字节是为了取整后那一小截仍在块内。
_IR_ARENA_HOST = '''; **宿主版**：向宿主要一块（`malloc`），惰性（第一次 `alloc` 才要）。
define internal ptr @__loment_arena() {
entry:
  %h = load ptr, ptr @__loment_heap
  %nul = icmp eq ptr %h, null
  br i1 %nul, label %ask, label %have
ask:
  %m = call ptr @malloc(i64 @ARENA16@)
  %bad = icmp eq ptr %m, null
  br i1 %bad, label %oom, label %set
oom:
  call void @__loment_abort()
  unreachable
set:
  store ptr %m, ptr @__loment_heap
  br label %have
have:
  %r = phi ptr [ %h, %entry ], [ %m, %set ]
  %ri = ptrtoint ptr %r to i64
  %ra = add i64 %ri, 15
  %rm = and i64 %ra, -16
  %al = inttoptr i64 %rm to ptr
  ret ptr %al
}
'''

#: `@__loment_arena()` 的**封着版**：arena 是一块**就在产物里**的静态数组
#: （`docs/219` §8b 的 **S2**，2026-10-15）。
#:
#: **为什么要单独一版**：`sealed` 的定义是"底下没有东西"（`docs/228` §2），所以没有宿主
#: `malloc` 可问；而一个真 C 库（zlib 的 `deflateInit` 就是）要 **256–512 KiB**，比
#: `_IR_HEAP` 那 64 KiB 大一档。两者合起来只有一条路：**arena 封在产物里，但给够**。
#: "向外面要"（`mmap`/`brk`，即 `docs/219` §8b 的 **S1e B**）**不在这条路上** —— 它要一张
#: 平台表，是另一格的事；这里**明说**代价：`[N x i8]` 是 `.bss`，而本仓的链接器把 `.bss`
#: 按零字节摆进数据段（S1f 的取舍）⇒ **产物文件会大 N 字节**。
#:
#: **谁走这一版**：根开了运行期、声明了 `extern fn`、且**不是** `hosted` 的单元
#: （见 `emit_llvm` 里那个门）。判据落在**产物自己说出来**的 `; loment-heap: ffi <N>` 上。
_IR_ARENA_STATIC = '''@__loment_arena_block = internal global [@ARENA16@ x i8] zeroinitializer

define internal ptr @__loment_arena() {
entry:
  ; **按 16 向上取整**（`#620`）：块的载荷在 `基址 + 8`，基址 16 对齐之后 `malloc`
  ; 才满足 C 的 `max_align_t`。多出的 16 字节保证取整后的尾端仍在数组内。
  %i = ptrtoint ptr @__loment_arena_block to i64
  %ia = add i64 %i, 15
  %im = and i64 %ia, -16
  %r = inttoptr i64 %im to ptr
  ret ptr %r
}
'''

#: `_IR_HEAP_HOSTED` 的**身子**：分配器本体 + 三个全局。与上面两段拼出两种 arena。
_IR_HEAP_HOSTED_BODY = '''define internal ptr @__loment_alloc(i32 %size) {
entry:
  %hb = call ptr @__loment_arena()
  %need0 = add i32 %size, 23
  %need = and i32 %need0, -16
  %head = load i32, ptr @__loment_freehead
  br label %floop
floop:
  %cur = phi i32 [ %head, %entry ], [ %nxt, %fnext ]
  %prev = phi i32 [ 0, %entry ], [ %cur, %fnext ]
  %fend = icmp eq i32 %cur, 0
  br i1 %fend, label %bump, label %fit
fit:
  %hp = getelementptr i8, ptr %hb, i32 %cur
  %bsz = load i32, ptr %hp
  %big = icmp uge i32 %bsz, %need
  br i1 %big, label %take, label %fnext
fnext:
  %n4 = add i32 %cur, 4
  %npp = getelementptr i8, ptr %hb, i32 %n4
  %nxt = load i32, ptr %npp
  br label %floop
take:
  %slack = sub i32 %bsz, %need
  %cansplit = icmp uge i32 %slack, 16
  %t4 = add i32 %cur, 4
  %tpp = getelementptr i8, ptr %hb, i32 %t4
  %rest = load i32, ptr %tpp
  br i1 %cansplit, label %split, label %nosplit
split:
  ; **块的记录尺寸只在「真拆了」的时候才改小**（`FujoJTOP/loment#368`）—— 理由见
  ; `_IR_HEAP` 同一条（这段与那一份逐字同源，只换 arena 的来处与寻址）。
  %so = add i32 %cur, %need
  %spp = getelementptr i8, ptr %hb, i32 %so
  store i32 %slack, ptr %spp
  %so4 = add i32 %so, 4
  %spp4 = getelementptr i8, ptr %hb, i32 %so4
  store i32 %rest, ptr %spp4
  store i32 %need, ptr %hp
  br label %relink
nosplit:
  ; 不拆：**尺寸原样留着**（就是 `%bsz`）。
  br label %relink
relink:
  %newhead = phi i32 [ %so, %split ], [ %rest, %nosplit ]
  %ishead = icmp eq i32 %prev, 0
  br i1 %ishead, label %sethead, label %setprev
sethead:
  store i32 %newhead, ptr @__loment_freehead
  br label %taken
setprev:
  %p4 = add i32 %prev, 4
  %ppp = getelementptr i8, ptr %hb, i32 %p4
  store i32 %newhead, ptr %ppp
  br label %taken
taken:
  %t4b = add i32 %cur, 4
  %tppb = getelementptr i8, ptr %hb, i32 %t4b
  store i32 -1, ptr %tppb
  %ko = add i32 %cur, 8
  %kp = getelementptr i8, ptr %hb, i32 %ko
  ret ptr %kp
bump:
  %off = load i32, ptr @__loment_off
  %nxtoff = add i32 %off, %need
  %fits = icmp ule i32 %nxtoff, @ARENA@
  br i1 %fits, label %bok, label %boom
boom:
  call void @__loment_abort()
  unreachable
bok:
  store i32 %nxtoff, ptr @__loment_off
  %wp = getelementptr i8, ptr %hb, i32 %off
  store i32 %need, ptr %wp
  %w4 = add i32 %off, 4
  %wp4 = getelementptr i8, ptr %hb, i32 %w4
  store i32 -1, ptr %wp4
  %bo = add i32 %off, 8
  %bp = getelementptr i8, ptr %hb, i32 %bo
  ret ptr %bp
}

define internal void @__loment_free(ptr %p) {
entry:
  %hb = call ptr @__loment_arena()
  %base = ptrtoint ptr %hb to i64
  %pi = ptrtoint ptr %p to i64
  %d64 = sub i64 %pi, %base
  %d32 = trunc i64 %d64 to i32
  %alo = icmp uge i32 %d32, 16
  br i1 %alo, label %inrange, label %out
inrange:
  %offa = load i32, ptr @__loment_off
  %ahi = icmp ule i32 %d32, %offa
  br i1 %ahi, label %align, label %out
align:
  %m7 = and i32 %d32, 7
  %aok = icmp eq i32 %m7, 0
  br i1 %aok, label %hdr, label %out
hdr:
  %hi = sub i32 %d32, 8
  %hp = getelementptr i8, ptr %hb, i32 %hi
  %sz = load i32, ptr %hp
  %h4 = add i32 %hi, 4
  %hpp = getelementptr i8, ptr %hb, i32 %h4
  %nx = load i32, ptr %hpp
  %onloan = icmp slt i32 %nx, 0
  br i1 %onloan, label %sane, label %out
sane:
  %szok = icmp uge i32 %sz, 8
  %hend = add i32 %hi, %sz
  %endok = icmp ule i32 %hend, %offa
  %hok = and i1 %szok, %endok
  br i1 %hok, label %check, label %out
check:
  %off = load i32, ptr @__loment_off
  %istail = icmp eq i32 %hend, %off
  br i1 %istail, label %rewind, label %link
out:
  ret void
rewind:
  store i32 %hi, ptr @__loment_off
  ret void
link:
  %head = load i32, ptr @__loment_freehead
  br label %lloop
lloop:
  %cur = phi i32 [ %head, %link ], [ %nxt, %lcont ]
  %prev = phi i32 [ 0, %link ], [ %cur, %lcont ]
  %lend = icmp eq i32 %cur, 0
  %after = icmp ugt i32 %cur, %hi
  %stop = or i1 %lend, %after
  br i1 %stop, label %ins, label %lcont
lcont:
  %l4 = add i32 %cur, 4
  %lpp = getelementptr i8, ptr %hb, i32 %l4
  %nxt = load i32, ptr %lpp
  br label %lloop
ins:
  %i4 = add i32 %hi, 4
  %ipp = getelementptr i8, ptr %hb, i32 %i4
  store i32 %cur, ptr %ipp
  %ishead2 = icmp eq i32 %prev, 0
  br i1 %ishead2, label %sethead, label %setprev
sethead:
  store i32 %hi, ptr @__loment_freehead
  ret void
setprev:
  %q4 = add i32 %prev, 4
  %qpp = getelementptr i8, ptr %hb, i32 %q4
  store i32 %hi, ptr %qpp
  ret void
}

@__loment_heap = internal global ptr zeroinitializer
@__loment_off = internal global i32 8
@__loment_freehead = internal global i32 0
'''


def _heap_indirect(arena: str, nbytes: int) -> str:
    """把"头 + 一段 `@__loment_arena()` + 身子"拼成一份**间接寻址**的分配器。

    两处用它：`hosted`（arena 从宿主的 `malloc` 来）与 **FFI**（arena 是产物里那块静态
    数组，见 `_IR_ARENA_STATIC`）。拼出来的两份**只差 arena 那一段** —— 分配器本体、分配器
    状态、块的形状都是同一份文本，因为那才是"换 arena 不该动的东西"。
    """
    return (_IR_HEAP_HOSTED_HEAD + arena + "\n" + _IR_HEAP_HOSTED_BODY).replace(
        "@ARENA16@", str(nbytes + 16)).replace("@ARENA@", str(nbytes))


_IR_HEAP_HOSTED = _heap_indirect(_IR_ARENA_HOST, HOSTED_ARENA_BYTES)

#: `FFI` 那一份的 arena 字节数（`docs/219` §8b 的 S2）。
#:
#: **量出来的，不是挑的**：真 `libz.a` 的 `deflateInit`（memLevel 默认 8）在这里要
#: **256–512 KiB** —— 512 KiB 起 `compress → uncompress → 原文` 才跑完（实测矩阵见
#: `docs/219` §8b）。取 1 MiB 是给它留一档余量。
#:
#: ⚠ **与 `HOSTED_ARENA_BYTES` 绑在一起**：`gc_auto` 那一档的收集器（标记表 = `arena/8`
#: 行）**两份共用同一段文本**（`_IR_GC_HOSTED`）—— 前提就是这两个数**相等**。改一个必须
#: 同时改另一个，否则 FFI + `gc_auto` 那份的表按错的 arena 算。这条不是注释，是约束。
FFI_ARENA_BYTES = 1 << 20
assert FFI_ARENA_BYTES == HOSTED_ARENA_BYTES, "FFI 与 hosted 的收集器共用文本，两个数必须相等"
_IR_HEAP_FFI = _heap_indirect(_IR_ARENA_STATIC, FFI_ARENA_BYTES)

_RUST_RUNTIME = '''
// ---- Loment 运行时 (M15 堆分配) ----
#[allow(static_mut_refs)]
static mut __LOMENT_HEAP: [u8; 65536] = [0; 65536];
#[allow(static_mut_refs)]
static mut __LOMENT_OFF: usize = 0;

fn __loment_alloc(size: u32) -> *mut u8 {
    unsafe {
        let off = __LOMENT_OFF;
        let end = off + size as usize;
        if end > 65536 {
            panic!("loment: heap oom");
        }
        __LOMENT_OFF = end;
        __LOMENT_HEAP.as_mut_ptr().add(off)
    }
}
fn __loment_load8(p: *mut u8, off: u32) -> u8 {
    unsafe { *p.add(off as usize) }
}
fn __loment_store8(p: *mut u8, off: u32, v: u8) {
    unsafe { *p.add(off as usize) = v; }
}

// ---- P4: 能力域运行时 ----
#[allow(static_mut_refs)]
static mut __LOMENT_AUDIT: [u64; 16] = [0; 16];

// **审计只在放行那一支里加**（`#162`）。原先 `__loment_guard` 先 `+= 1` 再判界,
// 于是越界的那一次**已经写了审计**才 panic —— SECURITY.md 属性 1（traps, with no
// other side effect）与属性 2（计数 == 放行次数）都不成立, 而且与 IR 后端分岔:
// 那边的审计只在 `gok` 块里, trap 块是光秃秃的 `__loment_abort()/unreachable`。
// 结构照 IR 镜像: 放行 -> `__loment_audit`, 越界 -> `__loment_trap`, 两个函数各管一事。
fn __loment_audit(cap: usize) {
    unsafe {
        __LOMENT_AUDIT[cap] += 1;
    }
}
fn __loment_trap(cap: usize, idx: u64) {
    panic!("loment: capability {} violation at {}", cap, idx);
}
'''


def _rust_t(t: str) -> str:
    if t == "str":
        return "&'static str"
    if t == "ptr":  # M15
        return "*mut u8"
    if t == "()":  # M16: unit
        return "()"
    if _is_mut_slice(t):
        return f"&mut [{_slice_elem(t)}]"
    if _is_slice(t):
        return f"&[{_slice_elem(t)}]"
    return t
BIN_OPS = (
    "||", "&&", "==", "!=", "<=", ">=", "<", ">",
    "|", "^", "&", "<<", ">>", "+", "-", "*", "/", "%",
)
UN_OPS = ("-", "!", "&")
# Rust 的运算符优先级 (数值越大结合越紧)
PRECEDENCE = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "|": 5, "^": 6, "&": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}

# ---------------------------------------------------------------- 语法树


def _is_array(t: str) -> bool:
    return t.startswith("[") and t.endswith("]") and ";" in t


def _is_slice(t: str) -> bool:
    """只读切片 `[T]` 或可变切片 `mut [T]` (M3/M4): ptr + len 视图。"""
    return (t.startswith("[") and t.endswith("]") and ";" not in t) or (
        t.startswith("mut [") and t.endswith("]")
    )


def _is_mut_slice(t: str) -> bool:
    return t.startswith("mut [")


def _slice_elem(t: str) -> str:
    return t[5:-1].strip() if _is_mut_slice(t) else t[1:-1].strip()


def _array_elem(t: str) -> str:
    return t[1:t.rindex(";")].strip()


def _array_len(t: str) -> int:
    return int(t[t.rindex(";") + 1:-1].strip())


def _type_ok(t: str, known: set[str]) -> bool:
    """类型是否已声明: 基类型 / struct 名 / 由它们构成的数组或切片。"""
    if t in known:
        return True
    if _is_array(t):
        return _type_ok(_array_elem(t), known)
    if _is_slice(t):
        return _type_ok(_slice_elem(t), known)
    return False


#: 类型位置上的未知名 —— **一条独立的诊断**（`#323`），消息形如
#: `未知类型 f64（let a 的类型）`，分类进 **E030**。
#:
#: **为什么不并进 E002**：按 `loment_diag` 那张表"码按修法分"的口径，两边的修法根本不是
#: 一件事。E002 的修法是"先声明这个名字 / 改拼写 / 补 `use`"；而 `f64` 在类型位上，用户
#: 要做的是"**这门语言没有这个类型**"（Loment 没有浮点，`loment types` 只列整型与聚合）。
#: 并进去的代价实测过：0.1.4.5 给的是 `error[E002]: undeclared name` + 一张讲"没有类型推断、
#: 先定义后使用、跨模块要 `pub`"的建议表 —— 三条都不适用，用户会去找一个不存在的拼写错误。
#:
#: **消息里为什么带那段括号**：同一个码在六处触发（字段 / 载荷 / 返回 / 形参 / let / 数组
#: 元素），而渲染器只给 `--> file:line`，丢了角色就只剩一个光秃秃的类型名。角色是**事实**，
#: 不是建议，所以它并进消息不违反上面的分工。
#:
#: **那六处为什么把字面文本写在各自的 f-string 里，不共用一个助手函数**：判据
#: `loment_err_test::test_every_message_the_compiler_can_print_has_a_code` 是**扫模板**的 ——
#: 它把 `errs.append(...)` 的参数取出来，非字面量的占位代成 `1` 再送 `classify()`。助手函数
#: 会让模板退化成 `1: 1`，那条判据当场红。这里重复六遍字面量是**判据要求的**，不是疏忽。


def _extern_ty_ok(t: str) -> bool:
    """`extern fn` 签名里允许的类型 (docs/173 §3)。

    第 1 阶段只收**标量与 ptr**。三条被挡在外面, 每条都有理由, 而且都会**改变调用点的
    代码形状** —— 那正是"报错退出、不静默错编"要挡的东西:

    * `str` —— 它是"指针 + 长度", **不是 C 字符串**。谁补 NUL、谁负责释放, 是一层独立的
      约定, 得先有 `cstr` 之类的转换 (阶段 2)。
    * 聚合按值 (struct/enum/数组/切片) —— System V 要按字段分类拆进寄存器, 大于 16 字节
      走内存。这是独立一块规则, 不是"顺手支持一下"。
    * 变参 —— 第 1 阶段的签名是定长的。
    """
    return t in INT_TYPES or t in ("ptr", "bool")


@dataclass
class Capability:
    name: str
    space: str
    lo: int
    hi: int
    revocable: bool
    line: int


@dataclass
class Struct:
    name: str
    fields: list  # list[tuple[str, str]]
    line: int
    pub: bool = False
    tparams: list = field(default_factory=list)  # M7
    from_generic: str = ""                       # M45: 由哪个泛型声明单态化而来
    generic_args: list = field(default_factory=list)  # M45: 单态化实参


@dataclass
class FieldAccess:
    obj: object
    name: str
    line: int


@dataclass
class StructLit:
    name: str
    inits: list  # list[tuple[str, object]]
    line: int


@dataclass
class Param:
    name: str
    type: str


@dataclass
class IntLit:
    value: int
    line: int


@dataclass
class BoolLit:
    value: bool
    line: int


@dataclass
class StrLit:
    value: str
    line: int


@dataclass
class Ident:
    name: str
    line: int


@dataclass
class Call:
    name: str
    args: list
    line: int


@dataclass
class Bin:
    op: str
    left: object
    right: object
    line: int


@dataclass
class Un:
    op: str
    expr: object
    line: int


@dataclass
class EnumDecl:
    name: str
    variants: list  # list[str]
    line: int
    payloads: dict = field(default_factory=dict)  # variant -> 载荷类型 (无载荷者不出现)
    pub: bool = False
    tparams: list = field(default_factory=list)  # M7
    from_generic: str = ""                       # M45
    generic_args: list = field(default_factory=list)  # M45
    from_prelude: bool = False   # M10 预置 Option/Result: 由 load() 注入**每个**模块,
                                 # 单元级唯一性检查必须排除它 (否则多模块单元必报假重名)


@dataclass
class EnumPath:
    enum: str
    variant: str
    line: int
    bind: str | None = None  # match 模式绑定名


@dataclass
class EnumCtor:
    enum: str
    variant: str
    arg: object
    line: int


@dataclass
class Match:
    subject: object
    arms: list  # list[tuple[Path | None, list]]  None = 通配 _
    line: int


@dataclass
class For:
    var: str
    lo: object
    hi: object
    body: list
    line: int
    #: 循环体的 **token 跨度**（半开）与"末尾能不能批量归还"（`docs/210` §2 的 L2）。
    #: 跨度由解析器记（它是唯一同时握着 token 流与 AST 的地方）；`l2` 由 `parse()` 末尾
    #: 那一趟填 —— 与 `Func.l0` 同一个理由：**规则定义在 token 流上**（见 `_l0_promotable`）。
    tok_at: int = 0
    tok_end: int = 0
    l2: bool = False
    #: **L1**（`docs/210` §2.5）：本循环之后要还掉的名字（"最后一次用处"落在体内）。
    l1: list = field(default_factory=list)


@dataclass
class ConstDecl:
    name: str
    type: str
    value: int
    line: int
    pub: bool = False


@dataclass
class ArrayLit:
    items: list
    line: int


@dataclass
class Index:
    obj: object
    idx: object
    line: int


@dataclass
class Guard:
    cap: str
    expr: object
    line: int
    cap_id: int = 0   # 解析后填充
    lo: int = 0
    hi: int = 0


@dataclass
class Let:
    name: str
    type: str
    expr: object  # None = 未初始化声明 (M9: let x: T;)
    line: int


@dataclass
class Cast:
    expr: object
    type: str
    line: int


@dataclass
class Try:
    expr: object
    line: int


@dataclass
class Assign:
    target: object  # Ident | Index
    expr: object
    line: int


@dataclass
class If:
    cond: object
    then: list
    otherwise: list
    line: int
    #: **两支各自的 token 跨度**（半开，`[at, end)`）—— 同 `While.tok_at/tok_end` 的约定：
    #: `at` 取 `{` **之后**那一格、`end` 取 `}` 那一格。`else if` 链上 `otherwise` 是
    #: **一条**语句，跨度就是那条语句本身。
    then_at: int = 0
    then_end: int = 0
    else_at: int = 0
    else_end: int = 0
    #: **L2**（`docs/210` §2.3）：这一支能不能在末尾批量归还（把前沿退回去）。
    #: 与 `While.l2` 同一条规则（`_l2_ok`），只是"体"换成了分支。
    then_l2: bool = False
    else_l2: bool = False


@dataclass
class While:
    cond: object
    body: list
    line: int
    #: 同 `For.tok_at/tok_end/l2`。
    tok_at: int = 0
    tok_end: int = 0
    l2: bool = False
    #: **L1**（`docs/210` §2.5）：本循环之后要还掉的名字（"最后一次用处"落在体内）。
    l1: list = field(default_factory=list)


@dataclass
class Return:
    expr: object
    line: int
    #: **L1**（`docs/210` §2.5）：本 `return` **求值之后、`ret` 之前**要还掉的名字。
    l1: list = field(default_factory=list)


@dataclass
class ExprStmt:
    expr: object
    line: int


@dataclass
class Func:
    name: str
    params: list
    ret: str
    body: list
    line: int
    pub: bool = False            # M12: 跨模块可见
    tparams: list = field(default_factory=list)  # M6: 泛型参数
    interrupt: bool = False      # M33: x86 中断处理函数
    from_generic: str = ""       # M45: 由哪个泛型声明单态化而来
    generic_args: list = field(default_factory=list)  # M45: 单态化实参
    extern: bool = False         # docs/173: 外部函数声明 (只有签名, 由链接进来的目标文件提供)
    #: **L0 静态提升**（`docs/210` §2 / `gc_auto_alpha`）：`{局部名: 提升到栈的字节数}`。
    #: 由**解析器**在 `parse()` 末尾填（见 `_l0_promotable`：规则定义在 token 流上，
    #: 而解析器是唯一同时握着 token 流与函数体跨度的地方）。其它档一律为空 dict。
    l0: dict = field(default_factory=dict)
    #: 函数体的 **token 跨度**（半开，`[tok_at, tok_end)`）—— L0 分析要它。
    #: 单态化克隆走 `deepcopy`，两个数跟着走 —— **这是对的**：规则看的是表面 token，
    #: 于是泛型参数写成 `T` 的函数在单态化成 `ptr` 之后**仍然不提升**（自举侧同判）。
    tok_at: int = 0
    tok_end: int = 0
    #: **L1**（`docs/210` §2.5）：**函数末尾**要还掉的名字 —— "最后一次用处"不落在任何
    #: 循环体内的那些（落点选函数末尾的理由见 `_l1_place`）。
    l1: list = field(default_factory=list)
    #: **跨函数 deref-only 参数分析的小结**（`docs/210` §7 的那次放宽）。形如
    #: `None`（首参不是 `ptr`，不参与）或 `(escapes, callees)`：
    #: `escapes` = 首参**有没有**逃逸的用法；`callees` = 首参**只**当"整个第一个实参"
    #: 流进去的那些被调名字的集合。由**解析器**在 token 流上填（见 `_deref_summary`：
    #: 规则定义在 token 流上，与 L0/L1/L2 同一条纪律）。全局不动点见 `_gc_alpha_pass`。
    #: 存**小结**而不是 token 流：它小、随 `deepcopy` 走，且不必把整条词法流留在模块上。
    deref: object = None


def _rename_self(stmts: list) -> None:
    """impl 方法里 self -> __self (Rust 参数名不能是 self)。"""
    def rx(e) -> None:
        if isinstance(e, Ident) and e.name == "self":
            e.name = "__self"
        elif isinstance(e, Bin):
            rx(e.left)
            rx(e.right)
        elif isinstance(e, Un):
            rx(e.expr)
        elif isinstance(e, Call):
            for a in e.args:
                rx(a)
        elif isinstance(e, MethodCall):
            rx(e.obj)
            for a in e.args:
                rx(a)
        elif isinstance(e, Index):
            rx(e.obj)
            rx(e.idx)
        elif isinstance(e, FieldAccess):
            rx(e.obj)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                rx(it)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                rx(fe)
        elif isinstance(e, EnumCtor):
            rx(e.arg)
        elif isinstance(e, Cast):
            rx(e.expr)

    for s in stmts:
        if isinstance(s, Let):
            rx(s.expr)
        elif isinstance(s, Assign):
            rx(s.expr)
            rx(s.target)
        elif isinstance(s, If):
            rx(s.cond)
            _rename_self(s.then)
            _rename_self(s.otherwise)
        elif isinstance(s, While):
            rx(s.cond)
            _rename_self(s.body)
        elif isinstance(s, For):
            rx(s.lo)
            rx(s.hi)
            _rename_self(s.body)
        elif isinstance(s, Match):
            rx(s.subject)
            for _, b in s.arms:
                _rename_self(b)
        elif isinstance(s, Return):
            rx(s.expr)
        elif isinstance(s, ExprStmt):
            rx(s.expr)


def _resolve_guards(mods, caps_map: dict) -> None:
    """P4: guard 语句按能力表填充 id/lo/hi (供后端发射, 避免再传上下文)。"""
    def walk(stmts) -> None:
        for s in stmts:
            if isinstance(s, Guard):
                info = caps_map.get(s.cap)
                if info:
                    s.cap_id, s.lo, s.hi = info
            elif isinstance(s, If):
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, While):
                walk(s.body)
            elif isinstance(s, For):
                walk(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk(b)

    for m in mods:
        for f in m.funcs:
            walk(f.body)


def _resolve_methods(mods, funcs_map: dict, structs: dict, enums: dict) -> None:
    """给 MethodCall 打上具体函数名 (静态派发): obj.m(...) -> Type_m(obj, ...)。"""
    def rx(e, scope) -> None:
        if isinstance(e, MethodCall):
            rx(e.obj, scope)
            for a in e.args:
                rx(a, scope)
            ot = expr_type(e.obj, scope, funcs_map, structs)
            if ot:
                e.mangled = f"{ot}_{e.name}"
            return
        if isinstance(e, Bin):
            rx(e.left, scope)
            rx(e.right, scope)
        elif isinstance(e, Un):
            rx(e.expr, scope)
        elif isinstance(e, Cast):
            rx(e.expr, scope)
        elif isinstance(e, Call):
            for a in e.args:
                rx(a, scope)
        elif isinstance(e, Index):
            rx(e.obj, scope)
            rx(e.idx, scope)
        elif isinstance(e, FieldAccess):
            rx(e.obj, scope)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                rx(it, scope)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                rx(fe, scope)
        elif isinstance(e, EnumCtor):
            rx(e.arg, scope)

    def walk(stmts, scope) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.expr is not None:
                    rx(s.expr, scope)
                scope[s.name] = s.type
            elif isinstance(s, Assign):
                rx(s.expr, scope)
                rx(s.target, scope)
            elif isinstance(s, If):
                rx(s.cond, scope)
                walk(s.then, dict(scope))
                walk(s.otherwise, dict(scope))
            elif isinstance(s, While):
                rx(s.cond, scope)
                walk(s.body, dict(scope))
            elif isinstance(s, For):
                rx(s.lo, scope)
                rx(s.hi, scope)
                walk(s.body, dict(scope))
            elif isinstance(s, Match):
                rx(s.subject, scope)
                for _, b in s.arms:
                    walk(b, dict(scope))
            elif isinstance(s, Return):
                rx(s.expr, scope)
            elif isinstance(s, ExprStmt):
                rx(s.expr, scope)
            elif isinstance(s, Guard):
                rx(s.expr, scope)

    for m in mods:
        for f in m.funcs:
            walk(f.body, {p.name: p.type for p in f.params})


@dataclass
class Trait:
    name: str
    methods: list  # list[tuple[str, str]]  (方法名, 返回类型)
    line: int
    pub: bool = False


@dataclass
class Impl:
    trait: str
    type: str
    funcs: list  # list[Func], 第 0 个参数是 self
    line: int


@dataclass
class MethodCall:
    obj: object
    name: str
    args: list
    line: int
    mangled: str = ""  # 解析后的具体函数名 (静态派发)


@dataclass
class ExtBlock:
    """一个**外部代码块**（`docs/185` S1）：`let c { … }` / 声明过的 `c { … }`。

    `body` 是 `{` 与 `}` 之间的**原始字节**（词法器按 raw 模式收的，不经过转义表）。
    **Loment 不看它一眼** —— 它只是一个要被原样带到产物里去的东西；谁去编它是 S2 的事。
    """
    lang: str
    body: str
    line: int


@dataclass
class Module:
    name: str
    uses: list[str] = field(default_factory=list)      # use "*.lom"  (L0 布局; 只有路径形式)
    imports: list[str] = field(default_factory=list)   # use "*.lomt" (L1 模块; 路径形式)
    name_imports: list[str] = field(default_factory=list)   # use <名字>  (L1 模块; 名字形式)
    #: 每条 `use` 写在第几行（`(文本, 行)`，两种写法都记）。`#480`：装载期的每一条
    #: 报错原来都钉在 `1:1`，caret 落在 `module m` 上 —— 而 `use` 在几十行之外。
    use_lines: list[tuple[str, int]] = field(default_factory=list)
    caps: list[Capability] = field(default_factory=list)
    structs: list[Struct] = field(default_factory=list)
    enums: list[EnumDecl] = field(default_factory=list)
    consts: list[ConstDecl] = field(default_factory=list)
    traits: list[Trait] = field(default_factory=list)
    impls: list[Impl] = field(default_factory=list)
    funcs: list[Func] = field(default_factory=list)
    #: 外部函数声明 (`extern fn`, docs/173)。**与 funcs 分开存** —— 它们没有函数体、不参与
    #: 单态化、发射时出 `declare` 而不是 `define`。混进 funcs 会让"发 define"那条路
    #: 发出一条没有函数体的 define (非法 IR), 所以要分开。
    externs: list[Func] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    #: **核心模式**的取值，按**维**记（`docs/175` §3.0 / §3.4）：`{"mode": "no_std"}`
    #: 或 `{"gc": "gc_auto"}`。某一维不写 = 那一维的默认档（`CORE_DEFAULTS`）——
    #: 所以"缺这一维"与"显式写了默认值"**语义上一样**，但**报错上不一样**：
    #: 同一维写两次要报（在 `check()` 里，语法层只收集）。
    #:
    #: **它是核心语法的硬写法**（用户 2026-09-17）：这几维都不走下面那套**可定义**的
    #: 开关机制，保有自己的规则（`docs/182` §1.3）。
    chooses: dict[str, str] = field(default_factory=dict)
    #: 每一维**写在第几行**（`(维, 取值, 行)`）。**留成列表而不是只留最后一个**：
    #: "这一维写了两次"要能报出第一次在哪一行、写的是什么。
    choose_lines: list[tuple[str, str, int]] = field(default_factory=list)
    #: **开关表**（`docs/182` §1）: `set choose <名字> {…}` 定义, `choose <名字>` /
    #: `choose close <名字>` 取值。由 `_apply_switches` 在**词法流上**落定后挂上来 ——
    #: 开关是**编译期**的事, 它的行不进 AST（关着的那段体连 token 都不进 parser）。
    switches: "SwitchTable | None" = None
    #: **本模块自己**声明了哪些开关。与 `switches` 分开是必须的 —— 两个问题：
    #:   * `switches` = **整个程序**的表（含 `addin` 单元里的定义）。"未定义的开关"、
    #:     "同名两次"、超上限、进 Potato，用的都是**它**；
    #:   * `own_switches` = **本模块自己写了没写**。只有"库不许 `choose`"用它 ——
    #:     混用的话，每个被 `use` 进来的库都会"因为全局表里有定义"而被误判成库写了 choose。
    own_switches: "SwitchTable | None" = None
    #: **外部代码块**（`docs/185` S1）。两个字段而不是一个:
    #:   * `ext_langs` = `command <语言>` / `foruse <语言>` **声明**过的语言名。
    #:     词法器就是靠它消歧的（不带 `let` 的写法只认声明过的名字），parser 只是
    #:     把它记下来 —— **S1 里它不驱动任何东西**，真去拉编译器是 S2；
    #:   * `ext_blocks` = 块本身（语言名 + 正文）。
    ext_langs: list[str] = field(default_factory=list)
    ext_blocks: list[ExtBlock] = field(default_factory=list)
    #: **这个单元注册的 `loment` 命令名**（`docs/223` 形态 B）。声明写成：
    #:
    #:     pub fn loment_command() -> str { return "mycmd"; }
    #:
    #: `None` = 没声明。"声明了但名字取不出来"是**另一种状态**，靠 `command_line` 分：
    #: `command_line > 0 && command is None` 就是形状不对（要报，不能当成"没声明"）。
    #:
    #: **它不是语法** —— 词法器扫标签读出来的（与 `loment.conf` 的 `source_ext` 同一种
    #: 读法），所以没有新关键字、没有新 AST 节点。
    command: "str | None" = None
    #: `command()` 那个函数写在第几行（报错要点到它）。
    command_line: int = 0
    #: 写了 `command()` 但名字取不出来时的**原因**（空串 = 取出来了 / 压根没写）。
    #: 它存在就说明"声明形状不对" —— 必须报，不能当成"没声明"。
    command_bad: str = ""
    #: 这个单元有没有 `command_main`（命令体入口）。它与 `_start` **互斥** ——
    #: 两个都是进程入口，同时写就是两个入口（`check()` 里报）。
    command_main: bool = False
    #: `addin <名字>` 的位置（`docs/182` §1.4）。**只有根单元能写** —— 与"库不许 `choose`"
    #: 是同一条纪律的两半。它不进 AST（`_apply_switches` 会抹掉），但**"写了没生效"必须报出来**：
    #: 预扫只走"根 + `addin` 目标"那一张图，所以**被 `use` 进来的库里的 `addin` 会静默失效**
    #: —— 那正是本仓反复要消灭的东西。
    addin_lines: list[tuple[str, int]] = field(default_factory=list)
    #: 这个模块是**根单元用 `addin` 拉进来的**（`docs/182` §1.4）。它与 `use` 进来的库
    #: 在 `check()` 里**待遇相反**：库不许 `choose`、也不许 `addin`；而 addin 单元
    #: **正是**为了写 `choose` 才存在的，它自己还常常以 `addin <自己>` 开头。
    #: 不加这个标记，`chooseset.lomt` 会被自己的规则判死。
    from_addin: bool = False
    #: **这个模块的源文件路径**（`load()` 填）。**只给调试信息用**（`docs/190`）——
    #: DWARF 的 `!DIFile` 要说清"这一行在哪个文件里"，而 `name` 是**模块名**，不是路径。
    #:
    #: 原先用 `{name}.lomt` + 仓库根拼出来的路径**指向一个不存在的文件**（实测：
    #: `loment/examples/user_hello.lomt` 的行表显示成 `D:/Dev/Loment-DEV\user_hello.lomt`）。
    #: 那让 `loment dbg`（M75）报的"源码级符号化"指不到源，也让断点无从对起 ——
    #: **调试器就是被这一条挡住的**。
    src: "Path | None" = None
    #: **直接依赖的单元身份**（`resolve_deps` 填，`_unit_id` 的取值）。“依赖体检那一趟”
    #: 要用它：每个单元**只拿自己的依赖**再接一遍，而不是整份闭包（#726）。
    #:
    #: **为什么这不是"更窄所以更危险"**：闭包里的**兄弟单元**会被当成它的依赖 —— 于是
    #: `f64bits`（没有一条 `use`）会收到"依赖 `mem` 要求 `runtime`"这种话，而它不是
    #: 根的依赖、只是**根的另一个依赖的依赖**。`check()` 里那句"传给整份闭包只会漏报、
    #: 不会误报"就是从这儿被证伪的。
    dep_ids: list = field(default_factory=list)
    #: 本模块的**词法流**（`load()` 填，已在开关/`comefor` 之后）。L0/L1/L2 的判据都
    #: 定义在 token 流上，而"跨函数 deref-only"那次放宽（`docs/210` §7）让分析**不能在
    #: `parse()` 里做**了 —— 安全表要 root + deps 一起算，那只有 `emit_*` 那一层够得着，
    #: 于是函数体跨度（`f.tok_at/tok_end`）得能重新对着 token 流核。**只在需要时读**。
    toks: list = field(default_factory=list)
    #: **会把运行期拖进产物的构造**（`docs/217`）：`[(点名, 行), …]`，按出现序。
    #: 由**解析器**在 `parse()` 末尾填（与 `Func.deref` 同一个位置、同一个理由：规则定义
    #: 在 token 流上，而解析器是唯一握着它的地方）。`no_runtime` 那条强保证读它。
    rt_sites: list = field(default_factory=list)


# ---------------------------------------------------------------- 开关 (docs/182 §1)

#: 单个文件的 `choose`（含 `set choose`）条数上限。用户 2026-09-17 定**至少 500**。
#: 与 `use` 的 300 条上限同一种保护: 超限**报错**, **绝不静默丢** —— 静默丢在这里的
#: 后果比丢一个 use 更坏: 开关少了一个, 那段代码凭空消失, 而报错一句都没有。
MAX_CHOOSE = 500

#: `addin` 的**条数**上限（`docs/182` §1.4）。与 `MAX_USE` (300) 同一种保护：
#: **超限报错，绝不静默丢** —— 静默丢在这里的后果是"开关少了一条"，那段代码凭空消失，
#: 而一句报错都没有。
MAX_ADDIN = 300

#: 依赖/`addin` 的**嵌套深度**上限，与自举 `loment/selfhost/driver.lomt:29 MAXDEPTH` 同值。
#:
#: **参考实现以前没有这个上限，而自举有 —— 而且是静默截断**（`driver.lomt:784-786`
#: `if depth >= MAXDEPTH { return off; }`）。于是**同一份源码，依赖链深过 8 层时两个实现
#: 给出不同的单元**。这与 `MAX_USE` 那条（`driver.lomt:46-50` 自己记的"2026-09-16 发现"）
#: 是同一个形状 —— 那次修的是**条数**，**深度**没跟着修。这一轮补齐：**两边同值，
#: 超限一律报错**。实测仓库里最深是 7（`lompi/lpi_test.lomt`），离静默截断只差一个 `use`。
MAXDEPTH = 8


#: **核心模式的维**（`docs/175` §3.0 / §3.4）。它与可定义的开关**不是一类东西**
#: （`docs/182` §1.3）：核心模式是**核心语法的硬写法**、由编译器检查、进 Potato；
#: 开关是用户可定义的，几万个也行。
#:
#: 所有维共用一条规矩：**每一维整个程序恰好一个取值，而且只有根单元能定**。
#: 所以这里是一张**维 → 取值**的表，而不是一串散在各处的字面量 —— 加一维就只动这一处。
#: （改之前 `("std", "no_std")` 裸写了三遍，那种写法加第二维必漏。）
#:
#: 今天五维：
#:   * `mode`    —— `std` / `no_std`：跑在宿主上还是裸机上（`docs/143` §3.2）；
#:   * `gc`      —— `gc_manual` / `gc_auto` / `gc_auto_alpha`：回收由谁做（`docs/175` §3.4）；
#:   * `runtime` —— `runtime` / `no_runtime`：产物里**有没有运行期**（`docs/175` §3.6）；
#:   * `port`    —— `sealed` / `hosted`：产物**通不通着世界**（`docs/222` §4）；
#:   * `vm`      —— `vm` / `no_vm`：**要哪种执行模型**（`docs/231`）；
#:   * `lms`     —— `close` / `open`：**要不要内存安全**（`docs/253`）。
#:
#: `runtime` 的取值**刻意只说"有没有"**，不说"里面装了什么"：装的东西会随年份长
#: （今天是收集器，明天可能是线程、宿主服务），把它钉进值名里，两年后加能力就得回头
#: 改这一维的定义。它今天装的就是 `gc_auto` 要的那个运行期。
CORE_DIMS: dict[str, tuple[str, ...]] = {
    "mode": ("std", "no_std"),
    # `gc_auto_alpha` = **混合档**（用户 2026-09-23 定，`docs/175` §3.4.1）：
    # 静态内存管理 + 动态回收，**不存在任何冻结全部业务的阶段**，且自适应。
    # 名字带 `alpha` 是**明说的**：这一档在动，用它的项目认这一点。
    "gc": ("gc_manual", "gc_auto", "gc_auto_alpha"),
    # 用户 2026-09-23：「运行期是海量工程必经之路，我们不得不利用 `choose` 开关
    # 启动/关闭 runtime」。**默认是关的** —— 默认档不许改变任何现有程序的行为。
    "runtime": ("runtime", "no_runtime"),
    # **对外端口**（`docs/222` §4，用户 2026-10-10 拍的价）。`docs/219` 把"链接结束后仍未
    # 定义的符号集合"翻成一份**世界端口表**；这一维声明的是**这份产物允不允许有端口**。
    # `sealed`（封闭）= 今天的形状，也是**默认** —— 默认档不许改变任何现有程序的行为；
    # `hosted`（对外）= 允许链真 libc / 真共享库，端口表就是它声明的世界面。
    # 取值**刻意不带生态名**：今天是 libc/zlib，明天是 JVM/CPython —— 与 `runtime` 同一条
    # 纪律，装的东西不进值名。
    "port": ("sealed", "hosted"),
    # **执行模型**（`docs/231`，用户 2026-10-11 定第五维）。与 `runtime` 同一条纪律：
    # 取值**只说"有没有"**，不说"是哪一档"—— 照 `docs/229` §1.5.5 那张梯子，档位会随年份长
    # （T0 解释 / T1 反馈向量 / T2 基线编译 / T3 优化+去优化），钉进值名里就等于把今天的内容
    # 钉成这一维的身份。**默认 `no_vm`** —— 默认档不许改变任何现有程序的行为。
    #
    # **它今天不提供任何东西**（没有字节码、没有解释器，`docs/231` §1.3），所以被**渠道闸**
    # 关在发行版外面（`_vm_gate` 那一段）：写得出、却什么也拿不到，比写不出来更坏。
    "vm": ("vm", "no_vm"),
    # **内存安全**（`docs/253`，用户 2026-10-15 定第六维）。与 `gc` **正交** ——
    # `gc` 管"谁归还"（生命周期的**内向可达**），这一维管"谁能碰"（许可的**外向**）；
    # `docs/210` §6.2 那句"箭头反了"说的就是这两条是同一张图上的相反方向。
    #
    # `lms_close`（关）= 今天的形状，也是**默认** —— 默认档不许改变任何现有程序的行为。
    # `lms_open`（开）= 检查器发 E024–E026，保障器**按站点分配**发内联界检查。
    #
    # **它不引入运行期组件**（`docs/253` §11.2）：四格落的都是**指令**
    # （一条 `cmp` / 一个 `llvm.trap`），没有表、没有位图 —— 所以它与 `no_runtime`
    # **不冲突**，`CORE_CONFLICTS` 里因此没有它。
    # 取值**刻意不带机制名**：今天是边界检查，明天可能是派生指针追踪 ——
    # 与 `runtime` / `vm` 同一条纪律，装的东西不进值名。
    # **但带维名前缀**（`lms_*`，照 `gc_manual`/`gc_auto` 的先例）：`open` / `close`
    # 单独太泛，而 `CORE_WORDS` 还兼管表层语法的 `choose` 行摘取。
    "lms": ("lms_close", "lms_open"),
    # **安全审计**（`docs/262`，用户 2026-10-18 定第七维）。与其它六维**正交**：
    # 它不改变任何判决，只决定"放行有没有来源"的记录在不在。
    # `lspe_open_admin`（开）= 默认；`lspe_close_admin`（关）= **关审计，不关检查** ——
    # LMS / LGC / runtime / 能力域的判决一条不少，关掉的只是"凭什么放行"的台账。
    # **关闭带 `_admin`**：关关键系统是一次管理动作（`docs/262` §10.1）。
    # 取值**带维名前缀**（`lspe_*`），理由同 `lms_*`：`open` / `close` 单独太泛。
    "lspe": ("lspe_open_admin", "lspe_close_admin"),
}
#: 冲突**两两查**时的维序。**写死的** —— 报错文本里两个取值的先后由它决定，
#: 而两个实现比的是字节，所以它不能是集合迭代顺序。
#: `lms` 排在最后：它**没有冲突对**，追加在尾部不会改动任何既有配对的先后。
CORE_DIM_ORDER: tuple[str, ...] = ("mode", "gc", "runtime", "port", "vm", "lms", "lspe")
#: 所有核心模式的取值 —— "这一个 `choose` 是核心模式还是开关"就看它在不在这里面。
CORE_WORDS = frozenset(w for _ws in CORE_DIMS.values() for w in _ws)
#: 取值 → 属于哪一维（报错要说清是**哪一维**写了两次）。
CORE_DIM_OF = {w: d for d, ws in CORE_DIMS.items() for w in ws}
#: 每一维**不写**时的取值 —— 默认档，且默认**不改变任何现有程序的行为**。
CORE_DEFAULTS = {"mode": "std", "gc": "gc_manual", "runtime": "no_runtime",
                 "port": "sealed", "vm": "no_vm", "lms": "lms_close",
                 "lspe": "lspe_open_admin"}
#: 维的**人话**名字。报错要说清是**哪一维**写了两次 —— `gc` 对用户不是一个词，
#: 而"核心模式只能声明一次"在有两维之后就**说不清是哪一维**了。
CORE_DIM_ZH = {
    "mode": "运行模式（`std` / `no_std`）",
    "gc": "回收档（`gc_manual` / `gc_auto` / `gc_auto_alpha`）",
    "runtime": "运行期（`runtime` / `no_runtime`）",
    "port": "对外端口（`sealed` / `hosted`）",
    "vm": "执行模型（`vm` / `no_vm`）",
    "lms": "内存安全（`lms_close` / `lms_open`）",
    "lspe": "安全审计（`lspe_open_admin` / `lspe_close_admin`）",
}
#: **互相冲突的取值对**（`docs/175` §3.4 ⚠）。键是取值，值 = (和它冲突的取值, 为什么)。
#: 报错要**点名这两档为什么冲突**，不能泛泛说"非法组合"（判据见 `docs/175` §3.4）。
CORE_CONFLICTS = {
    ("no_std", "gc_auto"): (
        "自动回收要一个**运行期**，而 `no_std` 的定义是"
        "「只能用核那一层」—— 两者放在一起等于要求**核里带一个收集器**，"
        "那不是「核保持小」。这一档**先划窄**：真有人要，再按 `docs/175` §4 那条"
        "「能独立校验」的路子把它开成一个**受约束的子集**"),
    # `gc_auto_alpha` 比 `gc_auto` 更依赖运行期（它要自适应、要策略池），所以同一条冲突
    # 对它**只强不弱** —— 它一样不能与 `no_std` 并存。
    ("no_std", "gc_auto_alpha"): (
        "混合档（`gc_auto_alpha`）比 `gc_auto` **更依赖运行期**（它要自适应、要有策略池），"
        "而 `no_std` 的定义是「只能用核那一层」—— 同一条冲突，对它只强不弱"),
    # `runtime` 一进语言，上面那两条冲突就**说得更直白**了：自动回收要的就是那个运行期，
    # 而 `no_runtime` 是明说不要它。所以这一对不是"暂时划窄"，是**定义上就矛盾**。
    # 注意 `gc_manual` **不与 `no_runtime` 冲突** —— "要运行期、但内存我自己管"是一条
    # 必须能表达的档（线程／宿主服务在，收集器不在）。
    ("gc_auto", "no_runtime"): (
        "自动回收**要的就是那个运行期**（根表、收集点、收集器本身都住在里面），"
        "而 `no_runtime` 是明说产物里不要运行期 —— 这一对是**定义上就矛盾**，"
        "不是暂时划窄。要手动回收请写 `choose gc_manual`；"
        "要运行期就把它开着（`choose runtime`），两者不冲突"),
    ("gc_auto_alpha", "no_runtime"): (
        "混合档（`gc_auto_alpha`）**更依赖运行期**（它要自适应、要策略池，"
        "还要放编译期算不出来的那部分），"
        "而 `no_runtime` 是明说产物里不要运行期 —— 同一条冲突，对它只强不弱"),
    # `hosted` 那两条（`docs/222` §4.2）。**两条理由不同，所以各写一份** —— 与
    # `no_std`+`gc_auto` / `no_runtime`+`gc_auto` 那一对的分法同形。
    ("no_std", "hosted"): (
        "`no_std` 的定义是「只能用核那一层」——**没有宿主**；"
        "而 `hosted` 要链的是**宿主上的**库（libc / 共享库）。"
        "这一对是**定义上就矛盾**：一个说「底下没有东西」，一个说「往下链东西」"),
    ("gc_auto_alpha", "hosted"): (
        "混合档的 L2（块纪元）是**分配器前沿回卷**（`docs/210` §2.3），买的是栈纪律；"
        "而外部库把指针放进**它自己的结构**里（zlib 的 `z_stream` 就是），"
        "那些指针在 Loment 的栈之外 —— 前沿一回卷，C 手里那个指针就指到了**已经退回去"
        "的地方**。`docs/219` §6.1 已把这一对写成硬边界；"
        "要 hosted 请用 `gc_manual` 或 `gc_auto`"),
}


class SwitchTable:
    """`docs/182` §1 的开关表: 名字 -> (定义行, 取值)。

    **为什么开关要能和核心模式分开**: 核心模式 (`std`/`no_std`) 是**硬写法**、是核心语法;
    开关是**可定义**的, 一个程序可以有几万个（上限见 `MAX_CHOOSE`）。两者同用一个词
    `choose`, 但只要看**名字是不是 `std`/`no_std`** 就分得开（`docs/182` §1.3）。
    """

    def __init__(self) -> None:
        self.defs: dict[str, int] = {}                  # 名字 -> 定义行（首次）
        self.vals: dict[str, tuple[bool, int]] = {}     # 名字 -> (开?, 取值行)（首次）
        #: `(名字, 重复行, 首次行)` —— **同一件事写两遍要报出来**，不是后一个盖前一个。
        #: 与"`choose` 只能出现一次"删除之后留下的那个空档正好互补：条数放开了，
        #: 但**同名**仍然只许一次，否则"这个开关到底开没开"就没有答案。
        self.dup: list[tuple[str, int, int]] = []
        self.ndup = 0                                   # 重复的额外条数（不重复计）

    def state(self, name: str) -> bool:
        """**没写就是关**。定义过但没取值, 与没定义过一样都是 `False`。"""
        v = self.vals.get(name)
        return v[0] if v else False

    def dump(self) -> list[dict]:
        """进 Potato 的形状（用户 2026-09-17：**开关取值要进 Potato**）。

        **按名字排序**：确定性是判据（同一份源 -> 同一串字节），字典序是唯一不依赖
        遍历顺序的排法。只收**定义过的**开关 —— 取值一个没定义的名字是错误
        （`check()` 里报），不该悄悄进对象。
        """
        return [{"name": n, "on": self.state(n)} for n in sorted(self.defs)]


def _collect_switches(toks: list, tbl: SwitchTable) -> None:
    """把 token 流里**深度 0** 的开关声明收进 `tbl`：定义 + 取值。

    先收全部、再摊开/抹掉 —— 这样"先定义后取值"与"先取值后定义"一个样（取值可能写在
    体的**后面**，见 `_apply_switches` 的说明）。

    **只收，不动 `toks`** —— 所以预扫那一趟可以直接用它（`prescan_switches`）。
    """
    depth = 0
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "punct" and t.val in "{[(":
            depth += 1
        elif t.kind == "punct" and t.val in "}])":
            depth -= 1
        elif depth == 0 and t.kind == "ident" and t.val == "set" \
                and i + 2 < len(toks) and toks[i + 1].kind == "ident" \
                and toks[i + 1].val == "choose" and toks[i + 2].kind == "ident":
            # **`set choose X` 是定义, 不是取值** —— 漏掉这一条会把定义当"打开"，
            # 关着的开关于是照样摊开（实测撞到：`choose close x` 之后体仍被编进去）。
            dn = toks[i + 2]
            if dn.val in tbl.defs:
                tbl.dup.append((dn.val, t.line, tbl.defs[dn.val]))
                tbl.ndup = tbl.ndup + 1
            else:
                tbl.defs[dn.val] = t.line
            i = i + 2
            continue
        elif depth == 0 and t.kind == "ident" and t.val == "choose":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and nxt.kind == "ident" and nxt.val == "close":
                nn = toks[i + 2] if i + 2 < len(toks) else None
                if nn is not None and nn.kind == "ident":
                    if nn.val in tbl.vals:
                        tbl.dup.append((nn.val, t.line, tbl.vals[nn.val][1]))
                        tbl.ndup = tbl.ndup + 1
                    else:
                        tbl.vals[nn.val] = (False, t.line)
            elif nxt is not None and nxt.kind == "ident" and nxt.val not in CORE_WORDS:
                if nxt.val in tbl.vals:
                    tbl.dup.append((nxt.val, t.line, tbl.vals[nxt.val][1]))
                    tbl.ndup = tbl.ndup + 1
                else:
                    tbl.vals[nxt.val] = (True, t.line)
        i = i + 1


def _reject_nested_switch_decls(toks: list) -> None:
    """开关声明（`set choose X` / `choose X` / `addin X`）**不许出现在任何块内部**。

    为什么必须掐掉（`docs/182` §1.10）：`set choose A { … }` 的体是**任意代码**，所以
    `set choose A { set choose B { … } }` 语法上是可能的；可 `B` 算不算数取决于 `A` 开没开
    —— 而 `A` 的状态正是装载器要算的东西。放开它就把"状态"变成**求不动点**，而不动点迭代
    没有显然的终止证明（而且它自己就又需要一条判据）。

    **必须在摊开之前判**：摊开之后"体里的声明"与"顶层的声明"在 token 流上就分不开了。

    **判据是"声明形状"而不是词本身** —— `set`/`choose`/`addin` **都不是保留字**
    （`docs/158` §4 第 12 条：参考实现接受关键字做标识符），所以 `fn set() -> u32`、
    `let choose: u32 = 1;` 全都合法，按词判会误伤。形状对上了才报。
    """
    depth = 0
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "punct" and t.val in "{[(":
            depth += 1
            i = i + 1
            continue
        if t.kind == "punct" and t.val in "}])":
            depth -= 1
            i = i + 1
            continue
        if depth > 0 and t.kind == "ident":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            nxt2 = toks[i + 2] if i + 2 < len(toks) else None
            is_ident = nxt is not None and nxt.kind == "ident"
            if t.val == "set" and is_ident and nxt.val == "choose" \
                    and nxt2 is not None and nxt2.kind == "ident":
                raise LomError(t.line, t.col, "开关声明不许写在另一个开关体里"
                                               "（`set choose`）—— 预扫看不见它，"
                                               "它算不算数取决于外层开关开没开")
            if t.val == "choose" and is_ident and nxt.val not in CORE_WORDS:
                raise LomError(t.line, t.col, "开关取值不许写在另一个开关体里"
                                               "（`choose`）—— 同上")
            if t.val == "addin" and is_ident:
                raise LomError(t.line, t.col, "`addin` 不许写在另一个开关体里 —— 同上")
        i = i + 1


def _check_chooseset(toks: list, where: str) -> None:
    """`addin` 拉进来的单元**只许写 `choose` 相关代码**（`docs/182` §1.4）。

    白名单（深度 0 的顶层）：`module <名字>` / `addin <名字>` / `set choose <名字> { … }` /
    `choose <名字>` / `choose close <名字>`。注释词法器已经吃掉了，不用管。

    **为什么要有这条**：`addin` 拉的是**一份开关设定**，不是库。它里面一旦能写 `fn`/`struct`/
    `use`，就等于"用 `addin` 从另一个方向绕开库的纪律" —— `use` 那条路禁 `choose`，
    这条路就得禁**其余的一切**。两个方向都堵上，"装代码"与"装开关"才真的是两件事。
    """
    allowed = ("module", "addin", "set", "choose")
    depth = 0
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "eof":                 # 词法器末尾补的那个 —— `val` 是空串
            break
        if t.kind == "punct" and t.val in "{[(":
            depth += 1
            i += 1
            continue
        if t.kind == "punct" and t.val in "}])":
            depth -= 1
            i += 1
            continue
        if depth > 0:                       # 体是**代码** —— 白名单只管顶层
            i += 1
            continue
        if t.kind != "ident" or t.val not in allowed:
            raise LomError(t.line, t.col,
                           f"`addin` 拉进来的单元里只能写 choose 相关代码（在 {where}）"
                           f" —— 这里是 {t.val!r}；装代码请用 `use`（那是库），"
                           f"装开关才用 `addin`")
        if t.val in ("module", "addin"):
            i += 2                          # 吃掉名字
        elif t.val == "set":
            i += 3                          # `set choose <名字>`
        else:
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            i += 3 if (nxt is not None and nxt.kind == "ident"
                       and nxt.val == "close") else 2
    return


def _apply_switches(toks: list, tbl: SwitchTable, collect: bool = True) -> list:
    """在**词法流**上把开关落定（`docs/182` §2）。

    **为什么要在这一层做**：关着的那段体要"**在解析之前**跳过"，而状态可能写在体的
    **后面**：

        set choose lomenterr { ... }      // 定义在前
        choose close lomenterr            // 取值在后

    在 token 流上做，那个顺序问题就消失了 —— 先扫一遍收全部取值，再扫第二遍摊开/抹掉。
    而且这样**"关着就解析跳过"是字面为真的**：那段 token 根本没进 parser。

    **花括号配对照做**（`docs/182` §2 那张表）：关着也要能挡住"少一个 `}` 把整份源
    结构弄塌"这种错。

    返回**新的** token 列表：
      * `set choose X { 体 }` 开着 -> 换成 **体本身**（摊到顶层，那段代码从此属于模块）
      * `set choose X { 体 }` 关着 -> **整段抹掉**
      * `choose X` / `choose close X` / `set choose X` 的行 -> 抹掉（开关是编译期的事，
        不进 AST；进 Potato 走 `SwitchTable.dump`）
      * `choose std` / `choose no_std` -> **原样留着**（核心模式，parser 要读）
      * `addin <名字>` -> 抹掉（装载器的事，`docs/182` §1.4；与 `choose` 同理，不进 AST）

    `collect=False` 时**跳过第一遍**：表已由预扫定死（`prescan_switches`，`docs/182` §1.10）。
    这是给"两趟装载"留的 —— 真装载趟不该把表再收一遍。
    """
    _reject_nested_switch_decls(toks)

    # 第一遍: 收**定义**与**取值**（抽成 `_collect_switches` —— 预扫那一趟也要用它，
    # 而且必须**同一段代码**：两处各写一份就是两份会漂的实现）。
    if collect:
        _collect_switches(toks, tbl)

    # 第二遍: 摊开或抹掉。
    out: list = []
    depth = 0
    i = 0
    while i < len(toks):
        t = toks[i]
        if depth == 0 and t.kind == "ident" and t.val == "set" \
                and i + 2 < len(toks) and toks[i + 1].kind == "ident" \
                and toks[i + 1].val == "choose" \
                and toks[i + 2].kind == "ident":
            sname = toks[i + 2].val
            tbl.defs.setdefault(sname, t.line)
            j = i + 3
            if j >= len(toks) or toks[j].kind != "punct" or toks[j].val != "{":
                raise LomError(t.line, t.col, "`set choose` 后面要跟一个块 `{ … }`")
            # 找配对的 `}`
            d = 1
            body_start = j + 1
            k = j + 1
            while k < len(toks) and d > 0:
                if toks[k].kind == "punct" and toks[k].val == "{":
                    d += 1
                elif toks[k].kind == "punct" and toks[k].val == "}":
                    d -= 1
                k += 1
            if d != 0:
                raise LomError(t.line, t.col, f"开关 `{sname}` 的块没闭合")
            body_end = k - 1                      # 指向那个 `}`
            if tbl.state(sname):
                out.extend(toks[body_start:body_end])   # 开着: 体摊到顶层
            # 关着: 一个 token 都不留 —— 这就是"解析跳过"
            i = k
            continue
        if depth == 0 and t.kind == "ident" and t.val == "choose":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and nxt.kind == "ident" and nxt.val == "close":
                i = i + 3                        # 吞掉 `choose close <名字>`
                continue
            if nxt is not None and nxt.kind == "ident" and nxt.val not in CORE_WORDS:
                i = i + 2                        # 吞掉 `choose <名字>`
                continue
        if depth == 0 and t.kind == "ident" and t.val == "addin":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and nxt.kind == "ident":
                i = i + 2                        # 吞掉 `addin <名字>`
                continue
        if t.kind == "punct" and t.val in "{[(":
            depth += 1
        elif t.kind == "punct" and t.val in "}])":
            depth -= 1
        out.append(t)
        i = i + 1
    return out


# ---------------------------------------------------------------- 语法分析

#: **嵌套深度上限**（`#44`）。解析器是递归下降的：一层块 = 好几帧 Python 栈，
#: 而 400 层嵌套块（4 KB 的一份源）原先直接把栈用穿 —— 参考实现抛 `RecursionError`
#: 冒到命令行上，**自举侧那边是 SIGSEGV**（C 的栈更小）。两种都不是诊断。
#: 本仓最深的语料是 12 层，64 给了 5 倍余量；上限存在的意义是"报出来"，不是"够用"。
MAX_NEST = 64


class Parser:
    """复用 lomc 的词法器; 只实现 L1 的顶层与语句/表达式。"""

    def __init__(self, toks: list[Tok], src: str):
        self.toks, self.src, self.i = toks, src, 0
        self.no_struct = 0  # >0 时禁止结构体字面量 (if/while 条件位置的歧义)
        #: 当前嵌套深度（块 + 表达式共用一个计数）—— 见 `MAX_NEST`。
        self.depth = 0

    # -- 基础
    def peek(self, k: int = 0) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind: str, val: str | None = None) -> bool:
        t = self.peek()
        return t.kind == kind and (val is None or t.val == val)

    def accept(self, kind: str, val: str | None = None) -> Tok | None:
        return self.next() if self.at(kind, val) else None

    def expect(self, kind: str, val: str | None = None, what: str = "") -> Tok:
        if not self.at(kind, val):
            t = self.peek()
            raise LomError(t.line, t.col, f"期望 {val or kind}{what}，得到 {t.val or '<eof>'!r}")
        return self.next()

    def ident(self, what: str = "标识符") -> str:
        return self.expect("ident", None, f"（{what}）").val

    def int_lit(self) -> int:
        neg = bool(self.accept("punct", "-"))
        t = self.expect("number", None, "（整数）")
        v = int(t.val, 16) if t.val[:2].lower() == "0x" else int(t.val, 10)
        return -v if neg else v

    def type_name(self) -> str:
        """类型名: 基类型 / struct / 数组 [T; N] / 只读切片 [T] / 可变切片 mut [T]。"""
        if self.at("ident", "mut") and self.peek(1).kind == "punct" and self.peek(1).val == "[":
            self.next()
            self.next()
            elem = self.type_name()
            self.expect("punct", "]")
            return f"mut [{elem}]"
        if self.at("punct", "["):
            self.next()
            elem = self.type_name()
            if self.accept("punct", ";"):
                n = self.int_lit()
                self.expect("punct", "]")
                if n <= 0:
                    raise LomError(self.peek().line, self.peek().col, "数组长度必须为正")
                return f"[{elem}; {n}]"
            self.expect("punct", "]")
            return f"[{elem}]"
        name = self.ident("类型名")
        if self.at("punct", "<"):  # M7: 泛型实参 Pair<u32>
            self.next()
            args: list[str] = []
            while not self.at("punct", ">"):
                args.append(self.type_name())
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
            return f"{name}<{', '.join(args)}>"
        return name

    def parse_tparams(self) -> list[str]:
        out: list[str] = []
        if self.at("punct", "<"):
            self.next()
            while not self.at("punct", ">"):
                out.append(self.ident("类型参数"))
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
        return out

    # -- 顶层
    def parse(self) -> Module:
        self.expect("ident", "module", "（文件必须以 module 开头）")
        mod = Module(self.ident("模块名"))
        self.parse_items(mod)
        # **deref-only 小结**（`docs/210` §7 那次放宽的原料）：每个函数算一次"首参流进
        # 哪些被调"。**规则定义在 token 流上**（同 L0/L1/L2），所以在这里算 —— 解析器是
        # 唯一同时握着 token 流与函数体跨度的地方。**与档位无关**：它只描述形状，"安全
        # 与否"由跨函数、跨模块的全局不动点定（`_gc_alpha_pass`）。
        for f in mod.funcs + [g for im in mod.impls for g in im.funcs]:
            f.deref = _deref_summary(f, self.toks)
        # 本模块的**词法流留在模块上** —— L0/L1/L2 的分析搬去了 `_gc_alpha_pass`（发射前），
        # 因为那次放宽要一张**跨模块**的安全表，而解析器手上只有本模块。
        mod.toks = self.toks
        # **拖运行期的构造**（`docs/227`）：与 `f.deref` 同一条理由 —— 规则定义在
        # token 流上，解析器是唯一能一次扫全模块的地方（`check()` 里再扫就要第二份实现）。
        mod.rt_sites = _runtime_sites(self.toks)
        return mod

    def parse_items(self, mod: Module, nested: bool = False) -> None:
        """顶层条目。`nested=True` 是 `register <名字> { … }` 块里那一层：**同一批条目**，
        只是遇到 `}` 就停（收尾那一步归调用方）。

        块里的条目就是**普通条目** —— 它们照常进 `mod.funcs` / `mod.structs` / …，
        于是后面每一趟（符号表、类型检查、发射、L0/L1 分析）都**不必知道 register 存在**。
        声明本身（名字、E024/E025/E026）全部由**词法标签那一趟**给出（`command_label_from_tokens`）
        —— 名字在这里只被**吃掉**，不另存一份（两处存必然漂，自举侧更是只有扫描那一处）。
        """
        while not self.at("eof") and not (nested and self.at("punct", "}")):
            t = self.peek()
            if t.kind != "ident":
                raise LomError(t.line, t.col, f"顶层只允许 use/capability/fn，得到 {t.val!r}")
            is_pub = False
            if t.val == "pub":  # M12
                self.next()
                is_pub = True
                t = self.peek()
                if t.kind != "ident":
                    raise LomError(t.line, t.col, "pub 之后需要一项声明")
            if t.val == "register":  # docs/223 §2 形态 A
                self.next()
                if not self.accept("string"):
                    # 名字：标识符（常用）或字符串字面量（要带 `-` 时用 —— 合法名字的字符集
                    # 是 `[A-Za-z0-9_-]`，而 `-` 不是标识符字符）。
                    self.ident("命令名")
                self.expect("punct", "{", "（`register <名字> { … }`：块里放这条命令的东西）")
                self.parse_items(mod, nested=True)
                self.expect("punct", "}")
            elif t.val == "use":
                self.next()
                # 两种写法并存:
                #   use "loment/examples/bytes.lomt"   路径形式
                #   use bytes                          名字形式 (后缀见 resolve_name)
                nxt = self.peek()
                if nxt.kind == "ident":
                    _nm = self.next()
                    mod.name_imports.append(_nm.val)
                    mod.use_lines.append((_nm.val, t.line))
                else:
                    p = self.expect("string", None, "（.lom 或源文件路径）").val
                    mod.use_lines.append((p, t.line))
                    # **L0 只有 `.lom` 一种后缀**, 别的任何后缀都算 L1 源 —— 后缀不是
                    # 语言的一部分, 只是习惯 (用户 2026-09-16: "Loment 也可以作为其他
                    # 文件名后缀的诞生地")。别把这条改回"等于 .lomt 才算 L1": 那样
                    # `use "x.foo"` 会被当成 L0 去找 .lom 而报"不存在"。
                    if p.endswith(L0_EXT):
                        mod.uses.append(p)
                    else:
                        mod.imports.append(p)
            elif t.val == "capability":
                mod.caps.append(self.parse_capability())
            elif t.val == "struct":
                s = self.parse_struct()
                s.pub = is_pub
                mod.structs.append(s)
            elif t.val == "enum":
                e = self.parse_enum()
                e.pub = is_pub
                mod.enums.append(e)
            elif t.val == "const":
                c = self.parse_const()
                c.pub = is_pub
                mod.consts.append(c)
            elif t.val == "excluded":
                self.next()
                mod.excluded.append(self.expect("string", None, "（出界声明）").val)
            elif t.val == "trait":
                mod.traits.append(self.parse_trait())
            elif t.val == "impl":
                im = self.parse_impl()
                mod.impls.append(im)
                for f in im.funcs:
                    f.name = f"{im.type}_{f.name}"  # 静态派发: 名字按接收者类型混淆
                    mod.funcs.append(f)
            elif t.val in ("command", "foruse"):
                # 语言名声明（`docs/185` §3）。**语法层只收集** —— 与 `choose` 同一条路数。
                # S1 里它唯一的作用是**让词法器消歧**（不带 `let` 的外部块只认声明过的名字）；
                # 真去拉目标语言的编译器是 S2。
                self.next()
                lang = self.expect("ident", None, "（语言名，例如 c / py）").val
                if lang not in mod.ext_langs:
                    mod.ext_langs.append(lang)
            elif (t.val == "let" and self.peek(1).kind == "ident"
                    and self.peek(2).kind == "raw"):
                # `let <语言> { 正文 }`（`docs/185` §3）。词法器已经把 `{…}` 收成**一个**
                # raw token，所以这里看的是 `let` `IDENT` `raw` 三个。
                self.next()
                lang = self.next().val
                r = self.next()
                mod.ext_blocks.append(ExtBlock(lang, r.val, r.line))
            elif t.val in mod.ext_langs and self.peek(1).kind == "raw":
                # 不带 `let` 的写法 —— **只有声明过的语言名走得到这里**（词法器已经按这条
                # 规则决定过要不要收 raw token；这里是同一规则在语法侧的镜像）。
                lang = self.next().val
                r = self.next()
                mod.ext_blocks.append(ExtBlock(lang, r.val, r.line))
            elif t.val == "choose":  # docs/143 §3.2 + docs/175 §3.4: 核心模式
                line = t.line
                self.next()
                w = self.expect("ident", None,
                                "（核心模式的取值：std / no_std / gc_manual / gc_auto）")
                # **记录每个出现位置, 判定放到 check()** —— 与 `extern` 的重名同一条路数:
                # 语法层只收集, 规则集中在一处, 两个实现要对齐的也就只有那一处。
                # 值属于**哪一维**由 `CORE_DIM_OF` 查 —— 加一维不必再动这两行。
                # （非核心的词在 `_apply_switches` 那一趟就被吞掉了, 到不了这里。）
                mod.chooses[CORE_DIM_OF[w.val]] = w.val
                mod.choose_lines.append((CORE_DIM_OF[w.val], w.val, line))
            elif t.val == "extern":  # docs/173: 外部函数声明
                self.next()
                f = self.parse_fn(extern=True)
                f.pub = is_pub
                mod.externs.append(f)
            elif t.val == "interrupt":  # M33: 中断处理函数
                self.next()
                f = self.parse_fn()
                f.interrupt = True
                f.pub = is_pub
                mod.funcs.append(f)
            elif t.val == "fn":
                f = self.parse_fn()
                f.pub = is_pub
                mod.funcs.append(f)
            else:
                raise LomError(t.line, t.col, f"未知顶层关键字 {t.val!r}")

    def parse_struct(self) -> Struct:
        kw = self.expect("ident", "struct")
        name = self.ident("结构体名")
        tparams = self.parse_tparams()
        self.expect("punct", "{")
        fields: list[tuple[str, str]] = []
        while not self.at("punct", "}"):
            fn = self.ident("字段名")
            self.expect("punct", ":")
            fields.append((fn, self.type_name()))
            if not self.accept("punct", ","):
                self.accept("punct", ";")
        self.expect("punct", "}")
        return Struct(name, fields, kw.line, False, tparams)

    def parse_enum(self) -> EnumDecl:
        """L1 枚举: 变体可带单载荷 (v1)。"""
        kw = self.expect("ident", "enum")
        name = self.ident("枚举名")
        tparams = self.parse_tparams()
        self.expect("punct", "{")
        variants: list[str] = []
        payloads: dict[str, str] = {}
        while not self.at("punct", "}"):
            v = self.ident("变体名")
            variants.append(v)
            if self.accept("punct", "("):
                payloads[v] = self.type_name()
                self.expect("punct", ")")
            if not self.accept("punct", ","):
                self.accept("punct", ";")
        self.expect("punct", "}")
        return EnumDecl(name, variants, kw.line, payloads, False, tparams)

    def parse_trait(self) -> Trait:
        """trait Name { fn m(self, ...) -> R; ... }"""
        kw = self.expect("ident", "trait")
        name = self.ident("trait 名")
        self.expect("punct", "{")
        methods: list[tuple[str, str]] = []
        while not self.at("punct", "}"):
            self.expect("ident", "fn", "（trait 内只允许 fn）")
            mname = self.ident("方法名")
            self.expect("punct", "(")
            self.expect("ident", "self", "（方法第一个参数必须是 self）")
            while not self.at("punct", ")"):
                self.expect("punct", ",")
                if self.at("punct", ")"):
                    break
                self.ident("参数名")
                self.expect("punct", ":")
                self.type_name()
            self.expect("punct", ")")
            self.expect("punct", "-")
            self.expect("punct", ">")
            ret = self.type_name()
            self.expect("punct", ";")
            methods.append((mname, ret))
        self.expect("punct", "}")
        return Trait(name, methods, kw.line)

    def parse_impl(self) -> Impl:
        """impl Trait for Type { fn m(self, ...) -> R { ... } }"""
        kw = self.expect("ident", "impl")
        tr = self.ident("trait 名")
        self.expect("ident", "for")
        ty = self.type_name()
        self.expect("punct", "{")
        funcs: list[Func] = []
        while not self.at("punct", "}"):
            f = self.parse_fn()
            if not f.params or f.params[0].name != "self":
                raise LomError(f.line, 1, f"impl 方法 {f.name} 第一个参数必须是 self")
            f.params[0].name = "__self"
            f.params[0].type = ty  # 接收者类型
            _rename_self(f.body)
            funcs.append(f)
        self.expect("punct", "}")
        return Impl(tr, ty, funcs, kw.line)

    def parse_const(self) -> ConstDecl:
        kw = self.expect("ident", "const")
        name = self.ident("常量名")
        self.expect("punct", ":")
        ty = self.type_name()
        self.expect("punct", "=")
        val = self.int_lit()
        self.expect("punct", ";")
        return ConstDecl(name, ty, val, kw.line)

    def parse_capability(self) -> Capability:
        kw = self.expect("ident", "capability")
        name = self.ident("能力名")
        self.expect("punct", ":")
        space = self.ident("能力空间")
        self.expect("punct", "[")
        lo = self.int_lit()
        self.expect("punct", ".")
        self.expect("punct", ".")
        hi = self.int_lit()
        self.expect("punct", "]")
        revocable = bool(self.accept("ident", "revocable"))
        return Capability(name, space, lo, hi, revocable, kw.line)

    def parse_fn(self, extern: bool = False) -> Func:
        kw = self.expect("ident", "fn")
        name = self.ident("函数名")
        tparams: list[str] = []
        if self.at("punct", "<"):  # M6: 泛型参数
            self.next()
            while not self.at("punct", ">"):
                tparams.append(self.ident("类型参数"))
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
        self.expect("punct", "(")
        params: list[Param] = []
        while not self.at("punct", ")"):
            pn = self.ident("参数名")
            if pn == "self" and not self.at("punct", ":"):
                params.append(Param("self", ""))  # 类型由 impl 填 (M8)
            else:
                self.expect("punct", ":")
                params.append(Param(pn, self.type_name()))
            if not self.accept("punct", ","):
                break
        self.expect("punct", ")")
        # M16: 无返回类型 = ()。**外部函数没有函数体**, 所以"后面是 `;`"同样表示无返回 ——
        # 只认 `{` 的话 `extern fn f(p: ptr);` 会被当成"缺了 `->`"而报错。
        if self.at("punct", "{") or (extern and self.at("punct", ";")):
            ret = "()"
        else:
            self.expect("punct", "-")
            self.expect("punct", ">")
            ret = self.type_name()
        if extern:
            # 外部函数**没有函数体**, 末尾是分号 (docs/143 §3.1)。泛型参数对它也说不通 ——
            # 单态化要靠看得到源码, 而 extern 的定义根本不在手上。
            if tparams:
                raise LomError(kw.line, kw.col, f"外部函数 {name} 不能带类型参数")
            self.expect("punct", ";", "（外部函数声明末尾要分号）")
            f = Func(name, params, ret, [], kw.line, False, tparams)
            f.extern = True
            return f
        body_at = self.i + 1          # '{' 之后 —— 与自举侧 find_body 同一个起点
        body = self.parse_block()
        f = Func(name, params, ret, body, kw.line, False, tparams)
        f.tok_at, f.tok_end = body_at, self.i - 1   # '}' 的下标（不含）
        return f

    def deeper(self) -> None:
        """进一层嵌套。**超了在这里报出来** —— 不让 Python / C 的栈去当那道闸（`#44`）。"""
        self.depth += 1
        if self.depth > MAX_NEST:
            t = self.peek()
            raise LomError(t.line, t.col,
                           f"嵌套超过 {MAX_NEST} 层 —— 编译器是递归下降的，再深就要把栈"
                           f"用穿了（自举那一边直接段错误，不是诊断）。把中间那几层提成"
                           f"函数，或把这段拆开")

    def parse_block(self) -> list:
        self.deeper()
        try:
            self.expect("punct", "{")
            out = []
            while not self.at("punct", "}"):
                out.append(self.parse_stmt())
            self.expect("punct", "}")
            return out
        finally:
            self.depth -= 1

    def parse_stmt(self):
        t = self.peek()
        if t.val == "guard":  # P4: 能力域守卫
            self.next()
            cap = self.ident("能力名")
            self.expect("punct", "(")
            e = self.parse_expr()
            self.expect("punct", ")")
            self.expect("punct", ";")
            return Guard(cap, e, t.line)
        if t.val == "let":
            self.next()
            name = self.ident("变量名")
            self.expect("punct", ":")
            ty = self.type_name()
            if self.accept("punct", ";"):  # M9: 未初始化声明
                return Let(name, ty, None, t.line)
            self.expect("punct", "=")
            e = self.parse_expr()
            self.expect("punct", ";")
            return Let(name, ty, e, t.line)
        if t.val == "if":
            self.next()
            if self.at("ident", "let"):  # M10: if let 语法糖 -> match
                self.next()
                en = self.ident("枚举名")
                self.expect("punct", ":")
                self.expect("punct", ":")
                vn = self.ident("变体名")
                bind = None
                if self.accept("punct", "("):
                    bind = self.ident("绑定名")
                    self.expect("punct", ")")
                self.expect("punct", "=")
                self.no_struct += 1
                subj = self.parse_expr()
                self.no_struct -= 1
                then = self.parse_block()
                otherwise = []
                if self.at("ident", "else"):
                    self.next()
                    otherwise = self.parse_block()
                return Match(subj, [(EnumPath(en, vn, t.line, bind), then), (None, otherwise)], t.line)
            self.no_struct += 1
            cond = self.parse_expr()
            self.no_struct -= 1
            then_at = self.i + 1              # '{' 之后 —— 与自举侧同一个起点
            then = self.parse_block()
            then_end = self.i - 1             # '}' 的下标（不含）
            otherwise = []
            else_at = 0
            else_end = 0
            if self.at("ident", "else"):
                self.next()
                if self.at("ident", "if"):  # else if 链
                    # **不给跨度**：`else if` 是一条**语句**（`otherwise = [If(...)]`），
                    # 它自己那两支的跨度由它自己的解析填 —— 在这里再包一层，自举侧那一份
                    # 就对不上（它走的是 `stmt()` 递归，没有对应的一层）⇒ 逐字节判据会红。
                    otherwise = [self.parse_stmt()]
                else:
                    else_at = self.i + 1
                    otherwise = self.parse_block()
                    else_end = self.i - 1
            s = If(cond, then, otherwise, t.line)
            s.then_at, s.then_end = then_at, then_end
            s.else_at, s.else_end = else_at, else_end
            return s
        if t.val == "while":
            self.next()
            self.no_struct += 1
            cond = self.parse_expr()
            self.no_struct -= 1
            at = self.i + 1                  # '{' 之后 —— 与自举侧同一个起点
            w = While(cond, self.parse_block(), t.line)
            w.tok_at, w.tok_end = at, self.i - 1
            return w
        if t.val == "for":
            self.next()
            var = self.ident("循环变量")
            self.expect("ident", "in")
            self.no_struct += 1
            lo = self.parse_expr()
            self.expect("punct", ".")
            self.expect("punct", ".")
            hi = self.parse_expr()
            self.no_struct -= 1
            at = self.i + 1                  # '{' 之后
            fl = For(var, lo, hi, self.parse_block(), t.line)
            fl.tok_at, fl.tok_end = at, self.i - 1
            return fl
        if t.val == "match":
            self.next()
            self.no_struct += 1
            subj = self.parse_expr()
            self.no_struct -= 1
            self.expect("punct", "{")
            arms = []
            while not self.at("punct", "}"):
                if self.at("ident", "_"):
                    self.next()
                    pat = None
                else:
                    en = self.ident("枚举名")
                    self.expect("punct", ":")
                    self.expect("punct", ":")
                    vn = self.ident("变体名")
                    bind = None
                    if self.accept("punct", "("):
                        bind = self.ident("绑定名")
                        self.expect("punct", ")")
                    pat = EnumPath(en, vn, t.line, bind)
                self.expect("punct", "=")
                self.expect("punct", ">")
                arms.append((pat, self.parse_block()))
            self.expect("punct", "}")
            return Match(subj, arms, t.line)
        if t.val == "return":
            self.next()
            e = self.parse_expr()
            self.expect("punct", ";")
            return Return(e, t.line)
        # 表达式语句或赋值 (左值: 标识符 / 下标)
        e = self.parse_expr()
        if self.at("punct", "="):
            self.next()
            rhs = self.parse_expr()
            self.expect("punct", ";")
            return Assign(e, rhs, t.line)
        self.expect("punct", ";")
        return ExprStmt(e, t.line)

    # -- 表达式 (优先级爬升)
    def parse_expr(self, min_prec: int = 0):
        # 表达式也递归（括号 / 一元前缀 / 后缀调用），**同一个计数** ——
        # `((((…))))` 与 `if` 套 400 层是同一件事（`#44`）。
        self.deeper()
        try:
            return self._parse_expr(min_prec)
        finally:
            self.depth -= 1

    def _parse_expr(self, min_prec: int = 0):
        left = self.parse_unary()
        while True:
            t = self.peek()
            if t.kind != "punct":
                break
            op = t.val
            nxt = self.peek(1)
            if nxt.kind == "punct" and nxt.val == "=" and t.val in ("=", "!", "<", ">"):
                op = t.val + "="
            elif nxt.kind == "punct" and nxt.val == t.val and t.val in ("&", "|", "<", ">"):
                op = t.val * 2
            if op not in PRECEDENCE or PRECEDENCE[op] < min_prec:
                break
            self.next()
            if len(op) == 2:
                self.next()
            right = self.parse_expr(PRECEDENCE[op] + 1)
            left = Bin(op, left, right, t.line)
        return left

    def parse_unary(self):
        t = self.peek()
        if t.kind == "punct" and t.val == "&" and self.peek(1).kind == "ident" \
                and self.peek(1).val == "mut":
            self.next()
            self.next()
            return Un("&mut", self.parse_unary(), t.line)
        if t.kind == "punct" and t.val in UN_OPS:
            self.next()
            return Un(t.val, self.parse_unary(), t.line)
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.kind == "number":
            return self.parse_postfix(IntLit(self.int_lit(), t.line))
        if t.kind == "string":
            return self.parse_postfix(StrLit(self.next().val, t.line))
        if t.kind == "punct" and t.val == "(":
            self.next()
            e = self.parse_expr()
            self.expect("punct", ")")
            return self.parse_postfix(e)
        if t.kind == "punct" and t.val == "[":
            self.next()
            items = []
            while not self.at("punct", "]"):
                items.append(self.parse_expr())
                if not self.accept("punct", ","):
                    break
            self.expect("punct", "]")
            return self.parse_postfix(ArrayLit(items, t.line))
        if t.kind == "ident":
            if t.val == "true":
                self.next()
                return self.parse_postfix(BoolLit(True, t.line))
            if t.val == "false":
                self.next()
                return self.parse_postfix(BoolLit(False, t.line))
            name = self.next().val
            if self.at("punct", ":") and self.peek(1).kind == "punct" and self.peek(1).val == ":":
                self.next()
                self.next()
                vn = self.ident("变体名")
                if self.at("punct", "("):
                    self.next()
                    arg = self.parse_expr()
                    self.expect("punct", ")")
                    return EnumCtor(name, vn, arg, t.line)
                return EnumPath(name, vn, t.line)
            if self.at("punct", "("):
                self.next()
                args = []
                while not self.at("punct", ")"):
                    args.append(self.parse_expr())
                    if not self.accept("punct", ","):
                        break
                self.expect("punct", ")")
                return self.parse_postfix(Call(name, args, t.line))
            if self.at("punct", "{") and self.no_struct == 0:
                self.next()
                inits: list[tuple[str, object]] = []
                while not self.at("punct", "}"):
                    fn = self.ident("字段名")
                    self.expect("punct", ":")
                    inits.append((fn, self.parse_expr()))
                    if not self.accept("punct", ","):
                        break
                self.expect("punct", "}")
                return StructLit(name, inits, t.line)
            return self.parse_postfix(Ident(name, t.line))
        raise LomError(t.line, t.col, f"期望表达式，得到 {t.val or '<eof>'!r}")

    def parse_postfix(self, e):
        """后缀: 字段访问 a.b / 下标 a[i]"""
        while True:
            if self.at("punct", ".") and self.peek(1).kind == "ident":
                t = self.next()
                fname = self.ident("字段/方法名")
                if self.at("punct", "("):  # M8: 方法调用
                    self.next()
                    args = []
                    while not self.at("punct", ")"):
                        args.append(self.parse_expr())
                        if not self.accept("punct", ","):
                            break
                    self.expect("punct", ")")
                    e = MethodCall(e, fname, args, t.line)
                else:
                    e = FieldAccess(e, fname, t.line)
            elif self.at("punct", "["):
                t = self.next()
                idx = self.parse_expr()
                self.expect("punct", "]")
                e = Index(e, idx, t.line)
            elif self.at("ident", "as"):  # 类型转换 (M16 起需要)
                t = self.next()
                e = Cast(e, self.type_name(), t.line)
            elif self.at("punct", "?"):  # M9: 错误传播
                t = self.next()
                e = Try(e, t.line)
            else:
                return e


# ---------------------------------------------------------------- 单态化 (M6)

def _fnv1a32(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


_WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64,
           "i8": 8, "i16": 16, "i32": 32, "i64": 64}


def _common_type(a: str, b: str) -> str | None:
    """a 与 b 的**公共类型**：两个值都装得下、且不改变任何一个值的那个类型。

    没有就返回 None —— 那才是"要写 `as`"的场合。
    """
    if a not in _WIDTHS or b not in _WIDTHS:
        return None
    sa, sb = a in _SIGNED, b in _SIGNED
    if sa == sb:
        return a if _WIDTHS[a] >= _WIDTHS[b] else b
    u, s = (a, b) if not sa else (b, a)
    return s if _WIDTHS[u] < _WIDTHS[s] else None


def _bit_width(t: str) -> int:
    if t == "bool":
        return 1
    if t in INT_TYPES:
        return int(t[1:])
    return 64  # ptr 等


def _is_generic_type(t: str) -> bool:
    return "<" in t and t.endswith(">")


def _generic_parts(t: str) -> tuple[str, list[str]]:
    base, rest = t.split("<", 1)
    inner = rest[:-1]
    args, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return base.strip(), args


def _display_ty(t: str) -> str:
    """实例名回成**人写的样子**：`Box_[u32]` -> `Box<[u32]>`（`#484`）。

    只认"基名 + `_[…]`"这一种形状 —— 它正是**没有名字规则**的那些实参拼出来的，
    所以只在给用户看的消息里出现。别的实例名（`Outer_Inner__u32`）原样返回：
    拆回去要靠泛型表，而消息里已经有 `it["of"]` / `it["args"]` 可以直接拼。
    """
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)_\[(.*)\]$", t)
    if m:
        return f"{m.group(1)}<{m.group(2)}>"
    return t


def _mangle_name(t: str, depth: int = 0) -> str:
    """泛型类型 -> **实例名**（规则见 `docs/147` §「实例命名」）。

    `_` 连接基名与它的实参；**嵌套层用更长的分隔**（第 `depth` 层用 `depth + 1` 个下划线）。
    于是：

        Box<u32>              -> Box_u32            （一层，与原规则一致）
        Outer<Inner<u32>>     -> Outer_Inner__u32   （里层用两个）
        Outer<Inner, u32>     -> Outer_Inner_u32    （两个实参，**不与上面撞名**）
        Wrap<Outer<Inner<u32>>> -> Wrap_Outer__Inner___u32

    旧规则是 `base + "_" + "_".join(args)`，对嵌套会拼出 `Outer_Inner<u32>` —— 那**不是
    标识符**，于是被形式对象自检拒掉（2026-09-23 补夹具时撞到）。
    """
    if not _is_generic_type(t):
        return t.strip()
    base, args = _generic_parts(t)
    sep = "_" * (depth + 1)
    return base + sep + sep.join(_mangle_name(a, depth + 1) for a in args)


def _replace_type(t: str, mapping: dict[str, str]) -> str:
    """按映射重写类型串 (含泛型实参递归); mapping 可含 "Pair<u32>" -> "Pair_u32"。"""
    if t in mapping:
        return mapping[t]
    if _is_generic_type(t):
        base, args = _generic_parts(t)
        return f"{base}<{', '.join(_replace_type(a, mapping) for a in args)}>"
    if _is_array(t):
        return f"[{_replace_type(_array_elem(t), mapping)}; {_array_len(t)}]"
    if _is_slice(t):
        inner = _replace_type(_slice_elem(t), mapping)
        return f"mut [{inner}]" if _is_mut_slice(t) else f"[{inner}]"
    return t


def _subst_type(t: str, m: dict[str, str]) -> str:
    if t in m:
        return m[t]
    if _is_array(t):
        return f"[{_subst_type(_array_elem(t), m)}; {_array_len(t)}]"
    if _is_slice(t):
        inner = _subst_type(_slice_elem(t), m)
        return f"mut [{inner}]" if _is_mut_slice(t) else f"[{inner}]"
    return t


def _subst_stmts(stmts: list, m: dict[str, str]) -> None:
    for s in stmts:
        if isinstance(s, Let):
            s.type = _subst_type(s.type, m)
        elif isinstance(s, If):
            _subst_stmts(s.then, m)
            _subst_stmts(s.otherwise, m)
        elif isinstance(s, While):
            _subst_stmts(s.body, m)
        elif isinstance(s, For):
            _subst_stmts(s.body, m)
        elif isinstance(s, Match):
            for _, b in s.arms:
                _subst_stmts(b, m)


def _instantiate(gf: Func, m: dict[str, str], name: str) -> Func:
    import copy as _c

    g = _c.deepcopy(gf)
    g.name, g.tparams, g.pub = name, [], True
    for p in g.params:
        p.type = _subst_type(p.type, m)
    g.ret = _subst_type(g.ret, m)
    _subst_stmts(g.body, m)
    return g


def _infer_targs(gf: Func, argtypes: list) -> dict[str, str] | None:
    m: dict[str, str] = {}
    for p, at in zip(gf.params, argtypes):
        if p.type in gf.tparams:
            if at is None:
                continue
            if p.type in m and m[p.type] != at:
                return None
            m[p.type] = at
        elif _is_slice(p.type) and _slice_elem(p.type) in gf.tparams and at is not None:
            if _is_slice(at):
                m[_slice_elem(p.type)] = _slice_elem(at)
            elif _is_array(at):
                m[_slice_elem(p.type)] = _array_elem(at)
    return m if all(t in m for t in gf.tparams) else None


def _mono_expr(e, scope, generics, insts, structs, enums) -> None:
    if isinstance(e, Call):
        for a in e.args:
            _mono_expr(a, scope, generics, insts, structs, enums)
        gf = generics.get(e.name)
        if gf is not None:
            argt = [expr_type(a, scope, {}, structs) for a in e.args]
            m = _infer_targs(gf, argt)
            if m is not None:
                key = (gf.name,) + tuple(m[t] for t in gf.tparams)
                if key not in insts:
                    inst = _instantiate(gf, m, gf.name + "_" + "_".join(m[t] for t in gf.tparams))
                    inst.from_generic = gf.name
                    inst.generic_args = [m[t] for t in gf.tparams]
                    insts[key] = inst
                e.name = insts[key].name
        return
    if isinstance(e, Bin):
        _mono_expr(e.left, scope, generics, insts, structs, enums)
        _mono_expr(e.right, scope, generics, insts, structs, enums)
    elif isinstance(e, Un):
        _mono_expr(e.expr, scope, generics, insts, structs, enums)
    elif isinstance(e, Cast):
        _mono_expr(e.expr, scope, generics, insts, structs, enums)
    elif isinstance(e, Index):
        _mono_expr(e.obj, scope, generics, insts, structs, enums)
        _mono_expr(e.idx, scope, generics, insts, structs, enums)
    elif isinstance(e, FieldAccess):
        _mono_expr(e.obj, scope, generics, insts, structs, enums)
    elif isinstance(e, ArrayLit):
        for it in e.items:
            _mono_expr(it, scope, generics, insts, structs, enums)
    elif isinstance(e, StructLit):
        for _, fe in e.inits:
            _mono_expr(fe, scope, generics, insts, structs, enums)
    elif isinstance(e, EnumCtor):
        _mono_expr(e.arg, scope, generics, insts, structs, enums)


def _mono_stmts(stmts, scope, generics, insts, structs, enums) -> None:
    for s in stmts:
        if isinstance(s, Let):
            if s.expr is not None:
                _mono_expr(s.expr, scope, generics, insts, structs, enums)
            scope[s.name] = s.type
        elif isinstance(s, Assign):
            _mono_expr(s.target, scope, generics, insts, structs, enums)
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, If):
            _mono_expr(s.cond, scope, generics, insts, structs, enums)
            _mono_stmts(s.then, dict(scope), generics, insts, structs, enums)
            _mono_stmts(s.otherwise, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, While):
            _mono_expr(s.cond, scope, generics, insts, structs, enums)
            _mono_stmts(s.body, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, For):
            _mono_expr(s.lo, scope, generics, insts, structs, enums)
            _mono_expr(s.hi, scope, generics, insts, structs, enums)
            bs = dict(scope)
            bs[s.var] = expr_type(s.lo, scope, {}, structs) \
                or expr_type(s.hi, scope, {}, structs) or "u32"
            _mono_stmts(s.body, bs, generics, insts, structs, enums)
        elif isinstance(s, Match):
            _mono_expr(s.subject, scope, generics, insts, structs, enums)
            for _, b in s.arms:
                _mono_stmts(b, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, Return):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, ExprStmt):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, Guard):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)


def _collect_types(mods) -> set[str]:
    refs: set[str] = set()

    def walk_stmts(stmts) -> None:
        for s in stmts:
            if isinstance(s, Let):
                refs.add(s.type)
            elif isinstance(s, If):
                walk_stmts(s.then)
                walk_stmts(s.otherwise)
            elif isinstance(s, While):
                walk_stmts(s.body)
            elif isinstance(s, For):
                walk_stmts(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk_stmts(b)

    for m in mods:
        for f in m.funcs:
            refs.add(f.ret)
            for p in f.params:
                refs.add(p.type)
            walk_stmts(f.body)
        for s in m.structs:
            for _, ft in s.fields:
                refs.add(ft)
        for e in m.enums:
            for _, pt in e.payloads.items():
                refs.add(pt)
    return refs


def _rewrite_types(mods, mapping: dict[str, str]) -> None:
    def walk_stmts(stmts) -> None:
        for s in stmts:
            if isinstance(s, Let):
                s.type = _replace_type(s.type, mapping)
            elif isinstance(s, If):
                walk_stmts(s.then)
                walk_stmts(s.otherwise)
            elif isinstance(s, While):
                walk_stmts(s.body)
            elif isinstance(s, For):
                walk_stmts(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk_stmts(b)

    for m in mods:
        for f in m.funcs:
            f.ret = _replace_type(f.ret, mapping)
            for p in f.params:
                p.type = _replace_type(p.type, mapping)
            # 实例自己的**实参**也要改写：嵌套时它是 `Inner<u32>`，而形式对象要求
            # `instances[].args` 是**合法类型串**（`_type_ok` 不认泛型语法）——
            # 所以它得写成那个实例的名字 `Inner_u32`（2026-09-23 加嵌套支持时撞到）。
            f.generic_args = [_replace_type(a, mapping) for a in f.generic_args]
            walk_stmts(f.body)
        for s in m.structs:
            s.fields = [(fn, _replace_type(ft, mapping)) for fn, ft in s.fields]
            s.generic_args = [_replace_type(a, mapping) for a in s.generic_args]
        for e in m.enums:
            e.payloads = {v: _replace_type(pt, mapping) for v, pt in e.payloads.items()}
            e.generic_args = [_replace_type(a, mapping) for a in e.generic_args]


def _fix_generic_literals(mods, bases: set[str]) -> None:
    """字面量名随 let 注解的实例名改写: Pair { .. } -> Pair_u32 { .. }。"""
    def walk(stmts, scope: dict) -> None:
        for s in stmts:
            if isinstance(s, Let):
                e = s.expr
                if e is None:
                    scope[s.name] = s.type
                    continue
                if isinstance(e, StructLit) and e.name in bases:
                    e.name = s.type
                elif isinstance(e, EnumCtor) and e.enum in bases:
                    e.enum = s.type
                elif isinstance(e, EnumPath) and e.enum in bases:
                    e.enum = s.type
                scope[s.name] = s.type
            elif isinstance(s, If):
                walk(s.then, dict(scope))
                walk(s.otherwise, dict(scope))
            elif isinstance(s, While):
                walk(s.body, dict(scope))
            elif isinstance(s, For):
                walk(s.body, dict(scope))
            elif isinstance(s, Return):
                if isinstance(s.expr, (EnumCtor, EnumPath)) and s.expr.enum in bases:
                    s.expr.enum = f.ret  # 按函数返回类型解析
            elif isinstance(s, Assign):
                if isinstance(s.target, Ident) and isinstance(s.expr, (EnumCtor, EnumPath)) \
                        and s.expr.enum in bases and s.target.name in scope:
                    s.expr.enum = scope[s.target.name]
            elif isinstance(s, Match):
                st = expr_type(s.subject, scope, {}, {})
                for pat, b in s.arms:
                    if pat is not None and pat.enum in bases and st:
                        pat.enum = st  # 模式枚举名随主体实例名改写
                    walk(b, dict(scope))

    for m in mods:
        for f in m.funcs:
            walk(f.body, {p.name: p.type for p in f.params})


def _ds_stmts(stmts, scope, f, funcs_map, structs):
    out = []
    for s in stmts:
        if isinstance(s, Let) and isinstance(s.expr, Try):
            inner = s.expr.expr
            rt = expr_type(inner, scope, funcs_map, structs)
            if rt is None or not rt.startswith("Result_"):
                out.append(s)
                continue
            tmp, v, er = f"__t{s.line}", f"__v{s.line}", f"__e{s.line}"
            out.append(Let(tmp, rt, inner, s.line))
            out.append(Let(s.name, s.type, None, s.line))
            arms = [
                (EnumPath(rt, "Ok", s.line, v),
                 [Assign(Ident(s.name, s.line), Ident(v, s.line), s.line)]),
                (EnumPath(rt, "Err", s.line, er),
                 [Return(EnumCtor(rt, "Err", Ident(er, s.line), s.line), s.line)]),
            ]
            out.append(Match(Ident(tmp, s.line), arms, s.line))
            scope[s.name] = s.type
            continue
        if isinstance(s, If):
            s.then = _ds_stmts(s.then, dict(scope), f, funcs_map, structs)
            s.otherwise = _ds_stmts(s.otherwise, dict(scope), f, funcs_map, structs)
        elif isinstance(s, While):
            s.body = _ds_stmts(s.body, dict(scope), f, funcs_map, structs)
        elif isinstance(s, For):
            s.body = _ds_stmts(s.body, dict(scope), f, funcs_map, structs)
        elif isinstance(s, Match):
            s.arms = [(p, _ds_stmts(b, dict(scope), f, funcs_map, structs)) for p, b in s.arms]
        out.append(s)
    return out


def _desugar_try(mods, funcs_map, structs) -> None:
    """M9: `let x: T = e?;` -> 临时绑定 + match (Err 早退)。"""
    for m in mods:
        for f in m.funcs:
            f.body = _ds_stmts(f.body, {p.name: p.type for p in f.params}, f, funcs_map, structs)


def _has_try(mods) -> bool:
    def scan_e(e) -> bool:
        if isinstance(e, Try):
            return True
        for child in (getattr(e, "left", None), getattr(e, "right", None), getattr(e, "expr", None),
                      getattr(e, "obj", None), getattr(e, "idx", None), getattr(e, "arg", None)):
            if child is not None and scan_e(child):
                return True
        return any(scan_e(a) for a in getattr(e, "args", []) or [])

    def scan_s(stmts) -> bool:
        for s in stmts:
            for e in (getattr(s, "expr", None), getattr(s, "cond", None), getattr(s, "subject", None)):
                if e is not None and scan_e(e):
                    return True
            for key in ("then", "otherwise", "body"):
                sub = getattr(s, key, None)
                if sub and scan_s(sub):
                    return True
            for _, b in getattr(s, "arms", []) or []:
                if scan_s(b):
                    return True
        return False

    return any(scan_s(f.body) for m in mods for f in m.funcs)


def _has_guard(mods) -> bool:
    def scan(stmts) -> bool:
        for s in stmts:
            if isinstance(s, Guard):
                return True
            for key in ("then", "otherwise", "body"):
                sub = getattr(s, key, None)
                if sub and scan(sub):
                    return True
            for _, b in getattr(s, "arms", []) or []:
                if scan(b):
                    return True
        return False

    return any(scan(f.body) for m in mods for f in m.funcs)


def prepare(mod: Module, deps: list[Module] | None = None) -> tuple[Module, list[Module]]:
    """M6/M7: 单态化 —— 泛型函数与泛型类型展开为具体副本 (幂等)。"""
    import copy as _c

    deps = list(deps or [])
    allmods = deps + [mod]
    if not (any(f.tparams for m in allmods for f in m.funcs)
            or any(s.tparams for m in allmods for s in m.structs)
            or any(e.tparams for m in allmods for e in m.enums)
            or any(m.impls for m in allmods)
            or _has_try(allmods)
            or _has_guard(allmods)):
        return mod, deps
    mod, deps = _c.deepcopy(mod), _c.deepcopy(deps)
    allmods = deps + [mod]

    # ---- M7: 泛型类型实例化
    gs = {s.name: s for m in allmods for s in m.structs if s.tparams}
    ge = {e.name: e for m in allmods for e in m.enums if e.tparams}
    if gs or ge:
        made: dict[str, str] = {}
        for _ in range(8):
            refs = sorted(t for t in _collect_types(allmods) if _is_generic_type(t) and t not in made)
            if not refs:
                break
            for ref in refs:
                base, args = _generic_parts(ref)
                name = _mangle_name(ref)
                made[ref] = name
                mp = {t: a for t, a in zip(gs.get(base, ge.get(base)).tparams, args)} if (
                    base in gs or base in ge) else None
                if mp is None:
                    continue
                mp[ref] = name
                if base in gs:
                    g = gs[base]
                    s = Struct(
                        name, [(fn, _replace_type(ft, mp)) for fn, ft in g.fields], g.line, True, [])
                    s.from_generic, s.generic_args = base, list(args)
                    mod.structs.append(s)
                else:
                    g = ge[base]
                    e = EnumDecl(
                        name, list(g.variants), g.line,
                        {v: _replace_type(pt, mp) for v, pt in g.payloads.items()}, True, [])
                    e.from_generic, e.generic_args = base, list(args)
                    mod.enums.append(e)
        _rewrite_types(allmods, made)
        _fix_generic_literals(allmods, set(gs) | set(ge))
        mod.structs = [s for s in mod.structs if not s.tparams]
        mod.enums = [e for e in mod.enums if not e.tparams]
        for m in deps:
            m.structs = [s for s in m.structs if not s.tparams]
            m.enums = [e for e in m.enums if not e.tparams]

    # ---- M6: 泛型函数单态化
    structs_map = {s.name: s for m in allmods for s in m.structs}
    enums_map = {e.name: e for m in allmods for e in m.enums}
    generics = {f.name: f for m in allmods for f in m.funcs if f.tparams}
    if generics:
        insts: dict[tuple, Func] = {}
        walked: set[str] = set()
        for _ in range(8):
            before = len(insts)
            pool = [x for m in allmods for x in m.funcs] + list(insts.values())
            for f in pool:
                if f.tparams or f.name in walked:
                    continue
                walked.add(f.name)
                _mono_stmts(f.body, {p.name: p.type for p in f.params}, generics, insts,
                            structs_map, enums_map)
            if len(insts) == before:
                break
        mod.funcs = [f for f in mod.funcs if not f.tparams] + list(insts.values())
        for m in deps:
            m.funcs = [f for f in m.funcs if not f.tparams]

    funcs_map = {f.name: f for m in allmods for f in m.funcs}
    if any(m.impls for m in allmods):
        _resolve_methods(allmods, funcs_map, structs_map, enums_map)
    if _has_try(allmods):  # M9: ? 语法糖
        _desugar_try(allmods, funcs_map, structs_map)
    if _has_guard(allmods):  # P4: guard 解析
        _resolve_guards(allmods, {c.name: (i, c.lo, c.hi) for i, c in enumerate(mod.caps)})
    return mod, deps


# ---------------------------------------------------------------- 语义检查

def _const_int(e) -> int | None:
    """`-字面量` 的值, 否则 None。

    只用来判一件事：两侧都没有声明类型时，**负字面量**是不是在参与比较/取模/移位
    —— 是就按有符号定宽（`#237` 第二处根）。**故意只认 `- <IntLit>` 这一种形状**：
    自举那侧只扫两个 token 就能判同一个条件，两边因此可证明一致；更复杂的常量式
    （`(0 - 1) < 0`）退回原来的 u32 缺省，不在这里悄悄改变语义。
    """
    if isinstance(e, IntLit):
        return e.value
    if isinstance(e, Un) and e.op == "-" and isinstance(e.expr, IntLit):
        return -e.expr.value
    return None


def expr_type(e, scope: dict[str, str], funcs: dict[str, Func], structs: dict[str, Struct]) -> str | None:
    if isinstance(e, IntLit):
        return None  # 整型字面量: 由上下文定宽
    if isinstance(e, BoolLit):
        return "bool"
    if isinstance(e, StrLit):
        return "str"
    if isinstance(e, Ident):
        return scope.get(e.name)
    if isinstance(e, Call):
        if e.name in BUILTINS:
            return BUILTINS[e.name][1]
        if e.name == "slice_len":
            return "u32"
        return funcs[e.name].ret if e.name in funcs else None
    if isinstance(e, Cast):
        st = expr_type(e.expr, scope, funcs, structs)
        # **空指针字面量**（`0 as ptr`）的类型就是 ptr（`#373`）。漏了这一条，它在
        # **实参位置**被判成"整型字面量"而拒，而 `let pin: ptr = 0 as ptr;` 与
        # `if pin != (0 as ptr)` 又过 —— 同一句只因为位置不同得到两个裁决。
        # 只认字面量 0：发射器对字面量的 ptr 期望印 `null`，非零字面量没有这个含义。
        if e.type == "ptr" and isinstance(e.expr, IntLit) and e.expr.value == 0:
            return "ptr"
        if st == "ptr" and e.type in ("u64", "i64"):  # M67
            return e.type
        if e.type == "ptr" and st in INT_TYPES:  # M83: 整数 -> 指针 (brk/mmap 取内存)
            return "ptr"
        if st is None and isinstance(e.expr, IntLit) and e.type in INT_TYPES:
            return e.type
        if st is not None and (st in INT_TYPES or st == "bool") \
                and (e.type in INT_TYPES or e.type == "bool"):
            return e.type
        return None
    if isinstance(e, MethodCall):
        ot = expr_type(e.obj, scope, funcs, structs)
        fn = funcs.get(f"{ot}_{e.name}") if ot else None
        return fn.ret if fn else None
    if isinstance(e, (EnumPath, EnumCtor)):
        return e.enum
    if isinstance(e, StructLit):
        return e.name if e.name in structs else None
    if isinstance(e, ArrayLit):
        if not e.items:
            return None
        et = None
        for it in e.items:
            t = expr_type(it, scope, funcs, structs)
            if t is not None:
                et = t
                break
        return f"[{et}; {len(e.items)}]" if et else None
    if isinstance(e, Index):
        ot = expr_type(e.obj, scope, funcs, structs)
        if ot and _is_array(ot):
            return _array_elem(ot)
        if ot and _is_slice(ot):
            return _slice_elem(ot)
        return None
    if isinstance(e, FieldAccess):
        ot = expr_type(e.obj, scope, funcs, structs)
        st = structs.get(ot) if ot else None
        if st is None:
            return None
        for fn, ft in st.fields:
            if fn == e.name:
                return ft
        return None
    if isinstance(e, Un):
        inner = expr_type(e.expr, scope, funcs, structs)
        if e.op == "&":  # M3: 数组 -> 只读切片
            return f"[{_array_elem(inner)}]" if inner and _is_array(inner) else None
        if e.op == "&mut":  # M4: 数组 -> 可变切片
            return f"mut [{_array_elem(inner)}]" if inner and _is_array(inner) else None
        if e.op == "!":
            if inner == "bool":
                return "bool"
            if inner in INT_TYPES:
                return inner
            return None
        return inner
    if isinstance(e, Bin):
        lt = expr_type(e.left, scope, funcs, structs)
        rt = expr_type(e.right, scope, funcs, structs)
        if e.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
            return "bool"
        # **移位量不是参与运算的值**：`<<`/`>>` 的右操作数只是个位数，任何整型都收
        # （C、C++、Rust 都这样），结果类型只看左操作数。少了这一条，C++ 写法里
        # 最常见的 `unsigned row >> int i` 会变成"没有公共类型"，而那是完全正常的 C++。
        if e.op in ("<<", ">>"):
            return lt or rt
        # **算术/位运算出的是两侧的公共类型**（`_common_type`），不是左操作数 ——
        # 发射器按同一个函数定型（`Emitter.binop`），两边必须是同一条规则，否则
        # `(x:i32 + y:i64) > 0` 里的加法在 64 位、外层比较又退回 32 位（`#133`）。
        # 有一侧是字面量（None）时维持原样：它按上下文定宽。
        return _common_type(lt, rt) or lt or rt
    return None


def _borrows_of(e, out: dict[str, set[str]]) -> None:
    """M5: 收集表达式里的借用 (变量 -> {"&","&mut"})。"""
    if isinstance(e, Un) and e.op in ("&", "&mut") and isinstance(e.expr, Ident):
        out.setdefault(e.expr.name, set()).add(e.op)
        return
    if isinstance(e, Call):
        for a in e.args:
            _borrows_of(a, out)
    elif isinstance(e, Bin):
        _borrows_of(e.left, out)
        _borrows_of(e.right, out)
    elif isinstance(e, Un):
        _borrows_of(e.expr, out)
    elif isinstance(e, Index):
        _borrows_of(e.obj, out)
        _borrows_of(e.idx, out)
    elif isinstance(e, FieldAccess):
        _borrows_of(e.obj, out)
    elif isinstance(e, ArrayLit):
        for it in e.items:
            _borrows_of(it, out)


def _check_borrows(args: list, line: int, errs: list[str]) -> None:
    """M5 借用检查 v0: 同一次调用里同一变量不得既借又可变借, 或可变借两次。"""
    b: dict[str, set[str]] = {}
    for a in args:
        _borrows_of(a, b)
    for name, kinds in b.items():
        if len(kinds) > 1:
            errs.append(f"{line}: 变量 {name} 在同一次调用里既被可变借用又被借用")
        elif kinds == {"&mut"}:
            n_mut = sum(1 for a in args if isinstance(a, Un) and a.op == "&mut"
                        and isinstance(a.expr, Ident) and a.expr.name == name)
            if n_mut > 1:
                errs.append(f"{line}: 变量 {name} 被可变借用两次 (不允许别名)")


def _walk_expr(e, scope: dict[str, str], funcs: dict[str, Func], structs: dict[str, Struct],
               errs: list[str], enums: dict[str, EnumDecl] | None = None) -> None:
    """结构性问题 (未声明变量 / 未知函数 / 结构体字段 / 枚举变体) 的递归报告。

    **这里没有档参数**（2026-10-16）：`lms` 曾经只喂 `Index` 那一支的 E028，而那条
    已经**升成语言规则**（`docs/205` §1.1 C5：字面量越界编译期拒）—— 见下面那一段的注解。
    另一个第 1 级检查（E029 用完再放）在 `_move_check` 里，它自己的参数自己带。
    """
    enums = enums or {}
    if isinstance(e, (IntLit, BoolLit, StrLit)):
        return
    if isinstance(e, Ident):
        if e.name not in scope:
            errs.append(f"{e.line}: 使用未声明的变量 {e.name}")
        return
    if isinstance(e, EnumPath):
        if e.enum not in enums:
            errs.append(f"{e.line}: 未知枚举 {e.enum}")
        elif e.variant not in enums[e.enum].variants:
            errs.append(f"{e.line}: 枚举 {e.enum} 无变体 {e.variant}")
        return
    if isinstance(e, EnumCtor):
        ed = enums.get(e.enum)
        if ed is None:
            errs.append(f"{e.line}: 未知枚举 {e.enum}")
        elif e.variant not in ed.variants:
            errs.append(f"{e.line}: 枚举 {e.enum} 无变体 {e.variant}")
        elif e.variant not in ed.payloads:
            errs.append(f"{e.line}: 变体 {e.enum}::{e.variant} 无载荷，不能带参数")
        else:
            at = expr_type(e.arg, scope, funcs, structs)
            pt = ed.payloads[e.variant]
            if at is not None and at != pt:
                errs.append(f"{e.line}: 载荷类型 {at}，声明为 {pt}")
        _walk_expr(e.arg, scope, funcs, structs, errs, enums)
        return
    if isinstance(e, Cast):
        _walk_expr(e.expr, scope, funcs, structs, errs, enums)
        st = expr_type(e.expr, scope, funcs, structs)
        if st == "ptr" and e.type in ("u64", "i64"):  # M67: 指针转整数
            return
        if e.type == "ptr" and st in INT_TYPES:  # M83: 整数 -> 指针 (brk/mmap 取内存)
            return
        if st is None and isinstance(e.expr, IntLit):  # 整型字面量按目标定宽
            # `ptr` 也算: 字面量的类型是 None, 所以上面那条 `e.type == "ptr" and st in
            # INT_TYPES` 够不着它 —— 于是 `0 as ptr`(空指针的惯用写法) 被当成非法目标。
            # 镜那边 (selfhost/checker.lomt, 同处按 `chk_ty_is_int`) 一直是放行的, 于是
            # 同一份源码**参考报错、打包版能编**。2026-09-15 由 lompi 的 `0 as ptr` 实测抓到。
            if e.type not in INT_TYPES and e.type != "ptr":
                errs.append(f"{e.line}: as 目标类型非法 {e.type}")
            return
        if st is None or not (st in INT_TYPES or st == "bool"):
            errs.append(f"{e.line}: as 只能作用于整型/布尔 (得到 {st})")
        elif e.type not in INT_TYPES and e.type != "bool":
            errs.append(f"{e.line}: as 目标类型非法 {e.type}")
        return
    if isinstance(e, Try):
        errs.append(f"{e.line}: ? 只能用于 let 绑定 (let x: T = e?;)")
        return
    if isinstance(e, MethodCall):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        fn = funcs.get(f"{ot}_{e.name}") if ot else None
        if fn is None:
            errs.append(f"{e.line}: 类型 {ot} 没有方法 {e.name}（需要 impl）")
        elif len(e.args) + 1 != len(fn.params):
            errs.append(f"{e.line}: 方法 {e.name} 需要 {len(fn.params) - 1} 个实参，得到 {len(e.args)}")
        for a in e.args:
            _walk_expr(a, scope, funcs, structs, errs, enums)
        return
    if isinstance(e, Call):
        if e.name == "slice_len":  # M3: 切片长度
            if len(e.args) != 1:
                errs.append(f"{e.line}: slice_len 需要 1 个实参，得到 {len(e.args)}")
            else:
                at = expr_type(e.args[0], scope, funcs, structs)
                if at is not None and not (_is_slice(at) or _is_array(at)):
                    errs.append(f"{e.line}: slice_len 实参 {at} 不是数组或切片")
            for a in e.args:
                _walk_expr(a, scope, funcs, structs, errs, enums)
            return
        if e.name in BUILTINS:
            want_t, _ret = BUILTINS[e.name]
            if len(e.args) != len(want_t):
                errs.append(f"{e.line}: 内建 {e.name} 需要 {len(want_t)} 个实参，得到 {len(e.args)}")
            else:
                for a, wt in zip(e.args, want_t):
                    at = expr_type(a, scope, funcs, structs)
                    if at is None:
                        if wt not in INT_TYPES:
                            errs.append(f"{e.line}: 内建 {e.name} 实参期望 {wt}，得到整型字面量")
                    elif at != wt:
                        errs.append(f"{e.line}: 内建 {e.name} 实参类型 {at}，期望 {wt}")
                # M84 （docs/245）: 入口那三条 —— **是字面量**、**名字合法**、**是本单元里
                # 恰好一个形参的函数**。三条都在编译期判得出来，那正是"直接调用"买到的东西：
                # 产物里写的是一条写死的调用边（`call @名字`），不是运行期取得到的值。
                # 形参个数那一条不是洁癖 —— 子线程只压**一个**实参，入口要是声明了两个，
                # 第二个读到的是栈上的垃圾，那是**静默错编**，比报错坏得多。
                # （消息措辞是按 `loment_diag.RULES` 的正则挑的：分类只看消息，不看调用点。）
                if e.name == "thread_spawn" and not isinstance(e.args[0], StrLit):
                    errs.append(
                        f"{e.line}: 内建 thread_spawn 实参 1 必须是**字符串字面量** —— 它是"
                        f"编译期的名字（产物里是一条直接调用边），不是运行期取得到的值")
                elif e.name == "thread_spawn":
                    ent = e.args[0].value
                    if not _is_fn_name(ent):
                        errs.append(
                            f"{e.line}: 内建 thread_spawn 实参 1 不是合法的函数名: {ent!r}")
                    elif ent not in funcs:
                        errs.append(
                            f"{e.line}: thread_spawn 的入口 {ent} 是未定义的函数"
                            f"（入口必须是本单元里写着的、能被直接调用的 Loment 函数）")
                    elif len(funcs[ent].params) != 1:
                        errs.append(
                            f"{e.line}: thread_spawn 的入口 {ent} 需要 1 个实参，"
                            f"得到 {len(funcs[ent].params)}（子线程只压一个槽）")
        elif e.name not in funcs:
            errs.append(f"{e.line}: 调用未定义的函数 {e.name}（跨模块调用需要 pub）")
        else:
            params = funcs[e.name].params
            _check_borrows(e.args, e.line, errs)  # M5
            if len(e.args) != len(params):
                errs.append(f"{e.line}: {e.name} 需要 {len(params)} 个实参，得到 {len(e.args)}")
            else:
                for a, p in zip(e.args, params):
                    at = expr_type(a, scope, funcs, structs)
                    if at is None:
                        if p.type not in INT_TYPES and not _is_slice(p.type):
                            errs.append(f"{e.line}: {e.name} 实参期望 {p.type}，得到整型字面量")
                    elif at == p.type:
                        pass
                    elif _is_slice(p.type) and _is_array(at) and _slice_elem(p.type) == _array_elem(at):
                        pass  # 数组 -> 切片 协变 (M3)
                    elif _is_slice(p.type) and _is_slice(at) and _slice_elem(p.type) == _slice_elem(at):
                        if _is_mut_slice(p.type) and not _is_mut_slice(at):
                            errs.append(f"{e.line}: {e.name} 形参要求可变切片，实参是只读切片")
                        # 可变 -> 只读 允许
                    else:
                        errs.append(f"{e.line}: {e.name} 实参类型 {at}，期望 {p.type}")
        for a in e.args:
            _walk_expr(a, scope, funcs, structs, errs, enums)
        return
    if isinstance(e, StructLit):
        st = structs.get(e.name)
        if st is None:
            errs.append(f"{e.line}: 未知结构体 {e.name}")
            return
        want = {n: t for n, t in st.fields}
        got: set[str] = set()
        for fn, fe in e.inits:
            if fn not in want:
                errs.append(f"{e.line}: 结构体 {e.name} 无字段 {fn}")
            elif fn in got:
                errs.append(f"{e.line}: 结构体 {e.name} 字段 {fn} 重复初始化")
            got.add(fn)
            _walk_expr(fe, scope, funcs, structs, errs, enums)
        for fn in want:
            if fn not in got:
                errs.append(f"{e.line}: 结构体 {e.name} 缺字段 {fn}")
        return
    if isinstance(e, FieldAccess):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        st = structs.get(ot) if ot else None
        if st is None:
            errs.append(f"{e.line}: 对非结构体类型取字段 .{e.name}")
        elif not any(fn == e.name for fn, _ in st.fields):
            errs.append(f"{e.line}: 结构体 {ot} 无字段 {e.name}")
        return
    if isinstance(e, ArrayLit):
        if not e.items:
            errs.append(f"{e.line}: 数组字面量不能为空")
        ts = []
        for it in e.items:
            _walk_expr(it, scope, funcs, structs, errs, enums)
            ts.append(expr_type(it, scope, funcs, structs))
        concrete = {t for t in ts if t is not None}
        if len(concrete) > 1:
            errs.append(f"{e.line}: 数组字面量元素类型不一致: {sorted(concrete)}")
        elif concrete and any(t is None for t in ts) and not all(t in INT_TYPES for t in concrete):
            errs.append(f"{e.line}: 数组字面量元素类型不一致 (整型字面量混入 {sorted(concrete)[0]})")
        return
    if isinstance(e, Index):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        _walk_expr(e.idx, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        if ot is None or not (_is_array(ot) or _is_slice(ot)):
            errs.append(f"{e.line}: 对非数组/切片类型取下标")
        it = expr_type(e.idx, scope, funcs, structs)
        if it is not None and it not in INT_TYPES:
            errs.append(f"{e.line}: 下标类型 {it}，应为整型")
        # **E028**：**字面量下标越过定长数组的界**当场拒。
        #
        # **2026-10-16 起它不再看 `lms`**（issue #18）。原先这条只在 `choose lms_open`
        # 时发 —— 而 `lms` 那一维是**开发通道的中间态**（`docs/253` §14.2：不进发行版），
        # 于是发行版里的用户拿不到它。可它本来就不是一条 LMS 检查：`docs/205` §1.1 C5
        # 早就把「越界访问在**字面量**时编译期拒」写成语言规则，只是那一格**只覆盖
        # `guard`**（`docs/146` §3：`guard cap(9)` 对域 `[0..4]` 是编译错误）。
        # 同一个"字面量越界"，两个构造两个答案 —— 那正是 issue #18 点名的那处不一致。
        # 于是它升成语言规则：**默认档也拒**（`docs/253` §14.2 那条"默认档一个字的检查都
        # 不加"说的是 LMS 那几级，与这条语言规则不是一回事）。
        #
        # 为什么这一格值得拒：实测（`docs/253` §4.4）：`let x: u32 = a[10]` 配 `[u32; 4]`
        # 发出来的是 `getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 10`，
        # 而 `inbounds` 在 LLVM IR 里**恰是"越界即 UB"的标记**。
        #
        # 只对**定长数组**成立：切片 `[T]` 的长度是运行期的，界不是编译期常量 ——
        # 那一半的裁决见 `docs/205` §1.1 C5（`lms_open` 按站点发内联界检查；默认档
        # 不检查，用 `slice_len` + 一个显式的 `if` 自己挡）。
        if ot is not None and _is_array(ot) and isinstance(e.idx, IntLit):
            n = _array_len(ot)
            if e.idx.value >= n:
                # **码不写进正文**（`#486`）：渲染器在头一行已经印了 `error[E028]`，
                # 正文再写一次就是同一件事印两遍。分类靠的是这句话本身
                # （`loment_diag.RULES` 匹配的是"越过定长数组的界"），不是前缀。
                errs.append(f"{e.line}: 下标 {e.idx.value} 越过定长数组的界"
                            f"（{ot} 的长度是 {n}）")
        return
    if isinstance(e, Un):
        _walk_expr(e.expr, scope, funcs, structs, errs, enums)
        if e.op in ("&", "&mut"):
            inner = expr_type(e.expr, scope, funcs, structs)
            if inner is None or not _is_array(inner):
                errs.append(f"{e.line}: {e.op} 只能作用于数组 (得到 {inner})")
        return
    if isinstance(e, Bin):
        _walk_expr(e.left, scope, funcs, structs, errs, enums)
        _walk_expr(e.right, scope, funcs, structs, errs, enums)
        # **两个整型操作数必须有一个公共类型**（`_common_type`）。没有就点名报 ——
        # 让发射器"挑一个"就是替用户改数：`#133` 里原先挑的是**左操作数**的类型，
        # 于是 `x:i32 + y:i64` 在 32 位里算，同一个子表达式一处等于 2³¹、一处为负。
        # 字面量的类型是 None（按上下文定宽），不参与这条 —— `x:u64 + 1` 照旧。
        lt = expr_type(e.left, scope, funcs, structs)
        rt = expr_type(e.right, scope, funcs, structs)
        if lt in INT_TYPES and rt in INT_TYPES and e.op not in ("<<", ">>") \
                and _common_type(lt, rt) is None:
            errs.append(
                f"{e.line}: 表达式类型 {lt} 与 {rt} 没有公共类型 —— 两个都装不下对方"
                f"（`{lt}` 装不下 `{rt}` 的全部取值，反过来也一样）。"
                f"写 `as` 说清这一处用哪一个：`… as {lt}` 或 `… as {rt}`")
        return


#: `use <名字>` 的搜索根 (相对仓库根, 按序找 `<名字>.lomt`)。
#: **只解析 .lomt** —— 名字形式是给 L1 模块用的; L0 布局 (`*.lom`) 继续走路径形式,
#: 于是不存在"这个名字算 L0 还是 L1"的歧义。顺序: 标准库在前, 后面三个覆盖仓库自身。
#: 这四个根是**仓库自己的纪律** (名字不许重), 见 `resolve_name` 第 3 层。
NAME_ROOTS = ("loment/lib", "loment/examples", "loment/selfhost", "loment/tools")
#: L0 布局的**唯一**后缀。**L1 没有专属后缀** —— 路径形式里"不是 .lom 就是 L1 模块",
#: 所以 `use "x.foo"` 是合法的 L1 导入 (用户 2026-09-16 定)。名字形式找的文件后缀见
#: `L1_EXT`, 那个是可以配的 (`loment.conf`)。
L0_EXT = ".lom"
#: 名字形式 `use <名字>` 找的文件后缀。默认 `.lomt`; 可由工具链旁边的 `loment.conf`
#: 的 `source_ext()` 改写 (见 `source_ext_of`)。
DEFAULT_L1_EXT = ".lomt"

#: 项目本地的依赖目录 —— lompi `plan/install --into deps` 的落点:
#: `<项目根>/deps/<名字>/<名字>.lomt`。项目根 = 入口文件所在目录 (docs/168 §4.1)。
LOCAL_DEPS = "deps"
#: 工具链**随包自带**的库, 按 lompi 的 store 布局放: `<工具目录>/../share/lompi/store/`。
#: 有它, 装在用户机器上 (没有仓库、也没有 `deps/`) 时 `use std` 才命中得了 ——
#: 用户 2026-09-16 定: "像 python/java 那样, 简单方便"。
TOOLCHAIN_STORE = ("share", "lompi", "store")
#: **开发 checkout** 里 store 的位置 —— `<仓根>/lompi/store`。发布包把它拷到
#: `share/lompi/store` 去, 所以那条路 (`TOOLCHAIN_STORE`) 才是给装出来的前缀用的;
#: 这一条是给"在本仓里编译"用的 (见 `store_roots`)。
STORE_IN_REPO = Path("lompi") / "store"
#: 单个文件的 `use` 条数上限 (路径形式 + 名字形式合起来算)。
#: **超限报错, 绝不静默丢** —— 自举镜的暂存区原先按 8 条布局, 超出的直接丢掉, 而参考
#: 实现无上限: 同一份源码两个实现给出**不同的单元** (2026-09-16 发现, 语料里没有超过
#: 3 条的用例, 所以一直没暴露)。抬到 300 是让 std 那种多模块门面装得下。
MAX_USE = 300


def _conf_ext_from_tokens(toks: list) -> str | None:
    """从词法流里取 `source_ext` 之后**第一个字符串字面量** (去掉两端引号)。

    用**词法器**而不是解析器, 与 `lompi` 读 `lompi.conf` / `pkg.lomp` 的做法一致 (它那里是
    `lex_find_label`): 配置文件里常常是一堆注释加一行标签, 为它套一个完整 parser 不值得,
    而且"注释里的同名字符串误命中"这件事词法器天然就不会犯。**自举镜用同一条规则**
    (`driver.lomt:cfg_source_ext`), 两边必须一起改。
    """
    for i, t in enumerate(toks):
        if t.kind != "ident" or t.val != "source_ext":
            continue
        for t2 in toks[i + 1:]:
            if t2.kind == "string":
                v = t2.val          # 这里的 `val` 已经去掉引号 (自举镜的 token 是**原始跨度**,
                                    # 含引号, 它那边自己剥 —— 两边的差别只在这一层, 别互相抄)
                if not v.startswith(".") or "\\" in v:
                    return None     # 不以 `.` 开头 = 没配; 带转义的也拒 —— 后缀是文件名的一段,
                                    # 不该有转义, 而"转义解不解"正是两个实现最容易分叉的地方
                return v
        return None      # 有标签但后面没有字符串 = 没配
    return None


#: `loment` **官方**的命令名（`docs/223` §3.3）：注册成这些名字的命令**直接拒**。
#:
#: **为什么编译器要管这件事**：启动器是"官方优先"的（`docs/169` §3b）—— `loment version`
#: 永远走官方那份，`PATH` 上的 `loment-version` 顶不掉。所以撞名的命令**永远跑不到**：
#: 编得过、装得上、敲了没反应。用户 2026-10-09 定的处置是**编的时候就拦住**
#: （"撞官方确定的名称直接报错，这是非法 token，不能被编译构建"）。
#:
#: **这份表是手抄的一份 38 条清单，所以有一条判据钉着它**（`tools/loment_register_test.py`：
#: 逐条等于 `loment commands` 打出来的那批）。官方加一条命令而这里没跟上 -> 那条红。
#: 自举镜在 `loment/selfhost/driver.lomt` 的 `cmd_reserved`，**同一份表，两处一起改**。
RESERVED_COMMANDS = (
    "about", "build", "builtins", "caps", "cat", "check", "cheat", "codes", "color",
    "commands", "count", "doc", "doctor", "env", "example", "examples", "explain", "fmt",
    "fns", "grep", "hash", "help", "ir", "keywords", "ls", "lsp", "new", "run", "skill",
    "stat", "syntax", "todo", "tokens", "tools", "tree", "types", "version", "where",
)


def valid_command_name(name: str) -> bool:
    """命令名合不合法（`docs/223` §3.4：名字是**平的**）。

    启动器按**文件名**找命令（`loment mycmd` -> `PATH` 上的 `loment-mycmd`），所以名字里
    出现路径分隔符、空格、或什么都不能有——拼出来的那个文件名要么指到别处、要么根本不是
    一个可靠的路径。只收 `[A-Za-z0-9_-]`，非空、带长度上限（与 `PATH` 上的文件名同一条规矩）。
    """
    if not name or len(name) > 64:
        return False
    return all(c.isascii() and (c.isalnum() or c in "-_") for c in name)


def _brace_close(toks: list, b: int) -> int:
    """与 `toks[b]`（一个 `{`）**配对**的那个 `}` 的下标；配不上返回 -1。"""
    depth = 1
    j = b + 1
    n = len(toks)
    while j < n and depth > 0:
        if toks[j].kind == "punct":
            if toks[j].val == "{":
                depth += 1
            elif toks[j].val == "}":
                depth -= 1
        j += 1
    return j - 1 if depth == 0 else -1


def _reg_one_fn(toks: list, b: int) -> bool:
    """`register <名字> { … }` 那个块的形状：**恰好一个函数**（`docs/223` §3.3）。

    **为什么钉死这一条**（与自举镜 `lex_reg_one_fn` 同源）：块那层壳对"按深度走"的读者
    （`potato` 收函数那两趟，将来还会有）是**可见**的，而语义上它是**透明**的（块里的函数
    就是顶层函数 —— 参考实现按 AST 收，根本没有这一层）。钉成"只有一个函数"之后，需要
    "看穿这层壳"的地方就只剩**函数**那两处；别的条目（`const` / `impl` / `use`）**进不来** ——
    它们会被判成形状不对，而不是在某一侧被悄悄丢掉。

    `toks[b]` 是块的 `{`。
    """
    n = len(toks)
    f = b + 1
    if f < n and toks[f].kind == "ident" and toks[f].val == "pub":
        f += 1
    if f >= n or toks[f].kind != "ident" or toks[f].val != "fn":
        return False                       # 块里第一样东西不是函数
    e = _brace_close(toks, b)
    if e < 0:
        return False                       # 块没有收尾
    body = f + 1
    while body < n and not (toks[body].kind == "punct" and toks[body].val == "{"):
        if toks[body].kind == "punct" and toks[body].val == ";":
            return False                   # 签名式（没有体）—— 命令体必须有体
        body += 1
    if body >= n:
        return False
    # 函数体的 `}` **紧贴**块的 `}`（块里除它之外什么都没有，前面只有可选的 `pub`）——
    # 注意不是"同一个 `}`"：块的收尾在函数体收尾的**后一格**。
    return _brace_close(toks, body) == e - 1


def command_label_from_tokens(toks: list) -> tuple[int, "str | None", str]:
    """从词法流里取**命令声明**（`docs/223`），两种写法：

      * 形态 B：`pub fn loment_command() -> str { return "x"; }`
      * 形态 A：`register x { … }` —— 名字就在语法里，块里是这条命令的条目

    返回 `(行号, 名字, 拒绝的原因)`：

      * 没写这个标签 -> `(0, None, "")` —— 这个单元不是一条命令，一切照旧；
      * 写了、名字取出来了 -> `(行, "x", "")`；
      * 写了但形状不对 -> `(行, None, 原因)` —— **不能当成"没声明"**：那样写错一个字母
        就会静默地编出一个普通的 `_start` 程序，而这正是本仓反复要消灭的东西。

    **读法与 `_conf_ext_from_tokens` 同源**（词法器扫标签，不套完整 parser）。比它多两条
    约束，都是"在真实单元里不误命中"必须的：

      * 标签后面**必须紧跟 `(`** —— 命令声明是个**函数**。不加这条的话，一个叫 `command`
        的变量后面随便哪个字符串都会被当成命令名；
      * 名字取**函数体顶层** `return` 后面紧跟的那个字符串字面量 —— 不是"体里第一个
        字面量"。差别实测得到：`let s: str = "x"; return s;` 那种写法在松规则下会**扫到
        `"x"` 并当成命令名**，而它根本不是常量（名字是**扫**出来的，不是算出来的）。宁拒勿猜。

    形态 A 那边只多一条约束：`register` 后面必须**真的是一个名字**（`fn register(…)` 那种
    下一个 token 是 `(`，不是本构造，直接放过）—— 误命中的面因此只剩 `register <名字>`。
    **不做深度判断**：扫出第一个就停，于是块里再套一个 `register` 自然被忽略（只认最外层
    那个），两个实现因此不必各写一套嵌套规则。

    **自举镜用同一条规则**（`loment/selfhost/lexer.lomt` 的 `lex_cmd_find`），两边必须一起改
    —— 与 `source_ext` 那条棘轮同源（`docs/158`：「两个实现的读法必须逐字节同源」）。
    """
    for i, t in enumerate(toks):
        if t.kind != "ident":
            continue
        if t.val == "register":  # 形态 A
            if i + 1 >= len(toks):
                continue
            n1 = toks[i + 1]
            if n1.kind == "ident":
                name = n1.val
            elif n1.kind == "string":
                # 名字里要带 `-`（合法名字的字符集是 `[A-Za-z0-9_-]`）就得写字符串 ——
                # `val` 已经去掉引号（自举镜那边是原始跨度、它自己剥）。
                name = n1.val
            else:
                continue          # `fn register(…)` 那种：下一个不是名字，不是本构造
            if i + 2 >= len(toks) or toks[i + 2].kind != "punct" or toks[i + 2].val != "{":
                return t.line, None, "名字后面要跟 `{`"
            if not _reg_one_fn(toks, i + 2):
                return t.line, None, "块里要**恰好一个函数**"
            return t.line, name, ""
        if t.val != "loment_command":
            continue
        if i + 1 >= len(toks) or toks[i + 1].kind != "punct" or toks[i + 1].val != "(":
            continue
        j = i + 2
        while j < len(toks) and not (toks[j].kind == "punct" and toks[j].val == "{"):
            if toks[j].kind == "punct" and toks[j].val == ";":
                return t.line, None, "没有函数体"
            j += 1
        if j >= len(toks):
            return t.line, None, "没有函数体"
        depth = 1
        k = j + 1
        while k < len(toks) and depth > 0:
            tk = toks[k]
            if tk.kind == "punct" and tk.val == "{":
                depth += 1
            elif tk.kind == "punct" and tk.val == "}":
                depth -= 1
            elif (depth == 1 and tk.kind == "ident" and tk.val == "return"
                    and k + 1 < len(toks) and toks[k + 1].kind == "string"):
                # `val` 已经去掉引号（自举镜那边是原始跨度、含引号，它自己剥 —— 这个差别
                # 只在这一层，别互相抄，与 `_conf_ext_from_tokens` 那条注解同源）。
                return t.line, toks[k + 1].val, ""
            k += 1
        return t.line, None, "函数体里没有 `return \"…\";`"
    return 0, None, ""


def source_ext_of(proj: Path | None, tool_dir: Path | None) -> str:
    """名字形式 `use <名字>` 找的文件后缀: **项目自己那份 `loment.conf` 优先**, 再工具链
    旁边那份, 都没配就是 `DEFAULT_L1_EXT`（`.lomt`）。

    配置就是**一份 Loment 源码** —— 与 `lompi.conf` / `pkg.lomp` 同一种形状, 只认一个标签:

        pub fn source_ext() -> str { return ".foo"; }

    读不出来（没文件 / 没这个标签 / 后面不是字符串）一律当没配 —— 配置文件坏掉不该让编译
    炸。后缀必须以 `.` 开头, 否则也当没配: 写错一个字母就把名字形式指到一堆奇怪的文件上,
    不如退回默认那个。
    """
    for d in (proj, tool_dir):
        if d is None:
            continue
        conf = d / "loment.conf"
        if not conf.is_file():
            continue
        try:
            ext = _conf_ext_from_tokens(lomc.lex(conf.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001  (读不动 = 没配, 不是编译错误)
            continue
        if ext:
            return ext
    return DEFAULT_L1_EXT


def _ext_chain(ext: str) -> tuple[str, ...]:
    """候选后缀, 按优先级。配了自定义后缀就**再兜一个默认 `.lomt`** —— 项目把自己的源
    改成 `.foo` 之后，工具链自带的那些模块仍然是 `.lomt`，名字形式得两个都找得到。"""
    return (ext,) if ext == DEFAULT_L1_EXT else (ext, DEFAULT_L1_EXT)


def _store_pick(store: Path, name: str, ext: str) -> Path | None:
    """自带 store 里取 `<store>/<名字>/<版本>/<名字><后缀>`; 没有这个包返回 None。

    版本那一层**只允许一个**: 编译器不做版本选择 (docs/168 §4.2 —— 多版本是"物化"那层
    解决的, 编译器完全不需要知道版本存在)。有多个就明说并指向 `deps/`, 不猜。
    """
    pkg = store / name
    if not pkg.is_dir():
        return None
    vers = sorted(d.name for d in pkg.iterdir() if d.is_dir())
    if not vers:
        return None
    if len(vers) > 1:
        raise LomError(1, 1, f"自带的库里 {name} 有 {len(vers)} 个版本 ({', '.join(vers)})"
                             f" —— 编译器不做版本选择, 要指定版本就用 deps/{name}/")
    return pkg / vers[0] / f"{name}{ext}"


def _pkg_module_hits(base: Path | None, name: str, exts: tuple[str, ...],
                     versioned: bool) -> list[Path]:
    """`<名字><后缀>` 作为**包内模块**的候选: 遍历 `<base>/*/` 下每一个包。

    包有两种布局, 由 `versioned` 选一种 (与 `deps/` 和 store 各自的形状对齐):

      * `versioned=False` —— `<base>/<包>/<名字><后缀>` (项目本地 `deps/`, lompi 落点)
      * `versioned=True`  —— `<base>/<包>/<版本>/<名字><后缀>` (随包 store)

    **为什么要这一层**: 包是"一个目录", 目录里除了与包同名的那个入口模块, 还有别的模块
    (`std/vec.lomt`、`host/fs.lomt`)。没有这一层, 包内模块**只有一个入口够得着** ——
    而那个入口把整包拖进同一个单元, 于是 128 个模块挤在**平的**发射符号空间里 (冲突)
    且编译代价按模块数的超线性涨 (实测自举侧 n=64 已 187 秒)。按模块取是唯一实用的路。

    多版本包**跳过**: 那是包入口那一层报的事 (编译器不做版本选择), 这里再报一次只会
    把同一件事说两遍。
    """
    if base is None or not base.is_dir():
        return []
    hits: list[Path] = []
    for pkg in sorted(p for p in base.iterdir() if p.is_dir()):
        d = pkg
        if versioned:
            vers = sorted(v for v in pkg.iterdir() if v.is_dir())
            if len(vers) != 1:
                continue
            d = vers[0]
        for e in exts:
            if (d / f"{name}{e}").exists():
                hits.append(d / f"{name}{e}")
                break
    return hits


def store_roots(root: Path, tool_dir: Path | None) -> list[Path]:
    """随包自带 store 的候选根, **按序** (第一个有命中的赢, 与层内"先命中先用"同规矩):

      1. `<工具目录>/../share/lompi/store` —— **装出来的前缀**。`bin/loment` 旁边就是
         `share/lompi/store/`, 这是发布包的形状 (`loment_dist.STORE_DIR`)。
      2. `<仓根>/lompi/store` —— **开发 checkout**。仓里 store 直接在 `lompi/` 下, 不摆
         `share/` 那一层 —— 那一层是打包时拷出来的。没有这一条, 在本仓里写 `use vec`
         一律 E018, 而"本仓能不能用 std"正是加它要回答的问题。

    两条都**必须存在**才进候选 (不存在就跳过), 所以发布包里第 2 条自然落空。
    """
    out: list[Path] = []
    if tool_dir is not None:
        out.append(tool_dir.parent.joinpath(*TOOLCHAIN_STORE))
    out.append(root / STORE_IN_REPO)
    seen: set[Path] = set()
    uniq: list[Path] = []
    for d in out:
        try:
            k = d.resolve()
        except OSError:
            continue
        if k in seen or not d.is_dir():
            continue
        seen.add(k)
        uniq.append(d)
    return uniq


def _in_store_root(importer: Path | None, stores: list[Path]) -> bool:
    """导入方这个文件**是不是住在商店的包里**。见 `resolve_name` 的 §归属。"""
    if importer is None:
        return False
    try:
        p = Path(importer).resolve()
    except OSError:
        return False
    for s in stores:
        try:
            p.relative_to(s.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def resolve_name(name: str, root: Path, proj: Path | None = None,
                 tool_dir: Path | None = None, ext: str = DEFAULT_L1_EXT,
                 importer: Path | None = None) -> Path:
    """`use <名字>` -> 真实文件。**按层搜, 先命中先用** (像 PYTHONPATH):

      1. `<项目根>/deps/<名字>/<名字><后缀>`  —— 项目本地 (lompi materialize 的落点)
      1b. `<项目根>/deps/<包>/<名字><后缀>`   —— 项目本地的**包内模块**
      2. 内置四根 `<仓根>/<根>/<名字><后缀>`  —— **这一层命中必须唯一**
      3. `<store>/<名字>/<版本>/<名字><后缀>`  —— 随包自带 (`store_roots`: 前缀的
         `share/lompi/store`, 或开发 checkout 的 `<仓根>/lompi/store`)
      3b. `<store>/<包>/<版本>/<名字><后缀>`   —— 随包自带的**包内模块**

    **§归属 (2026-09-20): 第 2 层与第 3 层谁在前面, 看导入方自己住在哪边。**

    这是加包内模块那一层时撞出来的, 不是设计的: 商店里同时存在 `mem.lomt`(std 包) 与
    `loment/lib/mem.lomt`(工具链自带), `interp.lomt`(std 包) 与
    `loment/selfhost/interp.lomt`(编译器源码)。**两个方向都撞**:

      * 商店放前面 -> `loment/selfhost/comefor.lomt` 的 `use interp` 拿到 std 那份,
        而那份没有 `CT_HEAP_BYTES`, 驱动直接编不过;
      * 四根放前面 -> `store/host/0.1.0/stat.lomt` 的 `use mem` 拿到 `loment/lib/mem.lomt`,
        而那份没有 `mem_load64`, host 包编不过。

    所以规矩是**"在谁的库里, 先用谁的名字"**: 商店里的文件先在商店里找, 其余文件先在
    四根里找。两边各自内部自洽, 而"用户项目里的 `use`"走的是"先四根"那条 (与加这一层
    之前**完全一致** —— 这一层没有改动任何既有解析结果, 它只补上了原先够不着的那些名字)。

    1b/3b 这两层**命中多处必须报错**, 理由与第 2 层同 —— 两个包里都有 `<名字>.lomt` 时
    "先搜到哪个"不能变成隐藏语义。

    后缀来自 `loment.conf`（`source_ext_of`），默认 `.lomt`；每层都按 `_ext_chain` 的顺序
    试（自定义后缀优先，再兜默认），所以项目换后缀不会把工具链自带的模块弄丢。

    ---- **渠道闸（`libl`）：已放开**（`docs/232` §1.1 / §1.3，2026-10-15）

    这里原先有一条与 `choose vm` 同形的闸：发行版里 `use libl` 直接报错（用户 2026-10-12
    「LibL 仍然在仓库中，和 LVM 一样发行再锁」）。`#358` 把它**放开**了 —— 放开的条件是
    **先把承诺写下来**，那就是 `docs/232` §1.3（哪些名字算数、加是自由的减不是、偏离写在
    模块头注里）。

    所以这一层现在**没有** `libl` 的判断：名字照常解析。**留这段注释是为了让"闸去哪了"
    有据可查** —— 判据那一侧也按棘轮规矩**反转**（`loment_channel_test` 从"发行版拒
    `use libl`"改成"发行版收 `use libl`"），而不是把那条判据删掉。

    （`choose vm` 那条闸**原样留着**：LVM 还没到兑现的时候。）
    """
    exts = _ext_chain(ext)
    stores = store_roots(root, tool_dir)

    def in_store() -> Path | None:
        for store in stores:
            for e in exts:
                f = _store_pick(store, name, e)
                if f is not None:
                    if not f.exists():
                        raise LomError(1, 1, f"自带的库里 {name} 缺同名模块 {name}{e}: {f}")
                    return f
        for store in stores:
            h = _pkg_module_hits(store, name, exts, versioned=True)
            if len(h) == 1:
                return h[0]
            if len(h) > 1:
                rel = ", ".join(x.as_posix() for x in h)
                raise LomError(1, 1, f"名字导入有歧义 {name}: 自带的库里多个包含这个模块 —— "
                                     f"命中 {rel} 多处")
        return None

    def in_roots() -> Path | None:
        hits = [root / rel / f"{name}{e}" for rel in NAME_ROOTS for e in exts
                if (root / rel / f"{name}{e}").exists()]
        # 同一个根里 `.foo` 与 `.lomt` 都在时**只算一次命中** —— 兜底那条不该把"唯一性"
        # 顶成假歧义 (那是"这份源码该用哪个后缀"的问题, 跟"两个根里都有"是两回事)。
        uniq: dict[Path, Path] = {}
        for h in hits:
            uniq.setdefault(h.parent, h)
        hits = sorted(uniq.values())
        if len(hits) > 1:
            rel = ", ".join(str(h.relative_to(root)).replace("\\", "/") for h in hits)
            raise LomError(1, 1, f"名字导入有歧义 {name}: 命中 {rel} 多处")
        return hits[0] if hits else None

    if proj is not None:
        d = proj / LOCAL_DEPS / name
        if d.is_dir():
            for e in exts:
                if (d / f"{name}{e}").exists():
                    return d / f"{name}{e}"
            raise LomError(1, 1, f"依赖 {name} 里没有同名模块 {name}{ext} —— "
                                 f"`use <名字>` 指的是**包内与包同名的那个模块**")
        pk = _pkg_module_hits(proj / LOCAL_DEPS, name, exts, versioned=False)
        if len(pk) == 1:
            return pk[0]
        if len(pk) > 1:
            rel = ", ".join(str(h.relative_to(proj)).replace("\\", "/") for h in pk)
            raise LomError(1, 1, f"名字导入有歧义 {name}: 项目本地有多个包含这个模块 —— "
                                 f"命中 {rel} 多处")
    order = ((in_store, in_roots) if _in_store_root(importer, stores) else (in_roots, in_store))
    for pick in order:
        got = pick()
        if got is not None:
            return got
    where = (f"项目本地 {proj / LOCAL_DEPS} (含包内模块)、内置根 {', '.join(NAME_ROOTS)}、"
             f"以及工具链自带的库 (含包内模块)" if proj is not None
             else f"内置根 {', '.join(NAME_ROOTS)} 与工具链自带的库")
    raise LomError(1, 1, f"名字导入找不到模块 {name}: {where} 下都没有 {name}{ext}")


def prescan_switches(entry: Path, root: Path, proj: Path | None,
                     tool_dir: Path | None, ext: str) -> tuple[SwitchTable, list[Path]]:
    """**两趟装载的第一趟**：只做词法，定出整张开关表（`docs/182` §1.6 ②、§1.10）。

    返回 `(表, addin 目标路径)` —— 后者**不能丢**：`addin` 拉进来的单元不只是"一堆声明"，
    它**本身要作为单元参与编译**（`set choose X { … }` 的体是**代码**，X 开着时那段代码
    就是程序的一部分）。`docs/182` §1.7 把 C2 叫"**跨单元**"就是这个意思。

    **为什么必须有这一趟**（§1.6 ② 那个鸡生蛋）：§2 定的语义是"关着就**在解析之前**
    跳过"，所以开关状态必须在装载**之前**定；而状态来自根单元的 `choose`，`choose` 又按
    名字找 `set choose` 的定义 —— 那份定义可能在 `addin` 进来的单元里，**还没读到**。

    **做法与 `_conf_ext_from_tokens` 同源**：用**词法器**而不是解析器。为它套一个完整
    parser 不值得，而且"注释里的同名标识符误命中"这件事词法器天然就不会犯。

    递归走在 **`addin` 图上**（**不是** `use` 图）—— `addin` 目标里还能有 `addin`。
    **环 = 去重，不是错误**：同一份开关设定套两次是幂等的，与 `use` 复查同处理
    （`resolve_deps` 的 `seen`）。

    这一趟**只收，不摊开也不抹掉** —— 摊开/抹掉是第二趟（`_apply_switches`）的事。
    收的那一段与第二趟**共用同一个函数**（`_collect_switches`）：两处各写一份就是两份
    会漂的实现。
    """
    tbl = SwitchTable()
    seen: set[Path] = set()
    addin_paths: list[Path] = []       # `addin` 目标，按遇到的顺序 —— 它们**要作为单元参与编译**
    n_addin = 0

    def _resolve_addin(name: str, base_dir: Path) -> Path:
        """`addin <名字>` -> 真实文件。**先看同目录，再走 `use` 那套。**

        为什么必须多这一层：`resolve_name` 的四层是 `deps/<名字>/`、工具链 store、
        内置四根 —— **没有一层是"入口文件旁边"**。可 `chooseset.lomt` 恰恰就住在项目根
        （`docs/182` §1.4），只走 `resolve_name` 会把 `addin chooseset` 判成**找不到**。
        """
        for e in _ext_chain(ext):
            cand = base_dir / f"{name}{e}"
            if cand.is_file():
                return cand
        return resolve_name(name, root, proj, tool_dir, ext)

    def scan(path: Path, depth: int) -> None:
        nonlocal n_addin
        rp = path.resolve()
        if rp in seen:
            return
        seen.add(rp)
        if depth >= MAXDEPTH:
            raise LomError(1, 1, f"`addin` 嵌套超过 {MAXDEPTH} 层: {rp} —— "
                                 f"开关设定不该套这么深")
        # **读法由声明决定，所以这一趟也要过前门**（`docs/188` §7.2）。
        #
        # **只接 `load` 是不够的** —— 预扫**自己**读一遍源。2026-09-18 实测：只接了 `load`
        # 的时候，一份 `choose write grammar python` 的 `.lomt` 在**这一趟**被读成
        # "未定义的开关 `write`"（`choose` 是开关关键字，`choose write` = 取开关 `write`），
        # 而那是**命令行**上先撞到的（`lomentc --check`）—— 库那一侧的判据全绿。
        # 这正是 `docs/182` §1.9 那张"读 L1 源的入口"清单的形状：**入口不止一处**。
        from potato_from import front_door          # noqa: PLC0415

        toks = lomc.lex(front_door(rp).source)
        # 嵌套声明在这里也要拦：预扫**只收深度 0**（与第二趟同规则），体里那份定义它
        # 本来就看不见 —— 不在这儿拦下，它会被**静默当成普通代码**。
        _reject_nested_switch_decls(toks)
        # **`addin` 目标只许是 chooseset** —— 而根单元是正常程序，不受这条管。
        if depth > 0:
            _check_chooseset(toks, str(rp))
        _collect_switches(toks, tbl)
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind == "ident" and t.val == "addin" \
                    and i + 1 < len(toks) and toks[i + 1].kind == "ident":
                n_addin = n_addin + 1
                if n_addin > MAX_ADDIN:
                    raise LomError(t.line, t.col, f"`addin` 有 {n_addin} 条, 超过上限 "
                                                  f"{MAX_ADDIN} —— 绝不静默丢")
                nm = toks[i + 1]
                try:
                    tgt = _resolve_addin(nm.val, rp.parent)
                except LomError as e:
                    raise LomError(nm.line, nm.col, f"`addin {nm.val}` 找不到: {e}") from e
                if tgt.resolve() not in seen:
                    addin_paths.append(tgt)
                scan(tgt, depth + 1)
                i = i + 1
            i = i + 1

    scan(Path(entry), 0)
    return tbl, addin_paths


def resolve_deps(mod: Module, root: Path, base: Path, entry: Path | None = None,
                 sw: SwitchTable | None = None,
                 addin_paths: list[Path] | None = None) -> list[Module]:
    """按依赖序返回导入的 L1 模块 (被依赖者在前), 去重 + 循环检测。

    `proj` 是**项目根** (= 入口文件所在目录), 名字形式的第 1 层 `deps/` 相对**它** ——
    不是相对每个文件: 依赖包里的 `use std` 也得找到**项目根**的 `deps/std/`, 因为 lompi
    把整个闭包摊平在项目根的 `deps/` 下 (照每个文件找会变成 `<包>/deps/std`, 永远找不到)。
    """
    order: list[Module] = []
    seen: set[Path] = set()
    stack: set[Path] = set()
    proj = Path(entry).resolve().parent if entry is not None else base
    tool_dir = Path(__file__).resolve().parent
    # 名字形式找什么后缀: 项目自己那份 loment.conf 优先, 再工具链旁边那份, 默认 .lomt
    ext = source_ext_of(proj, tool_dir)
    if entry is not None:
        rp = Path(entry).resolve()
        seen.add(rp)
        stack.add(rp)

    def visit(m: Module, cur_base: Path, depth: int) -> None:
        # **超深度报错, 不静默丢。** 自举侧原先在这里是 `if depth >= MAXDEPTH { return off; }`
        # —— 依赖被悄悄丢掉、编译继续, 而参考实现根本没有上限: 同一份源码, 链深过 8 层时
        # 两个实现给出**不同的单元**。与 `MAX_USE` 那条是同一个形状（那次修了条数, 深度没跟）。
        if depth >= MAXDEPTH:
            raise LomError(1, 1, f"依赖嵌套超过 {MAXDEPTH} 层（在 {m.name} 这一层）—— "
                                 f"拆掉一层中间门面; 上限与自举同值")
        n_use = len(m.imports) + len(m.name_imports)
        if n_use > MAX_USE:
            # 指到**第 MAX_USE+1 条** `use` 那一行：那条正是越界的第一条。
            _ln = m.use_lines[MAX_USE][1] if len(m.use_lines) > MAX_USE else 1
            raise LomError(_ln, 1, f"模块 {m.name} 的 use 有 {n_use} 条, 超过上限 "
                                   f"{MAX_USE} —— 门面拆小, 别把整库塞进一个文件")
        # **每条报错指到那条 `use` 自己的行**（`#480`）：原先一律 `1:1`，caret 落在
        # `module m` 上，而 `use` 可能在几十行之外 —— 用户照着 caret 改不到东西。
        def use_line(text: str) -> int:
            for _t, _ln in m.use_lines:
                if _t == text:
                    return _ln
            return 1
        # 名字形式先落到绝对路径, 之后与路径形式走同一条流水线 (去重/循环/先序)
        # `is_addin` 跟着走 —— 它决定 `check()` 里那份模块算"库"还是算"开关设定"。
        paths: list[tuple[str, bool, str]] = [(p, False, p) for p in m.imports]
        for _n in m.name_imports:
            try:
                _rp = str(resolve_name(_n, root, proj, tool_dir, ext, importer=cur_base))
            except LomError as _e:
                raise LomError(use_line(_n), 1, _e.msg) from None
            paths.append((_rp, False, _n))
        if depth == 0 and addin_paths:
            # `addin` 目标**只在根单元这一层**展开：`addin` 是根单元专属语法，库里的
            # `addin` 已被 `check()` 拒（写了也不会生效 —— 报出来，不静默）。
            paths += [(str(p), True, m.addin_lines[0][0] if m.addin_lines else "") for p in addin_paths]
        for imp, is_addin, _src in paths:
            p = Path(imp)
            cand = p if p.is_absolute() else None
            if cand is None or not cand.exists():
                for base_try in (root, cur_base):
                    q = base_try / imp
                    if q.exists():
                        cand = q
                        break
            if cand is None or not cand.exists():
                raise LomError(use_line(_src) if _src else 1, 1,
                               f"导入的 .lomt 不存在: {imp}")
            rp = cand.resolve()
            if rp in stack:
                raise LomError(use_line(_src) if _src else 1, 1, f"循环导入: {imp}")
            if rp in seen:
                # 边**先记**在 `seen` 之前那道 `continue` 上：同一份文件被两个单元
                # `use`，第二个单元的依赖图里也得有这条边，否则它的体检会少看见它。
                m.dep_ids.append(rp)
                continue
            seen.add(rp)
            stack.add(rp)
            sub = load(rp, sw=sw)
            m.dep_ids.append(_unit_id(sub))
            if is_addin:
                sub.from_addin = True
            visit(sub, rp.parent, depth + 1)
            order.append(sub)
            stack.discard(rp)

    visit(mod, base, 0)
    return order


def load_unit(path: Path, root: Path, lspe_level: int = 3) -> tuple[Module, list[Module]]:
    """**读一个编译单元的唯一入口**：预扫 → 装载 → 解析依赖（`docs/182` §1.10）。

    **别处别再自己拼 `load()` + `resolve_deps()`。** 少了预扫那一趟，`addin` 就白写了 ——
    症状是"未定义的开关"加上 `addin` 目标**根本没进单元**。**实测**：`tools/loment.py`
    的 `ir` 子命令原先就是自己拼的，加 `addin` 之后当场红；而编译链的判据全绿 ——
    和 `docs/182` §1.9 那两条消费者缺口是同一个形状。

    返回 `(根模块, 依赖)`；开关表挂在 `mod.switches`（**整个程序**的那张）。
    """
    tool_dir = Path(__file__).resolve().parent
    ext = source_ext_of(path.parent, tool_dir)
    from lomt_from import NotRepresentable          # noqa: PLC0415
    from potato_from import FrontDoorRefused        # noqa: PLC0415
    try:
        sw, addin_paths = prescan_switches(path, root, path.parent, tool_dir, ext)
        mod = load(path, sw=sw, lspe_level=lspe_level)
        deps = resolve_deps(mod, root, path.parent, entry=path, sw=sw,
                            addin_paths=addin_paths)
    except (FrontDoorRefused, NotRepresentable) as e:
        # **前门拒了这份源**（声明非法 / 这门拼法翻不出来）。它抛的**本来就是给人看的话**，
        # 不是给栈看的 —— 在**这一处**（读单元的**唯一入口**）翻成编译器自己的 `LomError`，
        # 于是**八个调用点**都按既有方式报错，而不是让用户看 traceback。
        # （`docs/182` §1.9：入口不止一处 —— 所以要拦在唯一入口上，而不是逐个调用点补。）
        # **位置传 0**：话里已经写着"第 N 行"了（`read_grammar_decl` 报的）。
        # 传 1:1 是**编一个位置**，而"错要指在错的地方"的反面正是"指到假的地方去"。
        raise LomError(0, 0, str(e)) from None
    return mod, deps


#: **会把运行期拖进产物的构造**（`docs/217` / `docs/175` §3.6）。
#: 键 = token 值（punct 的 `/` `%`，ident 的那六个内建/关键字），值 = 报错时点名的名字。
#:
#: 这张表**刻意长在 token 层**（与 `_l0_promotable` 同一个理由，`docs/210` §2.2）：自举侧
#: 手上只有 token 流，规则写在 token 上，两个实现才可能逐字节一致。
#:
#: 它是**保守的**：一个叫 `alloc` 的用户函数也会被算进来。多报的代价是一次点名拒，
#: 漏报的代价是"声明了 `no_runtime` 而产物里有运行期" —— 那是这一维唯一的失败形状，
#: 两边的代价不对称，所以往保守那边偏。`tools/loment_runtime_test.py` 在全仓语料上核
#: "被拒 ⟺ 产物真带运行期"，把这笔保守账钉成可证伪的。
_RUNTIME_SITES: dict[str, str] = {
    "/": "除法 `/`",
    "%": "取模 `%`",
    "alloc": "`alloc`",
    "free": "`free`",
    "str_concat": "`str_concat`",
    "str_eq": "`str_eq`",
    "panic": "`panic`",
    "guard": "`guard`（能力域守卫）",
}


def _runtime_sites(toks: list) -> list[tuple[str, int]]:
    """扫出本单元里所有**会把运行期拖进产物**的构造（`docs/217`）。

    **为什么是 token 而不是 AST**：同 `_l0_promotable`。自举侧没有 AST，只有 token 流；
    两边要数的必须是同一个东西，所以规则只能写在两边都完整持有的那个表示上。

    **为什么保守**：见 `_RUNTIME_SITES` 的注释 —— 多报一次是点名拒，漏报一次是
    "保证了产物里没有运行期而它其实有"。
    """
    out: list[tuple[str, int]] = []
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind == "punct" and t.val in ("/", "%"):
            out.append((_RUNTIME_SITES[t.val], t.line))
            continue
        if t.kind != "ident" or t.val not in _RUNTIME_SITES:
            continue
        # **只有"用"才触发，名字本身不触发**。这一条是被两处**实测的假阳性**逼出来的：
        #   * 一份 Java 语料的接口单元里有 `pub extern fn guard(...)` —— **方法叫 `guard`**；
        #   * 同一份里还有个**结构体字段** `guard: Guard,`。
        # 两者都只是名字，一个构造都没产生，却都会把产物判成"要运行期"。
        # 收紧成**位置**判据：
        #   * `fn` 后面的那个名字 —— 声明，不算；
        #   * 其余那五个（`alloc`/`free`/`str_concat`/`str_eq`/`panic`）—— **紧跟 `(`** 才算（调用）；
        #   * `guard` —— 语法里它是**语句**（`guard c(0);`），所以**紧跟一个名字**才算；
        #     写成 `guard: Guard` 或 `let g: guard` 都不符合这两条，自然落选。
        prev = toks[i - 1].val if i > 0 else ""
        if prev == "fn":
            continue
        nxt = toks[i + 1] if i + 1 < n else None
        if t.val == "guard":
            hit = nxt is not None and nxt.kind == "ident"
        else:
            hit = nxt is not None and nxt.kind == "punct" and nxt.val == "("
        if hit:
            out.append((_RUNTIME_SITES[t.val], t.line))
    return out

def _unit_id(m: Module) -> object:
    """单元的**身份** —— 判"两份文件是不是同一个单元"用它, **不用名字** (`#139`)。

    同一个文件被 `load()` 两次会得到**两个对象**, 但仍是同一个单元 —— 所以有源文件
    路径就取**规范化后的路径**; 没有路径 (`translated` 那种) 退回模块名。
    """
    if m.src is not None:
        try:
            return m.src.resolve()
        except OSError:
            return str(m.src)
    return m.name


def _unit_where(m: Module) -> str:
    """诊断里指一个单元指到**文件**上 —— 名字相同的两份文件, 只报名字分不开。"""
    return f"`{m.src.as_posix()}`" if m.src is not None else f"`{m.name}`"


#: `#226`：一个约 250 项的**扁平表达式**会让 `check` 死在 Python 的 `RecursionError` 上 ——
#: 三千行 traceback，一个字都不是诊断。两件事一起做：
#:
#: * 上限抬到 8000：普通程序（含这一档）本来就不该撞上；
#: * 真撞上了转成 **E023**（消息里有"嵌套超过"，分类器按修法归到那一码），而不是把
#:   traceback 甩给用户 —— 与 `#369` 那条不变量同源：**Python 的 traceback 不是诊断**。
#:
#: 抬上限不会变成段错误：CPython 3.11 起自己也会在 C 栈见底前抛 `RecursionError`。
_RECURSION_FLOOR = 8000


def check(mod: Module, ext_funcs: dict[str, Func] | None = None,
          deps: list[Module] | None = None, _dep_pass: bool = False,
          lspe_mode: str = "full") -> list[str]:
    if sys.getrecursionlimit() < _RECURSION_FLOOR:
        sys.setrecursionlimit(_RECURSION_FLOOR)
    try:
        return _check(mod, ext_funcs=ext_funcs, deps=deps, _dep_pass=_dep_pass,
                      lspe_mode=lspe_mode)
    except RecursionError:
        raise LomError(0, 0, "嵌套超过编译器的递归上限 —— 把这段拆成几个函数，或把表达式拆开") from None


def _check(mod: Module, ext_funcs: dict[str, Func] | None = None,
           deps: list[Module] | None = None, _dep_pass: bool = False,
           lspe_mode: str = "full") -> list[str]:
    # **依赖的正文也查** (`#149`, 2026-10-09 补)。原先只把 `deps` 的**导出符号**入表
    # (M12: 仅 pub 可见), 正文不验 —— 于是 `pub fn f(p: ptr) -> u32 { return p; }` 这样的库
    # **当依赖时一路绿**, 只有当**入口**查才报。后果: 一个库可以带着正文类型错发布, 而每一个
    # 使用它的程序 check 都是绿的。做法见本函数末尾: 把每个依赖**当成入口**再查一遍
    # (`_dep_pass` 挡住递归)。
    mod, deps = prepare(mod, deps)  # M6 单态化
    errs: list[str] = []
    # **LMS（`docs/253`）**：第 1 级检查器开不开。⚠ **只取根单元**的取值 —— 与 Potato 里
    # 那个 `lms` 字段、以及自举侧扫 token 的那一支 **同一个来源**。
    # 它管**合法性**（外向许可），与 `gc_*`（生命周期、内向可达）**正交**（`docs/210` §6.2）。
    # ⚠ 这一条**长在检查器里，不长在发射器里**：自举侧的 `codegen.lomt` **根本没有报错
    #   通道**（那边所有编译错误都出自 `checker.lomt`）—— 放到发射器里两边就不同构了。
    lms = mod.chooses.get("lms", CORE_DEFAULTS["lms"]) == "lms_open"
    funcs = dict(ext_funcs or {})
    deps = deps or []

    # 依赖模块的导出符号: 先入符号表, 再检查本模块是否重名 (M12: 仅 pub 可见)
    structs: dict[str, Struct] = {}
    enums: dict[str, EnumDecl] = {}
    const_scope: dict[str, str] = {}
    dep_names: set[str] = set()
    dep_private: set[str] = set()
    for d in deps:
        for f in d.funcs:
            (dep_names if f.pub else dep_private).add(f.name)
            if f.pub:
                funcs.setdefault(f.name, f)
        for s in d.structs:
            (dep_names if s.pub else dep_private).add(s.name)
            if s.pub:
                structs.setdefault(s.name, s)
        for e in d.enums:
            (dep_names if e.pub else dep_private).add(e.name)
            if e.pub:
                enums.setdefault(e.name, e)
        for c in d.consts:
            (dep_names if c.pub else dep_private).add(c.name)
            if c.pub:
                const_scope.setdefault(c.name, c.type)

    # ---- 核心模式 `choose` 与**开关** (docs/143 §3.2 + docs/182 §1 + docs/175 §3.4)。
    # 全部规则在这里 —— 语法层只收集, 两个实现要对齐的判断就只有这一处。
    #
    # **核心模式与开关是两类东西**（`docs/182` §1.3）: 核心模式是核心语法的硬写法,
    # 开关是 `set choose …` 那套可定义机制。`_apply_switches` 按名字分好, 这里只判规则。
    #
    # 用户 2026-09-17 改: **`choose` 可以出现至少 500 次**（开关天然是几百个）——
    # 原先那条"`choose` 只能出现一次"**删掉**。它要防的"声明的是整个程序"这件事
    # 现在由**核心模式**那条管。
    # 删掉之后留下的空档由 **同名只许一次** 补上 —— 否则"这个开关到底开没开"没有答案。
    #
    # **按维**判"恰好一次"（`docs/175` §3.0）：从 2026-09-23 起核心模式**不止一维**
    # （`mode` + `gc`），所以"只能声明一次"这条要**逐维**说 —— 两维各一份是合法的，
    # 同一维写两次（不管是同一个值还是两个值）都没有答案，都要报。
    per_dim: dict[str, list[tuple[str, int]]] = {}
    for dim, word, line in mod.choose_lines:
        per_dim.setdefault(dim, []).append((word, line))
    for dim, ws in sorted(per_dim.items()):
        if len(ws) > 1:
            (w0, l0), (w1, l1) = ws[0], ws[1]
            errs.append(f"{l1}: 核心模式这一维只能声明一次 —— "
                        f"{CORE_DIM_ZH[dim]}（第一次在第 {l0} 行，写的是 `{w0}`；"
                        f"这一行写的是 `{w1}`）。它声明的是**整个程序**的这一维。"
                        f"要开关请用 `set choose <名字> {{ … }}`")
    # **互相冲突的取值**（`docs/175` §3.4 ⚠）：`no_std` + `gc_auto` **暂时**报错。
    # 看的是**生效值**（显式写的，或那一维的默认档）—— 因为冲突说的是"这个程序最后
    # 是哪两档"，不是"源码里写了哪两行"。报错要**点名为什么冲突**，泛泛的"非法组合"
    # 正是判据点名的反面（`docs/188` 的"宁拒勿猜"）。
    _eff = dict(CORE_DEFAULTS)
    _eff.update(mod.chooses)
    # **两两查**（不是写死 `(mode, gc)` 那一对）：加一维时这一段一行都不用动。
    # 先后由 `CORE_DIM_ORDER` 定 —— 报错文本要比字节，不能靠 dict/set 的迭代顺序。
    _vals = [_eff[d] for d in CORE_DIM_ORDER]
    for _i in range(len(_vals)):
        for _j in range(_i + 1, len(_vals)):
            _pair = (_vals[_i], _vals[_j])
            _why = CORE_CONFLICTS.get(_pair)
            if _why:
                _ln = next((l for _d, _w, l in reversed(mod.choose_lines)
                            if _w in _pair), 1)
                errs.append(f"{_ln}: `{_pair[0]}` 与 `{_pair[1]}` 不能同时选 —— {_why}")

    # ---- **运行期维是一条保证，不是一句期望**（`docs/217` / `docs/175` §3.6）。
    # `no_runtime`（默认）说的不是"我们希望产物里没有运行期"，而是"**产物里没有运行期**"
    # —— 于是凡是会把它拖进去的构造，**编译期点名拒**（用户 2026-10-09 裁决：按字面做，
    # 规格让路）。这不是"顺手加一条检查"：没有它，`no_runtime` 与 `runtime` 在产物上
    # 没有任何可分辨的差别，"不读源码可判"（`docs/147`）那句就是空的。
    #
    # **默认档也拒**：这一维不写就是 `no_runtime`。所以"老程序一行不改照编"这条从
    # 2026-10-09 起**不再成立** —— 凡用到除法/分配/字符串拼接的程序都要在根单元写一行
    # `choose runtime`。这是**明写的代价**，不是漏掉的一格（`docs/217` §3）。
    if _eff["runtime"] == "no_runtime":
        _sites = mod.rt_sites
        if _sites:
            _name, _line = _sites[0]
            _rest = len(_sites) - 1
            errs.append(
                f"{_line}: 声明了 `no_runtime`，但它会让产物带上运行期 —— {_name}"
                f"（`no_runtime` 是**保证**：产物里不许出现那段运行期）。"
                + (f"这一类构造本单元另有 {_rest} 处。" if _rest else "")
                + "要运行期就在根单元写 `choose runtime`；不要就把这个构造换掉")

    # **渠道闸**（`docs/231` §2）：`vm` 这一维今天**不提供任何东西**（没有字节码、
    # 没有解释器，`docs/231` §1.3）—— 所以它被关在**发行版**外面。用户 2026-10-11 的话：
    # 「不要在发行版 Loment 里被调用，所有调用 VM 的都是非法的，只能 dev 可以被调用，
    # 直到第一个可使用的 LVM 诞生」。
    #
    # **为什么这是一条"闸"而不是一句提醒**：`choose vm` 若在发行版里编得过，它就是
    # **一句用户写得出、却什么也拿不到的承诺** —— 而承诺落空比拒绝更坏。发行版说"不"。
    #
    # **它不看冲突表**：`vm` 与 `no_runtime` 今天确实合不来（VM 要用运行期），但那是
    # 上面那条**既有的**检查接住的（"声明了 `no_runtime` 但它会让产物带上运行期"），
    # 不是一条新写的冲突 —— 少写一条冲突、多复用一条既有规则，与那条自己的口径一致。
    #
    # **次序**：排在运行期那条**之后** —— 自举侧的合并结果就是这个次序
    # （`checker.lomt` 的强保证块在 `eff_vm` 那块之前），两边报文次序才对得上。
    # **`vm` 那条闸 2026-10-17 放开**（用户裁决，LVM 完成一大半）：发行版接受 `choose vm`。
    # 放开之后这个维**今天仍是声明**，不是保证 —— 解释器是仓侧的 `tools/lvm.py` 那一族
    # （Python），发行包里没有它，产物也不走字节码。这一句是这一维的**明写边界**，
    # 与 `no_runtime` 那类维同一个口径：声明先于兑现，但承诺的字面意思不许含糊。

    # **对外端口那一维 2026-10-17 放开**（`#406`）：V1（静态 `.a`，`#359`）与 V2（运行期
    # `.so`，`#360`）两半都落齐了，所以发行版里 `choose hosted` 不再是"一句拿不到的承诺"，
    # 这条闸拆掉。用户 2026-10-12 那句「锁掉 LibC … 因为这尚未完工」的前提已经不成立。
    # **`vm` 那条照旧**（引擎还没写，承诺仍然落空）—— 两扇门是两件事，只开这一扇。
    #
    # 棘轮：`loment_channel_test` 里那条探针**留着**、期望**反转**（发行版必须收它），
    # 与 `use libl` 放开时（`#358`）同一个做法。

    for d in deps:
        if d.from_addin:
            # `addin` 拉的是**开关设定** —— `choose` 正是它存在的理由，所以那两条
            # "库不许"对它**不适用**（`chooseset.lomt` 还常常以 `addin <自己>` 开头）。
            # 但**核心模式**仍然只有根单元能定：它声明的是**整个程序**的取值，
            # addin 单元里写了就是**静默无效**，必须报出来（静默才是敌人）。
            if d.chooses:
                dim0, word0, _line0 = d.choose_lines[0]
                # **行号不写**（`#702`）：这是**依赖文件**里的一行，而诊断记录整体挂在
                # **入口文件**上 —— 把它当入口的行号发出去，就是一个越过入口 EOF 的
                # 位置（`#31` 那个形状）。指不出正确文件就别编一个：消息里点名文件。
                errs.append(f"`addin` 单元不许声明核心模式"
                            f"（在 {_unit_where(d)} / `{d.name}` 里，`{word0}`）—— "
                            f"{CORE_DIM_ZH[dim0]}是整个程序的，只有根单元能定")
            continue
        # **库要声明它的运行期需求**（`docs/217`）：上面那条"根必须满足依赖"只对
        # **声明了**的依赖有效 —— 一个用了除法却什么都不写的库，会在根写着 `no_runtime`
        # 的时候**静默**把运行期带进产物。那句老报错本来就写着"库该声明**能力需求**，
        # 由项目决定"：这一条就是把它兑现。
        if d.rt_sites and d.chooses.get("runtime") != "runtime":
            _n0, _l0 = d.rt_sites[0]
            # 同上（`#702`）：`_l0` 是**依赖**的行号，不是入口的。
            errs.append(
                f"依赖 `{d.name}`（{_unit_where(d)}）用了会把运行期拖进产物的构造"
                f"（{_n0}），却没有声明 —— 库该声明它的**需求**。在 `{d.name}` 里写一行 "
                f"`choose runtime`")
        if d.chooses:
            # **依赖可以声明它的需求**（`docs/217`，用户 2026-10-09 裁决）。从前这里一律
            # "库不许 `choose`" —— 那让一个要除法的库**两面堵死**：当根编要 `choose runtime`，
            # 当依赖又不许写（`loment/lib/proc.lomt` 实测就卡在这儿）。新规矩：依赖的声明
            # **是需求**，根单元的取值**必须满足它**（一致）；不一致才报，报的时候两边都点名。
            _seen: set[str] = set()
            for dim0, word0, _line0 in d.choose_lines:
                if dim0 in _seen:
                    continue        # 同一维写两次是**依赖自己**的错，不在这儿重复报
                _seen.add(dim0)
                if _eff[dim0] != word0:
                    # 位置见上面 `#702`：`_line0` 是依赖文件里的行号，不是入口的。
                    # 措辞保住 `依赖 \`X\` 要求 ` 这个前缀 —— `loment_diag.RULES` 的
                    # E022 按它锚（`test_every_message_the_compiler_can_print_has_a_code`）。
                    errs.append(
                        f"依赖 `{d.name}` 要求 {CORE_DIM_ZH[dim0]}=`{word0}`，"
                        f"而根单元这一维是 `{_eff[dim0]}` —— 依赖的声明是**需求**，"
                        f"根必须满足它（在 {_unit_where(d)} 里）。"
                        f"在根单元写一行 `choose {word0}`")
        if d.addin_lines:
            _nm, _ln = d.addin_lines[0]
            errs.append(f"库不许 `addin`（在 {_unit_where(d)} / `{d.name}` 里，"
                        f"`addin {_nm}`）—— `addin` 是**根单元**专属的开关设定，"
                        f"装载器只走「根 + 根 `addin` 到的单元」那张图，所以库里的 "
                        f"`addin` **不会生效**。库要装代码请用 `use`")

    # ---- 命令声明（`docs/223` 形态 B）：`pub fn loment_command() -> str { return "名字"; }`
    #
    # 一个单元要么是**一条命令**（声明 + `command_main`），要么是普通程序（`_start`）。
    # 三条判断都在这一处 —— 与 `choose` / `addin` 同一层，两个实现要对齐的只有这里。
    for d in deps:
        if d.command_line:
            errs.append(f"库不许声明命令（在 {_unit_where(d)} / `{d.name}` 里）—— 命令是"
                        f"**可执行产物**的身份，而库是给别人 `use` 的代码。"
                        f"把它挪进入口单元")
    if mod.command_line:
        _cline, _cname = mod.command_line, mod.command
        if mod.command_bad:
            errs.append(f"{_cline}: 命令声明的形状不对（{mod.command_bad}）—— 写成块那种"
                        f"`register 名字 {{ … }}`，或者写成**返回一个字面量**的函数："
                        f'`pub fn loment_command() -> str {{ return "名字"; }}`')
        elif _cname in RESERVED_COMMANDS:
            errs.append(f"{_cline}: `{_cname}` 是 loment 的官方命令 —— 启动器**官方优先**"
                        f"（`loment {_cname}` 永远走官方那份，PATH 上的同名文件顶不掉），"
                        f"所以这个名字注册上去**永远跑不到**。换一个名字")
        elif not valid_command_name(_cname):
            errs.append(f"{_cline}: 命令名 `{_cname}` 不合法 —— 启动器按**文件名**找命令"
                        f"（`loment <名>` -> `loment-<名>`），所以名字只能是 "
                        f"`[A-Za-z0-9_-]`、长度 1..64")
    _cm = next((f for f in mod.funcs if f.name == "command_main"), None)
    if mod.command_line and _cm is None:
        # 两半少了一半。这一向是**链接期**才炸的（生成的 `_start` 去调一个不存在的符号），
        # 所以拦在检查这一步 —— 码与"有体没声明"同一个：修法都是把两半凑齐。
        errs.append(f"{mod.command_line}: 声明了命令却没有 `command_main` —— 工具链生成的"
                    f"入口要调它，没有它就得等到**链接**才报错。补上 "
                    f"`fn command_main(argv: ptr, argc: u32) -> u32`，或者删掉那条声明")
    if _cm is not None:
        if not mod.command_line:
            errs.append(f"{_cm.line}: 写了 `command_main` 却没有命令声明 —— 它**不会被"
                        f"调用**（静默失效）。补上 "
                        f'`pub fn loment_command() -> str {{ return "名字"; }}`，'
                        f"或者把入口改回 `_start`")
        _st = next((f for f in mod.funcs if f.name == "_start"), None)
        if _st is not None:
            errs.append(f"{_cm.line}: `_start` 与 `command_main` 同时存在 —— 进程入口只能"
                        f"有一个。声明了命令就由工具链生成入口，删掉 `_start`；"
                        f"不想要命令就删掉那条声明")
        elif [p.type for p in _cm.params] != ["ptr", "u32"] or _cm.ret != "u32":
            errs.append(f"{_cm.line}: `command_main` 的签名不对 —— 入口是 "
                        f"`fn command_main(argv: ptr, argc: u32) -> u32`（拿到的整块缓冲就是"
                        f"`/proc/self/cmdline`，`argv[0]` 是命令自己的路径）")
    sw = mod.switches
    if sw is not None:
        n = len(sw.defs) + len(sw.vals) + sw.ndup
        if n > MAX_CHOOSE:
            errs.append(f"1: `choose` 有 {n} 条, 超过上限 {MAX_CHOOSE} —— 开关太多会把"
                        f"编译期的状态表撑爆; 拆成几个模块, 或改用 `addin`")
        for name, line, first in sorted(sw.dup):
            errs.append(f"{line}: 开关 `{name}` 写了两次（第一次在第 {first} 行）—— "
                        f"同一件事写两遍就没有答案了; 删掉一条")
        for name, (_on, line) in sorted(sw.vals.items()):
            if name not in sw.defs:
                errs.append(f"{line}: 未定义的开关 `{name}` —— 先写 "
                            f"`set choose {name} {{ … }}` 定义它")
        for d in deps:
            if d.from_addin:
                continue        # `addin` 拉的是开关设定 —— 它的 choose 正是用途
            if d.own_switches is not None and (d.own_switches.defs or d.own_switches.vals):
                errs.append(f"1: 库不许 `choose`（在 `{d.name}` 里）")

    # ---- 单元级唯一性 (2026-09-11 补): 发射出来的符号名是**平的**。
    # 内核线按名字找入口 (`_start` / `timer_isr` / syscall 包装, 见 docs/155 §3), 所以
    # 私有符号不能靠 mangling 变成模块限定名 —— 平的名字就是 ABI。代价: 同一单元里两个
    # 模块声明同名顶层符号时, 后端会发出**两条 `define @helper`** (非法 IR), 调用点还会
    # 解析到同一个函数 (静默错编)。以前只有"入口模块 vs 依赖的 pub"会报, 依赖之间的私有
    # 重名一路静默 —— 这里补齐。预置枚举 (Option/Result) 由 load() 注入每个模块, 排除。
    # 同一模块内部的重名由下面各自的规则报, 这里只管跨模块。
    # 闸立在这条**上面**: 单元的**名字**先得唯一。两份文件都写 `module same` 时,
    # 下面那把按名字去重的闸会把它们当成同一个模块 —— 闸不响, 产物里两条
    # `define @f` (非法 IR), 一直拖到链接器才炸成内部异常 (`#139`)。
    seen_unit: dict[object, Module] = {}
    for m0 in [*deps, mod]:
        seen_unit.setdefault(_unit_id(m0), m0)
    seen_name: dict[str, Module] = {}
    dup_unit = False
    for m0 in seen_unit.values():
        prev = seen_name.get(m0.name)
        if prev is None:
            seen_name[m0.name] = m0
        else:
            dup_unit = True
            # 措辞里带"重名" —— 与既有的 E-DUP 口径一致（`loment_diag.RULES` 的 E013
            # 就是按这几个词锚的；换一种说法会让这条消息掉出分类表，
            # `loment_tools_test::test_m64_all_reference_messages_are_classified` 当场红）。
            errs.append(f"1: 单元名 `{m0.name}` 重名 —— {_unit_where(prev)} 与 "
                        f"{_unit_where(m0)} 两份文件都声明了它；单元的发射符号是平的 "
                        f"(ABI)，两份同名单元会把同一个符号定义两遍。改掉其中一个 `module` 名")

    # 名字已经撞了的话下面这条**不再报** —— 那些"同名符号"全是上面那条的症状,
    # 一起倒出来只会把根因埋掉 (第一个错才是根因)。
    if not dup_unit:
        seen_decl: dict[str, Module] = {}   # name -> 先声明它的那个**单元**
        for m0 in [*deps, mod]:
            decls = [(f.name, f.line, "函数") for f in m0.funcs]
            decls += [(s.name, s.line, "结构体") for s in m0.structs]
            decls += [(e.name, e.line, "枚举") for e in m0.enums if not e.from_prelude]
            decls += [(c.name, c.line, "常量") for c in m0.consts]
            for nm, ln, kind in decls:
                prev = seen_decl.get(nm)
                if prev is None:
                    seen_decl[nm] = m0
                elif _unit_id(prev) != _unit_id(m0):
                    # **两边都点名**（`#141`）：原先写"与模块 {prev} 重名", 而那是
                    # **模块名**、不是撞车的那个符号 —— `函数 f 与模块 ma 重名` 让用户
                    # 去调解一个 `f` 与 `ma` 之间的冲突, 而这两个名字不撞车。真正撞车的
                    # 是**两份单元都发射 `f`**, 所以两份模块名与两份文件都写出来,
                    # 而不是只说其中一份。措辞仍留"重名"二字 —— `loment_diag.RULES` 的
                    # E013 按这几个词锚, 换一种说法这条消息会掉出分类表。
                    errs.append(
                        f"{ln}: {kind} `{nm}` 重名 —— {_unit_where(prev)}（模块 "
                        f"`{prev.name}`）与 {_unit_where(m0)}（模块 `{m0.name}`）两个"
                        f"单元都会发射 `{nm}`；单元的发射符号是平的 (ABI)，调用点也解析"
                        f"到同一个函数。改掉其中一个")


    # 结构体: 名字/字段唯一, 类型已声明
    for s in mod.structs:
        if s.name in structs:
            errs.append(f"{s.line}: 结构体 {s.name} 重复定义")
        if s.name in TYPES:
            errs.append(f"{s.line}: 结构体 {s.name} 与基类型同名")
        seen_f: set[str] = set()
        for fn, _ft in s.fields:
            if fn in seen_f:
                errs.append(f"{s.line}: 结构体 {s.name} 字段 {fn} 重复")
            seen_f.add(fn)
        if not s.fields:
            errs.append(f"{s.line}: 结构体 {s.name} 为空")
        structs[s.name] = s
    # 枚举: 名字/变体唯一
    for e in mod.enums:
        if e.name in enums or e.name in structs:
            errs.append(f"{e.line}: 枚举 {e.name} 重复定义")
        if e.name in TYPES:
            errs.append(f"{e.line}: 枚举 {e.name} 与基类型同名")
        seen_v: set[str] = set()
        for v in e.variants:
            if v in seen_v:
                errs.append(f"{e.line}: 枚举 {e.name} 变体 {v} 重复")
            seen_v.add(v)
        if not e.variants:
            errs.append(f"{e.line}: 枚举 {e.name} 为空")
        enums[e.name] = e
    known = set(TYPES) | set(structs) | set(enums)

    for s in mod.structs:
        for fn, ft in s.fields:
            if not _type_ok(ft, known):
                errs.append(f"{s.line}: 未知类型 {ft}（结构体 {s.name}.{fn} 的字段类型）")
    for e in mod.enums:
        for v, pt in e.payloads.items():
            if not _type_ok(pt, known):
                errs.append(f"{e.line}: 未知类型 {pt}（枚举 {e.name}::{v} 的载荷类型）")

    # 常量 (仅整型)
    seen_const: set[str] = set()
    fnames = {f.name for f in mod.funcs} | {x.name for x in mod.externs}
    for c in mod.consts:
        if c.name in seen_const or c.name in structs or c.name in enums or c.name in fnames \
                or c.name in dep_names:
            errs.append(f"{c.line}: 常量 {c.name} 与既有声明重名")
        seen_const.add(c.name)
        if c.type not in INT_TYPES:
            errs.append(f"{c.line}: 常量 {c.name} 类型必须是整型，得到 {c.type}")
        const_scope[c.name] = c.type

    for f in mod.funcs:
        if f.name in funcs:
            errs.append(f"{f.line}: 函数 {f.name} 重复定义")
        funcs[f.name] = f
    # ---- 外部函数声明 (docs/173)。三条与普通函数不同的口径:
    #   ① **彼此可以重名**: 两个模块各自 `extern fn malloc` 指的是**同一个外部符号**
    #      (等于 C 里重复包一个头文件), 不该报重名。所以用 setdefault, 不覆盖。
    #   ② 与**普通函数**（含依赖的 pub）撞名则报错: 那是两条不同的实现绑到同一个名字上。
    #   ③ 签名只收标量与 ptr —— 聚合/str/变参都会改变调用点形状, 出现就报 E021, 不静默错编。
    for m0 in [*deps, mod]:
        for x in m0.externs:
            if m0 is not mod and not x.pub:
                continue
            prev = funcs.get(x.name)
            if prev is not None and not prev.extern:
                errs.append(f"{x.line}: 外部函数 {x.name} 与既有函数重名 —— "
                            f"两个不同的实现不能绑到同一个名字上")
                continue
            funcs.setdefault(x.name, x)
    for x in mod.externs:
        if x.ret not in ("()",) and not _extern_ty_ok(x.ret):
            errs.append(f"{x.line}: 外部函数 {x.name} 的返回类型 {x.ret} 不支持 —— "
                        f"第 1 阶段只收标量与 ptr (docs/173 §3)")
        for p in x.params:
            if not _extern_ty_ok(p.type):
                errs.append(f"{x.line}: 外部函数 {x.name} 的参数 {p.name} 类型 {p.type} "
                            f"不支持 —— 第 1 阶段只收标量与 ptr (docs/173 §3)")
    seen_caps: set[str] = set()
    for c in mod.caps:
        if c.name in seen_caps:
            errs.append(f"{c.line}: 能力 {c.name} 重复声明")
        seen_caps.add(c.name)
        if c.lo > c.hi:
            errs.append(f"{c.line}: 能力 {c.name} 域下界 {c.lo} > 上界 {c.hi}")
        if c.lo < 0:
            errs.append(f"{c.line}: 能力 {c.name} 域下界为负")
        for ex in mod.excluded:  # M41: 出界声明的空间不得被能力使用
            space = ex.split(":")[0].strip()
            if space and space == c.space:
                errs.append(f"{c.line}: 能力 {c.name} 使用了 excluded 的空间 {space}")

    # **行为轴**（`docs/218` §7 第 2 件，2026-10-11）：上面那条 M41 比的是**名字**
    # （`excluded "usb"` 对 `capability … : usb[…]`）—— 它**从不看行为**，所以
    # "声明了不碰网络、代码里却在调 `socket`"是它看不见的。补上的就是这一条。
    #
    # **两轴必须分开**（`docs/218` §8 最后两行）：这条是**权威**读法（"这个门不开"），
    # 与 `docs/208` 那条**保密**读法（"我不会把数据送出去"，由 M41 那一族管）不是一件事 ——
    # 拿一条冒充两条，正是那次更正重演的路径。所以这里只判"**开了没有**"，
    # 不判"外发的字节里有没有本域的数据"。
    for site_line, num in _machine_call_sites(mod):
        if SURFACE_NUMBERS.get(num) != "net":
            continue
        for ex in mod.excluded:
            space = ex.split(":")[0].strip()
            if space in EXCLUDED_NET_SPACES:
                errs.append(f"{site_line}: 机调用号 {num} 使用了 excluded 的空间 {space}")
                break                      # 一个站点只报一次（两个写法都写了也还是一次）

    for f in mod.funcs:
        if not _type_ok(f.ret, known):
            errs.append(f"{f.line}: 未知类型 {f.ret}（函数 {f.name} 的返回类型）")
        scope: dict[str, str] = dict(const_scope)  # 模块级常量在函数体内可见
        # **形参之间**重名才算 E013（`#217`）：`scope` 一开始装着模块级常量，拿它判重
        # 会把"形参与常量同名"（合法，形参遮蔽常量）报成"参数重复" —— 两次误报一次也没挡住。
        _pnames: set[str] = set()
        for p in f.params:
            if p.name in _pnames:
                errs.append(f"{f.line}: 函数 {f.name} 参数 {p.name} 重复")
            _pnames.add(p.name)
            if not _type_ok(p.type, known):
                errs.append(f"{f.line}: 未知类型 {p.type}（参数 {p.name} 的类型）")
            scope[p.name] = p.type

        def walk(stmts: list, fscope: dict[str, str]) -> None:
            for s in stmts:
                if isinstance(s, Let):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    if not _type_ok(s.type, known):
                        errs.append(f"{s.line}: 未知类型 {s.type}（let {s.name} 的类型）")
                    if _is_array(s.type) and isinstance(s.expr, ArrayLit):
                        want = _array_len(s.type)
                        if want != len(s.expr.items):
                            errs.append(
                                f"{s.line}: 数组长度不符: 声明 {want}，字面量 {len(s.expr.items)}"
                            )
                    t = expr_type(s.expr, fscope, funcs, structs)
                    if t is not None and t != s.type:
                        errs.append(f"{s.line}: let {s.name}: {s.type} = 表达式类型 {t}")
                    fscope[s.name] = s.type
                elif isinstance(s, Assign):
                    _walk_expr(s.target, fscope, funcs, structs, errs, enums)
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    tt = expr_type(s.target, fscope, funcs, structs)
                    et = expr_type(s.expr, fscope, funcs, structs)
                    if isinstance(s.target, Index):
                        ot = expr_type(s.target.obj, fscope, funcs, structs)
                        if ot and _is_slice(ot) and not _is_mut_slice(ot):
                            errs.append(f"{s.line}: 只读切片不能写 (形参需声明 mut [T])")
                    if isinstance(s.target, Ident) and s.target.name not in fscope:
                        errs.append(f"{s.line}: 赋值未声明的变量 {s.target.name}")
                    elif not isinstance(s.target, (Ident, Index)):
                        errs.append(f"{s.line}: 赋值目标不是左值")
                    if tt is not None and et is not None and tt != et:
                        errs.append(f"{s.line}: 赋值 {tt} = 表达式类型 {et}")
                elif isinstance(s, If):
                    _walk_expr(s.cond, fscope, funcs, structs, errs, enums)
                    ct = expr_type(s.cond, fscope, funcs, structs)
                    if ct is not None and ct != "bool":
                        errs.append(f"{s.line}: if 条件类型 {ct}，应为 bool")
                    walk(s.then, fscope)
                    walk(s.otherwise, fscope)
                elif isinstance(s, While):
                    _walk_expr(s.cond, fscope, funcs, structs, errs, enums)
                    ct = expr_type(s.cond, fscope, funcs, structs)
                    if ct is not None and ct != "bool":
                        errs.append(f"{s.line}: while 条件类型 {ct}，应为 bool")
                    walk(s.body, fscope)
                elif isinstance(s, Guard):  # P4: 能力域检查
                    cap = next((c for c in mod.caps if c.name == s.cap), None)
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    if cap is None:
                        errs.append(f"{s.line}: guard 引用了未声明的能力 {s.cap}")
                    elif isinstance(s.expr, IntLit) and not (cap.lo <= s.expr.value <= cap.hi):
                        errs.append(
                            f"{s.line}: 能力 {s.cap} 域 [{cap.lo}..{cap.hi}]，索引 {s.expr.value} 越界"
                        )
                elif isinstance(s, For):
                    _walk_expr(s.lo, fscope, funcs, structs, errs, enums)
                    _walk_expr(s.hi, fscope, funcs, structs, errs, enums)
                    lt = expr_type(s.lo, fscope, funcs, structs)
                    ht = expr_type(s.hi, fscope, funcs, structs)
                    for bt, bw in ((lt, "下界"), (ht, "上界")):
                        if bt is not None and bt not in INT_TYPES:
                            errs.append(f"{s.line}: for {bw}类型 {bt}，应为整型")
                    body_scope = dict(fscope)
                    body_scope[s.var] = lt or ht or "u32"
                    walk(s.body, body_scope)
                elif isinstance(s, Match):
                    _walk_expr(s.subject, fscope, funcs, structs, errs, enums)
                    st = expr_type(s.subject, fscope, funcs, structs)
                    ed = enums.get(st) if st else None
                    if ed is None:
                        errs.append(f"{s.line}: match 主体类型 {st} 不是枚举")
                    seen_pat: set = set()
                    has_wild = False
                    for pat, body in s.arms:
                        arm_scope = dict(fscope)
                        if pat is None:
                            has_wild = True
                        else:
                            if ed is not None:
                                if pat.enum != ed.name:
                                    errs.append(
                                        f"{pat.line}: 模式 {pat.enum}::{pat.variant} 与主体枚举 {ed.name} 不符"
                                    )
                                elif pat.variant not in ed.variants:
                                    errs.append(f"{pat.line}: 枚举 {ed.name} 无变体 {pat.variant}")
                                elif pat.variant in ed.payloads and not pat.bind:
                                    errs.append(
                                        f"{pat.line}: 变体 {pat.enum}::{pat.variant} 有载荷，模式需绑定变量"
                                    )
                                elif pat.variant not in ed.payloads and pat.bind:
                                    errs.append(
                                        f"{pat.line}: 变体 {pat.enum}::{pat.variant} 无载荷，不能绑定"
                                    )
                                elif pat.bind:
                                    arm_scope[pat.bind] = ed.payloads[pat.variant]
                            key = (pat.enum, pat.variant)
                            if key in seen_pat:
                                errs.append(f"{pat.line}: 重复模式 {pat.enum}::{pat.variant}")
                            seen_pat.add(key)
                        walk(body, arm_scope)
                    if ed is not None and not has_wild:
                        missing = [v for v in ed.variants if (ed.name, v) not in seen_pat]
                        if missing:
                            errs.append(
                                f"{s.line}: match 不穷尽，缺 {', '.join(missing)}（或加 _ 通配）"
                            )
                elif isinstance(s, Return):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    t = expr_type(s.expr, fscope, funcs, structs)
                    if t is not None and t != f.ret:
                        errs.append(f"{s.line}: return 类型 {t}，函数 {f.name} 声明 {f.ret}")
                elif isinstance(s, ExprStmt):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)

        walk(f.body, scope)
        errs.extend(_move_check(f, funcs, structs, enums, lms))  # M13 移动检查 + E029
        # **非 `()` 的函数不许从末尾掉出去**（`#22`）。`check` 通过 = "这份程序不会 trap"
        # 是这条命令给人的唯一保证，而"掉出末尾"在发射之后是一条**非法指令**
        # （`SIGILL`）—— 只在**真走到那一支**时才发生，所以静态检查是唯一能在事前说这句
        # 话的地方。SKILL.md §6.14 原先把它写成一条**写作纪律**（"check 放行，跑到就是
        # Illegal instruction"）：一条静态检查的通过不意味着"不会 trap"，那正是这里的洞。
        if f.ret != "()" and _falls_through(f.body):
            errs.append(
                f"{f.line}: 函数 {f.name} 声明返回 {f.ret}，但**可能从末尾掉出去** —— "
                f"那条路上没有 `return`。走到那里是非法指令（SIGILL），不是返回一个默认值；"
                f"让每条路径都 `return`，或在最后补一个")

    # ---- 依赖的**正文**也要查 (`#149`, 2026-10-09 补)。
    #
    # 上面走完的是**入口单元**的正文; `deps` 的正文一个字都没验 —— 一个库可以带着
    # 正文类型错发布, 而每个使用它的程序 check 都是绿的 (`docs/198` §4)。
    #
    # 做法: 把每个依赖**当成入口**再查一遍。它的 `use` 图这次**不用重解** —— 把
    # 其余依赖整份当符号表递下去就够了(`resolve_deps` 给的就是整个闭包, 而被依赖者
    # 在前的顺序意味着一个依赖的符号在这次调用里**已经看见了**)。传整份闭包只会让
    # 可见面**更宽**, 于是漏报的可能有、误报没有 —— 宁可少报也不冤枉。
    #
    # `_dep_pass` 挡住递归: 依赖再审一遍它的依赖会转成环。
    #
    # 入口**已经有错**就不往下走了 —— 那些错多半就是下游症状的原因, 一起倒出来只会
    # 把根因埋掉 (与 `pytrans` 的 `pp_uns`、`check` 里那条 `dup_unit` 同一条纪律)。
    # 改完入口再跑一次, 依赖那层的错自然浮上来。
    if not _dep_pass and deps and not errs:
        _by_id = {_unit_id(x): x for x in deps}
        for d in deps:
            # **只给它自己的闭包**（#726）。从前这里传的是**整份** `deps` 减自己 ——
            # 于是兄弟单元被当成它的依赖：`f64bits` 没有一条 `use`，却因为闭包里
            # 还有 `mem`/`wide`/`f64`（都声明了 `choose runtime`）而被报"依赖要求
            # runtime，而根是 no_runtime"。被体检的单元**不是根**，它的"根"是它自己 ——
            # 而"依赖的声明是需求"这条只该对着**真正的依赖图**说。
            _reach: set = set()
            _todo = list(d.dep_ids)
            while _todo:
                _k = _todo.pop()
                if _k in _reach or _k == _unit_id(d):
                    continue
                _reach.add(_k)
                _u = _by_id.get(_k)
                if _u is not None:
                    _todo.extend(_u.dep_ids)
            others = [x for x in deps if _unit_id(x) in _reach]
            sub = check(d, ext_funcs=ext_funcs, deps=others, _dep_pass=True)
            if sub:
                # 消息前面点出**哪个单元**的错 —— 不然用户不知道去改哪个文件。
                where = _unit_where(d)
                errs.extend(f"{where}: {e}" for e in sub)
    # ---- LSPE（`docs/262`）：Q1 来源问与站点对账。
    # ⚠ **只在根单元、只在审计开**。默认档只把无来源站点记进台账的 `undecided`；
    # 只有 `lspe_mode == "strict"` 才把它们升级成 E034 —— 这是"温柔"那条的落点。
    if (not _dep_pass
            and mod.chooses.get("lspe", CORE_DEFAULTS["lspe"]) == "lspe_open_admin"):
        for s in getattr(mod, "lspe_sites", []):
            if s.get("kind") not in _LSPE_SITE_KINDS:
                errs.append(f"{s.get('line', 0)}: LSPE 台账里有未登记的站点种类"
                            f" {s.get('kind')!r}")
        if lspe_mode == "strict":
            for line in getattr(mod, "lspe_unsourced_lines", []):
                errs.append(f"{line}: LSPE 未能为这次放行给出来源（未标注的放行）")
    return errs


def _falls_through(body: list) -> bool:
    """这个体**可能**从末尾掉出去吗？（`#22`）

    只认三种**结构上**说得清的收尾 —— 末尾是 `return`；末尾是带 `else` 的 `if` 且两支
    都不掉；末尾是 `match` 且**每条臂**都不掉（穷尽性由既有的那条判据单独管，这里不重复
    判）。**别的形状一律算会掉** —— `while` / `for` 会正常结束，末尾一个表达式语句当然
    也掉。宁可让作者补一个 `return`，也不要让"check 过了却 trap"。

    跑遍本仓 184 份 `.lomt` 的语料，这条规则**一条都不误报**（0 个非 `()` 函数掉末尾）——
    所以它只会抓**新**写出来的漏。
    """
    if not body:
        return False
    s = body[-1]
    if isinstance(s, Return):
        return False
    if isinstance(s, If):
        if not s.otherwise:
            return True
        return _falls_through(s.then) or _falls_through(s.otherwise)
    if isinstance(s, Match):
        if not s.arms:
            return True
        return any(_falls_through(b) for _p, b in s.arms)
    return True


def _is_copy_type(t: str, structs: dict, enums: dict) -> bool:
    """M13: 标量/枚举/切片/str 为 Copy; struct 与数组为非 Copy (移动语义)。"""
    return not (t in structs or _is_array(t))


def _move_check(f: Func, funcs: dict, structs: dict, enums: dict,
                lms: bool = False) -> list[str]:
    errs: list[str] = []
    scope: dict[str, str] = {p.name: p.type for p in f.params}
    params = {p.name for p in f.params}
    moved: set[str] = set()
    #: **已经 `free` 过的块**（`docs/253` §5.1 的 E029）。**与 `moved` 分开两张表**：
    #: 移走是**所有权**的事（E006），释放是**合法性**的事（E029）—— 两回事。
    #: ⚠ 这一格今天**没有任何东西管**：`docs/210` §2.8 给 `free` 加的那五条检查管的是
    #: **`free` 自己那一端**（这块是不是真块、有没有重复释放），管不了"**之后再用**"——
    #: 那正是 use-after-free 的经典形状。
    freed: set[str] = set()

    def use(e) -> None:
        if isinstance(e, Ident):
            if e.name in moved:
                errs.append(f"{e.line}: 变量 {e.name} 已被移动, 不能再用")
            elif lms and e.name in freed:
                # 同上（`#486`）：码在头一行，正文不再重复。
                errs.append(f"{e.line}: 变量 {e.name} 指向的块已经被 `free`，不能再用")
        elif isinstance(e, Bin):
            use(e.left)
            use(e.right)
        elif isinstance(e, Un):
            if e.op not in ("&", "&mut"):
                use(e.expr)
        elif isinstance(e, Cast):
            use(e.expr)
        elif isinstance(e, MethodCall):
            use(e.obj)
            for a in e.args:
                use(a)
        elif isinstance(e, Call):
            gf = funcs.get(e.name)
            for i, a in enumerate(e.args):
                pt = gf.params[i].type if gf and i < len(gf.params) else None
                if pt and _is_slice(pt):
                    use(a)  # 切片参数是借用
                elif isinstance(a, Ident) and a.name in scope \
                        and not _is_copy_type(scope[a.name], structs, enums):
                    use(a)
                    moved.add(a.name)  # 非 Copy 传参 = 移动
                else:
                    use(a)
            # **E029**：`free(p)` 之后 `p` 指向的块没了 —— 记下来，之后再用到 `p` 就报。
            # ⚠ **登记放在循环之后**：`use(a)` 在上面已经把 `free` 自己那个实参走过一遍了，
            # 而**那一次是合法的**（清掉再报就成了"`free` 自己都不能调"）。
            if lms and e.name == "free" and e.args and isinstance(e.args[0], Ident):
                freed.add(e.args[0].name)
        elif isinstance(e, Index):
            use(e.obj)
            use(e.idx)
        elif isinstance(e, FieldAccess):
            use(e.obj)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                use(it)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                use(fe)
        elif isinstance(e, EnumCtor):
            use(e.arg)

    def walk(stmts, sc: dict) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.expr is not None:
                    use(s.expr)
                if isinstance(s.expr, Ident) and s.expr.name in sc \
                        and not _is_copy_type(sc[s.expr.name], structs, enums):
                    moved.add(s.expr.name)
                sc[s.name] = s.type
                freed.discard(s.name)      # 重新绑定 = 这个名字不再指那块死掉的块
            elif isinstance(s, Assign):
                use(s.expr)
                if isinstance(s.target, Ident):
                    if s.target.name in moved:
                        errs.append(f"{s.line}: 变量 {s.target.name} 已被移动, 不能赋值")
                    freed.discard(s.target.name)   # 重新赋值同理
                    if isinstance(s.expr, Ident) and s.expr.name in sc \
                            and not _is_copy_type(sc[s.expr.name], structs, enums):
                        moved.add(s.expr.name)
                else:
                    use(s.target)
            elif isinstance(s, If):
                use(s.cond)
                walk(s.then, dict(sc))
                walk(s.otherwise, dict(sc))
            elif isinstance(s, While):
                use(s.cond)
                walk(s.body, dict(sc))
            elif isinstance(s, For):
                use(s.lo)
                use(s.hi)
                bs = dict(sc)
                bs[s.var] = "u32"
                walk(s.body, bs)
            elif isinstance(s, Match):
                use(s.subject)
                for _, b in s.arms:
                    walk(b, dict(sc))
            elif isinstance(s, Return):
                use(s.expr)
                # M17: 不得返回局部变量的借用 (悬垂)
                if isinstance(s.expr, Un) and s.expr.op in ("&", "&mut") \
                        and isinstance(s.expr.expr, Ident) and s.expr.expr.name not in params:
                    errs.append(f"{s.line}: 返回局部变量 {s.expr.expr.name} 的借用（悬垂）")
            elif isinstance(s, ExprStmt):
                use(s.expr)
            elif isinstance(s, Guard):
                use(s.expr)

    walk(f.body, scope)
    return errs


# ---------------------------------------------------------------- 无分配审计 (M14)

ALLOC_BUILTINS: tuple = ()  # 语言当前无堆分配; 未来引入时在此登记


def alloc_audit(mod: Module, deps: list[Module] | None = None) -> list[str]:
    """M14: 列出分配点。当前语言按构造即 no-alloc, 恒为空。"""
    hits: list[str] = []
    for m in list(deps or []) + [mod]:
        for f in m.funcs:
            def scan(e) -> None:
                if isinstance(e, Call) and e.name in ALLOC_BUILTINS:
                    hits.append(f"{f.name}:{e.line} {e.name}")
                for child in (
                    getattr(e, "left", None), getattr(e, "right", None), getattr(e, "expr", None),
                    getattr(e, "obj", None), getattr(e, "idx", None), getattr(e, "arg", None),
                ):
                    if child is not None:
                        scan(child)
                for a in getattr(e, "args", []) or []:
                    scan(a)
                for it in getattr(e, "items", []) or []:
                    scan(it)
                for _, fe in getattr(e, "inits", []) or []:
                    scan(fe)

            def scan_stmts(stmts) -> None:
                for s in stmts:
                    for e in (getattr(s, "expr", None), getattr(s, "cond", None),
                              getattr(s, "subject", None), getattr(s, "lo", None),
                              getattr(s, "hi", None), getattr(s, "target", None)):
                        if e is not None:
                            scan(e)
                    for key in ("then", "otherwise", "body"):
                        sub = getattr(s, key, None)
                        if sub:
                            scan_stmts(sub)
                    for _, b in getattr(s, "arms", []) or []:
                        scan_stmts(b)

            scan_stmts(f.body)
    return hits


# ---------------------------------------------------------------- Rust 转译

BANNER = "// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。"


def _expr_rs(e) -> str:
    if isinstance(e, IntLit):
        return str(e.value)
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, Ident):
        return e.name
    if isinstance(e, StrLit):
        return json.dumps(e.value, ensure_ascii=False)  # Rust 字面量与 JSON 转义兼容
    if isinstance(e, Call) and (e.name in BUILTINS or e.name == "slice_len"):
        a = [_expr_rs(x) for x in e.args]
        if e.name == "slice_len":
            return f"({a[0]}.len() as u32)"
        if e.name == "panic":  # M19: no_std 友好 (宿主路径用 panic!)
            return f'panic!("loment panic {{}}", {a[0]})'
        if e.name == "alloc":  # M15
            return f"__loment_alloc({a[0]})"
        if e.name == "free":
            # Rust 后端**兑现不了** `free`：那边的运行期（`_RUST_RUNTIME`）只有一个 bump
            # 分配器，**没有空闲链表** —— 原生/IR 后端有 `@__loment_free`（bump + 地址序
            # 空闲链表 + 拆分 + 前沿回退），这里没有对应物。原先这句降级成
            # `{ let _ = p; 0u32 }`，于是**同一份源**在原生后端真回收、在 Rust 后端
            # **静默泄漏**（issue #165）。承诺落空比拒绝更坏，所以在这里点名拒。
            raise LomError(getattr(e, "line", 1), 1,
                           "rust 后端不支持 `free`：那边的运行期只有一个 bump 分配器，"
                           "`free` 在那里什么都不会释放。要真回收就用原生/IR 后端")
        if e.name == "load8":
            return f"(__loment_load8({a[0]}, {a[1]}) as u32)"
        if e.name == "store8":
            return "{ __loment_store8(" + ", ".join(a) + "); 0u32 }"
        if e.name == "atomic_add":  # M21
            return ("unsafe { (*((" + a[0] + ") as *const core::sync::atomic::AtomicU32))"
                    ".fetch_add(" + a[1] + ", core::sync::atomic::Ordering::SeqCst) }")
        if e.name == "get_bits":  # M22
            return f"((({a[0]}) >> ({a[1]})) & (((1u16 << ({a[2]})) - 1) as u8))"
        if e.name == "set_bits":
            return ("{ let __m: u8 = ((1u16 << (" + a[2] + ")) - 1) as u8; "
                    "(((" + a[0] + ") & !(__m << (" + a[1] + ")))"
                    " | ((((" + a[3] + ") & __m) << (" + a[1] + ")))) }")
        if e.name == "inb":  # M20: 端口 I/O (x86, 仅 Rust 路径)
            return ('{ let mut __v: u8 = 0; unsafe { core::arch::asm!("in al, dx", '
                    'in("dx") (' + a[0] + ') as u16, out("al") __v); } __v as u32 }')
        if e.name == "outb":
            return ('{ unsafe { core::arch::asm!("out dx, al", '
                    'in("dx") (' + a[0] + ') as u16, in("al") (' + a[1] + ') as u8); } 0u32 }')
        if e.name == "str_ptr":  # M67
            return f"(({a[0]}).as_ptr() as *mut u8)"
        if e.name == "ptr_add":  # M73
            return f"(({a[0]}) as *mut u8).add(({a[1]}) as usize)"
        if e.name == "ptr_sub":
            return f"(({a[0]}) as *mut u8).sub(({a[1]}) as usize)"
        if e.name == "syscall4":  # M67: Linux 系统调用 (rax/rdi/rsi/rdx)
            return ("{ let mut __r: i64; unsafe { core::arch::asm!(\"syscall\", "
                    "inlateout(\"rax\") (" + a[0] + ") as i64 => __r, "
                    "in(\"rdi\") (" + a[1] + ") as i64, "
                    "in(\"rsi\") (" + a[2] + ") as i64, "
                    "in(\"rdx\") (" + a[3] + ") as i64, "
                    "lateout(\"rcx\") _, lateout(\"r11\") _); } __r }")
        if e.name == "syscall6":  # M72: nr + a0..a4
            return ("{ let mut __r: i64; unsafe { core::arch::asm!(\"syscall\", "
                    "inlateout(\"rax\") (" + a[0] + ") as i64 => __r, "
                    "in(\"rdi\") (" + a[1] + ") as i64, "
                    "in(\"rsi\") (" + a[2] + ") as i64, "
                    "in(\"rdx\") (" + a[3] + ") as i64, "
                    "in(\"r10\") (" + a[4] + ") as i64, "
                    "in(\"r8\") (" + a[5] + ") as i64, "
                    "lateout(\"rcx\") _, lateout(\"r11\") _); } __r }")
        if e.name == "syscall7":  # M72+: 六个实参寄存器用满
            return ("{ let mut __r: i64; unsafe { core::arch::asm!(\"syscall\", "
                    "inlateout(\"rax\") (" + a[0] + ") as i64 => __r, "
                    "in(\"rdi\") (" + a[1] + ") as i64, "
                    "in(\"rsi\") (" + a[2] + ") as i64, "
                    "in(\"rdx\") (" + a[3] + ") as i64, "
                    "in(\"r10\") (" + a[4] + ") as i64, "
                    "in(\"r8\") (" + a[5] + ") as i64, "
                    "in(\"r9\") (" + a[6] + ") as i64, "
                    "lateout(\"rcx\") _, lateout(\"r11\") _); } __r }")
        if e.name == "str_len":
            return f"({a[0]}.len() as u32)"
        if e.name == "str_eq":
            return f"({a[0]} == {a[1]})"
        if e.name == "str_concat":  # M2: v0 的 str 是 &'static str -> 只能漏内存
            return (f"(Box::leak(format!(\"{{}}{{}}\", {a[0]}, {a[1]}).into_boxed_str()) "
                    f"as &'static str)")
        return f"({a[0]}.as_bytes()[({a[1]}) as usize] as u32)"
    if isinstance(e, Cast):
        return f"(({_expr_rs(e.expr)}) as {_rust_t(e.type)})"
    if isinstance(e, MethodCall):
        args = [_expr_rs(e.obj)] + [_expr_rs(a) for a in e.args]
        return f"{e.mangled}({', '.join(args)})"
    if isinstance(e, Call):
        return f"{e.name}({', '.join(_expr_rs(a) for a in e.args)})"
    if isinstance(e, Un):
        if e.op == "&mut":
            return f"(&mut {_expr_rs(e.expr)})"
        return f"({e.op}{_expr_rs(e.expr)})"
    if isinstance(e, Bin):
        op = e.op
        if op in ("&&", "||"):
            op = op[0] * 2  # && / ||
        return f"({_expr_rs(e.left)} {op} {_expr_rs(e.right)})"
    if isinstance(e, EnumPath):
        return f"{e.enum}::{e.variant}"
    if isinstance(e, EnumCtor):
        return f"{e.enum}::{e.variant}({_expr_rs(e.arg)})"
    if isinstance(e, StructLit):
        inner = ", ".join(f"{k}: {_expr_rs(v)}" for k, v in e.inits)
        return f"{e.name} {{ {inner} }}"
    if isinstance(e, FieldAccess):
        return f"{_expr_rs(e.obj)}.{e.name}"
    if isinstance(e, ArrayLit):
        return "[" + ", ".join(_expr_rs(x) for x in e.items) + "]"
    if isinstance(e, Index):
        # Rust 要求下标为 usize; Loment 允许任意整型, 转译时显式降级。
        return f"{_expr_rs(e.obj)}[({_expr_rs(e.idx)}) as usize]"
    raise AssertionError(f"未知表达式 {e!r}")


def _stmts_rs(stmts: list, indent: int) -> list[str]:
    pad = "    " * indent
    out: list[str] = []
    for s in stmts:
        if isinstance(s, Let):
            if s.expr is None:  # M9: 未初始化声明
                out.append(f"{pad}let mut {s.name}: {_rust_t(s.type)};")
            else:
                out.append(f"{pad}let mut {s.name}: {_rust_t(s.type)} = {_expr_rs(s.expr)};")
        elif isinstance(s, Assign):
            out.append(f"{pad}{_expr_rs(s.target)} = {_expr_rs(s.expr)};")
        elif isinstance(s, If):
            out.append(f"{pad}if {_expr_rs(s.cond)} {{")
            out += _stmts_rs(s.then, indent + 1)
            if s.otherwise:
                out.append(f"{pad}}} else {{")
                out += _stmts_rs(s.otherwise, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, While):
            out.append(f"{pad}while {_expr_rs(s.cond)} {{")
            out += _stmts_rs(s.body, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, For):
            out.append(f"{pad}for {s.var} in {_expr_rs(s.lo)}..{_expr_rs(s.hi)} {{")
            out += _stmts_rs(s.body, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, Match):
            out.append(f"{pad}match {_expr_rs(s.subject)} {{")
            for pat, body in s.arms:
                head = "_" if pat is None else (
                    f"{pat.enum}::{pat.variant}({pat.bind})" if pat.bind
                    else f"{pat.enum}::{pat.variant}"
                )
                out.append(f"{pad}    {head} => {{")
                out += _stmts_rs(body, indent + 2)
                out.append(f"{pad}    }}")
            out.append(f"{pad}}}")
        elif isinstance(s, Return):
            out.append(f"{pad}return {_expr_rs(s.expr)};")
        elif isinstance(s, ExprStmt):
            out.append(f"{pad}{_expr_rs(s.expr)};")
        elif isinstance(s, Guard):  # P4: 能力域守卫 (编译期已查字面量)
            # **审计只在放行那一支**（`#162`，与 IR 后端的 `gok` 块逐结构对应）:
            # 越界时 `__loment_trap` 只 panic，一个字节都不写 —— 属性 1 说的
            # "traps, with no other side effect" 才字面为真。
            out.append(
                f"{pad}{{ let __i: u64 = ({_expr_rs(s.expr)}) as u64; "
                f"if __i < {s.lo} || __i > {s.hi} {{ "
                f"__loment_trap({s.cap_id}, __i); }} else {{ "
                f"__loment_audit({s.cap_id}); }} }}"
            )
        else:
            raise AssertionError(f"未知语句 {s!r}")
    return out


def _rust_peq(ty: str) -> bool:
    """这个类型在**生成的 Rust 里**到底有没有 `PartialEq`。

    用在枚举的 derive 上: Rust 为枚举生成的 `impl PartialEq` 会要求载荷类型也可比较,
    而我们对 struct 只发 `Clone, Copy`(见下面 struct 那一段) —— 于是载荷是 struct 时,
    Rust 会把源码里根本没写过的 `Span == Span` 编出来, 报 E0369 (2026-09-15 用户实测)。

    **判据必须和"实际发了什么 derive"一致**, 不能是"理论上能不能派生" —— 我第一版就
    写成了后者(拿 struct 的字段递归算), 结果说"Span 可以", 于是一边发 PartialEq 一边
    不发, 照旧 E0369。这里只认我们真的会发 PartialEq 的那些类型。
    """
    if ty in ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "str"):
        return True           # str 在 Rust 侧是 &'static str, 有 PartialEq
    if _is_slice(ty):
        return _rust_peq(_slice_elem(ty))
    if ty.startswith("[") and ty.endswith("]"):
        return _rust_peq(ty[1:ty.rindex(";")].strip())
    return False              # ptr / struct / 未知 —— 我们都不发 PartialEq


def _rust_body(mod: Module, lom_root: Path) -> list[str]:
    out: list[str] = []
    for u in mod.uses:
        p = (lom_root / u) if not Path(u).is_absolute() else Path(u)
        if not p.exists():
            raise LomError(1, 1, f"use 的 .lom 不存在: {u}")
        sub = lomc.load(p)
        errs = lomc.check(sub)
        if errs:
            raise LomError(1, 1, f"use 的 {u} 有语义错误: {errs[0]}")
        out.append(f"// ---- 来自 {u} (L0 布局单源) ----")
        out.append(lomc.emit_rust(sub).rstrip())
        out.append("")
    for c in mod.caps:
        up = c.name.upper()
        out.append(f"// capability {c.name}: {c.space}[{c.lo}..{c.hi}]" + (" revocable" if c.revocable else ""))
        out.append(f'pub const CAP_{up}_SPACE: &str = "{c.space}";')
        out.append(f"pub const CAP_{up}_LO: u64 = {c.lo};")
        out.append(f"pub const CAP_{up}_HI: u64 = {c.hi};")
        out.append(f"pub const CAP_{up}_REVOCABLE: bool = {'true' if c.revocable else 'false'};")
        out.append("")
    if mod.caps:  # P4/M35: 域描述表 (内核可迭代)
        out.append("#[derive(Clone, Copy)]")
        out.append("pub struct CapDomain { pub space: &'static str, pub lo: u64, pub hi: u64, pub revocable: bool }")
        out.append("pub static CAP_DOMAINS: &[CapDomain] = &[")
        for c in mod.caps:
            rev = "true" if c.revocable else "false"
            out.append(f'    CapDomain {{ space: "{c.space}", lo: {c.lo}, hi: {c.hi}, revocable: {rev} }},')
        out.append("];")
        out.append("")
    for c in mod.consts:
        out.append(f"pub const {c.name}: {c.type} = {c.value};")
    if mod.consts:
        out.append("")
    for e in mod.enums:
        # `PartialEq` 不能无条件发: Rust 为枚举生成的 `impl PartialEq` 会**要求载荷类型也
        # PartialEq** —— 载荷是 struct 时, 而 struct 只有 `Clone, Copy`(见下), 于是报
        # E0369 `&Span` 不能比较, 可源码里根本没写过那次比较 (2026-09-15 用户实测)。
        # 所以先算"载荷全都能派生吗", 能才发 —— 对现有程序零变化。
        peq = all(_rust_peq(p) for p in e.payloads.values())
        out.append(f"#[derive(Clone, Copy{', PartialEq' if peq else ''})]")
        out.append(f"pub enum {e.name} {{")
        for v in e.variants:
            p = e.payloads.get(v)
            out.append(f"    {v}({p})," if p else f"    {v},")
        out.append("}")
        out.append("")
    drop_types = {im.type for im in mod.impls if im.trait == "Drop"}  # M16
    for s in mod.structs:
        if s.name not in drop_types:
            out.append("#[derive(Clone, Copy)]")
        out.append(f"pub struct {s.name} {{")
        for fn, ft in s.fields:
            out.append(f"    pub {fn}: {ft},")
        out.append("}")
        out.append("")
    # M16: Drop 走 Rust 原生 impl (不按普通函数发射)
    drop_names: set[str] = set()
    for im in mod.impls:
        if im.trait != "Drop":
            continue
        df = next((x for x in im.funcs if x.name.endswith("_drop")), None)
        if df is None:
            continue
        drop_names.add(df.name)
        out.append(f"impl Drop for {im.type} {{")
        out.append("    fn drop(&mut self) {")
        out += [ln.replace("__self", "self") for ln in _stmts_rs(df.body, 2)]
        out.append("    }")
        out.append("}")
        out.append("")
    for f in mod.funcs:
        if f.name in drop_names:
            continue
        if f.interrupt:  # M33: Rust 稳定版无 x86-interrupt, 仅注释标记
            out.append(f"// M33: {f.name} 是中断处理函数 (IR 路径用 x86_intrcc; Rust 路径需 nightly)")
        args = ", ".join(f"{p.name}: {_rust_t(p.type)}" for p in f.params)
        out.append(f"pub fn {f.name}({args}) -> {_rust_t(f.ret)} {{")
        out += _stmts_rs(f.body, 1)
        out.append("}")
        out.append("")
    return out


def emit_rust(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """依赖模块先出 (每个一次), 本模块在后 —— 单文件 Rust 产物。"""
    mod, deps = prepare(mod, deps)  # M6
    out = [BANNER, f"// module {mod.name} (loment v0 -> rust)", _RUST_RUNTIME]
    for d in deps or []:
        out.append("")
        out.append(f"// ==== 导入模块 {d.name} ====")
        out += _rust_body(d, lom_root)
    out.append("")
    out.append(f"// ==== 本模块 {mod.name} ====")
    out += _rust_body(mod, lom_root)
    return "\n".join(out).rstrip() + "\n"


def _count_guards(mod: Module) -> int:
    """M50: 审计站点数 —— 形式对象必须自描述 guard 数量 (不读源码即可断言 A2)。"""
    def walk(stmts: list) -> int:
        n = 0
        for s in stmts:
            if isinstance(s, Guard):
                n += 1
            elif isinstance(s, If):
                n += walk(s.then) + walk(s.otherwise)
            elif isinstance(s, (While, For)):
                n += walk(s.body)
            elif isinstance(s, Match):
                n += sum(walk(b) for _, b in s.arms)
        return n

    return sum(walk(f.body) for f in mod.funcs)


def _count_boundary(mod: Module) -> dict:
    """`docs/205` R5: 一份单元里的**边界操作**有几个 —— 形式对象自描述, 不读源码就能答。

    数的是"越过语言保证的每一步": 机调用 (`syscall4`/`syscall6`)、裸指针变换
    (`ptr_add`/`ptr_sub`/`str_ptr`)、以及调用本单元 `extern fn` 声明过的名字。

    **口径是词法的** —— `docs/204` R5 那一格要的正是"可 grep、可计数、可审计"。
    所以只看"这个名字被调用了没有", 不看类型, 也不判断它是不是真的危险
    （**边界可见 ≠ 边界正确**, 后半句是人的事）。两条细节:

    * `fn NAME(` 是**声明**不是调用 —— 谁定义了一个叫 `ptr_add` 的函数, 不该凭空
      多出一次"越界";
    * 方法调用 `x.NAME(...)` **算**（词法上它与 `NAME(...)` 同形）, 所以一并收。

    它和 `loment stat` 报的是**同一组数**（`loment_cli_test` 拿 `lomc.lex` 独立对过）,
    所以那份清单 `potato.BOUNDARY_BUILTINS` 是两边的**单一真源**。
    """
    import dataclasses as _dc

    import potato as _potato

    #: 走过的每一个"被调用的名字"。用**通用** dataclass 遍历, 而不是手写一张节点表 ——
    #: 手写的那种漏一个节点类型就少算几个, 而要等自举侧逐字节比对才看得出来。
    names: list[str] = []

    def walk(x) -> None:
        if isinstance(x, (Call, MethodCall)):
            names.append(x.name)
        if _dc.is_dataclass(x) and not isinstance(x, type):
            for fd in _dc.fields(x):
                if fd.name in ("line", "col", "off", "len"):
                    continue
                walk(getattr(x, fd.name))
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(mod)
    ext = [f.name for f in mod.externs]
    n_extc = sum(names.count(nm) for nm in ext)
    n_sys = sum(names.count(b) for b in _potato.BOUNDARY_BUILTINS if b.startswith("syscall"))
    n_ptr = sum(names.count(b) for b in _potato.BOUNDARY_BUILTINS if not b.startswith("syscall"))
    return {"extern_declared": len(ext), "extern_calls": n_extc, "syscalls": n_sys,
            "ptr_transforms": n_ptr, "total_sites": n_extc + n_sys + n_ptr}


#: **号 → 面**（`docs/222`）：只有这张表里点过名的号才有"面"，其余一律落在 `other`。
#:
#: **这份表照 `lomelf.PE_SYSCALLS` 抄**（那份是运行时的真话：这个仓自己实现了哪些号、
#: 各属哪一面），两边**逐条一致**由判据钉住（`loment_pe_test::test_surface_table_matches_the_runtime`）。
#: 抄第二份的理由与 `potato.BOUNDARY_BUILTINS` 那条子集判据同一个：两边分处两个模块，
#: 而判据能钉住它们不漂 —— 比"跨模块 import"更诚实（谁都不许偷偷多认一个号）。
SURFACE_NUMBERS = {
    60: "proc", 1: "file", 0: "file", 3: "file", 257: "file", 12: "mem",
    217: "file", 262: "file", 7: "net", 23: "net", 41: "net", 42: "net", 43: "net",
    46: "net", 47: "net", 53: "net", 232: "net", 233: "net", 291: "net",
    288: "net", 44: "net", 45: "net", 48: "net", 49: "net", 50: "net",
    51: "net", 52: "net", 54: "net", 55: "net",
}
#: 表里查不到的号落在这一面：**不猜**（`docs/188` 那条"不猜，报错"的同一条纪律 ——
#: 只是这里没有可报的错，因为"用了本仓运行时没实现的号"在语言里是合法的）。
SURFACE_OTHER = "other"

#: **联网站点 → 宽度档**（`docs/237`）：每处联网站点按"它要求多少宽度"归到一档。
#:
#: 这是**策略**，不是从运行时抄来的事实 —— 所以它**没有**"与运行时逐条判等"那条判据
#: （`SURFACE_NUMBERS` 有，因为它抄的是 `lomelf.PE_SYSCALLS`）。这里能判的是两条自洽：
#: ① **覆盖且不多余** —— 对 `SURFACE_NUMBERS` 的 net 集恰好一一对应（少一个号是多出来的
#: 一道"没归类的联网站点"，多一个号是给不是网络的号配了宽度）；
#: ② 各档之和 == `surfaces.net`（校验器判得了 —— 它读不到源码，但判得了两处自相矛盾）。
#:
#: 四档**从窄到宽**，对齐 `docs/218` §2 那三个刻度与 §11 的"宽度从 0 起"：
#:   `w0` 只够验证对端字节 —— 同机上的一对端点，**没有陌生对端可以进来**；
#:   `w1` 一条有界连接 —— 客户端与数据面：对端是它自己挑的**一个**；
#:   `w2` 多条并发 —— 多路复用本身不搬字节，要的宽度是"同时盯着很多条"；
#:   `w3` 接受任意对端 —— 对端**由别人挑**，宽度是"谁都能来"。
#:
#: **数的是站点，不是能力**：一个单元写了两处 `connect` 就是 `w1: 2` ——
#: 与 `gc_ladder` 数的是 `alloc` 站点同一条口径。宽度**本身**（同时能持几条、能传多少字节、
#: 能活多久）不在这里，它是这份组成 + 台账喂出来的**判定**（`docs/218` §2/§11）。
NET_WIDTH_RUNGS = {
    53: "w0",                                            # socketpair：同机
    41: "w1", 42: "w1", 44: "w1", 45: "w1", 46: "w1",    # socket/connect/收发
    47: "w1", 48: "w1", 51: "w1", 52: "w1", 54: "w1", 55: "w1",
    7: "w2", 23: "w2", 232: "w2", 233: "w2", 291: "w2",  # poll/select/epoll_*
    49: "w3", 50: "w3", 43: "w3", 288: "w3",             # bind/listen/accept*
}
#: `net_ladder` 的四个**分量**键 = 四档的名字，**从窄到宽**（`docs/237`）。
#: **自洽的那条**：`w0 + w1 + w2 + w3` 必须等于 `total_net`，且 `total_net` 必须等于
#: `surfaces.net` —— 两处数的是同一批站点，只是分的粗细不同。
NET_WIDTH_RUNG_KEYS = ("w0", "w1", "w2", "w3")
#: 机调用内建。与 `potato.BOUNDARY_BUILTINS` 里 `syscall` 开头那几个一致（那条子集判据钉着）。
SYSCALL_BUILTINS = ("syscall4", "syscall6", "syscall7")


def _machine_call_sites(mod: Module) -> list[tuple[int, int | None]]:
    """**每一个机调用站点** `(行号, 号或 None)` —— 号只在首参写成整数字面量时给得出。

    三处共用这一遍（`_count_surfaces` 与 `excluded` 的行为轴那一条）—— 走两遍就会漂：
    一处按 `IntLit` 认、另一处顺手也认了常量名，两份口径差一个号，判据各自绿着。
    """
    import dataclasses as _dc

    out: list[tuple[int, int | None]] = []

    def walk(x) -> None:
        if isinstance(x, (Call, MethodCall)) and getattr(x, "name", "") in SYSCALL_BUILTINS:
            args = getattr(x, "args", [])
            first = args[0] if args else None
            out.append((getattr(x, "line", 0),
                        first.value if isinstance(first, IntLit) else None))
        if _dc.is_dataclass(x) and not isinstance(x, type):
            for fd in _dc.fields(x):
                if fd.name in ("line", "col", "off", "len"):
                    continue
                walk(getattr(x, fd.name))
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(mod)
    return out


#: `excluded` 里**指网络空间**的那几个名字（`docs/218` §7 第 2 件）。
#: 面名本身由用的人定（`docs/224`），这里只认仓库自己的两个写法：
#: `net` 是**面**的叫法（`SURFACE_NUMBERS` 里那个），`network` 是**空间**的叫法
#: （`loment/examples/demo.lomt:18` 与 `docs/218` §1 用的就是它）。
EXCLUDED_NET_SPACES = ("net", "network")


def _count_surfaces(mod: Module) -> dict:
    """`docs/222`：把**机调用站点**按"面"分一分 —— 与 `boundary` / `gc_ladder` 同形的一份自描述。

    口径与 `_count_boundary` 一样是**词法的**：只看 `syscall4/6/7` 的**第一个实参**写没写成
    字面量 —— 是字面量就查 `SURFACE_NUMBERS` 得面名（查不到落 `other`），不是字面量也落
    `other`。于是"这个单元碰了哪个面、各几次"是**不读源码可判**的。

    **它多一条别的字段没有的交叉不变量**：`total_sites` 必然等于 `boundary.syscalls` ——
    每一个机调用站点都落在**恰好一个**面里，两处数的是同一批东西。那条由**校验器**独立判
    （它读不到源码，但判得了两处自相矛盾）。

    **为什么是"哪个面"而不是"哪个号"**：号是运行时的实现细节（`docs/217`），
    面才是读者要问的问题（"这份程序碰了网络没有"）。**号写成了变量就落 `other`** ——
    那是词法口径的诚实边界，不是漏数。
    """
    counts: dict = {}
    for _line, num in _machine_call_sites(mod):
        key = SURFACE_NUMBERS.get(num, SURFACE_OTHER)
        counts[key] = counts.get(key, 0) + 1
    out = {k: counts[k] for k in sorted(counts)}   # 键**按名字排序**：确定性是判据
    out["total_sites"] = sum(counts.values())
    return out


def _count_net_ladder(mod: Module) -> dict:
    """`docs/237`：把**联网站点的宽度组成**写进产物 —— 四档各领走几个站点。

    形状照 `gc_ladder`（四个非负整数 + `total_net`，各档之和必须等于总数），**多一条交叉**：
    `total_net` 必须等于 `surfaces.net` —— 两处数的是同一批站点，只是分的粗细不同。
    两条都由**校验器**独立判（它读不到源码，但判得了自相矛盾）。

    口径与 `_count_surfaces` **共用同一遍遍历**（`_machine_call_sites`）：非字面量的号
    两处都不认 —— 于是"梯子上的站点"与"面上的 net"**同集**，那条交叉不变量才成立。
    非 net 号不在这条梯上（`NET_WIDTH_RUNGS` 查不到就跳过），**不设 `other` 档**：
    `other` 是面那一层的兜底，梯子只回答"联网的部分按宽度怎么分"。

    **四档恒在**（哪怕为 0），与 `gc_ladder` 同 —— 键的集合固定，校验器与读者都不用猜。
    """
    counts: dict = {}
    for _line, num in _machine_call_sites(mod):
        rung = NET_WIDTH_RUNGS.get(num)
        if rung is None:
            continue
        counts[rung] = counts.get(rung, 0) + 1
    out = {k: counts.get(k, 0) for k in NET_WIDTH_RUNG_KEYS}
    out["total_net"] = sum(out[k] for k in NET_WIDTH_RUNG_KEYS)
    return out


def _count_gc_ladder(mod: Module) -> dict:
    """`docs/210` §5 第一条：把 **GC 的组成**写进产物 —— 四层梯各领走几个 `alloc` 站点。

    形状**照 `boundary`**（§5 R5 那条自洽规则的同形）：四个非负整数 + `total_sites`，
    而且 **`l0 + l1 + l2 + l3` 必须等于 `total_sites`**。这一条是**校验器独立判得了的**：
    它读不到源码，所以判不了"这几个数数得对不对"，但判得了"它们自相矛盾" —— 这正是
    `docs/210` §3 那句"GC 的组成是一个不读源码可判的数"要的那件事。

    口径：本单元**每一处 `alloc`** 从强到弱、先到先得，正好分到一层：

      * **L0** —— `let NAME: ptr = alloc(<字面量>)` 且 `NAME` 只被解引用透过（`Func.l0` 那张表）；
      * **L2** —— 落在某个**开着纪元**的循环体里（那个 `while`/`for` 的 `l2` 为真）；
      * **L1** —— 剩下的里、`NAME` 在 `Func.l1` 上的（定活，插 `free`）；
      * **L3** —— 其余（送给收集器）。

    **非 alpha 档**：`f.l0` / `f.l1` 恒空、`s.l2` 恒假 ⇒ L0=L1=L2=0、L3=total。这与
    "默认档一行都不动"（`docs/175` §3.4）是同一条事实，不是巧合。

    三个分层表都由**规则**在 token 流上填（`_l0_promotable` / `_l2_loop_epochs` /
    `_l1_place`），这里只是把它们的**决定**数出来 —— 于是自举侧
    （`loment/selfhost/potato.lomt`）数的是同一套谓词的同一个结果。

    在 **`raw_mod`（单态化之前）** 上数：自举侧看到的是**源 token**，也只有单态化前的
    那份视图与它对齐（单态化会造出实例的副本，那些副本两边都不数）。
    """
    import dataclasses as _dc

    #: 全部 `alloc` 调用点（**不看形状**）：凡是一个 `alloc` 就是一处站点，形状不合的
    #: （落不到 `let NAME: ptr = ...` 那种）自然落在 L3。用**通用** dataclass 遍历，
    #: 不手写节点表 —— 理由同 `_count_boundary`：漏一个节点类型就少算几个，要等自举侧
    #: 逐字节比对才看得出来。
    total = 0

    def reap(x) -> None:
        nonlocal total
        if isinstance(x, Call) and x.name == "alloc":
            total += 1
        if _dc.is_dataclass(x) and not isinstance(x, type):
            for fd in _dc.fields(x):
                if fd.name in ("line", "col", "off", "len"):
                    continue
                reap(getattr(x, fd.name))
        elif isinstance(x, (list, tuple)):
            for y in x:
                reap(y)

    reap(mod)

    l0 = l1 = l2 = 0

    def walk(ss: list, in_l2: bool, l0n: set, l1n: set) -> None:
        """可分层站点**只可能是语句级的** `let`（表达式里不嵌语句），所以这里只走语句
        列表：`if` 的两支、`while`/`for` 的体（带上 `l2` 这个"落在纪元里"的标记）、
        `match` 各臂。"""
        nonlocal l0, l1, l2
        for s in ss:
            if (isinstance(s, Let) and s.type == "ptr"
                    and isinstance(s.expr, Call) and s.expr.name == "alloc"):
                if s.name in l0n:
                    l0 += 1
                elif in_l2:
                    l2 += 1
                elif s.name in l1n:
                    l1 += 1
                # 其余落 L3（在 `l3` 那一格用 total 减出来）
            if isinstance(s, If):
                walk(s.then, in_l2 or s.then_l2, l0n, l1n)
                walk(s.otherwise, in_l2 or s.else_l2, l0n, l1n)
            elif isinstance(s, While):
                walk(s.body, in_l2 or s.l2, l0n, l1n)
            elif isinstance(s, For):
                walk(s.body, in_l2 or s.l2, l0n, l1n)
            elif isinstance(s, Match):
                for _pat, body in s.arms:
                    walk(body, in_l2, l0n, l1n)

    def ret_l1(ss: list, out: set) -> None:
        """收本函数体里被 L1 认领的名字 —— `_l1_place` 把 `free` 挂在**最早那条够得着的
        `return` 之前**（`Return.l1`），够不着就挂**函数末尾**（`Func.l1`）。两边合起来才是
        本函数的 L1 名单，只数 `f.l1` 会**漏掉最常见的那一格**（用处落在 `return` 之前）。"""
        for s in ss:
            if isinstance(s, Return):
                out.update(s.l1)
            elif isinstance(s, If):
                ret_l1(s.then, out)
                ret_l1(s.otherwise, out)
            elif isinstance(s, (While, For)):
                ret_l1(s.body, out)
            elif isinstance(s, Match):
                for _pat, body in s.arms:
                    ret_l1(body, out)

    for f in list(mod.funcs) + [g for im in mod.impls for g in im.funcs]:
        l1n = set(f.l1)
        ret_l1(f.body, l1n)
        walk(f.body, False, set(f.l0), l1n)
    return {"l0": l0, "l1": l1, "l2": l2, "l3": total - l0 - l1 - l2,
            "total_sites": total}


def emit_potato(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """M45: 形式对象 v1 —— 覆盖泛型/切片/字符串, 并由独立校验器自检 (M46)。"""
    import copy as _c

    _gc_alpha_pass(mod, deps)       # L0/L1/L2 —— **在 deepcopy 之前**（`_count_gc_ladder`
                                    # 读的就是 raw_mod 那几张表）
    raw_mod, raw_deps = _c.deepcopy(mod), _c.deepcopy(list(deps or []))
    mod, deps = prepare(mod, deps)  # M6
    layouts = []
    for u in mod.uses:
        p = (lom_root / u) if not Path(u).is_absolute() else Path(u)
        if not p.exists():
            continue
        sub = lomc.load(p)
        for r in sub.records:
            layouts.append(
                {
                    "name": r.name,
                    "size": r.size,
                    "endian": r.endian,
                    "packed": r.packed,
                    "fields": [
                        {"name": f.name, "type": f.type, "offset": f.offset}
                        for f in sorted(r.fields, key=lambda x: x.offset)
                    ],
                }
            )
    # M45: 泛型声明 (单态化前的本单元视图) + 单态化实例 (prepare 标记)
    generics = [{"kind": "fn", "name": f.name, "params": list(f.tparams)}
                for f in raw_mod.funcs if f.tparams]
    generics += [{"kind": "type", "name": s.name, "params": list(s.tparams)}
                 for s in raw_mod.structs if s.tparams]
    generics += [{"kind": "type", "name": e.name, "params": list(e.tparams)}
                 for e in raw_mod.enums if e.tparams]
    instances = [{"kind": "fn", "name": f.name, "of": f.from_generic,
                  "args": list(f.generic_args)} for f in mod.funcs if f.from_generic]
    instances += [{"kind": "type", "name": s.name, "of": s.from_generic,
                   "args": list(s.generic_args)} for s in mod.structs if s.from_generic]
    instances += [{"kind": "type", "name": e.name, "of": e.from_generic,
                   "args": list(e.generic_args)} for e in mod.enums if e.from_generic]
    # 实例名必须是**标识符**（`docs/147` §2「命名层」）。命名规则覆盖不到的形状 ——
    # 数组/切片/指针当泛型实参（`Box<[u32]>` 会拼成 `Box_[u32]`）—— 在这里**点名拒**。
    #
    # ⚠ **为什么不在 `prepare` 里拒**：`check()` 内部也调 `prepare()`（M6 单态化那一步），
    # 在那儿抛会把**检查器**也变成拒绝方 —— 那是**语言面**的改动（冻结面四条：改规范 +
    # 加一致性套件负例 + 两个实现同一次提交 + 过门禁），而这一格还没到那一步。
    # 放在**发射器**里，检查器的行为一个字节都不动（`Box<[u32]>` 仍旧 check 得过 ——
    # 它出界的是**形式对象**那一层）。
    for it in instances:
        if not it["name"].isidentifier():
            _disp = f"{it['of']}<{', '.join(it['args'])}>"
            raise LomError(1, 1,
                           f"泛型实参的形状这一格不收: `{_disp}` —— 收的是基类型名与嵌套泛型。"
                           "数组/切片/指针当泛型实参"
                           "（`Box<[u8; 4]>` / `Box<[u32]>` / `Box<*mut u8>`）**没有名字规则**，"
                           "而且这个后端先把它们挡住了（结构体的字段类型目前只支持标量）"
                           "—— 所以现在没有东西需要那个名字。见 `docs/147` §2「命名层」")
    # ---- LSPE 台账的计数（`docs/262` §8）。`lspe_close_admin` 下**关审计**：
    # 站点普查不算，台账只写状态与 `set_by`。这与"关的是警察、不是法律"一致。
    _lspe_state = mod.chooses.get("lspe", CORE_DEFAULTS["lspe"])
    _lspe_sites = ([] if _lspe_state == "lspe_close_admin"
                   else getattr(mod, "lspe_sites", []))
    if deps and _lspe_sites:
        # E4 的安全表要看**根 + 依赖**（自举侧 `compute_safe` 扫整个单元）；首参表仍只看根
        # —— 依赖里的函数不是这份产物要审计的站点。`load()` 手上没有 deps，所以这一步
        # 只能在发射这层补（与 `_gc_alpha_pass` 同一个理由）。
        _lspe_sites = [s for s in _lspe_sites if s["kind"] != "call"]
        _seen_e4: dict[str, int] = {}

        def _sid_e4(kind: str, key: str) -> str:
            occ = _seen_e4.get(key, 0)
            _seen_e4[key] = occ + 1
            return f"{mod.name}:{kind}:{_fnv1a32(key + '#' + str(occ)):08x}"

        _lspe_census_e4(
            getattr(mod, "toks", []), _sid_e4, _lspe_sites,
            [(getattr(d, "toks", []), _lspe_functions(getattr(d, "toks", [])))
             for d in deps])
    _lspe_level = int(getattr(mod, "lspe_level", 3))
    _lspe_solver = LspeSolver(_lspe_sites, _lspe_level).run()
    _lspe_by_kind: dict[str, list[int]] = {}
    for _s in _lspe_sites:
        # `per_kind` 按**义务种类**计，不按站点种类计（`docs/262` §5.11）。
        _pair = _lspe_by_kind.setdefault(_s["obligation"], [0, 0])
        _pair[0] += 1
        if _lspe_solver.proved(_s):
            _pair[1] += 1
    _lspe_n = len(_lspe_sites)
    _lspe_proved = sum(1 for _s in _lspe_sites if _lspe_solver.proved(_s))
    _lspe_undecided = _lspe_n - _lspe_proved
    _lspe_per_kind = {k: f"{v[1]}/{v[0]}" for k, v in sorted(_lspe_by_kind.items()) if v[0]}
    doc = {
        # v3 = v2 + **开关取值** (docs/182 §1)。**升版本而不是往 v2 加字段**, 与 v1->v2
        # 那条同一个理由: 新字段是必填的 (删掉它校验器必须红), 而往旧版加必填字段会让
        # 既有的对象全变非法 —— 旧版是承诺过能回放的 (docs/147 §5)。
        # v2 的新字段是 `mode`, **v3 的新字段是 `switches`**。
        # v4 = v3 + **方言**（`docs/184` §9 S4.3）。与 `mode`/`switches` 同一条纪律：
        # **必填、可为空数组** —— 不存在"缺这项"的形态。带上 `body` 是为了让产物
        # **自解释**：只记名字的话，读的人知道"用了方言 `def`"却不知道 `def` 是什么。
        # v5 = v4 + **外部代码块**（`docs/185` §7 ①）；
        # v6 = v5 + **表层语法声明** `grammar`（`docs/188` §2）；
        # v7 = v6 + **边界操作计数** `boundary`（`docs/205` R5）: 一份单元越过语言保证的
        # 那些调用点有几个。**与 `guards` 同级同形**（一个自描述的对象），理由也一样 ——
        # 审计要能**不读源码**就回答"这个单元的信任边界有多大"。
        #
        # ⚠ **v6 挂在 v7 之前从没被主编译器发过**：`grammar` 原先只有 `tools/potato_from.py`
        # 发（那一档的产出方是它）。加 `boundary` 时才发现 —— v7 让 v6 的 `grammar` 一起
        # 变成必填, 而这里没发它, 编译器的**自检当场就红**。所以这一版把 v6 也接了上来:
        # 前门早就知道答案（`FrontUnit.grammar`），只是没人把它带进来。
        # v8 = v7 + **回收档** `gc`（`docs/175` §3.4）：`gc_manual` / `gc_auto`。
        # v9 = v8 + **运行期** `runtime`（`docs/175` §3.6）：`runtime` / `no_runtime`。
        # 与 `mode` 同级同形 —— 一个字符串取值、**必填**、只有根单元能定，所以
        # "这个产物是在哪一档下编的"是**不读源码可判**的。
        # v10 = v9 + **GC 的组成** `gc_ladder`（`docs/210` §3 / §5）：四层梯各领走几个
        # `alloc` 站点。**与 `boundary` 同级同形**（一个自描述的对象 + 一条自洽的和）——
        # 于是"这份程序的 GC 由哪几层组成"是**不读源码可判**的（这正是 `docs/210` §3 那句
        # "GC 的组成是一个不读源码可判的数"）。
        # v11 = v10 + **对外端口** `port`（`docs/222` §4）：`sealed` / `hosted`。
        # v12 = v11 + **面的组成** `surfaces`（`docs/224`）：机调用站点按面分一分
        # （`net`/`file`/`mem`/`proc`/`other`）。与 `gc_ladder` 同形，但多一条**交叉**：
        # `total_sites` 必然等于 `boundary.syscalls`。
        # v13 = v12 + **执行模型** `vm`（`docs/231`）：`vm` / `no_vm`。与 `mode`/`gc`/
        # `runtime`/`port` 同级同形 —— 一个字符串取值、**必填**、只有根单元能定，所以
        # "这份产物是给 VM 的还是原生的"是**不读源码可判**的。
        # ⚠ 这一档今天**只在开发通道**编得出来（渠道闸，`docs/231` §2）：发行版里 `choose vm`
        # 是编译错误，所以发行版发的对象里 `vm` **恒为** `no_vm`。
        # v14 = v13 + **联网站点的宽度组成** `net_ladder`（`docs/237`）：四档各领走几个站点。
        # 与 `gc_ladder` 同形，多一条交叉：`total_net` 必须等于 `surfaces.net`。
        # v16 = v15 + **LSPE 台账** `lspe`（`docs/262`）：审计状态 + 站点/义务计数 +
        # 地板明细 + 不变量。它是**另一个字段**，按 `gc_ladder` 的先例单独升一版。
        "potato": "v16",
        "unit": mod.name,
        "language": "loment",
        # **表层语法**（`docs/188` §2）—— 与 `language` 分工不同, 别混:
        # `language` 说"这份东西**是**什么"（用别的写法写的, **它仍然是 `loment`**）,
        # `grammar` 说"用什么**写法**写的"。原生写法（含 `rust` 拼法、含没写声明）
        # 一律是 `loment`。
        "grammar": getattr(mod, "grammar", "loment"),
        # 整个程序的运行模式 (docs/143 §3.2)。**默认 std** —— 没写 `choose` 就是它,
        # 所以对象里永远是显式的两值之一, 不存在"缺这项"的形态。
        # 一个编译单元产出一个对象 (deps 走 `imports`), 所以这里没有"依赖的模式"
        # 那种歧义: 根单元自己声明的那一个就是它。
        "mode": mod.chooses.get("mode", CORE_DEFAULTS["mode"]),
        # **回收档**（`docs/175` §3.4）：`gc_manual` / `gc_auto`。与 `mode` 同一条纪律 ——
        # **必填**（删掉它校验器必须红），所以"这个产物放弃了确定性没有"是**可判**的。
        "gc": mod.chooses.get("gc", CORE_DEFAULTS["gc"]),
        # **运行期在不在**（`docs/175` §3.6）。取值只说"有没有"、不说"里面装了什么"——
        # 装的东西会随年份长（今天是收集器，明天可能是线程、宿主服务），钉进值名里
        # 就等于两年后加一项能力要回头改这一维的定义。
        "runtime": mod.chooses.get("runtime", CORE_DEFAULTS["runtime"]),
        # **对外端口在不在**（`docs/222` §4）：`sealed` / `hosted`。与上面三维同一条纪律 ——
        # **必填**，所以"这份产物通不通着世界"是**不读源码可判的**，而不是"看源码里有没有
        # `choose hosted`"。这正是 `docs/219` §7 那句"测量强于声明"在这一维的落法。
        "port": mod.chooses.get("port", CORE_DEFAULTS["port"]),
        # **执行模型**（`docs/231`）：`vm` / `no_vm`。与上面四维同一条纪律 —— **必填**，
        # 所以"这份产物是给 VM 的还是原生的"是**不读源码可判的**（`docs/229` §4.1 那处
        # 空白的第一格）。发行版里它恒为 `no_vm`（渠道闸）。**它今天不承诺性能**：
        # 没有字节码、没有解释器，`choose vm` 的产物与 `no_vm` **逐字节相同**。
        "vm": mod.chooses.get("vm", CORE_DEFAULTS["vm"]),
        # **内存安全**（`docs/253`，用户 2026-10-15 定第六维）：`close` / `open`。
        # 与 `gc` **正交** —— 同一张图上相反的两个方向（`docs/210` §6.2）。同一条纪律：
        # **必填**，于是"这份产物开没开内存安全"是**不读源码可判的**。
        "lms": mod.chooses.get("lms", CORE_DEFAULTS["lms"]),
        # **安全审计**（`docs/262`，用户 2026-10-18 定第七维）：台账是 LSPE 的第四件。
        # `state` 是开关值；`set_by` 说这一档是**谁定的**（今天只有 default / source）。
        # 计数在 0.1.5 先全为 0，0.1.5.1 起由 QIR/普查/来源问填真值 —— 但**字段必须现在就在**，
        # 否则"这份产物有没有被审计"仍然读不出来。
        "lspe": {
            "state": _lspe_state,
            "set_by": ("source" if "lspe" in mod.chooses else "default"),
            "mode": "full",
            "level": _lspe_level,
            "sites": _lspe_n,
            "obligations": _lspe_n,
            "proved": _lspe_proved,
            "undecided": _lspe_undecided,
            "deferred": 0,
            "refused": 0,
            "floors": 0,
            "floor_detail": {"assumed": 0, "stale_wf": 0, "budget": 0, "drop_honest": 0},
            "per_kind": _lspe_per_kind,
            # I1 = 站点对账（今天只对"种类都认识"这一半；跨组件那份在 #761）。
            "invariants": {
                "i1": ("ok" if all(s["kind"] in _LSPE_SITE_KINDS for s in _lspe_sites)
                       else "fail"),
                "i2": "not_run", "i3": "not_run", "i4": "not_run", "i5": "not_run",
            },
            "acked": 0,
        },
        # 开关取值 (用户 2026-09-17: **"开关的取值是要进 Potato 的"**, docs/182 §1)。
        # **永远是显式的数组**（可为空）—— 与 `mode` 同一条纪律: 不存在"缺这项"的形态,
        # 所以"这台机器上这个开关开没开"是**可回放**的, 不是"看当时的源码猜"。
        # **按名字排序**(见 `SwitchTable.dump`) —— 确定性是判据。
        "switches": (mod.switches.dump() if mod.switches is not None else []),
        "dialects": (getattr(mod, "dialects", None) or []),
        # 外部代码块（`docs/185` S1）：语言名 + 正文原文。**按源里的顺序**（序列，
        # 与 `dialects` 那个集合不同）。正文是 `{` 与 `}` 之间的原始字节。
        "bodies": [{"lang": b.lang, "body": b.body} for b in mod.ext_blocks],
        "imports": [d.name for d in (deps or [])],
        "capabilities": [
            {
                "name": c.name,
                "domain": {"space": c.space, "lo": c.lo, "hi": c.hi},
                "revocable": c.revocable,
            }
            for c in mod.caps
        ],
        "functions": [
            {
                "name": f.name,
                "params": [{"name": p.name, "type": p.type} for p in f.params],
                "ret": f.ret,
            }
            for f in mod.funcs
        ],
        "layouts": layouts,
        "consts": [{"name": c.name, "type": c.type, "value": c.value} for c in mod.consts],
        "enums": [
            {
                "name": e.name,
                "variants": list(e.variants),
                **({"payloads": dict(e.payloads)} if e.payloads else {}),
            }
            for e in mod.enums
        ],
        "types": [
            {"name": s.name, "fields": [{"name": fn, "type": ft} for fn, ft in s.fields]}
            for s in mod.structs
        ],
        "traits": [{"name": t.name, "methods": [m[0] for m in t.methods]}
                   for t in raw_mod.traits],
        "impls": [{"trait": i.trait, "for": i.type,
                   "methods": [f.name[len(i.type) + 1:]
                               if f.name.startswith(i.type + "_") else f.name
                               for f in i.funcs]}
                  for i in raw_mod.impls],
        "generics": generics,
        "instances": instances,
        "guards": _count_guards(mod),
        "boundary": _count_boundary(mod),
        # **GC 的组成**（`docs/210` §3/§5, v10）—— 在 `raw_mod`（单态化之前）上数，理由
        # 见 `_count_gc_ladder`：自举侧看到的是**源 token**，只有单态化前那份视图与它对齐。
        "gc_ladder": _count_gc_ladder(raw_mod),
        # **面的组成**（`docs/222`, v11）—— 与 `boundary` **同一个视图**（`mod`），
        # 因为那条交叉不变量（`total_sites == boundary.syscalls`）要求两处数的是同一批站点。
        "surfaces": _count_surfaces(mod),
        # **联网站点的宽度组成**（`docs/237`, v14）—— 与 `surfaces` 同一个视图、**同一遍
        # 遍历**：那条交叉不变量（`total_net == surfaces.net`）要求两处数的是同一批站点。
        "net_ladder": _count_net_ladder(mod),
        "excluded": list(mod.excluded),
    }
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    # M46: 编译器强制导出 —— 形式对象必须通过独立校验器, 否则编译失败。
    import potato as _potato
    errs = _potato.validate(doc)
    if errs:
        raise LomError(1, 1, "形式对象自检失败 (Potato v2): " + "; ".join(errs[:5]))
    return text


#: **自动回收**（`choose gc_auto`）那一段运行期 —— 与 `_IR_HEAP` **分开一段**，
#: 因为它只在 `gc_auto` 档追加，于是另外两档的产物**逐字节不变**（`docs/175` §3.4 之一）。
#:
#: 三条设计，每条对着市面自动 GC 的一个优点（"优点的集合"该抄哪几条、不抄哪几条，
#: 逐条写在 `docs/210` §2.4；**沉默不是理由**）：
#:
#: * **精确根**：根不是"把整个栈当字节扫"猜出来的，而是**编译器发的槽表** ——
#:   `@__loment_roots` 里存的是**槽的地址**，值在槽里。类型静态 ⇒ 编译器知道哪些
#:   局部/形参里有指针（`ptr`、`str`（`{ptr,i64}`，第一格是指针）、切片（同形）；
#:   struct 与枚举的字段在本后端**只装标量**，所以不必递归进聚合）。代价是每个
#:   "持有指针的函数"在入口 push、在每个 `return` 前 pop。
#: * **标记-清扫**（不搬移）：块无类型（`alloc(N)`），所以块内**保守**扫 ——
#:   每个 8 字节字当候选指针；**根是精确的**。
#: * **标记位另开一张表**（`@__loment_mark`，**位图**：每 8 字节一位 ⇒ **arena / 64 字节**）。
#:   索引仍是「块头偏移 `>> 3`」（那给出**位号**），字节下标再 `>> 3`、掩码取低三位 ——
#:   所以 **arena 的大小只体现在表的行数上**，口径不变（`docs/228` §1 第四行那个要害）。
#:   以前是"每 8 字节一个 `i32`"（arena / 2 字节），1 MiB 的 arena 要带 512 KiB 的 `.bss`
#:   —— `docs/231` 把它换成位图（1 MiB ⇒ 16 KiB）。
#:   曾经想借块头的 `next` 那一格（分配出去的块 `next` 恒为 0），**实测踩坑**：清标记那一趟
#:   会把空闲链表的 `next` 全抹掉 —— 链断了，`free` 过的块再也取不回来。
#: * **四趟遍历一律从偏移 8 起**：`@__loment_off` 的初值就是 8，偏移 0..7 不是块。
#:   从 0 起会读到 `size = 0`，而 `i += 0` 是个**死循环**（实测：那次的探针直接超时）。
#: * **清扫前先清空 `@__loment_freehead`**：不然"本来就在链上的块"会被再 `free` 一次
#:   （双重插入，链就毁了）。
#:
#: 触发：编译器在**每个 `alloc` 调用点**先喊一声 `@__loment_maybe_collect(请求字节数)`，
#: 它按**累计请求量**记 —— 过了 32 KiB 就收一次并清零。**不是按前沿**：前沿在有活块的
#: 时候根本不会回落（`free` 只在"释放的块顶到前沿"时退），按它判会让每次分配都收一次
#: （实测：40000 次循环跑成超时）。按分配量记是 O(1)、可预期，也是主流那几家的做法。
#: **根表那两个全局** —— 与收集器**分开一段**：`gc_any`（`gc_auto` / `gc_auto_alpha`）档
#: 只要函数里有指针槽就往根表写（`roots_push`），而**根表定义在收集器那一段里** ——
#: 一份"有根没堆"的单元（典型的：alpha 把 `alloc` 全 L0 提走、只剩下指针局部）会发出
#: **引用未定义全局**的产物（实测：`gc_auto` 只带一个 `fn f(p: ptr)` 也会）。所以根表
#: **跟着 `gc_any` 发**，收集器**跟着堆发** —— 两件事不再绑死。
# ---------------------------------------------------------------- LMS 第 3 级入口 A
#
# `docs/253` §5.3 的**入口 A**（内存用尽）：「`alloc` 真的分配不出时，**先试着拉回**，
# 拉不回来才死」。今天 `_IR_HEAP` 的 `boom` 是**直接 `abort`** —— 那是"运行期灾难"；
# 本文要的是"**先收一次再试**"。
#
# **为什么是变换、不是另抄一份**：那 200 行分配器在两处实现里必须同源。`hosted` 那一份
# 就是这么来的（`_arena_indirect`，见 `_IR_GC_HOSTED`）—— 手抄必漂。
# 三处**局部**改写，每一处都先 `assert` 命中：改不动就**当场炸**，不静默漂。
_LMS_RETRY_DECL = "@__loment_lms_retry = internal global i1 false\n"


def _heap_with_lms_rescue(text: str) -> str:
    lines = text.split("\n")
    i = next(k for k, l in enumerate(lines)
             if l.startswith("define internal ptr @__loment_alloc"))
    j = next(k for k in range(i + 1, len(lines)) if lines[k] == "}")
    body = lines[i:j + 1]
    # (1) `floop` 的两个 phi 各加一条入边（从 `rescue` 回来）。**只改这一个函数体**
    # —— 同一个文件里的 `__loment_free` 也有一个同名形状的 phi 循环，碰不得。
    k = body.index("floop:")
    assert body[k + 1] == "  %cur = phi i32 [ %head, %entry ], [ %nxt, %fnext ]"
    assert body[k + 2] == "  %prev = phi i32 [ 0, %entry ], [ %cur, %fnext ]"
    body[k + 1] = "  %cur = phi i32 [ %head, %entry ], [ %nxt, %fnext ], [ %head2, %rescue ]"
    body[k + 2] = "  %prev = phi i32 [ 0, %entry ], [ %cur, %fnext ], [ 0, %rescue ]"
    # (2) `boom` 不再直接 abort。**只许重来一次** —— 否则一份没有垃圾的程序会
    # 收了又收、永远出不去。
    k = body.index("boom:")
    assert body[k + 1] == "  call void @__loment_abort()"
    assert body[k + 2] == "  unreachable"
    body[k + 1:k + 3] = [
        "  ; ---- LMS 第 3 级入口 A（docs/253 §5.3）：先拉回，拉不回来才死 ----",
        "  %lmsdone = load i1, ptr @__loment_lms_retry",
        "  br i1 %lmsdone, label %dead, label %rescue",
        "rescue:",
        "  store i1 true, ptr @__loment_lms_retry",
        "  call void @__loment_collect()",
        "  %head2 = load i32, ptr @__loment_freehead",
        "  br label %floop",
        "dead:",
        "  call void @__loment_abort()",
        "  unreachable",
    ]
    # (3) **每一条成功返回**前清掉重试标记（`_IR_HEAP` 两条、`_IR_HEAP_HOSTED` 三条）。
    # 不清的话，"这一次"用掉的那一次重试会留给下一次分配 —— 那是**假的能力**。
    idx = [k for k, l in enumerate(body) if l.startswith("  ret ptr ")]
    assert len(idx) >= 2, idx
    for k in reversed(idx):
        body.insert(k, "  store i1 false, ptr @__loment_lms_retry")
    return _LMS_RETRY_DECL + "\n".join(lines[:i] + body + lines[j + 1:])


_IR_HEAP_LMS = _heap_with_lms_rescue(_IR_HEAP)
_IR_HEAP_LMS_HOSTED = _heap_with_lms_rescue(_IR_HEAP_HOSTED)
#: **`extern fn` 那一份也要救**（`docs/253` §5.3）。`_ffi_grown` 只是"**不是** alpha"，
#: 所以它可以与 `gc_auto` 同时成立 —— 那一档下产物里有收集器，第 3 级入口 A 就有东西
#: 可拉。漏掉这一份，`extern fn` + `gc_auto` + `lms_open` 会**静默没有**先拉回这一步。
_IR_HEAP_LMS_FFI = _heap_with_lms_rescue(_IR_HEAP_FFI)

_IR_ROOTS = '''; ---- 自动回收（`choose gc_auto`）---------------------------------------------------
@__loment_roots = internal global [1024 x i64] zeroinitializer
@__loment_rootn = internal global i32 0'''

_IR_GC_SHARED = '''@__loment_gcbytes = internal global i32 0
@__loment_mark = internal global [1024 x i8] zeroinitializer

; **四个遍历一律从偏移 8 起** —— `@__loment_off` 的初值就是 8，偏移 0..7 不是块
; （分配器那句注释：偏移 0 永远不是块）。从 0 起会读到 size=0，`i += 0` 就是死循环
; —— 实测：那次 40000 圈的探针直接跑到超时，成因就在这一行。
define internal i32 @__loment_markp(i32 %p) {
entry:
  %o0 = load i32, ptr @__loment_off
  %lo = icmp ult i32 %p, 8
  %hi = icmp uge i32 %p, %o0
  %bad = or i1 %lo, %hi
  br i1 %bad, label %no, label %scan
scan:
  %i = phi i32 [ 8, %entry ], [ %ex, %cont ]
  %o1 = load i32, ptr @__loment_off
  %done = icmp uge i32 %i, %o1
  br i1 %done, label %no, label %blk
blk:
  %szp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %i
  %sz = load i32, ptr %szp
  %ex = add i32 %i, %sz
  %ge = icmp uge i32 %p, %i
  %lt = icmp ult i32 %p, %ex
  %hit = and i1 %ge, %lt
  br i1 %hit, label %found, label %cont
cont:
  br label %scan
found:
  %midx = lshr i32 %i, 3
  %mby = lshr i32 %midx, 3
  %mp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %mby
  %mcur = load i8, ptr %mp
  %mbit = and i32 %midx, 7
  %msh = shl i32 1, %mbit
  %mmsk = trunc i32 %msh to i8
  %mold = and i8 %mcur, %mmsk
  %was = icmp ne i8 %mold, 0
  %mnew = or i8 %mcur, %mmsk
  store i8 %mnew, ptr %mp
  br i1 %was, label %no, label %yes
yes:
  ret i32 1
no:
  ret i32 0
}

; **根那一趟单独成函数** —— 分代那一档的「小回收」要**同一趟**（§2.7），抽出来比复制一份好。
; 代价是 `gc_auto` 的产物里这一段从内联变成一次调用；两个实现同时变，逐字节判据照旧守着。
define internal void @__loment_markroots() {
entry:
  br label %rl
rl:
  %rk = phi i32 [ 0, %entry ], [ %rk2, %rlc ]
  %rn = load i32, ptr @__loment_rootn
  %rd = icmp uge i32 %rk, %rn
  br i1 %rd, label %done, label %rb
rb:
  %rs = getelementptr [1024 x i64], ptr @__loment_roots, i32 0, i32 %rk
  %rsv = load i64, ptr %rs
  %rsp = inttoptr i64 %rsv to ptr
  %rval = load i64, ptr %rsp
  %rbase = ptrtoint ptr @__loment_heap to i64
  %rdo = sub i64 %rval, %rbase
  %rin = icmp ult i64 %rdo, 65536
  br i1 %rin, label %rmk, label %rlc
rmk:
  %rd32 = trunc i64 %rdo to i32
  %rr = call i32 @__loment_markp(i32 %rd32)
  br label %rlc
rlc:
  %rk2 = add i32 %rk, 1
  br label %rl
done:
  ret void
}

; **传播**：从 `%from` 起扫块，把块里**长得像堆内地址**的字递归标记上，直到不动点。
; 起点参数化是给分代那一档用的（小回收只扫年轻代）—— 全量那一趟传 8。
;
; **块内精确**（`docs/210` §2.9）：借出块头上那个字**就是掩码本身** —— 精确站点写
; `-(mask + 2)`，保守（以及空闲块）留在 `-1`。于是这里算出来：`1` ⇒ **整块扫**
; （也就是这一层加进来之前的行为），`≥ 2` ⇒ 掩码 = 那个数减 2，**只扫置了位的那些字**
; （位 0 = 载荷第一个 8 字节）。掩码由编译器在**分配站点**上推出来，规则与 L0/L1/L2 同一条
; 纪律（非逃逸 + 直接定偏移写，见 §2.9）。
; **没有槽表**：早先的稿子把掩码放在一张 per-program 的表里、头上只存下标 —— 那是多余的，
; 掩码本来就是**每个站点一个常数**，头上那个字有 32 位，装得下（`_SLOT_WORDS` 因此是 30）。
; 少一张表就少一处两套实现要同时改的东西。
; **空闲块一律保守**：`next ≥ 0` 说明它挂在空闲链表上（或 0 = 链尾）。那种块本来不该被扫，
; 但既然有指针指进来了就照老样子扫一遍 —— **宁可多留，不可漏**。
define internal void @__loment_propagate(i32 %from) {
entry:
  br label %pl
pl:
  %ch = phi i32 [ 1, %entry ], [ %acc, %pw ]
  %zero = icmp eq i32 %ch, 0
  br i1 %zero, label %done, label %pw
pw:
  %wi = phi i32 [ %from, %pl ], [ %we, %wlc ]
  %acc = phi i32 [ 0, %pl ], [ %acc2, %wlc ]
  %wo = load i32, ptr @__loment_off
  %wd = icmp uge i32 %wi, %wo
  br i1 %wd, label %pl, label %wb
wb:
  %wp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %wi
  %wsz = load i32, ptr %wp
  %widx = lshr i32 %wi, 3
  %wby = lshr i32 %widx, 3
  %wmp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %wby
  %wmk = load i8, ptr %wmp
  %wbit = and i32 %widx, 7
  %wsh = shl i32 1, %wbit
  %wmsk = trunc i32 %wsh to i8
  %wold = and i8 %wmk, %wmsk
  %wsm = icmp ne i8 %wold, 0
  br i1 %wsm, label %ws, label %wn2
ws:
  %wsstart = add i32 %wi, 8
  %wsend = add i32 %wi, %wsz
  %w4 = add i32 %wi, 4
  %w4p = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %w4
  %wnx = load i32, ptr %w4p
  %wlv = icmp slt i32 %wnx, 0
  br i1 %wlv, label %wk, label %wall
wk:
  %wneg = sub i32 0, %wnx
  %wz0 = icmp ule i32 %wneg, 1
  br i1 %wz0, label %wall, label %wm
wm:
  %wsub = sub i32 %wneg, 2
  %wm64 = zext i32 %wsub to i64
  br label %wl0
wall:
  br label %wl0
wl0:
  %wmask = phi i64 [ -1, %wall ], [ %wm64, %wm ]
  br label %wl
wl:
  %wj = phi i32 [ %wsstart, %wl0 ], [ %wj2, %wlc2 ]
  %wacc = phi i32 [ %acc, %wl0 ], [ %wacc2, %wlc2 ]
  %wjd = icmp uge i32 %wj, %wsend
  br i1 %wjd, label %wn, label %wx
wx:
  %wdif = sub i32 %wj, %wsstart
  %wshr = lshr i32 %wdif, 3
  %wsh64 = zext i32 %wshr to i64
  %wone = shl i64 1, %wsh64
  %wbs = and i64 %wmask, %wone
  %wnzs = icmp ne i64 %wbs, 0
  br i1 %wnzs, label %wscan, label %wlc2
wscan:
  %wjp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %wj
  %wv = load i64, ptr %wjp
  %wb2 = ptrtoint ptr @__loment_heap to i64
  %wdo = sub i64 %wv, %wb2
  %win = icmp ult i64 %wdo, 65536
  br i1 %win, label %wmk2, label %wlc2
wmk2:
  %wd32 = trunc i64 %wdo to i32
  %wr = call i32 @__loment_markp(i32 %wd32)
  br label %wlc2
wlc2:
  %wacc2 = phi i32 [ %wacc, %wx ], [ %wacc, %wscan ], [ %wr, %wmk2 ]
  %wj2 = add i32 %wj, 8
  br label %wl
wn:
  %wex = add i32 %wi, %wsz
  br label %wlc
wn2:
  %wey = add i32 %wi, %wsz
  br label %wlc
wlc:
  %we = phi i32 [ %wex, %wn ], [ %wey, %wn2 ]
  %acc2 = phi i32 [ %wacc, %wn ], [ %acc, %wn2 ]
  br label %pw
done:
  ret void
}

; **清扫**：从 `%from` 起把没标记的块**交回分配器**。**起点参数化**同 `propagate`
; （小回收只清扫年轻代）。
; **这里不重置空闲链表**（`@__loment_freehead`）—— 全量那一趟由**调用方**在进这里之前重置。
; 理由见 `__loment_collect` 里那段：重置是「防止同一块被挂两次」的手段，而**小回收不能重置**
; —— 老年代里那些**已经挂在链上的空闲块**不属于这次清扫的范围，重置一次就把它们从链上
; 丢了（不是不安全，是白漏内存）。
define internal void @__loment_sweep(i32 %from) {
entry:
  br label %sl
sl:
  %si = phi i32 [ %from, %entry ], [ %sex, %slc ]
  %so = load i32, ptr @__loment_off
  %sd = icmp uge i32 %si, %so
  br i1 %sd, label %done, label %sb
sb:
  %sszp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %si
  %ssz = load i32, ptr %sszp
  %sidx = lshr i32 %si, 3
  %sby = lshr i32 %sidx, 3
  %smp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %sby
  %smk = load i8, ptr %smp
  %sbit = and i32 %sidx, 7
  %ssh = shl i32 1, %sbit
  %smsk = trunc i32 %ssh to i8
  %sold = and i8 %smk, %smsk
  %smsm = icmp ne i8 %sold, 0
  br i1 %smsm, label %keep, label %kill
keep:
  br label %slc
kill:
  ; **这里的「清标记」什么都不做**（曾经写的是 `store i32 0, ptr %smp`）—— 那不是清一格，
  ; 是**越界清**：`%smp` 指向位图里的**一格字节**（`@__loment_mark` 每 8 字节堆一位），
  ; `store i32` 一次抹掉 **4 格 = 256 字节堆里的全部块**；就算写成 `store i8 0`，也照样
  ; 抹掉同一 64 字节里另外 7 个块的位。被顺带抹掉的块**可能是活的**，而**分代那一档的
  ; 旧块标记一旦被抹就再也标不回来**（`propagate` 只从 `oldtop` 起走，整个老年代不扫）
  ; —— 于是它们在同一趟清扫里被当垃圾交回分配器，程序随后读到的是复用后的字节。
  ; 而清零**本来就不需要**：走到这一格的**前提**就是「这一位是 0」（`%smsm` 为假）。
  ; 判据：`loment_elf_test::test_sweep_*` 两条同形语料（同源只差 `choose` 档位）。
  %k8 = add i32 %si, 8
  %kp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %k8
  call void @__loment_free(ptr %kp)
  br label %slc
slc:
  %sex = add i32 %si, %ssz
  br label %sl
done:
  ret void
}

; **全量回收的四趟**：清标记 → 扫根 → 传播到定点 → 清扫，一趟不少（`docs/210` §2.4）。
define internal void @__loment_collect() {
entry:
  br label %cl
cl:
  %ci = phi i32 [ 8, %entry ], [ %cex, %clc ]
  %co = load i32, ptr @__loment_off
  %cd = icmp uge i32 %ci, %co
  br i1 %cd, label %roots, label %clb
clb:
  %cidx = lshr i32 %ci, 3
  %cby = lshr i32 %cidx, 3
  %cmp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %cby
  store i8 0, ptr %cmp
  %csp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %ci
  %csz = load i32, ptr %csp
  %cex = add i32 %ci, %csz
  br label %clc
clc:
  br label %cl
roots:
  call void @__loment_markroots()
  call void @__loment_propagate(i32 8)
  ; **清扫前**不清空**空闲链表**（`#366`）—— 原先这里有一行 `store i32 0, ptr
  ; @__loment_freehead`，理由是「本来就在链上的块会被再 free 一次（双重插入，链就毁了）」。
  ; 那条理由站在 `#164` **之前**：当时 `free` 不验所有权，谁调都往里挂。
  ; `#164` 之后 `__loment_free` 只认**借出中**的块（`next == -1`），已在链上的块**惰性返回**
  ; —— 于是重置不再防住什么，却把**当前所有空闲块**从分配器手里**永久**丢掉：
  ; 清扫撞上它们时那道守卫拒绝重挂，而清扫**不会**把它们放回去。字节还在 arena 里、
  ; 还在前沿之下，`alloc` 再也摸不到。判据见 `loment_elf_test::test_a_collection_keeps_the_free_list`。
  call void @__loment_sweep(i32 8)
  ret void
}

'''
#: `gc_auto` 的触发：**按累计请求量**记 —— 过了 32 KiB 就收一次并清零。
_IR_GC_TRIGGER_FIXED = '''define internal void @__loment_maybe_collect(i32 %sz) {
entry:
  ; **时钟 = 分配器真正吃掉多少字节**（`__loment_alloc` 自己那两行 `(sz + 23) & -16`），
  ; 不是「载荷请求多少」—— 少了块头那 8 字节，小块程序的时钟就比消耗慢：8 字节载荷的
  ; 块消耗 32、时钟只走 8，慢一倍 ⇒ 计数越过阈值的那一刻，arena 已经装满了（实测：
  ; 纯拼接 3000 轮、计数 24000 < 32768，**一次都没收**，到 4096 轮 arena 满而 abort）。
  ; （16 而不是 8：载荷要 16 对齐，见 `#620` —— 这块时钟必须跟着分配器的步长走。）
  %r0 = add i32 %sz, 23
  %need = and i32 %r0, -16
  %b = load i32, ptr @__loment_gcbytes
  %b2 = add i32 %b, %need
  store i32 %b2, ptr @__loment_gcbytes
  %big = icmp ugt i32 %b2, 32768
  br i1 %big, label %go, label %out
go:
  store i32 0, ptr @__loment_gcbytes
  call void @__loment_collect()
  br label %out
out:
  ret void
}
'''

#: `gc_auto_alpha` 的**自适应**触发（`docs/210` §2.4 的"自适应策略池"那一半）—— 两处与
#: `gc_auto` **不同**，都是"让阈值跟着程序自己走"：
#:
#:   * **阈值跟着活集走**（`@__loment_thresh`）：每收一次，量出**活集字节数**（清扫后标记还在，
#:     扫一遍把标记块的 size 加总），把下次的触发阈值设成 `活集 × 2`（下限 32 KiB）。活集小
#:     ⇒ 收得勤而便宜；活集大 ⇒ 收得稀，让 arena 装得下。
#:   * **空间触发**（前沿 + 本次 + 头 > 65536 − 余量）：分配快把 arena 填满时**也收一次**。
#:     这一条正对着 `gc_auto` 那条明写的上限 —— 固定 32 KiB 触发时，"活集 + 至多 32 KiB 垃圾
#:     ≤ 64 KiB"，所以**活集 ≳ 32 KB 必耗尽**；空间触发把这一条抬到"活集接近整个 arena"。
#:
#: **不用 `select`**（下限那一格走分支）—— 自举镜像链接器 `lomelf` 的 `lower` 里没有 `select`。
#:
#: **再往上的一格：分代（`docs/210` §2.7）** —— 这一档的收集器**不止一种策略**：
#:
#:   * `__loment_major()` = 全量（`__loment_collect` + 把 `@__loment_oldtop` 推到前沿 + 清卡表）；
#:   * `__loment_minor()` = **只收年轻代**（地址 ≥ `@__loment_oldtop`）：清年轻代的标记、
#:     扫根、扫**脏卡**、**只传播年轻代**、**只清扫年轻代**。老年代一个字节都不扫。
#:
#: 年轻代 = **上次全量之后分配出来的那一截**。这一刀切在哪儿是**地址**，不是"对象的岁数"：
#: 分配器是 bump + 地址序空闲链表，于是"复用一块老空闲块"会落在老年代里（按地址算老）——
#: 那是**保守**的一侧（老年代不被小回收扫），不是不安全的一侧。
#:
#: **写屏障**（`__loment_wb`）是小回收能成立的前提：小回收不扫老年代，而**老块里可能存着
#: 指向年轻代的指针**（`p as u64` + `store64` 就写得出一个真指针，见 §4.2）—— 不记下来就会
#: 把活块当垃圾扫掉。所以**每一次写堆**（每个 `store8` 站点前、以及 `atomic_add` 前）落一条
#: 屏障调用：地址落在**老年代**就把它所在的 64 B **卡**置脏。小回收扫脏卡找年轻代指针，
#: 扫完清脏。卡表 1024 格 × 64 B = 整个 arena。
#:
#: **这是这一档的价钱，写明了**：屏障挂在**每一个 `store8` 站点**上（`gc_auto` 那一档**不带**，
#: 它老实做全量）。一次写一条间接调用，是这一档用"写多"换"收得少"的交换 —— 值不值要看
#: 写的次数与收的次数之比，这一版**没有量过**（§7 最后一条）。
_IR_GC_TRIGGER_ADAPT = '''@__loment_thresh = internal global i32 32768
@__loment_oldtop = internal global i32 8
@__loment_cards = internal global [1024 x i32] zeroinitializer

; **写屏障**：地址落在老年代 ⇒ 它所在的卡置脏。落在堆外（栈、只读段）或年轻代 ⇒ 什么都不做。
; 堆外那一条靠 `ptrtoint` 相减：栈地址与堆基址的差是一个**巨大的无符号数**，`ult 65536` 自然为假。
define internal void @__loment_wb(ptr %g) {
entry:
  %a64 = ptrtoint ptr %g to i64
  %b64 = ptrtoint ptr @__loment_heap to i64
  %d = sub i64 %a64, %b64
  %in = icmp ult i64 %d, 65536
  br i1 %in, label %heap, label %out
heap:
  %a = trunc i64 %d to i32
  %ge8 = icmp uge i32 %a, 8
  %ot = load i32, ptr @__loment_oldtop
  %lt = icmp ult i32 %a, %ot
  %yes = and i1 %ge8, %lt
  br i1 %yes, label %mark, label %out
mark:
  %ci = lshr i32 %a, 6
  %cp = getelementptr [1024 x i32], ptr @__loment_cards, i32 0, i32 %ci
  store i32 1, ptr %cp
  br label %out
out:
  ret void
}

; **小回收**：只动年轻代（地址 ≥ `@__loment_oldtop`）。五步 ——
;   ① 清**年轻代**的标记位（老年代的标记位不动：它们不参与这次清扫）；
;   ② 扫根（与全量同一趟）；
;   ③ 扫**脏卡**：老年代里被写过的那 64 B 块，逐个 8 字节窗口找落在堆里的值，标记其所在块；
;   ④ **只传播年轻代**（起点 `oldtop`）—— 年轻代内部互相指的那些；老→年轻由③兜住；
;   ⑤ **只清扫年轻代**（起点 `oldtop`），**不重置空闲链表**（理由见 `__loment_sweep`）。
define internal void @__loment_minor() {
entry:
  %ot = load i32, ptr @__loment_oldtop
  br label %cl
cl:
  %ci = phi i32 [ %ot, %entry ], [ %cex, %clc ]
  %co = load i32, ptr @__loment_off
  %cd = icmp uge i32 %ci, %co
  br i1 %cd, label %roots, label %clb
clb:
  ; **只清这一格自己的那一位** —— 这里曾经写 `store i8 0`（清**一整格字节**），而
  ; `@__loment_mark` 是**每 8 字节堆一位**的位图：一格字节覆盖**连续的 64 字节堆**，
  ; 里面很可能挤着 2~3 个块。本趟从 `%ot`（`oldtop`）起走，而 `oldtop` **不保证 64 字节
  ; 对齐** —— 于是第一趟就把 `oldtop` **下面**那几个**旧块**的位一起抹掉；而 `propagate`
  ; 只从 `oldtop` 起走、旧块整段不扫，抹掉就**再也标不回来** ⇒ 它们在同一趟清扫里被当
  ; 垃圾交回分配器，程序随后读到的是复用后的字节。
  ; **这一半没有最小复现**：它是读代码读出来的，改动方向是**单向**的（新写法清的是旧写法
  ; 所清位的一个子集），只会**少收**、不会多收。
  %cidx = lshr i32 %ci, 3
  %cby = lshr i32 %cidx, 3
  %cmp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %cby
  %cmk = load i8, ptr %cmp
  %cbit = and i32 %cidx, 7
  %csh = shl i32 1, %cbit
  %cmsk = trunc i32 %csh to i8
  %cclr = xor i8 %cmsk, -1
  %cnew = and i8 %cmk, %cclr
  store i8 %cnew, ptr %cmp
  %csp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %ci
  %csz = load i32, ptr %csp
  %cex = add i32 %ci, %csz
  br label %clc
clc:
  br label %cl
roots:
  call void @__loment_markroots()
  br label %cr
cr:
  %ri = phi i32 [ 0, %roots ], [ %ri2, %rc ]
  %rcd = icmp uge i32 %ri, 1024
  br i1 %rcd, label %prop, label %rcb
rcb:
  %rcp = getelementptr [1024 x i32], ptr @__loment_cards, i32 0, i32 %ri
  %rcv = load i32, ptr %rcp
  %rset = icmp eq i32 %rcv, 1
  br i1 %rset, label %cscan, label %rc
cscan:
  %rbase = ptrtoint ptr @__loment_heap to i64
  %cstart = shl i32 %ri, 6
  %cend = add i32 %cstart, 64
  br label %cs
cs:
  %sj = phi i32 [ %cstart, %cscan ], [ %sj2, %csc ]
  %sjd = icmp uge i32 %sj, %cend
  br i1 %sjd, label %cdone, label %csb
csb:
  %sp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %sj
  %sv = load i64, ptr %sp
  %sdo = sub i64 %sv, %rbase
  %sin = icmp ult i64 %sdo, 65536
  br i1 %sin, label %smk, label %csc
smk:
  %sd32 = trunc i64 %sdo to i32
  %sr = call i32 @__loment_markp(i32 %sd32)
  br label %csc
csc:
  %sj2 = add i32 %sj, 8
  br label %cs
cdone:
  store i32 0, ptr %rcp
  br label %rc
rc:
  %ri2 = add i32 %ri, 1
  br label %cr
prop:
  call void @__loment_propagate(i32 %ot)
  ; **清扫整堆，但不清空空闲链表**（`#366`，与 `__loment_collect` 同一条）。
  ; 原先这里也有那一行，理由两条（① 双重插入、② 既然重置就得能重复跑）；**两条都站在
  ; `#164` 之前**：今天 `__loment_free` 只认借出中的块，已在链上的一律**惰性返回**，
  ; 所以「同一块被挂两次」不会发生，而重置只会把那些**已经挂在链上**的空闲块
  ; （`docs/210` §2.7 自己点名的「不是不安全，是白漏内存」）永久丢掉。不做。
  call void @__loment_sweep(i32 8)
  ; **晋升**（不搬移的那一种）：这次小回收的**幸存者从此算老的** —— 把切成处推到前沿。
  ; 少了这一条，「年轻代」就永远是整个堆（`oldtop` 只有全量才动，而全量在小回收够用的
  ; 时候根本不会跑）—— 那样小回收 ≡ 全量、卡表永不生效。**实测**：少了这一条，
  ; `loment_elf_test` 那条分代判据在「拆掉卡扫描」之后照样通过（判据失去证伪力）。
  ; `off` 可能已经**退回**到 `oldtop` 之下（前沿回退），那就不动它（`max`，走分支不用 `select`）。
  %o2 = load i32, ptr @__loment_off
  %up = icmp ugt i32 %o2, %ot
  br i1 %up, label %prom, label %out
prom:
  store i32 %o2, ptr @__loment_oldtop
  br label %out
out:
  ret void
}

; **全量**：`__loment_collect` 那一套，外加「这一刀切在这儿」——把 `oldtop` 推到前沿、
; 把卡表清空（老年代整个换了一遍，旧卡不再代表任何东西）。
define internal void @__loment_major() {
entry:
  call void @__loment_collect()
  %o = load i32, ptr @__loment_off
  store i32 %o, ptr @__loment_oldtop
  br label %cc
cc:
  %i = phi i32 [ 0, %entry ], [ %i2, %ccc ]
  %cd = icmp uge i32 %i, 1024
  br i1 %cd, label %out, label %ccb
ccb:
  %cp = getelementptr [1024 x i32], ptr @__loment_cards, i32 0, i32 %i
  store i32 0, ptr %cp
  br label %ccc
ccc:
  %i2 = add i32 %i, 1
  br label %cc
out:
  ret void
}

; **策略选择**：该收的时候**先小回收**；小回收之后前沿**还是顶在装满那一档**就跟一次
; **全量**兜底 —— 于是「要么收够、要么确实装不下」，不留「收了但没用」那条路。
; **不做「年轻代太大就直接全量」那种优化**：前沿能不能退回去才是「这次小回收有没有用」的
; 判据（退回去了就说明死块都在顶端，正是小回收该干的事），而年轻代的**大小**不是。
define internal void @__loment_maybe_collect(i32 %sz) {
entry:
  ; **时钟 = 分配器真正吃掉多少字节**（`(sz + 23) & -16`），不是载荷请求 —— 与固定档同一条
  ; 理由（见 `_IR_GC_TRIGGER_FIXED`）。空间触发那一格原先写成 `off + sz + 8`：**多算了那
  ; 个 8**（块头早被 `& -16` 那条并进去了），于是它报的「下一块落在哪儿」比实际偏后。
  %r0 = add i32 %sz, 23
  %need = and i32 %r0, -16
  %b = load i32, ptr @__loment_gcbytes
  %b2 = add i32 %b, %need
  store i32 %b2, ptr @__loment_gcbytes
  %t = load i32, ptr @__loment_thresh
  %big = icmp ugt i32 %b2, %t
  %off = load i32, ptr @__loment_off
  %n1 = add i32 %off, %need
  %near = icmp ugt i32 %n1, 64512
  %go = or i1 %big, %near
  br i1 %go, label %do, label %out
do:
  store i32 0, ptr @__loment_gcbytes
  call void @__loment_minor()
  %of2 = load i32, ptr @__loment_off
  %m1 = add i32 %of2, %need
  %still = icmp ugt i32 %m1, 64512
  br i1 %still, label %maj, label %tune
maj:
  call void @__loment_major()
  br label %tune
tune:
  %live = call i32 @__loment_livebytes()
  %dbl = mul i32 %live, 2
  %lo = icmp ult i32 %dbl, 32768
  br i1 %lo, label %floor, label %set
floor:
  store i32 32768, ptr @__loment_thresh
  br label %out
set:
  store i32 %dbl, ptr @__loment_thresh
  br label %out
out:
  ret void
}

define internal i32 @__loment_livebytes() {
entry:
  br label %l
l:
  %li = phi i32 [ 8, %entry ], [ %lex, %lc ]
  %acc = phi i32 [ 0, %entry ], [ %acc2, %lc ]
  %lof = load i32, ptr @__loment_off
  %ld = icmp uge i32 %li, %lof
  br i1 %ld, label %done, label %b
b:
  %idx = lshr i32 %li, 3
  %bby = lshr i32 %idx, 3
  %mp = getelementptr [1024 x i8], ptr @__loment_mark, i32 0, i32 %bby
  %mk = load i8, ptr %mp
  %bbit = and i32 %idx, 7
  %bsh = shl i32 1, %bbit
  %bmsk = trunc i32 %bsh to i8
  %band = and i8 %mk, %bmsk
  %sm = icmp ne i8 %band, 0
  %sp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %li
  %sz = load i32, ptr %sp
  br i1 %sm, label %hit, label %miss
hit:
  %acc1 = add i32 %acc, %sz
  br label %lc
miss:
  br label %lc
lc:
  %acc2 = phi i32 [ %acc1, %hit ], [ %acc, %miss ]
  %lex = add i32 %li, %sz
  br label %l
done:
  ret i32 %acc
}
'''

_IR_GC = _IR_GC_SHARED + _IR_GC_TRIGGER_FIXED
_IR_GC_ALPHA = _IR_GC_SHARED + _IR_GC_TRIGGER_ADAPT


def _arena_indirect(ir: str, nbytes: int) -> str:
    """把一段**直接寻址 arena** 的 IR 换成**经基址指针**的间接寻址（`docs/230`）。

    `hosted` 的 arena 是宿主给的一块（`@__loment_heap` 是 `ptr`，不是 `[65536 x i8]`），
    所以收集器里**三件事**都得跟着换：

    * **基址** —— `getelementptr [65536 x i8], ptr @__loment_heap, …` → `getelementptr i8, ptr %hb, …`，
      以及 `ptrtoint ptr @__loment_heap` → `ptrtoint ptr %hb`；
    * **标记表的行数** —— 位图下它是 `arena / 64`（每 8 字节一位；`docs/228` §1 第四行那个
      要害），所以换 arena 就是换这个数，**口径一个字节没动**：位号仍是 `偏移 >> 3`，
      字节下标与掩码由它推出来；
    * **两处根扫的边界**（`icmp ult i64 %rdo / %wdo, 65536`）。

    `%hb` 插在**每个 `entry:` 之后**：这两个函数的入口块都叫 `entry`，而入口块**支配**
    其余每一块 —— 所以 `%hb` 在哪儿都可用。这是 LLVM 的支配关系保证的，不是"觉得够用"。

    **为什么是"换"而不是"再抄一份"**：`_IR_GC_SHARED` 是一条会继续长的文本，抄一份就是
    两份要同时改的东西（`docs/158` §5 那条孪生纪律）。判据反过来钉：两边的**非 arena 行
    必须逐字相同**（`lomentc_test` 里那条）。
    """
    out: list[str] = []
    for line in ir.split("\n"):
        if line == "entry:":
            out.append(line)
            out.append("  %hb = call ptr @__loment_arena()")
            continue
        line = line.replace("getelementptr [65536 x i8], ptr @__loment_heap, i32 0,",
                            "getelementptr i8, ptr %hb,")
        line = line.replace("ptrtoint ptr @__loment_heap to i64", "ptrtoint ptr %hb to i64")
        line = line.replace("icmp ult i64 %rdo, 65536", f"icmp ult i64 %rdo, {nbytes}")
        line = line.replace("icmp ult i64 %wdo, 65536", f"icmp ult i64 %wdo, {nbytes}")
        line = line.replace("[1024 x i8]", f"[{nbytes // 64} x i8]")
        out.append(line)
    return "\n".join(out)


#: **`hosted` 那一份收集器**（`docs/230`，`docs/228` §4 的 B1）—— 与 `_IR_GC_SHARED`
#: **同源**：机器从它换出来的，不手抄（换法与理由见 `_arena_indirect`）。
#:
#: 触发那一段**一个字节都不用换**：`_IR_GC_TRIGGER_FIXED` 按**字节数**触发（32768），
#: 不看 arena 大小 —— 所以两个端口共用它。`gc_auto_alpha` 不需要第二份 hosted：
#: `hosted` 与它本来就是**定义上冲突**的（`CORE_CONFLICTS`）。
_IR_GC_SHARED_HOSTED = _arena_indirect(_IR_GC_SHARED, HOSTED_ARENA_BYTES)
_IR_GC_HOSTED = _IR_GC_SHARED_HOSTED + _IR_GC_TRIGGER_FIXED


# ---------------------------------------------------------------- LLVM IR 后端 (M0: 标量子集, docs/144)

IR_TYPES = {
    "u8": "i8", "u16": "i16", "u32": "i32", "u64": "i64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64", "bool": "i1",
}


def _wrap_int(v: int, ty: str) -> int:
    """把整型字面量按 `ty` 的位宽环绕, 负值取补码。

    发射器必须印**装得下这个类型**的值: `store i8 300` 不是合法 LLVM (i8 常量装不下
    300), 包内链接器自己截断才让它"碰巧"跑对, clang 会拒。所以 `let v: u8 = 300;`
    要印成 44 —— 也就是运行时真正取到的那个值, 不做 Rust 那种编译期拒收 (那不符合
    本语言的语义; `docs/144` §"除零/溢出语义" 记着这层规则由 M2 定)。
    """
    n = 1 if ty == "bool" else int(ty[1:])
    v &= (1 << n) - 1
    if ty[:1] == "i" and v >> (n - 1):
        v -= 1 << n
    return v


_SIGNED = ("i8", "i16", "i32", "i64")

#: 整型的**值域**：位宽 + 有没有符号。下面那条规则只用这两样。
_WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64,
           "i8": 8, "i16": 16, "i32": 32, "i64": 64}


def _common_type(a: str, b: str) -> str | None:
    """两个整型的**公共类型**：一个两个值都装得下、且**不改变任何一个值**的类型。

    ## 这条规则（`docs/233`）

    **不会改变值的转换自动发生；会改变值的转换必须写 `as`。** 就这一句。

    * 同符号：取宽的那个（`u32` 与 `u64` ⇒ `u64`；`i32` 与 `i64` ⇒ `i64`）。
    * 一宽一窄、一有符号一无符号：**只有窄的那个是无符号**才有解
      （`u32` 与 `i64` ⇒ `i64`，因为 `i64` 装得下 0..2³²-1）；
      反过来（`u64` 与 `i32`、`u32` 与 `i32`）**没有公共类型** ——
      `u64` 装不下 -1，`i32` 装不下 2³²-1，两边谁都不肯，于是要写 `as`。

    取这个形状的理由（用户 2026-10-16 的裁定："简单，安全，方便，快捷，吸取
    Rust/C 和 py 的优势"）：

    * **安全**（Rust 那一半）：隐式转换**永远不会改变一个值**，所以不存在"编得过、
      跑得出、数不对"。C 在这里恰恰相反 —— 它的"无符号赢"会把 `-1` 变成一个
      极大的正数，那条坑这条规则进不来。
    * **方便**（C 那一半）：常见的混宽写法（`v >> (8 * i)`、`len + 1`）不用加 `as`。
    * **简单**（Python 那一半）：一句话，一张两行的表；不是 C 那张十几行的
      "寻常算术转换"，也没有"先提升到 int"那一步。
    * **快捷**：纯粹是编译期的定型；运行时那一步是 `zext`/`sext`，没有成本。

    没有公共类型就是**编译期错误**（E001），不是"挑一个"—— 挑不出来的时候，
    任何选择都是在替用户改数（这正是 `#133`：原先取**左操作数**的类型，
    `x:i32 + y:i64` 于是在 32 位里算，同一个子表达式一处等于 2³¹、一处为负）。
    """
    if a not in _WIDTHS or b not in _WIDTHS:
        return None
    sa, sb = a in _SIGNED, b in _SIGNED
    if sa == sb:
        return a if _WIDTHS[a] >= _WIDTHS[b] else b
    u, s = (a, b) if not sa else (b, a)
    return s if _WIDTHS[u] < _WIDTHS[s] else None


def _ir_t(t: str) -> str:
    if t not in IR_TYPES:
        raise LomError(1, 1, f"这个后端只支持标量类型，遇到 {_display_ty(t)}")
    return IR_TYPES[t]


def _ll_type(t: str, structs: dict, enums: dict) -> str:
    """Loment 类型 -> LLVM 类型 (M23–M25: 标量 / struct / 定长数组 / 枚举; M1: str)。"""
    if t in IR_TYPES:
        return IR_TYPES[t]
    if t == "str":
        return "{ ptr, i64 }"  # 字节指针 + 长度 (UTF-8 视图)
    if t == "ptr":  # M15: 不透明指针
        return "ptr"
    if t == "()":  # M16: unit
        return "void"
    if _is_slice(t):
        et = _slice_elem(t)
        if et not in IR_TYPES:
            raise LomError(1, 1, f"切片的元素类型 {_display_ty(et)} 目前只支持标量")
        return "{ ptr, i64 }"  # 元素指针 + 元素个数
    if t in structs:
        s = structs[t]
        for _, ft in s.fields:
            if ft not in IR_TYPES:
                raise LomError(s.line, 1, f"结构体 {_display_ty(t)} 的字段类型 {_display_ty(ft)} 目前只支持标量")
        return "{ " + ", ".join(IR_TYPES[ft] for _, ft in s.fields) + " }"
    if t in enums:
        e = enums[t]
        if not e.payloads:
            return "i32"
        for pt in e.payloads.values():
            if pt not in IR_TYPES:
                raise LomError(e.line, 1, f"枚举 {_display_ty(t)} 的载荷类型 {_display_ty(pt)} 目前只支持标量")
        return "{ i32, i64 }"
    if _is_array(t):
        et = _array_elem(t)
        if et not in IR_TYPES:
            raise LomError(1, 1, f"数组的元素类型 {_display_ty(et)} 目前只支持标量")
        return f"[{_array_len(t)} x {IR_TYPES[et]}]"
    raise LomError(1, 1, f"native 后端不支持类型 {t!r}")


#: **L0**（`docs/210` §2）里唯一允许"碰到指针"的方式：把指针作为**整个第一个实参**
#: 交给一个**只解引用它**的内建。
#:
#: **这份名单只有一个来源：`BUILTINS` 里 `ptr` 出现在首参的那几个。** 逐个说清为什么：
#:
#: | 首参是 `ptr` 的内建 | 算不算 | 为什么 |
#: |---|---|---|
#: | `load8` / `store8` / `atomic_add` | **算** | 内联降级，**只解引用** —— 不把指针存到别处、不产生派生的指针值 |
#: | `free` | 不算 | 它把块**还回去**；栈上的缓冲还给分配器是灾难 |
#: | `ptr_add` / `ptr_sub` | 不算 | 它们**产生一个派生的指针值**，而那个值会流到哪儿这里看不见 |
#:
#: **`load16/32/64`、`store16/32/64` 不在名单里 —— 它们根本不是内建**（在
#: `loment/examples/bytes.lomt` 里，用 `load8`/`store8` 拼出来的 pub 函数）。把它们当"安全的"
#: 是**不成立的**：那是一次**跨函数调用**，`p` 进了别人的栈帧，而"那个函数不会把 p 存起来"
#: 编译器并不知道（用户自己写一个同名函数就能推翻它）。**这是 L0 今天最窄的一处**，
#: 见 `docs/210` §7：要放宽就得做**跨函数的 deref-only 参数分析**。
#:
#: `loment_genesis_test` 有一条判据钉住"这张表 == 上表"：内建表里**新加一个首参是 ptr 的
#: 内建**时，那条判据会红，逼你在这里表态（而不是让它悄悄漏掉一个新内建）。
_L0_SAFE_BUILTINS = frozenset(("load8", "store8", "atomic_add"))

#: 上表**故意排除**的首参为 `ptr` 的内建（每条都要有理由，见上）。判据拿它做完整性检查。
_L0_PTR_ARG0_UNSAFE = frozenset(("free", "ptr_add", "ptr_sub"))


def _reset_l1(stmts: list) -> None:
    """清空一棵语句树的 `Return.l1` —— `_gc_alpha_pass` 要能**幂等**重跑（同一份 mod 被
    `emit_*` 两次不会把 `free` 叠两遍）。`f.l1` / `f.l0` 是赋值，`s.l2` 是赋值，只有
    `Return.l1` 是 `append`，所以要单独清。"""
    for s in stmts:
        if isinstance(s, Return):
            s.l1 = []
        elif isinstance(s, If):
            _reset_l1(s.then)
            _reset_l1(s.otherwise)
        elif isinstance(s, (While, For)):
            _reset_l1(s.body)
        elif isinstance(s, Match):
            for _pat, b in s.arms:
                _reset_l1(b)


def _deref_summary(f: Func, toks: list):
    """`f` 的**首参**流进哪些被调 —— `docs/210` §7 那次放宽的原料。回 `None` 或
    `(escapes, callees)`：首参不是 `ptr` 回 `None`；`escapes` = 首参**有没有**逃逸的用法；
    `callees` = 首参**只**当"整个第一个实参"流进去的那些被调名字。

    **规则与 L0/L1/L2 同一条，定义在 token 流上**（理由见 `_l0_promotable`）：首参名 `P`
    在函数体内每一处出现都必须紧跟 `(`、那个 `(` 紧跟一个**标识符**被调名（不是限定名）、
    且 `P` 后面紧跟 `,` 或 `)`。任何别的用法 —— 重新赋值、遮蔽（`let P`）、传第二参、
    进算术、当返回值 —— 即 **escape**。

    **"安全"不在这一步判**：这里只记"流进了哪些名字"。`callees` 是否全都安全，由
    `_gc_alpha_pass` 的**全局不动点**定 —— 于是 `load32 -> load16 -> load8` 一层层解开。
    """
    if not f.params or f.params[0].type != "ptr":
        return None
    p = f.params[0].name
    at, end = f.tok_at, f.tok_end
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    callees: set[str] = set()
    k = at
    while k < end:
        if ident(k) and toks[k].val == p:
            if word(k - 1, "let") or word(k + 1, "="):
                return (True, frozenset())              # 遮蔽 / 重新赋值
            if (word(k - 1, "(") and ident(k - 2) and not word(k - 3, ".")
                    and not word(k - 3, ":")
                    and (word(k + 1, ",") or word(k + 1, ")"))):
                callees.add(toks[k - 2].val)
            else:
                return (True, frozenset())              # 其余任何用法：逃逸
        k += 1
    return (False, frozenset(callees))


def _gc_alpha_pass(mod: Module, deps: list[Module] | None = None) -> None:
    """`docs/210` 的 L0/L1/L2 分析 —— **发射前跑**，因为它要一张**跨模块**的安全表。

    **为什么从 `parse()` 搬出来**：那次"最该先做的放宽"（`docs/210` §7）把 L0/L1/L2 的
    安全名单从三个内建放宽到"**首参不外逃的用户函数**"。而旗舰证据 `load32(p, off)`
    的那个 `load32` **常常在依赖里**（`bytes.lomt`），解析器手上只有本模块的 token ——
    安全表要 root + deps 一起才算得出，那只有 `emit_*` 这一层够得着。

    两半：

    1. **全局不动点**：`safe = 三个内建`；反复把"首参是 `ptr`、不外逃、且 `callees` 全落在
       `safe` 里"的用户函数加进去，直到不动。于是 `load8`（内建）⇒ `load16`（只进 `load8`）
       ⇒ `load32`（只进 `load16`）一层层解开。
    2. **跑分析**：对每个**自己选了 `gc_auto_alpha`** 的模块，用这张 `safe` 重算 L0/L1/L2。
       **幂等**（先清空三张表再算），所以同一份 mod 被 emit 两次也不会叠。

    只有**自选 alpha 的模块**才重算 —— 依赖没写 `choose`，照旧不分析（与"只有根单元能定
    `choose`"一致：默认档一行都不动）。
    """
    mods = list(deps or []) + [mod]
    safe: set[str] = set(_L0_SAFE_BUILTINS)
    changed = True
    while changed:
        changed = False
        for m in mods:
            for f in list(m.funcs) + [g for im in m.impls for g in im.funcs]:
                if f.name in safe or f.deref is None:
                    continue
                escapes, callees = f.deref
                if not escapes and callees <= safe:
                    safe.add(f.name)
                    changed = True
    frozen = frozenset(safe)
    for m in mods:
        if m.chooses.get("gc") != "gc_auto_alpha":
            continue
        toks = m.toks
        #: **按 token 位置排序** —— 槽表的次序必须与自举侧那趟**线性 token 走法**一致，
        #: 而 `list(m.funcs) + impls` 会把 impl 里的函数排到所有顶层函数**之后**（源码里
        #: 它们可能夹在中间）。排序之后两边数的是同一个次序。
        funcs = sorted(list(m.funcs) + [g for im in m.impls for g in im.funcs],
                       key=lambda g: g.tok_at)
        # **块内槽掩码**（`docs/210` §2.9）每次都从头算 —— 这一趟要**幂等**（同一份 mod
        # 被 emit 两次不会把表叠两遍）。掩码直接编进块头，所以这里只是"站点名 -> 掩码"。
        for f in funcs:
            f.slots = {}
        for f in funcs:
            f.l0 = _l0_promotable(f, toks, frozen)
            f.l1 = []
            _reset_l1(f.body)
            _l2_loop_epochs(f, toks, frozen)   # L2 在 L0 之后（它要读 f.l0）
            _l1_place(f, toks, frozen)         # L1 在最后（它要读 f.l0 与 s.l2）
        # 层梯走完再算掩码：被 L0/L1/L2 接走的站点根本不落到分配器上，也就不需要它。
        for f in funcs:
            for name, mask in _slot_plan(f, toks, frozen):
                f.slots[name] = mask



#: 块内精确（`docs/210` §2.9）能看穿的**写/读**内建。内建表里只有这两个 ——
#: `store16/32/64` 在 `bytes.lomt` 里是**用户函数**（`w64` 那样按字节拼），编译器看不进去，
#: 所以带它们的站点一律退回保守。这也解释了为什么这一格在真语料上"点得着的少"：
#: 真代码写堆结构都走辅助函数。
_SLOT_STORE = "store8"
_SLOT_LOAD = "load8"

#: 掩码**直接编在块头那个 32 位字里**（`-(mask + 2)`），所以一个站点最多认前 30 个载荷字
#: （240 字节）—— 留出符号位与"`1` = 保守"那一格。超出的站点**取消资格**：截断成
#: "只扫前 240 字节"是**不安全**的（后面的字会被漏掉），所以只能退回保守。
_SLOT_WORDS = 30


def _slot_plan(f: Func, toks: list, safe: "frozenset[str] | set[str]") -> list:
    """`f` 的**块内精确**站点 —— `[(变量名, 槽掩码), ...]`，按站点在源里出现的次序。

    规则（`docs/210` §2.9；与 L0/L1/L2 同一条纪律：**定义在 token 流上**）：

    1. 候选只能是 `let P: ptr = alloc(<任意>);` —— 尺寸可以是变量（所以 L0 接不到）；
    2. `P` 的**别名集** `A`（`let Q: ptr = R;` 且 `R` 已在 `A` 里）里每个名字，在函数体内
       **每一处**都只能是：那条声明本身、一条别名声明、`store8(N, <整数字面量>, V)` 或
       `load8(N, <偏移>)` 里**第一个实参**的那一次出现。**别的任何用法一律取消资格** ——
       交给函数、`as u64`、`ptr_add`、`free`、被赋给别的变量、被重新赋值、被遮蔽。
    3. 掩码：`store8(N, <off>, V)` 写的那个**字**（`off >> 3`）置位，**除非** `V` 可证非指针
       （一个整数字面量，或 `_ptr_free_names` 收进来的名字）。其余一切算"可能是指针"。
       一个**没有**被写过的字不是槽：程序从没往那儿放过东西，留着的是上一任住户的字节。

    **为什么安全**：第 2 条保证这块内存只有一个入口，而且**写进来的每一处编译器都看得见** ——
    于是没被置位的字**确实**收不到指针。这是 L0/L1 那条"不外逃"证明的第三次使用
    （`docs/210` §9：GC 是一次分类，不是更好的算法）。

    **只对 L3 站点可能成立**：不外逃的块早被 L0（字面量尺寸）或 L1 接走了，落不到分配器上。
    """
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    at, end = f.tok_at, f.tok_end
    params = {p.name for p in f.params}

    def close_paren(i: int) -> int:
        d = 0
        while i < end:
            if toks[i].kind != "string":
                if toks[i].val == "(":
                    d += 1
                elif toks[i].val == ")":
                    d -= 1
                    if d == 0:
                        return i
            i += 1
        return -1

    # ---- 候选与别名：先扫一遍，把 `let N : ptr = ...` 全收下来
    decl: dict[str, int] = {}            # 名字 -> 那次 `let` 的下标
    aliases: dict[str, str] = {}         # 别名 -> 它指向的名字
    cands: list[str] = []                # 候选（是 `alloc(...)` 的那些），按出现次序
    k = at
    while k < end:
        if word(k, "let") and ident(k + 1):
            nm = k + 1
            if word(nm + 1, ":") and word(nm + 2, "ptr") and word(nm + 3, "="):
                name = toks[nm].val
                if name in decl or name in params or word(nm - 1, "if"):
                    k += 1
                    continue
                if word(nm + 4, "alloc") and word(nm + 5, "("):
                    decl[name] = nm
                    cands.append(name)
                elif ident(nm + 4) and word(nm + 5, ";"):
                    decl[name] = nm
                    aliases[name] = toks[nm + 4].val
        k += 1

    out: list[tuple[str, int]] = []
    for root in cands:
        # **L0 / L1 接走的站点不要掩码**：`_ptr_only_uses` 过得去就说明这块不外逃，
        # 于是它要么被提到栈上（L0，根本不调分配器）、要么被定活插了 `free`（L1）——
        # 两条路都到不了收集器。于是这一格**只对 L3 可能成立**（`docs/210` §2.9）。
        decl_nm = decl[root]
        esc_ok, ndecl, _last = _ptr_only_uses(toks, at, end, root, decl_nm, safe)
        if esc_ok and ndecl == 1:
            continue
        # 别名闭包
        group = {root}
        changed = True
        while changed:
            changed = False
            for q, r in aliases.items():
                if r in group and q not in group:
                    group.add(q)
                    changed = True
        mask, ok = 0, True
        for nm in group:
            k = at
            while k < end:
                if ident(k) and toks[k].val == nm:
                    if k == decl[nm]:
                        k += 1
                        continue
                    # 别名声明 `let Q : ptr = nm ;`
                    if (word(k + 1, ";") and word(k - 1, "=") and word(k - 2, "ptr")
                            and word(k - 3, ":") and ident(k - 4) and word(k - 5, "let")
                            and toks[k - 4].val in group):
                        k += 1
                        continue
                    # `store8(nm, <字面量>, V)`
                    if (word(k - 2, _SLOT_STORE) and word(k - 1, "(") and word(k + 1, ",")
                            and toks[k + 2].kind == "number" and word(k + 3, ",")):
                        close = close_paren(k - 1)
                        if close < 0:
                            ok = False
                            break
                        off = _int_lit(toks[k + 2].val) & 0xFFFFFFFF
                        vs, ve = k + 4, close
                        if not _provably_free(toks, vs, ve):
                            w = off >> 3
                            if w >= _SLOT_WORDS:
                                ok = False
                                break
                            mask |= 1 << w
                        k = close + 1
                        continue
                    # `load8(nm, ...)` —— 只读，不写字
                    if word(k - 2, _SLOT_LOAD) and word(k - 1, "(") and word(k + 1, ","):
                        close = close_paren(k - 1)
                        if close < 0:
                            ok = False
                            break
                        k = close + 1
                        continue
                    ok = False
                    break
                k += 1
            if not ok:
                break
        if ok:
            out.append((root, mask))
    return out


def _provably_free(toks: list, s: int, e: int) -> bool:
    """这段右式**可证非指针**？—— 这里只认**整数字面量**（`-1` 这种带号的也算）。

    名字、表达式、调用、转型**一律**算"可能是指针"。**宁严勿宽**：多算一个槽只是少了一格
    精度，漏算一个槽就是**把活着的指针当成垃圾**。

    **为什么连名字都不认**：认名字要一层"这个名字的每一次赋值都是字面量"的不动点
    （与 `compute_safe` 同形），而它要在自举侧再养一张名字表 —— 为那几个字省下的精度
    不值那张表的钱。**这一条写明了**：`store8(p, 8, x)` 无论 `x` 是什么，第 1 个字都是槽。
    """
    if e - s == 1:
        return toks[s].kind == "number"
    return (e - s == 2 and toks[s].kind != "string" and toks[s].val == "-"
            and toks[e - 1].kind == "number")


def _l0_promotable(f: Func, toks: list,
                   safe: "frozenset[str] | set[str]" = _L0_SAFE_BUILTINS) -> dict[str, int]:
    """`gc_auto_alpha` 的 **L0**：哪些 `alloc(<常量>)` 可以**提升到栈**。回 `{变量名: 字节数}`。

    **规则定义在 token 流上，不在 AST 上 —— 这是有意的**，也是它与自举侧
    （`loment/selfhost/codegen.lomt` 的 `l0_size`）能逐字节一致的原因：token 流是两边
    **唯一都完整持有的**表示。AST 走法有两个自举侧补不平的洞：

    * 有几处"名字"**不在表达式位置上** —— `for x in …` 的 `x`、`match P::V(x)` 里的 `V`/`x`、
      被调函数名、struct 字面量的字段名。在 AST 里它们是**字符串**，递归遍历看不见它们，
      于是 AST 侧会**多**提升；而自举侧扫 token，看见的是一个无法归类的名字；
    * 反过来，AST 里有些节点根本不递归下去（例如以字段名为键的 dict），
      于是 AST 侧会**少**看见使用 —— "漏列一处就是静默放宽"。

    规则（候选形状只有一个：`let NAME: ptr = alloc(<整数字面量>);`）：

    1. `NAME` 不是形参；
    2. 在本函数体内，`NAME` 这个**标识符 token** 只出现在两种位置 ——
       (a) **恰好一处**紧跟 `let` 之后（就是那条声明本身）；
       (b) 其余每一处都紧跟 `(`，而那个 `(` 紧跟 `_L0_SAFE_BUILTINS` 里的名字，
           且 `NAME` 后面紧跟 `,` 或 `)`（即**恰好是整个第一个实参**）；
    3. 尺寸字面量（按 u32 回绕）> 0。

    (b) 等价于"只被读/写透过、从不外逃"：没被存进别处、没被传参、没被返回、没被重新赋值。
    提升后的缓冲是**入口块的一条 alloca**，活满整个函数调用，所以**不需要**"引用不出块"
    那条约束（先前的稿子多要了它，而它买不到任何东西，只是让规则更难镜像）。

    `if let` 已被解析器反糖成 `match`，所以它的绑定名在这里既不是声明、也不是使用 ——
    它落在 (b) 之外，**取消资格**（自举侧看见的是 `(` 后面一个不认识的调用名，同判）。
    """
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    at, end = f.tok_at, f.tok_end
    params = {p.name for p in f.params}
    out: dict[str, int] = {}

    d = at
    while d < end:
        nm = d + 1                                  # 候选只能长成 `let NAME : ptr = alloc ( <num> ) ;`
        if not (word(d, "let") and not word(d - 1, "if") and nm + 8 < end
                and ident(nm) and word(nm + 1, ":") and word(nm + 2, "ptr")
                and word(nm + 3, "=") and word(nm + 4, "alloc") and word(nm + 5, "(")
                and toks[nm + 6].kind == "number" and word(nm + 7, ")") and word(nm + 8, ";")):
            d += 1
            continue
        name = toks[nm].val
        size = _int_lit(toks[nm + 6].val) & 0xFFFFFFFF     # 与自举侧的 u32 回绕同值
        d += 1
        if name in params or size == 0 or name in out:
            continue
        ok, ndecl, _last = _ptr_only_uses(toks, at, end, name, nm, safe)
        if ok and ndecl == 1:
            out[name] = size
    return out


#: **系统调用的实参是"借"**（`docs/246` §5.3）：这批系统调用**在调用期间**就把用户缓冲区
#: 拷完 / 写完，**不留存那个用户指针** —— 所以把指针（或它的整数转换值）交给它们是"外借"，
#: 不是外逃；那些站点因此可以走 L0/L1，不必因为一句话整格落收集器。
#:
#: **号抄自内核头文件**（`/usr/include/x86_64-linux-gnu/asm/unistd_64.h`）—— 不凭记忆写：
#: 一个错的号就是**静默的悬垂指针**。入表的判据只有一条：**调用期间消费掉**。
#:
#: **故意不入表的**（它们才是这张表真正的内容）：
#:
#:   * `clone`(56) / `clone3`(435) —— 子进程**继续用**那个栈指针，调用返回后还留着；
#:   * `sendto`(44) / `sendmsg`(46) —— 带 `MSG_ZEROCOPY` 时内核持有那些页到发完；
#:   * `io_setup`(206) / `io_submit`(209) / `io_uring_setup`(425) / `io_uring_enter`(426) ——
#:     **登记**语义：内核留的就是那个地址；
#:   * `perf_event_open`(298) / `bpf`(321) —— 同理（attr / 指令流被长期持有）；
#:   * `ioctl`(16) —— 万能口，接什么都可能，整条不入；
#:   * `set_robust_list`(273) —— 登记一个"到进程结束都在"的链头。
_SYSCALL_BORROWS = frozenset((
    0, 1, 2, 4, 5, 6, 7,                       # read write open stat fstat lstat poll
    17, 18, 19, 20, 21, 23,                    # pread64 pwrite64 readv writev access select
    35, 42, 49, 51, 52, 54, 55,                # nanosleep connect bind getsockname getpeername setsockopt getsockopt
    59, 63, 72, 78, 79, 80, 81,                # execve uname fcntl getdents getcwd chdir fchdir
    82, 83, 84, 86, 87, 88, 89, 90, 91,        # rename mkdir rmdir link unlink symlink readlink chmod fchmod
    96, 202, 217, 228, 233,                    # gettimeofday futex getdents64 clock_gettime epoll_ctl
    257, 258, 260, 262, 263, 264, 265, 266, 267, 268, 269, 270, 271,
    280, 316, 318, 332,                        # 各种 …at 族 + utimensat renameat2 getrandom statx
))

#: **只认这三个名字**（不是"以 syscall 开头"）：自举侧只能逐名比（没有前缀比较），
#: 两边必须**同判** —— 放宽成前缀就可能在某个用户函数名上与自举侧分道扬镳。
_SYSCALL_BUILTINS = ("syscall4", "syscall6", "syscall7")


def _syscall_borrow_ok(toks: list, i: int) -> bool:
    """`NAME`（下标 `i`）是 `NAME as <类型>`、**整个实参就是这次 cast**，而且它所在的那个调用
    是 `syscall4/6/7` 之一、第一个实参是**白名单里的字面量号**？（`docs/246` §5.3 的落地形态。）

    **为什么要求 cast**：交出去的必须是"一个整数"，那一句正是程序在说"这个地址现在是个数"。
    **它是不是外逃由接收方决定**：白名单里的系统调用在调用期间消费掉它；不在名单里的一律
    按外逃算 —— **保守只能往一个方向错**（把借当逃只是错过一次提升，反过来是不安全）。
    """
    n = len(toks)
    if not (i + 3 < n and toks[i + 1].kind != "string" and toks[i + 1].val == "as"
            and toks[i + 2].kind == "ident" and toks[i + 3].kind != "string"
            and toks[i + 3].val in (",", ")")):
        return False                            # 形状不是"整个实参就是这次 cast"
    depth, j = 0, i - 1
    while j >= 0:                               # 往前找**未闭合**的那个 `(`
        tok = toks[j]
        if tok.kind == "string":
            j -= 1
            continue
        if tok.val == ")":
            depth += 1
        elif tok.val == "(":
            if depth == 0:
                break
            depth -= 1
        j -= 1
    if j < 1:
        return False
    callee, nr = toks[j - 1], toks[j + 1]
    if not (callee.kind == "ident" and callee.val in _SYSCALL_BUILTINS):
        return False
    if nr.kind != "number":
        return False                            # 号不是字面量 ⇒ 不知道是哪个 ⇒ 不入
    return (_int_lit(nr.val) & 0xFFFFFFFF) in _SYSCALL_BORROWS


def _ptr_only_uses(toks: list, at: int, end: int, name: str, nm: int,
                   safe: "frozenset[str] | set[str]" = _L0_SAFE_BUILTINS) -> tuple[bool, int, int]:
    """`name` 在 `[at, end)` 里**只被解引用透过、从不外逃**？回 `(ok, 声明次数, 最后出现下标)`。

    **L0 与 L1 共用这一条**（它们的差别只在"尺寸要不要字面量"与"插不插 `free`"）——
    抽出来是因为它俩的**安全性判据是同一个**：只要 `name` 不外逃，块就只有它这一个入口，
    于是"在最后一次用处之后还掉"就是安全的（L1 靠的就是这一句）。
    规则原文见 `_l0_promotable` 的 1–3 条；`last` 是**任意**出现的最大值（含声明处），
    一次都不出现时回 `-1`。
    """
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    def safe_callee(i: int) -> bool:
        return (ident(i) and toks[i].val in safe
                and not word(i - 1, ".") and not word(i - 1, ":"))

    ok, ndecl, last = True, 0, -1
    k = at
    while k < end:
        if ident(k) and toks[k].val == name:
            last = k
            if k == nm:
                ndecl += 1
            elif word(k - 1, "let"):
                ok = False                          # 第二处声明（遮蔽）
                break
            elif word(k + 1, "="):
                ok = False                          # 重新赋值
                break
            elif not ((word(k - 1, "(") and safe_callee(k - 2)
                       and (word(k + 1, ",") or word(k + 1, ")")))
                      or _syscall_borrow_ok(toks, k)):
                ok = False                          # 其余任何用法：外逃
                break
        k += 1
    return ok, ndecl, last


def _l1_place(f: Func, toks: list,
              safe: "frozenset[str] | set[str]" = _L0_SAFE_BUILTINS) -> None:
    """`gc_auto_alpha` 的 **L1（定活）**：把 `free` 挂到 AST 上（`s.l1` / `f.l1`）。

    **这一层证的是什么**：某个 `alloc` 出来的块，在**它最后一次被用到之后**立刻还掉。
    资格与 L0 **同一条**（`_ptr_only_uses`）—— 只被 `load8`/`store8`/`atomic_add`
    当整个首参透过、从不外逃 ⇒ **块只有这一个入口** ⇒ 还掉它不可能被别处再摸到。

    **声明必须在函数体的顶层**（不在任何 `{` 里）。这一条是**安全**要的：局部槽在入口块
    统一 alloca，而**赋值只发生在声明那一处**；声明若在条件里、那一支又没走，槽里是
    **未初始化**的值 —— 在函数末尾 `free` 它就是在 free 一个垃圾指针。顶层声明 ⇒
    每次调用**恰好执行一次、无条件** ⇒ 那个槽一定已经存好了块。**代价是明写的**：
    循环体内分配的那一类（L2 的地盘）L1 一律不接。

    **落点**（两个，按序取第一个成立的）：
      1. **最后一次用处之后、最早那条 `return` 之前**（在它求值之后、`ret` 之前 ——
         返回的那个值就是从块里读出来的，得先读到）；
      2. 都不成立 → **函数末尾**（在 `ret`/`unreachable` 之前、`roots_pop` 之前）。

    两处的共同点是：**都落在"最后一次用处之后"** —— 最后一次用处按 token 序取，
    而 token 序是**动态序的超集**（文本上更靠后的用处必然更晚执行），所以"所有用处都在这
    个位置之前"在两种序下都成立。落点 1 的 `tok_end > last` 是它的判据（结束在用处之后的
    `return` 才作数）。落点 2 之所以有用：块是**每次调用**新分配的，在函数返回前还掉就足以
    让**调用方**的循环不涨 —— 而调用方那一格 L2 管不到（它的体里只有调用、没有分配）。

    **原本还有第三个落点**（"含最后一次用处的最外层循环之后"），**撤掉了**：它要求知道
    外层循环的跨度，而自举侧的发射器手上的只有**当前**这一层的跨度（它不维护循环栈）
    —— 为了那一点更早的回收，两边要多一处容易写歪的推理。换掉的代价是明写的：
    用处落在循环里、函数又往下写了不少语句时，块要活到函数末尾（或者那条 `return`）
    才还 —— **晚一点还，不是不还**。

    **与 L2 不重叠**（不成立就会 double free）：L2 认领的是"**循环体内**分配、且名字不出体"
    的块；而这里要求声明在**顶层**（不在任何循环里）⇒ 两者**不可能指同一块** ——
    这是"声明必须顶层"这条限制白送的一个性质，所以这里不用再单独判 `s.l2`。

    **为什么不靠 `docs/206`**：那条路的证据是 E006（移出之后不许再用），而
    `_is_copy_type` 把 `ptr` 算作 **Copy** ⇒ 传一个 `ptr` 根本不算"移出" ⇒ E006 对 `ptr`
    一句话也说不了。所以 L1 与 L0/L2 一样，**长在 token 流上**（`docs/210` §2.5 记这条更正）。
    """
    cands: list[tuple[str, int, int]] = []      # (名字, 声明处, 最后一次用处)
    params = {p.name for p in f.params}
    at, end = f.tok_at, f.tok_end
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    d = at
    while d < end:
        nm = d + 1
        if not (word(d, "let") and not word(d - 1, "if")
                and ident(nm) and word(nm + 1, ":") and word(nm + 2, "ptr")
                and word(nm + 3, "=") and word(nm + 4, "alloc") and word(nm + 5, "(")):
            d += 1
            continue
        # `alloc(...)` 的右括号（尺寸**不限** —— 那是 L0 的事），紧跟一个 `;`
        j, depth = nm + 5, 0
        while j < end:
            if word(j, "("):
                depth += 1
            elif word(j, ")"):
                depth -= 1
                if depth == 0:
                    break
            j += 1
        d += 1
        if j >= end or not word(j + 1, ";"):
            continue
        name = toks[nm].val
        if name in params or name in f.l0:
            continue            # 形参没有"这次分配"；L0 已经提到栈上的根本不碰 arena
        # **声明必须在函数体的顶层**（不在任何 `{` 里 —— 也就是不在 if / match / 循环 / 裸块里）。
        # 这一条是**安全**要的，不是洁癖：局部槽在入口块统一 alloca，**赋值只发生在声明那一处**；
        # 声明若在条件里而那一支没走，槽里是**未初始化**的值 —— 在函数末尾 free 它就是在
        # free 一个垃圾指针。顶层声明 ⇒ 每次调用**恰好执行一次、无条件** ⇒ 那个槽一定已赋值。
        depth0 = 0
        for k in range(at, nm):
            if word(k, "{"):
                depth0 += 1
            elif word(k, "}"):
                depth0 -= 1
        if depth0 != 0:
            continue
        ok, ndecl, last = _ptr_only_uses(toks, at, end, name, nm, safe)
        if ok and ndecl == 1 and last >= 0 and last != nm:
            cands.append((name, nm, last))
    if not cands:
        return

    rets: list[tuple[int, int, object]] = []

    def walk(stmts: list, cur: int) -> int:
        """收所有 `return` 的跨度。**AST 序 == token 序**，所以跨度可以顺着游标在 token 流里
        找（`Return` 没有自己的跨度字段；这一招省掉一次解析器改动 —— 而解析器改动要两个
        实现 + 种子一起动）。"""
        for s in stmts:
            if isinstance(s, (While, For)):
                cur = walk(s.body, cur)
            elif isinstance(s, If):
                cur = walk(s.then, cur)
                cur = walk(s.otherwise, cur)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    cur = walk(b, cur)
            elif isinstance(s, Return):
                k = cur
                while k < end and not (ident(k) and toks[k].val == "return"):
                    k += 1
                j, d = k + 1, 0
                while j < end:
                    if word(j, "("):
                        d += 1
                    elif word(j, ")"):
                        d -= 1
                    elif d == 0 and word(j, ";"):
                        break
                    j += 1
                rets.append((k, j, s))
                cur = j + 1
        return cur

    walk(f.body, at)
    for name, _decl, last in cands:
        # 落点一：**最后一次用处之后最早的那条 `return` 之前**（求值完、`ret` 之前）。
        # 判据是 `tok_end > last` —— 这条 `return` 的**结束**在用处之后 ⇒ 它肯定在用处之后
        # 跑（`return load8(p, 0);` 那一格正是靠这条选中的，也是最常见的那一格）。
        # 为什么安全：`return` 是**终结**语句，每次调用至多执行一次 —— 哪怕它本身在某个
        # 循环体内（走到它就出函数了，不会再来一遍），插在它前面也不会多跑一次。
        after = [r for r in rets if r[1] > last]
        if after:
            min(after, key=lambda r: r[1])[2].l1.append(name)
            continue
        # 落点二：函数末尾（在 `ret`/`unreachable` 之前、`roots_pop` 之前）。
        # void 函数常常落在这儿（它没有 `return` 语句）。
        f.l1.append(name)


def _int_lit(v: str) -> int:
    """token 原文 -> 整数（`0x`/`0X` 前缀十六进制，否则十进制）。**与自举侧 `tok_int` 同表。**"""
    try:
        return int(v, 16) if v[:2].lower() == "0x" else int(v)
    except ValueError:
        return 0


def _l2_ok(toks: list, at: int, end: int, fend: int, l0: dict[str, int],
           safe: "frozenset[str] | set[str]" = _L0_SAFE_BUILTINS) -> bool:
    """循环体能不能在末尾**批量归还**（`docs/210` §2 的 **L2**）。**规则也定义在 token 流上。**

    这是"回收时间而不是对象"今天能落地的那一半：**体的末尾把分配器的前沿退回去**，
    整段一次性不存在 —— 不扫描、不逐个释放、不用链表。代价写在规则里：

    1. 体内**至少有一个** `alloc`（否则没什么可归还的，发了只是白费两条指令）；
    2. 体内**不许有 `str_concat`** —— 它的落点也是堆，而 `str` 的逃逸在这里看不见
       （`str_ptr` 会把里面的指针变成一个整数交出去）。这是这一层今天的边界；
    3. 体内**每一个** `alloc` 都必须长成 `let NAME: ptr = alloc(...)`（尺寸随便 ——
       这一层不要求常量，那是 L0 的事）。**不满足就整格取消**：认不出来的分配无从证明它不出体；
    4. 那些 `NAME` 在**本函数体内、循环体之外**一处都不许出现（出现即当作"出体了"）；
    5. 在体内，`NAME` 只许出现在它的声明处，或**恰好作为** `load8`/`store8`/`atomic_add`
       的整个第一个实参。**被重新赋值、被返回、被存进别处、被传参 —— 一律取消。**

    **`L0` 已经提到栈上的名字豁免第 4、5 条**：它根本不碰 arena，既不需要这次归还，
    也不该因为它挡掉旁边那些真在堆上的分配（`l0` 就是本函数那张表）。

    **这条规则的价钱是"整体取消"**：体内**一个**分配出体，整个体这一格就不能归还 ——
    不是只废掉那一个。这是**用表达力换零成本**（`docs/210` §4.4），不是缺陷：
    要更细的粒度就得看生命周期，那是 L1/L3 的活。
    """
    n = len(toks)

    def word(i: int, text: str) -> bool:
        return 0 <= i < n and toks[i].kind != "string" and toks[i].val == text

    def ident(i: int) -> bool:
        return 0 <= i < n and toks[i].kind == "ident"

    def safe_callee(i: int) -> bool:
        return (ident(i) and toks[i].val in safe
                and not word(i - 1, ".") and not word(i - 1, ":"))

    nalloc = 0
    for k in range(at, end):
        if word(k, "str_concat"):
            return False
        if word(k, "free"):
            # 体内不许 `free`：**前沿在体内只增不减**，末尾那一条无条件写回才对。
            # **这件事要证明，因为它管的范围比体内那一层调用大**（被调函数里也能 `free`）：
            #   ① 能把前沿往**下**的只有 `__loment_free`，而它**只在"释放的那块正好顶到
            #      前沿"时才往下**；
            #   ② 顶到前沿的那块，要么是**体内分配的** —— 那要把它**交给**别人（被调函数 /
            #      另一个变量 / 返回 / 派生指针），第 4 条已经把它挡掉了；要么是**体外分配的**
            #      —— 而它**不可能跨过 `sv`**（跨过就与体内那些块重叠了，而分配器不给出
            #      重叠的块）。
            #   于是体末尾 `off` 必然 >= `sv`，写回 `sv` 就是回退。
            # **而 `min` 要 `select`，自举镜像链接器 `lomelf.py` 的 `lower` 里没有 `select`**
            # （而包内链接器就是它）—— 所以这一条既是设计，也是那道硬约束逼出来的形态。
            return False
        if word(k, "alloc"):
            nalloc += 1
    if nalloc == 0:
        return False

    ncand = 0
    nlive = 0                              # 真在 arena 上的那几个（L0 提到栈上的不算）
    for d in range(at, end):
        nm = d + 1
        if not (word(d, "let") and not word(d - 1, "if") and nm + 5 < end
                and ident(nm) and word(nm + 1, ":") and word(nm + 2, "ptr")
                and word(nm + 3, "=") and word(nm + 4, "alloc") and word(nm + 5, "(")):
            continue
        ncand += 1
        name = toks[nm].val
        if name in l0:
            continue                       # 已经在栈上：既不欠这次归还，也不挡别人
        nlive += 1
        ndecl = 0
        for k in range(at, end):
            if ident(k) and toks[k].val == name:
                if k == nm:
                    ndecl += 1
                elif word(k - 1, "let"):
                    return False           # 体内第二处声明（遮蔽）：认不清哪个是哪个
                elif word(k + 1, "="):
                    return False           # 重新赋值
                elif not ((word(k - 1, "(") and safe_callee(k - 2)
                           and (word(k + 1, ",") or word(k + 1, ")")))
                          or _syscall_borrow_ok(toks, k)):
                    return False
        if ndecl != 1:
            return False
        # **只查体之后** [end, fend)：体**之前**不可能用到体里声明的那个名字（作用域），
        # 所以那里出现同名不构成逃逸。
        #
        # **扫到同一层下一次同名 `let` 就停**（`#364` 的第三条边界）。语言**没有块作用
        # 域**（`docs/228` 的根），所以一个名字被**新的** `let` 重新声明之后，后面那些出现
        # 就属于**新的**那个变量，不再证明这一块出体了。停下是 **sound** 的：在那一处之前
        # 的每一次出现都还指这一块。
        #
        # **另一个块里的同名 `let` 不能当成"换了主人"**（#714）：它可能藏在一条**没跑**
        # 的分支里（体后面一个不成立的 `if { let NAME = … }`），那之后名字**没有**换主人，
        # 后面的出体使用仍然指这一块。静态上认不出那条路跑没跑 ⇒ 整格取消（保守）。
        # 深度用 `{`/`}` 数：`end` 是体自己的 `}`，扫过它之后**同一层**是 -1，嵌进一个块
        # 是 0，落回外层是 <= -2。
        #
        # 只认 `let` 这一种重声明形状（这个语言里也只有它）：认不出来的形状一律**按没重
        # 声明处理**（照旧取消），所以这条规则只可能**少**放宽、不会多放宽。
        depth = 0
        for k in range(end, fend):
            if ident(k) and toks[k].val == name:
                if word(k - 1, "let"):
                    if depth != -1:
                        return False       # 另一个块里的重声明: 那条路可能没跑
                    break                  # 同一层的重声明: 换了主人
                return False               # 出体了
            if word(k, "{"):
                depth += 1
            elif word(k, "}"):
                depth -= 1
    if nlive == 0:
        return False                       # 体内的分配都提到栈上了 -> 没什么可归还的
    return nalloc == ncand                 # 有认不出来的分配 -> 整格取消


def _l2_loop_epochs(f: Func, toks: list,
                    safe: "frozenset[str] | set[str]" = _L0_SAFE_BUILTINS) -> None:
    """把 `f` 里每个 `while`/`for` **以及 `if` 的两支**的 `l2` 填上（`docs/210` §2.3 的 L2）。

    `if` 的两支与循环体是**同一个形状的块**（`docs/210` §7 原先把它列在"没做"里），规则也
    是同一条（`_l2_ok`）—— 只是"体"换成了那一支的 token 跨度。`else` 支为空时
    `else_end == 0`，`else_end > else_at` 天然不成立，不用再加一个"有没有 else"的判断。
    """
    def walk(stmts: list) -> None:
        for s in stmts:
            if isinstance(s, While):
                s.l2 = _l2_ok(toks, s.tok_at, s.tok_end, f.tok_end, f.l0, safe)
                walk(s.body)
            elif isinstance(s, For):
                s.l2 = _l2_ok(toks, s.tok_at, s.tok_end, f.tok_end, f.l0, safe)
                walk(s.body)
            elif isinstance(s, If):
                if s.then_end > s.then_at:
                    s.then_l2 = _l2_ok(toks, s.then_at, s.then_end, f.tok_end, f.l0, safe)
                if s.else_end > s.else_at:
                    s.else_l2 = _l2_ok(toks, s.else_at, s.else_end, f.tok_end, f.l0, safe)
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk(b)

    walk(f.body)


def _collect_locals(f: Func, enums: dict | None = None,
                    funcs: dict | None = None,
                    structs: dict | None = None) -> list[tuple[str, str, int]]:
    """按序收集需 alloca 的局部 (不含参数, 去重) 与它们的**声明行**。

    行号是 M59 的变量信息要的 (`DILocalVariable.line`); For 变量与 match 绑定按上下文推断。

    **funcs / structs 必须传进来**(2026-09-15 修): 这里要推 `match` 主体与 `for` 上下界的
    类型, 而被匹配值可能是**函数调用**、上下界可能是**字段访问** —— 给 `expr_type` 传空字典
    会让它推不出类型, 于是枚举查不到、绑定变量根本不会被收集, 后面发射期 `self.vars[bind]`
    直接 KeyError 崩掉(Python 栈回溯, 不是诊断)。当时只有 `use g()` 这种"主体是内联调用"
    的写法会踩到, 所以 `let r = g(); if let ... = r` 看着没事。
    """
    enums = enums or {}
    funcs = funcs or {}
    structs = structs or {}
    out: list[tuple[str, str, int]] = []
    seen = {p.name for p in f.params}
    scope: dict[str, str] = {p.name: p.type for p in f.params}

    def walk(stmts: list) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.name not in seen:
                    out.append((s.name, s.type, s.line))
                    seen.add(s.name)
                scope[s.name] = s.type
            elif isinstance(s, If):
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, While):
                walk(s.body)
            elif isinstance(s, For):
                vt = (expr_type(s.lo, scope, funcs, structs)
                      or expr_type(s.hi, scope, funcs, structs) or "u32")
                if s.var not in seen:
                    out.append((s.var, vt, s.line))
                    seen.add(s.var)
                scope[s.var] = vt
                walk(s.body)
            elif isinstance(s, Match):
                sty = expr_type(s.subject, scope, funcs, structs)
                ed = enums.get(sty) if sty else None
                for pat, body in s.arms:
                    if pat is not None and pat.bind and ed is not None:
                        pt = ed.payloads.get(pat.variant)
                        if pt and pat.bind not in seen:
                            out.append((pat.bind, pt, pat.line))
                            seen.add(pat.bind)
                        if pt:
                            scope[pat.bind] = pt
                    walk(body)

    walk(f.body)
    return out


class _Ir:
    """结构化发射: alloca/load/store + 基本块; 优化交给 clang (docs/144 §3)。"""

    def __init__(self, funcs: dict, consts: dict, f: Func,
                 structs: dict | None = None, enums: dict | None = None,
                 coverage: bool = False, cov_counter: list | None = None,
                 dbg_scope: int | None = None, dbg_lines: dict | None = None,
                 dbg_meta: list | None = None, dbg_types: dict | None = None,
                 gc_alpha: bool = False, gc_auto: bool = False,
                 gc_barrier: bool = True):
        self.funcs, self.consts, self.f = funcs, consts, f
        self.structs = structs or {}
        self.enums = enums or {}
        self.out: list[str] = []
        self.tmp = 0
        self.lbl = 0
        self.vars: dict[str, tuple[str, str]] = {}  # 名 -> (loment 类型, alloca)
        self.globals: list[str] = []                # 字符串常量等模块级声明
        self.terminated = False
        self.coverage = coverage                    # M63: IR 级块覆盖
        self.cov_counter = cov_counter if cov_counter is not None else [0]
        self.cov_ids: list[int] = []
        self.dbg_scope = dbg_scope                  # M59: 本函数的 DISubprogram
        self.dbg_lines = dbg_lines if dbg_lines is not None else {}
        self.dbg_meta = dbg_meta                    # M59: 共享元数据行
        #: **L0 提升表**（`docs/210` §2，只在 `gc_auto_alpha` 下非空）：`{局部名: 字节数}`。
        #: 其余档**一行都不动** —— 默认档不许改变任何现有程序的产物（`docs/175` §3.4 五条之一）。
        self.l0: dict[str, int] = f.l0 if gc_alpha else {}
        #: **块内槽掩码**（`docs/210` §2.9，同样只在 alpha 档非空）：`{局部名: 掩码}`。
        #: 命中就在那条 `alloc` 之后把块头的借出标记改写成 `-(mask + 2)` —— 收集器因此
        #: 知道**这一块哪几个字要扫**。保守站点一个字节都不变（标记还是那个 `-1`）。
        self.slots: dict[str, int] = getattr(f, "slots", {}) if gc_alpha else {}
        #: **`gc_auto`**（`docs/210` §2.1 的"自动"那一档）：本帧要不要 push 根表、多少格。
        #: 与 `gc_alpha` **互斥**（两档不同时成立），所以下面那些挂点不会互相打架。
        self.gc_auto = gc_auto
        #: **`gc_auto_alpha`**（`docs/210` §2.4 的"自适应"那一档）：它**也有收集器**了 ——
        #: 此前 alpha 的 L3 残差没人收（只有分配器）。`gc_any` = 要根表/收集器的任意一档。
        self.gc_alpha = gc_alpha
        self.gc_any = gc_auto or gc_alpha
        #: **写屏障要不要挂**：alpha 档**且这份单元真的要那块 arena**。一份把 `alloc` 全
        #: L0 提走的单元**没有堆**（`docs/210` §2.2 那句"连分配器都不剩"），而屏障引用堆
        #: —— 挂上去就是未定义符号（实测：`FujoJTOP/loment#239` 一修好，这条立刻露出来）。
        #: 与自举侧同一个判据（那边是 `heap_needed` 预扫描出来的 `s+516`）。
        self.gc_barrier = gc_barrier
        self.rootk = 0
        self.dbg_types = dbg_types if dbg_types is not None else {}  # M59: 类型 -> DIBasicType
        self.dbg_loc: int | None = None             # 当前语句的 DILocation
        self.cur_label: str | None = None           # 当前基本块标签 (phi 前驱用)

    def locals_prologue(self, f: Func, enums: dict, funcs: dict, structs: dict) -> None:
        """入口块的局部 alloca。**L0 提升的缓冲也在这一遍里发**（`docs/210` §2）。

        （两处调用点原先各抄了一遍这段循环 —— 加 L0 之后它要动两处，收成一个方法。）
        """
        for name, ty, ln in _collect_locals(f, enums, funcs, structs):
            self.w(f"%{name}.addr = alloca {self.ll(ty)}")
            self.vars[name] = (ty, f"%{name}.addr")
            self.dbg_declare(name, ty, f"%{name}.addr", ln)
            n = self.l0.get(name)
            if n:
                # 缓冲的类型是 **`[K x i64]`**，不是 `[N x i8]`：单条数组类型两个链接器都吃
                # （`ty_size` 认 `[...]`），而且**天然 8 字节对齐** —— 与 `__loment_alloc`
                # 返回的地址同对齐，于是 `store64` 那条默认对齐假设成立（`[N x i8]` 只对齐 1）。
                self.w(f"%{name}.buf = alloca [{(n + 7) // 8} x i64]")

    def dbg_for(self, line: int) -> int | None:
        """M59: 行号 -> DILocation id (每函数一份, 由 emit_llvm 分配)。"""
        if self.dbg_scope is None or self.dbg_meta is None:
            return None
        if line not in self.dbg_lines:
            i = len(self.dbg_meta)
            self.dbg_meta.append(
                f"!{i} = !DILocation(line: {line}, column: 1, scope: !{self.dbg_scope})")
            self.dbg_lines[line] = i
        return self.dbg_lines[line]

    def dbg_ty(self, ty: str) -> int:
        """M59: 局部变量类型 -> DIBasicType (每类型一份, 跨函数共享)。"""
        if ty in self.dbg_types:
            return self.dbg_types[ty]
        if ty == "bool":
            size, enc = 8, "DW_ATE_boolean"
        elif ty == "ptr":
            size, enc = 64, "DW_ATE_address"
        elif ty in ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"):
            size = int(ty[1:])
            enc = "DW_ATE_signed" if ty[0] == "i" else "DW_ATE_unsigned"
        else:  # str / 切片 / 数组 / 结构体 / 枚举: 只记名字, 尺寸交给 LLVM (size: 0)
            size, enc = 0, "DW_ATE_unsigned"
        i = len(self.dbg_meta)
        self.dbg_meta.append(
            f'!{i} = !DIBasicType(name: "{ty}", size: {size}, encoding: {enc})')
        self.dbg_types[ty] = i
        return i

    def dbg_declare(self, name: str, ty: str, ptr: str, line: int, arg: int = 0) -> None:
        """M59: 变量声明点 —— DILocalVariable 元数据 + 一条 `#dbg_declare` 记录。

        用**新式调试记录**而不是旧内建 `llvm.dbg.declare`: 本工具链的 clang 22 自己发射的
        就是记录形态 (探针实测), 记录不需内建声明, 且附着于**下一条指令** —— 序言里每个
        alloca 后面总还有指令 (至少函数体或收尾的 ret), 所以位置安全。
        记录**不能**带 `!dbg` 后缀 (`self.w` 会加), 所以直接写进 self.out。
        """
        if self.dbg_scope is None or self.dbg_meta is None:
            return
        loc = self.dbg_for(line)
        if loc is None:
            return
        vt = self.dbg_ty(ty)
        i = len(self.dbg_meta)
        a = f", arg: {arg}" if arg > 0 else ""
        self.dbg_meta.append(
            f'!{i} = !DILocalVariable(name: "{name}"{a}, scope: !{self.dbg_scope}, '
            f'file: !1, line: {line}, type: !{vt})')
        self.out.append(f"  #dbg_declare(ptr {ptr}, !{i}, !DIExpression(), !{loc})")

    def cov_hit(self) -> None:
        """M63: 在每个基本块开头对 @__loment_cov[块号] 加一。"""
        if not self.coverage:
            return
        idx = self.cov_counter[0]
        self.cov_counter[0] += 1
        self.cov_ids.append(idx)
        if idx >= 256:
            raise LomError(self.f.line, 1, "覆盖计数块数超过 256 (M63 上限)")
        g, v, n = self.t(), self.t(), self.t()
        self.w(f"{g} = getelementptr inbounds [256 x i64], ptr @__loment_cov, i32 0, i32 {idx}")
        self.w(f"{v} = load i64, ptr {g}")
        self.w(f"{n} = add i64 {v}, 1")
        self.w(f"store i64 {n}, ptr {g}")

    def ll(self, t: str) -> str:
        return _ll_type(t, self.structs, self.enums)

    def t(self) -> str:
        self.tmp += 1
        return f"%t{self.tmp}"

    def l(self, tag: str) -> str:
        self.lbl += 1
        return f"L{self.lbl}_{tag}"

    def w(self, s: str) -> None:
        if self.dbg_loc is not None:
            s += f", !dbg !{self.dbg_loc}"
        self.out.append("  " + s)

    def w_raw(self, s: str) -> None:
        """追加一行**不加** `!dbg` 后缀的文本 —— 给**多行指令的续行**用。

        `!dbg` 必须挂在整条指令的末尾 (对 `switch` 就是 `]` 那一行); 逐行都挂会写出
        `switch i32 %x, label %L [, !dbg !7` 这种非法 IR —— 这是 M59 收口时抓到的既有 bug
        (旧用例只喂没有 `match` 的语料, 从未触发)。
        """
        self.out.append("  " + s)

    def label(self, name: str) -> None:
        self.out.append(f"{name}:")
        self.terminated = False
        self.cur_label = name
        self.cov_hit()

    def jump(self, name: str) -> None:
        if not self.terminated:
            self.w(f"br label %{name}")
        self.terminated = True

    # -- L2：块纪元的开与关（`docs/210` §2）
    def epoch_open(self, s) -> str | None:
        """循环体入口记下**分配器前沿**（`@__loment_off`）。回 None = 这一格没通过认证。

        值就是一条 SSA 指令，不需要 alloca —— 循环体那块标签**支配**体内的一切，
        自然也支配回边之前那个位置。
        """
        return self.epoch_open_flag(s.l2)

    def epoch_open_flag(self, ok: bool) -> str | None:
        """同上，但"这一格开不开"由**调用方算好的布尔**给 —— `if` 的两支没有语句对象可读。
        """
        if not ok:
            return None
        v = self.t()
        self.w(f"{v} = load i32, ptr @__loment_off")
        return v

    def epoch_close(self, s, sv: str | None) -> None:
        """循环体末尾**批量归还**：把前沿写回入口那一刻的值。整段一次性不存在，不扫描。

        **一条 `store` 就够**，因为规则（`_l2_ok`）要求体内**没有 `free`** —— 于是前沿在体内
        **只增不减**，末尾那个值必然 `>= sv`，写回即回退。（若体内能 `free`，这里就得取
        `min` —— 而 `min` 要 `select`，**自举镜像链接器不支持 `select`**（`lomelf.py` 的
        `lower` 里没有它），而包内链接器就是它。所以这一条既是设计也是**约束**。）

        体已经终止（`return`/`break`）时不发：那条路少归还一次，**不错**。
        """
        if sv is None or self.terminated:
            return
        self.w(f"store i32 {sv}, ptr @__loment_off")

    def type_scope(self) -> dict[str, str]:
        return {k: v[0] for k, v in self.vars.items()}

    # -- gc_auto：精确根（`docs/210` §4.2）+ 自动收集的挂点
    @staticmethod
    def _root_ty(t: str) -> bool:
        """哪些类型算"槽里有指针"：`ptr` / `str`（`{ptr,i64}`）/ 切片（同形）。

        **`struct` 与枚举的字段在本后端只装标量**（`_ll_type` 拒绝别的），所以不必递归进聚合 ——
        那条限制在这里变成一条好处。
        """
        return t == "ptr" or t == "str" or _is_slice(t)

    def roots_push(self, f: Func) -> None:
        """`gc_auto`：把本帧"持有指针的槽"登记进根表。

        **槽的顺序必须与自举侧同序**（形参在前、局部按声明序）—— 那是逐字节判据的一部分。
        表满（1024 格，递归深了才可能）**点名拒**，不静默写穿。
        """
        if not self.gc_any:
            return

        def slots(name: str) -> int:
            """这个名字占几格根表：L0 提升过的缓冲按它的**字数**算，其余各占一格。"""
            n = self.l0.get(name)
            return (n + 7) // 8 if n else 1

        self.rootk = sum(slots(nm) for nm, (ty, _) in self.vars.items() if self._root_ty(ty))
        if self.rootk == 0:
            return
        n0 = self.t()
        self.w(f"{n0} = load i32, ptr @__loment_rootn")
        i = 0
        for name, (ty, slot) in self.vars.items():
            if not self._root_ty(ty):
                continue
            sz = self.l0.get(name)
            if sz:
                # **L0 提升的缓冲：它的每一个 8 字节字各占一格**（`FujoJTOP/loment#362`）。
                # 那个槽里存的是**栈**地址（`%NAME.buf`），而根扫拿槽里的值去减堆基址，
                # 得到一个巨大的无符号数、`ult 65536` 为假 —— 于是整块缓冲**从来没人扫**。
                # 而 L0 的规则**不禁止**缓冲里装堆指针：`_ptr_only_uses` 会把"只解引用"
                # 的函数（如 `wr64`）吸收进安全集，于是那种函数可以把任意 u64 写进缓冲。
                # 只登记**字**的地址就够了 —— 根扫本来就是"读那一格、看它像不像堆内地址"，
                # 收集器一侧一个字都不用改。
                k = (sz + 7) // 8
                for j in range(k):
                    idx = self.t()
                    self.w(f"{idx} = add i32 {n0}, {i}")
                    gp = self.t()
                    self.w(f"{gp} = getelementptr [1024 x i64], ptr @__loment_roots,"
                           f" i32 0, i32 {idx}")
                    bp = self.t()
                    self.w(f"{bp} = getelementptr [{k} x i64], ptr %{name}.buf, i32 0, i32 {j}")
                    v = self.t()
                    self.w(f"{v} = ptrtoint ptr {bp} to i64")
                    self.w(f"store i64 {v}, ptr {gp}")
                    i += 1
                continue
            idx = self.t()
            self.w(f"{idx} = add i32 {n0}, {i}")
            gp = self.t()
            self.w(f"{gp} = getelementptr [1024 x i64], ptr @__loment_roots, i32 0, i32 {idx}")
            v = self.t()
            self.w(f"{v} = ptrtoint ptr {slot} to i64")
            self.w(f"store i64 {v}, ptr {gp}")
            i += 1
        n1 = self.t()
        self.w(f"{n1} = add i32 {n0}, {self.rootk}")
        self.w(f"store i32 {n1}, ptr @__loment_rootn")
        ov = self.t()
        self.w(f"{ov} = icmp ugt i32 {n1}, 1024")
        ok, bad = self.l("rok"), self.l("rovf")
        self.w(f"br i1 {ov}, label %{bad}, label %{ok}")
        self.terminated = True
        self.label(bad)
        self.w("call void @__loment_abort()")
        self.w("unreachable")
        self.terminated = True
        self.label(ok)

    def l1_free(self, names) -> None:
        """`gc_auto_alpha` 的 **L1**：把这些名字的块还掉（`docs/210` §2.5）。

        发的 IR 与用户手写 `free(NAME);` **逐字节同形**（先一条 `load ptr`，再 `call
        void @__loment_free(ptr %t)`）—— 参考侧那句在 `builtin` 的 `free` 分支里，
        这里照抄同一形状，两个实现才对得上。
        """
        for name in names or ():
            _, slot = self.vars[name]
            t = self.t()
            self.w(f"{t} = load ptr, ptr {slot}")
            self.w(f"call void @__loment_free(ptr {t})")

    def roots_pop(self) -> None:
        """离开本帧：把根表的游标退回去（与 `roots_push` 成对）。"""
        if not self.gc_any or self.rootk == 0:
            return
        n = self.t()
        self.w(f"{n} = load i32, ptr @__loment_rootn")
        m = self.t()
        self.w(f"{m} = sub i32 {n}, {self.rootk}")
        self.w(f"store i32 {m}, ptr @__loment_rootn")

    # -- 表达式 -> (loment 类型, 值)
    def expr(self, e, want: str | None = None) -> tuple[str, str]:
        if isinstance(e, IntLit):
            if want == "ptr":  # M73: 空指针字面量
                return "ptr", "null"
            ty = want or "i32"
            if ty in INT_TYPES or ty == "bool":
                return ty, str(_wrap_int(e.value, ty))
            return ty, str(e.value)
        if isinstance(e, BoolLit):
            return "i1", ("1" if e.value else "0")
        if isinstance(e, Ident):
            if e.name in self.consts:  # 常量在生成期内联
                ty, val = self.consts[e.name]
                if ty in INT_TYPES or ty == "bool":
                    val = _wrap_int(val, ty)
                return ty, str(val)
            if e.name not in self.vars:
                raise LomError(e.line, 1, f"未解析的变量 {e.name} —— 后端拿到的单元不完整")
            ty, ptr = self.vars[e.name]
            r = self.t()
            self.w(f"{r} = load {self.ll(ty)}, ptr {ptr}")
            return ty, r
        if isinstance(e, Un):
            if e.op in ("&", "&mut"):  # M3/M4: 数组 -> 切片值
                if not isinstance(e.expr, Ident):
                    raise LomError(e.line, 1, f"native: {e.op} 只支持数组变量")
                ty, ptr = self.vars[e.expr.name]
                if not _is_array(ty):
                    raise LomError(e.line, 1, f"native: {ty} 不是数组")
                p0 = self.t()
                self.w(f"{p0} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 0")
                v1 = self.t()
                self.w(f"{v1} = insertvalue {{ ptr, i64 }} undef, ptr {p0}, 0")
                v2 = self.t()
                self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v1}, i64 {_array_len(ty)}, 1")
                st = f"mut [{_array_elem(ty)}]" if e.op == "&mut" else f"[{_array_elem(ty)}]"
                return st, v2
            ty, v = self.expr(e.expr, want)
            it = _ir_t(ty)
            r = self.t()
            if e.op == "-":
                self.w(f"{r} = sub {it} 0, {v}")
            elif it == "i1":
                self.w(f"{r} = xor i1 {v}, true")
            else:
                self.w(f"{r} = xor {it} {v}, -1")
            return ty, r
        if isinstance(e, Bin):
            return self.binop(e, want)
        if isinstance(e, StrLit):
            b = e.value.encode("utf-8")
            name = f"@.str.{self.f.name}.{len(self.globals)}"
            esc = "".join(f"\\{x:02X}" for x in b)
            self.globals.append(
                f'{name} = private unnamed_addr constant [{len(b)} x i8] c"{esc}"'
            )
            p = self.t()
            self.w(f"{p} = getelementptr inbounds [{len(b)} x i8], ptr {name}, i64 0, i64 0")
            v = self.t()
            self.w(f"{v} = insertvalue {{ ptr, i64 }} undef, ptr {p}, 0")
            v2 = self.t()
            self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v}, i64 {len(b)}, 1")
            return "str", v2
        if isinstance(e, Cast):  # 整数/布尔转换
            # **整数字面量 `as iN`：字面量的值就是值**（#694）。不能再走下面
            # `self.expr(e.expr, None)` 那条"无期望类型 ⇒ 按 i32 定宽"的路：
            # `253402300800 as i64` 会先被截成 32 位（-769664）再 zext，得到
            # 4294197632。按目标宽度直接发，值才是原文写的那个。自举侧
            # `expr_atom` 的整数字面量分支同一处一起改，IR 要保持逐字节相同。
            if isinstance(e.expr, IntLit) and e.type in INT_TYPES:
                return e.type, str(_wrap_int(e.expr.value, e.type))
            st = expr_type(e.expr, self.type_scope(), self.funcs, {})
            _, v = self.expr(e.expr, None)
            if st == e.type:
                return e.type, v
            if st == "ptr" and e.type in ("u64", "i64"):  # M67: 指针转整数
                r = self.t()
                self.w(f"{r} = ptrtoint ptr {v} to {self.ll(e.type)}")
                return e.type, r
            # M83: 整数 -> 指针 (brk/mmap 取内存)。**整型字面量也算** —— 它的类型是 None,
            # 不特判就会落到下面那条通用分支发成 `zext i32 0 to ptr`, 而 zext 产不出指针,
            # 那是**非法 LLVM**。镜发的是 `inttoptr i32 0 to ptr` (字面量按默认宽度 u32),
            # 这条对齐它。2026-09-15 由 lompi 的 `0 as ptr` 实测抓到。
            if e.type == "ptr" and (st in INT_TYPES or (st is None and isinstance(e.expr, IntLit))):
                r = self.t()
                self.w(f"{r} = inttoptr {self.ll(st or 'u32')} {v} to ptr")
                return "ptr", r
            si, di = self.ll(st or "u32"), self.ll(e.type)
            if si == di:  # 同宽异名 (u64 <-> i64): LLVM 里是同一个类型, 再 cast 是非法 IR
                return e.type, v
            sw, dw = _bit_width(st or "u32"), _bit_width(e.type)
            op = "trunc" if dw < sw else ("sext" if (st in _SIGNED) else "zext")
            r = self.t()
            self.w(f"{r} = {op} {si} {v} to {di}")
            return e.type, r
        if isinstance(e, MethodCall):  # M8: 静态派发
            fn = self.funcs.get(e.mangled)
            if fn is None:
                raise LomError(e.line, 1, f"native: 未解析的方法 {e.name}")
            _, ov = self.expr(e.obj, fn.params[0].type)
            args = [f"{self.ll(fn.params[0].type)} {ov}"]
            for a, p in zip(e.args, fn.params[1:]):
                _, av = self.expr(a, p.type)
                args.append(f"{self.ll(p.type)} {av}")
            if fn.ret == "()":
                self.w(f"call void @{fn.name}({', '.join(args)})")
                return "()", ""
            r = self.t()
            self.w(f"{r} = call {self.ll(fn.ret)} @{fn.name}({', '.join(args)})")
            return fn.ret, r
        if isinstance(e, Call) and (e.name in BUILTINS or e.name == "slice_len"):
            return self.builtin(e)
        if isinstance(e, Call):
            fn = self.funcs.get(e.name)
            if fn is None:
                raise LomError(e.line, 1, f"未知函数 {e.name} —— 后端拿到的单元不完整")
            if len(e.args) != len(fn.params):
                raise LomError(e.line, 1, f"{e.name} 的实参个数不符")
            args = []
            for a, p in zip(e.args, fn.params):
                _, av = self.expr(a, p.type)
                args.append(f"{self.ll(p.type)} {av}")
            if fn.ret == "()":  # void 调用不能带名字
                self.w(f"call void @{fn.name}({', '.join(args)})")
                return "()", ""
            r = self.t()
            self.w(f"{r} = call {self.ll(fn.ret)} @{fn.name}({', '.join(args)})")
            return fn.ret, r
        if isinstance(e, FieldAccess):
            if not isinstance(e.obj, Ident):
                raise LomError(e.line, 1, "这个后端只支持从变量上取字段")
            ty, ptr = self.vars[e.obj.name]
            st = self.structs.get(ty)
            if st is None:
                raise LomError(e.line, 1, f"{_display_ty(ty)} 不是结构体")
            idx = next((i for i, (fn, _) in enumerate(st.fields) if fn == e.name), None)
            if idx is None:
                raise LomError(e.line, 1, f"结构体 {_display_ty(ty)} 没有字段 {e.name}")
            ft = st.fields[idx][1]
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 {idx}")
            r = self.t()
            self.w(f"{r} = load {self.ll(ft)}, ptr {gp}")
            return ft, r
        if isinstance(e, Index):
            if not isinstance(e.obj, Ident):
                raise LomError(e.line, 1, "native: 只支持变量取下标")
            ty, ptr = self.vars[e.obj.name]
            if _is_slice(ty):
                et = _slice_elem(ty)
                _, sv = self.expr(e.obj, ty)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
                it, iv = self.expr(e.idx)          # 下标**自己的类型**（见下面那处注释）
                g = self.t()
                self.w(f"{g} = getelementptr inbounds {self.ll(et)}, ptr {p}, {self.ll(it)} {iv}")
                r = self.t()
                self.w(f"{r} = load {self.ll(et)}, ptr {g}")
                return et, r
            if not _is_array(ty):
                raise LomError(e.line, 1, f"{_display_ty(ty)} 不是数组")
            et = _array_elem(ty)
            # **下标按它自己的类型发**，不写死 `i32`。`self.expr` 对 `Ident` 返回的是变量
            # **自己的**类型（`want` 只管字面量），所以 `i: u64` 的 `a[i]` 拿到的是一个
            # **i64 值** —— 塞进写死 `i32` 的 GEP 就是**不合法的 LLVM IR**。
            # `lomelf` 按 GEP 里写的类型去读下标（`tools/lomelf.py:799`），于是**悄悄截成
            # 32 位**照跑，所以这个 bug 一直没露；clang 不宽容，当场拒（见 `docs/212` §4）。
            it, iv = self.expr(e.idx)
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, {self.ll(it)} {iv}")
            r = self.t()
            self.w(f"{r} = load {self.ll(et)}, ptr {gp}")
            return et, r
        if isinstance(e, EnumPath):
            ed = self.enums.get(e.enum)
            if ed is None or e.variant not in ed.variants:
                raise LomError(e.line, 1, f"未知枚举变体 {e.enum}::{e.variant}")
            idx = ed.variants.index(e.variant)
            if not ed.payloads:
                return e.enum, str(idx)
            et = self.ll(e.enum)  # 带载荷枚举的无载荷变体: 只写 tag
            a = self.t()
            self.w(f"{a} = insertvalue {et} undef, i32 {idx}, 0")
            return e.enum, a
        if isinstance(e, EnumCtor):
            ed = self.enums.get(e.enum)
            if ed is None or e.variant not in ed.variants:
                raise LomError(e.line, 1, f"未知枚举变体 {e.enum}::{e.variant}")
            pt = ed.payloads.get(e.variant)
            if pt is None:
                raise LomError(e.line, 1, f"{e.enum}::{e.variant} 没有载荷")
            et = self.ll(e.enum)
            _, pv = self.expr(e.arg, pt)
            a = self.t()
            self.w(f"{a} = insertvalue {et} undef, i32 {ed.variants.index(e.variant)}, 0")
            if self.ll(pt) == "i64":
                b = self.t()
                self.w(f"{b} = insertvalue {et} {a}, i64 {pv}, 1")
            else:
                ext = "sext" if pt in _SIGNED else "zext"
                c = self.t()
                self.w(f"{c} = {ext} {self.ll(pt)} {pv} to i64")
                b = self.t()
                self.w(f"{b} = insertvalue {et} {a}, i64 {c}, 1")
            return e.enum, b
        raise LomError(getattr(e, "line", 1), 1, f"native 后端不支持该表达式: {type(e).__name__}")

    def upcast(self, v: str, frm: str | None, to: str) -> str:
        """把 `v`（类型 `frm`）抬到公共类型 `to`；已经同型就原样返回。

        **只会是 `zext`/`sext`，永远不会 `trunc`** —— 公共类型按定义装得下两个值
        （`_common_type`），收窄的那一侧轮不到：真没有公共类型时，checker 已经
        报 E001 了，发射器走不到这儿。

        与 `expr()` 里 `Cast` 那一支同一套算法（`si == di` 那条短路也在那儿）：
        `u64` 与 `i64` 在 LLVM 里是**同一个类型**，再 cast 一次是非法 IR。"""
        if frm not in INT_TYPES or to not in INT_TYPES or frm == to:
            return v
        si, di = self.ll(frm), self.ll(to)
        if si == di:
            return v
        r = self.t()
        self.w(f"{r} = {'sext' if frm in _SIGNED else 'zext'} {si} {v} to {di}")
        return r

    def binop(self, e: Bin, want: str | None) -> tuple[str, str]:
        scope = self.type_scope()
        lt = expr_type(e.left, scope, self.funcs, {})
        rt = expr_type(e.right, scope, self.funcs, {})
        if e.op in ("==", "!=") and (lt == "ptr" or rt == "ptr"):  # M73: 指针比较
            _, a = self.expr(e.left, "ptr")
            _, b = self.expr(e.right, "ptr")
            r = self.t()
            self.w(f"{r} = icmp {e.op} ptr {a}, {b}")
            return "bool", r
        if lt == "ptr" and e.op in ("+", "-") and rt != "ptr":  # M73: 指针算术 (字节)
            _, a = self.expr(e.left, "ptr")
            _, n = self.expr(e.right, "u64")
            if e.op == "-":
                nn = self.t()
                self.w(f"{nn} = sub i64 0, {n}")
                n = nn
            r = self.t()
            self.w(f"{r} = getelementptr inbounds i8, ptr {a}, i64 {n}")
            return "ptr", r
        # 结果的宽度 = 两侧的**公共类型**（`_common_type`），**不是左操作数** ——
        # `#133` 就是这一处：`x:i32 + y:i64` 原先按左边的 i32 定型，右边那个 i64 值
        # 没转就塞进 `add i32`，于是同一个子表达式一处等于 2³¹、一处为负。
        # 有一侧是字面量（类型 None）时维持原样：它按上下文定宽，公共类型取另一侧。
        # **移位除外**：`<<`/`>>` 的右操作数是移位量，任何整型都收，结果类型只看左边
        # （与 `expr_type` 那一支同一条）。
        ct = None
        if lt in INT_TYPES and rt in INT_TYPES and e.op not in ("<<", ">>"):
            ct = _common_type(lt, rt)
        # `want` 只在**是整数类型**时才算定宽期望：bool/str/ptr 上下文里的字面量算术
        # 落 1 位或 {ptr,i64} 运算是灾难（自举侧 `want_is_int` 的注释记着同一条）。
        w = want if want in INT_TYPES else None
        ty = (ct or lt or rt or w)
        if ty is None:
            # 两侧都没有类型：常量算术里出现**负值**时按有符号定宽。
            # `if -1 < 0 {…}` 以前落 `icmp ult i32 -1, 0`（假），而 C/Go/Python/Rust
            # 都说真 —— #237 的第二处根。字面量本身是负的就该按 i32 读。
            lv, rv = _const_int(e.left), _const_int(e.right)
            ty = "i32" if ((lv is not None and lv < 0) or (rv is not None and rv < 0)) else "u32"
        it = _ir_t(ty)
        signed = ty in _SIGNED
        op = e.op
        if op in ("&&", "||"):
            # M27: 短路 (phi) —— 右操作数仅在需要时求值
            _, a = self.expr(e.left, "bool")
            rhs_l, short_l, end_l = self.l("sc_rhs"), self.l("sc_short"), self.l("sc_end")
            if op == "&&":
                self.w(f"br i1 {a}, label %{rhs_l}, label %{short_l}")
            else:
                self.w(f"br i1 {a}, label %{short_l}, label %{rhs_l}")
            self.terminated = True
            self.label(rhs_l)
            _, b = self.expr(e.right, "bool")
            rhs_pred = self.cur_label or "entry"
            self.jump(end_l)
            self.label(short_l)
            self.jump(end_l)
            self.label(end_l)
            short_v = "false" if op == "&&" else "true"
            r = self.t()
            self.w(f"{r} = phi i1 [ {b}, %{rhs_pred} ], [ {short_v}, %{short_l} ]")
            return "bool", r
        la, a = self.expr(e.left, ty)
        a = self.upcast(a, la, ty)
        rb, b = self.expr(e.right, ty)
        b = self.upcast(b, rb, ty)
        r = self.t()
        if op in ("==", "!="):
            self.w(f"{r} = icmp {'eq' if op == '==' else 'ne'} {it} {a}, {b}")
            return "bool", r
        if op in ("<", "<=", ">", ">="):
            m = {("<", False): "ult", ("<=", False): "ule", (">", False): "ugt", (">=", False): "uge",
                 ("<", True): "slt", ("<=", True): "sle", (">", True): "sgt", (">=", True): "sge"}[op, signed]
            self.w(f"{r} = icmp {m} {it} {a}, {b}")
            return "bool", r
        ins = {"+": "add", "-": "sub", "*": "mul", "&": "and", "|": "or", "^": "xor"}.get(op)
        if ins:
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        if op in ("/", "%"):
            ins = ("sdiv" if signed else "udiv") if op == "/" else ("srem" if signed else "urem")
            # M18: 除零 = 运行时 trap (与 Rust 路径的 panic 对齐)
            z = self.t()
            self.w(f"{z} = icmp eq {it} {b}, 0")
            ok_l, trap_l, end_l = self.l("dok"), self.l("dtrap"), self.l("dend")
            self.w(f"br i1 {z}, label %{trap_l}, label %{ok_l}")
            self.terminated = True
            self.label(trap_l)
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(ok_l)
            self.w(f"{r} = {ins} {it} {a}, {b}")
            self.jump(end_l)
            self.label(end_l)
            return ty, r
        if op in ("<<", ">>"):
            ins = "shl" if op == "<<" else ("ashr" if signed else "lshr")
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        raise LomError(e.line, 1, f"这个后端不支持运算符 {op}")

    # -- 内建 (M1/M2)
    def alloc_ir(self, size_var: str) -> str:
        """分配 `size_var` 字节, 返回指针 (alloc 与 str_concat 共用, M15/M2)。

        走运行期的 `__loment_alloc`（bump + 地址序空闲链表 + 拆分 + 前沿回退）——
        **不再是内联的纯 bump**：`free` 兑现之后，分配与回收必须是同一份状态的读写者，
        而"只涨不落的前沿 + 什么都不做的 free"正是 `docs/175` §3.4 点名的那个形状
        （**允诺却不兑现**）。

        **触发也发在这里**（`#367`）：它原先在 `alloc` 的**调用点**上，而 `str_concat`
        走的是这个函数、不经过那一处 —— 于是拼接分配的字节**不计入**那个计数器，
        只拼字符串的程序**一次收集都不跑**（`gc_auto_alpha` 也救不了：它的自适应阈值与
        空间触发都住在 `__loment_maybe_collect` 里，而那个函数一次都不会被调用）。
        挪到这儿之后**每一次**分配都计数；发的是同一段文本、同一顺序、同一个实参，
        所以只含 `alloc` 的程序产物字节**完全不变**。
        """
        if self.gc_any:
            # 先问一句"该收了没有"—— 收在**分配之前**，所以不需要"分配失败再重试"那条路，
            # 也就不必动 `__loment_alloc` 一个字节（另外两档的产物因此逐字节不变）。
            self.w(f"call void @__loment_maybe_collect(i32 {size_var})")
        p = self.t()
        self.w(f"{p} = call ptr @__loment_alloc(i32 {size_var})")
        return p

    def builtin(self, e: Call) -> tuple[str, str]:
        """str_len / str_eq / str_concat / str_byte / slice_len 的 IR 降级。"""
        if e.name == "alloc":  # M15: bump 分配器
            _, sz = self.expr(e.args[0], "u32")
            return "ptr", self.alloc_ir(sz)
        if e.name == "free":
            _, p = self.expr(e.args[0], "ptr")
            self.w(f"call void @__loment_free(ptr {p})")
            return "u32", "0"
        if e.name == "load8":
            _, pv = self.expr(e.args[0], "ptr")
            _, ov = self.expr(e.args[1], "u32")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {pv}, i32 {ov}")
            b8 = self.t()
            self.w(f"{b8} = load i8, ptr {g}")
            r = self.t()
            self.w(f"{r} = zext i8 {b8} to i32")
            return "u32", r
        if e.name == "store8":
            _, pv = self.expr(e.args[0], "ptr")
            _, ov = self.expr(e.args[1], "u32")
            _, vv = self.expr(e.args[2], "u8")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {pv}, i32 {ov}")
            if self.gc_barrier:
                # **写屏障**（`docs/210` §2.7）：分代那一档的小回收不扫老年代，而老块里可能
                # 存着指向年轻代的指针 —— 写一次就记一次卡。**只在 alpha 档发**：`gc_auto`
                # 老实做全量，不带这条税（两档的差别因此仍然是一个可测的事实）。
                # 挂在这里而不是 `__loment_alloc` 上，是因为**指针是这么写进堆里的**：
                # 一个 8 字节值由 8 条 `store8` 拼出来（`store64` 在 `bytes.lomt` 里就是这么
                # 写的），所以"字节级"就是这道屏障唯一能看见的粒度 —— 好在**卡是 64 B**，
                # 8 条 `store8` 无论怎么跨都落在同一张或相邻两三张卡里，一张都不会漏。
                self.w(f"call void @__loment_wb(ptr {g})")
            self.w(f"store i8 {vv}, ptr {g}")
            return "u32", "0"
        if e.name == "atomic_add":  # M21
            _, pv = self.expr(e.args[0], "ptr")
            _, vv = self.expr(e.args[1], "u32")
            r = self.t()
            if self.gc_barrier:
                self.w(f"call void @__loment_wb(ptr {pv})")   # 同上：它也是一次写堆
            self.w(f"{r} = atomicrmw add ptr {pv}, i32 {vv} seq_cst")
            return "u32", r
        if e.name in ("get_bits", "set_bits"):  # M22: 位域
            _, bv = self.expr(e.args[0], "u8")
            _, sv = self.expr(e.args[1], "u32")
            _, wv = self.expr(e.args[2], "u32")
            one = self.t()
            self.w(f"{one} = zext i8 1 to i16")
            w16 = self.t()
            self.w(f"{w16} = trunc i32 {wv} to i16")
            shl = self.t()
            self.w(f"{shl} = shl i16 {one}, {w16}")
            sub = self.t()
            self.w(f"{sub} = sub i16 {shl}, 1")
            mask = self.t()
            self.w(f"{mask} = trunc i16 {sub} to i8")
            if e.name == "get_bits":
                s8 = self.t()
                self.w(f"{s8} = trunc i32 {sv} to i8")
                shr = self.t()
                self.w(f"{shr} = lshr i8 {bv}, {s8}")
                r = self.t()
                self.w(f"{r} = and i8 {shr}, {mask}")
                return "u8", r
            _, vv = self.expr(e.args[3], "u8")
            nsh = self.t()
            self.w(f"{nsh} = trunc i32 {sv} to i8")
            shm = self.t()
            self.w(f"{shm} = shl i8 {mask}, {nsh}")
            nm = self.t()
            self.w(f"{nm} = xor i8 {shm}, -1")
            cleared = self.t()
            self.w(f"{cleared} = and i8 {bv}, {nm}")
            masked = self.t()
            self.w(f"{masked} = and i8 {vv}, {mask}")
            shifted = self.t()
            self.w(f"{shifted} = shl i8 {masked}, {nsh}")
            r = self.t()
            self.w(f"{r} = or i8 {cleared}, {shifted}")
            return "u8", r
        if e.name in ("inb", "outb"):  # M20: 仅 Rust 路径
            raise LomError(e.line, 1, f"native: {e.name} 暂未在 IR 后端实现 (M20 仅 Rust 路径)")
        if e.name == "str_ptr":  # M67
            _, sv = self.expr(e.args[0], "str")
            p = self.t()
            self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
            return "ptr", p
        if e.name in ("ptr_add", "ptr_sub"):  # M73
            _, pv = self.expr(e.args[0], "ptr")
            _, nv = self.expr(e.args[1], "u32")
            n64 = self.t()
            self.w(f"{n64} = zext i32 {nv} to i64")
            if e.name == "ptr_sub":
                nn = self.t()
                self.w(f"{nn} = sub i64 0, {n64}")
                n64 = nn
            r = self.t()
            self.w(f"{r} = getelementptr inbounds i8, ptr {pv}, i64 {n64}")
            return "ptr", r
        if e.name == "syscall4":  # M67: Linux 系统调用
            vals = [self.expr(a, "u64")[1] for a in e.args]
            r = self.t()
            self.w(f'{r} = call i64 asm sideeffect "syscall", '
                   f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},~{{cx}},~{{r11}},~{{memory}}"'
                   f"(i64 {vals[0]}, i64 {vals[1]}, i64 {vals[2]}, i64 {vals[3]})")
            return "i64", r
        if e.name == "syscall6":  # M72: nr + a0..a4 (r10/r8)
            vals = [self.expr(a, "u64")[1] for a in e.args]
            r = self.t()
            self.w(f'{r} = call i64 asm sideeffect "syscall", '
                   f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},{{r10}},{{r8}},~{{cx}},~{{r11}},'
                   f'~{{memory}}"'
                   f"(i64 {vals[0]}, i64 {vals[1]}, i64 {vals[2]}, i64 {vals[3]}, "
                   f"i64 {vals[4]}, i64 {vals[5]})")
            return "i64", r
        if e.name == "syscall7":  # M72+: 六个实参寄存器用满（多一个 r9）
            vals = [self.expr(a, "u64")[1] for a in e.args]
            r = self.t()
            self.w(f'{r} = call i64 asm sideeffect "syscall", '
                   f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},{{r10}},{{r8}},{{r9}},~{{cx}},~{{r11}},'
                   f'~{{memory}}"'
                   f"(i64 {vals[0]}, i64 {vals[1]}, i64 {vals[2]}, i64 {vals[3]}, "
                   f"i64 {vals[4]}, i64 {vals[5]}, i64 {vals[6]})")
            return "i64", r
        if e.name == "thread_spawn":  # M84: 真线程 —— 入口是**编译期的名字**
            # **入口名不求值**（它不是表达式，是编译期的名字）：只走它后面那三个实参。
            # 顺序是**从左到右**（栈顶、实参、完成通知地址）—— 自举侧 `arg1` 只能按 token
            # 顺序求值，两边要产生同一套临时值编号就得同序。
            a_stk = self.expr(e.args[1], "u64")[1]
            a_arg = self.expr(e.args[2], "u64")[1]
            a_done = self.expr(e.args[3], "u64")[1]
            ent = e.args[0].value
            r = self.t()
            # 这条 asm 的名字是**两个词**（`thread_spawn @入口`）：链接器按第一个词分派、
            # 从第二个词里取符号。约束里的 `{r9}` / `{si}` / `{r12}` 不是 Linux 的系统调用
            # 约定 —— 它只是"哪个操作数先算进哪个寄存器"的声明，`lomelf` 照它取参、自己发
            # 机器码。`r12` 存完成通知地址：它是**唯一**能把一个值安全带过 `clone` 又不与
            # `clone` 自己那五个实参寄存器（rdi/rsi/rdx/r10/r8）冲突的位置。
            self.w(f'{r} = call i64 asm sideeffect "thread_spawn @{ent}", '
                   f'"={{ax}},{{si}},{{r9}},{{r12}},'
                   f'~{{di}},~{{dx}},~{{r10}},~{{r8}},~{{cx}},~{{r11}},~{{memory}}"'
                   f"(i64 {a_stk}, i64 {a_arg}, i64 {a_done})")
            return "i64", r
        if e.name == "panic":  # M19
            self.expr(e.args[0], "u32")
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(self.l("dead"))
            return "u32", "0"
        if e.name == "slice_len":
            at = expr_type(e.args[0], self.type_scope(), self.funcs, {})
            if at and _is_slice(at):
                _, sv = self.expr(e.args[0], at)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 1")
            else:
                p = str(_array_len(at)) if at and _is_array(at) else "0"
            r = self.t()
            self.w(f"{r} = trunc i64 {p} to i32")
            return "u32", r
        if e.name == "str_len":
            _, sv = self.expr(e.args[0], "str")
            p = self.t()
            self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 1")
            r = self.t()
            self.w(f"{r} = trunc i64 {p} to i32")
            return "u32", r
        if e.name == "str_eq":
            _, a = self.expr(e.args[0], "str")
            _, b = self.expr(e.args[1], "str")
            ap, al = self.t(), self.t()
            self.w(f"{ap} = extractvalue {{ ptr, i64 }} {a}, 0")
            self.w(f"{al} = extractvalue {{ ptr, i64 }} {a}, 1")
            bp, bl = self.t(), self.t()
            self.w(f"{bp} = extractvalue {{ ptr, i64 }} {b}, 0")
            self.w(f"{bl} = extractvalue {{ ptr, i64 }} {b}, 1")
            le = self.t()
            self.w(f"{le} = icmp eq i64 {al}, {bl}")
            chk, neq, end = self.l("seq"), self.l("sneq"), self.l("send")
            self.w(f"br i1 {le}, label %{chk}, label %{neq}")
            self.terminated = True
            self.label(chk)
            c = self.t()
            self.w(f"{c} = call i32 @__loment_memcmp(ptr {ap}, ptr {bp}, i64 {al})")
            z = self.t()
            self.w(f"{z} = icmp eq i32 {c}, 0")
            self.jump(end)
            self.label(neq)
            self.jump(end)
            self.label(end)
            r = self.t()
            self.w(f"{r} = phi i1 [ {z}, %{chk} ], [ false, %{neq} ]")
            return "bool", r
        # M2: str_concat —— 拼到 bump 堆上 (v0 不做回收, 与 Rust 路径的 leak 同语义)
        if e.name == "str_concat":
            _, a = self.expr(e.args[0], "str")
            _, b = self.expr(e.args[1], "str")
            ap, al = self.t(), self.t()
            self.w(f"{ap} = extractvalue {{ ptr, i64 }} {a}, 0")
            self.w(f"{al} = extractvalue {{ ptr, i64 }} {a}, 1")
            bp, bl = self.t(), self.t()
            self.w(f"{bp} = extractvalue {{ ptr, i64 }} {b}, 0")
            self.w(f"{bl} = extractvalue {{ ptr, i64 }} {b}, 1")
            n = self.t()
            self.w(f"{n} = add i64 {al}, {bl}")
            nu = self.t()
            self.w(f"{nu} = trunc i64 {n} to i32")   # 堆计数是 u32
            p = self.alloc_ir(nu)
            self.w(f"call void @__loment_memcpy(ptr {p}, ptr {ap}, i64 {al})")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {p}, i64 {al}")
            self.w(f"call void @__loment_memcpy(ptr {g}, ptr {bp}, i64 {bl})")
            v1 = self.t()
            self.w(f"{v1} = insertvalue {{ ptr, i64 }} undef, ptr {p}, 0")
            v2 = self.t()
            self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v1}, i64 {n}, 1")
            return "str", v2
        # str_byte
        _, sv = self.expr(e.args[0], "str")
        p = self.t()
        self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
        _, iv = self.expr(e.args[1], "u32")
        g = self.t()
        self.w(f"{g} = getelementptr inbounds i8, ptr {p}, i32 {iv}")
        b8 = self.t()
        self.w(f"{b8} = load i8, ptr {g}")
        r = self.t()
        self.w(f"{r} = zext i8 {b8} to i32")
        return "u32", r

    # -- 语句
    def block(self, stmts: list) -> None:
        for s in stmts:
            self.stmt(s)

    def stmt(self, s) -> None:
        line = getattr(s, "line", None)
        if line:
            loc = self.dbg_for(line)
            if loc is not None:
                self.dbg_loc = loc
        if isinstance(s, Let) and isinstance(s.expr, StructLit):
            st = self.structs.get(s.type)
            if st is None:
                raise LomError(s.line, 1, f"{_display_ty(s.type)} 不是结构体")
            ptr = self.vars[s.name][1]
            inits = dict(s.expr.inits)
            for i, (fn, ft) in enumerate(st.fields):
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(s.type)}, ptr {ptr}, i32 0, i32 {i}")
                _, v = self.expr(inits[fn], ft)
                self.w(f"store {self.ll(ft)} {v}, ptr {gp}")
            return
        if isinstance(s, Let) and isinstance(s.expr, ArrayLit):
            if not _is_array(s.type):
                raise LomError(s.line, 1, f"{_display_ty(s.type)} 不是数组")
            et = _array_elem(s.type)
            ptr = self.vars[s.name][1]
            for i, it in enumerate(s.expr.items):
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(s.type)}, ptr {ptr}, i32 0, i32 {i}")
                _, v = self.expr(it, et)
                self.w(f"store {self.ll(et)} {v}, ptr {gp}")
            return
        if isinstance(s, Assign) and isinstance(s.target, Index):
            base = s.target.obj
            if not isinstance(base, Ident):
                raise LomError(s.line, 1, "native: 只支持数组/切片变量取下标赋值")
            ty, ptr = self.vars[base.name]
            if _is_slice(ty):
                if not _is_mut_slice(ty):
                    raise LomError(s.line, 1, f"{_display_ty(ty)} 是只读切片，不能写")
                et = _slice_elem(ty)
                _, sv = self.expr(base, ty)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
                it, iv = self.expr(s.target.idx)   # 下标**自己的类型**（见 `Index` 读处那段注释）
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(et)}, ptr {p}, {self.ll(it)} {iv}")
                _, v = self.expr(s.expr, et)
                self.w(f"store {self.ll(et)} {v}, ptr {gp}")
                return
            if not _is_array(ty):
                raise LomError(s.line, 1, f"{_display_ty(ty)} 不是数组")
            et = _array_elem(ty)
            it, iv = self.expr(s.target.idx)       # 同上：下标按它自己的类型发
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, {self.ll(it)} {iv}")
            _, v = self.expr(s.expr, et)
            self.w(f"store {self.ll(et)} {v}, ptr {gp}")
            return
        if isinstance(s, Match):
            sty, sv = self.expr(s.subject, None)
            ed = self.enums.get(sty)
            if ed is None:
                raise LomError(s.line, 1, f"match 的主体 {_display_ty(sty)} 不是枚举")
            if ed.payloads:
                tag = self.t()
                self.w(f"{tag} = extractvalue {self.ll(sty)} {sv}, 0")
            else:
                tag = sv
            end_l = self.l("mend")
            wild_l = end_l
            cases = []
            for pat, _ in s.arms:
                if pat is None:
                    wild_l = self.l("mwild")
                else:
                    cases.append((ed.variants.index(pat.variant), self.l(f"m{pat.variant}")))
            self.w_raw(f"switch i32 {tag}, label %{wild_l} [")
            for idx, lbl in cases:
                self.w_raw(f"    i32 {idx}, label %{lbl}")
            self.w("  ]")   # !dbg 挂整条指令末尾 (多行 switch 的最后一行)
            self.terminated = True
            for (pat, body), (_idx, lbl) in zip([a for a in s.arms if a[0] is not None], cases):
                self.label(lbl)
                if pat.bind:
                    pt = ed.payloads[pat.variant]
                    raw = self.t()
                    self.w(f"{raw} = extractvalue {self.ll(sty)} {sv}, 1")
                    bptr = self.vars[pat.bind][1]
                    if self.ll(pt) == "i64":
                        self.w(f"store i64 {raw}, ptr {bptr}")
                    else:
                        c = self.t()
                        self.w(f"{c} = trunc i64 {raw} to {self.ll(pt)}")
                        self.w(f"store {self.ll(pt)} {c}, ptr {bptr}")
                self.block(body)
                self.jump(end_l)
            if wild_l != end_l:
                self.label(wild_l)
                self.block(next(b for p, b in s.arms if p is None))
                self.jump(end_l)
            self.label(end_l)
            return
        if isinstance(s, Let):
            if s.expr is None:  # M9: 未初始化 (alloca 已在入口块)
                return
            if s.name in self.l0:
                # **L0**（`docs/210` §2 / `gc_auto_alpha`）：这条 `alloc` 已经**提到栈上**了 ——
                # 不调分配器，只把那条 alloca 的地址存进局部槽。
                # 这里不必再看右式的形状：`_l0_promotable` 只把 `let NAME: ptr = alloc(<常量>)`
                # 收进表里，所以进到这里就一定是那个形状。
                self.w(f"store ptr %{s.name}.buf, ptr {self.vars[s.name][1]}")
                return
            _, v = self.expr(s.expr, s.type)
            self.w(f"store {self.ll(s.type)} {v}, ptr {self.vars[s.name][1]}")
            mask = self.slots.get(s.name)
            if mask is not None:
                # **块内精确**（`docs/210` §2.9）：把块头的借出标记从 `-1` 改成
                # `-(mask + 2)`，收集器于是只扫掩码里置了位的那些字（`-1` = 保守）。
                # 进得了这张表的，右式一定是那条 `let NAME: ptr = alloc(...)`
                # （`_slot_plan` 只收这个形状），所以 `%v` 就是块载荷首地址。
                #
                # **头在 `%v - 4`，但这里不走 `getelementptr ... i32 -4`** —— 包内镜像
                # 链接器把 `i32` 立即数**零扩展**进 64 位再相加（`lomelf` 的 `Emitter.get`
                # 发的是 32 位 `mov`），于是 `-4` 变成 **+4294967292**，产物直接段错误
                # （实测：不带 `--link` 的 `clang` 路没事，正是这条差别暴露了它）。
                # 那一道"指令子集"是**硬约束**（`docs/210` §8 —— L2 那条"体内不许 `free`"
                # 就是被它逼出来的），所以这里**绕开负立即数**：指针减 4 用 `ptrtoint` /
                # `sub i64` / `inttoptr` 三步走。链接器那一侧的缺陷另开 issue，不在这里顺手修。
                h32 = self.t()
                self.w(f"{h32} = ptrtoint ptr {v} to i64")
                hs = self.t()
                self.w(f"{hs} = sub i64 {h32}, 4")
                h = self.t()
                self.w(f"{h} = inttoptr i64 {hs} to ptr")
                self.w(f"store i32 {-(mask + 2)}, ptr {h}")
            return
        if isinstance(s, Assign):
            if not isinstance(s.target, Ident):
                raise LomError(s.line, 1, "这个后端只支持给变量赋值（数组元素暂不支持）")
            ty, ptr = self.vars[s.target.name]
            _, v = self.expr(s.expr, ty)
            self.w(f"store {self.ll(ty)} {v}, ptr {ptr}")
            return
        if isinstance(s, If):
            _, c = self.expr(s.cond, "bool")
            then_l, else_l, end_l = self.l("then"), self.l("else"), self.l("end")
            self.w(f"br i1 {c}, label %{then_l}, label %{else_l}")
            self.terminated = True
            self.label(then_l)
            # **L2 也认 `if` 的两支**（`docs/210` §7 点名的"只认 while/for 的体"）：
            # 两支各是一个块，规则与循环体同一条 —— 入口记前沿、末尾退回。
            tv = self.epoch_open_flag(s.then_l2)
            self.block(s.then)
            self.epoch_close(s, tv)
            self.jump(end_l)
            self.label(else_l)
            ev = self.epoch_open_flag(s.else_l2)
            self.block(s.otherwise)
            self.epoch_close(s, ev)
            self.jump(end_l)
            self.label(end_l)
            return
        if isinstance(s, While):
            cond_l, body_l, end_l = self.l("wcond"), self.l("wbody"), self.l("wend")
            self.jump(cond_l)
            self.label(cond_l)
            _, c = self.expr(s.cond, "bool")
            self.w(f"br i1 {c}, label %{body_l}, label %{end_l}")
            self.terminated = True
            self.label(body_l)
            sv = self.epoch_open(s)
            self.block(s.body)
            self.epoch_close(s, sv)
            self.jump(cond_l)
            self.label(end_l)
            self.l1_free(s.l1)          # L1：落点就在**循环之后**（体内那次是最后一次用）
            return
        if isinstance(s, For):
            ty, ptr = self.vars[s.var]
            it = _ir_t(ty)
            _, lo = self.expr(s.lo, ty)
            self.w(f"store {it} {lo}, ptr {ptr}")
            cond_l, body_l, end_l = self.l("fcond"), self.l("fbody"), self.l("fend")
            self.jump(cond_l)
            self.label(cond_l)
            _, cv = self.expr(Ident(s.var, s.line), ty)
            _, hv = self.expr(s.hi, ty)
            r = self.t()
            self.w(f"{r} = icmp {'slt' if ty in _SIGNED else 'ult'} {it} {cv}, {hv}")
            self.w(f"br i1 {r}, label %{body_l}, label %{end_l}")
            self.terminated = True
            self.label(body_l)
            sv = self.epoch_open(s)
            self.block(s.body)
            self.epoch_close(s, sv)
            _, cur = self.expr(Ident(s.var, s.line), ty)
            nxt = self.t()
            self.w(f"{nxt} = add {it} {cur}, 1")
            self.w(f"store {it} {nxt}, ptr {ptr}")
            self.jump(cond_l)
            self.label(end_l)
            self.l1_free(s.l1)          # L1：落点就在**循环之后**（体内那次是最后一次用）
            return
        if isinstance(s, Guard):  # P4/M36+M38: 域检查 + 审计
            _, iv = self.expr(s.expr, "u32")
            v64 = self.t()
            self.w(f"{v64} = zext i32 {iv} to i64")
            ge = self.t()
            self.w(f"{ge} = icmp uge i64 {v64}, {s.lo}")
            le = self.t()
            self.w(f"{le} = icmp ule i64 {v64}, {s.hi}")
            ok = self.t()
            self.w(f"{ok} = and i1 {ge}, {le}")
            gok, gtrap = self.l("gok"), self.l("gtrap")
            self.w(f"br i1 {ok}, label %{gok}, label %{gtrap}")
            self.terminated = True
            self.label(gtrap)
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(gok)
            p = self.t()
            self.w(f"{p} = getelementptr [16 x i64], ptr @__loment_audit, i32 0, i32 {s.cap_id}")
            old = self.t()
            self.w(f"{old} = load i64, ptr {p}")
            new = self.t()
            self.w(f"{new} = add i64 {old}, 1")
            self.w(f"store i64 {new}, ptr {p}")
            return
        if isinstance(s, Return):
            _, v = self.expr(s.expr, self.f.ret)
            self.l1_free(s.l1)   # L1：返回值**已经读出来**了，现在可以还那块
            self.roots_pop()   # gc_auto：离开本帧前把根表游标退回去
            self.w(f"ret {self.ll(self.f.ret)} {v}")
            self.terminated = True
            return
        if isinstance(s, ExprStmt):
            self.expr(s.expr, None)
            return
        raise LomError(getattr(s, "line", 1), 1, f"这个后端不支持这条语句: {type(s).__name__}")


#: 程序入口（`docs/189` §3：入口是 `fn _start()`，不是 `main`）。ELF 那条路靠
#: `-Wl,-e,_start` 把它点成**进程入口**（`loment/bootstrap.sh`、`tools/loment_genesis.py`、
#: `tools/loment_elf_test.py::_link_clang`、`tools/loment_dist.py::_link_opt` 都是这么做的）。
ENTRY_FN = "_start"

#: 进程入口**不是一次 `call`**：内核进来时 `rsp` 是 16 对齐的，比 System V 的函数入口
#: 约定（`rsp % 16 == 8`，`call` 压了返回地址）**少 8**。不声明这一条，`_start` 与它下面
#: 的每一帧就整体偏 8 —— `lomelf` 是一台不碰对齐的栈机，所以一直看不出来；clang `-O1/-O2`
#: 会把局部数组的清零改写成 `movaps`（16 字节对齐的写），一跑就 `#GP`。实测：自举驱动
#: 3.2 MB 的 IR 在 `-O0` 编出来一切正常，`-O1/-O2` 编出来一跑就 SIGSEGV（`docs/212` §5.2）。
#: `stackrealign` 让后端在序言里补一句 `and rsp, -16`，那 8 个字节就回来了。
ENTRY_ATTR = ' "stackrealign"'


def _emit_ir_func(f: Func, funcs: dict, consts: dict,
                  structs: dict | None = None, enums: dict | None = None,
                  coverage: bool = False, cov_counter: list | None = None,
                  dbg_scope: int | None = None, dbg_lines: dict | None = None,
                  dbg_meta: list | None = None, dbg_types: dict | None = None,
                  gc_alpha: bool = False, gc_auto: bool = False,
                  gc_barrier: bool = True
                  ) -> tuple[list[str], str]:
    ir = _Ir(funcs, consts, f, structs, enums, coverage, cov_counter,
             dbg_scope, dbg_lines, dbg_meta, dbg_types, gc_alpha, gc_auto, gc_barrier)
    if f.interrupt:  # M33: x86_intrcc 需要中断帧指针
        ir.out.append(f"; {f.name} -> interrupt (x86_intrcc)")
        ir.out.append(f"define x86_intrcc void @{f.name}(ptr byval([8 x i8]) %__frame)"
                      f"{f' !dbg !{dbg_scope}' if dbg_scope is not None else ''} {{")
        ir.out.append("entry:")
        ir.cur_label = "entry"
        ir.cov_hit()
        ir.locals_prologue(f, enums, funcs, structs)
        ir.roots_push(f)
        ir.block(f.body)
        if not ir.terminated:
            ir.l1_free(f.l1)            # L1：函数末尾（落点选这儿的理由见 `_l1_place`）
            ir.roots_pop()
            ir.w("ret void")
        ir.out.append("}")
        return ir.globals, "\n".join(ir.out)
    args = ", ".join(f"{ir.ll(p.type)} %{p.name}" for p in f.params)
    ir.out.append(f"; {f.name} -> {f.ret}")
    ir.out.append(f"define {ir.ll(f.ret)} @{f.name}({args})"
                  f"{ENTRY_ATTR if f.name == ENTRY_FN else ''}"
                  f"{f' !dbg !{dbg_scope}' if dbg_scope is not None else ''} {{")
    ir.out.append("entry:")
    ir.cur_label = "entry"
    ir.cov_hit()
    for p in f.params:  # 参数与局部统一提升到入口块, 避免循环内反复分配
        ir.w(f"%{p.name}.addr = alloca {ir.ll(p.type)}")
        ir.vars[p.name] = (p.type, f"%{p.name}.addr")
    for j, p in enumerate(f.params):  # M59: 形参也声明, arg 从 1 起 (DWARF 约定)
        ir.dbg_declare(p.name, p.type, f"%{p.name}.addr", f.line, j + 1)
    ir.locals_prologue(f, enums, funcs, structs)
    for p in f.params:
        ir.w(f"store {ir.ll(p.type)} %{p.name}, ptr %{p.name}.addr")
    ir.roots_push(f)
    ir.block(f.body)
    if not ir.terminated:
        ir.l1_free(f.l1)            # L1：函数末尾（落点选这儿的理由见 `_l1_place`）
        if f.ret == "()":
            ir.roots_pop()
            ir.w("ret void")
        else:
            ir.w("unreachable")  # ponytail: 语言不强制全路径 return (docs/144 §3)
    ir.out.append("}")
    return ir.globals, "\n".join(ir.out)


#: **命令入口**（`docs/223` §3.2）：单元声明了命令、又没写 `_start` 时，工具链替它生成进程
#: 入口 —— 读 `/proc/self/cmdline`、数出字段数、调 `command_main(argv, argc)`、把返回值交给
#: `exit`。**生成而不是让每个命令自己抄**：一整段 `_start` + cmdline 解析 + `exit` 会在每一条
#: 命令里各写一遍（`loment/tools/lompicheck.lomt` 的引擎就是这么写的，四十行）。
#:
#: 局部名一律 `%lc.*` 前缀，与单元自己生成的 `%tN` 不会撞。辅助函数叫 `@.lc.argc`
#: **故意不带 `__loment_` 前缀** —— 那串名字是**运行期前奏的发不发的开关**
#: （`emit_llvm` 里 `"@__loment_" in text_all` 那一句），带了它就会给一条只调用
#: 这个桩的命令白发出整段 freestanding 运行时，而自举那边按需扫描不会发 —— 实测
#: 两边就这么差出 75 行。桩自带 argc 的计数，不欠运行期任何东西。**自举镜**（`loment/selfhost/codegen.lomt` 的 `emit_cmd_entry`）是同一段
#: 文本，两处的产物必须逐字节相同（`loment_p8_test` 那道闸）。
_IR_CMD_ENTRY = '''; ---- 命令入口 (docs/223: 工具链生成, 不是用户写的) ----
define void @_start() {
entry:
  %lc.o = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 12, i64 0, i64 0, i64 0)
  %lc.w = add i64 %lc.o, 65536
  %lc.g = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 12, i64 %lc.w, i64 0, i64 0)
  %lc.b = inttoptr i64 %lc.o to ptr
  %lc.p = getelementptr inbounds [19 x i8], ptr @.lc.cmdline, i64 0, i64 0
  %lc.pi = ptrtoint ptr %lc.p to i64
  %lc.fd = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 257, i64 18446744073709551516, i64 %lc.pi, i64 0)
  %lc.bi = ptrtoint ptr %lc.b to i64
  %lc.n = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 0, i64 %lc.fd, i64 %lc.bi, i64 65536)
  %lc.c = call i32 @.lc.argc(ptr %lc.b, i64 %lc.n)
  %lc.r = call i32 @command_main(ptr %lc.b, i32 %lc.c)
  %lc.re = zext i32 %lc.r to i64
  %lc.x = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %lc.re, i64 0, i64 0)
  ret void
}

define internal i32 @.lc.argc(ptr %b, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %cont ]
  %f = phi i64 [ 0, %entry ], [ %f1, %cont ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %out, label %body
body:
  %bp = getelementptr i8, ptr %b, i64 %i
  %bc = load i8, ptr %bp
  %bz = icmp eq i8 %bc, 0
  br i1 %bz, label %zero, label %cont
zero:
  %f0 = add i64 %f, 1
  br label %cont
cont:
  %f1 = phi i64 [ %f, %body ], [ %f0, %zero ]
  %i1 = add i64 %i, 1
  br label %loop
out:
  %r = trunc i64 %f to i32
  ret i32 %r
}
'''


def _cmd_entry_ir() -> tuple[list[str], list[str]]:
    """(全局, 函数体) —— 路径那个字符串常量按本文件里 `StrLit` 的老写法转义。

    **这个常量带 NUL**（19 字节），而别处的字符串字面量不带（`StrLit` 只按内容长度发）。
    `openat` 要的是 NUL 结尾的路径 —— 别处能跑是因为 `.rodata` 里后面恰好还有零字节，
    那不是一条能靠的规矩，所以这里自己写全。
    """
    b = b"/proc/self/cmdline\x00"
    esc = "".join(f"\\{x:02X}" for x in b)
    return ([f'@.lc.cmdline = private unnamed_addr constant [{len(b)} x i8] c"{esc}"'],
            [_IR_CMD_ENTRY])


def _alloc_promoted(toks: list, a: int, funcs: list) -> bool:
    """`alloc` 那一格（token `a`）是不是**已经落到 L0**（提到栈上了）—— 与自举侧
    `codegen.lomt` 的 `alloc_is_promoted` 同一条规则（同一份源 ⇒ 两边同一个答案）。

    形状就是 L0 认的那一个：`let NAME : ptr = alloc` —— `alloc` 往前四格是那个名字。
    """
    if a < 5:
        return False
    if not (toks[a - 1].val == "=" and toks[a - 2].val == "ptr"
            and toks[a - 3].val == ":" and toks[a - 5].val == "let"):
        return False
    name = toks[a - 4]
    if name.kind != "ident":
        return False
    for f in funcs:
        if f.tok_at <= a < f.tok_end:
            return name.val in f.l0
    return False


def _declares_extern(mods: list) -> bool:
    """这份单元（**连同依赖**）声明了 `extern fn` 吗 —— 也就是"它要链一份外面的东西"。

    **为什么要问这个**（`docs/219` S2 / `FujoJTOP/loment#359`）：真 C 库总是要 `malloc`
    （zlib 的 `zutil.o` 就要），而收编面那两条薄壳（`malloc`/`free`）**只在堆在时才发**
    —— 堆不在，链接期就是"引用了未定义的符号 malloc"。所以"要调外部库"这件事本身
    就该把堆带上。判据放宽到"有 `extern fn` 就发堆"，是 `docs/219` §8b 里写下的修法。
    """
    for m in mods:
        toks = getattr(m, "toks", None) or []
        for i, tk in enumerate(toks):
            if tk.kind == "ident" and tk.val == "extern":
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is not None and nxt.kind == "ident" and nxt.val == "fn":
                    return True
    return False


def _heap_needed(mods: list, rt_on: bool = True) -> bool:
    """这份单元（**连同依赖**）要不要那块 arena —— 与自举侧 `heap_needed` 同一条规则：

    * 三个**运行期构造**之一出现即算：`alloc`（**没被 L0 提到栈上的那些**）/ `str_concat`
      / `free`；
    * **多一条（2026-10-15）**：根开了运行期、而这份单元**声明了 `extern fn`** ——
      见 `_declares_extern` 的说明（真 C 库要 `malloc`，堆不在就发不出那两条薄壳）。
      `no_runtime` 那一档**不跟**：那一维是**保证**（`docs/217`）——产物里不带运行期，
      于是那样一个单元链一个要 `malloc` 的库时，报的是**未定义的符号**（响亮，且是它选的）。

    **必须在发射函数体之前算出来**：`gc_auto_alpha` 的写屏障挂在每个 `store8` 站点上，
    而屏障**引用堆** —— 一份把 `alloc` 全 L0 提走的单元没有堆，那儿的屏障就成了未定义符号
    （实测：`FujoJTOP/loment#239` 一修好，这条立刻露出来）。

    它与 `emit_llvm` 里那条**文本判据**（`"@__loment_alloc" in text_all`）说的必须是同一件事
    —— 自举侧两侧同源（`heap_needed` 预扫描 + 同一份发堆门），逐字节判据一直钉着这一点。
    """
    if rt_on and _declares_extern(mods):
        return True
    for m in mods:
        toks = getattr(m, "toks", None) or []
        funcs = getattr(m, "funcs", None) or []
        for i, tk in enumerate(toks):
            if tk.kind != "ident" or tk.val not in ("alloc", "str_concat", "free"):
                continue
            if tk.val == "alloc" and _alloc_promoted(toks, i, funcs):
                continue
            return True
    return False


def emit_llvm(mod: Module, lom_root: Path, deps: list[Module] | None = None,
              coverage: bool = False, debug: bool = False) -> str:
    """原生后端: LLVM IR。M0 标量 + M23–M26 聚合 + M1/M2 str + M3/M4 切片。"""
    _gc_alpha_pass(mod, deps)       # L0/L1/L2（`docs/210`）—— **在 prepare 之前**，要跨模块
    mod, deps = prepare(mod, deps)  # M6
    mods = list(deps or []) + [mod]
    funcs: dict[str, Func] = {}
    consts: dict[str, tuple[str, int]] = {}
    structs: dict[str, Struct] = {}
    enums: dict[str, EnumDecl] = {}
    for m in mods:
        for s in m.structs:
            structs[s.name] = s
        for e in m.enums:
            enums[e.name] = e
        for c in m.consts:
            consts[c.name] = (c.type, c.value)
        for f in m.funcs:
            funcs[f.name] = f
        # 外部函数进**解析表**(调用点要能找到它), 但不进"发 define"那条路 —— 它们发 declare。
        # setdefault: 两个模块声明同一个外部符号是同一件事, 先来的那份留着。
        for x in m.externs:
            funcs.setdefault(x.name, x)
    for m in mods:
        for f in m.funcs + m.externs:
            # 聚合参数/返回值在原生路径可用 (LLVM 结构体按值), 但不保证 C ABI — 勿从 C 直接调用
            _ll_type(f.ret, structs, enums)
            for p in f.params:
                _ll_type(p.type, structs, enums)
            for _, ty, _ln in _collect_locals(f, enums, funcs, structs):
                _ll_type(ty, structs, enums)
    out = [
        "; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)",
        "; clang -O1 driver.c this.ll -o exe",
    ]
    # `choose hosted`（`docs/222` §4.4）：构建那一侧要知道这份产物是**对外**的 ——
    # 它决定链接交给谁（C 工具链还是自举 lomelf）。**写进 IR 而不是让启动器去读源码**：
    # 读源码要启动器懂 Loment 的词法，而这份 IR 本来就是构建的输入。
    # **只有 hosted 才多发这一行** —— sealed 是默认档，于是现存每一份单元的 IR
    # 一个字节都不变（孪生判据逐字节比的那 46 份）。
    _hosted_port = mod.chooses.get("port", CORE_DEFAULTS["port"]) == "hosted"
    if _hosted_port:
        out.append("; loment-port: hosted")
    out.append("")
    globals_: list[str] = []
    body: list[str] = []
    # 外部函数声明: 每个外部符号**一条 `declare`**, 按名字去重 (两个模块声明同一个外部
    # 符号是同一件事)。LLVM 自己会按平台 C ABI 给 `declare` 的函数传参 —— 所以调用点
    # **一个字符都不用改**, C ABI 这件事在原生 LLVM 路径上是白拿的 (docs/173 §2)。
    # 真正要自己实现 C ABI 的是**自举那条路**(codegen.lomt / lomelf), 见 docs/173 §4。
    seen_ext: set[str] = set()
    for m in mods:
        for x in m.externs:
            if x.name in seen_ext:
                continue
            seen_ext.add(x.name)
            ps = ", ".join(_ll_type(p.type, structs, enums) for p in x.params)
            globals_.append(f"declare {_ll_type(x.ret, structs, enums)} @{x.name}({ps})")
    cov_counter = [0] if coverage else None
    # **L0 只在这一档下开**（`docs/210` §2）：`gc_manual`（默认）与 `gc_auto` 都**一行不动**。
    # 取的是**根单元**的取值 —— 与 Potato 里那个 `gc` 字段同一个来源。
    gc_alpha = mod.chooses.get("gc", CORE_DEFAULTS["gc"]) == "gc_auto_alpha"
    # **`gc_auto`**：自动那一档（`docs/210` §2.1 / §2.4）—— 编译器发精确根表 + 在每个分配点
    # 问一句"该收了没有"，收集器在运行期里。与 `gc_alpha` **互斥**（两档不同时成立）。
    gc_auto = mod.chooses.get("gc", CORE_DEFAULTS["gc"]) == "gc_auto"
    # **LMS 第 3 级入口 A**（`docs/253` §5.3）：`alloc` 分配不出时**先收一次再试**，
    # 而不是直接 abort。⚠ **两个条件缺一不可**：
    #   * `lms` 开着 —— 它属于 LMS 第 3 级，不是回收档的事；
    #   * **这份产物里真的有收集器**（`gc_auto` / `gc_auto_alpha`）—— `gc_manual` 下
    #     没有东西可收，"拉回"在那里是一句空话。这是这一级**明写的边界**。
    _lms_rescue = (mod.chooses.get("lms", CORE_DEFAULTS["lms"]) == "lms_open"
                   and (gc_auto or gc_alpha))
    # **运行期维**（`docs/217` / `docs/175` §3.6）。两个方向各一句话：
    #   * `runtime`    ⇒ 产物里**一定**有那段运行期 —— 这里的 `rt_on` 无条件发 `_IR_RUNTIME`；
    #   * `no_runtime` ⇒ 产物里**一定没有** —— 那条**不在这里**：它在 `check()` 里拒
    #     （编译期点名，而不是发射到一半才发现）。两半合起来才是这一维。
    #
    # **`_IR_HEAP` 不跟着 `rt_on` 强发**：那是 64 KiB 的零初全局 + 分配器，程序不 `alloc`
    # 就不该装它（一个只会除法的裸机引导程序不该凭空多 64 KiB 的 .bss）。它仍旧按需 ——
    # 而"用得到 `alloc`"这一半由 `check()` 管着：`no_runtime` 下写 `alloc` 是拒的，
    # 所以"产物里有分配器"必然意味着"根写过 `choose runtime`"。
    # **多一条按需**（2026-10-15）：根开了运行期、而这份单元声明了 `extern fn` ——
    # 真要链外部库了，堆就带上（`_declares_extern` 的说明）。这个判断要用到 `rt_on`，
    # 所以 `rt_on` 在这里先算（它只是一次字典读取，没有副作用）。
    rt_on = mod.chooses.get("runtime", CORE_DEFAULTS["runtime"]) == "runtime"
    # **写屏障要不要挂**（`docs/210` §2.7 / `FujoJTOP/loment#239`）：alpha 档**且**这份
    # 单元真的要那块 arena。判据得在**发射函数体之前**算好 —— 屏障是在 `store8` 站点上
    # 发的，而它引用堆。与自举侧同一条规则（那边预扫描成 `s+516`）。
    _has_heap_pre = _heap_needed([mod, *deps], rt_on)
    meta: list[str] = []
    dbg_types: dict = {}  # M59: 局部变量类型 -> DIBasicType (全模块共享一份)

    def _difile(m) -> str:
        """这个模块的源文件**真实路径** -> DWARF 的 `(filename, directory)`。

        **不能拿 `m.name` 当文件名**（那是模块名）：`loment/examples/user_hello.lomt`
        的模块名是 `user_hello`，拼出来的路径指到一个不存在的文件 —— 断点/单步就全错。
        `m.src` 没有（手写的 Module、或从字符串 parse 出来的）时退回旧行为。
        """
        p = getattr(m, "src", None)
        if p is None:
            return f'"{m.name}.lomt"', f'"{lom_root.as_posix()}"'
        # **绝对路径**：相对路径要靠消费方猜基准（编译时的 cwd），而读行表的是**调试器**，
        # 它那边的基准是编辑器的文档 URI —— 猜错就是"断点落不上、栈帧指不到源"。
        ap = Path(p).resolve()
        return f'"{ap.name}"', f'"{ap.parent.as_posix()}"'

    if debug:  # M59: DWARF 最小元数据 (编译单元 + 文件 + 签名类型)
        fn0, dir0 = _difile(mod)            # 编译单元挂**根模块**的文件
        meta += [
            '!0 = distinct !DICompileUnit(language: DW_LANG_C99, file: !1, '
            'producer: "lomentc", isOptimized: false, runtimeVersion: 0, '
            'emissionKind: FullDebug)',
            f'!1 = !DIFile(filename: {fn0}, directory: {dir0})',
            "!2 = !DISubroutineType(types: !3)",
            "!3 = !{}",
        ]
    #: 模块名 -> 它的 `!DIFile` 编号。**每模块一个** —— 一份程序可以 `use` 好几个
    #: `.lomt`，共用一个文件的话，跳进库里的函数会显示根文件，断点也就落错了地方。
    #: 根模块先占上 `!1`（编译单元那句已经引用它了），免得给它发第二份。
    dbg_file: dict = {mod.name: 1} if debug else {}
    for m in mods:
        for f in m.funcs:
            scope, lines = None, {}
            if debug:
                fid = dbg_file.get(m.name)
                if fid is None:
                    fn_m, dir_m = _difile(m)
                    fid = dbg_file[m.name] = len(meta)
                    meta.append(f'!{fid} = !DIFile(filename: {fn_m}, directory: {dir_m})')
                scope = len(meta)
                meta.append(
                    f'!{scope} = distinct !DISubprogram(name: "{f.name}", scope: !1, '
                    f'file: !{fid}, line: {f.line}, type: !2, unit: !0, '
                    f'spFlags: DISPFlagDefinition, retainedNodes: !3)')
            g, text = _emit_ir_func(f, funcs, consts, structs, enums, coverage,
                                    cov_counter, scope, lines, meta if debug else None,
                                    dbg_types if debug else None, gc_alpha, gc_auto,
                                    gc_alpha and _has_heap_pre)
            globals_ += g
            body.append(text)
    # **命令入口**（`docs/223`）：声明了命令、又没写 `_start` -> 工具链替它发一个。
    # 条件是三条一起看：有声明、名字取出来了、**没有** `_start`。三种"半截"状态
    # （形状不对 / 双入口 / 没声明却写了 `command_main`）在 `check()` 里就报掉了，
    # 走到这里必然干净 —— 这里再判一次只是不让 `emit_*` 单独被调用时发出畸形 IR。
    if mod.command_line and mod.command and not mod.command_bad \
            and not any(f.name == "_start" for f in mod.funcs):
        _g, _b = _cmd_entry_ir()
        globals_ += _g
        body += _b
    text_all = "\n".join(body)
    if debug:  # M59: 具名元数据 + 编号元数据
        out.append("!llvm.dbg.cu = !{!0}")
        base = len(meta)
        meta.append(f'!{base} = !{{i32 2, !"Dwarf Version", i32 4}}')
        meta.append(f'!{base + 1} = !{{i32 2, !"Debug Info Version", i32 3}}')
        out.append(f"!llvm.module.flags = !{{!{base}, !{base + 1}}}")
        out.append("")
    if coverage:  # M63: 块覆盖计数数组 + 块总数
        out.append("@__loment_cov = global [256 x i64] zeroinitializer")
        out.append(f"@__loment_cov_n = constant i64 {cov_counter[0]}")
        out.append("")
    if rt_on or "@__loment_" in text_all:  # M31: 自带运行时 (无 libc)
        out.append(_IR_RUNTIME)
    if "@__loment_audit" in text_all:  # P4/M38
        out.append("@__loment_audit = internal global [16 x i64] zeroinitializer")
        out.append("")
    if mod.caps:  # P4/M35: 域描述表 (space 名取 FNV-1a 32 位哈希)
        rows = ", ".join(
            "{ i64, i64, i64, i64 } { i64 %d, i64 %d, i64 %d, i64 %d }"
            % (_fnv1a32(c.space), c.lo, c.hi, 1 if c.revocable else 0)
            for c in mod.caps
        )
        out.append(
            f"@__loment_caps = internal constant [{len(mod.caps)} x {{ i64, i64, i64, i64 }}] [{rows}]"
        )
        out.append("")
    # **分配器与它的全局同进同出**（`docs/175` §3.4）：`alloc`/`free` 现在是对运行期的
    # **调用**，堆全局不再出现在**单元体**里 —— 判据必须落在"分配器被引用了没有"上，
    # 而不是"堆那个符号出现过没有"。两者分开判会漏发（实测：`free` 一用就报
    # "未定义的标签"；而只用整数除法、不用 alloc 的单元会反过来——发出分配器却没有全局）。
    _needs_heap = ("@__loment_alloc" in text_all or "@__loment_free" in text_all
                   or _has_heap_pre)
    # **`hosted` 的堆从宿主来**（`docs/228` 的 A = `docs/219` §8b 的 **S1e**）。
    # 闸门是 `choose port hosted`，因为"堆封在这份产物里"是 `sealed` 的一半含义 ——
    # 一次 `malloc` 是**运行期**对外面的依赖，给 `sealed` 加上它就等于把那条保证改掉
    # （`docs/228` §2）。`sealed` 因此走原来那段文本，**一个字节没动**。
    # **收集器那一档也在这支里**（`docs/230`，= `docs/228` §4 的 B1）：标记表的**行数
    # 就是 arena/8**，所以换 arena 必须**同时**换表 —— 那就是 `_IR_GC_HOSTED`。此前
    # 这一档留在静态 64 KiB 并靠 `; loment-heap: static 65536` 说出来；现在它也吃平台
    # 那一块，那条"不静默"的记号因此**没有再出现的场合**（判据从"它说了"改成"它涨了"）。
    # `gc_auto_alpha` 不用管 —— `hosted` 与它**定义上冲突**（`CORE_CONFLICTS`），走不到这儿。
    _hosted_grown = _hosted_port
    # **`extern fn` 那一档的 arena**（`docs/219` §8b 的 **S2**，2026-10-15）：根开了运行期、
    # 这份单元声明了 `extern fn`（于是堆按需带上，见 `_heap_needed`）、而且**不是** `hosted`
    # （那一档本来就吃平台那一块，比这块还大）—— 这一档的 arena 换成**封在产物里、但给够**
    # 的那一种（`_IR_HEAP_FFI`）。真 C 库要 256–512 KiB（zlib 的 `deflateInit`），
    # `_IR_HEAP` 那 64 KiB 装不下 —— 这是 V1（`docs/183` §4）实测剩下的最后一件。
    #
    # **`gc_auto_alpha` 除外，而且是明写的边界**：那一档的收集器
    # （`_IR_GC_TRIGGER_ADAPT`）是**直接寻址** arena 的（`getelementptr [65536 x i8], ptr
    # @__loment_heap, …`），换 arena 就得连它一起换、也就是**再抄一份那段文本**；而 `hosted`
    # 与 alpha **定义上冲突**（`CORE_CONFLICTS`），仓库里根本没有"间接寻址的 alpha"这份先例。
    # 所以这一档**留在直接寻址的 64 KiB** —— 与改之前**逐字节相同**，并且**说出来**
    # （`; loment-heap: static 65536`，"不静默"）。留这一格不是因为难：见 `_IR_ARENA_STATIC`
    # 的说明，这是"再抄一份"要不要付的问题，`docs/158` §5 那条孪生纪律要挡的正是它。
    _ffi_heap = rt_on and _declares_extern([mod, *deps]) and not _hosted_port
    _ffi_grown = _ffi_heap and not gc_alpha
    #: arena 是不是**从外面来**的（于是分配器与收集器都得走基址指针 `%hb`）
    _indirect = _hosted_grown or _ffi_grown
    if _needs_heap:
        if _hosted_grown:
            out.append(f"; loment-heap: platform {HOSTED_ARENA_BYTES}")
            if "malloc" not in seen_ext:  # 用户自己 `declare` 过就让他那份说了算
                out.append("declare ptr @malloc(i64)")
            out.append(_IR_HEAP_LMS_HOSTED if _lms_rescue else _IR_HEAP_HOSTED)
        elif _ffi_grown:
            out.append(f"; loment-heap: ffi {FFI_ARENA_BYTES}")
            out.append(_IR_HEAP_LMS_FFI if _lms_rescue else _IR_HEAP_FFI)
        else:
            if _ffi_heap:
                # 上面那个例外：说了才不是静默。
                out.append("; loment-heap: static 65536")
            out.append(_IR_HEAP_LMS if _lms_rescue else _IR_HEAP)
    # **根表跟着 `gc_any` 发**（见 `_IR_ROOTS`）：`gc_auto` / `gc_auto_alpha` 只要函数里有
    # 指针槽就会往根表写 —— 而收集器**跟着堆发**。两件事解绑，才能让"有根没堆"的单元
    # （alpha 把 alloc 全 L0 提走那种）不至于发出引用未定义全局的产物。**次序是 HEAP→ROOTS→GC**。
    if gc_auto or gc_alpha:
        out.append(_IR_ROOTS)
        if _needs_heap:
            if gc_auto:
                # **只在自动档追加**（`docs/210` §2.4）—— 另外两档的产物因此逐字节不变。
                # 它引用 `@__loment_heap`/`@__loment_off`/`@__loment_free`，所以必须排在堆之后。
                # `hosted` 那一份与它**同源**（`_arena_indirect` 机器换的，见 `_IR_GC_HOSTED`）：
                # 收集器要认的是**同一块 arena**，所以 arena 换了它也得换。
                # **FFI 那一档共用同一份**：`_IR_GC_HOSTED` 里的表是 `arena/8` 行，而
                # `FFI_ARENA_BYTES` 与 `HOSTED_ARENA_BYTES` **同数**（1 MiB ⇒ 16384 行）——
                # 这两个数是绑在一起的，不是各挑各的（改一个就得改另一个，见那条常量）。
                out.append(_IR_GC_HOSTED if _indirect else _IR_GC)
            else:
                # `gc_auto_alpha` 的**自适应**收集器（`docs/210` §2.4 的"策略池"那一半）：
                # 与 `gc_auto` 同一段 mark-sweep 本体，**触发**换成自适应的那一支。此前
                # alpha 根本不发收集器（L3 残差没人收）—— 这一步让它有。
                out.append(_IR_GC_ALPHA)
    out += globals_
    if globals_:
        out.append("")
    out.append(text_all)
    if debug:
        out.append("")
        out += meta
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- CLI

_PRELUDE = """
module __prelude
pub enum Option<T> { Some(T), None }
pub enum Result<T, E> { Ok(T), Err(E) }
"""


#: `[` 前面出现这些关键字时，它是**类型方括号 / 数组字面量**，不是下标。
_LSPE_BRACKET_KW = frozenset({
    "return", "if", "while", "match", "let", "const", "in", "else", "for",
    "fn", "struct", "enum", "impl", "trait", "pub", "use", "module", "choose",
    "capability", "excluded", "guard", "as", "mut",
})

#: 后接 `(` 也不算函数调用的关键字（`if (...)`、`guard d(...)` …）。
#: 与自举侧 `pt_lspe_call_kw` **逐字同表** —— 两边普查的调用点计数不能漂。
_LSPE_CALL_KW = _LSPE_BRACKET_KW | frozenset({"extern", "true", "false", "self"})

#: 普查里允许出现的站点种类（I1 的第一半）。加一种站点要同时登记在这里。
_LSPE_SITE_KINDS = frozenset({
    "guard", "index", "free", "alive", "init", "exclusive", "call",
})


#: LSPE 的证据种类（`docs/262` §5.3）。**`Differential` 永远不在 accept 集合里** ——
#: 孪生只证一致，不证对（`docs/253` §5.1.1）。
LSPE_EVIDENCE_KINDS = ("Axiom", "Syntactic", "Typed", "LocalFlow",
                       "Interproc", "GlobalInvariant", "Assumed", "Differential")

#: 每种证据**最早**在哪个上下文级别被承认（`docs/262` §5.4 / §5.6）。
#: 没列出的（Axiom / Syntactic / Assumed / Differential）从 L0 起就承认。
_LSPE_EVIDENCE_LEVEL = {"Typed": 1, "LocalFlow": 1, "Interproc": 2, "GlobalInvariant": 3}

#: LSPE 的 14 条义务（`docs/262` §5.2）。每条都点名**语义所有者**与**接收的听说种类**。
#: LSPE 不发明语义：`owner` 一项是指到那份语义文档/码的。
LSPE_OBLIGATIONS: dict[str, dict[str, object]] = {
    "InBounds": {"owner": "docs/146 / E028",
                 "accept": ("Syntactic", "Typed", "LocalFlow", "Interproc",
                            "GlobalInvariant", "Assumed")},
    "Alive": {"owner": "docs/210 / E029",
              "accept": ("LocalFlow", "Interproc", "Assumed")},
    "InDomain": {"owner": "docs/146",
                 "accept": ("Typed", "GlobalInvariant", "Assumed")},
    "Exclusive": {"owner": "docs/206 / E007",
                  "accept": ("LocalFlow", "Interproc", "Assumed")},
    "Initialized": {"owner": "docs/206 / E006",
                    "accept": ("Syntactic", "LocalFlow", "Interproc", "Assumed")},
    "NotDangling": {"owner": "docs/206 / E012",
                    "accept": ("LocalFlow", "Interproc", "Assumed")},
    "NoRuntime": {"owner": "docs/182 / E031",
                  "accept": ("Axiom", "Syntactic", "Typed")},
    "FreeOwnership": {"owner": "docs/210",
                      "accept": ("LocalFlow", "Interproc", "Assumed")},
    "LadderLiability": {"owner": "docs/210",
                        "accept": ("Typed", "Interproc", "GlobalInvariant", "Assumed")},
    "CapabilityLive": {"owner": "docs/146",
                       "accept": ("Typed", "GlobalInvariant", "Assumed")},
    "TypeWellFormed": {"owner": "docs/158 / docs/147",
                       "accept": ("Axiom", "Typed", "Interproc", "Assumed")},
    "DropHonest": {"owner": "docs/253 §1.2",
                   "accept": ("Assumed",)},
    "GuardHolds": {"owner": "docs/146",
                   "accept": ("Syntactic", "Typed", "GlobalInvariant", "Assumed")},
    "SummaryContract": {"owner": "docs/210 §7",
                        "accept": ("Axiom", "Interproc", "Assumed")},
}


class LspeSolver:
    """证据子集格上的工作列表（`docs/262` §5.3 / §5.5）。

    P0 每个站点只有一条义务、没有传播边；但队列顺序与"只做并集"的单调性已经按最终
    形状写死。接 E3/E4 时只加 successors，不改求解器，也不改确定性的那几条规矩。

    * 证据是 `LSPE_EVIDENCE_KINDS` 的**子集**，按包含关系构成有限格；
    * `Differential` 永远不在 accept 集合里 —— 它最多是闸门，不能销义务；
    * 队列按站点 id 排序，不是哈希序。
    """

    def __init__(self, sites: list[dict], level: int = 3) -> None:
        self.level = level
        self.order = sorted(sites, key=lambda s: s["id"])
        self.accept = {k: set(v["accept"]) for k, v in LSPE_OBLIGATIONS.items()}
        self.evidence = {s["id"]: self._initial(s) for s in self.order}

    def _initial(self, site: dict) -> frozenset:
        ev = site.get("evidence")
        # 级别只缩不放（`docs/262` §5.6）：E2 的 Typed 与 E3 的 LocalFlow 从 L1 起，
        # E4 的 Interproc 从 L2 起，E5 的 GlobalInvariant 从 L3 起。
        if self.level < _LSPE_EVIDENCE_LEVEL.get(ev, 0):
            return frozenset()
        if ev in (None, "none"):
            return frozenset()
        if ev in LSPE_EVIDENCE_KINDS:
            return frozenset({ev})
        return frozenset()

    def run(self) -> "LspeSolver":
        queue = list(self.order)
        while queue:
            site = queue.pop(0)
            # 目前没有传播边；保留 worklist 形状，E3/E4 在这里加 successors。
            self.evidence[site["id"]] = frozenset(self.evidence[site["id"]])
        return self

    def proved(self, site: dict) -> bool:
        return bool(self.evidence[site["id"]] & self.accept.get(site["obligation"], set()))


def _lspe_census(toks: list, unit: str) -> tuple[list[dict], list[int]]:
    """站点普查（`docs/262` §5.1）：E1/E2（guard + 下标）、E3、E4。

    口径是**词法的**，且两套实现用**同一条规则** —— 参考侧吃 `lomc.lex` 的 token，
    自举侧吃驱动器的 token 数组。这样 twin 的计数不会因为 AST 形状不同而漂。

    线性 CFG：语句按 token 顺序执行，`return` 结束它所在块剩下一段。这是 E3 的
    "固定顺序"定义（`docs/262` §5.4）——没有分支敏感，只有块结构。
    """
    sites: list[dict] = []
    unsourced: list[int] = []
    seen: dict[str, int] = {}

    def sid(kind: str, key: str) -> str:
        # 内容寻址：键是站点的 token 文本，重复序数只区分**完全相同**的表达式。
        # 不用行号（会漂），也不用全局序号（在前面插一个站点会把后面全部重编号）。
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        return f"{unit}:{kind}:{_fnv1a32(key + '#' + str(occ)):08x}"

    _lspe_census_e1e2(toks, sid, sites, unsourced)
    _lspe_census_e3(toks, sid, sites)
    _lspe_census_e4(toks, sid, sites)
    return sites, unsourced


def _lspe_census_e1e2(toks: list, sid, sites: list[dict], unsourced: list[int]) -> None:
    """E1 常量 + E2 类型（`docs/262` §5.4）。

    * `guard` 关键字 = 一个 guard 站点，来源 `capability`；
    * `[` 且前一个 token 是**非关键字标识符 / `)` / `]`** = 一个 index 站点；
      下标是**裸字面量**（`[ N ]` 三个 token）时来源 `lms(g0)`，否则无来源。
    """
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind == "ident" and t.val == "guard":
            cap = toks[i + 1].val if i + 1 < n else ""
            sites.append({"id": sid("guard", f"guard:{cap}"), "kind": "guard",
                          "claim": "allow", "obligation": "GuardHolds",
                          "evidence": "Typed",
                          "source": "capability", "line": t.line, "proved": True})
            continue
        if t.kind != "punct" or t.val != "[" or i == 0:
            continue
        prev = toks[i - 1]
        indexish = ((prev.kind == "ident" and prev.val not in _LSPE_BRACKET_KW)
                    or (prev.kind == "punct" and prev.val in (")", "]")))
        if not indexish:
            continue
        # 能力域声明 `capability d : disk[0..8]` 的 `[` 不是下标 —— 里面有 `..`
        # （词法器把它拆成两个 `.` token）。
        if (i + 3 < n and toks[i + 1].kind == "number"
                and toks[i + 2].kind == "punct" and toks[i + 2].val == "."
                and toks[i + 3].kind == "punct" and toks[i + 3].val == "."):
            continue
        literal = (i + 2 < n and toks[i + 1].kind == "number"
                   and toks[i + 2].kind == "punct" and toks[i + 2].val == "]")
        # 站点内容 = LHS 的最近几个 token + 方括号里的 token 文本；空格/注释/换行不进键。
        depth = 0
        j = i + 1
        inner: list[str] = []
        while j < n:
            if toks[j].kind == "punct" and toks[j].val == "[":
                depth += 1
            elif toks[j].kind == "punct" and toks[j].val == "]":
                if depth == 0:
                    break
                depth -= 1
            inner.append(toks[j].val)
            j += 1
        # 只用**紧邻的前一个 token** 作 LHS。往前多抓几个 token 会把上一条语句
        # （`;`、`return` …）也卷进键里 —— 插入一个无关站点就会让 id 变。
        lhs = prev.val
        sites.append({"id": sid("index", f"index:{lhs}[{''.join(inner)}]"),
                      "kind": "index",
                      "claim": "allow", "obligation": "InBounds",
                      "evidence": ("Syntactic" if literal else "none"),
                      "source": ("lms(g0)" if literal else None),
                      "line": t.line, "proved": literal})
        if not literal:
            unsourced.append(t.line)


def _lspe_match_brace(toks: list, i: int) -> int | None:
    d = 0
    for j in range(i, len(toks)):
        t = toks[j]
        if t.kind != "punct":
            continue
        if t.val == "{":
            d += 1
        elif t.val == "}":
            d -= 1
            if d == 0:
                return j
    return None


def _lspe_functions(toks: list) -> list[tuple[int, int, int, int]]:
    """本单元每个**有体**函数的 `(名字 token, 首形参 token, 体首 token, 体尾 token)`。"""
    n = len(toks)
    out: list[tuple[int, int, int, int]] = []
    for i, t in enumerate(toks):
        if not (t.kind == "ident" and t.val == "fn"):
            continue
        if i > 0 and toks[i - 1].kind == "ident" and toks[i - 1].val == "extern":
            continue
        if i + 1 >= n or toks[i + 1].kind != "ident":
            continue
        # 找形参表的 `(`（跳过 `<...>`）
        k = i + 2
        while k < n and not (toks[k].kind == "punct" and toks[k].val == "("):
            if toks[k].kind == "punct" and toks[k].val in (";", "{"):
                break
            k += 1
        if k >= n or not (toks[k].kind == "punct" and toks[k].val == "("):
            continue
        d = 0
        j = k
        while j < n:
            if toks[j].kind == "punct" and toks[j].val == "(":
                d += 1
            elif toks[j].kind == "punct" and toks[j].val == ")":
                d -= 1
                if d == 0:
                    break
            j += 1
        if d != 0:
            continue
        b = j + 1
        while b < n and not (toks[b].kind == "punct" and toks[b].val in ("{", ";")):
            b += 1
        if b >= n or toks[b].val != "{":
            continue
        e = _lspe_match_brace(toks, b)
        if e is None:
            continue
        out.append((i + 1, k + 1, b + 1, e))
    return out


def _lspe_is_write(toks: list, k: int) -> bool:
    """`x = ...` 是写；`x == ...` 是读（词法器把 `==` 拆成两个 `=` token）。"""
    n = len(toks)
    return (k + 1 < n and toks[k + 1].kind == "punct" and toks[k + 1].val == "="
            and not (k + 2 < n and toks[k + 2].kind == "punct" and toks[k + 2].val == "="))


def _lspe_unreachable(toks: list, free_i: int, use_i: int) -> bool:
    """从 use 反向走到 free：同一块或**外层**块里出现过 `return` ⇒ 这句不可达。

    反向走法自然区分兄弟块：兄弟块里的 `return` 会遇到它的 `}` 被记进深度，不算。
    """
    d = 0
    k = use_i - 1
    while k > free_i:
        t = toks[k]
        if t.kind == "punct":
            if t.val == "}":
                d += 1
            elif t.val == "{" and d > 0:
                d -= 1
        elif t.kind == "ident" and d == 0 and t.val == "return":
            return True
        k -= 1
    return False


def _lspe_owned(toks: list, at: int, free_i: int, name_i: int) -> bool:
    """`free(x)` 之前最近一处 `let x` 是不是 `let x : ptr = alloc (`。"""
    k = free_i - 1
    while k > at:
        if (toks[k].kind == "ident" and toks[k].val == toks[name_i].val
                and toks[k - 1].kind == "ident" and toks[k - 1].val == "let"):
            n = len(toks)
            return (k + 5 < n and toks[k + 1].kind == "punct" and toks[k + 1].val == ":"
                    and toks[k + 2].kind == "ident" and toks[k + 2].val == "ptr"
                    and toks[k + 3].kind == "punct" and toks[k + 3].val == "="
                    and toks[k + 4].kind == "ident" and toks[k + 4].val == "alloc"
                    and toks[k + 5].kind == "punct" and toks[k + 5].val == "(")
        k -= 1
    return False


def _lspe_census_e3(toks: list, sid, sites: list[dict]) -> None:
    """E3 局部流（`docs/262` §5.4）：`FreeOwnership` / `Alive` / `Initialized` / `Exclusive`。

    规则与检查器的局部表**同源**（E029 的 `freed` 表、E006 的 `moved` 表）：这里是
    把那些判决变成证据，不是另写一套语义。线性 CFG 见 `_lspe_census` 的说明。
    """
    for _name_i, _p_i, at, end in _lspe_functions(toks):
        for i in range(at, end):
            t = toks[i]
            # ---- FreeOwnership + 之后的 Alive ----
            if (t.kind == "ident" and t.val == "free" and i + 3 < end
                    and toks[i + 1].kind == "punct" and toks[i + 1].val == "("
                    and toks[i + 2].kind == "ident"
                    and toks[i + 3].kind == "punct" and toks[i + 3].val == ")"):
                name_i = i + 2
                name = toks[name_i].val
                uses: list[int] = []
                for j in range(i + 4, end):
                    if toks[j].kind != "ident" or toks[j].val != name:
                        continue
                    if j > at and toks[j - 1].kind == "ident" and toks[j - 1].val == "let":
                        continue                       # 重新声明 = 新名字主人
                    if _lspe_is_write(toks, j):
                        continue                       # 写是重新指向，不是用旧块
                    uses.append(j)
                reachable = [j for j in uses if not _lspe_unreachable(toks, i + 3, j)]
                owned = _lspe_owned(toks, at, i, name_i)
                sites.append({"id": sid("free", f"free:{name}"),
                              "kind": "free", "claim": "allow",
                              "obligation": "FreeOwnership",
                              "evidence": ("LocalFlow" if owned and not reachable else "none"),
                              "source": ("local-flow" if owned and not reachable else None),
                              "line": t.line, "proved": owned and not reachable})
                for j in uses:
                    un = _lspe_unreachable(toks, i + 3, j)
                    sites.append({"id": sid("alive", f"alive:{name}:{j - i}"),
                                  "kind": "alive", "claim": "allow",
                                  "obligation": "Alive",
                                  "evidence": ("LocalFlow" if un else "none"),
                                  "source": ("local-flow" if un else None),
                                  "line": toks[j].line, "proved": un})
            # ---- Initialized（`let x: T;` 之后的读） ----
            if (t.kind == "ident" and t.val == "let" and i + 1 < end
                    and toks[i + 1].kind == "ident"):
                name = toks[i + 1].val
                depth = 0
                initialized = False
                j = i + 2
                while j < end:
                    v = toks[j].val if toks[j].kind == "punct" else None
                    if v in ("(", "[", "{"):
                        depth += 1
                    elif v in (")", "]", "}") and depth > 0:
                        depth -= 1
                    elif v == "=" and depth == 0:
                        # 声明里的 `=`（`==` 是读，不算；它的第一个 `=` 后面紧跟 `=`）。
                        if not (j + 1 < end and toks[j + 1].kind == "punct"
                                and toks[j + 1].val == "="):
                            initialized = True
                    elif v == ";" and depth == 0:
                        break
                    j += 1
                if initialized or j >= end:
                    continue
                assigned = False
                for k in range(j + 1, end):
                    if (toks[k].kind == "ident" and toks[k].val == "let"
                            and k + 1 < end and toks[k + 1].kind == "ident"
                            and toks[k + 1].val == name):
                        break
                    if toks[k].kind != "ident" or toks[k].val != name:
                        continue
                    if k > at and toks[k - 1].kind == "ident" and toks[k - 1].val == "let":
                        continue
                    if _lspe_is_write(toks, k):
                        assigned = True
                        continue
                    sites.append({"id": sid("init", f"init:{name}:{k - j}"),
                                  "kind": "init", "claim": "allow",
                                  "obligation": "Initialized",
                                  "evidence": ("LocalFlow" if assigned else "none"),
                                  "source": ("local-flow" if assigned else None),
                                  "line": toks[k].line, "proved": assigned})
            # ---- Exclusive（`&mut x` 与同一语句里的另一次使用重叠） ----
            if (t.kind == "punct" and t.val == "&" and i + 2 < end
                    and toks[i + 1].kind == "ident" and toks[i + 1].val == "mut"
                    and toks[i + 2].kind == "ident"):
                name = toks[i + 2].val
                other = False
                k = i + 3
                while k < end:
                    if toks[k].kind == "punct" and toks[k].val == ";":
                        break
                    if toks[k].kind == "ident" and toks[k].val == name:
                        other = True
                    k += 1
                sites.append({"id": sid("exclusive", f"exclusive:{name}:{i}"),
                              "kind": "exclusive", "claim": "allow",
                              "obligation": "Exclusive",
                              "evidence": ("none" if other else "LocalFlow"),
                              "source": (None if other else "local-flow"),
                              "line": t.line, "proved": not other})


def _lspe_safe_set(toks: list, fns: list[tuple[int, int, int, int]],
                   extra: list[tuple[list, list]] = ()) -> set[str]:
    """deref-only 摘要的全局不动点（`docs/210` §7）。

    与自举侧 `compute_safe` **同一条 token 规则**（含 `syscall_borrow_ok` 那一支），
    所以两边算出的安全名集合相同 —— E4 的 `Interproc` 计数不能漂。
    `extra` 是依赖单元：自举侧 `compute_safe` 扫的是**整个单元**（依赖在根前面），
    所以发射时的安全表必须把依赖一起算进来（`load32 -> load16 -> load8` 常常跨模块）。
    递归环只有在有基例时才进入集合；纯递归的函数停在集合外，调用点记 UNDECIDED。
    """
    units = [(toks, fns)] + list(extra)
    safe: set[str] = set(_L0_SAFE_BUILTINS)
    changed = True
    rounds = 0
    while changed and rounds < 256:
        changed = False
        rounds += 1
        for utoks, ufns in units:
            for name_i, p_i, at, end in ufns:
                name = utoks[name_i].val
                if name in safe:
                    continue
                if (p_i + 2 >= end or utoks[p_i].kind != "ident"
                        or utoks[p_i].val == "self"):
                    continue
                if not (utoks[p_i + 1].kind == "punct" and utoks[p_i + 1].val == ":"
                        and utoks[p_i + 2].kind == "ident" and utoks[p_i + 2].val == "ptr"):
                    continue
                p = utoks[p_i].val
                ok = True
                for k in range(at, end):
                    if utoks[k].kind != "ident" or utoks[k].val != p:
                        continue
                    if (k > 0 and utoks[k - 1].kind == "ident"
                            and utoks[k - 1].val == "let") or _lspe_is_write(utoks, k):
                        ok = False
                        break
                    if _syscall_borrow_ok(utoks, k):
                        continue
                    unqualified = (k < 3
                                   or not (utoks[k - 3].kind == "punct"
                                           and utoks[k - 3].val in (".", ":")))
                    call_ok = (k >= 2 and k + 1 < len(utoks)
                               and utoks[k - 1].kind == "punct" and utoks[k - 1].val == "("
                               and utoks[k - 2].kind == "ident" and unqualified
                               and utoks[k + 1].kind == "punct"
                               and utoks[k + 1].val in (",", ")")
                               and utoks[k - 2].val in safe)
                    if not call_ok:
                        ok = False
                        break
                if ok:
                    safe.add(name)
                    changed = True
    return safe


def _lspe_census_e4(toks: list, sid, sites: list[dict],
                    extra: list[tuple[list, list]] = ()) -> None:
    """E4 过程间摘要（`docs/262` §5.4）：首参是 `ptr` 的函数的每个调用点一条
    `SummaryContract` 站点。摘要在安全表里 ⇒ `Interproc`；否则是洞（`none`）。

    摘要键是**调用点的实际实参 token 文本**，不是只有函数名 —— 推断式泛型的替换
    由实参决定，所以同一个 `f` 的两次不同调用是两个键。

    `extra` 是依赖单元：安全表要看**根 + 依赖**（自举侧 `compute_safe` 扫整个单元），
    但首参表只看根 —— 依赖里的函数不是这份产物要审计的站点。
    """
    fns = _lspe_functions(toks)
    ptr_first = {toks[n].val for (n, p, _at, end) in fns
                 if p + 2 < end and toks[p].kind == "ident"
                 and toks[p + 1].kind == "punct" and toks[p + 1].val == ":"
                 and toks[p + 2].kind == "ident" and toks[p + 2].val == "ptr"}
    if not ptr_first:
        return
    safe = _lspe_safe_set(toks, fns, extra)
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "ident" or t.val in _LSPE_CALL_KW or t.val not in ptr_first:
            continue
        if not (i + 1 < n and toks[i + 1].kind == "punct" and toks[i + 1].val == "("):
            continue
        if i > 0 and toks[i - 1].kind == "punct" and toks[i - 1].val in (".", ":"):
            continue
        if i > 0 and toks[i - 1].kind == "ident" and toks[i - 1].val == "fn":
            continue
        depth = 0
        j = i + 1
        inner: list[str] = []
        while j < n and len(inner) < 32:
            if toks[j].kind == "punct" and toks[j].val == "(":
                depth += 1
            elif toks[j].kind == "punct" and toks[j].val == ")":
                depth -= 1
                if depth == 0:
                    break
            inner.append(toks[j].val)
            j += 1
        proved = t.val in safe
        sites.append({"id": sid("call", f"call:{t.val}{''.join(inner)}"),
                      "kind": "call", "claim": "allow",
                      "obligation": "SummaryContract",
                      "evidence": ("Interproc" if proved else "none"),
                      "source": (t.val if proved else None),
                      "line": t.line, "proved": proved})


def load(path: Path, sw: SwitchTable | None = None, lspe_level: int = 3) -> Module:
    # **前门**（`docs/188` §7.2）：读法由**声明**决定，不由后缀或嗅探。
    # 一份 `choose write grammar python` 的 `.lomt` 按 `docs/188` §0 **仍然是 Loment**，
    # 只是拼法不同 —— 所以这里先把它**翻成 Loment 源码**再往下走。
    # **全程在本进程里算，不拉起 python / java / …**（用户定死的那条）。
    # 写明了 `grammar loment` 的那一行也在这里被抹成等长空白 —— **parser 不参与**，
    # 所以语言面两个实现、种子，这一格都不用动。
    #
    # import 放函数里：`potato_from` 会把各门翻译器按需拉进来，而**绝大多数文件是 Loment**。
    from potato_from import front_door          # noqa: PLC0415

    _fu = front_door(path)
    text = _fu.source
    # 开关**在词法流上**落定（`docs/182` §2）：`set choose X {…}` 开着就把体摊到顶层、
    # 关着就整段抹掉, 那两行本身不进 AST。**这一步必须在 parse 之前** —— 顺序问题
    # （取值可能写在体的后面）在 token 流上就消失了。
    #
    # **两张表，别混**（`docs/182` §1.10）：
    #   * `sw`  = **整个程序**的表，来自预扫（`prescan_switches`），可能含**别的单元**
    #     定义的开关 —— 裁减要用它，否则 `addin` 进来的开关管不到这里。
    #   * `own` = **本模块自己**声明了哪些 —— 只有"库不许 `choose`"用它。
    #
    # 混成一张表的话，每个被 `use` 进来的库都会"因为全局表里有定义"而被误判成**库写了
    # choose** —— 那会把 E022 第三条变成对所有库开火。
    toks = lomc.lex(text)
    own = SwitchTable()
    _collect_switches(toks, own)
    tbl = sw if sw is not None else own
    tt = _apply_switches(toks, tbl, collect=False)
    # `comefor` 的展开（`docs/184` §9 S4.1）：**也在 token 层，也在 parse 之前**。
    #
    # **必须在开关之后**，理由与"关着就解析跳过"是同一条：一个关着的 `set choose` 体里
    # 写的 `comefor` 该连 token 都不剩。反过来先展开的话，那段体里的方言词会先被认成
    # **一次使用**（而它本该整段消失）—— 顺序错一步，症状是"关着的语法仍然生效"。
    import loment_comefor

    tt, _dias = loment_comefor.expand(tt, text)
    mod = Parser(tt, text).parse()
    # LSPE 的站点普查（`docs/262` §5.1）：在**最终 token 流**上算一次。
    # 与自举侧 `potato.lomt` 的 `pt_lspe` 同一条规则 —— twin 的计数不能漂。
    mod.lspe_sites, mod.lspe_unsourced_lines = _lspe_census(tt, mod.name)
    mod.lspe_level = lspe_level
    # 方言清单挂在模块上，供 Potato v4 用（`docs/184` §9 S4.3）。**定义那一段已经被
    # 抹掉了**，所以这是唯一还记得"这份源用了哪些自定义语法"的地方。
    mod.dialects = _dias
    # **表层语法**（`docs/188` §2）挂在模块上，供 Potato v6 用。前门已经算出来了
    # （`front_door` 的 `FrontUnit.grammar`：原生写法一律是 `loment`，`rust` 也是
    # 基础语法的一种拼法、归到 `loment`；别的写法给的是那一门的规范名）。
    # 与 `dialects` 同一个做法：不是 dataclass 字段，是装载时挂上去的。
    mod.grammar = _fu.grammar
    mod.switches = tbl          # **整个程序**的表（单文件装载时就是 `own`）
    mod.own_switches = own      # 本模块**自己**写的 —— 只有"库不许 choose"用它
    # `addin` 的行单独记一份 —— 它被抹掉了，而"库里写了 `addin` 却没生效"要能报出来。
    _d = 0
    for _i, _t in enumerate(toks):
        if _t.kind == "punct" and _t.val in "{[(":
            _d += 1
        elif _t.kind == "punct" and _t.val in "}])":
            _d -= 1
        elif _d == 0 and _t.kind == "ident" and _t.val == "addin" \
                and _i + 1 < len(toks) and toks[_i + 1].kind == "ident":
            mod.addin_lines.append((toks[_i + 1].val, _t.line))
    # 命令声明（`docs/223` 形态 B）。**在 token 流上读**，与 `addin` / `source_ext` 同一层
    # 理由：它是标签、不是语法 —— 让它进 AST 就要动 lexer/parser/checker/codegen 两套实现。
    mod.command_line, mod.command, mod.command_bad = command_label_from_tokens(toks)
    # 命令体入口。与 `_start` 互斥，两个都是进程入口（`check()` 里报）。
    mod.command_main = any(f.name == "command_main" for f in mod.funcs)
    names = {e.name for e in mod.enums}  # M10: 预置 Option/Result
    if "Option" not in names or "Result" not in names:
        pre = Parser(lomc.lex(_PRELUDE), _PRELUDE).parse()
        for e in pre.enums:
            if e.name not in names:
                e.from_prelude = True
                # line 归零: 注入项的行号来自 **prelude 文件**, 原样留着的话文档生成器会
                # 拿它去查**目标文件**的行 —— 实测 Result 会抄到上文某条 capability 的注释。
                # 注入项在目标文件里没有行, 就该是 0 (Loment 版 lomdoc 也是这么做的)。
                e.line = 0
                mod.enums.append(e)
    # 记住源文件在哪 —— 调试信息（DWARF 的 `!DIFile`）要它。见 `Module.src` 的注释。
    #
    # **翻译出来的单元不声称源文件是那一份 `.lomt`**：`!DIFile` 说的是"行号属于哪个文件"，
    # 而这份的行号来自**翻译后的正文** —— 声称了就成了一句把调试器**引向错行**的话
    # （`docs/179` §6.5 那一类）。`m.src` 缺席时下游退回旧行为，**比说错话好**。
    mod.src = None if _fu.translated else path
    return mod


# ---------------------------------------------------- 结构化诊断 (docs/182 §5)
#
# 报错器 `lomenterr` 的输入前提：编译器要能吐**机器可读**的诊断，而不是只有人看的文本。
#
# **为什么另给一条路径，不混进 stderr**：stderr 上同时存在两种格式，解析方就得"嗅"；
# 本仓一贯反对嗅 —— `docs/179` §6 那个 `clang` 按后缀认语言的坑，症状正是"rc=0 却什么
# 都不产出"。另开 fd 在 PE/POSIX 上语义不一致（Windows 上 fd 3 不是继承来的），路径最稳。
#
# **`title`/`hint` 不在这里**（`docs/182` §5.3）：编译器只报"是什么、在哪" ——
# `code` + 位置 + 原文片段，**不解释**。标题与修复建议由**报错器** `lomenterr` 拿 `code`
# 去查 `surface_data` 补。
#
# 为什么这么切：两个实现的**消息文本本来就不同**（参考吐整句中文，自举手里只有
# 码 + token 索引），`docs/158` §4 记着这类分叉。硬凑"两侧 JSONL 逐字节一致"只会造出
# 一堆假一致；诚实的靶子是**码一致 + 形状一致**。切干净之后，自举驱动**根本不需要那张表**，
# 第三份手抄从设计上就不存在 —— 渲染本来就是报错器该干的事。
#
# **一行一条 JSON（JSONL）而不是一个数组**：边报边写，崩在半路也已经落盘。
DIAG_FIELDS = ("file", "line", "col", "code", "message")


def _leading_line(msg: str) -> int:
    """`check()` 的消息形如 `"12: 文本"`（**只有行，没有列**）。取不出行号就是 0。

    不引 `re`：这个文件一直没用过正则，为切一个冒号引进来不值。
    """
    head, sep, _ = msg.partition(":")
    head = head.strip()
    return int(head) if sep and head.isdigit() else 0


#: 不是"消息点名的那个名字"的词：关键字、内建类型、以及消息里常见的动词。
#: 推断列号时一律跳过 —— 少给一列比给一列**错的**好（与渲染器"只印确实知道的那部分"同一条）。
_COL_STOP = frozenset("""
fn let mut return if else while for match use module pub const struct enum impl trait
capability guard as true false and or not
u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str void
syscall4 syscall6 syscall7
""".split())


def _col_from_msg(text: str, line: int, msg: str) -> int:
    """语义错只带行号（`check()` 的约定）；**能从消息里无歧义地定出列**时补上（`#5`）。

    规则保守：消息里出现的标识符，必须**恰好一个**不是关键字、且在那一行里**恰好出现
    一次** —— 才用它的列。`使用未声明的变量 nosuchv` 定得出；`函数 f 参数 K 重复`
    定不出（`f` 与 `K` 都在行里），那就回 0，让调用方沿用"整行"的旧行为。
    """
    if line <= 0 or not msg:
        return 0
    rows = text.split(chr(10))
    if line > len(rows):
        return 0
    row = rows[line - 1]
    hits: list[int] = []
    seen: set[str] = set()
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", msg):
        if name in _COL_STOP or name in seen:
            continue
        seen.add(name)
        if len(re.findall(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", row)) == 1:
            hits.append(row.index(name) + 1)
    return hits[0] if len(hits) == 1 else 0


def diag_record(src: Path, line: int, col: int, msg: str) -> dict:
    """一条裸消息 -> 一条结构化诊断。**分类复用 `loment_diag.RULES`，不新造一份**
    （两份分类表必然漂，见 `docs/179` §8.1 第 4 条那个"同一批字段被查两遍"的教训）。

    取的是 `classify()` 的**第一个返回值**（码）；标题/建议不在这里出（见上）。
    """
    # 延迟 import：`loment_diag` 顶部就 `import lomentc`，模块级 import 会成环。
    import loment_diag
    code = loment_diag.classify(msg)[0]
    return {"file": str(src), "line": line, "col": col, "code": code, "message": msg}


def write_diags(out: str | None, records: list[dict]) -> None:
    """把诊断按一行一条 JSON 写到调用方给的路径。没给路径就什么都不做。"""
    if not out or not records:
        return
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[DIAG] {len(records)} 条 -> {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomentc", description="L1 Loment 编译器 v0")
    ap.add_argument("file", help=".lomt 源文件")
    ap.add_argument("--diag-out", metavar="PATH",
                    help="把诊断按一行一条 JSON 写到这里（供外部报错器渲染，docs/182 §5）")
    ap.add_argument("--emit-rust", metavar="PATH")
    ap.add_argument("--emit-potato", metavar="PATH")
    ap.add_argument("--emit-llvm", metavar="PATH", help="LLVM IR (native M0: 标量子集, docs/144)")
    ap.add_argument("--coverage", action="store_true", help="M63: IR 块覆盖计数 (配合 --emit-llvm)")
    ap.add_argument("--debug", action="store_true", help="M59: DWARF 行表元数据 (配合 --emit-llvm)")
    ap.add_argument("--print", dest="print_target", choices=("rust", "potato", "llvm"))
    ap.add_argument("--check", action="store_true")
    #: **这个单元注册的命令名**（`docs/223`）。给构建前端用的：它们要拿它去命名产物
    #: （`loment-<名字>`）。没声明就打印一个空行 —— 调用方按"空 = 不是命令"处理。
    #: 单独一趟而不是从 IR 里读：声明读在**装载**那一层，链接器那边看不到它。
    ap.add_argument("--print-command", action="store_true",
                    help="打印本单元注册的命令名 (没声明就打空行), docs/223")
    ap.add_argument("--lom-root", default=None, help="use 的 .lom 搜索根 (默认仓库根)")
    ap.add_argument("--lspe-level", type=int, default=3, choices=(0, 1, 2, 3, 4),
                    help="LSPE 上下文级别（默认 3；只缩不放）")
    args = ap.parse_args(argv)

    # `--diag-out`: **现在就建/清空**, 不是等出错了再建 —— 与自举驱动同一口径 (docs/182 §5)。
    # 这样上一次跑剩的旧文件不会被这一次误读 (空文件 = "这一次没有诊断"), 而路径写错
    # 会**立刻**炸出来, 不是"编译通过所以没写文件"看不出来。
    if args.diag_out:
        Path(args.diag_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.diag_out).write_text("", encoding="utf-8", newline="\n")

    # M46: 形式对象不可关闭 —— 产出任何后端工件必须同时导出 Potato 形式对象。
    if (args.emit_rust or args.emit_llvm) and not args.emit_potato:
        print("[ERR] M46: 编译器强制导出形式对象 —— 需同时给出 --emit-potato PATH",
              file=sys.stderr)
        return 2

    root = Path(args.lom_root) if args.lom_root else Path(__file__).resolve().parent.parent
    path = Path(args.file)
    try:
        # 预扫 → 装载 → 解析依赖，**一个入口**（`docs/182` §1.10）。
        mod, deps = load_unit(path, root, lspe_level=args.lspe_level)
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        # 位置指不出来的那一档（`line == 0`，例如"前门拒了这份源"）**不写**结构化诊断 ——
        # 写进去就等于给它编了一个行号，而那个行号会被渲染器当真。
        #
        # **`E028`..`E030` 那几条不受这条管**（`issue #8`）：它们说的就是"这条诊断不属于
        # 某一行"（文件读不到、产物写不出去、单元太大），所以它们**写**，`line` 明写 **0**。
        # 渲染器那边认得 0 = 没有位置，既不印 `:0`、也不去源里切那两行。
        if e.line > 0:
            write_diags(args.diag_out, [diag_record(path, e.line, e.col, e.msg)])
        return 1
    except OSError as e:
        # **文件这一层的失败**（`issue #8`）：路径不存在、给的是目录、没有读权限。
        # 这一路原先**一句话都没有** —— `Path.read_text` 抛 `FileNotFoundError` /
        # `PermissionError` / `IsADirectoryError`，用户拿到的是 Python 栈。它与"源码写错了"
        # 是两件事（源码一个字符都还没看），所以它有自己的码，`line` 是 0。
        msg = f"读不了输入文件 {path}: {e.strerror or e}"
        print(f"[ERR] {path}: {msg}", file=sys.stderr)
        write_diags(args.diag_out, [diag_record(path, 0, 0, msg)])
        return 1

    errs = check(mod, deps=deps)
    if errs:
        print(f"[ERR] {path}: {len(errs)} 项语义错误:", file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        # `check()` 的消息带行不带列，所以要切一次前缀（见 `_leading_line`）。
        # `check()` 只给行；能无歧义定出列时补上（`_col_from_msg`，`#5`）。
        _text = path.read_text(encoding="utf-8", errors="replace")
        write_diags(args.diag_out,
                    [diag_record(path, _l, _col_from_msg(_text, _l, e), e)
                     for e in errs for _l in (_leading_line(e),)])
        return 1

    if args.print_command:
        # 只回答"这个单元注册了哪条命令"。**放在 check 之后**：声明不合法时先报错，
        # 不能把一个非法名字当成构建前端要的那个名字（那样构建器会照着写一个畸形产物名）。
        print(mod.command or "")
        return 0

    try:
        rust = emit_rust(mod, root, deps)
        potato = emit_potato(mod, root, deps)
        llvm = emit_llvm(mod, root, deps, coverage=args.coverage, debug=args.debug) \
            if (args.emit_llvm or args.print_target == "llvm") else ""
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        write_diags(args.diag_out, [diag_record(path, e.line, e.col, e.msg)])
        return 1

    if args.print_target:
        sys.stdout.write({"rust": rust, "potato": potato, "llvm": llvm}[args.print_target])
        return 0

    targets = {
        "rust": (args.emit_rust, rust),
        "potato": (args.emit_potato, potato),
        "llvm": (args.emit_llvm, llvm),
    }
    if not any(dest for dest, _ in targets.values()) and not args.check:
        print("[ERR] 未指定输出 (--emit-rust / --emit-potato / --print / --check)", file=sys.stderr)
        return 2

    diffs: list[str] = []
    for kind, (dest, want) in targets.items():
        if not dest:
            continue
        p = Path(dest)
        if args.check:
            if not p.exists():
                diffs.append(f"{dest}: 缺失")
            elif p.read_text(encoding="utf-8") != want:
                diffs.append(f"{dest}: 与转译结果不一致")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(want, encoding="utf-8", newline="\n")
            print(f"[OK] {path} -> {dest} ({len(want)}B)")
    if args.check:
        if diffs:
            print(f"[DIFF] {path}: {len(diffs)} 项漂移:", file=sys.stderr)
            for d in diffs:
                print("  " + d, file=sys.stderr)
            return 1
        print(f"[OK] {path}: 生成物与磁盘一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
