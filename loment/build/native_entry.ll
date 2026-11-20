; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe


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

; ---- 分配器（docs/175 §3.4 的 `gc_manual`）------------------------------------
;
; 这里以前是**纯 bump**（`@__loment_off` 只涨不落），而 `free` 是个**占位** ——
; 写它编得过、跑起来什么都不发生。`docs/175` §3.4 把那种形状点名叫「**允诺却不兑现**」，
; 所以这一版把它兑现：**bump + 地址序空闲链表**。
;
; 块的形状（8 字节头，8 字节对齐）：
;   +0  i32  size   —— 这一块的**总长**（含头）
;   +4  i32  next   —— 下一块**空闲**块的偏移；0 = 链尾（所以偏移 0 永远不是块）
; 载荷从 +8 开始。整个 arena 是 `@__loment_heap`，偏移从 0 起算。
;
; 三条规则，各有它要挡的东西：
;   * **第一次适配 + 拆分**：空闲块大到有余（余量 ≥ 16）就切一块出来，余下的还回链上。
;     不拆的话一次大分配会把整个链表吃光。
;   * **地址序**：链表按地址升序，`free` 线性找插入点。顺序让「前沿回退」可判。
;   * **前沿回退**：释放的那一块**正好顶到 bump 前沿**时，直接把前沿退回去。
;     这是 LIFO（分配—释放—再分配）不涨内存的原因；没有它，`free` 只是把块挂起来，
;     bump 前沿照样一路涨到 OOM。
;
; **还没做的**（下一层）：物理相邻的两块不合并（coalesce）。非 LIFO 的碎片化因此还在，
; 而且它是收集器那一层的活（`docs/175` §3.4 的 `gc_auto`）。
define internal ptr @__loment_alloc(i32 %size) {
entry:
  %need0 = add i32 %size, 15
  %need = and i32 %need0, -8
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
  %so = add i32 %cur, %need
  %spp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %so
  store i32 %slack, ptr %spp
  %so4 = add i32 %so, 4
  %spp4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %so4
  store i32 %rest, ptr %spp4
  br label %relink
nosplit:
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
  store i32 %need, ptr %hp
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
  store i32 0, ptr %wp4
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
  %hi = sub i32 %d32, 8
  %hp = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %hi
  %sz = load i32, ptr %hp
  %hend = add i32 %hi, %sz
  %off = load i32, ptr @__loment_off
  %istail = icmp eq i32 %hend, %off
  br i1 %istail, label %rewind, label %link
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

; _start -> ()
define void @_start() "stackrealign" {
entry:
  %x.addr = alloca i32
  store i32 0, ptr %x.addr
  br label %L1_wcond
L1_wcond:
  br i1 1, label %L2_wbody, label %L3_wend
L2_wbody:
  %t1 = load i32, ptr %x.addr
  %t2 = add i32 %t1, 1
  store i32 %t2, ptr %x.addr
  br label %L1_wcond
L3_wend:
  ret void
}
; timer_isr -> interrupt (x86_intrcc)
define x86_intrcc void @timer_isr(ptr byval([8 x i8]) %__frame) {
entry:
  %t.addr = alloca i32
  store i32 1, ptr %t.addr
  %t1 = call ptr @__loment_alloc(i32 1)
  %t2 = load i32, ptr %t.addr
  %t3 = trunc i32 %t2 to i8
  %t4 = getelementptr i8, ptr %t1, i32 0
  store i8 %t3, ptr %t4
  ret void
}
